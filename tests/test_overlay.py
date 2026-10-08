"""Ride overlay: live stats, FTP zones, the FTP estimate and rendering (no window needed)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge.overlay import LiveStats, Overlay, render, zone  # noqa: E402
from bridge.summary import summarize_rows  # noqa: E402


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


def test_best_20_min_feeds_the_ftp_estimate():
    ride = summarize_rows([{"t_s": str(i), "power_w": "200" if i < 1300 else "100", "error": ""}
                           for i in range(1500)])
    assert ride.best["20 min"] == pytest.approx(200)
    short = summarize_rows([{"t_s": str(i), "power_w": "300", "error": ""} for i in range(600)])
    assert "20 min" not in short.best


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


def _snap(elapsed, miles=0.0, power=120, cad=85, paused=False, avg5=120):
    return {"elapsed": elapsed, "distance": miles, "dist_label": "MILES", "power": power, "cadence": cad,
            "avgs": [("1 MIN", 120, True), ("5 MIN", avg5, True), ("10 MIN", 120, True)],
            "ftp": 150, "ftp_estimated": False, "paused": paused}


def test_attention_fades_after_the_start_but_never_off():
    from bridge.overlay import Attention

    a = Attention(fade_after=20, dim=0.35, fade_s=3)
    assert a.apply(_snap(5), 5)["alpha"]["power"] == 1.0          # start: bright
    assert a.apply(_snap(21.5), 21.5)["alpha"]["power"] == pytest.approx(0.675)   # fading
    s = a.apply(_snap(40), 40)
    assert s["alpha"]["power"] == pytest.approx(0.35) and s["hot"] == set()        # calm: dim, not off


def test_attention_lights_up_only_the_number_that_matters():
    from bridge.overlay import Attention

    a = Attention(fade_after=20)
    t = 0.0
    for el in range(0, 59):
        a.apply(_snap(el), t + el)
    s = a.apply(_snap(60.5), 60.5)                                 # a whole minute
    assert s["alpha"]["time"] == 1.0 and s["alpha"]["power"] == pytest.approx(0.35)
    a.apply(_snap(70, miles=0.99), 70)
    s = a.apply(_snap(71, miles=1.01), 71)                         # a mile
    assert s["alpha"]["distance"] == 1.0 and "distance" in s["hot"]
    s = a.apply(_snap(90, miles=1.1, power=200, avg5=120), 90)     # surge
    assert "power" in s["hot"] and s["alpha"]["ftp"] == 1.0
    s = a.apply(_snap(91, miles=1.1, power=125, cad=104), 91)      # high cadence
    assert "cadence" in s["hot"]
    s = a.apply(_snap(115, miles=1.2, power=125, cad=85), 115)     # all calm again
    assert s["hot"] == set() and max(s["alpha"].values()) < 1.0


def test_attention_bright_while_paused_and_off_switch():
    from bridge.overlay import Attention

    a = Attention(fade_after=20)
    a.apply(_snap(100), 100)
    assert all(v == 1.0 for v in a.apply(_snap(130, paused=True), 130)["alpha"].values())
    assert a.apply(_snap(140), 140)["alpha"]["power"] == 1.0       # just resumed: a few seconds bright
    never = Attention(fade_after=0).apply(_snap(500), 500)
    assert "alpha" not in never


def test_render_uses_per_number_brightness():
    pytest.importorskip("PIL")
    bright = render(_snap(600), 1.0)
    faded = render({**_snap(600), "alpha": {k: 0.35 for k in ("time", "distance", "power", "cadence", "avgs", "ftp")},
                    "hot": set()}, 1.0)
    assert bright.size == faded.size
    assert faded.getchannel("A").getextrema()[1] < bright.getchannel("A").getextrema()[1]
    hot = render({**_snap(600), "alpha": {}, "hot": {"power"}}, 1.0)
    assert hot.size == bright.size
