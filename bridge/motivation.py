"""Motivation features from the 2026-10-04 research (docs/RESEARCH.md section 18), chosen to add almost nothing
to the screen:

  - welcome back after a break (coming back after a miss is what builds the habit),
  - a ride plan: your days, time and ride length (if-then plans), shown at the start,
  - finish easy: a nudge to ease off in the last minutes (an easy end is remembered as a better ride),
  - "how did it feel?" after the ride (1-5); the coach eases off when rides keep feeling hard,
  - a virtual journey: lifetime miles along a real road, with the next town as a small goal,
  - your ghost: this ride against your last similar one, a few times per ride,
  - one monthly challenge (rides, miles or one long ride), fresh each month.

Everything is stored in logs/ridebook.json (git-ignored). Set things with `python -m bridge.plan`.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .units import KM_PER_MILE

COMEBACK_DAYS = 4
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
FEEL_WORDS = {1: "easy", 2: "comfortable", 3: "moderate", 4: "hard", 5: "very hard"}

# Real roads with approximate road miles of towns along the way (for fun, not navigation).
ROUTES = (
    ("Pacific Coast Highway", 656, ((0, "Dana Point"), (9, "Laguna Beach"), (17, "Newport Beach"),
                                    (24, "Huntington Beach"), (41, "Long Beach"), (62, "Redondo Beach"),
                                    (74, "Santa Monica"), (86, "Malibu"), (116, "Oxnard"), (125, "Ventura"),
                                    (152, "Santa Barbara"), (248, "Pismo Beach"), (260, "San Luis Obispo"),
                                    (273, "Morro Bay"), (292, "Cambria"), (313, "San Simeon"), (378, "Big Sur"),
                                    (403, "Carmel"), (408, "Monterey"), (449, "Santa Cruz"), (498, "Half Moon Bay"),
                                    (524, "San Francisco"), (548, "Stinson Beach"), (588, "Bodega Bay"),
                                    (600, "Mendocino"), (610, "Fort Bragg"), (656, "Leggett"))),
    ("Route 66", 2448, ((0, "Chicago"), (200, "Springfield, IL"), (300, "St. Louis"), (600, "Joplin"),
                        (700, "Tulsa"), (800, "Oklahoma City"), (1060, "Amarillo"), (1350, "Albuquerque"),
                        (1490, "Gallup"), (1680, "Flagstaff"), (1830, "Kingman"), (2050, "Barstow"),
                        (2448, "Santa Monica"))),
)


# --------------------------------------------------------------------------- the ride book file

def load_book(log_dir: Path) -> dict:
    from .ridebook import BOOK_NAME, _read_json

    return _read_json(Path(log_dir) / BOOK_NAME)


def update_book(log_dir: Path, fn) -> None:
    from .ridebook import _update_book

    _update_book(log_dir, fn)


# --------------------------------------------------------------------------- welcome back

def comeback_days(last_ride_day: date | None, today: date) -> int | None:
    """Days off before today's ride if it's a comeback (4+ days), else None."""
    if last_ride_day is None:
        return None
    gap = (today - last_ride_day).days
    return gap if gap >= COMEBACK_DAYS else None


# --------------------------------------------------------------------------- ride plan

@dataclass
class Plan:
    days: tuple          # weekday numbers, Monday = 0
    time: str | None     # "18:30" or None
    minutes: int

    def text(self) -> str:
        days = " ".join(DAY_NAMES[d].capitalize() for d in self.days)
        return f"{days}{' ' + self.time if self.time else ''}, {self.minutes} min"

    def next_day(self, today: date) -> date:
        for i in range(7):
            d = today + timedelta(days=i)
            if d.weekday() in self.days:
                return d
        return today


def parse_plan(words: list) -> Plan:
    """['tue', 'thu', 'sat', '18:30', '30'] -> Plan. Raises ValueError with a readable message."""
    days, time_, minutes = [], None, 30
    for w in words:
        w = str(w).strip().lower()
        if w[:3] in DAY_NAMES and w[:3] not in [DAY_NAMES[d] for d in days]:
            days.append(DAY_NAMES.index(w[:3]))
        elif ":" in w:
            h, _, m = w.partition(":")
            if not (h.isdigit() and m.isdigit() and 0 <= int(h) < 24 and 0 <= int(m) < 60):
                raise ValueError(f"not a time: {w!r} (use 18:30)")
            time_ = f"{int(h):02d}:{int(m):02d}"
        elif w.isdigit() and 5 <= int(w) <= 300:
            minutes = int(w)
        else:
            raise ValueError(f"didn't understand {w!r}: use days (mon..sun), a time (18:30) and minutes (30)")
    if not days:
        raise ValueError("give at least one day, e.g. tue thu sat")
    return Plan(tuple(sorted(days)), time_, minutes)


def get_plan(book: dict) -> Plan | None:
    p = book.get("plan")
    if not isinstance(p, dict):
        return None
    try:
        days = tuple(sorted(int(d) for d in p.get("days", []) if 0 <= int(d) <= 6))
        minutes = int(p.get("minutes", 30))
    except (TypeError, ValueError):
        return None
    time_ = p.get("time") if isinstance(p.get("time"), str) else None
    return Plan(days, time_, max(5, min(300, minutes))) if days else None


def plan_line(plan: Plan | None, today: date) -> str | None:
    if plan is None:
        return None
    nxt = plan.next_day(today)
    when = "today" if nxt == today else "tomorrow" if nxt == today + timedelta(days=1) else nxt.strftime("%a")
    return f"plan: {plan.text()}; next ride {when}"


def ride_minutes(plan: Plan | None, weekly_minutes: int, weekly_rides: int) -> int:
    """How long today's free ride is meant to be (for the finish-easy nudge)."""
    if plan is not None:
        return plan.minutes
    return max(15, int(round(weekly_minutes / max(1, weekly_rides) / 5) * 5))


# --------------------------------------------------------------------------- how did it feel

def feels(book: dict) -> dict:
    out = {}
    for stamp, v in (book.get("feel") or {}).items():
        if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 5:
            out[stamp] = v
    return out


def set_feel(log_dir: Path, stamp: str, value: int) -> None:
    if not 1 <= int(value) <= 5:
        raise ValueError("feel is 1 (easy) to 5 (very hard)")

    def fn(book):
        book.setdefault("feel", {})[stamp] = int(value)
    update_book(log_dir, fn)


def feel_trend(feel_by_stamp: dict, stamps: list, n: int = 3) -> float | None:
    """Average feel of the last n rated rides among `stamps` (oldest first)."""
    vals = [feel_by_stamp[s] for s in stamps if s in feel_by_stamp][-n:]
    return sum(vals) / len(vals) if vals else None


# --------------------------------------------------------------------------- finish easy

def finished_easy(series: list, tail_s: int = 120) -> bool | None:
    """True if the last couple of minutes were clearly easier than the ride (a good finish)."""
    if not series or len(series) < tail_s * 3:
        return None
    avg = sum(series) / len(series)
    tail = series[-tail_s:]
    return sum(tail) / len(tail) <= 0.85 * avg if avg > 0 else None


# --------------------------------------------------------------------------- virtual journey

def journey(miles: float) -> dict:
    """Where `miles` of lifetime riding puts you along the routes (they follow on from each other)."""
    total = sum(r[1] for r in ROUTES)
    lap, rest = divmod(max(0.0, miles), total)
    for name, length, places in ROUTES:
        if rest < length:
            passed = [p for p in places if p[0] <= rest]
            ahead = [p for p in places if p[0] > rest]
            nxt = ahead[0] if ahead else (length, places[-1][1])
            return {"route": name, "length": length, "on_route": rest, "lap": int(lap) + 1,
                    "last": passed[-1][1] if passed else places[0][1], "next": nxt[1], "to_next": nxt[0] - rest}
        rest -= length
    name, length, places = ROUTES[-1]
    return {"route": name, "length": length, "on_route": length, "lap": int(lap) + 1, "last": places[-1][1],
            "next": places[-1][1], "to_next": 0.0}


def journey_line(miles: float) -> str:
    j = journey(miles)
    lap = f" (lap {j['lap']})" if j["lap"] > 1 else ""
    return (f"{j['route']}{lap}: {j['on_route']:.1f} of {j['length']} mi, past {j['last']}; "
            f"{j['next']} in {j['to_next']:.1f} mi")


# --------------------------------------------------------------------------- ghost of your last ride

def distance_timeline(series: list, rider_kg: float = 85.0) -> list:
    """Cumulative miles after each ridden second, replaying the watts through the virtual road bike."""
    from .drive import VirtualBike

    bike = VirtualBike(mass_kg=rider_kg)
    bike.substep = 0.25
    out, miles = [], 0.0
    for w in series:
        miles += bike.step(w, 1.0) / 3600 / KM_PER_MILE
        out.append(miles)
    return out


class Ghost:
    """Your last similar ride, second by second: how far ahead (+) or behind (-) you are now."""

    def __init__(self, timeline: list, label: str) -> None:
        self.timeline, self.label = timeline, label

    def gap(self, ride_s: float, miles_now: float) -> float | None:
        i = int(ride_s) - 1
        if not self.timeline or i < 0 or i >= len(self.timeline):
            return None
        return miles_now - self.timeline[i]


def pick_ghost(rides: list, workout: str | None, log_dir: Path, rider_kg: float = 85.0) -> Ghost | None:
    """The most recent ride with the same workout (or a free ride), at least 10 minutes long."""
    from .summary import summarize_csv

    for r in reversed(rides):
        if r.workout == workout and r.moving_s >= 600:
            path = Path(log_dir) / f"ride-{r.stamp}.csv"
            try:
                series = summarize_csv(path, rider_kg).series
            except OSError:
                continue
            if series:
                return Ghost(distance_timeline(series, rider_kg), f"{r.when:%a %d %b}")
    return None


# --------------------------------------------------------------------------- monthly challenge

KINDS = {"rides": "rides", "miles": "miles", "long": "one ride of"}


def challenge(book: dict, month: str, weekly_rides: int, year: int, month_no: int) -> dict:
    """This month's challenge: the one you set, or rides = your weekly goal over the month."""
    c = (book.get("challenges") or {}).get(month)
    if isinstance(c, dict) and c.get("kind") in KINDS and isinstance(c.get("target"), (int, float)):
        return {"kind": c["kind"], "target": float(c["target"]), "set": True}
    days = calendar.monthrange(year, month_no)[1]
    return {"kind": "rides", "target": float(round(weekly_rides * days / 7)), "set": False}


def challenge_progress(ch: dict, month_rides: list) -> float:
    if ch["kind"] == "rides":
        return float(len(month_rides))
    if ch["kind"] == "miles":
        return sum(r.miles for r in month_rides)
    return max((r.moving_s / 60 for r in month_rides), default=0.0)   # long: minutes of the longest ride


def challenge_line(ch: dict, progress: float, month_name: str) -> str:
    unit = {"rides": "rides", "miles": "mi", "long": "min"}[ch["kind"]]
    done = progress >= ch["target"]
    if ch["kind"] == "long":
        what = f"one {ch['target']:.0f} min ride (longest so far {progress:.0f} min)"
    else:
        what = f"{progress:.0f}/{ch['target']:.0f} {unit}" if unit == "rides" else \
            f"{progress:.1f}/{ch['target']:.0f} {unit}"
    return f"{month_name} challenge: {what}{'  DONE' if done else ''}"


def set_challenge(log_dir: Path, month: str, kind: str, target: float) -> None:
    if kind not in KINDS or not 1 <= float(target) <= 100000:
        raise ValueError("challenge: rides N | miles N | long MINUTES")

    def fn(book):
        book.setdefault("challenges", {})[month] = {"kind": kind, "target": float(target)}
    update_book(log_dir, fn)
