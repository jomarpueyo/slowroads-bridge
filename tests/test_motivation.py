"""Motivation features (research section 18): comeback, plan, feel, finish easy, journey, ghost, challenge."""

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge import motivation as mo  # noqa: E402
from bridge.coach import Coach  # noqa: E402
from bridge.companion import Companion  # noqa: E402
from test_coach import ride  # noqa: E402

TODAY = date(2026, 10, 7)  # Wednesday


def test_comeback_after_four_days():
    assert mo.comeback_days(date(2026, 10, 3), TODAY) == 4
    assert mo.comeback_days(date(2026, 10, 4), TODAY) is None
    assert mo.comeback_days(None, TODAY) is None
    c = Coach([ride(datetime(2026, 10, 1, 8))], today=TODAY)
    assert c.comeback() == 6 and "welcome back after 6 days" in c.brief_text()
    assert c.suggest()[0] == "easy"


def test_comeback_line_in_the_ride_report():
    old, back = ride(datetime(2026, 9, 28, 8)), ride(datetime(2026, 10, 6, 8))
    assert "first ride after 8 days off" in Coach([old, back], today=TODAY).ride_report(back)
    assert "Comeback" not in Coach([old, back], today=TODAY).ride_report(old)


def test_plan_parse_format_and_next_day():
    p = mo.parse_plan(["Tue", "thu", "SAT", "18:30", "35"])
    assert p.days == (1, 3, 5) and p.time == "18:30" and p.minutes == 35
    assert p.text() == "Tue Thu Sat 18:30, 35 min"
    assert p.next_day(TODAY) == date(2026, 10, 8)                 # Wednesday -> Thursday
    assert mo.plan_line(p, date(2026, 10, 8)).endswith("next ride today")
    assert mo.plan_line(p, TODAY).endswith("next ride tomorrow")
    for bad in (["18:30"], ["tue", "25:00"], ["tue", "banana"]):
        with pytest.raises(ValueError):
            mo.parse_plan(bad)
    assert mo.get_plan({"plan": {"days": [1, 9, 3], "time": "07:00", "minutes": 20}}).days == (1, 3)
    assert mo.get_plan({"plan": "nonsense"}) is None and mo.get_plan({}) is None
    assert mo.ride_minutes(None, 90, 3) == 30 and mo.ride_minutes(p, 90, 3) == 35


def test_feel_saved_and_used_by_the_coach(tmp_path):
    rides = [ride(datetime(2026, 10, d, 8)) for d in (5, 6)]
    for r, f in zip(rides, (4, 5)):
        mo.set_feel(tmp_path, r.stamp, f)
    book = json.loads((tmp_path / "ridebook.json").read_text(encoding="utf-8"))
    assert mo.feels(book) == {rides[0].stamp: 4, rides[1].stamp: 5}
    c = Coach(rides, ftp_set=180, today=TODAY, book=book)
    assert c.feel_trend() == 4.5 and c.suggest()[0] == "easy"
    assert "Feel        last rides" in c.scoreboard_text()
    with pytest.raises(ValueError):
        mo.set_feel(tmp_path, "x", 7)
    assert mo.feels({"feel": {"a": 9, "b": True, "c": 2}}) == {"c": 2}


def test_finished_easy():
    assert mo.finished_easy([150] * 600 + [80] * 120) is True
    assert mo.finished_easy([150] * 720) is False
    assert mo.finished_easy([150] * 100) is None


def test_journey_along_the_routes():
    j = mo.journey(54.6)
    assert j["route"] == "Pacific Coast Highway" and j["last"] == "Long Beach" and j["next"] == "Redondo Beach"
    assert j["to_next"] == pytest.approx(7.4)
    assert mo.journey(656 + 10)["route"] == "Route 66"
    assert mo.journey(656 + 2448 + 1)["lap"] == 2
    assert "54.6 of 656 mi" in mo.journey_line(54.6)


def test_companion_journey_messages():
    c = Companion({}, lifetime_miles=61.4, comfort_break_min=0, checkin_min=0)
    assert c.update(60, {}, 0.2) == [("", "REDONDO BEACH IN 0.4 MI")]
    assert c.update(61, {}, 0.3) == []                              # said once
    ev = c.update(120, {}, 0.7)
    assert ev and ev[0][0] == "milestone" and ev[0][1].startswith("ARRIVED: REDONDO BEACH · NEXT SANTA MONICA")


def test_ghost_and_finish_easy_messages():
    ghost = mo.Ghost([i * 0.004 for i in range(1, 4000)], "Sun 04 Oct")
    assert ghost.gap(600, 2.5) == pytest.approx(2.5 - 600 * 0.004)
    assert ghost.gap(5000, 1.0) is None
    c = Companion({}, lifetime_miles=0.0, comfort_break_min=0, checkin_min=0, ghost=ghost, finish_at_s=1800)
    ev = c.update(600, {}, 2.5)
    assert ev == [("", "VS SUN 04 OCT · +0.10 MI")]
    ev = c.update(1620, {}, 6.0)
    assert ("", "LAST 3 MIN · EASE OFF FOR A GOOD FINISH") in ev
    timeline = mo.distance_timeline([150] * 1800)
    assert 0.0 < timeline[59] < timeline[-1] and timeline[-1] == pytest.approx(30 / 60 * 30 / 1.609344, rel=0.15)


def test_ghost_is_the_last_ride_of_the_same_kind(tmp_path):
    from test_coach import _write_ride

    from bridge.ridebook import load_rides

    _write_ride(tmp_path, "20261001-080000", 900, 150)   # its bridge log says workout=tempo
    rides = load_rides(tmp_path)
    g = mo.pick_ghost(rides, "tempo", tmp_path)
    assert g is not None and g.label == "Thu 01 Oct" and len(g.timeline) >= 890
    assert mo.pick_ghost(rides, None, tmp_path) is None             # no free ride to compare with


def test_monthly_challenge(tmp_path):
    rides = [ride(datetime(2026, 10, d, 8), 40) for d in (1, 3, 5)] + [ride(datetime(2026, 9, 20, 8))]
    c = Coach(rides, today=TODAY)
    ch, progress = c.challenge()
    assert ch == {"kind": "rides", "target": 13.0, "set": False} and progress == 3
    assert c.challenge_text() == "October challenge: 3/13 rides"
    mo.set_challenge(tmp_path, "2026-10", "long", 60)
    c = Coach(rides, today=TODAY, book=mo.load_book(tmp_path))
    assert c.challenge_text() == "October challenge: one 60 min ride (longest so far 40 min)"
    mo.set_challenge(tmp_path, "2026-10", "miles", 3)
    assert Coach(rides, today=TODAY, book=mo.load_book(tmp_path)).challenge_text().endswith("DONE")
    with pytest.raises(ValueError):
        mo.set_challenge(tmp_path, "2026-10", "laps", 3)


def test_plan_cli(tmp_path, monkeypatch, capsys):
    from bridge import plan, ridelog

    monkeypatch.setattr(ridelog, "LOG_DIR", tmp_path)
    assert plan.main(["set", "tue", "sat", "07:15", "25"]) == 0
    assert mo.get_plan(mo.load_book(tmp_path)).text() == "Tue Sat 07:15, 25 min"
    assert plan.main(["set", "nope"]) == 1
    assert plan.main(["challenge", "miles", "120"]) == 0
    assert plan.main([]) == 0
    out = capsys.readouterr().out
    assert "plan: Tue Sat 07:15, 25 min" in out and "challenge" in out and "journey:" in out
    assert plan.main(["clear"]) == 0 and mo.get_plan(mo.load_book(tmp_path)) is None


def test_share_card_renders():
    pytest.importorskip("PIL")
    from bridge.sharecard import H, W, render_card

    rides = [ride(datetime(2026, 10, d, 8), 30 + d, 120 + d) for d in (1, 3, 5)]
    img = render_card(Coach(rides, ftp_set=160, today=TODAY), rides[-1])
    assert img.size == (W, H)
