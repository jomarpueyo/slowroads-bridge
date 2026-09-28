"""Off-production experiments against the live game (docs/RESEARCH.md section 7).

TESTING ONLY: drives the car through the virtual pad and reads the HUD with screen OCR.
Never used by the ride bridge. Every sample goes to logs/exp-<test>-<stamp>.csv and a summary to
logs/exp-<test>-<stamp>.log. Screenshots of notable moments go to logs/exp-<test>-<stamp>/.

Tests (car stopped, Slow Roads open, auto-steer on):
  limiter   Does the in-game speed limit cap controller throttle? Throttle 0.6 for 15 s, then coast.
  holds     Where does speed settle at small fixed throttle values? 0.03 .. 0.2, 15 s each.
  buttons   Which pad buttons change the speed-control target (the number by the padlock)?
            Presses each button once with the car stopped. Skips Start/Back/Guide.

Usage: python tools/experiments.py <test> [--focus]
  --focus  bring the Slow Roads window to the front first (Chromium may only deliver gamepad
           input to the focused window).
"""

import argparse
import csv
import ctypes
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge.ridelog import LOG_DIR, setup_event_log, timestamp  # noqa: E402
from speedo import SpeedoReader, csv_safe  # noqa: E402

log = logging.getLogger("exp")
HZ = 10
# 1920x1080 HUD boxes: speed/gear, and the speed-control target right of the padlock.
SPEED_BOX = {"left": 1420, "top": 880, "width": 220, "height": 140}
LIMIT_BOX = {"left": 1590, "top": 895, "width": 110, "height": 60}


def focus_game() -> bool:
    from bridge import gamewin

    user32 = ctypes.windll.user32
    hwnd = gamewin.find_game_window()  # by process (slowroads.exe), not by title
    if not hwnd:
        log.warning("Slow Roads window not found")
        return False
    user32.keybd_event(0x12, 0, 0, 0)  # Alt down/up lets SetForegroundWindow succeed
    user32.keybd_event(0x12, 0, 2, 0)
    user32.ShowWindow(hwnd, 9)
    ok = bool(user32.SetForegroundWindow(hwnd))
    log.info("focus game window: %s", ok)
    return ok


def foreground_title() -> str:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


class Probe:
    def __init__(self, name: str) -> None:
        self.stamp = timestamp()
        self.name = name
        self.speed = SpeedoReader(region=SPEED_BOX)
        self.limit = SpeedoReader(region=LIMIT_BOX)
        self.shots = LOG_DIR / f"exp-{name}-{self.stamp}"
        self.shots.mkdir(parents=True, exist_ok=True)
        self.file = open(LOG_DIR / f"exp-{name}-{self.stamp}.csv", "w", newline="", encoding="utf-8")
        self.w = csv.writer(self.file)
        self.w.writerow(["t_s", "phase", "throttle", "brake", "kmh", "gear", "limit", "focus", "ocr_speed", "ocr_limit"])
        self.t0 = time.monotonic()
        self.last_kmh = None

    def read_limit(self):
        text = self.limit.read().text
        digits = "".join(ch for ch in text if ch.isdigit())
        return (int(digits) if digits and len(digits) <= 3 else None), text

    def sample(self, phase, throttle=0.0, brake=0.0):
        r = self.speed.read()
        lim, lim_text = self.read_limit()
        if r.kmh is not None:
            self.last_kmh = r.kmh
        self.w.writerow([f"{time.monotonic() - self.t0:.3f}", phase, throttle, brake,
                         "" if r.kmh is None else f"{r.kmh:.2f}", r.gear or "", "" if lim is None else lim,
                         foreground_title()[:30], csv_safe(r.text), csv_safe(lim_text)])
        self.file.flush()
        return r.kmh, lim

    def shot(self, label: str) -> None:
        from PIL import Image

        s = self.speed._sct.grab(self.speed._sct.monitors[1])
        Image.frombytes("RGB", s.size, s.rgb).resize((960, 540)).save(self.shots / f"{label}.jpg", quality=75)


def hold(probe, pad, phase, throttle, brake, seconds):
    out = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        tick = time.monotonic()
        pad.set_controls(throttle, brake)
        kmh, lim = probe.sample(phase, throttle, brake)
        out.append((time.monotonic() - probe.t0, kmh))
        time.sleep(max(0.0, 1 / HZ - (time.monotonic() - tick)))
    return out


def settle_stats(points):
    """Last-3-s mean speed and slope (km/h per s) of a hold."""
    pts = [(t, v) for t, v in points if v is not None]
    if len(pts) < 5:
        return None, None
    tail = [p for p in pts if p[0] >= pts[-1][0] - 3.0]
    mean = sum(v for _, v in tail) / len(tail)
    slope = (tail[-1][1] - tail[0][1]) / max(tail[-1][0] - tail[0][0], 1e-3)
    return mean, slope


def stop_car(probe, pad):
    """Coast, then gentle brake only while clearly moving (never hold brake at a stop: reverse)."""
    hold(probe, pad, "coast", 0.0, 0.0, 3.0)
    for _ in range(40):
        if probe.last_kmh is None or probe.last_kmh < 12:
            break
        hold(probe, pad, "brake", 0.0, 0.4, 0.5)
    hold(probe, pad, "settle", 0.0, 0.0, 4.0)


def test_limiter(probe, pad):
    kmh, lim = probe.sample("start")
    log.info("start speed %s km/h, speed-control target %s", kmh, lim)
    pts = hold(probe, pad, "throttle_0.6", 0.6, 0.0, 15.0)
    probe.shot("limiter_end_of_throttle")
    mean, slope = settle_stats(pts)
    top = max((v for _, v in pts if v is not None), default=None)
    if top is None or top < 2:
        log.warning("car did not move: is the game window focused? (use --focus)")
        return
    log.info("throttle 0.6 for 15 s: top %.1f km/h, last-3-s mean %.1f, slope %+.2f km/h/s", top, mean, slope)
    if lim:
        log.info("limit %d mph = %.1f km/h -> %s", lim, lim * 1.609344,
                 "CAPPED by limiter" if top <= lim * 1.609344 + 3 else "NOT capped")
    stop_car(probe, pad)


def test_holds(probe, pad):
    for thr in (0.03, 0.06, 0.1, 0.15, 0.2):
        pts = hold(probe, pad, f"hold_{thr}", thr, 0.0, 15.0)
        mean, slope = settle_stats(pts)
        log.info("hold %.2f: last-3-s mean %s km/h, slope %s km/h/s", thr,
                 "n/a" if mean is None else f"{mean:.1f}", "n/a" if slope is None else f"{slope:+.2f}")
    probe.shot("holds_end")
    stop_car(probe, pad)


def test_buttons(probe, pad):
    import vgamepad as vg

    B = vg.XUSB_BUTTON
    buttons = [("dpad_up", B.XUSB_GAMEPAD_DPAD_UP), ("dpad_down", B.XUSB_GAMEPAD_DPAD_DOWN),
               ("dpad_left", B.XUSB_GAMEPAD_DPAD_LEFT), ("dpad_right", B.XUSB_GAMEPAD_DPAD_RIGHT),
               ("lb", B.XUSB_GAMEPAD_LEFT_SHOULDER), ("rb", B.XUSB_GAMEPAD_RIGHT_SHOULDER),
               ("x", B.XUSB_GAMEPAD_X), ("y", B.XUSB_GAMEPAD_Y), ("b", B.XUSB_GAMEPAD_B),
               ("a", B.XUSB_GAMEPAD_A), ("ls", B.XUSB_GAMEPAD_LEFT_THUMB), ("rs", B.XUSB_GAMEPAD_RIGHT_THUMB)]
    raw = pad._pad
    for name, btn in buttons:
        before = probe.read_limit()[0]
        probe.shot(f"btn_{name}_before")
        raw.press_button(button=btn)
        raw.update()
        time.sleep(0.15)
        raw.release_button(button=btn)
        raw.update()
        time.sleep(1.2)
        after = probe.read_limit()[0]
        probe.sample(f"after_{name}")
        probe.shot(f"btn_{name}_after")
        log.info("button %-10s limit %s -> %s%s", name, before, after,
                 "   <-- CHANGED" if before != after else "")


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def wheel(notches: int) -> None:
    """Scroll over the game (left side, clear of other windows); restores the cursor."""
    user32 = ctypes.windll.user32
    orig = POINT()
    user32.GetCursorPos(ctypes.byref(orig))
    user32.SetCursorPos(250, 600)
    for _ in range(abs(notches)):
        user32.mouse_event(0x0800, 0, 0, 120 if notches > 0 else -120, 0)
        time.sleep(0.12)
    user32.SetCursorPos(orig.x, orig.y)


def stable_limit(probe, tries=7):
    vals = [probe.read_limit()[0] for _ in range(tries)]
    vals = [v for v in vals if v is not None]
    return max(set(vals), key=vals.count) if vals else None


def test_limitrange(probe, pad):
    start = stable_limit(probe)
    log.info("limit at start: %s", start)
    seen = []
    for _ in range(30):
        wheel(-1)
        time.sleep(0.3)
        seen.append(stable_limit(probe))
    log.info("scrolling down: %s", seen)
    low = seen[-1]
    seen = []
    for _ in range(40):
        wheel(1)
        time.sleep(0.3)
        seen.append(stable_limit(probe))
    log.info("scrolling up: %s", seen)
    high = seen[-1]
    probe.shot("limit_max")
    log.info("limit range: %s .. %s (step 5)", low, high)
    if start and high:
        wheel(-round((high - start) / 5))
        time.sleep(0.5)
        log.info("restored limit to %s (wanted %s)", stable_limit(probe), start)


def test_limitstep(probe, pad):
    lim0 = stable_limit(probe)
    log.info("limit %s; accelerating to it with throttle 0.6", lim0)
    hold(probe, pad, "to_limit", 0.6, 0.0, 12.0)
    wheel(-4)  # -20 mph
    log.info("limit lowered 4 notches -> %s, holding throttle 0.6", stable_limit(probe))
    pts = hold(probe, pad, "after_drop", 0.6, 0.0, 10.0)
    v = [(t, k) for t, k in pts if k is not None]
    if v:
        t0 = v[0][0]
        for mark in (0.5, 1, 2, 3, 5, 8):
            near = min(v, key=lambda p: abs(p[0] - t0 - mark))
            log.info("  +%.1f s: %.1f km/h", mark, near[1])
    wheel(4)
    log.info("limit raised back -> %s, holding throttle 0.6", stable_limit(probe))
    pts = hold(probe, pad, "after_raise", 0.6, 0.0, 8.0)
    v = [(t, k) for t, k in pts if k is not None]
    if v:
        t0 = v[0][0]
        for mark in (1, 2, 4, 7):
            near = min(v, key=lambda p: abs(p[0] - t0 - mark))
            log.info("  +%.1f s: %.1f km/h", mark, near[1])
    stop_car(probe, pad)


def test_coast(probe, pad):
    """Coast-throttle sweep with the debug panel (F4) on: for each throttle c, get to ~40 mph under a
    40 limit, raise the limit to 60 so it can't interfere, hold c for 6 s, and measure deceleration and
    grade from the panel. Flat-equivalent decel = measured decel - g*grade (35.3 km/h/s per unit grade):
    on a climb part of the measured slowdown is gravity. NOTE: grade here uses start/end x,z, which OCR
    garbles (minus sign as a dash); prefer elevation vs integrated panel speed (see RESEARCH.md §10)."""
    from debugpanel import DebugPanelReader, grade

    panel = DebugPanelReader()
    dbg = open(LOG_DIR / f"exp-coast-{probe.stamp}-panel.csv", "w", newline="", encoding="utf-8")
    dw = csv.writer(dbg)
    dw.writerow(["t_s", "phase", "throttle", "x", "elev", "z", "kph", "rpm", "text"])

    def panel_hold(phase, thr, seconds):
        rows = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            tick = time.monotonic()
            pad.set_controls(thr, 0.0)
            r = panel.read()
            t = time.monotonic() - probe.t0
            dw.writerow([f"{t:.3f}", phase, thr, r.x, r.elev, r.z, r.kph, r.rpm, csv_safe(r.text)])
            rows.append((t, r))
            time.sleep(max(0.0, 1 / HZ - (time.monotonic() - tick)))
        dbg.flush()
        return rows

    wheel(-30)
    wheel(7)  # floor 5 + 7 notches = 40
    time.sleep(0.5)
    log.info("limit set to %s (want 40)", stable_limit(probe))
    results = []
    for c in (0.0, 0.03, 0.06, 0.1, 0.15):
        panel_hold(f"to40_{c}", 0.6, 9.0)  # under the 40 limit
        wheel(4)  # limit 60: out of the way
        rows = [(t, r) for t, r in panel_hold(f"coast_{c}", c, 6.0) if r.kph is not None]
        wheel(-4)
        if len(rows) < 10:
            log.warning("coast %.2f: too few panel readings (%d); is F4 on?", c, len(rows))
            continue
        head = [p for p in rows if p[0] <= rows[0][0] + 1.0]
        tail = [p for p in rows if p[0] >= rows[-1][0] - 1.0]
        v0 = sum(r.kph for _, r in head) / len(head)
        v1 = sum(r.kph for _, r in tail) / len(tail)
        dt = (tail[-1][0] + tail[0][0]) / 2 - (head[-1][0] + head[0][0]) / 2
        decel = (v0 - v1) / dt
        gr = grade(head[0][1], tail[-1][1])
        flat = None if gr is None else decel - 35.3 * gr
        results.append((c, v0, v1, decel, gr, flat))
        log.info("coast %.2f: %.1f -> %.1f km/h in %.1f s = %.2f km/h/s decel; grade %s; flat-equivalent %s",
                 c, v0, v1, dt, decel, "n/a" if gr is None else f"{gr * 100:+.1f}%",
                 "n/a" if flat is None else f"{flat:.2f} km/h/s")
    dbg.close()
    stop_car(probe, pad)
    log.info("summary (throttle, v0, v1, decel, grade, flat-equivalent decel): %s", results)


TESTS = {"limiter": test_limiter, "holds": test_holds, "buttons": test_buttons,
         "limitrange": test_limitrange, "limitstep": test_limitstep, "coast": test_coast}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("test", choices=sorted(TESTS))
    ap.add_argument("--focus", action="store_true")
    args = ap.parse_args()
    probe = Probe(args.test)
    setup_event_log(LOG_DIR, probe.stamp, verbose=True, prefix=f"exp-{args.test}")
    from bridge.pad import VirtualPad

    # These tests take over the game: focus, mouse wheel, cursor and controller (finding 8).
    print(f"TESTING TOOL: '{args.test}' will drive Slow Roads"
          f"{', bring it to the front,' if args.focus else ''} and move the mouse. Ctrl+C within 3 s to cancel.",
          flush=True)
    pad = VirtualPad()
    pad.set_controls(0.0, 0.0)
    time.sleep(3.0)  # cancel window, and lets the game pick up the new pad
    if args.focus:
        focus_game()
        time.sleep(1.0)
    log.info("foreground window: %r", foreground_title())
    try:
        TESTS[args.test](probe, pad)
    finally:
        pad.close()
        probe.file.close()
    return 0


if __name__ == "__main__":
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from bridge.crashreport import run_main

    sys.exit(run_main(main, "experiments"))
