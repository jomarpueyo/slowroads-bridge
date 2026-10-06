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
    from PIL import Image, ImageDraw

    from .overlay import _font

    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):  # quiet dusk gradient, like the game's sky
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(28 + 30 * t), int(36 + 22 * t), int(58 - 14 * t)))
    big, mid, small = _font(64, "Light"), _font(30, "Light"), _font(20, "SemiLight")
    white, muted, gold = (245, 245, 245), (175, 182, 196), (255, 214, 120)

    def tracked(xy, text, font, fill, tracking=3):
        x, y = xy
        for ch in text:
            d.text((x, y), ch, font=font, fill=fill)
            x += font.getlength(ch) + tracking

    tracked((60, 50), f"RIDE  ·  {ride.when:%A %d %B %Y}".upper(), small, muted)
    stats = [(_hms(ride.moving_s), "MOVING"), (f"{ride.miles:.1f}", "MILES"), (f"{ride.avg_w:.0f}", "AVG WATTS"),
             (f"{ride.work_kj:.0f}", "KJ")]
    x = 60
    for value, label in stats:
        d.text((x, 110), value, font=big, fill=white)
        tracked((x + 2, 190), label, small, muted)
        x += 270
    best = "   ".join(f"{label} {ride.curve[s]:.0f} W" for s, label in ((60, "1 min"), (300, "5 min"), (1200, "20 min"))
                       if s in ride.curve)
    np_ = f"NP {ride.np_w:.0f} W   " if ride.np_w else ""
    d.text((60, 270), f"{np_}{best}   cadence {ride.avg_cad:.0f} rpm", font=mid, fill=white)
    recs = coach.new_records(ride)
    if recs:
        d.text((60, 325), "New: " + "; ".join(recs)[:90], font=mid, fill=gold)
    life = sum(r.miles for r in coach.rides if r.start <= ride.start)
    j = mo.journey(life)
    d.text((60, 430), f"{j['route']}: {j['on_route']:.0f} of {j['length']} mi", font=mid, fill=white)
    bar_w = W - 120
    d.rounded_rectangle((60, 480, 60 + bar_w, 492), radius=6, fill=(70, 78, 98))
    d.rounded_rectangle((60, 480, 60 + max(12, int(bar_w * j["on_route"] / j["length"])), 492), radius=6,
                        fill=(90, 162, 255))
    d.text((60, 505), f"past {j['last']}, next {j['next']}", font=small, fill=muted)
    tracked((60, H - 50), f"STREAK {coach.streak_weeks()} WK  ·  SLOW ROADS + KICKR", small, muted)
    return img


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
