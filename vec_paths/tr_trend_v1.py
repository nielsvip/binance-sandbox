"""
vec_paths/tr_trend_v1.py — TR_TREND_v1 breakout-retest strategy (Daily-decision).

Source spec: data/research_20260516/strategy_plan.md §4.

DESIGN
======
Pure-function module — same pattern as vec_paths/exit_r1_r2.py.
NO live-code imports. Stateless beyond the per-symbol bookkeeping dict
passed in by the engine (`tr_state`).

Two-path entry (Bulkowski + Wyckoff/Minervini/Weinstein synthesis):
  Path A — initial breakout on D close above 20-D Donchian high with
           volume >= 1.5x SMA50(D vol) + Minervini-lite trend template +
           SPY regime + no earnings within 5d.  Opens HALF unit.
  Path B — within 5 D bars: price retests pivot +/- 0.5% on lighter
           volume (<= 0.7x breakout-bar vol), next D bar closes back
           above pivot. Adds SECOND HALF unit.

Three exit paths (D-close evaluation):
  1. STOP_HIT   — close <= stop_price (chandelier-trailed after +1 ATR profit)
  2. 50SMA_BREAK — D close below D 50-SMA (LONG; mirror SHORT)
  3. DC_REVERSE  — Donchian-20 reverse breakout
Plus 60-D-bar time-stop if no new high for last 30 bars.

NO fixed % targets. NO micro-gain exits. Let winners run.

CONFIG KNOBS (all read via getattr with sane defaults):
    TR_TREND_V1_ENABLED: bool = False  (master kill — default OFF)
    TR_TREND_V1_DC_LOOKBACK_D: int = 20
    TR_TREND_V1_VOL_MULT: float = 1.5
    TR_TREND_V1_VOL_SMA_LEN_D: int = 50
    TR_TREND_V1_TT_NEAR_HIGH_PCT: float = 25.0  (within X% of 52w high)
    TR_TREND_V1_TT_MIN_PASS: int = 4            (4-of-5 template gates)
    TR_TREND_V1_SMA50_LEN_D: int = 50
    TR_TREND_V1_SMA200_LEN_D: int = 200
    TR_TREND_V1_SMA200_SLOPE_LOOKBACK_D: int = 10
    TR_TREND_V1_W52_BARS_D: int = 252
    TR_TREND_V1_ATR_STOP_MULT: float = 2.0
    TR_TREND_V1_RETEST_TOL_PCT: float = 0.5
    TR_TREND_V1_RETEST_VOL_MAX_MULT: float = 0.7
    TR_TREND_V1_RETEST_MAX_BARS_D: int = 5
    TR_TREND_V1_TIME_STOP_BARS_D: int = 60
    TR_TREND_V1_TIME_STOP_NO_HIGH_BARS_D: int = 30
    TR_TREND_V1_SPY_REGIME_ENABLED: bool = True
    TR_TREND_V1_SPY_SLOPE_LOOKBACK_D: int = 10
    TR_TREND_V1_RISK_PCT: float = 0.5    (50bp account risk per FULL unit)
    TR_TREND_V1_ACCOUNT_USD: float = 35000.0

PRECOMPUTE
==========
build_tr_trend_v1_arrays(npz, mode, is_long, cfg, spy_npz=None, spy_ts=None) ->
    dict of per-bar arrays needed by evaluate_*. Built ONCE per symbol+side,
    consumed in the bar loop with O(1) lookups.

PER-BAR EVAL
============
evaluate_tr_trend_v1_entry(arrays, bar_idx, tr_state, is_long, cfg) -> Optional[dict]
    Returns ENTRY (Path A) or ADD (Path B) or None.

evaluate_tr_trend_v1_exit(arrays, bar_idx, mark, pos_state, tr_state, is_long, cfg)
    -> Optional[dict]
    Returns close dict {reason} or None.

`tr_state` is a plain dict the engine maintains per symbol+side. It carries:
    last_path_a_bar_idx, last_path_a_pivot, last_path_a_vol,
    path_b_taken, entry_stop_price, max_close_since_entry,
    bars_since_entry, last_new_high_bar_idx
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np


# ────────────────────────────────────────────────────────────────────────────────
# D-bar boundary detection + per-D-bar series construction
# ────────────────────────────────────────────────────────────────────────────────

def _detect_d_boundaries(npz: Dict[str, np.ndarray]) -> np.ndarray:
    """Return bool mask (length=n) where True marks the last base-TF bar of a D bar.

    D-bar fields in our NPZ (close_D, volume_D, ...) are forward-filled — every
    base-TF bar within a D bar carries the same value. A 'D-boundary' is the
    bar AFTER which the value changes (i.e. the last bar of the prior D-bar).

    We mark the bar where the next bar has a different close_D as the boundary,
    plus the final bar (treat as last-of-its-day).
    """
    close_d = npz.get("close_D")
    if close_d is None or len(close_d) == 0:
        return np.zeros(0, dtype=bool)
    n = len(close_d)
    mask = np.zeros(n, dtype=bool)
    if n == 1:
        mask[0] = True
        return mask
    mask[:-1] = close_d[:-1] != close_d[1:]
    mask[-1] = True
    return mask


def _build_d_bar_series(npz: Dict[str, np.ndarray], boundary_mask: np.ndarray) -> Dict[str, np.ndarray]:
    """Extract one value per D bar from the forward-filled NPZ fields.

    Uses each D bar's LAST base-TF bar (the boundary) to sample close_D/high_D/
    low_D/open_D/volume_D. Returns dict of compact arrays indexed by D-bar index.
    Also returns the base-TF index of each boundary (for mapping back).
    """
    idx_at_boundaries = np.flatnonzero(boundary_mask)
    out: Dict[str, np.ndarray] = {"boundary_idx_in_base": idx_at_boundaries}
    for k in ("close_D", "high_D", "low_D", "open_D", "volume_D", "atr_D"):
        arr = npz.get(k)
        if arr is not None and len(arr) >= int(idx_at_boundaries[-1]) + 1 if len(idx_at_boundaries) else 0:
            out[k] = np.asarray(arr[idx_at_boundaries], dtype=np.float64)
    return out


def _rolling_sma(arr: np.ndarray, window: int) -> np.ndarray:
    """Trailing SMA. arr length N, returns length N — first window-1 entries
    are filled with the partial mean (so callers can still index without nan)."""
    if window <= 1 or len(arr) <= 1:
        return arr.astype(np.float64).copy()
    csum = np.cumsum(np.insert(arr.astype(np.float64), 0, 0.0))
    sma = np.empty(len(arr), dtype=np.float64)
    for i in range(len(arr)):
        w = min(i + 1, window)
        sma[i] = (csum[i + 1] - csum[i + 1 - w]) / w
    return sma


def _rolling_max(arr: np.ndarray, window: int) -> np.ndarray:
    """Trailing rolling max (length N). Partial-window for first window-1."""
    if window <= 1 or len(arr) <= 1:
        return arr.astype(np.float64).copy()
    out = np.empty(len(arr), dtype=np.float64)
    for i in range(len(arr)):
        w = min(i + 1, window)
        out[i] = float(np.max(arr[i + 1 - w : i + 1]))
    return out


def _rolling_min(arr: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(arr) <= 1:
        return arr.astype(np.float64).copy()
    out = np.empty(len(arr), dtype=np.float64)
    for i in range(len(arr)):
        w = min(i + 1, window)
        out[i] = float(np.min(arr[i + 1 - w : i + 1]))
    return out


def _sma_slope_positive(sma: np.ndarray, lookback: int) -> np.ndarray:
    """Bool array of length N — True where sma[i] > sma[i - lookback]."""
    out = np.zeros(len(sma), dtype=bool)
    if len(sma) <= lookback or lookback <= 0:
        return out
    out[lookback:] = sma[lookback:] > sma[:-lookback]
    return out


# ────────────────────────────────────────────────────────────────────────────────
# Per-symbol precompute — called once before the bar loop
# ────────────────────────────────────────────────────────────────────────────────

def build_tr_trend_v1_arrays(
    npz: Dict[str, np.ndarray],
    mode: str,
    is_long: bool,
    cfg: Any,
    spy_npz: Optional[Dict[str, np.ndarray]] = None,
    spy_ts: Optional[np.ndarray] = None,
    base_ts: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """Build all per-base-TF-bar arrays the strategy needs in the hot loop.

    Returns a dict consumed by evaluate_tr_trend_v1_entry / _exit. The dict has
    bool gates already AND-reduced (so the bar loop is one lookup per check).

    SPY regime is optional — if spy_npz is None or fields missing, regime mask
    defaults to True (skip-gate). Per spec §4.2 LONG requires SPY > SMA200 +
    slope > 0; SHORT requires the mirror.
    """
    out: Dict[str, Any] = {}
    close_base = npz.get("close")
    if close_base is None:
        # crypto NPZs have close as well — fallback to close_5m / close_3m
        close_base = npz.get("close_5m") if mode == "tradier" else npz.get("close_3m")
    if close_base is None:
        return out
    n_base = len(close_base)
    out["enabled"] = False  # flipped True if all preconditions met
    out["n_base"] = n_base

    # D-bar boundaries on the base-TF timeline
    boundary_mask = _detect_d_boundaries(npz)
    if len(boundary_mask) != n_base:
        return out
    d_series = _build_d_bar_series(npz, boundary_mask)
    boundary_idx = d_series.get("boundary_idx_in_base", np.zeros(0, dtype=np.int64))
    n_d = len(boundary_idx)
    if n_d < 60:  # need at least 60 D bars to compute trend template (52w high + 200d sma)
        return out

    close_d = d_series.get("close_D", np.zeros(n_d))
    high_d = d_series.get("high_D", close_d.copy())
    low_d = d_series.get("low_D", close_d.copy())
    volume_d = d_series.get("volume_D", np.zeros(n_d))
    atr_d = d_series.get("atr_D", np.zeros(n_d))

    dc_lookback = int(getattr(cfg, "TR_TREND_V1_DC_LOOKBACK_D", 20))
    vol_sma_len = int(getattr(cfg, "TR_TREND_V1_VOL_SMA_LEN_D", 50))
    sma50_len = int(getattr(cfg, "TR_TREND_V1_SMA50_LEN_D", 50))
    sma200_len = int(getattr(cfg, "TR_TREND_V1_SMA200_LEN_D", 200))
    sma200_slope_lb = int(getattr(cfg, "TR_TREND_V1_SMA200_SLOPE_LOOKBACK_D", 10))
    w52_bars = int(getattr(cfg, "TR_TREND_V1_W52_BARS_D", 252))
    near_high_pct = float(getattr(cfg, "TR_TREND_V1_TT_NEAR_HIGH_PCT", 25.0))
    tt_min_pass = int(getattr(cfg, "TR_TREND_V1_TT_MIN_PASS", 4))

    # Donchian-20 reference (PRIOR 20 D bars, excluding current — shift by 1)
    dc_high20_curr = _rolling_max(high_d, dc_lookback)
    dc_low20_curr = _rolling_min(low_d, dc_lookback)
    dc_high20_prev = np.zeros(n_d, dtype=np.float64)
    dc_low20_prev = np.full(n_d, np.inf, dtype=np.float64)
    dc_high20_prev[1:] = dc_high20_curr[:-1]
    dc_low20_prev[1:] = dc_low20_curr[:-1]

    # Volume SMA
    vol_sma50 = _rolling_sma(volume_d, vol_sma_len)
    # protect against zeros
    vol_ratio = np.where(vol_sma50 > 0, volume_d / np.maximum(vol_sma50, 1e-9), 0.0)
    vol_ok = vol_ratio >= float(getattr(cfg, "TR_TREND_V1_VOL_MULT", 1.5))

    # SMA50 / SMA200 on D close
    sma50_d = _rolling_sma(close_d, sma50_len)
    sma200_d = _rolling_sma(close_d, sma200_len)
    sma200_slope_up = _sma_slope_positive(sma200_d, sma200_slope_lb)

    # 52w high / low
    w52_high = _rolling_max(high_d, w52_bars)
    w52_low = _rolling_min(low_d, w52_bars)

    # Trend template (4-of-5; RS dropped per spec)
    if is_long:
        t1 = close_d > sma50_d
        t2 = sma50_d > sma200_d
        t3 = sma200_slope_up
        # within near_high_pct of 52w high (price >= w52_high * (1 - near_high/100))
        thr_hi = w52_high * (1.0 - near_high_pct / 100.0)
        t4 = close_d >= thr_hi
        tt_pass = (t1.astype(int) + t2.astype(int) + t3.astype(int) + t4.astype(int)) >= tt_min_pass
    else:
        t1 = close_d < sma50_d
        t2 = sma50_d < sma200_d
        t3 = ~sma200_slope_up
        thr_lo = w52_low * (1.0 + near_high_pct / 100.0)
        t4 = close_d <= thr_lo
        tt_pass = (t1.astype(int) + t2.astype(int) + t3.astype(int) + t4.astype(int)) >= tt_min_pass

    # Donchian breakout (close above prior 20-bar high)
    if is_long:
        dc_breakout = close_d > dc_high20_prev
    else:
        dc_breakout = close_d < dc_low20_prev

    # Path A entry mask (per D bar) — pre-SPY/regime gating; SPY mask applied below
    path_a_d = dc_breakout & vol_ok & tt_pass

    # SPY regime mask — defaults to all True if SPY data unavailable
    spy_regime_ok_d = np.ones(n_d, dtype=bool)
    spy_enabled = bool(getattr(cfg, "TR_TREND_V1_SPY_REGIME_ENABLED", True))
    if spy_enabled and spy_npz is not None and base_ts is not None:
        try:
            spy_close_d_base = spy_npz.get("close_D")
            spy_sma200_d_base = spy_npz.get("sma_200_D")
            if spy_close_d_base is not None and spy_sma200_d_base is not None and spy_ts is not None:
                # align SPY values to this symbol's base-TF timeline
                idx = np.searchsorted(spy_ts, base_ts, side="right") - 1
                idx = np.clip(idx, 0, len(spy_close_d_base) - 1)
                spy_close_aligned = spy_close_d_base[idx]
                spy_sma_aligned = spy_sma200_d_base[idx]
                # Subsample at each D boundary so per-D-bar gate matches close_d's calendar
                spy_close_at_d = spy_close_aligned[boundary_idx]
                spy_sma_at_d = spy_sma_aligned[boundary_idx]
                spy_above = spy_close_at_d > spy_sma_at_d
                # slope: positive when current spy_sma > spy_sma 10 D bars ago
                spy_slope_up = np.zeros(n_d, dtype=bool)
                lb = int(getattr(cfg, "TR_TREND_V1_SPY_SLOPE_LOOKBACK_D", 10))
                if n_d > lb:
                    spy_slope_up[lb:] = spy_sma_at_d[lb:] > spy_sma_at_d[:-lb]
                spy_regime_ok_d = (spy_above & spy_slope_up) if is_long else ((~spy_above) & (~spy_slope_up))
        except Exception:
            spy_regime_ok_d = np.ones(n_d, dtype=bool)

    path_a_d = path_a_d & spy_regime_ok_d

    # Now FAN every per-D-bar gate back onto the base-TF timeline so the per-bar
    # loop can do an O(1) lookup. Boundaries[k] is the base-TF index where D bar k
    # CLOSES; the entry/exit gate is allowed to fire only on that boundary bar.
    path_a_base = np.zeros(n_base, dtype=bool)
    breakout_pivot_base = np.zeros(n_base, dtype=np.float64)
    breakout_vol_base = np.zeros(n_base, dtype=np.float64)
    atr_d_at_close_base = np.zeros(n_base, dtype=np.float64)
    sma50_d_base = np.zeros(n_base, dtype=np.float64)
    sma200_d_base = np.zeros(n_base, dtype=np.float64)
    dc_high20_prev_base = np.zeros(n_base, dtype=np.float64)
    dc_low20_prev_base = np.zeros(n_base, dtype=np.float64)
    close_d_base = np.zeros(n_base, dtype=np.float64)
    vol_ratio_base = np.zeros(n_base, dtype=np.float64)
    volume_d_base = np.zeros(n_base, dtype=np.float64)

    # forward-fill D-bar values onto base TF (every base-TF bar gets the value
    # of the LAST D bar that has closed). We use the boundary_idx to mark the
    # END of each D bar, then forward-fill from there.
    last_d_idx = -1
    for k in range(n_d):
        end_idx = int(boundary_idx[k])
        # the D bar with index k closes at base index end_idx — for base index
        # start_d at end_idx (entry on the same close), we expose this D bar's
        # values FROM end_idx onward (until next D bar end).
        next_end = int(boundary_idx[k + 1]) if k + 1 < n_d else n_base
        # mark Path A fire only on the boundary bar (single base-TF bar)
        if path_a_d[k]:
            path_a_base[end_idx] = True
            breakout_pivot_base[end_idx] = close_d[k]
            breakout_vol_base[end_idx] = volume_d[k]
        # forward-fill DAY values from end_idx through next_end-1 (we use these
        # for exit checks at every bar within the next D bar)
        atr_d_at_close_base[end_idx:next_end] = atr_d[k]
        sma50_d_base[end_idx:next_end] = sma50_d[k]
        sma200_d_base[end_idx:next_end] = sma200_d[k]
        dc_high20_prev_base[end_idx:next_end] = dc_high20_prev[k]
        dc_low20_prev_base[end_idx:next_end] = dc_low20_prev[k]
        close_d_base[end_idx:next_end] = close_d[k]
        vol_ratio_base[end_idx:next_end] = vol_ratio[k]
        volume_d_base[end_idx:next_end] = volume_d[k]
        last_d_idx = k

    # also expose boundary_mask so engine can gate decisions to D-close bars only
    out["enabled"] = True
    out["boundary_mask"] = boundary_mask
    out["boundary_idx_in_base"] = boundary_idx
    out["path_a_d"] = path_a_d
    out["path_a_base"] = path_a_base
    out["breakout_pivot_base"] = breakout_pivot_base
    out["breakout_vol_base"] = breakout_vol_base
    out["atr_d_base"] = atr_d_at_close_base
    out["sma50_d_base"] = sma50_d_base
    out["sma200_d_base"] = sma200_d_base
    out["dc_high20_prev_base"] = dc_high20_prev_base
    out["dc_low20_prev_base"] = dc_low20_prev_base
    out["close_d_base"] = close_d_base
    out["volume_d_base"] = volume_d_base
    out["vol_ratio_base"] = vol_ratio_base
    # for Path B we may need to look up vol at the retest D bar — same vol_ratio
    return out


# ────────────────────────────────────────────────────────────────────────────────
# Per-bar entry evaluation
# ────────────────────────────────────────────────────────────────────────────────

def evaluate_tr_trend_v1_entry(
    arrays: Dict[str, Any],
    bar_idx: int,
    tr_state: Dict[str, Any],
    is_long: bool,
    cfg: Any,
    *,
    pos_open: bool,
    mark: float,
) -> Optional[Dict[str, Any]]:
    """Evaluate Path A (initial breakout) and Path B (retest add) at this base-TF bar.

    Decisions fire ONLY on D-close bars (boundary_mask[bar_idx] == True).
    Returns:
        {"action": "ENTRY", "path": "TR_TREND_PATH_A", "size_unit": 0.5,
         "pivot_price": float, "stop_price": float, "reason": str}
        or
        {"action": "ADD", "path": "TR_TREND_PATH_B", "size_unit": 0.5, "reason": str}
        or None.
    """
    if not arrays.get("enabled", False):
        return None
    bmask: np.ndarray = arrays["boundary_mask"]
    if bar_idx >= len(bmask) or not bool(bmask[bar_idx]):
        return None

    # Path A
    if not pos_open:
        path_a_base: np.ndarray = arrays["path_a_base"]
        if bool(path_a_base[bar_idx]):
            pivot = float(arrays["breakout_pivot_base"][bar_idx])
            vol_today = float(arrays["breakout_vol_base"][bar_idx])
            atr_d = float(arrays["atr_d_base"][bar_idx])
            atr_mult = float(getattr(cfg, "TR_TREND_V1_ATR_STOP_MULT", 2.0))
            stop_price = (pivot - atr_mult * atr_d) if is_long else (pivot + atr_mult * atr_d)
            vol_ratio = float(arrays["vol_ratio_base"][bar_idx])
            reason = f"TR_TREND_PATH_A_breakout_vol{vol_ratio:.2f}x_pivot{pivot:.2f}_stop{stop_price:.2f}"
            tr_state["last_path_a_bar_idx"] = bar_idx
            tr_state["last_path_a_pivot"] = pivot
            tr_state["last_path_a_vol"] = vol_today
            tr_state["path_b_taken"] = False
            tr_state["entry_stop_price"] = stop_price
            tr_state["max_close_since_entry"] = pivot
            tr_state["entry_bar_idx"] = bar_idx
            tr_state["last_new_high_bar_idx"] = bar_idx
            return {
                "action": "ENTRY",
                "path": "TR_TREND_PATH_A",
                "size_unit": 0.5,
                "pivot_price": pivot,
                "stop_price": stop_price,
                "reason": reason,
            }
        return None

    # Path B — already in a Path A position; add second half on retest
    if not tr_state.get("path_b_taken", False):
        last_a_idx = int(tr_state.get("last_path_a_bar_idx", -1))
        if last_a_idx < 0:
            return None
        # count D bars between last_a_idx and bar_idx
        boundary_idx = arrays["boundary_idx_in_base"]
        # how many D boundaries are strictly between (last_a_idx, bar_idx]?
        d_bars_passed = int(np.searchsorted(boundary_idx, bar_idx, side="right") -
                            np.searchsorted(boundary_idx, last_a_idx, side="right"))
        max_bars = int(getattr(cfg, "TR_TREND_V1_RETEST_MAX_BARS_D", 5))
        if d_bars_passed > max_bars:
            return None
        pivot = float(tr_state.get("last_path_a_pivot", 0.0))
        if pivot <= 0:
            return None
        tol_pct = float(getattr(cfg, "TR_TREND_V1_RETEST_TOL_PCT", 0.5))
        # Need: today retested pivot AND today's close above pivot (LONG) on lighter vol
        close_today = float(arrays["close_d_base"][bar_idx])
        vol_today = float(arrays["volume_d_base"][bar_idx])
        vol_a = float(tr_state.get("last_path_a_vol", 0.0))
        vol_max_mult = float(getattr(cfg, "TR_TREND_V1_RETEST_VOL_MAX_MULT", 0.7))
        low_d_today = float(arrays.get("low_d_base", np.zeros(1))[bar_idx]) if "low_d_base" in arrays else 0.0
        high_d_today = float(arrays.get("high_d_base", np.zeros(1))[bar_idx]) if "high_d_base" in arrays else 0.0
        # use base-TF mark as proxy for today's retest extreme (since we mostly
        # care about whether price went near pivot at SOME point during the day)
        # Use close vs pivot for the close-back-above check; mark for retest distance.
        retest_dist_pct = abs(mark - pivot) / pivot * 100.0 if pivot > 0 else 999.0
        if retest_dist_pct > tol_pct:
            return None
        if vol_a > 0 and vol_today > vol_a * vol_max_mult:
            return None
        # close back above pivot (LONG) / back below (SHORT)
        if is_long and close_today < pivot:
            return None
        if (not is_long) and close_today > pivot:
            return None
        tr_state["path_b_taken"] = True
        reason = (
            f"TR_TREND_PATH_B_retest_dist{retest_dist_pct:.2f}%_"
            f"volr{(vol_today / max(vol_a, 1e-9)):.2f}_pivot{pivot:.2f}"
        )
        return {
            "action": "ADD",
            "path": "TR_TREND_PATH_B",
            "size_unit": 0.5,
            "reason": reason,
        }
    return None


# ────────────────────────────────────────────────────────────────────────────────
# Per-bar exit evaluation
# ────────────────────────────────────────────────────────────────────────────────

def evaluate_tr_trend_v1_exit(
    arrays: Dict[str, Any],
    bar_idx: int,
    mark: float,
    tr_state: Dict[str, Any],
    is_long: bool,
    cfg: Any,
) -> Optional[Dict[str, Any]]:
    """Check three exit paths in order at D-close bars.

    Also updates chandelier trailing stop and bars-since-entry/new-high counters.
    Returns {"action": "CLOSE", "reason": str} or None.
    """
    if not arrays.get("enabled", False):
        return None
    bmask: np.ndarray = arrays["boundary_mask"]
    if bar_idx >= len(bmask):
        return None
    # Update chandelier max even on intra-day bars (so price spikes mid-day count)
    if is_long:
        if mark > float(tr_state.get("max_close_since_entry", 0.0)):
            tr_state["max_close_since_entry"] = mark
            tr_state["last_new_high_bar_idx"] = bar_idx
    else:
        cur_extreme = float(tr_state.get("max_close_since_entry", 0.0))
        if cur_extreme <= 0 or mark < cur_extreme:
            tr_state["max_close_since_entry"] = mark
            tr_state["last_new_high_bar_idx"] = bar_idx

    # Chandelier trail: after +1 ATR profit, ratchet stop to max_close - 2*ATR
    pivot = float(tr_state.get("last_path_a_pivot", 0.0))
    atr_d = float(arrays["atr_d_base"][bar_idx])
    atr_mult = float(getattr(cfg, "TR_TREND_V1_ATR_STOP_MULT", 2.0))
    if pivot > 0 and atr_d > 0:
        profit_in_atr = ((mark - pivot) / atr_d) if is_long else ((pivot - mark) / atr_d)
        if profit_in_atr >= 1.0:
            max_close = float(tr_state.get("max_close_since_entry", mark))
            trailed = (max_close - atr_mult * atr_d) if is_long else (max_close + atr_mult * atr_d)
            cur_stop = float(tr_state.get("entry_stop_price", 0.0))
            if is_long and trailed > cur_stop:
                tr_state["entry_stop_price"] = trailed
            elif (not is_long) and (cur_stop == 0 or trailed < cur_stop):
                tr_state["entry_stop_price"] = trailed

    # Path 1: stop hit (evaluated every bar, not just D-close — per spec it's safety-first)
    stop_price = float(tr_state.get("entry_stop_price", 0.0))
    if stop_price > 0:
        breached = (is_long and mark <= stop_price) or ((not is_long) and mark >= stop_price)
        if breached:
            return {"action": "CLOSE", "reason": f"TR_TREND_STOP_HIT_px{mark:.2f}_stop{stop_price:.2f}"}

    # The structural exits (50SMA break, DC reverse, time-stop) fire only at D close
    if not bool(bmask[bar_idx]):
        return None

    # Path 2: 50-SMA break on D close
    sma50 = float(arrays["sma50_d_base"][bar_idx])
    close_d = float(arrays["close_d_base"][bar_idx])
    if sma50 > 0:
        breached_sma = (is_long and close_d < sma50) or ((not is_long) and close_d > sma50)
        if breached_sma:
            return {"action": "CLOSE", "reason": f"TR_TREND_50SMA_BREAK_close{close_d:.2f}_sma50{sma50:.2f}"}

    # Path 3: Donchian-20 reverse breakout
    dc_h_prev = float(arrays["dc_high20_prev_base"][bar_idx])
    dc_l_prev = float(arrays["dc_low20_prev_base"][bar_idx])
    if is_long and dc_l_prev > 0 and close_d < dc_l_prev:
        return {"action": "CLOSE", "reason": f"TR_TREND_DC_REVERSE_close{close_d:.2f}_dclow{dc_l_prev:.2f}"}
    if (not is_long) and dc_h_prev > 0 and close_d > dc_h_prev:
        return {"action": "CLOSE", "reason": f"TR_TREND_DC_REVERSE_close{close_d:.2f}_dchigh{dc_h_prev:.2f}"}

    # 60-D-bar time-stop with no new high for last 30 D bars
    entry_idx = int(tr_state.get("entry_bar_idx", -1))
    last_new_high_idx = int(tr_state.get("last_new_high_bar_idx", entry_idx))
    if entry_idx >= 0:
        boundary_idx = arrays["boundary_idx_in_base"]
        d_bars_since_entry = int(np.searchsorted(boundary_idx, bar_idx, side="right") -
                                 np.searchsorted(boundary_idx, entry_idx, side="right"))
        d_bars_since_new_high = int(np.searchsorted(boundary_idx, bar_idx, side="right") -
                                    np.searchsorted(boundary_idx, last_new_high_idx, side="right"))
        time_stop_bars = int(getattr(cfg, "TR_TREND_V1_TIME_STOP_BARS_D", 60))
        no_high_bars = int(getattr(cfg, "TR_TREND_V1_TIME_STOP_NO_HIGH_BARS_D", 30))
        if d_bars_since_entry > time_stop_bars and d_bars_since_new_high > no_high_bars:
            return {
                "action": "CLOSE",
                "reason": f"TR_TREND_TIME_STOP_bars{d_bars_since_entry}_nohigh{d_bars_since_new_high}",
            }

    return None


# ────────────────────────────────────────────────────────────────────────────────
# Size helper — full unit = account_risk / (2 ATR * price * shares_per_$)
# ────────────────────────────────────────────────────────────────────────────────

def compute_tr_trend_v1_full_unit_qty(
    cfg: Any,
    atr_d: float,
    price: float,
) -> float:
    """Returns full-unit quantity (Path A = 0.5 * this; Path B adds another 0.5 *).

    full_unit_qty = (account * risk_pct/100) / (atr_mult * atr_d)
    """
    if atr_d <= 0 or price <= 0:
        return 0.0
    risk_pct = float(getattr(cfg, "TR_TREND_V1_RISK_PCT", 0.5))
    acct = float(getattr(cfg, "TR_TREND_V1_ACCOUNT_USD", 35000.0))
    atr_mult = float(getattr(cfg, "TR_TREND_V1_ATR_STOP_MULT", 2.0))
    risk_dollar = acct * (risk_pct / 100.0)
    risk_per_share = atr_mult * atr_d
    if risk_per_share <= 0:
        return 0.0
    qty = risk_dollar / risk_per_share
    return max(qty, 0.0)
