#!/usr/bin/env python3
"""v15_row_coverage — ROW COVERAGE GATE (MASTER_PLAN_20261001 §4). Goal metric: 100% of rows OK.

Per row of the FINAL templates (SPREADSHEETS/TEMPLATE_{CRYPTO,STOCKS}_{LONG,SHORT}.xlsx, read-only; SPREADSHEETS/TEMPLATE_FINAL_NORM is what runs) —
  * white / orange rows  (tab, SWITCH=cand)   evidence = run19 progress 'done' entries (delta, reason, is_running, dep_forced)
  * yellow filter cells  (tab, FILTER=opt)    evidence = progress 'yellows' (+ Agent Y discovery data/yellow_discovery/<date>/fd_*/evidence_*.json as fallback)
per key: n_sym_calculated, n_nonzero, n_zero, n_noop, avg_delta (mean over REAL calculations only — a not-calculated cell is NEVER 0),
status  OK | NOT_WIRED_VEC | ZERO_EVERYWHERE | DEP_MISSING | NEEDS_LIVE | NEVER_RUN, reason, owner tab-group N1..N5.
NONE-vs-0 RULE: calculated = a numeric delta from an engine eval (exact 0.0 counts as calculated-zero); None / ZERO_TRADES / INVALID / skipped /
running-default rows are NOT calculated. noop (filter == naked switch) and dup (blanked copies) are no information and never counted as nonzero.
A bold is_default row IS the baseline (delta vs itself): status OK, reason DEFAULT_BASELINE (reported separately in the summary).

Modes:  --emit-partial OUT [--progress-dir D ...]   host side (stdlib): aggregate progress JSON
        --run                                        Mac: pull partials from hosts, build data/rowcoverage/{latest.csv,latest.md,worklist_N*.csv}, log line -> data/wiring/LOG.md
"""
import argparse, collections, csv, datetime, glob, hashlib, json, os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "rowcoverage"
CATS = ["CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"]
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")
EPS = 1e-9
SKIP_REASONS = ("DEAD_VEC", "LIVE_ONLY", "SKIPPED_GREY", "NO_CANDIDATE", "NOT_IN_CONFIG", "INVENTED_ALT", "NOT_WIRED_VEC", "ZERO_TRADES", "SKIPPED_SAMPLING")
TABS = ["ENTRY_REVERSAL_BOUNCE", "STDEV_SLOPE_SIZING", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE",
        "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
OWNER = {"ENTRY_REVERSAL_BOUNCE": "N1", "ENTRY_BREAKOUT_CHANNEL": "N1", "ENTRY_CONFIRMATION_GATES": "N1", "EXIT_STRUCTURAL": "N2", "EXIT_VELOCITY": "N2",
         "REENTRY_WINDOWED": "N3", "REENTRY_ADAPTIVE": "N3", "AUGMENT_TREND": "N4", "AUGMENT_RISK_SIZING": "N4", "REDUCE_PROFIT_LOCK": "N4", "REDUCE_SIGNAL_RATER": "N4",
         "GLOBAL_RISK_GATES": "N5", "STDEV_SLOPE_SIZING": "N5"}
HOSTS = [("s1-pub", "~/binance-sandbox"), ("s2", "~/binance-sandbox"), ("s5", "~/binance-sandbox")]


def cat_side_of(ss):
    s = ss.upper()
    side = "LONG" if s.endswith("_LONG") else "SHORT" if s.endswith("_SHORT") else None
    base = s[: -(len(side) + 1)] if side else s
    return f"{'CRYPTO' if base.endswith(CRYPTO_SUFFIX) else 'STOCKS'}_{side}" if side else None


def nv(v):
    s = str(v).strip().lower()
    try:
        return format(float(s), ".10g")
    except ValueError:
        return s


# ───────────────────────────── host side ─────────────────────────────
def emit_partial(dirs, outp):
    """per cat_side: rows[(tab,sw,val)] and yel[(tab,header)] per-sym aggregates."""
    rows = collections.defaultdict(lambda: collections.defaultdict(lambda: {"syms": {}, "reasons": collections.Counter(), "dep": set(), "running": 0}))
    yel = collections.defaultdict(lambda: collections.defaultdict(lambda: {"syms": {}}))
    nfiles = 0
    for d in dirs:
        for pj in sorted(glob.glob(os.path.join(os.path.expanduser(d), "*_v14_progress.json"))):
            ss = os.path.basename(pj)[: -len("_v14_progress.json")]
            cs = cat_side_of(ss)
            if not cs:
                continue
            try:
                done = json.load(open(pj)).get("done") or {}
            except Exception:
                continue
            nfiles += 1
            for key, e in done.items():
                if not isinstance(e, dict):
                    continue
                try:
                    tab = key.split("!", 1)[0].strip()
                    sw, val = key.split(":", 1)[1].split("=", 1)
                except Exception:
                    continue
                rk = f"{tab}\t{sw.strip()}\t{nv(val)}"
                R = rows[cs][rk]
                reason = str(e.get("reason") or "")
                if reason.startswith(SKIP_REASONS):
                    R["reasons"]["SKIPPED_SAMPLING" if reason.startswith("SKIPPED_SAMPLING") else reason.split(":")[0][:24]] += 1
                elif e.get("is_running"):
                    R["running"] += 1
                elif isinstance(e.get("delta"), (int, float)) and not e.get("delta_invalid"):
                    R["syms"][ss] = float(e["delta"]);
                elif e.get("delta_invalid"):
                    R["reasons"]["INVALID"] += 1
                else:
                    R["reasons"][reason.split(":")[0][:24] or "NO_RESULT"] += 1
                df = e.get("dep_forced") or {}
                for lab in ("naked", "yellow"):
                    for m in ((df.get("by_eval") or {}).get(lab) or {}):
                        R["dep"].add(m)
                bad = set((e.get("yellow_reasons") or {}).keys()); noop = set(e.get("noop_yellows") or [])
                for hdr, v in (e.get("yellows") or {}).items():
                    if not isinstance(v, (int, float)) or hdr in bad:
                        continue
                    Y = yel[cs][f"{tab}\t{hdr}"]["syms"].setdefault(ss, {"calc": 0, "nz": 0, "zero": 0, "noop": 0, "sum": 0.0, "n": 0})
                    Y["calc"] += 1
                    if hdr in noop:
                        Y["noop"] += 1
                    else:
                        Y["n"] += 1; Y["sum"] += float(v)
                        if abs(v) > EPS: Y["nz"] += 1
                        else: Y["zero"] += 1
    rows_out = {cs: {k: {"syms": v["syms"], "reasons": dict(v["reasons"]), "dep": sorted(v["dep"]), "running": v["running"]} for k, v in r.items()} for cs, r in rows.items()}
    yel_out = {cs: {k: {"syms": v["syms"]} for k, v in y.items()} for cs, y in yel.items()}
    eng = ""
    try:
        eng = hashlib.md5(open(os.path.expanduser("~/binance-sandbox/v12_quick_engine.py"), "rb").read()).hexdigest()[:8]
    except Exception:
        pass
    json.dump({"rows": rows_out, "yel": yel_out, "files": nfiles, "engine_md5": eng}, open(outp, "w"))
    print(f"[partial] {outp} files={nfiles} engine={eng}")


# ───────────────────────────── Mac side ─────────────────────────────
def parse_template(path):
    import openpyxl
    wb = openpyxl.load_workbook(str(path))
    out = {}
    for tab in TABS:
        if tab not in wb.sheetnames:
            continue
        ws = wb[tab]
        hdr = {}
        for c in range(1, ws.max_column + 1):
            v = ws.cell(row=2, column=c).value
            if isinstance(v, str):
                hdr[c] = v.strip()
        c_isd = next((c for c, h in hdr.items() if h.lower() == "is_default"), None)
        filt_cols = [h for c, h in hdr.items() if "=" in h]
        rows = []
        for r in range(3, ws.max_row + 1):
            a, b = ws.cell(row=r, column=1).value, ws.cell(row=r, column=2).value
            if a in (None, ""):
                continue
            fg = ws.cell(row=r, column=1).fill.fgColor.rgb if ws.cell(row=r, column=1).fill and ws.cell(row=r, column=1).fill.fgColor else ""
            orange = "FFE699" in str(fg) or "FFC000" in str(fg) or "F4B084" in str(fg)
            isd = str(ws.cell(row=r, column=c_isd).value or "").upper() == "YES" if c_isd else bool(ws.cell(row=r, column=2).font and ws.cell(row=r, column=2).font.b)
            rows.append((str(a).strip(), str(b).strip() if b is not None else "", orange, isd))
        out[tab] = {"rows": rows, "filters": filt_cols}
    return out


def pull(hosts):
    parts = []
    for h, root in hosts:
        try:
            pdir = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new", h, "cat ~/v15_current_progress_dir.txt"], capture_output=True, text=True, timeout=40).stdout.strip()
            dirs = {pdir, "~/v15_run19_20261001/progress"} - {""}
            dargs = " ".join(f"--progress-dir {d}" for d in sorted(dirs))
            subprocess.run(["rsync", "-az", "-e", "ssh -S none -o StrictHostKeyChecking=accept-new", str(ROOT / "tools" / "v15_row_coverage.py"), f"{h}:{root}/tools/"], capture_output=True, timeout=60)
            r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", h, f"cd {root} && python3 tools/v15_row_coverage.py {dargs} --emit-partial /tmp/rowcov_partial.json"], capture_output=True, text=True, timeout=600)
            dst = OUT / "partials" / f"{h}.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["scp", "-q", f"{h}:/tmp/rowcov_partial.json", str(dst)], check=True, timeout=120)
            parts.append((h, json.load(open(dst))))
        except Exception as ex:
            print(f"[pull] {h} failed: {str(ex)[:100]}", flush=True)
    return parts


def fd_evidence():
    """Agent Y filter discovery: {cat_side: {header: {n, effect_syms, avg}}} (latest dated dir)."""
    out = {cs: {} for cs in CATS}
    days = sorted(glob.glob(str(ROOT / "data" / "yellow_discovery" / "2*")))
    days = [d for d in days if "OLD" not in d]
    if not days:
        return out
    for f in glob.glob(days[-1] + "/fd_*/evidence_*.json"):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        cs = d.get("cat_side")
        for h, v in (d.get("filters_stage_A") or {}).items():
            if cs in out and isinstance(v, dict):
                cur = out[cs].setdefault(h, {"n": 0, "effect_syms": 0, "sum": 0.0, "k": 0, "notwired": 0})
                cur["n"] += int(v.get("n") or 0); cur["effect_syms"] = max(cur["effect_syms"], int(v.get("effect_syms") or 0)); cur["notwired"] += int(v.get("notwired_syms") or 0)
                if isinstance(v.get("avg_delta_vs_base"), (int, float)):
                    cur["sum"] += v["avg_delta_vs_base"]; cur["k"] += 1
    return out


def importance(tab, kind, switch):
    grp = {"N1": 0, "N3": 1, "N2": 2, "N4": 3, "N5": 4}[OWNER.get(tab, "N5")]
    tok = 0 if any(t in switch.upper() for t in ("GATE", "FILTER", "ENTRY", "REENTRY", "VETO", "BLOCK")) else 1
    return (grp, tok, 0 if kind == "yellow" else 1)


STATUS_ORDER = {"NOT_WIRED_VEC": 0, "DEP_MISSING": 1, "NEVER_RUN": 2, "ZERO_EVERYWHERE": 3, "NEEDS_LIVE": 4}


def run():
    sys.path.insert(0, str(ROOT))
    import v15_pilot as P
    deps = json.load(open(ROOT / "data" / "switch_dependencies.json")).get("masters", {}) if (ROOT / "data" / "switch_dependencies.json").exists() else {}
    unw = json.load(open(ROOT / "data" / "vec_unwired.json")) if (ROOT / "data" / "vec_unwired.json").exists() else {}
    unw_all = set(unw.get("switches", [])) | set(unw.get("switches_manual", [])) | set(unw.get("filters", [])) | set(unw.get("filters_manual", []))
    parts = pull(HOSTS)
    OUT.mkdir(parents=True, exist_ok=True)
    rows_ev = {cs: collections.defaultdict(lambda: {"syms": {}, "reasons": collections.Counter(), "dep": set(), "running": 0}) for cs in CATS}
    yel_ev = {cs: collections.defaultdict(dict) for cs in CATS}
    engines = {}
    for h, p in parts:
        engines[h] = p.get("engine_md5")
        for cs, rr in (p.get("rows") or {}).items():
            for k, v in rr.items():
                a = rows_ev[cs][k]
                a["syms"].update(v["syms"]); a["running"] += v["running"]; a["dep"] |= set(v["dep"])
                for r_, n_ in v["reasons"].items():
                    a["reasons"][r_] += n_
        for cs, yy in (p.get("yel") or {}).items():
            for k, v in yy.items():
                yel_ev[cs][k].update(v["syms"])
    fd = fd_evidence()
    final = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"
    allrows = []
    for cs in CATS:
        tp = final / f"TEMPLATE_{cs}.xlsx"
        if not tp.exists():
            tp = ROOT / "SPREADSHEETS" / f"TEMPLATE_{cs}.xlsx"
        tpl = parse_template(tp)
        for tab, td in tpl.items():
            owner = OWNER.get(tab, "N5")
            seen = set()
            for a, b, orange, isd in td["rows"]:
                k = f"{tab}\t{a}\t{nv(b)}"
                if k in seen:
                    continue
                seen.add(k)
                ev = rows_ev[cs].get(k)
                syms = ev["syms"] if ev else {}
                n_calc = len(syms); nz = sum(1 for v in syms.values() if abs(v) > EPS); nzero = n_calc - nz
                avg = sum(syms.values()) / n_calc if n_calc else None
                block = P.promotion_block_reason(a, tab)
                status, reason = "OK", ""
                if a in unw_all:
                    status, reason = "NOT_WIRED_VEC", "in vec_unwired.json: no reachable vectorized read"
                elif ev and ev["reasons"].get("NOT_WIRED_VEC") and not n_calc:
                    status, reason = "NOT_WIRED_VEC", "pilot skipped: NOT_WIRED_VEC"
                elif nz:
                    status = "OK"
                elif isd and not n_calc:
                    status, reason = "OK", "DEFAULT_BASELINE (bold default = the baseline)"
                elif block.startswith(("LIVE_ONLY", "LIVE_DEAD_KEY", "DEAD_VEC")):
                    status, reason = "NEEDS_LIVE", block[:80]
                elif n_calc and not nz:
                    masters = deps.get(a) or []
                    if masters and not (ev and ev["dep"]):
                        status, reason = "DEP_MISSING", f"needs master {masters} (no dep_forced observed)"
                    else:
                        status, reason = "ZERO_EVERYWHERE", f"n={n_calc} calculated, all exact 0.0"
                elif not n_calc and ev and ev["reasons"].get("SKIPPED_SAMPLING") and set(ev["reasons"]) <= {"SKIPPED_SAMPLING"}:
                    status, reason = "OK", "SAMPLED_OUT (not calculated by choice: low POS_SYM, earlier rounds' value stands)"
                elif not n_calc:
                    why = dict(ev["reasons"]) if ev else {}
                    masters = deps.get(a) or []
                    status = "DEP_MISSING" if masters and not (ev and ev["dep"]) else "NEVER_RUN"
                    reason = (f"needs master {masters}; " if status == "DEP_MISSING" else "") + (f"not calculated: {why}" if why else "no evidence yet")
                allrows.append({"template": cs, "tab": tab, "kind": "orange" if orange else "switch", "switch": a, "cand": b, "is_default": int(isd), "n_sym_calculated": n_calc, "n_nonzero": nz,
                                "n_zero": nzero, "n_noop": 0, "avg_delta": "" if avg is None else round(avg, 6), "status": status, "reason": reason, "owner": owner, "evidence": "run19" if n_calc else ("none" if not ev else ",".join(f"{k}:{v}" for k, v in list(ev["reasons"].items())[:2]) or "running"),
                                "dep_masters": "|".join(deps.get(a) or [])})
            for h in td["filters"]:
                f, o = h.split("=", 1)
                k = f"{tab}\t{h}"
                sy = yel_ev[cs].get(k, {})
                n_calc = len(sy); nz = sum(1 for v in sy.values() if v["nz"] > 0); nzero = sum(1 for v in sy.values() if v["zero"] > 0 and v["nz"] == 0)
                noop = sum(1 for v in sy.values() if v["noop"] > 0 and v["n"] == 0)
                tot_n = sum(v["n"] for v in sy.values()); avg = (sum(v["sum"] for v in sy.values()) / tot_n) if tot_n else None
                src = "run19"
                if not n_calc and h in fd[cs]:
                    e = fd[cs][h]; n_calc = e["n"]; nz = e["effect_syms"]; nzero = max(0, n_calc - nz); avg = (e["sum"] / e["k"]) if e["k"] else None; src = "yellow_discovery"
                    if e["notwired"] and not nz:
                        n_calc = 0
                status, reason = "OK", ""
                if f in unw_all:
                    status, reason = "NOT_WIRED_VEC", "filter in vec_unwired.json"
                elif nz:
                    status = "OK"
                elif P.promotion_block_reason(f, tab).startswith(("LIVE_ONLY", "LIVE_DEAD_KEY", "DEAD_VEC")):
                    status, reason = "NEEDS_LIVE", P.promotion_block_reason(f, tab)[:80]
                elif n_calc:
                    masters = deps.get(f) or []
                    status, reason = ("DEP_MISSING", f"needs master {masters}") if masters else ("ZERO_EVERYWHERE", f"n={n_calc} calculated, no non-noop effect")
                else:
                    status, reason = "NEVER_RUN", "no yellow evidence yet"
                allrows.append({"template": cs, "tab": tab, "kind": "yellow", "switch": f, "cand": o, "is_default": 0, "n_sym_calculated": n_calc, "n_nonzero": nz, "n_zero": nzero, "n_noop": noop,
                                "avg_delta": "" if avg is None else round(avg, 6), "status": status, "reason": reason, "owner": owner, "evidence": src, "dep_masters": "|".join(deps.get(f) or [])})
    cols = list(allrows[0].keys())
    tmp = OUT / "latest.csv.tmp"
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(allrows)
    tmp.replace(OUT / "latest.csv")
    # summary
    cnt = collections.defaultdict(collections.Counter)
    for r in allrows:
        cnt[(r["template"], r["tab"])][r["status"]] += 1
        cnt[(r["template"], "ALL")][r["status"]] += 1
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%MZ")
    md = [f"# Row coverage {now}", f"engines on hosts: {engines}; evidence sym_sides: " + ", ".join(f"{h}:{p.get('files')}" for h, p in parts), "",
          "| template | tab | rows | OK | NOT_WIRED_VEC | DEP_MISSING | NEVER_RUN | ZERO_EVERYWHERE | NEEDS_LIVE | %OK |", "|---|---|---|---|---|---|---|---|---|---|"]
    tot = collections.Counter(); line = []
    for (cs, tab), c in sorted(cnt.items(), key=lambda x: (x[0][0], x[0][1] != "ALL", x[0][1])):
        n = sum(c.values())
        md.append(f"| {cs} | {tab} | {n} | {c['OK']} | {c['NOT_WIRED_VEC']} | {c['DEP_MISSING']} | {c['NEVER_RUN']} | {c['ZERO_EVERYWHERE']} | {c['NEEDS_LIVE']} | {100*c['OK']/max(1,n):.1f} |")
        if tab == "ALL":
            line.append(f"{cs} {100*c['OK']/max(1,n):.1f}% ({c['OK']}/{n})"); tot.update(c)
    base = sum(1 for r in allrows if r["reason"].startswith("DEFAULT_BASELINE"))
    (OUT / "latest.md").write_text("\n".join(md) + f"\n\nrows counted OK as DEFAULT_BASELINE (bold default, no own delta): {base}\n")
    for g in ("N1", "N2", "N3", "N4", "N5"):
        wl = sorted([r for r in allrows if r["owner"] == g and r["status"] != "OK"], key=lambda r: (importance(r["tab"], r["kind"], r["switch"]), STATUS_ORDER.get(r["status"], 9), r["tab"], r["switch"]))
        with open(OUT / f"worklist_{g}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(wl)
    n_all = sum(tot.values())
    msg = f"- {now} ROWCOV: " + " | ".join(line) + f" | total OK {tot['OK']}/{n_all} ({100*tot['OK']/max(1,n_all):.1f}%); NWV {tot['NOT_WIRED_VEC']} DEP {tot['DEP_MISSING']} NEVER {tot['NEVER_RUN']} ZERO {tot['ZERO_EVERYWHERE']} LIVE {tot['NEEDS_LIVE']}\n"
    (ROOT / "data" / "wiring").mkdir(exist_ok=True)
    with open(ROOT / "data" / "wiring" / "LOG.md", "a") as fh:
        fh.write(msg)
    with open(OUT / "history.csv", "a") as fh:
        fh.write(f"{now},{tot['OK']},{n_all},{tot['NOT_WIRED_VEC']},{tot['DEP_MISSING']},{tot['NEVER_RUN']},{tot['ZERO_EVERYWHERE']},{tot['NEEDS_LIVE']}\n")
    print(msg.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-partial"); ap.add_argument("--progress-dir", action="append", default=[]); ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if a.emit_partial:
        emit_partial(a.progress_dir or ["~/v15_run19_20261001/progress"], a.emit_partial)
    elif a.run:
        run()
    else:
        ap.error("need --emit-partial or --run")


if __name__ == "__main__":
    main()
