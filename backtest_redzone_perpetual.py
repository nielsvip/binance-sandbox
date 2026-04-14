#!/usr/bin/env python3
"""
RED ZONE Perpetual Sweep — runs FOREVER, cycles through expanded config grid.

- Cycles through configs in deterministic order (so no two cycles overlap)
- Writes per-config result JSON immediately (resumable)
- Caches "done" configs in SQLite — skips on restart
- Catches per-config exceptions without dying
- Shell watchdog (run_redzone_sweep.sh) restarts this if it crashes
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from itertools import product
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--mode", choices=["crypto", "tradier"], required=True)
ap.add_argument("--account", required=True)
ap.add_argument("--start", required=True)
ap.add_argument("--capital", default="1000")
ap.add_argument("--symbols", required=True)
ap.add_argument("--per-config-timeout", type=int, default=7200)
ap.add_argument("--out-dir", default="backtest_v8/sweeps/redzone_perpetual")
ap.add_argument("--db", default="backtest_v8/sweeps/redzone_perpetual.sqlite")
# Worker sharding — allows N parallel workers, each handling a slice of configs.
# Each worker runs the same script with different --worker-id (0..total_workers-1).
ap.add_argument("--worker-id", type=int, default=0)
ap.add_argument("--total-workers", type=int, default=1)
args = ap.parse_args()
assert 0 <= args.worker_id < args.total_workers, "worker-id must be in [0, total_workers)"

OUT_DIR = Path(args.out_dir)
OUT_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(args.db)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

V8_CMD_BASE = [
    sys.executable, "backtest_v8_engine.py",
    "--mode", args.mode, "--account", args.account,
    "--start", args.start, "--capital", args.capital,
    "--symbols", args.symbols,
]

# Config grid — PHASE 3: NEW FEATURES ONLY (established params locked).
# Winners locked: WT_DC=75, ENTRY_ZONE=22, ALIGNMENT=6, DECAY=0.50
# Now test: divergence exits, zscore zones, two-phase exits, entry blocking.
# 864 configs — should complete in ~4-6 hours on 10 workers.
GRID = {
    # NEW FEATURES TO TEST (2026-04-11)
    "RZ_DIV_EXIT_ENABLED": [True, False],         # HTF divergence exit
    "RZ_ZSCORE_EXIT_ENABLED": [True, False],       # Zscore extreme exit
    "RZ_TWO_PHASE_EXIT_ENABLED": [True, False],    # Two-phase exit (sell the retest)
    "RZ_DIV_BLOCK_MIN": [2, 3, 999],              # Divergence entry block (999=disabled)
    "RZ_ZSCORE_ZONE_ENABLED": [True, False],       # Zscore zone detection
    # DELTA EXIT TUNING (narrow around winner 0.50)
    "DELTA_EXIT_DECAY_RATIO": [0.40, 0.50, 0.60],
    # RZ BASELINE (narrow around winners)
    "RZ_TOP_BB_THRESHOLD": [0.80, 0.85, 0.90],
    "RZ_BASELINE_TOL": [0.03, 0.05],
}
GRID_FIXED = {
    "RZ_ENTRY_ENABLED": True,
    "RZ_EXIT_ENABLED": True,
}


def gen_configs():
    keys = list(GRID.keys())
    for combo in product(*[GRID[k] for k in keys]):
        cfg = dict(zip(keys, combo))
        cfg.update(GRID_FIXED)
        # Skip invalid combos (only check keys that exist in grid)
        if "RZ_BOT_BB_THRESHOLD" in cfg and "RZ_TOP_BB_THRESHOLD" in cfg:
            if cfg["RZ_BOT_BB_THRESHOLD"] >= cfg["RZ_TOP_BB_THRESHOLD"]:
                continue
        if "RZ_K_EXIT" in cfg and "RZ_K_ENTRY_MAX" in cfg:
            if cfg["RZ_K_EXIT"] < cfg["RZ_K_ENTRY_MAX"]:
                continue
        # Build name from ALL swept params (works for any grid)
        name_parts = []
        for k, v in sorted(cfg.items()):
            if k == "name" or k in GRID_FIXED:
                continue
            if isinstance(v, float):
                name_parts.append(f"{k[:6]}{v:.0f}" if v == int(v) else f"{k[:6]}{v}")
            elif isinstance(v, bool):
                name_parts.append(f"{k[:6]}{'T' if v else 'F'}")
            else:
                name_parts.append(f"{k[:6]}{v}")
        cfg["name"] = "_".join(name_parts) if name_parts else "default"
        yield cfg


def write_override_file(cfg, path):
    """Write per-config V8_OVERRIDE_FILE JSON. Each worker uses its own file
    so parallel sweeps don't collide on a shared config.py."""
    # Pass ALL swept keys + fixed RZ keys to the override file
    OVERRIDE_KEYS = {
        # RZ params
        "RZ_ENTRY_ENABLED", "RZ_EXIT_ENABLED", "RZ_TOP_BB_THRESHOLD",
        "RZ_BOT_BB_THRESHOLD", "RZ_LEGS_MIN", "RZ_REQUIRE_STRUCT",
        "RZ_K_EXIT", "RZ_MFI_EXIT", "RZ_K_ENTRY_MAX", "RZ_BASELINE_TOL",
        "RZ_LTF_MICRO",
        # New feature toggles (2026-04-11)
        "RZ_DIV_EXIT_ENABLED", "RZ_ZSCORE_EXIT_ENABLED",
        "RZ_TWO_PHASE_EXIT_ENABLED", "RZ_DIV_BLOCK_MIN",
        "RZ_ZSCORE_ZONE_ENABLED",
        # Reentry
        "REENTRY_COOLDOWN_S", "TRADIER_REOPEN_WAIT_S",
        # TRADIER-specific knobs
        "TRADIER_MIN_HOLD_MINUTES",
        "WT_DC_ENTRY_THRESHOLD", "ENTRY_MIN_ALIGNMENT", "ENTRY_ZONE_LONG",
        "DELTA_EXIT_DECAY_RATIO", "DELTA_EXIT_MIN_TF_LOST",
    }
    overrides = {k: v for k, v in cfg.items() if k in OVERRIDE_KEYS}
    Path(path).write_text(json.dumps(overrides))


def parse_log(log_path):
    if not log_path.exists():
        return {}
    text = log_path.read_text()
    sig_baseline_long = text.count("BASELINE_BOUNCE_LONG")
    sig_baseline_short = text.count("BASELINE_BOUNCE_SHORT")
    sig_breakdown_truck = text.count("BREAKDOWN_TRUCK")
    sig_bottom_bounce = text.count("BOTTOM_HUGE_BOUNCE")
    sig_rejection_load = text.count("REJECTION_OLD_REDZONE")
    sig_top_rejection = text.count("TOP_REJECTION_SHORT")
    sig_red_zone_entry = text.count("RED_ZONE_ENTRY")
    wt_ltf_blocks = text.count("WT_LTF_GATE]")
    wt_gate_bypass = text.count("WT_GATE_BYPASS_RZ")
    # Crypto path logs [V8_TRADE], tradier path logs [V8_ETA] — count BOTH as actual trades
    v8_trades = text.count("V8_TRADE") + text.count("V8_ETA")
    v8_eta = text.count("V8_ETA")
    v8_open_quick = sum(1 for line in text.splitlines() if ("V8_TRADE" in line or "V8_ETA" in line) and "QUICK_OPEN" in line)
    v8_open_rz = sum(
        1
        for line in text.splitlines()
        if ("V8_TRADE" in line or "V8_ETA" in line)
        and ("RZ_" in line or "BASELINE_BOUNCE" in line or "BREAKDOWN_TRUCK" in line or "BOTTOM_HUGE" in line or "REJECTION_OLD" in line or "DELTA_ENTRY" in line)
    )
    v8_reduce = sum(1 for line in text.splitlines() if ("V8_TRADE" in line or "V8_ETA" in line) and "REDUCE" in line)
    v8_close = sum(1 for line in text.splitlines() if ("V8_TRADE" in line or "V8_ETA" in line) and "CLOSE" in line)
    return {
        "lines": text.count("\n"),
        "sig_baseline_long": sig_baseline_long,
        "sig_baseline_short": sig_baseline_short,
        "sig_breakdown_truck": sig_breakdown_truck,
        "sig_bottom_bounce": sig_bottom_bounce,
        "sig_rejection_load": sig_rejection_load,
        "sig_top_rejection": sig_top_rejection,
        "sig_red_zone_entry": sig_red_zone_entry,
        "sig_total_rz_entries": sig_baseline_long
        + sig_baseline_short
        + sig_breakdown_truck
        + sig_bottom_bounce
        + sig_rejection_load
        + sig_top_rejection,
        "wt_ltf_blocks": wt_ltf_blocks,
        "wt_gate_bypass_rz": wt_gate_bypass,
        "v8_open_quick": v8_open_quick,
        "v8_open_rz": v8_open_rz,
        "v8_reduce": v8_reduce,
        "v8_close": v8_close,
        "v8_trades": v8_trades,
        "v8_eta": v8_eta,
        "type_errors": text.count("TypeError"),
        "noloss_blocks": text.count("DEEP_LOSS_BLOCKED"),
        "done": "Done in" in text,
        "n_trades": int(m.group(1)) if (m := re.search(r"Done in [\d.]+s \| (\d+) trades", text)) else 0,
    }


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
    CREATE TABLE IF NOT EXISTS results (
        name TEXT PRIMARY KEY,
        cycle INT,
        mode TEXT,
        account TEXT,
        start_date TEXT,
        symbols TEXT,
        config_json TEXT,
        v8_trades INT,
        v8_open_quick INT,
        v8_open_rz INT,
        v8_reduce INT,
        v8_close INT,
        sig_total_rz_entries INT,
        wt_gate_bypass_rz INT,
        type_errors INT,
        elapsed_s REAL,
        finished_at REAL,
        result_json TEXT
    )
    """)
    con.commit()
    return con


def save_result(con, name, cycle, cfg, r, elapsed_s):
    con.execute(
        """INSERT OR REPLACE INTO results (name, cycle, mode, account, start_date, symbols, config_json, v8_trades, v8_open_quick, v8_open_rz, v8_reduce, v8_close, sig_total_rz_entries, wt_gate_bypass_rz, type_errors, elapsed_s, finished_at, result_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            name,
            cycle,
            args.mode,
            args.account,
            args.start,
            args.symbols,
            json.dumps({k: v for k, v in cfg.items() if k != "name"}, default=str),
            r.get("v8_trades", 0),
            r.get("v8_open_quick", 0),
            r.get("v8_open_rz", 0),
            r.get("v8_reduce", 0),
            r.get("v8_close", 0),
            r.get("sig_total_rz_entries", 0),
            r.get("wt_gate_bypass_rz", 0),
            r.get("type_errors", 0),
            elapsed_s,
            time.time(),
            json.dumps(r, default=str),
        ),
    )
    con.commit()


def main():
    print(f"PERPETUAL SWEEP: mode={args.mode} account={args.account} start={args.start}")
    print(f"DB: {DB_PATH}")
    print(f"OUT: {OUT_DIR}")
    print(f"WORKER: {args.worker_id}/{args.total_workers}")
    con = init_db()
    cycle = 0
    # Per-worker override file — isolates parallel workers
    override_file = OUT_DIR / f"override_w{args.worker_id}.json"
    try:
        while True:
            cycle += 1
            cycle_t0 = time.time()
            import random
            all_configs = list(gen_configs())
            random.seed(42 + cycle)  # deterministic per cycle but shuffled
            random.shuffle(all_configs)
            # Shard: this worker only handles configs where index % total_workers == worker_id
            configs = [c for i, c in enumerate(all_configs) if i % args.total_workers == args.worker_id]
            print(
                f"\n{'='*70}\nCYCLE {cycle} — worker {args.worker_id}/{args.total_workers} — {len(configs)} of {len(all_configs)} configs\n{'='*70}"
            )
            for i, cfg in enumerate(configs):
                name = cfg["name"]
                full_name = f"c{cycle}_{name}"
                log_file = OUT_DIR / f"{full_name}.log"
                t0 = time.time()
                print(f"\n[{i+1}/{len(configs)}] {full_name}", flush=True)
                try:
                    write_override_file(cfg, override_file)
                    sub_env = os.environ.copy()
                    sub_env["V8_OVERRIDE_FILE"] = str(override_file)
                    with open(log_file, "w") as lf:
                        proc = subprocess.run(
                            V8_CMD_BASE,
                            stdout=lf,
                            stderr=subprocess.STDOUT,
                            timeout=args.per_config_timeout,
                            env=sub_env,
                        )
                    elapsed = time.time() - t0
                    r = parse_log(log_file)
                    r["name"] = full_name
                    r["cycle"] = cycle
                    r["config"] = {k: v for k, v in cfg.items() if k != "name"}
                    r["elapsed_s"] = round(elapsed, 1)
                    save_result(con, full_name, cycle, cfg, r, elapsed)
                    with open(OUT_DIR / f"{full_name}_result.json", "w") as f:
                        json.dump(r, f, indent=2, default=str)
                    print(
                        f"  DONE: v8_trades={r.get('v8_trades', 0)} rz={r.get('v8_open_rz', 0)} bypass={r.get('wt_gate_bypass_rz', 0)} err={r.get('type_errors', 0)} t={elapsed:.0f}s",
                        flush=True,
                    )
                except subprocess.TimeoutExpired:
                    elapsed = time.time() - t0
                    r = parse_log(log_file)
                    r["error"] = "TIMEOUT"
                    r["elapsed_s"] = round(elapsed, 1)
                    save_result(con, full_name, cycle, cfg, r, elapsed)
                    print(f"  TIMEOUT after {elapsed:.0f}s", flush=True)
                except Exception as e:
                    elapsed = time.time() - t0
                    print(f"  ERROR: {type(e).__name__}: {e}", flush=True)
                    save_result(con, full_name, cycle, cfg, {"error": str(e)}, elapsed)
            cycle_elapsed = time.time() - cycle_t0
            print(f"\nCYCLE {cycle} COMPLETE in {cycle_elapsed/60:.1f}min — restarting cycle\n", flush=True)
    finally:
        con.close()
        print("\nDone.")


if __name__ == "__main__":
    main()
