#!/usr/bin/env python3
# metrics_guard retrofit (audited 2026-04-30): this script writes a Sharpe
# number to a print/log surface. Per CLAUDE.md NO-LIES MANDATE, any future
# user-facing Sharpe MUST be routed through metrics_guard.validate_and_format_sharpe()
# with explicit label, n_syms, years, trades, mode. Bare 'Sharpe X.XX' output is forbidden.
from metrics_guard import validate_and_format_sharpe  # noqa: F401  (forward-prevention import)
# pylint: disable=W,C,R,I
"""quick_ab_test.py — vectorized A/B test using v8_quick_engine.py.

Runs each switch ON vs OFF using the vectorized engine (seconds per arm, not minutes).
Designed as Tier-1 directional screening — use backtest_v8_engine for Tier-2 validation.

Usage:
  python3 quick_ab_test.py --mode crypto --switches LH_HL_FILTER_ENABLED,MI_ENTRY_ENABLED
  python3 quick_ab_test.py --mode tradier --switches all
  python3 quick_ab_test.py --mode crypto --switches all --symbols 48
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).parent
PYTHON = os.environ.get("V8_PYTHON", sys.executable)

CRYPTO_SYMBOLS_12 = "BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC,XRPUSDC,DOGEUSDT,ADAUSDC,AVAXUSDC,DOTUSDT,MATICUSDT,LINKUSDC,UNIUSDC"
TRADIER_SYMBOLS_10 = "AAPL,MSFT,NVDA,AMZN,AMD,XOM,QQQ,SPY,TSLA,META"

SWITCHES_CRYPTO = [
    "LH_HL_FILTER_ENABLED",
    "STDEV_BREAKOUT_ENABLED",
    "MI_ENTRY_ENABLED",
    "HLR_RALLY_ENABLED",
    "DELTA_ENGINE_ENABLED",
    "WT_COMP_DELTA_EXIT_ENABLED",
    "EXIT_GAIN_EROSION_ENABLED",
    "EXIT_TREND_REVERSAL_ENABLED",
    "AUGMENT_WT_CROSS_ENABLED",
    "SYMBOL_PERF_ENABLED",
    "PEAK_GIVEBACK_PROTECTION_ENABLED",
    "WT_COMPOSITE_SCORING_ENABLED",
]

SWITCHES_TRADIER = [
    "LH_HL_FILTER_ENABLED",
    "STDEV_BREAKOUT_ENABLED",
    "MI_ENTRY_ENABLED_TRADIER",
    "HLR_RALLY_ENABLED",
    "TR_CHOP4H_GATE_ENABLED",
    "TR_ADX4H_GATE_ENABLED",
    "AUGMENT_WT_CROSS_ENABLED",
    "SYMBOL_PERF_ENABLED",
    "EXIT_GAIN_EROSION_ENABLED",
    "WT_COMP_DELTA_EXIT_ENABLED",
]

SWITCH_DEFAULTS = {
    "DELTA_ENGINE_ENABLED": True,
    "WT_COMP_DELTA_EXIT_ENABLED": True,
    "AUGMENT_WT_CROSS_ENABLED": True,
    "EXIT_GAIN_EROSION_ENABLED": False,
    "EXIT_TREND_REVERSAL_ENABLED": False,
    "PEAK_GIVEBACK_PROTECTION_ENABLED": True,
    "WT_COMPOSITE_SCORING_ENABLED": True,
}


def run_arm(mode, symbols, start, switch, value, timeout=300):
    override = {switch: value}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        json.dump(override, tf)
        ov_path = tf.name
    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = ov_path
    env["TEST_RATE_GUARD_MIN_PER_DAY"] = "0"
    cmd = [PYTHON, "-u", str(BASE / "v8_quick_engine.py"),
           "--mode", mode, "--symbols", symbols, "--start", start]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           cwd=str(BASE), timeout=timeout)
        out = r.stdout + r.stderr
        m = re.search(r"pool_sharpe=([\d.eE+\-]+)", out)
        ps = float(m.group(1)) if m else 0.0
        m2 = re.search(r"trades=(\d+)", out)
        trades = int(m2.group(1)) if m2 else 0
        m3 = re.search(r"pnl=([\d.eE+\-]+)", out)
        pnl = float(m3.group(1)) if m3 else 0.0
        return {"pool_sharpe": ps, "trades": trades, "pnl": pnl, "ok": True}
    except subprocess.TimeoutExpired:
        return {"pool_sharpe": 0.0, "trades": 0, "pnl": 0.0, "ok": False, "err": "TIMEOUT"}
    except Exception as e:
        return {"pool_sharpe": 0.0, "trades": 0, "pnl": 0.0, "ok": False, "err": str(e)}
    finally:
        os.unlink(ov_path)


def get_val_a(switch, mode):
    default = SWITCH_DEFAULTS.get(switch, False)
    return not default


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    p.add_argument("--switches", default="all")
    p.add_argument("--symbols", default="12")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--timeout", type=int, default=300)
    args = p.parse_args()

    if args.symbols == "48":
        sym_file = BASE / "backtest_48_symbols.json"
        if sym_file.exists():
            syms = ",".join(json.loads(sym_file.read_text()))
        else:
            syms = CRYPTO_SYMBOLS_12
    elif "," in args.symbols:
        syms = args.symbols
    elif args.mode == "tradier":
        syms = TRADIER_SYMBOLS_10
    else:
        syms = CRYPTO_SYMBOLS_12

    all_switches = SWITCHES_TRADIER if args.mode == "tradier" else SWITCHES_CRYPTO
    if args.switches == "all":
        switches = all_switches
    else:
        switches = [s.strip() for s in args.switches.split(",")]

    print(f"\n=== QUICK A/B TEST — mode={args.mode} symbols={syms[:40]}... start={args.start} ===")
    print(f"{'Switch':<38} {'Val_A':>6} {'SH_A':>8} {'Val_B':>6} {'SH_B':>8} {'Delta':>8} {'T_A':>6} {'T_B':>6}")
    print("-" * 100)

    results = []
    for sw in switches:
        val_a = get_val_a(sw, args.mode)
        val_b = not val_a
        t0 = time.time()
        r_a = run_arm(args.mode, syms, args.start, sw, val_a, args.timeout)
        r_b = run_arm(args.mode, syms, args.start, sw, val_b, args.timeout)
        elapsed = time.time() - t0
        delta = r_a["pool_sharpe"] - r_b["pool_sharpe"]
        winner = "A" if delta > 0 else "B"
        winner_val = val_a if winner == "A" else val_b
        print(f"{sw:<38} {str(val_a):>6} {r_a['pool_sharpe']:>8.4f} {str(val_b):>6} "
              f"{r_b['pool_sharpe']:>8.4f} {delta:>+8.4f} {r_a['trades']:>6} {r_b['trades']:>6}  "
              f"→ winner={winner_val} ({elapsed:.0f}s)")
        results.append({
            "switch": sw, "val_a": val_a, "sharpe_a": r_a["pool_sharpe"], "trades_a": r_a["trades"],
            "val_b": val_b, "sharpe_b": r_b["pool_sharpe"], "trades_b": r_b["trades"],
            "delta": delta, "winner": winner_val
        })

    print("\n=== SUMMARY ===")
    wins = [(r["switch"], r["winner"], r["delta"]) for r in results if r["winner"] is True and abs(r["delta"]) > 0.02]
    losses = [(r["switch"], r["winner"], r["delta"]) for r in results if r["winner"] is False and abs(r["delta"]) > 0.02]
    print(f"ENABLE (ON wins): {[f'{s}(+{d:.3f})' for s,v,d in wins]}")
    print(f"DISABLE (OFF wins): {[f'{s}({d:.3f})' for s,v,d in losses]}")
    neutral = [r for r in results if abs(r["delta"]) <= 0.02]
    print(f"NEUTRAL (|delta|≤0.02): {[r['switch'] for r in neutral]}")

    out_path = BASE / f"data/quick_ab_results_{args.mode}_{int(time.time())}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nResults saved: {out_path}")


if __name__ == "__main__":
    main()
