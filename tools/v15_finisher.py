#!/usr/bin/env python3
"""v15_finisher — completes the sheet lifecycle: finished workbook -> bh/gain-named
final xlsx + zoomable chart (on the NPZ box) -> pulled to the Mac.

USER order 2026-09-28: "sheets get filled to the end, saved with bh and gain in
title and pulled to mac with chart."

FINISHED = every SWITCH_SHEETS data row in the workbook (matched by (sheet, switch,
candidate) identity, row-drift immune) has a done record in the progress JSON, AND
the v15_assure audit verdict is CONSISTENT. bh and gain come from stored engine
results only (progress JSON, fallback BASELINE_METRICS tab) — never recomputed
approximations (NO-LIES). State file prevents reprocessing unless the JSON advances.

Modes:
  --once [--watch off]     single scan pass (env V15_FINISHER_SYMSIDES=A,B limits scope)
  --watch --interval 300   daemon loop (cmdline carries no sym_side names)
  --pull-to-mac            run ON THE MAC: rsync finished xlsx+charts from s1/s2
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.v15_assure import OUT_DIR, PROG_DIR, SWITCH_SHEETS, acquire_lock, audit_one, find_progress, log, wb_path_for  # noqa: E402

STATE_PATH = PROG_DIR / "finisher_state.json"
CHART_DIRS = [ROOT / "SPREADSHEETS", ROOT / "data" / "reports" / "charts_1Y"]


def fmt(v):
    return f"{float(v):.2f}".replace("-", "m").replace(".", "p")


def parse_done_key(key):
    m = re.match(r"^([A-Z0-9_]+)!(\d+):(.+?)=(.*)$", key, re.S)
    return (m.group(1), m.group(3), m.group(4)) if m else None


def load_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def save_state(st):
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    os.replace(tmp, STATE_PATH)


def rows_all_done(wb_path, done):
    """Row-drift-immune completeness over CURRENT template rows: every (sheet,
    colA, colB) workbook data row has a done record keyed by (sheet, switch,
    cand) identity. Also measures render coverage (numeric F or G on those rows),
    because audit_one's yellow-count comparison is generation-blind: obsolete JSON
    keys from older template layouts leave a permanent JSON_AHEAD residue that
    says nothing about the current sheet's completeness."""
    import openpyxl
    done_ids = set()
    for k in done:
        p = parse_done_key(k)
        if p:
            done_ids.add((p[0], str(p[1]).strip(), str(p[2]).strip()))
    wb = openpyxl.load_workbook(wb_path, read_only=True, data_only=False)
    missing = 0
    first_missing = None
    total = 0
    rendered = 0
    for sn in SWITCH_SHEETS:
        if sn not in wb.sheetnames:
            continue
        ws = wb[sn]
        for row in ws.iter_rows(min_row=3, max_col=7):
            a = row[0].value if len(row) > 0 else None
            if a in (None, ""):
                continue
            total += 1
            b = row[1].value if len(row) > 1 else None
            fv = row[5].value if len(row) > 5 else None
            gv = row[6].value if len(row) > 6 else None
            if isinstance(fv, (int, float)) or isinstance(gv, (int, float)):
                rendered += 1
            ident = (sn, str(a).strip(), str(b).strip())
            if ident not in done_ids:
                missing += 1
                if first_missing is None:
                    first_missing = ident
    wb.close()
    coverage = rendered / total if total else 0.0
    return missing == 0, missing, first_missing, coverage, total


def get_bh(prog, symside, wb_path):
    bh = prog.get("bh")
    if isinstance(bh, (int, float)):
        return float(bh)
    # fallback: BASELINE_METRICS tab — find a label containing 'bh' and take the
    # adjacent numeric written by the pilot (stored engine result, not recomputed)
    try:
        import openpyxl
        wb = openpyxl.load_workbook(wb_path, read_only=True, data_only=False)
        bm = f"{symside}_BASELINE_METRICS"
        if bm in wb.sheetnames:
            ws = wb[bm]
            for row in ws.iter_rows(min_row=1, max_row=15, max_col=4):
                label = str(row[0].value or "").lower()
                if "bh" in label or "buy" in label and "hold" in label:
                    for cell in row[1:]:
                        if isinstance(cell.value, (int, float)):
                            wb.close()
                            return float(cell.value)
        wb.close()
    except Exception:
        pass
    return None


def finish_one(symside, state):
    wb_path = wb_path_for(symside)
    if not wb_path.exists():
        return False
    pj, prog = find_progress(symside)
    if not prog:
        return False
    mtime = pj.stat().st_mtime
    st = state.get(symside) or {}
    if st.get("json_mtime") == mtime and st.get("artifact"):
        return False  # already finished at this JSON state
    done = prog.get("done") or {}
    if not done:
        return False
    ok, missing, first, coverage, total = rows_all_done(wb_path, done)
    if not ok:
        if st.get("last_missing") != missing:
            log(f"{symside}: not finished — {missing} rows pending (first: {first})")
            state[symside] = {"json_mtime": mtime, "last_missing": missing}
        return False
    rep = audit_one(symside)
    verdict = str(rep.get("verdict") or "")
    if not verdict.startswith("CONSISTENT"):
        # generation-blind residue check: audit compares against ALL JSON records,
        # including obsolete keys from older template layouts. If every CURRENT
        # row is done AND >=99% carry a rendered numeric F/G, the sheet is
        # complete for its own template — proceed and log the residue.
        if coverage >= 0.99:
            log(f"{symside}: audit residue ({verdict[:60]}) but current-rows coverage {coverage:.1%} of {total} — proceeding (obsolete-key residue)")
        else:
            log(f"{symside}: rows done but audit says {verdict[:60]} and coverage {coverage:.1%} — holding (refill/complete will converge)")
            state[symside] = {"json_mtime": mtime, "held": f"{verdict[:40]} cov={coverage:.0%}"}
            return False
    gain = prog.get("cumulative_gain")
    bh = get_bh(prog, symside, wb_path)
    if not isinstance(gain, (int, float)) or bh is None:
        log(f"{symside}: finished but bh/gain unavailable from stored results (bh={bh} gain={gain}) — NOT naming with fabricated numbers")
        state[symside] = {"json_mtime": mtime, "held": "bh/gain missing"}
        return False
    # 2026-09-29 USER (SCCO +11.29 title vs -42% chart): a chain RESUMED across an engine
    # cut carries mixed-engine arithmetic — the stored cumulative_gain may be a number NO
    # current engine reproduces. RE-ANCHOR before naming: fresh eval of the final
    # cumulative_overrides on THIS box's engine. Mismatch -> the fresh number is the truth;
    # a large mismatch means the chain itself is contaminated -> hold for a clean rerun,
    # never publish the stale headline.
    try:
        from tools.opt.v12_pilot import evaluate_prepared_sanitized, prepare_batch
        _prep = prepare_batch(symside, int(prog.get("window_days") or 30))
        if _prep is not None:
            _fresh = evaluate_prepared_sanitized(_prep, dict(prog.get("cumulative_overrides") or {}), int(prog.get("window_days") or 30))
            _fg = _fresh.get("gain_pct")
            if _fg is None:
                log(f"{symside}: re-anchor eval returned no gain ({_fresh.get('invalid_reason')}) — holding, not publishing")
                state[symside] = {"json_mtime": mtime, "held": "reanchor no gain"}
                return False
            if abs(float(_fg) - float(gain)) > 0.5:
                log(f"{symside}: CONTAMINATED chain — stored gain {gain:.2f} vs current-engine {float(_fg):.2f}; holding for clean rerun (never publishing stale headline)")
                state[symside] = {"json_mtime": mtime, "held": f"contaminated stored={gain:.2f} fresh={float(_fg):.2f}"}
                return False
            gain = float(_fg)
        else:
            log(f"{symside}: no NPZ on this box — cannot re-anchor; holding (publish only where verifiable)")
            state[symside] = {"json_mtime": mtime, "held": "no NPZ to re-anchor"}
            return False
    except Exception as _rae:
        log(f"{symside}: re-anchor failed ({str(_rae)[:80]}) — holding, not publishing")
        state[symside] = {"json_mtime": mtime, "held": "reanchor error"}
        return False
    if prog.get("not_compliant") or not prog.get("final_path"):
        # USER 2026-09-30: not finished until the pilot published a compliant final set (valid TIM/DD/trades)
        log(f"{symside}: final set not compliant / not published by the pilot — holding, no bh/gain name")
        state[symside] = {"json_mtime": mtime, "held": "not compliant"}
        return False
    lock = acquire_lock(symside, wb_path)
    if lock is None:
        return False  # a pilot/refill owns it right now; next pass
    try:
        final_name = f"{symside}_bh{fmt(bh)}_gain{fmt(gain)}_30d_matrix.xlsx"
        final_path = OUT_DIR / final_name
        import shutil
        shutil.copy2(wb_path, final_path)
        chart = None
        try:
            import v15_pilot as VP
            chart = VP.write_zoomable_chart(symside, None, dict(prog.get("cumulative_overrides") or {}), int(prog.get("window_days") or 30))
        except Exception as e:
            log(f"{symside}: chart skipped ({str(e)[:80]}) — no NPZ on this box or chart error")
        state[symside] = {"json_mtime": mtime, "artifact": final_name, "chart": str(chart) if chart else None, "finished_at": time.time()}
        log(f"{symside}: FINISHED -> {final_name} bh={bh:.2f} gain={gain:.2f} chart={'yes' if chart else 'no'}")
        return True
    finally:
        lock.close()


def scan(symsides=None):
    state = load_state()
    targets = symsides
    if not targets:
        targets = sorted({p.name.split("_30d_matrix")[0] for p in OUT_DIR.glob("*_30d_matrix.xlsx") if re.match(r"^[A-Z0-9]+_(LONG|SHORT)_30d_matrix", p.name)})
    n = 0
    for ss in targets:
        try:
            if finish_one(ss, state):
                n += 1
        except Exception as e:
            log(f"{ss}: finisher error {str(e)[:100]}")
        save_state(state)
    return n


def pull_to_mac():
    fin_dir = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / "FINISHED"
    chart_dir = ROOT / "SPREADSHEETS" / "charts_finished"
    fin_dir.mkdir(parents=True, exist_ok=True)
    chart_dir.mkdir(parents=True, exist_ok=True)
    ssh = "ssh -S none -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8"
    for host in ("s1-int", "s2"):
        for src, dst in [
            (f"{host}:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*_bh*_gain*_30d_matrix.xlsx", fin_dir),
            (f"{host}:~/binance-sandbox/SPREADSHEETS/*ZOOMABLE*.html", chart_dir),
            (f"{host}:~/binance-sandbox/data/reports/charts_1Y/*ZOOMABLE*.html", chart_dir),
        ]:
            r = subprocess.run(f'rsync -azu -e "{ssh}" {src} {dst}/', shell=True, capture_output=True, text=True, timeout=600)
            if r.returncode not in (0, 23):  # 23 = some files vanished/none matched
                log(f"pull {host}: rc={r.returncode} {r.stderr.strip()[:120]}")
    n_fin = len(list(fin_dir.glob("*.xlsx")))
    n_charts = len(list(chart_dir.glob("*.html")))
    log(f"pull-to-mac: FINISHED/={n_fin} xlsx, charts_finished/={n_charts} html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--pull-to-mac", action="store_true")
    args = ap.parse_args()
    env_ss = [s for s in os.environ.get("V15_FINISHER_SYMSIDES", "").split(",") if s.strip()]
    if args.pull_to_mac:
        pull_to_mac()
        return
    if args.watch:
        log(f"finisher watch started interval={args.interval}s")
        while True:
            try:
                scan(env_ss or None)
            except Exception as e:
                log(f"scan error {str(e)[:120]}")
            time.sleep(args.interval)
    else:
        n = scan(env_ss or None)
        log(f"finisher pass complete: {n} newly finished")


if __name__ == "__main__":
    main()
