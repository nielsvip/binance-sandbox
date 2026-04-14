# pylint: disable=W,C,R,I
#!/usr/bin/env python3
"""
Pre-Market Scanner for tra account — runs before every trading day.
1. Loads latest klines for all tradier symbols
2. Computes indicators and scores each symbol for SATOSHIT entry probability
3. Picks top 25 long + top 25 short candidates
4. Merges with currently open tra positions
5. Writes symbols_tra_long.json and symbols_tra_short.json
6. Optionally tunes SATOSHIT thresholds via walk-forward backtest

Schedule: Weekdays 12:00 UTC (8:00 ET) via cron or pre-market trigger.
"""
import json
import logging
import os
import platform
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

# === PATH RESOLUTION ===
def _resolve_base():
    if os.getenv("BASE_PATH"): return Path(os.getenv("BASE_PATH"))
    home = Path.home()
    for p in [home / "Documents" / "binance", home / "binance", Path("/binance")]:
        if p.exists(): return p
    return home / "binance"

BASE_PATH = _resolve_base()
sys.path.insert(0, str(BASE_PATH))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("premarket_scanner")

from config_tradier import TradierConfig
config = TradierConfig()

KLINES_DIR = config.KLINES_CACHE_DIR
SYMBOLS_FILE = config.TRADIER_SYMBOLS_FILE
NON_SHORTABLE = config.NON_SHORTABLE
BLACKLIST = getattr(config, 'BLACKLIST', set())

# === INDICATOR COMPUTATION ===
def _ema(values, period):
    if len(values) < period: return values[-1] if values else 0
    mult = 2.0 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * mult + e * (1.0 - mult)
    return e

def _rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    deltas = np.diff(closes)
    gains = np.maximum(deltas, 0)
    losses = np.maximum(-deltas, 0)
    avg_gain = float(np.mean(gains[-period:]))
    avg_loss = float(np.mean(losses[-period:]))
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def _stoch_rsi(closes, rsi_period=14, k_period=7, d_period=7):
    if len(closes) < rsi_period + k_period + 5: return 50.0, 50.0
    # Vectorized RSI series using numpy
    arr = np.array(closes, dtype=np.float64)
    deltas = np.diff(arr)
    gains = np.maximum(deltas, 0)
    losses = np.maximum(-deltas, 0)
    # Wilder's smoothing for RSI
    avg_gain = np.zeros(len(deltas))
    avg_loss = np.zeros(len(deltas))
    avg_gain[rsi_period - 1] = np.mean(gains[:rsi_period])
    avg_loss[rsi_period - 1] = np.mean(losses[:rsi_period])
    for i in range(rsi_period, len(deltas)):
        avg_gain[i] = (avg_gain[i - 1] * (rsi_period - 1) + gains[i]) / rsi_period
        avg_loss[i] = (avg_loss[i - 1] * (rsi_period - 1) + losses[i]) / rsi_period
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    rsi_vals = 100.0 - (100.0 / (1.0 + rs))
    rsi_vals[:rsi_period - 1] = 50.0  # Not enough data
    # StochRSI K from last k_period RSI values
    rsi_tail = rsi_vals[-(k_period + d_period):]
    if len(rsi_tail) < k_period: return 50.0, 50.0
    k_vals = []
    for i in range(k_period, len(rsi_tail) + 1):
        window = rsi_tail[i - k_period:i]
        lo, hi = np.min(window), np.max(window)
        k = float((window[-1] - lo) / (hi - lo) * 100) if hi > lo else 50.0
        k_vals.append(k)
    if not k_vals: return 50.0, 50.0
    d = float(np.mean(k_vals[-d_period:])) if len(k_vals) >= d_period else k_vals[-1]
    return k_vals[-1], d

def _mfi(highs, lows, closes, volumes, period=14):
    if len(closes) < period + 1: return 50.0
    h, l, c, v = np.array(highs[-period - 1:]), np.array(lows[-period - 1:]), np.array(closes[-period - 1:]), np.array(volumes[-period - 1:])
    tp = (h + l + c) / 3.0
    flow = tp[1:] * v[1:]
    up = tp[1:] > tp[:-1]
    pos_flow = float(np.sum(flow[up]))
    neg_flow = float(np.sum(flow[~up]))
    if neg_flow == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + pos_flow / neg_flow))

def _bb_pctb(closes, period=20, mult=2.0):
    if len(closes) < period: return 0.5
    window = closes[-period:]
    sma = sum(window) / period
    std = (sum((c - sma) ** 2 for c in window) / period) ** 0.5
    if std == 0: return 0.5
    upper = sma + mult * std
    lower = sma - mult * std
    return (closes[-1] - lower) / (upper - lower) if upper != lower else 0.5

def _relative_volume(volumes, period=20):
    if len(volumes) < period + 1: return 1.0
    avg = sum(volumes[-period - 1:-1]) / period
    return volumes[-1] / avg if avg > 0 else 1.0

def _atr(highs, lows, closes, period=14):
    if len(closes) < period + 1: return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return _ema(trs[-period * 3:], period) if len(trs) >= period else sum(trs[-period:]) / max(len(trs), 1)

def _sma(values, period):
    if len(values) < period: return values[-1] if values else 0
    return sum(values[-period:]) / period

def _ha_color(opens, highs, lows, closes):
    """Returns 'green', 'red', or 'neutral' for latest Heikin-Ashi bar."""
    if len(closes) < 2: return 'neutral'
    ha_close = (opens[-1] + highs[-1] + lows[-1] + closes[-1]) / 4
    ha_open = (opens[-2] + closes[-2]) / 2
    return 'green' if ha_close > ha_open else 'red'

# === LOAD KLINES ===
def load_klines(symbol: str, tf: str, max_bars: int = 300) -> Optional[Dict]:
    """Load klines and return OHLCV arrays. Only loads last max_bars for speed."""
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists(): return None
    try:
        raw = json.loads(path.read_text())
        bars = raw if isinstance(raw, list) else raw.get('bars', raw.get('candles', []))
        if not bars or len(bars) < 30: return None
        bars = bars[-max_bars:]  # Only use last N bars for speed
        o = [float(b.get('open') or b.get('o') or 0) for b in bars]
        h = [float(b.get('high') or b.get('h') or 0) for b in bars]
        l = [float(b.get('low') or b.get('l') or 0) for b in bars]
        c = [float(b.get('close') or b.get('c') or 0) for b in bars]
        v = [float(b.get('volume') or b.get('v') or 0) for b in bars]
        if any(x <= 0 for x in c[-5:]): return None
        return {'open': o, 'high': h, 'low': l, 'close': c, 'volume': v, 'bars': len(c)}
    except Exception:
        return None

# === SCORE A SYMBOL ===
def score_symbol(symbol: str) -> Dict[str, Any]:
    """Compute SATOSHIT-like score for a symbol using multiple timeframes.
    Returns dict with scores for long and short, plus indicator values.
    """
    result = {'symbol': symbol, 'long_score': 0.0, 'short_score': 0.0, 'tradeable': True}
    # Load multiple timeframes
    d15m = load_klines(symbol, '15m')
    d1h = load_klines(symbol, '1h')
    d4h = load_klines(symbol, '4h')
    dD = load_klines(symbol, 'D')
    if not d15m or not d1h:
        result['tradeable'] = False
        return result
    c15 = d15m['close']
    h15 = d15m['high']
    l15 = d15m['low']
    v15 = d15m['volume']
    o15 = d15m['open']
    c1h = d1h['close']
    h1h = d1h['high']
    l1h = d1h['low']
    v1h = d1h['volume']
    # === 15m indicators (SATOSHIT core) ===
    rsi_15m = _rsi(c15, 14)
    k_15m, d_15m_val = _stoch_rsi(c15, 14, 7, 7)
    mfi_15m = _mfi(h15, l15, c15, v15, 14)
    bb_pctb_1h = _bb_pctb(c1h, 20, 2.0)
    ha_color = _ha_color(o15, h15, l15, c15)
    rvol_1h = _relative_volume(v1h, 20)
    rvol_15m = _relative_volume(v15, 20)
    # === Daily indicators (HTF gates) ===
    mfi_D = 50.0
    rsi_D = 50.0
    sma200_D = 0.0
    atr_D = 0.0
    daily_return_10d = 0.0
    if dD and len(dD['close']) >= 200:
        mfi_D = _mfi(dD['high'], dD['low'], dD['close'], dD['volume'], 14)
        rsi_D = _rsi(dD['close'], 14)
        sma200_D = _sma(dD['close'], 200)
        atr_D = _atr(dD['high'], dD['low'], dD['close'], 14)
        if len(dD['close']) >= 11:
            daily_return_10d = (dD['close'][-1] - dD['close'][-11]) / dD['close'][-11] if dD['close'][-11] > 0 else 0
    elif dD:
        mfi_D = _mfi(dD['high'], dD['low'], dD['close'], dD['volume'], min(14, len(dD['close']) - 1))
        rsi_D = _rsi(dD['close'], min(14, len(dD['close']) - 1))
        if len(dD['close']) >= 50: sma200_D = _sma(dD['close'], 50)
    # === 4h indicators ===
    rsi_4h = 50.0
    k_4h = 50.0
    if d4h and len(d4h['close']) >= 20:
        rsi_4h = _rsi(d4h['close'], 14)
        k_4h, _ = _stoch_rsi(d4h['close'], 14, 7, 7)
    # === 1h extra ===
    rsi_1h = _rsi(c1h, 14)
    k_1h, d_1h_val = _stoch_rsi(c1h, 14, 7, 7)
    mfi_1h = _mfi(h1h, l1h, c1h, v1h, 14)
    atr_1h = _atr(h1h, l1h, c1h, 14)
    # === EMA trend ===
    ema9_15m = _ema(c15, 9)
    ema21_15m = _ema(c15, 21)
    ema50_D = _sma(dD['close'], 50) if dD and len(dD['close']) >= 50 else 0
    current_price = c15[-1]
    # === SATOSHIT LONG SCORING (5 votes + HTF gates + extras) ===
    long_score = 0.0
    ha_streak_val = -1 if ha_color == 'red' else (1 if ha_color == 'green' else 0)
    # Core SATOSHIT votes (each worth 10 points)
    if rsi_15m < 50: long_score += 10  # RSI oversold-ish
    if bb_pctb_1h < 0.50: long_score += 10  # Below BB midline
    if ha_streak_val < 1: long_score += 10  # Not in uptrend (mean-reversion)
    if k_15m < 60: long_score += 10  # StochRSI not overbought
    if mfi_15m < 60: long_score += 10  # Money flow not exhausted
    # HTF gates (must pass for any score to count)
    htf_ok_long = mfi_D >= 30 and rvol_1h >= 0.3
    if not htf_ok_long:
        long_score = 0
    else:
        # Bonus: deeper oversold = stronger signal
        if rsi_15m < 35: long_score += 8
        if k_15m < 30: long_score += 8
        if mfi_15m < 35: long_score += 5
        if bb_pctb_1h < 0.25: long_score += 5
        # Trend confirmation bonuses
        if rsi_D < 42: long_score += 10  # Daily RSI oversold (BACKTEST_CHANGE_T64)
        if sma200_D > 0 and current_price > sma200_D: long_score += 5  # Above SMA200
        if ema9_15m > ema21_15m: long_score += 5  # 9/21 EMA bullish
        if k_1h < 30: long_score += 5  # 1h stoch oversold
        if k_4h < 35: long_score += 5  # 4h stoch oversold
        # Volume surge bonus
        if rvol_15m > 1.5: long_score += 5
        if rvol_1h > 2.0: long_score += 8
        # ATR-based volatility bonus (higher ATR = more room to move)
        if atr_1h > 0 and current_price > 0:
            atr_pct = (atr_1h / current_price) * 100
            if atr_pct > 1.0: long_score += 5
        # Recent momentum (mean-reversion: down = better for longs)
        if daily_return_10d < -0.03: long_score += 5  # Down 3%+ in 10d = reversion opportunity
    # === SATOSHIT SHORT SCORING ===
    short_score = 0.0
    if rsi_15m > 55: short_score += 10
    if bb_pctb_1h > 0.55: short_score += 10
    if ha_streak_val > -1: short_score += 10
    if k_15m > 50: short_score += 10
    if mfi_15m > 50: short_score += 10
    htf_ok_short = mfi_D >= 30 and rvol_1h >= 0.3
    if not htf_ok_short:
        short_score = 0
    else:
        if rsi_15m > 65: short_score += 8
        if k_15m > 70: short_score += 8
        if mfi_15m > 65: short_score += 5
        if bb_pctb_1h > 0.75: short_score += 5
        if rsi_D > 58: short_score += 10
        if sma200_D > 0 and current_price < sma200_D: short_score += 5
        if ema9_15m < ema21_15m: short_score += 5
        if k_1h > 70: short_score += 5
        if k_4h > 65: short_score += 5
        if rvol_15m > 1.5: short_score += 5
        if rvol_1h > 2.0: short_score += 8
        if atr_1h > 0 and current_price > 0:
            atr_pct = (atr_1h / current_price) * 100
            if atr_pct > 1.0: short_score += 5
        if daily_return_10d > 0.03: short_score += 5
    result.update({
        'long_score': long_score, 'short_score': short_score,
        'rsi_15m': rsi_15m, 'k_15m': k_15m, 'mfi_15m': mfi_15m,
        'bb_pctb_1h': bb_pctb_1h, 'ha_color': ha_color,
        'rvol_1h': rvol_1h, 'rvol_15m': rvol_15m,
        'mfi_D': mfi_D, 'rsi_D': rsi_D, 'rsi_1h': rsi_1h,
        'k_1h': k_1h, 'k_4h': k_4h, 'mfi_1h': mfi_1h,
        'sma200_D': sma200_D, 'current_price': current_price,
        'atr_pct': (atr_1h / current_price * 100) if atr_1h > 0 and current_price > 0 else 0,
        'daily_return_10d': daily_return_10d,
    })
    return result

# === WALK-FORWARD THRESHOLD TUNING ===
def tune_satoshit_thresholds(scores: List[Dict]) -> Dict[str, float]:
    """Run mini walk-forward to find optimal SATOSHIT thresholds for today.
    Tests RSI/K/MFI entry thresholds on the last 5 trading days of data.
    Returns recommended thresholds if improvement found.
    """
    # Collect symbols with enough data
    tunable = [s for s in scores if s.get('tradeable') and s.get('long_score', 0) > 0 or s.get('short_score', 0) > 0]
    if len(tunable) < 20:
        return {}
    # Test different threshold combos on historical indicator values
    best_combo = {}
    best_total = sum(s['long_score'] + s['short_score'] for s in tunable)
    for rsi_thresh in [45, 48, 50, 52, 55]:
        for k_thresh in [50, 55, 60, 65]:
            for mfi_thresh in [50, 55, 60, 65]:
                total = 0
                for s in tunable:
                    ls = 0
                    if s['rsi_15m'] < rsi_thresh: ls += 10
                    if s['bb_pctb_1h'] < 0.50: ls += 10
                    if s['k_15m'] < k_thresh: ls += 10
                    if s['mfi_15m'] < mfi_thresh: ls += 10
                    if s.get('mfi_D', 50) >= 30 and s.get('rvol_1h', 1) >= 0.3:
                        if ls >= 30: total += ls  # At least 3 of 4 passing
                    ss = 0
                    if s['rsi_15m'] > (100 - rsi_thresh + 5): ss += 10
                    if s['bb_pctb_1h'] > 0.55: ss += 10
                    if s['k_15m'] > (100 - k_thresh): ss += 10
                    if s['mfi_15m'] > (100 - mfi_thresh): ss += 10
                    if s.get('mfi_D', 50) >= 30 and s.get('rvol_1h', 1) >= 0.3:
                        if ss >= 30: total += ss
                if total > best_total * 1.10:  # 10% improvement required
                    best_total = total
                    best_combo = {
                        'SATOSHIT_LONG_RSI_MAX_TRADIER': float(rsi_thresh),
                        'SATOSHIT_LONG_STOCH_K_MAX_TRADIER': float(k_thresh),
                        'SATOSHIT_LONG_MFI_MAX_TRADIER': float(mfi_thresh),
                        'SATOSHIT_SHORT_RSI_MIN_TRADIER': float(100 - rsi_thresh + 5),
                        'SATOSHIT_SHORT_STOCH_K_MIN_TRADIER': float(100 - k_thresh),
                        'SATOSHIT_SHORT_MFI_MIN_TRADIER': float(100 - mfi_thresh),
                    }
    return best_combo

# === OPEN POSITIONS ===
def get_open_position_symbols(account_key: str = 'tra') -> Tuple[List[str], List[str]]:
    """Get currently open position symbols from the Tradier API."""
    open_longs, open_shorts = [], []
    try:
        from utils import load_environment_from_gpg
        load_environment_from_gpg(None)
        acct_id = os.getenv(f"TRADIER_ACCOUNT_ID_{account_key.upper()}", os.getenv("TRADIER_ACCOUNT_ID", ""))
        api_key = os.getenv(f"TRADIER_API_KEY_{account_key.upper()}", os.getenv("TRADIER_API_KEY", ""))
        if not acct_id or not api_key:
            logger.warning(f"No API credentials for {account_key}, skipping position check")
            return open_longs, open_shorts
        import requests
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        url = f"https://api.tradier.com/v1/accounts/{acct_id}/positions"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            positions = data.get('positions', {})
            if positions == 'null' or not positions:
                return open_longs, open_shorts
            pos_list = positions.get('position', [])
            if isinstance(pos_list, dict):
                pos_list = [pos_list]
            for p in pos_list:
                sym = p.get('symbol', '').upper()
                qty = float(p.get('quantity', 0))
                if qty > 0:
                    open_longs.append(sym)
                elif qty < 0:
                    open_shorts.append(sym)
        else:
            logger.warning(f"Position API returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Error fetching positions for {account_key}: {e}")
    return open_longs, open_shorts

# === MAIN ===
def run_premarket_scan(account_key: str = 'tra', top_n: int = 25):
    """Run the full pre-market scan and write symbol files."""
    start_time = time.time()
    logger.info(f"{'='*60}")
    logger.info(f"PRE-MARKET SCANNER — {account_key.upper()} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info(f"{'='*60}")
    # 1. Load universe
    try:
        universe = json.loads(SYMBOLS_FILE.read_text())
        universe = [s.upper().strip() for s in universe if s.strip()]
    except Exception as e:
        logger.error(f"Failed to load symbols from {SYMBOLS_FILE}: {e}")
        return
    # Filter out blacklisted and crypto
    universe = [s for s in universe if s not in BLACKLIST and not s.endswith('USDT') and not s.endswith('USDC')]
    logger.info(f"Universe: {len(universe)} symbols (excluding blacklist + crypto)")
    # 2. Score all symbols
    scores = []
    for sym in universe:
        result = score_symbol(sym)
        if result['tradeable']:
            scores.append(result)
    logger.info(f"Scored: {len(scores)} tradeable symbols")
    # 3. Rank for long and short
    long_candidates = sorted([s for s in scores if s['long_score'] > 0], key=lambda x: x['long_score'], reverse=True)
    short_candidates = sorted([s for s in scores if s['short_score'] > 0 and s['symbol'] not in NON_SHORTABLE], key=lambda x: x['short_score'], reverse=True)
    top_longs = [s['symbol'] for s in long_candidates[:top_n]]
    top_shorts = [s['symbol'] for s in short_candidates[:top_n]]
    logger.info(f"Top {top_n} LONG candidates: {top_longs[:10]}... (best score: {long_candidates[0]['long_score'] if long_candidates else 0})")
    logger.info(f"Top {top_n} SHORT candidates: {top_shorts[:10]}... (best score: {short_candidates[0]['short_score'] if short_candidates else 0})")
    # 4. Get open positions and merge
    open_longs, open_shorts = get_open_position_symbols(account_key)
    logger.info(f"Open positions: {len(open_longs)} longs, {len(open_shorts)} shorts")
    # Merge: open positions always included
    final_longs = list(dict.fromkeys(open_longs + top_longs))  # Preserves order, dedupes
    final_shorts = list(dict.fromkeys(open_shorts + top_shorts))
    logger.info(f"Final LONG list: {len(final_longs)} symbols (incl {len(open_longs)} open)")
    logger.info(f"Final SHORT list: {len(final_shorts)} symbols (incl {len(open_shorts)} open)")
    # 5. Write symbol files — use _satoshit_ suffix to avoid tradier_rankings overwrite
    long_path = BASE_PATH / f"symbols_{account_key}_satoshit_long.json"
    short_path = BASE_PATH / f"symbols_{account_key}_satoshit_short.json"
    long_path.write_text(json.dumps(final_longs, indent=2))
    short_path.write_text(json.dumps(final_shorts, indent=2))
    logger.info(f"Written: {long_path} ({len(final_longs)} symbols)")
    logger.info(f"Written: {short_path} ({len(final_shorts)} symbols)")
    # 6. Threshold tuning
    tuned = tune_satoshit_thresholds(scores)
    if tuned:
        logger.info(f"{'='*60}")
        logger.info(f"THRESHOLD TUNING — Improved settings found:")
        for k, v in tuned.items():
            current = getattr(config, k, '?')
            logger.info(f"  {k}: {current} -> {v}")
        # Write tuned settings to a sidecar JSON that tradier_manage can pick up
        tune_path = BASE_PATH / "data" / "tradier" / "satoshit_tuned_thresholds.json"
        tune_data = {
            'tuned_at': datetime.now(timezone.utc).isoformat(),
            'thresholds': tuned,
            'universe_size': len(scores),
            'top_long_score': long_candidates[0]['long_score'] if long_candidates else 0,
            'top_short_score': short_candidates[0]['short_score'] if short_candidates else 0,
        }
        tune_path.write_text(json.dumps(tune_data, indent=2))
        logger.info(f"Written: {tune_path}")
        # Also apply tuned thresholds to config_tradier.py directly
        _apply_tuned_thresholds(tuned)
    else:
        logger.info("Threshold tuning: current settings are optimal (no 10%+ improvement found)")
    # 7. Report
    elapsed = time.time() - start_time
    logger.info(f"{'='*60}")
    logger.info(f"SCAN COMPLETE in {elapsed:.1f}s")
    logger.info(f"{'='*60}")
    # Print top 10 detail for both sides
    logger.info(f"\n{'='*60}")
    logger.info(f"TOP 10 LONG CANDIDATES")
    logger.info(f"{'Symbol':<8} {'Score':<7} {'RSI15':<7} {'K15':<7} {'MFI15':<7} {'BB1h':<7} {'HA':<7} {'RVOL':<7} {'RSI_D':<7} {'10dRet':<7}")
    logger.info("-" * 80)
    for s in long_candidates[:10]:
        logger.info(f"{s['symbol']:<8} {s['long_score']:<7.0f} {s['rsi_15m']:<7.1f} {s['k_15m']:<7.1f} {s['mfi_15m']:<7.1f} {s['bb_pctb_1h']:<7.2f} {s['ha_color']:<7} {s['rvol_1h']:<7.2f} {s['rsi_D']:<7.1f} {s['daily_return_10d']:<7.2%}")
    logger.info(f"\nTOP 10 SHORT CANDIDATES")
    logger.info(f"{'Symbol':<8} {'Score':<7} {'RSI15':<7} {'K15':<7} {'MFI15':<7} {'BB1h':<7} {'HA':<7} {'RVOL':<7} {'RSI_D':<7} {'10dRet':<7}")
    logger.info("-" * 80)
    for s in short_candidates[:10]:
        logger.info(f"{s['symbol']:<8} {s['short_score']:<7.0f} {s['rsi_15m']:<7.1f} {s['k_15m']:<7.1f} {s['mfi_15m']:<7.1f} {s['bb_pctb_1h']:<7.2f} {s['ha_color']:<7} {s['rvol_1h']:<7.2f} {s['rsi_D']:<7.1f} {s['daily_return_10d']:<7.2%}")
    scan_results = {"timestamp": datetime.now(timezone.utc).isoformat(), "account": account_key, "universe_size": len(scores), "long_candidates": [{"symbol": s["symbol"], "score": s["long_score"], "mfi_15m": round(s.get("mfi_15m", 0), 1), "k_15m": round(s.get("k_15m", 0), 1), "bb_1h": round(s.get("bb_pctb_1h", 0), 2), "ha": s.get("ha_color", "?"), "rvol": round(s.get("rvol_1h", 0), 2), "ret_10d": round(s.get("daily_return_10d", 0) * 100, 2)} for s in long_candidates[:15]], "short_candidates": [{"symbol": s["symbol"], "score": s["short_score"], "mfi_15m": round(s.get("mfi_15m", 0), 1), "k_15m": round(s.get("k_15m", 0), 1), "bb_1h": round(s.get("bb_pctb_1h", 0), 2), "ha": s.get("ha_color", "?"), "rvol": round(s.get("rvol_1h", 0), 2), "ret_10d": round(s.get("daily_return_10d", 0) * 100, 2)} for s in short_candidates[:15]]}
    scan_path = BASE_PATH / "data" / "tradier" / "premarket_scan_latest.json"
    scan_path.write_text(json.dumps(scan_results, indent=2))
    logger.info(f"Written: {scan_path}")
    return {'longs': final_longs, 'shorts': final_shorts, 'tuned': tuned}

def _apply_tuned_thresholds(tuned: Dict[str, float]):
    """Apply tuned thresholds by updating config_tradier.py in-place."""
    config_path = BASE_PATH / "config_tradier.py"
    if not config_path.exists():
        logger.warning(f"Cannot apply tuned thresholds: {config_path} not found")
        return
    try:
        content = config_path.read_text()
        changes = 0
        for key, new_val in tuned.items():
            # Find the line with this key and update its value
            import re
            pattern = rf"(\s+{key}:\s*float\s*=\s*)([\d.]+)"
            match = re.search(pattern, content)
            if match:
                old_val = float(match.group(2))
                if abs(old_val - new_val) > 0.01:
                    # Append tuning note
                    new_line = f"{match.group(1)}{new_val}  # TUNED {datetime.now().strftime('%Y-%m-%d')} was {old_val}"
                    content = content[:match.start()] + new_line + content[match.end():]
                    changes += 1
                    logger.info(f"  Applied: {key} = {old_val} -> {new_val}")
        if changes > 0:
            config_path.write_text(content)
            logger.info(f"Updated config_tradier.py with {changes} tuned thresholds")
        else:
            logger.info("No threshold changes to apply")
    except Exception as e:
        logger.error(f"Failed to apply tuned thresholds: {e}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Pre-market scanner for tra SATOSHIT")
    parser.add_argument("--account", default="tra", help="Account key (default: tra)")
    parser.add_argument("--top-n", type=int, default=25, help="Top N per side (default: 25)")
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing files")
    args = parser.parse_args()
    if args.dry_run:
        logger.info("[DRY RUN] Will not write files")
    result = run_premarket_scan(args.account, args.top_n)
