#!/usr/bin/env python3
"""Deciding test for bucket A_dead_both: does flipping the switch move the vector ledger?

Read-only probe. For each name in A_dead_both of
data/reports/vec_unwired_classification.json, builds a baseline arm and a flipped
arm and evaluates both through tools.opt.v12_pilot.prepare_batch +
evaluate_prepared_sanitized (the real v12_quick_engine vector path). A name is
"dead" only if gain_pct AND trades are bit-identical across the flip on EVERY
probed sym_side. Any movement is a reclassification, never a skip.

Parent-enabler handling: for a non-_ENABLED name, if QuickConfig has a sibling
<prefix>_ENABLED field, that sibling is forced True in BOTH arms, so a threshold
is not reported dead merely because its engine is off by default.

Control: ATR_ADAPTIVE_STOP_MULT 5.0 vs 8.0 must move, else the harness is a dead
probe and the whole run is void.

Env: V15_EVAL_CACHE=0 V12_ALLOW_STALE_NPZ=1 (Mac NPZs are stale — disclosed in the report).
Usage: python3 tools/v15_dead_both_probe.py --symsides AAPL_LONG,NVDA_LONG,BTCUSDC_LONG
"""
import argparse
import dataclasses
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_EVAL_CACHE", "0")
os.environ.setdefault("V12_ALLOW_STALE_NPZ", "1")

TF_CYCLE = {"OFF": "4h", "D": "4h", "4h": "1h", "1h": "15m", "15m": "1h", "": "4h", "None": "4h"}
try:
    DEPS = json.loads((ROOT / "data" / "switch_dependencies.json").read_text())
except Exception:
    DEPS = {}


def names(bucket="A_dead_both"):
    d = json.loads((ROOT / "data" / "reports" / "vec_unwired_classification.json").read_text())
    out = []
    for e in d.get(bucket) or []:
        out.append(str(e[0] if isinstance(e, (list, tuple)) else e).strip())
    return out


def flip_value(name, val):
    """Return (flipped_value, note) or (None, reason-not-flippable)."""
    if isinstance(val, bool):
        return (not val), "bool"
    if isinstance(val, str):
        if name.endswith("_TF") or name.endswith("_TF_ENTRY"):
            return TF_CYCLE.get(val, "4h" if val != "4h" else "1h"), "tf"
        return (val + "_X") if val else "D", "str"
    if isinstance(val, (int, float)):
        if val == 0:
            return (1 if isinstance(val, int) else 1.0), "zero->1"
        f = type(val)(val * 2) if isinstance(val, float) else int(val * 2) or 1
        return f, "x2"
    if isinstance(val, (list, tuple)):
        return list(val)[:-1] if len(val) > 1 else [], "trunc"
    return None, f"unflippable type {type(val).__name__}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symsides", default="AAPL_LONG,NVDA_LONG,BTCUSDC_LONG")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--out", default=str(ROOT / "data" / "reports" / "dead_both_probe.json"))
    ap.add_argument("--bucket", default="A_dead_both", help="classification bucket to probe (A_dead_both, C_vec_only, ...)")
    ap.add_argument("--context", choices=["defaults", "promoted"], default="defaults", help="baseline arm: bare QuickConfig defaults, or the promoted per_sym full set (binding can be context-dependent)")
    a = ap.parse_args()
    ctx = {}
    if a.context == "promoted":
        import per_sym_store as _PS
        for _ss in [s.strip() for s in a.symsides.split(",") if s.strip()]:
            _c = _PS.get_full_config(_ss)
            if _c:
                ctx[_ss] = dict(_c)
                print(f"[context] {_ss}: promoted full set, {len(_c)} keys")
    import v12_quick_engine as VQ
    from tools.opt import v12_pilot as P
    qf = {f.name: f for f in dataclasses.fields(VQ.QuickConfig)}
    base_cfg = VQ.QuickConfig()
    symsides = [s.strip() for s in a.symsides.split(",") if s.strip()]
    preps = {}
    for ss in symsides:
        pr = P.prepare_batch(ss, a.window_days)
        if isinstance(pr, dict) and pr.get("_stale"):
            print(f"[stale] {ss}: {pr['_stale']}")
            continue
        preps[ss] = pr
    if not preps:
        print("no usable sym_sides")
        return 2
    # ——— harness control: must MOVE, else every flat result is meaningless ———
    control = {}
    for ss, pr in preps.items():
        c0 = dict(ctx.get(ss) or {})
        b = P.evaluate_prepared_sanitized(pr, dict(c0), a.window_days)
        lo = P.evaluate_prepared_sanitized(pr, dict(c0, ATR_ADAPTIVE_STOP_MULT=5.0), a.window_days)
        hi = P.evaluate_prepared_sanitized(pr, dict(c0, ATR_ADAPTIVE_STOP_MULT=8.0), a.window_days)
        control[ss] = {
            "baseline_gain": b.get("gain_pct"),
            "baseline_trades": b.get("trades"),
            "mult5_gain": lo.get("gain_pct"),
            "mult8_gain": hi.get("gain_pct"),
            "d5": (lo.get("gain_pct") or 0) - (b.get("gain_pct") or 0),
            "d8": (hi.get("gain_pct") or 0) - (b.get("gain_pct") or 0),
            "binds": abs((lo.get("gain_pct") or 0) - (hi.get("gain_pct") or 0)) > 1e-12,
        }
        print(f"[control] {ss} base {control[ss]['baseline_gain']} d5 {control[ss]['d5']:+.6f} d8 {control[ss]['d8']:+.6f} binds={control[ss]['binds']}")
    if not any(c["binds"] for c in control.values()):
        print("CONTROL DID NOT BIND — harness is a dead probe, results void")
        return 3
    baselines = {ss: P.evaluate_prepared_sanitized(pr, dict(ctx.get(ss) or {}), a.window_days) for ss, pr in preps.items()}
    results = []
    t0 = time.time()
    for n in names(a.bucket):
        rec = {"switch": n, "in_quickconfig": n in qf, "per_symside": {}, "moved": False, "note": ""}
        if n not in qf:
            rec["note"] = "NOT_IN_QUICKCONFIG: QuickConfig has no such field — vector cannot read it at all"
            results.append(rec)
            continue
        cur = getattr(base_cfg, n)
        fv, note = flip_value(n, cur)
        rec["default"] = cur if not isinstance(cur, (list, tuple, dict)) else str(cur)[:80]
        rec["flipped_to"] = fv if not isinstance(fv, (list, tuple, dict)) else str(fv)[:80]
        rec["flip_kind"] = note
        if fv is None:
            rec["note"] = f"UNPROVEN: {note}"
            results.append(rec)
            continue
        enabler = ""
        masters = [m for m in (DEPS.get("masters", {}).get(n) or []) if m in qf]
        if not n.endswith("_ENABLED"):
            parts = n.split("_")
            for i in range(len(parts), 1, -1):
                cand = "_".join(parts[:i]) + "_ENABLED"
                if cand in qf and cand != n:
                    enabler = cand
                    break
        rec["forced_enabler"] = enabler
        rec["forced_masters"] = masters
        for ss, pr in preps.items():
            pre = dict(ctx.get(ss) or {})
            if enabler:
                pre[enabler] = True
            # masters are forced in BOTH arms: tools/opt/evaluate_v12.py:812-814 forces a switch's
            # master ON whenever the switch is overridden, so an un-forced baseline makes a dead
            # switch look alive (HTF_GATE_BYPASS_RZ: the MASTER moved the ledger, not the switch).
            for m in masters:
                pre[m] = True
            b = P.evaluate_prepared_sanitized(pr, dict(pre), a.window_days) if (enabler or masters) else baselines[ss]
            o = dict(pre)
            o[n] = fv
            r = P.evaluate_prepared_sanitized(pr, o, a.window_days)
            dg = (r.get("gain_pct") or 0.0) - (b.get("gain_pct") or 0.0)
            dt_ = int(r.get("trades") or 0) - int(b.get("trades") or 0)
            rec["per_symside"][ss] = {
                "base_gain": b.get("gain_pct"),
                "flip_gain": r.get("gain_pct"),
                "d_gain": dg,
                "base_trades": b.get("trades"),
                "flip_trades": r.get("trades"),
                "d_trades": dt_,
            }
            if abs(dg) > 1e-12 or dt_ != 0:
                rec["moved"] = True
        rec["note"] = "MOVED_LEDGER: reclassify — NOT dead" if rec["moved"] else "FLAT: identical gain and trades across the flip on every probed sym_side"
        results.append(rec)
        print(f"  {'MOVE' if rec['moved'] else 'flat'} {n:<42} {rec['default']!r:>10} -> {rec['flipped_to']!r:<10} {'ENABLER=' + enabler if enabler else ''}")
    out = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "env": {"V15_EVAL_CACHE": os.environ.get("V15_EVAL_CACHE"), "V12_ALLOW_STALE_NPZ": os.environ.get("V12_ALLOW_STALE_NPZ")},
        "window_days": a.window_days,
        "context": a.context,
        "bucket": a.bucket,
        "symsides": list(preps),
        "control_atr_adaptive_stop_mult": control,
        "n_names": len(results),
        "n_moved": sum(1 for r in results if r["moved"]),
        "n_flat": sum(1 for r in results if not r["moved"] and r.get("in_quickconfig") and r.get("flipped_to") is not None),
        "n_not_in_quickconfig": sum(1 for r in results if not r.get("in_quickconfig")),
        "secs": round(time.time() - t0, 1),
        "results": results,
    }
    pathlib.Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "results"}, indent=1))
    print(f"[out] {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
