"""Record what the game did during a ride, for tuning afterwards. Observes only; controls nothing.

The ride bridge never reads the screen. This separate process does, so a ride can be compared
against what really happened in the game (actual speed vs the bridge's estimate, hills,
vehicle, surface). Slow Roads writes no play log, so the game side is captured as:
  - speed-<stamp>.csv       speedometer OCR ~5 Hz (speed, gear, raw OCR text, wall time)
  - shots-<stamp>/          JPEG of the game window every 5 s, only while it's in front (never the desktop)
  - game-<stamp>-start/end/ copies of the game's saved settings (Local Storage), for vehicle etc.

Waits for the bridge to start (logs/active-ride.txt), uses the same <stamp> so files line up
with ride-/drive-/bridge- logs, and stops when the bridge stops. Started by ride.bat.

Usage: python tools/ride_recorder.py [--shot-every 5] [--ocr-hz 5] [--no-shots]
"""

import argparse
import csv
import os
import re
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


STAMP_RE = re.compile(r"^\d{8}-\d{6}$")


def valid_stamp(text: str) -> str | None:
    """The ride stamp names every output path, so accept only YYYYMMDD-HHMMSS (docs/SECURITY.md
    finding 3: a crafted active-ride.txt with three or more parent-directory steps wrote outside logs/)."""
    text = (text or "").strip()
    return text if STAMP_RE.match(text) else None


def wait_for_ride() -> str | None:
    print("recorder: waiting for the bridge to start...", flush=True)
    end = time.monotonic() + WAIT_FOR_BRIDGE_S
    while time.monotonic() < end:
        if ACTIVE_RIDE.exists():
            try:
                raw = ACTIVE_RIDE.read_text(encoding="utf-8")[:64]
            except OSError:
                raw = ""
            stamp = valid_stamp(raw)
            if stamp is None:
                print(f"recorder: ignoring invalid ride stamp {raw[:40]!r}")
                return None
            return stamp
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
    from speedo import SpeedoReader, csv_safe

    from bridge import gamewin

    reader = SpeedoReader()
    shots = LOG_DIR / f"shots-{stamp}"
    if not args.no_shots:
        shots.mkdir(parents=True, exist_ok=True)
    snapshot_game_state(LOG_DIR / f"game-{stamp}-start")
    csv_path = LOG_DIR / f"speed-{stamp}.csv"
    print(f"recorder: ride {stamp} -> {csv_path}", flush=True)

    # Ride 11:39 lost the recorder 7 s in with no trace (console window closed with it). Errors now
    # go to recorder-<stamp>.log and the loop carries on, re-creating the screen reader if needed.
    import logging
    import traceback

    logging.basicConfig(filename=LOG_DIR / f"recorder-{stamp}.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    rlog = logging.getLogger("recorder")
    rlog.info("recording ride %s", stamp)
    t0 = time.monotonic()
    next_shot = t0
    reads = good = errors = 0
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "wall_time", "kmh", "shown", "unit", "gear", "ocr_text"])
        try:
            while ACTIVE_RIDE.exists():
                tick = time.monotonic()
                try:
                    r = reader.read()
                    reads += 1
                    good += r.speed is not None
                    w.writerow([f"{tick - t0:.3f}", datetime.now().isoformat(timespec="milliseconds"),
                                "" if r.kmh is None else f"{r.kmh:.2f}", "" if r.speed is None else r.speed,
                                r.unit or "", r.gear or "", csv_safe(r.text)])
                    if reads % 5 == 0:
                        f.flush()
                    if not args.no_shots and tick >= next_shot:
                        # Game window only, and only while it's in front: never other apps or the
                        # desktop (docs/SECURITY.md finding 4).
                        game = gamewin.find_game_window()
                        rect = gamewin.window_rect(game) if game and gamewin.game_focused() else None
                        if rect and rect[2] > rect[0] and rect[3] > rect[1]:
                            box = {"left": rect[0], "top": rect[1], "width": rect[2] - rect[0],
                                   "height": rect[3] - rect[1]}
                            shot = reader._sct.grab(box)
                            img = Image.frombytes("RGB", shot.size, shot.rgb)
                            img.thumbnail((960, 540))
                            img.save(shots / f"{datetime.now():%H%M%S}.jpg", quality=70)
                        next_shot = tick + args.shot_every
                except Exception:
                    errors += 1
                    rlog.error("sample failed (%d so far):\n%s", errors, traceback.format_exc())
                    time.sleep(1.0)
                    try:
                        reader = SpeedoReader()
                    except Exception:
                        rlog.error("could not re-create screen reader:\n%s", traceback.format_exc())
                time.sleep(max(0.0, 1 / args.ocr_hz - (time.monotonic() - tick)))
        except KeyboardInterrupt:
            pass
    rlog.info("done: %d reads, %d readable, %d errors", reads, good, errors)
    snapshot_game_state(LOG_DIR / f"game-{stamp}-end")
    print(f"recorder: done, {reads} speed reads ({good} readable)")
    return 0


if __name__ == "__main__":
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from bridge.crashreport import run_main

    sys.exit(run_main(main, "ride_recorder"))
