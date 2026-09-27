"""Probe the KICKR's Zwift trainer protocol channel: does it send riding data faster than FTMS (1 Hz)?

Protocol per Makinolo (docs/RESEARCH.md): service 00000001-19ca-..., notifications on ...0002,
control point ...0003, responses on ...0004. The app writes "RideOn" to the control point; the
trainer then sends protobuf messages. Message id 0x03 is riding data: field 1 power (W), field 2
cadence (rpm), field 3 speed x100 (km/h), field 4 HR. This tool only sends the handshake: no
resistance, gear or ERG commands. It also subscribes to FTMS 0x2AD2 for a side-by-side rate.

Usage: python tools/probe_zwift.py --handshake [--seconds 30]   (pedal while it runs)
It writes to the trainer, so it refuses to run without --handshake (docs/SECURITY.md finding 8).
Writes logs/probe-zwift-<stamp>.log and a per-message CSV.
"""

import argparse
import asyncio
import csv
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bleak import BleakClient  # noqa: E402

from bridge import settings  # noqa: E402
from bridge.ftms import INDOOR_BIKE_DATA, find_trainer, parse_indoor_bike_data  # noqa: E402
from bridge.ridelog import LOG_DIR, setup_event_log, timestamp  # noqa: E402

log = logging.getLogger("probe-zwift")
ZW = "-19ca-4651-86e5-fa29dcdd09d1"
NOTIFY, CONTROL, RESPONSE = "00000002" + ZW, "00000003" + ZW, "00000004" + ZW


def varints(buf: bytes, i: int = 0) -> dict:
    """Minimal protobuf decode: {field: value} for varint fields (wire type 0) at top level."""
    out = {}
    while i < len(buf):
        key, i = _varint(buf, i)
        field, wt = key >> 3, key & 7
        if wt == 0:
            out[field], i = _varint(buf, i)
        elif wt == 2:
            n, i = _varint(buf, i)
            i += n
        elif wt == 5:
            i += 4
        elif wt == 1:
            i += 8
        else:
            break
    return out


def _varint(buf, i):
    shift = val = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7
    return val, i


async def probe(seconds: float, stamp: str) -> None:
    device = await find_trainer(address=settings.load_trainer())  # pinned trainer only, if paired
    if device is None:
        sys.exit("trainer not found; close other apps and wake the KICKR")
    times = defaultdict(list)
    rows = []
    async with BleakClient(device) as client:
        chars = {c.uuid: c for s in client.services for c in s.characteristics}
        for u in (NOTIFY, CONTROL, RESPONSE):
            log.info("%s present=%s props=%s", u[:8], u in chars, getattr(chars.get(u), "properties", None))

        def handler(name):
            def h(_, data):
                t = time.monotonic()
                data = bytes(data)
                times[name].append(t)
                rows.append((t, name, data.hex(" ")))
            return h

        for u, name in ((NOTIFY, "zwift_0002"), (RESPONSE, "zwift_0004"), (INDOOR_BIKE_DATA, "ftms_2ad2")):
            if u in chars:
                try:
                    await client.start_notify(u, handler(name))
                except Exception as e:
                    log.warning("subscribe %s failed: %s", name, e)
        await asyncio.sleep(1.0)
        log.info("writing RideOn handshake")
        await client.write_gatt_char(CONTROL, b"RideOn", response=True)
        print(f"pedal now; measuring {seconds:.0f} s...", flush=True)
        await asyncio.sleep(seconds)
    with open(LOG_DIR / f"probe-zwift-{stamp}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t", "channel", "hex", "msg_id", "decoded"])
        t0 = rows[0][0] if rows else 0
        for t, name, hx in rows:
            b = bytes.fromhex(hx)
            dec = varints(b, 1) if name == "zwift_0002" and b and b[0] == 0x03 else ""
            if name == "ftms_2ad2":
                bd = parse_indoor_bike_data(b)
                dec = {"power": bd.power_w, "cadence": bd.cadence_rpm, "speed": bd.speed_kmh}
            w.writerow([f"{t - t0:.3f}", name, hx, f"0x{b[0]:02x}" if b else "", dec])
    for name, ts in times.items():
        rate = (len(ts) - 1) / (ts[-1] - ts[0]) if len(ts) > 1 and ts[-1] > ts[0] else 0.0
        ids = sorted({r[2][:2] for r in rows if r[1] == name})
        log.info("%-11s %4d msgs  %.2f Hz  first bytes %s", name, len(ts), rate, ids)
    for t, name, hx in [r for r in rows if r[1] == "zwift_0002"][:3] + [r for r in rows if r[1] == "zwift_0004"][:3]:
        log.info("sample %s: %s", name, hx)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--handshake", action="store_true",
                    help="required: confirm sending the Zwift 'RideOn' handshake to the trainer")
    args = ap.parse_args()
    if not args.handshake:
        sys.exit("This probe writes the Zwift 'RideOn' handshake to your trainer (no resistance, gear or ERG "
                 "commands). Re-run with --handshake to confirm. If the trainer ever behaves oddly "
                 "afterwards, unplug it for a few seconds.")
    stamp = timestamp()
    path = setup_event_log(LOG_DIR, stamp, verbose=True, prefix="probe-zwift")
    print(f"log: {path}")
    asyncio.run(probe(args.seconds, stamp))


if __name__ == "__main__":
    main()
