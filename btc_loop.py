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
) -> Tuple[bool, str]:
    """Combined exit decision. Branch on cfg.BTC_RISK_PATH."""
    if not getattr(cfg, "BTC_DEDICATED_ENABLED", False):
        return False, "BTC_LOOP_DISABLED"
    if position.side == "FLAT":
        return False, "FLAT"
    # Hard panic exit at $10 loss regardless of risk path
    if position.current_pnl_usd <= -getattr(cfg, "BTC_HARD_LOSS_USD_PER_TRADE", 10.0):
        return True, "HARD_LOSS_USD_FLOOR"
    risk_path = getattr(cfg, "BTC_RISK_PATH", "technical")
    if risk_path == "hedge":
        # Path A: never close at loss; let hedge engine handle losers.
        if getattr(cfg, "BTC_HEDGE_NEVER_CLOSE_AT_LOSS", True) and position.current_pnl_pct < 0:
            return False, "HEDGE_PATH_NO_LOSS_CLOSE"
    # Path B (default per user 2026-04-27): technical exit at any P/L.
    # 1. WT count against side
    if wt_against_count >= wt_against_min_tfs:
        return True, f"WT_AGAINST_{wt_against_count}OF5"
    # 2. Accel reversal
    if getattr(cfg, "BTC_INTRABAR_REVERSAL_EXIT", True):
        if position.side == "LONG" and accel["side"] == "bear":
            return True, "ACCEL_REVERSAL_BEAR"
        if position.side == "SHORT" and accel["side"] == "bull":
            return True, "ACCEL_REVERSAL_BULL"
    # 3. Divergence against
    if getattr(cfg, "BTC_DIVERGENCE_EXIT_AGAINST", True):
        if position.side == "LONG" and divergence.bear_inds_aligned >= getattr(cfg, "BTC_DIVERGENCE_BEAR_MIN_INDS", 2):
            return True, "BEAR_DIV_EXIT"
        if position.side == "SHORT" and divergence.bull_inds_aligned >= getattr(cfg, "BTC_DIVERGENCE_BULL_MIN_INDS", 2):
            return True, "BULL_DIV_EXIT"
    return False, "NO_EXIT_TRIGGER"


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
