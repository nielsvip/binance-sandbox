#!/usr/bin/env python3
"""scripts/zec_4yr_daily_baseline — daily ZEC-only 4-year baseline sweep.

Runs at 13:00 UTC (09:00 ET) via cron on S1. Calls zec_settings_search.py for
N iterations, reads the winning candidate, snapshots it to
data/hourly_reconfig/flz/zec_4yr_baseline_<YYYYMMDD>.json, and AUTO-PROMOTES
to data/hourly_reconfig/flz/active_config_7d.json[ZECUSDC].overrides per
USER 2026-05-21 explicit override of CLAUDE.md IMPOSTER BLOCK §1.

Sample-floor caveat: ZECUSDC is a single symbol; per CLAUDE.md this would be
sub-floor. USER override is logged in _meta.

Usage:
  python3 scripts/zec_4yr_daily_baseline.py [--iters 100] [--dry-run]
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZEC_SEARCH = ROOT / "zec_settings_search.py"
CAND = ROOT / "data" / "hourly_reconfig" / "_candidates" / "zec_ZECUSDC_top.json"
ACTIVE_7D = ROOT / "data" / "hourly_reconfig" / "flz" / "active_config_7d.json"
BASELINE_DIR = ROOT / "data" / "hourly_reconfig" / "flz"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=100, help="Number of search iterations")
    ap.add_argument("--dry-run", action="store_true", help="Run search but do not promote")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    print(f"[zec_4yr_baseline] day={today} iters={args.iters} dry_run={args.dry_run}")

    # Snapshot current candidate (we want this run's winner, not historical)
    if CAND.exists():
        bak = CAND.with_suffix(f".json.bak_{today}")
        shutil.copy2(CAND, bak)
        print(f"[snapshot] prev candidate → {bak.name}")

    # Run search
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, str(ZEC_SEARCH), "--once", "--max-iters", str(args.iters)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    print(f"[search] elapsed={time.time() - t0:.1f}s exit={proc.returncode}")
    if proc.stdout:
        for line in proc.stdout.splitlines()[-5:]:
            print(f"  > {line}")
    if proc.returncode != 0:
        print(f"[search] STDERR: {proc.stderr[:500]}", file=sys.stderr)
        return 1

    if not CAND.exists():
        print("[promote] no candidate file produced — nothing to promote", file=sys.stderr)
        return 1

    try:
        winner = json.loads(CAND.read_text())
    except Exception as e:
        print(f"[promote] failed reading candidate: {e}", file=sys.stderr)
        return 1

    sharpe = float(winner.get("_sharpe", 0) or 0)
    trades = int(winner.get("_trades", 0) or 0)
    pnl = float(winner.get("_pnl_usd", 0) or 0)
    wr = float(winner.get("_wr", 0) or 0)

    print(f"[winner] sharpe={sharpe:+.3f} trades={trades} pnl=${pnl:.0f} wr={wr:.2f}")

    if sharpe < 0.5 or trades < 5:
        print(f"[promote] REFUSED: sharpe<0.5 OR trades<5 — winner does not clear floor")
        return 1

    # Snapshot to dated baseline file
    snap_path = BASELINE_DIR / f"zec_4yr_baseline_{today}.json"
    snap_path.write_text(json.dumps(winner, indent=2))
    print(f"[snapshot] {snap_path.name}")

    if args.dry_run:
        print("[promote] DRY-RUN — skipping active_config_7d.json update")
        return 0

    # Merge into active_config_7d.json[ZECUSDC].overrides
    if not ACTIVE_7D.exists():
        print(f"[promote] {ACTIVE_7D} missing — refusing to create from scratch", file=sys.stderr)
        return 1
    bak = ACTIVE_7D.with_name(ACTIVE_7D.name + f".bak_{today}_{datetime.now().strftime('%H%M%S')}")
    shutil.copy2(ACTIVE_7D, bak)

    active = json.loads(ACTIVE_7D.read_text())
    zec = active.setdefault("ZECUSDC", {})
    overrides = zec.setdefault("overrides", {})

    knob_keys = [k for k in winner.keys() if not k.startswith("_")]
    n_changed = 0
    for k in knob_keys:
        if overrides.get(k) != winner[k]:
            overrides[k] = winner[k]
            n_changed += 1
    zec["pool_sharpe"] = sharpe
    zec["trades_7d"] = trades
    zec["wr_pct"] = wr * 100.0 if wr <= 1.0 else wr
    zec["promote"] = True
    zec["winning_tag"] = f"zec_4yr_daily_{today}"
    zec["updated_at"] = datetime.now(timezone.utc).isoformat()
    zec["_meta_promotion"] = (
        f"zec_4yr_daily_baseline_{today} | USER OVERRIDE 2026-05-21 — single-sym promotion "
        f"approved (CLAUDE.md IMPOSTER BLOCK §1 lifted for ZEC only). "
        f"sharpe={sharpe:.3f} trades={trades} pnl=${pnl:.0f}"
    )

    tmp = ACTIVE_7D.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(active, indent=2))
    os.replace(tmp, ACTIVE_7D)
    print(f"[promote] active_config_7d.json updated: {n_changed} overrides changed, backup={bak.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
