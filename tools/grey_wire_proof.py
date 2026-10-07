"""Grey-switch wiring proof (2026-09-30): default -> delta exactly 0 + identical ledger; alt -> changed ledger,
with the exit/entry reason histogram diff so the change is attributable to the wired decision (not to some
other path). Diagnostic only (single 30d window, never a promotion metric).

  python tools/grey_wire_proof.py SYM_SIDE[,SYM_SIDE...] SWITCH=alt [SWITCH=alt ...] [--ctx K=V,K=V] [--out FILE]
"""
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tools.opt import v12_pilot as P  # noqa: E402
import grey_switch_rewire_test as G  # noqa: E402

_PREP = {}


def _reasons(res, key):
    c = collections.Counter()
    for t in res.get("ledger") or []:
        if not isinstance(t, dict):
            continue
        if key == "entry":
            if t.get("type") == "OPEN":
                c[str(t.get("reason") or "")[:40]] += 1
        elif t.get("type") in ("CLOSE", "REDUCE") and "bar_exit" in t:
            c[str(t.get(key) or t.get("reason") or "").split(" ")[0].split("_g")[0][:40]] += 1
    return c


def prove(symside, switch, alt, ctx):
    prep = _PREP.get(symside)
    if prep is None:
        prep = _PREP[symside] = P.prepare_batch(symside, 30)
    if prep is None:
        return {"symside": symside, "switch": switch, "error": "no npz"}
    ctx = {k: G._coerce(k, v) for k, v in (ctx or {}).items()}
    base = P.evaluate_prepared_sanitized(prep, dict(ctx), 30, include_ledger=True)
    dflt = G.default_for(prep, switch)
    r_def = P.evaluate_prepared_sanitized(prep, {**ctx, switch: dflt}, 30, include_ledger=True)
    altv = G._coerce(switch, alt)
    r_alt = P.evaluate_prepared_sanitized(prep, {**ctx, switch: altv}, 30, include_ledger=True)
    bg = float(base.get("gain_pct") or 0.0)
    xb, xa = _reasons(base, "exit_reason"), _reasons(r_alt, "exit_reason")
    eb, ea = _reasons(base, "entry"), _reasons(r_alt, "entry")
    return {
        "symside": symside, "switch": switch, "ctx": ctx or None, "default": dflt, "alt": altv,
        "base_gain": round(bg, 6), "base_trades": base.get("trades"),
        "default_delta": round(float(r_def.get("gain_pct") or 0.0) - bg, 9),
        "default_ledger_same": G._ledger_key(r_def) == G._ledger_key(base),
        "alt_delta": round(float(r_alt.get("gain_pct") or 0.0) - bg, 6), "alt_trades": r_alt.get("trades"),
        "alt_ledger_changed": G._ledger_key(r_alt) != G._ledger_key(base),
        "exit_reason_diff": {k: xa.get(k, 0) - xb.get(k, 0) for k in set(xa) | set(xb) if xa.get(k, 0) != xb.get(k, 0)},
        "entry_reason_diff": {k: ea.get(k, 0) - eb.get(k, 0) for k in set(ea) | set(eb) if ea.get(k, 0) != eb.get(k, 0)},
        "valid": bool(r_alt.get("valid", True)),
    }


def main(argv):
    out = None
    if "--out" in argv:
        j = argv.index("--out")
        out = argv[j + 1]
        argv = argv[:j] + argv[j + 2:]
    ctx = {}
    if "--ctx" in argv:
        j = argv.index("--ctx")
        ctx = dict(kv.split("=", 1) for kv in argv[j + 1].split(","))
        argv = argv[:j] + argv[j + 2:]
    rows = []
    for s in argv[0].split(","):
        for kv in argv[1:]:
            k, v = kv.split("=", 1)
            try:
                r = prove(s, k, v, ctx)
            except Exception as e:  # report, never hide
                r = {"symside": s, "switch": k, "error": repr(e)}
            print(json.dumps(r, default=str), flush=True)
            rows.append(r)
    if out:
        Path(out).write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n")
    bad = [r for r in rows if "error" not in r and not (r["default_delta"] == 0 and r["default_ledger_same"])]
    print(f"[SUMMARY] rows={len(rows)} default_violations={len(bad)} alt_changed={sum(1 for r in rows if r.get('alt_ledger_changed'))}", flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
