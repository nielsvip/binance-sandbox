#!/usr/bin/env python3
"""vec_ab_sweep.py — Vectorized A/B sweep using v8_quick_engine.

For each pending item in /tmp/v8_test_queue.json, invoke v8_quick_engine.py
twice (A value, then B value) via V8_OVERRIDE_FILE env var. Parse the
V8_QUICK_RESULT line. Compare Sharpe / pool_sharpe / pnl / trades / wr.

NOTE: v8_quick_engine.py does NOT know all of today's switches. For unknown
switches the override is silently dropped → A=B numbers (engine-blind).
We label these clearly so the user doesn't mistake "no signal" for "no diff".

Usage:
  python3 vec_ab_sweep.py --mode crypto --symbols BTCUSDC,ETHUSDC,SOLUSDC --start 2024-01-01

Output: data/test_queue_results/vec_abtest_<param>_<ts>.json + summary table.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
QUEUE = Path("/tmp/v8_test_queue.json")
RESULTS_DIR = REPO / "data" / "test_queue_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
ENGINE = REPO / "v8_quick_engine.py"
PYTHON = os.environ.get("V8_PYTHON", sys.executable)
RESULT_RE = re.compile(
    r"V8_QUICK_RESULT:\s*sharpe=([-\d.]+)\s+pool_sharpe=([-\d.]+).*?pnl=([-\d.]+)\s+trades=(\d+)\s+wins=(\d+)\s+losses=(\d+)\s+wr=([-\d.]+)%\s+avg_pnl=([-\d.]+)%.*?elapsed=([\d.]+)s",
    re.DOTALL,
)


def parse_typed(s: str):
    if isinstance(s, (int, float, bool)):
        return s
    s = str(s)
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        return s


def run_engine(mode: str, symbols: str, start: str, override_kv: dict, capital: float = 2000.0, npz_dir: str = ""):
    """Run v8_quick_engine.py once with the given override. Return parsed result dict or None."""
    ov_file = Path(f"/tmp/vec_override_{int(time.time()*1000)}.json")
    ov_file.write_text(json.dumps(override_kv))
    cmd = [PYTHON, "-u", str(ENGINE), "--mode", mode, "--symbols", symbols, "--start", start, "--capital", str(capital)]
    if npz_dir:
        cmd += ["--npz-dir", npz_dir]
    env = dict(os.environ)
    env["V8_OVERRIDE_FILE"] = str(ov_file)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(REPO), timeout=600)
    except subprocess.TimeoutExpired:
        ov_file.unlink(missing_ok=True)
        return {"error": "timeout_600s"}
    ov_file.unlink(missing_ok=True)
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    m = RESULT_RE.search(out)
    if not m:
        return {"error": "no_result_line", "tail": out[-500:]}
    return {
        "sharpe": float(m.group(1)),
        "pool_sharpe": float(m.group(2)),
        "pnl": float(m.group(3)),
        "trades": int(m.group(4)),
        "wins": int(m.group(5)),
        "losses": int(m.group(6)),
        "wr": float(m.group(7)),
        "avg_pnl_pct": float(m.group(8)),
        "elapsed": float(m.group(9)),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    p.add_argument("--symbols", default="BTCUSDC,ETHUSDC,SOLUSDC")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--capital", type=float, default=2000.0)
    p.add_argument("--npz-dir", default="")
    from vector_mandatory_coverage import add_coverage_claim_arguments, enforce_coverage_claim
    add_coverage_claim_arguments(p)
    args = p.parse_args()
    coverage_contract = enforce_coverage_claim(args, runner="vec_ab_sweep.py")
    print(f"V8_VECTOR_GROUND_RULE: {coverage_contract['coverage_status']} shortlist_sha256={coverage_contract['shortlist_sha256']}")

    if not QUEUE.exists():
        print(f"queue file not found: {QUEUE}")
        return 1
    queue = json.loads(QUEUE.read_text())
    pending = [i for i in queue if i.get("status") == "pending"]
    print(f"vec_ab_sweep: {len(pending)} pending of {len(queue)} total")
    print(f"  symbols={args.symbols}  start={args.start}  mode={args.mode}")
    print()

    rows = []
    for idx, item in enumerate(queue):
        if item.get("status") != "pending":
            continue
        param = item["param"]
        va, vb = item["value_a"], item["value_b"]
        print(f"[{idx+1}/{len(queue)}] {param}: A={va}  B={vb}")
        ova = {param: parse_typed(va)}
        ovb = {param: parse_typed(vb)}
        t0 = time.time()
        ra = run_engine(args.mode, args.symbols, args.start, ova, args.capital, args.npz_dir)
        rb = run_engine(args.mode, args.symbols, args.start, ovb, args.capital, args.npz_dir)
        dt = time.time() - t0

        result_blob = {
            "param": param, "value_a": va, "value_b": vb,
            "engine": "v8_quick_engine", "mode": args.mode, "symbols": args.symbols, "start": args.start,
            "result_a": ra, "result_b": rb,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "elapsed_total_s": round(dt, 1),
        }
        # mark engine-blind if A==B exactly
        if isinstance(ra, dict) and isinstance(rb, dict) and "sharpe" in ra and "sharpe" in rb:
            engine_blind = (
                abs(ra["sharpe"] - rb["sharpe"]) < 1e-9
                and abs(ra["pnl"] - rb["pnl"]) < 1e-9
                and ra["trades"] == rb["trades"]
            )
            result_blob["engine_blind"] = engine_blind
            result_blob["delta_sharpe"] = round(ra["sharpe"] - rb["sharpe"], 4)
            result_blob["delta_pool_sharpe"] = round(ra["pool_sharpe"] - rb["pool_sharpe"], 4)
            result_blob["delta_pnl_pct"] = round(ra["pnl"] - rb["pnl"], 2)
            result_blob["delta_trades"] = ra["trades"] - rb["trades"]
            print(f"   A: sharpe={ra['sharpe']:.3f} pool={ra['pool_sharpe']:.3f} pnl={ra['pnl']:.2f} trades={ra['trades']} wr={ra['wr']:.0f}%")
            print(f"   B: sharpe={rb['sharpe']:.3f} pool={rb['pool_sharpe']:.3f} pnl={rb['pnl']:.2f} trades={rb['trades']} wr={rb['wr']:.0f}%")
            print(f"   Δ sharpe={result_blob['delta_sharpe']:+.3f} pool={result_blob['delta_pool_sharpe']:+.3f} pnl={result_blob['delta_pnl_pct']:+.2f}% trades={result_blob['delta_trades']:+d}  engine_blind={engine_blind}  ({dt:.0f}s)")
        else:
            print(f"   ERR: A={ra}  B={rb}")
        rows.append(result_blob)
        # save per-item
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = RESULTS_DIR / f"vec_abtest_{param}_{ts}.json"
        out_path.write_text(json.dumps(result_blob, indent=2))
        # mark queue completed (or engine_blind)
        item["status"] = "engine_blind" if result_blob.get("engine_blind") else "completed_vec"
        item["last_result_file"] = str(out_path)
        QUEUE.write_text(json.dumps(queue, indent=2))
        print()

    # final summary
    print("=" * 70)
    print(f"DONE — {len(rows)} A/Bs in {sum(r.get('elapsed_total_s',0) for r in rows):.0f}s")
    print()
    by_winner = []
    for r in rows:
        if r.get("engine_blind"):
            print(f"⚪ {r['param']}: ENGINE-BLIND (v8_quick_engine doesn't track this switch — re-test on backtest_v8_engine)")
            continue
        if "delta_sharpe" not in r:
            print(f"❌ {r['param']}: ERROR")
            continue
        d = r["delta_sharpe"]
        winner = "A" if d > 0 else "B"
        better = r['value_a'] if d > 0 else r['value_b']
        worse = r['value_b'] if d > 0 else r['value_a']
        marker = "🟢" if abs(d) >= 0.10 else "🟡" if abs(d) >= 0.03 else "⚫"
        print(f"{marker} {r['param']}: {better} > {worse} (Δsharpe={d:+.3f} Δpnl={r['delta_pnl_pct']:+.2f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
