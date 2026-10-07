"""Live logging: per-packet ride CSV, event log file, and a 1 Hz console status line.

The CSV is flushed per row so a crash or Ctrl+C still leaves the whole ride on disk.
"""

import csv
import logging
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

CSV_FIELDS = [
    "t_s", "wall_time", "power_w", "cadence_rpm", "speed_kmh",
    "smoothed_w", "throttle", "brake", "target_kmh", "car_est_kmh", "flags", "raw_hex", "error",
]


def setup_event_log(log_dir: Path, stamp: str, verbose: bool = False, prefix: str = "bridge") -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{prefix}-{stamp}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers[:] = [file_handler] + ([console] if sys.stderr else [])  # no console under pythonw
    # bleak logs every advertisement it hears at DEBUG, which buried the bridge's own events.
    logging.getLogger("bleak").setLevel(logging.INFO)
    logging.getLogger("asyncio").setLevel(logging.INFO)
    return path


def close_event_log() -> None:
    """Close the handlers setup_event_log() opened (lets repeated runs in one process, e.g. tests and
    fuzzing, release their log files)."""
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


ACTIVE_RIDE = LOG_DIR / "active-ride.txt"  # tools/ride_recorder.py follows this file


class DriveLog:
    """Every controller tick (20 Hz): what the bridge believed and what it sent."""

    FIELDS = ["t_s", "wall_time", "active", "bike_kmh", "target_kmh", "car_est_kmh",
              "throttle", "trigger", "brake", "plan"]

    def __init__(self, log_dir: Path, stamp: str, start: float) -> None:
        self.path = log_dir / f"drive-{stamp}.csv"
        self._file = open(self.path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(self.FIELDS)
        self.start = start
        self._rows = 0

    def write(self, now, active, bike_kmh, out, trigger, plan: str = "") -> None:
        self._writer.writerow([
            f"{now - self.start:.3f}", datetime.now().isoformat(timespec="milliseconds"), int(active),
            f"{bike_kmh:.2f}", f"{out.target_kmh:.2f}", f"{out.car_est_kmh:.2f}",
            f"{out.throttle:.3f}", f"{trigger:.3f}", f"{out.brake:.3f}", plan,
        ])
        self._rows += 1
        if self._rows % 20 == 0:  # flush once a second
            self._file.flush()

    def close(self) -> None:
        self._file.close()


class RideLog:
    def __init__(self, log_dir: Path, stamp: str) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / f"ride-{stamp}.csv"
        self._file = open(self.path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=CSV_FIELDS)
        self._writer.writeheader()
        self._file.flush()
        self.start = time.monotonic()
        self.packets = 0
        self.bad_packets = 0
        self._recent = deque()  # monotonic times of packets in the last 5 s

    def write(self, now: float, raw: bytes, bike=None, smoothed=None, throttle=None, error="", drive=None):
        self.packets += 1
        if error:
            self.bad_packets += 1
        self._recent.append(now)
        while self._recent and now - self._recent[0] > 5.0:
            self._recent.popleft()
        self._writer.writerow({
            "t_s": f"{now - self.start:.3f}",
            "wall_time": datetime.now().isoformat(timespec="milliseconds"),
            "power_w": "" if bike is None or bike.power_w is None else bike.power_w,
            "cadence_rpm": "" if bike is None or bike.cadence_rpm is None else f"{bike.cadence_rpm:.1f}",
            "speed_kmh": "" if bike is None or bike.speed_kmh is None else f"{bike.speed_kmh:.2f}",
            "smoothed_w": "" if smoothed is None else f"{smoothed:.1f}",
            "throttle": "" if throttle is None else f"{throttle:.3f}",
            "brake": "" if drive is None else f"{drive.brake:.3f}",
            "target_kmh": "" if drive is None else f"{drive.target_kmh:.1f}",
            "car_est_kmh": "" if drive is None else f"{drive.car_est_kmh:.1f}",
            "flags": "" if bike is None else f"0x{bike.flags:04x}",
            "raw_hex": raw.hex(" ").upper(),
            "error": error,
        })
        self._file.flush()

    def packet_rate(self, now: float) -> float:
        while self._recent and now - self._recent[0] > 5.0:
            self._recent.popleft()
        if len(self._recent) < 2:
            return 0.0
        span = now - self._recent[0]
        return len(self._recent) / span if span > 0 else 0.0

    def close(self) -> None:
        self._file.close()


def format_status(elapsed, state, power, cadence, drive, rate, bad, limit=None) -> str:
    m, s = divmod(int(elapsed), 60)
    p = "  --" if power is None else f"{power:4d}"
    c = " --" if cadence is None else f"{cadence:3.0f}"
    # Kept under ~80 columns: the ride.bat console wrapped the longer line (ride 12:23).
    if limit is not None:
        value, units = limit
        car = f"limit {'--' if value is None else value:>3}{units.replace('km/h', 'kph')}"
    else:
        car = f"car~{drive.car_est_kmh:4.0f}"
    return (
        f"{m:02d}:{s:02d} {state[:13]:<10} {p}W {c}rpm tgt{drive.target_kmh:4.0f} {car} "
        f"thr{drive.throttle * 100:3.0f}% brk{drive.brake * 100:3.0f}% {rate:3.1f}Hz bad {bad}"
    )
