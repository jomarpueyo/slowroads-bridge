"""Calibrate the bridge's car model from the real speedometer (screen OCR) and save settings.json.

CALIBRATION ONLY: screen OCR is used here to measure the game's response. The ride bridge
(python -m bridge) never reads the screen; it loads the settings this tool saves.

Before running: Slow Roads in the foreground, gearbox AUTOMATIC (or electric), car stopped,
auto-steer on, speedometer (bottom right) not covered. No bike needed. ~40 s.

Sequence (trigger values sent to the virtual pad):
  ramp          0 -> 0.8 over 16 s
  coast_1       6 s
  throttle_0.6  8 s
  brake_0.5     2 s   (only above 30 km/h, so it cannot reach a stop and reverse)
  coast_2       10 s

Fit: one least-squares model over every sample, the same one bridge/drive.py uses:
    dv/dt = accel*throttle - brake_rate*brake - coast - drag_quad*v^2,  capped at top_speed
with the game's response delay chosen to minimise error. The model is then replayed against
the speedometer and the RMS error reported.

Outputs: logs/calibrate-*.csv (every sample), logs/calibrate-*.log (summary), settings.json.

Usage:
  python tools/calibrate_car.py                     full calibration, saves settings.json
  python tools/calibrate_car.py --refit FILE.csv    re-fit a saved run, saves settings.json
  python tools/calibrate_car.py --watch 30          only log the speedometer (no inputs sent)
"""

import argparse
import csv
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge import settings  # noqa: E402
from bridge.drive import DriveConfig, step_car  # noqa: E402
from bridge.ridelog import LOG_DIR, setup_event_log, timestamp  # noqa: E402

log = logging.getLogger("calibrate")
HZ = 10
MAX_JUMP_KMH_S = 80.0  # reject OCR readings implying a faster change than this
MOVING_KMH = 1.5
BRAKE_MIN_KMH = 30.0
DERIV_HALF_S = 0.3     # speed derivative from a least-squares slope over +-0.3 s
# km/h, open-loop replay over the whole ~40 s run; above this the fit is not trusted and
# settings are not saved. Run 09:20 (automatic) replayed at 8.3, mostly timing near the brake.
MAX_RMS_OK = 10.0

# (name, trigger at start, trigger at end, brake, seconds)
SEQUENCE = [
    ("ramp", 0.0, 0.8, 0.0, 16.0),
    ("coast_1", 0.0, 0.0, 0.0, 6.0),
    ("throttle_0.6", 0.6, 0.6, 0.0, 8.0),
    ("brake_0.5", 0.0, 0.0, 0.5, 2.0),
    ("coast_2", 0.0, 0.0, 0.0, 10.0),
]


class Sampler:
    def __init__(self, path: Path) -> None:
        from speedo import SpeedoReader

        self.reader = SpeedoReader()
        self.file = open(path, "w", newline="", encoding="utf-8")
        self.w = csv.writer(self.file)
        self.w.writerow(["t_s", "phase", "throttle", "brake", "kmh", "shown", "unit", "gear", "accepted", "ocr_text"])
        self.t0 = time.monotonic()
        self.samples = []  # (t, phase, trigger, brake, kmh), accepted readings only
        self.last = None   # (t, kmh)
        self.reads = self.good = 0

    def sample(self, phase: str, trigger: float, brake: float) -> float | None:
        t = time.monotonic() - self.t0
        r = self.reader.read()
        kmh = r.kmh
        ok = kmh is not None
        if ok and self.last is not None:
            dt = max(t - self.last[0], 1e-3)
            ok = abs(kmh - self.last[1]) / dt <= MAX_JUMP_KMH_S
        self.reads += 1
        if ok:
            self.good += 1
            self.last = (t, kmh)
            self.samples.append((t, phase, trigger, brake, kmh))
        self.w.writerow([f"{t:.3f}", phase, f"{trigger:.3f}", brake, "" if kmh is None else f"{kmh:.2f}",
                         "" if r.speed is None else r.speed, r.unit or "", r.gear or "", int(ok), r.text])
        self.file.flush()
        return kmh if ok else None


def run_phase(sampler, pad, name, start, end, brake, seconds) -> None:
    log.info("phase %s: trigger %.2f->%.2f brake %.2f for %.1f s", name, start, end, brake, seconds)
    t_start = time.monotonic()
    while (elapsed := time.monotonic() - t_start) < seconds:
        tick = time.monotonic()
        trigger = start + (end - start) * min(elapsed / seconds, 1.0)
        if pad:
            pad.set_controls(trigger, brake)
        sampler.sample(name, trigger, brake)
        time.sleep(max(0.0, 1 / HZ - (time.monotonic() - tick)))


def load_samples(csv_path: Path):
    with open(csv_path, encoding="utf-8") as f:
        return [(float(r["t_s"]), r["phase"], float(r["throttle"]), float(r["brake"]), float(r["kmh"]))
                for r in csv.DictReader(f) if r["accepted"] == "1" and r["kmh"]]


# ---------- fitting ----------

def slope(points) -> float | None:
    """Least-squares slope over (x, y) points."""
    if len(points) < 3:
        return None
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    den = sum((p[0] - mx) ** 2 for p in points)
    return sum((p[0] - mx) * (p[1] - my) for p in points) / den if den else None


def solve(a, b):
    """Solve the small linear system a x = b (Gaussian elimination, partial pivoting)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12:
            return None
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


def input_at(samples, t):
    """(throttle, brake) that was being sent at time t (last sample at or before t)."""
    lo, hi = 0, len(samples) - 1
    if t < samples[0][0]:
        return 0.0, 0.0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if samples[mid][0] <= t:
            lo = mid
        else:
            hi = mid - 1
    return samples[lo][2], samples[lo][3]


def top_speed(samples) -> float:
    return max((v for *_, v in samples), default=0.0)


def fit_with_delay(samples, delay: float):
    """Least squares for [accel, brake_rate, coast, drag_quad] at one response delay."""
    top = top_speed(samples)
    rows, ys = [], []
    for i, (t, _, _, _, v) in enumerate(samples):
        win = [(s[0], s[4]) for s in samples if abs(s[0] - t) <= DERIV_HALF_S]
        dv = slope(win)
        if dv is None:
            continue
        thr, brk = input_at(samples, t - delay)
        if v < MOVING_KMH and thr == 0 and brk == 0:
            continue  # stopped: drag does not apply
        if thr > 0 and v > top - 3.0:
            continue  # at the speed cap: accel is clipped, not modelled
        rows.append([thr, -brk, -1.0, -v * v])
        ys.append(dv)
    if len(rows) < 20:
        return None, float("inf")
    ata = [[sum(r[i] * r[j] for r in rows) for j in range(4)] for i in range(4)]
    aty = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(4)]
    x = solve(ata, aty)
    if x is None:
        return None, float("inf")
    resid = sum((sum(a * b for a, b in zip(r, x)) - y) ** 2 for r, y in zip(rows, ys)) / len(rows)
    return x, resid


def fit(samples) -> dict:
    best = (None, float("inf"), 0.0)
    for d10 in range(0, 9):  # 0.0 .. 0.8 s
        x, resid = fit_with_delay(samples, d10 / 10)
        if x is not None and resid < best[1]:
            best = (x, resid, d10 / 10)
    x, _, delay = best
    if x is None:
        return {"accel": None}
    accel, brake, coast, quad = x
    return {
        "accel": accel,
        "brake_rate": max(brake, 0.0),
        "coast": max(coast, 0.0),
        "drag_quad": max(quad, 0.0),
        "top_speed": top_speed(samples),
        "delay_s": delay,
    }


BRAKE_REACT_S = 0.4  # run 09:20: speed barely changed for ~0.4 s after the brake went on


def brake_from_window(samples, res: dict) -> float | None:
    """Brake rate from the brake phase alone: observed decel after the reaction delay, minus
    the fitted drag at that speed, divided by the brake value. Used when the joint fit cannot
    separate braking from drag (the 09:20 run braked for only 1 s)."""
    pts = [s for s in samples if s[1] == "brake_0.5"]
    if len(pts) < 3:
        return None
    t0 = pts[0][0] + BRAKE_REACT_S
    win = [(t, v) for t, _, _, _, v in pts if t >= t0]
    # include the first coast samples too: the brake keeps acting for the reaction delay after release
    after = [(t, v) for t, ph, _, _, v in samples if ph == "coast_2" and t <= pts[-1][0] + BRAKE_REACT_S]
    dv = slope(win + after)
    if dv is None:
        return None
    v_mid = sum(v for _, v in win + after) / len(win + after)
    drag = res["coast"] + res["drag_quad"] * v_mid * v_mid
    return max((-dv - drag) / pts[0][3], 0.0)


def model_error(samples, res: dict) -> float | None:
    """Replay the logged inputs (with the fitted delay) through the bridge's car model and
    compare with the speedometer. RMS in km/h."""
    if not samples or res.get("accel") is None:
        return None
    cfg = DriveConfig(accel_kmh_s=res["accel"], coast_kmh_s=res["coast"], drag_quad=res["drag_quad"],
                      brake_kmh_s=res["brake_rate"], top_speed_kmh=res["top_speed"])
    v, prev_t, errs = samples[0][4], samples[0][0], []
    for t, _, _, _, measured in samples[1:]:
        thr, brk = input_at(samples, t - res["delay_s"])
        v = step_car(v, thr, brk, t - prev_t, cfg)
        errs.append(v - measured)
        prev_t = t
    return (sum(e * e for e in errs) / len(errs)) ** 0.5


def report_and_save(samples, source: Path) -> int:
    res = fit(samples)
    if res["accel"] is None:
        log.error("not enough readings to fit; see %s", source)
        return 1
    if res["brake_rate"] < 1.0:
        direct = brake_from_window(samples, res)
        log.info("joint fit could not separate braking (%.2f); brake-window estimate: %s",
                 res["brake_rate"], "n/a" if direct is None else f"{direct:.1f}")
        if direct:
            res["brake_rate"] = direct
    for k, v in res.items():
        log.info("fit %-10s %.5g", k, v)
    moving = [s for s in samples if s[1] == "ramp" and s[4] > 0.3]
    if moving:
        log.info("car first moved at trigger %.2f on the ramp", moving[0][2])
    rms = model_error(samples, res)
    log.info("model replay RMS error: %.1f km/h", rms)
    if rms > MAX_RMS_OK:
        log.error("fit error %.1f km/h is above %.0f: settings NOT saved (check gearbox is Automatic)",
                  rms, MAX_RMS_OK)
        return 1
    ride = {k: res[k] for k in ("accel", "coast", "drag_quad", "brake_rate", "top_speed")}
    ride["deadzone"] = 0.0
    settings.save(ride, {"source": source.name, "rms_kmh": round(rms, 2), "delay_s": res["delay_s"],
                         "saved": time.strftime("%Y-%m-%d %H:%M:%S")})
    log.info("saved %s: %s", settings.SETTINGS_PATH, {k: round(v, 4) for k, v in ride.items()})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=float, help="only log the speedometer for N seconds; send no inputs")
    ap.add_argument("--refit", type=Path, help="re-fit a saved calibrate-*.csv instead of driving")
    ap.add_argument("--countdown", type=float, default=5.0)
    args = ap.parse_args()

    stamp = timestamp()
    log_path = setup_event_log(LOG_DIR, stamp, verbose=True, prefix="calibrate")
    print(f"log: {log_path}")

    if args.refit:
        log.info("refit %s", args.refit)
        return report_and_save(load_samples(args.refit), args.refit)

    csv_path = LOG_DIR / f"calibrate-{stamp}.csv"
    sampler = Sampler(csv_path)
    print(f"samples: {csv_path}")
    log.info("speedometer region %s", sampler.reader.region)
    first = sampler.reader.read()
    log.info("first read: %r -> %s %s gear %s", first.text, first.speed, first.unit, first.gear)
    if first.speed is None:
        log.error("speedometer not readable: is Slow Roads in front with the HUD bottom-right uncovered?")
        return 1

    if args.watch:
        run_phase(sampler, None, "watch", 0.0, 0.0, 0.0, args.watch)
        log.info("watch done: %d reads, %d accepted", sampler.reads, sampler.good)
        return 0

    from bridge.pad import VirtualPad

    if first.kmh and first.kmh > 2:
        log.error("car is moving (%.1f km/h); stop it first", first.kmh)
        return 1
    # Create the pad before the countdown: run 09:12 created it at the first throttle command
    # and the game never responded; the trigger sweep created it 5 s early and worked.
    pad = VirtualPad()
    pad.set_controls(0.0, 0.0)
    try:
        for i in range(int(args.countdown), 0, -1):
            print(f"starting in {i}... (keep Slow Roads in front)", flush=True)
            time.sleep(1)
        for name, start, end, brk, secs in SEQUENCE:
            if name == "coast_1" and not any(v > MOVING_KMH for *_, v in sampler.samples):
                log.error("car never moved on the ramp: the game is not reading the virtual pad "
                          "(click into the game window, unplug real controllers, re-run)")
                return 1
            if brk > 0:
                v = sampler.last[1] if sampler.last else 0.0
                if v <= BRAKE_MIN_KMH:
                    log.info("skipping %s: %.1f km/h is below %.0f", name, v, BRAKE_MIN_KMH)
                    continue
            run_phase(sampler, pad, name, start, end, brk, secs)
    finally:
        pad.close()
        sampler.file.close()

    log.info("reads %d, accepted %d (%.0f%%)", sampler.reads, sampler.good,
             100 * sampler.good / max(sampler.reads, 1))
    return report_and_save(sampler.samples, csv_path)


if __name__ == "__main__":
    sys.exit(main())
