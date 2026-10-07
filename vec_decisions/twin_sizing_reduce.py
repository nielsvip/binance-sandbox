"""twin_sizing_reduce.py — SHARED stocks-live twins for 13 sizing/reduce/exit switches.

Stocks-live was missing these (crypto-live + vector EXIST); this module ports the
crypto-live predicate logic into tradier semantics so tradier_manage hooks and the
vector engine share ONE predicate family (BACKTEST_BIBLE section 39 recipe).

Covered switches (evidence /tmp/ev_sizing-reduce.json, all WIRED/VEC_ONLY):
  SIZING  ATR_ADAPTIVE_SIZING_ENABLED, EMA_DIST_SIZING_ENABLED, FIXED_QUANTITY_ENABLED
  REDUCE  CYCLE_TP_TIERED_ENABLED, PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT (+PPL family),
          SATOSHIT_EXIT_ENABLED (partial 70%)
  EXIT    E_1_WT_EXIT_USE_DELTA_ENABLED, HTF_AGAINST_FORCE_CLOSE_ENABLED,
          NEWBORN_LOSS_KILL_ENABLED, WT_PERCENTILE_EXIT_ENABLED
  AUGMENT PYRAMID_ENABLED
  ENTRY   MOMENTUM_SMA_WATCHDOG_ENABLED, MOMENTUM_SMA_WATCHDOG_PCT (predicate only;
          tradier has no watchdog loop — hook placement is an operator decision)

Tradier-semantics mappings (crypto 3m TF does not exist on stocks; analyzed TFs are
1m/5m/15m/1h/4h/D):
  wt1_3m/wt2_3m -> wt1_5m/wt2_5m, wt_velocity_3m -> wt_velocity_5m,
  ema_20_3m (TF_FOCUS) -> ema_20_5m, stoch 3m -> 5m, mfi_3m -> mfi_5m.
Sizing twins mirror VEC math (compute_regime_sizing_mult); exit/reduce/augment twins
mirror crypto-live (ez_manage inline / strategy_enhancements / ez_satoshit).

Contract: every public predicate takes the per-sym config getter as `get` (scan
reads literal keys only), is pure (state in/out, no module globals mutated except
none), fail-open (any error -> inert), and default-inert (disabled -> False/1.0/None).
Honest per BIBLE 19: missing data -> no fire, never a fabricated signal.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Tuple

Get = Callable[[str, Any], Any]

_EPS = 1e-9
_TIERED_DEFAULT_LEVELS = (0.0015, 0.003, 0.005, 0.007, 0.010, 0.015, 0.020, 0.030)
_WD_TF_ORDER = ("15m", "1h", "4h", "D")


def _f(x: Any, d: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return d
    if v != v or v in (float("inf"), float("-inf")):
        return d
    return v


def _alt(ind: Mapping[str, Any], primary: str, secondary: str, d: float = 0.0) -> float:
    if ind is None:
        return d
    try:
        if primary in ind and ind.get(primary) is not None:
            return _f(ind.get(primary), d)
        if secondary in ind and ind.get(secondary) is not None:
            return _f(ind.get(secondary), d)
    except Exception:
        return d
    return d


def fixed_quantity_active(get: Get) -> bool:
    """FIXED_QUANTITY bypass: live returns base_quantity before any sizing mult."""
    try:
        return bool(get("FIXED_QUANTITY_ENABLED", False))
    except Exception:
        return False


def atr_adaptive_size_mult(get: Get, ind: Mapping[str, Any] | None, price: float) -> float:
    """ATR adaptive sizing multiplier — VEC math (clip 0.25..4.0 of target/atr%)."""
    try:
        if not bool(get("ATR_ADAPTIVE_SIZING_ENABLED", False)):
            return 1.0
        tf = str(get("ATR_ADAPTIVE_STOP_TF", "1h") or "1h").strip().lower()
        if tf in ("15m", "15"):
            atr_key = "atr_15m"
        elif tf in ("4h", "4"):
            atr_key = "atr_4h"
        elif tf in ("d", "1d", "daily"):
            atr_key = "atr_D"
        elif tf in ("5m", "5"):
            atr_key = "atr_5m"
        else:
            atr_key = "atr_1h"
        atr = _f((ind or {}).get(atr_key), 0.0)
        px = _f(price, 0.0)
        if atr <= 0.0 or px <= 0.0:
            return 1.0
        atr_pct = atr / px * 100.0
        if atr_pct <= 0.0:
            return 1.0
        target = _f(get("ATR_ADAPTIVE_SIZING_TARGET_PCT", 1.5), 1.5)
        if target <= 0.0:
            return 1.0
        return min(4.0, max(0.25, target / atr_pct))
    except Exception:
        return 1.0


def ema_dist_size_mult(get: Get, ind: Mapping[str, Any] | None, price: float) -> float:
    """EMA-distance sizing multiplier — VEC math: 1 + dist_pct/100 * MULT (ema_20_1h)."""
    try:
        if not bool(get("EMA_DIST_SIZING_ENABLED", False)):
            return 1.0
        ema = _f((ind or {}).get("ema_20_1h"), 0.0)
        px = _f(price, 0.0)
        if ema <= 0.0 or px <= 0.0:
            return 1.0
        dist = abs(px - ema) / max(ema, _EPS) * 100.0
        mult = _f(get("EMA_DIST_SIZING_MULT", 2.0), 2.0)
        return 1.0 + dist / 100.0 * mult
    except Exception:
        return 1.0


def e1_wt_delta_fires(get: Get, ind: Mapping[str, Any] | None, is_long: bool) -> Tuple[bool, str]:
    """E-1 exit when wt_composite_delta crosses threshold against the position."""
    try:
        if not bool(get("E_1_WT_EXIT_USE_DELTA_ENABLED", False)):
            return False, ""
        if ind is None or "wt_composite_delta" not in ind:
            return False, ""
        try:
            delta = float(ind.get("wt_composite_delta"))
        except (TypeError, ValueError):
            return False, ""
        if delta != delta:
            return False, ""
        thr = _f(get("E_1_EXIT_DELTA_THR", 50.0), 50.0)
        fire = (is_long and delta < -thr) or ((not is_long) and delta > thr)
        if fire:
            return True, f"E_1_WT_DELTA_EXIT_delta={delta:+.0f}_thr={thr:.0f}"
        return False, ""
    except Exception:
        return False, ""


def htf_against_fires(get: Get, ind: Mapping[str, Any] | None, is_long: bool) -> Tuple[bool, str]:
    """1h WT flip against the side closes immediately (gain-agnostic), with 15m/4h/5m/D confirms."""
    try:
        if not bool(get("HTF_AGAINST_FORCE_CLOSE_ENABLED", False)):
            return False, ""
        m = ind or {}
        w1 = _f(m.get("wt1_1h"), 0.0)
        w2 = _f(m.get("wt2_1h"), 0.0)
        data = abs(w1) > _EPS or abs(w2) > _EPS
        against = data and ((is_long and w1 < w2) or ((not is_long) and w1 > w2))
        if not against:
            return False, ""
        confirm_ok = True
        if bool(get("WT_CROSS_EXIT_REQUIRE_15M_CONFIRM", True)):
            c1 = _f(m.get("wt1_15m"), 0.0)
            c2 = _f(m.get("wt2_15m"), 0.0)
            confirm_ok = (is_long and c1 < c2) or ((not is_long) and c1 > c2)
        if confirm_ok and bool(get("HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H", True)):
            c1 = _f(m.get("wt1_4h"), 0.0)
            c2 = _f(m.get("wt2_4h"), 0.0)
            confirm_ok = (is_long and c1 < c2) or ((not is_long) and c1 > c2)
        if confirm_ok and bool(get("HTF_AGAINST_FORCE_CLOSE_CONFIRM_3M", True)):
            c1 = _f(m.get("wt1_5m"), 0.0)
            c2 = _f(m.get("wt2_5m"), 0.0)
            if abs(c1) > _EPS or abs(c2) > _EPS:
                confirm_ok = (is_long and c1 < c2) or ((not is_long) and c1 > c2)
        if confirm_ok and bool(get("HTF_AGAINST_FORCE_CLOSE_CONFIRM_D", True)):
            c1 = _f(m.get("wt1_D"), 0.0)
            c2 = _f(m.get("wt2_D"), 0.0)
            if abs(c1) > _EPS or abs(c2) > _EPS:
                confirm_ok = (is_long and c1 < c2) or ((not is_long) and c1 > c2)
        if confirm_ok:
            return True, f"HTF_AGAINST_FORCE_CLOSE_wt1h{w1:.1f}vs{w2:.1f}"
        return False, ""
    except Exception:
        return False, ""


def newborn_kill_fires(get: Get, ind: Mapping[str, Any] | None, is_long: bool, age_min: float, gain_pct: float, is_hedge: bool) -> Tuple[bool, str]:
    """Kill a newborn position that is red with velocity against it (HTF-WT veto applies)."""
    try:
        if bool(is_hedge):
            return False, ""
        if not bool(get("NEWBORN_LOSS_KILL_ENABLED", False)):
            return False, ""
        age = _f(age_min, -1.0)
        gain = _f(gain_pct, 0.0)
        window = _f(get("NEWBORN_LOSS_KILL_WINDOW_MIN", 30.0), 30.0)
        threshold = _f(get("NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT", 0.0), 0.0)
        if not (0.0 <= age <= window):
            return False, ""
        if not (gain <= threshold):
            return False, ""
        vel_ok = True
        vel_used = 0.0
        if bool(get("NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST", True)):
            vel_tf = str(get("NEWBORN_LOSS_KILL_VEL_TF", "") or "3m").strip().lower()
            if vel_tf in ("5m", "5"):
                vel_key = "wt_velocity_5m"
            elif vel_tf in ("15m", "15"):
                vel_key = "wt_velocity_15m"
            elif vel_tf in ("1h", "1"):
                vel_key = "wt_velocity_1h"
            elif vel_tf in ("4h", "4"):
                vel_key = "wt_velocity_4h"
            else:
                vel_key = "wt_velocity_5m"
            vel_used = _f((ind or {}).get(vel_key), 0.0)
            vel_ok = (is_long and vel_used < 0.0) or ((not is_long) and vel_used > 0.0)
        if not vel_ok:
            return False, ""
        if bool(get("BOTTOM_EXIT_HTF_WT_VETO_ENABLED", True)):
            m = ind or {}
            h1 = _f(m.get("wt1_1h"), 0.0)
            h2 = _f(m.get("wt2_1h"), 0.0)
            f1 = _f(m.get("wt1_15m"), 0.0)
            f2 = _f(m.get("wt2_15m"), 0.0)
            q1 = _f(m.get("wt1_4h"), 0.0)
            q2 = _f(m.get("wt2_4h"), 0.0)
            if is_long:
                with_pos = h1 > h2 or f1 > f2 or q1 > q2
            else:
                with_pos = h1 < h2 or f1 < f2 or q1 < q2
            if with_pos:
                return False, ""
        return True, f"NEWBORN_LOSS_KILL_age{age:.1f}m_gain{gain:.2f}_vel{vel_used:.2f}"
    except Exception:
        return False, ""


def wt_percentile_fires(get: Get, ind: Mapping[str, Any] | None, is_long: bool) -> Tuple[bool, str]:
    """Close when D + 4h WT percentile is OB (long) / OS (short) with 15m turn confirm."""
    try:
        if not bool(get("WT_PERCENTILE_EXIT_ENABLED", False)):
            return False, ""
        m = ind or {}
        pct_d = _f(m.get("wt_percentile_D"), 50.0)
        pct_4h = _f(m.get("wt_percentile_4h"), 50.0)
        w1 = _f(m.get("wt1_15m"), 50.0)
        w2 = _f(m.get("wt2_15m"), 50.0)
        fire = False
        if is_long:
            ob_d = _f(get("WT_PERCENTILE_EXIT_OB_D", 75.0), 75.0)
            ob_4h = _f(get("WT_PERCENTILE_EXIT_OB_4H", 55.0), 55.0)
            fire = pct_d > ob_d and pct_4h > ob_4h and w1 < w2
        else:
            os_d = _f(get("WT_PERCENTILE_EXIT_OS_D", 10.0), 10.0)
            os_4h = _f(get("WT_PERCENTILE_EXIT_OS_4H", 25.0), 25.0)
            fire = pct_d < os_d and pct_4h < os_4h and w1 > w2
        if fire:
            return True, f"WT_PERCENTILE_pctD={pct_d:.0f}_4h={pct_4h:.0f}"
        return False, ""
    except Exception:
        return False, ""


def cycle_tp_tier(get: Get, gain_pct: float, fired: Any) -> Optional[Tuple[int, float, float]]:
    """First unfired tiered-TP level at/above current gain. Returns (idx, level_frac, frac)."""
    try:
        if not bool(get("CYCLE_TP_TIERED_ENABLED", False)):
            return None
        gain = _f(gain_pct, 0.0)
        noloss_min = _f(get("NOLOSS_MIN_PROFIT_PCT", 0.0), 0.0)
        if not (gain > max(0.05, noloss_min)):
            return None
        try:
            levels = list(get("CYCLE_TP_TIERED_LEVELS", list(_TIERED_DEFAULT_LEVELS)))
        except Exception:
            levels = list(_TIERED_DEFAULT_LEVELS)
        if not levels:
            return None
        frac = _f(get("CYCLE_TP_TIERED_FRAC", 0.25), 0.25)
        if frac <= 0.0:
            return None
        try:
            done = set(fired or set())
        except Exception:
            done = set()
        gain_frac = gain / 100.0
        for idx, level in enumerate(levels):
            if idx in done:
                continue
            try:
                lvl = float(level)
            except (TypeError, ValueError):
                continue
            if gain_frac >= lvl:
                return idx, lvl, frac
        return None
    except Exception:
        return None


def ppl_step(get: Get, gain_pct: float, price: float, entry_px: float, pos_amt: float, min_qty: float, state: Mapping[str, Any] | None, account_ok: bool) -> Dict[str, Any]:
    """Partial-profit-lock 3-phase step. Returns {action, qty, reason, state}."""
    try:
        st = dict(state or {})
        out_state = dict(st)
        gain = _f(gain_pct, 0.0)
        px = _f(price, 0.0)
        entry = _f(entry_px, 0.0)
        amt = abs(_f(pos_amt, 0.0))
        mq = _f(min_qty, 1.0)
        if not bool(account_ok):
            return {"action": "none", "qty": 0.0, "reason": "", "state": out_state}
        if not bool(get("PARTIAL_PROFIT_LOCK_ENABLED", False)):
            return {"action": "none", "qty": 0.0, "reason": "", "state": out_state}
        min_gain = _f(get("PARTIAL_PROFIT_LOCK_GAIN_PCT", 1.5), 1.5)
        arm_gain = _f(get("PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT", 1.75), 1.75)
        be_buffer = _f(get("PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT", 0.1), 0.1)
        frac = _f(get("PARTIAL_PROFIT_LOCK_FRAC", 0.5), 0.5)
        fired = bool(st.get("fired", False))
        first_exit_px = _f(st.get("first_exit_price", 0.0), 0.0)
        stop_level = _f(st.get("stop_level", 0.0), 0.0)
        upgraded = bool(st.get("stop_upgraded", False))
        if not fired and gain >= min_gain and amt > mq and entry > 0.0 and 0.0 < frac < 1.0:
            reduce_qty = amt * frac
            keep_qty = amt - reduce_qty
            if reduce_qty > mq and keep_qty > mq and px > 0.0:
                return {"action": "reduce", "qty": float(reduce_qty), "reason": f"PPL_TP_gain{gain:.2f}", "state": out_state,
                        "arm": {"first_exit_price": float(px), "frac": float(frac), "entry_px": float(entry), "gain": float(gain), "be_buffer": float(be_buffer)}}
        if fired and not upgraded and gain >= arm_gain and first_exit_px > 0.0:
            out_state["stop_level"] = float(first_exit_px)
            out_state["stop_upgraded"] = True
            return {"action": "upgrade", "qty": 0.0, "reason": f"PPL_STOP_UPGRADED_gain{gain:.2f}_stop{first_exit_px:.6f}", "state": out_state}
        if fired and stop_level > 0.0 and amt > mq and px > 0.0:
            return {"action": "check_stop", "qty": 0.0, "reason": "", "state": out_state, "stop_level": float(stop_level), "upgraded": bool(upgraded)}
        return {"action": "none", "qty": 0.0, "reason": "", "state": out_state}
    except Exception:
        try:
            return {"action": "none", "qty": 0.0, "reason": "", "state": dict(state or {})}
        except Exception:
            return {"action": "none", "qty": 0.0, "reason": "", "state": {}}


def ppl_be_stop(is_long: bool, entry_px: float, be_buffer_pct: float) -> float:
    """Breakeven stop with buffer: entry*(1+buf%) long, entry*(1-buf%) short."""
    try:
        entry = _f(entry_px, 0.0)
        buf = _f(be_buffer_pct, 0.0)
        if entry <= 0.0:
            return 0.0
        if is_long:
            return entry * (1.0 + buf / 100.0)
        return entry * (1.0 - buf / 100.0)
    except Exception:
        return 0.0


def ppl_stop_hit(is_long: bool, price: float, stop_level: float) -> bool:
    """True when price touches the PPL stop (long: at/below; short: at/above)."""
    try:
        px = _f(price, 0.0)
        stop = _f(stop_level, 0.0)
        if px <= 0.0 or stop <= 0.0:
            return False
        if is_long:
            return px <= stop
        return px >= stop
    except Exception:
        return False


def satoshit_fires(get: Get, ind: Mapping[str, Any] | None, is_long: bool, gain_pct: float, account_ok: bool) -> Tuple[bool, str, float]:
    """Exit-at-top: stoch cross down from OB (long) / up from OS (short) + MFI turn. Returns (fire, reason, frac)."""
    try:
        if not bool(account_ok):
            return False, "", 0.0
        if not bool(get("SATOSHIT_EXIT_ENABLED", False)):
            return False, "", 0.0
        if not bool(get("SATOSHIT_ENABLED", False)):
            return False, "", 0.0
        gain = _f(gain_pct, 0.0)
        noloss_min = _f(get("NOLOSS_MIN_PROFIT_PCT", 0.0), 0.0)
        if not (gain > noloss_min):
            return False, "", 0.0
        m = ind or {}
        k_5m = _alt(m, "k_5m", "stoch_k_5m", 50.0)
        k_5m_prev = _alt(m, "k_5m_prev", "stoch_k_5m_prev", 50.0)
        d_5m = _alt(m, "d_5m", "stoch_d_5m", 50.0)
        k_1m = _alt(m, "k_1m", "stoch_k_1m", 50.0)
        k_1m_prev = _alt(m, "k_1m_prev", "stoch_k_1m_prev", 50.0)
        d_1m = _alt(m, "d_1m", "stoch_d_1m", 50.0)
        mfi = _f(m.get("mfi_5m"), 50.0)
        if "mfi_5m_prev" not in m and "mfi_5m_ant" not in m:
            return False, "", 0.0
        mfi_prev = _f(m.get("mfi_5m_prev", m.get("mfi_5m_ant")), 50.0)
        frac = _f(get("SATOSHIT_EXIT_PARTIAL_PCT", 0.7), 0.7)
        if is_long:
            was_ob = k_5m_prev >= 80.0 or k_1m_prev >= 85.0
            cross_5m = k_5m_prev >= d_5m and k_5m < d_5m
            cross_1m = k_1m_prev >= d_1m and k_1m < d_1m
            mfi_turn = mfi < mfi_prev
            if was_ob and (cross_5m or cross_1m) and mfi_turn:
                tag = "5m" if cross_5m else "1m"
                return True, f"SATOSHIT_EXIT_LONG_TOP_{tag}_k{k_5m:.0f}_d{d_5m:.0f}_mfi{mfi:.0f}", frac
        else:
            was_os = k_5m_prev <= 20.0 or k_1m_prev <= 15.0
            cross_5m = k_5m_prev <= d_5m and k_5m > d_5m
            cross_1m = k_1m_prev <= d_1m and k_1m > d_1m
            mfi_turn = mfi > mfi_prev
            if was_os and (cross_5m or cross_1m) and mfi_turn:
                tag = "5m" if cross_5m else "1m"
                return True, f"SATOSHIT_EXIT_SHORT_BOTTOM_{tag}_k{k_5m:.0f}_d{d_5m:.0f}_mfi{mfi:.0f}", frac
        return False, "", 0.0
    except Exception:
        return False, "", 0.0


def pyramid_fires(get: Get, ind: Mapping[str, Any] | None, is_long: bool, gain_pct: float) -> Tuple[bool, str, float]:
    """Add into structural strength: min gain + 1h WT velocity with side + 15m DC position extreme."""
    try:
        if not bool(get("PYRAMID_ENABLED", False)):
            return False, "", 0.0
        gain = _f(gain_pct, 0.0)
        min_gain = _f(get("PYRAMID_MIN_GAIN_PCT", 1.5), 1.5)
        if gain < min_gain:
            return False, "", 0.0
        m = ind or {}
        vel = _f(m.get("wt_velocity_1h"), 0.0)
        dc_pos = _f(m.get("dc_position_15m"), 0.5)
        min_vel = _f(get("PYRAMID_MIN_WT_VEL_1H", 2.0), 2.0)
        size_mult = _f(get("PYRAMID_SIZE_MULT", 0.5), 0.5)
        if is_long:
            min_dc = _f(get("PYRAMID_MIN_DC_POS_15M", 0.7), 0.7)
            fire = vel >= min_vel and dc_pos >= min_dc
        else:
            max_dc = _f(get("PYRAMID_MAX_DC_POS_15M_SHORT", 0.3), 0.3)
            fire = vel <= -min_vel and dc_pos <= max_dc
        if fire:
            side = "LONG" if is_long else "SHORT"
            return True, f"PYRAMID_{side}_gain={gain:.2f}_vel={vel:.1f}_dcpos={dc_pos:.2f}", size_mult
        return False, "", 0.0
    except Exception:
        return False, "", 0.0


def watchdog_open(get: Get, ind: Mapping[str, Any] | None, is_long: bool, price: float) -> Optional[Dict[str, Any]]:
    """Force-open trigger: largest-TF Donchian breakout wins, else sma_200_15m +/-pct + 5m WT cross in favor."""
    try:
        if not bool(get("MOMENTUM_SMA_WATCHDOG_ENABLED", False)):
            return None
        px = _f(price, 0.0)
        if px <= 0.0:
            return None
        m = ind or {}
        base_usd = _f(get("WATCHDOG_DC_BASE_USD", 25.0), 25.0)
        max_usd = _f(get("WATCHDOG_DC_MAX_USD", 600.0), 600.0)
        try:
            want_tfs = list(get("WATCHDOG_DC_TFS", ["15m", "1h", "4h", "D"]))
        except Exception:
            want_tfs = ["15m", "1h", "4h", "D"]
        want = set(str(t) for t in (want_tfs or []))
        hit_tf = None
        if bool(get("WATCHDOG_DC_FORCE_OPEN_ENABLED", False)):
            for tf in _WD_TF_ORDER:
                if tf not in want:
                    continue
                if is_long:
                    if tf == "15m":
                        lvl = _f(m.get("dc_high_15m"), 0.0)
                    elif tf == "1h":
                        lvl = _f(m.get("dc_high_1h"), 0.0)
                    elif tf == "4h":
                        lvl = _f(m.get("dc_high_4h"), 0.0)
                    else:
                        lvl = _f(m.get("dc_high_D"), 0.0)
                    if tf == "15m":
                        cross = bool(m.get("dc_high_crossover_15m", False))
                    elif tf == "1h":
                        cross = bool(m.get("dc_high_crossover_1h", False))
                    elif tf == "4h":
                        cross = bool(m.get("dc_high_crossover_4h", False))
                    else:
                        cross = bool(m.get("dc_high_crossover_D", False))
                    if (lvl > 0.0 and px >= lvl) or cross:
                        hit_tf = tf
                else:
                    if tf == "15m":
                        lvl = _f(m.get("dc_low_15m"), 0.0)
                    elif tf == "1h":
                        lvl = _f(m.get("dc_low_1h"), 0.0)
                    elif tf == "4h":
                        lvl = _f(m.get("dc_low_4h"), 0.0)
                    else:
                        lvl = _f(m.get("dc_low_D"), 0.0)
                    if tf == "15m":
                        cross = bool(m.get("dc_low_crossunder_15m", False))
                    elif tf == "1h":
                        cross = bool(m.get("dc_low_crossunder_1h", False))
                    elif tf == "4h":
                        cross = bool(m.get("dc_low_crossunder_4h", False))
                    else:
                        cross = bool(m.get("dc_low_crossunder_D", False))
                    if (lvl > 0.0 and px <= lvl) or cross:
                        hit_tf = tf
        if hit_tf is not None:
            if hit_tf == "15m":
                tf_mult = _f(get("WATCHDOG_DC_MULT_15M", 1.0), 1.0)
            elif hit_tf == "1h":
                tf_mult = _f(get("WATCHDOG_DC_MULT_1H", 4.0), 4.0)
            elif hit_tf == "4h":
                tf_mult = _f(get("WATCHDOG_DC_MULT_4H", 8.0), 8.0)
            else:
                tf_mult = _f(get("WATCHDOG_DC_MULT_D", 16.0), 16.0)
            usd = min(base_usd * tf_mult, max_usd)
            return {"trigger": "DC_" + hit_tf + "_BREAKOUT", "usd": float(usd), "qty": float(usd / px) if px > 0.0 else 0.0}
        sma = _f(m.get("sma_200_15m"), 0.0)
        w1 = _f(m.get("wt1_5m"), 0.0)
        w2 = _f(m.get("wt2_5m"), 0.0)
        cross_fav = (is_long and w1 > w2) or ((not is_long) and w1 < w2)
        pct = _f(get("MOMENTUM_SMA_WATCHDOG_PCT", 1.0), 1.0) / 100.0
        if is_long:
            sma_ok = sma > 0.0 and px > sma * (1.0 + pct)
        else:
            sma_ok = sma > 0.0 and px < sma * (1.0 - pct)
        if sma_ok and cross_fav:
            return {"trigger": "SMA15M_WT5M", "usd": float(base_usd), "qty": float(base_usd / px) if px > 0.0 else 0.0}
        return None
    except Exception:
        return None
