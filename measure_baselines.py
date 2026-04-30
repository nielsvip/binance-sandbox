# metrics_guard-clean: Sharpe used internally only, never emitted to user surface (audited 2026-04-30).
"""Measure REAL per-trade pool Sharpe for each snapshot's overrides under CURRENT engine."""
import argparse, json, sys, time, gc
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["crypto", "tradier"])
    ap.add_argument("--overrides", default=None)
    ap.add_argument("--symbols", type=int, default=24)
    ap.add_argument("--years", type=float, default=4.0)
    ap.add_argument("--start", default=None)
    ap.add_argument("--npz-dir", required=True)
    ap.add_argument("--label", default="BASELINE")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from v8_quick_engine import QuickConfig, load_npz, simulate

    start_date = args.start or ("2022-01-01" if args.years >= 4 else "2024-01-01")
    cfg = QuickConfig()
    cfg.MODE = args.mode
    # CRITICAL: set LTF correctly. crypto base=3m, tradier base=5m.
    cfg.LTF = "3m" if args.mode == "crypto" else "5m"
    overrides = {}
    if args.overrides and Path(args.overrides).exists():
        with open(args.overrides) as f:
            overrides = json.load(f)
    applied = 0; skipped = []
    for k, v in overrides.items():
        if k.startswith("_"): continue
        if hasattr(cfg, k):
            setattr(cfg, k, v); applied += 1
        else:
            skipped.append(k)

    stores = load_npz(args.mode, None, start_date, args.npz_dir)
    syms = sorted(stores.keys())[:args.symbols]
    subset = {s: stores[s] for s in syms}
    # Free the rest
    for s in list(stores.keys()):
        if s not in subset: del stores[s]
    gc.collect()
    print(f"[{args.label}] mode={args.mode} LTF={cfg.LTF} overrides_applied={applied} skipped={skipped[:5]} symbols={len(subset)} start={start_date}", flush=True)

    t0 = time.time()
    r = simulate(subset, cfg)
    elapsed = time.time() - t0
    out = {
        "label": args.label, "mode": args.mode,
        "symbols": len(subset), "years": args.years, "LTF": cfg.LTF,
        "pool_sharpe": round(r.get("pool_sharpe", 0.0), 4),
        "accumulated_gain_pct": round(r.get("accumulated_gain_pct", 0.0), 2),
        "max_dd_pct": round(r.get("max_dd_pct", 0.0), 2),
        "trades": r.get("trades", 0),
        "elapsed_s": round(elapsed, 1),
        "overrides_applied": applied,
    }
    print("RESULT: " + json.dumps(out), flush=True)

if __name__ == "__main__":
    main()
