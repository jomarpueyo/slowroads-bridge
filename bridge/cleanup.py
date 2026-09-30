"""Remove old bulky logs: python -m bridge.cleanup [--keep-days 30] [--dry-run]

Run automatically at the end of each ride (keep_days in settings.json, default 30; 0 = never).
Only files and folders directly in logs/ whose name carries a YYYYMMDD-HHMMSS stamp older than
keep_days are removed. Ride CSVs (ride-*.csv) and ride summaries (summary-*.txt) are always kept:
they are small and the ride totals (python -m bridge.summary --all) are built from them.
Symlinks and junctions are never followed.
"""

import argparse
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

STAMP_RE = re.compile(r"(\d{8})-(\d{6})")
ALWAYS_KEEP = ("ride-", "summary-")
KEEP_NAMES = {"active-ride.txt"}


def _stamp(name: str) -> datetime | None:
    m = STAMP_RE.search(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def old_entries(log_dir: Path, keep_days: float, now: datetime | None = None, active: str | None = None) -> list[Path]:
    if keep_days <= 0 or not log_dir.is_dir():
        return []
    cutoff = (now or datetime.now()) - timedelta(days=keep_days)
    out = []
    for p in sorted(log_dir.iterdir()):
        name = p.name
        if name in KEEP_NAMES or name.startswith(ALWAYS_KEEP) or (active and active in name):
            continue
        stamp = _stamp(name)
        if stamp is None or stamp >= cutoff:
            continue
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            continue
        out.append(p)
    return out


def remove(paths: list[Path]) -> int:
    removed = 0
    for p in paths:
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed += 1
        except OSError:
            pass  # in use or already gone: try again next time
    return removed


def main(argv=None) -> int:
    from . import settings
    from .ridelog import LOG_DIR

    ap = argparse.ArgumentParser(prog="bridge.cleanup")
    ap.add_argument("--keep-days", type=float, default=settings.load().get("keep_days", 30.0))
    ap.add_argument("--log-dir", type=Path, default=LOG_DIR)
    ap.add_argument("--dry-run", action="store_true", help="only list what would be removed")
    args = ap.parse_args(argv)
    paths = old_entries(args.log_dir, args.keep_days)
    for p in paths:
        print(("would remove " if args.dry_run else "removing ") + p.name)
    n = len(paths) if args.dry_run else remove(paths)
    print(f"{n} old log entries {'to remove' if args.dry_run else 'removed'} (older than {args.keep_days:g} days; "
          f"ride CSVs and summaries kept)")
    return 0


if __name__ == "__main__":
    from .crashreport import run_main

    sys.exit(run_main(main, "cleanup"))
