"""Slow Roads Ride: one window for riding, your ride book and problem reports, in the share card's look.

  pythonw -m bridge.app [--book | --report] [any ride option, e.g. --gear 2.5]   (the desktop shortcut runs this)

Tabs at the top: RIDE (pick a free ride or a workout and the road feel, then begin), RIDES (your latest ride,
records and charts) and REPORT (bundle logs for the developer, personal details removed). During a ride the
window minimizes itself once the game is in front and comes back when the ride ends with the summary, the
"how did it feel?" rating, the charts and the coach's notes.

Every screen is one image drawn by bridge/theme.py in a fixed 960 x 800 design frame and scaled to the window,
so nothing ever scrolls and it looks the same at any window size and any Windows display scaling (the window's
thread is per-monitor DPI aware; the ride thread keeps the process default, so the game-window and mouse-wheel
code sees the same coordinates as always). Lightweight on purpose: tkinter + Pillow, no web server, no browser,
no network. The ride is the same bridge as `python -m bridge` (bridge/__main__.py run()) on a worker thread; the
window reads its status once a second and can ask it to end. The tuning recorder runs hidden.
"""

import ctypes
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

from . import motivation as mo
from .coach import Coach
from .units import KM_PER_MILE, hms
from .ridelog import ACTIVE_RIDE

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "slowroads-bridge.ride"
MUTEX_NAME = "Local\\slowroads-bridge-ride"
DW, DH = 960, 800          # the design frame every screen is laid out in
FOOT = 708                 # buttons live in the band from here to DH
M = 56                     # side margin
MIN_SCALE = 0.75           # smallest the frame may be drawn (text stays readable)
TABS = (("ride", "Ride"), ("book", "Rides"), ("report", "Report"))


def dpi_aware_thread() -> float:
    """Make the calling (window) thread per-monitor DPI aware; returns the display scale (1.0 = 100%)."""
    if sys.platform != "win32":
        return 1.0
    user32 = ctypes.windll.user32
    try:
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        return user32.GetDpiForSystem() / 96.0
    except (AttributeError, OSError):
        return 1.0


class RideApp:
    def __init__(self, args, book: bool = False, report: bool = False):
        from PIL import ImageTk  # noqa: F401  (fail early, before a window, if Pillow is missing)

        from . import theme

        self.t = theme
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
        self.coach = None
        self.detected_road = None
        self.view = None            # name of the screen on show
        self.tab = None             # which tab is lit ("ride", "book", "report"), None during a ride
        self.draw_fn = None         # (page) -> None: content above FOOT
        self.footer_fn = None       # (page) -> None: buttons in the band below FOOT
        self.actions: dict = {}     # region key -> callable
        self.regions: list = []     # (key, box) in design coordinates
        self.texts: list = []       # what the screen says (for tests)
        self.page = None            # the last drawn page (scale, offsets)
        self.notice = ""            # one line under the buttons (e.g. "saved ...")
        self.feel_value = None
        self.pick_feel = lambda v: None
        self.report_state: dict = {}

        self.dpi = dpi_aware_thread()
        self.root = tk.Tk()
        self.root.title("Slow Roads Ride")
        self.root.configure(bg="#%02x%02x%02x" % theme.TOP)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        fit = min(self.dpi, 0.92 * sw / DW, 0.85 * sh / DH)  # 100% of the design at this DPI, if the screen allows
        self.req_size = (round(DW * fit), round(DH * fit))
        self.root.geometry("%dx%d" % self.req_size)
        lo = min(MIN_SCALE * self.dpi, fit)
        self.root.minsize(round(DW * lo), round(DH * lo))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.report_callback_exception = self.on_ui_error
        ico = ROOT / "assets" / "ride.ico"
        if sys.platform == "win32" and ico.exists():
            try:
                self.root.iconbitmap(default=str(ico))
            except tk.TclError:
                pass
        self.canvas = tk.Canvas(self.root, bg=self.root["bg"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonRelease-1>", self.on_click)
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<Configure>", self.on_resize)
        self.root.bind("<Return>", lambda e: self.act("enter"))
        self.root.bind("<Escape>", lambda e: self.act("escape"))
        for v in mo.FEEL_WORDS:
            self.root.bind(str(v), lambda e, v=v: self.act(f"feel:{v}"))
        self._size = (0, 0)
        self._pending = None
        self.root.after(100, self.pump)
        if report:
            self.show_report()
        elif book:
            self.show_book()
        else:
            self.show_start()

    # ------------------------------------------------------------------ drawing and input

    def screen(self, name: str, tab, draw_fn, footer_fn, actions: dict) -> None:
        """Put a screen up. draw_fn(page) draws the content (design y < FOOT), footer_fn(page) the buttons."""
        self.view, self.tab, self.draw_fn, self.footer_fn = name, tab, draw_fn, footer_fn
        self.actions = dict(actions)
        if tab is not None and not self.riding:
            self.actions.update({"tab:ride": self.show_start, "tab:book": self.show_book,
                                 "tab:report": self.show_report})
        self.notice = ""
        self.redraw()

    def window_px(self) -> tuple[int, int]:
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        return (w, h) if w >= 50 and h >= 50 else self.req_size  # not on screen yet: the size we asked for

    def render(self, pw: int, ph: int):
        """The current screen as a pw x ph image (also used by tests to try any window size or DPI)."""
        t = self.t
        page = t.Page.fit(pw, ph, DW, DH)
        if self.tab is not None:
            self.draw_tabs(page)
        self.draw_fn(page)
        page.zone, page.oy = "footer", page.slack  # buttons stay at the bottom edge of a tall window
        page.band(FOOT, t.BOTTOM, t.mix(t.BOTTOM, (0, 0, 0), 0.25))
        if self.footer_fn:
            self.footer_fn(page)
        if self.notice:
            page.text(DW / 2, DH - 12, page.fit_text(self.notice, DW - 2 * M, 12), 12, "SemiLight", t.FAINT,
                      anchor="mm")
        return page

    def redraw(self) -> None:
        from PIL import ImageTk

        pw, ph = self.window_px()
        page = self.render(pw, ph)
        self.page, self.regions, self.texts = page, page.regions, page.texts
        self._img = ImageTk.PhotoImage(page.finish())
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._img, anchor="nw")

    def _hit(self, e):
        return self.page.hit(e.x, e.y) if self.page else None

    def on_click(self, e) -> None:
        key = self._hit(e)
        if key:
            self.act(key)

    def on_motion(self, e) -> None:
        self.canvas.configure(cursor="hand2" if self._hit(e) else "")

    def on_resize(self, e) -> None:
        if (e.width, e.height) != self._size and self.draw_fn:
            self._size = (e.width, e.height)
            if self._pending:
                self.root.after_cancel(self._pending)
            self._pending = self.root.after(60, self.redraw)  # once the drag settles

    def act(self, key: str) -> None:
        fn = self.actions.get(key)
        if fn is not None:
            fn()

    def click(self, key: str) -> None:
        """For tests: press a button by its key (it must be on screen)."""
        keys = {k for k, _ in self.regions}
        if key not in keys:
            raise KeyError(f"{key!r} is not on the {self.view} screen: {sorted(keys)}")
        self.act(key)

    def draw_tabs(self, pg) -> None:
        """RIDE · RIDES · REPORT at the top right; the lit one white with a blue line under it."""
        t, x = self.t, DW - M
        for key, label in reversed(TABS):
            w = pg.caps_width(label, 13)
            on = key == self.tab
            pg.caps(x, 34, label, 13, t.WHITE if on else t.FAINT, anchor="r")
            if on:
                pg.rect((x - w, 56, x, 58), fill=t.BLUE, radius=1)
            pg.region(f"tab:{key}", (x - w - 10, 22, x + 10, 64))
            x -= w + 34

    def header(self, pg, left: str) -> None:
        room = DW - 2 * M - (300 if self.tab is not None else 0)  # leave the tabs their space
        while len(left) > 4 and pg.caps_width(left, 13) > room:
            left = left[:-2].rstrip(" ·") + "…"
        pg.caps(M, 34, left, 13)

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

        t = self.t
        coach = self.coach = self.load_coach()
        road = RoadWatcher()
        try:
            road.poll(0.0)
        except Exception:
            pass
        self.detected_road = road.surface
        key, why = coach.suggest() if coach.rides else ("easy", "a gentle first ride to get going")
        options = [None, key] + [k for k in WORKOUT_KEYS if k != key]
        wanted = self.workout if self.workout is not None else self.args.workout  # ride.bat --workout X
        if wanted == "suggested":
            wanted = key
        self.workout = wanted if wanted in options else None
        back = coach.comeback()
        plan = mo.plan_line(coach.plan(), coach.today)

        def draw(pg):
            self.header(pg, f"{coach.today:%A %d %B}")
            w, life = coach.week(0), coach.lifetime()
            x = M
            for value, label in ((f"{w['rides']}/{coach.weekly_rides}", "rides this week"),
                                 (f"{w['minutes']:.0f}/{coach.weekly_minutes}", "minutes"),
                                 (str(coach.streak_weeks()), "week streak"),
                                 (f"{life['miles']:.1f}", "lifetime miles")):
                x += pg.stat(x, 70, value, label, 48) + 56
            pg.caps(M, 160, "today", 12)
            pg.text(M, 180, title_of(key, coach), 24, "Light")
            pg.text(M, 214, pg.fit_text(why[:1].upper() + why[1:], DW - 2 * M, 16), 16, "SemiLight", t.MUTED)
            extra = (f"Welcome back after {back} days: any ride this week keeps your streak going." if back
                     else plan[:1].upper() + plan[1:] if plan else "")
            if extra:  # one optional line: welcome back first, else your ride plan
                pg.text(M, 238, pg.fit_text(extra, DW - 2 * M, 15), 15, "SemiLight", t.GOLD if back else t.FAINT)
            pg.caps(M, 272, "choose a ride", 12)
            col_w, gap = (DW - 2 * M - 16) / 2, 16
            for i, k in enumerate(options):
                bx, by = M + (i % 2) * (col_w + gap), 294 + (i // 2) * 50
                label = "Free ride" if k is None else title_of(k, coach)
                pg.pill(f"ride:{k}", (bx, by, bx + col_w, by + 40), label + ("   ·   suggested" if k == key else ""),
                        "on" if k == self.workout else "ghost", size=16)
            y = 294 + ((len(options) + 1) // 2) * 50 + 8
            pg.caps(M, y, "road feel", 12)
            auto = f"Auto · {self.detected_road}" if self.detected_road else "Auto"
            x = M
            for value, label in ((None, auto), (True, "Gravel"), (False, "Tarmac")):
                bw = max(120, pg.width(label, 16) + 48)
                pg.pill(f"road:{value}", (x, y + 22, x + bw, y + 60), label, "on" if self.gravel is value else "ghost",
                        size=16)
                x += bw + 12
            y += 84
            if coach.rides:
                pg.journey(M, y, DW - 2 * M, mo.journey(life["miles"]), size=20)

        def footer(pg):
            pg.pill("begin", (DW / 2 - 115, FOOT + 22, DW / 2 + 115, FOOT + 70), "Begin", "primary", size=20)
            pg.caps(M, FOOT + 34, pg.fit_text(coach.challenge_text().upper(), 230, 11), 11, t.FAINT, tracking=2)
            pg.caps(M, FOOT + 52, "enter = begin", 11, t.FAINT)
            pg.caps(DW - M, FOOT + 34, "in the game", 11, t.FAINT, anchor="r")
            pg.caps(DW - M, FOOT + 52, "f6/f7 gear · f8 pause · f10 overlay", 11, t.FAINT, anchor="r")

        actions = {f"ride:{k}": (lambda k=k: self.choose(k)) for k in options}
        actions.update({f"road:{v}": (lambda v=v: self.set_road(v)) for v in (None, True, False)})
        actions.update({"begin": self.begin, "enter": self.begin})
        self.screen("start", "ride", draw, footer, actions)

    def choose(self, key) -> None:
        self.workout = key
        if self.view == "start":
            self.redraw()

    def set_road(self, value) -> None:
        self.gravel = value
        if self.view == "start":
            self.redraw()

    def _road_text(self) -> str:
        if self.gravel is None:
            return f"auto ({self.detected_road})" if self.detected_road else "auto"
        return "gravel" if self.gravel else "tarmac"

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

    def riding_state(self) -> tuple[str, str]:
        s = self.last_status
        conn = s.get("conn", "")
        if self.stop.is_set():
            return "Finishing the ride…", "handing the trainer back and writing your summary"
        if not s.get("connected"):
            return ("Waking the trainer…" if conn in ("", "starting", "scanning") else conn[:1].upper() + conn[1:],
                    "pedal a few strokes · close the Wahoo app and Zwift")
        if s.get("paused"):
            return "Paused", "F8 in the game to resume"
        if not s.get("focused"):
            return ("Starting Slow Roads…" if (s.get("ride_s") or 0) < 1 else "Click into the game",
                    "speed control on (limit mode) · autosteer · automatic gearbox")
        return "Riding", ("" if conn == "connected" else conn)

    def show_riding(self) -> None:
        from .workouts import title_of

        t = self.t
        what = title_of(self.workout, self.coach) if self.workout else "free ride"

        def draw(pg):
            self.header(pg, f"riding  ·  {what}  ·  road {self._road_text()}")
            title, hint = self.riding_state()
            pg.text(M, 76, title, 38, "Light")
            if hint:
                pg.text(M, 128, pg.fit_text(hint, DW - 2 * M, 16), 16, "SemiLight", t.MUTED)
            s = self.last_status
            miles = (s.get("miles") or 0.0) * (KM_PER_MILE if s.get("units") == "km/h" else 1)
            x = M
            for value, label in ((hms(s.get("ride_s") or 0), "time"), (f"{miles:.2f}", "miles"),
                                 ("--" if s.get("power") is None else f"{s['power']:.0f}", "watts"),
                                 ("--" if s.get("cadence") is None else f"{s['cadence']:.0f}", "rpm")):
                x += pg.stat(x, 186, value, label, 68) + 64
            y = 316
            wk = s.get("workout")
            if wk:
                pg.panel((M, y, DW - M, y + 92))
                pg.caps(M + 24, y + 18, f"{wk.get('block', '')}  ·  block {wk.get('index', '?')} of "
                                        f"{wk.get('count', '?')}", 12)
                left = wk.get("left")
                pg.text(M + 24, y + 42, f"{hms(left)} left" if left is not None else "", 28, "Light")
                target = wk.get("target")
                pg.text(DW - M - 24, y + 42, f"{target[0]:.0f}–{target[1]:.0f} W" if target
                        else "stand, stretch & drink", 28, "Light", t.GOLD, anchor="ra")
                y += 116
            if self.msg_text:
                pg.wrap(M, y + 6, self.msg_text[:1] + self.msg_text[1:].lower(), DW - 2 * M, 22, fill=t.GOLD,
                        max_lines=2)

        def footer(pg):
            label = "Finishing…" if self.stop.is_set() else "End ride"
            pg.pill("end", (DW / 2 - 110, FOOT + 22, DW / 2 + 110, FOOT + 70), label, size=18)
            pg.caps(M, FOOT + 34, "this window hides while you ride", 11, t.FAINT)
            pg.caps(M, FOOT + 52, "and comes back with your summary", 11, t.FAINT)

        self.screen("riding", None, draw, footer, {"end": self.end_ride})

    def end_ride(self) -> None:
        if self.riding and not self.stop.is_set():
            self.stop.set()
            if self.view == "riding":
                self.redraw()

    def on_status(self, s: dict) -> None:
        self.last_status = s
        if self.msg_text and time.monotonic() - self.message_at > 12:
            self.msg_text = None
        if self.view == "riding" and self.root.state() != "iconic":
            self.redraw()
        # hide once the game has the screen; the ride runs on without the window (a dry run has no game)
        if s.get("focused") and s.get("connected") and not self.minimized_for_game and not self.args.dry_run:
            self.minimized_for_game = True
            self.root.iconify()

    def on_message(self, text: str) -> None:
        self.msg_text, self.message_at = text, time.monotonic()
        if self.view == "riding" and self.root.state() != "iconic":
            self.redraw()

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
            self.show_message("ride over", "Too short to keep" if short else "Test ride",
                              "Under a minute of riding: nothing to add to your ride book." if short else
                              "Scripted test rides (--sim) aren't added to your ride book; the summary is in logs.")
            return
        self.show_summary(ride, coach, summary, result.get("workout"), post_ride=True,
                          ramp_ftp=result.get("ramp_ftp"))

    def show_message(self, header: str, title: str, text: str, extra: str = "", button: str = "Back") -> None:
        t = self.t

        def draw(pg):
            self.header(pg, header)
            pg.text(M, 76, title, 38, "Light")
            y = pg.wrap(M, 140, text, DW - 2 * M, 18, fill=t.GOLD if header == "stopped" else t.MUTED, max_lines=8)
            if extra:
                pg.wrap(M, y + 14, extra, DW - 2 * M, 14, fill=t.FAINT, max_lines=10)

        def footer(pg):
            pg.pill("back", (DW / 2 - 110, FOOT + 22, DW / 2 + 110, FOOT + 70), button, "primary", size=19)

        self.screen("message", "ride", draw, footer, {"back": self.show_start, "enter": self.show_start})

    def on_error(self, exc, path) -> None:
        from .crashreport import ISSUES_URL, redact

        self.riding = False
        self.bring_back()
        if isinstance(exc, SystemExit):  # a message for you, not a bug
            self.show_message("stopped", "Check the ride options", str(exc.code))
            return
        where = f"A crash report was saved to {path}." if path else "The crash report could not be saved."
        self.show_message("stopped", "Something went wrong", redact(str(exc))[:300] or type(exc).__name__,
                          f"{where} The Report tab bundles it with your recent logs; attach the zip to a new "
                          f"issue: {ISSUES_URL}")

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
                elif kind == "report":
                    self.report_state = data
                    if self.view == "report":
                        self.redraw()
        except queue.Empty:
            pass
        self.root.after(150, self.pump)

    # ------------------------------------------------------------------ summary and ride book

    def show_book(self) -> None:
        from .summary import summarize_csv

        coach = self.coach = self.load_coach()
        if not coach.rides:
            def draw(pg):
                self.header(pg, "ride book")
                pg.text(M, 76, "No rides yet", 38, "Light")
                pg.text(M, 140, "Your first ride starts the book.", 18, "SemiLight", self.t.MUTED)

            def footer(pg):
                pg.pill("go", (DW / 2 - 110, FOOT + 22, DW / 2 + 110, FOOT + 70), "Ride", "primary", size=19)

            self.screen("book", "book", draw, footer, {"go": self.show_start, "enter": self.show_start})
            return
        ride = coach.rides[-1]
        summary = None
        try:
            summary = summarize_csv(Path(self.args.log_dir) / f"ride-{ride.stamp}.csv", self.args.rider_kg)
        except Exception:
            pass
        self.show_summary(ride, coach, summary, ride.workout, post_ride=False)

    def show_summary(self, ride, coach, summary, workout, post_ride: bool, ramp_ftp=None) -> None:
        from .ridebook import DURATIONS, duration_label
        from .summary import advice
        from .workouts import title_of

        t = self.t
        self.coach = coach
        what = title_of(workout, coach) if workout else "free ride"
        try:
            recs = coach.new_records(ride)
        except Exception:
            recs = []
        notes = []
        if summary is not None:
            try:
                notes += advice(summary, None, coach.ftp()[0], workout)
            except Exception:
                pass
        notes += [text for _, text in coach.focus_areas()[:2]]
        nkey = coach.suggest()[0]
        ask_feel = post_ride and ride.moving_s >= 300 and self.args.feel
        self.feel_value = mo.load_book(Path(self.args.log_dir)).get("feel", {}).get(ride.stamp)

        def pick(v: int) -> None:
            try:
                mo.set_feel(Path(self.args.log_dir), ride.stamp, v)
            except Exception:
                return
            self.feel_value = v
            self.redraw()

        self.pick_feel = pick

        def draw(pg):
            self.header(pg, f"{'ride done' if post_ride else 'latest ride'}  ·  {ride.when:%a %d %b}  ·  {what}")
            x = M
            for value, label in ((hms(ride.moving_s), "moving"), (f"{ride.miles:.2f}", "miles"),
                                 (f"{ride.avg_w:.0f}", "avg watts"), (f"{ride.work_kj:.0f}", "kj")):
                x += pg.stat(x, 66, value, label, 56) + 58
            x = M
            row2 = [(f"{ride.np_w:.0f}" if ride.np_w else "--", "np watts"), (f"{ride.avg_cad:.0f}", "cadence")]
            row2 += [(f"{ride.curve[d]:.0f}", f"best {lab}") for d, lab in ((60, "1 min"), (300, "5 min"),
                                                                             (1200, "20 min")) if d in ride.curve]
            for value, label in row2:
                x += pg.stat(x, 160, value, label, 30) + 48
            news = ([f"Ramp test: FTP {ramp_ftp} W, saved"] if ramp_ftp else []) + (["New: " + "; ".join(recs)]
                                                                                    if recs else [])
            if news:
                pg.text(M, 222, pg.fit_text("  ·  ".join(news), DW - 2 * M, 17), 17, "SemiLight", t.GOLD)
            y = 256
            if ask_feel:
                pg.caps(M, y, "how did it feel?", 12)
                bx = M
                for v, word in mo.FEEL_WORDS.items():
                    label = f"{v}  {word}"
                    bw = pg.width(label, 15) + 40
                    pg.pill(f"feel:{v}", (bx, y + 20, bx + bw, y + 56), label, "on" if self.feel_value == v else "ghost",
                            size=15)
                    bx += bw + 10
                y += 74
            ch = 512 - y if not ask_feel else 190     # the charts take what's left above the notes
            half = (DW - 2 * M - 20) / 2
            best = {d: w for d, (w, _) in coach.records()["curve"].items()}
            pg.curve_chart((M, y, M + half, y + ch), ride.curve, best, DURATIONS, duration_label)
            weeks = [coach.week(i) for i in range(11, -1, -1)]
            pg.weeks_chart((M + half + 20, y, DW - M, y + ch), weeks, coach.weekly_minutes, coach.goal_met)
            y += ch + 22
            # bottom: notes on the left, journey and totals on the right
            lw = half
            pg.caps(M, y, "next time", 12)
            ny = y + 22
            for n in notes[:2]:
                pg.dot(M + 3, ny + 9, 2.2, t.MUTED)
                ny = pg.wrap(M + 14, ny, n, lw - 14, 14, max_lines=2, line=1.35) + 4
            pg.text(M, ny + 2, pg.fit_text(f"Next ride: {title_of(nkey, coach)}", lw, 15), 15, "SemiLight", t.GOLD)
            rx = M + half + 20
            life, w = coach.lifetime(), coach.week(0)
            pg.journey(rx, y - 2, DW - M - rx, mo.journey(life["miles"]), size=18)
            pg.caps(rx, y + 84, pg.fit_text(f"{life['rides']} rides  ·  week {w['rides']}/{coach.weekly_rides}  ·  "
                                            f"streak {coach.streak_weeks()} wk".upper(), DW - M - rx, 11), 11,
                    t.FAINT, tracking=2)
            pg.caps(rx, y + 102, pg.fit_text(coach.challenge_text().upper(), DW - M - rx, 11), 11, t.FAINT,
                    tracking=2)

        def footer(pg):
            pg.pill("again", (DW / 2 - 260, FOOT + 22, DW / 2 - 50, FOOT + 70), "Ride again" if post_ride else "Ride",
                    "primary", size=19)
            pg.pill("picture", (DW / 2 - 30, FOOT + 22, DW / 2 + 140, FOOT + 70), "Save picture", size=17)
            pg.pill("close", (DW / 2 + 160, FOOT + 22, DW / 2 + 280, FOOT + 70), "Close", size=17)

        actions = {"again": self.show_start, "enter": self.show_start, "close": self.on_close,
                   "picture": lambda: self.save_picture(ride)}
        if ask_feel:
            actions.update({f"feel:{v}": (lambda v=v: pick(v)) for v in mo.FEEL_WORDS})
        self.screen("summary" if post_ride else "book", "ride" if post_ride else "book", draw, footer, actions)

    def save_picture(self, ride) -> None:
        try:
            from .sharecard import render_card

            out = Path(self.args.log_dir) / f"share-{ride.stamp}.png"
            render_card(self.coach, ride).save(out)
            self.notice = f"saved {out}"
            if sys.platform == "win32":
                import os

                os.startfile(str(out))
        except Exception as e:
            self.notice = f"couldn't save the picture: {type(e).__name__}"
        self.redraw()

    # ------------------------------------------------------------------ report

    def show_report(self) -> None:
        """Bundle crash reports and recent logs (personal details removed) into one zip for a GitHub issue."""
        from .crashreport import ISSUES_URL

        t = self.t
        log_dir = Path(self.args.log_dir)
        crashes = sorted(log_dir.glob("crash-*.txt"))
        rides = sorted(log_dir.glob("bridge-*.log"))

        def draw(pg):
            self.header(pg, "problem report")
            st = self.report_state
            pg.text(M, 76, "Report a problem", 38, "Light")
            y = pg.wrap(M, 140, "Bundles your recent crash reports, logs and latest ride data into one zip, with "
                                "personal details removed: Bluetooth addresses, user and computer names, home "
                                "folder and e-mail addresses. No screenshots, game settings or settings.json.",
                        DW - 2 * M, 17, fill=t.MUTED, max_lines=4)
            x = M
            for value, label in ((str(len(crashes)), "crash reports"), (str(len(rides)), "ride logs")):
                x += pg.stat(x, y + 24, value, label, 48) + 70
            y += 120
            if crashes:
                pg.text(M, y, pg.fit_text(f"Latest crash report: {crashes[-1].name}", DW - 2 * M, 15), 15,
                        "SemiLight", t.FAINT)
                y += 30
            if st.get("busy"):
                pg.text(M, y + 10, "Making the report…", 24, "Light", t.WHITE)
            elif st.get("path"):
                pg.text(M, y + 10, "Report saved", 24, "Light", t.GOLD)
                y = pg.wrap(M, y + 48, str(st["path"]), DW - 2 * M, 15, fill=t.MUTED, max_lines=2)
                pg.wrap(M, y + 10, f"Attach it to a new issue: {ISSUES_URL}", DW - 2 * M, 15, fill=t.FAINT,
                        max_lines=2)
            elif st.get("error"):
                pg.wrap(M, y + 10, f"Couldn't make the report: {st['error']}", DW - 2 * M, 17, fill=t.GOLD,
                        max_lines=3)

        def footer(pg):
            st = self.report_state
            if st.get("path"):
                pg.pill("folder", (DW / 2 - 250, FOOT + 22, DW / 2 - 20, FOOT + 70), "Show in folder", "primary",
                        size=18)
                pg.pill("issue", (DW / 2, FOOT + 22, DW / 2 + 250, FOOT + 70), "Open a GitHub issue", size=17)
            else:
                pg.pill("make", (DW / 2 - 120, FOOT + 22, DW / 2 + 120, FOOT + 70),
                        "Working…" if st.get("busy") else "Make report", "primary", size=19)

        self.report_state = {}
        actions = {"make": self.make_report, "enter": self.make_report, "folder": self.show_report_file,
                   "issue": lambda: self._open(ISSUES_URL)}
        self.screen("report", "report", draw, footer, actions)

    def make_report(self) -> None:
        if self.report_state.get("busy") or self.riding:
            return
        self.report_state = {"busy": True}
        self.redraw()

        events, log_dir = self.events, Path(self.args.log_dir)  # the worker never touches the window

        def work():
            try:
                from .report import build_report

                events.put(("report", {"path": build_report(log_dir)}))
            except Exception as e:  # noqa: BLE001
                events.put(("report", {"error": f"{type(e).__name__}: {e}"}))

        threading.Thread(target=work, name="report", daemon=True).start()

    def show_report_file(self) -> None:
        path = self.report_state.get("path")
        if path and sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])

    @staticmethod
    def _open(target: str) -> None:
        if sys.platform == "win32":
            import os

            os.startfile(target)

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
    book, report = "--book" in argv, "--report" in argv
    argv = [a for a in argv if a not in ("--book", "--report")]
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
    try:
        app = RideApp(args, book=book, report=report)
    except ImportError:
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, "The ride window needs Pillow. Run scripts\\setup.ps1 again.",
                                             "Slow Roads Ride", 0x10)
        return 1
    app.run()
    if app.riding:  # window closed mid-ride: let the bridge hand the trainer back
        app.stop.set()
        if app.thread is not None:
            app.thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    from .crashreport import run_main

    sys.exit(run_main(main, "app"))
