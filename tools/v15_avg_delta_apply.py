#!/usr/bin/env python3
"""v15_avg_delta_apply — the ONE daily writer of avg-delta results into TEMPLATE_{cat_side}.xlsx (USER 2026-09-30).

Input: SPREADSHEETS/v15_avg_delta_latest.xlsx (tools/v15_avg_delta_rebuild.py: per cat_side, rows name/kind/pos_sym/avg_delta/
median_delta/n; switch names are "SWITCH=value", filter names are the yellow header "FILTER=opt").
Per template, per SWITCH_SHEETS tab:
  1. AVG_DELTA / POS_SYM written into the columns with THOSE HEADERS (never by position) for every row with stats.
  2. PROMOTION (the only place defaults change): within each (tab, switch) group that is not grey, the row with the highest
     POSITIVE avg_delta becomes bold + is_default=YES; the previous default becomes regular + is_default=NO. Same for every
     yellow filter: the "FILTER=opt" header with the highest positive avg_delta becomes the bold header with the DEFAULT note.
     Every promotion is recorded in data/cat_side_promotions.json (read by tools/v15_template_defaults_fix.py so a promoted
     default is never 'fixed' back to the config value). Live configs are NOT changed here (no per-cat_side config yet).
  3. worst_first: switch groups re-ordered by the mean avg_delta of their rows, most negative first, groups without data
     last; white switch groups always above orange filter groups; whole rows move (all cells, yellows, fonts, fills).
Rows are never added or removed. Every template is verified before it is saved (row multiset unchanged, no white row below
an orange row, exactly one is_default YES per group = the bold row, one bold header per filter); a failed check = not saved.
--apply writes (backup first); default = report only. --template-dir lets you run it on copies.
"""
import argparse
import collections
import copy
import datetime
import json
import shutil
import sys
from pathlib import Path

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Font

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from v15_template_restructure import snapshot_row, write_row, is_orange, white_below_orange  # noqa: E402

SPREAD = ROOT / "SPREADSHEETS"
AVG = SPREAD / "v15_avg_delta_latest.xlsx"
PROMOTIONS = ROOT / "data" / "cat_side_promotions.json"
TEMPLATES = {"CRYPTO_LONG": "TEMPLATE_CRYPTO_LONG.xlsx", "CRYPTO_SHORT": "TEMPLATE_CRYPTO_SHORT.xlsx", "STOCKS_LONG": "TEMPLATE_STOCKS_LONG.xlsx", "STOCKS_SHORT": "TEMPLATE_STOCKS_SHORT.xlsx"}
SWITCH_SHEETS = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
HDR = 2
GREY = "FFBFBFBF"
EPS = 1e-9


def load_stats(cat_side: str) -> dict:
    # keyed by (tab, "NAME=value") — USER 2026-09-30: avg_delta is per-tab now, so promotion uses THIS tab's stats
    # (a filter promoted on ENTRY must not inherit its EXIT-tab average). Older tab-less sheets fall back to tab="".
    wb = openpyxl.load_workbook(str(AVG), read_only=True, data_only=True)
    if cat_side not in wb.sheetnames:
        return {}
    rows = wb[cat_side].iter_rows(values_only=True)
    hdr = [str(h) for h in next(rows)]
    ix = {h: hdr.index(h) for h in ("name", "pos_sym", "avg_delta", "n") if h in hdr}
    itab = hdr.index("tab") if "tab" in hdr else None
    out = {}
    for r in rows:
        if r and r[ix["name"]] is not None and r[ix["avg_delta"]] is not None:
            tab = str(r[itab]).strip() if itab is not None else ""
            out[(tab, str(r[ix["name"]]).strip())] = (float(r[ix["avg_delta"]]), int(r[ix["pos_sym"]] or 0), int(r[ix["n"]] or 0))
    return out


def key_of(a, b) -> str:
    # same spelling as the pilot's progress keys "SWITCH=cand" (cand = the raw B cell value)
    return f"{str(a).strip()}={b}"


def col_of(ws, name: str):
    for c in range(1, ws.max_column + 1):
        v = ws.cell(row=HDR, column=c).value
        if isinstance(v, str) and v.strip().lower().startswith(name.lower()):
            return c
    return None


def is_grey(ws, r) -> bool:
    f = ws.cell(row=r, column=1).font
    return f is not None and f.color is not None and str(getattr(f.color, "rgb", "") or "").upper() == GREY


def set_bold(cell, bold):
    f = cell.font
    cell.font = Font(name=(f.name if f else None) or "Arial", size=(f.size if f else None) or 10, bold=bold, italic=f.italic if f else None, color=f.color if f else None)


def groups_of(ws):
    g = collections.OrderedDict()
    for r in range(HDR + 1, ws.max_row + 1):
        a = ws.cell(row=r, column=1).value
        if a in (None, ""):
            continue
        g.setdefault(str(a).strip(), []).append(r)
    return g


def apply_tab(ws, stats: dict, ledger: dict, rep: dict):
    c_isd, c_avg, c_pos = col_of(ws, "is_default"), col_of(ws, "AVG_DELTA"), col_of(ws, "POS_SYM")
    if not (c_isd and c_avg and c_pos):
        raise RuntimeError(f"{ws.title}: missing is_default/AVG_DELTA/POS_SYM header (run tools/v15_template_fixed_cols.py)")
    # 1. AVG_DELTA / POS_SYM by header
    for r in range(HDR + 1, ws.max_row + 1):
        a = ws.cell(row=r, column=1).value
        st = stats.get((ws.title, key_of(a, ws.cell(row=r, column=2).value))) if a not in (None, "") else None
        ws.cell(row=r, column=c_avg).value = round(st[0], 6) if st else None
        ws.cell(row=r, column=c_pos).value = st[1] if st else None
        if st:
            rep["rows_with_stats"] += 1
    # 2. promotion per switch group
    for sw, rows in groups_of(ws).items():
        if is_grey(ws, rows[0]):
            continue
        cand = [(stats[(ws.title, key_of(sw, ws.cell(row=r, column=2).value))][0], r) for r in rows if (ws.title, key_of(sw, ws.cell(row=r, column=2).value)) in stats]
        cand = [(a, r) for a, r in cand if a > EPS]
        if not cand:
            continue
        best_avg, best = max(cand)
        cur = [r for r in rows if str(ws.cell(row=r, column=c_isd).value or "").strip().upper() == "YES"]
        if cur == [best]:
            continue
        for r in rows:
            yes = r == best
            set_bold(ws.cell(row=r, column=2), yes)
            ws.cell(row=r, column=c_isd).value = "YES" if yes else "NO"
            ws.cell(row=r, column=c_isd).font = Font(name="Arial", size=10, bold=yes)
        val = ws.cell(row=best, column=2).value
        ledger[sw] = {"value": val, "avg_delta": best_avg, "tab": ws.title, "at": datetime.datetime.utcnow().isoformat() + "Z"}
        rep["promoted_switch"].append([ws.title, sw, [ws.cell(row=r, column=2).value for r in cur], val, round(best_avg, 4)])
    # 2b. promotion per yellow filter (header default)
    by_f = collections.OrderedDict()
    for c in range(1, ws.max_column + 1):
        h = ws.cell(row=HDR, column=c).value
        if isinstance(h, str) and "=" in h:
            by_f.setdefault(h.split("=", 1)[0].strip(), []).append(c)
    for f, cs in by_f.items():
        cand = [(stats[(ws.title, ws.cell(row=HDR, column=c).value.strip())][0], c) for c in cs if (ws.title, ws.cell(row=HDR, column=c).value.strip()) in stats]
        cand = [(a, c) for a, c in cand if a > EPS]
        if not cand:
            continue
        best_avg, best = max(cand)
        cur = [c for c in cs if ws.cell(row=HDR, column=c).font is not None and ws.cell(row=HDR, column=c).font.b]
        if cur == [best]:
            continue
        for c in cs:
            h = ws.cell(row=HDR, column=c)
            set_bold(h, c == best)
            if c == best:
                h.comment = Comment("DEFAULT", "v15_avg_delta_apply")
            elif h.comment is not None and h.comment.text.strip() == "DEFAULT":
                h.comment = None
        opt = ws.cell(row=HDR, column=best).value.split("=", 1)[1]
        ledger[f] = {"value": opt, "avg_delta": best_avg, "tab": ws.title, "at": datetime.datetime.utcnow().isoformat() + "Z"}
        rep["promoted_filter"].append([ws.title, f, [ws.cell(row=HDR, column=c).value for c in cur], opt, round(best_avg, 4)])
    # 3. worst_first re-order: groups by (switch, colour class), whole rows move, white above orange
    ncol = ws.max_column
    grp = collections.OrderedDict()
    last = None
    for r in range(HDR + 1, ws.max_row + 1):
        a = ws.cell(row=r, column=1).value
        if a in (None, ""):
            if last is not None:
                grp[last].append(snapshot_row(ws, r, ncol))
            continue
        k = (str(a).strip(), is_orange(ws, r))
        grp.setdefault(k, [])
        grp[k].append(snapshot_row(ws, r, ncol))
        last = k
    def gavg(k):
        vals = [cells[c_avg - 1][0] for cells in grp[k] if isinstance(cells[c_avg - 1][0], (int, float))]
        return (not vals, sum(vals) / len(vals) if vals else 0.0)
    order = [k for k in grp if not k[1]]
    order.sort(key=gavg)
    orange = [k for k in grp if k[1]]
    orange.sort(key=gavg)
    r = HDR + 1
    for k in order + orange:
        for cells in grp[k]:
            write_row(ws, r, cells)
            r += 1


def verify(before: openpyxl.Workbook, after: openpyxl.Workbook) -> list:
    bad = []
    for tab in SWITCH_SHEETS:
        if tab not in after.sheetnames:
            continue
        a, b = before[tab], after[tab]
        ms = lambda ws: collections.Counter((str(ws.cell(row=r, column=1).value), str(ws.cell(row=r, column=2).value)) for r in range(HDR + 1, ws.max_row + 1) if ws.cell(row=r, column=1).value)
        if ms(a) != ms(b):
            bad.append(f"{tab}: row set changed")
        if white_below_orange(b):
            bad.append(f"{tab}: white rows below orange {white_below_orange(b)[:5]}")
        c_isd = col_of(b, "is_default")
        for sw, rows in groups_of(b).items():
            yes = [r for r in rows if str(b.cell(row=r, column=c_isd).value or "").strip().upper() == "YES"]
            bold = [r for r in rows if b.cell(row=r, column=2).font is not None and b.cell(row=r, column=2).font.b]
            if len(yes) != 1 or yes != bold:
                bad.append(f"{tab}!{sw}: YES {yes} bold {bold}")
        by_f = collections.defaultdict(list)
        for c in range(1, b.max_column + 1):
            h = b.cell(row=HDR, column=c).value
            if isinstance(h, str) and "=" in h:
                by_f[h.split("=", 1)[0].strip()].append(c)
        for f, cs in by_f.items():
            if sum(1 for c in cs if b.cell(row=HDR, column=c).font is not None and b.cell(row=HDR, column=c).font.b) != 1:
                bad.append(f"{tab}: filter {f} bold headers != 1")
    return bad


def main():
    sys.exit("REFUSED: superseded by tools/v15_daily_template_update.py (the ONE template writer, BIBLE §56.0). This script wrote aggregates into the wrong columns.")
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--cat-side", default="ALL")
    ap.add_argument("--template-dir", default=str(SPREAD), help="directory holding the TEMPLATE_*.xlsx to update (copies for tests)")
    args = ap.parse_args()
    tdir = Path(args.template_dir)
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M")
    ledger_all = json.loads(PROMOTIONS.read_text()) if PROMOTIONS.exists() else {}
    report = {}
    for cs in (list(TEMPLATES) if args.cat_side == "ALL" else [args.cat_side]):
        path = tdir / TEMPLATES[cs]
        stats = load_stats(cs)
        before = openpyxl.load_workbook(str(path))
        wb = openpyxl.load_workbook(str(path))
        ledger = dict(ledger_all.get(cs) or {})
        rep = {"rows_with_stats": 0, "promoted_switch": [], "promoted_filter": []}
        for tab in SWITCH_SHEETS:
            if tab in wb.sheetnames:
                apply_tab(wb[tab], stats, ledger, rep)
        bad = verify(before, wb)
        report[cs] = {**rep, "violations": bad}
        print(f"[{cs}] stats={len(stats)} rows_with_stats={rep['rows_with_stats']} promoted switches={len(rep['promoted_switch'])} filters={len(rep['promoted_filter'])} violations={len(bad)} {bad[:3]}")
        if not args.apply:
            continue
        if bad:
            print(f"[{cs}] NOT saved — verification failed")
            continue
        shutil.copy2(path, ROOT / "backups" / f"before_avg_delta_apply_{ts}_{path.name}")
        tmp = path.with_suffix(".tmp.xlsx")
        wb.save(str(tmp))
        openpyxl.load_workbook(str(tmp))
        tmp.replace(path)
        ledger_all[cs] = ledger
        print(f"[{cs}] saved {path}")
    if args.apply and tdir.resolve() == SPREAD.resolve():
        PROMOTIONS.write_text(json.dumps(ledger_all, indent=1, default=str))
        # the FOUR-default layer (cat_side_defaults.py -> live managers, sweep engine, pilot) follows the new bold defaults
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "tools" / "build_cat_side_defaults_4.py")], check=True)
    out = ROOT / "data" / "reports" / f"v15_avg_delta_apply_{ts}.json"
    out.write_text(json.dumps(report, indent=1, default=str))
    print(f"[report] {out}")


if __name__ == "__main__":
    main()
