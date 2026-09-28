"""Crash reports and the report.bat bundle: redaction, fallbacks and contents."""

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge import crashreport, report  # noqa: E402

FAKE_HOME = r"C:\Users\zqxuser7"


@pytest.fixture
def identity(monkeypatch):
    """A fake user, home folder and computer name so the tests don't depend on this PC."""
    monkeypatch.setattr(crashreport.Path, "home", staticmethod(lambda: Path(FAKE_HOME)))
    monkeypatch.setenv("USERNAME", "zqxuser7")
    monkeypatch.setenv("COMPUTERNAME", "ZQX-DESKTOP")
    monkeypatch.setattr(crashreport.getpass, "getuser", lambda: "zqxuser7")


def test_redact_removes_personal_details(identity):
    text = (r"File C:\Users\zqxuser7\slowroads\bridge.py line 3" "\n"
            "C:/Users/zqxuser7/x and C:\\\\Users\\\\zqxuser7\\\\y\n"
            "device E8:3A:1B:22:4C:9D and e8-3a-1b-22-4c-9d on zqx-desktop by ZQXUSER7\n"
            "contact someone+tag@example.com")
    out = crashreport.redact(text)
    for secret in ("zqxuser7", "ZQXUSER7", "E8:3A", "e8-3a", "zqx-desktop", "example.com"):
        assert secret.lower() not in out.lower(), secret
    assert r"<home>\slowroads\bridge.py" in out
    assert out.count("<bt-address>") == 2 and "<email>" in out and "<host>" in out and "<user>" in out


def test_redact_keeps_ordinary_text(identity):
    text = "power 250 W at 12:34:56, packet 44 00 12 34, flags 0x0044"
    assert crashreport.redact(text) == text


def _boom():
    raise ValueError(r"bad value from C:\Users\zqxuser7\x at E8:3A:1B:22:4C:9D")


def test_run_main_writes_redacted_report(identity, tmp_path, capsys):
    (tmp_path / "bridge-20260101-000000.log").write_text("connected to E8:3A:1B:22:4C:9D\n", encoding="utf-8")
    assert crashreport.run_main(_boom, "bridge", log_dir=tmp_path) == 1
    reports = list(tmp_path.glob("crash-bridge-*.txt"))
    assert len(reports) == 1
    text = reports[0].read_text(encoding="utf-8")
    assert "ValueError" in text and "== environment" in text and "connected to <bt-address>" in text
    assert "zqxuser7" not in text.lower() and "E8:3A" not in text
    err = capsys.readouterr().err
    assert "report.bat" in err and crashreport.ISSUES_URL in err
    assert r"bad value from <home>\x at <bt-address>" in err  # (the local report path may show the user name)


def test_run_main_catches_stray_cancelled_error(tmp_path):
    import asyncio

    def cancelled():
        raise asyncio.CancelledError()

    assert crashreport.run_main(cancelled, "bridge", log_dir=tmp_path) == 1
    assert list(tmp_path.glob("crash-bridge-*.txt"))


def test_run_main_ctrl_c_and_exit_codes(tmp_path):
    def interrupted():
        raise KeyboardInterrupt

    assert crashreport.run_main(interrupted, "bridge", log_dir=tmp_path) == 0
    assert crashreport.run_main(lambda: None, "bridge", log_dir=tmp_path) == 0
    assert crashreport.run_main(lambda: 3, "bridge", log_dir=tmp_path) == 3
    with pytest.raises(SystemExit):
        crashreport.run_main(lambda: sys.exit("usage"), "bridge", log_dir=tmp_path)
    assert not list(tmp_path.glob("crash-*.txt"))


def test_write_report_falls_back_to_temp(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")  # a file where the log folder should be: mkdir fails
    monkeypatch.setattr(crashreport.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    path = crashreport.write_report(ValueError("x"), "bridge", log_dir=blocker)
    assert path is not None and path.parent == tmp_path / "temp" / "slowroads-bridge"


def test_write_report_prints_when_nothing_is_writable(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    monkeypatch.setattr(crashreport.tempfile, "gettempdir", lambda: str(blocker))
    assert crashreport.write_report(ValueError("printed"), "bridge", log_dir=blocker) is None
    assert "printed" in capsys.readouterr().err


def test_write_report_survives_a_broken_builder(tmp_path, monkeypatch):
    def broken(*a):
        raise RuntimeError("builder bug")

    monkeypatch.setattr(crashreport, "build_report", broken)
    path = crashreport.write_report(ValueError("original"), "bridge", log_dir=tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "builder bug" in text and "original" in text


def test_tool_name_is_sanitised(tmp_path):
    path = crashreport.write_report(ValueError("x"), r"..\..\evil tool", log_dir=tmp_path)
    assert path.parent == tmp_path and path.name.startswith("crash-eviltool-")


def test_report_zip_contents(identity, tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_environment_check", lambda: r"OK python at C:\Users\zqxuser7\.venv")
    (tmp_path / "crash-bridge-20260101-000000.txt").write_text("Traceback E8:3A:1B:22:4C:9D", encoding="utf-8")
    (tmp_path / "bridge-20260101-000000.log").write_text("line\n" * 4000, encoding="utf-8")
    (tmp_path / "ride-20260101-000000.csv").write_text("t_s,power_w\n0,100\n", encoding="utf-8")
    (tmp_path / "summary-20260101-000000.txt").write_text("Ride summary", encoding="utf-8")
    (tmp_path / "settings.json").write_text('{"trainer": "E8:3A:1B:22:4C:9D"}', encoding="utf-8")
    (tmp_path / "shots-20260101-000000").mkdir()
    (tmp_path / "shots-20260101-000000" / "a.png").write_bytes(b"png")
    (tmp_path / "game-20260101-000000-start").mkdir()

    out = report.build_report(log_dir=tmp_path)
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert {"README.txt", "environment.txt", "crash-bridge-20260101-000000.txt",
                "bridge-20260101-000000.log", "ride-20260101-000000.csv", "summary-20260101-000000.txt"} <= names
        assert not any("settings" in n or n.endswith(".png") or "game-" in n for n in names)
        everything = "".join(z.read(n).decode("utf-8") for n in names)
    assert "E8:3A" not in everything and "zqxuser7" not in everything.lower()
    assert "earlier lines omitted" in everything  # long logs are trimmed


def test_report_on_an_empty_log_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_environment_check", lambda: "ok")
    out = report.build_report(log_dir=tmp_path / "missing")
    with zipfile.ZipFile(out) as z:
        assert set(z.namelist()) == {"README.txt", "environment.txt"}
