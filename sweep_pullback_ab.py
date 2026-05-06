#!/usr/bin/env python3
"""
sweep_pullback_ab.py — A/B validation for REENTRY_PROFIT_PULLBACK_ENABLED.

Streams one NPZ symbol at a time to stay within S1 memory limits (existing
sweeps occupy ~15GB; each decompressed crypto NPZ is ~878MB so bulk loading
63 symbols would need 55GB — impossible on 30GB S1).

Strategy: for each symbol, run all 4 variants serially, accumulate trades,
del the IndicatorStore, gc.collect(). Peak memory ≈ 2GB per symbol.

After all symbols processed, compute canonical metrics from pooled trades and
write via metrics_guard.write_sharpe_row().

Results written to data/sweep_results/pullback_ab_<ts>.csv (4 rows).
Each row tagged [DIAGNOSTIC] if n_syms < 48 sample floor.

Usage (on S1):
    cd /home/niels/binance-sandbox
    nohup python sweep_pullback_ab.py > ~/logs/sweep_pullback_ab_$(date +%Y%m%d_%H%M).log 2>&1 < /dev/null &
"""

import asyncio
import gc
import logging
import traceback
import sys
import os
import time
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Do NOT set V8_SWEEP_MODE here — it replaces builtins.print at import time
# and silences all output from this script. The engine handles its own verbosity.
os.environ["V8_RATE_GUARD_DISABLED"] = "1"

import config
import metrics_guard

START_DATE = "2025-01-01"
ACCOUNT = "ang"
CAPITAL = 10000.0
MODE = "crypto"

# Validation scope: 2 symbols, 2025-01-01 start (~16 months).
# Real engine: 15.6 steps/sec × 231,816 steps = 4.1h per variant.
# 2 syms × 4 variants = ~33h → results by end of day tomorrow. [DIAGNOSTIC n_syms=2]
VALIDATION_SYMBOLS: list = [
    "BTCUSDC",   # highest volume, most representative
    "ETHUSDC",   # second major, different profile
]

PULLBACK_VARIANTS = [
    {
        "label": "baseline_no_pullback",
        "overrides": {
            "REENTRY_PROFIT_PULLBACK_ENABLED": False,
        },
    },
    {
        "label": "pullback_drop3pct_os10_vel",
        "overrides": {
            "REENTRY_PROFIT_PULLBACK_ENABLED": True,
            "REENTRY_PULLBACK_DROP_PCT": 3.0,
            "REENTRY_PULLBACK_WT3M_OS_THRESH": -10.0,
            "REENTRY_PULLBACK_REQUIRE_15M": True,
            "REENTRY_PULLBACK_REQUIRE_VEL": True,
            "REENTRY_PULLBACK_MIN_GAIN_OVERRIDE": 0.0,
        },
    },
    {
        "label": "pullback_drop5pct_os15_vel",
        "overrides": {
            "REENTRY_PROFIT_PULLBACK_ENABLED": True,
            "REENTRY_PULLBACK_DROP_PCT": 5.0,
            "REENTRY_PULLBACK_WT3M_OS_THRESH": -15.0,
            "REENTRY_PULLBACK_REQUIRE_15M": True,
            "REENTRY_PULLBACK_REQUIRE_VEL": True,
            "REENTRY_PULLBACK_MIN_GAIN_OVERRIDE": 0.0,
        },
    },
    {
        "label": "pullback_drop3pct_no15m",
        "overrides": {
            "REENTRY_PROFIT_PULLBACK_ENABLED": True,
            "REENTRY_PULLBACK_DROP_PCT": 3.0,
            "REENTRY_PULLBACK_WT3M_OS_THRESH": -10.0,
            "REENTRY_PULLBACK_REQUIRE_15M": False,
            "REENTRY_PULLBACK_REQUIRE_VEL": True,
            "REENTRY_PULLBACK_MIN_GAIN_OVERRIDE": 0.0,
        },
    },
]


def _apply_overrides(overrides: dict) -> None:
    for k, v in overrides.items():
        setattr(config, k, v)
        try:
            setattr(config.Config, k, v)
            for inst in list(getattr(config.Config, '_INSTANCES', [])):
                setattr(inst, k, v)
        except Exception:
            pass
    for mod_name in ('ez_manage', 'ez_positions_quick'):
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            cfg = getattr(mod, 'config', None)
            if cfg is not None:
                for k, v in overrides.items():
                    setattr(cfg, k, v)
        except Exception:
            pass


def _clear_pullback_state() -> None:
    try:
        import ez_reentry_pullback as _ppb
        _ppb._pullback_last_fire.clear()
    except Exception:
        pass


def _trades_to_metrics(trades: list, label: str) -> dict:
    if not trades:
        return {"label": label, "trades": 0, "pool_sharpe": 0.0, "sym_sharpe": 0.0,
                "avg_gain_trade": 0.0, "gain_per_yr": 0.0, "gain_sym_yr": 0.0,
                "max_dd_pct": 0.0, "n_syms": 0, "years": 0.0, "acc_gain_pct": 0.0}
    pnl = [float(t.get('pnl_pct', 0)) for t in trades if 'pnl_pct' in t]
    symbols = list({t.get('symbol', '') for t in trades if t.get('symbol')})
    n_syms = len(symbols)
    n_trades = len(pnl)
    if n_trades < 2 or n_syms < 1:
        return {"label": label, "trades": n_trades, "pool_sharpe": 0.0, "sym_sharpe": 0.0,
                "avg_gain_trade": 0.0, "gain_per_yr": 0.0, "gain_sym_yr": 0.0,
                "max_dd_pct": 0.0, "n_syms": n_syms, "years": 0.0, "acc_gain_pct": 0.0}

    import numpy as np
    pnl_arr = np.array(pnl)
    mean_r = float(np.mean(pnl_arr))
    std_r = float(np.std(pnl_arr))
    pool_sharpe = (mean_r / std_r) if std_r > 1e-9 else 0.0
    acc_gain = float(np.sum(pnl_arr))

    per_sym_sharpes = []
    for sym in symbols:
        sym_pnl = np.array([float(t.get('pnl_pct', 0)) for t in trades if t.get('symbol') == sym])
        if len(sym_pnl) >= 30:
            s = float(np.std(sym_pnl))
            ps = (float(np.mean(sym_pnl)) / s) if s > 1e-9 else 0.0
            per_sym_sharpes.append(min(5.0, max(-5.0, ps)))
    sym_sharpe = float(np.mean(per_sym_sharpes)) if per_sym_sharpes else 0.0

    ts_list = [float(t.get('exit_ts', t.get('entry_ts', 0))) for t in trades if t.get('exit_ts') or t.get('entry_ts')]
    if ts_list:
        years = (max(ts_list) - min(ts_list)) / (365.25 * 86400)
    else:
        years = (time.time() - 1640995200) / (365.25 * 86400)
    years = max(years, 0.01)

    gain_per_yr = acc_gain / years
    gain_sym_yr = acc_gain / max(n_syms, 1) / years
    avg_gain_trade = acc_gain / n_trades if n_trades else 0.0

    equity = np.cumsum(pnl_arr)
    rolling_max = np.maximum.accumulate(equity)
    dd = rolling_max - equity
    max_dd = float(np.max(dd)) if len(dd) else 0.0

    return {
        "label": label,
        "trades": n_trades,
        "pool_sharpe": pool_sharpe,
        "sym_sharpe": sym_sharpe,
        "avg_gain_trade": avg_gain_trade,
        "gain_per_yr": gain_per_yr,
        "gain_sym_yr": gain_sym_yr,
        "acc_gain_pct": acc_gain,
        "max_dd_pct": max_dd,
        "n_syms": n_syms,
        "years": years,
    }


CHECKPOINT_PATH = ROOT / "data" / "sweep_results" / "pullback_ab_checkpoint.json"


def _save_checkpoint(variant_trades: dict, completed: set) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "variant_trades": {k: v for k, v in variant_trades.items()},
        "completed": list(completed),
        "start_date": START_DATE,
        "symbols": VALIDATION_SYMBOLS,
    }
    tmp = CHECKPOINT_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f)
    tmp.rename(CHECKPOINT_PATH)
    print(f"[sweep_pullback_ab] Checkpoint saved: {CHECKPOINT_PATH}", flush=True)


def _load_checkpoint() -> tuple[dict, set]:
    if not CHECKPOINT_PATH.exists():
        return {v["label"]: [] for v in PULLBACK_VARIANTS}, set()
    try:
        with open(CHECKPOINT_PATH) as f:
            data = json.load(f)
        if data.get("start_date") != START_DATE or data.get("symbols") != VALIDATION_SYMBOLS:
            print(f"[sweep_pullback_ab] Checkpoint mismatch (different start_date/symbols) — ignoring", flush=True)
            return {v["label"]: [] for v in PULLBACK_VARIANTS}, set()
        variant_trades = {v["label"]: [] for v in PULLBACK_VARIANTS}
        for k, v in data.get("variant_trades", {}).items():
            if k in variant_trades:
                variant_trades[k] = v
        completed = set(tuple(x) for x in data.get("completed", []))
        print(f"[sweep_pullback_ab] Resuming from checkpoint: {len(completed)} (sym,variant) pairs done", flush=True)
        for sym_lbl in sorted(completed):
            print(f"[sweep_pullback_ab]   SKIP (checkpoint) {sym_lbl[0]}/{sym_lbl[1]}", flush=True)
        return variant_trades, completed
    except Exception as e:
        print(f"[sweep_pullback_ab] Checkpoint load failed ({e}) — starting fresh", flush=True)
        return {v["label"]: [] for v in PULLBACK_VARIANTS}, set()


async def main():
    from backtest_v8_engine import load_stores, run_simulation, get_npz_dir

    npz_dir, resolution = get_npz_dir(MODE)
    _crypto_quotes = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
    all_npz = sorted(npz_dir.glob("*.npz"))
    sym_paths = [
        (p.stem, p) for p in all_npz
        if any(p.stem.endswith(q) for q in _crypto_quotes)
        and (not VALIDATION_SYMBOLS or p.stem in VALIDATION_SYMBOLS)
    ]

    if not sym_paths:
        print("[sweep_pullback_ab] ERROR: no crypto NPZ files found", flush=True)
        sys.exit(1)

    variant_trades, completed = _load_checkpoint()
    sym_t0 = time.time()

    print(
        f"[sweep_pullback_ab] Streaming {len(sym_paths)} symbols one-at-a-time "
        f"(mode={MODE} start={START_DATE} variants={len(PULLBACK_VARIANTS)})",
        flush=True,
    )

    for i, (sym, npz_path) in enumerate(sym_paths):
        remaining_variants = [v for v in PULLBACK_VARIANTS if (sym, v["label"]) not in completed]
        if not remaining_variants:
            print(f"[sweep_pullback_ab] [{i+1}/{len(sym_paths)}] {sym} — all variants done (checkpoint)", flush=True)
            continue

        print(f"[sweep_pullback_ab] [{i+1}/{len(sym_paths)}] {sym} ({len(remaining_variants)} variants remaining) ...", flush=True)
        stores_single, _ = load_stores(MODE, {sym}, START_DATE)
        if not stores_single:
            print(f"[sweep_pullback_ab]   SKIP {sym} (not in NPZ or stale)", flush=True)
            continue

        for variant in remaining_variants:
            label = variant["label"]
            _apply_overrides(variant["overrides"])
            _clear_pullback_state()
            # Suppress per-bar logging during simulation — each bar fires dozens of
            # logger.critical/warning/info calls. setLevel on root doesn't work because
            # child loggers have their own levels. logging.disable() is the global kill.
            logging.disable(logging.CRITICAL)
            try:
                trades = await run_simulation(MODE, ACCOUNT, START_DATE, CAPITAL, stores_single, resolution)
            except asyncio.CancelledError:
                # run_simulation completes, then cancels its queue_task and does
                # `await queue_task` with only `except Exception` — CancelledError
                # (BaseException in Python 3.8+) escapes. Trades are already in
                # backtest_v8_engine._executed_trades (populated before the cancel).
                import backtest_v8_engine as _bte
                trades = list(getattr(_bte, '_executed_trades', []))
                logging.disable(logging.NOTSET)
                print(f"[sweep_pullback_ab]   {sym}/{label}: rescued {len(trades)} trades after CancelledError cleanup", flush=True)
            except Exception as e:
                trades = []
                logging.disable(logging.NOTSET)
                print(f"[sweep_pullback_ab]   ERROR {sym}/{label}: {e}\n{traceback.format_exc()}", flush=True)
            finally:
                logging.disable(logging.NOTSET)
            variant_trades[label].extend(trades or [])
            completed.add((sym, label))
            _save_checkpoint(variant_trades, completed)

        del stores_single
        gc.collect()

        elapsed = time.time() - sym_t0
        eta_s = elapsed / (i + 1) * (len(sym_paths) - i - 1)
        print(
            f"[sweep_pullback_ab]   done {sym} "
            f"[baseline={len([t for t in variant_trades['baseline_no_pullback'] if t.get('symbol')==sym])} trades] "
            f"elapsed={elapsed:.0f}s ETA={eta_s:.0f}s",
            flush=True,
        )

    print(f"\n[sweep_pullback_ab] All {len(sym_paths)} symbols processed. Computing metrics...", flush=True)

    results = []
    for variant in PULLBACK_VARIANTS:
        label = variant["label"]
        trades = variant_trades[label]
        metrics = _trades_to_metrics(trades, label)
        metrics["elapsed_s"] = time.time() - sym_t0
        results.append(metrics)
        print(
            f"[sweep_pullback_ab] {label}: "
            f"pool_sharpe={metrics['pool_sharpe']:.4f} "
            f"sym_sharpe={metrics['sym_sharpe']:.4f} "
            f"trades={metrics['trades']} "
            f"n_syms={metrics['n_syms']} "
            f"gain_per_yr={metrics['gain_per_yr']:.2f}% "
            f"dd={metrics['max_dd_pct']:.2f}% "
            f"years={metrics['years']:.2f}",
            flush=True,
        )

    # Write canonical CSV via metrics_guard
    ts = int(time.time())
    out_path = ROOT / "data" / "sweep_results" / f"pullback_ab_{ts}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    for m in results:
        row = {
            "iter": m["label"],
            "pool_sharpe": m["pool_sharpe"],
            "sym_sharpe": m["sym_sharpe"],
            "avg_gain_trade": m["avg_gain_trade"],
            "gain_per_yr": m["gain_per_yr"],
            "gain_sym_yr": m["gain_sym_yr"],
            "trades": m["trades"],
            "max_dd_pct": m["max_dd_pct"],
            "n_syms": m["n_syms"],
            "years": m["years"],
            "acc_gain_pct": m.get("acc_gain_pct", 0.0),
            "elapsed_s": m.get("elapsed_s", 0.0),
            "overrides_count": 1,
            "overrides_json": json.dumps({v["label"]: v["overrides"] for v in PULLBACK_VARIANTS if v["label"] == m["label"]}),
        }
        try:
            metrics_guard.write_sharpe_row(str(out_path), row)
            written += 1
        except Exception as e:
            print(f"[sweep_pullback_ab] metrics_guard refused {m['label']}: {e}", flush=True)

    print(f"\n[sweep_pullback_ab] Done. {written}/{len(results)} rows written to {out_path}", flush=True)
    print("\n=== SUMMARY ===", flush=True)
    baseline = next((r for r in results if "baseline" in r["label"]), None)
    for m in results:
        delta = f" (Δpool={m['pool_sharpe'] - baseline['pool_sharpe']:+.4f})" if baseline and m is not baseline else ""
        print(
            f"  {m['label']}: pool_sharpe={m['pool_sharpe']:.4f} | "
            f"sym_sharpe={m['sym_sharpe']:.4f} | "
            f"avg_gain={m['avg_gain_trade']:.3f}%/tr | "
            f"gain_yr={m['gain_per_yr']:.1f}%/yr | "
            f"dd={m['max_dd_pct']:.1f}% | "
            f"trades={m['trades']} | "
            f"n_syms={m['n_syms']} | "
            f"yrs={m['years']:.2f}"
            f"{delta}",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
