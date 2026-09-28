"""Measure how often the trainer notifies on every characteristic it offers.

Indoor Bike Data (FTMS 0x2AD2) arrives at 1 Hz. BLE is push, not poll: the rate is set by
the trainer, so the only way to get faster data is a characteristic the trainer sends more
often. This subscribes to every notify characteristic for --seconds and reports the rate of each.
Pedal while it runs. Output also goes to logs/probe-*.log.

Usage: python tools/probe_rates.py [--seconds 30]
"""

import argparse
import asyncio
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bleak import BleakClient  # noqa: E402

from bridge import settings  # noqa: E402
from bridge.ftms import find_trainer  # noqa: E402
from bridge.ridelog import LOG_DIR, setup_event_log, timestamp  # noqa: E402

log = logging.getLogger("probe")
KNOWN = {
    "2ad2": "FTMS Indoor Bike Data",
    "2a63": "Cycling Power Measurement",
    "2a5b": "CSC Measurement",
    "2ada": "FTMS Machine Status",
    "2ad9": "FTMS Control Point",
    "2a37": "Heart Rate Measurement",
}


async def probe(seconds: float) -> None:
    device = await find_trainer(address=settings.load_trainer())  # pinned trainer only, if paired
    if device is None:
        sys.exit("trainer not found; wake the KICKR and close other apps")
    times: dict[str, list[float]] = defaultdict(list)
    samples: dict[str, str] = {}
    async with BleakClient(device) as client:
        chars = [c for s in client.services for c in s.characteristics if "notify" in c.properties]
        for c in chars:
            def handler(_, data, uuid=c.uuid):
                times[uuid].append(time.monotonic())
                samples[uuid] = bytes(data).hex(" ").upper()
            try:
                await client.start_notify(c.uuid, handler)
                log.info("subscribed %s (%s)", c.uuid, KNOWN.get(c.uuid[4:8], c.description))
            except Exception as e:
                log.warning("could not subscribe %s: %s", c.uuid, e)
        print(f"pedal now; measuring {seconds:.0f} s...", flush=True)
        await asyncio.sleep(seconds)
    print(f"\n{'characteristic':<40} {'count':>5} {'rate Hz':>7}  last packet")
    for c in chars:
        ts = times.get(c.uuid, [])
        rate = (len(ts) - 1) / (ts[-1] - ts[0]) if len(ts) > 1 and ts[-1] > ts[0] else 0.0
        name = KNOWN.get(c.uuid[4:8], c.description or c.uuid)
        line = f"{name[:40]:<40} {len(ts):>5} {rate:7.2f}  {samples.get(c.uuid, '')}"
        print(line)
        log.info("rate %s %s count=%d hz=%.2f last=%s", c.uuid, name, len(ts), rate, samples.get(c.uuid, ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=30.0)
    args = ap.parse_args()
    path = setup_event_log(LOG_DIR, timestamp(), prefix="probe")
    print(f"log: {path}")
    asyncio.run(probe(args.seconds))


if __name__ == "__main__":
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from bridge.crashreport import run_main

    sys.exit(run_main(main, "probe_rates"))
