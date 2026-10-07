"""Grey-switch rewire proof: default -> delta exactly 0, alt -> changed trade ledger.

Usage (repo root):
  python tools/grey_switch_rewire_test.py SYM_SIDE[,SYM_SIDE...] SWITCH=alt [SWITCH=alt ...]
  python tools/grey_switch_rewire_test.py --plan tools/grey_switch_rewire_plan.json

For each (sym_side, switch, alt) it runs tools.opt.v12_pilot.evaluate_prepared_sanitized with
include_ledger=True for {} (baseline), {SWITCH: <QuickConfig default>} and {SWITCH: alt}, then
prints gain deltas and whether the ledger (entry/exit bar+reason list) changed.  The default run
must reproduce the baseline ledger bit-for-bit; a non-zero default delta is a P0 integrity bug.
Diagnostic tool only (single-window 30d, no promotion) — numbers are NOT pool_sharpe results.
"""
import dataclasses as dc
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import v12_quick_engine as V
from tools.opt import v12_pilot as P


def _ledger_key(res):
    led = res.get("ledger") or res.get("trades_list") or res.get("events") or []
    out = []
    for t in led if isinstance(led, list) else []:
        if isinstance(t, dict):
            out.append(tuple((k, str(t.get(k))) for k in sorted(t) if k in ("type", "bar", "ts", "price", "qty", "reason", "bar_entry", "bar_exit", "exit_reason", "pnl_pct")))
        else:
            out.append(str(t))
    return out


def _coerce(name, raw):
    d = {f.name: f.default for f in dc.fields(V.QuickConfig)}.get(name)
    if isinstance(raw, str):
        if isinstance(d, bool):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        if isinstance(d, int):
            return int(float(raw))
        if isinstance(d, float):
            return float(raw)
    return raw


def default_for(prep, name):
    """The value the SWEEP baseline uses: prepare_batch base_cfg_dict (raw QuickConfig — the v15 sweep
    path never calls apply_tradier_defaults) + evaluate_v12._VENUE_BAND_DEFAULTS for the venue."""
    from tools.opt import evaluate_v12 as E
    mode = "crypto" if prep.get("mode") == "crypto" else "tradier"
    table = E._VENUE_BAND_DEFAULTS.get(mode, {})
    if name in table:
        return table[name]
    return prep.get("base_cfg_dict", {}).get(name, getattr(V.QuickConfig(), name))


def run(symside, switch, alt, ctx=None, prep_cache={}):
    prep = prep_cache.get(symside)
    if prep is None:
        prep = P.prepare_batch(symside, 30)
        prep_cache[symside] = prep
    if prep is None:
        return {"symside": symside, "switch": switch, "error": "no npz"}
    ctx = {k: _coerce(k, v) for k, v in (ctx or {}).items()}
    base = P.evaluate_prepared_sanitized(prep, dict(ctx), 30, include_ledger=True)
    dflt = default_for(prep, switch)
    r_def = P.evaluate_prepared_sanitized(prep, {**ctx, switch: dflt}, 30, include_ledger=True)
    r_alt = P.evaluate_prepared_sanitized(prep, {**ctx, switch: _coerce(switch, alt)}, 30, include_ledger=True)
    bg = float(base.get("gain_pct") or 0.0)
    return {
        "symside": symside, "switch": switch, "ctx": ctx or None, "default": dflt, "alt": _coerce(switch, alt),
        "base_gain": round(bg, 6), "base_trades": base.get("trades"),
        "default_delta": round(float(r_def.get("gain_pct") or 0.0) - bg, 9),
        "default_ledger_same": _ledger_key(r_def) == _ledger_key(base),
        "alt_delta": round(float(r_alt.get("gain_pct") or 0.0) - bg, 6), "alt_trades": r_alt.get("trades"),
        "alt_ledger_changed": _ledger_key(r_alt) != _ledger_key(base),
        "ledger_keys": sorted(k for k in base if "ledger" in k or "trade" in k)[:6],
    }


def main(argv):
    if argv and argv[0] == "--plan":
        plan = json.loads(Path(argv[1]).read_text())
    else:
        ctx = {}
        if "--ctx" in argv:
            j = argv.index("--ctx")
            ctx = dict(kv.split("=", 1) for kv in argv[j + 1].split(","))
            argv = argv[:j] + argv[j + 2:]
        syms = argv[0].split(",")
        plan = [{"symside": s, "switch": kv.split("=", 1)[0], "alt": kv.split("=", 1)[1], "ctx": ctx} for s in syms for kv in argv[1:]]
    out = []
    for p in plan:
        try:
            r = run(p["symside"], p["switch"], p["alt"], p.get("ctx"))
        except Exception as e:  # report, never hide
            r = {"symside": p["symside"], "switch": p["switch"], "error": repr(e)}
        r.pop("ledger_keys", None)
        print(json.dumps(r, default=str), flush=True)
        out.append(r)
    return out


if __name__ == "__main__":
    main(sys.argv[1:])
