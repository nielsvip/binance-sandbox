#!/usr/bin/env python3
"""v15_reason_audit — which trade REASONS carry a sym_side, and which filters can gate that specific family (USER 2026-10-06).

Vec side (real engine, 30D slice in RAM, S2/S5 only):
  1. ledger of the sym_side's set (graph-search best set from --runs, else the driver's base) -> per entry-reason and
     exit-reason family: count, WR, avg win / avg loss (pp of this run's gain), contribution to total gain, share of closes.
  2. frequent families (>= max(8, 15% of closes)) -> candidate hooks: TEMPLATE rows whose switch name shares a significant
     token with the reason (HARDCODED_RALLY_* for HARDCODED_RALLY_REENTRY, EXIT_VELOCITY_WT_* for EXIT_VELOCITY_WT, ...),
     the reentry filters for reentry families, the yellow filters the graph links to those switches, and the generic entry
     FILTER_TFs for fresh-entry families (B*, ENTRY_SIGNAL) — fresh-entry B-blocks are REASON LABELS of entry_sig
     (compute_reentry_blocks is used for tagging only), so no per-block hook exists in the engine.
  3. every candidate evaluated (ledger) on the set: Δgain, ΔWR, Δtrades for the whole set AND the family's own n / WR / pnl
     after; specificity = share of the trades it changed that belong to the family.
  4. a family where no candidate changes its trades with specificity >= 0.6 = MISSING_FILTER (proposal: switch + vec + live).
Live side: data/history/{acct}/{SS}.jsonl OPEN/CLOSE reasons (|VEC_EXACT tagged ones separately): count per reason family,
  realised pnl% of CLOSE vs the previous OPEN price where both exist.
usage: nice -n 15 .venv/bin/python tools/v15_reason_audit.py --symsides A,B --runs ~/v15_graph_20261006 --history ~/v15_graph_20261006/history --out ~/v15_graph_20261006/reasons
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import json
import multiprocessing as mp
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
P30 = None
REENTRY_HOOKS = ("REENTRY_ENTRY_FILTER_ENABLED", "REENTRY_FILTER_MIN_PASS", "REENTRY_APPLY_ENTRY_GATES_ENABLED")
STOP = {"ENTRY", "EXIT", "REENTRY", "SIGNAL", "CLOSE", "TF", "ENABLED", "FILTER", "THE", "DC", "WT", "HTF", "15M", "1H", "4H", "D"}


def fam(reason: str) -> str:
    s = str(reason or "?").split("|", 1)[0].strip()
    m = re.match(r"([A-Z][A-Z0-9_]*?)(?:_g[-+]?\d|_\d|\s|\(|:|$)", s)
    s = m.group(1) if m else s.split(" ", 1)[0]
    return s[:48] or "?"


def tokens(name: str) -> set:
    return {t for t in re.split(r"[_\W]+", name.upper()) if len(t) >= 3 and t not in STOP and not t.isdigit()}


def _wl(ov):
    from tools.opt import v12_pilot as VP
    from tools import v15_trade_autopsy as TA
    r = VP.evaluate_prepared_sanitized(P30, ov, 30, True)
    rows = TA.scaled_rows(r)
    return {"gain": r.get("gain_pct"), "trades": r.get("trades"), "wr": r.get("wr_pct"), "valid": r.get("valid"),
            "rows": [{"be": t["be"], "bx": t["bx"], "pnl": t["pnl"], "er": fam(t["entry_reason"]), "xr": fam(t["exit_reason"])} for t in rows]}


def fam_stats(rows: list, key: str) -> dict:
    tot = sum(t["pnl"] for t in rows)
    out = defaultdict(lambda: {"n": 0, "wins": 0, "win_pp": 0.0, "loss_pp": 0.0, "pnl_pp": 0.0})
    for t in rows:
        a = out[t[key]]
        a["n"] += 1
        a["pnl_pp"] += t["pnl"]
        if t["pnl"] > 0:
            a["wins"] += 1
            a["win_pp"] += t["pnl"]
        else:
            a["loss_pp"] += t["pnl"]
    res = {}
    for k, a in out.items():
        n, w = a["n"], a["wins"]
        res[k] = {"n": n, "share": round(n / max(1, len(rows)), 3), "wr": round(100.0 * w / n, 1), "avg_win_pp": round(a["win_pp"] / w, 4) if w else None,
                  "avg_loss_pp": round(a["loss_pp"] / (n - w), 4) if n - w else None, "pnl_pp": round(a["pnl_pp"], 4), "contribution": round(a["pnl_pp"] / tot, 3) if abs(tot) > 1e-12 else None}
    return dict(sorted(res.items(), key=lambda x: -x[1]["n"]))


def run_symside(ss: str, runs: Path, out: Path, workers: int) -> dict:
    global P30
    import v15_pilot as P
    from tools import v15_repair_driver as RD
    from tools.opt import v12_pilot as VP
    from tools import v15_graph_search as GS
    GS._oom_first()
    defaults = P.get_defaults_for_symside(ss)
    san = lambda ov: P.sanitize_overrides(ov, defaults)[0]  # noqa: E731
    P30 = VP.prepare_batch(ss, 30)
    src, ov = "cat_side_defaults", {}
    cands_json = sorted(glob.glob(str(runs / "**" / f"{ss}.gs.json"), recursive=True), key=os.path.getmtime)
    if cands_json:
        j = json.load(open(cands_json[-1]))
        ov, src = dict(j.get("best_overrides") or j.get("base_overrides") or {}), f"graph_search best ({cands_json[-1].split('/')[-2]})"
    ov = san(ov)
    cat = ("CRYPTO" if RD._is_crypto(ss) else "STOCKS") + "_" + ss.rsplit("_", 1)[1]
    G = GS.load_graph(cat)
    pool = cf.ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork"), initializer=GS._pdeathsig)
    try:
        base = pool.submit(_wl, ov).result(timeout=300)
        rows = base["rows"]
        ent, ext = fam_stats(rows, "er"), fam_stats(rows, "xr")
        cands = RD.candidates_for(ss, defaults, P)
        floor = max(8, int(0.15 * len(rows)))
        frequent = [("er", k) for k, a in ent.items() if a["n"] >= floor] + [("xr", k) for k, a in ext.items() if a["n"] >= floor]
        tests, plan = [], []
        for side, f in frequent:
            ft = tokens(f)
            sel = [c for c in cands if ft & tokens(c["switch"])]
            if side == "er" and ("REENTRY" in f or "RALLY" in f):
                sel += [c for c in cands if c["switch"] in REENTRY_HOOKS]
            linked = set()
            for c in sel:
                linked |= set((G.get("yellow") or {}).get(c["switch"], []))
            sel += [c for c in cands if c["switch"] in linked]
            if side == "er" and (f.startswith("B") or f in ("ENTRY_SIGNAL", "VECTOR_ENTRY")):
                sel += [c for c in cands if c["switch"].endswith("_FILTER_TF") and c["tab"].startswith("ENTRY")]
            seen = set()
            for c in sel:
                k = (c["switch"], str(c["cand"]))
                if k in seen or all(RD.same_val(ov.get(kk, defaults.get(kk)), vv) for kk, vv in c["ov"].items()):
                    continue
                seen.add(k)
                plan.append((side, f, c))
        futs = [pool.submit(_wl, san({**ov, **c["ov"]})) for _s, _f, c in plan]
        bset = {(t["be"], t["bx"], round(t["pnl"], 9)) for t in rows}
        bfam = {(t["be"], t["bx"], round(t["pnl"], 9)): t for t in rows}
        for (side, f, c), fu in zip(plan, futs):
            try:
                r = fu.result(timeout=600)
            except Exception as e:
                tests.append({"family": f, "side": side, "switch": c["switch"], "cand": str(c["cand"]), "error": str(e)[:80]})
                continue
            cset = {(t["be"], t["bx"], round(t["pnl"], 9)) for t in r["rows"]}
            removed = bset - cset
            changed_fam = sum(1 for k in removed if bfam[k][side] == f)
            spec = changed_fam / len(removed) if removed else None
            fs = fam_stats(r["rows"], side).get(f, {"n": 0, "wr": None, "pnl_pp": 0.0})
            fb = (ent if side == "er" else ext)[f]
            tests.append({"family": f, "side": side, "tab": c["tab"], "row": c.get("row"), "switch": c["switch"], "cand": str(c["cand"]),
                          "d_gain": round((r["gain"] or 0) - (base["gain"] or 0), 4), "d_wr": round((r["wr"] or 0) - (base["wr"] or 0), 2), "d_trades": (r["trades"] or 0) - (base["trades"] or 0),
                          "valid": r["valid"], "fam_n": fs["n"], "fam_n_before": fb["n"], "fam_wr": fs["wr"], "fam_wr_before": fb["wr"], "fam_pnl_pp": fs["pnl_pp"], "fam_pnl_before": fb["pnl_pp"],
                          "trades_changed": len(removed), "specificity": round(spec, 3) if spec is not None else None})
        verdict = {}
        for side, f in frequent:
            ts = [t for t in tests if t.get("family") == f and t.get("side") == side and not t.get("error")]
            spec_ts = [t for t in ts if (t["specificity"] or 0) >= 0.6 and t["trades_changed"] > 0]
            best = max(spec_ts, key=lambda t: t["d_gain"], default=None)
            verdict[f"{side}:{f}"] = {"n_tests": len(ts), "n_specific": len(spec_ts), "best_specific": best,
                                      "status": "HOOK_EXISTS" if spec_ts else ("ONLY_GENERIC_HOOKS" if any(t["trades_changed"] for t in ts) else "MISSING_FILTER")}
        rep = {"symside": ss, "set_source": src, "base": {k: base[k] for k in ("gain", "trades", "wr", "valid")}, "n_closes": len(rows), "entry_families": ent, "exit_families": ext,
               "frequent": [f"{s}:{f}" for s, f in frequent], "tests": sorted(tests, key=lambda t: -(t.get("d_gain") or -1e9)), "verdict": verdict, "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        (out / f"{ss}.reasons.json").write_text(json.dumps(rep, default=str))
        print(f"[REASONS] {ss} {src} closes={len(rows)} frequent={rep['frequent']} tests={len(tests)} verdict={ {k: v['status'] for k, v in verdict.items()} }", flush=True)
        return {"symside": ss}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def live_reasons(hist: Path) -> dict:
    """live fills per account: reason family counts + realised pnl% of CLOSE vs previous OPEN (same file)."""
    out = defaultdict(lambda: {"n_open": 0, "n_close": 0, "vec_exact": 0, "pnl": [], "accounts": set()})
    for p in glob.glob(str(hist / "*" / "*.jsonl")):
        acct, ss = p.split("/")[-2], Path(p).stem
        side = ss.rsplit("_", 1)[-1]
        last_open = None
        for line in open(p, errors="ignore"):
            try:
                e = json.loads(line)
            except Exception:
                continue
            typ, rs = str(e.get("type", "")).upper(), str(e.get("reason", ""))
            f = fam(rs)
            a = out[f"{typ}:{f}"]
            a["accounts"].add(acct)
            if "VEC_EXACT" in rs:
                a["vec_exact"] += 1
            if typ == "OPEN":
                a["n_open"] += 1
                try:
                    last_open = float(e.get("price"))
                except Exception:
                    pass
            elif typ == "CLOSE":
                a["n_close"] += 1
                try:
                    px = float(e.get("price"))
                    if last_open:
                        a["pnl"].append((px / last_open - 1) * 100 * (1 if side == "LONG" else -1))
                except Exception:
                    pass
    res = {}
    for k, a in out.items():
        pn = a["pnl"]
        res[k] = {"n": a["n_open"] + a["n_close"], "vec_exact": a["vec_exact"], "accounts": sorted(a["accounts"]), "n_pnl": len(pn),
                  "wr": round(100.0 * sum(1 for x in pn if x > 0) / len(pn), 1) if pn else None, "avg_pnl_pct": round(sum(pn) / len(pn), 4) if pn else None}
    return dict(sorted(res.items(), key=lambda x: -x[1]["n"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symsides", default="")
    ap.add_argument("--runs", required=True)
    ap.add_argument("--history", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    os.chdir(ROOT)
    out = Path(os.path.expanduser(a.out))
    out.mkdir(parents=True, exist_ok=True)
    if a.history:
        lr = live_reasons(Path(os.path.expanduser(a.history)))
        (out / "live_reasons.json").write_text(json.dumps(lr, default=str))
        print(f"[REASONS] live families: {len(lr)}; top: {[(k, v['n'], v['wr']) for k, v in list(lr.items())[:12]]}", flush=True)
    for ss in [s for s in a.symsides.split(",") if s]:
        try:
            ex = cf.ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("spawn"))
            ex.submit(run_symside, ss, Path(os.path.expanduser(a.runs)), out, a.workers).result()
            ex.shutdown(wait=False)
        except Exception as e:
            print(f"[REASONS] {ss} failed: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
