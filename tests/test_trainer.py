"""Trainer resistance (FTMS control) and road detection from the game's saved settings."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge import trainer as tr  # noqa: E402
from bridge.gamestate import RoadWatcher, read_world, surface_of  # noqa: E402

KICKR_FEATURES = bytes.fromhex("03 40 00 00 0c 60 00 00")  # read from the rider's KICKR CORE, 2026-10-05


# ------------------------------------------------------------------ encoding

def test_command_bytes_follow_ftms():
    assert tr.request_control() == b"\x00"
    assert tr.reset() == b"\x01"
    assert tr.start() == b"\x07"
    assert tr.stop() == b"\x08\x01" and tr.stop(pause=True) == b"\x08\x02"
    assert tr.target_power(160) == bytes.fromhex("05 a0 00")
    assert tr.target_power(5000) == bytes.fromhex("05 d0 07")        # capped at 2000 W
    assert tr.target_power(-20) == bytes.fromhex("05 00 00")
    # wind 0, grade 0, Crr 0.0040 -> 40, Cw 0.20 -> 20
    assert tr.simulation() == bytes.fromhex("11 00 00 00 00 28 14")
    # grade -2.5 % -> -250 (0xff06), Crr 0.0100 -> 100
    assert tr.simulation(grade_pct=-2.5, crr=0.010) == bytes.fromhex("11 00 00 06 ff 64 14")
    assert tr.simulation(crr=1.0, cw=9.0)[-2:] == bytes([255, 255])  # clamped to the field range


def test_responses_and_features():
    assert tr.parse_response(bytes.fromhex("80 00 01")) == (0x00, "success")
    assert tr.parse_response(bytes.fromhex("80 05 05")) == (0x05, "control not permitted")
    assert tr.parse_response(bytes.fromhex("80 11 7f"))[1].startswith("reserved")
    assert tr.parse_response(b"\x01") is None
    assert tr.parse_features(KICKR_FEATURES) == {"resistance", "power", "simulation", "wheel circumference"}
    assert tr.parse_features(b"\x00") == set()


# ------------------------------------------------------------------ policy

def _decide(**kw):
    base = dict(enabled=True, workout_status=None, paused=False, pedalling=True, cadence=85, low_cadence_s=0.0,
                gravel=False, road_feel=True, erg=True, texture=None, now=0.0)
    base.update(kw)
    return tr.decide(**base)


def test_erg_holds_workout_targets_and_road_feel_otherwise():
    assert _decide() == tr.Want("sim", crr=tr.TARMAC_CRR)
    w = _decide(workout_status={"target": (150, 175)})
    assert w.mode == "erg" and w.watts == 160                     # middle of the range, rounded to 5 W
    assert _decide(workout_status={"target": (205, 215)}).watts == 210   # ramp step: exact
    assert _decide(workout_status={"target": None}).mode == "sim"         # stand & stretch block


def test_erg_lets_go_when_it_should():
    target = {"target": (150, 175)}
    assert _decide(workout_status=target, paused=True).mode == "sim"
    assert _decide(workout_status=target, pedalling=False).mode == "sim"
    assert _decide(workout_status=target, low_cadence_s=3.5).mode == "sim"   # no ERG spiral
    assert _decide(workout_status=target, erg=False).mode == "sim"
    assert _decide(workout_status=target, road_feel=False, erg=False).mode == "none"
    assert _decide(enabled=False, workout_status=target).mode == "none"


def test_gravel_feel_and_texture_stay_in_bounds():
    assert _decide(gravel=True).crr == tr.GRAVEL_CRR
    t = tr.Texture(tr.GRAVEL_CRR, amount=0.3, seed=4)
    values = [t.crr(i * 0.1) for i in range(2000)]
    assert min(values) >= tr.GRAVEL_CRR * 0.7 - 1e-9 and max(values) <= tr.GRAVEL_CRR * 1.6 + 1e-9
    assert len({round(v, 5) for v in values}) > 20                # it does wander
    assert any(v > tr.GRAVEL_CRR * 1.5 for v in values)            # and bumps now and then
    assert tr.Texture(tr.GRAVEL_CRR, amount=0).crr(5.0) == tr.GRAVEL_CRR


# ------------------------------------------------------------------ controller against a fake KICKR

class FakeKickr:
    def __init__(self, grant=True, features=KICKR_FEATURES):
        self.writes, self.grant, self.features, self.cb = [], grant, features, None
        self.indications_on = False

    async def read_gatt_char(self, uuid):
        assert uuid == tr.FEATURE
        return self.features

    async def start_notify(self, uuid, cb):
        assert uuid == tr.CONTROL_POINT
        self.cb, self.indications_on = cb, True

    async def write_gatt_char(self, uuid, data, response=True):
        assert uuid == tr.CONTROL_POINT and response and self.indications_on   # indications before writes
        self.writes.append(bytes(data))
        result = 0x05 if (data[0] == 0x00 and not self.grant) else 0x01   # refuse control, or succeed
        self.cb(None, bytes([0x80, data[0], result]))


def _run(coro):
    return asyncio.run(coro)


def test_controller_takes_control_then_applies_and_releases(monkeypatch):
    async def go():
        kickr = FakeKickr()
        events = []
        c = tr.TrainerControl(on_event=lambda m, label: events.append(m))
        await c.attach(kickr)
        c.set_want(tr.Want("erg", watts=160))
        await c.step()
        assert kickr.writes == [b"\x00", b"\x07", bytes.fromhex("05 a0 00")]
        await c.step()                                            # same want: nothing more
        assert len(kickr.writes) == 3
        c.set_want(tr.Want("sim", crr=tr.TARMAC_CRR))
        await c.step()                                            # rate limit: too soon after the last write
        assert len(kickr.writes) == 3
        c._last_write -= 5
        await c.step()
        assert kickr.writes[-1] == tr.simulation()
        await c.release()
        assert kickr.writes[-1] == b"\x01" and events == ["erg", "sim"]
    _run(go())


def test_controller_respects_a_trainer_that_says_no():
    async def go():
        kickr = FakeKickr(grant=False)
        c = tr.TrainerControl()
        await c.attach(kickr)
        c.set_want(tr.Want("erg", watts=160))
        await c.step()
        assert kickr.writes == [b"\x00"] and not c.has_control    # asked, refused, sent nothing else
        await c.step()
        assert kickr.writes == [b"\x00"]                           # backs off for 15 s
        await c.release()
        assert kickr.writes == [b"\x00"]                           # never controlled: nothing to release
    _run(go())


def test_controller_skips_unsupported_modes_and_reconnects():
    async def go():
        kickr = FakeKickr(features=bytes.fromhex("00 00 00 00 00 20 00 00"))   # simulation only
        c = tr.TrainerControl()
        await c.attach(kickr)
        c.set_want(tr.Want("erg", watts=160))
        await c.step()
        assert kickr.writes == []                                 # no ERG support: leave it alone
        c.set_want(tr.Want("sim"))
        await c.step()
        assert kickr.writes[-1] == tr.simulation()
        c.detach()                                                 # link dropped
        assert c.client is None and not c.has_control
        again = FakeKickr()
        await c.attach(again)
        c._last_write -= 5
        await c.step()
        assert again.writes[:2] == [b"\x00", b"\x07"]               # control requested again after reconnect
    _run(go())


def test_disabled_controller_never_touches_the_trainer():
    async def go():
        kickr = FakeKickr()
        c = tr.TrainerControl(enabled=False)
        await c.attach(kickr)
        c.set_want(tr.Want("erg", watts=200))
        await c.step()
        await c.release()
        assert kickr.writes == [] and not kickr.indications_on
    _run(go())


def test_bluetooth_errors_never_stop_the_ride():
    class Broken(FakeKickr):
        async def write_gatt_char(self, *a, **k):
            raise OSError("GATT error")

    async def go():
        c = tr.TrainerControl()
        await c.attach(Broken())
        c.set_want(tr.Want("sim"))
        task = asyncio.ensure_future(c.run())
        await asyncio.sleep(0.6)
        assert not task.done()                                     # still running despite the errors
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    _run(go())


# ------------------------------------------------------------------ the game's road

def _save(ts: int, lane: int, scene: int = 1) -> bytes:
    world = (f'{{"seed":"85203eef","scene":{scene},"roadStyle":1,"laneStyle":{lane},"roadWidth":0,'
             f'"startNode":0,"accumulatedDistance":0}}')
    return f'{{"version":"1.0.2","ts":{ts},"settings":{{"World":{world},"Traffic":{{"density":2}}}}}}'.encode()


def test_read_world_takes_the_newest_save_and_ignores_garbage(tmp_path):
    (tmp_path / "000005.ldb").write_bytes(b"\x00\x81junk" + _save(1790527074407, 4) + b"\xff\x00")
    (tmp_path / "000118.log").write_bytes(_save(1791254668524, 1) + b"\x01\x02" + _save(1791254532234, 3)
                                          + b'"ts":1791254999999,"settings":{"World":{"seed":"x"\x80}')
    (tmp_path / "LOCK").write_bytes(b"")
    ts, world = read_world(tmp_path)
    assert ts == 1791254668524 and world["laneStyle"] == 1 and surface_of(world) == "gravel"
    assert read_world(tmp_path / "missing") is None


@pytest.mark.parametrize("lane,surface", [(0, "gravel"), (1, "gravel"), (2, "tarmac"), (3, "tarmac"),
                                          (4, "tarmac"), (None, None), ("1", None), (True, None)])
def test_surface_of_lane_styles(lane, surface):
    assert surface_of({"laneStyle": lane}) == surface


def test_road_watcher_reports_changes_only(tmp_path):
    log = tmp_path / "000118.log"
    log.write_bytes(_save(1791254000000, 4))
    w = RoadWatcher(tmp_path, interval_s=15)
    assert w.poll(0.0) == "tarmac"
    assert w.poll(5.0) is None                                     # too soon
    assert w.poll(20.0) is None                                    # nothing changed
    log.write_bytes(log.read_bytes() + _save(1791254100000, 1))   # the game saved a dirt track
    assert w.poll(40.0) == "gravel" and w.surface == "gravel"
    assert w.poll(60.0) is None


def test_bridge_wires_resistance_in_a_normal_ride(tmp_path, monkeypatch, capsys):
    """Not a dry run (so control is on), with the controller/scrolling replaced: the ride runs, the banner
    explains the resistance, and the bridge asks for ERG during the workout's target block."""
    import struct
    import time as _time

    import bridge.__main__ as bm
    from bridge import settings
    from bridge.limiter import WheelActuator
    from bridge.pad import NullPad

    monkeypatch.setattr(settings, "load", lambda *a: {})
    monkeypatch.setattr(bm, "VirtualPad", NullPad)
    monkeypatch.setattr(bm, "WheelActuator", lambda dry_run=False: WheelActuator(dry_run=True))
    wants = []
    real_set = tr.TrainerControl.set_want
    monkeypatch.setattr(tr.TrainerControl, "set_want", lambda self, w: (wants.append(w), real_set(self, w)))

    async def source(on_packet, on_state):
        on_state("connected")
        t0 = _time.monotonic()
        while _time.monotonic() - t0 < 2.5:
            on_packet(struct.pack("<HHHh", 0x0044, 2500, 170, 160))
            await asyncio.sleep(0.25)

    args = bm.parse_args(["--log-dir", str(tmp_path), "--no-sounds", "--no-hotkeys", "--workout", "tempo",
                          "--no-launch-game", "--no-overlay", "--tarmac"])
    asyncio.run(bm.run(args, source_fn=source))
    out = capsys.readouterr().out
    assert "trainer:   ERG holds workout targets, road feel tarmac (forced)" in out
    assert any(w.mode == "erg" for w in wants)                  # warm-up has a target: ERG requested
    log_text = next(tmp_path.glob("bridge-*.log")).read_text(encoding="utf-8")
    assert "resistance=True erg=True road_feel=True gravel=False" in log_text
