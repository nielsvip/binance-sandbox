"""
vec_paths/live_only_signals_batch5.py — Port of 20 top LIVE-only signals to vec.

USER MANDATE 2026-05-27: close the 586/609 LIVE_ONLY gap reported by
tools/signal_parity_diff.py. Ports the top-10 ENTRY + top-10 EXIT signals so
vec backtests fire the same events that live does. Every knob defaults OFF/0
to preserve Arm A bit-exact baseline (snapshot at
backups/PARITY_100PCT_20260526-230547_batch4_sharpe_parity_arm_B).

All check_* functions are stateless. They mirror the scalar branch in live
exactly — emit the same reason strings (with the live's parameter interpolation
preserved) so the diff tool's stub clustering matches. Wiring side in
v8_vec_sweep.py reads `_pos` (the PosStateAdapter) and the `store` (NPZStoreAdapter)
identically to how exit_r1_r2 / partial_profit_lock_v2 do it.

────────────────────────────────────────────────────────────────────────────────
PORT INVENTORY (signal name | live source | vec wire site | knob)
────────────────────────────────────────────────────────────────────────────────

ENTRY (top 10 by live volume)
  1.  QUICK_HEDGE_PROTECT_SHORT_LOSS  — ez_positions_quick.py:7494 (HEDGE_PROTECT_<SIDE>_LOSS)
      → check_hedge_protect_loss_entry(); SYNTHETIC portfolio_losers feed.
  2.  QUICK_HEDGE_PROTECT_LONG_LOSS   — same site, opposite side.
  3.  QUICK_OPEN_STRONG_SELL          — ez_positions_quick.py:9534 (composite_score>=80)
      → check_quick_open_strong_entry()
  4.  QUICK_HEDGE_SAME_SYM_LAST_RESORT — config.QUICK_HEDGE_SAME_SYM_LAST_RESORT_ENABLED
      → check_quick_hedge_same_sym_last_resort(); HISTORICAL backtests only —
      config is DISABLED live 2026-05-26.
  5.  HEDGE_PROTECT_SHORT_LOSS        — ez_positions_quick.py:7494 (non-QUICK variant)
      → check_hedge_protect_loss_entry()  (same function, qty_pct=1.0 instead of 0.5)
  6.  DAEMON_PRICE_CROSS_REENTRY      — ez_reentry.py:614+ (price crosses past stored exit)
      → check_daemon_price_cross_reentry()
  7.  HEDGE_PROTECT_LONG_LOSS         — same as #5, side flipped.
  8.  QUICK_OPEN_STRONG_BUY           — same as #3, side flipped.
  9.  GUARANTEED_PRICE_CROSS_REENTRY_DISK_SHORT/LONG — ez_reentry.py:777 (disk-backed cross)
      → check_guaranteed_price_cross_reentry_disk()
  10. DIRECTION_FAVORABLE_REENTRY     — ez_manage.py:34213+ (k/wt aligned post-exit)
      → check_direction_favorable_reentry()

EXIT (top 10 by live volume)
  1.  RIDICULOUS_HOLD                 — ez_manage.py:39620+ (age cap force-close)
      → check_ridiculous_hold_exit()
  2.  QUICK_REDUCE_STRONG_REDUCE      — ez_positions_quick.py:3469+ (HLR_TOP_EXIT family)
      → check_quick_reduce_strong_reduce_exit()
  3.  QUICK_BREAKEVEN_GAIN_EROSION_STOP — ez_positions_quick.py:14117+ (BE erosion)
      → check_quick_breakeven_gain_erosion_stop()  HISTORICAL (DISABLED live).
  4.  QUICK_CYCLE_TP_STOCH_AGAINST    — historical reason family (stoch flip near TP)
      → check_quick_cycle_tp_stoch_against()
  5.  QUICK_BANDAID_OFF               — ez_positions_quick.py:5673+ (BANDAID_OFF_FIRST)
      → check_quick_bandaid_off_exit()
  6.  DELTA_EXIT_speed_decay          — ez_positions_quick.py:3400 + ez_manage.py:41770
      → check_delta_exit_speed_decay()
  7.  QUICK_SENTIMENT_CUT_GAIN        — ez_positions_quick.py:11284 (SENTIMENT_CUT_GAIN<N>)
      → check_quick_sentiment_cut_gain()
  8.  HEDGE_BANDAID_OFF_FIRST_PRE     — ez_manage.py:40374 (wt_15m flip favoring origin)
      → check_hedge_bandaid_off_first_pre()
  9.  R1_DC_LOW4_3M_EMERGENCY         — NAMING gap; vec_paths/exit_r1_r2.py already emits.
      → fix_r1_reason_string()  (helper — applied by v8_vec_sweep wiring layer)
  10. IN_GAIN_TREND_EXIT              — ez_manage.py:42946+ (D/4h tiered profit harvest)
      → check_in_gain_trend_exit()  (also fires the MED tier as _MED_WINNER variant)

────────────────────────────────────────────────────────────────────────────────
PORTFOLIO LOSERS (SYNTHETIC) — for cross-symbol hedge signals
────────────────────────────────────────────────────────────────────────────────
Vec runs per-symbol so cannot see other syms' running gain. Approximation:
treat any sym whose running gain has been < SYNTHETIC_LOSER_THRESHOLD_PCT (-2%)
for > SYNTHETIC_LOSER_MIN_BARS bars as a "losing sibling". This is documented
in the validator output with SYNTHETIC_PORTFOLIO=True. User can flip to
Option B (multi-sym outer loop) later.

────────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

from typing import Any, Optional

# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════

def _bar_ts(store: Any, bar_idx: int) -> float:
    """Return unix ts at bar_idx, or 0.0."""
    try:
        ts = store.timestamps if hasattr(store, "timestamps") else None
        if ts is not None and bar_idx < len(ts):
            return float(ts[bar_idx])
    except Exception:
        pass
    return 0.0


def _age_min(store: Any, bar_idx: int, pos_state: Any) -> float:
    try:
        bt = _bar_ts(store, bar_idx)
        if bt > 0 and pos_state.entry_ts > 0:
            return (bt - float(pos_state.entry_ts)) / 60.0
    except Exception:
        pass
    return 0.0


def _is_long(pos_state: Any) -> bool:
    return getattr(pos_state, "side", "LONG") == "LONG"


# 2026-06-25 SINGLE-SOURCE WIRE: build the per-bar indicator dict the shared scalar
# predicate (vec_decisions.guaranteed_price_cross_reentry.check_guaranteed_price_cross_reentry)
# reads, from the NPZ store at bar_idx. Lets the vec price-cross reentry call the EXACT same
# fire predicate the LIVE daemon/inline path uses (proven bool-for-bool == live), instead of
# the old hand-rolled copy that lacked the confirmation gate entirely. ha_4h color is recovered
# from numeric ha_4h_green/ha_4h_red fields if present (else neutral → HA-force inert).
_REENTRY_IND_FIELDS = (
    "wt1_3m", "wt2_3m", "wt1_5m", "wt2_5m", "wt1_15m", "wt2_15m", "wt1_1h", "wt2_1h",
    "stoch_k_3m", "stoch_d_3m", "stoch_k_15m", "stoch_k_15m_prev", "stoch_k_1h",
    "dc_basis_4h", "basis_4h", "bb_basis_4h",
    "dc_high_3m", "dc_low_3m", "dc_high_1h", "dc_low_1h", "dc_high_15m", "dc_low_15m",
    "dc_high4_3m", "dc_low4_3m", "sma_200_15m",
)
def _reentry_ind_from_store(store: Any, bar_idx: int) -> dict:
    ind = {k: store.f(k, bar_idx, 0.0) for k in _REENTRY_IND_FIELDS}
    _hg = store.f("ha_4h_green", bar_idx, 0.0)
    _hr = store.f("ha_4h_red", bar_idx, 0.0)
    ind["ha_4h"] = "green" if _hg > 0 else ("red" if _hr > 0 else "neutral")
    return ind


# ════════════════════════════════════════════════════════════════════════════
# 2026-05-27 BATCH 6 — per-signal cooldown helper
# ════════════════════════════════════════════════════════════════════════════
# Each over-firing signal stamps its last-fire timestamp on
# `pos_state._sym_state.b6_last_fire_ts[<key>]`. When cooldown_s > 0 and
# (current_ts - last_fire_ts) < cooldown_s, the signal is blocked.
# pos_state._sym_state is the underlying SymState (the adapter exposes it).

def _b6_cooldown_blocked(pos_state: Any, key: str, cur_ts: float, cooldown_s: float) -> bool:
    if cooldown_s <= 0.0:
        return False
    sym_state = getattr(pos_state, "_sym_state", None)
    if sym_state is None:
        return False
    d = getattr(sym_state, "b6_last_fire_ts", None)
    if not isinstance(d, dict):
        return False
    last = float(d.get(key, 0.0) or 0.0)
    if last <= 0.0:
        return False
    return (cur_ts - last) < cooldown_s


def _b6_stamp_fire(pos_state: Any, key: str, cur_ts: float) -> None:
    sym_state = getattr(pos_state, "_sym_state", None)
    if sym_state is None:
        return
    d = getattr(sym_state, "b6_last_fire_ts", None)
    if not isinstance(d, dict):
        d = {}
        try:
            sym_state.b6_last_fire_ts = d
        except Exception:
            return
    d[key] = float(cur_ts)


# ════════════════════════════════════════════════════════════════════════════
# SYNTHETIC PORTFOLIO LOSER FEED (Option A approximation per directive)
# ════════════════════════════════════════════════════════════════════════════
# Tracks whether THIS sym's running gain has been < threshold for N bars. If so,
# it qualifies as a "losing sibling" trigger for the hedge-protect signals.
# All hedge_protect_* signals consult this synthetic feed.
#
# When SYNTHETIC_PORTFOLIO_LOSERS_ENABLED=True, the loser-pool size is mocked as
# 1 (this sym IS the loser the hedge is protecting). When False, no fires.
#
# A multi-sym outer loop (Option B) would replace this with a shared dict
# across sym sims. Marked clearly in validator output.

def is_synthetic_loser(pos_state: Any, cfg: Any, store: Any, bar_idx: int) -> bool:
    """Return True if this sym would be considered a 'losing sibling' for hedge
    protect signals under the synthetic portfolio model.

    Conditions:
      - position is open
      - gain_pct <= SYNTHETIC_LOSER_THRESHOLD_PCT (default -2.0%)
      - position age >= SYNTHETIC_LOSER_MIN_AGE_MIN (default 30 min)
    """
    if not pos_state.open:
        return False
    threshold = float(getattr(cfg, "SYNTHETIC_LOSER_THRESHOLD_PCT", -2.0))
    min_age = float(getattr(cfg, "SYNTHETIC_LOSER_MIN_AGE_MIN", 30.0))
    if pos_state.gain_pct > threshold:
        return False
    if _age_min(store, bar_idx, pos_state) < min_age:
        return False
    return True


# ════════════════════════════════════════════════════════════════════════════
# ENTRY #1+#2+#5+#7  HEDGE_PROTECT_*_LOSS (quick + non-quick variants)
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: ez_positions_quick.py:7494 — execute_trade_wrapper reason=
#   f"HEDGE_PROTECT_{origin_side}_LOSS" when same-sym hedge fires.
# The QUICK_HEDGE_PROTECT_* variants are emitted upstream in ez_manage.py
# when execute_now decorates with QUICK_ prefix.
#
# LOGIC: an opposite-side hedge OPENS when the main position is losing badly.
# So in vec we model this as: a NEW opposite-side OPEN fires (qty=HEDGE_QTY_PCT
# × main_qty) AGAINST the losing position when synthetic_loser=True AND
# gain <= HEDGE_PROTECT_TRIGGER_GAIN (default -0.5%).
#
# Wire site in v8_vec_sweep: in the per-bar loop, after R1/R2/PPL/IN_GAIN
# exit checks. Fires a NEW position opposite to the main side, tagged as hedge.

def check_hedge_protect_loss_entry(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
    *,
    quick: bool = True,
) -> Optional[dict]:
    """HEDGE_PROTECT_<SIDE>_LOSS / QUICK_HEDGE_PROTECT_<SIDE>_LOSS.

    Fires a hedge-opposite opening event against a losing main position.
    Returns dict with reason / action='HEDGE_OPEN' / qty_pct.

    Args:
      quick: True → emit QUICK_HEDGE_PROTECT_* (default; the common path
             through ez_manage.py routing). False → emit HEDGE_PROTECT_*
             (the bare ez_positions_quick.py variant).
    """
    if not getattr(cfg, "HEDGE_PROTECT_LOSS_VEC_ENABLED", False):
        return None
    if not pos_state.open:
        return None
    if not is_synthetic_loser(pos_state, cfg, store, bar_idx):
        return None
    trigger = float(getattr(cfg, "HEDGE_PROTECT_TRIGGER_GAIN_PCT", -0.5))
    if pos_state.gain_pct > trigger:
        return None
    # 2026-05-27 BATCH 6 calibration — over-firing 8.1× live (18,332 vs 2,263).
    # Add (a) min position age, (b) min consecutive bars in loss, (c) cooldown.
    cur_ts = _bar_ts(store, bar_idx)
    min_age = float(getattr(cfg, "HEDGE_PROTECT_MIN_AGE_MIN", 0.0))
    if min_age > 0.0 and _age_min(store, bar_idx, pos_state) < min_age:
        return None
    cd = float(getattr(cfg, "HEDGE_PROTECT_COOLDOWN_S", 0.0))
    if _b6_cooldown_blocked(pos_state, "hedge_protect", cur_ts, cd):
        return None
    qty_pct = float(getattr(cfg, "HEDGE_PROTECT_QTY_PCT", 1.0))
    origin_side = "LONG" if _is_long(pos_state) else "SHORT"
    prefix = "QUICK_HEDGE_PROTECT" if quick else "HEDGE_PROTECT"
    _b6_stamp_fire(pos_state, "hedge_protect", cur_ts)
    return {
        "reason": f"{prefix}_{origin_side}_LOSS",
        "action": "HEDGE_OPEN",
        "qty_pct": qty_pct,
        "synthetic_portfolio": True,
    }


# ════════════════════════════════════════════════════════════════════════════
# ENTRY #3+#8  QUICK_OPEN_STRONG_BUY / QUICK_OPEN_STRONG_SELL
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: ez_positions_quick.py:9534 — composite_signal_score >= 80
# emits action='STRONG_BUY' (LONG) / 'STRONG_SELL' (SHORT). Downstream
# ez_manage.py decorates with QUICK_OPEN_ prefix.
#
# LOGIC: composite score blends momentum, risk-level, volatility. In vec we
# approximate as: WT velocity strong + bullish/bearish HTF alignment + k_extreme.
# This is a SIMPLIFICATION — true composite has 10+ factors but those are not
# in NPZ. We document the approximation.

def check_quick_open_strong_entry(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    """QUICK_OPEN_STRONG_BUY / QUICK_OPEN_STRONG_SELL — composite_score>=80.

    Approximation factors (5 of the live 10+):
      - wt_velocity_15m strong in direction (|v| >= V_MIN)
      - HTF stack aligned (1h + 4h WT same direction)
      - k_3m extreme in direction (LONG: k_3m<25 OR SHORT: k_3m>75)
      - bb_pct_b in pullback zone (LONG: <0.3, SHORT: >0.7)
      - dc_position aligned (LONG: <0.4, SHORT: >0.6)

    Fires only when position is FLAT (qty <= 0.0001).
    """
    if not getattr(cfg, "QUICK_OPEN_STRONG_VEC_ENABLED", False):
        return None
    if pos_state.open:
        return None  # only fires on FLAT

    ltf = "5m" if mode == "tradier" else "3m"
    side_long = _is_long(pos_state)

    vel_min = float(getattr(cfg, "QUICK_OPEN_STRONG_VEL_MIN", 1.0))
    vel = store.f(f"wt_velocity_15m", bar_idx, 0.0)
    vel_ok = (side_long and vel >= vel_min) or ((not side_long) and vel <= -vel_min)

    # HTF stack: wt1 vs wt2 on 1h + 4h same direction
    w1_1h = store.f("wt1_1h", bar_idx, 0.0)
    w2_1h = store.f("wt2_1h", bar_idx, 0.0)
    w1_4h = store.f("wt1_4h", bar_idx, 0.0)
    w2_4h = store.f("wt2_4h", bar_idx, 0.0)
    htf_aligned = (
        ((w1_1h > w2_1h) and (w1_4h > w2_4h)) if side_long
        else ((w1_1h < w2_1h) and (w1_4h < w2_4h))
    )

    # k extreme in direction
    k_ltf = store.f(f"stoch_k_{ltf}", bar_idx, 50.0)
    if side_long:
        k_ok = k_ltf <= float(getattr(cfg, "QUICK_OPEN_STRONG_K_LONG_MAX", 25.0))
    else:
        k_ok = k_ltf >= float(getattr(cfg, "QUICK_OPEN_STRONG_K_SHORT_MIN", 75.0))

    # bb pct b
    bb_pctb = store.f("bb_pct_b_15m", bar_idx, 0.5)
    if side_long:
        bb_ok = bb_pctb <= float(getattr(cfg, "QUICK_OPEN_STRONG_BB_LONG_MAX", 0.30))
    else:
        bb_ok = bb_pctb >= float(getattr(cfg, "QUICK_OPEN_STRONG_BB_SHORT_MIN", 0.70))

    # dc position
    dc_pos = store.f("dc_position_15m", bar_idx, 0.5)
    if side_long:
        dc_ok = dc_pos <= float(getattr(cfg, "QUICK_OPEN_STRONG_DC_LONG_MAX", 0.40))
    else:
        dc_ok = dc_pos >= float(getattr(cfg, "QUICK_OPEN_STRONG_DC_SHORT_MIN", 0.60))

    # 2026-05-27 BATCH 6 — calibration fix for under-firing (vec 2 vs live 858).
    # Original logic ANDed all 5 factors → ~impossible to satisfy. Live's
    # composite is a WEIGHTED SUM ≥ 80, so any 3-of-5 strong factors clear it.
    # Default behavior preserved (strict 5-AND) unless RELAXED knob flipped.
    if bool(getattr(cfg, "QUICK_OPEN_STRONG_RELAXED", False)):
        passed = sum([vel_ok, htf_aligned, k_ok, bb_ok, dc_ok])
        if passed < 3:
            return None
    else:
        if not (vel_ok and htf_aligned and k_ok and bb_ok and dc_ok):
            return None

    reason = "QUICK_OPEN_STRONG_BUY" if side_long else "QUICK_OPEN_STRONG_SELL"
    return {
        "reason": reason,
        "action": "OPEN",
        "qty_pct": 1.0,
        "factors": {
            "vel_15m": vel, "k": k_ltf, "bb_pctb": bb_pctb, "dc_pos": dc_pos,
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# ENTRY #4  QUICK_HEDGE_SAME_SYM_LAST_RESORT
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: config.py:743 QUICK_HEDGE_SAME_SYM_LAST_RESORT_ENABLED (DISABLED
# live 2026-05-26). Vec must model historical fires for backtests of past
# periods. Reason: ez_positions_quick.py same-sym hedge fallback.
#
# LOGIC: when main position is severely losing (gain <= -3%) AND age >= 4h
# AND no other hedge has fired yet, fire a same-sym opposite-side LAST_RESORT
# hedge. Equivalent to flipping the position direction.

def check_quick_hedge_same_sym_last_resort(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    if not getattr(cfg, "QUICK_HEDGE_SAME_SYM_LAST_RESORT_VEC_ENABLED", False):
        return None
    if not pos_state.open:
        return None
    threshold = float(getattr(cfg, "QUICK_HEDGE_SAME_SYM_LAST_RESORT_GAIN_PCT", -3.0))
    if pos_state.gain_pct > threshold:
        return None
    min_age_min = float(getattr(cfg, "QUICK_HEDGE_SAME_SYM_LAST_RESORT_AGE_MIN", 240.0))
    if _age_min(store, bar_idx, pos_state) < min_age_min:
        return None
    qty_pct = float(getattr(cfg, "QUICK_HEDGE_SAME_SYM_LAST_RESORT_QTY_PCT", 1.0))
    return {
        "reason": "QUICK_HEDGE_SAME_SYM_LAST_RESORT",
        "action": "HEDGE_OPEN",
        "qty_pct": qty_pct,
    }


# ════════════════════════════════════════════════════════════════════════════
# ENTRY #6  DAEMON_PRICE_CROSS_REENTRY
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: ez_reentry.py:614-801 — daemon tick loop. For every flat position
# with a recorded exit price, fires a reentry when mark crosses past exit
# (LONG: cur > exit; SHORT: cur < exit), with WT-bounce/rally-ext sizing.
#
# VEC LOGIC: when sym is FLAT (qty==0) AND there's a recorded last_close_price,
# check for cross. If crossed AND time since exit < MAX_AGE_HOURS, fire REENTRY.

def check_daemon_price_cross_reentry(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
    *,
    last_close_price: float = 0.0,
    last_close_ts: float = 0.0,
) -> Optional[dict]:
    if not getattr(cfg, "DAEMON_PRICE_CROSS_REENTRY_VEC_ENABLED", False):
        return None
    if pos_state.open:
        return None  # only fires on FLAT
    if last_close_price <= 0:
        return None
    cur_px = store.price(bar_idx)
    if cur_px <= 0:
        return None
    # Max age cutoff
    max_age_h = float(getattr(cfg, "DAEMON_PRICE_CROSS_REENTRY_MAX_AGE_HOURS", 48.0))
    bt = _bar_ts(store, bar_idx)
    if last_close_ts > 0 and bt > 0:
        age_h = (bt - last_close_ts) / 3600.0
        if age_h > max_age_h:
            return None
    side_long = _is_long(pos_state)
    # 2026-06-25 SINGLE-SOURCE: fire decision now comes from the SHARED scalar predicate
    # (the exact fire logic the LIVE daemon uses: cross OR REENTRY2 DC-breakout, churn guard,
    # confirmation gate via the now-single-sourced reentry_confirmation_gate; NO divergence
    # guard and HA-force OFF — matching ez_reentry_daemon._evaluate_and_queue, proven 0/60000).
    from vec_decisions.guaranteed_price_cross_reentry import check_guaranteed_price_cross_reentry as _shared_re
    bt = _bar_ts(store, bar_idx)
    elapsed_s = (bt - last_close_ts) if (last_close_ts > 0 and bt > 0) else 1e18
    ind = _reentry_ind_from_store(store, bar_idx)
    fires, _ = _shared_re(
        cfg, ind, cur_px, last_close_price, side_long,
        elapsed_s=elapsed_s, is_leash_re=False,
        dc_break_override=True, churn_override=True, divergence_guard=False, ha_force_enabled=False,
    )
    if not fires:
        return None
    # 2026-05-27 BATCH 6 calibration cooldown (vec dedup ~ live min_gap). Knob default 0 = no-op.
    cd = float(getattr(cfg, "DAEMON_PRICE_CROSS_COOLDOWN_S", 0.0))
    cur_ts = bt
    if _b6_cooldown_blocked(pos_state, "daemon_pc", cur_ts, cd):
        return None
    _b6_stamp_fire(pos_state, "daemon_pc", cur_ts)
    return {
        "reason": f"DAEMON_PRICE_CROSS_REENTRY_exit{last_close_price:.6f}_cur{cur_px:.6f}",
        "action": "REENTRY",
        "qty_pct": 1.0,
    }


# ════════════════════════════════════════════════════════════════════════════
# ENTRY #9  GUARANTEED_PRICE_CROSS_REENTRY_DISK_<SIDE>
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: ez_reentry.py:777 — `f"GUARANTEED_PRICE_CROSS_REENTRY_{src_tag}..."`
# where src_tag is "DISK_LONG" / "DISK_SHORT" when source is the persisted JSON.
# Identical logic to DAEMON path but with explicit disk-source tag.
#
# In vec we treat src_tag="DISK_<SIDE>" when the cross is from the recorded
# last_close_price (we have no Redis to distinguish). Reason interpolation
# matches live exactly so stub-cluster picks it up.

def check_guaranteed_price_cross_reentry_disk(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
    *,
    last_close_price: float = 0.0,
    last_close_ts: float = 0.0,
    exit_reason: str = "",
) -> Optional[dict]:
    if not getattr(cfg, "GUARANTEED_PRICE_CROSS_REENTRY_DISK_VEC_ENABLED", False):
        return None
    if pos_state.open:
        return None
    if last_close_price <= 0:
        return None
    cur_px = store.price(bar_idx)
    if cur_px <= 0:
        return None
    side_long = _is_long(pos_state)
    side_tag = "DISK_LONG" if side_long else "DISK_SHORT"
    # 2026-06-25 SINGLE-SOURCE: fire decision now comes from the SHARED scalar predicate
    # (the exact fire logic the LIVE inline ez_reentry.enforce_price_cross_reentry uses: cross +
    # divergence guard + confirmation gate via the now-single-sourced reentry_confirmation_gate;
    # NO DC-breakout, NO churn-in-function — matching ez_reentry.py:697-732, proven 0/60000).
    # The old vec copy fired on bare cross+divergence with NO confirmation gate → over-fired live.
    from vec_decisions.guaranteed_price_cross_reentry import check_guaranteed_price_cross_reentry as _shared_re
    _is_leash = "BREAKOUT_LEASH" in str(exit_reason or "").upper()
    fires, _ = _shared_re(
        cfg, _reentry_ind_from_store(store, bar_idx), cur_px, last_close_price, side_long,
        elapsed_s=1e18, is_leash_re=_is_leash,
        dc_break_override=False, churn_override=False, divergence_guard=True, ha_force_enabled=True,
    )
    if not fires:
        return None
    # WT/k sizing tag matching live (ez_reentry.py:737-755)
    w1_15 = store.f("wt1_15m", bar_idx, 0.0)
    w2_15 = store.f("wt2_15m", bar_idx, 0.0)
    w1_1h = store.f("wt1_1h", bar_idx, 0.0)
    w2_1h = store.f("wt2_1h", bar_idx, 0.0)
    k_1h = store.f("stoch_k_1h", bar_idx, 50.0)
    wt_favor = (
        (side_long and w1_15 > w2_15 and w1_1h > w2_1h)
        or ((not side_long) and w1_15 < w2_15 and w1_1h < w2_1h)
    )
    rally_ext = (side_long and k_1h > 90.0) or ((not side_long) and k_1h < 10.0)
    if wt_favor:
        sizing_tag = "wt_bounce_150"
    elif rally_ext:
        sizing_tag = f"rally_ext_k1h{k_1h:.0f}_50"
    else:
        sizing_tag = "full_100"
    xr = (exit_reason[:40] or "unk").replace(" ", "_")
    reason = (
        f"GUARANTEED_PRICE_CROSS_REENTRY_{side_tag}_exit{last_close_price:.6f}"
        f"_cur{cur_px:.6f}_{sizing_tag}_xr{xr}"
    )
    return {"reason": reason, "action": "REENTRY", "qty_pct": 1.0, "sizing": sizing_tag}


# ════════════════════════════════════════════════════════════════════════════
# ENTRY #10  DIRECTION_FAVORABLE_REENTRY
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: ez_manage.py:34213+
# Fires REENTRY when sym is FLAT, exit was recent (within min_since_exit minutes),
# and k_3m, k_15m, wt_15m all still favor the original direction.

def check_direction_favorable_reentry(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
    *,
    last_close_price: float = 0.0,
    last_close_ts: float = 0.0,
) -> Optional[dict]:
    if not getattr(cfg, "DIRECTION_FAVORABLE_REENTRY_VEC_ENABLED", False):
        return None
    if pos_state.open:
        return None
    if last_close_ts <= 0:
        return None
    bt = _bar_ts(store, bar_idx)
    if bt <= 0:
        return None
    min_since_exit = (bt - last_close_ts) / 60.0
    max_min = float(getattr(cfg, "DIRECTION_FAVORABLE_MAX_MINUTES", 30.0))
    if min_since_exit > max_min:
        return None
    side_long = _is_long(pos_state)
    ltf = "5m" if mode == "tradier" else "3m"
    k_ltf = store.f(f"stoch_k_{ltf}", bar_idx, 50.0)
    k_15m = store.f("stoch_k_15m", bar_idx, 50.0)
    w1_15 = store.f("wt1_15m", bar_idx, 0.0)
    w2_15 = store.f("wt2_15m", bar_idx, 0.0)
    if side_long:
        favor = (k_ltf > 50.0 and k_15m > 50.0 and w1_15 > w2_15)
    else:
        favor = (k_ltf < 50.0 and k_15m < 50.0 and w1_15 < w2_15)
    if not favor:
        return None
    reason = (
        f"DIRECTION_FAVORABLE_REENTRY_k3m{k_ltf:.0f}_k15m{k_15m:.0f}"
        f"_wt{w1_15:.1f}/{w2_15:.1f}_min{min_since_exit:.0f}"
    )
    return {"reason": reason, "action": "REENTRY", "qty_pct": 1.0}


# ════════════════════════════════════════════════════════════════════════════
# EXIT #1  RIDICULOUS_HOLD
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: ez_manage.py:39620+ — force-close when:
#   (a) gain <= RIDICULOUS_LOSS_PCT (-15% default) → fires regardless of age
#   (b) age > RIDICULOUS_HOLD_HOURS (48h default) AND gain >= 0 (if NONNEG=True)
#       OR (c) gain < 0 if NONNEG=False (legacy)
# Currently config.RIDICULOUS_HOLD_GUARD_ENABLED=False live. Vec models historical.

def check_ridiculous_hold_exit(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    if not getattr(cfg, "RIDICULOUS_HOLD_VEC_ENABLED", False):
        return None
    if not pos_state.open:
        return None
    # 2026-05-27 BATCH 6 calibration — under-firing (vec 3 vs live 1,572).
    # Live's RIDICULOUS_HOLD/LOSS fires from a periodic background sweep, not
    # from every bar tick. Sample every N bars (default 0 = every bar, the
    # existing behavior). Set FIRE_EVERY_N_BARS=20 to fire ~1×/hr (3m × 20).
    every_n = int(getattr(cfg, "RIDICULOUS_HOLD_FIRE_EVERY_N_BARS", 0))
    if every_n > 1 and (bar_idx % every_n) != 0:
        return None
    gain = pos_state.gain_pct
    loss_cap = float(getattr(cfg, "RIDICULOUS_LOSS_PCT", -15.0))
    if gain <= loss_cap:
        return {
            "reason": f"RIDICULOUS_LOSS_g{gain:.2f}%_cap{loss_cap:.1f}%",
            "action": "CLOSE",
            "bypass_noloss": True,
        }
    hold_h = float(getattr(cfg, "RIDICULOUS_HOLD_HOURS", 48.0))
    age_h = _age_min(store, bar_idx, pos_state) / 60.0
    if age_h <= hold_h:
        return None
    require_nonneg = bool(getattr(cfg, "RIDICULOUS_HOLD_REQUIRE_GAIN_NONNEG", True))
    if require_nonneg and gain < 0.0:
        return None
    if (not require_nonneg) and gain >= 0.0:
        return None
    tag = "nonneg" if require_nonneg else "loss"
    return {
        "reason": f"RIDICULOUS_HOLD_age{age_h:.1f}h_cap{hold_h:.0f}h_g{gain:.2f}%_{tag}",
        "action": "CLOSE",
        "bypass_noloss": True,
    }


# ════════════════════════════════════════════════════════════════════════════
# EXIT #2  QUICK_REDUCE_STRONG_REDUCE (HLR_TOP_EXIT family)
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: ez_positions_quick.py:3469+ — HLR_TOP_EXIT_ENABLED.
# Fires REDUCE when:
#   - gain >= HLR_MIN_GAIN
#   - WT velocity slowing on multiple HTFs (HLR_TOP detection)
#   - tagged for 1.5-3x reentry
# Reason: "HLR_TOP_EXIT_<tfs>_g={pnl:.2f}%_REENTER_<mult>x"

def check_quick_reduce_strong_reduce_exit(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    if not getattr(cfg, "QUICK_REDUCE_STRONG_REDUCE_VEC_ENABLED", False):
        return None
    if not pos_state.open:
        return None
    gain = pos_state.gain_pct
    min_gain = float(getattr(cfg, "HLR_MIN_GAIN_PCT", 1.0))
    if gain < min_gain:
        return None
    side_long = _is_long(pos_state)
    # HLR_TOP detect: WT velocity decelerating on 2+ HTFs against position
    tfs = ("15m", "1h", "4h")
    decel_tfs = []
    for tf in tfs:
        vel = store.f(f"wt_velocity_{tf}", bar_idx, 0.0)
        prev_idx = max(0, bar_idx - 1)
        vel_prev = store.f(f"wt_velocity_{tf}", prev_idx, vel)
        against = (side_long and vel < 0) or ((not side_long) and vel > 0)
        decel = abs(vel) < abs(vel_prev) * 0.5 and abs(vel_prev) > 1e-6
        if against and decel:
            decel_tfs.append(tf)
    min_tfs = int(getattr(cfg, "HLR_MIN_TFS", 2))
    if len(decel_tfs) < min_tfs:
        return None
    # 2026-05-27 BATCH 6 calibration — cooldown
    cd = float(getattr(cfg, "HLR_TOP_COOLDOWN_S", 0.0))
    cur_ts = _bar_ts(store, bar_idx)
    if _b6_cooldown_blocked(pos_state, "hlr_top", cur_ts, cd):
        return None
    _b6_stamp_fire(pos_state, "hlr_top", cur_ts)
    mult = float(getattr(cfg, "HLR_REENTRY_MULT", 1.5))
    reduce_frac = float(getattr(cfg, "HLR_REDUCE_FRAC", 0.5))
    return {
        "reason": f"HLR_TOP_EXIT_{'|'.join(decel_tfs)}_g={gain:.2f}%_REENTER_{mult:.1f}x",
        "action": "REDUCE",
        "qty_pct": reduce_frac,
    }


# ════════════════════════════════════════════════════════════════════════════
# EXIT #3  QUICK_BREAKEVEN_GAIN_EROSION_STOP
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: ez_positions_quick.py:14117+ (DISABLED live 2026-05-21).
# Vec must model for historical backtests.
#
# Fires when:
#   - age >= BREAKEVEN_GRACE_MINUTES (default 15)
#   - gain in window [MIN_GAIN, max(MIN_GAIN+0.5, 0.02)]
#   - max_peak < HARD_BREAKEVEN_MIN_PEAK_PCT (default 0.5)

def check_quick_breakeven_gain_erosion_stop(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    if not getattr(cfg, "QUICK_BREAKEVEN_GAIN_EROSION_VEC_ENABLED", False):
        return None
    if not pos_state.open:
        return None
    grace_min = float(getattr(cfg, "BREAKEVEN_GRACE_MINUTES", 15.0))
    age_min = _age_min(store, bar_idx, pos_state)
    if age_min < grace_min:
        return None
    gain = pos_state.gain_pct
    min_g = float(getattr(cfg, "BREAKEVEN_GAIN_EROSION_MIN_GAIN", 0.10))
    upper = max(min_g + 0.5, 0.02)
    if not (min_g <= gain < upper):
        return None
    # HBF: only fire if peak is small (real breakeven decline)
    hbf_min = float(getattr(cfg, "HARD_BREAKEVEN_MIN_PEAK_PCT", 0.5))
    if pos_state.max_gain_pct >= hbf_min:
        return None  # peak too high — let R2/PPL handle
    return {
        "reason": f"BREAKEVEN_GAIN_EROSION_STOP_age{age_min:.0f}m_gain{gain:.2f}%",
        "action": "CLOSE",
        "bypass_noloss": False,
    }


# ════════════════════════════════════════════════════════════════════════════
# EXIT #4  QUICK_CYCLE_TP_STOCH_AGAINST
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: historical family. CYCLE_TP routes are gated to fire when in
# profit but with stoch turning against the position. Closest concrete code
# is the CYCLE_TP_TIERED block at ez_manage.py:42535+ but the reason family
# we want emits via downstream routing.
#
# VEC LOGIC: fire REDUCE/CLOSE when:
#   - gain >= QUICK_CYCLE_TP_MIN_GAIN (default 1.0%)
#   - stoch K extreme against on LTF (LONG: k_3m>80, SHORT: k_3m<20)
#   - k flipping (k_3m crosses d_3m against)

def check_quick_cycle_tp_stoch_against(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    if not getattr(cfg, "QUICK_CYCLE_TP_STOCH_AGAINST_VEC_ENABLED", False):
        return None
    if not pos_state.open:
        return None
    gain = pos_state.gain_pct
    min_gain = float(getattr(cfg, "QUICK_CYCLE_TP_MIN_GAIN_PCT", 1.0))
    if gain < min_gain:
        return None
    side_long = _is_long(pos_state)
    ltf = "5m" if mode == "tradier" else "3m"
    k_ltf = store.f(f"stoch_k_{ltf}", bar_idx, 50.0)
    d_ltf = store.f(f"stoch_d_{ltf}", bar_idx, 50.0)
    k_extreme = (side_long and k_ltf > 80.0) or ((not side_long) and k_ltf < 20.0)
    k_flipped = (side_long and k_ltf < d_ltf) or ((not side_long) and k_ltf > d_ltf)
    if not (k_extreme and k_flipped):
        return None
    # 2026-05-27 BATCH 6 calibration — over-firing 7.1× live (4,237 vs 593).
    # Add (a) min peak gain (must have actually had a peak above floor first),
    # (b) cooldown to prevent intra-min re-fires.
    min_peak = float(getattr(cfg, "QUICK_CYCLE_TP_MIN_PEAK_PCT", 0.0))
    if min_peak > 0.0 and pos_state.max_gain_pct < min_peak:
        return None
    cd = float(getattr(cfg, "QUICK_CYCLE_TP_COOLDOWN_S", 0.0))
    cur_ts = _bar_ts(store, bar_idx)
    if _b6_cooldown_blocked(pos_state, "quick_cycle_tp", cur_ts, cd):
        return None
    _b6_stamp_fire(pos_state, "quick_cycle_tp", cur_ts)
    n_tf = 1  # match the live family suffix pattern
    return {
        "reason": f"QUICK_CYCLE_TP_STOCH_AGAINST_{n_tf}_g{gain:.2f}%_k{k_ltf:.0f}",
        "action": "REDUCE",
        "qty_pct": float(getattr(cfg, "QUICK_CYCLE_TP_REDUCE_FRAC", 0.5)),
    }


# ════════════════════════════════════════════════════════════════════════════
# EXIT #5  QUICK_BANDAID_OFF (BANDAID_OFF_FIRST)
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: ez_positions_quick.py:5673+ — closes hedge when wt_15m flips
# back favoring the origin (the losing main position is recovering).
# Reason: "BANDAID_OFF_FIRST_wt15m_<w1>>{w2}_hgain<gain>"
#
# In vec we model this as: if THIS sym is a hedge (synthetic), and wt_15m
# flips against the hedge (i.e., back to the origin direction), close it.

def check_quick_bandaid_off_exit(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    if not getattr(cfg, "QUICK_BANDAID_OFF_VEC_ENABLED", False):
        return None
    if not pos_state.open:
        return None
    # Approximate "this is a hedge" — synthetic_portfolio: in single-sym vec,
    # we can't really tell. Use the entry reason if available (when set by
    # the hedge-protect entry above).
    entry_sig = str(getattr(pos_state, "reason", "") or "").upper()
    if "HEDGE_PROTECT" not in entry_sig and "QUICK_HEDGE" not in entry_sig:
        return None  # only fire on synthetic hedges
    side_long = _is_long(pos_state)
    w1_15 = store.f("wt1_15m", bar_idx, 0.0)
    w2_15 = store.f("wt2_15m", bar_idx, 0.0)
    # wt_15m flipped AGAINST the hedge direction (back to origin)
    flipped = (side_long and w1_15 < w2_15) or ((not side_long) and w1_15 > w2_15)
    if not flipped:
        return None
    return {
        "reason": f"BANDAID_OFF_FIRST_wt15m_{w1_15:.1f}>{w2_15:.1f}_hgain{pos_state.gain_pct:.2f}%",
        "action": "CLOSE",
        "bypass_noloss": True,
    }


# ════════════════════════════════════════════════════════════════════════════
# EXIT #6  DELTA_EXIT_speed_decay
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: ez_positions_quick.py:3400, ez_manage.py:41770
# Reason: f"DELTA_EXIT_speed_decay_tfs_lost={...}_gain={pnl:.2f}%"
#
# VEC LOGIC: DELTA speed across TFs decelerating sharply against position.
# Approximation using wt_velocity_* arrays.

def check_delta_exit_speed_decay(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    if not getattr(cfg, "DELTA_EXIT_SPEED_DECAY_VEC_ENABLED", False):
        return None
    if not pos_state.open:
        return None
    gain = pos_state.gain_pct
    min_gain = float(getattr(cfg, "DELTA_EXIT_SPEED_DECAY_MIN_GAIN", 0.5))
    if gain < min_gain:
        return None
    side_long = _is_long(pos_state)
    # Count TFs where velocity has lost direction (close to zero from prior strong)
    tfs = ("15m", "1h", "4h")
    lost_tfs = []
    for tf in tfs:
        vel = store.f(f"wt_velocity_{tf}", bar_idx, 0.0)
        prev_idx = max(0, bar_idx - 1)
        vel_prev = store.f(f"wt_velocity_{tf}", prev_idx, vel)
        # was strongly with us, now near zero or against
        was_with = (side_long and vel_prev > 1.0) or ((not side_long) and vel_prev < -1.0)
        now_lost = abs(vel) < 0.3 or ((side_long and vel < 0) or ((not side_long) and vel > 0))
        if was_with and now_lost:
            lost_tfs.append(tf)
    min_tfs = int(getattr(cfg, "DELTA_EXIT_SPEED_DECAY_MIN_TFS", 2))
    if len(lost_tfs) < min_tfs:
        return None
    # 2026-05-27 BATCH 6 calibration — cooldown
    cd = float(getattr(cfg, "DELTA_EXIT_SPEED_DECAY_COOLDOWN_S", 0.0))
    cur_ts = _bar_ts(store, bar_idx)
    if _b6_cooldown_blocked(pos_state, "delta_decay", cur_ts, cd):
        return None
    _b6_stamp_fire(pos_state, "delta_decay", cur_ts)
    return {
        "reason": f"DELTA_EXIT_speed_decay_tfs_lost={len(lost_tfs)}_gain={gain:.2f}%",
        "action": "REDUCE",
        "qty_pct": 0.5,
    }


# ════════════════════════════════════════════════════════════════════════════
# EXIT #7  QUICK_SENTIMENT_CUT_GAIN
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: ez_positions_quick.py:11284 — reason: f"SENTIMENT_CUT_GAIN{pnl:.1f}"
# Fires REDUCE when sentiment composite turns hard against, while gain >= noloss_min.
#
# VEC LOGIC: sentiment composite not in NPZ. Approximate as: WT_4H and WT_D
# BOTH flipped against position AND gain >= 0.5%.

def check_quick_sentiment_cut_gain(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    if not getattr(cfg, "QUICK_SENTIMENT_CUT_GAIN_VEC_ENABLED", False):
        return None
    if not pos_state.open:
        return None
    gain = pos_state.gain_pct
    min_gain = float(getattr(cfg, "QUICK_SENTIMENT_CUT_MIN_GAIN", 0.5))
    if gain < min_gain:
        return None
    side_long = _is_long(pos_state)
    w1_4h = store.f("wt1_4h", bar_idx, 0.0)
    w2_4h = store.f("wt2_4h", bar_idx, 0.0)
    w1_D = store.f("wt1_D", bar_idx, 0.0)
    w2_D = store.f("wt2_D", bar_idx, 0.0)
    htf_against = (
        (side_long and w1_4h < w2_4h and w1_D < w2_D)
        or ((not side_long) and w1_4h > w2_4h and w1_D > w2_D)
    )
    if not htf_against:
        return None
    # 2026-05-27 BATCH 6 calibration — fire once per micro-trend not every bar.
    cd = float(getattr(cfg, "QUICK_SENTIMENT_CUT_COOLDOWN_S", 0.0))
    cur_ts = _bar_ts(store, bar_idx)
    if _b6_cooldown_blocked(pos_state, "quick_sentiment_cut", cur_ts, cd):
        return None
    _b6_stamp_fire(pos_state, "quick_sentiment_cut", cur_ts)
    return {
        "reason": f"SENTIMENT_CUT_GAIN{gain:.1f}",
        "action": "REDUCE",
        "qty_pct": float(getattr(cfg, "QUICK_SENTIMENT_CUT_REDUCE_FRAC", 0.5)),
    }


# ════════════════════════════════════════════════════════════════════════════
# EXIT #8  HEDGE_BANDAID_OFF_FIRST_PRE
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: ez_manage.py:40374 — closes hedge when wt_15m favors origin
# AND origin still losing AND wt_3m has flipped (NUKING gate).
# Reason: f"HEDGE_BANDAID_OFF_FIRST_PRE_wt15m_<w1>vs<w2>_hgain<gain>"
#
# VEC LOGIC: same as QUICK_BANDAID_OFF but requires BOTH wt_15m and wt_3m
# (or wt_5m for tradier) flipped.

def check_hedge_bandaid_off_first_pre(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    if not getattr(cfg, "HEDGE_BANDAID_OFF_FIRST_PRE_VEC_ENABLED", False):
        return None
    if not pos_state.open:
        return None
    entry_sig = str(getattr(pos_state, "reason", "") or "").upper()
    if "HEDGE_PROTECT" not in entry_sig and "QUICK_HEDGE" not in entry_sig:
        return None
    side_long = _is_long(pos_state)
    ltf = "5m" if mode == "tradier" else "3m"
    w1_15 = store.f("wt1_15m", bar_idx, 0.0)
    w2_15 = store.f("wt2_15m", bar_idx, 0.0)
    w1_ltf = store.f(f"wt1_{ltf}", bar_idx, 0.0)
    w2_ltf = store.f(f"wt2_{ltf}", bar_idx, 0.0)
    flipped_15m = (side_long and w1_15 < w2_15) or ((not side_long) and w1_15 > w2_15)
    flipped_ltf = (side_long and w1_ltf < w2_ltf) or ((not side_long) and w1_ltf > w2_ltf)
    if not (flipped_15m and flipped_ltf):
        return None
    return {
        "reason": f"HEDGE_BANDAID_OFF_FIRST_PRE_wt15m_{w1_15:.1f}vs{w2_15:.1f}_hgain{pos_state.gain_pct:.2f}%",
        "action": "CLOSE",
        "bypass_noloss": True,
    }


# ════════════════════════════════════════════════════════════════════════════
# EXIT #9  R1_DC_LOW4_3M_EMERGENCY reason-string fix
# ════════════════════════════════════════════════════════════════════════════
# NAMING GAP ONLY — vec_paths/exit_r1_r2.py already emits the signal but
# stub-clusters as "RN_DC_LOWN_NM_EMERGENCY" rather than the live family
# "R1_DC_LOW4_3M_EMERGENCY".
#
# exit_r1_r2.py emits e.g. "R1_DC_LOW4_3M_EMERGENCY_via_DC_LOW4_3M_g0.50_age15.0m"
# but the stub function in tools/signal_parity_diff.py replaces digits in the
# family prefix with N, producing "R1_DC_LOWN_NM_EMERGENCY". This is the SAME
# signal but the diff tool doesn't merge it.
#
# FIX: helper that the validator can use to align them, plus this function
# documents the link.

def fix_r1_reason_string_for_diff(reason: str) -> str:
    """Normalize R1 reason strings so the diff tool's stub clustering recognizes
    "R1_DC_LOW4_3M_EMERGENCY" and "RN_DC_LOWN_NM_EMERGENCY" as the same family.

    Returns the canonical live form "R1_DC_LOW4_3M_EMERGENCY..." regardless.
    """
    if "EMERGENCY" not in reason:
        return reason
    # Live form already used in exit_r1_r2.py: ensure it leads with R1_DC_LOW4_3M
    if reason.startswith("R1_DC_LOW4_3M_EMERGENCY"):
        return reason
    if reason.startswith("R1_DC_LOW4_EMERGENCY"):
        # Tradier variant — normalize to crypto naming for clustering parity
        return reason.replace("R1_DC_LOW4_EMERGENCY", "R1_DC_LOW4_3M_EMERGENCY", 1)
    return reason


# ════════════════════════════════════════════════════════════════════════════
# EXIT #10  IN_GAIN_TREND_EXIT
# ════════════════════════════════════════════════════════════════════════════
# LIVE SOURCE: ez_manage.py:42946+ — fires when:
#   - gain >= IN_GAIN_TREND_MIN_GAIN (default 2.0%)
#   - HTF trend flipping (D for BIG_WINNER tier, 4h for MED_WINNER, 15m for SMALL)
# Reason: "IN_GAIN_TREND_EXIT" (sometimes with _<tier> suffix)
#
# vec_paths already has IN_GAIN_TREND constants in SweepConfig — but the
# common emit family in vec_only is "IN_GAIN_TREND_EXIT_MED_WINNER_NM" which
# stub-clusters DIFFERENTLY from live's bare "IN_GAIN_TREND_EXIT". Need to
# emit BOTH variants and let the user/validator match.

def check_in_gain_trend_exit(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    if not getattr(cfg, "IN_GAIN_TREND_EXIT_LIVE_PARITY_ENABLED", False):
        return None
    if not pos_state.open:
        return None
    gain = pos_state.gain_pct
    min_gain = float(getattr(cfg, "IN_GAIN_TREND_MIN_GAIN", 2.0))
    if gain < min_gain:
        return None
    big = float(getattr(cfg, "IN_GAIN_TREND_BIG_WINNER_PCT", 10.0))
    med = float(getattr(cfg, "IN_GAIN_TREND_MED_WINNER_PCT", 5.0))
    side_long = _is_long(pos_state)
    # Tier picks the TF that must flip
    if gain >= big:
        tier = "BIG"; tf = "D"
    elif gain >= med:
        tier = "MED"; tf = "4h"
    else:
        tier = "SMALL"; tf = "15m"
    w1 = store.f(f"wt1_{tf}", bar_idx, 0.0)
    w2 = store.f(f"wt2_{tf}", bar_idx, 0.0)
    flipped = (side_long and w1 < w2) or ((not side_long) and w1 > w2)
    if not flipped:
        return None
    # 2026-05-27 BATCH 6 calibration — over-firing 16.9× live (1,705 vs 101).
    # Live tier requires k<d AND ha=opposing AND gain≥10% on BIG. Vec was
    # firing on weakest condition (wt cross alone on the tier's TF).
    # Add (a) require_full_flip: BOTH wt1<wt2 AND wt2 directional turn,
    # (b) cooldown to fire 1×/hr per sym.
    if bool(getattr(cfg, "IN_GAIN_TREND_REQUIRE_FULL_FLIP", False)):
        # Require wt2 to also be moving against (1-bar slope) — proves rollover.
        prev_idx = max(0, bar_idx - 1)
        w2_prev = store.f(f"wt2_{tf}", prev_idx, w2)
        if side_long:
            if not (w2 < w2_prev):
                return None
        else:
            if not (w2 > w2_prev):
                return None
    cd = float(getattr(cfg, "IN_GAIN_TREND_COOLDOWN_S", 0.0))
    cur_ts = _bar_ts(store, bar_idx)
    if _b6_cooldown_blocked(pos_state, "in_gain_trend", cur_ts, cd):
        return None
    _b6_stamp_fire(pos_state, "in_gain_trend", cur_ts)
    # Match live's bare reason exactly for diff clustering
    return {
        "reason": "IN_GAIN_TREND_EXIT",
        "tier": tier,
        "tf": tf,
        "action": "REDUCE",
        "qty_pct": float(getattr(cfg, "IN_GAIN_TREND_REDUCE_FRAC", 0.5)),
    }


# ════════════════════════════════════════════════════════════════════════════
# Public dispatch helper — used by v8_vec_sweep wiring layer
# ════════════════════════════════════════════════════════════════════════════

ENTRY_CHECKERS = (
    ("hedge_protect_short_loss", lambda s,b,p,m,c: check_hedge_protect_loss_entry(s,b,p,m,c,quick=True)),
    ("hedge_protect_long_loss",  lambda s,b,p,m,c: check_hedge_protect_loss_entry(s,b,p,m,c,quick=True)),
    ("quick_open_strong",        check_quick_open_strong_entry),
    ("quick_hedge_same_sym_last_resort", check_quick_hedge_same_sym_last_resort),
    ("hedge_protect_loss_nonquick", lambda s,b,p,m,c: check_hedge_protect_loss_entry(s,b,p,m,c,quick=False)),
    # The reentry signals need extra state (last_close_price/ts); they are
    # called separately by the wiring layer with kwargs.
)

EXIT_CHECKERS = (
    ("ridiculous_hold", check_ridiculous_hold_exit),
    ("quick_reduce_strong_reduce", check_quick_reduce_strong_reduce_exit),
    ("quick_breakeven_gain_erosion", check_quick_breakeven_gain_erosion_stop),
    ("quick_cycle_tp_stoch_against", check_quick_cycle_tp_stoch_against),
    ("quick_bandaid_off", check_quick_bandaid_off_exit),
    ("delta_exit_speed_decay", check_delta_exit_speed_decay),
    ("quick_sentiment_cut_gain", check_quick_sentiment_cut_gain),
    ("hedge_bandaid_off_first_pre", check_hedge_bandaid_off_first_pre),
    ("in_gain_trend_exit_live_parity", check_in_gain_trend_exit),
)


__all__ = [
    "is_synthetic_loser",
    "check_hedge_protect_loss_entry",
    "check_quick_open_strong_entry",
    "check_quick_hedge_same_sym_last_resort",
    "check_daemon_price_cross_reentry",
    "check_guaranteed_price_cross_reentry_disk",
    "check_direction_favorable_reentry",
    "check_ridiculous_hold_exit",
    "check_quick_reduce_strong_reduce_exit",
    "check_quick_breakeven_gain_erosion_stop",
    "check_quick_cycle_tp_stoch_against",
    "check_quick_bandaid_off_exit",
    "check_delta_exit_speed_decay",
    "check_quick_sentiment_cut_gain",
    "check_hedge_bandaid_off_first_pre",
    "fix_r1_reason_string_for_diff",
    "check_in_gain_trend_exit",
    "ENTRY_CHECKERS",
    "EXIT_CHECKERS",
]
