"""Entry point: python -m bridge [--dry-run] [--gear 3.0] [--mode limit|speed|power] ..."""

import argparse
import asyncio
import logging
import os
import sys
import time
from types import SimpleNamespace
from dataclasses import replace
from pathlib import Path

from . import settings
from .drive import DriveConfig, DriveOutput, SpeedController, VirtualBike, bike_speed_from_power, trigger_value
from .ftms import MalformedPacket, parse_indoor_bike_data, run_reader
from .overlay import Attention, LiveStats, Overlay
from .limiter import LimitConfig, SpeedLimitPlanner, WheelActuator, display_to_kmh
from .mapper import MapperConfig, ThrottleMapper
from .cleanup import old_entries, remove
from .crashreport import run_main
from .cues import Cues
from .hotkeys import HELP as HOTKEY_HELP, Hotkeys
from .pad import NullPad, VirtualPad
from .coach import Coach
from .companion import Companion
from .ridebook import analyse as analyse_ride
from .summary import advice, compare_line, format_advice, format_summary, save_summary, summarize_csv
from .workouts import KEYS as WORKOUT_KEYS, WorkoutRunner, build as build_workout
from .units import KM_PER_MILE
from .trainer import GRAVEL_CRR, Texture, TrainerControl, decide as decide_resistance
from .gamestate import RoadWatcher
from . import motivation as mo
from .ridelog import (ACTIVE_RIDE, LOG_DIR, DriveLog, RideLog, close_event_log, format_status,
                      setup_event_log, timestamp)

log = logging.getLogger("bridge")
OUTPUT_HZ = 20
STEAM_APP_ID = 3431300  # Slow Roads
GEAR_STEP, GEAR_MIN, GEAR_MAX = 0.25, 0.5, 6.0


def parse_args(argv=None) -> argparse.Namespace:
    m, d = MapperConfig(), DriveConfig()
    saved = settings.load()
    ap = argparse.ArgumentParser(prog="bridge", description="KICKR CORE -> Slow Roads throttle and brake")
    ap.add_argument("--dry-run", action="store_true", help="read and log only; no virtual pad")
    lc = LimitConfig()
    ap.add_argument("--mode", choices=["limit", "speed", "power"], default="limit",
                    help="limit: set the game's speed limit to the target and let the game hold it "
                         "(default; needs in-game speed control ON in limit mode); "
                         "speed: open-loop model with throttle+brake; power: legacy watts->throttle")
    ap.add_argument("--speed-source", choices=["virtual", "trainer", "power"], default="virtual",
                    help="virtual: simulated road bike stepped at 20 Hz from power (smooth, default); "
                         "trainer: KICKR flywheel speed (gear x cadence, 1 Hz steps); "
                         "power: steady-state road-bike speed from smoothed watts")
    ap.add_argument("--rider-kg", type=float, default=85.0, help="rider + bike mass for virtual/power")
    lg = ap.add_argument_group("limit mode")
    lg.add_argument("--units", choices=["mph", "km/h"], default=lc.units, help="the game's speed units")
    lg.add_argument("--drive-throttle", type=float, default=lc.drive_throttle,
                    help="throttle held while pedalling (the game caps speed at the limit)")
    lg.add_argument("--coast-throttle", type=float, default=lc.coast_throttle,
                    help="throttle while coasting; ~0.05 cancels engine braking so gravity decides")
    lg.add_argument("--coast-hold", type=float, default=lc.coast_hold_s,
                    help="seconds of coasting before easing down to a stop (0 = old behaviour)")
    lg.add_argument("--coast-margin", type=int, default=lc.coast_margin,
                    help="limit headroom while coasting, display units (room to speed up downhill)")
    lg.add_argument("--push-boost", type=float, default=lc.push_boost,
                    help="extra gear at hard effort: 0.5 = +50%% at --push-hard-w and above (0 = off)")
    lg.add_argument("--push-easy-w", type=float, default=lc.push_easy_w, help="watts where the push bonus starts")
    lg.add_argument("--push-hard-w", type=float, default=lc.push_hard_w, help="watts for the full push bonus")
    ap.add_argument("--sim", metavar="PROFILE",
                    help='test without the bike: "seconds:bike_kmh,..." e.g. "0:0,5:15,40:15,55:0"')
    g = ap.add_argument_group("speed mode (defaults come from settings.json when present)")
    g.add_argument("--gear", type=float, default=None,
                   help="car km/h per bike km/h (default 2.0 for virtual/power speed, 3.0 for trainer)")
    g.add_argument("--accel", type=float, default=d.accel_kmh_s, help="car km/h/s at full throttle (calibrate)")
    g.add_argument("--coast", type=float, default=d.coast_kmh_s, help="constant drag, km/h/s (calibrate)")
    g.add_argument("--drag-quad", type=float, default=d.drag_quad, help="speed-squared drag (calibrate)")
    g.add_argument("--brake-rate", type=float, default=d.brake_kmh_s, help="car km/h/s at full brake (calibrate)")
    g.add_argument("--top-speed", type=float, default=d.top_speed_kmh, help="car top speed, km/h (calibrate)")
    g.add_argument("--max-throttle", type=float, default=d.max_throttle)
    g.add_argument("--max-brake", type=float, default=d.max_brake, help="0 disables braking")
    g.add_argument("--ramp", type=float, default=d.throttle_ramp_up, help="max throttle rise per second")
    g.add_argument("--deadzone", type=float, default=0.0,
                   help="trigger value below which the game ignores throttle (0 with automatic gearbox)")
    p = ap.add_argument_group("power mode")
    p.add_argument("--p-min", type=float, default=m.p_min, help="watts that read as coasting")
    p.add_argument("--p-max", type=float, default=m.p_max, help="watts for full throttle")
    p.add_argument("--gamma", type=float, default=m.gamma, help="throttle curve exponent")
    p.add_argument("--tau", type=float, default=m.tau_s, help="power smoothing time constant, s")
    ap.add_argument("--name", default=None,
                    help="also accept a device by name when pairing (only if it doesn't advertise FTMS)")
    ap.add_argument("--trainer", metavar="ADDRESS|pair",
                    help="Bluetooth address of the trainer to use, or 'pair' to forget the saved one and "
                         "pair with the first FTMS trainer found (saved to settings.json)")
    q = ap.add_argument_group("comfort (settings.json keys: sounds, hotkeys, launch_game, idle_end, keep_days)")
    q.add_argument("--no-sounds", dest="sounds", action="store_false", default=True,
                   help="no beeps (connected, trainer lost, game not in front, re-sync, gear, pause, ride end)")
    q.add_argument("--no-hotkeys", dest="hotkeys", action="store_false", default=True,
                   help="ignore F6/F7 gear, F8 pause, F9 re-sync (only read while the game is in front)")
    q.add_argument("--no-launch-game", dest="launch_game", action="store_false", default=True,
                   help="don't start Slow Roads through Steam once the trainer is connected")
    q.add_argument("--idle-end", type=float, default=3.0,
                   help="end the ride after this many minutes without pedalling (0 = never)")
    q.add_argument("--no-overlay", dest="overlay", action="store_false", default=True,
                   help="no trainer-data overlay at the top right of the game (F10 hides it during a ride)")
    q.add_argument("--overlay-fade", type=float, default=20.0,
                   help="seconds before the overlay's numbers fade to dim (they light up again on each minute, "
                        "mile, surge or high cadence); 0 = always bright")
    q.add_argument("--overlay-dim", type=float, default=0.35, help="brightness of faded numbers (0.1-1)")
    q.add_argument("--ftp", type=float, default=0.0,
                   help="your FTP in watts for the overlay's %%FTP and zone (0 = estimate: 95%% of your best "
                        "20 min in past rides)")
    c = ap.add_argument_group("coach (settings.json keys: rider_kg, weekly_rides, weekly_minutes, comfort_break)")
    c.add_argument("--workout", choices=WORKOUT_KEYS + ("suggested",), default=None,
                   help="ride a guided workout on the overlay (python -m bridge.workouts lists them)")
    c.add_argument("--menu", action="store_true",
                   help="at the start, offer today's suggested workout and the list (ride.bat uses this)")
    c.add_argument("--comfort-break", type=float, default=20.0,
                   help="stand-up-and-stretch reminder every N minutes of riding (0 = off)")
    c.add_argument("--no-drink", dest="drink", action="store_false", default=True,
                   help="no 'drink water' reminder with the 15 min check-ins")
    c.add_argument("--weekly-rides", type=int, default=3, help="weekly goal: rides (or --weekly-minutes)")
    c.add_argument("--weekly-minutes", type=int, default=90, help="weekly goal: riding minutes")
    c.add_argument("--no-ghost", dest="ghost", action="store_false", default=True,
                   help="don't compare with your last similar ride during the ride")
    c.add_argument("--no-feel", dest="feel", action="store_false", default=True,
                   help="don't ask 'how did it feel?' after the ride")
    c.add_argument("--no-dashboard", dest="dashboard", action="store_false", default=True,
                   help="don't open the ride book charts (logs/dashboard.html) in your browser after the ride")
    t = ap.add_argument_group("trainer resistance (settings.json keys: resistance, erg, road_feel, gravel, rumble)")
    t.add_argument("--no-resistance", dest="resistance", action="store_false", default=True,
                   help="never control the trainer's resistance (it stays as it is, like before 2026-10-05)")
    t.add_argument("--no-erg", dest="erg", action="store_false", default=True,
                   help="don't hold workout targets in ERG; use the road feel instead")
    t.add_argument("--no-road-feel", dest="road_feel", action="store_false", default=True,
                   help="no flat-road simulation outside ERG (the trainer's own default resistance)")
    t.add_argument("--gravel", dest="gravel", action="store_const", const=True, default=None,
                   help="force gravel road feel (default: follow the road chosen in Slow Roads; G in the menu)")
    t.add_argument("--tarmac", dest="gravel", action="store_const", const=False,
                   help="force tarmac road feel")
    t.add_argument("--rumble", type=float, default=0.3,
                   help="gravel texture: how much the rolling resistance wanders, 0 (smooth) to 1")
    q.add_argument("--keep-days", type=float, default=30.0,
                   help="after a ride, delete bulky logs older than this (ride CSVs and summaries kept; 0 = never)")
    ap.add_argument("--log-dir", type=Path, default=LOG_DIR)
    ap.add_argument("--verbose", action="store_true", help="echo the event log to the console")
    ap.set_defaults(**saved)  # after every add_argument, or the later ones ignore settings.json
    args = ap.parse_args(argv)
    if args.gear is None:
        args.gear = d.gear_ratio if args.speed_source == "trainer" else 2.0
    return args


def _surface_text(gravel, detected) -> str:
    if gravel is None:
        return f"auto (the game's road: {detected or 'unknown yet'})"
    return "GRAVEL (forced)" if gravel else "tarmac (forced)"


def _console() -> bool:
    return bool(sys.stdin) and sys.stdin.isatty() and sys.platform == "win32"


def read_key(timeout_s: float) -> str | None:
    """One key press from the console, or None after timeout_s (console version only)."""
    import msvcrt

    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if msvcrt.kbhit():
            return msvcrt.getwch()
        time.sleep(0.05)
    return None


def choose_workout(coach: Coach, timeout_s: float = 20.0, gravel=None, detected=None):
    """Console start menu: Enter or 20 s = free ride, 1 = today's suggestion, 2.. = any workout, G = road surface
    auto -> gravel -> tarmac. Returns (workout key or None, gravel: None = auto, True, False)."""
    if not _console():
        return None, gravel
    from .workouts import title_of

    key = coach.suggest()[0]
    options = [key] + [k for k in WORKOUT_KEYS if k != key]
    print(coach.brief_text() if coach.rides else "first ride: welcome!")
    print("\nRide:  [Enter] free ride")
    for i, k in enumerate(options, 1):
        print(f"       [{i}] {title_of(k, coach)}" + ("   <- suggested" if i == 1 else ""))
    print(f"       [G] road feel: {_surface_text(gravel, detected)} (press to switch)")
    print(f"Choose (free ride in {timeout_s:.0f} s): ", end="", flush=True)
    while (ch := read_key(timeout_s)) in ("g", "G"):  # G switches the road feel and restarts the countdown
        gravel = True if gravel is None else (False if gravel else None)
        print(f"\n       road feel: {_surface_text(gravel, detected)}\nChoose: ", end="", flush=True)
    if ch and ch.isdigit() and 1 <= int(ch) <= len(options):
        print(ch)
        return options[int(ch) - 1], gravel
    print("free ride")
    return None, gravel


def ask_feel(log_dir: Path, stamp: str, timeout_s: float = 30.0) -> int | None:
    """Console version, after the ride: 'How did it feel?' 1-5, one key (Enter or 30 s skips)."""
    if not _console():
        return None
    print("\nHow did it feel?  1 easy  2 comfortable  3 moderate  4 hard  5 very hard  (Enter skips): ",
          end="", flush=True)
    ch = read_key(timeout_s)
    if ch and ch in "12345":
        mo.set_feel(log_dir, stamp, int(ch))
        print(f"{ch} ({mo.FEEL_WORDS[int(ch)]}), saved")
        return int(ch)
    print("skipped")
    return None


def open_dashboard(log_dir: Path) -> None:
    """After a real ride: refresh logs/dashboard.html (this ride included) and open it in the browser."""
    try:
        from .dashboard import write_dashboard

        out = write_dashboard(log_dir)
        os.startfile(str(out))
        print(f"ride book charts opened in your browser: {out}", flush=True)
    except Exception:
        log.exception("could not open the dashboard")


def drive_config(args: argparse.Namespace) -> DriveConfig:
    # In limit mode the game owns top speed. Ride 12:23 hit an 88.8 km/h target ceiling (0.95 x the
    # speed-mode calibration's 93.5) during an 865 W sprint, capping the limit at 55 mph.
    top = 0.0 if args.mode == "limit" else args.top_speed
    return DriveConfig(
        gear_ratio=args.gear, accel_kmh_s=args.accel, coast_kmh_s=args.coast,
        drag_quad=args.drag_quad, brake_kmh_s=args.brake_rate, top_speed_kmh=top,
        max_throttle=args.max_throttle, max_brake=args.max_brake, throttle_ramp_up=args.ramp,
    )


PLAUSIBLE = {"power_w": (0, 3000), "cadence_rpm": (0, 250), "speed_kmh": (0, 120)}


def plausible(bike):
    """Readings no rider or trainer can produce (corrupt or foreign packets) become 'not reported',
    so they can't drive the car. Returns the same object when everything is plausible."""
    bad = {k: None for k, (lo, hi) in PLAUSIBLE.items()
           if getattr(bike, k) is not None and not lo <= getattr(bike, k) <= hi}
    return replace(bike, **bad) if bad else bike


async def run(args: argparse.Namespace, source_fn=None, hooks=None) -> None:
    """One ride. source_fn(on_packet, on_state) is an optional coroutine function supplying trainer
    packets instead of Bluetooth (tests and fuzzing); the ride ends when the source returns.

    hooks (the ride window, bridge/app.py) is optional: hooks.status(dict) once a second, hooks.message(text)
    for coach messages, hooks.stop (a threading.Event) ends the ride, and hooks.finished(result) gets the
    summary once the ride is over. With hooks there is no console menu, feel prompt or browser dashboard."""
    stamp = timestamp()
    event_path = setup_event_log(args.log_dir, stamp, args.verbose)
    ride = RideLog(args.log_dir, stamp)
    drive_log = DriveLog(args.log_dir, stamp, ride.start)
    marker = Path(args.log_dir) / ACTIVE_RIDE.name  # next to the logs (tools/ride_recorder.py follows it)
    try:
        marker.write_text(stamp, encoding="utf-8")
    except OSError:
        log.warning("could not write %s", marker)
    mapper = ThrottleMapper(MapperConfig(p_min=args.p_min, p_max=args.p_max, gamma=args.gamma, tau_s=args.tau))
    controller = SpeedController(drive_config(args))
    planner = SpeedLimitPlanner(LimitConfig(
        units=args.units, drive_throttle=args.drive_throttle, coast_throttle=args.coast_throttle,
        coast_hold_s=args.coast_hold, coast_margin=args.coast_margin, push_boost=args.push_boost,
        push_easy_w=args.push_easy_w, push_hard_w=args.push_hard_w))
    actuator = WheelActuator(dry_run=args.dry_run)
    pad = NullPad() if args.dry_run else VirtualPad()
    vbike = VirtualBike(mass_kg=args.rider_kg)
    cues = Cues(enabled=bool(args.sounds))
    hotkeys = Hotkeys(enabled=bool(args.hotkeys))
    actuator.on_home = lambda: cues.play("resync")
    stats = LiveStats()
    try:
        coach = Coach.from_settings(Path(args.log_dir), exclude=ride.path, rider_kg=args.rider_kg, ftp=args.ftp)
        coach.weekly_rides, coach.weekly_minutes = max(1, args.weekly_rides), max(10, args.weekly_minutes)
    except Exception:
        log.exception("could not load the ride book")
        coach = Coach([], args.ftp)
    ftp, ftp_source = coach.ftp()
    ftp_estimated = ftp_source == "estimate"
    if args.gravel is not None:  # settings.json "gravel": 0/1 forces it like --tarmac/--gravel
        args.gravel = bool(args.gravel)
    road = RoadWatcher()
    if source_fn is None:
        road.poll(0.0)  # the road chosen in Slow Roads (bridge/gamestate.py), refreshed every 15 s
    workout_key = args.workout
    if workout_key == "suggested":
        workout_key = coach.suggest()[0]
    if args.menu and workout_key is None and source_fn is None and hooks is None:
        workout_key, args.gravel = choose_workout(coach, gravel=args.gravel, detected=road.surface)
    runner = None
    if workout_key:
        runner = WorkoutRunner(build_workout(workout_key, ftp or 150, coach.long_ride_target_min()), ftp)
    records = coach.records()
    ghost = None
    if args.ghost and source_fn is None:
        try:
            ghost = mo.pick_ghost(coach.rides, workout_key, Path(args.log_dir), args.rider_kg)
        except Exception:
            log.exception("could not load the ghost ride")
    finish_at = None if workout_key else 60 * mo.ride_minutes(coach.plan(), coach.weekly_minutes, coach.weekly_rides)
    companion = Companion({d: w for d, (w, _) in records["curve"].items()}, coach.lifetime()["miles"],
                          0 if workout_key == "long" else args.comfort_break, drink=bool(args.drink),
                          ghost=ghost, finish_at_s=finish_at)
    control = TrainerControl(enabled=bool(args.resistance) and not args.dry_run,
                             on_event=lambda mode, label: on_trainer_event(mode, label))
    texture = Texture(GRAVEL_CRR, args.rumble) if args.rumble > 0 else None

    def on_gravel() -> bool:
        return args.gravel if args.gravel is not None else road.surface == "gravel"
    attention = Attention(args.overlay_fade, args.overlay_dim)

    def overlay_snapshot() -> dict:
        now = time.monotonic()
        wk = state.get("workout_status")
        if wk is not None:
            wk = {**wk, "erg": control.applied is not None and control.applied.mode == "erg"}
        return attention.apply(stats.snapshot(now, ftp, ftp_estimated, args.units, state["paused"],
                                              wk, current_message()), now)

    overlay = Overlay(overlay_snapshot,
                      enabled=bool(args.overlay) and source_fn is None and sys.platform == "win32")
    state = {"conn": "starting", "power": None, "cadence": None,
             "out": DriveOutput(0.0, 0.0, 0.0, 0.0), "limit": None,
             "paused": False, "pedalled_at": None, "had_data": False, "focused": None, "launched": False,
             "paused_total": 0.0, "paused_at": None, "message": None, "message_until": 0.0, "greeted": False}

    def current_message():
        return state["message"] if time.monotonic() < state["message_until"] else None

    def say(cue: str, message: str, seconds: float = 8.0) -> None:
        """Coach message on the overlay (and console) with an optional beep."""
        if cue:
            cues.play(cue)
        state["message"], state["message_until"] = message, time.monotonic() + seconds
        log.info("coach: %s", message)
        if hooks is not None:
            hooks.message(message)
        print("\n" + message.replace("·", "-"), flush=True)  # the console code page lacks the dot

    def on_trainer_event(mode: str, label: str) -> None:
        """Say when ERG takes over or lets go (the road feel itself is quiet)."""
        previous = state.get("trainer_mode")
        state["trainer_mode"] = mode
        if mode == "erg" and previous != "erg":
            say("", f"ERG ON · TRAINER HOLDS {label.split()[1]} W", 5)
        elif mode == "sim" and previous == "erg":
            say("", "ERG OFF · ROAD FEEL", 5)

    def ride_time(now: float) -> float:
        """Seconds of riding since the first pedal stroke, paused time left out."""
        if stats.started_at is None:
            return 0.0
        paused = state["paused_total"] + (now - state["paused_at"] if state["paused_at"] is not None else 0.0)
        return max(0.0, now - stats.started_at - paused)
    log.info("start dry_run=%s mode=%s speed_source=%s units=%s deadzone=%.2f drive=%s limit=%s mapper=%s sim=%s "
             "workout=%s ftp=%.0f(%s) resistance=%s erg=%s road_feel=%s gravel=%s rumble=%.2f",
             args.dry_run, args.mode, args.speed_source, args.units, args.deadzone,
             controller.config, planner.config, mapper.config, args.sim, workout_key, ftp, ftp_source,
             control.enabled, args.erg, args.road_feel, "auto:" + str(road.surface) if args.gravel is None
             else args.gravel, args.rumble)
    c = controller.config
    how = ("game holds speed at the limit: speed control ON, limit mode, Slow Roads focused"
           if args.mode == "limit" else f"top {c.top_speed_kmh:.0f} km/h, max throttle {c.max_throttle}")
    print(f"ride CSV:  {ride.path}\nevent log: {event_path}\n"
          f"mode:      {args.mode} ({how})\n"
          f"settings:  gear {c.gear_ratio}, speed from {args.speed_source}"
          f"  ({'settings.json' if settings.load() else 'built-in defaults'})\n"
          + (f"keys:      {HOTKEY_HELP}  (with the game in front)\n" if hotkeys.enabled else "")
          + (f"overlay:   top right of the game; FTP {ftp:.0f} W{' (estimated from past rides; set --ftp)' if ftp_estimated else ''}\n"
             if overlay.enabled and ftp else "overlay:   top right of the game; set --ftp for %FTP and zones\n"
             if overlay.enabled else "")
          + (f"auto-end:  after {args.idle_end:g} min without pedalling\n" if args.idle_end > 0 else "")
          + (coach.brief_text() + "\n" if coach.rides else "")
          + (f"ghost:     your ride of {ghost.label}, every 10 min\n" if ghost else "")
          + (f"workout:   {runner.w.title}: {runner.w.blurb}\n" if runner else "")
          + ((f"trainer:   " + ", ".join(
              ([("ERG holds workout targets" if runner else "ERG in workouts")] if args.erg else [])
              + ([f"road feel {_surface_text(args.gravel, road.surface)}"
                  + (" (gravel rumble on)" if args.rumble > 0 else "")] if args.road_feel else []))
              + "  (F8 pause = easy)\n") if control.enabled and (args.erg or args.road_feel) else
             "trainer:   resistance not controlled\n")
          + "Ctrl+C to stop\n", flush=True)

    def launch_game() -> None:
        state["launched"] = True
        if not args.launch_game or args.dry_run or source_fn is not None or sys.platform != "win32":
            return
        try:
            if actuator.win.find_game_window() is None:
                os.startfile(f"steam://rungameid/{STEAM_APP_ID}")
                log.info("started Slow Roads through Steam")
                print("\nstarting Slow Roads through Steam...", flush=True)
        except Exception:
            log.exception("could not start Slow Roads")

    def on_state(s: str) -> None:
        state["conn"] = s
        log.info("state -> %s", s)
        if s == "connected" and not state["launched"]:
            launch_game()

    def on_hotkey(action: str, now: float) -> None:
        if action in ("gear_down", "gear_up"):
            c = controller.config
            g = c.gear_ratio + (GEAR_STEP if action == "gear_up" else -GEAR_STEP)
            c.gear_ratio = args.gear = round(min(GEAR_MAX, max(GEAR_MIN, g)), 2)
            try:
                settings.save_pref("gear", c.gear_ratio)
                saved = "saved"
            except Exception:
                log.exception("could not save gear")
                saved = "not saved"
            log.info("hotkey: gear %.2f", c.gear_ratio)
            print(f"\ngear {c.gear_ratio:.2f} ({saved})", flush=True)
            cues.play("gear")
        elif action == "pause":
            state["paused"] = not state["paused"]
            if state["paused"]:
                state["paused_at"] = now
            elif state["paused_at"] is not None:
                state["paused_total"] += now - state["paused_at"]
                state["paused_at"] = None
            log.info("hotkey: %s", "paused" if state["paused"] else "resumed")
            print("\n" + ("PAUSED: throttle off, speed limit left alone. F8 to resume" if state["paused"]
                          else "resumed"), flush=True)
            cues.play("paused" if state["paused"] else "resumed")
            state["pedalled_at"] = now if state["pedalled_at"] is not None else None
        elif action == "overlay":
            overlay.toggle()
            log.info("hotkey: overlay %s", "on" if overlay.visible else "off")
        elif action == "resync" and args.mode == "limit":
            state["resync_request"] = True  # done in the output loop: floor and straight back, ~1 s
            log.info("hotkey: re-sync limit")
            print("\nre-syncing the speed limit (car dips for a moment)", flush=True)

    def on_packet(raw: bytes) -> None:
        now = time.monotonic()
        try:
            bike = parse_indoor_bike_data(raw)
        except MalformedPacket as e:
            log.warning("malformed packet %s: %s", raw.hex(" "), e)
            ride.write(now, raw, error=str(e))
            return
        logged, bike = bike, plausible(bike)  # the ride CSV keeps what the trainer sent
        if bike is not logged and not state.get("warned_implausible"):
            state["warned_implausible"] = True
            log.warning("ignoring impossible readings (e.g. %s); further ones are not logged", raw.hex(" "))
        if bike.power_w is not None:
            mapper.add_sample(bike.power_w, now)
        # Power for the virtual bike. The KICKR sometimes reports 0 W mid-stroke with cadence still
        # high (ride 11:39 t=135 s: 0 W at 82 rpm); reuse the last real reading then.
        if bike.power_w is not None and (bike.power_w > 0 or (bike.cadence_rpm or 0) < 20):
            state["drive_w"] = bike.power_w
        # speed source "virtual" is stepped at 20 Hz in output_loop
        if args.speed_source == "power":
            state["bike_raw"] = bike_speed_from_power(mapper.smoothed, mass_kg=args.rider_kg)
        elif args.speed_source == "trainer" and bike.speed_kmh is not None:
            state["bike_raw"] = bike.speed_kmh
        state["power"], state["cadence"] = bike.power_w, bike.cadence_rpm
        if ((bike.power_w or 0) >= planner.config.coast_watts
                or (bike.cadence_rpm or 0) >= planner.config.coast_cadence):
            state["pedalled_at"] = now
            stats.start(now)
        stats.add(now, bike.power_w, bike.cadence_rpm)
        # The KICKR keeps reporting cadence for a couple of seconds after pedalling stops (ride 12:48:
        # 0 W at 63 rpm), which delayed coast detection. Two low-power packets in a row = not pedalling;
        # a single 0 W packet mid-stroke is still ignored.
        low = (bike.power_w or 0) < planner.config.coast_watts
        state["low_n"] = state.get("low_n", 0) + 1 if low else 0
        out = state["out"]
        ride.write(now, raw, logged, mapper.smoothed, out.throttle, drive=out)

    async def output_loop() -> None:
        last = time.monotonic()
        while True:
            now = time.monotonic()
            active = not mapper.is_stale(now)
            focused = actuator.game_focused()
            if not focused:
                state["focused_since"] = None
            elif state.get("focused_since") is None:
                state["focused_since"] = now
            for action in hotkeys.poll(focused):
                on_hotkey(action, now)
            if active != state["had_data"]:
                cues.play("connected" if active else "trainer_lost")
                state["had_data"] = active
            if control.enabled:  # resistance: ERG for workout targets, road feel otherwise (bridge/trainer.py)
                cad = state.get("cadence")
                if cad is None or cad < 45:
                    state["low_cad_s"] = state.get("low_cad_s", 0.0) + (now - last)
                elif cad >= 55:
                    state["low_cad_s"] = 0.0
                control.set_want(decide_resistance(
                    True, state.get("workout_status"), state["paused"],
                    active and state.get("low_n", 0) < 2, cad, state.get("low_cad_s", 0.0),
                    on_gravel(), bool(args.road_feel), bool(args.erg), texture, now))
            if state["paused"]:
                out = DriveOutput(0.0, 0.0, 0.0, state["out"].car_est_kmh)
                state["out"], state["plan"] = out, "paused"
                pad.set_controls(0.0, 0.0)
                drive_log.write(now, active, controller.bike_kmh, out, 0.0, "paused")
                last = now
                await asyncio.sleep(1 / OUTPUT_HZ)
                continue
            if args.speed_source == "virtual":
                state["bike_raw"] = vbike.step(state.get("drive_w", 0) if active else 0.0, now - last)
            push = planner.push_factor(mapper.smoothed) if (args.mode == "limit" and active) else 1.0
            state["push"] = push
            controller.set_bike_speed(state.get("bike_raw", 0.0) * push)
            if active:
                stats.add_distance(state.get("bike_raw", 0.0) * (now - last) / 3600)
            if args.mode == "limit":
                target = controller.step(now - last, active).target_kmh
                cad = state["cadence"] if state.get("low_n", 0) < 2 else 0
                pedalling = planner.is_pedalling(state["power"], cad)
                desired, throttle = planner.update(target, active, pedalling, now, pushing=push > 1.05)
                if planner.state == "riding":
                    throttle = planner.push_throttle(throttle, push)
                state["plan"] = f"push x{push:.2f}" if push > 1.05 and planner.state == "riding" else planner.state
                if planner.state != "riding":
                    state["riding_since"] = None
                elif state.get("riding_since") is None:
                    state["riding_since"] = now
                if state.pop("resync_request", False) or actuator.needs_confirming(
                        now, state.get("focused_since"), state.get("riding_since")):
                    actuator.resync(desired, now)  # one ~1 s burst; also confirms the count after start-up
                else:
                    actuator.step_toward(desired, now, planner.config.max_notches_per_s)
                state["limit"] = actuator.current
                limit_kmh = 0.0 if actuator.current is None else display_to_kmh(actuator.current, args.units)
                # In limit mode car_est_kmh logs the limit the game is holding the car to.
                out = DriveOutput(throttle, 0.0, target, limit_kmh)
            elif args.mode == "speed":
                out = controller.step(now - last, active)
            else:
                out = DriveOutput(mapper.throttle(now), 0.0, 0.0, 0.0)
            state["out"] = out
            # Logged throttle is the controller's 0..1; the trigger is lifted past the dead zone.
            trigger = trigger_value(out.throttle, args.deadzone)
            pad.set_controls(trigger, out.brake)
            drive_log.write(now, active, controller.bike_kmh, out, trigger, state.get("plan", ""))
            last = now
            await asyncio.sleep(1 / OUTPUT_HZ)

    async def status_loop() -> None:
        while True:
            now = time.monotonic()
            conn = state["conn"]
            if conn == "connected" and mapper.is_stale(now):
                conn = "no data"
            focused = actuator.game_focused()
            if state["focused"] is not None and focused != state["focused"]:
                cues.play("focus_back" if focused else "focus_lost")
            state["focused"] = focused
            if state["paused"]:
                conn = "PAUSED"
            elif args.mode == "limit" and not focused:
                conn = "NO GAME FOCUS"
            elif args.mode == "limit" and (state.get("plan") in ("coasting", "releasing")
                                           or str(state.get("plan", "")).startswith("push")):
                conn = state["plan"]
            line = format_status(now - ride.start, conn, state["power"], state["cadence"],
                                 state["out"], ride.packet_rate(now), ride.bad_packets,
                                 limit=None if args.mode != "limit" else (state["limit"], args.units))
            print("\r" + line, end="", flush=True)
            if args.gravel is None and source_fn is None:
                changed = road.poll(now)
                if changed:  # the road picked at start-up came from road.poll(0.0), so this is a real change
                    say("", f"ROAD: {changed.upper()}" + (" · GRAVEL FEEL" if changed == "gravel" else ""), 5)
                    log.info("road surface from the game: %s (%s)", changed, road.world)
            if stats.started_at is not None and not state["paused"]:
                coach_tick(now)
            if hooks is not None:
                hooks.status({"conn": conn, "connected": state["conn"] == "connected", "focused": focused,
                              "paused": state["paused"], "launched": state["launched"],
                              "power": state["power"], "cadence": state["cadence"], "ride_s": ride_time(now),
                              "miles": stats.distance_km / KM_PER_MILE, "limit": state["limit"],
                              "units": args.units, "workout": state.get("workout_status"),
                              "workout_title": runner.w.title if runner else None})
            await asyncio.sleep(1.0)

    def coach_tick(now: float) -> None:
        """Once a second while riding: workout progress, stand-up breaks, live bests, milestones."""
        t_ride = ride_time(now)
        if not state["greeted"]:
            state["greeted"] = True
            w = coach.week(0)
            n, goal = w["rides"] + 1, coach.weekly_rides
            if coach.comeback():  # coming back after a break is what builds the habit: say so warmly
                say("", "WELCOME BACK · ANY RIDE KEEPS THE STREAK GOING", 10)
            else:
                progress = f"RIDE {n} OF {goal} THIS WEEK" if n <= goal else f"RIDE {n} THIS WEEK · GOAL MET"
                say("", f"{progress} · STREAK {coach.streak_weeks()} WK", 10)
        events = []
        if runner is not None:
            status, ev = runner.update(t_ride, stats.avg(3.5, now)[0], stats.cadence)
            state["workout_status"] = status
            events += ev
        in_break = bool(state.get("workout_status") and state["workout_status"]["target"] is None)
        events += companion.update(t_ride, stats.rolling(now), stats.distance_km / KM_PER_MILE, in_break)
        for cue, message in events:
            say(cue, message, 12 if cue == "break" else 8)

    if source_fn is not None:
        source = source_fn(on_packet, on_state)
    elif args.sim:
        from .sim import parse_profile, run_sim

        source = run_sim(parse_profile(args.sim), on_packet, on_state)
    else:
        if args.trainer and args.trainer.lower() != "pair":
            if not settings.ADDRESS_RE.match(args.trainer):
                raise SystemExit(f"--trainer {args.trainer!r} is not a Bluetooth address (AA:BB:CC:DD:EE:FF)")
            address = args.trainer.upper()
        elif args.trainer:
            address = None
        else:
            address = settings.load_trainer()
        if address:
            log.info("using trainer %s (pinned; --trainer pair to change)", address)
        else:
            print("pairing: no trainer saved yet; the first FTMS trainer found will be saved\n", flush=True)

        def on_paired(addr: str, name: str) -> None:
            if args.dry_run:
                return
            try:
                settings.save_trainer(addr, name)
                log.info("paired with %s [%s]; saved to settings.json", name, addr)
            except Exception:
                log.exception("could not save trainer address")

        source = run_reader(on_packet, on_state, args.name, address=address, on_connected=on_paired,
                            control=control if control.enabled else None)
    overlay.start()

    async def idle_watch() -> None:
        """Ends the ride (returns) after --idle-end minutes without pedalling, once riding has started."""
        if args.idle_end <= 0:
            await asyncio.Event().wait()
        while True:
            await asyncio.sleep(1.0)
            t = state["pedalled_at"]
            if t is not None and not state["paused"] and time.monotonic() - t > args.idle_end * 60:
                msg = f"no pedalling for {args.idle_end:g} min: ending the ride"
                log.info(msg)
                print("\n" + msg, flush=True)
                return

    async def stop_watch() -> None:
        """Ends the ride when the window asks (its End ride button)."""
        while not hooks.stop.is_set():
            await asyncio.sleep(0.2)
        log.info("ride ended from the window")

    tasks = [asyncio.ensure_future(source), asyncio.ensure_future(output_loop()),
             asyncio.ensure_future(status_loop()), asyncio.ensure_future(idle_watch())]
    if hooks is not None:
        tasks.append(asyncio.ensure_future(stop_watch()))
    if control.enabled:
        tasks.append(asyncio.ensure_future(control.run()))
    try:
        # The ride ends when the data source finishes (simulated/fuzzed rides) or on Ctrl+C. An error in
        # any loop ends it too and is re-raised, so crash reporting sees it instead of it being swallowed.
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            t.result()
    finally:
        try:  # hand the trainer back while still connected (before the reader task is cancelled)
            await asyncio.wait_for(control.release(), 3.0)
        except BaseException as e:  # incl. a second Ctrl+C: never block shutdown on this
            log.warning("trainer release skipped: %r", e)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        pad.close()
        ride.close()
        drive_log.close()
        try:
            marker.unlink()
        except OSError:
            pass
        log.info("stop packets=%d bad=%d", ride.packets, ride.bad_packets)
        print(f"\n{ride.packets} packets ({ride.bad_packets} bad) -> {ride.path}")
        ftp_set = args.ftp
        if runner is not None and runner.w.ramp:  # ramp test: FTP = 75% of the best minute
            if runner.result_ftp is None and runner.best_minute and runner._idx >= 6:
                runner._ramp_finish()
            if runner.result_ftp:
                ftp_set = runner.result_ftp
                try:
                    settings.save_pref("ftp", ftp_set)
                    print(f"\nRamp test: FTP {ftp_set} W (75% of your best minute, "
                          f"{runner.best_minute:.0f} W). Saved to settings.json.", flush=True)
                except Exception:
                    log.exception("could not save FTP")
        moving_s = 0.0
        result = {"stamp": stamp, "path": ride.path, "workout": workout_key, "summary": None, "ride": None,
                  "coach": coach, "text": None, "ftp_set": ftp_set,
                  "ramp_ftp": runner.result_ftp if runner is not None and runner.w.ramp else None}
        try:  # summary, coach report and scoreboard, from the trainer's data only
            current = summarize_csv(ride.path, args.rider_kg)
            moving_s = current.moving_s
            this = analyse_ride(ride.path, args.rider_kg, current)
            board = Coach(coach.rides + ([this] if this else []), ftp_set, coach.weekly_rides, coach.weekly_minutes)
            text = format_summary(current)
            if this is not None:
                text += "\n" + board.ride_report(this)
                if mo.finished_easy(current.series or []):
                    text += "\nFinish      eased off at the end: a good way to finish (it makes the next ride easier to start)"
            text += format_advice(advice(current, None, ftp_set, workout_key))
            if this is not None:
                text += "\n\n" + board.scoreboard_text()
            saved = save_summary(ride.path, text)
            result.update(summary=current, ride=this, coach=board, text=text)
            print("\n" + text + (f"\nsaved: {saved}" if saved else ""), flush=True)
            if coach.rides and current.moving_s >= 60:
                prev = coach.rides[-1]
                print(compare_line(current, SimpleNamespace(moving_s=prev.moving_s, avg_power_w=prev.avg_w,
                                                            work_kj=prev.work_kj)), flush=True)
            if args.feel and source_fn is None and hooks is None and current.moving_s >= 300:
                ask_feel(Path(args.log_dir), stamp)
        except Exception:
            log.exception("could not summarize the ride")
        if (args.dashboard and source_fn is None and hooks is None and not args.sim and not args.dry_run and moving_s >= 60
                and sys.platform == "win32"):
            open_dashboard(Path(args.log_dir))
        try:
            removed = remove(old_entries(Path(args.log_dir), args.keep_days, active=stamp))
            if removed:
                log.info("removed %d log entries older than %g days", removed, args.keep_days)
                print(f"cleaned up {removed} old log entries (older than {args.keep_days:g} days)")
        except Exception:
            log.exception("log cleanup failed")
        overlay.stop()
        cues.play("ride_end")
        cues.close()
        close_event_log()
        if hooks is not None:
            hooks.finished(result)


def main(argv=None) -> int:
    asyncio.run(run(parse_args(argv)))
    return 0


if __name__ == "__main__":
    sys.exit(run_main(main, "bridge"))
