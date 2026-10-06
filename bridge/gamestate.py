"""The road you chose in Slow Roads, read from the game's own saved settings (read-only).

Slow Roads keeps its settings in Chromium Local Storage (%APPDATA%\\slowroads\\Local Storage\\leveldb, about
120 KB). Each save holds {"ts": ..., "settings": {"World": {"seed", "scene", "roadStyle", "laneStyle", ...}}}.
Mapped in-game 2026-10-05 by starting a road with each option of the new-road screen's bottom row:

    laneStyle 0  wide dirt/gravel road       -> gravel
    laneStyle 1  narrow dirt track           -> gravel   (also the 2026-10-04 17:41 ride: forest dirt track)
    laneStyle 2  paved, no centre line       -> tarmac
    laneStyle 3  paved, edge lines           -> tarmac
    laneStyle 4  paved, centre line          -> tarmac

The game writes a save when a road is generated and about once a minute after that (observed delay 0-200 s),
so a road changed mid-ride is picked up within a few minutes. This reads a file the game already writes; it
never touches the game's memory, files or behaviour. Run `python -m bridge.gamestate` to see the current road.
"""

import json
import os
import re
import sys
from pathlib import Path

STORE = Path(os.environ.get("APPDATA", "")) / "slowroads" / "Local Storage" / "leveldb"
GRAVEL_LANE_STYLES = {0, 1}
LANE_NAMES = {0: "wide dirt road", 1: "dirt track", 2: "paved, no lines", 3: "paved, edge lines",
              4: "paved, centre line"}
MAX_FILE_BYTES = 8 * 1024 * 1024
_SAVE_RE = re.compile(rb'"ts":(\d{13}),"settings":\{"World":(\{[ -z|~]{1,400}?\})')


def read_world(store: Path = STORE):
    """(timestamp ms, World settings dict) of the newest save, or None."""
    best = None
    try:
        files = [f for f in Path(store).iterdir() if f.suffix in (".log", ".ldb")]
    except OSError:
        return None
    for f in files:
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                continue
            data = f.read_bytes()
        except OSError:  # the game may be writing it
            continue
        for m in _SAVE_RE.finditer(data):
            try:
                world = json.loads(m.group(2))
            except ValueError:
                continue  # a fragment cut by leveldb block headers or compression
            ts = int(m.group(1))
            if isinstance(world, dict) and (best is None or ts > best[0]):
                best = (ts, world)
    return best


def surface_of(world) -> str | None:
    """'gravel', 'tarmac' or None (unknown)."""
    lane = world.get("laneStyle") if isinstance(world, dict) else None
    if not isinstance(lane, int) or isinstance(lane, bool):
        return None
    return "gravel" if lane in GRAVEL_LANE_STYLES else "tarmac"


class RoadWatcher:
    """Polls the save (cheaply: only when a file changed) and reports the road surface."""

    def __init__(self, store: Path = STORE, interval_s: float = 15.0) -> None:
        self.store, self.interval = Path(store), interval_s
        self.surface: str | None = None
        self.world: dict | None = None
        self._next = 0.0
        self._stamp = None

    def _files_stamp(self):
        try:
            return tuple(sorted((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in self.store.iterdir()
                                if f.suffix in (".log", ".ldb")))
        except OSError:
            return None

    def poll(self, now: float) -> str | None:
        """Returns the surface when it changed since the last poll, else None."""
        if now < self._next:
            return None
        self._next = now + self.interval
        stamp = self._files_stamp()
        if stamp is None or stamp == self._stamp:
            return None
        self._stamp = stamp
        found = read_world(self.store)
        if found is None:
            return None
        self.world = found[1]
        surface = surface_of(self.world)
        if surface is not None and surface != self.surface:
            self.surface = surface
            return surface
        return None


def main(argv=None) -> int:
    found = read_world()
    if found is None:
        print(f"no Slow Roads save found in {STORE}")
        return 1
    ts, world = found
    from datetime import datetime

    lane = world.get("laneStyle")
    print(f"saved {datetime.fromtimestamp(ts / 1000):%Y-%m-%d %H:%M:%S}: scene {world.get('scene')}, "
          f"road {LANE_NAMES.get(lane, lane)} -> {surface_of(world) or 'unknown'}")
    return 0


if __name__ == "__main__":
    from .crashreport import run_main

    sys.exit(run_main(main, "gamestate"))
