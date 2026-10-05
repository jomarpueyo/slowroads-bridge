"""Sound cues for things you would otherwise have to read on the console (you're on the bike).

Played on a background thread with winsound.Beep so the 20 Hz control loop never waits for audio.
Turn off with --no-sounds or "sounds": 0 in settings.json.
"""

import logging
import queue
import sys
import threading

log = logging.getLogger("bridge.cues")

# (frequency Hz, duration ms). Rising = good news, falling/low = needs attention.
TONES = {
    "connected": [(660, 120), (880, 180)],
    "trainer_lost": [(440, 200), (330, 350)],
    "focus_lost": [(300, 140), (300, 140), (300, 140)],
    "focus_back": [(880, 110)],
    "resync": [(520, 80), (520, 80)],
    "paused": [(500, 300)],
    "resumed": [(500, 110), (750, 170)],
    "gear": [(700, 70)],
    "ride_end": [(880, 150), (660, 150), (440, 350)],
    "break": [(600, 150), (800, 150), (600, 200)],          # stand up and stretch
    "record": [(784, 100), (988, 100), (1175, 260)],         # new personal best / FTP result
    "milestone": [(660, 120), (990, 240)],                   # lifetime miles, halfway, last 5 min
    "block": [(880, 90), (880, 90)],                         # workout block change
    "workout_done": [(660, 120), (880, 120), (1175, 300)],
}


class Cues:
    def __init__(self, enabled: bool = True, beep=None) -> None:
        self._beep = beep
        self.enabled = enabled and (beep is not None or sys.platform == "win32")
        self._q: queue.Queue = queue.Queue(maxsize=8)
        self._thread: threading.Thread | None = None

    def play(self, name: str) -> None:
        if not self.enabled or name not in TONES:
            return
        log.debug("cue %s", name)
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="cues", daemon=True)
            self._thread.start()
        try:
            self._q.put_nowait(name)
        except queue.Full:  # a burst of events: drop rather than fall behind
            pass

    def close(self, wait: float = 1.5) -> None:
        """Let queued cues (e.g. ride_end) finish, then stop the thread."""
        if self._thread is None:
            return
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(wait)

    def _run(self) -> None:
        beep = self._beep
        if beep is None:
            import winsound

            beep = winsound.Beep
        while True:
            name = self._q.get()
            if name is None:
                return
            for freq, ms in TONES[name]:
                try:
                    beep(freq, ms)
                except Exception as e:  # no audio device etc.: cues are optional
                    log.debug("beep failed: %s", e)
                    return
