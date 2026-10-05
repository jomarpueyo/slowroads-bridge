"""Ride coach on top of the ride book: FTP, training load, habit (weekly goal and streak), records, a power
profile of strengths and weak areas, and a simple weekly plan. Trainer data only; nothing from the game.

Training load uses the standard definitions: TSS = hours x IF^2 x 100 with IF = NP / FTP; fitness (CTL)
and fatigue (ATL) are 42- and 7-day exponentially weighted averages of daily TSS; form (TSB) = CTL - ATL.
Weak areas compare your best efforts with a typical all-round rider's shape relative to FTP; they reflect
what you have ridden, not your ceiling, so harder efforts and a ramp test sharpen them.
"""

from datetime import date, datetime, timedelta
from pathlib import Path

from .ridebook import KM_PER_MILE, Ride, duration_label, load_rides

# Best power as a multiple of FTP for a typical all-round recreational rider (shape, not level).
REFERENCE = {5: 3.2, 60: 1.75, 300: 1.18, 1200: 1.05}
PROFILE_NAMES = {5: "sprint (5 s)", 60: "short punch (1 min)", 300: "climbing efforts (5 min)",
                 1200: "threshold (20 min)"}
ZONES = ((0.55, "Z1"), (0.75, "Z2"), (0.90, "Z3"), (1.05, "Z4"), (1.20, "Z5"), (1.50, "Z6"), (99.0, "Z7"))
MILESTONES = (10, 25, 50, 75, 100, 150, 200, 250, 300, 400, 500, 750, 1000)


def _hms(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h" if seconds >= 3600 else f"{seconds / 60:.0f} min"


def next_milestone(miles: float) -> int:
    for m in MILESTONES:
        if m > miles:
            return m
    return int(miles // 250 + 1) * 250


class Coach:
    def __init__(self, rides: list[Ride], ftp_set: float = 0.0, weekly_rides: int = 3,
                 weekly_minutes: int = 90, today: date | None = None) -> None:
        self.rides = sorted(rides, key=lambda r: r.start)
        self.ftp_set = ftp_set or 0.0
        self.weekly_rides = max(1, int(weekly_rides))
        self.weekly_minutes = max(10, int(weekly_minutes))
        self.today = today or date.today()

    @classmethod
    def from_settings(cls, log_dir: Path, exclude: Path | None = None, rider_kg: float | None = None,
                      ftp: float | None = None) -> "Coach":
        from . import settings

        s = settings.load()
        kg = rider_kg if rider_kg is not None else s.get("rider_kg", 85.0)
        rides = load_rides(Path(log_dir), kg, exclude=exclude)
        return cls(rides, ftp if ftp is not None else s.get("ftp", 0.0), int(s.get("weekly_rides", 3)),
                   int(s.get("weekly_minutes", 90)))

    # ------------------------------------------------------------------ FTP and load

    def ftp(self) -> tuple[float, str | None]:
        """(watts, 'set' | 'estimate' | None). Estimate = 95% of the best 20 min in the last 120 days."""
        if self.ftp_set:
            return float(self.ftp_set), "set"
        cutoff = self.today - timedelta(days=120)
        best20 = max((r.curve.get(1200, 0.0) for r in self.rides if r.when.date() >= cutoff), default=0.0)
        return (round(best20 * 0.95), "estimate") if best20 else (0.0, None)

    def tss(self, ride: Ride, ftp: float | None = None) -> float | None:
        ftp = ftp or self.ftp()[0]
        if not ftp or not ride.np_w:
            return None
        intensity = ride.np_w / ftp
        return ride.moving_s / 3600 * intensity * intensity * 100

    def load(self) -> tuple[float, float, float] | None:
        """(fitness CTL, fatigue ATL, form TSB) as of today, or None without an FTP or rides."""
        ftp = self.ftp()[0]
        if not ftp or not self.rides:
            return None
        daily: dict = {}
        for r in self.rides:
            t = self.tss(r, ftp) or 0.0
            daily[r.when.date()] = daily.get(r.when.date(), 0.0) + t
        ctl = atl = 0.0
        day = min(daily)
        while day <= self.today:
            t = daily.get(day, 0.0)
            ctl += (t - ctl) / 42
            atl += (t - atl) / 7
            day += timedelta(days=1)
        return ctl, atl, ctl - atl

    def load_series(self, days: int = 90) -> list:
        """[(date, fitness, fatigue, form)] for the last `days` days (empty without an FTP)."""
        ftp = self.ftp()[0]
        if not ftp or not self.rides:
            return []
        daily: dict = {}
        for r in self.rides:
            daily[r.when.date()] = daily.get(r.when.date(), 0.0) + (self.tss(r, ftp) or 0.0)
        out, ctl, atl = [], 0.0, 0.0
        day = min(daily)
        start = self.today - timedelta(days=days)
        while day <= self.today:
            t = daily.get(day, 0.0)
            ctl += (t - ctl) / 42
            atl += (t - atl) / 7
            if day >= start:
                out.append((day, ctl, atl, ctl - atl))
            day += timedelta(days=1)
        return out

    def zones(self, ride: Ride) -> dict | None:
        """Seconds per power zone from the ride's 10 W histogram."""
        ftp = self.ftp()[0]
        if not ftp:
            return None
        out = {name: 0 for _, name in ZONES}
        for b, secs in ride.power_hist.items():
            watts = b * 10 + 5
            for upper, name in ZONES:
                if watts <= upper * ftp:
                    out[name] += secs
                    break
        return out

    # ------------------------------------------------------------------ habit

    def week(self, offset: int = 0) -> dict:
        """Rides, minutes and miles in the Monday-based week `offset` weeks back (0 = this week)."""
        monday = self.today - timedelta(days=self.today.weekday()) - timedelta(weeks=offset)
        sunday = monday + timedelta(days=6)
        rs = [r for r in self.rides if monday <= r.when.date() <= sunday]
        return {"start": monday, "rides": len(rs), "minutes": sum(r.moving_s for r in rs) / 60,
                "miles": sum(r.miles for r in rs), "list": rs}

    def goal_met(self, w: dict) -> bool:
        return w["rides"] >= self.weekly_rides or w["minutes"] >= self.weekly_minutes

    def streak_weeks(self) -> int:
        """Consecutive weeks meeting the goal (rides OR minutes), counting this week once it's met."""
        n = 1 if self.goal_met(self.week(0)) else 0
        offset = 1
        while offset < 520 and self.goal_met(self.week(offset)):
            n += 1
            offset += 1
        return n

    def days_since_last(self) -> int | None:
        return (self.today - self.rides[-1].when.date()).days if self.rides else None

    # ------------------------------------------------------------------ records

    def lifetime(self) -> dict:
        return {"rides": len(self.rides), "seconds": sum(r.moving_s for r in self.rides),
                "miles": sum(r.miles for r in self.rides), "kj": sum(r.work_kj for r in self.rides),
                "since": self.rides[0].when if self.rides else None}

    def records(self, before: Ride | None = None) -> dict:
        """{'curve': {duration: (watts, ride)}, 'longest': ride, 'farthest': ride, 'work': ride}."""
        rides = [r for r in self.rides if before is None or r.start < before.start]
        curve: dict = {}
        for r in rides:
            for d, w in r.curve.items():
                if d not in curve or w > curve[d][0]:
                    curve[d] = (w, r)
        pick = lambda key: max(rides, key=key) if rides else None  # noqa: E731
        return {"curve": curve, "longest": pick(lambda r: r.moving_s), "farthest": pick(lambda r: r.distance_km),
                "work": pick(lambda r: r.work_kj)}

    def new_records(self, ride: Ride) -> list[str]:
        rec = self.records(before=ride)
        if not rec["longest"]:
            return []
        out = []
        for d in (5, 60, 300, 1200, 3600):
            w = ride.curve.get(d)
            old = rec["curve"].get(d, (0.0, None))[0]
            if w and old and w > old * 1.01:
                out.append(f"best {duration_label(d)} power {w:.0f} W (was {old:.0f})")
        if ride.moving_s > rec["longest"].moving_s:
            out.append(f"longest ride {_hms(ride.moving_s)} (was {_hms(rec['longest'].moving_s)})")
        if ride.distance_km > rec["farthest"].distance_km:
            out.append(f"farthest ride {ride.miles:.1f} mi (was {rec['farthest'].miles:.1f})")
        return out

    # ------------------------------------------------------------------ strengths and weak areas

    def profile(self, days: int = 90) -> dict:
        """{duration: best watts / FTP / reference} over the last `days` (1.0 = typical shape)."""
        ftp = self.ftp()[0]
        if not ftp:
            return {}
        cutoff = self.today - timedelta(days=days)
        recent = [r for r in self.rides if r.when.date() >= cutoff]
        out = {}
        for d, ref in REFERENCE.items():
            best = max((r.curve.get(d, 0.0) for r in recent), default=0.0)
            if best:
                out[d] = best / ftp / ref
        return out

    def focus_areas(self) -> list[tuple[str, str]]:
        """[(key, explanation)], most useful first: what to work on, from your rides."""
        out = []
        recent = self.rides[-5:]
        first = self.rides[0].when.date() if self.rides else self.today
        weeks = [w for w in (self.week(i) for i in range(1, 5)) if w["start"] + timedelta(days=6) >= first]
        if weeks and sum(1 for w in weeks if self.goal_met(w)) < (len(weeks) + 1) // 2:
            avg = sum(w["rides"] for w in weeks) / len(weeks)
            out.append(("routine", f"Routine: {avg:.1f} rides a week over the last {len(weeks)} week(s) (goal "
                                   f"{self.weekly_rides}). Short rides count: a 20 min easy spin keeps the habit alive."))
        long_ones = [r for r in self.rides if r.moving_s >= 20 * 60]
        longest = max((r.moving_s for r in self.rides), default=0)
        if self.rides and longest < 45 * 60:
            out.append(("endurance", f"Endurance: longest ride {_hms(longest)}. Build the long ride by about "
                                     "5 min a week, in 15 min blocks with stand-up breaks."))
        fades = [r.second_half_w / r.first_half_w - 1 for r in long_ones[-5:] if r.first_half_w and r.second_half_w]
        if fades and sum(fades) / len(fades) < -0.08:
            out.append(("durability", f"Durability: power drops {-sum(fades) / len(fades) * 100:.0f}% in the "
                                      "second half on average. Start easier; hold steady Z2 longer."))
        cads = [r.avg_cad for r in recent if r.avg_cad]
        if cads and sum(cads) / len(cads) < 75:
            out.append(("cadence", f"Cadence: {sum(cads) / len(cads):.0f} rpm on average. 80-90 rpm spreads the work "
                                   "over more, lighter strokes: easier on legs and knees on long rides."))
        vis = [r.vi for r in recent if r.vi]
        if vis and sum(vis) / len(vis) > 1.10:
            out.append(("pacing", f"Pacing: variability {sum(vis) / len(vis):.2f} (steady is under 1.05). "
                                  "Surges and coasts cost more than steady pressure for the same distance."))
        prof = self.profile()
        if prof:
            weakest = min(prof, key=prof.get)
            if prof[weakest] < 0.85:
                out.append(("profile", f"Power profile: {PROFILE_NAMES[weakest]} is your relatively weakest "
                                       f"({prof[weakest] * 100:.0f}% of a typical rider's shape)."))
        if self.ftp()[1] != "set":
            out.append(("ftp", "No FTP set: the ramp test (about 15 min) measures it, so zones and targets fit you."))
        return out

    def strengths(self) -> list[str]:
        out = []
        prof = self.profile()
        if prof:
            best = max(prof, key=prof.get)
            if prof[best] >= 1.0:
                out.append(f"{PROFILE_NAMES[best]} ({prof[best] * 100:.0f}% of a typical rider's shape)")
        recent = self.rides[-5:]
        cads = [r.avg_cad for r in recent if r.avg_cad]
        if cads and sum(cads) / len(cads) >= 80:
            out.append(f"good cadence ({sum(cads) / len(cads):.0f} rpm)")
        vis = [r.vi for r in recent if r.vi]
        if vis and sum(vis) / len(vis) <= 1.06:
            out.append("steady pacing")
        if self.streak_weeks() >= 2:
            out.append(f"{self.streak_weeks()}-week streak")
        return out

    # ------------------------------------------------------------------ plan

    def long_ride_target_min(self) -> int:
        recent = [r.moving_s for r in self.rides if r.when.date() >= self.today - timedelta(days=28)]
        base = max(recent, default=0) / 60
        return int(max(30, min(180, round(base / 5) * 5 + 5)))  # longest recent + 5 min: 34:53 -> 40

    def suggest(self) -> tuple[str, str]:
        """(workout key, reason) for today."""
        load = self.load()
        if load and load[2] < -25:
            return "easy", "fatigue is high (form %.0f): an easy spin keeps the habit without digging deeper" % load[2]
        ftp_source = self.ftp()[1]
        ramp_recent = any(r.workout == "ramp" and r.when.date() >= self.today - timedelta(days=42) for r in self.rides)
        if ftp_source != "set" and not ramp_recent and len(self.rides) >= 3:
            return "ramp", "no FTP set yet: the ramp test measures it so every target fits you"
        week = self.week(0)
        done = [r.workout for r in week["list"]]
        if "long" not in done and (self.today.weekday() >= 4 or self.weekly_rides - week["rides"] <= 1):
            return "long", f"this week's long ride: {self.long_ride_target_min()} min, in blocks with stand-up breaks"
        keys = [k for k, _ in self.focus_areas()]
        if "cadence" in keys and "cadence" not in done:
            return "cadence", "cadence is a focus area"
        if "pacing" in keys and "tempo" not in done:
            return "tempo", "steady tempo blocks train even pacing"
        if "durability" in keys or "endurance" in keys:
            return "endurance", "aerobic base: steady Z2 builds endurance and durability"
        last = self.rides[-1].workout if self.rides else None
        return ("sweetspot", "a harder session this week raises your threshold") if last != "sweetspot" else \
            ("endurance", "steady base riding after yesterday's harder session")

    # ------------------------------------------------------------------ text

    def brief_text(self) -> str:
        """Short start-of-ride line: week progress, streak, last ride and today's suggestion."""
        from .workouts import title_of

        w = self.week(0)
        since = self.days_since_last()
        last = ("first ride!" if since is None else "last ride today" if since == 0 else
                "last ride yesterday" if since == 1 else f"last ride {since} days ago")
        key, why = self.suggest()
        return (f"week:      {w['rides']}/{self.weekly_rides} rides, {w['minutes']:.0f}/{self.weekly_minutes} min   "
                f"streak {self.streak_weeks()} wk   {last}\n"
                f"suggested: {title_of(key, self)} ({why})")

    def ride_report(self, ride: Ride) -> str:
        """Extra summary lines for one ride: load, zones, efforts, records."""
        lines = []
        ftp, source = self.ftp()
        t = self.tss(ride, ftp)
        if t is not None:
            lines.append(f"Load        TSS {t:.0f}   IF {ride.np_w / ftp:.2f}   (FTP {ftp:.0f} W"
                         f"{' estimated: set yours or do the ramp test' if source == 'estimate' else ''})")
        z = self.zones(ride)
        if z:
            total = sum(z.values()) or 1
            lines.append("Zones       " + "  ".join(f"{k} {v * 100 / total:.0f}%" for k, v in z.items() if v))
        lines.append(f"Efforts     longest steady stretch {_hms(ride.longest_steady_s)}   coasts of 10 s+: {ride.coasts}")
        recs = self.new_records(ride)
        if recs:
            lines.append("Records     NEW " + "; ".join(recs))
        return "\n".join(lines)

    def scoreboard_text(self) -> str:
        if not self.rides:
            return "== Scoreboard ==\nNo rides yet: your first ride starts the board."
        life = self.lifetime()
        w = self.week(0)
        rec = self.records()
        curve = rec["curve"]
        bests = "  ".join(f"{duration_label(d)} {curve[d][0]:.0f} W" for d in (5, 60, 300, 1200, 3600) if d in curve)
        since = self.days_since_last()
        nxt = next_milestone(life["miles"])
        lines = [
            "== Scoreboard ==",
            f"Lifetime    {life['miles']:.1f} mi   {_hours(life['seconds'])}   {life['rides']} rides   "
            f"{life['kj']:.0f} kJ   (since {life['since']:%d %b %Y}; next milestone {nxt} mi)",
            f"This week   {w['rides']}/{self.weekly_rides} rides   {w['minutes']:.0f}/{self.weekly_minutes} min   "
            f"{w['miles']:.1f} mi   streak {self.streak_weeks()} wk   last ride "
            + ("today" if since == 0 else f"{since} d ago"),
            f"Records     {bests}",
            f"            longest {_hms(rec['longest'].moving_s)}   farthest {rec['farthest'].miles:.1f} mi   "
            f"most work {rec['work'].work_kj:.0f} kJ",
        ]
        ftp, source = self.ftp()
        load = self.load()
        if load:
            lines.append(f"Form        fitness {load[0]:.0f}   fatigue {load[1]:.0f}   form {load[2]:+.0f}   "
                         f"(FTP {ftp:.0f} W {'set' if source == 'set' else 'estimated'})")
        good = self.strengths()
        if good:
            lines.append("Strengths   " + ", ".join(good))
        focus = self.focus_areas()[:3]
        for i, (_, text) in enumerate(focus, 1):
            lines.append(("Work on     " if i == 1 else "            ") + f"{i}) {text}")
        from .workouts import title_of

        key, why = self.suggest()
        lines.append(f"Next        {title_of(key, self)}: {why}")
        return "\n".join(lines)
