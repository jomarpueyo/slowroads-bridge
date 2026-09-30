"""Saved settings (settings.json at the project root).

"ride": numeric ride preferences and calibration, written by tools/calibrate_car.py; python -m bridge
uses them as defaults and command-line flags still override them.
"trainer": the Bluetooth address of the paired trainer, saved on the first successful connection so
the bridge never connects to another device later (docs/SECURITY.md finding 1).

The file is user-edited, so everything read from it is validated: wrong types or non-finite numbers
are ignored with a warning instead of crashing the bridge or poisoning the controller with NaN
(docs/SECURITY.md finding 5).
"""

import json
import logging
import math
import re
from pathlib import Path

log = logging.getLogger("bridge.settings")
SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"

# Keys match bridge command-line option names (dest form).
KEYS = ("gear", "accel", "coast", "drag_quad", "brake_rate", "top_speed", "deadzone",
        "max_throttle", "max_brake", "ramp",
        # quality-of-life preferences (numbers; 0 = off): sounds, hotkeys and launch_game are 0/1,
        # idle_end is minutes without pedalling before the ride ends, keep_days is log retention.
        "sounds", "hotkeys", "launch_game", "idle_end", "keep_days",
        # overlay: 0/1; ftp: functional threshold power in watts (0 = estimate from past rides)
        "overlay", "ftp")
# Bluetooth address as Windows/bleak report it: six hex pairs.
ADDRESS_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeDecodeError) as e:
        log.warning("ignoring unreadable %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        log.warning("ignoring %s: top level is %s, not an object", path, type(data).__name__)
        return {}
    return data


def load(path: Path = SETTINGS_PATH) -> dict:
    ride = _read(path).get("ride", {})
    if not isinstance(ride, dict):
        log.warning("ignoring settings 'ride': not an object")
        return {}
    out = {}
    for k, v in ride.items():
        if k not in KEYS:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            log.warning("ignoring settings ride.%s=%r: not a finite number", k, v)
            continue
        out[k] = float(v)
    return out


def load_trainer(path: Path = SETTINGS_PATH) -> str | None:
    trainer = _read(path).get("trainer", {})
    addr = trainer.get("address") if isinstance(trainer, dict) else None
    if addr is None:
        return None
    if not isinstance(addr, str) or not ADDRESS_RE.match(addr):
        log.warning("ignoring settings trainer.address=%r: not a Bluetooth address", addr)
        return None
    return addr.upper()


def _write(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def save(ride: dict, meta: dict, path: Path = SETTINGS_PATH) -> None:
    """Merge ride values into the file, keeping keys not being updated (e.g. a hand-set gear)."""
    data = _read(path)
    if not isinstance(data.get("ride"), dict):
        data["ride"] = {}
    for k, v in ride.items():
        if k in KEYS and isinstance(v, (int, float)) and math.isfinite(v):
            data["ride"][k] = round(float(v), 5)
    data["calibration"] = meta
    _write(data, path)


def save_pref(key: str, value: float, path: Path = SETTINGS_PATH) -> None:
    """Set one ride value (e.g. the gear from the F6/F7 hotkeys), keeping everything else."""
    if key not in KEYS or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"bad setting {key}={value!r}")
    data = _read(path)
    if not isinstance(data.get("ride"), dict):
        data["ride"] = {}
    data["ride"][key] = round(float(value), 5)
    _write(data, path)


def save_trainer(address: str, name: str, path: Path = SETTINGS_PATH) -> None:
    if not ADDRESS_RE.match(address or ""):
        raise ValueError(f"not a Bluetooth address: {address!r}")
    data = _read(path)
    data["trainer"] = {"address": address.upper(), "name": str(name)[:64]}
    _write(data, path)
