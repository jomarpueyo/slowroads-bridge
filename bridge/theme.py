"""One look for everything the rider sees outside the game: the ride window, the share card and (in CSS) the
dashboard. Dusk gradient from deep navy to olive, Bahnschrift with light numerals and small spaced capitals,
white for numbers, muted blue-grey for labels, gold for what's new, blue for progress and choices.

Pages are drawn in design coordinates (the ride window's frame is 960 x 800) and scaled to the real pixel size,
so a screen looks the same in any window size and at any Windows display scaling, and never needs scrolling.
Small pages are drawn at 2x and scaled down so text and chart lines come out smooth.
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

FONT_DIR = Path(r"C:\Windows\Fonts")
_font_cache: dict = {}


def font(px: float, weight: str = "Light"):
    """Bahnschrift at a pixel size and weight ("Light", "SemiLight", "Regular"); Segoe UI if it's missing."""
    key = (max(4, round(px)), weight)
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


def gradient(w: int, h: int, top=TOP, bottom=BOTTOM) -> Image.Image:
    col = Image.new("RGB", (1, max(1, h)))
    px = col.load()
    for y in range(h):
        px[0, y] = mix(top, bottom, y / max(1, h - 1))
    return col.resize((max(1, w), max(1, h)))


class Page:
    """A Pillow page in design coordinates. `Page(w, h)` is a w x h image (the share card);
    `Page.fit(pw, ph, dw, dh)` fits a dw x dh design frame, centred, into pw x ph pixels (the ride window)."""

    def __init__(self, w: float, h: float, scale: float = 1.0, ox: float = 0.0, oy: float = 0.0,
                 px: tuple | None = None, ss: int | None = None):
        self.w, self.h = w, h                      # the design frame
        self.scale, self.ox, self.oy = scale, ox, oy
        self.pw, self.ph = px or (round(w * scale), round(h * scale))
        self.ss = ss or (2 if self.pw * self.ph <= 2_400_000 else 1)
        self.k = scale * self.ss                   # design units -> drawing pixels
        self.img = gradient(self.pw * self.ss, self.ph * self.ss)
        self.d = ImageDraw.Draw(self.img, "RGBA")  # RGBA fills blend onto the RGB image
        self.regions: list = []   # (key, (x0, y0, x1, y1)) clickable areas, design coordinates
        self.texts: list = []     # every string drawn (tests read the screen from this)
        self.items: list = []     # (zone, (x0, y0, x1, y1)) of everything drawn, design coordinates
        self.zone = "content"
        self.slack = 0.0          # extra design units below the frame (tall windows); footers move down by it

    @classmethod
    def fit(cls, pw: int, ph: int, dw: float, dh: float) -> "Page":
        """Centred across, pinned to the top; spare height goes above the footer (see `slack`)."""
        scale = min(pw / dw, ph / dh)
        page = cls(dw, dh, scale, (pw / scale - dw) / 2, 0.0, px=(pw, ph))
        page.slack = ph / scale - dh
        return page

    # coordinates
    def P(self, x, y):
        return ((x + self.ox) * self.k, (y + self.oy) * self.k)

    def region(self, key, box):
        """A clickable box, remembered where it is drawn (footers sit lower on tall windows)."""
        self.regions.append((key, (box[0], box[1] + self.oy, box[2], box[3] + self.oy)))

    def hit(self, x_px: float, y_px: float):
        """The key of the clickable box under a window pixel, if any."""
        x, y = x_px / self.scale - self.ox, y_px / self.scale
        for key, (x0, y0, x1, y1) in reversed(self.regions):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return key
        return None

    def _f(self, size, weight):
        return font(size * self.k, weight)

    def _note(self, box):
        self.items.append((self.zone, tuple(box)))

    # ---------------------------------------------------------------- text
    def width(self, s, size=20, weight="SemiLight") -> float:
        return self._f(size, weight).getlength(s) / self.k

    def text(self, x, y, s, size=20, weight="SemiLight", fill=WHITE, anchor="la") -> float:
        f = self._f(size, weight)
        self.texts.append(s)
        self.d.text(self.P(x, y), s, font=f, fill=fill, anchor=anchor)
        l, t, r, b = f.getbbox(s, anchor=anchor)
        self._note((x + l / self.k, y + t / self.k, x + r / self.k, y + b / self.k))
        return f.getlength(s) / self.k

    def _fits(self, s, max_w, size, weight, suffix="") -> int:
        """The longest prefix length n of s with s[:n] + suffix no wider than max_w (binary search)."""
        lo, hi = 0, len(s)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.width(s[:mid] + suffix, size, weight) <= max_w:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def fit_text(self, s, max_w, size=20, weight="SemiLight") -> str:
        """s, shortened with an ellipsis if it's wider than max_w."""
        if self.width(s, size, weight) <= max_w:
            return s
        return s[:self._fits(s, max_w, size, weight, "…")].rstrip(" ,;·") + "…"

    def caps(self, x, y, s, size=13, fill=MUTED, tracking=3.0, weight="SemiLight", anchor="l") -> float:
        """Small spaced capitals (labels and headers). anchor "l", "m" or "r" on x. Returns the width."""
        f = self._f(size, weight)
        s = s.upper()
        self.texts.append(s)
        w = self.caps_width(s, size, tracking, weight)
        x0 = x - w / 2 if anchor == "m" else x - w if anchor == "r" else x
        cx, cy = self.P(x0, y)
        for ch in s:
            self.d.text((cx, cy), ch, font=f, fill=fill, anchor="la")
            cx += f.getlength(ch) + tracking * self.k
        self._note((x0, y, x0 + w, y + size * 1.2))
        return w

    def caps_width(self, s, size=13, tracking=3.0, weight="SemiLight") -> float:
        f = self._f(size, weight)
        return sum(f.getlength(ch) for ch in s.upper()) / self.k + tracking * max(0, len(s) - 1)

    def wrap(self, x, y, s, max_w, size=18, weight="SemiLight", fill=WHITE, line=1.4, max_lines=99) -> float:
        """Word-wrapped text, at most max_lines (the last one ends in … if cut); returns the y below it."""
        words = []
        for word in s.split():  # break words longer than a line (paths, links) at any character
            while self.width(word, size, weight) > max_w:
                n = max(1, self._fits(word, max_w, size, weight))
                words.append(word[:n])
                word = word[n:]
            words.append(word)
        lines, cur = [], ""
        for word in words:
            trial = f"{cur} {word}".strip()
            if cur and self.width(trial, size, weight) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self.fit_text(lines[-1] + " …", max_w, size, weight)
        for ln in lines:
            self.text(x, y, ln, size, weight, fill)
            y += size * line
        return y

    def stat(self, x, y, value, label, size=60, fill=WHITE, label_fill=MUTED) -> float:
        """A big light number with its spaced label underneath (the card's tiles). Returns its width."""
        w = self.text(x, y, value, size, "Light", fill)
        lsize = max(11, size * 0.21)
        self.caps(x + 2, y + size * 1.2, label, lsize, label_fill)
        return max(w, self.caps_width(label, lsize))

    # ---------------------------------------------------------------- shapes
    def rect(self, box, fill=None, outline=None, radius=0, width=1):
        x0, y0 = self.P(box[0], box[1])
        x1, y1 = self.P(box[2], box[3])
        self.d.rounded_rectangle((x0, y0, x1, y1), radius=radius * self.k, fill=fill, outline=outline,
                                 width=max(1, round(width * self.k)))
        self._note(box)

    def band(self, y0, top, bottom):
        """A full-width strip from y0 to the bottom of the window (the footer), edge to edge."""
        top_px = round((y0 + self.oy) * self.k)
        bot_px = self.img.height
        strip = gradient(self.img.width, max(1, bot_px - top_px), top, bottom)
        self.img.paste(strip, (0, top_px))
        self.d.line([(0, top_px), (self.img.width, top_px)], fill=(255, 255, 255, 28), width=max(1, self.ss))

    def panel(self, box, radius=16):
        self.rect(box, fill=(255, 255, 255, 12), radius=radius)

    def line(self, pts, fill=WHITE, width=2.0, dash: float | None = None):
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            self._note((min(xs), min(ys), max(xs), max(ys)))
        pts = [self.P(x, y) for x, y in pts]
        w = max(1, round(width * self.k))
        if not dash:
            self.d.line(pts, fill=fill, width=w, joint="curve")
            return
        step = dash * self.k
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
        x0, y0 = self.P(x - r, y - r)
        x1, y1 = self.P(x + r, y + r)
        self.d.ellipse((x0, y0, x1, y1), fill=fill)

    def bar(self, box, frac: float, fill=BLUE, track=TRACK):
        x0, y0, x1, y1 = box
        r = (y1 - y0) / 2
        self.rect(box, fill=track, radius=r)
        if frac > 0:
            self.rect((x0, y0, x0 + max(y1 - y0, (x1 - x0) * min(1.0, frac)), y1), fill=fill, radius=r)

    def pill(self, key, box, label, style="ghost", size=17):
        """Buttons: "primary" white with navy text, "on" blue (a choice that's selected), "ghost" outlined."""
        x0, y0, x1, y1 = box
        r = (y1 - y0) / 2
        if style == "primary":
            self.rect(box, fill=WHITE, radius=r)
            colour = INK
        elif style == "on":
            self.rect(box, fill=BLUE, radius=r)
            colour = (255, 255, 255)
        else:
            self.rect(box, fill=(255, 255, 255, 10), outline=(255, 255, 255, 70), radius=r, width=1.2)
            colour = WHITE
        label = self.fit_text(label, x1 - x0 - 24, size)
        self.text((x0 + x1) / 2, (y0 + y1) / 2, label, size, "SemiLight" if style != "primary" else "Regular",
                  colour, anchor="mm")
        if key:
            self.region(key, box)

    # ---------------------------------------------------------------- charts
    def curve_chart(self, box, mine: dict, best: dict, durations, label_of, title="POWER CURVE"):
        """This ride (white) against your best (gold, dashed) for each duration, log time axis."""
        x0, y0, x1, y1 = box
        self.panel(box)
        self.caps(x0 + 20, y0 + 16, title, 12)
        self.caps(x1 - 20, y0 + 16, "THIS RIDE", 11, WHITE, anchor="r")
        self.caps(x1 - 20 - self.caps_width("THIS RIDE", 11) - 18, y0 + 16, "BEST", 11, GOLD, anchor="r")
        xs = [d for d in durations if d in best or d in mine]
        if len(xs) < 2:
            self.text((x0 + x1) / 2, (y0 + y1) / 2, "no power curve yet", 16, "SemiLight", FAINT, anchor="mm")
            return
        top = max(list(best.values()) + list(mine.values())) * 1.12
        pl, pr, pt, pb = 50, 20, 46, 30
        lx = lambda d: x0 + pl + (math.log(d) - math.log(xs[0])) / (math.log(xs[-1]) - math.log(xs[0])) * (x1 - x0 - pl - pr)  # noqa: E731
        ly = lambda v: y1 - pb - v / top * (y1 - y0 - pb - pt)  # noqa: E731
        step = 400 if top > 1600 else 200 if top > 800 else 100 if top > 300 else 50
        for v in range(step, int(top), step):
            self.line([(x0 + pl, ly(v)), (x1 - pr, ly(v))], fill=(255, 255, 255, 22), width=1)
            self.text(x0 + pl - 10, ly(v), str(v), 12, "SemiLight", FAINT, anchor="rm")
        for d in (5, 60, 300, 1200, 3600):
            if xs[0] <= d <= xs[-1]:
                self.text(lx(d), y1 - 14, label_of(d), 12, "SemiLight", FAINT, anchor="mm")
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
        self.caps(x0 + 20, y0 + 16, title, 12)
        self.caps(x1 - 20, y0 + 16, f"GOAL {goal:.0f} MIN", 11, GOLD, anchor="r")
        top = max([w["minutes"] for w in weeks] + [goal]) * 1.15 or 1
        pl, pr, pt, pb = 20, 20, 46, 30
        bw = (x1 - x0 - pl - pr) / len(weeks)
        ly = lambda v: y1 - pb - v / top * (y1 - y0 - pb - pt)  # noqa: E731
        for i, w in enumerate(weeks):
            bx = x0 + pl + i * bw
            if w["minutes"] > 0:
                self.rect((bx + 5, ly(w["minutes"]), bx + bw - 5, y1 - pb), fill=WHITE if met(w) else TRACK,
                          radius=3)
            if i % 3 == 0 or i == len(weeks) - 1:
                self.text(bx + bw / 2, y1 - 14, f"{w['start']:%d %b}", 12, "SemiLight", FAINT, anchor="mm")
        self.line([(x0 + pl, ly(goal)), (x1 - pr, ly(goal))], fill=GOLD, width=1.5, dash=5)

    def journey(self, x, y, w, j: dict, size=22):
        """Route name and miles, the progress bar, and the towns either side (the card's journey block).
        It is 80 units tall."""
        lap = f" (lap {j['lap']})" if j.get("lap", 1) > 1 else ""
        self.text(x, y, f"{j['route']}{lap}", size, "Light")
        self.text(x + w, y + 2, f"{j['on_route']:.1f} of {j['length']} mi", size * 0.8, "SemiLight", MUTED,
                  anchor="ra")
        self.bar((x, y + size + 14, x + w, y + size + 26), j["on_route"] / j["length"])
        self.text(x, y + size + 36, self.fit_text(f"past {j['last']}  ·  {j['next']} in {j['to_next']:.1f} mi",
                                                  w, 15), 15, "SemiLight", MUTED)

    def finish(self) -> Image.Image:
        if self.ss == 1:
            return self.img
        return self.img.resize((self.pw, self.ph), Image.LANCZOS)
