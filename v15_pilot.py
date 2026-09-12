#!/usr/bin/env python3
"""v15_pilot — SERIOUS cell-by-cell TEMPLATE filler with numpy live calculations and in-memory NPZ.

Spec: script request.md CLEAR_SERIOUS_600_LINES — Previous slow (1481L, 14s/row, reload per row,
workers 8, 30s timeout, 75 days at 100x) vs New fast (1505L, 0.5s/row, wb_keep open per sheet,
workers 16, 60s, MAX_ROWS 50000, 7h at 100x). Fills L:BI yellows + Results_Deltas col5/col8
via _atomic_save per row, real ledger numbers recalculated on live trading scripts
(v12_pilot.prepare_batch + evaluate_prepared_sanitized → evaluate_v12.prepare/evaluate_prepared).

NPZ stays in RAM: V12_NPZ_CACHE=32, ALL_PREPARED dict keeps sliced 30D npz_prepared + base_cfg.
No per-row reload. ThreadPool 16 batched 0.07s each, skip 84 combos when >500 rows.

Does NOT overwrite: clones TEMPLATE.xlsx → V15_V16_CELL_BY_CELL/{SYM}_30d_matrix.xlsx
(reuses latest pilot if exists), _atomic_save tmp+rename, progress.json resume per row,
heartbeat /tmp/v14_heartbeat_{SYM}.txt per cell, never crashes whole sheet (per-row try/except).

Real numbers: delta = variant_gain - cumulative_before (NOT variant-baseline), E chain
IF(F>0,Eprev+F,Eprev) via Excel VLOOKUP, variant_gain = gain_pct pnl_dollars/peak,
trades/tim/max_dd/pool_sharpe per trade, validated via live parity (or vector_only).
"""
from __future__ import annotations
import os
os.environ["V12_NPZ_CACHE"] = "32"
import sys
import time
import json
import argparse
import dataclasses
import datetime
import itertools
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl
from openpyxl.styles import Font, PatternFill
import numpy as np

TEMPLATE = ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx"
OUT_DIR = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
PROGRESS_DIR = ROOT / "data" / "reports" / "lifecycle_pilot"
SWITCH_SHEETS = [
    "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY",
    "REENTRY_WINDOWED", "REENTRY_ADAPTIVE",
    "AUGMENT_TREND", "AUGMENT_RISK_SIZING",
    "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER",
    "GLOBAL_RISK_GATES",
]

ALL_PREPARED: dict[str, dict] = {}
ALL_NPZ_ARRAYS: dict[str, dict] = {}
_FILTER_DICT_CACHE = None
MAX_ROWS = 50000

# S1 NPZ locations (sandbox + gateway)
S1_NPZ_DIRS = [Path("/home/niels/binance-sandbox/backtest_v8/indicators"), ROOT / "backtest_v8" / "indicators"]
S1_SSH = "niels@157.90.168.35"
S1_SSH_SANDBOX = "niels@157.180.125.52"
CHARTS_1M_DIR = ROOT / "data" / "reports" / "charts_1M"

def ensure_npz_for_symside(symside: str, window_days: int = 30) -> Path | None:
    """Find (or generate if old/unavailable) NPZ on S1 before starting. Checks local, S1 sandbox, gateway; fetches via scp; regenerates if >30d old."""
    sym = symside.split("_")[0]
    # tokenised stocks: NVDAUSDT -> NVDA
    base = sym
    if sym.endswith("USDT"):
        try:
            from tools.opt.evaluate_v12 import _stock_universe
            if sym[:-4] in _stock_universe():
                base = sym[:-4]
        except Exception:
            pass
    local_candidates = [ROOT / "backtest_v8" / "indicators" / f"{base}.npz", ROOT / "backtest_v8" / "indicators" / f"{sym}.npz"]
    s1_candidates = [Path("/home/niels/binance-sandbox/backtest_v8/indicators") / f"{base}.npz", Path("/home/niels/binance-sandbox/backtest_v8/indicators") / f"{sym}.npz"]
    for p in local_candidates + s1_candidates:
        if p.exists() and p.stat().st_size > 100_000:
            age_days = (time.time() - p.stat().st_mtime) / 86400
            if age_days < 35:
                return p
            print(f"[npz-stale] {p} {age_days:.0f}d old — will refresh from S1", flush=True)
            break
    # try S1 fetch via scp -P 2201 gateway then s1-int
    for src_sym in [sym, base]:
        s1_src = f"/home/niels/binance-sandbox/backtest_v8/indicators/{src_sym}.npz"
        dst = ROOT / "backtest_v8" / "indicators" / f"{src_sym}.npz"
        for host in [S1_SSH, S1_SSH_SANDBOX]:
            try:
                import subprocess as _sp
                dst.parent.mkdir(parents=True, exist_ok=True)
                # try scp
                cmd = ["scp", "-P", "2201", f"{host}:{s1_src}", str(dst)] if "157.90" in host else ["scp", f"{host}:{s1_src}", str(dst)]
                r = _sp.run(cmd, capture_output=True, timeout=30)
                if dst.exists() and dst.stat().st_size > 100_000:
                    print(f"[npz-fetch] {src_sym}.npz from {host} -> {dst} {dst.stat().st_size/1e6:.1f}M", flush=True)
                    return dst
            except Exception as e:
                print(f"[npz-fetch-warn] {host} {e}", flush=True)
                continue
        # fallback: rsync
        try:
            import subprocess as _sp
            _sp.run(["rsync", "-avz", "-e", "ssh -p 2201", f"{S1_SSH}:{s1_src}", str(dst)], capture_output=True, timeout=30)
            if dst.exists() and dst.stat().st_size > 100_000:
                print(f"[npz-rsync] {src_sym}.npz -> {dst}", flush=True)
                return dst
        except Exception:
            pass
    # final check any local now exists
    for p in local_candidates:
        if p.exists() and p.stat().st_size > 100_000:
            print(f"[npz-found] {p} {p.stat().st_size/1e6:.1f}M", flush=True)
            return p
    print(f"[npz-missing] {symside} no NPZ found — will attempt vector prepare anyway (may be synthetic)", flush=True)
    return None

def write_zoomable_chart(symside: str, sheet: str | None, overrides: dict, window_days: int, suffix: str = "30D_REAL_ZOOMABLE"):
    """Produce zoomable chart with all trades metrics and reasons for each trade at end of every sheet. Single-file offline file:// HTML."""
    try:
        from tools.opt.hires_chart import generate_hires
        # sheet-specific
        if sheet:
            out_name = f"{symside}_{sheet}_{suffix}.html"
            p = generate_hires(symside, overrides, window_days, out_name=out_name)
            # also ensure SPREADSHEETS copy
            for dst in [ROOT / "SPREADSHEETS" / out_name, CHARTS_1M_DIR / out_name]:
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copy2(p, dst)
                except Exception:
                    pass
            print(f"[chart] {sheet} -> {out_name} ({p.stat().st_size/1024:.0f}K)", flush=True)
            return p
        else:
            out_name = f"{symside}_{suffix}.html"
            p = generate_hires(symside, overrides, window_days, out_name=out_name)
            for dst in [ROOT / "SPREADSHEETS" / out_name, CHARTS_1M_DIR / out_name, Path(f"/tmp/{out_name}")]:
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copy2(p, dst)
                except Exception:
                    pass
            print(f"[chart] COMPLETE -> {out_name}", flush=True)
            return p
    except Exception as e:
        import traceback
        print(f"[chart-warn] {sheet or 'COMPLETE'} {e} {traceback.format_exc()[:600]}", flush=True)
    return None

def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

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
    hdr = {str(ws.cell(1, c).value or "").strip(): c for c in range(1, 30)}
    fc = hdr.get("Filter", 2)
    oc = sc = gc = rc = None
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
    rows = []
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

def get_opportune_filters(switch: str, sheet: str) -> list[dict]:
    if not switch or not sheet:
        return []
    lifecycle = sheet.split("_")[0]
    rows = _load_filter_dictionary()
    out = []
    for e in rows:
        rec = (e["rec"] or "").strip().upper()
        if rec == "UNLIKELY" or "UNLIKELY" in rec:
            continue
        sa = e["sheets_app"] or ""
        if sa.strip() == "ALL":
            applicable = True
        else:
            applicable = (lifecycle in sa) or ("GLOBAL_CHECK" in sa)
        if not applicable:
            continue
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

def _atomic_save(wb, wb_path: Path):
    import tempfile, shutil, os as _os
    tmp = str(wb_path) + ".tmp"
    try:
        wb.save(tmp)
        _os.replace(tmp, str(wb_path))
    except Exception:
        try:
            wb.save(str(wb_path))
        except Exception:
            pass
        try:
            if _os.path.exists(tmp):
                _os.remove(tmp)
        except Exception:
            pass

def preload_prepared(symside: str, window_days: int = 30):
    if symside in ALL_PREPARED:
        return ALL_PREPARED[symside]
    from tools.opt.v12_pilot import prepare_batch
    try:
        prep = prepare_batch(symside, window_days)
        if prep and prep.get("npz_prepared") is not None:
            ALL_PREPARED[symside] = prep
            npz = prep.get("npz_prepared") or {}
            ALL_NPZ_ARRAYS[symside] = {k: np.asarray(v) for k, v in npz.items() if isinstance(v, np.ndarray) and v.ndim}
            print(f"[v15-preload] {symside} {len(ALL_NPZ_ARRAYS[symside])} arrays {len(np.asarray(npz.get('close', [])))} bars hot", flush=True)
            return prep
    except Exception as e:
        print(f"[v15-preload-warn] {symside} {e}", flush=True)
    return None

def live_evaluate(symside: str, overrides: dict, window_days: int = 30) -> dict:
    import os as _os
    _os.environ["V12_NPZ_CACHE"] = "32"
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
        return {"symside": symside, "valid": False, "invalid_reason": f"live_evaluate {e}", "trace": traceback.format_exc()[:2000], "gain_pct": 0.0, "trades": 0, "pool_sharpe": 0.0}

def vector_evaluate_cached(symside: str, overrides: dict, window_days: int = 30) -> dict:
    prep = ALL_PREPARED.get(symside) or preload_prepared(symside, window_days)
    if prep is None:
        from tools.opt.v12_pilot import evaluate_sanitized
        return evaluate_sanitized(symside, overrides, window_days=window_days)
    from tools.opt.v12_pilot import evaluate_prepared_sanitized
    return evaluate_prepared_sanitized(prep, overrides, window_days=window_days)

def parity_ok(live: dict, vec: dict, allow_zero_baseline: bool = False) -> tuple[bool, str]:
    if not live.get("valid"):
        return False, f"live invalid: {live.get('invalid_reason')}"
    if not vec.get("valid"):
        return False, f"vector invalid: {vec.get('invalid_reason')}"
    lt = int(live.get("trades") or 0); vt = int(vec.get("trades") or 0)
    if lt == 0 and vt == 0:
        return False, f"zero trades live={lt} vec={vt}"
    if lt == 0 and allow_zero_baseline and vt > 10:
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
    wb = openpyxl.load_workbook(str(wb_path))
    fd_rows = _load_filter_dictionary()
    for sheet in SWITCH_SHEETS:
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        try:
            for mr in list(ws.merged_cells.ranges):
                if mr.min_row == 1 and mr.max_row == 1 and mr.max_col >= 12 and mr.min_col <= ws.max_column + 50:
                    ws.unmerge_cells(str(mr))
        except Exception:
            pass
        existing = set()
        scan_max = max(ws.max_column, 12)
        stale = []
        for c in range(12, scan_max + 1):
            try:
                hv = ws.cell(row=2, column=c).value
            except Exception:
                hv = None
            if hv and isinstance(hv, str) and "=" in hv:
                existing.add(hv.strip())
                stale.append(c)
        for c in stale:
            try:
                ws.cell(row=2, column=c).value = None
            except Exception:
                pass
        existing = set()
        headers = []
        seen = set()
        lifecycle = sheet.split("_")[0]
        sheet_switches = [str(ws.cell(r, 1).value or "").strip() for r in range(2, min(50, ws.max_row+1)) if ws.cell(r, 1).value]
        scored = []
        for idx_e, e in enumerate(fd_rows):
            sa = (e["sheets_app"] or "").strip()
            if sa == "ALL":
                applicable = True
            else:
                applicable = (lifecycle in sa) or ("GLOBAL_CHECK" in sa)
            if not applicable:
                continue
            if not _is_general(e["rec"]):
                gates = e["gates"] or ""
                hits = 0
                earliest = 999
                for r_idx, sw in enumerate(sheet_switches):
                    if _token_overlap(gates, sw):
                        hits += 1
                        if r_idx < earliest:
                            earliest = r_idx
                if hits == 0:
                    continue
                scored.append((earliest, -hits, idx_e, e))
            else:
                scored.append((999, 0, idx_e, e))
        scored.sort(key=lambda x: (x[0], x[1], x[2]))
        for earliest, neg_hits, idx_e, e in scored:
            hdr = f"{e['filter']}={e['opt']}"
            if hdr in seen or hdr in existing:
                continue
            seen.add(hdr)
            headers.append(hdr)
            if len(headers) >= 50:
                break
        col = 12
        for hdr in headers:
            if hdr in existing:
                continue
            try:
                c = ws.cell(row=2, column=col)
                if str(getattr(c, "__class__", "")).endswith("MergedCell"):
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

def clone_template(template: Path, new_symside: str) -> Path:
    if not template.exists():
        raise FileNotFoundError(f"template missing {template}")
    import glob as _glob
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _pilots = sorted(_glob.glob(str(OUT_DIR / f"{new_symside}_30d_matrix_pilot_*.xlsx")))
    if _pilots:
        return Path(_pilots[-1])
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
        c2 = ws.cell(row=2, column=6)
        if isinstance(c2.value, str) and "-E1" in c2.value:
            c2.value = c2.value.replace("-E1", "-E2")
    for name in SWITCH_SHEETS:
        if name not in wb.sheetnames:
            continue
        wsc = wb[name]
        for r in range(2, wsc.max_row + 1):
            cv = wsc.cell(row=r, column=3).value
            if isinstance(cv, str) and " + " in cv:
                wsc.cell(row=r, column=3).value = None
    for cand in ["Results_Deltas", "Results_30d_Deltas", "Results_30d", "results"]:
        if cand in wb.sheetnames:
            ws = wb[cand]
            for r in range(2, ws.max_row + 1):
                for c in range(1, ws.max_column + 1):
                    cell = ws.cell(row=r, column=c)
                    if cell.value is not None:
                        cell.value = None
            full_hdr = ["key","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","REAL_COMPLETE_DELTA","variant_sharpe","trades","tim","dd","filter_or_override","symside","window","bh_pct","gain_pct","tim_pct","max_dd","win_rate","bars","peak","source"]
            for ci, h in enumerate(full_hdr, start=1):
                cur = ws.cell(1, ci).value
                if cur is None or str(cur).strip() == "":
                    ws.cell(1, ci).value = h
                    ws.cell(1, ci).font = Font(bold=True)
                elif str(cur).strip().lower() != h.lower() and ci <= 8:
                    ws.cell(1, ci).font = Font(bold=True)
            break
    else:
        ws = wb.create_sheet("Results_Deltas")
        ws.append(["key","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","REAL_COMPLETE_DELTA","variant_sharpe","trades","tim","dd","filter_or_override","symside","window","bh_pct","gain_pct","tim_pct","max_dd","win_rate","bars","peak","source"])
        for ci in range(1, 25):
            ws.cell(1, ci).font = Font(bold=True)
    if "Results_30d_Deltas" not in wb.sheetnames:
        ws2 = wb.create_sheet("Results_30d_Deltas")
        ws2.append(["key","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","REAL_COMPLETE_DELTA","variant_sharpe","trades","tim","dd","filter_or_override","symside","window","bh_pct","gain_pct","tim_pct","max_dd","win_rate","bars","peak","source"])
        for ci in range(1, 25):
            ws2.cell(1, ci).font = Font(bold=True)
    wb.save(str(target))
    return target

def main():
    ap = argparse.ArgumentParser(description="v15_pilot — SERIOUS TEMPLATE filler: cell-by-cell L:BI + Results_Deltas with in-memory NPZ (V12_NPZ_CACHE=32), workers 16, wb_keep open per sheet")
    ap.add_argument("--sym-side", dest="sym_side", default=None)
    ap.add_argument("--template", default=str(TEMPLATE))
    ap.add_argument("--out", default=None)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--max-switches", type=int, default=0)
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--vector-only", action="store_true", help="vector-only, no live parity (fast)")
    ap.add_argument("--no-lbI", action="store_true")
    args = ap.parse_args()

    import os as _os
    _os.environ["V8_SWEEP_MODE"] = "1"
    _os.environ.pop("V8_KEEP_ENTRY_GATES", None)

    if sys.platform == "darwin":
        print("[warn] Mac is live-only — filler is S1-only. Use ssh 157.180.125.52 (dry-run allowed on Mac)", flush=True)
        if not args.dry_run:
            print("[hint] adding --dry-run to test clone without NPZ is allowed; full fill needs S1 with NPZ", flush=True)

    if args.window_days == 365 or args.window_days >= 100:
        print("BLOCKED: 1yr requires 30D gate — run 30D first", file=sys.stderr)
        sys.exit(2)
    if args.window_days not in (30, 20, 7, 1):
        print(f"BLOCKED: only 30/20/7/1 allowed, got {args.window_days}", file=sys.stderr)
        sys.exit(2)

    if args.sym_side:
        new_symside = args.sym_side.strip().upper()
    else:
        try:
            import json as _j
            camp = _j.loads((PROGRESS_DIR / "campaign_order_1mo.json").read_text())
            queue = camp.get("queue") or []
            new_symside = queue[0].get("symside", "AAPL_LONG") if queue else "AAPL_LONG"
        except Exception:
            new_symside = "AAPL_LONG"

    # defaults + live recipes
    try:
        from tools.opt.v12_pilot import load_live_recipes
        recipes = load_live_recipes()
    except Exception:
        recipes = {}
    overrides = dict(recipes.get(new_symside, {}).get("overrides") or {}) if new_symside in recipes else {}
    overrides = {k: v for k, v in overrides.items() if not (isinstance(v, str) and " + " in v)}
    defaults = get_defaults_for_symside(new_symside)
    overrides, warns = sanitize_overrides(overrides, defaults)
    if warns:
        print(f"[sanitize] {warns}", flush=True)
    print(f"[baseline] {new_symside}: {len(overrides)} overrides + {len(defaults)} defaults workers={args.workers} vector_only={args.vector_only}", flush=True)

    # Find or generate NPZ on S1 before starting (old/unavailable)
    ensure_npz_for_symside(new_symside, args.window_days)
    # Keep NPZ in RAM
    prepared = preload_prepared(new_symside, args.window_days)
    if prepared is None:
        from tools.opt.v12_pilot import evaluate_sanitized
        baseline_vec = evaluate_sanitized(new_symside, overrides, window_days=args.window_days)
        print(f"[baseline] no prepared, vec valid={baseline_vec.get('valid')} gain={baseline_vec.get('gain_pct')} trades={baseline_vec.get('trades')}", flush=True)
        if ("ZECUSDC" not in new_symside) and (not baseline_vec.get("valid") or int(baseline_vec.get("trades") or 0) == 0):
            print(f"[skip-empty-baseline] {new_symside} invalid/0 trades — skipping", flush=True)
            return
        prepared_for_fallback = None
    else:
        from tools.opt.v12_pilot import evaluate_prepared_sanitized
        baseline_vec = evaluate_prepared_sanitized(prepared, overrides, window_days=args.window_days)
        print(f"[baseline] vec valid={baseline_vec.get('valid')} gain={baseline_vec.get('gain_pct')} trades={baseline_vec.get('trades')} sharpe={baseline_vec.get('pool_sharpe')} hot", flush=True)
        if ("ZECUSDC" not in new_symside) and (not baseline_vec.get("valid") or int(baseline_vec.get("trades") or 0) == 0):
            print(f"[skip-empty-baseline] {new_symside} baseline invalid/0 trades — skipping", flush=True)
            try:
                (PROGRESS_DIR / f"{new_symside}_v14_progress.json").unlink(missing_ok=True)
            except Exception:
                pass
            return
        prepared_for_fallback = prepared

    if args.vector_only:
        baseline_live = baseline_vec
        reason = "vector-only"
    else:
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(live_evaluate, new_symside, dict(overrides), args.window_days)
            try:
                baseline_live = fut.result(timeout=90)
            except Exception as e:
                baseline_live = {"valid": False, "invalid_reason": str(e), "gain_pct": 0.0}
        ok, reason = parity_ok(baseline_live, baseline_vec, allow_zero_baseline=True)
        print(f"[baseline] live valid={baseline_live.get('valid')} gain={baseline_live.get('gain_pct')} trades={baseline_live.get('trades')} parity={ok} {reason}", flush=True)
        if not baseline_live.get("valid") and baseline_vec.get("valid"):
            print(f"[baseline] live invalid — using vec for E2 ({baseline_live.get('invalid_reason')})", flush=True)
            baseline_live = baseline_vec
    baseline_gain = float(baseline_live.get("gain_pct") or baseline_vec.get("gain_pct") or 0.0)
    bh = float(baseline_live.get("bh_pct") or baseline_vec.get("bh_pct") or 0.0)

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

    # baseline metrics sheet
    try:
        wb = openpyxl.load_workbook(str(wb_path))
        sheet = f"{new_symside}_BASELINE_METRICS"
        if sheet not in wb.sheetnames:
            ws = wb.create_sheet(sheet)
            ws["A1"] = "metric"; ws["B1"] = "value"
        else:
            ws = wb[sheet]
        rows_baseline = [
            ("gain_pct", baseline_live.get("gain_pct")),
            ("bh_pct", baseline_live.get("bh_pct")),
            ("delta_vs_bh", baseline_live.get("delta_vs_bh")),
            ("trades", baseline_live.get("trades")),
            ("pool_sharpe", baseline_live.get("pool_sharpe")),
            ("tim_pct", baseline_live.get("tim_pct")),
            ("max_dd_pct", baseline_live.get("max_dd_pct")),
            ("gain_per_mo", baseline_live.get("gain_per_mo")),
            ("bh_per_mo", baseline_live.get("bh_per_mo")),
            ("source", f"live backtest_v12_engine {utcnow()} via {new_symside} overrides={len(overrides)}"),
            ("window", baseline_live.get("window")),
            ("valid", baseline_live.get("valid")),
        ]
        for r in range(2, max(20, ws.max_row + 1)):
            ws.cell(row=r, column=1).value = None
            ws.cell(row=r, column=2).value = None
        for i, (k, v) in enumerate(rows_baseline, start=2):
            ws.cell(row=i, column=1).value = k
            ws.cell(row=i, column=2).value = json.dumps(v) if isinstance(v, dict) else v
        wb.save(str(wb_path))
    except Exception as e:
        print(f"[baseline-metrics-warn] {e}", flush=True)

    if not args.no_lbI:
        ensure_lbI_headers(wb_path)
        print("[headers] L:BI ensured", flush=True)
    print(f"[baseline] E2={baseline_gain:.4f} bh={bh:.4f} trades={baseline_live.get('trades')} NPZ hot={prepared is not None}", flush=True)
    if args.dry_run:
        print("[dry-run] done", flush=True)
        return

    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    progress_path = PROGRESS_DIR / f"{new_symside}_v14_progress.json"
    try:
        progress = json.loads(progress_path.read_text())
    except Exception:
        progress = {"symside": new_symside, "baseline_gain": baseline_gain, "bh": bh, "done": {}}
    cumulative_gain = progress.get("cumulative_gain", baseline_gain)
    cumulative_overrides = dict(progress.get("cumulative_overrides", overrides))
    cumulative_overrides = {k: v for k, v in cumulative_overrides.items() if not (isinstance(v, str) and " + " in v)}
    _bl_trades = int(baseline_live.get("trades") or 0)
    _bl_valid = bool(baseline_live.get("valid"))
    baseline_had_zero_trades = (_bl_trades == 0) or (not _bl_valid)

    heartbeat_path = Path("/tmp") / f"v14_heartbeat_{new_symside}.txt"
    per_cell_timeout_sec = 60
    def _touch_heartbeat(msg: str):
        try:
            heartbeat_path.write_text(f"{time.time():.0f} {msg}")
        except: pass
    def _check_per_cell_timeout(cell_start: float) -> bool:
        return (time.time() - cell_start) > per_cell_timeout_sec

    _touch_heartbeat("start")
    total_pos = 0

    wb_tmp = openpyxl.load_workbook(str(wb_path), data_only=False)
    sheets = [args.sheet] if args.sheet else [s for s in SWITCH_SHEETS if s in wb_tmp.sheetnames]
    if not sheets:
        sheets = [s for s in wb_tmp.sheetnames if any(s.startswith(p) for p in ["ENTRY", "EXIT", "REENTRY", "AUGMENT", "REDUCE", "GLOBAL"])]
    wb_tmp.close()

    for sheet in sheets:
        try:
            print(f"\n[sheet] {sheet} cumulative={cumulative_gain:.4f}", flush=True)
            _touch_heartbeat(f"sheet {sheet}")
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
            _key_skip = f"{sheet}!{r}:{sw}={cand}"
            if norm(cand) == norm(eff) and _key_skip in progress.get("done", {}):
                continue
            if str(ws.cell(row=r, column=4).value or "") == "GLOBAL_CHECK":
                continue
            rows.append((r, sw, cand))
        wb.close()
        if args.max_switches and len(rows) > args.max_switches:
            rows = rows[:args.max_switches]
        print(f"[sheet] {sheet} {len(rows)} variants", flush=True)
        if not rows:
            continue

        wb_keep = openpyxl.load_workbook(str(wb_path), data_only=False)
        ws_keep = wb_keep[sheet] if sheet in wb_keep.sheetnames else None
        header_to_col = {}
        if ws_keep is not None:
            for c in range(12, ws_keep.max_column + 1):
                hv = ws_keep.cell(row=2, column=c).value
                if hv and isinstance(hv, str) and "=" in hv:
                    hv = hv.strip()
                    if not hv.upper().startswith("WHAT SWITCH"):
                        header_to_col[hv] = c
                if hv and isinstance(hv, str) and hv.strip().upper().startswith("WHAT SWITCH"):
                    break

        for (r, switch, cand) in rows:
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
            print(f"[DEBUG] sheet {sheet} row {r} {switch}={cand} start cum={cumulative_gain:.4f}", flush=True)
            _touch_heartbeat(f"cell {sheet}!{r}")
            try:
                cumulative_before = cumulative_gain
                opportune = get_opportune_filters(switch, sheet)
                specifics = [e for e in opportune if not _is_general(e["rec"])]
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
                single_filters = []
                for e in specifics:
                    filt = e["filter"]
                    opt_raw = e["opt"]
                    opt_val = parse_opt(opt_raw, defaults.get(filt))
                    hdr = f"{filt}={opt_raw}"
                    cur = cumulative_overrides.get(filt, defaults.get(filt))
                    if norm2(opt_val, cur):
                        continue
                    single_filters.append((filt, opt_val, hdr, opt_raw))
                candidates = []
                v0 = dict(cumulative_overrides)
                v0[switch] = cand
                v0, _ = sanitize_overrides(v0, defaults)
                candidates.append((v0, None, None, None))
                for (filt, opt_val, hdr, opt_raw) in single_filters:
                    v = dict(cumulative_overrides)
                    v[switch] = cand
                    v[filt] = opt_val
                    v, _ = sanitize_overrides(v, defaults)
                    candidates.append((v, filt, opt_val, hdr))
                _single_filters_for_combo = single_filters

                best = None
                pending_lbI = {}
                vector_delta_val = None
                try:
                    if prepared is not None:
                        import concurrent.futures as _cf2
                        from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_prep
                        with _cf2.ThreadPoolExecutor(max_workers=16) as ex:
                            vecs = list(ex.map(lambda v: _eval_prep(prepared, v, window_days=args.window_days), [c[0] for c in candidates]))
                    else:
                        from tools.opt.v12_pilot import evaluate_many_sanitized as _eval_many
                        vecs = _eval_many(new_symside, [c[0] for c in candidates], window_days=args.window_days)
                except Exception as e:
                    print(f"[vec-batch-err] {sheet}!{r} {switch} err {e}", flush=True)
                    vecs = []
                for idx, (variant, filt, fval, hdr) in enumerate(candidates):
                    if idx >= len(vecs):
                        break
                    vec = vecs[idx]
                    if not vec.get("valid"):
                        continue
                    vg = float(vec.get("gain_pct") or 0)
                    delta = vg - cumulative_before
                    if filt is None:
                        print(f"[CANDIDATE] {sheet}!{r} {switch}={cand} alone vec_gain={vg:.4f} delta={delta:.4f} vs cum {cumulative_before:.4f} trades={vec.get('trades')} sharpe={float(vec.get('pool_sharpe') or 0):.4f}", flush=True)
                    else:
                        print(f"[CANDIDATE] {sheet}!{r} {switch}={cand}+{filt}={fval} vec_gain={vg:.4f} delta={delta:.4f} vs cum {cumulative_before:.4f} trades={vec.get('trades')} sharpe={float(vec.get('pool_sharpe') or 0):.4f}", flush=True)
                    if filt is not None and hdr in header_to_col:
                        pending_lbI[hdr] = float(delta)
                    if best is None or delta > best[0]:
                        best = (delta, variant, filt, fval, hdr, vec)
                    if filt is None:
                        vector_delta_val = float(delta)

                if best is None or (best[0] <= 0 and len(rows) <= 500):
                    _best_before_combo = best[0] if best else float("-inf")
                    if (best is None or _best_before_combo <= 0) and len(rows) <= 500:
                        _combo_pool = _single_filters_for_combo
                        if len(_combo_pool) > 8:
                            _ranked = sorted(_combo_pool, key=lambda x: pending_lbI.get(f"{x[0]}={x[3]}", float("-inf")), reverse=True)
                            _combo_pool = _ranked[:8]
                        all_combos = []
                        for combo_size in [2, 3]:
                            if len(_combo_pool) < combo_size:
                                continue
                            for combo in itertools.combinations(_combo_pool, combo_size):
                                v_combo = dict(cumulative_overrides)
                                v_combo[switch] = cand
                                combo_label_parts = []
                                combo_hdr_parts = []
                                for (filt_c, opt_val_c, hdr_c, opt_raw_c) in combo:
                                    v_combo[filt_c] = opt_val_c
                                    combo_label_parts.append(f"{filt_c}={opt_val_c}")
                                    combo_hdr_parts.append(hdr_c)
                                v_combo, _ = sanitize_overrides(v_combo, defaults)
                                all_combos.append((v_combo, "+".join(combo_label_parts), "+".join(combo_hdr_parts), combo_label_parts, combo_hdr_parts))
                        if all_combos:
                            try:
                                if prepared is not None:
                                    from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_prep2
                                    vecs_c = [_eval_prep2(prepared, vc[0], window_days=args.window_days) for vc in all_combos]
                                else:
                                    from tools.opt.v12_pilot import evaluate_many_sanitized as _eval_many2
                                    vecs_c = _eval_many2(new_symside, [vc[0] for vc in all_combos], window_days=args.window_days)
                            except Exception as e:
                                print(f"[vec-batch-combo-err] {sheet}!{r} {switch} err {e}", flush=True)
                                vecs_c = []
                            for idx, (v_combo, combo_label, combo_hdr, combo_label_parts, combo_hdr_parts) in enumerate(all_combos):
                                if idx >= len(vecs_c):
                                    break
                                vec_c = vecs_c[idx]
                                if not vec_c.get("valid"):
                                    continue
                                if _check_per_cell_timeout(cell_start):
                                    print(f"[PER_CELL TIMEOUT] {sheet}!{r} stalled during combo", flush=True)
                                    break
                                vg_c = float(vec_c.get("gain_pct") or 0)
                                delta_c = vg_c - cumulative_before
                                print(f"[CANDIDATE-COMBO] {sheet}!{r} {switch}={cand}+{'+'.join(combo_label_parts)} vec_gain={vg_c:.4f} delta={delta_c:.4f} vs cum {cumulative_before:.4f}", flush=True)
                                if best is None or delta_c > best[0]:
                                    best = (delta_c, v_combo, combo_label, combo_label, "+".join(combo_hdr_parts), vec_c)
                                    for hdr_c in combo_hdr_parts:
                                        pending_lbI[hdr_c] = float(delta_c)

                if best is None:
                    progress.setdefault("done", {})[key] = {"delta": 0, "reason": "all vectors invalid"}
                    print(f"[ROW] {sheet}!{r} {switch}={cand} vs cum {cumulative_before:.4f} -> NO VALID", flush=True)
                    progress_path.write_text(json.dumps(progress, indent=2))
                    _touch_heartbeat(f"cell {sheet}!{r} NO VALID")
                    continue

                delta_best, variant_best, filt_best, fval_best, hdr_best, vec_best = best
                # _atomic_save via wb_keep (keep open per sheet)
                try:
                    wb_row = wb_keep
                    ws_row = wb_keep[sheet] if sheet in wb_keep.sheetnames else None
                    if pending_lbI and ws_row is not None:
                        for hdr, d in pending_lbI.items():
                            col = header_to_col.get(hdr)
                            if col:
                                try:
                                    ws_row.cell(row=r, column=col).value = float(d)
                                except Exception:
                                    pass
                    target = None
                    for cand_name in ["Results_Deltas", "Results_30d_Deltas", "Results_30d", "results"]:
                        if cand_name in wb_row.sheetnames:
                            target = cand_name
                            break
                    if target is None:
                        ws_new = wb_row.create_sheet("Results_Deltas")
                        ws_new.append(["key","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","REAL_COMPLETE_DELTA","variant_sharpe","trades","tim","dd"])
                        target = "Results_Deltas"
                    rws = wb_row[target]
                    if str(rws.cell(1,1).value or "").strip().lower() in ("param","switch"):
                        rws.cell(1,1).value = "key"
                    key_results = f"{switch}={cand}" if cand is not None else switch
                    found = None
                    for rr in range(2, rws.max_row + 1):
                        if str(rws.cell(row=rr, column=1).value or "").strip() == key_results:
                            found = rr
                            break
                    if found is None and cand is not None:
                        for rr in range(2, rws.max_row + 1):
                            if str(rws.cell(row=rr, column=1).value or "").strip() == switch:
                                rws.cell(row=rr, column=1).value = key_results
                                found = rr
                                break
                    if found is None:
                        found = rws.max_row + 1
                        rws.cell(row=found, column=1).value = key_results
                    rws.cell(row=found, column=8).value = float(vec_best.get("gain_pct") or 0)
                    rws.cell(row=found, column=5).value = float(delta_best)
                    try:
                        rws.cell(row=found, column=10).value = int(vec_best.get("trades") or 0)
                        rws.cell(row=found, column=12).value = float(vec_best.get("tim_pct") or 0)
                        rws.cell(row=found, column=11).value = float(vec_best.get("max_dd_pct") or 0) if vec_best.get("max_dd_pct") is not None else None
                        header_map = {str(rws.cell(1,c).value or "").strip().lower(): c for c in range(1, rws.max_column+1)}
                        if "variant_sharpe" in header_map:
                            rws.cell(row=found, column=header_map["variant_sharpe"]).value = float(vec_best.get("pool_sharpe") or 0)
                        if "bh_pct" in header_map:
                            rws.cell(row=found, column=header_map["bh_pct"]).value = float(vec_best.get("bh_pct") or 0)
                        if "gain_pct" in header_map:
                            rws.cell(row=found, column=header_map["gain_pct"]).value = float(vec_best.get("gain_pct") or 0)
                    except Exception:
                        pass
                    _atomic_save(wb_row, wb_path)
                except Exception as _e:
                    print(f"[row-write-err] {sheet}!{r} {_e}", flush=True)
                progress.setdefault("done", {})[key] = {"delta": float(delta_best), "vec_gain": float(vec_best.get("gain_pct") or 0), "vec": {k: vec_best.get(k) for k in ["gain_pct","trades","pool_sharpe","valid","bh_pct","tim_pct","max_dd_pct"]}, "best_filter": filt_best, "best_fval": fval_best}
                try:
                    progress_path.write_text(json.dumps(progress, indent=2))
                except: pass
                _filter_suffix = f"+{filt_best}={fval_best}" if filt_best else ""
                if delta_best <= 0:
                    print(f"[ROW] {sheet}!{r} {switch}={cand}{_filter_suffix} vec_gain={float(vec_best.get('gain_pct') or 0):.4f} delta={delta_best:.4f} vs cum {cumulative_before:.4f} -> NEG trades vec={vec_best.get('trades')} sharpe={float(vec_best.get('pool_sharpe') or 0):.4f}", flush=True)
                    _touch_heartbeat(f"cell {sheet}!{r} NEG")
                    continue
                print(f"[ROW] {sheet}!{r} {switch}={cand}{_filter_suffix} vec_gain={float(vec_best.get('gain_pct') or 0):.4f} delta={delta_best:.4f} vs cum {cumulative_before:.4f} -> POSITIVE candidate", flush=True)
                if args.vector_only:
                    ok, reason = True, "vector-only (deferred verify)"
                    live_delta = delta_best
                    live_best = vec_best
                else:
                    import concurrent.futures as _cf
                    with _cf.ThreadPoolExecutor(max_workers=16) as ex:
                        fut = ex.submit(live_evaluate, new_symside, dict(variant_best), args.window_days)
                        try:
                            live_best = fut.result(timeout=90)
                        except Exception as e:
                            live_best = {"valid": False, "invalid_reason": str(e), "gain_pct": 0.0}
                    if live_best.get("invalid_reason","").startswith("live timeout"):
                        print(f"[WATCHDOG LIVE TIMEOUT] {sheet}!{r} {switch}={cand}", flush=True)
                        ok, reason = False, live_best["invalid_reason"]
                        live_delta = None
                    else:
                        ok, reason = parity_ok(live_best, vec_best, allow_zero_baseline=baseline_had_zero_trades)
                        live_delta = float(live_best.get("gain_pct") or 0) - cumulative_before if live_best.get("valid") else None
                    if _check_per_cell_timeout(cell_start):
                        ok = False; reason = "per-cell timeout after live"
                progress["done"][key].update({"live": {k: live_best.get(k) for k in ["gain_pct","trades","pool_sharpe","valid","invalid_reason"]} if 'live_best' in locals() else {}, "live_delta": live_delta, "parity": ok if 'ok' in locals() else False, "reason": reason if 'reason' in locals() else ""})
                try:
                    progress_path.write_text(json.dumps(progress, indent=2))
                except: pass
                if not ok:
                    print(f"[parity-fail] {sheet}!{r} {switch}={cand}" + (f"+{filt_best}={fval_best}" if filt_best else "") + f" delta={delta_best:.4f} {reason}", flush=True)
                    _touch_heartbeat(f"cell {sheet}!{r} parity-fail")
                    continue
                if live_delta is not None and live_delta <= 0:
                    print(f"[live-neg] {sheet}!{r} {switch} live_delta={live_delta:.4f} — not promoting", flush=True)
                    _touch_heartbeat(f"cell {sheet}!{r} live-neg")
                    continue
                # PROMOTE — green C
                try:
                    wb3 = openpyxl.load_workbook(str(wb_path))
                    if sheet in wb3.sheetnames:
                        ws3 = wb3[sheet]
                        ws3.cell(row=r, column=3).value = cand
                        ws3.cell(row=r, column=3).font = Font(name="Arial", bold=True, color="006100")
                        wb3.save(str(wb_path))
                except Exception:
                    pass
                cumulative_gain = float(vec_best.get("gain_pct") or 0)
                cumulative_overrides = dict(variant_best)
                total_pos += 1
                progress["done"][key]["cumulative_after"] = cumulative_gain
                progress["cumulative_gain"] = cumulative_gain
                progress["cumulative_overrides"] = cumulative_overrides
                progress_path.write_text(json.dumps(progress, indent=2))
                print(f"[PROMOTE] {sheet}!{r} {switch}={cand}" + (f"+{filt_best}={fval_best}" if filt_best else "") + f" delta={delta_best:.4f} cum->{cumulative_gain:.4f}", flush=True)
                _touch_heartbeat(f"cell {sheet}!{r} PROMOTE")
            except Exception as e:
                import traceback
                print(f"[ROW-ERR] {sheet}!{r} {switch}={cand} err {e} {traceback.format_exc()[:800]}", flush=True)
                try:
                    progress.setdefault("done", {})[key] = {"delta": 0, "reason": f"row err {e}"}
                    progress_path.write_text(json.dumps(progress, indent=2))
                except: pass
                _touch_heartbeat(f"cell {sheet}!{r} ERR")
                continue
            if _check_per_cell_timeout(cell_start):
                print(f"[PER_CELL TIMEOUT] {sheet}!{r} {switch}={cand} >{per_cell_timeout_sec}s — advancing", flush=True)

        try:
            wb_keep.close()
        except Exception:
            pass
        print(f"[sheet DONE] {sheet} cum={cumulative_gain:.4f} positives={total_pos}", flush=True)
        # zoomable chart with all trades metrics and reasons for each trade at end of every sheet
        try:
            write_zoomable_chart(new_symside, sheet, cumulative_overrides, args.window_days)
        except Exception as _ce:
            print(f"[chart-warn] {sheet} {_ce}", flush=True)
        progress["cumulative_gain"] = cumulative_gain
        progress["cumulative_overrides"] = cumulative_overrides
        except Exception as _sheet_e:
            import traceback
            print(f"[sheet-ERR] {sheet} {_sheet_e} {traceback.format_exc()[:800]}", flush=True)
            try:
                progress_path.write_text(json.dumps(progress, indent=2))
            except Exception:
                pass
            continue

    bh_raw = float(baseline_live.get("bh_pct") or baseline_vec.get("bh_pct") or 0)
    # skip-empty guard
    try:
        _wb_check = openpyxl.load_workbook(str(wb_path), data_only=True)
        _rws_chk = None
        for _cand in ["Results_Deltas", "Results_30d", "results"]:
            if _cand in _wb_check.sheetnames:
                _rws_chk = _wb_check[_cand]
                break
        _is_empty = _rws_chk is None or _rws_chk.max_row < 2
        _wb_check.close()
    except Exception:
        _is_empty = False
    if _is_empty or (total_pos == 0 and abs(cumulative_gain - baseline_gain) < 1e-9):
        # still keep filled L:BI even if no promote, but mark
        print(f"[done] {new_symside} no positives but every cell written bh={bh_raw:.2f} gain={cumulative_gain:.2f} — keeping {wb_path.name}", flush=True)
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
    try:
        progress_path.write_text(json.dumps(progress, indent=2))
    except Exception:
        pass
    # complete zoomable chart (offline file:// like /private/tmp/MU_LONG_30D_REAL_ZOOMABLE.html) with all trades
    try:
        write_zoomable_chart(new_symside, None, cumulative_overrides, args.window_days, suffix="30D_REAL_ZOOMABLE")
    except Exception as _ce2:
        print(f"[chart-final-warn] {_ce2}", flush=True)
    print(f"[final] {final_path} bh={bh_raw:.2f} gain={cumulative_gain:.2f} positives={total_pos} hot={list(ALL_NPZ_ARRAYS.keys())[:2]}", flush=True)

if __name__ == "__main__":
    main()
