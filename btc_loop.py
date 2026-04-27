"""
btc_loop.py — BTC-dedicated trading loop decision module.

Single source of decision logic for flz:BTCUSDC (and inf BTC trades).
Imported by:
  - ez_positions_quick.py     (live)
  - v8_quick_engine.py        (vectorized backtest)
  - backtest_v8_engine.py     (Tier-2 replay)
  - btc_loop_paper.py         (forward-paper shadow runner)

PARITY RULE (user 2026-04-27):
  All four callers must import the SAME Python function objects from this module.
  No `if backtest:` / `if paper:` branches in decision logic. Adapter layer
  (execute_now wrapper, fork channel) handles environment differences OUTSIDE
  these functions. Verify id() equality between callers at startup.

Decision functions take pure features (numbers) and return (bool, reason) or
small dicts. They do NOT read Redis, NPZ files, or position trackers — the
caller assembles features and passes them in.

Master kill switch is `cfg.BTC_DEDICATED_ENABLED`. When False, every public
function short-circuits to a no-op (returns False / empty dict) so this module
is dormant on import.

See BTC_DEDICATED_LOOP_DESIGN_20260427.md for spec.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

# Sentinel used in id-equality parity checks
__btc_loop_module_id__ = "btc_loop_v0_20260427"

# ── Feature dataclasses (caller fills these from its own context) ────────────


@dataclass
class WTAccelFeatures:
    """Per-TF WaveTrend velocity + acceleration. Caller supplies for each TF."""
    velocity: float                           # Δwt = wt1 - wt1_prev
    velocity_prev: float                      # Δwt one bar ago
    acceleration: float                       # Δvelocity = velocity - velocity_prev


@dataclass
class DivergenceState:
    """Aggregated multi-indicator divergence state across TFs.

    Each indicator (WT, RSI, MFI, OBV, CVD) per TF (3m, 15m, 1h, 4h, D) is a
    bool for bull-div and bear-div in the lookback window.

    The caller computes these from raw arrays; this module only counts.
    """
    bull_inds_aligned: int = 0                # count of indicators with bull div across any TF
    bear_inds_aligned: int = 0
    bull_inds_strong_3plus_tfs: int = 0       # count where 3+ TFs simultaneously show bull div
    bear_inds_strong_3plus_tfs: int = 0


@dataclass
class RedZoneState:
    """Red-zone proximity result for current price."""
    active: bool                              # within proximity of any zone
    nearest_kind: str                         # "wt_dc" | "fib" | "round" | "none"
    nearest_distance_pct: float               # signed: + above, − below
    fib_count_within: int = 0                 # how many fib levels within proximity
    round_within: bool = False                # within proximity of any round level
    wt_dc_zone: str = "none"                  # "BASELINE" | "TOP" | "BOTTOM" | "TRANSIT" | "none"


@dataclass
class PositionRiskState:
    """Position-level risk state for the BTC loop."""
    side: str                                 # "LONG" | "SHORT" | "FLAT"
    own_capital_usd: float = 0.0              # capital committed (not notional)
    notional_usd: float = 0.0                 # leveraged exposure (= own × leverage)
    current_pnl_pct: float = 0.0
    current_pnl_usd: float = 0.0
    age_bars: int = 0                         # bars since entry (3m base TF)
    bars_since_last_exit: int = 9999          # bars since the last technical exit (for reentry gate)
    daily_pnl_pct: float = 0.0                # for floor checks
    weekly_pnl_pct: float = 0.0
    entry_type: str = "BOUNCE"                # "BOUNCE" (red-zone bounce) | "BREAKOUT" (DC/RZ breakout)
                                              # Different exit rules apply per type — breakouts use tighter stops.


# ── Pure compute helpers (deterministic; no I/O) ─────────────────────────────


def compute_accel_ramp(
    accel_per_tf: Dict[str, WTAccelFeatures],
    *,
    require_positive: bool = True,
) -> Dict[str, Any]:
    """Detect Δwt > Δwt_prev > 0 across TFs.

    Returns:
        {
          "bull_aligned_tfs": int,       # n TFs where (vel > vel_prev > 0)
          "bear_aligned_tfs": int,       # n TFs where (vel < vel_prev < 0)
          "tfs_evaluated": int,
          "side": "bull" | "bear" | "none",  # winning side if any
        }
    """
    bull = 0
    bear = 0
    n = 0
    for tf, f in accel_per_tf.items():
        n += 1
        v, vp = f.velocity, f.velocity_prev
        if require_positive:
            if v > vp > 0:
                bull += 1
            elif v < vp < 0:
                bear += 1
        else:
            if v > vp:
                bull += 1
            elif v < vp:
                bear += 1
    side = "none"
    if bull > bear and bull > 0:
        side = "bull"
    elif bear > bull and bear > 0:
        side = "bear"
    return {
        "bull_aligned_tfs": bull,
        "bear_aligned_tfs": bear,
        "tfs_evaluated": n,
        "side": side,
    }


def compute_fib_levels(swing_high: float, swing_low: float) -> Dict[str, float]:
    """Compute 5 fib retracements + 2 extensions each direction from swing range.

    Levels are absolute prices, not ratios. Caller picks anchor TF and lookback.
    """
    rng = swing_high - swing_low
    if rng <= 0:
        return {}
    out: Dict[str, float] = {}
    for r in (0.236, 0.382, 0.5, 0.618, 0.786):
        out[f"retr_{int(r * 1000)}"] = swing_high - rng * r
    for r in (1.272, 1.618):
        out[f"ext_up_{int(r * 1000)}"] = swing_low + rng * r
        out[f"ext_dn_{int(r * 1000)}"] = swing_high - rng * r
    return out


def compute_round_levels(
    price: float,
    *,
    primary_inc_usd: float = 5000.0,
    secondary_inc_usd: float = 1000.0,
    bands_each_side: int = 8,
) -> Dict[str, list]:
    """Round-number bands above/below current price."""
    if price <= 0:
        return {"primary_below": [], "primary_above": [], "secondary_below": [], "secondary_above": []}
    out: Dict[str, list] = {}
    for tag, inc in (("primary", primary_inc_usd), ("secondary", secondary_inc_usd)):
        base = (int(price) // int(inc)) * int(inc)
        below = [base - inc * k for k in range(0, bands_each_side)]
        above = [base + inc * (k + 1) for k in range(0, bands_each_side)]
        out[f"{tag}_below"] = below
        out[f"{tag}_above"] = above
    return out


def is_within_proximity(
    price: float,
    levels: list,
    proximity_pct: float,
) -> bool:
    """True if price is within proximity_pct of any level in `levels`."""
    if price <= 0 or not levels:
        return False
    p = proximity_pct / 100.0
    for lvl in levels:
        if lvl <= 0:
            continue
        if abs(price - lvl) / price <= p:
            return True
    return False


# ── Divergence detection (pure-function, single-bar at index i over lookback) ─


def detect_divergence_at(
    price: list,
    indicator: list,
    *,
    lookback_bars: int = 5,
    require_strict: bool = True,
) -> Tuple[bool, bool]:
    """Detect bull/bear divergence at the latest bar of `price` vs `indicator`.

    Algorithm:
      Bull div: price made a LOWER LOW than `lookback_bars` ago,
                AND indicator made a HIGHER LOW.
      Bear div: price made a HIGHER HIGH than `lookback_bars` ago,
                AND indicator made a LOWER HIGH.

    Inputs are ordered oldest→newest; the last element is "now". Min lookback 2.
    Strict mode requires the latest two extrema to be the comparison bars.
    Lenient mode also accepts within-window comparisons.

    Returns (bull_div, bear_div).
    """
    n = len(price)
    if n < 2 or n != len(indicator) or lookback_bars < 2:
        return False, False
    lb = min(lookback_bars, n - 1)
    p_now = price[-1]
    p_then = price[-1 - lb]
    i_now = indicator[-1]
    i_then = indicator[-1 - lb]
    bull = False
    bear = False
    if require_strict:
        # Strict: simply compare endpoints
        bull = (p_now < p_then) and (i_now > i_then)
        bear = (p_now > p_then) and (i_now < i_then)
    else:
        # Lenient: window minima/maxima vs current
        win_p = price[-lb:]
        win_i = indicator[-lb:]
        # bull div = current is a window low for price + window high for indicator
        bull = (p_now == min(win_p)) and (i_now > min(win_i))
        bear = (p_now == max(win_p)) and (i_now < max(win_i))
    return bool(bull), bool(bear)


def aggregate_multi_indicator_divergence(
    indicators_by_name: Dict[str, Dict[str, Tuple[list, list]]],
    *,
    lookback_bars: int = 5,
    require_strict: bool = True,
    strong_tfs_threshold: int = 3,
) -> DivergenceState:
    """Aggregate divergence across multiple indicators × multiple TFs.

    Args:
      indicators_by_name: nested dict
          {
              "WT":  {"3m": (price_arr, wt_arr), "15m": (...), ...},
              "RSI": {"3m": (price_arr, rsi_arr), ...},
              "MFI": ...,
              "OBV": ...,
              "CVD": ...,
          }
        Each TF entry is a (price_window, indicator_window) tuple of equal-length lists.
        Caller decides whether to include OBV/CVD (skip if NPZ doesn't have them).

      strong_tfs_threshold: an indicator counts as "strong-aligned" if N TFs simultaneously
        show divergence in the same direction.

    Returns DivergenceState with bull_inds_aligned, bear_inds_aligned, and the
    "strong" counts (how many indicators showed div on threshold+ TFs at once).
    """
    bull_inds = 0
    bear_inds = 0
    bull_strong = 0
    bear_strong = 0
    for ind_name, tf_map in indicators_by_name.items():
        ind_bull_tfs = 0
        ind_bear_tfs = 0
        for tf, payload in tf_map.items():
            if not payload or len(payload) != 2:
                continue
            price, ind = payload
            b, br = detect_divergence_at(
                price, ind,
                lookback_bars=lookback_bars,
                require_strict=require_strict,
            )
            if b:
                ind_bull_tfs += 1
            if br:
                ind_bear_tfs += 1
        if ind_bull_tfs > 0:
            bull_inds += 1
        if ind_bear_tfs > 0:
            bear_inds += 1
        if ind_bull_tfs >= strong_tfs_threshold:
            bull_strong += 1
        if ind_bear_tfs >= strong_tfs_threshold:
            bear_strong += 1
    return DivergenceState(
        bull_inds_aligned=bull_inds,
        bear_inds_aligned=bear_inds,
        bull_inds_strong_3plus_tfs=bull_strong,
        bear_inds_strong_3plus_tfs=bear_strong,
    )


def build_red_zone_state(
    *,
    current_price: float,
    fib_levels_per_tf: Optional[Dict[str, Dict[str, float]]] = None,
    round_levels: Optional[Dict[str, list]] = None,
    wt_dc_zone: str = "none",
    proximity_pct: float = 0.5,
) -> RedZoneState:
    """Compose RedZoneState from fib + round + wt_dc inputs.

    Args:
      fib_levels_per_tf: {"4h": {"retr_500": 70700.0, ...}, "D": {...}, ...}
        From compute_fib_levels per TF.
      round_levels: output of compute_round_levels.
      wt_dc_zone: from existing wt_dc_delta._run_redzone() result.

    Returns RedZoneState with active=True if proximity matches ANY level OR
    wt_dc_zone is meaningful (BASELINE/TOP/BOTTOM).
    """
    fib_count = 0
    nearest_dist = float("inf")
    nearest_kind = "none"
    if fib_levels_per_tf:
        for tf, levels in fib_levels_per_tf.items():
            for nm, lvl in levels.items():
                if lvl <= 0:
                    continue
                d = (lvl - current_price) / current_price
                if abs(d) <= proximity_pct / 100.0:
                    fib_count += 1
                    if abs(d) < abs(nearest_dist):
                        nearest_dist = d
                        nearest_kind = "fib"
    round_within = False
    if round_levels:
        all_round = []
        for k, v in round_levels.items():
            if isinstance(v, list):
                all_round.extend(v)
        if is_within_proximity(current_price, all_round, proximity_pct):
            round_within = True
            for lvl in all_round:
                if lvl <= 0:
                    continue
                d = (lvl - current_price) / current_price
                if abs(d) <= proximity_pct / 100.0 and abs(d) < abs(nearest_dist):
                    nearest_dist = d
                    nearest_kind = "round"
    wt_dc_active = wt_dc_zone in ("BASELINE", "TOP", "BOTTOM")
    if wt_dc_active and nearest_kind == "none":
        nearest_kind = "wt_dc"
        nearest_dist = 0.0
    active = (fib_count > 0) or round_within or wt_dc_active
    return RedZoneState(
        active=active,
        nearest_kind=nearest_kind,
        nearest_distance_pct=(nearest_dist * 100.0) if nearest_dist != float("inf") else 0.0,
        fib_count_within=fib_count,
        round_within=round_within,
        wt_dc_zone=wt_dc_zone,
    )


# ── Decision functions (called identically by live + paper + backtest) ───────


def should_enter_btc_long(
    *,
    current_price: float,
    red_zone: RedZoneState,
    accel: Dict[str, Any],                    # output of compute_accel_ramp
    divergence: DivergenceState,
    cfg: Any,                                 # Config or QuickConfig
) -> Tuple[bool, str]:
    """Return (enter_long, reason). Pure function — no I/O."""
    if not getattr(cfg, "BTC_DEDICATED_ENABLED", False):
        return False, "BTC_LOOP_DISABLED"
    # Veto: opposing divergence
    if (
        getattr(cfg, "BTC_DIVERGENCE_BLOCK_AGAINST", True)
        and getattr(cfg, "BTC_DIVERGENCE_ENABLED", True)
        and divergence.bear_inds_aligned >= getattr(cfg, "BTC_DIVERGENCE_BEAR_MIN_INDS", 2)
    ):
        return False, "BLOCKED_BEAR_DIVERGENCE"
    # Primary trigger: red zone + accel ramp aligned bull
    if getattr(cfg, "BTC_ENTRY_PRIMARY_REQUIRE_RZ", True) and not red_zone.active:
        return False, "NO_RED_ZONE"
    if getattr(cfg, "BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP", True):
        if accel["side"] != "bull":
            return False, "NO_BULL_ACCEL_RAMP"
        if accel["bull_aligned_tfs"] < getattr(cfg, "BTC_ACCEL_RAMP_MIN_TFS", 5):
            return False, f"ACCEL_RAMP_INSUFFICIENT_TFS_{accel['bull_aligned_tfs']}"
    return True, "PRIMARY_BULL"


def should_enter_btc_short(
    *,
    current_price: float,
    red_zone: RedZoneState,
    accel: Dict[str, Any],
    divergence: DivergenceState,
    cfg: Any,
) -> Tuple[bool, str]:
    """Return (enter_short, reason). Mirror of should_enter_btc_long."""
    if not getattr(cfg, "BTC_DEDICATED_ENABLED", False):
        return False, "BTC_LOOP_DISABLED"
    if (
        getattr(cfg, "BTC_DIVERGENCE_BLOCK_AGAINST", True)
        and getattr(cfg, "BTC_DIVERGENCE_ENABLED", True)
        and divergence.bull_inds_aligned >= getattr(cfg, "BTC_DIVERGENCE_BULL_MIN_INDS", 2)
    ):
        return False, "BLOCKED_BULL_DIVERGENCE"
    if getattr(cfg, "BTC_ENTRY_PRIMARY_REQUIRE_RZ", True) and not red_zone.active:
        return False, "NO_RED_ZONE"
    if getattr(cfg, "BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP", True):
        if accel["side"] != "bear":
            return False, "NO_BEAR_ACCEL_RAMP"
        if accel["bear_aligned_tfs"] < getattr(cfg, "BTC_ACCEL_RAMP_MIN_TFS", 5):
            return False, f"ACCEL_RAMP_INSUFFICIENT_TFS_{accel['bear_aligned_tfs']}"
    return True, "PRIMARY_BEAR"


def should_exit_btc(
    *,
    position: PositionRiskState,
    accel: Dict[str, Any],
    divergence: DivergenceState,
    wt_against_min_tfs: int,
    wt_against_count: int,
    cfg: Any,
    wt_3m_against: bool = False,        # NEW: for BREAKOUT entries — single 3m WT flip alone = exit
    dc_low4_3m_breach: bool = False,    # NEW: for LONG BREAKOUT — close < dc_low4_3m_prev → exit
    dc_high4_3m_breach: bool = False,   # NEW: for SHORT BREAKOUT — close > dc_high4_3m_prev → exit
) -> Tuple[bool, str]:
    """Combined exit decision. Branches on cfg.BTC_RISK_PATH and position.entry_type.

    BOUNCE entries: the "patient" exit cluster — needs N TFs against, accel reversal,
                    or divergence. Generous min-hold + standard hard-loss.

    BREAKOUT entries: tighter stops — single 3m WT flip alone OR dc_low4_3m breach
                      triggers exit immediately. Tighter hard-loss-pct.
                      (Per user 2026-04-27: breakouts can't tolerate the same patience
                      as bounces — they fail fast and need quick risk-off.)
    """
    if not getattr(cfg, "BTC_DEDICATED_ENABLED", False):
        return False, "BTC_LOOP_DISABLED"
    if position.side == "FLAT":
        return False, "FLAT"

    is_breakout = position.entry_type == "BREAKOUT"

    # Hard-loss panic — for BREAKOUTs, use the (tighter) breakout-specific floor if set.
    if is_breakout:
        # Breakout-specific USD ceiling — defaults to half of regular ($5 vs $10)
        bk_loss_usd = getattr(cfg, "BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE",
                              getattr(cfg, "BTC_HARD_LOSS_USD_PER_TRADE", 10.0) / 2.0)
        if position.current_pnl_usd <= -bk_loss_usd:
            return True, "BREAKOUT_HARD_LOSS_USD_FLOOR"
    else:
        if position.current_pnl_usd <= -getattr(cfg, "BTC_HARD_LOSS_USD_PER_TRADE", 10.0):
            return True, "HARD_LOSS_USD_FLOOR"

    # BREAKOUT exit cluster — fires regardless of risk_path because breakouts are
    # by design close-at-loss-with-reentry per user.
    if is_breakout:
        # 1. DC channel breach (structural failure of the breakout)
        if position.side == "LONG" and dc_low4_3m_breach:
            return True, "BREAKOUT_DC_LOW4_3M_BREACH"
        if position.side == "SHORT" and dc_high4_3m_breach:
            return True, "BREAKOUT_DC_HIGH4_3M_BREACH"
        # 2. Single 3m WT against (the breakout momentum failed)
        if wt_3m_against:
            return True, "BREAKOUT_WT_3M_FLIP"
        # 3. Accel reversal — kept for breakouts too (faster than bounce)
        if position.side == "LONG" and accel["side"] == "bear":
            return True, "BREAKOUT_ACCEL_REVERSAL"
        if position.side == "SHORT" and accel["side"] == "bull":
            return True, "BREAKOUT_ACCEL_REVERSAL"
        return False, "BREAKOUT_NO_EXIT"

    # ── BOUNCE path (existing logic — no change) ──
    risk_path = getattr(cfg, "BTC_RISK_PATH", "technical")
    if risk_path == "hedge":
        if getattr(cfg, "BTC_HEDGE_NEVER_CLOSE_AT_LOSS", True) and position.current_pnl_pct < 0:
            return False, "HEDGE_PATH_NO_LOSS_CLOSE"
    if wt_against_count >= wt_against_min_tfs:
        return True, f"WT_AGAINST_{wt_against_count}OF5"
    if getattr(cfg, "BTC_INTRABAR_REVERSAL_EXIT", True):
        if position.side == "LONG" and accel["side"] == "bear":
            return True, "ACCEL_REVERSAL_BEAR"
        if position.side == "SHORT" and accel["side"] == "bull":
            return True, "ACCEL_REVERSAL_BULL"
    if getattr(cfg, "BTC_DIVERGENCE_EXIT_AGAINST", True):
        if position.side == "LONG" and divergence.bear_inds_aligned >= getattr(cfg, "BTC_DIVERGENCE_BEAR_MIN_INDS", 2):
            return True, "BEAR_DIV_EXIT"
        if position.side == "SHORT" and divergence.bull_inds_aligned >= getattr(cfg, "BTC_DIVERGENCE_BULL_MIN_INDS", 2):
            return True, "BULL_DIV_EXIT"
    return False, "NO_EXIT_TRIGGER"


def detect_btc_breakout(
    *,
    current_price: float,
    prev_dc_high_3m: float,
    prev_dc_low_3m: float,
    accel: Dict[str, Any],
    divergence: DivergenceState,
    cfg: Any,
    htf_long_aligned_tfs: int = 0,           # NEW: count of HTFs (1h/4h/D) where wt1>wt2 (bullish)
    htf_short_aligned_tfs: int = 0,          # NEW: mirror for bearish
) -> Tuple[str, str]:
    """Detect a DC-channel breakout on 3m base TF.

    LONG_BREAKOUT: close > prev_dc_high_3m AND accel.bull aligned ≥ MIN_TFS
                   AND no opposing div block AND HTF aligned bullish (≥ HTF_MIN_ALIGNED).
    SHORT_BREAKDOWN: mirror.

    The HTF filter prevents buying breakouts INTO a downtrend (the small consolidation
    rallies through dc_high_3m during a bear leg) — without it, system bleeds on
    every fakeout. Default requires 2-of-3 HTFs aligned.

    Returns (side, reason). Side ∈ {"LONG", "SHORT", "NONE"}.
    """
    if not getattr(cfg, "BTC_DEDICATED_ENABLED", False):
        return "NONE", "BTC_LOOP_DISABLED"
    if not getattr(cfg, "BTC_BREAKOUT_ENTRY_ENABLED", True):
        return "NONE", "BREAKOUT_DISABLED"

    min_tfs = int(getattr(cfg, "BTC_BREAKOUT_ACCEL_MIN_TFS", 2))
    block_div = bool(getattr(cfg, "BTC_BREAKOUT_BLOCK_OPPOSING_DIV", True))
    require_htf = bool(getattr(cfg, "BTC_BREAKOUT_REQUIRE_HTF_ALIGNED", True))
    htf_min = int(getattr(cfg, "BTC_BREAKOUT_HTF_MIN_ALIGNED", 2))

    # LONG breakout
    if (
        current_price > prev_dc_high_3m > 0
        and accel["bull_aligned_tfs"] >= min_tfs
        and accel["side"] == "bull"
    ):
        if require_htf and htf_long_aligned_tfs < htf_min:
            return "NONE", f"BREAKOUT_LONG_HTF_AGAINST_{htf_long_aligned_tfs}_OF_{htf_min}"
        if block_div and divergence.bear_inds_aligned >= getattr(cfg, "BTC_DIVERGENCE_BEAR_MIN_INDS", 2):
            return "NONE", "BREAKOUT_LONG_BLOCKED_BEAR_DIV"
        return "LONG", "BREAKOUT_LONG"

    # SHORT breakdown
    if (
        current_price < prev_dc_low_3m
        and prev_dc_low_3m > 0
        and accel["bear_aligned_tfs"] >= min_tfs
        and accel["side"] == "bear"
    ):
        if require_htf and htf_short_aligned_tfs < htf_min:
            return "NONE", f"BREAKOUT_SHORT_HTF_AGAINST_{htf_short_aligned_tfs}_OF_{htf_min}"
        if block_div and divergence.bull_inds_aligned >= getattr(cfg, "BTC_DIVERGENCE_BULL_MIN_INDS", 2):
            return "NONE", "BREAKOUT_SHORT_BLOCKED_BULL_DIV"
        return "SHORT", "BREAKOUT_SHORT"

    return "NONE", "NO_BREAKOUT"


def should_reenter_btc(
    *,
    position: PositionRiskState,
    last_exit_side: str,                      # "LONG" | "SHORT" | "NONE"
    red_zone: RedZoneState,
    accel: Dict[str, Any],
    divergence: DivergenceState,
    cfg: Any,
) -> Tuple[bool, str]:
    """Path B reentry guarantee: re-enter on next valid setup after technical exit."""
    if not getattr(cfg, "BTC_DEDICATED_ENABLED", False):
        return False, "BTC_LOOP_DISABLED"
    if not getattr(cfg, "BTC_GUARANTEED_REENTRY_ENABLED", True):
        return False, "REENTRY_DISABLED"
    if last_exit_side == "NONE":
        return False, "NO_PRIOR_EXIT"
    if position.side != "FLAT":
        return False, "NOT_FLAT"
    if position.bars_since_last_exit < getattr(cfg, "BTC_GUARANTEED_REENTRY_MIN_GAP_BARS", 5):
        return False, "REENTRY_GAP_TOO_SHORT"
    if position.bars_since_last_exit > getattr(cfg, "BTC_GUARANTEED_REENTRY_MAX_AGE_BARS", 480):
        return False, "REENTRY_AGE_EXPIRED"
    if getattr(cfg, "BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE", True) and not red_zone.active:
        return False, "REENTRY_NO_RZ"
    # Re-evaluate primary entry trigger on the same side as the prior exit
    if last_exit_side == "LONG":
        return should_enter_btc_long(
            current_price=0.0,                 # not used by entry function (caller tracks)
            red_zone=red_zone,
            accel=accel,
            divergence=divergence,
            cfg=cfg,
        )
    if last_exit_side == "SHORT":
        return should_enter_btc_short(
            current_price=0.0,
            red_zone=red_zone,
            accel=accel,
            divergence=divergence,
            cfg=cfg,
        )
    return False, "UNKNOWN_SIDE"


def risk_gate_pre_entry(
    *,
    proposed_own_capital_usd: float,
    current_total_own_notional_usd: float,
    daily_pnl_pct: float,
    weekly_pnl_pct: float,
    cfg: Any,
) -> Tuple[bool, str, float]:
    """Returns (allow_entry, reason, allowed_own_capital_usd).

    Caps proposed size to BTC_PER_TRADE_NOTIONAL_USD_MAX and overall to
    BTC_TOTAL_NOTIONAL_USD_MAX. Vetoes entirely on daily/weekly floor.
    """
    if not getattr(cfg, "BTC_DEDICATED_ENABLED", False):
        return False, "BTC_LOOP_DISABLED", 0.0
    if daily_pnl_pct < getattr(cfg, "BTC_DAILY_LOSS_PCT_FLOOR", -0.5):
        return False, "DAILY_LOSS_FLOOR_BREACHED", 0.0
    if weekly_pnl_pct < getattr(cfg, "BTC_WEEKLY_LOSS_PCT_FLOOR", -1.5):
        return False, "WEEKLY_LOSS_FLOOR_BREACHED", 0.0
    per_trade_max = getattr(cfg, "BTC_PER_TRADE_NOTIONAL_USD_MAX", 90.0)
    total_max = getattr(cfg, "BTC_TOTAL_NOTIONAL_USD_MAX", 180.0)
    capped = min(proposed_own_capital_usd, per_trade_max)
    available = max(0.0, total_max - current_total_own_notional_usd)
    capped = min(capped, available)
    if capped <= 0:
        return False, "TOTAL_NOTIONAL_CAP_REACHED", 0.0
    return True, "OK", capped


# ── Parity verification helper ───────────────────────────────────────────────


def verify_parity_against(other_module) -> Dict[str, bool]:
    """Compare id() of decision functions against another import of this module.

    Used at startup of paper runner / live worker to assert that all callers
    are sharing the same Python objects.
    """
    fns = (
        "compute_accel_ramp",
        "compute_fib_levels",
        "compute_round_levels",
        "is_within_proximity",
        "should_enter_btc_long",
        "should_enter_btc_short",
        "should_exit_btc",
        "should_reenter_btc",
        "risk_gate_pre_entry",
    )
    out: Dict[str, bool] = {}
    for nm in fns:
        out[nm] = id(globals()[nm]) == id(getattr(other_module, nm, None))
    return out
