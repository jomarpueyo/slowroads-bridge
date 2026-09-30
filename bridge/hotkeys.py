"""Ride hotkeys, read while Slow Roads is the focused window (so you don't have to alt-tab).

F6 gear down, F7 gear up, F8 pause/resume, F9 re-sync the speed limit, F10 show/hide the overlay.

Keys are polled with GetAsyncKeyState, not registered: nothing is taken away from the game or other
programs, and a key only counts when the game window is in front. Turn off with --no-hotkeys.
"""

import sys

KEYS = {"F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79}
ACTIONS = {"F6": "gear_down", "F7": "gear_up", "F8": "pause", "F9": "resync", "F10": "overlay"}
HELP = "F6/F7 gear -/+   F8 pause   F9 re-sync limit   F10 overlay"


def _async_key_down(vk: int) -> bool:
    import ctypes

    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


class Hotkeys:
    def __init__(self, enabled: bool = True, key_down=None) -> None:
        self._key_down = key_down or (_async_key_down if sys.platform == "win32" else None)
        self.enabled = enabled and self._key_down is not None
        self._held: set[str] = set()

    def poll(self, focused: bool) -> list[str]:
        """Actions whose key went down since the last poll (one per press, not repeated while held)."""
        if not self.enabled:
            return []
        fired = []
        for name, vk in KEYS.items():
            down = self._key_down(vk)
            if down and name not in self._held and focused:
                fired.append(ACTIONS[name])
            if down:
                self._held.add(name)
            else:
                self._held.discard(name)
        return fired
