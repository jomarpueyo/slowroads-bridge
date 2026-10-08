"""Milestone 1: sweep the virtual pad's right trigger 0 -> 1 over 10 s, then back to 0.

Start this, then switch to the Slow Roads demo: the car should speed up smoothly.
Every step is logged to logs/sweep-*.log so the result can be checked afterwards.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge.pad import VirtualPad
from bridge.ridelog import LOG_DIR, setup_event_log, timestamp

log = logging.getLogger("sweep")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--delay", type=float, default=5.0, help="countdown to switch to the game")
    ap.add_argument("--hold", type=float, default=5.0, help="hold full throttle before release")
    args = ap.parse_args()

    path = setup_event_log(LOG_DIR, timestamp(), verbose=True, prefix="sweep")
    print(f"log: {path}", flush=True)
    pad = VirtualPad()
    try:
        for i in range(int(args.delay), 0, -1):
            print(f"switch to Slow Roads... {i}", flush=True)
            time.sleep(1)
        steps = int(args.seconds * 20)
        for n in range(steps + 1):
            value = n / steps
            pad.set_controls(value)
            if n % 20 == 0:
                log.info("trigger %.2f", value)
            time.sleep(0.05)
        log.info("holding full throttle %.0f s", args.hold)
        time.sleep(args.hold)
    finally:
        pad.close()
        log.info("released; sweep done")


if __name__ == "__main__":
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from bridge.crashreport import run_main

    sys.exit(run_main(main, "trigger_sweep"))
