# Fordham train, on my calendar

Every day you have class, this puts **one** event on your Google Calendar that
tells you which Metro-North train to take and when to walk out the door:

> **Track 107 · Train 1316 · 9:08am → Fordham 9:26am**

The event sits at your leave-home time, so its position on the calendar *is*
the answer. Your phone buzzes once at the alarm time and once when it is time
to leave. That is the whole thing.

If you have no class that day, it does nothing at all.

---

## How it decides

It works backwards from the **first class of the day**, using the numbers in
`settings.txt`:

```
class starts                      10:00am
  − 20 min  be seated early    →   9:40am
  − 10 min  Fordham → seat     →   9:30am   must be off the train by here
pick the latest train arriving Fordham by 9:30am
  → that train leaves Grand Central at   9:08am
  − 16 min  walk from home              8:52am
  − 12 min  slack inside the terminal   8:40am   LEAVE HOME
  − 45 min  getting ready               7:55am   ALARM
```

It looks at **all** Metro-North lines that stop at Fordham, not just the
Harlem Line — your usual 9:08 is actually a New Haven Line train to Stamford.

## The track number, and when you can trust it

Measured against the live feed on 27 August 2026 — 175 polls, one a minute,
across 5 trains:

- Tracks appear **19 to 20 minutes before departure**, tightly. Every train
  was blank at T‑20 or T‑21 and had a track one minute later.
- Once posted, a track **never changed** — zero changes in the whole run. So
  one good reading is enough; the repeated checking is insurance, not need.
- The printed timetable track was **wrong 3 times out of 4**: train 591 said
  28 and left from 107, train 593 said 26 and left from 110, train 595 said 28
  and left from 17. Only train 1394 matched.

That last one is why this reads the live feed at all.

Two consequences you should know about:

**1. The track will usually not be there when you leave home.** You leave 28
minutes before the train; the MTA decides at 19. Nothing can fix that — there
is nothing to fetch yet. What happens instead is that the event updates itself
while you are walking, so by the time you reach Grand Central (about 12
minutes before departure) the track is on the event. You glance at your phone,
not at the departure board.

**2. It only shows a track it actually confirmed.** It never prints the
timetable track, and if it cannot reach the live feed it leaves the event
exactly as it was rather than overwriting a confirmed track with a guess. The
description always carries the time of the last successful check, so you can
tell how fresh it is.

The honest limit: a reassignment in the final minute beats any tool. The board
in the terminal is always the final word.

If the train is delayed enough to make you late, or is cancelled, it switches
you to the next workable train and rewrites the whole event.

---

## Changing the timing rules

Open **`settings.txt`** in Notepad, change a number, save. That is it — you
never need to touch the code.

| Setting | What it means |
|---|---|
| `walk_to_grand_central_minutes` | Your walk from 150 E 57th to Grand Central. |
| `slack_at_grand_central_minutes` | Breathing room inside the terminal before the train leaves. |
| `fordham_station_to_seat_minutes` | Fordham platform to sitting in the classroom. |
| `seated_before_class_minutes` | How early you want to already be sitting down. |
| `alarm_to_out_the_door_minutes` | Alarm going off to actually leaving. |
| `track_check_minutes_before_train` | How early to start looking for the track. |
| `class_location_contains` | The text that marks an event as a class. Yours is `Rose Hill`. |
| `ignore_classes_starting_before` / `after` | Set a window if you only want, say, mornings. |
| `from_station` / `to_station` | Change these if you ever commute somewhere else. |

**Want a bigger cushion?** Raise `slack_at_grand_central_minutes`. Everything
else shifts earlier automatically.

**Want mornings only?** Set `ignore_classes_starting_after = 12:00`.

**Class events not being found?** Whatever text is in
`class_location_contains` has to appear in the event's Location field on
Google Calendar. Every Fordham class of yours ends in `Rose Hill`, so it
works — but if a class is added without a location, it will be skipped.

---

## Running it by hand

```
python commute.py preview
```

Shows what it would do today and writes nothing. Safe to run any time.

```
python commute.py plan
```

Creates or refreshes today's event.

```
python commute.py track
```

Looks up the live track and delay, and updates today's event.

```
python commute.py auto
```

Works out which of the two is due right now. This is what the scheduler runs.

You can add a date to any of them to test another day:

```
python commute.py preview 2026-09-01
```

### First-time setup on this laptop

1. Install the pieces it needs (one time):

   ```
   python -m pip install -r requirements.txt
   ```

2. Give it permission to see your calendar — see the next section.

---

## The one-time Google permission (the only fiddly bit)

The program needs its own permission slip from Google. This is free, takes
about ten minutes, and you can revoke it at any time from your Google account.

> **There is a fully illustrated version of this** with every button named,
> the traps called out, and a troubleshooting list:
> <https://claude.ai/code/artifact/fe2128d8-a404-4487-b07e-1625b0f0c224>
>
> Note that Google renamed these screens to **Google Auth Platform** — older
> guides you find online will not match what you see.

1. Go to **console.cloud.google.com** and sign in with your Gmail.
2. Top of the page, click the project dropdown → **NEW PROJECT**. Name it
   `commute` → **CREATE**. Check the dropdown now says *commute*.
3. In the search bar type **Google Calendar API**, open it, click **ENABLE**.
4. Search for **Google Auth Platform** (or ☰ → *APIs & Services* → *OAuth
   consent screen*) → **GET STARTED**.
   - App name `Commute`, support email = your Gmail → **NEXT**
   - Audience: **External** → **NEXT** *(External is correct — Internal only
     exists for company Workspace accounts)*
   - Contact email = your Gmail → **NEXT** → tick the policy box →
     **CONTINUE** → **CREATE**
5. **Do not skip this.** Left menu → **Audience**. Find **Publishing status:
   Testing** and click **PUBLISH APP**, then confirm.

   > On *Testing*, Google **expires the permission after 7 days** and the whole
   > thing silently stops working next week. Publishing makes it permanent.
   > You do **not** need Google to verify or review anything.

6. Left menu → **Clients** → **+ CREATE CLIENT** → Application type
   **Desktop app** (not *Web application*) → **CREATE**.
7. Click **DOWNLOAD JSON**. Rename it to exactly **`google_credentials.json`**
   and put it in this folder.

   > Windows hides file extensions, so you can end up with
   > `google_credentials.json.json`. In File Explorer turn on
   > **View → File name extensions** to check.

Then run:

```
python commute.py preview
```

A browser window opens once, asking you to allow calendar access. Click
through it (Google will warn that the app is unverified — that is expected for
your own project; choose Advanced → continue). After that it saves
`google_token.json` here and never asks again.

**Keep `google_credentials.json` and `google_token.json` private.** They are
already listed in `.gitignore` so they will not be uploaded anywhere by
accident.

---

## Making it run on its own

Right now it only runs when you run it. There are two ways to automate it.

### Option A — your laptop (already set up)

A Windows scheduled task called **Fordham commute** is registered and running.
It starts at 06:00 every day and repeats every 10 minutes until 19:00, running
`commute.py auto` each time. Almost every run does nothing and exits.

To look at it: press Start, type *Task Scheduler*, and find **Fordham commute**
in the top-level list. You can disable or delete it there.

To rebuild it from scratch, or change the hours, this is the command:

```powershell
$py = "C:\Users\mpeyrin\AppData\Local\Microsoft\WindowsApps\pythonw.exe"
$script = "C:\Users\mpeyrin\Downloads\fordham-commute\commute.py"
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`" auto" -WorkingDirectory "C:\Users\mpeyrin\Downloads\fordham-commute"
$trigger = New-ScheduledTaskTrigger -Daily -At 6:00am
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 6:00am -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Hours 13)).Repetition
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Fordham commute" -Action $action -Trigger $trigger -Settings $settings -Force
```

Do **not** use `schtasks /SC MINUTE` for this — it creates a trigger that runs
on one single day and then never again, which looks fine until you notice
nothing has happened for a week.

**The real limitation:** this only runs while the laptop is awake and you are
logged in. On a class morning you leave home at 08:40 and the track is posted
at about 08:49, by which time the laptop is usually shut — so the track update
is exactly the part this cannot deliver. That is what Option B is for.

### Option B — GitHub Actions (runs without your laptop)

Free, and runs on GitHub's computers so your laptop can be shut. The workflow
is already written: `.github/workflows/commute.yml`.

**How it copes with GitHub being late.** GitHub's free scheduler is not
punctual — it can fire 5–30 minutes behind. So instead of one carefully-timed
run, it runs **every 5 minutes** and each run asks "anything to do?" Almost
always the answer is no and it stops immediately. Many cheap attempts beat one
exact one.

**To set it up:**

1. Make a **new, private** repository on github.com.
2. Upload this whole folder to it. `google_credentials.json` and
   `google_token.json` are in `.gitignore`, so they will **not** be uploaded —
   that is deliberate, they go in as secrets instead.
3. In the repo: **Settings → Secrets and variables → Actions → New repository
   secret**, and add two:
   - `GOOGLE_CREDENTIALS` — paste the entire contents of
     `google_credentials.json`
   - `GOOGLE_TOKEN` — paste the entire contents of `google_token.json`
4. Go to the **Actions** tab, pick *Fordham commute*, and press **Run
   workflow** to test it once by hand.

**What it costs.** GitHub gives free accounts 2,000 minutes a month for
private repos, and each run is billed as a whole minute even though it takes
seconds.

| Setting | Runs per month | Fits in free 2,000? |
|---|---|---|
| `*/5` (every 5 min), private repo | about 3,400 | ✗ over |
| `*/10` (every 10 min), private repo | about 1,700 | ✓ yes, with room to spare |
| `*/5`, **public** repo | unlimited | ✓ always free |

So pick one:

- **Private repo + `*/10`** — change `*/5` to `*/10` in the workflow file.
  Costs nothing, stays private. You get about 2 chances to catch the track
  instead of 4. Slightly higher chance of a morning with no track shown.
- **Public repo + `*/5`** — costs nothing, catches the track more reliably,
  but anyone can read the code. There is nothing personal in it: your class
  times live in your calendar, never in the repo, and secrets stay encrypted.
  The risk is if you ever commit the two Google files by accident.

**Recommended: private repo with `*/10`.** Safer default, and the track window
is 19 minutes wide, so two attempts is usually enough.

**One warning about the Google side.** If you skipped the *PUBLISH APP* step
above, this will work for exactly 7 days and then start failing with a token
error. Go back and publish the app.

---

## What it talks to, and what it costs

**Nothing costs money.** No API key, no account, no subscription.

| It contacts | Why | What it sends |
|---|---|---|
| `rrgtfsfeeds.s3.amazonaws.com` | The Metro-North timetable | Nothing. Anonymous download. |
| `api-endpoint.mta.info` | Live tracks and delays | Nothing. Anonymous download. |
| `googleapis.com` | Your calendar | Your permission token, and the event it writes. |

Both MTA feeds are the official public ones listed at
[mta.info/developers](https://www.mta.info/developers). The MTA removed the
API key requirement, so there is nothing to sign up for.

---

## If something looks wrong

**"No class today"** but you do have one — check the class event has a
Location containing `Rose Hill`.

**No track number appears** — normal until about 15–30 minutes before
departure. Run `python commute.py track` again closer to the time.

**"Google permission file not found"** — you have not done the Google setup
above, or the file is not named exactly `google_credentials.json`.

**Timetable looks out of date** — delete `gtfs_mnr.zip` and run it again; it
re-downloads a fresh copy (it does this automatically every 24 hours anyway).

---

## The files

| File | What it is |
|---|---|
| `settings.txt` | **Your knobs.** Edit this, not the code. |
| `commute.py` | Works out the plan and writes the calendar event. |
| `traindata.py` | Talks to the MTA feeds. Knows nothing about your calendar. |
| `requirements.txt` | The list of pieces to install. |
| `gtfs_mnr.zip` | Cached timetable. Deleted safely; it re-downloads. |
| `google_credentials.json` | Your Google permission slip. Private. |
| `google_token.json` | Created after you approve access. Private. |
