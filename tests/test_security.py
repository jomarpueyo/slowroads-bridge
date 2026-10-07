"""Regression tests for docs/SECURITY.md findings."""

import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from bridge import settings  # noqa: E402
from bridge.ftms import FTMS_SERVICE, trainer_filter  # noqa: E402


def dev(address, name=""):
    return SimpleNamespace(address=address, name=name)


def adv(uuids=(), local_name=""):
    return SimpleNamespace(service_uuids=list(uuids), local_name=local_name)


# Finding 1: trainer spoofing / pinning
def test_pinned_trainer_only_accepts_its_own_address():
    f = trainer_filter("de:ad:be:ef:00:01")
    assert f(dev("DE:AD:BE:EF:00:01", "KICKR CORE"), adv([FTMS_SERVICE]))
    assert not f(dev("11:22:33:44:55:66", "KICKR CORE"), adv([FTMS_SERVICE]))  # imposter with FTMS + name


def test_pairing_requires_ftms_unless_name_is_explicit():
    f = trainer_filter()
    assert f(dev("11:22:33:44:55:66"), adv([FTMS_SERVICE]))
    assert not f(dev("11:22:33:44:55:66", "KICKR CORE"), adv())   # name alone is not enough
    assert trainer_filter(name_hint="KICKR")(dev("11:22:33:44:55:66", "KICKR CORE"), adv())


def test_trainer_address_round_trip_and_validation(tmp_path):
    p = tmp_path / "settings.json"
    settings.save_trainer("aa:bb:cc:dd:ee:ff", "KICKR", p)
    assert settings.load_trainer(p) == "AA:BB:CC:DD:EE:FF"
    with pytest.raises(ValueError):
        settings.save_trainer("not-an-address", "x", p)
    p.write_text(json.dumps({"trainer": {"address": "../../x"}}), encoding="utf-8")
    assert settings.load_trainer(p) is None
    p.write_text(json.dumps({"trainer": "x"}), encoding="utf-8")
    assert settings.load_trainer(p) is None


# Finding 5: settings validation
@pytest.mark.parametrize("content", ["", "{", "[]", "null", '{"ride": 5}', '{"ride": []}', "\x00\x01"])
def test_malformed_settings_do_not_crash(tmp_path, content):
    p = tmp_path / "settings.json"
    p.write_text(content, encoding="utf-8")
    assert settings.load(p) == {}
    assert settings.load_trainer(p) is None


def test_settings_reject_non_finite_and_non_numbers(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"ride": {"gear": NaN, "accel": Infinity, "coast": "x", "ramp": true, "top_speed": 90}}',
                 encoding="utf-8")
    assert settings.load(p) == {"top_speed": 90.0}


def test_settings_save_keeps_other_sections(tmp_path):
    p = tmp_path / "settings.json"
    settings.save_trainer("AA:BB:CC:DD:EE:FF", "KICKR", p)
    settings.save({"gear": 2.5, "accel": float("nan")}, {"source": "t"}, p)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["trainer"]["address"] == "AA:BB:CC:DD:EE:FF"
    assert data["ride"] == {"gear": 2.5}


# Finding 3: ride-stamp path traversal (recorder)
def test_ride_stamp_validation():
    rr = pytest.importorskip("ride_recorder")
    assert rr.valid_stamp("20260927-122345\n") == "20260927-122345"
    for bad in ("..\\..\\..\\escaped", "../x", "20260927-122345/..", "", "2026092-12345", "a" * 100):
        assert rr.valid_stamp(bad) is None


# Finding 8: test tools
def test_csv_safe_and_ocr_tab():
    speedo = pytest.importorskip("speedo")
    assert speedo.csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert speedo.csv_safe("-831.2") == "'-831.2"
    assert speedo.csv_safe("0.0 | MILES PER") == "0.0 | MILES PER"
    speedo.parse_speed("NPma8E6\t,3PER")  # used to raise ValueError


def test_control_chain_stays_bounded_for_any_int16_power():
    from bridge.drive import DriveConfig, SpeedController, VirtualBike
    from bridge.limiter import SpeedLimitPlanner

    for watts in (32767, -32768, 0, 1):
        vb, ctl, pl = VirtualBike(), SpeedController(DriveConfig(gear_ratio=2.0, top_speed_kmh=0)), SpeedLimitPlanner()
        for k in range(20 * 20):
            push = pl.push_factor(watts)
            ctl.set_bike_speed(vb.step(watts, 0.05) * push)
            tgt = ctl.step(0.05, True).target_kmh
            lim, thr = pl.update(tgt, True, pl.is_pedalling(watts, 80), k * 0.05, pushing=push > 1.05)
            thr = pl.push_throttle(thr, push)
            assert 5 <= lim <= 125 and 0.0 <= thr <= 1.0 and math.isfinite(tgt)


# ------------------------------------------------------------------ finding 9: ERG targets from a bad FTP

def test_out_of_range_ftp_is_ignored_and_erg_is_capped():
    from bridge import trainer as tr
    from bridge.coach import Coach
    assert Coach([], ftp_set=250).ftp() == (250.0, "set")
    for typo in (2500, 5000, 10, -100):          # ignored: no rides, so no estimate either
        assert Coach([], ftp_set=typo).ftp() == (0.0, None)
    want = tr.decide(enabled=True, workout_status={"target": (3000, 3400)}, paused=False, pedalling=True,
                     cadence=85, low_cadence_s=0.0, gravel=False, road_feel=True, erg=True, texture=None, now=0.0)
    assert want.mode == "erg" and want.watts == tr.ERG_MAX_W
