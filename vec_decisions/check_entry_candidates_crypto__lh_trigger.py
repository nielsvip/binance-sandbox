"""ENTRY_LH_TRIGGER (crypto+stocks entry) — fire SHORT on the first lower high after the top
(mirror: LONG on the first higher low after the bottom). Base-TF rolling windows + 1h DC.

USER 2026-10-09 INV-0001 (AGLDUSDT_SHORT autopsy: -15.97% slide from the 0.2292 top, first entry
201 bars late, 6.5% covered). v1 used forward-filled HTF high<high_prev prints — rejected on the
Mac (fired 71% of bars: an incomplete 1h bar is trivially below the prior completed one). v2 uses
only base-TF highs/lows + the 1h DC band: stateless, venue-agnostic, no HTF-alignment semantics,
identical inputs live (klines cache) and vec (NPZ).

SHORT (all strict, all >0-guarded):
  top    = max(high[i-L+1..i])            (L = LOOKBACK_BARS, default 96 = 24h)
  age    = i - first_index_of(top)        (0 = top is NOW)
  peak   = high[i] == max(high[i-7..i])   (local 2h peak AT bar i; known at close, no lookahead)
  FIRE   = (age >= 4) & peak & (high[i] < top) & (high[i] < dc_high_1h[i]*(1-dc_th))
LONG mirror on lows (trough, bot, dc_low_1h*(1+dc_th)).
The double-top retest does NOT fire (high<top strict). Default OFF = fully inert.

LIVE SOURCE (replica spec, NOT YET APPLIED — needs unlock):
  crypto: ez_positions_quick.py entry worker between the DC gate (~15968) and the VOL worker
          (~16394) — LAST-writer-wins, so vec FIRST-wins places this block after VOL_SPIKE_REVERSAL.
  stocks: tradier_manage.py should_enter_short (~12296) / should_enter_long (~12019).
  backtest_v12_engine needs NO separate code: it calls the live entry functions on frozen NPZ
  (guarded by _assert_live_path), so it inherits the replica verbatim.
This module is the SHARED predicate (scalar + vec twins, same comparisons); live imports it once
unlocked — parity by construction, never by parallel reimplementation.
"""
import numpy as np

PEAK_WINDOW = 8
TOP_MIN_AGE = 4


def _lh_trigger_params(config):
    return (
        int(getattr(config, "ENTRY_LH_TRIGGER_LOOKBACK_BARS", 96)),
        float(getattr(config, "ENTRY_LH_TRIGGER_DC_THRESHOLD_PCT", 0.5)) / 100.0,
    )


def _lh_trigger_fire(high, low, dc_high_1h, dc_low_1h, is_long, lookback, dc_th):
    """PURE per-bar trigger over trailing windows (lists, oldest->newest, len>=lookback).
    high/low/dc_* are trailing base-TF series ENDING at bar i. Returns bool. Shared by scalar+vec."""
    if len(high) < lookback or len(low) < lookback:
        return False
    h = high[-lookback:]
    lo = low[-lookback:]
    if is_long:
        bot = min(lo)
        age = lookback - 1 - lo.index(bot)
        peak = low[-1] == min(low[-PEAK_WINDOW:])
        return (
            age >= TOP_MIN_AGE
            and peak
            and low[-1] > 0
            and bot > 0
            and low[-1] > bot
            and dc_low_1h > 0
            and low[-1] > dc_low_1h * (1.0 + dc_th)
        )
    age = lookback - 1 - h.index(max(h))
    peak = high[-1] == max(high[-PEAK_WINDOW:])
    return (
        age >= TOP_MIN_AGE
        and peak
        and high[-1] > 0
        and max(h) > 0
        and high[-1] < max(h)
        and dc_high_1h > 0
        and high[-1] < dc_high_1h * (1.0 - dc_th)
    )


def check_lh_trigger_entry(config, indicators: dict, is_long: bool):
    """LIVE/scalar path. indicators carries trailing 'high'/'low' lists + 'dc_high_1h'/'dc_low_1h'.
    Returns (fire, reason). Static reason strings (no float suffixes)."""
    if not bool(getattr(config, "ENTRY_LH_TRIGGER_ENABLED", False)):
        return False, ""
    lookback, dc_th = _lh_trigger_params(config)
    high = [float(x or 0) for x in (indicators.get("high") or [])]
    low = [float(x or 0) for x in (indicators.get("low") or [])]
    dch = float(indicators.get("dc_high_1h", 0) or 0)
    dcl = float(indicators.get("dc_low_1h", 0) or 0)
    if _lh_trigger_fire(high, low, dch, dcl, is_long, lookback, dc_th):
        return True, f"LH_TRIGGER_ENTRY_{'LONG' if is_long else 'SHORT'}"
    return False, ""


def _roll_max(a, window):
    pad = np.concatenate([np.full(window - 1, -np.inf), np.asarray(a, dtype=float)])
    return np.lib.stride_tricks.sliding_window_view(pad, window).max(axis=1)


def _roll_min(a, window):
    pad = np.concatenate([np.full(window - 1, np.inf), np.asarray(a, dtype=float)])
    return np.lib.stride_tricks.sliding_window_view(pad, window).min(axis=1)


def _roll_argmax_age(a, window):
    """Bars since the FIRST occurrence of the trailing-window max (0 = max is NOW)."""
    pad = np.concatenate([np.full(window - 1, -np.inf), np.asarray(a, dtype=float)])
    win = np.lib.stride_tricks.sliding_window_view(pad, window)
    return (window - 1) - win.argmax(axis=1)


def _roll_argmin_age(a, window):
    pad = np.concatenate([np.full(window - 1, np.inf), np.asarray(a, dtype=float)])
    win = np.lib.stride_tricks.sliding_window_view(pad, window)
    return (window - 1) - win.argmin(axis=1)


def check_lh_trigger_entry_vec(config, high, low, dc_high_1h, dc_low_1h, is_long):
    """VECTORIZED fire mask. SAME comparisons as scalar (argmax/argmin = first occurrence, like
    list.index(max())). Early bars (i < lookback-1) never fire: pad infinities poison the peak
    equality (max of window incl +inf != high) and the >0 guards. Returns bool ndarray."""
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    dch = np.asarray(dc_high_1h, dtype=float)
    dcl = np.asarray(dc_low_1h, dtype=float)
    lookback, dc_th = _lh_trigger_params(config)
    lookback = max(PEAK_WINDOW, int(lookback))
    warm = np.zeros_like(high, dtype=bool)
    warm[lookback - 1 :] = True  # scalar returns False for short lists — mirror exactly
    if is_long:
        bot = _roll_min(low, lookback)
        age = _roll_argmin_age(low, lookback)
        peak = low == _roll_min(low, PEAK_WINDOW)
        return (
            warm
            & (age >= TOP_MIN_AGE)
            & peak
            & (low > 0)
            & (bot > 0)
            & (low > bot)
            & (dcl > 0)
            & (low > dcl * (1.0 + dc_th))
        )
    top = _roll_max(high, lookback)
    age = _roll_argmax_age(high, lookback)
    peak = high == _roll_max(high, PEAK_WINDOW)
    return (
        warm
        & (age >= TOP_MIN_AGE)
        & peak
        & (high > 0)
        & (top > 0)
        & (high < top)
        & (dch > 0)
        & (high < dch * (1.0 - dc_th))
    )
