"""Speed-follow controller: bike speed -> target car speed -> throttle and brake.

Slow Roads treats the right trigger as an accelerator: any steady partial throttle keeps
adding speed (ride 2026-09-27 08:53: throttle averaged 0.1-0.3 and the car still ran away).
So instead of mapping watts straight to throttle, we:

1. Take the trainer's own speed (it already models coasting) times a gear ratio as the
   target car speed.
2. Keep an open-loop estimate of the car's speed from what we have sent it
   (accel at full throttle, coast drag, brake decel -- all km/h per second).
3. Drive throttle/brake to close the gap between estimate and target.

There is no game telemetry, so the estimate is only as good as the three rates.
Measure them with tools/calibrate_car.py.

Brake safety: Slow Roads turns a brake held at a stop into reverse (patch 1.0.1), so the
brake is only used while the estimated car speed is above brake_floor_kmh, and never held
for more than max_brake_hold_s without a rest.
"""

from dataclasses import dataclass


@dataclass
class DriveConfig:
    gear_ratio: float = 3.0          # car km/h per bike km/h
    accel_kmh_s: float = 6.0         # car acceleration at full throttle (calibrate)
    coast_kmh_s: float = 1.0         # constant part of drag, km/h/s (calibrate)
    drag_quad: float = 0.0           # speed-squared drag, km/h/s per (km/h)^2 (calibrate)
    brake_kmh_s: float = 12.0        # car deceleration at full brake (calibrate)
    top_speed_kmh: float = 0.0       # car's speed cap; 0 = none (calibrate)
    target_tau_s: float = 1.0        # smoothing on the 1 Hz trainer speed
    kp_throttle: float = 0.08        # throttle per km/h below target
    kp_brake: float = 0.08           # brake per km/h above target (beyond deadband)
    brake_deadband_kmh: float = 4.0  # overspeed tolerated before braking
    max_throttle: float = 0.6
    max_brake: float = 0.6
    throttle_ramp_up: float = 0.2    # max throttle increase per second
    throttle_ramp_down: float = 1.5  # max throttle decrease per second
    brake_floor_kmh: float = 8.0     # never brake below this estimated car speed
    max_brake_hold_s: float = 3.0
    brake_rest_s: float = 1.0


def trigger_value(throttle: float, deadzone: float) -> float:
    """Map controller throttle (0..1, what moves the car) onto the trigger past the game's
    dead zone. Calibration 2026-09-27: 0.3 trigger for 15 s did not move the car; 0.6 did."""
    if throttle <= 0.0:
        return 0.0
    return deadzone + (1.0 - deadzone) * min(throttle, 1.0)


def bike_speed_from_power(watts: float, mass_kg: float = 85.0, cda: float = 0.32, crr: float = 0.004,
                          grade: float = 0.0, rho: float = 1.225, eta: float = 0.97) -> float:
    """Steady road-bike speed (km/h) for a power, from the standard cycling equation:
    P = v (m g (Crr cos a + sin a) + 0.5 rho CdA v^2) / eta. The KICKR's own speed is flywheel
    speed (gear x cadence), so this is what makes effort, not gear choice, set the pace
    (GTBike V computes speed the same way; docs/RESEARCH.md). 100 W flat ~ 25.9 km/h."""
    if watts <= 0:
        return 0.0
    import math

    a = math.atan(grade)
    lo, hi = 0.0, 40.0  # m/s
    for _ in range(60):
        v = (lo + hi) / 2
        need = v * (mass_kg * 9.81 * (crr * math.cos(a) + math.sin(a)) + 0.5 * rho * cda * v * v) / eta
        lo, hi = (v, hi) if need < watts else (lo, v)
    return lo * 3.6


class VirtualBike:
    """A simulated road bike stepped at the output rate (20 Hz) from the latest power reading.

    The KICKR reports once a second on every Bluetooth channel (FTMS, Cycling Power and Zwift's
    protocol all measured at 1.00 Hz, 2026-09-27), so readings can't come faster. Zwift and GTBike V
    get smooth motion from the same 1 Hz data by simulating the rider between readings: speed has
    inertia, so it moves continuously instead of stepping each second, surges build up, and coasting
    fades gently (about 0.7 km/h/s at 30 km/h on the flat).
        m dv/dt = P/v - (Crr m g + 0.5 rho CdA v^2)
    """

    def __init__(self, mass_kg: float = 85.0, cda: float = 0.32, crr: float = 0.004,
                 rho: float = 1.225, eta: float = 0.97) -> None:
        self.m, self.cda, self.crr, self.rho, self.eta = mass_kg, cda, crr, rho, eta
        self.v = 0.0  # m/s
        self.substep = 0.02  # s; replays of whole rides use a coarser step (still stable)

    @property
    def kmh(self) -> float:
        return self.v * 3.6

    def step(self, watts: float, dt: float) -> float:
        if dt <= 0:
            return self.kmh
        if not (watts == watts and abs(watts) != float("inf")):  # NaN/inf never reach the controller
            watts = 0.0
        # Integrate in small sub-steps so a large dt (a stalled loop) stays stable.
        n = max(1, int(dt / self.substep))
        h = dt / n
        for _ in range(n):
            drive = self.eta * max(watts, 0.0) / max(self.v, 1.0)  # force; capped at low speed
            resist = self.crr * self.m * 9.81 + 0.5 * self.rho * self.cda * self.v * self.v
            self.v = max(self.v + (drive - resist) / self.m * h, 0.0)
            if self.v < 0.05 and watts <= 0:
                self.v = 0.0
        return self.kmh


def car_accel(v_kmh: float, throttle: float, brake: float, c: DriveConfig) -> float:
    """Car model, km/h per second. Shared by the controller and tools/calibrate_car.py.
    Calibration 2026-09-27 09:20 (automatic): coasting shed ~23 km/h/s near 90 km/h but under
    2 km/h/s near 15 km/h, so drag has a speed-squared term; speed levelled off at ~92 km/h."""
    drag = (c.coast_kmh_s + c.drag_quad * v_kmh * v_kmh) if v_kmh > 0 else 0.0
    return c.accel_kmh_s * throttle - drag - c.brake_kmh_s * brake


def step_car(v_kmh: float, throttle: float, brake: float, dt: float, c: DriveConfig) -> float:
    v = max(v_kmh + car_accel(v_kmh, throttle, brake, c) * dt, 0.0)
    return min(v, c.top_speed_kmh) if c.top_speed_kmh > 0 else v


@dataclass
class DriveOutput:
    throttle: float
    brake: float
    target_kmh: float
    car_est_kmh: float


class SpeedController:
    def __init__(self, config: DriveConfig | None = None) -> None:
        self.config = config or DriveConfig()
        self.target_kmh = 0.0
        self.car_est_kmh = 0.0
        self.throttle = 0.0
        self.brake = 0.0
        self._bike_kmh = 0.0
        self._brake_held_s = 0.0
        self._brake_rest_left_s = 0.0

    @property
    def bike_kmh(self) -> float:
        return self._bike_kmh

    def set_bike_speed(self, kmh: float) -> None:
        self._bike_kmh = max(kmh, 0.0)

    def step(self, dt: float, active: bool = True) -> DriveOutput:
        """Advance dt seconds. active=False (stale trainer data) means coast: no throttle, no brake."""
        c = self.config
        goal = self._bike_kmh * c.gear_ratio if active else 0.0
        if c.top_speed_kmh > 0:
            goal = min(goal, 0.95 * c.top_speed_kmh)  # never chase a speed the car cannot hold
        alpha = min(dt / c.target_tau_s, 1.0)
        self.target_kmh += alpha * (goal - self.target_kmh)

        err = self.target_kmh - self.car_est_kmh
        want_throttle = min(max(c.kp_throttle * err, 0.0), c.max_throttle) if active else 0.0
        want_brake = 0.0
        if active and -err > c.brake_deadband_kmh and self.car_est_kmh > c.brake_floor_kmh:
            want_brake = min(c.kp_brake * (-err - c.brake_deadband_kmh), c.max_brake)

        # Brake hold limit: after max_brake_hold_s of braking, release for brake_rest_s.
        if self._brake_rest_left_s > 0:
            self._brake_rest_left_s -= dt
            want_brake = 0.0
        elif want_brake > 0:
            self._brake_held_s += dt
            if self._brake_held_s >= c.max_brake_hold_s:
                self._brake_held_s = 0.0
                self._brake_rest_left_s = c.brake_rest_s
        else:
            self._brake_held_s = 0.0

        if want_brake > 0:
            self.throttle = 0.0  # never overlap brake and throttle; cut immediately, no ramp
        else:
            delta = want_throttle - self.throttle
            delta = min(delta, c.throttle_ramp_up * dt) if delta > 0 else max(delta, -c.throttle_ramp_down * dt)
            self.throttle = min(max(self.throttle + delta, 0.0), c.max_throttle)
        self.brake = want_brake

        self.car_est_kmh = step_car(self.car_est_kmh, self.throttle, self.brake, dt, c)
        return DriveOutput(self.throttle, self.brake, self.target_kmh, self.car_est_kmh)
