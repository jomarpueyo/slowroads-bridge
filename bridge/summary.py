"""Ride summary from the trainer's own data only (power, cadence, speed packets), never the game.

Printed when a ride ends and saved as logs/summary-<stamp>.txt. Re-print any ride with:
  .venv\\Scripts\\python -m bridge.summary [logs\\ride-YYYYMMDD-HHMMSS.csv]   (default: latest ride)
Totals across rides: python -m bridge.summary --week | --month | --all   (simulated and <1 min rides skipped)

Notes on the numbers:
  - Distance and speed are the trainer's reported (flywheel) speed, as a head unit would show.
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
    best: dict = None  # {"5 s": watts, "1 min": ..., "5 min": ...}


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


def summarize_rows(rows) -> RideSummary:
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
    if per_second:
        series = [per_second[s] for s in sorted(per_second)]  # ridden seconds only; pauses are left out
        if len(series) >= 30:
            rolled = [sum(series[i - 30:i]) / 30 for i in range(30, len(series) + 1)]
            r.normalized_power_w = (sum(x ** 4 for x in rolled) / len(rolled)) ** 0.25
        for label, window in (("5 s", 5), ("1 min", 60), ("5 min", 300)):
            best = _rolling_best(series, window)
            if best is not None:
                r.best[label] = best
    return r


def summarize_csv(path: Path) -> RideSummary:
    with open(path, encoding="utf-8", newline="", errors="replace") as f:
        return summarize_rows(csv.DictReader(f))


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
        f"Distance    {r.distance_km:.2f} km   avg {r.avg_speed_kmh:.1f} km/h   max {r.max_speed_kmh:.1f} km/h",
        f"Power       avg {r.avg_power_w:.0f} W   max {r.max_power_w:.0f} W   normalized {np_}",
        f"Best        {best}",
        f"Work        {r.work_kj:.0f} kJ   (~{r.work_kj:.0f} kcal)",
        f"Cadence     avg {r.avg_cadence_rpm:.0f} rpm   max {r.max_cadence_rpm:.0f} rpm",
        f"Packets     {r.packets} ({r.bad_packets} bad)",
    ]
    return "\n".join(lines)


def write_summary(ride_csv: Path) -> tuple[str, Path | None]:
    """Summarize a ride CSV and save it next to it as summary-<stamp>.txt."""
    text = format_summary(summarize_csv(ride_csv))
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


def ride_history(log_dir: Path, since: datetime | None = None, exclude: Path | None = None) -> list:
    """[(start time, RideSummary, path)] of real rides, oldest first."""
    out = []
    for path in sorted(log_dir.glob("ride-*.csv")):
        stamp = _stamp_of(path)
        if stamp is None or (since and stamp < since) or (exclude and path.resolve() == exclude.resolve()):
            continue
        if is_simulated(path):
            continue
        try:
            r = summarize_csv(path)
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
    dist = sum(r.distance_km for _, r, _ in history)
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
        lines.append(f"  {stamp:%a %d %b %H:%M}  {_hms(r.moving_s):>7}  {r.distance_km:5.1f} km  "
                     f"{r.avg_power_w:4.0f} W avg  NP {np_:>3}  {r.work_kj:4.0f} kJ")
    if len(history) > 10:
        lines.append(f"  (and {len(history) - 10} earlier)")
    return "\n".join(lines)


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
