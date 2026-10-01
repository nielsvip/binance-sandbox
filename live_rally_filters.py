"""Live twin of vec_decisions/hardcoded_rally_filters.RallyFilters.ok (RE/001). All 5 filters default OFF = today's behaviour.
Enabled filter with missing data BLOCKS (same as the vector)."""
from datetime import datetime, timezone


def _f(v, d=0.0):
    try:
        x = float(v)
        return d if x != x else x
    except Exception:
        return d


def age_minutes(ts):
    if ts is None or ts == "":
        return None
    try:
        t = ts if not isinstance(ts, str) else datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 60.0
    except Exception:
        return None


def rally_ok(config, indicators, is_long, px, last_exit, age_min):
    try:
        min_move = _f(getattr(config, "HARDCODED_RALLY_MIN_MOVE_PCT", 0.0))
        min_age = _f(getattr(config, "HARDCODED_RALLY_MIN_AGE_MIN", 0.0))
        tf = str(getattr(config, "HARDCODED_RALLY_HTF_TREND_TF", "OFF") or "OFF").strip()
        tfs = [] if tf.upper() in ("OFF", "") else [t.strip() for t in tf.split(",") if t.strip()]
        dc_max = _f(getattr(config, "HARDCODED_RALLY_DC_POS_MAX", 0.0))
        sma = bool(getattr(config, "HARDCODED_RALLY_SMA200_SIDE_ENABLED", False))
        if not (min_move > 0 or min_age > 0 or tfs or dc_max > 0 or sma):
            return True
        ind = indicators or {}
        if min_move > 0 and last_exit and last_exit > 0:
            mv = (px - last_exit) / last_exit * 100.0
            if (mv if is_long else -mv) < min_move:
                return False
        if min_age > 0 and (age_min is None or age_min < min_age):
            return False
        for t in tfs:
            v = ind.get("wt1_" + t)
            if v is None:
                return False
            v = _f(v)
            if (v < 0) if is_long else (v > 0):
                return False
        if dc_max > 0:
            d = ind.get("dc_position_15m")
            if d is None:
                return False
            d = _f(d)
            if (d > dc_max) if is_long else (d < 1.0 - dc_max):
                return False
        if sma:
            s = _f(ind.get("sma_200_15m"))
            if s <= 0:
                return False
            if (px <= s) if is_long else (px >= s):
                return False
        return True
    except Exception:
        return True
