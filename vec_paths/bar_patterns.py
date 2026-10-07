"""vec_paths/bar_patterns.py — Vectorized candlestick + bar-structure detector.

LIVE LOGIC MIRRORED FROM:
  ez_indicators.py:detect_bar_patterns (lines 1805-2009)

WHY THIS EXISTS:
  detect_bar_patterns is called once per timeframe per symbol per bar during
  NPZ precompute AND inside the live indicators loop (~5-6 TFs). The scalar
  version walks the last 5 bars via pandas iloc, computes 20 pattern checks
  in priority order, runs streak + structure + compression + inside-bar +
  ATR percentile + volume regime, then writes 14 output fields.

  Per-bar cost is dominated by:
    - 5× iloc-indexed tuple extraction (_b(-1).._b(-5))  → slow
    - 20 sequential pattern conditionals                  → branchy
    - rolling vol_avg, ATR rank, streak loops             → Python loops

  Total ~200 LOC of Python per bar. Vectorizing across N bars at once
  turns this into a one-shot numpy pass — speedup ~50-200× for
  million-bar precompute / sweep runs.

VECTORIZATION STRATEGY:
  - Inputs are full OHLCV arrays (shape (N,) each).
  - Compute body, range, wick arrays once.
  - Volume avg = pandas-style rolling-20 (numpy convolve).
  - ATR rank = streaming rolling-50 rank via vectorized sort comparison.
  - Streak: vectorized via cumulative-reset trick.
  - Patterns: 20 mutually-exclusive boolean masks evaluated in priority
    order; first-match wins (mirrors live's elif chain).
  - Output dict has same keys as scalar (with `_<timeframe>` suffix), but
    values are shape (N,) arrays instead of scalars.

OUTPUT SCHEMA (vec):
  bar_pattern_<tf>          object array (string per bar)
  bar_direction_<tf>        int8 (-1/0/+1)
  bar_strength_<tf>         float32 (clipped 0..1)
  bar_vol_confirm_<tf>      bool
  bar_vol_ratio_<tf>        float32
  bar_body_ratio_<tf>       float32
  bar_upper_wick_<tf>       float32 (normalized)
  bar_lower_wick_<tf>       float32 (normalized)
  bar_streak_<tf>           int8
  bar_swing_bull_<tf>       bool
  bar_swing_bear_<tf>       bool
  bar_compression_<tf>      bool
  bar_compression_ratio_<tf> float32
  bar_inside_count_<tf>     int8
  bar_vol_spike_<tf>        bool
  bar_vol_expanding_<tf>    bool
  bar_vol_regime_<tf>       object array ("low"/"normal"/"high")
  bar_atr_rank_<tf>         float32

PARITY:
  scalar _core() returns dict for the LAST bar (matches live which uses
  iloc[-N] indexing).
  _vec() returns the full per-bar dict-of-arrays.
  Parity test verifies _vec[N-1] == _core(window ending at N-1) on 1000
  random OHLCV bars across 5 timeframes.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np

# Output keys (without _<tf> suffix) — useful for callers that want to enumerate
PATTERN_OUTPUT_KEYS = [
    "bar_pattern", "bar_direction", "bar_strength",
    "bar_vol_confirm", "bar_vol_ratio", "bar_body_ratio",
    "bar_upper_wick", "bar_lower_wick",
    "bar_streak", "bar_swing_bull", "bar_swing_bear",
    "bar_compression", "bar_compression_ratio",
    "bar_inside_count", "bar_vol_spike", "bar_vol_expanding",
    "bar_vol_regime", "bar_atr_rank",
]

# Pattern name codes — int8 → string, mirrors live's pattern string values.
# Ordering matches priority in detect_bar_patterns_core/_vec.
PATTERN_CODE_NONE = 0
PATTERN_NAMES = [
    "none",                  # 0
    "morning_star",          # 1
    "evening_star",          # 2
    "three_white_soldiers",  # 3
    "three_black_crows",     # 4
    "bull_engulfing",        # 5
    "bear_engulfing",        # 6
    "tweezer_bottom",        # 7
    "tweezer_top",           # 8
    "hammer",                # 9
    "shooting_star",         # 10
    "bull_harami",           # 11
    "bear_harami",           # 12
    "multi_inside",          # 13
    "inside_bar",            # 14
    "outside_bar",           # 15
    "pin_bar_bull",          # 16
    "pin_bar_bear",          # 17
    "three_bar_bull",        # 18
    "three_bar_bear",        # 19
    "doji",                  # 20
]
_PATTERN_NAME_ARR = np.array(PATTERN_NAMES, dtype=object)


# ════════════════════════════════════════════════════════════════════════════
# Scalar oracle — exact mirror of ez_indicators.detect_bar_patterns
# (kept self-contained so test can run without ez_indicators on path)
# ════════════════════════════════════════════════════════════════════════════

def detect_bar_patterns_core(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray,
    timeframe: str,
) -> Dict[str, Any]:
    """Exact scalar mirror of ez_indicators.detect_bar_patterns for the LAST bar.

    Args:
        o, h, l, c, v: shape (N,) arrays of open/high/low/close/volume.
                       Must have len >= 10 (matches live early-return guard).
        timeframe:     suffix appended to output keys (e.g. '15m').

    Returns:
        Dict with same keys/types as the live scalar function. Empty dict
        when N < 10 (matches live).
    """
    result: Dict[str, Any] = {}
    n = len(o)
    if n < 10:
        return result

    # Mirrors `_b(i)` which uses pd iloc[i] (positional). For arrays this is
    # arr[-i] when i is negative. iloc[-1] → arr[-1], iloc[-2] → arr[-2], etc.
    o1, h1, l1, c1, v1 = float(o[-1]), float(h[-1]), float(l[-1]), float(c[-1]), float(v[-1])
    o2, h2, l2, c2, v2 = float(o[-2]), float(h[-2]), float(l[-2]), float(c[-2]), float(v[-2])
    o3, h3, l3, c3, v3 = float(o[-3]), float(h[-3]), float(l[-3]), float(c[-3]), float(v[-3])
    o4, h4, l4, c4, v4 = float(o[-4]), float(h[-4]), float(l[-4]), float(c[-4]), float(v[-4])
    o5, h5, l5, c5, v5 = float(o[-5]), float(h[-5]), float(l[-5]), float(c[-5]), float(v[-5])

    body1, body2, body3, body4 = abs(c1 - o1), abs(c2 - o2), abs(c3 - o3), abs(c4 - o4)
    range1 = max(h1 - l1, 1e-10)
    range2 = max(h2 - l2, 1e-10)
    range3 = max(h3 - l3, 1e-10)
    upper_wick1 = h1 - max(o1, c1)
    lower_wick1 = min(o1, c1) - l1
    body_ratio1 = body1 / range1
    is_bull1, is_bear1 = c1 > o1, c1 < o1
    is_bull2, is_bear2 = c2 > o2, c2 < o2
    is_bull3, is_bear3 = c3 > o3, c3 < o3

    vol_window = min(20, n - 1)
    vol_avg = float(np.mean(v[-vol_window - 1:-1])) if vol_window > 0 else v1
    vol_ratio = v1 / vol_avg if vol_avg > 0 else 1.0
    vol_confirm = vol_ratio >= 1.3
    vol_spike = vol_ratio >= 2.0
    vol_dry = vol_ratio < 0.6
    vol_expanding = all(float(v[-i]) > float(v[-i - 1]) for i in range(1, min(4, n)))

    atr_arr = h - l
    atr_curr = float(atr_arr[-1])
    atr_window = min(50, n)
    atr_hist = np.sort(atr_arr[-atr_window:])
    atr_rank = float((atr_hist < atr_curr).sum()) / max(len(atr_hist), 1)
    vol_regime = "low" if atr_rank < 0.25 else ("high" if atr_rank > 0.75 else "normal")

    streak = 0
    for i in range(1, min(8, n)):
        ci_v = float(c[-i])
        oi_v = float(o[-i])
        if ci_v > oi_v:
            if streak >= 0:
                streak += 1
            else:
                break
        elif ci_v < oi_v:
            if streak <= 0:
                streak -= 1
            else:
                break
        else:
            break

    hh = h1 > h2 and h2 > h3
    hl = l1 > l2 and l2 > l3
    ll = l1 < l2 and l2 < l3
    lh = h1 < h2 and h2 < h3
    swing_bull = hh and hl
    swing_bear = ll and lh

    ranges_5 = [max(float(h[-i]) - float(l[-i]), 1e-10) for i in range(1, min(6, n))]
    if len(ranges_5) >= 3:
        compression = all(ranges_5[i] <= ranges_5[i + 1] for i in range(len(ranges_5) - 1))
        compression_ratio = ranges_5[0] / ranges_5[-1] if ranges_5[-1] > 0 else 1.0
    else:
        compression = False
        compression_ratio = 1.0

    inside_count = 0
    for i in range(1, min(5, n - 1)):
        if float(h[-i]) < float(h[-i - 1]) and float(l[-i]) > float(l[-i - 1]):
            inside_count += 1
        else:
            break

    pattern = "none"
    direction = 0
    strength = 0.0
    if is_bear3 and body3 > range3 * 0.5 and body2 < range2 * 0.3 and is_bull1 and body1 > range1 * 0.5 and c1 > (o3 + c3) / 2:
        pattern, direction = "morning_star", 1
        strength = min(1.0, (body1 + body3) / (2 * range1 + 1e-10))
    elif is_bull3 and body3 > range3 * 0.5 and body2 < range2 * 0.3 and is_bear1 and body1 > range1 * 0.5 and c1 < (o3 + c3) / 2:
        pattern, direction = "evening_star", -1
        strength = min(1.0, (body1 + body3) / (2 * range1 + 1e-10))
    elif is_bull1 and is_bull2 and is_bull3 and c1 > c2 > c3 and body1 > range1 * 0.5 and body2 > range2 * 0.5 and body3 > range3 * 0.5:
        pattern, direction = "three_white_soldiers", 1
        strength = min(1.0, min(body1, body2, body3) / max(range1, range2, range3))
    elif is_bear1 and is_bear2 and is_bear3 and c1 < c2 < c3 and body1 > range1 * 0.5 and body2 > range2 * 0.5 and body3 > range3 * 0.5:
        pattern, direction = "three_black_crows", -1
        strength = min(1.0, min(body1, body2, body3) / max(range1, range2, range3))
    elif is_bull1 and is_bear2 and c1 > o2 and o1 < c2 and body1 > body2:
        pattern, direction = "bull_engulfing", 1
        strength = min(1.0, (body1 / (body2 + 1e-10)) * 0.5)
    elif is_bear1 and is_bull2 and c1 < o2 and o1 > c2 and body1 > body2:
        pattern, direction = "bear_engulfing", -1
        strength = min(1.0, (body1 / (body2 + 1e-10)) * 0.5)
    elif is_bull1 and abs(l1 - l2) < range1 * 0.05 and l1 < min(l3, l4):
        pattern, direction = "tweezer_bottom", 1
        strength = min(1.0, 1.0 - abs(l1 - l2) / range1)
    elif is_bear1 and abs(h1 - h2) < range1 * 0.05 and h1 > max(h3, h4):
        pattern, direction = "tweezer_top", -1
        strength = min(1.0, 1.0 - abs(h1 - h2) / range1)
    elif body_ratio1 < 0.35 and lower_wick1 > body1 * 2.0 and upper_wick1 < body1 * 0.5:
        pattern, direction = "hammer", 1
        strength = min(1.0, lower_wick1 / range1)
    elif body_ratio1 < 0.35 and upper_wick1 > body1 * 2.0 and lower_wick1 < body1 * 0.5:
        pattern, direction = "shooting_star", -1
        strength = min(1.0, upper_wick1 / range1)
    elif is_bull1 and is_bear2 and body1 < body2 * 0.5 and h1 < h2 and l1 > l2:
        pattern, direction = "bull_harami", 1
        strength = 0.5 * (1.0 - body1 / (body2 + 1e-10))
    elif is_bear1 and is_bull2 and body1 < body2 * 0.5 and h1 < h2 and l1 > l2:
        pattern, direction = "bear_harami", -1
        strength = 0.5 * (1.0 - body1 / (body2 + 1e-10))
    elif inside_count >= 2:
        pattern, direction = "multi_inside", 0
        strength = min(1.0, inside_count * 0.3)
    elif h1 < h2 and l1 > l2:
        pattern, direction = "inside_bar", 0
        strength = 1.0 - (range1 / range2)
    elif h1 > h2 and l1 < l2 and body_ratio1 > 0.6:
        pattern = "outside_bar"
        direction = 1 if is_bull1 else -1
        strength = body_ratio1
    elif lower_wick1 > range1 * 0.6 and body_ratio1 < 0.25:
        pattern, direction = "pin_bar_bull", 1
        strength = lower_wick1 / range1
    elif upper_wick1 > range1 * 0.6 and body_ratio1 < 0.25:
        pattern, direction = "pin_bar_bear", -1
        strength = upper_wick1 / range1
    elif is_bull1 and is_bear2 and is_bear3 and c1 > h2:
        pattern, direction = "three_bar_bull", 1
        strength = min(1.0, body1 / (body2 + body3 + 1e-10))
    elif is_bear1 and is_bull2 and is_bull3 and c1 < l2:
        pattern, direction = "three_bar_bear", -1
        strength = min(1.0, body1 / (body2 + body3 + 1e-10))
    elif body_ratio1 < 0.1:
        pattern, direction = "doji", 0
        strength = 0.3 + (0.4 if vol_confirm else 0.0)

    if vol_confirm and direction != 0:
        strength = min(1.0, strength * 1.3)
    if vol_spike and direction != 0:
        strength = min(1.0, strength * 1.2)
    if vol_dry and direction != 0:
        strength *= 0.6

    result[f"bar_pattern_{timeframe}"] = pattern
    result[f"bar_direction_{timeframe}"] = direction
    result[f"bar_strength_{timeframe}"] = round(strength, 3)
    result[f"bar_vol_confirm_{timeframe}"] = vol_confirm
    result[f"bar_vol_ratio_{timeframe}"] = round(vol_ratio, 2)
    result[f"bar_body_ratio_{timeframe}"] = round(body_ratio1, 3)
    result[f"bar_upper_wick_{timeframe}"] = round(upper_wick1 / range1, 3) if range1 > 0 else 0.0
    result[f"bar_lower_wick_{timeframe}"] = round(lower_wick1 / range1, 3) if range1 > 0 else 0.0
    result[f"bar_streak_{timeframe}"] = streak
    result[f"bar_swing_bull_{timeframe}"] = swing_bull
    result[f"bar_swing_bear_{timeframe}"] = swing_bear
    result[f"bar_compression_{timeframe}"] = compression
    result[f"bar_compression_ratio_{timeframe}"] = round(compression_ratio, 3)
    result[f"bar_inside_count_{timeframe}"] = inside_count
    result[f"bar_vol_spike_{timeframe}"] = vol_spike
    result[f"bar_vol_expanding_{timeframe}"] = vol_expanding
    result[f"bar_vol_regime_{timeframe}"] = vol_regime
    result[f"bar_atr_rank_{timeframe}"] = round(atr_rank, 3)
    return result


# ════════════════════════════════════════════════════════════════════════════
# Helpers — vectorized rolling functions
# ════════════════════════════════════════════════════════════════════════════

def _rolling_mean_prev(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling mean over the PREVIOUS `window` bars (exclusive of current).

    Live uses `v.iloc[-vol_window-1:-1].mean()` which is the 20 bars
    BEFORE the last bar. For bar i, vec equivalent is mean(arr[i-window:i]).
    For i < window, returns the mean over whatever prior bars exist.
    Returns shape (N,) float64.
    """
    n = len(arr)
    out = np.zeros(n, dtype=np.float64)
    if n == 0:
        return out
    # cumulative sum trick — out[i] = sum(arr[max(0,i-window):i]) / count
    cs = np.concatenate([[0.0], np.cumsum(arr, dtype=np.float64)])
    for i in range(n):
        lo = max(0, i - window)
        hi = i
        cnt = hi - lo
        if cnt > 0:
            out[i] = (cs[hi] - cs[lo]) / cnt
        else:
            out[i] = float(arr[i]) if n > 0 else 0.0
    return out


def _rolling_rank_inclusive(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling rank ratio: fraction of last `window` bars (inclusive of current)
    with value < current. For bar i: (#{j ∈ [i-window+1, i] : arr[j] < arr[i]}) / window_len.
    Mirrors live's `(atr_hist < atr_curr).sum() / max(len(atr_hist), 1)` where
    atr_hist = atr_arr.iloc[-atr_window:] (inclusive of current bar).
    """
    n = len(arr)
    out = np.zeros(n, dtype=np.float64)
    for i in range(n):
        lo = max(0, i - window + 1)
        hi = i + 1
        win = arr[lo:hi]
        wlen = len(win)
        if wlen == 0:
            out[i] = 0.0
        else:
            out[i] = float((win < arr[i]).sum()) / wlen
    return out


def _streak_vec(o_arr: np.ndarray, c_arr: np.ndarray) -> np.ndarray:
    """Per-bar directional-close streak (live: loops back from current bar).

    Returns shape (N,) int (-7..+7 ish), zero where bar is doji.
    Mirrors live loop: starts at i=1, walks back, breaks on flip or doji.
    """
    n = len(o_arr)
    direction = np.where(c_arr > o_arr, 1, np.where(c_arr < o_arr, -1, 0)).astype(np.int8)
    out = np.zeros(n, dtype=np.int32)
    for i in range(n):
        d_curr = direction[i]
        if d_curr == 0:
            out[i] = 0
            continue
        s = 0
        # mirror: for k in range(1, min(8, n)): look at o.iloc[-k], c.iloc[-k] starting at the
        # CURRENT bar (k=1 → bar i). The live loop counts up to 7 bars back including current.
        # We mirror exactly: start at the current bar (k=1 → idx i) and walk to idx i-(k-1).
        for k in range(1, min(8, i + 2)):
            j = i - (k - 1)
            if j < 0:
                break
            cj = c_arr[j]
            oj = o_arr[j]
            if cj > oj:
                if s >= 0:
                    s += 1
                else:
                    break
            elif cj < oj:
                if s <= 0:
                    s -= 1
                else:
                    break
            else:
                break
        out[i] = s
    return out


def _inside_count_vec(h_arr: np.ndarray, l_arr: np.ndarray) -> np.ndarray:
    """Count of consecutive inside bars ending at each index (mirrors live loop).

    Live loop: for i in range(1, min(5, n - 1)): if bar[-i] inside bar[-i-1] count+=1 else break.
    For bar idx t: walk k = 1..4, check (h[t-(k-1)] < h[t-k]) AND (l[t-(k-1)] > l[t-k]).
    Break on first failure.
    """
    n = len(h_arr)
    out = np.zeros(n, dtype=np.int32)
    for t in range(n):
        cnt = 0
        for k in range(1, min(5, t + 1)):  # need at least k=1..min(4, t)
            a = t - (k - 1)
            b = t - k
            if b < 0:
                break
            if h_arr[a] < h_arr[b] and l_arr[a] > l_arr[b]:
                cnt += 1
            else:
                break
        out[t] = cnt
    return out


def _vol_expanding_vec(v_arr: np.ndarray) -> np.ndarray:
    """`all(v[-i] > v[-i-1] for i in 1..min(4,n))` per bar — needs 3 strictly-increasing prior bars."""
    n = len(v_arr)
    out = np.zeros(n, dtype=bool)
    for t in range(n):
        ok = True
        for i in range(1, min(4, t + 1)):
            a = t - (i - 1)
            b = t - i
            if b < 0:
                ok = False
                break
            if not (v_arr[a] > v_arr[b]):
                ok = False
                break
        out[t] = ok
    return out


def _compression_vec(h_arr: np.ndarray, l_arr: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """For each bar t, compute compression bool + compression_ratio from last 5 ranges.

    Live: ranges_5 = [range(-i) for i in 1..min(6,n)]. ranges_5[0] = current range.
    compression = ranges_5[i] <= ranges_5[i+1] for all i (i.e. ranges grow as we look further back
    → current is the most compressed). compression_ratio = ranges_5[0] / ranges_5[-1].
    Requires len(ranges_5) >= 3.
    """
    n = len(h_arr)
    compression = np.zeros(n, dtype=bool)
    ratio = np.ones(n, dtype=np.float64)
    for t in range(n):
        cnt = min(6, t + 1)
        if cnt < 3:
            continue
        ranges = np.empty(cnt - 1, dtype=np.float64)
        for i in range(1, cnt):
            j = t - (i - 1)
            r = h_arr[j] - l_arr[j]
            ranges[i - 1] = r if r >= 1e-10 else 1e-10
        # compression: ranges[i] <= ranges[i+1] for all i
        ok = True
        for i in range(len(ranges) - 1):
            if not (ranges[i] <= ranges[i + 1]):
                ok = False
                break
        compression[t] = ok
        if ranges[-1] > 0:
            ratio[t] = ranges[0] / ranges[-1]
        else:
            ratio[t] = 1.0
    return compression, ratio


# ════════════════════════════════════════════════════════════════════════════
# Vectorized pattern detector
# ════════════════════════════════════════════════════════════════════════════

def detect_bar_patterns_vec(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray,
    timeframe: str,
) -> Dict[str, np.ndarray]:
    """Vectorized form. Returns dict-of-shape(N,)-arrays. For idx < 5 the
    pattern is "none" and direction/strength are 0 (live early-returns when
    n < 10, but per-bar we set sentinel defaults for indices that lack 5 bars)."""
    n = len(o)
    out: Dict[str, np.ndarray] = {}
    if n == 0:
        return out

    o = np.asarray(o, dtype=np.float64)
    h = np.asarray(h, dtype=np.float64)
    l = np.asarray(l, dtype=np.float64)
    c = np.asarray(c, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)

    pattern_code = np.zeros(n, dtype=np.int8)
    direction = np.zeros(n, dtype=np.int8)
    strength = np.zeros(n, dtype=np.float64)
    bar_body_ratio = np.zeros(n, dtype=np.float64)
    upper_wick_norm = np.zeros(n, dtype=np.float64)
    lower_wick_norm = np.zeros(n, dtype=np.float64)

    # Compute per-bar body / range / wick arrays (shifted views).
    # For bar idx t, b1=t, b2=t-1, b3=t-2, b4=t-3, b5=t-4.
    # We need at least t >= 4 for the 5-back-bar patterns. For t<4, leave defaults.

    # Pre-compute generic per-bar fields (range1, body1, wicks) for ALL t.
    range_all = np.maximum(h - l, 1e-10)
    body_all = np.abs(c - o)
    upper_wick_all = h - np.maximum(o, c)
    lower_wick_all = np.minimum(o, c) - l
    bar_body_ratio = body_all / range_all
    upper_wick_norm = np.where(range_all > 0, upper_wick_all / range_all, 0.0)
    lower_wick_norm = np.where(range_all > 0, lower_wick_all / range_all, 0.0)

    is_bull_all = c > o
    is_bear_all = c < o

    # vol_avg: mean of prior `vol_window` bars (exclusive of current).
    # live uses vol_window = min(20, n-1). For per-bar, use 20 throughout (with
    # graceful fallback for small windows).
    vol_avg = _rolling_mean_prev(v, 20)
    # When vol_avg <= 0, scalar uses fallback vol_ratio = 1.0
    vol_ratio = np.where(vol_avg > 0, v / np.maximum(vol_avg, 1e-12), 1.0)
    vol_confirm = vol_ratio >= 1.3
    vol_spike = vol_ratio >= 2.0
    vol_dry = vol_ratio < 0.6
    vol_expanding = _vol_expanding_vec(v)

    atr_arr = h - l
    atr_rank = _rolling_rank_inclusive(atr_arr, 50)
    vol_regime = np.where(
        atr_rank < 0.25, "low",
        np.where(atr_rank > 0.75, "high", "normal"),
    ).astype(object)

    streak = _streak_vec(o, c)
    inside_count = _inside_count_vec(h, l)
    compression, compression_ratio = _compression_vec(h, l)

    # Swing structure for bar t needs h[t], h[t-1], h[t-2] and same for lows
    swing_bull = np.zeros(n, dtype=bool)
    swing_bear = np.zeros(n, dtype=bool)
    if n >= 3:
        h1 = h[2:]; h2 = h[1:-1]; h3 = h[:-2]
        l1 = l[2:]; l2 = l[1:-1]; l3 = l[:-2]
        hh = (h1 > h2) & (h2 > h3)
        hl = (l1 > l2) & (l2 > l3)
        ll = (l1 < l2) & (l2 < l3)
        lh = (h1 < h2) & (h2 < h3)
        swing_bull[2:] = hh & hl
        swing_bear[2:] = ll & lh

    # ── PATTERN PRIORITY EVALUATION (vectorized) ─────────────────────────────
    if n >= 5:
        # Slice helpers (length N-4): index t in [4, n-1] for bar1 == t.
        # For convenience we'll build aligned per-bar values; bar1..bar5 correspond to lags 0..4
        b1 = (o, h, l, c, v)
        # Aligned arrays for bar1..bar5 starting at index 4 of base arrays.
        # We'll compute per-bar everywhere and then mask-fill into pattern_code.

        # Shifted arrays
        def lag(a, k):
            # value of a[t-k] aligned at index t (only valid where t >= k)
            res = np.empty(n, dtype=a.dtype)
            res[:k] = a[0]  # filler; will be masked off
            res[k:] = a[:-k]
            return res

        o1, h1, l1, c1, v1_ = o, h, l, c, v
        o2, h2, l2, c2 = lag(o, 1), lag(h, 1), lag(l, 1), lag(c, 1)
        o3, h3, l3, c3 = lag(o, 2), lag(h, 2), lag(l, 2), lag(c, 2)
        o4, h4, l4, c4 = lag(o, 3), lag(h, 3), lag(l, 3), lag(c, 3)
        o5, h5, l5, c5 = lag(o, 4), lag(h, 4), lag(l, 4), lag(c, 4)

        body1 = np.abs(c1 - o1)
        body2 = np.abs(c2 - o2)
        body3 = np.abs(c3 - o3)
        body4 = np.abs(c4 - o4)
        range1 = np.maximum(h1 - l1, 1e-10)
        range2 = np.maximum(h2 - l2, 1e-10)
        range3 = np.maximum(h3 - l3, 1e-10)
        upper_wick1 = h1 - np.maximum(o1, c1)
        lower_wick1 = np.minimum(o1, c1) - l1
        body_ratio1 = body1 / range1
        is_bull1 = c1 > o1
        is_bear1 = c1 < o1
        is_bull2 = c2 > o2
        is_bear2 = c2 < o2
        is_bull3 = c3 > o3
        is_bear3 = c3 < o3

        # valid mask: bar needs t>=4 (5 bars of history)
        valid = np.zeros(n, dtype=bool)
        valid[4:] = True

        # 1 morning_star
        m1 = valid & is_bear3 & (body3 > range3 * 0.5) & (body2 < range2 * 0.3) & is_bull1 & (body1 > range1 * 0.5) & (c1 > (o3 + c3) / 2)
        # 2 evening_star
        m2 = valid & is_bull3 & (body3 > range3 * 0.5) & (body2 < range2 * 0.3) & is_bear1 & (body1 > range1 * 0.5) & (c1 < (o3 + c3) / 2)
        # 3 three_white_soldiers
        m3 = valid & is_bull1 & is_bull2 & is_bull3 & (c1 > c2) & (c2 > c3) & (body1 > range1 * 0.5) & (body2 > range2 * 0.5) & (body3 > range3 * 0.5)
        # 4 three_black_crows
        m4 = valid & is_bear1 & is_bear2 & is_bear3 & (c1 < c2) & (c2 < c3) & (body1 > range1 * 0.5) & (body2 > range2 * 0.5) & (body3 > range3 * 0.5)
        # 5 bull_engulfing
        m5 = valid & is_bull1 & is_bear2 & (c1 > o2) & (o1 < c2) & (body1 > body2)
        # 6 bear_engulfing
        m6 = valid & is_bear1 & is_bull2 & (c1 < o2) & (o1 > c2) & (body1 > body2)
        # 7 tweezer_bottom
        m7 = valid & is_bull1 & (np.abs(l1 - l2) < range1 * 0.05) & (l1 < np.minimum(l3, l4))
        # 8 tweezer_top
        m8 = valid & is_bear1 & (np.abs(h1 - h2) < range1 * 0.05) & (h1 > np.maximum(h3, h4))
        # 9 hammer
        m9 = valid & (body_ratio1 < 0.35) & (lower_wick1 > body1 * 2.0) & (upper_wick1 < body1 * 0.5)
        # 10 shooting_star
        m10 = valid & (body_ratio1 < 0.35) & (upper_wick1 > body1 * 2.0) & (lower_wick1 < body1 * 0.5)
        # 11 bull_harami
        m11 = valid & is_bull1 & is_bear2 & (body1 < body2 * 0.5) & (h1 < h2) & (l1 > l2)
        # 12 bear_harami
        m12 = valid & is_bear1 & is_bull2 & (body1 < body2 * 0.5) & (h1 < h2) & (l1 > l2)
        # 13 multi_inside
        m13 = valid & (inside_count >= 2)
        # 14 inside_bar
        m14 = valid & (h1 < h2) & (l1 > l2)
        # 15 outside_bar
        m15 = valid & (h1 > h2) & (l1 < l2) & (body_ratio1 > 0.6)
        # 16 pin_bar_bull
        m16 = valid & (lower_wick1 > range1 * 0.6) & (body_ratio1 < 0.25)
        # 17 pin_bar_bear
        m17 = valid & (upper_wick1 > range1 * 0.6) & (body_ratio1 < 0.25)
        # 18 three_bar_bull
        m18 = valid & is_bull1 & is_bear2 & is_bear3 & (c1 > h2)
        # 19 three_bar_bear
        m19 = valid & is_bear1 & is_bull2 & is_bull3 & (c1 < l2)
        # 20 doji
        m20 = valid & (body_ratio1 < 0.1)

        # Apply in priority order — only fill if pattern_code still 0
        def _apply(mask, code):
            target = (pattern_code == 0) & mask
            pattern_code[target] = code

        _apply(m1, 1); _apply(m2, 2); _apply(m3, 3); _apply(m4, 4)
        _apply(m5, 5); _apply(m6, 6); _apply(m7, 7); _apply(m8, 8)
        _apply(m9, 9); _apply(m10, 10); _apply(m11, 11); _apply(m12, 12)
        _apply(m13, 13); _apply(m14, 14); _apply(m15, 15); _apply(m16, 16)
        _apply(m17, 17); _apply(m18, 18); _apply(m19, 19); _apply(m20, 20)

        # Direction + strength per code
        # Compute strength per pattern, then select via masks.
        # We'll compute once per pattern and write into strength only where pattern_code matches that code.
        def _write(code, dir_val, strength_arr):
            sel = pattern_code == code
            if not np.any(sel):
                return
            direction[sel] = dir_val
            strength[sel] = strength_arr[sel]

        s1 = np.minimum(1.0, (body1 + body3) / (2 * range1 + 1e-10))  # morning_star
        s2 = np.minimum(1.0, (body1 + body3) / (2 * range1 + 1e-10))  # evening_star
        s3 = np.minimum(1.0,
                        np.minimum(np.minimum(body1, body2), body3) /
                        np.maximum(np.maximum(range1, range2), range3))
        s4 = s3
        s5 = np.minimum(1.0, (body1 / (body2 + 1e-10)) * 0.5)
        s6 = s5
        s7 = np.minimum(1.0, 1.0 - np.abs(l1 - l2) / range1)
        s8 = np.minimum(1.0, 1.0 - np.abs(h1 - h2) / range1)
        s9 = np.minimum(1.0, lower_wick1 / range1)
        s10 = np.minimum(1.0, upper_wick1 / range1)
        s11 = 0.5 * (1.0 - body1 / (body2 + 1e-10))
        s12 = s11
        s13 = np.minimum(1.0, inside_count.astype(np.float64) * 0.3)
        s14 = 1.0 - (range1 / range2)
        s15 = body_ratio1.copy()
        s16 = lower_wick1 / range1
        s17 = upper_wick1 / range1
        s18 = np.minimum(1.0, body1 / (body2 + body3 + 1e-10))
        s19 = np.minimum(1.0, body1 / (body2 + body3 + 1e-10))
        s20 = 0.3 + np.where(vol_confirm, 0.4, 0.0)

        _write(1, 1, s1)
        _write(2, -1, s2)
        _write(3, 1, s3)
        _write(4, -1, s4)
        _write(5, 1, s5)
        _write(6, -1, s6)
        _write(7, 1, s7)
        _write(8, -1, s8)
        _write(9, 1, s9)
        _write(10, -1, s10)
        _write(11, 1, s11)
        _write(12, -1, s12)
        _write(13, 0, s13)
        _write(14, 0, s14)
        # outside_bar — direction depends on is_bull1
        sel = pattern_code == 15
        if np.any(sel):
            direction[sel] = np.where(is_bull1[sel], 1, -1).astype(np.int8)
            strength[sel] = s15[sel]
        _write(16, 1, s16)
        _write(17, -1, s17)
        _write(18, 1, s18)
        _write(19, -1, s19)
        _write(20, 0, s20)

        # Apply vol amplifier (direction != 0)
        dir_mask = direction != 0
        if np.any(dir_mask):
            amp = np.ones(n, dtype=np.float64)
            amp = np.where(vol_confirm, 1.3, amp)
            new_strength = np.minimum(1.0, strength * amp)
            strength = np.where(dir_mask, new_strength, strength)

            amp2 = np.where(vol_spike, 1.2, 1.0)
            new_strength = np.minimum(1.0, strength * amp2)
            strength = np.where(dir_mask, new_strength, strength)

            amp3 = np.where(vol_dry, 0.6, 1.0)
            new_strength = strength * amp3
            strength = np.where(dir_mask, new_strength, strength)

    # Build the output dict
    out[f"bar_pattern_{timeframe}"] = _PATTERN_NAME_ARR[pattern_code]
    out[f"bar_direction_{timeframe}"] = direction
    out[f"bar_strength_{timeframe}"] = np.round(strength, 3)
    out[f"bar_vol_confirm_{timeframe}"] = vol_confirm
    out[f"bar_vol_ratio_{timeframe}"] = np.round(vol_ratio, 2)
    out[f"bar_body_ratio_{timeframe}"] = np.round(bar_body_ratio, 3)
    out[f"bar_upper_wick_{timeframe}"] = np.round(upper_wick_norm, 3)
    out[f"bar_lower_wick_{timeframe}"] = np.round(lower_wick_norm, 3)
    out[f"bar_streak_{timeframe}"] = streak
    out[f"bar_swing_bull_{timeframe}"] = swing_bull
    out[f"bar_swing_bear_{timeframe}"] = swing_bear
    out[f"bar_compression_{timeframe}"] = compression
    out[f"bar_compression_ratio_{timeframe}"] = np.round(compression_ratio, 3)
    out[f"bar_inside_count_{timeframe}"] = inside_count
    out[f"bar_vol_spike_{timeframe}"] = vol_spike
    out[f"bar_vol_expanding_{timeframe}"] = vol_expanding
    out[f"bar_vol_regime_{timeframe}"] = vol_regime
    out[f"bar_atr_rank_{timeframe}"] = np.round(atr_rank, 3)
    return out
