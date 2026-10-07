"""vec_paths/ttm_squeeze.py — TTM (LazyBear) Squeeze vectorization.

LIVE LOGIC MIRRORED FROM:
  ez_indicators.py:bb_features  (lines 1242-1253)
  ez_indicators.py:kc_features  (lines 1256-1271)
  ez_indicators.py:squeeze_features (lines 1274-1296)

WHY THIS EXISTS:
  squeeze_features is the entry signal for BB_SQUEEZE_ENTRY_ENABLED (one of
  the 9 dead-switches wired 2026-04-30). When evaluating that switch via
  Tier-1 sweeps, the per-bar call to squeeze_features triggers FOUR pandas
  rolling-window operations:
    - bb_features on current series
    - kc_features on current series (EMA + ATR)
    - bb_features on prev series (series.iloc[:-1])
    - kc_features on prev series

  Per-bar cost ~4× pandas rolling-mean/std/ewm/atr. Across 50 symbols ×
  100k bars × 6 TFs that's ~120M scalar calls — multi-minute per sweep.

  Vectorizing collapses to one rolling sum / one EMA per series, with
  cumulative-trick rolling std. ~50-200× speedup.

INPUTS (vec):
  close, high, low — shape (N,) float64 arrays
  length, bb_mult, kc_mult — TTM-Squeeze parameters (live defaults: 20, 2.0, 1.5)

OUTPUTS (vec):
  is_squeezed : shape (N,) bool — True when BB inside KC for that bar
  fire_dir    : shape (N,) int8 — +1 bull release, -1 bear release, 0 no release
                  (release = squeezed[t-1] AND NOT squeezed[t]; direction from
                   close[t] vs KC midline[t])

PARITY:
  scalar _core(close, high, low) returns (is_squeezed_now, fire_dir) for the
  LAST bar — bit-equivalent to ez_indicators.squeeze_features called on
  series of len N.
  _vec(close, high, low) returns per-bar arrays. Parity test checks
  vec[N-1] == core(series).
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np


# ════════════════════════════════════════════════════════════════════════════
# Scalar oracle — mirrors ez_indicators.bb_features / kc_features / squeeze_features
# Returns Python tuples to match live module exactly.
# ════════════════════════════════════════════════════════════════════════════

def _bb_features_scalar(close: np.ndarray, length: int, std_mult: float
                        ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Returns (upper, lower, pct_b) for the last bar; None tuple when too short."""
    n = len(close)
    if n < length:
        return None, None, None
    window = close[-length:]
    mid = float(np.mean(window))
    std = float(np.std(window, ddof=0))
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    price = float(close[-1])
    band_width = upper - lower
    pct_b = (price - lower) / band_width if band_width > 0 else 0.5
    return upper, lower, max(0.0, min(1.0, pct_b))


def _atr_scalar(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                length: int) -> Optional[float]:
    """ATR last value — bit-mirror of ez_indicators.atr_series:
       true_range[i] = max(|high-low|, |high-prev_close|, |low-prev_close|)
                       — with prev_close[0] = NaN; pandas max(axis=1, skipna=True)
                       returns just |high-low| at i=0.
       atr = TR.ewm(alpha=1/length, adjust=False, min_periods=length).mean()
    """
    n = len(close)
    if n < length + 1:
        return None
    high_f = np.asarray(high, dtype=np.float64)
    low_f = np.asarray(low, dtype=np.float64)
    close_f = np.asarray(close, dtype=np.float64)
    # TR with i=0 == |high-low| (pandas treats NaN prev_close as missing in max)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = abs(high_f[0] - low_f[0])
    for i in range(1, n):
        a = abs(high_f[i] - low_f[i])
        b = abs(high_f[i] - close_f[i - 1])
        c = abs(low_f[i] - close_f[i - 1])
        tr[i] = max(a, b, c)
    # ewm(alpha=1/length, adjust=False) with min_periods=length:
    #   recursion: ewm[i] = alpha*tr[i] + (1-alpha)*ewm[i-1]; seed ewm[0]=tr[0].
    #   But pandas with min_periods=length yields NaN for i < length-1.
    alpha = 1.0 / float(length)
    ewm = np.empty(n, dtype=np.float64)
    ewm[0] = tr[0]
    for i in range(1, n):
        ewm[i] = alpha * tr[i] + (1 - alpha) * ewm[i - 1]
    if n - 1 < length - 1:
        return None
    return float(ewm[-1])


def _kc_features_scalar(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                        length: int, atr_mult: float
                        ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Returns (upper, mid, lower) for last bar; (None,None,None) when too short."""
    n = len(close)
    if n < length:
        return None, None, None
    # EMA mid (pandas ewm span=length, adjust=False)
    alpha = 2.0 / (length + 1)
    ema = close[0]
    for i in range(1, n):
        ema = alpha * close[i] + (1 - alpha) * ema
    mid = float(ema)
    atr = _atr_scalar(high, low, close, length)
    if atr is None or not (atr > 0):
        return None, None, None
    upper = mid + atr_mult * atr
    lower = mid - atr_mult * atr
    return upper, mid, lower


def squeeze_features_core(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                          length: int = 20, bb_mult: float = 2.0, kc_mult: float = 1.5
                          ) -> Tuple[Optional[bool], Optional[int]]:
    """Bit-exact mirror of ez_indicators.squeeze_features for the LAST bar."""
    n = len(close)
    if close is None or n < length + 1:
        return None, None
    bb_u, bb_l, _ = _bb_features_scalar(close, length, bb_mult)
    kc_u, kc_m, kc_l = _kc_features_scalar(close, high, low, length, kc_mult)
    if any(v is None for v in (bb_u, bb_l, kc_u, kc_m, kc_l)):
        return None, None
    is_squeezed_now = bool(bb_u <= kc_u and bb_l >= kc_l)
    # Prior bar squeeze state (need length+1 bars for prior eval)
    prev_close = close[:-1]
    prev_high = high[:-1] if high is not None else None
    prev_low = low[:-1] if low is not None else None
    bb_u_p, bb_l_p, _ = _bb_features_scalar(prev_close, length, bb_mult)
    if prev_high is not None and prev_low is not None:
        kc_u_p, kc_m_p, kc_l_p = _kc_features_scalar(prev_close, prev_high, prev_low,
                                                     length, kc_mult)
    else:
        kc_u_p = kc_m_p = kc_l_p = None
    was_squeezed = bool(bb_u_p is not None and bb_l_p is not None and
                        kc_u_p is not None and kc_l_p is not None and
                        bb_u_p <= kc_u_p and bb_l_p >= kc_l_p)
    fire_dir = 0
    if was_squeezed and not is_squeezed_now:
        last_close = float(close[-1])
        fire_dir = 1 if last_close > kc_m else (-1 if last_close < kc_m else 0)
    return is_squeezed_now, int(fire_dir)


# ════════════════════════════════════════════════════════════════════════════
# Vectorized — full per-bar arrays in one numpy pass
# ════════════════════════════════════════════════════════════════════════════

def _rolling_mean_inclusive(arr: np.ndarray, length: int) -> np.ndarray:
    """Rolling mean over the LAST `length` bars (inclusive). For i < length-1, NaN."""
    n = len(arr)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < length:
        return out
    cs = np.concatenate([[0.0], np.cumsum(arr, dtype=np.float64)])
    for i in range(length - 1, n):
        out[i] = (cs[i + 1] - cs[i + 1 - length]) / length
    return out


def _rolling_std_inclusive(arr: np.ndarray, length: int) -> np.ndarray:
    """Rolling std (ddof=0) over the LAST `length` bars. NaN for i < length-1.
    Uses E[x^2] - (E[x])^2 with clamping.
    """
    n = len(arr)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < length:
        return out
    cs = np.concatenate([[0.0], np.cumsum(arr, dtype=np.float64)])
    cs2 = np.concatenate([[0.0], np.cumsum(arr.astype(np.float64) ** 2)])
    for i in range(length - 1, n):
        s = cs[i + 1] - cs[i + 1 - length]
        s2 = cs2[i + 1] - cs2[i + 1 - length]
        mean = s / length
        var = max(0.0, s2 / length - mean * mean)
        out[i] = np.sqrt(var)
    return out


def _ewm_vec(arr: np.ndarray, length: int) -> np.ndarray:
    """ewm(span=length, adjust=False) — equivalent to recursive
    ema[i] = alpha*x[i] + (1-alpha)*ema[i-1] with ema[0] = x[0]."""
    n = len(arr)
    out = np.zeros(n, dtype=np.float64)
    if n == 0:
        return out
    alpha = 2.0 / (length + 1)
    out[0] = arr[0]
    for i in range(1, n):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _wilder_atr_vec(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                    length: int) -> np.ndarray:
    """Per-bar ATR using ewm(alpha=1/length, adjust=False) — mirrors live atr_series.
    NaN for i < length-1.
    """
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < length:
        return out
    # TR with i=0 == |high-low| (NaN-prev_close fallback)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = abs(high[0] - low[0])
    if n > 1:
        prev_close = close[:-1]
        a = np.abs(high[1:] - low[1:])
        b = np.abs(high[1:] - prev_close)
        c = np.abs(low[1:] - prev_close)
        tr[1:] = np.maximum(a, np.maximum(b, c))
    alpha = 1.0 / float(length)
    ewm = np.empty(n, dtype=np.float64)
    ewm[0] = tr[0]
    for i in range(1, n):
        ewm[i] = alpha * tr[i] + (1 - alpha) * ewm[i - 1]
    out[length - 1:] = ewm[length - 1:]
    return out


def squeeze_features_vec(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                         length: int = 20, bb_mult: float = 2.0, kc_mult: float = 1.5
                         ) -> Tuple[np.ndarray, np.ndarray]:
    """Per-bar (is_squeezed, fire_dir).

    Returns:
      is_squeezed: shape (N,) bool. False (default) when bands undefined.
      fire_dir:    shape (N,) int8 with values in {-1, 0, +1}.
    """
    close = np.asarray(close, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    n = len(close)

    is_squeezed = np.zeros(n, dtype=bool)
    fire_dir = np.zeros(n, dtype=np.int8)
    if n < length + 1:
        return is_squeezed, fire_dir

    # BB upper/lower per bar
    mid = _rolling_mean_inclusive(close, length)
    std = _rolling_std_inclusive(close, length)
    bb_u = mid + bb_mult * std
    bb_l = mid - bb_mult * std

    # KC: EMA mid + ATR (Wilder)
    ema_mid = _ewm_vec(close, length)
    atr = _wilder_atr_vec(high, low, close, length)
    kc_valid = ~np.isnan(atr) & (atr > 0)
    kc_u = np.where(kc_valid, ema_mid + kc_mult * atr, np.nan)
    kc_l = np.where(kc_valid, ema_mid - kc_mult * atr, np.nan)
    kc_m = np.where(kc_valid, ema_mid, np.nan)

    valid = ~np.isnan(bb_u) & ~np.isnan(bb_l) & kc_valid
    is_squeezed = valid & (bb_u <= kc_u) & (bb_l >= kc_l)

    # Fire dir: was_squeezed[t-1] AND NOT is_squeezed[t]
    was_squeezed = np.zeros(n, dtype=bool)
    was_squeezed[1:] = is_squeezed[:-1]
    # First bar can't have "was squeezed"
    release_mask = was_squeezed & ~is_squeezed & valid
    if np.any(release_mask):
        gt = close > kc_m
        lt = close < kc_m
        fd = np.where(gt, 1, np.where(lt, -1, 0)).astype(np.int8)
        fire_dir = np.where(release_mask, fd, 0).astype(np.int8)

    return is_squeezed, fire_dir
