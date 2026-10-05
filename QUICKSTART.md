# Quick start

Pedal a Wahoo KICKR CORE and the car in Slow Roads follows. The game steers itself.

## Every ride

1. Close the Wahoo app and Zwift. Unplug any real game controllers. Wake the KICKR (pedal a few strokes).
2. Double-click **`ride.bat`** in this folder (or the **Slow Roads Ride** shortcut on your desktop). It starts two windows:
   - the bridge (live status line), and
   - a minimized recorder that logs the game side for tuning.
3. When the trainer connects (rising beep), the bridge **starts Slow Roads through Steam** if it isn't
   already running (`--no-launch-game` to start it yourself). In the game:
   - Assist **AUTOSTEER**. On the controller, X cycles AUTOSTEER → AUTOSPEED → AUTODRIVE and
     A turns the assist on and off. The bridge never presses them.
   - **Speed control on, in limit mode:** the padlock next to the speedometer, showing a number.
     The bridge sets that number.
   - Gearbox **Automatic** (or an electric motor).
   - A **first-person or interior camera**. In third-person view the game ignores the mouse wheel for
     the speed limit (reported on the Steam forum), so the bridge can't change it.
   - **Click into the game and leave it focused.** The game ignores the controller while another
     window is in front, and the status line then shows `NO GAME FOCUS`.
   - Leave the speedometer (bottom right) uncovered so the recorder can read it.
4. Ride. The bridge turns your speed into the game's speed limit (5 mph steps), and the game holds
   the car at that limit, uphill and down. **Stop pedalling and the car coasts** (status `coasting`):
   the limit stays put and gravity decides, so it rolls faster downhill and slows on climbs. After
   12 s without pedalling it eases to a stop (status `releasing`). Pedal again any time to resume: the
   limit holds where it was for 8 s while your watts build, then eases down a step at a time if you're
   riding easier than before. It never reverses.
5. Press **Ctrl+C** in the bridge window to finish. The recorder stops on its own.
   Or just get off the bike: after **3 minutes without pedalling** the ride ends by itself
   (`--idle-end`; the countdown only starts once you've pedalled, and pausing with F8 stops it).
6. The bridge prints a **ride summary** worked out from the trainer's data only (never the game): time
   (total and moving), distance and speed, average/max/normalized power, best 5 s / 1 min / 5 min / 20 min
   power, work (kJ, roughly kcal) and cadence. It is saved as `logs/summary-*.txt`. Print any ride again with
   `.venv\Scripts\python -m bridge.summary logs\ride-YYYYMMDD-HHMMSS.csv` (no file = the latest ride).
   - **Distance** is Zwift-like: your power through a virtual road bike (the same one the overlay uses),
     within about 1-4% of Zwift's flat speeds for 75-100 kg riders. Set `--rider-kg` to your weight plus
     about 9 kg of bike (default 85). The KICKR's own wheel speed is listed too; it reads about 30% lower.
   - **For next ride**: up to six plain suggestions from the ride's data: new personal bests, cadence,
     steady vs surgy pacing, coasting, fading or a negative split, and a target for next time.
   - Under it, one line compares the ride with your previous one (time, average power, work).
   Totals across rides: `.venv\Scripts\python -m bridge.summary --week` (or `--month`, `--all`).

## Coach: scoreboard, ride book and workouts

Everything here comes from the trainer's data in `logs/` and stays on this PC.

**Start menu.** `ride.bat` opens with your week (rides and minutes against your goal, streak, days since
your last ride) and today's suggested workout. Press **Enter** (or wait 20 s) for a free ride, **1** for the
suggestion, **2-7** for another workout. Skip the menu with `ride.bat --workout endurance` (or `suggested`).

**Workouts** (targets are a share of your FTP; the overlay shows the block, time left and the target in
green when you're on it, orange when off; a double beep marks each new block):

| Workout | What | Good for |
| --- | --- | --- |
| `easy` | 30 min light spin, 85-95 rpm | recovery days, keeping the habit on busy days |
| `endurance` | 45 min steady Z2 in 10 min blocks | aerobic base, durability |
| `cadence` | 34 min: 6 x 2 min fast spin (95-105 rpm) | smoother pedalling, higher cadence |
| `tempo` | 3 x 6 min tempo | even pacing |
| `sweetspot` | 2 x 10 min just under threshold | raising FTP |
| `long` | your long ride: last longest + 5 min, in ~15 min blocks with 1 min stand, stretch & drink breaks | long rides (saddle and mind) |
| `ramp` | ramp test: 1 min steps +20 W until you can't hold one | **measures your FTP** (75% of your best minute) and saves it |

The trainer is never controlled (data flows one way): you hold the target with your legs and gears.

**During any ride** the overlay also shows short coach messages with a beep: **stand up & stretch** every
20 min (`--comfort-break`, 0 = off), a check-in every 15 min with a **drink water** reminder and its own
three-note beep (`--no-drink` or `"drink": 0` to drop it), **new bests** live (1 / 5 / 20 min power) and
**lifetime mile milestones** (10, 25, 50, 100 ...).

**After the ride** the summary adds training load (TSS and intensity, needs an FTP), time in zones, your
longest steady stretch and coasts, new records, tips, and the **scoreboard**: lifetime miles, hours, rides
and kJ, this week vs your goal, streak, records, fitness / fatigue / form, strengths, what to work on, and
the next ride.

**Ride book** (double-click **`rides.bat`**: scoreboard here, charts in your browser):

| Command | |
| --- | --- |
| `.venv\Scripts\python -m bridge.ridebook` | scoreboard |
| `.venv\Scripts\python -m bridge.ridebook list` | all rides, numbered (1 = newest) |
| `.venv\Scripts\python -m bridge.ridebook show 3` | one ride in full, with its power curve |
| `.venv\Scripts\python -m bridge.ridebook hide 3` / `unhide 3` | leave a test ride out of totals and records |
| `.venv\Scripts\python -m bridge.ridebook note 1 sore seat after 25 min` | note a ride |
| `.venv\Scripts\python -m bridge.dashboard` | `logs\dashboard.html`: tiles, minutes per week vs goal, power curve, fitness and form, records, all rides |
| `.venv\Scripts\python -m bridge.workouts` | the workouts with your targets, and today's suggestion |

**Your goals and body** (in `settings.json` under `"ride"`, or as options): `"weekly_rides": 3`,
`"weekly_minutes": 90` (the week counts when either is met), `"rider_kg": 85` (you + about 9 kg of bike:
distance and speed), `"ftp"` (or let the ramp test set it), `"comfort_break": 20`.

## During the ride: overlay, keys and sounds

A small overlay sits at the top right of the game, in the style of the game's own dashboard:

![Overlay reminders at 15, 20 and 60 minutes](docs/images/overlay-reminders.png)

```
 24:13    4.21    186    88          ride time, virtual bike miles, watts (3 s), cadence
  TIME    MILES   WATTS  RPM
  182     171     158    93% ●       1 / 5 / 10 min average watts, share of FTP with its zone colour
 1 MIN   5 MIN  10 MIN  FTP 200
```

- Everything comes from the trainer, never from the game. Time starts at your first pedal stroke;
  miles are your virtual bike's distance (before the gear ratio), in km if the game is set to km/h.
- An average is dimmed until its window is full (e.g. the 10 min average during the first 10 minutes).
- **FTP:** set yours with `--ftp 220` or `"ftp": 220` in settings.json. Without it the overlay estimates
  95% of your best 20 minutes in past rides and marks it `EST`. The dot is the zone: grey Z1 recovery,
  blue Z2 endurance, green Z3 tempo, yellow Z4 threshold, orange Z5, red Z6, purple Z7.
- It shows only while the game is in front, never takes focus, and clicks and scrolls go straight
  through it. **F10** hides/shows it; `--no-overlay` (or `"overlay": 0`) turns it off.

With the game window in front (no need to alt-tab):

| Key | Does |
| --- | --- |
| **F6 / F7** | Gear down / up by 0.25 (car km/h per bike km/h). Saved as your new default |
| **F8** | Pause / resume: throttle off, the speed limit is left alone, auto-end waits. Use it before opening game menus |
| **F9** | Re-sync the speed limit now (scrolls to 5 mph and climbs back, so the car dips for a moment) |
| **F10** | Hide / show the overlay |

The keys are only read, never taken over, so they still reach the game. `--no-hotkeys` turns them off.

| Sound | Means |
| --- | --- |
| Two rising beeps | Trainer data flowing (start, or back after a drop) |
| Two falling beeps | Trainer data lost (throttle off) |
| Three low beeps | The game isn't the window in front: the car won't respond. Click the game |
| One high beep | The game is back in front |
| Two short beeps | Speed limit re-synced |
| Short tick | Gear changed |
| Long / rising pair | Paused / resumed |
| Three falling beeps | Ride ended |

`--no-sounds` turns them off.

**Don't touch the mouse wheel over the game during a ride.** The bridge changes the limit by
scrolling, and it counts its own scrolls. It briefly moves the cursor to the left side of the
screen to scroll, then puts it back. If the limit ever looks wrong, press F9 (or stop pedalling for a few
seconds): going back to 5 mph re-syncs the count. About 20 s into every ride (game in front, you
pedalling) it re-syncs once by itself, because the game may still have been loading when you started:
expect one short dip in speed then (two short beeps).

The status line reads:
`03:12 connected   185W  82rpm tgt  45 limit  30mph thr 60% brk  0% 1.0Hz bad 0`

- **tgt**: your (virtual) bike speed × gear ratio, in km/h: what the car should do. The first word is the state: `connected` (riding), `coasting`, `releasing`, or `NO GAME FOCUS`.
- **limit**: the speed limit the bridge has set in the game (the game holds the car there).
- `push x1.32` in place of the state means the push bonus is on: pushing above 150 W raises your gear (up to
  ×1.5 at 400 W), steps the limit up sooner, and opens the throttle to 1.0 so the game accelerates harder.
- **thr / brk**: what it is pressing on the virtual controller. In limit mode the throttle stays at
  0.6 while you pedal; the game caps the speed.

## Adjusting the feel

Pass options to `ride.bat` from a terminal in this folder, for example `ride.bat --gear 2.5`.

| Want | Option | Default |
| --- | --- | --- |
| Slower or faster car for the same pedalling | `--gear` (car km/h per bike km/h) | 2.0 (virtual), 3.0 (trainer) |
| Where your speed comes from | `--speed-source virtual` (simulated bike from your watts, smooth, default), `trainer` (KICKR flywheel speed, steps once a second), `power` | virtual |
| Reward for pushing hard (more speed and a faster climb to it) | `--push-boost` (0.5 = +50% gear at full push; 0 = off), `--push-easy-w` / `--push-hard-w` (where it starts / is full) | 0.5, 150 W, 400 W |
| Your weight (virtual bike: heavier builds speed slower and coasts longer) | `--rider-kg` (rider + bike) | 85 |
| Gentler or harder acceleration up to the limit | `--drive-throttle` | 0.6 |
| Game set to km/h | `--units km/h` | mph |
| Longer or shorter coast before easing to a stop | `--coast-hold` (seconds; 0 = old behaviour, no coasting) | 12 |
| Coast slows too much / rolls on too long on the flat | `--coast-throttle` (higher = rolls further) | 0.05 |
| Coast easing down faster or slower after the hold | (code: `release_step_s`, 4 s per 5 mph) | 4 |
| More or less room to speed up downhill while coasting | `--coast-margin` (mph above your last speed) | 5 |
| Test without the bike (scripted ride) | `--sim "0:0,5:15,40:15,55:0"` (seconds:bike km/h) | off |
| Test without the game | `--dry-run` (reads the bike, sends nothing) | off |
| Use a different trainer | `--trainer pair` (forget the saved one and pair with the first FTMS trainer found) or `--trainer AA:BB:CC:DD:EE:FF` | saved in `settings.json` |
| Previous model-based control (no in-game limit) | `--mode speed` (uses `--ramp`, `--max-throttle`, `--max-brake`) | limit |
| Old watts-to-throttle behaviour | `--mode power` | limit |

| Your FTP for the overlay's %FTP and zones | `--ftp` (watts; 0 = estimate from your best 20 min) | estimate |
| No overlay | `--no-overlay` (F10 hides it for a moment) | on |
| No beeps / no hotkeys / start the game yourself | `--no-sounds`, `--no-hotkeys`, `--no-launch-game` | on |
| Ride ends by itself after this long without pedalling | `--idle-end` (minutes; 0 = never) | 3 |
| Delete bulky logs (screenshots, 20 Hz logs) older than | `--keep-days` (ride CSVs and summaries are always kept; 0 = never) | 30 |

To make a change permanent, add it under `"ride"` in `settings.json` (create it by copying `settings.example.json`), for example `"gear": 2.5`
(F6/F7 do that for the gear). The comfort options use the keys `sounds`, `hotkeys` and `launch_game`
and `overlay` (1 = on, 0 = off), `idle_end`, `keep_days` and `ftp`.

## When to recalibrate

Limit mode (the default) needs no calibration: the game holds the speed itself. Calibration only
matters for `--mode speed`. Recalibrate after changing vehicle or the game's tuning sliders (speed
factor, max RPM), or if that mode feels wrong. Stop the car, keep the game in front, then double-click **`calibrate.bat`**. It takes about
40 s, needs no bike, and updates `settings.json`. It won't save a fit it doesn't trust.

The current settings came from one automatic-gearbox run (2026-09-27 09:20). Top speed was about
93 km/h, and coasting slowed the car much faster at high speed than at low. The world is
procedurally generated, so hills, surfaces and vehicles change the car's behaviour; the ride
recordings are how we measure that.

## Where things are

| Path | What |
| --- | --- |
| `logs/ride-*.csv` | Every trainer packet: power, cadence, bike speed, raw bytes |
| `logs/drive-*.csv` | Every controller tick (20 Hz): target, throttle, brake; `car_est_kmh` is the game limit set (limit mode) or the model estimate (speed mode) |
| `logs/bridge-*.log` | Connects, drops, errors, settings used |
| `logs/summary-*.txt` | Ride summary (trainer data only) |
| `logs/crash-*.txt` | Written if something crashes; personal details already removed |
| `logs/report-*.zip` | Made by `report.bat` to send to the developer |
| `logs/speed-*.csv` | Recorder: speedometer read from the screen (~5 Hz) |
| `logs/shots-*/` | Recorder: game-window screenshot every 5 s (only while the game is in front) |
| `logs/game-*-start/`, `-end/` | Recorder: copy of the game's saved settings (vehicle, units) |
| `settings.json` | Your ride preferences and calibration (git-ignored; start from `settings.example.json`) |
| `CLAUDE.md` | Decisions, verified facts, open questions for Claude Code sessions (local only, git-ignored) |

Files from one ride share the same timestamp. After a ride, ask Claude Code to "check the logs from my
last ride".

## Checks and tools

Run these from this folder in PowerShell.

| Command | Use |
| --- | --- |
| `.venv\Scripts\python tools\summarize_ride.py` | Packet rate, gaps, power and cadence stats for the last ride |
| `.venv\Scripts\python tools\replay_ride.py` | Replay the last ride through the controller with current settings |
| `.venv\Scripts\python tools\check_env.py` | Python, Bluetooth, ViGEmBus and packages all OK? |
| `.venv\Scripts\python tools\trigger_sweep.py` | Does the game respond to the virtual controller? |
| `.venv\Scripts\python tools\probe_rates.py` | How often the KICKR sends each data stream |
| `.venv\Scripts\python tools\experiments.py limiter --focus` | Testing only: game experiments (`limiter`, `holds`, `buttons`, `limitrange`, `limitstep`). Takes over the game window and drives the car |
| `.venv\Scripts\python -m bridge.summary --week` | Totals for the last 7 days (`--month`, `--all`); scripted `--sim` rides and rides under 1 min are left out |
| `.venv\Scripts\python -m bridge.cleanup --dry-run` | Which old logs the after-ride cleanup would delete (drop `--dry-run` to delete now) |
| `powershell -ExecutionPolicy Bypass -File scripts\shortcuts.ps1` | Desktop shortcuts for ride.bat and report.bat (setup does this too) |
| `report.bat` | Bundle crash reports, recent logs and the environment check into one zip to send to the developer |
| `.venv\Scripts\python -m bridge.summary` | Ride summary (trainer data only) for the last ride, or pass a `ride-*.csv` |
| `.venv\Scripts\python -m pytest -q` | Test suite (includes a short fuzz test of the whole bridge) |
| `.venv\Scripts\python tools\fuzz_bridge.py --minutes 10` | Testing only: long fuzz campaign, random corrupt trainer data through the real bridge (dry run) |

## New PC or broken install

```
Get-ChildItem -Recurse | Unblock-File
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

This installs Python 3.13, Git and the ViGEmBus driver (asks for admin), creates `.venv`, installs
packages, checks the environment and runs the tests. It is safe to run again.

## Troubleshooting

**If anything crashes**, the window says so and saves `logs/crash-*.txt`. Double-click **`report.bat`**:
it bundles the crash reports, recent logs, latest ride and an environment check into
`logs/report-*.zip` and opens the folder. Bluetooth addresses, your Windows user name, home folder,
computer name and e-mail addresses are removed; no screenshots, game settings or `settings.json` go in.
Attach the zip to a [new issue](https://github.com/jomarpueyo/slowroads-bridge/issues/new?template=bug_report.md).

| Symptom | Fix |
| --- | --- |
| `.venv\Scripts\python` not found | You're in the wrong folder: `cd` into `slowroads-bridge` first, or use the `.bat` files |
| Stuck on `scanning` | Wake the KICKR. Close the Wahoo app and Zwift (they hold the connection). New or replaced trainer: `ride.bat --trainer pair` |
| Status says `no FTMS data`, or the window prints "can't get data from the trainer after 3 tries" | The trainer connected but isn't offering its fitness data. Close Zwift, the Wahoo app and any **phone** app connected to it; remove it from Windows Bluetooth settings if it's paired there; then unplug the trainer for 10 s. The bridge keeps retrying on its own |
| Status says `no data` | The trainer is connected but sending nothing for 3 s. Throttle is held at 0. Pedal |
| Car doesn't move | Click into the game window (the controller only works while it's focused). Start the bridge before the game. Unplug real controllers |
| Car runs away | Speed control is off or in cruise mode: turn on the padlock, in limit mode. Or the gearbox is Manual: set Automatic |
| HUD gear shows **N**, engine revs but the car doesn't move | Gearbox is Manual (a PC crash on 2026-09-27 rolled the game's settings back to Manual). Vehicle → tuning → Gearbox: Automatic |
| First controller press after starting the game does nothing | The game only notices a controller after its first press. Press any button once, or it wakes when throttle is applied |
| Assist label says AUTOSPEED or AUTODRIVE | Press X on a controller (or use the game menu) until it says AUTOSTEER |
| Limit doesn't change at all | Third-person camera: the game ignores the wheel there. Switch to a first-person/interior view (the camera key is in Settings > Controls), then stop once so the bridge re-syncs |
| Car doesn't respond after a Slow Roads update | Updates can change controls (1.1.0 plans mouse pointer-lock and controller changes). Re-run `.venv\Scripts\python tools\experiments.py limitstep --focus` and tell Claude Code what happened |
| Virtual controller ignored, real controllers plugged in | The game only sees the first 4 controllers Windows reports. Unplug extras |
| Limit doesn't match your speed | Usually a game menu was open while the bridge changed the limit (the menu takes the scrolls). Stop pedalling until the car stops: the bridge re-syncs the count when you start again. Avoid opening game menus mid-ride |
| Recorder `speed-*.csv` empty | Speedometer covered or the game not in front. The ride still works without it |
