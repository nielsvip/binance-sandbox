"""FILTER_TF gate family — Wave 1 real wiring (2026-09-28).

USER order: every yellow/orange filter gets a dedicated vector path identical to live,
or gets added to live identically (default-neutral). These replace the deleted
hash-proxy `_ALL_FILTER_TF` dispatcher — real per-TF semantics, no proxies.

Per-family ground truth:
- MOM3_FILTER_TF: live base is the REAL MOM3 mean-reversion entry factor
  (ez_manage.py:40976-40990): mom3 = (px - close_3bar_{TF})/close_3bar_{TF}*100,
  long condition mom3 < MOM3_LONG_THRESHOLD (-1.0), short mom3 > MOM3_SHORT_THRESHOLD
  (1.0), fail-open when close_3bar missing (live: `if _close_3 > 0`). Live uses
  TF_FOCUS; the FILTER_TF variant selects the TF and acts as a hard entry gate per
  FILTERS_EXPLAINED ("blocks signal unless"), default OFF = inert. Live twin diff in
  PROPOSAL_FILTER_WAVE1_20260928.md (live's factor is a +22 score vote; the gate form
  is the new sweepable variant, identical both sides, default-neutral).
- MOMENTUM_BREAKOUT_FILTER_TF: live read is stub-only (tradier_manage.py:32561 farm;
  the ":32149 REAL-WIRED" comment is false — `_ = 1` body). Designed from
  FILTERS_EXPLAINED "Momentum breakout confirmation TF": entry requires 3-bar momentum
  CONTINUATION on the chosen TF (long mom3_tf > 0, short < 0). Default OFF.
- FAST_RISER_FILTER_TF: live base is the REAL FAST_RISER_DOUBLE quick-jump augment
  (ez_manage.py:52393-52468): price jump >= 0.2% vs prev TF close, HA color agrees,
  bar low beyond prev low (long: lower low while price jumps = shakeout rise), gain
  gate, -> DOUBLE the position. FILTER_TF selects the detection TF; OFF = inert.
  The position-gain gate (live `current_gain > 0.8`, displayed with :.2% — unit
  ambiguity flagged in the proposal; vec uses 0.8 PERCENT) applies in the loop.
"""
from __future__ import annotations

import numpy as np


def _tf_of(config, name):
    tf = str(getattr(config, name, "OFF") or "OFF").strip()
    return None if tf.upper() == "OFF" else tf


def _mom3_pct(npz, n, tf, close, safe):
    c3 = safe(npz, f"close_3bar_{tf}", n, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        m = np.where(c3 > 0, (close - c3) / np.where(c3 > 0, c3, 1.0) * 100.0, np.nan)
    return m


def mom3_entry_gate(npz, n, is_long, config, close, safe):
    """Bool[n] entry-allow mask or None when OFF. Fail-open where close_3bar missing."""
    tf = _tf_of(config, "MOM3_FILTER_TF")
    if tf is None:
        return None
    m = _mom3_pct(npz, n, tf, close, safe)
    thr_l = float(getattr(config, "MOM3_LONG_THRESHOLD", -1.0))
    thr_s = float(getattr(config, "MOM3_SHORT_THRESHOLD", 1.0))
    cond = (m < thr_l) if is_long else (m > thr_s)
    return np.where(np.isnan(m), True, cond)


def momentum_breakout_gate(npz, n, is_long, config, close, safe):
    """Bool[n] entry-allow mask or None when OFF: momentum continuation on TF."""
    tf = _tf_of(config, "MOMENTUM_BREAKOUT_FILTER_TF")
    if tf is None:
        return None
    m = _mom3_pct(npz, n, tf, close, safe)
    cond = (m > 0.0) if is_long else (m < 0.0)
    return np.where(np.isnan(m), True, cond)


def fast_riser_sig(npz, n, is_long, config, close, safe):
    """Bool[n] quick-jump detection on FAST_RISER_FILTER_TF, or None when OFF.
    Position-state gates (gain > FAST_RISER-style profit floor) apply in the loop."""
    tf = _tf_of(config, "FAST_RISER_FILTER_TF")
    if tf is None:
        return None
    prev = safe(npz, f"close_{tf}_prev", n, 0.0)
    low = safe(npz, f"low_{tf}", n, 0.0)
    low_prev = safe(npz, f"low_{tf}_prev", n, 0.0)
    ha = safe(npz, f"ha_{tf}", n, 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        jump = np.where(prev > 0, (close - prev) / np.where(prev > 0, prev, 1.0), 0.0)
    ok_data = (prev > 0) & (low > 0) & (low_prev > 0)
    if is_long:
        sig = ok_data & (close > prev) & (np.abs(jump) >= 0.002) & (ha > 0) & (low < low_prev)
    else:
        sig = ok_data & (close < prev) & (np.abs(jump) >= 0.002) & (ha < 0) & (low > low_prev)
    return sig


FAST_RISER_MIN_GAIN_PCT = 0.8  # live ez_manage.py:52400 `current_gain > 0.8` (unit ambiguity documented)
