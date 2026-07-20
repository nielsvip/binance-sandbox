#!/usr/bin/env python3
"""ofat_screen.py — one-factor-at-a-time screen of every sweepable config param.

For each sweepable param in data/param_sweep_manifest_tradier.json, run the chosen
engine on a fixed screen symbol set with ONLY that param overridden to each of its
test values, and record pool_sharpe vs a no-override baseline. Cheap shortlist
(CLAUDE.md Tier-1); winners go to a Tier-2 confirm + full-symbol big sweep.

  --tier vec    : v8_vec_sweep.py   (fast; covers VEC_SCREEN params)   [default]
  --tier engine : backtest_v8_engine single-sym pooled (ENGINE_SCREEN; slow)

Resumable: appends to a CSV and skips (param,value) cells already present.
Memory-respectful: runs ONE engine process at a time (vec already uses 8 workers).
NO-LIES: pool_sharpe is engine-emitted; 40-sym screen is sub-floor -> DIAGNOSTIC
shortlist only, never a promotion claim.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SBX = Path("/home/niels/binance-sandbox")
PY = "/home/niels/.conda/envs/binance_env/bin/python"
POOL_RE = re.compile(r"pool_sharpe=([+-]?\d+\.\d+)")
TRADES_RE = re.compile(r"trades=([\d,]+)")
NSYM_RE = re.compile(r"n_syms=(\d+)")
UNKNOWN_RE = re.compile(r"unknown SweepConfig knob (\w+)")


def screen_syms(n):
    allsyms = [s.strip() for s in Path("/tmp/tradier_syms.txt").read_text().split() if s.strip()]
    if n >= len(allsyms):
        return allsyms
    step = len(allsyms) / n
    return [allsyms[int(i * step)] for i in range(n)]


def run_vec(symbols, overrides, start, timeout):
    env = dict(os.environ)
    env["V8_VEC_ALLOW_DIAGNOSTIC"] = "1"
    cmd = [PY, str(SBX / "v8_vec_sweep.py"), "--mode", "tradier", "--account",
           "_ofat_screen", "--start", start, "--symbols", ",".join(symbols)]
    for k, v in overrides:
        cmd += ["--override", f"{k}={v}"]
    try:
        p = subprocess.run(cmd, cwd=str(SBX), env=env, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, 0, 0, [], "timeout"
    out = p.stdout + "\n" + p.stderr
    pool = POOL_RE.findall(out)
    trades = TRADES_RE.findall(out)
    nsyms = NSYM_RE.findall(out)
    inert = UNKNOWN_RE.findall(out)
    if not pool:
        return None, 0, 0, inert, "no_result"
    return (float(pool[-1]), int(trades[-1].replace(",", "")) if trades else 0,
            int(nsyms[-1]) if nsyms else 0, inert, "ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(SBX / "data/param_sweep_manifest_tradier.json"))
    ap.add_argument("--tier", choices=["vec", "engine"], default="vec")
    ap.add_argument("--syms", type=int, default=40)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default="/home/niels/logs/ofat_screen_vec.csv")
    ap.add_argument("--limit", type=int, default=0, help="cap params (0=all) for smoke test")
    args = ap.parse_args()

    man = json.loads(Path(args.manifest).read_text())["params"]
    want_tier = "VEC_SCREEN" if args.tier == "vec" else "ENGINE_SCREEN"
    params = [(n, v) for n, v in man.items()
              if v["sweepable"] and v["sweep_tier"] == want_tier and v["test_values"]]
    params.sort(key=lambda kv: (kv[1]["best_mean_pool_sharpe"] is None,
                                -(kv[1]["best_mean_pool_sharpe"] or 0)))
    if args.limit:
        params = params[: args.limit]
    syms = screen_syms(args.syms)
    out_path = Path(args.out)
    done = set()
    if out_path.exists():
        with out_path.open() as f:
            for row in csv.DictReader(f):
                done.add((row["param"], row["value"]))
    new_file = not out_path.exists()
    fh = out_path.open("a", newline="")
    w = csv.writer(fh)
    if new_file:
        w.writerow(["param", "value", "pool_sharpe", "delta_vs_baseline", "trades",
                    "n_syms", "inert", "status", "ts"])
        fh.flush()
    print(f"[ofat-{args.tier}] params={len(params)} syms={len(syms)} out={out_path}", flush=True)
    # baseline cell
    base_key = ("__BASELINE__", "")
    baseline = None
    if base_key not in done:
        bs, btr, bn, _, st = run_vec(syms, [], args.start, args.timeout)
        baseline = bs if bs is not None else 0.0
        w.writerow(["__BASELINE__", "", bs, 0.0, btr, bn, "", st, int(time.time())])
        fh.flush()
        print(f"[baseline] pool_sharpe={bs} trades={btr} n_syms={bn}", flush=True)
    else:
        for ln in out_path.read_text().splitlines():
            if ln.startswith("__BASELINE__"):
                try:
                    baseline = float(ln.split(",")[2])
                except Exception:
                    baseline = 0.0
    if baseline is None:
        baseline = 0.0
    t0 = time.time()
    cell = 0
    for name, meta in params:
        for val in meta["test_values"]:
            vs = str(val)
            if (name, vs) in done:
                continue
            cell += 1
            ps, tr, ns, inert, st = run_vec(syms, [(name, vs)], args.start, args.timeout)
            is_inert = name in inert
            delta = round(ps - baseline, 4) if ps is not None else None
            w.writerow([name, vs, ps, delta, tr, ns, int(is_inert), st, int(time.time())])
            fh.flush()
            flag = " INERT" if is_inert else ""
            print(f"[{cell}] {name}={vs} pool_sharpe={ps} d={delta}{flag} ({st}) "
                  f"elapsed={int(time.time()-t0)}s", flush=True)
    fh.close()
    print(f"[ofat-{args.tier}] DONE cells={cell} elapsed={int(time.time()-t0)}s", flush=True)


if __name__ == "__main__":
    main()
