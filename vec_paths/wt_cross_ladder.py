"""
vec_paths/wt_cross_ladder.py — graded "STACKED HIGHER-CROSS" entry-conviction ladder.

USER MANDATE 2026-06-03: in BOTH sizing AND entry-signal, for EACH timeframe in
{M,W,D,4h,1h,15m,3m} compare the CURRENT wt value AND the CURRENT price against the
wt value AND price recorded AT THAT TF's LAST WT CROSS. The MORE timeframes whose
current price+wt sit ABOVE (LONG) / BELOW (SHORT) their last-cross level, the BIGGER
the entry signal (rate() score) AND the bigger the quantity (execute_trade_action) —
so it is impossible to miss a bounce that has already confirmed across many TFs at a
higher level, even when normal filters/monitoring "missed the boat".

USER ADDENDUM 2026-06-03: (hidden) WaveTrend divergence on the HTFs (4h and above)
must ALSO have high impact on the traded amount.

Shared scalar+vec core (same pattern as vec_paths.funding_gate) so live, the vec sweep
(v8_vec_sweep) and the Tier-2 real-code engine cannot drift. Caller supplies, per TF:
  - current price + price AT the last cross
  - current wt1 + wt1 AT the last cross   (live: wt_cross_value_<tf>; NPZ: derived)
For the HTF divergence leg: wt_divergence_<tf> (BULL=1/HIDDEN_BULL=2/BEAR=-1/HIDDEN_BEAR=-2)
and wt_divergence_strength_<tf> (0..1).

All behaviour is config-gated and DEFAULT-OFF (WT_CROSS_LADDER_ENABLED defaults False),
so wiring this in is a byte-no-op until explicitly enabled.
"""
from __future__ import annotations
from typing import Dict, Optional, Tuple
import math
import numpy as np

DEFAULT_TFS = ("M", "W", "D", "4h", "1h", "15m", "3m")
DEFAULT_HTF_DIV_TFS = ("4h", "D", "W", "M")


def _csv(val, default):
    if val is None:
        return default
    if isinstance(val, (list, tuple)):
        return tuple(str(x).strip() for x in val if str(x).strip())
    return tuple(s.strip() for s in str(val).split(",") if s.strip())


def cfg_tfs(config) -> Tuple[str, ...]:
    return _csv(getattr(config, "WT_CROSS_LADDER_TFS", None), DEFAULT_TFS)


def cfg_htf_div_tfs(config) -> Tuple[str, ...]:
    return _csv(getattr(config, "WT_CROSS_LADDER_HTF_DIV_TFS", None), DEFAULT_HTF_DIV_TFS)


def _favorable(cur, ref, is_long: bool) -> bool:
    if cur is None or ref is None:
        return False
    try:
        c = float(cur)
        r = float(ref)
    except (TypeError, ValueError):
        return False
    if math.isnan(c) or math.isnan(r):
        return False
    return (c > r) if is_long else (c < r)


def _div_aligned(div_code, is_long: bool, include_hidden: bool) -> bool:
    try:
        d = int(div_code)
    except (TypeError, ValueError):
        return False
    if is_long:
        return d == 1 or (include_hidden and d == 2)
    return d == -1 or (include_hidden and d == -2)


def ladder_count_scalar(price_by_tf: Dict[str, float], cross_price_by_tf: Dict[str, float], wt_by_tf: Dict[str, float], cross_wt_by_tf: Dict[str, float], is_long: bool, config) -> Dict[str, int]:
    """Per-bar count across TFs. Returns dict with both_count / price_count / wt_count / count.
    `count` follows WT_CROSS_LADDER_REQUIRE_BOTH (default True → require price AND wt favorable)."""
    require_both = bool(getattr(config, "WT_CROSS_LADDER_REQUIRE_BOTH", True))
    both = price_c = wt_c = 0
    for tf in cfg_tfs(config):
        p_ok = _favorable(price_by_tf.get(tf), cross_price_by_tf.get(tf), is_long)
        w_ok = _favorable(wt_by_tf.get(tf), cross_wt_by_tf.get(tf), is_long)
        price_c += int(p_ok)
        wt_c += int(w_ok)
        both += int(p_ok and w_ok)
    count = both if require_both else max(price_c, wt_c)
    return {"count": count, "both_count": both, "price_count": price_c, "wt_count": wt_c}


def htf_div_scalar(div_by_tf: Dict[str, float], strength_by_tf: Dict[str, float], is_long: bool, config) -> Dict[str, float]:
    """HTF (4h+) divergence leg. Returns {div_count, div_strength}."""
    if not bool(getattr(config, "WT_CROSS_LADDER_HTF_DIV_ENABLED", True)):
        return {"div_count": 0, "div_strength": 0.0}
    include_hidden = bool(getattr(config, "WT_CROSS_LADDER_INCLUDE_HIDDEN", True))
    n = 0
    s = 0.0
    for tf in cfg_htf_div_tfs(config):
        if _div_aligned(div_by_tf.get(tf), is_long, include_hidden):
            n += 1
            try:
                s += float(strength_by_tf.get(tf) or 0.0)
            except (TypeError, ValueError):
                pass
    return {"div_count": n, "div_strength": s}


def score_boost(count: int, div_count: int, config) -> float:
    """Entry-signal (rate()) additive boost. Default knobs preserve behaviour only when
    WT_CROSS_LADDER_ENABLED is False (caller gates)."""
    per_tf = float(getattr(config, "WT_CROSS_LADDER_SCORE_PER_TF", 4.0))
    div_each = float(getattr(config, "WT_CROSS_LADDER_HTF_DIV_SCORE", 6.0))
    cap = float(getattr(config, "WT_CROSS_LADDER_MAX_SCORE_BOOST", 24.0))
    return float(min(count * per_tf + div_count * div_each, cap))


def size_mult(count: int, div_count: int, div_strength: float, config) -> float:
    """Quantity multiplier. 1.0 at count=0/no-div; grows with stacked TFs and HTF divergence."""
    per_tf = float(getattr(config, "WT_CROSS_LADDER_SIZE_PER_TF", 0.35))
    div_w = float(getattr(config, "WT_CROSS_LADDER_HTF_DIV_SIZE_WEIGHT", 0.5))
    cap = float(getattr(config, "WT_CROSS_LADDER_MAX_SIZE_MULT", 4.0))
    mult = 1.0 + count * per_tf + (div_count + div_strength) * div_w
    return float(max(1.0, min(mult, cap)))


# ----------------------------------------------------------------------------- vec (NPZ) path
def _carry_forward_at_cross(values: np.ndarray, cross_mask: np.ndarray) -> np.ndarray:
    """Per-bar array of `values` sampled at the most recent True in cross_mask, NaN before
    the first cross. No look-ahead (uses value at the cross bar itself)."""
    n = len(values)
    out = np.full(n, np.nan, dtype=float)
    last = np.nan
    for i in range(n):
        if cross_mask[i]:
            last = float(values[i])
        out[i] = last
    return out


def derive_cross_refs_vec(npz: dict, tf: str, is_long: bool) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Returns (cross_price_arr, cross_wt_arr) per base-bar for one TF, from NPZ fields.
    price = base `close` at the cross bar; wt = wt1_<tf> at the cross bar."""
    wt1 = npz.get(f"wt1_{tf}")
    cross = npz.get(f"wt_cross_bull_{tf}") if is_long else npz.get(f"wt_cross_bear_{tf}")
    close = npz.get("close")
    if wt1 is None or cross is None or close is None:
        return None, None
    wt1 = np.asarray(wt1, float)
    close = np.asarray(close, float)
    cmask = np.asarray(cross).astype(bool)
    cross_wt = _carry_forward_at_cross(wt1, cmask)
    cross_price = _carry_forward_at_cross(close, cmask)
    return cross_price, cross_wt


def _prev_cross_ref_vec(values: np.ndarray, cross_mask: np.ndarray) -> np.ndarray:
    """At each cross bar, the value at the IMMEDIATELY PREVIOUS cross (NaN if none / non-cross bar).
    This is the CORRECT 'value at previous cross' — the NPZ wt_cross_prev_value_<tf> field is
    mislabeled (it is just wt1 lagged-1), so derive from real cross bars instead."""
    n = len(values)
    out = np.full(n, np.nan, dtype=float)
    last = np.nan
    for i in range(n):
        if cross_mask[i]:
            out[i] = last
            last = float(values[i])
    return out


def fresh_higher_cross_vec(npz: dict, is_long: bool, base_tf: str, config) -> np.ndarray:
    """Per-bar mask: a FRESH WT cross on base_tf THIS bar whose wt1 AND (optionally) price are
    HIGHER (LONG)/LOWER (SHORT) than at the PREVIOUS cross of the same direction — the genuine
    'higher cross' the user means. Fixes the degenerate NPZ-field version. Price leg gated by
    WT_CROSS_LADDER_HIGHER_REQUIRE_PRICE (default True)."""
    wt1 = npz.get(f"wt1_{base_tf}")
    close = npz.get("close")
    cross = npz.get(f"wt_cross_bull_{base_tf}") if is_long else npz.get(f"wt_cross_bear_{base_tf}")
    if wt1 is None or close is None or cross is None:
        return np.zeros(0, dtype=bool)
    wt1 = np.asarray(wt1, float)
    close = np.asarray(close, float)
    cmask = np.asarray(cross).astype(bool)
    prev_wt = _prev_cross_ref_vec(wt1, cmask)
    require_price = bool(getattr(config, "WT_CROSS_LADDER_HIGHER_REQUIRE_PRICE", True))
    if is_long:
        wt_hi = np.isnan(prev_wt) | (wt1 > prev_wt)
        if require_price:
            prev_px = _prev_cross_ref_vec(close, cmask)
            px_hi = np.isnan(prev_px) | (close > prev_px)
        else:
            px_hi = np.ones(len(wt1), dtype=bool)
    else:
        wt_hi = np.isnan(prev_wt) | (wt1 < prev_wt)
        if require_price:
            prev_px = _prev_cross_ref_vec(close, cmask)
            px_hi = np.isnan(prev_px) | (close < prev_px)
        else:
            px_hi = np.ones(len(wt1), dtype=bool)
    return cmask & wt_hi & px_hi


def ladder_count_vec(npz: dict, is_long: bool, config) -> np.ndarray:
    """Per-bar int count of TFs whose current price+wt sit above (LONG)/below (SHORT) their
    last-cross level. require_both honoured."""
    require_both = bool(getattr(config, "WT_CROSS_LADDER_REQUIRE_BOTH", True))
    close = npz.get("close")
    if close is None:
        return np.zeros(0, dtype=int)
    close = np.asarray(close, float)
    n = len(close)
    both = np.zeros(n, dtype=int)
    price_c = np.zeros(n, dtype=int)
    wt_c = np.zeros(n, dtype=int)
    for tf in cfg_tfs(config):
        wt1 = npz.get(f"wt1_{tf}")
        if wt1 is None:
            continue
        wt1 = np.asarray(wt1, float)
        cprice, cwt = derive_cross_refs_vec(npz, tf, is_long)
        if cprice is None:
            continue
        if is_long:
            p_ok = (close > cprice) & ~np.isnan(cprice)
            w_ok = (wt1 > cwt) & ~np.isnan(cwt)
        else:
            p_ok = (close < cprice) & ~np.isnan(cprice)
            w_ok = (wt1 < cwt) & ~np.isnan(cwt)
        price_c += p_ok.astype(int)
        wt_c += w_ok.astype(int)
        both += (p_ok & w_ok).astype(int)
    return both if require_both else np.maximum(price_c, wt_c)


def htf_div_vec(npz: dict, is_long: bool, config) -> Tuple[np.ndarray, np.ndarray]:
    """Per-bar (div_count, div_strength) over HTF (4h+) divergence fields."""
    close = npz.get("close")
    n = 0 if close is None else len(np.asarray(close))
    div_count = np.zeros(n, dtype=int)
    div_strength = np.zeros(n, dtype=float)
    if not bool(getattr(config, "WT_CROSS_LADDER_HTF_DIV_ENABLED", True)) or n == 0:
        return div_count, div_strength
    include_hidden = bool(getattr(config, "WT_CROSS_LADDER_INCLUDE_HIDDEN", True))
    for tf in cfg_htf_div_tfs(config):
        dv = npz.get(f"wt_divergence_{tf}")
        if dv is None:
            continue
        dv = np.asarray(dv)
        st = npz.get(f"wt_divergence_strength_{tf}")
        st = np.zeros(n, dtype=float) if st is None else np.asarray(st, float)
        if is_long:
            aligned = (dv == 1) | (include_hidden & (dv == 2))
        else:
            aligned = (dv == -1) | (include_hidden & (dv == -2))
        div_count += aligned.astype(int)
        div_strength += np.where(aligned, st, 0.0)
    return div_count, div_strength


def size_mult_vec(npz: dict, is_long: bool, config) -> np.ndarray:
    """Per-bar quantity multiplier array (>=1.0)."""
    count = ladder_count_vec(npz, is_long, config)
    if len(count) == 0:
        return count.astype(float)
    dcount, dstr = htf_div_vec(npz, is_long, config)
    per_tf = float(getattr(config, "WT_CROSS_LADDER_SIZE_PER_TF", 0.35))
    div_w = float(getattr(config, "WT_CROSS_LADDER_HTF_DIV_SIZE_WEIGHT", 0.5))
    cap = float(getattr(config, "WT_CROSS_LADDER_MAX_SIZE_MULT", 4.0))
    mult = 1.0 + count * per_tf + (dcount + dstr) * div_w
    return np.clip(mult, 1.0, cap)
