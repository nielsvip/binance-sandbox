"""mtf_live_evaluator.py — LIVE-side evaluator for MTF protocol.

Provides scalar/dict-based equivalents of the vectorized logic in
vec_paths/mtf_armed_entries.py + vec_paths/gr_filter_vec.py. Called by
ez_manage.process_position and tradier_manage.process_position with the
current indicators dict, position state, and a per-symbol persistent
state dict (held on trade_manager).

API:
  update_armed_state(state, indicators, side, config) -> None
  gr_filter_pass(indicators, side, mode, config) -> bool
  evaluate_mtf_entry(state, indicators, mark, side, mode, config)
      -> (fire: bool, reason: str, size_mult: float)
  evaluate_mtf_exit(state, position, indicators, mark, side, mode, config)
      -> (fire: bool, reason: str)
  reset_state(state) -> None  # after a CLOSE

State dict layout (one per (sym, side), held on trade_manager.mtf_states):
  {
    "armed":               {TF_BAND: bool}   # e.g. {"1h_dc": True, "4h_bb": False, "D_wt": True}
    "prev_wt1":            {TF: float}       # for WT-direction suspension
    "prior_dc_high":       {TF: float}       # for armed-on detection prev-bar
    "prior_dc_low":        {TF: float}
    "prior_bb_upper":      {TF: float}
    "prior_bb_lower":      {TF: float}
    "prior_wt1":           {TF: float}
    "prior_wt2":           {TF: float}
    "prior_close":         {TF: float}
    "prior_bounce_price":  float             # HH-validation for BIG ADD
    "atr_trail":           float             # ratcheting trail level
    "ever_outside_dc":     bool              # for DC reject exit
    "bb_tag_history":      list              # for BB reject lookback (last N tag bools)
    "max_gain":            float             # for slowdown / peak gate
    "entry_price":         float             # cached for exit logic
    "big_added":           bool
    "last_close_ts":       float
  }

USER MANDATE 2026-05-19/20:
  - HEDGE OFF (this module never fires hedges)
  - NO_LOSS OFF (no min-gain-required exits; ATR trail is the floor)
  - 5 compound exit triggers: ATR trail OR (GR exit AND WT cross) OR DC reject OR BB reject

Per-sym overlay reads happen in the CALLER (ez_manage/tradier_manage) via
_psym_get(symbol, side, "MTF_ENTRY_BLOCKED"/"MTF_SIZE_MULT", ...).
This module assumes the caller has already done that filtering.
"""
from __future__ import annotations
from typing import Any, Dict, Tuple

# ── Helpers ──────────────────────────────────────────────────────────────────

def _f(d: dict, k: str, default: float = 0.0) -> float:
    v = d.get(k)
    if v is None: return default
    try: return float(v)
    except (TypeError, ValueError): return default


def _new_state() -> dict:
    return {
        "armed": {},
        "prev_wt1": {},
        "prior_dc_high": {}, "prior_dc_low": {},
        "prior_bb_upper": {}, "prior_bb_lower": {},
        "prior_wt1": {}, "prior_wt2": {},
        "prior_close": {},
        "prior_bounce_price": 0.0,
        "atr_trail": 0.0,
        "ever_outside_dc": False,
        "bb_tag_history": [],
        "max_gain": 0.0,
        "entry_price": 0.0,
        "big_added": False,
        "last_close_ts": 0.0,
    }


def reset_state(state: dict) -> None:
    """Reset after position CLOSE — preserves armed flags (they're per-symbol)."""
    state["prior_bounce_price"] = 0.0
    state["atr_trail"] = 0.0
    state["ever_outside_dc"] = False
    state["max_gain"] = 0.0
    state["entry_price"] = 0.0
    state["big_added"] = False


# ── Armed-state maintenance ─────────────────────────────────────────────────

def update_armed_state(state: dict, indicators: dict, side: str, config: Any) -> None:
    """Update per-(TF, bandtype) ARMED flags from current indicator bar.

    Call ONCE per bar update for each symbol (regardless of position open/closed).

    2026-05-23 USER MANDATE: read <field>_prev directly from indicators (added in
    ez_indicators.py for wt1/wt2/bb_upper/bb_lower; dc_high/dc_low_prev existed already).
    This means arming fires on FIRST call with no need to wait for state-persisted priors
    to accumulate. Fixes the chokepoint where 88.9% of fin entries blocked by MTF_NO_ARMED_STATE.
    State-persistence remains as fallback for indicator sources that don't expose _prev.
    """
    if not bool(getattr(config, "MTF_ARMED_ENTRY_ENABLED", False)):
        return
    is_long = (side == "LONG")
    tf_list = str(getattr(config, "MTF_ARMED_HTF_LIST", "1h,4h,D,W")).split(",")
    bandtypes = str(getattr(config, "MTF_ARMED_BANDTYPES", "dc,bb,wt")).split(",")
    armed = state.setdefault("armed", {})
    for tf in tf_list:
        tf = tf.strip()
        if not tf: continue
        c = _f(indicators, f"close_{tf}")
        if c <= 0: continue
        for bt in bandtypes:
            bt = bt.strip()
            key = f"{tf}_{bt}"
            if bt == "dc":
                # PREFER indicator _prev field, fall back to state-persisted prior
                _ind_dc_high_prev = _f(indicators, f"dc_high_{tf}_prev")
                _ind_dc_low_prev = _f(indicators, f"dc_low_{tf}_prev")
                if _ind_dc_high_prev > 0 and _ind_dc_low_prev > 0:
                    prev_up = _ind_dc_high_prev if is_long else _ind_dc_low_prev
                    prev_dn = _ind_dc_low_prev if is_long else _ind_dc_high_prev
                else:
                    prev_up = state["prior_dc_high"].get(tf, 0.0) if is_long else state["prior_dc_low"].get(tf, 0.0)
                    prev_dn = state["prior_dc_low"].get(tf, 0.0) if is_long else state["prior_dc_high"].get(tf, 0.0)
                cur_up = _f(indicators, f"dc_high_{tf}") if is_long else _f(indicators, f"dc_low_{tf}")
                cur_dn = _f(indicators, f"dc_low_{tf}") if is_long else _f(indicators, f"dc_high_{tf}")
                if is_long:
                    on = (prev_up > 0 and c > prev_up)
                    off = (prev_dn > 0 and c < prev_dn)
                else:
                    on = (prev_up > 0 and c < prev_up)
                    off = (prev_dn > 0 and c > prev_dn)
                state["prior_dc_high"][tf] = cur_up if is_long else state["prior_dc_high"].get(tf, cur_up)
                state["prior_dc_low"][tf]  = cur_dn if is_long else state["prior_dc_low"].get(tf, cur_dn)
                if not is_long:
                    state["prior_dc_low"][tf]  = cur_up
                    state["prior_dc_high"][tf] = cur_dn
            elif bt == "bb":
                # PREFER indicator _prev field
                _ind_bb_upper_prev = _f(indicators, f"bb_upper_{tf}_prev")
                _ind_bb_lower_prev = _f(indicators, f"bb_lower_{tf}_prev")
                if _ind_bb_upper_prev > 0 and _ind_bb_lower_prev > 0:
                    prev_up = _ind_bb_upper_prev if is_long else _ind_bb_lower_prev
                    prev_dn = _ind_bb_lower_prev if is_long else _ind_bb_upper_prev
                else:
                    prev_up = state["prior_bb_upper"].get(tf, 0.0) if is_long else state["prior_bb_lower"].get(tf, 0.0)
                    prev_dn = state["prior_bb_lower"].get(tf, 0.0) if is_long else state["prior_bb_upper"].get(tf, 0.0)
                cur_up = _f(indicators, f"bb_upper_{tf}") if is_long else _f(indicators, f"bb_lower_{tf}")
                cur_dn = _f(indicators, f"bb_lower_{tf}") if is_long else _f(indicators, f"bb_upper_{tf}")
                if is_long:
                    on = (prev_up > 0 and c > prev_up)
                    off = (prev_dn > 0 and c < prev_dn)
                else:
                    on = (prev_up > 0 and c < prev_up)
                    off = (prev_dn > 0 and c > prev_dn)
                state["prior_bb_upper"][tf] = cur_up if is_long else state["prior_bb_upper"].get(tf, cur_up)
                state["prior_bb_lower"][tf] = cur_dn if is_long else state["prior_bb_lower"].get(tf, cur_dn)
            elif bt == "wt":
                # 2026-05-22 USER MANDATE: directional arming (wt1>wt2 for LONG) replaces cross-only.
                # 2026-05-23 USER MANDATE: WT armed flag persists. Disarm only when wt1<wt2 (LONG)
                # AND wt1 fell below wt2 BETWEEN bars (true cross-down), not just current-bar
                # noise. Uses indicator wt1_<tf>_prev / wt2_<tf>_prev for first-call arming.
                wt1 = _f(indicators, f"wt1_{tf}")
                wt2 = _f(indicators, f"wt2_{tf}")
                _ind_wt1_prev = _f(indicators, f"wt1_{tf}_prev")
                _ind_wt2_prev = _f(indicators, f"wt2_{tf}_prev")
                if _ind_wt1_prev != 0 or _ind_wt2_prev != 0:
                    wt1_p = _ind_wt1_prev
                    wt2_p = _ind_wt2_prev
                else:
                    wt1_p = state["prior_wt1"].get(tf, wt1)
                    wt2_p = state["prior_wt2"].get(tf, wt2)
                if is_long:
                    on = (wt1 > wt2)
                    off = (wt1 < wt2 and wt1_p >= wt2_p)
                else:
                    on = (wt1 < wt2)
                    off = (wt1 > wt2 and wt1_p <= wt2_p)
                state["prior_wt1"][tf] = wt1
                state["prior_wt2"][tf] = wt2
            else:
                continue
            cur = armed.get(key, False)
            if on: cur = True
            if off: cur = False
            armed[key] = cur
        # WT direction tracking for suspend check
        wt1_now = _f(indicators, f"wt1_{tf}")
        state["prev_wt1"][tf] = state["prior_wt1"].get(tf, wt1_now)


def _is_expansion_regime(indicators: dict) -> bool:
    """USER MANDATE 2026-05-22: bypass WT-direction-suspend during expansion regime.
    Expansion = HTF dc_pos >= 0.85 AND ATR_1h > 1.5x its 20-bar median (or any of {1h,4h,D} dc_pos > 0.9).
    Rationale: NEAR +83% rally — WT spent days at extreme overbought, wt1 flattened/fell while price climbed.
    Suspending on "wt1 not rising this bar" blocked entries through the entire parabolic.
    During expansion, dc/bb arming is the meaningful signal; WT direction is noise.
    """
    dc_pos_1h = _f(indicators, "dc_pos_1h")
    dc_pos_4h = _f(indicators, "dc_pos_4h")
    dc_pos_d  = _f(indicators, "dc_pos_D")
    # ANY of {1h,4h,D} at dc_pos>=0.9 = expansion (LONG side); <=0.10 = expansion (SHORT side)
    if dc_pos_1h >= 0.90 or dc_pos_4h >= 0.90 or dc_pos_d >= 0.90: return True
    if 0 < dc_pos_1h <= 0.10 or 0 < dc_pos_4h <= 0.10 or 0 < dc_pos_d <= 0.10: return True
    # Composite: 1h dc_pos elevated AND atr expanding
    atr_1h = _f(indicators, "atr_1h"); atr_1h_med = _f(indicators, "atr_1h_med_20") or _f(indicators, "atr_1h_sma_20")
    if dc_pos_1h >= 0.85 and atr_1h_med > 0 and atr_1h > 1.5 * atr_1h_med: return True
    return False


def _armed_any_effective(state: dict, indicators: dict, side: str, config: Any) -> bool:
    """Return True if any armed flag is currently True AND WT-direction is favorable on that TF.

    2026-05-22 USER MANDATE: bypass WT direction-suspend during expansion regime
    (dc_pos at HTF extreme). Suspend logic was designed for mean-reverting chop, not parabolic
    breakouts where price is at top of range BECAUSE of expansion (not despite of it).
    """
    if not state.get("armed"): return False
    suspend_on = bool(getattr(config, "MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED", True))
    # NEW: bypass suspend entirely when expansion regime AND user opted into the bypass
    if suspend_on and bool(getattr(config, "MTF_ARMED_WT_DIRECTION_SUSPEND_BREAKOUT_BYPASS", True)):
        if _is_expansion_regime(indicators):
            suspend_on = False
    is_long = (side == "LONG")
    for key, is_armed in state["armed"].items():
        if not is_armed: continue
        if not suspend_on: return True
        tf = key.split("_")[0]
        wt1 = _f(indicators, f"wt1_{tf}")
        wt1_p = state["prev_wt1"].get(tf, wt1)
        if is_long and wt1 > wt1_p: return True
        if (not is_long) and wt1 < wt1_p: return True
    return False


# ── GR filter ───────────────────────────────────────────────────────────────

def _tf_score(indicators: dict, tf: str, is_long: bool, invert_dc_bb: bool) -> int:
    """Count bullish (LONG) or bearish (SHORT) indicators on a single TF — mirrors gr_filter_vec."""
    score = 0
    wt1 = _f(indicators, f"wt1_{tf}"); wt2 = _f(indicators, f"wt2_{tf}")
    if wt1 != 0 or wt2 != 0:
        if (is_long and wt1 > wt2) or ((not is_long) and wt1 < wt2): score += 1
    rsi = _f(indicators, f"rsi_{tf}")
    if rsi > 0 and ((is_long and rsi > 50) or ((not is_long) and rsi < 50)): score += 1
    mfi = _f(indicators, f"mfi_{tf}")
    if mfi > 0 and ((is_long and mfi > 50) or ((not is_long) and mfi < 50)): score += 1
    dc_pct = _f(indicators, f"dc_pct_{tf}")
    if dc_pct > 0:
        if invert_dc_bb:
            if (is_long and dc_pct >= 0.65) or ((not is_long) and dc_pct <= 0.35): score += 1
        else:
            if (is_long and dc_pct < 0.65) or ((not is_long) and dc_pct > 0.35): score += 1
    bb_pb = _f(indicators, f"bb_pct_b_{tf}")
    if bb_pb > 0:
        if invert_dc_bb:
            if (is_long and bb_pb >= 0.75) or ((not is_long) and bb_pb <= 0.25): score += 1
        else:
            if (is_long and bb_pb < 0.75) or ((not is_long) and bb_pb > 0.25): score += 1
    rvol = _f(indicators, f"relative_volume_{tf}")
    if rvol > 1.0: score += 1
    k = _f(indicators, f"k_{tf}") or _f(indicators, f"stoch_k_{tf}")
    if k > 0 and ((is_long and k < 80) or ((not is_long) and k > 20)): score += 1
    adx = _f(indicators, f"adx_{tf}")
    if adx > 20: score += 1
    macd_h = _f(indicators, f"macd_hist_{tf}")
    if (is_long and macd_h > 0) or ((not is_long) and macd_h < 0): score += 1
    ha = _f(indicators, f"ha_color_{tf}")
    if (is_long and ha > 0) or ((not is_long) and ha < 0): score += 1
    d = _f(indicators, f"d_{tf}") or _f(indicators, f"stoch_d_{tf}")
    if k > 0 and d > 0 and ((is_long and k > d) or ((not is_long) and k < d)): score += 1
    return score


def gr_filter_pass(indicators: dict, side: str, mode: str, config: Any,
                   min_tfs: int = None, min_ind: int = None,
                   invert_dc_bb: bool = None) -> bool:
    """Returns True if MIN_TFS of 6 TFs each have MIN_IND bullish/bearish indicators."""
    if not bool(getattr(config, "MTF_GR_FILTER_ENABLED", True)):
        return True
    if min_tfs is None:
        min_tfs = int(getattr(config, "MTF_GR_MIN_TFS", getattr(config, "GOLDEN_RULE_HTF_MIN_TFS", 3)))
    if min_ind is None:
        min_ind = int(getattr(config, "MTF_GR_MIN_IND", getattr(config, "GOLDEN_RULE_MIN_IND", 5)))
    if invert_dc_bb is None:
        invert_dc_bb = bool(getattr(config, "MTF_GR_INVERT_DC_BB", False))
    is_long = (side == "LONG")
    tfs = ["3m", "15m", "1h", "4h", "D", "W"] if mode == "crypto" else ["5m", "15m", "1h", "4h", "D", "W"]
    confirmed = 0
    for tf in tfs:
        if _tf_score(indicators, tf, is_long, invert_dc_bb) >= min_ind:
            confirmed += 1
    return confirmed >= min_tfs


# ── Entry triggers ──────────────────────────────────────────────────────────

def _wt_in_bb_fires(indicators: dict, side: str, config: Any) -> bool:
    """Rule (A): WT cross within bb on configured TF."""
    if not bool(getattr(config, "MTF_TRIGGER_WT_IN_BB_ENABLED", False)):
        return False
    tf = str(getattr(config, "MTF_TRIGGER_WT_TF", "1h"))
    wt1 = _f(indicators, f"wt1_{tf}"); wt2 = _f(indicators, f"wt2_{tf}")
    # Need prior bar's wt — caller maintains via update_armed_state. Use prior_wt1/2 from state? Hmm.
    # For LIVE: simplest proxy = wt1 vs wt2 NOW and was-opposite recently. Use current state of armed_wt for TF as proxy of "just crossed".
    # Better: caller tracks prior wt1/wt2; here we treat "cross" as direction-correct WT (with armed_wt[TF] handling the just-crossed semantics).
    is_long = (side == "LONG")
    if is_long and wt1 <= wt2: return False
    if (not is_long) and wt1 >= wt2: return False
    # Within BB
    price = _f(indicators, "current_price") or _f(indicators, f"close_{tf}")
    bbu = _f(indicators, f"bb_upper_{tf}"); bbl = _f(indicators, f"bb_lower_{tf}")
    if bbu <= 0 or bbl <= 0 or price <= 0: return False
    return bbl < price < bbu


def _direct_1h_breakout(indicators: dict, side: str, config: Any, state: dict) -> bool:
    """Rule (B): 1h close > prev_dc_high_1h (LONG)."""
    if not bool(getattr(config, "MTF_TRIGGER_1H_DIRECT_ENABLED", False)):
        return False
    bt = str(getattr(config, "MTF_TRIGGER_1H_DIRECT_BANDTYPE", "dc"))
    is_long = (side == "LONG")
    c = _f(indicators, "close_1h")
    if c <= 0: return False
    if bt == "dc":
        prev = state.get("prior_dc_high", {}).get("1h", 0.0) if is_long else state.get("prior_dc_low", {}).get("1h", 0.0)
    else:
        prev = state.get("prior_bb_upper", {}).get("1h", 0.0) if is_long else state.get("prior_bb_lower", {}).get("1h", 0.0)
    if prev <= 0: return False
    return (c > prev) if is_long else (c < prev)


def evaluate_mtf_entry(state: dict, indicators: dict, mark: float, side: str,
                       mode: str, config: Any) -> Tuple[bool, str, float]:
    """Returns (fire, reason, size_mult).

    size_mult = MTF_SMALL_SIZE_FRAC (default 0.25). Caller multiplies by normal qty.
    BIG ADD is a SEPARATE call (evaluate_mtf_big_add) when position is open.
    """
    if not bool(getattr(config, "MTF_ARMED_ENTRY_ENABLED", False)):
        return False, "", 0.0
    if bool(getattr(config, "MTF_REQUIRE_ARMED_ANY", True)):
        if not _armed_any_effective(state, indicators, side, config):
            return False, "", 0.0
    if bool(getattr(config, "MTF_ENTRY_REQUIRE_GR_FILTER", True)):
        if not gr_filter_pass(indicators, side, mode, config):
            return False, "", 0.0
    size_mult = float(getattr(config, "MTF_SMALL_SIZE_FRAC", 0.25))
    if _wt_in_bb_fires(indicators, side, config):
        return True, f"MTF_SMALL_WT_BB_{getattr(config, 'MTF_TRIGGER_WT_TF', '1h')}", size_mult
    if _direct_1h_breakout(indicators, side, config, state):
        return True, f"MTF_SMALL_1H_{getattr(config, 'MTF_TRIGGER_1H_DIRECT_BANDTYPE', 'dc')}", size_mult
    return False, "", 0.0


# ── Exit logic (compound) ───────────────────────────────────────────────────

def _atr_trail_check(state: dict, mark: float, entry_price: float,
                     atr_tf: str, atr_mult: float, is_long: bool, indicators: dict) -> Tuple[bool, str, float]:
    """ATR trailing stop. Returns (fire, reason, new_trail_level)."""
    atr_v = _f(indicators, f"atr_{atr_tf}") or _f(indicators, f"atr_14_{atr_tf}")
    if atr_v <= 0 or entry_price <= 0:
        return False, "", state.get("atr_trail", 0.0)
    prev_trail = state.get("atr_trail", 0.0)
    if is_long:
        candidate = max(entry_price - atr_mult * atr_v, mark - atr_mult * atr_v)
        new_trail = max(prev_trail, candidate) if prev_trail > 0 else candidate
        if new_trail > 0 and mark < new_trail:
            return True, f"MTF_ATR_TRAIL_{atr_tf}_x{atr_mult}_lvl{new_trail:.4f}", new_trail
    else:
        candidate = min(entry_price + atr_mult * atr_v, mark + atr_mult * atr_v)
        new_trail = min(prev_trail, candidate) if prev_trail > 0 else candidate
        if new_trail > 0 and mark > new_trail:
            return True, f"MTF_ATR_TRAIL_{atr_tf}_x{atr_mult}_lvl{new_trail:.4f}", new_trail
    return False, "", new_trail


def _dc_reject_check(state: dict, mark: float, side: str, indicators: dict, tf: str) -> Tuple[bool, str, bool]:
    """DC reject: price was outside dc_high/low_TF and re-crossed back."""
    is_long = (side == "LONG")
    new_ever = state.get("ever_outside_dc", False)
    if is_long:
        band = _f(indicators, f"dc_high_{tf}")
        if band > 0:
            if mark > band:
                new_ever = True
            elif new_ever and mark < band:
                return True, f"MTF_DC_REJECT_{tf}_px{mark:.4f}", new_ever
    else:
        band = _f(indicators, f"dc_low_{tf}")
        if band > 0:
            if mark < band:
                new_ever = True
            elif new_ever and mark > band:
                return True, f"MTF_DC_REJECT_{tf}_px{mark:.4f}", new_ever
    return False, "", new_ever


def _bb_reject_check(state: dict, indicators: dict, side: str, tf: str, lookback: int) -> bool:
    """BB reject: recent N TF-bars had bb_upper tag AND current bar fails to reach.

    State tracks last 'lookback' tag events.
    """
    is_long = (side == "LONG")
    h = _f(indicators, f"high_{tf}"); l = _f(indicators, f"low_{tf}")
    bbu = _f(indicators, f"bb_upper_{tf}"); bbl = _f(indicators, f"bb_lower_{tf}")
    if is_long:
        if bbu <= 0 or h <= 0: return False
        tag_now = (h >= bbu - 1e-9)
        fail_now = (h < bbu)
    else:
        if bbl <= 0 or l <= 0: return False
        tag_now = (l <= bbl + 1e-9)
        fail_now = (l > bbl)
    hist = state.setdefault("bb_tag_history", [])
    hist.append(tag_now)
    if len(hist) > lookback:
        hist[:] = hist[-lookback:]
    recent_had_tag = any(hist[:-1]) if len(hist) > 1 else False
    return recent_had_tag and fail_now


def _wt_cross_against(indicators: dict, tf: str, side: str, state: dict) -> bool:
    """wt1_TF crosses against position (LONG: wt1 < wt2 after being >=)."""
    wt1 = _f(indicators, f"wt1_{tf}"); wt2 = _f(indicators, f"wt2_{tf}")
    if wt1 == 0 and wt2 == 0: return False
    prior_w1 = state.get("prior_wt1", {}).get(tf, wt1)
    prior_w2 = state.get("prior_wt2", {}).get(tf, wt2)
    is_long = (side == "LONG")
    if is_long:
        return wt1 < wt2 and prior_w1 >= prior_w2
    return wt1 > wt2 and prior_w1 <= prior_w2


def evaluate_mtf_exit(state: dict, position: Any, indicators: dict, mark: float,
                      side: str, mode: str, config: Any) -> Tuple[bool, str]:
    """Returns (fire, reason). 5 triggers, any fires CLOSE.

    Assumes caller has updated armed state already this bar.
    """
    if not bool(getattr(config, "MTF_EXIT_USE_COMPOUND", False)):
        return False, ""
    entry_price = float(getattr(position, "entry_price", 0.0) or state.get("entry_price", 0.0))
    if entry_price <= 0: return False, ""
    is_long = (side == "LONG")
    # 1. ATR trail
    if bool(getattr(config, "MTF_ATR_TRAIL_ENABLED", True)):
        atr_tf = str(getattr(config, "MTF_ATR_TRAIL_TF", "15m"))
        atr_mult = float(getattr(config, "MTF_ATR_TRAIL_MULT", 2.0))
        fire, reason, new_trail = _atr_trail_check(state, mark, entry_price, atr_tf, atr_mult, is_long, indicators)
        state["atr_trail"] = new_trail
        if fire: return True, reason
    # 4. DC reject
    if bool(getattr(config, "MTF_DC_REJECT_EXIT_ENABLED", True)):
        dc_tf = str(getattr(config, "MTF_DC_REJECT_EXIT_TF", "1h"))
        fire, reason, new_ever = _dc_reject_check(state, mark, side, indicators, dc_tf)
        state["ever_outside_dc"] = new_ever
        if fire: return True, reason
    # 5. BB reject
    if bool(getattr(config, "MTF_BB_REJECT_EXIT_ENABLED", True)):
        bb_tf = str(getattr(config, "MTF_BB_REJECT_EXIT_TF", "1h"))
        bb_lb = int(getattr(config, "MTF_BB_REJECT_EXIT_LOOKBACK", 5))
        if _bb_reject_check(state, indicators, side, bb_tf, bb_lb):
            return True, f"MTF_BB_REJECT_{bb_tf}"
    # 2+3. GR exit + WT cross (require BOTH)
    if bool(getattr(config, "MTF_GR_EXIT_GATE_ENABLED", True)):
        gr_exit_tfs = int(getattr(config, "MTF_GR_EXIT_MIN_TFS", 3))
        gr_exit_ind = int(getattr(config, "MTF_GR_EXIT_MIN_IND", 5))
        opposite_side = "SHORT" if is_long else "LONG"
        # GR exit = GR filter pass for OPPOSITE side
        if gr_filter_pass(indicators, opposite_side, mode, config,
                          min_tfs=gr_exit_tfs, min_ind=gr_exit_ind):
            if bool(getattr(config, "MTF_WT_CROSS_EXIT_ENABLED", True)):
                wt_tf = str(getattr(config, "MTF_WT_CROSS_EXIT_TF", "15m"))
                if wt_tf == "either":
                    if _wt_cross_against(indicators, "15m", side, state) or _wt_cross_against(indicators, "1h", side, state):
                        return True, f"MTF_GR_WT_EXIT_either"
                elif _wt_cross_against(indicators, wt_tf, side, state):
                    return True, f"MTF_GR_WT_EXIT_{wt_tf}"
    return False, ""


# ── Initialisation helpers for caller ───────────────────────────────────────

def ensure_state(states: dict, key: str) -> dict:
    """Get-or-create state for a (sym, side) key. Caller passes its own dict."""
    if key not in states:
        states[key] = _new_state()
    return states[key]


def load_mtf_states() -> dict:
    """Load cached mtf_states from disk with fallback to empty dict."""
    import os, json
    path = "data/mtf_states_cache.json"
    if not os.path.exists(path): return {}
    try:
        with open(path, "r") as f: return json.load(f)
    except Exception: return {}


def save_mtf_states(mtf_states: dict) -> None:
    """Save cached mtf_states to disk atomically."""
    import os, json
    path = "data/mtf_states_cache.json"
    tmp_path = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "w") as f: json.dump(mtf_states, f)
        os.replace(tmp_path, path)
    except Exception: pass


# ── ONE-LINE FILTER for chokepoint use in ez_manage / tradier_manage ────────

def mtf_entry_filter_passes(
    mtf_states: dict, symbol: str, side: str, indicators: dict, mode: str,
    config: Any, blocked: bool = False,
) -> tuple[bool, str]:
    """One-line gate. Returns (allow_entry: bool, reason_if_blocked: str)."""
    if blocked:
        return False, "MTF_PER_SYM_BLOCKED"
    key = f"{symbol}_{side}"
    state = ensure_state(mtf_states, key)
    update_armed_state(state, indicators, side, config)
    save_mtf_states(mtf_states)
    if bool(getattr(config, "MTF_REQUIRE_ARMED_ANY", True)):
        if not _armed_any_effective(state, indicators, side, config):
            return False, "MTF_NO_ARMED_STATE"
    if bool(getattr(config, "MTF_ENTRY_REQUIRE_GR_FILTER", True)):
        min_tfs = int(getattr(config, "MTF_GR_MIN_TFS", 3))
        min_ind = int(getattr(config, "MTF_GR_MIN_IND", 5))
        if not gr_filter_pass(indicators, side, mode, config, min_tfs=min_tfs, min_ind=min_ind):
            return False, f"MTF_GR_FILTER_FAIL_{min_tfs}tf_{min_ind}ind"
    return True, ""


def mtf_size_mult_for(psym_get_fn, symbol: str, side: str, default: float = 1.0) -> float:
    """Returns size multiplier from per-sym overlay (0.0/0.5/1.0 per Phase K)."""
    try:
        return float(psym_get_fn(symbol, side, "MTF_SIZE_MULT", default))
    except (TypeError, ValueError):
        return default
