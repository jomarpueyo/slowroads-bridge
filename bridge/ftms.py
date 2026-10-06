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


def trainer_filter(address: str | None = None, name_hint: str | None = None):
    """Which advertising devices count as our trainer (docs/SECURITY.md finding 1).

    Pinned: only the saved address. Pairing (no saved address): the device must advertise the FTMS
    service; matching by name alone is only allowed when name_hint is given explicitly (--name).
    FTMS has no authentication, so pinning is what stops a nearby device posing as the trainer."""
    pinned = address.upper() if address else None

    def is_trainer(device, adv) -> bool:
        if pinned:
            return (device.address or "").upper() == pinned
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if FTMS_SERVICE in uuids:
            return True
        name = device.name or adv.local_name or ""
        return bool(name_hint) and name_hint.lower() in name.lower()

    return is_trainer


async def find_trainer(name_hint: str | None = None, timeout: float = 15.0, address: str | None = None):
    from bleak import BleakScanner

    if address:
        log.info("scanning for the paired trainer %s (up to %.0f s)", address, timeout)
    else:
        log.info("pairing: scanning for a device advertising FTMS%s (up to %.0f s)",
                 f" or named like {name_hint!r}" if name_hint else "", timeout)
    is_trainer = trainer_filter(address, name_hint)

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
    name_hint: str | None = None,
    reconnect_delay: float = 3.0,
    address: str | None = None,
    on_connected: Callable[[str, str], None] | None = None,
    control=None,
) -> None:
    """Connect, subscribe to Indoor Bike Data, and reconnect forever on drops. With address set, only
    that trainer is accepted; after a first pairing, on_connected(address, name) lets the caller pin it.
    control (bridge.trainer.TrainerControl) is attached to each connection to set resistance."""
    from bleak import BleakClient

    failures = 0
    while True:
        on_state("scanning")
        device = await find_trainer(name_hint, address=address)
        if device is None:
            await asyncio.sleep(reconnect_delay)
            continue
        disconnected = asyncio.Event()
        subscribed = False
        try:
            on_state("connecting")
            async with BleakClient(
                device, disconnected_callback=lambda _: disconnected.set()
            ) as client:
                log.info("connected to %s", device.address)
                if client.services.get_characteristic(INDOOR_BIKE_DATA) is None:
                    # 2026-09-28: the KICKR (fw 1.5.36) connected but offered only its basic services
                    # (1800/1801/180a), no FTMS; seen when something else holds it or it needs a
                    # power-cycle. Say so plainly and retry instead of logging a traceback.
                    seen = sorted({s.uuid[4:8] for s in client.services})
                    log.warning("trainer connected but isn't offering its fitness data (FTMS); services "
                                "seen: %s. Another app may be holding it, or it needs a power-cycle.", seen)
                    on_state("no FTMS data")
                    raise ConnectionError("trainer offers no FTMS Indoor Bike Data")
                on_state("connected")
                await client.start_notify(INDOOR_BIKE_DATA, lambda _, d: on_packet(bytes(d)))
                subscribed = True
                failures = 0
                if address is None:
                    address = device.address.upper()  # pin for reconnects in this run too
                    if on_connected:
                        on_connected(address, device.name or "")
                if control is not None:
                    await control.attach(client)  # never raises: resistance is optional
                try:
                    await disconnected.wait()
                finally:
                    if control is not None:
                        control.detach()
                log.warning("trainer disconnected")
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise  # the bridge itself is shutting down (Ctrl+C, end of --sim)
            # bleak on Windows reports a WinRT operation cancelled by the OS or the trainer (e.g. the
            # link dropped during service discovery) as CancelledError. Friend's test 2026-09-27
            # 20:23: this escaped the handler below and crashed the bridge instead of retrying.
            log.warning("connection to %s was cancelled by Windows/the trainer; retrying", device.address)
        except ConnectionError as e:
            log.debug("connect attempt failed: %s", e)
        except Exception:
            log.exception("bluetooth error")
        if not subscribed:
            failures += 1
            if failures == 3:
                msg = ("can't get data from the trainer after 3 tries. Close Zwift, the Wahoo app and any "
                       "phone app connected to it; if it's paired in Windows Bluetooth settings, remove it "
                       "there; then power-cycle the trainer (unplug it for 10 s). Still retrying...")
                log.warning(msg)
                print(f"\n{msg}", flush=True)
        on_state("disconnected")
        await asyncio.sleep(reconnect_delay)
