"""Calibration fit logic, checked against a synthetic car with known rates (no game, no OCR)."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

calibrate = pytest.importorskip("calibrate_car")
from bridge import settings  # noqa: E402
from bridge.drive import DriveConfig, step_car  # noqa: E402

pytest.importorskip("speedo")  # needs the calibration extras (mss, winrt OCR)
from speedo import parse_speed  # noqa: E402

# Roughly the car measured 2026-09-27 09:20 (automatic).
TRUE = DriveConfig(accel_kmh_s=55.0, coast_kmh_s=5.0, drag_quad=0.0027, brake_kmh_s=20.0, top_speed_kmh=93.0)
DELAY_S = 0.3


def synthetic_samples(hz=10, noise=0.0):
    import random

    rng = random.Random(1)
    v, t, out, history = 0.0, 0.0, [], []
    for name, start, end, brk, secs in calibrate.SEQUENCE:
        n = int(secs * hz)
        for i in range(n):
            trig = start + (end - start) * i / n
            history.append((trig, brk))
            thr_d, brk_d = history[max(0, len(history) - 1 - int(DELAY_S * hz))]
            v = step_car(v, thr_d, brk_d, 1 / hz, TRUE)
            t += 1 / hz
            shown = max(v + rng.uniform(-noise, noise), 0.0) if v > 0 else 0.0  # HUD: exact 0 when stopped
            out.append((t, name, trig, brk, shown))
    return out


def test_fit_recovers_known_car():
    res = calibrate.fit(synthetic_samples())
    assert res["accel"] == pytest.approx(TRUE.accel_kmh_s, rel=0.1)
    assert res["coast"] == pytest.approx(TRUE.coast_kmh_s, abs=1.5)
    assert res["drag_quad"] == pytest.approx(TRUE.drag_quad, rel=0.25)
    assert res["brake_rate"] == pytest.approx(TRUE.brake_kmh_s, rel=0.25)
    assert res["top_speed"] == pytest.approx(TRUE.top_speed_kmh, abs=1.0)
    assert res["delay_s"] == pytest.approx(DELAY_S, abs=0.15)


def test_fit_replays_accurately_with_noise():
    samples = synthetic_samples(noise=0.3)
    res = calibrate.fit(samples)
    assert calibrate.model_error(samples, res) < 3.0


def test_solve_linear_system():
    assert calibrate.solve([[2, 1], [1, 3]], [3, 5]) == pytest.approx([0.8, 1.4])


def test_settings_round_trip_keeps_other_keys(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"ride": {"gear": 2.5}}), encoding="utf-8")
    settings.save({"accel": 50.0, "top_speed": 90.0, "bogus": 1}, {"source": "x"}, path)
    loaded = settings.load(path)
    assert loaded == {"gear": 2.5, "accel": 50.0, "top_speed": 90.0}
    assert settings.load(tmp_path / "missing.json") == {}


@pytest.mark.parametrize(
    "text, shown, unit, gear",
    [
        ("0.0 | MILES PER HOUR | 1 | GEAR", 0.0, "mph", "1"),
        ("0.0 | MILES PER | 1 | GEAR", 0.0, "mph", "1"),  # OCR dropped HOUR (live, 2026-09-27)
        ("42.7 | MILES PER HOUR | 4 | GEAR", 42.7, "mph", "4"),
        ("63 , 5 | KILOMETRES PER HOUR | N | GEAR", 63.5, "km/h", "N"),
        # Live misreads from calibration run 2026-09-27 09:13:
        ("74.2 | NILES PER '-OUR | 2 | GEAR | cAj", 74.2, "mph", "2"),
        ("75.2 | RILES PER HOUR | 2 | GEAR", 75.2, "mph", "2"),
        ("73.2 | BULES PER I-mJR | GEAR", 73.2, "mph", None),
        ("53.1 | mlL€s PER | 2 | GEAR", 53.1, "mph", "2"),
        ("48.5 | NIL-ES PER | 21 | GEAR", 48.5, "mph", None),
        ("0.0 | MILES PER YOUR | 1 | GEAR", 0.0, "mph", "1"),
    ],
)
def test_parse_hud(text, shown, unit, gear):
    r = parse_speed(text)
    assert (r.speed, r.unit, r.gear) == (shown, unit, gear)
