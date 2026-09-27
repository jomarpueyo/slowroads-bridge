# Research: improving the ride

As of 2026-09-27. V1 works: the KICKR CORE drives Slow Roads through a virtual Xbox pad.
This file collects what we know about the game, the trainer and similar projects, and ranks the
next improvements. No code has changed because of it yet. Items marked **unverified** need a test.

**Headline:** the first calibrated ride (09:29) showed the bridge's open-loop speed estimate was badly
wrong: 38–45 km/h against 75–97 km/h in the game, and the brake never engaged. **Resolved 10:27–10:47:**
the game's own speed limit now holds the speed, set by the bridge with the mouse wheel. It's verified
in-game and is the default `--mode limit`; see §8.

## 1. What the 09:29 ride showed

These are mid-ride numbers from `logs/*-20260927-092937*` (bridge estimate vs speedometer read by the
recorder, per minute).

| Minute | Power (W) | Bike (km/h) | Target (km/h) | Bridge estimate | Game speed | Estimate − game | Throttle |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 11 | 62 | 11.4 | 35.4 | 32.6 | 60.4 | −27.8 | 0.17 |
| 15 | 124 | 20.9 | 62.9 | 59.5 | 83.4 | −23.9 | 0.27 |
| 16 | 60 | 13.2 | 40.5 | 38.3 | 83.3 | −45.0 | 0.17 |
| 18 | 77 | 15.7 | 47.4 | 44.9 | 89.6 | −44.7 | 0.20 |

Over the first 21 minutes the average error was −15 km/h, and the brake was used 0% of the time.

What the second-by-second trace (minutes 14–16) shows:

1. **Light throttle doesn't settle.** At 0.27–0.31 throttle the car climbed 60 → 97 km/h without
   levelling off. The model predicted about 64 km/h. 97 km/h is also above the calibrated top speed
   of 93.5, possibly from a downhill or the vehicle's real limit.
2. **Zero throttle and light throttle are different regimes.** At 0 the car loses about 23 km/h/s near
   90 km/h (calibration), which looks like engine braking. At 0.08–0.15 it lost only about 4 km/h/s. The
   calibration fit put both into a single "drag" term, which made the model think light throttle holds
   a moderate speed.
3. **The car creeps.** With no input it sat at 4.2 km/h for over a minute (an automatic creeping).
   Calibration coast-downs also levelled off near 11 km/h.
4. **OCR gear readings** rose to 6 at around 90 km/h, so the automatic gearbox is shifting. Gear OCR is
   noisy (it often misreads the digit), so treat it as a hint only.

Conclusion: a single-equation open-loop model can't track this car. Gears, engine braking, creep
and terrain all change the response. The world is procedurally generated, and vehicles differ.

## 2. Try now, no code change: the in-game speed limit (unverified)

Slow Roads has a **speed control** with two modes, *cruise* (hold a target) and *max* (a limiter).
It's the padlock next to the speedometer. Setting it to limit mode at about 40–50 mph should stop the
runaway while the bridge still works the throttle. Whether the limiter caps throttle input from a
controller is not yet tested.

## 3. Slow Roads facts

| Fact | Source |
| --- | --- |
| Steam release 2026-09-23; patch 1.0.1 on 09-24 added "a short delay when braking to a stop before transitioning into reverse" and axis input for binary actions | [Steam community](https://steamcommunity.com/app/3431300) |
| 1.1.0 (target late September): controller mapping fixes, **force feedback** ("optimistic"). 1.2.0 (autumn): rain, day-night cycle | [Steam community](https://steamcommunity.com/app/3431300) |
| Vehicles: coupe, hatch, sports car, coach, and a **bike that is a motorbike** ("perfect lean into every corner"). Engines are electric or combustion (manual or automatic) | [Steam store](https://store.steampowered.com/app/3431300/Slow_Roads/) |
| Assists: automatic steering, automatic speed control, full autodrive. Traffic density, speed and lanes are configurable. Worlds are procedural and seeded, so a location can be revisited | [Steam store](https://store.steampowered.com/app/3431300/Slow_Roads/) |
| No modding API, telemetry or plugin interface is advertised | [Steam store](https://store.steampowered.com/app/3431300/Slow_Roads/) |
| Keyboard: W accelerate, S brake then reverse, Shift boost, R reset to road, F autodrive, U hide HUD. Cruise target is changed with the scroll wheel in steps of 5 (third-party guide, 2022 web version) | [slowxroads.com](https://slowxroads.com/blog/slow-roads-controls/) |

Checked locally in the installed game (read-only, nothing changed):

- **Electron 40.4.1 / Chromium 144.** Electron fuses are at defaults (`10110001`: RunAsNode on, node
  inspect arguments on, asar integrity validation off). So the standard Chromium
  `--remote-debugging-port` switch should work if added to Steam launch options (**unverified**).
- The app is a SvelteKit web app (`resources/app.asar`, 848 files). It reads controllers through the
  browser Gamepad API (`navigator.getGamepads`), so any XInput pad works, including ours.
- **Speed control is a set of bindable actions:** `ToggleSpeedControl`, `ToggleSpeedControlMode`,
  `IncSpeedControl` and `DecSpeedControl`. They sit in the same action table as the camera controls,
  so they are probably mappable to pad buttons (**unverified** in the controls menu). Modes are
  `Cruise` and `Max`. The target is rounded to **steps of 5 display units** (5 mph ≈ 8 km/h).
- Driver modes are logged as `AUTODRIVER: Set mode autosteer` and `autospeed`; auto speed follows a
  road-based speed target.
- The game's saved settings live in `%APPDATA%\slowroads\Local Storage` (LevelDB). Keys include
  `units`, `driveSide`, `autodrive`, per-vehicle settings and best 1 km times. There is no play log;
  `ride_recorder.py` snapshots this folder at the start and end of each ride.

## 4. KICKR CORE facts

| Fact | Source |
| --- | --- |
| FTMS Indoor Bike Data (0x2AD2) and Cycling Power (0x2A63) both notify at exactly **1.00 Hz** | our probe, `logs/probe-20260927-090253.log` |
| The trainer also exposes Wahoo private characteristics (`a026e002/e004/e023/e037…`) and the **Zwift trainer service** (`00000002-19ca-4651-86e5-fa29dcdd09d1`, which sent one message containing "ATX 01, STX 00") | our probe |
| Zwift protocol: notifications on `…0002`, commands on `…0003`, "RideOn" handshake, protobuf messages. Commands: simulation (wind, incline ×100, CWa ×10000 [Zwift sends 5100], Crr ×100000 [400]), **virtual gear ratio ×10000**, rider/bike weight, ERG target. Riding data carries power, cadence, speed ×100 and HR. Supported by KICKR CORE, Zwift Hub and JetBlack Victory. Warning: "coefficients … significantly different from what the trainer expects … very strange ways" | [Makinolo: Zwift trainer protocol](https://www.makinolo.com/blog/2024/10/20/zwift-trainer-protocol/) |
| Virtual shifting reached existing KICKR COREs by firmware in Feb 2024. Firmware v1.5.36 (2024-10-28) updated the Zwift protocol version | [Zwift Insider](https://zwiftinsider.com/kickr-core-firmware-v1-3-17/), [Zwift Insider](https://zwiftinsider.com/wahoo-firmware-20241028/) |
| **Race Mode** (10 Hz power) is a KICKR CORE 2 feature over WiFi; over Bluetooth it's Zwift only. The original CORE is 1 Hz | [Wahoo CORE 2 Q&A](https://www.wahoofitness.com/blog/wahoo-kickr-core-2-technical-qa/) |
| ANT+ trainer data is 4 Hz by the profile, but Wahoo's CORE 2 Q&A says its ANT+ broadcasts at 1 Hz. **Unverified** for our unit | [Cyclingnews](https://www.cyclingnews.com/features/what-is-ant-plus/), [Wahoo CORE 2 Q&A](https://www.wahoofitness.com/blog/wahoo-kickr-core-2-technical-qa/) |
| FTMS "Set Indoor Bike Simulation Parameters" (op 0x11: wind, grade, Crr, Cw) sets slope-based resistance. GoldenCheetah and qdomyos-zwift disagree 10× on Crr scaling, so test on hardware | [onyourleft #43](https://github.com/openzigs/onyourleft/issues/43) |
| The KICKR's reported **speed is flywheel speed**, set by gear × cadence, not by power. Our logs: 305 W gave 17 km/h and 128 W gave 11 km/h | our ride logs |
| Model still unknown: the unit advertises "KICKR CORE". Read the BLE Device Information model string (0x2A24) or check the Wahoo app to tell a CORE from a CORE 2 | — |

## 5. Prior art

| Project | What it does | Lesson for us |
| --- | --- | --- |
| [GTBike V](https://www.gtbikev.com/) (GTA V) | Reads incline, surface and wind from the game and sends them to the trainer. Speed is **virtual, from power and environment**: "the trainer won't be a trusted source for speed anymore" ([0.5.0.4](https://www.gtbikev.com/2021/06/23/new-version-0-5-0-4-ble-sterzo-bots-and-bikecity/)). `SlopeScale` 0–2, default 0.5 ([docs](https://github.com/gtbikev/docs/blob/master/mod/INDEX.md)). Also Sterzo steering, Zwift Play/Ride buttons, **native virtual gears on Zwift trainers incl. KICKR CORE** (0.7.5.4), auto-shifting, Strava/FIT ([news](https://www.gtbikev.com/news/)) | Compute speed from power, not trainer speed. Scaling slope by 0.5 feels realistic. It's two-way because GTA V exposes state through a script hook |
| [SimCycling](https://github.com/dmwnz/SimCycling) (Assetto Corsa) | Reads position and incline from shared memory, sends the throttle through vJoy to "replicate the home trainer speed onto the ingame car", and the slope through ANT+ FE-C | The same design as ours, but with **game telemetry closing the loop** |
| [USBcycle-EuroBikeSimulator](https://github.com/RootlessAgrarian/USBcycle-EuroBikeSimulator) (ETS2) | Arduino plus an ETS2 mod: pedal speed drives the throttle | Hardware route; ETS2 has an official telemetry SDK |
| [BikeControl](https://github.com/OpenBikeControl/bikecontrol) | Maps Zwift Click/Play/Ride buttons to keys or gamepad in any app; can add virtual shifting | Handlebar buttons could send cruise +/−, camera or brake |
| [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift), [SHIFTR](https://github.com/JuergenLeber/SHIFTR), [Kickr-Virtual-Shifting](https://github.com/Berg0162/Kickr-Virtual-Shifting) | Open implementations of FTMS, Wahoo Direct Connect and Zwift protocols | Reference code for trainer control and virtual gears |

## 6. Improvement options, ranked

| # | Idea | Fixes | Effort | Risk / unknowns |
| --- | --- | --- | --- | --- |
| 1 | **Let the game hold the speed.** Bind Inc/Dec Speed Control to pad buttons; the bridge sets the game's cruise target to bike speed × gear and presses pedal-driven throttle only for feel | Runaway, hills, vehicle differences (the game's own controller closes the loop) | M | Target steps are 5 mph; we must track the target by counting presses (reset to min at start); does manual throttle override cruise? |
| 2 | **Limiter mode plus throttle.** Speed control in `Max` mode set just above the target; bridge throttle stays moderate | Runaway | S | Is the limiter applied to controller throttle? Same 5 mph steps |
| 3 | **Separate the two regimes in the model.** Zero throttle = engine braking; any throttle = weak drag plus creep. Refit from calibration and ride data, add feedforward throttle and pulse between 0 and a small throttle | Better estimate if staying open-loop | M | Still blind to hills; per vehicle |
| 4 | **Ask the developer for telemetry** (speed, grade, surface over UDP or a local WebSocket, like sim-racing games). Force feedback is already on the 1.1.0 list, so input/output work is underway | Everything, officially | S for us | Their timeline |
| 5 | **Read game state via Chromium remote debugging** (`--remote-debugging-port` in Steam launch options, read speed and slope over CDP) | Closed loop plus **grade → trainer resistance** | M–L | Scoped out in the V1 overview ("no memory reading"); internal names are minified and change with patches; ask the developer first |
| 6 | **Speed from power** (GTBike V style) instead of flywheel speed | Effort matters, not gear choice; realistic feel | S | Needs grade for hills (from 4 or 5); flat-only otherwise |
| 7 | **Resistance on the trainer**: FTMS 0x11 grade, or the Zwift protocol simulation/virtual gears | Hills you can feel; gear changes without touching the bike | M | Needs a grade source; test scaling carefully |
| 8 | **Rumble → road feel.** vgamepad can receive the game's rumble (`register_notification`, large/small motor 0–255, Windows) | Surface/impact feel on the trainer | S once FFB ships | Depends on 1.1.0 FFB driving XInput rumble |
| 9 | **Faster input.** ANT+ dongle (4 Hz if the CORE sends it), CORE 2 Race Mode over Wahoo Direct Connect (WiFi) | Latency (today 1 Hz plus smoothing) | M | Model and firmware dependent |
| 10 | Extras: heart rate, FIT export/Strava, a Wahoo Headwind fan following car speed, replaying a world seed | Fun and training value | S each | — |

Road-bike reference (85 kg, CdA 0.32, Crr 0.004), for idea 6. Rider speed = power / (rolling +
gravity + aero), solved numerically:

| Power | −4% | Flat | +4% | +8% |
| --- | --- | --- | --- | --- |
| 100 W | 49.5 km/h | 25.9 | 9.2 | 5.0 |
| 200 W | 53.4 | 33.8 | 17.0 | 9.8 |
| 300 W | 56.6 | 39.3 | 23.3 | 14.4 |

```latex
P = \frac{v\left(m g\,(C_{rr}\cos\theta + \sin\theta) + \tfrac{1}{2}\rho\, C_dA\, v^2\right)}{\eta}
```

## 7. Experiments to run next

- [x] Can a pad button change the speed-control target? **No default one does** (tested all 12 face, shoulder, D-pad and stick buttons). The mouse wheel does: 5 units per notch, **range 5–125**. Binding "Cruise control increase/decrease" to pad buttons in the controls menu is untested.
- [x] Limit (`Max`) mode + controller throttle: **the limit caps throttle** and actively slows the car when lowered. Cruise mode is untested.
- [ ] Throttle-hold test per vehicle (`tools/experiments.py holds`): fixed trigger 0.03–0.2. Where does speed settle? Only needed for `--mode speed`.
- [ ] Which vehicle was used for calibration and for the 09:29 ride? Check `logs/game-*-start/`.
- [ ] Read the KICKR model string (BLE 0x180A/0x2A24): original CORE or CORE 2?
- [ ] With an ANT+ USB stick: does the CORE broadcast FE-C at 4 Hz?
- [ ] After 1.1.0: does the game send rumble to an XInput pad (vgamepad notification callback)?

## 8. In-game test results (2026-09-27 10:27–10:47)

All tests used `tools/experiments.py` or `python -m bridge --sim` with the recorder-style OCR monitor.
Logs are `logs/exp-*` and `logs/*-2026092710*`.

| Test | Result |
| --- | --- |
| Throttle 0.6 with the game **not focused** | Car didn't move. **The game only reads the controller while its window is focused** |
| Throttle 0.6 under a 40 mph limit | Held **63.9 km/h (39.7 mph)** flat. The limit caps controller throttle |
| Limit 40 → 20 mph at throttle 0.6 | 64 → 32 km/h in about 3 s, then held 32.2 km/h (20.0 mph). The game **actively slows** the car |
| Limit 20 → 40 mph | Back to 64 km/h in about 2 s |
| Default pad buttons | X cycles AUTOSTEER → AUTOSPEED → AUTODRIVE, A toggles the assist on and off, D-pad left/right change the scene. **None changes the limit** |
| Mouse wheel over the game | ±5 per notch, range **5–125** mph |
| Bridge `--mode limit --sim` (bike 0 → 15 → 20 → 8 → 0 km/h, gear 3) | Car held **25.0, 35.0 and 15.0 mph** exactly, then stopped. Bridge notch count = displayed limit in 15/16 readable samples (the 1 miss was OCR reading 30 as "39") |
| Same with `--speed-source power` | Correct; 6/6 limit samples matched |
| After the rounding fix (hysteresis 0.25) | 15 km/h × 3 = 45 km/h → **30 mph**, held 30.0; 8/8 matched |

Also found and fixed in the OCR used by the recorder and tests: the padlock icon ("0.0 a | NILES") and a
dropped decimal point ("29 7") both broke speed parsing.

Remaining limitations: 5 mph steps are coarse at low speed. The bridge scrolls with the real mouse
(cursor moves for about 0.1 s per step). Cruise mode and binding pad buttons to the limit are untested.
Hills now come from the game itself, but the trainer still gets no resistance (ideas 4, 5, 7).

## Sources

- [Steam store: Slow Roads](https://store.steampowered.com/app/3431300/Slow_Roads/) · [Steam community announcements](https://steamcommunity.com/app/3431300) · [Slow Roads controls (third-party)](https://slowxroads.com/blog/slow-roads-controls/)
- [GTBike V](https://www.gtbikev.com/) · [GTBike V mod docs](https://github.com/gtbikev/docs/blob/master/mod/INDEX.md) · [GTBike V 0.5.0.4 notes](https://www.gtbikev.com/2021/06/23/new-version-0-5-0-4-ble-sterzo-bots-and-bikecity/) · [GTBike V news](https://www.gtbikev.com/news/)
- [Makinolo: Zwift trainer protocol](https://www.makinolo.com/blog/2024/10/20/zwift-trainer-protocol/) · [Makinolo: Zwift Ride protocol](https://www.makinolo.com/blog/2024/07/26/zwift-ride-protocol/)
- [Zwift Insider: KICKR CORE virtual shifting firmware](https://zwiftinsider.com/kickr-core-firmware-v1-3-17/) · [Zwift Insider: Wahoo firmware Oct 2024](https://zwiftinsider.com/wahoo-firmware-20241028/) · [Wahoo KICKR CORE 2 technical Q&A](https://www.wahoofitness.com/blog/wahoo-kickr-core-2-technical-qa/)
- [openzigs/onyourleft #43 (FTMS control)](https://github.com/openzigs/onyourleft/issues/43) · [vgamepad README](https://github.com/yannbouteiller/vgamepad/blob/main/README.md)
- [SimCycling](https://github.com/dmwnz/SimCycling) · [USBcycle-EuroBikeSimulator](https://github.com/RootlessAgrarian/USBcycle-EuroBikeSimulator) · [BikeControl](https://github.com/OpenBikeControl/bikecontrol) · [SHIFTR](https://github.com/JuergenLeber/SHIFTR) · [Kickr-Virtual-Shifting](https://github.com/Berg0162/Kickr-Virtual-Shifting) · [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift)
- [Cyclingnews: what is ANT+](https://www.cyclingnews.com/features/what-is-ant-plus/) · [SmartBear: Electron remote debugging](https://support.smartbear.com/testleft/docs/using/configuring/web-apps/electron/index.html)
