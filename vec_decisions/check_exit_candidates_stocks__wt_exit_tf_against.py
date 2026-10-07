"""WT_EXIT_TF_AGAINST — count of WT timeframes against the position (shared scalar+vec).

Two closely-related live constructs both reduce to "count TFs where wt1 < wt2 (LONG) /
wt1 > wt2 (SHORT) and compare to a minimum":

LIVE SOURCE A: tradier_manage.py evaluate_stop lines ~6868-6877 — the multi-TF WT exit
  CONFIRMATION gate (`_exit_confirmed`). TFs = 5m,15m,1h,4h. Returns the against-count;
  caller uses `_exit_tf_against >= MIN_EXIT_TF_AGAINST_TRADIER (default 2)` to allow K5M /
  STRUCT_LH/HL_5M exits.
LIVE SOURCE B: tradier_manage.py evaluate_stop lines ~6560-6576 — WT_EXIT_TFS_VARFIX. TFs
  configurable (default 5m+15m+1h+4h+D). Fires an EXIT when against-count >= WT_EXIT_MIN_TFS.

Both share the identical per-TF against test (wt1<wt2 LONG / wt1>wt2 SHORT). This module
provides ONE pure counting core used by both, plus a fire wrapper for B.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): scalar and vec
share the SAME pure core _wt_against_count() / _wt_against_count_vec, so the two paths
CANNOT drift. Pure core: no config, no state.

NPZ / indicator fields read: wt1_<tf>, wt2_<tf> for each tf in the supplied tf list.
"""
from typing import List, Tuple


def _wt_against_count(wt_pairs, is_long: bool) -> int:
    """PURE: count of TFs whose WT is against the position. wt_pairs = list of (wt1, wt2)
    floats, one per TF. Faithful replica of the per-TF test in tradier_manage.py lines
    6570-6573 (varfix) and 6873-6875 (confirm gate)."""
    n = 0
    for wt1, wt2 in wt_pairs:
        if is_long and wt1 < wt2:
            n += 1
        elif (not is_long) and wt1 > wt2:
            n += 1
    return n


def _read_pairs(indicators: dict, tfs: List[str]):
    return [(float(indicators.get(f"wt1_{tf}", 0) or 0), float(indicators.get(f"wt2_{tf}", 0) or 0)) for tf in tfs]


def check_wt_exit_confirm_count(config, indicators: dict, is_long: bool) -> int:
    """LIVE/scalar SOURCE A — returns _exit_tf_against over the fixed 5m,15m,1h,4h set
    (tradier_manage.py lines 6868-6875). Caller compares to MIN_EXIT_TF_AGAINST_TRADIER."""
    return _wt_against_count(_read_pairs(indicators, ["5m", "15m", "1h", "4h"]), is_long)


def check_wt_exit_tfs_varfix(config, indicators: dict, gain_pct: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar SOURCE B — WT_EXIT_TFS_VARFIX exit. Faithful to lines 6560-6576.
    Gated by WT_EXIT_VETO_ENABLED_TRADIER (default False). TF list + min from config."""
    if not bool(getattr(config, "WT_EXIT_VETO_ENABLED_TRADIER", False)):
        return False, ""
    tfs_str = str(getattr(config, "WT_EXIT_TFS_TRADIER", "5m+15m+1h+4h+D"))
    min_tfs = int(getattr(config, "WT_EXIT_MIN_TFS_TRADIER", 4) or 4)
    tfs = [t.strip() for t in tfs_str.replace("+", ",").split(",") if t.strip()]
    against = _wt_against_count(_read_pairs(indicators, tfs), is_long)
    if min_tfs > 0 and against >= min_tfs and len(tfs) > 0:
        return True, f"WT_EXIT_TFS_VARFIX({against}/{len(tfs)}>={min_tfs})_g={gain_pct:.2f}%"
    return False, ""


def _wt_against_count_vec(wt1_arrs, wt2_arrs, is_long):
    """PURE VEC: per-bar against-count across TFs. wt1_arrs/wt2_arrs are lists of equal-length
    1-D arrays (one pair per TF). Returns an int ndarray of against-counts. SAME per-TF test
    as the scalar core."""
    import numpy as np
    total = None
    for w1, w2 in zip(wt1_arrs, wt2_arrs):
        a = np.asarray(w1, dtype=float)
        b = np.asarray(w2, dtype=float)
        m = (a < b) if is_long else (a > b)
        total = m.astype(int) if total is None else total + m.astype(int)
    return total


def check_wt_exit_confirm_count_vec(config, wt1_arrs, wt2_arrs, is_long):
    """VECTORIZED SOURCE A — per-bar _exit_tf_against count (int ndarray). Pass the 4 TF
    pairs (5m,15m,1h,4h). Caller compares to MIN_EXIT_TF_AGAINST_TRADIER."""
    return _wt_against_count_vec(wt1_arrs, wt2_arrs, is_long)


def check_wt_exit_tfs_varfix_vec(config, wt1_arrs, wt2_arrs, is_long):
    """VECTORIZED SOURCE B — per-bar WT_EXIT_TFS_VARFIX fire mask (bool ndarray). SAME counting
    core + SAME min as the scalar. wt1_arrs/wt2_arrs ordered to match WT_EXIT_TFS_TRADIER."""
    import numpy as np
    if not bool(getattr(config, "WT_EXIT_VETO_ENABLED_TRADIER", False)):
        n = len(np.asarray(wt1_arrs[0])) if wt1_arrs else 0
        return np.zeros(n, dtype=bool)
    min_tfs = int(getattr(config, "WT_EXIT_MIN_TFS_TRADIER", 4) or 4)
    counts = _wt_against_count_vec(wt1_arrs, wt2_arrs, is_long)
    n_tfs = len(wt1_arrs)
    if min_tfs <= 0 or n_tfs == 0:
        return np.zeros(len(counts), dtype=bool)
    return counts >= min_tfs
