"""Limit mode: let Slow Roads hold the speed. The bridge sets the game's speed limit.

Verified 2026-09-27 (tools/experiments.py, logs/exp-*):
  - The in-game speed control in limit ("max") mode caps controller throttle: at throttle 0.6 the
    car held 63.9 km/h under a 40 mph limit.
  - Lowering the limit slows the car actively: 40 -> 20 mph took the car 64 -> 32 km/h in ~3 s and
    held 32.2; raising it back took ~2 s. So the game closes the speed loop, hills and all.
  - The mouse wheel over the game changes the limit by 5 display units per notch (mph or km/h),
    range 5..125. No default pad button does (X cycles assist mode, A toggles assist).
  - Gamepad input only reaches the game while its window is focused.

The bridge never reads the limit back (no screen reading in production). It counts the notches
it sends, and re-syncs by over-scrolling down whenever it goes to the 5-unit floor: extra notches
below the floor are ignored by the game, so the count is exact again.
"""

import ctypes
import logging
import time
from dataclasses import dataclass

log = logging.getLogger("bridge.limiter")
MPH_KMH = 1.609344
STEP = 5          # display units per wheel notch
MIN_LIMIT = 5
MAX_LIMIT = 125
RESYNC_EXTRA = 4  # extra down notches when homing to the floor


@dataclass
class LimitConfig:
    units: str = "mph"            # the game's display units ("mph" or "km/h")
    drive_throttle: float = 0.6   # throttle held while pedalling; the game caps speed at the limit
    moving_kmh: float = 3.0       # target below this counts as stopped
    # Display units past a step boundary before changing the limit. 1.5 and then 0.5 left a
    # steady 45 km/h (27.96 mph) target at a 25 limit in the 10:40/10:43 tests; 0.25 rounds to
    # nearest while still damping flicker (the target is smoothed trainer speed).
    hysteresis: float = 0.25
    max_notches_per_s: float = 4.0


def kmh_to_display(kmh: float, units: str) -> float:
    return kmh / MPH_KMH if units == "mph" else kmh


def display_to_kmh(value: float, units: str) -> float:
    return value * MPH_KMH if units == "mph" else value


class SpeedLimitPlanner:
    """Target speed -> desired game limit (5-unit steps, with hysteresis) and throttle."""

    def __init__(self, config: LimitConfig | None = None) -> None:
        self.config = config or LimitConfig()
        self.desired = MIN_LIMIT

    def update(self, target_kmh: float, active: bool) -> tuple[int, float]:
        c = self.config
        if not active or target_kmh < c.moving_kmh:
            self.desired = MIN_LIMIT
            return self.desired, 0.0
        want = kmh_to_display(target_kmh, c.units)
        # Move a step only once the target is clearly past the boundary to the next step.
        while want > self.desired + STEP / 2 + c.hysteresis and self.desired < MAX_LIMIT:
            self.desired += STEP
        while want < self.desired - STEP / 2 - c.hysteresis and self.desired > MIN_LIMIT:
            self.desired -= STEP
        return self.desired, c.drive_throttle


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class WheelActuator:
    """Scrolls the game's speed limit. Only acts while Slow Roads is the foreground window."""

    def __init__(self, window_title: str = "Slow Roads", point=(250, 600), dry_run: bool = False) -> None:
        self.title = window_title
        self.point = point
        self.dry_run = dry_run
        self.current: int | None = None  # unknown until homed
        self._last_notch = 0.0
        self._user32 = None if dry_run else ctypes.windll.user32

    def game_focused(self) -> bool:
        if self.dry_run:
            return True
        hwnd = self._user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(128)
        self._user32.GetWindowTextW(hwnd, buf, 128)
        return buf.value == self.title

    def _scroll(self, notches: int) -> None:
        if self.dry_run or notches == 0:
            return
        u = self._user32
        orig = POINT()
        u.GetCursorPos(ctypes.byref(orig))
        u.SetCursorPos(*self.point)
        for _ in range(abs(notches)):
            u.mouse_event(0x0800, 0, 0, 120 if notches > 0 else -120, 0)  # MOUSEEVENTF_WHEEL
            time.sleep(0.03)
        u.SetCursorPos(orig.x, orig.y)

    def home(self) -> bool:
        """Scroll to the floor so the count is known. Returns False if the game isn't focused."""
        if not self.game_focused():
            return False
        self._scroll(-((MAX_LIMIT - MIN_LIMIT) // STEP + RESYNC_EXTRA))
        self.current = MIN_LIMIT
        log.info("limit homed to %d", MIN_LIMIT)
        return True

    def step_toward(self, desired: int, now: float, max_per_s: float) -> None:
        """Send at most one notch per call, rate-limited. Re-sync when going to the floor."""
        if self.current is None:
            self.home()
            return
        if desired == self.current or not self.game_focused():
            return
        if now - self._last_notch < 1.0 / max_per_s:
            return
        if desired == MIN_LIMIT and self.current - STEP == MIN_LIMIT:
            self._scroll(-(1 + RESYNC_EXTRA))  # extra notches are ignored at the floor: exact again
        else:
            self._scroll(1 if desired > self.current else -1)
        self.current += STEP if desired > self.current else -STEP
        self._last_notch = now
        log.debug("limit -> %d", self.current)
