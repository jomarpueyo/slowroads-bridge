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
