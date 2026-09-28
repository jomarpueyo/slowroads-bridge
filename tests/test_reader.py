"""Bluetooth reader resilience (friend's test 2026-09-27 20:23: a Windows-side cancel during connect
crashed the bridge instead of retrying). Uses a fake bleak module, so no Bluetooth is needed."""

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import ftms  # noqa: E402


class FakeServices:
    def __init__(self, has_ftms):
        self.has_ftms = has_ftms

    def get_characteristic(self, uuid):
        return object() if self.has_ftms else None

    def __iter__(self):
        return iter([SimpleNamespace(uuid="00001800-0000-1000-8000-00805f9b34fb")])


def fake_bleak(fail_first: int, error=asyncio.CancelledError, no_ftms_first: int = 0):
    """A bleak stand-in whose first `fail_first` connections raise `error` while connecting, and whose
    first `no_ftms_first` connections expose no FTMS characteristic."""
    attempts = {"n": 0}

    class FakeClient:
        def __init__(self, device, disconnected_callback=None):
            self.device = device

        async def __aenter__(self):
            attempts["n"] += 1
            if attempts["n"] <= fail_first:
                raise error()  # what bleak raises when WinRT service discovery is cancelled
            return self

        async def __aexit__(self, *exc):
            return False

        async def start_notify(self, uuid, cb):
            pass

        @property
        def services(self):
            return FakeServices(attempts["n"] > no_ftms_first)

    mod = types.ModuleType("bleak")
    mod.BleakClient = FakeClient
    return mod, attempts


async def _run_until_paired(monkeypatch, fail_first, error=asyncio.CancelledError, no_ftms_first=0):
    mod, attempts = fake_bleak(fail_first, error, no_ftms_first)
    monkeypatch.setitem(sys.modules, "bleak", mod)

    async def fake_find(name_hint=None, timeout=15.0, address=None):
        return SimpleNamespace(address="aa:bb:cc:dd:ee:ff", name="KICKR CORE")

    monkeypatch.setattr(ftms, "find_trainer", fake_find)
    paired = asyncio.Event()
    saved = {}

    def on_connected(addr, name):
        saved["addr"] = addr
        paired.set()

    task = asyncio.create_task(ftms.run_reader(lambda b: None, lambda s: None, reconnect_delay=0.01,
                                               on_connected=on_connected))
    await asyncio.wait_for(paired.wait(), 5)
    # Now a real shutdown (Ctrl+C / end of --sim) must still stop the reader.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return attempts["n"], saved


def test_windows_cancel_during_connect_is_retried_not_fatal(monkeypatch):
    n, saved = asyncio.run(_run_until_paired(monkeypatch, fail_first=2))
    assert n == 3                                    # two cancelled attempts, then connected
    assert saved["addr"] == "AA:BB:CC:DD:EE:FF"      # paired once it finally connected


def test_other_connect_errors_are_retried(monkeypatch):
    n, _ = asyncio.run(_run_until_paired(monkeypatch, fail_first=1, error=OSError))
    assert n == 2


def test_repeated_failures_print_a_hint(monkeypatch, capsys):
    asyncio.run(_run_until_paired(monkeypatch, fail_first=3))
    assert "can't get data from the trainer after 3 tries" in capsys.readouterr().out


def test_trainer_without_ftms_is_retried_with_a_clear_message(monkeypatch, caplog):
    # 2026-09-28: KICKR connected but offered only basic services (no 0x2AD2).
    n, saved = asyncio.run(_run_until_paired(monkeypatch, fail_first=0, no_ftms_first=2))
    assert n == 3 and saved["addr"] == "AA:BB:CC:DD:EE:FF"
    assert "isn't offering its fitness data" in caplog.text
    assert "Traceback" not in caplog.text
