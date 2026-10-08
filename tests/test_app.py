"""The ride window (bridge/app.py) and the hooks it uses in the ride loop (bridge/__main__.py run())."""

import asyncio
import struct
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bridge.__main__ as bm  # noqa: E402


def packet(watts: int, rpm: int = 85, kmh: float = 30.0) -> bytes:
    return struct.pack("<HHHh", 0x0044, int(kmh * 100), rpm * 2, watts)


class Hooks:
    def __init__(self):
        self.stop = threading.Event()
        self.statuses, self.messages, self.results = [], [], []

    def status(self, snap):
        self.statuses.append(snap)

    def message(self, text):
        self.messages.append(text)

    def finished(self, result):
        self.results.append(result)


def _args(tmp_path, *extra):
    return bm.parse_args(["--dry-run", "--log-dir", str(tmp_path), "--no-sounds", "--no-hotkeys", *extra])


def test_hooks_get_status_messages_and_the_result(tmp_path):
    hooks = Hooks()

    async def source(on_packet, on_state):
        on_state("connected")
        for _ in range(75):
            on_packet(packet(180))
            await asyncio.sleep(0.01)

    asyncio.run(bm.run(_args(tmp_path), source_fn=source, hooks=hooks))
    assert len(hooks.results) == 1
    result = hooks.results[0]
    assert result["path"].exists() and result["stamp"] in result["path"].name
    assert result["summary"] is not None and result["text"]
    assert hooks.messages and "THIS WEEK" in hooks.messages[0]          # the greeting
    snap = hooks.statuses[-1]
    assert snap["connected"] is True and snap["power"] == 180 and snap["cadence"] == 85
    assert {"conn", "focused", "paused", "ride_s", "miles", "units", "workout"} <= set(snap)


def test_stop_event_ends_the_ride_and_still_summarises(tmp_path):
    hooks = Hooks()

    async def source(on_packet, on_state):  # would run for ever
        on_state("connected")
        while True:
            on_packet(packet(150))
            await asyncio.sleep(0.01)

    async def ride():
        task = asyncio.ensure_future(bm.run(_args(tmp_path), source_fn=source, hooks=hooks))
        await asyncio.sleep(1.5)
        hooks.stop.set()
        await asyncio.wait_for(task, 10)

    asyncio.run(ride())
    assert len(hooks.results) == 1 and hooks.results[0]["path"].exists()


def test_hooks_skip_console_menu_and_feel_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "choose_workout", lambda *a, **k: pytest.fail("console menu with a window"))
    monkeypatch.setattr(bm, "ask_feel", lambda *a, **k: pytest.fail("console feel prompt with a window"))
    monkeypatch.setattr(bm, "open_dashboard", lambda *a, **k: pytest.fail("browser dashboard with a window"))
    hooks = Hooks()

    async def source(on_packet, on_state):
        on_state("connected")
        for _ in range(30):
            on_packet(packet(200))
            await asyncio.sleep(0.01)

    asyncio.run(bm.run(_args(tmp_path, "--menu"), source_fn=source, hooks=hooks))
    assert hooks.results


# ------------------------------------------------------------------ the window (needs Tk)

def make_app(*argv):
    """A RideApp, or skip without a display. Tcl on Windows sometimes fails to start when one process creates
    many interpreters in a row (\"Can't find a usable init.tcl\"); the app itself makes one, so retry here."""
    tk = pytest.importorskip("tkinter")
    from bridge.app import RideApp

    for attempt in range(5):
        try:
            return RideApp(bm.parse_args(list(argv)))
        except tk.TclError as e:
            error = e
    pytest.skip(f"no display for Tk: {error}")


@pytest.fixture
def app(tmp_path):
    tk = pytest.importorskip("tkinter")
    a = make_app("--dry-run", "--log-dir", str(tmp_path), "--no-sounds", "--no-hotkeys")
    a.root.withdraw()
    yield a
    try:
        a.root.destroy()
    except tk.TclError:
        pass


def screen_text(app) -> str:
    return " | ".join(app.texts)


def keys(app) -> set:
    return {k for k, _ in app.regions + app.footer_regions}


def test_start_screen_choices_and_road_feel(app):
    assert app.view == "start" and {"begin", "book", "ride:None", "road:None", "road:True", "road:False"} <= keys(app)
    assert app.workout is None
    app.click("ride:tempo")
    assert app.workout == "tempo"
    app.click("road:True")
    assert app.gravel is True
    app.click("road:False")
    assert app.gravel is False
    app.click("road:None")
    assert app.gravel is None
    seen = []
    for _ in range(3):
        app.toggle_road()
        seen.append(app.gravel)
    assert seen == [True, False, None]
    assert "TODAY" in app.texts and "CHOOSE A RIDE" in app.texts and "Begin" in app.texts


def test_status_updates_numbers_and_hides_once_the_game_is_in_front(app, monkeypatch):
    app.coach = app.load_coach()
    app.args.dry_run = False      # a dry run has no game to make room for, so it never hides
    app.show_riding()
    assert "Waking the trainer\u2026" in app.texts
    hidden = []
    monkeypatch.setattr(app.root, "iconify", lambda: hidden.append(1))
    base = {"conn": "connected", "connected": True, "focused": False, "paused": False, "power": 212,
            "cadence": 88, "ride_s": 75, "miles": 0.5, "units": "mph", "workout": None}
    app.on_status(base)
    assert "212" in app.texts and "1:15" in app.texts and "0.50" in app.texts and "88" in app.texts
    assert "Click into the game" in app.texts
    assert hidden == []
    app.on_status({**base, "focused": True})
    app.on_status({**base, "focused": True})
    assert hidden == [1]                     # minimized once, not every second
    app.on_status({**base, "focused": True, "workout": {"block": "Tempo", "index": 2, "count": 7, "left": 90,
                                                         "target": (150, 170)}})
    assert "150\u2013170 W" in app.texts and "1:30 left" in app.texts
    app.on_message("STAND UP & STRETCH \u00b7 30 S")
    assert "Stand up & stretch \u00b7 30 s" in screen_text(app)
    app.riding = True
    app.click("end")
    assert app.stop.is_set() and "Finishing\u2026" in app.texts


def test_short_and_sim_rides_show_a_short_screen(app):
    from types import SimpleNamespace

    app.riding = True
    app.on_finished({"ride": None, "summary": None, "coach": None, "workout": None})
    assert app.riding is False and app.view == "message" and "Too short to keep" in app.texts
    app.on_finished({"ride": None, "summary": SimpleNamespace(moving_s=75), "coach": None, "workout": None})
    assert "Test ride" in app.texts
    app.click("back")
    assert app.view == "start"


def test_error_screen_and_ui_errors_never_end_a_ride(app, monkeypatch):
    import bridge.crashreport as cr

    monkeypatch.setattr(cr, "write_report", lambda exc, tool, log_dir=None: None)
    app.on_error(RuntimeError("boom"), None)
    assert app.view == "message" and "Something went wrong" in app.texts and "boom" in screen_text(app)
    app.on_error(SystemExit("--trainer 'x' is not a Bluetooth address"), None)
    assert "Check the ride options" in app.texts
    app.riding = True
    app.on_ui_error(RuntimeError, RuntimeError("draw bug"), None)   # mid-ride: swallowed (crash report only)
    assert app.riding is True


def _coach_with_rides():
    from datetime import datetime

    from bridge.coach import Coach
    from tests.test_coach import TODAY, ride

    rides = [ride(datetime(2026, 10, d, 8), 30 + d, 120 + d, workout="tempo") for d in range(1, 4)]
    return Coach(rides, ftp_set=160, today=TODAY), rides


def test_summary_feel_and_keys(app, tmp_path):
    from bridge import motivation as mo

    coach, rides = _coach_with_rides()
    r = rides[-1]
    app.show_summary(r, coach, None, "tempo", post_ride=True)
    assert app.view == "summary" and {"again", "picture", "close", "feel:1", "feel:5"} <= keys(app)
    assert "HOW DID IT FEEL?" in app.texts and "POWER CURVE" in app.texts and "MINUTES PER WEEK" in app.texts
    app.act("feel:4")                                         # the 4 key
    assert mo.load_book(tmp_path)["feel"][r.stamp] == 4
    app.click("feel:2")
    assert mo.load_book(tmp_path)["feel"][r.stamp] == 2
    app.click("again")                                        # leaving the summary: 1-5 no longer rate it
    assert app.view == "start"
    app.act("feel:5")
    assert mo.load_book(tmp_path)["feel"][r.stamp] == 2
    app.show_summary(r, coach, None, "tempo", post_ride=False)
    assert app.view == "book" and "feel:1" not in keys(app)  # the ride book doesn't ask


def test_long_text_wraps_inside_the_page(app):
    from bridge import theme

    path = "C:\\Users\\someone\\AppData\\Local\\Temp\\" + "x" * 200 + "\\crash-app-20261006-190000.txt"
    app.on_error(RuntimeError("could not reach the trainer"), path)
    for s in app.texts:
        assert theme.font(15, "SemiLight").getlength(s) / theme.SS <= 960 - 2 * 56 + 1, s[:40]


def test_single_instance():
    from bridge import app as ap

    if sys.platform != "win32":
        pytest.skip("Windows mutex")
    first = ap.single_instance()
    assert first is not None
    assert ap.single_instance() is None      # a second window would fight over the trainer
    import ctypes

    ctypes.windll.kernel32.CloseHandle(first)


def test_closing_mid_ride_asks_then_ends_the_ride(app, monkeypatch):
    import tkinter.messagebox as mb

    app.coach = app.load_coach()
    app.show_riding()
    app.riding = True
    answers = iter([False, True])
    monkeypatch.setattr(mb, "askyesno", lambda *a, **k: next(answers))
    app.on_close()
    assert not app.stop.is_set() and app.root.winfo_exists()      # "no": keep riding
    app.on_close()
    assert app.stop.is_set() and app.root.winfo_exists()          # "yes": end it, summary still to come


def test_recorder_runs_hidden_with_fixed_arguments(app, monkeypatch):
    import subprocess

    from bridge import app as ap

    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw)) or object())
    app.args.dry_run, app.args.sim = True, None
    app.start_recorder()
    assert calls == []                                            # dry runs and sims: no recorder
    app.args.dry_run = False
    app.start_recorder()
    if sys.platform != "win32":
        assert calls == []
        return
    (argv, kw), = calls
    assert argv[-1].endswith("ride_recorder.py") and Path(argv[0]).name.lower().startswith("python")
    assert kw["creationflags"] == subprocess.CREATE_NO_WINDOW and "shell" not in kw
    assert Path(kw["cwd"]) == ap.ROOT


def test_begin_passes_the_choices_to_the_ride(app, monkeypatch):
    started = []
    monkeypatch.setattr(app, "start_recorder", lambda: None)
    monkeypatch.setattr(app, "_ride", lambda: started.append((app.args.workout, app.args.gravel, app.args.menu)))
    app.click("ride:endurance")
    app.click("road:True")
    app.act("enter")                     # Enter = begin
    app.thread.join(2)
    assert started == [("endurance", True, False)] and app.riding and app.view == "riding"
    app.begin()                          # a second click while riding does nothing
    assert len(started) == 1


def test_workout_option_preselects(tmp_path):
    a = make_app("--dry-run", "--log-dir", str(tmp_path), "--workout", "cadence")
    try:
        assert a.workout == "cadence"
    finally:
        a.root.destroy()


def test_bad_option_in_the_ride_thread_shows_a_message_not_a_crash(tmp_path, monkeypatch):
    import bridge.crashreport as cr

    monkeypatch.setattr(cr, "write_report", lambda *a, **k: pytest.fail("no crash report for a bad option"))
    a = make_app("--dry-run", "--log-dir", str(tmp_path), "--no-sounds", "--no-hotkeys", "--trainer", "xyz")
    try:
        a.root.withdraw()
        a.begin()
        a.thread.join(20)
        kinds = []
        while not a.events.empty():
            kind, data = a.events.get_nowait()
            kinds.append(kind)
            if kind == "error":
                a.on_error(*data)
        assert "error" in kinds and a.riding is False
        assert "not a Bluetooth address" in screen_text(a)
    finally:
        a.root.destroy()


def test_save_picture(app, tmp_path, monkeypatch):
    import os

    opened = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    coach, rides = _coach_with_rides()
    app.show_summary(rides[-1], coach, None, "tempo", post_ride=True)
    app.click("picture")
    assert app.notice.startswith("saved") and list(tmp_path.glob("share-*.png"))
    assert any(t.startswith("saved") for t in app.texts)


def test_window_and_card_share_one_look(app):
    from bridge import theme
    from bridge.sharecard import render_card

    coach, rides = _coach_with_rides()
    app.show_summary(rides[-1], coach, None, "tempo", post_ride=True)
    assert app._page_img.width() == 960 and app._page_img.height() >= 400
    card = render_card(coach, rides[-1])
    assert card.size == (1200, 630)
    top, bottom = card.getpixel((600, 2)), card.getpixel((600, 627))   # the shared dusk gradient
    assert all(abs(a - b) <= 6 for a, b in zip(top, theme.TOP))
    assert all(abs(a - b) <= 8 for a, b in zip(bottom, theme.BOTTOM))
