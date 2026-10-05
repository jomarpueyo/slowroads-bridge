"""Ride overlay: a small, click-through panel of trainer data at the top right of Slow Roads.

Shows ride time, virtual bike distance, 3 s power, cadence, 1/5/10 min average power and power as a
share of FTP with its training zone. Trainer data only: nothing is read from the game.

It is a separate see-through window laid over the game (layered, click-through, never focused), not
part of the game. It only shows while Slow Roads is the window in front, keeps to the top of the
screen (the bridge scrolls the speed limit at 30-55% of the height), and F10 hides/shows it.
--no-overlay or "overlay": 0 in settings.json turns it off.
"""

import logging
import threading
import time
from collections import deque
from pathlib import Path

log = logging.getLogger("bridge.overlay")

KM_PER_MILE = 1.609344
# Coggan power zones as fractions of FTP: (upper bound, name, accent colour)
ZONES = ((0.55, "Z1", (170, 170, 170)), (0.75, "Z2", (90, 160, 255)), (0.90, "Z3", (90, 210, 120)),
         (1.05, "Z4", (250, 210, 70)), (1.20, "Z5", (255, 150, 60)), (1.50, "Z6", (255, 85, 85)),
         (float("inf"), "Z7", (190, 110, 255)))


def zone(watts: float, ftp: float):
    if not ftp or watts is None:
        return None
    frac = watts / ftp
    for upper, name, colour in ZONES:
        if frac <= upper:
            return name, colour, frac
    return None


class LiveStats:
    """Rolling power averages, distance and ride time, fed by the bridge."""

    def __init__(self) -> None:
        self.samples: deque = deque()  # (t, watts), one per trainer packet (1 Hz), last 20 min
        self.cadence = None
        self.started_at = None       # first pedal stroke
        self.distance_km = 0.0

    def add(self, t: float, watts, cadence) -> None:
        self.cadence = cadence
        if watts is None:
            return
        self.samples.append((t, float(watts)))
        while self.samples and self.samples[0][0] < t - 1200:
            self.samples.popleft()

    def start(self, t: float) -> None:
        if self.started_at is None:
            self.started_at = t

    def add_distance(self, km: float) -> None:
        if self.started_at is not None and km > 0:
            self.distance_km += km

    def avg(self, seconds: float, now: float):
        """(average watts over the last `seconds`, True once a full window of riding is covered)."""
        if self.started_at is None:
            return None, False
        window = [w for t, w in self.samples if t > now - seconds]
        if not window:
            return None, False
        return sum(window) / len(window), now - self.started_at >= seconds

    def rolling(self, now: float, durations=(60, 300, 1200)) -> dict:
        """{duration: average W over the last `duration` s, only once a full window has been ridden}."""
        out = {}
        for d in durations:
            v, full = self.avg(d, now)
            out[d] = v if full else None
        return out

    def snapshot(self, now: float, ftp: float, ftp_estimated: bool, units: str, paused: bool,
                 workout: dict | None = None, message: str | None = None) -> dict:
        p3, _ = self.avg(3.5, now)
        return {
            "elapsed": 0.0 if self.started_at is None else now - self.started_at,
            "distance": self.distance_km / (KM_PER_MILE if units == "mph" else 1.0),
            "dist_label": "MILES" if units == "mph" else "KM",
            "power": p3, "cadence": self.cadence,
            "avgs": [("1 MIN",) + self.avg(60, now), ("5 MIN",) + self.avg(300, now),
                     ("10 MIN",) + self.avg(600, now)],
            "ftp": ftp, "ftp_estimated": ftp_estimated, "paused": paused,
            "workout": workout, "message": message,
        }


# ---------------------------------------------------------------- drawing (Pillow)

FONT_DIR = Path(r"C:\Windows\Fonts")


def _font(size: int, weight: str):
    from PIL import ImageFont

    for name, variation in (("bahnschrift.ttf", weight), ("segoeuil.ttf" if weight == "Light" else "segoeui.ttf", None)):
        try:
            f = ImageFont.truetype(str(FONT_DIR / name), size)
            if variation:
                try:
                    f.set_variation_by_name(variation)
                except Exception:
                    pass
            return f
        except OSError:
            continue
    return ImageFont.load_default()


_fonts: dict = {}


def fonts(scale: float) -> dict:
    key = round(scale, 2)
    if key not in _fonts:
        s = lambda px: max(8, int(round(px * scale)))  # noqa: E731
        _fonts[key] = {"big": _font(s(30), "Light"), "mid": _font(s(20), "Light"),
                       "label": _font(s(10), "SemiLight")}
    return _fonts[key]


def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _tracked_width(text: str, font, tracking: float) -> int:
    """Width of a letter-spaced label (the game's small caps labels are spaced out)."""
    return int(sum(font.getlength(ch) for ch in text) + tracking * max(0, len(text) - 1))


def _draw_tracked(d, xy, text: str, font, fill, tracking: float) -> None:
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill, anchor="la")
        x += font.getlength(ch) + tracking


HIGHLIGHT = (255, 214, 120)  # warm gold for a number that just did something worth a look


class Attention:
    """Keeps the numbers quiet so they don't pull your eyes all ride (reading them all the time was
    distracting): after `fade_after` s they fade to `dim` (never off), and only the number that matters
    lights up for a moment: the time each whole minute, the miles each mile, the watts on a surge (25% over
    your 5 min average, or 20% over FTP), the cadence at `high_rpm` and up. Paused or the first seconds:
    everything bright. Coach messages and the workout row are never dimmed. fade_after 0 = never fade."""

    KEYS = ("time", "distance", "power", "cadence", "avgs", "ftp")

    def __init__(self, fade_after: float = 20.0, dim: float = 0.35, fade_s: float = 3.0,
                 high_rpm: float = 100.0) -> None:
        self.fade_after, self.fade_s, self.high_rpm = fade_after, fade_s, high_rpm
        self.dim = max(0.1, min(1.0, dim))
        self._hold: dict = {}
        self._hot: dict = {}
        self._minute = None
        self._mile = None
        self._paused = False

    def bump(self, key: str, now: float, seconds: float, hot: bool = False) -> None:
        self._hold[key] = max(self._hold.get(key, float("-inf")), now + seconds)
        if hot:
            self._hot[key] = max(self._hot.get(key, float("-inf")), now + seconds)

    def level(self, key: str, now: float) -> float:
        until = self._hold.get(key, float("-inf"))
        if now < until:
            return 1.0
        if now < until + self.fade_s:
            return 1.0 - (1.0 - self.dim) * (now - until) / self.fade_s
        return self.dim

    def apply(self, snap: dict, now: float) -> dict:
        """Add snap['alpha'] (key -> brightness 0..1) and snap['hot'] (keys to highlight); returns snap."""
        if self.fade_after <= 0:
            return snap
        elapsed = snap.get("elapsed") or 0.0
        paused = bool(snap.get("paused"))
        if paused or elapsed < self.fade_after:
            for k in self.KEYS:  # all bright at the start (then a gentle fade) and while paused
                self.bump(k, now, 0.5 if paused else self.fade_after - elapsed)
        if self._paused and not paused:
            for k in self.KEYS:
                self.bump(k, now, 5.0)
        self._paused = paused
        minute = int(elapsed // 60)
        if self._minute is not None and minute > self._minute:
            self.bump("time", now, 3.0)
        self._minute = minute
        mile = int(snap.get("distance") or 0.0)
        if self._mile is not None and mile > self._mile:
            self.bump("distance", now, 6.0, hot=True)
        self._mile = mile
        power, ftp = snap.get("power"), snap.get("ftp") or 0.0
        avg5 = next((v for name, v, _ in snap.get("avgs", []) if name == "5 MIN"), None)
        surge = power is not None and elapsed >= 60 and (
            (avg5 and power >= 1.25 * avg5 and power >= avg5 + 30) or (ftp and power >= 1.2 * ftp))
        if surge:
            self.bump("power", now, 3.0, hot=True)
            self.bump("ftp", now, 3.0)
        cad = snap.get("cadence")
        if cad is not None and cad >= self.high_rpm:
            self.bump("cadence", now, 3.0, hot=True)
        snap["alpha"] = {k: self.level(k, now) for k in self.KEYS}
        snap["hot"] = {k for k, t in self._hot.items() if now < t}
        return snap


def render(snap: dict, scale: float = 1.0):
    """RGBA image of the panel: white numbers with small spaced labels and a soft shadow, no box."""
    from PIL import Image, ImageDraw, ImageFilter

    f = fonts(scale)
    pad, col_gap, row_gap = int(14 * scale), int(26 * scale), int(10 * scale)
    white = (255, 255, 255, 235)
    dim = (255, 255, 255, 140)
    label_c = (255, 255, 255, 170)

    power = snap["power"]
    # Cells are (value, label, colour, key): `key` picks the brightness from snap["alpha"] (Attention), and
    # keys in snap["hot"] are drawn in the highlight colour (a mile done, a surge, a high cadence).
    top = [(_fmt_time(snap["elapsed"]), "TIME", white, "time"),
           (f"{snap['distance']:.2f}", snap["dist_label"], white, "distance"),
           ("--" if power is None else f"{power:.0f}", "WATTS", white, "power"),
           ("--" if snap["cadence"] is None else f"{snap['cadence']:.0f}", "RPM", white, "cadence")]
    bottom = [("--" if v is None else f"{v:.0f}", name, white if full else dim, "avgs")
              for name, v, full in snap["avgs"]]
    z = zone(power, snap["ftp"])
    if snap["ftp"]:
        pct = "--" if z is None else f"{z[2] * 100:.0f}%"
        bottom.append((pct, f"FTP {snap['ftp']:.0f}" + (" EST" if snap["ftp_estimated"] else ""), white, "ftp"))
    if snap["paused"]:
        top[0] = ("PAUSED", "F8 TO RESUME", white, None)
    alphas, hot = snap.get("alpha") or {}, snap.get("hot") or set()

    def shade(colour, key, highlight=True):
        if highlight and key in hot:
            colour = HIGHLIGHT + (colour[3],)
        a = alphas.get(key, 1.0) if key else 1.0
        return colour[:3] + (int(colour[3] * a),)

    probe = ImageDraw.Draw(Image.new("L", (1, 1)))

    def size(text, font):
        l, t, r, b = probe.textbbox((0, 0), text, font=font)
        return r - l, b - t

    track = 1.6 * scale

    def row_layout(cells, font):
        widths = [max(size(c[0], font)[0], _tracked_width(c[1], f["label"], track)) for c in cells]
        return widths, sum(widths) + col_gap * (len(cells) - 1)

    # optional workout row: block, time left, target (coloured by whether you're on it), cadence target
    wk = snap.get("workout")
    work = []
    if wk:
        state_c = {"ok": (130, 225, 140, 240), "low": (255, 195, 90, 240), "high": (255, 150, 110, 240)}
        work.append((f"{wk['index']}/{wk['count']}", wk["block"].upper()[:18], white, None))
        work.append((_fmt_time(wk["left"]), "LEFT", white, None))
        if wk["target"]:
            lo, hi = wk["target"]
            work.append((f"{lo:.0f}-{hi:.0f}", "TARGET W", state_c.get(wk["state"], white), None))
        else:
            work.append(("EASY", "ANY POWER", white, None))
        if wk.get("cadence"):
            work.append((f"{wk['cadence'][0]}-{wk['cadence'][1]}", "RPM TARGET", white, None))
    message = snap.get("message")

    w_top, total_top = row_layout(top, f["big"])
    w_bot, total_bot = row_layout(bottom, f["mid"])
    w_wk, total_wk = row_layout(work, f["mid"]) if work else ([], 0)
    big_h, mid_h, lab_h = size("0", f["big"])[1], size("0", f["mid"])[1], size("M", f["label"])[1]
    lab_gap = int(7 * scale)
    msg_w = size(message, f["mid"])[0] if message else 0
    width = max(total_top, total_bot, total_wk, msg_w) + 2 * pad + int(18 * scale)
    height = pad * 2 + big_h + lab_gap + lab_h + row_gap * 2 + mid_h + lab_gap + lab_h
    if work:
        height += row_gap * 2 + mid_h + lab_gap + lab_h
    if message:
        height += row_gap * 2 + mid_h + int(10 * scale)  # room for the message pill

    text = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(text)

    def draw_row(cells, widths, total, font, y, h):
        x = width - pad - total  # right-aligned block
        for (value, label, colour, key), w in zip(cells, widths):
            vw = size(value, font)[0]
            lw = _tracked_width(label, f["label"], track)
            d.text((x + w - vw, y), value, font=font, fill=shade(colour, key), anchor="la")
            _draw_tracked(d, (x + w - lw, y + h + lab_gap), label, f["label"], shade(label_c, key, False), track)
            x += w + col_gap
        return x

    y1 = pad
    draw_row(top, w_top, total_top, f["big"], y1, big_h)
    y2 = y1 + big_h + lab_gap + lab_h + row_gap * 2
    draw_row(bottom, w_bot, total_bot, f["mid"], y2, mid_h)
    if z is not None:  # small zone dot beside the FTP share
        r = int(4 * scale)
        cx, cy = width - pad + int(8 * scale), y2 + mid_h // 2 + int(2 * scale)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=z[1] + (int(230 * alphas.get("ftp", 1.0)),))
    y = y2 + mid_h + lab_gap + lab_h
    if work:
        y += row_gap * 2
        draw_row(work, w_wk, total_wk, f["mid"], y, mid_h)
        y += mid_h + lab_gap + lab_h
    if message:  # coach message: stand-up break, new best, milestone, next block
        y += row_gap * 2
        # a soft dark pill behind the message only: reminders must read on bright sky too (they're easy to miss)
        px, py = int(10 * scale), int(5 * scale)
        d.rounded_rectangle((width - pad - msg_w - px, y - py, width - pad + px, y + mid_h + py + int(3 * scale)),
                            radius=int(9 * scale), fill=(0, 0, 0, 120))
        d.text((width - pad - msg_w, y), message, font=f["mid"], fill=(255, 225, 150, 250), anchor="la")

    # soft shadow so it reads on bright sky and dark road alike
    alpha = text.getchannel("A")
    shadow = Image.new("RGBA", text.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.point(lambda a: int(a * 0.55)).filter(ImageFilter.GaussianBlur(max(1, int(3 * scale)))))
    out = Image.alpha_composite(shadow, text)
    return out


# ---------------------------------------------------------------- window (Win32 layered)

class Overlay:
    """The overlay window on its own thread. snapshot_fn() -> dict for render()."""

    def __init__(self, snapshot_fn, enabled: bool = True, win=None, fps: float = 4.0) -> None:
        self.snapshot_fn = snapshot_fn
        self.enabled = enabled
        self.visible = True  # F10
        self.fps = fps
        self._win = win
        self._stop = threading.Event()
        self._thread = None
        self.hwnd = None

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        try:
            import PIL  # noqa: F401  (installed by setup.ps1 via requirements-calibration.txt)
        except ImportError:
            self.enabled = False
            log.warning("overlay off: Pillow is not installed (run scripts\\setup.ps1)")
            print("overlay off: Pillow is not installed (run scripts\\setup.ps1)", flush=True)
            return
        self._thread = threading.Thread(target=self._run_safe, name="overlay", daemon=True)
        self._thread.start()

    def toggle(self) -> None:
        self.visible = not self.visible

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.0)

    def _run_safe(self) -> None:
        try:
            self._run()
        except Exception:
            log.exception("overlay stopped")  # never take the ride down with it

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        from . import gamewin

        win = self._win or gamewin
        user32, gdi32, kernel32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT

        WM_NCHITTEST, HTTRANSPARENT = 0x0084, -1

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_NCHITTEST:
                return HTTRANSPARENT  # clicks and wheel go through to the game
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wndproc)  # keep a reference

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                        ("hIconSm", wintypes.HICON)]

        hinst = kernel32.GetModuleHandleW(None)
        cls_name = "SlowRoadsBridgeOverlay"
        wc = WNDCLASSEXW(ctypes.sizeof(WNDCLASSEXW), 0, self._wndproc, 0, 0, hinst, None, None, None, None,
                         cls_name, None)
        user32.RegisterClassExW(ctypes.byref(wc))  # fails harmlessly if already registered

        WS_POPUP = 0x80000000
        WS_EX = 0x00080000 | 0x00000020 | 0x00000008 | 0x08000000 | 0x00000080
        # LAYERED | TRANSPARENT (click-through) | TOPMOST | NOACTIVATE (never takes focus) | TOOLWINDOW (no taskbar)
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        hwnd = user32.CreateWindowExW(WS_EX, cls_name, "slowroads-bridge overlay", WS_POPUP,
                                      0, 0, 1, 1, None, None, hinst, None)
        if not hwnd:
            log.warning("overlay window could not be created")
            return
        self.hwnd = hwnd
        log.info("overlay started")

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        user32.GetDC.restype = wintypes.HDC
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                               ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                                               ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
                                               ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        user32.IsIconic.argtypes = [wintypes.HWND]

        def blit(img, x, y):
            from PIL import Image, ImageChops

            r, g, b, a = img.split()
            premult = Image.merge("RGBA", (ImageChops.multiply(r, a), ImageChops.multiply(g, a),
                                           ImageChops.multiply(b, a), a))  # layered windows want premultiplied
            data = premult.tobytes("raw", "BGRA")
            w, h = img.size
            bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            screen = user32.GetDC(None)
            mem = gdi32.CreateCompatibleDC(screen)
            bits = ctypes.c_void_p()
            bmp = gdi32.CreateDIBSection(screen, ctypes.byref(bih), 0, ctypes.byref(bits), None, 0)
            try:
                ctypes.memmove(bits, data, len(data))
                old = gdi32.SelectObject(mem, bmp)
                blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, per-pixel alpha
                user32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(wintypes.POINT(x, y)),
                                           ctypes.byref(wintypes.SIZE(w, h)), mem,
                                           ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), 2)
                gdi32.SelectObject(mem, old)
            finally:
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(mem)
                user32.ReleaseDC(None, screen)

        msg = wintypes.MSG()
        shown = False
        next_draw = 0.0
        try:
            while not self._stop.is_set():
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                now = time.monotonic()
                if now >= next_draw:
                    next_draw = now + 1.0 / self.fps
                    game = win.find_game_window() if self.visible else None
                    show = bool(game) and win.game_focused() and not user32.IsIconic(game)
                    if show:
                        rc = wintypes.RECT()
                        user32.GetClientRect(game, ctypes.byref(rc))
                        origin = wintypes.POINT(0, 0)
                        user32.ClientToScreen(game, ctypes.byref(origin))
                        gw, gh = rc.right - rc.left, rc.bottom - rc.top
                        scale = max(0.6, min(2.5, gh / 1080))
                        img = render(self.snapshot_fn(), scale)
                        margin = int(22 * scale)
                        blit(img, origin.x + gw - img.size[0] - margin, origin.y + margin)
                        if not shown:
                            user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
                            shown = True
                    elif shown:
                        user32.ShowWindow(hwnd, 0)  # SW_HIDE
                        shown = False
                time.sleep(0.03)
        finally:
            user32.DestroyWindow(hwnd)
            self.hwnd = None
