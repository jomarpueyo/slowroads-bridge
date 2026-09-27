"""Saved ride settings (settings.json at the project root), written by tools/calibrate_car.py.

python -m bridge uses these as its defaults; command-line flags still override them.
"""

import json
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"

# Keys match bridge command-line option names (dest form).
KEYS = ("gear", "accel", "coast", "drag_quad", "brake_rate", "top_speed", "deadzone",
        "max_throttle", "max_brake", "ramp")


def load(path: Path = SETTINGS_PATH) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in data.get("ride", {}).items() if k in KEYS}


def save(ride: dict, meta: dict, path: Path = SETTINGS_PATH) -> None:
    """Merge ride values into the file, keeping keys not being updated (e.g. a hand-set gear)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("ride", {}).update({k: round(float(v), 5) for k, v in ride.items() if k in KEYS})
    data["calibration"] = meta
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
