#!/usr/bin/env python3
"""
save_snapshot.py — create an immutable local snapshot of all relevant files (2026-04-21).

Usage:
  python3 save_snapshot.py --name best_ppl_wrong_side \
    --overrides data/orchestrator/ablation_crypto_20260421/movers.csv \
    --results data/orchestrator/crypto_20260421/stage2.csv \
    --summary "WRONG_SIDE 3/5+1div+15min age, Sharpe 0.82, gain 186%, DD 1.52%"

Creates snapshots_local/{name}_{timestamp}/ with:
  - All relevant .py files (config, ez_*, tradier_*, v8_*, backtest_v8_*, wt_*, utils, symbols.json)
  - SNAPSHOT_INFO.md with summary + overrides + results
  - All files set to read-only via chmod -w

Also offers --rsync to push to S1/S2 /home/niels/binance-sandbox/snapshots/.
"""
import argparse
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path


RELEVANT_FILES = [
    # Configs
    "config.py", "config_tradier.py",
    # Live code
    "ez_manage.py", "ez_positions.py", "ez_positions_quick.py", "ez_positions_service.py",
    "ez_indicators.py", "ez_klines.py", "ez_market_data.py", "ez_rankings.py",
    "tradier_manage.py", "tradier_api.py", "tradier_indicators.py", "tradier_positions.py",
    "tradier_prices.py", "tradier_rankings.py",
    # Backtest
    "v8_quick_engine.py", "v8_quick_sweep.py",
    "backtest_v8_engine.py", "backtest_v8_harness.py", "backtest_v8_precompute.py",
    # Signals
    "wt_dc_delta.py", "wt_dc_delta_engine.py", "wt_dc_entry_scorer.py", "wt_dc_exit_scorer.py",
    "breakout_multi_lung.py",
    # Utilities
    "utils.py", "symbols.json",
    # Orchestrator + ablation
    "ppl_orchestrator.py", "ppl_ablation.py",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="snapshot name (e.g. best_wrong_side_20260421)")
    ap.add_argument("--summary", default="", help="one-line summary for SNAPSHOT_INFO.md")
    ap.add_argument("--overrides", default="", help="path to override JSON / CSV to include")
    ap.add_argument("--results", default="", help="path to results CSV to include")
    ap.add_argument("--src-dir", default="/Users/niels/Documents/binance")
    ap.add_argument("--out-base", default="/Users/niels/Documents/binance/snapshots_local")
    ap.add_argument("--rsync-s1", action="store_true")
    ap.add_argument("--rsync-s2", action="store_true")
    ap.add_argument("--no-readonly", action="store_true", help="skip chmod -w (for testing)")
    args = ap.parse_args()

    ts = time.strftime("%Y%m%d_%H%M%S")
    snap_name = f"{args.name}_{ts}"
    out_dir = Path(args.out_base) / snap_name
    out_dir.mkdir(parents=True, exist_ok=False)
    src = Path(args.src_dir)

    copied = []
    missing = []
    for fname in RELEVANT_FILES:
        src_path = src / fname
        if src_path.exists():
            shutil.copy2(src_path, out_dir / fname)
            copied.append(fname)
        else:
            missing.append(fname)

    # Copy extra artifacts
    extras = []
    if args.overrides:
        ov = Path(args.overrides)
        if ov.exists():
            shutil.copy2(ov, out_dir / f"overrides_{ov.name}")
            extras.append(ov.name)
    if args.results:
        rp = Path(args.results)
        if rp.exists():
            shutil.copy2(rp, out_dir / f"results_{rp.name}")
            extras.append(rp.name)

    # Write SNAPSHOT_INFO.md
    info = [
        f"# Snapshot: {snap_name}",
        "",
        f"**Created:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}  ",
        f"**Source:** {src}",
        "",
        "## Summary",
        args.summary or "(no summary provided)",
        "",
        "## Files snapshotted",
    ]
    for f in copied:
        info.append(f"- {f}")
    if missing:
        info.append("\n## Missing (not in source)")
        for f in missing: info.append(f"- {f}")
    if extras:
        info.append("\n## Extras")
        for f in extras: info.append(f"- {f}")
    info.append("\n## Restore")
    info.append("To roll back to this snapshot (source of truth = MacBook):")
    info.append("```bash")
    for f in copied:
        info.append(f"cp {out_dir/f} {src/f}")
    info.append("```")
    info.append("")
    info.append("## Rsync targets")
    info.append("S1: niels@157.180.125.52:/home/niels/binance-sandbox/snapshots/")
    info.append("S2: niels@204.168.181.211:/home/niels/binance-sandbox/snapshots/")
    info.append("")
    info.append("**DO NOT EDIT FILES IN THIS DIRECTORY — IT IS AN IMMUTABLE SNAPSHOT.**")

    (out_dir / "SNAPSHOT_INFO.md").write_text("\n".join(info))
    print(f"[SNAPSHOT] saved {len(copied)} files to {out_dir}")
    if missing:
        print(f"[SNAPSHOT] missing {len(missing)}: {missing[:5]}...")

    # chmod -w to make read-only (user + group + other read-only)
    if not args.no_readonly:
        for f in out_dir.iterdir():
            if f.is_file():
                os.chmod(f, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        # Also lock the SNAPSHOT_INFO
        print(f"[SNAPSHOT] set read-only on {len(list(out_dir.iterdir()))} files")

    # Rsync to servers
    if args.rsync_s1:
        dst = f"niels@157.180.125.52:/home/niels/binance-sandbox/snapshots/{snap_name}/"
        print(f"[SNAPSHOT] rsync to S1 {dst}")
        subprocess.run(["rsync", "-az", f"{out_dir}/", dst], check=False)
    if args.rsync_s2:
        dst = f"niels@204.168.181.211:/home/niels/binance-sandbox/snapshots/{snap_name}/"
        print(f"[SNAPSHOT] rsync to S2 {dst}")
        subprocess.run(["rsync", "-az", f"{out_dir}/", dst], check=False)

    print(f"[SNAPSHOT] DONE: {out_dir}")


if __name__ == "__main__":
    main()
