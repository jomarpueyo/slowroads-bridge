"""Crash reports that are safe to send to the developer.

Every entry point (the bridge and each tool) runs through run_main(). If something goes wrong it writes
logs/crash-<tool>-<stamp>.txt with the error, versions, the command line and the end of the latest event
log, then tells the user how to send it (report.bat bundles everything into one zip).

Everything written is redacted: Bluetooth addresses, the Windows user name and home folder, the computer
name and e-mail addresses are replaced with placeholders. The handler never raises: if it can't write
to logs/ it falls back to the temp folder, and if that fails it prints the report instead.
"""

import getpass
import os
import platform
import re
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

ISSUES_URL = "https://github.com/jomarpueyo/slowroads-bridge/issues/new?template=bug_report.md"
ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ("bleak", "vgamepad", "mss", "pillow", "winrt-runtime", "pytest")

_MAC_RE = re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def _identity_strings() -> list[tuple[str, str]]:
    """(secret, placeholder) pairs, longest first so a home path is replaced before the bare user name."""
    pairs = []
    home = str(Path.home())
    for variant in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
        pairs.append((variant, "<home>"))
    for name, label in ((_safe(getpass.getuser), "<user>"), (os.environ.get("USERNAME"), "<user>"),
                        (os.environ.get("COMPUTERNAME"), "<host>"), (_safe(platform.node), "<host>")):
        if name and len(name) >= 3:
            pairs.append((name, label))
    return sorted(set(pairs), key=lambda p: -len(p[0]))


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def redact(text: str) -> str:
    """Remove personal details from text going into a report."""
    text = _MAC_RE.sub("<bt-address>", text)
    text = _EMAIL_RE.sub("<email>", text)
    for secret, placeholder in _identity_strings():
        text = re.sub(re.escape(secret), placeholder, text, flags=re.IGNORECASE)
    return text


def version() -> str:
    """Package version plus the git commit if this is a clone (read from .git without running git)."""
    from . import __version__

    commit = ""
    try:
        head = (ROOT / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = ROOT / ".git" / head[5:]
            commit = ref.read_text(encoding="utf-8").strip() if ref.exists() else ""
            if not commit:  # packed refs
                packed = (ROOT / ".git" / "packed-refs").read_text(encoding="utf-8")
                commit = next((ln.split()[0] for ln in packed.splitlines() if ln.endswith(head[5:])), "")
        else:
            commit = head
    except Exception:
        pass
    return f"{__version__}" + (f" (commit {commit[:10]})" if commit else " (downloaded zip, no git)")


def package_versions() -> dict:
    from importlib import metadata

    out = {}
    for name in PACKAGES:
        try:
            out[name] = metadata.version(name)
        except Exception:
            out[name] = "not installed"
    return out


def environment() -> str:
    lines = [f"slowroads-bridge {version()}",
             f"python {platform.python_version()} ({platform.architecture()[0]})",
             f"os {platform.platform()}"]
    lines += [f"{k} {v}" for k, v in package_versions().items()]
    return "\n".join(lines)


def settings_summary() -> str:
    """Which settings are set (values only for numeric ride preferences; the trainer address is hidden)."""
    try:
        from . import settings

        ride = settings.load()
        paired = settings.load_trainer() is not None
        return f"ride settings: {ride or 'none'}\ntrainer paired: {paired}"
    except Exception as e:
        return f"settings unreadable: {type(e).__name__}"


def latest_event_log_tail(log_dir: Path, lines: int = 80) -> str:
    try:
        logs = sorted(log_dir.glob("bridge-*.log"), key=lambda p: p.stat().st_mtime)
        if not logs:
            return "(no event log)"
        text = logs[-1].read_text(encoding="utf-8", errors="replace").splitlines()
        return f"{logs[-1].name} (last {min(lines, len(text))} lines)\n" + "\n".join(text[-lines:])
    except Exception as e:
        return f"(event log unreadable: {type(e).__name__})"


def build_report(exc: BaseException, tool: str, log_dir: Path) -> str:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    parts = [
        f"slowroads-bridge crash report ({tool})",
        f"time: {datetime.now().isoformat(timespec='seconds')}",
        "", "== error", tb.rstrip(),
        "", "== command line", " ".join(sys.argv),
        "", "== environment", environment(),
        "", "== settings", settings_summary(),
        "", "== latest event log", latest_event_log_tail(log_dir),
    ]
    return redact("\n".join(parts)) + "\n"


def write_report(exc: BaseException, tool: str, log_dir: Path | None = None) -> Path | None:
    """Write a redacted crash report; returns its path (or None if nothing could be written)."""
    from .ridelog import LOG_DIR

    log_dir = Path(log_dir or LOG_DIR)
    try:
        text = build_report(exc, tool, log_dir)
    except Exception as e:  # never let the reporter itself crash
        text = redact(f"crash report builder failed: {e!r}\n\n" + "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)))
    name = f"crash-{re.sub(r'[^a-z0-9_-]', '', tool.lower()) or 'tool'}-{datetime.now():%Y%m%d-%H%M%S}.txt"
    for folder in (log_dir, Path(tempfile.gettempdir()) / "slowroads-bridge"):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / name
            path.write_text(text, encoding="utf-8")
            return path
        except Exception:
            continue
    if sys.stderr:  # None under pythonw (the ride window)
        sys.stderr.write(text)
    return None


def tell_user(path: Path | None, exc: BaseException) -> None:
    where = f"A crash report was saved to:\n  {path}" if path else "The crash report is printed above."
    print(f"\n\nSomething went wrong: {type(exc).__name__}: {redact(str(exc))[:200]}\n{where}\n"
          f"To send it to the developer, run report.bat (it bundles the report and recent logs into one\n"
          f"zip with personal details removed) and attach the zip to a new issue:\n  {ISSUES_URL}\n",
          file=sys.stderr, flush=True)


def run_main(main_fn, tool: str, log_dir: Path | None = None) -> int:
    """Run an entry point; turn any unexpected error into a saved, redacted crash report + exit code 1.
    Ctrl+C and SystemExit behave normally."""
    try:
        rc = main_fn()
        return int(rc or 0)
    except KeyboardInterrupt:
        return 0
    except SystemExit:
        raise
    except BaseException as exc:  # includes stray CancelledError
        path = write_report(exc, tool, log_dir)
        tell_user(path, exc)
        return 1
