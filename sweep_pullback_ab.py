#!/usr/bin/env python3
"""
sweep_pullback_ab.py — A/B validation for REENTRY_PROFIT_PULLBACK_ENABLED.

Runs backtest_v8_engine.run_simulation twice (baseline OFF / pullback ON) on
the full crypto NPZ universe, then reports canonical metrics via metrics_guard.

Results written to data/sweep_results/pullback_ab_<ts>.csv (2 rows).
Each row is validated via metrics_guard.write_sharpe_row() — if sample floor
is not met the row gets [DIAGNOSTIC] tag automatically.

Usage (on S1):
    cd /home/niels/binance-sandbox
    nohup python sweep_pullback_ab.py > ~/logs/sweep_pullback_ab_$(date +%Y%m%d_%H%M).log 2>&1 < /dev/null &
"""

import asyncio
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

def _log(msg: str) -> None:
    sys.stderr.write(f"[sweep_pullback_ab] {msg}\n")
    sys.stderr.flush()

START_DATE = "2022-01-01"
ACCOUNT = "ang"
CAPITAL = 10000.0
MODE = "crypto"

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


async def main():
    from backtest_v8_engine import load_stores, run_simulation

    print(f"[sweep_pullback_ab] Loading NPZ for {MODE} start={START_DATE}", flush=True)
    stores, resolution = load_stores(MODE, None, START_DATE)
    if not stores:
        print("[sweep_pullback_ab] ERROR: no NPZ data loaded", flush=True)
        sys.exit(1)
    print(f"[sweep_pullback_ab] Loaded {len(stores)} symbols", flush=True)

    results = []
    for variant in PULLBACK_VARIANTS:
        label = variant["label"]
        overrides = variant["overrides"]
        print(f"\n[sweep_pullback_ab] Running variant: {label}", flush=True)
        print(f"  overrides: {json.dumps(overrides)}", flush=True)
        _apply_overrides(overrides)
        t0 = time.time()
        try:
            trades = await run_simulation(MODE, ACCOUNT, START_DATE, CAPITAL, stores, resolution)
        except Exception as e:
            print(f"[sweep_pullback_ab] ERROR in {label}: {e}", flush=True)
            trades = []
        elapsed = time.time() - t0
        metrics = _trades_to_metrics(trades or [], label)
        metrics["elapsed_s"] = elapsed
        results.append(metrics)
        print(
            f"[sweep_pullback_ab] {label}: "
            f"pool_sharpe={metrics['pool_sharpe']:.4f} "
            f"sym_sharpe={metrics['sym_sharpe']:.4f} "
            f"trades={metrics['trades']} "
            f"n_syms={metrics['n_syms']} "
            f"gain_per_yr={metrics['gain_per_yr']:.2f}% "
            f"dd={metrics['max_dd_pct']:.2f}% "
            f"years={metrics['years']:.2f} "
            f"elapsed={elapsed:.0f}s",
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
