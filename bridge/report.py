"""Bundle a problem report to send to the developer: python -m bridge.report  (or double-click report.bat)

Creates logs/report-<stamp>.zip containing, with personal details removed (Bluetooth addresses, Windows
user name and home folder, computer name, e-mail addresses):
  - the most recent crash reports (crash-*.txt)
  - the latest event logs (bridge-*.log) and setup/recorder logs
  - the latest ride summary, and the latest ride/drive CSVs (trainer packets and controller decisions)
  - environment.txt: versions, settings summary and the environment check
Never included: screenshots, copies of the game's saved settings, settings.json itself.
Attach the zip to a GitHub issue: https://github.com/jomarpueyo/slowroads-bridge/issues/new?template=bug_report.md
"""

import contextlib
import io
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from .crashreport import ISSUES_URL, environment, redact, settings_summary
from .ridelog import LOG_DIR

MAX_CSV_LINES = 5000


def _latest(log_dir: Path, pattern: str, n: int) -> list[Path]:
    files = [p for p in log_dir.glob(pattern) if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime)[-n:]


def _read(path: Path, tail_lines: int | None = None) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if tail_lines is not None:
        lines = text.splitlines()
        if len(lines) > tail_lines:
            text = "\n".join(lines[:1] + [f"... ({len(lines) - tail_lines} earlier lines omitted) ..."]
                             + lines[-tail_lines:])
    return redact(text)


def _environment_check() -> str:
    """tools/check_env.py output, captured in-process."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        import check_env  # noqa: PLC0415

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            check_env.main()
        return buf.getvalue()
    except Exception as e:
        return f"(environment check failed: {type(e).__name__}: {e})"


def build_report(log_dir: Path = LOG_DIR, out_dir: Path | None = None) -> Path:
    log_dir = Path(log_dir)
    out_dir = Path(out_dir or log_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"report-{datetime.now():%Y%m%d-%H%M%S}.zip"
    picked = []
    picked += [(p, None) for p in _latest(log_dir, "crash-*.txt", 5)]
    picked += [(p, 3000) for p in _latest(log_dir, "bridge-*.log", 3)]
    picked += [(p, 500) for p in _latest(log_dir, "recorder-*.log", 1) + _latest(log_dir, "setup-*.log", 1)]
    picked += [(p, None) for p in _latest(log_dir, "summary-*.txt", 3)]
    picked += [(p, MAX_CSV_LINES) for p in _latest(log_dir, "ride-*.csv", 1) + _latest(log_dir, "drive-*.csv", 1)]
    env = "\n\n".join(["== versions\n" + environment(), "== settings\n" + settings_summary(),
                       "== environment check\n" + _environment_check()])
    readme = (f"slowroads-bridge problem report, {datetime.now().isoformat(timespec='seconds')}\n"
              "Personal details (Bluetooth addresses, user/computer names, home folder, e-mail) are removed.\n"
              "No screenshots, game settings or settings.json are included.\n\nFiles:\n"
              + "".join(f"  {p.name}\n" for p, _ in picked) + "  environment.txt\n")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", readme)
        z.writestr("environment.txt", redact(env))
        for path, tail in picked:
            try:
                z.writestr(path.name, _read(path, tail))
            except OSError as e:
                z.writestr(path.name + ".unreadable.txt", f"{type(e).__name__}: {e}")
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = build_report()
    print(f"Problem report saved:\n  {out}\n\nIt contains recent crash reports, logs and ride data with personal "
          f"details removed.\nTo send it to the developer, open a new issue and attach the zip:\n  {ISSUES_URL}")
    if "--no-open" not in argv and sys.platform == "win32":
        with contextlib.suppress(Exception):
            subprocess.Popen(["explorer", "/select,", str(out)])
    return 0


if __name__ == "__main__":
    from .crashreport import run_main

    sys.exit(run_main(main, "report"))
