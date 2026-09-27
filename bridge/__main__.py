"""Entry point: python -m bridge [--dry-run] [--gear 3.0] [--mode limit|speed|power] ..."""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from . import settings
from .drive import DriveConfig, DriveOutput, SpeedController, VirtualBike, bike_speed_from_power, trigger_value
from .ftms import MalformedPacket, parse_indoor_bike_data, run_reader
from .limiter import LimitConfig, SpeedLimitPlanner, WheelActuator, display_to_kmh
from .mapper import MapperConfig, ThrottleMapper
from .pad import NullPad, VirtualPad
from .ridelog import ACTIVE_RIDE, LOG_DIR, DriveLog, RideLog, format_status, setup_event_log, timestamp

log = logging.getLogger("bridge")
OUTPUT_HZ = 20


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
    ap.set_defaults(**saved)
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
    ap.add_argument("--log-dir", type=Path, default=LOG_DIR)
    ap.add_argument("--verbose", action="store_true", help="echo the event log to the console")
    args = ap.parse_args(argv)
    if args.gear is None:
        args.gear = d.gear_ratio if args.speed_source == "trainer" else 2.0
    return args


def drive_config(args: argparse.Namespace) -> DriveConfig:
    # In limit mode the game owns top speed. Ride 12:23 hit an 88.8 km/h target ceiling (0.95 x the
    # speed-mode calibration's 93.5) during an 865 W sprint, capping the limit at 55 mph.
    top = 0.0 if args.mode == "limit" else args.top_speed
    return DriveConfig(
        gear_ratio=args.gear, accel_kmh_s=args.accel, coast_kmh_s=args.coast,
        drag_quad=args.drag_quad, brake_kmh_s=args.brake_rate, top_speed_kmh=top,
        max_throttle=args.max_throttle, max_brake=args.max_brake, throttle_ramp_up=args.ramp,
    )


async def run(args: argparse.Namespace) -> None:
    stamp = timestamp()
    event_path = setup_event_log(args.log_dir, stamp, args.verbose)
    ride = RideLog(args.log_dir, stamp)
    drive_log = DriveLog(args.log_dir, stamp, ride.start)
    try:
        ACTIVE_RIDE.write_text(stamp, encoding="utf-8")  # lets tools/ride_recorder.py follow along
    except OSError:
        log.warning("could not write %s", ACTIVE_RIDE)
    mapper = ThrottleMapper(MapperConfig(p_min=args.p_min, p_max=args.p_max, gamma=args.gamma, tau_s=args.tau))
    controller = SpeedController(drive_config(args))
    planner = SpeedLimitPlanner(LimitConfig(
        units=args.units, drive_throttle=args.drive_throttle, coast_throttle=args.coast_throttle,
        coast_hold_s=args.coast_hold, coast_margin=args.coast_margin, push_boost=args.push_boost,
        push_easy_w=args.push_easy_w, push_hard_w=args.push_hard_w))
    actuator = WheelActuator(dry_run=args.dry_run)
    pad = NullPad() if args.dry_run else VirtualPad()
    vbike = VirtualBike(mass_kg=args.rider_kg)
    state = {"conn": "starting", "power": None, "cadence": None,
             "out": DriveOutput(0.0, 0.0, 0.0, 0.0), "limit": None}
    log.info("start dry_run=%s mode=%s speed_source=%s units=%s deadzone=%.2f drive=%s limit=%s mapper=%s sim=%s",
             args.dry_run, args.mode, args.speed_source, args.units, args.deadzone, controller.config,
             planner.config, mapper.config, args.sim)
    c = controller.config
    how = ("game holds speed at the limit: speed control ON, limit mode, Slow Roads focused"
           if args.mode == "limit" else f"top {c.top_speed_kmh:.0f} km/h, max throttle {c.max_throttle}")
    print(f"ride CSV:  {ride.path}\nevent log: {event_path}\n"
          f"mode:      {args.mode} ({how})\n"
          f"settings:  gear {c.gear_ratio}, speed from {args.speed_source}"
          f"  ({'settings.json' if settings.load() else 'built-in defaults'})\n"
          f"Ctrl+C to stop\n", flush=True)

    def on_state(s: str) -> None:
        state["conn"] = s
        log.info("state -> %s", s)

    def on_packet(raw: bytes) -> None:
        now = time.monotonic()
        try:
            bike = parse_indoor_bike_data(raw)
        except MalformedPacket as e:
            log.warning("malformed packet %s: %s", raw.hex(" "), e)
            ride.write(now, raw, error=str(e))
            return
        if bike.power_w is not None:
            mapper.add_sample(bike.power_w, now)
        # Power for the virtual bike. The KICKR sometimes reports 0 W mid-stroke with cadence still
        # high (ride 11:39 t=135 s: 0 W at 82 rpm); reuse the last real reading then.
        if bike.power_w is not None:
            if bike.power_w > 0 or (bike.cadence_rpm or 0) < 20:
                state["drive_w"] = bike.power_w
        if args.speed_source == "virtual":
            pass  # stepped at 20 Hz in output_loop
        elif args.speed_source == "power":
            state["bike_raw"] = bike_speed_from_power(mapper.smoothed, mass_kg=args.rider_kg)
        elif bike.speed_kmh is not None:
            state["bike_raw"] = bike.speed_kmh
        state["power"], state["cadence"] = bike.power_w, bike.cadence_rpm
        # The KICKR keeps reporting cadence for a couple of seconds after pedalling stops (ride 12:48:
        # 0 W at 63 rpm), which delayed coast detection. Two low-power packets in a row = not pedalling;
        # a single 0 W packet mid-stroke is still ignored.
        low = (bike.power_w or 0) < planner.config.coast_watts
        state["low_n"] = state.get("low_n", 0) + 1 if low else 0
        out = state["out"]
        ride.write(now, raw, bike, mapper.smoothed, out.throttle, drive=out)

    async def output_loop() -> None:
        last = time.monotonic()
        while True:
            now = time.monotonic()
            active = not mapper.is_stale(now)
            if args.speed_source == "virtual":
                state["bike_raw"] = vbike.step(state.get("drive_w", 0) if active else 0.0, now - last)
            push = planner.push_factor(mapper.smoothed) if (args.mode == "limit" and active) else 1.0
            state["push"] = push
            controller.set_bike_speed(state.get("bike_raw", 0.0) * push)
            if args.mode == "limit":
                target = controller.step(now - last, active).target_kmh
                cad = state["cadence"] if state.get("low_n", 0) < 2 else 0
                pedalling = planner.is_pedalling(state["power"], cad)
                desired, throttle = planner.update(target, active, pedalling, now, pushing=push > 1.05)
                if planner.state == "riding":
                    throttle = planner.push_throttle(throttle, push)
                state["plan"] = f"push x{push:.2f}" if push > 1.05 and planner.state == "riding" else planner.state
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
            if args.mode == "limit" and not actuator.game_focused():
                conn = "NO GAME FOCUS"
            elif args.mode == "limit" and (state.get("plan") in ("coasting", "releasing")
                                           or str(state.get("plan", "")).startswith("push")):
                conn = state["plan"]
            line = format_status(now - ride.start, conn, state["power"], state["cadence"],
                                 state["out"], ride.packet_rate(now), ride.bad_packets,
                                 limit=None if args.mode != "limit" else (state["limit"], args.units))
            print("\r" + line, end="", flush=True)
            await asyncio.sleep(1.0)

    if args.sim:
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

        source = run_reader(on_packet, on_state, args.name, address=address, on_connected=on_paired)
    try:
        await asyncio.gather(source, output_loop(), status_loop())
    except asyncio.CancelledError:
        if not args.sim:
            raise
    finally:
        pad.close()
        pad.close()
        ride.close()
        drive_log.close()
        try:
            ACTIVE_RIDE.unlink()
        except OSError:
            pass
        log.info("stop packets=%d bad=%d", ride.packets, ride.bad_packets)
        print(f"\n{ride.packets} packets ({ride.bad_packets} bad) -> {ride.path}")


def main(argv=None) -> int:
    try:
        asyncio.run(run(parse_args(argv)))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
