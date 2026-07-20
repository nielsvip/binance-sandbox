#!/usr/bin/env python3
"""
Practical Sizing Backtest: DC_MOMENT qty sizer + Sentiment Contrarian boost
Tests the two strongest 0xxx findings on real kline data.

Test 1: DC_MOMENT as quantity sizer (vs flat $55)
Test 2: SENTIMENT CONTRARIAN boost (2x on extreme, 1.5x on divergence)
Test 3: COMBINED (dc_moment + sentiment)
Test 4: DC_EXPANSION gate (>1.0, >1.5, no gate)

70/30 train/test split, daily Sharpe, L/S ratio tracking, 0.20% RT cost.
"""

import json
import os
import sys
import time
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

KLINES_DIR = Path("/home/niels/binance/klines_cache")
RESULTS_DB = Path("/home/niels/binance-sandbox/backtest_framework/results/ez_practical_sizing.db")
RT_COST = 0.0020  # 0.20% round-trip
BASE_QTY = 55.0
MIN_QTY = 20.0
MAX_QTY = 150.0
MAX_POSITIONS = 20
LS_RATIO_MIN = 0.40
LS_RATIO_MAX = 2.50
STOCH_PERIOD = 14
STOCH_SMOOTH = 3
DC_PERIOD = 20
SENTIMENT_LOOKBACK = 96  # 96 bars = 24h on 15m
HOLD_BARS = 8  # 2 hours on 15m


@dataclass
class Position:
    symbol: str
    side: str  # LONG or SHORT
    entry_price: float
    qty_usd: float
    entry_bar: int
    dc_moment: float = 0.0
    sentiment_local: float = 0.0


@dataclass
class TradeResult:
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    qty_usd: float
    pnl_usd: float
    pnl_pct: float
    entry_bar: int
    exit_bar: int
    dc_moment: float = 0.0
    sentiment_local: float = 0.0


def load_klines(symbol_file: Path) -> Optional[pd.DataFrame]:
    """Load 15m klines from JSON."""
    try:
        with open(symbol_file) as f:
            data = json.load(f)
        if len(data) < 500:
            return None
        df = pd.DataFrame(data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        for c in ['open', 'high', 'low', 'close', 'volume']:
            df[c] = df[c].astype(float)
        df = df.sort_values('timestamp').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"  Skip {symbol_file.name}: {e}")
        return None


def compute_stochastic(df: pd.DataFrame) -> pd.DataFrame:
    """Compute stochastic K and D."""
    low_min = df['low'].rolling(STOCH_PERIOD, min_periods=STOCH_PERIOD).min()
    high_max = df['high'].rolling(STOCH_PERIOD, min_periods=STOCH_PERIOD).max()
    denom = high_max - low_min
    denom = denom.replace(0, np.nan)
    df['stoch_k'] = ((df['close'] - low_min) / denom * 100.0).fillna(50.0)
    df['stoch_d'] = df['stoch_k'].rolling(STOCH_SMOOTH, min_periods=1).mean()
    df['stoch_k_prev'] = df['stoch_k'].shift(1)
    df['stoch_d_prev'] = df['stoch_d'].shift(1)
    df['stoch_crossover'] = (df['stoch_k_prev'] < df['stoch_d_prev']) & (df['stoch_k'] > df['stoch_d'])
    df['stoch_crossunder'] = (df['stoch_k_prev'] > df['stoch_d_prev']) & (df['stoch_k'] < df['stoch_d'])
    return df


def compute_donchian(df: pd.DataFrame) -> pd.DataFrame:
    """Compute Donchian channels and derived metrics."""
    df['dc_high'] = df['high'].rolling(DC_PERIOD, min_periods=DC_PERIOD).max()
    df['dc_low'] = df['low'].rolling(DC_PERIOD, min_periods=DC_PERIOD).min()
    dc_range = df['dc_high'] - df['dc_low']
    dc_range_safe = dc_range.replace(0, np.nan)
    df['dc_position'] = ((df['close'] - df['dc_low']) / dc_range_safe).fillna(0.5)
    df['dc_width'] = (dc_range / df['dc_low'].replace(0, np.nan) * 100.0).fillna(0.0)
    # Multi-TF simulation: use rolling windows of different lengths for HTF/LTF
    # LTF = current 15m DC (period 20), HTF = longer period (80 = ~1D equivalent)
    htf_period = 80
    df['dc_high_htf'] = df['high'].rolling(htf_period, min_periods=htf_period).max()
    df['dc_low_htf'] = df['low'].rolling(htf_period, min_periods=htf_period).min()
    dc_range_htf = df['dc_high_htf'] - df['dc_low_htf']
    dc_range_htf_safe = dc_range_htf.replace(0, np.nan)
    df['dc_position_htf'] = ((df['close'] - df['dc_low_htf']) / dc_range_htf_safe).fillna(0.5)
    df['dc_width_htf'] = (dc_range_htf / df['dc_low_htf'].replace(0, np.nan) * 100.0).fillna(0.0)
    # DC expansion = current width vs trailing average width (are channels expanding?)
    dc_width_ma = df['dc_width'].rolling(48, min_periods=20).mean()  # 12h trailing avg
    df['dc_expansion'] = (df['dc_width'] / dc_width_ma.replace(0, np.nan)).fillna(1.0)
    return df


def compute_dc_moment(df: pd.DataFrame) -> pd.DataFrame:
    """Compute dc_moment (-100..+100) matching production logic."""
    hp = df['dc_position_htf']  # HTF position 0-1
    lp = df['dc_position']       # LTF position 0-1
    trend = (hp - 0.5) * 2.0     # -1 to +1
    # Pullback depth
    pbd = pd.Series(np.where(trend > 0, np.maximum(0.0, hp - lp) / np.maximum(hp, 0.01), np.maximum(0.0, lp - hp) / np.maximum(1.0 - hp, 0.01)), index=df.index)
    pbd = pbd.clip(0.0, 1.0)
    # Expansion boost
    exp = df['dc_expansion']
    eb = pd.Series(np.where(exp > 1.0, np.clip(exp * 0.65 + 0.35, 1.0, 1.3), np.maximum(0.7, exp)), index=df.index)
    df['dc_moment'] = (trend * pbd * eb * 100.0).clip(-100, 100).round(1)
    return df


def compute_sentiment_local(df: pd.DataFrame) -> pd.DataFrame:
    """
    Approximate sentiment_local from price action.
    Uses momentum + volume divergence as a proxy since we don't have live sentiment.
    This creates a -100 to +100 score based on:
    - Price momentum vs longer-term trend
    - Volume anomalies
    - RSI divergence
    """
    # Price momentum: 24-bar ROC (6h on 15m)
    roc_24 = df['close'].pct_change(24) * 100.0
    # Volume z-score
    vol_ma = df['volume'].rolling(96, min_periods=20).mean()
    vol_std = df['volume'].rolling(96, min_periods=20).std().replace(0, np.nan)
    vol_z = ((df['volume'] - vol_ma) / vol_std).fillna(0.0).clip(-3, 3)
    # RSI-like component (14-bar)
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100.0 - 100.0 / (1.0 + rs)).fillna(50.0)
    rsi_centered = rsi - 50.0  # -50 to +50
    # Combine: momentum drives direction, volume confirms, RSI adds mean-reversion signal
    # Invert RSI for contrarian nature: very high RSI = negative sentiment (overbought)
    raw = roc_24 * 2.0 + vol_z * 10.0 - rsi_centered * 0.5
    # Normalize to -100..+100
    roll_max = raw.rolling(SENTIMENT_LOOKBACK, min_periods=20).max()
    roll_min = raw.rolling(SENTIMENT_LOOKBACK, min_periods=20).min()
    denom = (roll_max - roll_min).replace(0, np.nan)
    df['sentiment_local'] = (((raw - roll_min) / denom * 200.0 - 100.0).fillna(0.0)).clip(-100, 100)
    return df


def compute_alignment(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute alignment score (0-24) from multi-TF trend agreement.
    Uses EMA crossovers at simulated timeframes.
    """
    score = pd.Series(0.0, index=df.index)
    # Simulate different TF trends using different EMA periods on 15m data
    # 15m trend: EMA 12 vs 26 (standard)
    ema12 = df['close'].ewm(span=12, min_periods=12).mean()
    ema26 = df['close'].ewm(span=26, min_periods=26).mean()
    score += np.where(ema12 > ema26, 4.0, 0.0)
    # 1h trend: EMA 48 vs 104
    ema48 = df['close'].ewm(span=48, min_periods=48).mean()
    ema104 = df['close'].ewm(span=104, min_periods=104).mean()
    score += np.where(ema48 > ema104, 4.0, 0.0)
    # 4h trend: EMA 192 vs 416
    ema192 = df['close'].ewm(span=192, min_periods=192).mean()
    ema416 = df['close'].ewm(span=416, min_periods=416).mean()
    score += np.where(ema192 > ema416, 4.0, 0.0)
    # Stochastic alignment: K > D across windows
    for period in [14, 56, 224]:
        low_min = df['low'].rolling(period, min_periods=period).min()
        high_max = df['high'].rolling(period, min_periods=period).max()
        denom = (high_max - low_min).replace(0, np.nan)
        k = ((df['close'] - low_min) / denom * 100.0).fillna(50.0)
        d = k.rolling(3, min_periods=1).mean()
        score += np.where(k > d, 2.0, 0.0)
    # Volume confirmation
    vol_sma = df['volume'].rolling(20, min_periods=10).mean()
    score += np.where(df['volume'] > vol_sma, 2.0, 0.0)
    # Price above SMA200
    sma200 = df['close'].rolling(200, min_periods=200).mean()
    score += np.where(df['close'] > sma200, 2.0, 0.0)
    df['alignment'] = score
    return df


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all required indicators."""
    df = compute_stochastic(df)
    df = compute_donchian(df)
    df = compute_dc_moment(df)
    df = compute_sentiment_local(df)
    df = compute_alignment(df)
    return df


class PortfolioSimulator:
    """Full portfolio simulator with L/S ratio enforcement."""

    def __init__(self, name: str, sizing_fn, entry_gate_fn=None):
        self.name = name
        self.sizing_fn = sizing_fn  # (side, dc_moment, sentiment_local, dc_expansion) -> qty_usd or None
        self.entry_gate_fn = entry_gate_fn  # (row) -> bool, additional gate
        self.positions: List[Position] = []
        self.trades: List[TradeResult] = []
        self.equity = 10000.0
        self.peak_equity = 10000.0
        self.daily_returns: List[float] = []
        self.ls_ratio_history: List[float] = []
        self._prev_equity = 10000.0
        self._bar_count = 0

    def _count_sides(self) -> Tuple[int, int]:
        longs = sum(1 for p in self.positions if p.side == 'LONG')
        shorts = sum(1 for p in self.positions if p.side == 'SHORT')
        return longs, shorts

    def _ls_ratio(self) -> float:
        l, s = self._count_sides()
        if s == 0:
            return 999.0 if l > 0 else 1.0
        return l / s

    def _can_open(self, side: str) -> bool:
        if len(self.positions) >= MAX_POSITIONS:
            return False
        l, s = self._count_sides()
        if side == 'LONG':
            new_ratio = (l + 1) / max(s, 1)
            return new_ratio <= LS_RATIO_MAX
        else:
            new_ratio = l / max(s + 1, 1)
            return new_ratio >= LS_RATIO_MIN or l == 0

    def try_open(self, symbol: str, side: str, price: float, bar_idx: int, dc_moment: float, sentiment_local: float, dc_expansion: float):
        if not self._can_open(side):
            return
        # Check if already have this symbol+side
        for p in self.positions:
            if p.symbol == symbol and p.side == side:
                return
        qty = self.sizing_fn(side, dc_moment, sentiment_local, dc_expansion)
        if qty is None:
            return
        qty = max(MIN_QTY, min(MAX_QTY, qty))
        if qty > self.equity * 0.15:  # Max 15% of equity per position
            qty = self.equity * 0.15
        cost = qty * RT_COST / 2.0  # Half RT cost on entry
        self.equity -= cost
        self.positions.append(Position(symbol=symbol, side=side, entry_price=price, qty_usd=qty, entry_bar=bar_idx, dc_moment=dc_moment, sentiment_local=sentiment_local))

    def try_close(self, pos: Position, price: float, bar_idx: int):
        if pos.side == 'LONG':
            pnl_pct = (price - pos.entry_price) / pos.entry_price
        else:
            pnl_pct = (pos.entry_price - price) / pos.entry_price
        pnl_usd = pos.qty_usd * pnl_pct
        cost = pos.qty_usd * RT_COST / 2.0  # Half RT cost on exit
        self.equity += pnl_usd - cost
        self.trades.append(TradeResult(symbol=pos.symbol, side=pos.side, entry_price=pos.entry_price, exit_price=price, qty_usd=pos.qty_usd, pnl_usd=pnl_usd - cost, pnl_pct=pnl_pct - RT_COST, entry_bar=pos.entry_bar, exit_bar=bar_idx, dc_moment=pos.dc_moment, sentiment_local=pos.sentiment_local))
        self.positions.remove(pos)

    def record_daily(self, bar_idx: int, bars_per_day: int = 96):
        """Track daily returns every 96 bars (1 day on 15m)."""
        self._bar_count += 1
        ratio = self._ls_ratio()
        self.ls_ratio_history.append(ratio)
        if self._bar_count % bars_per_day == 0:
            daily_ret = (self.equity - self._prev_equity) / max(self._prev_equity, 1.0)
            self.daily_returns.append(daily_ret)
            self._prev_equity = self.equity
            self.peak_equity = max(self.peak_equity, self.equity)

    def stats(self) -> Dict:
        if not self.trades:
            return {'name': self.name, 'trades': 0, 'wins': 0, 'losses': 0, 'total_pnl': 0, 'final_equity': round(self.equity, 2), 'win_rate': 0, 'avg_win_pct': 0, 'avg_loss_pct': 0, 'sharpe': 0, 'max_dd_pct': 0, 'avg_ls_ratio': 0}
        wins = [t for t in self.trades if t.pnl_usd > 0]
        losses = [t for t in self.trades if t.pnl_usd <= 0]
        total_pnl = sum(t.pnl_usd for t in self.trades)
        avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
        avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0
        # Daily Sharpe
        if len(self.daily_returns) > 5:
            dr = np.array(self.daily_returns)
            sharpe = (dr.mean() / dr.std()) if dr.std() > 0 else 0  # per-trade pool_sharpe (sqrt(365) stripped 2026-04-29 per CLAUDE.md rule 4)
        else:
            sharpe = 0
        # Max drawdown from equity curve
        peak = 10000.0
        max_dd = 0.0
        eq = 10000.0
        for r in self.daily_returns:
            eq *= (1 + r)
            peak = max(peak, eq)
            dd = (peak - eq) / peak
            max_dd = max(max_dd, dd)
        avg_ratio = np.mean(self.ls_ratio_history) if self.ls_ratio_history else 1.0
        return {
            'name': self.name,
            'trades': len(self.trades),
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': len(wins) / len(self.trades) * 100,
            'total_pnl': round(total_pnl, 2),
            'final_equity': round(self.equity, 2),
            'avg_win_pct': round(avg_win * 100, 4),
            'avg_loss_pct': round(avg_loss * 100, 4),
            'sharpe': round(sharpe, 3),
            'max_dd_pct': round(max_dd * 100, 2),
            'avg_ls_ratio': round(avg_ratio, 3),
        }


def run_portfolio_test(symbols_data: Dict[str, pd.DataFrame], test_name: str, sizing_fn, entry_gate_fn=None, split='train') -> Dict:
    """Run a full portfolio simulation across all symbols."""
    sim = PortfolioSimulator(test_name, sizing_fn, entry_gate_fn)
    # Determine bar range based on split
    min_bars = min(len(df) for df in symbols_data.values())
    if split == 'train':
        start_bar = 500  # warmup
        end_bar = int(min_bars * 0.70)
    else:
        start_bar = int(min_bars * 0.70)
        end_bar = min_bars
    # Build combined timeline
    for bar_idx in range(start_bar, end_bar):
        # Check exits first
        for pos in list(sim.positions):
            df = symbols_data[pos.symbol]
            if bar_idx >= len(df):
                continue
            row = df.iloc[bar_idx]
            price = row['close']
            bars_held = bar_idx - pos.entry_bar
            # Exit conditions
            if bars_held >= HOLD_BARS:
                # Time-based exit
                sim.try_close(pos, price, bar_idx)
            elif pos.side == 'LONG' and row.get('stoch_crossunder', False) and row['stoch_k'] > 70:
                sim.try_close(pos, price, bar_idx)
            elif pos.side == 'SHORT' and row.get('stoch_crossover', False) and row['stoch_k'] < 30:
                sim.try_close(pos, price, bar_idx)
            elif pos.side == 'LONG' and (price - pos.entry_price) / pos.entry_price < -0.015:
                sim.try_close(pos, price, bar_idx)  # 1.5% stop
            elif pos.side == 'SHORT' and (pos.entry_price - price) / pos.entry_price < -0.015:
                sim.try_close(pos, price, bar_idx)  # 1.5% stop
        # Check entries
        for symbol, df in symbols_data.items():
            if bar_idx >= len(df):
                continue
            row = df.iloc[bar_idx]
            if pd.isna(row.get('dc_moment', np.nan)):
                continue
            dc_moment = row['dc_moment']
            sentiment_local = row['sentiment_local']
            dc_expansion = row['dc_expansion']
            alignment = row['alignment']
            price = row['close']
            # Additional gate if provided
            if entry_gate_fn and not entry_gate_fn(row):
                continue
            # LONG entry: stoch crossover from zone 22 + alignment >= 12
            if row.get('stoch_crossover', False) and row['stoch_k_prev'] < 22 and alignment >= 12:
                sim.try_open(symbol, 'LONG', price, bar_idx, dc_moment, sentiment_local, dc_expansion)
            # SHORT entry: stoch crossunder from zone 78 + alignment < 12
            if row.get('stoch_crossunder', False) and row['stoch_k_prev'] > 78 and alignment < 12:
                sim.try_open(symbol, 'SHORT', price, bar_idx, dc_moment, sentiment_local, dc_expansion)
        sim.record_daily(bar_idx)
    # Close remaining positions at last price
    for pos in list(sim.positions):
        df = symbols_data[pos.symbol]
        last_bar = min(end_bar - 1, len(df) - 1)
        sim.try_close(pos, df.iloc[last_bar]['close'], last_bar)
    return sim.stats()


# ===================== SIZING FUNCTIONS =====================

def sizing_flat(side, dc_moment, sentiment_local, dc_expansion):
    return BASE_QTY

def sizing_dc_moment(side, dc_moment, sentiment_local, dc_expansion):
    """Scale position with dc_moment magnitude."""
    if side == 'LONG':
        return BASE_QTY * (1.0 + dc_moment / 50.0)
    else:
        return BASE_QTY * (1.0 + abs(dc_moment) / 50.0)

def sizing_sentiment_contrarian(side, dc_moment, sentiment_local, dc_expansion):
    """Boost on extreme sentiment (contrarian)."""
    qty = BASE_QTY
    if side == 'LONG' and sentiment_local < -50:
        qty *= 2.0  # Oversold bounce
    elif side == 'SHORT' and sentiment_local > 50:
        qty *= 2.0  # Overbought fade
    elif side == 'LONG' and sentiment_local > 0 and dc_moment < 0:
        # Local positive but global (dc_moment proxy) negative = divergence
        qty *= 1.5
    return qty

def sizing_combined(side, dc_moment, sentiment_local, dc_expansion):
    """DC_MOMENT sizing + sentiment boost."""
    if side == 'LONG':
        qty = BASE_QTY * (1.0 + dc_moment / 50.0)
    else:
        qty = BASE_QTY * (1.0 + abs(dc_moment) / 50.0)
    # Sentiment boost on top
    if side == 'LONG' and sentiment_local < -50:
        qty *= 1.5
    elif side == 'SHORT' and sentiment_local > 50:
        qty *= 1.5
    return qty

def gate_dc_expansion_1_0(row):
    return row.get('dc_expansion', 0) > 1.0

def gate_dc_expansion_1_5(row):
    return row.get('dc_expansion', 0) > 1.5

def gate_none(row):
    return True


def save_results(all_results: List[Dict], db_path: Path):
    """Save results to SQLite."""
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS sizing_tests")
    c.execute("""CREATE TABLE sizing_tests (
        test_name TEXT, split TEXT, trades INT, wins INT, losses INT,
        win_rate REAL, total_pnl REAL, final_equity REAL,
        avg_win_pct REAL, avg_loss_pct REAL, sharpe REAL,
        max_dd_pct REAL, avg_ls_ratio REAL
    )""")
    for r in all_results:
        c.execute("INSERT INTO sizing_tests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            r['name'], r.get('split', ''), r.get('trades', 0), r.get('wins', 0), r.get('losses', 0),
            r.get('win_rate', 0), r.get('total_pnl', 0), r.get('final_equity', 10000),
            r.get('avg_win_pct', 0), r.get('avg_loss_pct', 0), r.get('sharpe', 0),
            r.get('max_dd_pct', 0), r.get('avg_ls_ratio', 0),
        ))
    conn.commit()
    conn.close()


def main():
    t0 = time.time()
    print("=" * 80)
    print("PRACTICAL SIZING BACKTEST: DC_MOMENT + SENTIMENT CONTRARIAN")
    print("=" * 80)
    # Find symbols with >17000 bars on 15m
    kline_files = sorted(KLINES_DIR.glob("*_15m.json"))
    print(f"\nFound {len(kline_files)} 15m kline files")
    symbols_data = {}
    for kf in kline_files:
        symbol = kf.name.replace("_15m.json", "")
        df = load_klines(kf)
        if df is not None and len(df) >= 17000:
            print(f"  Loading {symbol}: {len(df)} bars ({df['timestamp'].iloc[0].date()} to {df['timestamp'].iloc[-1].date()})")
            df = compute_all_indicators(df)
            symbols_data[symbol] = df
    print(f"\nLoaded {len(symbols_data)} symbols with >=17000 bars")
    if not symbols_data:
        print("ERROR: No qualifying symbols found!")
        sys.exit(1)
    # Diagnostic: check indicator ranges
    sample_sym = list(symbols_data.keys())[0]
    sample_df = symbols_data[sample_sym]
    mid = len(sample_df) // 2
    print(f"\nIndicator diagnostics ({sample_sym}, bar {mid}):")
    print(f"  dc_moment:       min={sample_df['dc_moment'].min():.1f}, max={sample_df['dc_moment'].max():.1f}, mean={sample_df['dc_moment'].mean():.1f}")
    print(f"  dc_expansion:    min={sample_df['dc_expansion'].min():.3f}, max={sample_df['dc_expansion'].max():.3f}, mean={sample_df['dc_expansion'].mean():.3f}")
    print(f"  sentiment_local: min={sample_df['sentiment_local'].min():.1f}, max={sample_df['sentiment_local'].max():.1f}, mean={sample_df['sentiment_local'].mean():.1f}")
    print(f"  alignment:       min={sample_df['alignment'].min():.0f}, max={sample_df['alignment'].max():.0f}, mean={sample_df['alignment'].mean():.1f}")
    print(f"  stoch_k:         min={sample_df['stoch_k'].min():.1f}, max={sample_df['stoch_k'].max():.1f}")
    # Count entry signals
    total_long_signals = 0
    total_short_signals = 0
    for sym, df in symbols_data.items():
        long_mask = df['stoch_crossover'] & (df['stoch_k_prev'] < 22) & (df['alignment'] >= 12)
        short_mask = df['stoch_crossunder'] & (df['stoch_k_prev'] > 78) & (df['alignment'] < 12)
        total_long_signals += long_mask.sum()
        total_short_signals += short_mask.sum()
    print(f"\nEntry signals: {total_long_signals} LONG, {total_short_signals} SHORT")
    # Define test configurations
    tests = [
        # Test 1: DC_MOMENT sizing
        ("T1_flat_baseline", sizing_flat, None),
        ("T1_dc_moment_sizing", sizing_dc_moment, None),
        # Test 2: Sentiment contrarian
        ("T2_flat_baseline", sizing_flat, None),
        ("T2_sentiment_contrarian", sizing_sentiment_contrarian, None),
        # Test 3: Combined
        ("T3_combined_dcm_sent", sizing_combined, None),
        # Test 4: DC_EXPANSION gate
        ("T4_no_gate_baseline", sizing_flat, gate_none),
        ("T4_dc_expansion_1_0", sizing_flat, gate_dc_expansion_1_0),
        ("T4_dc_expansion_1_5", sizing_flat, gate_dc_expansion_1_5),
    ]
    all_results = []
    for split in ['train', 'test']:
        print(f"\n{'=' * 60}")
        print(f"  SPLIT: {split.upper()} ({'70%' if split == 'train' else '30%'})")
        print(f"{'=' * 60}")
        for test_name, sizing_fn, gate_fn in tests:
            full_name = f"{test_name}"
            print(f"\n  Running: {full_name} ({split})...", end=" ", flush=True)
            result = run_portfolio_test(symbols_data, full_name, sizing_fn, gate_fn, split)
            result['split'] = split
            all_results.append(result)
            print(f"Trades={result['trades']}, PnL=${result['total_pnl']}, WR={result['win_rate']:.1f}%, Sharpe={result['sharpe']:.3f}, MaxDD={result['max_dd_pct']:.1f}%")
    # Save to DB
    save_results(all_results, RESULTS_DB)
    print(f"\nResults saved to: {RESULTS_DB}")
    # Print summary table
    print("\n" + "=" * 120)
    print("SUMMARY TABLE")
    print("=" * 120)
    print(f"{'Test':<30} {'Split':<6} {'Trades':>7} {'Wins':>6} {'WR%':>7} {'PnL$':>10} {'AvgW%':>8} {'AvgL%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'L/S':>6}")
    print("-" * 120)
    for r in all_results:
        print(f"{r['name']:<30} {r['split']:<6} {r['trades']:>7} {r.get('wins',0):>6} {r['win_rate']:>7.1f} {r['total_pnl']:>10.2f} {r.get('avg_win_pct',0):>8.4f} {r.get('avg_loss_pct',0):>8.4f} {r['sharpe']:>8.3f} {r.get('max_dd_pct',0):>8.1f} {r.get('avg_ls_ratio',1):>6.2f}")
    # Print test-vs-train comparison
    print("\n" + "=" * 80)
    print("TRAIN vs TEST COMPARISON (key metrics)")
    print("=" * 80)
    test_names_unique = list(dict.fromkeys(t[0] for t in tests))
    for tn in test_names_unique:
        train_r = next((r for r in all_results if r['name'] == tn and r['split'] == 'train'), None)
        test_r = next((r for r in all_results if r['name'] == tn and r['split'] == 'test'), None)
        if train_r and test_r:
            sharpe_delta = test_r['sharpe'] - train_r['sharpe']
            wr_delta = test_r['win_rate'] - train_r['win_rate']
            print(f"  {tn:<30} Train Sharpe={train_r['sharpe']:>7.3f}  Test Sharpe={test_r['sharpe']:>7.3f}  delta={sharpe_delta:>+7.3f}  |  WR train={train_r['win_rate']:.1f}%  test={test_r['win_rate']:.1f}%  delta={wr_delta:>+5.1f}%")
    elapsed = time.time() - t0
    print(f"\nTotal runtime: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
