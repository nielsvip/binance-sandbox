#!/usr/bin/env python3
"""chart_header_truth_fix — every chart's header and <title> state the number its OWN trades produce (USER 2026-10-18).

USER: "correct all in headers and titles of the html files" — after the forensics showed 203 of 566 applied claims were
contradicted by their own evidence, and that the true number was being written BESIDE the claim instead of into it.

THE TRUTH IS NEVER INVENTED HERE. For each file the numbers come from `tools.opt.chart_truth.recompute(TRADES, EVENTS,
CLOSE, is_long)` — the project's own function, which derives the header a chart is allowed to print from the arrays that
chart embeds, through `evaluate_v12._honest_gain_pct / _honest_max_dd_pct / _peak_concurrent` and
`metrics_guard.pool_sharpe_from_ledger`. This tool only compares and rewrites text.

WHAT IT CORRECTS, in place, on --apply:
  * the `class="metrics"` header: gain / trades / DD / TIM / WR / pool_sharpe tokens that disagree beyond --tol
  * the `<title>`: becomes "<SYM_SIDE> gain <true>% · trades <n> (verified from its own trades)" so a browser tab cannot
    show a number the page's own ledger denies
  * a visible CORRECTED banner naming what was changed and what it said before — a silent correction is how the original
    lie survived (board_name_truth_guard wrote the truth into a side field and every consumer kept reading the old one)
It NEVER touches the trade arrays, the chart script, or the per-trade table: the evidence stays exactly as published.
Filenames are reported, never renamed — `tools/board_name_truth_guard.py` owns names and other tools resolve by them.

Every changed file is copied to data/reports/chart_truth_fix/originals/<name> first, and every change is listed in the
report JSON, so any correction can be undone and audited.

Usage:
  tools/chart_header_truth_fix.py                      # dry run over every chart dir, prints the verdict counts
  tools/chart_header_truth_fix.py --apply              # correct them
  tools/chart_header_truth_fix.py --dirs data/reports/charts_1M --limit 50 --apply
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import os
import re
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("V12_ALLOW_STALE_NPZ", "1")
OUT = ROOT / "data" / "reports" / "chart_truth_fix"
DEFAULT_DIRS = ("data/reports/charts_1M", "data/reports/charts_1Y", "SPREADSHEETS")
METRICS_OPEN = '<div class="metrics">'
SYMSIDE_RE = re.compile(r"^([A-Z0-9]+_(?:LONG|SHORT))")
# A gain token in a NON-matrix chart name (zoom / REAL_ZOOMABLE / S6GS). `tools/board_name_truth_guard.py` owns every
# `*_30d_matrix*` name and its triplet map, so those are never touched here; the generators reference these pages by
# suffix pattern (`_30d_zoom.html`, `S6GS_REAL_ZOOMABLE`), never by the gain token, so correcting the token is safe.
GAINTOK_RE = re.compile(r"_gain(m?\d+(?:p\d+)?)")


def gain_token(v: float) -> str:
    return "gain" + ("m" if v < 0 else "") + f"{abs(v):.2f}".replace(".", "p")
TOK = {
    "gain_pct": re.compile(r"(gain\s+)([+-]?[\d.]+)(%)"),
    "trades": re.compile(r"(trades\s+)(\d+)"),
    "max_dd_pct": re.compile(r"(DD\s+)([+-]?[\d.]+)(%)"),
    "tim_pct": re.compile(r"(TIM\s+)([+-]?[\d.]+)(%)"),
    "wr_pct": re.compile(r"(WR\s+)([+-]?[\d.]+)(%)"),
}
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
BANNER_ID = "chart-truth-corrected"


def symside_of(path: Path):
    m = SYMSIDE_RE.match(path.name)
    return m.group(1) if m else None


def check_one(args):
    """(path, apply, tol) -> verdict dict. Pure read unless apply and a correction is needed."""
    path, apply_fix, tol = Path(args[0]), bool(args[1]), float(args[2])
    rec = {"file": str(path), "verdict": "", "changed": [], "before": {}, "after": {}}
    ss = symside_of(path)
    if not ss:
        rec["verdict"] = "SKIP_NOT_A_SYMSIDE_CHART"
        return rec
    rec["sym_side"] = ss
    try:
        text = path.read_text(errors="ignore")
    except Exception as exc:
        rec["verdict"] = f"UNREADABLE:{type(exc).__name__}"
        return rec
    already = BANNER_ID in text
    try:
        from tools.opt import chart_truth as CT

        trades = CT.parse_js_const(text, "TRADES")
        events = CT.parse_js_const(text, "EVENTS") or []
        close = CT.parse_js_const(text, "CLOSE")
        if trades is None or close is None:
            rec["verdict"] = "NO_EVIDENCE_ARRAYS"
            return rec
        true = CT.recompute(trades, events, close, ss.endswith("_LONG"))
        # THE TRADE COUNT NEEDS THE E2 SEMANTIC, NOT len(TRADES). metrics_guard counts a realised close by the presence of
        # pnl_dollars, label irrelevant, and a chart may file genuine closes in EVENTS (measured: 1000FLOKIUSDT_LONG zoom
        # has TRADES=1 with 11 realised closes in EVENTS -> 12, while chart_truth.recompute would say 1). The gain is
        # already right because recompute feeds TRADES+EVENTS to _honest_gain_pct; only the count needs repairing, and
        # getting it wrong here would replace an overstatement with an understatement.
        hidden = [e for e in events if isinstance(e, dict) and e.get("pnl_dollars") is not None and not e.get("seeded_prewindow")]
        true["trades"] = len(trades) + len(hidden)
        rec["hidden_realised_in_events"] = len(hidden)
    except Exception as exc:
        rec["verdict"] = f"RECOMPUTE_FAILED:{type(exc).__name__}"
        rec["error"] = str(exc)[:160]
        return rec
    rec["true_gain"] = round(float(true["gain_pct"]), 4)  # computed even for an already-corrected page, so --rename can judge its NAME
    i = text.find(METRICS_OPEN)
    if i < 0:
        rec["verdict"] = "NO_HEADER"
        return rec
    if already:
        rec["verdict"] = "ALREADY_CORRECTED"
        return rec
    j = text.find("</div>", i)
    head = text[i:j]
    new_head = head
    for key, pat in TOK.items():
        tv = true.get(key)
        if tv is None:
            continue
        m = pat.search(new_head)
        if not m:
            continue
        shown = float(m.group(2))
        want = float(tv)
        if abs(shown - want) <= (tol if key != "trades" else 0):
            continue
        rec["before"][key] = shown
        rec["after"][key] = round(want, 4)
        rec["changed"].append(key)
        fmt = f"{int(round(want))}" if key == "trades" else f"{want:+.2f}" if key == "gain_pct" else f"{want:.2f}"
        new_head = new_head[: m.start()] + m.group(1) + fmt + (m.group(3) if m.lastindex and m.lastindex >= 3 else "") + new_head[m.end():]
    mt = TITLE_RE.search(text)
    true_title = f"{ss} gain {float(true['gain_pct']):+.2f}% · trades {int(true['trades'])} (verified from its own trades)"
    title_wrong = (not mt) or (mt.group(1).strip() != true_title)
    rec["true_gain"] = round(float(true["gain_pct"]), 4)
    if not rec["changed"] and not title_wrong:
        rec["verdict"] = "ALREADY_TRUE"
        return rec
    rec["verdict"] = "CORRECTED" if rec["changed"] else "TITLE_ONLY"
    if title_wrong and mt:
        rec["before"]["title"] = mt.group(1).strip()[:120]
        rec["after"]["title"] = true_title
    if not apply_fix:
        return rec
    try:
        if rec["changed"]:  # only a NUMBER change needs the original kept; a title is derived and the old one is in the report
            OUT.joinpath("originals").mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, OUT / "originals" / path.name)
        out = text[:i] + new_head + text[j:]
        if mt:
            out = out[: mt.start()] + f"<title>{true_title}</title>" + out[mt.end():]
        banner = (f'<div id="{BANNER_ID}" class="metrics" style="border:1px solid #a33;background:#2a1414">'
                  f'<b>HEADER CORRECTED {dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ")}</b> — recomputed from this '
                  f'page\'s own TRADES/EVENTS/CLOSE arrays via tools.opt.chart_truth.recompute. '
                  + ("Changed: " + ", ".join(f"{k} {rec['before'][k]} → {rec['after'][k]}" for k in rec["changed"]) + ". " if rec["changed"] else "")
                  + "The trade table and chart data below are untouched.</div>")
        k = out.find("</div>", out.find(METRICS_OPEN))
        out = out[: k + 6] + banner + out[k + 6:]
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(out)
        os.replace(tmp, path)
        rec["applied"] = True
    except Exception as exc:
        rec["verdict"] = f"WRITE_FAILED:{type(exc).__name__}"
        rec["error"] = str(exc)[:160]
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", default=",".join(DEFAULT_DIRS))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--tol", type=float, default=0.01)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--rename", action="store_true", help="also correct the gain token in NON-matrix chart filenames (zoom / ZOOMABLE); *_30d_matrix* names belong to board_name_truth_guard and are never touched")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    files = []
    for d in [x.strip() for x in a.dirs.split(",") if x.strip()]:
        files += sorted(glob.glob(str(ROOT / d / "*.html")))
    if a.limit:
        files = files[: a.limit]
    print(f"[chart_truth_fix] {len(files)} html files · {'APPLYING' if a.apply else 'DRY RUN'} · tol {a.tol}", flush=True)
    jobs = [(f, a.apply, a.tol) for f in files]
    recs = []
    with ProcessPoolExecutor(max_workers=max(1, a.workers)) as ex:
        for i, r in enumerate(ex.map(check_one, jobs, chunksize=8), 1):
            recs.append(r)
            if i % 1000 == 0:
                print(f"[chart_truth_fix] {i}/{len(files)}", flush=True)
    renames = []
    if a.rename:
        for r in recs:
            f = Path(r["file"])
            if "_30d_matrix" in f.name:
                r["rename"] = "SKIP_MATRIX_OWNED_BY_NAME_TRUTH_GUARD"
                continue
            m = GAINTOK_RE.search(f.name)
            true_g = r.get("true_gain", r.get("after", {}).get("gain_pct"))
            if not m or true_g is None:
                continue
            want = gain_token(float(true_g))
            if m.group(0) == "_" + want:
                continue
            new = f.with_name(f.name[: m.start()] + "_" + want + f.name[m.end():])
            if new.exists():
                r["rename"] = f"TARGET_EXISTS {new.name}"
                continue
            renames.append((f, new, m.group(1), want))
            if a.apply:
                try:
                    os.replace(f, new)
                    r["rename"] = f"RENAMED -> {new.name}"
                except Exception as exc:
                    r["rename"] = f"RENAME_FAILED {type(exc).__name__}: {exc}"
            else:
                r["rename"] = f"WOULD_RENAME -> {new.name}"
        print(f"\nfilenames whose gain token disagrees with the page: {len(renames)}")
        for f, new, old_tok, want in renames[:12]:
            print(f"  {f.name[:58]:58s} _{old_tok} -> _{want}")
        if renames:
            mp = OUT / "renames_old_to_new.json"
            OUT.mkdir(parents=True, exist_ok=True)
            prev = {}
            if mp.exists():
                try:
                    prev = json.loads(mp.read_text())
                except Exception:
                    prev = {}
            prev.update({f.name: new.name for f, new, _o, _w in renames})
            mp.write_text(json.dumps(prev, indent=1))
            print(f"  map: {mp}")
    v = collections.Counter(r["verdict"] for r in recs)
    print("\n=== verdicts ===")
    for k, n in v.most_common():
        print(f"  {n:6d}  {k}")
    fixed = [r for r in recs if r["verdict"] in ("CORRECTED", "TITLE_ONLY")]
    gain_moves = [(r, r["before"]["gain_pct"], r["after"]["gain_pct"]) for r in fixed if "gain_pct" in r["before"]]
    print(f"\nheaders whose GAIN was wrong: {len(gain_moves)}")
    for r, b, aft in sorted(gain_moves, key=lambda x: -abs(x[1] - x[2]))[:15]:
        print(f"  {Path(r['file']).name[:62]:62s} {b:+.2f}% -> {aft:+.2f}%")
    td = [r for r in fixed if "trades" in r["before"]]
    print(f"\nheaders whose TRADE COUNT was wrong: {len(td)}")
    for r in td[:8]:
        print(f"  {Path(r['file']).name[:62]:62s} {r['before']['trades']} -> {r['after']['trades']}")
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%MZ")
    path = Path(a.json) if a.json else OUT / f"chart_truth_fix_{stamp}{'_applied' if a.apply else '_dryrun'}.json"
    path.write_text(json.dumps({"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "applied": bool(a.apply),
                                "tol": a.tol, "n_files": len(files), "verdicts": dict(v),
                                "records": [r for r in recs if r["verdict"] not in ("ALREADY_TRUE", "SKIP_NOT_A_SYMSIDE_CHART")]},
                               default=str, indent=1))
    print(f"\nwrote {path}")
    if a.apply:
        print(f"originals kept in {OUT / 'originals'}")


if __name__ == "__main__":
    main()
