"""Fuzzed simulation: the real bridge loop (dry run) against hostile trainer data must never crash and
must always keep its outputs safe. Longer campaigns: python tools/fuzz_bridge.py --minutes 10"""

import pytest

from fuzz_harness import check_outputs, run_fuzz


@pytest.mark.parametrize("kind", ["garbage", "extreme", "flood", "silence", "mixed"])
def test_limit_mode_survives_fuzzed_data(tmp_path, kind):
    result = run_fuzz(kind, seed=1234, seconds=1.5, log_dir=tmp_path)
    assert check_outputs(result) == []


def test_limit_mode_survives_dropouts(tmp_path):
    result = run_fuzz("dropout", seed=7, seconds=5.0, log_dir=tmp_path)
    assert check_outputs(result) == []


@pytest.mark.parametrize("mode,source", [("speed", "trainer"), ("power", "trainer"), ("limit", "power"),
                                         ("limit", "trainer")])
def test_other_modes_survive_mixed_data(tmp_path, mode, source):
    result = run_fuzz("mixed", seed=99, seconds=1.5, log_dir=tmp_path, mode=mode, speed_source=source)
    assert check_outputs(result, mode=mode) == []


def test_impossible_readings_do_not_reach_the_controller():
    import struct

    from bridge.__main__ import plausible
    from bridge.ftms import parse_indoor_bike_data

    bike = parse_indoor_bike_data(struct.pack("<HHHh", 0x0044, 65535, 13796, -10059))
    clean = plausible(bike)
    assert (clean.speed_kmh, clean.cadence_rpm, clean.power_w) == (None, None, None)
    ok = parse_indoor_bike_data(struct.pack("<HHHh", 0x0044, 3000, 180, 250))
    assert plausible(ok) is ok


def test_repeated_runs_release_their_log_files(tmp_path):
    import shutil

    for i in range(2):
        d = tmp_path / str(i)
        run_fuzz("mixed", seed=i, seconds=0.5, log_dir=d)
        shutil.rmtree(d)  # fails on Windows if the event log is still open
