"""
Work out which train to take to class today, and put it on Google Calendar.

Run it like this:

  python commute.py preview      Show what it would do. Changes nothing.
  python commute.py plan         Create/refresh today's train event (the 6am job).
  python commute.py track        Add the live track number and any delay.
  python commute.py auto         Do whichever of the above is due right now.

All the timing rules live in settings.txt. You should never need to edit
this file to change how early you leave.
"""

import os
import sys
import datetime as dt
from zoneinfo import ZoneInfo

import traindata

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "settings.txt")
CREDENTIALS_FILE = os.path.join(HERE, "google_credentials.json")
TOKEN_FILE = os.path.join(HERE, "google_token.json")
SERVICE_ACCOUNT_FILE = os.path.join(HERE, "service_account.json")

CALENDAR_SCOPE = ["https://www.googleapis.com/auth/calendar"]

# Stamped on every event we create, so we can find and update our own event
# later without ever touching anything else on your calendar.
MARKER_KEY = "fordhamCommute"


# ----------------------------------------------------------------------
# settings.txt
# ----------------------------------------------------------------------

def read_settings():
    settings = {}
    with open(SETTINGS_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                key, value = line.split("=", 1)
                settings[key.strip()] = value.strip()

    def minutes(name):
        try:
            return int(settings[name])
        except (KeyError, ValueError):
            raise SystemExit(
                "settings.txt: '" + name + "' is missing or is not a whole number."
            )

    def clock(name):
        try:
            hour, minute = settings[name].split(":")
            return dt.time(int(hour), int(minute))
        except (KeyError, ValueError):
            raise SystemExit(
                "settings.txt: '" + name + "' should look like 08:30."
            )

    return {
        "walk": minutes("walk_to_grand_central_minutes"),
        "slack": minutes("slack_at_grand_central_minutes"),
        "to_seat": minutes("fordham_station_to_seat_minutes"),
        "seated_before": minutes("seated_before_class_minutes"),
        "get_ready": minutes("alarm_to_out_the_door_minutes"),
        "track_check": minutes("track_check_minutes_before_train"),
        "wake_block": settings.get("show_get_ready_block", "yes").strip().lower()
                      in ("yes", "y", "true", "1", "on"),
        "class_marker": settings.get("class_location_contains", "Rose Hill"),
        "earliest": clock("ignore_classes_starting_before"),
        "latest": clock("ignore_classes_starting_after"),
        "calendar_id": settings.get("calendar_id", "primary"),
        "from_station": settings.get("from_station", "Grand Central"),
        "to_station": settings.get("to_station", "Fordham"),
        "tz": ZoneInfo(settings.get("timezone", "America/New_York")),
    }


# ----------------------------------------------------------------------
# Google Calendar
# ----------------------------------------------------------------------

def _gcloud_signin():
    """
    Reuse the sign-in that Google's own command-line tool already did.

    This is what lets us skip the Google Cloud console entirely. gcloud ships
    with its own Google-approved sign-in, so `gcloud auth application-default
    login` is enough on its own. We copy the long-lived part of it into our
    own token file, after which the program never needs gcloud again.
    """
    import json
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    adc = os.path.join(os.environ.get("APPDATA", ""), "gcloud",
                       "application_default_credentials.json")
    if not os.path.exists(adc):
        return None

    with open(adc, encoding="utf-8") as fh:
        saved = json.load(fh)
    if "refresh_token" not in saved:
        return None

    creds = Credentials(
        token=None,
        refresh_token=saved["refresh_token"],
        client_id=saved.get("client_id"),
        client_secret=saved.get("client_secret"),
        token_uri="https://oauth2.googleapis.com/token",
        scopes=CALENDAR_SCOPE,
    )
    creds.refresh(Request())
    return creds


def calendar_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    # 0. The robot account, if one is set up. Preferred: you share your
    #    calendar with the robot's address the same way you would share it
    #    with a person, so there is no consent screen and nothing expires.
    if os.path.exists(SERVICE_ACCOUNT_FILE):
        from google.oauth2 import service_account
        robot = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=CALENDAR_SCOPE)
        return build("calendar", "v3", credentials=robot, cache_discovery=False)

    creds = None

    # 1. A sign-in we already saved. The normal case after the first run.
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, CALENDAR_SCOPE)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())

    # 2. Your own permission file from the Google Cloud console. Preferred:
    #    it is yours, so it is not affected by Google retiring the shared
    #    command-line client for calendar access.
    if (not creds or not creds.valid) and os.path.exists(CREDENTIALS_FILE):
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, CALENDAR_SCOPE)
        creds = flow.run_local_server(port=0)

    # 3. Failing that, piggyback on the Google command-line tool.
    if not creds or not creds.valid:
        creds = _gcloud_signin()

    if not creds or not creds.valid:
        raise SystemExit(
            "No Google sign-in yet. Run this once, then try again:\n\n"
            "  gcloud auth application-default login "
            "--scopes=https://www.googleapis.com/auth/calendar,"
            "https://www.googleapis.com/auth/cloud-platform\n"
        )

    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def first_class_of_day(service, settings, day):
    """The earliest class-looking event on `day`, or None."""
    tz = settings["tz"]
    start = dt.datetime.combine(day, dt.time.min, tzinfo=tz)
    end = start + dt.timedelta(days=1)

    response = service.events().list(
        calendarId=settings["calendar_id"],
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    for event in response.get("items", []):
        if settings["class_marker"].lower() not in (event.get("location") or "").lower():
            continue
        when = event["start"].get("dateTime")
        if not when:
            continue  # all-day events are not classes
        begins = dt.datetime.fromisoformat(when).astimezone(tz)
        if not settings["earliest"] <= begins.time() <= settings["latest"]:
            continue
        return {
            "name": event.get("summary", "class"),
            "starts": begins,
            "where": event.get("location", ""),
        }
    return None


def marker_for(day, part):
    """The private stamp that lets us find our own events again."""
    return day.isoformat() if part == "train" else day.isoformat() + "/" + part


def find_our_events(service, settings, day, part="train"):
    """
    Every event of ours for `day`. Normally zero or one, but two schedulers
    can both look, both see nothing, and both create one, so this returns a
    list and the caller tidies up any twins.
    """
    tz = settings["tz"]
    start = dt.datetime.combine(day, dt.time.min, tzinfo=tz)
    response = service.events().list(
        calendarId=settings["calendar_id"],
        timeMin=start.isoformat(),
        timeMax=(start + dt.timedelta(days=1)).isoformat(),
        singleEvents=True,
        orderBy="startTime",
        privateExtendedProperty=MARKER_KEY + "=" + marker_for(day, part),
    ).execute()
    return response.get("items", [])


def find_our_event(service, settings, day, part="train"):
    """One of our own events for `day`, if we already made it."""
    items = find_our_events(service, settings, day, part)
    return items[0] if items else None


# ----------------------------------------------------------------------
# Working out the plan
# ----------------------------------------------------------------------

def build_plan(settings, klass, live_feed=None):
    """Pick the train and work out every time, from alarm to seat."""
    tz = settings["tz"]
    day = klass["starts"].date()

    be_seated_by = klass["starts"] - dt.timedelta(minutes=settings["seated_before"])
    off_train_by = be_seated_by - dt.timedelta(minutes=settings["to_seat"])

    trains = traindata.trains_on(day, settings["from_station"], settings["to_station"])
    if not trains:
        return None, "No Metro-North service found between those stations today."

    # Compare naive timetable times against a naive deadline.
    deadline = off_train_by.replace(tzinfo=None)
    train = traindata.best_train(trains, deadline)
    if train is None:
        earliest = trains[0]
        return None, (
            "No train gets you there in time. The first one today leaves at "
            + earliest["depart"].strftime("%H:%M") + " and arrives "
            + earliest["arrive"].strftime("%H:%M") + "."
        )

    live = None
    note = None
    live_ok = True
    if live_feed is not False:
        zf = __import__("zipfile").ZipFile(traindata.download_timetable())
        origin_id = traindata.find_station_id(zf, settings["from_station"])
        try:
            live = traindata.live_status(train, origin_id, feed=live_feed)
        except Exception as problem:  # never let a feed hiccup lose you the event
            live_ok = False
            note = "Live feed unavailable (" + type(problem).__name__ + "); times are the timetable."

        # If the chosen train is cancelled or now too late, fall to the next one.
        if live and (live["cancelled"] or (live["delay_minutes"] or 0) > 0):
            delayed_arrival = train["arrive"] + dt.timedelta(minutes=live["delay_minutes"] or 0)
            if live["cancelled"] or delayed_arrival > deadline:
                later = [t for t in trains if t["depart"] > train["depart"]]
                replacement = traindata.best_train(later, deadline)
                if replacement:
                    note = ("Train " + train["train_number"] + " was "
                            + ("cancelled" if live["cancelled"] else "running late")
                            + "; switched to " + replacement["train_number"] + ".")
                    train = replacement
                    live = traindata.live_status(train, origin_id, feed=live_feed)
                else:
                    note = ("Train " + train["train_number"] + " is "
                            + ("cancelled" if live["cancelled"] else "late")
                            + " and there is no later train that gets you there on time.")

    delay = (live or {}).get("delay_minutes") or 0
    depart = train["depart"] + dt.timedelta(minutes=delay)
    arrive = train["arrive"] + dt.timedelta(minutes=delay)

    leave_home = depart - dt.timedelta(minutes=settings["walk"] + settings["slack"])
    alarm = leave_home - dt.timedelta(minutes=settings["get_ready"])

    return {
        "day": day,
        "class": klass,
        "train": train,
        "live": live,
        "live_ok": live_ok,
        "note": note,
        "delay": delay,
        "depart": depart.replace(tzinfo=tz),
        "arrive": arrive.replace(tzinfo=tz),
        "leave_home": leave_home.replace(tzinfo=tz),
        "alarm": alarm.replace(tzinfo=tz),
        "track": (live or {}).get("track"),
    }, None


def hhmm(when):
    """Times people can read at a glance: 9:08am, 3:08pm."""
    twelve_hour = when.strftime("%#I:%M" if os.name == "nt" else "%-I:%M")
    return twelve_hour + when.strftime("%p").lower()


def event_title(plan):
    bits = []
    if plan["track"]:
        bits.append("Track " + plan["track"])
    bits.append("Train " + plan["train"]["train_number"])
    when = hhmm(plan["depart"])
    if plan["delay"]:
        when += " (" + str(plan["delay"]) + " min late)"
    bits.append(when + " → Fordham " + hhmm(plan["arrive"]))
    return " · ".join(bits)


def event_description(plan, settings):
    klass = plan["class"]
    lines = [
        "Leave home " + hhmm(plan["leave_home"]) + ".  Alarm " + hhmm(plan["alarm"]) + ".",
        "",
        "Train " + plan["train"]["train_number"] + " — " + plan["train"]["line"]
        + " Line toward " + plan["train"]["headsign"] + ".",
        settings["from_station"] + " " + hhmm(plan["depart"])
        + "  →  " + settings["to_station"] + " " + hhmm(plan["arrive"]) + ".",
    ]
    if plan["track"]:
        lines.append("Track " + plan["track"] + " at " + settings["from_station"] + ".")
    else:
        lines.append("Track not posted yet — this event updates itself when it is.")

    lines += [
        "",
        "For: " + klass["name"] + " at " + hhmm(klass["starts"])
        + (", " + klass["where"] if klass["where"] else "") + ".",
    ]
    if plan["note"]:
        lines += ["", plan["note"]]
    lines += [
        "",
        "Updated " + dt.datetime.now(settings["tz"]).strftime("%a %d %b, %H:%M")
        + ". Times from the MTA official Metro-North feeds.",
    ]
    return "\n".join(lines)


def event_body(plan, settings):
    return {
        "summary": event_title(plan),
        "description": event_description(plan, settings),
        "location": settings["from_station"] + ", New York, NY",
        "start": {"dateTime": plan["leave_home"].isoformat(), "timeZone": str(settings["tz"])},
        "end": {"dateTime": plan["arrive"].isoformat(), "timeZone": str(settings["tz"])},
        "extendedProperties": {"private": {MARKER_KEY: marker_for(plan["day"], "train")}},
        "reminders": {
            "useDefault": False,
            # Only the "leave now" nudge here. When the separate get-ready
            # block is switched on it carries the alarm, so we do not buzz
            # twice for the same thing.
            "overrides": (
                [{"method": "popup", "minutes": 0}]
                if settings["wake_block"] else
                [{"method": "popup", "minutes": 0},
                 {"method": "popup", "minutes": settings["get_ready"]}]
            ),
        },
        "colorId": "7",
    }


def wake_body(plan, settings):
    """The optional 'Get ready' block, from alarm until out the door."""
    return {
        "summary": "Get ready — leave at " + hhmm(plan["leave_home"]),
        "description": (
            "Alarm " + hhmm(plan["alarm"]) + ". Out the door " + hhmm(plan["leave_home"]) + ".\n\n"
            "Then: walk to Grand Central for the "
            + hhmm(plan["depart"]) + " (train " + plan["train"]["train_number"] + ") "
            "to Fordham, arriving " + hhmm(plan["arrive"]) + ".\n\n"
            "For: " + plan["class"]["name"] + " at " + hhmm(plan["class"]["starts"]) + "."
        ),
        "location": "150 E 57th St, New York, NY",
        "start": {"dateTime": plan["alarm"].isoformat(), "timeZone": str(settings["tz"])},
        "end": {"dateTime": plan["leave_home"].isoformat(), "timeZone": str(settings["tz"])},
        "extendedProperties": {"private": {MARKER_KEY: marker_for(plan["day"], "wake")}},
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": 0}],  # the alarm itself
        },
        "colorId": "5",
    }


# ----------------------------------------------------------------------
# The commands
# ----------------------------------------------------------------------

def describe(plan):
    print("  Class      " + plan["class"]["name"] + " at " + hhmm(plan["class"]["starts"])
          + ("  (" + plan["class"]["where"] + ")" if plan["class"]["where"] else ""))
    print("  Alarm      " + hhmm(plan["alarm"]))
    print("  Leave home " + hhmm(plan["leave_home"]))
    print("  Train      " + event_title(plan))
    if plan["note"]:
        print("  Note       " + plan["note"])


def run(command, day=None):
    settings = read_settings()
    now = dt.datetime.now(settings["tz"])
    day = day or now.date()

    if command == "preview":
        # Reads your calendar, prints the plan, writes nothing.
        service = calendar_service()
        klass = first_class_of_day(service, settings, day)
        if not klass:
            print("No class on " + day.strftime("%A %d %B") + " — nothing to do.")
            return
        plan, problem = build_plan(settings, klass)
        if problem:
            print(problem)
            return
        print("Plan for " + day.strftime("%A %d %B") + ":")
        describe(plan)
        print("\n(Preview only. Nothing was written to your calendar.)")
        return

    service = calendar_service()
    klass = first_class_of_day(service, settings, day)
    if not klass:
        print("No class on " + day.strftime("%A %d %B") + " — nothing to do.")
        return

    plan, problem = build_plan(settings, klass)
    if problem:
        print(problem)
        return

    if command == "auto":
        minutes_until_train = (plan["depart"] - now).total_seconds() / 60
        already = find_our_event(service, settings, day)
        if already is None:
            command = "plan"
        elif 0 < minutes_until_train <= settings["track_check"]:
            command = "track"
        else:
            print("Nothing due right now (train in "
                  + str(round(minutes_until_train)) + " min). Left the event alone.")
            return

    existing = find_our_event(service, settings, day, "train")

    # Never replace information we already confirmed with information we could
    # not confirm. A dead feed should downgrade nothing.
    if existing and not plan["live_ok"]:
        print("Could not reach the MTA live feed. Left the existing event as it was "
              "rather than overwriting a confirmed track with a guess.")
        return

    def put(part, body):
        mine = find_our_events(service, settings, day, part)
        if not mine:
            service.events().insert(
                calendarId=settings["calendar_id"], body=body
            ).execute()
            return False

        service.events().update(
            calendarId=settings["calendar_id"], eventId=mine[0]["id"], body=body
        ).execute()

        # If two schedulers raced and both created one, keep the first and
        # quietly remove the rest, so duplicates heal themselves.
        for twin in mine[1:]:
            service.events().delete(
                calendarId=settings["calendar_id"], eventId=twin["id"]
            ).execute()
            print("Removed a duplicate " + part + " event left by a second run.")
        return True

    updated = put("train", event_body(plan, settings))

    if settings["wake_block"]:
        put("wake", wake_body(plan, settings))
    else:
        # Setting was switched off: clear away any block we made earlier.
        stale = find_our_event(service, settings, day, "wake")
        if stale:
            service.events().delete(
                calendarId=settings["calendar_id"], eventId=stale["id"]
            ).execute()

    print(("Updated" if updated else "Created") + " the plan for "
          + day.strftime("%A %d %B") + ":")
    describe(plan)


if __name__ == "__main__":
    args = sys.argv[1:]
    command = args[0] if args else "preview"
    if command not in ("preview", "plan", "track", "auto"):
        raise SystemExit(__doc__)
    on = dt.date.fromisoformat(args[1]) if len(args) > 1 else None
    run(command, on)
