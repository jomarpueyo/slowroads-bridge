"""One look for everything the rider sees outside the game: the ride window, the share card and (in CSS) the
dashboard. Dusk gradient from deep navy to olive, Bahnschrift with light numerals and small spaced capitals,
white for numbers, muted blue-grey for labels, gold for what's new, blue for progress and choices.

Pages are drawn with Pillow at 2x and scaled down, so text and chart lines come out smooth. Coordinates passed
to Page are in final (1x) pixels.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# palette
TOP, BOTTOM = (28, 36, 58), (58, 58, 44)       # the dusk gradient (top -> bottom)
WHITE = (245, 245, 245)
MUTED = (175, 182, 196)
FAINT = (120, 128, 146)
GOLD = (255, 214, 120)
BLUE = (90, 162, 255)
TRACK = (70, 78, 98)
INK = TOP                                        # text on white
FEEL = {1: (120, 200, 150), 2: (160, 205, 120), 3: (240, 205, 110), 4: (240, 160, 100), 5: (230, 110, 100)}

SS = 2  # supersampling
FONT_DIR = Path(r"C:\Windows\Fonts")
_font_cache: dict = {}


def font(size: float, weight: str = "Light"):
    """Bahnschrift at a weight ("Light", "SemiLight", "Regular", "SemiBold"); Segoe UI if it's missing."""
    key = (round(size * SS), weight)
    if key not in _font_cache:
        f = None
        for name, variation in (("bahnschrift.ttf", weight),
                                ("segoeuil.ttf" if weight == "Light" else "segoeui.ttf", None)):
            try:
                f = ImageFont.truetype(str(FONT_DIR / name), key[0])
                if variation:
                    try:
                        f.set_variation_by_name(variation)
                    except Exception:
                        pass
                break
            except OSError:
                continue
        _font_cache[key] = f or ImageFont.load_default()
    return _font_cache[key]


def mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(w: int, h: int, top=TOP, bottom=BOTTOM, span: int | None = None) -> Image.Image:
    """Vertical dusk gradient. `span` lets a tall scrolled page keep the same colours per pixel as a short one."""
    span = span or h
    col = Image.new("RGB", (1, h))
    px = col.load()
    for y in range(h):
        px[0, y] = mix(top, bottom, min(1.0, y / max(1, span)))
    return col.resize((w, h))


def hms(seconds: float) -> str:
    s = int(seconds or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class Page:
    """A Pillow canvas in 1x coordinates that renders at SS x and returns a smooth 1x image."""

    def __init__(self, w: int, h: int, background: Image.Image | None = None, span: int | None = None):
        self.w, self.h = w, h
        bg = background or gradient(w, h, span=span)
        self.img = bg.resize((w * SS, h * SS), Image.BILINEAR).convert("RGB")  # RGBA draws blend onto RGB
        self.d = ImageDraw.Draw(self.img, "RGBA")
        self.regions: list = []  # (key, (x0, y0, x1, y1)) clickable areas, 1x
        self.texts: list = []    # every string drawn (tests read the screen from this)

    # ---------------------------------------------------------------- text
    def text(self, x, y, s, size=20, weight="SemiLight", fill=WHITE, anchor="la") -> float:
        f = font(size, weight)
        self.texts.append(s)
        self.d.text((x * SS, y * SS), s, font=f, fill=fill, anchor=anchor)
        return f.getlength(s) / SS

    def width(self, s, size=20, weight="SemiLight") -> float:
        return font(size, weight).getlength(s) / SS

    def caps(self, x, y, s, size=13, fill=MUTED, tracking=3.0, weight="SemiLight", anchor="l") -> float:
        """Small spaced capitals (labels and headers). anchor "l", "m" or "r" on x. Returns the width."""
        f = font(size, weight)
        s = s.upper()
        self.texts.append(s)
        w = sum(f.getlength(ch) for ch in s) / SS + tracking * max(0, len(s) - 1)
        x0 = x - w / 2 if anchor == "m" else x - w if anchor == "r" else x
        cx = x0 * SS
        for ch in s:
            self.d.text((cx, y * SS), ch, font=f, fill=fill, anchor="la")
            cx += f.getlength(ch) + tracking * SS
        return w

    def caps_width(self, s, size=13, tracking=3.0, weight="SemiLight") -> float:
        f = font(size, weight)
        return sum(f.getlength(ch) for ch in s.upper()) / SS + tracking * max(0, len(s) - 1)

    def wrap(self, x, y, s, max_w, size=18, weight="SemiLight", fill=WHITE, line=1.4) -> float:
        """Word-wrapped text; returns the y below it."""
        words, cur = [], ""
        for word in s.split():  # break words longer than a line (paths, links) at any character
            while self.width(word, size, weight) > max_w:
                n = len(word)
                while n > 1 and self.width(word[:n], size, weight) > max_w:
                    n -= 1
                words.append(word[:n])
                word = word[n:]
            words.append(word)
        for word in words:
            trial = f"{cur} {word}".strip()
            if cur and self.width(trial, size, weight) > max_w:
                self.text(x, y, cur, size, weight, fill)
                y += size * line
                cur = word
            else:
                cur = trial
        if cur:
            self.text(x, y, cur, size, weight, fill)
            y += size * line
        return y

    def stat(self, x, y, value, label, size=60, fill=WHITE, label_fill=MUTED) -> float:
        """A big light number with its spaced label underneath (the card's tiles). Returns its width."""
        w = self.text(x, y, value, size, "Light", fill)
        self.caps(x + 2, y + size * 1.2, label, max(11, size * 0.21), label_fill)
        return max(w, self.caps_width(label, max(11, size * 0.21)))

    # ---------------------------------------------------------------- shapes
    def rect(self, box, fill=None, outline=None, radius=0, width=1):
        x0, y0, x1, y1 = (v * SS for v in box)
        self.d.rounded_rectangle((x0, y0, x1, y1), radius=radius * SS, fill=fill, outline=outline,
                                 width=max(1, int(width * SS)))

    def panel(self, box, radius=16):
        self.rect(box, fill=(255, 255, 255, 12), radius=radius)

    def line(self, pts, fill=WHITE, width=2.0, dash: float | None = None):
        pts = [(x * SS, y * SS) for x, y in pts]
        if not dash:
            self.d.line(pts, fill=fill, width=int(width * SS), joint="curve")
            return
        step, w = dash * SS, int(width * SS)
        on, left = True, step  # the dash pattern carries on across corners
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            seg = math.hypot(x1 - x0, y1 - y0)
            t = 0.0
            while t < seg - 1e-6:
                t1 = min(seg, t + left)
                if on:
                    a, b = t / seg, t1 / seg
                    self.d.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a),
                                 (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)], fill=fill, width=w)
                left -= t1 - t
                t = t1
                if left <= 1e-6:
                    on, left = not on, step

    def dot(self, x, y, r, fill):
        self.d.ellipse(((x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS), fill=fill)

    def bar(self, box, frac: float, fill=BLUE, track=TRACK):
        x0, y0, x1, y1 = box
        r = (y1 - y0) / 2
        self.rect(box, fill=track, radius=r)
        if frac > 0:
            self.rect((x0, y0, x0 + max(y1 - y0, (x1 - x0) * min(1.0, frac)), y1), fill=fill, radius=r)

    def pill(self, key, box, label, style="ghost", hover=False, size=17, fill=None):
        """Buttons: "primary" white with navy text, "on" blue (a choice that's selected), "ghost" outlined."""
        x0, y0, x1, y1 = box
        r = (y1 - y0) / 2
        if fill is not None:
            self.rect(box, fill=fill, radius=r)
            colour = INK
        elif style == "primary":
            self.rect(box, fill=(255, 255, 255) if hover else WHITE, radius=r)
            colour = INK
        elif style == "on":
            self.rect(box, fill=mix(BLUE, (255, 255, 255), 0.12) if hover else BLUE, radius=r)
            colour = (255, 255, 255)
        else:
            self.rect(box, fill=(255, 255, 255, 26 if hover else 10), outline=(255, 255, 255, 70), radius=r,
                      width=1.2)
            colour = WHITE
        self.text((x0 + x1) / 2, (y0 + y1) / 2, label, size, "SemiLight" if style != "primary" else "Regular",
                  colour, anchor="mm")
        if key:
            self.regions.append((key, box))

    # ---------------------------------------------------------------- charts
    def curve_chart(self, box, mine: dict, best: dict, durations, label_of, title="POWER CURVE"):
        """This ride (white) against your best (gold, dashed) for each duration, log time axis."""
        x0, y0, x1, y1 = box
        self.panel(box)
        self.caps(x0 + 20, y0 + 18, title, 12)
        self.caps(x1 - 20, y0 + 18, "THIS RIDE", 11, WHITE, anchor="r")
        self.caps(x1 - 20 - self.caps_width("THIS RIDE", 11) - 18, y0 + 18, "BEST", 11, GOLD, anchor="r")
        xs = [d for d in durations if d in best or d in mine]
        if len(xs) < 2:
            self.text((x0 + x1) / 2, (y0 + y1) / 2, "no power curve yet", 16, "SemiLight", FAINT, anchor="mm")
            return
        top = max(list(best.values()) + list(mine.values())) * 1.12
        pl, pr, pt, pb = 52, 20, 52, 34
        lx = lambda d: x0 + pl + (math.log(d) - math.log(xs[0])) / (math.log(xs[-1]) - math.log(xs[0])) * (x1 - x0 - pl - pr)  # noqa: E731
        ly = lambda v: y1 - pb - v / top * (y1 - y0 - pb - pt)  # noqa: E731
        step = 200 if top > 800 else 100 if top > 300 else 50
        for v in range(step, int(top), step):
            self.line([(x0 + pl, ly(v)), (x1 - pr, ly(v))], fill=(255, 255, 255, 22), width=1)
            self.text(x0 + pl - 10, ly(v), str(v), 12, "SemiLight", FAINT, anchor="rm")
        for d in (5, 60, 300, 1200, 3600):
            if xs[0] <= d <= xs[-1]:
                self.text(lx(d), y1 - 16, label_of(d), 12, "SemiLight", FAINT, anchor="mm")
        b = [(lx(d), ly(best[d])) for d in xs if d in best]
        if len(b) > 1:
            self.line(b, fill=GOLD, width=2, dash=5)
        m = [(lx(d), ly(mine[d])) for d in xs if d in mine]
        if len(m) > 1:
            self.line(m, fill=WHITE, width=2.6)
            self.dot(*m[-1], 3.5, WHITE)

    def weeks_chart(self, box, weeks: list, goal: float, met, title="MINUTES PER WEEK"):
        """Minutes per week (white when the week counted) against the goal (gold dashed)."""
        x0, y0, x1, y1 = box
        self.panel(box)
        self.caps(x0 + 20, y0 + 18, title, 12)
        self.caps(x1 - 20, y0 + 18, f"GOAL {goal:.0f} MIN", 11, GOLD, anchor="r")
        top = max([w["minutes"] for w in weeks] + [goal]) * 1.15 or 1
        pl, pr, pt, pb = 20, 20, 52, 34
        bw = (x1 - x0 - pl - pr) / len(weeks)
        ly = lambda v: y1 - pb - v / top * (y1 - y0 - pb - pt)  # noqa: E731
        for i, w in enumerate(weeks):
            bx = x0 + pl + i * bw
            if w["minutes"] > 0:
                self.rect((bx + 5, ly(w["minutes"]), bx + bw - 5, y1 - pb), fill=WHITE if met(w) else TRACK,
                          radius=3)
            if i % 3 == 0 or i == len(weeks) - 1:
                self.text(bx + bw / 2, y1 - 16, f"{w['start']:%d %b}", 12, "SemiLight", FAINT, anchor="mm")
        self.line([(x0 + pl, ly(goal)), (x1 - pr, ly(goal))], fill=GOLD, width=1.5, dash=5)

    def journey(self, x, y, w, j: dict, compact=False):
        """Route name and miles, the progress bar, and the towns either side (the card's journey block)."""
        lap = f" (lap {j['lap']})" if j.get("lap", 1) > 1 else ""
        self.text(x, y, f"{j['route']}{lap}", 22 if not compact else 20, "Light")
        self.text(x + w, y, f"{j['on_route']:.1f} of {j['length']} mi", 18, "SemiLight", MUTED, anchor="ra")
        self.bar((x, y + 40, x + w, y + 52), j["on_route"] / j["length"])
        self.text(x, y + 64, f"past {j['last']}  ·  {j['next']} in {j['to_next']:.1f} mi", 15, "SemiLight", MUTED)

    def finish(self) -> Image.Image:
        return self.img.resize((self.w, self.h), Image.LANCZOS)
