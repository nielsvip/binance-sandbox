"""LH_HL_FILTER (stocks entry) — block LONG on lower-highs, SHORT on higher-lows (1h/4h).

LIVE SOURCE: tradier_manage.py should_enter_long ~12019 / should_enter_short ~12296.
  Params: mode = LH_HL_FILTER_MODE ('STRICT_2BAR' default vs 'DC_REGRESS'),
          tf_req = LH_HL_FILTER_TF_REQ (2), dc_th = LH_HL_FILTER_DC_THRESHOLD_PCT/100 (0.005),
          req_both = LH_HL_FILTER_REQUIRE_BOTH (False).
  LONG (block when primary AND confirm):
    DC_REGRESS:  lh_<tf> = high_<tf>>0 AND dc_high_<tf>>0 AND high_<tf> < dc_high_<tf>*(1-dc_th)
    else:        lh_<tf> = high_<tf>>0 AND high_<tf>_prev>0 AND high_<tf> < high_<tf>_prev
    ll_<tf>    = low_<tf>>0 AND low_<tf>_prev>0 AND low_<tf> < low_<tf>_prev
    primary = (lh_1h+lh_4h) >= tf_req ; confirm = (not req_both) OR ((ll_1h+ll_4h) >= tf_req)
  SHORT (block when primary AND confirm):
    DC_REGRESS:  hl_<tf> = low_<tf>>0 AND dc_low_<tf>>0 AND low_<tf> > dc_low_<tf>*(1+dc_th)
    else:        hl_<tf> = low_<tf>>0 AND low_<tf>_prev>0 AND low_<tf> > low_<tf>_prev
    hh_<tf>    = high_<tf>>0 AND high_<tf>_prev>0 AND high_<tf> > high_<tf>_prev
    primary = (hl_1h+hl_4h) >= tf_req ; confirm = (not req_both) OR ((hh_1h+hh_4h) >= tf_req)

Returns True == BLOCKED. 2026-05-30 PARITY: shared pure predicate drives scalar+vec.
NPZ fields: high_{1h,4h}{,_prev}, low_{1h,4h}{,_prev}, dc_high_{1h,4h}, dc_low_{1h,4h} — all present.
"""
from typing import Tuple


def _lh_hl_blocks(h1h, h1hp, h4h, h4hp, l1h, l1hp, l4h, l4hp,
                  dch1h, dch4h, dcl1h, dcl4h,
                  is_long: bool, mode: str, tf_req: int, dc_th: float, req_both: bool) -> bool:
    """PURE per-bar LH_HL blocking predicate. Faithful replica of live both sides."""
    if is_long:
        if mode == "DC_REGRESS":
            p_1h = (h1h > 0 and dch1h > 0 and h1h < dch1h * (1.0 - dc_th))
            p_4h = (h4h > 0 and dch4h > 0 and h4h < dch4h * (1.0 - dc_th))
        else:
            p_1h = (h1h > 0 and h1hp > 0 and h1h < h1hp)
            p_4h = (h4h > 0 and h4hp > 0 and h4h < h4hp)
        c_1h = (l1h > 0 and l1hp > 0 and l1h < l1hp)
        c_4h = (l4h > 0 and l4hp > 0 and l4h < l4hp)
    else:
        if mode == "DC_REGRESS":
            p_1h = (l1h > 0 and dcl1h > 0 and l1h > dcl1h * (1.0 + dc_th))
            p_4h = (l4h > 0 and dcl4h > 0 and l4h > dcl4h * (1.0 + dc_th))
        else:
            p_1h = (l1h > 0 and l1hp > 0 and l1h > l1hp)
            p_4h = (l4h > 0 and l4hp > 0 and l4h > l4hp)
        c_1h = (h1h > 0 and h1hp > 0 and h1h > h1hp)
        c_4h = (h4h > 0 and h4hp > 0 and h4h > h4hp)
    primary_count = int(p_1h) + int(p_4h)
    confirm_count = int(c_1h) + int(c_4h)
    primary_hit = primary_count >= tf_req
    confirm_hit = (not req_both) or (confirm_count >= tf_req)
    return primary_hit and confirm_hit


def _lh_hl_params(config):
    return (str(getattr(config, "LH_HL_FILTER_MODE", "STRICT_2BAR")),
            int(getattr(config, "LH_HL_FILTER_TF_REQ", 2)),
            float(getattr(config, "LH_HL_FILTER_DC_THRESHOLD_PCT", 0.5)) / 100.0,
            bool(getattr(config, "LH_HL_FILTER_REQUIRE_BOTH", False)))


def check_lh_hl_filter(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (blocked, reason)."""
    if not bool(getattr(config, "LH_HL_FILTER_ENABLED", False)):
        return False, ""
    mode, tf_req, dc_th, req_both = _lh_hl_params(config)
    g = indicators.get
    h1h = float(g("high_1h", 0) or 0); h1hp = float(g("high_1h_prev", 0) or 0)
    h4h = float(g("high_4h", 0) or 0); h4hp = float(g("high_4h_prev", 0) or 0)
    l1h = float(g("low_1h", 0) or 0); l1hp = float(g("low_1h_prev", 0) or 0)
    l4h = float(g("low_4h", 0) or 0); l4hp = float(g("low_4h_prev", 0) or 0)
    dch1h = float(g("dc_high_1h", 0) or 0); dch4h = float(g("dc_high_4h", 0) or 0)
    dcl1h = float(g("dc_low_1h", 0) or 0); dcl4h = float(g("dc_low_4h", 0) or 0)
    if _lh_hl_blocks(h1h, h1hp, h4h, h4hp, l1h, l1hp, l4h, l4hp, dch1h, dch4h, dcl1h, dcl4h,
                     is_long, mode, tf_req, dc_th, req_both):
        return True, f"LH_HL_FILTER_BLOCK_{'LONG' if is_long else 'SHORT'}"
    return False, ""


def check_lh_hl_filter_vec(config, h1h, h1hp, h4h, h4hp, l1h, l1hp, l4h, l4hp,
                           dch1h, dch4h, dcl1h, dcl4h, is_long):
    """VECTORIZED blocking mask. SAME predicate as scalar. Returns bool ndarray (True==blocked)."""
    import numpy as np
    h1h = np.asarray(h1h, dtype=float); h1hp = np.asarray(h1hp, dtype=float)
    h4h = np.asarray(h4h, dtype=float); h4hp = np.asarray(h4hp, dtype=float)
    l1h = np.asarray(l1h, dtype=float); l1hp = np.asarray(l1hp, dtype=float)
    l4h = np.asarray(l4h, dtype=float); l4hp = np.asarray(l4hp, dtype=float)
    dch1h = np.asarray(dch1h, dtype=float); dch4h = np.asarray(dch4h, dtype=float)
    dcl1h = np.asarray(dcl1h, dtype=float); dcl4h = np.asarray(dcl4h, dtype=float)
    mode, tf_req, dc_th, req_both = _lh_hl_params(config)
    if is_long:
        if mode == "DC_REGRESS":
            p_1h = (h1h > 0) & (dch1h > 0) & (h1h < dch1h * (1.0 - dc_th))
            p_4h = (h4h > 0) & (dch4h > 0) & (h4h < dch4h * (1.0 - dc_th))
        else:
            p_1h = (h1h > 0) & (h1hp > 0) & (h1h < h1hp)
            p_4h = (h4h > 0) & (h4hp > 0) & (h4h < h4hp)
        c_1h = (l1h > 0) & (l1hp > 0) & (l1h < l1hp)
        c_4h = (l4h > 0) & (l4hp > 0) & (l4h < l4hp)
    else:
        if mode == "DC_REGRESS":
            p_1h = (l1h > 0) & (dcl1h > 0) & (l1h > dcl1h * (1.0 + dc_th))
            p_4h = (l4h > 0) & (dcl4h > 0) & (l4h > dcl4h * (1.0 + dc_th))
        else:
            p_1h = (l1h > 0) & (l1hp > 0) & (l1h > l1hp)
            p_4h = (l4h > 0) & (l4hp > 0) & (l4h > l4hp)
        c_1h = (h1h > 0) & (h1hp > 0) & (h1h > h1hp)
        c_4h = (h4h > 0) & (h4hp > 0) & (h4h > h4hp)
    primary_count = p_1h.astype(int) + p_4h.astype(int)
    confirm_count = c_1h.astype(int) + c_4h.astype(int)
    primary_hit = primary_count >= tf_req
    if req_both:
        confirm_hit = confirm_count >= tf_req
    else:
        confirm_hit = np.ones_like(primary_hit, dtype=bool)
    return primary_hit & confirm_hit
