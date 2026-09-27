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
    # Coast hold (ride 11:39 feedback: stopping pedalling downhill braked the car at once because the
    # limit followed the KICKR flywheel spin-down). When not pedalling, freeze the limit a step above
    # where it was and send a small throttle that cancels the game's engine braking, so gravity decides:
    # descents hold or gain speed, climbs slow. Coast test 11:57 (grade-corrected): throttle 0.03-0.1
    # slows the car ~0.5-1.5 km/h/s on the flat (bike-like); 0 engine-brakes ~4; 0.15 accelerates.
    coast_watts: float = 25.0       # below this (and low cadence) = not pedalling
    coast_cadence: float = 20.0
    coast_throttle: float = 0.05
    coast_margin: int = 5           # display units above the frozen limit, room to gain speed downhill
    coast_hold_s: float = 12.0      # then release: throttle 0, limit steps down to stop
    # One 5-unit step per this while releasing, with the coast throttle kept until the last 10 units.
    # 2 s with throttle 0 dropped the car 60 -> 18 km/h in 6 s in the 12:34 test (~7 km/h/s, harsh).
    release_step_s: float = 4.0
    release_throttle_floor: int = 10
    # Push bonus (rider request 2026-09-27: reward pushing hard). Road-bike physics makes speed grow
    # with ~cube root of power, so hard efforts barely move the limit. Above push_easy_w the gear ratio
    # ramps up to (1 + push_boost) at push_hard_w, and while pushing the limit steps up as soon as the
    # target passes the current step instead of waiting for the midpoint to the next one.
    push_boost: float = 0.5
    push_easy_w: float = 150.0
    push_hard_w: float = 400.0
    # Resume grace (ride 12:48: resuming after a coast dropped the limit at once, e.g. 40 -> 25 mph at
    # 244 s, because the virtual bike slowed on "flat" physics while the game car rolled downhill).
    # After coasting, hold the limit (up-steps still allowed) for resume_grace_s while watts build, and
    # while riding never step down more than once per down_step_s.
    resume_grace_s: float = 8.0
    down_step_s: float = 2.5


def kmh_to_display(kmh: float, units: str) -> float:
    return kmh / MPH_KMH if units == "mph" else kmh


def display_to_kmh(value: float, units: str) -> float:
    return value * MPH_KMH if units == "mph" else value


class SpeedLimitPlanner:
    """Target speed -> desired game limit (5-unit steps, with hysteresis) and throttle."""

    def __init__(self, config: LimitConfig | None = None) -> None:
        self.config = config or LimitConfig()
        self.desired = MIN_LIMIT
        self.coast_since: float | None = None
        self.coast_limit = MIN_LIMIT
        self.state = "stopped"  # stopped | riding | coasting | releasing
        self.resume_until = float("-inf")
        self.resume_floor = MIN_LIMIT
        self._last_down = float("-inf")

    def push_factor(self, smoothed_w: float) -> float:
        """Gear multiplier for effort: 1.0 at or below push_easy_w, 1 + push_boost at push_hard_w+."""
        c = self.config
        if c.push_boost <= 0 or c.push_hard_w <= c.push_easy_w:
            return 1.0
        x = (smoothed_w - c.push_easy_w) / (c.push_hard_w - c.push_easy_w)
        return 1.0 + c.push_boost * min(max(x, 0.0), 1.0)

    def push_throttle(self, base: float, factor: float) -> float:
        """Throttle while riding: drive_throttle, rising to 1.0 at full push, so the game accelerates
        harder toward the higher limit (the limit still caps the speed)."""
        c = self.config
        if c.push_boost <= 0 or base <= 0:
            return base
        level = min(max((factor - 1.0) / c.push_boost, 0.0), 1.0)
        return base + (1.0 - base) * level

    def is_pedalling(self, power_w, cadence_rpm) -> bool:
        c = self.config
        return (power_w or 0) >= c.coast_watts or (cadence_rpm or 0) >= c.coast_cadence

    def update(self, target_kmh: float, active: bool, pedalling: bool = True,
               now: float = 0.0, pushing: bool = False) -> tuple[int, float]:
        c = self.config
        if not active:
            self.coast_since, self.state, self.desired = None, "stopped", MIN_LIMIT
            return self.desired, 0.0
        if c.coast_hold_s <= 0:
            pedalling = True  # coast hold disabled: the limit follows the trainer speed (pre-11:39 behaviour)
        if not pedalling and self.desired > MIN_LIMIT:
            if self.coast_since is None:
                self.coast_since = now
                self.coast_limit = min(self.desired + c.coast_margin, MAX_LIMIT)
            held = now - self.coast_since
            if held < c.coast_hold_s:
                self.state = "coasting"
                return self.coast_limit, c.coast_throttle
            steps = int((held - c.coast_hold_s) // c.release_step_s) + 1
            self.state = "releasing"
            self.desired = max(MIN_LIMIT, self.coast_limit - STEP * steps)
            thr = c.coast_throttle if self.desired > c.release_throttle_floor else 0.0
            return self.desired, thr
        if pedalling:
            if self.coast_since is not None:  # resuming after a coast: hold the limit while watts build
                self.resume_until = now + c.resume_grace_s
                self.resume_floor = self.coast_limit if self.state == "coasting" else self.desired
                self.desired = max(self.desired, self.resume_floor)
            self.coast_since = None
        elif self.coast_since is not None:
            # Released to the floor but still not pedalling (flywheel may still be spinning): stay stopped.
            self.state, self.desired = "stopped", MIN_LIMIT
            return self.desired, 0.0
        if target_kmh < c.moving_kmh:
            self.state, self.desired = "stopped", MIN_LIMIT
            return self.desired, 0.0
        self.state = "riding"
        want = kmh_to_display(target_kmh, c.units)
        if self.desired == MIN_LIMIT and want > MIN_LIMIT:
            self.desired = max(MIN_LIMIT, int(round(want / STEP)) * STEP)  # start from nearest step
        # Move a step only once the target is clearly past the boundary to the next step. While pushing
        # hard, step up as soon as the target passes the current step (rewards effort).
        up_at = c.hysteresis if pushing else STEP / 2 + c.hysteresis
        while want > self.desired + up_at and self.desired < MAX_LIMIT:
            self.desired += STEP
        # ...and don't undo that early step while still pushing: only drop below the step under it.
        down_at = STEP + c.hysteresis if pushing else STEP / 2 + c.hysteresis
        in_grace = now < self.resume_until
        if (want < self.desired - down_at and self.desired > MIN_LIMIT and not in_grace
                and now - self._last_down >= c.down_step_s):
            self.desired -= STEP  # one step at a time, at most every down_step_s
            self._last_down = now
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
