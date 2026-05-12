#!/usr/bin/env python3
"""
vec_sweep.py — Ultra-fast multi-process vectorized sweep runner using vec_engine_v1.py.

DESIGN:
  - Loads NPZ once per worker process (cached across all configs that process handles).
  - Runs configs in concurrent.futures.ProcessPoolExecutor (--workers N processes).
  - Uses vec_engine_v1.VecEngine.simulate() directly — no subprocess overhead.
  - Streams results to CSV via metrics_guard.write_sharpe_row() (NEVER bypassed).
  - All Sharpe values route through metrics_guard — CLAUDE.md NO-LIES MANDATE enforced.

PERFORMANCE:
  - Target: 100 configs × 10 syms × 16 months under 5 minutes.
  - Target: 1000 configs × 10 syms × 16 months under 60 minutes.
  - Memory: < 4 GB total with 8 workers (NPZ loaded once per worker, not per config).

VALIDATION GATES (CLAUDE.md NO-LIES MANDATE):
  - Every row includes all 9 canonical fields: pool_sharpe, sym_sharpe, avg_gain_trade,
    gain_per_yr, gain_sym_yr, trades, max_dd_pct, n_syms, years.
  - Sub-floor results tagged [DIAGNOSTIC ONLY · n_syms=X · years=Y].
  - NO banned column names (sharpe_annual, sharpe_yearly, etc.).
  - Rows written ONLY via metrics_guard.write_sharpe_row().
  - NO per-symbol BEST promotion (IMPOSTER BLOCK rule 1).

USAGE:
    # Quick validation (1 config, 3 symbols):
    python vec_sweep.py --mode tradier --symbols AMD,AMZN,AVGO --start 2025-01-01 \\
                        --tier live_default_baseline --workers 1 \\
                        --out /tmp/test.csv

    # 42-config grid (8 symbols):
    python vec_sweep.py --mode tradier \\
                        --symbols AMD,AMZN,AVGO,ARM,GOOGL,MSTR,NVDA,COP \\
                        --start 2025-01-01 --tier gr_entry_consensus_grid \\
                        --workers 4 --out /tmp/test_grid.csv

    # 1000+ config stress test:
    python vec_sweep.py --mode tradier --symbols AMD,AMZN,AVGO,ARM,GOOGL,MSTR,NVDA,COP \\
                        --start 2025-01-01 --tier mega_combo_v1 --workers 8 \\
                        --out data/sweep_results/vec_sweep_mega.csv

    # List available tiers:
    python vec_sweep.py --list-tiers

NOTES:
  - vec_engine_v1 results are Tier-1 shortlist only — NOT a substitute for
    backtest_v8_engine.py (Tier-2). Any config promoted from this sweep MUST
    be re-validated via backtest_v8_engine before touching live config.
  - All rows are tagged [VEC ONLY — UNVALIDATED] for this reason.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import platform
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Path setup — mirrors vec_engine_v1.py ─────────────────────────────
IS_SERVER = platform.system() == "Linux"
if IS_SERVER:
    BASE_PATH = Path("/home/niels/binance-sandbox")
else:
    BASE_PATH = Path("/Users/niels/Documents/binance")

sys.path.insert(0, str(BASE_PATH))

import metrics_guard  # MANDATORY — CLAUDE.md NO-LIES MANDATE
from vec_sweep_tiers import get_tier, list_tiers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vec_sweep")


# ───────────────────────────────────────────────────────────
# Worker globals — loaded once per worker process
# ───────────────────────────────────────────────────────────
_worker_engine = None
_worker_symbols: List[str] = []
_worker_start_ts: Optional[int] = None
_worker_end_ts: Optional[int] = None
_worker_mode: str = "tradier"


def _worker_init(
    mode: str,
    symbols: List[str],
    start_ts: Optional[int],
    end_ts: Optional[int],
    npz_dir: Optional[str],
) -> None:
    """Called once per worker process — creates VecEngine and pre-loads NPZ stores."""
    global _worker_engine, _worker_symbols, _worker_start_ts, _worker_end_ts, _worker_mode

    _worker_mode = mode
    _worker_symbols = symbols
    _worker_start_ts = start_ts
    _worker_end_ts = end_ts

    from vec_engine_v1 import VecEngine
    _worker_engine = VecEngine(mode=mode, npz_dir=npz_dir)

    t0 = time.perf_counter()
    loaded = 0
    for sym in symbols:
        store = _worker_engine._load_store(sym)
        if store is not None:
            loaded += 1
    elapsed = time.perf_counter() - t0
    log.debug(
        f"Worker {os.getpid()}: loaded {loaded}/{len(symbols)} NPZ stores in {elapsed:.2f}s"
    )


def _run_one_config(
    task: Tuple[int, str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Run a single config variant inside a worker process.

    Args:
        task: (config_idx, label, overrides_dict)

    Returns:
        canonical metric dict + label, config_idx, elapsed_s, overrides_json
    """
    config_idx, label, overrides = task

    from vec_engine_v1 import VecConfig

    cfg = VecConfig()
    if overrides:
        cfg = cfg.update_from_dict(overrides)

    t0 = time.perf_counter()
    result = _worker_engine.simulate(
        symbols=_worker_symbols,
        cfg=cfg,
        start_ts=_worker_start_ts,
        end_ts=_worker_end_ts,
    )
    elapsed = time.perf_counter() - t0

    result["label"] = label
    result["config_idx"] = config_idx
    result["elapsed_s"] = round(elapsed, 3)
    result["overrides_json"] = json.dumps(overrides)
    result["mode"] = _worker_mode
    result["engine"] = "vec_engine_v1"
    return result


# ───────────────────────────────────────────────────────────
# Row writer — routes through metrics_guard exclusively
# ───────────────────────────────────────────────────────────
def _write_result_row(
    result: Dict[str, Any],
    out_path: Path,
    mode: str,
) -> None:
    """Write one result row to CSV via metrics_guard.write_sharpe_row().

    Tags all rows [VEC ONLY — UNVALIDATED].
    Sub-floor results also get [DIAGNOSTIC ONLY · n_syms=X · years=Y] from metrics_guard.
    NEVER writes banned column names. NEVER bypasses metrics_guard.

    Raises metrics_guard.FakeMetricRefused on violation (caller logs and skips).
    """
    pool_sharpe = float(result.get("pool_sharpe") or 0.0)
    sym_sharpe = float(result.get("sym_sharpe") or 0.0)
    avg_gain_trade = float(result.get("avg_gain_trade") or 0.0)
    gain_per_yr = float(result.get("gain_per_yr") or 0.0)
    gain_sym_yr = float(result.get("gain_sym_yr") or 0.0)
    trades = int(result.get("trades") or 0)
    max_dd_pct = float(result.get("max_dd_pct") or 0.0)
    n_syms = int(result.get("n_syms") or 0)
    years = float(result.get("years") or 0.0)

    # Tag as VEC ONLY — results here are Tier-1 shortlist, NOT Tier-2 validated.
    # The [VEC ONLY — UNVALIDATED] tag must appear on every row per design contract.
    verdict_base = str(result.get("verdict", "DIAGNOSTIC"))
    vec_tag = "[VEC ONLY — UNVALIDATED]"
    verdict = f"{verdict_base} {vec_tag}" if vec_tag not in verdict_base else verdict_base

    row: Dict[str, Any] = {
        # ── 9 canonical columns (CLAUDE.md NO-LIES rule 2) ──────────────────
        "pool_sharpe": pool_sharpe,
        "sym_sharpe": sym_sharpe,
        "avg_gain_trade": avg_gain_trade,
        "gain_per_yr": gain_per_yr,
        "gain_sym_yr": gain_sym_yr,
        "trades": trades,
        "max_dd_pct": max_dd_pct,
        "n_syms": n_syms,
        "years": years,
        # ── Extra provenance fields (appended after canonical 9) ─────────────
        "label": result.get("label", ""),
        "config_idx": int(result.get("config_idx") or 0),
        "elapsed_s": float(result.get("elapsed_s") or 0.0),
        "overrides_json": result.get("overrides_json", "{}"),
        "acc_gain_pct": float(result.get("acc_gain_pct") or 0.0),
        "wins": int(result.get("wins") or 0),
        "losses": int(result.get("losses") or 0),
        "mode": result.get("mode", mode),
        "engine": result.get("engine", "vec_engine_v1"),
        "verdict": verdict,
        "note": result.get("note", ""),
        "ts_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # metrics_guard.write_sharpe_row:
    #   - validates all 9 canonical columns present
    #   - refuses banned column names (sharpe_annual, etc.)
    #   - tags sub-floor as DIAGNOSTIC automatically
    #   - raises FakeMetricRefused on any violation
    metrics_guard.write_sharpe_row(
        csv_path=out_path,
        row=row,
        mode="stocks" if mode == "tradier" else "crypto",
        append=True,
    )


# ───────────────────────────────────────────────────────────
# Main sweep runner
# ───────────────────────────────────────────────────────────
def run_sweep(
    mode: str,
    symbols: List[str],
    start_ts: Optional[int],
    end_ts: Optional[int],
    tier_name: str,
    workers: int,
    out_path: Path,
    npz_dir: Optional[str] = None,
) -> None:
    """Run the full tier sweep and stream results to out_path CSV.

    Args:
        mode: "crypto" or "tradier"
        symbols: list of symbols e.g. ["AMD", "AMZN"]
        start_ts: Unix timestamp UTC for simulation start (None = NPZ start)
        end_ts: Unix timestamp UTC for simulation end (None = NPZ end)
        tier_name: name from vec_sweep_tiers.TIER_REGISTRY
        workers: parallel worker processes (1 = single-process, no fork overhead)
        out_path: CSV output path (written exclusively via metrics_guard)
        npz_dir: override NPZ directory (default: BASE_PATH/backtest_v8/indicators)
    """
    configs = get_tier(tier_name)
    n_configs = len(configs)

    log.info(
        f"vec_sweep START: tier={tier_name!r} mode={mode} n_syms={len(symbols)} "
        f"n_configs={n_configs} workers={workers}"
    )
    log.info(f"  symbols: {', '.join(symbols)}")
    log.info(f"  output:  {out_path}")

    if n_configs == 0:
        log.error(f"Tier {tier_name!r} returned 0 configs — nothing to run.")
        return

    tasks: List[Tuple[int, str, Dict[str, Any]]] = [
        (i, label, overrides) for i, (label, overrides) in enumerate(configs)
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)

    t_start = time.perf_counter()
    n_done = 0
    n_ok = 0
    n_err = 0
    last_log_n = 0
    log_interval = max(1, n_configs // 10)

    def _log_progress():
        elapsed = time.perf_counter() - t_start
        rate = n_done / elapsed if elapsed > 0 else 0.0
        eta = (n_configs - n_done) / rate if rate > 0 else float("inf")
        log.info(
            f"Progress {n_done}/{n_configs}  ok={n_ok}  err={n_err}  "
            f"rate={rate:.2f}/s  eta={eta:.0f}s"
        )

    # 🚩 NO-OP DETECTOR (2026-05-12 user mandate): if 2+ configs produce IDENTICAL
    # (pool_sharpe, trades, max_dd) over the same window, ABORT — that's a silent
    # no-op pattern that wastes compute and lies about knob effects.
    _result_fingerprints: Dict[Tuple[float, int, float], str] = {}
    _DUP_ABORT_THRESHOLD = 2  # abort on 2nd identical result

    def _check_duplicate(label: str, result: Dict[str, Any]) -> Optional[str]:
        """Return offending-other-label if result is a duplicate of a prior one."""
        try:
            ps = round(float(result.get("pool_sharpe", 0.0) or 0.0), 6)
            tr = int(result.get("trades", 0) or 0)
            dd = round(float(result.get("max_dd_pct", 0.0) or 0.0), 4)
        except (TypeError, ValueError):
            return None
        fp = (ps, tr, dd)
        prior = _result_fingerprints.get(fp)
        if prior is not None and prior != label:
            return prior
        _result_fingerprints[fp] = label
        return None

    if workers <= 1:
        # ── Single-process path ─────────────────────────────────────────
        # Avoids ProcessPoolExecutor overhead — ideal for small tiers / debugging.
        _worker_init(mode, symbols, start_ts, end_ts, npz_dir)
        for task in tasks:
            try:
                result = _run_one_config(task)
                _write_result_row(result, out_path, mode)
                n_ok += 1
                dup = _check_duplicate(task[1], result)
                if dup is not None:
                    log.error(
                        f"🛑 NO-OP DETECTED — IDENTICAL OUTPUT: '{task[1]}' "
                        f"== '{dup}' "
                        f"(pool_sharpe={result.get('pool_sharpe')} "
                        f"trades={result.get('trades')} "
                        f"max_dd={result.get('max_dd_pct')}). "
                        f"Aborting sweep per user mandate 2026-05-12."
                    )
                    raise SystemExit(2)
            except metrics_guard.FakeMetricRefused as exc:
                log.error(f"METRICS_GUARD REFUSED [{task[1]}]: {exc}")
                n_err += 1
            except Exception as exc:
                log.error(f"ERROR [{task[1]}]: {exc}", exc_info=True)
                n_err += 1
            n_done += 1
            if n_done - last_log_n >= log_interval:
                _log_progress()
                last_log_n = n_done
    else:
        # ── Multi-process path ──────────────────────────────────────────
        # Each worker process gets _worker_init called once, loading all NPZ stores.
        # Results streamed back to master process as they complete.
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(mode, symbols, start_ts, end_ts, npz_dir),
        ) as pool:
            future_map = {pool.submit(_run_one_config, task): task for task in tasks}
            for future in as_completed(future_map):
                task = future_map[future]
                try:
                    result = future.result()
                    _write_result_row(result, out_path, mode)
                    n_ok += 1
                    dup = _check_duplicate(task[1], result)
                    if dup is not None:
                        log.error(
                            f"🛑 NO-OP DETECTED — IDENTICAL OUTPUT: '{task[1]}' "
                            f"== '{dup}' "
                            f"(pool_sharpe={result.get('pool_sharpe')} "
                            f"trades={result.get('trades')} "
                            f"max_dd={result.get('max_dd_pct')}). "
                            f"Aborting sweep per user mandate 2026-05-12."
                        )
                        for fut2 in future_map:
                            fut2.cancel()
                        pool.shutdown(wait=False, cancel_futures=True)
                        raise SystemExit(2)
                except metrics_guard.FakeMetricRefused as exc:
                    log.error(f"METRICS_GUARD REFUSED [{task[1]}]: {exc}")
                    n_err += 1
                except Exception as exc:
                    log.error(f"ERROR [{task[1]}]: {exc}", exc_info=True)
                    n_err += 1
                n_done += 1
                if n_done - last_log_n >= log_interval:
                    _log_progress()
                    last_log_n = n_done

    elapsed_total = time.perf_counter() - t_start
    rate = n_done / elapsed_total if elapsed_total > 0 else 0.0

    log.info(
        f"SWEEP DONE: {n_done}/{n_configs} configs in {elapsed_total:.1f}s "
        f"({rate:.2f} cfg/s)  ok={n_ok}  err={n_err}  out={out_path}"
    )
    if n_err > 0:
        log.warning(
            f"{n_err} config(s) failed — METRICS_GUARD refused or exception. "
            f"These were NOT written to the output CSV. Check logs above."
        )

    _print_top_results(out_path, n=5)


def _print_top_results(csv_path: Path, n: int = 5) -> None:
    """Log top-N rows by pool_sharpe from the output CSV (informational only)."""
    if not csv_path.exists():
        return
    try:
        rows = []
        with csv_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    float(row.get("pool_sharpe") or 0)
                    rows.append(row)
                except (ValueError, TypeError):
                    pass
        if not rows:
            return
        rows_sorted = sorted(
            rows, key=lambda r: float(r.get("pool_sharpe") or 0), reverse=True
        )
        log.info(f"Top {min(n, len(rows_sorted))} results by pool_sharpe:")
        for r in rows_sorted[:n]:
            ps = float(r.get("pool_sharpe") or 0)
            ss = float(r.get("sym_sharpe") or 0)
            ag = float(r.get("avg_gain_trade") or 0)
            tr = int(r.get("trades") or 0)
            dd = float(r.get("max_dd_pct") or 0)
            ns = int(r.get("n_syms") or 0)
            yr = float(r.get("years") or 0)
            lbl = r.get("label", "?")
            log.info(
                f"  [{lbl}] pool_sharpe={ps:+.4f} sym_sharpe={ss:+.4f} "
                f"avg_gain={ag:.2f}%/trade trades={tr} dd={dd:.1f}% "
                f"n_syms={ns} years={yr:.2f}"
            )
    except Exception as exc:
        log.debug(f"_print_top_results error: {exc}")


# ───────────────────────────────────────────────────────────
# CLI
# ───────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="vec_sweep.py — ultra-fast multi-process vectorized sweep runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--mode", choices=["crypto", "tradier"], default="tradier",
        help="Mode: crypto (3m base TF) or tradier (5m base TF)"
    )
    ap.add_argument(
        "--symbols", default="",
        help="Comma-separated symbol list e.g. AMD,AMZN,AVGO"
    )
    ap.add_argument(
        "--symbols-file", default="",
        help="JSON file with list or dict of symbols (overrides --symbols)"
    )
    ap.add_argument(
        "--start", default="",
        help="Start date YYYY-MM-DD (UTC). Default = NPZ start."
    )
    ap.add_argument(
        "--end", default="",
        help="End date YYYY-MM-DD (UTC). Default = latest bar in NPZ."
    )
    ap.add_argument(
        "--tier", default="live_default_baseline",
        help=f"Tier name. Available: {', '.join(list_tiers())}"
    )
    ap.add_argument(
        "--workers", type=int, default=4,
        help="Parallel worker processes (default 4; use 1 for debugging)"
    )
    ap.add_argument(
        "--out", default="",
        help="Output CSV path. Default: data/sweep_results/vec_sweep_<tier>_<ts>.csv"
    )
    ap.add_argument(
        "--npz-dir", default="",
        help="Override NPZ directory (default: BASE_PATH/backtest_v8/indicators)"
    )
    ap.add_argument(
        "--list-tiers", action="store_true",
        help="List available tier names with config counts and exit"
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Show tier config list without running the sweep"
    )
    ap.add_argument(
        "--debug", action="store_true",
        help="Enable DEBUG logging"
    )
    args = ap.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # ── --list-tiers ─────────────────────────────────────────
    if args.list_tiers:
        print("Available tiers:")
        for t in list_tiers():
            cfgs = get_tier(t)
            print(f"  {t}: {len(cfgs)} configs")
        return

    # ── Resolve symbols ───────────────────────────────────────
    symbols: List[str] = []
    if args.symbols_file:
        fp = Path(args.symbols_file)
        if not fp.exists():
            log.error(f"--symbols-file not found: {fp}")
            sys.exit(1)
        data = json.loads(fp.read_text())
        if isinstance(data, list):
            symbols = [str(s).strip().upper() for s in data if s]
        elif isinstance(data, dict):
            symbols = [str(s).strip().upper() for s in data.keys()]
        else:
            log.error("--symbols-file must contain a JSON list or object.")
            sys.exit(1)
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    if not symbols:
        log.error(
            "No symbols specified. Use --symbols AMD,AMZN,AVGO "
            "or --symbols-file path/to/symbols.json"
        )
        sys.exit(1)

    # ── Resolve timestamps ────────────────────────────────────
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    if args.start:
        try:
            start_ts = int(datetime.strptime(args.start, "%Y-%m-%d").timestamp())
        except ValueError:
            log.error(f"Invalid --start: {args.start!r}  Use YYYY-MM-DD.")
            sys.exit(1)
    if args.end:
        try:
            end_ts = int(datetime.strptime(args.end, "%Y-%m-%d").timestamp())
        except ValueError:
            log.error(f"Invalid --end: {args.end!r}  Use YYYY-MM-DD.")
            sys.exit(1)

    # ── Resolve output path ───────────────────────────────────
    ts_str = str(int(time.time()))
    if args.out:
        out_path = Path(args.out)
    else:
        out_path = (
            BASE_PATH / "data" / "sweep_results"
            / f"vec_sweep_{args.tier}_{ts_str}.csv"
        )

    # ── Resolve NPZ dir ───────────────────────────────────────
    npz_dir: Optional[str] = args.npz_dir if args.npz_dir else None

    # ── Validate tier ─────────────────────────────────────────
    try:
        configs = get_tier(args.tier)
    except KeyError as exc:
        log.error(str(exc))
        sys.exit(1)

    # ── --dry-run ─────────────────────────────────────────────
    if args.dry_run:
        print(f"DRY RUN: tier={args.tier}  n_configs={len(configs)}  symbols={symbols}")
        show_n = min(10, len(configs))
        for i, (label, overrides) in enumerate(configs[:show_n]):
            print(f"  [{i:4d}] {label}: {json.dumps(overrides)}")
        if len(configs) > show_n:
            print(f"  ... ({len(configs) - show_n} more)")
        return

    # ── IMPOSTER BLOCK sanity check ───────────────────────────
    # Single-symbol runs: always DIAGNOSTIC. Warn loudly.
    if len(symbols) < 2:
        log.warning(
            "IMPOSTER BLOCK WARNING: single-symbol run (n_syms=1). "
            "Results will be tagged [DIAGNOSTIC ONLY · n_syms=1]. "
            "Never use single-symbol results for live config changes per CLAUDE.md."
        )

    # ── Run ───────────────────────────────────────────────────
    run_sweep(
        mode=args.mode,
        symbols=symbols,
        start_ts=start_ts,
        end_ts=end_ts,
        tier_name=args.tier,
        workers=args.workers,
        out_path=out_path,
        npz_dir=npz_dir,
    )


if __name__ == "__main__":
    main()
