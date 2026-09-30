"""Ride summary from the trainer's own data only (power, cadence, speed packets), never the game.

Printed when a ride ends and saved as logs/summary-<stamp>.txt. Re-print any ride with:
  .venv\\Scripts\\python -m bridge.summary [logs\\ride-YYYYMMDD-HHMMSS.csv]   (default: latest ride)
Totals across rides: python -m bridge.summary --week | --month | --all   (simulated and <1 min rides skipped)

Notes on the numbers:
  - Distance and speed come from your power through the same virtual road bike as the ride and the
    overlay (85 kg rider + bike by default, --rider-kg), which lands within about 1-4% of Zwift's flat
    speeds for 75-100 kg riders (docs/RESEARCH.md section 16). The KICKR's own wheel speed is shown too:
    it reads about 30% lower than Zwift-style distance.
  - Gaps longer than 5 s between packets count as paused, not ridden.
  - Normalized power: 30 s rolling average of 1 s power, 4th-power mean, 4th root (needs >= 30 s).
  - kcal is estimated as roughly equal to mechanical work in kJ (typical ~24% cycling efficiency).
"""

import csv
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

MAX_GAP_S = 5.0
MAX_RIDE_S = 7 * 24 * 3600  # t_s is seconds since the bridge started; anything beyond a week is corrupt
LIMITS = {"power": (0, 3000), "cadence": (0, 250), "speed": (0, 120)}  # drop impossible readings


@dataclass
class RideSummary:
    packets: int = 0
    bad_packets: int = 0
    duration_s: float = 0.0
    moving_s: float = 0.0
    distance_km: float = 0.0
    avg_speed_kmh: float = 0.0
    max_speed_kmh: float = 0.0
    avg_power_w: float = 0.0
    max_power_w: float = 0.0
    normalized_power_w: float | None = None
    work_kj: float = 0.0
    avg_cadence_rpm: float = 0.0
    max_cadence_rpm: float = 0.0
    best: dict = None  # {"5 s": watts, "1 min": ..., "5 min": ..., "20 min": ...}
    virtual_km: float = 0.0          # Zwift-like distance from power (the headline distance)
    virtual_avg_kmh: float = 0.0
    virtual_max_kmh: float = 0.0
    first_half_w: float | None = None   # average power over the first / second half of ridden time
    second_half_w: float | None = None
    low_power_share: float = 0.0     # share of ridden seconds under 25 W (coasting)


def _num(value, kind):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    lo, hi = LIMITS[kind]
    return v if lo <= v <= hi and v == v else None


def _rolling_best(series, window):
    if len(series) < window:
        return None
    s = sum(series[:window])
    best = s
    for i in range(window, len(series)):
        s += series[i] - series[i - window]
        best = max(best, s)
    return best / window


def _virtual_ride(samples, rider_kg: float):
    """Replay power through the virtual road bike: (distance km, max km/h)."""
    from .drive import VirtualBike

    bike = VirtualBike(mass_kg=rider_kg)
    bike.substep = 0.25
    dist = vmax = 0.0
    for (t0, p, _, _), (t1, *_ ) in zip(samples, samples[1:]):
        dt = t1 - t0
        if dt <= 0:
            continue
        if dt > MAX_GAP_S:  # paused: the bike stops
            bike.v = 0.0
            continue
        for _ in range(4):
            dist += bike.step(p or 0.0, dt / 4) * (dt / 4) / 3600
        vmax = max(vmax, bike.kmh)
    return dist, vmax


def summarize_rows(rows, rider_kg: float = 85.0) -> RideSummary:
    r = RideSummary(best={})
    samples = []  # (t, power, cadence, speed)
    for row in rows:
        r.packets += 1
        if (row.get("error") or "").strip():
            r.bad_packets += 1
            continue
        try:
            t = float(row.get("t_s", ""))
        except (TypeError, ValueError):
            t = -1.0
        if not 0 <= t <= MAX_RIDE_S:  # also rejects NaN
            r.bad_packets += 1
            continue
        samples.append((t, _num(row.get("power_w"), "power"), _num(row.get("cadence_rpm"), "cadence"),
                        _num(row.get("speed_kmh"), "speed")))
    samples.sort(key=lambda s: s[0])
    if len(samples) < 2:
        return r
    r.duration_s = samples[-1][0] - samples[0][0]
    work = cad_sum = cad_time = 0.0
    per_second = {}
    for (t0, p, c, v), (t1, *_ ) in zip(samples, samples[1:]):
        dt = t1 - t0
        if dt <= 0 or dt > MAX_GAP_S:
            continue
        p, c, v = p or 0.0, c or 0.0, v or 0.0
        if p > 0 or c > 0 or v > 1:
            r.moving_s += dt
        r.distance_km += v * dt / 3600
        work += p * dt
        if c > 0:
            cad_sum += c * dt
            cad_time += dt
        for sec in range(int(t0), int(t1) + 1):  # hold each reading until the next (1 s resolution)
            if t0 <= sec < t1:
                per_second[sec] = p
    valid_p = [s[1] for s in samples if s[1] is not None]
    valid_c = [s[2] for s in samples if s[2] is not None]
    valid_v = [s[3] for s in samples if s[3] is not None]
    r.max_power_w = max(valid_p, default=0.0)
    r.max_cadence_rpm = max(valid_c, default=0.0)
    r.max_speed_kmh = max(valid_v, default=0.0)
    r.work_kj = work / 1000
    if r.moving_s > 0:
        r.avg_power_w = work / r.moving_s
        r.avg_speed_kmh = r.distance_km / (r.moving_s / 3600)
    if cad_time > 0:
        r.avg_cadence_rpm = cad_sum / cad_time
    r.virtual_km, r.virtual_max_kmh = _virtual_ride(samples, rider_kg)
    if r.moving_s > 0:
        r.virtual_avg_kmh = r.virtual_km / (r.moving_s / 3600)
    if per_second:
        series = [per_second[s] for s in sorted(per_second)]  # ridden seconds only; pauses are left out
        half = len(series) // 2
        if half >= 60:
            r.first_half_w = sum(series[:half]) / half
            r.second_half_w = sum(series[half:]) / (len(series) - half)
        r.low_power_share = sum(1 for p in series if p < 25) / len(series)
        if len(series) >= 30:
            rolled = [sum(series[i - 30:i]) / 30 for i in range(30, len(series) + 1)]
            r.normalized_power_w = (sum(x ** 4 for x in rolled) / len(rolled)) ** 0.25
        for label, window in (("5 s", 5), ("1 min", 60), ("5 min", 300), ("20 min", 1200)):
            best = _rolling_best(series, window)
            if best is not None:
                r.best[label] = best
    return r


def summarize_csv(path: Path, rider_kg: float = 85.0) -> RideSummary:
    with open(path, encoding="utf-8", newline="", errors="replace") as f:
        return summarize_rows(csv.DictReader(f), rider_kg)


def _hms(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def format_summary(r: RideSummary, title: str = "Ride summary") -> str:
    if r.packets == 0 or r.duration_s <= 0:
        return f"{title}: no trainer data recorded."
    best = "  ".join(f"{k} {v:.0f} W" for k, v in r.best.items()) or "(ride too short)"
    np_ = f"{r.normalized_power_w:.0f} W" if r.normalized_power_w else "(needs 30 s)"
    lines = [
        f"== {title} (trainer data only) ==",
        f"Time        {_hms(r.duration_s)} total, {_hms(r.moving_s)} moving",
        f"Distance    {r.virtual_km:.2f} km ({r.virtual_km / 1.609344:.2f} mi)   avg {r.virtual_avg_kmh:.1f} km/h   "
        f"max {r.virtual_max_kmh:.1f} km/h",
        f"            virtual road bike, Zwift-like; KICKR wheel speed reads {r.distance_km:.2f} km",
        f"Power       avg {r.avg_power_w:.0f} W   max {r.max_power_w:.0f} W   normalized {np_}",
        f"Best        {best}",
        f"Work        {r.work_kj:.0f} kJ   (~{r.work_kj:.0f} kcal)",
        f"Cadence     avg {r.avg_cadence_rpm:.0f} rpm   max {r.max_cadence_rpm:.0f} rpm",
        f"Packets     {r.packets} ({r.bad_packets} bad)",
    ]
    return "\n".join(lines)


def advice(r: RideSummary, previous: list | None = None, ftp: float = 0.0) -> list[str]:
    """A few plain suggestions for the next ride, from this ride's trainer data (and earlier rides)."""
    tips = []
    if r.moving_s < 5 * 60:
        return tips
    prev = [p for p in (previous or []) if p.moving_s >= 5 * 60]
    # personal bests first: good news is worth saying
    for label in ("5 s", "1 min", "5 min", "20 min"):
        mine = r.best.get(label)
        before = max((p.best.get(label, 0.0) for p in prev), default=0.0)
        if mine and prev and before and mine > before * 1.02:
            if len(tips) < 2:  # at most two, so the coaching points still fit
                tips.append(f"New best {label} power: {mine:.0f} W (was {before:.0f} W).")
    if r.avg_cadence_rpm and r.avg_cadence_rpm < 75:
        tips.append(f"Cadence averaged {r.avg_cadence_rpm:.0f} rpm. Try spinning 80-90 rpm at the same watts: "
                    "a lighter push per stroke that you can keep up for longer.")
    if r.normalized_power_w and r.avg_power_w and r.normalized_power_w / r.avg_power_w > 1.10:
        tips.append(f"Surgy pacing: normalized {r.normalized_power_w:.0f} W vs {r.avg_power_w:.0f} W average "
                    f"(variability {r.normalized_power_w / r.avg_power_w:.2f}; steady riding is under 1.05). "
                    "Smoother, steadier pressure costs less for the same distance.")
    if r.low_power_share > 0.15:
        tips.append(f"You were under 25 W for {r.low_power_share * 100:.0f}% of the ride. Short coasts are fine; "
                    "for fitness, keep a light pressure on the pedals between efforts.")
    if r.first_half_w and r.second_half_w:
        change = r.second_half_w / r.first_half_w - 1
        if change < -0.10:
            tips.append(f"Power faded {-change * 100:.0f}% in the second half ({r.first_half_w:.0f} -> "
                        f"{r.second_half_w:.0f} W). Start a little easier and finish stronger.")
        elif change > 0.05:
            tips.append(f"Negative split: second half {change * 100:.0f}% stronger ({r.first_half_w:.0f} -> "
                        f"{r.second_half_w:.0f} W). Well paced.")
    if prev:
        longest = max(p.moving_s for p in prev)
        if r.moving_s > longest:
            tips.append(f"Longest ride so far ({_hms(r.moving_s)} moving).")
        tips = tips[:4]
        target_min = int(round(r.moving_s / 300) * 5 + 5)  # 34:53 -> 40 min
        tips.append(f"Next ride idea: {target_min} min at about {r.avg_power_w * 1.05:.0f} W average "
                    f"(this ride: {_hms(r.moving_s)} at {r.avg_power_w:.0f} W).")
    if not ftp:
        tips.append("Set your FTP (--ftp, or \"ftp\" in settings.json) for zone-based feedback on the overlay.")
    return tips[:6]


def format_advice(tips: list[str]) -> str:
    if not tips:
        return ""
    return "\n".join(["", "", "== For next ride =="] + [f"- {t}" for t in tips])


def write_summary(ride_csv: Path, rider_kg: float = 85.0, previous: list | None = None,
                  ftp: float = 0.0) -> tuple[str, Path | None]:
    """Summarize a ride CSV (with suggestions for next time) and save it as summary-<stamp>.txt."""
    r = summarize_csv(ride_csv, rider_kg)
    text = format_summary(r) + format_advice(advice(r, previous, ftp))
    out = ride_csv.with_name(ride_csv.name.replace("ride-", "summary-", 1)).with_suffix(".txt")
    try:
        out.write_text(text + "\n", encoding="utf-8")
    except OSError:
        out = None
    return text, out


MIN_RIDE_S = 60  # shorter rides (tests, false starts) don't count in totals


def _stamp_of(path: Path) -> datetime | None:
    m = re.search(r"(\d{8})-(\d{6})", path.name)
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") if m else None
    except ValueError:
        return None


def is_simulated(ride_csv: Path) -> bool:
    """True if the bridge log for this ride shows a --sim run (scripted, not pedalled)."""
    log_path = ride_csv.with_name(ride_csv.name.replace("ride-", "bridge-", 1)).with_suffix(".log")
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if " start " in line and " sim=" in line:
                    return " sim=None" not in line
    except OSError:
        pass
    return False


def ride_history(log_dir: Path, since: datetime | None = None, exclude: Path | None = None,
                 rider_kg: float = 85.0) -> list:
    """[(start time, RideSummary, path)] of real rides, oldest first."""
    out = []
    for path in sorted(log_dir.glob("ride-*.csv")):
        stamp = _stamp_of(path)
        if stamp is None or (since and stamp < since) or (exclude and path.resolve() == exclude.resolve()):
            continue
        if is_simulated(path):
            continue
        try:
            r = summarize_csv(path, rider_kg)
        except OSError:
            continue
        if r.moving_s >= MIN_RIDE_S:
            out.append((stamp, r, path))
    return out


def format_totals(history: list, title: str) -> str:
    if not history:
        return f"{title}: no rides."
    moving = sum(r.moving_s for _, r, _ in history)
    work = sum(r.work_kj for _, r, _ in history)
    dist = sum(r.virtual_km or r.distance_km for _, r, _ in history)
    best = {}
    for _, r, _ in history:
        for k, v in r.best.items():
            best[k] = max(best.get(k, 0.0), v)
    lines = [f"== {title} (trainer data only) ==",
             f"Rides       {len(history)}   {_hms(moving)} moving   {dist:.1f} km",
             f"Work        {work:.0f} kJ (~{work:.0f} kcal)   avg power {work * 1000 / moving:.0f} W" if moving else
             f"Work        {work:.0f} kJ",
             "Best        " + ("  ".join(f"{k} {v:.0f} W" for k, v in best.items()) or "-"),
             ""]
    for stamp, r, _ in history[-10:]:
        np_ = f"{r.normalized_power_w:.0f}" if r.normalized_power_w else "--"
        lines.append(f"  {stamp:%a %d %b %H:%M}  {_hms(r.moving_s):>7}  {r.virtual_km or r.distance_km:5.1f} km  "
                     f"{r.avg_power_w:4.0f} W avg  NP {np_:>3}  {r.work_kj:4.0f} kJ")
    if len(history) > 10:
        lines.append(f"  (and {len(history) - 10} earlier)")
    return "\n".join(lines)


def estimate_ftp(history: list) -> float | None:
    """95% of the best 20 minutes across rides (a standard 20-minute-test estimate), or None."""
    best20 = max((r.best.get("20 min", 0.0) for _, r, _ in history), default=0.0)
    return round(best20 * 0.95) if best20 > 0 else None


def compare_line(current: RideSummary, previous: RideSummary) -> str:
    def d(a, b, fmt):
        return ("+" if a >= b else "-") + fmt.format(abs(a - b))
    return (f"vs last ride: time {d(current.moving_s / 60, previous.moving_s / 60, '{:.0f} min')}, "
            f"avg power {d(current.avg_power_w, previous.avg_power_w, '{:.0f} W')}, "
            f"work {d(current.work_kj, previous.work_kj, '{:.0f} kJ')}")


def main(argv=None) -> int:
    from .ridelog import LOG_DIR

    argv = sys.argv[1:] if argv is None else argv
    periods = {"--week": ("Last 7 days", 7), "--month": ("Last 30 days", 30), "--all": ("All rides", None)}
    if argv and argv[0] in periods:
        title, days = periods[argv[0]]
        since = datetime.now() - timedelta(days=days) if days else None
        print(format_totals(ride_history(LOG_DIR, since), title))
        return 0
    if argv:
        path = Path(argv[0])
    else:
        rides = sorted(LOG_DIR.glob("ride-*.csv"))
        if not rides:
            print("no rides in", LOG_DIR)
            return 1
        path = rides[-1]
    print(format_summary(summarize_csv(path), f"Ride summary {path.stem.replace('ride-', '')}"))
    return 0


if __name__ == "__main__":
    from .crashreport import run_main

    sys.exit(run_main(main, "summary"))
