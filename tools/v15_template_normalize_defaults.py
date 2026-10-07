#!/usr/bin/env python3
"""v15_template_normalize_defaults — makes a COPY of the user's SPREADSHEETS/TEMPLATE_CLEANED/*.xlsx pass the pilot's DEFAULTS-GATE
(exactly one is_default=YES row == the bold B row per (tab, switch)) WITHOUT touching the user's files and WITHOUT moving any row:
only the is_default column (L) and the bold flag of column B change, on the copy in SPREADSHEETS/TEMPLATE_CLEANED_NORM/.
Rule per (tab, switch) group: exactly one bold row -> it is the default; several bold/YES rows -> keep the one whose value equals the venue
config default (else the first bold/YES); none -> the row equal to the venue config default; unresolved groups are reported, left as is.
  python tools/v15_template_normalize_defaults.py [--src DIR] [--out DIR]"""
import argparse, collections, copy, csv, json, sys
from pathlib import Path
import openpyxl
from openpyxl.styles import Font

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
SW = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]


def norm(v):
    s = str(v).strip().lower()
    try:
        return format(float(s), ".10g")
    except ValueError:
        return s


def _kind(v):
    if isinstance(v, bool) or str(v).strip().lower() in ("true", "false"):
        return "bool"
    try:
        float(str(v))
        return "num"
    except ValueError:
        return "str"


def _compat(ref, cell):
    kr, kc = _kind(ref), _kind(cell)
    if str(ref).strip().upper() in ("OFF", "") or str(ref).strip() in ("3m", "5m", "1m"):
        return False
    return kr == kc or (kr == "bool" and kc == "num" and str(cell).strip() in ("0", "1", "0.0", "1.0")) or (kr == "num" and kc == "bool")


def _typed_like(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return int(v) if float(v) == int(v) else float(v)
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_CLEANED"))
    ap.add_argument("--out", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_CLEANED_NORM"))
    a = ap.parse_args()
    import build_cat_side_defaults_4 as B
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    report = {}
    enforced = []
    _ex = ROOT / "data" / "wiring" / "defaults" / "enforce_exclude_baseline_moving.json"
    skip_enforce = set()  # DEF2 2026-10-01: config is source of truth, enforce all (pilot already runs the config default; the old exclusion list only guarded template-value overrides)
    skip_enforce.add("AUGMENT_MIN_GAIN_PCT")
    cat4 = json.loads((ROOT / "data" / "cat_side_defaults_4.json").read_text())
    for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
        src = Path(a.src) / f"TEMPLATE_{cs}.xlsx"
        if not src.exists():
            continue
        typed, _l, _q = B.venue_values(cs.startswith("STOCKS"))
        typed = dict(typed)
        for _k, _v in cat4.get(cs, {}).items():
            if not isinstance(_v, (dict, list, tuple, set)) and _v is not None:
                typed[_k] = _v
        try:
            wb = openpyxl.load_workbook(str(src))
        except Exception as e:
            print(f"[{cs}] cannot read {src} ({e}) — skipped"); continue
        fixed = unresolved = 0
        un = []
        for tab in SW:
            if tab not in wb.sheetnames:
                continue
            ws = wb[tab]
            cisd = next((c for c in range(1, ws.max_column + 1) if str(ws.cell(2, c).value or "").strip().lower() == "is_default"), None)
            if not cisd:
                continue
            groups = collections.OrderedDict()
            for r in range(3, ws.max_row + 1):
                an = ws.cell(r, 1).value
                if an not in (None, ""):
                    groups.setdefault(str(an).strip(), []).append(r)
            for sw, rows in groups.items():
                bold = [r for r in rows if ws.cell(r, 2).font is not None and ws.cell(r, 2).font.b]
                yes = [r for r in rows if str(ws.cell(r, cisd).value or "").strip().upper() == "YES"]
                cand = bold or yes
                dflt = norm(typed[sw]) if sw in typed and not isinstance(typed[sw], (dict, list, tuple, set)) else None
                pick = None
                if len(set(cand)) == 1 and len(yes) <= 1 and len(bold) <= 1 and (not yes or yes == bold or not bold):
                    pick = cand[0]
                if pick is None:
                    m = [r for r in (cand or rows) if dflt is not None and norm(ws.cell(r, 2).value) == dflt]
                    pick = m[0] if m else (cand[0] if cand else None)
                cfg_v = typed.get(sw)
                if isinstance(cfg_v, str) and sw not in skip_enforce:
                    off_rows = [r for r in rows if str(ws.cell(r, 2).value).strip().upper() == "OFF"]
                    cfg_s = cfg_v.strip()
                    in_opts = any(norm(ws.cell(r, 2).value) == norm(cfg_v) for r in rows)
                    cur_eq = pick is not None and norm(ws.cell(pick, 2).value) == norm(cfg_v)
                    if off_rows and not cur_eq and (cfg_s.upper() == "OFF" or cfg_s == "" or cfg_s in ("3m", "5m", "1m") or not in_opts):
                        pick = off_rows[0]  # DEF2 2026-10-01: config default OFF / 3m-5m-1m (inert, no such data) / not an option -> the OFF option is the default
                        enforced.append([sw, cs, tab, ws.cell(pick, 2).value, cfg_v, "DEFAULT_ROW_OFF_FOR_NONTESTABLE_CONFIG"])
                if pick is None:
                    # no option equals the venue config default and nothing is bold/YES: BIBLE §56.0 -> GREY (font BFBFBF) = never calculated, no default needed
                    unresolved += 1; un.append(f"{tab}!{sw}")
                    for r in rows:
                        f1 = ws.cell(r, 1).font
                        ws.cell(r, 1).font = Font(name=(f1.name if f1 else None) or "Arial", size=(f1.size if f1 else None) or 10, bold=f1.bold if f1 else None, color="FFBFBFBF")
                    continue
                cell_v = ws.cell(pick, 2).value
                if dflt is not None and sw not in skip_enforce and norm(cell_v) != dflt and _compat(typed[sw], cell_v):
                    enforced.append([sw, cs, tab, ws.cell(pick, 2).value, typed[sw], "ENFORCE_CONFIG_DEFAULT_IN_PICKED_ROW"])
                    ws.cell(pick, 2).value = _typed_like(typed[sw])
                if sw == "AUGMENT_MIN_GAIN_PCT":
                    for r in rows:
                        f1 = ws.cell(r, 1).font
                        ws.cell(r, 1).font = Font(name=(f1.name if f1 else None) or "Arial", size=(f1.size if f1 else None) or 10, bold=f1.bold if f1 else None, color="FFBFBFBF")
                cur_ok = yes == [pick] and bold == [pick]
                if cur_ok:
                    continue
                for r in rows:
                    isd = r == pick
                    f = ws.cell(r, 2).font
                    ws.cell(r, 2).font = Font(name=(f.name if f else None) or "Arial", size=(f.size if f else None) or 10, bold=isd, italic=f.italic if f else None, color=f.color if f else None)
                    ws.cell(r, cisd).value = "YES" if isd else "NO"
                fixed += 1
        if "WIRING_INVENTORY" in wb.sheetnames:
            del wb["WIRING_INVENTORY"]  # INV 2026-10-01: documentation tab lives only in the user finals; sweep copies stay lean (pilots clone/auto-adjust every sheet)
        tmp = out / f"TEMPLATE_{cs}.tmp{__import__('os').getpid()}.xlsx"
        wb.save(str(tmp)); openpyxl.load_workbook(str(tmp)); tmp.replace(out / f"TEMPLATE_{cs}.xlsx")
        report[cs] = {"groups_fixed": fixed, "unresolved": unresolved, "unresolved_sample": un[:20]}
        print(f"[{cs}] groups_fixed={fixed} unresolved={unresolved} {un[:4]}")
    with (out / "DEFAULTS_ENFORCED.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["key", "cat_side", "tab", "template_value", "config_default", "action"]); w.writerows(enforced)
    (out / "NORMALIZE_REPORT.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
