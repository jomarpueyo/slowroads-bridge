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
CONFIRM_AFTER_S = 20.0  # game in front and rider pedalling this long -> one confirming resync burst


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
        if smoothed_w != smoothed_w:  # NaN
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
                if now < self.resume_until and self.desired <= self.resume_floor:
                    # Coasting again inside the last resume grace: keep that coast's limit. Adding the
                    # margin again ratcheted 30 -> 100 mph on burst riding (ride 2026-09-29 02:28, 6-9 min).
                    self.coast_limit = self.resume_floor
                else:
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
    """Scrolls the game's speed limit. Only acts while the slowroads.exe window is focused *and* is the
    window under the scroll point (docs/SECURITY.md finding 2); a notch only counts if it was sent to
    the game, so the bridge's count can't drift when another window is in the way."""

    def __init__(self, dry_run: bool = False, win=None) -> None:
        from . import gamewin

        self.dry_run = dry_run
        self.win = win or gamewin
        self.current: int | None = None  # unknown until homed
        # True only straight after a full home. Notches can be lost without the bridge knowing (the game's
        # own menu eats wheel events: ride 2026-09-29 02:42 left the count 15 mph low for 9 min), so the
        # bridge re-homes whenever it leaves the floor after stepping there.
        self._homed = False
        self._homes = 0
        self.confirmed = False  # a resync() burst succeeded while the game was surely on the road
        self._last_confirm_try = float("-inf")
        self.on_home = None  # optional callback after each successful home (sound cue)
        self._last_notch = 0.0
        self._game = None
        self._user32 = None if dry_run else ctypes.windll.user32

    def _game_hwnd(self):
        if self._game is not None and self.win.is_game(self._game):
            return self._game
        self._game = self.win.find_game_window()
        return self._game

    def game_focused(self) -> bool:
        return True if self.dry_run else self.win.game_focused()

    def _scroll(self, notches: int) -> int:
        """Scroll over an uncovered point of the game window. Returns the notches actually sent."""
        if self.dry_run or notches == 0:
            return abs(notches)
        game = self._game_hwnd()
        point = self.win.safe_scroll_point(game) if game else None
        if point is None:
            log.debug("no uncovered point on the game window; not scrolling")
            return 0
        u = self._user32
        orig = POINT()
        u.GetCursorPos(ctypes.byref(orig))
        sent = 0
        try:
            u.SetCursorPos(*point)
            for _ in range(abs(notches)):
                if self.win.top_level_at(*point) != game or not self.win.game_focused():
                    break  # something moved over the point, or focus left the game
                u.mouse_event(0x0800, 0, 0, 120 if notches > 0 else -120, 0)  # MOUSEEVENTF_WHEEL
                sent += 1
                time.sleep(0.03)
        finally:
            u.SetCursorPos(orig.x, orig.y)
        return sent

    def home(self) -> bool:
        """Scroll to the floor so the count is known. Returns False if it couldn't complete."""
        if not self.game_focused():
            return False
        want = (MAX_LIMIT - MIN_LIMIT) // STEP + RESYNC_EXTRA
        if self._scroll(-want) < want:
            return False  # interrupted: position unknown, try again later
        self.current = MIN_LIMIT
        # The first home happens as soon as the game window is in front, which is often still the main
        # menu: those scrolls change nothing (ride 2026-09-29 19:21 homed on the menu, then the padlock
        # read 25 while the count said 5, for 7 min). Don't trust it: re-home on first leaving the floor.
        self._homed = self._homes > 0
        self._homes += 1
        log.info("limit homed to %d", MIN_LIMIT)
        if self.on_home:
            self.on_home()
        return True

    def resync(self, target: int | None = None, now: float | None = None) -> bool:
        """Scroll to the floor and straight back up to `target` in one burst (about 1 s at 30 ms per
        notch), so the count is known without the rider stopping. The car dips for that second."""
        if now is not None:
            self._last_confirm_try = now
        if not self.game_focused():
            return False
        target = MIN_LIMIT if target is None else max(MIN_LIMIT, min(MAX_LIMIT, target))
        down = (MAX_LIMIT - MIN_LIMIT) // STEP + RESYNC_EXTRA
        if self._scroll(-down) < down:
            return False
        up = (target - MIN_LIMIT) // STEP
        sent = self._scroll(up)
        self.current = MIN_LIMIT + sent * STEP
        self._homed = sent == 0
        self._homes += 1
        self.confirmed = sent == up
        log.info("limit re-synced to %d%s", self.current, "" if self.confirmed else " (interrupted)")
        if self.on_home:
            self.on_home()
        return self.confirmed

    def needs_confirming(self, now: float, focused_since, riding_since) -> bool:
        """True once per run: the game has been in front and the rider pedalling for CONFIRM_AFTER_S.
        Earlier homes can all land on a loading screen or menu (ride 2026-10-04 17:42: the game was still
        loading behind an MSI Center pop-up when riding began, and the limit ran 30 mph high all ride)."""
        return (not self.confirmed and self.current is not None and focused_since is not None
                and riding_since is not None and now - focused_since >= CONFIRM_AFTER_S
                and now - riding_since >= CONFIRM_AFTER_S and now - self._last_confirm_try >= 5.0)

    def step_toward(self, desired: int, now: float, max_per_s: float) -> None:
        """Send at most one notch per call, rate-limited. Re-sync when going to the floor."""
        if self.current is None:
            self.home()
            return
        if desired == self.current or not self.game_focused():
            return
        if now - self._last_notch < 1.0 / max_per_s:
            return
        if self.current == MIN_LIMIT and desired > MIN_LIMIT and not self._homed:
            # Leaving the floor (starting to ride again): full re-home first, <1 s. Harmless when the
            # count is right (the game ignores scrolls below its floor), fixes it when notches were lost.
            self.home()
            self._last_notch = now
            return
        if desired == MIN_LIMIT and self.current - STEP == MIN_LIMIT:
            if self._scroll(-(1 + RESYNC_EXTRA)) >= 1:  # extras are ignored at the floor: exact again
                self.current = MIN_LIMIT
                self._homed = False  # only exact if no earlier notch was lost; re-home when leaving
        elif self._scroll(1 if desired > self.current else -1) == 1:
            self.current += STEP if desired > self.current else -STEP
            self._homed = False
        else:
            return  # not delivered: keep the count, retry next tick
        self._last_notch = now
        log.debug("limit -> %d", self.current)
