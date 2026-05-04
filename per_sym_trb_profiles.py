#!/usr/bin/env python3
"""per_sym_trb_profiles — comprehensive per-symbol tradier sweeper for trb account.

Pipeline:
  Phase 1 — Baseline metrics from existing canonical trade JSONLs (data/canonical_trades/trb_BEST_*/).
             Skips to Phase 2 if no JSONL found for a symbol.
  Phase 2 — Full tradier-specific parameter sweep (~60 variants per symbol).
             Winner = max(total_gain_pct) among configs with pool_sharpe>0,
             dd<=PROMOTE_DD_MAX, trades>=30. pool_sharpe>0 guards against lucky PnL.
  Phase 3 — Write winner JSON per symbol to data/hourly_reconfig/trb/_candidates/.
             OHLC candlestick charts with trade overlays written to plots/.
  Phase 4 — Leaderboard sorted by total_gain_pct.

CLAUDE.md constraints honoured:
  - ATR_TRAIL_ENABLED always False (hardcoded — #1 stock PnL destroyer per CLAUDE.md).
  - MODE always "tradier" — MODE_CONFIG_MISMATCH_SKIP if wrong.
  - NPZ dir same as crypto: backtest_v8/indicators/ (tradier NPZ files live there too).
  - All metrics routed through metrics_guard.write_sharpe_row / pool_sharpe.
  - No bare "Sharpe X.X" — always pool_sharpe / sym_sharpe qualifiers.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import shutil

import numpy as np
import metrics_guard as mg
from v8_quick_engine import simulate, QuickConfig

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    from matplotlib.patches import Patch
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


def _prune_old_per_sym_runs(sweep_dir: Path, prefix: str, keep: int = 2) -> None:
    dirs = sorted(sweep_dir.glob(f"{prefix}_*/"), key=lambda p: p.name)
    for old in dirs[:-keep] if keep > 0 else dirs:
        try:
            shutil.rmtree(old)
        except Exception as e:
            print(f"  [prune] could not remove {old}: {e}")


# ── Paths ─────────────────────────────────────────────────────────────────────
NPZ_DIR = ROOT / "backtest_v8" / "indicators"
SWEEP_DIR = ROOT / "data" / "sweep_results"
CAND_OUT_DIR = ROOT / "data" / "hourly_reconfig" / "trb" / "_candidates"
CHARTS_OUT_DIR = ROOT / "plots"
GLOBAL_ACTIVE_CFG = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
SYMBOLS_LONG_FILE = ROOT / "symbols_trb_long.json"
SYMBOLS_SHORT_FILE = ROOT / "symbols_trb_short.json"
# Phase 1 canonical trade JSONL search root — matches any trb_BEST_* subdirectory
CANONICAL_TRADES_ROOT = ROOT / "data" / "canonical_trades"

# ── Promotion thresholds ───────────────────────────────────────────────────────
PROMOTE_POOL_MIN = 0.3
PROMOTE_TRADES_MIN = 30          # per-symbol floor; stocks trade less than crypto
PROMOTE_DD_MAX = 10.0
PROMOTE_GAIN_PER_YR_MIN = 50.0  # lower than crypto — stocks move less %
PROMOTE_WR_MIN = 55.0           # tradier baseline WR ~60%


# ── Symbol loading ─────────────────────────────────────────────────────────────

def load_trb_symbols() -> List[str]:
    longs: List[str] = json.loads(SYMBOLS_LONG_FILE.read_text())
    shorts: List[str] = json.loads(SYMBOLS_SHORT_FILE.read_text())
    syms = sorted(set(longs) | set(shorts))
    return [s for s in syms if (NPZ_DIR / f"{s}.npz").exists()]


# ── Metrics helpers ───────────────────────────────────────────────────────────

def per_sym_metrics(rets: List[float], years: float, sym: str) -> Dict:
    n = len(rets)
    n_wins = sum(1 for r in rets if r > 0)
    wr = (100.0 * n_wins / n) if n else 0.0
    total_gain = float(sum(rets))
    avg_gain = (total_gain / n) if n else 0.0
    yrs = max(0.01, years)
    pool = mg.pool_sharpe(rets)
    sym_pool_capped = max(-mg.PER_SYM_SHARPE_CAP, min(mg.PER_SYM_SHARPE_CAP, pool))
    eq = 0.0; peak = 0.0; worst = 0.0
    for r in rets:
        eq += r
        if eq > peak:
            peak = eq
        elif peak - eq > worst:
            worst = peak - eq
    return {
        "pool_sharpe": pool, "sym_sharpe": sym_pool_capped,
        "avg_gain_trade": avg_gain, "gain_per_yr": total_gain / yrs,
        "gain_sym_yr": total_gain / yrs,
        "trades": n, "n_syms": 1, "years": yrs,
        "total_gain_pct": total_gain, "wr_pct": wr,
        "max_dd_pct": worst, "n_wins": n_wins, "tag": f"trb_per_sym_{sym}",
    }


def verdict(m: Dict) -> str:
    if m["max_dd_pct"] > PROMOTE_DD_MAX:
        return "REJECT_DD"
    if m["wr_pct"] < PROMOTE_WR_MIN:
        return "REJECT_WR"
    if m["trades"] < PROMOTE_TRADES_MIN:
        return "REJECT_TRADES"
    if m["gain_per_yr"] < PROMOTE_GAIN_PER_YR_MIN:
        return "REJECT_PNL"
    if m["pool_sharpe"] < PROMOTE_POOL_MIN:
        return "DIAGNOSTIC"
    return "PROMOTE"


def effective_score(m: Dict) -> float:
    return float(m["pool_sharpe"]) * math.sqrt(max(1, m["trades"]) / 1000.0)


def fmt_canonical(sym: str, m: Dict, v: str) -> str:
    return (
        f"{sym:10s} | pool={m['pool_sharpe']:+.4f} | sym={m['sym_sharpe']:+.4f} | "
        f"wr={m['wr_pct']:.1f}% | avg={m['avg_gain_trade']:+.4f}% | "
        f"gpy={m['gain_per_yr']:+.1f}% | pnl={m['total_gain_pct']:+.1f}% | "
        f"trades={m['trades']:,} | dd={m['max_dd_pct']:.2f}% | {v}"
    )


def pick_winner(results: List[Tuple[str, Dict, Dict]]) -> Tuple[str, Dict, Dict, str]:
    """Winner = max total_gain_pct among configs with pool_sharpe>0, dd<=cap, trades>=30."""
    qualified = [(tag, ovr, m) for tag, ovr, m in results
                 if m["pool_sharpe"] > 0
                 and m["max_dd_pct"] <= PROMOTE_DD_MAX
                 and m["trades"] >= PROMOTE_TRADES_MIN]
    if qualified:
        best = max(qualified, key=lambda t: t[2]["total_gain_pct"])
        return best[0], best[1], best[2], "QUALIFIED_BY_PNL"
    viable = [(tag, ovr, m) for tag, ovr, m in results if m["trades"] >= PROMOTE_TRADES_MIN] or results
    best = max(viable, key=lambda t: t[2]["effective_score"])
    return best[0], best[1], best[2], "FALLBACK_EFF"


# ── Phase 1 — load existing canonical trade JSONLs ────────────────────────────

def find_canonical_jsonl(sym: str) -> Optional[Path]:
    """Search data/canonical_trades/trb_BEST_*/ for {sym}.jsonl."""
    for cand_dir in sorted(CANONICAL_TRADES_ROOT.glob("trb_BEST_*")):
        p = cand_dir / f"{sym}.jsonl"
        if p.exists():
            return p
    return None


def jsonl_pnls_and_meta(path: Path) -> Tuple[List[float], int, int, float, float]:
    rs: List[float] = []
    wins = 0
    first: Optional[float] = None
    last: float = 0.0
    with path.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            r = float(rec.get("pnl_pct", 0.0))
            rs.append(r)
            if r > 0:
                wins += 1
            ets = int(rec.get("exit_ts", 0) or 0)
            if ets and (first is None or ets < first):
                first = float(ets)
            if ets > last:
                last = float(ets)
    return rs, len(rs), wins, float(first or 0), last


def phase1_baseline(syms: List[str]) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    print("=" * 88)
    print("PHASE 1 — per-sym baseline (from existing canonical trb_BEST_* JSONLs)")
    print("=" * 88)
    for sym in syms:
        jp = find_canonical_jsonl(sym)
        if jp is None:
            print(f"  {sym}: no canonical JSONL — proceeding to Phase 2")
            continue
        rets, n, wins, first_ts, last_ts = jsonl_pnls_and_meta(jp)
        years = (last_ts - first_ts) / 86400.0 / 365.25 if (first_ts and last_ts) else 2.0
        m = per_sym_metrics(rets, years, sym)
        v = verdict(m)
        m["verdict"] = v
        out[sym] = m
        print(f"  {fmt_canonical(sym, m, v)}  [{jp.parent.name}]")
    return out


# ── Phase 2 — tradier mutation grid ──────────────────────────────────────────

def mutation_grid() -> List[Tuple[str, Dict]]:
    """~60-variant tradier-specific sweep grid.

    Covers CLAUDE.md tradier-relevant params:
      ENTRY_SCORE_THRESHOLD, STDEV_BREAKOUT/BOUNCE, HTF_MIN_ALIGNED,
      USE_PROCESS_POSITION_EXIT_GATES, PEAK_GIVEBACK, and key combos.

    ATR_TRAIL_ENABLED is NEVER set True — hardcoded False per CLAUDE.md (#1 stock PnL destroyer).
    """
    grid: List[Tuple[str, Dict]] = []

    def add(tag: str, deltas: Dict) -> None:
        o = dict(deltas)
        # ATR_TRAIL_ENABLED always False — CLAUDE.md absolute rule
        o["ATR_TRAIL_ENABLED_TRADIER"] = False
        o["ATR_TRAIL_ENABLED"] = False
        grid.append((tag, o))

    # ── Baseline ──────────────────────────────────────────────────────────────
    add("baseline", {})

    # ── 1. ENTRY_SCORE_THRESHOLD ───────────────────────────────────────────────
    # tradier apply_tradier_defaults sets ENTRY_SCORE_THRESHOLD=0 (disabled).
    # CLAUDE.md: stock best is 24. Sweep the full range.
    for es in (16, 18, 20, 22, 24, 26, 28, 30):
        add(f"es_{es}", {"ENTRY_SCORE_THRESHOLD": float(es)})

    # ── 2. HTF_MIN_ALIGNED — CLAUDE.md stocks best is 2, sweep 1–3 ────────────
    for htf in (1, 2, 3):
        add(f"htf_{htf}", {"HTF_MIN_ALIGNED": htf})

    # ── 3. STDEV_BREAKOUT_ENABLED + STDEV_BOUNCE_ENABLED combos ──────────────
    add("stdev_break_only", {"STDEV_BREAKOUT_ENABLED": True, "STDEV_BOUNCE_ENABLED": False})
    add("stdev_bounce_only", {"STDEV_BREAKOUT_ENABLED": False, "STDEV_BOUNCE_ENABLED": True})
    add("stdev_both", {"STDEV_BREAKOUT_ENABLED": True, "STDEV_BOUNCE_ENABLED": True})
    add("stdev_both_reject",
        {"STDEV_BREAKOUT_ENABLED": True, "STDEV_BOUNCE_ENABLED": True,
         "STDEV_REJECT_EXIT_ENABLED": True})
    add("stdev_break_suppress",
        {"STDEV_BREAKOUT_ENABLED": True, "STDEV_SUPPRESS_EARLY_EXIT": True})

    # ── 4. USE_PROCESS_POSITION_EXIT_GATES — live-parity exit paths ───────────
    # CLAUDE.md: helps tradier +9%
    add("exit_gates_on", {"USE_PROCESS_POSITION_EXIT_GATES": True})
    add("exit_gates_off", {"USE_PROCESS_POSITION_EXIT_GATES": False})

    # ── 5. PEAK_GIVEBACK — exit on giveback from peak profit ──────────────────
    for pg_pct in (0.3, 0.5, 0.7):
        add(f"pg_{pg_pct:.1f}", {"PEAK_GIVEBACK_ENABLED": True, "PEAK_GIVEBACK_DROP_PCT": pg_pct})
    add("pg_off", {"PEAK_GIVEBACK_ENABLED": False})
    # PEAK_GIVEBACK + arm floor combinations
    add("pg_arm0.5_drop0.3",
        {"PEAK_GIVEBACK_ENABLED": True, "PEAK_GIVEBACK_PEAK_MIN_PCT": 0.5, "PEAK_GIVEBACK_DROP_PCT": 0.3})
    add("pg_arm1.0_drop0.5",
        {"PEAK_GIVEBACK_ENABLED": True, "PEAK_GIVEBACK_PEAK_MIN_PCT": 1.0, "PEAK_GIVEBACK_DROP_PCT": 0.5})

    # ── 6. WT_EXIT_MIN_TFS — CLAUDE.md stocks best is 3 ──────────────────────
    for wt in (2, 3, 4):
        add(f"wt_exit_{wt}", {"WT_EXIT_MIN_TFS": wt})

    # ── 7. MIN_HOLD_BARS — tradier default 4; sweep wider ────────────────────
    for mh in (2, 4, 8, 16, 32):
        add(f"mh_{mh}", {"MIN_HOLD_BARS": mh})

    # ── 8. EXIT_SCORER — tradier validated +25.3% per engine comment ─────────
    add("exit_scorer_off", {"EXIT_SCORER_ENABLED": False})
    for sc in (2, 3, 4):
        add(f"exit_scorer_cond{sc}", {"EXIT_SCORER_ENABLED": True, "EXIT_SCORER_MIN_CONDITIONS": sc})

    # ── 9. ENTRY_ZONE — CLAUDE.md note: stocks live uses k_1h < 30 LONG / > 70 SHORT ──
    add("ez_default", {"ENTRY_ZONE_LONG": 30.0, "ENTRY_ZONE_SHORT": 70.0, "ENTRY_ZONE_K_TF": "1h"})
    add("ez_tight",   {"ENTRY_ZONE_LONG": 20.0, "ENTRY_ZONE_SHORT": 80.0, "ENTRY_ZONE_K_TF": "1h"})
    add("ez_loose",   {"ENTRY_ZONE_LONG": 40.0, "ENTRY_ZONE_SHORT": 60.0, "ENTRY_ZONE_K_TF": "1h"})
    add("ez_off",     {"ENTRY_ZONE_LONG": 0.0, "ENTRY_ZONE_SHORT": 100.0})

    # ── 10. DC_RECOVERY_EXIT — tradier default ON (stocks only escape valve) ──
    add("dc_rec_off", {"DC_RECOVERY_EXIT_ENABLED": False})
    add("dc_rec_on",  {"DC_RECOVERY_EXIT_ENABLED": True})

    # ── 11. RANK_CONVICTION — S_E baseline switch for tradier ─────────────────
    add("rank_conv_off", {"RANK_CONVICTION_ENABLED": False})
    for rc in (2, 3):
        add(f"rank_conv_{rc}", {"RANK_CONVICTION_ENABLED": True, "RANK_CONVICTION_MIN": rc})

    # ── 12. DC_MOMENT — tradier default ON ────────────────────────────────────
    add("dc_moment_off", {"DC_MOMENT_ENABLED": False})

    # ── 13. RZ_EXIT tuning ────────────────────────────────────────────────────
    for rz_k in (70.0, 80.0, 90.0):
        add(f"rz_k_{rz_k:.0f}", {"RZ_EXIT_ENABLED": True, "RZ_K_EXIT": rz_k})
    add("rz_exit_off", {"RZ_EXIT_ENABLED": False})

    # ── 14. Key 2-param combos ────────────────────────────────────────────────
    # es + htf
    for es, htf in ((20, 2), (24, 2), (24, 3), (28, 3)):
        add(f"es{es}_htf{htf}", {"ENTRY_SCORE_THRESHOLD": float(es), "HTF_MIN_ALIGNED": htf})
    # stdev + exit_gates
    add("stdev_both_exit_gates",
        {"STDEV_BREAKOUT_ENABLED": True, "STDEV_BOUNCE_ENABLED": True,
         "USE_PROCESS_POSITION_EXIT_GATES": True})
    # es + stdev_break
    add("es24_stdev_break",
        {"ENTRY_SCORE_THRESHOLD": 24.0, "STDEV_BREAKOUT_ENABLED": True})
    add("es24_stdev_both",
        {"ENTRY_SCORE_THRESHOLD": 24.0, "STDEV_BREAKOUT_ENABLED": True,
         "STDEV_BOUNCE_ENABLED": True})
    # htf + exit_gates
    add("htf2_exit_gates",
        {"HTF_MIN_ALIGNED": 2, "USE_PROCESS_POSITION_EXIT_GATES": True})
    add("htf3_exit_gates",
        {"HTF_MIN_ALIGNED": 3, "USE_PROCESS_POSITION_EXIT_GATES": True})
    # pg + exit_gates
    add("pg_exit_gates",
        {"PEAK_GIVEBACK_ENABLED": True, "PEAK_GIVEBACK_DROP_PCT": 0.5,
         "USE_PROCESS_POSITION_EXIT_GATES": True})
    # es + mh
    add("es24_mh8", {"ENTRY_SCORE_THRESHOLD": 24.0, "MIN_HOLD_BARS": 8})
    add("es20_mh4", {"ENTRY_SCORE_THRESHOLD": 20.0, "MIN_HOLD_BARS": 4})
    # Triple: es + htf + exit_gates
    add("es24_htf2_egates",
        {"ENTRY_SCORE_THRESHOLD": 24.0, "HTF_MIN_ALIGNED": 2,
         "USE_PROCESS_POSITION_EXIT_GATES": True})
    add("es24_htf2_stdev_egates",
        {"ENTRY_SCORE_THRESHOLD": 24.0, "HTF_MIN_ALIGNED": 2,
         "STDEV_BREAKOUT_ENABLED": True, "STDEV_BOUNCE_ENABLED": True,
         "USE_PROCESS_POSITION_EXIT_GATES": True})
    # Rank + stdev + htf
    add("rc3_htf2_stdev",
        {"RANK_CONVICTION_ENABLED": True, "RANK_CONVICTION_MIN": 3,
         "HTF_MIN_ALIGNED": 2, "STDEV_BREAKOUT_ENABLED": True})
    # ES + pg + wt_exit
    add("es24_pg0.5_wt3",
        {"ENTRY_SCORE_THRESHOLD": 24.0,
         "PEAK_GIVEBACK_ENABLED": True, "PEAK_GIVEBACK_DROP_PCT": 0.5,
         "WT_EXIT_MIN_TFS": 3})
    add("es24_htf2_pg0.5_wt3_egates",
        {"ENTRY_SCORE_THRESHOLD": 24.0, "HTF_MIN_ALIGNED": 2,
         "PEAK_GIVEBACK_ENABLED": True, "PEAK_GIVEBACK_DROP_PCT": 0.5,
         "WT_EXIT_MIN_TFS": 3, "USE_PROCESS_POSITION_EXIT_GATES": True})

    return grid


# ── Engine runner ─────────────────────────────────────────────────────────────

def run_engine_for_sym(sym: str, overrides: Dict, run_dir: Path, run_id: str,
                       npz: dict) -> Tuple[List[float], List[Dict]]:
    """Run v8_quick simulate for a single tradier symbol; return (returns, trade_records)."""
    os.environ["V8_TRADES_OUT_DIR"] = str(run_dir)
    os.environ["V8_TRADES_RUN_ID"] = run_id
    os.environ["V8_RATE_GUARD_DISABLED"] = "1"
    cfg = QuickConfig()
    cfg.apply_tradier_defaults()
    # ATR_TRAIL_ENABLED hardcoded False — CLAUDE.md absolute rule
    cfg.ATR_TRAIL_ENABLED_TRADIER = False
    cfg.ATR_TRAIL_ENABLED = False
    for k, v in overrides.items():
        if k.startswith("_"):
            continue
        try:
            setattr(cfg, k, v)
        except Exception:
            pass
    # Re-enforce ATR_TRAIL after any override (defensive)
    cfg.ATR_TRAIL_ENABLED_TRADIER = False
    cfg.ATR_TRAIL_ENABLED = False
    try:
        simulate({sym: npz}, cfg, capital=10000.0)
    except SystemExit:
        pass
    except Exception as e:
        print(f"    [engine] EXC {sym}: {e}")
        return [], []
    jp = run_dir / f"{run_id}__{sym}.jsonl"
    rets: List[float] = []
    records: List[Dict] = []
    if jp.exists():
        with jp.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    rets.append(float(rec.get("pnl_pct", 0.0)))
                    records.append(rec)
                except Exception:
                    pass
    return rets, records


# ── Phase 2 driver ────────────────────────────────────────────────────────────

def phase2_sweep_sym(sym: str, run_root: Path, sweep_csv: Path,
                     full_years: float) -> Tuple[Dict, Dict, List[Dict]]:
    print()
    print("─" * 80)
    print(f"PHASE 2 — {sym}  (tradier)")
    print("─" * 80)
    grid = mutation_grid()
    print(f"  grid size: {len(grid)} variants")

    npz_path = NPZ_DIR / f"{sym}.npz"
    z = np.load(str(npz_path))
    npz = {k: z[k] for k in z.files}
    z.close()

    run_dir = run_root / sym
    run_dir.mkdir(parents=True, exist_ok=True)

    results: List[Tuple[str, Dict, Dict]] = []
    winner_trades: List[Dict] = []
    t0 = time.time()
    best_pnl = -1e9
    for i, (tag, ovr) in enumerate(grid, 1):
        run_id = f"{sym}__{tag}__{i:03d}"
        rets, records = run_engine_for_sym(sym, ovr, run_dir, run_id, npz)
        m = per_sym_metrics(rets, full_years, sym)
        m["tag"] = f"trb_mut_{sym}_{tag}"
        v = verdict(m)
        m["verdict"] = v
        m["effective_score"] = effective_score(m)
        results.append((tag, ovr, m))
        try:
            mg.write_sharpe_row(sweep_csv, m, mode="tradier", append=True)
        except Exception as e:
            print(f"    [csv] REFUSED {tag}: {e}")
        marker = ""
        if m["pool_sharpe"] > 0 and m["max_dd_pct"] <= PROMOTE_DD_MAX and m["trades"] >= PROMOTE_TRADES_MIN:
            if m["total_gain_pct"] > best_pnl:
                best_pnl = m["total_gain_pct"]
                marker = " ← NEW BEST PNL"
                winner_trades = records
        if i <= 5 or i % 15 == 0 or marker:
            print(f"    [{i:3d}/{len(grid)}] {tag:35s} pool={m['pool_sharpe']:+.4f} "
                  f"pnl={m['total_gain_pct']:+8.1f}% tr={m['trades']:>5d} "
                  f"dd={m['max_dd_pct']:.2f}% {v}{marker}")

    elapsed = time.time() - t0
    print(f"  {sym} done in {elapsed:.1f}s ({elapsed/len(grid):.1f}s/variant)")

    tag, ovr, m, bucket = pick_winner(results)
    print(f"  WINNER ({bucket}): {tag} | pool_sharpe={m['pool_sharpe']:+.4f} | "
          f"pnl={m['total_gain_pct']:+.1f}% | trades={m['trades']:,} | "
          f"dd={m['max_dd_pct']:.2f}% | wr={m['wr_pct']:.1f}% | gpy={m['gain_per_yr']:+.1f}%")

    # Reload winner trades if needed (winner may have been picked via FALLBACK_EFF)
    if not winner_trades:
        winner_idx = next((idx + 1 for idx, (t, _, _) in enumerate(results) if t == tag), 1)
        winner_run_id = f"{sym}__{tag}__{winner_idx:03d}"
        jp = run_dir / f"{winner_run_id}__{sym}.jsonl"
        if jp.exists():
            with jp.open() as f:
                for line in f:
                    try:
                        winner_trades.append(json.loads(line))
                    except Exception:
                        pass
    return ovr, m, winner_trades


# ── Phase 3 — write winner JSONs + charts ────────────────────────────────────

_TRB_BANNED_PARAMS = {"ATR_TRAIL_ENABLED", "ATR_TRAIL_ENABLED_TRADIER", "_meta"}


def _side_metrics(trades: List[Dict], years: float, sym: str, side: str) -> Dict:
    rets = [float(t.get("pnl_pct", 0)) for t in trades if t.get("side", "").upper() == side.upper()]
    if not rets:
        return {}
    return per_sym_metrics(rets, years, f"{sym}_{side}")


def promote_to_global_active_config(sym: str, overrides: Dict, m: Dict, trades: List[Dict]) -> None:
    """Write trb winner to GLOBAL per_sym_active_config.json so live trading + 7D agent can read it.
    Keyed by {sym}_{side} (no account prefix) — per-symbol settings are global across all accounts.
    Skips writing if pool_sharpe<=0 or trades<floor (quality gate)."""
    ps = float(m.get("pool_sharpe", 0))
    tr = int(m.get("trades", 0))
    if ps <= 0 or tr < PROMOTE_TRADES_MIN:
        print(f"  [global_active] SKIP {sym}: pool={ps:+.4f} trades={tr} — quality gate")
        return
    GLOBAL_ACTIVE_CFG.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(GLOBAL_ACTIVE_CFG.read_text())
    except Exception:
        existing = {}
    delta = {k: v for k, v in overrides.items()
             if not k.startswith("_") and k not in _TRB_BANNED_PARAMS}
    years = float(m.get("years", 2.0))
    now_tag = time.strftime("%Y-%m-%d", time.gmtime())
    for side in ("LONG", "SHORT"):
        sm = _side_metrics(trades, years, sym, side) if trades else {}
        side_ps = float(sm.get("pool_sharpe", ps))
        side_tr = int(sm.get("trades", tr // 2))
        existing[f"{sym}_{side}"] = {
            "winning_tag": f"trb_{sym}_{now_tag}",
            "wsharpe": side_ps,
            "trades": side_tr,
            "total_trades_combined": tr,
            "sample_tag": "TRB",
            "side": side,
            "overrides": delta,
            "_delta_params": len(delta),
        }
    GLOBAL_ACTIVE_CFG.write_text(json.dumps(existing, indent=2, default=str))
    print(f"  [global_active] WRITE {sym}: pool={ps:+.4f} trades={tr:,} delta_params={len(delta)} → {GLOBAL_ACTIVE_CFG.name}")


def write_winner_json(sym: str, overrides: Dict, m: Dict) -> Path:
    CAND_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CAND_OUT_DIR / f"trb_{sym}_winner.json"
    payload = {k: v for k, v in overrides.items() if not k.startswith("_")}
    # ATR_TRAIL always False in written file
    payload["ATR_TRAIL_ENABLED_TRADIER"] = False
    payload["ATR_TRAIL_ENABLED"] = False
    payload["_meta"] = (
        f"trb per_sym winner {sym} | "
        f"pool_sharpe={m['pool_sharpe']:+.4f} sym_sharpe={m['sym_sharpe']:+.4f} "
        f"avg_gain_trade={m['avg_gain_trade']:+.4f}%/trade "
        f"gain_per_yr={m['gain_per_yr']:+.1f}%/yr gain_sym_yr={m['gain_sym_yr']:+.1f}%/sym/yr "
        f"trades={m['trades']} max_dd_pct={m['max_dd_pct']:.2f}% "
        f"n_syms=1 years={m['years']:.2f} "
        f"wr={m['wr_pct']:.1f}% verdict={m.get('verdict')} eff={m.get('effective_score', 0):+.3f}"
    )
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)
    return out_path


# ── Chart generation ──────────────────────────────────────────────────────────

def generate_sym_chart(sym: str, trades: List[Dict], m: Dict, overrides: Dict,
                       days: int = 60) -> None:
    """3-panel dark chart: OHLC candlesticks + trade overlays | cumPnL | per-trade bars."""
    if not HAS_MPL or not trades:
        return
    from datetime import datetime, timezone

    BG = "#0d1117"
    GRID = "#21262d"
    TICK = "#6e7681"
    TITLE = "#c9d1d9"
    cutoff_ts = time.time() - max(1, days) * 86400

    trades_w = [t for t in trades if t.get("exit_ts", 0) >= cutoff_ts]
    if not trades_w:
        trades_w = list(trades[-2000:])
    if not trades_w:
        print(f"  [chart] {sym}: no trades in window")
        return

    pnl = [float(t.get("pnl_pct", 0)) for t in trades_w]
    eq = 0.0
    cum_pnl: List[float] = []
    for p in pnl:
        eq += p
        cum_pnl.append(eq)
    n_win = sum(1 for p in pnl if p > 0)
    n_lose = len(pnl) - n_win

    dt_exit = [datetime.fromtimestamp(int(t.get("exit_ts", 0)), tz=timezone.utc)
               for t in trades_w if t.get("exit_ts", 0) > 0]
    if not dt_exit:
        print(f"  [chart] {sym}: no valid exit timestamps")
        return

    # Load NPZ: tradier base TF is 5m; fall back to 15m close if 5m not present
    npz_path = NPZ_DIR / f"{sym}.npz"
    ts_arr = open_arr = high_arr = low_arr = close_arr = None
    if npz_path.exists():
        z = np.load(str(npz_path))
        npz = {k: z[k] for k in z.files}
        z.close()
        # Prefer 5m (tradier base TF), fall back to 15m
        for tf_label in ("5m", "15m"):
            if f"close_{tf_label}" in npz:
                close_arr = npz[f"close_{tf_label}"]
                open_arr = npz.get(f"open_{tf_label}")
                high_arr = npz.get(f"high_{tf_label}")
                low_arr = npz.get(f"low_{tf_label}")
                ts_arr = npz.get(f"timestamps_{tf_label}", npz.get("timestamps_15m", npz.get("timestamps")))
                break

    fig = plt.figure(figsize=(18, 12), facecolor=BG, dpi=150)
    fig.patch.set_facecolor(BG)
    has_price = (ts_arr is not None and close_arr is not None and len(ts_arr) > 0)
    ratios = [3, 1.5, 1] if has_price else [2, 1]
    n_panels = len(ratios)
    gs = gridspec.GridSpec(n_panels, 1, height_ratios=ratios, hspace=0.05, figure=fig)

    def _style(ax):
        ax.set_facecolor(BG)
        for sp in ax.spines.values():
            sp.set_color("#30363d")
        ax.tick_params(colors=TICK, labelsize=7)
        ax.grid(axis="y", color=GRID, linewidth=0.4, zorder=0)

    panel = 0

    # ── Panel 0: OHLC candlesticks + entry/exit overlays ────────────────────
    if has_price:
        ax_p = fig.add_subplot(gs[panel])
        _style(ax_p)
        panel += 1
        mask = ts_arr >= cutoff_ts
        ts_w = ts_arr[mask]
        close_w = close_arr[mask]
        if len(ts_w) > 0:
            dt_p = [datetime.fromtimestamp(int(v), tz=timezone.utc) for v in ts_w]
            # Draw candlestick bodies if OHLC available; else line
            if open_arr is not None and high_arr is not None and low_arr is not None:
                open_w = open_arr[mask]
                high_w = high_arr[mask]
                low_w = low_arr[mask]
                # Draw wicks and bodies
                for idx, (dt_bar, o, h, l, c) in enumerate(zip(dt_p, open_w, high_w, low_w, close_w)):
                    color = "#3fb950" if c >= o else "#f85149"
                    # vertical wick: low → high
                    ax_p.plot([dt_bar, dt_bar], [l, h], color=color, lw=0.6, zorder=1, alpha=0.7)
                    # body rectangle (simplified as thick bar)
                    body_lo = min(o, c)
                    body_hi = max(o, c)
                    if body_hi > body_lo:
                        ax_p.fill_betweenx([body_lo, body_hi], [dt_bar], [dt_bar],
                                           color=color, alpha=0.0)  # placeholder
                        ax_p.plot([dt_bar, dt_bar], [body_lo, body_hi],
                                  color=color, lw=2.5, zorder=2, alpha=0.9, solid_capstyle="butt")
            else:
                ax_p.plot(dt_p, close_w, color="#c9d1d9", lw=0.7, zorder=2)

        # Trade overlays: ▲ LONG entry, ▼ SHORT entry, ● exit; lines entry→exit
        for t in trades_w:
            e_ts = int(t.get("entry_ts") or 0)
            x_ts = int(t.get("exit_ts") or 0)
            e_price = float(t.get("entry_price") or 0)
            x_price = float(t.get("exit_price") or 0)
            pnl_t = float(t.get("pnl_pct", 0))
            if not (e_ts and e_price):
                continue
            color = "#3fb950" if pnl_t > 0 else "#f85149"
            dt_e = datetime.fromtimestamp(e_ts, tz=timezone.utc)
            side = t.get("side", "LONG").upper()
            marker = "^" if side == "LONG" else "v"
            ax_p.scatter([dt_e], [e_price], marker=marker, s=50, color=color, zorder=5, alpha=0.9)
            if x_ts and x_price:
                dt_x = datetime.fromtimestamp(x_ts, tz=timezone.utc)
                ax_p.scatter([dt_x], [x_price], marker="o", s=25, color=color, zorder=5, alpha=0.75)
                ax_p.plot([dt_e, dt_x], [e_price, e_price], color=color,
                          lw=0.8, alpha=0.5, linestyle="--", zorder=3)

        # Legend
        handles = [
            Patch(facecolor="#3fb950", label=f"Win entry ({n_win})"),
            Patch(facecolor="#f85149", label=f"Loss entry ({n_lose})"),
        ]
        ax_p.legend(handles=handles, loc="upper left", fontsize=7,
                    facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")

        # Key overrides in title
        key_fields = ("ENTRY_SCORE_THRESHOLD", "HTF_MIN_ALIGNED", "WT_EXIT_MIN_TFS",
                      "STDEV_BREAKOUT_ENABLED", "STDEV_BOUNCE_ENABLED",
                      "USE_PROCESS_POSITION_EXIT_GATES", "PEAK_GIVEBACK_DROP_PCT")
        ovr_items = [(k, overrides[k]) for k in key_fields if k in overrides and not str(k).startswith("_")]
        ovr_str = "  ".join(f"{k.replace('_ENABLED', '').replace('STDEV_', 'SD').replace('USE_PROCESS_POSITION_', '').replace('PEAK_GIVEBACK_', 'PG')}={v}"
                            for k, v in ovr_items)
        ax_p.set_title(
            f"TRB | {sym}  pool_sharpe={m['pool_sharpe']:+.4f}  wr={m.get('wr_pct', 0):.1f}%  "
            f"dd={m.get('max_dd_pct', 0):.2f}%  trades={m['trades']:,}  wins={n_win}/{len(pnl)}  (last {days}d)\n"
            f"{ovr_str or '(tradier baseline)'}",
            color=TITLE, fontsize=9, pad=4)
        ax_p.xaxis.set_visible(False)

    # ── Panel 1: cumulative PnL ──────────────────────────────────────────────
    ax_cum = fig.add_subplot(gs[panel])
    _style(ax_cum)
    panel += 1
    if dt_exit and len(dt_exit) == len(cum_pnl):
        ax_cum.plot(dt_exit, cum_pnl, color="#58a6ff", lw=1.4, zorder=3)
        ax_cum.fill_between(dt_exit, cum_pnl, alpha=0.13, color="#58a6ff")
    ax_cum.axhline(0, color="#6e7681", lw=0.5, linestyle="--")
    ax_cum.set_ylabel("Cum PnL %", color=TICK, fontsize=8)
    if not has_price:
        ax_cum.set_title(
            f"TRB | {sym}  pool_sharpe={m['pool_sharpe']:+.4f}  wr={m.get('wr_pct', 0):.1f}%  "
            f"dd={m.get('max_dd_pct', 0):.2f}%  trades={m['trades']:,}  (last {days}d)",
            color=TITLE, fontsize=9, pad=4)
    ax_cum.xaxis.set_visible(panel < n_panels)

    # ── Panel 2: per-trade PnL bars ──────────────────────────────────────────
    ax_bar = fig.add_subplot(gs[panel], sharex=ax_cum)
    _style(ax_bar)
    if dt_exit and len(dt_exit) == len(pnl):
        colors = ["#3fb950" if p > 0 else "#f85149" for p in pnl]
        ax_bar.bar(dt_exit, pnl, color=colors, width=0.4, zorder=3)
    ax_bar.axhline(0, color="#6e7681", lw=0.5, linestyle="--")
    ax_bar.set_ylabel("Trade PnL %", color=TICK, fontsize=8)
    ax_bar.legend(
        handles=[Patch(facecolor="#3fb950", label=f"Win ({n_win})"),
                 Patch(facecolor="#f85149", label=f"Loss ({n_lose})")],
        loc="upper right", fontsize=7, facecolor="#161b22",
        edgecolor="#30363d", labelcolor="#c9d1d9")

    CHARTS_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CHARTS_OUT_DIR / f"OPT_trb_{sym}.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  [chart] {out_path.name}")


# ── Driver ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Per-symbol tradier (trb) parameter sweep and winner promotion.")
    ap.add_argument("--syms", default="",
                    help="Comma-separated symbol subset (default: all with NPZ)")
    ap.add_argument("--phase", default="1234",
                    help="Phases to run (default 1234)")
    ap.add_argument("--years", type=float, default=2.0,
                    help="Years to assume for per-symbol metrics (default 2.0)")
    ap.add_argument("--chart-days", type=int, default=60,
                    help="Days window for charts (default 60)")
    ap.add_argument("--mutate-all", action="store_true",
                    help="Force Phase 2 even if Phase 1 already PROMOTEs")
    args = ap.parse_args()

    all_syms = load_trb_symbols()
    syms = [s for s in args.syms.split(",") if s] if args.syms else all_syms
    syms = [s for s in syms if (NPZ_DIR / f"{s}.npz").exists()]

    print("=" * 88)
    print(f"per_sym_trb_profiles — tradier account trb — {len(syms)} symbols")
    print(f"NPZ dir     : {NPZ_DIR}")
    print(f"Candidates  : {CAND_OUT_DIR}")
    print(f"Python      : {sys.executable}")
    print(f"Phases      : {args.phase}  mutate_all={args.mutate_all}")
    print("=" * 88)
    if not syms:
        print("ERROR: no symbols with NPZ files found — check NPZ_DIR and symbol files")
        return 1

    ts = int(time.time())
    sweep_csv = SWEEP_DIR / f"per_sym_trb_{ts}.csv"
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    run_root = SWEEP_DIR / f"per_sym_trb_{ts}" / "engine_runs"
    run_root.mkdir(parents=True, exist_ok=True)

    # Phase 1
    phase1: Dict[str, Dict] = {}
    if "1" in args.phase:
        phase1 = phase1_baseline(syms)
        for sym, m in phase1.items():
            row = dict(m)
            row["tag"] = f"phase1_trb_{sym}"
            try:
                mg.write_sharpe_row(sweep_csv, row, mode="tradier", append=True)
            except Exception as e:
                print(f"  [phase1 csv] REFUSED {sym}: {e}")

    # Phase 2
    winners: Dict[str, Tuple[Dict, Dict]] = {}
    winner_trades_map: Dict[str, List[Dict]] = {}
    if "2" in args.phase:
        for sym in syms:
            p1 = phase1.get(sym, {})
            if p1.get("verdict") == "PROMOTE" and not args.mutate_all:
                winners[sym] = ({}, p1)
                print(f"\n  {sym}: Phase 1 PROMOTE — skipping Phase 2 (use --mutate-all to force)")
                continue
            try:
                ovr, m, wt = phase2_sweep_sym(sym, run_root, sweep_csv, args.years)
                winners[sym] = (ovr, m)
                winner_trades_map[sym] = wt
            except Exception as e:
                import traceback
                print(f"  PHASE2 EXC {sym}: {e}")
                traceback.print_exc()
            gc.collect()

    # Phase 3
    paths_written: List[Path] = []
    if "3" in args.phase:
        print()
        print("=" * 88)
        print("PHASE 3 — write winner JSONs → trb/_candidates/ + generate charts")
        print("=" * 88)
        for sym in syms:
            if sym not in winners:
                if sym in phase1:
                    winners[sym] = ({}, phase1[sym])
                else:
                    print(f"  {sym}: no data — skipping")
                    continue
            ovr, m = winners[sym]
            p = write_winner_json(sym, ovr, m)
            paths_written.append(p)
            print(f"  {p.name}  pool_sharpe={m['pool_sharpe']:+.4f}  trades={m['trades']:,}  dd={m['max_dd_pct']:.2f}%")
            # ALSO write to GLOBAL per_sym_active_config.json (live + 7D agent read this)
            try:
                promote_to_global_active_config(sym, ovr, m, winner_trades_map.get(sym, []))
            except Exception as e:
                print(f"  [global_active] EXC {sym}: {e}")
        print()
        print("PHASE 3b — charts")
        for sym in syms:
            if sym not in winners:
                continue
            ovr, m = winners[sym]
            trades = winner_trades_map.get(sym, [])
            if not trades:
                # Try loading from phase1 JSONL
                jp = find_canonical_jsonl(sym)
                if jp is not None:
                    with jp.open() as f:
                        for line in f:
                            try:
                                trades.append(json.loads(line))
                            except Exception:
                                pass
            if trades and HAS_MPL:
                try:
                    generate_sym_chart(sym, trades, m, ovr, days=args.chart_days)
                except Exception as ce:
                    print(f"  [chart] ERR {sym}: {ce}")
            elif not trades:
                print(f"  [chart] {sym}: no trade data for chart")

    # Phase 4 — leaderboard
    if "4" in args.phase:
        print()
        print("=" * 88)
        print("PHASE 4 — leaderboard (sorted by total_gain_pct — primary winner criterion)")
        print("=" * 88)
        table: List[Tuple[str, str, Dict]] = []
        for sym in syms:
            if sym in winners:
                _, m = winners[sym]
            elif sym in phase1:
                m = phase1[sym]
            else:
                continue
            v = m.get("verdict", verdict(m))
            table.append((sym, v, m))
        table.sort(key=lambda t: t[2].get("total_gain_pct", 0.0), reverse=True)
        print(f"  {'Rank':<5} {'Symbol':<10} {'pnl%':>9} {'pool_sharpe':>12} "
              f"{'gpy%':>8} {'trades':>8} {'dd%':>7} {'wr%':>7} {'eff':>7}  verdict")
        print("  " + "─" * 80)
        for i, (sym, v, m) in enumerate(table, 1):
            eff = effective_score(m)
            print(f"  {i:<5} {sym:<10} {m.get('total_gain_pct', 0):>+9.1f}% "
                  f"{m['pool_sharpe']:>+12.4f} {m['gain_per_yr']:>+8.1f}% "
                  f"{m['trades']:>8,} {m['max_dd_pct']:>6.2f}% {m['wr_pct']:>6.1f}% "
                  f"{eff:>+7.3f}  [{v}]")
        promotes = [(sym, m) for sym, v, m in table if v == "PROMOTE"]
        diagnostics = [(sym, m) for sym, v, m in table if v == "DIAGNOSTIC"]
        rejects = [(sym, v, m) for sym, v, m in table if v.startswith("REJECT")]
        print()
        print(f"  Summary: {len(promotes)} PROMOTE | {len(diagnostics)} DIAGNOSTIC | {len(rejects)} REJECT")
        if rejects:
            reject_dd = [(sym, m) for sym, v, m in rejects if v == "REJECT_DD"]
            if reject_dd:
                print(f"  REJECT_DD symbols (dd > {PROMOTE_DD_MAX}%): {[s for s, _ in reject_dd]}")

    print()
    print(f"CSV: {sweep_csv}")
    if paths_written:
        print("Candidates written:")
        for p in paths_written:
            print(f"  {p}")
    print(f"Charts: {CHARTS_OUT_DIR}/OPT_trb_*.png")
    _prune_old_per_sym_runs(SWEEP_DIR, "per_sym_trb", keep=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
