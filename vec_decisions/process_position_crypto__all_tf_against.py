"""SHARED scalar+vectorized predicate for the LIVE decision ALL_TF_AGAINST_CLOSE.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() lines ~39605-39636.

Counts how many of the 5 timeframes (3m/15m/1h/4h/D) have WT crossed against the
position, fires when count >= ALL_TF_AGAINST_CLOSE_MIN_TFS (default 5).

  LONG  against on a TF iff wt1_<TF> <  wt2_<TF>
  SHORT against on a TF iff wt1_<TF> >  wt2_<TF>
  count = sum over {3m,15m,1h,4h,D}
  fires iff count >= min_tfs

CLASSIFICATION: vectorized. Pure per-bar predicate on 10 NPZ WT fields. (The
live cooldown dict + the post-fire 'hedge becomes main' promotion are
side-effects/state, NOT part of the fire decision.)
"""
from typing import Tuple
import numpy as np

_TFS = ("3m", "15m", "1h", "4h", "D")


def _all_tf_against_count(wt1: dict, wt2: dict, is_long: bool) -> int:
    """Pure: number of TFs (of 5) with WT crossed against the position."""
    c = 0
    for tf in _TFS:
        a = wt1.get(tf, 0.0)
        b = wt2.get(tf, 0.0)
        if (is_long and a < b) or ((not is_long) and a > b):
            c += 1
    return c


def _all_tf_against_fires(wt1: dict, wt2: dict, is_long: bool, min_tfs: int) -> bool:
    return _all_tf_against_count(wt1, wt2, is_long) >= min_tfs


def _min_tfs(config) -> int:
    return int(getattr(config, "ALL_TF_AGAINST_CLOSE_MIN_TFS", 5))


def check_all_tf_against(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason)."""
    wt1 = {tf: float((indicators or {}).get(f"wt1_{tf}", 0) or 0) for tf in _TFS}
    wt2 = {tf: float((indicators or {}).get(f"wt2_{tf}", 0) or 0) for tf in _TFS}
    cnt = _all_tf_against_count(wt1, wt2, is_long)
    if cnt < _min_tfs(config):
        return False, ""
    return True, f"ALL_TF_AGAINST_CLOSE count={cnt}"


def check_all_tf_against_vec(config, wt1_arrs: dict, wt2_arrs: dict, is_long, min_tfs=None):
    """VECTORIZED per-bar fire mask. wt1_arrs/wt2_arrs map TF -> ndarray.
    SAME count-then-threshold logic as the scalar core."""
    _use = [t for t in _TFS if t in wt1_arrs and t in wt2_arrs]   # staged b1: 3m absent in vec -> ignored (only supplied TFs counted)
    n = len(np.asarray(wt1_arrs[_use[0]], dtype=float))
    cnt = np.zeros(n, dtype=int)
    for tf in _use:
        a = np.asarray(wt1_arrs[tf], dtype=float)
        b = np.asarray(wt2_arrs[tf], dtype=float)
        if is_long:
            cnt = cnt + (a < b).astype(int)
        else:
            cnt = cnt + (a > b).astype(int)
    return cnt >= (int(min_tfs) if min_tfs is not None else _min_tfs(config))
