"""Run all snapshot configs through backtest_v8_engine.py (REAL engine, Tier 2).
Collects honest sharpe_per_trade numbers vs the lying quick-engine (Tier 1) numbers.

Usage (on S1 for crypto):
  python3 retest_real_sharpe_runner.py --mode crypto

Usage (on S2 for tradier):
  python3 retest_real_sharpe_runner.py --mode tradier
"""
import argparse, json, os, subprocess, sys, time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent


def run_backtest(mode, account, start, symbols_arg, override_file, label, python_bin="python3"):
    env = os.environ.copy()
    if override_file:
        from stock_v8_override_contract import establish_stock_v8_override
        establish_stock_v8_override(env, override_file)
    cmd = [python_bin, str(BASE / "backtest_v8_engine.py"),
           "--mode", mode,
           "--account", account,
           "--start", start]
    if symbols_arg:
        cmd += ["--symbols", symbols_arg]
    print(f"\n{'='*60}", flush=True)
    print(f"RUNNING: {label}", flush=True)
    print(f"  override: {override_file}", flush=True)
    print(f"  cmd: {' '.join(cmd[:6])}... symbols={symbols_arg[:40] if symbols_arg else 'ALL'}", flush=True)
    t0 = time.time()
    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=7200)
        elapsed = time.time() - t0
        # Parse V8_RESULT line
        v8_result = {}
        for line in (result.stdout + result.stderr).splitlines():
            if line.startswith("V8_RESULT:") and "sharpe_pt=" in line:
                parts = dict(p.split("=") for p in line[10:].split() if "=" in p)
                v8_result = {
                    "sharpe_per_trade": float(parts.get("sharpe_pt", 0)),
                    "sharpe_annual_BANNED": float(parts.get("sharpe_ann", 0)),
                    "gain_pct": float(parts.get("gain_pct", 0)),
                    "closes": int(parts.get("closes", 0)),
                    "wins": int(parts.get("wins", 0)),
                    "losses": int(parts.get("losses", 0)),
                    "wr_pct": round(int(parts.get("wins", 0)) / max(1, int(parts.get("closes", 0))) * 100, 1),
                }
                break
        # Also capture last V8_RESULT_LIVE if present (more verbose)
        for line in (result.stdout + result.stderr).splitlines():
            if "V8_RESULT_LIVE:" in line and "sharpe_pt=" in line:
                parts = dict(p.split("=") for p in line.split("V8_RESULT_LIVE:")[1].split() if "=" in p)
                v8_result["sharpe_per_trade"] = float(parts.get("sharpe_pt", v8_result.get("sharpe_per_trade", 0)))
                v8_result["gain_pct"] = float(parts.get("gain_pct", v8_result.get("gain_pct", 0)))
                break
        return {
            "label": label,
            "mode": mode,
            "start": start,
            "override_file": str(override_file) if override_file else None,
            "elapsed_s": round(elapsed, 1),
            "returncode": result.returncode,
            **v8_result,
            "stdout_tail": (result.stdout + result.stderr)[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {"label": label, "error": "TIMEOUT after 7200s"}
    except Exception as e:
        return {"label": label, "error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["crypto", "tradier"])
    ap.add_argument("--quick", action="store_true", help="Use subset of symbols for fast sanity check")
    ap.add_argument("--full", action="store_true", help="Use full symbol set (overrides --quick)")
    args = ap.parse_args()

    IS_SERVER = sys.platform == "linux"
    if IS_SERVER:
        python_bin = ("/home/niels/.conda/envs/binance_env/bin/python" if args.mode == "crypto"
                      else "/home/niels/miniconda3/envs/binance_env/bin/python")
        base_path = Path("/home/niels/binance-sandbox")
        npz_dir = str(base_path / "backtest_v8" / "indicators")
    else:
        python_bin = "/opt/anaconda3/envs/binance_env/bin/python"
        base_path = Path("/Users/niels/Documents/binance")
        npz_dir = ""

    results_dir = base_path / "data" / "retest_real_sharpe"
    results_dir.mkdir(parents=True, exist_ok=True)
    override_dir = base_path / "data" / "retest_real_sharpe"

    if args.mode == "crypto":
        account = "ang"
        start = "2022-01-01"
        all_syms_file = base_path / "backtest_48_symbols.json"
        if all_syms_file.exists():
            all_syms = json.loads(all_syms_file.read_text())
        else:
            all_syms = []
        quick_syms = all_syms[:12] if all_syms else []
        full_syms = all_syms[:50] if all_syms else []
        if args.full:
            syms = full_syms
        elif args.quick:
            syms = quick_syms
        else:
            syms = all_syms[:24]  # default: 24 symbols
        symbols_arg = ",".join(syms)

        configs = [
            ("BASELINE_crypto", None),
            ("le_dynamic_v2_crypto", override_dir / "override_le_dynamic_v2_crypto.json"),
            ("c08_winner_crypto", base_path / "snapshots" / "c08_crypto_winner_20260421_034137" / "overrides_c08_crypto_winner.json"),
        ]

    else:  # tradier
        account = "trb"
        start = "2024-01-01"
        all_syms_file = base_path / "backtest_tradier_symbols.json"
        if all_syms_file.exists():
            all_syms = json.loads(all_syms_file.read_text())
        else:
            all_syms = []
        quick_syms = all_syms[:24] if all_syms else []
        full_syms = all_syms  # use all for tradier
        if args.full:
            syms = full_syms
        elif args.quick:
            syms = quick_syms
        else:
            syms = all_syms[:48]  # default: 48 symbols
        symbols_arg = ",".join(syms)

        configs = [
            ("BASELINE_tradier", None),
            ("le_dynamic_v2_tradier", override_dir / "override_le_dynamic_v2_tradier.json"),
            ("t20_winner_tradier", base_path / "snapshots" / "t20_tradier_winner_20260421_034144" / "overrides_t20_tradier_winner.json"),
        ]

    print(f"Mode: {args.mode}  Symbols: {len(syms)}  Start: {start}  Account: {account}", flush=True)
    print(f"Running {len(configs)} configs...", flush=True)

    results = []
    for label, override_file in configs:
        if override_file and not Path(override_file).exists():
            print(f"SKIP {label}: override file not found: {override_file}", flush=True)
            results.append({"label": label, "error": "override_file_missing"})
            continue
        r = run_backtest(args.mode, account, start, symbols_arg, override_file, label, python_bin)
        results.append(r)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        print(f"\nRESULT [{label}]: sharpe_pt={r.get('sharpe_per_trade', 'ERR'):.4f}  "
              f"gain={r.get('gain_pct', 'ERR'):.1f}%  "
              f"closes={r.get('closes', '?')}  WR={r.get('wr_pct', '?')}%  "
              f"elapsed={r.get('elapsed_s', '?')}s", flush=True)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_file = results_dir / f"retest_{args.mode}_{ts}.json"
    out_file.write_text(json.dumps(results, indent=2))
    print(f"\nResults saved to: {out_file}", flush=True)

    # Summary table
    print("\n" + "="*80, flush=True)
    print("SUMMARY (REAL backtest_v8_engine.py Tier 2 results)", flush=True)
    print("="*80, flush=True)
    print(f"{'Label':<35} {'Sharpe_pt':>10} {'Gain%':>8} {'Closes':>7} {'WR%':>6} {'secs':>6}", flush=True)
    print("-"*80, flush=True)
    for r in results:
        if "error" in r:
            print(f"{r['label']:<35} ERROR: {r['error']}", flush=True)
        else:
            print(f"{r['label']:<35} {r.get('sharpe_per_trade', 0):>10.4f} "
                  f"{r.get('gain_pct', 0):>8.1f} "
                  f"{r.get('closes', 0):>7} "
                  f"{r.get('wr_pct', 0):>6.1f} "
                  f"{r.get('elapsed_s', 0):>6.0f}", flush=True)
    print("\nNOTE: sharpe_pt = mean/std of per-trade returns (pool across all symbols).", flush=True)
    print("      sharpe_annual_BANNED column omitted — frequency inflation artifact.", flush=True)


if __name__ == "__main__":
    main()
