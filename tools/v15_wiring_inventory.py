#!/usr/bin/env python3
"""v15_wiring_inventory — documentation tab WIRING_INVENTORY in the 4 TEMPLATE_*.xlsx (INV agent, 2026-10-01, USER order).

One row per switch row / orange filter row of every switch tab (+ one row per yellow filter column per tab) with its wiring state:
live_real (crypto/stocks), vector_real, class, pos_sym, n_sym, avg_delta, priority, owner/next action, evidence file:line.
PRIORITY RULE (user): a function that exists in the vector but not in live and has pos_sym > 0 is P1 (wire into live first); every other
non-BOTH_WIRED row is P2 (fix down the line). The tab is DOCUMENTATION ONLY: pilots never read it. The sweep reads SPREADSHEETS/TEMPLATE_FINAL_NORM
from which tools/v15_template_normalize_defaults.py strips this tab (pilots clone lean sheets, no slow-down).

Sources: data/SWITCH_BIBLE.json (per-venue status), data/wiring/unwired_report_20261001.csv, data/wiring/unw/DEAD_TRIAGE.csv,
data/wiring/unw/L_STATUS.md (UNW-L FINAL TABLE), data/rowcoverage/latest.csv (row list + status), data/avg_delta_pos_sym.json (pos_sym/n_sym/avg_delta).

  python tools/v15_wiring_inventory.py --dry-run                      # build + print counts, write nothing
  python tools/v15_wiring_inventory.py --out-dir DIR                  # write painted COPIES of SPREADSHEETS/TEMPLATE_*.xlsx into DIR
  TEMPLATES_UNFREEZE=1 python tools/v15_wiring_inventory.py --apply   # in place (backup to backups/run_inv_<ts>/, row guard + all-sheet compare)
  python tools/v15_wiring_inventory.py --ensure                       # --apply only when the tab is missing or its input stamp is stale
  python tools/v15_wiring_inventory.py --strip --templates DIR        # remove the tab from copies in DIR (used for lean sweep copies)
Idempotent: the tab is deleted and rebuilt (last sheet). Exit code 0 on success."""
import argparse
import collections
import csv
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import template_row_guard as G  # noqa: E402

TAB = "WIRING_INVENTORY"
CATS = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
SWITCH_SHEETS = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
HEAD = ["tab", "name", "option", "kind", "live_real (crypto/stocks)", "vector_real", "class", "pos_sym", "n_sym", "avg_delta", "priority", "owner / next action", "evidence file:line", "sweep status"]
FILL = {"P1": "FFFFC7CE", "BOTH_WIRED": "FFC6EFCE", "VECTOR_ONLY": "FFFFEB9C", "LIVE_ONLY": "FFDDEBF7", "DEAD_BOTH": "FFD9D9D9", "GHOST": "FFBFBFBF", "FILTER_TF_UNDEFINED": "FFE4DFEC", "ABLATION_BLOCKED": "FFFCE4D6", "UNCLASSIFIED": "FFFFFFFF"}
SRC = {
    "bible": ROOT / "data" / "SWITCH_BIBLE.json",
    "unwired": ROOT / "data" / "wiring" / "unwired_report_20261001.csv",
    "triage": ROOT / "data" / "wiring" / "unw" / "DEAD_TRIAGE.csv",
    "lstatus": ROOT / "data" / "wiring" / "unw" / "L_STATUS.md",
    "cover": ROOT / "data" / "rowcoverage" / "latest.csv",
    "pos": ROOT / "data" / "avg_delta_pos_sym.json",
}


def input_stamp():
    h = hashlib.md5()
    for k in sorted(SRC):
        p = SRC[k]
        h.update(f"{k}:{p.stat().st_size if p.exists() else 0}:{int(p.stat().st_mtime) if p.exists() else 0}".encode())
    return h.hexdigest()[:12]


def expand_braces(tok):
    """'A_{B,C}_D' -> ['A_B_D','A_C_D'] (one level; enough for L_STATUS)."""
    m = re.search(r"\{([^{}]+)\}", tok)
    if not m:
        return [tok]
    return [x for part in m.group(1).split(",") for x in expand_braces(tok[: m.start()] + part.strip() + tok[m.end():])]


def parse_lstatus():
    """name -> 'WIRED' | 'BLOCKED' | 'ALREADY_LIVE' from the FINAL TABLE of UNW-L (tags only; free text kept short)."""
    out = {}
    if not SRC["lstatus"].exists():
        return out
    txt = SRC["lstatus"].read_text(errors="ignore")
    i = txt.find("FINAL TABLE")
    if i < 0:
        return out
    seg, mode = txt[i:], None
    for line in seg.splitlines():
        s = line.strip()
        if s.startswith("WIRED NOW"):
            mode = "WIRED"
            continue
        if s.startswith("ALREADY LIVE"):
            mode = "ALREADY_LIVE"
        elif s.startswith("BLOCKED"):
            mode = "BLOCKED"
            continue
        if not mode:
            continue
        head = s.split(" — ")[0] if " — " in s else s.split(":")[0]
        for tok in re.findall(r"[A-Z][A-Z0-9_]{5,}(?:\{[^{}]+\}[A-Z0-9_]*)?", head):
            for n in expand_braces(tok):
                if n.isupper() and "_" in n:
                    out.setdefault(n, mode)
    return out


def load_sources():
    d = {}
    d["bible"] = json.loads(SRC["bible"].read_text())["switches"]
    d["unwired"] = {r["name"]: r for r in csv.DictReader(open(SRC["unwired"]))} if SRC["unwired"].exists() else {}
    d["triage"] = {r["name"]: r for r in csv.DictReader(open(SRC["triage"]))} if SRC["triage"].exists() else {}
    d["lstatus"] = parse_lstatus()
    d["cover"] = collections.defaultdict(list)
    for r in csv.DictReader(open(SRC["cover"])):
        d["cover"][r["template"]].append(r)
    d["pos"] = json.loads(SRC["pos"].read_text())
    return d


def venue_of(cs):
    return "stocks" if cs.startswith("STOCKS") else "crypto"


def wiring(name, cs, S):
    """-> (live_real_str, vector_real_str, class, note, evidence)"""
    v = venue_of(cs)
    e = S["bible"].get(name)
    tri = S["triage"].get(name)
    uw = S["unwired"].get(name)
    ls = S["lstatus"].get(name)
    st = {"crypto": None, "stocks": None}
    if e:
        st.update({k: (str(x) if x else None) for k, x in (e.get("status") or {}).items()})
    base = lambda s: (s or "").split("+")[0]
    staged = lambda s: "+STAGED_VEC" in (s or "")

    def live_flag(s):
        return "Y" if base(s) in ("LIVE_ONLY", "WIRED_BOTH_UNPROVEN") else "N"
    lr = f"{live_flag(st['crypto'])}/{live_flag(st['stocks'])}" if e else "?/?"
    vs = st[v]
    vr = "Y" if base(vs) in ("VEC_ONLY", "WIRED_BOTH_UNPROVEN") else ("S" if staged(vs) else "N") if e else "?"
    note = []
    ev = ""
    if e:
        reads = (e.get("live_reads") or {}).get(v) or []
        vecs = e.get("vec_reads") or []
        ev = (reads[0] if reads else (vecs[0] if vecs else ""))
    tcls = (tri or {}).get("class", "")[:1] if tri else ""
    if tri:
        ev = tri.get("evidence_file_line") or ev
        note.append((tri.get("proposed_action") or "")[:140])
    cls = None
    if name.endswith("_FILTER_TF") and uw and uw.get("class") == "FILTER_TF_FAMILY":
        cls = "FILTER_TF_UNDEFINED"
    elif tcls == "b":
        cls = "ABLATION_BLOCKED"
    elif tcls == "c":
        cls = "GHOST"
    elif tcls in ("d", "e"):
        cls = "DEAD_BOTH"
    elif tri and (tri.get("class") or "").startswith("RECLASSIFY"):
        cls = "LIVE_ONLY"
    elif e:
        cls = {"WIRED_BOTH_UNPROVEN": "BOTH_WIRED", "VEC_ONLY": "VECTOR_ONLY", "LIVE_ONLY": "LIVE_ONLY", "DEAD": "DEAD_BOTH"}.get(base(vs), "UNCLASSIFIED")
    else:
        cls = "UNCLASSIFIED"
    if ls == "WIRED":
        lr = "Y/Y"
        note.append("UNW-L: live twin wired default OFF (BIBLE scan lags)")
        if vr in ("Y", "S"):
            cls = "BOTH_WIRED"
        elif cls in ("VECTOR_ONLY", "DEAD_BOTH", "UNCLASSIFIED", "LIVE_ONLY"):
            cls = "LIVE_ONLY"
            note.append("vector twin pending (UNW-V)")
    elif ls == "BLOCKED":
        note.append("UNW-L: blocked (ablation flag / strategy decision / no definition)")
        if cls in ("VECTOR_ONLY", "DEAD_BOTH", "UNCLASSIFIED"):
            cls = "ABLATION_BLOCKED" if cls != "VECTOR_ONLY" else cls
    elif ls == "ALREADY_LIVE":
        lr = "Y/Y"
        note.append("UNW-L: already live (stocks loop), vector cannot model")
    return lr, vr, cls, "; ".join(x for x in note if x), ev


def build_rows(cs, S):
    pos = S["pos"].get(cs, {})
    rows, yellow = [], collections.OrderedDict()
    for r in S["cover"].get(cs, []):
        tab, kind, name, cand = r["tab"], r["kind"], r["switch"], r["cand"]
        p = pos.get(f"{tab}!{name}={cand}") or {}
        if kind == "yellow":
            y = yellow.setdefault((tab, name), {"opts": [], "pos": None, "n": 0, "avg": None, "st": collections.Counter(), "owner": r["owner"]})
            y["opts"].append(cand)
            if p.get("pos_sym") is not None:
                y["pos"] = max(y["pos"] or 0, p["pos_sym"])
            y["n"] = max(y["n"], p.get("n_sym") or 0)
            if p.get("avg_delta") is not None:
                y["avg"] = p["avg_delta"] if y["avg"] is None else max(y["avg"], p["avg_delta"])
            y["st"][r["status"]] += 1
            continue
        rows.append((tab, name, cand, "switch row" if kind == "switch" else "orange filter row", p, r))
    out = []
    for tab, name, cand, kind, p, r in rows:
        out.append(_row(cs, S, tab, name, cand, kind, p.get("pos_sym"), p.get("n_sym"), p.get("avg_delta"), r["status"], r["owner"]))
    for (tab, name), y in yellow.items():
        out.append(_row(cs, S, tab, name, f"ALL ({len(y['opts'])} opts)", "yellow filter column", y["pos"], y["n"], y["avg"], "/".join(f"{k}:{v}" for k, v in y["st"].most_common(2)), y["owner"]))
    order = {t: i for i, t in enumerate(SWITCH_SHEETS)}
    korder = {"switch row": 0, "orange filter row": 1, "yellow filter column": 2}
    out.sort(key=lambda x: (order.get(x[0], 99), korder.get(x[3], 9), x[1], str(x[2])))
    return out


def _row(cs, S, tab, name, opt, kind, pos, n, avg, status, owner):
    lr, vr, cls, note, ev = wiring(name, cs, S)
    prio = "-"
    if cls != "BOTH_WIRED":
        prio = "P1" if (cls == "VECTOR_ONLY" and (pos or 0) > 0) else "P2"
    act = ""
    if prio == "P1":
        act = f"WIRE INTO LIVE FIRST (vector-only, pos_sym={pos}); owner {owner}"
    elif prio == "P2":
        act = f"fix down the line; owner {owner}"
    if note:
        act = (act + "; " if act else "") + note
    return [tab, name, opt, kind, lr, vr, cls, pos if pos is not None else "", n if n else "", round(avg, 4) if isinstance(avg, (int, float)) else "", prio, act[:300], ev, status]


def write_tab(wb, cs, rows, stamp):
    if TAB in wb.sheetnames:
        del wb[TAB]
    ws = wb.create_sheet(TAB)
    cnt = collections.Counter(r[6] for r in rows)
    p1 = sorted([r for r in rows if r[10] == "P1"], key=lambda r: -(r[7] or 0))
    ws["A1"] = f"WIRING_INVENTORY — {cs} — documentation only, never read by pilots (generated {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%MZ')}, input stamp {stamp})"
    ws["A1"].font = Font(bold=True, size=12)
    ws["A2"] = "Rule: vector function without a live consumer and pos_sym>0 = P1 (wire into live first); every other non-BOTH_WIRED row = P2 (fix down the line; weekend task after more numbers). live_real: crypto/stocks, vector_real: Y/N/S(staged). Rebuild: python tools/v15_wiring_inventory.py --apply"
    ws["A3"] = "Counts: " + " | ".join(f"{k}={v}" for k, v in sorted(cnt.items(), key=lambda x: -x[1])) + f" | P1={len(p1)} P2={sum(1 for r in rows if r[10] == 'P2')} total={len(rows)}"
    ws["A3"].font = Font(bold=True)
    r = 5
    ws.cell(r, 1, f"P1 — vector-only with pos_sym > 0 ({len(p1)} rows): wire into LIVE first").font = Font(bold=True, color="FF9C0006")
    r += 1
    for j, h in enumerate(HEAD, 1):
        c = ws.cell(r, j, h)
        c.font = Font(bold=True, color="FFFFFFFF")
        c.fill = PatternFill("solid", start_color="FF1F4E78", end_color="FF1F4E78")
    r += 1
    for row in p1:
        for j, v in enumerate(row, 1):
            ws.cell(r, j, v)
        ws.cell(r, 7).fill = PatternFill("solid", start_color=FILL["P1"], end_color=FILL["P1"])
        r += 1
    r += 1
    ws.cell(r, 1, f"FULL INVENTORY ({len(rows)} rows)").font = Font(bold=True)
    r += 1
    top = r
    for j, h in enumerate(HEAD, 1):
        c = ws.cell(r, j, h)
        c.font = Font(bold=True, color="FFFFFFFF")
        c.fill = PatternFill("solid", start_color="FF1F4E78", end_color="FF1F4E78")
    r += 1
    for row in rows:
        for j, v in enumerate(row, 1):
            ws.cell(r, j, v)
        col = FILL["P1"] if row[10] == "P1" else FILL.get(row[6], "FFFFFFFF")
        ws.cell(r, 7).fill = PatternFill("solid", start_color=col, end_color=col)
        r += 1
    ws.auto_filter.ref = f"A{top}:{openpyxl.utils.get_column_letter(len(HEAD))}{r - 1}"
    for col, w in zip("ABCDEFGHIJKLMN", (24, 44, 16, 20, 14, 10, 22, 8, 8, 10, 8, 70, 46, 18)):
        ws.column_dimensions[col].width = w
    ws.sheet_properties.tabColor = "FF7F7F7F"
    return p1


def snapshot(wb):
    out = {}
    for ws in wb.worksheets:
        if ws.title == TAB:
            continue
        d = {}
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None or (c.fill is not None and c.fill.fill_type == "solid"):
                    f = c.fill
                    d[(c.row, c.column)] = (repr(c.value), str(getattr(f.fgColor, "rgb", "")) if f is not None and f.fill_type == "solid" else "", bool(c.font.b) if c.font else False)
        out[ws.title] = d
    return out


def stamp_of(path):
    wb = openpyxl.load_workbook(str(path), read_only=True)
    try:
        if TAB not in wb.sheetnames:
            return None
        v = wb[TAB]["A1"].value or ""
        m = re.search(r"input stamp ([0-9a-f]+)", v)
        return m.group(1) if m else None
    finally:
        wb.close()


def process(src, dst, cs, S, stamp, write, backup_dir=None):
    wb = openpyxl.load_workbook(str(src))
    before = snapshot(wb)
    rows = build_rows(cs, S)
    p1 = write_tab(wb, cs, rows, stamp)
    after = snapshot(wb)
    bad = []
    if before.keys() != after.keys():
        bad.append("sheet set of other tabs changed")
    for k in before:
        if before[k] != after.get(k):
            bad.append(f"{k}: cell values/fills/bold differ")
    ref = openpyxl.load_workbook(str(src))  # pristine copy: row guard (fingerprint of every row, all columns) on the 13 switch sheets
    for tab in SWITCH_SHEETS:
        if tab in wb.sheetnames and tab in ref.sheetnames:
            try:
                G.assert_rows_intact(ref[tab], wb[tab])
            except AssertionError as e:
                bad.append(str(e))
    if bad:
        return rows, p1, bad
    if write:
        if backup_dir:
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, backup_dir / Path(src).name)
        tmp = Path(str(dst) + f".tmp{os.getpid()}.xlsx")
        wb.save(str(tmp))
        wb2 = openpyxl.load_workbook(str(tmp), read_only=True)
        ok = TAB in wb2.sheetnames
        wb2.close()
        if not ok:
            tmp.unlink()
            return rows, p1, ["saved file lacks the tab"]
        tmp.replace(dst)
    return rows, p1, []


def strip(path):
    wb = openpyxl.load_workbook(str(path))
    if TAB not in wb.sheetnames:
        return False
    del wb[TAB]
    tmp = Path(str(path) + f".tmp{os.getpid()}.xlsx")
    wb.save(str(tmp))
    openpyxl.load_workbook(str(tmp), read_only=True).close()
    tmp.replace(path)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--templates", default=str(ROOT / "SPREADSHEETS"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ensure", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-dir")
    ap.add_argument("--strip", action="store_true")
    ap.add_argument("--p1-csv", default=str(ROOT / "data" / "wiring" / "P1_vector_only_pos.csv"))
    a = ap.parse_args()
    tdir = Path(a.templates)
    if a.strip:
        for cs in CATS:
            p = tdir / f"TEMPLATE_{cs}.xlsx"
            if p.exists():
                print(f"[{cs}] stripped={strip(p)}")
        return
    inplace = a.apply or a.ensure
    if inplace and tdir.resolve() == (ROOT / "SPREADSHEETS").resolve() and (ROOT / "SPREADSHEETS" / "TEMPLATES_FROZEN").exists() and not os.environ.get("TEMPLATES_UNFREEZE"):
        sys.exit("REFUSED: SPREADSHEETS/TEMPLATES_FROZEN exists (set TEMPLATES_UNFREEZE=1 only for an approved change)")
    if not SRC["cover"].exists():
        print(f"[inventory] skip: {SRC['cover']} missing (row-coverage --run has not produced latest.csv on this host) — docs tab left as-is")
        return
    S = load_sources()
    stamp = input_stamp()
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M")
    bdir = ROOT / "backups" / f"run_inv_{ts}"
    allp1, summary = [], {}
    for cs in CATS:
        src = tdir / f"TEMPLATE_{cs}.xlsx"
        if not src.exists():
            print(f"[{cs}] missing {src}")
            continue
        if a.ensure and stamp_of(src) == stamp:
            print(f"[{cs}] up to date (stamp {stamp})")
            continue
        if a.out_dir:
            Path(a.out_dir).mkdir(parents=True, exist_ok=True)
            dst, write = Path(a.out_dir) / src.name, True
        elif inplace:
            dst, write = src, True
        else:
            dst, write = src, False
        rows, p1, bad = process(src, dst, cs, S, stamp, write, bdir if (inplace and not a.out_dir) else None)
        cnt = collections.Counter(r[6] for r in rows)
        summary[cs] = {"rows": len(rows), "P1": len(p1), "P2": sum(1 for r in rows if r[10] == "P2"), **dict(cnt)}
        print(f"[{cs}] rows={len(rows)} P1={len(p1)} classes={dict(cnt)} violations={len(bad)} {bad[:2]} {'-> ' + str(dst) if write and not bad else '(not written)'}")
        if bad:
            sys.exit(2)
        allp1 += [[cs] + r for r in p1]
    if allp1 and not a.strip:
        Path(a.p1_csv).parent.mkdir(parents=True, exist_ok=True)
        with open(a.p1_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["template"] + HEAD)
            w.writerows(sorted(allp1, key=lambda r: (r[0], -(r[8] or 0))))
        print(f"P1 list: {a.p1_csv} ({len(allp1)} rows)")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
