"""Throttle mapping: smoothed watts -> 0..1 throttle with floor, ceiling and curve."""

import math
from dataclasses import dataclass


@dataclass
class MapperConfig:
    p_min: float = 50.0
    p_max: float = 250.0
    gamma: float = 1.0
    tau_s: float = 2.0
    stale_s: float = 3.0


class ThrottleMapper:
    def __init__(self, config: MapperConfig | None = None) -> None:
        self.config = config or MapperConfig()
        self.smoothed = 0.0
        self.last_t: float | None = None

    def add_sample(self, watts: float, now: float) -> None:
        if self.last_t is None:
            self.smoothed = watts
        else:
            # Time-based alpha keeps smoothing identical whatever rate the trainer notifies at.
            alpha = 1 - math.exp(-(now - self.last_t) / self.config.tau_s)
            self.smoothed += alpha * (watts - self.smoothed)
        self.last_t = now

    def is_stale(self, now: float) -> bool:
        return self.last_t is None or now - self.last_t > self.config.stale_s

    def throttle(self, now: float | None = None) -> float:
        if now is not None and self.is_stale(now):
            return 0.0
        c = self.config
        x = (self.smoothed - c.p_min) / (c.p_max - c.p_min)
        return min(max(x, 0.0), 1.0) ** c.gamma
