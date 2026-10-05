"""Ride book, coach, workouts, companion and dashboard."""

import asyncio
import csv
import struct
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge import ridebook  # noqa: E402
from bridge.coach import Coach, next_milestone  # noqa: E402
from bridge.companion import Companion  # noqa: E402
from bridge.dashboard import render_dashboard  # noqa: E402
from bridge.ridebook import Ride, load_rides, power_curve  # noqa: E402
from bridge.summary import summarize_rows  # noqa: E402
from bridge.workouts import KEYS, WorkoutRunner, build  # noqa: E402

TODAY = date(2026, 10, 7)  # a Wednesday


def ride(when: datetime, minutes: float = 30, watts: float = 120, np_=None, cad=85, workout=None,
         curve=None, fade=0.0) -> Ride:
    secs = minutes * 60
    c = curve or {d: watts * (1 + 0.6 * (60 / max(d, 60)) ** 0.5 if d < 60 else 1 + 0.15 * 60 / d)
                  for d in ridebook.DURATIONS if d <= secs}
    return Ride(stamp=when.strftime("%Y%m%d-%H%M%S"), start=when.isoformat(), moving_s=secs,
                distance_km=secs / 3600 * 28, trainer_km=secs / 3600 * 20, work_kj=watts * secs / 1000,
                avg_w=watts, np_w=np_ or watts * 1.05, max_w=watts * 3, avg_cad=cad, curve=c,
                first_half_w=watts, second_half_w=watts * (1 + fade), low_share=0.05,
                power_hist={int(watts // 10): int(secs)}, cadence_hist={int(cad // 10): int(secs)},
                longest_steady_s=int(secs * 0.6), coasts=3, workout=workout)


# --------------------------------------------------------------------- ride book

def test_power_curve_is_best_rolling_average():
    series = [100] * 300 + [400] * 10 + [100] * 300
    c = power_curve(series)
    assert c[5] == 400 and c[15] == pytest.approx((400 * 10 + 100 * 5) / 15)
    assert c[300] == pytest.approx((400 * 10 + 100 * 290) / 300)
    assert 1200 not in c and power_curve([]) == {}


def test_efforts_count_coasts_and_longest_steady_stretch():
    series = [150] * 600 + [0] * 15 + [150] * 300 + [0] * 5 + [150] * 100
    longest, coasts = ridebook._efforts(series)
    assert coasts == 1                       # the 5 s dip is not a coast
    assert longest == 600


def test_idle_time_at_the_ends_is_trimmed():
    rows = [{"t_s": str(i), "power_w": "0", "cadence_rpm": "0", "speed_kmh": "0", "error": ""} for i in range(600)]
    rows += [{"t_s": str(600 + i), "power_w": "150", "cadence_rpm": "85", "speed_kmh": "25", "error": ""}
             for i in range(300)]
    r = summarize_rows(rows)
    assert len(r.series) == 299 and r.low_power_share == 0 and r.normalized_power_w == pytest.approx(150)


def _write_ride(log_dir: Path, stamp: str, seconds: int, watts: int, sim: bool = False):
    path = log_dir / f"ride-{stamp}.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "power_w", "cadence_rpm", "speed_kmh", "error"])
        for i in range(seconds + 1):
            w.writerow([i, watts, 85, 30, ""])
    (log_dir / f"bridge-{stamp}.log").write_text(
        f"x INFO bridge: start dry_run=False sim={'0:0' if sim else 'None'} workout=tempo ftp=0(None)\n")
    return path


def test_load_rides_caches_hides_and_notes(tmp_path):
    _write_ride(tmp_path, "20261001-080000", 600, 150)
    _write_ride(tmp_path, "20261002-080000", 900, 200)
    _write_ride(tmp_path, "20261003-080000", 600, 300, sim=True)       # scripted: never counts
    _write_ride(tmp_path, "20261004-080000", 30, 300)                  # too short
    rides = load_rides(tmp_path)
    assert [r.stamp for r in rides] == ["20261001-080000", "20261002-080000"]
    assert rides[0].workout == "tempo" and rides[1].curve[300] == pytest.approx(200)
    assert (tmp_path / "ride-index.json").exists()
    t = time.perf_counter()
    assert len(load_rides(tmp_path)) == 2 and time.perf_counter() - t < 0.5   # from the cache
    ridebook.set_hidden(tmp_path, "20261001-080000", True)
    ridebook.set_note(tmp_path, "20261002-080000", "new saddle")
    assert [r.stamp for r in load_rides(tmp_path)] == ["20261002-080000"]
    everything = load_rides(tmp_path, include_hidden=True)
    assert everything[0].hidden and everything[1].note == "new saddle"
    (tmp_path / "ride-20261002-080000.csv").unlink()                  # deleted ride leaves the index
    assert [r.stamp for r in load_rides(tmp_path, include_hidden=True)] == ["20261001-080000"]
    assert "new saddle" not in ridebook.format_list(load_rides(tmp_path, include_hidden=True))


# --------------------------------------------------------------------- coach

def test_ftp_set_beats_estimate_and_tss():
    rides = [ride(datetime(2026, 10, 5, 8), 40, 150, curve={1200: 200})]
    assert Coach(rides, today=TODAY).ftp() == (190, "estimate")
    c = Coach(rides, ftp_set=200, today=TODAY)
    assert c.ftp() == (200, "set")
    hour_at_ftp = ride(datetime(2026, 10, 6, 8), 60, 200, np_=200)
    assert c.tss(hour_at_ftp) == pytest.approx(100)
    assert Coach([], today=TODAY).ftp() == (0.0, None)


def test_training_load_rises_with_riding_and_form_dips():
    rides = [ride(datetime(2026, 10, d, 8), 60, 200, np_=200) for d in range(1, 8)]
    ctl, atl, tsb = Coach(rides, ftp_set=200, today=TODAY).load()
    assert atl > ctl > 0 and tsb < 0
    assert len(Coach(rides, ftp_set=200, today=TODAY).load_series(30)) == 7


def test_week_goal_and_streak():
    rides = [ride(datetime(2026, 10, 5, 8)), ride(datetime(2026, 10, 6, 8)), ride(datetime(2026, 10, 7, 8)),
             ride(datetime(2026, 9, 29, 8), 100),                     # last week: minutes goal met
             ride(datetime(2026, 9, 22, 8), 20)]                      # two weeks ago: not met
    c = Coach(rides, weekly_rides=3, weekly_minutes=90, today=TODAY)
    assert c.week(0)["rides"] == 3 and c.goal_met(c.week(0))
    assert c.streak_weeks() == 2
    assert c.days_since_last() == 0


def test_records_and_new_records():
    old = ride(datetime(2026, 10, 1, 8), 30, 120)
    new = ride(datetime(2026, 10, 6, 8), 45, 150)
    c = Coach([old, new], today=TODAY)
    recs = c.new_records(new)
    assert any("best 1 min" in r for r in recs) and any("longest ride" in r for r in recs)
    assert c.new_records(old) == []                                   # nothing before the first ride
    assert c.records()["longest"] is new


def test_focus_areas_and_strengths_from_ride_data():
    rides = [ride(datetime(2026, 10, d, 8), 25, 120, np_=140, cad=68, fade=-0.15) for d in (1, 3, 5)]
    keys = [k for k, _ in Coach(rides, today=TODAY).focus_areas()]
    for k in ("endurance", "durability", "cadence", "pacing", "ftp"):
        assert k in keys, k
    good = [ride(datetime(2026, 10, d, 8), 70, 160, np_=165, cad=88) for d in (5, 6, 7)]
    c = Coach(good, ftp_set=170, today=TODAY)
    assert "cadence" not in [k for k, _ in c.focus_areas()] and any("cadence" in s for s in c.strengths())


def test_suggestions_follow_the_week_and_fatigue():
    few = [ride(datetime(2026, 10, d, 8)) for d in (1, 2, 3)]
    assert Coach(few, today=TODAY).suggest()[0] == "ramp"              # no FTP yet
    c = Coach(few, ftp_set=180, today=date(2026, 10, 9))              # Friday, no long ride yet
    assert c.suggest()[0] == "long"
    tired = [ride(datetime(2026, 10, d, 8), 90, 220, np_=230) for d in range(1, 8)]
    assert Coach(tired, ftp_set=180, today=TODAY).suggest()[0] == "easy"
    assert Coach([ride(datetime(2026, 10, 6, 8), 35)], today=TODAY).long_ride_target_min() == 40


def test_text_outputs():
    rides = [ride(datetime(2026, 10, d, 8), 30 + d, 120 + d) for d in range(1, 7)]
    c = Coach(rides, ftp_set=160, today=TODAY)
    board = c.scoreboard_text()
    for part in ("Lifetime", "This week", "Records", "Form", "Next"):
        assert part in board
    assert "suggested:" in c.brief_text()
    report = c.ride_report(rides[-1])
    assert "TSS" in report and "Zones" in report and "longest steady" in report
    assert "No rides yet" in Coach([], today=TODAY).scoreboard_text()
    assert next_milestone(0) == 10 and next_milestone(49.9) == 50 and next_milestone(1100) == 1250


def test_dashboard_renders_without_internet_resources():
    rides = [ride(datetime(2026, 10, d, 8), 30 + d, 120 + d, workout="tempo") for d in range(1, 7)]
    page = render_dashboard(Coach(rides, ftp_set=160, today=TODAY))
    assert page.startswith("<!doctype html>") and "<svg" in page and "Ride book" in page
    assert "http://" not in page and "https://" not in page and "<script" not in page
    assert "prefers-color-scheme:dark" in page
    empty = render_dashboard(Coach([], today=TODAY))
    assert "No power curve yet" in empty


# --------------------------------------------------------------------- workouts

@pytest.mark.parametrize("key", KEYS)
def test_every_workout_builds(key):
    w = build(key, 200, 40)
    assert w.blocks and w.seconds > 0 and w.title
    assert all(b.seconds > 0 for b in w.blocks)


def test_long_ride_matches_its_target_with_stand_up_breaks():
    w = build("long", 200, 40)
    assert abs(w.seconds - 40 * 60) <= 60
    assert sum(1 for b in w.blocks if b.name == "Stand, stretch & drink") >= 1


def test_runner_blocks_targets_and_events():
    w = build("tempo", 200)
    r = WorkoutRunner(w, 200)
    status, events = r.update(0, 100, 85)
    assert status["block"] == "Warm up" and status["target"] == (90, 120) and status["state"] == "ok"
    assert events and events[0][0] == "block"
    status, events = r.update(10 * 60 + 1, 140, 85)
    assert status["block"] == "Tempo 1/3" and status["state"] == "low" and events[0][0] == "block"
    status, _ = r.update(10 * 60 + 5, 190, 85)
    assert status["state"] == "high"
    _, events = r.update(w.seconds / 2 + 1, 160, 85)
    assert ("milestone", "HALFWAY") in events
    _, events = r.update(w.seconds - 299, 100, 85)
    assert ("milestone", "LAST 5 MINUTES") in events
    status, events = r.update(w.seconds + 1, 100, 85)
    assert status is None and r.done and events[0][0] == "workout_done"


def test_ramp_test_ends_on_failure_and_gives_ftp():
    w = build("ramp", 160)
    r = WorkoutRunner(w, 160)
    t, best = 0, 0
    while not r.done and t < w.seconds:
        _, b, _ = r._block_at(t)
        target = b.watts if b and b.watts else 100
        power = target if t < 5 * 60 + 8 * 60 else target - 60      # can't hold step 9
        r.update(t, power, 90)
        t += 1
    assert r.done and r.result_ftp is not None
    assert r.result_ftp == round(r.best_minute * 0.75)
    assert 120 < r.result_ftp < 200


# --------------------------------------------------------------------- companion

def test_companion_breaks_records_and_milestones():
    c = Companion({60: 200.0, 300: 150.0}, lifetime_miles=48.0, comfort_break_min=20, checkin_min=15)
    assert c.update(60, {60: 180, 300: None, 1200: None}, 0.5) == []
    ev = c.update(15 * 60, {60: 205, 300: None, 1200: None}, 2.1)
    messages = [m for _, m in ev]
    assert "15 MIN DONE · DRINK WATER" in messages and any(m.startswith("NEW BEST 1 MIN") for m in messages)
    assert ("drink", "15 MIN DONE · DRINK WATER") in ev
    assert ("milestone", "50 LIFETIME MILES") in ev
    assert not any(m.startswith("NEW BEST 1 MIN") for _, m in c.update(15 * 60 + 2, {60: 206}, 2.2))  # once
    ev = c.update(20 * 60, {}, 3.0)
    assert ("break", "STAND UP & STRETCH · 30 S") in ev
    assert ("break", "STAND UP & STRETCH · 30 S") not in Companion({}, 0, 20).update(40 * 60, {}, 0, True)
    off = Companion({}, 0, comfort_break_min=0, checkin_min=0)
    assert off.update(3600, {}, 0) == []


def test_drink_reminder_rides_with_the_check_ins_and_merges_with_breaks():
    c = Companion({}, 0, comfort_break_min=20, checkin_min=15)
    assert c.update(15 * 60, {}, 0) == [("drink", "15 MIN DONE · DRINK WATER")]
    assert c.update(20 * 60, {}, 0) == [("break", "STAND UP & STRETCH · 30 S")]
    assert c.update(30 * 60, {}, 0) == [("drink", "30 MIN DONE · DRINK WATER")]
    c.update(40 * 60, {}, 0)
    c.update(45 * 60, {}, 0)
    assert c.update(60 * 60, {}, 0) == [("break", "60 MIN · STAND UP & STRETCH · DRINK WATER")]   # one, not two
    quiet = Companion({}, 0, comfort_break_min=0, checkin_min=15, drink=False)
    assert quiet.update(15 * 60, {}, 0) == [("", "15 MIN DONE")]


# --------------------------------------------------------------------- through the bridge

def test_coached_ride_through_the_bridge(tmp_path, monkeypatch):
    import bridge.__main__ as bm
    from bridge import settings

    monkeypatch.setattr(settings, "load", lambda *a: {})
    saved = []
    monkeypatch.setattr(settings, "save_pref", lambda k, v, *a, **kw: saved.append((k, v)))
    _write_ride(tmp_path, "20261001-080000", 900, 150)

    async def source(on_packet, on_state):
        on_state("connected")
        for _ in range(12):
            on_packet(struct.pack("<HHHh", 0x0044, 2500, 170, 160))
            await asyncio.sleep(0.25)

    args = bm.parse_args(["--dry-run", "--log-dir", str(tmp_path), "--no-sounds", "--no-hotkeys",
                          "--workout", "endurance", "--weekly-rides", "4"])
    asyncio.run(bm.run(args, source_fn=source))
    log_text = next(tmp_path.glob("bridge-2*.log")).read_text(encoding="utf-8")
    newest = sorted(tmp_path.glob("bridge-*.log"))[-1].read_text(encoding="utf-8")
    assert "workout=endurance" in newest
    assert "coach: RIDE 1 OF 4 THIS WEEK" in newest or "coach: RIDE" in newest
    assert "coach: WARM UP" in newest
    assert log_text
