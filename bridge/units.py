"""Little shared units and formats. A leaf module: it imports nothing from the bridge, so anything can use it."""

KM_PER_MILE = 1.609344


def hms(seconds: float) -> str:
    """1:05 for 65 s, 1:02:03 from an hour up."""
    s = int(round(seconds or 0))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
