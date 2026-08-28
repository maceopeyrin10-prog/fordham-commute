"""
Everything about Metro-North trains: the published timetable and the live feed.
Nothing in here touches your calendar.

Data comes from the MTA official public feeds. No API key, no account, free.
  Timetable  https://rrgtfsfeeds.s3.amazonaws.com/gtfsmnr.zip
  Live       https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/mnr%2Fgtfs-mnr
"""

import csv
import io
import os
import time
import zipfile
from datetime import datetime, timedelta

import requests
from google.protobuf.unknown_fields import UnknownFieldSet
from google.transit import gtfs_realtime_pb2

TIMETABLE_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfsmnr.zip"
LIVE_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/mnr%2Fgtfs-mnr"

HERE = os.path.dirname(os.path.abspath(__file__))
TIMETABLE_FILE = os.path.join(HERE, "gtfs_mnr.zip")
REFRESH_AFTER_HOURS = 24

# MTA add-on to the standard live feed, which carries the track number.
# Defined in the MTA official gtfs-realtime-MTARR.proto:
#   extend TripUpdate.StopTimeUpdate { MtaRailroadStopTimeUpdate ... = 1005 }
#   message MtaRailroadStopTimeUpdate { string track = 1; string trainStatus = 2 }
MTA_RAILROAD_EXTENSION_FIELD = 1005


# ----------------------------------------------------------------------
# The published timetable
# ----------------------------------------------------------------------

def download_timetable(force=False):
    """Grab the timetable zip unless we already have a recent copy."""
    if not force and os.path.exists(TIMETABLE_FILE):
        age_hours = (time.time() - os.path.getmtime(TIMETABLE_FILE)) / 3600
        if age_hours < REFRESH_AFTER_HOURS:
            return TIMETABLE_FILE
    resp = requests.get(TIMETABLE_URL, timeout=120)
    resp.raise_for_status()
    with open(TIMETABLE_FILE, "wb") as fh:
        fh.write(resp.content)
    return TIMETABLE_FILE


def _rows(zf, name):
    with zf.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, "utf-8-sig"))


def _gtfs_time_to_minutes(value):
    """GTFS clock strings can run past midnight, e.g. 25:10:00."""
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return hours * 60 + minutes + (1 if seconds >= 30 else 0)


def find_station_id(zf, station_name):
    for stop in _rows(zf, "stops.txt"):
        if stop["stop_name"].strip().lower() == station_name.strip().lower():
            return stop["stop_id"]
    raise SystemExit("Could not find a Metro-North station called: " + station_name)


def trains_on(service_date, from_station, to_station):
    """
    Every train running on service_date that goes from one station to the other.

    Returns a list of dicts sorted by departure time, each with:
      train_number, depart, arrive, line, headsign, scheduled_track, trip_id
    """
    zf = zipfile.ZipFile(download_timetable())

    from_id = find_station_id(zf, from_station)
    to_id = find_station_id(zf, to_station)

    # This feed has no calendar.txt: every running day is listed explicitly.
    stamp = service_date.strftime("%Y%m%d")
    running_today = {
        row["service_id"]
        for row in _rows(zf, "calendar_dates.txt")
        if row["date"] == stamp and row["exception_type"] == "1"
    }

    line_names = {row["route_id"]: row["route_long_name"] for row in _rows(zf, "routes.txt")}

    trips = {}
    for row in _rows(zf, "trips.txt"):
        if row["service_id"] in running_today:
            trips[row["trip_id"]] = {
                "train_number": row["trip_short_name"],
                "line": line_names.get(row["route_id"], "Metro-North"),
                "headsign": row["trip_headsign"],
            }

    # Keep only the two stops we care about, for trips running today.
    legs = {}
    for row in _rows(zf, "stop_times.txt"):
        if row["stop_id"] not in (from_id, to_id):
            continue
        if row["trip_id"] not in trips:
            continue
        legs.setdefault(row["trip_id"], {})[row["stop_id"]] = row

    midnight = datetime.combine(service_date, datetime.min.time())
    results = []
    for trip_id, stops in legs.items():
        origin, destination = stops.get(from_id), stops.get(to_id)
        if not origin or not destination:
            continue
        # Must call at the origin before the destination, i.e. the outbound direction.
        if int(origin["stop_sequence"]) >= int(destination["stop_sequence"]):
            continue
        # Skip trains you cannot board here, or cannot get off there.
        if origin["pickup_type"] == "1" or destination["drop_off_type"] == "1":
            continue

        results.append({
            "trip_id": trip_id,
            "train_number": trips[trip_id]["train_number"],
            "line": trips[trip_id]["line"],
            "headsign": trips[trip_id]["headsign"],
            "depart": midnight + timedelta(minutes=_gtfs_time_to_minutes(origin["departure_time"])),
            "arrive": midnight + timedelta(minutes=_gtfs_time_to_minutes(destination["arrival_time"])),
            "scheduled_track": (origin.get("track") or "").strip(),
        })

    results.sort(key=lambda train: train["depart"])
    return results


def best_train(trains, be_off_train_by, not_before=None):
    """The latest train that still gets you off at your destination in time."""
    workable = [t for t in trains if t["arrive"] <= be_off_train_by]
    if not_before is not None:
        workable = [t for t in workable if t["depart"] >= not_before]
    return workable[-1] if workable else None


# ----------------------------------------------------------------------
# The live feed
# ----------------------------------------------------------------------

def _read_varint(buf, i):
    result = shift = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7


def _decode_mta_extension(raw):
    """Pull track and trainStatus out of the MTA add-on block."""
    track = status = None
    i = 0
    while i < len(raw):
        key, i = _read_varint(raw, i)
        field, wire = key >> 3, key & 7
        if wire == 2:
            length, i = _read_varint(raw, i)
            value, i = raw[i:i + length], i + length
            if field == 1:
                track = value.decode("utf-8", "replace").strip()
            elif field == 2:
                status = value.decode("utf-8", "replace").strip()
        elif wire == 0:
            _, i = _read_varint(raw, i)
        elif wire == 5:
            i += 4
        elif wire == 1:
            i += 8
        else:
            break
    return track or None, status or None


def fetch_live_feed():
    resp = requests.get(LIVE_URL, timeout=60)
    resp.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)
    return feed


def live_status(train, origin_station_id, feed=None):
    """
    What the MTA is saying right now about one train at one station.

    The live feed uses its own internal trip ids, so we match on the train
    number (which the feed puts in the entity id) and double-check that its
    start time is the scheduled departure we expect.

    Returns a dict with cancelled / track / train_status / delay_minutes /
    actual_departure, or None if this train is not in the live feed yet.
    The feed only carries trains running now or shortly, so None simply means
    "too early to know" rather than "something is wrong".
    """
    feed = feed or fetch_live_feed()
    wanted_start = train["depart"].strftime("%H:%M:%S")

    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        if entity.id != train["train_number"]:
            continue
        update = entity.trip_update
        if update.trip.start_time and update.trip.start_time != wanted_start:
            continue

        info = {
            "cancelled": update.trip.schedule_relationship == update.trip.CANCELED,
            "track": None,
            "train_status": None,
            "delay_minutes": None,
            "actual_departure": None,
        }

        for stop in update.stop_time_update:
            if stop.stop_id != origin_station_id:
                continue
            if stop.schedule_relationship == stop.SKIPPED:
                info["cancelled"] = True

            for field in UnknownFieldSet(stop):
                if field.field_number == MTA_RAILROAD_EXTENSION_FIELD:
                    track, status = _decode_mta_extension(field.data)
                    info["track"] = track or info["track"]
                    info["train_status"] = status or info["train_status"]

            timing = stop.departure if stop.HasField("departure") else stop.arrival
            if timing.delay:
                info["delay_minutes"] = round(timing.delay / 60)
            if timing.time:
                info["actual_departure"] = datetime.fromtimestamp(timing.time)
            break

        return info

    return None
