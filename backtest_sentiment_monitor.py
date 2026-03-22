#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
SENTIMENT MONITOR BACKTEST — Test sentiment-reactive hold/exit rules.

Simulates a portfolio where sentiment data drives exit decisions:
  - Bullish sentiment → hold longs longer
  - Bearish sentiment → exit longs early (even <1% gain)
  - Sentiment reversal → emergency exit
  - Sentiment velocity → defensive exit

Uses 15m klines + simulated sentiment (derived from BTC price action as proxy).
"""
import os, sys, json, math, time, signal, logging, warnings
import numpy as np
warnings.filterwarnings("ignore", category=RuntimeWarning)
from pathlib import Path
from multiprocessing import Pool, cpu_count
from collections import defaultdict
from dataclasses import dataclass, asdict
from numpy.lib.stride_tricks import sliding_window_view

logging.basicConfig(level=logging.INFO, format='%(asctime)s [SENT_BT] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

import platform
if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance")
KLINES_DIR = BASE_PATH / "klines_cache"
if not KLINES_DIR.exists():
    KLINES_DIR = Path("/home/niels/binance-sandbox/klines_cache")
RESULTS_DIR = BASE_PATH / "data" / "backtest_sentiment_monitor"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE = RESULTS_DIR / "sentiment_monitor_results.json"
N_WORKERS = max(1, cpu_count() - 1)
WARMUP = 300
ENTRY_TF = "15m"
shutdown_flag = False

def _handle_signal(sig, frame):
    global shutdown_flag
    shutdown_flag = True
signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

def _sma(arr, p):
    if len(arr) < p: return np.full(len(arr), np.nan, dtype=np.float64)
    clean = np.where(np.isnan(arr), 50.0, arr).astype(np.float64)
    cum = np.cumsum(clean); result = np.full(len(arr), np.nan, dtype=np.float64)
    result[p-1] = cum[p-1]/p
    if len(arr)>p: result[p:] = (cum[p:]-cum[:-p])/p
    return result

def _ema(arr, p):
    result = np.full(len(arr), np.nan);
    if len(arr)<p: return result
    m=2.0/(p+1); result[p-1]=np.mean(arr[:p])
    for i in range(p,len(arr)): result[i]=arr[i]*m+result[i-1]*(1-m)
    return result

def stoch(h,lo,c,kp=14,sk=5,sd=5):
    n=len(c)
    if n<kp: return np.full(n,50.0),np.full(n,50.0)
    hh=np.max(sliding_window_view(h,kp),axis=1); ll=np.min(sliding_window_view(lo,kp),axis=1)
    raw=np.full(n,50.0); d=hh-ll; v=d>0
    raw[kp-1:]=np.where(v,(c[kp-1:]-ll)/d*100,50.0)
    k=_sma(raw,sk); dd=_sma(k,sd); return k,dd

def rsi_calc(c,p=14):
    delta=np.diff(c,prepend=c[0]); g=np.where(delta>0,delta,0.0); l=np.where(delta<0,-delta,0.0)
    ag=_ema(g,p); al=_ema(l,p); rs=np.where(al>0,ag/al,100.0); return 100-100/(1+rs)

def ha_calc(o,h,lo,c):
    hc=(o+h+lo+c)/4; ho=np.empty_like(o); ho[0]=(o[0]+c[0])/2
    for i in range(1,len(o)): ho[i]=(ho[i-1]+hc[i-1])/2
    return np.where(hc>=ho,1,-1)

def load_klines(sym, tf):
    p = KLINES_DIR / f"{sym}_{tf}.json"
    if not p.exists(): return None
    try:
        bars = json.loads(p.read_text())
        if not isinstance(bars, list) or len(bars) < WARMUP+100: return None
        if isinstance(bars[0], dict):
            o=np.array([float(b["open"]) for b in bars],dtype=np.float64)
            h=np.array([float(b["high"]) for b in bars],dtype=np.float64)
            lo=np.array([float(b["low"]) for b in bars],dtype=np.float64)
            c=np.array([float(b["close"]) for b in bars],dtype=np.float64)
        else:
            arr=np.array(bars,dtype=np.float64); o,h,lo,c=arr[:,0],arr[:,1],arr[:,2],arr[:,3]
        data=np.column_stack([o,h,lo,c])
        if len(data)>35000: data=data[-35000:]
        return data
    except: return None


def compute_market_sentiment(btc_data):
    """Derive synthetic sentiment from BTC price action (proxy for real sentiment)."""
    o,h,lo,c = btc_data[:,0],btc_data[:,1],btc_data[:,2],btc_data[:,3]
    n = len(c)
    # Sentiment = combo of momentum + trend + volatility
    # Short-term momentum (20-bar return scaled to -100..+100)
    ret20 = np.zeros(n)
    ret20[20:] = (c[20:] / c[:-20] - 1) * 100 * 10  # Scale to sentiment range
    ret20 = np.clip(ret20, -100, 100)
    # RSI contribution
    r = rsi_calc(c, 14)
    rsi_sent = (r - 50) * 2  # Maps RSI 0-100 to sentiment -100..+100
    # Blend
    sentiment = ret20 * 0.6 + rsi_sent * 0.4
    # Smooth with EMA
    sentiment_ema = _ema(sentiment, 20)
    sentiment_ema = np.where(np.isnan(sentiment_ema), 0, sentiment_ema)
    return sentiment_ema


@dataclass
class SentimentMonitorConfig:
    name: str = "BASELINE"
    # Monitor rules
    sentiment_monitor_enabled: bool = True
    bullish_threshold: float = 25.0
    bearish_threshold: float = -25.0
    bearish_long_exit_gain: float = 0.5    # Exit longs at 0.5% in bearish
    strong_bearish_exit_gain: float = 0.0  # Exit longs at ANY gain in strong bearish
    reversal_exit: bool = True             # Exit on sentiment reversal
    reversal_magnitude: float = 30.0       # Min swing to count as reversal
    velocity_exit: bool = True             # Exit on fast sentiment drop
    velocity_threshold: float = -5.0       # Sentiment dropping 5+/reading
    hold_extension: bool = True            # Extend hold in favorable sentiment
    hold_extension_mult: float = 2.0       # 2x hold time
    # Entry (from ablation winners)
    rsi_max_entry: float = 37.0
    exit_gain_base: float = 1.0            # Base exit gain threshold
    max_hold_bars: int = 48


def run_backtest(symbols_data, btc_sentiment, cfg):
    """Portfolio-level backtest with sentiment monitor."""
    all_syms = sorted(symbols_data.keys())
    min_n = min(d["n"] for d in symbols_data.values())
    min_n = min(min_n, len(btc_sentiment))
    if min_n < WARMUP + 100: return None
    positions = {}
    trades = []
    fee_mult = 0.08 / 100.0
    sentiment_history = []
    for bar in range(WARMUP, min_n):
        if shutdown_flag: break
        sent = btc_sentiment[bar]
        sentiment_history.append(sent)
        if len(sentiment_history) > 60: sentiment_history = sentiment_history[-60:]
        # Classify
        is_bullish = sent > cfg.bullish_threshold
        is_bearish = sent < cfg.bearish_threshold
        is_strong_bearish = sent < -50
        # Velocity
        velocity = sentiment_history[-1] - sentiment_history[-2] if len(sentiment_history) >= 2 else 0
        # Reversal detection
        reversal = None
        if len(sentiment_history) >= 5:
            swing = sentiment_history[-1] - sentiment_history[-5]
            if abs(swing) >= cfg.reversal_magnitude:
                if sentiment_history[-5] > 0 and sentiment_history[-1] < 0: reversal = "BULL_TO_BEAR"
                elif sentiment_history[-5] < 0 and sentiment_history[-1] > 0: reversal = "BEAR_TO_BULL"
        # Manage positions
        to_close = []
        for sym, pos in list(positions.items()):
            if bar >= symbols_data[sym]["n"]: to_close.append(sym); continue
            price = symbols_data[sym]["c"][bar]
            is_long = pos["side"] == "LONG"
            gain = ((price - pos["entry"]) / pos["entry"] * 100) if is_long else ((pos["entry"] - price) / pos["entry"] * 100) if pos["entry"] > 0 else 0
            bars_held = bar - pos["entry_bar"]
            exit_reason = None
            if cfg.sentiment_monitor_enabled:
                # RULE 1: Reversal exit
                if cfg.reversal_exit and reversal:
                    if is_long and reversal == "BULL_TO_BEAR" and gain > 0:
                        exit_reason = f"REVERSAL_{reversal}"
                    elif not is_long and reversal == "BEAR_TO_BULL" and gain > 0:
                        exit_reason = f"REVERSAL_{reversal}"
                # RULE 2: Bearish → exit longs early
                if not exit_reason and is_long and is_bearish and gain > 0:
                    thresh = cfg.strong_bearish_exit_gain if is_strong_bearish else cfg.bearish_long_exit_gain
                    if gain >= thresh:
                        exit_reason = f"BEARISH_EARLY_EXIT"
                # RULE 3: Bullish → exit shorts early
                if not exit_reason and not is_long and is_bullish and gain > 0:
                    thresh = cfg.strong_bearish_exit_gain if sent > 50 else cfg.bearish_long_exit_gain
                    if gain >= thresh:
                        exit_reason = f"BULLISH_SHORT_EXIT"
                # RULE 4: Velocity exit
                if not exit_reason and cfg.velocity_exit:
                    if is_long and velocity < cfg.velocity_threshold and gain > 0:
                        exit_reason = f"VELOCITY_DROP"
                    elif not is_long and velocity > abs(cfg.velocity_threshold) and gain > 0:
                        exit_reason = f"VELOCITY_RISE"
                # RULE 5: Hold extension
                effective_max_hold = cfg.max_hold_bars
                if cfg.hold_extension:
                    if (is_long and is_bullish) or (not is_long and is_bearish):
                        effective_max_hold = int(cfg.max_hold_bars * cfg.hold_extension_mult)
            else:
                effective_max_hold = cfg.max_hold_bars
            # Standard exits (no sentiment)
            if not exit_reason:
                k_val = symbols_data[sym]["k"][bar]
                d_val = symbols_data[sym]["d"][bar]
                k_prev = symbols_data[sym]["k"][bar-1] if bar > 0 else k_val
                d_prev = symbols_data[sym]["d"][bar-1] if bar > 0 else d_val
                if gain > cfg.exit_gain_base:
                    if (is_long and k_val < d_val and k_prev >= d_prev) or (not is_long and k_val > d_val and k_prev <= d_prev):
                        exit_reason = "STOCH_CROSS"
                if not exit_reason and bars_held >= effective_max_hold and gain > cfg.exit_gain_base:
                    exit_reason = "HOLD_BARS"
            if exit_reason:
                pnl = gain - fee_mult * 100 * 2
                trades.append({"pnl": pnl, "reason": exit_reason, "side": pos["side"], "bars": bars_held, "sentiment_at_exit": sent})
                to_close.append(sym)
        for s in set(to_close): positions.pop(s, None)
        # Open new positions
        if len(positions) < 20:
            for sym in all_syms:
                if sym in positions or bar >= symbols_data[sym]["n"]: continue
                if len(positions) >= 20: break
                price = symbols_data[sym]["c"][bar]
                if price <= 0: continue
                k_val = symbols_data[sym]["k"][bar]; d_val = symbols_data[sym]["d"][bar]
                rsi_val = symbols_data[sym]["rsi"][bar]; ha_val = symbols_data[sym]["ha"][bar]
                if np.isnan(k_val) or np.isnan(rsi_val): continue
                k_prev = symbols_data[sym]["k"][bar-1] if bar > 0 else k_val
                d_prev = symbols_data[sym]["d"][bar-1] if bar > 0 else d_val
                co = k_val > d_val and k_prev <= d_prev
                cu = k_val < d_val and k_prev >= d_prev
                if co and ha_val == 1 and rsi_val < cfg.rsi_max_entry and k_val < 65:
                    positions[sym] = {"side": "LONG", "entry": price, "entry_bar": bar}
                elif cu and ha_val == -1 and rsi_val > (100-cfg.rsi_max_entry) and k_val > 35:
                    positions[sym] = {"side": "SHORT", "entry": price, "entry_bar": bar}
    if len(trades) < 10: return None
    rets = np.array([t["pnl"] for t in trades])
    mean_r = rets.mean(); std_r = rets.std()
    if std_r <= 0: return None
    sharpe = mean_r / std_r * math.sqrt(35040)
    wins = np.sum(rets > 0); wr = wins/len(rets)*100
    gp = rets[rets>0].sum(); gl = abs(rets[rets<0].sum())
    reason_counts = defaultdict(int); reason_pnl = defaultdict(float)
    for t in trades: reason_counts[t["reason"]] += 1; reason_pnl[t["reason"]] += t["pnl"]
    return {"name": cfg.name, "n_trades": len(rets), "sharpe": round(sharpe, 3), "win_rate": round(wr, 1), "profit_factor": round(gp/gl, 3) if gl > 0 else 999, "total_pnl": round(rets.sum(), 2), "mean_pnl": round(mean_r, 4), "reason_counts": dict(reason_counts), "reason_pnl": {k: round(v, 1) for k, v in sorted(reason_pnl.items(), key=lambda x: x[1])}}


def generate_configs():
    configs = []
    # Baseline: no sentiment monitor
    configs.append(SentimentMonitorConfig(name="NO_SENTIMENT", sentiment_monitor_enabled=False))
    # Default sentiment monitor
    configs.append(SentimentMonitorConfig(name="SENTIMENT_DEFAULT"))
    # Threshold sweeps
    for bull in [15, 20, 25, 30, 40, 50]:
        configs.append(SentimentMonitorConfig(name=f"BULL_{bull}_BEAR_{-bull}", bullish_threshold=float(bull), bearish_threshold=float(-bull)))
    # Early exit gain sweep
    for gain in [0.0, 0.2, 0.5, 0.8, 1.0]:
        configs.append(SentimentMonitorConfig(name=f"BEARISH_EXIT_{gain}pct", bearish_long_exit_gain=gain))
    # Strong bearish exit
    for gain in [-0.5, 0.0, 0.3, 0.5]:
        configs.append(SentimentMonitorConfig(name=f"STRONG_BEAR_EXIT_{gain}pct", strong_bearish_exit_gain=gain))
    # Reversal magnitude
    for mag in [15, 20, 25, 30, 40, 50]:
        configs.append(SentimentMonitorConfig(name=f"REVERSAL_MAG_{mag}", reversal_magnitude=float(mag)))
    # Velocity threshold
    for vel in [-2, -3, -5, -7, -10]:
        configs.append(SentimentMonitorConfig(name=f"VELOCITY_{abs(vel)}", velocity_threshold=float(vel)))
    # Hold extension multiplier
    for mult in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]:
        configs.append(SentimentMonitorConfig(name=f"HOLD_EXT_{mult}X", hold_extension_mult=mult))
    # Component ablation
    configs.append(SentimentMonitorConfig(name="NO_REVERSAL", reversal_exit=False))
    configs.append(SentimentMonitorConfig(name="NO_VELOCITY", velocity_exit=False))
    configs.append(SentimentMonitorConfig(name="NO_HOLD_EXT", hold_extension=False))
    configs.append(SentimentMonitorConfig(name="ONLY_REVERSAL", reversal_exit=True, velocity_exit=False, hold_extension=False, bearish_long_exit_gain=999.0))
    configs.append(SentimentMonitorConfig(name="ONLY_EARLY_EXIT", reversal_exit=False, velocity_exit=False, hold_extension=False))
    return configs


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--symbols", type=int, default=0)
    args = parser.parse_args()
    if args.report:
        if not RESULTS_FILE.exists(): print("No results"); return
        results = json.loads(RESULTS_FILE.read_text())
        sorted_r = sorted(results.items(), key=lambda x: x[1].get("sharpe", -999), reverse=True)
        print(f"\n{'='*100}")
        print("SENTIMENT MONITOR BACKTEST — Impact on Sharpe")
        print(f"{'='*100}")
        baseline = results.get("NO_SENTIMENT", {})
        bl_sharpe = baseline.get("sharpe", 0)
        print(f"\n{'#':>3} {'Config':<35} {'Sharpe':>8} {'Delta':>8} {'WR%':>7} {'PF':>7} {'Trades':>7} {'TotalPnL':>10}")
        print("-"*90)
        for i, (n, m) in enumerate(sorted_r[:30]):
            delta = m.get("sharpe", 0) - bl_sharpe
            d_str = f"{delta:+.3f}" if n != "NO_SENTIMENT" else "---"
            print(f"{i+1:>3} {n:<35} {m.get('sharpe', 0):>8.3f} {d_str:>8} {m.get('win_rate', 0):>6.1f}% {m.get('profit_factor', 0):>7.3f} {m.get('n_trades', 0):>7} {m.get('total_pnl', 0):>10.1f}")
        # Exit reason breakdown for best config
        best_name, best = sorted_r[0]
        if "reason_pnl" in best:
            print(f"\nExit Reason PnL ({best_name}):")
            for reason, pnl in sorted(best["reason_pnl"].items(), key=lambda x: x[1]):
                cnt = best.get("reason_counts", {}).get(reason, 0)
                print(f"  {reason:<30} PnL={pnl:>8.1f} Count={cnt:>5}")
        return
    # Load data
    logger.info("Loading klines...")
    symbols_data = {}
    btc_data = load_klines("BTCUSDT", ENTRY_TF) if ENTRY_TF != "D" else load_klines("BTCUSDT", "15m")
    if btc_data is None:
        # Try any large-cap as sentiment proxy
        for proxy in ["1000PEPEUSDC", "ETHUSDT", "1000SHIBUSDC"]:
            btc_data = load_klines(proxy, ENTRY_TF)
            if btc_data is not None:
                logger.info(f"Using {proxy} as sentiment proxy (no BTCUSDT)")
                break
    if btc_data is None:
        logger.error("No BTC/proxy data for sentiment"); return
    btc_sentiment = compute_market_sentiment(btc_data)
    logger.info(f"Sentiment proxy: {len(btc_sentiment)} bars, range [{btc_sentiment[WARMUP:].min():.0f}, {btc_sentiment[WARMUP:].max():.0f}]")
    files = sorted([f.name for f in KLINES_DIR.iterdir() if f.name.endswith(f"_{ENTRY_TF}.json")])
    for fname in files:
        sym = fname.replace(f"_{ENTRY_TF}.json", "")
        if not (sym.endswith("USDT") or sym.endswith("USDC")): continue
        data = load_klines(sym, ENTRY_TF)
        if data is None: continue
        o,h,lo,c = data[:,0],data[:,1],data[:,2],data[:,3]
        k,d = stoch(h,lo,c,14,5,5)
        r = rsi_calc(c,14)
        ha_arr = ha_calc(o,h,lo,c)
        symbols_data[sym] = {"c": c, "k": k, "d": d, "rsi": r, "ha": ha_arr, "n": len(c)}
        if args.symbols > 0 and len(symbols_data) >= args.symbols: break
    logger.info(f"Loaded {len(symbols_data)} symbols")
    configs = generate_configs()
    logger.info(f"Testing {len(configs)} configs")
    all_results = {}
    for ci, cfg in enumerate(configs):
        if shutdown_flag: break
        t0 = time.time()
        result = run_backtest(symbols_data, btc_sentiment, cfg)
        if not result: continue
        all_results[cfg.name] = result
        elapsed = time.time() - t0
        logger.info(f"  [{ci+1}/{len(configs)}] {cfg.name}: Sharpe={result['sharpe']} WR={result['win_rate']}% PnL={result['total_pnl']} ({elapsed:.1f}s)")
    RESULTS_FILE.write_text(json.dumps(all_results, indent=2, default=str))
    logger.info(f"Done. {len(all_results)} configs. Results: {RESULTS_FILE}")


if __name__ == "__main__":
    main()
