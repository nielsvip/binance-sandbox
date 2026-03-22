#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
ABLATION BACKTEST — Systematically test every entry/exit/augment component.

Phase 1: Baseline — run full pipeline with all components ON
Phase 2: Ablation — remove one component at a time, measure Sharpe delta
Phase 3: Sweep MIN_GAIN_TO_BUY_AGGRESSIVELY from 0.5% to 5.0% in 0.5% steps
Phase 4: Golden config — combine only the winners

Usage:
  python3 backtest_ablation.py                    # Full run
  python3 backtest_ablation.py --report           # Print results
  python3 backtest_ablation.py --phase 2          # Run specific phase
  python3 backtest_ablation.py --symbols 30       # Limit symbols
"""
import os, sys, json, math, time, signal, logging, warnings, argparse, hashlib
import numpy as np
warnings.filterwarnings("ignore", category=RuntimeWarning)
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from multiprocessing import Pool, cpu_count
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from copy import deepcopy

logging.basicConfig(level=logging.INFO, format='%(asctime)s [ABLATION] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

# ═══ PATHS ═══════════════════════════════════════════════════════════════════
import platform
if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance")
KLINES_DIR = BASE_PATH / "klines_cache"
if not KLINES_DIR.exists():
    KLINES_DIR = Path("/home/niels/binance-sandbox/klines_cache")
RESULTS_DIR = BASE_PATH / "data" / "backtest_ablation"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE = RESULTS_DIR / "ablation_results.json"
DETAIL_FILE = RESULTS_DIR / "ablation_detail.jsonl"

FEE_PCT = 0.08  # 0.08% round-trip (maker+taker avg)
ANNUAL_BARS = {"1m": 525600, "3m": 175200, "5m": 105120, "15m": 35040, "1h": 8760, "4h": 2190, "D": 365}
N_WORKERS = max(1, cpu_count() - 1)
MIN_TRADES = 10
WARMUP = 300
ENTRY_TF = "15m"  # Primary entry timeframe (15m has years of data; 3m/1h have <2k bars)
HTF_CONFIRM = None  # HTF uses longer-period indicators on same 15m data (SMA200, DC80, etc.)
shutdown_flag = False


def _handle_signal(sig, frame):
    global shutdown_flag
    shutdown_flag = True
    logger.info("Shutdown requested...")

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ═══ VECTORIZED INDICATOR ENGINE (from backtest_factory.py) ══════════════════

def _ema_np(arr, period):
    result = np.empty_like(arr)
    result[:] = np.nan
    if len(arr) < period:
        return result
    mult = 2.0 / (period + 1)
    result[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        result[i] = arr[i] * mult + result[i - 1] * (1 - mult)
    return result


def _sma_np(arr, period):
    if len(arr) < period:
        return np.full_like(arr, np.nan)
    # Fill NaN with 50.0 (neutral) before cumsum to prevent propagation
    clean = np.where(np.isnan(arr), 50.0, arr)
    cum = np.cumsum(clean)
    cum[period:] = cum[period:] - cum[:-period]
    result = np.full_like(arr, np.nan)
    result[period - 1:] = cum[period - 1:] / period
    return result


def ind_stoch(h, lo, c, k_period=14, sk=5, sd=5):
    n = len(c)
    # Vectorized rolling max/min using stride tricks
    from numpy.lib.stride_tricks import sliding_window_view
    if n >= k_period:
        hh_windows = sliding_window_view(h, k_period)
        ll_windows = sliding_window_view(lo, k_period)
        hh = np.max(hh_windows, axis=1)
        ll = np.min(ll_windows, axis=1)
        raw_k = np.full(n, 50.0)
        denom = hh - ll
        valid = denom > 0
        raw_k[k_period - 1:] = np.where(valid, (c[k_period - 1:] - ll) / denom * 100, 50.0)
    else:
        raw_k = np.full(n, 50.0)
    k = _sma_np(raw_k, sk)
    d = _sma_np(k, sd)
    return k, d


def ind_rsi(c, period=14):
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = _ema_np(gain, period)
    avg_loss = _ema_np(loss, period)
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    return 100 - 100 / (1 + rs)


def ind_donchian(h, lo, period=20):
    n = len(h)
    from numpy.lib.stride_tricks import sliding_window_view
    dc_h = np.full(n, np.nan)
    dc_l = np.full(n, np.nan)
    if n >= period:
        dc_h[period - 1:] = np.max(sliding_window_view(h, period), axis=1)
        dc_l[period - 1:] = np.min(sliding_window_view(lo, period), axis=1)
    dc_mid = (dc_h + dc_l) / 2
    return dc_h, dc_l, dc_mid


def ind_heikin_ashi(o, h, lo, c):
    ha_c = (o + h + lo + c) / 4
    ha_o = np.empty_like(o)
    ha_o[0] = (o[0] + c[0]) / 2
    for i in range(1, len(o)):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    return np.where(ha_c >= ha_o, 1, -1)


def ind_wavetrend(h, lo, c, n1=10, n2=21):
    hlc3 = (h + lo + c) / 3.0
    esa = _ema_np(hlc3, n1)
    d = _ema_np(np.abs(hlc3 - esa), n1)
    ci = np.where(d > 0, (hlc3 - esa) / (0.015 * d), 0.0)
    wt1 = _ema_np(ci, n2)
    wt2 = _sma_np(wt1, 4)
    return wt1, wt2


def ind_hull_trend(c, short_p=9, long_p=21):
    ema_s = _ema_np(c, short_p)
    ema_l = _ema_np(c, long_p)
    t_up = np.where(ema_s > ema_l, 1, 0)
    return t_up


def ind_atr(h, lo, c, period=14):
    tr = np.maximum(h - lo, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(lo - np.roll(c, 1))))
    tr[0] = h[0] - lo[0]
    return _ema_np(tr, period)


def load_klines(symbol, tf):
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists():
        return None
    try:
        bars = json.loads(path.read_text())
        if not isinstance(bars, list) or len(bars) < WARMUP + 100:
            return None
        if isinstance(bars[0], dict):
            o = np.array([float(b["open"]) for b in bars], dtype=np.float64)
            h = np.array([float(b["high"]) for b in bars], dtype=np.float64)
            lo = np.array([float(b["low"]) for b in bars], dtype=np.float64)
            c = np.array([float(b["close"]) for b in bars], dtype=np.float64)
            v = np.array([float(b.get("volume", 0)) for b in bars], dtype=np.float64)
        else:
            arr = np.array(bars, dtype=np.float64)
            if arr.ndim == 2 and arr.shape[1] >= 5:
                o, h, lo, c, v = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
            elif arr.ndim == 2 and arr.shape[1] >= 4:
                o, h, lo, c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
                v = np.ones(len(c))
            else:
                return None
        data = np.column_stack([o, h, lo, c, v])
        # Limit to last 35000 bars (~1 year of 15m data) for speed
        if len(data) > 35000:
            data = data[-35000:]
        return data
    except Exception:
        return None


# ═══ COMPUTE INDICATORS FOR BACKTEST ════════════════════════════════════════

def compute_indicators(data):
    """Compute all indicators needed for the ablation backtest."""
    o, h, lo, c, v = data[:, 0], data[:, 1], data[:, 2], data[:, 3], data[:, 4]
    n = len(c)
    I = {}
    # Stochastic
    k, d = ind_stoch(h, lo, c, 14, 5, 5)
    I["k"] = k; I["d"] = d
    kp = np.roll(k, 1); kp[0] = k[0]
    dp = np.roll(d, 1); dp[0] = d[0]
    I["kp"] = kp; I["dp"] = dp
    I["stoch_co"] = ((k > d) & (kp <= dp)).astype(np.int8)
    I["stoch_cu"] = ((k < d) & (kp >= dp)).astype(np.int8)
    # RSI
    I["rsi"] = ind_rsi(c, 14)
    # Donchian
    dc_h, dc_l, dc_mid = ind_donchian(h, lo, 20)
    I["dc_h"] = dc_h; I["dc_l"] = dc_l; I["dc_mid"] = dc_mid
    dc_h4, dc_l4, _ = ind_donchian(h, lo, 4)
    I["dc_h4"] = dc_h4; I["dc_l4"] = dc_l4
    # Heikin-Ashi
    ha = ind_heikin_ashi(o, h, lo, c)
    I["ha"] = ha
    hap = np.roll(ha, 1); hap[0] = ha[0]
    I["hap"] = hap
    # WaveTrend
    wt1, wt2 = ind_wavetrend(h, lo, c, 10, 21)
    I["wt1"] = wt1; I["wt2"] = wt2
    wt1p = np.roll(wt1, 1); wt1p[0] = wt1[0]
    wt2p = np.roll(wt2, 1); wt2p[0] = wt2[0]
    I["wt_co"] = ((wt1 > wt2) & (wt1p <= wt2p)).astype(np.int8)
    I["wt_cu"] = ((wt1 < wt2) & (wt1p >= wt2p)).astype(np.int8)
    # Hull Trend
    I["t_up"] = ind_hull_trend(c, 9, 21)
    # EMA/SMA
    I["ema20"] = _ema_np(c, 20)
    I["sma200"] = _sma_np(c, 200)
    # ATR
    I["atr"] = ind_atr(h, lo, c, 14)
    # Long-period indicators as "HTF proxy" (computed on same TF but longer lookback)
    k_slow, d_slow = ind_stoch(h, lo, c, 21, 7, 7)  # Slower stoch as HTF proxy
    I["k_slow"] = k_slow; I["d_slow"] = d_slow
    k_slow_prev = np.roll(k_slow, 1); k_slow_prev[0] = k_slow[0]
    I["k_slow_prev"] = k_slow_prev
    dc_h80, dc_l80, dc_mid80 = ind_donchian(h, lo, 80)  # Wider DC as HTF proxy
    I["dc_h80"] = dc_h80; I["dc_l80"] = dc_l80; I["dc_mid80"] = dc_mid80
    # Price arrays
    I["c"] = c; I["o"] = o; I["h"] = h; I["lo"] = lo; I["v"] = v
    # Previous low/high
    I["lo_prev"] = np.roll(lo, 1); I["lo_prev"][0] = lo[0]
    I["hi_prev"] = np.roll(h, 1); I["hi_prev"][0] = h[0]
    I["c_prev"] = np.roll(c, 1); I["c_prev"][0] = c[0]
    return I


# ═══ COMPONENT TOGGLES ═════════════════════════════════════════════════════

@dataclass
class StrategyConfig:
    """Toggleable components for ablation testing."""
    name: str = "BASELINE"
    # ── ENTRY EVALUATORS ──
    entry_stoch_cross: bool = True          # Stoch K/D crossover entry
    entry_ha_flip: bool = True              # Heikin-Ashi color flip entry
    entry_dc_breakout: bool = True          # Donchian channel breakout entry
    entry_wt_cross: bool = True             # WaveTrend cross entry
    entry_momentum_confirm: bool = True     # Require momentum (k rising for long)
    entry_htf_alignment: bool = True        # Require HTF stoch alignment
    # ── EXIT CONDITIONS ──
    exit_stoch_cross_3m: bool = True        # Exit on 3m stoch cross against
    exit_optimal_hold_bars: bool = True     # Time-based exit (21 bars = 63min)
    exit_profit_tp_exhaustion: bool = True  # Exit on momentum exhaustion at profit
    exit_gain_tiered_harvest: bool = True   # Tiered profit taking (2%/5%/10%)
    exit_dc_basis_profit: bool = True       # Exit at DC basis cross when profitable
    exit_immediate_wrong_way: bool = True   # Kill if -0.12% within 2 bars
    exit_break_even_guard: bool = True      # Close if peak 3%+ drops to 1%
    exit_dc_3m_reduce: bool = True          # Reduce at DC basis 3m break
    exit_stop_major_loss: bool = True       # Reduce on major loss + indicators agree
    exit_hard_drop: bool = True             # Exit on hard price drop below prev low
    # ── AUGMENTATION (PYRAMIDING) ──
    augment_enabled: bool = True            # Allow pyramiding at all
    augment_fast_riser: bool = True         # Double on fast risers
    # ── PARAMETERS ──
    min_gain_to_augment: float = 1.5        # MIN_GAIN_TO_BUY_AGGRESSIVELY
    max_augments: int = 3                   # Max pyramids per position
    optimal_hold_bars: int = 48             # Max bars to hold (15m TF = 12h)
    gain_threshold_low: float = 0.3          # Min gain for stoch cross exit (15m needs wider)
    start_position_pct: float = 1.0         # Position size as % of capital


# ═══ POSITION TRACKING ═════════════════════════════════════════════════════

@dataclass
class Position:
    side: str  # "LONG" or "SHORT"
    entry_price: float = 0.0
    entry_bar: int = 0
    size: float = 1.0  # number of units
    augments: int = 0
    max_gain: float = 0.0
    cost_basis: float = 0.0  # weighted avg entry
    total_invested: float = 0.0


# ═══ BACKTEST ENGINE ═══════════════════════════════════════════════════════

def run_backtest(symbol: str, cfg: StrategyConfig, data_entry, data_htf_unused=None) -> Optional[dict]:
    """Run a single symbol backtest with the given strategy configuration."""
    I = compute_indicators(data_entry)
    c = I["c"]
    n = len(c)
    if n < WARMUP + 100:
        return None
    # HTF proxy: use slow stochastic and wide DC computed on same TF
    htf_k = I["k_slow"]; htf_d = I["d_slow"]; htf_ha = I["ha"]
    htf_dc_mid = I["dc_mid80"]; htf_dc_l = I["dc_l80"]; htf_dc_h = I["dc_h80"]
    # ── Generate entry signals ──
    # The live system uses stoch cross on 3m with 15m confirmation.
    # With 15m-only data, use stoch cross as trigger with multiple confirmations.
    rsi = I["rsi"]
    # Primary trigger: stoch cross (K crosses D)
    long_trigger = np.zeros(n, dtype=bool)
    short_trigger = np.zeros(n, dtype=bool)
    if cfg.entry_stoch_cross:
        long_trigger |= I["stoch_co"].astype(bool)
        short_trigger |= I["stoch_cu"].astype(bool)
    if cfg.entry_wt_cross:
        long_trigger |= I["wt_co"].astype(bool)
        short_trigger |= I["wt_cu"].astype(bool)
    if cfg.entry_dc_breakout:
        dc_co = (c > I["dc_mid"]) & (I["c_prev"] <= np.roll(I["dc_mid"], 1))
        dc_cu = (c < I["dc_mid"]) & (I["c_prev"] >= np.roll(I["dc_mid"], 1))
        long_trigger |= dc_co
        short_trigger |= dc_cu
    # Confirmation layer 1: HA direction
    if cfg.entry_ha_flip:
        long_trigger &= (I["ha"] == 1)
        short_trigger &= (I["ha"] == -1)
    long_entry = long_trigger
    short_entry = short_trigger
    # Confirmation layer 2: Momentum (K rising/falling)
    if cfg.entry_momentum_confirm:
        long_entry &= (I["k"] > I["kp"])
        short_entry &= (I["k"] < I["kp"])
    # Confirmation layer 3: HTF (slow stoch) alignment
    if cfg.entry_htf_alignment and htf_k is not None:
        long_entry &= (htf_k > htf_d)
        short_entry &= (htf_k < htf_d)
    # Zone filters: K not extreme (avoid chasing)
    long_entry &= (I["k"] < 65) & (I["k"] > 5)
    short_entry &= (I["k"] > 35) & (I["k"] < 95)
    # RSI confirmation
    long_entry &= (rsi < 60)
    short_entry &= (rsi > 40)
    # Warmup
    long_entry[:WARMUP] = False
    short_entry[:WARMUP] = False
    # ── Simulate trades ──
    trades = []
    position = None  # current position
    fee_mult = FEE_PCT / 100.0
    cooldown_until = 0
    for bar in range(WARMUP, n):
        if shutdown_flag:
            break
        price = c[bar]
        if price <= 0:
            continue
        # ── Manage existing position ──
        if position is not None:
            is_long = position.side == "LONG"
            gain_pct = ((price - position.cost_basis) / position.cost_basis * 100) if is_long else ((position.cost_basis - price) / position.cost_basis * 100) if position.cost_basis > 0 else 0.0
            position.max_gain = max(position.max_gain, gain_pct)
            bars_held = bar - position.entry_bar
            should_exit = False
            exit_reason = ""
            # ── EXIT: Immediate wrong way (15m: 4 bars = 1h, -1.5% threshold) ──
            if cfg.exit_immediate_wrong_way and bars_held <= 4 and gain_pct < -1.5:
                should_exit = True
                exit_reason = "IMMEDIATE_WRONG_WAY"
            # ── EXIT: Break even guard ──
            if not should_exit and cfg.exit_break_even_guard and position.max_gain > 3.0 and gain_pct <= 1.0:
                should_exit = True
                exit_reason = "BREAK_EVEN_GUARD"
            # ── EXIT: Stoch cross 3m against ──
            if not should_exit and cfg.exit_stoch_cross_3m and gain_pct > cfg.gain_threshold_low:
                if is_long and I["stoch_cu"][bar]:
                    should_exit = True
                    exit_reason = "STOCH_CROSS_3M"
                elif not is_long and I["stoch_co"][bar]:
                    should_exit = True
                    exit_reason = "STOCH_CROSS_3M"
            # ── EXIT: Optimal hold bars ──
            if not should_exit and cfg.exit_optimal_hold_bars and bars_held >= cfg.optimal_hold_bars and gain_pct > cfg.gain_threshold_low:
                should_exit = True
                exit_reason = "OPTIMAL_HOLD_BARS"
            # ── EXIT: Profit TP exhaustion ──
            if not should_exit and cfg.exit_profit_tp_exhaustion and gain_pct > 0.5:
                if is_long and I["k"][bar] > 90 and I["k"][bar] < I["kp"][bar]:
                    should_exit = True
                    exit_reason = "TP_EXHAUSTION_K90"
                elif not is_long and I["k"][bar] < 10 and I["k"][bar] > I["kp"][bar]:
                    should_exit = True
                    exit_reason = "TP_EXHAUSTION_K10"
                # Hard drop below prev low
                if not should_exit and cfg.exit_hard_drop:
                    if is_long and I["lo_prev"][bar] > 0 and price < I["lo_prev"][bar]:
                        should_exit = True
                        exit_reason = "HARD_DROP_BELOW_PREV_LOW"
                    elif not is_long and I["hi_prev"][bar] > 0 and price > I["hi_prev"][bar]:
                        should_exit = True
                        exit_reason = "HARD_RISE_ABOVE_PREV_HIGH"
            # ── EXIT: Gain tiered harvest ──
            if not should_exit and cfg.exit_gain_tiered_harvest and gain_pct > 2.0:
                if gain_pct >= 10.0:
                    if htf_k is not None:
                        if (is_long and htf_k[bar] < htf_d[bar] and htf_ha is not None and htf_ha[bar] == -1) or (not is_long and htf_k[bar] > htf_d[bar] and htf_ha is not None and htf_ha[bar] == 1):
                            should_exit = True
                            exit_reason = "BIG_WINNER_HTF_HARVEST"
                elif gain_pct >= 5.0:
                    if htf_k is not None and htf_ha is not None:
                        if (is_long and htf_ha[bar] == -1 and htf_k[bar] < htf_d[bar]) or (not is_long and htf_ha[bar] == 1 and htf_k[bar] > htf_d[bar]):
                            should_exit = True
                            exit_reason = "MED_WINNER_15M_HARVEST"
            # ── EXIT: DC basis profit exit ──
            if not should_exit and cfg.exit_dc_basis_profit and gain_pct > 3.0:
                if htf_dc_mid is not None:
                    if (is_long and price < htf_dc_mid[bar]) or (not is_long and price > htf_dc_mid[bar]):
                        should_exit = True
                        exit_reason = "DC_BASIS_PROFIT_EXIT"
            # ── EXIT: DC 3m reduce ──
            if not should_exit and cfg.exit_dc_3m_reduce:
                if is_long and I["dc_l"][bar] > 0 and price < I["dc_l"][bar]:
                    should_exit = True
                    exit_reason = "DC_3M_FLOOR_BREAK"
                elif not is_long and I["dc_h"][bar] > 0 and price > I["dc_h"][bar]:
                    should_exit = True
                    exit_reason = "DC_3M_CEIL_BREAK"
            # ── EXIT: Stop major loss ──
            if not should_exit and cfg.exit_stop_major_loss and gain_pct < -2.0 and bars_held > 6:
                long_stop = is_long and (I["k"][bar] < I["d"][bar] or I["ha"][bar] == -1)
                short_stop = not is_long and (I["k"][bar] > I["d"][bar] or I["ha"][bar] == 1)
                if long_stop or short_stop:
                    should_exit = True
                    exit_reason = "STOP_MAJOR_LOSS"
            # ── AUGMENT (PYRAMID) ──
            if not should_exit and cfg.augment_enabled and position.augments < cfg.max_augments and gain_pct >= cfg.min_gain_to_augment:
                # Check momentum alignment for augment
                aug_ok = False
                if is_long and I["k"][bar] > I["kp"][bar] and I["ha"][bar] == 1 and I["k"][bar] < 80:
                    aug_ok = True
                elif not is_long and I["k"][bar] < I["kp"][bar] and I["ha"][bar] == -1 and I["k"][bar] > 20:
                    aug_ok = True
                if aug_ok:
                    # Pyramid: add to position
                    aug_size = position.size  # match original size
                    old_cost = position.cost_basis * position.size
                    position.size += aug_size
                    position.cost_basis = (old_cost + price * aug_size) / position.size
                    position.total_invested += price * aug_size
                    position.augments += 1
            # ── AUGMENT: Fast riser double ──
            if not should_exit and cfg.augment_fast_riser and position.augments < cfg.max_augments and gain_pct > 0.8:
                if I["c_prev"][bar] > 0:
                    price_jump = abs(price - I["c_prev"][bar]) / I["c_prev"][bar]
                    if price_jump >= 0.002:
                        if (is_long and I["k"][bar] < 60 and I["k"][bar] > I["d"][bar] and I["lo"][bar] < I["lo_prev"][bar]) or (not is_long and I["k"][bar] > 40 and I["k"][bar] < I["d"][bar] and I["h"][bar] > I["hi_prev"][bar]):
                            aug_size = position.size
                            old_cost = position.cost_basis * position.size
                            position.size += aug_size
                            position.cost_basis = (old_cost + price * aug_size) / position.size
                            position.total_invested += price * aug_size
                            position.augments += 1
            # ── Execute exit ──
            if should_exit:
                exit_price = price
                if is_long:
                    pnl_pct = (exit_price - position.cost_basis) / position.cost_basis * 100 - fee_mult * 100 * 2
                else:
                    pnl_pct = (position.cost_basis - exit_price) / position.cost_basis * 100 - fee_mult * 100 * 2
                # Scale PnL by size (augmented positions have larger PnL impact)
                pnl_pct_sized = pnl_pct * position.size
                trades.append({
                    "side": position.side,
                    "entry": position.cost_basis,
                    "exit": exit_price,
                    "pnl_pct": pnl_pct,
                    "pnl_sized": pnl_pct_sized,
                    "bars_held": bars_held,
                    "augments": position.augments,
                    "max_gain": position.max_gain,
                    "reason": exit_reason,
                })
                position = None
                cooldown_until = bar + 3  # 3-bar cooldown after exit
                continue
        # ── Open new position ──
        if position is None and bar > cooldown_until:
            if long_entry[bar]:
                position = Position(side="LONG", entry_price=price, entry_bar=bar, size=1.0, cost_basis=price, total_invested=price)
            elif short_entry[bar]:
                position = Position(side="SHORT", entry_price=price, entry_bar=bar, size=1.0, cost_basis=price, total_invested=price)
    # Close any remaining position at last bar
    if position is not None:
        price = c[-1]
        is_long = position.side == "LONG"
        pnl_pct = ((price - position.cost_basis) / position.cost_basis * 100 if is_long else (position.cost_basis - price) / position.cost_basis * 100) - fee_mult * 100 * 2
        trades.append({"side": position.side, "entry": position.cost_basis, "exit": price, "pnl_pct": pnl_pct, "pnl_sized": pnl_pct * position.size, "bars_held": len(c) - position.entry_bar, "augments": position.augments, "max_gain": position.max_gain, "reason": "END_OF_DATA"})
    if len(trades) < MIN_TRADES:
        return None
    return _compute_metrics(trades, symbol, cfg)


def _compute_metrics(trades, symbol, cfg):
    """Compute portfolio metrics from trade list."""
    rets = np.array([t["pnl_pct"] for t in trades])
    sized_rets = np.array([t["pnl_sized"] for t in trades])
    n_trades = len(rets)
    mean_r = rets.mean()
    std_r = rets.std()
    if std_r <= 0:
        return None
    annual = ANNUAL_BARS.get(ENTRY_TF, 175200)
    sharpe = mean_r / std_r * math.sqrt(annual)
    # Sortino
    downside = rets[rets < 0]
    down_std = downside.std() if len(downside) > 2 else std_r
    sortino = mean_r / (down_std + 1e-9) * math.sqrt(annual)
    # Win rate
    wins = np.sum(rets > 0)
    win_rate = wins / n_trades * 100
    # Profit factor
    gross_profit = rets[rets > 0].sum() if np.any(rets > 0) else 0
    gross_loss = abs(rets[rets < 0].sum()) if np.any(rets < 0) else 1e-9
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    # Max drawdown (cumulative)
    cum = np.cumsum(rets)
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    max_dd = dd.min()
    # Avg bars held
    avg_bars = np.mean([t["bars_held"] for t in trades])
    # Augment stats
    augmented = [t for t in trades if t["augments"] > 0]
    aug_count = len(augmented)
    aug_win_rate = (sum(1 for t in augmented if t["pnl_pct"] > 0) / aug_count * 100) if aug_count > 0 else 0
    avg_aug_pnl = np.mean([t["pnl_pct"] for t in augmented]) if aug_count > 0 else 0
    # Total PnL (sized — accounts for pyramiding)
    total_pnl = sized_rets.sum()
    # Exit reason breakdown
    reason_counts = defaultdict(int)
    reason_pnl = defaultdict(float)
    for t in trades:
        reason_counts[t["reason"]] += 1
        reason_pnl[t["reason"]] += t["pnl_pct"]
    return {
        "symbol": symbol,
        "config": cfg.name,
        "n_trades": n_trades,
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 3),
        "max_drawdown": round(max_dd, 2),
        "total_pnl": round(total_pnl, 2),
        "mean_pnl": round(mean_r, 4),
        "avg_bars_held": round(avg_bars, 1),
        "aug_trades": aug_count,
        "aug_win_rate": round(aug_win_rate, 1),
        "avg_aug_pnl": round(avg_aug_pnl, 4),
        "min_gain_augment": cfg.min_gain_to_augment,
        "reason_counts": dict(reason_counts),
        "reason_pnl": {k: round(v, 2) for k, v in reason_pnl.items()},
    }


# ═══ PARALLEL WORKER ═══════════════════════════════════════════════════════

def _pool_init():
    """Ignore signals in worker processes — parent handles shutdown."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def _worker(args):
    """Worker function for multiprocessing."""
    symbol, cfg_dict = args
    cfg = StrategyConfig(**cfg_dict)
    data_entry = load_klines(symbol, ENTRY_TF)
    if data_entry is None:
        return None
    try:
        return run_backtest(symbol, cfg, data_entry)
    except Exception as e:
        return None


# ═══ TEST CONFIGURATIONS ══════════════════════════════════════════════════

def build_ablation_configs() -> List[StrategyConfig]:
    """Build all ablation test configs: baseline + one-off removals."""
    configs = []
    # ── BASELINE: everything ON ──
    configs.append(StrategyConfig(name="BASELINE"))
    # ── ENTRY ABLATIONS: remove one entry type at a time ──
    for field_name, label in [
        ("entry_stoch_cross", "NO_STOCH_CROSS_ENTRY"),
        ("entry_ha_flip", "NO_HA_FLIP_ENTRY"),
        ("entry_dc_breakout", "NO_DC_BREAKOUT_ENTRY"),
        ("entry_wt_cross", "NO_WT_CROSS_ENTRY"),
        ("entry_momentum_confirm", "NO_MOMENTUM_CONFIRM"),
        ("entry_htf_alignment", "NO_HTF_ALIGNMENT"),
    ]:
        cfg = StrategyConfig(name=label)
        setattr(cfg, field_name, False)
        configs.append(cfg)
    # ── EXIT ABLATIONS: remove one exit at a time ──
    for field_name, label in [
        ("exit_stoch_cross_3m", "NO_STOCH_CROSS_3M_EXIT"),
        ("exit_optimal_hold_bars", "NO_OPTIMAL_HOLD_BARS"),
        ("exit_profit_tp_exhaustion", "NO_TP_EXHAUSTION"),
        ("exit_gain_tiered_harvest", "NO_TIERED_HARVEST"),
        ("exit_dc_basis_profit", "NO_DC_BASIS_PROFIT_EXIT"),
        ("exit_immediate_wrong_way", "NO_IMMEDIATE_WRONG_WAY"),
        ("exit_break_even_guard", "NO_BREAK_EVEN_GUARD"),
        ("exit_dc_3m_reduce", "NO_DC_3M_REDUCE"),
        ("exit_stop_major_loss", "NO_STOP_MAJOR_LOSS"),
        ("exit_hard_drop", "NO_HARD_DROP_EXIT"),
    ]:
        cfg = StrategyConfig(name=label)
        setattr(cfg, field_name, False)
        configs.append(cfg)
    # ── AUGMENT ABLATIONS ──
    configs.append(StrategyConfig(name="NO_AUGMENT", augment_enabled=False))
    configs.append(StrategyConfig(name="NO_FAST_RISER", augment_fast_riser=False))
    return configs


def build_augment_sweep_configs() -> List[StrategyConfig]:
    """Sweep MIN_GAIN_TO_BUY_AGGRESSIVELY from 0.5% to 5.0% in 0.5% steps."""
    configs = []
    for pct in np.arange(0.5, 5.5, 0.5):
        pct = round(pct, 1)
        cfg = StrategyConfig(name=f"AUG_MIN_GAIN_{pct}", min_gain_to_augment=pct)
        configs.append(cfg)
    return configs


def build_golden_config(ablation_results: dict) -> List[StrategyConfig]:
    """Build optimized configs by removing components that hurt Sharpe."""
    baseline_sharpe = ablation_results.get("BASELINE", {}).get("sharpe", 0)
    # Find components whose removal IMPROVED Sharpe
    beneficial_removals = []
    for name, metrics in ablation_results.items():
        if name == "BASELINE":
            continue
        if metrics.get("sharpe", 0) > baseline_sharpe:
            beneficial_removals.append((name, metrics["sharpe"] - baseline_sharpe))
    beneficial_removals.sort(key=lambda x: x[1], reverse=True)
    configs = []
    # Golden: remove ALL harmful components
    if beneficial_removals:
        cfg = StrategyConfig(name="GOLDEN_ALL_REMOVALS")
        for name, _ in beneficial_removals:
            field_map = {
                "NO_STOCH_CROSS_ENTRY": "entry_stoch_cross",
                "NO_HA_FLIP_ENTRY": "entry_ha_flip",
                "NO_DC_BREAKOUT_ENTRY": "entry_dc_breakout",
                "NO_WT_CROSS_ENTRY": "entry_wt_cross",
                "NO_MOMENTUM_CONFIRM": "entry_momentum_confirm",
                "NO_HTF_ALIGNMENT": "entry_htf_alignment",
                "NO_STOCH_CROSS_3M_EXIT": "exit_stoch_cross_3m",
                "NO_OPTIMAL_HOLD_BARS": "exit_optimal_hold_bars",
                "NO_TP_EXHAUSTION": "exit_profit_tp_exhaustion",
                "NO_TIERED_HARVEST": "exit_gain_tiered_harvest",
                "NO_DC_BASIS_PROFIT_EXIT": "exit_dc_basis_profit",
                "NO_IMMEDIATE_WRONG_WAY": "exit_immediate_wrong_way",
                "NO_BREAK_EVEN_GUARD": "exit_break_even_guard",
                "NO_DC_3M_REDUCE": "exit_dc_3m_reduce",
                "NO_STOP_MAJOR_LOSS": "exit_stop_major_loss",
                "NO_HARD_DROP_EXIT": "exit_hard_drop",
                "NO_AUGMENT": "augment_enabled",
                "NO_FAST_RISER": "augment_fast_riser",
            }
            if name in field_map:
                setattr(cfg, field_map[name], False)
        configs.append(cfg)
        # Also try removing just the top-3 worst components
        if len(beneficial_removals) >= 2:
            cfg2 = StrategyConfig(name="GOLDEN_TOP3_REMOVALS")
            for name, _ in beneficial_removals[:3]:
                field_map = {
                    "NO_STOCH_CROSS_ENTRY": "entry_stoch_cross",
                    "NO_HA_FLIP_ENTRY": "entry_ha_flip",
                    "NO_DC_BREAKOUT_ENTRY": "entry_dc_breakout",
                    "NO_WT_CROSS_ENTRY": "entry_wt_cross",
                    "NO_MOMENTUM_CONFIRM": "entry_momentum_confirm",
                    "NO_HTF_ALIGNMENT": "entry_htf_alignment",
                    "NO_STOCH_CROSS_3M_EXIT": "exit_stoch_cross_3m",
                    "NO_OPTIMAL_HOLD_BARS": "exit_optimal_hold_bars",
                    "NO_TP_EXHAUSTION": "exit_profit_tp_exhaustion",
                    "NO_TIERED_HARVEST": "exit_gain_tiered_harvest",
                    "NO_DC_BASIS_PROFIT_EXIT": "exit_dc_basis_profit",
                    "NO_IMMEDIATE_WRONG_WAY": "exit_immediate_wrong_way",
                    "NO_BREAK_EVEN_GUARD": "exit_break_even_guard",
                    "NO_DC_3M_REDUCE": "exit_dc_3m_reduce",
                    "NO_STOP_MAJOR_LOSS": "exit_stop_major_loss",
                    "NO_HARD_DROP_EXIT": "exit_hard_drop",
                    "NO_AUGMENT": "augment_enabled",
                    "NO_FAST_RISER": "augment_fast_riser",
                }
                if name in field_map:
                    setattr(cfg2, field_map[name], False)
            configs.append(cfg2)
    # Golden + best augment sweep
    return configs


# ═══ MAIN ══════════════════════════════════════════════════════════════════

def get_symbols(max_symbols=0):
    """Get tradeable symbols from klines cache."""
    symbols = set()
    for f in KLINES_DIR.iterdir():
        if f.name.endswith(f"_{ENTRY_TF}.json"):
            sym = f.name.replace(f"_{ENTRY_TF}.json", "")
            if sym.endswith("USDT") or sym.endswith("USDC"):
                symbols.add(sym)
    symbols = sorted(symbols)
    if max_symbols > 0:
        symbols = symbols[:max_symbols]
    return symbols


def aggregate_results(per_symbol_results: List[dict]) -> dict:
    """Aggregate per-symbol results into portfolio-level metrics."""
    valid = [r for r in per_symbol_results if r is not None]
    if not valid:
        return {}
    all_pnl = [r["mean_pnl"] for r in valid]
    all_sharpe = [r["sharpe"] for r in valid]
    total_trades = sum(r["n_trades"] for r in valid)
    total_pnl = sum(r["total_pnl"] for r in valid)
    avg_sharpe = np.mean(all_sharpe)
    med_sharpe = np.median(all_sharpe)
    avg_wr = np.mean([r["win_rate"] for r in valid])
    avg_pf = np.mean([r["profit_factor"] for r in valid if r["profit_factor"] < 100])
    avg_dd = np.mean([r["max_drawdown"] for r in valid])
    aug_trades = sum(r["aug_trades"] for r in valid)
    aug_wr = np.mean([r["aug_win_rate"] for r in valid if r["aug_trades"] > 0]) if any(r["aug_trades"] > 0 for r in valid) else 0
    # Reason aggregation
    reason_counts = defaultdict(int)
    reason_pnl = defaultdict(float)
    for r in valid:
        for k, v in r.get("reason_counts", {}).items():
            reason_counts[k] += v
        for k, v in r.get("reason_pnl", {}).items():
            reason_pnl[k] += v
    return {
        "n_symbols": len(valid),
        "n_trades": total_trades,
        "avg_sharpe": round(avg_sharpe, 3),
        "median_sharpe": round(med_sharpe, 3),
        "avg_win_rate": round(avg_wr, 1),
        "avg_profit_factor": round(avg_pf, 3),
        "avg_max_drawdown": round(avg_dd, 2),
        "total_pnl": round(total_pnl, 2),
        "aug_trades": aug_trades,
        "aug_win_rate": round(aug_wr, 1),
        "reason_counts": dict(reason_counts),
        "reason_pnl": {k: round(v, 2) for k, v in sorted(reason_pnl.items(), key=lambda x: x[1])},
    }


def run_phase(configs: List[StrategyConfig], symbols: List[str], phase_name: str) -> dict:
    """Run a set of configs across all symbols using a single persistent pool."""
    results = {}
    total_combos = len(configs) * len(symbols)
    logger.info(f"═══ {phase_name} ═══ {len(configs)} configs × {len(symbols)} symbols = {total_combos} backtests on {N_WORKERS} workers")
    pool = Pool(N_WORKERS, initializer=_pool_init)
    try:
        for ci, cfg in enumerate(configs):
            if shutdown_flag:
                break
            t0 = time.time()
            cfg_dict = asdict(cfg)
            tasks = [(sym, cfg_dict) for sym in symbols]
            symbol_results = pool.map(_worker, tasks, chunksize=max(1, len(symbols) // N_WORKERS))
            agg = aggregate_results(symbol_results)
            agg["config_name"] = cfg.name
            agg["config"] = cfg_dict
            results[cfg.name] = agg
            elapsed = time.time() - t0
            sharpe_str = f"Sharpe={agg.get('avg_sharpe', 'N/A')}"
            wr_str = f"WR={agg.get('avg_win_rate', 'N/A')}%"
            pf_str = f"PF={agg.get('avg_profit_factor', 'N/A')}"
            logger.info(f"  [{ci+1}/{len(configs)}] {cfg.name}: {sharpe_str} {wr_str} {pf_str} ({agg.get('n_trades', 0)} trades, {agg.get('n_symbols', 0)} syms, {elapsed:.1f}s)")
    finally:
        pool.close()
        pool.join()
    return results


def print_report(results_file=RESULTS_FILE):
    """Print formatted results report."""
    if not results_file.exists():
        logger.error(f"No results file at {results_file}")
        return
    all_results = json.loads(results_file.read_text())
    # Phase 1+2: Ablation results
    print("\n" + "═" * 100)
    print("ABLATION BACKTEST RESULTS — Component Impact on Sharpe Ratio")
    print("═" * 100)
    ablation = all_results.get("ablation", {})
    if ablation:
        baseline = ablation.get("BASELINE", {})
        bl_sharpe = baseline.get("avg_sharpe", 0)
        print(f"\n{'Config':<35} {'Sharpe':>8} {'Delta':>8} {'WR%':>7} {'PF':>7} {'DD%':>7} {'Trades':>7} {'AugWR%':>7} {'Verdict':>10}")
        print("-" * 100)
        sorted_configs = sorted(ablation.items(), key=lambda x: x[1].get("avg_sharpe", -999), reverse=True)
        for name, m in sorted_configs:
            delta = m.get("avg_sharpe", 0) - bl_sharpe
            delta_str = f"{delta:+.3f}" if name != "BASELINE" else "---"
            verdict = ""
            if name != "BASELINE":
                if delta > 0.05:
                    verdict = "REMOVE"
                elif delta < -0.05:
                    verdict = "KEEP"
                else:
                    verdict = "NEUTRAL"
            print(f"{name:<35} {m.get('avg_sharpe', 0):>8.3f} {delta_str:>8} {m.get('avg_win_rate', 0):>6.1f}% {m.get('avg_profit_factor', 0):>7.3f} {m.get('avg_max_drawdown', 0):>6.2f}% {m.get('n_trades', 0):>7} {m.get('aug_win_rate', 0):>6.1f}% {verdict:>10}")
    # Phase 3: Augment sweep
    sweep = all_results.get("augment_sweep", {})
    if sweep:
        print(f"\n{'═' * 80}")
        print("AUGMENT SWEEP — MIN_GAIN_TO_BUY_AGGRESSIVELY")
        print("═" * 80)
        print(f"\n{'Min Gain%':<12} {'Sharpe':>8} {'WR%':>7} {'PF':>7} {'AugTrades':>10} {'AugWR%':>8} {'TotalPnL':>10}")
        print("-" * 70)
        sorted_sweep = sorted(sweep.items(), key=lambda x: x[1].get("avg_sharpe", -999), reverse=True)
        for name, m in sorted_sweep:
            gain = m.get("config", {}).get("min_gain_to_augment", "?")
            print(f"{gain:<12} {m.get('avg_sharpe', 0):>8.3f} {m.get('avg_win_rate', 0):>6.1f}% {m.get('avg_profit_factor', 0):>7.3f} {m.get('aug_trades', 0):>10} {m.get('aug_win_rate', 0):>7.1f}% {m.get('total_pnl', 0):>10.1f}")
    # Phase 4: Golden configs
    golden = all_results.get("golden", {})
    if golden:
        print(f"\n{'═' * 80}")
        print("GOLDEN CONFIGS — Optimized Component Selection")
        print("═" * 80)
        for name, m in sorted(golden.items(), key=lambda x: x[1].get("avg_sharpe", -999), reverse=True):
            print(f"\n  {name}: Sharpe={m.get('avg_sharpe', 0):.3f} WR={m.get('avg_win_rate', 0):.1f}% PF={m.get('avg_profit_factor', 0):.3f} DD={m.get('avg_max_drawdown', 0):.2f}%")
            cfg = m.get("config", {})
            disabled = [k for k, v in cfg.items() if isinstance(v, bool) and not v and k != "name"]
            if disabled:
                print(f"    Disabled: {', '.join(disabled)}")
    # Exit reason breakdown (from baseline)
    if ablation and "BASELINE" in ablation:
        reason_pnl = ablation["BASELINE"].get("reason_pnl", {})
        if reason_pnl:
            print(f"\n{'═' * 60}")
            print("EXIT REASON PnL BREAKDOWN (Baseline)")
            print("═" * 60)
            for reason, pnl in sorted(reason_pnl.items(), key=lambda x: x[1]):
                count = ablation["BASELINE"].get("reason_counts", {}).get(reason, 0)
                avg = pnl / count if count > 0 else 0
                bar = "+" * max(0, int(pnl / 5)) if pnl > 0 else "-" * max(0, int(-pnl / 5))
                print(f"  {reason:<35} PnL={pnl:>8.1f}% Count={count:>5} Avg={avg:>6.2f}% {bar}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Ablation Backtest Engine")
    parser.add_argument("--report", action="store_true", help="Print results report")
    parser.add_argument("--phase", type=int, default=0, help="Run specific phase (1-4, 0=all)")
    parser.add_argument("--symbols", type=int, default=0, help="Limit number of symbols")
    args = parser.parse_args()
    if args.report:
        print_report()
        return
    symbols = get_symbols(args.symbols)
    logger.info(f"Found {len(symbols)} symbols with {ENTRY_TF} klines data")
    all_results = {}
    if RESULTS_FILE.exists():
        try:
            all_results = json.loads(RESULTS_FILE.read_text())
        except Exception:
            pass
    # ── Phase 1+2: Ablation (baseline + one-off removals) ──
    if args.phase in (0, 1, 2):
        ablation_configs = build_ablation_configs()
        ablation_results = run_phase(ablation_configs, symbols, "PHASE 1+2: ABLATION")
        all_results["ablation"] = ablation_results
        RESULTS_FILE.write_text(json.dumps(all_results, indent=2, default=str))
        logger.info(f"Phase 1+2 complete. Results saved to {RESULTS_FILE}")
    # ── Phase 3: Augment sweep ──
    if args.phase in (0, 3):
        sweep_configs = build_augment_sweep_configs()
        sweep_results = run_phase(sweep_configs, symbols, "PHASE 3: AUGMENT SWEEP")
        all_results["augment_sweep"] = sweep_results
        RESULTS_FILE.write_text(json.dumps(all_results, indent=2, default=str))
        logger.info(f"Phase 3 complete. Results saved to {RESULTS_FILE}")
    # ── Phase 4: Golden configs ──
    if args.phase in (0, 4):
        ablation_results = all_results.get("ablation", {})
        if ablation_results:
            golden_configs = build_golden_config(ablation_results)
            # Add best augment sweep value to golden configs
            sweep = all_results.get("augment_sweep", {})
            if sweep:
                best_aug = max(sweep.items(), key=lambda x: x[1].get("avg_sharpe", -999))
                best_gain = best_aug[1].get("config", {}).get("min_gain_to_augment", 1.5)
                for gc in golden_configs:
                    gc.min_gain_to_augment = best_gain
                # Also test golden with each augment level
                for pct in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
                    if golden_configs:
                        gc = deepcopy(golden_configs[0])
                        gc.name = f"GOLDEN_AUG_{pct}"
                        gc.min_gain_to_augment = pct
                        golden_configs.append(gc)
            if golden_configs:
                golden_results = run_phase(golden_configs, symbols, "PHASE 4: GOLDEN CONFIGS")
                all_results["golden"] = golden_results
                RESULTS_FILE.write_text(json.dumps(all_results, indent=2, default=str))
                logger.info(f"Phase 4 complete. Results saved to {RESULTS_FILE}")
        else:
            logger.warning("No ablation results found — run phases 1+2 first")
    print_report()


if __name__ == "__main__":
    main()
