#!/usr/bin/env python3
"""v15_zero_delta_watchdog — systematic-0 correction across sym_sides (USER 2026-10-03).

A 0.0 delta on ONE sym_side is noise. avg==0.0 AND median==0.0 over >= --min-n sym_sides
for a NON-default value is a dead switch (vec ignores it) burning fleet compute on every
sheet. This tool finds them from the cross-sym aggregate and:
  - report (default): classify each systematic-0 as EXPLAINED_HONEST (row IS the current
    default: default-vs-itself) or DEAD_SUSPECT, write data/reports/v15_zero_delta_<ts>.json.
  - --apply: append switches whose EVERY measured option is systematic-zero (>=2 options)
    to data/reports/lifecycle_pilot/disabled_switches_never_pos_per_category.json (the
    pilot's sanctioned skip list; KG switches are never added — pilot purges them anyway).
    Backup first. Does NOT touch templates.
"""
import argparse
import collections
import datetime
import json
import shutil
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
SPREAD = ROOT / "SPREADSHEETS"
AGG_DEFAULT = SPREAD / "v15_vector_delta_latest.xlsx"
TEMPLATES = {"CRYPTO_LONG": "TEMPLATE_CRYPTO_LONG.xlsx", "CRYPTO_SHORT": "TEMPLATE_CRYPTO_SHORT.xlsx", "STOCKS_LONG": "TEMPLATE_STOCKS_LONG.xlsx", "STOCKS_SHORT": "TEMPLATE_STOCKS_SHORT.xlsx"}
SWITCH_SHEETS = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
DISABLED = ROOT / "data" / "reports" / "lifecycle_pilot" / "disabled_switches_never_pos_per_category.json"
HDR = 2
KG_SUBSTR = ("HTF_", "MTF_", "MTS_", "WT_", "W15M", "TOP_OF_RANGE", "GR_FILTER", "GR_", "ADX_", "BB_SQUEEZE", "COUNTER_TREND", "DELTA_REENTRY", "EXIT_BLOCKER", "MANDATORY_REENTRY", "OPEN_RATE")


def _is_kg(sw):
    return any(k in str(sw) for k in KG_SUBSTR)


def load_agg(path):
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    out = {}
    for cs in TEMPLATES:
        d = {}
        if cs in wb.sheetnames:
            rows = wb[cs].iter_rows(values_only=True)
            hdr = [str(h) for h in next(rows)]
            col = "avg_delta" if "avg_delta" in hdr else "vector_delta"
            ix = {h: hdr.index(h) for h in ("tab", "name", col, "n")}
            imed = hdr.index("median_delta") if "median_delta" in hdr else None
            for r in rows:
                if r and r[ix["name"]] is not None and r[ix[col]] is not None:
                    med = float(r[imed]) if imed is not None and r[imed] is not None else None
                    d[(str(r[ix["tab"]]).strip(), str(r[ix["name"]]).strip())] = (float(r[ix[col]]), int(r[ix["n"]] or 0), med)
        out[cs] = d
    wb.close()
    return out


def load_bolds(cs):
    wb = openpyxl.load_workbook(str(SPREAD / TEMPLATES[cs]), read_only=True, data_only=True)
    bolds = {}
    for tab in SWITCH_SHEETS:
        if tab not in wb.sheetnames:
            continue
        ws = wb[tab]
        ci = next((c for c in range(1, ws.max_column + 1) if str(ws.cell(row=HDR, column=c).value or "").strip().upper() == "IS_DEFAULT"), None)
        for row in ws.iter_rows(min_row=HDR + 1, values_only=False):
            a = row[0].value
            if a in (None, ""):
                continue
            if ci is not None and str(row[ci - 1].value or "").strip().upper() == "YES":
                bolds[(tab, str(a).strip())] = row[1].value
    wb.close()
    return bolds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agg", default=str(AGG_DEFAULT))
    ap.add_argument("--min-n", type=int, default=8)
    ap.add_argument("--cat-side", default="ALL")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M")
    agg = load_agg(Path(args.agg))
    report = {"agg": str(args.agg), "min_n": args.min_n, "cats": {}}
    for cs in (list(TEMPLATES) if args.cat_side == "ALL" else [args.cat_side]):
        stats = agg[cs]
        bolds = load_bolds(cs)
        explained, suspect = [], []
        for (tab, name), (avg, n, med) in stats.items():
            if not (avg == 0.0 and n >= args.min_n and (med is None or med == 0.0)):
                continue
            if "=" in name and (tab, name.split("=", 1)[0].strip()) in bolds:
                sw = name.split("=", 1)[0].strip()
                cur = bolds[(tab, sw)]
                if str(cur) == name.split("=", 1)[1].strip() or repr(cur) == name.split("=", 1)[1].strip():
                    explained.append([tab, name, n])
                    continue
                suspect.append([tab, name, n])
            else:
                suspect.append([tab, name, n])
        by_sw = collections.defaultdict(list)
        for tab, name, n in suspect:
            by_sw[name.split("=", 1)[0].strip() if "=" in name else name].append((tab, name, n))
        disablable = sorted(s for s, rows in by_sw.items() if len(rows) >= 2 and not _is_kg(s))
        kg_blocked = sorted(s for s, rows in by_sw.items() if len(rows) >= 2 and _is_kg(s))
        report["cats"][cs] = {"systematic_zero_rows": len(explained) + len(suspect), "explained_honest": len(explained), "dead_suspect_rows": len(suspect), "disablable_switches": disablable, "kg_blocked": kg_blocked, "suspect_sample": suspect[:10]}
        print(f"[{cs}] stats={len(stats)} syszero={len(explained) + len(suspect)} explained={len(explained)} suspect_rows={len(suspect)} disablable={len(disablable)} {disablable[:6]} kg_blocked={len(kg_blocked)}", flush=True)
    out = ROOT / "data" / "reports" / f"v15_zero_delta_{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    print(f"[report] {out}", flush=True)
    if args.apply:
        cur = json.loads(DISABLED.read_text()) if DISABLED.exists() else {}
        shutil.copy2(DISABLED, ROOT / "backups" / f"before_zero_watchdog_{ts}_disabled.json") if DISABLED.exists() else None
        added = 0
        for cs, r in report["cats"].items():
            have = set(cur.get(cs) or [])
            for s in r["disablable_switches"]:
                if s not in have:
                    have.add(s)
                    added += 1
            cur[cs] = sorted(have)
        DISABLED.parent.mkdir(parents=True, exist_ok=True)
        DISABLED.write_text(json.dumps(cur, indent=1))
        print(f"[apply] disabled += {added} switches -> {DISABLED}", flush=True)


if __name__ == "__main__":
    main()
