"""Causal predecessor values for Tradier daily-parent STDEV exits."""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np


def previous_completed_parent_values(
    current_values: Iterable[float], parent_tokens: Iterable[object]
) -> np.ndarray:
    """Return each row's prior parent's final value, or NaN if unavailable.

    Daily %B is forward-filled by parent timestamp but can vary within a
    parent as intraday closes arrive. The predecessor must therefore be the
    final observed value from the previous distinct parent, not the preceding
    intraday row.
    """
    current = np.asarray(current_values, dtype=float)
    tokens = np.asarray(parent_tokens)
    if current.ndim != 1 or tokens.ndim != 1 or current.shape != tokens.shape:
        raise ValueError("current values and parent tokens must be matching 1D arrays")
    previous = np.full(current.shape, np.nan, dtype=float)
    have_parent = False
    previous_parent_value = np.nan
    current_token = None
    current_parent_value = np.nan
    for index, (value, token) in enumerate(zip(current, tokens)):
        if token is None or (isinstance(token, (float, np.floating)) and not math.isfinite(float(token))):
            have_parent = False
            current_token = None
            current_parent_value = np.nan
            previous_parent_value = np.nan
            continue
        if not math.isfinite(float(value)):
            continue
        if not have_parent:
            have_parent = True
            current_token = token
            current_parent_value = float(value)
            continue
        if token != current_token:
            previous_parent_value = current_parent_value
            current_token = token
            current_parent_value = float(value)
            previous[index] = previous_parent_value
            continue
        previous[index] = previous_parent_value
        current_parent_value = float(value)
    return previous


def _parent_available_at(token: object, timeframe: str) -> float:
    """Map a stable parent label to its stock-session availability epoch."""
    from datetime import datetime, time, timedelta, timezone
    from zoneinfo import ZoneInfo

    try:
        value = float(token)
        if not math.isfinite(value):
            return math.nan
        label_utc = datetime.fromtimestamp(value, timezone.utc)
        et = ZoneInfo("America/New_York")
        label_et = label_utc.astimezone(et)
        if timeframe == "D":
            # Stock daily NPZ labels may be UTC midnight or the live 16:00 ET
            # forming label. Both identify the same session date.
            return datetime.combine(label_utc.date(), time(16, 0), et).timestamp()
        if timeframe == "4h":
            session_date = label_et.date()
            if label_et.time() < time(12, 45):
                available = datetime.combine(session_date, time(12, 45), et)
            elif label_et.time() < time(16, 0):
                available = datetime.combine(session_date, time(16, 0), et)
            else:
                available = label_et + timedelta(hours=4)
            return available.timestamp()
        if timeframe == "1h":
            close = datetime.combine(label_et.date(), time(16, 0), et)
            return min(label_et + timedelta(hours=1), close).timestamp()
        seconds = {"5m": 300, "15m": 900}.get(timeframe)
        if seconds is None:
            return math.nan
        return value + seconds
    except (OverflowError, OSError, TypeError, ValueError):
        return math.nan


def completed_parent_snapshot_values(
    current_values: Iterable[float],
    parent_tokens: Iterable[object],
    target_close_times: Iterable[float],
    timeframe: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Return current/prior completed values at each causal evaluation time.

    Input values can update while a parent forms. A parent becomes eligible
    only when the base bar's close time reaches that parent's stable availability
    timestamp. This includes a Friday 16:00 close and keeps Friday's completed
    values through Monday premarket.
    """
    current = np.asarray(current_values, dtype=float)
    tokens = np.asarray(parent_tokens)
    target_times = np.asarray(target_close_times, dtype=float)
    if (
        current.ndim != 1
        or tokens.ndim != 1
        or target_times.ndim != 1
        or current.shape != tokens.shape
        or current.shape != target_times.shape
    ):
        raise ValueError("current values, parent tokens, and target times must be matching 1D arrays")
    completed_current = np.full(current.shape, np.nan, dtype=float)
    completed_previous = np.full(current.shape, np.nan, dtype=float)
    have_parent = False
    current_token = None
    current_parent_value = np.nan
    last_completed_value = np.nan
    prior_completed_value = np.nan
    current_parent_available_at = np.nan
    current_parent_completed = False
    for index, (value, token, target_close) in enumerate(zip(current, tokens, target_times)):
        if token is None or not math.isfinite(float(target_close)):
            have_parent = False
            current_token = None
            current_parent_value = np.nan
            last_completed_value = np.nan
            prior_completed_value = np.nan
            current_parent_available_at = np.nan
            current_parent_completed = False
            continue
        if not math.isfinite(float(value)):
            continue
        if not have_parent or token != current_token:
            have_parent = True
            current_token = token
            current_parent_value = float(value)
            current_parent_available_at = _parent_available_at(token, timeframe)
            current_parent_completed = False
        else:
            current_parent_value = float(value)
        if (
            not current_parent_completed
            and math.isfinite(current_parent_available_at)
            and target_close >= current_parent_available_at
        ):
            prior_completed_value = last_completed_value
            last_completed_value = current_parent_value
            current_parent_completed = True
        completed_current[index] = last_completed_value
        completed_previous[index] = prior_completed_value
    return completed_current, completed_previous
