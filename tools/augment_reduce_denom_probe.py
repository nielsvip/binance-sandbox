#!/usr/bin/env python3
"""Does a candidate that AUGMENTS more get penalised by its own larger gain denominator?

gain% = net $ / peak cost basis. An AUGMENT raises the basis; a REDUCE lowers it.
If the greedy sweep ranks on gain% alone, augment candidates lose rows they win on $
and reduce candidates win rows they lose on $. Measure it.
"""
import os
import sys

sys.path.insert(0, os.getcwd())
from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized  # noqa: E402
from tools.opt.evaluate_v12 import _peak_concurrent  # noqa: E402

CANDS = [
    ("baseline", {}),
    ("PYRAMID_ENABLED=True", {"PYRAMID_ENABLED": True}),
    ("BOUNCE_AUGMENT_ENABLED=True", {"BOUNCE_AUGMENT_ENABLED": True}),
    ("HAIKU_WINNER_ENABLED=True", {"HAIKU_WINNER_ENABLED": True}),
    ("AUGMENT_INTO_REENTRY_ENABLED=True", {"AUGMENT_INTO_REENTRY_ENABLED": True}),
    ("AUGMENT_AT_LOSS_ENABLED=True", {"AUGMENT_AT_LOSS_ENABLED": True}),
    ("CYCLE_TP_TIERED_ENABLED=False", {"CYCLE_TP_TIERED_ENABLED": False}),
    ("SATOSHIT_EXIT_PARTIAL_PCT=1.4", {"SATOSHIT_EXIT_PARTIAL_PCT": 1.4}),
    ("HLR_TOP_EXIT_ENABLED=False", {"HLR_TOP_EXIT_ENABLED": False}),
    ("ENABLE_FAST_RISER_REDUCE=False", {"ENABLE_FAST_RISER_REDUCE": False}),
    ("MANAGE_REDUCE=False", {"MANAGE_REDUCE": False}),
    ("BREAKEVEN_GAIN_EROSION_ENABLED=True", {"BREAKEVEN_GAIN_EROSION_ENABLED": True}),
    ("PEAK_GIVEBACK_DROP_TRIGGER_ENABLED=True", {"PEAK_GIVEBACK_DROP_TRIGGER_ENABLED": True}),
    ("WT_REDUCE_FRAC_HIGH=0", {"WT_REDUCE_FRAC_HIGH": 0.0}),
    ("WT_REDUCE_FRAC_LOW=2", {"WT_REDUCE_FRAC_LOW": 2.0}),
    ("REGIME_TRENDING_WT_REDUCE_FRAC_LOW=0.2", {"REGIME_TRENDING_WT_REDUCE_FRAC_LOW": 0.2}),
    ("REGIME_RANGING_WT_REDUCE_FRAC_LOW=0.6", {"REGIME_RANGING_WT_REDUCE_FRAC_LOW": 0.6}),
    ("QUICK_REDUCE_TECHNICAL_ONLY=False", {"QUICK_REDUCE_TECHNICAL_ONLY": False}),
    ("DC_BASIS_3M_REDUCE_ENABLED=False", {"DC_BASIS_3M_REDUCE_ENABLED": False}),
    ("MI_EXHAUST_EXIT_ENABLED=False", {"MI_EXHAUST_EXIT_ENABLED": False}),
    ("MI_DIV_EXIT_ENABLED=False", {"MI_DIV_EXIT_ENABLED": False}),
    ("PARTIAL_PROFIT_LOCK_ENABLED=True", {"PARTIAL_PROFIT_LOCK_ENABLED": True}),
    ("SATOSHIT_EXIT_ENABLED=True", {"SATOSHIT_EXIT_ENABLED": True}),
]


def row(prep, name, ov):
    r = evaluate_prepared_sanitized(prep, ov, 30, include_ledger=True)
    led = [t for t in (r.get("execution_ledger") or []) if isinstance(t, dict)]
    n_aug = sum(1 for t in led if str(t.get("type") or "").upper() == "AUGMENT")
    n_red = sum(1 for t in led if str(t.get("type") or "").upper() == "REDUCE")
    return {
        "name": name,
        "gain": float(r.get("gain_pct") or 0.0),
        "net": float(r.get("gain_dollars") or 0.0),
        "peak": _peak_concurrent(led),
        "trades": int(r.get("trades") or 0),
        "aug": n_aug,
        "red": n_red,
        "valid": bool(r.get("valid")),
    }


def main(symsides):
    for ss in symsides:
        prep = prepare_batch(ss, 30)
        if not prep or prep.get("_stale"):
            print(f"{ss}: unavailable")
            continue
        base = None
        print("=" * 112)
        print(f"{ss}")
        print(f"  {'candidate':<40} {'gain%':>9} {'Δgain':>8} {'net $':>10} {'Δ$':>9} {'peak $':>10} {'tr':>5} {'aug':>5} {'red':>5}")
        for name, ov in CANDS:
            try:
                d = row(prep, name, ov)
            except Exception as exc:
                print(f"  {name:<40} ERROR {exc}")
                continue
            if base is None:
                base = d
                dg = dn = 0.0
            else:
                dg = d["gain"] - base["gain"]
                dn = d["net"] - base["net"]
            flag = ""
            if base is not d and (dg > 0) != (dn > 0) and abs(dg) > 1e-9 and abs(dn) > 1e-9:
                flag = "  <-- gain% and $ DISAGREE"
            print(f"  {d['name']:<40} {d['gain']:>9.3f} {dg:>8.3f} {d['net']:>10.2f} {dn:>9.2f} {d['peak']:>10.2f} {d['trades']:>5} {d['aug']:>5} {d['red']:>5}{flag}")


if __name__ == "__main__":
    main(sys.argv[1:])
