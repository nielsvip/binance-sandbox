"""Filter-flip exhaust: turn every red-365D side green, one filter switch at a time.

USER 2026-10-08: neg 365D gains are one filter-switch flip away from positive.
For one sym_side, test EVERY filter switch (FILTER_DICTIONARY_V2 sheet) x EVERY
setting via real 365D evals, 30D-verify the improvers, greedy-apply the best
365D-positive flip, repeat until green or exhausted.

No new defaults, no fabrication: every number is a real v12_quick_engine eval
(evaluate_prepared_sanitized on the RAM-prepared slices, same as the s6 driver).
Unmeasured cells are null, never zero. Checkpoint per flip: resume never recalcs.
Budget slices exit 11 (40-min rule); relaunch resumes from the ledger.

Output {ss}_flip.json: base/applied/tried/final/status. Tried flips feed the
lever inventory by_switch_365 table (measured single-change 365D evidence).
"""
import concurrent.futures as cf
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import v15_pilot as P
from tools import v15_diagnose_repair as DR
from tools.opt import v12_pilot as VP
from tools.v15_graph_search import no_touch

BUILD = "flip1-20261008"
EXIT_SLICE = 11


def load_filter_dict(template: Path):
    """filter rows from the TEMPLATE dictionary sheet (same source the pilot uses)."""
    import openpyxl
    wb = openpyxl.load_workbook(str(template), data_only=True, read_only=True)
    ws = None
    for cand in ("FILTER_DICTIONARY_V2", "FILTER_DICTIONARY", "FILTER_DICTIONARY_V8"):
        if cand in wb.sheetnames:
            ws = wb[cand]
            break
    if ws is None:
        return []
    hdr = {str(ws.cell(1, c).value or "").strip(): c for c in range(1, 30)}
    fc = hdr.get("Filter", 2)
    oc2 = hdr.get("Options (all settings)")
    rows = []
    for r in range(2, ws.max_row + 1):
        f = ws.cell(r, fc).value
        if not f or not isinstance(f, str) or f.strip().isdigit():
            continue
        f = f.strip()
        if not f:
            continue
        opts = str(ws.cell(r, oc2).value or "") if oc2 else ""
        vals = []
        for part in opts.split(","):
            v = part.strip().split("(")[0].strip()
            if v and v not in vals:
                vals.append(v)
        if vals:
            rows.append((f, vals))
    try:
        wb.close()
    except Exception:
        pass
    return rows


def slim(r):
    if not r:
        return None
    return {k: r.get(k) for k in ("gain_pct", "trades", "tim_pct", "max_dd_pct", "wr_pct", "valid", "invalid_reason", "bh_pct")}


def run_side(ss, base_gs, template, outdir, budget=2400.0, workers=6, max_rounds=5, max_filters=0, only=None):
    t0 = time.time()
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    ckf = outdir / f"{ss}_flip.json"
    ck = {}
    try:
        if ckf.exists():
            ck = json.loads(ckf.read_text())
    except Exception:
        ck = {}
    if ck.get("status") in ("GREEN", "EXHAUSTED"):
        return {"symside": ss, "status": ck["status"], "note": "already finished"}

    def save(status):
        ck["status"] = status
        ck["elapsed_s"] = round(time.time() - t0, 1)
        tmp = ckf.with_suffix(".tmp")
        tmp.write_text(json.dumps(ck, default=str))
        tmp.replace(ckf)

    sym = ss.rsplit("_", 1)[0]
    g = json.loads(Path(base_gs).read_text())
    base = dict(g.get("best_overrides") or g.get("base_overrides") or {})
    if not base:
        return {"symside": ss, "error": "no base overrides in gs.json"}
    for ap in ck.get("applied") or []:
        base[ap["switch"]] = ap["to"]
    cat_side = ("CRYPTO" if sym.endswith("USDC") or sym.endswith("USDT") else "STOCKS") + ("_SHORT" if ss.endswith("_SHORT") else "_LONG")
    defaults = P.get_defaults_for_symside(ss)
    san = lambda ov: P.sanitize_overrides(ov, defaults)[0]
    prep30 = VP.prepare_batch(ss, 30)
    prep365 = VP.prepare_batch(ss, 365)
    if not prep30 or not prep365:
        return {"symside": ss, "error": "NPZ prepare failed"}
    try:
        t = ((prep365.get("npz_prepared") or {}).get("timestamps")) or []
        span = float((t[-1] - t[0]) / 86400000) if len(t) > 1 else 0.0
    except Exception:
        span = 0.0
    ex = cf.ThreadPoolExecutor(max_workers=workers)

    def one(prep, ov, wd, to=180.0):
        f = ex.submit(VP.evaluate_prepared_sanitized, prep, dict(ov), wd)
        try:
            return f.result(timeout=to)
        except Exception:
            try:
                f.cancel()
            except Exception:
                pass
            return None

    base30, base365 = None, None
    if ck.get("base"):
        base30, base365 = ck["base"].get("m30"), ck["base"].get("m365")
    else:
        base30, base365 = slim(one(prep30, san(base), 30)), slim(one(prep365, san(base), 365))
        ck.update({"symside": ss, "cat_side": cat_side, "build": BUILD, "span_365": span,
                   "base": {"m30": base30, "m365": base365}, "applied": ck.get("applied") or [], "tried": ck.get("tried") or []})
        save("RUNNING")
    b365g = (base365 or {}).get("gain_pct")
    if b365g is not None and b365g > 0:
        save("GREEN")
        return {"symside": ss, "status": "GREEN", "note": "base already green"}
    tried_keys = {(x.get("round"), x.get("switch"), json.dumps(x.get("to"), default=str)) for x in ck.get("tried", [])}
    filters = load_filter_dict(Path(template))
    if only:
        want = {w.strip() for w in only.split(",") if w.strip()}
        filters = [(f, v) for f, v in filters if f in want]
    if max_filters:
        filters = filters[:max_filters]
    skipped_unknown = [f for f, _v in filters if f not in defaults]
    filters = [(f, v) for f, v in filters if f in defaults and not no_touch(f)]
    ck["n_filters"] = len(filters)
    ck["skipped_unknown"] = sorted(set(skipped_unknown))

    def over():
        return (time.time() - t0) > budget

    for rnd in range(max_rounds):
        cands = []
        for f, vals in filters:
            cur = base.get(f, defaults.get(f))
            for v in vals:
                if over():
                    save("TIME_SLICE")
                    return {"symside": ss, "status": "TIME_SLICE", "round": rnd}
                try:
                    if P.same_val(v, cur):
                        continue
                except Exception:
                    pass
                key = (rnd, f, json.dumps(v, default=str))
                if key in tried_keys:
                    continue
                ov = dict(base)
                ov[f] = v
                r365 = one(prep365, san(ov), 365)
                m365 = slim(r365)
                rec = {"switch": f, "from": cur, "to": v, "round": rnd, "m30": None, "m365": m365,
                       "d365": (round((m365["gain_pct"] or 0) - (b365g or 0), 4) if m365 else None),
                       "ok365": bool(m365 and P._qualifies_365d(r365, span)[0])}
                tried_keys.add(key)
                ck["tried"].append(rec)
                if rec["d365"] is not None and rec["d365"] > 0:
                    r30 = one(prep30, san(ov), 30)
                    rec["m30"] = slim(r30)
                    rec["ok30"] = bool(r30 and DR.compliant(DR.metrics(r30)))
                    if rec["ok365"] and rec["ok30"]:
                        cands.append(rec)
                if len(ck["tried"]) % 25 == 0:
                    save("RUNNING")
        save("RUNNING")
        if not cands:
            save("EXHAUSTED")
            ck["final"] = {"m30": base30, "m365": base365, "applied": ck["applied"]}
            save("EXHAUSTED")
            return {"symside": ss, "status": "EXHAUSTED", "rounds": rnd + 1, "tried": len(ck["tried"])}
        cands.sort(key=lambda c: ((c["m365"] or {}).get("gain_pct") or -1e9, (c["m30"] or {}).get("gain_pct") or -1e9), reverse=True)
        win = cands[0]
        base[win["switch"]] = win["to"]
        ck["applied"].append({"switch": win["switch"], "from": win["from"], "to": win["to"], "m30": win["m30"], "m365": win["m365"]})
        base30, base365 = win["m30"], win["m365"]
        b365g = (base365 or {}).get("gain_pct")
        ck["base"] = {"m30": base30, "m365": base365}
        save("RUNNING")
        if b365g is not None and b365g > 0:
            ck["final"] = {"m30": base30, "m365": base365, "applied": ck["applied"]}
            save("GREEN")
            return {"symside": ss, "status": "GREEN", "rounds": rnd + 1, "applied": len(ck["applied"])}
    ck["final"] = {"m30": base30, "m365": base365, "applied": ck["applied"]}
    save("EXHAUSTED")
    return {"symside": ss, "status": "EXHAUSTED", "rounds": max_rounds}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--symside", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget", type=float, default=2400.0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--max-filters", type=int, default=0)
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    r = run_side(a.symside, a.base, a.template, a.out, a.budget, a.workers, a.max_rounds, a.max_filters, a.only)
    print(json.dumps({k: (v if k != "tried" else f"<{len(v)} flips>") for k, v in r.items()}, default=str), flush=True)
    if r.get("status") == "TIME_SLICE":
        sys.exit(EXIT_SLICE)


if __name__ == "__main__":
    main()
