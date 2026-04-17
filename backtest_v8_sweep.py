#!/usr/bin/env python3
"""
backtest_v8_sweep.py — Ablation sweep harness for backtest_v8_engine.

Each config variant runs as an isolated subprocess. Override values are written
to a JSON file and passed via the V8_OVERRIDE_FILE env var, which backtest_v8_engine
patches at 4 levels (module attr, Config class, dataclass defaults, live instances)
BEFORE importing ez_manage — so the real hedge/reentry code reads the overridden values.

Unlike v8_quick_sweep (vectorized, no hedge simulation), this harness runs the REAL
engine with full HedgeEngine execution, so hedge switches actually move numbers.

Results: one V8_RESULT line per variant captured from stdout; accumulated into a CSV
written incrementally so interruptions don't lose work.

Usage examples:
  # Test all hedge switches one-by-one (baseline + single-flip ablation)
  python3 backtest_v8_sweep.py --mode crypto --account ang \\
      --start 2026-01-01 --symbols BTCUSDT,ETHUSDT,SOLUSDT \\
      --tier hedge_one_by_one --workers 4

  # Full reentry-overhaul ablation
  python3 backtest_v8_sweep.py --mode crypto --account ang \\
      --start 2026-01-01 --symbols BTCUSDT,ETHUSDT,LINKUSDT,DOTUSDT \\
      --tier reentry_one_by_one --workers 4

  # Combined ablation
  python3 backtest_v8_sweep.py --mode crypto --account ang \\
      --start 2026-01-01 --tier hedge_reentry_ablation --workers 8

Tiers:
  hedge_one_by_one       — flip each hedge switch alone vs baseline (6 variants)
  reentry_one_by_one     — flip each reentry switch alone (10 variants)
  hedge_reentry_ablation — union of both (16 variants)
  hedge_full             — cartesian over hedge switches (48 variants)

Output CSV: data/sweep_results/backtest_v8_sweep_<tier>_<timestamp>.csv
"""
import argparse
import csv
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent
ENGINE_PATH = BASE_PATH / "backtest_v8_engine.py"
OVERRIDE_DIR = BASE_PATH / "data" / "sweep_overrides"
RESULTS_DIR = BASE_PATH / "data" / "sweep_results"
OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

PY_BIN = sys.executable
V8_RESULT_RE = re.compile(
    r"V8_RESULT:\s*"
    r"sharpe_w=(?P<sharpe_w>[-\d.]+)\s+"
    r"sharpe_pt=(?P<sharpe_pt>[-\d.]+)\s+"
    r"sharpe_ann=(?P<sharpe_ann>[-\d.]+)\s+"
    r"gain_pct=(?P<gain_pct>[-+\d.]+)\s+"
    r"closes=(?P<closes>\d+)\s+"
    r"wins=(?P<wins>\d+)\s+"
    r"losses=(?P<losses>\d+)"
)


# ══════════════════════════════════════════════════════════════════════════════
# ABLATION GRIDS
# Each grid is a list of (label, overrides_dict). Baseline has empty dict → all
# defaults. Each subsequent entry flips exactly ONE switch from default.
# ══════════════════════════════════════════════════════════════════════════════

def grid_hedge_one_by_one():
    return [
        ("baseline", {}),
        ("HEDGE_EXIT_BYPASS_NOLOSS_OFF", {"HEDGE_EXIT_BYPASS_NOLOSS": False}),
        ("HEDGE_CLOSE_REMOVE_FROM_TRADEABLE_OFF", {"HEDGE_CLOSE_REMOVE_FROM_TRADEABLE": False}),
        ("HEDGE_SAME_SYMBOL_PCT_0.5", {"HEDGE_SAME_SYMBOL_PCT": 0.5}),
        ("HEDGE_SAME_SYMBOL_PCT_1.5", {"HEDGE_SAME_SYMBOL_PCT": 1.5}),
        ("HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE_OFF", {"HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE": False}),
    ]


def grid_reentry_one_by_one():
    return [
        ("baseline", {}),
        ("REENTRY_WT15M_CROSS_OFF", {"REENTRY_WT15M_CROSS_ENABLED": False}),
        ("REENTRY_WT15M_SIZE_1.0", {"REENTRY_WT15M_SIZE_MULT": 1.0}),
        ("REENTRY_WT15M_SIZE_2.0", {"REENTRY_WT15M_SIZE_MULT": 2.0}),
        ("REENTRY_WT15M_HTF_NOT_REQUIRED", {"REENTRY_WT15M_HTF_FAVOR_REQUIRED": False}),
        ("REENTRY_K15M_PARTIAL_OFF", {"REENTRY_K15M_PARTIAL_ENABLED": False}),
        ("REENTRY_K15M_THRESHOLD_80", {"REENTRY_K15M_PARTIAL_THRESHOLD": 80.0}),
        ("REENTRY_K15M_PARTIAL_MULT_0.3", {"REENTRY_K15M_PARTIAL_MULT": 0.3}),
        ("REENTRY_POST_CONSOL_OFF", {"REENTRY_POST_CONSOL_ENABLED": False}),
        ("REENTRY_POST_CONSOL_MULT_2.0", {"REENTRY_POST_CONSOL_MULT": 2.0}),
    ]


def grid_hedge_reentry_ablation():
    return grid_hedge_one_by_one() + grid_reentry_one_by_one()[1:]  # drop dup baseline


def grid_reentry_wide():
    """Extended reentry ablation — new overhaul switches at multiple values +
    all existing REENTRY2_* + REENTRY_B* block toggles. Use with 10-12 symbols
    over 2+ months for statistical power. User priority 2026-04-17: make
    reentries fire again."""
    return [
        ("baseline", {}),
        # ── NEW OVERHAUL SWITCHES (C/D/E) — multiple values each ──
        ("WT15M_CROSS_OFF", {"REENTRY_WT15M_CROSS_ENABLED": False}),
        ("WT15M_SIZE_1.0", {"REENTRY_WT15M_SIZE_MULT": 1.0}),
        ("WT15M_SIZE_1.3", {"REENTRY_WT15M_SIZE_MULT": 1.3}),
        ("WT15M_SIZE_2.0", {"REENTRY_WT15M_SIZE_MULT": 2.0}),
        ("WT15M_K_MAX_30", {"REENTRY_WT15M_K_MAX": 30.0}),
        ("WT15M_K_MAX_70", {"REENTRY_WT15M_K_MAX": 70.0}),
        ("WT15M_K_MAX_100", {"REENTRY_WT15M_K_MAX": 100.0}),
        ("WT15M_HTF_NOT_REQUIRED", {"REENTRY_WT15M_HTF_FAVOR_REQUIRED": False}),
        ("K15M_PARTIAL_OFF", {"REENTRY_K15M_PARTIAL_ENABLED": False}),
        ("K15M_THRESHOLD_70", {"REENTRY_K15M_PARTIAL_THRESHOLD": 70.0}),
        ("K15M_THRESHOLD_80", {"REENTRY_K15M_PARTIAL_THRESHOLD": 80.0}),
        ("K15M_THRESHOLD_100", {"REENTRY_K15M_PARTIAL_THRESHOLD": 100.0}),
        ("K15M_MULT_0.3", {"REENTRY_K15M_PARTIAL_MULT": 0.3}),
        ("K15M_MULT_0.7", {"REENTRY_K15M_PARTIAL_MULT": 0.7}),
        ("POST_CONSOL_OFF", {"REENTRY_POST_CONSOL_ENABLED": False}),
        ("POST_CONSOL_MULT_1.3", {"REENTRY_POST_CONSOL_MULT": 1.3}),
        ("POST_CONSOL_MULT_2.0", {"REENTRY_POST_CONSOL_MULT": 2.0}),
        ("POST_CONSOL_TFS_1", {"REENTRY_POST_CONSOL_TFS_REQUIRED": 1}),
        ("POST_CONSOL_TFS_3", {"REENTRY_POST_CONSOL_TFS_REQUIRED": 3}),
        # ── EXISTING REENTRY2_* BLOCKS (ablation) ──
        ("REENTRY2_MASTER_OFF", {"REENTRY_2_ENABLED": False}),
        ("REENTRY2_DIR_FAV_OFF", {"REENTRY2_DIR_FAV_ENABLED": False}),
        ("REENTRY2_DC_BREAK_OFF", {"REENTRY2_DC_BREAK_ENABLED": False}),
        ("REENTRY2_QUICK_RECOVERY_OFF", {"REENTRY2_QUICK_RECOVERY_ENABLED": False}),
        # ── EXISTING REENTRY_B* BLOCKS (ablation) ──
        ("REENTRY_B02_OFF", {"REENTRY_B02_BC156_BOTTOM_ENABLED": False}),
        ("REENTRY_B10_OFF", {"REENTRY_B10_STOCH_REV_ENABLED": False}),
        ("REENTRY_B11_OFF", {"REENTRY_B11_DC_BREAK_ENABLED": False}),
        ("REENTRY_B12_OFF", {"REENTRY_B12_WT_MOM_ENABLED": False}),
        ("REENTRY_B14_OFF", {"REENTRY_B14_HA_TREND_ENABLED": False}),
        ("REENTRY_B15_OFF", {"REENTRY_B15_STRONG_TREND_ENABLED": False}),
        # ── COMBINED: turn everything off to see marginal value of ALL reentries ──
        ("ALL_NEW_REENTRIES_OFF", {
            "REENTRY_WT15M_CROSS_ENABLED": False,
            "REENTRY_K15M_PARTIAL_ENABLED": False,
            "REENTRY_POST_CONSOL_ENABLED": False,
        }),
        ("ALL_REENTRIES_OFF", {
            "REENTRY_2_ENABLED": False,
            "REENTRY_WT15M_CROSS_ENABLED": False,
            "REENTRY_K15M_PARTIAL_ENABLED": False,
            "REENTRY_POST_CONSOL_ENABLED": False,
            "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
            "REENTRY_B10_STOCH_REV_ENABLED": False,
            "REENTRY_B11_DC_BREAK_ENABLED": False,
            "REENTRY_B12_WT_MOM_ENABLED": False,
            "REENTRY_B14_HA_TREND_ENABLED": False,
            "REENTRY_B15_STRONG_TREND_ENABLED": False,
        }),
    ]


def grid_hedge_full():
    keys = {
        "HEDGE_EXIT_BYPASS_NOLOSS": [True, False],
        "HEDGE_CLOSE_REMOVE_FROM_TRADEABLE": [True, False],
        "HEDGE_SAME_SYMBOL_PCT": [0.5, 1.0, 1.5],
        "HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE": [True, False],
    }
    names = list(keys.keys())
    variants = []
    for combo in itertools.product(*[keys[k] for k in names]):
        cfg = dict(zip(names, combo))
        label = "_".join(f"{k.replace('HEDGE_', '')}={v}" for k, v in cfg.items())
        variants.append((label, cfg))
    return variants


def grid_reentry_killed_rerun():
    """Re-run the 12 variants that died with rc=-9 (OOM) at end of reentry_wide v3 on S1.
    Use lower worker count + maybe narrower symbols to avoid memory pressure."""
    return [
        ("baseline", {}),
        ("REENTRY2_MASTER_OFF", {"REENTRY_2_ENABLED": False}),
        ("REENTRY2_DIR_FAV_OFF", {"REENTRY2_DIR_FAV_ENABLED": False}),
        ("REENTRY2_DC_BREAK_OFF", {"REENTRY2_DC_BREAK_ENABLED": False}),
        ("REENTRY2_QUICK_RECOVERY_OFF", {"REENTRY2_QUICK_RECOVERY_ENABLED": False}),
        ("REENTRY_B02_OFF", {"REENTRY_B02_BC156_BOTTOM_ENABLED": False}),
        ("REENTRY_B10_OFF", {"REENTRY_B10_STOCH_REV_ENABLED": False}),
        ("REENTRY_B11_OFF", {"REENTRY_B11_DC_BREAK_ENABLED": False}),
        ("REENTRY_B12_OFF", {"REENTRY_B12_WT_MOM_ENABLED": False}),
        ("REENTRY_B14_OFF", {"REENTRY_B14_HA_TREND_ENABLED": False}),
        ("REENTRY_B15_OFF", {"REENTRY_B15_STRONG_TREND_ENABLED": False}),
        ("ALL_NEW_REENTRIES_OFF", {
            "REENTRY_WT15M_CROSS_ENABLED": False,
            "REENTRY_K15M_PARTIAL_ENABLED": False,
            "REENTRY_POST_CONSOL_ENABLED": False,
        }),
        ("ALL_REENTRIES_OFF", {
            "REENTRY_2_ENABLED": False,
            "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
            "REENTRY_B10_STOCH_REV_ENABLED": False,
            "REENTRY_B11_DC_BREAK_ENABLED": False,
            "REENTRY_B12_WT_MOM_ENABLED": False,
            "REENTRY_B14_HA_TREND_ENABLED": False,
            "REENTRY_B15_STRONG_TREND_ENABLED": False,
            "REENTRY_WT15M_CROSS_ENABLED": False,
            "REENTRY_K15M_PARTIAL_ENABLED": False,
            "REENTRY_POST_CONSOL_ENABLED": False,
        }),
    ]


TIER_MAP = {
    "hedge_one_by_one": grid_hedge_one_by_one,
    "reentry_one_by_one": grid_reentry_one_by_one,
    "reentry_wide": grid_reentry_wide,
    "reentry_killed_rerun": grid_reentry_killed_rerun,
    "hedge_reentry_ablation": grid_hedge_reentry_ablation,
    "hedge_full": grid_hedge_full,
}


# ══════════════════════════════════════════════════════════════════════════════
# SUBPROCESS RUNNER
# ══════════════════════════════════════════════════════════════════════════════

def _config_hash(cfg: dict) -> str:
    return hashlib.md5(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:8]


def run_one_variant(args_tuple):
    """Worker: writes override JSON, runs engine subprocess, parses V8_RESULT."""
    (label, overrides, mode, account, start, symbols, capital, npz_dir, timeout_s) = args_tuple
    # MERGE BASE OVERRIDES applied to every variant (including baseline):
    # - USDC_PREFERENCE_BLOCK_ENABLED=False: NPZ data is USDT-only, so the live USDC-preference
    #   gate would block 100% of USDT opens. Turn it off for backtest so entries are evaluated.
    #   This is a DATA shape concern, not a research variable.
    merged = {"USDC_PREFERENCE_BLOCK_ENABLED": False}
    merged.update(overrides)
    overrides = merged
    cfg_hash = _config_hash(overrides)
    override_path = OVERRIDE_DIR / f"v8sweep_{label}_{cfg_hash}.json"
    override_path.write_text(json.dumps(overrides, indent=2))

    cmd = [
        PY_BIN, str(ENGINE_PATH),
        "--mode", mode,
        "--account", account,
        "--start", start,
        "--capital", str(capital),
    ]
    if symbols:
        cmd += ["--symbols", symbols]
    if npz_dir:
        cmd += ["--npz-dir", npz_dir]

    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = str(override_path)
    env["V8_SWEEP_MODE"] = "1"  # suppress per-trade logs, 10-50x speedup

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, env=env,
            capture_output=True, text=True,
            timeout=timeout_s,
        )
        elapsed = time.time() - t0
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        match = None
        for line in stdout.splitlines():
            m = V8_RESULT_RE.search(line)
            if m:
                match = m  # keep last V8_RESULT line (LIVE lines appear earlier)
        if match:
            return {
                "label": label,
                "overrides": overrides,
                "cfg_hash": cfg_hash,
                "status": "ok",
                "sharpe_w": float(match["sharpe_w"]),
                "sharpe_pt": float(match["sharpe_pt"]),
                "sharpe_ann": float(match["sharpe_ann"]),
                "gain_pct": float(match["gain_pct"]),
                "closes": int(match["closes"]),
                "wins": int(match["wins"]),
                "losses": int(match["losses"]),
                "elapsed_s": round(elapsed, 1),
                "rc": proc.returncode,
                "stderr_tail": stderr.splitlines()[-5:] if stderr else [],
            }
        return {
            "label": label, "overrides": overrides, "cfg_hash": cfg_hash,
            "status": "no_result", "elapsed_s": round(elapsed, 1), "rc": proc.returncode,
            "stderr_tail": stderr.splitlines()[-5:] if stderr else [],
            "stdout_tail": stdout.splitlines()[-5:] if stdout else [],
        }
    except subprocess.TimeoutExpired:
        return {
            "label": label, "overrides": overrides, "cfg_hash": cfg_hash,
            "status": "timeout", "elapsed_s": timeout_s, "rc": -1,
        }
    except Exception as e:
        return {
            "label": label, "overrides": overrides, "cfg_hash": cfg_hash,
            "status": "error", "error": str(e),
        }


# ══════════════════════════════════════════════════════════════════════════════
# CSV WRITER — incremental flush so interruptions don't lose results
# ══════════════════════════════════════════════════════════════════════════════

CSV_FIELDS = [
    "label", "cfg_hash", "status", "sharpe_w", "sharpe_pt", "sharpe_ann",
    "gain_pct", "closes", "wins", "losses", "win_rate", "elapsed_s", "rc",
    "overrides_json",
]


def _result_to_row(r: dict) -> dict:
    wins = r.get("wins", 0) or 0
    losses = r.get("losses", 0) or 0
    total = wins + losses
    return {
        "label": r.get("label", ""),
        "cfg_hash": r.get("cfg_hash", ""),
        "status": r.get("status", ""),
        "sharpe_w": r.get("sharpe_w", ""),
        "sharpe_pt": r.get("sharpe_pt", ""),
        "sharpe_ann": r.get("sharpe_ann", ""),
        "gain_pct": r.get("gain_pct", ""),
        "closes": r.get("closes", ""),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total * 100, 1) if total > 0 else "",
        "elapsed_s": r.get("elapsed_s", ""),
        "rc": r.get("rc", ""),
        "overrides_json": json.dumps(r.get("overrides", {}), sort_keys=True),
    }


def write_csv_row(path: Path, row: dict, header_written: bool) -> None:
    mode = "a" if header_written else "w"
    with path.open(mode, newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not header_written:
            w.writeheader()
        w.writerow(row)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    ap.add_argument("--account", default="ang")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--symbols", default="", help="Comma-separated; empty=all NPZ symbols")
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--npz-dir", default="", help="Explicit NPZ dir, leave empty for default")
    ap.add_argument("--tier", default="hedge_one_by_one", choices=sorted(TIER_MAP.keys()))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=600, help="Per-variant subprocess timeout (s)")
    ap.add_argument("--output", default="", help="CSV output path (default: auto-dated)")
    args = ap.parse_args()

    grid = TIER_MAP[args.tier]()
    print(f"[sweep] tier={args.tier}  variants={len(grid)}  mode={args.mode}  account={args.account}  start={args.start}  symbols={args.symbols or 'ALL'}  workers={args.workers}")

    out_path = Path(args.output) if args.output else (
        RESULTS_DIR / f"backtest_v8_sweep_{args.tier}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.csv"
    )
    print(f"[sweep] output -> {out_path}")

    tasks = [
        (label, overrides, args.mode, args.account, args.start, args.symbols, args.capital, args.npz_dir, args.timeout)
        for (label, overrides) in grid
    ]

    header_written = False
    t_start = time.time()
    completed = 0
    baseline_sharpe = None

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one_variant, t): t[0] for t in tasks}
        for fut in as_completed(futures):
            label = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = {"label": label, "status": "worker_error", "error": str(e)}
            completed += 1
            row = _result_to_row(res)
            write_csv_row(out_path, row, header_written)
            header_written = True
            delta_s = ""
            if res.get("status") == "ok":
                s_w = res["sharpe_w"]
                if label == "baseline":
                    baseline_sharpe = s_w
                elif baseline_sharpe is not None:
                    delta_s = f" Δ={s_w - baseline_sharpe:+.3f}"
                print(f"[{completed:3d}/{len(tasks)}] {label:50s} sharpe_w={res['sharpe_w']:+.3f}{delta_s} gain={res['gain_pct']:+.2f}% closes={res['closes']} wr={row['win_rate']}% {res['elapsed_s']:.0f}s")
            else:
                print(f"[{completed:3d}/{len(tasks)}] {label:50s} STATUS={res.get('status')} rc={res.get('rc')} {res.get('stderr_tail', [])}")

    elapsed_total = time.time() - t_start
    print(f"\n[sweep] done in {elapsed_total:.0f}s ({elapsed_total/60:.1f}m)  out={out_path}")

    # Best-of summary
    try:
        import csv as _csv
        rows = []
        with out_path.open() as f:
            for r in _csv.DictReader(f):
                if r["status"] == "ok" and r["sharpe_w"]:
                    rows.append(r)
        rows.sort(key=lambda r: float(r["sharpe_w"] or 0), reverse=True)
        print("\nTop 5 by sharpe_w:")
        for r in rows[:5]:
            print(f"  {r['label']:50s} sharpe_w={r['sharpe_w']} gain={r['gain_pct']}% closes={r['closes']} wr={r['win_rate']}%")
    except Exception:
        pass


if __name__ == "__main__":
    main()
