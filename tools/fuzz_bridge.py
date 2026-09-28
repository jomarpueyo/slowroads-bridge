"""Long fuzz campaign: run the real bridge loop (dry run, no game, no trainer) against random hostile
trainer data for N minutes and check every ride's outputs. Testing only.

Usage: python tools/fuzz_bridge.py [--minutes 5] [--seed 1] [--ride-seconds 4]
Each ride picks a data kind (garbage, extreme values, floods, silence, dropouts, mixed) and a bridge
mode. Crashes and invariant violations are listed at the end with the seed that reproduces them;
exit code 1 if there were any. The pytest suite runs a short fixed version (tests/test_fuzz_bridge.py).
"""

import argparse
import contextlib
import io
import logging
import random
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))
from fuzz_harness import check_outputs, run_fuzz  # noqa: E402

KINDS = ["garbage", "extreme", "flood", "silence", "dropout", "mixed"]
MODES = [("limit", "virtual"), ("limit", "trainer"), ("limit", "power"), ("speed", "trainer"), ("power", "trainer")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=int(time.time()))
    ap.add_argument("--ride-seconds", type=float, default=4.0)
    args = ap.parse_args()
    logging.disable(logging.CRITICAL)  # the bridge logs every bad packet; keep the console readable
    rng = random.Random(args.seed)
    end = time.monotonic() + args.minutes * 60
    rides, failures = 0, []
    print(f"fuzzing for {args.minutes} min, seed {args.seed}", flush=True)
    while time.monotonic() < end:
        kind, (mode, source), seed = rng.choice(KINDS), rng.choice(MODES), rng.randrange(1 << 30)
        label = f"kind={kind} mode={mode} source={source} seed={seed}"
        with tempfile.TemporaryDirectory() as tmp:
            try:
                with contextlib.redirect_stdout(io.StringIO()):  # the bridge's status line and summary
                    result = run_fuzz(kind, seed, args.ride_seconds, Path(tmp), mode, source)
                problems = check_outputs(result, mode)
            except BaseException as e:  # noqa: BLE001 - a crash is the finding
                if isinstance(e, KeyboardInterrupt):
                    break
                problems = ["CRASH " + "".join(traceback.format_exception_only(type(e), e)).strip(),
                            traceback.format_exc()]
        rides += 1
        print(f"{rides:4d} {'FAIL' if problems else 'ok  '} {label}", flush=True)
        if problems:
            failures.append((label, problems))
    print(f"\n{rides} rides, {len(failures)} with problems")
    for label, problems in failures:
        print(f"\n{label}\n  " + "\n  ".join(problems[:10]))
    return 1 if failures else 0


if __name__ == "__main__":
    from bridge.crashreport import run_main

    sys.exit(run_main(main, "fuzz_bridge"))
