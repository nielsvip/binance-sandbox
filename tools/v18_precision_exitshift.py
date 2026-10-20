#!/usr/bin/env python3
"""v18_precision_exitshift - CAUSAL exit-family shift of a switch: the same published set WITHOUT vs WITH one key flipped.

Not co-occurrence: per sym_side, ONE real engine eval of the published set (base) and ONE of base + switch=alt (cand), both
`evaluate_prepared_sanitized(prep, set, 30, include_ledger=True)` on the same prepared NPZ, trades matched by ENTRY BAR exactly
as tools/v15_trade_autopsy.attribute does (same engine + same data -> identical entry bars). Per matched trade the exit family
of base vs cand gives the shift A -> B; base entries absent in cand are FILTERED; cand entries absent in base are ADDED.

    plan       (Mac)  python3 tools/v18_precision_exitshift.py plan --top 10 --syms 8 --out data/learn/precision/exitshift_jobs.json
    run        (S2)   nice -n 10 python3 v18_precision_exitshift.py run --root ~/binance-sandbox --jobs exitshift_jobs.json
                         --sets ~/v18_learn/replay_sets.jsonl.gz --out exitshift_raw.json --workers 2
    attribute  (Mac)  python3 tools/v18_precision_exitshift.py attribute --raw exitshift_raw.json --out data/learn/precision/exitshift_<stamp>.json

Writes only its --out files. Never touches sheets, progress JSONs, templates, configs, gate flags.
"""
import argparse
import collections
import gzip
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
EXIT_TABS = ("EXIT_STRUCTURAL", "EXIT_VELOCITY", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER")
EPS = 1e-9
ALWAYS = ("EXIT_VELOCITY_WT_ENABLED", "EXIT_VELOCITY_WT_MIN_TFS")


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------------------------------------------ plan (Mac)

def plan(a):
    sys.path.insert(0, str(ROOT / "tools"))
    from v18_learn_common import template_index
    sets = {}
    with gzip.open(a.sets, "rt") as fh:
        for line in fh:
            d = json.loads(line)
            sets[d["sym_side"]] = d
    jobs = {}
    for cs in CAT_SIDES:
        tidx = template_index(cs)
        prof = json.loads((ROOT / "data" / "learn" / f"switch_profile_{cs}.json").read_text())
        exit_sw = []
        for sw, s in prof["switches"].items():
            tabs = s.get("tabs", [])
            if tabs and all(t in EXIT_TABS for t in tabs) and not sw.endswith("_FILTER_TF"):
                exit_sw.append((s.get("n_evals", 0) + (10 ** 9 if sw in ALWAYS else 0), sw))
        exit_sw.sort(reverse=True)
        ledg = ROOT / "data" / "learn" / "trade_ledgers" / cs
        cands_syms = []
        for f in ledg.glob("*.jsonl"):
            n = sum(1 for _ in open(f, errors="replace")) - 1
            if f.stem in sets and n >= 20:
                cands_syms.append((n, f.stem))
        cands_syms.sort(reverse=True)
        syms = [s for _n, s in cands_syms[: a.syms]]
        rows = prof["rows"]
        picked = []
        for _n, sw in exit_sw:
            if len(picked) >= a.top:
                break
            cl = tidx["switches"].get(sw) or []
            if len(cl) < 2:
                continue
            ranked = sorted(cl, key=lambda c: -(rows.get(f"{sw}={c}", {}).get("n_evals") or 0))
            picked.append({"switch": sw, "cands": cl, "ranked": ranked, "bold": tidx["defaults"].get(sw)})
        jobs[cs] = {"switches": picked, "syms": syms}
        print(cs, [p["switch"] for p in picked], syms)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"at": utc(), "jobs": jobs, "rule": "exit-tab-only switches (not *_FILTER_TF) by corpus eval volume, EXIT_VELOCITY_WT_ENABLED/_MIN_TFS always; sym_sides = replayed ledgers with most trades; alt = the most-evaluated template cand != the set's value"}, indent=1))
    return 0


# ------------------------------------------------------------------------------------------------ run (S2)

def _compact(rows):
    return [[r["bar_entry"], r["bar_exit"], r.get("pnl_pp"), r.get("exit_family"), r.get("entry_family")] for r in rows]


def run_one(job):
    rec, switches, root = job
    sys.path.insert(0, root)
    from tools.v18_ledger_replay import ledger_rows, wait_for_load
    from tools.opt import v12_pilot as VP
    from tools import switch_types as ST
    venue = "tradier" if rec["cat_side"].startswith("STOCKS") else "crypto"
    wait_for_load()
    t0 = time.time()
    prep = VP.prepare_batch(rec["sym_side"], 30)
    if prep is None or (isinstance(prep, dict) and prep.get("_stale")):
        return rec["sym_side"], {"skip": "NPZ_STALE_OR_MISSING"}
    npz = prep.get("npz_prepared") or {}
    is_long = bool(prep.get("is_long"))
    base = VP.evaluate_prepared_sanitized(prep, dict(rec["set"]), 30, include_ledger=True)
    brows, _ = ledger_rows(base, npz, is_long, "base")
    out = {"base": {"gain": base.get("gain_pct"), "trades": base.get("trades"), "valid": bool(base.get("valid")), "rows": _compact(brows)}, "cands": []}
    for s in switches:
        sw = s["switch"]
        cur = rec["set"].get(sw)
        alt = None
        for c in s["ranked"]:
            ok, cv, _why = ST.coerce(sw, c, venue)
            if ok and cv != cur:
                alt = (c, cv)
                break
        if alt is None:
            out["cands"].append({"switch": sw, "skip": "NO_ALT"})
            continue
        ov = dict(rec["set"])
        ov[sw] = alt[1]
        wait_for_load()
        res = VP.evaluate_prepared_sanitized(prep, ov, 30, include_ledger=True)
        crows, _ = ledger_rows(res, npz, is_long, "cand")
        out["cands"].append({"switch": sw, "from": None if cur is None else str(cur), "to": alt[0], "gain": res.get("gain_pct"), "trades": res.get("trades"),
                             "valid": bool(res.get("valid")), "rows": _compact(crows)})
    out["secs"] = round(time.time() - t0, 2)
    return rec["sym_side"], out


def run(a):
    root = str(Path(os.path.expanduser(a.root)).resolve())
    os.chdir(root)
    sys.path.insert(0, root)
    jobs_doc = json.loads(Path(os.path.expanduser(a.jobs)).read_text())
    sets = {}
    with gzip.open(os.path.expanduser(a.sets), "rt") as fh:
        for line in fh:
            d = json.loads(line)
            sets[d["sym_side"]] = d
    todo = []
    for cs, j in jobs_doc["jobs"].items():
        for ss in j["syms"]:
            if ss in sets:
                todo.append((sets[ss], j["switches"], root))
    print(f"[exitshift] {len(todo)} sym_sides {utc()}", flush=True)
    results = {}
    if a.workers > 1:
        import multiprocessing as mp
        with mp.get_context("fork").Pool(a.workers, maxtasksperchild=4) as pool:
            for ss, res in pool.imap_unordered(run_one, todo):
                results[ss] = res
                print(f"[exitshift] {ss} {res.get('skip') or len(res.get('cands', []))} {res.get('secs')}s", flush=True)
    else:
        for t in todo:
            ss, res = run_one(t)
            results[ss] = res
            print(f"[exitshift] {ss} {res.get('skip') or len(res.get('cands', []))} {res.get('secs')}s", flush=True)
    meta = {"at": utc(), "host": os.uname().nodename, "fn": "tools.opt.v12_pilot.evaluate_prepared_sanitized(include_ledger=True)", "window_days": 30}
    Path(os.path.expanduser(a.out)).write_text(json.dumps({"meta": meta, "jobs": jobs_doc, "results": results}))
    return 0


# ------------------------------------------------------------------------------------------------ attribute (Mac)

def cat_side_of(ss):
    sym, _, side = ss.rpartition("_")
    q = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD")
    return ("CRYPTO" if sym.upper().endswith(q) else "STOCKS") + "_" + side.upper()


def shift_one(base_rows, cand_rows):
    """Trade-matched by entry bar (v15_trade_autopsy.attribute semantics). -> transitions Counter, loss/gain deltas."""
    cb = {r[0]: r for r in cand_rows}
    bb = {r[0]: r for r in base_rows}
    tr = collections.Counter()
    tr_pnl = collections.defaultdict(float)
    for be, r in bb.items():
        c = cb.get(be)
        fa = r[3] or "UNKNOWN"
        if c is None:
            key = (fa, "FILTERED")
            tr[key] += 1
            tr_pnl[key] += -(r[2] or 0.0)
            continue
        fb = c[3] or "UNKNOWN"
        if fb != fa or c[1] != r[1] or abs((c[2] or 0.0) - (r[2] or 0.0)) > EPS:
            key = (fa, fb)
            tr[key] += 1
            tr_pnl[key] += (c[2] or 0.0) - (r[2] or 0.0)
    for be, c in cb.items():
        if be not in bb:
            key = ("ADDED", c[3] or "UNKNOWN")
            tr[key] += 1
            tr_pnl[key] += c[2] or 0.0
    loss_b = sum(r[2] for r in base_rows if (r[2] or 0.0) < 0)
    loss_c = sum(r[2] for r in cand_rows if (r[2] or 0.0) < 0)
    return tr, tr_pnl, loss_b, loss_c


def attribute(a):
    raw = json.loads(Path(a.raw).read_text())
    agg = {}
    for ss, res in raw["results"].items():
        if res.get("skip"):
            continue
        cs = cat_side_of(ss)
        base = res["base"]["rows"]
        for c in res["cands"]:
            if c.get("skip"):
                continue
            k = (cs, c["switch"])
            e = agg.setdefault(k, {"cat_side": cs, "switch": c["switch"], "n_syms": 0, "directions": collections.Counter(), "base_exits": 0,
                                   "moved": 0, "transitions": collections.Counter(), "transition_pnl": collections.defaultdict(float),
                                   "d_loss_pp": 0.0, "d_gain_pp": 0.0, "n_gain_pos": 0, "n_gain_neg": 0, "per_sym": []})
            tr, trp, lb, lc = shift_one(base, c["rows"])
            e["n_syms"] += 1
            e["directions"][f"{c['from']}->{c['to']}"] += 1
            e["base_exits"] += len(base)
            e["moved"] += sum(v for (fa, fb), v in tr.items() if fa != "ADDED")
            for kk, v in tr.items():
                e["transitions"][kk] += v
                e["transition_pnl"][kk] += trp[kk]
            dg = (c["gain"] or 0.0) - (res["base"]["gain"] or 0.0)
            e["d_gain_pp"] += dg
            e["n_gain_pos"] += 1 if dg > EPS else 0
            e["n_gain_neg"] += 1 if dg < -EPS else 0
            e["d_loss_pp"] += lc - lb
            e["per_sym"].append({"sym_side": ss, "from": c["from"], "to": c["to"], "d_gain": round(dg, 4), "d_loss_pp": round(lc - lb, 4),
                                 "base_trades": len(base), "cand_trades": len(c["rows"]), "valid": c["valid"]})
    out = {}
    for (cs, sw), e in agg.items():
        tops = sorted(e["transitions"].items(), key=lambda kv: -kv[1])[:8]
        cross = sorted(((k, v) for k, v in e["transitions"].items() if k[0] != k[1] and k[0] != "ADDED" and k[1] != "FILTERED"), key=lambda kv: -kv[1])[:6]
        out.setdefault(cs, {})[sw] = {
            "n_syms": e["n_syms"], "directions": dict(e["directions"]), "base_exits": e["base_exits"], "moved": e["moved"],
            "moved_share": round(e["moved"] / e["base_exits"], 4) if e["base_exits"] else None,
            "top_transitions": [{"from": fa, "to": fb, "n": n, "share_of_base_exits": round(n / e["base_exits"], 4) if (e["base_exits"] and fa != "ADDED") else None,
                                 "d_pnl_pp": round(e["transition_pnl"][(fa, fb)], 3)} for (fa, fb), n in tops],
            "family_changes": [{"from": fa, "to": fb, "n": n, "share_of_base_exits": round(n / e["base_exits"], 4) if e["base_exits"] else None,
                                "d_pnl_pp": round(e["transition_pnl"][(fa, fb)], 3)} for (fa, fb), n in cross],
            "filtered": sum(v for k, v in e["transitions"].items() if k[1] == "FILTERED"), "added": sum(v for k, v in e["transitions"].items() if k[0] == "ADDED"),
            "sum_d_loss_pp": round(e["d_loss_pp"], 3), "sum_d_gain_pp": round(e["d_gain_pp"], 3), "n_gain_pos": e["n_gain_pos"], "n_gain_neg": e["n_gain_neg"],
            "per_sym": e["per_sym"], "basis": "CAUSAL: same set +/- one key, same NPZ, trades matched by entry bar"}
    doc = {"meta": dict(raw.get("meta") or {}, attributed_at=utc(), rule="d_loss_pp = Σ losing pnl_pp (cand) - Σ losing pnl_pp (base); > 0 = fewer losses; d_gain_pp = cand gain - base gain"),
           "per_cat_side": out}
    Path(a.out).write_text(json.dumps(doc, indent=1))
    for cs, d in out.items():
        for sw, e in sorted(d.items(), key=lambda kv: kv[1]["sum_d_gain_pp"]):
            t = e["family_changes"][0] if e["family_changes"] else {}
            print(f"{cs} {sw} syms={e['n_syms']} moved={e['moved_share']} filtered={e['filtered']} added={e['added']} top_change={t.get('from')}->{t.get('to')} n={t.get('n')} dpnl={t.get('d_pnl_pp')} dloss={e['sum_d_loss_pp']} dgain={e['sum_d_gain_pp']} +{e['n_gain_pos']}/-{e['n_gain_neg']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--syms", type=int, default=8)
    p.add_argument("--sets", default=str(ROOT / "data" / "learn" / "replay_sets.jsonl.gz"))
    p.add_argument("--out", default=str(ROOT / "data" / "learn" / "precision" / "exitshift_jobs.json"))
    r = sub.add_parser("run")
    r.add_argument("--root", required=True)
    r.add_argument("--jobs", required=True)
    r.add_argument("--sets", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--workers", type=int, default=2)
    t = sub.add_parser("attribute")
    t.add_argument("--raw", required=True)
    t.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    return {"plan": plan, "run": run, "attribute": attribute}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
