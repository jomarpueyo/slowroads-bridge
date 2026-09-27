"""Replay a ride CSV through the speed controller at 20 Hz and compare with what was sent.

Usage: python tools/replay_ride.py [ride.csv] [--gear 3.0] [--accel 6] [--coast 1] [--brake-rate 12]
No hardware needed. Shows, per 10 s, the logged throttle next to the new throttle/brake and the
model's target and estimated car speed.
"""

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge import settings  # noqa: E402
from bridge.drive import DriveConfig, SpeedController  # noqa: E402
from bridge.ridelog import LOG_DIR  # noqa: E402

HZ = 20
STALE_S = 3.0


def main() -> int:
    d = DriveConfig()
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?", type=Path)
    ap.add_argument("--gear", type=float, default=d.gear_ratio)
    ap.add_argument("--accel", type=float, default=d.accel_kmh_s)
    ap.add_argument("--coast", type=float, default=d.coast_kmh_s)
    ap.add_argument("--brake-rate", type=float, default=d.brake_kmh_s)
    ap.add_argument("--max-throttle", type=float, default=d.max_throttle)
    ap.add_argument("--ramp", type=float, default=d.throttle_ramp_up)
    ap.add_argument("--drag-quad", type=float, default=d.drag_quad)
    ap.add_argument("--top-speed", type=float, default=d.top_speed_kmh)
    ap.add_argument("--bucket", type=float, default=10.0)
    ap.set_defaults(**{k: v for k, v in settings.load().items()
                       if k in ("gear", "accel", "coast", "brake_rate", "drag_quad", "top_speed", "max_throttle", "ramp")})
    args = ap.parse_args()
    path = args.csv or sorted(LOG_DIR.glob("ride-*.csv"))[-1]
    rows = [r for r in csv.DictReader(open(path, encoding="utf-8")) if not r["error"]]
    if not rows:
        sys.exit("no packets")
    cfg = DriveConfig(gear_ratio=args.gear, accel_kmh_s=args.accel, coast_kmh_s=args.coast,
                      brake_kmh_s=args.brake_rate, max_throttle=args.max_throttle,
                      throttle_ramp_up=args.ramp, drag_quad=args.drag_quad,
                      top_speed_kmh=args.top_speed)
    ctl = SpeedController(cfg)
    print(f"file: {path}\nconfig: {cfg}\n")

    t0 = float(rows[0]["t_s"])
    end = float(rows[-1]["t_s"]) + 1.0
    buckets: dict[int, dict] = {}
    i, last_packet, t = 0, None, t0
    while t < end:
        while i < len(rows) and float(rows[i]["t_s"]) <= t:
            r = rows[i]
            if r["speed_kmh"]:
                ctl.set_bike_speed(float(r["speed_kmh"]))
            last_packet = float(r["t_s"])
            b = buckets.setdefault(int((last_packet - t0) // args.bucket), _bucket())
            b["power"].append(float(r["power_w"] or 0))
            b["bike"].append(float(r["speed_kmh"] or 0))
            b["old_thr"].append(float(r["throttle"] or 0))
            i += 1
        active = last_packet is not None and t - last_packet <= STALE_S
        out = ctl.step(1 / HZ, active)
        b = buckets.setdefault(int((t - t0) // args.bucket), _bucket())
        b["thr"].append(out.throttle)
        b["brk"].append(out.brake)
        b["target"].append(out.target_kmh)
        b["car"].append(out.car_est_kmh)
        t += 1 / HZ

    print(f"{'t':>5} {'W avg':>6} {'bike':>5} | {'old thr':>7} | {'thr avg':>7} {'thr max':>7} "
          f"{'brake s':>7} | {'target':>6} {'car~':>6}")
    all_thr, all_old, brake_ticks = [], [], 0
    for k in sorted(buckets):
        b = buckets[k]
        brake_ticks += sum(1 for x in b["brk"] if x > 0)
        all_thr += b["thr"]
        all_old += b["old_thr"]
        print(f"{int(k * args.bucket):>4}s {_avg(b['power']):6.0f} {_avg(b['bike']):5.1f} | "
              f"{_avg(b['old_thr']):7.2f} | {_avg(b['thr']):7.2f} {max(b['thr'], default=0):7.2f} "
              f"{sum(1 for x in b['brk'] if x > 0) / HZ:7.1f} | {_avg(b['target']):6.1f} {_avg(b['car']):6.1f}")
    print(f"\nold throttle mean {_avg(all_old):.2f}  |  new throttle mean {_avg(all_thr):.2f}, "
          f"max {max(all_thr):.2f}, braking {brake_ticks / HZ:.1f} s")
    return 0


def _bucket() -> dict:
    return {k: [] for k in ("power", "bike", "old_thr", "thr", "brk", "target", "car")}


def _avg(xs) -> float:
    return sum(xs) / len(xs) if xs else 0.0


if __name__ == "__main__":
    sys.exit(main())
