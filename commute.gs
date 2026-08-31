/**
 * Fordham commute — the whole thing, running on Google's servers.
 *
 * Every 5 minutes this looks at your calendar, and if you have class today it
 * works out which Metro-North train to take, writes it on your calendar, and
 * fills in the real track number once Grand Central posts it.
 *
 * Your laptop is not involved. There is no password, token or key anywhere in
 * here: Apps Script is already you, so it can just read and write your own
 * calendar. That is the entire reason this lives here.
 *
 * SETUP, once:
 *   1. script.google.com -> New project
 *   2. Delete the sample code, paste this whole file
 *   3. Pick "installTimer" from the function dropdown, press Run, approve
 *   That is it.
 */

// ---------------------------------------------------------------------------
// Your settings. Change a number, save. Nothing else needs touching.
// ---------------------------------------------------------------------------

const WALK_TO_GRAND_CENTRAL = 16;   // minutes, door to Grand Central
const SLACK_AT_GRAND_CENTRAL = 12;  // minutes of breathing room before the train
const FORDHAM_STATION_TO_SEAT = 10; // minutes, platform to classroom
const SEATED_BEFORE_CLASS = 20;     // minutes early you want to be sitting
const ALARM_TO_OUT_THE_DOOR = 45;   // minutes from alarm to leaving

const START_WATCHING_MINUTES_BEFORE = 35; // when to start looking for the track

const CLASS_LOCATION_CONTAINS = 'Rose Hill'; // marks an event as a class
const HOME_ADDRESS = '150 E 57th St, New York, NY';

const TIMEZONE = 'America/New_York';

// Where the precomputed timetable lives. Public, so no credentials needed.
const TIMETABLE_URL =
  'https://raw.githubusercontent.com/maceopeyrin10-prog/fordham-commute/main/timetable/';

// The MTA's live Metro-North feed. Free, no key, no account.
const LIVE_FEED_URL =
  'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/mnr%2Fgtfs-mnr';

const GRAND_CENTRAL_STOP_ID = '1';

// ---------------------------------------------------------------------------
// The bit you run
// ---------------------------------------------------------------------------

/** Run this ONCE by hand. Installs the timer and does a first pass. */
function installTimer() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'updateCommute') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('updateCommute').timeBased().everyMinutes(5).create();
  console.log('Timer installed: every 5 minutes.');
  updateCommute();
}

/** Handy for testing. Shows what it would do without writing anything. */
function preview() {
  const plan = buildPlan(new Date());
  console.log(plan ? describe(plan) : 'No class today, or no workable train.');
}

/** The one the timer calls. */
function updateCommute() {
  const now = new Date();
  const plan = buildPlan(now);
  if (!plan) return;                     // no class, nothing to do

  // Before the watching window there is nothing new to learn, but we still
  // want the event to exist early in the day.
  writeEvents(plan);
}

// ---------------------------------------------------------------------------
// Working out the plan
// ---------------------------------------------------------------------------

function buildPlan(now) {
  const klass = firstClassOf(now);
  if (!klass) return null;

  const beSeatedBy = addMinutes(klass.start, -SEATED_BEFORE_CLASS);
  const offTrainBy = addMinutes(beSeatedBy, -FORDHAM_STATION_TO_SEAT);

  const trains = timetableFor(now);
  if (!trains || !trains.length) return null;

  let chosen = null;
  for (let i = 0; i < trains.length; i++) {
    if (trains[i].arrive <= offTrainBy) chosen = trains[i];
  }
  if (!chosen) return null;

  // Only bother the live feed once we are close enough for it to know anything.
  let live = null;
  const minutesOut = (chosen.depart - now) / 60000;
  if (minutesOut <= START_WATCHING_MINUTES_BEFORE) {
    live = liveStatus(chosen);

    // Cancelled, or late enough to make you miss class: take the next one.
    if (live && (live.cancelled || (live.delayMinutes || 0) > 0)) {
      const wouldArrive = addMinutes(chosen.arrive, live.delayMinutes || 0);
      if (live.cancelled || wouldArrive > offTrainBy) {
        let replacement = null;
        for (let i = 0; i < trains.length; i++) {
          if (trains[i].depart > chosen.depart && trains[i].arrive <= offTrainBy) {
            replacement = trains[i];
          }
        }
        if (replacement) {
          chosen = replacement;
          live = liveStatus(chosen);
        }
      }
    }
  }

  const delay = (live && live.delayMinutes) || 0;
  const depart = addMinutes(chosen.depart, delay);
  const arrive = addMinutes(chosen.arrive, delay);
  const leaveHome = addMinutes(depart, -(WALK_TO_GRAND_CENTRAL + SLACK_AT_GRAND_CENTRAL));
  const alarm = addMinutes(leaveHome, -ALARM_TO_OUT_THE_DOOR);

  return {
    klass: klass,
    train: chosen,
    track: live ? live.track : null,
    delay: delay,
    depart: depart,
    arrive: arrive,
    leaveHome: leaveHome,
    alarm: alarm
  };
}

function firstClassOf(now) {
  const events = CalendarApp.getDefaultCalendar().getEventsForDay(now);
  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    if (e.isAllDayEvent()) continue;
    const where = e.getLocation() || '';
    if (where.toLowerCase().indexOf(CLASS_LOCATION_CONTAINS.toLowerCase()) === -1) continue;
    return { title: e.getTitle(), start: e.getStartTime(), where: where };
  }
  return null;
}

function timetableFor(now) {
  const key = Utilities.formatDate(now, TIMEZONE, 'yyyy-MM-dd');

  const cache = CacheService.getScriptCache();
  let raw = cache.get('tt_' + key);

  if (!raw) {
    const response = UrlFetchApp.fetch(TIMETABLE_URL + key + '.json',
                                       { muteHttpExceptions: true });
    if (response.getResponseCode() !== 200) {
      console.error('No timetable for ' + key + ' (HTTP ' +
                    response.getResponseCode() + '). Has build_timetable.py run lately?');
      return null;
    }
    raw = response.getContentText();
    cache.put('tt_' + key, raw, 21600); // 6 hours
  }

  const data = JSON.parse(raw);
  return data.trains.map(function (t) {
    return {
      number: t.n,
      line: t.line,
      headsign: t.to,
      depart: atTime(now, t.d),
      arrive: atTime(now, t.a)
    };
  });
}

// ---------------------------------------------------------------------------
// The live feed, read straight from the binary
// ---------------------------------------------------------------------------

/**
 * The MTA publishes this as protobuf, and Apps Script has no protobuf library,
 * so we walk the bytes ourselves. We only need three things out of it, so this
 * is far less work than it sounds: the train number, the scheduled start time
 * (to be sure it is the right day's train), and the track.
 */
function liveStatus(train) {
  let bytes;
  try {
    const response = UrlFetchApp.fetch(LIVE_FEED_URL, { muteHttpExceptions: true });
    if (response.getResponseCode() !== 200) return null;
    bytes = toUnsigned(response.getContent());
  } catch (err) {
    console.error('Live feed unreachable: ' + err);
    return null;
  }

  const wantStart = Utilities.formatDate(train.depart, TIMEZONE, 'HH:mm:ss');
  let found = null;

  walk(bytes, 0, bytes.length, function (field, wire, value, from, to) {
    if (found || field !== 2 || wire !== 2) return;   // FeedMessage.entity
    const entity = readEntity(bytes, from, to, train.number, wantStart);
    if (entity) found = entity;
  });

  return found;
}

function readEntity(bytes, from, to, wantNumber, wantStart) {
  let id = null;
  let tripFrom = -1, tripTo = -1;

  walk(bytes, from, to, function (field, wire, value, f, t) {
    if (field === 1 && wire === 2) id = text(bytes, f, t);          // entity.id
    else if (field === 3 && wire === 2) { tripFrom = f; tripTo = t; } // trip_update
  });

  // The feed puts the train number in the entity id. That is our join key.
  if (id !== wantNumber || tripFrom < 0) return null;

  const result = { cancelled: false, track: null, status: null,
                   delayMinutes: null, startTime: null };
  let stops = [];

  walk(bytes, tripFrom, tripTo, function (field, wire, value, f, t) {
    if (field === 1 && wire === 2) {                    // TripDescriptor
      walk(bytes, f, t, function (df, dw, dv, df1, dt1) {
        if (df === 2 && dw === 2) result.startTime = text(bytes, df1, dt1);
        if (df === 4 && dw === 0 && dv === 3) result.cancelled = true; // CANCELED
      });
    } else if (field === 2 && wire === 2) {             // StopTimeUpdate
      stops.push([f, t]);
    }
  });

  // Same train number can appear on another day's feed entry; make sure the
  // scheduled departure matches the one we planned around.
  if (result.startTime && result.startTime !== wantStart) return null;

  for (let i = 0; i < stops.length; i++) {
    readStop(bytes, stops[i][0], stops[i][1], result);
    if (result.track !== null || result.delayMinutes !== null) break;
  }
  return result;
}

function readStop(bytes, from, to, result) {
  let stopId = null, delay = null, ext = null, skipped = false;

  walk(bytes, from, to, function (field, wire, value, f, t) {
    if (field === 4 && wire === 2) stopId = text(bytes, f, t);
    else if (field === 5 && wire === 0 && value === 1) skipped = true;
    else if (field === 3 && wire === 2) {               // departure
      walk(bytes, f, t, function (ef, ew, ev) {
        if (ef === 1 && ew === 0) delay = signed(ev);
      });
    } else if (field === 1005 && wire === 2) { ext = [f, t]; }
  });

  if (stopId !== GRAND_CENTRAL_STOP_ID) return;

  if (skipped) result.cancelled = true;
  if (delay !== null && delay !== 0) result.delayMinutes = Math.round(delay / 60);

  if (ext) {
    walk(bytes, ext[0], ext[1], function (xf, xw, xv, f, t) {
      if (xf === 1 && xw === 2) result.track = text(bytes, f, t);
      else if (xf === 2 && xw === 2) result.status = text(bytes, f, t);
    });
  }
}

// --- the small amount of protobuf machinery the above needs ---

function walk(bytes, from, to, onField) {
  let i = from;
  while (i < to) {
    const key = varint(bytes, i);
    if (!key) return;
    i = key.next;
    const field = Math.floor(key.value / 8);
    const wire = key.value % 8;

    if (wire === 2) {
      const len = varint(bytes, i);
      if (!len) return;
      const start = len.next;
      const end = start + len.value;
      if (end > to) return;
      onField(field, wire, null, start, end);
      i = end;
    } else if (wire === 0) {
      const v = varint(bytes, i);
      if (!v) return;
      onField(field, wire, v.value, i, v.next);
      i = v.next;
    } else if (wire === 5) {
      onField(field, wire, null, i, i + 4);
      i += 4;
    } else if (wire === 1) {
      onField(field, wire, null, i, i + 8);
      i += 8;
    } else {
      return; // groups: not used by this feed
    }
  }
}

function varint(bytes, i) {
  let result = 0, shift = 1, count = 0;
  while (i < bytes.length && count < 10) {
    const b = bytes[i++];
    result += (b & 0x7f) * shift;
    if ((b & 0x80) === 0) return { value: result, next: i };
    shift *= 128;
    count++;
  }
  return null;
}

/** protobuf writes negative int32 as a very large varint. */
function signed(v) {
  return v >= 9223372036854775808 ? v - 18446744073709551616 : v;
}

function text(bytes, from, to) {
  let out = '';
  for (let i = from; i < to; i++) out += String.fromCharCode(bytes[i]);
  return decodeURIComponent(escape(out)); // bytes are utf-8
}

function toUnsigned(signedBytes) {
  const out = new Array(signedBytes.length);
  for (let i = 0; i < signedBytes.length; i++) {
    out[i] = signedBytes[i] < 0 ? signedBytes[i] + 256 : signedBytes[i];
  }
  return out;
}

// ---------------------------------------------------------------------------
// Writing it on the calendar
// ---------------------------------------------------------------------------

const TRAIN_TAG = '[commute:train]';
const WAKE_TAG = '[commute:wake]';

function writeEvents(plan) {
  const cal = CalendarApp.getDefaultCalendar();
  const today = cal.getEventsForDay(plan.depart);

  upsert(cal, today, WAKE_TAG,
         'Get ready — leave at ' + hhmm(plan.leaveHome),
         plan.alarm, plan.leaveHome, HOME_ADDRESS,
         wakeDescription(plan), /^Get ready — leave at /);

  upsert(cal, today, TRAIN_TAG,
         trainTitle(plan),
         plan.leaveHome, plan.arrive, 'Grand Central, New York, NY',
         trainDescription(plan), /^(Track .+ · )?Train \d+ · /);
}

function upsert(cal, todaysEvents, tag, title, start, end, where, body, titlePattern) {
  let mine = null;
  const twins = [];

  for (let i = 0; i < todaysEvents.length; i++) {
    const e = todaysEvents[i];
    const desc = e.getDescription() || '';
    // Match our tag, or the older events written before this script existed.
    if (desc.indexOf(tag) !== -1 || titlePattern.test(e.getTitle())) {
      if (mine) twins.push(e); else mine = e;
    }
  }

  const description = body + '\n\n' + tag;

  if (mine) {
    if (mine.getTitle() !== title) mine.setTitle(title);
    mine.setDescription(description);
    mine.setLocation(where);
    if (mine.getStartTime().getTime() !== start.getTime() ||
        mine.getEndTime().getTime() !== end.getTime()) {
      mine.setTime(start, end);
    }
  } else {
    mine = cal.createEvent(title, start, end,
                           { location: where, description: description });
    mine.removeAllReminders();
    mine.addPopupReminder(0);
  }

  // If anything ever double-created, clear the spares.
  twins.forEach(function (t) { t.deleteEvent(); });
}

function trainTitle(plan) {
  let title = '';
  if (plan.track) title += 'Track ' + plan.track + ' · ';
  title += 'Train ' + plan.train.number + ' · ' + hhmm(plan.depart);
  if (plan.delay) title += ' (' + plan.delay + ' min late)';
  return title + ' → Fordham ' + hhmm(plan.arrive);
}

function trainDescription(plan) {
  const lines = [
    'Leave home ' + hhmm(plan.leaveHome) + '.  Alarm ' + hhmm(plan.alarm) + '.',
    '',
    'Train ' + plan.train.number + ' — ' + plan.train.line +
      ' Line toward ' + plan.train.headsign + '.',
    'Grand Central ' + hhmm(plan.depart) + '  →  Fordham ' + hhmm(plan.arrive) + '.',
    plan.track ? 'Track ' + plan.track + ' at Grand Central.'
               : 'Track not posted yet — this updates itself when it is.',
    '',
    'For: ' + plan.klass.title + ' at ' + hhmm(plan.klass.start) + ', ' + plan.klass.where + '.',
    '',
    'Checked ' + Utilities.formatDate(new Date(), TIMEZONE, 'EEE d MMM, HH:mm') +
      '. Times from the MTA official Metro-North feeds.'
  ];
  return lines.join('\n');
}

function wakeDescription(plan) {
  return 'Alarm ' + hhmm(plan.alarm) + '. Out the door ' + hhmm(plan.leaveHome) + '.\n\n' +
         'Then: walk to Grand Central for the ' + hhmm(plan.depart) +
         ' (train ' + plan.train.number + ') to Fordham, arriving ' +
         hhmm(plan.arrive) + '.\n\n' +
         'For: ' + plan.klass.title + ' at ' + hhmm(plan.klass.start) + '.';
}

function describe(plan) {
  return 'Class:      ' + plan.klass.title + ' at ' + hhmm(plan.klass.start) + '\n' +
         'Alarm:      ' + hhmm(plan.alarm) + '\n' +
         'Leave home: ' + hhmm(plan.leaveHome) + '\n' +
         'Train:      ' + trainTitle(plan);
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function addMinutes(when, minutes) {
  return new Date(when.getTime() + minutes * 60000);
}

function atTime(day, hhmmText) {
  const stamp = Utilities.formatDate(day, TIMEZONE, 'yyyy/MM/dd') + ' ' + hhmmText + ':00';
  return new Date(stamp + ' ' + Utilities.formatDate(day, TIMEZONE, 'Z'));
}

function hhmm(when) {
  return Utilities.formatDate(when, TIMEZONE, 'h:mm').toLowerCase() +
         Utilities.formatDate(when, TIMEZONE, 'a').toLowerCase();
}
