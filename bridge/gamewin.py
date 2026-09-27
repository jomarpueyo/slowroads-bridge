"""Locate the Slow Roads window by process, and pick safe points on it (docs/SECURITY.md finding 2).

The bridge scrolls the mouse wheel to set the game's speed limit. Windows delivers wheel events to the
window *under the cursor* (MouseWheelRouting = 2 is the default), not the focused one, and any window
can call itself "Slow Roads". So before scrolling we require that:
  - the foreground window belongs to the slowroads.exe process, and
  - the top-level window under the scroll point is that same game window.
If no candidate point on the game is uncovered, we don't scroll (and don't count a notch).
"""

import ctypes
from ctypes import wintypes

GAME_EXE = "slowroads.exe"
GA_ROOT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# Candidate scroll points as fractions of the game window (x, y): left-middle first (clear of the
# centre and the bottom-right HUD), then other open road/sky areas.
CANDIDATES = ((0.13, 0.55), (0.13, 0.30), (0.50, 0.30), (0.87, 0.30), (0.87, 0.55), (0.50, 0.55))

_user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
_kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None


def process_image(hwnd) -> str:
    """Lower-case executable file name of the process that owns hwnd ('' if unknown)."""
    if not hwnd or _user32 is None:
        return ""
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(len(buf))
        if not _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    finally:
        _kernel32.CloseHandle(h)


def is_game(hwnd) -> bool:
    return bool(hwnd) and process_image(_user32.GetAncestor(hwnd, GA_ROOT)) == GAME_EXE


def find_game_window():
    """The visible top-level window of slowroads.exe with a title, or None."""
    if _user32 is None:
        return None
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if _user32.IsWindowVisible(hwnd) and _user32.GetWindowTextLengthW(hwnd) > 0 \
                and process_image(hwnd) == GAME_EXE:
            found.append(hwnd)
            return False
        return True

    _user32.EnumWindows(cb, 0)
    return found[0] if found else None


def game_focused() -> bool:
    return is_game(_user32.GetForegroundWindow()) if _user32 else False


def window_rect(hwnd):
    """(left, top, right, bottom) of hwnd on screen, or None."""
    r = wintypes.RECT()
    if not hwnd or not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.left, r.top, r.right, r.bottom


def top_level_at(x: int, y: int):
    return _user32.GetAncestor(_user32.WindowFromPoint(wintypes.POINT(x, y)), GA_ROOT)


def safe_scroll_point(game=None):
    """A screen point whose top-level window is the game, or None if every candidate is covered."""
    game = game or find_game_window()
    rect = window_rect(game)
    if not rect:
        return None
    left, top, right, bottom = rect
    for fx, fy in CANDIDATES:
        x, y = int(left + fx * (right - left)), int(top + fy * (bottom - top))
        if top_level_at(x, y) == game:
            return x, y
    return None
