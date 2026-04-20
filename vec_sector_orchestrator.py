#!/usr/bin/env python3
"""Long-running sector-sweep orchestrator.

Runs until --stop-utc (default 2026-04-20 12:30 UTC — 1hr before 13:30 market open).

Per iteration:
  1. For each sector in SECTORS, run vec_stock_mega pick=3 (2yr × 12 syms).
  2. After all sectors done, run vec_stock_wxw for each sector (validate top-100 on 128sym×2yr + combine top-20).
  3. Write per-iteration + cross-iteration audit markdown.
  4. Sleep to next iteration cycle.

Self-heals on errors. Heartbeat file every loop.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOGS = Path("/home/niels/logs") if Path("/home/niels/logs").exists() else (BASE / "logs")
LOGS.mkdir(exist_ok=True)

SECTORS = [
    "mix_12",
    "tech_big",
    "tech_growth",
    "metals_miners",
    "energy_oil",
    "industrials_ag",
    "financials_etfs",
    "misc_industrial",
]


def utcnow():
    return datetime.now(tz=timezone.utc)


def log(msg):
    line = f"[{utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    with open(LOGS / "sector_orchestrator.log", "a") as f:
        f.write(line + "\n")


def run_cmd(cmd, timeout=None, env=None):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"


def sector_db_path(sector, bars_label):
    d = Path("/home/niels/binance-sandbox/data/sweep_results") if Path("/home/niels/binance-sandbox/data/sweep_results").exists() else (BASE / "data" / "sweep_results")
    d.mkdir(parents=True, exist_ok=True)
    return d / f"vec_sector_{sector}_{bars_label}.sqlite"


def sweep_sector(sector, bars, pick, workers, time_budget_s, python_bin):
    db = sector_db_path(sector, f"p{pick}_b{bars}")
    # Drop stale tables so schema stays clean
    if db.exists():
        try:
            c = sqlite3.connect(str(db))
            c.execute("DROP TABLE IF EXISTS validated_full")
            c.execute("DROP TABLE IF EXISTS combined_results")
            c.commit()
            c.close()
        except Exception:
            pass
    cmd = (f"{python_bin} -u /home/niels/binance-sandbox/vec_stock_mega.py "
           f"--sector {sector} --bars {bars} --pick {pick} --workers {workers} "
           f"--time-budget-s {time_budget_s} --floor-robust 0.1 --elite-robust 1.0 "
           f"--out-db {db}")
    log(f"  [{sector}] RUN: {cmd}")
    rc, out, err = run_cmd(cmd, timeout=time_budget_s + 120)
    tail = out.split("\n")[-60:] if out else []
    if rc != 0:
        log(f"  [{sector}] FAIL rc={rc} err={err[-200:] if err else 'none'}")
    else:
        log(f"  [{sector}] OK rows-DB={db.name}")
        for line in tail[-15:]:
            if line.strip():
                log(f"    {line[:200]}")
    return db, rc == 0


def validate_and_combine(sector_db, bars, python_bin):
    cmd = (f"{python_bin} -u /home/niels/binance-sandbox/vec_stock_wxw.py "
           f"--src-db {sector_db} --mode both --top-n 100 --combine-k 20 "
           f"--bars {bars} --min-bars-per-sym 35000 --max-syms 128 "
           f"--min-trades-per-sym 50 --min-syms 40")
    log(f"  WxW: {sector_db.name}")
    rc, out, err = run_cmd(cmd, timeout=900)
    if rc != 0:
        log(f"  WxW FAIL rc={rc} err={err[-200:] if err else 'none'}")
    else:
        log(f"  WxW OK")
        # Show tail
        for line in out.split("\n")[-25:]:
            if line.strip():
                log(f"    {line[:200]}")
    return rc == 0


def audit_snapshot(iteration, bars, pick):
    """Collect top-N per sector, cross-sector top, write markdown."""
    out_md = BASE / "data" / "sweep_results" / f"sector_audit_iter{iteration}.md"
    lines = [f"# Sector Audit — Iter {iteration} @ {utcnow().isoformat()}", ""]
    global_best = []
    for sec in SECTORS:
        db = sector_db_path(sec, f"p{pick}_b{bars}")
        if not db.exists():
            lines.append(f"## {sec} — NO DB YET"); lines.append("")
            continue
        try:
            c = sqlite3.connect(str(db))
            sample = c.execute("SELECT sharpe_robust, sharpe_avg, sharpe_pool, syms_included, n_trades_total, wr_avg, side, combo FROM results ORDER BY sharpe_robust DESC LIMIT 5").fetchall()
            full = c.execute("SELECT full_sharpe_robust, full_sharpe_avg, full_sharpe_pool, full_syms, full_trades, full_wr, dropoff_pct, side, combo FROM validated_full ORDER BY full_sharpe_robust DESC LIMIT 5").fetchall() if _table_exists(c, "validated_full") else []
            merged = c.execute("SELECT full_sharpe_robust, full_sharpe_avg, full_sharpe_pool, full_syms, full_trades, full_wr, side, combo_merged FROM combined_results ORDER BY full_sharpe_robust DESC LIMIT 5").fetchall() if _table_exists(c, "combined_results") else []
            c.close()
        except Exception as e:
            lines.append(f"## {sec} — DB ERROR {e}"); lines.append(""); continue
        lines.append(f"## {sec}")
        lines.append(f"### Sample (2yr × 12 sector syms) top-5 by robust — REAL exits (technical)")
        lines.append("| Robust | Avg | Pool | Syms | Trades | WR | Side | Combo |")
        lines.append("|--|--|--|--|--|--|--|--|")
        for r in sample:
            lines.append(f"| {r[0]:.3f} | {r[1]:.3f} | {r[2]:.3f} | {r[3]} | {r[4]} | {r[5]:.1f}% | {r[6]} | {r[7]} |")
        if full:
            lines.append(f"### Validated full (128 syms × 2yr) top-5 by robust")
            lines.append("| Robust | Avg | Pool | Syms | Trades | WR | Drop% | Side | Combo |")
            lines.append("|--|--|--|--|--|--|--|--|--|")
            for r in full:
                lines.append(f"| {r[0]:.3f} | {r[1]:.3f} | {r[2]:.3f} | {r[3]} | {r[4]} | {r[5]:.1f}% | {r[6]:.0f}% | {r[7]} | {r[8]} |")
                global_best.append(("validated_full", sec, r))
        if merged:
            lines.append(f"### Winners-with-Winners merged top-5 by robust")
            lines.append("| Robust | Avg | Pool | Syms | Trades | WR | Side | Merged |")
            lines.append("|--|--|--|--|--|--|--|--|")
            for r in merged:
                lines.append(f"| {r[0]:.3f} | {r[1]:.3f} | {r[2]:.3f} | {r[3]} | {r[4]} | {r[5]:.1f}% | {r[6]} | {r[7]} |")
                global_best.append(("combined", sec, r))
        lines.append("")

    # Global ranking
    lines.append("## GLOBAL TOP 20 (across sectors, all sources, by robust)")
    lines.append("| Source | Sector | Robust | Avg | Pool | Syms | N | WR | Side | Combo |")
    lines.append("|--|--|--|--|--|--|--|--|--|--|")
    global_best.sort(key=lambda x: x[2][0], reverse=True)
    for src, sec, r in global_best[:20]:
        if src == "validated_full":
            lines.append(f"| val | {sec} | {r[0]:.3f} | {r[1]:.3f} | {r[2]:.3f} | {r[3]} | {r[4]} | {r[5]:.1f}% | {r[7]} | {r[8]} |")
        else:
            lines.append(f"| merge | {sec} | {r[0]:.3f} | {r[1]:.3f} | {r[2]:.3f} | {r[3]} | {r[4]} | {r[5]:.1f}% | {r[6]} | {r[7]} |")

    with open(out_md, "w") as f:
        f.write("\n".join(lines))
    log(f"AUDIT written: {out_md}")
    return out_md


def _table_exists(conn, name):
    r = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(r)


def heartbeat(state):
    hb = BASE / "data" / "sweep_results" / "sector_orchestrator_heartbeat.json"
    hb.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utcnow().isoformat()
    with open(hb, "w") as f:
        json.dump(state, f, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stop-utc", default="2026-04-20T12:30:00Z", help="hard stop")
    ap.add_argument("--bars", type=int, default=40000, help="~2yr of 5m bars")
    ap.add_argument("--pick", type=int, default=3)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--per-sector-budget", type=int, default=900, help="seconds per sector sweep")
    ap.add_argument("--python-bin", default="/home/niels/miniconda3/envs/binance_env/bin/python")
    ap.add_argument("--skip-sweep", action="store_true", help="skip sector sweep, only validate + audit")
    args = ap.parse_args()

    try:
        stop_at = datetime.fromisoformat(args.stop_utc.replace("Z", "+00:00"))
    except Exception:
        stop_at = utcnow().replace(hour=12, minute=30, second=0, microsecond=0)
    log(f"Orchestrator start. Stop at {stop_at.isoformat()}. Sectors: {SECTORS}")

    iteration = 0
    state = {"start": utcnow().isoformat(), "stop_at": stop_at.isoformat(), "iterations": []}

    while utcnow() < stop_at:
        iteration += 1
        iter_start = utcnow()
        log(f"--- ITERATION {iteration} @ {iter_start.isoformat()} ---")
        state["current_iter"] = iteration
        state["iter_start"] = iter_start.isoformat()
        heartbeat(state)

        # Per-sector sweep
        if not args.skip_sweep:
            for sec in SECTORS:
                if utcnow() >= stop_at:
                    log("STOP reached during sweep"); break
                sweep_sector(sec, args.bars, args.pick, args.workers, args.per_sector_budget, args.python_bin)
                state[f"iter{iteration}_{sec}_sweep"] = "done"
                heartbeat(state)

        # Validate + combine per sector
        for sec in SECTORS:
            if utcnow() >= stop_at:
                log("STOP reached during WxW"); break
            db = sector_db_path(sec, f"p{args.pick}_b{args.bars}")
            if db.exists():
                validate_and_combine(db, args.bars, args.python_bin)
                state[f"iter{iteration}_{sec}_wxw"] = "done"
                heartbeat(state)

        # Audit snapshot
        md = audit_snapshot(iteration, args.bars, args.pick)
        state["iterations"].append({
            "iter": iteration,
            "start": iter_start.isoformat(),
            "end": utcnow().isoformat(),
            "audit_md": str(md),
        })
        heartbeat(state)

        # Small cooldown between iterations to avoid tight-loop if sweeps all fast
        cooldown = 30
        remaining_to_stop = (stop_at - utcnow()).total_seconds()
        if remaining_to_stop < cooldown:
            log("Insufficient time for another iteration — exiting")
            break
        time.sleep(cooldown)

    log(f"Orchestrator END. Completed {iteration} iterations.")
    heartbeat(state)


if __name__ == "__main__":
    main()
