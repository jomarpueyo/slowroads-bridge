"""Entry point: python -m bridge [--dry-run] [--gear 3.0] [--mode limit|speed|power] ..."""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from . import settings
from .drive import DriveConfig, DriveOutput, SpeedController, bike_speed_from_power, trigger_value
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
    ap.add_argument("--speed-source", choices=["trainer", "power"], default="trainer",
                    help="trainer: KICKR flywheel speed (gear x cadence); power: road-bike physics from watts")
    ap.add_argument("--rider-kg", type=float, default=85.0, help="rider + bike mass for --speed-source power")
    lg = ap.add_argument_group("limit mode")
    lg.add_argument("--units", choices=["mph", "km/h"], default=lc.units, help="the game's speed units")
    lg.add_argument("--drive-throttle", type=float, default=lc.drive_throttle,
                    help="throttle held while pedalling (the game caps speed at the limit)")
    ap.add_argument("--sim", metavar="PROFILE",
                    help='test without the bike: "seconds:bike_kmh,..." e.g. "0:0,5:15,40:15,55:0"')
    g = ap.add_argument_group("speed mode (defaults come from settings.json when present)")
    g.add_argument("--gear", type=float, default=d.gear_ratio, help="car km/h per bike km/h")
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
    ap.add_argument("--name", default="KICKR", help="device name fallback if FTMS UUID is not advertised")
    ap.add_argument("--log-dir", type=Path, default=LOG_DIR)
    ap.add_argument("--verbose", action="store_true", help="echo the event log to the console")
    return ap.parse_args(argv)


def drive_config(args: argparse.Namespace) -> DriveConfig:
    return DriveConfig(
        gear_ratio=args.gear, accel_kmh_s=args.accel, coast_kmh_s=args.coast,
        drag_quad=args.drag_quad, brake_kmh_s=args.brake_rate, top_speed_kmh=args.top_speed,
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
    planner = SpeedLimitPlanner(LimitConfig(units=args.units, drive_throttle=args.drive_throttle))
    actuator = WheelActuator(dry_run=args.dry_run)
    pad = NullPad() if args.dry_run else VirtualPad()
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
        if args.speed_source == "power":
            controller.set_bike_speed(bike_speed_from_power(mapper.smoothed, mass_kg=args.rider_kg))
        elif bike.speed_kmh is not None:
            controller.set_bike_speed(bike.speed_kmh)
        state["power"], state["cadence"] = bike.power_w, bike.cadence_rpm
        out = state["out"]
        ride.write(now, raw, bike, mapper.smoothed, out.throttle, drive=out)

    async def output_loop() -> None:
        last = time.monotonic()
        while True:
            now = time.monotonic()
            active = not mapper.is_stale(now)
            if args.mode == "limit":
                target = controller.step(now - last, active).target_kmh
                desired, throttle = planner.update(target, active)
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
            drive_log.write(now, active, controller.bike_kmh, out, trigger)
            last = now
            await asyncio.sleep(1 / OUTPUT_HZ)

    async def status_loop() -> None:
        while True:
            now = time.monotonic()
            conn = state["conn"]
            if conn == "connected" and mapper.is_stale(now):
                conn = "no data"
            if args.mode == "limit" and not actuator.game_focused():
                conn += " (game not focused)"
            line = format_status(now - ride.start, conn, state["power"], state["cadence"],
                                 state["out"], ride.packet_rate(now), ride.bad_packets,
                                 limit=None if args.mode != "limit" else (state["limit"], args.units))
            print("\r" + line, end="", flush=True)
            await asyncio.sleep(1.0)

    if args.sim:
        from .sim import parse_profile, run_sim

        source = run_sim(parse_profile(args.sim), on_packet, on_state)
    else:
        source = run_reader(on_packet, on_state, args.name)
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
