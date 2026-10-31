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


def completed_parent_snapshot_values(
    current_values: Iterable[float], parent_tokens: Iterable[object]
) -> tuple[np.ndarray, np.ndarray]:
    """Return the latest and prior completed-parent values at each forming row.

    The output is constant while a parent token repeats. At a new token, the
    prior parent's last observed value becomes the completed current value.
    Both outputs stay NaN until their source parents have been observed.
    """
    current = np.asarray(current_values, dtype=float)
    tokens = np.asarray(parent_tokens)
    if current.ndim != 1 or tokens.ndim != 1 or current.shape != tokens.shape:
        raise ValueError("current values and parent tokens must be matching 1D arrays")
    completed_current = np.full(current.shape, np.nan, dtype=float)
    completed_previous = np.full(current.shape, np.nan, dtype=float)
    have_parent = False
    current_token = None
    current_parent_value = np.nan
    last_completed_value = np.nan
    prior_completed_value = np.nan
    for index, (value, token) in enumerate(zip(current, tokens)):
        if token is None or (isinstance(token, (float, np.floating)) and not math.isfinite(float(token))):
            have_parent = False
            current_token = None
            current_parent_value = np.nan
            last_completed_value = np.nan
            prior_completed_value = np.nan
            continue
        if not math.isfinite(float(value)):
            continue
        if not have_parent:
            have_parent = True
            current_token = token
            current_parent_value = float(value)
            continue
        if token != current_token:
            prior_completed_value = last_completed_value
            last_completed_value = current_parent_value
            current_token = token
            current_parent_value = float(value)
            completed_current[index] = last_completed_value
            completed_previous[index] = prior_completed_value
            continue
        completed_current[index] = last_completed_value
        completed_previous[index] = prior_completed_value
        current_parent_value = float(value)
    return completed_current, completed_previous
