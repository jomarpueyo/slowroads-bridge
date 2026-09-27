# Quick start

Pedal a Wahoo KICKR CORE and the car in Slow Roads follows. The game steers itself.

## Every ride

1. Close the Wahoo app and Zwift. Unplug any real game controllers. Wake the KICKR (pedal a few strokes).
2. Double-click **`ride.bat`** in this folder. It starts two windows:
   - the bridge (live status line), and
   - a minimized recorder that logs the game side for tuning.
3. Start Slow Roads **after** the bridge says `connected`. In the game:
   - Auto-steer **on**. Auto speed and autodrive **off**.
   - Gearbox **Automatic** (or an electric motor). Manual gears break the throttle.
   - Leave the speedometer (bottom right) uncovered so the recorder can read it.
4. Ride. Stop pedalling and the car coasts, and brakes if needed. It never reverses.
5. Press **Ctrl+C** in the bridge window to finish. The recorder stops on its own.

The status line reads:
`03:12  connected   185 W   82 rpm  target  45.2  car~  43.8 km/h  thr  22%  brk   0%  1.0 pkt/s  bad 0`

- **target**: bike speed × gear ratio (what the car should do).
- **car~**: the bridge's estimate of the car's speed. The bridge has no game telemetry.
- **thr / brk**: what it is pressing on the virtual controller.

## Adjusting the feel

Pass options to `ride.bat` from a terminal in this folder, for example `ride.bat --gear 2.5`.

| Want | Option | Default |
| --- | --- | --- |
| Slower or faster car for the same pedalling | `--gear` (car km/h per bike km/h) | 3.0 |
| Gentler acceleration | `--ramp` (max throttle rise per second) | 0.2 |
| Lower peak throttle | `--max-throttle` | 0.6 |
| No braking at all | `--max-brake 0` | 0.6 |
| Test without the game | `--dry-run` (reads the bike, sends nothing) | off |
| Old watts-to-throttle behaviour | `--mode power` | speed |

To make a change permanent, add it under `"ride"` in `settings.json`, for example `"gear": 2.5`.

## When to recalibrate

Recalibrate after changing vehicle or the game's tuning sliders (speed factor, max RPM), or if the car
feels wrong. Stop the car, keep the game in front, then double-click **`calibrate.bat`**. It takes about
40 s, needs no bike, and updates `settings.json`. It won't save a fit it doesn't trust.

The current settings came from one automatic-gearbox run (2026-09-27 09:20). Top speed was about
93 km/h, and coasting slowed the car much faster at high speed than at low. The world is
procedurally generated, so hills, surfaces and vehicles change the car's behaviour; the ride
recordings are how we measure that.

## Where things are

| Path | What |
| --- | --- |
| `logs/ride-*.csv` | Every trainer packet: power, cadence, bike speed, raw bytes |
| `logs/drive-*.csv` | Every controller tick (20 Hz): target, estimate, throttle, brake |
| `logs/bridge-*.log` | Connects, drops, errors, settings used |
| `logs/speed-*.csv` | Recorder: speedometer read from the screen (~5 Hz) |
| `logs/shots-*/` | Recorder: screenshot every 5 s |
| `logs/game-*-start/`, `-end/` | Recorder: copy of the game's saved settings (vehicle, units) |
| `settings.json` | Calibrated car model and your ride preferences |
| `CLAUDE.md` | Decisions, verified facts, open questions (for Claude Code sessions) |

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
| `.venv\Scripts\python -m pytest -q` | Test suite |

## New PC or broken install

```
Get-ChildItem -Recurse | Unblock-File
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

This installs Python 3.13, Git and the ViGEmBus driver (asks for admin), creates `.venv`, installs
packages, checks the environment and runs the tests. It is safe to run again.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `.venv\Scripts\python` not found | You're in the wrong folder: `cd` into `slowroads-bridge` first, or use the `.bat` files |
| Stuck on `scanning` | Wake the KICKR. Close the Wahoo app and Zwift (they hold the connection) |
| Status says `no data` | The trainer is connected but sending nothing for 3 s. Throttle is held at 0. Pedal |
| Car doesn't move | Start the bridge before the game. Unplug real controllers. Click into the game window |
| Car stuck in one gear or runs away | Gearbox is Manual. Set it to Automatic |
| Recorder `speed-*.csv` empty | Speedometer covered or the game not in front. The ride still works without it |
