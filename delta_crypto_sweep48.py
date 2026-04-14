#!/usr/bin/env python3
"""
Focused V2 crypto sweep on ALL 48 symbols / 4 years.
Tests entry + exit combos systematically to find what generalizes.
Target: ~600 configs × 35s = ~5.8 hours
"""
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sweep_wt_dc_delta import backtest_symbol_v2, CRYPTO_NPZ
from wt_dc_delta import DEFAULT_CFG

logging.basicConfig(level=logging.INFO, format="%(asctime)s [48SYM] %(message)s")
logger = logging.getLogger("sweep48")

RESULTS_DIR = Path("data/delta_sweep")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Entry configs: FOCUSED — tz=1.5 and sm=5 are universal winners, only sweep what varies
ENTRY_COMBOS = []
for tw in [
    {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5},  # 3m dominant (ST)
    {"3m": 1.0, "15m": 2.0, "1h": 3.0, "4h": 2.0, "D": 1.0},  # 1h dominant (LT)
]:
    for mtf in [2, 3]:
        for ez in [1.0, 1.5, 2.0, 2.5]:
            for ea in [0.0, 0.3]:
                for htf in ["4h", "4h_D"]:
                    for cd in [10, 20, 60, 120]:
                        ENTRY_COMBOS.append({
                            "tf_weights": tw,
                            "entry_min_tf": mtf,
                            "entry_z_threshold": ez,
                            "entry_accel_threshold": ea,
                            "speed_smooth": 5,
                            "tf_z_threshold": 1.5,
                            "htf_gate": htf,
                            "cooldown_bars": cd,
                            "atr_entry_filter": 0,
                        })

# Exit configs: CUT — only proven types, 2 TFs, 2 holds
EXIT_COMBOS = []
for exit_type in ["speed_decay", "wt_cross", "combined_wt_speed", "giveback"]:
    for exit_tf in ["3m", "15m"]:
        for max_hold in [30, 120]:
            base = {"exit_tf": exit_tf, "max_hold_bars": max_hold,
                    "exit_speed_pct": 50, "giveback_pct": 50, "atr_trail_mult": 2.0}
            if exit_type == "speed_decay":
                for sp in [20, 50]:
                    EXIT_COMBOS.append({**base, "exit_type": exit_type, "exit_speed_pct": sp})
            elif exit_type == "giveback":
                for gb in [30, 50]:
                    EXIT_COMBOS.append({**base, "exit_type": exit_type, "giveback_pct": gb})
            elif exit_type == "combined_wt_speed":
                EXIT_COMBOS.append({**base, "exit_type": exit_type, "exit_speed_pct": 30})
            else:
                EXIT_COMBOS.append({**base, "exit_type": exit_type})

# Deduplicate exits
seen = set()
unique_exits = []
for e in EXIT_COMBOS:
    key = f"{e['exit_type']}_{e['exit_tf']}_{e['max_hold_bars']}_{e.get('exit_speed_pct',0)}_{e.get('giveback_pct',0)}"
    if key not in seen:
        seen.add(key)
        unique_exits.append(e)
EXIT_COMBOS = unique_exits

logger.info(f"Entry combos: {len(ENTRY_COMBOS)}, Exit combos: {len(EXIT_COMBOS)}")
logger.info(f"Full cross: {len(ENTRY_COMBOS) * len(EXIT_COMBOS)} — TOO MANY")

# PHASE 1: Find best entry using speed_decay sp=20 on 3m (proven best ST exit)
# Only 1 exit config means we test entries directly
PHASE1_EXIT = {"exit_type": "speed_decay", "exit_tf": "3m", "exit_speed_pct": 20,
               "max_hold_bars": 60, "giveback_pct": 50, "atr_trail_mult": 2.0}

logger.info(f"\n{'='*100}")
logger.info(f"PHASE 1: Find best ENTRY on 48 symbols ({len(ENTRY_COMBOS)} configs)")
logger.info(f"Fixed exit: speed_decay sp=20 on 3m, max_hold=60")
logger.info(f"Estimated time: {len(ENTRY_COMBOS) * 35 / 3600:.1f}h")
logger.info(f"{'='*100}")

npz_files = sorted(CRYPTO_NPZ.glob("*.npz"))
results_p1 = []
for ci, entry_cfg in enumerate(ENTRY_COMBOS):
    cfg = {**DEFAULT_CFG, **entry_cfg, **PHASE1_EXIT}
    htf = entry_cfg["htf_gate"]
    cd = entry_cfg["cooldown_bars"]
    tw_short = list(entry_cfg["tf_weights"].values())[:3]
    cfg_name = f"tw={tw_short}_mtf={entry_cfg['entry_min_tf']}_ez={entry_cfg['entry_z_threshold']}_ea={entry_cfg['entry_accel_threshold']}_tz={entry_cfg['tf_z_threshold']}_htf={htf}_cd={cd}"
    all_stats = []
    t0 = time.time()
    for npz_path in npz_files:
        stat = backtest_symbol_v2(npz_path, cfg)
        if stat and stat["trades"] > 0:
            all_stats.append(stat)
    elapsed = time.time() - t0
    valid = [s for s in all_stats if s["trades"] >= 3]
    if not valid:
        continue
    total_trades = sum(s["trades"] for s in all_stats)
    avg_sharpe = float(np.mean([s["sharpe"] for s in valid]))
    avg_wr = float(np.mean([s["wr"] for s in valid]))
    avg_ret = float(np.mean([s["avg_ret"] for s in valid]))
    n_prof = sum(1 for s in all_stats if s["sharpe"] > 0)
    avg_hold = float(np.mean([s.get("avg_hold", 0) for s in valid]))
    results_p1.append({
        "name": cfg_name, "config": entry_cfg,
        "sharpe": round(avg_sharpe, 4), "wr": round(avg_wr, 1),
        "avg_ret": round(avg_ret, 4), "trades": total_trades,
        "symbols": len(all_stats), "profitable": n_prof,
        "pct_profitable": round(n_prof / max(len(all_stats), 1) * 100, 1),
        "avg_hold": round(avg_hold, 1), "elapsed": round(elapsed, 1),
    })
    if (ci + 1) % 20 == 0 or ci == 0:
        logger.info(f"[{ci+1}/{len(ENTRY_COMBOS)}] {cfg_name[:70]} → S={avg_sharpe:.3f} WR={avg_wr:.1f}% tr={total_trades} prof={n_prof}/{len(all_stats)} ({elapsed:.1f}s)")

results_p1.sort(key=lambda x: -x["sharpe"])

# Save Phase 1
out_p1 = RESULTS_DIR / f"crypto48_phase1_entry_{int(time.time())}.json"
with open(out_p1, "w") as f:
    json.dump({"phase": 1, "n_configs": len(ENTRY_COMBOS), "n_symbols": len(npz_files),
               "results": results_p1}, f, indent=2, default=str)
logger.info(f"\nPhase 1 saved to {out_p1}")

# Print top 20 entries
logger.info(f"\n{'='*100}")
logger.info(f"TOP 20 ENTRIES — 48 crypto symbols, 4 years")
logger.info(f"{'#':>3} {'Sharpe':>7} {'WR':>5} {'AvgRet':>7} {'Trades':>7} {'Prof%':>6} {'Hold':>5} {'Name'}")
logger.info(f"{'-'*100}")
for i, r in enumerate(results_p1[:20], 1):
    logger.info(f"{i:>3} {r['sharpe']:>+6.3f} {r['wr']:>4.1f}% {r['avg_ret']:>+6.3f}% {r['trades']:>7} {r['pct_profitable']:>5.1f}% {r['avg_hold']:>5.0f} {r['name'][:55]}")
logger.info(f"{'='*100}")

# PHASE 2: Take top 8 entries, sweep all exits
TOP_N = 8
top_entries = results_p1[:TOP_N]
logger.info(f"\n{'='*100}")
logger.info(f"PHASE 2: Top {TOP_N} entries × {len(EXIT_COMBOS)} exits = {TOP_N * len(EXIT_COMBOS)} configs")
logger.info(f"Estimated time: {TOP_N * len(EXIT_COMBOS) * 35 / 3600:.1f}h")
logger.info(f"{'='*100}")

results_p2 = []
total_p2 = TOP_N * len(EXIT_COMBOS)
ci = 0
for entry_r in top_entries:
    entry_cfg = entry_r["config"]
    entry_name = entry_r["name"][:40]
    for exit_cfg in EXIT_COMBOS:
        cfg = {**DEFAULT_CFG, **entry_cfg, **exit_cfg}
        exit_name = f"{exit_cfg['exit_type']}_{exit_cfg['exit_tf']}_mh={exit_cfg['max_hold_bars']}"
        if exit_cfg["exit_type"] in ("speed_decay", "combined_wt_speed"):
            exit_name += f"_sp={exit_cfg['exit_speed_pct']}"
        elif exit_cfg["exit_type"] == "giveback":
            exit_name += f"_gb={exit_cfg['giveback_pct']}"
        full_name = f"{entry_name}|{exit_name}"
        all_stats = []
        t0 = time.time()
        for npz_path in npz_files:
            stat = backtest_symbol_v2(npz_path, cfg)
            if stat and stat["trades"] > 0:
                all_stats.append(stat)
        elapsed = time.time() - t0
        valid = [s for s in all_stats if s["trades"] >= 3]
        if not valid:
            ci += 1
            continue
        total_trades = sum(s["trades"] for s in all_stats)
        avg_sharpe = float(np.mean([s["sharpe"] for s in valid]))
        avg_atr_sharpe = float(np.mean([s.get("atr_sharpe", 0) for s in valid]))
        avg_wr = float(np.mean([s["wr"] for s in valid]))
        avg_ret = float(np.mean([s["avg_ret"] for s in valid]))
        n_prof = sum(1 for s in all_stats if s["sharpe"] > 0)
        avg_hold = float(np.mean([s.get("avg_hold", 0) for s in valid]))
        avg_gb = float(np.mean([s.get("avg_giveback", 0) for s in valid]))
        results_p2.append({
            "name": full_name,
            "entry_config": entry_cfg, "exit_config": exit_cfg,
            "sharpe": round(avg_sharpe, 4), "atr_sharpe": round(avg_atr_sharpe, 4),
            "wr": round(avg_wr, 1), "avg_ret": round(avg_ret, 4),
            "trades": total_trades, "symbols": len(all_stats),
            "profitable": n_prof,
            "pct_profitable": round(n_prof / max(len(all_stats), 1) * 100, 1),
            "avg_hold": round(avg_hold, 1), "avg_giveback": round(avg_gb, 3),
            "elapsed": round(elapsed, 1),
        })
        ci += 1
        if ci % 20 == 0 or ci == 1:
            logger.info(f"[{ci}/{total_p2}] {full_name[:80]} → S={avg_sharpe:.3f} WR={avg_wr:.1f}% tr={total_trades} hold={avg_hold:.0f} ({elapsed:.1f}s)")

results_p2.sort(key=lambda x: -x["sharpe"])

# Save Phase 2
out_p2 = RESULTS_DIR / f"crypto48_phase2_full_{int(time.time())}.json"
with open(out_p2, "w") as f:
    json.dump({"phase": 2, "n_entry": TOP_N, "n_exit": len(EXIT_COMBOS),
               "n_configs": total_p2, "n_symbols": len(npz_files),
               "results": results_p2}, f, indent=2, default=str)
logger.info(f"\nPhase 2 saved to {out_p2}")

# Final report
logger.info(f"\n{'='*120}")
logger.info(f"FINAL TOP 20 — CRYPTO 48 SYMBOLS / 4 YEARS — ENTRY + EXIT")
logger.info(f"{'#':>3} {'Sharpe':>7} {'atrS':>6} {'WR':>5} {'AvgRet':>7} {'Trades':>7} {'Prof%':>6} {'Hold':>5} {'GvBk':>5} {'Name'}")
logger.info(f"{'-'*120}")
for i, r in enumerate(results_p2[:20], 1):
    logger.info(f"{i:>3} {r['sharpe']:>+6.3f} {r['atr_sharpe']:>+5.3f} {r['wr']:>4.1f}% {r['avg_ret']:>+6.3f}% {r['trades']:>7} {r['pct_profitable']:>5.1f}% {r['avg_hold']:>5.0f} {r['avg_giveback']:>5.2f} {r['name'][:70]}")
logger.info(f"{'='*120}")
if results_p2:
    w = results_p2[0]
    logger.info(f"\nWINNER: {w['name']}")
    logger.info(f"  Entry: {json.dumps(w['entry_config'], default=str)}")
    logger.info(f"  Exit:  {json.dumps(w['exit_config'], default=str)}")
