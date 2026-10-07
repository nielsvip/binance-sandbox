#!/usr/bin/env python3
"""v15_row365_filters — filters on top of EXISTING NON-ZERO rows, evaluated on the long window (Agent R365, USER ORDER 2026-10-01).

Why: the sweeps only hold whole-config totals on 365D, so nothing says per ROW which orange filters stay, which switches are useless and which
cells should be yellow. This tool answers that cheaply: it touches ONLY rows whose most recent valid recorded delta (any finished progress JSON of
data/avg2_sources.json incl. contaminated dirs, plus --extra-glob) is non-zero (positive AND negative: a negative row may be rescued by a filter).
Rows whose recorded delta is None / exactly 0 / invalid are NOT touched (they come in another round after repair).

Per sym_side (one sym_side per worker process, vector engine, prepare_batch ONCE, all arrays in RAM, no per-cell file I/O):
  anchor : the row alone ({switch: cand}) on the window vs the sym_side baseline (current defaults recomputed on the window)  -> 'delta'
           (the existing 30D delta is kept next to it as 'delta30' -> 30D vs 365D per row)
  cells  : switch=cand + FILTER=opt for every filter option applicable to the row's tab/switch (data/opportune_filter_map.json + tab-level filters),
           vs baseline ('yellows') and vs the row alone ('yellow_vs_switch'); fingerprint-equal cells are 'noop_yellows'.
  Skipped (not calculated, never 0): filters listed unwired in data/vec_unwired.json, keys not in the config, options that cannot be coerced.
Window: 365D for crypto AND stocks (stock NPZs hold ~730 days; the engine's 365D prepare is used; a sym_side whose real NPZ span is < 330 d is recorded unverifiable); --window 30 runs the 30D pass.
Efficiency: task list built and counted first; a calibration measures seconds per eval; the per-sym_side task budget is sized so that >= --min-sym
sym_sides per cat_side finish within --budget-hours on --procs processes (lowest |delta30| rows are dropped first when a sym_side exceeds its share).
Resumable: checkpoints every --ckpt-secs and at the end (atomic); a rerun skips cells already stored. Output = AVG2 progress schema with window tag:
  <out>/<SYM_SIDE>_v14_progress.json  {"initial_baseline_gain", "done": {"TAB!row:SWITCH=cand": {delta, delta30, yellows, yellow_vs_switch, noop_yellows,
  yellow_reasons, naked_binding, trades, source:'row365', window:'365D'|'30D', engine_md5, htf_align}}, "window_days", "source", ...}
usage: v15_row365_filters.py --cat CRYPTO_LONG [--cat CRYPTO_SHORT ...] [--min-sym 40] [--budget-hours 2.5] [--procs 15] [--extra-glob 'GLOB'] [--plan-only]
       v15_row365_filters.py --syms COTIUSDT_LONG --window 30 --max-rows 10   (test mode vs the NEWX numbers)
"""
import argparse, datetime, glob, hashlib, json, multiprocessing as mp, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import avg2_sources as AS  # noqa: E402
import v15_newx_scan as NX  # noqa: E402  (load_template, coerce, atomic_write, fp, mem_avail_gb helpers; its main is guarded)

EPS = 1e-9
SRC = "row365"


def usable(res):
    """trades > 0 and a real gain; SOFT invalid (TIM > 80 %, DD > 30 %, trades < floor) still carries honest numbers: kept and flagged (exploratory
    row evidence, nothing is promoted from it); hard invalid (prepare failed / 0 trades / crash) is never used."""
    if int(res.get("trades") or 0) <= 0 or res.get("gain_pct") is None:
        return False
    if res.get("valid"):
        return True
    return str(res.get("invalid_reason") or "").startswith(("TIM ", "DD ", "trades "))


def npz_span_days(ss):
    """real history of the sym_side's NPZ in days (timestamps key only: cheap); 0 if unreadable"""
    try:
        import numpy as np
        z = np.load(ROOT / "backtest_v8" / "indicators" / f"{ss.rsplit('_', 1)[0]}.npz", allow_pickle=True)
        ts = z["timestamps"]
        return (float(ts[-1]) - float(ts[0])) / (1000.0 if float(ts[-1]) > 1e11 else 1.0) / 86400.0
    except Exception:
        return 0.0


def split_key(k):
    """'TAB!row:SWITCH=cand' -> (tab, switch, cand) or None"""
    try:
        tab, rest = k.split("!", 1)
        _, rest = rest.split(":", 1)
        sw, cand = rest.split("=", 1)
        return tab, sw, cand
    except Exception:
        return None


def evidence_by_symside(extra_globs, cat_filter):
    """{sym_side: {(tab,switch,cand): (delta, mtime, path)}} most recent VALID delta per row over all evidence files."""
    cfg = AS.load(str(ROOT / "data" / "avg2_sources.json"))
    files = list(AS.list_files(cfg))
    for g in extra_globs:
        files += [(p, "progress") for p in glob.glob(g)]
    out = {}
    for path, kind in files:
        ss = os.path.basename(path)[: -len("_v14_progress.json")] if path.endswith("_v14_progress.json") else None
        if not ss or not any(ss.endswith(c.split("_")[-1]) and True for c in cat_filter):
            continue
        try:
            mt = os.path.getmtime(path)
            d = json.load(open(path)).get("done", {})
        except Exception:
            continue
        cur = out.setdefault(ss, {})
        for k, ent in d.items():
            if not isinstance(ent, dict) or ent.get("delta_invalid"):
                continue
            dv = ent.get("delta")
            if not isinstance(dv, (int, float)):
                continue
            sk = split_key(k)
            if sk is None:
                continue
            if sk not in cur or mt > cur[sk][1]:
                cur[sk] = (float(dv), mt, path)
    return out


def build_plan(cat, ev, rows, fhdr, opp, tl, unwired, qf_names):
    """per sym_side: ordered list of (rowinfo, delta30, [(hdr, fbase, fopt)]) for eligible rows"""
    tmpl = {}
    for r in rows:
        if r["is_default"]:
            continue
        tmpl.setdefault((r["tab"], r["switch"], r["cand"]), r)
    plan = {}
    for ss, rowsd in ev.items():
        items = []
        for sk, (dv, mt, path) in rowsd.items():
            if abs(dv) <= EPS or sk not in tmpl:
                continue
            r = tmpl[sk]
            tab, sw, cand = sk
            if sw not in qf_names:
                continue
            bases = set((opp.get(tab) or {}).get(sw, [])) | set((tl.get(tab) or {}).keys())
            cells = []
            for hdr in fhdr.get(tab, []):
                fb, _, fo = hdr.partition("=")
                if fb in bases and fb != sw and fb in qf_names and fb not in unwired:
                    cells.append((hdr, fb, fo))
            items.append((r, dv, cells))
        items.sort(key=lambda x: -abs(x[1]))
        plan[ss] = items
    return plan


def eval_symside(args):
    (ss, cat, items, window, out_dir, tmpl_info, ckpt_secs, min_mem, abort_mem, eng_md5) = args
    os.nice(5)
    os.environ.setdefault("V12_NPZ_CACHE", "2")
    import dataclasses
    import v12_quick_engine as V
    from tools.opt import v12_pilot as P
    NX.mem_guard(min_mem, abort_mem)
    t0 = time.time()
    outp = Path(out_dir) / f"{ss}_v14_progress.json"
    prev = {}
    if outp.exists():
        try:
            prev = json.load(open(outp))
        except Exception:
            prev = {}
    done = prev.get("done", {}) if prev.get("window_days") == window else {}
    prep = P.prepare_batch(ss, window)
    if prep is None:
        NX.atomic_write(outp, {"source": SRC, "cat_side": cat, "window_days": window, "unverifiable": True, "reason": "no valid NPZ / span < window", "done": {}})
        return ss, 0, 0, 0.0, "unverifiable"
    if window >= 365:
        try:
            ts = prep["npz_prepared"]["timestamps"]
            span = (float(ts[-1]) - float(ts[0])) / (1000.0 if float(ts[-1]) > 1e11 else 1.0) / 86400.0
        except Exception:
            span = 0.0
        if span < 330:
            NX.atomic_write(outp, {"source": SRC, "cat_side": cat, "window_days": window, "unverifiable": True, "reason": f"365D slice covers only {span:.0f}d", "done": {}})
            return ss, 0, 0, 0.0, "unverifiable"
    base = P.evaluate_prepared_sanitized(prep, {}, window)
    if not usable(base):
        NX.atomic_write(outp, {"source": SRC, "cat_side": cat, "window_days": window, "invalid_baseline": True, "reason": str(base.get("invalid_reason"))[:120], "done": {}})
        return ss, 0, 0, 0.0, "invalid_baseline"
    base_soft = None if base.get("valid") else str(base.get("invalid_reason"))[:80]
    bgain, bfp = float(base["gain_pct"]), NX.fp(base)
    cfgd = prep.get("base_cfg_dict", {})
    qf = {f.name for f in dataclasses.fields(V.QuickConfig)}
    cache = {}

    def ev(ov):
        key = tuple(sorted(ov.items()))
        if key not in cache:
            cache[key] = P.evaluate_prepared_sanitized(prep, ov, window)
        return cache[key]

    prog = {"initial_baseline_gain": bgain, "baseline_trades": int(base.get("trades") or 0), "bh": prep.get("bh"), "engine_md5": eng_md5, "htf_align": base.get("htf_align"),
            "baseline_soft_invalid": base_soft, "source": SRC, "cat_side": cat, "window_days": window, "window": f"{window}D", "at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z", "done": done}
    last_ck, n_cells, n_rows = time.time(), 0, 0
    for (r, d30, cells) in items:
        tab, sw, cand = r["tab"], r["switch"], r["cand"]
        key = f"{tab}!{r['row']}:{sw}={cand}"
        if key in done and done[key].get("complete"):
            continue
        ref = cfgd.get(sw, None)
        try:
            val = NX.coerce(cand, ref)
        except Exception:
            done[key] = {"source": SRC, "reason": "NO_CANDIDATE", "complete": True}
            continue
        res = ev({sw: val})
        ent = {"source": SRC, "window": f"{window}D", "engine_md5": eng_md5, "ref_fp": bfp, "delta30": round(d30, 6), "complete": False}
        ok = usable(res)
        if not ok:
            ent["delta_invalid"] = True
            ent["reason"] = str(res.get("invalid_reason") or "invalid")[:120]
            ent["complete"] = True
            done[key] = ent
            continue
        ent["delta"] = round(float(res["gain_pct"]) - bgain, 6)
        if not res.get("valid"):
            ent["soft_invalid"] = str(res.get("invalid_reason"))[:60]
        ent["naked_binding"] = NX.fp(res) != bfp
        ent["trades"] = int(res.get("trades") or 0)
        ent["dep_forced"] = res.get("dep_forced")
        yel, noop, yr, yvs = {}, [], {}, {}
        for (hdr, fb, fo) in cells:
            try:
                v2 = NX.coerce(fo, cfgd.get(fb, None))
            except Exception:
                continue
            r2 = ev({sw: val, fb: v2})
            if usable(r2):
                if NX.fp(r2) == NX.fp(res):
                    noop.append(hdr)
                else:
                    yel[hdr] = round(float(r2["gain_pct"]) - bgain, 6)
                    yvs[hdr] = round(float(r2["gain_pct"]) - float(res["gain_pct"]), 6)
                n_cells += 1
            else:
                yr[hdr] = str(r2.get("invalid_reason") or "invalid")[:80]
        ent.update(yellows=yel, noop_yellows=noop, yellow_reasons=yr, yellow_vs_switch=yvs, complete=True)
        done[key] = ent
        n_rows += 1
        if time.time() - last_ck > ckpt_secs:
            NX.atomic_write(outp, prog)
            last_ck = time.time()
    prog["at"] = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    prog["finished"] = True
    NX.atomic_write(outp, prog)
    return ss, n_rows, n_cells, time.time() - t0, "ok"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cat", action="append", choices=NX.CATS, default=None)
    ap.add_argument("--window", type=int, default=0, help="0 = crypto 365 / stocks 30")
    ap.add_argument("--min-sym", type=int, default=40)
    ap.add_argument("--max-sym", type=int, default=0)
    ap.add_argument("--budget-hours", type=float, default=2.5)
    ap.add_argument("--procs", type=int, default=15)
    ap.add_argument("--eval-secs", type=float, default=0.0, help="seconds per eval (0 = calibrate 60 s on the first sym_side)")
    ap.add_argument("--max-rows", type=int, default=0, help="test mode: cap rows per sym_side")
    ap.add_argument("--syms", default="")
    ap.add_argument("--shard", default="0/1", help="i/n: this host takes sym_sides whose md5(name) %% n == i of the selected list (hosts split the work, nothing evaluated twice)")
    ap.add_argument("--extra-glob", action="append", default=[])
    ap.add_argument("--template-dir", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"))
    ap.add_argument("--out-dir", default=os.path.expanduser("~/v15_row365_20261001/progress"))
    ap.add_argument("--ckpt-secs", type=int, default=300)
    ap.add_argument("--min-mem-gb", type=float, default=3.0)
    ap.add_argument("--abort-mem-gb", type=float, default=1.5)
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--job", default="", help="JSON file with the real argv list (keeps the process command line free of sym_side / cat names so pkill -f patterns on pilots never match these workers)")
    a0, _ = ap.parse_known_args()
    if a0.job:
        a = ap.parse_args(json.load(open(a0.job))["argv"])
    else:
        a = ap.parse_args()
    eng_md5 = hashlib.md5(open(ROOT / "v12_quick_engine.py", "rb").read()).hexdigest()[:8]
    unwired = set()
    try:
        vu = json.load(open(ROOT / "data" / "vec_unwired.json"))
        unwired = set(vu.get("filters", [])) | set(vu.get("switches", []))
    except Exception:
        pass
    import dataclasses
    import v12_quick_engine as V
    qf = {f.name for f in dataclasses.fields(V.QuickConfig)}
    opp_all = json.load(open(ROOT / "data" / "opportune_filter_map.json"))
    try:
        tl_all = json.load(open(ROOT / "data" / "wiring" / "tab_filters" / "tab_level_filters.json")).get("cats", {})
    except Exception:
        tl_all = {}
    ev_all = evidence_by_symside(a.extra_glob, a.cat)
    jobs, summary = [], {}
    for cat in a.cat:
        window = a.window or 365
        rows, fhdr = NX.load_template(cat, Path(a.template_dir))
        side = cat.split("_")[1]
        ev = {ss: v for ss, v in ev_all.items() if ss.endswith("_" + side) and (cat.startswith("CRYPTO") == AS.is_crypto(ss))}
        if a.syms:
            ev = {ss: v for ss, v in ev.items() if ss in a.syms.split(",")}
        plan = build_plan(cat, ev, rows, fhdr, opp_all.get(cat, {}), (tl_all.get(cat, {}) or {}), unwired, qf)
        plan = {ss: it for ss, it in plan.items() if it and (ROOT / "backtest_v8" / "indicators" / f"{ss.rsplit('_', 1)[0]}.npz").exists()}
        if window >= 365:
            n_before = len(plan)
            plan = {ss: it for ss, it in plan.items() if npz_span_days(ss) >= 330}
            print(f"[row365] {cat}: {len(plan)}/{n_before} sym_sides have >= 330 d of NPZ history (the others are unverifiable on 365D and skipped)", flush=True)
        order = sorted(plan, key=lambda s: -len(plan[s]))
        tasks = {ss: sum(1 + len(c) for _, _, c in plan[ss]) for ss in plan}
        summary[cat] = {"window": window, "sym_sides_with_evidence": len(plan), "rows_total": sum(len(v) for v in plan.values()), "tasks_total": sum(tasks.values())}
        print(f"[row365] {cat} window={window}D engine={eng_md5} sym_sides={len(plan)} eligible_rows={summary[cat]['rows_total']} tasks={summary[cat]['tasks_total']}", flush=True)
        n = a.max_sym or max(a.min_sym, 0)
        order = sorted(order, key=lambda x: (-len(plan[x]), x))
        sel = order[: max(n, 1)] if len(order) > n else order
        _si, _sn = [int(x) for x in a.shard.split("/")]
        if _sn > 1:
            sel = [x for x in sel if int(hashlib.md5(x.encode()).hexdigest(), 16) % _sn == _si]
        jobs.append((cat, window, sel, plan, tasks))
    es = a.eval_secs or 0.0
    if not es:
        es = 0.66 if any(c.startswith("CRYPTO") for c in a.cat) and not a.window else 0.07
        print(f"[row365] eval-secs default {es}s (no calibration in plan-only/first pass; the run logs real rates)", flush=True)
    budget_evals = a.budget_hours * 3600.0 * a.procs / es
    allj = []
    tot_sel = sum(len(s) for _, _, s, _, _ in jobs) or 1
    share = budget_evals / tot_sel
    for cat, window, sel, plan, tasks in jobs:
        for ss in sel:
            items = plan[ss]
            if a.max_rows:
                items = items[: a.max_rows]
            cum, keep = 0, []
            for it in items:
                c = 1 + len(it[2])
                if cum + c > share and keep:
                    break
                keep.append(it)
                cum += c
            allj.append((ss, cat, keep, window, a.out_dir, None, a.ckpt_secs, a.min_mem_gb, a.abort_mem_gb, eng_md5))
            print(f"[row365]   {ss}: rows {len(keep)}/{len(items)} tasks {cum}/{tasks[ss]} est {cum*es/60:.0f} cpu-min", flush=True)
    if a.plan_only or not allj:
        print(f"[row365] plan only: {len(allj)} sym_sides, budget {a.budget_hours} h x {a.procs} procs at {es}s/eval = {budget_evals:.0f} evals, share/sym {share:.0f}")
        return
    Path(a.out_dir).mkdir(parents=True, exist_ok=True)
    def _done_rows(j):
        """rows already complete in this sym_side's output (resume): finish the nearly-done sym_sides first"""
        try:
            d = json.load(open(Path(j[4]) / f"{j[0]}_v14_progress.json")).get("done", {})
            return sum(1 for v in d.values() if v.get("complete"))
        except Exception:
            return 0
    allj.sort(key=lambda j: (-_done_rows(j), sum(1 + len(c) for _, _, c in j[2])))
    ctx = mp.get_context("fork")
    t0 = time.time()
    with ctx.Pool(processes=a.procs, maxtasksperchild=1) as pool:
        for k, (ss, n_rows, n_cells, secs, st) in enumerate(pool.imap_unordered(eval_symside, allj), 1):
            print(f"[row365] {k}/{len(allj)} {ss}: {st} rows={n_rows} cells={n_cells} wall={secs:.0f}s total={(time.time()-t0)/60:.0f}min", flush=True)
    print(f"[row365] done in {(time.time()-t0)/60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
