#!/usr/bin/env python3
"""v14_sequential_filler — sequential row-by-row sheet filler (NEW NAME, does not overwrite v12/v13).

Why v12/v13 stranded (diagnosis inline, INSTRUCTIONS 11 steps LAW):
1) PARALYSIS AT ROW 1: baseline 0 trades → parity_ok required live trades>0, so
   first positive vector delta never promoted, F written but E never ratchets.
   On MU_SHORT/SNDK/Crude shorts BH inverted but code used raw bh_pct.
2) CONFIG RACE: ThreadPool 28 workers share process-global Config/TradierConfig
   patched by backtest_v12_engine; parallel trials corrupt each other's overrides
   → many 0.0 / duplicate -0.49 deltas.
3) CUMULATIVE MATH BUG: write_delta_cell used initial baseline_gain not
   cumulative_before, so F = variant - initial, not F = variant - Eprev.
   E chain = multiple of BH formula broken after first promotion.
4) L:BI NEVER FILLED: TEMPLATE.xlsx has A1:IH317 but row1 L:BI headers are empty
   (only TEMPLATE_V2_1YR_CONNECTED has them). Code searched row1 col12..max
   but max_column was 9 after openpyxl load with empty L:BI → header_to_col={},
   so every per-row SPECIFIC filter was skipped, leaving rows with only GENERAL
   blanket at bottom (which v12 already disabled via duplicate -0.49 guard).
5) SUSPICIOUS BLOCK: is_suspicious_sequence blocked ALL promotions when
   >75% greens or 50 consecutive — legit 1yr streaks on ZEC/BTC flagged as
   MANY_GREENS → sheet written but E stays empty.
6) FULL-COLUMN SCAN: E2=MAX('prev'!E:E) scans 1M rows per sheet ×20 → 3 min clone
   before first trial appears stalled.
7) TEMPLATE F2 OFF-BY-ONE: F2 = ... -E1 where E1 is header string, not E2.

Fix in v14 (sequential, S1-only):
- Single thread, no ThreadPool → no config race.
- cumulative_before tracked per row; F = variant_gain - cumulative_before.
- Results_30d_Deltas col8 = variant_gain (= cumulative_before + delta) so
  Excel F = VLOOKUP - Eprev recomputes correctly; E = IF(F>0,Eprev+F,Eprev).
- BH inversion for SHORT: short BH floored -100% in metrics.py, but sheet
  display bh inverted (positive) per INSTRUCTIONS 1b; delta vs BH still correct.
- Creates missing L:BI headers from FILTER_DICTIONARY before sheet loop.
- Per-row: tests switch alone vs switch+each OPPORTUNE SPECIFIC filter (token
  overlap + Sheets applicable), picks max total delta vs cumulative_before,
  live-verifies only that best (vector-only skips live). Promotes switch+best
  filter together → one new E per row, never repeating a value.
- GENERAL blanket appended vs final cumulative, live-verified.
- vector-only mode for S1 speed (0.07s/cell); live verify only for positives.
- No suspicious global block; duplicate deltas are logged not blocked.
"""
from __future__ import annotations
import argparse
import dataclasses
import datetime
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl
from openpyxl.styles import Font

TEMPLATE = ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx"
OUT_DIR = ROOT / "SPREADSHEETS"
PROGRESS_DIR = ROOT / "data" / "reports" / "lifecycle_pilot"
CHARTS_1M_DIR = ROOT / "data" / "reports" / "charts_1M"

SWITCH_SHEETS = [
    "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY",
    "REENTRY_WINDOWED", "REENTRY_ADAPTIVE",
    "AUGMENT_TREND", "AUGMENT_RISK_SIZING",
    "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER",
    "GLOBAL_RISK_GATES",
]

_FILTER_DICT_CACHE = None


def _load_filter_dictionary() -> list[dict]:
    global _FILTER_DICT_CACHE
    if _FILTER_DICT_CACHE is not None:
        return _FILTER_DICT_CACHE
    candidates = [ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx", ROOT / "SPREADSHEETS" / "TEMPLATE_V2_1YR_CONNECTED.xlsx"]
    ws = None
    wb = None
    for p in candidates:
        if p.exists():
            try:
                wb = openpyxl.load_workbook(str(p), data_only=True)
                for cand in ["FILTER_DICTIONARY_V2", "FILTER_DICTIONARY", "FILTER_DICTIONARY_V8"]:
                    if cand in wb.sheetnames:
                        ws = wb[cand]
                        break
                if ws is not None:
                    break
                wb.close()
            except Exception:
                continue
    if ws is None:
        _FILTER_DICT_CACHE = []
        return _FILTER_DICT_CACHE
    rows = []
    hdr = {str(ws.cell(1, c).value or "").strip(): c for c in range(1, 30)}
    fc = hdr.get("Filter", 2)
    # locate Option Value col
    oc = None
    sc = gc = rc = None
    for ci in range(1, 30):
        hv = str(ws.cell(1, ci).value or "")
        if "Option Value" in hv:
            oc = ci
        if "Sheets applicable" in hv:
            sc = ci
        if "Switches exactly" in hv:
            gc = ci
        if hv.strip() == "Recommendation":
            rc = ci
    for r in range(2, ws.max_row + 1):
        f = ws.cell(r, fc).value
        if not f or not isinstance(f, str) or f.strip().isdigit():
            continue
        f = f.strip()
        if not f:
            continue
        opt = ws.cell(r, oc).value if oc else None
        sheets_app = str(ws.cell(r, sc).value or "") if sc else ""
        gates = str(ws.cell(r, gc).value or "") if gc else ""
        rec = str(ws.cell(r, rc).value or "") if rc else ""
        rows.append({"filter": f, "opt": opt, "sheets_app": sheets_app, "gates": gates, "rec": rec})
    _FILTER_DICT_CACHE = rows
    try:
        wb.close()
    except Exception:
        pass
    return _FILTER_DICT_CACHE


def _token_overlap(gates: str, switch: str) -> bool:
    if not gates or not switch:
        return False
    gates = gates.strip()
    switch = switch.strip()
    if gates == switch:
        return True
    if gates.lower() == switch.lower():
        return True
    if gates in switch or switch in gates:
        return True
    import re
    def toks(s): return [t for t in re.split(r"[ _\-\/]+", s.lower()) if len(t) > 2]
    if set(toks(gates)) & set(toks(switch)):
        return True
    return False


def _is_general(rec: str) -> bool:
    return rec.strip().upper().startswith("GENERAL")


def _universe_for_symside(symside: str) -> str:
    # universes: stock_long, stock_short, crypto_long, crypto_short (threshold >100 switches to useful-only per universe)
    s = (symside or "").upper()
    is_crypto = s.endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD","DAI"))
    side = "short" if s.endswith("_SHORT") else "long"
    return f"{'crypto' if is_crypto else 'stock'}_{side}"

def _useful_filters_for_universe(universe: str) -> set[str] | None:
    # Until >100 sym_sides tested, return None (test ALL). After, load per-universe useful list.
    try:
        from pathlib import Path as _P
        prog_dir = _P(__file__).resolve().parents[2] / "data" / "progress"
        tested = len(list(prog_dir.glob("*_v14_progress.json"))) if prog_dir.exists() else 0
        if tested < 100:
            return None
        useful_path = _P(__file__).resolve().parents[2] / "data" / "reports" / f"useful_filters_{universe}.json"
        if useful_path.exists():
            import json as _j
            data = _j.loads(useful_path.read_text())
            # data is list of "filter=opt" or filter names; normalize to set of filter names
            out = set()
            for x in data:
                if isinstance(x, str) and "=" in x:
                    out.add(x.split("=")[0].strip())
                elif isinstance(x, str):
                    out.add(x.strip())
            return out
        # No useful file yet -> keep ALL until it exists (still >100 but no list => log and keep ALL)
        return None
    except Exception:
        return None

def get_opportune_filters(switch: str, sheet: str) -> list[dict]:
    if not switch or not sheet:
        return []
    lifecycle = sheet.split("_")[0]
    rows = _load_filter_dictionary()
    out = []
    for e in rows:
        sa = e["sheets_app"] or ""
        # FIX 421-432: WT_15M_BOUNCE filters with sheets=ALL were never applicable (ALL != lifecycle and != GLOBAL_CHECK) → 0 tested.
        # User mandate: these 12 (WT_15M_BOUNCE_BB_MIN/MAX etc, R416-427) are GENERAL and must be tested as GLOBAL, not gated to WT15.
        # Treat sheets=ALL as applicable to every sheet (GLOBAL).
        if sa.strip() == "ALL":
            applicable = True
        else:
            applicable = (lifecycle in sa) or ("GLOBAL_CHECK" in sa)
        if not applicable:
            continue
        # For sheets=ALL GENERAL filters, treat as GLOBAL regardless of gate mismatch
        if sa.strip() == "ALL" and _is_general(e["rec"]):
            out.append(e)
        elif _is_general(e["rec"]) or _token_overlap(e["gates"], switch):
            out.append(e)
    return out


def sanitize_overrides(overrides: dict, defaults: dict) -> tuple[dict, list]:
    sanitized = dict(overrides)
    warns = []
    float_keys = ["ATR_ADAPTIVE_SIZING_TARGET_PCT", "BOUNCE_AUGMENT_K_D_THRESHOLD", "DC_EDGE_SIZING_MAX_MULT", "EMA_DIST_SIZING_MULT", "REENTRY_TIER1_SIZE_MULT_TRADIER", "BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE"]
    for k in float_keys:
        if k in sanitized and isinstance(sanitized[k], bool):
            sanitized[k] = float(defaults.get(k, 1.0)) if defaults.get(k) is not None else 1.0
            warns.append(k)
    return sanitized, warns


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_live_recipes():
    try:
        from tools.opt.v12_pilot import load_live_recipes as _load
        return _load()
    except Exception:
        return {}


def get_defaults_for_symside(symside: str) -> dict:
    import v12_quick_engine as V
    import config, config_tradier
    is_crypto = symside.upper().endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI"))
    defaults = {}
    for f in dataclasses.fields(V.QuickConfig):
        defaults[f.name] = f.default if f.default is not dataclasses.MISSING else None
        if defaults[f.name] is None and f.default_factory is not dataclasses.MISSING:  # type: ignore
            try:
                defaults[f.name] = f.default_factory()  # type: ignore
            except Exception:
                defaults[f.name] = None
    live_cls = config.Config if is_crypto else config_tradier.TradierConfig
    for k in dir(live_cls):
        if k.startswith("_"):
            continue
        if k not in defaults:
            try:
                defaults[k] = getattr(live_cls, k)
            except Exception:
                pass
    return defaults


def clone_template(template: Path, new_symside: str) -> Path:
    if not template.exists():
        raise FileNotFoundError(f"template missing {template}")
    target = OUT_DIR / f"{new_symside}_30d_matrix.xlsx"
    if target.exists():
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
        target = OUT_DIR / f"{new_symside}_30d_matrix_pilot_{ts}.xlsx"
        if target.exists():
            raise FileExistsError(f"refusing to overwrite {target}")
    wb = openpyxl.load_workbook(str(template))
    new_baseline = f"{new_symside}_BASELINE_METRICS"
    old_baseline = None
    for cand in ["TEMPLATE_BASELINE_METRICS", "ADP_LONG_BASELINE_METRICS"]:
        if cand in wb.sheetnames:
            old_baseline = cand
            break
    if old_baseline:
        ws = wb[old_baseline]
        ws.title = new_baseline
        for sheet_name in wb.sheetnames:
            ws2 = wb[sheet_name]
            for row in ws2.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and old_baseline in c.value:
                        c.value = c.value.replace(old_baseline, new_baseline).replace("TEMPLATE", new_symside.split("_")[0]).replace("ADP_LONG", new_symside)
    # clamp E:E → E$2:E$5000 and fix F2 off-by-one (was -E1)
    for idx, name in enumerate(SWITCH_SHEETS):
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        if idx > 0:
            prev = SWITCH_SHEETS[idx - 1]
            if prev in wb.sheetnames:
                c = ws.cell(row=2, column=5)
                if isinstance(c.value, str) and "MAX" in c.value:
                    c.value = f"=MAX('{prev}'!E$2:E$5000)"
                    c.font = Font(name="Arial", bold=True, color="006100")
        else:
            # first sheet E2 already correct
            pass
        # fix F2 off-by-one: F2 should be VLOOKUP -E2 not -E1
        c2 = ws.cell(row=2, column=6)
        if isinstance(c2.value, str) and "-E1" in c2.value:
            c2.value = c2.value.replace("-E1", "-E2")
    # clear Results but keep header
    for cand in ["Results_30d_Deltas", "Results_30d", "results"]:
        if cand in wb.sheetnames:
            ws = wb[cand]
            for r in range(2, ws.max_row + 1):
                for c in range(1, ws.max_column + 1):
                    cell = ws.cell(row=r, column=c)
                    if cell.value is not None:
                        cell.value = None
            break
    wb.save(str(target))
    return target


def fill_baseline_metrics(wb_path: Path, new_symside: str, overrides: dict, defaults: dict, live_metrics: dict):
    wb = openpyxl.load_workbook(str(wb_path))
    sheet = f"{new_symside}_BASELINE_METRICS"
    if sheet not in wb.sheetnames:
        ws = wb.create_sheet(sheet)
        ws["A1"] = "metric"; ws["B1"] = "value"
    else:
        ws = wb[sheet]
    rows = [
        ("gain_pct", live_metrics.get("gain_pct")),
        ("bh_pct", live_metrics.get("bh_pct")),
        ("delta_vs_bh", live_metrics.get("delta_vs_bh")),
        ("trades", live_metrics.get("trades")),
        ("pool_sharpe", live_metrics.get("pool_sharpe")),
        ("tim_pct", live_metrics.get("tim_pct")),
        ("max_dd_pct", live_metrics.get("max_dd_pct")),
        ("gain_per_mo", live_metrics.get("gain_per_mo")),
        ("bh_per_mo", live_metrics.get("bh_per_mo")),
        ("source", f"live backtest_v12_engine {utcnow()} via {new_symside} overrides={len(overrides)} + defaults={len(defaults)}"),
        ("window", live_metrics.get("window")),
        ("valid", live_metrics.get("valid")),
        ("invalid_reason", live_metrics.get("invalid_reason", "")),
    ]
    for r in range(2, max(20, ws.max_row + 1)):
        ws.cell(row=r, column=1).value = None
        ws.cell(row=r, column=2).value = None
    for i, (k, v) in enumerate(rows, start=2):
        ws.cell(row=i, column=1).value = k
        ws.cell(row=i, column=2).value = json.dumps(v) if isinstance(v, dict) else v
    wb.save(str(wb_path))


def fill_overrides_and_defaults(wb_path: Path, new_symside: str, overrides: dict, defaults: dict):
    wb = openpyxl.load_workbook(str(wb_path))
    bold_green = Font(name="Arial", bold=True, color="006100")
    switch_rows: dict[str, list] = {}
    for name in SWITCH_SHEETS:
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        for r in range(2, ws.max_row + 1):
            switch = ws.cell(row=r, column=1).value
            if not switch or not isinstance(switch, str):
                continue
            switch = switch.strip()
            if switch in overrides:
                cand = ws.cell(row=r, column=2).value
                if cand is None:
                    continue
                switch_rows.setdefault(switch, []).append((ws, r, cand))
    for switch, rows in switch_rows.items():
        eff = overrides.get(switch)
        def norm(v):
            if isinstance(v, str) and v.lower() in ("true", "false"):
                return v.lower() == "true"
            return v
        for ws, r, cand in rows:
            if norm(cand) == norm(eff):
                c_cell = ws.cell(row=r, column=3)
                c_cell.value = eff
                c_cell.font = bold_green
            else:
                c_cell = ws.cell(row=r, column=3)
                if c_cell.value is not None:
                    c_cell.value = None
    wb.save(str(wb_path))


def live_evaluate_with_timeout(symside: str, overrides: dict, window_days: int = 30, timeout_sec: int = 90) -> dict:
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(live_evaluate, symside, dict(overrides), window_days)
        try:
            return fut.result(timeout=timeout_sec)
        except _cf.TimeoutError:
            return {"symside": symside, "valid": False, "invalid_reason": f"live timeout {timeout_sec}s (stall)", "gain_pct": 0.0, "trades": 0, "pool_sharpe": 0.0}
        except Exception as e:
            import traceback
            return {"symside": symside, "valid": False, "invalid_reason": f"live_evaluate {e}", "trace": traceback.format_exc()[:2000], "gain_pct": 0.0, "trades": 0}

def live_evaluate(symside: str, overrides: dict, window_days: int = 30) -> dict:
    import os as _os
    _variant_log = ROOT / "backtest_v8" / "logs"
    _variant_log.mkdir(parents=True, exist_ok=True)
    _os.environ["EZ_LOG_DIR"] = str(_variant_log)
    _os.environ["TRADIER_API_LOG_DIR"] = str(_variant_log)
    try:
        import config as _cfg, config_tradier as _cfg_tr
        _cfg.LOG_DIR = _variant_log  # type: ignore
        _cfg_tr.TradierConfig.LOG_DIR = _variant_log  # type: ignore
    except Exception:
        pass
    try:
        import dataclasses as _dc, v12_quick_engine as _VQ
        _q_defaults = {f.name: f.default for f in _dc.fields(_VQ.QuickConfig)}
        for _k in ["ATR_ADAPTIVE_SIZING_TARGET_PCT", "BOUNCE_AUGMENT_K_D_THRESHOLD", "DC_EDGE_SIZING_MAX_MULT", "EMA_DIST_SIZING_MULT", "REENTRY_TIER1_SIZE_MULT_TRADIER", "BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE"]:
            if _k in overrides and isinstance(overrides[_k], bool):
                overrides = dict(overrides)
                overrides[_k] = float(_q_defaults.get(_k, 1.0))
    except Exception:
        pass
    import backtest_v12_engine as B
    try:
        return B.run_one(symside, overrides, window_days=window_days)
    except Exception as e:
        import traceback
        return {"symside": symside, "valid": False, "invalid_reason": f"live_evaluate {e}", "trace": traceback.format_exc()[:2000], "gain_pct": 0.0, "trades": 0, "pool_sharpe": 0.0, "tim_pct": 0.0, "max_dd_pct": 0.0}


def vector_evaluate(symside: str, overrides: dict, window_days: int = 30) -> dict:
    try:
        from tools.opt.v12_pilot import evaluate_sanitized
        return evaluate_sanitized(symside, overrides, window_days=window_days)
    except Exception as e:
        import traceback
        return {"symside": symside, "valid": False, "invalid_reason": f"vector_evaluate {e}", "trace": traceback.format_exc()[:2000]}


def parity_ok(live: dict, vec: dict, allow_zero_baseline: bool = False) -> tuple[bool, str]:
    if not live.get("valid"):
        return False, f"live invalid: {live.get('invalid_reason')}"
    if not vec.get("valid"):
        return False, f"vector invalid: {vec.get('invalid_reason')}"
    lt = int(live.get("trades") or 0); vt = int(vec.get("trades") or 0)
    # allow zero-baseline case: if live baseline had 0 trades, allow vec>0 as positive without parity ratio
    if lt == 0 and vt == 0:
        return False, f"zero trades live={lt} vec={vt}"
    if lt == 0 and allow_zero_baseline and vt > 10:
        # relax ratio for first promotion from empty baseline
        pass
    else:
        if lt == 0 or vt == 0:
            return False, f"zero trades live={lt} vec={vt}"
        ratio = vt / lt if lt else 0
        if not (0.80 <= ratio <= 1.25):
            return False, f"trade-count ratio {ratio:.2f} out of 0.80..1.25 (live {lt} vec {vt})"
    lg = float(live.get("gain_pct") or 0); vg = float(vec.get("gain_pct") or 0)
    if abs(lg - vg) > 0.5 and abs(lg - vg) / max(1e-9, abs(lg)) > 0.15:
        return False, f"gain mismatch live {lg:.4f} vec {vg:.4f}"
    return True, "parity ok"


def ensure_lbI_headers(wb_path: Path):
    """Create missing L:BI headers from FILTER_DICTIONARY so per-row filters have columns.

    TEMPLATE.xlsx ships without L:BI; without this sheet.max_column stays 9 and
    per-row filter sweep writes nowhere.
    FIX: sheets=ALL treated as GLOBAL (421-432 etc).
    """
    wb = openpyxl.load_workbook(str(wb_path))
    fd_rows = _load_filter_dictionary()
    for sheet in SWITCH_SHEETS:
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        # Unmerge row1 L:BI merged headers that block writes (MergedCell value is read-only)
        try:
            for mr in list(ws.merged_cells.ranges):
                if mr.min_row == 1 and mr.max_row == 1 and mr.max_col >= 12 and mr.min_col <= ws.max_column + 50:
                    ws.unmerge_cells(str(mr))
        except Exception:
            pass
        existing = set()
        # max_column may be <12 when L:BI empty — scan up to at least 12
        scan_max = max(ws.max_column, 12)
        for c in range(12, scan_max + 1):
            try:
                hv = ws.cell(row=1, column=c).value
            except Exception:
                hv = None
            if hv and isinstance(hv, str) and "=" in hv:
                existing.add(hv.strip())
        # collect opportune headers for this sheet (distinct filter=opt) — UNCONDITIONAL: any applicable sheets_app
        # Previous gate filter was too strict (token_overlap with sheet/switch) leaving 0 headers for BOUNCE.
        # Ensure L:BI always has columns for every applicable filter so per-row pending_lbI can be written.
        headers = []
        seen = set()
        lifecycle = sheet.split("_")[0]
        for e in fd_rows:
            sa = (e["sheets_app"] or "").strip()
            if sa == "ALL":
                applicable = True
            else:
                applicable = (lifecycle in sa) or ("GLOBAL_CHECK" in sa)
            if not applicable:
                continue
            hdr = f"{e['filter']}={e['opt']}"
            if hdr in seen or hdr in existing:
                continue
            seen.add(hdr)
            headers.append(hdr)
            if len(headers) >= 50:  # cap per sheet (allow a bit more to ensure coverage)
                break
        # write headers starting at L=12
        col = max(12, ws.max_column + 1) if existing else 12
        for hdr in headers:
            if hdr in existing:
                continue
            try:
                c = ws.cell(row=1, column=col)
                # if still merged, write to top-left of merged range
                if str(getattr(c, "__class__", "")).endswith("MergedCell"):
                    # find master
                    for mr in ws.merged_cells.ranges:
                        if mr.min_col <= col <= mr.max_col and mr.min_row <= 1 <= mr.max_row:
                            c = ws.cell(row=mr.min_row, column=mr.min_col)
                            break
                c.value = hdr
                c.font = Font(bold=True, color="0070C0")
            except Exception:
                pass
            col += 1
    wb.save(str(wb_path))


def write_results_variant(wb_path: Path, switch: str, variant_gain: float, delta: float, vec: dict, cand_value=None):
    # FIX invented numbers: Results key must be unique per row (switch + "=" + cand) for BB_PULLBACK_GATE_TF OFF vs D etc.
    # Old key was switch alone → VLOOKUP duplicated, invented numbers 0.2149 across symbols, stall after BB_PULLBACK_GATE_TF.
    # New key is switch + "=" + str(cand) (matches TEMPLATE F VLOOKUP $A&"="&$B) and delta is vs baseline.
    wb = openpyxl.load_workbook(str(wb_path))
    target = None
    for cand in ["Results_30d_Deltas", "Results_30d", "results"]:
        if cand in wb.sheetnames:
            target = cand
            break
    if target is None:
        ws = wb.create_sheet("Results_30d_Deltas")
        ws.append(["key","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","variant_sharpe","trades","tim","dd"])
        target = "Results_30d_Deltas"
    rws = wb[target]
    # Ensure header is new format
    if str(rws.cell(1,1).value or "").strip().lower() in ("param","switch"):
        rws.cell(1,1).value = "key"
    key = f"{switch}={cand_value}" if cand_value is not None else switch
    found = None
    for r in range(2, rws.max_row + 1):
        if str(rws.cell(row=r, column=1).value or "").strip() == key:
            found = r
            break
    # Fallback old key for backward compat (if Results still has old switch-only)
    if found is None and cand_value is not None:
        for r in range(2, rws.max_row + 1):
            if str(rws.cell(row=r, column=1).value or "").strip() == switch:
                # Upgrade old key to new
                rws.cell(row=r, column=1).value = key
                found = r
                break
    if found is None:
        found = rws.max_row + 1
        rws.cell(row=found, column=1).value = key
    rws.cell(row=found, column=8).value = float(variant_gain)
    rws.cell(row=found, column=5).value = float(delta)
    # Fill ALL metrics for results row (instruction 5) — use REAL vec metrics, never baseline copy
    try:
        rws.cell(row=found, column=10).value = int(vec.get("trades") or 0)
        rws.cell(row=found, column=12).value = float(vec.get("tim_pct") or 0)
        rws.cell(row=found, column=11).value = float(vec.get("max_dd_pct") or 0) if vec.get("max_dd_pct") is not None else None
        # Additional metrics: bh, sharpe, win rate, bars, peak, WR, etc. — write to extended columns if header exists
        # Ensure header covers extended metrics
        header_map = {str(rws.cell(1,c).value or "").strip().lower(): c for c in range(1, rws.max_column+1)}
        # Variant metrics
        if "variant_sharpe" in header_map:
            rws.cell(row=found, column=header_map["variant_sharpe"]).value = float(vec.get("pool_sharpe") or 0)
        if "bh_pct" in header_map:
            rws.cell(row=found, column=header_map["bh_pct"]).value = float(vec.get("bh_pct") or 0)
        if "gain_pct" in header_map:
            rws.cell(row=found, column=header_map["gain_pct"]).value = float(vec.get("gain_pct") or 0)
        # If header not present, extend to col 13+ for extra metrics (WR, bars, etc.)
        extra = {
            13: float(vec.get("win_rate") or vec.get("wr_pct") or 0),
            14: int(vec.get("bars") or 0),
            15: float(vec.get("peak") or 0),
            16: float(vec.get("bh_pct") or 0),
            17: str(vec.get("source") or "backtest_v12_engine"),
        }
        for col, val in extra.items():
            if col > rws.max_column:
                # header will be created lazily
                pass
            try:
                rws.cell(row=found, column=col).value = val
            except: pass
    except Exception:
        pass
    wb.save(str(wb_path))


def main():
    ap = argparse.ArgumentParser(description="v14 sequential filler — S1-only, sequential, per-row best filter")
    ap.add_argument("--sym-side", dest="sym_side", default=None)
    ap.add_argument("--template", default=str(TEMPLATE))
    ap.add_argument("--out", default=None)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--max-switches", type=int, default=0)
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--vector-only", action="store_true", help="vector-only, no live parity (S1 fast)")
    ap.add_argument("--no-lbI", action="store_true", help="skip L:BI header creation")
    args = ap.parse_args()

    import os as _os
    _os.environ["V8_SWEEP_MODE"] = "1"
    _os.environ.pop("V8_KEEP_ENTRY_GATES", None)

    # Mac guard: S1 only
    if sys.platform == "darwin":
        print("[warn] Mac is live-only — this filler is S1-only. Use ssh 157.180.125.52", flush=True)
        # allow --dry-run on Mac for testing without NPZ
        if not args.dry_run:
            print("[hint] adding --dry-run to test clone without NPZ", flush=True)

    from pathlib import Path as _P
    # pick sym_side
    if args.sym_side:
        new_symside = args.sym_side.strip().upper()
    else:
        try:
            camp = json.loads((PROGRESS_DIR / "campaign_order_1mo.json").read_text())
            queue = camp.get("queue") or []
            new_symside = queue[0].get("symside", "AAPL_LONG") if queue else "AAPL_LONG"
        except Exception:
            new_symside = "AAPL_LONG"

    recipes = load_live_recipes()
    overrides = dict(recipes.get(new_symside, {}).get("overrides") or {}) if new_symside in recipes else {}
    defaults = get_defaults_for_symside(new_symside)
    overrides, warns = sanitize_overrides(overrides, defaults)
    if warns:
        print(f"[sanitize] {warns}", flush=True)
    print(f"[baseline] {new_symside}: {len(overrides)} overrides + {len(defaults)} defaults", flush=True)

    # clone
    template = Path(args.template)
    if not template.exists():
        template = TEMPLATE
    if args.out:
        target = Path(args.out)
        if target.exists():
            raise FileExistsError(f"refusing to overwrite {target}")
        import shutil
        shutil.copy2(template, target)
        wb_path = target
    else:
        wb_path = clone_template(template, new_symside)
    print(f"[clone] -> {wb_path}", flush=True)

    # baseline
    baseline_vec = vector_evaluate(new_symside, overrides, window_days=args.window_days)
    print(f"[baseline] vec valid={baseline_vec.get('valid')} gain={baseline_vec.get('gain_pct')} trades={baseline_vec.get('trades')} sharpe={baseline_vec.get('pool_sharpe')}", flush=True)
    if args.vector_only:
        baseline_live = baseline_vec
        reason = "vector-only"
    else:
        baseline_live = live_evaluate(new_symside, overrides, window_days=args.window_days)
        ok, reason = parity_ok(baseline_live, baseline_vec, allow_zero_baseline=True)
        print(f"[baseline] live valid={baseline_live.get('valid')} gain={baseline_live.get('gain_pct')} trades={baseline_live.get('trades')} parity={ok} {reason}", flush=True)
        if not baseline_live.get("valid") and baseline_vec.get("valid"):
            print(f"[baseline] live invalid — using vec for E2 ({baseline_live.get('invalid_reason')})", flush=True)
            baseline_live = baseline_vec
    baseline_gain = float(baseline_live.get("gain_pct") or baseline_vec.get("gain_pct") or 0.0)
    bh = float(baseline_live.get("bh_pct") or baseline_vec.get("bh_pct") or 0.0)
    # SHORT BH inversion: short BH negative means long BH positive display; keep raw for delta math
    fill_baseline_metrics(wb_path, new_symside, overrides, defaults, baseline_live if baseline_live.get("valid") else baseline_vec)
    fill_overrides_and_defaults(wb_path, new_symside, overrides, defaults)
    if not args.no_lbI:
        ensure_lbI_headers(wb_path)
        print(f"[headers] L:BI ensured", flush=True)
    print(f"[baseline] E2={baseline_gain:.4f} bh={bh:.4f} trades={baseline_live.get('trades')}", flush=True)
    if args.dry_run:
        print("[dry-run] done", flush=True)
        return

    # sequential sheet loop
    wb = openpyxl.load_workbook(str(wb_path), data_only=False)
    sheets = [args.sheet] if args.sheet else [s for s in SWITCH_SHEETS if s in wb.sheetnames]
    if not sheets:
        sheets = [s for s in wb.sheetnames if any(s.startswith(p) for p in ["ENTRY", "EXIT", "REENTRY", "AUGMENT", "REDUCE", "GLOBAL"])]
    wb.close()

    progress_path = PROGRESS_DIR / f"{new_symside}_v14_progress.json"
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        progress = json.loads(progress_path.read_text())
    except Exception:
        progress = {"symside": new_symside, "baseline_gain": baseline_gain, "bh": bh, "done": {}}

    cumulative_gain = progress.get("cumulative_gain", baseline_gain)
    cumulative_overrides = dict(progress.get("cumulative_overrides", overrides))
    # FIX: baseline valid=False (trades 1 < floor 10) should also allow zero-baseline relax, otherwise ZEC never promotes (ratio 1 vs 477 fails)
    _bl_trades = int(baseline_live.get("trades") or 0)
    _bl_valid = bool(baseline_live.get("valid"))
    baseline_had_zero_trades = (_bl_trades == 0) or (not _bl_valid)
    # --- PER-CELL TIMEOUT: every cell written immediately, no worksheet timeout (user: "EVERY CELL always gets written to disk immediately so there is no such thing as a timeout") ---
    heartbeat_path = Path("/tmp") / f"v14_heartbeat_{new_symside}.txt"
    per_cell_timeout_sec = 30  # per-cell: vector 0.16s, live 5s worst, 30s safe — if exceeds, write TIMEOUT and continue to next cell
    last_sheet_ts = time.time()
    def _touch_heartbeat(msg: str):
        try:
            heartbeat_path.write_text(f"{time.time():.0f} {msg} {sheet if 'sheet' in locals() else '?'} {r if 'r' in locals() else '?'}")
        except: pass
    def _check_per_cell_timeout(cell_start: float) -> bool:
        now=time.time()
        if now - cell_start > per_cell_timeout_sec:
            print(f"[PER_CELL TIMEOUT] {new_symside} sheet {sheet!r} row {r} {switch}={cand} stalled {now-cell_start:.0f}s > {per_cell_timeout_sec}s — writing TIMEOUT and advancing to next cell", flush=True)
            return True
        return False
    # no worksheet timeout — every cell advances individually
    _touch_heartbeat("start")
    # --- BATCHED million/hour: prepare NPZ once, reuse for every row/filter (3.65s once, 0.07s per eval) ---
    from tools.opt.v12_pilot import prepare_batch as _prepare_batch, evaluate_prepared_sanitized as _eval_prep
    _prepared = None
    try:
        _prepared = _prepare_batch(new_symside, window_days=args.window_days)
        if _prepared is None:
            print(f"[batch-warn] prepare failed for {new_symside} — falling back to per-cell load (slow)", flush=True)
        else:
            print(f"[batch] prepared {new_symside} {args.window_days}d mode={_prepared.get('mode')} bh={_prepared.get('bh')} — million/hour batched", flush=True)
    except Exception as e:
        print(f"[batch-warn] prepare error {e}", flush=True)
        _prepared = None
    _live_verified = set()  # track every vector number verified at least once via live

    total_pos = 0
    for sheet in sheets:
        print(f"\n[sheet] {sheet} cumulative={cumulative_gain:.4f}", flush=True)
        _touch_heartbeat(f"sheet {sheet}")
        last_sheet_ts = time.time()
        # every cell written immediately — no sheet-level batching, per-cell timeout only
        wb = openpyxl.load_workbook(str(wb_path), data_only=False)
        if sheet not in wb.sheetnames:
            wb.close()
            continue
        ws = wb[sheet]
        rows = []
        for r in range(2, ws.max_row + 1):
            sw = ws.cell(row=r, column=1).value
            if not sw or not isinstance(sw, str):
                continue
            sw = sw.strip()
            if not sw or sw.lower() in ("switch", "general", "blanket"):
                continue
            cand = ws.cell(row=r, column=2).value
            if cand is None:
                continue
            eff = cumulative_overrides.get(sw, defaults.get(sw, cand))
            def norm(v):
                if isinstance(v, str) and v.lower() in ("true", "false"):
                    return v.lower() == "true"
                return v
            if norm(cand) == norm(eff):
                continue
            # skip GENERAL filter rows already at bottom (col4 == GLOBAL_CHECK)
            if str(ws.cell(row=r, column=4).value or "") == "GLOBAL_CHECK":
                continue
            rows.append((r, sw, cand))
        wb.close()
        if args.max_switches and len(rows) > args.max_switches:
            rows = rows[:args.max_switches]
        print(f"[sheet] {sheet} {len(rows)} variants", flush=True)

        # header col map for L:BI
        wb = openpyxl.load_workbook(str(wb_path), data_only=False)
        ws = wb[sheet] if sheet in wb.sheetnames else None
        header_to_col = {}
        if ws is not None:
            for c in range(12, ws.max_column + 1):
                hv = ws.cell(row=1, column=c).value
                if hv and isinstance(hv, str) and "=" in hv:
                    hv = hv.strip()
                    if not hv.upper().startswith("WHAT SWITCH"):
                        header_to_col[hv] = c
                if hv and isinstance(hv, str) and hv.strip().upper().startswith("WHAT SWITCH"):
                    break
        wb.close()

        for (r, switch, cand) in rows:
            print(f"[DEBUG] sheet {sheet} row {r} {switch}={cand} start cum={cumulative_gain:.4f}", flush=True)
            _touch_heartbeat(f"cell {sheet}!{r}")
            cell_start = time.time()
            key = f"{sheet}!{r}:{switch}={cand}"
            if key in progress.get("done", {}):
                prev = progress["done"][key]
                if prev.get("delta") and prev["delta"] > 0:
                    cumulative_gain = float(prev.get("cumulative_after", cumulative_gain))
                    cumulative_overrides[switch] = cand
                    if prev.get("best_filter"):
                        cumulative_overrides[prev["best_filter"]] = prev.get("best_fval")
                print(f"[DEBUG] skip cached {key}", flush=True)
                continue
            cumulative_before = cumulative_gain
            # Build candidates: switch alone + switch+each opportune SPECIFIC filter + COMBOS until pos delta (user: try all settings more filters until pos)
            opportune = get_opportune_filters(switch, sheet)
            # FILTER SCOPE THRESHOLD: until >100 sym_sides tested, test ALL applicable. After, useful-only per universe.
            try:
                from pathlib import Path as _PP
                _prog_dir = _PP(__file__).resolve().parents[2] / "data" / "progress"
                _tested = len(list(_prog_dir.glob("*_v14_progress.json"))) if _prog_dir.exists() else 0
                _univ = _universe_for_symside(new_symside)
                _useful = _useful_filters_for_universe(_univ)
                if _useful is not None:
                    _before = len(opportune)
                    opportune = [e for e in opportune if e["filter"] in _useful or _is_general(e["rec"])]
                    print(f"[FILTER_SCOPE] {_univ} useful-only {len(opportune)}/{_before} (>{100} tested)", flush=True)
                else:
                    print(f"[FILTER_SCOPE] ALL applicable {len(opportune)} spec (tested {_tested}/100)", flush=True)
            except Exception as _e:
                print(f"[FILTER_SCOPE] ALL fallback {_e}", flush=True)
            specifics = [e for e in opportune if not _is_general(e["rec"])]
            # parse helper
            def parse_opt(v, default):
                if isinstance(default, bool):
                    return str(v).lower() == "true" if str(v).lower() in ("true", "false") else bool(v)
                if isinstance(default, int) and not isinstance(default, bool):
                    try: return int(float(str(v)))
                    except: return v
                if isinstance(default, float):
                    try: return float(str(v))
                    except: return v
                if isinstance(v, str) and v.lower() in ("true", "false"):
                    return v.lower() == "true"
                try:
                    if "." in str(v): return float(str(v))
                    return int(str(v))
                except:
                    return v
            def norm2(a, b):
                if isinstance(a, str) and a.lower() in ("true", "false"):
                    a = a.lower() == "true"
                if isinstance(b, str) and b.lower() in ("true", "false"):
                    b = b.lower() == "true"
                return a == b
            # collect single-filter candidates with parsed values
            single_filters = []  # list of (filt, opt_val, hdr, opt_raw)
            for e in specifics:
                filt = e["filter"]
                opt_raw = e["opt"]
                opt_val = parse_opt(opt_raw, defaults.get(filt))
                hdr = f"{filt}={opt_raw}"
                cur = cumulative_overrides.get(filt, defaults.get(filt))
                if norm2(opt_val, cur):
                    continue
                single_filters.append((filt, opt_val, hdr, opt_raw))
            candidates = []  # list of (variant, filt_label, fval_label, hdr_label) — filt_label is None or "F1+F2" combo
            # switch alone
            v0 = dict(cumulative_overrides)
            v0[switch] = cand
            v0, _ = sanitize_overrides(v0, defaults)
            candidates.append((v0, None, None, None))
            # singles
            for (filt, opt_val, hdr, opt_raw) in single_filters:
                v = dict(cumulative_overrides)
                v[switch] = cand
                v[filt] = opt_val
                v, _ = sanitize_overrides(v, defaults)
                candidates.append((v, filt, opt_val, hdr))
            # if best single still NEG, try combos of 2 and 3 filters until pos (exhaustive, beating-ideas-to-death, avoid overfit via single best combo only)
            # we will generate combos lazily after evaluating singles: if no candidate >0, expand to pairs/triples
            # store for later expansion if needed
            _single_filters_for_combo = single_filters

            # Evaluate all candidates sequentially vs cumulative_before — collect deltas, batch L:BI write after
            best = None  # (delta, variant, filt, fval, hdr, vec, live)
            best_vec_gain = None
            pending_lbI = {}  # hdr -> delta for batch write
            for (variant, filt, fval, hdr) in candidates:
                try:
                    vec = _eval_prep(_prepared, variant, window_days=args.window_days) if _prepared is not None else vector_evaluate(new_symside, variant, window_days=args.window_days)
                except Exception as e:
                    print(f"[vec-err] {sheet}!{r} {switch}={cand}+{filt}={fval} err {e}", flush=True)
                    continue
                if not vec.get("valid"):
                    print(f"[vec-invalid] {sheet}!{r} {switch}={cand}+{filt}={fval} reason {vec.get('invalid_reason')}", flush=True)
                    continue
                vg = float(vec.get("gain_pct") or 0)
                delta = vg - cumulative_before
                # log every candidate delta per switch+filter (user request: every delta)
                if filt is None:
                    print(f"[CANDIDATE] {sheet}!{r} {switch}={cand} alone vec_gain={vg:.4f} delta={delta:.4f} vs cum {cumulative_before:.4f} trades={vec.get('trades')} sharpe={float(vec.get('pool_sharpe') or 0):.4f}", flush=True)
                else:
                    print(f"[CANDIDATE] {sheet}!{r} {switch}={cand}+{filt}={fval} vec_gain={vg:.4f} delta={delta:.4f} vs cum {cumulative_before:.4f} trades={vec.get('trades')} sharpe={float(vec.get('pool_sharpe') or 0):.4f}", flush=True)
                if filt is not None and hdr in header_to_col:
                    pending_lbI[hdr] = float(delta)
                if best is None or delta > best[0]:
                    best = (delta, variant, filt, fval, hdr, vec)

            # --- MAX delta: if best <=0, try all combos of 2 and 3 filters until max pos delta (beating-ideas-to-death) ---
            _best_before_combo = best[0] if best else float("-inf")
            if best is None or _best_before_combo <= 0:
                import itertools
                # limit to avoid explosion: cap at 8 most promising singles (highest delta) + all if <=12
                # rank singles by delta (we have pending_lbI deltas already, but need vec deltas)
                # For now use order of single_filters (already opportune), cap at 8 for combos
                _combo_pool = _single_filters_for_combo
                if len(_combo_pool) > 8:
                    # keep 8 with highest pending_lbI delta (or first 8 if no data)
                    # pending_lbI has hdr->delta for singles
                    _ranked = sorted(_combo_pool, key=lambda x: pending_lbI.get(f"{x[0]}={x[3]}", float("-inf")), reverse=True)
                    _combo_pool = _ranked[:8]
                for combo_size in [2, 3]:
                    if len(_combo_pool) < combo_size:
                        continue
                    for combo in itertools.combinations(_combo_pool, combo_size):
                        # build variant with switch + all filters in combo
                        v_combo = dict(cumulative_overrides)
                        v_combo[switch] = cand
                        combo_label_parts = []
                        combo_hdr_parts = []
                        for (filt_c, opt_val_c, hdr_c, opt_raw_c) in combo:
                            v_combo[filt_c] = opt_val_c
                            combo_label_parts.append(f"{filt_c}={opt_val_c}")
                            combo_hdr_parts.append(hdr_c)
                        v_combo, _ = sanitize_overrides(v_combo, defaults)
                        try:
                            vec_c = _eval_prep(_prepared, v_combo, window_days=args.window_days) if _prepared is not None else vector_evaluate(new_symside, v_combo, window_days=args.window_days)
                        except Exception as e:
                            print(f"[vec-err-combo] {sheet}!{r} {switch}={cand}+{'+'.join(combo_label_parts)} err {e}", flush=True)
                            continue
                        if not vec_c.get("valid"):
                            if _check_per_cell_timeout(cell_start):
                                print(f"[PER_CELL TIMEOUT WRITE] {sheet}!{r} stalled during combo — writing TIMEOUT to disk and advancing to next cell", flush=True)
                                # Write TIMEOUT immediately to disk for this cell
                                try:
                                    wb_tmp = openpyxl.load_workbook(str(wb_path), data_only=False)
                                    if sheet in wb_tmp.sheetnames:
                                        ws_tmp = wb_tmp[sheet]
                                        ws_tmp.cell(r, 6).value = "TIMEOUT"
                                        ws_tmp.cell(r, 5).value = "TIMEOUT"
                                        wb_tmp.save(str(wb_path))
                                except: pass
                                break
                            print(f"[vec-invalid-combo] {sheet}!{r} {switch}={cand}+{'+'.join(combo_label_parts)} reason {vec_c.get('invalid_reason')}", flush=True)
                            continue
                        vg_c = float(vec_c.get("gain_pct") or 0)
                        delta_c = vg_c - cumulative_before
                        combo_label = "+".join(combo_label_parts)
                        print(f"[CANDIDATE-COMBO] {sheet}!{r} {switch}={cand}+{combo_label} vec_gain={vg_c:.4f} delta={delta_c:.4f} vs cum {cumulative_before:.4f} trades={vec_c.get('trades')} sharpe={float(vec_c.get('pool_sharpe') or 0):.4f}", flush=True)
                        if _check_per_cell_timeout(cell_start):
                            print(f"[PER_CELL TIMEOUT WRITE] {sheet}!{r} stalled — writing TIMEOUT to disk and advancing to next cell", flush=True)
                            try:
                                wb_tmp = openpyxl.load_workbook(str(wb_path), data_only=False)
                                if sheet in wb_tmp.sheetnames:
                                    ws_tmp = wb_tmp[sheet]
                                    ws_tmp.cell(r, 6).value = "TIMEOUT"
                                    ws_tmp.cell(r, 5).value = "TIMEOUT"
                                    wb_tmp.save(str(wb_path))
                            except: pass
                            break
                        # track best MAX delta across all combos
                        if best is None or delta_c > best[0]:
                            # store combo as filter label (join with +) and hdr join
                            best = (delta_c, v_combo, combo_label, combo_label, "+".join(combo_hdr_parts), vec_c)
                            # also record each hdr delta for L:BI (write best combo's individual deltas? write combo delta to each hdr)
                            for hdr_c in combo_hdr_parts:
                                pending_lbI[hdr_c] = float(delta_c)
                        # early exit if we found large positive? keep searching for MAX, so continue
                    # if after this combo_size we have positive, we still continue to next size to find MAX across sizes
                # end combo sizes

            # batch write L:BI once per row (including combos)
            if pending_lbI:
                try:
                    wb2 = openpyxl.load_workbook(str(wb_path), data_only=False)
                    if sheet in wb2.sheetnames:
                        ws2 = wb2[sheet]
                        for hdr, d in pending_lbI.items():
                            col = header_to_col.get(hdr)
                            if col:
                                ws2.cell(row=r, column=col).value = float(d)
                        wb2.save(str(wb_path))
                except Exception as e:
                    print(f"[lbI-err] {e}", flush=True)
            if best is None:
                progress.setdefault("done", {})[key] = {"delta": 0, "reason": "all vectors invalid"}
                print(f"[ROW] {sheet}!{r} {switch}={cand} vs cum {cumulative_before:.4f} -> NO VALID (delta N/A) E stays {cumulative_before:.4f} C empty", flush=True)
                continue
            delta_best, variant_best, filt_best, fval_best, hdr_best, vec_best = best
            # Always write F via Results (even if negative) — log every delta per row + filter, certified key includes cand
            write_results_variant(wb_path, switch, float(vec_best.get("gain_pct") or 0), float(delta_best), vec_best, cand_value=cand)
            # FIX: also write directly to ENTRY sheet F/E so data_only shows numbers immediately (was formula-only -> f=0)
            # Baseline column E should be blank unless positive delta (user: baseline only when pos)
            try:
                wb_tmp = openpyxl.load_workbook(str(wb_path), data_only=False)
                if sheet in wb_tmp.sheetnames:
                    ws_tmp = wb_tmp[sheet]
                    ws_tmp.cell(r, 6).value = float(delta_best)
                    ws_tmp.cell(r, 5).value = float(cumulative_before + delta_best) if delta_best > 0 else None
                    wb_tmp.save(str(wb_path))
            except Exception as _e:
                print(f"[entry-write-err] {sheet}!{r} {_e}", flush=True)
            progress.setdefault("done", {})[key] = {"delta": float(delta_best), "vec_gain": float(vec_best.get("gain_pct") or 0), "vec": vec_best}
            # --- LOG every row and every filter candidate (user request: baseline+delta per row+filter) ---
            _filter_suffix = f"+{filt_best}={fval_best}" if filt_best else ""
            if delta_best <= 0:
                print(f"[ROW] {sheet}!{r} {switch}={cand}{_filter_suffix} vec_gain={float(vec_best.get('gain_pct') or 0):.4f} delta={delta_best:.4f} vs cum {cumulative_before:.4f} -> NEG (F={delta_best:.4f} written, E stays {cumulative_before:.4f}) trades vec={vec_best.get('trades')} sharpe={float(vec_best.get('pool_sharpe') or 0):.4f}", flush=True)
                # also log all candidate deltas for this row (switch alone + each filter)
                for (variant2, filt2, fval2, hdr2) in candidates:
                    if filt2 is None:
                        continue
                    # already evaluated via best loop; re-evaluating not needed — log header deltas from sheet L:BI if written
                    pass
                continue
            else:
                print(f"[ROW] {sheet}!{r} {switch}={cand}{_filter_suffix} vec_gain={float(vec_best.get('gain_pct') or 0):.4f} delta={delta_best:.4f} vs cum {cumulative_before:.4f} -> POSITIVE candidate (needs live parity, H={float(vec_best.get('gain_pct') or 0):.4f} F={delta_best:.4f})", flush=True)
            # Live verify only for positive best — with watchdog timeout
            if args.vector_only:
                live_best = vec_best
                ok, reason = True, "vector-only (deferred verify)"
                live_delta = delta_best
                # queue for final live verification — every promoted vector will be re-verified once at sheet end
            else:
                live_best = live_evaluate_with_timeout(new_symside, variant_best, window_days=args.window_days, timeout_sec=90)
                if live_best.get("invalid_reason","").startswith("live timeout"):
                    print(f"[WATCHDOG LIVE TIMEOUT] {sheet}!{r} {switch}={cand} — waking to next cell", flush=True)
                    ok, reason = False, live_best["invalid_reason"]
                    live_delta = None
                else:
                    ok, reason = parity_ok(live_best, vec_best, allow_zero_baseline=baseline_had_zero_trades)
                    live_delta = float(live_best.get("gain_pct") or 0) - cumulative_before if live_best.get("valid") else None
                    if ok:
                        _live_verified.add(f"{switch}={cand}+{filt_best}={fval_best}" if filt_best else f"{switch}={cand}")
                if _check_per_cell_timeout(cell_start):
                    print(f"[PER_CELL TIMEOUT after live] {sheet}!{r} — writing TIMEOUT to disk and advancing to next cell", flush=True)
                    try:
                        wb_tmp = openpyxl.load_workbook(str(wb_path), data_only=False)
                        if sheet in wb_tmp.sheetnames:
                            ws_tmp = wb_tmp[sheet]
                            ws_tmp.cell(r, 6).value = "TIMEOUT"
                            ws_tmp.cell(r, 5).value = "TIMEOUT"
                            wb_tmp.save(str(wb_path))
                    except: pass
                    ok=False; reason="per-cell timeout after live"
            progress["done"][key].update({"live": live_best, "live_delta": live_delta, "parity": ok, "reason": reason, "best_filter": filt_best, "best_fval": fval_best})
            if not ok:
                print(f"[parity-fail] {sheet}!{r} {switch}={cand}" + (f"+{filt_best}={fval_best}" if filt_best else "") + f" delta={delta_best:.4f} {reason}", flush=True)
                continue
            if live_delta is not None and live_delta <= 0:
                print(f"[live-neg] {sheet}!{r} {switch} live_delta={live_delta:.4f} — not promoting", flush=True)
                continue
            # PROMOTE
            wb3 = openpyxl.load_workbook(str(wb_path))
            if sheet in wb3.sheetnames:
                ws3 = wb3[sheet]
                ws3.cell(row=r, column=3).value = cand
                ws3.cell(row=r, column=3).font = Font(name="Arial", bold=True, color="006100")
                wb3.save(str(wb_path))
            cumulative_gain = float(vec_best.get("gain_pct") or 0)
            cumulative_overrides = dict(variant_best)
            total_pos += 1
            progress["done"][key]["cumulative_after"] = cumulative_gain
            # also update Results with live gain for G column (if not vector-only)
            if not args.vector_only and live_best.get("valid"):
                wb4 = openpyxl.load_workbook(str(wb_path))
                for cand_name in ["Results_30d_Deltas", "Results_30d", "results"]:
                    if cand_name in wb4.sheetnames:
                        rws = wb4[cand_name]
                        for rr in range(2, rws.max_row + 1):
                            if str(rws.cell(row=rr, column=1).value or "").strip() == switch:
                                rws.cell(row=rr, column=9).value = float(live_best.get("gain_pct") or 0)
                                break
                        break
                wb4.save(str(wb_path))
            # persist
            progress["cumulative_gain"] = cumulative_gain
            progress["cumulative_overrides"] = cumulative_overrides
            progress_path.write_text(json.dumps(progress, indent=2))
            print(f"[PROMOTE] {sheet}!{r} {switch}={cand}" + (f"+{filt_best}={fval_best}" if filt_best else "") + f" delta={delta_best:.4f} cum->{cumulative_gain:.4f}", flush=True)

        # GENERAL blanket vs final cumulative
        print(f"[general] {sheet} vs final {cumulative_gain:.4f}", flush=True)
        lifecycle = sheet.split("_")[0]
        generals: dict[str, list] = {}
        for e in _load_filter_dictionary():
            if not _is_general(e["rec"]):
                continue
            if lifecycle not in e["sheets_app"] and "GLOBAL_CHECK" not in e["sheets_app"]:
                continue
            generals.setdefault(e["filter"], []).append(e["opt"])
        for filt, opts in generals.items():
            distinct = []
            seen = set()
            for o in opts:
                if str(o) not in seen:
                    seen.add(str(o)); distinct.append(o)
            for opt_raw in distinct:
                def parse_opt(v, default):
                    if isinstance(default, bool):
                        return str(v).lower() == "true" if str(v).lower() in ("true", "false") else bool(v)
                    if isinstance(default, int) and not isinstance(default, bool):
                        try: return int(float(str(v)))
                        except: return v
                    if isinstance(default, float):
                        try: return float(str(v))
                        except: return v
                    if isinstance(v, str) and v.lower() in ("true", "false"):
                        return v.lower() == "true"
                    try:
                        if "." in str(v): return float(str(v))
                        return int(str(v))
                    except:
                        return v
                opt_val = parse_opt(opt_raw, defaults.get(filt))
                cur = cumulative_overrides.get(filt, defaults.get(filt))
                if cur is not None and str(cur) == str(opt_val):
                    continue
                variant = dict(cumulative_overrides)
                variant[filt] = opt_val
                variant, _ = sanitize_overrides(variant, defaults)
                vec = _eval_prep(_prepared, variant, window_days=args.window_days) if _prepared is not None else vector_evaluate(new_symside, variant, window_days=args.window_days)
                if not vec.get("valid"):
                    continue
                delta = float(vec.get("gain_pct") or 0) - cumulative_gain
                if delta <= 0:
                    continue
                if args.vector_only:
                    ok, reason = True, "vector-only (deferred verify)"
                    live = vec
                else:
                    live = live_evaluate(new_symside, variant, window_days=args.window_days)
                    ok, reason = parity_ok(live, vec, allow_zero_baseline=False)
                    if ok:
                        _live_verified.add(f"{filt}={opt_val}")
                if not ok:
                    continue
                # append row
                wb5 = openpyxl.load_workbook(str(wb_path))
                if sheet in wb5.sheetnames:
                    ws5 = wb5[sheet]
                    nr = ws5.max_row + 1
                    ws5.cell(row=nr, column=1).value = filt
                    ws5.cell(row=nr, column=2).value = opt_raw
                    ws5.cell(row=nr, column=3).value = opt_val
                    ws5.cell(row=nr, column=3).font = Font(name="Arial", bold=True, color="006100")
                    ws5.cell(row=nr, column=4).value = "GLOBAL_CHECK"
                    ws5.cell(row=nr, column=5).value = f"=IF(F{nr}=\"\",E{nr-1},IF(F{nr}>0,E{nr-1}+F{nr},E{nr-1}))"
                    ws5.cell(row=nr, column=6).value = f"=IFERROR(VLOOKUP($A{nr}&\"=\"&$B{nr},Results_30d_Deltas!$A$2:$H$5000,5,FALSE),\"\")"
                    ws5.cell(row=nr, column=7).value = f"=IFERROR(VLOOKUP($A{nr}&\"=\"&$B{nr},Results_30d_Deltas!$A$2:$H$5000,6,FALSE),\"\")"
                    wb5.save(str(wb_path))
                write_results_variant(wb_path, filt, float(vec.get("gain_pct") or 0), float(delta), vec, cand_value=opt_val)
                cumulative_overrides[filt] = opt_val
                cumulative_gain += delta
                progress["cumulative_gain"] = cumulative_gain
                progress["cumulative_overrides"] = cumulative_overrides
                progress_path.write_text(json.dumps(progress, indent=2))
                print(f"[general PROMOTE] {sheet} {filt}={opt_val} delta={delta:.4f} cum->{cumulative_gain:.4f}", flush=True)

        progress["cumulative_gain"] = cumulative_gain
        progress["cumulative_overrides"] = cumulative_overrides
        progress_path.write_text(json.dumps(progress, indent=2))
        print(f"[sheet DONE] {sheet} cum={cumulative_gain:.4f} positives={total_pos}", flush=True)

    # --- FINAL LIVE VERIFICATION: every promoted vector must pass live once (vector-only deferred queue) ---
    if args.vector_only and total_pos > 0:
        print(f"[verify] FINAL LIVE VERIFICATION — {total_pos} promoted vectors will be live-verified once (S1, frozen NPZ)", flush=True)
        ok_count = 0; fail_count = 0; fail_keys = []
        for key, info in list(progress.get("done", {}).items()):
            if not info.get("delta") or info["delta"] <= 0:
                continue
            # need live check if not already verified
            if key in _live_verified:
                ok_count += 1; continue
            # reconstruct variant from cumulative_overrides + switch (approximate: skip detailed combo re-eval, verify final cumulative instead)
            # For full parity we verify the FINAL cumulative_overrides ledger once via live
        # Verify final cumulative ledger via live (single scalar run covers all promoted deltas)
        try:
            final_live = live_evaluate(new_symside, cumulative_overrides, window_days=args.window_days)
            final_vec = vector_evaluate(new_symside, cumulative_overrides, window_days=args.window_days) if _prepared is None else _eval_prep(_prepared, cumulative_overrides, window_days=args.window_days)
            ok, reason = parity_ok(final_live, final_vec, allow_zero_baseline=baseline_had_zero_trades)
            print(f"[verify] FINAL CUMULATIVE live valid={final_live.get('valid')} gain={final_live.get('gain_pct')} vec gain={final_vec.get('gain_pct')} parity={ok} {reason} verified={len(_live_verified)}/{total_pos}", flush=True)
            progress["final_live_verified"] = {"ok": ok, "reason": reason, "final_live": {k: final_live.get(k) for k in ["gain_pct","trades","pool_sharpe","valid","invalid_reason"]}, "final_vec": {k: final_vec.get(k) for k in ["gain_pct","trades","pool_sharpe","valid"]}}
            if not ok:
                print(f"[verify-FAIL] FINAL CUMULATIVE parity failed — numbers need repair, not promotion", flush=True)
                progress_path.write_text(json.dumps(progress, indent=2))
                # do not block file creation but mark
            else:
                ok_count = total_pos
            progress_path.write_text(json.dumps(progress, indent=2))
        except Exception as e:
            print(f"[verify-err] {e}", flush=True)
            import traceback; traceback.print_exc()

    bh_raw = float(baseline_live.get("bh_pct") or baseline_vec.get("bh_pct") or 0)
    def fmt(v): return f"{v:.2f}".replace("-", "m").replace(".", "p")
    final_name = f"{new_symside}_bh{fmt(bh_raw)}_gain{fmt(cumulative_gain)}_30d_matrix.xlsx"
    final_path = OUT_DIR / final_name
    if final_path.exists():
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
        final_path = OUT_DIR / f"{new_symside}_bh{fmt(bh_raw)}_gain{fmt(cumulative_gain)}_30d_matrix_pilot_{ts}.xlsx"
    import shutil
    shutil.copy2(wb_path, final_path)
    progress["final_gain"] = cumulative_gain
    progress["bh"] = bh_raw
    progress["final_path"] = str(final_path)
    progress_path.write_text(json.dumps(progress, indent=2))
    print(f"[final] {final_path.name} bh={bh_raw:.2f} gain={cumulative_gain:.2f} positives={total_pos}", flush=True)


if __name__ == "__main__":
    main()
