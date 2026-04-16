#!/usr/bin/env python3
"""
Tradier Options Analyzer — finds mispriced options on high-conviction directional signals.

Modes:
  scan     — Find mispriced options on D/4h/W directional signals (default)
  order    — Place an options order (buy_to_open / sell_to_close)
  watch    — Monitor open option positions, sell on local tops/bottoms or theta decay

Usage:
    python tradier_options_analyzer.py                              # Full scan
    python tradier_options_analyzer.py --symbol NVDA                # Single symbol deep dive
    python tradier_options_analyzer.py order NEM 105 call 2026-06-18 --qty 2 --limit 8.95 --account trb
    python tradier_options_analyzer.py watch                        # Monitor open positions (one-shot)
    python tradier_options_analyzer.py watch --daemon               # Continuous monitoring (every 60s)
    python tradier_options_analyzer.py watch --auto-sell             # Auto sell_to_close on exit signals
"""
import asyncio
import json
import logging
import math
import os
import platform
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler

BASE_PATH = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
sys.path.insert(0, str(BASE_PATH))

from config_tradier import TradierConfig
from tradier_api import TradierAPIClient

logger = logging.getLogger("options_analyzer")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    log_dir = Path(os.path.expanduser("~")) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(log_dir / "tradier_options_analyzer.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(sh)


# ── Black-Scholes for theoretical pricing ────────────────────────────────────

def norm_cdf(x: float) -> float:
    """Standard normal CDF approximation (Abramowitz & Stegun)."""
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x / 2.0)
    return 0.5 * (1.0 + sign * y)


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    """Black-Scholes option price."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0, (S - K) if is_call else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if is_call:
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> Dict[str, float]:
    """Compute delta, gamma, theta, vega for an option."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return {"delta": 1.0 if is_call else -1.0, "gamma": 0, "theta": 0, "vega": 0}
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    pdf_d1 = norm_pdf(d1)
    if is_call:
        delta = norm_cdf(d1)
        theta = (-S * pdf_d1 * sigma / (2 * sqrt_T) - r * K * math.exp(-r * T) * norm_cdf(d2)) / 365
    else:
        delta = norm_cdf(d1) - 1
        theta = (-S * pdf_d1 * sigma / (2 * sqrt_T) + r * K * math.exp(-r * T) * norm_cdf(-d2)) / 365
    gamma = pdf_d1 / (S * sigma * sqrt_T)
    vega = S * pdf_d1 * sqrt_T / 100
    return {"delta": round(delta, 4), "gamma": round(gamma, 6), "theta": round(theta, 4), "vega": round(vega, 4)}


def implied_vol(market_price: float, S: float, K: float, T: float, r: float, is_call: bool, tol: float = 1e-5, max_iter: int = 100) -> Optional[float]:
    """Newton-Raphson implied volatility solver."""
    if market_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    intrinsic = max(0, (S - K) if is_call else (K - S))
    if market_price < intrinsic - 0.01:
        return None
    sigma = 0.3
    for _ in range(max_iter):
        price = bs_price(S, K, T, r, sigma, is_call)
        sqrt_T = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
        vega_raw = S * norm_pdf(d1) * sqrt_T
        if vega_raw < 1e-12:
            break
        sigma = sigma - (price - market_price) / vega_raw
        if sigma <= 0.001:
            sigma = 0.001
        if abs(price - market_price) < tol:
            return sigma
    return sigma if 0.01 < sigma < 5.0 else None


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class DirectionalSignal:
    symbol: str
    direction: str  # "LONG" or "SHORT"
    conviction: float  # 0-100
    price: float
    atr_D: float
    signals: Dict[str, Any] = field(default_factory=dict)
    ranking_score: float = 0.0


@dataclass
class OptionOutlier:
    symbol: str
    direction: str
    expiration: str
    strike: float
    option_type: str  # "call" or "put"
    bid: float
    ask: float
    mid: float
    last: float
    volume: int
    open_interest: int
    iv_market: Optional[float]
    iv_computed: Optional[float]
    iv_surface_mean: float
    iv_deviation_pct: float  # how far from surface mean (negative = cheap)
    theo_price: float
    edge_pct: float  # (theo - mid) / mid * 100
    greeks: Dict[str, float]
    dte: int
    moneyness: float  # strike / stock_price
    recommendation: str
    score: float  # composite outlier score


@dataclass
class SpreadOpportunity:
    symbol: str
    direction: str
    spread_type: str  # "bull_call", "bear_put", "bull_put_credit", "bear_call_credit"
    expiration: str
    long_strike: float
    short_strike: float
    net_debit: float  # negative = credit
    max_profit: float
    max_loss: float
    risk_reward: float
    breakeven: float
    probability_profit: float  # rough estimate from delta
    score: float
    dte: int


# ── Signal Detection ─────────────────────────────────────────────────────────

def detect_directional_signals(indicators: Dict[str, Dict], rankings: Dict) -> List[DirectionalSignal]:
    """Find symbols with strong D/4h/W alignment for directional plays."""
    signals = []
    ranking_scores = {}
    if "rankings" in rankings:
        for r in rankings["rankings"]:
            ranking_scores[r["symbol"]] = r.get("final_score_norm", 50)
    for symbol, ind in indicators.items():
        if not ind:
            continue
        price = ind.get("current_price") or ind.get("mark_price") or ind.get("close_D_prev") or ind.get("close_1h_prev")
        if not price or price <= 0:
            continue
        atr_D = ind.get("atr_D", 0) or 0
        if atr_D <= 0:
            continue
        # ── Gather HTF signals ──
        wt1_4h = ind.get("wt1_4h")
        wt1_D = ind.get("wt1_D")
        wt2_4h = ind.get("wt2_4h")
        wt2_D = ind.get("wt2_D")
        wt_cross_4h = ind.get("wt_cross_4h", "")
        wt_cross_D = ind.get("wt_cross_D", "")
        wt_velocity_4h = ind.get("wt_velocity_4h", 0) or 0
        wt_velocity_D = ind.get("wt_velocity_D", 0) or 0
        wt_momentum_4h = ind.get("wt_momentum_state_4h", "")
        wt_momentum_D = ind.get("wt_momentum_state_D", "")
        wt_divergence_D = ind.get("wt_divergence_D", "")
        wt_divergence_4h = ind.get("wt_divergence_4h", "")
        wt_extreme_D = ind.get("wt_extreme_D", False)
        stoch_k_D = ind.get("stoch_k_D", 50) or 50
        stoch_d_D = ind.get("stoch_d_D", 50) or 50
        stoch_k_4h = ind.get("stoch_k_4h", 50) or 50
        stoch_d_4h = ind.get("stoch_d_4h", 50) or 50
        dc_position_D = ind.get("dc_position_D", 0.5) or 0.5
        dc_position_4h = ind.get("dc_position_4h", 0.5) or 0.5
        dc_width_D = ind.get("dc_width_D", 0) or 0
        mfi_D = ind.get("mfi_D", 50) or 50
        mfi_4h = ind.get("mfi_4h", 50) or 50
        adx_D = ind.get("adx_D", 0) or 0
        adx_4h = ind.get("adx_4h", 0) or 0
        if wt1_4h is None or wt1_D is None:
            continue
        # ── LONG signal scoring ──
        long_score = 0
        long_signals = {}
        # WT oversold on D (strongest signal)
        if wt1_D < -50:
            long_score += 20
            long_signals["wt1_D_oversold"] = round(wt1_D, 1)
        elif wt1_D < -30:
            long_score += 10
            long_signals["wt1_D_low"] = round(wt1_D, 1)
        # WT cross bull on D or 4h
        if wt_cross_D == "BULL":
            long_score += 15
            long_signals["wt_cross_D"] = "BULL"
        if wt_cross_4h == "BULL":
            long_score += 10
            long_signals["wt_cross_4h"] = "BULL"
        # WT velocity positive (momentum turning up)
        if wt_velocity_D > 5:
            long_score += 8
            long_signals["wt_velocity_D"] = round(wt_velocity_D, 1)
        if wt_velocity_4h > 10:
            long_score += 5
            long_signals["wt_velocity_4h"] = round(wt_velocity_4h, 1)
        # WT impulse up
        if wt_momentum_4h == "IMPULSE_UP":
            long_score += 8
            long_signals["wt_momentum_4h"] = "IMPULSE_UP"
        # WT hidden bull divergence
        if "BULL" in str(wt_divergence_D):
            long_score += 12
            long_signals["wt_div_D"] = wt_divergence_D
        if "BULL" in str(wt_divergence_4h):
            long_score += 8
            long_signals["wt_div_4h"] = wt_divergence_4h
        # WT extreme on D (oversold extreme = high conviction reversal)
        if wt_extreme_D and wt1_D < -50:
            long_score += 10
            long_signals["wt_extreme_D"] = True
        # Stochastic oversold
        if stoch_k_D < 20:
            long_score += 10
            long_signals["stoch_k_D_oversold"] = round(stoch_k_D, 1)
        if stoch_k_D > stoch_d_D and stoch_k_D < 40:
            long_score += 5
            long_signals["stoch_cross_D"] = f"k={round(stoch_k_D,1)}>d={round(stoch_d_D,1)}"
        # DC at bottom
        if dc_position_D < 0.15:
            long_score += 8
            long_signals["dc_position_D"] = round(dc_position_D, 3)
        # MFI oversold
        if mfi_D < 25:
            long_score += 6
            long_signals["mfi_D_oversold"] = round(mfi_D, 1)
        # ADX trending
        if adx_D > 25:
            long_score += 4
            long_signals["adx_D_trending"] = round(adx_D, 1)
        # DC wide (big range = bigger option moves)
        if dc_width_D > 8:
            long_score += 3
            long_signals["dc_width_D"] = round(dc_width_D, 1)
        # ── SHORT signal scoring ──
        short_score = 0
        short_signals = {}
        if wt1_D > 50:
            short_score += 20
            short_signals["wt1_D_overbought"] = round(wt1_D, 1)
        elif wt1_D > 30:
            short_score += 10
            short_signals["wt1_D_high"] = round(wt1_D, 1)
        if wt_cross_D == "BEAR":
            short_score += 15
            short_signals["wt_cross_D"] = "BEAR"
        if wt_cross_4h == "BEAR":
            short_score += 10
            short_signals["wt_cross_4h"] = "BEAR"
        if wt_velocity_D < -5:
            short_score += 8
            short_signals["wt_velocity_D"] = round(wt_velocity_D, 1)
        if wt_velocity_4h < -10:
            short_score += 5
            short_signals["wt_velocity_4h"] = round(wt_velocity_4h, 1)
        if wt_momentum_4h == "IMPULSE_DOWN":
            short_score += 8
            short_signals["wt_momentum_4h"] = "IMPULSE_DOWN"
        if "BEAR" in str(wt_divergence_D):
            short_score += 12
            short_signals["wt_div_D"] = wt_divergence_D
        if "BEAR" in str(wt_divergence_4h):
            short_score += 8
            short_signals["wt_div_4h"] = wt_divergence_4h
        if wt_extreme_D and wt1_D > 50:
            short_score += 10
            short_signals["wt_extreme_D"] = True
        if stoch_k_D > 80:
            short_score += 10
            short_signals["stoch_k_D_overbought"] = round(stoch_k_D, 1)
        if stoch_k_D < stoch_d_D and stoch_k_D > 60:
            short_score += 5
            short_signals["stoch_cross_D"] = f"k={round(stoch_k_D,1)}<d={round(stoch_d_D,1)}"
        if dc_position_D > 0.85:
            short_score += 8
            short_signals["dc_position_D"] = round(dc_position_D, 3)
        if mfi_D > 75:
            short_score += 6
            short_signals["mfi_D_overbought"] = round(mfi_D, 1)
        if adx_D > 25:
            short_score += 4
            short_signals["adx_D_trending"] = round(adx_D, 1)
        if dc_width_D > 8:
            short_score += 3
            short_signals["dc_width_D"] = round(dc_width_D, 1)
        rank_score = ranking_scores.get(symbol, 50)
        # Boost conviction if ranking agrees with direction
        if long_score >= 30:
            if rank_score > 70:
                long_score += 10  # ranking confirms uptrend
            signals.append(DirectionalSignal(symbol=symbol, direction="LONG", conviction=min(long_score, 100), price=price, atr_D=atr_D, signals=long_signals, ranking_score=rank_score))
        if short_score >= 30:
            if rank_score < 30:
                short_score += 10  # ranking confirms downtrend
            signals.append(DirectionalSignal(symbol=symbol, direction="SHORT", conviction=min(short_score, 100), price=price, atr_D=atr_D, signals=short_signals, ranking_score=rank_score))
    signals.sort(key=lambda s: s.conviction, reverse=True)
    return signals


# ── Options Chain Fetching ───────────────────────────────────────────────────

async def fetch_expirations(client: TradierAPIClient, symbol: str) -> List[str]:
    """Get all available expiration dates for a symbol."""
    res = await client._request("GET", "/markets/options/expirations", params={"symbol": symbol, "includeAllRoots": "true", "strikes": "false"}, use_data_context=True)
    if res and "expirations" in res and res["expirations"]:
        dates = res["expirations"].get("date", [])
        if isinstance(dates, str):
            return [dates]
        return dates if isinstance(dates, list) else []
    return []


async def fetch_option_chain(client: TradierAPIClient, symbol: str, expiration: str) -> List[Dict]:
    """Fetch full option chain for a symbol and expiration with greeks."""
    res = await client._request("GET", "/markets/options/chains", params={"symbol": symbol, "expiration": expiration, "greeks": "true"}, use_data_context=True)
    if res and "options" in res and res["options"]:
        chain = res["options"].get("option", [])
        if isinstance(chain, dict):
            return [chain]
        return chain if isinstance(chain, list) else []
    return []


async def fetch_quote(client: TradierAPIClient, symbol: str) -> Dict:
    """Get current quote for underlying."""
    return await client.get_quote(symbol)


# ── Analysis Engine ──────────────────────────────────────────────────────────

def analyze_chain_for_outliers(signal: DirectionalSignal, chain: List[Dict], expiration: str, risk_free_rate: float = 0.043) -> Tuple[List[OptionOutlier], List[SpreadOpportunity]]:
    """Analyze an option chain for mispriced options and spread opportunities."""
    outliers = []
    spreads = []
    S = signal.price
    exp_date = datetime.strptime(expiration, "%Y-%m-%d")
    dte = max(1, (exp_date - datetime.now()).days)
    T = dte / 365.0
    # Separate calls and puts, filter for reasonable strikes
    calls = []
    puts = []
    for opt in chain:
        if not opt:
            continue
        strike = opt.get("strike", 0)
        if strike <= 0:
            continue
        bid = opt.get("bid", 0) or 0
        ask = opt.get("ask", 0) or 0
        if bid <= 0 and ask <= 0:
            continue
        moneyness = strike / S
        # Focus on strikes within 20% of current price
        if moneyness < 0.80 or moneyness > 1.20:
            continue
        mid = (bid + ask) / 2.0
        opt_data = {**opt, "mid": mid, "moneyness": moneyness, "dte": dte, "T": T}
        if opt.get("option_type") == "call":
            calls.append(opt_data)
        else:
            puts.append(opt_data)
    # Compute IV surface for calls and puts separately
    call_ivs = []
    put_ivs = []
    for opt in calls:
        iv = implied_vol(opt["mid"], S, opt["strike"], T, risk_free_rate, True)
        opt["iv_computed"] = iv
        if iv and 0.05 < iv < 3.0:
            call_ivs.append(iv)
    for opt in puts:
        iv = implied_vol(opt["mid"], S, opt["strike"], T, risk_free_rate, False)
        opt["iv_computed"] = iv
        if iv and 0.05 < iv < 3.0:
            put_ivs.append(iv)
    call_iv_mean = sum(call_ivs) / len(call_ivs) if call_ivs else 0.3
    put_iv_mean = sum(put_ivs) / len(put_ivs) if put_ivs else 0.3
    # Analyze each option for mispricing
    is_long = signal.direction == "LONG"
    for opt_list, is_call, iv_mean in [(calls, True, call_iv_mean), (puts, False, put_iv_mean)]:
        for opt in opt_list:
            iv_c = opt.get("iv_computed")
            if not iv_c:
                continue
            # IV deviation from surface mean
            iv_dev_pct = ((iv_c - iv_mean) / iv_mean) * 100 if iv_mean > 0 else 0
            # Theoretical price at surface mean IV
            theo = bs_price(S, opt["strike"], T, risk_free_rate, iv_mean, is_call)
            edge_pct = ((theo - opt["mid"]) / opt["mid"]) * 100 if opt["mid"] > 0.01 else 0
            greeks = bs_greeks(S, opt["strike"], T, risk_free_rate, iv_c, is_call)
            # Market IV from Tradier greeks (if available)
            market_greeks = opt.get("greeks", {}) or {}
            iv_market = market_greeks.get("mid_iv")
            # Score this option
            score = 0
            recommendation = ""
            option_type_str = "call" if is_call else "put"
            # ── Directional alignment scoring ──
            if is_long and is_call:
                # Buying calls for bullish signal
                if iv_dev_pct < -15:
                    score += 30  # cheap call on bullish setup
                    recommendation = "BUY_CHEAP_CALL"
                elif iv_dev_pct < -8:
                    score += 15
                    recommendation = "BUY_UNDERPRICED_CALL"
            elif is_long and not is_call:
                # Selling puts for bullish signal (premium collection)
                if iv_dev_pct > 15:
                    score += 25
                    recommendation = "SELL_OVERPRICED_PUT"
                elif iv_dev_pct > 8:
                    score += 12
                    recommendation = "SELL_RICH_PUT"
            elif not is_long and not is_call:
                # Buying puts for bearish signal
                if iv_dev_pct < -15:
                    score += 30
                    recommendation = "BUY_CHEAP_PUT"
                elif iv_dev_pct < -8:
                    score += 15
                    recommendation = "BUY_UNDERPRICED_PUT"
            elif not is_long and is_call:
                # Selling calls for bearish signal
                if iv_dev_pct > 15:
                    score += 25
                    recommendation = "SELL_OVERPRICED_CALL"
                elif iv_dev_pct > 8:
                    score += 12
                    recommendation = "SELL_RICH_CALL"
            if not recommendation:
                continue
            # Moneyness bonus — backtest: 5-7% OTM = Sharpe 2.13-2.20, 1-3% OTM loses money
            if is_long:
                if is_call and 1.05 <= opt["moneyness"] <= 1.08:
                    score += 15  # sweet spot: 5-8% OTM call (backtest winner)
                elif is_call and 1.03 <= opt["moneyness"] < 1.05:
                    score += 8
                elif is_call and 1.01 < opt["moneyness"] < 1.03:
                    score += 3  # near ATM = expensive, more risk
                elif not is_call and 0.92 <= opt["moneyness"] <= 0.95:
                    score += 15  # sweet spot: 5-8% OTM put
                elif not is_call and 0.95 < opt["moneyness"] <= 0.97:
                    score += 8
            else:
                if not is_call and 0.92 <= opt["moneyness"] <= 0.95:
                    score += 15
                elif is_call and 1.01 < opt["moneyness"] < 1.08:
                    score += 10
            # Volume/OI bonus (liquidity = real pricing)
            vol = opt.get("volume", 0) or 0
            oi = opt.get("open_interest", 0) or 0
            if vol > 100:
                score += 5
            if oi > 500:
                score += 5
            if oi > 2000:
                score += 5
            # Edge bonus
            abs_edge = abs(edge_pct)
            if abs_edge > 20:
                score += 10
            elif abs_edge > 10:
                score += 5
            # DTE sweet spot (14-60 DTE for directional)
            if 14 <= dte <= 60:
                score += 8
            elif 7 <= dte <= 90:
                score += 4
            # Tight spread bonus
            spread_width = (opt.get("ask", 0) or 0) - (opt.get("bid", 0) or 0)
            if opt["mid"] > 0 and spread_width / opt["mid"] < 0.10:
                score += 5
            # Signal conviction bonus
            score += int(signal.conviction * 0.2)
            outliers.append(OptionOutlier(symbol=signal.symbol, direction=signal.direction, expiration=expiration, strike=opt["strike"], option_type=option_type_str, bid=opt.get("bid", 0) or 0, ask=opt.get("ask", 0) or 0, mid=opt["mid"], last=opt.get("last", 0) or 0, volume=vol, open_interest=oi, iv_market=iv_market, iv_computed=round(iv_c, 4) if iv_c else None, iv_surface_mean=round(iv_mean, 4), iv_deviation_pct=round(iv_dev_pct, 2), theo_price=round(theo, 4), edge_pct=round(edge_pct, 2), greeks=greeks, dte=dte, moneyness=round(opt["moneyness"], 4), recommendation=recommendation, score=score))
    # ── Spread construction ──
    # Sort calls and puts by strike
    calls_sorted = sorted([c for c in calls if c.get("iv_computed")], key=lambda x: x["strike"])
    puts_sorted = sorted([p for p in puts if p.get("iv_computed")], key=lambda x: x["strike"])
    if is_long:
        # Bull call spread: buy lower strike call, sell higher strike call
        for i, long_call in enumerate(calls_sorted):
            for short_call in calls_sorted[i + 1:]:
                if short_call["strike"] - long_call["strike"] > S * 0.15:
                    break
                debit = long_call["mid"] - short_call["mid"]
                if debit <= 0:
                    continue
                width = short_call["strike"] - long_call["strike"]
                max_profit = width - debit
                max_loss = debit
                if max_loss <= 0:
                    continue
                rr = max_profit / max_loss
                be = long_call["strike"] + debit
                prob = abs(short_call.get("greeks", {}).get("delta", 0.3)) if short_call.get("greeks") else 0.3
                spread_score = 0
                if rr > 2.0:
                    spread_score += 20
                elif rr > 1.5:
                    spread_score += 10
                long_iv = long_call.get("iv_computed", 0.3)
                short_iv = short_call.get("iv_computed", 0.3)
                if long_iv and short_iv and short_iv > long_iv * 1.05:
                    spread_score += 15  # selling richer vol
                if 14 <= dte <= 60:
                    spread_score += 5
                spread_score += int(signal.conviction * 0.15)
                if spread_score >= 15:
                    spreads.append(SpreadOpportunity(symbol=signal.symbol, direction=signal.direction, spread_type="bull_call_debit", expiration=expiration, long_strike=long_call["strike"], short_strike=short_call["strike"], net_debit=round(debit, 2), max_profit=round(max_profit, 2), max_loss=round(max_loss, 2), risk_reward=round(rr, 2), breakeven=round(be, 2), probability_profit=round(1 - prob, 3), score=spread_score, dte=dte))
        # Bull put spread (credit): sell higher strike put, buy lower strike put
        for i in range(len(puts_sorted) - 1, 0, -1):
            short_put = puts_sorted[i]
            for j in range(i - 1, -1, -1):
                long_put = puts_sorted[j]
                if short_put["strike"] - long_put["strike"] > S * 0.15:
                    break
                credit = short_put["mid"] - long_put["mid"]
                if credit <= 0:
                    continue
                width = short_put["strike"] - long_put["strike"]
                max_loss = width - credit
                if max_loss <= 0:
                    continue
                rr = credit / max_loss
                be = short_put["strike"] - credit
                prob = 1 - abs(short_put.get("greeks", {}).get("delta", 0.3)) if short_put.get("greeks") else 0.7
                spread_score = 0
                if rr > 0.5:
                    spread_score += 15
                if prob > 0.65:
                    spread_score += 10
                short_iv = short_put.get("iv_computed", 0.3)
                long_iv = long_put.get("iv_computed", 0.3)
                if short_iv and long_iv and short_iv > long_iv * 1.05:
                    spread_score += 10
                if 14 <= dte <= 45:
                    spread_score += 5
                spread_score += int(signal.conviction * 0.15)
                if spread_score >= 15:
                    spreads.append(SpreadOpportunity(symbol=signal.symbol, direction=signal.direction, spread_type="bull_put_credit", expiration=expiration, long_strike=long_put["strike"], short_strike=short_put["strike"], net_debit=round(-credit, 2), max_profit=round(credit, 2), max_loss=round(max_loss, 2), risk_reward=round(rr, 2), breakeven=round(be, 2), probability_profit=round(prob, 3), score=spread_score, dte=dte))
    else:
        # Bear put spread: buy higher strike put, sell lower strike put
        for i in range(len(puts_sorted) - 1, 0, -1):
            long_put = puts_sorted[i]
            for j in range(i - 1, -1, -1):
                short_put = puts_sorted[j]
                if long_put["strike"] - short_put["strike"] > S * 0.15:
                    break
                debit = long_put["mid"] - short_put["mid"]
                if debit <= 0:
                    continue
                width = long_put["strike"] - short_put["strike"]
                max_profit = width - debit
                max_loss = debit
                if max_loss <= 0:
                    continue
                rr = max_profit / max_loss
                be = long_put["strike"] - debit
                prob = abs(short_put.get("greeks", {}).get("delta", 0.3)) if short_put.get("greeks") else 0.3
                spread_score = 0
                if rr > 2.0:
                    spread_score += 20
                elif rr > 1.5:
                    spread_score += 10
                long_iv = long_put.get("iv_computed", 0.3)
                short_iv = short_put.get("iv_computed", 0.3)
                if long_iv and short_iv and short_iv > long_iv * 1.05:
                    spread_score += 15
                if 14 <= dte <= 60:
                    spread_score += 5
                spread_score += int(signal.conviction * 0.15)
                if spread_score >= 15:
                    spreads.append(SpreadOpportunity(symbol=signal.symbol, direction=signal.direction, spread_type="bear_put_debit", expiration=expiration, long_strike=long_put["strike"], short_strike=short_put["strike"], net_debit=round(debit, 2), max_profit=round(max_profit, 2), max_loss=round(max_loss, 2), risk_reward=round(rr, 2), breakeven=round(be, 2), probability_profit=round(1 - prob, 3), score=spread_score, dte=dte))
        # Bear call spread (credit): sell lower strike call, buy higher strike call
        for i, short_call in enumerate(calls_sorted):
            for long_call in calls_sorted[i + 1:]:
                if long_call["strike"] - short_call["strike"] > S * 0.15:
                    break
                credit = short_call["mid"] - long_call["mid"]
                if credit <= 0:
                    continue
                width = long_call["strike"] - short_call["strike"]
                max_loss = width - credit
                if max_loss <= 0:
                    continue
                rr = credit / max_loss
                be = short_call["strike"] + credit
                prob = 1 - abs(short_call.get("greeks", {}).get("delta", 0.3)) if short_call.get("greeks") else 0.7
                spread_score = 0
                if rr > 0.5:
                    spread_score += 15
                if prob > 0.65:
                    spread_score += 10
                short_iv = short_call.get("iv_computed", 0.3)
                long_iv = long_call.get("iv_computed", 0.3)
                if short_iv and long_iv and short_iv > long_iv * 1.05:
                    spread_score += 10
                if 14 <= dte <= 45:
                    spread_score += 5
                spread_score += int(signal.conviction * 0.15)
                if spread_score >= 15:
                    spreads.append(SpreadOpportunity(symbol=signal.symbol, direction=signal.direction, spread_type="bear_call_credit", expiration=expiration, long_strike=long_call["strike"], short_strike=short_call["strike"], net_debit=round(-credit, 2), max_profit=round(credit, 2), max_loss=round(max_loss, 2), risk_reward=round(rr, 2), breakeven=round(be, 2), probability_profit=round(prob, 3), score=spread_score, dte=dte))
    outliers.sort(key=lambda x: x.score, reverse=True)
    spreads.sort(key=lambda x: x.score, reverse=True)
    return outliers, spreads


# ── OCC Symbol Builder ────────────────────────────────────────────────────────

def build_occ_symbol(symbol: str, expiration: str, option_type: str, strike: float) -> str:
    """Build OCC option symbol like NEM260618C00105000."""
    exp_dt = datetime.strptime(expiration, "%Y-%m-%d")
    exp_str = exp_dt.strftime("%y%m%d")
    cp = "C" if option_type.lower() == "call" else "P"
    strike_int = int(strike * 1000)
    return f"{symbol.upper()}{exp_str}{cp}{strike_int:08d}"


# ── Options Order Placement ──────────────────────────────────────────────────

async def place_option_order(client: TradierAPIClient, symbol: str, option_symbol: str, side: str, qty: int, order_type: str = "limit", price: float = None, duration: str = "day") -> Dict:
    """Place an option order via Tradier API. Side: buy_to_open, sell_to_close, etc."""
    data = {"class": "option", "symbol": symbol.upper(), "option_symbol": option_symbol, "side": side.lower(), "quantity": str(qty), "type": order_type.lower(), "duration": duration.lower()}
    if price is not None:
        data["price"] = f"{float(price):.2f}"
    account_id = client._current_id
    if not account_id:
        return {"error": "Missing Account ID"}
    res = await client._request("POST", f"/accounts/{account_id}/orders", data=data, use_data_context=False)
    if not res:
        return {"status": "error", "reason": "Gateway Rejected / Bad Request"}
    return res


async def smart_fill_option(client: TradierAPIClient, symbol: str, occ: str, side: str, qty: int, initial_price: float, bid: float, ask: float, max_walk_steps: int = 5, walk_interval: int = 120, account_key: str = "trb") -> Dict:
    """Smart fill with price discipline.
    For buy_to_open: start at bid (best for us), walk UP toward mid. NEVER above mid.
    For sell_to_close: start ABOVE ask (best for us), walk DOWN toward mid. NEVER below mid.
    We are not desperate — patience gets better fills.
    """
    is_buy = "buy" in side.lower()
    mid = (bid + ask) / 2.0
    spread = ask - bid
    if spread <= 0:
        spread = 0.05
    # Walk range is only half the spread — we NEVER cross mid
    half_spread = spread / 2.0
    step_size = half_spread / max(max_walk_steps, 1)
    # Start price: for sells, start 5% ABOVE ask. For buys, start at bid.
    if initial_price:
        current_price = initial_price
    else:
        if is_buy:
            current_price = bid
        else:
            current_price = round(ask * 1.05, 2)  # Start 5% above ask — aim high
    account_id = client._current_id
    active_order_id = None
    # SAFETY: For sell_to_close, verify position actually exists in API before placing orders
    if not is_buy and account_id:
        try:
            api_pos = await client._request("GET", f"/accounts/{account_id}/positions", use_data_context=False)
            held_occ = set()
            if api_pos and "positions" in api_pos:
                inner = api_pos["positions"]
                if isinstance(inner, dict) and "position" in inner:
                    plist = inner["position"] if isinstance(inner["position"], list) else [inner["position"]]
                elif isinstance(inner, list):
                    plist = inner
                else:
                    plist = []
                for p in plist:
                    p_qty = float(p.get("quantity", 0) or 0)
                    if abs(p_qty) > 0:
                        held_occ.add(p.get("symbol", ""))
            if occ not in held_occ:
                logger.critical(f"[PHANTOM_SELL_BLOCK] {occ}: sell_to_close BLOCKED — position not found in API. Held: {held_occ}")
                print(f"    \033[91mBLOCKED sell_to_close {occ} — position does NOT exist in API\033[0m")
                return {"errors": f"Position {occ} not held in API — phantom sell blocked"}
        except Exception as e:
            logger.warning(f"Smart fill {occ}: position verification failed: {e}")
    # SAFETY: For sell_to_close, cancel any existing open sell orders on this OCC first
    # Tradier rejects sells when open_sell_qty + new_qty > position_qty
    if not is_buy and account_id:
        try:
            all_orders = await client.get_orders(account_key)
            for existing in (all_orders or []):
                if existing.get("status") not in ("pending", "open"):
                    continue
                ex_side = existing.get("side", "")
                if "sell" not in ex_side:
                    continue
                ex_occ = ""
                if "leg" in existing:
                    legs = existing["leg"] if isinstance(existing["leg"], list) else [existing["leg"]]
                    for leg in legs:
                        ex_occ = leg.get("option_symbol", "")
                        if ex_occ:
                            break
                if not ex_occ:
                    ex_occ = existing.get("option_symbol", existing.get("symbol", ""))
                if ex_occ == occ:
                    ex_id = existing.get("id")
                    print(f"    Cancelling existing sell order {ex_id} on {occ} before placing new one")
                    logger.info(f"Smart fill {occ}: cancelling existing sell order {ex_id}")
                    await client.cancel_order(account_key, ex_id)
                    await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"Smart fill {occ}: failed to check/cancel existing sells: {e}")
    for step in range(max_walk_steps + 1):
        # Clamp price — NEVER cross mid
        if is_buy:
            current_price = min(current_price, mid)  # Never pay above mid
        else:
            current_price = max(current_price, mid)  # Never sell below mid
        current_price = round(current_price, 2)
        label = f"Step {step}/{max_walk_steps}"
        if step == 0:
            # Place initial order
            print(f"    [{label}] Placing limit @ ${current_price:.2f}  (bid=${bid:.2f} ask=${ask:.2f} mid=${mid:.2f})")
            res = await place_option_order(client, symbol, occ, side, qty, "limit", current_price)
            if "order" in res:
                active_order_id = res["order"].get("id")
                status = res["order"].get("status", "")
                print(f"    [{label}] Order ID: {active_order_id}  Status: {status}")
                logger.info(f"Smart fill {occ} step {step}: limit ${current_price:.2f} → ID={active_order_id}")
                if status == "filled":
                    print(f"    \033[92mFILLED immediately @ ${current_price:.2f}\033[0m")
                    return res
                if status == "rejected":
                    print(f"    \033[91mORDER REJECTED by exchange — position may not exist. STOP.\033[0m")
                    logger.warning(f"Smart fill {occ} REJECTED by exchange — phantom position? Stopping walk.")
                    return {"errors": f"Order rejected by exchange (status=rejected, id={active_order_id})"}
            elif "errors" in res:
                print(f"    \033[91mORDER REJECTED\033[0m  {res['errors']}")
                return res
            else:
                print(f"    Unexpected: {json.dumps(res, indent=2)}")
                return res
        else:
            # Check if previous order filled
            if active_order_id:
                order_status = await client._request("GET", f"/accounts/{account_id}/orders/{active_order_id}", use_data_context=False)
                if order_status and "order" in order_status:
                    st = order_status["order"].get("status", "")
                    if st == "filled":
                        fill_price = order_status["order"].get("avg_fill_price", current_price)
                        print(f"    \033[92mFILLED @ ${fill_price}\033[0m after {step} walk steps")
                        logger.info(f"Smart fill {occ} FILLED @ ${fill_price} after {step} steps")
                        return order_status
                    elif st in ("canceled", "rejected", "expired"):
                        print(f"    Order {active_order_id} status: {st}")
                        active_order_id = None
                # Cancel and replace with better price
                if active_order_id:
                    await client._request("DELETE", f"/accounts/{account_id}/orders/{active_order_id}", use_data_context=False)
                    await asyncio.sleep(1)
            # Walk the price toward mid (NEVER past mid)
            if is_buy:
                current_price += step_size  # Walk up from bid toward mid
            else:
                current_price -= step_size  # Walk down from above-ask toward mid
            current_price = round(current_price, 2)
            # Re-fetch quotes to get current bid/ask
            opt_quote_res = await client._request("GET", "/markets/quotes", params={"symbols": occ}, use_data_context=True)
            if opt_quote_res and "quotes" in opt_quote_res and "quote" in opt_quote_res["quotes"]:
                q = opt_quote_res["quotes"]["quote"]
                if isinstance(q, list):
                    q = q[0] if q else {}
                new_bid = q.get("bid", bid) or bid
                new_ask = q.get("ask", ask) or ask
                bid, ask = new_bid, new_ask
                mid = (bid + ask) / 2.0
            # Enforce mid floor/ceiling after re-quote
            if is_buy:
                current_price = min(current_price, mid)
            else:
                current_price = max(current_price, mid)
            current_price = round(current_price, 2)
            print(f"    [{label}] Walking to ${current_price:.2f}  (bid=${bid:.2f} ask=${ask:.2f} mid=${mid:.2f})")
            res = await place_option_order(client, symbol, occ, side, qty, "limit", current_price)
            if "order" in res:
                active_order_id = res["order"].get("id")
                status = res["order"].get("status", "")
                if status == "filled":
                    print(f"    \033[92mFILLED @ ${current_price:.2f}\033[0m")
                    return res
                if status == "rejected":
                    print(f"    \033[91mStep {step} REJECTED by exchange — stopping walk.\033[0m")
                    logger.warning(f"Smart fill {occ} step {step} REJECTED — phantom position? Stopping.")
                    return {"errors": f"Order rejected by exchange at step {step} (id={active_order_id})"}
                logger.info(f"Smart fill {occ} step {step}: limit ${current_price:.2f} → ID={active_order_id}")
            elif "errors" in res:
                print(f"    \033[91mStep {step} rejected\033[0m: {res['errors']}")
                return res
        if step < max_walk_steps:
            print(f"    Waiting {walk_interval}s for fill...")
            await asyncio.sleep(walk_interval)
    # Final check
    if active_order_id:
        order_status = await client._request("GET", f"/accounts/{account_id}/orders/{active_order_id}", use_data_context=False)
        if order_status and "order" in order_status:
            st = order_status["order"].get("status", "")
            if st == "filled":
                fill_price = order_status["order"].get("avg_fill_price", current_price)
                print(f"    \033[92mFILLED @ ${fill_price}\033[0m")
                return order_status
            print(f"    Final order status: {st}. Order ID {active_order_id} still open.")
    return {"status": "pending", "order_id": active_order_id, "final_price": current_price}


async def run_order(args):
    """Handle the 'order' subcommand."""
    config = TradierConfig()
    account_key = args.account or "trb"
    client = TradierAPIClient(config, account_key=account_key)
    await client.connect()
    try:
        occ = build_occ_symbol(args.order_symbol, args.expiration, args.call_put, args.strike)
        side = args.side or "buy_to_open"
        use_smart = getattr(args, "smart", False)
        price = args.limit
        print(f"\n  Placing option order:")
        print(f"    Account:  {account_key}")
        print(f"    Symbol:   {args.order_symbol}")
        print(f"    Option:   {occ}")
        print(f"    Side:     {side}")
        print(f"    Qty:      {args.qty}")
        if use_smart:
            # Get current bid/ask for smart fill
            opt_quote_res = await client._request("GET", "/markets/quotes", params={"symbols": occ}, use_data_context=True)
            bid, ask = 0, 0
            if opt_quote_res and "quotes" in opt_quote_res and "quote" in opt_quote_res["quotes"]:
                q = opt_quote_res["quotes"]["quote"]
                if isinstance(q, list):
                    q = q[0] if q else {}
                bid = q.get("bid", 0) or 0
                ask = q.get("ask", 0) or 0
            print(f"    Mode:     SMART FILL (bid=${bid:.2f} ask=${ask:.2f})")
            if price:
                print(f"    Start at: ${price:.2f}")
            print()
            res = await smart_fill_option(client, args.order_symbol, occ, side, args.qty, price, bid, ask, max_walk_steps=5, walk_interval=120, account_key=account_key)
        else:
            order_type = "limit" if price else "market"
            print(f"    Type:     {order_type}")
            if price:
                print(f"    Limit:    ${price:.2f}")
            print()
            res = await place_option_order(client, args.order_symbol, occ, side, args.qty, order_type, price)
        if "order" in res:
            order_info = res["order"]
            order_id = order_info.get("id", "unknown")
            status = order_info.get("status", "unknown")
            print(f"  \033[92mORDER PLACED\033[0m  ID: {order_id}  Status: {status}")
            logger.info(f"Option order placed: {occ} {side} x{args.qty} @ ${price} → ID={order_id} status={status}")
        elif "errors" in res:
            err = res["errors"]
            print(f"  \033[91mORDER REJECTED\033[0m  {err}")
            logger.error(f"Option order rejected: {occ} {side} x{args.qty} → {err}")
        elif res.get("status") == "pending":
            print(f"  \033[93mPENDING\033[0m — smart fill did not complete. Order ID: {res.get('order_id')} still active @ ${res.get('final_price'):.2f}")
        else:
            print(f"  Response: {json.dumps(res, indent=2)}")
    finally:
        await client.close()


# ── Options Position Watchdog ────────────────────────────────────────────────

@dataclass
class OptionPosition:
    symbol: str
    option_symbol: str
    quantity: int
    cost_basis: float
    current_price: float
    current_bid: float
    current_ask: float
    unrealized_pnl: float
    unrealized_pct: float
    option_type: str  # call or put
    strike: float
    expiration: str
    dte: int
    underlying_price: float
    iv_current: Optional[float]
    delta: float
    theta: float
    gamma: float
    vega: float


@dataclass
class ExitSignal:
    position: OptionPosition
    reason: str
    urgency: str  # "HIGH", "MEDIUM", "LOW"
    action: str  # "SELL_NOW", "TIGHTEN_STOP", "WATCH"
    detail: str
    score: int  # 0-100, higher = more urgent


def parse_occ_symbol(occ: str) -> Optional[Dict]:
    """Parse OCC symbol like NEM260618C00105000 into components."""
    import re
    m = re.match(r'^([A-Z]+)(\d{6})([CP])(\d{8})$', occ)
    if not m:
        return None
    symbol, exp_str, cp, strike_str = m.groups()
    exp_date = datetime.strptime(exp_str, "%y%m%d").strftime("%Y-%m-%d")
    strike = int(strike_str) / 1000.0
    return {"symbol": symbol, "expiration": exp_date, "option_type": "call" if cp == "C" else "put", "strike": strike}


async def get_option_positions(client: TradierAPIClient) -> List[Dict]:
    """Fetch current option positions from account."""
    account_id = client._current_id
    if not account_id:
        return []
    res = await client._request("GET", f"/accounts/{account_id}/positions", use_data_context=False)
    if not res or "positions" not in res:
        return []
    inner = res["positions"]
    if inner == "null" or inner is None:
        return []
    if isinstance(inner, dict) and "position" in inner:
        p = inner["position"]
        positions = p if isinstance(p, list) else [p]
    elif isinstance(inner, list):
        positions = inner
    else:
        return []
    option_positions = []
    for pos in positions:
        sym = pos.get("symbol", "")
        parsed = parse_occ_symbol(sym)
        if parsed:
            qty = 0
            try:
                qty = abs(float(pos.get("quantity", 0) or 0))
            except (ValueError, TypeError):
                pass
            if qty <= 0:
                continue
            option_positions.append({**pos, **parsed, "occ_symbol": sym})
    return option_positions


async def analyze_option_position(client: TradierAPIClient, pos: Dict, indicators: Dict, risk_free_rate: float = 0.043, portfolio_over_limit: bool = False, allowed_call_symbols: set = None, allowed_put_symbols: set = None, wt_history: Dict = None, config=None) -> Tuple[OptionPosition, List[ExitSignal]]:
    """Analyze a single option position for exit signals."""
    symbol = pos["symbol"]
    occ = pos["occ_symbol"]
    strike = pos["strike"]
    expiration = pos["expiration"]
    option_type = pos["option_type"]
    is_call = option_type == "call"
    quantity = pos.get("quantity", 0)
    cost_basis = pos.get("cost_basis", 0) or 0
    # Tradier cost_basis is TOTAL dollars. Option prices are per-share (1 contract = 100 shares).
    # cost_per_share = total_cost / (contracts * 100)
    cost_per_share = cost_basis / (abs(quantity) * 100) if quantity != 0 else 0
    # Fetch current option quote
    opt_quote_res = await client._request("GET", "/markets/quotes", params={"symbols": occ, "greeks": "true"}, use_data_context=True)
    opt_quote = {}
    if opt_quote_res and "quotes" in opt_quote_res and "quote" in opt_quote_res["quotes"]:
        q = opt_quote_res["quotes"]["quote"]
        opt_quote = q if isinstance(q, dict) else (q[0] if isinstance(q, list) and q else {})
    current_bid = opt_quote.get("bid", 0) or 0
    current_ask = opt_quote.get("ask", 0) or 0
    current_mid = (current_bid + current_ask) / 2.0 if (current_bid + current_ask) > 0 else (opt_quote.get("last", 0) or 0)
    current_price = current_mid
    # Underlying quote
    und_quote = await client.get_quote(symbol)
    underlying_price = und_quote.get("last", 0) or und_quote.get("bid", 0) or 0
    # Compute PnL — current_price and cost_per_share are both per-share option prices
    unrealized_pnl = (current_price - cost_per_share) * abs(quantity) * 100 if quantity > 0 else (cost_per_share - current_price) * abs(quantity) * 100
    unrealized_pct = ((current_price - cost_per_share) / cost_per_share * 100) if cost_per_share > 0 else 0
    # Compute greeks + IV
    exp_dt = datetime.strptime(expiration, "%Y-%m-%d")
    dte = max(1, (exp_dt - datetime.now()).days)
    T = dte / 365.0
    iv = implied_vol(current_price, underlying_price, strike, T, risk_free_rate, is_call) if underlying_price > 0 else None
    sigma = iv if iv else 0.3
    greeks = bs_greeks(underlying_price, strike, T, risk_free_rate, sigma, is_call)
    opt_pos = OptionPosition(symbol=symbol, option_symbol=occ, quantity=quantity, cost_basis=cost_basis, current_price=current_price, current_bid=current_bid, current_ask=current_ask, unrealized_pnl=unrealized_pnl, unrealized_pct=unrealized_pct, option_type=option_type, strike=strike, expiration=expiration, dte=dte, underlying_price=underlying_price, iv_current=iv, delta=greeks["delta"], theta=greeks["theta"], gamma=greeks["gamma"], vega=greeks["vega"])
    # ── SANITY CHECK — catch impossible PnL values ──
    if unrealized_pct < -95 or unrealized_pct > 10000:
        logger.warning(f"SANITY FAIL {occ}: pnl={unrealized_pct:.1f}% cost_basis={cost_basis} cost_per_share={cost_per_share} current={current_price} qty={quantity}. Skipping exit signals.")
        return opt_pos, []  # Return empty signals — never trade on broken data
    # ── Exit Signal Detection ──
    exit_signals = []
    ind = indicators.get(symbol, {})
    # 1. EXPIRATION PROXIMITY — only fire in FINAL WEEK (<=7 DTE)
    # Options are SWING positions (hold weeks/months). Wide spreads make day-trading suicidal.
    # Only exit on 1h/4h noise when expiry is truly imminent.
    if dte <= 7 and ind:
        wt_cross_1h_exp = ind.get("wt_cross_1h", "")
        wt_cross_4h_exp = ind.get("wt_cross_4h", "")
        wt1_1h_exp = ind.get("wt1_1h", 0) or 0
        wt1_4h_exp = ind.get("wt1_4h", 0) or 0
        at_peak = False
        peak_detail = []
        if is_call:
            if wt_cross_1h_exp == "BEAR" and wt1_1h_exp > 20:
                at_peak = True
                peak_detail.append(f"1h WT bear cross (wt1={wt1_1h_exp:.0f})")
            if wt_cross_4h_exp == "BEAR" and wt1_4h_exp > 0:
                at_peak = True
                peak_detail.append(f"4h WT bear cross (wt1={wt1_4h_exp:.0f})")
            if (ind.get("stoch_k_1h", 50) or 50) > 75 and (ind.get("stoch_k_1h", 50) or 50) < (ind.get("stoch_d_1h", 50) or 50):
                at_peak = True
                peak_detail.append(f"1h stoch overbought cross (k={ind.get('stoch_k_1h', 0):.0f})")
        else:
            if wt_cross_1h_exp == "BULL" and wt1_1h_exp < -20:
                at_peak = True
                peak_detail.append(f"1h WT bull cross (wt1={wt1_1h_exp:.0f})")
            if wt_cross_4h_exp == "BULL" and wt1_4h_exp < 0:
                at_peak = True
                peak_detail.append(f"4h WT bull cross (wt1={wt1_4h_exp:.0f})")
            if (ind.get("stoch_k_1h", 50) or 50) < 25 and (ind.get("stoch_k_1h", 50) or 50) > (ind.get("stoch_d_1h", 50) or 50):
                at_peak = True
                peak_detail.append(f"1h stoch oversold cross (k={ind.get('stoch_k_1h', 0):.0f})")
        if at_peak:
            urgency = "HIGH" if dte <= 3 else "MEDIUM"
            action = "SELL_NOW" if dte <= 3 else "TIGHTEN_STOP"
            exit_signals.append(ExitSignal(position=opt_pos, reason="EXPIRY_PEAK_EXIT", urgency=urgency, action=action, detail=f"{dte} DTE — local {'top' if is_call else 'bottom'} detected: {', '.join(peak_detail)}. Expiry imminent.", score=85 if dte <= 3 else 65))
        elif dte <= 5:
            exit_signals.append(ExitSignal(position=opt_pos, reason="THETA_URGENT", urgency="HIGH", action="SELL_NOW", detail=f"Only {dte} DTE. Theta decay ${abs(greeks['theta']*100*abs(quantity)):.2f}/day. Sell at next favorable tick.", score=88))
        elif dte <= 10:
            exit_signals.append(ExitSignal(position=opt_pos, reason="THETA_WARNING", urgency="MEDIUM", action="WATCH", detail=f"{dte} DTE — theta accelerating. Watch for D reversal to exit.", score=45))
    # 2. PROFIT TARGET — ride the D trend, only sell on WT reversal
    # Don't cap profits with arbitrary %  — let the D/4h WT tell us when the move is done
    wt_cross_D_profit = (ind.get("wt_cross_D", "") if ind else "")
    wt_cross_4h_profit = (ind.get("wt_cross_4h", "") if ind else "")
    wt1_D_profit = ind.get("wt1_D", 0) if ind else 0
    if unrealized_pct >= 50:
        # Check if D WT has rolled over against the position
        d_reversed = False
        if is_call and wt_cross_D_profit == "BEAR" and (wt1_D_profit or 0) > 30:
            d_reversed = True
        elif not is_call and wt_cross_D_profit == "BULL" and (wt1_D_profit or 0) < -30:
            d_reversed = True
        if d_reversed:
            exit_signals.append(ExitSignal(position=opt_pos, reason="PROFIT_WT_REVERSAL", urgency="HIGH", action="SELL_NOW", detail=f"Up {unrealized_pct:+.1f}% and D WT has reversed (cross={wt_cross_D_profit}, wt1_D={wt1_D_profit:.0f}). Lock in ${unrealized_pnl:.2f}.", score=90))
        elif unrealized_pct >= 100 and wt_cross_4h_profit in ("BEAR" if is_call else "BULL",):
            exit_signals.append(ExitSignal(position=opt_pos, reason="PROFIT_4H_REVERSAL", urgency="MEDIUM", action="TIGHTEN_STOP", detail=f"Up {unrealized_pct:+.1f}% and 4h WT reversing. Consider partial sell.", score=65))
        elif unrealized_pct >= 200:
            # 3x+ with no WT reversal — just flag it, don't force sell
            exit_signals.append(ExitSignal(position=opt_pos, reason="PROFIT_LARGE", urgency="LOW", action="WATCH", detail=f"Up {unrealized_pct:+.1f}% — D WT still aligned. Riding the trend.", score=30))
    # 3. DIRECTIONAL REVERSAL — REQUIRES D TIMEFRAME for options >14 DTE
    # Options are swing trades. 4h/1h noise is irrelevant for a 60+ DTE position.
    # Only D WT reversal matters. Under 14 DTE, 4h becomes relevant.
    if ind:
        wt_cross_D = ind.get("wt_cross_D", "")
        wt1_D = ind.get("wt1_D", 0) or 0
        wt1_4h = ind.get("wt1_4h")
        wt_cross_4h = ind.get("wt_cross_4h", "")
        wt_cross_1h = ind.get("wt_cross_1h", "")
        wt_velocity_4h = ind.get("wt_velocity_4h", 0) or 0
        wt_momentum_4h = ind.get("wt_momentum_state_4h", "")
        wt_momentum_D = ind.get("wt_momentum_state_D", "")
        stoch_k_4h = ind.get("stoch_k_4h", 50) or 50
        stoch_k_1h = ind.get("stoch_k_1h", 50) or 50
        stoch_d_4h = ind.get("stoch_d_4h", 50) or 50
        stoch_d_1h = ind.get("stoch_d_1h", 50) or 50
        dc_position_4h = ind.get("dc_position_4h", 0.5) or 0.5
        dc_position_1h = ind.get("dc_position_1h", 0.5) or 0.5
        wt_score_1h = ind.get("wt_score_1h", 0) or 0
        wt_score_4h = ind.get("wt_score_4h", 0) or 0
        # For >14 DTE: D reversal is MANDATORY. Without D cross, no exit signal at all.
        # For <=14 DTE: 4h+1h scoring as before but with higher threshold.
        d_reversal_required = dte > 14
        if is_call and quantity > 0:
            reversal_score = 0
            reversal_parts = []
            d_confirmed = False
            if wt_cross_D == "BEAR" and (wt1_D or 0) > 20:
                reversal_score += 40
                reversal_parts.append(f"WT_cross_D=BEAR (wt1_D={wt1_D:.0f})")
                d_confirmed = True
            if wt_momentum_D in ("IMPULSE_DOWN", "EXHAUST_DOWN"):
                reversal_score += 15
                reversal_parts.append(f"WT_mom_D={wt_momentum_D}")
                d_confirmed = True
            if not d_reversal_required or d_confirmed:
                if wt_cross_4h == "BEAR":
                    reversal_score += 20
                    reversal_parts.append("WT_cross_4h=BEAR")
                if wt_velocity_4h < -10:
                    reversal_score += 10
                    reversal_parts.append(f"WT_vel_4h={wt_velocity_4h:.0f}")
                if stoch_k_4h > 80 and stoch_k_4h < stoch_d_4h:
                    reversal_score += 15
                    reversal_parts.append(f"stoch_4h_overbought_cross={stoch_k_4h:.0f}")
                if dc_position_4h > 0.9:
                    reversal_score += 10
                    reversal_parts.append(f"DC_top_4h={dc_position_4h:.2f}")
            sell_threshold = 70 if dte > 14 else 50
            if reversal_score >= sell_threshold:
                exit_signals.append(ExitSignal(position=opt_pos, reason="BEARISH_REVERSAL", urgency="HIGH" if reversal_score >= 80 else "MEDIUM", action="SELL_NOW" if reversal_score >= 80 else "WATCH", detail=f"Bearish reversal signals ({reversal_score}): {', '.join(reversal_parts)}", score=reversal_score))
        elif not is_call and quantity > 0:
            reversal_score = 0
            reversal_parts = []
            d_confirmed = False
            if wt_cross_D == "BULL" and (wt1_D or 0) < -20:
                reversal_score += 40
                reversal_parts.append(f"WT_cross_D=BULL (wt1_D={wt1_D:.0f})")
                d_confirmed = True
            if wt_momentum_D in ("IMPULSE_UP", "EXHAUST_UP"):
                reversal_score += 15
                reversal_parts.append(f"WT_mom_D={wt_momentum_D}")
                d_confirmed = True
            if not d_reversal_required or d_confirmed:
                if wt_cross_4h == "BULL":
                    reversal_score += 20
                    reversal_parts.append("WT_cross_4h=BULL")
                if wt_velocity_4h > 10:
                    reversal_score += 10
                    reversal_parts.append(f"WT_vel_4h={wt_velocity_4h:.0f}")
                if stoch_k_4h < 20 and stoch_k_4h > stoch_d_4h:
                    reversal_score += 15
                    reversal_parts.append(f"stoch_4h_oversold_cross={stoch_k_4h:.0f}")
                if dc_position_4h < 0.1:
                    reversal_score += 10
                    reversal_parts.append(f"DC_bottom_4h={dc_position_4h:.2f}")
            sell_threshold = 70 if dte > 14 else 50
            if reversal_score >= sell_threshold:
                exit_signals.append(ExitSignal(position=opt_pos, reason="BULLISH_REVERSAL", urgency="HIGH" if reversal_score >= 80 else "MEDIUM", action="SELL_NOW" if reversal_score >= 80 else "WATCH", detail=f"Bullish reversal signals ({reversal_score}): {', '.join(reversal_parts)}", score=reversal_score))
    # 3b. WT_DELTA_ACCEL — WaveTrend velocity (NOT greeks delta) accelerating against position.
    # User rule: "sell when decline accelerates (delta going up)". In this system
    # "delta" = wt_velocity (bar-over-bar change of the WT oscillator). A CALL dies
    # when wt_velocity_D flips negative AND its magnitude grows on successive bars;
    # a PUT dies when wt_velocity_D flips positive AND grows.
    if ind and wt_history is not None and config is not None:
        wt_vel_D_now = ind.get("wt_velocity_D", 0) or 0
        wt_vel_4h_now = ind.get("wt_velocity_4h", 0) or 0
        prev_entry = wt_history.get(occ, {}) if isinstance(wt_history, dict) else {}
        wt_vel_D_prev = prev_entry.get("wt_velocity_D", wt_vel_D_now)
        min_abs = getattr(config, "OPTIONS_WT_ACCEL_MIN_ABS", 10.0)
        growth_pct = getattr(config, "OPTIONS_WT_ACCEL_GROWTH_PCT", 25.0)
        accel_detected = False
        accel_detail = ""
        # Direction: adverse = negative velocity for calls, positive for puts
        if is_call and wt_vel_D_now < 0 and wt_vel_4h_now < 0:
            if abs(wt_vel_D_now) >= min_abs and abs(wt_vel_D_now) > abs(wt_vel_D_prev) * (1 + growth_pct / 100.0):
                accel_detected = True
                accel_detail = f"CALL WT_vel_D={wt_vel_D_now:.1f} (prev {wt_vel_D_prev:.1f}), 4h={wt_vel_4h_now:.1f} — decline accelerating"
        elif (not is_call) and wt_vel_D_now > 0 and wt_vel_4h_now > 0:
            if abs(wt_vel_D_now) >= min_abs and abs(wt_vel_D_now) > abs(wt_vel_D_prev) * (1 + growth_pct / 100.0):
                accel_detected = True
                accel_detail = f"PUT WT_vel_D={wt_vel_D_now:.1f} (prev {wt_vel_D_prev:.1f}), 4h={wt_vel_4h_now:.1f} — rally accelerating"
        if accel_detected:
            exit_signals.append(ExitSignal(position=opt_pos, reason="WT_DELTA_ACCEL", urgency="HIGH", action="SELL_NOW", detail=accel_detail, score=85))
    # 3c. LEVEL_BREAK — support (calls) or resistance (puts) broken beyond buffer.
    # User rule: "sell when key levels are broken (fall through red zones)".
    # Only fire on dte > OPTIONS_LEVEL_BREAK_MIN_DTE — sub-14-DTE is noise-dominated
    # and already covered by THETA_URGENT / DEEP_OTM.
    if ind and config is not None:
        buf = getattr(config, "OPTIONS_LEVEL_BREAK_BUFFER", 0.01)
        min_lb_dte = getattr(config, "OPTIONS_LEVEL_BREAK_MIN_DTE", 14)
        if dte > min_lb_dte and underlying_price > 0:
            dc_low_D_lb = ind.get("dc_low_D")
            dc_high_D_lb = ind.get("dc_high_D")
            wt_cross_D_lb = ind.get("wt_cross_D", "")
            if is_call and dc_low_D_lb and underlying_price < dc_low_D_lb * (1.0 - buf):
                confirm = "confirmed" if wt_cross_D_lb == "BEAR" else "unconfirmed"
                exit_signals.append(ExitSignal(position=opt_pos, reason="SUPPORT_BREAK", urgency="HIGH" if confirm == "confirmed" else "MEDIUM", action="SELL_NOW" if confirm == "confirmed" else "TIGHTEN_STOP", detail=f"CALL: underlying ${underlying_price:.2f} broke dc_low_D ${dc_low_D_lb:.2f} (buf {buf*100:.1f}%) — D {confirm}", score=82 if confirm == "confirmed" else 60))
            elif (not is_call) and dc_high_D_lb and underlying_price > dc_high_D_lb * (1.0 + buf):
                confirm = "confirmed" if wt_cross_D_lb == "BULL" else "unconfirmed"
                exit_signals.append(ExitSignal(position=opt_pos, reason="RESISTANCE_BREAK", urgency="HIGH" if confirm == "confirmed" else "MEDIUM", action="SELL_NOW" if confirm == "confirmed" else "TIGHTEN_STOP", detail=f"PUT: underlying ${underlying_price:.2f} broke dc_high_D ${dc_high_D_lb:.2f} (buf {buf*100:.1f}%) — D {confirm}", score=82 if confirm == "confirmed" else 60))
    # 4. IV CRUSH — IV dropping = option losing extrinsic value
    if iv and iv < 0.15 and dte > 14:
        exit_signals.append(ExitSignal(position=opt_pos, reason="IV_CRUSH", urgency="MEDIUM", action="TIGHTEN_STOP", detail=f"IV at {iv*100:.1f}% — very low, extrinsic value minimal. Consider selling if near breakeven.", score=45))
    # 5. DEEP OTM — option moving far from the money
    moneyness = strike / underlying_price if underlying_price > 0 else 1.0
    if is_call and moneyness > 1.15 and dte < 10:
        exit_signals.append(ExitSignal(position=opt_pos, reason="DEEP_OTM_CALL", urgency="HIGH", action="SELL_NOW", detail=f"Call {(moneyness-1)*100:.1f}% OTM with only {dte} DTE. Low probability of profit.", score=75))
    elif not is_call and moneyness < 0.85 and dte < 10:
        exit_signals.append(ExitSignal(position=opt_pos, reason="DEEP_OTM_PUT", urgency="HIGH", action="SELL_NOW", detail=f"Put {(1-moneyness)*100:.1f}% OTM with only {dte} DTE. Low probability of profit.", score=75))
    # 6. MAX LOSS GUARD — DTE-aware. With 60+ DTE there's plenty of time to recover,
    # but bleeding to -80% is not "recovery" — it's capitulation-minus-slippage.
    # Thresholds pulled from config_tradier.OPTIONS_MAX_LOSS_PCT_DTE_* so they are tunable.
    _cfg_loss = config
    if _cfg_loss is None:
        try:
            from config_tradier import TradierConfig as _TC
            _cfg_loss = _TC()
        except Exception:
            _cfg_loss = None
    _t30 = getattr(_cfg_loss, "OPTIONS_MAX_LOSS_PCT_DTE_30", -40.0) if _cfg_loss else -40.0
    _t14 = getattr(_cfg_loss, "OPTIONS_MAX_LOSS_PCT_DTE_14", -30.0) if _cfg_loss else -30.0
    _tlo = getattr(_cfg_loss, "OPTIONS_MAX_LOSS_PCT_DTE_LOW", -20.0) if _cfg_loss else -20.0
    loss_threshold = _t30 if dte > 30 else (_t14 if dte > 14 else _tlo)
    if unrealized_pct <= loss_threshold:
        exit_signals.append(ExitSignal(position=opt_pos, reason="MAX_LOSS_GUARD", urgency="HIGH", action="SELL_NOW", detail=f"Position down {unrealized_pct:.1f}% (threshold {loss_threshold}% for {dte} DTE). Salvage remaining ${current_price*abs(quantity)*100:.2f} premium.", score=80))
    elif unrealized_pct <= loss_threshold + 15:
        exit_signals.append(ExitSignal(position=opt_pos, reason="LOSS_WARNING", urgency="LOW", action="WATCH", detail=f"Position down {unrealized_pct:.1f}%. {dte} DTE remaining — monitoring.", score=35))
    # 7. PORTFOLIO REDUCTION — close rogue positions when over ceiling
    # OIL SPREAD (USO/BNO) positions are EXCLUDED — they have their own budget/strategy
    _oil_spread_symbols = {"USO", "BNO"}
    if portfolio_over_limit and symbol not in _oil_spread_symbols:
        is_rogue = False
        if is_call and allowed_call_symbols is not None and symbol not in allowed_call_symbols:
            is_rogue = True
        elif not is_call and allowed_put_symbols is not None and symbol not in allowed_put_symbols:
            is_rogue = True
        if is_rogue:
            # Rogue + over limit: NEVER auto-sell. Place GTC sells above market instead.
            # Priority: near-breakeven first (cheap exit), deep losers wait for bounce.
            # Winners: place GTC sell ABOVE current ask to catch the top — don't dump.
            rogue_score = 50
            rogue_action = "WATCH"
            detail = f"ROGUE symbol (not in symbols_trb list). Place GTC sell above market."
            if -10 < unrealized_pct <= 0:
                rogue_score = 70
                rogue_action = "TIGHTEN_STOP"
                detail = f"ROGUE near breakeven ({unrealized_pct:+.1f}%). GTC sell 5% above bid."
            elif unrealized_pct > 0:
                rogue_score = 60
                rogue_action = "WATCH"
                detail = f"ROGUE but profitable ({unrealized_pct:+.1f}%). GTC sell above ask — ride and exit on WT turn."
            elif unrealized_pct > -25:
                rogue_score = 55
                rogue_action = "WATCH"
                detail = f"ROGUE moderate loss ({unrealized_pct:+.1f}%). GTC sell 10% above bid — wait for bounce."
            else:
                rogue_score = 40
                rogue_action = "WATCH"
                detail = f"ROGUE deep loss ({unrealized_pct:+.1f}%). GTC sell 15% above bid — patience."
            # WT timing bonus — only bump to SELL_NOW if D confirms
            if ind:
                d_cross = ind.get("wt_cross_D", "")
                d_mom = ind.get("wt_momentum_state_D", "")
                if is_call and d_cross == "BEAR" and d_mom in ("IMPULSE_DOWN", "EXHAUST_DOWN"):
                    rogue_score += 25
                    rogue_action = "SELL_NOW"
                    detail += " D reversal confirmed — exit now."
                elif not is_call and d_cross == "BULL" and d_mom in ("IMPULSE_UP", "EXHAUST_UP"):
                    rogue_score += 25
                    rogue_action = "SELL_NOW"
                    detail += " D reversal confirmed — exit now."
            exit_signals.append(ExitSignal(position=opt_pos, reason="PORTFOLIO_REDUCE_ROGUE", urgency="HIGH" if rogue_score >= 80 else "MEDIUM" if rogue_score >= 60 else "LOW", action=rogue_action, detail=detail, score=rogue_score))
        elif not is_rogue and len(exit_signals) == 0:
            # Valid symbol but portfolio still over limit — flag for awareness
            exit_signals.append(ExitSignal(position=opt_pos, reason="PORTFOLIO_OVER_CEILING", urgency="LOW", action="WATCH", detail=f"Portfolio over $7k ceiling. Position is valid symbol but consider reducing if up {unrealized_pct:+.1f}%.", score=30))
    exit_signals.sort(key=lambda x: x.score, reverse=True)
    return opt_pos, exit_signals


def format_option_position(pos: OptionPosition) -> str:
    pnl_color = "\033[92m" if pos.unrealized_pnl >= 0 else "\033[91m"
    reset = "\033[0m"
    iv_str = f"IV={pos.iv_current*100:.1f}%" if pos.iv_current else "IV=N/A"
    return f"  {pos.symbol:6s} {pos.option_type.upper():4s} ${pos.strike:>8.2f} exp {pos.expiration} ({pos.dte}d)  x{abs(pos.quantity)}  Bid/Ask: ${pos.current_bid:.2f}/${pos.current_ask:.2f}  {pnl_color}PnL: ${pos.unrealized_pnl:+.2f} ({pos.unrealized_pct:+.1f}%){reset}  Underlying: ${pos.underlying_price:.2f}  {iv_str}  Delta: {pos.delta:+.3f}  Theta: {pos.theta:+.4f}"


def format_exit_signal(sig: ExitSignal) -> str:
    colors = {"HIGH": "\033[91m", "MEDIUM": "\033[93m", "LOW": "\033[90m"}
    color = colors.get(sig.urgency, "\033[0m")
    reset = "\033[0m"
    return f"    {color}[{sig.urgency:6s}] {sig.reason:25s}{reset} → {sig.action:15s}  {sig.detail}  (score: {sig.score})"


def _load_gtc_orders(config) -> Dict:
    """Load active GTC sell orders from tracking file."""
    gtc_file = config.DATA_DIR / "options_gtc_orders.json"
    if gtc_file.exists():
        with open(gtc_file) as f:
            return json.load(f)
    return {}


def _save_gtc_orders(config, gtc_orders: Dict):
    """Save active GTC sell orders to tracking file."""
    gtc_file = config.DATA_DIR / "options_gtc_orders.json"
    with open(gtc_file, "w") as f:
        json.dump(gtc_orders, f, indent=2, default=str)


def _wt_history_path(config) -> Path:
    return config.DATA_DIR / "options_wt_history.json"


def _load_wt_history(config) -> Dict:
    """Per-OCC snapshot of last seen WT velocities so we can detect bar-over-bar acceleration."""
    p = _wt_history_path(config)
    if p.exists():
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_wt_history(config, hist: Dict):
    p = _wt_history_path(config)
    with open(p, "w") as f:
        json.dump(hist, f, indent=2, default=str)


def _compute_gtc_target(opt_pos: OptionPosition, ind: Dict) -> Optional[float]:
    """Compute a GTC sell target price for an option whose D trend is exhausting.
    Uses delta to estimate option price at underlying's DC high (calls) or DC low (puts).
    Returns target option price, or None if not applicable.
    """
    is_call = opt_pos.option_type.lower() == "call"
    underlying = opt_pos.underlying_price
    if underlying <= 0:
        return None
    delta = abs(opt_pos.delta) if opt_pos.delta else 0.5
    if delta <= 0.01:
        return None
    # For calls: target = DC high on D (where the rally tops out)
    # For puts: target = DC low on D (where the selloff bottoms out)
    symbol = opt_pos.symbol
    if is_call:
        dc_high = ind.get("dc_high_D") or ind.get("dc_high_4h")
        if not dc_high or dc_high <= underlying:
            # Fallback: use ATR to project upside
            atr_d = ind.get("atr_D", 0) or 0
            dc_high = underlying + max(atr_d * 0.7, underlying * 0.03)
        underlying_move = dc_high - underlying
    else:
        dc_low = ind.get("dc_low_D") or ind.get("dc_low_4h")
        if not dc_low or dc_low >= underlying:
            atr_d = ind.get("atr_D", 0) or 0
            dc_low = underlying - max(atr_d * 0.7, underlying * 0.03)
        underlying_move = underlying - dc_low
    # Option price move ≈ delta × underlying move (first-order approx)
    option_price_bump = delta * underlying_move
    # Target = current ask + estimated bump (sell ABOVE current market)
    current_mid = (opt_pos.current_bid + opt_pos.current_ask) / 2.0 if (opt_pos.current_bid + opt_pos.current_ask) > 0 else opt_pos.current_price
    target = current_mid + option_price_bump
    # Floor: at least 20% above current mid (worth the wait)
    min_target = current_mid * 1.20
    target = max(target, min_target)
    # Round to nearest 0.05 (options tick)
    target = round(target * 20) / 20
    return target


async def _manage_gtc_orders(client: TradierAPIClient, config, positions: list, indicators: Dict):
    """Place/check/cancel GTC sell orders for positions showing D exhaustion.
    - EXHAUST_UP on D for calls → place GTC sell above market
    - EXHAUST_DOWN on D for puts → place GTC sell above market
    - If D reverses to IMPULSE against position → cancel GTC, let normal exit handle it
    - If GTC fills → log and celebrate
    """
    gtc_orders = _load_gtc_orders(config)
    account_id = client._current_id
    changed = False
    for pos_data in positions:
        parsed = parse_occ_symbol(pos_data.get("occ_symbol", "") or pos_data.get("symbol", ""))
        if not parsed:
            continue
        symbol = parsed["symbol"]
        occ = pos_data.get("occ_symbol", "") or pos_data.get("symbol", "")
        is_call = parsed["option_type"] == "call"
        quantity = pos_data.get("quantity", 0)
        if quantity <= 0:
            continue
        ind = indicators.get(symbol, {})
        momentum_D = ind.get("wt_momentum_state_D", "")
        # Check if we already have a GTC order for this OCC
        if occ in gtc_orders:
            order_id = gtc_orders[occ].get("order_id")
            # CANCEL COOLDOWN: user canceled a GTC → no_re-place for 4h to stop the loop
            if not order_id:
                _ccat = gtc_orders[occ].get("_user_canceled_at")
                if _ccat:
                    try:
                        _cc_dt = datetime.fromisoformat(_ccat)
                        _cc_hrs = (datetime.utcnow() - _cc_dt).total_seconds() / 3600.0
                    except Exception:
                        _cc_hrs = 99.0
                    if _cc_hrs < 4.0:
                        logger.debug(f"GTC cancel cooldown active for {occ} ({_cc_hrs:.1f}h < 4h)")
                        continue
                    del gtc_orders[occ]
                    changed = True
                else:
                    continue
            if order_id and account_id:
                order_status = await client._request("GET", f"/accounts/{account_id}/orders/{order_id}", use_data_context=False)
                if order_status and "order" in order_status:
                    st = order_status["order"].get("status", "")
                    if st == "filled":
                        fill_price = order_status["order"].get("avg_fill_price", "?")
                        print(f"    \033[92m★ GTC FILLED\033[0m  {occ} @ ${fill_price}  (target was ${gtc_orders[occ].get('target_price', '?')})")
                        logger.info(f"GTC FILLED {occ} @ ${fill_price} — target ${gtc_orders[occ].get('target_price')}")
                        del gtc_orders[occ]
                        changed = True
                        continue
                    elif st in ("canceled", "rejected", "expired"):
                        logger.info(f"GTC order {order_id} for {occ} is {st} — adding 4h cancel cooldown")
                        gtc_orders[occ] = {"_user_canceled_at": datetime.utcnow().isoformat(), "order_id": None}
                        changed = True
                    else:
                        # Still open — check if thesis reversed (D now impulse against us)
                        should_cancel = False
                        if is_call and momentum_D == "IMPULSE_DOWN":
                            should_cancel = True
                        elif not is_call and momentum_D == "IMPULSE_UP":
                            should_cancel = True
                        if should_cancel:
                            print(f"    \033[93mCANCEL GTC\033[0m  {occ} — D momentum reversed to {momentum_D}")
                            logger.info(f"Cancelling GTC {order_id} for {occ} — D momentum {momentum_D}")
                            await client._request("DELETE", f"/accounts/{account_id}/orders/{order_id}", use_data_context=False)
                            del gtc_orders[occ]
                            changed = True
                        else:
                            target = gtc_orders[occ].get("target_price", "?")
                            print(f"    \033[96m⏳ GTC OPEN\033[0m  {occ} — sell @ ${target}  (order {order_id}, status={st})")
                        continue
            continue
        # No existing GTC — check if we should place one
        # Condition: D showing exhaustion in our direction
        should_place = False
        if is_call and momentum_D == "EXHAUST_UP":
            should_place = True
        elif not is_call and momentum_D == "EXHAUST_DOWN":
            should_place = True
        if not should_place:
            continue
        # Build a temporary OptionPosition to compute target
        opt_quote_res = await client._request("GET", "/markets/quotes", params={"symbols": occ, "greeks": "true"}, use_data_context=True)
        opt_quote = {}
        if opt_quote_res and "quotes" in opt_quote_res and "quote" in opt_quote_res["quotes"]:
            q = opt_quote_res["quotes"]["quote"]
            opt_quote = q if isinstance(q, dict) else (q[0] if isinstance(q, list) and q else {})
        current_bid = opt_quote.get("bid", 0) or 0
        current_ask = opt_quote.get("ask", 0) or 0
        if current_bid <= 0 and current_ask <= 0:
            continue
        und_quote = await client.get_quote(symbol)
        underlying_price = und_quote.get("last", 0) or und_quote.get("bid", 0) or 0
        if underlying_price <= 0:
            continue
        cost_basis = pos_data.get("cost_basis", 0) or 0
        cost_per_share = cost_basis / (abs(quantity) * 100) if quantity != 0 else 0
        current_mid = (current_bid + current_ask) / 2.0
        exp_dt = datetime.strptime(parsed["expiration"], "%Y-%m-%d")
        dte = max(1, (exp_dt - datetime.now()).days)
        T = dte / 365.0
        sigma = 0.3
        greeks = bs_greeks(underlying_price, parsed["strike"], T, 0.043, sigma, is_call)
        tmp_pos = OptionPosition(symbol=symbol, option_symbol=occ, quantity=quantity, cost_basis=cost_basis, current_price=current_mid, current_bid=current_bid, current_ask=current_ask, unrealized_pnl=(current_mid - cost_per_share) * abs(quantity) * 100, unrealized_pct=((current_mid - cost_per_share) / cost_per_share * 100) if cost_per_share > 0 else 0, option_type=parsed["option_type"], strike=parsed["strike"], expiration=parsed["expiration"], dte=dte, underlying_price=underlying_price, iv_current=sigma, delta=greeks["delta"], theta=greeks["theta"], gamma=greeks["gamma"], vega=greeks["vega"])
        target = _compute_gtc_target(tmp_pos, ind)
        if not target or target <= current_ask:
            continue
        # Place GTC sell_to_close
        print(f"    \033[96m★ PLACING GTC SELL\033[0m  {occ} x{quantity} @ ${target:.2f}  (current bid=${current_bid:.2f} ask=${current_ask:.2f} mid=${current_mid:.2f})")
        print(f"      D momentum={momentum_D}, delta={greeks['delta']:+.3f}, target is {((target - current_mid) / current_mid * 100):.1f}% above mid")
        logger.info(f"Placing GTC sell {occ} x{quantity} @ ${target:.2f} — D {momentum_D}, delta={greeks['delta']:.3f}, mid=${current_mid:.2f}")
        res = await place_option_order(client, symbol, occ, "sell_to_close", quantity, "limit", target, duration="gtc")
        if "order" in res:
            order_id = res["order"].get("id")
            print(f"      \033[92mGTC ORDER PLACED\033[0m  Order ID: {order_id}")
            logger.info(f"GTC order placed {occ} ID={order_id} @ ${target:.2f}")
            gtc_orders[occ] = {"order_id": order_id, "target_price": target, "placed_at": datetime.now().isoformat(), "symbol": symbol, "momentum_D": momentum_D, "delta": greeks["delta"], "current_mid": current_mid, "underlying": underlying_price}
            changed = True
        elif "errors" in res:
            print(f"      \033[91mFAILED\033[0m  {res['errors']}")
            logger.error(f"GTC order failed {occ}: {res['errors']}")
        await asyncio.sleep(0.3)
    if changed:
        _save_gtc_orders(config, gtc_orders)
    return gtc_orders


async def run_watch(args):
    """Monitor open option positions for exit signals."""
    config = TradierConfig()
    account_key = args.account or "trb"
    auto_sell = getattr(args, "auto_sell", False)
    daemon = getattr(args, "daemon", False)
    interval = getattr(args, "interval", 60)
    # Load indicators
    ind_file = config.DATA_DIR / "tradier_indicators_latest.json"
    indicators = {}
    if ind_file.exists():
        with open(ind_file) as f:
            indicators = json.load(f)
    client = TradierAPIClient(config, account_key=account_key)
    await client.connect()
    try:
        while True:
            # Reload indicators each cycle
            if ind_file.exists():
                with open(ind_file) as f:
                    indicators = json.load(f)
            positions = await get_option_positions(client)
            if not positions:
                print(f"\n  [{datetime.now().strftime('%H:%M:%S')}] No option positions found in account {account_key}")
                if not daemon:
                    break
                await asyncio.sleep(interval)
                continue
            # ── Check portfolio limits for reduction mode ──
            # Exclude spread strategy symbols from the ceiling — separate budgets
            _spread_excluded_syms = {"USO", "BNO", "MSTR", "IBIT", "COIN", "NEM", "GLD", "GDX", "AEM", "RGLD", "WPM"}
            total_cost = sum(abs(float(p.get("cost_basis", 0))) for p in positions if parse_occ_symbol(p.get("occ_symbol", "") or p.get("symbol", "")) is None or parse_occ_symbol(p.get("occ_symbol", "") or p.get("symbol", "")).get("symbol", "") not in _spread_excluded_syms)
            n_non_spread = sum(1 for p in positions if parse_occ_symbol(p.get("occ_symbol", "") or p.get("symbol", "")) is None or parse_occ_symbol(p.get("occ_symbol", "") or p.get("symbol", "")).get("symbol", "") not in _spread_excluded_syms)
            portfolio_over = total_cost > 7000 or n_non_spread > 15
            # Load allowed symbols
            long_file = Path(str(config.DATA_DIR).replace("/data/tradier", "")) / "symbols_trb_long.json"
            short_file = Path(str(config.DATA_DIR).replace("/data/tradier", "")) / "symbols_trb_short.json"
            allowed_calls = set()
            allowed_puts = set()
            if long_file.exists():
                with open(long_file) as f:
                    allowed_calls = {s for s in json.load(f) if isinstance(s, str) and len(s) <= 5}
            if short_file.exists():
                with open(short_file) as f:
                    allowed_puts = {s for s in json.load(f) if isinstance(s, str) and len(s) <= 5}
            print(f"\n{'='*90}")
            print(f"  OPTIONS WATCHDOG — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC — Account: {account_key}")
            print(f"  {len(positions)} position(s) | ${total_cost:,.0f}/$7,000 ceiling | {'OVER LIMIT — reducing rogues' if portfolio_over else 'within limits'}")
            print(f"{'='*90}")
            all_sell_now = []
            # SAFETY: No auto-sells in last 30min of market (19:30+ UTC) — illiquid, wide spreads
            now_utc = datetime.utcnow()
            market_closing_soon = now_utc.hour == 19 and now_utc.minute >= 30
            if market_closing_soon and auto_sell:
                print(f"  \033[93mAUTO-SELL DISABLED — market closing in <30min. Wide spreads = bad fills.\033[0m")
                auto_sell = False
            # ── EMERGENCY: Market benchmark divergence detection ──
            # Fetch SPY (stocks), QQQ (tech), IBIT (crypto) as benchmarks
            _bench_quotes = await client.get_quotes(["SPY", "QQQ", "IBIT"])
            _bench_moves = {}
            for _bsym in ["SPY", "QQQ", "IBIT"]:
                _bq = _bench_quotes.get(_bsym, {})
                _blast = _bq.get("last", 0) or 0
                _bprev = _bq.get("prevclose", 0) or _bq.get("previous_close", 0) or 0
                if _blast > 0 and _bprev > 0:
                    _bench_moves[_bsym] = (_blast - _bprev) / _bprev * 100
            _spy_move = _bench_moves.get("SPY", 0)
            _qqq_move = _bench_moves.get("QQQ", 0)
            _ibit_move = _bench_moves.get("IBIT", 0)
            if _bench_moves:
                print(f"  Market: SPY {_spy_move:+.2f}% | QQQ {_qqq_move:+.2f}% | IBIT {_ibit_move:+.2f}%")
            emergency_symbols = {}  # symbol → divergence info
            # ── GTC "sell at the top" management ──
            gtc_orders = await _manage_gtc_orders(client, config, positions, indicators)
            if gtc_orders:
                print(f"\n  \033[96m{len(gtc_orders)} GTC sell order(s) active — waiting for fills\033[0m")
            # Load prior WT velocity snapshots so WT_DELTA_ACCEL can compare bar-over-bar
            wt_history = _load_wt_history(config)
            wt_history_updated = {}
            for pos_data in positions:
                opt_pos, exit_signals = await analyze_option_position(client, pos_data, indicators, portfolio_over_limit=portfolio_over, allowed_call_symbols=allowed_calls, allowed_put_symbols=allowed_puts, wt_history=wt_history, config=config)
                # record this cycle's velocity for next comparison (per-OCC)
                _occ_key = pos_data.get("occ_symbol", "") or pos_data.get("symbol", "")
                _ind_sym = indicators.get(opt_pos.symbol, {}) if indicators else {}
                if _occ_key:
                    wt_history_updated[_occ_key] = {"wt_velocity_D": _ind_sym.get("wt_velocity_D", 0) or 0, "wt_velocity_4h": _ind_sym.get("wt_velocity_4h", 0) or 0, "ts": datetime.utcnow().isoformat()}
                # ── EMERGENCY CHECK: underlying moving 3x+ more than market ──
                _und_sym = opt_pos.symbol
                if _und_sym not in emergency_symbols and _bench_moves:
                    _und_q = await client.get_quote(_und_sym)
                    _und_last = _und_q.get("last", 0) or 0
                    _und_prev = _und_q.get("prevclose", 0) or _und_q.get("previous_close", 0) or 0
                    if _und_last > 0 and _und_prev > 0:
                        _und_move = (_und_last - _und_prev) / _und_prev * 100
                        # Pick appropriate benchmark
                        _crypto_syms = {"IBIT", "MSTR", "COIN", "MARA", "CLSK", "BITO", "RIOT"}
                        _bench_ref = _ibit_move if _und_sym in _crypto_syms else _spy_move
                        _divergence = _und_move - _bench_ref
                        _is_emergency = abs(_divergence) > 5.0 or (abs(_bench_ref) > 0.3 and abs(_und_move) > abs(_bench_ref) * 3)
                        if _is_emergency:
                            emergency_symbols[_und_sym] = {"move": _und_move, "bench": _bench_ref, "divergence": _divergence}
                            # Add emergency exit signal
                            _emg_detail = f"EMERGENCY: {_und_sym} {_und_move:+.1f}% vs market {_bench_ref:+.1f}% (divergence {_divergence:+.1f}%)"
                            _emg_score = 45  # 2026-04-08: was 85 SELL_NOW. Single-day divergence is NOT a swing exit. Downgraded to WATCH.
                            _emg_action = "WATCH"
                            # Only sell if the move is AGAINST our position
                            _is_call = opt_pos.option_type.lower() == "call"
                            _move_against = (_is_call and _und_move < _bench_ref - 3) or (not _is_call and _und_move > _bench_ref + 3)
                            if _move_against:
                                exit_signals.append(ExitSignal(position=opt_pos, reason="EMERGENCY_DIVERGENCE", urgency="HIGH", action=_emg_action, detail=_emg_detail, score=_emg_score))
                                print(f"  \033[91m!!! EMERGENCY {_und_sym}: {_und_move:+.1f}% vs market {_bench_ref:+.1f}% — AGAINST our {'CALL' if _is_call else 'PUT'}\033[0m")
                            elif abs(_divergence) > 8:
                                # Extreme move in our favor — might want to take profit
                                exit_signals.append(ExitSignal(position=opt_pos, reason="EMERGENCY_WINDFALL", urgency="MEDIUM", action="TIGHTEN_STOP", detail=f"WINDFALL: {_und_sym} {_und_move:+.1f}% (divergence {_divergence:+.1f}%). Consider locking gains.", score=60))
                                print(f"  \033[92m★ WINDFALL {_und_sym}: {_und_move:+.1f}% vs market {_bench_ref:+.1f}% — IN our favor\033[0m")
                    await asyncio.sleep(0.1)
                print(f"\n{format_option_position(opt_pos)}")
                if exit_signals:
                    for sig in exit_signals:
                        print(format_exit_signal(sig))
                    sell_signals = [s for s in exit_signals if s.action == "SELL_NOW" and s.score >= 80]
                    if sell_signals:
                        all_sell_now.append((opt_pos, sell_signals[0]))
                else:
                    print(f"    \033[92m[OK] No exit signals — position looks healthy\033[0m")
                await asyncio.sleep(0.2)  # rate limit
            # Auto-sell if enabled — uses smart fill (start at ask, walk to mid)
            if auto_sell and all_sell_now:
                print(f"\n  {'─'*86}")
                print(f"  AUTO-SELL EXECUTING ({len(all_sell_now)} positions):")
                sold_occs = set()  # Track what we've sold this cycle
                for opt_pos, sig in all_sell_now:
                    occ = opt_pos.option_symbol
                    if occ in sold_occs:
                        print(f"    SKIP {occ} — already sold this cycle")
                        continue
                    # Re-check position still exists before selling
                    current_positions = await get_option_positions(client)
                    still_held = any(p.get("occ_symbol", p.get("symbol", "")) == occ for p in current_positions)
                    if not still_held:
                        print(f"    SKIP {occ} — position already closed")
                        sold_occs.add(occ)
                        continue
                    qty = abs(opt_pos.quantity)
                    bid = opt_pos.current_bid
                    ask = opt_pos.current_ask
                    start_price = round(ask * 1.05, 2)  # Start 5% ABOVE ask — never sell cheap
                    print(f"    Smart-selling {occ} x{qty} — Reason: {sig.reason}  (bid=${bid:.2f} ask=${ask:.2f} start=${start_price:.2f})")
                    logger.info(f"Auto-sell triggered {occ} x{qty} — {sig.reason} (bid={bid}, ask={ask})")
                    steps = 3 if sig.score >= 80 else 5
                    walk_sec = 60 if sig.score >= 80 else 120
                    res = await smart_fill_option(client, opt_pos.symbol, occ, "sell_to_close", qty, start_price, bid, ask, max_walk_steps=steps, walk_interval=walk_sec)
                    if "order" in res:
                        order_info = res["order"]
                        fill_price = order_info.get("avg_fill_price", "pending")
                        print(f"    \033[92mSOLD\033[0m  Order ID: {order_info.get('id')}  Status: {order_info.get('status')}  Fill: ${fill_price}")
                        logger.info(f"Auto-sold {occ} x{qty} fill=${fill_price} — {sig.reason}")
                        sold_occs.add(occ)
                    elif res.get("status") == "pending":
                        print(f"    \033[93mPENDING\033[0m — order {res.get('order_id')} still working @ ${res.get('final_price'):.2f}")
                        sold_occs.add(occ)
                    elif "errors" in res:
                        err_str = str(res["errors"])
                        if "no position" in err_str.lower() or "quantity" in err_str.lower() or "rejected" in err_str.lower():
                            print(f"    \033[93mPHANTOM POSITION\033[0m {occ} — exchange says no position exists. Skipping permanently this cycle.")
                            logger.warning(f"Phantom position detected: {occ} — API shows position but exchange rejects sell. Possible stale API data.")
                            sold_occs.add(occ)
                        else:
                            print(f"    \033[91mFAILED\033[0m  {res['errors']}")
                            logger.error(f"Auto-sell failed {occ}: {res['errors']}")
                    else:
                        print(f"    Response: {json.dumps(res, indent=2)}")
                    await asyncio.sleep(0.3)
            elif not auto_sell and all_sell_now:
                print(f"\n  \033[93m{len(all_sell_now)} position(s) have SELL_NOW signals. Run with --auto-sell to execute.\033[0m")
            # Save watchdog state
            watch_file = config.DATA_DIR / "options_watchdog_latest.json"
            watch_data = {"timestamp": datetime.now().isoformat(), "account": account_key, "positions": len(positions), "sell_signals": len(all_sell_now), "auto_sell": auto_sell, "gtc_orders": len(gtc_orders)}
            with open(watch_file, "w") as f:
                json.dump(watch_data, f, indent=2)
            # Persist WT velocity snapshots for next bar's acceleration comparison
            if wt_history_updated:
                _save_wt_history(config, wt_history_updated)
            if not daemon:
                break
            print(f"\n  Next check in {interval}s...")
            await asyncio.sleep(interval)
    finally:
        await client.close()


# ── Display ──────────────────────────────────────────────────────────────────

def format_signal(sig: DirectionalSignal) -> str:
    arrow = "\033[92m▲ LONG\033[0m" if sig.direction == "LONG" else "\033[91m▼ SHORT\033[0m"
    lines = [f"\n{'='*80}", f"  {sig.symbol:8s} {arrow}   Price: ${sig.price:.2f}   Conviction: {sig.conviction:.0f}/100   ATR(D): ${sig.atr_D:.2f} ({sig.atr_D/sig.price*100:.1f}%)   Rank: {sig.ranking_score:.0f}"]
    sig_parts = []
    for k, v in sig.signals.items():
        sig_parts.append(f"{k}={v}")
    lines.append(f"  Signals: {', '.join(sig_parts)}")
    return "\n".join(lines)


def format_outlier(o: OptionOutlier) -> str:
    color = "\033[92m" if "BUY" in o.recommendation else "\033[91m"
    reset = "\033[0m"
    iv_str = f"IV={o.iv_computed*100:.1f}%" if o.iv_computed else "IV=N/A"
    return f"    {color}{o.recommendation:25s}{reset} {o.option_type.upper():4s} ${o.strike:>8.2f}  {o.expiration}  ({o.dte}d)  Bid/Ask: ${o.bid:.2f}/${o.ask:.2f}  Mid: ${o.mid:.2f}  {iv_str}  Surface: {o.iv_surface_mean*100:.1f}%  Dev: {o.iv_deviation_pct:+.1f}%  Edge: {o.edge_pct:+.1f}%  Vol: {o.volume}  OI: {o.open_interest}  Delta: {o.greeks['delta']:+.3f}  Score: {o.score}"


def format_spread(sp: SpreadOpportunity) -> str:
    is_credit = sp.net_debit < 0
    color = "\033[93m"
    reset = "\033[0m"
    credit_str = f"Credit: ${abs(sp.net_debit):.2f}" if is_credit else f"Debit: ${sp.net_debit:.2f}"
    return f"    {color}{sp.spread_type:22s}{reset} {sp.expiration} ({sp.dte}d)  ${sp.long_strike:.2f}/${sp.short_strike:.2f}  {credit_str}  MaxProfit: ${sp.max_profit:.2f}  MaxLoss: ${sp.max_loss:.2f}  R:R {sp.risk_reward:.2f}  BE: ${sp.breakeven:.2f}  P(profit): {sp.probability_profit:.0%}  Score: {sp.score}"


# ── Main ─────────────────────────────────────────────────────────────────────

async def run_analysis(args):
    config = TradierConfig()
    # Load indicator data
    ind_file = config.DATA_DIR / "tradier_indicators_latest.json"
    if not ind_file.exists():
        logger.error(f"No indicator data at {ind_file}")
        return
    with open(ind_file) as f:
        indicators = json.load(f)
    logger.info(f"Loaded indicators for {len(indicators)} symbols")
    # Load rankings
    rank_file = config.DATA_DIR / "tradier_rankings.json"
    rankings = {}
    if rank_file.exists():
        with open(rank_file) as f:
            rankings = json.load(f)
    # Detect directional signals
    signals = detect_directional_signals(indicators, rankings)
    if args.symbol:
        signals = [s for s in signals if s.symbol == args.symbol.upper()]
        if not signals:
            # Force analysis even without strong signal — fetch price from API if not in indicators
            symbol = args.symbol.upper()
            ind = indicators.get(symbol, {})
            price = ind.get("current_price") or ind.get("mark_price") or ind.get("close_D_prev") or 0
            atr_D = ind.get("atr_D", 0) or 0
            if price <= 0:
                _tmp_client = TradierAPIClient(config, account_key="trb")
                await _tmp_client.connect()
                try:
                    _q = await _tmp_client.get_quote(symbol)
                    price = _q.get("last", 0) or _q.get("bid", 0) or 0
                finally:
                    await _tmp_client.close()
            if price > 0:
                signals = [DirectionalSignal(symbol=symbol, direction="LONG", conviction=50, price=price, atr_D=atr_D, signals={"forced": True}), DirectionalSignal(symbol=symbol, direction="SHORT", conviction=50, price=price, atr_D=atr_D, signals={"forced": True})]
    if args.direction:
        signals = [s for s in signals if s.direction == args.direction.upper()]
    signals = signals[:args.top]
    if not signals:
        logger.info("No strong directional signals detected.")
        print("\nNo symbols with strong D/4h directional signals right now.")
        print("Try --symbol NVDA to force analysis on a specific ticker.")
        return
    print(f"\n{'='*80}")
    print(f"  TRADIER OPTIONS ANALYZER — {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Found {len(signals)} directional signals above threshold")
    print(f"{'='*80}")
    # Initialize API client
    client = TradierAPIClient(config, account_key="trb")
    await client.connect()
    all_outliers = []
    all_spreads = []
    try:
        for sig in signals:
            print(format_signal(sig))
            # Fetch expirations
            expirations = await fetch_expirations(client, sig.symbol)
            if not expirations:
                print(f"    No options available for {sig.symbol}")
                continue
            # Filter to relevant expirations (7-90 DTE for directional, up to 180 for LEAPS)
            now = datetime.now()
            filtered_exps = []
            for exp in expirations:
                try:
                    exp_dt = datetime.strptime(exp, "%Y-%m-%d")
                    dte = (exp_dt - now).days
                    if 3 <= dte <= 180:
                        filtered_exps.append((exp, dte))
                except ValueError:
                    continue
            if not filtered_exps:
                print(f"    No expirations in 3-180 DTE range for {sig.symbol}")
                continue
            # Prioritize: weekly (7-14d), monthly (25-45d), quarterly (60-90d)
            priority_exps = []
            buckets = [(3, 14), (20, 50), (55, 100), (100, 180)]
            for lo, hi in buckets:
                bucket_exps = [(e, d) for e, d in filtered_exps if lo <= d <= hi]
                if bucket_exps:
                    priority_exps.append(bucket_exps[0])  # nearest in each bucket
            if not priority_exps:
                priority_exps = filtered_exps[:4]
            sym_outliers = []
            sym_spreads = []
            for exp, dte in priority_exps:
                chain = await fetch_option_chain(client, sig.symbol, exp)
                if not chain:
                    continue
                outliers, spreads = analyze_chain_for_outliers(sig, chain, exp)
                sym_outliers.extend(outliers)
                sym_spreads.extend(spreads)
                await asyncio.sleep(0.15)  # rate limit courtesy
            # Show top outliers
            sym_outliers.sort(key=lambda x: x.score, reverse=True)
            sym_spreads.sort(key=lambda x: x.score, reverse=True)
            if sym_outliers:
                print(f"\n  {'─'*76}")
                print(f"  TOP OPTION OUTLIERS:")
                for o in sym_outliers[:8]:
                    print(format_outlier(o))
            else:
                print(f"    No significant option outliers found")
            if sym_spreads:
                print(f"\n  SPREAD OPPORTUNITIES:")
                for sp in sym_spreads[:5]:
                    print(format_spread(sp))
            all_outliers.extend(sym_outliers[:8])
            all_spreads.extend(sym_spreads[:5])
    finally:
        await client.close()
    # Summary
    if all_outliers or all_spreads:
        print(f"\n{'='*80}")
        print(f"  SUMMARY — TOP PICKS ACROSS ALL SYMBOLS")
        print(f"{'='*80}")
        all_outliers.sort(key=lambda x: x.score, reverse=True)
        all_spreads.sort(key=lambda x: x.score, reverse=True)
        if all_outliers:
            print(f"\n  BEST OUTLIER OPTIONS (top 10):")
            for o in all_outliers[:10]:
                print(format_outlier(o))
        if all_spreads:
            print(f"\n  BEST SPREAD TRADES (top 5):")
            for sp in all_spreads[:5]:
                print(format_spread(sp))
    # Save results to JSON
    results = {"timestamp": datetime.now().isoformat(), "signals": [{"symbol": s.symbol, "direction": s.direction, "conviction": s.conviction, "price": s.price, "signals": s.signals} for s in signals], "outliers": [{"symbol": o.symbol, "direction": o.direction, "expiration": o.expiration, "strike": o.strike, "type": o.option_type, "recommendation": o.recommendation, "bid": o.bid, "ask": o.ask, "mid": o.mid, "iv_computed": o.iv_computed, "iv_surface_mean": o.iv_surface_mean, "iv_deviation_pct": o.iv_deviation_pct, "edge_pct": o.edge_pct, "delta": o.greeks["delta"], "dte": o.dte, "volume": o.volume, "open_interest": o.open_interest, "score": o.score} for o in all_outliers[:20]], "spreads": [{"symbol": sp.symbol, "direction": sp.direction, "type": sp.spread_type, "expiration": sp.expiration, "long_strike": sp.long_strike, "short_strike": sp.short_strike, "net_debit": sp.net_debit, "max_profit": sp.max_profit, "max_loss": sp.max_loss, "risk_reward": sp.risk_reward, "breakeven": sp.breakeven, "probability_profit": sp.probability_profit, "dte": sp.dte, "score": sp.score} for sp in all_spreads[:10]]}
    out_file = config.DATA_DIR / "options_analysis_latest.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"Results saved to {out_file}")
    print(f"\n  Results saved to {out_file}")


def main():
    parser = argparse.ArgumentParser(description="Tradier Options Analyzer — scan, order, and watch options")
    subparsers = parser.add_subparsers(dest="mode")
    # Scan mode (default when no subcommand)
    scan_p = subparsers.add_parser("scan", help="Scan for mispriced options on directional signals")
    scan_p.add_argument("--symbol", type=str, help="Analyze a specific symbol")
    scan_p.add_argument("--top", type=int, default=15, help="Max signals to analyze")
    scan_p.add_argument("--direction", type=str, choices=["long", "short"], help="Filter by direction")
    # Order mode
    order_p = subparsers.add_parser("order", help="Place an options order")
    order_p.add_argument("order_symbol", type=str, help="Underlying symbol (e.g. NEM)")
    order_p.add_argument("strike", type=float, help="Strike price")
    order_p.add_argument("call_put", type=str, choices=["call", "put"], help="Option type")
    order_p.add_argument("expiration", type=str, help="Expiration date YYYY-MM-DD")
    order_p.add_argument("--qty", type=int, default=1, help="Number of contracts")
    order_p.add_argument("--limit", type=float, help="Limit price (market order if omitted)")
    order_p.add_argument("--side", type=str, default="buy_to_open", choices=["buy_to_open", "sell_to_close", "buy_to_close", "sell_to_open"], help="Order side")
    order_p.add_argument("--account", type=str, default="trb", help="Account key (trb/trc)")
    order_p.add_argument("--smart", action="store_true", help="Smart fill: start at best price, walk toward mid if not filling")
    # Watch mode
    watch_p = subparsers.add_parser("watch", help="Monitor open option positions")
    watch_p.add_argument("--account", type=str, default="trb", help="Account key")
    watch_p.add_argument("--auto-sell", action="store_true", help="Auto sell_to_close on HIGH urgency signals")
    watch_p.add_argument("--daemon", action="store_true", help="Run continuously")
    watch_p.add_argument("--interval", type=int, default=60, help="Check interval in seconds (daemon mode)")
    args = parser.parse_args()
    if args.mode == "order":
        asyncio.run(run_order(args))
    elif args.mode == "watch":
        asyncio.run(run_watch(args))
    else:
        # Default to scan mode — add scan-mode defaults for missing attrs
        if not hasattr(args, "symbol"):
            args.symbol = None
        if not hasattr(args, "top"):
            args.top = 15
        if not hasattr(args, "direction"):
            args.direction = None
        asyncio.run(run_analysis(args))


if __name__ == "__main__":
    main()
