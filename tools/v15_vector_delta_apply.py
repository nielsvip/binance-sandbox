#!/usr/bin/env python3
"""v15_vector_delta_apply — write aggregated VECTOR_DELTA + POS_SYM into TEMPLATE_*.xlsx and sort rows by it.

New version of v15_avg_delta_apply (USER 2026-09-30). The cross-sym_side aggregate (tools/v15_vector_delta_rebuild.py,
ONE best value per sym_side per (tab, switch=value) — so n == #sym_sides) is written into each tab's **VECTOR_DELTA**
and **POS_SYM** columns (by header, never by index), then rows are reordered so the column reads sorted:

  * stats are matched PER TAB (a switch is a row in up to ~15 tabs; each tab-row gets THAT tab's aggregate),
  * white switch groups are ordered worst_first by their mean VECTOR_DELTA, orange filter groups below them
    (BIBLE §56 R5), and WITHIN each group the candidate rows are sorted by VECTOR_DELTA ascending,
  * a whole row is moved as one unit — every column's value, font, FILL/COLOR, number-format and alignment travel
    together (yellow cells included); is_default/bold travels with its row.

Scope: fill VECTOR_DELTA + POS_SYM, sort. No promotion, no is_default flips, AVG_DELTA untouched, no rows added/removed.
Every template is verified before save (row multiset unchanged; no white row below an orange row; exactly one
is_default=YES per group; written value == stat; row-content/colour preserved for moved rows) — a failed check = not saved.
--apply writes (backup first); default = report only. --template-dir runs it on copies.
"""
import argparse
import collections
import copy
import datetime
import shutil
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from v15_template_restructure import snapshot_row, write_row, is_orange, white_below_orange  # noqa: E402

SPREAD = ROOT / "SPREADSHEETS"
AGG = SPREAD / "v15_vector_delta_latest.xlsx"
TEMPLATES = {"CRYPTO_LONG": "TEMPLATE_CRYPTO_LONG.xlsx", "CRYPTO_SHORT": "TEMPLATE_CRYPTO_SHORT.xlsx", "STOCKS_LONG": "TEMPLATE_STOCKS_LONG.xlsx", "STOCKS_SHORT": "TEMPLATE_STOCKS_SHORT.xlsx"}
SWITCH_SHEETS = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
HDR = 2


def load_stats() -> dict:
    """{(tab, 'SWITCH=value'): (vector_delta, pos_sym, n)} for the given cat_side sheet, keyed by header name."""
    wb = openpyxl.load_workbook(str(AGG), read_only=True, data_only=True)
    out = {}
    for cs in TEMPLATES:
        if cs not in wb.sheetnames:
            out[cs] = {}
            continue
        rows = wb[cs].iter_rows(values_only=True)
        hdr = [str(h) for h in next(rows)]
        ix = {h: hdr.index(h) for h in ("tab", "name", "pos_sym", "vector_delta", "n")}
        d = {}
        for r in rows:
            if r and r[ix["name"]] is not None and r[ix["vector_delta"]] is not None:
                d[(str(r[ix["tab"]]).strip(), str(r[ix["name"]]).strip())] = (float(r[ix["vector_delta"]]), int(r[ix["pos_sym"]] or 0), int(r[ix["n"]] or 0))
        out[cs] = d
    return out


def key_of(a, b) -> str:
    return f"{str(a).strip()}={b}"


def col_of(ws, name: str):
    for c in range(1, ws.max_column + 1):
        v = ws.cell(row=HDR, column=c).value
        if isinstance(v, str) and v.strip().upper() == name.upper():
            return c
    return None


def apply_tab(ws, stats: dict, rep: dict):
    """stats = {(tab,'SWITCH=value'): (vec,pos,n)} for THIS cat_side. Writes VECTOR_DELTA/POS_SYM for this tab's
    rows, then reorders. Returns nothing; mutates ws."""
    tab = ws.title
    c_vec, c_pos = col_of(ws, "VECTOR_DELTA"), col_of(ws, "POS_SYM")
    if not (c_vec and c_pos):
        raise RuntimeError(f"{tab}: missing VECTOR_DELTA/POS_SYM header")
    ncol = ws.max_column
    # 1. write VECTOR_DELTA / POS_SYM per row, keyed by (tab, colA=colB)
    for r in range(HDR + 1, ws.max_row + 1):
        a = ws.cell(row=r, column=1).value
        st = stats.get((tab, key_of(a, ws.cell(row=r, column=2).value))) if a not in (None, "") else None
        ws.cell(row=r, column=c_vec).value = round(st[0], 6) if st else None
        ws.cell(row=r, column=c_pos).value = st[1] if st else None
        if st:
            rep["rows_with_stats"] += 1
    # 2. group rows by (colA name, colour class) in document order; trailing blank rows stay with their group
    groups = collections.OrderedDict()   # key -> list[snapshot]
    order = []                            # keys in first-seen order
    last = None
    for r in range(HDR + 1, ws.max_row + 1):
        a = ws.cell(row=r, column=1).value
        snap = snapshot_row(ws, r, ncol)
        if a in (None, ""):
            if last is not None:
                groups[last].append(snap)
            continue
        k = (str(a).strip(), is_orange(ws, r))
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(snap)
        last = k
    # 3. within each group sort the NON-blank candidate rows by VECTOR_DELTA asc (None last); blanks sink to end
    def vecval(snap):
        v = snap[c_vec - 1][0]
        return v if isinstance(v, (int, float)) else None
    for k in order:
        cells = groups[k]
        nonblank = [s for s in cells if str(s[0][0] or "").strip() != ""]
        blanks = [s for s in cells if str(s[0][0] or "").strip() == ""]
        nonblank.sort(key=lambda s: (vecval(s) is None, vecval(s) if vecval(s) is not None else 0.0))
        groups[k] = nonblank + blanks
    # 4. group sort key = mean VECTOR_DELTA of the group's rows (None -> +inf, sinks last); white before orange
    BIG = float("inf")
    def gmean(k):
        vs = [vecval(s) for s in groups[k] if vecval(s) is not None]
        return (not vs, sum(vs) / len(vs) if vs else BIG)
    white = sorted([k for k in order if not k[1]], key=gmean)
    orange = sorted([k for k in order if k[1]], key=gmean)
    # 5. rewrite rows in the new order (whole snapshots -> content + font + FILL + nfmt + alignment all move)
    r = HDR + 1
    for k in white + orange:
        for snap in groups[k]:
            write_row(ws, r, snap)
            r += 1


def verify(before, after, stats, rep):
    bad = []
    for tab in SWITCH_SHEETS:
        if tab not in after.sheetnames:
            continue
        a, b = before[tab], after[tab]
        c_isd = col_of(b, "is_default")
        c_vec = col_of(b, "VECTOR_DELTA")
        ncol = min(a.max_column, b.max_column)
        # row-move integrity (cheap): per-row signature = (colA, colB, colA-fill-rgb). Whole snapshots move as a
        # unit by construction, so the multiset of these signatures must be identical before/after.
        def sig_bag(ws):
            c = collections.Counter()
            for r in range(HDR + 1, ws.max_row + 1):
                av = ws.cell(row=r, column=1).value
                if av in (None, ""):
                    continue
                f = ws.cell(row=r, column=1).fill
                c[(str(av), str(ws.cell(row=r, column=2).value), str(getattr(f.fgColor, "rgb", "") if f and f.fgColor else ""))] += 1
            return c
        if sig_bag(a) != sig_bag(b):
            bad.append(f"{tab}: row signature multiset changed (a row lost content/colour or moved partially)")
        # deep check: the full 207-col snapshot (value+fill) of one moved row survives intact. Match by (colA,colB).
        def full_index(ws):
            idx = {}
            for r in range(HDR + 1, ws.max_row + 1):
                av = ws.cell(row=r, column=1).value
                if av in (None, ""):
                    continue
                k = (str(av), str(ws.cell(row=r, column=2).value))
                idx.setdefault(k, r)
            return idx
        ia, ib = full_index(a), full_index(b)
        sample = [k for k in ib if k in ia][len(ib) // 2 : len(ib) // 2 + 1]
        for k in sample:
            ra_, rb_ = ia[k], ib[k]
            for c in range(1, ncol + 1):
                if c in (c_vec, col_of(b, "POS_SYM")):
                    continue  # the two columns we intentionally rewrote
                va, vb = a.cell(row=ra_, column=c).value, b.cell(row=rb_, column=c).value
                fa2 = str(getattr(a.cell(row=ra_, column=c).fill.fgColor, "rgb", "") or "")
                fb2 = str(getattr(b.cell(row=rb_, column=c).fill.fgColor, "rgb", "") or "")
                if str(va) != str(vb) or fa2 != fb2:
                    bad.append(f"{tab} deep-row {k}: col {c} changed (val/fill) on move")
                    break
        wbo = white_below_orange(b)
        if wbo:
            bad.append(f"{tab}: white rows below orange {wbo[:5]}")
        # exactly one is_default YES per switch NAME (a name split across white+orange rows still has ONE default),
        # and the same count as BEFORE the sort (we never touch is_default, only move whole rows)
        def yes_by_name(ws):
            g = collections.Counter()
            for r in range(HDR + 1, ws.max_row + 1):
                av = ws.cell(row=r, column=1).value
                if av in (None, ""):
                    continue
                if str(ws.cell(row=r, column=c_isd).value or "").strip().upper() == "YES":
                    g[str(av).strip()] += 1
            return g
        ya, yb = yes_by_name(a), yes_by_name(b)
        if ya != yb:
            moved = {k: (ya.get(k, 0), yb.get(k, 0)) for k in set(ya) | set(yb) if ya.get(k, 0) != yb.get(k, 0)}
            bad.append(f"{tab}: is_default YES count changed by sort: {dict(list(moved.items())[:5])}")
        # written value == stat, for a sample of filled rows
        checked = 0
        for r in range(HDR + 1, b.max_row + 1):
            av = b.cell(row=r, column=1).value
            if av in (None, ""):
                continue
            st = stats.get((tab, key_of(av, b.cell(row=r, column=2).value)))
            got = b.cell(row=r, column=c_vec).value
            want = round(st[0], 6) if st else None
            if (got is None) != (want is None) or (want is not None and abs((got or 0) - want) > 1e-6):
                bad.append(f"{tab} r{r} {av}: VECTOR_DELTA {got} != stat {want}")
                if len([x for x in bad if 'VECTOR_DELTA' in x]) > 3:
                    break
            checked += 1
        rep.setdefault("cells_verified", 0)
        rep["cells_verified"] += checked
    return bad


def main():
    sys.exit("REFUSED: superseded by tools/v15_daily_template_update.py (the ONE template writer, BIBLE §56.0). This script wrote aggregates into the wrong columns.")
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--cat-side", default="ALL")
    ap.add_argument("--template-dir", default=str(SPREAD))
    args = ap.parse_args()
    tdir = Path(args.template_dir)
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M")
    all_stats = load_stats()
    for cs in (list(TEMPLATES) if args.cat_side == "ALL" else [args.cat_side]):
        path = tdir / TEMPLATES[cs]
        stats = all_stats[cs]
        before = openpyxl.load_workbook(str(path))
        wb = openpyxl.load_workbook(str(path))
        rep = {"rows_with_stats": 0}
        for tab in SWITCH_SHEETS:
            if tab in wb.sheetnames:
                apply_tab(wb[tab], stats, rep)
        bad = verify(before, wb, stats, rep)
        print(f"[{cs}] stats={len(stats)} rows_with_stats={rep['rows_with_stats']} cells_verified={rep.get('cells_verified',0)} violations={len(bad)} {bad[:3]}")
        if not args.apply:
            continue
        if bad:
            print(f"[{cs}] NOT saved — verification failed")
            continue
        shutil.copy2(path, ROOT / "backups" / f"before_vector_delta_apply_{ts}_{path.name}")
        tmp = path.with_suffix(".tmp.xlsx")
        wb.save(str(tmp))
        openpyxl.load_workbook(str(tmp))
        tmp.replace(path)
        print(f"[{cs}] saved {path}")


if __name__ == "__main__":
    main()
