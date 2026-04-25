"""
scalp_v3.py — Ultra-short bar-based scalper for inf account.

Pure functions. No Redis, no ez_manage imports, no live dependencies.
Backtest harness and (eventually) live wiring both call these.

Principles (per user directive 2026-04-22):
- BAR structure (HH/HL/LL/LH) is the trigger. K is overextension context, never trigger.
- NO crossunder logic anywhere. "Crossunder" = lagging indicator = every failed system.
- Exits hair-triggered on latest bar. Reentry allowed immediately unless k_15m falling.
- K as config: 98/95 thresholds for exit (LONG); mirrored to 2/5 for SHORT in-code.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple, Sequence

Bar = Tuple[float, float, float, float, float, float]


@dataclass
class V3Input:
    symbol: str
    side: str
    bars_1m: Sequence[Bar]
    bars_3m: Sequence[Bar]
    bars_15m: Sequence[Bar]
    k_1m: float
    k_1m_prev: float
    k_3m: float
    k_3m_prev: float
    k_15m: float
    k_15m_prev: float
    k_15m_prev2: float
    k_1h: float
    k_4h: float
    now_ts: float
    current_price: float


@dataclass
class V3Position:
    side: str
    entry_price: float
    entry_ts: float
    entry_k_15m: float
    peak_gain_pct: float = 0.0  # 2026-04-24 winners: tracks peak gain for peak-giveback exit


@dataclass
class V3ExitState:
    last_exit_ts: float
    k_15m_at_exit: float
    k_15m_prev_at_exit: float


_PATTERN_INVERSE = {
    "HH_AND_HL": "LL_AND_LH",
    "HH_OR_HL": "LL_OR_LH",
    "HH": "LL",
    "LL_AND_LH": "HH_AND_HL",
    "LL_OR_LH": "HH_OR_HL",
    "LL": "HH",
}


def _bar_pattern_match(prev: Bar, curr: Bar, pattern: str, side: str) -> bool:
    p = pattern if side == "LONG" else _PATTERN_INVERSE.get(pattern, pattern)
    hh = curr[2] > prev[2]
    hl = curr[3] > prev[3]
    lh = curr[2] < prev[2]
    ll = curr[3] < prev[3]
    if p == "HH_AND_HL": return hh and hl
    if p == "HH_OR_HL":  return hh or hl
    if p == "HH":        return hh
    if p == "LL_AND_LH": return ll and lh
    if p == "LL_OR_LH":  return ll or lh
    if p == "LL":        return ll
    return False


def _k_oversold(k: float, max_threshold: float, side: str) -> bool:
    if side == "LONG": return k < max_threshold
    return k > (100.0 - max_threshold)


def _k_overbought(k: float, min_threshold: float, side: str) -> bool:
    if side == "LONG": return k > min_threshold
    return k < (100.0 - min_threshold)


def _volume_avg(bars: Sequence[Bar], n: int) -> float:
    if not bars: return 0.0
    tail = list(bars)[-n:]
    if not tail: return 0.0
    return sum(b[5] for b in tail) / len(tail)


def _compute_gain_pct(pos: V3Position, current_price: float) -> float:
    if pos.entry_price <= 0: return 0.0
    if pos.side == "LONG":
        return (current_price - pos.entry_price) / pos.entry_price * 100.0
    return (pos.entry_price - current_price) / pos.entry_price * 100.0


# ═════ 2026-04-24 WINNER TECHNIQUES (ATR TP, VWAP dev, BB squeeze, pin-bar, peak-giveback) ═════
# All optional — live/paper supply extra indicator data via `indicators` dict arg.
# Each returns (pass_or_trigger, reason).

def _vwap_deviation_filter(price: float, vwap: Optional[float], side: str, min_pct: float) -> Tuple[bool, str]:
    """LONG requires price BELOW vwap by ≥min_pct (oversold). SHORT requires price ABOVE."""
    if min_pct <= 0 or vwap is None or vwap <= 0 or price <= 0:
        return True, "vwap_skip"
    dev_pct = (price - vwap) / vwap * 100.0
    if side == "LONG":
        if dev_pct > -min_pct:
            return False, f"VWAP_NOT_DEEP_ENOUGH_dev={dev_pct:+.2f}%_need<=-{min_pct:.2f}%"
    else:
        if dev_pct < min_pct:
            return False, f"VWAP_NOT_HIGH_ENOUGH_dev={dev_pct:+.2f}%_need>=+{min_pct:.2f}%"
    return True, f"vwap_dev={dev_pct:+.2f}%"


def _bb_squeeze_filter(bbw_pct: Optional[float], max_pct: float) -> Tuple[bool, str]:
    """Require BB width <= max_pct (compression). 0=disabled."""
    if max_pct <= 0: return True, "bb_skip"
    if bbw_pct is None: return True, "bb_no_data"
    if bbw_pct > max_pct:
        return False, f"BB_NOT_SQUEEZED_width={bbw_pct:.2f}%_max={max_pct:.2f}%"
    return True, f"bb_squeezed={bbw_pct:.2f}%"


def _pin_bar_filter(bar: Bar, side: str, min_ratio: float) -> Tuple[bool, str]:
    """Require entry bar wick >= N× body (rejection). bar = (ts, o, h, l, c, v)."""
    if min_ratio <= 0: return True, "pin_skip"
    op, hi, lo, cl = bar[1], bar[2], bar[3], bar[4]
    body = abs(cl - op)
    if body <= 0: return False, "PIN_ZERO_BODY"
    if side == "LONG":
        lower_wick = min(op, cl) - lo
        ratio = lower_wick / body
        if ratio < min_ratio:
            return False, f"PIN_LOWER_WICK_RATIO_{ratio:.1f}<{min_ratio:.1f}"
    else:
        upper_wick = hi - max(op, cl)
        ratio = upper_wick / body
        if ratio < min_ratio:
            return False, f"PIN_UPPER_WICK_RATIO_{ratio:.1f}<{min_ratio:.1f}"
    return True, f"pin_ratio={ratio:.1f}"


def check_v3_winner_entry_filters(inp: V3Input, cfg, ind: Optional[dict] = None) -> Tuple[bool, str]:
    """Check the three ADD-ON entry filters (VWAP deviation, BB squeeze, pin bar).
    `ind` dict supplies vwap_3m, bbw_3m; bar comes from inp. All gated by cfg thresholds.
    Returns (True, '') if all pass or disabled. (False, reason) on first failure."""
    ind = ind or {}
    vwap_min = float(getattr(cfg, 'SCALP_V3_VWAP_DEV_MIN_PCT', 0.0) or 0.0)
    bb_max = float(getattr(cfg, 'SCALP_V3_BB_SQUEEZE_MAX_PCT', 0.0) or 0.0)
    pin_min = float(getattr(cfg, 'SCALP_V3_PIN_BAR_RATIO', 0.0) or 0.0)
    if vwap_min > 0:
        ok, why = _vwap_deviation_filter(inp.current_price, ind.get('vwap_3m') or ind.get('vwap_dc_basis_3m'), inp.side, vwap_min)
        if not ok: return False, why
    if bb_max > 0:
        ok, why = _bb_squeeze_filter(ind.get('bbw_3m') or ind.get('bb_width_3m_pct'), bb_max)
        if not ok: return False, why
    if pin_min > 0 and len(inp.bars_1m) >= 1:
        ok, why = _pin_bar_filter(inp.bars_1m[-1], inp.side, pin_min)
        if not ok: return False, why
    return True, "V3_WINNER_FILTERS_OK"


def check_v3_atr_take_profit(pos: V3Position, current_price: float, atr_3m_pct: Optional[float], cfg) -> Tuple[bool, str]:
    """Exit when gain >= SCALP_V3_ATR_TP_MULT × current 3m-ATR%."""
    mult = float(getattr(cfg, 'SCALP_V3_ATR_TP_MULT', 0.0) or 0.0)
    if mult <= 0 or atr_3m_pct is None or atr_3m_pct <= 0:
        return False, ""
    gain = _compute_gain_pct(pos, current_price)
    target = mult * atr_3m_pct
    if gain >= target:
        return True, f"SCALP_V3_EXIT_ATR_TP_{pos.side}_gain={gain:.2f}%_target={target:.2f}%"
    return False, ""


def check_v3_peak_giveback(pos: V3Position, current_price: float, cfg) -> Tuple[bool, str, float]:
    """Exit when position gave back configured % from peak (after arming).
    Returns (fire, reason, new_peak_gain). Caller must persist new_peak_gain back to pos."""
    arm = float(getattr(cfg, 'SCALP_V3_PG_ARM_PCT', 0.0) or 0.0)
    give = float(getattr(cfg, 'SCALP_V3_PG_GIVEBACK_PCT', 0.0) or 0.0)
    peak = float(getattr(pos, 'peak_gain_pct', 0.0) or 0.0)
    if arm <= 0 or give <= 0:
        return False, "", peak
    gain = _compute_gain_pct(pos, current_price)
    if gain > peak:
        peak = gain
    if peak >= arm and gain <= peak - give:
        return True, f"SCALP_V3_EXIT_PEAK_GIVEBACK_{pos.side}_peak={peak:.2f}%_cur={gain:.2f}%", peak
    return False, "", peak


def check_scalp_v3_entry(inp: V3Input, cfg) -> Tuple[bool, str]:
    side = inp.side
    if len(inp.bars_1m) < 2 or len(inp.bars_3m) < 2:
        return False, "INSUFFICIENT_BARS"
    # SHORT-specific: require price has recently dumped ≥X% (bounce-to-midline-then-fail pattern).
    # Cruder version of wt_dc_delta.py:876-894 BASELINE_BOUNCE_SHORT; skip if dump history absent.
    if side == "SHORT" and getattr(cfg, 'SCALP_V3_SHORT_REQUIRE_RECENT_DUMP', False):
        lookback = int(getattr(cfg, 'SCALP_V3_SHORT_RECENT_DUMP_LOOKBACK_MIN', 60))
        drop_pct = float(getattr(cfg, 'SCALP_V3_SHORT_RECENT_DUMP_PCT', 3.0))
        if len(inp.bars_1m) >= lookback:
            min_close = min(b[4] for b in inp.bars_1m[-lookback:])
            if min_close > inp.current_price * (1.0 - drop_pct / 100.0):
                return False, "SHORT_NO_RECENT_DUMP"
    # HTF runway — ALWAYS required regardless of TF mode
    if _k_overbought(inp.k_1h, cfg.SCALP_V3_ENTRY_K_1H_MAX, side):
        return False, "K_1H_CAPPED"
    if _k_overbought(inp.k_4h, cfg.SCALP_V3_ENTRY_K_4H_MAX, side):
        return False, "K_4H_CAPPED"
    if _k_overbought(inp.k_15m, cfg.SCALP_V3_ENTRY_K_15M_MAX, side):
        return False, "K_15M_CAPPED"
    tf_mode = cfg.SCALP_V3_ENTRY_TF_MODE
    use_1m = tf_mode in ("1M_ONLY", "1M_AND_3M", "3M_CONFIRMS_1M")
    use_3m = tf_mode in ("3M_ONLY", "1M_AND_3M", "3M_CONFIRMS_1M")
    if use_1m:
        if not _k_oversold(inp.k_1m, cfg.SCALP_V3_ENTRY_K_1M_MAX, side):
            return False, "K_1M_NOT_OVERSOLD"
        if cfg.SCALP_V3_ENTRY_REQUIRE_K_TURNUP:
            if side == "LONG" and inp.k_1m <= inp.k_1m_prev: return False, "K_1M_NOT_TURNING_UP"
            if side == "SHORT" and inp.k_1m >= inp.k_1m_prev: return False, "K_1M_NOT_TURNING_DOWN"
        if not _bar_pattern_match(inp.bars_1m[-2], inp.bars_1m[-1], cfg.SCALP_V3_ENTRY_BAR_1M_REQUIRE, side):
            return False, "BAR_1M_PATTERN_FAIL"
        vol_avg = _volume_avg(inp.bars_1m[-21:-1], 20)
        if vol_avg > 0 and inp.bars_1m[-1][5] < cfg.SCALP_V3_ENTRY_VOL_SPIKE_MULT * vol_avg:
            return False, "VOL_SPIKE_FAIL"
    if use_3m:
        if not _k_oversold(inp.k_3m, cfg.SCALP_V3_ENTRY_K_3M_MAX, side):
            return False, "K_3M_NOT_OVERSOLD"
        if not _bar_pattern_match(inp.bars_3m[-2], inp.bars_3m[-1], cfg.SCALP_V3_ENTRY_BAR_3M_REQUIRE, side):
            return False, "BAR_3M_PATTERN_FAIL"
    return True, f"SCALP_V3_ENTRY_{side}_{tf_mode}"


def check_scalp_v3_exit(pos: V3Position, inp: V3Input, cfg, ind: Optional[dict] = None) -> Tuple[bool, str]:
    """Technical-only exits (2026-04-24 user directive: no max_hold, always respect technicals,
    exit at gain whenever possible). Priority order:
      1. ATR_TP — exit when gain ≥ ATR_TP_MULT × atr_3m_pct (caller supplies atr_3m_pct via ind)
      2. PEAK_GIVEBACK — exit when position gave back GIVEBACK_PCT from peak (after arming).
         Mutates pos.peak_gain_pct in-place; caller persists.
      3. K+Bar technical (overbought K AND bar reversal)
      4. GAIN-AWARE early exit: gain ≥ min_tp AND bar reversal → lock profit
      5. STALL (ONLY fires if SCALP_V3_STALL_ENABLED=True; default False)
    """
    side = pos.side
    gain_pct = _compute_gain_pct(pos, inp.current_price)
    ind = ind or {}
    # 1) ATR_TP — fires when gain meets ATR-scaled target
    _atr_3m_pct = ind.get('atr_3m_pct')
    if _atr_3m_pct is not None:
        ok_atr, why_atr = check_v3_atr_take_profit(pos, inp.current_price, _atr_3m_pct, cfg)
        if ok_atr:
            return True, why_atr
    # 2) PEAK_GIVEBACK — mutate pos.peak_gain_pct so caller's persisted dict reflects new peak.
    ok_pg, why_pg, new_peak = check_v3_peak_giveback(pos, inp.current_price, cfg)
    pos.peak_gain_pct = new_peak
    if ok_pg:
        return True, why_pg
    tf_mode = cfg.SCALP_V3_EXIT_TF_MODE
    check_1m = tf_mode in ("ANY", "1M_ONLY")
    check_3m = tf_mode in ("ANY", "3M_ONLY")
    check_15m = tf_mode in ("ANY", "15M_ONLY")
    # 3) Full K+Bar technical exit (original)
    if check_1m and len(inp.bars_1m) >= 2:
        if _k_overbought(inp.k_1m, cfg.SCALP_V3_EXIT_1M_K_MIN, side):
            if _bar_pattern_match(inp.bars_1m[-2], inp.bars_1m[-1], cfg.SCALP_V3_EXIT_1M_BAR, side):
                return True, f"SCALP_V3_EXIT_1M_BAR_{side}"
    if check_3m and len(inp.bars_3m) >= 2:
        if _k_overbought(inp.k_3m, cfg.SCALP_V3_EXIT_3M_K_MIN, side):
            if _bar_pattern_match(inp.bars_3m[-2], inp.bars_3m[-1], cfg.SCALP_V3_EXIT_3M_BAR, side):
                return True, f"SCALP_V3_EXIT_3M_BAR_{side}"
    if check_15m and len(inp.bars_15m) >= 2:
        if _k_overbought(inp.k_15m, cfg.SCALP_V3_EXIT_15M_K_MIN, side):
            if _bar_pattern_match(inp.bars_15m[-2], inp.bars_15m[-1], cfg.SCALP_V3_EXIT_15M_BAR, side):
                return True, f"SCALP_V3_EXIT_15M_BAR_{side}"
    # 4) GAIN-AWARE EARLY EXIT (2026-04-24): when in gain ≥ SCALP_V3_MIN_TP_FOR_EARLY_EXIT (default 0.2%),
    # any bar reversal pattern fires immediately — lock the profit before it slips away. K gate is bypassed
    # because in-gain positions don't need overbought confirmation; the bar reversal IS the signal.
    _min_tp = float(getattr(cfg, 'SCALP_V3_MIN_TP_FOR_EARLY_EXIT', 0.2) or 0.2)
    if gain_pct >= _min_tp:
        if check_1m and len(inp.bars_1m) >= 2:
            if _bar_pattern_match(inp.bars_1m[-2], inp.bars_1m[-1], cfg.SCALP_V3_EXIT_1M_BAR, side):
                return True, f"SCALP_V3_EXIT_EARLY_1M_BAR_{side}_gain={gain_pct:.2f}%"
        if check_3m and len(inp.bars_3m) >= 2:
            if _bar_pattern_match(inp.bars_3m[-2], inp.bars_3m[-1], cfg.SCALP_V3_EXIT_3M_BAR, side):
                return True, f"SCALP_V3_EXIT_EARLY_3M_BAR_{side}_gain={gain_pct:.2f}%"
    # 5) STALL — only if explicitly enabled. max_hold_min is no longer a trigger by itself.
    age_min = (inp.now_ts - pos.entry_ts) / 60.0
    if getattr(cfg, 'SCALP_V3_STALL_ENABLED', False) and age_min > cfg.SCALP_V3_MAX_HOLD_MIN and gain_pct <= cfg.SCALP_V3_STALL_GAIN_MAX_PCT:
        return True, f"SCALP_V3_EXIT_STALL_{side}"
    return False, ""


def check_scalp_v3_reentry_allowed(exit_state: Optional[V3ExitState], inp: V3Input, cfg) -> Tuple[bool, str]:
    if exit_state is None:
        return True, "NO_PRIOR_EXIT"
    cooldown = cfg.SCALP_V3_REENTRY_COOLDOWN_S
    if cooldown > 0 and (inp.now_ts - exit_state.last_exit_ts) < cooldown:
        return False, "COOLDOWN"
    if not cfg.SCALP_V3_REENTRY_REQUIRE_BOUNCE_IF_15M_FALLING:
        return True, "BOUNCE_GATE_DISABLED"
    k15_was_falling = exit_state.k_15m_at_exit < exit_state.k_15m_prev_at_exit
    if not k15_was_falling:
        return True, "K15M_WAS_RISING_AT_EXIT"
    # 15m was falling at exit — require clear bounce
    side = inp.side
    bar_3m_bounced = False
    if len(inp.bars_3m) >= 2:
        bar_3m_bounced = _bar_pattern_match(
            inp.bars_3m[-2], inp.bars_3m[-1], cfg.SCALP_V3_REENTRY_BOUNCE_BAR_3M, side
        )
    k15_bounced = _k_oversold(inp.k_15m, cfg.SCALP_V3_REENTRY_BOUNCE_K_15M_MAX, side) and (
        (side == "LONG" and inp.k_15m > inp.k_15m_prev) or
        (side == "SHORT" and inp.k_15m < inp.k_15m_prev)
    )
    mode = cfg.SCALP_V3_REENTRY_BOUNCE_MODE
    if mode == "3M_BAR_ONLY":
        return (bar_3m_bounced, "3M_BOUNCE" if bar_3m_bounced else "NO_3M_BOUNCE")
    if mode == "K15M_ONLY":
        return (k15_bounced, "K15M_BOUNCE" if k15_bounced else "NO_K15M_BOUNCE")
    if mode == "3M_BAR_AND_K15M":
        ok = bar_3m_bounced and k15_bounced
        return (ok, "BOTH_BOUNCE" if ok else f"PARTIAL_3M={bar_3m_bounced}_K15M={k15_bounced}")
    ok = bar_3m_bounced or k15_bounced
    return (ok, "3M_OR_K15M_BOUNCE" if ok else "NO_BOUNCE")
