"""Ride book: every ride in one place, from the trainer data in logs/ (never the game).

  python -m bridge.ridebook              scoreboard: lifetime, records, this week, form, what to work on
  python -m bridge.ridebook list         every ride, newest first, numbered
  python -m bridge.ridebook show N       one ride in full (N from the list; 1 = newest)
  python -m bridge.ridebook hide N       leave a ride out of totals and records (tests, false starts)
  python -m bridge.ridebook unhide N
  python -m bridge.ridebook note N TEXT  add a note to a ride ("sore seat", "new saddle", ...)
  python -m bridge.ridebook dashboard    write logs/dashboard.html and open it (or double-click rides.bat)

Everything stays in logs/ (git-ignored). ride-index.json is a cache rebuilt from the ride CSVs when they
change; ridebook.json keeps your hidden rides and notes. Scripted --sim runs never count.
"""

import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .units import KM_PER_MILE, hms
from .summary import MIN_RIDE_S, is_simulated, summarize_csv

log = logging.getLogger("bridge.ridebook")

DURATIONS = (5, 15, 30, 60, 120, 180, 300, 480, 600, 720, 1200, 1800, 2700, 3600)
INDEX_VERSION = 3  # bump when Ride fields or their meaning change: the cache is rebuilt
INDEX_NAME, BOOK_NAME = "ride-index.json", "ridebook.json"
COAST_W, COAST_MIN_S = 25.0, 10  # a "stop": at least 10 s under 25 W


def duration_label(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h"


def power_curve(series: list) -> dict:
    """Best average power for each duration in DURATIONS (prefix sums, O(n) per duration)."""
    out = {}
    if not series:
        return out
    pre = [0.0]
    for w in series:
        pre.append(pre[-1] + w)
    n = len(series)
    for d in DURATIONS:
        if d > n:
            break
        out[d] = max(pre[i + d] - pre[i] for i in range(n - d + 1)) / d
    return out


def _histogram(values: list, width: float) -> dict:
    h: dict = {}
    for v in values:
        k = int(v // width)
        h[k] = h.get(k, 0) + 1
    return h


def _efforts(series: list) -> tuple[int, int]:
    """(longest stretch without a coast of 10+ s, number of such coasts)."""
    longest = run = coasts = low = 0
    for w in series:
        if w < COAST_W:
            low += 1
            if low == COAST_MIN_S:
                coasts += 1
                longest = max(longest, run - (COAST_MIN_S - 1))
                run = 0
        else:
            low = 0
        run += 1
    longest = max(longest, run - low)
    return longest, coasts


@dataclass
class Ride:
    stamp: str
    start: str                     # ISO time the ride started
    moving_s: float
    distance_km: float             # virtual road bike (Zwift-like)
    trainer_km: float
    work_kj: float
    avg_w: float
    np_w: float | None
    max_w: float
    avg_cad: float
    curve: dict                    # duration s -> best watts
    first_half_w: float | None
    second_half_w: float | None
    low_share: float
    power_hist: dict               # 10 W bin -> seconds
    cadence_hist: dict             # 10 rpm bin -> seconds
    longest_steady_s: int
    coasts: int
    workout: str | None = None
    hidden: bool = False
    note: str = ""
    size: int = 0
    mtime: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.start)

    @property
    def miles(self) -> float:
        return self.distance_km / KM_PER_MILE

    @property
    def vi(self) -> float | None:
        return self.np_w / self.avg_w if self.np_w and self.avg_w else None


def _stamp_time(stamp: str) -> datetime | None:
    try:
        return datetime.strptime(stamp, "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def _workout_of(ride_csv: Path) -> str | None:
    log_path = ride_csv.with_name(ride_csv.name.replace("ride-", "bridge-", 1)).with_suffix(".log")
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.search(r" workout=(\S+)", line)
                if m:
                    return None if m.group(1) == "None" else m.group(1)
                if " start " in line:
                    return None
    except OSError:
        pass
    return None


def analyse(ride_csv: Path, rider_kg: float = 85.0, summary=None) -> Ride | None:
    """The ride book's view of one ride (None for --sim and false starts). Pass the RideSummary if it's
    already been worked out, so the CSV isn't read twice."""
    stamp = ride_csv.stem.replace("ride-", "", 1)
    when = _stamp_time(stamp)
    if when is None or is_simulated(ride_csv):
        return None
    r = summary or summarize_csv(ride_csv, rider_kg)
    if r.moving_s < MIN_RIDE_S or not r.series:
        return None
    longest, coasts = _efforts(r.series)
    st = ride_csv.stat()
    return Ride(stamp=stamp, start=when.isoformat(), moving_s=r.moving_s, distance_km=r.virtual_km,
                trainer_km=r.distance_km, work_kj=r.work_kj, avg_w=r.avg_power_w, np_w=r.normalized_power_w,
                max_w=r.max_power_w, avg_cad=r.avg_cadence_rpm, curve=power_curve(r.series),
                first_half_w=r.first_half_w, second_half_w=r.second_half_w, low_share=r.low_power_share,
                power_hist=_histogram(r.series, 10), cadence_hist=_histogram(r.cadence_series or [], 10),
                longest_steady_s=longest, coasts=coasts, workout=_workout_of(ride_csv),
                size=st.st_size, mtime=st.st_mtime)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _from_json(d: dict) -> Ride:
    d = dict(d)
    d["curve"] = {int(k): v for k, v in d.get("curve", {}).items()}
    d["power_hist"] = {int(k): v for k, v in d.get("power_hist", {}).items()}
    d["cadence_hist"] = {int(k): v for k, v in d.get("cadence_hist", {}).items()}
    known = Ride.__dataclass_fields__
    return Ride(**{k: v for k, v in d.items() if k in known})


def load_rides(log_dir: Path, rider_kg: float = 85.0, include_hidden: bool = False,
               exclude: Path | None = None) -> list[Ride]:
    """All real rides, oldest first, from the cache (re-analysing only new or changed ride CSVs)."""
    log_dir = Path(log_dir)
    index_path = log_dir / INDEX_NAME
    cache = _read_json(index_path)
    if cache.get("version") != INDEX_VERSION or cache.get("rider_kg") != rider_kg:
        cache = {"version": INDEX_VERSION, "rider_kg": rider_kg, "rides": {}}
    cached = cache["rides"]
    book = _read_json(log_dir / BOOK_NAME)
    hidden, notes = set(book.get("hidden", [])), book.get("notes", {})
    rides, changed, seen = [], False, set()
    for path in sorted(log_dir.glob("ride-*.csv")) if log_dir.is_dir() else []:
        stamp = path.stem.replace("ride-", "", 1)
        seen.add(stamp)
        if exclude is not None and path.resolve() == Path(exclude).resolve():
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        entry = cached.get(stamp)
        if entry is not None and entry.get("size") == st.st_size and entry.get("mtime") == st.st_mtime:
            ride = None if entry.get("skip") else _from_json(entry)
        else:
            try:
                ride = analyse(path, rider_kg)
            except Exception:
                log.exception("could not analyse %s", path.name)
                ride = None
            cached[stamp] = asdict(ride) if ride else {"skip": True, "size": st.st_size, "mtime": st.st_mtime}
            changed = True
        if ride is None:
            continue
        ride.hidden, ride.note = stamp in hidden, str(notes.get(stamp, ""))
        if include_hidden or not ride.hidden:
            rides.append(ride)
    for stamp in list(cached):
        if stamp not in seen:
            del cached[stamp]
            changed = True
    if changed:
        try:
            index_path.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass
    rides.sort(key=lambda r: r.start)
    return rides


def _update_book(log_dir: Path, fn) -> None:
    path = Path(log_dir) / BOOK_NAME
    book = _read_json(path)
    book.setdefault("hidden", [])
    book.setdefault("notes", {})
    fn(book)
    path.write_text(json.dumps(book, indent=2) + "\n", encoding="utf-8")


def set_hidden(log_dir: Path, stamp: str, hidden: bool) -> None:
    def fn(book):
        s = set(book["hidden"])
        (s.add if hidden else s.discard)(stamp)
        book["hidden"] = sorted(s)
    _update_book(log_dir, fn)


def set_note(log_dir: Path, stamp: str, note: str) -> None:
    def fn(book):
        if note:
            book["notes"][stamp] = note[:200]
        else:
            book["notes"].pop(stamp, None)
    _update_book(log_dir, fn)


def format_list(rides: list[Ride]) -> str:
    if not rides:
        return "No rides yet."
    lines = ["  #  date              moving   miles   avg W   NP   kJ    workout / note"]
    for i, r in enumerate(reversed(rides), 1):
        np_ = f"{r.np_w:.0f}" if r.np_w else "--"
        tag = (r.workout or "") + (f"  {r.note}" if r.note else "") + ("  [hidden]" if r.hidden else "")
        lines.append(f"{i:3d}  {r.when:%a %d %b %H:%M}  {hms(r.moving_s):>7}  {r.miles:5.1f}  {r.avg_w:6.0f}  "
                     f"{np_:>3}  {r.work_kj:4.0f}  {tag}")
    return "\n".join(lines)


def format_ride(r: Ride) -> str:
    curve = "  ".join(f"{duration_label(d)} {w:.0f}" for d, w in r.curve.items())
    np_ = f"{r.np_w:.0f} W" if r.np_w else "--"
    lines = [f"== Ride {r.when:%A %d %B %Y %H:%M} ({r.stamp}) ==",
             f"Moving      {hms(r.moving_s)}   {r.distance_km:.2f} km ({r.miles:.2f} mi)",
             f"Power       avg {r.avg_w:.0f} W   NP {np_}   max {r.max_w:.0f} W   work {r.work_kj:.0f} kJ",
             f"Cadence     avg {r.avg_cad:.0f} rpm",
             f"Efforts     longest steady stretch {hms(r.longest_steady_s)}   coasts of 10 s+: {r.coasts}",
             f"Power curve {curve}"]
    if r.workout:
        lines.append(f"Workout     {r.workout}")
    if r.note:
        lines.append(f"Note        {r.note}")
    return "\n".join(lines)


def _pick(rides: list[Ride], n: str) -> Ride:
    try:
        i = int(n)
    except ValueError:
        raise SystemExit(f"ride number expected, got {n!r} (see: python -m bridge.ridebook list)")
    if not 1 <= i <= len(rides):
        raise SystemExit(f"no ride {i}: there are {len(rides)} (1 = newest)")
    return list(reversed(rides))[i - 1]


def main(argv=None) -> int:
    from . import settings
    from .ridelog import LOG_DIR

    argv = sys.argv[1:] if argv is None else argv
    saved = settings.load()
    rider_kg = saved.get("rider_kg", 85.0)
    cmd = argv[0] if argv else "board"
    if cmd == "board":
        from .coach import Coach

        print(Coach.from_settings(LOG_DIR).scoreboard_text())
        return 0
    if cmd == "dashboard":
        from .dashboard import main as dashboard

        return dashboard(argv[1:])
    rides = load_rides(LOG_DIR, rider_kg, include_hidden=True)
    if cmd == "list":
        print(format_list(rides))
    elif cmd == "show" and len(argv) > 1:
        print(format_ride(_pick(rides, argv[1])))
    elif cmd in ("hide", "unhide") and len(argv) > 1:
        r = _pick(rides, argv[1])
        set_hidden(LOG_DIR, r.stamp, cmd == "hide")
        print(f"{cmd}: {r.when:%a %d %b %H:%M}")
    elif cmd == "note" and len(argv) > 1:
        r = _pick(rides, argv[1])
        set_note(LOG_DIR, r.stamp, " ".join(argv[2:]))
        print(f"note saved for {r.when:%a %d %b %H:%M}")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    from .crashreport import run_main

    sys.exit(run_main(main, "ridebook"))
