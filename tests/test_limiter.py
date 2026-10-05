import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.drive import bike_speed_from_power  # noqa: E402
from bridge.limiter import MIN_LIMIT, LimitConfig, SpeedLimitPlanner, WheelActuator  # noqa: E402
from bridge.sim import packet, parse_profile, speed_at  # noqa: E402
from bridge.ftms import parse_indoor_bike_data  # noqa: E402


def mph(x):
    return x * 1.609344


def test_planner_steps_of_five_with_hysteresis():
    p = SpeedLimitPlanner(LimitConfig(units="mph", hysteresis=1.5))
    assert p.update(mph(30), True) == (30, 0.6)
    assert p.update(mph(33.5), True)[0] == 30   # inside hysteresis: stay
    assert p.update(mph(34.5), True)[0] == 35   # clearly past 32.5 + 1.5
    assert p.update(mph(31.5), True)[0] == 35   # not yet below 32.5 - 1.5
    assert p.update(mph(30.5), True)[0] == 30


def test_planner_default_rounds_near_nearest_step():
    p = SpeedLimitPlanner()
    p.update(mph(25), True)
    assert p.update(mph(27.6), True)[0] == 25  # within 27.5 + 0.25
    assert p.update(45.0, True)[0] == 30       # 45 km/h (bike 15 x 3) = 27.96 mph -> 30
    assert p.update(mph(27.4), True)[0] == 30  # not yet below 27.5 - 0.25


def test_planner_stopped_or_stale_goes_to_floor_with_no_throttle():
    p = SpeedLimitPlanner()
    p.update(mph(40), True)
    assert p.update(1.0, True) == (MIN_LIMIT, 0.0)
    p.update(mph(40), True)
    assert p.update(mph(40), False) == (MIN_LIMIT, 0.0)


def test_planner_kmh_units_and_cap():
    p = SpeedLimitPlanner(LimitConfig(units="km/h"))
    assert p.update(50, True)[0] == 50
    assert p.update(500, True)[0] == 125


def test_actuator_counts_notches_rate_limited_and_resyncs(monkeypatch):
    a = WheelActuator(dry_run=True)
    sent = []
    monkeypatch.setattr(a, "_scroll", lambda n: (sent.append(n), abs(n))[1])
    a.step_toward(30, 0.0, 4.0)          # first call homes
    assert a.current == MIN_LIMIT and sent[-1] < -24
    t = 1.0
    for _ in range(10):
        a.step_toward(30, t, 4.0)
        t += 0.25
    assert a.current == 30
    a.step_toward(30, t + 0.01, 4.0)
    assert a.current == 30               # no change once there
    a.step_toward(25, t + 1.0, 4.0)      # one notch down
    before = len(sent)
    a.step_toward(10, t + 1.05, 4.0)     # rate limit: too soon after that notch
    assert len(sent) == before and a.current == 25
    t += 1.0
    for _ in range(10):
        t += 0.3
        a.step_toward(MIN_LIMIT, t, 4.0)
    assert a.current == MIN_LIMIT
    assert sent[-1] == -5                # last step to the floor over-scrolls to re-sync


def test_power_speed_reference_values():
    assert bike_speed_from_power(100) == pytest.approx(25.9, abs=0.3)
    assert bike_speed_from_power(200) == pytest.approx(33.8, abs=0.3)
    assert bike_speed_from_power(0) == 0.0
    assert bike_speed_from_power(200, grade=0.04) < bike_speed_from_power(200)


def test_sim_profile_and_packets():
    prof = parse_profile("0:0,10:20,20:20")
    assert speed_at(prof, 5) == pytest.approx(10)
    assert speed_at(prof, 30) == 20
    bike = parse_indoor_bike_data(packet(15.5, 80, 120))
    assert (bike.speed_kmh, bike.cadence_rpm, bike.power_w) == (pytest.approx(15.5), 80.0, 120)


def test_coast_hold_freezes_limit_with_margin_and_coast_throttle():
    p = SpeedLimitPlanner(LimitConfig(coast_hold_s=12, coast_margin=5, coast_throttle=0.05, release_step_s=2))
    assert p.update(mph(40), True, True, 0.0) == (40, 0.6)
    # stop pedalling: flywheel speed decays fast, but the limit holds at 40 + 5
    assert p.update(mph(30), True, False, 1.0) == (45, 0.05)
    assert p.update(mph(5), True, False, 12.9) == (45, 0.05)
    assert p.state == "coasting"


def test_coast_releases_gently_then_stays_stopped():
    p = SpeedLimitPlanner(LimitConfig(coast_hold_s=12, coast_margin=5, release_step_s=2, coast_throttle=0.0))
    p.update(mph(40), True, True, 0.0)
    p.update(mph(40), True, False, 0.0)                       # coast from 45
    assert p.update(mph(10), True, False, 12.0) == (40, 0.0)  # first release step (test sets 2 s steps, coast thr 0)
    assert p.update(mph(10), True, False, 14.0) == (35, 0.0)
    assert p.update(mph(10), True, False, 30.0) == (MIN_LIMIT, 0.0)
    # flywheel still turning but no pedalling: must not go back to drive throttle
    assert p.update(mph(10), True, False, 31.0) == (MIN_LIMIT, 0.0)


def test_pedalling_again_holds_limit_during_resume_grace_then_steps_down_gently():
    # Ride 12:48 t=244 s: resuming after a coast dropped the limit 40 -> 25 at once.
    p = SpeedLimitPlanner(LimitConfig(coast_hold_s=12, resume_grace_s=8, down_step_s=2.5))
    p.update(mph(40), True, True, 0.0)
    p.update(mph(40), True, False, 1.0)                        # coasting at 45
    assert p.update(mph(26), True, True, 5.0) == (45, 0.6)      # resume: held at the coast limit
    assert p.state == "riding"
    assert p.update(mph(26), True, True, 12.9)[0] == 45         # still in the 8 s grace
    assert p.update(mph(26), True, True, 13.1)[0] == 40         # grace over: one step
    assert p.update(mph(26), True, True, 14.0)[0] == 40         # not again until 2.5 s later
    assert p.update(mph(26), True, True, 15.6)[0] == 35


def test_resume_grace_still_allows_stepping_up():
    p = SpeedLimitPlanner(LimitConfig(coast_hold_s=12))
    p.update(mph(40), True, True, 0.0)
    p.update(mph(40), True, False, 1.0)                        # coasting at 45
    assert p.update(mph(55), True, True, 3.0)[0] == 55          # watts built fast: go up



def test_is_pedalling_uses_power_or_cadence():
    p = SpeedLimitPlanner()
    assert p.is_pedalling(0, 82)       # ride 11:39 t=135 s: a 0 W packet mid-pedalling at 82 rpm
    assert p.is_pedalling(120, 0)
    assert not p.is_pedalling(0, 0)
    assert not p.is_pedalling(None, None)


def test_sim_profile_power_override():
    from bridge.sim import watts_at
    prof = parse_profile("0:0,10:20:150,20:15:0,30:0")
    assert watts_at(prof, 5) is None
    assert watts_at(prof, 12) == 150
    assert watts_at(prof, 25) == 0


def test_coast_hold_zero_restores_old_following():
    p = SpeedLimitPlanner(LimitConfig(coast_hold_s=0))
    p.update(mph(40), True, True, 0.0)
    assert p.update(mph(30), True, False, 1.0) == (35, 0.6)   # follows the target (one step at a time)
    assert p.update(mph(30), True, False, 3.6) == (30, 0.6)
    assert p.state == "riding"


def test_virtual_bike_steady_state_matches_physics():
    from bridge.drive import VirtualBike
    vb = VirtualBike()
    for _ in range(20 * 120):          # 2 min at 20 Hz, 200 W
        vb.step(200, 0.05)
    assert vb.kmh == pytest.approx(bike_speed_from_power(200), abs=0.5)


def test_virtual_bike_is_smooth_between_1hz_readings():
    from bridge.drive import VirtualBike
    vb = VirtualBike()
    for _ in range(20 * 60):
        vb.step(100, 0.05)
    speeds = [vb.step(300, 0.05) for _ in range(20)]   # power jumps 100 -> 300 W for one second
    steps = [b - a for a, b in zip(speeds, speeds[1:])]
    assert all(0 < s < 0.2 for s in steps)               # rises a little every tick, never a jump


def test_virtual_bike_coasts_gently():
    from bridge.drive import VirtualBike
    vb = VirtualBike()
    for _ in range(20 * 120):
        vb.step(150, 0.05)
    v0 = vb.kmh
    for _ in range(20):
        vb.step(0, 0.05)
    assert 0.3 < v0 - vb.kmh < 1.5                       # ~0.7-1 km/h lost in the first second


def test_release_keeps_coast_throttle_until_low_and_steps_every_4s_by_default():
    p = SpeedLimitPlanner()
    p.update(mph(40), True, True, 0.0)
    p.update(mph(40), True, False, 0.0)                       # coast from 45
    assert p.update(0, True, False, 12.0) == (40, 0.05)
    assert p.update(0, True, False, 15.9) == (40, 0.05)       # still the first step
    assert p.update(0, True, False, 16.0) == (35, 0.05)
    assert p.update(0, True, False, 32.0) == (15, 0.05)
    assert p.update(0, True, False, 36.0) == (10, 0.0)        # last 10 mph: throttle off to stop


def test_push_factor_ramps_from_easy_to_hard():
    p = SpeedLimitPlanner()
    assert p.push_factor(100) == 1.0
    assert p.push_factor(150) == 1.0
    assert p.push_factor(275) == pytest.approx(1.25)
    assert p.push_factor(400) == pytest.approx(1.5)
    assert p.push_factor(900) == pytest.approx(1.5)
    assert SpeedLimitPlanner(LimitConfig(push_boost=0)).push_factor(900) == 1.0


def test_pushing_steps_up_as_soon_as_target_passes_the_step():
    normal, pushing = SpeedLimitPlanner(), SpeedLimitPlanner()
    for p in (normal, pushing):
        p.update(mph(30), True)
    assert normal.update(mph(31), True)[0] == 30                 # needs > 32.75 normally
    assert pushing.update(mph(31), True, pushing=True)[0] == 35  # > 30.25 is enough when pushing


def test_push_throttle_rises_to_full_at_full_push():
    p = SpeedLimitPlanner()
    assert p.push_throttle(0.6, 1.0) == 0.6
    assert p.push_throttle(0.6, 1.25) == pytest.approx(0.8)
    assert p.push_throttle(0.6, 1.5) == pytest.approx(1.0)


def test_step_down_is_rate_limited_while_riding():
    p = SpeedLimitPlanner(LimitConfig(down_step_s=2.5))
    p.update(mph(50), True, True, 0.0)
    assert p.update(mph(20), True, True, 1.0)[0] == 45
    assert p.update(mph(20), True, True, 2.0)[0] == 45
    assert p.update(mph(20), True, True, 3.5)[0] == 40



class FakeWin:
    """Stand-in for bridge.gamewin: a game window that can be covered or lose focus."""

    def __init__(self):
        self.game, self.focused, self.covered, self.cover_after = 1, True, False, None
        self.checks = 0

    def is_game(self, hwnd):
        return hwnd == self.game

    def find_game_window(self):
        return self.game

    def game_focused(self):
        return self.focused

    def safe_scroll_point(self, game):
        return None if self.covered else (100, 100)

    def top_level_at(self, x, y):
        self.checks += 1
        if self.cover_after is not None and self.checks > self.cover_after:
            return 99  # another window slid over the point mid-scroll
        return self.game


def real_actuator(win, monkeypatch):
    from bridge import limiter
    a = WheelActuator(win=win)
    a.dry_run = False

    class U:
        def GetCursorPos(self, p): pass
        def SetCursorPos(self, x, y): pass
        def mouse_event(self, *a): pass
    a._user32 = U()
    monkeypatch.setattr(limiter.time, "sleep", lambda s: None)
    return a


def test_actuator_does_not_scroll_or_count_when_game_is_covered(monkeypatch):
    win = FakeWin()
    a = real_actuator(win, monkeypatch)
    assert a.home() and a.home() and a.current == MIN_LIMIT   # second home: trusted, no re-home on leaving
    win.covered = True
    a.step_toward(30, 10.0, 4.0)
    assert a.current == MIN_LIMIT          # not delivered -> count unchanged
    win.covered = False
    a.step_toward(30, 10.1, 4.0)
    assert a.current == 10


def test_actuator_stops_counting_when_focus_leaves(monkeypatch):
    win = FakeWin()
    a = real_actuator(win, monkeypatch)
    a.home()
    win.focused = False
    a.step_toward(30, 10.0, 4.0)
    assert a.current == MIN_LIMIT


def test_interrupted_homing_leaves_position_unknown(monkeypatch):
    win = FakeWin()
    win.cover_after = 5                    # covered after 5 of the ~28 homing notches
    a = real_actuator(win, monkeypatch)
    assert a.home() is False and a.current is None


def test_burst_riding_does_not_ratchet_the_coast_limit():
    # Ride 2026-09-29 02:28 min 6-9: 3-5 s hard bursts, 2-4 s coasts. Each coast inside the resume
    # grace added +5 again: 30 -> 100 mph at a steady ~35 mph target.
    p = SpeedLimitPlanner(LimitConfig(coast_hold_s=12, resume_grace_s=8, coast_margin=5))
    t = 0.0
    p.update(mph(35), True, True, t)
    seen = []
    for _ in range(20):
        for _ in range(4):
            t += 1.0
            seen.append(p.update(mph(35), True, True, t)[0])
        for _ in range(3):
            t += 1.0
            seen.append(p.update(mph(30), True, False, t)[0])
    assert max(seen) == 40                                      # 35 + one margin, never stacked


def test_coast_after_the_grace_uses_the_new_riding_limit():
    p = SpeedLimitPlanner(LimitConfig(coast_hold_s=12, resume_grace_s=8))
    p.update(mph(35), True, True, 0.0)
    p.update(mph(35), True, False, 1.0)                          # coast at 40
    p.update(mph(50), True, True, 3.0)                           # really faster now: 50
    assert p.update(mph(50), True, True, 20.0)[0] == 50
    assert p.update(mph(45), True, False, 21.0)[0] == 55         # grace over: margin on the new limit


class GameModel:
    """The game's real limit, driven by wheel events. While a menu is open, the wheel scrolls the menu."""

    def __init__(self, limit):
        self.limit, self.menu_open = limit, False

    def mouse_event(self, flags, x, y, delta, extra):
        if not self.menu_open:
            self.limit = min(125, max(MIN_LIMIT, self.limit + (5 if delta > 0 else -5)))

    def GetCursorPos(self, p): pass
    def SetCursorPos(self, x, y): pass


def test_notches_lost_to_a_game_menu_are_fixed_when_riding_resumes(monkeypatch):
    # Ride 2026-09-29 02:42: the settings menu was open while the bridge scrolled 20 -> 5 mph; the
    # padlock stayed at 20 and every limit was 15 mph high for 9 minutes.
    a = real_actuator(FakeWin(), monkeypatch)
    game = GameModel(limit=60)
    a._user32 = game
    t = 0.0
    a.step_toward(20, t, 4.0)                                    # homes first
    while a.current != 20:
        t += 0.3
        a.step_toward(20, t, 4.0)
    assert game.limit == 20
    game.menu_open = True
    while a.current != MIN_LIMIT:                                # bridge steps down; the menu eats it
        t += 0.3
        a.step_toward(MIN_LIMIT, t, 4.0)
    assert game.limit == 20                                      # drifted: game 20, bridge 5
    game.menu_open = False
    for _ in range(40):                                          # rider starts pedalling again
        t += 0.3
        a.step_toward(30, t, 4.0)
    assert a.current == 30 and game.limit == 30                  # re-homed on leaving the floor


def test_no_rehome_while_riding_between_steps(monkeypatch):
    a = real_actuator(FakeWin(), monkeypatch)
    sent = []
    monkeypatch.setattr(a, "_scroll", lambda n: (sent.append(n), abs(n))[1])
    a.step_toward(30, 0.0, 4.0)                                  # initial home
    t = 0.0
    for _ in range(20):
        t += 0.3
        a.step_toward(30, t, 4.0)
    for _ in range(20):
        t += 0.3
        a.step_toward(20, t, 4.0)
    homes = [i for i, n in enumerate(sent) if abs(n) > 1]
    assert homes == [0, 1]              # start-up home, then one re-home on first leaving the floor; no more




def test_start_up_home_on_the_main_menu_is_fixed_before_riding(monkeypatch):
    # Ride 2026-09-29 19:21: the bridge started the game and homed while the main menu was showing; the
    # scrolls changed nothing, and the padlock read 25 while the count said 5 for 7 minutes.
    a = real_actuator(FakeWin(), monkeypatch)
    game = GameModel(limit=25)
    game.menu_open = True
    a._user32 = game
    t = 0.0
    a.step_toward(MIN_LIMIT, t, 4.0)                             # start-up home: eaten by the menu
    assert a.current == MIN_LIMIT and game.limit == 25
    game.menu_open = False                                       # "continue": on the road
    for _ in range(40):                                          # first pedal strokes
        t += 0.3
        a.step_toward(35, t, 4.0)
    assert a.current == 35 and game.limit == 35


def test_confirming_resync_fixes_a_count_lost_while_the_game_loaded(monkeypatch):
    # Ride 2026-10-04 17:42: the game was still loading (an MSI Center pop-up on top) through both the
    # start-up home and the first departure from the floor; the padlock read 45 while the count said 15,
    # and with no stop all ride the limit stayed 30 mph high.
    a = real_actuator(FakeWin(), monkeypatch)
    game = GameModel(limit=45)
    game.menu_open = True                                        # loading: wheel does nothing
    a._user32 = game
    t = 0.0
    a.step_toward(MIN_LIMIT, t, 4.0)                             # start-up home (eaten)
    for _ in range(10):                                          # rider starts: re-home (eaten), climb
        t += 0.3
        a.step_toward(20, t, 4.0)
    game.menu_open = False                                       # now on the road; climbs reach the game
    game.limit = 45 + 0                                          # still 45: the earlier notches were lost
    for _ in range(8):
        t += 0.3
        a.step_toward(35, t, 4.0)
    assert a.current == 35 and game.limit != 35                  # drifted
    focused_since = riding_since = 1.0
    assert not a.needs_confirming(15.0, focused_since, riding_since)   # not yet 20 s
    assert a.needs_confirming(21.5, focused_since, riding_since)
    assert a.resync(35, now=21.5)
    assert a.current == 35 and game.limit == 35 and a.confirmed
    assert not a.needs_confirming(60.0, focused_since, riding_since)   # once per run


def test_resync_retries_are_spaced_and_wait_for_focus(monkeypatch):
    win = FakeWin()
    a = real_actuator(win, monkeypatch)
    a.home()
    win.focused = False
    assert not a.resync(30, now=25.0) and not a.confirmed
    assert not a.needs_confirming(27.0, 0.0, 0.0)                # retry no sooner than 5 s
    win.focused = True
    assert a.needs_confirming(30.5, 0.0, 0.0)
    assert not a.needs_confirming(30.5, None, 0.0)               # game not in front
    assert not a.needs_confirming(30.5, 0.0, None)               # not riding
