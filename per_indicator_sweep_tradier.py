#!/usr/bin/env python3
"""
PER-INDICATOR SWEEP — TRADIER (Stocks)
Comprehensive per-indicator backtest: tests EVERY indicator from tradier_indicators.py
across ALL timeframes with threshold sweeps.

Indicators tested:
  - Stochastic K thresholds (16 levels x 6 TFs x 2 sides = 192)
  - Stochastic K > D per TF (6 TFs x 2 sides = 12)
  - RSI thresholds (13 levels x 6 TFs x 2 sides = 156)
  - MFI thresholds (13 levels x 6 TFs x 2 sides = 156)
  - WaveTrend score thresholds (10 levels x 6 TFs x 2 sides = 120)
  - WaveTrend crossover/crossunder (6 TFs x 2 = 12)
  - HA green/red per TF (6 TFs x 2 sides = 12)
  - DC position thresholds (8 levels x 6 TFs x 2 sides = 96)
  - DC width thresholds (8 levels x 6 TFs x 2 sides = 96)
  - DC floor rising / ceil falling (6 TFs x 2 = 12)
  - DC low4 rising / high4 falling (6 TFs x 2 = 12)
  - DC basis/high/low crossovers (6 types x 6 TFs = 36)
  - Relative volume thresholds (6 levels x 6 TFs x 2 sides = 72)
  - ATR ratio short/long (6 levels x 6 TFs x 2 sides = 72)
  - LinReg slope direction (6 TFs x 2 sides = 12)
  - BB %B thresholds (8 levels x 3 TFs x 2 sides = 48)
  - BB width thresholds (6 levels x 3 TFs x 2 sides = 36)
  - SMA200 distance thresholds (8 levels x 6 TFs x 2 sides = 96)
  - EMA20 distance thresholds (8 levels x 6 TFs x 2 sides = 96)
  - THMA/Hull trend (t_up, tco, tcu) (3 types x 6 TFs x 2 sides = 36)
  - SMA crossover/crossunder (6 TFs x 2 = 12)
  - Linearity R-squared (6 levels x 2 sides = 12)
  - LinReg channel %B (8 levels x 3 TFs x 2 sides = 48)
  - EMA distance cross (ema20 vs ema50, ema50 vs ema200) (4 x 6 TFs = 24)
  TOTAL: ~1,400+ indicator conditions
"""
import json, os, sys, time, math, sqlite3, traceback
from pathlib import Path
import numpy as np
from datetime import datetime
from collections import defaultdict

KLINES_DIR = Path("/home/niels/binance-sandbox/klines_cache/tradier")
DB_PATH = Path("/home/niels/binance-sandbox/backtest_framework/results/per_indicator_sweep_tradier.db")
os.makedirs(DB_PATH.parent, exist_ok=True)
FEE = 0.0010; SLIPPAGE = 0.0010; COST = FEE + SLIPPAGE  # 0.20% RT for stocks
WARMUP = 300; POS_SIZE = 55.0; MAX_CONCURRENT = 20; MIN_TRADES = 50; MAX_HOLD = 500
TRAIN_RATIO = 0.70

TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "D"]
TF_BARS_PER_DAY = {"1m": 390, "5m": 78, "15m": 26, "1h": 7, "4h": 2, "D": 1}

# ═══════════════════════════════════════════════════════════
# INDICATOR FUNCTIONS (vectorized numpy, matching tradier_indicators.py)
# ═══════════════════════════════════════════════════════════
def stoch_kd(h, l, c, kp=14, ks=5, ds=5):
    n = len(c); rk = np.full(n, 50.0)
    for i in range(kp - 1, n):
        hi = np.max(h[i - kp + 1:i + 1]); li = np.min(l[i - kp + 1:i + 1])
        rk[i] = (c[i] - li) / (hi - li) * 100 if hi > li else 50.0
    k = np.full(n, 50.0)
    for i in range(ks - 1, n): k[i] = rk[i - ks + 1:i + 1].mean()
    d = np.full(n, 50.0)
    for i in range(ds - 1, n): d[i] = k[i - ds + 1:i + 1].mean()
    return k, d

def donchian(h, l, p=20):
    n = len(h); dh = np.zeros(n); dl = np.zeros(n)
    for i in range(p, n): dh[i] = np.max(h[i - p:i]); dl[i] = np.min(l[i - p:i])
    db = (dh + dl) / 2
    return dh, dl, db

def donchian4(h, l):
    return donchian(h, l, p=4)

def heikin_ashi(o, h, l, c):
    hc = (o + h + l + c) / 4; ho = np.empty(len(c)); ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(c)): ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    return ho, hc

def atr_ema(h, l, c, p=14):
    n = len(c); tr = np.empty(n); tr[0] = h[0] - l[0]
    for i in range(1, n): tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    a = np.zeros(n)
    if n > p: a[p] = tr[:p + 1].mean()
    for i in range(p + 1, n): a[i] = (a[i - 1] * (p - 1) + tr[i]) / p
    return a

def atr_long(h, l, c, p=100):
    return atr_ema(h, l, c, p)

def rsi(c, p=14):
    n = len(c); r = np.full(n, 50.0)
    if n < p + 1: return r
    d2 = np.diff(c); g = np.where(d2 > 0, d2, 0.0); lo = np.where(d2 < 0, -d2, 0.0)
    ag = g[:p].mean(); al = lo[:p].mean()
    r[p] = 100 - 100 / (1 + ag / max(al, 1e-10))
    for i in range(p, len(d2)):
        ag = (ag * (p - 1) + g[i]) / p; al = (al * (p - 1) + lo[i]) / p
        r[i + 1] = 100 - 100 / (1 + ag / max(al, 1e-10))
    return r

def ema_arr(c, p):
    e = np.empty(len(c)); e[0] = c[0]; a = 2 / (p + 1)
    for i in range(1, len(c)): e[i] = c[i] * a + e[i - 1] * (1 - a)
    return e

def sma_arr(c, p):
    n = len(c); s = np.full(n, c[0] if n > 0 else 0.0)
    if n >= p:
        cs = np.cumsum(c)
        s[p - 1:] = (cs[p - 1:] - np.concatenate([[0], cs[:-(p)]])) / p
        s[:p - 1] = s[p - 1]
    return s

def wavetrend(h, l, c):
    hlc3 = (h + l + c) / 3; esa = ema_arr(hlc3, 10); da = ema_arr(np.abs(hlc3 - esa), 10)
    with np.errstate(divide='ignore', invalid='ignore'):
        ci = np.where(da != 0, (hlc3 - esa) / (0.015 * da), 0.0)
    wt1 = ema_arr(np.nan_to_num(ci, 0.0), 21)
    wt2 = sma_arr(wt1, 3)
    return wt1, wt2

def mfi(h, l, c, v, p=14):
    n = len(c); r = np.full(n, 50.0); tp = (h + l + c) / 3; mf = tp * v
    for i in range(p, n):
        pos = sum(mf[j] for j in range(i - p + 1, i + 1) if tp[j] > tp[j - 1])
        neg = sum(mf[j] for j in range(i - p + 1, i + 1) if tp[j] < tp[j - 1])
        r[i] = 100 - 100 / (1 + pos / max(neg, 1e-10))
    return r

def rel_vol(v, p=20):
    rv = np.ones(len(v))
    for i in range(p, len(v)):
        avg = v[i - p:i].mean()
        rv[i] = v[i] / avg if avg > 0 else 1.0
    return rv

def linreg(c, p=50):
    n = len(c); slope = np.zeros(n); r2 = np.zeros(n); intercept = np.zeros(n)
    x = np.arange(p, dtype=np.float64); xm = x.mean(); xv = ((x - xm) ** 2).sum()
    for i in range(p - 1, n):
        y = c[i - p + 1:i + 1]; ym = y.mean()
        s = ((x - xm) * (y - ym)).sum() / xv if xv > 0 else 0
        b = ym - s * xm; slope[i] = s; intercept[i] = b
        ss_res = ((y - (s * x + b)) ** 2).sum(); ss_tot = ((y - ym) ** 2).sum()
        r2[i] = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    return slope, intercept, r2

def linreg_channel(c, p=50, std_m=2.5):
    slope, intercept, r2 = linreg(c, p)
    n = len(c); upper = np.zeros(n); lower = np.zeros(n); pct_b = np.full(n, 0.5)
    for i in range(p - 1, n):
        y = c[i - p + 1:i + 1]
        x = np.arange(p, dtype=np.float64)
        pred = slope[i] * x + intercept[i]
        std = np.std(y - pred)
        mid = pred[-1]
        upper[i] = mid + std_m * std
        lower[i] = mid - std_m * std
        rng = upper[i] - lower[i]
        pct_b[i] = (c[i] - lower[i]) / rng if rng > 0 else 0.5
    return upper, lower, pct_b

def bollinger(c, p=20, std_m=2.0):
    mid = sma_arr(c, p); std = np.zeros(len(c))
    for i in range(p - 1, len(c)): std[i] = np.std(c[i - p + 1:i + 1])
    u = mid + std_m * std; lo2 = mid - std_m * std
    with np.errstate(divide='ignore', invalid='ignore'):
        pb = np.where((u - lo2) > 0, (c - lo2) / (u - lo2), 0.5)
    return u, lo2, np.nan_to_num(pb, 0.5), mid

def hull_thma(c, p=9):
    n = len(c)
    if n < p * 3: return np.ones(n, dtype=bool), np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    wma1 = sma_arr(c, p); wma2 = sma_arr(c, max(p // 2, 1))
    raw = 2 * wma2 - wma1; hull = sma_arr(raw, max(int(p ** 0.5), 1))
    hull_prev = np.roll(hull, 1); hull_prev[0] = hull[0]
    t_up = hull > hull_prev
    tco = t_up & ~np.roll(t_up, 1)
    tcu = ~t_up & np.roll(t_up, 1)
    return t_up, tco, tcu

# ═══════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════
def load_klines(symbol, tf):
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists(): return None
    try:
        with open(path) as f: raw = json.load(f)
        if not raw or len(raw) < WARMUP + 100: return None
        o = np.array([float(r.get('open', 0)) for r in raw])
        h = np.array([float(r.get('high', 0)) for r in raw])
        l = np.array([float(r.get('low', 0)) for r in raw])
        c = np.array([float(r.get('close', 0)) for r in raw])
        v = np.array([float(r.get('volume', 0)) for r in raw])
        # Filter out zero prices
        valid = (c > 0) & (o > 0) & (h > 0) & (l > 0)
        if valid.sum() < WARMUP + 100: return None
        return {'o': o[valid], 'h': h[valid], 'l': l[valid], 'c': c[valid], 'v': v[valid], 'n': int(valid.sum())}
    except Exception:
        return None

def get_symbols():
    syms = set()
    for f in KLINES_DIR.iterdir():
        if f.suffix == '.json' and '_' in f.stem:
            sym = f.stem.rsplit('_', 1)[0]
            syms.add(sym)
    return sorted(syms)

# ═══════════════════════════════════════════════════════════
# COMPUTE ALL INDICATORS FOR A SYMBOL/TF
# ═══════════════════════════════════════════════════════════
def compute_all_indicators(d):
    o, h, l, c, v, n = d['o'], d['h'], d['l'], d['c'], d['v'], d['n']
    ind = {}
    ind['stoch_k'], ind['stoch_d'] = stoch_kd(h, l, c)
    ind['dc_high'], ind['dc_low'], ind['dc_basis'] = donchian(h, l, 20)
    ind['dc_high4'], ind['dc_low4'], _ = donchian(h, l, 4)
    ho, hc = heikin_ashi(o, h, l, c)
    ind['ha_green'] = hc > ho
    ind['atr'] = atr_ema(h, l, c, 14)
    ind['atr_long'] = atr_long(h, l, c, 100)
    ind['rsi'] = rsi(c, 14)
    ind['ema20'] = ema_arr(c, 20)
    ind['ema50'] = ema_arr(c, 50)
    ind['ema200'] = ema_arr(c, 200)
    ind['sma200'] = sma_arr(c, 200)
    ind['wt1'], ind['wt2'] = wavetrend(h, l, c)
    ind['mfi'] = mfi(h, l, c, v, 14)
    ind['rel_vol'] = rel_vol(v, 20)
    ind['lr_slope'], ind['lr_intercept'], ind['lr_r2'] = linreg(c, 50)
    ind['lr_upper'], ind['lr_lower'], ind['lr_pct_b'] = linreg_channel(c, 50, 2.5)
    ind['bb_upper'], ind['bb_lower'], ind['bb_pct_b'], ind['bb_mid'] = bollinger(c, 20, 2.0)
    ind['t_up'], ind['tco'], ind['tcu'] = hull_thma(c, 9)
    ind['close'] = c
    ind['high'] = h
    ind['low'] = l
    ind['open'] = o
    ind['volume'] = v
    # DC width (normalized)
    dc_range = ind['dc_high'] - ind['dc_low']
    with np.errstate(divide='ignore', invalid='ignore'):
        ind['dc_width'] = np.where(ind['dc_basis'] > 0, dc_range / ind['dc_basis'] * 100, 0)
    # DC position (price within channel, 0=at low, 1=at high)
    with np.errstate(divide='ignore', invalid='ignore'):
        ind['dc_pos'] = np.where(dc_range > 0, (c - ind['dc_low']) / dc_range, 0.5)
    # BB width
    with np.errstate(divide='ignore', invalid='ignore'):
        ind['bb_width'] = np.where(ind['bb_mid'] > 0, (ind['bb_upper'] - ind['bb_lower']) / ind['bb_mid'] * 100, 0)
    # EMA distances
    with np.errstate(divide='ignore', invalid='ignore'):
        ind['ema20_dist'] = np.where(ind['ema20'] > 0, (c - ind['ema20']) / ind['ema20'] * 100, 0)
        ind['ema50_dist'] = np.where(ind['ema50'] > 0, (c - ind['ema50']) / ind['ema50'] * 100, 0)
        ind['sma200_dist'] = np.where(ind['sma200'] > 0, (c - ind['sma200']) / ind['sma200'] * 100, 0)
    # ATR ratio (short/long)
    with np.errstate(divide='ignore', invalid='ignore'):
        ind['atr_ratio'] = np.where(ind['atr_long'] > 0, ind['atr'] / ind['atr_long'], 1.0)
    # WT score
    ind['wt_score'] = ind['wt1'] - ind['wt2']
    return ind

# ═══════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════
def backtest_condition(all_data, condition_fn, is_long, exit_fn=None):
    """
    Run a portfolio backtest across all symbols.
    condition_fn(ind, bar) -> bool  (entry signal)
    exit_fn(ind, bar, entry_bar) -> bool  (exit signal, default: condition flips)
    Returns: (sharpe, n_trades, win_rate, avg_ret, max_dd) for train and test
    """
    # Collect all trades across all symbols
    all_trades_train = []
    all_trades_test = []
    for sym, tf_data in all_data.items():
        ind = tf_data['ind']
        n = tf_data['n']
        c = ind['close']
        split = int(n * TRAIN_RATIO)
        # Run backtest
        for phase, start, end, trade_list in [('train', WARMUP, split, all_trades_train), ('test', split, n, all_trades_test)]:
            in_position = False
            entry_price = 0.0
            entry_bar = 0
            concurrent = 0
            for bar in range(start, end):
                if not in_position:
                    try:
                        if condition_fn(ind, bar):
                            entry_price = c[bar]
                            entry_bar = bar
                            in_position = True
                    except Exception:
                        pass
                else:
                    # Check exit
                    bars_held = bar - entry_bar
                    should_exit = bars_held >= MAX_HOLD
                    if not should_exit and exit_fn is not None:
                        try:
                            should_exit = exit_fn(ind, bar, entry_bar)
                        except Exception:
                            should_exit = False
                    elif not should_exit:
                        try:
                            should_exit = not condition_fn(ind, bar)
                        except Exception:
                            should_exit = True
                    if should_exit:
                        exit_price = c[bar]
                        if is_long:
                            ret = (exit_price - entry_price) / entry_price - COST
                        else:
                            ret = (entry_price - exit_price) / entry_price - COST
                        trade_list.append(ret)
                        in_position = False
    return all_trades_train, all_trades_test

def calc_daily_sharpe(trades):
    if len(trades) < MIN_TRADES: return -999.0, len(trades), 0.0, 0.0, 0.0
    rets = np.array(trades)
    # Group into "days" of ~20 trades
    chunk = max(1, len(rets) // max(1, len(rets) // 20))
    if chunk < 1: chunk = 1
    daily_rets = []
    for i in range(0, len(rets), chunk):
        daily_rets.append(rets[i:i + chunk].sum())
    daily_rets = np.array(daily_rets)
    if len(daily_rets) < 5: return -999.0, len(trades), 0.0, 0.0, 0.0
    mean_r = daily_rets.mean()
    std_r = daily_rets.std()
    sharpe = (mean_r / std_r * np.sqrt(252)) if std_r > 1e-10 else 0.0
    win_rate = (rets > 0).mean()
    avg_ret = rets.mean() * 100
    # Max drawdown
    cum = np.cumsum(rets)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    max_dd = dd.max() * 100 if len(dd) > 0 else 0.0
    return sharpe, len(trades), win_rate, avg_ret, max_dd

# ═══════════════════════════════════════════════════════════
# GENERATE ALL INDICATOR CONDITIONS
# ═══════════════════════════════════════════════════════════
def generate_conditions():
    """Generate all indicator conditions to test."""
    conditions = []

    for tf in TIMEFRAMES:
        # === 1. STOCHASTIC K THRESHOLDS ===
        for thresh in [10, 15, 18, 20, 22, 25, 30, 35, 40, 60, 65, 70, 75, 80, 85, 90]:
            # Long: stoch_k < thresh (oversold entry)
            conditions.append({
                'name': f'stoch_k_lt_{thresh}_{tf}',
                'side': 'LONG',
                'tf': tf,
                'entry': lambda ind, bar, t=thresh: ind['stoch_k'][bar] < t,
                'exit': lambda ind, bar, eb, t=thresh: ind['stoch_k'][bar] > (100 - t),
            })
            # Short: stoch_k > thresh (overbought entry)
            conditions.append({
                'name': f'stoch_k_gt_{thresh}_{tf}',
                'side': 'SHORT',
                'tf': tf,
                'entry': lambda ind, bar, t=thresh: ind['stoch_k'][bar] > t,
                'exit': lambda ind, bar, eb, t=thresh: ind['stoch_k'][bar] < (100 - t),
            })

        # === 2. STOCHASTIC K > D ===
        conditions.append({
            'name': f'stoch_k_gt_d_{tf}',
            'side': 'LONG',
            'tf': tf,
            'entry': lambda ind, bar: ind['stoch_k'][bar] > ind['stoch_d'][bar] and ind['stoch_k'][bar] < 30,
            'exit': lambda ind, bar, eb: ind['stoch_k'][bar] < ind['stoch_d'][bar],
        })
        conditions.append({
            'name': f'stoch_k_lt_d_{tf}',
            'side': 'SHORT',
            'tf': tf,
            'entry': lambda ind, bar: ind['stoch_k'][bar] < ind['stoch_d'][bar] and ind['stoch_k'][bar] > 70,
            'exit': lambda ind, bar, eb: ind['stoch_k'][bar] > ind['stoch_d'][bar],
        })

        # === 3. STOCHASTIC CROSSOVER (K crosses above D from oversold) ===
        conditions.append({
            'name': f'stoch_crossover_{tf}',
            'side': 'LONG',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['stoch_k'][bar] > ind['stoch_d'][bar] and ind['stoch_k'][bar - 1] <= ind['stoch_d'][bar - 1] and ind['stoch_k'][bar] < 35,
            'exit': lambda ind, bar, eb: ind['stoch_k'][bar] < ind['stoch_d'][bar] and ind['stoch_k'][bar] > 65,
        })
        conditions.append({
            'name': f'stoch_crossunder_{tf}',
            'side': 'SHORT',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['stoch_k'][bar] < ind['stoch_d'][bar] and ind['stoch_k'][bar - 1] >= ind['stoch_d'][bar - 1] and ind['stoch_k'][bar] > 65,
            'exit': lambda ind, bar, eb: ind['stoch_k'][bar] > ind['stoch_d'][bar] and ind['stoch_k'][bar] < 35,
        })

        # === 4. RSI THRESHOLDS ===
        for thresh in [20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80]:
            conditions.append({
                'name': f'rsi_lt_{thresh}_{tf}',
                'side': 'LONG',
                'tf': tf,
                'entry': lambda ind, bar, t=thresh: ind['rsi'][bar] < t,
                'exit': lambda ind, bar, eb, t=thresh: ind['rsi'][bar] > (100 - t),
            })
            conditions.append({
                'name': f'rsi_gt_{thresh}_{tf}',
                'side': 'SHORT',
                'tf': tf,
                'entry': lambda ind, bar, t=thresh: ind['rsi'][bar] > t,
                'exit': lambda ind, bar, eb, t=thresh: ind['rsi'][bar] < (100 - t),
            })

        # === 5. MFI THRESHOLDS ===
        for thresh in [20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80]:
            conditions.append({
                'name': f'mfi_lt_{thresh}_{tf}',
                'side': 'LONG',
                'tf': tf,
                'entry': lambda ind, bar, t=thresh: ind['mfi'][bar] < t,
                'exit': lambda ind, bar, eb, t=thresh: ind['mfi'][bar] > (100 - t),
            })
            conditions.append({
                'name': f'mfi_gt_{thresh}_{tf}',
                'side': 'SHORT',
                'tf': tf,
                'entry': lambda ind, bar, t=thresh: ind['mfi'][bar] > t,
                'exit': lambda ind, bar, eb, t=thresh: ind['mfi'][bar] < (100 - t),
            })

        # === 6. WAVETREND SCORE THRESHOLDS ===
        for thresh in [-60, -50, -40, -30, -20, 20, 30, 40, 50, 60]:
            if thresh < 0:
                conditions.append({
                    'name': f'wt_score_lt_{thresh}_{tf}',
                    'side': 'LONG',
                    'tf': tf,
                    'entry': lambda ind, bar, t=thresh: ind['wt_score'][bar] < t,
                    'exit': lambda ind, bar, eb, t=thresh: ind['wt_score'][bar] > -t,
                })
            else:
                conditions.append({
                    'name': f'wt_score_gt_{thresh}_{tf}',
                    'side': 'SHORT',
                    'tf': tf,
                    'entry': lambda ind, bar, t=thresh: ind['wt_score'][bar] > t,
                    'exit': lambda ind, bar, eb, t=thresh: ind['wt_score'][bar] < -t,
                })

        # === 7. WAVETREND CROSSOVER/CROSSUNDER ===
        conditions.append({
            'name': f'wt_crossover_{tf}',
            'side': 'LONG',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['wt1'][bar] > ind['wt2'][bar] and ind['wt1'][bar - 1] <= ind['wt2'][bar - 1] and ind['wt1'][bar] < -50,
            'exit': lambda ind, bar, eb: ind['wt1'][bar] < ind['wt2'][bar] and ind['wt1'][bar] > 50,
        })
        conditions.append({
            'name': f'wt_crossunder_{tf}',
            'side': 'SHORT',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['wt1'][bar] < ind['wt2'][bar] and ind['wt1'][bar - 1] >= ind['wt2'][bar - 1] and ind['wt1'][bar] > 50,
            'exit': lambda ind, bar, eb: ind['wt1'][bar] > ind['wt2'][bar] and ind['wt1'][bar] < -50,
        })

        # === 8. HEIKIN ASHI ===
        conditions.append({
            'name': f'ha_green_{tf}',
            'side': 'LONG',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['ha_green'][bar] and not ind['ha_green'][bar - 1],
            'exit': lambda ind, bar, eb: not ind['ha_green'][bar],
        })
        conditions.append({
            'name': f'ha_red_{tf}',
            'side': 'SHORT',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and not ind['ha_green'][bar] and ind['ha_green'][bar - 1],
            'exit': lambda ind, bar, eb: ind['ha_green'][bar],
        })

        # === 9. DC POSITION THRESHOLDS ===
        for thresh in [0.05, 0.10, 0.15, 0.20, 0.80, 0.85, 0.90, 0.95]:
            if thresh < 0.5:
                conditions.append({
                    'name': f'dc_pos_lt_{int(thresh*100)}_{tf}',
                    'side': 'LONG',
                    'tf': tf,
                    'entry': lambda ind, bar, t=thresh: ind['dc_pos'][bar] < t,
                    'exit': lambda ind, bar, eb, t=thresh: ind['dc_pos'][bar] > 0.5,
                })
            else:
                conditions.append({
                    'name': f'dc_pos_gt_{int(thresh*100)}_{tf}',
                    'side': 'SHORT',
                    'tf': tf,
                    'entry': lambda ind, bar, t=thresh: ind['dc_pos'][bar] > t,
                    'exit': lambda ind, bar, eb, t=thresh: ind['dc_pos'][bar] < 0.5,
                })

        # === 10. DC WIDTH THRESHOLDS ===
        for thresh in [1, 2, 3, 5, 8, 10, 15, 20]:
            conditions.append({
                'name': f'dc_width_lt_{thresh}_{tf}_long',
                'side': 'LONG',
                'tf': tf,
                'entry': lambda ind, bar, t=thresh: ind['dc_width'][bar] < t and ind['dc_pos'][bar] < 0.2,
                'exit': lambda ind, bar, eb, t=thresh: ind['dc_width'][bar] > t * 1.5 or ind['dc_pos'][bar] > 0.8,
            })
            conditions.append({
                'name': f'dc_width_lt_{thresh}_{tf}_short',
                'side': 'SHORT',
                'tf': tf,
                'entry': lambda ind, bar, t=thresh: ind['dc_width'][bar] < t and ind['dc_pos'][bar] > 0.8,
                'exit': lambda ind, bar, eb, t=thresh: ind['dc_width'][bar] > t * 1.5 or ind['dc_pos'][bar] < 0.2,
            })

        # === 11. DC FLOOR RISING / CEIL FALLING ===
        conditions.append({
            'name': f'dc_floor_rising_{tf}',
            'side': 'LONG',
            'tf': tf,
            'entry': lambda ind, bar: bar > 1 and ind['dc_low'][bar] > ind['dc_low'][bar - 1] and ind['dc_low'][bar - 1] > 0,
            'exit': lambda ind, bar, eb: ind['dc_low'][bar] < ind['dc_low'][bar - 1] if bar > 0 else False,
        })
        conditions.append({
            'name': f'dc_ceil_falling_{tf}',
            'side': 'SHORT',
            'tf': tf,
            'entry': lambda ind, bar: bar > 1 and ind['dc_high'][bar] < ind['dc_high'][bar - 1] and ind['dc_high'][bar - 1] > 0,
            'exit': lambda ind, bar, eb: ind['dc_high'][bar] > ind['dc_high'][bar - 1] if bar > 0 else False,
        })

        # === 12. DC LOW4 RISING / HIGH4 FALLING ===
        conditions.append({
            'name': f'dc_low4_rising_{tf}',
            'side': 'LONG',
            'tf': tf,
            'entry': lambda ind, bar: bar > 1 and ind['dc_low4'][bar] > ind['dc_low4'][bar - 1] and ind['dc_low4'][bar - 1] > 0,
            'exit': lambda ind, bar, eb: ind['dc_low4'][bar] < ind['dc_low4'][bar - 1] if bar > 0 else False,
        })
        conditions.append({
            'name': f'dc_high4_falling_{tf}',
            'side': 'SHORT',
            'tf': tf,
            'entry': lambda ind, bar: bar > 1 and ind['dc_high4'][bar] < ind['dc_high4'][bar - 1] and ind['dc_high4'][bar - 1] > 0,
            'exit': lambda ind, bar, eb: ind['dc_high4'][bar] > ind['dc_high4'][bar - 1] if bar > 0 else False,
        })

        # === 13. DC BASIS/HIGH/LOW CROSSOVERS ===
        conditions.append({
            'name': f'dc_basis_crossover_{tf}',
            'side': 'LONG',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['close'][bar] > ind['dc_basis'][bar] and ind['close'][bar - 1] <= ind['dc_basis'][bar - 1] and ind['dc_basis'][bar] > 0,
            'exit': lambda ind, bar, eb: ind['close'][bar] < ind['dc_basis'][bar],
        })
        conditions.append({
            'name': f'dc_basis_crossunder_{tf}',
            'side': 'SHORT',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['close'][bar] < ind['dc_basis'][bar] and ind['close'][bar - 1] >= ind['dc_basis'][bar - 1] and ind['dc_basis'][bar] > 0,
            'exit': lambda ind, bar, eb: ind['close'][bar] > ind['dc_basis'][bar],
        })
        conditions.append({
            'name': f'dc_high_crossover_{tf}',
            'side': 'LONG',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['close'][bar] > ind['dc_high'][bar] and ind['close'][bar - 1] <= ind['dc_high'][bar - 1] and ind['dc_high'][bar] > 0,
            'exit': lambda ind, bar, eb: ind['close'][bar] < ind['dc_basis'][bar],
        })
        conditions.append({
            'name': f'dc_low_crossunder_{tf}',
            'side': 'SHORT',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['close'][bar] < ind['dc_low'][bar] and ind['close'][bar - 1] >= ind['dc_low'][bar - 1] and ind['dc_low'][bar] > 0,
            'exit': lambda ind, bar, eb: ind['close'][bar] > ind['dc_basis'][bar],
        })
        conditions.append({
            'name': f'dc_high_crossunder_{tf}',
            'side': 'SHORT',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['close'][bar] < ind['dc_high'][bar] and ind['close'][bar - 1] >= ind['dc_high'][bar - 1] and ind['dc_high'][bar] > 0,
            'exit': lambda ind, bar, eb: ind['close'][bar] > ind['dc_high'][bar],
        })
        conditions.append({
            'name': f'dc_low_crossover_{tf}',
            'side': 'LONG',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['close'][bar] > ind['dc_low'][bar] and ind['close'][bar - 1] <= ind['dc_low'][bar - 1] and ind['dc_low'][bar] > 0,
            'exit': lambda ind, bar, eb: ind['close'][bar] < ind['dc_low'][bar],
        })

        # === 14. RELATIVE VOLUME ===
        for thresh in [0.5, 0.8, 1.0, 1.2, 1.5, 2.0]:
            conditions.append({
                'name': f'rvol_gt_{int(thresh*10)}_{tf}_long',
                'side': 'LONG',
                'tf': tf,
                'entry': lambda ind, bar, t=thresh: ind['rel_vol'][bar] > t and ind['stoch_k'][bar] < 30,
                'exit': lambda ind, bar, eb, t=thresh: ind['stoch_k'][bar] > 70,
            })
            conditions.append({
                'name': f'rvol_gt_{int(thresh*10)}_{tf}_short',
                'side': 'SHORT',
                'tf': tf,
                'entry': lambda ind, bar, t=thresh: ind['rel_vol'][bar] > t and ind['stoch_k'][bar] > 70,
                'exit': lambda ind, bar, eb, t=thresh: ind['stoch_k'][bar] < 30,
            })

        # === 15. ATR RATIO (short/long) ===
        for thresh in [0.5, 0.7, 0.8, 1.0, 1.2, 1.5]:
            conditions.append({
                'name': f'atr_ratio_lt_{int(thresh*10)}_{tf}_long',
                'side': 'LONG',
                'tf': tf,
                'entry': lambda ind, bar, t=thresh: ind['atr_ratio'][bar] < t and ind['stoch_k'][bar] < 35,
                'exit': lambda ind, bar, eb, t=thresh: ind['atr_ratio'][bar] > t * 1.5 or ind['stoch_k'][bar] > 70,
            })
            conditions.append({
                'name': f'atr_ratio_gt_{int(thresh*10)}_{tf}_short',
                'side': 'SHORT',
                'tf': tf,
                'entry': lambda ind, bar, t=thresh: ind['atr_ratio'][bar] > t and ind['stoch_k'][bar] > 65,
                'exit': lambda ind, bar, eb, t=thresh: ind['atr_ratio'][bar] < t * 0.7 or ind['stoch_k'][bar] < 30,
            })

        # === 16. LINREG SLOPE DIRECTION ===
        conditions.append({
            'name': f'lr_slope_pos_{tf}',
            'side': 'LONG',
            'tf': tf,
            'entry': lambda ind, bar: ind['lr_slope'][bar] > 0 and ind['stoch_k'][bar] < 40,
            'exit': lambda ind, bar, eb: ind['lr_slope'][bar] < 0,
        })
        conditions.append({
            'name': f'lr_slope_neg_{tf}',
            'side': 'SHORT',
            'tf': tf,
            'entry': lambda ind, bar: ind['lr_slope'][bar] < 0 and ind['stoch_k'][bar] > 60,
            'exit': lambda ind, bar, eb: ind['lr_slope'][bar] > 0,
        })

        # === 17. SMA200 DISTANCE THRESHOLDS ===
        for thresh in [-10, -5, -3, -1, 1, 3, 5, 10]:
            if thresh < 0:
                conditions.append({
                    'name': f'sma200_dist_lt_{thresh}_{tf}',
                    'side': 'LONG',
                    'tf': tf,
                    'entry': lambda ind, bar, t=thresh: ind['sma200_dist'][bar] < t,
                    'exit': lambda ind, bar, eb, t=thresh: ind['sma200_dist'][bar] > 0,
                })
            else:
                conditions.append({
                    'name': f'sma200_dist_gt_{thresh}_{tf}',
                    'side': 'SHORT',
                    'tf': tf,
                    'entry': lambda ind, bar, t=thresh: ind['sma200_dist'][bar] > t,
                    'exit': lambda ind, bar, eb, t=thresh: ind['sma200_dist'][bar] < 0,
                })

        # === 18. EMA20 DISTANCE THRESHOLDS ===
        for thresh in [-5, -3, -2, -1, 1, 2, 3, 5]:
            if thresh < 0:
                conditions.append({
                    'name': f'ema20_dist_lt_{thresh}_{tf}',
                    'side': 'LONG',
                    'tf': tf,
                    'entry': lambda ind, bar, t=thresh: ind['ema20_dist'][bar] < t,
                    'exit': lambda ind, bar, eb, t=thresh: ind['ema20_dist'][bar] > 0,
                })
            else:
                conditions.append({
                    'name': f'ema20_dist_gt_{thresh}_{tf}',
                    'side': 'SHORT',
                    'tf': tf,
                    'entry': lambda ind, bar, t=thresh: ind['ema20_dist'][bar] > t,
                    'exit': lambda ind, bar, eb, t=thresh: ind['ema20_dist'][bar] < 0,
                })

        # === 19. HULL/THMA TREND ===
        conditions.append({
            'name': f't_up_true_{tf}',
            'side': 'LONG',
            'tf': tf,
            'entry': lambda ind, bar: bool(ind['t_up'][bar]) and ind['stoch_k'][bar] < 40,
            'exit': lambda ind, bar, eb: not bool(ind['t_up'][bar]),
        })
        conditions.append({
            'name': f't_up_false_{tf}',
            'side': 'SHORT',
            'tf': tf,
            'entry': lambda ind, bar: not bool(ind['t_up'][bar]) and ind['stoch_k'][bar] > 60,
            'exit': lambda ind, bar, eb: bool(ind['t_up'][bar]),
        })
        conditions.append({
            'name': f'tco_{tf}',
            'side': 'LONG',
            'tf': tf,
            'entry': lambda ind, bar: bool(ind['tco'][bar]),
            'exit': lambda ind, bar, eb: bool(ind['tcu'][bar]) or (bar - eb > MAX_HOLD // 2),
        })
        conditions.append({
            'name': f'tcu_{tf}',
            'side': 'SHORT',
            'tf': tf,
            'entry': lambda ind, bar: bool(ind['tcu'][bar]),
            'exit': lambda ind, bar, eb: bool(ind['tco'][bar]) or (bar - eb > MAX_HOLD // 2),
        })

        # === 20. SMA CROSSOVER (ema20 vs ema50) ===
        conditions.append({
            'name': f'ema20_cross_ema50_up_{tf}',
            'side': 'LONG',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['ema20'][bar] > ind['ema50'][bar] and ind['ema20'][bar - 1] <= ind['ema50'][bar - 1],
            'exit': lambda ind, bar, eb: ind['ema20'][bar] < ind['ema50'][bar],
        })
        conditions.append({
            'name': f'ema20_cross_ema50_down_{tf}',
            'side': 'SHORT',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['ema20'][bar] < ind['ema50'][bar] and ind['ema20'][bar - 1] >= ind['ema50'][bar - 1],
            'exit': lambda ind, bar, eb: ind['ema20'][bar] > ind['ema50'][bar],
        })

        # === 21. EMA50 vs EMA200 cross ===
        conditions.append({
            'name': f'ema50_cross_ema200_up_{tf}',
            'side': 'LONG',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['ema50'][bar] > ind['ema200'][bar] and ind['ema50'][bar - 1] <= ind['ema200'][bar - 1],
            'exit': lambda ind, bar, eb: ind['ema50'][bar] < ind['ema200'][bar],
        })
        conditions.append({
            'name': f'ema50_cross_ema200_down_{tf}',
            'side': 'SHORT',
            'tf': tf,
            'entry': lambda ind, bar: bar > 0 and ind['ema50'][bar] < ind['ema200'][bar] and ind['ema50'][bar - 1] >= ind['ema200'][bar - 1],
            'exit': lambda ind, bar, eb: ind['ema50'][bar] > ind['ema200'][bar],
        })

    # === BB and LR_PCT_B only for 1h, 4h, D (as in tradier_indicators.py) ===
    for tf in ["1h", "4h", "D"]:
        # === 22. BB %B THRESHOLDS ===
        for thresh in [0.05, 0.10, 0.15, 0.20, 0.80, 0.85, 0.90, 0.95]:
            if thresh < 0.5:
                conditions.append({
                    'name': f'bb_pct_b_lt_{int(thresh*100)}_{tf}',
                    'side': 'LONG',
                    'tf': tf,
                    'entry': lambda ind, bar, t=thresh: ind['bb_pct_b'][bar] < t,
                    'exit': lambda ind, bar, eb, t=thresh: ind['bb_pct_b'][bar] > 0.5,
                })
            else:
                conditions.append({
                    'name': f'bb_pct_b_gt_{int(thresh*100)}_{tf}',
                    'side': 'SHORT',
                    'tf': tf,
                    'entry': lambda ind, bar, t=thresh: ind['bb_pct_b'][bar] > t,
                    'exit': lambda ind, bar, eb, t=thresh: ind['bb_pct_b'][bar] < 0.5,
                })

        # === 23. BB WIDTH THRESHOLDS ===
        for thresh in [1, 2, 3, 5, 8, 12]:
            conditions.append({
                'name': f'bb_width_lt_{thresh}_{tf}_long',
                'side': 'LONG',
                'tf': tf,
                'entry': lambda ind, bar, t=thresh: ind['bb_width'][bar] < t and ind['bb_pct_b'][bar] < 0.2,
                'exit': lambda ind, bar, eb, t=thresh: ind['bb_pct_b'][bar] > 0.8,
            })
            conditions.append({
                'name': f'bb_width_lt_{thresh}_{tf}_short',
                'side': 'SHORT',
                'tf': tf,
                'entry': lambda ind, bar, t=thresh: ind['bb_width'][bar] < t and ind['bb_pct_b'][bar] > 0.8,
                'exit': lambda ind, bar, eb, t=thresh: ind['bb_pct_b'][bar] < 0.2,
            })

        # === 24. LINREG CHANNEL %B ===
        for thresh in [0.05, 0.10, 0.15, 0.20, 0.80, 0.85, 0.90, 0.95]:
            if thresh < 0.5:
                conditions.append({
                    'name': f'lr_pct_b_lt_{int(thresh*100)}_{tf}',
                    'side': 'LONG',
                    'tf': tf,
                    'entry': lambda ind, bar, t=thresh: ind['lr_pct_b'][bar] < t,
                    'exit': lambda ind, bar, eb, t=thresh: ind['lr_pct_b'][bar] > 0.5,
                })
            else:
                conditions.append({
                    'name': f'lr_pct_b_gt_{int(thresh*100)}_{tf}',
                    'side': 'SHORT',
                    'tf': tf,
                    'entry': lambda ind, bar, t=thresh: ind['lr_pct_b'][bar] > t,
                    'exit': lambda ind, bar, eb, t=thresh: ind['lr_pct_b'][bar] < 0.5,
                })

    # === 25. LINEARITY (R-squared) — 1h only (matching tradier_indicators.py) ===
    for thresh in [0.3, 0.5, 0.6, 0.7, 0.8, 0.9]:
        conditions.append({
            'name': f'linearity_r2_gt_{int(thresh*100)}_long',
            'side': 'LONG',
            'tf': '1h',
            'entry': lambda ind, bar, t=thresh: ind['lr_r2'][bar] > t and ind['lr_slope'][bar] > 0,
            'exit': lambda ind, bar, eb, t=thresh: ind['lr_r2'][bar] < t * 0.7 or ind['lr_slope'][bar] < 0,
        })
        conditions.append({
            'name': f'linearity_r2_gt_{int(thresh*100)}_short',
            'side': 'SHORT',
            'tf': '1h',
            'entry': lambda ind, bar, t=thresh: ind['lr_r2'][bar] > t and ind['lr_slope'][bar] < 0,
            'exit': lambda ind, bar, eb, t=thresh: ind['lr_r2'][bar] < t * 0.7 or ind['lr_slope'][bar] > 0,
        })

    return conditions

# ═══════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        condition_name TEXT NOT NULL,
        side TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        train_sharpe REAL, train_trades INTEGER, train_winrate REAL, train_avg_ret REAL, train_maxdd REAL,
        test_sharpe REAL, test_trades INTEGER, test_winrate REAL, test_avg_ret REAL, test_maxdd REAL,
        timestamp TEXT,
        UNIQUE(condition_name, side, timeframe)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sharpe ON results(test_sharpe DESC)")
    conn.commit()
    return conn

def save_result(conn, name, side, tf, train_stats, test_stats):
    conn.execute("""INSERT OR REPLACE INTO results
        (condition_name, side, timeframe, train_sharpe, train_trades, train_winrate, train_avg_ret, train_maxdd,
         test_sharpe, test_trades, test_winrate, test_avg_ret, test_maxdd, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, side, tf, *train_stats, *test_stats, datetime.now().isoformat()))
    conn.commit()

def get_completed(conn):
    try:
        rows = conn.execute("SELECT condition_name, side, timeframe FROM results").fetchall()
        return {(r[0], r[1], r[2]) for r in rows}
    except Exception:
        return set()

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════
def main():
    print("=" * 80)
    print("PER-INDICATOR SWEEP — TRADIER (Stocks)")
    print(f"Klines dir: {KLINES_DIR}")
    print(f"DB: {DB_PATH}")
    print(f"Cost: {COST*100:.2f}% RT | Min trades: {MIN_TRADES} | Max hold: {MAX_HOLD}")
    print(f"Train/Test split: {TRAIN_RATIO:.0%}/{1-TRAIN_RATIO:.0%}")
    print("=" * 80)

    symbols = get_symbols()
    print(f"\nFound {len(symbols)} symbols")

    # Generate all conditions
    conditions = generate_conditions()
    print(f"Generated {len(conditions)} indicator conditions to test")

    # Init DB
    conn = init_db()
    completed = get_completed(conn)
    remaining = [c for c in conditions if (c['name'], c['side'], c['tf']) not in completed]
    print(f"Already completed: {len(completed)} | Remaining: {len(remaining)}")

    if not remaining:
        print("\nAll conditions already tested! Printing top results...")
        print_top_results(conn)
        return

    # Pre-load all kline data per timeframe
    print("\nLoading kline data...")
    tf_data = {}  # tf -> {sym: {'ind': ..., 'n': ...}}
    for tf in TIMEFRAMES:
        tf_data[tf] = {}
        loaded = 0
        for sym in symbols:
            d = load_klines(sym, tf)
            if d is not None:
                ind = compute_all_indicators(d)
                tf_data[tf][sym] = {'ind': ind, 'n': d['n']}
                loaded += 1
        print(f"  {tf}: loaded {loaded}/{len(symbols)} symbols")

    # Run sweep
    total = len(remaining)
    t0 = time.time()
    tested = 0
    skipped = 0

    for idx, cond in enumerate(remaining):
        name = cond['name']
        side = cond['side']
        tf = cond['tf']
        is_long = side == 'LONG'

        data_for_tf = tf_data.get(tf, {})
        if not data_for_tf:
            skipped += 1
            continue

        try:
            train_trades, test_trades = backtest_condition(data_for_tf, cond['entry'], is_long, cond.get('exit'))
            train_stats = calc_daily_sharpe(train_trades)
            test_stats = calc_daily_sharpe(test_trades)
            save_result(conn, name, side, tf, train_stats, test_stats)
            tested += 1

            # Progress
            elapsed = time.time() - t0
            rate = tested / elapsed if elapsed > 0 else 0
            eta = (total - idx - 1) / rate if rate > 0 else 0

            if tested % 20 == 0 or tested <= 5:
                print(f"  [{tested}/{total}] {name} ({side} {tf}): train_sharpe={train_stats[0]:.2f} test_sharpe={test_stats[0]:.2f} trades={test_stats[1]} | {rate:.1f}/s ETA {eta/60:.0f}m")

        except Exception as e:
            print(f"  ERROR {name}: {e}")
            skipped += 1

    elapsed = time.time() - t0
    print(f"\n{'='*80}")
    print(f"COMPLETE: {tested} tested, {skipped} skipped in {elapsed:.0f}s")
    print(f"{'='*80}")

    print_top_results(conn)
    conn.close()

def print_top_results(conn):
    print("\n" + "=" * 80)
    print("TOP 30 CONDITIONS BY TEST SHARPE (min 50 trades)")
    print("=" * 80)
    rows = conn.execute("""
        SELECT condition_name, side, timeframe,
               train_sharpe, train_trades, train_winrate, train_avg_ret,
               test_sharpe, test_trades, test_winrate, test_avg_ret, test_maxdd
        FROM results
        WHERE test_trades >= ? AND train_trades >= ?
        ORDER BY test_sharpe DESC
        LIMIT 30
    """, (MIN_TRADES, MIN_TRADES)).fetchall()

    print(f"{'Condition':<45} {'Side':<6} {'TF':<4} {'TrSh':>6} {'TrN':>5} {'TsSh':>6} {'TsN':>5} {'WR%':>5} {'AvgR':>6} {'MaxDD':>6}")
    print("-" * 120)
    for r in rows:
        print(f"{r[0]:<45} {r[1]:<6} {r[2]:<4} {r[3]:>6.2f} {r[4]:>5} {r[7]:>6.2f} {r[8]:>5} {r[9]*100:>5.1f} {r[10]:>6.3f} {r[11]:>6.1f}")

    print("\n\nTOP 30 CONDITIONS BY TEST SHARPE (SHORT side)")
    print("-" * 120)
    rows = conn.execute("""
        SELECT condition_name, side, timeframe,
               train_sharpe, train_trades, train_winrate, train_avg_ret,
               test_sharpe, test_trades, test_winrate, test_avg_ret, test_maxdd
        FROM results
        WHERE test_trades >= ? AND train_trades >= ? AND side = 'SHORT'
        ORDER BY test_sharpe DESC
        LIMIT 30
    """, (MIN_TRADES, MIN_TRADES)).fetchall()

    print(f"{'Condition':<45} {'Side':<6} {'TF':<4} {'TrSh':>6} {'TrN':>5} {'TsSh':>6} {'TsN':>5} {'WR%':>5} {'AvgR':>6} {'MaxDD':>6}")
    print("-" * 120)
    for r in rows:
        print(f"{r[0]:<45} {r[1]:<6} {r[2]:<4} {r[3]:>6.2f} {r[4]:>5} {r[7]:>6.2f} {r[8]:>5} {r[9]*100:>5.1f} {r[10]:>6.3f} {r[11]:>6.1f}")

    # Summary stats
    total = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    positive = conn.execute("SELECT COUNT(*) FROM results WHERE test_sharpe > 0 AND test_trades >= ?", (MIN_TRADES,)).fetchone()[0]
    strong = conn.execute("SELECT COUNT(*) FROM results WHERE test_sharpe > 1.0 AND test_trades >= ?", (MIN_TRADES,)).fetchone()[0]
    print(f"\n\nSUMMARY: {total} total conditions | {positive} positive Sharpe | {strong} Sharpe > 1.0")

    # Per-indicator category summary
    print("\n\nPER-CATEGORY SUMMARY (best test Sharpe per category):")
    categories = [
        ("Stochastic K thresh", "stoch_k_%"),
        ("Stochastic K>D / cross", "stoch_k_gt_d%", "stoch_k_lt_d%", "stoch_cross%"),
        ("RSI", "rsi_%"),
        ("MFI", "mfi_%"),
        ("WaveTrend score", "wt_score%"),
        ("WaveTrend cross", "wt_cross%"),
        ("Heikin Ashi", "ha_%"),
        ("DC position", "dc_pos_%"),
        ("DC width", "dc_width_%"),
        ("DC floor/ceil", "dc_floor%", "dc_ceil%"),
        ("DC low4/high4", "dc_low4%", "dc_high4%"),
        ("DC crossovers", "dc_basis_cross%", "dc_high_cross%", "dc_low_cross%"),
        ("Relative Volume", "rvol_%"),
        ("ATR ratio", "atr_ratio_%"),
        ("LinReg slope", "lr_slope_%"),
        ("BB %B", "bb_pct_b_%"),
        ("BB width", "bb_width_%"),
        ("LR channel %B", "lr_pct_b_%"),
        ("SMA200 distance", "sma200_%"),
        ("EMA20 distance", "ema20_dist_%"),
        ("Hull/THMA", "t_up_%", "tco_%", "tcu_%"),
        ("EMA cross", "ema20_cross%", "ema50_cross%"),
        ("Linearity R2", "linearity_%"),
    ]
    print(f"{'Category':<30} {'Best Test Sharpe':>16} {'Best Condition':<45}")
    print("-" * 95)
    for cat in categories:
        cat_name = cat[0]
        patterns = cat[1:]
        where_clauses = " OR ".join([f"condition_name LIKE '{p}'" for p in patterns])
        row = conn.execute(f"""
            SELECT condition_name, test_sharpe FROM results
            WHERE ({where_clauses}) AND test_trades >= {MIN_TRADES}
            ORDER BY test_sharpe DESC LIMIT 1
        """).fetchone()
        if row:
            print(f"{cat_name:<30} {row[1]:>16.3f} {row[0]:<45}")
        else:
            print(f"{cat_name:<30} {'N/A':>16} {'—':<45}")

if __name__ == "__main__":
    main()
