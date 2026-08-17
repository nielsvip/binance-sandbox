"""Pure lifecycle timestamp parsing shared by live positions and exits."""

from datetime import datetime, timezone
import math


def parse_position_timestamp(value):
    """Return an aware UTC datetime, or None when the value is unusable."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        try:
            return datetime.fromtimestamp(
                numeric / (1000.0 if numeric > 1e12 else 1.0), tz=timezone.utc
            )
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        candidate = value.strip()
        try:
            numeric = float(candidate[:-1] if candidate[-1:] in ("Z", "z") else candidate)
        except (TypeError, ValueError):
            numeric = None
        if numeric is not None:
            return parse_position_timestamp(numeric)
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00").replace("z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None
