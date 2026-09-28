"""Ride summary (trainer data only): metrics and edge cases."""

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge.summary import format_summary, summarize_csv, summarize_rows, write_summary  # noqa: E402


def rows(seconds, power=200, cadence=90, speed=30.0, start=0.0):
    return [{"t_s": str(start + i), "power_w": str(power), "cadence_rpm": str(cadence),
             "speed_kmh": str(speed), "error": ""} for i in range(seconds + 1)]


def test_steady_ride():
    r = summarize_rows(rows(600))
    assert r.duration_s == 600 and r.moving_s == 600
    assert r.avg_power_w == pytest.approx(200) and r.normalized_power_w == pytest.approx(200)
    assert r.distance_km == pytest.approx(5.0) and r.avg_speed_kmh == pytest.approx(30)
    assert r.work_kj == pytest.approx(120) and r.avg_cadence_rpm == pytest.approx(90)
    assert set(r.best) == {"5 s", "1 min", "5 min"} and r.best["5 min"] == pytest.approx(200)


def test_best_efforts_and_np_above_average():
    data = rows(120, power=100) + rows(10, power=600, start=121)[:10] + rows(120, power=100, start=131)
    r = summarize_rows(data)
    assert r.best["5 s"] == pytest.approx(600)
    assert r.max_power_w == 600
    assert r.normalized_power_w > r.avg_power_w


def test_pauses_are_not_counted_as_riding():
    r = summarize_rows(rows(60) + rows(60, start=600))  # 9 min gap (bridge stopped / trainer asleep)
    assert r.moving_s == pytest.approx(120)
    assert r.distance_km == pytest.approx(1.0)


def test_coasting_counts_as_moving_but_stopped_does_not():
    r = summarize_rows(rows(30, power=0, cadence=0, speed=20) + rows(30, power=0, cadence=0, speed=0, start=31))
    assert r.moving_s == pytest.approx(30, abs=1)
    assert r.avg_cadence_rpm == 0


@pytest.mark.parametrize("data", [
    [],
    rows(0),
    [{"t_s": "x", "error": ""}, {"t_s": "", "error": ""}],
    [{"t_s": "1", "error": "too short"}] * 5,
    [{}] * 3,
])
def test_no_usable_data(data):
    r = summarize_rows(data)
    assert format_summary(r).endswith("no trainer data recorded.")


def test_impossible_and_garbage_values_are_ignored():
    data = rows(40)
    data[5].update(power_w="65535", cadence_rpm="32767.5", speed_kmh="655.35")
    data[6].update(power_w="nan", cadence_rpm="inf", speed_kmh="-5")
    data[7].update(power_w="", cadence_rpm="abc", speed_kmh=None)
    r = summarize_rows(data)
    assert r.max_power_w == 200 and r.max_cadence_rpm == 90 and r.max_speed_kmh == 30
    text = format_summary(r)
    assert "nan" not in text.lower() and "inf" not in text.lower()


def test_corrupt_timestamps_do_not_hang_or_crash():
    data = rows(40) + rows(40, start=500000) + [{"t_s": t, "power_w": "100", "error": ""}
                                                 for t in ("nan", "inf", "-inf", "1e308", "-5")]
    r = summarize_rows(data)
    assert r.bad_packets == 5 and r.moving_s == pytest.approx(80)


def test_short_ride_has_no_np():
    r = summarize_rows(rows(20))
    assert r.normalized_power_w is None and "5 s" in r.best and "1 min" not in r.best
    assert "(needs 30 s)" in format_summary(r)


def test_out_of_order_and_duplicate_timestamps():
    data = rows(100)
    random.Random(3).shuffle(data)
    data += data[:10]
    r = summarize_rows(data)
    assert r.duration_s == 100 and r.avg_power_w == pytest.approx(200)


def test_fuzzed_rows_never_crash():
    rng = random.Random(11)
    junk = ["", "0", "-1", "1e308", "nan", "abc", "12.5", "\x00", "65535", "3000", "999999999999", "inf", "86400"]
    for _ in range(200):
        data = [{k: rng.choice(junk) for k in ("t_s", "power_w", "cadence_rpm", "speed_kmh", "error")}
                for _ in range(rng.randrange(0, 40))]
        format_summary(summarize_rows(data))


def test_write_summary_from_csv(tmp_path):
    csv_path = tmp_path / "ride-20260101-000000.csv"
    lines = ["t_s,wall_time,power_w,cadence_rpm,speed_kmh,smoothed_w,throttle,flags,raw_hex,error"]
    lines += [f"{i},x,150,85,28.0,150,0.6,0x0044,00,"for i in range(90)]
    lines += ["90,x,,,,,,,00,too short", "91,broken"]
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    text, out = write_summary(csv_path)
    assert out == tmp_path / "summary-20260101-000000.txt" and out.read_text(encoding="utf-8").strip() == text
    assert "avg 150 W" in text and "(1 bad)" in text
    assert summarize_csv(csv_path).packets == 92


def test_write_summary_on_binary_garbage(tmp_path):
    csv_path = tmp_path / "ride-20260101-000001.csv"
    csv_path.write_bytes(bytes(random.Random(5).getrandbits(8) for _ in range(4000)))
    text, _ = write_summary(csv_path)
    assert "no trainer data" in text
