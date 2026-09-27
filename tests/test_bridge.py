import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.drive import DriveConfig, SpeedController  # noqa: E402
from bridge.ftms import MalformedPacket, parse_indoor_bike_data, parse_power  # noqa: E402
from bridge.mapper import MapperConfig, ThrottleMapper  # noqa: E402
from bridge.ridelog import RideLog  # noqa: E402

# Synthetic vector from the tech overview: speed 20 km/h, cadence 90 rpm, 200 W.
VECTOR = bytes.fromhex("44 00 D0 07 B4 00 C8 00")


def test_synthetic_vector():
    bike = parse_indoor_bike_data(VECTOR)
    assert bike.power_w == 200
    assert bike.speed_kmh == pytest.approx(20.0)
    assert bike.cadence_rpm == pytest.approx(90.0)
    assert parse_power(VECTOR) == 200


def test_speed_absent_when_bit0_set():
    # flags 0x0041: bit 0 set (no speed), bit 6 power = 150 W
    assert parse_indoor_bike_data(bytes.fromhex("41 00 96 00")).power_w == 150


def test_all_fields_before_power():
    # flags 0x007E: speed present (bit0 clear), avg speed, cadence, avg cadence, distance(3), resistance, power
    data = bytes.fromhex("7E 00 E8 03 E8 03 A0 00 A0 00 10 27 00 05 00 FA 00")
    bike = parse_indoor_bike_data(data)
    assert bike.power_w == 250
    assert bike.cadence_rpm == pytest.approx(80.0)


def test_negative_power_is_signed():
    assert parse_power(bytes.fromhex("41 00 F6 FF")) == -10


def test_no_power_flag():
    assert parse_power(bytes.fromhex("04 00 D0 07 B4 00")) is None


def test_truncated_packet_raises():
    with pytest.raises(MalformedPacket):
        parse_indoor_bike_data(bytes.fromhex("44 00 D0 07"))
    with pytest.raises(MalformedPacket):
        parse_indoor_bike_data(b"\x44")


@pytest.mark.parametrize(
    "raw, speed, cadence, power",
    [
        # Captured from KICKR CORE, logs/ride-20260927-085102.csv (flags always 0x0044).
        ("44 00 CA 06 60 00 31 01", 17.38, 48.0, 305),
        ("44 00 59 07 86 00 FA 00", 18.81, 67.0, 250),
        ("44 00 DA 03 44 00 29 00", 9.86, 34.0, 41),
        ("44 00 71 04 00 00 00 00", 11.37, 0.0, 0),
    ],
)
def test_captured_kickr_packets(raw, speed, cadence, power):
    bike = parse_indoor_bike_data(bytes.fromhex(raw))
    assert bike.flags == 0x0044
    assert bike.speed_kmh == pytest.approx(speed)
    assert bike.cadence_rpm == pytest.approx(cadence)
    assert bike.power_w == power


def test_mapper_linear_points():
    m = ThrottleMapper()
    m.add_sample(150, 0.0)
    assert m.throttle() == pytest.approx(0.5)
    m2 = ThrottleMapper()
    m2.add_sample(100, 0.0)
    assert m2.throttle() == pytest.approx(0.25)


def test_mapper_clamps():
    lo, hi = ThrottleMapper(), ThrottleMapper()
    lo.add_sample(20, 0.0)
    hi.add_sample(400, 0.0)
    assert lo.throttle() == 0.0
    assert hi.throttle() == 1.0


def test_mapper_smoothing_time_constant():
    m = ThrottleMapper(MapperConfig(tau_s=2.0))
    m.add_sample(0, 0.0)
    m.add_sample(100, 2.0)  # one time constant -> ~63.2% of the step
    assert m.smoothed == pytest.approx(63.2, abs=0.1)


def test_mapper_stale_drops_to_zero():
    m = ThrottleMapper()
    m.add_sample(250, 0.0)
    assert m.throttle(now=1.0) == 1.0
    assert m.throttle(now=3.5) == 0.0


def test_ride_csv_round_trip(tmp_path):
    ride = RideLog(tmp_path, "test")
    bike = parse_indoor_bike_data(VECTOR)
    ride.write(ride.start + 0.25, VECTOR, bike, 200.0, 0.75)
    ride.write(ride.start + 0.5, b"\x44", error="packet too short")
    ride.close()
    rows = list(csv.DictReader(open(ride.path, encoding="utf-8")))
    assert rows[0]["power_w"] == "200"
    assert rows[0]["flags"] == "0x0044"
    assert rows[0]["raw_hex"] == "44 00 D0 07 B4 00 C8 00"
    assert rows[1]["error"] == "packet too short"
    assert ride.bad_packets == 1


def run_for(ctl, seconds, bike_kmh, active=True, hz=20):
    ctl.set_bike_speed(bike_kmh)
    outs = []
    for _ in range(int(seconds * hz)):
        outs.append(ctl.step(1 / hz, active))
    return outs


def test_drive_settles_at_target_without_runaway():
    ctl = SpeedController()
    outs = run_for(ctl, 60, 20.0)
    target = 20.0 * ctl.config.gear_ratio
    assert outs[-1].car_est_kmh == pytest.approx(target, abs=3.0)
    assert max(o.car_est_kmh for o in outs) < target + 5.0
    # Cruise throttle ~ coast/accel, well below the cap.
    assert outs[-1].throttle < 0.3


def test_drive_throttle_ramp_and_cap():
    ctl = SpeedController()
    outs = run_for(ctl, 1.0, 40.0)
    assert outs[-1].throttle <= ctl.config.throttle_ramp_up * 1.0 + 1e-9
    outs = run_for(ctl, 10.0, 40.0)
    assert max(o.throttle for o in outs) <= ctl.config.max_throttle


def test_drive_brakes_when_bike_slows_and_never_below_floor():
    ctl = SpeedController()
    run_for(ctl, 60, 25.0)
    outs = run_for(ctl, 30, 0.0)
    assert any(o.brake > 0 for o in outs)
    for o in outs:
        if o.brake > 0:
            assert o.car_est_kmh > ctl.config.brake_floor_kmh - 1.0
            assert o.throttle == 0.0


def test_drive_brake_hold_limit():
    cfg = DriveConfig(brake_kmh_s=0.5)  # weak brake so braking lasts long
    ctl = SpeedController(cfg)
    ctl.car_est_kmh = 100.0
    outs = run_for(ctl, 6, 0.0)
    held = longest = 0
    for o in outs:
        held = held + 1 if o.brake > 0 else 0
        longest = max(longest, held)
    assert longest / 20 <= cfg.max_brake_hold_s + 0.05
    assert any(o.brake == 0 for o in outs[70:])


def test_drive_stale_coasts():
    ctl = SpeedController()
    run_for(ctl, 30, 20.0)
    outs = run_for(ctl, 5, 20.0, active=False)
    assert all(o.brake == 0 for o in outs)
    assert outs[-1].throttle == 0.0
