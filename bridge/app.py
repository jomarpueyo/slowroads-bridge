"""The ride window: one small app for the whole ride, styled after Slow Roads' own menus.

  pythonw -m bridge.app [--book] [any ride option, e.g. --gear 2.5]      (the desktop shortcut runs this)

Start: pick a free ride or a workout and the road feel, then "begin". The window minimizes itself once the game
is in front, shows live numbers if you look at it, and comes back when the ride ends with the summary, the
"how did it feel?" rating, the charts and the coach's notes. --book opens straight to the ride book (latest
ride and charts).

Lightweight on purpose: tkinter from the standard library, no web server, no browser, no network. The ride itself
is the same bridge as `python -m bridge` (bridge/__main__.py run()), on a worker thread; this window only reads its
status once a second and can ask it to end. The tuning recorder (tools/ride_recorder.py) runs hidden.
"""

import ctypes
import math
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

from . import motivation as mo
from .coach import Coach, _hms
from .ridebook import DURATIONS, duration_label
from .ridelog import ACTIVE_RIDE, LOG_DIR

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "slowroads-bridge.ride"
MUTEX_NAME = "Local\\slowroads-bridge-ride"

# Colours from the game's menus (logs/shots-*): slate ground and road, warm cream text and buttons, a dusk sky
# fading to a gold horizon, layered hill silhouettes.
SLATE = "#2f3538"
SLATE_HI = "#3b4347"     # pills, chart backgrounds
SLATE_LINE = "#4a5357"
CREAM = "#f2efe8"
MUTED = "#9aa3a6"
GOLD = "#ffd678"         # the overlay's highlight colour
GOOD = "#8fd19e"
SKY = ((0.0, (126, 196, 232)), (0.55, (236, 196, 132)), (1.0, (232, 150, 74)))
HILLS = ((0.42, "#6b5a45", 3, 11), (0.58, "#4f4a42", 5, 23), (0.74, SLATE, 7, 37))
FEEL_COLOURS = {1: "#8fd19e", 2: "#b9d98a", 3: "#f2d27a", 4: "#f2a66a", 5: "#e8786a"}


def _family(*names: str) -> str:
    have = set(tkfont.families())
    return next((n for n in names if n in have), "TkFixedFont")


def spaced(text: str, gap: int = 1) -> str:
    """Wide letter spacing like the game's titles (H I L L S); Tk has no tracking, so spaces it is."""
    return (" " * gap).join(text)


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _sky_colour(t: float) -> str:
    for (t0, c0), (t1, c1) in zip(SKY, SKY[1:]):
        if t <= t1:
            r, g, b = _mix(c0, c1, (t - t0) / max(1e-9, t1 - t0))
            return f"#{r:02x}{g:02x}{b:02x}"
    r, g, b = SKY[-1][1]
    return f"#{r:02x}{g:02x}{b:02x}"


def _ridge(width: int, base: float, height: float, seed: int, step: int = 8) -> list:
    """A soft hill line: a few sines, the same every time for the same seed."""
    pts = []
    for x in range(-step, width + 2 * step, step):
        u = x / max(1, width)
        y = (math.sin(u * 5.1 + seed) * 0.5 + math.sin(u * 11.3 + seed * 1.7) * 0.3
             + math.sin(u * 23.0 + seed * 0.3) * 0.12)
        pts += [x, base - y * height]
    return pts


# --------------------------------------------------------------------------- widgets

class Pill(tk.Canvas):
    """A rounded button like the game's "begin": cream when primary or selected, slate otherwise."""

    def __init__(self, master, text: str, command=None, primary: bool = False, width: int | None = None,
                 font=None, pad: int = 22, height: int = 38, fill: str | None = None):
        self.font = font
        self.text = text
        w = width or (font.measure(text) + 2 * pad)
        super().__init__(master, width=w, height=height, bg=master["bg"], highlightthickness=0, bd=0,
                         cursor="hand2" if command else "")
        self.command, self.primary, self.selected, self.hover, self.fill = command, primary, False, False, fill
        self.bind("<Configure>", lambda e: self.draw())
        if command:
            self.bind("<Enter>", lambda e: self._hover(True))
            self.bind("<Leave>", lambda e: self._hover(False))
            self.bind("<ButtonRelease-1>", lambda e: self.command())
        self.draw()

    def _hover(self, on: bool) -> None:
        self.hover = on
        self.draw()

    def set(self, text: str | None = None, selected: bool | None = None) -> None:
        if text is not None:
            self.text = text
        if selected is not None:
            self.selected = selected
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        on = self.primary or self.selected
        bg = self.fill or (CREAM if on else (SLATE_LINE if self.hover else SLATE_HI))
        if on and self.hover:
            bg = "#ffffff"
        fg = SLATE if on or self.fill else CREAM
        r = h / 2
        self.create_oval(0, 0, h, h, fill=bg, outline=bg)
        self.create_oval(w - h - 1, 0, w - 1, h, fill=bg, outline=bg)
        self.create_rectangle(r, 0, w - r, h, fill=bg, outline=bg)
        self.create_text(w / 2, h / 2, text=self.text, fill=fg, font=self.font)


class Sky(tk.Canvas):
    """The header: dusk gradient, three hill silhouettes and a spaced title, redrawn on resize."""

    def __init__(self, master, fonts, height: int = 170):
        super().__init__(master, height=height, bg=SLATE, highlightthickness=0, bd=0)
        self.fonts, self.title, self.sub = fonts, "", ""
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, title: str, sub: str = "") -> None:
        self.title, self.sub = title, sub
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        w, h = max(1, self.winfo_width()), int(self["height"])
        band = 4
        for y in range(0, h, band):
            self.create_rectangle(0, y, w, y + band, fill=_sky_colour(y / h), outline="")
        family, size = self.fonts["title"][0], self.fonts["title"][1]
        title = self.title.upper()
        # wide spacing like the game; narrower, then smaller, if the title wouldn't fit
        for size_now, gap in ((size, 2), (size, 1), (size, 0), (size * 3 // 4, 0), (size // 2, 0)):
            font = tkfont.Font(family=family, size=size_now)
            text = spaced(title, gap) if gap else title
            if font.measure(text) <= w - 60:
                break
        tw = font.measure(text)
        sx, sy = w / 2 + tw / 2 + 70, h * 0.47  # a low sun right of the title, half behind the far hills
        if sx + 30 < w:
            self.create_oval(sx - 30, sy - 30, sx + 30, sy + 30, fill=_sky_colour(0.62), outline="")
            self.create_oval(sx - 19, sy - 19, sx + 19, sy + 19, fill="#fff1cf", outline="")
        for base, colour, seed, amp in HILLS:
            pts = _ridge(w, h * base + amp, amp, seed)
            self.create_polygon(*pts, w + 20, h + 2, -20, h + 2, fill=colour, outline="", smooth=True)
        self.create_rectangle(0, h * 0.86, w, h + 2, fill=SLATE, outline="")
        self.create_text(w / 2 + 1, h * 0.36 + 1, text=text, font=font, fill="#5a4a3a")
        self.create_text(w / 2, h * 0.36, text=text, font=font, fill=CREAM)
        if self.sub:
            self.create_text(w / 2, h * 0.36 + 34, text=self.sub, font=self.fonts["sub"], fill="#fff8ec")


class Scrolled(tk.Frame):
    """A vertically scrolling body (mouse wheel), so the summary fits small screens too."""

    def __init__(self, master):
        super().__init__(master, bg=SLATE)
        self.canvas = tk.Canvas(self, bg=SLATE, highlightthickness=0, bd=0)
        self.inner = tk.Frame(self.canvas, bg=SLATE)
        self.win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.pack(fill="both", expand=True)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.bind_all("<MouseWheel>", self._wheel)

    def _wheel(self, e) -> None:
        if self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-e.delta / 120), "units")


# --------------------------------------------------------------------------- charts (tk canvas)

def curve_chart(parent, fonts, ride, coach, width=420, height=190):
    """This ride's best power for each duration against your all-time best."""
    c = tk.Canvas(parent, width=width, height=height, bg=SLATE_HI, highlightthickness=0, bd=0)
    rec = coach.records()["curve"]
    xs = [d for d in DURATIONS if d in rec or (ride and d in ride.curve)]
    c.create_text(14, 14, text=spaced("POWER CURVE"), anchor="nw", font=fonts["label"], fill=MUTED)
    if len(xs) < 2:
        c.create_text(width / 2, height / 2, text="no power curve yet", font=fonts["small"], fill=MUTED)
        return c
    top = max([v for v, _ in rec.values()] + ([max(ride.curve.values())] if ride and ride.curve else [])) * 1.12
    pl, pr, pt, pb = 44, 14, 36, 26
    lx = lambda d: pl + (math.log(d) - math.log(xs[0])) / (math.log(xs[-1]) - math.log(xs[0])) * (width - pl - pr)  # noqa: E731
    ly = lambda v: height - pb - v / top * (height - pb - pt)  # noqa: E731
    step = 200 if top > 800 else 100 if top > 300 else 50
    for v in range(step, int(top), step):
        c.create_line(pl, ly(v), width - pr, ly(v), fill=SLATE_LINE)
        c.create_text(pl - 6, ly(v), text=str(v), anchor="e", font=fonts["tiny"], fill=MUTED)
    for d in (5, 60, 300, 1200, 3600):
        if xs[0] <= d <= xs[-1]:
            c.create_text(lx(d), height - 12, text=duration_label(d), font=fonts["tiny"], fill=MUTED)
    best = [(lx(d), ly(rec[d][0])) for d in xs if d in rec]
    if len(best) > 1:
        c.create_line(*[p for xy in best for p in xy], fill=GOLD, width=2, dash=(5, 3), smooth=True)
    if ride and ride.curve:
        mine = [(lx(d), ly(ride.curve[d])) for d in xs if d in ride.curve]
        if len(mine) > 1:
            c.create_line(*[p for xy in mine for p in xy], fill=CREAM, width=2.5, smooth=True)
    c.create_text(width - pr, 14, text="this ride", anchor="ne", font=fonts["tiny"], fill=CREAM)
    c.create_text(width - pr - 70, 14, text="best", anchor="ne", font=fonts["tiny"], fill=GOLD)
    return c


def weeks_chart(parent, fonts, coach, weeks=12, width=420, height=190):
    """Minutes per week against your goal (cream when the week counted)."""
    c = tk.Canvas(parent, width=width, height=height, bg=SLATE_HI, highlightthickness=0, bd=0)
    c.create_text(14, 14, text=spaced("MINUTES PER WEEK"), anchor="nw", font=fonts["label"], fill=MUTED)
    data = [coach.week(i) for i in range(weeks - 1, -1, -1)]
    top = max([d["minutes"] for d in data] + [coach.weekly_minutes]) * 1.15 or 1
    pl, pr, pt, pb = 14, 14, 36, 26
    bw = (width - pl - pr) / weeks
    ly = lambda v: height - pb - v / top * (height - pb - pt)  # noqa: E731
    for i, d in enumerate(data):
        x = pl + i * bw
        colour = CREAM if coach.goal_met(d) else SLATE_LINE
        if d["minutes"] > 0:
            c.create_rectangle(x + 4, ly(d["minutes"]), x + bw - 4, height - pb, fill=colour, outline="")
        if i % 3 == 0 or i == weeks - 1:
            c.create_text(x + bw / 2, height - 12, text=f"{d['start']:%d %b}", font=fonts["tiny"], fill=MUTED)
    gy = ly(coach.weekly_minutes)
    c.create_line(pl, gy, width - pr, gy, fill=GOLD, dash=(4, 4))
    c.create_text(width - pr, 14, text=f"goal {coach.weekly_minutes} min", anchor="ne", font=fonts["tiny"], fill=GOLD)
    return c


def journey_bar(parent, fonts, miles: float, width=860):
    j = mo.journey(miles)
    c = tk.Canvas(parent, width=width, height=58, bg=SLATE, highlightthickness=0, bd=0)
    c.create_text(0, 8, text=spaced(j["route"].upper()), anchor="w", font=fonts["label"], fill=MUTED)
    c.create_text(width, 8, text=f"{j['on_route']:.1f} of {j['length']} mi", anchor="e", font=fonts["small"],
                  fill=CREAM)
    frac = min(1.0, j["on_route"] / j["length"])
    c.create_line(6, 30, width - 6, 30, fill=SLATE_HI, width=10, capstyle="round")
    c.create_line(6, 30, max(8, 6 + (width - 12) * frac), 30, fill=CREAM, width=10, capstyle="round")
    c.create_text(0, 50, text=f"past {j['last']} · {j['next']} in {j['to_next']:.1f} mi", anchor="w",
                  font=fonts["small"], fill=MUTED)
    return c


# --------------------------------------------------------------------------- the app

class RideApp:
    def __init__(self, args, book: bool = False):
        self.args = args
        self.events: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        self.thread = None
        self.riding = False
        self.recorder = None
        self.minimized_for_game = False
        self.last_status: dict = {}
        self.msg_text, self.message_at = None, 0.0
        self.workout = None
        self.gravel = args.gravel if args.gravel is None else bool(args.gravel)

        self.root = tk.Tk()
        self.root.title("Slow Roads Ride")
        self.root.configure(bg=SLATE)
        self.root.geometry("900x860")
        self.root.minsize(760, 560)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.report_callback_exception = self.on_ui_error
        mono, ui = _family("Cascadia Mono", "Consolas", "Lucida Console", "Courier New"), _family("Bahnschrift", "Segoe UI")
        self.f = {"title": (mono, 26), "sub": (mono, 11, "italic"), "big": (mono, 30), "mid": (mono, 17),
                  "body": (mono, 11), "small": (mono, 10), "tiny": (mono, 8), "label": (mono, 8),
                  "button": tkfont.Font(family=mono, size=11), "button_big": tkfont.Font(family=mono, size=13),
                  "ui": (ui, 10)}
        self._icon()
        self.sky = Sky(self.root, self.f)
        self.sky.pack(fill="x")
        self.footer = tk.Frame(self.root, bg=SLATE)
        self.footer.pack(side="bottom", fill="x")
        self.body = None
        self.root.after(100, self.pump)
        self.root.bind("<Return>", lambda e: self._enter())
        self._on_enter = None
        if book:
            self.show_book()
        else:
            self.show_start()

    # ------------------------------------------------------------------ chrome

    def _icon(self) -> None:
        """assets/ride.ico (sunset over hills); drawn here if it's missing."""
        ico = ROOT / "assets" / "ride.ico"
        if sys.platform == "win32" and ico.exists():
            try:
                self.root.iconbitmap(default=str(ico))
                return
            except tk.TclError:
                pass
        n = 32
        img = tk.PhotoImage(width=n, height=n)
        for y in range(n):
            row = []
            for x in range(n):
                hill = n * 0.62 + math.sin(x / n * 5 + 1) * 3
                row.append(SLATE if y > hill else _sky_colour(y / (n * 0.7)))
            img.put("{" + " ".join(row) + "}", to=(0, y))
        self.root.iconphoto(True, img)
        self._icon_img = img

    def fresh_body(self) -> tk.Frame:
        if self.body is not None:
            self.body.destroy()
        for child in self.footer.winfo_children():
            child.destroy()
        for v in mo.FEEL_WORDS:  # keys 1-5 rate only the ride on screen
            self.root.unbind(str(v))
        self._on_enter = None
        self.body = Scrolled(self.root)
        self.body.pack(fill="both", expand=True)
        inner = tk.Frame(self.body.inner, bg=SLATE, padx=36, pady=10)
        inner.pack(fill="both", expand=True)
        return inner

    def label(self, parent, text, font="body", fg=CREAM, **kw):
        return tk.Label(parent, text=text, font=self.f[font], fg=fg, bg=parent["bg"], **kw)

    def _enter(self) -> None:
        if self._on_enter:
            self._on_enter()

    # ------------------------------------------------------------------ start

    def load_coach(self) -> Coach:
        try:
            coach = Coach.from_settings(Path(self.args.log_dir), rider_kg=self.args.rider_kg, ftp=self.args.ftp)
            coach.weekly_rides = max(1, self.args.weekly_rides)
            coach.weekly_minutes = max(10, self.args.weekly_minutes)
            return coach
        except Exception:
            return Coach([], self.args.ftp)

    def show_start(self) -> None:
        from .gamestate import RoadWatcher
        from .workouts import KEYS as WORKOUT_KEYS, title_of

        coach = self.load_coach()
        self.coach = coach
        road = RoadWatcher()
        try:
            road.poll(0.0)
        except Exception:
            pass
        self.detected_road = road.surface
        w = coach.week(0)
        self.sky.set("ride", f"week {w['rides']}/{coach.weekly_rides} · {w['minutes']:.0f}/{coach.weekly_minutes} min"
                             f" · streak {coach.streak_weeks()} wk")
        page = self.fresh_body()
        back = coach.comeback()
        if back:
            self.label(page, f"welcome back after {back} days: any ride this week keeps your streak going",
                       fg=GOLD).pack(anchor="w", pady=(4, 2))
        key, why = coach.suggest() if coach.rides else ("easy", "a gentle first ride")
        self.label(page, spaced("TODAY"), "label", MUTED).pack(anchor="w", pady=(8, 2))
        self.label(page, f"{title_of(key, coach)}: {why}", "body", CREAM, wraplength=820,
                   justify="left").pack(anchor="w")
        plan = mo.plan_line(coach.plan(), coach.today)
        info = [plan] if plan else []
        info.append(coach.challenge_text())
        if coach.rides:
            info.append(mo.journey_line(coach.lifetime()["miles"]))
        for line in info:
            self.label(page, line, "small", MUTED, wraplength=820, justify="left").pack(anchor="w", pady=1)

        self.label(page, spaced("CHOOSE A RIDE"), "label", MUTED).pack(anchor="w", pady=(18, 6))
        grid = tk.Frame(page, bg=SLATE)
        grid.pack(anchor="w")
        options = [(None, "free ride")] + [(k, title_of(k, coach) + ("  ·  suggested" if k == key else ""))
                                           for k in ([key] + [k for k in WORKOUT_KEYS if k != key])]
        self.choice_pills = {}
        for i, (k, text) in enumerate(options):
            p = Pill(grid, text, command=lambda k=k: self.choose(k), font=self.f["button"], width=400, height=34)
            p.grid(row=i // 2, column=i % 2, padx=(0, 12), pady=5, sticky="w")
            self.choice_pills[k] = p
        wanted = self.workout if self.workout is not None else self.args.workout  # ride.bat --workout X
        if wanted == "suggested":
            wanted = key
        self.choose(wanted if wanted in self.choice_pills else None)

        row = tk.Frame(page, bg=SLATE)
        row.pack(anchor="w", pady=(16, 0))
        self.label(row, spaced("ROAD FEEL"), "label", MUTED).pack(side="left", padx=(0, 12))
        self.road_pill = Pill(row, self._road_text(), command=self.toggle_road, font=self.f["button"], width=300,
                              height=32)
        self.road_pill.pack(side="left")

        bottom = tk.Frame(page, bg=SLATE)
        bottom.pack(fill="x", pady=(26, 10))
        Pill(bottom, "begin", command=self.begin, primary=True, font=self.f["button_big"], width=190,
             height=48).pack()
        links = tk.Frame(page, bg=SLATE)
        links.pack(fill="x", pady=(8, 0))
        Pill(links, "ride book", command=self.show_book, font=self.f["button"], height=30).pack(side="left")
        self.label(links, "Enter = begin  ·  in the game: F6/F7 gear · F8 pause · F9 re-sync · F10 overlay",
                   "tiny", MUTED).pack(side="right")
        self._on_enter = self.begin

    def choose(self, key) -> None:
        self.workout = key
        for k, p in self.choice_pills.items():
            p.set(selected=(k == key))

    def _road_text(self) -> str:
        if self.gravel is None:
            return f"auto · {self.detected_road or 'from the game'}"
        return "gravel" if self.gravel else "tarmac"

    def toggle_road(self) -> None:
        self.gravel = True if self.gravel is None else (False if self.gravel else None)
        self.road_pill.set(self._road_text())

    # ------------------------------------------------------------------ riding

    def begin(self) -> None:
        if self.riding:
            return
        self.args.workout, self.args.gravel, self.args.menu = self.workout, self.gravel, False
        self.stop.clear()
        self.riding = True
        self.minimized_for_game = False
        self.last_status, self.msg_text = {}, None
        self.start_recorder()
        self.thread = threading.Thread(target=self._ride, name="ride", daemon=True)
        self.thread.start()
        self.show_riding()

    def start_recorder(self) -> None:
        """tools/ride_recorder.py, hidden (no console window). It follows logs/active-ride.txt and stops itself."""
        script = ROOT / "tools" / "ride_recorder.py"
        if self.args.dry_run or self.args.sim or not script.exists() or sys.platform != "win32":
            return
        try:
            ACTIVE_RIDE.unlink(missing_ok=True)
        except OSError:
            pass
        exe = Path(sys.executable)
        quiet = exe.with_name("pythonw.exe")
        try:
            self.recorder = subprocess.Popen([str(quiet if quiet.exists() else exe), str(script)], cwd=str(ROOT),
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
        except OSError:
            self.recorder = None

    def _ride(self) -> None:
        import asyncio

        from .__main__ import run

        try:
            asyncio.run(run(self.args, hooks=self))
        except SystemExit as exc:  # a bad option (e.g. --trainer xyz): say so, no crash report
            self.events.put(("error", (exc, None)))
        except BaseException as exc:  # noqa: BLE001 - shown in the window and saved as a crash report
            from .crashreport import write_report

            self.events.put(("error", (exc, write_report(exc, "app"))))

    # hooks called from the ride thread: only queue things, the Tk thread does the drawing
    def status(self, snap: dict) -> None:
        self.events.put(("status", snap))

    def message(self, text: str) -> None:
        self.events.put(("message", text))

    def finished(self, result: dict) -> None:
        self.events.put(("finished", result))

    def show_riding(self) -> None:
        from .workouts import title_of

        what = title_of(self.workout, self.coach) if self.workout else "free ride"
        self.sky.set("riding", f"{what} · road {self._road_text()}")
        page = self.fresh_body()
        self.state_label = self.label(page, "waking the trainer…", "mid", CREAM)
        self.state_label.pack(pady=(14, 4))
        self.hint_label = self.label(page, "pedal a few strokes · close the Wahoo app and Zwift", "small", MUTED)
        self.hint_label.pack()
        nums = tk.Frame(page, bg=SLATE)
        nums.pack(pady=(26, 8))
        self.num = {}
        for i, (key, label) in enumerate((("time", "TIME"), ("miles", "MILES"), ("watts", "WATTS"),
                                          ("rpm", "RPM"))):
            cell = tk.Frame(nums, bg=SLATE)
            cell.grid(row=0, column=i, padx=26)
            v = self.label(cell, "--", "big", CREAM)
            v.pack()
            self.label(cell, spaced(label), "label", MUTED).pack()
            self.num[key] = v
        self.workout_label = self.label(page, "", "body", MUTED)
        self.workout_label.pack(pady=(10, 0))
        self.msg_label = self.label(page, "", "body", GOLD, wraplength=780)
        self.msg_label.pack(pady=(14, 0))
        self.end_pill = Pill(page, "end ride", command=self.end_ride, font=self.f["button_big"], width=190, height=46)
        self.end_pill.pack(pady=(30, 6))
        self.label(page, "this window hides while you ride and comes back with your summary", "tiny",
                   MUTED).pack()

    def end_ride(self) -> None:
        if self.riding and not self.stop.is_set():
            self.stop.set()
            self.end_pill.set("finishing…")
            self.state_label.configure(text="finishing the ride…")

    def on_status(self, s: dict) -> None:
        self.last_status = s
        if not hasattr(self, "state_label") or not self.state_label.winfo_exists():
            return
        conn = s.get("conn", "")
        if self.stop.is_set():
            text, hint = "finishing the ride…", ""
        elif not s.get("connected"):
            text, hint = ("waking the trainer…" if conn in ("starting", "scanning") else conn), \
                "pedal a few strokes · close the Wahoo app and Zwift"
        elif s.get("paused"):
            text, hint = "paused", "F8 in the game to resume"
        elif not s.get("focused"):
            text, hint = "starting slow roads…" if s.get("ride_s", 0) < 1 else "click into the game", \
                "speed control on (limit mode) · autosteer · automatic gearbox"
        else:
            text, hint = "riding", conn if conn not in ("connected",) else ""
        self.state_label.configure(text=text)
        self.hint_label.configure(text=hint)
        miles = s.get("miles") or 0.0
        if s.get("units") == "km/h":
            miles *= 1.609344
        self.num["time"].configure(text=_hms(s.get("ride_s") or 0))
        self.num["miles"].configure(text=f"{miles:.2f}")
        self.num["watts"].configure(text="--" if s.get("power") is None else f"{s['power']:.0f}")
        self.num["rpm"].configure(text="--" if s.get("cadence") is None else f"{s['cadence']:.0f}")
        wk = s.get("workout")
        if wk:
            target = wk.get("target")
            left = wk.get("left")
            self.workout_label.configure(text="  ·  ".join(x for x in (
                f"{wk.get('block', '')} ({wk.get('index', '?')}/{wk.get('count', '?')})",
                (f"{_hms(left)} left" if left is not None else ""),
                (f"{target[0]:.0f}-{target[1]:.0f} W" if target else "stand, stretch & drink")) if x))
        if self.msg_text and time.monotonic() - self.message_at > 12:
            self.msg_label.configure(text="")
            self.msg_text = None
        # hide once the game has the screen; the ride runs on without the window
        if s.get("focused") and s.get("connected") and not self.minimized_for_game and not self.args.dry_run:
            self.minimized_for_game = True
            self.root.iconify()

    def on_message(self, text: str) -> None:
        self.msg_text, self.message_at = text, time.monotonic()
        if hasattr(self, "msg_label") and self.msg_label.winfo_exists():
            self.msg_label.configure(text=text.lower())

    def bring_back(self) -> None:
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(1500, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except tk.TclError:
            pass

    def on_finished(self, result: dict) -> None:
        self.riding = False
        self.bring_back()
        ride, coach, summary = result.get("ride"), result.get("coach"), result.get("summary")
        if ride is None or summary is None or summary.moving_s < 60:
            short = summary is None or summary.moving_s < 60
            self.sky.set("ride over", "too short to keep" if short else "test ride")
            page = self.fresh_body()
            text = ("under a minute of riding: nothing to add to your ride book." if short else
                    "scripted test rides (--sim) aren't added to your ride book; the summary is in logs/.")
            self.label(page, text, "body", MUTED).pack(pady=30)
            Pill(page, "back", command=self.show_start, primary=True, font=self.f["button_big"], width=190,
                 height=46).pack()
            self._on_enter = self.show_start
            return
        self.show_summary(ride, coach, summary, result.get("workout"), post_ride=True,
                          ramp_ftp=result.get("ramp_ftp"))

    def on_error(self, exc, path) -> None:
        self.riding = False
        self.bring_back()
        from .crashreport import ISSUES_URL, redact

        page = self.fresh_body()
        if isinstance(exc, SystemExit):  # a message for you, not a bug
            self.sky.set("stopped", "check the ride options")
            self.label(page, str(exc.code), "body", GOLD, wraplength=800, justify="left").pack(anchor="w",
                                                                                                pady=(14, 20))
            Pill(page, "back", command=self.show_start, primary=True, font=self.f["button_big"], width=190,
                 height=46).pack()
            return
        self.sky.set("stopped", "something went wrong · " + type(exc).__name__)
        self.label(page, redact(str(exc))[:300] or type(exc).__name__, "body", GOLD, wraplength=800,
                   justify="left").pack(anchor="w", pady=(14, 8))
        where = f"a crash report was saved to {path}" if path else "the crash report could not be saved"
        self.label(page, where, "small", MUTED, wraplength=800, justify="left").pack(anchor="w")
        self.label(page, "double-click report.bat and attach the zip to a new issue:\n" + ISSUES_URL, "small",
                   MUTED, justify="left").pack(anchor="w", pady=(8, 20))
        Pill(page, "back", command=self.show_start, primary=True, font=self.f["button_big"], width=190,
             height=46).pack()

    def on_ui_error(self, exc_type, exc, tb) -> None:
        from .crashreport import write_report

        path = write_report(exc, "app")
        if self.riding:  # never let a drawing bug end the ride; it's in the crash report
            return
        try:
            self.on_error(exc, path)
        except Exception:
            pass

    def pump(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "status":
                    self.on_status(data)
                elif kind == "message":
                    self.on_message(data)
                elif kind == "finished":
                    self.on_finished(data)
                elif kind == "error":
                    self.on_error(*data)
        except queue.Empty:
            pass
        self.root.after(150, self.pump)

    # ------------------------------------------------------------------ summary and ride book

    def show_book(self) -> None:
        from .summary import summarize_csv

        coach = self.load_coach()
        self.coach = coach
        if not coach.rides:
            self.sky.set("ride book", "no rides yet")
            page = self.fresh_body()
            self.label(page, "your first ride starts the book.", "body", MUTED).pack(pady=30)
            Pill(page, "ride", command=self.show_start, primary=True, font=self.f["button_big"], width=190,
                 height=46).pack()
            return
        ride = coach.rides[-1]
        summary = None
        try:
            summary = summarize_csv(Path(self.args.log_dir) / f"ride-{ride.stamp}.csv", self.args.rider_kg)
        except Exception:
            pass
        self.show_summary(ride, coach, summary, ride.workout, post_ride=False)

    def show_summary(self, ride, coach, summary, workout, post_ride: bool, ramp_ftp=None) -> None:
        from .summary import advice
        from .workouts import title_of

        self.coach = coach
        what = title_of(workout, coach) if workout else "free ride"
        self.sky.set("ride done" if post_ride else "ride book", f"{ride.when:%A %d %B · %H:%M} · {what}")
        page = self.fresh_body()
        if ramp_ftp:
            self.label(page, f"ramp test: FTP {ramp_ftp} W, saved", "mid", GOLD).pack(anchor="w", pady=(4, 4))

        tiles = tk.Frame(page, bg=SLATE)
        tiles.pack(fill="x", pady=(6, 4))
        np_ = f"{ride.np_w:.0f}" if ride.np_w else "--"
        cells = ((_hms(ride.moving_s), "MOVING"), (f"{ride.miles:.2f}", "MILES"), (f"{ride.avg_w:.0f}", "AVG W"),
                 (np_, "NP W"), (f"{ride.work_kj:.0f}", "KJ"), (f"{ride.avg_cad:.0f}", "RPM"))
        for i, (value, label) in enumerate(cells):
            cell = tk.Frame(tiles, bg=SLATE)
            cell.grid(row=0, column=i, sticky="w", padx=(0, 30))
            self.label(cell, value, "big", CREAM).pack(anchor="w")
            self.label(cell, spaced(label), "label", MUTED).pack(anchor="w")
        bests = "   ".join(f"{label} {ride.curve[d]:.0f} W" for d, label in
                           ((5, "5 s"), (60, "1 min"), (300, "5 min"), (1200, "20 min")) if d in ride.curve)
        if bests:
            self.label(page, "best  " + bests, "body", MUTED).pack(anchor="w", pady=(6, 0))
        try:
            recs = coach.new_records(ride)
        except Exception:
            recs = []
        if recs:
            self.label(page, "new  " + " · ".join(recs), "body", GOLD, wraplength=820, justify="left").pack(
                anchor="w", pady=(4, 0))

        if post_ride and ride.moving_s >= 300 and self.args.feel:
            self.feel_row(page, ride.stamp)

        self.root.update_idletasks()
        avail = max(660, self.root.winfo_width() - 2 * 36 - 8)
        half = (avail - 16) // 2
        charts = tk.Frame(page, bg=SLATE)
        charts.pack(fill="x", pady=(18, 6))
        curve_chart(charts, self.f, ride, coach, width=half).grid(row=0, column=0, padx=(0, 16))
        weeks_chart(charts, self.f, coach, width=half).grid(row=0, column=1)
        journey_bar(page, self.f, coach.lifetime()["miles"], width=avail).pack(anchor="w", pady=(10, 6))

        notes = []
        if summary is not None:
            try:
                notes += advice(summary, None, coach.ftp()[0], workout)
            except Exception:
                pass
        notes += [text for _, text in coach.focus_areas()[:2]]
        key, why = coach.suggest()
        self.label(page, spaced("NEXT TIME"), "label", MUTED).pack(anchor="w", pady=(12, 4))
        for n in notes[:4]:
            self.label(page, "·  " + n, "small", CREAM, wraplength=820, justify="left").pack(anchor="w", pady=1)
        self.label(page, f"next ride: {title_of(key, coach)} — {why}", "small", GOLD, wraplength=820,
                   justify="left").pack(anchor="w", pady=(6, 0))
        life, w = coach.lifetime(), coach.week(0)
        self.label(page, f"{life['miles']:.1f} mi lifetime · {life['rides']} rides · this week "
                         f"{w['rides']}/{coach.weekly_rides} rides, {w['minutes']:.0f}/{coach.weekly_minutes} min · "
                         f"streak {coach.streak_weeks()} wk · {coach.challenge_text()}", "tiny", MUTED,
                   wraplength=820, justify="left").pack(anchor="w", pady=(10, 0))

        tk.Frame(self.footer, bg=SLATE_LINE, height=1).pack(fill="x")
        buttons = tk.Frame(self.footer, bg=SLATE)
        buttons.pack(pady=(10, 4))
        Pill(buttons, "ride again" if post_ride else "ride", command=self.show_start, primary=True,
             font=self.f["button_big"], width=170, height=44).pack(side="left", padx=8)
        Pill(buttons, "save picture", command=lambda: self.save_picture(ride), font=self.f["button"],
             height=44).pack(side="left", padx=8)
        Pill(buttons, "close", command=self.on_close, font=self.f["button"], height=44).pack(side="left", padx=8)
        self.saved_label = self.label(self.footer, "", "tiny", MUTED)
        self.saved_label.pack(pady=(0, 6))
        self._on_enter = self.show_start

    def feel_row(self, page, stamp: str) -> None:
        self.label(page, spaced("HOW DID IT FEEL?"), "label", MUTED).pack(anchor="w", pady=(16, 6))
        row = tk.Frame(page, bg=SLATE)
        row.pack(anchor="w")
        current = mo.load_book(Path(self.args.log_dir)).get("feel", {}).get(stamp)
        pills = {}

        def pick(v: int) -> None:
            try:
                mo.set_feel(Path(self.args.log_dir), stamp, v)
            except Exception:
                return
            for k, p in pills.items():
                p.fill = FEEL_COLOURS[k] if k == v else None
                p.draw()

        for v, word in mo.FEEL_WORDS.items():
            p = Pill(row, f"{v} {word}", command=lambda v=v: pick(v), font=self.f["button"], height=32)
            p.pack(side="left", padx=(0, 8))
            pills[v] = p
        self.pick_feel = pick
        if current in pills:
            pills[current].fill = FEEL_COLOURS[current]
            pills[current].draw()
        for v in mo.FEEL_WORDS:
            self.root.bind(str(v), lambda e, v=v: pick(v))

    def save_picture(self, ride) -> None:
        try:
            from .sharecard import render_card

            out = Path(self.args.log_dir) / f"share-{ride.stamp}.png"
            render_card(self.coach, ride).save(out)
            self.saved_label.configure(text=f"saved {out}")
            if sys.platform == "win32":
                import os

                os.startfile(str(out))
        except Exception as e:
            self.saved_label.configure(text=f"couldn't save the picture: {type(e).__name__}")

    # ------------------------------------------------------------------ closing

    def on_close(self) -> None:
        if self.riding:
            from tkinter import messagebox

            if messagebox.askyesno("End the ride?", "End the ride now? Your summary will be shown.",
                                   parent=self.root):
                self.end_ride()
            return
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def single_instance() -> object | None:
    """One ride window at a time (two bridges would fight over the trainer and the game). Returns a handle to
    keep, or None if another window is already open."""
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return handle


def main(argv=None) -> int:
    from .__main__ import parse_args

    argv = list(sys.argv[1:] if argv is None else argv)
    book = "--book" in argv
    argv = [a for a in argv if a != "--book"]
    args = parse_args(argv)
    if sys.platform == "win32":
        try:  # own taskbar entry and icon instead of Python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
    lock = single_instance()
    if lock is None:
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, "Slow Roads Ride is already open.", "Slow Roads Ride", 0x40)
        return 1
    app = RideApp(args, book=book)
    app.run()
    if app.riding:  # window closed mid-ride: let the bridge hand the trainer back
        app.stop.set()
        if app.thread is not None:
            app.thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    from .crashreport import run_main

    sys.exit(run_main(main, "app"))
