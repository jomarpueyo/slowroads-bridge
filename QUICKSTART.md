# Quick start

Pedal a Wahoo KICKR CORE and the car in Slow Roads follows. The game steers itself.

## Every ride

1. Close the Wahoo app and Zwift. Unplug any real game controllers. Wake the KICKR (pedal a few strokes).
2. Double-click **`ride.bat`** in this folder. It starts two windows:
   - the bridge (live status line), and
   - a minimized recorder that logs the game side for tuning.
3. Start Slow Roads **after** the bridge says `connected`. In the game:
   - Assist **AUTOSTEER**. On the controller, X cycles AUTOSTEER → AUTOSPEED → AUTODRIVE and
     A turns the assist on and off. The bridge never presses them.
   - **Speed control on, in limit mode:** the padlock next to the speedometer, showing a number.
     The bridge sets that number.
   - Gearbox **Automatic** (or an electric motor).
   - **Click into the game and leave it focused.** The game ignores the controller while another
     window is in front, and the status line then shows `(game not focused)`.
   - Leave the speedometer (bottom right) uncovered so the recorder can read it.
4. Ride. The bridge turns your speed into the game's speed limit (5 mph steps), and the game holds
   the car at that limit, uphill and down. Stop pedalling and the limit steps down, so the car slows
   and stops. It never reverses.
5. Press **Ctrl+C** in the bridge window to finish. The recorder stops on its own.

**Don't touch the mouse wheel over the game during a ride.** The bridge changes the limit by
scrolling, and it counts its own scrolls. It briefly moves the cursor to the left side of the
screen to scroll, then puts it back. If the limit ever looks wrong, stop pedalling for a few
seconds: going back to 5 mph re-syncs the count.

The status line reads:
`03:12  connected   185 W   82 rpm  target  45.0  game limit  30 mph  thr  60%  brk   0%  1.0 pkt/s  bad 0`

- **target**: bike speed × gear ratio (what the car should do), in km/h.
- **game limit**: the speed limit the bridge has set in the game (the game holds the car there).
- **thr / brk**: what it is pressing on the virtual controller. In limit mode the throttle stays at
  0.6 while you pedal; the game caps the speed.

## Adjusting the feel

Pass options to `ride.bat` from a terminal in this folder, for example `ride.bat --gear 2.5`.

| Want | Option | Default |
| --- | --- | --- |
| Slower or faster car for the same pedalling | `--gear` (car km/h per bike km/h) | 3.0 |
| Speed from effort (watts, road-bike physics) instead of flywheel speed | `--speed-source power` (and `--rider-kg`) | trainer |
| Gentler or harder acceleration up to the limit | `--drive-throttle` | 0.6 |
| Game set to km/h | `--units km/h` | mph |
| Test without the bike (scripted ride) | `--sim "0:0,5:15,40:15,55:0"` (seconds:bike km/h) | off |
| Test without the game | `--dry-run` (reads the bike, sends nothing) | off |
| Previous model-based control (no in-game limit) | `--mode speed` (uses `--ramp`, `--max-throttle`, `--max-brake`) | limit |
| Old watts-to-throttle behaviour | `--mode power` | limit |

To make a change permanent, add it under `"ride"` in `settings.json`, for example `"gear": 2.5`.

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
| `.venv\Scripts\python tools\experiments.py limiter --focus` | Testing only: game experiments (`limiter`, `holds`, `buttons`, `limitrange`, `limitstep`). Takes over the game window and drives the car |
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
| Car doesn't move | Click into the game window (the controller only works while it's focused). Start the bridge before the game. Unplug real controllers |
| Car runs away | Speed control is off or in cruise mode: turn on the padlock, in limit mode. Or the gearbox is Manual: set Automatic |
| HUD gear shows **N**, engine revs but the car doesn't move | Gearbox is Manual (a PC crash on 2026-09-27 rolled the game's settings back to Manual). Vehicle → tuning → Gearbox: Automatic |
| First controller press after starting the game does nothing | The game only notices a controller after its first press. Press any button once, or it wakes when throttle is applied |
| Assist label says AUTOSPEED or AUTODRIVE | Press X on a controller (or use the game menu) until it says AUTOSTEER |
| Limit doesn't match your speed | Stop pedalling for a few seconds: the limit drops to 5 mph and the bridge's count re-syncs |
| Recorder `speed-*.csv` empty | Speedometer covered or the game not in front. The ride still works without it |
