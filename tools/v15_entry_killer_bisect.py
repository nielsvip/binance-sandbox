#!/usr/bin/env python3
"""v15_entry_killer_bisect — which TEMPLATE bold defaults kill every entry of a sym_side? (2026-10-08 system audit:
CRYPTO_SHORT template defaults = 0 trades on KSMUSDT_SHORT / UNIUSDC_SHORT / ETHUSDC_SHORT while engine defaults trade
82 / 140 / n — a sweep cannot repair a 0-trade base one row at a time, every delta is 0.)

Method (real engine evals only, 30D prepared NPZ): start from the full template-bold set B (0 trades) and the engine
defaults E (trades>0). Greedy group bisection on the keys where B differs from E: drop a group of keys (revert them to E)
and re-evaluate; keep narrowing groups that restore trades until single keys are isolated. Reports the minimal killer
keys (with trades/gain when each one alone is reverted) and the trades/gain of the base with ALL killers reverted.

  .venv/bin/python tools/v15_entry_killer_bisect.py --symsides KSMUSDT_SHORT,UNIUSDC_SHORT --out ~/v15_killers
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
FLOOR = 10


def _eval(prep, ov):
    from tools.opt.v12_pilot import evaluate_prepared_sanitized
    r = evaluate_prepared_sanitized(prep, dict(ov), 30)
    return int(r.get("trades") or 0), float(r.get("gain_pct") or 0.0), bool(r.get("valid"))


def bisect(ss, out, max_evals=400):
    import v15_pilot as P
    from tools.opt.v12_pilot import prepare_batch
    t0 = time.time()
    defaults = P.get_defaults_for_symside(ss)
    san = lambda ov: P.sanitize_overrides(ov, defaults)[0]
    bolds, bad = P.template_bold_defaults(P.get_template_for_symside(ss), defaults)
    B = san(bolds)
    prep = prepare_batch(ss, 30)
    tB, gB, vB = _eval(prep, B)
    tE, gE, vE = _eval(prep, {})
    rep = {"symside": ss, "template_base": {"keys": len(B), "trades": tB, "gain": gB, "valid": vB}, "engine_defaults": {"trades": tE, "gain": gE, "valid": vE}, "layout_violations": len(bad)}
    if tB >= FLOOR or tE < FLOOR:
        rep["status"] = "NOT_A_KILLER_CASE" if tB >= FLOOR else "ENGINE_DEFAULTS_ALSO_DEAD"
        return rep
    # keys where the template bold differs from the engine default (the only keys that can be killers)
    qc = {}
    try:
        import dataclasses as _dc
        import v12_quick_engine as _V
        qc = {f.name: f.default for f in _dc.fields(_V.QuickConfig)}
    except Exception:
        pass
    diff = [k for k, v in B.items() if k in qc and qc[k] != v]
    rep["differing_keys"] = len(diff)
    evals = 2
    killers = []
    work = dict(B)

    def trades_without(keys):
        ov = {k: v for k, v in work.items() if k not in set(keys)}
        return _eval(prep, ov)

    # iterative: find groups whose reversion restores trades, narrow to singles, revert them, repeat until floor reached
    while evals < max_evals:
        t_all, g_all, _ = trades_without(diff)
        evals += 1
        if t_all < FLOOR:
            rep["note"] = f"reverting ALL {len(diff)} differing keys gives only {t_all} trades — killers outside the bold/QuickConfig diff (cat_side/per_sym/data)"
            break
        groups = [diff[i::8] for i in range(8)]
        found = None
        for grp in groups:
            if not grp:
                continue
            t, g, _ = trades_without(grp)
            evals += 1
            if t >= FLOOR:
                found = grp
                break
        if found is None:
            # no single eighth restores trades -> killers are a conjunction; take the group with the most trades and narrow it
            best = max(((trades_without(grp)[0], grp) for grp in groups if grp), key=lambda x: x[0])
            evals += len([g for g in groups if g])
            found = best[1]
        # narrow found to single keys
        while len(found) > 1 and evals < max_evals:
            half = found[: len(found) // 2]
            t, g, _ = trades_without(half)
            evals += 1
            found = half if t >= FLOOR else found[len(found) // 2:]
        key = found[0]
        t1, g1, v1 = trades_without([key])
        evals += 1
        killers.append({"key": key, "bold": B.get(key), "engine_default": qc.get(key), "trades_if_reverted_alone": t1, "gain_if_reverted_alone": round(g1, 3)})
        work.pop(key, None)
        diff = [k for k in diff if k != key]
        tw, gw, vw = _eval(prep, work)
        evals += 1
        rep.setdefault("steps", []).append({"reverted": key, "trades": tw, "gain": round(gw, 3), "valid": vw})
        if tw >= FLOOR:
            break
    rep["killers"] = killers
    rep["after_reverting_killers"] = {"trades": tw if killers else tB, "gain": round(gw, 3) if killers else gB, "valid": vw if killers else vB}
    rep["evals"] = evals
    rep["secs"] = round(time.time() - t0, 1)
    rep["status"] = "KILLERS_FOUND" if killers and rep["after_reverting_killers"]["trades"] >= FLOOR else "PARTIAL"
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--symsides", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(os.path.expanduser(a.out))
    out.mkdir(parents=True, exist_ok=True)
    for ss in [s for s in a.symsides.split(",") if s]:
        try:
            rep = bisect(ss, out)
        except Exception as e:
            rep = {"symside": ss, "status": "ERROR", "error": repr(e)[:300]}
        (out / f"{ss}_killers.json").write_text(json.dumps(rep, indent=1, default=str))
        print(f"[killers] {ss} {rep['status']} template={rep.get('template_base')} engine={rep.get('engine_defaults')} killers={[ (k['key'], k['bold'], k['engine_default'], k['trades_if_reverted_alone']) for k in rep.get('killers', [])]} after={rep.get('after_reverting_killers')} evals={rep.get('evals')} {rep.get('note', '')} {rep.get('error', '')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
