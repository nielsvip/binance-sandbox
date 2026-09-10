#!/usr/bin/env python3
"""v12 pilot sheet runner — TOTAL REWRITE 2026-09-09 per user + INSTRUCTIONS sheet law.

Bible §4.3.1 + TEMPLATE INSTRUCTIONS (11 steps) are LAW — this sheet wins after 2026-09-07.
- v12_quick_engine generates deltas for EVERY cell: F = vector_gain - current_baseline (VLOOKUP)
- E = IF(F>0,Eprev+F,Eprev) chain NEVER overwritten by python — only Results_30d_Deltas col H variant_gain
- Tabs processed SEQUENTIALLY ENTRY_REVERSAL_BOUNCE → GLOBAL_RISK_GATES, each tab finished before next
- Per-row L:BI via FILTER_DICTIONARY_V2 token-overlap, GENERAL blanket rows at bottom vs final baseline
- C/E update ONLY after parity (trades 0.80..1.25, gain 0.5pp|15%, sharpe 0.25) and F>0
- No ThreadPool nesting, no 5s VECTOR_STALL fake, no universal 13k expansion, no E/F/G formula overwrite
- Mac NEVER runs pilots (S1 only, 30d of NPZ, 15m causal)
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

try:
    from tools.opt.hires_chart import generate_hires as _generate_hires
except Exception:
    _generate_hires = None

TEMPLATE = ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx"
TEMPLATE_FALLBACK = ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx"
OUT_DIR = ROOT / "SPREADSHEETS"
PROGRESS_DIR = ROOT / "data" / "reports" / "lifecycle_pilot"
CAMPAIGN = PROGRESS_DIR / "campaign_order_1mo.json"
PARITY_CONTRACT = PROGRESS_DIR / "per_sym_parity_contract.json"
CHARTS_1M_DIR = ROOT / "data" / "reports" / "charts_1M"

# V2 12 IDEA-grouped sheets (canonical after 2026-09-09). Old 20 still supported if template is TEMPLATE.xlsx
SWITCH_SHEETS_V2 = [
    "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY",
    "REENTRY_WINDOWED", "REENTRY_ADAPTIVE",
    "AUGMENT_TREND", "AUGMENT_RISK_SIZING",
    "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER",
    "GLOBAL_RISK_GATES",
]
SWITCH_SHEETS_LEGACY = [
    "ENTRY_PULLBACK_BOUNCE", "ENTRY_BREAKOUT", "ENTRY_NEUTRAL", "ENTRY_FULL_FILTERED",
    "EXIT_PULLBACK_BOUNCE", "EXIT_BREAKOUT", "EXIT_NEUTRAL", "EXIT_FULL_FILTERED",
    "REENTRY_PULLBACK_BOUNCE", "REENTRY_BREAKOUT", "REENTRY_NEUTRAL", "REENTRY_FULL_FILTERED",
    "AUGMENT_PULLBACK_BOUNCE", "AUGMENT_BREAKOUT", "AUGMENT_NEUTRAL", "AUGMENT_FULL_FILTERED",
    "REDUCE_PULLBACK_BOUNCE", "REDUCE_BREAKOUT", "REDUCE_NEUTRAL", "REDUCE_FULL_FILTERED",
]
# Use V2 order if template is V2, else legacy
SWITCH_SHEETS = SWITCH_SHEETS_V2

_FILTER_DICT_CACHE = None

def _load_filter_dictionary() -> list[dict]:
    global _FILTER_DICT_CACHE
    if _FILTER_DICT_CACHE is not None:
        return _FILTER_DICT_CACHE
    candidates = [
        ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx",
        ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx",
        Path("/tmp/TEMPLATE_V2_MIGRATED.xlsx"),
    ]
    ws = None
    used = None
    for p in candidates:
        if p.exists():
            try:
                wb = openpyxl.load_workbook(str(p), data_only=True)
                for cand in ["FILTER_DICTIONARY_V2", "FILTER_DICTIONARY_V8", "FILTER_DICTIONARY"]:
                    if cand in wb.sheetnames:
                        ws = wb[cand]
                        used = p
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
    for r in range(2, ws.max_row + 1):
        f = ws.cell(r, 2).value
        if f is None:
            continue
        opt = ws.cell(r, 3).value
        sheets_app = ws.cell(r, 6).value or ws.cell(r, 5).value or ""
        gates = ws.cell(r, 7).value or ws.cell(r, 6).value or ""
        rec = ws.cell(r, 8).value or ws.cell(r, 7).value or ""
        # handle both 9-col and 17-col layouts
        # If col3 is Option Value but we mis-aligned, already captured above via fixed cols
        # Re-read via header name fallback
        rows.append({
            "filter": str(f).strip(),
            "opt": str(opt).strip() if opt is not None else "",
            "sheets_app": str(sheets_app),
            "gates": str(gates).strip() if gates else "",
            "rec": str(rec),
        })
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
    # token split len>2
    import re
    def toks(s): return [t for t in re.split(r"[ _\-\/]+", s.lower()) if len(t) > 2]
    gt = set(toks(gates)); st = set(toks(switch))
    if gt & st:
        return True
    return False

def _is_general(rec: str) -> bool:
    return rec.strip().upper().startswith("GENERAL")

def get_opportune_filters(switch: str, sheet: str) -> list[dict]:
    if not switch or not sheet:
        return []
    lifecycle = sheet.split("_")[0]
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

def get_opportune_filters_for_v2(switch: str, sheet: str) -> list[str]:
    entries = get_opportune_filters(switch, sheet)
    seen = []
    seen_set = set()
    for e in entries:
        hdr = f"{e['filter']}={e['opt']}"
        if hdr not in seen_set:
            seen_set.add(hdr)
            seen.append(hdr)
    return seen

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
    is_crypto = symside.upper().endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD","DAI"))
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

def ensure_npz_dir():
    target = ROOT / "backtest_v8" / "indicators"
    target.mkdir(parents=True, exist_ok=True)
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
    for entry in queue:
        ss = entry.get("symside", "")
        if ss and ss not in blocked and ss in recipes:
            return ss
    for ss in recipes:
        if ss not in blocked:
            return ss
    return "AAPL_LONG"

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
    # detect sheet set
    global SWITCH_SHEETS
    if "ENTRY_PULLBACK_BOUNCE" in wb.sheetnames:
        SWITCH_SHEETS = SWITCH_SHEETS_LEGACY
    else:
        SWITCH_SHEETS = SWITCH_SHEETS_V2
    # rename baseline metrics sheet
    new_baseline = f"{new_symside}_BASELINE_METRICS"
    old_candidates = ["TEMPLATE_BASELINE_METRICS", "ADP_LONG_BASELINE_METRICS", "TEMPLATE_V2_BASELINE_METRICS"]
    old_baseline = None
    for cand in old_candidates:
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
                        c.value = c.value.replace(old_baseline, new_baseline)
                        if "TEMPLATE" in c.value or "ADP_LONG" in c.value:
                            c.value = c.value.replace("TEMPLATE", new_symside.split("_")[0]).replace("ADP_LONG", new_symside)
    # Fix E2 chain: clamp E:E to E$2:E$5000, ensure first sheet E2 = baseline, others MAX(prev)
    for idx, name in enumerate(SWITCH_SHEETS):
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        if idx > 0:
            prev = SWITCH_SHEETS[idx-1]
            if prev in wb.sheetnames:
                # Fix any MAX('prev'!E:E) to clamped
                for r in range(1, ws.max_row+1):
                    v = ws.cell(row=r, column=5).value
                    if isinstance(v, str) and f"'{prev}'!E:E" in v:
                        ws.cell(row=r, column=5).value = v.replace(f"'{prev}'!E:E", f"'{prev}'!E$2:E$5000").replace(f"'{prev}'!E$2:E$5000", f"'{prev}'!E$2:E$5000")
                    if isinstance(v, str) and "E$2:E$5000" not in v and idx>0 and r==2 and v and "MAX" in v:
                        # ensure row2 E is clamped
                        ws.cell(row=r, column=5).value = f"=MAX('{prev}'!E$2:E$5000)"
        # Do NOT clear F/G/E formulas — they are live VLOOKUP chain
    # Erase results but keep header
    for cand in ["results", "Results_30d_Deltas", "Results_30d"]:
        if cand in wb.sheetnames:
            ws = wb[cand]
            # keep row1 header, clear row2+
            for r in range(2, ws.max_row+1):
                for c in range(1, ws.max_column+1):
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
        ("invalid_reason", live_metrics.get("invalid_reason","")),
    ]
    for r in range(2, max(20, ws.max_row+1)):
        ws.cell(row=r, column=1).value = None
        ws.cell(row=r, column=2).value = None
    for i, (k,v) in enumerate(rows, start=2):
        ws.cell(row=i, column=1).value = k
        ws.cell(row=i, column=2).value = json.dumps(v) if isinstance(v, dict) else v
    wb.save(str(wb_path))

def fill_overrides_and_defaults(wb_path: Path, new_symside: str, overrides: dict, defaults: dict):
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

def live_evaluate(symside: str, overrides: dict, window_days: int = 30) -> dict:
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
    try:
        from tools.opt.v12_pilot import evaluate_sanitized
        return evaluate_sanitized(symside, overrides, window_days=window_days)
    except Exception as e:
        import traceback
        return {"symside": symside, "valid": False, "invalid_reason": f"vector_evaluate {e}", "trace": traceback.format_exc()[:2000]}

def parity_ok(live: dict, vec: dict) -> tuple[bool, str]:
    """Parity gate — trades, gain, bh, TIM, DD are primary; sharpe is soft warn only per user."""
    if not live.get("valid"):
        return False, f"live invalid: {live.get('invalid_reason')}"
    if not vec.get("valid"):
        return False, f"vector invalid: {vec.get('invalid_reason')}"
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
    # bh / TIM / DD consistency (allow 5pp TIM, 5pp DD)
    lbh = float(live.get("bh_pct") or 0); vbh = float(vec.get("bh_pct") or 0)
    if abs(lbh - vbh) > 0.5:
        return False, f"bh mismatch live {lbh:.4f} vec {vbh:.4f}"
    ltim = float(live.get("tim_pct") or 0); vtim = float(vec.get("tim_pct") or 0)
    if abs(ltim - vtim) > 5.0:
        return False, f"TIM mismatch live {ltim:.2f} vec {vtim:.2f}"
    ldd = float(live.get("max_dd_pct") or 0); vdd = float(vec.get("max_dd_pct") or 0)
    if abs(ldd - vdd) > 5.0:
        return False, f"DD mismatch live {ldd:.2f} vec {vdd:.2f}"
    # sharpe is NOT a hard gate — soft warn only (user: variant_sharpe not relevant)
    ls = float(live.get("pool_sharpe") or 0.0); vs = float(vec.get("pool_sharpe") or 0.0)
    if abs(ls - vs) > 0.25:
        print(f"[parity-warn] sharpe delta live {ls:.4f} vec {vs:.4f} — ignored (soft, not blocking)", flush=True)
    return True, "parity ok"

def write_delta_to_results(wb_path: Path, switch: str, variant_gain: float, delta: float, vec: dict, live: dict | None = None):
    """Write ONLY to Results_30d_Deltas col H variant_gain so F/G VLOOKUP formulas compute.
    Never overwrite E/F/G formulas. C is updated separately only on parity.
    """
    wb = openpyxl.load_workbook(str(wb_path))
    # Find/create Results sheet
    target = None
    for cand in ["Results_30d_Deltas", "Results_30d", "results"]:
        if cand in wb.sheetnames:
            target = cand
            break
    if target is None:
        ws = wb.create_sheet("Results_30d_Deltas")
        ws.append(["param","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","REAL_COMPLETE_DELTA","variant_sharpe","trades","tim","filter_or_override","notes"])
        target = "Results_30d_Deltas"
    rws = wb[target]
    # Find row for switch or append
    target_row = None
    for r in range(2, rws.max_row+1):
        if str(rws.cell(row=r, column=1).value or "").strip() == switch:
            target_row = r
            break
    if target_row is None:
        target_row = rws.max_row + 1 if rws.max_row >= 1 else 2
        rws.cell(row=target_row, column=1).value = switch
    # Cols: 5 delta, 8 variant_gain, 6 delta_sharpe, 7 delta_trades, 10 variant_sharpe, 11 trades etc.
    rws.cell(row=target_row, column=8).value = float(variant_gain)
    rws.cell(row=target_row, column=5).value = float(delta)
    try:
        rws.cell(row=target_row, column=6).value = float(vec.get("pool_sharpe") or 0) - 0  # delta sharpe vs baseline not tracked, keep raw
        rws.cell(row=target_row, column=10).value = float(vec.get("pool_sharpe") or 0)
        rws.cell(row=target_row, column=11).value = int(vec.get("trades") or 0)
        rws.cell(row=target_row, column=12).value = float(vec.get("tim_pct") or 0)
    except Exception:
        pass
    if live:
        rws.cell(row=target_row, column=7).value = float(live.get("gain_pct") or 0) - 0
    wb.save(str(wb_path))

def read_instructions_or_die():
    """INSTRUCTIONS are LAW — read at startup per Template 2026-09-07."""
    for tmpl in [TEMPLATE, TEMPLATE_FALLBACK]:
        if tmpl.exists():
            try:
                wb = openpyxl.load_workbook(str(tmpl), data_only=False)
                for name in ["INSTRUCTIONS_V2", "INSTRUCTIONS"]:
                    if name in wb.sheetnames:
                        ws = wb[name]
                        # ensure at least 9 steps readable
                        txt = " ".join(str(ws.cell(r,1).value or "") for r in range(1, 15))
                        if "INSTRUCTIONS" in txt or "TEMPLATE" in txt:
                            wb.close()
                            return
                wb.close()
            except Exception:
                pass
    print("[warn] INSTRUCTIONS sheet not found in TEMPLATE — continuing per BACKTEST_BIBLE", flush=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym-side", dest="sym_side", default=None)
    ap.add_argument("--template", default=str(TEMPLATE))
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=1, help="workers kept for compat, but sequential per spec")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--max-switches", type=int, default=0)
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--vector-only", action="store_true")
    ap.add_argument("--vector-timeout", type=float, default=5.0)
    ap.add_argument("--promote", action="store_true")
    ap.add_argument("--no-promote", action="store_true")
    args = ap.parse_args()

    read_instructions_or_die()

    # V2 canonical: only fallback if neither exists; log
    if not Path(args.template).exists():
        if TEMPLATE_FALLBACK.exists():
            print(f"[template-fallback] {args.template} missing -> {TEMPLATE_FALLBACK}", flush=True)
            args.template = str(TEMPLATE_FALLBACK)
        else:
            print(f"[template] {args.template} missing and no fallback", flush=True)
    else:
        print(f"[template] using {args.template}", flush=True)

    import os as _os_pilot
    _os_pilot.environ["V8_SWEEP_MODE"] = "1"
    _os_pilot.environ.pop("V8_KEEP_ENTRY_GATES", None)

    ensure_npz_dir()
    new_symside = pick_first_symside(args.sym_side)

    # kill DAR_LONG forever
    try:
        _kf = Path("data/reports/lifecycle_pilot/killed_forever.json")
        if _kf.exists():
            import json as _jk
            _killed = set(_jk.loads(_kf.read_text()).get("killed", []))
            if new_symside in _killed or "DAR_LONG" in new_symside:
                print(f"[KILLED_FOREVER] {new_symside}", flush=True)
                return
        _bf = Path("data/reports/lifecycle_pilot/blocklist.txt")
        if _bf.exists() and "DAR_LONG" in _bf.read_text() and "DAR_LONG" in new_symside:
            print(f"[KILLED_FOREVER] blocklist {new_symside}", flush=True)
            return
    except Exception:
        pass

    template = Path(args.template)
    print(f"[pilot] sym_side={new_symside} template={template} workers={args.workers} vector_only={args.vector_only}", flush=True)

    recipes = load_live_recipes()
    overrides = dict(recipes.get(new_symside, {}).get("overrides") or {}) if new_symside in recipes else {}
    defaults = get_defaults_for_symside(new_symside)
    overrides, warns = sanitize_overrides(overrides, defaults)
    if warns:
        print(f"[sanitize] bool-for-float fixed {warns}", flush=True)
    print(f"[baseline] {new_symside}: {len(overrides)} overrides + {len(defaults)} defaults", flush=True)

    if args.out:
        target = Path(args.out)
        if target.exists():
            raise FileExistsError(f"refusing to overwrite {target}")
        import shutil
        shutil.copy2(template, target)
        wb_path = target
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
    print(f"[clone] -> {wb_path}", flush=True)

    # Baseline: vector first then live for parity (live may differ)
    print(f"[baseline] VECTOR {new_symside} with {len(overrides)} overrides ...", flush=True)
    t0 = time.time()
    baseline_vec = vector_evaluate(new_symside, overrides, window_days=args.window_days)
    print(f"[baseline] vector valid={baseline_vec.get('valid')} gain={baseline_vec.get('gain_pct')} trades={baseline_vec.get('trades')} sharpe={baseline_vec.get('pool_sharpe')} dt={time.time()-t0:.2f}s", flush=True)

    if args.vector_only:
        baseline_live = baseline_vec
        ok, reason = True, "vector-only"
    else:
        print(f"[baseline] LIVE {new_symside} ...", flush=True)
        baseline_live = live_evaluate(new_symside, overrides, window_days=args.window_days)
        ok, reason = parity_ok(baseline_live, baseline_vec)
        print(f"[baseline] live valid={baseline_live.get('valid')} gain={baseline_live.get('gain_pct')} trades={baseline_live.get('trades')} parity={ok} {reason}", flush=True)
        if not baseline_live.get("valid") and baseline_vec.get("valid"):
            print(f"[baseline] LIVE invalid ({baseline_live.get('invalid_reason')}) — using vector for sheet, live kept", flush=True)
            # still use live metrics for baseline sheet? Use vector gain if live invalid? Instructions say baseline = per_sym overrides union defaults, so vector gain is correct baseline.
            baseline_live = baseline_vec  # fallback for E2 so F calc works

    baseline_gain = float(baseline_live.get("gain_pct") or baseline_vec.get("gain_pct") or 0.0)
    fill_baseline_metrics(wb_path, new_symside, overrides, defaults, baseline_live if baseline_live.get("valid") else baseline_vec)
    fill_overrides_and_defaults(wb_path, new_symside, overrides, defaults)
    print(f"[baseline] filled E2={baseline_gain:.4f} trades={baseline_live.get('trades')} valid={baseline_live.get('valid')}", flush=True)

    if args.dry_run:
        print("[dry-run] done", flush=True)
        return

    # Sequential per-sheet processing
    wb = openpyxl.load_workbook(str(wb_path), data_only=False)
    # detect sheets present
    sheets_to_process = [args.sheet] if args.sheet else [s for s in SWITCH_SHEETS if s in wb.sheetnames]
    if not sheets_to_process:
        # fallback to whatever sheets exist that look like switch sheets
        sheets_to_process = [s for s in wb.sheetnames if s.startswith(("ENTRY","EXIT","REENTRY","AUGMENT","REDUCE","GLOBAL"))]
    print(f"[trials] sheets {sheets_to_process[:3]}... total {len(sheets_to_process)}", flush=True)
    wb.close()

    progress_path = PROGRESS_DIR / f"{new_symside}_pilot_progress.json"
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        progress = json.loads(progress_path.read_text())
    except Exception:
        progress = {"symside": new_symside, "baseline_gain": baseline_gain, "done": {}, "sheet_status": {}}

    cumulative_gain = baseline_gain
    cumulative_overrides = dict(overrides)
    total_written = 0
    total_pos = 0

    # For incremental delta we track cumulative_before per row in order
    for sheet in sheets_to_process:
        print(f"\n[sheet] START {sheet} cumulative={cumulative_gain:.4f}", flush=True)
        wb = openpyxl.load_workbook(str(wb_path), data_only=False)
        if sheet not in wb.sheetnames:
            wb.close()
            continue
        ws = wb[sheet]
        # Build ordered rows for this sheet
        rows = []
        for r in range(2, ws.max_row + 1):
            switch = ws.cell(row=r, column=1).value
            if not switch or not isinstance(switch, str):
                continue
            switch = switch.strip()
            if not switch or switch.lower() in ("switch","general","blanket"):
                continue
            cand = ws.cell(row=r, column=2).value
            if cand is None:
                continue
            eff = overrides.get(switch, defaults.get(switch, cand))
            # also consider cumulative_overrides may have promoted switch already
            if switch in cumulative_overrides:
                eff = cumulative_overrides[switch]
            def norm(v):
                if isinstance(v, str) and v.lower() in ("true","false"):
                    return v.lower() == "true"
                return v
            if norm(cand) == norm(eff):
                continue
            rows.append((r, switch, cand))
        wb.close()
        if args.max_switches and len(rows) > args.max_switches:
            rows = rows[:args.max_switches]
        print(f"[sheet] {sheet} {len(rows)} variants to test", flush=True)

        for (r, switch, cand) in rows:
            key = f"{sheet}!{r}:{switch}={cand}"
            if key in progress.get("done", {}):
                # already done, apply cumulative if it was pos
                prev = progress["done"][key]
                if prev.get("parity") and prev.get("delta") and prev["delta"] > 0:
                    cumulative_gain += float(prev["delta"])
                    cumulative_overrides[switch] = cand
                continue
            variant = dict(cumulative_overrides)
            variant[switch] = cand
            # sanitize bool-for-float
            variant, _ = sanitize_overrides(variant, defaults)

            # Vector delta vs cumulative_before (not vs initial baseline)
            cumulative_before = cumulative_gain
            t0 = time.time()
            vec = vector_evaluate(new_symside, variant, window_days=args.window_days)
            dt = time.time() - t0
            if not vec.get("valid"):
                print(f"[vec-fail] {sheet}!{r} {switch}={cand} {vec.get('invalid_reason')} dt={dt:.2f}s", flush=True)
                # write delta as vec invalid -> treat as 0, still write to Results so F shows something? Write variant_gain 0 delta = -cumulative_before? Actually keep F as vec delta invalid => not written? Write as 0 - cumulative_before? Instead write variant_gain 0 and delta = -cumulative_before for audit
                write_delta_to_results(wb_path, switch, 0.0, -cumulative_before, {"gain_pct":0,"trades":0,"pool_sharpe":0}, None)
                progress.setdefault("done", {})[key] = {"vec": vec, "parity": False, "delta": -cumulative_before, "reason": vec.get("invalid_reason")}
                continue
            vec_gain = float(vec.get("gain_pct") or vec.get("gain_per_mo") or 0.0)
            vector_delta = vec_gain - cumulative_before
            print(f"[vec] {sheet}!{r} {switch}={cand} vec_gain={vec_gain:.4f} delta={vector_delta:.4f} trades={vec.get('trades')} dt={dt:.2f}s", flush=True)

            # Always write F via Results so Excel computes (even if negative)
            write_delta_to_results(wb_path, switch, vec_gain, vector_delta, vec, None)
            progress.setdefault("done", {})[key] = {"vec": vec, "vector_delta": vector_delta, "delta": vector_delta, "parity": False}

            # Parity check only for non-negative deltas (need live) — negative deltas never promote, no live needed
            if vector_delta < 0:
                # E stays via formula, no C update
                progress["done"][key]["reason"] = "vector_negative_no_live"
                continue
            # vector_delta >=0 (0 or positive) needs live verification
            if args.vector_only:
                live = vec
                ok2, reason2 = True, "vector-only"
                live_delta = vector_delta
            else:
                live = live_evaluate(new_symside, variant, window_days=args.window_days)
                ok2, reason2 = parity_ok(live, vec)
                live_delta = float(live.get("gain_pct") or 0) - cumulative_before if live.get("valid") else None
                # update Results with live gain for G formula (variant_gain for live is same as vec? Actually G uses live gain via col 7)
                # Write live gain to same Results row col 7 via re-save
                if live.get("valid"):
                    # overwrite variant_gain for live? Keep live delta separate but Results col 7 is live variant gain
                    wb2 = openpyxl.load_workbook(str(wb_path))
                    for cand_name in ["Results_30d_Deltas", "Results_30d", "results"]:
                        if cand_name in wb2.sheetnames:
                            rws = wb2[cand_name]
                            for rr in range(2, rws.max_row+1):
                                if str(rws.cell(row=rr, column=1).value or "").strip() == switch:
                                    rws.cell(row=rr, column=9).value = float(live.get("gain_pct") or 0)  # col 7? Actually col8 is vec, col7 live? Keep vec in 8, live in 9? But earlier we used 8 for vec. Keep live in 9 for G?
                                    break
                            break
                    wb2.save(str(wb_path))
            progress["done"][key].update({"live": live, "live_delta": live_delta, "parity": ok2, "reason": reason2})
            if not ok2:
                print(f"[parity-fail] {sheet}!{r} {switch} {reason2} — F written, E stays", flush=True)
                continue
            if vector_delta > 0 and live_delta is not None and live_delta > 0:
                # PROMOTE — update C bold green, cumulative advances
                wb3 = openpyxl.load_workbook(str(wb_path))
                if sheet in wb3.sheetnames:
                    ws3 = wb3[sheet]
                    c_cell = ws3.cell(row=r, column=3)
                    c_cell.value = cand
                    c_cell.font = Font(name='Arial', bold=True, color="006100")
                    wb3.save(str(wb_path))
                cumulative_gain = vec_gain
                cumulative_overrides[switch] = cand
                total_pos += 1
                print(f"[PROMOTE] {sheet}!{r} {switch}={cand} delta={vector_delta:.4f} cumulative->{cumulative_gain:.4f}", flush=True)
            total_written += 1

        # Per-row L:BI filters for this sheet (SPECIFIC) — after switch deltas, fill each row's opportune filter columns
        # Only for rows where switch was tested (rows list) — vs switch-alone gain
        print(f"[filter] {sheet} per-row L:BI start", flush=True)
        wb = openpyxl.load_workbook(str(wb_path), data_only=False)
        if sheet not in wb.sheetnames:
            wb.close()
            continue
        ws = wb[sheet]
        # Map header -> col
        header_to_col = {}
        for c in range(12, ws.max_column+1):  # L=12
            hv = ws.cell(row=1, column=c).value
            if hv and isinstance(hv, str) and "=" in hv and hv.strip() not in ("", "Notes"):
                hv = hv.strip()
                if hv and not hv.upper().startswith("WHAT SWITCH"):
                    header_to_col[hv] = c
            if hv and isinstance(hv, str) and hv.strip().upper().startswith("WHAT SWITCH"):
                break
        wb.close()
        # For each row in this sheet, test its opportune filters
        for (r, switch, cand) in rows:
            # Get base variant gain for this switch (from Results)
            # We already have vec_gain for switch alone in progress, but need to test filter+switch vs cumulative_before at row time?
            # For SPECIFIC per-row, delta is vs switch alone (not vs baseline) — keep max per filter per row
            opportune = get_opportune_filters(switch, sheet)
            # SPECIFIC only (GENERAL handled at bottom)
            specifics = [e for e in opportune if not _is_general(e["rec"])]
            for entry in specifics:
                filt = entry["filter"]
                # header for this filter: filter=opt
                # But FILTER_DICTIONARY has one row per option; we need to find distinct headers for this filter that exist in sheet
                opt = entry["opt"]
                hdr = f"{filt}={opt}"
                if hdr not in header_to_col:
                    continue
                col = header_to_col[hdr]
                # Skip if already filled and >0? Always recompute vs current?
                # Build variant with switch+filter
                # Parse opt to correct type
                opt_val = opt
                def parse_opt(v, default):
                    if isinstance(default, bool):
                        return str(v).lower() == "true" if str(v).lower() in ("true","false") else bool(v)
                    if isinstance(default, int) and not isinstance(default, bool):
                        try: return int(float(str(v)))
                        except: return v
                    if isinstance(default, float):
                        try: return float(str(v))
                        except: return v
                    if isinstance(v, str) and v.lower() in ("true","false"):
                        return v.lower() == "true"
                    try:
                        if "." in str(v): return float(str(v))
                        return int(str(v))
                    except: return v
                default_filt = defaults.get(filt)
                opt_val = parse_opt(opt, default_filt)
                variant2 = dict(cumulative_overrides)
                variant2[switch] = cand
                variant2[filt] = opt_val
                variant2, _ = sanitize_overrides(variant2, defaults)
                vec2 = vector_evaluate(new_symside, variant2, window_days=args.window_days)
                if not vec2.get("valid"):
                    continue
                # For SPECIFIC, L:BI is vs switch alone gain? Use delta vs switch alone gain
                # Get switch-alone gain from progress
                key = f"{sheet}!{r}:{switch}={cand}"
                base_vec = progress.get("done", {}).get(key, {}).get("vec", {})
                base_gain = float(base_vec.get("gain_pct") or 0) if base_vec else 0
                # if base invalid, use cumulative_before
                if base_gain == 0:
                    base_gain = cumulative_gain  # fallback
                delta_filter = float(vec2.get("gain_pct") or 0) - base_gain
                # Write to L:BI column
                wb2 = openpyxl.load_workbook(str(wb_path))
                if sheet in wb2.sheetnames:
                    ws2 = wb2[sheet]
                    ws2.cell(row=r, column=col).value = float(delta_filter)
                    wb2.save(str(wb_path))
                    print(f"[filter] {sheet}!{r} {switch}+{filt}={opt_val} delta_filter={delta_filter:.4f}", flush=True)

        # GENERAL blanket filters at bottom: vs final cumulative of sheet
        print(f"[filter] {sheet} GENERAL blanket start vs final cumulative {cumulative_gain:.4f}", flush=True)
        wb = openpyxl.load_workbook(str(wb_path), data_only=False)
        ws = wb[sheet]
        # collect GENERAL filters for this lifecycle
        lifecycle = sheet.split("_")[0]
        general_filters: dict[str, list] = {}
        for entry in _load_filter_dictionary():
            if not _is_general(entry["rec"]):
                continue
            if lifecycle not in entry["sheets_app"] and "GLOBAL_CHECK" not in entry["sheets_app"]:
                continue
            filt = entry["filter"]
            general_filters.setdefault(filt, []).append(entry["opt"])
        wb.close()
        for filt, opts in general_filters.items():
            # distinct opts
            distinct = []
            seen = set()
            for o in opts:
                if str(o) not in seen:
                    seen.add(str(o))
                    distinct.append(o)
            for opt in distinct:
                default_filt = defaults.get(filt)
                def parse_opt2(v, default):
                    if isinstance(default, bool):
                        return str(v).lower() == "true" if str(v).lower() in ("true","false") else bool(v)
                    if isinstance(default, int) and not isinstance(default, bool):
                        try: return int(float(str(v)))
                        except: return v
                    if isinstance(default, float):
                        try: return float(str(v))
                        except: return v
                    if isinstance(v, str) and v.lower() in ("true","false"):
                        return v.lower() == "true"
                    try:
                        if "." in str(v): return float(str(v))
                        return int(str(v))
                    except: return v
                opt_val = parse_opt2(opt, default_filt)
                # skip if opt == current cumulative value
                cur = cumulative_overrides.get(filt, default_filt)
                def norm2(a,b):
                    if isinstance(a,str) and a.lower() in ("true","false"):
                        a = a.lower()=="true"
                    if isinstance(b,str) and b.lower() in ("true","false"):
                        b = b.lower()=="true"
                    return a==b
                if norm2(opt_val, cur):
                    continue
                variant3 = dict(cumulative_overrides)
                variant3[filt] = opt_val
                variant3, _ = sanitize_overrides(variant3, defaults)
                vec3 = vector_evaluate(new_symside, variant3, window_days=args.window_days)
                if not vec3.get("valid"):
                    continue
                delta_g = float(vec3.get("gain_pct") or 0) - cumulative_gain
                if delta_g <= 0:
                    continue
                # Need live parity for GENERAL too
                if args.vector_only:
                    ok3, reason3 = True, "vector-only"
                    live3 = vec3
                else:
                    live3 = live_evaluate(new_symside, variant3, window_days=args.window_days)
                    ok3, reason3 = parity_ok(live3, vec3)
                if not ok3:
                    print(f"[general parity-fail] {sheet} {filt}={opt_val} {reason3}", flush=True)
                    continue
                # Promote GENERAL: append new row at bottom
                wb4 = openpyxl.load_workbook(str(wb_path))
                if sheet in wb4.sheetnames:
                    ws4 = wb4[sheet]
                    nr = ws4.max_row + 1
                    ws4.cell(row=nr, column=1).value = filt
                    ws4.cell(row=nr, column=2).value = opt
                    ws4.cell(row=nr, column=3).value = opt_val
                    ws4.cell(row=nr, column=3).font = Font(name='Arial', bold=True, color="006100")
                    ws4.cell(row=nr, column=4).value = "GLOBAL_CHECK"
                    # F/G will compute via VLOOKUP after we write Results
                    ws4.cell(row=nr, column=5).value = f"=IF(F{nr}=\"\",E{nr-1},IF(F{nr}>0,E{nr-1}+F{nr},E{nr-1}))"
                    # keep F/G formulas as VLOOKUP? For GENERAL new rows, ensure F/G formulas exist
                    ws4.cell(row=nr, column=6).value = f"=IFERROR(VLOOKUP($A{nr},Results_30d_Deltas!$A$2:$H$5000,8,FALSE)-E{nr-1},\"\")"
                    ws4.cell(row=nr, column=7).value = f"=IFERROR(VLOOKUP($A{nr},Results_30d_Deltas!$A$2:$H$5000,7,FALSE)-E{nr-1},\"\")"
                    wb4.save(str(wb_path))
                write_delta_to_results(wb_path, filt, float(vec3.get("gain_pct") or 0), delta_g, vec3, live3)
                cumulative_overrides[filt] = opt_val
                cumulative_gain += delta_g
                print(f"[general PROMOTE] {sheet} {filt}={opt_val} delta={delta_g:.4f} cumulative->{cumulative_gain:.4f}", flush=True)

        # sheet done
        print(f"[sheet] DONE {sheet} cumulative={cumulative_gain:.4f} positives={total_pos}", flush=True)
        # persist progress
        progress["cumulative_gain"] = cumulative_gain
        progress["cumulative_overrides"] = cumulative_overrides
        progress_path.write_text(json.dumps(progress, indent=2))

    # Final: save with bh/gain naming per INSTRUCTIONS 7
    final_gain = cumulative_gain
    # bh from baseline
    bh = float(baseline_live.get("bh_pct") or baseline_vec.get("bh_pct") or 0)
    def fmt(v): return f"{v:.2f}".replace("-","m").replace(".","p")
    final_name = f"{new_symside}_bh{fmt(bh)}_gain{fmt(final_gain)}_30d_matrix.xlsx"
    final_path = OUT_DIR / final_name
    if final_path.exists():
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
        final_path = OUT_DIR / f"{new_symside}_bh{fmt(bh)}_gain{fmt(final_gain)}_30d_matrix_pilot_{ts}.xlsx"
    import shutil
    shutil.copy2(wb_path, final_path)
    print(f"[final] {final_path.name} bh={bh:.2f} gain={final_gain:.2f} cumulative deltas {total_pos} positives", flush=True)
    # also write pilot_progress json final
    progress["final_gain"] = final_gain
    progress["bh"] = bh
    progress["done_sheet"] = True
    progress_path.write_text(json.dumps(progress, indent=2))

    # Charts: keep minimal
    try:
        CHARTS_1M_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

if __name__ == "__main__":
    main()
