"""FTMS reader: finds the KICKR CORE and decodes Indoor Bike Data (0x2AD2) packets."""

import asyncio
import logging
import struct
from dataclasses import dataclass
from typing import Callable

FTMS_SERVICE = "00001826-0000-1000-8000-00805f9b34fb"
INDOOR_BIKE_DATA = "00002ad2-0000-1000-8000-00805f9b34fb"

log = logging.getLogger("bridge.ftms")


class MalformedPacket(ValueError):
    pass


@dataclass
class BikeData:
    flags: int
    speed_kmh: float | None
    cadence_rpm: float | None
    power_w: int | None
    raw: bytes


def parse_indoor_bike_data(data: bytes) -> BikeData:
    """Decode the fields up to and including instantaneous power.

    Each set flag bit adds a field in a fixed order, so field offsets depend on the flags.
    Bit 0 is inverted in FTMS: instantaneous speed is present when the bit is CLEAR.
    """
    if len(data) < 2:
        raise MalformedPacket(f"packet too short ({len(data)} bytes)")
    flags = int.from_bytes(data[0:2], "little")
    offset = 2
    speed = cadence = power = None

    def take(fmt: str) -> int:
        nonlocal offset
        size = struct.calcsize(fmt)
        if offset + size > len(data):
            raise MalformedPacket(
                f"flags 0x{flags:04x} need {offset + size} bytes, got {len(data)}"
            )
        (value,) = struct.unpack_from(fmt, data, offset)
        offset += size
        return value

    if not flags & 0x0001:
        speed = take("<H") * 0.01
    if flags & (1 << 1):
        take("<H")  # average speed
    if flags & (1 << 2):
        cadence = take("<H") * 0.5
    if flags & (1 << 3):
        take("<H")  # average cadence
    if flags & (1 << 4):
        offset += 3  # total distance, uint24
        if offset > len(data):
            raise MalformedPacket(f"flags 0x{flags:04x} truncated at total distance")
    if flags & (1 << 5):
        take("<h")  # resistance level
    if flags & (1 << 6):
        power = take("<h")
    return BikeData(flags, speed, cadence, power, bytes(data))


def parse_power(data: bytes) -> int | None:
    return parse_indoor_bike_data(data).power_w


async def find_trainer(name_hint: str = "KICKR", timeout: float = 15.0):
    from bleak import BleakScanner

    log.info("scanning for FTMS trainer (name hint %r, up to %.0f s)", name_hint, timeout)

    def is_trainer(device, adv) -> bool:
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        name = device.name or adv.local_name or ""
        return FTMS_SERVICE in uuids or name_hint.lower() in name.lower()

    # Returns as soon as a match is seen instead of waiting out the full timeout.
    device = await BleakScanner.find_device_by_filter(is_trainer, timeout=timeout)
    if device is None:
        log.warning("no trainer found in %.0f s", timeout)
    else:
        log.info("found %s [%s]", device.name, device.address)
    return device


async def run_reader(
    on_packet: Callable[[bytes], None],
    on_state: Callable[[str], None],
    name_hint: str = "KICKR",
    reconnect_delay: float = 3.0,
) -> None:
    """Connect, subscribe to Indoor Bike Data, and reconnect forever on drops."""
    from bleak import BleakClient

    while True:
        on_state("scanning")
        device = await find_trainer(name_hint)
        if device is None:
            await asyncio.sleep(reconnect_delay)
            continue
        disconnected = asyncio.Event()
        try:
            on_state("connecting")
            async with BleakClient(
                device, disconnected_callback=lambda _: disconnected.set()
            ) as client:
                log.info("connected to %s", device.address)
                on_state("connected")
                await client.start_notify(INDOOR_BIKE_DATA, lambda _, d: on_packet(bytes(d)))
                await disconnected.wait()
                log.warning("trainer disconnected")
        except Exception:
            log.exception("bluetooth error")
        on_state("disconnected")
        await asyncio.sleep(reconnect_delay)
