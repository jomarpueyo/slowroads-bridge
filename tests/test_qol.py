"""Quality-of-life features: sound cues, hotkeys, auto-end, ride totals, log cleanup, saved gear."""

import asyncio
import csv
import os
import struct
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bridge.__main__ as bm  # noqa: E402
from bridge import settings  # noqa: E402
from bridge.cleanup import old_entries, remove  # noqa: E402
from bridge.cues import TONES, Cues  # noqa: E402
from bridge.hotkeys import KEYS, Hotkeys  # noqa: E402
from bridge.summary import compare_line, format_totals, ride_history, summarize_rows  # noqa: E402


# --- sound cues

def test_cues_play_in_order_off_the_caller_thread():
    heard = []
    c = Cues(beep=lambda f, ms: heard.append((f, ms)))
    c.play("connected")
    c.play("ride_end")
    c.play("not-a-cue")
    c.close()
    assert heard == TONES["connected"] + TONES["ride_end"]


def test_cues_disabled_and_broken_audio_are_silent():
    heard = []
    c = Cues(enabled=False, beep=lambda f, ms: heard.append(f))
    c.play("connected")
    c.close()
    assert heard == []

    def broken(f, ms):
        raise RuntimeError("no audio device")

    c = Cues(beep=broken)
    c.play("connected")
    c.close()  # must not raise


# --- hotkeys

def test_hotkeys_fire_once_per_press_and_only_with_the_game_in_front():
    down = set()
    h = Hotkeys(key_down=lambda vk: vk in down)
    assert h.poll(True) == []
    down.add(KEYS["F7"])
    assert h.poll(True) == ["gear_up"]
    assert h.poll(True) == []                     # held: no repeat
    down.clear()
    h.poll(True)
    down.add(KEYS["F8"])
    assert h.poll(False) == []                    # game not in front: ignored
    assert h.poll(True) == []                     # still the same press
    down.clear()
    h.poll(True)
    down.update({KEYS["F6"], KEYS["F9"]})
    assert sorted(h.poll(True)) == ["gear_down", "resync"]
    assert Hotkeys(enabled=False, key_down=lambda vk: True).poll(True) == []


# --- settings

def test_save_pref_round_trip_keeps_other_values(tmp_path):
    path = tmp_path / "settings.json"
    settings.save_trainer("AA:BB:CC:DD:EE:FF", "KICKR", path=path)
    settings.save_pref("gear", 2.25, path=path)
    settings.save_pref("idle_end", 5, path=path)
    assert settings.load(path) == {"gear": 2.25, "idle_end": 5.0}
    assert settings.load_trainer(path) == "AA:BB:CC:DD:EE:FF"
    for bad in (("gear", float("nan")), ("gear", True), ("evil", 1.0), ("gear", "3")):
        with pytest.raises(ValueError):
            settings.save_pref(*bad, path=path)


def test_comfort_preferences_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "load", lambda *a: {"sounds": 0.0, "idle_end": 7.0, "keep_days": 0.0})
    args = bm.parse_args([])
    assert not args.sounds and args.idle_end == 7.0 and args.keep_days == 0.0 and args.hotkeys
    assert bm.parse_args(["--no-hotkeys", "--idle-end", "0"]).hotkeys is False


# --- log cleanup

def test_cleanup_removes_only_old_bulky_entries(tmp_path):
    now = datetime(2026, 9, 29, 12, 0, 0)
    old, new = "20260801-100000", "20260928-100000"
    for name in (f"drive-{old}.csv", f"speed-{old}.csv", f"bridge-{old}.log", f"ride-{old}.csv",
                 f"summary-{old}.txt", f"drive-{new}.csv", "active-ride.txt", "notes.txt",
                 f"crash-bridge-{old}.txt"):
        (tmp_path / name).write_text("x")
    (tmp_path / f"shots-{old}").mkdir()
    (tmp_path / f"shots-{old}" / "a.jpg").write_bytes(b"j")
    (tmp_path / f"shots-{new}").mkdir()
    doomed = old_entries(tmp_path, 30, now=now)
    assert sorted(p.name for p in doomed) == sorted([f"drive-{old}.csv", f"speed-{old}.csv", f"bridge-{old}.log",
                                                     f"shots-{old}", f"crash-bridge-{old}.txt"])
    assert remove(doomed) == 5
    left = sorted(p.name for p in tmp_path.iterdir())
    assert f"ride-{old}.csv" in left and f"summary-{old}.txt" in left and f"shots-{new}" in left
    assert old_entries(tmp_path, 0, now=now) == []                        # 0 = never
    (tmp_path / f"drive-{old}.csv").write_text("x")
    assert old_entries(tmp_path, 30, now=now, active=old) == []            # never the running ride


# --- ride totals

def _ride_csv(path: Path, seconds: int, watts: int, sim: bool = False):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "power_w", "cadence_rpm", "speed_kmh", "error"])
        for i in range(seconds + 1):
            w.writerow([i, watts, 85, 30, ""])
    log = path.with_name(path.name.replace("ride-", "bridge-")).with_suffix(".log")
    log.write_text(f"x INFO bridge: start dry_run=False mode=limit sim={'0:0,10:20' if sim else 'None'}\n")


def test_ride_history_skips_sims_and_false_starts(tmp_path):
    _ride_csv(tmp_path / "ride-20260927-080000.csv", 600, 100)
    _ride_csv(tmp_path / "ride-20260928-080000.csv", 600, 300, sim=True)
    _ride_csv(tmp_path / "ride-20260929-080000.csv", 30, 200)
    _ride_csv(tmp_path / "ride-20260929-090000.csv", 1200, 150)
    hist = ride_history(tmp_path)
    assert [p.name for _, _, p in hist] == ["ride-20260927-080000.csv", "ride-20260929-090000.csv"]
    text = format_totals(hist, "All rides")
    assert "Rides       2" in text and "30:00 moving" in text and "240 kJ" in text
    assert ride_history(tmp_path, since=datetime(2026, 9, 29)) [0][2].name == "ride-20260929-090000.csv"
    assert ride_history(tmp_path, exclude=tmp_path / "ride-20260929-090000.csv")[-1][2].name == "ride-20260927-080000.csv"
    assert format_totals([], "Last 7 days") == "Last 7 days: no rides."


def test_compare_line():
    a = summarize_rows([{"t_s": str(i), "power_w": "150", "error": ""} for i in range(1201)])
    b = summarize_rows([{"t_s": str(i), "power_w": "100", "error": ""} for i in range(601)])
    assert compare_line(a, b) == "vs last ride: time +10 min, avg power +50 W, work +120 kJ"
    assert "time -10 min" in compare_line(b, a)


# --- the bridge loop: hotkeys, pause, auto-end

def pkt(watts, cadence=80, kmh=30.0):
    return struct.pack("<HHHh", 0x0044, int(kmh * 100), int(cadence * 2), watts)


class ScriptedHotkeys:
    """Fires actions at given seconds after the ride starts."""

    script: list = []

    def __init__(self, enabled=True):
        self.enabled, self.t0, self.done = enabled, time.monotonic(), set()

    def poll(self, focused):
        t = time.monotonic() - self.t0
        fired = [a for i, (at, a) in enumerate(self.script) if at <= t and i not in self.done]
        self.done.update(i for i, (at, _) in enumerate(self.script) if at <= t)
        return fired


def ride(tmp_path, monkeypatch, seconds, watts_at, extra=(), script=()):
    ScriptedHotkeys.script = list(script)
    monkeypatch.setattr(bm, "Hotkeys", ScriptedHotkeys)
    saved = []
    monkeypatch.setattr(settings, "save_pref", lambda k, v, *a, **kw: saved.append((k, v)))

    async def source(on_packet, on_state):
        on_state("connected")
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            w = watts_at(time.monotonic() - t0)
            on_packet(pkt(w, cadence=80 if w else 0))
            await asyncio.sleep(0.2)

    args = bm.parse_args(["--dry-run", "--log-dir", str(tmp_path), "--no-sounds", *extra])
    t0 = time.monotonic()
    asyncio.run(bm.run(args, source_fn=source))
    rows = list(csv.DictReader(open(next(tmp_path.glob("drive-*.csv")), encoding="utf-8")))
    return time.monotonic() - t0, rows, saved, args


def test_gear_hotkeys_change_and_save_the_gear(tmp_path, monkeypatch):
    _, _, saved, args = ride(tmp_path, monkeypatch, 1.0, lambda t: 200,
                             script=[(0.2, "gear_up"), (0.4, "gear_up"), (0.6, "gear_down")])
    assert saved == [("gear", 2.25), ("gear", 2.5), ("gear", 2.25)]
    assert args.gear == 2.25


def test_pause_holds_throttle_at_zero_and_leaves_the_limit(tmp_path, monkeypatch):
    _, rows, _, _ = ride(tmp_path, monkeypatch, 2.0, lambda t: 250,
                         script=[(0.5, "pause"), (1.3, "pause")])
    paused = [r for r in rows if r["plan"] == "paused"]
    assert paused and all(float(r["throttle"]) == 0 and float(r["trigger"]) == 0 for r in paused)
    assert rows[-1]["plan"] != "paused"                                  # resumed


def test_ride_ends_itself_after_idle_minutes(tmp_path, monkeypatch):
    # pedal 0.6 s, then 0 W / 0 rpm; --idle-end 0.02 min = 1.2 s. The source would run 20 s.
    took, _, _, _ = ride(tmp_path, monkeypatch, 20.0, lambda t: 200 if t < 0.6 else 0,
                         extra=["--idle-end", "0.02"])
    assert took < 6


def test_idle_end_waits_for_the_first_pedal_stroke(tmp_path, monkeypatch):
    took, _, _, _ = ride(tmp_path, monkeypatch, 3.0, lambda t: 0, extra=["--idle-end", "0.01"])
    assert took >= 2.9                                                    # never pedalled: no auto-end
