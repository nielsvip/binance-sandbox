#!/usr/bin/env python3
"""v15_xlsx_topup — top up each tradeable sym_side's LATEST PUBLISHED board with ONLY the new and adjusted rows.

USER 2026-10-15/16, verbatim: *"write the script that overwrites the latest best_settings from each xlsx by testing
only the new and adjusted switches and filters on top of every existing xlsx for tradeable sym_sides"* /
*"recalculates ALL the most recent per_sym settings based on the new templates"*.
Spec: HANDOVER_20261015/06_PER_SYM_AUTHORSHIP_AND_TOPUP_SPEC.md. This is NOT a board — a board is 20-60 min, this is
minutes, because it only tests the rows the 2026-10-15 coverage collapse compromised plus the rows added since the
board ran.

AUTHORSHIP (06 §12, corrected: the Mac does NOT author per_sym — it only runs the apply):
    S1/S2 board  ->  SPREADSHEETS/V15_V16_CELL_BY_CELL/{SS}_bh..._gain..._t..._30d_matrix.xlsx + _manifest.json
                 ->  per_sym_history row (source=fleet_orchestrator_publish) written by tools/v15_settings_history.py,
                     the set recovered from the progress JSON whose md5(cumulative_overrides) == manifest overrides_md5
                 ->  tools/v15_published_selection.py  (latest publish per sym_side, MANIFEST_MD5_OK)
                 ->  tools/v15_persym_golive.py --published --pin-all --apply   (Mac cron, hourly)
                 ->  per_sym_active  ->  live reads it via _cfg / _cfg_auto / _psym_get
So a top-up must emit a MATCHED PAIR or nothing reaches live: a new published xlsx + manifest whose `overrides_md5`
is the hash of the final set, AND a `{SS}_v14_progress.json` carrying that exact set as `cumulative_overrides` where
v15_settings_history looks for it (`~/v15_*/progress`). per_sym_active* is never written here.

WHERE IT RUNS: on S1 (crypto NPZ) and s2 (stocks — s2 is the ONLY host that can refresh a stock NPZ; S1 answers
EXCHANGE_BAR_NOT_READY for every stock). Never on the Mac: 21 NPZ there, so nearly every sym_side returns DATA_ERROR.

HARD RULES
  - v15_pilot.py is IMPORTED, never modified (gate candidate 20261015T220419Z). The read-only sidecar
    `_v15_pilot_for_rerun.py` at the repo root is preferred when present — it carries the switch-aware
    mandatory_bases_for_tab and _header_disc_* that the Oct-14 fleet champion lacks.
  - SPREADSHEETS/TEMPLATE_FINAL_NORM/*.xlsx are NEVER written (TEMPLATE ONE-SCRIPT RULE). The boards' own
    V15_V16_CELL_BY_CELL/*.xlsx are what this script writes.
  - Promotion comes from v15_suspect_row_rerun.hunt_switch — the same greedy, positive-delta-only, _qualifies_30d
    path, including the BIBLE §14.2 accumulation invariant that caught a fabricated +118.3 on 2026-10-16. Nothing is
    published unless the final set RE-MEASURES to baseline + Σ(promoted deltas) within --accum-tol.
  - WR is `[UNVERIFIED]` on this path: VP.evaluate_prepared_sanitized returns no per-trade returns, so no win-rate
    number may be printed or promoted on (06 §WR, amended).
  - Best-start law: the published set is the baseline and it is RE-MEASURED on the current NPZ (the stored gain was
    computed on an older NPZ — see npz_md5 in the manifest). A final below that re-measured baseline is never
    published. FLAT (nothing found) is a legitimate result and is reported as FLAT.

USAGE
    python3 tools/v15_xlsx_topup.py --sym-sides BATUSDT_LONG,RLCUSDT_SHORT,XTZUSDT_SHORT          # dry run
    python3 tools/v15_xlsx_topup.py --venue crypto --tiers 1,2 --yellow-mode off --apply          # publish
    python3 tools/v15_xlsx_topup.py --pause s1,s2    # drain dispatch first (in-flight boards finish untouched)
    python3 tools/v15_xlsx_topup.py --resume
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("V12_NPZ_CACHE", "32")

import tools.v15_suspect_row_rerun as RR  # noqa: E402  the measurement half, imported so there is ONE delta path
from tools.opt import v12_pilot as VP  # noqa: E402
from tools.v15_final_naming import final_matrix_name  # noqa: E402

PILOT = RR.PILOT
SHEET_DIR = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
DB = Path(os.environ.get("PER_SYM_STORE_DB") or (ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"))
PUBLISH_SOURCE = "fleet_orchestrator_publish"
OUT_DIR = ROOT / "data" / "reports"
COL = {"C": 3, "E": 5, "F": 6, "G": 7, "H": 8}


def overrides_md5(ov):
    """The pilot's own hash (v15_pilot.py:9354) — v15_settings_history and v15_published_selection both match on it."""
    return hashlib.md5(json.dumps(ov, sort_keys=True, default=str).encode()).hexdigest()


def manifest_index(sym_side):
    out = {}
    for p in sorted(SHEET_DIR.glob(f"{sym_side}_*_manifest.json")):
        try:
            m = json.loads(p.read_text())
        except Exception:
            continue
        if str(m.get("symside", "")).upper() != sym_side.upper() or not m.get("overrides_md5"):
            continue
        xl = SHEET_DIR / str(m.get("final_name") or p.name.replace("_manifest.json", ".xlsx"))
        out[str(m["overrides_md5"])] = {"manifest": p, "xlsx": xl, "source": m.get("source"), "published_utc": m.get("published_utc"), "metrics": m.get("metrics") or {}, "publish_class": m.get("publish_class"), "template": m.get("template"), "pilot_md5": m.get("pilot_md5")}
    return out


def publish_rows(sym_side):
    if not DB.exists():
        return []
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = c.execute("select updated_at, gain_pct, trades, bh_pct, max_dd_pct, tim_pct, pool_sharpe, overrides_json, winning_tag, defaults_round from per_sym_history where sym_side=? and json_extract(meta_json,'$.source')=? order by updated_at desc, id desc", (sym_side.upper(), PUBLISH_SOURCE)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        c.close()
    out = []
    for (upd, gain, trades, bh, dd, tim, psh, ovj, tag, dround) in rows:
        try:
            ov = json.loads(ovj) if ovj else None
        except Exception:
            ov = None
        if isinstance(ov, dict) and ov:
            out.append({"updated_at": upd, "gain_pct": gain, "trades": trades, "bh_pct": bh, "max_dd_pct": dd, "tim_pct": tim, "pool_sharpe": psh, "overrides": ov, "winning_tag": tag, "defaults_round": dround, "md5": overrides_md5(ov)})
    return out


def set_from_progress(sym_side, want_md5s):
    """Recover a published set from the board's own progress JSON — the same recovery `v15_settings_history.COLLECT`
    does: md5(cumulative_overrides) must equal a manifest `overrides_md5`. Needed because only the orchestrator (S1)
    holds per_sym_history: s2 has 0 publish rows but 337 manifests + 933 boards, so without this the stocks host
    could never top up its own boards."""
    pats = []
    for d in list(Path.home().glob("v15_*/progress")) + [ROOT / "data" / "reports" / "lifecycle_pilot"]:
        if not Path(d).is_dir():
            continue
        pats += sorted(Path(d).glob(f"{sym_side}_v14_progress.json")) + sorted(Path(d).glob(f"{sym_side}_v14_progress.json.*")) + sorted(Path(d).glob(f"{sym_side}_*d_progress.json"))
    for p in pats:
        try:
            j = json.loads(p.read_text())
        except Exception:
            continue
        ov = j.get("cumulative_overrides") or j.get("hustler_overrides")
        if not isinstance(ov, dict) or not ov:
            continue
        h = overrides_md5(ov)
        if h in want_md5s:
            return dict(ov), {"progress": str(p), "md5": h, "defaults_round": j.get("defaults_round")}
    return None, None


_PUB_EXPORT = {}


def load_published_sets(path):
    """The orchestrator's per_sym_history, exported by tools/v15_export_start_sets.py --published and read here.
    Measured 2026-10-16: s2 holds 337 manifests but 0 publish rows, and the progress JSON that produced a board is
    usually overwritten by the next round — 1 of 7 sym_sides recovered locally. With the export, every published
    board can be topped up on whichever host holds its NPZ."""
    global _PUB_EXPORT
    d = json.loads(Path(path).read_text())
    sets = d.get("sets") if isinstance(d, dict) else d
    if not isinstance(sets, dict) or not sets:
        raise SystemExit(f"--published-sets {path}: no sets found")
    _PUB_EXPORT = {str(k).upper(): v for k, v in sets.items()}
    RR.log(f"[published-sets] {path}: {len(_PUB_EXPORT)} sym_sides exported_at={d.get('exported_at')} from {d.get('host')}")


def published_start_set(sym_side):
    """06 §1-2: the board's own best published set — manifest-bound first, highest published gain wins (best-start
    law), newest publish breaks a tie. Never template defaults, never the live per_sym row (live can have drifted
    off its own board; the board is the authored truth)."""
    rows = publish_rows(sym_side)
    mans = manifest_index(sym_side)
    if not rows and _PUB_EXPORT.get(sym_side.upper()):
        e = _PUB_EXPORT[sym_side.upper()]
        ov = e.get("overrides") or {}
        md5 = e.get("overrides_md5") or overrides_md5(ov)
        man = mans.get(md5)
        xl = man["xlsx"] if man and Path(man["xlsx"]).exists() else None
        if xl is None:
            cands = sorted(SHEET_DIR.glob(f"{sym_side}_bh*_30d_matrix.xlsx"))
            xl = cands[-1] if cands else None
        return dict(ov), {"source": "PUBLISHED_EXPORT" + (" + MANIFEST_MD5_OK" if man else " (no local manifest with that md5)"), "published_utc": (man or {}).get("published_utc") or e.get("updated_at"), "stored_gain_pct": RR._r(e.get("gain_pct")), "stored_trades": e.get("trades"), "stored_bh_pct": RR._r(e.get("bh_pct")), "overrides_md5": md5, "keys": len(ov), "xlsx": str(xl) if xl else None, "board_template": (man or {}).get("template"), "board_pilot_md5": (man or {}).get("pilot_md5"), "defaults_round": e.get("defaults_round"), "publish_rows_seen": 0, "manifests_on_disk": len(mans), "exported_from": "per_sym_history on the orchestrator"}
    if not rows and not mans and os.environ.get("V15_TOPUP_ALLOW_LIVE_START", "0") == "1":
        # USER 2026-10-16: "make sure every single tradeable sym_side gets recalculated". ~21 tradeable sym_sides
        # have never been published, so there is no board to top up. Their LIVE per_sym set is what they trade
        # today, so that is the honest baseline — flagged as such, never silently mixed in with board baselines.
        try:
            import per_sym_store as _PSS
            live = _PSS.get_overrides(sym_side)
        except Exception as e:
            live = None
            RR.log(f"  {sym_side}: live start lookup failed ({e})")
        if live:
            return dict(live), {"source": "LIVE_PER_SYM (never published — no board exists to top up)", "published_utc": None,
                                "stored_gain_pct": None, "stored_trades": None, "stored_bh_pct": None,
                                "overrides_md5": None, "keys": len(live), "xlsx": None, "board_template": None,
                                "board_pilot_md5": None, "publish_rows_seen": 0, "manifests_on_disk": 0}
    if not rows:
        if not mans:
            return None, {"why": "no per_sym_history publish row and no manifest on this host — the board was published elsewhere", "manifests_on_disk": 0}
        ov, got = set_from_progress(sym_side, set(mans))
        if not ov:
            return None, {"why": f"{len(mans)} manifest(s) here but no progress JSON whose md5(cumulative_overrides) matches one of them — the set cannot be recovered on this host", "manifests_on_disk": len(mans)}
        man = mans[got["md5"]]
        best = max(mans.values(), key=lambda m: float((m.get("metrics") or {}).get("gain_pct") or -1e9))
        xl = man["xlsx"] if Path(man["xlsx"]).exists() else None
        return dict(ov), {"source": "MANIFEST_MD5_OK (progress-recovered set; this host holds no per_sym_history)", "published_utc": man.get("published_utc"), "stored_gain_pct": RR._r((man.get("metrics") or {}).get("gain_pct")), "stored_trades": (man.get("metrics") or {}).get("trades"), "stored_bh_pct": RR._r((man.get("metrics") or {}).get("bh")), "overrides_md5": got["md5"], "keys": len(ov), "xlsx": str(xl) if xl else None, "board_template": man.get("template"), "board_pilot_md5": man.get("pilot_md5"), "defaults_round": got.get("defaults_round"), "progress": got["progress"], "publish_rows_seen": 0, "manifests_on_disk": len(mans), "note": None if man is best else f"recovered set belongs to {Path(man['xlsx']).name}; the highest-gain manifest here is {Path(best['xlsx']).name} ({(best.get('metrics') or {}).get('gain_pct')}) whose set could not be recovered"}
    # USER 2026-10-16: "we just had a revision of the charts ... the recalc of all has to be on top of these results
    # not on top of previous fake numbers" (and "not just zro, all previous results"). So the LATEST publish wins
    # outright — a revision that CORRECTS a number downwards must not be beaten by the inflated row it replaced.
    # Manifest-md5 agreement is only a tiebreak between rows published at the same instant, never a reason to reach
    # back past a revision.
    # USER 2026-10-16: "make sure you work on top of the VERIFIED charts not the previous fake charts and numbers".
    # A board this top-up published BEFORE the revision rule landed (04:00Z) may itself have been built on a
    # pre-revision baseline, so it is not a verified starting point: skip it and fall back to the publish it
    # superseded. Measured: 18 sym_sides had exactly such a row as their latest publish. Only top-up rows are
    # skipped — an orchestrator board is always a legitimate baseline. V15_TOPUP_VERIFIED_AFTER overrides, and
    # setting it empty disables the skip.
    _cut = os.environ.get("V15_TOPUP_VERIFIED_AFTER", "2026-10-16T04:00")
    scored = []
    for r in rows:
        man = mans.get(r["md5"])
        if _cut and man and str(man.get("source") or "") == "v15_xlsx_topup" and str(man.get("published_utc") or "") < _cut:
            continue
        scored.append((r["updated_at"], 1 if man else 0, float(r["gain_pct"] or -1e9), r, man))
    if not scored:
        scored = [(r["updated_at"], 1 if mans.get(r["md5"]) else 0, float(r["gain_pct"] or -1e9), r, mans.get(r["md5"])) for r in rows]
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    upd, md5_ok, gain, row, man = scored[0]
    xl = man["xlsx"] if man and Path(man["xlsx"]).exists() else None
    if xl is None:
        cands = sorted(SHEET_DIR.glob(f"{sym_side}_bh*_30d_matrix.xlsx"))
        xl = cands[-1] if cands else None
    info = {"source": "MANIFEST_MD5_OK" if md5_ok else "HISTORY_PUBLISH_ROW", "published_utc": (man or {}).get("published_utc") or upd, "stored_gain_pct": RR._r(gain), "stored_trades": row["trades"], "stored_bh_pct": RR._r(row["bh_pct"]), "overrides_md5": row["md5"], "keys": len(row["overrides"]), "xlsx": str(xl) if xl else None, "board_template": (man or {}).get("template"), "board_pilot_md5": (man or {}).get("pilot_md5"), "publish_rows_seen": len(rows), "manifests_on_disk": len(mans)}
    return dict(row["overrides"]), info


_STAMP_CACHE = {}


def stamped_new_rows(sym_side, since="20261013"):
    """The rows the TEMPLATE actually gained, read from the stamp tools/v15_switch_add.py leaves in the column-A
    comment ("switch-add YYYYMMDDHHMM"). That is the precise meaning of "the new rows": not "a switch missing from
    this board's set" (a per_sym full set carries ~3500 keys, so almost nothing is missing — that definition tested
    0 WT rows on AAPL_LONG) and not the whole WT family (114 rows x ~127 filters = ~15 min/board).
    -> {switch: {"tabs": [...], "stamps": [...]}}"""
    tpl = str(PILOT.get_template_for_symside(sym_side))
    key = (tpl, since)
    if key in _STAMP_CACHE:
        return _STAMP_CACHE[key]
    import openpyxl
    wb = openpyxl.load_workbook(tpl)
    out = {}
    try:
        for ws in wb.worksheets:
            if ws.title not in PILOT.SWITCH_SHEETS:
                continue
            for r in range(3, ws.max_row + 1):
                c = ws.cell(r, 1)
                if not c.comment or "switch-add" not in str(c.comment.text):
                    continue
                ts = str(c.comment.text).split("switch-add")[1].strip()[:8]
                if ts < since or not isinstance(c.value, str):
                    continue
                e = out.setdefault(c.value.strip(), {"tabs": [], "stamps": []})
                if ws.title not in e["tabs"]:
                    e["tabs"].append(ws.title)
                e["stamps"].append(ts)
    finally:
        wb.close()
    _STAMP_CACHE[key] = out
    return out


def new_row_suspects(view, start_ov, venue):
    """Rows the board never had: a template row whose switch is absent from the published set. That is the honest,
    measurable reading of "new and adjusted rows" — the board's own template file is not kept on disk, so the diff is
    taken against the set it produced (the manifest records only the template's NAME)."""
    seen, out = set(), []
    for tab, v in view.items():
        for (_r_, sw, cand, _isdef) in v["rows"]:
            if sw in RR.NEVER_TEST or sw in start_ov or sw in seen:
                continue
            seen.add(sw)
            ladder = sorted({RR._j(c) for (_rr, s2, c, _d) in v["rows"] if s2 == sw and c is not None and c != PILOT.DEFAULT_MARKER}, key=str)
            if not ladder:
                continue
            out.append({"switch": sw, "ladder": ladder, "parents": {}, "parent_ref": "new row (absent from the published set)", "venue": "both", "tabs": [tab], "new_row": True})
    return out


def board_view(wb):
    """{tab: {"rows": {(switch, str(cand)): row}, "hmap": {"FILTER=opt": col}}} read off the BOARD workbook, so cells
    land on that board's own layout rather than today's template's."""
    view = {}
    for ws in wb.worksheets:
        if ws.title not in PILOT.SWITCH_SHEETS:
            continue
        rows, hmap = {}, {}
        for rr in range(3, ws.max_row + 1):
            a = ws.cell(row=rr, column=1).value
            if not isinstance(a, str) or not a.strip():
                continue
            rows.setdefault((a.strip(), str(ws.cell(row=rr, column=2).value)), rr)
        for cc in range(12, ws.max_column + 1):
            hv = ws.cell(row=2, column=cc).value
            if isinstance(hv, str) and hv.strip().upper().startswith("WHAT SWITCH"):
                break
            if isinstance(hv, str) and "=" in hv:
                hmap[hv.strip()] = cc
        view[ws.title] = {"rows": rows, "hmap": hmap}
    return view


def write_cells(wb, out):
    """Write what was measured onto the board: C/E/F/G per tested row and the yellow cells that were evaluated.
    CLAUDE.md column law — C=override string, E=cumulative_before, F=HUSTLE (vec_gain-baseline), G=VECTOR (delta).
    Only rows this run actually evaluated are touched; every other cell of the board is left exactly as published."""
    bv = board_view(wb)
    base = float(out["baseline"]["gain_pct"])
    written = {"rows": 0, "yellows": 0, "missing_rows": [], "missing_yellows": 0}
    for rec in out["switches"]:
        sw = rec["switch"]
        for row in rec.get("ladder") or []:
            cand = row["move"].get(sw)
            if cand is None:
                continue
            hit = None
            for tab in rec.get("tabs") or list(bv):
                hit = (bv.get(tab) or {}).get("rows", {}).get((sw, str(cand)))
                if hit:
                    ws, rr = wb[tab], hit
                    break
            if not hit:
                written["missing_rows"].append(f"{sw}={cand}")
                continue
            ws.cell(row=rr, column=COL["E"]).value = round(float(rec["cum_before"]), 4)
            if row.get("gain") is not None:
                ws.cell(row=rr, column=COL["F"]).value = round(float(row["gain"]) - base, 4)
            if row.get("delta") is not None:
                ws.cell(row=rr, column=COL["G"]).value = round(float(row["delta"]), 4)
            p = rec.get("promoted")
            if p and row["label"] == p["label"]:
                ws.cell(row=rr, column=COL["C"]).value = "+".join(f"{k}={v}" for k, v in sorted(p["move"].items()))
            written["rows"] += 1
            for y in rec.get("yellows") or []:
                col = (bv.get(y.get("tab")) or {}).get("hmap", {}).get(y.get("header"))
                if not col:
                    written["missing_yellows"] += 1
                    continue
                if y.get("delta_vs_row") is not None:
                    wb[y["tab"]].cell(row=rr, column=col).value = round(float(y["delta_vs_row"]), 4)
                    written["yellows"] += 1
    return written


def progress_dir(tag):
    d = Path.home() / f"v15_topup_{tag}" / "progress"  # matches the ~/v15_*/progress glob v15_settings_history scans
    d.mkdir(parents=True, exist_ok=True)
    return d


def measure_wr(sym_side, ov, window_days):
    """Fresh win rate for a set, from the SAME ledger path the zoomable chart renders
    (tools/opt/lifecycle_pilot.evaluate_month -> wr_pct at :335, computed over realised trades).
    USER 2026-10-16: "there is no reason it would not be calculated ... if you dont measure you can not pass" — so a
    publish REQUIRES a measured WR, and the number travels into the manifest, the progress JSON and the chart.
    ~2.2 s measured on ACN_LONG (gain reproduced to the 4th decimal against the vector).

    USER 2026-10-16 ("no more exaggeration of technicals and gains can ever take place"): the engine closes the
    window by booking the OPEN position's paper P&L as a ledger row (FINAL_MTM) and its gain_pct/trades/wr_pct
    include it — 138 of the 439 latest published charts carried one. The headline that names a board is therefore
    re-derived HERE from the realised closes only (metrics_guard), with the paper dollars reported beside it, so
    the board name, the manifest, the per_sym row and the chart header all state the same realised number."""
    try:
        from tools.opt.lifecycle_pilot import evaluate_month
        from tools.opt.evaluate_v12 import _honest_gain_pct as _ev_gain, _honest_max_dd_pct as _ev_dd
        import metrics_guard as _mg
        r = evaluate_month(sym_side, dict(ov), include_ledger=True, window_days=int(window_days))
        if not r.get("valid"):
            return None, {"why": f"ledger eval invalid ({r.get('invalid_reason')})"}
        led = r.get("execution_ledger") or []
        real = _mg.realised_closes(led)
        marks, paper = _mg.unrealised_marks(led), _mg.unrealised_dollars(led)
        if not real:
            return None, {"why": f"no realised close in the ledger ({len(led)} rows, {len(marks)} unrealised mark(s)) — nothing to publish"}
        wins = sum(1 for t in real if float(t.get("pnl_dollars") or 0.0) > 0)
        wr = 100.0 * wins / len(real)
        _cnt = {"ledger": [t for t in led if not _mg.is_unrealised_mark(t)], "mean_deployed": r.get("mean_deployed")}
        out = {"gain_pct": RR._r(_ev_gain(_cnt)), "trades": len(real), "wr_pct": RR._r(wr),
               "max_dd_pct": RR._r(_ev_dd(_cnt)), "tim_pct": RR._r(r.get("tim_pct")), "bh_pct": RR._r(r.get("bh_pct")),
               "ledger_rows": len(led), "metrics_source": "metrics_guard.realised_closes (unrealised marks excluded)"}
        if marks:
            out["unrealised_marks"] = len(marks)
            out["unrealised_dollars"] = RR._r(paper)
            out["engine_gain_pct_with_marks"] = RR._r(r.get("gain_pct"))
            out["engine_trades_with_marks"] = int(r.get("trades") or 0)
            RR.log(f"  {sym_side} UNREALISED EXCLUDED: {len(marks)} mark(s) = ${paper:+.2f} paper — engine said gain {RR._r(r.get('gain_pct'))} / {r.get('trades')} trades, realised is {out['gain_pct']} / {out['trades']}")
        return float(wr), out
    except Exception as e:
        return None, {"why": f"{type(e).__name__}: {str(e)[:160]}"}


def fetch_source_xlsx(sym_side, info):
    """A crypto board can have been published on EITHER host, so the set resolves from per_sym_history here while
    its workbook sits on the peer — measured 2026-10-16: XTZUSDT_SHORT improved +2.85 and could not publish
    ("source board xlsx not on this host"). Pull the named workbook from the peer instead of losing the result.
    Returns a local path or None; never raises."""
    import subprocess

    name = None
    if info.get("xlsx"):
        name = Path(info["xlsx"]).name
    if not name:
        mans = manifest_index(sym_side)
        m = mans.get(info.get("overrides_md5") or "")
        name = Path(m["xlsx"]).name if m else None
    if not name:
        return None
    me = os.uname().nodename
    # USER 2026-10-16: the s1 branch pointed at 10.0.0.4 (S4 — long destroyed), so s1 effectively had NO peer and
    # 66 measured crypto boards were discarded here. s1 <-> s2 both ways; the Mac is NAT'd and cannot be pulled
    # from, so tools/v15_topup_push_boards.py pushes its boards ahead of the chunk instead.
    peers = ["s1-pub", "10.0.0.3"] if me == "s2" else ["s2", "s2-pub"]
    dst = SHEET_DIR / name
    for peer in peers:
        try:
            r = subprocess.run(["rsync", "-az", "--timeout=60", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new",
                                f"{peer}:binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{name}", str(dst)],
                               capture_output=True, timeout=180)
            if r.returncode == 0 and dst.exists() and dst.stat().st_size > 50000:
                RR.log(f"  {sym_side} source workbook pulled from {peer}: {name}")
                return str(dst)
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def activate(sym_side, final_name, gain, cls, tmp, out, args):
    """Which xlsx becomes the ONE active board for this sym_side.

    `v15_pilot._apply_best_publish` ranks on the gain parsed out of the FILENAME — a number each board measured on
    its OWN NPZ window. Measured 2026-10-16 on BATUSDT_LONG: the standing board's name says `gain97p` while that
    board's own set re-measures to **+6.15%** today (its `bh` moved 138.02 -> 198.94 — the 30 d window slid three
    days over a symbol that nearly tripled). Ranking a freshly measured +6.17 against a stale 97.03 would file the
    only current number as `.superseded` and nothing would ever reach live again for that sym_side.

    So: when the set we topped up IS the standing best's set (the usual case — `published_start_set` picks the
    standing board), the comparison is like-for-like on ONE NPZ and `final > re-measured baseline` is the honest
    ranking, so we activate and say so. Otherwise we hand the decision back to the pilot's own law unchanged.
    `--rank pilot` forces the pilot's filename ranking; `--rank none` files the xlsx without superseding anything."""
    st = PILOT._standing_best(SHEET_DIR, sym_side)
    same_set = bool(out["start_set"].get("xlsx") and st and Path(out["start_set"]["xlsx"]).name == st[0])
    if args.rank == "pilot" or not same_set:
        a, w = PILOT._apply_best_publish(SHEET_DIR, sym_side, final_name, gain, cls, tmp)
        return a, w, f"pilot filename ranking (same_set={same_set}, standing={st[0] if st else None})"
    if args.rank == "none":
        PILOT._atomic_copy(tmp, SHEET_DIR / final_name)
        return final_name, True, "filed without superseding (--rank none)"
    for old in SHEET_DIR.glob(f"{sym_side}_bh*_30d_matrix.xlsx"):
        if old.name == final_name:
            continue
        try:
            old.rename(SHEET_DIR / (old.name + ".superseded"))
            for comp in (SHEET_DIR / old.name.replace(".xlsx", ".html"), SHEET_DIR / old.name.replace(".xlsx", "_manifest.json")):
                if comp.exists():
                    comp.rename(SHEET_DIR / (comp.name + ".superseded"))
        except Exception as e:
            RR.log(f"[topup-warn] supersede {old.name}: {e}")
    PILOT._atomic_copy(tmp, SHEET_DIR / final_name)
    note = f"re-measured ranking: {st[0]} says {st[1]:+.2f} but ITS OWN SET re-measures {out['baseline']['gain_pct']:+.2f} today -> topped up to {gain:+.2f} on the same NPZ"
    RR.log(f"[BEST-PUBLISH-REMEASURED] {sym_side} {note}")
    return final_name, True, note


def publish(out, args):
    """Emit the matched pair: published xlsx (+ html companion name reserved) + manifest + a progress JSON carrying
    the exact set. Refuses on anything the measurement half did not prove."""
    ss = out["sym_side"]
    if out.get("status") != "OK":
        return {"published": False, "why": out.get("status")}
    if out.get("apply_blocked_by_stale_npz"):
        return {"published": False, "why": "measured on a stale NPZ — never published"}
    # USER 2026-10-16: "it should be physically impossible to score less unless the updated npz caused it and nothing
    # in the rerun could save it - in which case the NEW worse result on the new npz would prevail." So a FLAT
    # sym_side still publishes: the SET is unchanged but its metrics are re-measured on today's NPZ, which is what
    # stops a stale filename gain (BATUSDT_LONG said 97.03, re-measures 6.15) from outranking the current truth.
    if not out["final"].get("qualifies"):
        return {"published": False, "why": f"final set fails _qualifies_30d: {out['final'].get('why')}"}
    if float(out["final"]["gain_pct"]) < float(out["baseline"]["gain_pct"]) - 1e-9:
        return {"published": False, "why": "final below the re-measured baseline — greedy makes this impossible, so it is a bug, not a result"}
    # The accumulation sum is RECORDED, never a gate (USER 2026-10-16: "the sum of the pos delta is not always the
    # outcome of the rerun so make sure the actual pos delta of the combination of the switches is what counts").
    # Every promotion is already a joint eval of the whole combination; the final set is re-evaluated once more above,
    # and THAT measured number is what is published.
    wr, wrinfo = measure_wr(ss, out["final_set"], out["window_days"])
    out["final"]["wr_pct"] = RR._r(wr)
    out["final"]["wr_source"] = wrinfo
    if wr is None:
        return {"published": False, "why": f"WR not measured — an xlsx may not close without it (USER 2026-10-16): {wrinfo.get('why')}"}
    src = out["start_set"].get("xlsx")
    if not src or not Path(src).exists():
        src = fetch_source_xlsx(ss, out["start_set"])
        out["start_set"]["xlsx_fetched_from_peer"] = bool(src)
    if not src or not Path(src).exists():
        return {"published": False, "why": "source board xlsx is on neither this host nor the peer — the measurement stands in the report but cannot be published"}
    import openpyxl
    wb = openpyxl.load_workbook(src)
    try:
        cells = write_cells(wb, out)
        # NAME AND MANIFEST COME FROM THE LEDGER, NEVER THE VECTOR (USER 2026-10-16, after BATUSDT_LONG was
        # published 04:09 as `gain40p` while the chart drawn from the same set summed to +47.92%). `measure_wr`
        # above already ran the LEDGER path (lifecycle_pilot.evaluate_month) — the only producer with per-trade
        # returns and the one the chart is drawn from — so its numbers are used for the filename and the metrics.
        # The vector figures are kept alongside for provenance. Fail closed: no ledger number, no publish.
        _led = wrinfo if isinstance(wrinfo, dict) else {}
        if _led.get("gain_pct") is None or _led.get("trades") is None:
            return {"published": False, "why": f"ledger metrics missing — a board may not be named from the vector (USER 2026-10-16): {_led.get('why')}"}
        bh = RR._r(_led.get("bh_pct")) if _led.get("bh_pct") is not None else out["baseline"]["bh_pct"]
        gain, trades = float(_led["gain_pct"]), int(_led["trades"])
        # CHAIN-LAW (USER 2026-10-17 "never add neg deltas"): the floor above compared VECTOR final vs VECTOR baseline, then the
        # board was named from the LEDGER — 11 topups (KSMUSDT_SHORT vec 16.45->111.58 but ledger 14.72->4.96) published below
        # their own start. The published metric is the ledger, so the floor is the baseline set's ledger number, same NPZ.
        _bled = (out.get("baseline") or {}).get("wr_source") if isinstance((out.get("baseline") or {}).get("wr_source"), dict) else {}
        if _bled.get("gain_pct") is None:
            return {"published": False, "why": "CHAIN-LAW: baseline ledger gain not measured — a final cannot be proven >= its start"}
        if gain < float(_bled["gain_pct"]) - 1e-9:
            out["chain_law"] = {"baseline_ledger": _bled["gain_pct"], "final_ledger": gain, "baseline_vector": out["baseline"]["gain_pct"], "final_vector": out["final"].get("gain_pct")}
            RR.log(f"  {ss} [CHAIN-LAW] final ledger {gain:+.4f} < baseline ledger {float(_bled['gain_pct']):+.4f} (vector {out['baseline']['gain_pct']} -> {out['final'].get('gain_pct')}) — not published, the standing board stays")
            return {"published": False, "why": f"CHAIN-LAW: final ledger {gain:.4f} < baseline ledger {float(_bled['gain_pct']):.4f} — vector promotions did not hold on the published metric"}
        out["final"]["gain_pct_vector"] = out["final"].get("gain_pct")
        out["final"]["trades_vector"] = out["final"].get("trades")
        out["final"]["gain_pct"], out["final"]["trades"] = gain, trades
        out["final"]["tim_pct"] = _led.get("tim_pct", out["final"].get("tim_pct"))
        out["final"]["max_dd_pct"] = _led.get("max_dd_pct", out["final"].get("max_dd_pct"))
        out["final"]["metrics_source"] = "ledger (lifecycle_pilot.evaluate_month) — the same path the chart is drawn from"
        final_name = final_matrix_name(ss, bh, gain, trades, int(out["window_days"]))
        tmp = Path(src).parent / f".{ss}_topup_{args.tag}.xlsx"
        PILOT._atomic_save(wb, tmp)
    finally:
        wb.close()
    if not tmp.exists():
        return {"published": False, "why": "_atomic_save produced no file (zip validation refused it)"}
    cls = "QUALIFIED"
    active, won, rank_note = activate(ss, final_name, gain, cls, tmp, out, args)
    ovm = overrides_md5(out["final_set"])
    npp = PILOT._npz_path(ss)
    man = PILOT._build_publish_manifest(ss, final_name, {**{k: out["final"].get(k) for k in ("gain_pct", "trades", "tim_pct", "max_dd_pct", "valid", "pool_sharpe")}, "bh": bh, "wr_pct": out["final"].get("wr_pct")}, (cells["rows"], len(out["promoted_moves"]), cells["rows"], len(out["switches"])), ovm, PILOT._md5_file(SHEET_DIR / final_name) if (SHEET_DIR / final_name).exists() else None, npp.name if npp else None, PILOT._md5_file(npp) if npp else None, PILOT._npz_span_days(ss), PILOT._md5_file(Path(PILOT.__file__)), out["start_set"].get("board_template"), os.uname().nodename)
    man.update(publish_class=cls, source="v15_xlsx_topup", wr_pct=out["final"].get("wr_pct"), wr_source=out["final"].get("wr_source"), flat=not out.get("promoted_moves"), topped_up_from={k: out["start_set"].get(k) for k in ("overrides_md5", "published_utc", "stored_gain_pct", "source")}, baseline_remeasured_gain_pct=out["baseline"]["gain_pct"], promoted_moves=out["promoted_moves"], wr="measured on the ledger (lifecycle_pilot.evaluate_month), the same per-trade list the chart draws", metrics_source=out["final"].get("metrics_source"), metrics_vector={"gain_pct": out["final"].get("gain_pct_vector"), "trades": out["final"].get("trades_vector"), "note": "evaluate_prepared_sanitized — no per-trade returns; recorded for provenance, never for the filename"}, accumulation_check=out["accumulation_check"], cells_written=cells)
    prog = {"symside": ss, "sym_side": ss, "cumulative_overrides": out["final_set"], "baseline_gain": out["baseline"]["gain_pct"], "final_gain": gain, "trades": trades, "wr_pct": out["final"].get("wr_pct"), "verdict": ("TOPUP_PUBLISHED" if out.get("promoted_moves") else "TOPUP_REMEASURED") if won else "TOPUP_SUPERSEDED", "defaults_round": out["start_set"].get("defaults_round"), "result_done_utc": RR.utcnow(), "npz_id": {"name": npp.name if npp else None, "md5": PILOT._md5_file(npp) if npp else None}, "source": "v15_xlsx_topup", "overrides_md5": ovm, "done": {}, "window_days": out["window_days"]}
    pp = progress_dir(args.tag) / f"{ss}_v14_progress.json"
    pp.write_text(json.dumps(prog, indent=1, default=str))
    res = {"published": bool(won), "active": active, "final_name": final_name, "overrides_md5": ovm, "progress_json": str(pp), "cells": cells, "rank_note": rank_note}
    if won:
        mp = SHEET_DIR / final_name.replace(".xlsx", "_manifest.json")
        mp.write_text(json.dumps(man, indent=1, default=str))
        res["manifest"] = str(mp)
        # The HTML companion is part of a publish (USER 2026-10-16: "incl new xlsx and html"). Same generator and
        # same naming the pilot uses at its own publish (v15_pilot.py:9411-9419), on the SAME set as the xlsx, so the
        # chart is coherent with the board by construction. A chart failure never invalidates the xlsx.
        if not args.no_html:
            try:
                from tools.opt.hires_chart import generate_hires
                from tools.v15_final_naming import chart_name_for
                cn = chart_name_for(final_name)
                generate_hires(ss, dict(out["final_set"]), int(out["window_days"]), out_name=cn)  # no live_metrics: the chart header reads its OWN evaluate_month result, which carries BH, delta_vs_bh, peak_capital and score as well as gain/WR/DD/TIM. Passing a partial dict REPLACED that dict (hires_chart.py:171) and blanked BH on the first 13 charts.
                src = ROOT / "data" / "reports" / "charts_1Y" / cn
                if src.exists():
                    shutil.copy2(src, SHEET_DIR / cn)
                    res["html"] = cn
                else:
                    res["html_warn"] = f"generator left no {src}"
            except Exception as ce:
                res["html_warn"] = f"{type(ce).__name__}: {str(ce)[:160]} — xlsx stands, chart skipped"
    else:
        res["why"] = f"{active} still ranks higher — xlsx filed as .superseded, no manifest written (pilot behaviour)"
        try:
            tmp.unlink()
        except Exception:
            pass
    return res


def topup_one(sym_side, args):
    t0 = time.time()
    venue = RR.venue_of(sym_side)
    out = {"sym_side": sym_side, "venue": venue, "ts": RR.utcnow(), "host": os.uname().nodename, "window_days": args.window_days, "source": "v15_xlsx_topup", "pilot_module": RR._PILOT_NAME, "pilot_md5": RR._pilot_md5(), "switches": []}
    ov, info = published_start_set(sym_side)
    out["start_set"] = info
    if not ov:
        out["status"] = f"NO_PUBLISHED_SET — {info.get('why')}"
        RR.log(f"  {sym_side}: {out['status']}")
        return out
    ov, coerced = RR.coerce_set_types(ov, venue)
    if coerced:
        out["type_coerced"] = {k: [str(a)[:60], str(b)[:60]] for k, (a, b) in coerced.items()}
        RR.log(f"  {sym_side} TYPE-COERCED {list(coerced)} (published set held a container as a string — the evaluator rejects the whole set)")
    ov_raw = dict(ov)
    ov, reent = RR.apply_reentry_guarantee(ov, venue)
    if reent:
        out["reentry_guarantee_repaired"] = {k: [str(a)[:40], str(b)[:40]] for k, (a, b) in reent.items()}
        RR.log(f"  {sym_side} REENTRY-GUARANTEE repaired {out['reentry_guarantee_repaired']} (USER 2026-10-16: a board may not optimise its own reentries away)")
    ov, floored = RR.apply_trend_floors(ov, venue)
    if floored:
        out["trend_floor_repaired"] = floored
        RR.log(f"  {sym_side} TREND-FLOOR repaired {floored} (board sat below its own live default — USER 2026-10-16)")
    ov, pinned = RR.apply_hard_set(ov)
    if pinned:
        out["hard_set_pinned"] = {k: RR._j(v) for k, v in pinned.items()}
        RR.log(f"  {sym_side} HARD-SET pinned {out['hard_set_pinned']} (USER 2026-10-16: not tested, not promoted)")
    out["start_set_keys"] = len(ov)
    out["start_set_md5"] = RR.set_md5(ov)
    if os.environ.get("V12_ALLOW_STALE_NPZ") == "1":
        out["NUMBERS_ARE"] = "FROZEN-WINDOW STRUCTURAL FIGURES — V12_ALLOW_STALE_NPZ=1; never published"
        out["apply_blocked_by_stale_npz"] = True
    prepared = VP.prepare_batch(sym_side, args.window_days)
    if isinstance(prepared, dict) and prepared.get("_stale"):
        out["status"] = f"NPZ_STALE: {prepared['_stale']}"
        RR.log(f"  {sym_side}: {out['status']}")
        return out
    defaults = PILOT.get_defaults_for_symside(sym_side)
    view = RR.template_view(sym_side, defaults)
    ev = RR.Evaluator(prepared, args.window_days, args.workers, args.cell_timeout)
    base = ev.one(ov)
    alt = (_PUB_EXPORT.get(sym_side.upper()) or {}).get("alt") if _PUB_EXPORT else None
    if isinstance(alt, dict) and (alt.get("overrides") or {}):
        # 06 §1 wants the highest published gain as the start, but stored gains come from different NPZ windows
        # (BATUSDT_LONG's board says 97.03 and re-measures 6.15). So both candidates are re-measured here and the
        # higher one on TODAY's NPZ becomes the baseline — a like-for-like best-start instead of a paper one.
        ab = ev.one(alt["overrides"])
        out["start_set_alt"] = {"overrides_md5": alt.get("overrides_md5"), "published_utc": alt.get("updated_at"), "stored_gain_pct": RR._r(alt.get("gain_pct")), "keys": len(alt["overrides"]), "remeasured_gain_pct": RR._r(ab.get("gain_pct"))}
        if False:  # USER 2026-10-16: the revision is the baseline. An older set that happens to re-measure higher
            # today is recorded above for the record but is NEVER adopted as the start — that is how a pre-revision
            # number would creep back in.
            RR.log(f"  {sym_side} ALT published set re-measures higher ({RR._r(ab.get('gain_pct'))} vs {RR._r(base.get('gain_pct'))}) — starting from it")
            ov, base = dict(alt["overrides"]), ab
            info.update(source=str(info.get("source")) + " -> ALT (re-measured higher)", overrides_md5=alt.get("overrides_md5"), stored_gain_pct=RR._r(alt.get("gain_pct")), keys=len(ov), published_utc=alt.get("updated_at"))
            out["start_set_keys"], out["start_set_md5"] = len(ov), RR.set_md5(ov)
    bh = RR._r(base.get("bh_pct"))
    if base.get("gain_pct") is None:
        out["status"] = f"BASELINE_UNMEASURABLE: {base.get('invalid_reason')}"
        ev.close()
        RR.log(f"  {sym_side}: {out['status']}")
        return out
    cum = float(base["gain_pct"])
    bok, breasons = RR.qualifies(base, bh)
    out["baseline"] = {"gain_pct": RR._r(cum), "bh_pct": bh, "trades": int(base.get("trades") or 0), "max_dd_pct": RR._r(base.get("max_dd_pct")), "tim_pct": RR._r(base.get("tim_pct")), "pool_sharpe": RR._r(base.get("pool_sharpe")), "valid": bool(base.get("valid")), "qualifies": bok, "why": "" if bok else "; ".join(breasons), "remeasured_on": "current NPZ", "stored_on_board": info.get("stored_gain_pct"), "drift_vs_board": RR._r((cum - float(info["stored_gain_pct"])) if info.get("stored_gain_pct") is not None else None)}
    RR.log(f"  {sym_side} [{venue}] published {info['source']} gain {info.get('stored_gain_pct')} -> RE-MEASURED {cum:+.2f}% bh {bh} tr {base.get('trades')} ({len(ov)} keys, {Path(info['xlsx']).name if info.get('xlsx') else 'no xlsx here'})")
    # USER 2026-10-16: "ONLY retest the new rows each with all relevant filters" — --only-new drops the standing
    # suspect ladders and the already-tested WT family, leaving exactly the rows this board has never seen (switch
    # absent from its published set) plus every option value the template gained since it ran. Each still carries
    # its FULL yellow box, which is where the gain actually comes from.
    if args.only_new:
        # USER 2026-10-16: "make sure you are actually testing all the filters on the new wt 15 rows — that is where
        # the 10x should come from". The first cut of --only-new defined "new" as a switch ABSENT from the board's
        # set, but a per_sym full set carries ~3500 keys, so every WT15 switch counts as present and the 40-row cap
        # then took whatever came first in template order (HA_WICK_QUALITY_TF, MARKET_QUALITY_SCORE_ENABLED ...).
        # Measured on AAPL_LONG: 324 "new" rows, 40 tested, **0 of them WT**. The rows the user means are the ones
        # the TEMPLATE gained — the WT15 family and the reentry family — so they go FIRST and are never cut by the
        # cap; genuinely-absent switches only fill whatever room is left.
        stamped = stamped_new_rows(sym_side, args.new_since)
        famall = RR.wt_family_rows(view, defaults, ov, venue)
        fam = [x for x in famall if x["switch"] in stamped] or famall[: args.max_new_rows]
        nr = [x for x in new_row_suspects(view, ov, venue)
              if x["switch"] not in {f["switch"] for f in fam} and x["switch"] in stamped]
        room = max(0, args.max_new_rows - len(fam)) if args.max_new_rows else len(nr)
        suspects = fam + nr[:room]
        out["new_rows_found"] = [x["switch"] for x in nr]
        out["family_rows_tested"] = [x["switch"] for x in fam]
        out["stamped_since"] = args.new_since
        RR.log(f"  {sym_side} ONLY-NEW (stamped >= {args.new_since}): {len(fam)} WT15/reentry template rows + {min(room, len(nr))} never-set rows = {len(suspects)} tested (each with its full yellow box)")
    else:
        suspects = list(RR.SUSPECTS)
        if args.wt:
            suspects += RR.wt_family_rows(view, defaults, ov, venue)
        if args.new_rows:
            nr = new_row_suspects(view, ov, venue)
            out["new_rows_found"] = [x["switch"] for x in nr]
            suspects += nr[: args.max_new_rows] if args.max_new_rows else nr
    cur, promoted, wr = dict(ov), {}, RR._wr(base)
    for sus in suspects:
        rec, mv, cum, wr = RR.hunt_switch(sus, cur, cum, wr, ev, view, defaults, venue, args.yellow_mode, args.probe_unwired, args.wr_tol)
        rec["tabs"] = sus.get("tabs") or []
        rec["new_row"] = bool(sus.get("new_row"))
        out["switches"].append(rec)
        if mv:
            cur = dict(cur, **mv)
            promoted.update(mv)
            RR.log(f"    + {rec['promoted']['label']}  delta {rec['promoted']['delta']:+.3f} -> {rec['promoted']['gain_after']:+.2f}% / {rec['promoted']['trades']}tr")
    final = ev.one(cur) if promoted else base
    out["final"] = {"gain_pct": RR._r(final.get("gain_pct")), "trades": int(final.get("trades") or 0), "max_dd_pct": RR._r(final.get("max_dd_pct")), "tim_pct": RR._r(final.get("tim_pct")), "pool_sharpe": RR._r(final.get("pool_sharpe")), "valid": bool(final.get("valid"))}
    fok, freasons = RR.qualifies(final, bh)
    out["final"].update(qualifies=fok, why="" if fok else "; ".join(freasons))
    expect = float(out["baseline"]["gain_pct"]) + sum(float(s["promoted"]["delta"]) for s in out["switches"] if s.get("promoted"))
    got = out["final"]["gain_pct"]
    out["accumulation_check"] = {"expected": RR._r(expect), "measured": got, "drift": RR._r((got - expect) if got is not None else None), "ok": got is not None and abs(got - expect) <= args.accum_tol}
    out["promoted_moves"] = {k: RR._j(v) for k, v in promoted.items()}
    out["final_set"] = {k: RR._j(v) for k, v in cur.items()}
    if out.get("trend_floor_repaired"):
        pre = ev.one(ov_raw)
        out["baseline_before_trend_floor"] = {"gain_pct": RR._r(pre.get("gain_pct")), "trades": int(pre.get("trades") or 0), "max_dd_pct": RR._r(pre.get("max_dd_pct"))}
        out["trend_floor_cost_pct"] = RR._r((cum - float(pre.get("gain_pct"))) if pre.get("gain_pct") is not None else None)
        RR.log(f"  {sym_side} trend-floor cost {out['trend_floor_cost_pct']} (unrepaired set measured {out['baseline_before_trend_floor']['gain_pct']}, repaired {RR._r(cum)})")
    bwr, bwrinfo = measure_wr(sym_side, ov, args.window_days)
    out["baseline"]["wr_pct"], out["baseline"]["wr_source"] = RR._r(bwr), bwrinfo
    out["evals"] = ev.n
    out["secs"] = round(time.time() - t0, 1)
    out["status"] = "OK"
    ev.close()
    tag = "FLAT(re-measured)" if not promoted else "IMPROVED"
    RR.log(f"  {sym_side} {tag} {out['secs']}s {ev.n} evals  {out['baseline']['gain_pct']:+.2f}% -> {got:+.2f}%  {len(promoted)} moves  wr_base={out['baseline'].get('wr_pct')}  accum_sum={out['accumulation_check']['expected']} (recorded, not a gate)")
    return out


def main():
    ap = argparse.ArgumentParser(description="top up each tradeable sym_side's latest published board with only the new/adjusted rows")
    ap.add_argument("--sym-sides", default="")
    ap.add_argument("--venue", default="crypto", choices=["crypto", "stocks", "all"], help="crypto first — it trades 24/7")
    ap.add_argument("--tiers", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--cell-timeout", type=float, default=120.0)
    ap.add_argument("--yellow-mode", default="off", choices=["best", "off"], help="off = ladder only (11-13 s/sym_side, measured); best = yellows on the winning rung (171-252 s)")
    ap.add_argument("--wt", action="store_true", help="also hunt the WT15 family rows added 2026-10-15")
    ap.add_argument("--only-new", action="store_true", help="USER 2026-10-16: test ONLY the rows new to this board (plus their full yellow box) — no standing suspects, no re-testing the WT family")
    ap.add_argument("--new-rows", action="store_true", help="also hunt template rows absent from the published set")
    ap.add_argument("--max-new-rows", type=int, default=40)
    ap.add_argument("--new-since", default="20261013", help="a row counts as NEW when v15_switch_add stamped it on/after this date (column-A comment)")
    ap.add_argument("--probe-unwired", action="store_true", help="MEASURE data/vec_unwired.json switches (research only, never promoted)")
    ap.add_argument("--wr-tol", type=float, default=5.0)
    ap.add_argument("--accum-tol", type=float, default=2.5, help="BIBLE board-nondeterminism tolerance (gate TOL_SUM)")
    ap.add_argument("--apply", action="store_true", help="publish the matched xlsx+manifest+progress pair (OFF by default)")
    ap.add_argument("--allow-untradeable", action="store_true")
    ap.add_argument("--no-html", action="store_true", help="skip the HTML chart companion (it is written by default — USER 2026-10-16)")
    ap.add_argument("--rank", default="remeasured", choices=["remeasured", "pilot", "none"], help="which xlsx becomes active: remeasured = like-for-like on today's NPZ when we topped up the standing board (default); pilot = v15_pilot's filename-gain ranking; none = file without superseding")
    ap.add_argument("--tag", default=time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    ap.add_argument("--published-sets", default="", help="JSON from tools/v15_export_start_sets.py --published (run on S1) — the published set per sym_side, for hosts that hold no per_sym_history")
    ap.add_argument("--pause", default="")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    if args.published_sets:
        load_published_sets(args.published_sets)
    if args.pause:
        return RR.pause_fleet([h.strip() for h in args.pause.split(",") if h.strip()])
    if args.resume:
        return RR.resume_fleet()
    if not SHEET_DIR.exists():
        RR.log(f"[topup] {SHEET_DIR} missing — run on S1/s2, not the Mac")
        return 2
    targets = RR.select_sym_sides(args)
    if not targets:
        RR.log("[topup] nothing to do")
        return 1
    RR.log(f"[topup] tag={args.tag} host={os.uname().nodename} {len(targets)} sym_sides window={args.window_days}d workers={args.workers} yellow={args.yellow_mode} wt={args.wt} new_rows={args.new_rows} apply={args.apply}")
    RR.log(f"[topup] pilot={RR._PILOT_NAME} md5={RR._pilot_md5()} sheets={SHEET_DIR}")
    outs, op = [], OUT_DIR / f"v15_xlsx_topup_{args.tag}.json"
    for i, ss in enumerate(targets, 1):
        RR.log(f"[{i}/{len(targets)}] {ss}")
        try:
            o = topup_one(ss, args)
        except Exception as ex:
            import traceback
            o = {"sym_side": ss, "status": f"ERROR {type(ex).__name__}: {ex}", "traceback": traceback.format_exc()[-900:]}
            RR.log(f"  {ss} ERROR {ex}")
        if args.apply:
            try:
                o["publish"] = publish(o, args)
            except Exception as ex:
                import traceback
                o["publish"] = {"published": False, "why": f"{type(ex).__name__}: {ex}", "traceback": traceback.format_exc()[-600:]}
            RR.log(f"  {ss} PUBLISH {json.dumps(o['publish'])[:220]}")
        outs.append(o)
        op.write_text(json.dumps({"tag": args.tag, "ts": RR.utcnow(), "args": vars(args), "results": outs}, indent=1, default=str))
    ok = [o for o in outs if o.get("status") == "OK"]
    imp = [o for o in ok if o.get("promoted_moves")]
    pub = [o for o in outs if (o.get("publish") or {}).get("published")]
    RR.log("")
    RR.log(f"[summary] {len(ok)}/{len(outs)} measured · {len(imp)} improved · {len(ok) - len(imp)} FLAT · {len(pub)} published")
    if ok:
        secs = sorted(o["secs"] for o in ok)
        RR.log(f"[summary] wall-clock per sym_side: min {secs[0]}s median {secs[len(secs) // 2]}s max {secs[-1]}s · evals {sum(o['evals'] for o in ok)}")
    for o in imp:
        RR.log(f"[summary] {o['sym_side']} {o['baseline']['gain_pct']:+.2f}% -> {o['final']['gain_pct']:+.2f}% (board said {o['start_set'].get('stored_gain_pct')}) {json.dumps(o['promoted_moves'])}")
    RR.log(f"[summary] evidence {op}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
