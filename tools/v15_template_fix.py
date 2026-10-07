#!/usr/bin/env python3
"""Repair TEMPLATE_{STOCKS,CRYPTO}_{LONG,SHORT}.xlsx structure (2026-09-29 audit).

1. Delete duplicated yellow header columns (keep the LAST occurrence = the column v15_pilot/v15_assure map).
2. Row-2 BASELINE header case (REENTRY_ADAPTIVE had "Baseline").
3. DC_BREACH_REDUCE_FILTER_TF rows in GLOBAL_RISK_GATES = OFF (bold default) / 15m / 1h / 4h / D in every template.
4. is_default backup column agrees with the bold default (bold wins; is_default only restores bold when a group lost it).
5. Report (no change): empty-candidate rows, orange FILTER rows above white SWITCH rows, groups with != 1 default.
"""
import argparse
import copy
import shutil
import sys
import time
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill

ROOT = Path(__file__).resolve().parent.parent
TABS = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
ORANGE = "FFFFE699"
DC_BREACH = "DC_BREACH_REDUCE_FILTER_TF"
DC_BREACH_VALUES = ["OFF", "15m", "1h", "4h", "D"]


def is_orange(cell):
    return cell.fill is not None and cell.fill.fill_type == "solid" and str(cell.fill.fgColor.rgb or "").upper() == ORANGE


def isdef_col(ws):
    for c in range(1, ws.max_column + 1):
        v = ws.cell(row=2, column=c).value
        if isinstance(v, str) and v.lower().startswith("is_default"):
            return c
    return None


def dedupe_columns(ws):
    seen = {}
    for c in range(12, ws.max_column + 1):
        v = ws.cell(row=2, column=c).value
        if isinstance(v, str) and "=" in v:
            seen.setdefault(v.strip(), []).append(c)
    drop = sorted((c for cols in seen.values() for c in cols[:-1]), reverse=True)
    if not drop:
        return 0
    merged = [str(m) for m in ws.merged_cells.ranges]
    for m in merged:
        ws.unmerge_cells(m)
    for c in drop:
        ws.delete_cols(c)
    for m in merged:
        if m.startswith("A1:"):
            ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ws.max_column)
        else:
            print(f"    WARN non-title merge {m} dropped on {ws.title}", flush=True)
    return len(drop)


def fix_dc_breach(ws, report):
    rows = [r for r in range(3, ws.max_row + 1) if ws.cell(row=r, column=1).value == DC_BREACH]
    idc = isdef_col(ws)
    if rows:
        anchor = rows[0]
        template_row = anchor
        for r, v in zip(rows, DC_BREACH_VALUES):
            ws.cell(row=r, column=2).value = v
        extra = DC_BREACH_VALUES[len(rows):]
        insert_at = rows[-1] + 1
        surplus = rows[len(DC_BREACH_VALUES):]
        for r in reversed(surplus):
            ws.delete_rows(r)
    else:
        oranges = [r for r in range(3, ws.max_row + 1) if is_orange(ws.cell(row=r, column=1))]
        insert_at = (oranges[-1] + 1) if oranges else ws.max_row + 1
        template_row = oranges[-1] if oranges else ws.max_row
        extra = DC_BREACH_VALUES
    if extra:
        ws.insert_rows(insert_at, amount=len(extra))
        src = template_row if template_row < insert_at else template_row + len(extra)
        for i, v in enumerate(extra):
            r = insert_at + i
            for c in range(1, ws.max_column + 1):
                s = ws.cell(row=src, column=c)
                d = ws.cell(row=r, column=c)
                d.font = copy.copy(s.font)
                d.fill = copy.copy(s.fill) if c <= 11 or c == idc else PatternFill(fill_type=None)
                d.alignment = copy.copy(s.alignment)
                d.border = copy.copy(s.border)
                d.value = None
            ws.cell(row=r, column=1).value = DC_BREACH
            ws.cell(row=r, column=2).value = v
            ws.cell(row=r, column=1).fill = PatternFill(start_color=ORANGE, end_color=ORANGE, fill_type="solid")
    rows = [r for r in range(3, ws.max_row + 1) if ws.cell(row=r, column=1).value == DC_BREACH]
    for r in rows:
        b = ws.cell(row=r, column=2)
        default = b.value == "OFF"
        b.font = Font(name=b.font.name or "Arial", size=b.font.size or 10, bold=default)
        if idc:
            ws.cell(row=r, column=idc).value = "YES" if default else "NO"
    report.append(f"{ws.title}: {DC_BREACH} rows {[ws.cell(row=r, column=2).value for r in rows]} (OFF bold)")


def same_value(cand, live):
    if cand is None or live is None:
        return False
    if isinstance(live, bool) or str(cand).strip().lower() in ("true", "false"):
        return str(cand).strip().lower() == str(live).strip().lower()
    try:
        return abs(float(cand) - float(live)) < 1e-9
    except (TypeError, ValueError):
        return str(cand).strip() == str(live).strip()


def bold_row(ws, r, idc, rows):
    b = ws.cell(row=r, column=2)
    b.font = Font(name=b.font.name or "Arial", size=b.font.size or 10, bold=True)
    if idc:
        for rr in rows:
            ws.cell(row=rr, column=idc).value = "YES" if rr == r else "NO"


def reconcile_defaults(ws, report, live):
    idc = isdef_col(ws)
    groups = {}
    for r in range(3, ws.max_row + 1):
        a = ws.cell(row=r, column=1).value
        if isinstance(a, str) and a.strip() and a.strip().upper() not in ("SWITCH", "FILTER", "GENERAL"):
            groups.setdefault(a.strip(), []).append(r)
    fixed = restored = odd = 0
    for name, rows in groups.items():
        bold = [r for r in rows if ws.cell(row=r, column=2).font.b]
        if len(bold) == 1 and idc:
            for r in rows:
                want = "YES" if r == bold[0] else "NO"
                if ws.cell(row=r, column=idc).value != want:
                    ws.cell(row=r, column=idc).value = want
                    fixed += 1
        elif not bold:
            # bold = default = LIVE (bible §14.1): the row whose candidate equals the live config value
            match = [r for r in rows if same_value(ws.cell(row=r, column=2).value, live.get(name))] if name in live else []
            yes = [r for r in rows if idc and ws.cell(row=r, column=idc).value == "YES"]
            if match:
                bold_row(ws, match[0], idc, rows)
                restored += 1
            elif len(yes) == 1:
                bold_row(ws, yes[0], idc, rows)
                restored += 1
            else:
                odd += 1
                why = f"live={live[name]!r} not among candidates {[ws.cell(row=r, column=2).value for r in rows]}" if name in live else "not in live config"
                report.append(f"  {ws.title}: {name} no bold default — {why}")
        elif len(bold) > 1:
            odd += 1
            report.append(f"  {ws.title}: {name} has {len(bold)} bold rows {[ws.cell(row=r, column=2).value for r in bold]} — needs operator")
    return fixed, restored, odd


def audit_only(ws, report):
    empty = [(r, ws.cell(row=r, column=1).value) for r in range(3, ws.max_row + 1) if isinstance(ws.cell(row=r, column=1).value, str) and ws.cell(row=r, column=1).value.strip() and ws.cell(row=r, column=2).value in (None, "", "None", "none")]
    for r, a in empty:
        report.append(f"  {ws.title}!{r}: {a} empty candidate (B) — NO_CANDIDATE forever")
    first_orange = next((r for r in range(3, ws.max_row + 1) if is_orange(ws.cell(row=r, column=1))), None)
    if first_orange:
        whites_below = [r for r in range(first_orange, ws.max_row + 1) if ws.cell(row=r, column=1).value and not is_orange(ws.cell(row=r, column=1))]
        if whites_below:
            report.append(f"  {ws.title}: {len(whites_below)} white SWITCH rows below the first orange FILTER row {first_orange} (R5)")


def live_defaults(stocks):
    import dataclasses
    sys.path.insert(0, str(ROOT))
    if stocks:
        import config_tradier as CT
        obj = CT.TradierConfig()
    else:
        import config as C
        obj = C.Config()
    return {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}


GREY = "FFBFBFBF"


def quickconfig_fields():
    import dataclasses
    sys.path.insert(0, str(ROOT))
    import v12_quick_engine as V
    return {f.name: getattr(V.QuickConfig(), f.name) for f in dataclasses.fields(V.QuickConfig())}


def sync_live(ws, live, qfields, report):
    """USER 2026-09-29: exactly ONE bold default per switch = live config value; every row has a candidate; the live value
    is always a row; switches missing from live config or QuickConfig are greyed (pilot skips them)."""
    idc = isdef_col(ws)
    groups = {}
    for r in range(3, ws.max_row + 1):
        a = ws.cell(row=r, column=1).value
        if isinstance(a, str) and a.strip() and a.strip().upper() not in ("SWITCH", "FILTER", "GENERAL"):
            groups.setdefault(a.strip(), []).append(r)
    stats = {"grey": 0, "filled": 0, "added": 0, "bold_fixed": 0, "dropped": 0}
    for name in sorted(groups, key=lambda k: -groups[k][-1]):
        rows = groups[name]
        a_font = lambda r, grey: setattr(ws.cell(row=r, column=1), "font", Font(name=ws.cell(row=r, column=1).font.name or "Arial", size=ws.cell(row=r, column=1).font.size or 10, bold=ws.cell(row=r, column=1).font.b, color=GREY if grey else None))
        v = live.get(name, qfields.get(name)) if name in live else None
        if name not in live or name not in qfields or isinstance(v, (list, dict, tuple, set)):
            for r in rows:
                a_font(r, True)
            stats["grey"] += 1
            continue
        for r in rows:
            if str(ws.cell(row=r, column=1).font.color.rgb if ws.cell(row=r, column=1).font.color else "").upper() == GREY:
                a_font(r, False)
        cands = {r: ws.cell(row=r, column=2).value for r in rows}
        empty = [r for r, c in cands.items() if c in (None, "", "None", "none")]
        has_live = any(same_value(c, v) for c in cands.values())
        qv = qfields.get(name)
        drop = []
        for r in empty:
            if not has_live:
                ws.cell(row=r, column=2).value = v; has_live = True; stats["filled"] += 1
            elif not any(same_value(c, qv) for c in cands.values()) and not same_value(qv, v):
                ws.cell(row=r, column=2).value = qv; stats["filled"] += 1
            else:
                drop.append(r)
        for r in sorted(drop, reverse=True):
            ws.delete_rows(r); stats["dropped"] += 1
        rows = [r for r in rows if r not in drop]
        rows = [r - sum(1 for d in drop if d < r) for r in rows]
        if not any(same_value(ws.cell(row=r, column=2).value, v) for r in rows):
            at = rows[-1] + 1
            ws.insert_rows(at)
            for c in range(1, ws.max_column + 1):
                src = ws.cell(row=rows[-1], column=c); dst = ws.cell(row=at, column=c)
                dst.font = copy.copy(src.font); dst.fill = copy.copy(src.fill); dst.alignment = copy.copy(src.alignment); dst.border = copy.copy(src.border)
                dst.value = src.value if c in (1, 4) else None
            ws.cell(row=at, column=2).value = v
            rows.append(at); stats["added"] += 1
        live_row = next(r for r in rows if same_value(ws.cell(row=r, column=2).value, v))
        for r in rows:
            b = ws.cell(row=r, column=2)
            want = r == live_row
            if bool(b.font.b) != want:
                stats["bold_fixed"] += 1
            b.font = Font(name=b.font.name or "Arial", size=b.font.size or 10, bold=want)
            if idc:
                ws.cell(row=r, column=idc).value = "YES" if want else "NO"
    report.append(f"{ws.title}: sync_live {stats}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sync-live", action="store_true")
    parser.add_argument("templates", nargs="*", default=["TEMPLATE_STOCKS_LONG", "TEMPLATE_STOCKS_SHORT", "TEMPLATE_CRYPTO_LONG", "TEMPLATE_CRYPTO_SHORT"])
    args = parser.parse_args()
    if args.apply:
        # USER 2026-09-30: tools/v15_avg_delta_apply.py is the ONLY script that may change template defaults or row order;
        # this tool also deleted columns (dedupe) — it is report-only now
        sys.exit("REFUSED: template defaults/order/columns change ONLY via tools/v15_avg_delta_apply.py — report-only")
    stamp = time.strftime("%Y%m%d%H%M")
    qfields = quickconfig_fields() if args.sync_live else {}
    for name in args.templates:
        path = ROOT / "SPREADSHEETS" / f"{name}.xlsx"
        wb = openpyxl.load_workbook(path)
        report = []
        live = live_defaults("STOCKS" in name)
        total_drop = 0
        for t in TABS:
            ws = wb[t]
            total_drop += dedupe_columns(ws)
            e2 = ws.cell(row=2, column=5)
            if isinstance(e2.value, str) and e2.value.strip().upper() == "BASELINE" and e2.value != "BASELINE":
                e2.value = "BASELINE"
                report.append(f"{t}: E2 header -> BASELINE")
            if t == "GLOBAL_RISK_GATES":
                fix_dc_breach(ws, report)
            if args.sync_live:
                sync_live(ws, live, qfields, report)
                fixed = restored = odd = 0
            else:
                fixed, restored, odd = reconcile_defaults(ws, report, live)
            if fixed or restored:
                report.append(f"{t}: is_default synced to bold {fixed} cells, bold restored from is_default {restored} groups")
            audit_only(ws, report)
        print(f"== {name}: dropped {total_drop} duplicate header columns", flush=True)
        for line in report:
            print("  " + line, flush=True)
        if args.apply:
            shutil.copy2(path, ROOT / "backups" / f"before_template_fix_{stamp}_{name}.xlsx")
            tmp = path.with_name(f".{name}.tmp.xlsx")
            wb.save(tmp)
            import zipfile
            with zipfile.ZipFile(tmp) as z:
                assert len(z.namelist()) >= 10 and z.testzip() is None
            tmp.replace(path)
            print(f"  saved {path.name} (backup before_template_fix_{stamp}_{name}.xlsx)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
