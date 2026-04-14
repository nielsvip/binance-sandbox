#!/usr/bin/env python3
"""TRADIER VECTORIZED MASS SWEEP — MILLIONS of data points per hour.

Uses numpy vectorized entry/exit masks from backtest_evaluate_functions_tradier.py
to screen THOUSANDS of configs in minutes. Results → CSV → XLS.

Each config tests 121 symbols × 9000+ bars × LONG+SHORT = massive output.

Usage:
    python3 tradier_vectorized_mass_sweep.py                # Full sweep
    python3 tradier_vectorized_mass_sweep.py --half 1       # First half (Server 1)
    python3 tradier_vectorized_mass_sweep.py --half 2       # Second half (Server 2)
"""
import argparse, csv, itertools, json, logging, os, platform, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("mass_sweep")

IS_SERVER = platform.system() == "Linux"
BASE = Path("/home/niels/binance-sandbox") if IS_SERVER else Path("/Users/niels/Documents/binance")
INDICATORS_DIR = BASE / "backtest_v4_tradier" / "indicators"
if IS_SERVER:
    RESULTS_DIR = Path("/home/niels/tradier_sweep_results")
else:
    RESULTS_DIR = Path("/Users/niels/Documents/binance/sweep_results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Indicator reader (same as evaluate_functions_tradier)
# ─────────────────────────────────────────────────────────────────────────────
class IndicatorReader:
    def __init__(self):
        self.data: Dict[str, dict] = {}
        self.timestamps = None
        self.symbols: List[str] = []
    def load_all(self, indicators_dir):
        t0 = time.time()
        for f in sorted(Path(indicators_dir).glob("*.npz")):
            sym = f.stem
            d = dict(np.load(str(f), allow_pickle=True))
            self.data[sym] = d
            self.symbols.append(sym)
        if self.symbols:
            self.timestamps = self.data[self.symbols[0]]["timestamps"]
        log.info(f"Loaded {len(self.symbols)} symbols, {len(self.timestamps) if self.timestamps is not None else 0} bars in {time.time()-t0:.1f}s")

def arr(data, key, n, default=0.0):
    a = data.get(key)
    if a is None: return np.full(n, default, dtype=np.float64)
    a = np.asarray(a, dtype=np.float64)
    if len(a) < n: a = np.concatenate([a, np.full(n - len(a), default, dtype=np.float64)])
    return a[:n]

def arr_bool(data, key, n):
    a = data.get(key)
    if a is None: return np.zeros(n, dtype=bool)
    a = np.asarray(a)
    if len(a) < n: a = np.concatenate([a, np.zeros(n - len(a), dtype=a.dtype)])
    return a[:n].astype(bool)

# ─────────────────────────────────────────────────────────────────────────────
# Entry mask — vectorized rate_entry for stocks
# ─────────────────────────────────────────────────────────────────────────────
def mask_entry(d, n, is_long, cfg):
    k5m = arr(d, "stoch_k_5m", n, 50)
    k15m = arr(d, "stoch_k_15m", n, 50)
    rsi15m = arr(d, "rsi_15m", n, 50)
    mfi15m = arr(d, "mfi_15m", n, 50)
    mfi1h = arr(d, "mfi_1h", n, 50)
    wt_bull_5m = arr_bool(d, "wt_bullish_5m", n)
    wt_bull_15m = arr_bool(d, "wt_bullish_15m", n)
    wt_bull_1h = arr_bool(d, "wt_bullish_1h", n)
    wt_bull_4h = arr_bool(d, "wt_bullish_4h", n)
    wt_bull_D = arr_bool(d, "wt_bullish_D", n)
    wt_vel_5m = arr(d, "wt_velocity_5m", n)
    wt_cross_bull_5m = arr_bool(d, "wt_cross_bull_5m", n)
    wt_cross_bear_5m = arr_bool(d, "wt_cross_bear_5m", n)
    dc_pos_15m = arr(d, "dc_position_15m", n, 0.5)
    ha_5m = arr(d, "ha_5m", n)
    sma200D = arr(d, "sma_200_D", n)
    close = arr(d, "close", n)
    score = np.zeros(n, dtype=np.float64)
    if is_long:
        ltf = wt_bull_5m.astype(np.float64) + wt_bull_15m.astype(np.float64)
        htf = wt_bull_1h.astype(np.float64) + wt_bull_4h.astype(np.float64) + wt_bull_D.astype(np.float64)
    else:
        ltf = (~wt_bull_5m).astype(np.float64) + (~wt_bull_15m).astype(np.float64)
        htf = (~wt_bull_1h).astype(np.float64) + (~wt_bull_4h).astype(np.float64) + (~wt_bull_D).astype(np.float64)
    ltf_min = cfg.get("wt_ltf_min", 2)
    htf_min = cfg.get("htf_min", 1)
    ltf_ok = ltf >= ltf_min
    htf_ok = htf >= htf_min
    score += ltf * 4 + htf * 3
    filt = cfg.get("entry_filter_mode", "mfi")
    if filt == "mfi":
        if is_long: score += np.where(mfi15m < cfg.get("mfi_entry_max_long", 40), 3, -2)
        else: score += np.where(mfi15m > cfg.get("mfi_entry_min_short", 60), 3, -2)
    elif filt == "rsi":
        if is_long: score += np.where(rsi15m < cfg.get("rsi_entry_max_long", 40), 3, -2)
        else: score += np.where(rsi15m > cfg.get("rsi_entry_min_short", 60), 3, -2)
    elif filt == "none":
        pass
    if is_long:
        score += np.where(dc_pos_15m < 0.3, 2, 0)
        score += np.where(wt_cross_bull_5m, 3, 0)
        score += np.where(wt_vel_5m > 0, 1, 0)
        score += np.where(ha_5m > 0, 1, 0)
    else:
        score += np.where(dc_pos_15m > 0.7, 2, 0)
        score += np.where(wt_cross_bear_5m, 3, 0)
        score += np.where(wt_vel_5m < 0, 1, 0)
        score += np.where(ha_5m < 0, 1, 0)
    sma200_dist = np.where(sma200D > 0, (close - sma200D) / sma200D * 100, 0)
    if is_long:
        score += np.where(sma200_dist < -5, 2, 0)
        score += np.where(sma200_dist > 20, -3, 0)
    else:
        score += np.where(sma200_dist > 5, 2, 0)
        score += np.where(sma200_dist < -20, -3, 0)
    k5m_cap = cfg.get("k5m_cap", 80)
    if is_long:
        k_ok = k5m < k5m_cap
    else:
        k_ok = k5m > (100 - k5m_cap)
    score_min = cfg.get("entry_score_min", 18)
    mask = ltf_ok & htf_ok & k_ok & (score >= score_min)
    # D gate: block entry if Daily WT opposes direction
    if cfg.get("d_gate", False):
        if is_long: mask &= wt_bull_D
        else: mask &= ~wt_bull_D
    return mask, score

# ─────────────────────────────────────────────────────────────────────────────
# Exit masks — vectorized
# ─────────────────────────────────────────────────────────────────────────────
def mask_exit_wt(d, n, is_long, cfg):
    tfs = cfg.get("wt_exit_tfs", ["4h", "D"])
    exit_mask = np.zeros(n, dtype=bool)
    wt_count = np.zeros(n, dtype=np.float64)
    for tf in tfs:
        wt_bull = arr_bool(d, f"wt_bullish_{tf}", n)
        if is_long:
            wt_count += (~wt_bull).astype(np.float64)
        else:
            wt_count += wt_bull.astype(np.float64)
    min_tfs = cfg.get("wt_exit_min_tfs", max(1, len(tfs) - 1))
    exit_mask = wt_count >= min_tfs
    # Velocity-based exit
    if cfg.get("wt_exit_velocity", False):
        vel_thresh = cfg.get("wt_vel_exit_threshold", -3.0)
        for tf in tfs:
            vel = arr(d, f"wt_velocity_{tf}", n)
            if is_long:
                exit_mask |= (vel < vel_thresh)
            else:
                exit_mask |= (vel > -vel_thresh)
    return exit_mask

def mask_exit_stoch(d, n, is_long):
    k5m = arr(d, "stoch_k_5m", n, 50)
    d5m = arr(d, "stoch_d_5m", n, 50)
    if is_long:
        return (k5m > 80) & (k5m < d5m)
    else:
        return (k5m < 20) & (k5m > d5m)

# ─────────────────────────────────────────────────────────────────────────────
# Portfolio simulator (vectorized positions)
# ─────────────────────────────────────────────────────────────────────────────
def simulate(reader, entry_masks, exit_wt, exit_stoch, cfg, si, ei):
    capital = cfg.get("capital", 70000.0)
    max_pos = cfg.get("max_positions", 40)
    pos_size = cfg.get("position_size", 1200.0)
    noloss_pct = cfg.get("noloss_min_profit_pct", 0.0)
    min_hold = cfg.get("min_hold_bars", 4)
    cd_bars = cfg.get("cooldown_bars", 16)
    positions = {}
    trades = []
    equity = [capital]
    total_pnl = 0.0
    cooldown = {}
    for bar in range(si, ei):
        # Process exits
        to_close = []
        for pk, pos in positions.items():
            sym, side = pos["sym"], pos["side"]
            price = float(reader.data[sym]["close"][bar]) if bar < len(reader.data[sym]["close"]) else 0
            if price <= 0: continue
            is_long = side == "LONG"
            gain = ((price - pos["entry"]) / pos["entry"] * 100) if is_long else ((pos["entry"] - price) / pos["entry"] * 100)
            pos["max_gain"] = max(pos["max_gain"], gain)
            held = bar - pos["bar"]
            if held < min_hold: continue
            # WT exit
            do_exit = False
            wt_exit = exit_wt[sym][side][bar] if bar < len(exit_wt[sym][side]) else False
            stoch_exit = exit_stoch[sym][side][bar] if bar < len(exit_stoch[sym][side]) else False
            if wt_exit or stoch_exit:
                if noloss_pct <= 0 or gain >= noloss_pct:
                    do_exit = True
                elif noloss_pct > 0 and gain < noloss_pct:
                    do_exit = False  # NOLOSS blocks
            if do_exit:
                pnl = pos["qty"] * pos["entry"] * gain / 100
                total_pnl += pnl
                trades.append({"bar": bar, "symbol": sym, "side": side, "action": "EXIT", "gain": gain, "pnl": pnl, "held": held, "reason": "WT" if wt_exit else "STOCH"})
                to_close.append(pk)
        for pk in to_close:
            del positions[pk]
        # Process entries
        if len(positions) < max_pos:
            cands = []
            for sym in reader.symbols:
                if cooldown.get(sym, 0) > bar: continue
                price = float(reader.data[sym]["close"][bar]) if bar < len(reader.data[sym]["close"]) else 0
                if price <= 0: continue
                for side in ["LONG", "SHORT"]:
                    pk = f"{sym}_{side}"
                    if pk in positions: continue
                    if bar < len(entry_masks[sym][side]) and entry_masks[sym][side][bar]:
                        qty = pos_size / price
                        cands.append((sym, side, price, qty))
            for sym, side, price, qty in cands[:6]:
                pk = f"{sym}_{side}"
                if pk in positions or len(positions) >= max_pos: continue
                positions[pk] = {"sym": sym, "side": side, "entry": price, "qty": qty, "max_gain": 0, "bar": bar}
                trades.append({"bar": bar, "symbol": sym, "side": side, "action": "ENTRY", "gain": 0, "pnl": 0, "held": 0, "reason": "ENTRY"})
                cooldown[sym] = bar + cd_bars
        # Track equity daily
        if bar % 96 == 0:
            ur = 0.0
            for pos in positions.values():
                p = float(reader.data[pos["sym"]]["close"][bar]) if bar < len(reader.data[pos["sym"]]["close"]) else 0
                if p <= 0 or pos["entry"] <= 0: continue
                g = ((p - pos["entry"]) / pos["entry"]) if pos["side"] == "LONG" else ((pos["entry"] - p) / pos["entry"])
                ur += pos["qty"] * pos["entry"] * g
            equity.append(capital + total_pnl + ur)
    return trades, equity, total_pnl

def compute_metrics(trades, equity, total_pnl):
    eq = np.array(equity) if equity else np.array([70000.0])
    ret = np.diff(eq) / np.maximum(eq[:-1], 1.0) if len(eq) > 1 else np.array([0.0])
    sharpe = float(np.mean(ret) / max(np.std(ret), 1e-10) * np.sqrt(252)) if len(ret) > 10 else 0.0
    max_dd = 0.0; peak = eq[0]
    for v in eq:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd
    exits = [t for t in trades if t["action"] == "EXIT"]
    wins = [t for t in exits if t["pnl"] > 0]
    losses = [t for t in exits if t["pnl"] <= 0]
    twp = sum(t["pnl"] for t in wins)
    tlp = abs(sum(t["pnl"] for t in losses)) or 1.0
    return {
        "sharpe": round(sharpe, 4),
        "total_pnl": round(total_pnl, 2),
        "final_equity": round(float(eq[-1]), 2),
        "max_drawdown_pct": round(max_dd, 2),
        "total_trades": len(exits),
        "entries": sum(1 for t in trades if t["action"] == "ENTRY"),
        "win_rate": round(len(wins) / max(len(exits), 1) * 100, 1),
        "profit_factor": round(twp / tlp, 3),
        "avg_win_pct": round(np.mean([t["gain"] for t in wins]), 3) if wins else 0,
        "avg_loss_pct": round(np.mean([t["gain"] for t in losses]), 3) if losses else 0,
        "avg_hold_bars": round(np.mean([t["held"] for t in exits]), 1) if exits else 0,
        "wt_exits": sum(1 for t in exits if t["reason"] == "WT"),
        "stoch_exits": sum(1 for t in exits if t["reason"] == "STOCH"),
    }

# ─────────────────────────────────────────────────────────────────────────────
# PARAMETER GRID — MASSIVE
# ─────────────────────────────────────────────────────────────────────────────
def generate_configs():
    """Generate ALL config combinations to test."""
    configs = []
    # Grid dimensions
    noloss_vals = [0, 0.1, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0]
    entry_score_vals = [12, 14, 16, 18, 20, 22, 24, 28, 32]
    ltf_min_vals = [1, 2]
    htf_min_vals = [1, 2, 3]
    filter_modes = ["mfi", "rsi", "none"]
    mfi_long_vals = [30, 35, 40, 50]
    k5m_cap_vals = [60, 70, 80, 90, 100]
    wt_exit_tf_combos = [["4h"], ["D"], ["4h", "D"], ["1h", "4h"], ["1h", "4h", "D"], ["1h"]]
    wt_exit_min_tfs_vals = [1, 2]
    wt_exit_velocity_vals = [False, True]
    wt_vel_thresholds = [-2.0, -3.0, -5.0, -8.0]
    d_gate_vals = [False, True]
    min_hold_vals = [4, 8, 16, 32, 64]
    cooldown_vals = [8, 16, 32]
    pos_size_vals = [800, 1200, 1800]
    max_pos_vals = [20, 40, 60]
    # PHASE 1: Entry parameter sweep (fast — no exit variation)
    log.info("Generating Phase 1: Entry sweep configs...")
    for score in entry_score_vals:
        for ltf in ltf_min_vals:
            for htf in htf_min_vals:
                for filt in filter_modes:
                    mfi_vals = mfi_long_vals if filt == "mfi" else [40]
                    for mfi in mfi_vals:
                        for k5m in k5m_cap_vals:
                            for dg in d_gate_vals:
                                name = f"E_s{score}_l{ltf}_h{htf}_{filt}_m{mfi}_k{k5m}_dg{int(dg)}"
                                cfg = {
                                    "entry_score_min": score, "wt_ltf_min": ltf, "htf_min": htf,
                                    "entry_filter_mode": filt, "mfi_entry_max_long": mfi,
                                    "mfi_entry_min_short": 100 - mfi, "k5m_cap": k5m, "d_gate": dg,
                                    "noloss_min_profit_pct": 0, "wt_exit_tfs": ["4h", "D"],
                                    "wt_exit_min_tfs": 1, "min_hold_bars": 16,
                                    "cooldown_bars": 16, "position_size": 1200, "max_positions": 40,
                                }
                                configs.append((name, cfg))
    log.info(f"  Phase 1: {len(configs)} entry configs")
    p1_count = len(configs)
    # PHASE 2: Exit parameter sweep (with default entry)
    log.info("Generating Phase 2: Exit sweep configs...")
    for noloss in noloss_vals:
        for wt_tfs in wt_exit_tf_combos:
            for wt_min in wt_exit_min_tfs_vals:
                if wt_min > len(wt_tfs): continue
                for vel in wt_exit_velocity_vals:
                    vel_vals = wt_vel_thresholds if vel else [0]
                    for vt in vel_vals:
                        for mh in min_hold_vals:
                            for cd in cooldown_vals:
                                tfs_str = "+".join(wt_tfs)
                                name = f"X_nl{noloss}_wt{tfs_str}_min{wt_min}_vel{int(vel)}_vt{vt}_mh{mh}_cd{cd}"
                                cfg = {
                                    "entry_score_min": 18, "wt_ltf_min": 2, "htf_min": 1,
                                    "entry_filter_mode": "mfi", "mfi_entry_max_long": 40,
                                    "mfi_entry_min_short": 60, "k5m_cap": 80, "d_gate": False,
                                    "noloss_min_profit_pct": noloss, "wt_exit_tfs": wt_tfs,
                                    "wt_exit_min_tfs": wt_min, "wt_exit_velocity": vel,
                                    "wt_vel_exit_threshold": vt, "min_hold_bars": mh,
                                    "cooldown_bars": cd, "position_size": 1200, "max_positions": 40,
                                }
                                configs.append((name, cfg))
    log.info(f"  Phase 2: {len(configs) - p1_count} exit configs")
    p2_count = len(configs)
    # PHASE 3: Sizing/position management sweep
    log.info("Generating Phase 3: Sizing sweep configs...")
    for ps in pos_size_vals:
        for mp in max_pos_vals:
            for noloss in [0, 0.5, 2.0]:
                for mh in [8, 16, 32]:
                    name = f"S_ps{ps}_mp{mp}_nl{noloss}_mh{mh}"
                    cfg = {
                        "entry_score_min": 18, "wt_ltf_min": 2, "htf_min": 1,
                        "entry_filter_mode": "mfi", "mfi_entry_max_long": 40,
                        "mfi_entry_min_short": 60, "k5m_cap": 80, "d_gate": False,
                        "noloss_min_profit_pct": noloss, "wt_exit_tfs": ["4h", "D"],
                        "wt_exit_min_tfs": 1, "min_hold_bars": mh,
                        "cooldown_bars": 16, "position_size": ps, "max_positions": mp,
                    }
                    configs.append((name, cfg))
    log.info(f"  Phase 3: {len(configs) - p2_count} sizing configs")
    log.info(f"  TOTAL: {len(configs)} configs")
    return configs

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--half", type=int, default=0, help="1=first half, 2=second half")
    args = parser.parse_args()
    log.info("Loading tradier indicators...")
    reader = IndicatorReader()
    reader.load_all(INDICATORS_DIR)
    if not reader.symbols:
        log.error("No data!"); return
    n = len(reader.timestamps)
    ts = reader.timestamps
    # Date range: 2024-06-01 to end
    start_epoch = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp())
    end_epoch = int(ts[-1])
    si = int(np.searchsorted(ts, start_epoch))
    ei = min(int(np.searchsorted(ts, end_epoch, side="right")), n)
    log.info(f"Range: bar {si}-{ei} ({ei-si:,} bars), {len(reader.symbols)} symbols")
    configs = generate_configs()
    if args.half == 1:
        configs = configs[:len(configs)//2]
        log.info(f"Running FIRST half: {len(configs)} configs")
    elif args.half == 2:
        configs = configs[len(configs)//2:]
        log.info(f"Running SECOND half: {len(configs)} configs")
    # Precompute exit masks for ALL configs' TF combos
    log.info("Precomputing exit masks for all TF combos...")
    t0 = time.time()
    exit_cache = {}
    all_tf_combos = set()
    for name, cfg in configs:
        key = tuple(sorted(cfg.get("wt_exit_tfs", ["4h", "D"])))
        all_tf_combos.add(key)
    for tf_combo in all_tf_combos:
        exit_wt = {}; exit_stoch = {}
        for sym in reader.symbols:
            d = reader.data[sym]
            exit_wt[sym] = {}; exit_stoch[sym] = {}
            for side, il in [("LONG", True), ("SHORT", False)]:
                exit_wt[sym][side] = mask_exit_wt(d, n, il, {"wt_exit_tfs": list(tf_combo), "wt_exit_min_tfs": 1})
                exit_stoch[sym][side] = mask_exit_stoch(d, n, il)
        exit_cache[tf_combo] = (exit_wt, exit_stoch)
    log.info(f"Exit masks precomputed for {len(all_tf_combos)} TF combos in {time.time()-t0:.1f}s")
    # CSV output — summary
    summary_csv = RESULTS_DIR / f"tradier_sweep_summary_{'h'+str(args.half) if args.half else 'full'}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    log.info(f"Output: {summary_csv}")
    # Skip already-done configs from previous run
    done_configs = set()
    for prev in RESULTS_DIR.glob("tradier_sweep_summary_*.csv"):
        with open(prev) as pf:
            for pr in csv.DictReader(pf):
                done_configs.add(pr.get("config_name", ""))
    if done_configs:
        log.info(f"Skipping {len(done_configs)} already-done configs from previous runs")
    with open(summary_csv, "w", newline="") as sf:
        sw = csv.writer(sf)
        sw.writerow(["config_name", "entry_score_min", "wt_ltf_min", "htf_min", "entry_filter_mode",
                      "mfi_entry_max_long", "k5m_cap", "d_gate", "noloss_min_profit_pct",
                      "wt_exit_tfs", "wt_exit_min_tfs", "wt_exit_velocity", "wt_vel_exit_threshold",
                      "min_hold_bars", "cooldown_bars", "position_size", "max_positions",
                      "sharpe", "total_pnl", "final_equity", "max_drawdown_pct", "total_trades",
                      "entries", "win_rate", "profit_factor", "avg_win_pct", "avg_loss_pct",
                      "avg_hold_bars", "wt_exits", "stoch_exits"])
        total = len(configs)
        skipped = 0
        t_start = time.time()
        for i, (name, cfg) in enumerate(configs):
            if name in done_configs:
                skipped += 1
                continue
            t0 = time.time()
            # Build entry masks
            entry_masks = {}
            for sym in reader.symbols:
                d = reader.data[sym]
                entry_masks[sym] = {}
                for side, il in [("LONG", True), ("SHORT", False)]:
                    entry_masks[sym][side], _ = mask_entry(d, n, il, cfg)
            # Get precomputed exits
            tf_key = tuple(sorted(cfg.get("wt_exit_tfs", ["4h", "D"])))
            exit_wt, exit_stoch = exit_cache[tf_key]
            # Run simulation
            trades, equity, total_pnl = simulate(reader, entry_masks, exit_wt, exit_stoch, cfg, si, ei)
            metrics = compute_metrics(trades, equity, total_pnl)
            elapsed = time.time() - t0
            # Write summary row
            sw.writerow([
                name, cfg.get("entry_score_min"), cfg.get("wt_ltf_min"), cfg.get("htf_min"),
                cfg.get("entry_filter_mode"), cfg.get("mfi_entry_max_long"), cfg.get("k5m_cap"),
                cfg.get("d_gate"), cfg.get("noloss_min_profit_pct"),
                "+".join(cfg.get("wt_exit_tfs", [])), cfg.get("wt_exit_min_tfs"),
                cfg.get("wt_exit_velocity", False), cfg.get("wt_vel_exit_threshold", 0),
                cfg.get("min_hold_bars"), cfg.get("cooldown_bars"),
                cfg.get("position_size"), cfg.get("max_positions"),
                metrics["sharpe"], metrics["total_pnl"], metrics["final_equity"],
                metrics["max_drawdown_pct"], metrics["total_trades"], metrics["entries"],
                metrics["win_rate"], metrics["profit_factor"], metrics["avg_win_pct"],
                metrics["avg_loss_pct"], metrics["avg_hold_bars"], metrics["wt_exits"], metrics["stoch_exits"]
            ])
            sf.flush()
            done_count = i + 1 - skipped
            if done_count % 10 == 0 or done_count <= 1:
                avg_s = (time.time() - t_start) / max(done_count, 1)
                remaining = total - i - 1
                eta_m = remaining * avg_s / 60
                log.info(f"[{done_count}/{total}] {name}: Sharpe={metrics['sharpe']:.3f} PnL=${metrics['total_pnl']:.0f} WR={metrics['win_rate']:.1f}% Trades={metrics['total_trades']} ({elapsed:.1f}s) ETA={eta_m:.0f}m")
    log.info(f"DONE. {total} configs ({skipped} skipped) in {(time.time()-t_start)/60:.1f}m")
    log.info(f"Summary: {summary_csv}")
    log.info(f"Trades:  {trades_csv}")
    # Print top 20 by Sharpe
    import csv as csv_mod
    with open(summary_csv) as f:
        rows = list(csv_mod.DictReader(f))
    rows.sort(key=lambda r: float(r.get("sharpe", 0)), reverse=True)
    log.info(f"\nTOP 20 BY SHARPE:")
    log.info(f"{'Rank':<5} {'Config':<60} {'Sharpe':>8} {'PnL':>10} {'WR%':>6} {'PF':>7} {'Trades':>7}")
    for i, r in enumerate(rows[:20]):
        log.info(f"{i+1:<5} {r['config_name']:<60} {float(r['sharpe']):>8.3f} {float(r['total_pnl']):>10.0f} {float(r['win_rate']):>6.1f} {float(r['profit_factor']):>7.3f} {int(r['total_trades']):>7}")

if __name__ == "__main__":
    main()
