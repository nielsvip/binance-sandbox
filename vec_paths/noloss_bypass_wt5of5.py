"""
vec_paths/noloss_bypass_wt5of5.py — NOLOSS_BYPASS_WT_5OF5 exit path (vectorized).

LIVE SOURCE:
  tradier_manage.py (wired; not yet wired in ez_manage.py — per CLAUDE.md:
  "per-path edit needed" for crypto).
  Config: config.py / config_tradier.py

WHAT "ALL 5 TFS AGAINST" MEANS:
  Five WaveTrend timeframes are checked simultaneously: 3m, 15m, 1h, 4h, D.
  A TF is "against" a LONG position when wt1 < wt2 (bearish WT cross, meaning
  the fast WT line is below the slow WT line — momentum is to the downside).
  A TF is "against" a SHORT position when wt1 > wt2 (bullish WT cross).
  When ALL MIN_TFS (default 5, all five) are against the position at the same
  bar → bypass the UNIVERSAL_NOLOSS_GATE and close immediately.

  This is the most conservative possible WT alignment signal — if every TF
  from 3m to Daily is pointing the wrong way, staying in a loser helps nobody.

CONFIG KEYS (mirrored from config.py / config_tradier.py):
  NOLOSS_BYPASS_WT_5OF5_ENABLED: bool = False   (default OFF — live 0 events/30d)
  WT5OF5_MIN_TFS: int = 5                        (default 5 = true 5-of-5;
                                                   lower to 3/4 for testing)
  WT5OF5_REQUIRE_OPEN_LOSER: bool = True         (only fire when gain < 0,
                                                   matching the "bypass NOLOSS"
                                                   purpose — no point firing at
                                                   gain >= 0, NOLOSS doesn't block
                                                   then anyway)

NPZ FIELDS READ:
  wt1_3m, wt2_3m   — base LTF (crypto: 3m, tradier: 5m with 3m fallback)
  wt1_15m, wt2_15m
  wt1_1h,  wt2_1h
  wt1_4h,  wt2_4h
  wt1_D,   wt2_D

  Field naming follows the convention established in exit_r1_r2.py and
  wt_crossunder_final.py: "wt1_{tf}" / "wt2_{tf}" where tf ∈ {3m,15m,1h,4h,D}.

RETURN CONTRACT:
  check_wt5of5_exit() returns:
    dict: {
      "side":          str,    "LONG" or "SHORT"
      "reason":        str,    matches NOLOSS_BYPASS_WT_5OF5_BYPASS_NOLOSS_{N}of5 pattern
      "close":         True,
      "bypass_noloss": True,
      "n_tfs_against": int,   count of TFs actually against (always >= WT5OF5_MIN_TFS)
    }
    or None if conditions not met.

  check_wt5of5_exit_vec() returns:
    np.ndarray[bool] shape (N,) — True where position should be closed.
    True means ALL min_tfs arrays in wt1_arrays are < wt2_arrays for LONG
    (or > for SHORT).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

# TF order: innermost (fastest) to outermost (slowest).
# The scalar path checks in this order and counts how many are against.
_CRYPTO_TFS_ORDERED = ["3m", "15m", "1h", "4h", "D"]
_TRADIER_TFS_ORDERED = ["3m", "15m", "1h", "4h", "D"]  # same 5 per live spec


def check_wt5of5_exit(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[Dict[str, Any]]:
    """WT 5-of-5 TFs against position — bypass NOLOSS and close.

    Mirrors tradier_manage.py NOLOSS_BYPASS_WT_5OF5 block.
    Default OFF (NOLOSS_BYPASS_WT_5OF5_ENABLED=False in live).

    Args:
        store:     _NPZStore for this symbol (supports .f(key, idx) and .price(idx)).
        bar_idx:   Current bar index.
        pos_state: _PositionState (must be open=True).
        mode:      "crypto" or "tradier".
        cfg:       VecConfig with NOLOSS_BYPASS_WT_5OF5_* fields.

    Returns:
        dict if the bypass fires, None otherwise.
    """
    if not getattr(cfg, "NOLOSS_BYPASS_WT_5OF5_ENABLED", False):
        return None
    if pos_state is None or not pos_state.open:
        return None
    require_loser = getattr(cfg, "WT5OF5_REQUIRE_OPEN_LOSER", True)
    if require_loser and pos_state.gain_pct >= 0:
        return None
    min_tfs = int(getattr(cfg, "WT5OF5_MIN_TFS", 5))
    min_tfs = max(1, min(5, min_tfs))
    is_long = (pos_state.side == "LONG")
    tf_list = _TRADIER_TFS_ORDERED if mode == "tradier" else _CRYPTO_TFS_ORDERED
    n_against = 0
    for tf in tf_list:
        wt1 = store.f(f"wt1_{tf}", bar_idx, 0.0)
        wt2 = store.f(f"wt2_{tf}", bar_idx, 0.0)
        if wt1 == 0.0 and wt2 == 0.0:
            continue
        if is_long:
            if wt1 < wt2:
                n_against += 1
        else:
            if wt1 > wt2:
                n_against += 1
    if n_against < min_tfs:
        return None
    gain = pos_state.gain_pct
    side = pos_state.side
    reason = (
        f"NOLOSS_BYPASS_WT_5OF5_{n_against}of5_tfs_against"
        f"_{side}_g{gain:.3f}%"
    )
    return {
        "side": side,
        "reason": reason,
        "close": True,
        "bypass_noloss": True,
        "n_tfs_against": n_against,
    }


def check_wt5of5_exit_vec(
    wt1_arrays: List[np.ndarray],
    wt2_arrays: List[np.ndarray],
    pos_open_arr: np.ndarray,
    pos_side_arr: np.ndarray,
    mode: str,
    cfg: Any,
) -> np.ndarray:
    """Vectorized WT 5-of-5 exit: returns bool array, True where close should fire.

    NOTE ON NPZ FIELD NAMES:
      Caller should build wt1_arrays / wt2_arrays from NPZ fields in this order,
      matching _CRYPTO_TFS_ORDERED / _TRADIER_TFS_ORDERED above:
        wt1_arrays = [wt1_3m, wt1_15m, wt1_1h, wt1_4h, wt1_D]
        wt2_arrays = [wt2_3m, wt2_15m, wt2_1h, wt2_4h, wt2_D]
      Each element is a shape-(N,) float array from the NPZ store.
      See exit_r1_r2.py and wt_crossunder_final.py for how these fields are read.

    Args:
        wt1_arrays:    List of shape-(N,) arrays, one per TF (wt1_3m first, wt1_D last).
        wt2_arrays:    Matching list of shape-(N,) arrays (wt2_3m first, wt2_D last).
        pos_open_arr:  Shape-(N,) bool array — True where a position is open.
        pos_side_arr:  Shape-(N,) str or int array — "LONG"/"SHORT" or 1/-1.
                       Use 1 for LONG, -1 for SHORT if using int encoding.
        mode:          "crypto" or "tradier" (currently unused; kept for API parity).
        cfg:           VecConfig with NOLOSS_BYPASS_WT_5OF5_ENABLED, WT5OF5_MIN_TFS.

    Returns:
        np.ndarray bool shape (N,) — True where 5-of-5 bypass should fire.
        All-False if NOLOSS_BYPASS_WT_5OF5_ENABLED=False.

    VECTORIZATION NOTE:
      This function is O(N × n_tfs) numpy operations — no Python loops over bars.
      For a sweep over 48 symbols × 4 years × 8 bars/hour × 4yr =~1.4M bars, this
      runs in microseconds vs seconds for the scalar equivalent.
    """
    n = len(wt1_arrays[0]) if wt1_arrays else 0
    result = np.zeros(n, dtype=bool)
    if not getattr(cfg, "NOLOSS_BYPASS_WT_5OF5_ENABLED", False):
        return result
    if not wt1_arrays or len(wt1_arrays) != len(wt2_arrays):
        return result
    min_tfs = int(getattr(cfg, "WT5OF5_MIN_TFS", 5))
    min_tfs = max(1, min(len(wt1_arrays), min_tfs))
    is_long_arr = _resolve_side_bool(pos_side_arr, n)
    n_tfs = len(wt1_arrays)
    against_count = np.zeros(n, dtype=np.int32)
    for i in range(n_tfs):
        w1 = np.asarray(wt1_arrays[i], dtype=np.float64)
        w2 = np.asarray(wt2_arrays[i], dtype=np.float64)
        both_zero = (w1 == 0.0) & (w2 == 0.0)
        long_against = is_long_arr & (w1 < w2) & ~both_zero
        short_against = (~is_long_arr) & (w1 > w2) & ~both_zero
        against_count += (long_against | short_against).astype(np.int32)
    open_mask = np.asarray(pos_open_arr, dtype=bool)
    result = open_mask & (against_count >= min_tfs)
    return result


def _resolve_side_bool(pos_side_arr: Any, n: int) -> np.ndarray:
    """Return bool array: True = LONG, False = SHORT.

    Accepts string arrays ("LONG"/"SHORT") or int arrays (1/-1) or
    plain Python lists — whichever the caller provides.
    """
    arr = np.asarray(pos_side_arr)
    if arr.dtype.kind in ("U", "S", "O"):
        return arr == "LONG"
    return arr > 0
