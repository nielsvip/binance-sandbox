#!/usr/bin/env python3
"""
Autonomous Options Trading Agent — runs at 14:05 UTC (30min after open).

Workflow:
  1. Read scanner results from 14:00 UTC run (options_analysis_latest.json)
  2. Assess market conditions: gap direction, first 30min action, news sentiment, fear/greed
  3. Check current option positions + total exposure vs $5k cap
  4. Decide: load up (buy new), hold, or unload (sell existing)
  5. Execute via smart fill (best price → walk to mid)

Budget rules:
  - Max per order: $800. Exception: if ONE contract costs >$800, buy exactly 1 contract.
  - If option price >$9/share (>$900/contract): max 1 contract, no exceptions.
  - Max calls: $3000 total. Max puts: $3000 total. Both sides must stay balanced.
  - Never add to a position already held on that underlying symbol.
  - Never buy into extreme fear without strong reversal signal
  - Never buy calls into a gap-down that hasn't corrected
  - Prefer 60-120 DTE for directional plays

Usage:
    python tradier_options_agent.py                     # Full autonomous run
    python tradier_options_agent.py --dry-run            # Analyze only, no orders
    python tradier_options_agent.py --account trc        # Use sandbox account
    python tradier_options_agent.py --budget 500         # Override max per order
"""
import asyncio
import json
import logging
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
from tradier_options_analyzer import (
    detect_directional_signals, fetch_expirations, fetch_option_chain,
    analyze_chain_for_outliers, get_option_positions, analyze_option_position,
    smart_fill_option, place_option_order, build_occ_symbol, parse_occ_symbol,
    implied_vol, bs_price, bs_greeks, DirectionalSignal, OptionOutlier,
    SpreadOpportunity, ExitSignal, _load_gtc_orders, _save_gtc_orders,
    _load_wt_history, _save_wt_history,
    score_sell_put_csp, pick_best_structure, CSPCandidate, StructureChoice,
    score_bull_put_spread, SpreadCandidate,
)
from wt_dc_entry_scorer import score_entry as wt_dc_score_entry

logger = logging.getLogger("options_agent")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    log_dir = Path(os.path.expanduser("~")) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(log_dir / "tradier_options_agent.log", maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(sh)

# ── Budget & Risk Constants ──────────────────────────────────────────────────
# Per-order sizing rules (2026-04-23):
#   • Option price ≤ $9/share  → buy floor($800 / contract_cost) contracts, total ≤ $800
#   • Option price  > $9/share → buy exactly 1 contract at full cost (e.g. $20 = $2,000 order)
# MAX_CHEAP_ORDER_BUDGET is the $800 cap that applies ONLY to cheap options.
# It is NOT a global per-order spend limit — expensive single contracts pay their full price.
MAX_CHEAP_ORDER_BUDGET = 800.0    # Max spend when option price ≤ $9/share (multiple contracts OK)
MAX_PER_ORDER = MAX_CHEAP_ORDER_BUDGET  # alias kept for legacy arg plumbing
PREFERRED_PER_ORDER = MAX_CHEAP_ORDER_BUDGET
# 2026-04-22 user rule: $3k calls / $3k puts / $6k total
MAX_TOTAL_OPTIONS = 6000.0        # Hard ceiling — NO new positions above this (= sum of per-side caps)
MAX_TOTAL_CALLS = 3000.0          # Max $ in calls
MAX_TOTAL_PUTS = 3000.0           # Max $ in puts
MAX_POSITIONS = 8                 # Max open option positions (tightened 2026-04-26 per OPTIONS_OVERHAUL §L1.C1, was 15)
MAX_CALL_RATIO = 0.65             # Max calls as fraction of total (65%)
MAX_PUT_RATIO = 0.65              # Max puts as fraction of total (65%)
MIN_ORDER_SIZE = 50.0
GTC_DISCOUNT_NORMAL = 0.85        # 85% of ask — harder to fill
GTC_DISCOUNT_HIGH = 0.88          # 88% of ask for high conviction
GTC_DISCOUNT_LOWBALL = 0.60       # 60% for lottery tickets
MAX_CONTRACTS_PER_ORDER = 3   # Hard cap — never buy >3 contracts in one order


# ── Diversification Engine ───────────────────────────────────────────────────

@dataclass
class PortfolioDiversification:
    total_exposure: float
    call_exposure: float
    put_exposure: float
    hedge_ratio: float  # puts / (puts + calls), 0.0-1.0
    positions_by_sector: Dict[str, float]  # sector → $ exposure
    positions_by_group: Dict[str, float]  # group → $ exposure
    positions_by_symbol: Dict[str, float]  # symbol → $ exposure
    calls_by_sector: Dict[str, float]
    puts_by_sector: Dict[str, float]
    n_sectors: int
    n_groups: int
    n_hedged_sectors: int  # sectors that have BOTH calls and puts
    diversification_tier: str  # "BASE", "HEDGED", "FULL_DIV"
    effective_cap: float  # what's the actual $ cap based on diversification
    headroom: float  # effective_cap - total_exposure
    violations: List[str]  # any concentration violations
    score: float  # 0-100 diversification quality


def analyze_diversification(positions: List[Dict], config: TradierConfig) -> PortfolioDiversification:
    """Analyze current option portfolio for diversification quality."""
    sector_map = config.SECTOR_MAP
    group_map = config.SECTOR_GROUPS
    by_sector = {}
    by_group = {}
    by_symbol = {}
    calls_by_sector = {}
    puts_by_sector = {}
    call_exp = 0.0
    put_exp = 0.0
    total = 0.0
    for pos in positions:
        parsed = parse_occ_symbol(pos.get("occ_symbol", "") or pos.get("symbol", ""))
        if not parsed:
            continue
        symbol = parsed["symbol"]
        opt_type = parsed["option_type"]
        cost = abs(pos.get("cost_basis", 0) or 0)
        if cost <= 0:
            continue
        sector = sector_map.get(symbol, "UNKNOWN")
        group = group_map.get(sector, "OTHER")
        total += cost
        by_symbol[symbol] = by_symbol.get(symbol, 0) + cost
        by_sector[sector] = by_sector.get(sector, 0) + cost
        by_group[group] = by_group.get(group, 0) + cost
        if opt_type == "call":
            call_exp += cost
            calls_by_sector[sector] = calls_by_sector.get(sector, 0) + cost
        else:
            put_exp += cost
            puts_by_sector[sector] = puts_by_sector.get(sector, 0) + cost
    hedge_ratio = put_exp / (put_exp + call_exp) if (put_exp + call_exp) > 0 else 0.0
    n_sectors = len(by_sector)
    n_groups = len(by_group)
    # Count hedged sectors: sectors with BOTH calls and puts
    hedged_sectors = set(calls_by_sector.keys()) & set(puts_by_sector.keys())
    n_hedged = len(hedged_sectors)
    # Determine tier
    violations = []
    if total > 0:
        for sym, exp in by_symbol.items():
            if exp / total > config.OPTIONS_MAX_PER_SYMBOL:
                violations.append(f"{sym}: {exp/total*100:.0f}% of portfolio (max {config.OPTIONS_MAX_PER_SYMBOL*100:.0f}%)")
        for sec, exp in by_sector.items():
            if exp / total > config.OPTIONS_MAX_PER_SECTOR:
                violations.append(f"Sector {sec}: {exp/total*100:.0f}% (max {config.OPTIONS_MAX_PER_SECTOR*100:.0f}%)")
        for grp, exp in by_group.items():
            if exp / total > config.OPTIONS_MAX_PER_GROUP:
                violations.append(f"Group {grp}: {exp/total*100:.0f}% (max {config.OPTIONS_MAX_PER_GROUP*100:.0f}%)")
    # Tier logic
    is_hedged = hedge_ratio >= config.OPTIONS_HEDGE_RATIO_MIN and n_sectors >= config.OPTIONS_MIN_SECTORS
    is_full_div = is_hedged and n_groups >= config.OPTIONS_MIN_GROUPS and not violations
    if is_full_div:
        tier = "FULL_DIV"
        cap = config.OPTIONS_FULL_DIV_CAP
    elif is_hedged:
        tier = "HEDGED"
        cap = config.OPTIONS_HEDGED_CAP
    else:
        tier = "BASE"
        cap = config.OPTIONS_BASE_CAP
    # Score: 0-100
    score = 0
    if hedge_ratio >= 0.25:
        score += 25
    elif hedge_ratio >= 0.10:
        score += 10
    score += min(n_sectors * 8, 30)  # up to 30 for sector diversity
    score += min(n_groups * 10, 25)  # up to 25 for group diversity
    score += min(n_hedged * 10, 20)  # up to 20 for hedged sectors
    if not violations:
        score = min(score, 100)
    else:
        score = max(score - len(violations) * 10, 0)
    return PortfolioDiversification(total_exposure=total, call_exposure=call_exp, put_exposure=put_exp, hedge_ratio=hedge_ratio, positions_by_sector=by_sector, positions_by_group=by_group, positions_by_symbol=by_symbol, calls_by_sector=calls_by_sector, puts_by_sector=puts_by_sector, n_sectors=n_sectors, n_groups=n_groups, n_hedged_sectors=n_hedged, diversification_tier=tier, effective_cap=cap, headroom=cap - total, violations=violations, score=score)


def get_diversification_guidance(div: PortfolioDiversification, config: TradierConfig) -> Dict[str, Any]:
    """Return guidance on what to buy next to improve diversification."""
    guidance = {"needs_puts": False, "needs_new_sector": False, "needs_new_group": False, "blocked_sectors": [], "blocked_symbols": [], "preferred_direction": None, "preferred_sectors": [], "preferred_groups": []}
    if div.total_exposure <= 0:
        return guidance
    # Need puts?
    if div.hedge_ratio < config.OPTIONS_HEDGE_RATIO_MIN:
        guidance["needs_puts"] = True
        guidance["preferred_direction"] = "SHORT"
    # Blocked sectors (over concentration)
    for sec, exp in div.positions_by_sector.items():
        if exp / max(div.total_exposure, 1) > config.OPTIONS_MAX_PER_SECTOR * 0.8:
            guidance["blocked_sectors"].append(sec)
    # Blocked symbols
    for sym, exp in div.positions_by_symbol.items():
        if exp / max(div.total_exposure, 1) > config.OPTIONS_MAX_PER_SYMBOL * 0.8:
            guidance["blocked_symbols"].append(sym)
    # What groups are underrepresented?
    all_groups = set(config.SECTOR_GROUPS.values())
    current_groups = set(div.positions_by_group.keys())
    missing_groups = all_groups - current_groups - {"OTHER", "SPECULATIVE", "INDEX"}
    if missing_groups:
        guidance["needs_new_group"] = True
        guidance["preferred_groups"] = list(missing_groups)
    # What sectors within existing groups could add hedging?
    for sec in div.calls_by_sector:
        if sec not in div.puts_by_sector:
            group = config.SECTOR_GROUPS.get(sec, "OTHER")
            # Find other sectors in same group for cross-hedge
            siblings = [s for s, g in config.SECTOR_GROUPS.items() if g == group and s != sec]
            guidance["preferred_sectors"].extend(siblings)
    return guidance


def format_diversification(div: PortfolioDiversification) -> str:
    """Pretty-print diversification status."""
    lines = []
    tier_colors = {"BASE": "\033[91m", "HEDGED": "\033[93m", "FULL_DIV": "\033[92m"}
    tc = tier_colors.get(div.diversification_tier, "\033[0m")
    lines.append(f"    Tier: {tc}{div.diversification_tier}\033[0m  Cap: ${div.effective_cap:,.0f}  Headroom: ${div.headroom:,.0f}  Score: {div.score}/100")
    lines.append(f"    Calls: ${div.call_exposure:,.0f}  Puts: ${div.put_exposure:,.0f}  Hedge ratio: {div.hedge_ratio:.0%}")
    lines.append(f"    Sectors: {div.n_sectors}  Groups: {div.n_groups}  Hedged sectors: {div.n_hedged_sectors}")
    if div.positions_by_sector:
        sec_str = "  ".join(f"{s}=${v:,.0f}" for s, v in sorted(div.positions_by_sector.items(), key=lambda x: -x[1]))
        lines.append(f"    By sector: {sec_str}")
    if div.violations:
        for v in div.violations:
            lines.append(f"    \033[91mVIOLATION: {v}\033[0m")
    return "\n".join(lines)


def is_bear_scenario(symbol: str, config: TradierConfig) -> bool:
    """Return True if this symbol goes UP when the broad market goes DOWN.
    A CALL on a bear_scenario symbol = bearish market bet.
    A PUT on a bear_scenario symbol = bullish market bet."""
    bear_set = getattr(config, "BEAR_SCENARIO_SYMBOLS", set())
    return symbol in bear_set


def compute_market_direction_ratio(positions: List[Dict], config: TradierConfig) -> Tuple[float, float, float]:
    """Compute true market-direction exposure from options positions.
    Returns (bull_exposure, bear_exposure, bull_fraction).
    bull_fraction = bull_exposure / (bull + bear), range 0-1.
    CALL on bull_scenario = bullish. PUT on bull_scenario = bearish.
    CALL on bear_scenario = bearish. PUT on bear_scenario = bullish."""
    bull_exp = 0.0
    bear_exp = 0.0
    for pos in positions:
        parsed = parse_occ_symbol(pos.get("occ_symbol", "") or pos.get("symbol", ""))
        if not parsed:
            continue
        cost = abs(pos.get("cost_basis", 0) or 0)
        if cost <= 0:
            continue
        symbol = parsed["symbol"]
        opt_type = parsed["option_type"]
        bear = is_bear_scenario(symbol, config)
        is_bull_bet = (opt_type == "call" and not bear) or (opt_type == "put" and bear)
        if is_bull_bet:
            bull_exp += cost
        else:
            bear_exp += cost
    total = bull_exp + bear_exp
    bull_fraction = bull_exp / total if total > 0 else 0.5
    return bull_exp, bear_exp, bull_fraction


# ── Market Assessment ────────────────────────────────────────────────────────

@dataclass
class MarketAssessment:
    fear_greed_value: int
    fear_greed_class: str
    market_mode: str
    market_index: float
    gap_direction: str  # "UP", "DOWN", "FLAT"
    gap_pct: float
    first_30min_trend: str  # "BULLISH", "BEARISH", "CHOPPY"
    first_30min_range_pct: float
    news_bias: str  # "BULLISH", "BEARISH", "NEUTRAL"
    news_stock_longs: List[str]
    news_stock_shorts: List[str]
    overall_bias: str  # "BULLISH", "BEARISH", "NEUTRAL", "EXTREME_FEAR", "EXTREME_GREED"
    confidence: float  # 0-100
    notes: List[str]


async def assess_market(client: TradierAPIClient, config: TradierConfig) -> MarketAssessment:
    """Assess current market conditions using all available data sources."""
    notes = []
    # 1. Fear & Greed
    fear_greed_value = 50
    fear_greed_class = "Neutral"
    news_file = BASE_PATH / "data" / "news_daily_picks.json"
    stock_longs = []
    stock_shorts = []
    if news_file.exists():
        try:
            with open(news_file) as f:
                news = json.load(f)
            fg = news.get("fear_greed", {})
            fear_greed_value = fg.get("value", 50)
            fear_greed_class = fg.get("classification", "Neutral")
            stock_longs = [p["symbol"] for p in news.get("stock_long", [])]
            stock_shorts = [p["symbol"] for p in news.get("stock_short", [])]
            notes.append(f"Fear/Greed: {fear_greed_value} ({fear_greed_class})")
            if stock_longs:
                notes.append(f"News bullish: {', '.join(stock_longs[:5])}")
            if stock_shorts:
                notes.append(f"News bearish: {', '.join(stock_shorts[:5])}")
        except Exception as e:
            notes.append(f"News data error: {e}")
    # 2. Market mode
    market_mode = "NORMAL_MODE"
    market_index = 50.0
    mode_file = BASE_PATH / "data" / "market_mode.json"
    if mode_file.exists():
        try:
            with open(mode_file) as f:
                mm = json.load(f)
            market_mode = mm.get("mode", "NORMAL_MODE")
            market_index = mm.get("market_index", 50.0)
            notes.append(f"Market mode: {market_mode} (index={market_index:.1f})")
        except Exception:
            pass
    # 3. SPY gap + first 30min analysis
    gap_direction = "FLAT"
    gap_pct = 0.0
    first_30min_trend = "CHOPPY"
    first_30min_range_pct = 0.0
    try:
        spy_quote = await client.get_quote("SPY")
        spy_open = spy_quote.get("open", 0) or 0
        spy_prevclose = spy_quote.get("prevclose", 0) or spy_quote.get("previous_close", 0) or 0
        spy_last = spy_quote.get("last", 0) or 0
        if spy_open > 0 and spy_prevclose > 0:
            gap_pct = ((spy_open - spy_prevclose) / spy_prevclose) * 100
            if gap_pct > 0.3:
                gap_direction = "UP"
            elif gap_pct < -0.3:
                gap_direction = "DOWN"
            else:
                gap_direction = "FLAT"
            notes.append(f"SPY gap: {gap_direction} ({gap_pct:+.2f}%)")
        # First 30min: compare current price to open
        if spy_last > 0 and spy_open > 0:
            move_from_open = ((spy_last - spy_open) / spy_open) * 100
            if move_from_open > 0.3:
                first_30min_trend = "BULLISH"
            elif move_from_open < -0.3:
                first_30min_trend = "BEARISH"
            else:
                first_30min_trend = "CHOPPY"
            # Get high/low for range
            spy_high = spy_quote.get("high", spy_last) or spy_last
            spy_low = spy_quote.get("low", spy_last) or spy_last
            if spy_low > 0:
                first_30min_range_pct = ((spy_high - spy_low) / spy_low) * 100
            notes.append(f"First 30min: {first_30min_trend} (from open: {move_from_open:+.2f}%, range: {first_30min_range_pct:.2f}%)")
    except Exception as e:
        notes.append(f"SPY data error: {e}")
    # 4. News bias
    news_bias = "NEUTRAL"
    if len(stock_longs) > len(stock_shorts) + 2:
        news_bias = "BULLISH"
    elif len(stock_shorts) > len(stock_longs) + 2:
        news_bias = "BEARISH"
    # 5. Overall assessment
    bull_score = 0
    bear_score = 0
    if fear_greed_value < 25:
        bear_score += 2
        # But extreme fear = contrarian long opportunity
        if fear_greed_value < 15:
            bull_score += 1  # contrarian
            notes.append("Extreme fear → contrarian long opportunity")
    elif fear_greed_value > 75:
        bull_score += 2
        if fear_greed_value > 85:
            bear_score += 1  # contrarian
            notes.append("Extreme greed → contrarian short opportunity")
    if gap_direction == "UP":
        bull_score += 1
    elif gap_direction == "DOWN":
        bear_score += 1
    if first_30min_trend == "BULLISH":
        bull_score += 2
    elif first_30min_trend == "BEARISH":
        bear_score += 2
    if news_bias == "BULLISH":
        bull_score += 1
    elif news_bias == "BEARISH":
        bear_score += 1
    if market_mode == "LIGHT_MODE":
        bear_score += 1
    elif market_mode == "EXTREME_MODE":
        bear_score += 2
    total = bull_score + bear_score
    confidence = abs(bull_score - bear_score) / max(total, 1) * 100
    if fear_greed_value < 15:
        overall_bias = "EXTREME_FEAR"
    elif fear_greed_value > 85:
        overall_bias = "EXTREME_GREED"
    elif bull_score > bear_score + 1:
        overall_bias = "BULLISH"
    elif bear_score > bull_score + 1:
        overall_bias = "BEARISH"
    else:
        overall_bias = "NEUTRAL"
    notes.append(f"Overall: {overall_bias} (bull={bull_score} bear={bear_score} conf={confidence:.0f}%)")
    return MarketAssessment(fear_greed_value=fear_greed_value, fear_greed_class=fear_greed_class, market_mode=market_mode, market_index=market_index, gap_direction=gap_direction, gap_pct=gap_pct, first_30min_trend=first_30min_trend, first_30min_range_pct=first_30min_range_pct, news_bias=news_bias, news_stock_longs=stock_longs, news_stock_shorts=stock_shorts, overall_bias=overall_bias, confidence=confidence, notes=notes)


# ── Position Exposure Calculator ─────────────────────────────────────────────

async def get_options_exposure(client: TradierAPIClient, config=None) -> Tuple[float, List[Dict]]:
    """Calculate total options exposure (cost basis) and return positions."""
    positions = await get_option_positions(client, config=config)
    total_exposure = 0.0
    for pos in positions:
        cost = abs(pos.get("cost_basis", 0) or 0)
        total_exposure += cost
    return total_exposure, positions


# ── Decision Engine ──────────────────────────────────────────────────────────

@dataclass
class TradeDecision:
    action: str  # "BUY", "SELL", "HOLD", "SKIP"
    symbol: str
    option_type: str
    strike: float
    expiration: str
    qty: int
    limit_price: float
    budget_used: float
    reason: str
    confidence: float
    signal_score: int
    occ_symbol: str


def make_decisions(market: MarketAssessment, scan_results: Dict, existing_positions: List[Dict], current_exposure: float, max_per_order: float, max_total: float, diversification: PortfolioDiversification = None, config: TradierConfig = None) -> List[TradeDecision]:
    """Core decision logic: what to buy, sell, or hold. Diversification-aware."""
    decisions = []
    # EMERGENCY_BRAKE HALT (2026-04-26): if tradier_emergency_brake fired a Tier-4
    # halt, refuse all new options entries until manual reset.
    _halted, _halt_info = _emergency_brake_halted()
    if _halted:
        _hi = _halt_info or {}
        logger.critical(f"[EMERGENCY_BRAKE_HALT] make_decisions REFUSED — halt.flag present: {_hi}")
        decisions.append(TradeDecision(action="SKIP", symbol="ALL", option_type="", strike=0, expiration="", qty=0, limit_price=0, budget_used=0, reason=f"EMERGENCY_BRAKE_HALT({_hi.get('reason','?')[:60]})", confidence=0, signal_score=0, occ_symbol=""))
        return decisions
    # OPENING_BUFFER_NO_TRADE (2026-04-26): block ALL options buys in first 30m
    # after open. Wait for VWAP + first-30m data before deciding anything.
    _in_buf, _mins_open = _in_opening_buffer(config)
    if _in_buf:
        logger.warning(f"[OPENING_BUFFER_NO_TRADE] {_mins_open:.0f}m<30m — make_decisions returning empty (waiting for first-30m data)")
        decisions.append(TradeDecision(action="SKIP", symbol="ALL", option_type="", strike=0, expiration="", qty=0, limit_price=0, budget_used=0, reason=f"OPENING_BUFFER_NO_TRADE({_mins_open:.0f}m<30m_no_data)", confidence=0, signal_score=0, occ_symbol=""))
        return decisions
    # Use diversification-adjusted cap if available
    if diversification:
        effective_cap = diversification.effective_cap
        max_total = max(max_total, effective_cap)  # use whichever is higher
    div_guidance = get_diversification_guidance(diversification, config) if diversification and config else {}
    available_budget = max_total - current_exposure
    # ── ALLOWLIST + SANITY GATES (added 2026-04-22 after JNJ/ABT bypass) ──
    # This path (run_agent → make_decisions) previously did NOT check the trb_long/short
    # allowlist, so the 14:05 UTC cron bought calls on non-allowlisted downtrending
    # stocks. These loads mirror what _daily_find_opportunities has always done.
    _ma_call_allowed, _ma_put_allowed = _load_allowed_symbols(config) if config else (set(), set())
    _ma_ind_map = {}
    if config is not None:
        try:
            _ma_ind_path = config.DATA_DIR / "tradier_indicators_latest.json"
            if _ma_ind_path.exists():
                with open(_ma_ind_path) as _f:
                    _ma_ind_map = json.load(_f)
        except Exception:
            _ma_ind_map = {}
    _ma_min_abs_delta = float(getattr(config, "OPTIONS_BUY_MIN_ABS_DELTA", 0.35) or 0.35) if config else 0.35
    _ma_max_otm_pct = float(getattr(config, "OPTIONS_BUY_MAX_OTM_PCT", 3.0) or 3.0) if config else 3.0
    _ma_require_d_align = bool(getattr(config, "OPTIONS_BUY_REQUIRE_D_ALIGN", True)) if config else True
    _ma_min_dte = int(getattr(config, "OPTIONS_BUY_MIN_DTE", 60) or 60) if config else 60
    _ma_pref_dte = int(getattr(config, "OPTIONS_BUY_PREFERRED_DTE", 90) or 90) if config else 90
    # ── Market direction ratio (bull/bear scenario awareness) ──
    _mdr_min = getattr(config, "OPTIONS_MARKET_RATIO_MIN", 0.25) if config else 0.25
    _mdr_max = getattr(config, "OPTIONS_MARKET_RATIO_MAX", 0.75) if config else 0.75
    _max_contracts = getattr(config, "OPTIONS_MAX_CONTRACTS_PER_ORDER", MAX_CONTRACTS_PER_ORDER) if config else MAX_CONTRACTS_PER_ORDER
    _bull_exp, _bear_exp, _bull_frac = compute_market_direction_ratio(existing_positions, config) if config else (0.0, 0.0, 0.5)
    # ── STRICT call/put ratio: hard gate, not soft halving ──
    # Compute current call/put cost from existing positions (spread strategies excluded)
    # so each new BUY decision can be vetoed if it would push the book past the cap.
    _running_call_cost, _running_put_cost = _split_positions_by_side(existing_positions)
    _cp_status = _ratio_status(_running_call_cost, _running_put_cost)
    _cp_under_side = _cp_status["side_needed"]  # 'call', 'put', or 'balanced'
    if _cp_under_side != "balanced":
        logger.info(f"CALL/PUT RATIO OUT OF BAND: calls {_cp_status['call_frac']:.0%} / puts {_cp_status['put_frac']:.0%} — only adding {_cp_under_side.upper()}s until rebalanced")
    # ── SELL decisions first (free up budget) ──
    for pos in existing_positions:
        parsed = parse_occ_symbol(pos.get("occ_symbol", "") or pos.get("symbol", ""))
        if not parsed:
            continue
        # Simple sell triggers based on market regime
        cost = abs(pos.get("cost_basis", 0) or 0)
        qty = abs(pos.get("quantity", 0))
        if qty <= 0:
            continue
        symbol = parsed["symbol"]
        option_type = parsed["option_type"]
        # Gap against position: only sell short-DTE (<21d) or massive gaps (>2%) for longer-dated
        exp_dt_gap = datetime.strptime(parsed["expiration"], "%Y-%m-%d")
        pos_dte_gap = max(1, (exp_dt_gap - datetime.now()).days)
        gap_sell_threshold = -1.0 if pos_dte_gap < 21 else -2.0
        if option_type == "call" and market.gap_direction == "DOWN" and market.gap_pct < gap_sell_threshold:
            decisions.append(TradeDecision(action="SELL", symbol=symbol, option_type=option_type, strike=parsed["strike"], expiration=parsed["expiration"], qty=qty, limit_price=0, budget_used=-cost, reason=f"Gap down {market.gap_pct:.1f}% against long call ({pos_dte_gap}d DTE)", confidence=70, signal_score=0, occ_symbol=pos.get("occ_symbol", pos.get("symbol", ""))))
        elif option_type == "put" and market.gap_direction == "UP" and market.gap_pct > abs(gap_sell_threshold):
            decisions.append(TradeDecision(action="SELL", symbol=symbol, option_type=option_type, strike=parsed["strike"], expiration=parsed["expiration"], qty=qty, limit_price=0, budget_used=-cost, reason=f"Gap up {market.gap_pct:.1f}% against long put ({pos_dte_gap}d DTE)", confidence=70, signal_score=0, occ_symbol=pos.get("occ_symbol", pos.get("symbol", ""))))
        # First 30min bearish + long calls = consider selling (SHORT DTE ONLY — don't sell 30+ DTE on intraday noise)
        exp_dt = datetime.strptime(parsed["expiration"], "%Y-%m-%d")
        pos_dte = max(1, (exp_dt - datetime.now()).days)
        if option_type == "call" and market.first_30min_trend == "BEARISH" and market.first_30min_range_pct > 1.5 and pos_dte < 21:
            decisions.append(TradeDecision(action="SELL", symbol=symbol, option_type=option_type, strike=parsed["strike"], expiration=parsed["expiration"], qty=qty, limit_price=0, budget_used=-cost, reason=f"First 30min bearish ({market.first_30min_range_pct:.1f}% range) against short-DTE ({pos_dte}d) call", confidence=60, signal_score=0, occ_symbol=pos.get("occ_symbol", pos.get("symbol", ""))))
    # Update available budget after sells
    for d in decisions:
        if d.action == "SELL":
            available_budget -= d.budget_used  # budget_used is negative for sells, so this adds
    # ── BUY decisions ──
    if available_budget < MIN_ORDER_SIZE:
        logger.info(f"No budget for new positions (available=${available_budget:.2f}, max=${max_total:.2f})")
        return decisions
    # Don't buy in extreme choppy conditions
    if market.first_30min_range_pct > 2.5 and market.first_30min_trend == "CHOPPY":
        logger.info(f"Skipping buys — first 30min too choppy (range={market.first_30min_range_pct:.1f}%)")
        decisions.append(TradeDecision(action="SKIP", symbol="ALL", option_type="", strike=0, expiration="", qty=0, limit_price=0, budget_used=0, reason=f"First 30min too choppy ({market.first_30min_range_pct:.1f}% range)", confidence=0, signal_score=0, occ_symbol=""))
        return decisions
    outliers = scan_results.get("outliers", [])
    if not outliers:
        logger.info("No scanner outliers to trade")
        return decisions
    # Filter outliers by market alignment + diversification
    sector_map = config.SECTOR_MAP if config else {}
    group_map = config.SECTOR_GROUPS if config else {}
    blocked_sectors = set(div_guidance.get("blocked_sectors", []))
    blocked_symbols = set(div_guidance.get("blocked_symbols", []))
    needs_puts = div_guidance.get("needs_puts", False)
    preferred_groups = set(div_guidance.get("preferred_groups", []))
    preferred_sectors = set(div_guidance.get("preferred_sectors", []))
    filtered = []
    for o in outliers:
        direction = o.get("direction", "")
        score = o.get("score", 0)
        otype = o.get("type", "")
        rec = o.get("recommendation", "")
        symbol = o.get("symbol", "")
        # Only buy recommendations
        if "BUY" not in rec:
            continue
        # ── Allowlist enforcement: calls only on trb_long, puts only on trb_short ──
        if otype == "call" and _ma_call_allowed and symbol not in _ma_call_allowed:
            logger.info(f"ALLOWLIST skip CALL {symbol} — not in symbols_trb_long.json")
            continue
        if otype == "put" and _ma_put_allowed and symbol not in _ma_put_allowed:
            logger.info(f"ALLOWLIST skip PUT {symbol} — not in symbols_trb_short.json")
            continue
        # ── DTE floor: ≥2 months out, prefer 3+ months (user rule 2026-04-22) ──
        _o_dte = int(o.get("dte", 0) or 0)
        if _o_dte < _ma_min_dte:
            logger.info(f"DTE_FLOOR skip {otype.upper()} {symbol} — dte={_o_dte}<{_ma_min_dte}")
            continue
        if _o_dte >= _ma_pref_dte:
            score += 15  # bonus for 3+ months out
        # ── Delta floor: no lottery tickets ──
        _o_delta = float(o.get("delta", 0) or 0)
        if abs(_o_delta) < _ma_min_abs_delta:
            logger.info(f"DELTA_FLOOR skip {otype.upper()} {symbol} — |delta|={abs(_o_delta):.2f}<{_ma_min_abs_delta:.2f}")
            continue
        # ── Moneyness cap: keep strikes close to money ──
        _o_strike = float(o.get("strike", 0) or 0)
        _ind_sym = _ma_ind_map.get(symbol, {}) if _ma_ind_map else {}
        _o_und = float(_ind_sym.get("current_price", 0) or _ind_sym.get("mark_price", 0) or 0)
        if _o_und > 0 and _o_strike > 0:
            if otype == "call" and _o_strike > _o_und * (1 + _ma_max_otm_pct / 100.0):
                logger.info(f"OTM_CAP skip CALL {symbol} strike={_o_strike:.2f} > underlying={_o_und:.2f}*(1+{_ma_max_otm_pct:.1f}%)")
                continue
            if otype == "put" and _o_strike < _o_und * (1 - _ma_max_otm_pct / 100.0):
                logger.info(f"OTM_CAP skip PUT {symbol} strike={_o_strike:.2f} < underlying={_o_und:.2f}*(1-{_ma_max_otm_pct:.1f}%)")
                continue
        # ── Daily-trend alignment: no calls into bear D, no puts into bull D ──
        if _ma_require_d_align and _ind_sym:
            _wt_d = str(_ind_sym.get("wt_cross_D", "") or "")
            _mom_d = str(_ind_sym.get("wt_momentum_state_D", "") or "")
            if otype == "call" and (_wt_d == "BEAR" or _mom_d in ("IMPULSE_DOWN", "EXHAUST_DOWN")):
                logger.info(f"D_TREND_BLOCK skip CALL {symbol} — wt_D={_wt_d} mom_D={_mom_d}")
                continue
            if otype == "put" and (_wt_d == "BULL" or _mom_d in ("IMPULSE_UP", "EXHAUST_UP")):
                logger.info(f"D_TREND_BLOCK skip PUT {symbol} — wt_D={_wt_d} mom_D={_mom_d}")
                continue
        # ── WT/DC MULTI-TF ENTRY GATE (2026-04-26 owner directive) ─────────────
        # See _wt_dc_options_entry_gate docstring. Closes the "buy any cheap option"
        # leak. Wired in BOTH make_decisions (here) AND _daily_find_opportunities.
        _gate_ok, _gate_reason = _wt_dc_options_entry_gate(symbol, otype == "call", _ma_ind_map, config)
        if not _gate_ok:
            logger.info(f"WT_DC_GATE skip {otype.upper()} {symbol} — {_gate_reason}")
            continue
        # Bonus for high-score passes (encourages picking the strongest signals)
        if "score=" in _gate_reason:
            try:
                _gate_score_val = float(_gate_reason.split("score=")[1].split("/")[0])
                _gate_min = float(getattr(config, "OPTIONS_BUY_MIN_WT_DC_SCORE", 70.0))
                score += min(20, int((_gate_score_val - _gate_min) / 2))
            except Exception:
                pass
        # Don't trade blocked symbols/sectors (concentration limits)
        if symbol in blocked_symbols:
            continue
        sym_sector = sector_map.get(symbol, "UNKNOWN")
        sym_group = group_map.get(sym_sector, "OTHER")
        if sym_sector in blocked_sectors:
            continue
        # Market alignment filter
        if direction == "LONG" and otype == "call":
            if market.overall_bias == "BEARISH" and market.confidence > 50:
                continue
            if market.gap_direction == "DOWN" and market.gap_pct < -0.5 and market.first_30min_trend != "BULLISH":
                continue
            if market.overall_bias == "BULLISH" or market.first_30min_trend == "BULLISH":
                score += 10
        elif direction == "SHORT" and otype == "put":
            if market.overall_bias == "BULLISH" and market.confidence > 50:
                continue
            if market.gap_direction == "UP" and market.gap_pct > 0.5 and market.first_30min_trend != "BEARISH":
                continue
            if market.overall_bias == "BEARISH" or market.first_30min_trend == "BEARISH":
                score += 10
        # ── Diversification scoring ──
        # Big bonus for puts when portfolio needs hedging
        if needs_puts and otype == "put":
            score += 25
        # Bonus for new sector groups (diversification)
        if sym_group in preferred_groups:
            score += 20
        # Bonus for sectors that would create hedged pairs
        if sym_sector in preferred_sectors:
            score += 15
        # Bonus for being in a different group than current holdings
        if diversification and sym_group not in diversification.positions_by_group:
            score += 15
        # News alignment bonus
        if symbol in market.news_stock_longs and direction == "LONG":
            score += 15
        if symbol in market.news_stock_shorts and direction == "SHORT":
            score += 15
        # Don't trade existing symbols (avoid doubling up)
        existing_symbols = {parse_occ_symbol(p.get("occ_symbol", "") or p.get("symbol", "")).get("symbol", "") for p in existing_positions if parse_occ_symbol(p.get("occ_symbol", "") or p.get("symbol", ""))}
        if symbol in existing_symbols:
            continue
        filtered.append({**o, "adjusted_score": score, "sector": sym_sector, "group": sym_group})
    # Sort with strict ratio bias: if the call/put ratio is out of band, the
    # under-side ALWAYS sorts first regardless of adjusted_score. Within each
    # side, score still wins. Falls back to needs_puts/score otherwise.
    if _cp_under_side in ("call", "put"):
        filtered.sort(key=lambda x: (x.get("type") == _cp_under_side, x.get("adjusted_score", 0)), reverse=True)
    elif needs_puts:
        filtered.sort(key=lambda x: (x.get("type") == "put", x.get("adjusted_score", 0)), reverse=True)
    else:
        filtered.sort(key=lambda x: x.get("adjusted_score", 0), reverse=True)
    # Select top picks within budget — enforce diversification per-order
    budget_remaining = available_budget
    used_symbols = set()
    used_sectors = dict(diversification.positions_by_sector) if diversification else {}
    used_groups = dict(diversification.positions_by_group) if diversification else {}
    running_total = diversification.total_exposure if diversification else current_exposure
    for o in filtered:
        if budget_remaining < MIN_ORDER_SIZE:
            break
        symbol = o.get("symbol", "")
        if symbol in used_symbols:
            continue
        mid = o.get("mid", 0)
        if mid <= 0:
            continue
        sym_sector = o.get("sector", "UNKNOWN")
        sym_group = o.get("group", "OTHER")
        # Check sector concentration would-be
        if config and running_total > 0:
            sector_exp = used_sectors.get(sym_sector, 0)
            if (sector_exp + PREFERRED_PER_ORDER) / (running_total + PREFERRED_PER_ORDER) > config.OPTIONS_MAX_PER_SECTOR:
                continue  # would over-concentrate this sector
            group_exp = used_groups.get(sym_group, 0)
            if (group_exp + PREFERRED_PER_ORDER) / (running_total + PREFERRED_PER_ORDER) > config.OPTIONS_MAX_PER_GROUP:
                continue  # would over-concentrate this group
        # Calculate position size
        dte = o.get("dte", 30)
        order_budget = min(max_per_order, budget_remaining)
        if order_budget > PREFERRED_PER_ORDER and len(filtered) > 3:
            order_budget = PREFERRED_PER_ORDER
        if dte < 14:
            order_budget = min(order_budget, 500)
        # ── Market direction ratio gate ──
        # A CALL on a bear_scenario symbol is actually a bearish market bet.
        _opt_type = o.get("type", "call")
        _is_bear_sym = is_bear_scenario(symbol, config) if config else False
        _is_bull_bet = (_opt_type == "call" and not _is_bear_sym) or (_opt_type == "put" and _is_bear_sym)
        _ratio_tag = ""
        if _is_bull_bet and _bull_frac > _mdr_max:
            order_budget = order_budget * 0.5
            _ratio_tag = f"|RATIO_CUT_BULL(frac={_bull_frac:.0%})"
        elif not _is_bull_bet and _bull_frac < _mdr_min:
            order_budget = order_budget * 0.5
            _ratio_tag = f"|RATIO_CUT_BEAR(frac={_bull_frac:.0%})"
        elif _is_bull_bet and _bull_frac < _mdr_min:
            order_budget = min(order_budget * 1.3, max_per_order)
            _ratio_tag = f"|RATIO_BOOST_BULL(frac={_bull_frac:.0%})"
        elif not _is_bull_bet and _bull_frac > _mdr_max:
            order_budget = min(order_budget * 1.3, max_per_order)
            _ratio_tag = f"|RATIO_BOOST_BEAR(frac={_bull_frac:.0%})"
        contract_cost = mid * 100
        if contract_cost <= 0:
            continue
        # ── Budget + qty rules (2026-04-23) ──
        # Expensive (>$9/share = >$900/contract): ALWAYS exactly 1 contract.
        #   → Order can be $2,000+ for a $20 option. That's intentional.
        # Cheap (≤$9/share): buy floor($800 / contract_cost) contracts, capped at $800 total.
        #   → A $3 option gets qty=2 ($600), a $8 option gets qty=1 ($800).
        _max_single_price = float(getattr(config, "OPTIONS_MAX_SINGLE_CONTRACT_PRICE", 9.0)) if config else 9.0
        _cheap_budget = float(getattr(config, "OPTIONS_MAX_ORDER_BUDGET", 800.0)) if config else 800.0
        if mid > _max_single_price:
            qty = 1
            total_cost = contract_cost  # full contract cost — may exceed $800, that's fine
        else:
            qty = min(_max_contracts, max(1, int(_cheap_budget / contract_cost)))
            total_cost = qty * contract_cost
        if total_cost > budget_remaining or total_cost < MIN_ORDER_SIZE:
            continue
        # ── HARD CALL/PUT RATIO GATE ──
        # Veto any pick that would push the book past MAX_CALL_RATIO/MAX_PUT_RATIO.
        # Try shrinking qty down to 1 contract first; if even 1 contract violates,
        # skip the pick entirely. Spread strategy symbols don't go through this path.
        _opt_type_for_gate = o.get("type", "call")
        if symbol not in SPREAD_EXCLUDED_SYMBOLS and _ratio_would_violate(_running_call_cost, _running_put_cost, _opt_type_for_gate, total_cost):
            # Try shrinking to a single contract
            single_cost = contract_cost
            if qty > 1 and not _ratio_would_violate(_running_call_cost, _running_put_cost, _opt_type_for_gate, single_cost):
                logger.info(f"RATIO_GATE shrink {symbol} {_opt_type_for_gate.upper()}: qty {qty}->1 to stay under {MAX_CALL_RATIO:.0%} cap")
                qty = 1
                total_cost = single_cost
            else:
                logger.info(f"RATIO_GATE veto {symbol} {_opt_type_for_gate.upper()}: would push ratio past cap (calls=${_running_call_cost:.0f}, puts=${_running_put_cost:.0f}, +{_opt_type_for_gate}=${total_cost:.0f})")
                continue
        occ = build_occ_symbol(symbol, o.get("expiration", ""), o.get("type", "call"), o.get("strike", 0))
        bid = o.get("bid", mid * 0.95)
        ask = o.get("ask", mid * 1.05)
        start_price = round(bid, 2) if bid > 0 else round(mid * 0.95, 2)
        _scenario_tag = f" [{'BEAR_SYM' if _is_bear_sym else 'BULL_SYM'}→{'bearish' if not _is_bull_bet else 'bullish'}_market]"
        div_tag = f" [sector={sym_sector}, group={sym_group}]{_scenario_tag}{_ratio_tag}"
        if needs_puts and o.get("type") == "put":
            div_tag += " [HEDGE]"
        decisions.append(TradeDecision(action="BUY", symbol=symbol, option_type=o.get("type", "call"), strike=o.get("strike", 0), expiration=o.get("expiration", ""), qty=qty, limit_price=start_price, budget_used=total_cost, reason=f"{o.get('recommendation', '')} — IV dev {o.get('iv_deviation_pct', 0):+.1f}%, edge {o.get('edge_pct', 0):+.1f}%, delta {o.get('delta', 0):+.3f}, {dte}d DTE, market={market.overall_bias}{div_tag}", confidence=min(o.get("adjusted_score", 0), 100), signal_score=o.get("score", 0), occ_symbol=occ))
        budget_remaining -= total_cost
        running_total += total_cost
        used_symbols.add(symbol)
        used_sectors[sym_sector] = used_sectors.get(sym_sector, 0) + total_cost
        used_groups[sym_group] = used_groups.get(sym_group, 0) + total_cost
        # Update running market direction totals for subsequent picks
        if _is_bull_bet:
            _bull_exp += total_cost
        else:
            _bear_exp += total_cost
        _total_mdr = _bull_exp + _bear_exp
        _bull_frac = _bull_exp / _total_mdr if _total_mdr > 0 else 0.5
        # Update running call/put cost for the next pick's ratio gate
        if symbol not in SPREAD_EXCLUDED_SYMBOLS:
            if _opt_type_for_gate == "put":
                _running_put_cost += total_cost
            else:
                _running_call_cost += total_cost
            _cp_status = _ratio_status(_running_call_cost, _running_put_cost)
            _cp_under_side = _cp_status["side_needed"]
    return decisions


# ── Execution ────────────────────────────────────────────────────────────────

async def execute_decisions(client: TradierAPIClient, decisions: List[TradeDecision], dry_run: bool = False, config=None) -> List[Dict]:
    """Execute all trade decisions."""
    results = []
    if not dry_run and not bool(getattr(config or TradierConfig(), "OPTIONS_LIVE_TRADING_ENABLED", False)):
        for d in decisions:
            if d.action in ("BUY", "SELL"):
                logger.warning("[PAPER_ONLY_BLOCK] decision held: %s %s", d.action, d.occ_symbol or d.symbol)
                results.append({"action": d.action, "symbol": d.symbol, "occ": d.occ_symbol, "qty": d.qty, "status": "paper_only_blocked"})
        return results
    for d in decisions:
        if d.action == "SKIP":
            logger.info(f"SKIP: {d.reason}")
            results.append({"action": "SKIP", "reason": d.reason})
            continue
        if d.action == "HOLD":
            results.append({"action": "HOLD", "symbol": d.symbol, "reason": d.reason})
            continue
        if dry_run:
            print(f"  \033[93m[DRY RUN]\033[0m {d.action} {d.qty}x {d.occ_symbol} @ ${d.limit_price:.2f} — {d.reason}")
            results.append({"action": d.action, "symbol": d.symbol, "occ": d.occ_symbol, "qty": d.qty, "price": d.limit_price, "dry_run": True})
            continue
        if d.action == "BUY":
            side = "buy_to_open"
            # Fetch fresh bid/ask for smart fill
            opt_quote_res = await client._request("GET", "/markets/quotes", params={"symbols": d.occ_symbol}, use_data_context=True)
            bid, ask = d.limit_price, d.limit_price * 1.05
            if opt_quote_res and "quotes" in opt_quote_res and "quote" in opt_quote_res["quotes"]:
                q = opt_quote_res["quotes"]["quote"]
                if isinstance(q, list):
                    q = q[0] if q else {}
                bid = q.get("bid", bid) or bid
                ask = q.get("ask", ask) or ask
            print(f"\n  \033[92mBUYING\033[0m {d.qty}x {d.occ_symbol} (bid=${bid:.2f} ask=${ask:.2f})")
            print(f"    Reason: {d.reason}")
            logger.info(f"BUYING {d.qty}x {d.occ_symbol} budget=${d.budget_used:.2f} — {d.reason}")
            res = await smart_fill_option(client, d.symbol, d.occ_symbol, side, d.qty, d.limit_price, bid, ask, max_walk_steps=5, walk_interval=120)
            results.append({"action": "BUY", "symbol": d.symbol, "occ": d.occ_symbol, "qty": d.qty, "result": res})
        elif d.action == "SELL":
            # Fetch fresh bid/ask
            opt_quote_res = await client._request("GET", "/markets/quotes", params={"symbols": d.occ_symbol}, use_data_context=True)
            bid, ask = 0, 0
            if opt_quote_res and "quotes" in opt_quote_res and "quote" in opt_quote_res["quotes"]:
                q = opt_quote_res["quotes"]["quote"]
                if isinstance(q, list):
                    q = q[0] if q else {}
                bid = q.get("bid", 0) or 0
                ask = q.get("ask", 0) or 0
            if bid <= 0 and ask <= 0:
                print(f"  \033[91mSKIP SELL\033[0m {d.occ_symbol} — no bid/ask available")
                continue
            print(f"\n  \033[91mSELLING\033[0m {d.qty}x {d.occ_symbol} (bid=${bid:.2f} ask=${ask:.2f})")
            print(f"    Reason: {d.reason}")
            logger.info(f"SELLING {d.qty}x {d.occ_symbol} — {d.reason}")
            res = await smart_fill_option(client, d.symbol, d.occ_symbol, "sell_to_close", d.qty, ask, bid, ask, max_walk_steps=3, walk_interval=60, config=config)
            results.append({"action": "SELL", "symbol": d.symbol, "occ": d.occ_symbol, "qty": d.qty, "result": res})
        await asyncio.sleep(1)
    return results


# ── Main ─────────────────────────────────────────────────────────────────────

async def run_agent(args):
    config = TradierConfig()
    _apply_runtime_overrides(config)
    account_key = args.account or "trb"
    dry_run = args.dry_run
    max_per = args.budget or MAX_PER_ORDER
    max_total = args.max_total or MAX_TOTAL_OPTIONS
    now = datetime.utcnow()
    logger.info(f"=== OPTIONS AGENT START === {now.strftime('%Y-%m-%d %H:%M:%S')} UTC | account={account_key} | dry_run={dry_run} | budget=${max_per:.0f}/order, ${max_total:.0f} total")
    print(f"\n{'='*90}")
    print(f"  OPTIONS TRADING AGENT — {now.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Account: {account_key} | Max/order: ${max_per:.0f} | Max total: ${max_total:.0f}")
    if dry_run:
        print(f"  \033[93m*** DRY RUN — no orders will be placed ***\033[0m")
    print(f"{'='*90}")
    client = TradierAPIClient(config, account_key=account_key)
    await client.connect()
    try:
        # Step 1: Market assessment
        print(f"\n  Step 1: Market Assessment")
        print(f"  {'─'*86}")
        market = await assess_market(client, config)
        for note in market.notes:
            print(f"    {note}")
        # Step 2: Load scanner results
        print(f"\n  Step 2: Scanner Results")
        print(f"  {'─'*86}")
        scan_file = config.DATA_DIR / "options_analysis_latest.json"
        scan_results = {}
        if scan_file.exists():
            with open(scan_file) as f:
                scan_results = json.load(f)
            scan_age = (datetime.now() - datetime.fromisoformat(scan_results.get("timestamp", datetime.now().isoformat()))).total_seconds()
            n_outliers = len(scan_results.get("outliers", []))
            n_spreads = len(scan_results.get("spreads", []))
            print(f"    Loaded: {n_outliers} outliers, {n_spreads} spreads (age: {scan_age/60:.0f}min)")
            if scan_age > 3600:
                print(f"    \033[93mWARNING: Scanner results are {scan_age/3600:.1f}h old. Running fresh scan...\033[0m")
                # Run fresh scan inline
                ind_file = config.DATA_DIR / "tradier_indicators_latest.json"
                if ind_file.exists():
                    with open(ind_file) as f:
                        indicators = json.load(f)
                    rank_file = config.DATA_DIR / "tradier_rankings.json"
                    rankings = {}
                    if rank_file.exists():
                        with open(rank_file) as f:
                            rankings = json.load(f)
                    signals = detect_directional_signals(indicators, rankings)[:10]
                    all_outliers = []
                    all_spreads = []
                    for sig in signals:
                        exps = await fetch_expirations(client, sig.symbol)
                        if not exps:
                            continue
                        now_dt = datetime.now()
                        filtered = [(e, max(1, (datetime.strptime(e, "%Y-%m-%d") - now_dt).days)) for e in exps if 3 <= (datetime.strptime(e, "%Y-%m-%d") - now_dt).days <= 180]
                        buckets = [(3, 14), (20, 50), (55, 100)]
                        priority = []
                        for lo, hi in buckets:
                            b = [(e, d) for e, d in filtered if lo <= d <= hi]
                            if b:
                                priority.append(b[0])
                        for exp, dte in (priority or filtered[:3]):
                            chain = await fetch_option_chain(client, sig.symbol, exp)
                            if chain:
                                outs, sprs = analyze_chain_for_outliers(sig, chain, exp)
                                all_outliers.extend(outs[:5])
                                all_spreads.extend(sprs[:3])
                            await asyncio.sleep(0.15)
                    scan_results = {"timestamp": datetime.now().isoformat(), "outliers": [{"symbol": o.symbol, "direction": o.direction, "expiration": o.expiration, "strike": o.strike, "type": o.option_type, "recommendation": o.recommendation, "bid": o.bid, "ask": o.ask, "mid": o.mid, "iv_deviation_pct": o.iv_deviation_pct, "edge_pct": o.edge_pct, "delta": o.greeks["delta"], "dte": o.dte, "volume": o.volume, "open_interest": o.open_interest, "score": o.score} for o in sorted(all_outliers, key=lambda x: x.score, reverse=True)[:20]], "spreads": []}
                    # Save fresh results
                    with open(scan_file, "w") as f:
                        json.dump(scan_results, f, indent=2, default=str)
                    print(f"    Fresh scan: {len(scan_results['outliers'])} outliers found")
        else:
            print(f"    \033[91mNo scanner results found. Running fresh scan...\033[0m")
            # Same fresh scan as above
            ind_file = config.DATA_DIR / "tradier_indicators_latest.json"
            if ind_file.exists():
                with open(ind_file) as f:
                    indicators = json.load(f)
                rank_file = config.DATA_DIR / "tradier_rankings.json"
                rankings = {}
                if rank_file.exists():
                    with open(rank_file) as f:
                        rankings = json.load(f)
                signals = detect_directional_signals(indicators, rankings)[:10]
                all_outliers = []
                for sig in signals:
                    exps = await fetch_expirations(client, sig.symbol)
                    if not exps:
                        continue
                    now_dt = datetime.now()
                    filtered = [(e, max(1, (datetime.strptime(e, "%Y-%m-%d") - now_dt).days)) for e in exps if 3 <= (datetime.strptime(e, "%Y-%m-%d") - now_dt).days <= 180]
                    for exp, dte in filtered[:3]:
                        chain = await fetch_option_chain(client, sig.symbol, exp)
                        if chain:
                            outs, _ = analyze_chain_for_outliers(sig, chain, exp)
                            all_outliers.extend(outs[:5])
                        await asyncio.sleep(0.15)
                scan_results = {"timestamp": datetime.now().isoformat(), "outliers": [{"symbol": o.symbol, "direction": o.direction, "expiration": o.expiration, "strike": o.strike, "type": o.option_type, "recommendation": o.recommendation, "bid": o.bid, "ask": o.ask, "mid": o.mid, "iv_deviation_pct": o.iv_deviation_pct, "edge_pct": o.edge_pct, "delta": o.greeks["delta"], "dte": o.dte, "volume": o.volume, "open_interest": o.open_interest, "score": o.score} for o in sorted(all_outliers, key=lambda x: x.score, reverse=True)[:20]], "spreads": []}
                with open(scan_file, "w") as f:
                    json.dump(scan_results, f, indent=2, default=str)
                print(f"    Fresh scan: {len(scan_results['outliers'])} outliers")
        # Step 3: Current exposure
        print(f"\n  Step 3: Current Options Exposure & Diversification")
        print(f"  {'─'*86}")
        exposure, positions = await get_options_exposure(client, config=config)
        div = analyze_diversification(positions, config)
        _bull_e, _bear_e, _bull_f = compute_market_direction_ratio(positions, config)
        _mdr_min_disp = getattr(config, "OPTIONS_MARKET_RATIO_MIN", 0.25)
        _mdr_max_disp = getattr(config, "OPTIONS_MARKET_RATIO_MAX", 0.75)
        _mdr_color = "\033[92m" if _mdr_min_disp <= _bull_f <= _mdr_max_disp else "\033[91m"
        print(f"    Total exposure: ${exposure:,.2f}")
        print(format_diversification(div))
        print(f"    Market direction: {_mdr_color}bull={_bull_f:.0%} (${_bull_e:,.0f}) bear={(1-_bull_f):.0%} (${_bear_e:,.0f})\033[0m  [target {_mdr_min_disp:.0%}–{_mdr_max_disp:.0%} bull]")
        print(f"    Open positions: {len(positions)}")
        for pos in positions:
            parsed = parse_occ_symbol(pos.get("occ_symbol", "") or pos.get("symbol", ""))
            if parsed:
                cost = abs(pos.get("cost_basis", 0) or 0)
                qty = abs(pos.get("quantity", 0))
                sec = config.SECTOR_MAP.get(parsed["symbol"], "?")
                _bear_sym = is_bear_scenario(parsed["symbol"], config)
                _is_bull_bet = (parsed["option_type"] == "call" and not _bear_sym) or (parsed["option_type"] == "put" and _bear_sym)
                _dir_tag = "→bull_mkt" if _is_bull_bet else "→bear_mkt"
                print(f"      {parsed['symbol']:6s} {parsed['option_type'].upper():4s} ${parsed['strike']:.2f} exp {parsed['expiration']} x{qty} cost=${cost:,.2f} [{sec}] {_dir_tag}")
        div_guidance = get_diversification_guidance(div, config)
        if div_guidance.get("needs_puts"):
            print(f"    \033[93mDIVERSIFICATION: Portfolio needs PUTS for hedging (ratio={div.hedge_ratio:.0%}, min={config.OPTIONS_HEDGE_RATIO_MIN:.0%})\033[0m")
        if div_guidance.get("preferred_groups"):
            print(f"    \033[93mDIVERSIFICATION: Add exposure in groups: {', '.join(div_guidance['preferred_groups'])}\033[0m")
        if div_guidance.get("blocked_sectors"):
            print(f"    \033[91mBLOCKED sectors (over-concentrated): {', '.join(div_guidance['blocked_sectors'])}\033[0m")
        # Step 4: Make decisions
        print(f"\n  Step 4: Trade Decisions")
        print(f"  {'─'*86}")
        decisions = make_decisions(market, scan_results, positions, exposure, max_per, max_total, diversification=div, config=config)
        if not decisions:
            print(f"    No trades recommended today.")
            logger.info("No trades recommended")
        else:
            for d in decisions:
                action_color = {"BUY": "\033[92m", "SELL": "\033[91m", "SKIP": "\033[93m", "HOLD": "\033[90m"}.get(d.action, "\033[0m")
                print(f"    {action_color}{d.action:4s}\033[0m {d.qty}x {d.occ_symbol or d.symbol:30s} @ ${d.limit_price:.2f}  budget=${d.budget_used:.2f}  conf={d.confidence:.0f}  {d.reason}")
        # Step 5: Execute
        if decisions and any(d.action in ("BUY", "SELL") for d in decisions):
            print(f"\n  Step 5: Execution")
            print(f"  {'─'*86}")
            results = await execute_decisions(client, decisions, dry_run, config=config)
            # Save agent report
            report = {"timestamp": datetime.now().isoformat(), "market": {"fear_greed": market.fear_greed_value, "gap": f"{market.gap_direction} {market.gap_pct:+.2f}%", "first_30min": market.first_30min_trend, "overall_bias": market.overall_bias, "mode": market.market_mode}, "exposure_before": exposure, "decisions": [{"action": d.action, "symbol": d.symbol, "occ": d.occ_symbol, "qty": d.qty, "price": d.limit_price, "budget": d.budget_used, "reason": d.reason} for d in decisions], "results": results, "dry_run": dry_run}
            report_file = config.DATA_DIR / f"options_agent_report_{now.strftime('%Y%m%d_%H%M')}.json"
            with open(report_file, "w") as f:
                json.dump(report, f, indent=2, default=str)
            print(f"\n  Report saved: {report_file}")
            logger.info(f"Agent report saved: {report_file}")
    finally:
        await client.close()
    print(f"\n{'='*90}")
    print(f"  OPTIONS AGENT COMPLETE — {datetime.utcnow().strftime('%H:%M:%S')} UTC")
    print(f"{'='*90}")


# ── Daily Cycle: afternoon analysis + GTC order placement ────────────────────
# Runs every afternoon after market close. Scans opportunities, places GTC
# buy orders at dip prices with a 7-day fill window. Replaces day-trading
# with swing-trade accumulation.

DAILY_PLAN_FILE = "options_daily_plan.json"
GTC_MAX_AGE_DAYS = 7


def _apply_runtime_overrides(config) -> dict:
    """Apply variant-graduation overrides at startup (2026-04-26 owner directive).

    Reads `data/options_shadow/live_overrides_active.json` and patches the live
    TradierConfig in-place. Overrides are written by tradier_options_shadow_scorer
    when a variant beats live for SUGGESTION_WIN_STREAK consecutive days.

    Safe behaviors:
    - Missing file → no-op (returns {}).
    - Read error → logs warning, no patch applied.
    - Unknown config keys → silently skipped (defensive).
    - Each applied override is logged with old/new value + source variant.

    Returns dict of {key: {"old": val, "new": val, "from": variant_name}}.
    Manual revert: delete or edit the JSON file. Doesn't touch config_tradier.py."""
    overrides_file = BASE_PATH / "data" / "options_shadow" / "live_overrides_active.json"
    if not overrides_file.exists():
        return {}
    try:
        data = json.loads(overrides_file.read_text())
    except Exception as e:
        logger.warning(f"[RUNTIME_OVERRIDE] read error {e} — no overrides applied")
        return {}
    applied: dict = {}
    active = data.get("active_overrides") or {}
    for key, info in active.items():
        if key == "OPTIONS_LIVE_TRADING_ENABLED":
            logger.error("[SAFETY_LOCK] refusing runtime override for OPTIONS_LIVE_TRADING_ENABLED")
            continue
        if not hasattr(config, key):
            logger.warning(f"[RUNTIME_OVERRIDE] unknown config key {key} — skipped")
            continue
        val = info.get("value") if isinstance(info, dict) else info
        src = info.get("graduated_from", "manual") if isinstance(info, dict) else "manual"
        old = getattr(config, key)
        setattr(config, key, val)
        applied[key] = {"old": old, "new": val, "from": src}
        logger.info(f"[RUNTIME_OVERRIDE] {key}: {old} → {val} (graduated from variant '{src}')")
    if applied:
        logger.info(f"[RUNTIME_OVERRIDE] applied {len(applied)} variant-graduated override(s)")
    return applied



def _load_allowed_symbols(config) -> tuple:
    """Load symbols_trb_long.json (for calls) and symbols_trb_short.json (for puts).
    These are the hand-picked best long/short candidates. Options ONLY on these.

    BLACKLIST from config_tradier is applied as a final reject. Closes the
    2026-04-22 ABT/JNJ rogue-buy bypass (OPTIONS_OVERHAUL_FRAMEWORK §0.7) where
    BLACKLIST existed but was never consulted by the options entry path —
    blacklisted symbols got bought as calls because allowlist + blacklist were
    two parallel systems that never spoke."""
    long_file = BASE_PATH / "symbols_trb_long.json"
    short_file = BASE_PATH / "symbols_trb_short.json"
    call_symbols = set()
    put_symbols = set()
    if long_file.exists():
        with open(long_file) as f:
            data = json.load(f)
        call_symbols = {s for s in data if isinstance(s, str) and len(s) <= 5}
    if short_file.exists():
        with open(short_file) as f:
            data = json.load(f)
        put_symbols = {s for s in data if isinstance(s, str) and len(s) <= 5}
    blacklist: set = set()
    try:
        bl = getattr(config, 'BLACKLIST', None) or []
        blacklist = {s.upper() for s in bl if isinstance(s, str)}
    except Exception as _bl_err:
        logger.warning(f"[BLACKLIST_GATE] read error: {_bl_err}")
    if blacklist:
        _rm_calls = call_symbols & blacklist
        _rm_puts = put_symbols & blacklist
        call_symbols -= blacklist
        put_symbols -= blacklist
        if _rm_calls or _rm_puts:
            logger.warning(f"[BLACKLIST_GATE] rejected from allowlist: calls={sorted(_rm_calls)} puts={sorted(_rm_puts)} (BLACKLIST={sorted(blacklist)})")
    return call_symbols, put_symbols


# Symbols that are part of the oil spread strategy — excluded from $7k portfolio limit
OIL_SPREAD_SYMBOLS = {"USO", "BNO"}
BTC_SPREAD_SYMBOLS = {"MSTR", "IBIT", "COIN"}
GOLD_SPREAD_SYMBOLS = {"NEM", "GLD", "GDX", "AEM", "RGLD", "WPM"}
SPREAD_EXCLUDED_SYMBOLS = OIL_SPREAD_SYMBOLS | BTC_SPREAD_SYMBOLS | GOLD_SPREAD_SYMBOLS


def _emergency_brake_halted() -> tuple:
    """Returns (is_halted, payload). Reads data/emergency_brake/halt.flag written
    by tradier_emergency_brake.py when a position breaches the -85% tier. When
    True, ALL new options entries must be blocked. Manual reset required."""
    p = BASE_PATH / "data" / "emergency_brake" / "halt.flag"
    if not p.exists():
        return False, None
    try:
        return True, json.loads(p.read_text())
    except Exception:
        return True, None


def _in_opening_buffer(config) -> tuple:
    """(is_in_buffer, mins_since_open). Mirrors tradier_manage.in_opening_buffer.
    Used to block OPTIONS opens/augments in the first 30m after market open
    until first-30m + VWAP data is meaningful (2026-04-26 owner directive)."""
    try:
        min_minutes = float(getattr(config, "OPENING_BUFFER_NO_CLOSE_MINUTES", 30.0))
    except Exception:
        min_minutes = 30.0
    if min_minutes <= 0:
        return False, 0.0
    try:
        import pytz
        now_et = datetime.now(pytz.timezone("America/New_York"))
        if now_et.weekday() >= 5:
            return False, 0.0
        open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        mins = (now_et - open_et).total_seconds() / 60.0
        if 0 <= mins < min_minutes:
            return True, mins
        return False, mins
    except Exception:
        return False, 0.0


def _wt_dc_options_entry_gate(symbol: str, is_long: bool, indicators_map: dict, config) -> tuple:
    """Multi-TF WT/DC gate for options entries (2026-04-26 owner directive).

    Runs the same wt_dc_score_entry that equity stocks use (tradier_manage.py:1367)
    against the symbol's indicator snapshot. Returns (allow: bool, reason: str).

    score_entry_multitf checks D_aligned + 4h_aligned + 1h_cross + dc_1h + k_5m
    (range 0-100, validated Sharpe 27.4 over 121 stocks 2.9yr). Threshold default
    70 = at least 3 of 5 majors aligned. Closes the "buy any cheap option" leak
    that contributed to the 2026-04-22 -20% week."""
    if not bool(getattr(config, "OPTIONS_BUY_WT_DC_GATE_ENABLED", True)):
        return True, "WT_DC_GATE_DISABLED"
    ind = (indicators_map or {}).get(symbol, {}) if isinstance(indicators_map, dict) else {}
    if not ind:
        return True, "WT_DC_GATE_NO_INDICATORS"
    try:
        score, reason = wt_dc_score_entry(ind, is_long, 0.0)
    except Exception as e:
        logger.warning(f"WT_DC_GATE {symbol}: scorer error {e} — BLOCKING")
        return False, f"WT_DC_GATE_ERROR:{e}"
    min_score = float(getattr(config, "OPTIONS_BUY_MIN_WT_DC_SCORE", 70.0))
    if score < min_score:
        return False, f"WT_DC_GATE_BLOCK score={score:.0f}<{min_score:.0f} ({reason})"
    return True, f"WT_DC_GATE_PASS score={score:.0f}/{min_score:.0f} ({reason})"


# ── STRICT CALL/PUT RATIO ENFORCEMENT ─────────────────────────────────────────
# Hard cap: neither side may exceed MAX_CALL_RATIO / MAX_PUT_RATIO of the
# total directional options book (spread strategies excluded). The cap is
# enforced on EVERY new buy across ALL paths: make_decisions, _daily_find_opportunities
# and the daily-cycle selection loop. Pending buy_to_open GTC orders are counted
# toward the side they will land on, so an open GTC consumes ratio headroom.

def _classify_occ_side(occ: str) -> str:
    """Return 'put' or 'call' from an OCC symbol."""
    parsed = parse_occ_symbol(occ) if occ and len(occ) > 5 else None
    if parsed and parsed.get("option_type") in ("call", "put"):
        return parsed["option_type"]
    # Fallback: scan the post-date portion of the OCC
    if occ and len(occ) > 6 and "P" in occ[6:]:
        return "put"
    return "call"


def _occ_underlying(occ: str) -> str:
    parsed = parse_occ_symbol(occ) if occ and len(occ) > 5 else None
    return parsed["symbol"] if parsed else (occ or "")


def _gtc_pending_buy_exposure(gtc_orders: dict) -> tuple:
    """Sum committed cost of pending buy_to_open GTC orders, split by side.
    Spread-strategy symbols (USO/BNO/MSTR/IBIT/COIN/NEM/GLD/GDX/AEM/RGLD/WPM)
    are excluded — they have their own budget and do not count toward the
    main call/put ratio book."""
    call_cost = 0.0
    put_cost = 0.0
    if not gtc_orders:
        return call_cost, put_cost
    for occ, info in gtc_orders.items():
        if info.get("side") != "buy_to_open":
            continue  # exit GTCs (sell_to_close) don't consume buy headroom
        underlying = info.get("symbol") or _occ_underlying(occ)
        if underlying in SPREAD_EXCLUDED_SYMBOLS:
            continue
        opt_type = info.get("type") or _classify_occ_side(occ)
        price = info.get("target_price") or info.get("gtc_price") or 0
        try:
            qty = int(info.get("qty", 1) or 1)
        except (TypeError, ValueError):
            qty = 1
        cost = float(price) * qty * 100
        if cost <= 0:
            continue
        if opt_type == "put":
            put_cost += cost
        else:
            call_cost += cost
    return call_cost, put_cost


def _build_live_exposure_map(positions: list, gtc_orders: dict, config) -> dict:
    """Build the CURRENT effective exposure book by symbol/sector/group across
    live positions PLUS pending buy_to_open GTC orders. Spread-strategy symbols
    excluded. Returns {by_symbol, by_sector, by_group, total, hedge_ratio,
    call_cost, put_cost, bull_exp, bear_exp, bull_fraction}."""
    sector_map = getattr(config, "SECTOR_MAP", {}) or {}
    group_map = getattr(config, "SECTOR_GROUPS", {}) or {}
    bear_set = getattr(config, "BEAR_SCENARIO_SYMBOLS", set()) or set()
    by_symbol, by_sector, by_group = {}, {}, {}
    call_cost, put_cost = 0.0, 0.0
    bull_exp, bear_exp = 0.0, 0.0
    def _add(sym: str, opt_type: str, cost: float):
        nonlocal call_cost, put_cost, bull_exp, bear_exp
        if sym in SPREAD_EXCLUDED_SYMBOLS or cost <= 0:
            return
        sector = sector_map.get(sym, "UNKNOWN")
        group = group_map.get(sector, "OTHER")
        by_symbol[sym] = by_symbol.get(sym, 0.0) + cost
        by_sector[sector] = by_sector.get(sector, 0.0) + cost
        by_group[group] = by_group.get(group, 0.0) + cost
        if opt_type == "put":
            put_cost += cost
        else:
            call_cost += cost
        bear = sym in bear_set
        is_bull_bet = (opt_type == "call" and not bear) or (opt_type == "put" and bear)
        if is_bull_bet:
            bull_exp += cost
        else:
            bear_exp += cost
    for p in positions or []:
        occ = p.get("occ_symbol", "") or p.get("symbol", "")
        sym = _occ_underlying(occ)
        opt_type = _classify_occ_side(occ)
        _add(sym, opt_type, abs(float(p.get("cost_basis", 0) or 0)))
    for occ, info in (gtc_orders or {}).items():
        if info.get("side") != "buy_to_open":
            continue
        sym = info.get("symbol") or _occ_underlying(occ)
        opt_type = info.get("type") or _classify_occ_side(occ)
        price = info.get("target_price") or info.get("gtc_price") or 0
        try:
            qty = int(info.get("qty", 1) or 1)
        except (TypeError, ValueError):
            qty = 1
        _add(sym, opt_type, float(price) * qty * 100)
    total = call_cost + put_cost
    hedge_ratio = put_cost / total if total > 0 else 0.0
    market_total = bull_exp + bear_exp
    bull_fraction = bull_exp / market_total if market_total > 0 else 0.5
    return {"by_symbol": by_symbol, "by_sector": by_sector, "by_group": by_group, "total": total, "call_cost": call_cost, "put_cost": put_cost, "hedge_ratio": hedge_ratio, "bull_exp": bull_exp, "bear_exp": bear_exp, "bull_fraction": bull_fraction}


def _sector_gate_would_violate(expo: dict, symbol: str, opt_type: str, new_cost: float, config) -> Tuple[bool, str]:
    """Return (blocked, reason). Blocks a proposed BUY if it would widen an
    existing sector/group/symbol violation OR push bull/bear market-direction
    past configured extremes. Matches the stocks-side 'block-on-violation'
    pattern — guidance is entry-time preventive, not a rebalancer."""
    if not getattr(config, "OPTIONS_CONTINUOUS_SECTOR_GATE", True):
        return False, ""
    if symbol in SPREAD_EXCLUDED_SYMBOLS or new_cost <= 0:
        return False, ""
    sector_map = getattr(config, "SECTOR_MAP", {}) or {}
    group_map = getattr(config, "SECTOR_GROUPS", {}) or {}
    bear_set = getattr(config, "BEAR_SCENARIO_SYMBOLS", set()) or set()
    sector = sector_map.get(symbol, "UNKNOWN")
    group = group_map.get(sector, "OTHER")
    new_total = expo["total"] + new_cost
    new_sym = expo["by_symbol"].get(symbol, 0.0) + new_cost
    new_sec = expo["by_sector"].get(sector, 0.0) + new_cost
    new_grp = expo["by_group"].get(group, 0.0) + new_cost
    max_sym = getattr(config, "OPTIONS_MAX_PER_SYMBOL", 0.25)
    max_sec = getattr(config, "OPTIONS_MAX_PER_SECTOR", 0.40)
    max_grp = getattr(config, "OPTIONS_MAX_PER_GROUP", 0.60)
    # Concentration caps only kick in once the book is established. A fresh/empty
    # book is allowed to make its first position without hitting 100% per-symbol.
    # Floor matches OPTIONS_BASE_CAP so caps apply once the book is actively sized.
    caps_floor = float(getattr(config, "OPTIONS_BASE_CAP", 5000.0)) * 0.2  # 20% of base = $1000
    if new_total >= caps_floor:
        if new_sym / new_total > max_sym:
            return True, f"{symbol}: {new_sym/new_total*100:.0f}% > {max_sym*100:.0f}% per-symbol cap"
        if new_sec / new_total > max_sec:
            return True, f"sector {sector}: {new_sec/new_total*100:.0f}% > {max_sec*100:.0f}% per-sector cap"
        if new_grp / new_total > max_grp:
            return True, f"group {group}: {new_grp/new_total*100:.0f}% > {max_grp*100:.0f}% per-group cap"
    bear = symbol in bear_set
    is_bull_bet = (opt_type == "call" and not bear) or (opt_type == "put" and bear)
    new_bull = expo["bull_exp"] + (new_cost if is_bull_bet else 0.0)
    new_bear = expo["bear_exp"] + (0.0 if is_bull_bet else new_cost)
    new_mkt_total = new_bull + new_bear
    if new_mkt_total > 0:
        new_bull_frac = new_bull / new_mkt_total
        mmin = getattr(config, "OPTIONS_MARKET_RATIO_MIN", 0.25)
        mmax = getattr(config, "OPTIONS_MARKET_RATIO_MAX", 0.75)
        if is_bull_bet and new_bull_frac > mmax:
            return True, f"market bull_frac {new_bull_frac*100:.0f}% > {mmax*100:.0f}% (too long-market)"
        if (not is_bull_bet) and new_bull_frac < mmin:
            return True, f"market bull_frac {new_bull_frac*100:.0f}% < {mmin*100:.0f}% (too short-market)"
    return False, ""


def _ratio_would_violate(call_cost: float, put_cost: float, new_type: str, new_cost: float) -> bool:
    """Return True if adding `new_cost` on `new_type` side pushes the call/put
    ratio past MAX_CALL_RATIO/MAX_PUT_RATIO. Always allow the FIRST order on the
    losing side so we can recover from a skewed book."""
    if new_cost <= 0:
        return False
    # Bootstrap: empty book — allow the very first order on either side to seed the portfolio.
    # Without this, an empty book (0/0) would compute new_frac=1.0 >0.65 and block every seed trade,
    # producing the 0-opportunities deadlock observed 2026-08-04 through 2026-08-11.
    if (call_cost + put_cost) <= 1e-9:
        return False
    if new_type == "call":
        new_call = call_cost + new_cost
        new_put = put_cost
    else:
        new_call = call_cost
        new_put = put_cost + new_cost
    new_total = new_call + new_put
    if new_total <= 0:
        return False
    if new_type == "call" and new_call / new_total > MAX_CALL_RATIO:
        return True
    if new_type == "put" and new_put / new_total > MAX_PUT_RATIO:
        return True
    return False


def _ratio_status(call_cost: float, put_cost: float) -> dict:
    """Compute ratio summary. side_needed = which side rebalances the book."""
    total = call_cost + put_cost
    call_frac = call_cost / total if total > 0 else 0.5
    put_frac = put_cost / total if total > 0 else 0.5
    if call_frac > MAX_CALL_RATIO:
        side_needed = "put"
    elif put_frac > MAX_PUT_RATIO:
        side_needed = "call"
    else:
        side_needed = "balanced"
    return {"call_frac": call_frac, "put_frac": put_frac, "side_needed": side_needed, "total": total, "call_cost": call_cost, "put_cost": put_cost}


def _scanner_liquidity_priority(
    scan_data: dict | None,
    option_type: str,
) -> dict[str, float]:
    """Map symbol -> best scanner score for liquid, BUYable 60-120 DTE outliers."""
    target = str(option_type or "").lower()
    out: dict[str, float] = {}
    for item in (scan_data or {}).get("outliers", []) or []:
        if str(item.get("type") or "").lower() != target:
            continue
        if "BUY" not in str(item.get("recommendation") or ""):
            continue
        try:
            dte = int(item.get("dte", 0) or 0)
            score = float(item.get("score", 0) or 0)
            oi = float(item.get("open_interest", 0) or 0)
        except (TypeError, ValueError):
            continue
        if dte < 60 or dte > 120 or score < 55 or oi < 500:
            continue
        sym = str(item.get("symbol") or "").upper()
        if not sym:
            continue
        out[sym] = max(out.get(sym, float("-inf")), score)
    return out


def _prioritize_signals_for_chain_fetch(
    signals: list,
    scan_data: dict | None,
    option_type: str,
) -> list:
    """Prefer symbols with scanner-confirmed liquid outliers before raw conviction.

    On 2026-08-04 the paper lane reached 0/0/0 because the top-five conviction
    names per side were illiquid or wrong-DTE, while valid liquid calls such as
    GOOGL existed further down the list and were never refetched.
    """
    priority = _scanner_liquidity_priority(scan_data, option_type)
    return sorted(
        signals,
        key=lambda sig: (
            priority.get(str(getattr(sig, "symbol", "")).upper(), float("-inf")),
            float(getattr(sig, "conviction", 0) or 0),
        ),
        reverse=True,
    )


def _split_positions_by_side(positions: list) -> tuple:
    """Return (call_cost, put_cost) for non-spread positions."""
    call_cost = 0.0
    put_cost = 0.0
    for p in positions:
        occ = p.get("occ_symbol", "") or p.get("symbol", "")
        underlying = _occ_underlying(occ)
        if underlying in SPREAD_EXCLUDED_SYMBOLS:
            continue
        cost = abs(float(p.get("cost_basis", 0) or 0))
        if cost <= 0:
            continue
        if _classify_occ_side(occ) == "put":
            put_cost += cost
        else:
            call_cost += cost
    return call_cost, put_cost


def _check_portfolio_limits(positions: list, gtc_orders: dict = None) -> dict:
    """Check current portfolio against hard limits. Returns what's allowed.
    Spread strategy positions (oil/btc/gold) are EXCLUDED from the main
    portfolio cap — they have their own budgets.
    Pending buy_to_open GTC orders ARE counted (they consume future headroom)."""
    n_positions = 0
    call_cost = 0.0
    put_cost = 0.0
    oil_spread_cost = 0.0
    for p in positions:
        occ = p.get("occ_symbol", "") or p.get("symbol", "")
        cost = abs(float(p.get("cost_basis", 0)))
        underlying = _occ_underlying(occ)
        if underlying in SPREAD_EXCLUDED_SYMBOLS:
            oil_spread_cost += cost
            continue
        if _classify_occ_side(occ) == "put":
            put_cost += cost
        else:
            call_cost += cost
        n_positions += 1
    # Add pending buy GTC commitments — an open GTC is real future exposure
    pending_call, pending_put = _gtc_pending_buy_exposure(gtc_orders or {})
    call_cost_eff = call_cost + pending_call
    put_cost_eff = put_cost + pending_put
    total = call_cost_eff + put_cost_eff
    can_buy_calls = total < MAX_TOTAL_OPTIONS and call_cost_eff < MAX_TOTAL_CALLS and n_positions < MAX_POSITIONS
    can_buy_puts = total < MAX_TOTAL_OPTIONS and put_cost_eff < MAX_TOTAL_PUTS and n_positions < MAX_POSITIONS
    if total > 0:
        call_frac = call_cost_eff / total
        put_frac = put_cost_eff / total
        if call_frac >= MAX_CALL_RATIO:
            can_buy_calls = False
        if put_frac >= MAX_PUT_RATIO:
            can_buy_puts = False
    return {"total": total, "call_cost": call_cost_eff, "put_cost": put_cost_eff, "call_cost_filled": call_cost, "put_cost_filled": put_cost, "pending_call": pending_call, "pending_put": pending_put, "n_positions": n_positions, "can_buy_calls": can_buy_calls, "can_buy_puts": can_buy_puts, "headroom": MAX_TOTAL_OPTIONS - total, "oil_spread_cost": oil_spread_cost}


def _load_daily_plan(config) -> Dict:
    plan_file = config.DATA_DIR / DAILY_PLAN_FILE
    if plan_file.exists():
        with open(plan_file) as f:
            return json.load(f)
    return {"orders": [], "updated_at": None}


def _save_daily_plan(config, plan: Dict):
    plan_file = config.DATA_DIR / DAILY_PLAN_FILE
    plan["updated_at"] = datetime.now().isoformat()
    with open(plan_file, "w") as f:
        json.dump(plan, f, indent=2, default=str)


async def _cancel_stale_gtc(client: TradierAPIClient, config) -> int:
    """Cancel GTC orders older than GTC_MAX_AGE_DAYS."""
    if not bool(getattr(config, "OPTIONS_LIVE_TRADING_ENABLED", False)):
        logger.info("[PAPER_ONLY_BLOCK] stale GTC cancellation disabled")
        return 0
    gtc_orders = _load_gtc_orders(config)
    if not gtc_orders:
        return 0
    account_id = client._current_id
    cancelled = 0
    to_remove = []
    now = datetime.now()
    for occ, info in gtc_orders.items():
        placed_at = info.get("placed_at", "")
        if not placed_at:
            continue
        try:
            placed_dt = datetime.fromisoformat(placed_at)
        except (ValueError, TypeError):
            continue
        age_days = (now - placed_dt).days
        if age_days >= GTC_MAX_AGE_DAYS:
            order_id = info.get("order_id")
            if order_id and account_id:
                order_status = await client._request("GET", f"/accounts/{account_id}/orders/{order_id}", use_data_context=False)
                st = "unknown"
                if order_status and "order" in order_status:
                    st = order_status["order"].get("status", "unknown")
                if st == "filled":
                    fill_price = order_status["order"].get("avg_fill_price", "?")
                    print(f"  GTC FILLED (late check): {occ} @ ${fill_price}")
                    logger.info(f"GTC filled (stale check) {occ} @ ${fill_price}")
                elif st in ("pending", "open", "partially_filled"):
                    print(f"  CANCEL STALE GTC: {occ} — {age_days} days old")
                    logger.info(f"Cancelling stale GTC {occ} age={age_days}d")
                    await client._request("DELETE", f"/accounts/{account_id}/orders/{order_id}", use_data_context=False)
                    cancelled += 1
            to_remove.append(occ)
    for occ in to_remove:
        del gtc_orders[occ]
    if to_remove:
        _save_gtc_orders(config, gtc_orders)
    return cancelled


async def _daily_find_opportunities(client: TradierAPIClient, config, indicators: Dict, rankings: Dict, held_symbols: set, needs_puts: bool, limits: dict) -> List[Dict]:
    """Find call and put opportunities — RESTRICTED to trb_long (calls) / trb_short (puts).
    Enforces portfolio limits: max positions, max exposure, call/put ratio ceilings."""
    # EMERGENCY_BRAKE HALT: if tradier_emergency_brake fired a Tier-4 halt, no scan
    _halted, _halt_info = _emergency_brake_halted()
    if _halted:
        logger.critical(f"[EMERGENCY_BRAKE_HALT] _daily_find_opportunities REFUSED — halt.flag present: {_halt_info}")
        return []
    # OPENING_BUFFER_NO_TRADE: don't scan for opportunities in first 30m after open
    _in_buf, _mins_open = _in_opening_buffer(config)
    if _in_buf:
        logger.warning(f"[OPENING_BUFFER_NO_TRADE] _daily_find_opportunities held: {_mins_open:.0f}m<30m_no_data")
        return []
    call_allowed, put_allowed = _load_allowed_symbols(config)
    logger.info(f"Allowed symbols: {len(call_allowed)} for calls, {len(put_allowed)} for puts")
    if not limits["can_buy_calls"] and not limits["can_buy_puts"]:
        logger.warning(f"Portfolio at limits: ${limits['total']:,.0f} total, {limits['n_positions']} positions. No new orders.")
        return []
    signals = detect_directional_signals(indicators, rankings)
    # ── Incorporate morning scanner for conviction boost ──
    scan_data = {}
    scanner_file = config.DATA_DIR / "options_analysis_latest.json"
    if scanner_file.exists():
        try:
            with open(scanner_file) as f:
                scan_data = json.load(f)
            for o in scan_data.get("outliers", []):
                sym = o.get("symbol", "")
                if sym and o.get("score", 0) >= 55:
                    direction = "LONG" if o.get("type", "") == "call" else "SHORT"
                    for sig in signals:
                        if sig.symbol == sym and sig.direction == direction:
                            sig.conviction = min(100, sig.conviction + min(20, o["score"] / 5))
        except Exception:
            pass
    opportunities = []
    # Track running call/put exposure as we pick — every new opp is gated against
    # the live ratio so we never queue up an order that would push us over the cap.
    running_call = float(limits["call_cost"])
    running_put = float(limits["put_cost"])
    # Build a running sector/group/market-direction exposure map so we can block
    # any new order that would widen an existing concentration/ratio violation.
    try:
        positions_for_expo = await get_option_positions(client)
    except Exception:
        positions_for_expo = []
    gtc_for_expo = _load_gtc_orders(config)
    running_expo = _build_live_exposure_map(positions_for_expo, gtc_for_expo, config)
    # ── CSP preflight: fetch available cash + total equity for capital + notional-cap gating ──
    csp_available_cash = 0.0
    csp_account_value = 0.0
    if getattr(config, "OPTIONS_CSP_ENABLED", False):
        try:
            bal_res = await client.get_account_balances()
            if bal_res and isinstance(bal_res, dict):
                bals = bal_res.get("balances", bal_res)
                csp_available_cash = float(bals.get("total_cash", 0) or bals.get("cash", {}).get("cash_available", 0) or 0)
                csp_account_value = float(bals.get("total_equity", 0) or bals.get("market_value", 0) or csp_available_cash)
            pos_cap = csp_account_value * config.OPTIONS_CSP_MAX_POS_PCT_OF_ACCOUNT
            logger.info(f"CSP_PREFLIGHT available_cash=${csp_available_cash:,.0f} total_equity=${csp_account_value:,.0f} per_pos_cap=${pos_cap:,.0f} ({config.OPTIONS_CSP_MAX_POS_PCT_OF_ACCOUNT:.0%} of equity)")
        except Exception as e:
            logger.warning(f"CSP_PREFLIGHT balance fetch failed: {e} — CSP disabled for this cycle")
            csp_available_cash = 0.0
            csp_account_value = 0.0
    # ── CALLS: only trb_long symbols (with CSP alternative when enabled) ──
    if limits["can_buy_calls"]:
        call_signals = [s for s in signals if s.direction == "LONG" and s.conviction >= 50 and s.symbol in call_allowed]
        call_signals = _prioritize_signals_for_chain_fetch(call_signals, scan_data, "call")
        for sig in call_signals[:20]:
            if sig.symbol in held_symbols:
                continue
            # WT/DC multi-TF gate (2026-04-26 owner directive — see helper docstring)
            _gate_ok, _gate_reason = _wt_dc_options_entry_gate(sig.symbol, True, indicators, config)
            if not _gate_ok:
                logger.info(f"WT_DC_GATE skip CALL {sig.symbol} (daily) — {_gate_reason}")
                continue
            expirations = await fetch_expirations(client, sig.symbol)
            if not expirations:
                continue
            now = datetime.now()
            target_exps = [(e, (datetime.strptime(e, "%Y-%m-%d") - now).days) for e in expirations if 60 <= (datetime.strptime(e, "%Y-%m-%d") - now).days <= 120]
            if not target_exps:
                continue
            exp, dte = target_exps[0]
            chain = await fetch_option_chain(client, sig.symbol, exp)
            if not chain:
                continue
            outliers, _ = analyze_chain_for_outliers(sig, chain, exp)
            calls = [o for o in outliers if o.option_type == "call" and o.score >= 55 and o.open_interest >= 500]
            # ── Structure comparison: SPREAD (Tier 1 primary) > CSP > buy_call ──
            csp_choice = None
            if (getattr(config, "OPTIONS_CSP_ENABLED", False) or getattr(config, "OPTIONS_SPREAD_ENABLED", False)) and csp_available_cash > 0:
                try:
                    csps = []
                    spreads = []
                    if getattr(config, "OPTIONS_CSP_ENABLED", False):
                        csps = score_sell_put_csp(sig, chain, exp, config, account_value=csp_account_value)
                    if getattr(config, "OPTIONS_SPREAD_ENABLED", False):
                        spreads = score_bull_put_spread(sig, chain, exp, config, account_value=csp_account_value)
                    if csps or spreads:
                        csp_choice = pick_best_structure(sig, outliers, csps, config, available_cash=csp_available_cash, account_value=csp_account_value, spread_candidates=spreads)
                except Exception as e:
                    logger.warning(f"Structure scoring failed for {sig.symbol}: {e}")
                    csp_choice = None
            # ── SPREAD won: emit bull_put_spread opportunity (2-leg credit spread) ──
            if csp_choice and csp_choice.option_side == "bull_put_spread":
                contract_cost = csp_choice.max_loss  # max-loss = capital at risk for spread
                if _ratio_would_violate(running_call, running_put, "call", contract_cost):
                    logger.info(f"RATIO_GATE skip SPREAD {sig.symbol} — over call cap")
                    await asyncio.sleep(0.3)
                    continue
                _sec_blocked, _sec_reason = _sector_gate_would_violate(running_expo, sig.symbol, "call", contract_cost, config)
                if _sec_blocked:
                    logger.info(f"SECTOR_GATE skip SPREAD {sig.symbol} — {_sec_reason}")
                    await asyncio.sleep(0.3)
                    continue
                opportunities.append({"symbol": sig.symbol, "type": "spread", "side": "sell_to_open", "strike": csp_choice.strike, "long_strike": csp_choice.long_leg_strike, "expiration": csp_choice.expiration, "dte": (datetime.strptime(csp_choice.expiration, "%Y-%m-%d") - datetime.now()).days, "gtc_price": csp_choice.price, "net_credit": csp_choice.net_credit, "max_loss": csp_choice.max_loss, "capital_required": csp_choice.capital_required, "score": csp_choice.raw_score, "edge_score": csp_choice.edge_score, "conviction": sig.conviction, "reason": csp_choice.rationale, "priority": "HIGH" if sig.conviction >= 70 else "MEDIUM", "qty": 1, "occ_symbol": csp_choice.occ_symbol, "long_leg_occ": csp_choice.long_leg_occ})
                running_call += contract_cost
                running_expo = _build_live_exposure_map(positions_for_expo, {**gtc_for_expo, f"_plan_{sig.symbol}_SP{csp_choice.strike}": {"side": "sell_to_open", "symbol": sig.symbol, "type": "put", "target_price": csp_choice.price, "qty": 1}}, config)
                gtc_for_expo = {**gtc_for_expo, f"_plan_{sig.symbol}_SP{csp_choice.strike}": {"side": "sell_to_open", "symbol": sig.symbol, "type": "put", "target_price": csp_choice.price, "qty": 1}}
                logger.info(f"SPREAD_PICKED {sig.symbol} {csp_choice.strike}/{csp_choice.long_leg_strike} exp={csp_choice.expiration} credit=${csp_choice.net_credit:.0f} max_loss=${csp_choice.max_loss:.0f}")
                await asyncio.sleep(0.3)
                continue
            # ── CSP won: emit sell_to_open opportunity (cash-secured put) ──
            if csp_choice and csp_choice.option_side == "sell_put_csp":
                contract_cost = csp_choice.capital_required  # cash-secured amount
                if _ratio_would_violate(running_call, running_put, "call", contract_cost):
                    logger.info(f"RATIO_GATE skip CSP {sig.symbol} — would push long-exposure past cap (CSP counted as long-equiv)")
                    await asyncio.sleep(0.3)
                    continue
                _sec_blocked, _sec_reason = _sector_gate_would_violate(running_expo, sig.symbol, "call", contract_cost, config)
                if _sec_blocked:
                    logger.info(f"SECTOR_GATE skip CSP {sig.symbol} — {_sec_reason}")
                    await asyncio.sleep(0.3)
                    continue
                opportunities.append({"symbol": sig.symbol, "type": "csp", "side": "sell_to_open", "strike": csp_choice.strike, "expiration": csp_choice.expiration, "dte": (datetime.strptime(csp_choice.expiration, "%Y-%m-%d") - datetime.now()).days, "gtc_price": csp_choice.price, "capital_required": csp_choice.capital_required, "score": csp_choice.raw_score, "edge_score": csp_choice.edge_score, "conviction": sig.conviction, "reason": f"CSP {sig.symbol} — {csp_choice.rationale}", "priority": "HIGH" if sig.conviction >= 70 else "MEDIUM", "qty": 1, "occ_symbol": csp_choice.occ_symbol})
                running_call += contract_cost
                running_expo = _build_live_exposure_map(positions_for_expo, {**gtc_for_expo, f"_plan_{sig.symbol}_CSP{csp_choice.strike}": {"side": "sell_to_open", "symbol": sig.symbol, "type": "put", "target_price": csp_choice.price, "qty": 1}}, config)
                gtc_for_expo = {**gtc_for_expo, f"_plan_{sig.symbol}_CSP{csp_choice.strike}": {"side": "sell_to_open", "symbol": sig.symbol, "type": "put", "target_price": csp_choice.price, "qty": 1}}
                logger.info(f"CSP_PICKED {sig.symbol} strike={csp_choice.strike} exp={csp_choice.expiration} price=${csp_choice.price:.2f} capital=${contract_cost:,.0f} {csp_choice.rationale}")
                await asyncio.sleep(0.3)
                continue
            if calls:
                best = calls[0]
                discount = GTC_DISCOUNT_HIGH if sig.conviction >= 70 else GTC_DISCOUNT_NORMAL
                gtc_price = round(best.ask * discount * 20) / 20
                contract_cost = gtc_price * 100  # qty=1
                if _ratio_would_violate(running_call, running_put, "call", contract_cost):
                    logger.info(f"RATIO_GATE skip CALL {sig.symbol} — would push call ratio past {MAX_CALL_RATIO:.0%} (running calls=${running_call:.0f}, puts=${running_put:.0f})")
                    await asyncio.sleep(0.3)
                    continue
                _sec_blocked, _sec_reason = _sector_gate_would_violate(running_expo, sig.symbol, "call", contract_cost, config)
                if _sec_blocked:
                    logger.info(f"SECTOR_GATE skip CALL {sig.symbol} — {_sec_reason}")
                    await asyncio.sleep(0.3)
                    continue
                opportunities.append({"symbol": sig.symbol, "type": "call", "strike": best.strike, "expiration": best.expiration, "dte": best.dte, "bid": best.bid, "ask": best.ask, "mid": best.mid, "gtc_price": gtc_price, "edge_pct": best.edge_pct, "iv_deviation": best.iv_deviation_pct, "delta": best.greeks["delta"], "oi": best.open_interest, "score": best.score, "conviction": sig.conviction, "reason": f"D oversold — conv {sig.conviction:.0f}, edge {best.edge_pct:+.1f}%", "priority": "HIGH" if sig.conviction >= 70 else "MEDIUM", "qty": 1})
                running_call += contract_cost
                # Re-score the live exposure map so the next pick sees this one as filled
                running_expo = _build_live_exposure_map(positions_for_expo, {**gtc_for_expo, f"_plan_{sig.symbol}_C{best.strike}": {"side": "buy_to_open", "symbol": sig.symbol, "type": "call", "target_price": gtc_price, "qty": 1}}, config)
                gtc_for_expo = {**gtc_for_expo, f"_plan_{sig.symbol}_C{best.strike}": {"side": "buy_to_open", "symbol": sig.symbol, "type": "call", "target_price": gtc_price, "qty": 1}}
            await asyncio.sleep(0.3)
    else:
        logger.info(f"CALLS blocked: ratio {limits['call_cost']/(limits['total']+0.01):.0%} >= {MAX_CALL_RATIO:.0%} or at ceiling")
    # ── PUTS: only trb_short symbols ──
    if limits["can_buy_puts"]:
        put_signals = [s for s in signals if s.direction == "SHORT" and s.conviction >= 40 and s.symbol in put_allowed]
        # Also add losing shorts as hedge candidates (already restricted to trb_short)
        shorts_file = config.DATA_DIR / "ladder_levels_trb_short.json"
        if shorts_file.exists():
            with open(shorts_file) as f:
                short_positions = json.load(f)
            for k, v in short_positions.items():
                sym = k.split(":")[1].replace("_SHORT", "") if ":" in k else k.replace("_SHORT", "")
                if not sym or sym in held_symbols or sym not in put_allowed:
                    continue
                ind = indicators.get(sym, {})
                price = ind.get("current_price") or ind.get("mark_price") or 0
                levels = v.get("levels", [])
                entry = levels[0].get("level", 0) if levels else 0
                if entry > 0 and price > 0 and price > entry * 1.03:
                    loss_pct = (price - entry) / entry * 100
                    d_mom = ind.get("wt_momentum_state_D", "")
                    d_cross = ind.get("wt_cross_D", "")
                    if d_cross == "BEAR" or d_mom in ("IMPULSE_DOWN", "EXHAUST_DOWN"):
                        if not any(s.symbol == sym for s in put_signals):
                            atr_D = ind.get("atr_D", 0) or 0
                            put_signals.append(DirectionalSignal(symbol=sym, direction="SHORT", conviction=min(80, 40 + loss_pct * 2), price=price, atr_D=atr_D, signals={"hedge_short": True, "loss_pct": round(loss_pct, 1)}))
        put_signals = _prioritize_signals_for_chain_fetch(put_signals, scan_data, "put")
        for sig in put_signals[:20]:
            if sig.symbol in held_symbols:
                continue
            # WT/DC multi-TF gate (2026-04-26 owner directive — see helper docstring)
            _gate_ok, _gate_reason = _wt_dc_options_entry_gate(sig.symbol, False, indicators, config)
            if not _gate_ok:
                logger.info(f"WT_DC_GATE skip PUT {sig.symbol} (daily) — {_gate_reason}")
                continue
            expirations = await fetch_expirations(client, sig.symbol)
            if not expirations:
                continue
            now = datetime.now()
            target_exps = [(e, (datetime.strptime(e, "%Y-%m-%d") - now).days) for e in expirations if 60 <= (datetime.strptime(e, "%Y-%m-%d") - now).days <= 120]
            if not target_exps:
                continue
            exp, dte = target_exps[0]
            chain = await fetch_option_chain(client, sig.symbol, exp)
            if not chain:
                continue
            outliers, _ = analyze_chain_for_outliers(sig, chain, exp)
            puts = [o for o in outliers if o.option_type == "put" and o.score >= 50 and o.open_interest >= 300]
            if puts:
                best = puts[0]
                discount = GTC_DISCOUNT_HIGH if sig.conviction >= 60 else GTC_DISCOUNT_NORMAL
                gtc_price = round(best.ask * discount * 20) / 20
                contract_cost = gtc_price * 100  # qty=1
                if _ratio_would_violate(running_call, running_put, "put", contract_cost):
                    logger.info(f"RATIO_GATE skip PUT {sig.symbol} — would push put ratio past {MAX_PUT_RATIO:.0%} (running calls=${running_call:.0f}, puts=${running_put:.0f})")
                    await asyncio.sleep(0.3)
                    continue
                _sec_blocked, _sec_reason = _sector_gate_would_violate(running_expo, sig.symbol, "put", contract_cost, config)
                if _sec_blocked:
                    logger.info(f"SECTOR_GATE skip PUT {sig.symbol} — {_sec_reason}")
                    await asyncio.sleep(0.3)
                    continue
                hedge_note = f" [hedge short losing {sig.signals.get('loss_pct', '?')}%]" if sig.signals.get("hedge_short") else ""
                opportunities.append({"symbol": sig.symbol, "type": "put", "strike": best.strike, "expiration": best.expiration, "dte": best.dte, "bid": best.bid, "ask": best.ask, "mid": best.mid, "gtc_price": gtc_price, "edge_pct": best.edge_pct, "iv_deviation": best.iv_deviation_pct, "delta": best.greeks["delta"], "oi": best.open_interest, "score": best.score, "conviction": sig.conviction, "reason": f"D bearish — conv {sig.conviction:.0f}, edge {best.edge_pct:+.1f}%{hedge_note}", "priority": "HIGH" if needs_puts or sig.conviction >= 60 else "MEDIUM", "qty": 1})
                running_put += contract_cost
                running_expo = _build_live_exposure_map(positions_for_expo, {**gtc_for_expo, f"_plan_{sig.symbol}_P{best.strike}": {"side": "buy_to_open", "symbol": sig.symbol, "type": "put", "target_price": gtc_price, "qty": 1}}, config)
                gtc_for_expo = {**gtc_for_expo, f"_plan_{sig.symbol}_P{best.strike}": {"side": "buy_to_open", "symbol": sig.symbol, "type": "put", "target_price": gtc_price, "qty": 1}}
            await asyncio.sleep(0.3)
    else:
        logger.info(f"PUTS blocked: ratio {limits['put_cost']/(limits['total']+0.01):.0%} >= {MAX_PUT_RATIO:.0%} or at ceiling")
    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    opportunities.sort(key=lambda o: (priority_order.get(o["priority"], 2), -o["score"]))
    return opportunities


async def _place_gtc_buys(client: TradierAPIClient, config, orders: List[Dict], dry_run: bool = False) -> List[Dict]:
    """Place GTC orders: buy_to_open, sell_to_open (CSP), or multileg credit spread."""
    if not dry_run and not bool(getattr(config, "OPTIONS_LIVE_TRADING_ENABLED", False)):
        logger.warning("[PAPER_ONLY_BLOCK] held %d GTC option order(s)", len(orders))
        return [{**order, "status": "paper_only_blocked", "reason": "OPTIONS_LIVE_TRADING_ENABLED=False"} for order in orders]
    gtc_orders = _load_gtc_orders(config)
    # DEFENSIVE GATE (2026-04-26): symbol allowlist + BLACKLIST check at the
    # order-placement site itself. Upstream gates exist in make_decisions and
    # _daily_find_opportunities, but adding this final-mile check makes the
    # function self-defending — any future caller cannot bypass.
    _gtc_blacklist: set = {s.upper() for s in (getattr(config, 'BLACKLIST', None) or []) if isinstance(s, str)}
    _gtc_call_allowed, _gtc_put_allowed = _load_allowed_symbols(config) if config else (set(), set())
    # AUGMENT-INTO-LOSS BLOCK (2026-04-26): refuse to add more contracts to an
    # existing OCC that has fallen below entry_avg × THRESHOLD. Closes the PLTR
    # Jul17 $150C 6-buy averaging pattern (entries at $17.45, $17.30, $14.65,
    # $12.52, $12.45, $12.35 = doubling down through a 28% drop). Fetches
    # current positions ONCE upfront — single API call cost amortized over batch.
    _aug_block_enabled = bool(getattr(config, "OPTIONS_AUGMENT_INTO_LOSS_BLOCK_ENABLED", True))
    _aug_threshold = float(getattr(config, "OPTIONS_AUGMENT_INTO_LOSS_THRESHOLD", 0.85))
    _occ_to_avg_cost: dict = {}
    if _aug_block_enabled:
        try:
            _curr_positions = await get_option_positions(client, config=config)
            for _p in (_curr_positions or []):
                _occ_p = _p.get("occ_symbol") or _p.get("symbol")
                _qty_p = float(_p.get("quantity", 0) or 0)
                _cb_p = float(_p.get("cost_basis", 0) or 0)
                if _occ_p and _qty_p > 0:
                    _occ_to_avg_cost[_occ_p] = _cb_p / (_qty_p * 100.0)
        except Exception as _ape:
            logger.warning(f"[AUGMENT_BLOCK] could not fetch positions for augment check: {_ape}")
    results = []
    for order in orders:
        _o_sym = str(order.get("symbol", "")).upper()
        if _gtc_blacklist and _o_sym in _gtc_blacklist:
            logger.error(f"[BLACKLIST_BLOCK] refused to place order on blacklisted {_o_sym} (order={order.get('type')}/{order.get('side')})")
            results.append({**order, "status": "blocked_blacklist"})
            continue
        is_spread = order.get("type") == "spread"
        is_csp = (order.get("type") == "csp") or (order.get("side") == "sell_to_open" and not is_spread)
        # AUGMENT-INTO-LOSS gate: only applies to outright buys on existing OCCs
        if _aug_block_enabled and not is_spread and not is_csp and order.get("side") != "sell_to_open":
            try:
                _o_occ = order.get("occ_symbol") or build_occ_symbol(order["symbol"], order["expiration"], order["type"], order["strike"])
            except Exception:
                _o_occ = order.get("occ_symbol", "")
            if _o_occ and _o_occ in _occ_to_avg_cost:
                _existing_avg = _occ_to_avg_cost[_o_occ]
                _new_price = float(order.get("gtc_price", order.get("price", 0)) or 0)
                if _existing_avg > 0 and _new_price > 0 and _new_price < _existing_avg * _aug_threshold:
                    logger.error(f"[AUGMENT_BLOCK] refused add to {_o_occ}: new=${_new_price:.2f} < avg=${_existing_avg:.2f} × {_aug_threshold:.2f} (down >{(1-_aug_threshold)*100:.0f}% from entry — do not double down on a losing position)")
                    results.append({**order, "status": "blocked_augment_into_loss", "reason": f"new ${_new_price:.2f} < avg ${_existing_avg:.2f} × {_aug_threshold:.2f}"})
                    continue
        # Allowlist: only enforce on outright long calls/puts (not spreads/CSPs which are
        # defined-risk and may legitimately use a different symbol universe).
        if not is_spread and not is_csp:
            _o_type = order.get("type")
            if _o_type == "call" and _gtc_call_allowed and _o_sym not in _gtc_call_allowed:
                logger.error(f"[ALLOWLIST_BLOCK] refused call on non-allowlisted {_o_sym}")
                results.append({**order, "status": "blocked_allowlist"})
                continue
            if _o_type == "put" and _gtc_put_allowed and _o_sym not in _gtc_put_allowed:
                logger.error(f"[ALLOWLIST_BLOCK] refused put on non-allowlisted {_o_sym}")
                results.append({**order, "status": "blocked_allowlist"})
                continue
        if is_spread:
            short_occ = order.get("occ_symbol") or build_occ_symbol(order["symbol"], order["expiration"], "put", order["strike"])
            long_occ = order.get("long_leg_occ") or build_occ_symbol(order["symbol"], order["expiration"], "put", order["long_strike"])
            occ = short_occ  # key by short leg
            tradier_type = "put"
            if occ in gtc_orders:
                print(f"  SKIP {occ} — existing GTC")
                results.append({**order, "status": "skipped"})
                continue
            qty = order.get("qty", 1)
            net_credit_per_share = order["gtc_price"]
            max_loss = order.get("max_loss", 0)
            print(f"  {'[DRY] ' if dry_run else ''}GTC SPREAD {order['symbol']} {order['strike']}P/{order['long_strike']}P x{qty} @ net_credit ${net_credit_per_share:.2f}/share — max_loss ${max_loss:.0f}  {order['reason']}")
            if dry_run:
                results.append({**order, "status": "dry_run", "occ": occ})
                continue
            legs = [{"option_symbol": short_occ, "side": "sell_to_open", "quantity": qty}, {"option_symbol": long_occ, "side": "buy_to_open", "quantity": qty}]
            res = await client.place_multileg_option_order(order["symbol"], legs, order_type="credit", price=net_credit_per_share, duration="gtc")
            if "order" in res:
                order_id = res["order"].get("id")
                status = res["order"].get("status", "")
                print(f"    PLACED MULTILEG — ID: {order_id} ({status})")
                logger.info(f"GTC SPREAD {order['symbol']} {short_occ}/{long_occ} credit=${net_credit_per_share:.2f} ID={order_id}")
                gtc_orders[occ] = {"order_id": order_id, "target_price": net_credit_per_share, "placed_at": datetime.now().isoformat(), "symbol": order["symbol"], "type": "put", "strike": order["strike"], "expiration": order["expiration"], "reason": order["reason"], "side": "sell_to_open", "structure": "bull_put_spread", "short_leg_occ": short_occ, "long_leg_occ": long_occ, "long_strike": order["long_strike"], "net_credit": order.get("net_credit"), "max_loss": max_loss}
                results.append({**order, "status": "placed", "order_id": order_id, "occ": occ})
            elif "errors" in res:
                print(f"    FAILED: {res['errors']}")
                results.append({**order, "status": "failed", "occ": occ})
            await asyncio.sleep(0.3)
            continue
        if is_csp:
            occ = order.get("occ_symbol") or build_occ_symbol(order["symbol"], order["expiration"], "put", order["strike"])
            side = "sell_to_open"
            tradier_type = "put"
        else:
            occ = build_occ_symbol(order["symbol"], order["expiration"], order["type"], order["strike"])
            side = "buy_to_open"
            tradier_type = order["type"]
        if occ in gtc_orders:
            print(f"  SKIP {occ} — existing GTC")
            results.append({**order, "status": "skipped"})
            continue
        qty = order.get("qty", 1)
        price = order["gtc_price"]
        if is_csp:
            capital = order.get("capital_required", price * qty * 100)
            print(f"  {'[DRY] ' if dry_run else ''}GTC SELL_TO_OPEN (CSP) {occ} x{qty} @ ${price:.2f} — capital_locked=${capital:,.0f}  {order['reason']}")
        else:
            cost = price * qty * 100
            discount_pct = price / order["ask"] * 100 if order["ask"] > 0 else 0
            print(f"  {'[DRY] ' if dry_run else ''}GTC BUY {occ} x{qty} @ ${price:.2f} ({discount_pct:.0f}% of ask=${order['ask']:.2f}) Cost: ${cost:.0f}  {order['reason']}")
        if dry_run:
            results.append({**order, "status": "dry_run", "occ": occ})
            continue
        res = await place_option_order(client, order["symbol"], occ, side, qty, "limit", price, duration="gtc", config=config)
        if "order" in res:
            order_id = res["order"].get("id")
            status = res["order"].get("status", "")
            print(f"    PLACED — ID: {order_id} ({status})")
            logger.info(f"GTC {side} {occ} x{qty} @ ${price:.2f} ID={order_id}")
            gtc_orders[occ] = {"order_id": order_id, "target_price": price, "placed_at": datetime.now().isoformat(), "symbol": order["symbol"], "type": tradier_type, "strike": order["strike"], "expiration": order["expiration"], "reason": order["reason"], "side": side, "capital_required": order.get("capital_required")}
            results.append({**order, "status": "placed", "order_id": order_id, "occ": occ})
        elif "errors" in res:
            print(f"    FAILED: {res['errors']}")
            results.append({**order, "status": "failed", "occ": occ})
        await asyncio.sleep(0.3)
    _save_gtc_orders(config, gtc_orders)
    return results


async def run_daily_cycle(args):
    """Afternoon cycle: review portfolio, find opportunities, place GTC orders at dip prices."""
    config = TradierConfig()
    _apply_runtime_overrides(config)
    if not bool(getattr(config, "OPTIONS_LIVE_TRADING_ENABLED", False)) and not getattr(args, "dry_run", False):
        print("[PAPER_ONLY] daily options cycle is analysis-only; no orders or cancellations will run")
        return
    account_key = getattr(args, "account", "trb") or "trb"
    dry_run = getattr(args, "dry_run", False)
    review_only = getattr(args, "review", False)
    budget = getattr(args, "budget", None) or MAX_TOTAL_OPTIONS * 3
    ind_file = config.DATA_DIR / "tradier_indicators_latest.json"
    indicators = {}
    if ind_file.exists():
        with open(ind_file) as f:
            indicators = json.load(f)
    rank_file = config.DATA_DIR / "tradier_rankings.json"
    rankings = {}
    if rank_file.exists():
        with open(rank_file) as f:
            rankings = json.load(f)
    client = TradierAPIClient(config, account_key=account_key)
    await client.connect()
    try:
        print(f"\n{'='*90}")
        print(f"  OPTIONS DAILY CYCLE — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"{'='*90}")
        # Step 1: Cancel stale GTC orders
        print(f"\n  Step 1: Cleaning stale GTC orders (>{GTC_MAX_AGE_DAYS}d)...")
        cancelled = await _cancel_stale_gtc(client, config)
        print(f"  {'Cancelled ' + str(cancelled) if cancelled else 'None stale'}")
        # Step 2: Review positions
        print(f"\n  Step 2: Portfolio review...")
        positions = await get_option_positions(client)
        held_symbols = set()
        total_calls = 0
        total_puts = 0
        total_call_val = 0
        total_put_val = 0
        total_pnl = 0
        wt_history = _load_wt_history(config)
        wt_history_updated = {}
        for pos_data in positions:
            opt_pos, exit_signals = await analyze_option_position(client, pos_data, indicators, wt_history=wt_history, config=config)
            _occ_key = pos_data.get("occ_symbol", "") or pos_data.get("symbol", "")
            _ind_sym = indicators.get(opt_pos.symbol, {}) if indicators else {}
            if _occ_key:
                wt_history_updated[_occ_key] = {"wt_velocity_D": _ind_sym.get("wt_velocity_D", 0) or 0, "wt_velocity_4h": _ind_sym.get("wt_velocity_4h", 0) or 0, "ts": datetime.utcnow().isoformat()}
            is_call = opt_pos.option_type.lower() == "call"
            value = opt_pos.current_price * abs(opt_pos.quantity) * 100
            held_symbols.add(opt_pos.symbol)
            if is_call:
                total_calls += abs(opt_pos.quantity)
                total_call_val += value
            else:
                total_puts += abs(opt_pos.quantity)
                total_put_val += value
            total_pnl += opt_pos.unrealized_pnl
            sig_str = f" [{len(exit_signals)} signals]" if exit_signals else ""
            print(f"    {opt_pos.symbol:6s} {opt_pos.option_type.upper():4s} ${opt_pos.strike:>8.2f} {opt_pos.expiration} ({opt_pos.dte}d) x{abs(opt_pos.quantity):.0f}  PnL: ${opt_pos.unrealized_pnl:+.2f} ({opt_pos.unrealized_pct:+.1f}%)  D={opt_pos.delta:+.3f}{sig_str}")
            await asyncio.sleep(0.2)
        if wt_history_updated:
            _save_wt_history(config, wt_history_updated)
        needs_puts = total_calls > total_puts * 1.5
        # Check portfolio limits — pass GTC orders so pending buys count toward the cap
        gtc_orders_for_limits = _load_gtc_orders(config)
        limits = _check_portfolio_limits(positions, gtc_orders_for_limits)
        _ratio_now = _ratio_status(limits["call_cost"], limits["put_cost"])
        print(f"  Calls: {total_calls:.0f} (${total_call_val:.0f}) | Puts: {total_puts:.0f} (${total_put_val:.0f}) | PnL: ${total_pnl:+.2f}")
        print(f"  Positions: {limits['n_positions']}/{MAX_POSITIONS} | Total: ${limits['total']:,.0f}/${MAX_TOTAL_OPTIONS:,.0f} | Headroom: ${limits['headroom']:,.0f}")
        print(f"  Effective (incl. pending GTC): calls=${limits['call_cost']:,.0f} puts=${limits['put_cost']:,.0f}  pending +call=${limits['pending_call']:,.0f} +put=${limits['pending_put']:,.0f}")
        _ratio_color = "\033[92m" if _ratio_now["side_needed"] == "balanced" else "\033[91m"
        print(f"  {_ratio_color}RATIO: calls {_ratio_now['call_frac']:.0%} / puts {_ratio_now['put_frac']:.0%}  (cap {MAX_CALL_RATIO:.0%} either side)  side_needed={_ratio_now['side_needed'].upper()}\033[0m")
        print(f"  Can buy calls: {limits['can_buy_calls']} | Can buy puts: {limits['can_buy_puts']} | {'NEEDS MORE PUTS' if needs_puts else 'Ratio OK'}")
        # Step 3: Active GTC orders
        gtc_orders = _load_gtc_orders(config)
        if gtc_orders:
            print(f"\n  Active GTC orders ({len(gtc_orders)}):")
            for occ, info in gtc_orders.items():
                age = (datetime.now() - datetime.fromisoformat(info["placed_at"])).days if info.get("placed_at") else "?"
                print(f"    {occ}: {info.get('side','?')} @ ${info.get('target_price', 0.0):.2f} — {info.get('reason', '?')[:50]} — {age}d old")
        if review_only:
            print("\n  Review complete (no orders)")
            return
        # Step 4: Find opportunities
        call_allowed, put_allowed = _load_allowed_symbols(config)
        print(f"\n  Step 4: Scanning opportunities (budget=${budget:.0f}, calls from {len(call_allowed)} trb_long, puts from {len(put_allowed)} trb_short)...")
        opportunities = await _daily_find_opportunities(client, config, indicators, rankings, held_symbols, needs_puts, limits)
        if not opportunities:
            print("  No opportunities above threshold.")
            _save_daily_plan(config, {"orders": [], "cycle_time": datetime.now().isoformat()})
            return
        # Select within budget AND under the strict call/put ratio cap.
        # We track running per-side cost so each pick is gated against the
        # would-be ratio, preventing the daily plan from queueing up enough
        # one-sided orders to break the cap when they all fill.
        total_cost = 0
        selected = []
        sel_running_call = float(limits["call_cost"])
        sel_running_put = float(limits["put_cost"])
        # Sector/group/market-direction guard — built fresh here because the
        # opportunity list may have been trimmed/re-sorted above.
        sel_expo = _build_live_exposure_map(positions, _load_gtc_orders(config), config)
        sel_gtc_extra: Dict = {}
        # User-cancel cooldown: skip any OCC the user canceled within N hours
        _user_cancel_cooldown_h = float(getattr(config, "OPTIONS_USER_CANCEL_COOLDOWN_HOURS", 4.0))
        _existing_gtc = _load_gtc_orders(config)
        for opp in opportunities:
            cost = opp["gtc_price"] * opp.get("qty", 1) * 100
            if total_cost + cost > budget:
                continue
            opp_type = opp.get("type", "call")
            _opp_occ = build_occ_symbol(opp["symbol"], opp["expiration"], opp_type, opp["strike"])
            _prev = _existing_gtc.get(_opp_occ, {})
            _cancel_ts = _prev.get("_user_canceled_at")
            if _cancel_ts:
                try:
                    _cc_hrs = (datetime.utcnow() - datetime.fromisoformat(_cancel_ts)).total_seconds() / 3600.0
                except Exception:
                    _cc_hrs = 99.0
                if _cc_hrs < _user_cancel_cooldown_h:
                    logger.info(f"CANCEL_COOLDOWN skip {_opp_occ} — user canceled {_cc_hrs:.1f}h ago (< {_user_cancel_cooldown_h:.1f}h)")
                    continue
            if _ratio_would_violate(sel_running_call, sel_running_put, opp_type, cost):
                logger.info(f"RATIO_GATE skip plan {opp['symbol']} {opp_type.upper()} — would push ratio past cap (calls=${sel_running_call:.0f}, puts=${sel_running_put:.0f})")
                continue
            _sec_blocked, _sec_reason = _sector_gate_would_violate(sel_expo, opp["symbol"], opp_type, cost, config)
            if _sec_blocked:
                logger.info(f"SECTOR_GATE skip plan {opp['symbol']} {opp_type.upper()} — {_sec_reason}")
                continue
            total_cost += cost
            selected.append(opp)
            if opp_type == "put":
                sel_running_put += cost
            else:
                sel_running_call += cost
            sel_gtc_extra[f"_sel_{opp['symbol']}_{opp_type}_{opp['strike']}"] = {"side": "buy_to_open", "symbol": opp["symbol"], "type": opp_type, "target_price": opp["gtc_price"], "qty": opp.get("qty", 1)}
            sel_expo = _build_live_exposure_map(positions, {**_load_gtc_orders(config), **sel_gtc_extra}, config)
        print(f"  Found {len(opportunities)}, selected {len(selected)} (${total_cost:.0f}):")
        for opp in selected:
            print(f"    [{opp['priority']:6s}] {opp['symbol']:6s} {opp['type'].upper():4s} ${opp['strike']:>8.2f} {opp['expiration']} ({opp['dte']}d)  GTC=${opp['gtc_price']:.2f} (ask=${opp['ask']:.2f})  Edge:{opp['edge_pct']:+.1f}%  OI:{opp['oi']}  Score:{opp['score']:.0f}")
            print(f"           {opp['reason']}")
        # Step 5: Place GTC orders
        print(f"\n  Step 5: Placing {len(selected)} GTC buy orders...")
        results = await _place_gtc_buys(client, config, selected, dry_run=dry_run)
        plan = {"orders": selected if dry_run else [], "selected": len(selected), "total_cost": total_cost, "calls": total_calls, "puts": total_puts, "cycle_time": datetime.now().isoformat(), "results": [{k: v for k, v in r.items() if k != "signals"} for r in results]}
        _save_daily_plan(config, plan)
        placed = sum(1 for r in results if r.get("status") == "placed")
        print(f"\n  Done: {placed} orders placed, {len(results) - placed} skipped/failed")
    finally:
        await client.close()
    # ── Step 6: Oil Spread Monitor ──
    try:
        from oil_spread_monitor import run_scan as oil_scan
        print(f"\n  Step 6: Oil Spread Monitor...")
        oil_args = argparse.Namespace(dry_run=dry_run)
        await oil_scan(oil_args)
    except Exception as e:
        logger.warning(f"Oil spread monitor error: {e}")
    # ── Step 7: BTC Trio Spread Monitor (MSTR/IBIT/COIN) ──
    try:
        from btc_spread_monitor import run_scan as btc_spread_scan
        print(f"\n  Step 7: BTC Trio Spread Monitor (MSTR/IBIT/COIN)...")
        btc_args = argparse.Namespace(dry_run=dry_run)
        await btc_spread_scan(btc_args)
    except Exception as e:
        logger.warning(f"BTC spread monitor error: {e}")
    # ── Step 8: Gold Spread Monitor (NEM/GDX/GLD/AEM/RGLD/WPM) ──
    try:
        from gold_spread_monitor import run_scan as gold_spread_scan
        print(f"\n  Step 8: Gold Spread Monitor (NEM/GDX/GLD/AEM/RGLD/WPM)...")
        gold_args = argparse.Namespace(dry_run=dry_run)
        await gold_spread_scan(gold_args)
    except Exception as e:
        logger.warning(f"Gold spread monitor error: {e}")
    print(f"\n{'='*90}")
    print(f"  DAILY CYCLE COMPLETE — {datetime.utcnow().strftime('%H:%M:%S')} UTC")
    print(f"{'='*90}")


async def run_premarket(args):
    """Pre-market: re-scan prices, adjust GTC orders, fire pending plan orders."""
    config = TradierConfig()
    _apply_runtime_overrides(config)
    if not bool(getattr(config, "OPTIONS_LIVE_TRADING_ENABLED", False)) and not getattr(args, "dry_run", False):
        print("[PAPER_ONLY] premarket options cycle is analysis-only; no orders will run")
        return
    account_key = getattr(args, "account", "trb") or "trb"
    ind_file = config.DATA_DIR / "tradier_indicators_latest.json"
    indicators = {}
    if ind_file.exists():
        with open(ind_file) as f:
            indicators = json.load(f)
    plan = _load_daily_plan(config)
    orders = plan.get("orders", [])
    client = TradierAPIClient(config, account_key=account_key)
    await client.connect()
    try:
        print(f"\n{'='*90}")
        print(f"  PRE-MARKET ADJUSTMENT — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"  {len(orders)} pending orders from daily plan")
        print(f"{'='*90}")
        if not orders:
            print("  No pending orders. Run --daily first.")
            return
        # Pull current portfolio so premarket also respects sector/ratio/cancel cooldown
        positions_pm = []
        try:
            positions_pm = await get_option_positions(client)
        except Exception as e:
            logger.warning(f"premarket: failed to fetch positions: {e}")
        existing_gtc_pm = _load_gtc_orders(config)
        pm_expo = _build_live_exposure_map(positions_pm, existing_gtc_pm, config)
        pm_running_call = float(sum(c for c in [pm_expo.get("call_cost", 0.0)]))
        pm_running_put = float(sum(c for c in [pm_expo.get("put_cost", 0.0)]))
        cooldown_h = float(getattr(config, "OPTIONS_USER_CANCEL_COOLDOWN_HOURS", 4.0))
        adjusted = []
        for order in orders:
            symbol = order["symbol"]
            occ = build_occ_symbol(symbol, order["expiration"], order["type"], order["strike"])
            # Skip any OCC the user canceled within the cooldown window
            _prev = existing_gtc_pm.get(occ, {})
            _cancel_ts = _prev.get("_user_canceled_at")
            if _cancel_ts:
                try:
                    _cc_hrs = (datetime.utcnow() - datetime.fromisoformat(_cancel_ts)).total_seconds() / 3600.0
                except Exception:
                    _cc_hrs = 99.0
                if _cc_hrs < cooldown_h:
                    print(f"  {occ}: SKIP — user canceled {_cc_hrs:.1f}h ago (< {cooldown_h:.0f}h cooldown)")
                    continue
            opt_quote_res = await client._request("GET", "/markets/quotes", params={"symbols": occ}, use_data_context=True)
            opt_quote = {}
            if opt_quote_res and "quotes" in opt_quote_res and "quote" in opt_quote_res["quotes"]:
                q = opt_quote_res["quotes"]["quote"]
                opt_quote = q if isinstance(q, dict) else (q[0] if isinstance(q, list) and q else {})
            new_bid = opt_quote.get("bid", 0) or 0
            new_ask = opt_quote.get("ask", 0) or 0
            if new_ask <= 0:
                print(f"  {occ}: no quote, keeping ${order['gtc_price']:.2f}")
                adjusted.append(order)
                continue
            ind = indicators.get(symbol, {})
            d_mom = ind.get("wt_momentum_state_D", "?")
            is_call = order["type"] == "call"
            discount = GTC_DISCOUNT_HIGH if order.get("conviction", 0) >= 70 else GTC_DISCOUNT_NORMAL
            new_price = round(new_ask * discount * 20) / 20
            old_price = order["gtc_price"]
            change = (new_price - old_price) / old_price * 100 if old_price > 0 else 0
            # Drop puts if D flipped strongly bullish
            thesis_ok = True
            if not is_call and d_mom == "IMPULSE_UP":
                thesis_ok = False
            # Drop calls if D flipped strongly bearish (symmetric to put guard)
            if is_call and d_mom == "IMPULSE_DOWN":
                thesis_ok = False
            order["bid"] = new_bid
            order["ask"] = new_ask
            order["gtc_price"] = new_price
            # Sector/ratio re-check at fire time — market moved overnight, the book may
            # have drifted; don't blindly place a stale plan order that now widens a
            # violation (the exact scenario the user cancels every morning).
            pm_cost = new_price * order.get("qty", 1) * 100
            pm_type = order["type"]
            ratio_violate = _ratio_would_violate(pm_running_call, pm_running_put, pm_type, pm_cost)
            sec_blocked, sec_reason = _sector_gate_would_violate(pm_expo, symbol, pm_type, pm_cost, config)
            if ratio_violate:
                thesis_ok = False
                status_extra = " [RATIO_GATE]"
            elif sec_blocked:
                thesis_ok = False
                status_extra = f" [SECTOR_GATE {sec_reason}]"
            else:
                status_extra = ""
            status = "DROP" if not thesis_ok else ("ADJ" if abs(change) > 2 else "OK")
            print(f"  {occ}: ${old_price:.2f}->${new_price:.2f} ({change:+.1f}%) D={d_mom} [{status}]{status_extra}")
            if thesis_ok:
                adjusted.append(order)
                if pm_type == "put":
                    pm_running_put += pm_cost
                else:
                    pm_running_call += pm_cost
                pm_expo = _build_live_exposure_map(positions_pm, {**existing_gtc_pm, f"_pm_{symbol}_{pm_type}_{order['strike']}": {"side": "buy_to_open", "symbol": symbol, "type": pm_type, "target_price": new_price, "qty": order.get("qty", 1)}}, config)
                existing_gtc_pm = {**existing_gtc_pm, f"_pm_{symbol}_{pm_type}_{order['strike']}": {"side": "buy_to_open", "symbol": symbol, "type": pm_type, "target_price": new_price, "qty": order.get("qty", 1)}}
            await asyncio.sleep(0.2)
        if adjusted:
            # PREMARKET NO-FIRE GUARD (2026-04-26 owner directive)
            # Premarket cron fires at 13:23 UTC = 9:23 AM ET while owner may still
            # be asleep. Default-on guard skips order placement; the analysis +
            # adjusted plan is still saved so morning brief can show it. Owner
            # reviews, then manually re-runs without the flag to fire.
            if bool(getattr(config, "OPTIONS_PREMARKET_NO_FIRE", True)):
                print(f"\n  \033[93m[PREMARKET_NO_FIRE]\033[0m {len(adjusted)} adjusted orders held — config OPTIONS_PREMARKET_NO_FIRE=True")
                print(f"  Plan saved for review. To fire manually: flip OPTIONS_PREMARKET_NO_FIRE=False, run --premarket again.")
                logger.warning(f"[PREMARKET_NO_FIRE] held {len(adjusted)} orders: {[o.get('symbol') + '/' + o.get('type','?') + '/' + str(o.get('strike','?')) for o in adjusted]}")
                plan["orders"] = adjusted  # preserve so morning brief can read
                plan["premarket_held"] = datetime.now().isoformat()
                plan["premarket_held_reason"] = "OPTIONS_PREMARKET_NO_FIRE=True"
                _save_daily_plan(config, plan)
            else:
                print(f"\n  Placing {len(adjusted)} adjusted orders...")
                results = await _place_gtc_buys(client, config, adjusted)
                plan["orders"] = []
                plan["premarket_placed"] = datetime.now().isoformat()
                _save_daily_plan(config, plan)
        else:
            print("  No orders survived pre-market adjustment.")
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description="Autonomous Options Trading Agent")
    parser.add_argument("--dry-run", action="store_true", help="Analyze only, no orders")
    parser.add_argument("--account", type=str, default="trb", help="Account key (trb/trc)")
    parser.add_argument("--budget", type=float, help=f"Max per order (default: ${MAX_PER_ORDER:.0f})")
    parser.add_argument("--max-total", type=float, help=f"Max total options exposure (default: ${MAX_TOTAL_OPTIONS:.0f})")
    parser.add_argument("--daily", action="store_true", help="Afternoon cycle: review + scan + place GTC buy orders at dip prices")
    parser.add_argument("--premarket", action="store_true", help="Pre-market: re-scan prices + adjust + fire pending plan orders")
    parser.add_argument("--review", action="store_true", help="Review only, no orders")
    args = parser.parse_args()
    if args.daily:
        asyncio.run(run_daily_cycle(args))
    elif args.premarket:
        asyncio.run(run_premarket(args))
    else:
        asyncio.run(run_agent(args))


if __name__ == "__main__":
    main()
