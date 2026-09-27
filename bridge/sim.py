"""Simulated trainer for off-production tests: python -m bridge --sim "0:0,5:15,40:15,55:0".

Emits real Indoor Bike Data packets (flags 0x0044) at 1 Hz, like the KICKR, following a
piecewise-linear bike-speed profile of "seconds:km/h" points. Cadence and power are rough fills
(power ~ road-bike physics at that speed, flat) so every downstream path is exercised.
"""

import asyncio
import logging
import struct
import time
from typing import Callable

log = logging.getLogger("bridge.sim")


def parse_profile(text: str) -> list[tuple[float, float]]:
    pts = sorted((float(t), float(v)) for t, v in (p.split(":") for p in text.split(",") if p.strip()))
    if not pts:
        raise ValueError("empty --sim profile")
    return pts


def speed_at(profile, t: float) -> float:
    if t <= profile[0][0]:
        return profile[0][1]
    for (t0, v0), (t1, v1) in zip(profile, profile[1:]):
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0) if t1 > t0 else v1
    return profile[-1][1]


def packet(speed_kmh: float, cadence_rpm: float, power_w: int) -> bytes:
    return struct.pack("<HHHh", 0x0044, int(round(speed_kmh * 100)), int(round(cadence_rpm * 2)), power_w)


def power_for(speed_kmh: float) -> int:
    v = speed_kmh / 3.6  # flat road bike: rolling + aero, 85 kg, CdA 0.32, Crr 0.004
    return int(round(v * (85 * 9.81 * 0.004 + 0.5 * 1.225 * 0.32 * v * v) / 0.97))


async def run_sim(profile, on_packet: Callable[[bytes], None], on_state: Callable[[str], None]) -> None:
    on_state("connected")
    log.info("simulated trainer: %s", profile)
    t0 = time.monotonic()
    end = profile[-1][0]
    while True:
        t = time.monotonic() - t0
        v = speed_at(profile, t)
        on_packet(packet(v, 80.0 if v > 0.5 else 0.0, power_for(v)))
        if t > end + 2:
            on_state("sim done")
            log.info("simulated profile finished")
            raise asyncio.CancelledError
        await asyncio.sleep(1.0)
