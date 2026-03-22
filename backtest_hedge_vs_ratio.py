#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
HEDGE vs RATIO EXAGGERATION BACKTEST — Portfolio-level simulation.

Tests three approaches when positions go against you:
  A) NO HEDGE (baseline) — let L/S ratio naturally balance
  B) CROSS-SYMBOL HEDGE — open opposite position on correlated symbol (various sizes)
  C) RATIO EXAGGERATION — multiply the market ratio signal (1x to 5x)

Both crypto (15m, 243 syms) and tradier (D, 121 syms) supported.
No same-symbol hedge allowed per user requirement.

Portfolio simulation: tracks multiple concurrent positions, applies hedge/ratio logic,
measures Sharpe, max drawdown, total PnL at portfolio level.
"""
import os, sys, json, math, time, signal, logging, warnings, argparse, random
import numpy as np
warnings.filterwarnings("ignore", category=RuntimeWarning)
from pathlib import Path
from multiprocessing import Pool, cpu_count
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple
from numpy.lib.stride_tricks import sliding_window_view

logging.basicConfig(level=logging.INFO, format='%(asctime)s [HEDGE_BT] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

import platform
if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance")
RESULTS_DIR = BASE_PATH / "data" / "backtest_hedge_vs_ratio"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE_CRYPTO = RESULTS_DIR / "hedge_ratio_crypto.json"
RESULTS_FILE_TRADIER = RESULTS_DIR / "hedge_ratio_tradier.json"
N_WORKERS = max(1, cpu_count() - 1)
shutdown_flag = False


def _handle_signal(sig, frame):
    global shutdown_flag
    shutdown_flag = True

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ═══ INDICATORS ══════════════════════════════════════════════════════════════

def _sma(arr, p):
    if len(arr) < p: return np.full(len(arr), np.nan)
    clean = np.where(np.isnan(arr), 50.0, arr).astype(np.float64)
    cum = np.cumsum(clean)
    result = np.full(len(arr), np.nan, dtype=np.float64)
    result[p - 1] = cum[p - 1] / p
    if len(arr) > p: result[p:] = (cum[p:] - cum[:-p]) / p
    return result

def _ema(arr, p):
    result = np.full(len(arr), np.nan);
    if len(arr) < p: return result
    m = 2.0 / (p + 1); result[p - 1] = np.mean(arr[:p])
    for i in range(p, len(arr)): result[i] = arr[i] * m + result[i - 1] * (1 - m)
    return result

def stoch(h, lo, c, kp=14, sk=5, sd=5):
    n = len(c)
    if n < kp: return np.full(n, 50.0), np.full(n, 50.0)
    hh = np.max(sliding_window_view(h, kp), axis=1)
    ll = np.min(sliding_window_view(lo, kp), axis=1)
    raw = np.full(n, 50.0); d = hh - ll; v = d > 0
    raw[kp-1:] = np.where(v, (c[kp-1:] - ll) / d * 100, 50.0)
    k = _sma(raw, sk); dd = _sma(k, sd)
    return k, dd

def rsi(c, p=14):
    delta = np.diff(c, prepend=c[0])
    g = np.where(delta > 0, delta, 0.0); l = np.where(delta < 0, -delta, 0.0)
    ag = _ema(g, p); al = _ema(l, p)
    rs = np.where(al > 0, ag / al, 100.0)
    return 100 - 100 / (1 + rs)

def ha(o, h, lo, c):
    hc = (o + h + lo + c) / 4; ho = np.empty_like(o); ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(o)): ho[i] = (ho[i-1] + hc[i-1]) / 2
    return np.where(hc >= ho, 1, -1)

def load_klines(sym, tf, klines_dir):
    p = klines_dir / f"{sym}_{tf}.json"
    if not p.exists(): return None
    try:
        bars = json.loads(p.read_text())
        if not isinstance(bars, list) or len(bars) < 300: return None
        if isinstance(bars[0], dict):
            o = np.array([float(b["open"]) for b in bars], dtype=np.float64)
            h = np.array([float(b["high"]) for b in bars], dtype=np.float64)
            lo = np.array([float(b["low"]) for b in bars], dtype=np.float64)
            c = np.array([float(b["close"]) for b in bars], dtype=np.float64)
        else:
            arr = np.array(bars, dtype=np.float64)
            o, h, lo, c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
        data = np.column_stack([o, h, lo, c])
        if len(data) > 35000: data = data[-35000:]
        return data
    except Exception: return None


# ═══ PORTFOLIO SIMULATOR ═════════════════════════════════════════════════════

@dataclass
class HedgeConfig:
    name: str = "BASELINE"
    # Hedge approach
    hedge_enabled: bool = False
    hedge_cross_symbol: bool = True    # Cross-symbol only (no same-symbol)
    hedge_trigger_loss_pct: float = -1.0  # Open hedge when position loses this %
    hedge_size_ratio: float = 0.5      # Hedge at 50% of losing value
    hedge_momentum_gate: bool = True   # Only hedge when price still moving against
    hedge_max_per_position: int = 1    # Max 1 hedge per losing position
    # Ratio exaggeration
    ratio_multiplier: float = 2.0      # Current system uses 2x. Test 1x to 5x
    ratio_enabled: bool = True         # Apply ratio at all
    # Entry (from ablation winners)
    rsi_max_entry: float = 37.0        # RSI entry gate (crypto)
    exit_gain_threshold: float = 1.0
    # System
    fee_pct: float = 0.08             # Crypto fees
    max_concurrent: int = 20          # Max open positions
    start_capital: float = 10000.0


def run_portfolio_backtest(symbols_data: Dict[str, dict], cfg: HedgeConfig) -> Optional[dict]:
    """Run a portfolio-level backtest with hedge/ratio logic.
    symbols_data: {symbol: {"c": close_array, "k": stoch_k, "d": stoch_d, "ha": ha, "rsi": rsi_array, "n": length}}
    """
    if not symbols_data: return None
    # Find common length (shortest)
    all_syms = sorted(symbols_data.keys())
    min_n = min(d["n"] for d in symbols_data.values())
    if min_n < 400: return None
    warmup = 300
    capital = cfg.start_capital
    fee_mult = cfg.fee_pct / 100.0
    # Portfolio state
    positions = {}  # {symbol: {"side": "LONG"/"SHORT", "entry": price, "entry_bar": bar, "size_usd": $, "hedge_for": None/symbol}}
    equity_curve = []
    trades_log = []
    hedge_count = 0
    hedge_pnl = 0.0
    ratio_adjustments = 0
    for bar in range(warmup, min_n):
        if shutdown_flag: break
        # ── Calculate portfolio L/S ratio ──
        long_value = sum(p["size_usd"] for p in positions.values() if p["side"] == "LONG")
        short_value = sum(p["size_usd"] for p in positions.values() if p["side"] == "SHORT")
        total_value = long_value + short_value
        current_long_pct = (long_value / total_value * 100) if total_value > 0 else 50.0
        # ── Compute market signal (average stoch across portfolio) ──
        avg_k = np.mean([symbols_data[s]["k"][bar] for s in all_syms[:50] if bar < symbols_data[s]["n"] and not np.isnan(symbols_data[s]["k"][bar])])
        avg_d = np.mean([symbols_data[s]["d"][bar] for s in all_syms[:50] if bar < symbols_data[s]["n"] and not np.isnan(symbols_data[s]["d"][bar])])
        market_bullish = avg_k > avg_d
        # ── Apply ratio with multiplier ──
        if cfg.ratio_enabled:
            raw_ratio = 55.0 if market_bullish else 45.0
            applied_long_pct = 50.0 + (raw_ratio - 50.0) * cfg.ratio_multiplier
            applied_long_pct = max(10.0, min(90.0, applied_long_pct))
        else:
            applied_long_pct = 50.0
        target_long_pct = applied_long_pct
        # ── Manage existing positions ──
        to_close = []
        for sym, pos in list(positions.items()):
            if bar >= symbols_data[sym]["n"]: to_close.append(sym); continue
            price = symbols_data[sym]["c"][bar]
            entry = pos["entry"]
            is_long = pos["side"] == "LONG"
            gain_pct = ((price - entry) / entry * 100) if is_long else ((entry - price) / entry * 100) if entry > 0 else 0
            bars_held = bar - pos["entry_bar"]
            k_val = symbols_data[sym]["k"][bar] if not np.isnan(symbols_data[sym]["k"][bar]) else 50
            k_prev = symbols_data[sym]["k"][bar-1] if bar > 0 and not np.isnan(symbols_data[sym]["k"][bar-1]) else k_val
            # Exit conditions (from ablation winners)
            should_exit = False
            if gain_pct > cfg.exit_gain_threshold:
                # Stoch cross exit
                d_val = symbols_data[sym]["d"][bar] if not np.isnan(symbols_data[sym]["d"][bar]) else 50
                d_prev = symbols_data[sym]["d"][bar-1] if bar > 0 and not np.isnan(symbols_data[sym]["d"][bar-1]) else d_val
                if (is_long and k_val < d_val and k_prev >= d_prev) or (not is_long and k_val > d_val and k_prev <= d_prev):
                    should_exit = True
            if bars_held >= 48 and gain_pct > cfg.exit_gain_threshold:
                should_exit = True
            # ── HEDGE LOGIC: if losing and hedge enabled ──
            if cfg.hedge_enabled and gain_pct < cfg.hedge_trigger_loss_pct and not pos.get("is_hedge") and pos.get("hedge_count", 0) < cfg.hedge_max_per_position:
                # Momentum gate
                open_hedge = True
                if cfg.hedge_momentum_gate:
                    price_moving_against = (is_long and k_val < k_prev) or (not is_long and k_val > k_prev)
                    if not price_moving_against:
                        open_hedge = False
                if open_hedge and cfg.hedge_cross_symbol and len(positions) < cfg.max_concurrent:
                    # Find a correlated symbol to hedge with (pick random from top movers opposite direction)
                    hedge_side = "SHORT" if is_long else "LONG"
                    hedge_size = pos["size_usd"] * cfg.hedge_size_ratio
                    # Pick best hedge candidate (highest abs momentum in opposite direction)
                    best_hedge_sym = None
                    best_score = -999
                    for hs in all_syms:
                        if hs == sym or hs in positions: continue
                        if bar >= symbols_data[hs]["n"]: continue
                        hk = symbols_data[hs]["k"][bar]
                        if np.isnan(hk): continue
                        # For long hedge (we want price going up): pick high k
                        # For short hedge (we want price going down): pick low k
                        score = (100 - hk) if hedge_side == "SHORT" else hk
                        if score > best_score:
                            best_score = score
                            best_hedge_sym = hs
                    if best_hedge_sym:
                        h_price = symbols_data[best_hedge_sym]["c"][bar]
                        if h_price > 0:
                            positions[best_hedge_sym] = {"side": hedge_side, "entry": h_price, "entry_bar": bar, "size_usd": hedge_size, "is_hedge": True, "hedge_for": sym, "hedge_count": 0}
                            pos["hedge_count"] = pos.get("hedge_count", 0) + 1
                            hedge_count += 1
            if should_exit:
                pnl = gain_pct - fee_mult * 100 * 2
                pnl_usd = pnl / 100 * pos["size_usd"]
                capital += pnl_usd
                if pos.get("is_hedge"):
                    hedge_pnl += pnl_usd
                trades_log.append({"pnl_pct": pnl, "pnl_usd": pnl_usd, "is_hedge": pos.get("is_hedge", False), "bars": bars_held})
                to_close.append(sym)
        for sym in set(to_close):
            positions.pop(sym, None)
        # ── Open new positions based on ratio ──
        if len(positions) < cfg.max_concurrent:
            # How many longs vs shorts should we have?
            n_pos = len(positions)
            n_long = sum(1 for p in positions.values() if p["side"] == "LONG")
            n_short = n_pos - n_long
            target_n_long = int(cfg.max_concurrent * target_long_pct / 100)
            target_n_short = cfg.max_concurrent - target_n_long
            need_long = target_n_long - n_long > 0
            need_short = target_n_short - n_short > 0
            # Pick entry candidates
            for sym in all_syms:
                if sym in positions: continue
                if bar >= symbols_data[sym]["n"]: continue
                if len(positions) >= cfg.max_concurrent: break
                price = symbols_data[sym]["c"][bar]
                if price <= 0: continue
                k_val = symbols_data[sym]["k"][bar]
                rsi_val = symbols_data[sym]["rsi"][bar]
                ha_val = symbols_data[sym]["ha"][bar]
                if np.isnan(k_val) or np.isnan(rsi_val): continue
                k_prev = symbols_data[sym]["k"][bar-1] if bar > 0 and not np.isnan(symbols_data[sym]["k"][bar-1]) else k_val
                d_val = symbols_data[sym]["d"][bar]
                d_prev = symbols_data[sym]["d"][bar-1] if bar > 0 else d_val
                # Entry signals (from ablation: stoch cross + HA + RSI gate)
                stoch_co = k_val > d_val and k_prev <= d_prev
                stoch_cu = k_val < d_val and k_prev >= d_prev
                if need_long and stoch_co and ha_val == 1 and rsi_val < cfg.rsi_max_entry and k_val < 65:
                    pos_size = capital * 0.02  # 2% per position
                    positions[sym] = {"side": "LONG", "entry": price, "entry_bar": bar, "size_usd": pos_size, "hedge_count": 0}
                    if cfg.ratio_enabled: ratio_adjustments += 1
                elif need_short and stoch_cu and ha_val == -1 and rsi_val > (100 - cfg.rsi_max_entry) and k_val > 35:
                    pos_size = capital * 0.02
                    positions[sym] = {"side": "SHORT", "entry": price, "entry_bar": bar, "size_usd": pos_size, "hedge_count": 0}
                    if cfg.ratio_enabled: ratio_adjustments += 1
        equity_curve.append(capital)
    # Metrics
    if len(trades_log) < 10: return None
    rets = np.array([t["pnl_pct"] for t in trades_log])
    mean_r = rets.mean(); std_r = rets.std()
    if std_r <= 0: return None
    annual = 35040  # 15m
    sharpe = mean_r / std_r * math.sqrt(annual)
    wins = np.sum(rets > 0); wr = wins / len(rets) * 100
    gp = rets[rets > 0].sum(); gl = abs(rets[rets < 0].sum())
    pf = gp / gl if gl > 0 else 999
    eq = np.array(equity_curve)
    peak = np.maximum.accumulate(eq); dd = ((eq - peak) / peak * 100)
    max_dd = dd.min() if len(dd) > 0 else 0
    hedge_trades = [t for t in trades_log if t.get("is_hedge")]
    hedge_wr = sum(1 for t in hedge_trades if t["pnl_pct"] > 0) / len(hedge_trades) * 100 if hedge_trades else 0
    return {"name": cfg.name, "n_trades": len(rets), "sharpe": round(sharpe, 3), "win_rate": round(wr, 1), "profit_factor": round(pf, 3), "max_drawdown": round(max_dd, 2), "final_capital": round(capital, 2), "total_return_pct": round((capital - cfg.start_capital) / cfg.start_capital * 100, 2), "hedge_count": hedge_count, "hedge_pnl": round(hedge_pnl, 2), "hedge_wr": round(hedge_wr, 1), "ratio_adjustments": ratio_adjustments, "avg_pnl": round(mean_r, 4), "n_hedge_trades": len(hedge_trades)}


# ═══ CONFIG GENERATOR ════════════════════════════════════════════════════════

def generate_configs():
    configs = []
    # A) BASELINE — no hedge, ratio at current 2x
    configs.append(HedgeConfig(name="BASELINE_NO_HEDGE_RATIO2X", hedge_enabled=False, ratio_multiplier=2.0))
    configs.append(HedgeConfig(name="BASELINE_NO_HEDGE_NO_RATIO", hedge_enabled=False, ratio_enabled=False, ratio_multiplier=1.0))
    # B) RATIO EXAGGERATION sweep (no hedge)
    for mult in [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0]:
        configs.append(HedgeConfig(name=f"RATIO_{mult}X", hedge_enabled=False, ratio_multiplier=mult))
    # C) HEDGE sweep (ratio at 2x baseline)
    for size in [0.15, 0.25, 0.35, 0.5, 0.75, 1.0]:
        for trigger in [-0.5, -1.0, -1.5, -2.0, -3.0]:
            configs.append(HedgeConfig(name=f"HEDGE_{int(size*100)}pct_T{abs(trigger)}pct", hedge_enabled=True, hedge_size_ratio=size, hedge_trigger_loss_pct=trigger, ratio_multiplier=2.0))
    # D) HEDGE without momentum gate
    for size in [0.25, 0.5]:
        for trigger in [-1.0, -2.0]:
            configs.append(HedgeConfig(name=f"HEDGE_{int(size*100)}pct_T{abs(trigger)}_NO_MOM", hedge_enabled=True, hedge_size_ratio=size, hedge_trigger_loss_pct=trigger, hedge_momentum_gate=False, ratio_multiplier=2.0))
    # E) HEDGE + HIGH RATIO (hedge + aggressive ratio)
    for mult in [3.0, 4.0, 5.0]:
        for size in [0.25, 0.5]:
            configs.append(HedgeConfig(name=f"HEDGE_{int(size*100)}pct_RATIO_{mult}X", hedge_enabled=True, hedge_size_ratio=size, hedge_trigger_loss_pct=-1.5, ratio_multiplier=mult))
    # F) RATIO ONLY (no hedge) with different entry RSI
    for mult in [2.0, 3.0, 4.0]:
        for rsi_max in [35, 37, 40, 45]:
            configs.append(HedgeConfig(name=f"RATIO_{mult}X_RSI{rsi_max}", hedge_enabled=False, ratio_multiplier=mult, rsi_max_entry=float(rsi_max)))
    return configs


# ═══ MAIN ════════════════════════════════════════════════════════════════════

def precompute_symbols(klines_dir, tf, max_syms=0):
    """Load and precompute indicators for all symbols."""
    symbols_data = {}
    files = sorted([f.name for f in klines_dir.iterdir() if f.name.endswith(f"_{tf}.json")])
    for fname in files:
        sym = fname.replace(f"_{tf}.json", "")
        if klines_dir.name == "tradier":
            pass  # Stock symbols: AAPL, MSFT, etc. — no suffix filter
        elif not (sym.endswith("USDT") or sym.endswith("USDC")):
            continue
        data = load_klines(sym, tf, klines_dir)
        if data is None: continue
        o, h, lo, c = data[:, 0], data[:, 1], data[:, 2], data[:, 3]
        k, d = stoch(h, lo, c, 14, 5, 5)
        r = rsi(c, 14)
        ha_arr = ha(o, h, lo, c)
        symbols_data[sym] = {"c": c, "k": k, "d": d, "rsi": r, "ha": ha_arr, "n": len(c)}
        if max_syms > 0 and len(symbols_data) >= max_syms: break
    return symbols_data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", choices=["crypto", "tradier", "both"], default="both")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--symbols", type=int, default=0)
    args = parser.parse_args()
    if args.report:
        for name, rf in [("CRYPTO", RESULTS_FILE_CRYPTO), ("TRADIER", RESULTS_FILE_TRADIER)]:
            if not rf.exists(): continue
            results = json.loads(rf.read_text())
            sorted_r = sorted(results.items(), key=lambda x: x[1].get("sharpe", -999), reverse=True)
            print(f"\n{'=' * 110}")
            print(f"HEDGE vs RATIO — {name}")
            print(f"{'=' * 110}")
            print(f"{'#':>3} {'Config':<40} {'Sharpe':>8} {'WR%':>7} {'PF':>7} {'DD%':>7} {'Return%':>8} {'HedgeCnt':>8} {'HedgePnL':>9} {'HedgeWR':>8}")
            print("-" * 110)
            for i, (n, m) in enumerate(sorted_r[:25]):
                print(f"{i+1:>3} {n:<40} {m.get('sharpe', 0):>8.3f} {m.get('win_rate', 0):>6.1f}% {m.get('profit_factor', 0):>7.3f} {m.get('max_drawdown', 0):>6.2f}% {m.get('total_return_pct', 0):>7.2f}% {m.get('hedge_count', 0):>8} {m.get('hedge_pnl', 0):>8.1f}$ {m.get('hedge_wr', 0):>7.1f}%")
            # Group by approach
            ratio_only = [(n, m) for n, m in results.items() if n.startswith("RATIO_") and "HEDGE" not in n]
            hedge_only = [(n, m) for n, m in results.items() if n.startswith("HEDGE_") and "RATIO" not in n]
            both = [(n, m) for n, m in results.items() if "HEDGE" in n and "RATIO" in n]
            print(f"\nBEST PER APPROACH:")
            for label, group in [("Ratio Only", ratio_only), ("Hedge Only", hedge_only), ("Hedge+Ratio", both)]:
                if group:
                    best = max(group, key=lambda x: x[1].get("sharpe", -999))
                    print(f"  {label:<15} → {best[0]:<40} Sharpe={best[1].get('sharpe', 0):.3f} Return={best[1].get('total_return_pct', 0):.2f}%")
        return
    configs = generate_configs()
    logger.info(f"Generated {len(configs)} hedge/ratio configs to test")
    for system in (["crypto", "tradier"] if args.system == "both" else [args.system]):
        if system == "crypto":
            klines_dir = BASE_PATH / "klines_cache"
            if not klines_dir.exists(): klines_dir = Path("/home/niels/binance-sandbox/klines_cache")
            tf = "15m"
            results_file = RESULTS_FILE_CRYPTO
        else:
            klines_dir = BASE_PATH / "klines_cache" / "tradier"
            tf = "D"
            results_file = RESULTS_FILE_TRADIER
            # Adjust for stocks
            for c in configs:
                c.fee_pct = 0.10
                c.rsi_max_entry = 30.0  # RSI(14) < 30 for stocks (from tradier ablation)
                c.max_concurrent = 15
                c.exit_gain_threshold = 2.0  # Stocks need wider exits (daily bars)
        logger.info(f"[{system.upper()}] Loading klines from {klines_dir} ({tf})...")
        symbols_data = precompute_symbols(klines_dir, tf, args.symbols)
        logger.info(f"[{system.upper()}] Loaded {len(symbols_data)} symbols")
        if len(symbols_data) < 5:
            logger.warning(f"[{system.upper()}] Too few symbols, skipping")
            continue
        all_results = {}
        if results_file.exists():
            try: all_results = json.loads(results_file.read_text())
            except: pass
        remaining = [c for c in configs if c.name not in all_results]
        logger.info(f"[{system.upper()}] Already: {len(all_results)}, remaining: {len(remaining)}")
        best_sharpe = max((v.get("sharpe", -999) for v in all_results.values()), default=-999)
        last_save = time.time()
        for ci, cfg in enumerate(remaining):
            if shutdown_flag: break
            t0 = time.time()
            result = run_portfolio_backtest(symbols_data, cfg)
            if not result: continue
            all_results[cfg.name] = result
            elapsed = time.time() - t0
            marker = " *" if result.get("sharpe", -999) > best_sharpe else ""
            if marker: best_sharpe = result["sharpe"]
            logger.info(f"  [{system.upper()}][{ci+1}/{len(remaining)}] {cfg.name}: Sharpe={result['sharpe']} WR={result['win_rate']}% Return={result['total_return_pct']}% Hedges={result['hedge_count']} ({elapsed:.1f}s){marker}")
            if time.time() - last_save > 30:
                results_file.write_text(json.dumps(all_results, indent=2, default=str)); last_save = time.time()
        results_file.write_text(json.dumps(all_results, indent=2, default=str))
        logger.info(f"[{system.upper()}] Done. {len(all_results)} configs. Best Sharpe: {best_sharpe:.3f}")


if __name__ == "__main__":
    main()
