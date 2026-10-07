#!/usr/bin/env python3
"""v15_template_defaults_fix — EXACTLY ONE default per switch and per filter in every TEMPLATE tab (USER 2026-09-29).

Per template (cat_side) and per SWITCH_SHEETS tab:
  * switch rows (white) and filter rows (orange) are grouped by (tab, col-A name);
  * the default of a group = the LIVE config value for that venue (config.Config for CRYPTO_*, config_tradier.TradierConfig
    for STOCKS_*), falling back to v12_quick_engine.QuickConfig (apply_tradier_defaults() for STOCKS_*). LONG and SHORT of a
    venue share the value: the configs carry no per-side default for the same key (side-specific knobs are separate keys);
  * exactly ONE row per group gets bold col B + is_default=YES (the row whose candidate equals the default; a plain value is
    preferred over its "_ALT" twin); every other row: regular + is_default=NO;
  * no row carries the default value (placeholder options) -> NO row is ever added: the group is greyed + excluded from
    calculation, keeps one is_default row, and the engine keeps the real config value;
  * no config source for the name -> the group keeps its single existing bold row; 0 or >1 bold -> reported UNRESOLVED;
  * yellow filter headers (row 2 "FILTER=opt"): exactly one bold header per filter (opt == filter default), its header carries a
    "DEFAULT" note as the written backup; filters with no header for their default are reported.
  * is_default column: header "is_default", written YES/NO on every row (created after the last header if missing).
Rows are never added, removed or reordered. --apply writes (backup first);
default = report only.
"""
import argparse
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
SPREAD = ROOT / "SPREADSHEETS"
TEMPLATES = {"CRYPTO_LONG": "TEMPLATE_CRYPTO_LONG.xlsx", "CRYPTO_SHORT": "TEMPLATE_CRYPTO_SHORT.xlsx", "STOCKS_LONG": "TEMPLATE_STOCKS_LONG.xlsx", "STOCKS_SHORT": "TEMPLATE_STOCKS_SHORT.xlsx"}
SWITCH_SHEETS = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
HDR_ROWS = 2
SKIP_NAMES = {"switch", "general", "blanket", "filter", "option value", "sheets applicable", "gates"}


PROMOTIONS = ROOT / "data" / "cat_side_promotions.json"


def cat_side_defaults(cat_side: str) -> dict:
    """venue config defaults + the avg-delta promotions for this cat_side (tools/v15_avg_delta_apply.py ledger): a promoted
    default is the default for this template and must never be 'fixed' back to the config value."""
    out = venue_defaults(cat_side.startswith("STOCKS"))
    try:
        for k, v in (json.loads(PROMOTIONS.read_text()).get(cat_side) or {}).items():
            out[k] = ("promotion", v["value"] if isinstance(v, dict) else v)
    except FileNotFoundError:
        pass
    return out


def venue_defaults(stocks: bool) -> dict:
    import v12_quick_engine as V
    qc = V.QuickConfig()
    if stocks:
        qc.apply_tradier_defaults()
        import config_tradier as CT
        live = CT.TradierConfig()
    else:
        import config as C
        live = C.Config()
    out = {}
    for k in dir(qc):
        if not k.startswith("_") and k.isupper():
            out[k] = ("QuickConfig", getattr(qc, k))
    for k in dir(live):
        if not k.startswith("_") and k.isupper():
            try:
                out[k] = ("config_tradier" if stocks else "config", getattr(live, k))
            except Exception:
                pass
    # composite template knob (v15_pilot COMPOSITE_SWITCHES): OFF unless WT_DC + detailed scorer are on, else the entry TF
    wt = lambda k: (out.get(k) or (None, None))[1]
    out["WT_DC_DETAILED_TF"] = ("derived", str(wt("WT_DC_TF_ENTRY")) if wt("WT_DC_ENABLED") and wt("WT_DC_DETAILED_SCORER_ENABLED") else "OFF")
    return out


def norm(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return ("b", v)
    s = str(v).strip()
    if s.endswith("_ALT"):
        s = s[:-4]
    if s.lower() in ("true", "false"):
        return ("b", s.lower() == "true")
    try:
        return ("n", round(float(s), 9))
    except Exception:
        return ("s", s.lower())


def is_alt(v):
    return isinstance(v, str) and v.strip().endswith("_ALT")


def isdef_col(ws):
    for c in range(1, ws.max_column + 1):
        v = ws.cell(row=2, column=c).value
        if isinstance(v, str) and v.strip().lower().startswith("is_default"):
            return c
    return None


def last_hdr_col(ws):
    return max((c for c in range(1, ws.max_column + 1) if ws.cell(row=2, column=c).value not in (None, "")), default=ws.max_column)


def snapshot_row(ws, r, ncol):
    return [(ws.cell(row=r, column=c).value, copy.copy(ws.cell(row=r, column=c).font), copy.copy(ws.cell(row=r, column=c).fill), ws.cell(row=r, column=c).number_format, copy.copy(ws.cell(row=r, column=c).alignment)) for c in range(1, ncol + 1)]


def write_row(ws, r, cells):
    for c, (val, font, fill, nfmt, align) in enumerate(cells, start=1):
        cc = ws.cell(row=r, column=c)
        cc.value, cc.font, cc.fill, cc.number_format, cc.alignment = val, font, fill, nfmt, align


def set_bold(cell, bold):
    f = copy.copy(cell.font) if cell.font is not None else Font(name="Arial", size=10)
    cell.font = Font(name=f.name or "Arial", size=f.size or 10, bold=bold, italic=f.italic, color=f.color)


def fix_tab(ws, src: dict, report: dict, apply: bool):
    ncol = ws.max_column
    idc = isdef_col(ws)
    if idc is None:
        idc = last_hdr_col(ws) + 1
        report["created_isdef"] += 1
    ws.cell(row=2, column=idc).value = "is_default"
    ws.cell(row=2, column=idc).font = Font(name="Arial", size=10, bold=True)
    ncol = max(ncol, idc)
    groups = {}
    order = []
    for r in range(HDR_ROWS + 1, ws.max_row + 1):
        a = ws.cell(row=r, column=1).value
        if a in (None, "") or str(a).strip().lower() in SKIP_NAMES or str(a).startswith("—"):
            continue
        a = str(a).strip()
        if a not in groups:
            groups[a] = []
            order.append(a)
        groups[a].append(r)
    choice = {}   # row -> YES/NO
    for sw in order:
        rows = groups[sw]
        s = src.get(sw)
        bold_rows = [r for r in rows if ws.cell(row=r, column=2).font is not None and ws.cell(row=r, column=2).font.b]
        sval = s[1] if s is not None else None
        if isinstance(sval, (list, tuple, set)):
            sval = ",".join(str(x) for x in sval)
        # an empty default ('' / ()) is an option-less knob (blacklists, strict-TF lists): same class as no options
        no_options = all(ws.cell(row=r, column=2).value in (None, "") for r in rows) or (sval is not None and str(sval).strip() in ("", "()"))
        if s is None or sval is None or isinstance(sval, dict) or no_options:
            # USER 2026-09-29: no config field / no usable (dict) value / no candidate options -> no place in the sweep:
            # light-grey name = pilot skip (never calculated); still exactly one default row (existing single bold, else
            # the first plain row) so the one-default invariant holds everywhere
            plain = [r for r in rows if not is_alt(ws.cell(row=r, column=2).value)] or rows
            pick = bold_rows[0] if len(bold_rows) == 1 else plain[0]
            report["unresolved"].append([ws.title, sw, "NO_OPTIONS" if no_options else "NO_CONFIG_FIELD" if s is None else f"no usable config value {type(s[1]).__name__}", ws.cell(row=pick, column=2).value])
            if apply:
                for r in rows:
                    f = ws.cell(row=r, column=1).font
                    ws.cell(row=r, column=1).font = Font(name=(f.name if f else None) or "Arial", size=(f.size if f else None) or 10, bold=f.b if f else False, color="FFBFBFBF")
            for r in rows:
                choice[r] = "YES" if r == pick else "NO"
            continue
        s = (s[0], sval)
        want = norm(s[1])
        matches = [r for r in rows if norm(ws.cell(row=r, column=2).value) == want]
        if matches:
            matches.sort(key=lambda r: (is_alt(ws.cell(row=r, column=2).value), r))
            pick = matches[0]
            if bold_rows != [pick]:
                report["rebolded"].append([ws.title, sw, [ws.cell(row=r, column=2).value for r in bold_rows], ws.cell(row=pick, column=2).value, s[0]])
            for r in rows:
                choice[r] = "YES" if r == pick else "NO"
        else:
            # the live default is NOT among this group's options (placeholder candidates): NEVER add a row (USER 2026-09-30).
            # The group cannot be swept honestly -> grey + excluded; one is_default row kept (existing single bold, else the
            # first plain row); the engine keeps the real config value because grey groups are not passed as bold defaults.
            plain = [r for r in rows if not is_alt(ws.cell(row=r, column=2).value)] or rows
            pick = bold_rows[0] if len(bold_rows) == 1 else plain[0]
            report["default_not_in_options"].append([ws.title, sw, str(s[1]), s[0], [ws.cell(row=r, column=2).value for r in rows]])
            if apply:
                for r in rows:
                    f = ws.cell(row=r, column=1).font
                    ws.cell(row=r, column=1).font = Font(name=(f.name if f else None) or "Arial", size=(f.size if f else None) or 10, bold=f.b if f else False, color="FFBFBFBF")
            for r in rows:
                choice[r] = "YES" if r == pick else "NO"
    if not apply:
        return
    for r, yn in choice.items():
        set_bold(ws.cell(row=r, column=2), yn == "YES")
        c = ws.cell(row=r, column=idc)
        c.value = yn
        c.font = Font(name="Arial", size=10, bold=yn == "YES")

def fix_filter_headers(ws, src: dict, report: dict, apply: bool):
    by_filter = {}
    for c in range(12, ws.max_column + 1):
        h = ws.cell(row=2, column=c).value
        if isinstance(h, str) and "=" in h and not h.upper().startswith("WHAT SWITCH"):
            f, o = h.split("=", 1)
            by_filter.setdefault(f.strip(), []).append((c, o.strip()))
    for f, cols in by_filter.items():
        s = src.get(f)
        pick = None
        if s is not None and s[1] is not None and not isinstance(s[1], (dict, list, tuple, set)):
            m = [c for c, o in cols if norm(o) == norm(s[1])]
            pick = m[0] if m else None
            if pick is None:
                report["filter_default_missing"].append([ws.title, f, str(s[1]), [o for _, o in cols]])
        else:
            b = [c for c, _ in cols if ws.cell(row=2, column=c).font is not None and ws.cell(row=2, column=c).font.b]
            pick = b[0] if len(b) == 1 else None
            if pick is None:
                report["filter_unresolved"].append([ws.title, f, f"no config source, {len(b)} bold headers"])
        if apply:
            for c, _ in cols:
                h = ws.cell(row=2, column=c)
                set_bold(h, c == pick)
                # written backup of the filter default (row 1 is the merged tab title): a "DEFAULT" note on the header
                if c == pick:
                    h.comment = Comment("DEFAULT", "v15_template_defaults_fix")
                elif h.comment is not None and h.comment.text.strip() == "DEFAULT":
                    h.comment = None


def audit(path: Path, cat_side: str) -> list:
    """violations: groups without exactly one is_default YES, or YES row not bold / bold row not YES, or YES != config."""
    src = cat_side_defaults(cat_side)
    wb = openpyxl.load_workbook(str(path))
    bad = []
    for tab in SWITCH_SHEETS:
        if tab not in wb.sheetnames:
            continue
        ws = wb[tab]
        idc = isdef_col(ws)
        if idc is None:
            bad.append([tab, "*", "no is_default column"])
            continue
        groups = {}
        for r in range(HDR_ROWS + 1, ws.max_row + 1):
            a = ws.cell(row=r, column=1).value
            if a in (None, "") or str(a).strip().lower() in SKIP_NAMES or str(a).startswith("—"):
                continue
            groups.setdefault(str(a).strip(), []).append(r)
        for sw, rows in groups.items():
            grey = str(getattr(getattr(ws.cell(row=rows[0], column=1).font, "color", None), "rgb", "") or "").upper() == "FFBFBFBF"
            yes = [r for r in rows if str(ws.cell(row=r, column=idc).value or "").strip().upper() == "YES"]
            bold = [r for r in rows if ws.cell(row=r, column=2).font is not None and ws.cell(row=r, column=2).font.b]
            if len(yes) != 1 or bold != yes:
                bad.append([tab, sw, f"is_default YES rows {yes} bold rows {bold}"])
                continue
            s = src.get(sw)
            if not grey and s is not None and s[1] is not None and not isinstance(s[1], (dict, list, tuple, set)) and norm(ws.cell(row=yes[0], column=2).value) != norm(s[1]):
                bad.append([tab, sw, f"PLACEHOLDER default {ws.cell(row=yes[0], column=2).value!r} != {s[0]} {s[1]!r} (not used as default)"])
        by_filter = {}
        for c in range(12, ws.max_column + 1):
            h = ws.cell(row=2, column=c).value
            if isinstance(h, str) and "=" in h and not h.upper().startswith("WHAT SWITCH"):
                by_filter.setdefault(h.split("=", 1)[0].strip(), []).append(c)
        for f, cs in by_filter.items():
            b = [c for c in cs if ws.cell(row=2, column=c).font is not None and ws.cell(row=2, column=c).font.b]
            n = [c for c in cs if ws.cell(row=2, column=c).comment is not None and ws.cell(row=2, column=c).comment.text.strip() == "DEFAULT"]
            if len(b) != 1 or n != b:
                bad.append([tab, f, f"filter header: bold {b} DEFAULT-note {n}"])
                continue
            s = src.get(f)
            opt = ws.cell(row=2, column=b[0]).value.split("=", 1)[1]
            if s is not None and s[1] is not None and not isinstance(s[1], (dict, list, tuple, set)) and norm(opt) != norm(s[1]):
                bad.append([tab, f, f"filter default {opt!r} != {s[0]} {s[1]!r}"])
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--cat-side", default="ALL")
    ap.add_argument("--audit", action="store_true", help="only audit (exit 1 on any violation)")
    args = ap.parse_args()
    if args.apply:
        # USER 2026-09-30: tools/v15_avg_delta_apply.py is the ONLY script that may change template defaults or row order.
        # The one-time repair ran 2026-09-30 00:20 (report data/reports/v15_template_defaults_fix_202609300020.json).
        sys.exit("REFUSED: template defaults are changed ONLY by tools/v15_avg_delta_apply.py — this tool is audit/report-only now")
    cats = list(TEMPLATES) if args.cat_side == "ALL" else [args.cat_side]
    if args.audit:
        nbad = 0
        for cs in cats:
            bad = audit(SPREAD / TEMPLATES[cs], cs)
            ph = [b for b in bad if "PLACEHOLDER" in b[2]]
            hard = [b for b in bad if "PLACEHOLDER" not in b[2]]
            nbad += len(hard)
            print(f"[audit] {cs}: {len(hard)} violations {hard[:5]} | {len(ph)} placeholder-option groups (real default kept)")
        sys.exit(1 if nbad else 0)
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M")
    full = {}
    for cs in cats:
        path = SPREAD / TEMPLATES[cs]
        src = cat_side_defaults(cs)
        wb = openpyxl.load_workbook(str(path))
        rep = {"created_isdef": 0, "kept_no_source": 0, "rebolded": [], "default_not_in_options": [], "unresolved": [], "filter_default_missing": [], "filter_unresolved": []}
        for tab in SWITCH_SHEETS:
            if tab in wb.sheetnames:
                fix_tab(wb[tab], src, rep, args.apply)
                fix_filter_headers(wb[tab], src, rep, args.apply)
        print(f"[{cs}] rebolded={len(rep['rebolded'])} default_not_in_options(greyed)={len(rep['default_not_in_options'])} kept_no_source={rep['kept_no_source']} unresolved={len(rep['unresolved'])} filter_default_missing={len(rep['filter_default_missing'])} filter_unresolved={len(rep['filter_unresolved'])} isdef_created={rep['created_isdef']}")
        full[cs] = rep
        if args.apply:
            shutil.copy2(path, ROOT / "backups" / f"before_defaults_fix_{ts}_{path.name}")
            tmp = path.with_suffix(".tmp.xlsx")
            wb.save(str(tmp))
            openpyxl.load_workbook(str(tmp))
            tmp.replace(path)
    out = ROOT / "data" / "reports" / f"v15_template_defaults_fix_{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(full, indent=1, default=str))
    print(f"[report] {out}")


if __name__ == "__main__":
    main()
