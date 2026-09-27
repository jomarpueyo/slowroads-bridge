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
    monkeypatch.setattr(a, "_scroll", lambda n: sent.append(n))
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


def test_pedalling_again_resumes_from_target():
    p = SpeedLimitPlanner(LimitConfig(coast_hold_s=12))
    p.update(mph(40), True, True, 0.0)
    p.update(mph(40), True, False, 1.0)
    assert p.update(mph(30), True, True, 5.0) == (30, 0.6)
    assert p.state == "riding"


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
    assert p.update(mph(30), True, False, 1.0) == (30, 0.6)   # follows the target, no freeze
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
