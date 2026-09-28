"""Verify a ride CSV: packet rate, flags seen, power/cadence stats, and a re-parse of every raw packet.

Usage: python tools/summarize_ride.py [logs/ride-....csv]   (defaults to the newest ride)
Exit code 1 if any raw packet re-parses to a different power than was logged.
"""

import csv
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge.ftms import MalformedPacket, parse_indoor_bike_data  # noqa: E402
from bridge.ridelog import LOG_DIR  # noqa: E402


def newest_ride(log_dir: Path) -> Path:
    rides = sorted(log_dir.glob("ride-*.csv"))
    if not rides:
        sys.exit(f"no ride CSVs in {log_dir}")
    return rides[-1]


def summarize(path: Path) -> int:
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    print(f"file:          {path}")
    print(f"packets:       {len(rows)}")
    if not rows:
        return 0
    times = [float(r["t_s"]) for r in rows]
    span = times[-1] - times[0]
    print(f"duration:      {span:.1f} s")
    if span > 0:
        print(f"packet rate:   {(len(rows) - 1) / span:.2f} pkt/s")
        gaps = [b - a for a, b in zip(times, times[1:])]
        print(f"max gap:       {max(gaps):.2f} s")
    errors = [r for r in rows if r["error"]]
    print(f"bad packets:   {len(errors)}")
    for flags, n in Counter(r["flags"] for r in rows if r["flags"]).most_common():
        print(f"flags {flags}:  {n}")
    for field, unit in (("power_w", "W"), ("cadence_rpm", "rpm"), ("speed_kmh", "km/h"), ("throttle", "")):
        vals = [float(r[field]) for r in rows if r[field]]
        if vals:
            print(f"{field:<14} min {min(vals):.1f}  mean {statistics.mean(vals):.1f}  max {max(vals):.1f} {unit}")

    mismatches = 0
    for i, r in enumerate(rows):
        try:
            p = parse_indoor_bike_data(bytes.fromhex(r["raw_hex"])).power_w
        except MalformedPacket:
            continue
        logged = int(r["power_w"]) if r["power_w"] else None
        if p != logged:
            mismatches += 1
            if mismatches <= 5:
                print(f"MISMATCH row {i}: raw {r['raw_hex']} parses {p}, logged {logged}")
    print(f"re-parse:      {'OK' if not mismatches else f'{mismatches} mismatches'}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from bridge.crashreport import run_main

    sys.exit(run_main(lambda: summarize(Path(sys.argv[1]) if len(sys.argv) > 1 else newest_ride(LOG_DIR)), "summarize_ride"))
