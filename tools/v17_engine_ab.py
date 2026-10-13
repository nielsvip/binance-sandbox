#!/usr/bin/env python3
"""V17 engine A/B (2026-10-13): baseline-neutrality + effect proof for FILTER_EXEMPTIONS / AUGMENT_INTO_REENTRY.

Runs the REAL v12_quick_engine (tools.opt.evaluate_v12.prepare + _evaluate_prepared_nocache, include_ledger, eval cache off)
for a list of sym_sides and a list of override sets, printing one JSON with gain/trades/augments/carries/ledger hash per
(sym_side, set). --engine-dir puts a patched v12_quick_engine.py first on sys.path (A/B against the installed one by
running this twice and diffing with --compare a.json b.json).

Sets: base ({}), template flips (a few per sym_side), the V17 switches with defaults restated (must be identical to base),
and effect sets (AUGMENT_INTO_REENTRY on, FILTER_EXEMPTIONS per active filter x top entry family / REENTRY / AUGMENT).
"""
import argparse
import collections
import concurrent.futures as cf
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("V15_EVAL_CACHE", "0")
FIXED_SETS = {
    "base": {},
    "restate_v17_defaults": {"FILTER_EXEMPTIONS": {}, "AUGMENT_INTO_REENTRY_ENABLED": False, "REENTRY_AUGMENT_SIZE_MULT": 1.0},
    "fx_empty_lists": {"FILTER_EXEMPTIONS": {"MOM3": [], "REENTRY": [], "AUGMENT": []}},
    "fx_unknown_filter": {"FILTER_EXEMPTIONS": {"REENTRY": ["NOT_A_FILTER_KEY"], "AUGMENT_SIGNAL": ["MIN_GAIN_TO_BUY_AGGRESSIVELY"]}},
    "flip_dc_edge": {"DC_EDGE_SIZING_ENABLED": True},
    "flip_aal": {"AUGMENT_AT_LOSS_ENABLED": True},
    "air_on": {"AUGMENT_INTO_REENTRY_ENABLED": True},
    "air_on_no_hl": {"AUGMENT_INTO_REENTRY_ENABLED": True, "REENTRY_AUGMENT_REQUIRE_HIGHER_LOW": False},
    "air_on_hl_low": {"AUGMENT_INTO_REENTRY_ENABLED": True, "REENTRY_AUGMENT_HL_REF": "low"},
    "air_on_x0p5": {"AUGMENT_INTO_REENTRY_ENABLED": True, "REENTRY_AUGMENT_SIZE_MULT": 0.5},
    "air_on_win1h": {"AUGMENT_INTO_REENTRY_ENABLED": True, "REENTRY_AUGMENT_WINDOW_SEC": 3600.0},
    "pyr_bounce_on": {"PYRAMID_ENABLED": True, "BOUNCE_AUGMENT_ENABLED": True},
    "pyr_bounce_fx_uag": {"PYRAMID_ENABLED": True, "BOUNCE_AUGMENT_ENABLED": True, "FILTER_EXEMPTIONS": {"AUGMENT_SIGNAL": ["UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED"]}},
    "pyr_bounce_fx_uag_agg": {"PYRAMID_ENABLED": True, "BOUNCE_AUGMENT_ENABLED": True, "FILTER_EXEMPTIONS": {"AUGMENT_SIGNAL": ["UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", "AUG_GAIN_GATE_LIVE_TWIN_ENABLED"]}},
    "pyr_bounce_no_uag_agg_global": {"PYRAMID_ENABLED": True, "BOUNCE_AUGMENT_ENABLED": True, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED": False, "AUG_GAIN_GATE_LIVE_TWIN_ENABLED": False},
}


def _hash_ledger(led):
    h = hashlib.sha1()
    for e in led or []:
        h.update(json.dumps({k: (round(v, 9) if isinstance(v, float) else v) for k, v in sorted(e.items()) if k != "fixed_stop"}, sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


def _run(prep, ov):
    from tools.opt import evaluate_v12 as E
    from tools.opt import v12_pilot as VP
    r = E._evaluate_prepared_nocache(prep, VP._venue_augment_guard(prep, dict(ov)), include_ledger=True)
    led = r.get("execution_ledger") or []
    opens = [e for e in led if isinstance(e, dict) and e.get("type") == "OPEN"]
    reasons = collections.Counter(str(e.get("reason", ""))[:30] for e in opens)
    return {
        "gain_pct": round(float(r.get("gain_pct") or 0.0), 6),
        "trades": int(r.get("trades") or 0),
        "valid": bool(r.get("valid")),
        "invalid_reason": r.get("invalid_reason"),
        "augments": sum(1 for e in led if isinstance(e, dict) and e.get("type") == "AUGMENT"),
        "carries": sum(1 for e in opens if e.get("carry_aug_mult")),
        "fx_opens": sum(1 for e in opens if str(e.get("reason", "")).startswith("FX_EXEMPT_")),
        "ledger_hash": _hash_ledger(led),
        "top_open_reasons": dict(reasons.most_common(5)),
    }


def one(args):
    ss, engine_dir, effect = args
    if engine_dir:
        sys.path.insert(0, engine_dir)
    sys.path.insert(1 if engine_dir else 0, ROOT)
    import v12_quick_engine as V
    from tools.opt import evaluate_v12 as E
    prep = E.prepare(ss, 30)
    if prep is None:
        return ss, {"error": "prepare failed"}
    out = {"_engine_file": V.__file__}
    for name, ov in FIXED_SETS.items():
        try:
            out[name] = _run(prep, ov)
        except Exception as e:
            out[name] = {"error": str(e)[:200]}
    if effect:
        try:
            import vec_decisions.filter_exemptions as FX
        except Exception as e:
            out["_effect_error"] = str(e)
            return ss, out
        bcfg = prep.get("base_cfg_dict") or {}
        active = sorted(f for f in FX.FILTER_KEYS if (bcfg.get(f) is True) or (isinstance(bcfg.get(f), str) and str(bcfg.get(f)).upper() not in ("OFF", "", "NONE")))
        out["_active_filters"] = active
        fams = [r for r in (out.get("base", {}).get("top_open_reasons") or {}) if r.startswith("B")][:2]
        for f in active:
            out[f"fx_REENTRY|{f}"] = _run(prep, {"FILTER_EXEMPTIONS": {"REENTRY": [f]}})
            out[f"global_off|{f}"] = _run(prep, {f: FX.off_value(bcfg.get(f))})
            for fam in fams:
                out[f"fx_{fam}|{f}"] = _run(prep, {"FILTER_EXEMPTIONS": {fam: [f]}})
    return ss, out


def compare(a, b):
    rep = {"identical": 0, "diff": []}
    for ss in a:
        if ss.startswith("_") or ss not in b:
            continue
        for k, va in a[ss].items():
            if k.startswith("_") or not isinstance(va, dict):
                continue
            vb = b[ss].get(k)
            if not isinstance(vb, dict):
                continue
            same = all(va.get(x) == vb.get(x) for x in ("gain_pct", "trades", "augments", "ledger_hash"))
            if same:
                rep["identical"] += 1
            else:
                rep["diff"].append((ss, k, {x: (va.get(x), vb.get(x)) for x in ("gain_pct", "trades", "augments", "carries", "ledger_hash")}))
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symsides", default="")
    ap.add_argument("--engine-dir", default="")
    ap.add_argument("--effect", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--compare", nargs=2)
    a = ap.parse_args()
    if a.compare:
        print(json.dumps(compare(json.load(open(a.compare[0])), json.load(open(a.compare[1]))), indent=1, default=str))
        return
    sss = [s.strip() for s in a.symsides.split(",") if s.strip()]
    import multiprocessing as mp
    res = {}
    with cf.ProcessPoolExecutor(max_workers=min(a.workers, len(sss)), mp_context=mp.get_context("spawn")) as ex:
        for ss, r in ex.map(one, [(s, a.engine_dir, a.effect) for s in sss]):
            res[ss] = r
    res["_meta"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": os.uname().nodename, "engine_dir": a.engine_dir or "installed"}
    txt = json.dumps(res, indent=1, default=str)
    if a.out:
        open(a.out, "w").write(txt)
    print(txt[:2000])


if __name__ == "__main__":
    main()
