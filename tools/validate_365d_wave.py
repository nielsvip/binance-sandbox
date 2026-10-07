"""365D validation wave: baseline vs final-override set per sym_side (vector)."""
import argparse
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "opt"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--syms", default=None, help="comma list, default=all with progress")
    ap.add_argument("--window", type=int, default=365)
    ap.add_argument("--out", default="/tmp/365d_wave.json")
    args = ap.parse_args()
    from v12_pilot import evaluate_prepared_sanitized, prepare_batch
    if args.syms:
        syms = [s.strip() for s in args.syms.split(",") if s.strip()]
    else:
        syms = sorted(
            os.path.basename(p).replace("_v14_progress.json", "")
            for p in glob.glob(os.path.join(ROOT, "data", "reports", "lifecycle_pilot", "*_v14_progress.json"))
        )
    print(f"[wave] {len(syms)} syms window={args.window}", flush=True)
    results = []
    for sym in syms:
        t0 = time.time()
        try:
            prog = json.load(open(os.path.join(ROOT, "data", "reports", "lifecycle_pilot", f"{sym}_v14_progress.json")))
        except Exception as e:
            print(f"[wave] {sym} NO-PROG {e}", flush=True)
            continue
        ov = dict(prog.get("cumulative_overrides", {}))
        try:
            prep = prepare_batch(sym, args.window)
            if not prep:
                print(f"[wave] {sym} NO-PREP", flush=True)
                continue
            base = evaluate_prepared_sanitized(prep, {}, args.window) or {}
            best = evaluate_prepared_sanitized(prep, ov, args.window) or {}
            rec = {
                "sym": sym,
                "n_overrides": len(ov),
                "base30": round(prog.get("baseline_gain", 0) or 0, 4),
                "cum30": round(prog.get("cumulative_gain", 0) or 0, 4),
                "base_w": {k: base.get(k) for k in ("gain_pct", "trades", "pool_sharpe", "max_dd_pct", "tim_pct", "bh_pct")},
                "best_w": {k: best.get(k) for k in ("gain_pct", "trades", "pool_sharpe", "max_dd_pct", "tim_pct", "bh_pct")},
                "delta_w": round((best.get("gain_pct") or 0) - (base.get("gain_pct") or 0), 4),
                "secs": round(time.time() - t0, 1),
            }
            print(f"[wave] {sym} d365={rec['delta_w']:.2f} best={round(best.get('gain_pct') or 0,2):.2f} base={round(base.get('gain_pct') or 0,2):.2f} tr={best.get('trades')} {rec['secs']}s", flush=True)
        except Exception as e:
            rec = {"sym": sym, "error": str(e)[:200]}
            print(f"[wave] {sym} ERROR {e}", flush=True)
        results.append(rec)
        try:
            json.dump(results, open(args.out, "w"), indent=1)
        except Exception:
            pass
    print(f"[wave] DONE {len(results)} -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
