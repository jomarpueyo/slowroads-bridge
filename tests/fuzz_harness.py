"""Shared fuzz harness: run the real bridge loop (dry run, temp logs) against hostile packet sources and
check its outputs stay safe. Used by tests/test_fuzz_bridge.py and tools/fuzz_bridge.py (long runs)."""

import asyncio
import csv
import math
import random
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.__main__ import parse_args, run  # noqa: E402

PLANS = {"", "stopped", "riding", "coasting", "releasing"}  # plus "push xN.NN"


def valid_packet(rng: random.Random, extreme: bool) -> bytes:
    """FTMS Indoor Bike Data (flags 0x0044) with plausible or extreme field values."""
    if extreme:
        speed = rng.choice([0, 65535, rng.randrange(65536)])
        cadence = rng.choice([0, 65535, rng.randrange(65536)])
        power = rng.choice([-32768, -1, 0, 32767, rng.randrange(-32768, 32768)])
    else:
        speed, cadence, power = rng.randrange(0, 5000), rng.randrange(0, 240), rng.randrange(0, 600)
    return struct.pack("<HHHh", 0x0044, speed, cadence, power)


def garbage_packet(rng: random.Random) -> bytes:
    n = rng.choice([0, 1, 2, 3, 5, 8, 12, 20, 40, 200])
    return bytes(rng.getrandbits(8) for _ in range(n))


def make_source(kind: str, seed: int, seconds: float, sent: list):
    """Coroutine function (on_packet, on_state) feeding `kind` of data for about `seconds`."""
    rng = random.Random(seed)

    async def source(on_packet, on_state):
        on_state("connected")
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while loop.time() < end:
            if kind == "silence":
                await asyncio.sleep(0.1)
                continue
            if kind == "garbage":
                pkt = garbage_packet(rng)
            elif kind == "extreme":
                pkt = valid_packet(rng, extreme=True)
            elif kind == "flood":
                for _ in range(200):
                    pkt = valid_packet(rng, extreme=rng.random() < 0.3)
                    sent.append(pkt)
                    on_packet(pkt)
                await asyncio.sleep(0)
                continue
            elif kind == "dropout":
                pkt = valid_packet(rng, extreme=False)
                if rng.random() < 0.05:
                    on_state("disconnected")
                    await asyncio.sleep(3.3)  # longer than the 3 s stale timeout
                    on_state("connected")
            else:  # mixed
                r = rng.random()
                pkt = garbage_packet(rng) if r < 0.3 else valid_packet(rng, extreme=r < 0.5)
            sent.append(pkt)
            on_packet(pkt)
            await asyncio.sleep(rng.choice([0, 0.001, 0.02, 0.2, 0.5, 1.0]))
        on_state("sim done")

    return source


def run_fuzz(kind: str, seed: int, seconds: float, log_dir: Path, mode: str = "limit",
             speed_source: str = "virtual") -> dict:
    """Run one fuzzed ride and return what it produced (raises on any crash)."""
    sent: list = []
    args = parse_args(["--dry-run", "--log-dir", str(log_dir), "--mode", mode, "--speed-source", speed_source])
    asyncio.run(run(args, source_fn=make_source(kind, seed, seconds, sent)))
    return {"sent": sent, "log_dir": log_dir}


def check_outputs(result: dict, mode: str = "limit") -> list[str]:
    """Invariants every ride must satisfy, whatever the input. Returns a list of violations."""
    log_dir: Path = result["log_dir"]
    problems = []
    drives = sorted(log_dir.glob("drive-*.csv"))
    rides = sorted(log_dir.glob("ride-*.csv"))
    if not drives or not rides:
        return ["missing drive or ride log"]
    with open(rides[-1], encoding="utf-8", newline="") as f:
        ride_rows = list(csv.DictReader(f))
    if len(ride_rows) != len(result["sent"]):
        problems.append(f"ride log has {len(ride_rows)} rows for {len(result['sent'])} packets")
    with open(drives[-1], encoding="utf-8", newline="") as f:
        for i, r in enumerate(csv.DictReader(f)):
            thr, trig, brk = float(r["throttle"]), float(r["trigger"]), float(r["brake"])
            tgt, lim = float(r["target_kmh"]), float(r["car_est_kmh"])
            if not (0.0 <= thr <= 1.0 and 0.0 <= trig <= 1.0 and 0.0 <= brk <= 1.0):
                problems.append(f"drive row {i}: control out of range thr={thr} trig={trig} brk={brk}")
            if not (math.isfinite(tgt) and math.isfinite(lim) and tgt >= 0):
                problems.append(f"drive row {i}: non-finite or negative target={tgt} est={lim}")
            if mode == "limit":
                if brk != 0.0:
                    problems.append(f"drive row {i}: brake used in limit mode ({brk})")
                if lim > 125 * 1.609344 + 0.1:
                    problems.append(f"drive row {i}: limit above the game's 125 mph maximum ({lim:.1f} km/h)")
                if r["plan"] not in PLANS and not r["plan"].startswith("push x"):
                    problems.append(f"drive row {i}: unknown plan {r['plan']!r}")
            if len(problems) > 20:
                return problems
    if not list(log_dir.glob("summary-*.txt")):
        problems.append("no ride summary written")
    if (log_dir / "active-ride.txt").exists():
        problems.append("ride marker left behind")
    return problems
