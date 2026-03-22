#!/usr/bin/env python3
"""
TradierHedgeEngine + STRICT_NO_LOSS Backtest System
====================================================
Stocks CANNOT hold same-symbol opposite-side positions.
Uses cross-symbol correlated/inverse hedging.

Architecture:
  1. TradierRatingRegistry — caches per-symbol scores from stoch/RSI/SMA indicators
  2. CorrelationMatrix — computes rolling return correlations between symbols
  3. TradierHedgeEngine — finds correlated hedge candidates, sizes, triggers, exits
  4. Backtester — simulates full system with/without hedge on tradier kline data

Usage:
  nice -n 19 python3 tradier_hedge_engine.py
"""

import json, os, sys, time, sqlite3, math, logging, argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TradierHedge")

KLINES_DIR = Path("/home/niels/binance-sandbox/klines_cache/tradier")
RESULTS_DIR = Path("/home/niels/binance-sandbox/backtest_framework/results")
RESULTS_DIR.mkdir(exist_ok=True)
DB_PATH = RESULTS_DIR / "tradier_hedge_backtest.db"
COMMISSION_PCT = 0.0016  # 0.08% per side = 0.16% roundtrip

# ═══════════════════════════════════════════════════════════════
# SECTOR ETF MAPPING
# ═══════════════════════════════════════════════════════════════
SECTOR_MAP = {
    "Tech": ["QQQ"],
    "Finance": ["JPM", "BK", "SCHW", "MA", "V"],
    "Energy": ["XOM", "OXY", "USO"],
    "Healthcare": ["UNH", "JNJ", "PFE", "LLY", "MRK", "ABBV", "ABT", "GILD", "MDT", "CVS"],
    "Consumer": ["COST", "WMT", "HD", "LOW", "TGT", "MCD", "SBUX", "NKE", "ULTA", "KO", "PEP", "CLX", "STZ", "MO"],
    "Industrial": ["CAT", "GE", "HON", "BA", "LMT", "RTX", "FDX", "UPS", "BWXT"],
    "Broad": ["SPY", "QQQ"],
}

SYMBOL_TO_SECTOR = {}
for sector, syms in SECTOR_MAP.items():
    for s in syms:
        SYMBOL_TO_SECTOR[s] = sector

# Tech stocks not explicitly in sector map
TECH_SYMBOLS = {"AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA", "AMD", "AVGO", "ADBE", "CRM", "ORCL", "IBM", "QCOM", "MU", "LRCX", "TSM", "ASML", "SAP", "MRVL", "TXN", "NFLX", "SHOP", "SNOW", "SPOT", "RBLX", "RDDT", "PYPL", "SMCI", "CRWD", "ARM", "TTD", "WDAY", "PATH", "DUOL", "FIVN", "ROKU", "OLED", "QBTS", "QUBT", "QLYS", "SNDK", "INOD", "ZETA", "RKLB", "ASTS", "JOBY", "CRWV"}
for s in TECH_SYMBOLS:
    if s not in SYMBOL_TO_SECTOR:
        SYMBOL_TO_SECTOR[s] = "Tech"

# Remaining unclassified → Broad
CRYPTO_ETFS = {"IBIT", "BITO", "BTCL", "ETH", "ETHD", "SBIT"}
for s in CRYPTO_ETFS:
    SYMBOL_TO_SECTOR[s] = "Crypto"

MISC = {"GLD": "Commodity", "SLV": "Commodity", "GOLD": "Commodity", "COPX": "Commodity", "SHY": "Bond", "USO": "Energy", "GME": "Meme", "LYFT": "Transport", "GM": "Auto", "TSLA": "Auto", "USAR": "Broad", "BLOK": "Crypto", "DIME": "Finance", "APO": "Finance", "APA": "Energy", "CME": "Finance", "T": "Telecom", "VZ": "Telecom", "TCEHY": "Tech", "BABA": "Tech", "BIDU": "Tech", "DHR": "Healthcare", "TMO": "Healthcare"}
for s, sec in MISC.items():
    SYMBOL_TO_SECTOR[s] = sec

def get_sector(symbol: str) -> str:
    return SYMBOL_TO_SECTOR.get(symbol, "Broad")

# Best hedge ETFs per sector (opposite direction candidates)
SECTOR_HEDGE_FALLBACK = {
    "Tech": ["QQQ", "SPY"],
    "Finance": ["SPY"],
    "Energy": ["SPY"],
    "Healthcare": ["SPY"],
    "Consumer": ["SPY"],
    "Industrial": ["SPY"],
    "Crypto": ["SPY", "QQQ"],
    "Commodity": ["SPY"],
    "Bond": ["SPY"],
    "Meme": ["SPY", "QQQ"],
    "Transport": ["SPY"],
    "Auto": ["SPY", "QQQ"],
    "Telecom": ["SPY"],
    "Broad": ["QQQ", "SPY"],
}


# ═══════════════════════════════════════════════════════════════
# DATA LOADER
# ═══════════════════════════════════════════════════════════════
def load_klines(symbol: str, tf: str) -> List[Dict]:
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    for c in data:
        ts_str = c.get("timestamp") or c.get("timestamp_dt") or c.get("date", "")
        if ts_str:
            try:
                if "T" in ts_str:
                    c["_dt"] = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace(".000000Z", "+00:00"))
                else:
                    c["_dt"] = datetime.strptime(ts_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                c["_dt"] = None
        else:
            c["_dt"] = None
        c["_close"] = float(c.get("close", 0))
        c["_high"] = float(c.get("high", 0))
        c["_low"] = float(c.get("low", 0))
        c["_open"] = float(c.get("open", 0))
        c["_volume"] = float(c.get("volume", 0))
    data = [c for c in data if c["_dt"] is not None and c["_close"] > 0]
    data.sort(key=lambda x: x["_dt"])
    return data


def compute_indicators(candles: List[Dict], dc_period=20, atr_period=14, stoch_period=14, sma_period=200) -> List[Dict]:
    if len(candles) < max(dc_period, atr_period, stoch_period, sma_period) + 5:
        return candles
    closes = np.array([c["_close"] for c in candles])
    highs = np.array([c["_high"] for c in candles])
    lows = np.array([c["_low"] for c in candles])
    for i in range(len(candles)):
        c = candles[i]
        if i >= dc_period:
            c["dc_high"] = float(np.max(highs[i - dc_period:i]))
            c["dc_low"] = float(np.min(lows[i - dc_period:i]))
            c["dc_basis"] = (c["dc_high"] + c["dc_low"]) / 2
        if i >= atr_period + 1:
            trs = []
            for j in range(i - atr_period, i):
                tr = max(highs[j] - lows[j], abs(highs[j] - closes[j - 1]), abs(lows[j] - closes[j - 1]))
                trs.append(tr)
            c["atr"] = float(np.mean(trs))
        if i >= stoch_period:
            h_max = float(np.max(highs[i - stoch_period:i + 1]))
            l_min = float(np.min(lows[i - stoch_period:i + 1]))
            if h_max > l_min:
                c["stoch_k"] = ((closes[i] - l_min) / (h_max - l_min)) * 100
            else:
                c["stoch_k"] = 50.0
        if i >= stoch_period + 2:
            c["stoch_d"] = np.mean([candles[j].get("stoch_k", 50) for j in range(i - 2, i + 1)])
        if i >= 14:
            gains = []
            losses = []
            for j in range(i - 13, i + 1):
                delta = closes[j] - closes[j - 1]
                gains.append(max(delta, 0))
                losses.append(max(-delta, 0))
            avg_gain = np.mean(gains)
            avg_loss = np.mean(losses)
            if avg_loss > 0:
                rs = avg_gain / avg_loss
                c["rsi"] = 100 - (100 / (1 + rs))
            else:
                c["rsi"] = 100.0
        if i >= sma_period:
            c["sma200"] = float(np.mean(closes[i - sma_period + 1:i + 1]))
    return candles


def is_market_hours(dt: datetime) -> bool:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    et = dt - timedelta(hours=5)  # rough UTC->ET
    if et.weekday() >= 5:
        return False
    h, m = et.hour, et.minute
    if h < 9 or (h == 9 and m < 30):
        return False
    if h >= 16:
        return False
    return True


def minutes_to_close(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    et = dt - timedelta(hours=5)
    close_time = et.replace(hour=16, minute=0, second=0, microsecond=0)
    return (close_time - et).total_seconds() / 60


# ═══════════════════════════════════════════════════════════════
# CORRELATION MATRIX
# ═══════════════════════════════════════════════════════════════
class CorrelationMatrix:
    def __init__(self, symbols: List[str], lookback: int = 60):
        self.symbols = symbols
        self.lookback = lookback
        self.returns: Dict[str, List[float]] = {s: [] for s in symbols}
        self.corr_cache: Dict[Tuple[str, str], float] = {}
        self._dirty = True
    def update(self, symbol: str, ret: float):
        if symbol not in self.returns:
            self.returns[symbol] = []
        self.returns[symbol].append(ret)
        if len(self.returns[symbol]) > self.lookback:
            self.returns[symbol] = self.returns[symbol][-self.lookback:]
        self._dirty = True
    def recompute(self):
        if not self._dirty:
            return
        self.corr_cache.clear()
        sym_list = [s for s in self.symbols if len(self.returns.get(s, [])) >= 20]
        for i, s1 in enumerate(sym_list):
            for s2 in sym_list[i + 1:]:
                r1 = np.array(self.returns[s1][-self.lookback:])
                r2 = np.array(self.returns[s2][-self.lookback:])
                min_len = min(len(r1), len(r2))
                if min_len < 20:
                    continue
                r1 = r1[-min_len:]
                r2 = r2[-min_len:]
                std1, std2 = np.std(r1), np.std(r2)
                if std1 < 1e-10 or std2 < 1e-10:
                    continue
                corr = float(np.corrcoef(r1, r2)[0, 1])
                self.corr_cache[(s1, s2)] = corr
                self.corr_cache[(s2, s1)] = corr
        self._dirty = False
    def get_correlation(self, s1: str, s2: str) -> float:
        if s1 == s2:
            return 1.0
        return self.corr_cache.get((s1, s2), 0.0)
    def get_most_negatively_correlated(self, symbol: str, exclude: set = None, top_n: int = 10) -> List[Tuple[str, float]]:
        self.recompute()
        candidates = []
        for s in self.symbols:
            if s == symbol:
                continue
            if exclude and s in exclude:
                continue
            corr = self.get_correlation(symbol, s)
            candidates.append((s, corr))
        candidates.sort(key=lambda x: x[1])
        return candidates[:top_n]


# ═══════════════════════════════════════════════════════════════
# POSITION DATA
# ═══════════════════════════════════════════════════════════════
@dataclass
class Position:
    symbol: str
    side: str  # "LONG" or "SHORT"
    entry_price: float
    qty: float  # always positive, shares
    entry_time: datetime = None
    is_hedge: bool = False
    hedge_for: str = ""  # position_key of the losing position this hedges
    hedge_id: str = ""
    max_gain_pct: float = 0.0
    status: str = "active"  # active, closed
    close_price: float = 0.0
    close_time: datetime = None
    realized_pnl: float = 0.0
    def notional(self, price: float = None) -> float:
        p = price if price else self.entry_price
        return self.qty * p
    def pnl_pct(self, current_price: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        if self.side == "LONG":
            return ((current_price - self.entry_price) / self.entry_price) * 100
        else:
            return ((self.entry_price - current_price) / self.entry_price) * 100
    def pnl_usd(self, current_price: float) -> float:
        return self.qty * self.entry_price * (self.pnl_pct(current_price) / 100.0)
    @property
    def key(self) -> str:
        return f"{self.symbol}_{self.side}"


# ═══════════════════════════════════════════════════════════════
# TRADIER RATING REGISTRY (Simplified for backtest)
# ═══════════════════════════════════════════════════════════════
class TradierRatingRegistry:
    def __init__(self):
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.top_longs: List[Tuple[str, float]] = []
        self.top_shorts: List[Tuple[str, float]] = []
    def update_from_candle(self, symbol: str, candle: Dict):
        stoch_k = candle.get("stoch_k", 50)
        stoch_d = candle.get("stoch_d", 50)
        rsi = candle.get("rsi", 50)
        close = candle.get("_close", 0)
        sma200 = candle.get("sma200", 0)
        dc_low = candle.get("dc_low", 0)
        dc_high = candle.get("dc_high", 0)
        dc_basis = candle.get("dc_basis", 0)
        atr = candle.get("atr", 0)
        long_score = 0
        short_score = 0
        # Stoch oversold → long candidate
        if stoch_k < 25:
            long_score += 5
        if stoch_k < 15:
            long_score += 5
        if stoch_k > stoch_d and stoch_k < 40:
            long_score += 3  # bullish cross in oversold
        if rsi < 35:
            long_score += 3
        if dc_low > 0 and dc_high > dc_low and close <= dc_low + (dc_high - dc_low) * 0.2:
            long_score += 4
        if sma200 > 0 and close > sma200:
            long_score += 2  # above SMA200 = trend support
        # Stoch overbought → short candidate
        if stoch_k > 75:
            short_score += 5
        if stoch_k > 85:
            short_score += 5
        if stoch_k < stoch_d and stoch_k > 60:
            short_score += 3  # bearish cross in overbought
        if rsi > 65:
            short_score += 3
        if dc_low > 0 and dc_high > dc_low and close >= dc_low + (dc_high - dc_low) * 0.8:
            short_score += 4
        if sma200 > 0 and close < sma200:
            short_score += 2
        self.cache[symbol] = {"long_score": long_score, "short_score": short_score, "stoch_k": stoch_k, "stoch_d": stoch_d, "rsi": rsi, "close": close, "sma200": sma200, "dc_low": dc_low, "dc_high": dc_high, "atr": atr}
    def refresh_tops(self):
        longs = [(s, d["long_score"]) for s, d in self.cache.items() if d["long_score"] >= 8]
        shorts = [(s, d["short_score"]) for s, d in self.cache.items() if d["short_score"] >= 8]
        longs.sort(key=lambda x: x[1], reverse=True)
        shorts.sort(key=lambda x: x[1], reverse=True)
        self.top_longs = longs[:50]
        self.top_shorts = shorts[:50]
    def get_hedge_score(self, symbol: str, target_side: str) -> float:
        d = self.cache.get(symbol)
        if not d:
            return 0
        if target_side == "LONG":
            return d["long_score"]
        return d["short_score"]


# ═══════════════════════════════════════════════════════════════
# TRADIER HEDGE ENGINE
# ═══════════════════════════════════════════════════════════════
class TradierHedgeEngine:
    def __init__(self, registry: TradierRatingRegistry, corr_matrix: CorrelationMatrix, all_symbols: List[str]):
        self.registry = registry
        self.corr_matrix = corr_matrix
        self.all_symbols = set(all_symbols)
        self.hedge_cooldowns: Dict[str, datetime] = {}
        self.HEDGE_COOLDOWN_SEC = 420  # 7 min
        self.active_hedges: List[Dict] = []
        self._hedge_counter = 0
    def _dynamic_ratio(self, loss_pct: float) -> float:
        loss = abs(loss_pct)
        if loss >= 5.0:
            return 1.5
        if loss >= 2.0:
            return 1.2
        if loss >= 1.0:
            return 1.0
        if loss >= 0.3:
            return 0.5
        return 0.3
    def find_hedge_candidates(self, losing_symbol: str, losing_side: str, current_prices: Dict[str, float], open_positions: Dict[str, Position], current_time: datetime) -> List[Tuple[str, float, str]]:
        """Find best hedge candidates. Returns list of (symbol, score, side)."""
        target_side = "SHORT" if losing_side == "LONG" else "LONG"
        # Clean cooldowns
        for k in list(self.hedge_cooldowns.keys()):
            if (current_time - self.hedge_cooldowns[k]).total_seconds() > self.HEDGE_COOLDOWN_SEC:
                del self.hedge_cooldowns[k]
        exclude = {losing_symbol}
        for k, cd in self.hedge_cooldowns.items():
            exclude.add(k)
        # Don't hedge with symbols we already have open positions on same side
        for pk, pos in open_positions.items():
            if pos.status == "active" and pos.side == target_side:
                exclude.add(pos.symbol)
        candidates = []
        # STRATEGY 1: Negatively correlated symbols from correlation matrix
        neg_corr = self.corr_matrix.get_most_negatively_correlated(losing_symbol, exclude=exclude, top_n=15)
        for sym, corr in neg_corr:
            if sym in exclude:
                continue
            if sym not in current_prices or current_prices[sym] <= 0:
                continue
            neg_strength = max(0, -corr)  # 0 to 1, higher = more negative correlation
            stoch_score = self.registry.get_hedge_score(sym, target_side)
            # Combined score: correlation strength (40%) + stoch readiness (40%) + volume proxy (20%)
            combined = neg_strength * 0.4 + min(stoch_score / 20.0, 1.0) * 0.4 + 0.2
            if combined > 0.2:
                candidates.append((sym, combined, target_side))
        # STRATEGY 2: Same-sector opposite move (sector ETF)
        sector = get_sector(losing_symbol)
        fallbacks = SECTOR_HEDGE_FALLBACK.get(sector, ["SPY"])
        for fb_sym in fallbacks:
            if fb_sym in exclude or fb_sym not in current_prices:
                continue
            stoch_score = self.registry.get_hedge_score(fb_sym, target_side)
            combined = 0.3 + min(stoch_score / 20.0, 1.0) * 0.3
            candidates.append((fb_sym, combined * 0.8, target_side))  # slight penalty for generic
        # STRATEGY 3: SPY/QQQ last resort
        for broad in ["SPY", "QQQ"]:
            if broad in exclude or broad not in current_prices:
                continue
            already_added = any(c[0] == broad for c in candidates)
            if not already_added:
                candidates.append((broad, 0.15, target_side))
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:5]
    def should_trigger_hedge(self, pos: Position, current_price: float, candle_1h: Dict) -> Tuple[bool, str]:
        """Check if hedge should be triggered for a losing position."""
        pnl = pos.pnl_pct(current_price)
        if pnl >= -0.3:
            return False, ""
        stoch_k = candle_1h.get("stoch_k", 50)
        stoch_d = candle_1h.get("stoch_d", 50)
        # 1h stoch crosses against position
        if pos.side == "LONG" and stoch_k < stoch_d and pnl < -0.3:
            return True, f"LOSS_{pnl:.2f}%_STOCH_BEARISH_CROSS"
        if pos.side == "SHORT" and stoch_k > stoch_d and pnl < -0.3:
            return True, f"LOSS_{pnl:.2f}%_STOCH_BULLISH_CROSS"
        # Deep loss override — hedge regardless of stoch
        if pnl < -1.0:
            return True, f"DEEP_LOSS_{pnl:.2f}%_FORCED"
        return False, ""
    def compute_hedge_size(self, losing_value: float, loss_pct: float, max_notional: float = 50000) -> float:
        ratio = self._dynamic_ratio(loss_pct)
        size = losing_value * ratio
        return min(size, max_notional)
    def should_exit_hedge(self, hedge_pos: Position, original_pos: Position, current_prices: Dict[str, float], current_time: datetime, candle_data: Dict[str, Dict] = None) -> Tuple[bool, str]:
        """Check if hedge should be closed."""
        h_price = current_prices.get(hedge_pos.symbol, 0)
        o_price = current_prices.get(original_pos.symbol, 0)
        if h_price <= 0 or o_price <= 0:
            return False, ""
        h_pnl = hedge_pos.pnl_pct(h_price)
        o_pnl = original_pos.pnl_pct(o_price)
        # 1. Original recovered to breakeven
        if o_pnl >= 0.05:
            return True, f"ORIGINAL_RECOVERED_{o_pnl:.2f}%"
        # 2. Hedge is losing > -0.15% (liability kill)
        if h_pnl < -0.15:
            return True, f"HEDGE_LIABILITY_KILL_{h_pnl:.2f}%"
        # 3. 15 min before market close
        mtc = minutes_to_close(current_time)
        if 0 < mtc <= 15:
            return True, f"MARKET_CLOSE_FORCED_{mtc:.0f}min"
        # 4. Hedge significant drop from peak
        if hedge_pos.max_gain_pct > 0.3 and (hedge_pos.max_gain_pct - h_pnl) > 0.2:
            return True, f"PEAK_DECAY_{hedge_pos.max_gain_pct:.2f}%_to_{h_pnl:.2f}%"
        return False, ""
    def create_hedge_id(self) -> str:
        self._hedge_counter += 1
        return f"TH_{self._hedge_counter}"


# ═══════════════════════════════════════════════════════════════
# STRICT_NO_LOSS EXIT LOGIC
# ═══════════════════════════════════════════════════════════════
class StrictNoLossPolicy:
    """NEVER close a position at a loss. Only at breakeven or profit.
    Exception: positions held > MAX_HOLD_DAYS with deep loss get a controlled exit
    to avoid unlimited portfolio drag."""
    MAX_HOLD_DAYS = 999  # Effectively disabled — true STRICT_NO_LOSS
    MAX_LOSS_FORCED_EXIT = -99.0  # Effectively disabled
    @staticmethod
    def can_close(pos: Position, current_price: float, current_time: datetime = None) -> Tuple[bool, str]:
        pnl = pos.pnl_pct(current_price)
        if pnl >= 0.05:  # 0.05% buffer above breakeven (covers commission)
            return True, f"PROFIT_{pnl:.2f}%"
        # Time-based escape: after MAX_HOLD_DAYS, allow loss exit if deep enough
        if current_time and pos.entry_time:
            hold_days = (current_time - pos.entry_time).total_seconds() / 86400
            if hold_days > StrictNoLossPolicy.MAX_HOLD_DAYS and pnl < StrictNoLossPolicy.MAX_LOSS_FORCED_EXIT:
                return True, f"TIME_EXIT_{hold_days:.0f}d_loss_{pnl:.2f}%"
            # (Extended time exit disabled — true STRICT_NO_LOSS)
        return False, f"STRICT_NO_LOSS_BLOCK_{pnl:.2f}%"
    @staticmethod
    def should_take_profit(pos: Position, current_price: float) -> Tuple[bool, str]:
        pnl = pos.pnl_pct(current_price)
        # DC-based trailing stop logic
        if pos.max_gain_pct > 1.5 and pnl < pos.max_gain_pct * 0.5:
            return True, f"TRAILING_STOP_{pnl:.2f}%_from_peak_{pos.max_gain_pct:.2f}%"
        if pnl > 3.0:
            return True, f"TAKE_PROFIT_{pnl:.2f}%"
        return False, ""


# ═══════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════
@dataclass
class BacktestConfig:
    initial_capital: float = 100000.0
    position_size_pct: float = 0.02  # 2% of capital per position
    max_positions: int = 20
    hedge_enabled: bool = True
    strict_no_loss: bool = True
    entry_score_threshold: int = 12
    max_hedge_per_position: int = 1


@dataclass
class BacktestStats:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    hedge_count: int = 0
    hedge_wins: int = 0
    hedge_losses: int = 0
    hedge_pnl: float = 0.0
    positions_recovered_by_hedge: int = 0
    strict_no_loss_blocks: int = 0
    forced_market_close_exits: int = 0
    peak_equity: float = 0.0
    avg_hold_time_hours: float = 0.0
    ls_ratio_violations: int = 0
    total_commission: float = 0.0
    trades: List[Dict] = field(default_factory=list)


class TradierBacktester:
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.symbols: List[str] = []
        self.klines_1h: Dict[str, List[Dict]] = {}
        self.klines_D: Dict[str, List[Dict]] = {}
        self.current_prices: Dict[str, float] = {}
        self.positions: Dict[str, Position] = {}  # key = symbol_SIDE
        self.closed_positions: List[Position] = []
        self.equity = config.initial_capital
        self.peak_equity = config.initial_capital
        self.stats = BacktestStats()
        self.stats.peak_equity = config.initial_capital
        self.registry = TradierRatingRegistry()
        self.corr_matrix = None
        self.hedge_engine = None
        self.no_loss = StrictNoLossPolicy()
        self.equity_curve: List[Tuple[datetime, float]] = []
        self._entry_cooldowns: Dict[str, datetime] = {}
        self._prev_candle_idx: Dict[str, int] = {}
    def load_data(self):
        logger.info("Loading kline data...")
        available = set()
        for f in KLINES_DIR.iterdir():
            if f.suffix == ".json":
                parts = f.stem.rsplit("_", 1)
                if len(parts) == 2:
                    available.add(parts[0])
        self.symbols = sorted(available)
        logger.info(f"Found {len(self.symbols)} symbols")
        loaded = 0
        for sym in self.symbols:
            k1h = load_klines(sym, "1h")
            if len(k1h) >= 250:
                k1h = compute_indicators(k1h)
                self.klines_1h[sym] = k1h
                loaded += 1
            kd = load_klines(sym, "D")
            if len(kd) >= 250:
                kd = compute_indicators(kd, sma_period=200)
                self.klines_D[sym] = kd
        self.symbols = [s for s in self.symbols if s in self.klines_1h]
        logger.info(f"Loaded 1h klines for {loaded} symbols, D klines for {len(self.klines_D)} symbols")
        self.corr_matrix = CorrelationMatrix(self.symbols, lookback=60)
        self.hedge_engine = TradierHedgeEngine(self.registry, self.corr_matrix, self.symbols)
    def _build_time_index(self) -> List[datetime]:
        all_times = set()
        for sym, candles in self.klines_1h.items():
            for c in candles:
                if c["_dt"]:
                    all_times.add(c["_dt"])
        sorted_times = sorted(all_times)
        return sorted_times
    def _get_candle_at_time(self, symbol: str, dt: datetime) -> Optional[Dict]:
        candles = self.klines_1h.get(symbol, [])
        if not candles:
            return None
        idx = self._prev_candle_idx.get(symbol, 0)
        # Advance index
        while idx < len(candles) - 1 and candles[idx + 1]["_dt"] <= dt:
            idx += 1
        if idx < len(candles) and candles[idx]["_dt"] <= dt:
            self._prev_candle_idx[symbol] = idx
            return candles[idx]
        return None
    def _get_daily_candle(self, symbol: str, dt: datetime) -> Optional[Dict]:
        candles = self.klines_D.get(symbol, [])
        if not candles:
            return None
        target_date = dt.date()
        for c in reversed(candles):
            if c["_dt"].date() <= target_date:
                return c
        return None
    def _count_open(self, side: str = None) -> int:
        count = 0
        for pk, pos in self.positions.items():
            if pos.status == "active":
                if side is None or pos.side == side:
                    count += 1
        return count
    def _ls_ratio(self) -> float:
        longs = self._count_open("LONG")
        shorts = self._count_open("SHORT")
        total = longs + shorts
        if total == 0:
            return 0.5
        return longs / total
    def _should_enter(self, symbol: str, side: str, candle: Dict, daily: Dict, current_time: datetime) -> bool:
        pk = f"{symbol}_{side}"
        if pk in self.positions and self.positions[pk].status == "active":
            return False
        # Cooldown
        if pk in self._entry_cooldowns:
            if (current_time - self._entry_cooldowns[pk]).total_seconds() < 3600:
                return False
        score = self.registry.get_hedge_score(symbol, side)
        if score < self.config.entry_score_threshold:
            return False
        stoch_k = candle.get("stoch_k", 50)
        stoch_d = candle.get("stoch_d", 50)
        rsi = candle.get("rsi", 50)
        sma200 = (daily or {}).get("sma200", 0)
        close = candle["_close"]
        if side == "LONG":
            if stoch_k > 70:
                return False
            if stoch_k < stoch_d:
                return False  # need bullish cross
            if sma200 > 0 and close < sma200 * 0.95:
                return False  # too far below SMA200
        else:
            if stoch_k < 30:
                return False
            if stoch_k > stoch_d:
                return False  # need bearish cross
            if sma200 > 0 and close > sma200 * 1.05:
                return False  # too far above SMA200
        # L/S ratio enforcement — keep 35-65% long
        ratio = self._ls_ratio()
        if side == "LONG" and ratio > 0.65:
            return False
        if side == "SHORT" and ratio < 0.35:
            return False
        # Max positions
        if self._count_open() >= self.config.max_positions:
            return False
        # Market hours
        if not is_market_hours(current_time):
            return False
        # Not too close to market close (30 min buffer for new entries)
        if minutes_to_close(current_time) < 30:
            return False
        return True
    def _open_position(self, symbol: str, side: str, price: float, current_time: datetime, is_hedge: bool = False, hedge_for: str = "", notional_override: float = 0) -> Optional[Position]:
        if notional_override > 0:
            notional = notional_override
        else:
            notional = self.equity * self.config.position_size_pct
        qty = notional / price
        if qty <= 0:
            return None
        commission = notional * COMMISSION_PCT
        self.equity -= commission
        self.stats.total_commission += commission
        pos = Position(symbol=symbol, side=side, entry_price=price, qty=qty, entry_time=current_time, is_hedge=is_hedge, hedge_for=hedge_for, hedge_id=self.hedge_engine.create_hedge_id() if is_hedge else "")
        pk = f"{symbol}_{side}"
        self.positions[pk] = pos
        self._entry_cooldowns[pk] = current_time
        return pos
    def _close_position(self, pk: str, price: float, current_time: datetime, reason: str) -> float:
        pos = self.positions.get(pk)
        if not pos or pos.status != "active":
            return 0.0
        pnl_pct = pos.pnl_pct(price)
        pnl_usd = pos.pnl_usd(price)
        commission = pos.notional(price) * COMMISSION_PCT
        self.equity += pnl_usd - commission
        self.stats.total_commission += commission
        pos.status = "closed"
        pos.close_price = price
        pos.close_time = current_time
        pos.realized_pnl = pnl_usd - commission
        self.closed_positions.append(pos)
        del self.positions[pk]
        self.stats.total_trades += 1
        if pnl_usd - commission > 0.01:
            self.stats.winning_trades += 1
        elif pnl_usd - commission < -0.01:
            self.stats.losing_trades += 1
        else:
            self.stats.breakeven_trades += 1
        self.stats.total_pnl += pnl_usd - commission
        self.stats.trades.append({"symbol": pos.symbol, "side": pos.side, "entry_price": pos.entry_price, "exit_price": price, "pnl_pct": pnl_pct, "pnl_usd": pnl_usd - commission, "is_hedge": pos.is_hedge, "reason": reason, "entry_time": str(pos.entry_time), "exit_time": str(current_time), "hold_hours": (current_time - pos.entry_time).total_seconds() / 3600 if pos.entry_time else 0})
        return pnl_usd - commission
    def _process_hedges(self, current_time: datetime):
        """Main hedge logic: scan losers → find candidates → open/close hedges."""
        if not self.config.hedge_enabled:
            return
        # 1. Check existing hedges for exit
        hedges_to_close = []
        for pk, pos in list(self.positions.items()):
            if not pos.is_hedge or pos.status != "active":
                continue
            original_pk = pos.hedge_for
            original_pos = self.positions.get(original_pk)
            if not original_pos or original_pos.status != "active":
                hedges_to_close.append((pk, "ORPHAN_HEDGE", 0))
                continue
            should_exit, reason = self.hedge_engine.should_exit_hedge(pos, original_pos, self.current_prices, current_time)
            if should_exit:
                hedges_to_close.append((pk, reason, 0))
        for pk, reason, exit_px in hedges_to_close:
            pos = self.positions.get(pk)
            if not pos:
                continue
            price = exit_px if exit_px > 0 else self.current_prices.get(pos.symbol, pos.entry_price)
            pnl = self._close_position(pk, price, current_time, reason)
            self.stats.hedge_count += 1
            if pnl > 0:
                self.stats.hedge_wins += 1
            else:
                self.stats.hedge_losses += 1
            self.stats.hedge_pnl += pnl
            # Check if original recovered
            original_pk = pos.hedge_for
            original_pos = self.positions.get(original_pk)
            if original_pos and original_pos.status == "active":
                o_price = self.current_prices.get(original_pos.symbol, 0)
                if o_price > 0 and original_pos.pnl_pct(o_price) >= 0:
                    self.stats.positions_recovered_by_hedge += 1
            # Cooldown
            self.hedge_engine.hedge_cooldowns[pos.symbol] = current_time
        # 2. Scan losers for new hedges
        for pk, pos in list(self.positions.items()):
            if pos.is_hedge or pos.status != "active":
                continue
            price = self.current_prices.get(pos.symbol, 0)
            if price <= 0:
                continue
            # Already has a hedge?
            has_hedge = any(h.status == "active" and h.hedge_for == pk for h in self.positions.values() if h.is_hedge)
            if has_hedge:
                continue
            candle = self._get_candle_at_time(pos.symbol, current_time)
            if not candle:
                continue
            should_hedge, reason = self.hedge_engine.should_trigger_hedge(pos, price, candle)
            if not should_hedge:
                continue
            pnl_pct = pos.pnl_pct(price)
            losing_value = pos.notional(price)
            candidates = self.hedge_engine.find_hedge_candidates(pos.symbol, pos.side, self.current_prices, self.positions, current_time)
            if not candidates:
                continue
            # Pick best candidate
            best_sym, best_score, target_side = candidates[0]
            hedge_price = self.current_prices.get(best_sym, 0)
            if hedge_price <= 0:
                continue
            # Check we don't already have this symbol open
            hedge_pk = f"{best_sym}_{target_side}"
            if hedge_pk in self.positions and self.positions[hedge_pk].status == "active":
                # Try next candidate
                opened = False
                for sym, sc, ts in candidates[1:]:
                    hpk = f"{sym}_{ts}"
                    if hpk not in self.positions or self.positions[hpk].status != "active":
                        hp = self.current_prices.get(sym, 0)
                        if hp > 0:
                            hedge_size = self.hedge_engine.compute_hedge_size(losing_value, pnl_pct)
                            h_pos = self._open_position(sym, ts, hp, current_time, is_hedge=True, hedge_for=pk, notional_override=hedge_size)
                            if h_pos:
                                opened = True
                                break
                if not opened:
                    continue
            else:
                hedge_size = self.hedge_engine.compute_hedge_size(losing_value, pnl_pct)
                self._open_position(best_sym, target_side, hedge_price, current_time, is_hedge=True, hedge_for=pk, notional_override=hedge_size)
    def _process_exits(self, current_time: datetime):
        """Check all positions for exit signals."""
        to_close = []
        for pk, pos in list(self.positions.items()):
            if pos.status != "active" or pos.is_hedge:
                continue
            price = self.current_prices.get(pos.symbol, 0)
            if price <= 0:
                continue
            pnl = pos.pnl_pct(price)
            # Update max gain
            if pnl > pos.max_gain_pct:
                pos.max_gain_pct = pnl
            # STRICT_NO_LOSS check
            if self.config.strict_no_loss:
                can_close, reason = self.no_loss.can_close(pos, price, current_time)
                if not can_close:
                    self.stats.strict_no_loss_blocks += 1
                    continue
                should_tp, tp_reason = self.no_loss.should_take_profit(pos, price)
                if should_tp:
                    to_close.append((pk, price, tp_reason))
                    continue
                # Time-based exit was allowed by can_close → close it
                if "TIME_EXIT" in reason:
                    to_close.append((pk, price, reason))
                    continue
                # If profitable but no TP trigger, check trailing
                if pnl > 0.5:
                    if pos.max_gain_pct > 0.8 and pnl < pos.max_gain_pct * 0.6:
                        to_close.append((pk, price, f"TRAILING_{pnl:.2f}%_from_{pos.max_gain_pct:.2f}%"))
                        continue
            else:
                # Without strict no-loss: simple stop/TP
                if pnl < -2.0:
                    to_close.append((pk, price, f"STOP_LOSS_{pnl:.2f}%"))
                elif pnl > 3.0:
                    to_close.append((pk, price, f"TAKE_PROFIT_{pnl:.2f}%"))
                elif pos.max_gain_pct > 1.0 and pnl < pos.max_gain_pct * 0.5:
                    to_close.append((pk, price, f"TRAILING_{pnl:.2f}%"))
            # Market close force exit for hedges handled in _process_hedges
            # For regular positions: close if 5 min to close and profitable
            mtc = minutes_to_close(current_time)
            if 0 < mtc <= 5 and pnl > 0:
                to_close.append((pk, price, f"MARKET_CLOSE_{mtc:.0f}min"))
                self.stats.forced_market_close_exits += 1
        for pk, price, reason in to_close:
            self._close_position(pk, price, current_time, reason)
    def _process_entries(self, current_time: datetime):
        """Scan for new entry opportunities."""
        for sym in self.symbols:
            candle = self._get_candle_at_time(sym, current_time)
            if not candle:
                continue
            daily = self._get_daily_candle(sym, current_time)
            price = candle["_close"]
            self.current_prices[sym] = price
            # Update registry
            self.registry.update_from_candle(sym, candle)
            # Compute returns for correlation
            if len(self.klines_1h.get(sym, [])) > 1:
                idx = self._prev_candle_idx.get(sym, 0)
                candles = self.klines_1h[sym]
                if idx > 0 and idx < len(candles):
                    prev_close = candles[idx - 1]["_close"]
                    if prev_close > 0:
                        ret = (price - prev_close) / prev_close
                        self.corr_matrix.update(sym, ret)
            for side in ["LONG", "SHORT"]:
                if self._should_enter(sym, side, candle, daily, current_time):
                    self._open_position(sym, side, price, current_time)
    def _update_max_gains(self):
        """Update max_gain_pct for ALL open positions including hedges."""
        for pk, pos in self.positions.items():
            if pos.status != "active":
                continue
            price = self.current_prices.get(pos.symbol, 0)
            if price <= 0:
                continue
            pnl = pos.pnl_pct(price)
            if pnl > pos.max_gain_pct:
                pos.max_gain_pct = pnl
    def run(self) -> BacktestStats:
        self.load_data()
        time_index = self._build_time_index()
        logger.info(f"Time index: {len(time_index)} candles from {time_index[0]} to {time_index[-1]}")
        # Skip first 250 candles for indicator warmup
        warmup = 250
        if len(time_index) <= warmup:
            logger.error("Not enough data for warmup")
            return self.stats
        # Warmup phase: just update prices/indicators
        logger.info(f"Warming up indicators ({warmup} candles)...")
        for i in range(warmup):
            dt = time_index[i]
            for sym in self.symbols:
                candle = self._get_candle_at_time(sym, dt)
                if candle:
                    self.current_prices[sym] = candle["_close"]
                    self.registry.update_from_candle(sym, candle)
                    if i > 0:
                        idx = self._prev_candle_idx.get(sym, 0)
                        candles = self.klines_1h.get(sym, [])
                        if idx > 0 and idx < len(candles):
                            prev = candles[idx - 1]["_close"]
                            if prev > 0:
                                self.corr_matrix.update(sym, (candle["_close"] - prev) / prev)
        # Recompute correlation once after warmup
        self.corr_matrix.recompute()
        self.registry.refresh_tops()
        logger.info("Warmup done. Starting trading simulation...")
        trade_candles = time_index[warmup:]
        report_interval = max(1, len(trade_candles) // 20)
        corr_recompute_interval = 24  # every 24 candles (~24h)
        for i, dt in enumerate(trade_candles):
            # Update prices
            for sym in self.symbols:
                candle = self._get_candle_at_time(sym, dt)
                if candle:
                    self.current_prices[sym] = candle["_close"]
            # Update max gains
            self._update_max_gains()
            # Process in order: exits → hedges → entries
            self._process_exits(dt)
            self._process_hedges(dt)
            if is_market_hours(dt):
                self._process_entries(dt)
            # Periodic correlation recompute
            if i % corr_recompute_interval == 0:
                self.corr_matrix.recompute()
                self.registry.refresh_tops()
            # Equity tracking
            unrealized = 0.0
            for pk, pos in self.positions.items():
                if pos.status == "active":
                    p = self.current_prices.get(pos.symbol, pos.entry_price)
                    unrealized += pos.pnl_usd(p)
            current_equity = self.config.initial_capital + self.stats.total_pnl + unrealized
            if current_equity > self.peak_equity:
                self.peak_equity = current_equity
            dd = (self.peak_equity - current_equity) / self.peak_equity * 100 if self.peak_equity > 0 else 0
            if dd > self.stats.max_drawdown:
                self.stats.max_drawdown = dd
            self.equity_curve.append((dt, current_equity))
            if i % report_interval == 0:
                open_count = self._count_open()
                hedge_count = sum(1 for p in self.positions.values() if p.is_hedge and p.status == "active")
                logger.info(f"[{i}/{len(trade_candles)}] {dt.strftime('%Y-%m-%d %H:%M')} | Equity: ${current_equity:,.0f} | PnL: ${self.stats.total_pnl:,.0f} | Open: {open_count} (H:{hedge_count}) | Trades: {self.stats.total_trades} | DD: {dd:.1f}%")
        # Close remaining positions at last price
        logger.info("Closing remaining positions...")
        final_time = trade_candles[-1]
        for pk in list(self.positions.keys()):
            pos = self.positions[pk]
            if pos.status == "active":
                price = self.current_prices.get(pos.symbol, pos.entry_price)
                self._close_position(pk, price, final_time, "BACKTEST_END")
        # Compute avg hold time
        hold_times = [t["hold_hours"] for t in self.stats.trades if t.get("hold_hours", 0) > 0]
        self.stats.avg_hold_time_hours = np.mean(hold_times) if hold_times else 0
        self.stats.peak_equity = self.peak_equity
        return self.stats


# ═══════════════════════════════════════════════════════════════
# RESULTS STORAGE
# ═══════════════════════════════════════════════════════════════
def save_results_to_db(stats_with: BacktestStats, stats_without: BacktestStats):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS backtest_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        mode TEXT,
        total_trades INTEGER,
        winning_trades INTEGER,
        losing_trades INTEGER,
        breakeven_trades INTEGER,
        total_pnl REAL,
        max_drawdown REAL,
        hedge_count INTEGER,
        hedge_wins INTEGER,
        hedge_losses INTEGER,
        hedge_pnl REAL,
        positions_recovered INTEGER,
        strict_no_loss_blocks INTEGER,
        forced_close_exits INTEGER,
        peak_equity REAL,
        avg_hold_hours REAL,
        total_commission REAL,
        win_rate REAL,
        profit_factor REAL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER,
        symbol TEXT,
        side TEXT,
        entry_price REAL,
        exit_price REAL,
        pnl_pct REAL,
        pnl_usd REAL,
        is_hedge INTEGER,
        reason TEXT,
        entry_time TEXT,
        exit_time TEXT,
        hold_hours REAL,
        FOREIGN KEY (run_id) REFERENCES backtest_runs(id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS comparison (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        with_hedge_pnl REAL,
        without_hedge_pnl REAL,
        hedge_advantage REAL,
        with_hedge_dd REAL,
        without_hedge_dd REAL,
        dd_improvement REAL,
        with_hedge_trades INTEGER,
        without_hedge_trades INTEGER,
        positions_recovered INTEGER,
        hedge_win_rate REAL
    )""")
    ts = datetime.now(timezone.utc).isoformat()
    def insert_run(stats, mode):
        total = stats.winning_trades + stats.losing_trades
        win_rate = (stats.winning_trades / total * 100) if total > 0 else 0
        wins_pnl = sum(t["pnl_usd"] for t in stats.trades if t["pnl_usd"] > 0)
        loss_pnl = abs(sum(t["pnl_usd"] for t in stats.trades if t["pnl_usd"] < 0))
        pf = (wins_pnl / loss_pnl) if loss_pnl > 0 else 999
        c.execute("""INSERT INTO backtest_runs (timestamp, mode, total_trades, winning_trades, losing_trades, breakeven_trades, total_pnl, max_drawdown, hedge_count, hedge_wins, hedge_losses, hedge_pnl, positions_recovered, strict_no_loss_blocks, forced_close_exits, peak_equity, avg_hold_hours, total_commission, win_rate, profit_factor) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (ts, mode, stats.total_trades, stats.winning_trades, stats.losing_trades, stats.breakeven_trades, stats.total_pnl, stats.max_drawdown, stats.hedge_count, stats.hedge_wins, stats.hedge_losses, stats.hedge_pnl, stats.positions_recovered_by_hedge, stats.strict_no_loss_blocks, stats.forced_market_close_exits, stats.peak_equity, stats.avg_hold_time_hours, stats.total_commission, win_rate, pf))
        run_id = c.lastrowid
        for t in stats.trades:
            c.execute("""INSERT INTO trades (run_id, symbol, side, entry_price, exit_price, pnl_pct, pnl_usd, is_hedge, reason, entry_time, exit_time, hold_hours) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (run_id, t["symbol"], t["side"], t["entry_price"], t["exit_price"], t["pnl_pct"], t["pnl_usd"], 1 if t["is_hedge"] else 0, t["reason"], t["entry_time"], t["exit_time"], t["hold_hours"]))
        return run_id
    run_with = insert_run(stats_with, "WITH_HEDGE")
    run_without = insert_run(stats_without, "WITHOUT_HEDGE")
    hedge_total = stats_with.hedge_wins + stats_with.hedge_losses
    hedge_wr = (stats_with.hedge_wins / hedge_total * 100) if hedge_total > 0 else 0
    c.execute("""INSERT INTO comparison (timestamp, with_hedge_pnl, without_hedge_pnl, hedge_advantage, with_hedge_dd, without_hedge_dd, dd_improvement, with_hedge_trades, without_hedge_trades, positions_recovered, hedge_win_rate) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (ts, stats_with.total_pnl, stats_without.total_pnl, stats_with.total_pnl - stats_without.total_pnl, stats_with.max_drawdown, stats_without.max_drawdown, stats_without.max_drawdown - stats_with.max_drawdown, stats_with.total_trades, stats_without.total_trades, stats_with.positions_recovered_by_hedge, hedge_wr))
    conn.commit()
    conn.close()
    logger.info(f"Results saved to {DB_PATH}")


def print_comparison(stats_with: BacktestStats, stats_without: BacktestStats):
    def wr(s):
        total = s.winning_trades + s.losing_trades
        return (s.winning_trades / total * 100) if total > 0 else 0
    def pf(s):
        wins = sum(t["pnl_usd"] for t in s.trades if t["pnl_usd"] > 0)
        losses = abs(sum(t["pnl_usd"] for t in s.trades if t["pnl_usd"] < 0))
        return (wins / losses) if losses > 0 else 999
    print("\n" + "=" * 80)
    print("TRADIER HEDGE ENGINE BACKTEST — COMPARISON REPORT")
    print("=" * 80)
    print(f"\n{'Metric':<40} {'WITH HEDGE':>18} {'WITHOUT HEDGE':>18}")
    print("-" * 80)
    print(f"{'Total PnL ($)':<40} {stats_with.total_pnl:>18,.2f} {stats_without.total_pnl:>18,.2f}")
    print(f"{'Total Trades':<40} {stats_with.total_trades:>18} {stats_without.total_trades:>18}")
    print(f"{'Win Rate (%)':<40} {wr(stats_with):>17.1f}% {wr(stats_without):>17.1f}%")
    print(f"{'Profit Factor':<40} {pf(stats_with):>18.2f} {pf(stats_without):>18.2f}")
    print(f"{'Max Drawdown (%)':<40} {stats_with.max_drawdown:>17.1f}% {stats_without.max_drawdown:>17.1f}%")
    print(f"{'Total Commission ($)':<40} {stats_with.total_commission:>18,.2f} {stats_without.total_commission:>18,.2f}")
    print(f"{'Avg Hold Time (hours)':<40} {stats_with.avg_hold_time_hours:>18.1f} {stats_without.avg_hold_time_hours:>18.1f}")
    print(f"{'Winning Trades':<40} {stats_with.winning_trades:>18} {stats_without.winning_trades:>18}")
    print(f"{'Losing Trades':<40} {stats_with.losing_trades:>18} {stats_without.losing_trades:>18}")
    print(f"{'Breakeven Trades':<40} {stats_with.breakeven_trades:>18} {stats_without.breakeven_trades:>18}")
    print(f"{'STRICT_NO_LOSS Blocks':<40} {stats_with.strict_no_loss_blocks:>18} {stats_without.strict_no_loss_blocks:>18}")
    print(f"{'Forced Market Close Exits':<40} {stats_with.forced_market_close_exits:>18} {stats_without.forced_market_close_exits:>18}")
    print()
    print("HEDGE-SPECIFIC METRICS:")
    print("-" * 50)
    print(f"  Hedge Trades:                   {stats_with.hedge_count}")
    print(f"  Hedge Wins:                     {stats_with.hedge_wins}")
    print(f"  Hedge Losses:                   {stats_with.hedge_losses}")
    print(f"  Hedge PnL ($):                  {stats_with.hedge_pnl:,.2f}")
    hw = stats_with.hedge_wins + stats_with.hedge_losses
    print(f"  Hedge Win Rate:                 {(stats_with.hedge_wins / hw * 100) if hw > 0 else 0:.1f}%")
    print(f"  Positions Recovered by Hedge:   {stats_with.positions_recovered_by_hedge}")
    print()
    adv = stats_with.total_pnl - stats_without.total_pnl
    dd_imp = stats_without.max_drawdown - stats_with.max_drawdown
    print(f"HEDGE ADVANTAGE: ${adv:,.2f}")
    print(f"DRAWDOWN IMPROVEMENT: {dd_imp:.1f}%")
    print("=" * 80)
    # Top 10 most profitable trades with hedge
    profitable = sorted(stats_with.trades, key=lambda t: t["pnl_usd"], reverse=True)[:10]
    print("\nTOP 10 PROFITABLE TRADES (WITH HEDGE):")
    for t in profitable:
        print(f"  {t['symbol']:>6} {t['side']:>5} | PnL: ${t['pnl_usd']:>8.2f} ({t['pnl_pct']:>5.2f}%) | {t['reason'][:40]} | Hedge: {t['is_hedge']}")
    # Top 10 worst trades
    worst = sorted(stats_with.trades, key=lambda t: t["pnl_usd"])[:10]
    print("\nTOP 10 WORST TRADES (WITH HEDGE):")
    for t in worst:
        print(f"  {t['symbol']:>6} {t['side']:>5} | PnL: ${t['pnl_usd']:>8.2f} ({t['pnl_pct']:>5.2f}%) | {t['reason'][:40]} | Hedge: {t['is_hedge']}")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="Tradier Hedge Engine Backtest")
    parser.add_argument("--capital", type=float, default=100000, help="Initial capital")
    parser.add_argument("--no-compare", action="store_true", help="Only run WITH hedge")
    args = parser.parse_args()
    t0 = time.time()
    # Run WITH hedge + STRICT_NO_LOSS
    logger.info("=" * 60)
    logger.info("RUN 1: WITH HEDGE + STRICT_NO_LOSS")
    logger.info("=" * 60)
    cfg_with = BacktestConfig(initial_capital=args.capital, hedge_enabled=True, strict_no_loss=True)
    bt_with = TradierBacktester(cfg_with)
    stats_with = bt_with.run()
    if not args.no_compare:
        # Run WITHOUT hedge (standard stop-loss)
        logger.info("\n" + "=" * 60)
        logger.info("RUN 2: WITHOUT HEDGE (Standard Stop-Loss)")
        logger.info("=" * 60)
        cfg_without = BacktestConfig(initial_capital=args.capital, hedge_enabled=False, strict_no_loss=False)
        bt_without = TradierBacktester(cfg_without)
        stats_without = bt_without.run()
    else:
        stats_without = BacktestStats()
    elapsed = time.time() - t0
    logger.info(f"\nTotal backtest time: {elapsed:.1f}s")
    print_comparison(stats_with, stats_without)
    save_results_to_db(stats_with, stats_without)
    logger.info(f"Database: {DB_PATH}")


if __name__ == "__main__":
    main()
