# Slow Roads × KICKR CORE bridge

Pedal power from a Wahoo KICKR CORE (Bluetooth FTMS) becomes the right trigger of a virtual
Xbox 360 pad, which Slow Roads reads as the throttle. The game auto-steers. Windows only.
Background and design history: docs/RESEARCH.md (the original V1 tech overview lived in a private doc).

## Layout
- `bridge/ftms.py` — BLE scan/connect/reconnect (bleak) + Indoor Bike Data parser
- `bridge/mapper.py` — EMA smoothing (time constant) + power→throttle mapping
- `bridge/pad.py` — vgamepad output; `NullPad` for `--dry-run`
- `bridge/ridelog.py` — ride CSV, event log, console status line
- `bridge/__main__.py` — `python -m bridge [--dry-run] [--p-min W] [--p-max W] [--gamma G] [--verbose]`
- `bridge/limiter.py` — limit mode (default): target -> game speed limit via mouse wheel
- `bridge/sim.py` — simulated trainer for `--sim` off-production tests
- `bridge/drive.py` — target speed (gear ratio, power->speed physics) and the `--mode speed` model
- `tools/experiments.py` — testing only: drives the live game (limiter, holds, buttons, limitrange, limitstep)
- `tools/trigger_sweep.py` — milestone 1 (virtual trigger 0→1 over 10 s)
- `tools/calibrate_car.py` — auto-calibrate car accel/coast/brake by reading the speedometer (OCR);
  `--watch N` only logs the speedometer. Writes logs/calibrate-*.csv + .log with bridge flags.
- `tools/speedo.py` — speedometer OCR (Windows.Media.Ocr, mss). HUD box at 1920x1080 is
  x 1420-1640, y 880-1020; shows e.g. "0.0 MILES PER HOUR" + "1 GEAR" (OCR often drops HOUR).
  **Calibration/testing only — never import it from bridge/.** Extras in requirements-calibration.txt.
- `tools/replay_ride.py` — replay a ride CSV through the controller, no hardware
- `tools/probe_rates.py` — notification rate of every trainer characteristic
- `tools/summarize_ride.py` — verify a ride CSV (stats + re-parse of raw hex)
- `tools/check_env.py` — environment check
- `scripts/setup.ps1` — one-time init (winget: Python 3.13, Git, ViGEmBus; venv; tests)

User-facing how-to is QUICKSTART.md (ride.bat, calibrate.bat, settings.json). Keep it current.
Research, prior art (GTBike V etc.) and the ranked improvement list are in docs/RESEARCH.md.
**Default is now `--mode limit` (bridge/limiter.py), verified in-game 2026-09-27 10:27–10:47.**
The ride 09:29 showed the open-loop `--mode speed` estimate is wrong (38–45 km/h vs 75–97 in game).
Limit mode sets the game's own speed limit (speed control ON, limit/"max" mode) with the mouse
wheel (5 display units/notch, range 5–125, counted, re-synced by over-scrolling at the floor) and
holds throttle 0.6; the game caps and actively slows the car. Facts: gamepad input only reaches the
game while its window is focused; default pad X cycles AUTOSTEER/AUTOSPEED/AUTODRIVE, A toggles the
assist, D-pad left/right change scene/weather; no default pad button changes the limit.
Off-production tests: tools/experiments.py and `python -m bridge --sim "t:kmh,..."` (bridge/sim.py).
Results in docs/RESEARCH.md §8.
**Coast hold (default, 2026-09-27):** not pedalling (<25 W and <20 rpm) freezes the limit +5 mph with
throttle 0.05 for 12 s, then releases (throttle 0, -5 mph / 2 s). In-game: descent 48 -> 56 km/h
instead of 48 -> 24. `--coast-hold 0` = old following. docs/RESEARCH.md §10.
**Speed source (default virtual, 2026-09-27):** every KICKR Bluetooth channel incl. Zwift protocol is 1.00 Hz
(measured); no ANT+ stick present. bridge/drive.py VirtualBike integrates power at 20 Hz (gear 2.0 default for it).
Limit mode no longer caps target at speed-mode top speed. docs/RESEARCH.md section 11.
**Push bonus (default):** smoothed power 150->400 W ramps gear x1.0->x1.5, limit steps up early, throttle 0.6->1.0
(user asked to reward hard efforts). docs/RESEARCH.md section 12.
**Resume grace (2026-09-27):** after a coast, pedalling holds the limit 8 s (up-steps allowed); riding step-downs
at most one per 2.5 s; two <25 W packets = coasting regardless of lingering cadence. docs/RESEARCH.md section 13.
The debug panel (F4) and tools/debugpanel.py are for TESTING ONLY (user: no debug screen while riding).
Grade for tests = elevation (pos.y) vs distance integrated from panel speed.

## Ride-time data (for tuning after a ride; same <stamp> across files)
- Bridge (never reads the screen): ride-*.csv (packets), drive-*.csv (20 Hz controller ticks),
  bridge-*.log. Writes logs/active-ride.txt while running.
- tools/ride_recorder.py (started by ride.bat; observes only): speed-*.csv (speedometer OCR ~5 Hz),
  shots-*/ (screenshot every 5 s), game-*-start|end/ (copy of %APPDATA%\slowroads\Local Storage).
  Slow Roads writes no play log; that copy is the only game-side state.
- Analyse by aligning wall_time columns: actual speed (speed-) vs car_est_kmh (drive-).
- The car model is NOT fixed: the world is procedurally generated (hills, surfaces) and vehicles
  differ. Current settings.json is one flat-ish automatic run (09:20). Expect estimate drift on
  hills and per vehicle; use recordings to decide on per-vehicle settings or drift correction.

## Commands (from this folder)
- Tests: `.venv\Scripts\python -m pytest -q`
- Env check: `.venv\Scripts\python tools\check_env.py`
- Read + log only: `.venv\Scripts\python -m bridge --dry-run`
- Full run: `.venv\Scripts\python -m bridge`
- Verify newest ride: `.venv\Scripts\python tools\summarize_ride.py`

## Verifying data (logging)
Every run writes to `logs/` (git-ignored):
- `ride-YYYYMMDD-HHMMSS.csv` — one row per trainer packet: t_s, wall_time, power_w, cadence_rpm,
  speed_kmh, smoothed_w, throttle, flags, raw_hex, error. Flushed per row.
- `bridge-YYYYMMDD-HHMMSS.log` — connects, disconnects, malformed packets, errors, config at start.
- `sweep-*.log` — milestone 1 runs. `setup-*.log` — setup transcripts.
To check a ride, run `tools/summarize_ride.py` rather than reading the whole CSV; it reports packet
rate, max gap, flags seen, min/mean/max per field, and exits 1 if raw_hex re-parses to a different
power than logged. Raw hex of real KICKR packets is the ground truth for parser fixes: add captured
packets to `tests/test_bridge.py`.

## Decisions
- Data flows one way: never read game memory, mod the game, or send anything to the trainer.
- Default `--mode speed` (bridge/drive.py): the game treats the trigger as an accelerator, so
  watts->throttle ran away (ride 08:53: throttle mean 0.13, car still too fast). Target car speed =
  trainer speed x `--gear` (3.0), capped at 95% of top speed; open-loop car-speed estimate from
  drive.car_accel (accel, constant + speed-squared drag, brake, top speed), loaded from
  settings.json (written by tools/calibrate_car.py; built-in defaults are guesses); throttle capped 0.6,
  rises <= 0.2/s. Brake on the left trigger only above 8 km/h estimated and <= 3 s per hold
  (patch 1.0.1: braking to a stop goes into reverse). `--max-brake 0` disables braking.
  `--mode power` keeps the original watts->throttle mapping, no brake.
- Check controller changes offline: `tools/replay_ride.py logs/ride-....csv [flags]`.
- KICKR Indoor Bike Data is 1 Hz and BLE is push, not poll. `tools/probe_rates.py` measures every
  notify characteristic. Result 2026-09-27 (logs/probe-20260927-090253.log): Indoor Bike Data and
  Cycling Power 0x2A63 are both exactly 1.00 Hz; nothing faster exists. 1 Hz is the input ceiling.
- Throttle forced to 0 when no power packet for 3 s (stale timeout).
- Output at 20 Hz regardless of trainer notify rate; smoothing tau 2 s, time-based alpha.
- Starting mapping: P_min 50 W, P_max 250 W, gamma 1.0 — tuning values, not measured.
  Open question in the doc: full throttle at 250 W or ~200 W. Tune with `--p-max`.
- ViGEmBus 1.22.0 is the final release (retired 2023); keep the installer.

## Verified facts
- FTMS service 0x1826, Indoor Bike Data 0x2AD2; flags uint16 LE; bit 0 inverted (speed present when clear).
- Field order before power: speed(2), avg speed(2), cadence(2, 0.5 rpm), avg cadence(2), distance(3),
  resistance(2, signed), power(2, signed, W).
- KICKR CORE allows 3 BLE connections. Slow Roads: separate auto-steer / auto-speed / autodrive, Win10+.

- Milestone 1 passed 2026-09-27 (logs/sweep-20260927-084929.log): Slow Roads detects the ViGEm
  virtual pad, and the right trigger is proportional — the car sped up smoothly over a 0→1 sweep in 10 s.
  XInput read-back: throttle 0.25/0.5/1.0 → trigger 64/128/255.

- Milestone 2 passed 2026-09-27 (logs/ride-20260927-085102.csv): the KICKR CORE
  advertises the FTMS UUID; flags are 0x0044 on every packet (speed, cadence, power); notify rate is
  1 Hz (max gap 1.08 s); 38/38 packets parsed. Captured packets are in tests/test_bridge.py.
- At 1 Hz, tau 2 s makes smoothed power lag ~2 s behind a surge (305 W peak -> 238 W smoothed two
  packets later). Revisit tau in milestone 4.

- Calibration runs 2026-09-27 09:12/09:13 (manual gearbox, no fit): run 1 the game ignored a pad
  created at the first throttle command (now created before the countdown). Run 2: trigger 0.3 for
  15 s did not move the car, 0.6 did (dead zone between) -> bridge `--deadzone`; in manual gear 2
  the car hit ~121 km/h and held (rev limit), and on release fell to 0 in ~2.5 s (engine braking).
  Calibrate with the gearbox on Automatic.

## Unverified / open
- Does auto-steer work while throttle stays manual?

## Milestones
1. Controller check — `tools/trigger_sweep.py` with the Slow Roads demo. Stop and rethink if the car doesn't respond.
2. Bluetooth check — `python -m bridge --dry-run` while pedaling; note flags and rate.
3. Parser — add captured packets as tests; compare watts with the Wahoo app.
4. Mapper — dry run, check smoothing feels responsive but not jumpy.
5. Full loop against the demo; tune P_min, P_max, gamma.
6. Calibration ride — hold 100/150/200 W, note in-game speed at each.

## Before a ride
Close the Wahoo app and Zwift. Disconnect real controllers. Start the bridge before the game.
In Slow Roads: auto-steer ON, auto speed and autodrive OFF, accelerate on right trigger,
electric or automatic transmission, traffic off for early tests.
