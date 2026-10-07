"""HTF_W_M_ALIGN_GATE (stocks entry) — block entry unless N/2 (W,M) WaveTrend agree.

LIVE SOURCE: tradier_manage.py should_enter_long ~11865 / should_enter_short ~12149.
  LONG  block when agree < req where W_ok=(wt1_W>wt2_W if has else True), M_ok=(wt1_M>wt2_M if has else True)
  SHORT block when agree < req where W_ok=(wt1_W<wt2_W if has else True), M_ok=(wt1_M<wt2_M if has else True)
  has_W = (wt1_W!=0 or wt2_W!=0); has_M = (wt1_M!=0 or wt2_M!=0) — pre-cache bars pass through.
  req = HTF_W_M_ALIGN_TRADIER_REQUIRED (default 2).
Returns whether the gate BLOCKS the entry (True == blocked).

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires): live scalar
(check_htf_w_m_align_blocks) and the vec mask (check_htf_w_m_align_blocks_vec) both
derive from the same pure predicate _htf_w_m_align_blocks() — cannot drift.

NPZ fields: wt1_W, wt2_W, wt1_M, wt2_M (confirmed present in backtest NPZ).
"""
from typing import Tuple


def _htf_w_m_align_blocks(wt1_W: float, wt2_W: float, wt1_M: float, wt2_M: float,
                          is_long: bool, req: int) -> bool:
    """PURE per-bar blocking predicate. Faithful replica of tradier_manage.py."""
    w_has = (wt1_W != 0 or wt2_W != 0)
    m_has = (wt1_M != 0 or wt2_M != 0)
    if is_long:
        w_ok = (wt1_W > wt2_W) if w_has else True
        m_ok = (wt1_M > wt2_M) if m_has else True
    else:
        w_ok = (wt1_W < wt2_W) if w_has else True
        m_ok = (wt1_M < wt2_M) if m_has else True
    agree = int(w_ok) + int(m_ok)
    return agree < req


def _htf_w_m_align_req(config) -> int:
    return int(getattr(config, "HTF_W_M_ALIGN_TRADIER_REQUIRED", 2))


def check_htf_w_m_align_blocks(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (blocked, reason). Mirrors .get(...,0) defaults."""
    if not getattr(config, "HTF_W_M_ALIGN_GATE_TRADIER_ENABLED", False):
        return False, ""
    req = _htf_w_m_align_req(config)
    wt1_W = float(indicators.get("wt1_W", 0) or 0)
    wt2_W = float(indicators.get("wt2_W", 0) or 0)
    wt1_M = float(indicators.get("wt1_M", 0) or 0)
    wt2_M = float(indicators.get("wt2_M", 0) or 0)
    if _htf_w_m_align_blocks(wt1_W, wt2_W, wt1_M, wt2_M, is_long, req):
        return True, f"HTF_W_M_ALIGN_BLOCK req={req}"
    return False, ""


def check_htf_w_m_align_blocks_vec(config, wt1_W_arr, wt2_W_arr, wt1_M_arr, wt2_M_arr, is_long):
    """VECTORIZED blocking mask. SAME predicate as scalar. Returns bool ndarray (True==blocked)."""
    import numpy as np
    w1W = np.asarray(wt1_W_arr, dtype=float)
    w2W = np.asarray(wt2_W_arr, dtype=float)
    w1M = np.asarray(wt1_M_arr, dtype=float)
    w2M = np.asarray(wt2_M_arr, dtype=float)
    req = _htf_w_m_align_req(config)
    w_has = (w1W != 0) | (w2W != 0)
    m_has = (w1M != 0) | (w2M != 0)
    if is_long:
        w_ok = np.where(w_has, w1W > w2W, True)
        m_ok = np.where(m_has, w1M > w2M, True)
    else:
        w_ok = np.where(w_has, w1W < w2W, True)
        m_ok = np.where(m_has, w1M < w2M, True)
    agree = w_ok.astype(int) + m_ok.astype(int)
    return agree < req
