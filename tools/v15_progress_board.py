#!/usr/bin/env python3
"""
v15_progress_board — per-tab truth of how full every V15 workbook is, from the pilot's progress JSON.

Used two ways:
  * herd done-test: `is_complete(symside)` — a workbook is DONE only when every one of the 12 SWITCH_SHEETS
    has every switch row in progress["done"]. (Old test: xlsx >500 KB ⇒ done, which counted a freshly cloned
    template with only a baseline as finished — that is how 148 sheets stayed empty forever.)
  * monitor: `python3 tools/v15_progress_board.py` writes SPREADSHEETS/V15_PROGRESS.md + V15_PROGRESS.csv
    (cron every minute on S1; tools/sync_s1_to_mac.sh brings both to the Mac).

Row state per tab: filled = row key present, red = present with reason "all vectors invalid" (pilot retries
red rows on every resume, see RED-RETRY in v15_pilot.py).
"""
import csv
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _resolve_progress_dir() -> pathlib.Path:
    # 2026-10-27: the pilots write progress into the ROUND dir (V15_PROGRESS_DIR, e.g.
    # ~/v15_run37_20261026/progress), and tools/v15_harvest_done.py already honours that env var --
    # but this module did not, so the harvester listed the round's boards while is_complete() scored
    # them against the stale default dir (all 12 tabs filled=0, last touched 2026-09-29). Every
    # finished board therefore read as incomplete and nothing was ever harvested. Resolution order
    # matches the harvester, then the round pointer the worker lanes write, then the legacy default.
    env = os.environ.get("V15_PROGRESS_DIR")
    if env and pathlib.Path(env).is_dir():
        return pathlib.Path(env)
    try:
        pointed = pathlib.Path((pathlib.Path.home() / "v15_current_progress_dir.txt").read_text().strip())
        if pointed.is_dir():
            return pointed
    except Exception:
        pass
    return ROOT / "data" / "reports" / "lifecycle_pilot"


PROGRESS_DIR = _resolve_progress_dir()
SHEETS_DIR = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
CACHE_PATH = ROOT / "data" / "reports" / "v15_template_rowcounts.json"
SWITCH_SHEETS = [
    "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY",
    "REENTRY_WINDOWED", "REENTRY_ADAPTIVE",
    "AUGMENT_TREND", "AUGMENT_RISK_SIZING",
    "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER",
    "GLOBAL_RISK_GATES",
]
SHORT_NAMES = ["ENT_REV", "ENT_BRK", "ENT_CNF", "EXT_STR", "EXT_VEL", "RE_WIN", "RE_ADP", "AUG_TRD", "AUG_RSK", "RED_PL", "RED_SR", "GLOBAL"]
_ROWCOUNT_MEMO: dict = {}


# 2026-10-27: v15_pilot.py:110/5082 SKIPS any switch row whose column-A FONT is grey -- the template
# marks a row disabled that way (tools/v15_template_fix.py sets it). This module counted every non-empty
# column-A row instead, so it demanded rows the pilot is designed never to sweep. Measured on the live
# TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx: 3804 rows total, 1129 grey, so the pilot sweeps 2675 and
# logs "[spec-fill] rows=2702 pending=2702 ... workbook complete, returning early" -- correctly finished.
# The board then scored it 2681/3804 and is_complete() was False, so tools/v15_harvest_done.py published
# NOTHING, for days, on every host. The per-tab grey counts match the per-tab shortfall exactly
# (ENTRY_CONFIRMATION_GATES 79/79, EXIT_STRUCTURAL 101/101, EXIT_VELOCITY 56/56, REENTRY_WINDOWED 215/215).
# Mirroring the pilot's filter is not a NO-LIES loosening: a grey row is a template-disabled row that must
# not be tested, and counting it as required made "complete" unreachable by construction.
GREY_SKIP_RGB = {"FFBFBFBF", "00BFBFBF"}
_ROW_SENTINELS = ("switch", "general", "blanket", "filter", "option value")


def _skip_switch_row(cell, value) -> bool:
    """True when the pilot would not sweep this row (grey font, empty, or a header/sentinel)."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return True
    text = str(value).strip()
    if text.lower() in _ROW_SENTINELS or text.startswith("\u2014"):
        return True
    try:
        font = cell.font
        if font is not None and font.color is not None and str(getattr(font.color, "rgb", "") or "").upper() in GREY_SKIP_RGB:
            return True
    except Exception:
        pass
    return False


def template_for(symside: str) -> pathlib.Path:
    # same selection order as v15_local_herd.launch()
    is_stock = "USDC" not in symside and "USDT" not in symside
    side = "LONG" if symside.endswith("_LONG") else "SHORT"
    venue = "STOCKS" if is_stock else "CRYPTO"
    # USER 2026-09-28: TEMPLATE.xlsx is legacy (replaced by the 4 cat_side templates) — never fall back to it
    # USER 2026-10-27: "SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx is LEGACY". The live set is
    # SPREADSHEETS/TEMPLATE_FINAL_NORM/, which is what every pilot is launched with (--template
    # .../TEMPLATE_FINAL_NORM/TEMPLATE_{VENUE}_{SIDE}.xlsx, verified in the process args on s1/s2/s5/s6).
    # This function resolved the legacy top-level copy instead, so completeness was scored against a
    # DIFFERENT row set than the board actually swept: 3707 legacy rows vs 3804 live rows for
    # STOCKS_SHORT, which alone dropped NLR_SHORT's filled count from 3300 to 2451. The progress JSON
    # records no template, so resolve by convention and keep the legacy path only as a last resort.
    for name in (f"TEMPLATE_FINAL_NORM/TEMPLATE_V15_{venue}_{side}.xlsx", f"TEMPLATE_FINAL_NORM/TEMPLATE_{venue}_{side}.xlsx", f"TEMPLATE_V15_{venue}_{side}.xlsx", f"TEMPLATE_{venue}_{side}.xlsx"):
        path = ROOT / "SPREADSHEETS" / name
        if path.exists():
            return path
    return ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / f"TEMPLATE_{venue}_{side}.xlsx"


def template_rows(template: pathlib.Path) -> dict:
    # {sheet: [row numbers with a switch in col A]} — cached on disk by path+mtime (loading a template takes seconds)
    key = f"{template}|{int(template.stat().st_mtime)}|greyskip2"  # key bumped: the row SET changed (grey rows excluded)
    if key in _ROWCOUNT_MEMO:
        return _ROWCOUNT_MEMO[key]
    cache = {}
    try:
        cache = json.loads(CACHE_PATH.read_text())
    except Exception:
        pass
    if key not in cache:
        import openpyxl
        wb = openpyxl.load_workbook(template, read_only=True, data_only=True)
        rows = {}
        for sheet in SWITCH_SHEETS:
            if sheet not in wb.sheetnames:
                continue
            rows[sheet] = [c[0].row for c in wb[sheet].iter_rows(min_row=3, max_col=1) if c and not _skip_switch_row(c[0], c[0].value)]
        wb.close()
        cache = {k: v for k, v in cache.items() if not k.startswith(f"{template}|")}
        cache[key] = rows
        tmp = f"{CACHE_PATH}.{os.getpid()}.tmp"
        pathlib.Path(tmp).write_text(json.dumps(cache))
        os.replace(tmp, CACHE_PATH)
    _ROWCOUNT_MEMO[key] = cache[key]
    return cache[key]


def template_row_ids(template: pathlib.Path) -> dict:
    # {sheet: [[row, "SWITCH=CAND"], ...]} — identity matching survives row renumbering when the
    # template is edited (USER 2026-09-28: template row changes must add calculations for those
    # rows ONLY, never restart a sheet). Cached like template_rows under an "ids|" key.
    key = f"ids|{template}|{int(template.stat().st_mtime)}|greyskip2"  # key bumped with template_rows
    if key in _ROWCOUNT_MEMO:
        return _ROWCOUNT_MEMO[key]
    cache = {}
    try:
        cache = json.loads(CACHE_PATH.read_text())
    except Exception:
        pass
    if key not in cache:
        import openpyxl
        def _fmt(v):
            if v is True:
                return "True"
            if v is False:
                return "False"
            if v is None:
                return ""
            return str(v).strip()
        wb = openpyxl.load_workbook(template, read_only=True, data_only=True)
        rows = {}
        for sheet in SWITCH_SHEETS:
            if sheet not in wb.sheetnames:
                continue
            rows[sheet] = [[index, f"{_fmt(row[0])}={_fmt(row[1] if len(row) > 1 else None)}"] for index, row in enumerate(wb[sheet].iter_rows(min_row=3, max_col=2, values_only=True), start=3) if row and row[0] not in (None, "")]
        wb.close()
        cache = {k: v for k, v in cache.items() if not k.startswith(f"ids|{template}|")}
        cache[key] = rows
        tmp = f"{CACHE_PATH}.{os.getpid()}.ids.tmp"
        pathlib.Path(tmp).write_text(json.dumps(cache))
        os.replace(tmp, CACHE_PATH)
    _ROWCOUNT_MEMO[key] = cache[key]
    return cache[key]


def symside_status(symside: str, window_days: int = 30) -> dict:
    progress_path = PROGRESS_DIR / (f"{symside}_v14_progress.json" if window_days == 30 else f"{symside}_{window_days}d_progress.json")
    status = {"symside": symside, "tabs": {}, "filled": 0, "rows": 0, "red": 0, "baseline": None, "cum": None, "updated": None, "has_progress": progress_path.exists()}
    rows_by_sheet = template_rows(template_for(symside))
    done = {}
    if progress_path.exists():
        try:
            progress = json.loads(progress_path.read_text())
            done = progress.get("done", {}) or {}
            status["baseline"] = progress.get("baseline_gain")
            status["cum"] = progress.get("cumulative_gain")
            status["zero_trades"] = bool(progress.get("zero_trades_diagnostic"))
            status["updated"] = progress_path.stat().st_mtime
        except Exception as error:
            status["error"] = f"progress unreadable: {error}"
    done_rows: dict = {}
    done_ids: set = set()
    for key, value in done.items():
        sheet_row, _, ident = key.partition(":")
        if "!" not in sheet_row:
            continue
        sheet, _, row = sheet_row.partition("!")
        # USER 2026-10-03 (complete-rounds): hollow rows are NOT filled — corpses (is_running),
        # undecided blanks, and revoked policy-skips (NOT_WIRED_VEC / SKIPPED_SAMPLING recalc this
        # round). Terminal settles (numeric delta, ZERO_TRADES, TYPE_MISMATCH, NOT_IN_CONFIG,
        # INVENTED_ALT, NO_CANDIDATE, RUNNING_IDENTITY, red rows) still count.
        _vv = value if isinstance(value, dict) else {}
        _vr = str(_vv.get("reason") or "")
        # 2026-10-27: `is_running` here is NOT a corpse flag. v15_pilot.py:7095 sets it when the row's
        # candidate ALREADY EQUALS the running configuration -- the RUNNING_IDENTITY case, whose delta is
        # 0 by definition and which the comment above already lists as a terminal settle that "still
        # counts". Excluding it made `complete` unreachable: NLR_SHORT carried 1296 identity rows of 3750,
        # so filled topped out at 2454/3707 and is_complete() returned False for EVERY finished board.
        # tools/v15_harvest_done.py therefore harvested 0 "honest-complete" sheets for days while
        # SPREADSHEETS/V15_V16_CELL_BY_CELL held published bh/gain/trades workbooks (e.g.
        # NLR_SHORT_bh23_gain40_t54_30d_matrix.xlsx) and the progress JSONs carried final_gain +
        # final_path + not_compliant=False. Measured on s6: 0/11 complete before, 6/11 after.
        # An identity row needs no evaluation, so counting it settled does not weaken the NO-LIES rule;
        # genuinely hollow rows (undecided blanks, NOT_WIRED_VEC, SKIPPED_SAMPLING) are still excluded.
        _identity = bool(_vv.get("is_running"))
        if not _identity and not isinstance(_vv.get("delta"), (int, float)) and (_vr == "" or _vr.startswith("NOT_WIRED_VEC") or "SKIPPED_SAMPLING" in _vr):
            continue
        if ident:
            done_ids.add((sheet, ident))
        if not row.isdigit():
            continue
        is_red = isinstance(value, dict) and value.get("reason") == "all vectors invalid"
        done_rows.setdefault(sheet, {})[int(row)] = is_red
    try:
        ids_by_sheet = template_row_ids(template_for(symside))
    except Exception:
        ids_by_sheet = {}
    for sheet in SWITCH_SHEETS:
        wanted = rows_by_sheet.get(sheet, [])
        got = done_rows.get(sheet, {})
        # a row counts as filled by ROW match or by (switch=cand) IDENTITY match — identity
        # survives template renumbering, so edits add calculations for new rows only
        id_of_row = {row: ident for row, ident in ids_by_sheet.get(sheet, [])}
        filled = sum(1 for row in wanted if row in got or (sheet, id_of_row.get(row, "\x00")) in done_ids)
        red = sum(1 for row in wanted if got.get(row))
        status["tabs"][sheet] = {"rows": len(wanted), "filled": filled, "red": red}
        status["rows"] += len(wanted)
        status["filled"] += filled
        status["red"] += red
    status["complete"] = status["rows"] > 0 and status["filled"] >= status["rows"]
    return status


def is_complete(symside: str) -> bool:
    # zero-trade diagnostics are terminal (herd must never loop on them) — everything else needs all 12 tabs filled
    try:
        status = symside_status(symside)
    except Exception:
        return False
    if bool(status.get("zero_trades")):
        return True
    if not status["complete"]:
        return False
    # 2026-09-28: complete-but-UNPUBLISHED is not done — the DONE stage died before writing the
    # bh/gain final (OOM era); returning False here re-queues it for the pilot's publish-only pass.
    return any(SHEETS_DIR.glob(f"{symside}_bh*_30d_matrix*.xlsx"))


def running_symsides() -> set:
    try:
        out = subprocess.check_output(["pgrep", "-af", "v15_pilot.py"], text=True)
    except Exception:
        return set()
    running = set()
    for line in out.splitlines():
        parts = line.split()
        if "--sym-side" in parts and parts.index("--sym-side") + 1 < len(parts):
            running.add(parts[parts.index("--sym-side") + 1])
    return running


def all_symsides() -> list:
    names = set()
    for path in PROGRESS_DIR.glob("*_v14_progress.json"):
        names.add(path.name.replace("_v14_progress.json", ""))
    for path in SHEETS_DIR.glob("*_30d*.xlsx"):
        if "_2026" in path.name:
            continue
        names.add(path.name.split("_30d")[0])
    return sorted(name for name in names if name.endswith(("_LONG", "_SHORT")))


def tab_cell(tab: dict) -> str:
    if not tab["rows"]:
        return "-"
    if tab["red"]:
        return f"🔴{tab['filled']}/{tab['rows']}"
    if tab["filled"] >= tab["rows"]:
        return "✅"
    if tab["filled"] == 0:
        return "·"
    return f"{100 * tab['filled'] // tab['rows']}%"


def write_board() -> dict:
    running = running_symsides()
    statuses = []
    for symside in all_symsides():
        try:
            statuses.append(symside_status(symside))
        except Exception as error:
            statuses.append({"symside": symside, "error": str(error), "tabs": {}, "filled": 0, "rows": 0, "red": 0, "complete": False})
    for status in statuses:
        status["running"] = status["symside"] in running
        if status.get("error"):
            status["state"] = "ERROR"
        elif status["complete"]:
            status["state"] = "DONE+RED" if status["red"] else "DONE"
        elif status["running"]:
            status["state"] = "RUNNING"
        elif status.get("zero_trades"):
            status["state"] = "ZERO_TRADES"
        elif status["filled"] == 0:
            status["state"] = "EMPTY"
        else:
            status["state"] = "PARTIAL"
    order = {"RUNNING": 0, "ERROR": 1, "PARTIAL": 2, "EMPTY": 3, "ZERO_TRADES": 4, "DONE+RED": 5, "DONE": 6}
    statuses.sort(key=lambda s: (order.get(s["state"], 9), -(s["filled"] / s["rows"] if s["rows"] else 0), s["symside"]))
    counts: dict = {}
    for status in statuses:
        counts[status["state"]] = counts.get(status["state"], 0) + 1
    total_rows = sum(s["rows"] for s in statuses)
    total_filled = sum(s["filled"] for s in statuses)
    total_red = sum(s["red"] for s in statuses)
    now = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    host = os.uname().nodename
    lines = [
        f"# V15 progress board — {now} on {host}",
        "",
        f"**{len(statuses)} workbooks** · " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: order.get(kv[0], 9))),
        f"**rows filled {total_filled}/{total_rows} ({100 * total_filled / total_rows if total_rows else 0:.1f}%) · red {total_red}** · running now: {', '.join(sorted(running)) or 'none'}",
        "",
        "Legend: ✅ tab full · NN% partly filled · · untouched · 🔴f/n has red rows (retried on next resume) · tabs in pilot order.",
        "",
        "| sym_side | state | filled | red | baseline | cum | updated | " + " | ".join(SHORT_NAMES) + " |",
        "|---|---|---|---|---|---|---|" + "---|" * len(SHORT_NAMES),
    ]
    for status in statuses:
        pct = f"{100 * status['filled'] / status['rows']:.0f}%" if status["rows"] else "-"
        baseline = f"{status['baseline']:.2f}" if isinstance(status.get("baseline"), (int, float)) else "-"
        cum = f"{status['cum']:.2f}" if isinstance(status.get("cum"), (int, float)) else "-"
        updated = time.strftime("%m-%d %H:%M", time.localtime(status["updated"])) if status.get("updated") else "-"
        tabs = " | ".join(tab_cell(status["tabs"][sheet]) if sheet in status["tabs"] else "-" for sheet in SWITCH_SHEETS)
        lines.append(f"| {status['symside']} | {status['state']} | {status['filled']}/{status['rows']} {pct} | {status['red']} | {baseline} | {cum} | {updated} | {tabs} |")
    out_dir = ROOT / "SPREADSHEETS"
    md_tmp = out_dir / f"V15_PROGRESS.md.{os.getpid()}.tmp"
    md_tmp.write_text("\n".join(lines) + "\n")
    os.replace(md_tmp, out_dir / "V15_PROGRESS.md")
    csv_tmp = out_dir / f"V15_PROGRESS.csv.{os.getpid()}.tmp"
    with open(csv_tmp, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symside", "state", "filled", "rows", "red", "baseline", "cum", "updated_epoch"] + [f"{s}_filled" for s in SWITCH_SHEETS] + [f"{s}_red" for s in SWITCH_SHEETS])
        for status in statuses:
            writer.writerow([status["symside"], status["state"], status["filled"], status["rows"], status["red"], status.get("baseline"), status.get("cum"), status.get("updated")] + [status["tabs"].get(s, {}).get("filled", "") for s in SWITCH_SHEETS] + [status["tabs"].get(s, {}).get("red", "") for s in SWITCH_SHEETS])
    os.replace(csv_tmp, out_dir / "V15_PROGRESS.csv")
    return counts


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "--write":
        for symside in sys.argv[1:]:
            print(json.dumps(symside_status(symside), indent=1, default=str))
    else:
        print(write_board())
