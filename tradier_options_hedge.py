"""Hedge ladder for losing options (2026-04-26 owner directive).

When a long option position is in trouble, decide between three actions:

  1. HOLD     — at a confirmed bottom (red zone retest / dc_low / wt_velocity
                slowdown / k_extreme). Don't hedge; reversal is justified.
  2. BUY_PUT  — an underpriced protective put exists (low IV-rank vs same-
                expiry chain peers, target delta in band). Buy it.
  3. EQUITY   — default fallback. Sell-short equity (for a losing call) or
                buy equity (for a losing put), sized to delta×qty×100. This
                is what tradier_options_analyzer.py:2590 already wires.

Used in two places:
  - OPTIONS_EQUITY_HEDGE entry (sellable_pct ≤ -10%)
  - OPTIONS_MAX_LOSS_GUARD entry (premium loss ≥ -75%)

The decision logic is framework-aligned (OPTIONS_OVERHAUL_FRAMEWORK §4 / §3).
"""
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("options_hedge")


@dataclass
class HedgeAction:
    """Result of decide_hedge_action()."""
    action: str  # "HOLD" | "BUY_PUT" | "EQUITY_HEDGE"
    reason: str
    # BUY_PUT-only fields
    put_occ: Optional[str] = None
    put_strike: Optional[float] = None
    put_expiration: Optional[str] = None
    put_dte: Optional[int] = None
    put_iv_chain_rank: Optional[float] = None
    put_delta: Optional[float] = None
    put_limit_price: Optional[float] = None
    put_qty: int = 1
    # EQUITY_HEDGE-only fields populated by caller


def at_confirmed_bottom(symbol: str, indicators: Dict[str, Any], cfg=None) -> Tuple[bool, str]:
    """Identify a confirmed reversal point that justifies HOLD instead of hedging.

    Per owner directive 2026-04-26: hedge only when NOT at a bottom. At a bottom,
    the losing position is more likely to recover than to bleed further; hedging
    would lock in the loss right before the bounce.

    Confirmation requires ≥ MIN_SIGNALS of:
      red_zone retest      — price below dc_low_D in last N bars and back above
      dc_low_4h support    — price within REL_TOL of dc_low_4h
      wt_velocity slowdown — wt_velocity_D rising (less negative or now positive)
      k_D oversold         — stoch_K_D < K_OVERSOLD_THRESHOLD
    """
    min_signals = int(getattr(cfg, "OPTIONS_HEDGE_BOTTOM_MIN_SIGNALS", 2)) if cfg else 2
    k_oversold = float(getattr(cfg, "OPTIONS_HEDGE_K_OVERSOLD_PCT", 25.0)) if cfg else 25.0
    rel_tol = float(getattr(cfg, "OPTIONS_HEDGE_DC_REL_TOL_PCT", 1.0)) if cfg else 1.0

    px = float(indicators.get("current_price", 0) or indicators.get("mark_price", 0) or 0)
    dc_low_D = float(indicators.get("dc_low_D", 0) or 0)
    dc_low_4h = float(indicators.get("dc_low_4h", 0) or 0)
    k_D = float(indicators.get("stoch_k_D", 50) or 50)
    wt_vel_D = float(indicators.get("wt_velocity_D", 0) or 0)
    wt_vel_D_prev = float(indicators.get("wt_velocity_D_prev", wt_vel_D) or wt_vel_D)
    bars_since_red = indicators.get("bars_since_dc_low_D_break", None)
    signals: List[str] = []
    if k_D < k_oversold:
        signals.append(f"k_D={k_D:.0f}<{k_oversold:.0f}")
    if dc_low_4h > 0 and px > 0 and abs(px - dc_low_4h) / px * 100.0 < rel_tol:
        signals.append(f"px={px:.2f}~dc_low_4h={dc_low_4h:.2f}")
    if wt_vel_D > wt_vel_D_prev and wt_vel_D <= 0:
        signals.append(f"wt_vel_D_slowing({wt_vel_D_prev:.2f}->{wt_vel_D:.2f})")
    if isinstance(bars_since_red, (int, float)) and 0 < bars_since_red <= 5 and dc_low_D > 0 and px > dc_low_D:
        signals.append(f"red_zone_retest_n={int(bars_since_red)}")
    if dc_low_D > 0 and px > 0 and abs(px - dc_low_D) / px * 100.0 < rel_tol:
        signals.append(f"px={px:.2f}~dc_low_D={dc_low_D:.2f}")
    n = len(signals)
    if n >= min_signals:
        return True, f"BOTTOM({n}/{min_signals}): " + ", ".join(signals)
    return False, f"NO_BOTTOM({n}/{min_signals}): " + (", ".join(signals) if signals else "no_signals")


async def find_underpriced_protective_put(
    client,
    symbol: str,
    underlying_price: float,
    losing_position_qty: int,
    losing_position_dte: int,
    config,
) -> Optional[Dict[str, Any]]:
    """Find a put on the same underlying that hedges the position and is cheap.

    Strategy: scan the same-expiration chain (closest match to losing position's
    DTE within DTE_BAND), pick puts whose computed IV is LOW vs chain peers
    (chain IV rank < MAX_IV_RANK_PCT) and whose |delta| is in the target band.
    Return the cheapest qualifying ask. None if no put qualifies.
    """
    from tradier_options_analyzer import (
        fetch_expirations, fetch_option_chain, implied_vol, parse_occ_symbol,
    )
    target_delta_min = float(getattr(config, "OPTIONS_HEDGE_PUT_DELTA_MIN", 0.30))
    target_delta_max = float(getattr(config, "OPTIONS_HEDGE_PUT_DELTA_MAX", 0.50))
    max_iv_rank_pct = float(getattr(config, "OPTIONS_HEDGE_PUT_MAX_IV_RANK", 35.0))
    dte_min = max(30, int(getattr(config, "OPTIONS_HEDGE_PUT_DTE_MIN", 45)))
    dte_max = int(getattr(config, "OPTIONS_HEDGE_PUT_DTE_MAX", 120))
    max_spread_pct = float(getattr(config, "OPTIONS_HEDGE_PUT_MAX_SPREAD_PCT", 8.0))
    rfr = float(getattr(config, "RISK_FREE_RATE", 0.045))
    try:
        expirations = await fetch_expirations(client, symbol)
    except Exception as e:
        logger.warning(f"[HEDGE_PUT] {symbol}: fetch_expirations failed: {e}")
        return None
    if not expirations:
        return None
    now = datetime.now()
    candidate_exps: List[Tuple[str, int]] = []
    for exp in expirations:
        try:
            d = (datetime.strptime(exp, "%Y-%m-%d") - now).days
        except Exception:
            continue
        if dte_min <= d <= dte_max:
            candidate_exps.append((exp, d))
    if not candidate_exps:
        return None
    candidate_exps.sort(key=lambda x: abs(x[1] - max(losing_position_dte, dte_min)))
    target_exp, target_dte = candidate_exps[0]
    try:
        chain = await fetch_option_chain(client, symbol, target_exp)
    except Exception as e:
        logger.warning(f"[HEDGE_PUT] {symbol}: fetch_option_chain failed: {e}")
        return None
    if not chain:
        return None
    puts: List[Dict[str, Any]] = []
    T = max(1.0 / 365.0, target_dte / 365.0)
    for opt in chain:
        if (opt.get("option_type") or "").lower() != "put":
            continue
        bid = float(opt.get("bid", 0) or 0)
        ask = float(opt.get("ask", 0) or 0)
        if bid <= 0 or ask <= 0:
            continue
        mid = (bid + ask) / 2.0
        if mid <= 0:
            continue
        spread_pct = (ask - bid) / mid * 100.0
        if spread_pct > max_spread_pct:
            continue
        strike = float(opt.get("strike", 0) or 0)
        if strike <= 0:
            continue
        try:
            iv_c = implied_vol(mid, underlying_price, strike, T, rfr, is_call=False)
        except Exception:
            iv_c = None
        if iv_c is None or iv_c <= 0:
            continue
        d1 = (math.log(underlying_price / strike) + (rfr + 0.5 * iv_c * iv_c) * T) / (iv_c * math.sqrt(T))
        delta_put = _norm_cdf(d1) - 1.0
        delta_mag = abs(delta_put)
        if not (target_delta_min <= delta_mag <= target_delta_max):
            continue
        puts.append({
            "occ": opt.get("symbol"),
            "strike": strike,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "iv_c": iv_c,
            "delta": delta_put,
            "delta_mag": delta_mag,
            "spread_pct": spread_pct,
            "volume": int(opt.get("volume", 0) or 0),
            "open_interest": int(opt.get("open_interest", 0) or 0),
        })
    if not puts:
        return None
    ivs_sorted = sorted(p["iv_c"] for p in puts)
    n = len(ivs_sorted)
    for p in puts:
        rank_idx = sum(1 for v in ivs_sorted if v <= p["iv_c"])
        p["iv_chain_rank"] = (rank_idx / n) * 100.0 if n > 0 else 50.0
    cheap_puts = [p for p in puts if p["iv_chain_rank"] <= max_iv_rank_pct]
    if not cheap_puts:
        return None
    cheap_puts.sort(key=lambda p: (p["ask"], -p["open_interest"]))
    chosen = cheap_puts[0]
    chosen["expiration"] = target_exp
    chosen["dte"] = target_dte
    return chosen


async def decide_hedge_action(
    symbol: str,
    option_type: str,
    indicators: Dict[str, Any],
    underlying_price: float,
    losing_qty: int,
    losing_dte: int,
    client,
    config,
) -> HedgeAction:
    """Run the 3-step ladder: HOLD / BUY_PUT / EQUITY_HEDGE."""
    is_bottom, bottom_reason = at_confirmed_bottom(symbol, indicators, cfg=config)
    if is_bottom:
        return HedgeAction(action="HOLD", reason=bottom_reason)
    if option_type.lower() == "call":
        choice = await find_underpriced_protective_put(
            client, symbol, underlying_price, losing_qty, losing_dte, config,
        )
        if choice:
            return HedgeAction(
                action="BUY_PUT",
                reason=f"protective_put iv_rank={choice['iv_chain_rank']:.0f}% delta={choice['delta']:+.2f} dte={choice['dte']}",
                put_occ=choice["occ"],
                put_strike=choice["strike"],
                put_expiration=choice["expiration"],
                put_dte=choice["dte"],
                put_iv_chain_rank=choice["iv_chain_rank"],
                put_delta=choice["delta"],
                put_limit_price=round(choice["mid"], 2),
                put_qty=max(1, losing_qty),
            )
    return HedgeAction(
        action="EQUITY_HEDGE",
        reason=f"fallback_no_bottom_no_underpriced_put: {bottom_reason}",
    )


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
