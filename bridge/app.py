"""The ride window: one small app for the whole ride, in the same look as the share card (bridge/theme.py).

  pythonw -m bridge.app [--book] [any ride option, e.g. --gear 2.5]      (the desktop shortcut runs this)

Start: pick a free ride or a workout and the road feel, then "begin". The window minimizes itself once the game
is in front, shows live numbers if you look at it, and comes back when the ride ends with the summary, the
"how did it feel?" rating, the charts and the coach's notes. --book opens straight to the ride book (latest
ride and charts).

Lightweight on purpose: every screen is one Pillow image (drawn by bridge/theme.py, like the share card) shown in
a plain tkinter window, which only handles clicks, keys and scrolling. No web server, no browser, no network. The
ride itself is the same bridge as `python -m bridge` (bridge/__main__.py run()) on a worker thread; this window
reads its status once a second and can ask it to end. The tuning recorder (tools/ride_recorder.py) runs hidden.
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
from .ridelog import ACTIVE_RIDE

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "slowroads-bridge.ride"
MUTEX_NAME = "Local\\slowroads-bridge-ride"
W = 960            # window width (the height can be changed; long pages scroll)
M = 56             # side margin
FOOTER_H = 92


class RideApp:
    def __init__(self, args, book: bool = False):
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
        self.draw_fn = None         # (page) -> content height; redrawn on changes
        self.footer_fn = None       # (page) -> None
        self.actions: dict = {}     # region key -> callable
        self.regions: list = []     # (key, box) on the page, in page pixels
        self.footer_regions: list = []
        self.notice = ""            # one line in the footer (e.g. "saved ...")
        self.texts: list = []       # what the screen says (for tests)
        self.feel_value = None
        self.pick_feel = lambda v: None

        self.root = tk.Tk()
        self.root.title("Slow Roads Ride")
        self.root.configure(bg="#%02x%02x%02x" % theme.TOP)
        self.root.geometry(f"{W}x820")
        self.root.minsize(W, 560)
        self.root.resizable(False, True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.report_callback_exception = self.on_ui_error
        ico = ROOT / "assets" / "ride.ico"
        if sys.platform == "win32" and ico.exists():
            try:
                self.root.iconbitmap(default=str(ico))
            except tk.TclError:
                pass
        bottom = "#%02x%02x%02x" % theme.BOTTOM
        self.footer = tk.Canvas(self.root, width=W, height=FOOTER_H, bg=bottom, highlightthickness=0, bd=0)
        self.footer.pack(side="bottom", fill="x")
        self.canvas = tk.Canvas(self.root, width=W, bg=self.root["bg"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        for c, which in ((self.canvas, "page"), (self.footer, "footer")):
            c.bind("<ButtonRelease-1>", lambda e, w=which: self.on_click(w, e))
            c.bind("<Motion>", lambda e, w=which: self.on_motion(w, e))
        self.root.bind("<MouseWheel>", self.on_wheel)
        self.root.bind("<Return>", lambda e: self.act("enter"))
        self.root.bind("<Escape>", lambda e: self.act("escape"))
        for v in mo.FEEL_WORDS:
            self.root.bind(str(v), lambda e, v=v: self.act(f"feel:{v}"))
        self._last_h = 0
        self.root.bind("<Configure>", self.on_resize)
        self.root.after(100, self.pump)
        if book:
            self.show_book()
        else:
            self.show_start()

    # ------------------------------------------------------------------ drawing and input

    def screen(self, name: str, draw_fn, footer_fn, actions: dict) -> None:
        """Put a screen up: draw_fn(page) draws the page and returns its height, footer_fn(page) the buttons."""
        self.view, self.draw_fn, self.footer_fn, self.actions = name, draw_fn, footer_fn, actions
        self.notice = ""
        self.canvas.yview_moveto(0)
        self.redraw()

    def redraw(self) -> None:
        from PIL import ImageTk

        t = self.t
        view_h = max(400, self.canvas.winfo_height() if self.canvas.winfo_height() > 1 else 820 - FOOTER_H)
        page = t.Page(W, 2400, span=view_h)
        h = int(max(view_h, min(2400, self.draw_fn(page) + 30)))
        img = page.finish().crop((0, 0, W, h))
        self.regions, self.texts = page.regions, page.texts
        self._page_img = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._page_img, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, W, h))
        foot = t.Page(W, FOOTER_H, background=t.gradient(W, FOOTER_H, t.BOTTOM, t.mix(t.BOTTOM, (0, 0, 0), 0.25)))
        foot.line([(0, 0.5), (W, 0.5)], fill=(255, 255, 255, 28), width=1)
        if self.footer_fn:
            self.footer_fn(foot)
        if self.notice:
            foot.text(W / 2, FOOTER_H - 12, self.notice, 12, "SemiLight", t.FAINT, anchor="mm")
        self.footer_regions = foot.regions
        self.texts = self.texts + foot.texts
        self._foot_img = ImageTk.PhotoImage(foot.finish())
        self.footer.delete("all")
        self.footer.create_image(0, 0, image=self._foot_img, anchor="nw")

    def _hit(self, where: str, e):
        if where == "page":
            x, y, regions = e.x, self.canvas.canvasy(e.y), self.regions
        else:
            x, y, regions = e.x, e.y, self.footer_regions
        for key, (x0, y0, x1, y1) in reversed(regions):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return key
        return None

    def on_click(self, where: str, e) -> None:
        key = self._hit(where, e)
        if key:
            self.act(key)

    def on_motion(self, where: str, e) -> None:
        widget = self.canvas if where == "page" else self.footer
        widget.configure(cursor="hand2" if self._hit(where, e) else "")

    def on_wheel(self, e) -> None:
        top, bottom = self.canvas.yview()
        if bottom - top < 1.0:
            self.canvas.yview_scroll(int(-e.delta / 120) * 3, "units")

    def on_resize(self, e) -> None:
        if e.widget is self.root and e.height != self._last_h and self.draw_fn:
            self._last_h = e.height
            self.root.after_idle(self.redraw)

    def act(self, key: str) -> None:
        fn = self.actions.get(key)
        if fn is None and key.startswith("feel:"):
            return
        if fn is not None:
            fn()

    def click(self, key: str) -> None:
        """For tests: press a button by its key (it must be on screen)."""
        keys = {k for k, _ in self.regions + self.footer_regions}
        if key not in keys:
            raise KeyError(f"{key!r} is not on the {self.view} screen: {sorted(keys)}")
        self.act(key)

    def header(self, pg, left: str, right: str = "slow roads + kickr") -> None:
        pg.caps(M, 40, left, 14)
        pg.caps(W - M, 40, right, 14, self.t.FAINT, anchor="r")

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

        def draw(pg):
            self.header(pg, f"ride  ·  {coach.today:%A %d %B}")
            w, life = coach.week(0), coach.lifetime()
            x = M
            for value, label in ((f"{w['rides']}/{coach.weekly_rides}", "rides this week"),
                                 (f"{w['minutes']:.0f}/{coach.weekly_minutes}", "minutes"),
                                 (str(coach.streak_weeks()), "week streak"),
                                 (f"{life['miles']:.1f}", "lifetime miles")):
                x += pg.stat(x, 80, value, label, 56) + 60
            y = 196
            back = coach.comeback()
            if back:
                y = pg.wrap(M, y, f"Welcome back after {back} days: any ride this week keeps your streak going.",
                            W - 2 * M, 18, fill=t.GOLD) + 8
            pg.caps(M, y, "today", 13)
            pg.text(M, y + 26, title_of(key, coach), 26, "Light")
            y = pg.wrap(M, y + 66, why[:1].upper() + why[1:] + ".", W - 2 * M, 17, fill=t.MUTED)
            plan = mo.plan_line(coach.plan(), coach.today)
            if plan:
                y = pg.wrap(M, y + 2, plan[:1].upper() + plan[1:], W - 2 * M, 15, fill=t.FAINT)

            y += 22
            pg.caps(M, y, "choose a ride", 13)
            y += 26
            col_w, gap = (W - 2 * M - 16) / 2, 16
            for i, k in enumerate(options):
                bx, by = M + (i % 2) * (col_w + gap), y + (i // 2) * 58
                label = "Free ride" if k is None else title_of(k, coach)
                pg.pill(f"ride:{k}", (bx, by, bx + col_w, by + 46), label + ("   ·   suggested" if k == key else ""),
                        "on" if k == self.workout else "ghost", size=17)
            y += ((len(options) + 1) // 2) * 58 + 18
            pg.caps(M, y, "road feel", 13)
            y += 26
            auto = f"Auto · {self.detected_road}" if self.detected_road else "Auto"
            x = M
            for value, label in ((None, auto), (True, "Gravel"), (False, "Tarmac")):
                bw = max(130, pg.width(label, 17) + 50)
                pg.pill(f"road:{value}", (x, y, x + bw, y + 42), label, "on" if self.gravel is value else "ghost")
                x += bw + 12
            y += 42 + 34
            if coach.rides:
                pg.journey(M, y, W - 2 * M, mo.journey(life["miles"]), compact=True)
                y += 92
            pg.caps(M, y, coach.challenge_text(), 12, t.FAINT)
            return y + 20

        def footer(pg):
            pg.pill("begin", (W / 2 - 120, 18, W / 2 + 120, 72), "Begin", "primary", size=20)
            pg.pill("book", (M, 26, M + 150, 64), "Ride book", size=16)
            pg.caps(W - M, 38, "enter = begin", 11, t.FAINT, anchor="r")
            pg.caps(W - M, 56, "f6/f7 gear · f8 pause · f10 overlay", 11, t.FAINT, anchor="r")

        actions = {f"ride:{k}": (lambda k=k: self.choose(k)) for k in options}
        actions.update({f"road:{v}": (lambda v=v: self.set_road(v)) for v in (None, True, False)})
        actions.update({"begin": self.begin, "enter": self.begin, "book": self.show_book})
        self.screen("start", draw, footer, actions)

    def choose(self, key) -> None:
        self.workout = key
        if self.view == "start":
            self.redraw()

    def set_road(self, value) -> None:
        self.gravel = value
        if self.view == "start":
            self.redraw()

    def toggle_road(self) -> None:
        self.set_road(True if self.gravel is None else (False if self.gravel else None))

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
            pg.text(M, 84, title, 40, "Light")
            if hint:
                pg.text(M, 138, hint, 17, "SemiLight", t.MUTED)
            s = self.last_status
            miles = (s.get("miles") or 0.0) * (1.609344 if s.get("units") == "km/h" else 1)
            x = M
            for value, label in ((t.hms(s.get("ride_s") or 0), "time"), (f"{miles:.2f}", "miles"),
                                 ("--" if s.get("power") is None else f"{s['power']:.0f}", "watts"),
                                 ("--" if s.get("cadence") is None else f"{s['cadence']:.0f}", "rpm")):
                x += pg.stat(x, 196, value, label, 72) + 70
            y = 330
            wk = s.get("workout")
            if wk:
                pg.panel((M, y, W - M, y + 96))
                pg.caps(M + 24, y + 20, f"{wk.get('block', '')}  ·  block {wk.get('index', '?')} of "
                                        f"{wk.get('count', '?')}", 12)
                left = wk.get("left")
                pg.text(M + 24, y + 44, f"{t.hms(left)} left" if left is not None else "", 28, "Light")
                target = wk.get("target")
                pg.text(W - M - 24, y + 44, f"{target[0]:.0f}–{target[1]:.0f} W" if target
                        else "stand, stretch & drink", 28, "Light", t.GOLD, anchor="ra")
                y += 120
            if self.msg_text:
                pg.wrap(M, y + 6, self.msg_text[:1] + self.msg_text[1:].lower(), W - 2 * M, 22, fill=t.GOLD)
            return y + 80

        def footer(pg):
            label = "Finishing…" if self.stop.is_set() else "End ride"
            pg.pill("end", (W / 2 - 110, 20, W / 2 + 110, 70), label, size=18)
            pg.caps(M, 40, "this window hides while you ride", 11, t.FAINT)
            pg.caps(M, 58, "and comes back with your summary", 11, t.FAINT)

        self.screen("riding", draw, footer, {"end": self.end_ride})

    def end_ride(self) -> None:
        if self.riding and not self.stop.is_set():
            self.stop.set()
            if self.view == "riding":
                self.redraw()

    def on_status(self, s: dict) -> None:
        self.last_status = s
        if self.msg_text and time.monotonic() - self.message_at > 12:
            self.msg_text = None
        if self.view == "riding":
            self.redraw()
        # hide once the game has the screen; the ride runs on without the window (a dry run has no game)
        if s.get("focused") and s.get("connected") and not self.minimized_for_game and not self.args.dry_run:
            self.minimized_for_game = True
            self.root.iconify()

    def on_message(self, text: str) -> None:
        self.msg_text, self.message_at = text, time.monotonic()
        if self.view == "riding":
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
            pg.text(M, 84, title, 40, "Light")
            y = pg.wrap(M, 150, text, W - 2 * M, 19, fill=t.GOLD if header == "stopped" else t.MUTED)
            if extra:
                y = pg.wrap(M, y + 14, extra, W - 2 * M, 15, fill=t.FAINT)
            return y

        def footer(pg):
            pg.pill("back", (W / 2 - 110, 20, W / 2 + 110, 70), button, "primary", size=19)

        self.screen("message", draw, footer, {"back": self.show_start, "enter": self.show_start})

    def on_error(self, exc, path) -> None:
        from .crashreport import ISSUES_URL, redact

        self.riding = False
        self.bring_back()
        if isinstance(exc, SystemExit):  # a message for you, not a bug
            self.show_message("stopped", "Check the ride options", str(exc.code))
            return
        where = f"A crash report was saved to {path}." if path else "The crash report could not be saved."
        self.show_message("stopped", "Something went wrong", redact(str(exc))[:300] or type(exc).__name__,
                          f"{where} Double-click report.bat and attach the zip to a new issue: {ISSUES_URL}")

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

        coach = self.coach = self.load_coach()
        if not coach.rides:
            self.show_message("ride book", "No rides yet", "Your first ride starts the book.", button="Ride")
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
        nkey, nwhy = coach.suggest()
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
            self.header(pg, f"{'ride done' if post_ride else 'ride book'}  ·  {ride.when:%A %d %B %Y}  ·  {what}")
            x = M
            for value, label in ((t.hms(ride.moving_s), "moving"), (f"{ride.miles:.2f}", "miles"),
                                 (f"{ride.avg_w:.0f}", "avg watts"), (f"{ride.work_kj:.0f}", "kj")):
                x += pg.stat(x, 78, value, label, 68) + 64
            x = M
            row2 = [(f"{ride.np_w:.0f}" if ride.np_w else "--", "np watts"), (f"{ride.avg_cad:.0f}", "cadence")]
            row2 += [(f"{ride.curve[d]:.0f}", f"best {lab}") for d, lab in ((60, "1 min"), (300, "5 min"),
                                                                             (1200, "20 min")) if d in ride.curve]
            for value, label in row2:
                x += pg.stat(x, 200, value, label, 36) + 54
            y = 282
            if ramp_ftp:
                pg.text(M, y, f"Ramp test: FTP {ramp_ftp} W, saved", 22, "Light", t.GOLD)
                y += 36
            if recs:
                y = pg.wrap(M, y, "New: " + "; ".join(recs), W - 2 * M, 20, fill=t.GOLD) + 4
            if ask_feel:
                y += 14
                pg.caps(M, y, "how did it feel?", 13)
                y += 26
                bx = M
                for v, word in mo.FEEL_WORDS.items():
                    label = f"{v}  {word}"
                    bw = pg.width(label, 16) + 44
                    pg.pill(f"feel:{v}", (bx, y, bx + bw, y + 40), label, "on" if self.feel_value == v else "ghost",
                            size=16)
                    bx += bw + 10
                y += 40
            y += 26
            half = (W - 2 * M - 20) / 2
            best = {d: w for d, (w, _) in coach.records()["curve"].items()}
            pg.curve_chart((M, y, M + half, y + 236), ride.curve, best, DURATIONS, duration_label)
            weeks = [coach.week(i) for i in range(11, -1, -1)]
            pg.weeks_chart((M + half + 20, y, W - M, y + 236), weeks, coach.weekly_minutes, coach.goal_met)
            y += 236 + 32
            life = coach.lifetime()
            pg.journey(M, y, W - 2 * M, mo.journey(life["miles"]), compact=True)
            y += 104
            pg.caps(M, y, "next time", 13)
            y += 28
            for n in notes[:4]:
                pg.dot(M + 4, y + 11, 2.5, t.MUTED)
                y = pg.wrap(M + 18, y, n, W - 2 * M - 18, 17) + 4
            y = pg.wrap(M, y + 6, f"Next ride: {title_of(nkey, coach)}, {nwhy}.", W - 2 * M, 17, fill=t.GOLD)
            w = coach.week(0)
            pg.caps(M, y + 16, f"{life['rides']} rides  ·  this week {w['rides']}/{coach.weekly_rides}  ·  "
                               f"streak {coach.streak_weeks()} wk  ·  {coach.challenge_text()}", 11, t.FAINT)
            return y + 40

        def footer(pg):
            pg.pill("again", (W / 2 - 260, 20, W / 2 - 50, 70), "Ride again" if post_ride else "Ride", "primary",
                    size=19)
            pg.pill("picture", (W / 2 - 30, 20, W / 2 + 140, 70), "Save picture", size=17)
            pg.pill("close", (W / 2 + 160, 20, W / 2 + 280, 70), "Close", size=17)

        actions = {"again": self.show_start, "enter": self.show_start, "close": self.on_close,
                   "picture": lambda: self.save_picture(ride)}
        if ask_feel:
            actions.update({f"feel:{v}": (lambda v=v: pick(v)) for v in mo.FEEL_WORDS})
        self.screen("summary" if post_ride else "book", draw, footer, actions)

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
    try:
        app = RideApp(args, book=book)
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
