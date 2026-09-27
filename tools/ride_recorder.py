"""Record what the game did during a ride, for tuning afterwards. Observes only; controls nothing.

The ride bridge never reads the screen. This separate process does, so a ride can be compared
against what really happened in the game (actual speed vs the bridge's estimate, hills,
vehicle, surface). Slow Roads writes no play log, so the game side is captured as:
  - speed-<stamp>.csv       speedometer OCR ~5 Hz (speed, gear, raw OCR text, wall time)
  - shots-<stamp>/          full-screen JPEG every 5 s (960x540)
  - game-<stamp>-start/end/ copies of the game's saved settings (Local Storage), for vehicle etc.

Waits for the bridge to start (logs/active-ride.txt), uses the same <stamp> so files line up
with ride-/drive-/bridge- logs, and stops when the bridge stops. Started by ride.bat.

Usage: python tools/ride_recorder.py [--shot-every 5] [--ocr-hz 5] [--no-shots]
"""

import argparse
import csv
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge.ridelog import ACTIVE_RIDE, LOG_DIR  # noqa: E402

GAME_STORAGE = Path(os.environ.get("APPDATA", "")) / "slowroads" / "Local Storage"
WAIT_FOR_BRIDGE_S = 120


def snapshot_game_state(dest: Path) -> None:
    try:
        shutil.copytree(GAME_STORAGE, dest, ignore=shutil.ignore_patterns("LOCK"))
    except (OSError, shutil.Error) as e:
        print(f"game state snapshot failed: {e}")


def wait_for_ride() -> str | None:
    print("recorder: waiting for the bridge to start...", flush=True)
    end = time.monotonic() + WAIT_FOR_BRIDGE_S
    while time.monotonic() < end:
        if ACTIVE_RIDE.exists():
            return ACTIVE_RIDE.read_text(encoding="utf-8").strip()
        time.sleep(0.5)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot-every", type=float, default=5.0, help="seconds between screenshots")
    ap.add_argument("--ocr-hz", type=float, default=5.0)
    ap.add_argument("--no-shots", action="store_true")
    args = ap.parse_args()

    stamp = wait_for_ride()
    if stamp is None:
        print("recorder: bridge did not start; exiting")
        return 1
    from PIL import Image
    from speedo import SpeedoReader

    reader = SpeedoReader()
    shots = LOG_DIR / f"shots-{stamp}"
    if not args.no_shots:
        shots.mkdir(parents=True, exist_ok=True)
    snapshot_game_state(LOG_DIR / f"game-{stamp}-start")
    csv_path = LOG_DIR / f"speed-{stamp}.csv"
    print(f"recorder: ride {stamp} -> {csv_path}", flush=True)

    t0 = time.monotonic()
    next_shot = t0
    reads = good = 0
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "wall_time", "kmh", "shown", "unit", "gear", "ocr_text"])
        try:
            while ACTIVE_RIDE.exists():
                tick = time.monotonic()
                r = reader.read()
                reads += 1
                good += r.speed is not None
                w.writerow([f"{tick - t0:.3f}", datetime.now().isoformat(timespec="milliseconds"),
                            "" if r.kmh is None else f"{r.kmh:.2f}", "" if r.speed is None else r.speed,
                            r.unit or "", r.gear or "", r.text])
                if reads % 5 == 0:
                    f.flush()
                if not args.no_shots and tick >= next_shot:
                    shot = reader._sct.grab(reader._sct.monitors[1])
                    img = Image.frombytes("RGB", shot.size, shot.rgb).resize((960, 540))
                    img.save(shots / f"{datetime.now():%H%M%S}.jpg", quality=70)
                    next_shot = tick + args.shot_every
                time.sleep(max(0.0, 1 / args.ocr_hz - (time.monotonic() - tick)))
        except KeyboardInterrupt:
            pass
    snapshot_game_state(LOG_DIR / f"game-{stamp}-end")
    print(f"recorder: done, {reads} speed reads ({good} readable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
