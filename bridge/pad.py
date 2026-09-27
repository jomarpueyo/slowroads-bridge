"""Virtual Xbox 360 pad output. Throttle -> right trigger, brake -> left trigger."""

import logging

log = logging.getLogger("bridge.pad")


class VirtualPad:
    def __init__(self) -> None:
        import vgamepad as vg

        self._pad = vg.VX360Gamepad()
        log.info("virtual Xbox 360 pad created")

    def set_controls(self, throttle: float, brake: float = 0.0) -> None:
        self._pad.right_trigger_float(value_float=throttle)
        self._pad.left_trigger_float(value_float=brake)
        self._pad.update()

    def set_throttle(self, value: float) -> None:
        self.set_controls(value, 0.0)

    def close(self) -> None:
        self._pad.reset()
        self._pad.update()
        log.info("virtual pad reset")


class NullPad:
    """Stand-in for --dry-run: records the last values instead of driving a device."""

    def __init__(self) -> None:
        self.throttle = 0.0
        self.brake = 0.0

    def set_controls(self, throttle: float, brake: float = 0.0) -> None:
        self.throttle, self.brake = throttle, brake

    def set_throttle(self, value: float) -> None:
        self.set_controls(value, 0.0)

    def close(self) -> None:
        self.throttle = self.brake = 0.0
