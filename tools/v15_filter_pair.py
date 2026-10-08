"""Filter-pair exhaust, round P: rescue EXHAUSTED sides whose 365D-greening singles
died on 30D-compliance (TIM/trade collapse — regime lottery, correctly refused).

Reads {ss}_flip.json tried[] (measured singles, no recompute):
  A. OPENER pairs (sides with >=1 ok365 single): top-8 ok365 singles x top-12
     30D-openers (ranked by measured 30D trades, then TIM) -> pair must pass
     FULL gates (365D qualifies + 30D compliant). Greedy-apply best, rescreen once.
  B. COMBO pairs (sides with no ok365 single): top-12 live d365 singles,
     all pairs -> same gates, one round.
Bounds: <=96 pair-evals + shortlist 30D fills per side (~10-15 min), checkpoint
per pair in the same ledger (pairs[]), resume never recalcs. Budget slices exit 11.
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

BUILD = "pair1-20261008"
EXIT_SLICE = 11


def slim(r):
    if not r:
        return None
    return {k: r.get(k) for k in ("gain_pct", "trades", "tim_pct", "max_dd_pct", "wr_pct", "valid", "invalid_reason", "bh_pct")}


def run_side(ss, flip_path, base_gs, outdir, budget=2300.0, workers=4, top_k=8, top_o=12):
    t0 = time.time()
    outdir = Path(outdir)
    ckf = outdir / f"{ss}_flip.json"
    ck = json.loads(Path(flip_path).read_text())
    if ck.get("status") == "GREEN":
        return {"symside": ss, "status": "GREEN", "note": "already green"}
    g = json.loads(Path(base_gs).read_text())
    winner = dict(g.get("best_overrides") or g.get("base_overrides") or {})
    if not winner:
        return {"symside": ss, "error": "no winner overrides in gs.json"}
    pairs_done = {(tuple(sorted((x.get("a"), x.get("b")))), x.get("round", "P")) for x in ck.get("pairs", [])}

    def save(status):
        ck["status"] = status
        ck["elapsed_s"] = round(time.time() - t0, 1)
        tmp = ckf.with_suffix(".tmp")
        tmp.write_text(json.dumps(ck, default=str))
        tmp.replace(ckf)

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

    tried = [t for t in ck.get("tried", []) if t.get("d365") is not None]
    live = [t for t in tried if ((t.get("m365") or {}).get("trades") or 0) > 0]
    ok365 = [t for t in live if t.get("ok365")]
    base = dict(winner)
    for ap in ck.get("applied", []):
        base[ap["switch"]] = ap["to"]
    gs_base = ck.get("base", {})
    b365g = (gs_base.get("m365") or {}).get("gain_pct")
    if b365g is not None and b365g > 0:
        save("GREEN")
        return {"symside": ss, "status": "GREEN", "note": "base already green"}
    # shortlists (single-change ledgers -> pair candidates; no recompute for 365D ranks)
    _fill = sorted(live, key=lambda t: t["d365"], reverse=True)[:24]  # bounded 30D fill widens the opener pool honestly
    for t in _fill:
        if (time.time() - t0) > budget:
            save("TIME_SLICE")
            return {"symside": ss, "status": "TIME_SLICE", "mode": "FILL30"}
        if t.get("m30") is None:
            r30 = one(prep30, san({**base, t["switch"]: t["to"]}), 30)
            t["m30"] = slim(r30)
    save("RUNNING-P")
    ok365_sorted = sorted(ok365, key=lambda t: ((t.get("m365") or {}).get("gain_pct") or -1e9), reverse=True)[:top_k]
    if ok365_sorted:
        mode = "OPENER"
        # openers: measured 30D trades/TIM first; unmeasured get a bounded 30D fill below
        with30 = [t for t in live if t.get("m30")]
        with30.sort(key=lambda t: (((t["m30"] or {}).get("trades") or 0), ((t["m30"] or {}).get("tim_pct") or 0)), reverse=True)
        openers = with30[:top_o]
        staticks = [(a, b) for a in ok365_sorted for b in openers if a["switch"] != b["switch"]]
    else:
        mode = "COMBO"
        live_sorted = sorted(live, key=lambda t: t["d365"], reverse=True)[:top_k + 4]
        staticks = [(live_sorted[i], live_sorted[j]) for i in range(len(live_sorted)) for j in range(i + 1, len(live_sorted)) if live_sorted[i]["switch"] != live_sorted[j]["switch"]]
    ck.setdefault("pairs", [])
    ck["pair_mode"] = mode
    cands = []

    def over():
        return (time.time() - t0) > budget

    for a, b in staticks[:96]:
        if over():
            save("TIME_SLICE")
            return {"symside": ss, "status": "TIME_SLICE", "mode": mode}
        key = tuple(sorted(((a["switch"], json.dumps(a["to"], default=str)), (b["switch"], json.dumps(b["to"], default=str)))))
        if (key, "P") in pairs_done:
            continue
        ov = dict(base)
        ov[a["switch"]] = a["to"]
        ov[b["switch"]] = b["to"]
        r365 = one(prep365, san(ov), 365)
        m365 = slim(r365)
        rec = {"a": key[0][0], "av": a["to"], "b": key[1][0], "bv": b["to"], "round": "P", "m30": None, "m365": m365,
               "d365": (round((m365["gain_pct"] or 0) - (b365g or 0), 4) if m365 else None),
               "ok365": bool(m365 and P._qualifies_365d(r365, span)[0])}
        ck["pairs"].append(rec)
        pairs_done.add((key, "P"))
        if rec["ok365"]:
            r30 = one(prep30, san(ov), 30)
            rec["m30"] = slim(r30)
            rec["ok30"] = bool(r30 and DR.compliant(DR.metrics(r30)))
            if rec["ok30"]:
                cands.append(rec)
        if len(ck["pairs"]) % 20 == 0:
            save("RUNNING-P")
    save("RUNNING-P")
    if not cands:
        save("EXHAUSTED-P")
        return {"symside": ss, "status": "EXHAUSTED-P", "mode": mode, "pairs": len(ck["pairs"])}
    cands.sort(key=lambda c: ((c["m365"] or {}).get("gain_pct") or -1e9, (c["m30"] or {}).get("gain_pct") or -1e9), reverse=True)
    win = cands[0]
    ck["applied"].append({"switch": win["a"], "to": win["av"], "m30": win["m30"], "m365": win["m365"], "pair_with": win["b"]})
    ck["applied"].append({"switch": win["b"], "to": win["bv"], "m30": win["m30"], "m365": win["m365"], "pair_with": win["a"]})
    ck["final"] = {"m30": win["m30"], "m365": win["m365"], "applied": ck["applied"]}
    save("GREEN")
    return {"symside": ss, "status": "GREEN", "mode": mode, "pair": [win["a"], win["b"]], "g365": (win["m365"] or {}).get("gain_pct")}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--symside", required=True)
    ap.add_argument("--flip", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget", type=float, default=2300.0)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    r = run_side(a.symside, a.flip, a.base, a.out, a.budget, a.workers)
    print(json.dumps(r, default=str), flush=True)
    if r.get("status") == "TIME_SLICE":
        sys.exit(EXIT_SLICE)


if __name__ == "__main__":
    main()
