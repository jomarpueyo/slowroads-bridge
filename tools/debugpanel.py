"""Read Slow Roads' debug panel (F4) with Windows OCR: position (x, elevation y, z), rpm, speed.

TESTING ONLY, like speedo.py. The panel sits top-left (1920x1080: x 0-280, y 0-510) and shows
e.g. "pos 11613.0, 13.3, -831.2", "rpm 1469.00", "speed 0kph". pos.y is elevation in metres
(the game is three.js, y-up), so grade = d(elevation) / d(horizontal distance).
"""

import math
import re
from dataclasses import dataclass

from speedo import SpeedoReader

# Value columns only (labels OCR as separate lines and break the pairing).
POS_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)")
RPM_RE = re.compile(r"(\d+(?:\.\d+)?)")
SPEED_RE = re.compile(r"(\d+(?:\.\d+)?)\s*k", re.I)
POS_BOX = {"left": 90, "top": 34, "width": 190, "height": 20}
VEHICLE_BOX = {"left": 170, "top": 466, "width": 110, "height": 38}


def _f(s: str) -> float:
    return float(s.replace(",", "."))


@dataclass
class DebugReading:
    x: float | None
    elev: float | None
    z: float | None
    rpm: float | None
    kph: float | None
    text: str


class DebugPanelReader:
    def __init__(self) -> None:
        self.pos = SpeedoReader(region=POS_BOX, scale=3)
        self.veh = SpeedoReader(region=VEHICLE_BOX, scale=3)

    def read(self) -> DebugReading:
        pt = self.pos.read().text  # read() has a timeout, see speedo.SpeedoReader.read
        vt = self.veh.read().text
        # OCR splits "11613.0, 13.3, -831.2" into "11613.ø, | 13.3, | -831.2"
        clean = pt.replace("ø", "0").replace("O", "0").replace("|", " ")
        m = POS_RE.search(clean.replace("—", "-").replace("–", "-"))  # minus OCRs as a dash
        x = elev = z = None
        if m:
            x, elev, z = (float(g) for g in m.groups())
        vt0 = vt.replace("ø", "0").replace("O", "0").replace("e", "0")
        parts = vt0.split("|")
        r = RPM_RE.search(parts[0]) if parts else None
        s = SPEED_RE.search(vt0)
        return DebugReading(x, elev, z, float(r.group(1)) if r else None, float(s.group(1)) if s else None,
                            f"{pt} || {vt}")


def grade(p0: DebugReading, p1: DebugReading) -> float | None:
    """Rise over horizontal run between two readings (0.05 = 5 %). None if too close to tell."""
    if None in (p0.x, p0.elev, p0.z, p1.x, p1.elev, p1.z):
        return None
    run = math.hypot(p1.x - p0.x, p1.z - p0.z)
    return None if run < 5.0 else (p1.elev - p0.elev) / run
