"""Ride overlay: live stats, FTP zones, the FTP estimate and rendering (no window needed)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge.overlay import LiveStats, Overlay, render, zone  # noqa: E402
from bridge.summary import estimate_ftp, summarize_rows  # noqa: E402


def test_rolling_averages_and_full_windows():
    s = LiveStats()
    s.start(0.0)
    for t in range(0, 700):
        s.add(float(t), 100 if t < 400 else 200, 85)
    now = 699.0
    one, full1 = s.avg(60, now)
    five, full5 = s.avg(300, now)
    ten, full10 = s.avg(600, now)
    assert one == pytest.approx(200) and full1
    assert five == pytest.approx(200) and full5
    assert ten == pytest.approx(150, abs=1) and full10
    assert len(s.samples) <= 1201                     # only the last 20 min is kept


def test_averages_wait_for_the_first_pedal_stroke_and_mark_partial_windows():
    s = LiveStats()
    s.add(0.0, 0, 0)
    assert s.avg(60, 0.0) == (None, False)
    s.start(10.0)
    for t in range(10, 40):
        s.add(float(t), 150, 80)
    value, full = s.avg(60, 39.0)
    assert value == pytest.approx(144, abs=6) and not full   # 30 s into a 1 min window: dimmed


def test_distance_and_snapshot_units():
    s = LiveStats()
    s.add_distance(5.0)                                # before starting: ignored
    s.start(0.0)
    s.add_distance(1.609344)
    snap = s.snapshot(90.0, 200, False, "mph", False)
    assert snap["distance"] == pytest.approx(1.0) and snap["dist_label"] == "MILES" and snap["elapsed"] == 90
    assert s.snapshot(90.0, 200, False, "km/h", False)["distance"] == pytest.approx(1.609344)


@pytest.mark.parametrize("watts,name", [(100, "Z1"), (140, "Z2"), (170, "Z3"), (200, "Z4"), (230, "Z5"),
                                        (280, "Z6"), (400, "Z7")])
def test_zones(watts, name):
    assert zone(watts, 200)[0] == name


def test_no_zone_without_ftp_or_power():
    assert zone(200, 0) is None and zone(None, 200) is None


def test_ftp_estimate_is_95_percent_of_best_20_min():
    ride = summarize_rows([{"t_s": str(i), "power_w": "200" if i < 1300 else "100", "error": ""}
                           for i in range(1500)])
    assert ride.best["20 min"] == pytest.approx(200)
    assert estimate_ftp([(None, ride, None)]) == 190
    short = summarize_rows([{"t_s": str(i), "power_w": "300", "error": ""} for i in range(600)])
    assert estimate_ftp([(None, short, None)]) is None


@pytest.mark.parametrize("paused,ftp,power", [(False, 200, 186), (True, 95, None), (False, 0, 50)])
def test_render_produces_a_transparent_panel(paused, ftp, power):
    pytest.importorskip("PIL")
    snap = {"elapsed": 3725, "distance": 12.345, "dist_label": "MILES", "power": power, "cadence": None,
            "avgs": [("1 MIN", 182, True), ("5 MIN", None, False), ("10 MIN", 158, False)],
            "ftp": ftp, "ftp_estimated": ftp == 95, "paused": paused}
    img = render(snap, 1.0)
    assert img.mode == "RGBA" and 250 < img.size[0] < 600 and 60 < img.size[1] < 200
    alpha = img.getchannel("A")
    assert alpha.getpixel((0, 0)) == 0 and alpha.getextrema()[1] > 200   # see-through box, solid text
    assert render(snap, 2.0).size[0] > img.size[0] * 1.7                 # scales with the game window


def test_overlay_disabled_does_nothing():
    ov = Overlay(lambda: {}, enabled=False)
    ov.start()
    ov.toggle()
    ov.stop()
    assert ov.hwnd is None and not ov.visible
