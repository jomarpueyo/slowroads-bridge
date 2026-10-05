"""Guided workouts for the overlay: blocks with a power target (a share of FTP), optional cadence target,
countdown, and a beep at each change. The trainer is never controlled (data flows one way): you hold the
target, the overlay shows whether you're on it.

  ride.bat --workout endurance      (or pick one from the menu ride.bat shows at the start)
  python -m bridge.workouts         list them with today's targets
"""

from collections import deque
from dataclasses import dataclass, field

KEYS = ("easy", "endurance", "cadence", "tempo", "sweetspot", "long", "ramp")


@dataclass
class Block:
    name: str
    seconds: int
    lo: float | None = None        # share of FTP (None = ride as you like)
    hi: float | None = None
    cadence: tuple | None = None   # (lo, hi) rpm
    watts: float | None = None     # absolute target (ramp test steps)


@dataclass
class Workout:
    key: str
    title: str
    blurb: str
    blocks: list = field(default_factory=list)
    ramp: bool = False

    @property
    def seconds(self) -> int:
        return sum(b.seconds for b in self.blocks)


def _warm(minutes=5):
    return Block("Warm up", minutes * 60, 0.45, 0.60)


def _cool(minutes=5):
    return Block("Cool down", minutes * 60, 0.40, 0.55)


def build(key: str, ftp: float, long_min: int = 45) -> Workout:
    z2 = (0.60, 0.72)
    if key == "easy":
        return Workout(key, "Easy spin 30", "Recovery and habit: light, smooth, 85-95 rpm.",
                       [_warm(), Block("Easy", 20 * 60, 0.50, 0.65, (85, 95)), _cool()])
    if key == "endurance":
        return Workout(key, "Endurance 45", "Steady Z2 aerobic base in three 10 min blocks.",
                       [_warm(), *[Block(f"Steady {i}/3", 10 * 60, *z2) for i in (1, 2, 3)],
                        Block("Steady finish", 5 * 60, *z2), _cool(5)])
    if key == "cadence":
        blocks = [_warm()]
        for i in range(1, 7):
            blocks += [Block(f"Fast spin {i}/6", 120, 0.55, 0.70, (95, 105)), Block("Normal", 120, 0.55, 0.70, (80, 90))]
        return Workout(key, "Cadence drills 34", "Spin fast at easy power: smoother, rounder pedalling.",
                       blocks + [_cool()])
    if key == "tempo":
        blocks = [_warm(10)]
        for i in (1, 2, 3):
            blocks += [Block(f"Tempo {i}/3", 6 * 60, 0.76, 0.88), Block("Easy", 3 * 60, 0.50, 0.60)]
        return Workout(key, "Tempo 3x6", "Steady, comfortably hard: practise even pacing.", blocks + [_cool()])
    if key == "sweetspot":
        return Workout(key, "Sweet spot 2x10", "Just under threshold: the most fitness per minute.",
                       [_warm(10), Block("Sweet spot 1/2", 10 * 60, 0.88, 0.94), Block("Easy", 5 * 60, 0.50, 0.60),
                        Block("Sweet spot 2/2", 10 * 60, 0.88, 0.94), _cool()])
    if key == "long":
        n = max(2, -(-long_min // 16))                    # blocks of about 15 min
        block_s = int((long_min - (n - 1)) * 60 / n)      # 1 min stand-up break between blocks
        blocks = []
        for i in range(1, n + 1):
            blocks.append(Block(f"Block {i}/{n}", block_s, *z2))
            if i < n:
                blocks.append(Block("Stand, stretch & drink", 60, None, None))
        return Workout(key, f"Long ride {long_min}", f"Steady Z2 in {n} blocks of {block_s // 60} min with a 1 min "
                       "stand-up break (and a drink) between: easier on the seat and the mind.", blocks)
    if key == "ramp":
        start = max(60, round(ftp * 0.5 / 10) * 10 if ftp else 100)
        steps = [Block(f"Step {i + 1}", 60, watts=start + 20 * i) for i in range(30)]
        return Workout(key, "Ramp test (FTP)", "Ride each 1 min step at its target until you can't hold it. "
                       "FTP = 75% of your best minute. Go all out at the end.",
                       [Block("Warm up", 5 * 60, 0.45, 0.60)] + steps, ramp=True)
    raise KeyError(key)


def title_of(key: str, coach=None) -> str:
    long_min = coach.long_ride_target_min() if coach else 45
    return build(key, 150, long_min).title


class WorkoutRunner:
    """Follows a workout in ride time (time since the first pedal stroke, paused time excluded by the bridge)."""

    RAMP_FAIL_W, RAMP_FAIL_S = 20, 15  # ramp ends when 3 s power is 20 W under the step for 15 s

    def __init__(self, workout: Workout, ftp: float) -> None:
        self.w, self.ftp = workout, ftp or 150.0
        self._idx = -1
        self._said = set()
        self.done = False
        self._fail_s = 0.0
        self._last_t = None
        self._recent: deque = deque()  # (t, watts) for the ramp's best minute
        self.best_minute = 0.0
        self.result_ftp = None

    def _block_at(self, t: float):
        acc = 0
        for i, b in enumerate(self.w.blocks):
            if t < acc + b.seconds:
                return i, b, acc + b.seconds - t
            acc += b.seconds
        return None, None, 0.0

    def target(self, b: Block):
        if b.watts is not None:
            return b.watts - 5, b.watts + 5
        if b.lo is None:
            return None
        return round(b.lo * self.ftp / 5) * 5, round(b.hi * self.ftp / 5) * 5

    def update(self, t: float, power, cadence) -> tuple[dict | None, list]:
        """(status for the overlay, [(cue, message)]) at ride time t."""
        events = []
        if self.done:
            return None, events
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        total_left = max(0.0, self.w.seconds - t)
        idx, b, left = self._block_at(t)
        if b is None:
            self.done = True
            events.append(("workout_done", f"{self.w.title.upper()} DONE"))
            return None, events + self._ramp_finish()
        if idx != self._idx:
            self._idx = idx
            tgt = self.target(b)
            what = f"{tgt[0]:.0f}-{tgt[1]:.0f} W" if tgt and b.watts is None else (
                f"{b.watts:.0f} W" if b.watts else "ride easy")
            events.append(("block", f"{b.name.upper()}  {b.seconds // 60 or b.seconds}"
                                    f"{' MIN' if b.seconds >= 60 else ' S'}  {what}"))
        if not self.w.ramp:
            if total_left <= self.w.seconds / 2 and "half" not in self._said and self.w.seconds >= 20 * 60:
                self._said.add("half")
                events.append(("milestone", "HALFWAY"))
            if total_left <= 300 and "last5" not in self._said and self.w.seconds >= 15 * 60:
                self._said.add("last5")
                events.append(("milestone", "LAST 5 MINUTES"))
        tgt = self.target(b)
        state = "free"
        if tgt and power is not None:
            state = "low" if power < tgt[0] else "high" if power > tgt[1] else "ok"
        if self.w.ramp and power is not None:
            self._recent.append((t, power))
            while self._recent and self._recent[0][0] < t - 60:
                self._recent.popleft()
            if t - self._recent[0][0] >= 55:
                self.best_minute = max(self.best_minute, sum(p for _, p in self._recent) / len(self._recent))
            if b.watts is not None and idx >= 4:  # ignore the first steps
                self._fail_s = self._fail_s + dt if power < b.watts - self.RAMP_FAIL_W else 0.0
                if self._fail_s >= self.RAMP_FAIL_S:
                    self.done = True
                    events.append(("workout_done", "RAMP TEST DONE"))
                    return None, events + self._ramp_finish()
        status = {"title": self.w.title, "block": b.name, "index": idx + 1, "count": len(self.w.blocks),
                  "left": left, "total_left": total_left, "target": tgt, "cadence": b.cadence, "state": state}
        return status, events

    def _ramp_finish(self) -> list:
        if not self.w.ramp or not self.best_minute:
            return []
        self.result_ftp = round(self.best_minute * 0.75)
        return [("record", f"FTP {self.result_ftp} W (best minute {self.best_minute:.0f} W)")]


def main(argv=None) -> int:
    import sys
    from pathlib import Path

    from .coach import Coach
    from .ridelog import LOG_DIR

    coach = Coach.from_settings(Path(LOG_DIR))
    ftp, source = coach.ftp()
    print(f"FTP {ftp:.0f} W ({source or 'unknown: targets assume 150 W'})\n")
    for key in KEYS:
        w = build(key, ftp or 150, coach.long_ride_target_min())
        print(f"{key:<10} {w.title:<22} {w.seconds // 60:3d} min  {w.blurb}")
    key, why = coach.suggest()
    print(f"\nsuggested today: {key} ({why})\nride with:  ride.bat --workout {key}")
    return 0


if __name__ == "__main__":
    import sys

    from .crashreport import run_main

    sys.exit(run_main(main, "workouts"))
