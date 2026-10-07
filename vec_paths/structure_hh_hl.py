"""structure_hh_hl.py — Vectorized HH/HL/LH/LL structure detection.

USER MANDATE 2026-05-17: Higher-high / higher-low / lower-high / lower-low
structure must drive entries and exits. ALL of the following series get
HH/HL/LH/LL tracking on every TF:
  - price (using high for HH/LH, low for HL/LL)
  - wt1 (wavetrend fast)
  - wt2 (wavetrend slow)
  - stoch_k
  - dc_basis (midpoint of donchian channel)

Pivot rule mixed by TF (user-confirmed 2026-05-17):
  - 3m, 5m, 15m  -> 3-bar Williams fractal (1-bar lag)
  - 1h, 4h, D, W -> 5-bar Bill Williams fractal (2-bar lag)

Entry framework (LONG, mirror for SHORT):
  - On STRUCT_BREAKOUT_TF (D or W): most-recent confirmed pivot is HH
  - On STRUCT_RETEST_TF (1h or 4h crypto / 15m or 1h stocks):
    current bar within STRUCT_ATR_RETEST_BAND * atr_{retest_tf}
    of last_pivot_lo for that TF
  - On STRUCT_TRIGGER_TF (3m/5m or 15m): >= STRUCT_MIN_SERIES_COUNT of the
    5 series have struct == HL.

Exit fires when >= STRUCT_EXIT_FLIP_MIN of the 5 series flip to opposite
structure on the trigger TF.

ALL functions pure numpy. Computed on-the-fly from existing NPZ fields
(high_<tf>, low_<tf>, wt1_<tf>, wt2_<tf>, stoch_k_<tf>, dc_high_<tf>,
dc_low_<tf>, atr_<tf>). No precompute regen needed.
"""
from __future__ import annotations

from typing import Dict, Tuple, Optional, Sequence, Mapping

import numpy as np

STRUCT_NONE = np.int8(0)
STRUCT_HH = np.int8(2)
STRUCT_HL = np.int8(1)
STRUCT_LH = np.int8(-1)
STRUCT_LL = np.int8(-2)

_FAST_TFS = frozenset(("3m", "5m", "15m"))

_SERIES_KEYS = ("price", "wt1", "wt2", "stoch_k", "dc_basis")


def _pivot_bars_for_tf(tf: str) -> int:
    return 1 if tf in _FAST_TFS else 2


def _extract_unique_steps(series: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """For a forward-filled HTF series on a base-TF grid (e.g. high_D stored at
    3m frequency with 480 identical consecutive values per D bar), return:
      compressed_values : one value per HTF bar
      base_idx_at_step  : base-TF index where each compressed value FIRST appears

    For a non-stepped series (one value per bar), returns the series itself and
    np.arange(n). Constant-equal-NaN runs are treated as a single step.
    """
    n = series.shape[0]
    if n == 0:
        return series, np.zeros(0, dtype=np.int64)
    # diff treats NaN!=NaN as True; use nan-aware mask
    s = np.asarray(series, dtype=np.float64)
    prev = np.concatenate(([np.nan], s[:-1]))
    cur_nan = np.isnan(s)
    prev_nan = np.isnan(prev)
    same_nan = cur_nan & prev_nan
    same_val = (s == prev) & ~cur_nan & ~prev_nan
    changed = ~(same_nan | same_val)
    changed[0] = True
    edges = np.where(changed)[0]
    return s[edges], edges


def _detect_pivots_stepped(series: np.ndarray, side: str, bars: int) -> Tuple[np.ndarray, np.ndarray]:
    """Detect pivots on a (possibly forward-filled) series.

    Returns:
      pivot_mask_base : bool[n] — True at base-TF bar where pivot CONFIRMS
                        (the (bars)-th step after the pivot step itself)
      pivot_step_idx  : int64[k] — for each confirmed pivot, the base-TF index
                        of the FIRST bar of the pivot step (== the HTF bar's
                        starting base-TF bar)
    """
    compressed, edges = _extract_unique_steps(series)
    n_steps = compressed.shape[0]
    n_base = series.shape[0]
    base_mask = np.zeros(n_base, dtype=bool)
    if n_steps < (2 * bars + 1):
        return base_mask, np.zeros(0, dtype=np.int64)
    inner = np.ones(n_steps - 2 * bars, dtype=bool)
    center = compressed[bars:n_steps - bars]
    if side == "hi":
        for off in range(1, bars + 1):
            inner &= center > compressed[bars - off:n_steps - bars - off]
            inner &= center > compressed[bars + off:n_steps - bars + off]
    else:
        for off in range(1, bars + 1):
            inner &= center < compressed[bars - off:n_steps - bars - off]
            inner &= center < compressed[bars + off:n_steps - bars + off]
    pivot_step_positions = np.where(inner)[0] + bars  # index into compressed[]
    # Pivot CONFIRMS at the start of the (pivot_step + bars + 1) step — i.e. when
    # the (bars)-th future HTF bar has begun and we know the center is the extreme.
    confirm_step_positions = pivot_step_positions + bars
    # Clamp confirm positions that fall past the last step (still confirm at last edge)
    confirm_step_positions = np.minimum(confirm_step_positions, n_steps - 1)
    confirm_base_idx = edges[confirm_step_positions]
    # Pivot VALUE step's base-TF starting index (for last_hi/last_lo)
    pivot_value_base_idx = edges[pivot_step_positions]
    base_mask[confirm_base_idx] = True
    return base_mask, pivot_value_base_idx


def _detect_pivots(series: np.ndarray, side: str, bars: int) -> np.ndarray:
    """Back-compat single-array path — uses stepped detector under the hood."""
    mask, _ = _detect_pivots_stepped(series, side, bars)
    return mask


def compute_series_structure(
    series_hi: np.ndarray,
    series_lo: np.ndarray,
    bars: int,
) -> Dict[str, np.ndarray]:
    """Compute structure arrays for ONE series on ONE TF (handles stepped HTF series).

    series_hi: array used for detecting pivot-highs (e.g. price.high or wt1)
    series_lo: array used for detecting pivot-lows  (e.g. price.low  or wt1)
    Pass same array twice if the series is a single line (wt1, wt2, k, dc_basis).
    Use high/low arrays for price.

    For NPZ-stored HTF fields that are forward-filled to base-TF cadence
    (e.g. high_D is constant within each D bar), pivots are detected on the
    compressed unique-step series and the resulting struct/last-pivot info is
    forward-filled back to base-TF cadence so callers can index at any base bar.

    Returns dict with int8 + float32 arrays of length n_base:
      pivot_event : 0 normally, +2/+1/-1/-2 at the base-TF bar a pivot CONFIRMS
      struct      : carried-forward classification (+2 HH, +1 HL, -1 LH, -2 LL, 0 none)
      last_hi     : float, last confirmed pivot-high value (carried forward; NaN until first)
      last_lo     : float, last confirmed pivot-low value
      prev_hi     : previous confirmed pivot-high value
      prev_lo     : previous confirmed pivot-low value
    """
    n = series_hi.shape[0]
    pivot_event = np.zeros(n, dtype=np.int8)
    struct = np.zeros(n, dtype=np.int8)
    last_hi = np.full(n, np.nan, dtype=np.float64)
    last_lo = np.full(n, np.nan, dtype=np.float64)
    prev_hi_arr = np.full(n, np.nan, dtype=np.float64)
    prev_lo_arr = np.full(n, np.nan, dtype=np.float64)
    if n == 0:
        return {
            "pivot_event": pivot_event, "struct": struct,
            "last_hi": last_hi.astype(np.float32), "last_lo": last_lo.astype(np.float32),
            "prev_hi": prev_hi_arr.astype(np.float32), "prev_lo": prev_lo_arr.astype(np.float32),
        }
    # Detect pivots on the (possibly stepped) series. confirm_base_idx is where
    # we learn about the pivot; pivot_value_base_idx is the bar whose VALUE we
    # treat as the pivot price (start of the pivot HTF step).
    hi_confirm_mask, hi_value_idx = _detect_pivots_stepped(series_hi, "hi", bars)
    lo_confirm_mask, lo_value_idx = _detect_pivots_stepped(series_lo, "lo", bars)
    hi_confirm_idx = np.where(hi_confirm_mask)[0]
    lo_confirm_idx = np.where(lo_confirm_mask)[0]
    # Merge HI and LO pivot events into a single ordered timeline by confirm bar.
    # Each event: (confirm_idx, kind, value).  kind: 0=hi, 1=lo.
    events = []
    for idx, vidx in zip(hi_confirm_idx, hi_value_idx):
        events.append((int(idx), 0, float(series_hi[vidx])))
    for idx, vidx in zip(lo_confirm_idx, lo_value_idx):
        events.append((int(idx), 1, float(series_lo[vidx])))
    events.sort(key=lambda x: (x[0], x[1]))
    cur_hi = np.nan
    cur_lo = np.nan
    prev_hi = np.nan
    prev_lo = np.nan
    cur_struct = STRUCT_NONE
    # We accumulate event-by-event and write into the *segment* starting at this
    # event's base-TF index. Between events nothing changes.
    fill_starts = [0]
    fill_values = {"struct": [STRUCT_NONE], "last_hi": [np.nan], "last_lo": [np.nan],
                   "prev_hi": [np.nan], "prev_lo": [np.nan]}
    for confirm_idx, kind, val in events:
        if kind == 0:  # hi
            prev_hi = cur_hi
            cur_hi = val
            if not np.isnan(prev_hi):
                ev = STRUCT_HH if cur_hi > prev_hi else STRUCT_LH
                pivot_event[confirm_idx] = ev
                cur_struct = ev
        else:  # lo
            prev_lo = cur_lo
            cur_lo = val
            if not np.isnan(prev_lo):
                ev = STRUCT_HL if cur_lo > prev_lo else STRUCT_LL
                pivot_event[confirm_idx] = ev
                cur_struct = ev
        fill_starts.append(confirm_idx)
        fill_values["struct"].append(cur_struct)
        fill_values["last_hi"].append(cur_hi)
        fill_values["last_lo"].append(cur_lo)
        fill_values["prev_hi"].append(prev_hi)
        fill_values["prev_lo"].append(prev_lo)
    fill_starts.append(n)
    for i in range(len(fill_starts) - 1):
        a = fill_starts[i]
        b = fill_starts[i + 1]
        if b <= a:
            continue
        struct[a:b] = fill_values["struct"][i]
        last_hi[a:b] = fill_values["last_hi"][i]
        last_lo[a:b] = fill_values["last_lo"][i]
        prev_hi_arr[a:b] = fill_values["prev_hi"][i]
        prev_lo_arr[a:b] = fill_values["prev_lo"][i]
    return {
        "pivot_event": pivot_event,
        "struct": struct,
        "last_hi": last_hi.astype(np.float32),
        "last_lo": last_lo.astype(np.float32),
        "prev_hi": prev_hi_arr.astype(np.float32),
        "prev_lo": prev_lo_arr.astype(np.float32),
    }


def _get_field(npz: Mapping[str, np.ndarray], key: str, n: int) -> Optional[np.ndarray]:
    arr = npz.get(key)
    if arr is None:
        return None
    a = np.asarray(arr)
    if a.shape[0] != n:
        return None
    return a


def compute_all_structure(
    npz: Mapping[str, np.ndarray],
    mode: str,
) -> Dict[Tuple[str, str], Dict[str, np.ndarray]]:
    """Compute structure for ALL (series, TF) pairs available in this NPZ.

    Returns dict keyed by (series_name, tf):
      ("price",    "15m") -> {struct, pivot_event, last_hi, last_lo, prev_hi, prev_lo}
      ("wt1",      "15m") -> ...
      ...etc

    Only emits keys for series×TF where required NPZ fields are present.
    Length of each output array == length of base-TF (matches NPZ).
    """
    out: Dict[Tuple[str, str], Dict[str, np.ndarray]] = {}
    if mode == "tradier":
        tfs = ("5m", "15m", "1h", "4h", "D")
    else:
        tfs = ("3m", "15m", "1h", "4h", "D", "W")
    close = npz.get("close")
    if close is None:
        return out
    n = int(np.asarray(close).shape[0])
    for tf in tfs:
        bars = _pivot_bars_for_tf(tf)
        hi = _get_field(npz, f"high_{tf}", n)
        lo = _get_field(npz, f"low_{tf}", n)
        if hi is not None and lo is not None:
            out[("price", tf)] = compute_series_structure(hi, lo, bars)
        wt1 = _get_field(npz, f"wt1_{tf}", n)
        if wt1 is not None:
            out[("wt1", tf)] = compute_series_structure(wt1, wt1, bars)
        wt2 = _get_field(npz, f"wt2_{tf}", n)
        if wt2 is not None:
            out[("wt2", tf)] = compute_series_structure(wt2, wt2, bars)
        k = _get_field(npz, f"stoch_k_{tf}", n)
        if k is not None:
            out[("stoch_k", tf)] = compute_series_structure(k, k, bars)
        dch = _get_field(npz, f"dc_high_{tf}", n)
        dcl = _get_field(npz, f"dc_low_{tf}", n)
        if dch is not None and dcl is not None:
            dcb = ((dch + dcl) * 0.5).astype(np.float32)
            out[("dc_basis", tf)] = compute_series_structure(dcb, dcb, bars)
    return out


def _streak_at_value(arr: np.ndarray, target: int) -> np.ndarray:
    """For each i, length of consecutive same-value run ending at i where value==target."""
    matches = (arr == target).astype(np.int64)
    n = matches.shape[0]
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    # Index of last bar where match fails, carried forward
    idx_no_match = np.where(matches == 0, np.arange(n), -1)
    last_no_match = np.maximum.accumulate(idx_no_match)
    streak = np.arange(n) - last_no_match
    streak = streak * matches  # zero where not matching
    return streak


def _recent_events_count(event_mask: np.ndarray, window_bars: int) -> np.ndarray:
    """For each bar i, return 1 if any True in event_mask[i-window+1 : i+1], else 0.
    Vectorized via running prefix sum (O(n))."""
    n = event_mask.shape[0]
    if n == 0:
        return np.zeros(0, dtype=np.int8)
    em = event_mask.astype(np.int32)
    cs = np.cumsum(em)
    # window sum at i = cs[i] - cs[i - window]   (cs[-1] treated as 0)
    shifted = np.zeros(n, dtype=np.int32)
    if window_bars < n:
        shifted[window_bars:] = cs[:n - window_bars]
    window_sum = cs - shifted
    return (window_sum > 0).astype(np.int8)


def compose_entry_exit_fires(
    npz: Mapping[str, np.ndarray],
    structure: Mapping[Tuple[str, str], Mapping[str, np.ndarray]],
    *,
    breakout_tf: str,
    retest_tf: str,
    trigger_tf: str,
    series_required: Sequence[str] = _SERIES_KEYS,
    min_count: int = 4,
    atr_retest_band: float = 0.5,
    exit_flip_min: int = 2,
    side_mode: str = "BOTH",
    regime_persist_bars: int = 0,
    exit_recent_window_bars: int = 0,
) -> Dict[str, np.ndarray]:
    """Compose per-bar entry + exit fire arrays.

    Returns dict:
      entry_long  : bool array, True at bars where LONG should fire
      entry_short : bool array
      exit_long   : bool array, True at bars where LONG position should close
      exit_short  : bool array

    All arrays length == base-TF bar count.

    Rule (LONG):
      a) On breakout_tf: most-recent pivot_event resolved to HH (struct >= HH at this bar).
      b) On retest_tf: |close - last_lo_retest| / atr_retest < atr_retest_band.
      c) On trigger_tf: >= min_count of {series_required} have struct == HL.

    Rule (SHORT): mirror with LL/LH/LH.

    Exit (LONG): >= exit_flip_min of {series_required} on trigger_tf
                 flipped to struct == LH or LL.
    Exit (SHORT): mirror with HH/HL.
    """
    close = np.asarray(npz["close"])
    n = close.shape[0]
    z_bool = np.zeros(n, dtype=bool)
    entry_long = z_bool.copy()
    entry_short = z_bool.copy()
    exit_long = z_bool.copy()
    exit_short = z_bool.copy()

    bk_price = structure.get(("price", breakout_tf))
    rt_price = structure.get(("price", retest_tf))
    if bk_price is None or rt_price is None:
        return {
            "entry_long": entry_long, "entry_short": entry_short,
            "exit_long": exit_long, "exit_short": exit_short,
        }
    bk_struct = bk_price["struct"]
    rt_last_lo = rt_price["last_lo"]
    rt_last_hi = rt_price["last_hi"]

    atr_rt = npz.get(f"atr_{retest_tf}")
    if atr_rt is None:
        atr_rt = np.full(n, np.nan, dtype=np.float32)
    atr_rt = np.where(np.isfinite(atr_rt) & (atr_rt > 0), atr_rt, np.nan)

    if regime_persist_bars > 0:
        bk_is_hh = _streak_at_value(bk_struct, int(STRUCT_HH)) >= regime_persist_bars
        bk_is_ll = _streak_at_value(bk_struct, int(STRUCT_LL)) >= regime_persist_bars
    else:
        bk_is_hh = bk_struct >= STRUCT_HH
        bk_is_ll = bk_struct <= STRUCT_LL
    dist_lo = np.abs(close - rt_last_lo)
    dist_hi = np.abs(close - rt_last_hi)
    with np.errstate(invalid="ignore", divide="ignore"):
        retest_lo_ok = (dist_lo / atr_rt) < atr_retest_band
        retest_hi_ok = (dist_hi / atr_rt) < atr_retest_band
    retest_lo_ok = np.where(np.isfinite(retest_lo_ok), retest_lo_ok, False).astype(bool)
    retest_hi_ok = np.where(np.isfinite(retest_hi_ok), retest_hi_ok, False).astype(bool)

    hl_count = np.zeros(n, dtype=np.int8)
    lh_count = np.zeros(n, dtype=np.int8)
    exit_flip_long_count = np.zeros(n, dtype=np.int8)
    exit_flip_short_count = np.zeros(n, dtype=np.int8)
    series_used = 0
    use_pivot_window = exit_recent_window_bars > 0
    for s in series_required:
        d = structure.get((s, trigger_tf))
        if d is None:
            continue
        st = d["struct"]
        hl_count += (st == STRUCT_HL).astype(np.int8)
        lh_count += (st == STRUCT_LH).astype(np.int8)
        if use_pivot_window:
            ev = d["pivot_event"]
            bear_event = (ev == STRUCT_LH) | (ev == STRUCT_LL)
            bull_event = (ev == STRUCT_HH) | (ev == STRUCT_HL)
            exit_flip_long_count += _recent_events_count(bear_event, exit_recent_window_bars)
            exit_flip_short_count += _recent_events_count(bull_event, exit_recent_window_bars)
        else:
            exit_flip_long_count += ((st == STRUCT_LH) | (st == STRUCT_LL)).astype(np.int8)
            exit_flip_short_count += ((st == STRUCT_HH) | (st == STRUCT_HL)).astype(np.int8)
        series_used += 1

    if series_used == 0:
        return {
            "entry_long": entry_long, "entry_short": entry_short,
            "exit_long": exit_long, "exit_short": exit_short,
        }

    long_armed = bk_is_hh & retest_lo_ok & (hl_count >= min_count)
    short_armed = bk_is_ll & retest_hi_ok & (lh_count >= min_count)

    if side_mode in ("BOTH", "LONG_ONLY"):
        entry_long = long_armed
    if side_mode in ("BOTH", "SHORT_ONLY"):
        entry_short = short_armed

    exit_long = exit_flip_long_count >= exit_flip_min
    exit_short = exit_flip_short_count >= exit_flip_min

    return {
        "entry_long": entry_long,
        "entry_short": entry_short,
        "exit_long": exit_long,
        "exit_short": exit_short,
    }


def summarize_pivots(structure: Mapping[Tuple[str, str], Mapping[str, np.ndarray]]) -> Dict[str, int]:
    """Pivot count by (series, TF) for diagnostics."""
    out: Dict[str, int] = {}
    for (s, tf), d in structure.items():
        ev = d.get("pivot_event")
        if ev is None:
            continue
        out[f"{s}_{tf}_pivots_hi"] = int(((ev == STRUCT_HH) | (ev == STRUCT_LH)).sum())
        out[f"{s}_{tf}_pivots_lo"] = int(((ev == STRUCT_HL) | (ev == STRUCT_LL)).sum())
    return out
