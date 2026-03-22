#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
CRYPTO DEEP VARIATION BACKTEST — Exhaustive parameter sweep based on ablation findings.

Key finding: Removing STOP_MAJOR_LOSS gives positive Sharpe. HTF alignment essential.
Now test: stoch periods, entry zones, exit thresholds, hold bars, indicator combos.

Uses 15m bars (~1 year). Runs on server in binance-sandbox.
"""
import os, sys, json, math, time, signal, logging, warnings
import numpy as np
warnings.filterwarnings("ignore", category=RuntimeWarning)
from pathlib import Path
from multiprocessing import Pool, cpu_count
from collections import defaultdict
from dataclasses import dataclass, asdict
from numpy.lib.stride_tricks import sliding_window_view

logging.basicConfig(level=logging.INFO, format='%(asctime)s [DEEP_C] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

import platform
if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance")
KLINES_DIR = BASE_PATH / "klines_cache"
if not KLINES_DIR.exists():
    KLINES_DIR = Path("/home/niels/binance-sandbox/klines_cache")
RESULTS_DIR = BASE_PATH / "data" / "backtest_deep_crypto"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE = RESULTS_DIR / "deep_crypto_results.json"
BEST_FILE = RESULTS_DIR / "deep_crypto_best.json"
FEE_PCT = 0.08
ANNUAL_BARS = {"15m": 35040}
N_WORKERS = max(1, cpu_count() - 1)
MIN_TRADES = 10
WARMUP = 300
ENTRY_TF = "15m"
shutdown_flag = False


def _handle_signal(sig, frame):
    global shutdown_flag
    shutdown_flag = True

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def _sma_np(arr, period):
    if len(arr) < period: return np.full_like(arr, np.nan, dtype=np.float64)
    clean = np.where(np.isnan(arr), 50.0, arr).astype(np.float64)
    cum = np.cumsum(clean)
    result = np.full(len(arr), np.nan, dtype=np.float64)
    result[period - 1] = cum[period - 1] / period
    if len(arr) > period:
        result[period:] = (cum[period:] - cum[:-period]) / period
    return result

def _ema_np(arr, period):
    result = np.empty_like(arr); result[:] = np.nan
    if len(arr) < period: return result
    mult = 2.0 / (period + 1); result[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)): result[i] = arr[i] * mult + result[i - 1] * (1 - mult)
    return result

def ind_stoch(h, lo, c, k_period=14, sk=5, sd=5):
    n = len(c)
    if n >= k_period:
        hh = np.max(sliding_window_view(h, k_period), axis=1)
        ll = np.min(sliding_window_view(lo, k_period), axis=1)
        raw_k = np.full(n, 50.0); denom = hh - ll; valid = denom > 0
        raw_k[k_period - 1:] = np.where(valid, (c[k_period - 1:] - ll) / denom * 100, 50.0)
    else: raw_k = np.full(n, 50.0)
    k = _sma_np(raw_k, sk)
    d = _sma_np(k, sd)
    return k, d

def ind_rsi(c, period=14):
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0.0); loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = _ema_np(gain, period); avg_loss = _ema_np(loss, period)
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    return 100 - 100 / (1 + rs)

def ind_donchian(h, lo, period=20):
    n = len(h); dc_h = np.full(n, np.nan); dc_l = np.full(n, np.nan)
    if n >= period:
        dc_h[period - 1:] = np.max(sliding_window_view(h, period), axis=1)
        dc_l[period - 1:] = np.min(sliding_window_view(lo, period), axis=1)
    return dc_h, dc_l, (dc_h + dc_l) / 2

def ind_heikin_ashi(o, h, lo, c):
    ha_c = (o + h + lo + c) / 4; ha_o = np.empty_like(o); ha_o[0] = (o[0] + c[0]) / 2
    for i in range(1, len(o)): ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    return np.where(ha_c >= ha_o, 1, -1)

def ind_wavetrend(h, lo, c, n1=10, n2=21):
    hlc3 = (h + lo + c) / 3.0; esa = _ema_np(hlc3, n1)
    d = _ema_np(np.abs(hlc3 - esa), n1)
    ci = np.where(d > 0, (hlc3 - esa) / (0.015 * d), 0.0)
    wt1 = _ema_np(ci, n2); wt2 = _sma_np(wt1, 4)
    return wt1, wt2

def ind_hull_trend(c, short_p=9, long_p=21):
    return np.where(_ema_np(c, short_p) > _ema_np(c, long_p), 1, 0)

def load_klines(symbol, tf):
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists(): return None
    try:
        bars = json.loads(path.read_text())
        if not isinstance(bars, list) or len(bars) < WARMUP + 100: return None
        if isinstance(bars[0], dict):
            o = np.array([float(b["open"]) for b in bars], dtype=np.float64)
            h = np.array([float(b["high"]) for b in bars], dtype=np.float64)
            lo = np.array([float(b["low"]) for b in bars], dtype=np.float64)
            c = np.array([float(b["close"]) for b in bars], dtype=np.float64)
            v = np.array([float(b.get("volume", 0)) for b in bars], dtype=np.float64)
        else:
            arr = np.array(bars, dtype=np.float64)
            o, h, lo, c, v = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
        data = np.column_stack([o, h, lo, c, v])
        if len(data) > 35000: data = data[-35000:]
        return data
    except Exception: return None


@dataclass
class CryptoConfig:
    name: str = "DEFAULT"
    # Stoch params
    stoch_k: int = 14; stoch_sk: int = 5; stoch_sd: int = 5
    # Entry
    use_stoch_cross: bool = True
    use_wt_cross: bool = False        # Ablation: neutral
    use_dc_breakout: bool = True      # Ablation: KEEP
    use_ha_confirm: bool = True       # Ablation: KEEP
    use_htf_alignment: bool = True    # Ablation: KEEP (essential)
    htf_stoch_k: int = 21; htf_stoch_sk: int = 7; htf_stoch_sd: int = 7
    entry_k_max_long: float = 65.0    # Max K for long entry
    entry_k_min_short: float = 35.0   # Min K for short entry
    use_rsi_filter: bool = True
    rsi_max_long: float = 60.0
    rsi_min_short: float = 40.0
    # Exit
    use_stoch_cross_exit: bool = True  # Ablation: KEEP
    exit_gain_threshold: float = 0.3   # Min gain for stoch cross exit
    use_tp_exhaustion: bool = False    # Ablation: neutral/remove
    use_hard_drop_exit: bool = True    # Ablation: KEEP
    use_dc_basis_profit_exit: bool = True  # Ablation: KEEP
    dc_profit_exit_min_gain: float = 3.0
    use_optimal_hold_bars: bool = True  # Ablation: KEEP
    optimal_hold_bars: int = 48
    use_stop_major_loss: bool = False   # Ablation: REMOVE (#1 destroyer)
    stop_loss_pct: float = -2.0
    use_break_even_guard: bool = False  # Ablation: neutral
    use_immediate_wrong_way: bool = False  # Ablation: REMOVE
    # Augment
    augment_enabled: bool = False      # Ablation: REMOVE
    min_gain_to_augment: float = 5.0
    max_augments: int = 3


@dataclass
class Position:
    side: str; entry_price: float = 0.0; entry_bar: int = 0; cost_basis: float = 0.0
    max_gain: float = 0.0; size: float = 1.0; augments: int = 0


def run_backtest(symbol, cfg, data, oos_only=False):
    o, h, lo, c, v = data[:, 0], data[:, 1], data[:, 2], data[:, 3], data[:, 4]
    n = len(c)
    if n < WARMUP + 100: return None
    k, d = ind_stoch(h, lo, c, cfg.stoch_k, cfg.stoch_sk, cfg.stoch_sd)
    kp = np.roll(k, 1); kp[0] = k[0]; dp = np.roll(d, 1); dp[0] = d[0]
    stoch_co = ((k > d) & (kp <= dp)).astype(bool)
    stoch_cu = ((k < d) & (kp >= dp)).astype(bool)
    htf_k, htf_d = ind_stoch(h, lo, c, cfg.htf_stoch_k, cfg.htf_stoch_sk, cfg.htf_stoch_sd)
    rsi = ind_rsi(c, 14)
    dc_h, dc_l, dc_mid = ind_donchian(h, lo, 20)
    ha = ind_heikin_ashi(o, h, lo, c); hap = np.roll(ha, 1); hap[0] = ha[0]
    wt1, wt2 = ind_wavetrend(h, lo, c)
    wt1p = np.roll(wt1, 1); wt1p[0] = wt1[0]; wt2p = np.roll(wt2, 1); wt2p[0] = wt2[0]
    wt_co = ((wt1 > wt2) & (wt1p <= wt2p)).astype(bool)
    wt_cu = ((wt1 < wt2) & (wt1p >= wt2p)).astype(bool)
    c_prev = np.roll(c, 1); c_prev[0] = c[0]
    lo_prev = np.roll(lo, 1); lo_prev[0] = lo[0]
    hi_prev = np.roll(h, 1); hi_prev[0] = h[0]
    dc_mid80_h, dc_mid80_l, dc_mid80 = ind_donchian(h, lo, 80)
    split_bar = int(n * 0.7) if oos_only else WARMUP
    start_bar = max(split_bar, WARMUP)
    # Entry signals
    long_e = np.zeros(n, dtype=bool); short_e = np.zeros(n, dtype=bool)
    if cfg.use_stoch_cross: long_e |= stoch_co; short_e |= stoch_cu
    if cfg.use_wt_cross: long_e |= wt_co; short_e |= wt_cu
    if cfg.use_dc_breakout:
        long_e |= ((c > dc_mid) & (c_prev <= np.roll(dc_mid, 1)))
        short_e |= ((c < dc_mid) & (c_prev >= np.roll(dc_mid, 1)))
    if cfg.use_ha_confirm: long_e &= (ha == 1); short_e &= (ha == -1)
    if cfg.use_htf_alignment: long_e &= (htf_k > htf_d); short_e &= (htf_k < htf_d)
    long_e &= (k < cfg.entry_k_max_long) & (k > 5)
    short_e &= (k > cfg.entry_k_min_short) & (k < 95)
    if cfg.use_rsi_filter: long_e &= (rsi < cfg.rsi_max_long); short_e &= (rsi > cfg.rsi_min_short)
    long_e[:start_bar] = False; short_e[:start_bar] = False
    trades = []; position = None; fee_mult = FEE_PCT / 100.0; cooldown_until = 0
    for bar in range(start_bar, n):
        if shutdown_flag: break
        price = c[bar]
        if price <= 0: continue
        if position is not None:
            is_long = position.side == "LONG"
            gain_pct = ((price - position.cost_basis) / position.cost_basis * 100) if is_long else ((position.cost_basis - price) / position.cost_basis * 100) if position.cost_basis > 0 else 0.0
            position.max_gain = max(position.max_gain, gain_pct)
            bars_held = bar - position.entry_bar
            should_exit = False; exit_reason = ""
            if cfg.use_immediate_wrong_way and bars_held <= 4 and gain_pct < -1.5:
                should_exit = True; exit_reason = "WRONG_WAY"
            if not should_exit and cfg.use_break_even_guard and position.max_gain > 3.0 and gain_pct <= 1.0:
                should_exit = True; exit_reason = "BREAK_EVEN"
            if not should_exit and cfg.use_stoch_cross_exit and gain_pct > cfg.exit_gain_threshold:
                if (is_long and stoch_cu[bar]) or (not is_long and stoch_co[bar]):
                    should_exit = True; exit_reason = "STOCH_CROSS_EXIT"
            if not should_exit and cfg.use_optimal_hold_bars and bars_held >= cfg.optimal_hold_bars and gain_pct > cfg.exit_gain_threshold:
                should_exit = True; exit_reason = "HOLD_BARS_EXIT"
            if not should_exit and cfg.use_tp_exhaustion and gain_pct > 0.5:
                if (is_long and k[bar] > 90 and k[bar] < kp[bar]) or (not is_long and k[bar] < 10 and k[bar] > kp[bar]):
                    should_exit = True; exit_reason = "TP_EXHAUSTION"
            if not should_exit and cfg.use_hard_drop_exit and gain_pct > 0.5:
                if (is_long and lo_prev[bar] > 0 and price < lo_prev[bar]) or (not is_long and hi_prev[bar] > 0 and price > hi_prev[bar]):
                    should_exit = True; exit_reason = "HARD_DROP"
            if not should_exit and cfg.use_dc_basis_profit_exit and gain_pct > cfg.dc_profit_exit_min_gain:
                if not np.isnan(dc_mid80[bar]):
                    if (is_long and price < dc_mid80[bar]) or (not is_long and price > dc_mid80[bar]):
                        should_exit = True; exit_reason = "DC_PROFIT_EXIT"
            if not should_exit and cfg.use_stop_major_loss and gain_pct < cfg.stop_loss_pct and bars_held > 6:
                if (is_long and k[bar] < d[bar]) or (not is_long and k[bar] > d[bar]):
                    should_exit = True; exit_reason = "STOP_LOSS"
            if not should_exit and cfg.augment_enabled and position.augments < cfg.max_augments and gain_pct >= cfg.min_gain_to_augment:
                if (is_long and k[bar] > kp[bar] and ha[bar] == 1) or (not is_long and k[bar] < kp[bar] and ha[bar] == -1):
                    old_cost = position.cost_basis * position.size
                    position.size += position.size; position.cost_basis = (old_cost + price * position.size / 2) / position.size
                    position.augments += 1
            if should_exit:
                pnl = gain_pct - fee_mult * 100 * 2
                trades.append({"pnl_pct": pnl, "bars_held": bars_held, "reason": exit_reason, "side": position.side})
                position = None; cooldown_until = bar + 3; continue
        if position is None and bar > cooldown_until:
            if long_e[bar]: position = Position(side="LONG", entry_price=price, entry_bar=bar, cost_basis=price)
            elif short_e[bar]: position = Position(side="SHORT", entry_price=price, entry_bar=bar, cost_basis=price)
    if position is not None:
        is_long = position.side == "LONG"
        pnl = ((c[-1] - position.cost_basis) / position.cost_basis * 100 if is_long else (position.cost_basis - c[-1]) / position.cost_basis * 100) - fee_mult * 100 * 2
        trades.append({"pnl_pct": pnl, "bars_held": n - position.entry_bar, "reason": "END", "side": position.side})
    if len(trades) < MIN_TRADES: return None
    rets = np.array([t["pnl_pct"] for t in trades])
    mean_r = rets.mean(); std_r = rets.std()
    if std_r <= 0: return None
    sharpe = mean_r / std_r * math.sqrt(35040)
    wins = np.sum(rets > 0)
    gp = rets[rets > 0].sum() if np.any(rets > 0) else 0
    gl = abs(rets[rets < 0].sum()) if np.any(rets < 0) else 1e-9
    cum = np.cumsum(rets); peak = np.maximum.accumulate(cum)
    reason_pnl = defaultdict(float); reason_cnt = defaultdict(int)
    for t in trades: reason_pnl[t["reason"]] += t["pnl_pct"]; reason_cnt[t["reason"]] += 1
    return {"symbol": symbol, "n_trades": len(rets), "sharpe": round(sharpe, 3), "win_rate": round(wins / len(rets) * 100, 1), "profit_factor": round(gp / gl, 3), "max_drawdown": round((cum - peak).min(), 2), "total_pnl": round(rets.sum(), 2), "mean_pnl": round(mean_r, 4), "avg_bars": round(np.mean([t["bars_held"] for t in trades]), 1), "reason_pnl": {k: round(v, 1) for k, v in reason_pnl.items()}, "reason_cnt": dict(reason_cnt)}


def _pool_init():
    signal.signal(signal.SIGINT, signal.SIG_IGN); signal.signal(signal.SIGTERM, signal.SIG_IGN)

def _worker(args):
    symbol, cfg_dict, oos = args
    cfg = CryptoConfig(**cfg_dict)
    data = load_klines(symbol, ENTRY_TF)
    if data is None: return None
    try: return run_backtest(symbol, cfg, data, oos)
    except Exception: return None

def get_symbols(max_n=0):
    syms = sorted({f.name.replace(f"_{ENTRY_TF}.json", "") for f in KLINES_DIR.iterdir() if f.name.endswith(f"_{ENTRY_TF}.json") and not f.name.startswith(".")})
    syms = [s for s in syms if s.endswith("USDT") or s.endswith("USDC")]
    return syms[:max_n] if max_n > 0 else syms

def aggregate(results):
    valid = [r for r in results if r is not None]
    if not valid: return {}
    return {"n_syms": len(valid), "n_trades": sum(r["n_trades"] for r in valid), "avg_sharpe": round(np.mean([r["sharpe"] for r in valid]), 3), "med_sharpe": round(np.median([r["sharpe"] for r in valid]), 3), "avg_wr": round(np.mean([r["win_rate"] for r in valid]), 1), "avg_pf": round(np.mean([r["profit_factor"] for r in valid if r["profit_factor"] < 100]), 3), "avg_dd": round(np.mean([r["max_drawdown"] for r in valid]), 2), "total_pnl": round(sum(r["total_pnl"] for r in valid), 1), "avg_bars": round(np.mean([r["avg_bars"] for r in valid]), 1)}


def generate_all_configs():
    configs = []
    # ── Stoch period sweep ──
    for sk in [9, 14, 21]:
        for smooth in [3, 5, 7]:
            configs.append(CryptoConfig(name=f"STOCH_K{sk}_S{smooth}", stoch_k=sk, stoch_sk=smooth, stoch_sd=smooth))
    # ── Entry zone sweep ──
    for k_max in [40, 50, 55, 60, 65, 70, 80]:
        configs.append(CryptoConfig(name=f"ENTRY_KMAX{k_max}", entry_k_max_long=k_max, entry_k_min_short=100-k_max))
    # ── RSI filter sweep ──
    for rsi_max in [45, 50, 55, 60, 65, 70, 999]:
        configs.append(CryptoConfig(name=f"RSI_MAX{rsi_max}", rsi_max_long=rsi_max, rsi_min_short=100-rsi_max if rsi_max < 999 else 0))
    # ── Hold bars sweep ──
    for bars in [12, 24, 36, 48, 64, 96, 144, 192, 288]:
        configs.append(CryptoConfig(name=f"HOLD_{bars}bars", optimal_hold_bars=bars))
    # ── Exit gain threshold sweep ──
    for thresh in [0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0]:
        configs.append(CryptoConfig(name=f"EXIT_GAIN{thresh}", exit_gain_threshold=thresh))
    # ── DC profit exit threshold ──
    for dc_gain in [1.0, 2.0, 3.0, 5.0, 7.0, 10.0]:
        configs.append(CryptoConfig(name=f"DC_PROFIT{dc_gain}", dc_profit_exit_min_gain=dc_gain))
    # ── HTF slow stoch params ──
    for htf_k in [14, 21, 30, 50]:
        for htf_s in [5, 7, 10]:
            configs.append(CryptoConfig(name=f"HTF_K{htf_k}_S{htf_s}", htf_stoch_k=htf_k, htf_stoch_sk=htf_s, htf_stoch_sd=htf_s))
    # ── Component combos (ablation winners) ──
    # Best: no stop loss, no augment, no wrong way, no break even, yes stoch exit, yes htf, yes ha
    configs.append(CryptoConfig(name="GOLDEN_BASE"))  # Already has best defaults
    configs.append(CryptoConfig(name="GOLDEN_WT", use_wt_cross=True))
    configs.append(CryptoConfig(name="GOLDEN_NO_DC", use_dc_breakout=False))
    configs.append(CryptoConfig(name="GOLDEN_NO_RSI", use_rsi_filter=False))
    configs.append(CryptoConfig(name="GOLDEN_NO_HA", use_ha_confirm=False))
    configs.append(CryptoConfig(name="GOLDEN_STOP3", use_stop_major_loss=True, stop_loss_pct=-3.0))
    configs.append(CryptoConfig(name="GOLDEN_STOP5", use_stop_major_loss=True, stop_loss_pct=-5.0))
    configs.append(CryptoConfig(name="GOLDEN_STOP10", use_stop_major_loss=True, stop_loss_pct=-10.0))
    configs.append(CryptoConfig(name="GOLDEN_AUG3", augment_enabled=True, min_gain_to_augment=3.0))
    configs.append(CryptoConfig(name="GOLDEN_AUG5", augment_enabled=True, min_gain_to_augment=5.0))
    configs.append(CryptoConfig(name="GOLDEN_AUG10", augment_enabled=True, min_gain_to_augment=10.0))
    # ── Multi-param golden combos ──
    for sk in [9, 14, 21]:
        for k_max in [50, 60, 70]:
            for hold in [36, 48, 96]:
                for exit_g in [0.2, 0.5, 1.0]:
                    configs.append(CryptoConfig(name=f"G_K{sk}_Z{k_max}_H{hold}_E{exit_g}", stoch_k=sk, entry_k_max_long=k_max, entry_k_min_short=100-k_max, optimal_hold_bars=hold, exit_gain_threshold=exit_g))
    return configs


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--symbols", type=int, default=0)
    parser.add_argument("--max-configs", type=int, default=0)
    args = parser.parse_args()
    if args.report:
        if not RESULTS_FILE.exists(): logger.error("No results"); return
        results = json.loads(RESULTS_FILE.read_text())
        sorted_r = sorted(results.items(), key=lambda x: x[1].get("avg_sharpe", -999), reverse=True)
        print(f"\n{'═' * 110}")
        print(f"CRYPTO DEEP VARIATION — Top 30 (15m bars)")
        print(f"{'═' * 110}")
        print(f"\n{'#':>3} {'Config':<40} {'Sharpe':>8} {'WR%':>7} {'PF':>7} {'DD%':>7} {'Trades':>7} {'AvgBars':>8} {'TotalPnL':>10}")
        print("-" * 100)
        for i, (name, m) in enumerate(sorted_r[:30]):
            print(f"{i+1:>3} {name:<40} {m.get('avg_sharpe', 0):>8.3f} {m.get('avg_wr', 0):>6.1f}% {m.get('avg_pf', 0):>7.3f} {m.get('avg_dd', 0):>6.2f}% {m.get('n_trades', 0):>7} {m.get('avg_bars', 0):>8.1f} {m.get('total_pnl', 0):>10.1f}")
        print(f"\nTotal configs tested: {len(results)}")
        return
    symbols = get_symbols(args.symbols)
    logger.info(f"Found {len(symbols)} crypto symbols")
    configs = generate_all_configs()
    if args.max_configs > 0: configs = configs[:args.max_configs]
    logger.info(f"Generated {len(configs)} configs")
    all_results = {}
    if RESULTS_FILE.exists():
        try: all_results = json.loads(RESULTS_FILE.read_text())
        except: pass
    remaining = [c for c in configs if c.name not in all_results]
    logger.info(f"Already tested: {len(all_results)}, remaining: {len(remaining)}")
    pool = Pool(N_WORKERS, initializer=_pool_init)
    best_sharpe = max((v.get("avg_sharpe", -999) for v in all_results.values()), default=-999)
    last_save = time.time()
    try:
        for ci, cfg in enumerate(remaining):
            if shutdown_flag: break
            t0 = time.time()
            tasks = [(s, asdict(cfg), False) for s in symbols]
            results = pool.map(_worker, tasks, chunksize=max(1, len(symbols) // N_WORKERS))
            agg = aggregate(results)
            if not agg: continue
            agg["config"] = asdict(cfg)
            all_results[cfg.name] = agg
            elapsed = time.time() - t0
            marker = " ★" if agg.get("avg_sharpe", -999) > best_sharpe else ""
            if marker: best_sharpe = agg["avg_sharpe"]
            logger.info(f"  [{ci+1}/{len(remaining)}] {cfg.name}: Sharpe={agg.get('avg_sharpe')} WR={agg.get('avg_wr')}% PF={agg.get('avg_pf')} ({agg.get('n_trades', 0)} trades, {elapsed:.1f}s){marker}")
            if time.time() - last_save > 60:
                RESULTS_FILE.write_text(json.dumps(all_results, indent=2, default=str)); last_save = time.time()
    finally:
        pool.close(); pool.join()
    RESULTS_FILE.write_text(json.dumps(all_results, indent=2, default=str))
    sorted_best = sorted(all_results.items(), key=lambda x: x[1].get("avg_sharpe", -999), reverse=True)[:20]
    BEST_FILE.write_text(json.dumps(dict(sorted_best), indent=2, default=str))
    logger.info(f"Done. {len(all_results)} configs. Best: {best_sharpe:.3f}")


if __name__ == "__main__":
    main()
