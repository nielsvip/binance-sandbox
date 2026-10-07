"""STOCK augment sources — live-parity vec twins of tradier_manage.TradierStrategy.evaluate_augment (tradier_manage.py:21062-21400). [N4]

LIVE (stocks) augment decision order inside evaluate_augment (first fire wins):
  1. GAP_RISK_REENTRY partial-reduce fill augment            -> needs the gap-pending state of a prior GAP_RISK reduce: NOT modelled here.
  2. LR_BAND_LADDER_ORDINARY_PARITY ladder (LR_BAND_LADDER_ENABLED + _ORDINARY_PARITY_ENABLED, both False)   -> not modelled (default off, needs band ladder state).
  3. WT_D_BOUNCE_AUG   (_cfg_auto WT_D_BOUNCE_AUG_ENABLED default False; gain < 0)                      -> wt_d_bounce_fire()
  4. TRAILING_AUG      (TRAILING_AUG_ENABLED_TRADIER default False; gain > 0, stepped +GAIN_STEP_PCT)  -> trailing_aug_fire()
  5. GENERIC GATE: eff_gain >= AUGMENT_MIN_GAIN_PCT (effective_min_gain: MIN_GAIN_TO_BUY_AGGRESSIVELY floor 2.5), cooldown 300 s,
     then DC TIER augment (DC_TIER_AUG enabled default True): active tier = highest of {5m,15m,1h,4h} Donchian-high breakouts
     (long: close > dc_high_TF*(1+0.001); short: close < dc_low_TF*(1-0.001)); target value = START_POSITION_SIZE * {1,2,3,5}[tier];
     add value = target - current_value when current_value < 0.75*target (cap MAX_SYMBOL_VALUE_TRADIER)   -> dc_tier_fire()
NO 5m/3m/1m data in the backtest system: tier 1 (5m) is INERT (never active); tiers 2-4 use the 15m/1h/4h arrays.
eff_gain = raw gain, or raw/(1-PPL_FRAC) after a partial profit lock fired (ez_reentry.effective_gain_pct, EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED default True).
"""
from __future__ import annotations

TIER_MULTS = {2: 2.0, 3: 3.0, 4: 5.0}   # tier 1 (5m) inert: no 5m data allowed


def effective_gain(cfg, raw_gain_pct: float, ppl_fired: bool, ppl_frac: float) -> float:
    if ppl_fired and bool(getattr(cfg, "EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED", True)):
        denom = 1.0 - float(ppl_frac or 0.5)
        if denom > 1e-9:
            return raw_gain_pct / denom
    return raw_gain_pct


def min_gain(cfg) -> float:
    import vec_decisions.gain_ladder_augment as G
    return G.effective_min_gain(cfg)


def active_tier(is_long: bool, px: float, dc15: float, dc1h: float, dc4h: float, buf: float = 0.001) -> int:
    t = 0
    if is_long:
        if dc15 > 0 and px > dc15 * (1 + buf): t = 2
        if dc1h > 0 and px > dc1h * (1 + buf): t = 3
        if dc4h > 0 and px > dc4h * (1 + buf): t = 4
    else:
        if dc15 > 0 and px < dc15 * (1 - buf): t = 2
        if dc1h > 0 and px < dc1h * (1 - buf): t = 3
        if dc4h > 0 and px < dc4h * (1 - buf): t = 4
    return t


def dc_tier_fire(cfg, is_long, px, raw_gain_pct, cur_value, dc15, dc1h, dc4h, ppl_fired=False, ppl_frac=0.5):
    """-> (fire, add_value_usd, reason). cooldown is enforced by the caller (AUGMENTATION_COOLDOWN_SECONDS)."""
    if not bool(getattr(cfg, "DC_TIER_AUG_ENABLED", True)):
        return False, 0.0, ""
    _mg = min_gain(cfg)
    if bool(getattr(cfg, "AUGMENT_TYPED_MIN_GAIN_ENABLED", False)):  # [UNWV/002] live DC_TIER = breakout type: AUGMENT_BREAKOUT_MIN_GAIN_PCT replaces the generic tier (same fn as live)
        try:
            import vec_decisions.live_unw_gates as _lug
            _mg = _lug.augment_tier_min_gain(lambda k, d=None: getattr(cfg, k, d), "DC_TIER_AUG", _mg)
        except Exception:
            pass
    if effective_gain(cfg, raw_gain_pct, ppl_fired, ppl_frac) < _mg:
        return False, 0.0, ""
    tier = active_tier(is_long, px, dc15, dc1h, dc4h)
    if tier <= 0:
        return False, 0.0, ""
    cap = float(getattr(cfg, "MAX_SYMBOL_VALUE_TRADIER", 15000.0) or 15000.0)
    if cur_value >= cap:
        return False, 0.0, ""
    target = float(getattr(cfg, "START_POSITION_SIZE", 500.0)) * TIER_MULTS[tier]
    target = min(target, cap - cur_value)
    if cur_value < target * 0.75:
        add = target - cur_value
        if add / max(px, 1e-9) >= 0.5:
            return True, add, f"DC_TIER{tier}_{ {2: 'DC15M', 3: 'DC1H', 4: 'DC4H'}[tier] }_AUG target={target:.0f} cur={cur_value:.0f}"
    return False, 0.0, ""


def trailing_aug_fire(cfg, raw_gain_pct, cur_value, state: dict):
    """TRAILING_AUG_ENABLED_TRADIER (default False): every +GAIN_STEP_PCT gain, max N per position; add = max(1 share, START_POSITION_SIZE). -> (fire, add_value, reason, new_state)"""
    if not bool(getattr(cfg, "TRAILING_AUG_ENABLED_TRADIER", False)) or raw_gain_pct <= 0:
        return False, 0.0, "", state
    step = float(getattr(cfg, "TRAILING_AUG_GAIN_STEP_PCT", 0.5)); mx = int(getattr(cfg, "TRAILING_AUG_MAX_PER_POSITION", 3)); mn = float(getattr(cfg, "TRAILING_AUG_MIN_GAIN_PCT", 0.5))
    st = dict(state or {"last_threshold": 0.0, "aug_count": 0})
    nxt = max(mn, st["last_threshold"] + step)
    cap = float(getattr(cfg, "MAX_SYMBOL_VALUE_TRADIER", 15000.0) or 15000.0)
    if raw_gain_pct >= nxt and st["aug_count"] < mx and cur_value < cap * 0.95:
        st = {"last_threshold": nxt, "aug_count": st["aug_count"] + 1}
        return True, float(getattr(cfg, "START_POSITION_SIZE", 500.0)), f"TRAILING_AUG_step{nxt:.2f}%_g{raw_gain_pct:.2f}%_count{st['aug_count']}/{mx}", st
    return False, 0.0, "", state


def wt_d_bounce_fire(cfg, is_long, px, raw_gain_pct, wt1_d, prev_wt1_d, cur_qty, state: dict, bar_seconds: float, now_s: float):
    """WT_D_BOUNCE_AUG (default False): gain<0, wt_D turns (bounce), higher WT/price than last aug, cooldown hours; add = cur_qty*(MULT-1). -> (fire, add_qty, reason, new_state)"""
    if not bool(getattr(cfg, "WT_D_BOUNCE_AUG_ENABLED", False)) or raw_gain_pct >= 0:
        return False, 0.0, "", state
    st = dict(state or {})
    last_wt = st.get("last_aug_wt1_d", -999.0 if is_long else 999.0)
    last_px = st.get("last_aug_price", 0.0 if is_long else float("inf"))
    last_ts = st.get("last_aug_ts", -1e18)
    bounce = (wt1_d > prev_wt1_d) if is_long else (wt1_d < prev_wt1_d)
    hwt = (not bool(getattr(cfg, "WT_D_BOUNCE_AUG_REQUIRE_HIGHER_WT", True))) or ((wt1_d > last_wt) if is_long else (wt1_d < last_wt))
    hpx = (not bool(getattr(cfg, "WT_D_BOUNCE_AUG_REQUIRE_HIGHER_PRICE", True))) or ((px > last_px) if is_long else (px < last_px))
    cd = (now_s - last_ts) >= float(getattr(cfg, "WT_D_BOUNCE_AUG_COOLDOWN_HOURS", 1.0)) * 3600.0
    if bounce and hwt and hpx and cd:
        mult = float(getattr(cfg, "WT_D_BOUNCE_AUG_MULTIPLIER", 2.0))
        st = {"last_aug_wt1_d": wt1_d, "last_aug_price": px, "last_aug_ts": now_s}
        return True, cur_qty * (mult - 1.0), f"WT_D_BOUNCE_AUG wt1_D={wt1_d:.2f}>prev={prev_wt1_d:.2f} px={px:.2f} gain={raw_gain_pct:.2f}% mult={mult:.1f}x", st
    return False, 0.0, "", state
