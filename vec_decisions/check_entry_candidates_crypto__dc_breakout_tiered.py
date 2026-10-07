"""check_entry_candidates_crypto__dc_breakout_tiered.py

SHARED scalar+vectorized predicate for the LIVE DC_BREAKOUT entry block that runs
INSIDE check_entry_candidates_for_account (ez_positions_quick.py:15258-15330).

This is DISTINCT from vec_decisions/dc_break.py — that module replicates the
DC_BREAKOUT_ENTRY score-bonus inside AdvancedSignalRater.rate()
(ez_positions_quick.py:4296). The block reproduced HERE is the standalone tiered
DC4H/DC1H/DC15M/DC3M breakout entry path in check_entry_candidates, which:
  1. picks the highest broken DC tier (4h>1h>15m>3m) and a tier multiplier,
  2. (for low-TF DC15M/DC3M only) blocks if k_15m is on the extreme-wrong side,
  3. requires WT (3m OR 15m) momentum aligned with the side,
  4. fires should_trade + emits reason "DC_BREAKOUT_<TF>_x<mult>@<level>".

FAITHFUL EXTRACTION of ez_positions_quick.py:15280-15330 (LONG; SHORT mirror):

    _buf = 0.001
    if is_long:
        if   _dch4h>0 and price > _dch4h*(1+_buf):                 tf,mult="DC4H",3.0
        elif _dch1h>0 and price > _dch1h*(1+_buf):                 tf,mult="DC1H",2.0
        elif allow_15m and _dch15>0 and price > _dch15*(1+_buf):   tf,mult="DC15M",1.5
        elif allow_3m  and _dch3 >0 and price > _dch3 *(1+_buf):   tf,mult="DC3M",1.0
    else: (mirror with dc_low_* and price < lvl*(1-_buf))
    # tier picked → cooldown (live-only state, NOT in predicate)
    _dc_htf_break = tf in ("DC1H","DC4H")
    _dc_ob_cap = DC_BREAKOUT_OVERBOUGHT_K15M_CAP   (default 95.0)
    _stoch_wrong = (not _dc_htf_break) and ((is_long and k15>cap) or (short and k15<100-cap))
    _wt_ok = (is_long and (wt1_3m>wt2_3m or wt1_15m>wt2_15m)) or
             (short   and (wt1_3m<wt2_3m or wt1_15m<wt2_15m))
    fires iff tf is not None AND (not _stoch_wrong) AND _wt_ok

PURITY / SEAM NOTES:
  - The TIER SELECTION + stoch_wrong + wt_ok logic is a PURE per-bar predicate on
    NPZ-available fields (dc_high_*, dc_low_*, stoch_k_15m, wt1_3m, wt2_3m,
    wt1_15m, wt2_15m, current_price/close). Vectorized faithfully below.
  - The 900s per-position cooldown (_dc_breakout_entry_cd) is live-only sim state
    and is INTENTIONALLY excluded from the predicate (caller honors first-True /
    cooldown exactly like pyramid once-per-position). This is the documented
    stateful_seam: the engine takes the first True per position and applies its own
    cooldown, identical to how live applies the cooldown dict.
  - _force_fresh (45min indicator-age gate) is a data-freshness check, not a
    signal predicate; excluded.

The pure core below is the single source of truth shared by the scalar wrapper and
the numpy vec fn so the two paths CANNOT drift (mirrors strategy_enhancements.py
_pyramid_fires / check_pyramid_signal / check_pyramid_signal_vec).
"""
from typing import Tuple, Optional
import numpy as np

_BUF = 0.001


def _select_dc_tier(price: float, dch4h: float, dch1h: float, dch15: float,
                    dch3: float, dcl4h: float, dcl1h: float, dcl15: float,
                    dcl3: float, is_long: bool, allow_15m: bool,
                    allow_3m: bool) -> Tuple[Optional[str], float, float]:
    """Exact replica of the tier ladder (ez_positions_quick.py:15281-15290).
    Returns (tf_name_or_None, tier_mult, dc_level)."""
    if is_long:
        if dch4h > 0 and price > dch4h * (1.0 + _BUF):
            return "DC4H", 3.0, dch4h
        if dch1h > 0 and price > dch1h * (1.0 + _BUF):
            return "DC1H", 2.0, dch1h
        if allow_15m and dch15 > 0 and price > dch15 * (1.0 + _BUF):
            return "DC15M", 1.5, dch15
        if allow_3m and dch3 > 0 and price > dch3 * (1.0 + _BUF):
            return "DC3M", 1.0, dch3
        return None, 1.0, 0.0
    if dcl4h > 0 and price < dcl4h * (1.0 - _BUF):
        return "DC4H", 3.0, dcl4h
    if dcl1h > 0 and price < dcl1h * (1.0 - _BUF):
        return "DC1H", 2.0, dcl1h
    if allow_15m and dcl15 > 0 and price < dcl15 * (1.0 - _BUF):
        return "DC15M", 1.5, dcl15
    if allow_3m and dcl3 > 0 and price < dcl3 * (1.0 - _BUF):
        return "DC3M", 1.0, dcl3
    return None, 1.0, 0.0


def _dc_breakout_tiered_fires(price, dch4h, dch1h, dch15, dch3, dcl4h, dcl1h,
                              dcl15, dcl3, k15, wt1_3m, wt2_3m, wt1_15m, wt2_15m,
                              is_long, allow_15m, allow_3m, ob_cap) -> bool:
    """PURE per-bar fire test. Mirrors ez_positions_quick.py:15291-15330 minus the
    live-only 900s cooldown (applied by the caller / engine state seam)."""
    tf, _mult, _lvl = _select_dc_tier(price, dch4h, dch1h, dch15, dch3, dcl4h,
                                      dcl1h, dcl15, dcl3, is_long, allow_15m, allow_3m)
    if tf is None:
        return False
    dc_htf_break = tf in ("DC1H", "DC4H")
    stoch_wrong = (not dc_htf_break) and (
        (is_long and k15 > ob_cap) or ((not is_long) and k15 < (100.0 - ob_cap))
    )
    if stoch_wrong:
        return False
    wt_ok = (is_long and (wt1_3m > wt2_3m or wt1_15m > wt2_15m)) or (
        (not is_long) and (wt1_3m < wt2_3m or wt1_15m < wt2_15m)
    )
    return bool(wt_ok)


def _ob_cap(config) -> float:
    return float(getattr(config, "DC_BREAKOUT_OVERBOUGHT_K15M_CAP", 95.0))


def _allow_flags(config) -> Tuple[bool, bool]:
    return (bool(getattr(config, "DC_BREAKOUT_ALLOW_15M", False)),
            bool(getattr(config, "DC_BREAKOUT_ALLOW_3M", False)))


def check_dc_breakout_tiered(config, indicators: dict, current_price: float,
                             is_long: bool) -> Tuple[bool, float, str]:
    """LIVE/scalar path. Returns (fires, tier_mult, reason).

    Reads the SAME indicator fields as ez_positions_quick.py:15269-15317.
    Caller still owns the 900s cooldown + score assignment exactly as live does."""
    def g(k):
        v = indicators.get(k, 0.0)
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0
    allow_15m, allow_3m = _allow_flags(config)
    ob_cap = _ob_cap(config)
    k15 = indicators.get("stoch_k_15m")
    if k15 is None:
        k15 = indicators.get("k_15m_prev", 50)
    try:
        k15 = float(k15) if k15 is not None else 50.0
    except (TypeError, ValueError):
        k15 = 50.0
    tf, mult, lvl = _select_dc_tier(current_price, g("dc_high_4h"), g("dc_high_1h"),
                                    g("dc_high_15m"), g("dc_high_3m"), g("dc_low_4h"),
                                    g("dc_low_1h"), g("dc_low_15m"), g("dc_low_3m"),
                                    is_long, allow_15m, allow_3m)
    # 2026-10-06 NO-3M PARITY (NOTE_3M_REENABLE): NPZ has no 3m; while the switch is off the
    # WT confirm reads 15m in place of 3m — identical to the live fallback in ez_positions_quick.
    _no3m = not bool(getattr(config, "USE_1M_3M_SIGNALS_ENABLED", False))
    if _no3m:
        allow_3m = False
    _w1_3 = g("wt1_15m") if _no3m else g("wt1_3m")
    _w2_3 = g("wt2_15m") if _no3m else g("wt2_3m")
    fires = _dc_breakout_tiered_fires(current_price, g("dc_high_4h"), g("dc_high_1h"),
                                      g("dc_high_15m"), g("dc_high_3m"), g("dc_low_4h"),
                                      g("dc_low_1h"), g("dc_low_15m"), g("dc_low_3m"),
                                      k15, _w1_3, _w2_3, g("wt1_15m"),
                                      g("wt2_15m"), is_long, allow_15m, allow_3m, ob_cap)
    if not fires:
        return False, 1.0, ""
    return True, mult, f"DC_BREAKOUT_{tf}_x{mult}@{lvl:.8g}"


def check_dc_breakout_tiered_vec(config, price_arr, dch4h_arr, dch1h_arr, dch15_arr,
                                 dch3_arr, dcl4h_arr, dcl1h_arr, dcl15_arr, dcl3_arr,
                                 k15_arr, wt1_3m_arr, wt2_3m_arr, wt1_15m_arr,
                                 wt2_15m_arr, is_long, details=False):
    """VECTORIZED per-bar fire mask — backtest path. SAME thresholds + SAME tier
    ladder + SAME stoch_wrong/wt_ok logic as the scalar check_dc_breakout_tiered.
    Returns a bool ndarray (per bar), or (mask, tier, mult, level) dicts when
    details=True (tier = DC4H/DC1H/DC15M/DC3M/'' per bar for live-exact reasons).
    Caller takes first-True per position and applies the 900s cooldown (state seam),
    exactly as live."""
    allow_15m, allow_3m = _allow_flags(config)
    ob_cap = _ob_cap(config)
    # 2026-10-06 NO-3M PARITY (NOTE_3M_REENABLE): 3m leg reads 15m while the switch is off.
    _no3m = not bool(getattr(config, "USE_1M_3M_SIGNALS_ENABLED", False))
    if _no3m:
        wt1_3m_arr, wt2_3m_arr = wt1_15m_arr, wt2_15m_arr
        allow_3m = False
    p = np.asarray(price_arr, dtype=float)
    h4 = np.asarray(dch4h_arr, dtype=float)
    h1 = np.asarray(dch1h_arr, dtype=float)
    h15 = np.asarray(dch15_arr, dtype=float)
    h3 = np.asarray(dch3_arr, dtype=float)
    l4 = np.asarray(dcl4h_arr, dtype=float)
    l1 = np.asarray(dcl1h_arr, dtype=float)
    l15 = np.asarray(dcl15_arr, dtype=float)
    l3 = np.asarray(dcl3_arr, dtype=float)
    k15 = np.asarray(k15_arr, dtype=float)
    w1_3 = np.asarray(wt1_3m_arr, dtype=float)
    w2_3 = np.asarray(wt2_3m_arr, dtype=float)
    w1_15 = np.asarray(wt1_15m_arr, dtype=float)
    w2_15 = np.asarray(wt2_15m_arr, dtype=float)
    n = p.shape[0]
    # tier selection (priority 4h>1h>15m>3m). tier_htf True for DC4H/DC1H.
    if is_long:
        t4 = (h4 > 0) & (p > h4 * (1.0 + _BUF))
        t1 = (h1 > 0) & (p > h1 * (1.0 + _BUF))
        t15 = (h15 > 0) & (p > h15 * (1.0 + _BUF)) if allow_15m else np.zeros(n, bool)
        t3 = (h3 > 0) & (p > h3 * (1.0 + _BUF)) if allow_3m else np.zeros(n, bool)
    else:
        t4 = (l4 > 0) & (p < l4 * (1.0 - _BUF))
        t1 = (l1 > 0) & (p < l1 * (1.0 - _BUF))
        t15 = (l15 > 0) & (p < l15 * (1.0 - _BUF)) if allow_15m else np.zeros(n, bool)
        t3 = (l3 > 0) & (p < l3 * (1.0 - _BUF)) if allow_3m else np.zeros(n, bool)
    sel4 = t4
    sel1 = t1 & ~t4
    sel15 = t15 & ~t4 & ~t1
    sel3 = t3 & ~t4 & ~t1 & ~t15
    has_tier = sel4 | sel1 | sel15 | sel3
    is_htf = sel4 | sel1
    if is_long:
        stoch_wrong = (~is_htf) & (k15 > ob_cap)
        wt_ok = (w1_3 > w2_3) | (w1_15 > w2_15)
        lvl = np.where(sel4, h4, np.where(sel1, h1, np.where(sel15, h15, np.where(sel3, h3, 0.0))))
    else:
        stoch_wrong = (~is_htf) & (k15 < (100.0 - ob_cap))
        wt_ok = (w1_3 < w2_3) | (w1_15 < w2_15)
        lvl = np.where(sel4, l4, np.where(sel1, l1, np.where(sel15, l15, np.where(sel3, l3, 0.0))))
    mask = has_tier & (~stoch_wrong) & wt_ok
    if not details:
        return mask
    tier = np.where(sel4, "DC4H", np.where(sel1, "DC1H", np.where(sel15, "DC15M", np.where(sel3, "DC3M", ""))))
    mult = np.where(sel4, 3.0, np.where(sel1, 2.0, np.where(sel15, 1.5, np.where(sel3, 1.0, 1.0))))
    return {"mask": mask, "tier": tier, "mult": mult, "level": lvl}
