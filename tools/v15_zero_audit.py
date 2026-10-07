#!/usr/bin/env python3
"""v15_zero_audit — READ-ONLY audit of every 0 / repeated / synthetic-looking delta in the sweep (progress JSON + delta logs).

Phases (two-phase like v15_vector_delta_rebuild so ~60 MB delta logs never leave the workers):
  --emit-partial OUT.json [--progress-dir DIR]   (runs on each host; stdlib only)  per-sym_side raw stats
  --merge P1.json,P2.json,... [--out-dir DIR]    (Mac; also does the CODE TRACE via tools/_zero_ast.py) -> report.md + report.json
                                                  + data/vec_unwired.json (switch/filter keys with NO reachable vectorized read AND no
                                                  ledger movement in any finished sheet = NOT_WIRED_VEC; consumed by v15_pilot to
                                                  stop writing fake exact-0 cells for them)
Cell taxonomy (NO-LIES; 0 is only an exact 0.0 of a real calculation, None is never 0):
  real      non-zero delta from a calculation that is specific to the row
  zero      exact 0.0 of a calculation (ledger identical or gain identical)
  noop      yellow == naked switch delta (filter changed nothing on top of the switch) -> NOT a filter delta
  dup       same non-zero value repeated over rows whose switch is non-binding (naked==0 / running row): it is the filter's standalone
            effect copied onto unrelated switches -> counted ONCE per baseline epoch, never per row
Switch taxonomy (naked evals): moves ledger / never moves (gain identical AND trades identical to the baseline of its epoch).
"""
import argparse, collections, glob, json, os, pathlib, statistics, sys, datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")
EPS = 1e-9


def cat_side_of(symside):
    s = symside.upper()
    side = "LONG" if s.endswith("_LONG") else "SHORT" if s.endswith("_SHORT") else None
    base = s[: -(len(side) + 1)] if side else s
    return f"{'CRYPTO' if base.endswith(CRYPTO_SUFFIX) else 'STOCKS'}_{side}"


def parse_key(key):
    try:
        tab = key.split("!", 1)[0]
        sw, val = key.split(":", 1)[1].split("=", 1)
        return tab, sw.strip(), val.strip()
    except Exception:
        return None


def scan_symside(pj):
    """raw stats for ONE finished sym_side (progress json + its delta log)."""
    symside = os.path.basename(pj)[: -len("_v14_progress.json")]
    d = json.load(open(pj))
    done = d.get("done") or {}
    out = {"symside": symside, "cat": cat_side_of(symside), "rows": len(done), "row_delta": collections.Counter(), "sw": {}, "flt": {}}
    # ---- yellow taxonomy per epoch (cumulative_before)
    epoch_vals = collections.defaultdict(lambda: collections.defaultdict(list))   # epoch -> hdr -> [(key, value, nonbinding_row)]
    for key, e in done.items():
        if not isinstance(e, dict):
            continue
        dl = e.get("delta")
        out["row_delta"]["running" if e.get("is_running") else "sampled_out" if str(e.get("reason") or "").startswith("SKIPPED_SAMPLING") else "none" if dl is None else "zero" if dl == 0 else "nonzero"] += 1
        pk = parse_key(key)
        if not pk:
            continue
        nb = bool(e.get("is_running")) or e.get("naked_delta") == 0
        noop = set(e.get("noop_yellows") or [])
        bad = set((e.get("yellow_reasons") or {}).keys())
        for h, v in (e.get("yellows") or {}).items():
            if not isinstance(v, (int, float)) or h in bad:
                continue
            epoch_vals[round(float(e.get("cumulative_before") or 0), 6)][h].append((key, float(v), nb, h in noop))
    for ep, hv in epoch_vals.items():
        for h, lst in hv.items():
            fk = h.split("=", 1)[0]
            f = out["flt"].setdefault(fk, collections.Counter())
            seen_nb = set()
            for key, v, nb, noop in lst:
                f["cells"] += 1
                if v == 0.0:
                    f["zero"] += 1
                elif noop:
                    f["noop"] += 1
                elif nb:
                    rv = round(v, 9)
                    if rv in seen_nb:
                        f["dup"] += 1
                    else:
                        seen_nb.add(rv)
                        f["standalone_first"] += 1
                else:
                    f["real"] += 1
    # ---- naked evals from the delta log: does the switch move the ledger?
    dlp = pathlib.Path(pj).parent / "v15_delta_log" / f"{symside}_jump.jsonl"
    if dlp.exists():
        rows = []
        for l in open(dlp):
            try:
                r = json.loads(l)
            except Exception:
                continue
            if r.get("label") == "naked":
                rows.append(r)
        ep = collections.defaultdict(collections.Counter)
        for r in rows:
            if r.get("delta") == 0 and r.get("trades") is not None:
                ep[round(r["cum_before"], 6)][r["trades"]] += 1
        for r in rows:
            s = out["sw"].setdefault(r["switch"], collections.Counter())
            s["n"] += 1
            if r.get("delta") not in (0, None):
                s["moves"] += 1
            elif r.get("delta") == 0:
                m = ep[round(r["cum_before"], 6)].most_common(1)
                if m and r.get("trades") != m[0][0]:
                    s["moves"] += 1
                    s["moves_trades_only"] += 1
    return out


def is_modern(d):
    return any(isinstance(e, dict) and "ref_fp" in e for e in list((d.get("done") or {}).values())[:80])


def emit_partial(progress_dirs, outp, new_only=False, since=0.0):
    res = []
    for pdir in progress_dirs:
        for pj in sorted(glob.glob(os.path.join(os.path.expanduser(pdir), "*_v14_progress.json"))):
            try:
                d = json.load(open(pj))
                if d.get("final_gain") is None and not (since and len(d.get("done") or {}) >= 1500):
                    continue  # unfinished sheets only count in --since mode once >= 1500 rows are done
                if new_only and not is_modern(d):
                    continue
                if since and os.path.getmtime(pj) < since:
                    continue
                res.append(scan_symside(pj))
            except Exception as ex:
                print(f"[skip] {pj}: {ex}", file=sys.stderr)
    json.dump(res, open(outp, "w"), default=lambda o: dict(o))
    print(f"[partial] {outp} sym_sides={len(res)}")


def merge(paths, out_dir):
    sys.path.insert(0, str(ROOT / "tools"))
    sys.path.insert(0, str(ROOT))
    import _zero_ast as Z
    import dataclasses as dc
    import v12_quick_engine as V
    qf = {f.name for f in dc.fields(V.QuickConfig)}
    allss = []
    for p in paths:
        allss += json.load(open(p))
    sw = collections.defaultdict(lambda: collections.Counter())
    flt = collections.defaultdict(lambda: collections.Counter())
    cats = collections.Counter()
    rowstat = collections.Counter()
    for s in allss:
        cats[s["cat"]] += 1
        for k, v in s["row_delta"].items():
            rowstat[k] += v
        for name, c in s["sw"].items():
            for k, v in c.items():
                sw[name][k] += v
        for name, c in s["flt"].items():
            for k, v in c.items():
                flt[name][k] += v
    vec = Z.scan(["v12_quick_engine.py"] + sorted(glob.glob("vec_decisions/*.py")))
    tm = Z.scan(["tradier_manage.py"])
    ez = Z.scan(["ez_manage.py"])

    def reach(name):
        # reads inside reachable functions, plus module-level string constants of vec_decisions/* (declarative maps such as
        # generic_filter_tf.FILTER_TF_MAP that build_masks() consumes via getattr(config, name))
        return [x for x in vec.get(name, {"live": []})["live"] if x[2] != "<module>" or "vec_decisions" in x[0]]

    def live_reads(name):
        return len(tm.get(name, {"live": []})["live"]), len(ez.get(name, {"live": []})["live"])

    table = []
    for name, c in sw.items():
        r = reach(name)
        t, e = live_reads(name)
        moves = c["moves"]
        if name not in qf:
            cls = "NOT_A_QUICKCONFIG_FIELD"
        elif moves == 0 and not r:
            cls = "NOT_WIRED_VEC"
        elif moves == 0:
            cls = "WIRED_BUT_NEVER_BINDING"
        else:
            cls = "MOVES_LEDGER"
        table.append({"switch": name, "evals": c["n"], "moves": moves, "class": cls, "vec_reachable_reads": len(r), "vec_dead_reads": len(vec.get(name, {"dead": []})["dead"]), "tradier_reads": t, "ez_reads": e,
                      "where": [f"{a}:{b}({fn})" for a, b, fn in r[:3]]})
    table.sort(key=lambda x: (x["class"], -x["evals"]))
    ftable = []
    for name, c in flt.items():
        r = reach(name)
        t, e = live_reads(name)
        cells = c["cells"] or 1
        ftable.append({"filter": name, "cells": c["cells"], "zero%": round(100 * c["zero"] / cells, 1), "noop%": round(100 * c["noop"] / cells, 1), "dup%": round(100 * c["dup"] / cells, 1),
                       "real": c["real"], "standalone_first": c["standalone_first"], "vec_reachable_reads": len(r), "tradier_reads": t, "ez_reads": e,
                       "class": "NOT_A_QUICKCONFIG_FIELD" if name not in qf else ("NOT_WIRED_VEC" if not r and c["real"] + c["standalone_first"] == 0 else "OK")})
    ftable.sort(key=lambda x: -x["dup%"])
    unwired_sw = sorted(x["switch"] for x in table if x["class"] in ("NOT_WIRED_VEC", "NOT_A_QUICKCONFIG_FIELD"))
    unwired_f = sorted(x["filter"] for x in ftable if x["class"] in ("NOT_WIRED_VEC", "NOT_A_QUICKCONFIG_FIELD"))
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tot_naked = sum(c["n"] for c in sw.values())
    tot_moves = sum(c["moves"] for c in sw.values())
    fz = sum(c["zero"] for c in flt.values()); fn_ = sum(c["noop"] for c in flt.values()); fd = sum(c["dup"] for c in flt.values())
    fr = sum(c["real"] for c in flt.values()); fs = sum(c["standalone_first"] for c in flt.values()); fc = sum(c["cells"] for c in flt.values())
    rep = {"generated": datetime.datetime.utcnow().isoformat() + "Z", "sym_sides": dict(cats), "row_delta": dict(rowstat), "naked_evals": tot_naked, "naked_moves": tot_moves,
           "yellow_cells": fc, "yellow_zero": fz, "yellow_noop": fn_, "yellow_dup": fd, "yellow_real": fr, "yellow_standalone_first": fs,
           "switches": table, "filters": ftable, "unwired_switches": unwired_sw, "unwired_filters": unwired_f}
    (out / "report.json").write_text(json.dumps(rep, indent=1))
    md = [f"# v15 zero/synthetic audit {rep['generated']}", f"sym_sides {dict(cats)}", "",
          f"rows: {dict(rowstat)}", f"naked switch evals {tot_naked}: ledger moved {tot_moves} ({100*tot_moves/max(1,tot_naked):.1f}%) — {tot_naked-tot_moves} identical to baseline",
          f"yellow cells {fc}: zero {fz} ({100*fz/max(1,fc):.1f}%), noop(=naked) {fn_} ({100*fn_/max(1,fc):.1f}%), DUP(standalone copied onto unrelated rows) {fd} ({100*fd/max(1,fc):.1f}%), real {fr}, standalone-first {fs}", "",
          "## switches by class"]
    cc = collections.Counter(x["class"] for x in table)
    md += [f"- {k}: {v}" for k, v in cc.items()]
    md += ["", "## NOT_WIRED_VEC / NOT_A_QUICKCONFIG_FIELD switches (no reachable vectorized read, never moved the ledger) — live reads shown",
           "| switch | evals | tradier | ez | dead-code reads |", "|---|---|---|---|---|"]
    md += [f"| {x['switch']} | {x['evals']} | {x['tradier_reads']} | {x['ez_reads']} | {x['vec_dead_reads']} |" for x in table if x["class"] in ("NOT_WIRED_VEC", "NOT_A_QUICKCONFIG_FIELD")]
    md += ["", "## filters ranked by DUP% (same value copied across unrelated switch rows)", "| filter | cells | zero% | noop% | dup% | real | class |", "|---|---|---|---|---|---|---|"]
    md += [f"| {x['filter']} | {x['cells']} | {x['zero%']} | {x['noop%']} | {x['dup%']} | {x['real']} | {x['class']} |" for x in ftable[:60]]
    (out / "report.md").write_text("\n".join(md))
    (ROOT / "data").mkdir(exist_ok=True)
    up = ROOT / "data" / "vec_unwired.json"
    prev = json.loads(up.read_text()) if up.exists() else {}
    up.write_text(json.dumps({"_doc": "v15_zero_audit: keys with NO reachable vectorized read and no ledger movement in finished sheets. v15_pilot skips them (row/cell = None, reason NOT_WIRED_VEC) instead of writing a fake exact 0. Remove a key here when it is really wired.",
                              "generated": rep["generated"], "switches": sorted(set(unwired_sw) | set(prev.get("switches", []))), "filters": sorted(set(unwired_f) | set(prev.get("filters", []))), "switches_manual": prev.get("switches_manual", []), "filters_manual": prev.get("filters_manual", []), "sym_sides_seen": dict(cats)}, indent=1))
    print(f"[merge] {out}/report.md  unwired switches={len(unwired_sw)} filters={len(unwired_f)}  naked moves {tot_moves}/{tot_naked}  yellow zero {fz}/{fc} dup {fd} noop {fn_}")
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-partial")
    ap.add_argument("--progress-dir", action="append", default=[])
    ap.add_argument("--merge")
    ap.add_argument("--new-code-only", action="store_true", help="only sheets produced by pilot >= 47794652 (ref_fp present)")
    ap.add_argument("--since", type=float, default=0.0, help="epoch: only progress files modified after this (e.g. engine deploy time)")
    ap.add_argument("--by-type", action="store_true", help="append exact-0 share by filter type (census status + vec consumer) to report.md")
    ap.add_argument("--log", help="append a one-line cycle summary to this markdown file")
    ap.add_argument("--out-dir", default=str(ROOT / "data" / "zero_audit" / datetime.datetime.utcnow().strftime("%Y%m%d_%H%M")))
    a = ap.parse_args()
    if a.emit_partial:
        emit_partial(a.progress_dir or ["~/v15_run17_20260930/progress"], a.emit_partial, a.new_code_only, a.since)
    elif a.merge:
        rep_ = merge([p for p in a.merge.split(",") if p], a.out_dir)
        if a.by_type:
            import csv
            census = {}
            cp = ROOT / "data" / "filter_census" / "m1" / "census.csv"
            for r in csv.DictReader(open(cp)):
                census[r["key"]] = r["status"].split(" (")[0].split(" after")[0]
            grp = collections.defaultdict(lambda: collections.Counter())
            for x in rep_["filters"]:
                kind = "REAL(consumer in vec)" if x["vec_reachable_reads"] > 0 else "STUB_NO_CONSUMER(vec)"
                g = grp[(kind, census.get(x["filter"], "not-in-census"))]
                g["filters"] += 1; g["cells"] += x["cells"]
                g["zero"] += round(x["cells"] * x["zero%"] / 100); g["real"] += x["real"] + x["standalone_first"]; g["noop"] += round(x["cells"] * x["noop%"] / 100)
            lines = ["", "## exact-0 share by filter type (census status x vec consumer)", "| vec consumer | census | filters | cells | exact-0 % | real % | noop % |", "|---|---|---|---|---|---|---|"]
            for (kind, st), g in sorted(grp.items()):
                c_ = max(1, g["cells"]); lines.append(f"| {kind} | {st} | {g['filters']} | {g['cells']} | {100*g['zero']/c_:.1f} | {100*g['real']/c_:.2f} | {100*g['noop']/c_:.1f} |")
            (pathlib.Path(a.out_dir) / "report.md").open("a").write("\n".join(lines) + "\n")
            print("\n".join(lines))
        if a.log:
            nm, nn = rep_["naked_moves"], max(1, rep_["naked_evals"]); yc = max(1, rep_["yellow_cells"])
            line = (f"- {rep_['generated'][:16]}Z cycle: sym_sides {sum(rep_['sym_sides'].values())} {rep_['sym_sides']} | switch evals {rep_['naked_evals']}: ledger-changing {100*nm/nn:.1f}% | "
                    f"yellow cells {rep_['yellow_cells']}: real {100*(rep_['yellow_real']+rep_['yellow_standalone_first'])/yc:.2f}% zero {100*rep_['yellow_zero']/yc:.1f}% noop {100*rep_['yellow_noop']/yc:.1f}% copies-left {100*rep_['yellow_dup']/yc:.1f}% | unwired sw {len(rep_['unwired_switches'])}/flt {len(rep_['unwired_filters'])}\n")
            open(a.log, "a").write(line)
            (ROOT / "data" / "zero_audit" / "latest.json").write_text(json.dumps({"generated": rep_["generated"], "sym_sides": rep_["sym_sides"], "switch_evals": rep_["naked_evals"], "switch_ledger_changing_pct": round(100 * nm / nn, 2),
                "yellow_cells": rep_["yellow_cells"], "yellow_real_pct": round(100 * (rep_["yellow_real"] + rep_["yellow_standalone_first"]) / yc, 3), "yellow_zero_pct": round(100 * rep_["yellow_zero"] / yc, 2),
                "yellow_noop_pct": round(100 * rep_["yellow_noop"] / yc, 2), "yellow_copies_left_pct": round(100 * rep_["yellow_dup"] / yc, 2), "unwired_switches": len(rep_["unwired_switches"]), "report": a.out_dir, "line": line.strip()}, indent=1))
    else:
        ap.error("need --emit-partial or --merge")


if __name__ == "__main__":
    main()
