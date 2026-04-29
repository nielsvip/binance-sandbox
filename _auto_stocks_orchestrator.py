#!/usr/bin/env python3
"""Autonomous stocks sweep orchestrator — runs until 2026-04-20 06:00 UTC.

Self-iterates:
  1. Queue: base sweeps on each sector (mix_12, tech_big, tech_growth, metals_miners, energy_oil, industrials_ag, financials_etfs, misc_industrial, all)
  2. After each sweep, parse top-10 → if best > current running_best, spawn neighborhood grid around winner
  3. Neighborhood sweeps take precedence over new sectors
  4. Heartbeat file every iteration
  5. Final report at stop time

Designed to run on S2 where _ab_sector_sweep.py and NPZ already live.
"""
import itertools
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

STOP_AT = datetime(2026, 4, 20, 6, 0, tzinfo=timezone.utc)
PY = "/home/niels/miniconda3/envs/binance_env/bin/python"
if not os.path.exists(PY):
    PY = sys.executable

RESULTS_DIR = BASE / "data" / "sweep_results"
HEARTBEAT = Path("/tmp/auto_stocks_heartbeat.json")
MASTER_LOG = Path("/tmp/auto_stocks_master.log")
STATE_FILE = Path("/tmp/auto_stocks_state.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(MASTER_LOG, mode='a'), logging.StreamHandler()],
)
log = logging.getLogger("auto")

BASE_SECTORS = [
    "mix_12",           # priority — diverse
    "tech_big",
    "metals_miners",
    "energy_oil",
    "financials_etfs",
    "tech_growth",
    "industrials_ag",
    "misc_industrial",
    "all",              # big one, last
]

# Knob ranges for neighborhood grids (centered ±2 steps around winner)
KNOB_NEIGHBORS = {
    "HTF_MIN_ALIGNED":          {1, 2, 3},
    "D_TREND_REQUIRED":         {True, False},
    "CT_WT_VELOCITY_1H_MIN":    {0.0, 1.0, 2.0, 4.0, 6.0, 8.0},
    "REENTRY_RALLY_K15M_MAX":   {30.0, 40.0, 50.0, 60.0, 80.0, 100.0},
    "ENTRY_ZONE_K_TF":          {"5m", "15m", "1h", "4h"},
    "ENTRY_ZONE_LONG":          {0.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0},
    "ENTRY_ZONE_SHORT":         {60.0, 65.0, 70.0, 75.0, 80.0, 85.0, 100.0},
    "STRENGTH_MIN_SCORE":       {2.0, 3.0, 4.0, 5.0, 7.0},
    "PROFIT_TARGET_ENABLED":    {True, False},
    "PROFIT_TARGET_PCT":        {0.3, 0.5, 0.75, 1.0, 1.5, 2.0},
    "MIN_HOLD_BARS":            {5, 10, 15, 20, 30, 40, 60},
}


def now_utc():
    return datetime.now(timezone.utc)


def remaining_hours():
    return (STOP_AT - now_utc()).total_seconds() / 3600.0


def heartbeat(status):
    HEARTBEAT.write_text(json.dumps({
        "updated": now_utc().isoformat(),
        "stop_at": STOP_AT.isoformat(),
        "hours_remaining": round(remaining_hours(), 2),
        "status": status,
    }, indent=2))


def load_state():
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text())
        except Exception: pass
    return {
        "running_best": {"sharpe": -999, "cfg": None, "sector": None, "trades": 0, "wr": 0},
        "completed_sweeps": [],      # list of {sector, grid_tag, best, path}
        "queue": [],                  # list of {sector, grid_tag, grid_overrides}
        "base_sectors_done": [],
        "started_at": now_utc().isoformat(),
    }


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def build_neighborhood_overrides(winner_cfg):
    """Generate ~300-500 configs around winner, three modes:
    1. Single-knob coord descent (original)
    2. Two-knob simultaneous perturbations (escape local max by exploring diagonals)
    3. Random samples from full knob space (injected diversity)
    """
    if not winner_cfg: return None
    import random
    overrides_list = []
    # 1. Single-knob perturbations (coord descent)
    for k, v in winner_cfg.items():
        if k not in KNOB_NEIGHBORS: continue
        for nv in KNOB_NEIGHBORS[k]:
            cfg = dict(winner_cfg)
            cfg[k] = nv
            overrides_list.append(cfg)
    # 2. Two-knob simultaneous perturbations — pick knob pairs, vary both
    knob_keys = [k for k in winner_cfg if k in KNOB_NEIGHBORS]
    for _ in range(150):
        k1, k2 = random.sample(knob_keys, 2) if len(knob_keys) >= 2 else (knob_keys[0], knob_keys[0])
        cfg = dict(winner_cfg)
        cfg[k1] = random.choice(list(KNOB_NEIGHBORS[k1]))
        cfg[k2] = random.choice(list(KNOB_NEIGHBORS[k2]))
        overrides_list.append(cfg)
    # 3. Random samples — inject full-space diversity (~80 random cfgs)
    for _ in range(80):
        cfg = dict(winner_cfg)
        for k in knob_keys:
            cfg[k] = random.choice(list(KNOB_NEIGHBORS[k]))
        overrides_list.append(cfg)
    # Deduplicate
    seen, unique = set(), []
    for cfg in overrides_list:
        key = tuple(sorted((k, v) for k, v in cfg.items()))
        if key not in seen:
            seen.add(key)
            unique.append(cfg)
    return unique[:500]  # cap


def run_sweep(sector, grid_tag="base", grid_overrides=None, workers=8):
    """Execute one sweep subprocess. Returns parsed result dict."""
    log.info(f"[SWEEP] sector={sector} grid={grid_tag} starting")
    heartbeat(f"sweep {sector} {grid_tag}")
    cmd = [PY, "-u", str(BASE / "_ab_sector_sweep.py"),
           "--sector", sector, "--workers", str(workers)]
    # Build subprocess env — DO NOT leak AUTO_GRID_OVERRIDES into unrelated sweeps.
    sub_env = os.environ.copy()
    sub_env.pop("AUTO_GRID_OVERRIDES", None)
    if grid_overrides:
        gpath = Path(f"/tmp/auto_grid_{sector}_{grid_tag}_{int(time.time())}.json")
        gpath.write_text(json.dumps(grid_overrides))
        sub_env["AUTO_GRID_OVERRIDES"] = str(gpath)
    log_path = Path(f"/tmp/auto_sweep_{sector}_{grid_tag}.log")
    t0 = time.time()
    try:
        with open(log_path, "w") as out:
            proc = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT, timeout=3600, cwd=str(BASE), env=sub_env)
    except subprocess.TimeoutExpired:
        log.warning(f"[SWEEP] {sector} {grid_tag} TIMEOUT after 3600s")
        return None
    except Exception as e:
        log.error(f"[SWEEP] {sector} {grid_tag} ERROR {e}")
        return None
    elapsed = time.time() - t0
    # Find the result JSON file (newest matching)
    results = sorted(RESULTS_DIR.glob(f"sector_sweep_{sector}_*.json"), key=lambda p: -p.stat().st_mtime)
    if not results:
        log.error(f"[SWEEP] {sector} {grid_tag} produced no result file")
        return None
    data = json.loads(results[0].read_text())
    log.info(f"[SWEEP] {sector} {grid_tag} DONE in {elapsed:.0f}s — best_sharpe={data.get('best', {}).get('sharpe', 0):.4f}")
    return {"sector": sector, "grid_tag": grid_tag, "path": str(results[0]),
            "best": data.get("best", {}), "baseline": data.get("baseline", {}),
            "elapsed": round(elapsed, 0), "n_results": len(data.get("results", []))}


def main():
    log.info(f"=== AUTONOMOUS STOCKS ORCHESTRATOR === stop_at={STOP_AT.isoformat()}")
    log.info(f"    remaining: {remaining_hours():.1f} hours")
    state = load_state()

    # Ingest existing recent result files — treat as already-done
    for sec in BASE_SECTORS:
        existing = sorted(RESULTS_DIR.glob(f"sector_sweep_{sec}_*.json"), key=lambda p: -p.stat().st_mtime)
        if existing and sec not in state["base_sectors_done"]:
            try:
                data = json.loads(existing[0].read_text())
                best = data.get("best", {}) or {}
                if best.get("sharpe", 0) > state["running_best"]["sharpe"] and best.get("trades", 0) >= 20:
                    state["running_best"] = {"sharpe": best.get("sharpe", 0), "cfg": best.get("cfg"),
                                             "sector": sec, "trades": best.get("trades", 0),
                                             "wr": best.get("wr", 0), "avg": best.get("avg", 0),
                                             "found_at": now_utc().isoformat()}
                state["base_sectors_done"].append(sec)
                state["completed_sweeps"].append({
                    "sector": sec, "grid_tag": "base_ingested",
                    "best_sharpe": best.get("sharpe", 0), "best_cfg": best.get("cfg"),
                    "best_trades": best.get("trades", 0),
                    "baseline_sharpe": (data.get("baseline", {}) or {}).get("sharpe", 0),
                    "path": str(existing[0]), "timestamp": now_utc().isoformat(),
                })
                log.info(f"[INGEST] {sec} existing result: best_sharpe={best.get('sharpe', 0):.4f}")
            except Exception as e:
                log.warning(f"[INGEST] {sec} failed: {e}")

    # Seed queue with base sectors not yet done
    if not state["queue"]:
        for sec in BASE_SECTORS:
            if sec not in state["base_sectors_done"]:
                state["queue"].append({"sector": sec, "grid_tag": "base", "overrides": None})
        # If we have a running best from ingest, also queue a neighborhood refinement
        if state["running_best"]["cfg"]:
            nbrs = build_neighborhood_overrides(state["running_best"]["cfg"])
            if nbrs:
                state["queue"].append({"sector": state["running_best"]["sector"] or "mix_12",
                                       "grid_tag": "refine_initial", "overrides": nbrs})
                log.info(f"[QUEUE] seeded initial {len(nbrs)}-config refinement around ingested best")

    iteration = 0
    while remaining_hours() > 0.1:
        iteration += 1
        if not state["queue"]:
            log.info("[QUEUE] empty — all known sweeps done. Seeding new round with diverse neighborhoods.")
            # Seed around: running_best + top-3 robustness leaders (different configs = different search paths)
            import math
            def _robust(r): return r.get("best_sharpe", 0) * (1.0 + 0.1 * math.log(max(r.get("best_trades", 0), 1)))  # per-trade sharpe weighted by log(n_trades) — sqrt(N) inflation stripped 2026-04-29 per CLAUDE.md rule 4
            # Get top configs by robustness, dedup
            cs = state["completed_sweeps"]
            seen_cfg_keys = set()
            seeds = []
            rb = state["running_best"]
            if rb.get("cfg"):
                seeds.append(("running_best", rb["sector"] or "mix_12", rb["cfg"]))
                seen_cfg_keys.add(tuple(sorted(rb["cfg"].items())))
            for r in sorted(cs, key=lambda x: -_robust(x))[:8]:
                cfg = r.get("best_cfg")
                if not cfg: continue
                k = tuple(sorted(cfg.items()))
                if k in seen_cfg_keys: continue
                seen_cfg_keys.add(k)
                seeds.append(("robust", r["sector"], cfg))
                if len(seeds) >= 4: break
            log.info(f"[QUEUE] seeding {len(seeds)} diverse exploration paths")
            for tag_prefix, sector, cfg in seeds:
                nbrs = build_neighborhood_overrides(cfg)
                if nbrs:
                    state["queue"].append({"sector": sector, "grid_tag": f"{tag_prefix}_r{iteration}",
                                           "overrides": nbrs})
            if not state["queue"]:
                if not state["running_best"].get("cfg"):
                    log.info("[QUEUE] no running best yet; sleeping 300s")
                    heartbeat("idle — no winner yet")
                    time.sleep(300)
                else:
                    log.info("[QUEUE] no neighborhoods to generate; sleeping 600s")
                    heartbeat("idle — waiting for time")
                    time.sleep(600)
                continue

        task = state["queue"].pop(0)
        save_state(state)
        log.info(f"[ITER {iteration}] running {task['sector']} {task['grid_tag']} (queue after: {len(state['queue'])})")

        result = run_sweep(task["sector"], task["grid_tag"], task.get("overrides"))
        if result is None:
            log.warning(f"[ITER {iteration}] sweep returned None, continuing")
            continue

        state["completed_sweeps"].append({
            "sector": task["sector"],
            "grid_tag": task["grid_tag"],
            "best_sharpe": result["best"].get("sharpe", 0),
            "best_cfg": result["best"].get("cfg"),
            "best_trades": result["best"].get("trades", 0),
            "baseline_sharpe": result["baseline"].get("sharpe", 0),
            "elapsed_s": result["elapsed"],
            "timestamp": now_utc().isoformat(),
            "path": result["path"],
        })
        if task["grid_tag"] == "base":
            state["base_sectors_done"].append(task["sector"])

        # Check if new running best
        best_sharpe = result["best"].get("sharpe", 0) or 0
        trades = result["best"].get("trades", 0) or 0
        if best_sharpe > state["running_best"]["sharpe"] and trades >= 20:
            log.info(f"[NEW BEST] {best_sharpe:.4f} (prev {state['running_best']['sharpe']:.4f}) on {task['sector']}")
            state["running_best"] = {
                "sharpe": best_sharpe,
                "cfg": result["best"].get("cfg"),
                "sector": task["sector"],
                "trades": trades,
                "wr": result["best"].get("wr", 0),
                "avg": result["best"].get("avg", result["best"].get("avg_pnl_pct", 0)),
                "found_at": now_utc().isoformat(),
            }
            # Spawn neighborhood refinement
            nbrs = build_neighborhood_overrides(result["best"]["cfg"])
            if nbrs:
                state["queue"].insert(0, {  # prepend — refinement takes precedence
                    "sector": task["sector"],
                    "grid_tag": f"refine_{iteration}",
                    "overrides": nbrs,
                })
                log.info(f"[QUEUE] prepended {len(nbrs)}-config refinement around new best")

        save_state(state)
        heartbeat(f"iter {iteration} done — best {state['running_best']['sharpe']:.4f}")

        # Light throttle between sweeps
        time.sleep(10)

    # Stop reached — final report
    log.info(f"=== STOP REACHED at {now_utc().isoformat()} ===")
    final = {
        "stopped_at": now_utc().isoformat(),
        "iterations": iteration,
        "running_best": state["running_best"],
        "top_5_all_sweeps": sorted(state["completed_sweeps"], key=lambda r: -r.get("best_sharpe", 0))[:5],
        "total_sweeps": len(state["completed_sweeps"]),
    }
    final_path = BASE / "data" / "sweep_results" / f"auto_stocks_final_{int(time.time())}.json"
    final_path.write_text(json.dumps(final, indent=2, default=str))
    log.info(f"Final report: {final_path}")
    heartbeat(f"DONE — final best {state['running_best']['sharpe']:.4f}")


if __name__ == "__main__":
    main()
