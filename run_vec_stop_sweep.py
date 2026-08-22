"""run_vec_stop_sweep.py — Vectorized frozen stop sweep for tradier.

37 variants × ~115 stocks × 4yr. Entry: RULE_A (D/W breakout → LTF bounce).
Each symbol NPZ loaded ONCE; all 37 stop variants run against cached data.

Uses v8_vec_sweep.simulate_one_symbol + ProcessPoolExecutor for symbol-level
parallelism. Expected wall-clock on S1 with workers=4: ~8-12 min for 37 variants.

Output: data/sweep_results/vec_stop_sweep_<TS>.csv

Usage:
  python run_vec_stop_sweep.py --start 2024-01-01 --workers 4 --mode tradier
  python run_vec_stop_sweep.py --status  # print available symbols
"""
from __future__ import annotations
import argparse, csv, json, math, sys, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(REPO_ROOT))
from v12_wide_engine import SweepConfig, simulate_one_symbol, load_npz, SWEEP_RESULTS_DIR, NPZ_DIR

# ── Stop sweep grid ──────────────────────────────────────────────────────────

def _make_cfg(**overrides) -> SweepConfig:
    cfg = SweepConfig()
    cfg.BREAKOUT_RETEST_ARMED_ENABLED = True   # RULE_A: D/W breakout → LTF bounce entry
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg

_R1_OFF = {"R1_DC_LOW4_3M_EMERGENCY_ENABLED": False}
_R2_OFF = {"WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED": False}
_FA_OFF = {"FROZEN_ACTIVATION_STOP_ENABLED": False}  # disable tradier_manage level stop

STOP_GRID: List[Tuple[str, SweepConfig]] = [
    # ── Control arms ──
    ("baseline_r1r2",             _make_cfg()),
    ("baseline_r1off",            _make_cfg(**_R1_OFF, **_FA_OFF)),
    ("baseline_r1r2off_nostop",   _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF)),
    # ── DC frozen stops — R1 off, R2 active (DC vs R2 race) ──
    ("r1off_dc4_5m",    _make_cfg(**_R1_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="5m",  DC_LOW_FROZEN_STOP_USE_4BAR=True)),
    ("r1off_dc_5m",     _make_cfg(**_R1_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="5m")),
    ("r1off_dc4_15m",   _make_cfg(**_R1_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="15m", DC_LOW_FROZEN_STOP_USE_4BAR=True)),
    ("r1off_dc_15m",    _make_cfg(**_R1_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="15m")),
    ("r1off_dc_1h",     _make_cfg(**_R1_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="1h")),
    ("r1off_dc_4h",     _make_cfg(**_R1_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="4h")),
    # ── DC frozen stops — R1+R2 off (pure stop, no velocity exit) ──
    ("r1r2off_dc4_5m",  _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="5m",  DC_LOW_FROZEN_STOP_USE_4BAR=True)),
    ("r1r2off_dc_5m",   _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="5m")),
    ("r1r2off_dc4_15m", _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="15m", DC_LOW_FROZEN_STOP_USE_4BAR=True)),
    ("r1r2off_dc_15m",  _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="15m")),
    ("r1r2off_dc_1h",   _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="1h")),
    ("r1r2off_dc_4h",   _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="4h")),
    # ── DC with absolute floor (backstop for runaway losers) ──
    ("r1r2off_dc_5m_fl8",  _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="5m",  DC_LOW_FROZEN_STOP_FLOOR_PCT=-8.0)),
    ("r1r2off_dc_15m_fl8", _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="15m", DC_LOW_FROZEN_STOP_FLOOR_PCT=-8.0)),
    ("r1r2off_dc_1h_fl8",  _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="1h",  DC_LOW_FROZEN_STOP_FLOOR_PCT=-8.0)),
    # ── BB frozen stops — R1 off, R2 active ──
    ("r1off_bb_lower_5m",   _make_cfg(**_R1_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="5m",  BB_FROZEN_STOP_FIELD="lower")),
    ("r1off_bb_lower_15m",  _make_cfg(**_R1_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="15m", BB_FROZEN_STOP_FIELD="lower")),
    ("r1off_bb_lower_1h",   _make_cfg(**_R1_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="1h",  BB_FROZEN_STOP_FIELD="lower")),
    ("r1off_bb_lower_4h",   _make_cfg(**_R1_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="4h",  BB_FROZEN_STOP_FIELD="lower")),
    ("r1off_bb_basis_5m",   _make_cfg(**_R1_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="5m",  BB_FROZEN_STOP_FIELD="basis")),
    ("r1off_bb_basis_15m",  _make_cfg(**_R1_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="15m", BB_FROZEN_STOP_FIELD="basis")),
    ("r1off_bb_basis_1h",   _make_cfg(**_R1_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="1h",  BB_FROZEN_STOP_FIELD="basis")),
    ("r1off_bb_basis_4h",   _make_cfg(**_R1_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="4h",  BB_FROZEN_STOP_FIELD="basis")),
    # ── BB frozen stops — R1+R2 off ──
    ("r1r2off_bb_lower_5m",   _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="5m",  BB_FROZEN_STOP_FIELD="lower")),
    ("r1r2off_bb_lower_15m",  _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="15m", BB_FROZEN_STOP_FIELD="lower")),
    ("r1r2off_bb_lower_1h",   _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="1h",  BB_FROZEN_STOP_FIELD="lower")),
    ("r1r2off_bb_lower_4h",   _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="4h",  BB_FROZEN_STOP_FIELD="lower")),
    ("r1r2off_bb_basis_5m",   _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="5m",  BB_FROZEN_STOP_FIELD="basis")),
    ("r1r2off_bb_basis_15m",  _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="15m", BB_FROZEN_STOP_FIELD="basis")),
    ("r1r2off_bb_basis_1h",   _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="1h",  BB_FROZEN_STOP_FIELD="basis")),
    ("r1r2off_bb_basis_4h",   _make_cfg(**_R1_OFF, **_R2_OFF, **_FA_OFF, BB_FROZEN_STOP_ENABLED=True, BB_FROZEN_STOP_TF="4h",  BB_FROZEN_STOP_FIELD="basis")),
    # ── Combined DC+floor best-guesses ──
    ("r1off_dc4_5m_fl8",  _make_cfg(**_R1_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="5m",  DC_LOW_FROZEN_STOP_USE_4BAR=True, DC_LOW_FROZEN_STOP_FLOOR_PCT=-8.0)),
    ("r1off_dc_15m_fl8",  _make_cfg(**_R1_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="15m", DC_LOW_FROZEN_STOP_FLOOR_PCT=-8.0)),
    ("r1off_dc_1h_fl8",   _make_cfg(**_R1_OFF, **_FA_OFF, DC_LOW_FROZEN_STOP_ENABLED=True, DC_LOW_FROZEN_STOP_TF="1h",  DC_LOW_FROZEN_STOP_FLOOR_PCT=-8.0)),
]

# ── Compute pool_sharpe from trade returns ────────────────────────────────────

def _pool_sharpe(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    import numpy as _np
    r = _np.array(returns, dtype=float)
    std = r.std()
    return float(r.mean() / std) if std > 1e-12 else 0.0

# ── Per-symbol worker ────────────────────────────────────────────────────────

def _run_one_sym(args):
    sym, side, mode, start_ts, grid_labels, grid_cfgs = args
    try:
        npz_cache = load_npz(sym, mode, start_ts=start_ts)
    except (FileNotFoundError, Exception) as e:
        return sym, {}
    results: Dict[str, List[float]] = {}
    for label, cfg in zip(grid_labels, grid_cfgs):
        try:
            _, rets, _ = simulate_one_symbol(sym, side, mode, cfg, start_ts=start_ts, _npz_cache=npz_cache)
            results[label] = rets
        except Exception:
            results[label] = []
    return sym, results

# ── Main sweep runner ────────────────────────────────────────────────────────

def discover_tradier_symbols() -> List[str]:
    """All stock tickers that have NPZs (no USDC/USDT suffix, no leading digit)."""
    return sorted(
        p.stem for p in NPZ_DIR.glob("*.npz")
        if not p.stem.endswith("USDC") and not p.stem.endswith("USDT")
        and not p.stem[0].isdigit()
    )

def run_stop_sweep(
    *,
    mode: str = "tradier",
    start: str = "2024-01-01",
    symbols: Optional[List[str]] = None,
    workers: int = 4,
    out_csv: Optional[Path] = None,
) -> Path:
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ts = int(start_dt.timestamp())
    n_years = (datetime.now(timezone.utc) - start_dt).total_seconds() / 86400.0 / 365.25

    syms = symbols or discover_tradier_symbols()
    n_syms = len(syms)
    n_variants = len(STOP_GRID)
    labels = [label for label, _ in STOP_GRID]
    cfgs = [cfg for _, cfg in STOP_GRID]

    ts_run = int(time.time())
    SWEEP_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = out_csv or (SWEEP_RESULTS_DIR / f"vec_stop_sweep_{ts_run}.csv")

    print(f"[vec_stop_sweep] mode={mode} syms={n_syms} variants={n_variants} start={start} workers={workers}")
    print(f"[vec_stop_sweep] output -> {out_path}")
    t0 = time.perf_counter()

    # All-variant returns per label
    variant_returns: Dict[str, List[float]] = {lbl: [] for lbl in labels}
    variant_wins: Dict[str, int] = {lbl: 0 for lbl in labels}
    variant_losses: Dict[str, int] = {lbl: 0 for lbl in labels}
    variant_sym_sharpes: Dict[str, List[float]] = {lbl: [] for lbl in labels}
    variant_gain_pct: Dict[str, float] = {lbl: 0.0 for lbl in labels}

    tasks = [(sym, "LONG", mode, start_ts, labels, cfgs) for sym in syms]
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_one_sym, t): t[0] for t in tasks}
        for fut in as_completed(futures):
            sym = futures[fut]
            done += 1
            try:
                _, sym_results = fut.result()
            except Exception as e:
                print(f"[vec_stop_sweep] ERROR {sym}: {e}", flush=True)
                continue
            for lbl in labels:
                rets = sym_results.get(lbl, [])
                variant_returns[lbl].extend(rets)
                wins = sum(1 for r in rets if r > 0)
                losses = sum(1 for r in rets if r <= 0)
                variant_wins[lbl] += wins
                variant_losses[lbl] += losses
                variant_gain_pct[lbl] += sum(rets)
                if rets:
                    variant_sym_sharpes[lbl].append(_pool_sharpe(rets))
            if done % 10 == 0 or done == n_syms:
                elapsed = time.perf_counter() - t0
                print(f"[vec_stop_sweep] {done}/{n_syms} syms done | {elapsed:.0f}s | "
                      f"baseline_r1r2 trades={len(variant_returns['baseline_r1r2'])}", flush=True)

    elapsed = time.perf_counter() - t0
    print(f"[vec_stop_sweep] compute done in {elapsed:.1f}s")

    # Write CSV
    fieldnames = [
        "pool_sharpe", "sym_sharpe", "avg_gain_trade", "gain_per_yr", "gain_sym_yr",
        "trades", "max_dd_pct", "n_syms", "years", "label",
        "wins", "losses", "win_rate", "gain_pct", "status", "verdict",
    ]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for lbl in labels:
            rets = variant_returns[lbl]
            n_trades = len(rets)
            ps = _pool_sharpe(rets)
            sym_sharpes = variant_sym_sharpes[lbl]
            ss = float(sum(sym_sharpes) / len(sym_sharpes)) if sym_sharpes else 0.0
            gain_pct = variant_gain_pct[lbl] * 100.0
            avg_gain = gain_pct / n_trades if n_trades > 0 else 0.0
            g_yr = gain_pct / n_years if n_years > 0 else 0.0
            g_sym_yr = gain_pct / n_syms / n_years if n_syms > 0 and n_years > 0 else 0.0
            wins = variant_wins[lbl]
            losses = variant_losses[lbl]
            wr = 100.0 * wins / n_trades if n_trades > 0 else 0.0
            # max_dd_pct: approximate as worst cumulative drawdown from returns
            import numpy as _np
            if rets:
                _cum = _np.cumsum(_np.array(rets)) * 100.0
                _peak = _np.maximum.accumulate(_cum)
                _dd = float((_cum - _peak).min())
            else:
                _dd = 0.0
            status = "ok" if n_trades > 0 else "no_trades"
            floor = n_syms >= 100 and n_years > 1.0 and all(
                len([r for r in variant_returns[lbl] if r != 0]) / n_syms >= 30
            )
            verdict = "DIAGNOSTIC"
            if floor and ps >= 1.0:
                verdict = "PROMOTE_CANDIDATE"
            elif floor and ps >= 0.5:
                verdict = "DIRECTIONAL"
            w.writerow({
                "pool_sharpe": round(ps, 4),
                "sym_sharpe": round(ss, 4),
                "avg_gain_trade": round(avg_gain, 4),
                "gain_per_yr": round(g_yr, 4),
                "gain_sym_yr": round(g_sym_yr, 4),
                "trades": n_trades,
                "max_dd_pct": round(_dd, 2),
                "n_syms": n_syms,
                "years": round(n_years, 3),
                "label": lbl,
                "wins": wins,
                "losses": losses,
                "win_rate": round(wr, 1),
                "gain_pct": round(gain_pct, 2),
                "status": status,
                "verdict": verdict,
            })

    print(f"[vec_stop_sweep] wrote {out_path}  ({n_variants} rows)")
    baseline = variant_returns.get("baseline_r1r2", [])
    print(f"[vec_stop_sweep] baseline_r1r2: trades={len(baseline)} pool_sharpe={_pool_sharpe(baseline):.4f}")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Vectorized frozen stop sweep — tradier")
    ap.add_argument("--mode", default="tradier")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--symbols", default="", help="Comma-separated override; default=all NPZ stocks")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--output", default="")
    ap.add_argument("--status", action="store_true", help="Print available symbols and exit")
    from vector_mandatory_coverage import add_coverage_claim_arguments, enforce_coverage_claim
    add_coverage_claim_arguments(ap)
    args = ap.parse_args()
    coverage_contract = enforce_coverage_claim(args, runner="run_vec_stop_sweep.py")
    print(f"V8_VECTOR_GROUND_RULE: {coverage_contract['coverage_status']} shortlist_sha256={coverage_contract['shortlist_sha256']}", flush=True)

    if args.status:
        syms = discover_tradier_symbols()
        print(f"Available tradier symbols with NPZ: {len(syms)}")
        print(", ".join(syms))
        return 0

    syms = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    run_stop_sweep(
        mode=args.mode,
        start=args.start,
        symbols=syms,
        workers=args.workers,
        out_csv=Path(args.output) if args.output else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
