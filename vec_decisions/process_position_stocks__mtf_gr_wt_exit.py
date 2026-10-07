"""PROCESS_POSITION_STOCKS · MTF_GR_WT_EXIT — GR-HTF-count + WT-cross exit (shared scalar+vec).

LIVE SOURCE: tradier_manage.py process_position lines ~2379-2395 (trigger #4 of the MTF
COMPOUND EXIT). This is the only one of the 4 MTF compound triggers that is a PURE per-bar
indicator predicate (the other 3 — ATR_TRAIL, DC_REJECT, BB_REJECT — carry per-position
ratchet/ever-outside/tag-bar STATE and are classified stateful_seam/manual, NOT here).

  wt_tf = MTF_WT_CROSS_EXIT_TF (default '1h')
  wt_against = (LONG and wt1_{tf}<wt2_{tf}) or (SHORT and wt1_{tf}>wt2_{tf})
  requires MTF_WT_CROSS_EXIT_ENABLED.
  gr_count = number of TFs in (1h,4h,D,W) where wt cross is against AND wt1!=0:
      LONG : wt1_g < wt2_g and wt1_g != 0
      SHORT: wt1_g > wt2_g and wt1_g != 0
  min_tfs = MTF_GR_EXIT_MIN_TFS (default 3)
  FIRE (CLOSE) when wt_against AND gr_count >= min_tfs.

2026-05-30 PARITY: live scalar (check_mtf_gr_wt_exit) AND vec twin
(check_mtf_gr_wt_exit_vec) derive from the SAME pure predicate _mtf_gr_wt_exit_fires() —
cannot drift.

CLASSIFICATION: vectorized. Pure per-bar predicate on NPZ fields wt1/2_{1h,4h,D,W}. No
per-position state. The position-held + MTF_EXIT_USE_COMPOUND + open-ts gates are layered
by the caller (state seam).

NPZ / indicator fields read: wt1_1h, wt2_1h, wt1_4h, wt2_4h, wt1_D, wt2_D, wt1_W, wt2_W
(plus the wt_tf-specific pair, which for the default tf '1h' is wt1_1h/wt2_1h). All present
in backtest NPZ. The wrappers take the wt_tf cross pair explicitly so the tf stays faithful.
"""
from typing import Tuple

_GR_TFS = ("1h", "4h", "D", "W")


def _mtf_gr_wt_exit_fires(wt1_tf: float, wt2_tf: float, gr_wt1, gr_wt2, is_long: bool,
                          min_tfs: int) -> bool:
    """PURE per-bar fire condition. gr_wt1/gr_wt2 are length-4 sequences for TFs (1h,4h,D,W).
    Faithful replica of tradier_manage.py lines 2382-2393."""
    if is_long:
        wt_against = wt1_tf < wt2_tf
    else:
        wt_against = wt1_tf > wt2_tf
    if not wt_against:
        return False
    cnt = 0
    for w1, w2 in zip(gr_wt1, gr_wt2):
        if is_long and w1 < w2 and w1 != 0:
            cnt += 1
        elif (not is_long) and w1 > w2 and w1 != 0:
            cnt += 1
    return cnt >= min_tfs


def _mtf_gr_wt_params(config):
    return (str(getattr(config, "MTF_WT_CROSS_EXIT_TF", "1h")),
            int(getattr(config, "MTF_GR_EXIT_MIN_TFS", 3)),
            bool(getattr(config, "MTF_WT_CROSS_EXIT_ENABLED", False)))


def check_mtf_gr_wt_exit(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason). Defaults mirror live safe_fetch_float(.,0)."""
    if not bool(getattr(config, "MTF_GR_EXIT_GATE_ENABLED", False)):
        return False, ""
    wt_tf, min_tfs, wt_cross_en = _mtf_gr_wt_params(config)
    if not wt_cross_en:
        return False, ""
    wt1_tf = float(indicators.get(f"wt1_{wt_tf}", 0) or 0)
    wt2_tf = float(indicators.get(f"wt2_{wt_tf}", 0) or 0)
    gr_wt1 = [float(indicators.get(f"wt1_{t}", 0) or 0) for t in _GR_TFS]
    gr_wt2 = [float(indicators.get(f"wt2_{t}", 0) or 0) for t in _GR_TFS]
    if not _mtf_gr_wt_exit_fires(wt1_tf, wt2_tf, gr_wt1, gr_wt2, is_long, min_tfs):
        return False, ""
    return True, f"MTF_GR_WT_EXIT_{wt_tf}"


def check_mtf_gr_wt_exit_vec(config, wt1_tf_arr, wt2_tf_arr, gr_wt1_arrs, gr_wt2_arrs, is_long):
    """VECTORIZED per-bar fire mask. gr_wt1_arrs / gr_wt2_arrs = length-4 lists of per-bar arrays
    for TFs (1h,4h,D,W). wt1_tf_arr/wt2_tf_arr = the MTF_WT_CROSS_EXIT_TF cross pair. SAME
    predicate as scalar. Returns a bool ndarray. Caller layers position-held + compound + ts gates."""
    import numpy as np
    w1 = np.asarray(wt1_tf_arr, dtype=float)
    if not bool(getattr(config, "MTF_GR_EXIT_GATE_ENABLED", False)):
        return np.zeros(len(w1), dtype=bool)
    _wt_tf, min_tfs, wt_cross_en = _mtf_gr_wt_params(config)
    if not wt_cross_en:
        return np.zeros(len(w1), dtype=bool)
    w2 = np.asarray(wt2_tf_arr, dtype=float)
    wt_against = (w1 < w2) if is_long else (w1 > w2)
    cnt = np.zeros(len(w1), dtype=int)
    for g1, g2 in zip(gr_wt1_arrs, gr_wt2_arrs):
        a1 = np.asarray(g1, dtype=float)
        a2 = np.asarray(g2, dtype=float)
        if is_long:
            cnt += ((a1 < a2) & (a1 != 0)).astype(int)
        else:
            cnt += ((a1 > a2) & (a1 != 0)).astype(int)
    return wt_against & (cnt >= min_tfs)
