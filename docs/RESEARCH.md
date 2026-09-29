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
| Model still unknown: the unit advertises "KICKR CORE" plus a 4-character ID. Read the BLE Device Information model string (0x2A24) or check the Wahoo app to tell a CORE from a CORE 2 | — |

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

## 9. Coasting and elevation (ride 11:39, 2026-09-27)

**Problem (rider feedback):** going downhill without pedalling, the car slows almost at once, which feels
wrong. The log shows why. In limit mode the target follows the KICKR's reported speed, and that decays as
the flywheel spins down (about 1 km/h/s, 20 → 0 km/h in about 20 s at 65–85 s). The ×3 gear makes that
about 3 km/h/s for the car. The limit steps down 5 mph at a time (40 → 30 mph within 5 s), and the game
**actively brakes** to each new limit. So coasting behaves like braking regardless of terrain, where a real
bike on a descent would hold or gain speed.

**Is there an elevation or grade metric?** Searched the installed game (read-only):

| Source | Result |
| --- | --- |
| Game logs / saved settings | None: no play log; Local Storage has settings and best times only |
| Debug overlay (`Toggle debug overlay`) | Only a three.js Stats frame-rate panel; no altitude or grade |
| HUD | Speed, gear, odometer, speed-control target. No elevation |
| Game internals | **Yes**: the car's pitch `G.pitch = G.rotation.z` (≈ road grade) and `G.position` (y = elevation) are computed every frame, and the chase camera uses the pitch. Only reachable from outside through Chromium remote debugging (idea 5), which the V1 scope excludes |
| Screenshots (testing only) | The camera tilts with car pitch, so the horizon's height on screen moves with grade. Possible to estimate, but noisy (hills, trees) and it's screen reading |
| Trainer | The KICKR reports no grade; it only receives one |

**Options for natural coasting, without a grade source.** The game already simulates gravity; the bridge
just has to stop braking the car when you stop pedalling:

1. **Coast hold.** When power drops below about 20 W, freeze the limit at its current value (don't follow the
   flywheel decay) and send a small "coast throttle" that roughly cancels the game's engine braking. The
   game's physics then decides: a descent holds or gains speed up to the frozen limit (plus an optional
   margin), and a climb slows the car. After a timeout (say 8 s), or when cadence stays 0, step the limit
   down gently (one notch every 2 s) to stop. Needs one test: the throttle where flat-road deceleration is
   bike-like (about 1 km/h/s). Candidates 0, 0.05, 0.1, 0.15 from 40 mph with `tools/experiments.py holds`.
   Ride 09:29 hints that 0.08–0.15 gives about 4 km/h/s at 90 km/h, while 0 gives about 23 km/h/s
   (engine braking).
2. **Slower limit decay.** Rate-limit downward limit changes while not pedalling (for example one notch per
   3 s). Simple, but still terrain-blind.
3. **Real grade via remote debugging** (idea 5): read `G.pitch` and `G.position.y`, which also enables
   trainer resistance on climbs. It's the most complete option and the one the original scope excludes.

## 10. Coast hold: implemented and tested in-game (2026-09-27 11:57–12:19)

Grade was measured for testing only, with the game's debug panel (F4), read by `tools/debugpanel.py`:
elevation `pos.y`, plus distance integrated from the panel's `speed` (its x/z OCR is unreliable because
the minus sign reads as a dash). The panel is not used while riding.

**Coast-throttle sweep** (`tools/experiments.py coast`, grade-corrected: flat decel = measured − 35.3 × grade):

| Coast throttle | Measured | Grade | Flat-equivalent |
| --- | --- | --- | --- |
| 0 | 8.6 km/h/s | +13.4% | about 3.9 (engine braking to a stop) |
| 0.03 | 3.7 | +9.3% | about 0.5 |
| 0.06 | −2.2 | −10.7% | about 1.5 |
| 0.10 | 1.9 | +3.5% | about 0.6 |
| 0.15 | 0.1 | +2.5% | about −0.8 (accelerates) |

A road bike coasting on the flat loses about 0.7 km/h/s at 30 km/h, so the default is **0.05**. Single
samples on steep terrain (±10–13%), so the value is a flag: `--coast-throttle`.

**Coast hold** (`bridge/limiter.py`, default on): when power is under 25 W and cadence under 20 rpm, the
limit freezes one step (5 mph) above where it was, and throttle goes to 0.05 for 12 s. After that,
throttle 0 and the limit steps down 5 mph every 2 s to stop. Pedalling again resumes at once.

Same simulated ride (`--sim`, bike 15 km/h then coast, 18 km/h then coast), real game, automatic motorbike:

| Moment | Old (`--coast-hold 0`) | Coast hold |
| --- | --- | --- |
| Stop pedalling on a −1.5 to −4.6% descent | Limit followed flywheel spin-down 30 → 5 mph; car **48 → 24 km/h in 6 s** | Limit held 35 mph; car **48 → 56 km/h** (gained speed downhill) |
| Stop pedalling on the flat, then +5% | Car 55 → 24 km/h in 9 s | Car 57 → 43 km/h in 9 s (about 1.5 km/h/s), slowing on the climb |
| After about 12 s without pedalling | — | Throttle 0, limit steps down, stopped in about 6 s |

Other findings this session: a PC crash rolled the game's saved settings back (gearbox to **Manual**, so
gear N and no movement), and the first controller press after a game restart only wakes the controller.
Both are in QUICKSTART troubleshooting. Windows OCR can hang indefinitely (the recorder froze for 15 min),
so every OCR call now has a 2 s timeout.

## 11. Faster updates: polling, Zwift channel, ANT+, virtual bike (2026-09-27 12:30–12:40)

**Can the trainer report faster than once a second?** Measured on this KICKR CORE (`tools/probe_rates.py`,
`tools/probe_zwift.py`):

| Channel | Rate |
| --- | --- |
| FTMS Indoor Bike Data (0x2AD2) | 1.00 Hz |
| Cycling Power (0x2A63) | 1.00 Hz |
| Zwift trainer protocol (`…0002`, after the "RideOn" handshake; riding-data message 0x03: power, cadence, speed ×100) | **1.00 Hz** (20 messages in 20 s). The trainer answered `RideOn` + `02 02` and logged `gap_params_change(1): 48, 48, 0, 960` and `ATX 02, STX 02` |
| ANT+ FE-C | Not testable: no ANT+ USB stick on this PC (no USB VID 0FCF). The ANT+ profile runs at 4 Hz, but Wahoo says the CORE broadcasts power at 1 Hz over ANT+. Worth one test if a stick is bought |
| Race Mode (10 Hz) | KICKR CORE 2 over WiFi only (Wahoo) |

The probe only sent the handshake: no resistance, gear or ERG commands.

**Our own method: a virtual bike** (`bridge/drive.py: VirtualBike`, now the default `--speed-source virtual`).
Like Zwift and GTBike V, the bridge simulates the rider between readings: speed is a physical state
stepped at 20 Hz from the latest power, `m dv/dt = P/v − (Crr m g + ½ ρ CdA v²)` (85 kg, CdA 0.32,
Crr 0.004). Speed then has inertia: no once-a-second steps, surges build, and coasting fades about
0.7 km/h/s at 30 km/h. It reads faster than the KICKR's flywheel speed, so the default gear for it is 2.0
(trainer source keeps 3.0). A 0 W packet with cadence ≥ 20 (a KICKR quirk) reuses the last real power.

Replaying ride 12:23 offline (`compare_sources.py` in the session scratchpad):

| | Trainer speed, gear 3 | Virtual bike, gear 2 |
| --- | --- | --- |
| Game-limit changes | 83 (17/min) | 44 (9/min) |
| Target jumps > 0.5 km/h in one 20 Hz tick | 123 | 0 (max 0.3) |
| 865 W sprint top target | 102 km/h | 83 km/h |

In-game `--sim` run (12:34): limit 15 → 20 → 25 → 30 → 35 → 40 → 45 mph and the car held each; the displayed
limit matched in 20/20 samples; both coasts were on descents and the car rolled 55 → 64 and up to 80 km/h.

**Other changes from ride 12:23:**
- Limit mode no longer caps the target at the speed-mode calibration (0.95 × 93.5 km/h); the sprint was held at 55 mph.
- The coast release was softened. It was throttle 0 and −5 mph every 2 s (60 → 18 km/h in 6 s); now it keeps the 0.05 coast throttle and steps every 4 s, so 64 → 0 km/h takes about 21 s (12:40 test).
- The status line is shortened to under 80 columns (it wrapped in the ride.bat console).
- At 12:28:12 the game showed a LOADING screen (a reset). Afterwards the game limit (25) differed from the bridge's count (30) until the next stop re-synced it.

## 12. Push bonus: rewarding hard efforts (2026-09-27 12:50)

Rider request: pushing hard should make the in-game limit rise faster and higher. The problem is physics:
road-bike speed grows with about the cube root of power, so doubling watts adds only about 26% speed.

`bridge/limiter.py` push bonus, on by default. It's driven by power smoothed over about 2 s, so a single
hard stroke doesn't trigger it:
1. **Gear ×(1 + 0.5 × effort)**, where effort ramps from 0 at 150 W to 1 at 400 W.
2. **Earlier step-up:** while pushing, the limit steps up as soon as the target passes the current step
   (normally it waits until halfway to the next), and it doesn't step back down until the target is a full
   step below.
3. **Throttle 0.6 → 1.0** at full push, so the game accelerates harder toward the new limit (the limit still
   caps the speed).

| Replay of ride 12:23 (virtual bike, gear 2) | No bonus | Push bonus |
| --- | --- | --- |
| Mean limit when smoothed power > 250 W | 44.5 mph | 65.0 mph |
| Mean limit at 80–150 W | 33.4 mph | 33.8 mph |
| Top limit (865 W sprint) | 55 mph | 80 mph |

In-game `--sim` test: 150 W held 30 mph. At 450 W the limit went 35 → 60 mph in 4 s and 80 mph in 16 s, and
the car followed 52 → 127 km/h. Back at 150 W it eased to 45 mph over about 10 s. The limit matched in 15/16
samples. Tune with `--push-boost`, `--push-easy-w` and `--push-hard-w`.

## 13. Resuming after a coast (ride 12:48, 2026-09-27 13:00)

**Rider feedback:** pedalling again after coasting dropped the car to a lower speed before the watts could
build. The log showed it: at 244 s the car was rolling 40 mph downhill under a 40 limit; resuming at 162 W
dropped the limit to 25 (car 40 → 25 mph). Also at 158 s (40 → 35) and 212 s (40 → 30). The cause: while
coasting, the virtual bike slows on flat-road physics while the game car may be rolling downhill, so on
resume the target is well below where the car is.

A second finding: the KICKR keeps reporting cadence for a couple of seconds after pedalling stops (0 W at
63 rpm), which delayed coast detection by 2–4 s while the limit slid down.

**Changes (`bridge/limiter.py`, `bridge/__main__.py`):**
- **Resume grace 8 s:** when pedalling resumes after a coast or release, the limit is held (step-ups are
  still allowed) while watts build.
- **Gentle step-down:** while riding, the limit drops at most one 5 mph step every 2.5 s.
- **Coast detection:** two consecutive packets under 25 W now count as not pedalling, whatever the cadence;
  a single 0 W packet mid-stroke is still ignored.

In-game `--sim` (200 W, 10 s coast, resume at 110 W): coasting held 50 mph (car 72 → 76 km/h downhill). On
resume the limit **stayed 50 for 8 s** (car about 49 mph), then eased 50 → 45 → 40 → 35 → 30 over about 8 s to
the 110 W steady state. The second coast was detected within 1 s. The limit matched in 31/32 samples.

## 14. Burst riding and lost notches (ride 02:28, 2026-09-29)

31:38 ride, 1899 packets, 0 bad; car and motorcycle. Recorder speedometer vs the bridge's counted limit
matched within 1-2 mph except in two stretches, both bugs:

**Coast-margin ratchet (min 6-9).** The rider rode in bursts: 3-5 s at 200-450 W, 2-4 s at 0 W. Each coast
set the limit to the current limit + 5 mph. Pedalling again held that as the resume floor for 8 s. The next
coast inside those 8 s added +5 again. The limit climbed 30 -> 100 mph while the target stayed around 35 mph,
and the game really reached about 114 km/h. **Fix:** a coast that starts inside the resume grace, with the
limit not above the grace floor, reuses that floor instead of adding the margin again. Replaying the ride
through the fixed planner: the limit stays 30-60 mph (the peaks are real push-bonus targets). While
pedalling it averaged 9 mph above target instead of 28.

**Notches lost to the game's menu (min 13-22).** At 02:42:36 the game's settings menu (Controls tab) was
open while the bridge stepped the limit 20 -> 5. The wheel scrolled the menu, not the limit, but the
bridge counted the notches. The game window was still focused and under the cursor, so the checks from
docs/SECURITY.md finding 2 can't tell. The padlock showed 20 while the bridge believed 5 (screenshot
02:42:46). Every limit was 15 mph high until the next full stop re-synced at minute 22.5. **Fix:** after
any stepping, the bridge fully re-homes (over-scrolls to the floor, under 1 s at 30 ms per notch) when it
next leaves the floor, i.e. when the rider starts again from a stop. It's harmless when the count is right,
because the game ignores scrolls below 5. A menu opened mid-ride can still leave the count off until the
next stop. Detecting the menu without reading the screen is untested; one idea is whether the game shows
the mouse cursor only in menus (GetCursorInfo).

## Sources

- [Steam store: Slow Roads](https://store.steampowered.com/app/3431300/Slow_Roads/) · [Steam community announcements](https://steamcommunity.com/app/3431300) · [Slow Roads controls (third-party)](https://slowxroads.com/blog/slow-roads-controls/)
- [GTBike V](https://www.gtbikev.com/) · [GTBike V mod docs](https://github.com/gtbikev/docs/blob/master/mod/INDEX.md) · [GTBike V 0.5.0.4 notes](https://www.gtbikev.com/2021/06/23/new-version-0-5-0-4-ble-sterzo-bots-and-bikecity/) · [GTBike V news](https://www.gtbikev.com/news/)
- [Makinolo: Zwift trainer protocol](https://www.makinolo.com/blog/2024/10/20/zwift-trainer-protocol/) · [Makinolo: Zwift Ride protocol](https://www.makinolo.com/blog/2024/07/26/zwift-ride-protocol/)
- [Zwift Insider: KICKR CORE virtual shifting firmware](https://zwiftinsider.com/kickr-core-firmware-v1-3-17/) · [Zwift Insider: Wahoo firmware Oct 2024](https://zwiftinsider.com/wahoo-firmware-20241028/) · [Wahoo KICKR CORE 2 technical Q&A](https://www.wahoofitness.com/blog/wahoo-kickr-core-2-technical-qa/)
- [openzigs/onyourleft #43 (FTMS control)](https://github.com/openzigs/onyourleft/issues/43) · [vgamepad README](https://github.com/yannbouteiller/vgamepad/blob/main/README.md)
- [SimCycling](https://github.com/dmwnz/SimCycling) · [USBcycle-EuroBikeSimulator](https://github.com/RootlessAgrarian/USBcycle-EuroBikeSimulator) · [BikeControl](https://github.com/OpenBikeControl/bikecontrol) · [SHIFTR](https://github.com/JuergenLeber/SHIFTR) · [Kickr-Virtual-Shifting](https://github.com/Berg0162/Kickr-Virtual-Shifting) · [qdomyos-zwift](https://github.com/cagnulein/qdomyos-zwift)
- [Cyclingnews: what is ANT+](https://www.cyclingnews.com/features/what-is-ant-plus/) · [SmartBear: Electron remote debugging](https://support.smartbear.com/testleft/docs/using/configuring/web-apps/electron/index.html)
