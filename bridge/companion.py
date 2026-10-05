"""In-ride companion: short overlay messages (with a beep) that make long rides easier and progress visible.

  - Stand-up break every --comfort-break minutes of riding (default 20; 0 = off): stand and stretch for 30 s,
    which relieves the saddle on long rides.
  - Live personal bests: when your rolling 1 / 5 / 20 min power beats your record during the ride.
  - Lifetime milestones: crossing 10, 25, 50, 100 ... lifetime miles.
  - A quiet check-in every 15 minutes ("30 MIN DONE") so a long ride feels like blocks, not one long slog.
Trainer data only.
"""

from .coach import MILESTONES, next_milestone
from .ridebook import duration_label

LIVE_RECORD_DURATIONS = (60, 300, 1200)


class Companion:
    def __init__(self, records: dict, lifetime_miles: float, comfort_break_min: float = 20.0,
                 checkin_min: float = 15.0) -> None:
        self.records = dict(records)           # duration s -> watts before this ride
        self.lifetime_miles = lifetime_miles
        self.comfort_s = comfort_break_min * 60
        self.checkin_s = checkin_min * 60
        self._next_break = self.comfort_s if self.comfort_s > 0 else float("inf")
        self._next_checkin = self.checkin_s if self.checkin_s > 0 else float("inf")
        self._next_mile = next_milestone(lifetime_miles)
        self._announced: dict = {}

    def update(self, ride_s: float, rolling: dict, ride_miles: float, in_workout_break: bool = False) -> list:
        """[(cue, message)] for ride time `ride_s`; rolling = {duration: current rolling average W or None}."""
        out = []
        if ride_s >= self._next_break:
            self._next_break += self.comfort_s
            if not in_workout_break:
                out.append(("break", "STAND UP & STRETCH · 30 S"))
        if ride_s >= self._next_checkin:
            done = int(self._next_checkin // 60)
            self._next_checkin += self.checkin_s
            out.append(("", f"{done} MIN DONE"))
        for d in LIVE_RECORD_DURATIONS:
            w = rolling.get(d)
            old = self.records.get(d)
            if w and old and w > old * 1.01 and w > self._announced.get(d, 0) * 1.03:
                self._announced[d] = w
                out.append(("record", f"NEW BEST {duration_label(d).upper()} · {w:.0f} W"))
        total = self.lifetime_miles + ride_miles
        if total >= self._next_mile:
            out.append(("milestone", f"{self._next_mile} LIFETIME MILES"))
            self._next_mile = next_milestone(total)
        return out


__all__ = ["Companion", "MILESTONES"]
