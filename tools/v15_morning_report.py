#!/usr/bin/env python3
"""v15_morning_report — daily verify + analyse the last round of 30D backtests across s1/s2/s5.

Where it fits (DAILY_OPTIMIZATION_PLAN.md, Stage 7 boundary): every day at/near market open, after the
full 30D sweep on all sym_sides has run and v15_avg_delta has been rebuilt+applied to the 4 TEMPLATE_*
sheets, this script produces the MORNING REPORT. It does three jobs, all NO-LIES (only real recorded
numbers; nothing annualised, nothing fabricated):

  1. VERIFY the round      — how many sym_sides finished on each server in the window, freshness, coverage
                             gaps (Mac vs servers), engine md5 parity across boxes (mixed-baseline = a lie),
                             and per-sym_side NO-LIES flags: ZERO-DELTA (dead NPZ / no-op stubs) and
                             REPEATED-DELTA (the v12 synthetic-distinctness fabrication trap).
  2. PER CAT_SIDE analysis — CRYPTO_LONG/SHORT, STOCKS_LONG/SHORT: count, aggregate gain vs buy&hold,
                             TOP and BOTTOM performers (by final gain AND by within-round improvement),
                             and day-over-day comparison against the previous snapshot this script wrote.
  3. DEAD SWITCH/FILTER worklist (bottom section) — from the authoritative v15_avg_delta workbook
                             (SPREADSHEETS/v15_avg_delta_latest.xlsx) plus the dated archive diff: every
                             switch/filter value that produces NO value anywhere (never tested, all-zero
                             no-op, or one-repeated-value fabrication), categorised with a concrete fix
                             instruction per offender so an agent can action every one.

Source of truth:
  * per-sym_side performance  = data/reports/lifecycle_pilot/*_v14_progress.json (Mac) + each server's
                                newest ~/v15_run*/progress and ~/binance-sandbox/.../lifecycle_pilot,
                                deduped by sym_side keeping the newest mtime in the window.
  * dead switch/filter        = SPREADSHEETS/v15_avg_delta_latest.xlsx (built by v15_avg_delta_rebuild.py)
                                + SPREADSHEETS/v15_avg_delta/v15_avg_delta_{date}.xlsx archive for the diff.

Outputs:
  * data/reports/v15_morning_report_{YYYYMMDD}.md   — the report.
  * data/reports/v15_daily/snapshot_{YYYYMMDD}.json — per-sym_side + per-cat_side metrics, so tomorrow's
                                                      run has a real "previous day" to diff against.

Run now (Mac):   python3 tools/v15_morning_report.py --window-hours 30
Local only:      python3 tools/v15_morning_report.py --no-remote
Cron (Mac, ~market open, after avg_delta apply):  python3 tools/v15_morning_report.py
"""
import argparse
import datetime
import glob
import json
import os
import pathlib
import statistics
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROG_LOCAL = ROOT / "data" / "reports" / "lifecycle_pilot"
AVG_LATEST = ROOT / "SPREADSHEETS" / "v15_avg_delta_latest.xlsx"
AVG_ARCHIVE = ROOT / "SPREADSHEETS" / "v15_avg_delta"
REPORT_DIR = ROOT / "data" / "reports"
SNAP_DIR = ROOT / "data" / "reports" / "v15_daily"
CAT_SIDES = ["CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"]
EPS = 1e-9

# server -> ssh alias (with fallbacks tried in order). s1's ControlMaster mux goes stale, so s1-pub is the
# reliable public-IP fallback; s2/s5 answer on their bare aliases (see CLAUDE.md INFRASTRUCTURE).
HOSTS = {
    "s1": ["s1-int", "s1-pub", "s1"],
    "s2": ["s2", "s2-fresh-int"],
    "s5": ["s5"],
}
REMOTE_DIR_GLOBS = "~/v15_run*/progress ~/binance-sandbox/data/reports/lifecycle_pilot"
ENGINE_REL = "~/binance-sandbox/v12_quick_engine.py"

# Self-contained collector: run identically on the Mac (subprocess) and on each server (ssh python3 -c).
# argv[1] = window hours, argv[2:] = dir globs to scan. Prints one JSON object. Stdlib only. The delta/
# yellow accounting mirrors tools/v15_avg_delta_rebuild.py exactly so flags line up with the avg workbook.
COLLECTOR = r'''
import sys, json, glob, os, statistics, collections, time
WIN_H = float(sys.argv[1]); GLOBS = sys.argv[2:]; NOW = time.time(); EPS = 1e-9
CRYPTO_SFX = ("USDT","USDC","USD1","BUSD","FDUSD","TUSD","DAI")
SKIP = ("DEAD_VEC","LIVE_ONLY","SKIPPED_GREY","NO_CANDIDATE","NOT_IN_CONFIG","INVENTED_ALT")
def cat_side_of(ss):
    s=ss.upper()
    if s.endswith("_LONG"): side="LONG"; base=s[:-5]
    elif s.endswith("_SHORT"): side="SHORT"; base=s[:-6]
    else: return None
    venue="CRYPTO" if base.endswith(CRYPTO_SFX) else "STOCKS"
    return venue+"_"+side
def num(x): return isinstance(x,(int,float)) and not isinstance(x,bool)
# newest progress json per sym_side across all globbed dirs
best={}
for g in GLOBS:
    for f in glob.glob(os.path.join(os.path.expanduser(g),"*_v14_progress.json")):
        ss=os.path.basename(f)[:-len("_v14_progress.json")]
        m=os.path.getmtime(f)
        if ss not in best or m>best[ss][0]: best[ss]=(m,f)
rows=[]
for ss,(m,f) in best.items():
    if (NOW-m)/3600.0 > WIN_H: continue
    cs=cat_side_of(ss)
    if cs is None: continue
    try: d=json.load(open(f))
    except Exception: continue
    done=d.get("done",{})
    if not isinstance(done,dict): done={}
    base = d.get("initial_baseline_gain")
    if not num(base): base=d.get("baseline_gain")
    final = d.get("final_gain")
    if not num(final): final=d.get("cumulative_gain")
    if not num(final): final=base
    bh=d.get("bh")
    # per-switch delta accounting + final-step vec (trades/sharpe/dd of the winning cumulative point)
    deltas=[]; npos=nneg=nzero=0; top=None; topc=-1e18
    ZTOL=1e-6; bl={}  # (switch, rounded baseline) -> {value_lower: delta} for the default-baseline check
    for k,e in done.items():
        if not isinstance(e,dict): continue
        r=str(e.get("reason") or "")
        ca=e.get("cumulative_after")
        # final-step representative = entry at the top of the greedy cumulative chain (gain ~= final_gain)
        if num(ca) and ca>topc: topc=ca; top=e
        if r.startswith(SKIP): continue
        dl=e.get("delta")
        # default-baseline integrity (USER 2026-09-30): a value equal to the running baseline MUST give
        # delta 0. For a bool switch, the baseline is True OR False, so if BOTH are evaluated against the
        # SAME baseline (cumulative_before) and NEITHER is ~0, the baseline held neither = impossible.
        if num(dl) and not e.get("delta_invalid") and ":" in k and "=" in k:
            try:
                sw,val=k.split(":",1)[1].split("=",1); cb=e.get("cumulative_before")
                vlow=val.strip().lower()
                if vlow in ("true","false") and num(cb):
                    bl.setdefault((sw.strip(),round(cb,6)),{})[vlow]=float(dl)
            except Exception: pass
        if num(dl) and not e.get("delta_invalid"):
            deltas.append(float(dl))
            if dl>EPS: npos+=1
            elif dl<-EPS: nneg+=1
            else: nzero+=1
        elif e.get("is_running"):
            deltas.append(0.0); nzero+=1
    bool_incons=[]  # [switch, true_delta, false_delta] where both present, same baseline, neither ~0
    for (sw,cb),vv in bl.items():
        if "true" in vv and "false" in vv and min(abs(vv["true"]),abs(vv["false"]))>ZTOL:
            bool_incons.append([sw,round(vv["true"],6),round(vv["false"],6)])
    incons_sw=sorted({x[0] for x in bool_incons})
    nz=[round(x,6) for x in deltas if abs(x)>EPS]
    rep_val=rep_cnt=0
    if nz:
        val,cnt=collections.Counter(nz).most_common(1)[0]; rep_val, rep_cnt = val, cnt
    te=top or {}; tv=te.get("vec") if isinstance(te.get("vec"),dict) else {}
    def pick(k):  # old schema stored stats under vec{}, run16 stores trades on the entry directly
        v=tv.get(k)
        return v if v is not None else te.get(k)
    rows.append({
        "symside":ss,"cat_side":cs,"mtime":m,"file":f,
        "baseline":base if num(base) else None,
        "final":final if num(final) else None,
        "improve":(final-base) if (num(final) and num(base)) else None,
        "bh":bh if num(bh) else None,
        "trades":pick("trades"),"pool_sharpe":pick("pool_sharpe"),
        "max_dd_pct":pick("max_dd_pct"),"tim_pct":pick("tim_pct"),
        "n_done":len(done),"n_delta":len(deltas),"n_pos":npos,"n_neg":nneg,"n_zero":nzero,
        "nz_count":len(nz),"rep_val":rep_val,"rep_cnt":rep_cnt,
        "bool_incons_n":len(bool_incons),"bool_incons_sw":incons_sw[:12],
        "bool_incons_ex":sorted(bool_incons,key=lambda x:-min(abs(x[1]),abs(x[2])))[:5],
    })
print(json.dumps({"rows":rows}))
'''


def run_collector_local(window_h):
    dirs = str(PROG_LOCAL)
    try:
        r = subprocess.run([sys.executable, "-c", COLLECTOR, str(window_h), dirs],
                           capture_output=True, text=True, timeout=300)
        return _parse(r.stdout), None
    except Exception as e:
        return [], str(e)[:120]


def run_collector_remote(aliases, window_h):
    for alias in aliases:
        try:
            cmd = f"cd ~/binance-sandbox 2>/dev/null; python3 -c {json_q(COLLECTOR)} {window_h} {REMOTE_DIR_GLOBS}"
            r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                               "-o", "StrictHostKeyChecking=accept-new", alias, cmd],
                              capture_output=True, text=True, timeout=180)
            rows = _parse(r.stdout)
            if rows or (r.returncode == 0 and r.stdout.strip()):
                return rows, alias, None
        except Exception as e:
            last = str(e)[:120]
            continue
    return [], aliases[0], "unreachable (tried: " + ", ".join(aliases) + ")"


def json_q(s):
    # shell-safe single-quote wrap of the collector for `python3 -c '...'`
    return "'" + s.replace("'", "'\\''") + "'"


def _parse(stdout):
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line).get("rows", [])
            except Exception:
                return []
    return []


def engine_md5(aliases):
    for alias in aliases:
        try:
            r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                               "-o", "StrictHostKeyChecking=accept-new", alias,
                               f"md5sum {ENGINE_REL} 2>/dev/null | awk '{{print $1}}'"],
                              capture_output=True, text=True, timeout=30)
            h = (r.stdout or "").strip().split("\n")[0]
            if len(h) == 32:
                return h
        except Exception:
            continue
    return None


def local_engine_md5():
    try:
        import hashlib
        return hashlib.md5((ROOT / "v12_quick_engine.py").read_bytes()).hexdigest()
    except Exception:
        return None


def fmt(x, d=2, plus=False):
    if x is None:
        return "—"
    try:
        s = f"{x:+.{d}f}" if plus else f"{x:.{d}f}"
        return s
    except Exception:
        return str(x)


# ------------------------- dead switch / filter analysis -------------------------

def load_avg_pertab(path):
    """Return {cat_side: {(tab, name): {"kind","pos_sym","avg","median","n"}}} from a per-tab avg_delta workbook.
    Tab-less (older) sheets map to tab=""."""
    import openpyxl
    out = {cs: {} for cs in CAT_SIDES}
    if not pathlib.Path(path).exists():
        return out
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    for cs in CAT_SIDES:
        if cs not in wb.sheetnames:
            continue
        it = wb[cs].iter_rows(values_only=True)
        hdr = [str(h) for h in next(it)]
        ix = {h: hdr.index(h) for h in ("name", "kind", "pos_sym", "avg_delta", "median_delta", "n") if h in hdr}
        itab = hdr.index("tab") if "tab" in hdr else None
        for r in it:
            if not r or r[ix["name"]] is None:
                continue
            tab = str(r[itab]).strip() if itab is not None else ""
            name = str(r[ix["name"]]).strip()
            out[cs][(tab, name)] = {
                "kind": str(r[ix["kind"]]) if "kind" in ix else "",
                "pos_sym": int(r[ix["pos_sym"]] or 0) if "pos_sym" in ix else 0,
                "avg": float(r[ix["avg_delta"]] or 0.0) if "avg_delta" in ix else 0.0,
                "median": float(r[ix["median_delta"]] or 0.0) if "median_delta" in ix else 0.0,
                "n": int(r[ix["n"]] or 0) if "n" in ix else 0,
            }
    wb.close()
    return out


def load_avg(path):
    """Return {cat_side: {name: {...}}} with tabs MERGED per name (n-weighted avg) — the collapsed cat_side lever
    view the dead-lever analysis wants. Underlying sheet is per-tab (USER 2026-09-30); use load_avg_pertab for tabs."""
    per = load_avg_pertab(path)
    out = {cs: {} for cs in CAT_SIDES}
    for cs in CAT_SIDES:
        acc = {}
        for (tab, name), e in per[cs].items():
            a = acc.setdefault(name, {"kind": e["kind"], "pos_sym": 0, "n": 0, "wavg": 0.0, "wmed": 0.0})
            if e["kind"]:
                a["kind"] = e["kind"]
            a["pos_sym"] += e["pos_sym"]
            a["n"] += e["n"]
            a["wavg"] += e["avg"] * e["n"]
            a["wmed"] += e["median"] * e["n"]
        for name, a in acc.items():
            n = a["n"] or 1
            out[cs][name] = {"kind": a["kind"], "pos_sym": a["pos_sym"], "n": a["n"],
                             "avg": a["wavg"] / n, "median": a["wmed"] / n}
    return out


def template_switch_universe():
    """Switch names (col A) present in the 4 TEMPLATE_* sheets = the wired/swept universe. Used to tell a
    NEVER-TESTED switch (in template, absent from avg_delta) from one that is simply not in the template."""
    import openpyxl
    from tools.v15_avg_delta_apply import SWITCH_SHEETS  # noqa
    names = set()
    for fn in ["TEMPLATE_CRYPTO_LONG.xlsx", "TEMPLATE_CRYPTO_SHORT.xlsx",
               "TEMPLATE_STOCKS_LONG.xlsx", "TEMPLATE_STOCKS_SHORT.xlsx"]:
        p = ROOT / "SPREADSHEETS" / fn
        if not p.exists():
            continue
        try:
            wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
        except Exception:
            continue
        for sh in SWITCH_SHEETS:
            if sh not in wb.sheetnames:
                continue
            for row in wb[sh].iter_rows(min_row=3, max_col=1, values_only=True):
                if row and row[0] and str(row[0]).strip():
                    names.add(str(row[0]).strip().upper())
        wb.close()
    return names


NOOP_AVG = 0.01  # |avg_delta| below this (gain %) for EVERY value of a lever = it moves nothing = no-op


def base_of(name):
    # "SWITCH=value" -> "SWITCH"; a FILTER_TF header "FOO_FILTER_TF=4h" -> "FOO_FILTER_TF"
    return name.split("=", 1)[0].strip()


def analyse_dead(avg_latest):
    """Aggregate to the LEVER level (base switch / filter name, across all its VALUES and all 4 cat_sides).
    A lever is what the operator means by 'a switch/filter that produces no value anywhere'. Returns
    base -> record with a classification:
      NEVER_TESTED  — n==0 everywhere (no candidate delta was ever recorded: wiring / whitelist gap).
      DEAD_NOOP     — tested, but |avg| < NOOP_AVG for EVERY value in EVERY cat_side → the lever moves
                      nothing (dead engine key / no-op stub). THE headline defect.
      NEVER_HELPS   — tested, has real non-zero deltas, but NO value is ever positive in any cat_side
                      (honest loser lever → Stage-6 throttle candidate; condensed, not a defect per se).
      OK            — at least one value is a real positive somewhere.
    """
    levers = {}
    for cs in CAT_SIDES:
        for name, e in avg_latest[cs].items():
            b = base_of(name)
            r = levers.setdefault(b, {"kind": e["kind"] or "switch", "values": set(), "cat_sides": set(),
                                      "tot_n": 0, "tot_pos": 0, "max_abs_avg": 0.0, "best_avg": None,
                                      "worst_avg": None, "rows": []})
            if e["kind"]:
                r["kind"] = e["kind"]
            r["values"].add(name.split("=", 1)[1] if "=" in name else "")
            r["cat_sides"].add(cs)
            r["tot_n"] += e["n"]
            r["tot_pos"] += e["pos_sym"]
            r["max_abs_avg"] = max(r["max_abs_avg"], abs(e["avg"]))
            r["best_avg"] = e["avg"] if r["best_avg"] is None else max(r["best_avg"], e["avg"])
            r["worst_avg"] = e["avg"] if r["worst_avg"] is None else min(r["worst_avg"], e["avg"])
            r["rows"].append((cs, name, e["avg"], e["pos_sym"], e["n"]))
    for b, r in levers.items():
        if r["tot_n"] == 0:
            r["cls"] = "NEVER_TESTED"
        elif r["max_abs_avg"] < NOOP_AVG:
            r["cls"] = "DEAD_NOOP"
        elif r["tot_pos"] == 0:
            r["cls"] = "NEVER_HELPS"
        else:
            r["cls"] = "OK"
        r["n_values"] = len(r["values"])
        r["cat_side_list"] = sorted(r["cat_sides"])
    return levers


def fix_hint(base, kind, cls):
    # short per-row cell; the full methodology is the one-time note under each subsection
    if cls == "NEVER_TESTED":
        return "wiring/whitelist gap — confirm in wired∩live set, template row not grey, v15_pilot emits values"
    if cls == "DEAD_NOOP":
        tf = " (FILTER_TF stub? check opportune_filter_map)" if str(base).endswith("_FILTER_TF") else ""
        return f"trace `{base}` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead{tf}"
    if cls == "NEVER_HELPS":
        return "honest loser — Stage-6 throttle, never promote; confirm live==vec once"
    return ""


def consistency_summary(rows):
    """Default-baseline integrity (USER 2026-09-30). A boolean switch's baseline is True OR False, so when
    both are evaluated against the same baseline exactly one MUST score delta 0. Rows where neither does are
    NO-LIES inconsistencies (a default value that scored non-zero against itself). Returns
    (offender_rows, switch_counter, total_pairs)."""
    import collections as _c
    offenders = [r for r in rows if r.get("bool_incons_n")]
    swc = _c.Counter()
    total = 0
    for r in rows:
        total += r.get("bool_incons_n") or 0
        for sw in (r.get("bool_incons_sw") or []):
            swc[sw] += 1
    return offenders, swc, total


def consistency_lines(rows):
    offenders, swc, total = consistency_summary(rows)
    L = [f"- **DEFAULT-BASELINE INCONSISTENCIES** (a bool switch where both True & False scored non-zero vs "
         f"the same baseline — one MUST be the default → 0; NO-LIES): **{len(offenders)} sym_sides, "
         f"{total} switch×baseline cases**"]
    if offenders:
        L.append(f"    - worst offending switches (by # sym_sides): " +
                 ", ".join(f"`{sw}`×{n}" for sw, n in swc.most_common(12)))
        for r in sorted(offenders, key=lambda r: -r["bool_incons_n"])[:12]:
            ex = r.get("bool_incons_ex") or []
            exs = "; ".join(f"{e[0]} T={e[1]:+.3f}/F={e[2]:+.3f}" for e in ex[:2])
            L.append(f"    - `{r['symside']}` [{r['host']}] {r['bool_incons_n']} cases — {exs}")
        L.append("    - _Fix: the engine must return the frozen-baseline gain (delta 0) for the value that "
                 "equals the sym_side's running config. A non-zero there means the baseline snapshot and the "
                 "candidate eval used different configs/NPZ, or the eval is non-deterministic — trace "
                 "`evaluate_prepared_sanitized` baseline handling. These deltas are unsafe to promote._")
    return L + [""]


# ------------------------- report build -------------------------

def build(window_h, use_remote):
    day = datetime.date.today().strftime("%Y%m%d")
    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ")
    sources = {}  # host -> {"rows":[], "alias":str, "err":str|None, "md5":str|None}

    lr, lerr = run_collector_local(window_h)
    sources["mac"] = {"rows": lr, "alias": "local", "err": lerr, "md5": local_engine_md5()}
    if use_remote:
        for host, aliases in HOSTS.items():
            rows, alias, err = run_collector_remote(aliases, window_h)
            sources[host] = {"rows": rows, "alias": alias, "err": err, "md5": engine_md5(aliases)}

    # dedup per sym_side keeping newest mtime across all sources; remember contributing host
    merged = {}
    for host, s in sources.items():
        for r in s["rows"]:
            ss = r["symside"]
            r = dict(r, host=host)
            if ss not in merged or r["mtime"] > merged[ss]["mtime"]:
                merged[ss] = r
    allrows = list(merged.values())

    # per cat_side buckets
    by_cs = {cs: [] for cs in CAT_SIDES}
    for r in allrows:
        by_cs.get(r["cat_side"], []).append(r)

    # dead switch/filter
    avg_latest = load_avg(AVG_LATEST)
    dead = analyse_dead(avg_latest)
    try:
        univ = template_switch_universe()
    except Exception:
        univ = set()

    # previous snapshot for day-over-day
    prev = load_prev_snapshot(day)

    L = []
    L.append(f"# v15 Morning Report — {day}")
    L.append("")
    L.append(f"_Generated {stamp} · window = last {window_h}h · NO-LIES: only real recorded deltas, no annualisation._")
    L.append("")

    # ---- 1. VERIFY ----
    L.append("## 1 · Round verification")
    L.append("")
    L.append("| source | alias | sym_sides in window | freshest | oldest | engine md5 | status |")
    L.append("|---|---|---|---|---|---|---|")
    md5s = {}
    for host in ["mac", "s1", "s2", "s5"]:
        s = sources.get(host)
        if not s:
            continue
        rws = s["rows"]
        md5s[host] = s["md5"]
        if rws:
            fresh = max(r["mtime"] for r in rws)
            old = min(r["mtime"] for r in rws)
            fresh_s = datetime.datetime.utcfromtimestamp(fresh).strftime("%m-%d %H:%MZ")
            old_s = datetime.datetime.utcfromtimestamp(old).strftime("%m-%d %H:%MZ")
        else:
            fresh_s = old_s = "—"
        status = "OK" if (rws and not s["err"]) else (s["err"] or "no rows in window")
        L.append(f"| {host} | {s['alias']} | {len(rws)} | {fresh_s} | {old_s} | {(s['md5'] or '—')[:12]} | {status} |")
    L.append("")
    L.append(f"**Deduped sym_sides in window (newest-per-sym_side across all boxes): {len(allrows)}**")
    L.append("")
    # engine md5 parity (mixed baseline = NO-LIES violation)
    present = {h: m for h, m in md5s.items() if m}
    if len(set(present.values())) > 1:
        L.append("> ⚠️ **ENGINE MD5 MISMATCH across boxes — MIXED BASELINE. Deltas from different engines "
                 "are not comparable (NO-LIES).** Re-sync `v12_quick_engine.py`, rerun the mismatched box.")
        for h, m in present.items():
            L.append(f"> - {h}: `{m[:12]}`")
        L.append("")
    elif present:
        L.append(f"Engine md5 parity across boxes: ✅ `{list(present.values())[0][:12]}` on {', '.join(present)}.")
        L.append("")

    # NO-LIES per-sym_side offenders
    zero_off = [r for r in allrows if r["n_delta"] >= 8 and r["nz_count"] == 0]
    rep_off = [r for r in allrows if r["nz_count"] >= 16 and r["rep_cnt"] >= 8 and r["rep_cnt"] >= 0.5 * r["nz_count"]]
    data_err = [r for r in allrows if (r["trades"] in (0, None) and (r["baseline"] in (0, None)))]
    L.append(f"- **ZERO-DELTA sym_sides** (≥8 switches, every delta ~0 → dead NPZ / no-op stubs): **{len(zero_off)}**")
    for r in sorted(zero_off, key=lambda r: -r["n_delta"])[:15]:
        L.append(f"    - `{r['symside']}` [{r['host']}] {r['n_delta']} switches all-zero")
    L.append(f"- **REPEATED-DELTA sym_sides** (one value repeated across many switches → v12 synthetic-"
             f"distinctness fabrication, NEVER promote): **{len(rep_off)}**")
    for r in sorted(rep_off, key=lambda r: -r["rep_cnt"])[:15]:
        L.append(f"    - `{r['symside']}` [{r['host']}] {r['rep_val']} ×{r['rep_cnt']}/{r['nz_count']}")
    L.append(f"- **DATA_ERROR / zero-trade sym_sides** (no baseline trades — NPZ gap): **{len(data_err)}**")
    for r in sorted(data_err, key=lambda r: r["symside"])[:15]:
        L.append(f"    - `{r['symside']}` [{r['host']}]")
    L.append("")
    L.extend(consistency_lines(allrows))

    # ---- 2. PER CAT_SIDE ----
    L.append("## 2 · Per cat_side performance")
    L.append("")
    snap = {"day": day, "generated": stamp, "window_hours": window_h, "cat_sides": {}, "symsides": {}}
    for cs in CAT_SIDES:
        rows = [r for r in by_cs[cs] if r["final"] is not None]
        L.append(f"### {cs}  ·  {len(rows)} sym_sides")
        if not rows:
            L.append("_No sym_sides finished in the window._\n")
            snap["cat_sides"][cs] = {"n": 0}
            continue
        finals = [r["final"] for r in rows]
        bhs = [r["bh"] for r in rows if r["bh"] is not None]
        imps = [r["improve"] for r in rows if r["improve"] is not None]
        beat = sum(1 for r in rows if r["bh"] is not None and r["final"] > r["bh"])
        agg = {
            "n": len(rows),
            "mean_final": statistics.fmean(finals),
            "median_final": statistics.median(finals),
            "mean_bh": statistics.fmean(bhs) if bhs else None,
            "mean_improve": statistics.fmean(imps) if imps else None,
            "pos_final": sum(1 for f in finals if f > 0),
            "beat_bh": beat,
        }
        snap["cat_sides"][cs] = agg
        pv = (prev or {}).get("cat_sides", {}).get(cs) if prev else None

        def dv(key):
            if not pv or pv.get(key) is None or agg.get(key) is None:
                return ""
            return f" (Δ {agg[key]-pv[key]:+.2f} vs prev)"

        L.append(f"- mean final gain **{fmt(agg['mean_final'])}%**{dv('mean_final')} · "
                 f"median **{fmt(agg['median_final'])}%** · mean B&H {fmt(agg['mean_bh'])}%")
        L.append(f"- positive-gain: **{agg['pos_final']}/{agg['n']}** · beat B&H: **{agg['beat_bh']}/{len(bhs)}** · "
                 f"mean within-round improvement {fmt(agg['mean_improve'], plus=True)}%{dv('mean_improve')}")
        if pv:
            L.append(f"- prev snapshot ({prev.get('day')}): n={pv.get('n')} mean_final={fmt(pv.get('mean_final'))}%")
        L.append("")
        # top / bottom by final gain
        by_final = sorted(rows, key=lambda r: r["final"], reverse=True)
        L.append("| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |")
        L.append("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(by_final[:5], 1):
            L.append(f"| {i} | `{r['symside']}` | {fmt(r['final'])} | {fmt(r['bh'])} | {fmt(r['improve'], plus=True)} | "
                     f"{r['trades'] if r['trades'] is not None else '—'} | {fmt(r['pool_sharpe'], 3)} | {fmt(r['max_dd_pct'])} |")
        L.append("")
        L.append("| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |")
        L.append("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(by_final[-5:][::-1], 1):
            L.append(f"| {i} | `{r['symside']}` | {fmt(r['final'])} | {fmt(r['bh'])} | {fmt(r['improve'], plus=True)} | "
                     f"{r['trades'] if r['trades'] is not None else '—'} | {fmt(r['pool_sharpe'], 3)} | {fmt(r['max_dd_pct'])} |")
        L.append("")
        # biggest within-round movers (optimisation actually helped)
        movers = sorted([r for r in rows if r["improve"] is not None], key=lambda r: r["improve"], reverse=True)
        if movers:
            L.append("Biggest within-round improvements (baseline → final): " +
                     ", ".join(f"`{r['symside']}` {fmt(r['improve'], plus=True)}%" for r in movers[:5]))
            L.append("")
        for r in rows:
            snap["symsides"][r["symside"]] = {"final": r["final"], "bh": r["bh"], "improve": r["improve"],
                                              "trades": r["trades"], "pool_sharpe": r["pool_sharpe"], "cat_side": cs}

    # day-over-day per-sym_side movers (needs a prior snapshot)
    if prev and prev.get("symsides"):
        L.append("### Day-over-day movers (final gain vs previous snapshot)")
        diffs = []
        for ss, cur in snap["symsides"].items():
            p = prev["symsides"].get(ss)
            if p and p.get("final") is not None and cur.get("final") is not None:
                diffs.append((ss, cur["final"] - p["final"], p["final"], cur["final"], cur["cat_side"]))
        diffs.sort(key=lambda x: x[1])
        if diffs:
            L.append("| sym_side | cat_side | prev% | now% | Δ |")
            L.append("|---|---|---|---|---|")
            for ss, d, pf, cf, cs in (diffs[:8] + diffs[-8:]):
                L.append(f"| `{ss}` | {cs} | {fmt(pf)} | {fmt(cf)} | {fmt(d, plus=True)} |")
        else:
            L.append("_No overlapping sym_sides with the previous snapshot yet._")
        L.append("")
    else:
        L.append("### Day-over-day movers")
        L.append("_First snapshot written this run — per-sym_side day-over-day comparison starts tomorrow. "
                 "(Switch/filter day-over-day is available below from the avg_delta archive.)_")
        L.append("")

    # ---- 3. DEAD SWITCH / FILTER WORKLIST ----
    L.extend(dead_section(dead, univ, day))
    L.extend(chain_section(day))

    report = "\n".join(L) + "\n"
    out = REPORT_DIR / f"v15_morning_report_{day}.md"
    out.write_text(report)
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    (SNAP_DIR / f"snapshot_{day}.json").write_text(json.dumps(snap, indent=1))
    return out, report, len(allrows), dead


def _cs_short(lst):
    return ",".join(c.replace("CRYPTO_", "C_").replace("STOCKS_", "S_") for c in lst) or "none"


def chain_section(day):
    """ADDITIVE (USER 2026-09-30): 365D / REPAIR / live-faithful / promotions / unfinished-before-open, from the fleet scheduler + pipeline
    state files. Never raises — a missing file just yields a 'no data' line."""
    L = ["## 4 · Daily chain: 365D verify, REPAIR loop, live-faithful, promotions", ""]
    base = pathlib.Path(__file__).resolve().parents[1] / "data"
    dd = base / "daily_reports" / day
    def jl(p):
        try:
            return json.loads(pathlib.Path(p).read_text())
        except Exception:
            return None
    try:
        cs = jl(dd / "chain_state.json")
        pl = jl(base / "daily_pipeline_state.json") or {}
        if not cs:
            L += ["_no chain_state.json for today (fleet scheduler has not run / not deployed yet)_", ""]
        else:
            L.append(f"_scheduler snapshot {cs.get('now')} · market_open={cs.get('market_open')} · {cs.get('min_to_open')} min to open · queue {cs.get('todo_by_window')}_")
            L.append("")
            L.append("| cat_side | sym_sides | 30D done | 365D ok | 365D neg/invalid | unverifiable | repair attempts | repaired ok | still failing |")
            L.append("|---|---|---|---|---|---|---|---|---|")
            for c, v in (cs.get("cats") or {}).items():
                L.append(f"| {c} | {v['sym_sides']} | {v['done30']} | {v['v365_ok']} | {v['v365_bad']} | {v['v365_unverifiable']} | {v['repair_attempts']} | {v['repair_ok']} | {len(v['failing'])} |")
            L.append("")
            for c, v in (cs.get("cats") or {}).items():
                if v["failing"]:
                    L.append(f"- **still failing {c}** ({len(v['failing'])}): " + ", ".join(f"`{x}`" for x in v["failing"][:25]))
            if cs.get("WARN"):
                L.append(f"- **NOT FINISHED BEFORE OPEN:** {cs['WARN']}")
            L.append(f"- stock chain jobs still unfinished: **{cs.get('stocks_chain_left', '?')}**")
            L.append("")
        # repair outcomes: what problem was diagnosed / what changed (from pulled chain verdict + repair JSONs)
        rows = []
        for f in sorted((dd / "chain").glob("*/repair_a*/*_365_cycle.json")) if (dd / "chain").exists() else []:
            v = jl(f) or {}
            r0 = (v.get("rounds") or [{}])[0]
            w365 = r0.get("w365") or {}
            problem = w365.get("invalid_reason") or (f"365D gain {w365.get('gain_pct')}" if (w365.get("gain_pct") or 0) <= 0 else "ok")
            steps = []
            for rf in sorted(f.parent.glob("repair/*/*_365_repair.json")):
                steps += [x.get("applied") for x in (jl(rf) or {}).get("steps", []) if x.get("applied")]
            last = (v.get("rounds") or [{}])[-1]
            rows.append((v.get("sym_side") or f.name, f.parent.name, problem, steps[:4], v.get("final_both_ok"), (last.get("w30") or {}).get("gain_pct"), (last.get("w365") or {}).get("gain_pct")))
        if rows:
            L.append("### REPAIR loop outcomes (diagnosed problem → what changed → result)")
            for ss, att, prob, steps, ok, g30, g365 in rows[:40]:
                L.append(f"- `{ss}` {att}: problem `{prob}` → changes {steps or 'none'} → both_ok={ok} (30D {g30} / 365D {g365})")
            L.append("")
        # live-faithful coverage + parity gaps
        lf = []
        p = dd / "live_faithful.jsonl"
        if p.exists():
            for l in p.read_text().splitlines():
                try:
                    lf.append(json.loads(l))
                except Exception:
                    pass
        L.append(f"### live-faithful (backtest_v12_engine) coverage: **{len(lf)}** sym_sides today")
        bad = [r for r in lf if r.get("parity_ok") is False]
        L.append(f"- parity gaps (vec vs live-faithful): **{len(bad)}**" + (" — " + ", ".join(f"`{r.get('sym_side')}` ({r.get('reason','')})" for r in bad[:15]) if bad else ""))
        L.append("")
        # promotions today
        prom = jl(base / "cat_side_promotions.json") or {}
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        L.append("### Promotions made today (bold defaults, cat_side_promotions.json)")
        n = 0
        for c, d in prom.items():
            for k, v in (d or {}).items():
                if str(v.get("at", "")).startswith(today):
                    n += 1
                    if n <= 40:
                        L.append(f"- {c} `{k}` → {v.get('value')} (avg_delta {v.get('avg_delta')}, {v.get('tab')})")
        L.append(f"_{n} promotions today_" if n else "_none today_")
        L.append("")
        if pl:
            L.append(f"_pipeline next stage: `{pl.get('next')}` · per_sym_apply: {pl.get('per_sym_apply')} · {pl.get('NEEDS_DECISION') or ''}_")
            L.append("")
    except Exception as e:
        L += [f"_chain section error: {e}_", ""]
    return L


def dead_section(levers, univ, day):
    L = []
    L.append("---")
    L.append("")
    L.append("## 3 · Switches & filters that produce no value — agent fix worklist")
    L.append("")
    L.append("_Aggregated to the LEVER level (base switch / filter, across all its values and all 4 cat_sides) "
             "from `SPREADSHEETS/v15_avg_delta_latest.xlsx` — the authoritative NO-LIES aggregate rebuilt from "
             "every recorded delta. A lever that moves nothing anywhere is a wiring gap or a no-op stub: both "
             "are real coverage/NO-LIES defects. Action every one in 3a/3b. 3c (honest losers) is condensed._")
    L.append("")
    buckets = {"NEVER_TESTED": [], "DEAD_NOOP": [], "NEVER_HELPS": []}
    for b, rec in levers.items():
        if rec["cls"] in buckets:
            buckets[rec["cls"]].append((b, rec))
    ok = sum(1 for r in levers.values() if r["cls"] == "OK")
    c = {k: len(v) for k, v in buckets.items()}
    L.append(f"Lever summary: **{ok}** produce ≥1 positive value · **{c['NEVER_TESTED']}** never tested · "
             f"**{c['DEAD_NOOP']}** DEAD no-op (moves nothing) · **{c['NEVER_HELPS']}** never help "
             f"(honest losers). Total levers seen: **{len(levers)}**.")
    L.append("")

    def table(items, header_extra=""):
        out = ["| kind | lever | cat_sides | values | n | pos | best avg | worst avg | fix |",
               "|---|---|---|---|---|---|---|---|---|"]
        for b, rec in items:
            out.append(f"| {rec['kind']} | `{b}` | {_cs_short(rec['cat_side_list'])} | {rec['n_values']} | "
                       f"{rec['tot_n']} | {rec['tot_pos']} | {fmt(rec['best_avg'], 3, plus=True)} | "
                       f"{fmt(rec['worst_avg'], 3, plus=True)} | {fix_hint(b, rec['kind'], rec['cls'])} |")
        return out

    # 3a NEVER TESTED
    L.append("### 3a · NEVER TESTED — no candidate delta in any cat_side (wiring / whitelist gap)")
    L.append("")
    items = sorted(buckets["NEVER_TESTED"], key=lambda kv: (kv[1]["kind"], kv[0]))
    if items:
        L.append("_Fix each: confirm the lever is in the wired∩live set (memory `v15_wired_switch_whitelist`), "
                 "its template row exists and is not grey, and v15_pilot emits candidate rows for its values; "
                 "a FILTER_TF must map to a switch in `data/opportune_filter_map.json`._")
        L.append("")
        L.extend(table(items))
    else:
        L.append("_None._")
    L.append("")

    # 3b DEAD NO-OP (headline)
    L.append(f"### 3b · DEAD NO-OP — tested but EVERY value moves gain < {NOOP_AVG}% (dead engine key / stub)")
    L.append("")
    items = sorted(buckets["DEAD_NOOP"], key=lambda kv: (kv[1]["kind"], -kv[1]["tot_n"]))
    if items:
        L.append(f"**{len(items)} levers are exercised by the sweep but change nothing** — a switch the optimiser "
                 f"can never use is wasted compute and a silent wiring bug. _Fix each: trace the real code path "
                 f"for the lever in `ez_manage.py`/`tradier_manage.py` (crypto vs stocks) AND "
                 f"`v12_quick_engine.py` — a live-wired lever MUST change trades. If it is a known stub farm "
                 f"(memory `filter_stub_farms_and_ghost_push`), wire it on all 4 surfaces "
                 f"(vec+ez+tradier+config), never a proxy (memory `switch_wiring_pattern_20260930`)._")
        L.append("")
        L.extend(table(items))
    else:
        L.append("_None — every tested lever moves gain somewhere._")
    L.append("")

    # 3c NEVER HELPS (condensed — not a table)
    L.append("### 3c · NEVER HELPS — real deltas but no value ever positive (honest losers → Stage-6 throttle)")
    L.append("")
    items = sorted(buckets["NEVER_HELPS"], key=lambda kv: kv[1]["best_avg"])
    if items:
        sw = [b for b, r in items if r["kind"] == "switch"]
        fl = [b for b, r in items if r["kind"] == "filter"]
        L.append(f"These **{len(items)}** levers ({len(sw)} switches, {len(fl)} filters) record real non-zero "
                 f"deltas but never a win in any cat_side. Not defects — they are correctly-measured losers. "
                 f"Action: throttle to the Stage-6 low cadence; do NOT promote; do NOT delete. Spot-check "
                 f"live==vec on one sym_side before throttling to rule out a sign/parity artefact.")
        L.append("")
        for label, names in (("switches", sw), ("filters", fl)):
            if names:
                L.append(f"- **{label} ({len(names)}):** " + ", ".join(f"`{n}`" for n in names[:60]) +
                         (" …" if len(names) > 60 else ""))
    else:
        L.append("_None._")
    L.append("")

    # 3d template universe absent entirely
    seen = {base_of(b).upper() for b in levers.keys()}
    missing = sorted(sw for sw in univ if sw not in seen)
    L.append("### 3d · In the TEMPLATE universe but ABSENT from avg_delta entirely")
    L.append("")
    if missing:
        L.append(f"These **{len(missing)}** switches are template rows but produced ZERO candidate deltas this "
                 f"round — the sweep never emitted a single value. Highest-priority wiring gap: confirm "
                 f"v15_pilot emits candidates and the row is not grey/invalid.")
        L.append("")
        for i in range(0, len(missing), 6):
            L.append("- " + ", ".join(f"`{s}`" for s in missing[i:i + 6]))
    else:
        L.append("_Every template switch produced at least one recorded delta this round._")
    L.append("")
    L.append("> **How to action (3a/3b/3d):** wiring fixes use the proven 4-surface pattern "
             "(vec + ez_manage + tradier_manage + config, never a proxy) — memory "
             "`switch_wiring_pattern_20260930`. For no-op stubs, trace the real code path before touching live "
             "(memory `filter_stub_farms_and_ghost_push`). Never delete a row (DAILY_OPTIMIZATION_PLAN Stage 6 "
             "throttles losers; it does not remove them).")
    L.append("")
    return L


def load_prev_snapshot(day):
    if not SNAP_DIR.exists():
        return None
    snaps = sorted(glob.glob(str(SNAP_DIR / "snapshot_*.json")))
    snaps = [s for s in snaps if f"snapshot_{day}.json" not in s]
    if not snaps:
        return None
    try:
        return json.loads(pathlib.Path(snaps[-1]).read_text())
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-hours", type=float, default=30.0,
                    help="how far back a progress JSON's mtime may be to count as this round (default 30h)")
    ap.add_argument("--no-remote", action="store_true", help="Mac-local lifecycle_pilot only; skip s1/s2/s5 ssh")
    args = ap.parse_args()
    out, report, n, dead = build(args.window_hours, use_remote=not args.no_remote)
    print(report)
    print(f"\n[written] {out}")
    print(f"[snapshot] {SNAP_DIR / ('snapshot_' + datetime.date.today().strftime('%Y%m%d') + '.json')}")
    print(f"[summary] {n} sym_sides in window · lever classes: " +
          ", ".join(f"{c}={sum(1 for r in dead.values() if r['cls']==c)}"
                    for c in ("NEVER_TESTED", "DEAD_NOOP", "NEVER_HELPS", "OK")))


if __name__ == "__main__":
    main()
