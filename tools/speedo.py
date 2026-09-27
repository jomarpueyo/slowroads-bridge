"""Read the Slow Roads speedometer off the screen with Windows' built-in OCR.

Read-only: captures a screen region and OCRs it; nothing is sent to or read from the game process.
Default region is the bottom-right quadrant of the primary monitor.
"""

import asyncio
import logging
import re
from dataclasses import dataclass

import mss
from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

log = logging.getLogger("bridge.speedo")
# HUD (2026-09-27): "0.0 | MILES PER HOUR | 1 | GEAR" -- speed with one decimal, unit spelled out.
# The HUD always shows one decimal; OCR sometimes drops the point ("29 7" for 29.7).
NUM = r"(\d{1,3}(?:\s?[.,]\s?\d| \d\b)?)"
# Anchor on "PER": OCR mangles MILES ("NILES", "RILES", "BULES", "mlL€s") and HOUR, but reads
# PER reliably (calibration run 2026-09-27 09:13). Unit: km/h only if KILO/KM appears.
# With the speed limit on, a padlock icon sits between number and label and OCRs as a stray
# token ("0.0 a | NILES PER"), so allow up to a couple of short tokens before the unit word.
UNIT_RE = re.compile(NUM + r"(?:\s*\|?\s*\S{1,3}(?=\s*\|))?\s*\|?\s*(\S{0,12})\s*PER\b", re.IGNORECASE)
KMH_RE = re.compile(r"KILO|KM", re.IGNORECASE)
GEAR_RE = re.compile(r"\b([1-9RN])\W{0,5}GEAR", re.IGNORECASE)
MPH_TO_KMH = 1.609344


@dataclass
class SpeedReading:
    speed: float | None  # as displayed
    unit: str | None     # "mph" or "km/h"
    gear: str | None
    text: str

    @property
    def kmh(self) -> float | None:
        if self.speed is None:
            return None
        return self.speed * MPH_TO_KMH if self.unit == "mph" else self.speed


def parse_speed(text: str) -> SpeedReading:
    """Speed is the number immediately before the unit label; gear the character before GEAR."""
    gear_m = GEAR_RE.search(text)
    gear = gear_m.group(1).upper() if gear_m else None
    m = UNIT_RE.search(text)
    if not m:
        return SpeedReading(None, None, gear, text)
    unit = "km/h" if KMH_RE.search(m.group(2)) else "mph"
    raw = m.group(1).replace(",", ".")
    if "." not in raw and " " in raw:
        raw = raw.replace(" ", ".", 1)  # "29 7" -> 29.7
    value = float(raw.replace(" ", ""))
    return SpeedReading(value, unit, gear, text)


class SpeedoReader:
    def __init__(self, region: dict | None = None, scale: int = 2) -> None:
        self._sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
        mon = self._sct.monitors[1]
        # Speedometer box only (1920x1080: x 1420-1640, y 880-1020), scaled to the monitor.
        # A whole quadrant picked up other windows' text, so keep this tight.
        w, h = mon["width"], mon["height"]
        self.region = region or {
            "left": mon["left"] + int(w * 0.740),
            "top": mon["top"] + int(h * 0.815),
            "width": int(w * 0.115),
            "height": int(h * 0.130),
        }
        self.scale = scale  # upscaling helps OCR on small HUD digits
        self._engine = OcrEngine.try_create_from_user_profile_languages()
        if self._engine is None:
            raise RuntimeError("Windows OCR unavailable: add a language with OCR support in Settings")

    def grab(self):
        from PIL import Image

        shot = self._sct.grab(self.region)
        img = Image.frombytes("RGB", shot.size, shot.rgb)
        if self.scale != 1:
            img = img.resize((img.width * self.scale, img.height * self.scale), Image.LANCZOS)
        return img

    async def _ocr(self, img) -> str:
        bgra = img.convert("RGBA").tobytes("raw", "BGRA")
        writer = DataWriter()
        writer.write_bytes(bgra)
        bitmap = SoftwareBitmap(BitmapPixelFormat.BGRA8, img.width, img.height, BitmapAlphaMode.PREMULTIPLIED)
        bitmap.copy_from_buffer(writer.detach_buffer())
        result = await self._engine.recognize_async(bitmap)
        return " | ".join(line.text for line in result.lines)

    async def read_async(self, img=None) -> SpeedReading:
        img = img if img is not None else self.grab()
        return parse_speed(await self._ocr(img))

    def read(self, img=None, timeout: float = 2.0) -> SpeedReading:
        """OCR with a timeout. Windows OCR occasionally never completes (ride 11:39: the recorder
        froze 7 s in and stayed frozen for 15 min), so each call runs on a daemon thread; a timed-out
        call is abandoned, the engine is re-created, and an empty reading is returned."""
        import threading

        img = img if img is not None else self.grab()
        box: dict = {}

        def work():
            try:
                box["text"] = asyncio.run(self._ocr(img))
            except Exception as e:  # surface as an empty reading
                box["error"] = repr(e)

        t = threading.Thread(target=work, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            self.timeouts = getattr(self, "timeouts", 0) + 1
            self._engine = OcrEngine.try_create_from_user_profile_languages()
            return SpeedReading(None, None, None, "<ocr timeout>")
        if "error" in box:
            return SpeedReading(None, None, None, f"<ocr error {box['error']}>")
        return parse_speed(box["text"])
