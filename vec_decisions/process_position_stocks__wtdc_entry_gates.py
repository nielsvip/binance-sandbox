"""PROCESS_POSITION_STOCKS · WT_DC_ENTRY gates — entry-veto predicates (shared scalar+vec).

LIVE SOURCE: tradier_manage.py process_position lines ~2909-2998 (WT_DC scorer gates).
These are the BLOCK flags that gate the WT_DC primary entry path. The function below
returns True when the entry is BLOCKED (any gate trips). Faithful replica:

  K5M block (lines 2909-2910):
    LONG  : stoch_k_5m > WT_DC_ENTRY_K5M_MAX_LONG   (default 100 → never blocks)
    SHORT : stoch_k_5m < WT_DC_ENTRY_K5M_MIN_SHORT  (default 0   → never blocks)

  HTF block (lines 2913-2929), gate = WT_DC_HTF_GATE (default 'none'):
    '1h'  : block if (LONG and wt1_1h<wt2_1h) or (SHORT and wt1_1h>wt2_1h)
    '4h'/'4h_d': block if (LONG and wt1_4h<wt2_4h) or (SHORT and wt1_4h>wt2_4h)
    '4h_d' additionally: if not already blocked, block if (LONG and wt1_D<wt2_D) or (SHORT and wt1_D>wt2_D)

  HTF_ALIGN block (lines 2940-2954), req = HTF_ALIGN_REQUIRED_TRADIER (default 0 → off):
    count of (1h,4h,D) where favourable wt cross; block if count < req.

  COMBINED_STOCH gate (lines 2955-2961), thr = COMBINED_STOCH_GATE_TRADIER (default 100 → off):
    if thr<100: LONG block if k5m>=thr ; SHORT block if k5m<=(100-thr).

  BAR_MATURITY block (lines 2982-2994), behind WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED
    (default False), thr=WT_DC_ENTRY_BAR_MATURITY_BLOCK (default 0.7, only active if 0<thr<1),
    needs open_D>0 AND atr_D>0:
      pos = |price-open_D|/atr_D ; bar_up = price>open_D ;
      same_dir = (LONG and bar_up) or (SHORT and not bar_up) ;
      block if pos>thr AND same_dir.

NOT INCLUDED (classified runtime_blocked / manual — see report): GR_HTF block (reads
wt_bull/bear_alignment which the live path reads from parse_market_data and is a derived
GR field; included separately is risky) and UVE block (calls _uve_entry_allowed, a stateful
per-symbol overlay). Those are layered by the caller.

2026-05-30 PARITY: live scalar (wtdc_entry_blocked) AND vectorized backtest
(wtdc_entry_blocked_vec) BOTH derive from the SAME pure predicate _wtdc_entry_blocked()
below — cannot drift.

CLASSIFICATION: vectorized. Pure per-bar predicates on NPZ fields stoch_k_5m, wt1/2_{1h,4h,D},
open_D, atr_D, close. No per-position state.

NPZ / indicator fields read: stoch_k_5m, wt1_1h, wt2_1h, wt1_4h, wt2_4h, wt1_D, wt2_D,
open_D, atr_D, close. All present in backtest NPZ.
"""
from typing import Tuple


def _wtdc_entry_blocked(current_price: float, stoch_k_5m: float, wt1_1h: float, wt2_1h: float,
                        wt1_4h: float, wt2_4h: float, wt1_D: float, wt2_D: float, open_D: float,
                        atr_D: float, is_long: bool, htf_gate: str, k5m_max_long: float,
                        k5m_min_short: float, htf_align_req: int, stoch_gate_thr: float,
                        bar_maturity_enabled: bool, bar_maturity_thr: float) -> Tuple[bool, str]:
    """PURE per-bar entry-block predicate. Returns (blocked, reason). Faithful replica of
    tradier_manage.py lines 2909-2994 (K5M, HTF, HTF_ALIGN, COMBINED_STOCH, BAR_MATURITY)."""
    if is_long:
        if stoch_k_5m > k5m_max_long:
            return True, "K5M_L"
    else:
        if stoch_k_5m < k5m_min_short:
            return True, "K5M_S"
    g = (htf_gate or "none").lower()
    if g == "1h":
        if (is_long and wt1_1h < wt2_1h) or ((not is_long) and wt1_1h > wt2_1h):
            return True, "HTF_1h"
    if g in ("4h", "4h_d"):
        if (is_long and wt1_4h < wt2_4h) or ((not is_long) and wt1_4h > wt2_4h):
            return True, "HTF_4h"
        if g == "4h_d":
            if (is_long and wt1_D < wt2_D) or ((not is_long) and wt1_D > wt2_D):
                return True, "HTF_D"
    if htf_align_req > 0:
        cnt = 0
        for w1, w2 in ((wt1_1h, wt2_1h), (wt1_4h, wt2_4h), (wt1_D, wt2_D)):
            if (is_long and w1 > w2) or ((not is_long) and w1 < w2):
                cnt += 1
        if cnt < htf_align_req:
            return True, "HTF_ALIGN"
    if stoch_gate_thr < 100.0:
        if is_long and stoch_k_5m >= stoch_gate_thr:
            return True, "STOCH_GATE_L"
        if (not is_long) and stoch_k_5m <= (100.0 - stoch_gate_thr):
            return True, "STOCH_GATE_S"
    if bar_maturity_enabled and 0.0 < bar_maturity_thr < 1.0 and open_D > 0 and atr_D > 0:
        pos = abs(current_price - open_D) / max(atr_D, 1e-6)
        bar_up = current_price > open_D
        same_dir = (is_long and bar_up) or ((not is_long) and (not bar_up))
        if pos > bar_maturity_thr and same_dir:
            return True, "BAR_MATURITY"
    return False, ""


def _wtdc_entry_gate_params(config):
    return (str(getattr(config, "WT_DC_HTF_GATE", "none")).lower(),
            float(getattr(config, "WT_DC_ENTRY_K5M_MAX_LONG", 100)),
            float(getattr(config, "WT_DC_ENTRY_K5M_MIN_SHORT", 0)),
            int(getattr(config, "HTF_ALIGN_REQUIRED_TRADIER", 0)),
            float(getattr(config, "COMBINED_STOCH_GATE_TRADIER", 100.0)),
            bool(getattr(config, "WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED", False)),
            float(getattr(config, "WT_DC_ENTRY_BAR_MATURITY_BLOCK", 0.7)))


def wtdc_entry_blocked(config, indicators: dict, current_price: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (blocked, reason). Block decision from the shared predicate.
    Live default reads mirror the .get(...) defaults in tradier_manage.py (k=50, wt=0, etc)."""
    gate, k5m_max, k5m_min, align_req, stoch_thr, bm_en, bm_thr = _wtdc_entry_gate_params(config)
    stoch_k_5m = float(indicators.get("stoch_k_5m", 50) or 50)
    wt1_1h = float(indicators.get("wt1_1h", 0) or 0)
    wt2_1h = float(indicators.get("wt2_1h", 0) or 0)
    wt1_4h = float(indicators.get("wt1_4h", 0) or 0)
    wt2_4h = float(indicators.get("wt2_4h", 0) or 0)
    wt1_D = float(indicators.get("wt1_D", 0) or 0)
    wt2_D = float(indicators.get("wt2_D", 0) or 0)
    open_D = float(indicators.get("open_D", 0) or 0)
    atr_D = float(indicators.get("atr_D", 0) or 0)
    return _wtdc_entry_blocked(current_price, stoch_k_5m, wt1_1h, wt2_1h, wt1_4h, wt2_4h, wt1_D,
                               wt2_D, open_D, atr_D, is_long, gate, k5m_max, k5m_min, align_req,
                               stoch_thr, bm_en, bm_thr)


def wtdc_entry_blocked_vec(config, current_price_arr, stoch_k_5m_arr, wt1_1h_arr, wt2_1h_arr,
                           wt1_4h_arr, wt2_4h_arr, wt1_D_arr, wt2_D_arr, open_D_arr, atr_D_arr,
                           is_long):
    """VECTORIZED per-bar BLOCK mask — backtest path. SAME params + SAME predicate as the live
    scalar wtdc_entry_blocked (vectorized via numpy). Returns a bool ndarray (True = blocked)."""
    import numpy as np
    p = np.asarray(current_price_arr, dtype=float)
    gate, k5m_max, k5m_min, align_req, stoch_thr, bm_en, bm_thr = _wtdc_entry_gate_params(config)
    k5 = np.asarray(stoch_k_5m_arr, dtype=float)
    w11h = np.asarray(wt1_1h_arr, dtype=float)
    w21h = np.asarray(wt2_1h_arr, dtype=float)
    w14h = np.asarray(wt1_4h_arr, dtype=float)
    w24h = np.asarray(wt2_4h_arr, dtype=float)
    w1D = np.asarray(wt1_D_arr, dtype=float)
    w2D = np.asarray(wt2_D_arr, dtype=float)
    oD = np.asarray(open_D_arr, dtype=float)
    aD = np.asarray(atr_D_arr, dtype=float)
    blocked = np.zeros(len(p), dtype=bool)
    if is_long:
        blocked |= k5 > k5m_max
    else:
        blocked |= k5 < k5m_min
    if gate == "1h":
        blocked |= (w11h < w21h) if is_long else (w11h > w21h)
    if gate in ("4h", "4h_d"):
        blocked |= (w14h < w24h) if is_long else (w14h > w24h)
        if gate == "4h_d":
            blocked |= (w1D < w2D) if is_long else (w1D > w2D)
    if align_req > 0:
        cnt = np.zeros(len(p), dtype=int)
        for w1, w2 in ((w11h, w21h), (w14h, w24h), (w1D, w2D)):
            cnt += ((w1 > w2) if is_long else (w1 < w2)).astype(int)
        blocked |= cnt < align_req
    if stoch_thr < 100.0:
        if is_long:
            blocked |= k5 >= stoch_thr
        else:
            blocked |= k5 <= (100.0 - stoch_thr)
    if bm_en and 0.0 < bm_thr < 1.0:
        aD_safe = np.where(aD > 1e-6, aD, 1e-6)
        pos = np.abs(p - oD) / aD_safe
        bar_up = p > oD
        same_dir = bar_up if is_long else ~bar_up
        valid = (oD > 0) & (aD > 0)
        blocked |= valid & (pos > bm_thr) & same_dir
    return blocked
