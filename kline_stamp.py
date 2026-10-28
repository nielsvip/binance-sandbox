"""kline_stamp - the ONE formatter for kline timestamps (operator law 2026-10-28: every kline/NPZ stamp is UTC, 27 chars, YYYY-MM-DDTHH:MM:SS.ffffffZ).

canonical_stamp(value)   -> str   any accepted input -> the canonical stamp; ValueError for NaT / unparseable.
is_canonical(value)      -> bool
normalize_rows(rows, drop_day_duplicates=False)
    -> (rows', stats)     rows' has every "timestamp" (and "timestamp_dt" when it is a real stamp) canonical. A date-only stamp ("YYYY-MM-DD",
                          "YYYY-MM-DD.000000Z") is that day's bar at 00:00:00Z. With drop_day_duplicates=True a date-only row is DROPPED when the same
                          calendar day already has a normal (non date-only) row; the default converts only and never removes a row (klines_guard /
                          klines_vault forbid shrinking a store).
Date-only inputs are the only ones that need a convention; everything else keeps its instant (+00:00 / no fraction / epoch seconds or ms).
"""
import re
from datetime import datetime, timezone

CANONICAL = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$")
DAY_ONLY = re.compile(r"^(\d{4}-\d\d-\d\d)(?:\.0+Z)?$")


def is_canonical(value):
    return isinstance(value, str) and CANONICAL.match(value) is not None


def canonical_stamp(value):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"not a timestamp: {value!r}")
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000.0 if abs(float(value)) > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if hasattr(value, "strftime") and not isinstance(value, str):
        stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    text = str(value).strip()
    if text in ("", "NaT", "nan", "NaN", "None", "null"):
        raise ValueError(f"not a timestamp: {value!r}")
    if CANONICAL.match(text):
        return text
    match = DAY_ONLY.match(text)
    if match:
        return match.group(1) + "T00:00:00.000000Z"
    stamp = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def normalize_rows(rows, drop_day_duplicates=False):
    stats = {"converted": 0, "dropped": 0, "unparseable": 0, "secondary_nulled": 0}
    normal_days = set()
    if drop_day_duplicates:
        for row in rows:
            value = row.get("timestamp") if isinstance(row, dict) else None
            if isinstance(value, str) and not DAY_ONLY.match(value.strip()):
                normal_days.add(value.strip()[:10])
    out = []
    for row in rows:
        if not isinstance(row, dict) or "timestamp" not in row:
            out.append(row)
            continue
        value = row["timestamp"]
        if drop_day_duplicates and isinstance(value, str) and DAY_ONLY.match(value.strip()) and value.strip()[:10] in normal_days:
            stats["dropped"] += 1
            continue
        try:
            fixed = canonical_stamp(value)
        except ValueError:
            stats["unparseable"] += 1
            out.append(row)
            continue
        new = row
        if fixed != value:
            new = dict(row, timestamp=fixed)
            stats["converted"] += 1
        if "timestamp_dt" in new:
            secondary = new["timestamp_dt"]
            try:
                fixed_dt = canonical_stamp(secondary)
            except ValueError:
                fixed_dt = None
            if fixed_dt != secondary:
                new = dict(new, timestamp_dt=fixed_dt if fixed_dt is not None else fixed)
                stats["secondary_nulled"] += 1
        out.append(new)
    return out, stats
