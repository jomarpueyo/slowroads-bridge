"""A small image of one ride to send a friend: python -m bridge.sharecard [N]   (N from the ride list; 1 = newest)

Saves logs/share-<stamp>.png (1200 x 630) and opens it. It shows the ride's numbers, your journey and streak,
and nothing that identifies you (no name, no trainer, no location). Trainer data only.
"""

import os
import sys
from pathlib import Path

from . import motivation as mo
from .coach import Coach, _hms

W, H = 1200, 630


def render_card(coach: Coach, ride):
    """1200 x 630, drawn with the shared look (bridge/theme.py) like the ride window."""
    from . import theme as t
    from .ridebook import DURATIONS, duration_label
    from .workouts import title_of

    pg = t.Page(W, H)
    what = title_of(ride.workout, coach) if ride.workout else "free ride"
    pg.caps(60, 48, f"ride  ·  {ride.when:%A %d %B %Y}  ·  {what}", 14)
    pg.caps(W - 60, 48, "slow roads + kickr", 14, t.FAINT, anchor="r")

    x = 60
    for value, label in ((_hms(ride.moving_s), "moving"), (f"{ride.miles:.1f}", "miles"),
                         (f"{ride.avg_w:.0f}", "avg watts")):
        x += pg.stat(x, 92, value, label, 72) + 64
    x = 60
    for value, label in ((f"{ride.np_w:.0f}" if ride.np_w else "--", "np watts"), (f"{ride.work_kj:.0f}", "kj"),
                         (f"{ride.avg_cad:.0f}", "cadence")):
        x += pg.stat(x, 236, value, label, 40) + 70

    best = {d: w for d, (w, _) in coach.records()["curve"].items()}
    pg.curve_chart((760, 84, W - 60, 330), ride.curve, best, DURATIONS, duration_label)

    recs = coach.new_records(ride)
    if recs:
        pg.wrap(60, 366, "New: " + "; ".join(recs), W - 120, 21, "SemiLight", t.GOLD, 1.35)
    else:
        bests = "   ".join(f"{label} {ride.curve[d]:.0f} W" for d, label in
                           ((60, "1 min"), (300, "5 min"), (1200, "20 min")) if d in ride.curve)
        pg.text(60, 366, ("best   " + bests) if bests else "", 21, "SemiLight", t.MUTED)

    life = sum(r.miles for r in coach.rides if r.start <= ride.start)
    pg.journey(60, 440, W - 120, mo.journey(life))
    pg.caps(60, H - 52, f"streak {coach.streak_weeks()} wk  ·  {coach.lifetime()['rides']} rides  ·  "
                        f"{life:.0f} lifetime miles", 13)
    return pg.finish()


def main(argv=None) -> int:
    from .ridebook import _pick
    from .ridelog import LOG_DIR

    argv = sys.argv[1:] if argv is None else argv
    coach = Coach.from_settings(LOG_DIR)
    if not coach.rides:
        print("no rides yet")
        return 1
    ride = _pick(coach.rides, argv[0]) if argv and argv[0].isdigit() else coach.rides[-1]
    out = Path(LOG_DIR) / f"share-{ride.stamp}.png"
    render_card(coach, ride).save(out)
    print(f"share card: {out}")
    if "--no-open" not in argv and sys.platform == "win32":
        os.startfile(str(out))
    return 0


if __name__ == "__main__":
    from .crashreport import run_main

    sys.exit(run_main(main, "sharecard"))
