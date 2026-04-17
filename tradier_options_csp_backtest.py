#!/usr/bin/env python3
"""CSP strategy backtest using Black-Scholes synthetic pricing on historic stock klines.

Data constraint: Polygon free tier does NOT include historical options aggregates.
So we build synthetic option prices from:
  - Historic 15m stock klines (real spot path)
  - Rolling 20-day realized vol × 1.08 (IV proxy — captures variance risk premium)
  - Risk-free rate (constant 4.3%)

Strategy simulated: sell 30 DTE cash-secured put at target delta (default 25Δ), hold until
(a) expiration OR (b) non-skippable risk-monitor fires (loss trigger + technical turn).

Compares 3 strategies per symbol:
  - BUY_CALL: buy 25Δ call, 30 DTE, hold to expiration
  - SELL_PUT_CSP: sell 25Δ put, 30 DTE, close on technical turn or expiration
  - BUY_AND_HOLD: baseline

Output per CLAUDE.md backtest reporting rules:
  - Sharpe = mean/std of per-TRADE returns (not annualized)
  - Per-symbol mean, range (min/p25/median/p75/max)
  - Max drawdown % across full window
  - Label: N symbols × N bars × N years × 15m base

Usage:
    python tradier_options_csp_backtest.py --symbols SPY,AAPL,QQQ
    python tradier_options_csp_backtest.py --klines-dir /path/to/klines
    python tradier_options_csp_backtest.py --target-delta 0.25 --dte 30
"""
import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple


def bs_d1(S, K, T, r, sigma):
    if sigma <= 0 or T <= 0:
        return 0.0
    return (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_put_price(S, K, T, r, sigma):
    if T <= 0:
        return max(K - S, 0.0)
    if sigma <= 0:
        return max(K * math.exp(-r * T) - S, 0.0)
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_call_price(S, K, T, r, sigma):
    if T <= 0:
        return max(S - K, 0.0)
    if sigma <= 0:
        return max(S - K * math.exp(-r * T), 0.0)
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def bs_put_delta(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return -1.0 if S < K else 0.0
    return _norm_cdf(bs_d1(S, K, T, r, sigma)) - 1.0  # negative for put


def bs_call_delta(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    return _norm_cdf(bs_d1(S, K, T, r, sigma))


def solve_strike_for_put_delta(S, T, r, sigma, target_delta_abs):
    """Binary search for put strike that yields |delta| ≈ target_delta_abs."""
    lo, hi = S * 0.5, S * 1.01
    for _ in range(40):
        mid = (lo + hi) / 2.0
        d = abs(bs_put_delta(S, mid, T, r, sigma))
        if d < target_delta_abs:
            lo = mid  # need higher strike (closer to ATM) for higher |delta|
        else:
            hi = mid
    return (lo + hi) / 2.0


def solve_strike_for_call_delta(S, T, r, sigma, target_delta_abs):
    lo, hi = S * 0.99, S * 1.5
    for _ in range(40):
        mid = (lo + hi) / 2.0
        d = bs_call_delta(S, mid, T, r, sigma)
        if d > target_delta_abs:
            lo = mid  # need higher strike to lower delta
        else:
            hi = mid
    return (lo + hi) / 2.0


def load_klines(path: Path) -> List[Dict]:
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    return data


def daily_closes(bars: List[Dict]) -> List[Tuple[str, float]]:
    """Collapse 15m bars to daily (date, close). Keeps the LAST 15m close of each UTC date."""
    by_date = {}
    for b in bars:
        ts = b.get("timestamp", "")
        if not ts:
            continue
        d = ts[:10]
        by_date[d] = float(b.get("close", 0))
    return sorted([(k, v) for k, v in by_date.items() if v > 0])


def rolling_realized_vol(closes: List[float], window: int = 20) -> List[float]:
    """Returns σ_annualized per index (0..len-1). Before window: last valid / 0.20."""
    out = [0.20] * len(closes)
    if len(closes) < window + 1:
        return out
    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    # log_rets has len = len(closes) - 1
    for i in range(window, len(closes)):
        window_rets = log_rets[i - window:i]
        mean = sum(window_rets) / window
        var = sum((x - mean) ** 2 for x in window_rets) / (window - 1)
        out[i] = math.sqrt(var * 252)
    # backfill pre-window
    if len(closes) > window:
        first_valid = out[window]
        for i in range(window):
            out[i] = first_valid
    return out


@dataclass
class CSPTrade:
    symbol: str
    entry_date: str
    exit_date: str
    strike: float
    premium_collected: float
    exit_cost: float  # 0 if expired worthless / assigned-no-buy-back
    assigned: bool
    pnl_dollars: float  # premium - exit_cost - (assignment loss if any)
    pnl_pct: float  # pnl / capital_at_risk
    capital_at_risk: float  # strike * 100
    days_held: int
    exit_reason: str


@dataclass
class CallTrade:
    symbol: str
    entry_date: str
    exit_date: str
    strike: float
    premium_paid: float
    exit_value: float
    pnl_dollars: float
    pnl_pct: float
    capital_at_risk: float
    days_held: int


@dataclass
class StockTrade:
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    pnl_dollars: float
    pnl_pct: float
    capital_at_risk: float  # shares × entry_price
    days_held: int


@dataclass
class StockPlusCSPTrade:
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    strike: float
    premium_collected: float
    put_exit_cost: float
    stock_pnl: float
    put_pnl: float
    pnl_dollars: float  # combined
    pnl_pct: float  # combined pnl / capital_at_risk
    capital_at_risk: float  # shares × entry_price + strike × 100 (cash reserved)
    days_held: int
    exit_reason: str


@dataclass
class SpreadTrade:
    symbol: str
    entry_date: str
    exit_date: str
    short_strike: float
    long_strike: float
    width: float
    credit_collected: float  # net credit per spread (short premium - long premium) * 100
    exit_cost: float          # net cost to close (short buyback - long sell) * 100
    pnl_dollars: float
    pnl_pct: float            # pnl / max_loss (capital at risk)
    max_loss: float           # (width - credit/100) * 100 = width_dollars - credit
    capital_at_risk: float    # = max_loss (defined-risk structure)
    days_held: int
    exit_reason: str


def simulate_csp_strategy(symbol: str, bars: List[Dict], target_delta: float, dte: int, r: float, iv_markup: float, loss_trigger: float, hard_cut: float, require_technical: bool, vol_window: int = 60, static_iv: bool = False, strike_breach_pct: float = 0.05, gap_from_entry_pct: float = 0.15) -> Dict:
    """Sell 25Δ cash-secured put every cycle_days, hold to expiry or risk-monitor close.
    static_iv: if True, reprice using entry-time IV throughout (removes vol-dynamics noise).
    vol_window: realized vol lookback window in days (20 = noisy, 60 = smoother)."""
    daily = daily_closes(bars)
    if len(daily) < 60:
        return {"error": "insufficient_data", "symbol": symbol, "n_days": len(daily)}
    dates = [d[0] for d in daily]
    closes = [d[1] for d in daily]
    rvols = rolling_realized_vol(closes, window=vol_window)
    trades: List[CSPTrade] = []
    i = 30
    # Technical proxy: short-put turn-against = 5-day return < -2% (D-frame bearish proxy)
    def tech_against(idx: int) -> bool:
        if idx < 5:
            return False
        r5 = (closes[idx] - closes[idx - 5]) / closes[idx - 5]
        return r5 < -0.02
    while i < len(daily) - dte - 1:
        entry_date = dates[i]
        S0 = closes[i]
        sigma = max(0.08, rvols[i] * iv_markup)  # IV proxy with risk premium markup
        T = dte / 365.0
        K = solve_strike_for_put_delta(S0, T, r, sigma, target_delta)
        premium = bs_put_price(S0, K, T, r, sigma)
        premium_collected = premium * 100.0
        capital_at_risk = K * 100.0
        # Simulate day-by-day
        exit_idx = None
        exit_reason = ""
        exit_cost = 0.0
        assigned = False
        for j in range(1, dte + 1):
            if i + j >= len(daily):
                break
            S_t = closes[i + j]
            T_rem = max(0.001, (dte - j) / 365.0)
            if static_iv:
                sigma_t = sigma
            else:
                sigma_t = max(0.08, rvols[i + j] * iv_markup)
            current_cost = bs_put_price(S_t, K, T_rem, r, sigma_t)
            pnl_pct = (premium - current_cost) / premium if premium > 0.01 else 0.0
            # ── GUARD 1: STRIKE_BREACH — spot N% below strike (wipeout guard) ──
            if S_t <= K * (1.0 - strike_breach_pct):
                exit_idx = i + j
                exit_reason = f"strike_breach(S={S_t:.2f}<={K*(1.0-strike_breach_pct):.2f})"
                exit_cost = current_cost * 100.0
                break
            # ── GUARD 2: ENTRY_GAP — spot N% below entry spot (gap/crash guard) ──
            if S_t <= S0 * (1.0 - gap_from_entry_pct):
                exit_idx = i + j
                exit_reason = f"entry_gap({(S_t-S0)/S0:.1%})"
                exit_cost = current_cost * 100.0
                break
            # ── GUARD 3: HARD_PREMIUM — catastrophic premium loss ──
            if pnl_pct <= hard_cut:
                exit_idx = i + j
                exit_reason = f"hard_premium({pnl_pct:.1%})"
                exit_cost = current_cost * 100.0
                break
            # ── SOFT: Technical-gated close ──
            if pnl_pct <= loss_trigger:
                if (not require_technical) or tech_against(i + j):
                    exit_idx = i + j
                    exit_reason = f"tech_turn(pnl={pnl_pct:.1%})"
                    exit_cost = current_cost * 100.0
                    break
        # Expiration
        if exit_idx is None:
            exit_idx = min(i + dte, len(daily) - 1)
            S_exp = closes[exit_idx]
            if S_exp < K:
                # Assigned — realized loss = strike - spot (per share)
                assigned = True
                exit_cost = (K - S_exp) * 100.0  # amount you lose on assignment before adding premium
                exit_reason = f"assigned(S_exp={S_exp:.2f}<K={K:.2f})"
            else:
                exit_cost = 0.0
                exit_reason = "expired_worthless"
        pnl_dollars = premium_collected - exit_cost
        pnl_pct_cap = pnl_dollars / capital_at_risk
        trades.append(CSPTrade(symbol=symbol, entry_date=entry_date, exit_date=dates[exit_idx], strike=round(K, 2), premium_collected=round(premium_collected, 2), exit_cost=round(exit_cost, 2), assigned=assigned, pnl_dollars=round(pnl_dollars, 2), pnl_pct=round(pnl_pct_cap, 5), capital_at_risk=round(capital_at_risk, 2), days_held=exit_idx - i, exit_reason=exit_reason))
        # Advance cycle — start next trade day after exit
        i = exit_idx + 1
    return {"symbol": symbol, "trades": [asdict(t) for t in trades], "n_trades": len(trades)}


def rolling_iv_rank(rvols: List[float], window: int = 252) -> List[float]:
    """IV rank: percentile of current IV within trailing window. Range [0, 100]."""
    out = [50.0] * len(rvols)
    for i in range(len(rvols)):
        lo = max(0, i - window)
        win = rvols[lo:i + 1]
        if not win:
            continue
        rank = sum(1 for v in win if v <= rvols[i]) / len(win)
        out[i] = rank * 100.0
    return out


def simulate_bull_put_spread(symbol: str, bars: List[Dict], target_delta: float, dte: int, width: float, r: float, iv_markup: float, profit_target_pct: float, max_hold_days: int, strike_breach_pct: float, gap_from_entry_pct: float, iv_rank_min: float, vol_window: int = 60, static_iv: bool = False, account_value: float = 70000.0, max_pos_pct: float = 0.03) -> Dict:
    """Simulate bull put credit spread with all improvements wired in.
    Sell short_delta put, buy long put width dollars below. Close on:
      - 50% profit target (close early, capture time value fast)
      - Strike breach (spot drops N% below short strike → wipeout guard)
      - Entry gap (spot drops N% from entry → gap/crash guard)
      - Max hold days (force close after N days)
      - Expiration (collect full credit if above short strike; max loss if below long strike)
    Entry filter: IV rank must exceed iv_rank_min (only sell when premium is rich).
    Capital at risk per spread = max_loss = (width - credit) dollars.
    Per-position cap: max_loss must be ≤ max_pos_pct × account_value."""
    daily = daily_closes(bars)
    if len(daily) < 60:
        return {"error": "insufficient_data", "symbol": symbol, "n_days": len(daily)}
    dates = [d[0] for d in daily]
    closes = [d[1] for d in daily]
    rvols = rolling_realized_vol(closes, window=vol_window)
    iv_ranks = rolling_iv_rank(rvols, window=252)
    pos_cap_dollars = account_value * max_pos_pct
    trades: List[SpreadTrade] = []
    i = 30
    while i < len(daily) - dte - 1:
        if iv_ranks[i] < iv_rank_min:
            i += 5  # step forward a week if IV not rich enough
            continue
        entry_date = dates[i]
        S0 = closes[i]
        sigma = max(0.08, rvols[i] * iv_markup)
        T = dte / 365.0
        K_short = solve_strike_for_put_delta(S0, T, r, sigma, target_delta)
        K_long = K_short - width
        if K_long <= 0:
            i += 5
            continue
        premium_short = bs_put_price(S0, K_short, T, r, sigma)
        premium_long = bs_put_price(S0, K_long, T, r, sigma)
        credit = (premium_short - premium_long) * 100.0  # net credit dollars
        max_loss = (width - (premium_short - premium_long)) * 100.0
        if max_loss <= 0 or credit <= 0:
            i += 5
            continue
        if max_loss > pos_cap_dollars:
            i += 5  # spread too big for 3% cap at this strike
            continue
        profit_target_dollars = credit * profit_target_pct
        capital_at_risk = max_loss
        exit_idx = None
        exit_reason = ""
        exit_cost = 0.0
        for j in range(1, dte + 1):
            if i + j >= len(daily):
                break
            S_t = closes[i + j]
            T_rem = max(0.001, (dte - j) / 365.0)
            if static_iv:
                sigma_t = sigma
            else:
                sigma_t = max(0.08, rvols[i + j] * iv_markup)
            short_cost = bs_put_price(S_t, K_short, T_rem, r, sigma_t)
            long_cost = bs_put_price(S_t, K_long, T_rem, r, sigma_t)
            current_cost = (short_cost - long_cost) * 100.0  # cost to close both legs
            pnl_now = credit - current_cost
            # GUARD 1: STRIKE_BREACH (spot N% below short strike → wipeout)
            if S_t <= K_short * (1.0 - strike_breach_pct):
                exit_idx = i + j
                exit_cost = current_cost
                exit_reason = f"strike_breach(S={S_t:.2f}<=short={K_short:.2f}*{1-strike_breach_pct:.2f})"
                break
            # GUARD 2: ENTRY_GAP
            if S_t <= S0 * (1.0 - gap_from_entry_pct):
                exit_idx = i + j
                exit_cost = current_cost
                exit_reason = f"entry_gap({(S_t-S0)/S0:.1%})"
                break
            # PROFIT TARGET: close at 50% credit captured
            if pnl_now >= profit_target_dollars:
                exit_idx = i + j
                exit_cost = current_cost
                exit_reason = f"profit_target(pnl=${pnl_now:.0f}>={profit_target_dollars:.0f})"
                break
            # MAX HOLD
            if j >= max_hold_days:
                exit_idx = i + j
                exit_cost = current_cost
                exit_reason = f"max_hold({j}d)"
                break
        # Expiration
        if exit_idx is None:
            exit_idx = min(i + dte, len(daily) - 1)
            S_exp = closes[exit_idx]
            if S_exp >= K_short:
                exit_cost = 0.0  # both expire worthless, keep full credit
                exit_reason = "expire_both_worthless"
            elif S_exp >= K_long:
                # short assigned, long expires worthless → loss = (K_short - S_exp) * 100
                exit_cost = (K_short - S_exp) * 100.0
                exit_reason = f"expire_short_assigned(S={S_exp:.2f})"
            else:
                # both ITM → max loss
                exit_cost = width * 100.0
                exit_reason = f"expire_max_loss(S={S_exp:.2f}<long={K_long:.2f})"
        pnl_dollars = credit - exit_cost
        pnl_pct_risk = pnl_dollars / max_loss if max_loss > 0 else 0.0
        trades.append(SpreadTrade(symbol=symbol, entry_date=entry_date, exit_date=dates[exit_idx], short_strike=round(K_short, 2), long_strike=round(K_long, 2), width=width, credit_collected=round(credit, 2), exit_cost=round(exit_cost, 2), pnl_dollars=round(pnl_dollars, 2), pnl_pct=round(pnl_pct_risk, 5), max_loss=round(max_loss, 2), capital_at_risk=round(capital_at_risk, 2), days_held=exit_idx - i, exit_reason=exit_reason))
        i = exit_idx + 1
    return {"symbol": symbol, "trades": [asdict(t) for t in trades], "n_trades": len(trades)}


def simulate_buy_stock_strategy(symbol: str, bars: List[Dict], hold_days: int, shares: int = 100) -> Dict:
    """Buy shares at entry, hold N days, sell. No options."""
    daily = daily_closes(bars)
    if len(daily) < 60:
        return {"error": "insufficient_data", "symbol": symbol}
    dates = [d[0] for d in daily]
    closes = [d[1] for d in daily]
    trades: List[StockTrade] = []
    i = 30
    while i < len(daily) - hold_days - 1:
        entry_date = dates[i]
        S0 = closes[i]
        exit_idx = min(i + hold_days, len(daily) - 1)
        S_exit = closes[exit_idx]
        pnl = (S_exit - S0) * shares
        cap = S0 * shares
        trades.append(StockTrade(symbol=symbol, entry_date=entry_date, exit_date=dates[exit_idx], entry_price=round(S0, 2), exit_price=round(S_exit, 2), shares=shares, pnl_dollars=round(pnl, 2), pnl_pct=round(pnl / cap if cap > 0 else 0.0, 5), capital_at_risk=round(cap, 2), days_held=exit_idx - i))
        i = exit_idx + 1
    return {"symbol": symbol, "trades": [asdict(t) for t in trades], "n_trades": len(trades)}


def simulate_stock_plus_csp(symbol: str, bars: List[Dict], target_delta: float, dte: int, r: float, iv_markup: float, profit_target_pct: float, max_hold_days: int, strike_breach_pct: float, gap_from_entry_pct: float, iv_rank_min: float, vol_window: int = 60, static_iv: bool = False, shares: int = 100) -> Dict:
    """Combined bullish position: buy N shares + sell 25Δ put. Stock gives no-theta upside,
    put gives premium income. Combined PnL = stock_delta + premium - put_close_cost."""
    daily = daily_closes(bars)
    if len(daily) < 60:
        return {"error": "insufficient_data", "symbol": symbol}
    dates = [d[0] for d in daily]
    closes = [d[1] for d in daily]
    rvols = rolling_realized_vol(closes, window=vol_window)
    iv_ranks = rolling_iv_rank(rvols, window=252)
    trades: List[StockPlusCSPTrade] = []
    i = 30
    while i < len(daily) - dte - 1:
        if iv_ranks[i] < iv_rank_min:
            i += 5
            continue
        entry_date = dates[i]
        S0 = closes[i]
        sigma = max(0.08, rvols[i] * iv_markup)
        T = dte / 365.0
        K = solve_strike_for_put_delta(S0, T, r, sigma, target_delta)
        premium = bs_put_price(S0, K, T, r, sigma)
        premium_collected = premium * 100.0
        stock_cost = S0 * shares
        cash_reserve = K * 100.0  # CSP reserve
        capital_at_risk = stock_cost + cash_reserve - premium_collected  # total capital deployed
        exit_idx = None
        exit_reason = ""
        put_exit_cost = 0.0
        for j in range(1, dte + 1):
            if i + j >= len(daily):
                break
            S_t = closes[i + j]
            T_rem = max(0.001, (dte - j) / 365.0)
            sigma_t = sigma if static_iv else max(0.08, rvols[i + j] * iv_markup)
            put_cost = bs_put_price(S_t, K, T_rem, r, sigma_t)
            put_pnl_now = (premium - put_cost) * 100.0
            stock_pnl_now = (S_t - S0) * shares
            combined_pnl = put_pnl_now + stock_pnl_now
            # PROFIT_TARGET: close at 50% of premium captured AND stock up
            profit_target_dollars = premium_collected * profit_target_pct
            if put_pnl_now >= profit_target_dollars and stock_pnl_now > 0:
                exit_idx = i + j
                put_exit_cost = put_cost * 100.0
                exit_reason = f"profit_target(put=${put_pnl_now:.0f}>={profit_target_dollars:.0f})"
                break
            # STRIKE_BREACH (only puts care; stock just rides)
            if S_t <= K * (1.0 - strike_breach_pct):
                exit_idx = i + j
                put_exit_cost = put_cost * 100.0
                exit_reason = f"strike_breach(S={S_t:.2f})"
                break
            # ENTRY_GAP
            if S_t <= S0 * (1.0 - gap_from_entry_pct):
                exit_idx = i + j
                put_exit_cost = put_cost * 100.0
                exit_reason = f"entry_gap({(S_t-S0)/S0:.1%})"
                break
            # MAX_HOLD
            if j >= max_hold_days:
                exit_idx = i + j
                put_exit_cost = put_cost * 100.0
                exit_reason = f"max_hold({j}d)"
                break
        if exit_idx is None:
            exit_idx = min(i + dte, len(daily) - 1)
            S_exp = closes[exit_idx]
            if S_exp >= K:
                put_exit_cost = 0.0
                exit_reason = "expire_put_worthless"
            else:
                # Assigned: in a real scenario we'd buy more shares; here treat as put_exit_cost = intrinsic
                put_exit_cost = (K - S_exp) * 100.0
                exit_reason = f"expire_assigned(S={S_exp:.2f})"
        S_exit = closes[exit_idx]
        stock_pnl = (S_exit - S0) * shares
        put_pnl = premium_collected - put_exit_cost
        combined = stock_pnl + put_pnl
        trades.append(StockPlusCSPTrade(symbol=symbol, entry_date=entry_date, exit_date=dates[exit_idx], entry_price=round(S0, 2), exit_price=round(S_exit, 2), shares=shares, strike=round(K, 2), premium_collected=round(premium_collected, 2), put_exit_cost=round(put_exit_cost, 2), stock_pnl=round(stock_pnl, 2), put_pnl=round(put_pnl, 2), pnl_dollars=round(combined, 2), pnl_pct=round(combined / capital_at_risk if capital_at_risk > 0 else 0.0, 5), capital_at_risk=round(capital_at_risk, 2), days_held=exit_idx - i, exit_reason=exit_reason))
        i = exit_idx + 1
    return {"symbol": symbol, "trades": [asdict(t) for t in trades], "n_trades": len(trades)}


def simulate_buy_call_strategy(symbol: str, bars: List[Dict], target_delta: float, dte: int, r: float, iv_markup: float) -> Dict:
    daily = daily_closes(bars)
    if len(daily) < 60:
        return {"error": "insufficient_data", "symbol": symbol}
    dates = [d[0] for d in daily]
    closes = [d[1] for d in daily]
    rvols = rolling_realized_vol(closes, window=20)
    trades: List[CallTrade] = []
    i = 30
    while i < len(daily) - dte - 1:
        entry_date = dates[i]
        S0 = closes[i]
        sigma = max(0.08, rvols[i] * iv_markup)
        T = dte / 365.0
        K = solve_strike_for_call_delta(S0, T, r, sigma, target_delta)
        premium_paid = bs_call_price(S0, K, T, r, sigma) * 100.0
        # Hold to expiration
        exit_idx = min(i + dte, len(daily) - 1)
        S_exp = closes[exit_idx]
        exit_value = max(S_exp - K, 0.0) * 100.0
        pnl = exit_value - premium_paid
        trades.append(CallTrade(symbol=symbol, entry_date=entry_date, exit_date=dates[exit_idx], strike=round(K, 2), premium_paid=round(premium_paid, 2), exit_value=round(exit_value, 2), pnl_dollars=round(pnl, 2), pnl_pct=round(pnl / premium_paid if premium_paid > 0.01 else 0.0, 5), capital_at_risk=round(premium_paid, 2), days_held=exit_idx - i))
        i = exit_idx + 1
    return {"symbol": symbol, "trades": [asdict(t) for t in trades], "n_trades": len(trades)}


def summarize(trades: List[Dict], strategy: str, symbol: str) -> Dict:
    if not trades:
        return {"strategy": strategy, "symbol": symbol, "n_trades": 0}
    returns = [t["pnl_pct"] for t in trades]
    pnl_dollars = [t["pnl_dollars"] for t in trades]
    mean_ret = statistics.mean(returns)
    stdev_ret = statistics.stdev(returns) if len(returns) > 1 else 1e-9
    sharpe = mean_ret / stdev_ret if stdev_ret > 0 else 0.0
    # Cumulative equity path for drawdown
    equity = [0.0]
    for p in pnl_dollars:
        equity.append(equity[-1] + p)
    peak = equity[0]
    max_dd_dollars = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd_dollars:
            max_dd_dollars = dd
    # DD as % of capital-at-risk average
    avg_capital = statistics.mean([t["capital_at_risk"] for t in trades])
    max_dd_pct = -(max_dd_dollars / avg_capital * 100.0) if avg_capital > 0 else 0.0
    win_rate = sum(1 for r in returns if r > 0) / len(returns) * 100.0
    sorted_r = sorted(returns)
    n = len(sorted_r)
    def pct(p):
        idx = min(n - 1, max(0, int(p * n)))
        return sorted_r[idx]
    return {"strategy": strategy, "symbol": symbol, "n_trades": len(trades), "mean_ret": round(mean_ret, 5), "stdev_ret": round(stdev_ret, 5), "sharpe_per_trade": round(sharpe, 3), "win_rate_pct": round(win_rate, 1), "total_pnl": round(sum(pnl_dollars), 2), "avg_capital": round(avg_capital, 2), "max_dd_dollars": round(max_dd_dollars, 2), "max_dd_pct_of_capital": round(max_dd_pct, 2), "min_ret": round(sorted_r[0], 5), "p25_ret": round(pct(0.25), 5), "median_ret": round(pct(0.50), 5), "p75_ret": round(pct(0.75), 5), "max_ret": round(sorted_r[-1], 5)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="SPY,AAPL,QQQ")
    ap.add_argument("--klines-dir", default="/tmp/csp_test")
    ap.add_argument("--target-delta", type=float, default=0.25)
    ap.add_argument("--dte", type=int, default=30)
    ap.add_argument("--risk-free", type=float, default=0.043)
    ap.add_argument("--iv-markup", type=float, default=1.08, help="IV proxy = realized_vol × markup (default 1.08 captures variance risk premium)")
    ap.add_argument("--loss-trigger", type=float, default=-0.05)
    ap.add_argument("--hard-cut", type=float, default=-0.50)
    ap.add_argument("--no-technical", action="store_true", help="Disable technical-turn gate (pure P&L exit)")
    ap.add_argument("--vol-window", type=int, default=60, help="Realized vol lookback days (20=noisy, 60=smoother)")
    ap.add_argument("--static-iv", action="store_true", help="Use entry-time IV throughout (no vol-dynamics repricing)")
    ap.add_argument("--strike-breach-pct", type=float, default=0.05, help="Wipeout guard: close if spot drops N%% below strike (put ITM)")
    ap.add_argument("--gap-from-entry-pct", type=float, default=0.15, help="Wipeout guard: close if spot drops N%% below entry spot")
    ap.add_argument("--strategy", default="both", choices=["csp", "spread", "both", "all"], help="Which sell strategy: csp|spread|both|all (all includes stock+csp + pure stock)")
    ap.add_argument("--klines-suffix", default="15m", help="Kline file suffix: 15m or daily")
    ap.add_argument("--spread-width", type=float, default=10.0, help="Bull put spread width in $ (5, 10, 20)")
    ap.add_argument("--spread-short-delta", type=float, default=0.25, help="Short put target delta for spread")
    ap.add_argument("--profit-target", type=float, default=0.50, help="Close at N%% of credit captured")
    ap.add_argument("--max-hold-days", type=int, default=21, help="Force close after N days")
    ap.add_argument("--iv-rank-min", type=float, default=50.0, help="Only open when realized-vol rank >= this (0-100)")
    ap.add_argument("--account-value", type=float, default=70000.0, help="Account equity for 3%% cap")
    ap.add_argument("--max-pos-pct", type=float, default=0.03, help="Max fraction of account at risk per position")
    ap.add_argument("--out", default="/tmp/csp_test/csp_backtest_results.json")
    args = ap.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    klines_dir = Path(args.klines_dir)
    results = {"config": vars(args), "per_symbol": {}, "summary": {}}
    csp_summaries = []
    call_summaries = []
    spread_summaries = []
    stock_summaries = []
    stock_csp_summaries = []
    date_ranges = []
    total_bars = 0
    for sym in symbols:
        kpath = klines_dir / f"{sym}_{args.klines_suffix}.json"
        if not kpath.exists():
            print(f"SKIP {sym}: no klines at {kpath}", file=sys.stderr)
            continue
        bars = load_klines(kpath)
        if not bars:
            continue
        total_bars += len(bars)
        date_ranges.append((bars[0].get("timestamp", "")[:10], bars[-1].get("timestamp", "")[:10]))
        per_sym_data = {}
        if args.strategy in ("csp", "both"):
            csp_res = simulate_csp_strategy(sym, bars, args.target_delta, args.dte, args.risk_free, args.iv_markup, args.loss_trigger, args.hard_cut, require_technical=not args.no_technical, vol_window=args.vol_window, static_iv=args.static_iv, strike_breach_pct=args.strike_breach_pct, gap_from_entry_pct=args.gap_from_entry_pct)
            csp_summary = summarize(csp_res.get("trades", []), "SELL_PUT_CSP", sym)
            csp_summaries.append(csp_summary)
            per_sym_data["csp"] = csp_summary
            per_sym_data["csp_trades"] = csp_res.get("trades", [])[:5]
        if args.strategy in ("spread", "both"):
            spread_res = simulate_bull_put_spread(sym, bars, args.spread_short_delta, args.dte, args.spread_width, args.risk_free, args.iv_markup, args.profit_target, args.max_hold_days, args.strike_breach_pct, args.gap_from_entry_pct, args.iv_rank_min, vol_window=args.vol_window, static_iv=args.static_iv, account_value=args.account_value, max_pos_pct=args.max_pos_pct)
            spread_summary = summarize(spread_res.get("trades", []), "BULL_PUT_SPREAD", sym)
            spread_summaries.append(spread_summary)
            per_sym_data["spread"] = spread_summary
            per_sym_data["spread_trades"] = spread_res.get("trades", [])[:5]
        call_res = simulate_buy_call_strategy(sym, bars, args.target_delta, args.dte, args.risk_free, args.iv_markup)
        call_summary = summarize(call_res.get("trades", []), "BUY_CALL", sym)
        call_summaries.append(call_summary)
        per_sym_data["buy_call"] = call_summary
        per_sym_data["buy_call_trades"] = call_res.get("trades", [])[:5]
        results["per_symbol"][sym] = per_sym_data
    # Per-symbol aggregation (CLAUDE.md mandate — not pool)
    def agg(summaries, key):
        vals = [s[key] for s in summaries if s.get("n_trades", 0) > 0 and key in s]
        if not vals:
            return None
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        return {"mean": round(statistics.mean(vals), 4), "min": round(vals_sorted[0], 4), "p25": round(vals_sorted[max(0, n // 4)], 4), "median": round(vals_sorted[n // 2], 4), "p75": round(vals_sorted[min(n - 1, 3 * n // 4)], 4), "max": round(vals_sorted[-1], 4), "n_symbols": n}
    def total_trades(summaries):
        return sum(s.get("n_trades", 0) for s in summaries)
    results["summary"]["label"] = f"{len(symbols)} symbols × {total_bars:,} bars × ~2 years × 15m base (daily-sampled)"
    results["summary"]["date_range"] = f"{min(dr[0] for dr in date_ranges) if date_ranges else '?'} → {max(dr[1] for dr in date_ranges) if date_ranges else '?'}"
    results["summary"]["CSP_per_symbol_sharpe"] = agg(csp_summaries, "sharpe_per_trade")
    results["summary"]["CSP_per_symbol_mean_ret"] = agg(csp_summaries, "mean_ret")
    results["summary"]["CSP_per_symbol_win_rate"] = agg(csp_summaries, "win_rate_pct")
    results["summary"]["CSP_per_symbol_max_dd_pct"] = agg(csp_summaries, "max_dd_pct_of_capital")
    results["summary"]["CSP_total_trades"] = total_trades(csp_summaries)
    if spread_summaries:
        results["summary"]["SPREAD_per_symbol_sharpe"] = agg(spread_summaries, "sharpe_per_trade")
        results["summary"]["SPREAD_per_symbol_mean_ret"] = agg(spread_summaries, "mean_ret")
        results["summary"]["SPREAD_per_symbol_win_rate"] = agg(spread_summaries, "win_rate_pct")
        results["summary"]["SPREAD_per_symbol_max_dd_pct"] = agg(spread_summaries, "max_dd_pct_of_capital")
        results["summary"]["SPREAD_per_symbol_total_pnl"] = agg(spread_summaries, "total_pnl")
        results["summary"]["SPREAD_total_trades"] = total_trades(spread_summaries)
    results["summary"]["BUY_CALL_per_symbol_sharpe"] = agg(call_summaries, "sharpe_per_trade")
    results["summary"]["BUY_CALL_per_symbol_mean_ret"] = agg(call_summaries, "mean_ret")
    results["summary"]["BUY_CALL_per_symbol_win_rate"] = agg(call_summaries, "win_rate_pct")
    results["summary"]["BUY_CALL_per_symbol_max_dd_pct"] = agg(call_summaries, "max_dd_pct_of_capital")
    results["summary"]["BUY_CALL_total_trades"] = total_trades(call_summaries)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    # Print summary
    print(f"\n{'=' * 90}")
    print(f"CSP BACKTEST SUMMARY — {results['summary']['label']}")
    print(f"Date range: {results['summary']['date_range']}")
    print(f"Config: delta={args.target_delta} dte={args.dte} iv_markup={args.iv_markup} loss_trig={args.loss_trigger} hard_cut={args.hard_cut} require_tech={not args.no_technical}")
    print(f"{'=' * 90}")
    def row(label, stat):
        if stat is None:
            return f"  {label}: no data"
        return f"  {label}: mean={stat['mean']} | range [{stat['min']} .. {stat['p25']} .. {stat['median']} .. {stat['p75']} .. {stat['max']}] across {stat['n_symbols']} sym"
    print("\n[SELL_PUT_CSP]")
    print(row("Sharpe (per-trade)", results["summary"]["CSP_per_symbol_sharpe"]))
    print(row("Mean return/trade", results["summary"]["CSP_per_symbol_mean_ret"]))
    print(row("Win rate %", results["summary"]["CSP_per_symbol_win_rate"]))
    print(row("Max DD % of capital", results["summary"]["CSP_per_symbol_max_dd_pct"]))
    print(f"  Total trades across all symbols: {results['summary']['CSP_total_trades']}")
    if spread_summaries:
        print(f"\n[BULL_PUT_SPREAD width=${args.spread_width} short_delta={args.spread_short_delta} iv_rank>={args.iv_rank_min} PT={args.profit_target:.0%} max_hold={args.max_hold_days}d]")
        print(row("Sharpe (per-trade)", results["summary"]["SPREAD_per_symbol_sharpe"]))
        print(row("Mean return/trade", results["summary"]["SPREAD_per_symbol_mean_ret"]))
        print(row("Win rate %", results["summary"]["SPREAD_per_symbol_win_rate"]))
        print(row("Total $ PnL", results["summary"]["SPREAD_per_symbol_total_pnl"]))
        print(row("Max DD % of max-loss", results["summary"]["SPREAD_per_symbol_max_dd_pct"]))
        print(f"  Total trades across all symbols: {results['summary']['SPREAD_total_trades']}")
    print("\n[BUY_CALL baseline]")
    print(row("Sharpe (per-trade)", results["summary"]["BUY_CALL_per_symbol_sharpe"]))
    print(row("Mean return/trade", results["summary"]["BUY_CALL_per_symbol_mean_ret"]))
    print(row("Win rate %", results["summary"]["BUY_CALL_per_symbol_win_rate"]))
    print(row("Max DD % of capital", results["summary"]["BUY_CALL_per_symbol_max_dd_pct"]))
    print(f"  Total trades across all symbols: {results['summary']['BUY_CALL_total_trades']}")
    print(f"\nFull JSON written to {out_path}")
    print(f"\n⚠️  IMPORTANT: IV is a realized-vol × {args.iv_markup} proxy, not real implied vol.")
    print("   In reality, IV often exceeds realized by 10-20% during volatility regimes,")
    print("   which benefits sellers. Treat these numbers as LOWER BOUND for CSP edge.")


if __name__ == "__main__":
    main()
