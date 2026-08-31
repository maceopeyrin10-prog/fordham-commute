"""
Boil the MTA's 19 MB timetable down to one small file per day.

The Apps Script that actually runs your morning cannot chew through a 19 MB
CSV every five minutes, and it does not need to: the published timetable
changes rarely. So this pulls it apart once, writes a few kilobytes for each
day, and the live part just reads the file for today.

Run it with no arguments to refresh the next 120 days:

    python build_timetable.py

The files land in timetable/YYYY-MM-DD.json and are committed to the repo, so
Apps Script can fetch them from the public raw URL without any credentials.
"""

import datetime as dt
import json
import os
import sys

import traindata

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "timetable")

FROM_STATION = "Grand Central"
TO_STATION = "Fordham"
DAYS_AHEAD = 120


def hhmm(when):
    return when.strftime("%H:%M")


def build(days_ahead=DAYS_AHEAD, start=None):
    os.makedirs(OUT_DIR, exist_ok=True)
    traindata.download_timetable(force=True)

    start = start or dt.date.today()
    written = 0
    empty = 0

    index = {"built": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
             "from": FROM_STATION, "to": TO_STATION, "days": []}

    for offset in range(days_ahead):
        day = start + dt.timedelta(days=offset)
        trains = traindata.trains_on(day, FROM_STATION, TO_STATION)
        if not trains:
            empty += 1
            continue

        payload = {
            "date": day.isoformat(),
            "from": FROM_STATION,
            "to": TO_STATION,
            "trains": [
                {
                    "n": t["train_number"],
                    "d": hhmm(t["depart"]),
                    "a": hhmm(t["arrive"]),
                    "line": t["line"],
                    "to": t["headsign"],
                }
                for t in trains
            ],
        }

        path = os.path.join(OUT_DIR, day.isoformat() + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))

        index["days"].append(day.isoformat())
        written += 1

    with open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=1)

    return written, empty


def tidy_up(keep_from):
    """Delete day files that are in the past, so the folder does not grow."""
    removed = 0
    for name in os.listdir(OUT_DIR):
        if not name.endswith(".json") or name == "index.json":
            continue
        try:
            day = dt.date.fromisoformat(name[:-5])
        except ValueError:
            continue
        if day < keep_from:
            os.remove(os.path.join(OUT_DIR, name))
            removed += 1
    return removed


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else DAYS_AHEAD
    today = dt.date.today()

    written, empty = build(days, today)
    removed = tidy_up(today)

    print("wrote   :", written, "day files")
    print("no service:", empty, "days (beyond the published timetable)")
    print("removed :", removed, "past day files")

    sample = os.path.join(OUT_DIR, today.isoformat() + ".json")
    if os.path.exists(sample):
        size = os.path.getsize(sample)
        with open(sample, encoding="utf-8") as fh:
            data = json.load(fh)
        print("today   :", len(data["trains"]), "trains,", size, "bytes")
