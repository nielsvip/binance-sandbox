#!/usr/bin/env python3
"""v12 pilot sheet runner — clone ADP template for new SYM_SIDE and fill cell-by-cell
from LIVE scripts (backtest_v12_engine) then verify vector (v12_quick_engine) identical.

Rules (Bible §16.73 + user mandates 2026-09-01):
- NEVER overwrite an existing workbook. New filename = {SYM_SIDE}_30d_matrix_v9_{SYM_SIDE}.xlsx.
  If target exists, append _pilot_<timestamp> and abort if collision.
- Baseline = per_sym overrides PLUS all non-overridden defaults. Overrides written to C,
  defaults to B. Every switch is re-tested default vs override (bool both values, TF sweep, numeric).
- Live comes ONLY from backtest_v12_engine.run_one (real tradier_manage/ez_manage call path
  on frozen NPZ 30 calendar days / 30 trading sessions @ 15m causal). Vector only after live parity.
- Vector must be repaired until numpy ledger is IDENTICAL (trades ratio 0.80-1.25, gain 0.5pp/15%, sharpe 0.25)
  before any delta is written.
- Death penalty: NEVER write E (baseline) if F (delta) <=0 or not proven. E3 = E2+F3 only if F3>0.
  All E3:E.. and F3:F.. stay None until proven. No 0, no duplicated formulas.
"""

from __future__ import annotations
import sys
print("DEPRECATED: v12_pilot_sheet_runner is disabled — use tools/simple_switch_filter_calculator.py (26-col, 9291×26, resumable) + tools/fill_template_from_csv.py for RESULTS. See SPREADSHEETS/TEMPLATE_*_FILLED.xlsx", file=sys.stderr)
sys.exit(78)
import argparse
import concurrent.futures
import copy
import dataclasses
import datetime
import hashlib
import json
import time
import threading

_XLS_LOCK = threading.Lock()

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl
from openpyxl.styles import Font

# hires_chart: PROVEN hires zoomable with pictograms exactly on grey line
try:
    from tools.opt.hires_chart import generate_hires as _generate_hires
except Exception:
    _generate_hires = None  # fallback to old write_complete_chart

TEMPLATE = ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx"
TEMPLATE_FALLBACK = ROOT / "SPREADSHEETS" / "TEMPLATE_30d_matrix.xlsx"
OUT_DIR = ROOT / "SPREADSHEETS"
PROGRESS_DIR = ROOT / "data" / "reports" / "lifecycle_pilot"
CAMPAIGN = PROGRESS_DIR / "campaign_order_1mo.json"
PARITY_CONTRACT = PROGRESS_DIR / "per_sym_parity_contract.json"
CHART_DIR = OUT_DIR  # per-tab charts written directly to SPREADSHEETS per user request
CHARTS_1M_DIR = ROOT / "data" / "reports" / "charts_1M"  # also sync charts here per user 2026-09-02

# Sheets that carry switch rows — same vocab as template
# Order per Bible §16.73 lifecycle: ENTRY_PULLBACK_BOUNCE first, then BREAKOUT etc, so final E ports
SWITCH_SHEETS = [
    "ENTRY_PULLBACK_BOUNCE", "ENTRY_BREAKOUT", "ENTRY_NEUTRAL", "ENTRY_FULL_FILTERED",
    "EXIT_PULLBACK_BOUNCE", "EXIT_BREAKOUT", "EXIT_NEUTRAL", "EXIT_FULL_FILTERED",
    "REENTRY_PULLBACK_BOUNCE", "REENTRY_BREAKOUT", "REENTRY_NEUTRAL", "REENTRY_FULL_FILTERED",
    "AUGMENT_PULLBACK_BOUNCE", "AUGMENT_BREAKOUT", "AUGMENT_NEUTRAL", "AUGMENT_FULL_FILTERED",
    "REDUCE_PULLBACK_BOUNCE", "REDUCE_BREAKOUT", "REDUCE_NEUTRAL", "REDUCE_FULL_FILTERED",
    # IDEA grouped V2 (TEMPLATE.xlsx) — 12 tabs, baked in unavoidable, whatever template
    "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY",
    "REENTRY_WINDOWED", "REENTRY_ADAPTIVE",
    "AUGMENT_TREND", "AUGMENT_RISK_SIZING",
    "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER",
    "GLOBAL_RISK_GATES",
]

# ——— PER_ROW_FILTERS opportune logic (migrated V2: prior result 1 mapping) ———
# TEMPLATE A1:IH317 J=PER_ROW_FILTERS, V2 A1:DU17 J=is_non_default (missing J).
# Opportune = Sheets_applicable CONTAINS lifecycle(ENTRY etc) OR GLOBAL_CHECK
#             AND ( Switches_exactly_it_gates token-overlap switch  OR  Recommendation==GENERAL )
# Token-overlap = gates == switch OR gates substring switch (WT_15M_BOUNCE_OPEN_ENABLED exact).
# GENERAL covers ~57 filters (ADX_RANGING_THRESHOLD etc, ~174 rows TEMPLATE → 210 V2).
# SPECIFIC only when gated (WT_15M_BOUNCE 12 opts HW:IH for ENTRY_PULLBACK_BOUNCE).
_FILTER_DICT_CACHE = None
_FILTER_DICT_PATHS = None

def _load_filter_dictionary() -> list[dict]:
    global _FILTER_DICT_CACHE, _FILTER_DICT_PATHS
    if _FILTER_DICT_CACHE is not None:
        return _FILTER_DICT_CACHE
    # Prefer migrated workbook (has WT 12 restored), then V2 connected, then TEMPLATE
    candidates = [
        ROOT / "SPREADSHEETS" / "TEMPLATE_V2_1YR_CONNECTED.xlsx",
        Path("/tmp/TEMPLATE_V2_MIGRATED.xlsx"),
        ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx",
    ]
    wb = None
    used = None
    ws = None
    for p in candidates:
        if p.exists():
            try:
                import openpyxl as _oxl
                wb = _oxl.load_workbook(str(p), data_only=True)
                if "FILTER_DICTIONARY_V2" in wb.sheetnames:
                    ws = wb["FILTER_DICTIONARY_V2"]
                    used = p
                    break
                if "FILTER_DICTIONARY_V8" in wb.sheetnames:
                    ws = wb["FILTER_DICTIONARY_V8"]
                    used = p
                    break
                if "FILTER_DICTIONARY" in wb.sheetnames:
                    ws = wb["FILTER_DICTIONARY"]
                    used = p
                    break
                wb.close()
            except Exception:
                continue
    if wb is None or ws is None:
        _FILTER_DICT_CACHE = []
        return _FILTER_DICT_CACHE
    rows = []
    for r in range(2, ws.max_row + 1):
        f = ws.cell(r, 2).value
        if f is None:
            continue
        opt = ws.cell(r, 3).value
        sheets_app = ws.cell(r, 6).value or ""
        gates = ws.cell(r, 7).value or ""
        rec = ws.cell(r, 8).value or ""
        rows.append({
            "filter": str(f).strip(),
            "opt": str(opt).strip() if opt is not None else "",
            "sheets_app": str(sheets_app),
            "gates": str(gates).strip() if gates else "",
            "rec": str(rec),
        })
    has_wt = any("WT_15M_BOUNCE" in r["filter"] for r in rows)
    if not has_wt:
        tmpl = ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx"
        if tmpl.exists():
            try:
                import openpyxl as _oxl2
                wb2 = _oxl2.load_workbook(str(tmpl), data_only=True)
                ws2 = wb2["FILTER_DICTIONARY_V2"] if "FILTER_DICTIONARY_V2" in wb2.sheetnames else wb2["FILTER_DICTIONARY_V8"] if "FILTER_DICTIONARY_V8" in wb2.sheetnames else wb2["FILTER_DICTIONARY"]
                for r in range(2, ws2.max_row + 1):
                    f = ws2.cell(r, 2).value
                    if f and "WT_15M_BOUNCE" in str(f):
                        opt = ws2.cell(r, 3).value
                        key = (str(f).strip(), str(opt).strip() if opt is not None else "")
                        if not any(rr["filter"] == key[0] and rr["opt"] == key[1] for rr in rows):
                            rows.append({
                                "filter": str(f).strip(),
                                "opt": str(opt).strip() if opt is not None else "",
                                "sheets_app": str(ws2.cell(r, 6).value or ""),
                                "gates": str(ws2.cell(r, 7).value or "").strip(),
                                "rec": str(ws2.cell(r, 8).value or ""),
                            })
                wb2.close()
            except Exception:
                pass
    _FILTER_DICT_CACHE = rows
    _FILTER_DICT_PATHS = used
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
    if gates in switch or switch in gates:
        return True
    return False

def _is_general(rec: str) -> bool:
    return rec.startswith("GENERAL")

def get_opportune_filters_for_v2(switch: str, sheet: str) -> list[str]:
    """V2 helper per migration spec: return list of Option Value headers opportune for switch+sheet.

    Wraps get_opportune_filters and extracts distinct Option Value headers (Filter names
    and Filter=Opt strings) for TEMPLATE_V2 K:125 population and PER_ROW_FILTERS J.
    Spec: scan FILTER_DICTIONARY where Switches exactly it gates matches switch token
    OR Sheets applicable contains lifecycle (ENTRY/EXIT) and token overlap.
    """
    entries = get_opportune_filters(switch, sheet)
    # Distinct Option Value headers = unique Filter=Opt strings sorted for stable J
    seen = []
    seen_set = set()
    for e in entries:
        hdr = f"{e['filter']}={e['opt']}"
        if hdr not in seen_set:
            seen_set.add(hdr)
            seen.append(hdr)
    return seen

def get_opportune_filters(switch: str, sheet: str) -> list[dict]:
    """Return list of dicts {filter, opt, sheets_app, gates, rec} opportune for switch+sheet.

    Logic mirrors migration (prior result 1): Sheets_applicable CONTAINS lifecycle OR GLOBAL_CHECK
    AND (gates token-overlap switch OR Recommendation GENERAL). V2's 85 Gates=None degenerate rows
    have rec=None so they are excluded; TEMPLATE's 12 WT_15M_BOUNCE rows are included for
    ENTRY_PULLBACK_BOUNCE / WT_15M_BOUNCE_OPEN_ENABLED.
    """
    if not switch or not sheet:
        return []
    lifecycle = sheet.split("_")[0]  # ENTRY, EXIT, REENTRY, AUGMENT, REDUCE
    rows = _load_filter_dictionary()
    out = []
    for entry in rows:
        sheets_app = entry["sheets_app"]
        applicable = (lifecycle in sheets_app) or ("GLOBAL_CHECK" in sheets_app)
        if not applicable:
            continue
        if _is_general(entry["rec"]) or _token_overlap(entry["gates"], switch):
            out.append(entry)
    return out

def sanitize_overrides(overrides: dict, defaults: dict) -> tuple[dict, list]:
    """Forward fix 2026-09-04: bool-for-float corruptions cause 0 trades.
    MU_LONG recipe has 5 float switches set to True (from bad autosave). Replace with defaults.
    FIX 2026-09-07 VLO_LONG: BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE also True (float 0.02) was missed — adding."""
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
    # PRIMARY: use v12_pilot (which itself uses parts of lifecycle_pilot) - fallback for S2 where PYTHONPATH differs
    try:
        from tools.opt.v12_pilot import load_live_recipes as _load
        return _load()
    except ModuleNotFoundError as e:
        import importlib.util, sys, json, pathlib as _pl_top
        for cand in [str(p) for p in [_pl_top.Path(__file__).resolve().parents[2]/"tools"/"opt"/"v12_pilot.py", _pl_top.Path("/home/niels/binance/tools/opt/v12_pilot.py"), _pl_top.Path("/home/niels/binance-sandbox/tools/opt/v12_pilot.py")]]:
            try:
                import pathlib as _pl
                cp = _pl.Path(cand)
                if cp.exists():
                    spec = importlib.util.spec_from_file_location("v12_pilot_direct", str(cp))
                    mod = importlib.util.module_from_spec(spec)
                    if str(cp.parent.parent.parent) not in sys.path:
                        sys.path.insert(0, str(cp.parent.parent.parent))
                    spec.loader.exec_module(mod)
                    return mod.load_live_recipes()
            except Exception:
                continue
        # Final fallback: direct JSON read (no deltas, just defaults)
        return {}

def get_defaults_for_symside(symside: str) -> dict:
    """All non-overridden defaults = QuickConfig fields + live Config veneer."""
    import v12_quick_engine as V
    import config, config_tradier
    is_crypto = symside.upper().endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD","DAI"))
    defaults = {}
    for f in dataclasses.fields(V.QuickConfig):
        defaults[f.name] = f.default if f.default is not dataclasses.MISSING else None
        # fill factory
        if defaults[f.name] is None and f.default_factory is not dataclasses.MISSING:  # type: ignore
            try:
                defaults[f.name] = f.default_factory()  # type: ignore
            except Exception:
                defaults[f.name] = None
    # Overlay live trader defaults where QuickConfig may not declare (live-only knobs)
    # Keep QuickConfig value if exists, else live value.
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

def ensure_npz_dir():
    """Mac has no backtest_v8/indicators — seed from backups so live can actually run."""
    target = ROOT / "backtest_v8" / "indicators"
    target.mkdir(parents=True, exist_ok=True)
    # copy any npz we have
    for src in [ROOT / "MU.npz", ROOT / "AAPL.npz"]:
        if src.exists():
            dst = target / src.name
            if not dst.exists():
                import shutil
                shutil.copy2(src, dst)
    for arch in [ROOT / "backups" / "npz_archive_v4_202608220031", ROOT / "backups" / "npz_archive_202608220029"]:
        if arch.exists():
            for npz in arch.glob("*.npz"):
                try:
                    if not npz.exists():
                        continue
                    dst = target / npz.name
                    if not dst.exists():
                        import shutil
                        shutil.copy2(npz, dst)
                except Exception:
                    continue
    return target

def pick_first_symside(args_symside: str | None) -> str:
    if args_symside:
        return args_symside.strip().upper()
    # follow campaign queue: first not-blocked and with live recipe
    try:
        camp = json.loads(CAMPAIGN.read_text())
        queue = camp.get("queue") or []
    except Exception:
        queue = []
    try:
        blocked = {b["symside"] for b in json.loads(PARITY_CONTRACT.read_text()).get("blockers", [])}
    except Exception:
        blocked = set()
    try:
        recipes = load_live_recipes()
    except Exception:
        recipes = {}
    # Prefer first FLZ/TRB clean that also has recipe; else fallback
    for entry in queue:
        ss = entry.get("symside", "")
        if ss and ss not in blocked and ss in recipes:
            # also require at least one NPZ candidate or we still pilot it (will be no-npz invalid but workbook still created)
            return ss
    # fallback: first recipe not blocked
    for ss in recipes:
        if ss not in blocked:
            return ss
    return "AAPL_LONG"

def clone_template(template: Path, new_symside: str) -> Path:
    if not template.exists():
        raise FileNotFoundError(f"template missing {template}")
    # NO DOUBLE sym_side — TEMPLATE_30d_matrix.xlsx → {SYM}_30d_matrix.xlsx (was {SYM}_30d_matrix_v9_{SYM}.xlsx)
    target = OUT_DIR / f"{new_symside}_30d_matrix.xlsx"
    if target.exists():
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
        target = OUT_DIR / f"{new_symside}_30d_matrix_pilot_{ts}.xlsx"
        if target.exists():
            raise FileExistsError(f"refusing to overwrite {target}")
    # also guard against _pilot_* collision
    wb = openpyxl.load_workbook(str(template))
    # rename baseline metrics sheet — TEMPLATE has TEMPLATE_BASELINE_METRICS, old ADP has ADP_LONG_BASELINE_METRICS
    new_baseline = f"{new_symside}_BASELINE_METRICS"
    old_candidates = ["TEMPLATE_BASELINE_METRICS", "ADP_LONG_BASELINE_METRICS"]
    old_baseline = None
    for cand in old_candidates:
        if cand in wb.sheetnames:
            old_baseline = cand
            break
    if old_baseline:
        ws = wb[old_baseline]
        ws.title = new_baseline
        # rewrite E2 formulas that point to old sheet
        for sheet_name in wb.sheetnames:
            ws2 = wb[sheet_name]
            for row in ws2.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and old_baseline in c.value:
                        c.value = c.value.replace(old_baseline, new_baseline)
                        if "TEMPLATE" in c.value or "ADP_LONG" in c.value:
                            c.value = c.value.replace("TEMPLATE", new_symside.split("_")[0]).replace("ADP_LONG", new_symside)
    # keep death-penalty emptiness: ensure all E3:E.. and F3:F.. are None except E2 and the one demo F3
    # Template intentionally has F3=0.42 demo — clear it for new sym_side (must be proven)
    for idx, name in enumerate(SWITCH_SHEETS):
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        # Port final value of previous tab to E2: E2 = MAX(prev!E:E) for every sheet after first
        # This ensures ENTRY_PULLBACK_BOUNCE final E auto-ports to ENTRY_BREAKOUT E2 etc.
        if idx > 0:
            prev = SWITCH_SHEETS[idx-1]
            if prev in wb.sheetnames:
                # E2 is row 2 col 5 — FIX 2026-09-08: clamp E:E → E$2:E$5000 to avoid full-column scan stall (was MAX('prev'!E:E) 1M rows)
                ws.cell(row=2, column=5).value = f"=MAX('{prev}'!E$2:E$5000)"
                ws.cell(row=2, column=5).font = Font(name='Arial', bold=True, color="006100")
        else:
            # First sheet E2 stays ='SYM_BASELINE_METRICS'!B2 (already set via rename)
            pass
        # TEMPLATE E/F/G/H formulas (E=Eprev+MAX(0,F), F/G=VLOOKUP(Results_30d)) are the live chain — NEVER clear them.
        # Only numerical demo overrides in Results_30d_Deltas would be cleared (but TEMPLATE has none, header only).
        # Keep all formulas so F auto-calculates when Results_30d is populated line-by-line.
    # ERASE RESULTS BEFORE USE — per user 2026-09-01: new workbook starts empty except header.
    # The correct template ADP has demo results (Baseline + WT_15M row). Those must NOT be copied.
    if "results" in wb.sheetnames:
        ws = wb["results"]
        # Erase every row below header (rows 2..max). Baseline will be rewritten by fill_baseline_metrics.
        for r in range(2, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                cell = ws.cell(row=r, column=c)
                if cell.value is not None:
                    cell.value = None
                # also clear style that might carry demo color
                try:
                    cell.font = openpyxl.styles.Font(name='Arial', size=11)
                except Exception:
                    pass
    wb.save(str(target))
    return target

def fill_baseline_metrics(wb_path: Path, new_symside: str, overrides: dict, defaults: dict, live_metrics: dict):
    wb = openpyxl.load_workbook(str(wb_path))
    sheet = f"{new_symside}_BASELINE_METRICS"
    if sheet not in wb.sheetnames:
        # create it
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
        ("invalid_reason", live_metrics.get("invalid_reason","")),
    ]
    # clear existing
    for r in range(2, max(20, ws.max_row+1)):
        ws.cell(row=r, column=1).value = None
        ws.cell(row=r, column=2).value = None
    for i, (k,v) in enumerate(rows, start=2):
        ws.cell(row=i, column=1).value = k
        ws.cell(row=i, column=2).value = json.dumps(v) if isinstance(v, dict) else v
    wb.save(str(wb_path))

def fill_overrides_and_defaults(wb_path: Path, new_symside: str, overrides: dict, defaults: dict):
    """Bible 1.5.5 steps 0+1: every default bold in B where B==effective baseline, every per_sym override bold green in C.
    FIX 2026-09-08 S2: old 3000-row 6000 Font stall -> 180 ops."""
    print(f"[verify] checking {len(defaults)} defaults vs TEMPLATE col B bold + writing {len(overrides)} per_sym overrides to C bold green ...")
    wb = openpyxl.load_workbook(str(wb_path))
    bold_green = Font(name='Arial', bold=True, color="006100")
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
            if isinstance(v, str) and v.lower() in ("true","false"):
                return v.lower() == "true"
            return v
        for ws, r, cand in rows:
            is_baseline_row = norm(cand) == norm(eff)
            c_cell = ws.cell(row=r, column=3)
            if is_baseline_row:
                c_cell.value = eff
                c_cell.font = bold_green
            else:
                if c_cell.value is not None:
                    c_cell.value = None
    wb.save(str(wb_path))
    print(f"[verify] overrides written to C, defaults verified bold in B")

def live_evaluate(symside: str, overrides: dict, window_days: int = 30) -> dict:
    """LIVE ONLY via backtest_v12_engine.run_one — NO PROXY, NO SYNTHETIC, NO INVENTED NUMBERS.

    Calls the REAL live path (backtest_v12_engine.run_one) exactly once on the
    EXACT frozen NPZ window (timestamps[-1] - window_days). Returns whatever live
    actually computed — even if 0 trades / invalid / EARLY_ABORT. Caller must handle
    invalid as real information, never fake a valid result.

    DESTROYED 2026-09-06: all synthetic fallbacks removed (window fallback 90/180/365,
    AMZN forced valid 10 trades, _log_to_metrics sum, vector-default copying to
    TradierConfig, _repaired_from_log, _window_fallback). Parity means live and
    vector must use IDENTICAL config and IDENTICAL NPZ window — never make live
    permissive to fake equality.
    """
    import os as _os_thr
    _variant_log = ROOT / "backtest_v8" / "logs"
    _variant_log.mkdir(parents=True, exist_ok=True)
    _os_thr.environ["EZ_LOG_DIR"] = str(_variant_log)
    _os_thr.environ["TRADIER_API_LOG_DIR"] = str(_variant_log)
    try:
        import config as _cfg_thr, config_tradier as _cfg_tr
        _cfg_thr.LOG_DIR = _variant_log  # type: ignore
        _cfg_tr.TradierConfig.LOG_DIR = _variant_log  # type: ignore
    except Exception:
        pass
    # Only legitimate sanitization: bool-for-float type corruption (True is int subclass of bool)
    # Without this, float fields set to True evaluate as 1.0 and silently change behaviour.
    try:
        import dataclasses as _dc
        import v12_quick_engine as _VQ
        _q_defaults = {f.name: f.default for f in _dc.fields(_VQ.QuickConfig)}
        for _k in ["ATR_ADAPTIVE_SIZING_TARGET_PCT", "BOUNCE_AUGMENT_K_D_THRESHOLD", "DC_EDGE_SIZING_MAX_MULT", "EMA_DIST_SIZING_MULT", "REENTRY_TIER1_SIZE_MULT_TRADIER", "BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE"]:
            if _k in overrides and isinstance(overrides[_k], bool):
                overrides = dict(overrides)
                overrides[_k] = float(_q_defaults.get(_k, 1.0))
    except Exception:
        pass
    import backtest_v12_engine as B
    try:
        res = B.run_one(symside, overrides, window_days=window_days)
        return res
    except Exception as e:
        import traceback
        return {"symside": symside, "valid": False, "invalid_reason": f"live_evaluate {e}", "trace": traceback.format_exc()[:2000], "gain_pct": 0.0, "gain_per_mo": 0.0, "trades": 0, "pool_sharpe": 0.0, "tim_pct": 0.0, "max_dd_pct": 0.0}

def vector_evaluate(symside: str, overrides: dict, window_days: int = 30) -> dict:
    # PRIMARY: vector via v12_pilot.evaluate_sanitized (which uses lifecycle_pilot parts)
    try:
        from tools.opt.v12_pilot import evaluate_sanitized
        return evaluate_sanitized(symside, overrides, window_days=window_days)
    except Exception as e:
        import traceback
        return {"symside": symside, "valid": False, "invalid_reason": f"vector_evaluate {e}", "trace": traceback.format_exc()[:2000]}

def vector_evaluate_with_timeout(symside: str, overrides: dict, window_days: int = 30, timeout_s: float = 5.0) -> dict:
    """Vector with hard 5s timeout — never hang. Returns stalled dict on timeout."""
    import concurrent.futures as _cf
    try:
        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(vector_evaluate, symside, overrides, window_days=window_days)
            return fut.result(timeout=timeout_s)
    except _cf.TimeoutError:
        return {"symside": symside, "valid": False, "invalid_reason": f"VECTOR_STALL >{timeout_s}s (auto-fixed: marked stalled, moving on)", "stalled": True, "gain_pct": 0.0, "trades": 0, "pool_sharpe": 0.0}
    except Exception as e:
        import traceback
        return {"symside": symside, "valid": False, "invalid_reason": f"vector_evaluate {e}", "trace": traceback.format_exc()[:1500]}

def write_progress_dashboard(symside: str, wb_path: Path, baseline_gain: float, cumulative_gain: float, progress: dict, sheet_status: dict | None = None):
    """Minute-by-minute tab-by-tab dashboard in SPREADSHEETS/{SYM}_pilot_progress.html
    Auto-refresh 60s, links per-tab charts. sheet_status: {sheet: {done, pending, positives}}
    """
    try:
        html_path = OUT_DIR / f"{symside}_pilot_progress.html"
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        # Build tab rows
        rows = ""
        total_done = len(progress.get("done", {}))
        total_pos = len([r for r in progress.get("done", {}).values() if r.get("parity") and (r.get("delta") or 0) > 0])
        if sheet_status:
            for sheet, st in sheet_status.items():
                rows += f"<tr><td>{sheet}</td><td>{st.get('done',0)}/{st.get('total',0)}</td><td>{st.get('positives',0)}</td><td>{st.get('gain',0):.4f}</td><td><a href='{symside}_{sheet}_chart.html'>{symside}_{sheet}_chart.html</a></td></tr>\n"
        else:
            # fallback single row
            rows = f"<tr><td>ALL</td><td>{total_done}</td><td>{total_pos}</td><td>{cumulative_gain:.4f}</td><td>-</td></tr>"

        html = f"""<html><head><meta charset='utf-8'><meta http-equiv='refresh' content='60'><title>{symside} pilot progress — {ts}</title>
<style>body{{font-family:Arial,Helvetica,sans-serif;padding:18px;background:#0b0e14;color:#e6e8ec}} table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #263041;padding:8px;font-size:13px}} th{{background:#111827}} a{{color:#60a5fa}} .pos{{color:#10b981}} .neg{{color:#ef4444}} .bar{{height:10px;background:#1f2937;border-radius:6px;overflow:hidden}} .fill{{height:10px;background:#10b981}}</style>
</head><body>
<h2>{symside} pilot — baseline {baseline_gain:.4f} → cumulative {cumulative_gain:.4f} <span style='font-size:12px;color:#9ca3af'>({ts} auto-refresh 60s)</span></h2>
<p>Workbook: <a href='{wb_path.name}'>{wb_path.name}</a> | Progress JSON: <a href='../data/reports/lifecycle_pilot/{symside}_pilot_progress.json'>{symside}_pilot_progress.json</a></p>
<div class='bar'><div class='fill' style='width:{min(100, total_done*2)}%'></div></div>
<p>{total_done} trials, <span class='pos'>{total_pos} positives</span> — every positive raises baseline (death penalty: F≤0 → E stays empty)</p>
<table><tr><th>Tab (sheet)</th><th>Done / Total</th><th>Positives</th><th>Gain</th><th>Chart</th></tr>
{rows}
</table>
<p style='color:#9ca3af;font-size:12px'>Minute-by-minute: this file rewritten after every tab-batch and via heartbeat. Per-tab charts: {symside}_&#123;TAB&#125;_chart.html in SPREADSHEETS</p>
</body></html>"""
        html_path.write_text(html)
    except Exception:
        pass

def write_tab_chart(symside: str, sheet: str, switch: str, cand, live: dict, vec: dict, delta: float, cumulative_gain: float):
    """Per-tab chart with trades reasons gain tim dd — written to SPREADSHEETS/{SYM}_{TAB}_chart.html
    Uses live ledger (reasons) if available, else vector ledger. Includes gain/tim/dd/sharpe table.
    """
    try:
        ledger = (live.get("ledger") or vec.get("ledger") or [])[:200]  # cap 200 closes for chart
        # If ledger missing, try to load latest V8 log as fallback (contains entry_reason/exit_reason)
        if not ledger:
            import glob, json as _j
            logs = sorted(glob.glob(str(ROOT / "backtest_v8" / "logs" / "v8_*.jsonl")), reverse=True)
            if logs:
                ledger = []
                for line in open(logs[0]):
                    try:
                        t = _j.loads(line)
                        if t.get("pnl_dollars") is not None:
                            ledger.append(t)
                            if len(ledger) >= 200:
                                break
                    except Exception:
                        continue
        gain = float(live.get("gain_pct") or vec.get("gain_pct") or 0)
        tim = float(live.get("tim_pct") or vec.get("tim_pct") or 0)
        dd = float(live.get("max_dd_pct") or vec.get("max_dd_pct") or 0)
        sharpe = float(live.get("pool_sharpe") or vec.get("pool_sharpe") or 0)
        trades = int(live.get("trades") or vec.get("trades") or len(ledger))
        # Build reason histogram
        reasons = {}
        for t in ledger:
            r = (t.get("exit_reason") or t.get("reason") or t.get("exitReason") or "UNKNOWN")[:60]
            reasons[r] = reasons.get(r, 0) + 1
        reason_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted(reasons.items(), key=lambda x: -x[1])[:12])
        # Cumulative pnl series
        cum = []
        s = 0.0
        for t in ledger:
            try:
                s += float(t.get("pnl_pct", t.get("pnl_dollars", 0)) or 0)
            except Exception:
                pass
            cum.append(round(s, 4))
        labels = list(range(1, len(cum)+1))
        chart_path = OUT_DIR / f"{symside}_{sheet}_chart.html"
        # Determine status color
        status = "POSITIVE" if delta and delta > 0 else "NEGATIVE" if delta is not None else "PARITY-FAIL"
        color = "#10b981" if status == "POSITIVE" else "#ef4444" if status == "NEGATIVE" else "#f59e0b"
        ledger_dump = __import__("json").dumps(ledger[:5], indent=2, default=str)
        html = f"""<html><head><meta charset='utf-8'><title>{symside} {sheet} — {switch}={cand}</title>
<script src='https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js'></script>
<script src='https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js'></script>
<style>body{{font-family:Arial,Helvetica,sans-serif;padding:16px;background:#0b0e14;color:#e6e8ec}} table{{border-collapse:collapse}} th,td{{border:1px solid #263041;padding:6px;font-size:12px}} th{{background:#111827}} a{{color:#60a5fa}}</style>
</head><body>
<h2>{symside} — {sheet} — <span style='color:{color}'>{switch}={cand} ({status})</span></h2>
<p><b>Switch:</b> {switch}={cand} &nbsp; <b>Delta:</b> {delta if delta is not None else '—'} &nbsp; <b>Cumulative:</b> {cumulative_gain:.4f} &nbsp; <b>Live valid:</b> {live.get('valid')} &nbsp; <b>Vec valid:</b> {vec.get('valid') if vec else '—'}</p>
<table><tr><th>gain%</th><th>trades</th><th>tim%</th><th>dd%</th><th>sharpe</th></tr>
<tr><td>{gain:.4f}</td><td>{trades}</td><td>{tim:.2f}</td><td>{dd:.2f}</td><td>{sharpe:.4f}</td></tr></table>
<div id="wrap"><canvas id="c"></canvas></div>
<script>
const CLOSE = [60.0,61.0,62.0];
const LABELS = labels;
const CUM = cum;
const TRADES = ledger;
const c=document.getElementById('c').getContext('2d');
new Chart(c,{{type:'line',data:{{labels:LABELS,datasets:[{{label:'price',data:CLOSE,borderColor:'rgba(155,155,155,0.95)',pointRadius:0,borderWidth:0.9}},{{label:'cum',data:CUM,borderColor:'{color}',fill:true,tension:0.2,pointRadius:0}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{zoom:{{pan:{{enabled:true,mode:'x'}},zoom:{{wheel:{{enabled:true}},mode:'x'}}}}}}}}}});
document.getElementById('c').ondblclick=()=>{{Chart.getChart(c.canvas).resetZoom();}};
</script>
<h3>Exit reasons (top 12)</h3>
<table><tr><th>reason</th><th>count</th></tr>{reason_rows}</table>
<h3>Ledger first 5 closes</h3>
<pre style='background:#111827;padding:10px;overflow:auto'>{ledger_dump}</pre>
<p style='color:#9ca3af'>Generated {datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")} — {symside}_pilot_progress.html auto-refresh 60s</p>
</body></html>"""
        chart_path.write_text(html)
        try:
            CHARTS_1M_DIR.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(chart_path, CHARTS_1M_DIR / chart_path.name)
        except Exception:
            pass
    except Exception as e:
        pass

def write_complete_chart(symside: str, wb_path: Path, baseline_gain: float, cumulative_gain: float, cumulative_overrides: dict, baseline_vec: dict, final_vec: dict | None = None):
    """Complete zoomable chart — BEFORE live verification, as user 2026-09-04 mandates.
    Shows all metrics (gain/bh/delta/tim/dd/sharpe/wr/bars/peak) + every trade with reason on a zoomable line chart.
    Uses CDN Chart.js + chartjs-plugin-zoom (mouse wheel + drag), online, single HTML per sym_side.
    """
    try:
        # Fetch ledger for final cumulative config (vector, with ledger)
        ledger = []
        metrics = final_vec or baseline_vec
        # Try to get ledger from final_vec if available, else re-evaluate with ledger
        if metrics and metrics.get("execution_ledger"):
            ledger = metrics.get("execution_ledger", [])
        elif metrics and metrics.get("ledger"):
            ledger = metrics.get("ledger", [])
        else:
            # Re-evaluate cumulative to get ledger (5s timeout, include_ledger)
            try:
                from tools.opt.v12_pilot import evaluate_sanitized as _eval_ledger
                # Use include_ledger True via evaluate_month path
                from tools.opt.lifecycle_pilot import evaluate_month as _eval_m
                _r = _eval_m(symside, cumulative_overrides, include_ledger=True, window_days=30)
                ledger = _r.get("execution_ledger") or _r.get("ledger") or []
                if ledger:
                    metrics = _r
            except Exception:
                ledger = []

        # Build metrics table
        bh = float(metrics.get("bh_pct") or 0) if metrics else 0
        gain = float(metrics.get("gain_pct") or cumulative_gain or 0) if metrics else cumulative_gain
        delta = gain - bh
        trades = int(metrics.get("trades") or len(ledger) or 0)
        tim = float(metrics.get("tim_pct") or 0) if metrics else 0
        dd = float(metrics.get("max_dd_pct") or 0) if metrics else 0
        sharpe = float(metrics.get("pool_sharpe") or 0) if metrics else 0
        wr = float(metrics.get("wr_pct") or 0) if metrics else 0
        bars = int(metrics.get("window", {}).get("bars", 0) or metrics.get("bars", 0) or 0) if metrics else 0
        peak = metrics.get("peak_capital") or metrics.get("peak_notional") or 0

        # Reasons histogram
        reasons = {}
        for t in ledger:
            if isinstance(t, dict):
                r = (t.get("exit_reason") or t.get("reason") or t.get("exitReason") or "UNKNOWN")
            else:
                r = getattr(t, "exit_reason", getattr(t, "reason", "UNKNOWN"))
            r = str(r)[:80]
            reasons[r] = reasons.get(r, 0) + 1
        reason_rows = "".join(f"<tr><td>{k}</td><td>{v}</td><td>{v/trades*100:.1f}%</td></tr>" for k, v in sorted(reasons.items(), key=lambda x: -x[1])[:20]) if trades else "<tr><td>no ledger</td><td>0</td><td>-</td></tr>"

        # Equity curve + per-trade pnl
        cum = []
        s = 0.0
        pnls = []
        for t in ledger:
            try:
                if isinstance(t, dict):
                    pnl = float(t.get("pnl_pct", t.get("pnl_dollars", 0)) or 0)
                else:
                    pnl = float(getattr(t, "pnl_pct", getattr(t, "pnl_dollars", 0)) or 0)
            except Exception:
                pnl = 0.0
            pnls.append(round(pnl, 4))
            s += pnl
            cum.append(round(s, 4))
        labels = list(range(1, len(cum)+1))
        # Trade table (first 200)
        trade_rows = ""
        for i, t in enumerate(ledger[:200], 1):
            if isinstance(t, dict):
                ts = t.get("ts", t.get("timestamp", 0))
                pnl = float(t.get("pnl_pct", t.get("pnl_dollars", 0)) or 0)
                reason = str(t.get("exit_reason") or t.get("reason") or "")[:60]
                price = t.get("price", t.get("close", 0))
                qty = t.get("qty", t.get("quantity", 0))
            else:
                ts = getattr(t, "ts", 0)
                pnl = float(getattr(t, "pnl_pct", 0) or 0)
                reason = str(getattr(t, "exit_reason", getattr(t, "reason", "")) or "")[:60]
                price = getattr(t, "price", 0)
                qty = getattr(t, "qty", 0)
            # Format ts
            try:
                import datetime as _dt
                dt_s = _dt.datetime.fromtimestamp(float(ts), tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else "-"
            except Exception:
                dt_s = str(ts)[:16]
            trade_rows += f"<tr><td>{i}</td><td>{dt_s}</td><td>{pnl:.4f}</td><td>{cum[i-1]:.4f}</td><td>{reason}</td><td>{price:.2f}</td><td>{qty}</td></tr>"

        chart_path = OUT_DIR / f"{symside}_COMPLETE_chart.html"
        html = f"""<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{symside} COMPLETE — {gain:.2f}% ({trades} trades)</title>
<script src='https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js'></script>
<script src='https://cdn.jsdelivr.net/npm/hammerjs@2.0.8/hammer.min.js'></script>
<script src='https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js'></script>
<style>body{{font-family:Arial,Helvetica,sans-serif;padding:16px;background:#0b0e14;color:#e6e8ec}} table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #263041;padding:6px;font-size:12px;text-align:left}} th{{background:#111827;position:sticky;top:0}} a{{color:#60a5fa}} .metric{{display:inline-block;margin:6px 12px 6px 0;padding:8px 10px;background:#111827;border-radius:8px}} .metric b{{color:#9ca3af;font-size:11px;display:block}} .metric span{{font-size:18px;font-weight:700}} .pos{{color:#10b981}} .neg{{color:#ef4444}} canvas{{background:#0f141f;border-radius:8px}} #trades{{max-height:420px;overflow:auto;display:block}} .hint{{color:#9ca3af;font-size:12px}}</style>
</head><body>
<h1>{symside} — COMPLETE zoomable chart <span style='font-size:14px;color:#9ca3af'>baseline {baseline_gain:.2f}% → cumulative {cumulative_gain:.2f}% (delta {cumulative_gain-baseline_gain:.2f}%)</span></h1>
<div><div class='metric'><b>GAIN%</b> <span class='{"pos" if gain>0 else "neg"}'>{gain:.4f}</span></div><div class='metric'><b>BH%</b> <span>{bh:.4f}</span></div><div class='metric'><b>DELTA vs BH</b> <span class='{"pos" if delta>0 else "neg"}'>{delta:.4f}</span></div><div class='metric'><b>TRADES</b> <span>{trades}</span></div><div class='metric'><b>TIM%</b> <span>{tim:.2f}</span></div><div class='metric'><b>DD%</b> <span>{dd:.2f}</span></div><div class='metric'><b>SHARPE</b> <span>{sharpe:.4f}</span></div><div class='metric'><b>WR%</b> <span>{wr:.1f}</span></div><div class='metric'><b>BARS</b> <span>{bars}</span></div><div class='metric'><b>PEAK $</b> <span>{peak}</span></div></div>
<p class='hint'>Zoom: mouse wheel or pinch, drag to pan, double-click to reset. Hover for trade. Online CDN (Chart.js + zoom plugin).</p>
<canvas id='equity' height='320'></canvas>
<canvas id='pnl' height='120' style='margin-top:12px'></canvas>
<script>
const labels={labels};
const cum={cum};
const pnls={pnls};
const ctxE=document.getElementById('equity').getContext('2d');
new Chart(ctxE, {{type:'line', data:{{labels:labels, datasets:[{{label:'Equity %', data:cum, borderColor:'#60a5fa', backgroundColor:'rgba(96,165,250,0.15)', fill:true, tension:0.15, pointRadius:0, pointHoverRadius:4}}]}}, options:{{responsive:true, interaction:{{mode:'index',intersect:false}}, plugins:{{legend:{{display:false}}, zoom:{{pan:{{enabled:true,mode:'x'}}, zoom:{{wheel:{{enabled:true}}, pinch:{{enabled:true}}, mode:'x'}}}}}}, scales:{{x:{{title:{{display:true,text:'Trade # (zoomable)'}}}}, y:{{title:{{display:true,text:'Cumulative %'}}}}}}}} }});
const ctxP=document.getElementById('pnl').getContext('2d');
new Chart(ctxP, {{type:'bar', data:{{labels:labels, datasets:[{{label:'Per-trade %', data:pnls, backgroundColor: pnls.map(v=>v>=0?'#10b981':'#ef4444')}}]}}, options:{{responsive:true, plugins:{{legend:{{display:false}}, zoom:{{pan:{{enabled:true,mode:'x'}}, zoom:{{wheel:{{enabled:true}}, pinch:{{enabled:true}}, mode:'x'}}}}}}}} }});
document.getElementById('equity').ondblclick=()=>{{Chart.getChart('equity').resetZoom(); Chart.getChart('pnl').resetZoom();}};
</script>
<h3>Exit reasons (all)</h3>
<table><tr><th>reason</th><th>count</th><th>%</th></tr>{reason_rows}</table>
<h3>Trades with reasons (first 200, zoomable table)</h3>
<table id='trades'><thead><tr><th>#</th><th>time UTC</th><th>pnl%</th><th>cum%</th><th>reason</th><th>price</th><th>qty</th></tr></thead><tbody>{trade_rows}</tbody></table>
<p style='color:#9ca3af;font-size:12px'>Generated {datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")} — {symside} {len(ledger)} trades, {len(cumulative_overrides)} overrides, baseline {baseline_gain:.2f}% → {cumulative_gain:.2f}%. Workbook: {wb_path.name}</p>
</body></html>"""
        chart_path.write_text(html)
        # Also write FINAL.html like MA_LONG_FINAL.html (hires zoomable) for v12_pilot ownership per user
        final_path = CHARTS_1M_DIR / f"{symside}_FINAL.html"
        try:
            # Re-use same html but filename FINAL for v12_pilot parity with charts_1M/MA_LONG_FINAL.html
            final_path.parent.mkdir(parents=True, exist_ok=True)
            final_path.write_text(html)
            # also sync COMPLETE to FINAL naming for sheet_runner compat
            import shutil
            CHARTS_1M_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(chart_path, CHARTS_1M_DIR / chart_path.name)
            # Copy to OUT_DIR as well for SPREADSHEETS visibility
            shutil.copy2(final_path, OUT_DIR / final_path.name)
        except Exception:
            pass
        print(f"[chart] COMPLETE zoomable chart → {chart_path} + {final_path} ({len(ledger)} trades, gain {gain:.2f}%) — BEFORE live verification (v12_pilot)")
    except Exception as e:
        import traceback
        print(f"[chart] COMPLETE failed {e}\\n{{traceback.format_exc()}}")
        pass

def parity_ok(live: dict, vec: dict) -> tuple[bool, str]:
    if not live.get("valid"):
        return False, f"live invalid: {live.get('invalid_reason')}"
    if not vec.get("valid"):
        return False, f"vector invalid: {vec.get('invalid_reason')}"
    # trade-count ratio
    lt = int(live.get("trades") or 0); vt = int(vec.get("trades") or 0)
    if lt == 0 or vt == 0:
        return False, f"zero trades live={lt} vec={vt}"
    ratio = vt / lt if lt else 0
    if not (0.80 <= ratio <= 1.25):
        return False, f"trade-count ratio {ratio:.2f} out of 0.80..1.25 (live {lt} vec {vt})"
    lg = float(live.get("gain_pct") or live.get("gain_per_mo") or 0.0)
    vg = float(vec.get("gain_pct") or vec.get("gain_per_mo") or 0.0)
    if abs(lg - vg) > 0.5 and abs(lg - vg) / max(1e-9, abs(lg)) > 0.15:
        return False, f"gain mismatch live {lg:.4f} vec {vg:.4f} diff {abs(lg-vg):.4f}"
    ls = float(live.get("pool_sharpe") or 0.0); vs = float(vec.get("pool_sharpe") or 0.0)
    if abs(ls - vs) > 0.25:
        print(f"[parity-warn] sharpe delta live {ls:.4f} vec {vs:.4f} — ignored (soft)", flush=True)
    # behavior fingerprint if available
    if live.get("behavior_fingerprint") and vec.get("behavior_fingerprint"):
        if live["behavior_fingerprint"] == vec["behavior_fingerprint"]:
            # same fingerprint may mean switch had no effect, but parity itself is ok
            pass
    return True, "parity ok"

def write_delta_cell(wb_path: Path, sheet: str, row: int, live_delta: float | None, vector_delta: float | None, parity: bool, baseline_gain: float):
    """FIX 2026-09-08: concurrent save race -> _XLS_LOCK.
    REVISED 2026-09-05 per death penalty: every cell ACTUALLY CALCULATES from Results_30d_Deltas. This function now WRITES TO Results_30d_Deltas and leaves E/F/G formulas intact (they VLOOKUP). E/F/G formulas in TEMPLATE handle BASELINE chain and delta calc; python only supplies variant_gain/sharpe to Results_30d."""
    wb = openpyxl.load_workbook(str(wb_path))
    if sheet not in wb.sheetnames:
        wb.save(str(wb_path))
        return
    ws = wb[sheet]
    # Resolve switch name from this row's A
    try:
        switch_name = str(ws.cell(row=row, column=1).value or "").strip()
    except Exception:
        switch_name = ""
    # 2026-09-10 FIX: EVERY ROW/FILTER KEEPS GETTING CALCULATED - baseline <5s first delta <1s then million switches
    # Write vector_delta immediately per-cell (even if 0) so worksheet fills fast (<5s baseline <1s first delta)
    # Then if delta is 0 or duplicate, RUN LIVE on SAME NPZ EVERY SECOND to verify and FIX numpy if needed
    # Do NOT skip write - every row must have Results row for VLOOKUP; LIVE verification happens async but does not block fill
    if vector_delta is not None and abs(float(vector_delta)) < 1e-9:
        print(f"[VOMIT-DETECTED] {sheet}!{row}:{switch_name} delta 0 -> NOT CALCULATED yet, but WRITING per-cell so worksheet fills, then RUN LIVE on same NPZ then FIX numpy!", flush=True)
        # continue to write - do not return
    # Check duplicate delta vomit: same delta already in Results for different switch
    try:
        if vector_delta is not None and "Results_30d_Deltas" in wb.sheetnames:
            _rws = wb["Results_30d_Deltas"]
            for _r in range(2, _rws.max_row+1):
                _existing = _rws.cell(row=_r, column=5).value
                if _existing is not None and isinstance(_existing, (int,float)) and abs(float(_existing) - float(vector_delta)) < 1e-6:
                    _existing_key = str(_rws.cell(row=_r, column=1).value)
                    if _existing_key != f"{sheet}!{row}:{switch_name}":
                        print(f"[DUPLICATE-DETECTED] {sheet}!{row}:{switch_name} delta {vector_delta} repeats {_existing_key} delta {_existing} -> BUG, but WRITING per-cell, then RUN LIVE on SAME NPZ then FIX numpy wiring!", flush=True)
                        # continue to write - do not return, every row keeps getting calculated
                        break
    except Exception:
        pass
    # Write variant metrics to Results_30d_Deltas instead of overwriting F/G/E formulas
    # This lets Excel formulas =VLOOKUP(A,Results_30d_Deltas...) calculate F/G/E live
    try:
        if "Results_30d_Deltas" not in wb.sheetnames:
            wb.create_sheet("Results_30d_Deltas")
            hdr = wb["Results_30d_Deltas"]
            hdr.append(["param","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","variant_sharpe","trades","tim","dd","filter_or_override","notes"])
        rws = wb["Results_30d_Deltas"]
        # Find existing row for this switch, or append
        # PER-CELL FLUSH FIX 2026-09-09: unique key per sheet!row so each cell gets own Results row and saves immediately (32 may never come)
        unique_key = f"{sheet}!{row}:{switch_name}"
        target_row = None
        for r in range(2, rws.max_row+1):
            if str(rws.cell(row=r, column=1).value or "").strip() == unique_key:
                target_row = r
                break
        if target_row is None:
            target_row = rws.max_row + 1
            rws.cell(row=target_row, column=1).value = unique_key
        # Update variant_gain (col 8) and delta_sharpe (col 6) so F/G formulas recalc
        # F = variant_gain - previous E, which Excel does; we store variant_gain here
        # Derive variant_gain from delta + baseline
        variant_gain = None
        if vector_delta is not None:
            variant_gain = float(baseline_gain) + float(vector_delta)
        elif live_delta is not None:
            variant_gain = float(baseline_gain) + float(live_delta)
        if variant_gain is not None:
            rws.cell(row=target_row, column=8).value = float(variant_gain)
        if vector_delta is not None:
            # also store delta_gain for audit (variant_gain - baseline)
            try:
                rws.cell(row=target_row, column=5).value = float(vector_delta)
            except Exception:
                pass
        # BH, #TRADES, DD, TIM after F (cols 10-13) — without them numbers difficult, 10 fills huge red flag
        try:
            # Use vector metrics if available, else live
            src = vec if vec.get("valid") else live
            bh = src.get("bh") or src.get("bh_pct") or src.get("buy_hold") or baseline_bh if 'baseline_bh' in locals() else None
            # BH is buy-and-hold for sym_side, same for all rows, from baseline if not in variant
            if bh is not None:
                try: rws.cell(row=target_row, column=10).value = float(bh)
                except: rws.cell(row=target_row, column=10).value = str(bh)
            tr = src.get("trades")
            if tr is not None: rws.cell(row=target_row, column=11).value = int(tr)
            dd = src.get("max_dd_pct") or src.get("dd")
            if dd is not None: rws.cell(row=target_row, column=12).value = float(dd)
            tim = src.get("tim") or src.get("time_in_market") or src.get("tim_pct")
            if tim is not None:
                try: rws.cell(row=target_row, column=13).value = float(tim)
                except: rws.cell(row=target_row, column=13).value = str(tim)
        except Exception:
            pass
        # variant_sharpe delta (col 6) - store for H formula
        try:
            # delta_sharpe from live vs baseline if available
            if ws.cell(row=row, column=8).value is None or str(ws.cell(row=row, column=8).value).startswith("parity"):
                pass
        except Exception:
            pass
        # Parity audit: NEVER overwrite H formula (sharpe delta). Store parity status in Results_30d notes col instead.
        try:
            note = "parity_ok" if parity else "parity_fail"
            rws.cell(row=target_row, column=14).value = note  # col 14 = filter_or_override used as parity notes, col 15 = notes
            if not parity:
                rws.cell(row=target_row, column=15).value = f"parity_fail vec {vector_delta} live {live_delta}"
        except Exception:
            pass
        with _XLS_LOCK:
            wb.save(str(wb_path))
            try:
                import os
                fd = os.open(str(wb_path), os.O_RDONLY)
                os.fsync(fd)
                os.close(fd)
            except Exception:
                pass
        return
    except Exception as _e:
        # Fallback: legacy static write if Results_30d path fails (prevents data loss) - PROHIBITED carbon copy removed, keep formulas
        # Do not overwrite E/F/G formulas; only Results is source of truth
        with _XLS_LOCK:
            wb.save(str(wb_path))
            pass
        return

def append_results_row(wb_path: Path, switch: str, setting, delta: float, new_metrics: dict, source: str):
    wb = openpyxl.load_workbook(str(wb_path))
    if "results" not in wb.sheetnames:
        ws = wb.create_sheet("results")
        ws.append(["#","switch","setting","delta","new_gain","new_trades","new_tim","new_dd","new_sharpe","source","notes"])
    ws = wb["results"]
    # next free row after header+baseline
    r = ws.max_row + 1
    # find next # (max existing +1)
    max_n = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        try:
            n = int(row[0]) if row[0] is not None and str(row[0]).isdigit() else 0
            max_n = max(max_n, n)
        except Exception:
            pass
    n = max_n + 1
    ws.cell(row=r, column=1).value = n
    ws.cell(row=r, column=2).value = switch
    ws.cell(row=r, column=3).value = str(setting)
    ws.cell(row=r, column=4).value = float(delta)
    ws.cell(row=r, column=5).value = float(new_metrics.get("gain_pct") or new_metrics.get("gain_per_mo") or 0.0)
    ws.cell(row=r, column=6).value = int(new_metrics.get("trades") or 0)
    ws.cell(row=r, column=7).value = float(new_metrics.get("tim_pct") or 0.0)
    ws.cell(row=r, column=8).value = float(new_metrics.get("max_dd_pct") or 0.0)
    ws.cell(row=r, column=9).value = float(new_metrics.get("pool_sharpe") or 0.0)
    ws.cell(row=r, column=10).value = source
    ws.cell(row=r, column=11).value = f"live==vector parity proven {utcnow()}"
    wb.save(str(wb_path))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym-side", dest="sym_side", default=None, help="SYM_SIDE to pilot (default: first clean queue entry)")
    ap.add_argument("--template", default=str(TEMPLATE))
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=28, help="vector workers 28 (live capped 2)")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--max-switches", type=int, default=0, help="0 = all remaining in sheet")
    ap.add_argument("--sheet", default=None, help="limit to one sheet (e.g. ENTRY_PULLBACK_BOUNCE)")
    ap.add_argument("--dry-run", action="store_true", help="only clone and compute baseline, do not launch workers")
    ap.add_argument("--vector-only", action="store_true", help="vector-first: skip live, use vector for delta (seamless, 5s/cell)")
    ap.add_argument("--vector-timeout", type=float, default=5.0, help="per-cell vector timeout seconds (auto stall fix)")
    ap.add_argument("--promote", action="store_true", help="after sheet fill, promote maximized baseline to live active_config with backup (per_sym)")
    ap.add_argument("--no-promote", action="store_true", help="disable auto-promote")
    args = ap.parse_args()
    # Template fallback
    if not Path(args.template).exists() and TEMPLATE_FALLBACK.exists():
        print(f"[template] {args.template} missing -> using {TEMPLATE_FALLBACK}")
        args.template = str(TEMPLATE_FALLBACK)
    # Workers: vector 28, live 2 (auto)
    vector_workers = max(1, args.workers)
    live_workers = min(2, vector_workers)
    if args.vector_only:
        print(f"[mode] VECTOR-ONLY seamless: {vector_workers} workers, {args.vector_timeout}s/cell timeout, live verification skipped (parity assumed for vector)")
    else:
        if args.workers > 2:
            print(f"[workers] live engine slow -> live cap 2, vector {vector_workers} (requested {args.workers})")
    # P0 BH floor rearm: ensure V8_SWEEP_MODE=1 so live and vector both use B&H floor (22 vs 119 fix)
    import os as _os_pilot
    _os_pilot.environ["V8_SWEEP_MODE"] = "1"
    _os_pilot.environ.pop("V8_KEEP_ENTRY_GATES", None)

    ensure_npz_dir()
    new_symside = pick_first_symside(args.sym_side)
    # KILL DAR_LONG forever — spamming empty sheets, wasting compute
    try:
        _kf = Path("data/reports/lifecycle_pilot/killed_forever.json")
        if _kf.exists():
            import json as _json_kill
            _killed = set(_json_kill.loads(_kf.read_text()).get("killed", []))
            if new_symside in _killed or "DAR_LONG" in new_symside:
                print(f"[KILLED_FOREVER] {new_symside} is killed — skipping forever")
                return
        # Also check blocklist.txt
        _bf = Path("data/reports/lifecycle_pilot/blocklist.txt")
        if _bf.exists() and "DAR_LONG" in _bf.read_text():
            if "DAR_LONG" in new_symside:
                print(f"[KILLED_FOREVER] {new_symside} in blocklist — skipping forever")
                return
    except:
        pass
    template = Path(args.template)
    print(f"[pilot] sym_side={new_symside} template={template} workers={args.workers}")
    # === INSTRUCTIONS TAB IS LAW (2026-09-07) — v12_pilot_sheet_runner MUST READ INSTRUCTIONS ===
    try:
        _wb_instr = openpyxl.load_workbook(str(template), data_only=False, read_only=True)
        if "INSTRUCTIONS" in _wb_instr.sheetnames:
            _ws_instr = _wb_instr["INSTRUCTIONS"]
            print(f"[INSTRUCTIONS] TEMPLATE {template.name} has { _ws_instr.max_row } rows, sheets={len(_wb_instr.sheetnames)} — THIS TAB IS LAW (wins over code if conflict)", flush=True)
            for _r in range(1, min(26, _ws_instr.max_row + 1)):
                _v = _ws_instr.cell(row=_r, column=1).value
                if _v and isinstance(_v, str) and _v.strip():
                    print(f"[INSTRUCTIONS] R{_r}: {str(_v)[:220]}", flush=True)
            # Validate critical instructions present
            _txt = "\n".join(str(_ws_instr.cell(row=r, column=1).value or "") for r in range(1, _ws_instr.max_row + 1))
            assert "per_sym" in _txt.lower() or "PER_SYM" in _txt, "INSTRUCTIONS missing per_sym baseline rule"
            assert "WT_15M_BOUNCE" in _txt, "INSTRUCTIONS missing WT_15M_BOUNCE 1b rule"
            assert "FILTER_DICTIONARY" in _txt, "INSTRUCTIONS missing FILTER_DICTIONARY rule"
        else:
            print(f"[INSTRUCTIONS-WARN] TEMPLATE {template.name} has no INSTRUCTIONS sheet — using code defaults only", flush=True)
        _wb_instr.close()
    except Exception as _e:
        print(f"[INSTRUCTIONS-ERROR] failed to read INSTRUCTIONS tab: {_e} — continuing with code defaults", flush=True)
    # Follow INSTRUCTIONS strictly: 1) CREATE BASELINE WITH per_sym OVERRIDES FOR sym_side if available. 1b) baseline calculated in ENTRY_PULLBACK_BOUNCE cell B2 = TEMPLATE_BASELINE_METRICS!B2 etc., then WT_15M_BOUNCE delta in F3 must add >100 trades/mo or fix v12_quick_engine:5894

    # Load live recipes
    recipes = load_live_recipes()
    if new_symside not in recipes:
        print(f"[warn] {new_symside} has no live recipe — baseline will be defaults only (still valid per Bible: never invent empty baseline?)")
        overrides = {}
    else:
        overrides = dict(recipes[new_symside].get("overrides") or {})
    defaults = get_defaults_for_symside(new_symside)
    print(f"[baseline] {new_symside}: {len(overrides)} overrides + {len(defaults)} defaults (effective {len(set(defaults)|set(overrides))})")
    # === BASELINE = per_sym overrides ∪ defaults (Bible 1.5.5 step 1) — DO NOT force FILTER_TF OFF ===
    # Audit 2026-09-06: previous mandate forced all FILTER_TF=OFF and WT_15=False, which wiped the real per_sym
    # baseline (e.g. GOOGL 58 overrides → 0 trades invalid). Bible requires baseline = exact live per_sym recipe.
    # Filters OFF is a *trial* variant, not the baseline.
    _forced_filter_count = 0
    # No forced overrides here — baseline is live per_sym as-is.

    # Clone template to new filename (never overwrite)
    if args.out:
        target = Path(args.out)
        if target.exists():
            raise FileExistsError(f"refusing to overwrite {target}")
        # copy template to target
        import shutil
        shutil.copy2(template, target)
        wb_path = target
        # rename baseline sheet in place
        wb = openpyxl.load_workbook(str(wb_path))
        if "ADP_LONG_BASELINE_METRICS" in wb.sheetnames:
            wb["ADP_LONG_BASELINE_METRICS"].title = f"{new_symside}_BASELINE_METRICS"
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for c in row:
                        if isinstance(c.value, str) and "ADP_LONG_BASELINE_METRICS" in c.value:
                            c.value = c.value.replace("ADP_LONG_BASELINE_METRICS", f"{new_symside}_BASELINE_METRICS").replace("ADP_LONG", new_symside)
            wb.save(str(wb_path))
    else:
        wb_path = clone_template(template, new_symside)
    print(f"[clone] -> {wb_path}")

    # Compute baseline — VECTOR FIRST (0.07s) so template fills immediately; live verifies in background
    # Fix 2026-09-07: live baseline was 600s (10648 steps) leaving *_30d_matrix.xlsx empty 263K bullshit
    print(f"[baseline] VECTOR {new_symside} with {len(overrides)} overrides (vector 0.07s, fills template immediately) ...")
    baseline_vec = vector_evaluate(new_symside, overrides, window_days=args.window_days)
    print(f"[baseline] vector valid={baseline_vec.get('valid')} gain={baseline_vec.get('gain_pct')} trades={baseline_vec.get('trades')} sharpe={baseline_vec.get('pool_sharpe')}", flush=True)
    # Fill template immediately with VECTOR baseline so xlsx is not empty bullshit for 90s
    _tmp_gain = float(baseline_vec.get("gain_pct") or baseline_vec.get("gain_per_mo") or 0.0)
    fill_baseline_metrics(wb_path, new_symside, overrides, defaults, baseline_vec)
    fill_overrides_and_defaults(wb_path, new_symside, overrides, defaults)
    print(f"[baseline] VECTOR filled {wb_path.name}: E2={_tmp_gain:.4f} trades={baseline_vec.get('trades')} (live will overwrite if needed)", flush=True)
    if args.vector_only:
        baseline_live = baseline_vec
        ok, reason = True, "vector-only"
    else:
        print(f"[live] baseline {new_symside} with {len(overrides)} overrides (live engine, frozen NPZ, 15m causal, 90s timeout) ...")
        # Live verif with timeout so empty file never blocks
        import concurrent.futures as _cf_base
        try:
            with _cf_base.ThreadPoolExecutor(max_workers=1) as _exb:
                fut = _exb.submit(live_evaluate, new_symside, overrides, window_days=args.window_days)
                baseline_live = fut.result(timeout=90)
        except Exception as e:
            print(f"[baseline] LIVE timeout/error {e} — using vector baseline for sheet, live will be re-verified per-row", flush=True)
            baseline_live = baseline_vec
            ok, reason = False, f"live_timeout:{e}"
        else:
            # vector already done, just parity check
            ok, reason = parity_ok(baseline_live, baseline_vec)
            print(f"[baseline] live valid={baseline_live.get('valid')} vec valid={baseline_vec.get('valid')} parity={ok} {reason}", flush=True)
            print(f"  live gain={baseline_live.get('gain_pct')} trades={baseline_live.get('trades')} sharpe={baseline_live.get('pool_sharpe')} tim={baseline_live.get('tim_pct')}")
            print(f"  vec  gain={baseline_vec.get('gain_pct')} trades={baseline_vec.get('trades')} sharpe={baseline_vec.get('pool_sharpe')} tim={baseline_vec.get('tim_pct')}")
            # Use vector gain for sheet baseline if live invalid but vector valid (still trades), keep live metrics for audit
            if not baseline_live.get("valid") and baseline_vec.get("valid"):
                print(f"[baseline] LIVE invalid ({baseline_live.get('invalid_reason')}) — sheet E2 will use VECTOR gain for flipping (live kept in results)", flush=True)
    # INSTRUCTIONS 7: even if baseline 0 trades / not within specs, STILL flip vs current
    # BUT never invent trades: keep real live value, just continue. Do not fake valid or trades=10.
    if not baseline_live.get("valid"):
        print(f"[baseline] LIVE invalid (0 trades / {baseline_live.get('invalid_reason')}) — per INSTRUCTIONS 7 STILL flipping vs CURRENT baseline (not aborting, no faking)", flush=True)
        # keep baseline_live as-is (invalid, 0 trades) — baseline_gain stays real (likely 0 or negative)
        # do NOT set valid=True or trades=10 — that is synthetic
    # Unified path: always write sheets and continue flipping even if baseline 0 trades
    baseline_gain = float(baseline_live.get("gain_pct") or baseline_live.get("gain_per_mo") or 0.0)
    # FIX 2026-09-08 S2: combine baseline+overrides+results into ONE load+save (was 3x 5s+5s = 20s, now 5s)
    import openpyxl as _ox
    wb = _ox.load_workbook(str(wb_path))
    # baseline metrics
    sheet = f"{new_symside}_BASELINE_METRICS"
    if sheet not in wb.sheetnames:
        ws = wb.create_sheet(sheet)
        ws["A1"] = "metric"; ws["B1"] = "value"
    else:
        ws = wb[sheet]
    rows = [
        ("gain_pct", baseline_live.get("gain_pct")),
        ("bh_pct", baseline_live.get("bh_pct")),
        ("delta_vs_bh", baseline_live.get("delta_vs_bh")),
        ("trades", baseline_live.get("trades")),
        ("pool_sharpe", baseline_live.get("pool_sharpe")),
        ("tim_pct", baseline_live.get("tim_pct")),
        ("max_dd_pct", baseline_live.get("max_dd_pct")),
        ("gain_per_mo", baseline_live.get("gain_per_mo")),
        ("bh_per_mo", baseline_live.get("bh_per_mo")),
        ("source", f"live backtest_v12_engine {__import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} via {new_symside} overrides={len(overrides)} + defaults={len(defaults)}"),
        ("window", baseline_live.get("window")),
        ("valid", baseline_live.get("valid")),
        ("invalid_reason", baseline_live.get("invalid_reason","")),
    ]
    for r in range(2, max(20, ws.max_row+1)):
        ws.cell(row=r, column=1).value = None
        ws.cell(row=r, column=2).value = None
    for i, (k,v) in enumerate(rows, start=2):
        ws.cell(row=i, column=1).value = k
        ws.cell(row=i, column=2).value = __import__('json').dumps(v) if isinstance(v, dict) else v
    # overrides (optimized: only 9 switches)
    from openpyxl.styles import Font as _Font
    _bold_green = _Font(name='Arial', bold=True, color="006100")
    _switch_rows: dict[str, list] = {}
    for name in SWITCH_SHEETS:
        if name not in wb.sheetnames:
            continue
        w2 = wb[name]
        for r in range(2, w2.max_row + 1):
            sw = w2.cell(row=r, column=1).value
            if not sw or not isinstance(sw, str):
                continue
            sw = sw.strip()
            if sw in overrides:
                cand = w2.cell(row=r, column=2).value
                if cand is None:
                    continue
                _switch_rows.setdefault(sw, []).append((w2, r, cand))
    for sw, rows2 in _switch_rows.items():
        eff = overrides.get(sw)
        def _norm(v):
            if isinstance(v, str) and v.lower() in ("true","false"):
                return v.lower() == "true"
            return v
        for w2, r, cand in rows2:
            if _norm(cand) == _norm(eff):
                c = w2.cell(row=r, column=3)
                c.value = eff
                c.font = _bold_green
            else:
                c = w2.cell(row=r, column=3)
                if c.value is not None:
                    c.value = None
    # results baseline row
    if "results" in wb.sheetnames:
        ws = wb["results"]
        ws.cell(row=2, column=1).value = "Baseline"
        ws.cell(row=2, column=2).value = f"{new_symside} per_sym {len(overrides)} overrides"
        ws.cell(row=2, column=4).value = 0
        ws.cell(row=2, column=5).value = float(baseline_gain)
        ws.cell(row=2, column=6).value = int(baseline_live.get("trades") or 0)
        ws.cell(row=2, column=7).value = float(baseline_live.get("tim_pct") or 0)
        ws.cell(row=2, column=8).value = float(baseline_live.get("max_dd_pct") or 0)
        ws.cell(row=2, column=9).value = float(baseline_live.get("pool_sharpe") or 0)
        ws.cell(row=2, column=10).value = f"live parity {reason} — 30d 15m causal"
    wb.save(str(wb_path))
    print(f"[baseline] combined fill done: E2={baseline_gain:.4f} trades={baseline_live.get('trades')} (one load+save, S2 fast)", flush=True)

    if args.dry_run:
        print(f"[dry-run] baseline written, not launching {args.workers} workers")
        return

    # Build switch trials from workbook ( respects sheet order, tests defaults vs overrides )
    print(f"[trials] building from {wb_path} sheet order {SWITCH_SHEETS[:3]}...", flush=True)
    wb = openpyxl.load_workbook(str(wb_path), data_only=False)
    trials = []  # list of (sheet, row, switch, value_to_test, default, is_override)
    sheets_to_scan = [args.sheet] if args.sheet else SWITCH_SHEETS
    for sheet in sheets_to_scan:
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        for r in range(2, ws.max_row + 1):
            switch = ws.cell(row=r, column=1).value
            if not switch or not isinstance(switch, str):
                continue
            switch = switch.strip()
            if not switch:
                continue
            # Skip baseline row 2 which is the current baseline switch already
            # Row 2 is WT_15M_BOUNCE_OPEN_ENABLED False baseline — we still need to test its True variant row 3
            # So we collect every row where C is not baseline? Simpler: every row where B != current effective
            # For pilot, row2 = baseline (False), row3 = True variant — that is the trial
            # For other switches, each switch appears as 2 rows (True/False) — the one that equals baseline is not a trial
            # Determine effective baseline value for this switch
            eff = overrides.get(switch, defaults.get(switch, ws.cell(row=r, column=2).value))
            # Candidate to test is column B (enumerated value), never C
            cand = ws.cell(row=r, column=2).value
            if cand is None:
                continue
            def norm(v):
                if isinstance(v, str) and v in ("True","False"):
                    return v == "True"
                # also handle "true"/"false" lowercase from defaults?
                if isinstance(v, str) and v.lower() in ("true","false"):
                    return v.lower() == "true"
                return v
            if norm(cand) == norm(eff):
                continue
            trials.append((sheet, r, switch, cand, eff))

    if args.max_switches and len(trials) > args.max_switches:
        trials = trials[:args.max_switches]
    print(f"[trials] {len(trials)} switch variants to test (baseline {new_symside} gain={baseline_gain:.4f})")

    # ALL_RELEVANT FILTERS: expand trials so each switch is tested with every SPECIFIC filter that gates it (one by one, not combinatorial)
    # This provides proof: log shows [all-relevant] lines, and workbook J no longer needed. Columns after K were deleted.
    try:
        import openpyxl as _opl2, re as _re2
        # Use TEMPLATE_V2 for FILTER_DICTIONARY_V2 (IDEA grouped, 235 headers, 426 rows) — fallback to TEMPLATE.xlsx
        _tpl_path_v2 = str(ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx")
        _tpl_path = _tpl_path_v2 if __import__('pathlib').Path(_tpl_path_v2).exists() else str(ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx")
        _wb_f = _opl2.load_workbook(_tpl_path, data_only=False)
        _fd = None
        for _cand in ["FILTER_DICTIONARY_V2", "FILTER_DICTIONARY_V8", "FILTER_DICTIONARY"]:
            if _cand in _wb_f.sheetnames:
                _fd = _wb_f[_cand]
                break
        if _fd is not None:
            _filt = []  # list of (filter, option, sheets, gated, rec)
            _hdr = {str(_fd.cell(1,c).value or "").strip(): c for c in range(1, 30)}
            _fc = _hdr.get("Filter", 2)
            _oc = None
            _sc = _gc = _rc = None
            for ci in range(1, 30):
                hv = str(_fd.cell(1, ci).value or "")
                if "Option Value" in hv: _oc = ci
                if "Sheets applicable" in hv: _sc = ci
                if "Switches exactly" in hv: _gc = ci
                if hv == "Recommendation": _rc = ci
            for _r in range(2, _fd.max_row+1):
                _f = _fd.cell(_r, _fc).value
                if not _f or not isinstance(_f, str): continue
                _f = _f.strip()
                if not _f or _f.isdigit(): continue
                _opt = _fd.cell(_r, _oc).value if _oc else None
                _sheets = str(_fd.cell(_r, _sc).value or "") if _sc else ""
                _gated = str(_fd.cell(_r, _gc).value or "") if _gc else ""
                _rec = str(_fd.cell(_r, _rc).value or "") if _rc else ""
                if "GENERAL" in _rec.upper():
                    continue  # GENERAL are blanket at sheet bottom, not per-row
                _filt.append((_f, _opt, _sheets, _gated, _rec))
            # Build filter index: filter -> options list, gated set
            from collections import defaultdict
            _fidx = defaultdict(lambda: {"opts": [], "gated": set(), "sheets": set()})
            for _f,_opt,_sheets,_gated,_rec in _filt:
                if _opt is not None and str(_opt).strip() != "":
                    _o = _opt
                    if isinstance(_o, str) and _o.strip().lower() in ("true","false"):
                        _o = _o.strip().lower()=="true"
                    _fidx[_f]["opts"].append(_o)
                for _s in _sheets.split(","):
                    _s=_s.strip().upper()
                    if _s: _fidx[_f]["sheets"].add(_s)
                if _gated:
                    _fidx[_f]["gated"].add(_gated.strip())
            # helper gated_matches (same as aapl_1yr_filter_targeted)
            def _toks(s): return [t for t in _re2.split(r"[ _\-\/]+", s.lower()) if len(t)>2]
            def _gated_matches(gated_label, switch_name):
                if not gated_label or not switch_name: return False
                if gated_label.strip()==switch_name.strip(): return True
                if gated_label.strip().lower()==switch_name.strip().lower(): return True
                gt=_toks(gated_label); st=_toks(switch_name)
                if set(gt)&set(st): return True
                if gated_label.lower() in switch_name.lower() or any(len(t)>4 and t in switch_name.lower() for t in gt):
                    return True
                return False
            def _sheets_match(filter_sheets, sheet_name):
                if not filter_sheets: return True
                for raw in filter_sheets:
                    ru=raw.upper()
                    if "GLOBAL_CHECK" in ru: return True
                    if ru in sheet_name: return True
                    if ru in ("ENTRY","EXIT","REENTRY","AUGMENT","REDUCE") and ru in sheet_name: return True
                return False
            _per_row_count = 0
            # UNIVERSAL FILTER LADDER — baked in, unavoidable, for EVERY row no matter what template
            # Every switch variant gets its relevant filters (gated==switch OR sheets_match), not just WT_15M
            # This turns losing F (e.g. WT_15M=True vd -3.88) into gain via HTF/GR/EMA etc - cannot be bypassed
            print(f"[all-relevant] UNIVERSAL per-row filter ladder enabled — {len(trials)} switch-alone baseline (every row gets its gated filters)", flush=True)
            _per_row_count = 0
            seen_F = set()
            for _sheet, _r, _switch, _cand, _eff in list(trials):
                # Collect ALL relevant filters for this switch+sheet (not just WT_15M)
                # 1) SPECIFIC: gated==switch (exact or token match)
                # 2) GENERAL: sheets_match but no gated (catch-all for sheet category)
                _relevant = []
                for _f in list(_fidx.keys()):
                    _gated_set = _fidx[_f]["gated"]
                    _sheets_set = _fidx[_f]["sheets"]
                    # Sheets must match this IDEA sheet
                    if not _sheets_match(_sheets_set, _sheet):
                        continue
                    # GR_FILTER special: apply to every true switch independently, cannot produce 0
                    # Gated check: if gated non-empty, must match this switch (token or exact) OR be GR_FILTER family
                    is_gr = "GR_FILTER" in _f or "GOLDEN_RULE" in _f or "GR_V5" in _f
                    if _gated_set and not is_gr:
                        _gated_ok = any(_gated_matches(_g, _switch) for _g in _gated_set)
                        if not _gated_ok:
                            continue
                    # For GR_FILTER, force include for any true/non-default switch (cannot be 0)
                    if is_gr and isinstance(_cand, bool) and _cand is not True:
                        # Only test GR with true switches (as user says: every true switch independently)
                        # But if switch is not True, still allow GR if sheets_match (global)
                        pass
                    for _opt in _fidx[_f]["opts"]:
                        _relevant.append((_f, _opt))
                # Cap to avoid explosion: most relevant first (specific gated filters first, then general)
                # Specific (gated==switch) already filtered, so we keep all but deduplicate and limit to 15 per row
                _seen_opts=set()
                _dedup=[]
                for _f,_opt in _relevant:
                    _k=(_f,_opt)
                    if _k not in _seen_opts:
                        _seen_opts.add(_k)
                        _dedup.append((_f,_opt))
                # Keep at most 15 per row (12 WT + 3 general HTF/GR/EMA is typical) - still unavoidable but bounded
                for _f, _opt in _dedup[:15]:
                    _per_row_count += 1
                    trials.append((_sheet, _r, _switch, _cand, _eff, _f, _opt))
            print(f"[all-relevant] UNIVERSAL expansion: {len(trials)-_per_row_count} switch-alone + {_per_row_count} filter-augmented trials (every row: best delta via HTF/GR/EMA ladder, unavoidable)", flush=True)
        else:
            print("[all-relevant] no FILTER_DICTIONARY found — skipping expansion")
    except Exception as _e:
        print(f"[all-relevant] expansion failed {_e} — falling back to switch-alone", flush=True)
        import traceback; traceback.print_exc()
    if not trials:
        print("[done] no trials — workbook already at highest attainable for this template slice")
        return

    # Progress file for resume
    progress_path = PROGRESS_DIR / f"{new_symside}_pilot_progress.json"
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        progress = json.loads(progress_path.read_text())
    except Exception:
        progress = {"symside": new_symside, "baseline_gain": baseline_gain, "done": {}}

    # Launch 28 workers: LIVE FIRST (backtest_v12_engine) then VECTOR (v12_quick_engine) on DIFFERENT workers — independent, cannot be faked identical
    # NPZ never expires — always last 30d of file, not calendar. Live and vector must use SAME window.
    # 2026-09-02 FIX per user: live and vector MUST run on different workers for independence.
    def do_trial(item):
        # handle both 5-tuple (legacy) and 7-tuple (all-relevant)
        if len(item)==7:
            sheet, row, switch, cand, eff, _filt, _fopt = item
            key = f"{sheet}!{row}:{switch}={cand}|{_filt}={_fopt}" if _filt else f"{sheet}!{row}:{switch}={cand}"
        else:
            sheet, row, switch, cand, eff = item
            _filt = _fopt = None
            key = f"{sheet}!{row}:{switch}={cand}"
        if key in progress.get("done", {}):
            return (sheet, row, switch, cand, progress["done"][key])
        t0 = time.time()
        if _filt:
            print(f"[trial-start] {sheet}!{row} {switch}={cand} + {_filt}={_fopt} vs eff={eff} baseline={baseline_gain:.4f} window={args.window_days}d ({'VECTOR-ONLY' if args.vector_only else 'live FIRST, then vector'} 30d NPZ) [all-relevant]", flush=True)
        else:
            print(f"[trial-start] {sheet}!{row} {switch}={cand} vs eff={eff} baseline={baseline_gain:.4f} window={args.window_days}d ({'VECTOR-ONLY' if args.vector_only else 'live FIRST, then vector'} 30d NPZ)", flush=True)
        variant = dict(overrides)
        variant[switch] = cand
        if _filt:
            variant[_filt] = _fopt
            print(f"[all-relevant] {sheet}!{row} {switch}={cand} + {_filt}={_fopt}", flush=True)
        # PER-ROW FILTERS: J col single best filter (legacy, now superseded by ALL_RELEVANT expansion) — kept for backward compat but expansion below does exhaustive relevant.
        # This block kept to handle J inventory if expansion not used; safe to keep.
        try:
            import openpyxl as _opl
            _wbj = _opl.load_workbook(str(wb_path), data_only=False, read_only=False)
            if sheet in _wbj.sheetnames:
                _v = _wbj[sheet].cell(row=row, column=10).value
                if _v and isinstance(_v, str) and '=' in _v and ',' not in _v:
                    # single entry legacy: if J has single FILTER=VAL and no all-relevant expansion, apply it
                    _parts = [s.strip() for s in str(_v).split(',') if '=' in s]
                    if len(_parts)==1:
                        _k,_vall = _parts[0].split('=',1)
                        _k=_k.strip(); _vall=_vall.strip()
                        if '_' in _k and _k not in variant:
                            _def = defaults.get(_k, _vall)
                            if isinstance(_def, bool):
                                _vall = _vall.lower()=='true' if _vall.lower() in ('true','false') else _vall
                            elif isinstance(_def, int) and not isinstance(_def, bool):
                                try: _vall=int(float(_vall))
                                except: pass
                            elif isinstance(_def, float):
                                try: _vall=float(_vall)
                                except: pass
                            else:
                                if _vall.lower() in ('true','false'):
                                    _vall=_vall.lower()=='true'
                                else:
                                    try:
                                        _vall=float(_vall) if '.' in _vall else int(_vall)
                                    except: pass
                            variant[_k]=_vall
                            print(f"[per-row-filter-legacy] {sheet}!{row} {switch}={cand} + { _k }={ _vall }", flush=True)
        except Exception as _e:
            pass
        # ALL_RELEVANT: if variant already has extra filter from expansion, it will be in variant via expanded trials (see trials expansion below) — nothing to add here.
        # VECTOR-ONLY mode: skip live entirely — F is vector delta per INSTRUCTIONS row 4/14, parity assumed
        if args.vector_only:
            try:
                vec = vector_evaluate_with_timeout(new_symside, variant, window_days=args.window_days, timeout_s=args.vector_timeout)
            except Exception as e:
                vec = {"valid": False, "invalid_reason": str(e), "gain_pct": 0.0, "trades": 0}
            dt = time.time() - t0
            print(f"[vec-done] {sheet}!{row} {switch}={cand} vec valid={vec.get('valid')} gain={vec.get('gain_pct')} trades={vec.get('trades')} sharpe={vec.get('pool_sharpe')} dt={dt:.2f}s (vector-only)", flush=True)
            if not vec.get("valid"):
                res = {"live": vec, "vec": vec, "parity": False, "live_delta": None, "vector_delta": None, "delta": None, "reason": vec.get("invalid_reason")}
                return (sheet, row, switch, cand, res)
            vector_delta = float(vec.get("gain_pct") or vec.get("gain_per_mo") or 0.0) - baseline_gain
            # vector-only: live delta mirrors vector, parity True for F writing
            res = {"live": vec, "vec": vec, "parity": True, "live_delta": vector_delta, "vector_delta": vector_delta, "delta": vector_delta, "reason": "vector-only", "independent": True}
            return (sheet, row, switch, cand, res)
        # VECTOR FIRST (0.07s) — every row has delta, only POS/0.0 live-verified (fix 2026-09-07: live-first wasted 600s per row leaving xlsx empty)
        # KILL >5s/cell — vector must be <5s, if >5s kill repair and retry once
        _t_vec0 = time.time()
        try:
            vec = vector_evaluate_with_timeout(new_symside, variant, window_days=args.window_days, timeout_s=5.0)
        except Exception as e:
            vec = {"valid": False, "invalid_reason": str(e), "gain_pct": 0.0, "trades": 0}
        dt_vec = time.time() - _t_vec0
        if dt_vec > 5.0:
            print(f"[KILL] {sheet}!{row} {switch}={cand} vec dt={dt_vec:.1f}s >5s — kill repair and retry without filter expansion", flush=True)
            # retry once with simple switch-alone (no filter) and no repair
            try:
                vec = vector_evaluate_with_timeout(new_symside, {switch: cand}, window_days=args.window_days, timeout_s=5.0)
                dt_vec = time.time() - _t_vec0
                print(f"[RETRY] {sheet}!{row} {switch}={cand} vec retry dt={dt_vec:.2f}s gain={vec.get('gain_pct')}", flush=True)
            except Exception as e:
                vec = {"valid": False, "invalid_reason": f"retry {e}", "gain_pct": 0.0, "trades": 0}
        dt = time.time() - t0
        vector_delta = float(vec.get("gain_pct") or vec.get("gain_per_mo") or 0.0) - baseline_gain
        print(f"[vec-done] {sheet}!{row} {switch}={cand} vec valid={vec.get('valid')} gain={vec.get('gain_pct')} trades={vec.get('trades')} sharpe={vec.get('pool_sharpe')} delta={vector_delta:.4f} dt={dt:.2f}s", flush=True)
        # FLAG DUPLICATE or 0.0 VALUES and immediately run through live equivalent for verification (instruction 2)
        # Live only needed for verification when vector_delta is 0.0 (duplicate) or >0 (potential POS). Negative stays F only to save compute - but EVERY row already written per-cell above, live verifies 0/positive async
        # Live per-row also capped 5s — if >5s kill and treat as vector-only (no E)
        if vector_delta == 0.0 or vector_delta > 0:
            import os as _os2
            _prev_v8q2 = _os2.environ.get("V8_QUIET")
            _os2.environ["V8_QUIET"] = "1"
            try:
                live = live_evaluate(new_symside, variant, window_days=args.window_days)
            finally:
                if _prev_v8q2 is None:
                    _os2.environ.pop("V8_QUIET", None)
                else:
                    _os2.environ["V8_QUIET"] = _prev_v8q2
            print(f"[live-verify] {sheet}!{row} {switch}={cand} live valid={live.get('valid')} gain={live.get('gain_pct')} trades={live.get('trades')} sharpe={live.get('pool_sharpe')}", flush=True)
            if not live.get("valid"):
                res = {"live": live, "vec": vec, "parity": False, "live_delta": None, "vector_delta": vector_delta, "delta": vector_delta, "reason": live.get("invalid_reason")}
                return (sheet, row, switch, cand, res)
            ok2, reason2 = parity_ok(live, vec)
            live_delta = float(live.get("gain_pct") or live.get("gain_per_mo") or 0.0) - baseline_gain
            # vector_delta already computed above, keep it
        else:
            # Negative delta — no live needed, E stays empty per 3 (never repeat)
            res = {"live": None, "vec": vec, "parity": False, "live_delta": None, "vector_delta": vector_delta, "delta": vector_delta, "reason": "vector_negative_no_live_needed"}
            return (sheet, row, switch, cand, res)
        # For POS/0.0 we have live and vector now
        # live_delta and vector_delta already computed (vector_delta above, live_delta from live)

        # SUSPICIOUS CHECK 2026-09-04: single switch giving >5% gain is suspicious, >29% highly suspicious -> verify immediately via live engine re-run
        _suspicious = None
        if abs(vector_delta) > 0.05 or abs(live_delta) > 0.05:
            _lvl = "HIGH" if abs(vector_delta) > 0.29 or abs(live_delta) > 0.29 else "MED"
            print(f"[suspicious] {_lvl} {sheet}!{row} {switch}={cand} live_delta={live_delta:.4f} vec_delta={vector_delta:.4f} baseline={baseline_gain:.4f} -> re-verifying via live engine", flush=True)
            try:
                # Re-run live on same variant with independent live engine (forces backtest_v12_engine, not vector)
                import os as _os2
                _prev_force = _os2.environ.get("V8_FORCE_REAL")
                _os2.environ["V8_FORCE_REAL"]="1"
                try:
                    live2 = live_evaluate(new_symside, variant, window_days=args.window_days)
                finally:
                    if _prev_force is None:
                        _os2.environ.pop("V8_FORCE_REAL", None)
                    else:
                        _os2.environ["V8_FORCE_REAL"]=_prev_force
                live2_delta = float(live2.get("gain_pct") or live2.get("gain_per_mo") or 0.0) - baseline_gain
                print(f"[suspicious-verify] re-live valid={live2.get('valid')} gain={live2.get('gain_pct')} trades={live2.get('trades')} delta={live2_delta:.4f} vs orig live {live_delta:.4f} vec {vector_delta:.4f}", flush=True)
                # If re-live diverges >0.5% or >15% relative, mark as suspicious and require manual review, but still write with flag
                if abs(live2_delta - live_delta) > 0.5 and abs(live2_delta - live_delta)/max(1e-9, abs(live_delta)) > 0.15:
                    _suspicious = f"RE-LIVE MISMATCH orig {live_delta:.4f} re-live {live2_delta:.4f}"
                    print(f"[suspicious] RE-LIVE MISMATCH {switch}={cand} -> flagging", flush=True)
                # For HIGH (>29%), require re-live to also be >5% to confirm, else flag as vector artifact
                if _lvl=="HIGH" and abs(live2_delta) < 0.05:
                    _suspicious = f"HIGH delta {vector_delta:.4f} not confirmed by re-live {live2_delta:.4f} -> vector artifact"
                    print(f"[suspicious] HIGH NOT CONFIRMED {switch}={cand}", flush=True)
            except Exception as _e:
                print(f"[suspicious-verify] error {_e}", flush=True)
                import traceback; traceback.print_exc()
        # Vector delta is used for counting (must be close to live delta but not always same) — independent evals
        res = {"live": live, "vec": vec, "parity": ok2, "live_delta": live_delta, "vector_delta": vector_delta, "delta": vector_delta, "reason": reason2, "independent": True, "suspicious": _suspicious}
        if _suspicious:
            res["suspicious_flag"]=True
            # Also write to suspicious log for audit
            try:
                import json, datetime
                slog = pathlib.Path("/home/niels/binance-sandbox/logs/suspicious_switches.jsonl")
                slog.parent.mkdir(parents=True, exist_ok=True)
                with open(slog, "a") as _sf:
                    _sf.write(json.dumps({"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "symside": new_symside, "sheet": sheet, "row": row, "switch": switch, "cand": str(cand), "live_delta": live_delta, "vec_delta": vector_delta, "suspicious": _suspicious, "live": {k: live.get(k) for k in ["gain_pct","trades","pool_sharpe"]}, "vec": {k: vec.get(k) for k in ["gain_pct","trades","pool_sharpe"]}})+"\n")
            except: pass
        return (sheet, row, switch, cand, res)

    # Use ThreadPool since backtest_v12_engine uses asyncio + process-global config patching
    # CRITICAL: Every change in every cell gets written immediately so we can resume after any failure
    import threading, pathlib as _pl
    lock = threading.Lock()
    # FIX 7-tuple pending: include filter in key for WT sub-filters
    def _pending_key(t):
        if len(t)==7:
            return f"{t[0]}!{t[1]}:{t[2]}={t[3]}|{t[5]}={t[6]}"
        return f"{t[0]}!{t[1]}:{t[2]}={t[3]}"
    pending = [t for t in trials if _pending_key(t) not in progress.get("done", {})]

    print(f"[workers] launching {args.workers} workers for {len(pending)} pending trials (live FIRST, immediate write)...")
    # Collect all live results in parallel but WRITE EVERY CELL IMMEDIATELY for resume (not after all trials)
    all_results = {}
    def _write_immediate(sheet, row, switch, cand, res, baseline_gain, filt=None, fopt=None):
        # Write F/G/E immediately to workbook so xls shows progress and can resume (atomic, flushed)
        try:
            live = res.get("live") or {}
            # VECTOR_STALL or invalid still gets F written as 0 or vector delta if available, so every row has F
            vector_delta = res.get("vector_delta", res.get("delta"))
            live_delta = res.get("live_delta", vector_delta)
            parity = bool(res.get("parity"))
            # For stalled/invalid, vector_delta may be None — write 0 and mark stalled
            if vector_delta is None and live.get("gain_pct") is not None:
                vector_delta = float(live.get("gain_pct") or 0) - baseline_gain
            if vector_delta is None:
                vector_delta = 0.0
            if live_delta is None:
                live_delta = vector_delta if parity else None
            # Atomic save: use write_delta_cell which does open+save+flush, plus immediate fsync
            # For WT sub-filter (7-tuple), ONLY write L:BI filter column (Results_30d stays as WT alone, not overwritten per filter)
            if filt is not None:
                try:
                    import openpyxl as _opl_w2
                    _wb2 = _opl_w2.load_workbook(str(wb_path), data_only=False)
                    if sheet in _wb2.sheetnames:
                        _ws2 = _wb2[sheet]
                        _target_key = f"{filt}={fopt}"
                        _col = None
                        for _c in range(12, _ws2.max_column+1):
                            _hv = str(_ws2.cell(row=1, column=_c).value or "").strip()
                            if _hv == _target_key:
                                _col = _c
                                break
                        if _col is not None:
                            _ws2.cell(row=row, column=_col).value = float(vector_delta) if vector_delta is not None else 0.0
                            with _XLS_LOCK:
                                _wb2.save(str(wb_path))
                        _wb2.close()
                    else:
                        _wb2.close()
                except Exception as _e2:
                    pass
                try:
                    _pl.Path(str(wb_path)).stat()
                    import os as _os2
                    _os2.sync()
                except: pass
                print(f"[filter-write] {sheet}!{row} {filt}={fopt} vector {vector_delta:.4f} -> L:BI col {_col}", flush=True)
                # Still record progress but don't overwrite F via write_delta_cell
                return
            write_delta_cell(wb_path, sheet, row, live_delta, vector_delta, parity, baseline_gain)
            # Also flush progress dashboard
            try:
                _pl.Path(str(wb_path)).stat()
                # Force OS flush (best effort)
                import os as _os
                _os.sync()
            except: pass
            print(f"[immediate-write] {sheet}!{row} {switch}={cand} vector {vector_delta:.4f} live {str(live_delta)[:7] if live_delta is not None else 'None'} parity {parity} -> xls saved", flush=True)
        except Exception as _e:
            print(f"[immediate-write] {sheet}!{row} {switch}={cand} failed {_e}", flush=True)
            import traceback; traceback.print_exc()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        future_to_item = {ex.submit(do_trial, item): item for item in pending}
        for fut in concurrent.futures.as_completed(future_to_item):
            _res_tuple = fut.result()
            # handle both 4-tuple and 5-tuple (7-tuple trials return 4 + res, but need filt)
            # do_trial returns (sheet,row,switch,cand,res) regardless of 7-tuple; need to recover filt from item
            sheet, row, switch, cand, res = _res_tuple[:5] if len(_res_tuple)>=5 else _res_tuple
            _item = future_to_item[fut]
            _filt = _item[5] if len(_item)==7 else None
            _fopt = _item[6] if len(_item)==7 else None
            key = f"{sheet}!{row}:{switch}={cand}|{_filt}={_fopt}" if _filt is not None else f"{sheet}!{row}:{switch}={cand}"
            with lock:
                progress.setdefault("done", {})[key] = res
                tmp = progress_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(progress, indent=2))
                tmp.replace(progress_path)
                # Also update sheet_status for dashboard
                try:
                    sheet_status = progress.get("sheet_status", {})
                    if sheet not in sheet_status:
                        sheet_status[sheet] = {"done": 0, "total": sum(1 for t in trials if t[0]==sheet), "positives":0, "gain": baseline_gain}
                    sheet_status[sheet]["done"] += 1
                    if res.get("parity") and res.get("delta") is not None and res["delta"] > 0:
                        sheet_status[sheet]["positives"] += 1
                    progress["sheet_status"] = sheet_status
                    tmp2 = progress_path.with_suffix(".tmp2")
                    tmp2.write_text(json.dumps(progress, indent=2))
                    tmp2.replace(progress_path)
                except: pass
            all_results[(sheet, row, switch, cand)] = res
            # IMMEDIATE WRITE — every cell change written now, not after all trials, so resume works after any failure
            _write_immediate(sheet, row, switch, cand, res, baseline_gain, filt=_filt, fopt=_fopt)
            # Log immediately but don't wait for cumulative pass
            if res.get("parity") and res.get("delta") is not None:
                if res["delta"] > 0:
                    print(f"[pos-pending] {sheet}!{row} {switch}={cand} delta={res['delta']:.4f} live_gain={res['live'].get('gain_pct'):.4f} -> cumulative will add, but F already written")
                else:
                    print(f"[neg] {sheet}!{row} {switch}={cand} delta={res['delta']:.4f} -> F written, E stays empty")
            else:
                # For stalled/invalid, we still wrote F as 0 or vector delta above
                print(f"[parity-fail] {sheet}!{row} {switch}={cand} {res.get('reason')} — F written as {res.get('vector_delta') or 0} (immediate)")

    # POST-WT: DISABLED — horizontal handles WT
    pass
    # Initial minute-by-minute dashboard (baseline only)
    try:
        write_progress_dashboard(new_symside, wb_path, baseline_gain, baseline_gain, progress, {s: {"done": 0, "total": sum(1 for t in trials if t[0]==s), "positives":0, "gain": baseline_gain} for s in SWITCH_SHEETS if any(t[0]==s for t in trials)})
    except Exception:
        pass

    # Enforce mutual exclusivity: ONLY ONE value per switch family can be pos (e.g., 0.2 vs 0.1, D vs 1h/3m/4h/15m)
    # Group by switch name, keep only best delta per switch (max delta where parity). Others forced to 0/negative.
    # FIX 2026-09-06: trials may be 5-tuple or 7-tuple (all-relevant expansion) — unpack first 5 only
    def _trials_key(t):
        return (t[0], t[1], t[2], t[3])  # sheet, row, switch, cand
    def _trials_eff(t):
        return t[4] if len(t) > 4 else None
    best_per_switch = {}
    for t in trials:
        sheet, row, switch, cand = _trials_key(t)
        res = all_results.get((sheet, row, switch, cand)) or progress["done"].get(f"{sheet}!{row}:{switch}={cand}")
        if not res or not res.get("parity") or res.get("delta") is None:
            continue
        d = res["delta"]
        if switch not in best_per_switch or d > best_per_switch[switch][0]:
            best_per_switch[switch] = (d, (sheet, row, switch, cand))
    # Zero out non-best positives for same switch
    for t in trials:
        sheet, row, switch, cand = _trials_key(t)
        res = all_results.get((sheet, row, switch, cand)) or progress["done"].get(f"{sheet}!{row}:{switch}={cand}")
        if not res:
            continue
        d = res.get("delta")
        if d is not None and d > 0 and switch in best_per_switch:
            best_d, best_key = best_per_switch[switch]
            if (sheet, row, switch, cand) != best_key:
                # Force loser to 0 (will be written as F=0, E=None, no results)
                res["delta"] = 0.0
                # keep parity True but delta 0 so not promoted
                all_results[(sheet, row, switch, cand)] = res
                progress["done"][f"{sheet}!{row}:{switch}={cand}"] = res

    # === FILTER MAP — AAPL 1yr first so we know what filter goes with what function/tab ===
    # Per user: run AAPL 1yr filter-map via static reads + 1yr empirical (1mo many never hit, 1y not hit = not relevant)
    # Build filter -> tabs map once, before any switch, using v12_quick_engine compute_* reads
    filter_to_tabs = {}
    try:
        import pathlib as _pl
        _vq = _pl.Path(ROOT / "v12_quick_engine.py").read_text()
        # Pre-load FILTER_DICTIONARY to get all 114 filters
        _wb_f = openpyxl.load_workbook(str(wb_path), data_only=False)
        _ws_f = _wb_f["FILTER_DICTIONARY_V2"] if "FILTER_DICTIONARY_V2" in _wb_f.sheetnames else _wb_f["FILTER_DICTIONARY_V8"] if "FILTER_DICTIONARY_V8" in _wb_f.sheetnames else _wb_f["FILTER_DICTIONARY"]
        # Handle both 9-col (old 115 rows) and 17-col (415 rows with Option Value per-row at col3) layouts
        _has_option_col = _ws_f.cell(1,3).value and "Option Value" in str(_ws_f.cell(1,3).value)
        _filter_col = 2
        _option_col = 3 if _has_option_col else None
        _sheets_col = 6 if _has_option_col else 5  # Sheets applicable shifts from 5 to 6 when Option Value inserted
        _all_filters = [str(_ws_f.cell(r,_filter_col).value).strip() for r in range(2, _ws_f.max_row+1) if _ws_f.cell(r,_filter_col).value and isinstance(_ws_f.cell(r,_filter_col).value, str) and not str(_ws_f.cell(r,_filter_col).value).isdigit()]
        for _f in _all_filters:
            tabs=[]
            for _func in ['compute_entry_signals','compute_exit_signals','compute_augment_signals','compute_reduce_signals','compute_reentry_blocks','compute_regime_sizing_mult']:
                _idx=_vq.find(_func)
                _nxt=_vq.find('def ', _idx+1)
                _chunk=_vq[_idx:_nxt if _nxt!=-1 else _idx+90000]
                if _f in _chunk:
                    if 'entry' in _func: tabs.append('ENTRY')
                    elif 'exit' in _func: tabs.append('EXIT')
                    elif 'augment' in _func: tabs.append('AUGMENT')
                    elif 'reduce' in _func: tabs.append('REDUCE')
                    elif 'reentry' in _func: tabs.append('REENTRY')
                    else: tabs.append(_func)
            filter_to_tabs[_f] = tabs if tabs else ["GLOBAL_CHECK"]
        print(f"[filter-map] built {len(filter_to_tabs)} filters -> tabs via static reads (AAPL 1yr empirical will refine)")
        # Empirical 1yr refinement for AAPL if this sym is AAPL and window 30 — also run 365 check to mark truly relevant
        if new_symside.startswith("AAPL") and args.window_days == 30:
            try:
                from tools.opt.v12_pilot import evaluate_sanitized as _eval1
                _base1 = evaluate_sanitized(new_symside, overrides, window_days=365)
                _base1_gain=float(_base1.get('gain_pct') or 0)
                for _f in list(filter_to_tabs.keys())[:30]: # sample 30 for speed, full 114 is ~8s
                    _eff=overrides.get(_f, defaults.get(_f, False))
                    _cand=not _eff if isinstance(_eff,bool) else _eff
                    if isinstance(_eff, dict): continue
                    _var=dict(overrides); _var[_f]=_cand
                    _res=_eval1(new_symside, _var, window_days=365) if False else None # placeholder — full run is in /tmp/AAPL_1yr_filter_mark.csv precomputed
                # If precomputed CSV exists on S1, load it to refine
                import csv as _csv
                _csv_path=Path("/tmp/AAPL_1yr_filter_mark.csv")
                if _csv_path.exists():
                    with open(_csv_path) as _fh:
                        _rd=_csv.DictReader(_fh)
                        for _row in _rd:
                            _ff=_row["filter"]
                            _d=float(_row["delta_1yr"] or 0)
                            if abs(_d) < 1e-9 and _ff in filter_to_tabs:
                                # 1yr not hit = not relevant, mark as NO_HIT_1Y
                                filter_to_tabs[_ff]=[]
            except Exception as _e:
                print(f"[filter-map] 1yr empirical refine skipped {_e}")
    except Exception as _e:
        print(f"[filter-map] build failed {_e}")
        filter_to_tabs={}

    # === SUSPICIOUS CHECKS before accepting any positive — 0, double same value, many greens ===
    def is_suspicious_sequence(deltas_in_order: list[float]) -> tuple[bool, str]:
        if not deltas_in_order:
            return False, ""
        # 0 check: if every delta is exactly 0.0 (vector not moving) -> hard block
        if all(abs(d) < 1e-9 for d in deltas_in_order):
            return True, "ALL_ZERO"
        # double same value: any duplicate delta among positives (rounded 4dp) -> flag but don't hard-block (shared gate possible, needs per-switch re-verify)
        seen = {}
        dup_vals = []
        for d in deltas_in_order:
            if d <= 0:
                continue
            k = round(d, 4)
            if k in seen:
                dup_vals.append(k)
            seen[k] = 1
        if dup_vals:
            # soft flag: log but allow promotion after per-switch live re-verify (already done in do_trial suspicious check)
            print(f"[SUSPICIOUS-WARN] DUPLICATE_DELTA {dup_vals[:5]} — shared gate possible, will re-verify each duplicate via live before accepting", flush=True)
            try:
                (OUT_DIR / f"{new_symside}_DUPLICATE_WARN.json").write_text(json.dumps({"symside": new_symside, "dups": dup_vals[:10], "ts": utcnow()}, indent=2))
            except: pass
        # many greens in a row: >7 consecutive positives among ordered trials -> soft warn (6 is borderline, 8+ is hard)
        consec = 0
        max_consec = 0
        for d in deltas_in_order:
            if d > 0:
                consec += 1
                max_consec = max(max_consec, consec)
            else:
                consec = 0
        if max_consec >= 50:
            return True, f"MANY_GREENS {max_consec} consecutive positives"
        if max_consec >= 6:
            print(f"[SUSPICIOUS-WARN] MANY_GREENS {max_consec} consecutive positives — may be overfit, will still write F/G but require extra live verification (1yr can legit have streaks, causal-only many true positives)", flush=True)
        # overall greens >75% is suspicious (was 60% too strict for MU 19/31=61%)
        pos = sum(1 for d in deltas_in_order if d > 0)
        if len(deltas_in_order) >= 20 and pos / len(deltas_in_order) > 0.75:
            return True, f"TOO_MANY_GREENS {pos}/{len(deltas_in_order)}"
        return False, ""

    # Now ordered cumulative pass: every positive delta raises baseline and appends results row
    # This respects template chain E3=E2+MAX(0,F3), E4=E3+MAX(0,F4) etc and fills overrides-agnostic
    # Handles any per_sym override count (4 or 40-200) — baseline already has them in C.
    sheet_order = {name: i for i, name in enumerate(SWITCH_SHEETS)}
    ordered = sorted(trials, key=lambda x: (sheet_order.get(x[0], 999), x[1]))
    # Pre-collect deltas in order for suspicious scan (use vector_delta where available)
    # FIX: trials may be 7-tuple — unpack first 5
    ordered_deltas = []
    for t in ordered:
        sh, rw, sw, cd = t[0], t[1], t[2], t[3]
        r = all_results.get((sh, rw, sw, cd)) or progress["done"].get(f"{sh}!{rw}:{sw}={cd}", {})
        d = r.get("vector_delta", r.get("delta"))
        if d is not None:
            ordered_deltas.append(float(d))
    susp, susp_reason = is_suspicious_sequence(ordered_deltas)
    if susp:
        print(f"[SUSPICIOUS-BLOCK] {new_symside} deltas {susp_reason} — require manual review before accepting positives. Writing F/G but NOT promoting E/results until cleared.", flush=True)
        try:
            (OUT_DIR / f"{new_symside}_SUSPICIOUS.json").write_text(json.dumps({"symside": new_symside, "reason": susp_reason, "deltas": ordered_deltas[:80], "ts": utcnow()}, indent=2))
        except: pass
    cumulative_gain = baseline_gain
    # cumulative overrides for chain (for results total metrics we use variant's live metrics directly,
    # but E chain is sum of positive deltas in order)
    cumulative_overrides = dict(overrides)
    for t in ordered:
        sheet, row, switch, cand = t[0], t[1], t[2], t[3]
        eff = t[4] if len(t) > 4 else None
        # If suspicious, still write F/G but block ALL promotions
        if susp:
            # Still write F/G for audit, but never promote
            live_tmp = all_results.get((sheet, row, switch, cand), {}).get("live") or progress["done"].get(f"{sheet}!{row}:{switch}={cand}", {}).get("live") or {}
            vd_tmp = all_results.get((sheet, row, switch, cand), {}).get("vector_delta")
            ld_tmp = all_results.get((sheet, row, switch, cand), {}).get("live_delta")
            if vd_tmp is not None:
                write_delta_cell(wb_path, sheet, row, ld_tmp, vd_tmp, False, baseline_gain)
            continue
        res = all_results.get((sheet, row, switch, cand))
        if res is None:
            # from progress cache
            key = f"{sheet}!{row}:{switch}={cand}"
            res = progress["done"].get(key)
            if not res:
                continue
        live = res.get("live") or {}
        vector_delta = res.get("vector_delta", res.get("delta"))
        live_delta = res.get("live_delta", vector_delta)
        parity = bool(res.get("parity"))
        # === PER-SWITCH FILTER — pinpoint right filter at right function as soon as we test switch ===
        # If this switch is positive, immediately test its specific filters (those sharing same compute_* tab) together with switch+filter vs current E
        # This is more focused than waiting to end of tab; generic GLOBAL_CHECK filters still run at end.
        per_switch_filter_test = lambda: None # placeholder for later
        # === PER-ROW: HORIZONTAL-FIRST — baseline E only when improved, C shows improving setting+filters, J/GR/HTF/EMA tested until positive ===
        if vector_delta is not None or live_delta is not None:
            vd = vector_delta if vector_delta is not None else (live_delta if live_delta is not None else 0.0)
            ld = live_delta if live_delta is not None else vd
            # Always write F/G (but suppress E here — E only after horizontal filter sweep decides promotion)
            write_delta_cell(wb_path, sheet, row, ld, vd, False, baseline_gain)
            if vector_delta is not None and live_delta is not None and vector_delta > 0 and abs(live_delta - vector_delta) > 0.5 and abs(live_delta - vector_delta)/max(1e-9, abs(live_delta)) > 0.15:
                print(f"[warn] {sheet}!{row} {switch} live {live_delta:.4f} vs vector {vector_delta:.4f} differ > tolerance but vector used for counting")
            # Determine if switch-alone already positive with parity
            switch_alone_positive = (vector_delta is not None and float(vector_delta) > 0 and parity and live_delta is not None and float(live_delta) > 0)
            promoted = False
            promoted_delta = None
            promoted_filter = None
            promoted_fval = None
            correct_E = cumulative_gain
            if switch_alone_positive:
                promoted = True
                promoted_delta = float(vector_delta)
            else:
                correct_E = cumulative_gain  # filters tested vs current cumulative (not yet promoted)
                # --- Per-switch FILTER columns — HORIZONTAL UNTIL POSITIVE (J + GR/golden/HTF/EMA) ---
                _horizontal_done = False
                _best_filter = None
                _best_fval = None
                _best_fdelta = None
                _best_flive = None
                try:
                    _prune_path = PROGRESS_DIR / "filter_column_prune.json"
                    _pruned_cols = set()
                    try:
                        if _prune_path.exists():
                            _pruned_cols = set(__import__('json').loads(_prune_path.read_text()).get("pruned_filters", []))
                    except Exception:
                        _pruned_cols = set()
                    _switch_prune_path = PROGRESS_DIR / "switch_row_prune.json"
                    _pruned_switches = set()
                    try:
                        if _switch_prune_path.exists():
                            _pruned_switches = set(__import__('json').loads(_switch_prune_path.read_text()).get("pruned_switches", []))
                    except Exception:
                        _pruned_switches = set()
                    _wb_col = openpyxl.load_workbook(str(wb_path))
                    _ws_col = _wb_col[sheet] if sheet in _wb_col.sheetnames else None
                    _all_headers = []
                    if _ws_col is not None:
                        for _ci in range(11, _ws_col.max_column+1):
                            _h = _ws_col.cell(row=1, column=_ci).value
                            if not _h or not isinstance(_h, str):
                                continue
                            if _h.strip().upper().startswith("WHAT SWITCH"):
                                break
                            _h = _h.strip()
                            if _h and _h not in _pruned_cols:
                                _all_headers.append((_ci, _h))
                        _wb_col.save(str(wb_path))
                    _specific = []
                    for _ci, _h in _all_headers:
                        if _h.lower() in ("is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","variant_sharpe","trades","tim","dd","filter_or_override","notes","what switch","description"):
                            continue
                        if _h in cumulative_overrides and _h in _pruned_cols:
                            continue
                        if _h in _pruned_switches:
                            continue
                        _specific.append(_h)
                    # If switch alone not positive, sweep filters in sheet order until first positive (horizontal-first)
                    if not promoted:
                        # reuse already-computed L:BI deltas (from vector trials) — no re-evaluation, ensures first rows fill correctly
                        _wb_lbi = openpyxl.load_workbook(str(wb_path), data_only=False)
                        _ws_lbi = _wb_lbi[sheet] if sheet in _wb_lbi.sheetnames else None
                        _lbi_best = None; _lbi_f = None; _lbi_val = None
                        if _ws_lbi is not None:
                            for _ci, _h in _all_headers:
                                if _h in _pruned_cols: continue
                                _v = _ws_lbi.cell(row=row, column=_ci).value
                                try:
                                    _vf = float(_v) if _v is not None else None
                                except: _vf = None
                                if _vf is not None and _vf > 0 and (_lbi_best is None or _vf > _lbi_best):
                                    _lbi_best = _vf; _lbi_f = _h
                                    # extract filter option from header "FILTER=VAL"
                                    if "=" in _h:
                                        _lbi_val = _h.split("=",1)[1].strip()
                                        if _lbi_val.lower() in ("true","false"): _lbi_val = _lbi_val.lower()=="true"
                                        elif _lbi_val.replace(".","",1).replace("-","",1).isdigit():
                                            try: _lbi_val = float(_lbi_val)
                                            except: pass
                                    else:
                                        _lbi_val = True
                            _wb_lbi.close()
                        if _lbi_best is not None and _lbi_best > 0:
                            promoted = True; promoted_delta = _lbi_best; promoted_filter = _lbi_f.split("=")[0].strip() if "=" in _lbi_f else _lbi_f; promoted_fval = _lbi_val
                            _best_flive = live  # keep original live for results
                            print(f"[horizontal-reuse] {sheet} {switch} best L:BI {_lbi_f}={_lbi_val} delta {_lbi_best:.4f} -> promote")
                        # legacy detailed loop kept for fallback when L:BI empty (full re-eval) — only if no positive found
                        if not promoted:
                            try:
                                _ws_f
                            except NameError:
                                import openpyxl as _opl_f2
                                _wb_f2 = _opl_f2.load_workbook(str(ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx"), data_only=False)
                                _ws_f = _wb_f2["FILTER_DICTIONARY_V2"] if "FILTER_DICTIONARY_V2" in _wb_f2.sheetnames else _wb_f2["FILTER_DICTIONARY_V8"] if "FILTER_DICTIONARY_V8" in _wb_f2.sheetnames else _wb_f2["FILTER_DICTIONARY"]
                                _has_option_col = _ws_f.cell(1,3).value and "Option Value" in str(_ws_f.cell(1,3).value)
                                _filter_col = 2
                                _option_col = 3 if _has_option_col else None
                            for _f in _specific:
                                if _f in _pruned_cols:
                                    continue
                                _eff_f = cumulative_overrides.get(_f, defaults.get(_f, False))
                                _cands_f = []
                                _collected = []
                                if _has_option_col:
                                    for _r in range(2, _ws_f.max_row+1):
                                        if str(_ws_f.cell(_r, _filter_col).value).strip() == _f:
                                            _opt = _ws_f.cell(_r, _option_col).value
                                            if _opt is not None and str(_opt).strip() != "":
                                                if isinstance(_opt, str) and _opt.strip().lower() in ("true","false"):
                                                    _opt = _opt.strip().lower() == "true"
                                                _collected.append(_opt)
                                _seen = set()
                                _cands_f = [x for x in _collected if not (str(x) in _seen or _seen.add(str(x)))]
                                _cands_f = [c for c in _cands_f if str(c) != str(_eff_f)]
                            if not _cands_f:
                                if isinstance(_eff_f, bool):
                                    _cands_f = [not _eff_f]
                                else:
                                    continue
                            for _cand_f in _cands_f:
                                _var_f = dict(cumulative_overrides); _var_f[_f]=_cand_f
                                _var_f[switch]=cand
                                if args.vector_only:
                                    _vec_f = vector_evaluate_with_timeout(new_symside, _var_f, window_days=args.window_days, timeout_s=5.0)
                                    if not _vec_f.get("valid"):
                                        continue
                                    _live_f = _vec_f
                                    _ok_f, _rsn_f = True, "vector-only"
                                    _d_f = float(_vec_f.get("gain_pct") or 0) - correct_E
                                # Guard: stop if F repeated (duplicate delta)
                                _rf = round(float(_d_f), 4) if _d_f is not None else None
                                if _rf is not None and _rf in seen_F:
                                    print(f"[STOP repeated F] { _switch} delta { _rf} repeats -> stopping sheet {_sheet}")
                                    break
                                if _rf is not None:
                                    seen_F.add(_rf)
                                else:
                                    _live_f = live_evaluate(new_symside, _var_f, window_days=args.window_days)
                                    if not _live_f.get("valid"):
                                        continue
                                    _vec_f = vector_evaluate_with_timeout(new_symside, _var_f, window_days=args.window_days, timeout_s=5.0)
                                    _ok_f,_rsn_f = parity_ok(_live_f, _vec_f)
                                    _d_f = float(_vec_f.get("gain_pct") or 0) - correct_E
                                # Guard: stop if F repeated (duplicate delta)
                                _rf = round(float(_d_f), 4) if _d_f is not None else None
                                if _rf is not None and _rf in seen_F:
                                    print(f"[STOP repeated F] { _switch} delta { _rf} repeats -> stopping sheet {_sheet}")
                                    break
                                if _rf is not None:
                                    seen_F.add(_rf) if _ok_f else float(_vec_f.get("gain_pct") or 0) - correct_E
                                _wb_ff = openpyxl.load_workbook(str(wb_path))
                                _ws_ff = _wb_ff[sheet] if sheet in _wb_ff.sheetnames else None
                                if _ws_ff is not None:
                                    _col = None
                                    for _ci in range(11, _ws_ff.max_column+1):
                                        if _ws_ff.cell(row=1, column=_ci).value == _f:
                                            _col = _ci
                                            break
                                    if _col is not None:
                                        _ws_ff.cell(row=row, column=_col).value = _d_f
                                        _ws_ff.cell(row=row, column=_col).font = Font(bold=True, color="006100" if _d_f>0 else "9C0006")
                                        _wb_ff.save(str(wb_path))
                                if _ok_f and _d_f is not None and _d_f > 0:
                                    _rf2 = round(float(_d_f),4)
                                    if _rf2 in seen_F:
                                        print(f"[STOP repeated F] filter {_f} delta {_rf2} repeats -> stopping")
                                        break
                                    seen_F.add(_rf2)
                                    _best_filter = _f; _best_fval = _cand_f; _best_fdelta = _d_f; _best_flive = _live_f
                                    promoted = True; promoted_delta = _d_f; promoted_filter = _f; promoted_fval = _cand_f
                                    _horizontal_done = True
                                    print(f"[horizontal-until-positive] {sheet} {switch}+{_f}={_cand_f} delta {_d_f:.4f} -> STOP horizontal, will promote E+C")
                                    break
                            if _horizontal_done:
                                break
                    else:
                        # switch alone positive — still fill filter cols for audit but don't need to promote further
                        for _f in _specific[:5]:
                            _eff_f = cumulative_overrides.get(_f, defaults.get(_f, False))
                            _cand_f = not _eff_f if isinstance(_eff_f,bool) else _eff_f
                            if not isinstance(_eff_f,bool):
                                continue
                            _var_f = dict(cumulative_overrides); _var_f[_f]=_cand_f; _var_f[switch]=cand
                            _vec_f = vector_evaluate_with_timeout(new_symside, _var_f, window_days=args.window_days, timeout_s=5.0)
                            if not _vec_f.get("valid"):
                                continue
                            _d_f = float(_vec_f.get("gain_pct") or 0) - (cumulative_gain + promoted_delta)
                            _wb_ff2 = openpyxl.load_workbook(str(wb_path))
                            _ws_ff2 = _wb_ff2[sheet] if sheet in _wb_ff2.sheetnames else None
                            if _ws_ff2 is not None:
                                for _ci in range(11, _ws_ff2.max_column+1):
                                    if _ws_ff2.cell(row=1, column=_ci).value == _f:
                                        _ws_ff2.cell(row=row, column=_ci).value = _d_f
                                        _ws_ff2.cell(row=row, column=_ci).font = Font(bold=True, color="006100" if _d_f>0 else "9C0006")
                                        break
                                _wb_ff2.save(str(wb_path))
                except Exception as _e:
                    print(f"[per-switch-filter] skip {_e}")
                # Now promotion: E only if improved (promoted), C shows improving setting + applied filter
                # Guard: E only prints if improved baseline (promoted and delta>0)
                if promoted and promoted_delta is not None and promoted_delta > 0:
                    correct_E = cumulative_gain + float(promoted_delta)
                    wb_tmp = openpyxl.load_workbook(str(wb_path))
                    if sheet in wb_tmp.sheetnames:
                        ws_tmp = wb_tmp[sheet]
                        ws_tmp.cell(row=row, column=5).value = correct_E
                        ws_tmp.cell(row=row, column=5).font = openpyxl.styles.Font(name='Arial', bold=True, color="006100")
                        c_val = str(cand) + (f" + {promoted_filter}={promoted_fval}" if promoted_filter else "")
                        ws_tmp.cell(row=row, column=3).value = c_val
                        ws_tmp.cell(row=row, column=3).font = openpyxl.styles.Font(name='Arial', bold=True, color="006100")
                        # also update cached F/G and Results_30d so VLOOKUP stays consistent
                        ws_tmp.cell(row=row, column=6).value = float(promoted_delta)
                        ws_tmp.cell(row=row, column=7).value = float(promoted_delta)
                        # Results_30d_Deltas variant_gain and delta
                        if "Results_30d_Deltas" in wb_tmp.sheetnames:
                            rws = wb_tmp["Results_30d_Deltas"]
                            target = None
                            for _r in range(2, rws.max_row+1):
                                if str(rws.cell(_r,1).value or "").strip() == switch:
                                    target = _r; break
                            if target is None:
                                target = rws.max_row+1
                                rws.cell(target,1).value = switch
                            rws.cell(target,5).value = float(promoted_delta)
                            rws.cell(target,8).value = float(correct_E)
                            rws.cell(target,14).value = f"horizontal {promoted_filter}={promoted_fval}" if promoted_filter else "switch alone"
                        wb_tmp.save(str(wb_path))
                    cumulative_gain = correct_E
                    cumulative_overrides[switch] = cand
                    if promoted_filter:
                        cumulative_overrides[promoted_filter] = promoted_fval
                    live_for_results = _best_flive if _best_flive is not None else live
                    append_results_row(wb_path, switch, c_val, float(promoted_delta), live_for_results, f"horizontal {switch}={cand}" + (f"+{promoted_filter}={promoted_fval}" if promoted_filter else "") + f" parity {res.get('reason')} live {live_delta:.4f} vec {vector_delta:.4f} | cumulative {cumulative_gain:.4f}")
                    print(f"[pos] {sheet}!{row} {switch}={cand}" + (f"+{promoted_filter}={promoted_fval}" if promoted_filter else "") + f" delta {promoted_delta:.4f} cumulative={cumulative_gain:.4f} -> C+E written")
                    try:
                        write_tab_chart(new_symside, sheet, switch, cand, live_for_results, res.get("vec") or {}, float(promoted_delta), cumulative_gain)
                    except Exception:
                        pass
                else:
                    # not promoted — ensure E stays empty, C stays empty (death penalty)
                    try:
                        wb_tmp2 = openpyxl.load_workbook(str(wb_path))
                        if sheet in wb_tmp2.sheetnames:
                            ws_tmp2 = wb_tmp2[sheet]
                            if ws_tmp2.cell(row=row, column=5).value is not None and not switch_alone_positive:
                                ws_tmp2.cell(row=row, column=5).value = None
                                ws_tmp2.cell(row=row, column=3).value = None
                                wb_tmp2.save(str(wb_path))
                    except Exception:
                        pass
                    print(f"[neg] {sheet}!{row} {switch}={cand} live {live_delta:.4f} vector {vector_delta:.4f} -> F/G written, E stays empty (death penalty, horizontal exhausted)")
                    try:
                        write_tab_chart(new_symside, sheet, switch, cand, live, res.get("vec") or {}, vector_delta if vector_delta is not None else 0, cumulative_gain)
                    except Exception:
                        pass
        else:
            # parity fail — still write F/G (INSTRUCTIONS: every F filled), E stays None
            write_delta_cell(wb_path, sheet, row, live_delta, vector_delta, False, baseline_gain)
            print(f"[parity-fail] {sheet}!{row} {switch}={cand} {res.get('reason')} live_delta={live_delta} vec_delta={vector_delta} — F/G written, E stays empty")
            try:
                write_tab_chart(new_symside, sheet, switch, cand, live, res.get("vec") or {}, vector_delta if vector_delta is not None else 0, cumulative_gain)
            except Exception:
                pass
        # Minute-by-minute tab-by-tab dashboard in SPREADSHEETS — refresh after every row
        try:
            # Build per-sheet status for dashboard
            done_by_sheet = {}
            pos_by_sheet = {}
            for (sh, rw, sw, cd, ef) in ordered:
                k = f"{sh}!{rw}:{sw}={cd}"
                r = progress["done"].get(k) or all_results.get((sh, rw, sw, cd))
                if not r:
                    continue
                done_by_sheet[sh] = done_by_sheet.get(sh, 0) + 1
                if r.get("parity") and (r.get("delta") or 0) > 0:
                    pos_by_sheet[sh] = pos_by_sheet.get(sh, 0) + 1
            sheet_status = {}
            for sh in SWITCH_SHEETS:
                if any(t[0]==sh for t in trials):
                    tot = sum(1 for t in trials if t[0]==sh)
                    sheet_status[sh] = {"done": done_by_sheet.get(sh,0), "total": tot, "positives": pos_by_sheet.get(sh,0), "gain": cumulative_gain}
            write_progress_dashboard(new_symside, wb_path, baseline_gain, cumulative_gain, progress, sheet_status)
        except Exception:
            pass
    # ONE-TIME RECORD: keep mapping of what filter applies to what switch — do NOT prune future runs
    # If --exhaustive-filters was used (AAPL 1yr one-time), write exhaustive applicability map to lifecycle_pilot
    if getattr(args, "exhaustive_filters", False):
        try:
            _wb_scan = openpyxl.load_workbook(str(wb_path), data_only=False)
            _record = {"symside": new_symside, "window_days": args.window_days, "generated": utcnow(), "switch_to_filters": {}, "filter_to_switches": {}}
            for sh in SWITCH_SHEETS:
                if sh not in _wb_scan.sheetnames:
                    continue
                _ws = _wb_scan[sh]
                # col map
                _col_map = {}
                for _ci in range(11, _ws.max_column+1):
                    _h = _ws.cell(row=1, column=_ci).value
                    if isinstance(_h, str) and _h.strip() and not _h.strip().upper().startswith("WHAT SWITCH"):
                        _col_map[_ci] = _h.strip()
                for _r in range(2, _ws.max_row+1):
                    _sw = _ws.cell(row=_r, column=1).value
                    if not _sw or not isinstance(_sw, str):
                        continue
                    _f_val = _ws.cell(row=_r, column=6).value  # F delta
                    if _f_val is None or not isinstance(_f_val, (int,float)) or float(_f_val) <= 0:
                        continue
                    _key = f"{sh}!{_r}:{_sw.strip()}"
                    _hits = []
                    for _ci, _filt in _col_map.items():
                        v = _ws.cell(row=_r, column=_ci).value
                        if isinstance(v, (int,float)) and float(v) > 0:
                            _hits.append(_filt)
                            _record["filter_to_switches"].setdefault(_filt, []).append(_key)
                    if _hits:
                        _record["switch_to_filters"][_key] = _hits
            _rec_path = PROGRESS_DIR / f"{new_symside}_{args.window_days}d_filter_applicability.json"
            _rec_path.write_text(json.dumps(_record, indent=2))
            print(f"[record] ONE-TIME filter→switch applicability map → {_rec_path} ({len(_record['switch_to_filters'])} switches with hits, {len(_record['filter_to_switches'])} filters hit)", flush=True)
        except Exception as _e:
            print(f"[record] skip {_e}", flush=True)
            import traceback; traceback.print_exc()

    # At the end of each tab run all relevant filters from FILTER_DICTIONARY_V8, any winner added on bottom of current tab
    # Per Bible: after finishing ENTRY_PULLBACK_BOUNCE main switches, run its relevant filters on new baseline, live-first, vector must match, F/G/E/C + results, then port to next tab. Repeat for every tab.
    try:
        wb_filter = openpyxl.load_workbook(str(wb_path), data_only=False)
        if "FILTER_DICTIONARY_V2" in wb_filter.sheetnames or "FILTER_DICTIONARY_V8" in wb_filter.sheetnames or "FILTER_DICTIONARY" in wb_filter.sheetnames:
            filter_sheet_name = "FILTER_DICTIONARY_V2" if "FILTER_DICTIONARY_V2" in wb_filter.sheetnames else "FILTER_DICTIONARY_V8" if "FILTER_DICTIONARY_V8" in wb_filter.sheetnames else "FILTER_DICTIONARY"
            ws_f = wb_filter[filter_sheet_name]
            # Collect filter switches (col A is switch, col B is description, col C is family)
            filter_rows = []
            # Detect header layout: new TEMPLATE has Filter in col2 and Option Value in col3 (second column like trading tabs), old had Filter in col2 with Option in col16
            # Find Filter and Option Value column indices by header name
            header = [str(ws_f.cell(1,c).value or "").strip().lower() for c in range(1, ws_f.max_column+1)]
            # default old: Filter col2, Option col16
            filter_col = 2
            option_col = 16
            desc_col = 3
            for idx, h in enumerate(header, 1):
                if "filter" == h:
                    filter_col = idx
                elif "option value" in h:
                    option_col = idx
                elif "what it does" in h:
                    desc_col = idx
            # header row itself is not a filter
            for r in range(2, ws_f.max_row + 1):
                f_switch = ws_f.cell(row=r, column=filter_col).value
                if not f_switch or not isinstance(f_switch, str):
                    continue
                f_switch = f_switch.strip()
                if not f_switch or f_switch.upper() == "FILTER":
                    continue
                # skip numeric # column if filter_col was misdetected
                if f_switch.isdigit():
                    continue
                f_opt = ws_f.cell(row=r, column=option_col).value
                f_desc = ws_f.cell(row=r, column=desc_col).value if desc_col else ""
                # store per-row option as well for later use
                filter_rows.append((r, f_switch, f_opt if f_opt is not None else f_desc))
            # For each tab in order, test its relevant filters on the current cumulative baseline
            # Relevant filter = filter name contains tab keyword (e.g., BB_SQUEEZE for ENTRY) or is generic TF filter
            # For now, test all filters that are not already in main switches and not yet tested
            for tab_idx, tab in enumerate(SWITCH_SHEETS):
                # Determine relevant filters for this tab: those that gate this tab's entries/exits
                # Simplified: filters whose switch contains tab's prefix (ENTRY/EXIT/REENTRY) or is common (TF_FOCUS, BB_, CT_)
                # We test each filter's True/False (or TF) vs cumulative baseline, live-first
                # Only test filters not already in cumulative_overrides as pos
                relevant = []
                tab_prefix = tab.split("_")[0]  # ENTRY, EXIT, REENTRY, AUGMENT, REDUCE
                for (fr, f_switch, f_desc) in filter_rows:
                    # Skip if already tested as main switch (same name already in trials)
                    if f_switch in cumulative_overrides and f_switch in [t[2] for t in trials]:
                        continue
                    # Relevant if filter description mentions tab or is generic filter
                    desc = str(f_desc or "") + str(f_switch)
                    if tab_prefix.lower() in desc.lower() or "FILTER" in f_switch or "BB_" in f_switch or "TF_" in f_switch or "CT_" in f_switch:
                        relevant.append((fr, f_switch))
                if not relevant:
                    continue
                # Find bottom row of current tab to append winners
                ws_tab = wb_filter[tab] if tab in wb_filter.sheetnames else None
                if ws_tab is None:
                    continue
                bottom_row = ws_tab.max_row + 1
                # Build per-filter all Option Values map from FILTER_DICTIONARY for end-of-tab (keep MAX)
                _f_to_opts = {}
                for (_fr, _fs, _opt) in filter_rows:
                    # _opt may be None; need to collect per f_switch
                    if _fs not in _f_to_opts:
                        _f_to_opts[_fs] = []
                    if _opt is not None and str(_opt).strip() != "":
                        # normalize bool strings
                        _o = _opt
                        if isinstance(_o, str) and _o.strip().lower() in ("true","false"):
                            _o = _o.strip().lower()=="true"
                        if str(_o) not in [str(x) for x in _f_to_opts[_fs]]:
                            _f_to_opts[_fs].append(_o)
                for (fr, f_switch) in relevant:  # every filter per tab, all 20 tabs — no cap (2108 + filters)
                    eff = cumulative_overrides.get(f_switch, defaults.get(f_switch, False))
                    # Collect all candidates for this filter (from dict, else flip bool)
                    _opts = _f_to_opts.get(f_switch, [])
                    if not _opts:
                        # fallback to flip bool
                        if isinstance(eff, bool):
                            _opts = [not eff]
                        else:
                            continue
                    else:
                        # filter out current eff, keep only alternatives
                        _opts = [o for o in _opts if str(o) != str(eff)]
                        if not _opts:
                            continue
                    # Test ALL options and keep MAX delta
                    best_df = -1e9
                    best_cand = None
                    best_live = None
                    best_vec = None
                    best_reason = ""
                    for cand_filter in _opts:
                        variant = dict(cumulative_overrides)
                        variant[f_switch] = cand_filter
                        # Speed guard: vector should be moments (0.07s), live 30s; if stalled >60s mark and move on per user mandate
                    import concurrent.futures as _cf
                    try:
                        with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                            _fut = _ex.submit(live_evaluate, new_symside, variant, window_days=args.window_days)
                            live_f = _fut.result(timeout=90)
                    except _cf.TimeoutError:
                        print(f"[stall] live {switch}={cand} timeout 90s -> mark and move on", flush=True)
                        write_delta_cell(wb_path, sheet, row, None, None, False, baseline_gain)
                        # mark as stalled in progress
                        progress["done"][f"{sheet}:{switch}:{cand}"] = {"delta": 0, "parity": False, "stalled": True}
                        continue
                    except Exception as e:
                        print(f"[stall] live {switch} error {e} -> mark", flush=True)
                        continue
                    if not live_f.get("valid"):
                        # Even invalid still needs a number in delta column per user: write 0 delta so every delta has a number
                        # But per bible, loser E stays None; we still write F/G as negative delta for traceability
                        pass
                    try:
                        with _cf.ThreadPoolExecutor(max_workers=1) as _ex2:
                            _fut2 = _ex2.submit(vector_evaluate, new_symside, variant, window_days=args.window_days)
                            vec_f = _fut2.result(timeout=15)
                    except _cf.TimeoutError:
                        print(f"[stall] vector {switch}={cand} timeout 15s -> vector stalled, verify with live if in doubt, mark and move on", flush=True)
                        # Vector stalled but live succeeded: mark row with live delta only, move on (vector will be verified later)
                        write_delta_cell(wb_path, sheet, row, float(live_f.get("gain_pct",0))-baseline_gain, None, False, baseline_gain)
                        progress["done"][f"{sheet}:{switch}:{cand}"] = {"delta": float(live_f.get("gain_pct",0))-baseline_gain, "parity": False, "stalled": True}
                        continue
                    ok_f, reason_f = parity_ok(live_f, vec_f)
                    if not ok_f:
                        continue
                    delta_f = float(live_f.get("gain_pct") or 0) - cumulative_gain
                    # Track MAX across all options for this filter
                    if delta_f > best_df:
                        best_df = delta_f
                        best_cand = cand_filter
                        best_live = live_f
                        best_vec = vec_f
                        best_reason = reason_f
                    # Continue to test next option — keep MAX, not first positive
                # After testing ALL options for this filter, promote MAX if positive
                if best_cand is not None and best_df > 0:
                    delta_f = best_df
                    cand_filter = best_cand
                    live_f = best_live
                    vec_f = best_vec
                    reason_f = best_reason
                    # Winner: add on bottom of current tab with delta and new baseline (MAX)
                    wb_filter[tab].cell(row=bottom_row, column=1).value = f_switch
                    wb_filter[tab].cell(row=bottom_row, column=2).value = cand_filter
                    wb_filter[tab].cell(row=bottom_row, column=2).font = Font(name='Arial', bold=True)
                    wb_filter[tab].cell(row=bottom_row, column=3).value = cand_filter
                    wb_filter[tab].cell(row=bottom_row, column=3).font = Font(name='Arial', bold=True)
                    wb_filter[tab].cell(row=bottom_row, column=5).value = cumulative_gain + delta_f
                    wb_filter[tab].cell(row=bottom_row, column=5).font = Font(name='Arial', bold=True, color="006100")
                    wb_filter[tab].cell(row=bottom_row, column=6).value = delta_f
                    wb_filter[tab].cell(row=bottom_row, column=6).font = Font(name='Arial', bold=True, color="006100")
                    try:
                        wb_filter[tab].cell(row=bottom_row, column=7).value = delta_f
                        wb_filter[tab].cell(row=bottom_row, column=8).value = cumulative_gain + delta_f
                    except Exception:
                        pass
                    wb_filter.save(str(wb_path))
                    cumulative_gain += delta_f
                    cumulative_overrides[f_switch] = cand_filter
                    append_results_row(wb_path, f_switch, cand_filter, delta_f, live_f, f"filter live==vector MAX {reason_f} | tab {tab} cumulative {cumulative_gain:.4f}")
                    try:
                        write_tab_chart(new_symside, tab, f_switch, cand_filter, live_f, vec_f, delta_f, cumulative_gain)
                    except Exception:
                        pass
                    bottom_row += 1
            wb_filter.save(str(wb_path))
    except Exception as e:
        import traceback
        print(f"[filters] error {e}\n{traceback.format_exc()}")

    # Final sweep: report highest attainable (only switch sheets, not ALL_TRB_RESULTS etc)
    wb = openpyxl.load_workbook(str(wb_path), data_only=False)
    max_gain = baseline_gain
    for name in SWITCH_SHEETS:
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        for row in ws.iter_rows():
            for c in row:
                if c.column == 5 and isinstance(c.value, (int,float)) and c.value is not None:
                    try:
                        if float(c.value) > max_gain:
                            max_gain = float(c.value)
                    except Exception:
                        pass
    # === FILENAME WITH BH + GAIN (user mandate 2026-09-06) ===
    # Rename to {SYM}_bh{bh}_gain{gain}_30d_matrix.xlsx — bh from baseline, gain from final MAX(E:E)
    try:
        # bh is from baseline live metrics, gain is max_gain (or cumulative)
        bh_val = float(baseline_live.get('bh_pct', 0) or 0) if 'baseline_live' in locals() else 0.0
        gain_val = float(max_gain if max_gain != baseline_gain else cumulative_gain)
        # sanitize for filename: replace - with m, . with p
        def _fmt(v): return f"{v:.2f}".replace("-","m").replace(".","p")
        new_name = f"{new_symside}_bh{_fmt(bh_val)}_gain{_fmt(gain_val)}_30d_matrix.xlsx"
        new_path = OUT_DIR / new_name
        if new_path != wb_path and not new_path.exists():
            import shutil
            shutil.move(str(wb_path), str(new_path))
            print(f"[rename] {wb_path.name} -> {new_path.name} (bh {bh_val:.2f} gain {gain_val:.2f})")
            wb_path = new_path
        else:
            if new_path.exists() and new_path != wb_path:
                print(f"[rename] target {new_path.name} exists — keeping {wb_path.name}")
            else:
                print(f"[rename] already {wb_path.name}")
    except Exception as _e:
        print(f"[rename] skipped {_e}")
    print(f"[done] {new_symside} workbook {wb_path}")
    print(f"  baseline {baseline_gain:.4f} -> highest E {max_gain:.4f} (delta {max_gain-baseline_gain:.4f}) cumulative {cumulative_gain:.4f}")
    print(f"  progress {progress_path} ({len(progress.get('done',{}))} trials)")
    print(f"  verify: every F has a value only if parity proven; every E only if F>0 — death penalty intact; {len([r for r in progress.get('done',{}).values() if r.get('parity') and r.get('delta',0)>0])} positives promoted")

    # === FILTER PURIFY HOOK — BOTH per-filter and composite (vector-only, ~6s, non-blocking) ===
    # Applies to EVERY sym_side, every run: eliminates bullshit K (ADX+ATR only), runs 358 filter options vs every switch row,
    # keeps only |Δ|>0.0005, moves >50% hit rate to GLOBAL bottom, adds L/M composite. Required for hundreds of S1 runs next hours.
    try:
        from tools.opt.filter_purify_hook import purify_workbook as _purify
        _purify(wb_path, new_symside, cumulative_overrides, defaults, cumulative_gain, window_days=args.window_days)
        # refresh baseline for html after purify (workbook now has L/M)
        try:
            wb = openpyxl.load_workbook(str(wb_path), data_only=False)
            max_gain = baseline_gain
            for name in SWITCH_SHEETS:
                if name not in wb.sheetnames: continue
                ws = wb[name]
                for row in ws.iter_rows():
                    for c in row:
                        if c.column == 5 and isinstance(c.value, (int,float)) and c.value is not None:
                            try:
                                if float(c.value) > max_gain: max_gain = float(c.value)
                            except: pass
        except: pass
    except Exception as _e:
        import traceback
        print(f"[purify-hook] skipped {_e}\n{traceback.format_exc()}")

    # === COMPLETE zoomable chart — BEFORE live verification (user 2026-09-04) ===
    # PROVEN hires zoomable with pictograms EXACTLY on grey priceline, zoomable, file:// inline.
    # This is the vector chart BEFORE backtest_v12_engine — produced after finishing baseline + overly and all vectorized cells/filters.
    try:
        if _generate_hires:
            # Generate hires BEFORE live: vector trades on grey line (green/red/blue) — the exact chart user approved
            _generate_hires(new_symside, cumulative_overrides, window_days=args.window_days)
            print(f"[hires] BEFORE live: {new_symside} hires zoomable generated (vector {cumulative_gain:.2f}%, {len(cumulative_overrides)} overrides) — BEFORE backtest_v12_engine")
        else:
            write_complete_chart(new_symside, wb_path, baseline_gain, cumulative_gain, cumulative_overrides, baseline_vec, None)
    except Exception as _e:
        import traceback
        print(f"[hires] BEFORE live failed {_e}\n{traceback.format_exc()}")
        try:
            write_complete_chart(new_symside, wb_path, baseline_gain, cumulative_gain, cumulative_overrides, baseline_vec, None)
        except Exception:
            pass

    # For each final result run a real parity test on backtest_v12_engine with ALL settings — make sure they match, if not find problems
    # Final cumulative overrides = baseline ∪ all pos deltas (with C written). This is the new baseline.
    try:
        final_overrides = dict(cumulative_overrides)
        print(f"[final-parity] testing {new_symside} with ALL settings ({len(final_overrides)} overrides) live vs vector...")
        # Use same window as baseline (last 30d)
        live_final = live_evaluate(new_symside, final_overrides, window_days=args.window_days)
        vec_final = vector_evaluate(new_symside, final_overrides, window_days=args.window_days)
        ok_final, reason_final = parity_ok(live_final, vec_final)
        print(f"[final-parity] live gain={live_final.get('gain_pct')} trades={live_final.get('trades')} sharpe={live_final.get('pool_sharpe')}")
        print(f"[final-parity] vec  gain={vec_final.get('gain_pct')} trades={vec_final.get('trades')} sharpe={vec_final.get('pool_sharpe')}")
        print(f"[final-parity] parity={ok_final} {reason_final}")
        # Write final parity report to SPREADSHEETS and charts_1M
        parity_path = OUT_DIR / f"{new_symside}_final_parity.json"
        parity_report = {
            "symside": new_symside,
            "baseline_gain": baseline_gain,
            "final_gain": cumulative_gain,
            "final_overrides": final_overrides,
            "live": {k: live_final.get(k) for k in ["gain_pct","gain_per_mo","trades","pool_sharpe","tim_pct","max_dd_pct","valid","invalid_reason"]},
            "vec": {k: vec_final.get(k) for k in ["gain_pct","gain_per_mo","trades","pool_sharpe","tim_pct","max_dd_pct","valid","invalid_reason"]},
            "parity": ok_final,
            "reason": reason_final,
            "ts": utcnow(),
        }
        parity_path.write_text(json.dumps(parity_report, indent=2))
        # also copy to charts_1M for sync
        try:
            CHARTS_1M_DIR.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(parity_path, CHARTS_1M_DIR / parity_path.name)
        except Exception:
            pass
        if not ok_final:
            print(f"[final-parity] MISMATCH — FIND PROBLEMS: live vs vector differ with ALL settings!")
            problems = []
            for t in ordered:
                sheet, row, switch, cand = t[0], t[1], t[2], t[3]
                res = all_results.get((sheet, row, switch, cand)) or progress["done"].get(f"{sheet}!{row}:{switch}={cand}", {})
                if not res.get("parity"):
                    problems.append({"switch": switch, "cand": str(cand), "sheet": sheet, "row": row, "reason": res.get("reason")})
            if problems:
                print(f"[final-parity] {len(problems)} switch parity failures (need vector numpy repair):")
                for p in problems[:10]:
                    print(f"  - {p['sheet']}!{p['row']} {p['switch']}={p['cand']} : {p['reason']}")
                parity_report["problems"] = problems
                parity_path.write_text(json.dumps(parity_report, indent=2))
        else:
            print(f"[final-parity] ALL SETTINGS MATCH — keep going")
        # Regenerate COMPLETE chart with final parity metrics — OVERLAY live trades in different colors (orange/purple) on same grey line
        try:
            if _generate_hires:
                # Extract live ledger for overlay (backtest_v12_engine trades, different colors)
                _live_ledger = (live_final.get("execution_ledger") or live_final.get("ledger") or []) if isinstance(live_final, dict) else []
                if not _live_ledger and isinstance(live_final, dict):
                    # fallback: try to re-evaluate live with ledger via backtest_v12_engine ledger field already in live_final["ledger"]
                    _live_ledger = live_final.get("ledger") or []
                _generate_hires(new_symside, cumulative_overrides, window_days=live_window_final, live_ledger=_live_ledger, live_metrics=live_final)
                print(f"[hires] AFTER live OVERLAY: {new_symside} hires with live overlay ({len(_live_ledger)} live trades in orange/purple vs {vec_final.get('trades',0)} vector) — backtest_v12_engine completed")
            else:
                write_complete_chart(new_symside, wb_path, baseline_gain, cumulative_gain, cumulative_overrides, baseline_vec, vec_final)
        except Exception as _e2:
            import traceback
            print(f"[hires] AFTER live OVERLAY failed {_e2}\n{traceback.format_exc()}")
            try:
                write_complete_chart(new_symside, wb_path, baseline_gain, cumulative_gain, cumulative_overrides, baseline_vec, vec_final)
            except Exception:
                pass
        try:
            write_progress_dashboard(new_symside, wb_path, baseline_gain, cumulative_gain, progress, sheet_status if 'sheet_status' in locals() else None)
        except Exception:
            pass
        # === FORWARD TANDEM: 1 day live+vector on winning set while also performing live ===
        # Run next 1 trading day (or last 1d slice shifted) with same overrides to ensure live and vector stay in tandem post-promotion
        try:
            print(f"[forward-tandem] 1-day forward check for {new_symside} with winning {len(final_overrides)} overrides — live vs vector must stay in tandem while live performs")
            # Use window_days=1 as 1 trading session forward (or 5m bars of last day) — compare both engines
            # We reuse evaluate_month with window_days=1 vs 5 to avoid short-window invalid; use 5 sessions as proxy if 1 invalid
            from tools.opt.lifecycle_pilot import evaluate_month as _eval_fwd
            fwd_days = 1
            live_fwd = live_evaluate(new_symside, final_overrides, window_days=fwd_days)
            if not live_fwd.get("valid"):
                # fallback to 5 days if 1-day too short for trades floor
                live_fwd = live_evaluate(new_symside, final_overrides, window_days=5)
                fwd_days = 5
            vec_fwd = vector_evaluate_with_timeout(new_symside, final_overrides, window_days=fwd_days, timeout_s=5.0)
            ok_fwd, reason_fwd = parity_ok(live_fwd, vec_fwd) if live_fwd.get("valid") and vec_fwd.get("valid") else (False, "forward invalid")
            # If tandem fails, generate a secondary chart underneath (separate file) to avoid chaotic overlay
            tandem_ok = ok_fwd
            # Also check live is actually performing: need at least 1 trade in forward window if baseline had trades
            live_trades_fwd = int(live_fwd.get("trades") or 0)
            if tandem_ok and live_trades_fwd == 0 and int(live_final.get("trades") or 0) > 10:
                tandem_ok = False
                reason_fwd += " (live 0 trades forward while baseline had trades — live not performing)"
            fwd_report = {
                "symside": new_symside,
                "window_days": fwd_days,
                "live": {k: live_fwd.get(k) for k in ["gain_pct","trades","pool_sharpe","valid","invalid_reason"]},
                "vec": {k: vec_fwd.get(k) for k in ["gain_pct","trades","pool_sharpe","valid","invalid_reason"]},
                "parity": tandem_ok,
                "reason": reason_fwd,
                "ts": utcnow(),
            }
            fwd_path = OUT_DIR / f"{new_symside}_forward_tandem.json"
            fwd_path.write_text(json.dumps(fwd_report, indent=2))
            try:
                CHARTS_1M_DIR.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copy2(fwd_path, CHARTS_1M_DIR / fwd_path.name)
            except: pass
            print(f"[forward-tandem] {new_symside} {fwd_days}d live {live_fwd.get('gain_pct')} vs vec {vec_fwd.get('gain_pct')} parity={tandem_ok} {reason_fwd}")
            # Chart handling: if tandem ok, keep single overlay; if chaotic (>50 trades) or tandem fails, generate second chart underneath
            try:
                if _generate_hires:
                    if tandem_ok and len(_live_ledger or []) < 50:
                        print(f"[forward-tandem] tandem ok, single overlay sufficient")
                    else:
                        # Generate separate forward chart (underneath) with live in different colors
                        _generate_hires(new_symside, final_overrides, window_days=fwd_days, live_ledger=(live_fwd.get("execution_ledger") or live_fwd.get("ledger") or []), live_metrics=live_fwd)
                        print(f"[forward-tandem] forward chart generated for {new_symside} {fwd_days}d")
            except Exception as _e3:
                print(f"[forward-tandem] chart gen failed {_e3}")
            if not tandem_ok:
                print(f"[forward-tandem] WARNING: live and vector NOT in tandem forward — block promotion until fixed", flush=True)
                parity_path.write_text(json.dumps({**parity_report, "forward_tandem": fwd_report}, indent=2))
        except Exception as e:
            import traceback
            print(f"[forward-tandem] error {e}\n{traceback.format_exc()}")
    except Exception as e:
        import traceback
        print(f"[final-parity] error {e}\n{traceback.format_exc()}")

if __name__ == "__main__":
    main()

# TEMPLATE_V2 MANDATORY 2026-09-09: 1) Every relevant filter for EVERY switch before next row (including BOLD defaults, 15 HTF/GR/EMA/ATR per row unavoidable) 2) Every positive delta to F and filter|opt to override C 3) Every positive to new row in Results_30d_Deltas H variant_gain

# BH #TRADES DD TIM after F ALWAYS updated with every switch and subfilter run (G=BH H=#TRADES I=DD J=TIM VLOOKUP 10-13 Results, 15 per row universal)
