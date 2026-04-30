"""Parse real-engine retest results and update SNAPSHOT_INFO.md files + print report.

Usage:
  python3 update_snapshot_real_sharpe.py --mode crypto  (reads from data/retest_real_sharpe/)
  python3 update_snapshot_real_sharpe.py --mode tradier
  python3 update_snapshot_real_sharpe.py --both
"""
# metrics_guard retrofit (audited 2026-04-30): this script writes a Sharpe
# number to a print/log surface. Per CLAUDE.md NO-LIES MANDATE, any future
# user-facing Sharpe MUST be routed through metrics_guard.validate_and_format_sharpe()
# with explicit label, n_syms, years, trades, mode. Bare 'Sharpe X.XX' output is forbidden.
from metrics_guard import validate_and_format_sharpe  # noqa: F401  (forward-prevention import)
import argparse, json, sys
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent


def load_latest_result(mode):
    result_dir = BASE / "data" / "retest_real_sharpe"
    files = sorted(result_dir.glob(f"retest_{mode}_*.json"))
    if not files:
        return None, None
    latest = files[-1]
    return latest, json.loads(latest.read_text())


SNAPSHOT_LABEL_MAP = {
    "BASELINE_crypto":       "pre_fullsweep_pool_sharpe_20260421_032035",
    "le_dynamic_v2_crypto":  "le_dynamic_v2_baseline_20260420_2156",
    "c08_winner_crypto":     "c08_crypto_winner_20260421_034137",
    "BASELINE_tradier":      "pre_fullsweep_pool_sharpe_20260421_032035",
    "le_dynamic_v2_tradier": "le_dynamic_v2_baseline_20260420_2156",
    "t20_winner_tradier":    "t20_tradier_winner_20260421_034144",
}

QUICK_ENGINE_CLAIMED = {
    "BASELINE_crypto":       {"pool_sharpe": "unknown (pre_fullsweep baseline)", "note": ""},
    "le_dynamic_v2_crypto":  {"pool_sharpe": 8.007, "wr": 99.4, "trades": 2465, "syms": 49},
    "c08_winner_crypto":     {"pool_sharpe": 0.59, "gain": 13144, "dd": 11.1, "trades": 14769, "syms": 50},
    "BASELINE_tradier":      {"pool_sharpe": "unknown (pre_fullsweep baseline)", "note": ""},
    "le_dynamic_v2_tradier": {"pool_sharpe": 6.577, "wr": 75.9, "trades": 582, "syms": 262},
    "t20_winner_tradier":    {"pool_sharpe": 0.13, "gain": 6707, "dd": 34.1, "trades": 23282, "syms": 128},
}


def print_report(mode, results, result_file):
    print(f"\n{'='*90}", flush=True)
    print(f"REAL ENGINE (backtest_v8_engine.py Tier 2) RESULTS — {mode.upper()}", flush=True)
    print(f"Source: {result_file}", flush=True)
    print(f"{'='*90}", flush=True)
    print(f"{'Config':<30} {'REAL_sharpe_pt':>14} {'REAL_gain%':>11} {'REAL_closes':>12} {'REAL_WR%':>9} | {'QUICK_sharpe':>12} {'QUICK_WR%':>10}", flush=True)
    print("-"*90, flush=True)
    for r in results:
        label = r.get("label", "?")
        claimed = QUICK_ENGINE_CLAIMED.get(label, {})
        if "error" in r:
            print(f"{label:<30} ERROR: {r['error']}", flush=True)
            continue
        real_sharpe = r.get("sharpe_per_trade", 0.0)
        real_gain = r.get("gain_pct", 0.0)
        real_closes = r.get("closes", 0)
        real_wr = r.get("wr_pct", 0.0)
        quick_sharpe = claimed.get("pool_sharpe", "?")
        quick_wr = claimed.get("wr", "?")
        ratio = ""
        if isinstance(quick_sharpe, (int, float)) and real_sharpe != 0:
            ratio = f"  [{quick_sharpe/real_sharpe:.1f}x lie]"
        print(f"{label:<30} {real_sharpe:>14.4f} {real_gain:>11.1f} {real_closes:>12} {real_wr:>9.1f} | "
              f"{str(quick_sharpe):>12} {str(quick_wr):>10}{ratio}", flush=True)
    print(f"\nNOTE: sharpe_pt = mean/std trade returns pooled (CLAUDE.md rule #4). "
          f"sharpe_annual column NOT shown (BANNED per rule #3).", flush=True)


def rsync_result_from_server(mode):
    import subprocess
    result_dir = BASE / "data" / "retest_real_sharpe"
    if mode == "crypto":
        server = "s1-int"
    else:
        server = "s2-int"
    remote = f"{server}:/home/niels/binance-sandbox/data/retest_real_sharpe/retest_{mode}_*.json"
    cmd = ["rsync", "-az", remote, str(result_dir) + "/"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"rsync from {server} OK", flush=True)
    else:
        print(f"rsync from {server} failed: {result.stderr}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier", "both"], default="both")
    ap.add_argument("--rsync", action="store_true", help="Pull latest results from servers first")
    args = ap.parse_args()

    modes = ["crypto", "tradier"] if args.mode == "both" else [args.mode]

    for mode in modes:
        if args.rsync:
            rsync_result_from_server(mode)
        result_file, results = load_latest_result(mode)
        if not results:
            print(f"No results yet for mode={mode}. Servers still running.", flush=True)
            continue
        print_report(mode, results, result_file)


if __name__ == "__main__":
    main()
