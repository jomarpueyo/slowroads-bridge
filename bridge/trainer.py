"""Trainer resistance over Bluetooth FTMS: ERG for workout targets and a road feel for everything else.

The rider asked for it (2026-10-05): the ramp test "sucked" with nothing to push against, and they wanted
feedback from the road. The KICKR CORE (fw 1.5.36) reports support for power targets (ERG, 0-2000 W),
resistance levels and indoor bike simulation (read 2026-10-05, Fitness Machine Feature 03 40 00 00 0c 60 00 00).

What the bridge sends (opcodes on the Fitness Machine Control Point 0x2AD9, responses come back as
indications, which must be enabled first and again after every reconnect):
  - Request Control (0x00) once per connection, then Start (0x07).
  - ERG, Set Target Power (0x05, sint16 W): during workout blocks that have a power target (the middle of
    the range; ramp test steps exactly). The trainer then holds that power whatever your gear or cadence.
  - Road feel, Set Indoor Bike Simulation (0x11: wind 0.001 m/s, grade 0.01 %, Crr 0.0001, Cw 0.01 kg/m) on
    a flat road: the trainer adds rolling resistance and air drag that grow with your wheel speed, like a
    real road. Gravel uses a higher Crr, and an optional texture varies it in small random steps.
  - Reset (0x01) at the end of every ride: hands the trainer back. (FTMS Stop alone may leave the last
    setting applied, as one open-source app found.)

Safety: ERG drops to road feel when you stop pedalling or your cadence falls below 45 rpm (no "ERG spiral"),
while paused (F8) and in rest blocks; changes are rate-limited; any Bluetooth error only logs and the ride
goes on. --no-resistance (or "resistance": 0) turns all of it off; --dry-run never sends anything.
"""

import asyncio
import logging
import random
import struct
import time
from dataclasses import dataclass

log = logging.getLogger("bridge.trainer")

CONTROL_POINT = "00002ad9-0000-1000-8000-00805f9b34fb"
FEATURE = "00002acc-0000-1000-8000-00805f9b34fb"

OP_REQUEST_CONTROL, OP_RESET, OP_TARGET_POWER, OP_START, OP_STOP, OP_SIMULATION, OP_RESPONSE = (
    0x00, 0x01, 0x05, 0x07, 0x08, 0x11, 0x80)
RESULT = {0x01: "success", 0x02: "not supported", 0x03: "invalid parameter", 0x04: "operation failed",
          0x05: "control not permitted"}
TARGET_BITS = {0: "speed", 1: "inclination", 2: "resistance", 3: "power", 4: "heart rate",
               13: "simulation", 14: "wheel circumference", 15: "spin down", 16: "cadence"}

# Road feel. Cw = 0.5 x air density x CdA: 0.5 x 1.225 x 0.32 = 0.20 kg/m, the virtual bike's own numbers.
TARMAC_CRR, GRAVEL_CRR, ROAD_CW = 0.004, 0.010, 0.20


# --------------------------------------------------------------------------- encoding (FTMS v1.0)

def request_control() -> bytes:
    return bytes([OP_REQUEST_CONTROL])


def reset() -> bytes:
    return bytes([OP_RESET])


def start() -> bytes:
    return bytes([OP_START])


def stop(pause: bool = False) -> bytes:
    return bytes([OP_STOP, 0x02 if pause else 0x01])


def target_power(watts: float, max_w: int = 2000) -> bytes:
    return struct.pack("<Bh", OP_TARGET_POWER, int(round(max(0.0, min(float(max_w), watts)))))


def simulation(grade_pct: float = 0.0, crr: float = TARMAC_CRR, cw: float = ROAD_CW, wind_mps: float = 0.0) -> bytes:
    def clamp(v, lo, hi):
        return max(lo, min(hi, v))

    return struct.pack("<BhhBB", OP_SIMULATION,
                       int(round(clamp(wind_mps, -32.0, 32.0) * 1000)),
                       int(round(clamp(grade_pct, -40.0, 40.0) * 100)),
                       int(round(clamp(crr, 0.0, 0.0255) * 10000)),
                       int(round(clamp(cw, 0.0, 2.55) * 100)))


def parse_response(data: bytes):
    """(request opcode, result name) from a Control Point indication, or None if it isn't a response."""
    data = bytes(data)
    if len(data) >= 3 and data[0] == OP_RESPONSE:
        return data[1], RESULT.get(data[2], f"reserved 0x{data[2]:02x}")
    return None


def parse_features(data: bytes) -> set:
    """Supported target settings from the Fitness Machine Feature characteristic (second 32-bit field)."""
    data = bytes(data)
    if len(data) < 8:
        return set()
    target = struct.unpack_from("<I", data, 4)[0]
    return {name for bit, name in TARGET_BITS.items() if target >> bit & 1}


# --------------------------------------------------------------------------- what to ask for

@dataclass(frozen=True)
class Want:
    mode: str                 # "erg", "sim" or "none" (leave the trainer alone)
    watts: float = 0.0
    crr: float = TARMAC_CRR
    cw: float = ROAD_CW
    grade: float = 0.0

    def label(self) -> str:
        if self.mode == "erg":
            return f"ERG {self.watts:.0f} W"
        if self.mode == "sim":
            return f"road feel (Crr {self.crr:.4f}, Cw {self.cw:.2f})"
        return "off"


class Texture:
    """Gravel 'rumble': rolling resistance that wanders in small random steps, with an occasional bump.
    The flywheel smooths it into an uneven, draggy feel rather than a vibration (a trainer can't shake)."""

    def __init__(self, base_crr: float = GRAVEL_CRR, amount: float = 0.3, seed=None) -> None:
        self.base, self.amount = base_crr, max(0.0, min(1.0, amount))
        self.rng = random.Random(seed)
        self._crr, self._next, self._bump_until = base_crr, 0.0, 0.0

    def crr(self, now: float) -> float:
        if self.amount <= 0:
            return self.base
        if now >= self._next:
            self._next = now + self.rng.uniform(0.4, 0.9)
            if now >= self._bump_until and self.rng.random() < 0.08:   # a rock or rut now and then
                self._bump_until = now + 0.6
            swing = self.base * self.amount
            self._crr = self.base + self.rng.uniform(-swing, swing)
        return self.base * (1 + 2 * self.amount) if now < self._bump_until else self._crr


def decide(enabled: bool, workout_status: dict | None, paused: bool, pedalling: bool, cadence,
           low_cadence_s: float, gravel: bool, road_feel: bool, erg: bool, texture: Texture | None,
           now: float) -> Want:
    """The resistance the trainer should have right now (pure function, see the module docstring)."""
    if not enabled:
        return Want("none")
    crr = (texture.crr(now) if texture else GRAVEL_CRR) if gravel else TARMAC_CRR
    road = Want("sim", crr=crr) if road_feel else Want("none")
    if paused or not pedalling:
        return road
    target = (workout_status or {}).get("target")
    if erg and target and low_cadence_s < 3.0:
        lo, hi = target
        watts = (lo + hi) / 2
        return Want("erg", watts=round(watts / 5) * 5 if hi - lo > 10 else watts)
    return road


# --------------------------------------------------------------------------- the controller

class TrainerControl:
    """Applies the latest Want to the connected trainer. attach()/detach() are called by the Bluetooth
    reader on each (re)connection; run() is a task of the ride; release() hands the trainer back."""

    ERG_INTERVAL, SIM_INTERVAL = 1.0, 0.5   # seconds between commands of each kind

    def __init__(self, enabled: bool = True, on_event=None) -> None:
        self.enabled = enabled
        self.on_event = on_event or (lambda kind, text: None)
        self.want = Want("none")
        self.applied = None
        self.client = None
        self.features: set = set()
        self.has_control = False
        self.ever_controlled = False
        self._responses: asyncio.Queue | None = None
        self._last_write = 0.0
        self._denied_until = 0.0
        self._warned: set = set()

    # -- connection

    async def attach(self, client) -> None:
        if not self.enabled:
            return
        self.client, self.has_control, self.applied = client, False, None
        self._responses = asyncio.Queue()
        try:
            self.features = parse_features(await client.read_gatt_char(FEATURE))
            await client.start_notify(CONTROL_POINT, self._on_indication)  # indications, per connection
            log.info("trainer control ready; supports %s", ", ".join(sorted(self.features)) or "nothing")
        except Exception as e:
            log.warning("trainer control unavailable: %s", e)
            self.client = None

    def detach(self) -> None:
        self.client, self.has_control, self.applied = None, False, None

    def _on_indication(self, _sender, data) -> None:
        r = parse_response(data)
        if r is None:
            return
        if self._responses is not None:
            self._responses.put_nowait(r)
        if r[1] != "success":
            log.info("trainer answered 0x%02x: %s", r[0], r[1])

    async def _command(self, payload: bytes, wait: bool = True, timeout: float = 2.0):
        """Write one command; with wait, return the trainer's result for it (or 'timeout')."""
        while self._responses is not None and not self._responses.empty():
            self._responses.get_nowait()
        await self.client.write_gatt_char(CONTROL_POINT, payload, response=True)
        self._last_write = time.monotonic()
        if not wait or self._responses is None:
            return "sent"
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                op, result = await asyncio.wait_for(self._responses.get(), end - time.monotonic())
            except asyncio.TimeoutError:
                break
            if op == payload[0]:
                return result
        return "timeout"

    # -- what the bridge calls

    def set_want(self, want: Want) -> None:
        self.want = want

    async def _take_control(self) -> bool:
        if time.monotonic() < self._denied_until:
            return False
        result = await self._command(request_control())
        if result != "success":
            self._denied_until = time.monotonic() + 15.0
            self._warn("control", f"trainer didn't give control ({result}); is another app controlling it?")
            return False
        await self._command(start())
        self.has_control = self.ever_controlled = True
        log.info("trainer control granted")
        return True

    def _warn(self, key: str, text: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(text)

    async def step(self) -> None:
        """Send what's needed to bring the trainer to self.want (called a few times a second)."""
        want = self.want
        if not self.enabled or self.client is None or want.mode == "none" or want == self.applied:
            return
        needed = "power" if want.mode == "erg" else "simulation"
        if needed not in self.features:
            self._warn(needed, f"this trainer doesn't support {needed} targets; leaving it alone")
            return
        interval = self.ERG_INTERVAL if want.mode == "erg" else self.SIM_INTERVAL
        if time.monotonic() - self._last_write < interval:
            return
        if not self.has_control and not await self._take_control():
            return
        payload = target_power(want.watts) if want.mode == "erg" else simulation(want.grade, want.crr, want.cw)
        result = await self._command(payload)
        if result == "control not permitted":
            self.has_control = False
            return
        if result not in ("success", "timeout"):
            self._warn(f"{want.mode}-{result}", f"trainer refused {want.label()}: {result}")
            return
        mode_changed = self.applied is None or self.applied.mode != want.mode
        self.applied = want
        if mode_changed or want.mode == "erg":
            log.info("trainer: %s", want.label())
            self.on_event(want.mode, want.label())

    async def run(self) -> None:
        while True:
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # never let resistance take the ride down
                self._warn("error", f"trainer control error (ride continues): {e!r}")
                self.has_control = False
            await asyncio.sleep(0.25)

    async def release(self) -> None:
        """Hand the trainer back (Reset) if this ride ever controlled it."""
        if self.client is None or not self.ever_controlled:
            return
        try:
            result = await self._command(reset(), timeout=1.5)
            log.info("trainer released (reset: %s)", result)
        except Exception as e:
            log.warning("could not release the trainer: %s", e)
        self.has_control, self.applied = False, None
