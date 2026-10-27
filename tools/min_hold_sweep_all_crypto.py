#!/usr/bin/env python3
"""MIN_HOLD_BARS = 0 / 1 / 2 / 4 / 8 on EVERY crypto sym_side.

USER 2026-10-27: "TESTED with 0-1-2-4-8 on EVERY crypto".

Controlled A/B: one frozen 30-day NPZ slice per symbol, every arm run on the
identical bytes, only MIN_HOLD_BARS differing. Both LONG and SHORT. The engine's
min hold now comes from `vec_decisions/min_hold_gate.threshold_bars`, the same
function `ez_manage` calls, so a delta measured here is a delta live will feel.

Reports per arm: gain, trades and the per-trade gain, plus the sweep's own
verdict on whether the option BOUND at all — because with
`MIN_HOLD_SOLE_SWITCH_ENABLED=False` the effective hold is
max(MIN_HOLD_BARS, MIN_HOLD_BARS_BEFORE_EXIT=10) and every option <= 8 is
masked, which would make all five arms identical by construction (Bible 14.3).
Run with --sole to test the arm where MIN_HOLD_BARS is the only driver.

  python3 tools/min_hold_sweep_all_crypto.py --limit 40 --out data/reports/min_hold_sweep.json
  python3 tools/min_hold_sweep_all_crypto.py --sole --out data/reports/min_hold_sweep_sole.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

# NEVER hardcode the option set here: that would be a second source of truth for
# a number the sheet owns, which is precisely what BACKTEST_BIBLE's "THE SHEET IS
# THE AUTHORITY" forbids. (Caught 2026-10-27: this file DID hardcode (0,1,2,4,8)
# and silently swept the old set after the operator changed it to 2/4/8/12.)
from vec_decisions import min_hold_gate as _mhg_opts  # noqa: E402

OPTIONS = tuple(_mhg_opts.SWEEP_OPTIONS)
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD")


def crypto_symbols(npz_dir: Path) -> list:
    out = []
    for p in sorted(npz_dir.glob("*.npz")):
        if p.stem.upper().endswith(CRYPTO_SUFFIX):
            out.append(p.stem)
    return out


def slice30(npz_dir: Path, sym: str, days: float = 30.0):
    d = np.load(npz_dir / f"{sym}.npz", allow_pickle=True)
    ts = np.asarray(d["timestamps"], dtype=float)
    n = len(ts)
    if n == 0:
        return None
    span = days * 86400.0 * (1000.0 if ts.max() > 1e11 else 1.0)
    mask = ts >= (ts.max() - span)
    return {k: (d[k][mask] if (getattr(d[k], "ndim", 0) == 1 and d[k].shape[0] == n) else d[k]) for k in d.files}


def run_arm(V, sliced, sym, is_long, option, sole):
    cfg = V.QuickConfig()
    cfg.MODE = "crypto"
    cfg.MIN_HOLD_BARS = int(option)
    if sole:
        cfg.MIN_HOLD_SOLE_SWITCH_ENABLED = True
    r = V.simulate_one(sliced, sym, is_long, cfg)
    from vec_decisions import min_hold_gate as g

    return {
        "gain_pct": r.get("gain_pct"),
        "trades": r.get("trades"),
        "effective_bars": g.threshold_bars(cfg),
        "effective_minutes": g.threshold_seconds(cfg) / 60.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--npz-dir", default=str(ROOT / "backtest_v8" / "indicators"))
    ap.add_argument("--limit", type=int, default=0, help="0 = every crypto symbol")
    ap.add_argument("--days", type=float, default=30.0)
    ap.add_argument("--sole", action="store_true", help="MIN_HOLD_BARS as the sole driver")
    ap.add_argument("--sides", default="both", choices=("long", "short", "both"))
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    os.environ.setdefault("V12_NPZ_CACHE", "4")
    import v12_quick_engine as V

    npz_dir = Path(a.npz_dir)
    syms = crypto_symbols(npz_dir)
    if a.limit:
        syms = syms[: a.limit]
    sides = [True, False] if a.sides == "both" else [a.sides == "long"]
    print(f"MIN_HOLD_BARS sweep {list(OPTIONS)} | {len(syms)} crypto symbols x {len(sides)} side(s) "
          f"| sole={a.sole} | {a.days:.0f}d frozen slices", flush=True)

    rows = []
    t0 = time.time()
    for i, sym in enumerate(syms, 1):
        try:
            sliced = slice30(npz_dir, sym, a.days)
        except Exception as e:
            print(f"  [{i}/{len(syms)}] {sym} SKIP load {type(e).__name__}", flush=True)
            continue
        if sliced is None:
            continue
        for is_long in sides:
            ss = f"{sym}_{'LONG' if is_long else 'SHORT'}"
            arms = {}
            try:
                for opt in OPTIONS:
                    arms[opt] = run_arm(V, sliced, sym, is_long, opt, a.sole)
            except Exception as e:
                print(f"  {ss} ERROR {type(e).__name__}: {str(e)[:70]}", flush=True)
                continue
            gains = [arms[o]["gain_pct"] for o in OPTIONS]
            trades = [arms[o]["trades"] for o in OPTIONS]
            if any(gv is None for gv in gains):
                continue
            bound = (len(set(round(float(gv), 9) for gv in gains)) > 1) or (len(set(trades)) > 1)
            base = float(arms[0]["gain_pct"])
            best_opt = max(OPTIONS, key=lambda o: float(arms[o]["gain_pct"]))
            rows.append({
                "sym_side": ss, "bound": bool(bound),
                "gain": {str(o): round(float(arms[o]["gain_pct"]), 4) for o in OPTIONS},
                "trades": {str(o): int(arms[o]["trades"] or 0) for o in OPTIONS},
                "effective_bars": {str(o): arms[o]["effective_bars"] for o in OPTIONS},
                "best_option": best_opt,
                "best_delta_vs_0": round(float(arms[best_opt]["gain_pct"]) - base, 4),
                "trades_at_0": int(arms[0]["trades"] or 0),
                "trades_at_best": int(arms[best_opt]["trades"] or 0),
            })
        if i % 10 == 0:
            print(f"  [{i}/{len(syms)}] {len(rows)} sym_sides done, {time.time() - t0:.0f}s", flush=True)

    nb = sum(1 for r in rows if r["bound"])
    print(f"\nsym_sides measured: {len(rows)} | the option BOUND on {nb} "
          f"({100.0 * nb / max(len(rows), 1):.1f}%)", flush=True)
    if nb == 0 and rows:
        print("  => STRUCTURAL ZERO on every sym_side: the effective hold is pinned by "
              "MIN_HOLD_BARS_BEFORE_EXIT. Re-run with --sole for a binding test.", flush=True)
    bound_rows = [r for r in rows if r["bound"]]
    if bound_rows:
        from collections import Counter

        best = Counter(r["best_option"] for r in bound_rows)
        print("  best option (of the binding sym_sides):", dict(sorted(best.items())), flush=True)
        tot0 = sum(r["trades_at_0"] for r in bound_rows)
        totb = sum(r["trades_at_best"] for r in bound_rows)
        print(f"  trades at MIN_HOLD_BARS=0: {tot0}  ->  at each sym_side's best: {totb} "
              f"({100.0 * (totb - tot0) / max(tot0, 1):+.1f}%)", flush=True)
        import statistics

        print(f"  mean gain delta best vs 0: {statistics.mean(r['best_delta_vs_0'] for r in bound_rows):+.3f} pp", flush=True)
        print("\n  worst churn offenders (most trades removed by the best hold):", flush=True)
        for r in sorted(bound_rows, key=lambda x: x["trades_at_best"] - x["trades_at_0"])[:10]:
            print(f"    {r['sym_side']:22s} best={r['best_option']} "
                  f"trades {r['trades_at_0']:4d}->{r['trades_at_best']:4d} "
                  f"gain {r['gain']['0']:+8.2f}->{r['gain'][str(r['best_option'])]:+8.2f}", flush=True)
    doc = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "options": list(OPTIONS), "sole": bool(a.sole), "days": a.days,
           "sym_sides": len(rows), "bound": nb, "rows": rows}
    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, indent=1))
        print(f"\nwrote {p}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
