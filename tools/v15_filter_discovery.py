#!/usr/bin/env python3
"""v15_filter_discovery — which FILTER=opt really DOES something? (Agent Y, USER REDIRECT 2026-10-01; supersedes the name-token candidate idea.)

Every FILTER=opt column of the cat_side TEMPLATE (read-only; all options) is run against the EXISTING results of each sym_side (its final set from the
newest fixed-pilot progress JSON) on the SAME engine path as the pilot/audit: tools.opt.v12_pilot.evaluate_prepared_sanitized(prepared, overrides, window),
one prepare per sym_side, forked workers share it (copy-on-write). A cell is marked wherever the filter changes the LEDGER (behavior_fingerprint differs),
never by name. NEVER recalculates sheets: only filter evaluations on top of rows that already exist.

Stage A  baseline + every wired filter option ALONE on the sym_side's final set (~ #headers evals/sym_side, ~200). inert in A (fingerprint == baseline) =
         the filter does nothing for that sym_side. Filters listed in data/vec_unwired.json are recorded NOT_WIRED_VEC (not calculated, never 0).
Stage B  per UNIQUE switch=cand row (deduped across tabs): naked = final set + switch override; a row is BINDING iff its fingerprint differs from the
         baseline fingerprint. Binding rows get their own filter columns evaluated (every non-inert wired filter option on top of the naked row, delta vs
         the row's own naked result). Non-binding rows: row-specific effect == Stage A's baseline effect -> recorded as INHERITED_FROM_BASELINE (not a new
         calculation, not a copy-lie; kept distinguishable in the evidence). A (switch,filter) pair never evaluated stays UNKNOWN (never 0).
Window   --window 365 (default; needs >=330d NPZ span, else the sym_side is skipped+recorded) or 30.  Resumable: every eval is appended to a jsonl, reruns skip.
Output   <dir>/results_<cat>__<ss>.jsonl, plan_<cat>__<ss>.json, then --summarize -> <dir>/evidence_<cat>.json (+ summary.json).

  python tools/v15_filter_discovery.py --dry-run                         counts + time estimate per cat_side (no NPZ needed)
  python tools/v15_filter_discovery.py --cat-side STOCKS_LONG --stage A --window 365 --workers 4 --nice 0 --dir DIR
  python tools/v15_filter_discovery.py --cat-side STOCKS_LONG --stage B --window 365 --symsides AAPL_LONG,CRM_LONG ...
  python tools/v15_filter_discovery.py --summarize --dir DIR
  --sanity: 2 sym_sides, Stage A only (use after an engine deploy: wired filters must now show effects)
"""
import argparse
import datetime
import glob
import hashlib
import json
import multiprocessing as mp
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import v15_yellow_365_audit as AU  # noqa: E402  (helpers only; its main is guarded)

CAT_SIDES = AU.CAT_SIDES
EPS = 1e-9
MIN_SPAN_DAYS = 330
_PREP = None
_WIN = 365
FIXED_ONLY = False  # --fixed-only: only progress files written by the fixed pilot
COST = {  # sec per eval (cpu) — measured: data/yellow_365_audit/measured_cost.json (365D) ; 30D engine 0.07 s cached (BIBLE §22)
    (365, "CRYPTO"): 0.66, (365, "STOCKS"): 0.05, (365, "STOCKS_LOADED"): 0.21, (30, "CRYPTO"): 0.07, (30, "STOCKS"): 0.07,
}


def atomic_write(path: Path, obj):
    AU.atomic_write(path, obj)


def ck_of(ov):
    return AU.ck_of(ov)


# ───────────────────────── template universe (read-only) ─────────────────────────
def template_universe(cs, P, d: Path):
    """(filter_headers [FILTER=opt...], switch_rows [(sw,cand)...] unique across tabs, tabs{(sw,cand)->[tab]})"""
    cf = d / f"universe_{cs}.json"
    tpl = Path(os.environ.get("FD_TEMPLATE_DIR") or (ROOT / "SPREADSHEETS")) / f"TEMPLATE_{cs}.xlsx"  # read-only; results stay keyed by SWITCH=cand + FILTER=opt
    if cf.exists() and cf.stat().st_mtime > tpl.stat().st_mtime:
        j = json.loads(cf.read_text())
        return j["headers"], [tuple(x) for x in j["rows"]], {tuple(k.split("\t")): v for k, v in j["tabs"].items()}
    import openpyxl
    wb = openpyxl.load_workbook(str(tpl), read_only=True)
    known = P.known_config_fields()
    headers, rows, tabs = {}, {}, {}
    for tab in AU.SWITCH_SHEETS:
        if tab not in wb.sheetnames:
            continue
        it = wb[tab].iter_rows(min_row=1, values_only=True)
        next(it, None)
        hdr = next(it, None) or ()
        pos = next((i for i, v in enumerate(hdr) if str(v or "").strip().upper() == "POS_SYM"), 13)
        for i in range(pos + 1, len(hdr)):
            h = hdr[i]
            if isinstance(h, str) and "=" in h and h.split("=", 1)[0].strip() in known and not h.strip().endswith("_ALT"):
                headers[h.strip()] = 1
        for r in it:
            if not r or r[0] in (None, "") or len(r) < 2 or r[1] in (None, "") or str(r[1]).strip().endswith("_ALT"):
                continue
            k = (str(r[0]).strip(), str(r[1]).strip())
            rows[k] = 1
            tabs.setdefault(k, [])
            if tab not in tabs[k]:
                tabs[k].append(tab)
    out = (sorted(headers), sorted(rows), tabs)
    atomic_write(cf, {"headers": out[0], "rows": [list(x) for x in out[1]], "tabs": {"\t".join(k): v for k, v in tabs.items()}})
    return out


def census_status():
    """filter name -> REAL | STUB_NO_CONSUMER | NOT_WIRED_VEC (data/filter_census/m1: 'calculates' = REAL, 'ZERO' on a yellow FILTER_TF column = no consumer in live or vector (stub farm), vec_unwired.json = NOT_WIRED_VEC)."""
    import csv
    out = {}
    p = ROOT / "data" / "filter_census" / "m1" / "census.csv"
    try:
        for r in csv.DictReader(open(p)):
            if r["set"] != "yellow_FILTER_TF_column":
                continue
            st = "REAL" if r["status"].strip().lower().startswith("calculates") else "STUB_NO_CONSUMER"
            if out.get(r["key"]) != "REAL":  # REAL on either venue wins
                out[r["key"]] = st
    except Exception:
        pass
    return out


def wiring_of(filter_name, census, unw):
    if filter_name in unw:
        return "NOT_WIRED_VEC"
    return census.get(filter_name, "UNCENSUSED")


def unwired_filters():
    try:
        j = json.loads((ROOT / "data" / "vec_unwired.json").read_text())
        return set(j.get("filters", [])) | set(j.get("filters_manual", []))
    except Exception:
        return set()


# ───────────────────────── sym_side selection ─────────────────────────
def start_from_xlsx(path, P, defaults):
    """final set of an old finished sheet = the promoted overrides of column C (later tabs/rows supersede earlier); missing keys come from the bold defaults (sanitize)."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    start = {}
    for tab in AU.SWITCH_SHEETS:
        if tab not in wb.sheetnames:
            continue
        it = wb[tab].iter_rows(min_row=1, values_only=True)
        next(it, None)
        hdr = next(it, None) or ()
        ci = next((i for i, v in enumerate(hdr) if str(v or "").strip().lower().startswith("override")), 2)
        for r in it:
            if not r or len(r) <= ci or not isinstance(r[ci], str):
                continue
            for part in r[ci].split(" + "):
                if "=" not in part:
                    continue
                k, v = part.split("=", 1)
                k = k.strip()
                if k:
                    start[k] = P._parse_opt_value(v.strip(), defaults.get(k))
    return start


def load_start(c, P, defaults):
    if c.get("kind") == "xlsx":
        return start_from_xlsx(c["path"], P, defaults)
    return json.load(open(c["path"])).get("cumulative_overrides") or {}


def candidates_from_manifest(cs, path):
    man = json.loads(Path(path).read_text())
    out = [dict(m, gain30=None) for m in man if m["cat"] == cs and os.path.exists(m["path"])]
    return sorted(out, key=lambda x: (0 if x.get("has365") else 1, x["symside"]))


def candidates(cs, P, globs):
    """newest FIXED-pilot (entries carry 'naked_binding') progress JSON per sym_side of this cat_side with a complete final set (>=50 keys)."""
    best = {}
    has365 = set()
    for p in glob.glob(os.path.expanduser("~/v15_run*/chain/**/*_365_cycle.json"), recursive=True):
        has365.add(os.path.basename(p)[: -len("_365_cycle.json")])
    for g in globs:
        for p in glob.glob(os.path.expanduser(g)):
            ss = os.path.basename(p)[: -len("_v14_progress.json")]
            if P.map_key_for_symside(ss) != cs:
                continue
            m = os.path.getmtime(p)
            if ss not in best or m > best[ss][0]:
                best[ss] = (m, p)
    out = []
    for ss, (m, p) in best.items():
        try:
            d = json.load(open(p))
        except Exception:
            continue
        ov = d.get("cumulative_overrides") or {}
        g = d.get("final_gain", d.get("cumulative_gain"))
        done = d.get("done") or {}
        fixed = any(isinstance(e, dict) and "naked_binding" in e for e in done.values())
        if len(ov) < 50 or g is None or (FIXED_ONLY and not fixed):
            continue  # a final set (>=50 keys) from ANY round is a valid existing result; legacy files only lose their (fake-zero) delta bookkeeping, not the set itself
        out.append({"symside": ss, "path": p, "gain30": float(g), "mtime": m, "fixed": fixed, "has365": ss in has365})
    # 365D sources first (sym_sides with a 365D chain verdict), then the rest; stable by name
    return sorted(out, key=lambda x: (0 if x["has365"] else 1, x["symside"]))


# ───────────────────────── plans ─────────────────────────
def parse_filter(P, defaults, hdr):
    f, o = hdr.split("=", 1)
    return f.strip(), P._parse_opt_value(o.strip(), defaults.get(f.strip()))


def plan_stage_a(P, start, defaults, headers, unwired):
    base = P.sanitize_overrides(dict(start), defaults)[0]
    cells, status = {}, {}
    for h in headers:
        f, v = parse_filter(P, defaults, h)
        if f in unwired:
            status[h] = "NOT_WIRED_VEC"
            continue
        if AU._same_val(start.get(f, defaults.get(f)), v):
            status[h] = "SAME_AS_BASELINE"
            continue
        ov = dict(start)
        ov[f] = v
        cells[h] = P.sanitize_overrides(ov, defaults)[0]
    return base, cells, status


def plan_stage_b(P, start, defaults, rows, headers_active):
    """naked{(sw,cand)->(ov|None if running)} and cells{(sw,cand,hdr)->ov} for the active headers (cells only built for BINDING rows later)."""
    naked = {}
    for (sw, cand) in rows:
        sw_ov = P._switch_overrides(sw, P._parse_opt_value(cand, defaults.get(sw)))
        running = all(AU._same_val(start.get(k, defaults.get(k)), v) for k, v in sw_ov.items())
        if running:
            naked[(sw, cand)] = (None, sw_ov)
            continue
        sv = dict(start)
        sv.update(sw_ov)
        naked[(sw, cand)] = (P.sanitize_overrides(sv, defaults)[0], sw_ov)
    return naked


def cell_ov(P, defaults, naked_ov, sw_ov, hdr):
    f, v = parse_filter(P, defaults, hdr)
    if f in sw_ov:
        return None  # the switch row itself sets this key: not a combination
    ov = dict(naked_ov)
    ov[f] = v
    return P.sanitize_overrides(ov, defaults)[0]


# ───────────────────────── evaluation ─────────────────────────
def _work(item):
    ck, ov = item
    from tools.opt.v12_pilot import evaluate_prepared_sanitized as eps
    t0 = time.time()
    try:
        r = eps(_PREP, ov, _WIN) or {}
        rec = {k: r.get(k) for k in ("gain_pct", "trades", "valid", "invalid_reason", "behavior_fingerprint", "dep_forced")}
    except Exception as e:  # recorded, never fabricated
        rec = {"gain_pct": None, "trades": None, "valid": False, "invalid_reason": f"ERR {e}"[:160], "behavior_fingerprint": None}
    rec["secs"] = round(time.time() - t0, 3)
    return ck, rec


def run_pool(pending, workers, nice, rf, res, tag, max_secs, t_start):
    ctx = mp.get_context("fork")
    n = 0
    with ctx.Pool(workers, initializer=AU._init_worker, initargs=(nice,)) as pool, open(rf, "a") as fh:
        for ck, rec in pool.imap_unordered(_work, pending, chunksize=2):
            fh.write(json.dumps({"ck": ck, "res": rec}, default=str) + "\n")
            fh.flush()
            res[ck] = rec
            n += 1
            if n % 200 == 0:
                print(f"[{tag}] {n}/{len(pending)} evals, wall {time.time() - t_start:.0f}s", flush=True)
            if max_secs and time.time() - t_start > max_secs:
                print(f"[{tag}] --max-secs reached (resumable)", flush=True)
                pool.terminate()
                return False
    return True


def fp(r):
    return (r or {}).get("behavior_fingerprint")


def run_symside(cs, c, a, P, d: Path, headers, rows, unwired, t_start):
    global _PREP, _WIN
    from tools.opt import evaluate_v12 as E
    ss = c["symside"]
    defaults = P.get_defaults_for_symside(ss)
    start = load_start(c, P, defaults)
    base, acells, astatus = plan_stage_a(P, start, defaults, headers, unwired)
    rf = d / f"results_{cs}__{ss}.jsonl"
    res = AU.load_results(rf)
    want = {ck_of(base): base}
    for h, ov in acells.items():
        want[ck_of(ov)] = ov
    pend = [(ck, ov) for ck, ov in want.items() if ck not in res]
    qf = d / "rerun_queue.jsonl"
    if astatus and not (d / f"plan_{cs}__{ss}.json").exists():
        with open(qf, "a") as qh:
            for h, st in astatus.items():
                if st == "NOT_WIRED_VEC":
                    qh.write(json.dumps({"symside": ss, "switch_cand": "*", "filter_opt": h, "reason": "NOT_WIRED_VEC (vec_unwired.json)", "engine_md5": engine_md5(), "scope": "stage A + every binding row (re-run when the filter is wired)"}) + "\n")
    plan = {"symside": ss, "window": a.window, "src": c["path"], "base": ck_of(base), "A": {h: ck_of(ov) for h, ov in acells.items()}, "A_status": astatus,
            "engine_md5": engine_md5(), "at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z"}
    pf = d / f"plan_{cs}__{ss}.json"
    old = json.loads(pf.read_text()) if pf.exists() else {}
    old.update(plan)
    atomic_write(pf, old)
    print(f"[{cs}] {ss}: Stage A evals {len(want)} pending {len(pend)} (not-wired {sum(1 for v in astatus.values() if v == 'NOT_WIRED_VEC')}, same {sum(1 for v in astatus.values() if v == 'SAME_AS_BASELINE')})", flush=True)
    prep = None
    if pend or a.stage in ("B", "F"):
        _WIN = a.window
        prep = E.prepare(ss, a.window)
        if prep is not None and a.window >= 300 and AU.span_days(prep) < MIN_SPAN_DAYS:
            print(f"[{cs}] {ss}: 365D span {AU.span_days(prep):.0f}d < {MIN_SPAN_DAYS}d — falling back to the 30D window", flush=True)
            _WIN = 30
            prep = E.prepare(ss, 30)
        if prep is None:
            print(f"[{cs}] {ss}: prepare failed — skipped", flush=True)
            old["skipped"] = "prepare"
            atomic_write(pf, old)
            return
        old["window"] = _WIN
        atomic_write(pf, old)
        _PREP = prep
    if pend and not run_pool(sorted(pend, key=lambda it: (0 if it[0] == ck_of(base) else 1, it[0])), a.workers, a.nice, rf, res, f"{cs} {ss} A", a.max_secs, t_start):
        return
    if a.stage not in ("B", "F"):
        return
    b = res.get(ck_of(base))
    if not b or b.get("gain_pct") is None or not fp(b):
        print(f"[{cs}] {ss}: baseline missing/no fingerprint — Stage B skipped", flush=True)
        return
    active = [h for h, ck in old["A"].items() if res.get(ck) and res[ck].get("valid") and fp(res[ck]) and fp(res[ck]) != fp(b)]
    if a.stage == "F":
        active = list(old["A"].keys())  # EVERY wired filter column (inert ones included): the user wants to know what goes where
    naked = plan_stage_b(P, start, defaults, rows, active)
    nwant = {}
    for k, (ov, sw_ov) in naked.items():
        if ov is not None:
            nwant[ck_of(ov)] = ov
    pend = [(ck, ov) for ck, ov in nwant.items() if ck not in res]
    old["naked"] = {f"{k[0]}={k[1]}": (ck_of(v[0]) if v[0] is not None else "RUNNING") for k, v in naked.items()}
    old["active_filters"] = active
    atomic_write(pf, old)
    print(f"[{cs}] {ss}: Stage B naked evals {len(nwant)} pending {len(pend)}; active filters {len(active)}", flush=True)
    if pend and not run_pool(sorted(pend, key=lambda it: it[0]), a.workers, a.nice, rf, res, f"{cs} {ss} B-naked", a.max_secs, t_start):
        return
    bind = []
    for k, (ov, sw_ov) in naked.items():
        r = res.get(ck_of(ov)) if ov is not None else None
        if r and r.get("valid") and fp(r) and fp(r) != fp(b):
            bind.append(k)
    if a.stage == "F":  # full grid: every row (running rows use the baseline as their naked set; their cells equal Stage A but are recorded under the row key)
        bind = list(naked.keys())
    old["binding_rows"] = [f"{k[0]}={k[1]}" for k in bind]
    cells, cwant = {}, {}
    for k in bind:
        ov, sw_ov = naked[k]
        if ov is None:
            if a.stage != "F":
                continue
            ov = base
        for h in active:
            co = cell_ov(P, defaults, ov, sw_ov, h)
            if co is None:
                continue
            cells[f"{k[0]}={k[1]}\t{h}"] = ck_of(co)
            cwant[ck_of(co)] = co
    old["F" if a.stage == "F" else "B"] = cells
    old["F_done_rows"] = len(naked) if a.stage == "F" else old.get("F_done_rows")
    atomic_write(pf, old)
    pend = [(ck, ov) for ck, ov in cwant.items() if ck not in res]
    print(f"[{cs}] {ss}: binding rows {len(bind)}/{sum(1 for v in naked.values() if v[0] is not None)}; Stage B cell evals {len(cwant)} pending {len(pend)}", flush=True)
    if pend:
        run_pool(sorted(pend, key=lambda it: it[0]), a.workers, a.nice, rf, res, f"{cs} {ss} B", a.max_secs, t_start)
    _PREP = None


def engine_md5():
    try:
        return hashlib.md5((ROOT / "v12_quick_engine.py").read_bytes()).hexdigest()[:12]
    except Exception:
        return None


# ───────────────────────── summarize ─────────────────────────
def summarize(d: Path, min_n: int):
    census, unw_s = census_status(), unwired_filters()
    zero_real = {}
    out = {"at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z", "min_n": min_n, "cat_sides": {}}
    for cs in CAT_SIDES:
        plans = sorted(d.glob(f"plan_{cs}__*.json"))
        if not plans:
            continue
        flt, cellB, inh, rows_per_sym = {}, {}, {}, {}
        syms = []
        for pf in plans:
            pl = json.loads(pf.read_text())
            if pl.get("skipped"):
                continue
            ss = pl["symside"]
            res = AU.load_results(d / f"results_{cs}__{ss}.jsonl")
            b = res.get(pl["base"])
            if not b or b.get("gain_pct") is None:
                continue
            syms.append({"symside": ss, "base_gain": b["gain_pct"], "base_trades": b.get("trades"), "engine_md5": pl.get("engine_md5"), "window": pl.get("window")})
            A = {}
            for h, st in (pl.get("A_status") or {}).items():
                A[h] = {"status": st}
                flt.setdefault(h, {"per_sym": {}})["per_sym"][ss] = {"status": st}
            for h, ck in (pl.get("A") or {}).items():
                r = res.get(ck)
                if r is None:
                    continue
                e = {"valid": bool(r.get("valid")), "reason": r.get("invalid_reason")}
                if r.get("gain_pct") is not None and r.get("valid"):
                    e["delta_vs_base"] = round(r["gain_pct"] - b["gain_pct"], 6)
                    e["effect"] = bool(fp(r) and fp(b) and fp(r) != fp(b))
                A[h] = e
                flt.setdefault(h, {"per_sym": {}})["per_sym"][ss] = e
            binding = set(pl.get("binding_rows") or [])
            naked = pl.get("naked") or {}
            for h, e in A.items():  # INHERITED_FROM_BASELINE for non-binding rows: aggregate per (switch row, filter) lazily = per filter; rows list kept per sym
                pass
            if naked:
                inh_rows = [k for k, v in naked.items() if v == "RUNNING" or k not in binding]
                inh.setdefault(ss, {"non_binding_rows": len(inh_rows), "binding_rows": len(binding)})
                evaluated = []
                for k, v in naked.items():
                    if v == "RUNNING":
                        evaluated.append(k)
                    else:
                        rr = res.get(v)
                        if rr and rr.get("valid") and rr.get("gain_pct") is not None and fp(rr):
                            evaluated.append(k)
                rows_per_sym[ss] = {"evaluated": evaluated, "binding": sorted(binding)}
            for key, ck in list((pl.get("B") or {}).items()) + list((pl.get("F") or {}).items()):
                sw, hdr = key.split("\t")
                r = res.get(ck)
                _nv = (pl.get("naked") or {}).get(sw) or ""
                nk = res.get(pl["base"] if _nv == "RUNNING" else _nv)
                if r is None or not nk or nk.get("gain_pct") is None:
                    continue
                e = {"valid": bool(r.get("valid")), "reason": r.get("invalid_reason")}
                if r.get("gain_pct") is not None and r.get("valid"):
                    e["delta_vs_naked"] = round(r["gain_pct"] - nk["gain_pct"], 6)
                    e["effect"] = bool(fp(r) and fp(nk) and fp(r) != fp(nk))
                cellB.setdefault(key, {"per_sym": {}})["per_sym"][ss] = e
        for h, rec in flt.items():
            vals = [e for e in rec["per_sym"].values() if "delta_vs_base" in e]
            rec["n"] = len(vals)
            rec["effect_syms"] = sum(1 for e in vals if e.get("effect"))
            rec["pos_sym"] = sum(1 for e in vals if e["delta_vs_base"] > EPS)
            rec["neg_sym"] = sum(1 for e in vals if e["delta_vs_base"] < -EPS)
            rec["zero_sym"] = sum(1 for e in vals if abs(e["delta_vs_base"]) <= EPS)
            rec["notwired_syms"] = sum(1 for e in rec["per_sym"].values() if e.get("status") == "NOT_WIRED_VEC")
            rec["avg_delta_vs_base"] = round(statistics.fmean([e["delta_vs_base"] for e in vals]), 6) if vals else None
            rec["wiring"] = wiring_of(h.split("=", 1)[0].strip(), census, unw_s)
            rec["verdict"] = ("NOT_WIRED_VEC" if rec["notwired_syms"] and not vals else "UNKNOWN" if rec["n"] < min_n else "ACTIVE" if rec["effect_syms"] else "INERT")
        for key, rec in cellB.items():
            vals = [e for e in rec["per_sym"].values() if "delta_vs_naked" in e]
            rec["n"] = len(vals)
            rec["effect_syms"] = sum(1 for e in vals if e.get("effect"))
            rec["pos_sym"] = sum(1 for e in vals if e["delta_vs_naked"] > EPS)
            rec["neg_sym"] = sum(1 for e in vals if e["delta_vs_naked"] < -EPS)
            rec["zero_sym"] = sum(1 for e in vals if abs(e["delta_vs_naked"]) <= EPS)
            rec["avg_delta_vs_naked"] = round(statistics.fmean([e["delta_vs_naked"] for e in vals]), 6) if vals else None
        atomic_write(d / f"evidence_{cs}.json", {"cat_side": cs, "syms": syms, "filters_stage_A": flt, "cells_stage_B_binding": cellB, "inherited_rows_per_sym": inh, "rows_per_sym": rows_per_sym,
                                                 "note": "Stage B cells exist only for BINDING switch rows; non-binding rows are INHERITED_FROM_BASELINE (their row-specific effect == the filter's Stage A effect on that sym_side); pairs never evaluated are UNKNOWN."})
        cnt = {}
        for h2, rec in flt.items():
            cnt[rec["verdict"]] = cnt.get(rec["verdict"], 0) + 1
            if rec["wiring"] == "REAL" and rec["verdict"] == "INERT":
                zero_real.setdefault(cs, []).append({"filter_opt": h2, "n": rec["n"], "note": "REAL filter (calculates somewhere per census) is INERT on every audited sym_side: flag for the wiring agents"})
            if rec["wiring"] in ("STUB_NO_CONSUMER", "NOT_WIRED_VEC") and rec.get("effect_syms"):
                rec["WARNING"] = "non-zero effect on a stub/unwired filter: investigate (dependency key side effect?)"
        out["cat_sides"][cs] = {"syms": len(syms), "filters": len(flt), "filter_verdicts": cnt, "stageB_cells": len(cellB)}
        print(f"[{cs}] syms={len(syms)} filters={len(flt)} {cnt} stageB binding cells={len(cellB)}")
    atomic_write(d / "summary.json", out)
    atomic_write(d / "zero_real_filters.json", zero_real)


# ───────────────────────── dry-run / estimates ─────────────────────────
def dry_run(a, P, d: Path):
    unw = unwired_filters()
    tot_a = tot_b = 0.0
    for cs in a.cats:
        headers, rows, tabs = template_universe(cs, P, d)
        wired = [h for h in headers if h.split("=", 1)[0].strip() not in unw]
        venue = cs.split("_")[0]
        for win in (365, 30):
            cost = COST[(win, venue)]
            a_evals = len(wired) + 1
            b_naked = len(rows)
            for frac, act in ((0.05, 0.4), (0.09, 0.8)):
                b_cells = int(len(rows) * frac) * int(len(wired) * act)
                sec = (a_evals + b_naked + b_cells) * cost
                print(f"[{cs}] win {win}D: headers {len(headers)} (wired now {len(wired)}; after deploy up to {len(headers)}), unique rows {len(rows)} | Stage A ~{a_evals} evals ({a_evals * cost / 60:.1f} cpu-min) | "
                      f"Stage B naked {b_naked} + cells {b_cells} (binding {frac:.0%}, active {act:.0%}) = {(b_naked + b_cells)} evals -> {sec / 3600:.2f} cpu-h/sym, {sec / a.workers / 60:.0f} min wall at {a.workers} workers")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat-side", action="append", choices=CAT_SIDES)
    ap.add_argument("--stage", choices=["A", "B", "F"], default="A", help="F = FULL GRID: every switch row x every wired filter column (user 2026-10-01), on --f-limit selected sym_sides per cat_side")
    ap.add_argument("--f-limit", default="", help="Stage F: sym_sides per cat_side, e.g. 2 or crypto:1,stocks:2 (evenly spaced over the sorted candidate list, 365D-available first)")
    ap.add_argument("--window", type=int, choices=[30, 365], default=365)
    ap.add_argument("--symsides", help="comma list; default = all candidates of the cat_side on THIS host")
    ap.add_argument("--manifest", help="data/yellow_discovery/manifest.json from tools/v15_filter_discovery_plan.py (deduped sources assigned to THIS host)")
    ap.add_argument("--shard", default="0/1", help="i/n: run only sym_sides i, i+n, ... of the candidate list (several processes per host overlap NPZ prepare with evaluation)")
    ap.add_argument("--fixed-only", action="store_true")
    ap.add_argument("--reverse", action="store_true", help="walk this shard's sym_sides from the END (a second process per shard meets the forward one in the middle; per-sym results are resumable)")
    ap.add_argument("--limit", type=int, default=0, help="max sym_sides this run (0 = all)")
    ap.add_argument("--progress-glob", action="append", default=None, help="default ~/v15_run*/progress/*_v14_progress.json")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--nice", type=int, default=0)
    ap.add_argument("--max-secs", type=int, default=0)
    ap.add_argument("--min-n", type=int, default=3)
    ap.add_argument("--dir", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summarize", action="store_true")
    ap.add_argument("--sanity", action="store_true", help="2 sym_sides, Stage A only, 365D: wired filters must show effects after an engine deploy")
    a = ap.parse_args()
    global FIXED_ONLY
    FIXED_ONLY = bool(a.fixed_only)
    a.cats = a.cat_side or list(CAT_SIDES)
    d = Path(a.dir) if a.dir else ROOT / "data" / "yellow_discovery" / "filter_discovery"
    d.mkdir(parents=True, exist_ok=True)
    if a.summarize:
        return summarize(d, a.min_n)
    import v15_pilot as P
    if a.dry_run:
        return dry_run(a, P, d)
    if a.sanity:
        a.stage, a.limit = "A", 2
    if a.nice:
        try:
            os.nice(a.nice)
        except OSError:
            pass
    unw = unwired_filters()
    globs = a.progress_glob or ["~/v15_*/progress/*_v14_progress.json", "~/binance-sandbox/data/reports/lifecycle_pilot/*_v14_progress.json"]
    t_start = time.time()
    for cs in a.cats:
        headers, rows, _ = template_universe(cs, P, d)
        cands = candidates_from_manifest(cs, a.manifest) if a.manifest else candidates(cs, P, globs)
        if a.symsides:
            want = set(a.symsides.split(","))
            cands = [c for c in cands if c["symside"] in want]
        si, sn = (int(x) for x in a.shard.split("/"))
        cands = cands[si::sn]
        if a.reverse:
            cands = cands[::-1]
        if a.stage == "F" and a.f_limit:
            _lim = {}
            for part in str(a.f_limit).split(","):
                if ":" in part:
                    k_, v_ = part.split(":"); _lim[k_.strip().lower()] = int(v_)
                else:
                    _lim["crypto"] = _lim["stocks"] = int(part)
            fl = _lim.get("crypto" if cs.startswith("CRYPTO") else "stocks", 0)
            if fl and len(cands) > fl:
                n_ = len(cands); cands = [cands[int(i * n_ / fl)] for i in range(fl)]
        if a.limit:
            cands = cands[: a.limit]
        print(f"[{cs}] {len(cands)} sym_sides, {len(headers)} filter headers, {len(rows)} unique switch rows, unwired filters {len(unw)}, engine {engine_md5()}", flush=True)
        for c in cands:
            run_symside(cs, c, a, P, d, headers, rows, unw, t_start)
            if a.max_secs and time.time() - t_start > a.max_secs:
                break
    summarize(d, a.min_n)


if __name__ == "__main__":
    main()
