#!/usr/bin/env python3
"""v15_vector_delta_rebuild — aggregate REAL per-row VECTOR_DELTAs across sym_sides into a v15 workbook.

New version of v15_avg_delta_rebuild (USER 2026-09-30): the aggregate is the mean of the per-sym_side
VECTOR_DELTA (progress-JSON 'done' 'delta' = §56.0 real joint delta vs latest baseline) and is destined for
the TEMPLATE_* VECTOR_DELTA column (tools/v15_vector_delta_apply.py), with rows reordered by it.

Aggregation rule = identical to v15_avg_delta_rebuild (NO-LIES §19): only real recorded deltas; honest 0s
(running default row) stay 0; grey/invalid/dead/live-only candidates never enter; yellows counted per header.

Because the freshest progress JSON for each sym_side is spread across s1/s2/s5, this runs in two phases so
~900 MB of JSON never leaves the workers:
  1. --emit-partial : on each host, aggregate that host's file list (--files manifest.txt) -> a tiny partial
     JSON of {cat_side: {"switch\tNAME": [deltas], "filter\tHDR": [deltas]}, "_symsides": {cat_side:[...]}}.
  2. --merge p1,p2,... : merge the partials (concatenate the delta lists per key) -> the 4-tab workbook.
Convenience: --progress-dir DIR (repeatable) aggregates locally (newest file wins per sym_side) and writes
the workbook in one shot, same as the legacy tool.
"""

import argparse, glob, json, os, pathlib, re, statistics

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "SPREADSHEETS" / "v15_avg_delta"
LATEST = ROOT / "SPREADSHEETS" / "v15_avg_delta_latest.xlsx"
COMPAT_LATEST = (
    ROOT / "SPREADSHEETS" / "v15_vector_delta_latest.xlsx"
)  # same content; v15_daily_template_update.py default --agg
CAT_SIDES = ["CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"]
EPS = 1e-9
SKIP_REASONS = (
    "DEAD_VEC",
    "LIVE_ONLY",
    "SKIPPED_GREY",
    "NO_CANDIDATE",
    "NOT_IN_CONFIG",
    "INVENTED_ALT",
    "NOT_WIRED_VEC",
    "ZERO_TRADES",
)
# 2026-10-06 short-zero fix: the pilot RECORDS the delta of a candidate that failed its own validity gates (BIBLE TIM 20-80 "G/F/Y still
# recorded; only the promotion is blocked", trade floor, TIM/DD vomit) with delta_invalid=False. Counting those as promotion evidence
# promoted ~35 CRYPTO_SHORT entry gates at 17:18Z (WICK_REJECT 10/10 pos, all TIM-guard) whose joint stack zeroed every crypto short.
# Only the switch delta is dropped (it is not promotable evidence); the row's yellows keep their own per-header validity handling.
INVALID_CANDIDATE_RE = re.compile(
    r"^(TIM-guard|trades \d+ < \d+|fewer than \d+ completed trades|TIM [\d.]+% >|DD [\d.]+% >|prepare failed)"
)


def cat_side_of(symside):
    s = symside.upper()
    side = "LONG" if s.endswith("_LONG") else "SHORT" if s.endswith("_SHORT") else None
    if not side:
        return None
    base = s[: -(len(side) + 1)]
    venue = (
        "CRYPTO"
        if base.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI"))
        else "STOCKS"
    )
    return f"{venue}_{side}"


def switch_name(key):
    # kept for compatibility; prefer parse_key which also returns the tab
    r = parse_key(key)
    return r[1] if r else None


def parse_key(key):
    """done key 'TAB!row:SWITCH=value' -> (TAB, 'SWITCH=value'). Tab matters: the SAME switch is a row in
    up to ~15 tabs and each tab-row is its own test context, so deltas are aggregated PER (tab, switch).
    """
    try:
        tab = key.split("!", 1)[0].strip()
        sw, val = key.split(":", 1)[1].split("=", 1)
        return tab, f"{sw.strip()}={val.strip()}"
    except Exception:
        return None


def add_file(path, agg, seen):
    """Fold ONE progress JSON into agg. USER 2026-09-30: one value per sym_side per (tab, kind, name) — the
    BEST (max) of that key's occurrences in this file — so n == number of sym_sides, never a per-cell count.
    Key = 'TAB\\tkind\\tname'. Same NO-LIES exclusions as v15_avg_delta_rebuild."""
    symside = os.path.basename(path)[: -len("_v14_progress.json")]
    cs = cat_side_of(symside)
    if cs is None:
        return
    try:
        done = (json.load(open(path)) or {}).get("done", {})
    except Exception:
        return
    if not isinstance(done, dict) or not done:
        return
    best = {}  # (tab, kind, name) -> best value within THIS sym_side's file
    # NONE-vs-ZERO RULE (USER 2026-09-30, v15_zero_audit): nothing that was not a real, informative calculation enters an average.
    #  * a running-default row is the baseline itself (delta vs itself) -> not a measurement, never 0.0
    #  * an exact-0.0 switch row = the flip left the ledger identical (non-binding) -> no information, not counted
    #  * noop yellows (filter changed nothing on top of the row) are not filter deltas
    #  * legacy files (no ref_fp): the same non-zero yellow value repeated over non-binding rows of one baseline epoch is the filter's
    #    STANDALONE effect copied onto unrelated switches -> counted once (modern pilots blank those cells themselves)
    modern = any(
        isinstance(_e, dict) and "ref_fp" in _e for _e in list(done.values())[:50]
    )
    seen_nb = set()
    for key, e in done.items():
        if not isinstance(e, dict):
            continue
        pk = parse_key(key)
        if not pk:
            continue
        tab, sw = pk
        if str(e.get("reason") or "").startswith(SKIP_REASONS):
            continue
        val = None
        if (
            isinstance(e.get("delta"), (int, float))
            and not e.get("delta_invalid")
            and not e.get("is_running")
            and not INVALID_CANDIDATE_RE.match(str(e.get("reason") or ""))
        ):
            val = float(e["delta"])
            if abs(val) < 1e-12 and not (modern and e.get("naked_binding") is True):
                val = None  # exact 0.0 = ledger identical to the baseline: non-binding, carries no information
        if val is not None:
            k = (tab, "switch", sw)
            best[k] = val if k not in best else max(best[k], val)
        _bad = e.get("yellow_reasons") or {}
        _noop = set(e.get("noop_yellows") or [])
        _nb_row = (
            bool(e.get("is_running"))
            or e.get("naked_delta") == 0
            or e.get("naked_binding") is False
        )
        _ep = round(float(e.get("cumulative_before") or 0.0), 6)
        for hdr, d in (e.get("yellows") or {}).items():
            if not isinstance(d, (int, float)) or hdr in _bad or hdr in _noop:
                continue
            if not modern and _nb_row and d != 0:
                _sk = (_ep, hdr, round(float(d), 9))
                if _sk in seen_nb:
                    continue
                seen_nb.add(_sk)
            k = (tab, "filter", hdr)
            best[k] = float(d) if k not in best else max(best[k], float(d))
    if not best:
        return
    seen[cs].add(symside)
    for (tab, kind, name), v in best.items():
        agg[cs].setdefault(f"{tab}\t{kind}\t{name}", []).append(v)


def scan_file(path):
    """Score one progress JSON for 'best result per sym_side' selection.
    real = count of done entries with a real (non-invalid, non-skipped) float delta OR a running-default 0;
    is56 = §56.0-era (sequential fill) marker; returns (symside, real, is56, final_gain_present, done_n).
    """
    symside = os.path.basename(path)[: -len("_v14_progress.json")]
    try:
        d = json.load(open(path)) or {}
    except Exception:
        return symside, 0, 0, 0, 0
    done = d.get("done", {})
    if not isinstance(done, dict):
        return symside, 0, 0, 0, 0
    is56 = 1 if ("e3_default_base" in d or "initial_baseline_gain" in d) else 0
    fg = 1 if d.get("final_gain") is not None else 0
    real = 0
    for e in done.values():
        if not isinstance(e, dict):
            continue
        if str(e.get("reason") or "").startswith(SKIP_REASONS):
            continue
        if (
            isinstance(e.get("delta"), (int, float))
            and not e.get("delta_invalid")
            and not e.get("is_running")
            and abs(float(e["delta"])) > 1e-12
            and not INVALID_CANDIDATE_RE.match(str(e.get("reason") or ""))
        ):
            real += 1
    return symside, real, is56, fg, len(done)


FIX_CUTOFF_EPOCH = 1790582400  # 2026-09-28T08:00Z — earlier progress JSONs carry the abs()-flipped baseline (memory v15_baseline_sign_corruption)


SELECT_FLAGS = (
    {}
)  # symside -> 'clean'|'contaminated' (only filled when select_latest is given is_clean; EVID 2026-10-01)


DONE_COMPLETE_N = 2000  # 2026-10-09: a sweep that evaluated >= this many cells is complete — its zeros are verdicts, not gaps; exempt from the real-cell floor (run29 converged boards lost to stale exploratory files otherwise)


def select_latest(rows, min_frac=0.5, is_clean=None):
    """rows = [(symside, real, is56, fg, mtime, path, host, done_n)]. Per sym_side: LATEST (newest mtime) §56.0-era file written after the
    sign-fix cutoff whose real-cell count is >= min_frac * the best real-cell count of that sym_side's valid files (an
    incomplete/garbage newest file falls back to the next newest). Files with done_n >= DONE_COMPLETE_N are exempt from the
    floor (a converged complete sweep has few nonzero deltas by nature). Returns {symside: row}.
    is_clean(row)->bool (optional): prefer the newest CLEAN file over contaminated ones (look-ahead crypto results fall back only when no clean file reaches the floor); SELECT_FLAGS records which was used.
    """
    by = {}
    for r in rows:
        by.setdefault(r[0], []).append(r)
    out = {}
    for ss, lst in by.items():
        ok = [r for r in lst if r[2] and r[4] >= FIX_CUTOFF_EPOCH and r[1] > 0]
        if not ok:
            continue
        floor = min_frac * max(r[1] for r in ok)
        ok = [
            r for r in ok if r[1] >= floor or (len(r) > 7 and r[7] >= DONE_COMPLETE_N)
        ]
        if is_clean is not None:
            cl = [r for r in ok if is_clean(r)]
            if cl:
                out[ss] = max(cl, key=lambda r: r[4])
                SELECT_FLAGS[ss] = "clean"
            else:
                out[ss] = max(ok, key=lambda r: r[4])
                SELECT_FLAGS[ss] = "contaminated" if not is_clean(out[ss]) else "clean"
            continue
        out[ss] = max(ok, key=lambda r: r[4])
    return out


def new_agg():
    return {cs: {} for cs in CAT_SIDES}, {cs: set() for cs in CAT_SIDES}


def collect_files(files):
    agg, seen = new_agg()
    for p in files:
        add_file(p, agg, seen)
    return agg, seen


def merge_partials(paths):
    agg, seen = new_agg()
    for p in paths:
        part = json.load(open(p))
        for cs in CAT_SIDES:
            for k, lst in (part.get(cs) or {}).items():
                agg[cs].setdefault(k, []).extend(lst)
            for ss in part.get("_symsides", {}).get(cs) or []:
                seen[cs].add(ss)
    return agg, seen


def summarize(deltas):
    n = len(deltas)
    pos = sum(1 for d in deltas if d > EPS)
    avg = statistics.fmean(deltas) if deltas else 0.0
    med = statistics.median(deltas) if deltas else 0.0
    return pos, avg, med, n


def write_workbook(agg, seen, out):
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for cs in CAT_SIDES:
        ws = wb.create_sheet(cs)
        ws.append(["tab", "name", "kind", "pos_sym", "avg_delta", "median_delta", "n"])
        for c in ws[1]:
            c.font = Font(bold=True)
        rows = []
        for k, deltas in agg[cs].items():
            tab, kind, name = k.split("\t", 2)
            pos, avg, med, n = summarize(deltas)
            rows.append([tab, name, kind, pos, round(avg, 6), round(med, 6), n])
        rows.sort(
            key=lambda r: (r[0], r[2], r[4])
        )  # tab, then switch/filter, then vector_delta asc
        for r in rows:
            ws.append(r)
        nmax = max((r[6] for r in rows), default=0)
        assert nmax <= len(
            seen[cs]
        ), f"[{cs}] n_max {nmax} > #sym_sides {len(seen[cs])}: a sym_side was counted more than once"
        promote = sum(1 for r in rows if r[4] > EPS and r[3] >= 2)
        print(
            f"[{cs}] symsides={len(seen[cs])} rows={len(rows)} n_max={nmax} | gated_promote(vec>0 & pos_sym>=2)={promote}"
        )
    pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"[written] {out}")


def main():
    import datetime, shutil

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--files",
        help="newline-list of progress JSON paths to aggregate (with --emit-partial)",
    )
    ap.add_argument(
        "--scan",
        help="newline-list of progress JSON paths to score for best-per-sym_side; writes TSV here (symside real is56 final_gain mtime path done_n) and stops",
    )
    ap.add_argument(
        "--emit-partial",
        help="write the partial agg JSON here and stop (server-side phase 1)",
    )
    ap.add_argument(
        "--merge",
        help="comma-separated partial JSONs to merge into the workbook (phase 2)",
    )
    ap.add_argument(
        "--progress-dir",
        action="append",
        default=[],
        help="aggregate a whole dir locally (repeatable; newest file per sym_side wins)",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="workbook path; default = dated archive + _latest pointer",
    )
    ap.add_argument("--date", default=datetime.datetime.utcnow().strftime("%Y%m%d"))
    args = ap.parse_args()

    if args.scan:
        files = [l.strip() for l in open(args.files) if l.strip()] if args.files else []
        with open(args.scan, "w") as out:
            for p in files:
                ss, real, is56, fg, done_n = scan_file(p)
                try:
                    mt = os.path.getmtime(p)
                except Exception:
                    mt = 0
                out.write(f"{ss}\t{real}\t{is56}\t{fg}\t{mt}\t{p}\t{done_n}\n")
        print(f"[scan] {args.scan} files={len(files)}")
        return

    if args.emit_partial:
        files = [l.strip() for l in open(args.files) if l.strip()] if args.files else []
        agg, seen = collect_files(files)
        out = {cs: agg[cs] for cs in CAT_SIDES}
        out["_symsides"] = {cs: sorted(seen[cs]) for cs in CAT_SIDES}
        json.dump(out, open(args.emit_partial, "w"))
        tot = sum(len(seen[cs]) for cs in CAT_SIDES)
        print(f"[partial] {args.emit_partial} files={len(files)} symsides={tot}")
        return

    if args.merge:
        agg, seen = merge_partials(
            [p.strip() for p in args.merge.split(",") if p.strip()]
        )
    elif args.progress_dir:
        # newest file per sym_side across the given dirs
        best = {}
        for d in args.progress_dir:
            for p in glob.glob(os.path.join(d, "*_v14_progress.json")):
                ss = os.path.basename(p)[: -len("_v14_progress.json")]
                mt = os.path.getmtime(p)
                if ss not in best or mt > best[ss][0]:
                    best[ss] = (mt, p)
        agg, seen = collect_files([v[1] for v in best.values()])
    else:
        ap.error("need --emit-partial, --merge, or --progress-dir")

    dated_out = args.out or str(ARCHIVE / f"v15_vector_delta_{args.date}.xlsx")
    write_workbook(agg, seen, dated_out)
    try:
        shutil.copyfile(dated_out, str(LATEST))
        shutil.copyfile(dated_out, str(COMPAT_LATEST))
        print(f"[latest]  {LATEST} (+ compat copy {COMPAT_LATEST.name})")
    except Exception:
        pass


if __name__ == "__main__":
    main()
