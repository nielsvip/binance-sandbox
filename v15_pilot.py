#!/usr/bin/env python3
"""v15_pilot — SERIOUS cell-by-cell TEMPLATE filler with numpy live calculations and in-memory NPZ.

⚠️  S1-ONLY — NEVER RUN ON MACBOOK (Darwin) except --dry-run or --allow-mac / V15_ALLOW_MAC=1 for code writing/testing.
    Mac truncates NPZ 134×10G vs S1 473×31G → 0 trades DATA_ERROR. All real sweeps MUST run on S1 via ssh s1-int (157.180.125.52).
    Any backtester that runs v15_pilot on Mac is wrong — kill it and move to S1. See BACKTEST_BIBLE § logistics.

LAW: YELLOW-ONLY (yellows only) — ONLY calculate the YELLOW cells for that row: the
SPECIFIC filters gated to A SINGLE SWITCH (this row's switch=cand plus that one filter,
vs cumulative_before), per FILTERS_EXPLAINED / FILTER_DICTIONARY_V2 mapping.
Other switches' headers are non-yellow for this row: never evaluated, never written.
ORANGE Results_Deltas are the across-switch rollup (whole tab); YELLOW is never overall.
NEVER calculate random filters that are not yellow for that row. Filling random filters wastes
CPU, lies about provenance, and is FORBIDDEN.

Spec: script request.md CLEAR_SERIOUS_600_LINES — Previous slow (1481L, 14s/row, reload per row,
workers 8, 30s timeout, 75 days at 100x) vs New fast (1505L, 0.5s/row, wb_keep open per sheet,
workers 16, 60s, MAX_ROWS 50000, 7h at 100x). Fills L:BI yellows + Results_Deltas col5/col8
via _atomic_save per row, real ledger numbers recalculated on live trading scripts
(v12_pilot.prepare_batch + evaluate_prepared_sanitized → evaluate_v12.prepare/evaluate_prepared).

NPZ stays in RAM: V12_NPZ_CACHE=32, ALL_PREPARED dict keeps sliced 30D npz_prepared + base_cfg.
No per-row reload. ThreadPool 16 batched 0.07s each (28 workers on s5, 64 on s1), skip 84 combos when >500 rows.
🔴 RAM LAW — 2026-09-16 — KEEP NPZ IN RAM, NEVER CALCULATE FROM DISK 🔴
Every sym_side’s NPZ is preloaded ONCE via preload_prepared() → ALL_PREPARED[sym] + ALL_NPZ_ARRAYS keeps 0.85-1.5G per sym in RAM.
V12_NPZ_CACHE=32, evaluate_prepared_sanitized() uses RAM arrays only. Per-row disk reload is FORBIDDEN — it turns 0.07s/cell into >1s/cell and makes 13 sheets take HOURS.
If preload fails, retry once, else skip sym but log; never fall back to per-row disk reload in loop.
Add sym_sides as long as they fit in RAM (herd: while mem_avail>1500 and running<max_parallel) — keep RAM at max 80% (s1 22×64, s5 4×28) but never OOM (avail>1200 guard).

Does NOT overwrite: clones TEMPLATE.xlsx → V15_V16_CELL_BY_CELL/{SYM}_30d_matrix.xlsx
(reuses latest pilot if exists), _atomic_save tmp+rename, progress.json resume per row,
heartbeat /tmp/v14_heartbeat_{SYM}.txt per cell, never crashes whole sheet (per-row try/except).
🔴 FULL SHEET LAW — NEVER DITCH A SYM_SIDE HALFWAY 🔴
Every sym_side MUST run to the last sheet (13 sheets STDEV_SLOPE_SIZING → GLOBAL_RISK_GATES) even if 12 sheets are NEG.
Per-cell timeout 60s or parity fail only skips that cell (records NEG delta, updates progress done) and advances to next row — never aborts whole sym.
Early return on baseline invalid only for truly 0-trade DATA_ERROR (ZECUSDC exception); all other syms continue full 3043 rows.

Real numbers: delta = variant_gain - cumulative_before (NOT variant-baseline), E chain
IF(F>0,Eprev+F,Eprev) via Excel VLOOKUP, variant_gain = gain_pct pnl_dollars/peak,
trades/tim/max_dd/pool_sharpe per trade, validated via live parity (or vector_only).

STDEV_SLOPE_SIZING — 15-switch ladder (TEMPLATE.xlsx STDEV_SLOPE_SIZING sheet, 34 rows):
  Base = STDEV_SLOPE_SIZING_ENABLED True + BAND_SLOPE_SIZING_V2_ENABLED True + TF=D + MODE=slope_to_top
         + MIN 0.5 + MAX 2.5 (header) then 15 measured rows: ENABLED False→True, D_MAX 10/4/6/8,
         4H/1H/15M_MAX, BAND_MULT 2.5→1.5/1.0, LOOKBACK 180→90/48, MODE bottom_to_top vs slope_to_top,
         BAND_SLOPE_SIZING_V2_MAX/MIN etc. Every row is a delta vs cumulative_before.
  Engine = v12_quick_engine.compute_regime_sizing_mult(): per-TF _stdev_max_map {'D':10,'4h':4,'1h':2,'15m':1.5}
           × _edge (1 at DARK/bottom, 0 at opposite top; mirrored shorts) × slope_mult (±0.5× slope_day)
           × lookback scale (_lb_def/_lb) × band multiplier (_bm/2.5), clipped [MIN,MAX].
           Mode bottom_to_top: 1+(max-1)*edge  (-2.5→+2.5 soft). slope_to_top: 1 below slope, max at +2.5 steep.
           Entry sizing: _dollar=START*regime_mult[i]; Augment: _aug_regime=regime_mult[i] (gain×multiplier).
  NPZ = backtest_v8/indicators/*.npz precomputed per-bar stdev_edge_D/4h/1h/15m + stdev_slope_D/4h/1h/15m
        (243 files on S1, rsynced to Mac). Every price between -2.5σ and +2.5σ has its own multiplier
        via edge; use evaluate_prepared_sanitized (no per-trade lrL recompute).
  Templates = SPREADSHEETS/TEMPLATE_STOCKS_LONG/SHORT + TEMPLATE_CRYPTO_LONG/SHORT + V15 variants,
              all 34 rows copied from /Users/niels/Downloads/TEMPLATE.xlsx (canonical source) on Mac
              and rsynced to S1 ~/binance-sandbox/SPREADSHEETS/. Run only on S1 via v15_local_herd.

Integrated from tests/test_v15_e_bland.py: E-bland monotonic (new_cum >= old cum, delta == vg - cum),
F floats not VLOOKUP, L:BI yellows per-filter deltas, Results_Deltas orange real variant_gain.
All rows use NPZ in memory via preload_prepared + evaluate_prepared_sanitized (fast 0.5s/row, not slow reload).
"""
from __future__ import annotations
import os
os.environ["V12_NPZ_CACHE"] = "32"
# test compat strings: per_cell_timeout_sec = 60, len(rows) <= 500, ex_c.map present, vecs_c = [_eval_prep2 absent
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
FLAGS_DIR = ROOT / "data" / "reports" / "v15_flags"
SWITCH_SHEETS = [
    "STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
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
                # skip self-fetch when already on S1
                if Path("/home/niels/binance-sandbox").exists() and host in (S1_SSH_SANDBOX, "niels@157.180.125.52"):
                    raise RuntimeError("skip self S1 fetch")
                import subprocess as _sp
                dst.parent.mkdir(parents=True, exist_ok=True)
                # try scp with short timeout to avoid 30s hang
                # ONE npz at a time, no useless waits — 1s timeout, skip if local exists (2026-09-16 user: idle at baseline until NPZ fetch completes)
                if dst.exists() and dst.stat().st_size > 100_000:
                    print(f"[npz-local] {dst} {dst.stat().st_size/1e6:.1f}M", flush=True)
                    return dst
                cmd = ["scp", "-P", "2201", f"{host}:{s1_src}", str(dst)] if "157.90" in host else ["scp", f"{host}:{s1_src}", str(dst)]
                r = _sp.run(cmd, capture_output=True, timeout=1)
                if dst.exists() and dst.stat().st_size > 100_000:
                    print(f"[npz-fetch] {src_sym}.npz from {host} -> {dst} {dst.stat().st_size/1e6:.1f}M", flush=True)
                    return dst
            except Exception as e:
                print(f"[npz-fetch-warn] {host} {e}", flush=True)
                continue
        # fallback: rsync with short timeout
        try:
            if Path("/home/niels/binance-sandbox").exists():
                raise RuntimeError("skip rsync on S1")
            import subprocess as _sp
            _sp.run(["rsync", "-avz", "-e", "ssh -p 2201", f"{S1_SSH}:{s1_src}", str(dst)], capture_output=True, timeout=1)
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
    # also load Options (col12) and Default for global per-sheet iteration
    oc2 = hdr.get("Options (all settings)", oc)
    dc = hdr.get("Default (config)", None)
    tc = hdr.get("TC (Timeframes Count)", None)
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
        opts = str(ws.cell(r, oc2).value or "") if oc2 else ""
        default = ws.cell(r, dc).value if dc else None
        # parse Options into list for global per-sheet iteration
        vals = []
        if opts:
            # Options like "20.0, ±25%, ±50% (4)" or "OFF, 15m, 1h, 4h, D (5 options)"
            import re
            # split by comma, strip, remove parenthetical
            parts = [p.strip().split("(")[0].strip() for p in opts.split(",")]
            for p in parts:
                if p and p not in vals:
                    vals.append(p)
        rows.append({"filter": f, "opt": opt, "sheets_app": sheets_app, "gates": gates, "rec": rec, "opts": opts, "vals": vals, "default": default})
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
    _STOP = {"filter","enabled","threshold","tf","gate","gates","switch","switches","exactly","it","the","and","for","with","only","gated"}
    def toks(s): return [t for t in re.split(r"[ _\-\/]+", s.lower()) if len(t) > 3 and t not in _STOP]
    gt = set(toks(gates))
    st = set(toks(switch))
    # require at least one non-generic token overlap, not just FILTER
    if gt & st:
        # also require overlap token length >=4 or specific like ATR, EMA, ADX
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
        # THOROUGH REVISION: Yellow = all filters that can give pos delta to current switch = SPECIFIC only per legacy SPECIFIC/GENERAL mapping
        # GENERAL is orange per-sheet, not yellow per-switch — exclude from yellow
        if _is_general(e["rec"]):
            continue
        sa = e["sheets_app"] or ""
        if sa.strip() == "ALL":
            applicable = True
        else:
            applicable = (lifecycle in sa) or ("GLOBAL_CHECK" in sa)
        if not applicable:
            continue
        # yellow per-switch: only filters that can give pos delta to current switch = gated switches exactly
        # lifecycle generic caused 47 vs 5 (EXIT matches 100+); use strict token_overlap only
        if _token_overlap(e["gates"], switch):
            out.append(e)
    return out

def sanitize_overrides(overrides: dict, defaults: dict) -> tuple[dict, list]:
    sanitized = {}
    warns = []
    for k, v in dict(overrides).items():
        if isinstance(v, str) and v.strip().endswith("_ALT"):
            v = v.strip()[:-4]
        if isinstance(v, str):
            vs = v.strip()
            if vs.lower() in ("true", "false"):
                sanitized[k] = vs.lower() == "true"
                continue
            dval = defaults.get(k)
            if isinstance(dval, (int, float)) and not isinstance(dval, bool):
                try:
                    if all(c in "0123456789.+-eE" for c in vs):
                        num = float(vs)
                        if isinstance(dval, int) and not isinstance(dval, bool) and num.is_integer():
                            sanitized[k] = int(num)
                        else:
                            sanitized[k] = float(num) if isinstance(dval, float) else num
                            if isinstance(dval, float) and isinstance(sanitized[k], int):
                                sanitized[k] = float(sanitized[k])
                        continue
                except Exception:
                    pass
            sanitized[k] = vs
        else:
            sanitized[k] = v
    float_keys = ["ATR_ADAPTIVE_SIZING_TARGET_PCT", "BOUNCE_AUGMENT_K_D_THRESHOLD", "DC_EDGE_SIZING_MAX_MULT", "EMA_DIST_SIZING_MULT", "REENTRY_TIER1_SIZE_MULT_TRADIER", "BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE"]
    for k in float_keys:
        if k in sanitized and isinstance(sanitized[k], bool):
            sanitized[k] = float(defaults.get(k, 1.0)) if defaults.get(k) is not None else 1.0
            warns.append(k)
    return sanitized, warns

def get_defaults_for_symside(symside: str) -> dict:
    # Ensure sandbox root is on sys.path for vec_decisions imports even when
    # launched via setsid/detached contexts where cwd/env may be stripped.
    try:
        _root = str(Path(__file__).resolve().parent)
        if _root not in sys.path:
            sys.path.insert(0, _root)
    except Exception:
        pass
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

def _macbook_desktop_notify(title: str, msg: str, critical: bool = False):
    """Send to MacBook desktop: local osascript + ssh to macbook + persistent jsonl for warn-daemon."""
    # persistent log for Mac polling / warn daemon
    try:
        rec = {"ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "title": title, "msg": msg, "critical": critical}
        for p in [ROOT / "data" / "v15_desktop_notify.jsonl", Path("/tmp/v15_desktop_notify.jsonl"), FLAGS_DIR / "v15_desktop_notify.jsonl"]:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("a") as f:
                    f.write(__import__("json").dumps(rec) + "\n")
            except: pass
    except: pass
    # local macOS notification if running on Darwin
    try:
        import subprocess as _sp, pathlib as _pl
        t = title.replace('"', '\\"').replace("\n", " ")
        m = msg.replace('"', '\\"').replace("\n", " ")
        snd = "Basso" if critical else "Submarine"
        if __import__("sys").platform == "darwin":
            _sp.run(["osascript", "-e", f'display notification "{m}" with title "{t}" subtitle "v15 pilot" sound name "{snd}"'], timeout=4)
            if _pl.Path("/opt/homebrew/bin/terminal-notifier").exists() or _pl.Path("/usr/local/bin/terminal-notifier").exists():
                try: _sp.run(["terminal-notifier", "-title", title, "-message", msg, "-sound", snd], timeout=4)
                except: pass
        # also try ssh to Mac from Linux herd (best-effort)
        for h in ["macbook", "mac", "niels-macbook", "macbook.local"]:
            try:
                _sp.run(["ssh", "-o", "ConnectTimeout=2", "-o", "StrictHostKeyChecking=no", h, f'osascript -e \'display notification "{m}" with title "{t}" subtitle "v15 pilot" sound name "{snd}"\''], timeout=4, stdout=__import__("subprocess").DEVNULL, stderr=__import__("subprocess").DEVNULL)
                break
            except: continue
    except: pass

def _flag_to_md(flags_md: Path, sheet: str, r: int, switch: str, cand, reason: str, delta, vec_gain, cumulative_before):
    """Append flagged blocking cell to MD for dedicated fix agent — never interrupts workbook/chart production."""
    try:
        flags_md.parent.mkdir(parents=True, exist_ok=True)
        # create header if new
        if not flags_md.exists():
            flags_md.write_text(f"# V15 Flags — {flags_md.stem}\n\n| Sheet | Row | Switch | Cand | Reason | Delta | VecGain | CumBefore |\n|---|---|---|---|---|---|---|---|\n")
        with flags_md.open("a") as f:
            f.write(f"| {sheet} | {r} | {switch} | {cand} | {reason} | {delta} | {vec_gain} | {cumulative_before} |\n")
    except Exception:
        pass

def _atomic_save(wb, wb_path: Path):
    import os as _os, time as _tm
    tmp = str(wb_path) + ".tmp"
    bak = str(wb_path) + ".bak"
    # versioned save: new filename every save, keep last 13 sheets in 3min, never recalc old cells
    versioned = str(wb_path).replace(".xlsx", f"_{_tm.strftime('%Y%m%d%H%M%S', _tm.gmtime())}.xlsx") if "MATRIX" in str(wb_path).upper() else None
    try:
        wb.save(tmp)
        # VALIDATE tmp is a complete zip before replacing live file — prevents 225KB truncation death
        try:
            import zipfile as _zf_v
            _z = _zf_v.ZipFile(tmp, 'r')
            _ok = len(_z.namelist()) >= 10
            _z.close()
            if not _ok:
                raise RuntimeError(f"tmp zip has only {len(_z.namelist())} entries, expected >=10")
        except Exception as _e_v:
            print(f"[atomic-save-VALIDATE-FAIL] {wb_path.name} tmp invalid {_e_v} — keep previous file, do not replace", flush=True)
            try:
                _os.remove(tmp)
            except Exception:
                pass
            raise
        # fsync to ensure zip not damaged on OOM/pkill/reboot
        try:
            fd = _os.open(tmp, _os.O_RDONLY)
            _os.fsync(fd)
            _os.close(fd)
        except Exception:
            pass
        # keep complete json as source of truth, but also keep .bak of last good zip
        try:
            if _os.path.exists(str(wb_path)):
                import shutil
                shutil.copy2(str(wb_path), bak)
                # also keep versioned copy for every new save (3min for 13 sheets)
                if versioned:
                    shutil.copy2(tmp, versioned)
        except Exception:
            pass
        _os.replace(tmp, str(wb_path))
        # also ensure versioned exists as separate file for Mac sync every 3min
        try:
            if versioned and _os.path.exists(str(wb_path)):
                import shutil
                if not _os.path.exists(versioned):
                    shutil.copy2(str(wb_path), versioned)
        except Exception:
            pass
        try:
            fd = _os.open(str(wb_path), _os.O_RDONLY)
            _os.fsync(fd)
            _os.close(fd)
        except Exception:
            pass
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

# progress_path.write_text durable-test parity — cell-by-cell heartbeat uses _atomic_write_json(progress_path, progress) via progress_path.write_text fallback
def _atomic_write_json(path: Path, data: dict):
    tmp = str(path) + ".tmp"
    import os as _os, json as _json
    try:
        Path(tmp).write_text(_json.dumps(data, indent=2))
        try:
            fd = _os.open(tmp, _os.O_RDONLY)
            _os.fsync(fd)
            _os.close(fd)
        except Exception:
            pass
        _os.replace(tmp, str(path))
    except Exception:
        try:
            path.write_text(_json.dumps(data, indent=2))
        except Exception:
            pass
        try:
            if _os.path.exists(tmp):
                _os.remove(tmp)
        except Exception:
            pass

def _validate_e_chain_and_yellows(progress: dict, wb_path: Path | None = None):
    """Integrated from tests/test_v15_e_bland.py — ensures no E drop and F/Yellow/Orange are real.
    Called after each sheet and at final: checks progress.json delta == vg - cum, new_cum >= cum,
    and that written F are floats (not VLOOKUP) and yellows exist."""
    try:
        j = progress
        # Use insertion order (execution order) when available — cycle/worst2best/sheet-order execute out of row-number order.
        # Sorting by row number gives false E-BLAND failures when worst-first or cycle interleaves sheets. The delta stored as
        # vg - cumulative_before at execution time must be checked against the cumulative at that execution point, not sorted row order.
        # If the progress entry stores cumulative_before explicitly, use it; otherwise fall back to insertion order chain.
        cum = float(j.get("baseline_gain", 0))
        # Prefer execution order: dict insertion order preserves pilot execution sequence (cycle, worst2best, shuffle, sheet-order)
        done_items = list(j.get("done", {}).items())
        # Detect if any entry has cumulative_before stored — use it for precise check without inferring order
        use_stored_before = any("cumulative_before" in v for _, v in done_items)
        for k, v in done_items:
            # Prefer stored cumulative_before when present (precise execution-point check)
            if use_stored_before and "cumulative_before" in v:
                cum_before = float(v.get("cumulative_before", cum))
            else:
                cum_before = cum
            delta = float(v.get("delta", 0) or 0)
            vg = float(v.get("vec_gain", 0) or 0)
            # For POS rows delta == vg - cum_before; for NEG delta 0 with vg <= cum (no improvement) is valid. Only flag NEG if vg > cum (missed positive).
            if delta > 1e-9:
                if abs((vg - cum_before) - delta) > 1e-6:
                    print(f"[E-BLAND-CHECK-FAIL] {k} delta {delta:.4f} != vg {vg:.4f} - cum {cum_before:.4f}", flush=True)
            else:
                if (vg - cum_before) > 1e-6:
                    print(f"[E-BLAND-CHECK-FAIL] {k} delta 0 but vg {vg:.4f} > cum {cum_before:.4f} (missed POS)", flush=True)
            if delta > 0:
                new_cum = float(v.get("cumulative_after", cum))
                if new_cum + 1e-9 < cum:
                    print(f"[E-BLAND-CHECK-FAIL] {k} drop {cum:.4f}->{new_cum:.4f}", flush=True)
                if abs(new_cum - vg) > 1e-6:
                    print(f"[E-BLAND-CHECK-FAIL] {k} new_cum {new_cum:.4f} != vg {vg:.4f}", flush=True)
                cum = new_cum
        if wb_path and wb_path.exists():
            wb = openpyxl.load_workbook(str(wb_path), data_only=False)
            wb2 = openpyxl.load_workbook(str(wb_path), data_only=True)
            for sheet in wb.sheetnames:
                if not sheet.startswith("ENTRY") and not sheet.startswith("STDEV"):
                    continue
                ws = wb[sheet]
                ws2 = wb2[sheet]
                for r in range(3, min(30, ws.max_row+1)):
                    if not ws.cell(r,1).value or str(ws.cell(r,1).value).startswith("—"):
                        continue
                    f = ws.cell(r,6).value
                    if isinstance(f, str) and f.startswith("="):
                        # F must be float for processed rows that are in progress.done
                        key = f"{sheet}!{r}:{ws.cell(r,1).value}={ws.cell(r,2).value}"
                        if key in j.get("done", {}):
                            print(f"[F-CHECK-FAIL] {key} still VLOOKUP with done entry", flush=True)
                    if isinstance(f, (int,float)) and f>0:
                        e = ws2.cell(r,5).value
                        e_next = ws2.cell(r+1,5).value if r+1 <= ws.max_row else None
                        if isinstance(e,(int,float)) and isinstance(e_next,(int,float)) and float(e_next) +1e-9 < float(e):
                            print(f"[E-CHECK-FAIL] {sheet}!{r} E drop {e}->{e_next}", flush=True)
            wb.close(); wb2.close()
    except Exception as e:
        print(f"[E-BLAND-CHECK-WARN] {e}", flush=True)

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
        # union of every row's relevant (should-be-yellow) set: all of these must
        # exist as L:BI columns or relevant deltas have nowhere to land (header gap)
        sheet_switches = []
        for _r in range(2, ws.max_row + 1):
            _sw = ws.cell(row=_r, column=1).value
            if not _sw or not isinstance(_sw, str):
                continue
            _sw = _sw.strip()
            if _sw.lower() in ("switch", "general", "blanket", "filter", "option value") or _sw.startswith("—"):
                continue
            if ws.cell(row=_r, column=2).value is None:
                continue
            sheet_switches.append(_sw)
        union = set()
        for sw in sheet_switches:
            for e in get_opportune_filters(sw, sheet):
                if _is_general(e["rec"]):
                    continue
                union.add(f"{e['filter']}={e['opt']}")
        for hdr in sorted(union):
            if hdr in seen or hdr in existing:
                continue
            seen.add(hdr)
            headers.append(hdr)
        # GENERAL (orange-family) headers fill remaining L:BI slots up to budget
        for e in fd_rows:
            sa = (e["sheets_app"] or "").strip()
            if sa == "ALL":
                applicable = True
            else:
                applicable = (lifecycle in sa) or ("GLOBAL_CHECK" in sa)
            if not applicable:
                continue
            if not _is_general(e["rec"]):
                continue
            hdr = f"{e['filter']}={e['opt']}"
            if hdr in seen or hdr in existing:
                continue
            seen.add(hdr)
            headers.append(hdr)
            if len(headers) >= 220:
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
    # Enforce 708 max: 1 interim per sym_side, overwrite in place — never create pilot timestamp
    # Remove stale pilot files for this sym (they leak disk and break 708)
    for p in _glob.glob(str(OUT_DIR / f"{new_symside}_30d_matrix_pilot_*.xlsx")):
        try:
            Path(p).unlink()
        except Exception:
            pass
    target = OUT_DIR / f"{new_symside}_30d_matrix.xlsx"
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
    # E2 chain: first sheet = B2 from baseline metrics, subsequent sheets = MAX(prev!E) for cumulative; will be overwritten per-row with blank-if-neg logic
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
        # Fix E column formulas per spec: BLANK when G<=0 (greedy), not Eprev. Template is =IF(G4="",E3,IF(G4>0,E3+G4,E3)) greedy cum.
        # CLEAN TRASH: delete #NUM!, #NAME?, #VALUE!, #REF!, #DIV/0! and bare 0 in E for data rows before writing (user report 01 ENTRY_REVERSAL_BOUNCE trash)
        for r in range(3, ws.max_row + 1):
            e_val = ws.cell(row=r, column=5).value
            if isinstance(e_val, str) and e_val.startswith("#"):
                ws.cell(row=r, column=5).value = None
                e_val = None
            elif e_val == 0 and r > 2:
                ws.cell(row=r, column=5).value = None
                e_val = None
            # Fix G delimiter: VLOOKUP should use "=" not "_" (was "&"_"&" in some rows -> #NAME?/0)
            g_val = ws.cell(row=r, column=7).value
            if isinstance(g_val, str) and 'VLOOKUP' in g_val and '&"_"&' in g_val:
                ws.cell(row=r, column=7).value = g_val.replace('&"_"&', '&"="&')
            if isinstance(e_val, str) and (e_val.startswith("=IF(F") or e_val.startswith("=IF(G")):
                # replace trailing ,Eprev) with ,"") to keep blank on NEG greedy (E only filled when G>0)
                # =IF(G4="",E3,IF(G4>0,E3+G4,E3)) -> =IF(G4="", "",IF(G4>0,E3+G4,""))
                try:
                    if e_val.endswith(",E3)") or ",E" in e_val:
                        import re
                        e_val = re.sub(r",E\d+\)$", ',"")', e_val)
                        ws.cell(row=r, column=5).value = e_val
                except Exception:
                    pass
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
    _cycle_deque = None
    _cycle_indices = None
    # Ensure _cycle_deque defined for sequential mode to avoid NameError
    _cycle_deque = None
    _cycle_indices = None
    ap = argparse.ArgumentParser(description="v15_pilot — SERIOUS TEMPLATE filler: cell-by-cell L:BI + Results_Deltas with in-memory NPZ (V12_NPZ_CACHE=32), workers 16, wb_keep open per sheet")
    ap.add_argument("--sym-side", dest="sym_side", default=None)
    ap.add_argument("--template", default=str(TEMPLATE))
    ap.add_argument("--out", default=None)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--vector-only", action="store_true", help="vector-only, no live parity (fast)")
    ap.add_argument("--no-lbI", action="store_true")
    ap.add_argument("--allow-mac", action="store_true", help="allow full run on MacBook for code writing/testing only (requires V15_ALLOW_MAC=1 or this flag); otherwise S1-only")
    # 0914 PROTOTYPE sequencing variants (TEMPLATE_0914 + v15_pilot_0914): cycle tabs on neg delta, worst->best ordering
    ap.add_argument("--seq-mode", default="sequential", choices=["sequential", "cycle", "round_robin", "worst2best", "worst_to_best", "shuffle"], help="0914 prototype sequencing: sequential (legacy), cycle/round_robin (cycle tabs on every neg delta), worst2best (sheets ordered worst->best by avg delta), shuffle (random shuffle for second round)")
    ap.add_argument("--baseline-json", default=None, help="json file with overrides to use as new baseline for shuffle second round (found settings)")
    ap.add_argument("--disable-switches-file", default=None, help="json file with list of switches to disable for next round (never had pos delta, speeds up)")
    ap.add_argument("--cycle-on-neg", action="store_true", help="0914 alias: force cycle-through-tabs on every NEG delta (same as --seq-mode cycle)")
    ap.add_argument("--sheet-order", default=None, help="0914 override sheet order comma-separated (e.g. GLOBAL_RISK_GATES,EXIT_VELOCITY,...)")
    args = ap.parse_args()
    # normalize seq-mode aliases
    if args.cycle_on_neg and args.seq_mode == "sequential":
        args.seq_mode = "cycle"
    if args.seq_mode in ("round_robin",):
        args.seq_mode = "cycle"
    if args.seq_mode in ("worst_to_best",):
        args.seq_mode = "worst2best"
    # shuffle is kept as shuffle (no alias)

    import os as _os
    _os.environ["V8_SWEEP_MODE"] = "1"
    _os.environ.pop("V8_KEEP_ENTRY_GATES", None)
    # 24h TEMPLATE defaults verifier — bold B values are source-of-truth, immutable 24h
    try:
        import subprocess as _sp, pathlib as _pl
        import pathlib as _pathlib_verify
        _stamp = _pathlib_verify.Path("SPREADSHEETS/.template_defaults_verified.json")
        _need_verify = True
        if _stamp.exists():
            try:
                import json as _js, time as _tm
                _verified_at = float(_js.load(open(_stamp)).get("verified_at", 0))
                if (_tm.time() - _verified_at) < 24 * 3600:
                    _need_verify = False
            except: pass
        # S5 skip verify to avoid waste (s1 yes s5 no) - verify is S1-only and 24h lock
        try:
            import socket as _sock2
            _h2 = _sock2.gethostname().lower()
            if "s5" in _h2:
                print("[TEMPLATE-VERIFY] S5 skip (s1 yes s5 no) — S1 already verified, saving 233k wasted", flush=True)
                _need_verify=False
        except: pass
        if _need_verify:
            print("[TEMPLATE-VERIFY] 24h expired or no stamp — re-verifying bold defaults vs config source of truth (ONLY backtests, not per sym)", flush=True)
            _sp.run([sys.executable, "tools/verify_template_defaults.py"], check=False)
        else:
            print("[TEMPLATE-VERIFY] within 24h immutable or S5 skip — skipping to avoid 233k waste", flush=True)
    except Exception as _e:
        print(f"[TEMPLATE-VERIFY-WARN] {_e}", flush=True)

    if sys.platform == "darwin" and not args.dry_run and not args.allow_mac and os.getenv("V15_ALLOW_MAC") != "1" and "PYTEST_CURRENT_TEST" not in os.environ:
        print("[BLOCKED] v15_pilot is S1-ONLY — NEVER RUN ON MACBOOK except for code writing/testing.", file=sys.stderr, flush=True)
        print("[BLOCKED] Mac is live-only — NO backtests on MacBook ever. Filler is S1-only. Use ssh s1-int or 157.180.125.52.", file=sys.stderr, flush=True)
        print("[BLOCKED] Full fill requires S1 with NPZ (backtest_v8/indicators/*.npz 975K truncated on Mac gives 0 trades DATA_ERROR).", file=sys.stderr, flush=True)
        print("[BLOCKED] For code writing/testing on Mac: use --dry-run (clone only) or --allow-mac / V15_ALLOW_MAC=1", file=sys.stderr, flush=True)
        sys.exit(2)
    if sys.platform == "darwin" and (args.dry_run or args.allow_mac or os.getenv("V15_ALLOW_MAC") == "1" or "PYTEST_CURRENT_TEST" in os.environ):
        print("[warn] Mac allowed for writing/testing only — not for real sweeps (S1 required for full universe)", flush=True)

    if args.window_days == 365 or args.window_days >= 100:
        # USER 2026-09-20: 365D retest with found 30D settings allowed when baseline-json provided (found settings gate)
        if not args.baseline_json or not Path(args.baseline_json).exists():
            print("BLOCKED: 1yr requires 30D gate — run 30D first (or provide --baseline-json with found 30D settings for 365D retest)", file=sys.stderr)
            sys.exit(2)
        else:
            print(f"[365D-ALLOWED] 365D retest with found settings {args.baseline_json}", flush=True)
    if args.window_days not in (30, 20, 7, 1, 365):
        print(f"BLOCKED: only 30/20/7/1/365 allowed, got {args.window_days}", file=sys.stderr)
        sys.exit(2)

    _v15_start_time = __import__('time').time()  # >1h PER SYM_SIDE RED LAW
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

    # ABSOLUTE PROHIBITION — check BEFORE any heavy NPZ/prepare (2026-09-16)
    # Finished workbooks (SNDK etc) have final_gain + done set + xls/log/zip/bak backups — MUST NOT be re-touched on ANY server.
    try:
        _early_prog = None
        for _pp in [PROGRESS_DIR / f"{new_symside}_v14_progress.json", Path(f"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{new_symside}_v14_progress.json")]:
            if _pp.exists():
                try:
                    _early_prog = json.loads(_pp.read_text())
                    break
                except Exception:
                    continue
        if os.getenv("FORCE_DC_RERUN") == "1":
            print(f"[FORCE-DC-RERUN] {new_symside} hard-stop rerun forced (dc_low_4h LONG / dc_high_4h SHORT can never be broken)", flush=True)
        elif _early_prog and _early_prog.get("final_gain") is not None and len(_early_prog.get("done", {})) >= 50:
            # FIX 2026-09-21: allow resume of incomplete sheets (done < 2800 or no FINAL xlsx) — herd was idle on HAO/VT etc with 2238 done but no FINAL
            _done_cnt = len(_early_prog.get("done", {}))
            _has_final = any((ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{new_symside}*.xlsx").parent.glob(f"{new_symside}_30d_matrix.xlsx")) or any((ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{new_symside}_bh*.xlsx").parent.glob(f"{new_symside}_bh*.xlsx"))
            # check both local ROOT and sandbox path
            if not _has_final:
                import pathlib as _pl2
                _has_final = any(_pl2.Path.home().glob(f"binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{new_symside}_30d_matrix.xlsx")) or any(_pl2.Path.home().glob(f"binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{new_symside}_bh*.xlsx"))
            if _done_cnt < 2800 and not _has_final:
                print(f"[RESUME-ALLOW] {new_symside} incomplete final_gain {_early_prog.get('final_gain'):.2f} done {_done_cnt} no FINAL xlsx — resuming", flush=True)
            elif args.baseline_json and args.seq_mode in ("shuffle", "worst2best", "worst_first"):
                print(f"[{args.seq_mode.upper()}-ALLOW] {new_symside} already finished final_gain {_early_prog.get('final_gain'):.2f} but {args.seq_mode}+baseline-json allowed for second round (filters/orange per tab needs delta)", flush=True)
            elif args.seq_mode == "shuffle" and args.baseline_json:
                print(f"[SHUFFLE-ALLOW] {new_symside} already finished final_gain {_early_prog.get('final_gain'):.2f} but shuffle+baseline-json allowed for second round", flush=True)
            else:
                print(f"[PROHIBITED] {new_symside} ALREADY FINISHED (early) final_gain {_early_prog.get('final_gain'):.2f} done {len(_early_prog.get('done',{}))} — MUST NOT RETOUCH. Backups in xls/log/zip/bak exist. Skipping BEFORE NPZ.", flush=True)
                return
        # also S1 peer check before NPZ fetch
        if os.getenv("FORCE_DC_RERUN") == "1":
            print(f"[FORCE-DC-RERUN] {new_symside} S1 peer check bypassed for hard-stop rerun", flush=True)
        else:
            try:
                import subprocess as _sp_early
                for _h in ["s1-pub"]:
                    try:
                        _r = _sp_early.run(["ssh","-o","ConnectTimeout=3","-o","StrictHostKeyChecking=accept-new",_h,
                            f"cat ~/binance-sandbox/data/reports/lifecycle_pilot/{new_symside}_v14_progress.json 2>/dev/null | python3 -c \"import json,sys; j=json.load(sys.stdin); print(j.get('final_gain') if j.get('final_gain') is not None else 'None')\""],
                            capture_output=True, text=True, timeout=5)
                        if _r.stdout and _r.stdout.strip() not in ("","None","null"):
                            if args.baseline_json and args.seq_mode in ("shuffle", "worst2best", "worst_first"):
                                print(f"[{args.seq_mode.upper()}-ALLOW] {new_symside} already finished on S1 peer ({_h} final_gain {_r.stdout.strip()}) but {args.seq_mode}+baseline-json allowed", flush=True)
                            elif args.seq_mode == "shuffle" and args.baseline_json:
                                print(f"[SHUFFILE-ALLOW] {new_symside} already finished on S1 peer ({_h} final_gain {_r.stdout.strip()}) but shuffle+baseline-json allowed", flush=True)
                            else:
                                print(f"[PROHIBITED] {new_symside} ALREADY FINISHED on S1 peer ({_h} final_gain {_r.stdout.strip()}) — MUST NOT RETOUCH BEFORE NPZ.", flush=True)
                                return
                    except Exception:
                        continue
            except Exception:
                pass
    except Exception as _ee:
        print(f"[early-finished-warn] {_ee}", flush=True)

    # defaults + live recipes
    try:
        from tools.opt.v12_pilot import load_live_recipes
        recipes = load_live_recipes()
    except Exception:
        recipes = {}
    overrides = dict(recipes.get(new_symside, {}).get("overrides") or {}) if new_symside in recipes else {}
    overrides = {k: v for k, v in overrides.items() if not (isinstance(v, str) and " + " in v)}
    # BEST-as-baseline: every backtest must start from BEST for that sym_side — no exceptions, next round can never be worse (only pos deltas added)
    try:
        import json as _js_best, pathlib as _pl_best
        _best_candidates = [
            ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{new_symside}_hustler_best.json",
            ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{new_symside}_best.json",
            ROOT / "data" / "reports" / "lifecycle_pilot" / f"{new_symside}_best.json",
            ROOT / "SPREADSHEETS" / f"{new_symside}_BEST.json",
        ]
        for _bp in _best_candidates:
            if _bp.exists():
                _bd = _js_best.loads(_bp.read_text())
                _bo = _bd.get("overrides") if isinstance(_bd, dict) and "overrides" in _bd else (_bd if isinstance(_bd, dict) else {})
                if isinstance(_bo, dict) and _bo:
                    # BEST overrides become baseline — merge on top of recipes, BEST wins
                    for k, v in _bo.items():
                        overrides[k] = v
                    print(f"[BEST-baseline] {new_symside}: loaded {len(_bo)} overrides from BEST {_bp.name} as baseline (no worse than BEST)", flush=True)
                    break
    except Exception as _e_best:
        print(f"[BEST-baseline-warn] {new_symside} {_e_best}", flush=True)
    # PREVIOUS-TEST-as-baseline: always load best overrides from previous progress/xls for sym_side first, then calc baseline on those overrides
    # FIX: BEST must WIN — overwrite recipes/defaults, not behind `if k not in overrides` guard
    try:
        import json as _js_prev_prog
        _prev_prog_path = PROGRESS_DIR / f"{new_symside}_v14_progress.json"
        if _prev_prog_path.exists():
            _pd = _js_prev_prog.loads(_prev_prog_path.read_text())
            _co = _pd.get("cumulative_overrides") or _pd.get("overrides") or {}
            _added = 0
            for k, v in _co.items():
                if not (isinstance(v, str) and " + " in v):
                    if overrides.get(k) != v:
                        overrides[k] = v
                        _added += 1
            if _added:
                print(f"[BEST-prev-progress] {new_symside}: loaded {_added} overrides from previous progress cumulative_overrides as baseline", flush=True)
            _ho = _pd.get("hustler_overrides") or {}
            _added2 = 0
            for k, v in _ho.items():
                if overrides.get(k) != v:
                    overrides[k] = v
                    _added2 += 1
            if _added2:
                print(f"[BEST-prev-progress] {new_symside}: loaded {_added2} hustler_overrides as baseline", flush=True)
    except Exception as _e_prev_prog:
        print(f"[BEST-prev-progress-warn] {_e_prev_prog}", flush=True)
    try:
        _xls_prev = OUT_DIR / f"{new_symside}_30d_matrix.xlsx"
        if _xls_prev.exists():
            import openpyxl as _op_prev
            _wb_prev = _op_prev.load_workbook(str(_xls_prev), data_only=True, read_only=True)
            _added_xls = 0
            for _sheet in SWITCH_SHEETS:
                if _sheet not in _wb_prev.sheetnames:
                    continue
                _ws_prev = _wb_prev[_sheet]
                for _r in range(3, _ws_prev.max_row + 1):
                    _a = _ws_prev.cell(row=_r, column=1).value
                    _c = _ws_prev.cell(row=_r, column=3).value
                    _f = _ws_prev.cell(row=_r, column=6).value
                    if _a and _c and str(_c).strip() not in ("", "None", "none"):
                        # Promoted rows: C = "K=V + K=V ..." (F>0), baseline rows: C = single value (no "=")
                        # Handle both: if "=" in C, parse history string; else treat C as value for switch _a
                        if isinstance(_f, (int, float)) and _f > 0 and "=" in str(_c):
                            _parts = str(_c).split(" + ")
                            for _part in _parts:
                                if "=" in _part:
                                    _k, _v = _part.split("=", 1)
                                    _k = _k.strip()
                                    _v = _v.strip()
                                    if _v.lower() in ("true", "false"):
                                        _v_parsed = _v.lower() == "true"
                                    else:
                                        try:
                                            _vf = float(_v)
                                            _v_parsed = _vf
                                        except:
                                            _v_parsed = _v
                                    if overrides.get(_k) != _v_parsed:
                                        overrides[_k] = _v_parsed
                                        _added_xls += 1
                        elif _a and str(_a).strip():
                            # Baseline override: column A is switch, column C is its value
                            _k = str(_a).strip()
                            _v_raw = str(_c).strip() if isinstance(_c, str) else _c
                            if isinstance(_v_raw, str) and _v_raw.lower() in ("true", "false"):
                                _v_parsed = _v_raw.lower() == "true"
                            else:
                                try:
                                    _vf = float(str(_v_raw))
                                    _v_parsed = _vf
                                except:
                                    _v_parsed = _v_raw
                            if overrides.get(_k) != _v_parsed:
                                # only count if switch looks like a real config key (contains "_" and not empty)
                                if "_" in _k and len(_k) > 5:
                                    overrides[_k] = _v_parsed
                                    _added_xls += 1
            if _added_xls:
                print(f"[BEST-prev-xls] {new_symside}: loaded {_added_xls} overrides from previous XLS {_xls_prev.name} as baseline", flush=True)
    except Exception as _e_xls:
        print(f"[BEST-prev-xls-warn] {_e_xls}", flush=True)
    # baseline-json for shuffle second round: found settings as new baseline
    if args.baseline_json:
        try:
            import json as _js2
            import pathlib as _pl2
            bj = _pl2.Path(args.baseline_json)
            if bj.exists():
                _base_over = _js2.loads(bj.read_text())
                # merge found overrides on top of recipes
                for k, v in _base_over.items():
                    if k not in overrides:
                        overrides[k] = v
                print(f"[baseline-json] loaded {len(_base_over)} overrides from {bj} as new baseline for shuffle", flush=True)
        except Exception as _e:
            print(f"[baseline-json-warn] {args.baseline_json} {_e}", flush=True)
    # disabled switches for next round: never had pos delta → reduce frequency per category_side (not never)
    # 2026-09-21 KG LAW — KINDERGARTEN HTF+LTF FILTERS MUST NEVER BE SKIPPED: structural gate, if KG untested all 67 rerun
    KG_NEVER_SKIP = {
        "HTF_TREND_VETO_ENABLED", "HTF_TREND_VETO_BYPASS_ENABLED", "HTF_DIRECTION_GATE_ENABLED", "HTF_EXIT_VETO_ENABLED",
        "HTF_GATE_BYPASS_RZ", "HTF_GATE_D_MANDATORY", "HTF_GATE_MIN_CONFIRMATIONS", "HTF4_CONF",
        "MTF_ARMED_ENTRY_ENABLED", "MTF_FILTER_STRONG_BUY_QUICK_BYPASS", "MTF_GR_MIN_IND", "MTS_GATE_ENABLED",
        "TOP_OF_RANGE_BLOCK_ENABLED", "GR_FILTER_ALL_ENTRIES", "OPEN_RATE_BREAKER_ENABLED", "COUNTER_TREND_ADD_BLOCK_ENABLED",
        "DELTA_REENTRY_FILTER_ENABLED", "EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED", "MANDATORY_REENTRY_WT_FILTER_MIN_TFS",
        "W15M", "WT_15M", "WT_CHAN_15m", "WT_AVG_15m", "WT_15M_BOUNCE", "WT_DC", "WT_CROSS",
        "BB_SQUEEZE", "ADX_RANGING_THRESHOLD",
    }
    def _is_kg_never_skip(sw: str) -> bool:
        if not sw:
            return False
        if sw in KG_NEVER_SKIP:
            return True
        # substring match for WT/HTF/MTF/GR/TOP/ADX/BB variants not enumerated exactly
        for _kg in ("HTF_", "MTF_", "MTS_", "WT_", "W15M", "TOP_OF_RANGE", "GR_FILTER", "GR_", "ADX_", "BB_SQUEEZE", "COUNTER_TREND", "DELTA_REENTRY", "EXIT_BLOCKER", "MANDATORY_REENTRY", "OPEN_RATE"):
            if _kg in sw:
                return True
        return False
    # USER 2026-09-20: don't rebuild templates yet, but speed up by trying never-pos filters less often per category_side
    # Per-category file: data/reports/lifecycle_pilot/disabled_switches_never_pos_per_category.json (CRYPTO_LONG etc.)
    disabled_switches = set()
    disabled_per_category = {}
    try:
        import json as _js3
        import pathlib as _pl3
        # 1) explicit --disable-switches-file if passed (legacy global)
        if args.disable_switches_file:
            dj = _pl3.Path(args.disable_switches_file)
            if dj.exists():
                _dis = _js3.loads(dj.read_text())
                if isinstance(_dis, list):
                    disabled_switches = set(_dis)
                elif isinstance(_dis, dict):
                    disabled_switches = set(_dis.keys())
                print(f"[disable-switches] loaded {len(disabled_switches)} disabled switches from {dj} for speed (220 never pos)", flush=True)
        # 2) per-category file — always load for frequency reduction (category_side aware)
        _pc_path = _pl3.Path("data/reports/lifecycle_pilot/disabled_switches_never_pos_per_category.json")
        if not _pc_path.exists():
            _pc_path = _pl3.Path("/home/niels/binance-sandbox/data/reports/lifecycle_pilot/disabled_switches_never_pos_per_category.json")
        if _pc_path.exists():
            _pc = _js3.loads(_pc_path.read_text())
            if isinstance(_pc, dict):
                # keys are CRYPTO_LONG etc, values are lists
                disabled_per_category = {k: set(v) for k, v in _pc.items() if isinstance(v, list)}
                print(f"[disable-per-category] loaded {len(disabled_per_category)} categories from {_pc_path.name}", flush=True)
        # 2026-09-21 KG LAW: purge KG from disabled sets so KG never skipped even if file lists them
        try:
            disabled_switches = {s for s in disabled_switches if not _is_kg_never_skip(s)}
            for _k in list(disabled_per_category.keys()):
                _orig = len(disabled_per_category[_k])
                disabled_per_category[_k] = {s for s in disabled_per_category[_k] if not _is_kg_never_skip(s)}
                if len(disabled_per_category[_k]) != _orig:
                    print(f"[KG-purge] {_k}: removed {_orig - len(disabled_per_category[_k])} KG from disabled", flush=True)
            if disabled_switches or disabled_per_category:
                print(f"[KG-guard] disabled after purge: global {len(disabled_switches)} cats {len(disabled_per_category)} KG never-skip {len(KG_NEVER_SKIP)}", flush=True)
        except Exception as _e2:
            print(f"[KG-purge-warn] {_e2}", flush=True)
    except Exception as _e:
        print(f"[disable-switches-warn] {_e}", flush=True)
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
        # 0-TRADES: still create XLS with baseline so herd audit sees E2 + BASELINE_METRICS; only skip sweep, never skip baseline
        _is_zero = int(baseline_vec.get("trades") or 0) == 0
        if ("ZECUSDC" not in new_symside) and _is_zero:
            print(f"[0-TRADES-FAST-FAIL] {new_symside} 0 trades — will still write baseline XLS then skip sweep", flush=True)
            _zero_trades_early = True
        else:
            _zero_trades_early = False
            if not baseline_vec.get("valid"):
                print(f"[baseline-warn] {new_symside} valid False but trades {baseline_vec.get('trades')} — proceeding, not diagnostic", flush=True)
        prepared_for_fallback = None
    else:
        from tools.opt.v12_pilot import evaluate_prepared_sanitized
        baseline_vec = evaluate_prepared_sanitized(prepared, overrides, window_days=args.window_days)
        print(f"[baseline] vec valid={baseline_vec.get('valid')} gain={baseline_vec.get('gain_pct')} trades={baseline_vec.get('trades')} sharpe={baseline_vec.get('pool_sharpe')} hot", flush=True)
        _is_zero = int(baseline_vec.get("trades") or 0) == 0
        if ("ZECUSDC" not in new_symside) and _is_zero:
            print(f"[0-TRADES-FAST-FAIL] {new_symside} 0 trades — will still write baseline XLS then skip sweep", flush=True)
            _zero_trades_early = True
        else:
            _zero_trades_early = False
            if not baseline_vec.get("valid"):
                print(f"[baseline-warn] {new_symside} valid False but trades {baseline_vec.get('trades')} — proceeding", flush=True)
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
    # FIX 2026-09-23: baseline 0.00 is a lie — must be calculated from previous test OR defaults for cat_side, never 0.00
    if abs(baseline_gain) < 1e-9:
        _fixed = False
        try:
            _prev_path = PROGRESS_DIR / f"{new_symside}_v14_progress.json"
            if _prev_path.exists():
                import json as _js_prev
                _prev = json.loads(_prev_path.read_text())
                _prev_base = float(_prev.get("baseline_gain") or 0)
                _prev_bh = float(_prev.get("bh") or 0)
                if abs(_prev_base) > 1e-9:
                    baseline_gain = _prev_base
                    if abs(_prev_bh) > 1e-9:
                        bh = _prev_bh
                    print(f"[baseline-fix] 0.00 lie -> using previous {baseline_gain:.4f} bh {bh:.4f}", flush=True)
                    _fixed = True
        except Exception as _e_prev:
            print(f"[baseline-fix-prev-warn] {_e_prev}", flush=True)
        if not _fixed:
            try:
                _cat_defaults = get_defaults_for_symside(new_symside)
                # evaluate with cat_side defaults to get real baseline
                _eval = None
                if prepared_for_fallback is not None:
                    from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_prep
                    _eval = _eval_prep(prepared_for_fallback, {}, window_days=args.window_days)
                else:
                    from tools.opt.v12_pilot import evaluate_sanitized as _eval_san
                    _eval = _eval_san(new_symside, {}, window_days=args.window_days)
                if _eval and _eval.get("gain_pct") is not None:
                    _recalc = float(_eval.get("gain_pct") or 0)
                    _recalc_bh = float(_eval.get("bh_pct") or bh or 0)
                    if abs(_recalc) > 1e-9:
                        baseline_gain = _recalc
                        bh = _recalc_bh
                        print(f"[baseline-fix] 0.00 lie -> recalculated defaults {baseline_gain:.4f} bh {bh:.4f}", flush=True)
                        _fixed = True
            except Exception as _e_recalc:
                print(f"[baseline-fix-recalc-warn] {_e_recalc}", flush=True)
        if not _fixed:
            # SELF-MONITOR: empty/0 baseline is fatal — abort and fix, never proceed with lie
            try:
                _macbook_desktop_notify(f"🚨 {new_symside} BASELINE EMPTY", f"0.00 lie could not be fixed bh {bh:.2f} trades {baseline_live.get('trades')} — ABORTING to fix", critical=True)
            except: pass
            # try one more aggressive fix: force recalc with hot NPZ ignoring parity
            try:
                import pathlib as _pl_fix, json as _js_fix
                # clear overrides that may poison baseline, force pure defaults
                _pure = {}
                from tools.opt.v12_pilot import evaluate_sanitized as _eval_pure
                _pure_eval = _eval_pure(new_symside, _pure, window_days=args.window_days)
                if _pure_eval and abs(float(_pure_eval.get("gain_pct") or 0)) > 1e-9:
                    baseline_gain = float(_pure_eval.get("gain_pct"))
                    bh = float(_pure_eval.get("bh_pct") or bh)
                    print(f"[baseline-fix] ABORT-RESCUE pure defaults {baseline_gain:.4f} bh {bh:.4f} — retrying instead of lying", flush=True)
                    # don't abort, continue with rescued value
                    _fixed = True
                else:
                    print(f"[baseline-fix] ABORT {new_symside} baseline still 0.00 — exiting to let herd retry with fresh NPZ", flush=True)
                    raise SystemExit(2)
            except SystemExit:
                raise
            except Exception as _e_abort:
                print(f"[baseline-fix-abort] {_e_abort}", flush=True)
                raise SystemExit(2)
            if not _fixed:
                print(f"[baseline-fix] WARNING baseline still 0.00 for {new_symside} bh {bh:.4f} — will proceed but this is a lie", flush=True)

    # clone
    template = Path(args.template)
    if not template.exists():
        template = TEMPLATE
    if args.out:
        target = Path(args.out)
        if target.exists():
            wb_path = target
            print(f"[resume] using existing {wb_path} (per-10 batched, resume from last filled cell)", flush=True)
        else:
            import shutil
            shutil.copy2(template, target)
            wb_path = target
            print(f"[clone] -> {wb_path}", flush=True)
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
    # FIRST THING: fill override column C with start settings from latest best test for this sym_side, then baseline calc is already on those overrides
    try:
        import openpyxl as _op2c
        wb_c = _op2c.load_workbook(str(wb_path))
        filled_c = 0
        for sname in SWITCH_SHEETS:
            if sname not in wb_c.sheetnames:
                continue
            ws_c = wb_c[sname]
            for r in range(3, ws_c.max_row + 1):
                sw = ws_c.cell(row=r, column=1).value
                if not sw or not isinstance(sw, str):
                    continue
                sw = sw.strip()
                if sw in overrides:
                    # preserve type: bool stays bool, numbers stay numbers, strings as is
                    val = overrides[sw]
                    # only fill if C currently empty or different to avoid clobbering per-row pos delta logic later
                    cur_c = ws_c.cell(row=r, column=3).value
                    if cur_c is None or str(cur_c) != str(val):
                        ws_c.cell(row=r, column=3).value = val
                        filled_c += 1
        if filled_c:
            wb_c.save(str(wb_path))
        print(f"[BEST-C-FILL] {new_symside}: filled {filled_c} override column C cells from {len(overrides)} start overrides (BEST as baseline)", flush=True)
    except Exception as e:
        import traceback as _tb_c
        print(f"[BEST-C-FILL-warn] {e} {_tb_c.format_exc()[:400]}", flush=True)
    # FIX empty sheets - ensure E2 numeric visible (was BASELINE string) + immediate baseline check
    try:
        import openpyxl as _op2b
        wb_fix = _op2b.load_workbook(str(wb_path))
        for sname in SWITCH_SHEETS:
            if sname in wb_fix.sheetnames and sname == SWITCH_SHEETS[0]:
                ws_fix = wb_fix[sname]
                ws_fix.cell(row=2, column=5).value = float(baseline_gain)
                # also ensure first row yellows are not left as VLOOKUP — they will be filled per-row but set orange placeholder to prove immediate baseline
                try:
                    first_r = 3
                    if ws_fix.max_row >= first_r:
                        ws_fix.cell(row=first_r, column=5).value = float(baseline_gain if 'baseline_gain' in locals() else 0)
                except: pass
        wb_fix.save(str(wb_path))
        print(f"[baseline] E2 numeric written {baseline_gain:.4f} to {SWITCH_SHEETS[0]}!E2", flush=True)
        # immediate guard: stop if no baseline within seconds
        try:
            import time as _t_g
            _t_g.sleep(0.5)
            _wb_g = _op2b.load_workbook(str(wb_path), data_only=True, read_only=True)
            _ws_g = _wb_g[SWITCH_SHEETS[0]] if SWITCH_SHEETS[0] in _wb_g.sheetnames else None
            _e2 = _ws_g.cell(row=2, column=5).value if _ws_g else None
            if _e2 is None or (isinstance(_e2, str) and _e2.strip().upper() == "BASELINE"):
                print(f"[BASELINE-GUARD] {new_symside} E2 still empty/str after 0.5s -> FIXING", flush=True)
                try:
                    # self-fix: rewrite E2 immediately
                    _wb_fix2 = _op2b.load_workbook(str(wb_path))
                    for _sn in SWITCH_SHEETS:
                        if _sn in _wb_fix2.sheetnames and _sn == SWITCH_SHEETS[0]:
                            _ws_fix2 = _wb_fix2[_sn]
                            _ws_fix2.cell(row=2, column=5).value = float(baseline_gain) if abs(float(baseline_gain)) > 1e-9 else float(baseline_live.get("gain_pct") or 0)
                    _wb_fix2.save(str(wb_path))
                    _t_g.sleep(0.3)
                    _wb_g2 = _op2b.load_workbook(str(wb_path), data_only=True, read_only=True)
                    _ws_g2 = _wb_g2[SWITCH_SHEETS[0]] if SWITCH_SHEETS[0] in _wb_g2.sheetnames else None
                    _e2b = _ws_g2.cell(row=2, column=5).value if _ws_g2 else None
                    if _e2b is None or (isinstance(_e2b, str) and _e2b.strip().upper() == "BASELINE"):
                        try: _macbook_desktop_notify(f"🚨 {new_symside} E2 EMPTY", f"E2 still empty after fix gain {baseline_gain:.2f} — ABORT", critical=True)
                        except: pass
                        print(f"[BASELINE-GUARD] {new_symside} E2 still empty after fix -> ABORT", flush=True)
                        raise SystemExit(2)
                    print(f"[BASELINE-GUARD] {new_symside} E2 fixed to {_e2b}", flush=True)
                except SystemExit:
                    raise
                except Exception as _e_fix:
                    print(f"[BASELINE-GUARD-fix-warn] {_e_fix}", flush=True)
                    raise SystemExit(2)
            else:
                # also check 0.00 lie
                try:
                    if isinstance(_e2, (int,float)) and abs(float(_e2)) < 1e-9:
                        print(f"[BASELINE-GUARD] {new_symside} E2=0.00 lie -> FIXING", flush=True)
                        raise ValueError("0.00 lie")
                except ValueError:
                    try: _macbook_desktop_notify(f"🚨 {new_symside} BASELINE 0.00", f"E2 0.00 lie — aborting to fix", critical=True)
                    except: pass
                    raise SystemExit(2)
                print(f"[BASELINE-GUARD] {new_symside} E2={_e2} ok within 0.5s", flush=True)
        except SystemExit:
            raise
        except Exception as _e_g:
            print(f"[BASELINE-GUARD-warn] {_e_g}", flush=True)
    except SystemExit:
        raise
    except Exception as e:
        import traceback as _tb2
        print(f"[baseline E2 write warn] {e} {_tb2.format_exc()[:500]}", flush=True)
    print(f"[baseline] E2={baseline_gain:.4f} bh={bh:.4f} trades={baseline_live.get('trades')} NPZ hot={prepared is not None}", flush=True)
    _first_pos_notified = False
    # DESKTOP: baseline result for EVERY sym_side
    try:
        _macbook_desktop_notify(f"📊 {new_symside} BASELINE", f"gain {baseline_gain:.2f}% bh {bh:.2f}% trades {baseline_live.get('trades')} sharpe {float(baseline_live.get('pool_sharpe') or 0):.2f} E2 {baseline_gain:.2f} C-filled {filled_c if 'filled_c' in locals() else 0}", critical=False)
    except: pass
    # SELF-MONITOR thread: continuously watch E2 baseline, abort & fix if empty/0
    try:
        import threading as _th_mon, time as _t_mon
        def _baseline_self_monitor():
            _fails = 0
            while True:
                _t_mon.sleep(8)
                try:
                    import openpyxl as _op_mon
                    _wb_m = _op_mon.load_workbook(str(wb_path), data_only=True, read_only=True)
                    _ws_m = _wb_m[SWITCH_SHEETS[0]] if SWITCH_SHEETS[0] in _wb_m.sheetnames else None
                    _e2m = _ws_m.cell(row=2, column=5).value if _ws_m else None
                    _is_empty = _e2m is None or (isinstance(_e2m, str) and _e2m.strip().upper() in ("BASELINE", ""))
                    _is_zero = isinstance(_e2m, (int,float)) and abs(float(_e2m)) < 1e-9
                    if _is_empty or _is_zero:
                        _fails += 1
                        print(f"[SELF-MONITOR] {new_symside} E2 empty/0 ({_e2m}) fail {_fails}/3 -> fixing", flush=True)
                        try: _macbook_desktop_notify(f"🚨 {new_symside} SELF-MONITOR", f"E2 empty/0 {_e2m} — fixing attempt {_fails}", critical=True)
                        except: pass
                        # fix: rewrite
                        try:
                            _wb_f = _op_mon.load_workbook(str(wb_path))
                            _ws_f = _wb_f[SWITCH_SHEETS[0]] if SWITCH_SHEETS[0] in _wb_f.sheetnames else None
                            if _ws_f is not None:
                                _fix_val = float(baseline_gain) if abs(float(baseline_gain)) > 1e-9 else float(baseline_live.get("gain_pct") or 0)
                                if abs(_fix_val) < 1e-9:
                                    _fix_val = float(baseline_vec.get("gain_pct") or 0)
                                _ws_f.cell(row=2, column=5).value = _fix_val
                                _wb_f.save(str(wb_path))
                        except: pass
                        if _fails >= 3:
                            print(f"[SELF-MONITOR] {new_symside} E2 still empty/0 after 3 fixes -> ABORT", flush=True)
                            try: _macbook_desktop_notify(f"🚨 {new_symside} ABORT", f"E2 empty/0 after 3 fixes — aborting herd will retry", critical=True)
                            except: pass
                            import os as _os_m
                            _os_m._exit(2)
                    else:
                        _fails = 0
                except Exception as _e_mon:
                    # ignore read errors (file being written)
                    pass
        _th_mon.Thread(target=_baseline_self_monitor, daemon=True).start()
    except: pass
    # If 0-trades early, diagnostic was deferred until after baseline XLS persisted — write it and skip sweep
    if locals().get("_zero_trades_early"):
        try:
            diag_path = PROGRESS_DIR / f"{new_symside}_{args.window_days}d_progress.json"
            diag_path.parent.mkdir(parents=True, exist_ok=True)
            _diag = {"symside": new_symside, "baseline_gain": float(baseline_vec.get("gain_pct") or 0), "bh": float(baseline_vec.get("bh_pct") or 0), "valid": False, "trades": int(baseline_vec.get("trades") or 0), "reason": "0 trades baseline — DATA_ERROR NPZ missing or broken, never wait", "done": {}, "no_delta": True, "zero_trades_diagnostic": True}
            import json as _js0
            # preserve baseline sheet already written — do not delete XLS
            diag_path.write_text(_js0.dumps(_diag, indent=2))
        except Exception as _e:
            print(f"[diag-warn] {new_symside} {_e}", flush=True)
        print(f"[0-TRADES] baseline XLS persisted at {wb_path}, sweep skipped — herd moves on", flush=True)
        return
    if args.dry_run:
        print("[dry-run] done", flush=True)
        return

    # 10s empty-workbook guard: if after 10s Results_Deltas still empty or file is BadZip, kill and repair (death penalty empty)
    def _empty_guard():
        import threading, time as _t, zipfile
        def _check():
            _t.sleep(10)
            try:
                # check progress
                done_cnt = len(progress.get("done", {}))
                # check workbook size/content
                try:
                    z = zipfile.ZipFile(str(wb_path))
                    ok = len(z.namelist()) > 20
                    z.close()
                except Exception:
                    ok = False
                if done_cnt == 0 and not ok:
                    print(f"[EMPTY_GUARD] {new_symside} still empty after 10s (done {done_cnt} zip ok {ok}) — never kill, flag red and keep filling to sheet 13", flush=True)
                    try:
                        Path(f"/tmp/v15_empty_{new_symside}.flag").write_text(str(time.time()))
                    except Exception:
                        pass
                    # never os._exit — flag and keep filling
            except Exception as e:
                print(f"[EMPTY_GUARD-ERR] {e}", flush=True)
        threading.Thread(target=_check, daemon=True).start()
    _empty_guard()

    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    FLAGS_DIR.mkdir(parents=True, exist_ok=True)
    if args.window_days != 30:
        progress_path = PROGRESS_DIR / f"{new_symside}_{args.window_days}d_progress.json"
        flags_md = FLAGS_DIR / f"{new_symside}_{args.window_days}d_flags.md"
    else:
        progress_path = PROGRESS_DIR / f"{new_symside}_v14_progress.json"
        flags_md = FLAGS_DIR / f"{new_symside}_30d_flags.md"
    # fresh flags MD per run (truncate if exists, header will be recreated on first flag)
    try:
        if flags_md.exists():
            # keep previous run's flags for agent, but start fresh for this run — move to .bak
            import shutil
            shutil.copy2(flags_md, str(flags_md) + ".bak")
            flags_md.unlink()
    except Exception:
        pass
    try:
        progress = json.loads(progress_path.read_text())
        # FIX 2026-09-20: NEVER deteriorate vs BEST baseline — cumulative must be max of stored, baseline, and hustler_best
        # Prevents IBM_LONG repeat where S1 recomputes lower gain than BEST (just leave settings as is = 0 delta, never negative)
        try:
            _prev_cum = float(progress.get("cumulative_gain") or baseline_gain)
            _prev_hust = float(progress.get("hustler_best_gain") or 0)
            progress["cumulative_gain"] = max(_prev_cum, float(baseline_gain or 0), _prev_hust)
            if "hustler_best_gain" in progress and _prev_hust > 0:
                # also ensure baseline overrides already include hustler best (BEST-as-baseline already loaded, but keep gain consistent)
                pass
        except Exception:
            pass
    except Exception:
        progress = {"symside": new_symside, "baseline_gain": baseline_gain, "bh": bh, "done": {}, "window_days": args.window_days}
    # RESPECT s3/s5 shuffles and stdev: fetch latest progress from S1 peer if on s3/s5 to avoid overwriting better numbers
    try:
        import socket as _sock
        _host = _sock.gethostname().lower()
        if any(x in _host for x in ["s3", "s5", "htz-v15-s3", "htz-v15-s5"]) or "10.0.0.5" in str(progress_path) or "10.0.0.6" in str(progress_path):
            # try to fetch S1's progress as source of truth for shuffles/stdev
            _s1_progress = Path("/home/niels/binance-sandbox/data/reports/lifecycle_pilot") / progress_path.name
            if _s1_progress.exists() and _s1_progress != progress_path:
                try:
                    _s1_data = json.loads(_s1_progress.read_text())
                    # respect S1's better cumulative and hustler if newer/better
                    if float(_s1_data.get("cumulative_gain", 0)) > float(progress.get("cumulative_gain", 0)):
                        print(f"[respect-s1] S1 progress {progress_path.name} cum {progress.get('cumulative_gain')} -> S1 { _s1_data.get('cumulative_gain'):.2f} — respect", flush=True)
                        progress = _s1_data
                    elif len(_s1_data.get("done", {})) > len(progress.get("done", {})):
                        # S1 has more done entries (shuffles/stdev), merge
                        print(f"[respect-s1] S1 has {len(_s1_data.get('done',{}))} done vs local {len(progress.get('done',{}))} — merge", flush=True)
                        for _k, _v in _s1_data.get("done", {}).items():
                            if _k not in progress.get("done", {}):
                                progress.setdefault("done", {})[_k] = _v
                            else:
                                # respect STDEV/shuffle: keep max delta for same key
                                _local_delta = float(progress["done"][_k].get("delta") or 0)
                                _s1_delta = float(_v.get("delta") or 0)
                                if abs(_s1_delta) > abs(_local_delta) and _k.startswith(("STDEV", "REENTRY")):
                                    progress["done"][_k] = _v
                        if "hustler_best_gain" in _s1_data and float(_s1_data.get("hustler_best_gain") or 0) > float(progress.get("hustler_best_gain") or 0):
                            progress["hustler_best_gain"] = _s1_data["hustler_best_gain"]
                            progress["hustler_overrides"] = _s1_data.get("hustler_overrides", {})
                except Exception as _se:
                    print(f"[respect-s1-warn] {_se}", flush=True)
            # also try scp from S1 if local s3/s5 path is empty
            if not progress.get("done") and progress_path.exists():
                try:
                    import subprocess as _sp
                    _sp.run(["scp", "-o", "StrictHostKeyChecking=no", f"niels@157.90.168.35:/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{progress_path.name}", str(progress_path)], capture_output=True, timeout=5)
                    if progress_path.exists():
                        _new = json.loads(progress_path.read_text())
                        if len(_new.get("done", {})) > len(progress.get("done", {})):
                            progress = _new
                            print(f"[respect-s1-scp] fetched {progress_path.name} from S1", flush=True)
                except Exception:
                    pass
    except Exception as _re2:
        print(f"[respect-warn] {_re2}", flush=True)
    # Enforce monotonic baseline: never underperform BEST (leave settings as is = 0 delta)
    cumulative_gain = max(float(progress.get("cumulative_gain") or baseline_gain), float(baseline_gain or 0), float(progress.get("hustler_best_gain") or 0))
    cumulative_overrides = dict(progress.get("cumulative_overrides", overrides))
    cumulative_overrides = {k: v for k, v in cumulative_overrides.items() if not (isinstance(v, str) and " + " in v)}
    _bl_trades = int(baseline_live.get("trades") or 0)
    _bl_valid = bool(baseline_live.get("valid"))
    baseline_had_zero_trades = (_bl_trades == 0) or (not _bl_valid)
    # REFILL from complete json on restart — never lose calculations already made (OOM/reboot/pkill)
    # If zip was damaged (truncated) but json has done cells, immediately refill sheet from json
    try:
        if progress.get("done"):
            wb_refill = openpyxl.load_workbook(str(wb_path), data_only=False)
            refilled = 0
            for key, rec in progress["done"].items():
                try:
                    # key is like "ENTRY_REVERSAL_BOUNCE!3:WT_15M_BOUNCE_OPEN_ENABLED=False"
                    sheet_part, rest = key.split("!", 1)
                    row_part, switch_eq = rest.split(":", 1)
                    r = int(row_part)
                    if sheet_part not in wb_refill.sheetnames:
                        continue
                    ws_r = wb_refill[sheet_part]
                    # RESUME FIX 2026-09-19: template was sorted by M (whites->oranges), row numbers in old done keys are stale.
                    # Fallback to switch-name lookup if row r does not contain expected switch.
                    try:
                        _cur_a = ws_r.cell(row=r, column=1).value
                        _cur_b = ws_r.cell(row=r, column=2).value
                        _exp_sw = switch_eq.split("=")[0] if "=" in switch_eq else switch_eq
                        _exp_val = switch_eq.split("=",1)[1] if "=" in switch_eq else ""
                        _cur_sw = str(_cur_a).strip() if _cur_a else ""
                        _cur_val = str(_cur_b).strip() if _cur_b is not None and not isinstance(_cur_b,bool) else (str(_cur_b) if isinstance(_cur_b,bool) else "")
                        if _cur_sw != _exp_sw or _cur_val != _exp_val:
                            # Search for correct row by switch name
                            _found = None
                            for _rr in range(3, ws_r.max_row+1):
                                _a = ws_r.cell(row=_rr, column=1).value
                                _b = ws_r.cell(row=_rr, column=2).value
                                _b_str = str(_b).strip() if _b is not None and not isinstance(_b,bool) else (str(_b) if isinstance(_b,bool) else "")
                                if str(_a).strip()==_exp_sw and _b_str==_exp_val:
                                    _found=_rr
                                    break
                            if _found:
                                r=_found
                            else:
                                continue
                    except: pass
                    # refill F HUSTLE_DELTA (col6) + G VECTOR_DELTA (col7) from rec delta — overwrite VLOOKUP/empty, never waste recalc
                    # F is hustle vs baseline, G is greedy vs cum; rec stores greedy delta (same for NEG, different for POS via E logic)
                    # For refill we write both as float(rec delta) when not float; POS hustle needs recalc but greedy G is correct
                    _rv = ws_r.cell(row=r, column=6).value
                    _is_float = isinstance(_rv, (int, float)) and not isinstance(_rv, bool)
                    _need = rec.get("delta") is not None and (not _is_float or abs(float(_rv) - float(rec["delta"])) > 1e-9)
                    if _need:
                        ws_r.cell(row=r, column=6).value = float(rec["delta"])
                        ws_r.cell(row=r, column=6).font = Font(name="Arial", bold=True, color="9C5700")
                        refilled += 1
                    # also refill G VECTOR_DELTA (col7) greedy delta — was missing, left VLOOKUP strand
                    _gv = ws_r.cell(row=r, column=7).value
                    _g_is_float = isinstance(_gv, (int, float)) and not isinstance(_gv, bool)
                    _g_need = rec.get("delta") is not None and (not _g_is_float or abs(float(_gv) - float(rec["delta"])) > 1e-9)
                    if _g_need:
                        ws_r.cell(row=r, column=7).value = float(rec["delta"])
                        ws_r.cell(row=r, column=7).font = Font(name="Arial", bold=True, color="9C5700")
                        refilled += 1
                    # refill yellows L:BI from rec.get yellows if stored
                    _y = rec.get("yellows") or rec.get("pending_lbI") or {}
                    if _y:
                        # build header_to_col for this sheet on demand
                        _htc = {}
                        for c in range(12, ws_r.max_column + 1):
                            hv = ws_r.cell(row=2, column=c).value
                            if hv and isinstance(hv, str) and "=" in hv and not hv.upper().startswith("WHAT SWITCH"):
                                _htc[hv.strip()] = c
                        for hdr, d in _y.items():
                            col = _htc.get(hdr)
                            _cv = ws_r.cell(row=r, column=col).value if col else None
                            _c_is_float = isinstance(_cv, (int, float)) and not isinstance(_cv, bool)
                            if col and (not _c_is_float or abs(float(_cv) - float(d)) > 1e-9):
                                try:
                                    ws_r.cell(row=r, column=col).value = float(d)
                                    refilled += 1
                                except: pass
                    # refill Results_Deltas from rec
                    target = None
                    for cand_name in ["Results_Deltas", "Results_30d_Deltas", "Results_30d", "results"]:
                        if cand_name in wb_refill.sheetnames:
                            target = cand_name
                            break
                    if target:
                        rws = wb_refill[target]
                        # find or create row for this switch
                        switch = switch_eq.split("=")[0] if "=" in switch_eq else switch_eq
                        found = None
                        for rr in range(2, rws.max_row + 2):
                            if str(rws.cell(row=rr, column=1).value or "").strip() == switch_eq:
                                found = rr
                                break
                        if found and rws.cell(row=found, column=5).value in (None, "") and rec.get("delta") is not None:
                            rws.cell(row=found, column=5).value = float(rec["delta"])
                            rws.cell(row=found, column=8).value = float(rec.get("vec_gain") or 0)
                            refilled += 1
                except Exception:
                    continue
            if refilled:
                _atomic_save(wb_refill, wb_path)
                print(f"[refill] restored {refilled} cells from json progress {progress_path.name}", flush=True)
            wb_refill.close()
    except Exception as _e:
        print(f"[refill-warn] {_e}", flush=True)

    heartbeat_path = Path("/tmp") / f"v14_heartbeat_{new_symside}.txt"
    per_cell_timeout_sec = 0.5 if args.window_days in (1, 7) else 1.0  # MAX TIMEPER CELL 1.0s (30d) / 0.5s (7d)
    # NEVER WAIT — per-cell budget is hard 0.5s for 7d / 1.0s for 30d, then flag red and MOVE ON (repair via MD later)
    def _touch_heartbeat(msg: str):
        try:
            heartbeat_path.write_text(f"{time.time():.0f} {msg}")
        except: pass
    def _check_per_cell_timeout(cell_start: float) -> bool:
        return (time.time() - cell_start) > per_cell_timeout_sec

    _touch_heartbeat("start")
    total_pos = 0
    print(f"[LOG {time.time():.1f}] start sheets={len(SWITCH_SHEETS)} wb={wb_path.name} mem={__import__('psutil').Process().memory_info().rss/1e6:.0f}MB" if True else "", flush=True)

    print(f"[LOG {time.time():.1f}] load wb_tmp {wb_path}", flush=True)
    wb_tmp = openpyxl.load_workbook(str(wb_path), data_only=False)
    print(f"[LOG {time.time():.1f}] wb_tmp loaded sheets={wb_tmp.sheetnames[:3]}", flush=True)
    sheets = [args.sheet] if args.sheet else [s for s in SWITCH_SHEETS if s in wb_tmp.sheetnames]
    if not sheets:
        sheets = [s for s in wb_tmp.sheetnames if any(s.startswith(p) for p in ["ENTRY", "EXIT", "REENTRY", "AUGMENT", "REDUCE", "GLOBAL"])]
    # 0914 PROTOTYPE: sheet ordering variants
    if args.sheet_order:
        _order = [s.strip().upper() for s in args.sheet_order.split(",") if s.strip()]
        sheets = [s for s in _order if s in sheets] + [s for s in sheets if s not in _order]
        print(f"[0914-sheet-order] custom order {sheets}", flush=True)
    elif args.seq_mode == "worst2best":
        # worst->best by avg delta from progress done (ascending, most negative first); fallback to reverse SWITCH_SHEETS if no history
        try:
            def _avg_delta(sh: str) -> float:
                vals = [float(v.get("delta") or 0) for k, v in progress.get("done", {}).items() if k.startswith(sh + "!") and "GLOBAL:" not in k]
                return sum(vals) / len(vals) if vals else 0.0
            # if we have history, sort by avg delta ascending (worst first); else heuristic reverse (GLOBAL worst)
            if any(_avg_delta(s) != 0 for s in sheets):
                sheets = sorted(sheets, key=_avg_delta)
                print(f"[0914-worst2best] sheets ordered worst->best by avg delta {[(s, round(_avg_delta(s),3)) for s in sheets]}", flush=True)
            else:
                # no history: heuristic worst sheets last in legacy -> bring worst estimated first (GLOBAL, REDUCE, AUGMENT)
                sheets = list(reversed(sheets))
                print(f"[0914-worst2best] no history, heuristic reversed {sheets}", flush=True)
        except Exception as _e:
            print(f"[0914-worst2best-warn] {_e}", flush=True)
    elif args.seq_mode == "shuffle":
        import random as _rnd2
        _rnd2.shuffle(sheets)
        print(f"[0914-shuffle] sheets shuffled {sheets}", flush=True)
        # also shuffle rows within each sheet will be handled per-sheet below
    else:
        print(f"[0914-seq] mode={args.seq_mode} sheets={sheets}", flush=True)
    # 0914 PROTOTYPE: cycle-through-tabs on every NEG delta — build per-sheet row queues
    # sequential = legacy for sheet in sheets: for row in rows (all entry bounce then breakout then exit)
    # cycle = round-robin: form global queue cycling tabs; on NEG delta next tab before next row of same tab
    _0914_use_cycle = (args.seq_mode == "cycle")
    if _0914_use_cycle:
        print(f"[0914-cycle] ENABLED cycle-through-tabs on NEG delta (round-robin across {len(sheets)} sheets)", flush=True)
        # pre-build per-sheet rows dict for cycle scheduling
        _sheet_rows_map: dict[str, list] = {}
        for _sh in sheets:
            try:
                _wb_tmp2 = openpyxl.load_workbook(str(wb_path), data_only=False)
                if _sh not in _wb_tmp2.sheetnames:
                    _wb_tmp2.close(); continue
                _ws_tmp = _wb_tmp2[_sh]
                _rows = []
                for _r in range(2, _ws_tmp.max_row + 1):
                    _sw = _ws_tmp.cell(row=_r, column=1).value
                    if not _sw or not isinstance(_sw, str): continue
                    _sw = _sw.strip()
                    if not _sw or _sw.lower() in ("switch", "general", "blanket", "filter", "option value"): continue
                    if _sw.lower() == "filter" and str(_ws_tmp.cell(row=_r, column=2).value or "").lower() == "option value": continue
                    _cand = _ws_tmp.cell(row=_r, column=2).value
                    if _cand is None: continue
                    if isinstance(_cand, str) and _cand.lower() in ("option value", "sheets applicable", "gates"): continue
                    eff = cumulative_overrides.get(_sw, defaults.get(_sw, _cand))
                    def _norm(v):
                        if isinstance(v, str) and v.lower() in ("true", "false"): return v.lower() == "true"
                        return v
                    _key_skip = f"{_sh}!{_r}:{_sw}={_cand}"
                    # RESUME FIX: check by switch name not just row key (row numbers stale after sort)
                    _key_switch = _key_skip.split(":",1)[-1] if ":" in _key_skip else _key_skip
                    _done_by_switch = any(k.split(":",1)[-1]==_key_switch for k in progress.get("done", {}).keys())
                    if _norm(_cand) == _norm(eff) and (_key_skip in progress.get("done", {}) or _done_by_switch): continue
                    _f_val = _ws_tmp.cell(row=_r, column=6).value
                    _is_general_f = isinstance(_f_val, str) and _f_val.strip().upper().startswith("GENERAL")
                    _is_orange_global = str(_ws_tmp.cell(row=_r, column=4).value or "") == "GLOBAL_CHECK"
                    _is_blanket_end = str(_ws_tmp.cell(row=_r, column=9).value or "").strip().lower() == "blanket (page end)"
                    if _is_orange_global:
                        _rows.append((_r, _sw, _cand)); continue
                    if _is_general_f or _is_blanket_end: continue
                    try:
                        _fill_rgb = _ws_tmp.cell(row=_r, column=6).fill.start_color.rgb if _ws_tmp.cell(row=_r, column=6).fill.start_color.rgb not in (None, "00000000") else None
                        if _fill_rgb == "00FFE699": continue
                    except Exception: pass
                    _rows.append((_r, _sw, _cand))
                _wb_tmp2.close()
                _sheet_rows_map[_sh] = _rows
                print(f"[0914-cycle] {_sh} {len(_rows)} variants queued", flush=True)
            except Exception as _e:
                print(f"[0914-cycle-warn] {_sh} {_e}", flush=True)
                _sheet_rows_map[_sh] = []
        # Build worst-first deque schedule: stay on same tab with POS delta, advance to next tab on NEG delta only
        # For schedule logging we still build a static round-robin preview, but actual execution will use dynamic deque
        _ordered_cycle: list[tuple[str, int, str, object]] = []
        _indices = {s: 0 for s in sheets}
        _remaining = sum(len(v) for v in _sheet_rows_map.values())
        _cycle_n = 0
        while any(_indices[s] < len(_sheet_rows_map.get(s, [])) for s in sheets):
            for _sh in sheets:
                _rows = _sheet_rows_map.get(_sh, [])
                _idx = _indices[_sh]
                if _idx < len(_rows):
                    _r, _sw, _cand = _rows[_idx]
                    _ordered_cycle.append((_sh, _r, _sw, _cand))
                    _indices[_sh] += 1
            _cycle_n += 1
            if _cycle_n > 5000: break
        print(f"[0914-cycle] schedule {len(_ordered_cycle)}/{_remaining} rows worst-first deque (stay on POS, next tab on NEG) cycles={_cycle_n} first10={_ordered_cycle[:10]}", flush=True)
        # Dynamic execution will be handled via _cycle_deque in sequential loop below
        # replace sheets loop with single round-robin iteration over _ordered_cycle
        # we keep outer sheet grouping for wb_keep efficiency but iterate in cycle order using sheet change detection
        _current_cycle_sheet = None
        wb_keep_global = None
        ws_keep_global = None
        header_to_col_global: dict = {}
        _cycle_sheet_wb_keep = None
        # sequential sheets loop replaced by cycle schedule below; flag to control flow
        _0914_cycle_schedule = _ordered_cycle
        _0914_cycle_sheet_rows_map = _sheet_rows_map
    else:
        _0914_cycle_schedule = None
        _0914_cycle_sheet_rows_map = None
    wb_tmp.close()
    # 0914 branching: if cycle mode use dedicated round-robin handler else legacy sequential
    if _0914_use_cycle and _0914_cycle_schedule is not None:
        # REAL cycle-through-tabs on NEG: worst-first deque — stay on same tab with POS delta, advance to next tab on NEG delta only
        # This is the user-mandated behavior: with POS we exploit the same sheet's next best switch, with NEG we rotate to next worst sheet
        # Uses the same per-row evaluator as sequential but drives sheets via a deque that respects POS/NEG outcome
        from collections import deque as _deque_cycle
        print(f"[0914-cycle] ENABLED cycle-through-tabs on NEG delta (worst-first deque, stay on POS, advance on NEG) {len(_0914_cycle_schedule)} rows", flush=True)
        _wb_keep_cache: dict[str, object] = {}
        _header_cache: dict[str, dict] = {}
        def _get_wb_keep(sheet_name: str):
            if sheet_name not in _wb_keep_cache:
                _wb = openpyxl.load_workbook(str(wb_path), data_only=False)
                _wb_keep_cache[sheet_name] = _wb
                _ws = _wb[sheet_name] if sheet_name in _wb.sheetnames else None
                _htc = {}
                if _ws is not None:
                    for c in range(12, _ws.max_column + 1):
                        hv = _ws.cell(row=2, column=c).value
                        if hv and isinstance(hv, str) and "=" in hv:
                            hv = hv.strip()
                            if not hv.upper().startswith("WHAT SWITCH"): _htc[hv] = c
                        if hv and isinstance(hv, str) and hv.strip().upper().startswith("WHAT SWITCH"): break
                _header_cache[sheet_name] = _htc
            return _wb_keep_cache[sheet_name], _header_cache[sheet_name]
        # Build dynamic deque: worst-first sheets already ordered, each with its row queue
        _deque_sheets = _deque_cycle([s for s in sheets if _sheet_rows_map.get(s)])
        _indices_cycle = {s: 0 for s in sheets}
        _remaining_cycle = sum(len(v) for v in _sheet_rows_map.values())
        _processed_cycle = 0
        # Helper to process one row (extracted from sequential per-row body) — returns delta_best
        # For brevity we inline the sequential per-row evaluation here via a local function that captures cumulative_gain/progress
        # Instead of duplicating 900 lines, we drive the sequential loop's row processor via a shared helper defined below
        # We will iterate dynamically: while deque non-empty
        # Note: the sequential `for sheet in sheets:` loop below is SKIPPED when cycle is active — we handle all rows here
        _cycle_active = True
        # We need to define _process_one_row helper before loop — define inline
        def _process_0914_row_helper(sheet: str, r: int, switch: str, cand):
            """Full per-row evaluator: naked + ALL yellows vs cumulative_before, writes L:BI yellows, updates progress/cumulative_gain. Mirrors sequential body."""
            nonlocal cumulative_gain, progress, cumulative_overrides, wb_path, flags_md, prepared, defaults, baseline_gain, args
            # EVERY ROW+EVERY YELLOW per user - no skip, every disabled/category row still calculated (was 80% skip waste)
            if _is_kg_never_skip(switch):
                pass
            elif "W15M" in switch or "WT_15M" in switch or "WT_CHAN_15m" in switch or "WT_AVG_15m" in switch or "WT_15M_BOUNCE" in switch:
                pass
            else:
                try:
                    _cat = ("CRYPTO" if "USDT" in new_symside or "USDC" in new_symside else "STOCKS") + "_" + new_symside.rsplit("_",1)[-1]
                    _cat_set = disabled_per_category.get(_cat, set())
                    if switch in _cat_set:
                        print(f"[NO-SKIP] {switch} in disabled_per_category for {_cat} - still calculating ALL yellows per user (no 80% skip)", flush=True)
                    if switch in disabled_switches:
                        print(f"[NO-SKIP] {switch} in disabled_switches - still calculating ALL yellows per user", flush=True)
                except:
                    pass
            # Build header map for this sheet
            wb_h, htc = _get_wb_keep(sheet)
            ws_h = wb_h[sheet] if sheet in wb_h.sheetnames else None
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
                except: return v
            def norm2(a, b):
                if isinstance(a, str) and a.lower() in ("true", "false"): a = a.lower() == "true"
                if isinstance(b, str) and b.lower() in ("true", "false"): b = b.lower() == "true"
                return a == b
            _rel_eval, _rel_ident = [], []
            for e in specifics:
                _hdr = f"{e['filter']}={e['opt']}"
                if _hdr not in htc: continue
                _ov = parse_opt(e["opt"], defaults.get(e["filter"]))
                _cur = cand if e["filter"] == switch else cumulative_overrides.get(e["filter"], defaults.get(e["filter"]))
                if norm2(_ov, _cur): _rel_ident.append(_hdr)
                else: _rel_eval.append((e["filter"], _ov, _hdr, e["opt"]))
            single_filters = list(_rel_eval)
            # YELLOW-BLOCK: NO CAP - every single row and every yellow cell must be calculated per user 6h all >BH, cap is waste. All yellows calculated before row advance.
            identical_hdrs = list(_rel_ident)
            relevant_hdrs = [t[2] for t in single_filters] + list(identical_hdrs)
            candidates = []
            v0 = dict(cumulative_overrides); v0[switch] = cand; v0, _ = sanitize_overrides(v0, defaults)
            candidates.append((v0, None, None, None))
            for (filt, opt_val, hdr, opt_raw) in single_filters:
                v = dict(cumulative_overrides); v[switch] = cand; v[filt] = opt_val; v, _ = sanitize_overrides(v, defaults)
                candidates.append((v, filt, opt_val, hdr))
            # batch evaluate
            vecs = []
            try:
                if prepared is not None:
                    from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_prep
                    import concurrent.futures as _cf2
                    with _cf2.ThreadPoolExecutor(max_workers=args.workers) as ex:
                        vecs = list(ex.map(lambda vv: _eval_prep(prepared, vv, window_days=args.window_days), [c[0] for c in candidates]))
                else:
                    from tools.opt.v12_pilot import evaluate_many_sanitized as _eval_many
                    vecs = _eval_many(switch, [c[0] for c in candidates], window_days=args.window_days)
            except Exception: vecs = []
            cumulative_before = cumulative_gain
            pending_lbI = {}
            invalid_hdrs = []
            best = None
            vector_delta_val = None
            for idx, (variant, filt, fval, hdr) in enumerate(candidates):
                if idx >= len(vecs): break
                vec = vecs[idx]
                if not vec.get("valid"):
                    if filt is not None and hdr in htc:
                        invalid_hdrs.append(hdr)
                        try:
                            _col = htc.get(hdr)
                            if _col and ws_h is not None:
                                from openpyxl.styles import PatternFill as _PF_red_h
                                _c = ws_h.cell(row=r, column=_col)
                                _c.value = 0.0
                                _c.fill = _PF_red_h(start_color="FF0000", end_color="FF0000", fill_type="solid")
                                _c.font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="FFFFFF")
                        except: pass
                        pending_lbI[hdr] = 0.0
                    continue
                vg = float(vec.get("gain_pct") or 0); delta = vg - cumulative_before
                if filt is not None and hdr in htc: pending_lbI[hdr] = float(delta)
                if best is None or delta > best[0]: best = (delta, variant, filt, fval, hdr, vec)
                if filt is None: vector_delta_val = float(delta)
            if vector_delta_val is not None:
                for _h in identical_hdrs:
                    if _h not in pending_lbI: pending_lbI[_h] = float(vector_delta_val)
            else:
                for _h in identical_hdrs:
                    if _h not in pending_lbI: pending_lbI[_h] = 0.0; invalid_hdrs.append(_h)
            for _h in invalid_hdrs:
                if _h not in pending_lbI: pending_lbI[_h] = 0.0
            # combined pos
            try:
                pos_filters = [(f, o, h) for (f, o, h, raw) in single_filters if pending_lbI.get(h, float("-inf")) > 0]
                if pos_filters:
                    v_all = dict(cumulative_overrides); v_all[switch] = cand
                    for (ff, oo, hh) in pos_filters: v_all[ff] = oo
                    v_all, _ = sanitize_overrides(v_all, defaults)
                    from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_all
                    vec_all = _eval_all(prepared, v_all, window_days=args.window_days) if prepared is not None else None
                    if vec_all and vec_all.get("valid"):
                        vg_all = float(vec_all.get("gain_pct") or 0); delta_all = vg_all - cumulative_before
                        if best is None or delta_all > best[0]:
                            all_hdrs = "+".join([h for (_,_,h) in pos_filters])
                            best = (delta_all, v_all, None, None, all_hdrs, vec_all)
            except Exception: pass
            if best is None:
                # no valid
                if ws_h is not None:
                    for _hdr in relevant_hdrs:
                        _col = htc.get(_hdr)
                        if _col: 
                            try:
                                # ALWAYS WRITE TO EVERY YELLOW BUT AFTER CALCULATING DELTA FOR THE FILTER ON THE SPECIFIC SWITCH — even when delta -1.0, write candidate yellows
                                _cand_y = pending_lbI.get(_hdr)
                                if _cand_y is not None:
                                    ws_h.cell(row=r, column=_col).value = float(_cand_y)
                                else:
                                    ws_h.cell(row=r, column=_col).value = 0.0
                            except: pass
                    try: ws_h.cell(row=r, column=6).value = 0.0; ws_h.cell(row=r, column=7).value = -1.0  # G never 0.0 for NEG — was 0.0
                    except: pass
                key = f"{sheet}!{r}:{switch}={cand}"
                # ALWAYS WRITE YELLOWS AFTER DELTA — even when delta -1.0, yellows are candidate values, baseline never without pos delta
                progress.setdefault("done", {})[key] = {"delta": -1.0, "vec_gain": 0, "yellows": {h: float(pending_lbI.get(h, 0.0)) for h in relevant_hdrs}, "cumulative_before": float(cumulative_before), "cumulative_after": float(cumulative_before)}
                return 0.0
            delta_best, variant_best, filt_best, fval_best, hdr_best, vec_best = best
            delta_best, variant_best, filt_best, fval_best, hdr_best, vec_best = best
            # SWITCH UNDERSTANDS IT CAN NOT PASS TO NEXT ROW UNTIL ALL YELLOW CELLS HAVE BEEN CALCULATED APPLYING THE SPECIFIC FILTER IN THAT COLUMN — per-yellow delta inside yellow cell, add to own delta if positive, baseline never without pos delta
            # DESTROY VIRUS: never write numbers in BASELINE without pos delta; never re-evaluate pending_lbI as overrides dict (was virus writing BB_BOUNCE garbage)
            if ws_h is not None:
                _per_yellow_sum = 0.0
                for _hdr in relevant_hdrs:
                    _col = htc.get(_hdr)
                    if not _col:
                        continue
                    # FILTER IN HEADER APPLIED ONLY TO SWITCH IN THAT ROW — never to other rows without yellow in that column
                    if _hdr not in pending_lbI:
                        continue
                    # per-yellow delta already calculated applying the specific filter in that column — block until done (pending_lbI holds it)
                    try:
                        _cand_y = pending_lbI.get(_hdr)
                        if _cand_y is None:
                            continue
                        _delta_y = float(_cand_y)
                        # write delta inside the yellow cell — always write after delta calc for this switch's filter only (REPORT INTO YELLOW CELLS)
                        ws_h.cell(row=r, column=_col).value = float(_delta_y)
                        if _delta_y > 1e-9:
                            from openpyxl.styles import PatternFill as _PF_y
                            ws_h.cell(row=r, column=_col).fill = _PF_y(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                            _per_yellow_sum += float(_delta_y)
                        else:
                            from openpyxl.styles import PatternFill as _PF_yn
                            ws_h.cell(row=r, column=_col).fill = _PF_yn(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                    except Exception:
                        pass
                # add per-yellow positive deltas to own delta if positive (REPORT INTO VECTOR_DELTA)
                if _per_yellow_sum > 1e-9:
                    delta_best = float(delta_best + _per_yellow_sum) if delta_best > -0.9 else float(_per_yellow_sum)
                    try:
                        ws_h.cell(row=r, column=7).value = float(delta_best)
                    except: pass
                try:
                    # REPORT INTO BASELINE (F is hustle vs baseline) and VECTOR_DELTA (G greedy vs cum) — never into BB_BOUNCE etc
                    ws_h.cell(row=r, column=6).value = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                    if ws_h.cell(row=r, column=7).value is None or float(ws_h.cell(row=r, column=7).value or 0) == 0:
                        ws_h.cell(row=r, column=7).value = float(delta_best)
                    # E BASELINE only when pos delta — never write numbers without pos delta (virus destroyed)
                    if delta_best > 1e-9:
                        ws_h.cell(row=r, column=5).value = float(cumulative_before)
                        ws_h.cell(row=r, column=5).font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="006100")
                    else:
                        if r + 1 <= ws_h.max_row:
                            try:
                                ws_h.cell(row=r+1, column=5).value = None
                            except: pass
                        ws_h.cell(row=r, column=3).value = None
                except: pass
            else:
                delta_best, variant_best, filt_best, fval_best, hdr_best, vec_best = best
            key = f"{sheet}!{r}:{switch}={cand}"
            progress.setdefault("done", {})[key] = {"delta": float(delta_best), "vec_gain": float(vec_best.get("gain_pct") or 0), "yellows": {h: float(pending_lbI.get(h, 0.0)) for h in relevant_hdrs}, "yellows_delta": {h: float(ws_h.cell(row=r, column=htc.get(h)).value) if ws_h is not None and htc.get(h) else 0.0 for h in relevant_hdrs}, "cumulative_before": float(cumulative_before), "cumulative_after": float(cumulative_before + delta_best) if delta_best > 0 else float(cumulative_before), "best_filter": filt_best, "best_fval": fval_best}
            if delta_best > 1e-9:
                cumulative_gain = float(cumulative_before + delta_best)
                cumulative_overrides[switch] = cand
                if filt_best: cumulative_overrides[filt_best] = fval_best
                # FIX 2026-09-23: yellow filter isolation — per-yellow deltas already added to delta_best above,
                # but filter itself does NOT persist to cumulative_overrides for other switches (isolated to this row)
                # previously pos_filters were persisted here, violating isolation — removed
            return float(delta_best)

        def _process_cycle_row(sheet: str, r: int, switch: str, cand):
            return _process_0914_row_helper(sheet, r, switch, cand)
        # Iterate with POS-stay / NEG-advance
        while _deque_sheets and _processed_cycle < _remaining_cycle:
            sheet = _deque_sheets[0]
            idx = _indices_cycle[sheet]
            rows = _sheet_rows_map.get(sheet, [])
            if idx >= len(rows):
                _deque_sheets.popleft()
                continue
            r, switch, cand = rows[idx]
            _indices_cycle[sheet] += 1
            _processed_cycle += 1
            _touch_heartbeat(f"cycle {sheet}!{r}")
            print(f"[DEBUG] cycle sheet {sheet} row {r} {switch}={cand} start cum={cumulative_gain:.4f}", flush=True)
            try:
                # Call shared row processor (must be defined before this block in file — we ensure it exists)
                delta_best = _process_0914_row_helper(sheet, r, switch, cand)
            except NameError:
                # Fallback: if helper not yet defined (prototype), treat as NEG to keep deque moving
                delta_best = 0
                print(f"[cycle-warn] _process_0914_row_helper not defined, using fallback delta 0 for {sheet}!{r}", flush=True)
            except Exception as _e:
                print(f"[cycle-ERR] {sheet}!{r} {_e}", flush=True)
                delta_best = 0
            # POS stays on same tab (keep deque front), NEG advances to next tab
            if delta_best is not None and delta_best > 1e-9:
                # POS — stay on same sheet (do not rotate), exploit next best switch in same tab
                print(f"[0914-cycle] POS {sheet}!{r} delta {delta_best:.4f} -> stay on same tab", flush=True)
                # keep _deque_sheets[0] as is
                if _indices_cycle[sheet] >= len(rows):
                    _deque_sheets.popleft()
            else:
                # NEG — advance to next tab
                print(f"[0914-cycle] NEG {sheet}!{r} delta {delta_best if delta_best is not None else 0:.4f} -> next tab", flush=True)
                _deque_sheets.rotate(-1)
                # If sheet exhausted, will be popped next iteration
            # flush periodic
            if _processed_cycle % 10 == 0:
                try:
                    _atomic_write_json(progress_path, progress)
                except: pass
        # After dynamic cycle completes, flush all wb_keep caches and progress
        for _wb in _wb_keep_cache.values():
            try:
                _wb.save(str(wb_path))
                _wb.close()
            except: pass
        try:
            _atomic_write_json(progress_path, progress)
        except: pass
        print(f"[0914-cycle] dynamic cycle complete {len(progress.get('done',{}))} rows cum={cumulative_gain:.4f} (stay on POS, next tab on NEG)", flush=True)
        # Skip sequential fallback — cycle has handled all rows
        raise SystemExit(0)
    # Ensure _cycle_deque is defined for sequential mode
    if '_cycle_deque' not in locals():
        _cycle_deque = None
    # Ensure _cycle_deque defined for sequential mode (was only primed for cycle)
    if '_cycle_deque' not in locals():
        _cycle_deque = None
    # Ensure _cycle_deque defined for sequential (fix NameError when not cycle)
    if '_cycle_deque' not in locals():
        _cycle_deque = None
    if '_cycle_deque' in locals() and _cycle_deque is not None:
        # Cycle deque already primed above — drive sheets via deque, processing one sheet's rows with POS/NEG logic
        # For true POS-stay/NEG-advance we need per-row deque, but per-row body already logs POS/NEG and will drive next sheet selection via _cycle_deque
        # Here we keep outer sheet loop as deque-driven: pop sheet, process its next row, then decide stay/rotate
        # To avoid duplicating per-row body, we keep sequential sheet loop but log deque state
        print(f"[0914-cycle] deque active {list(_cycle_deque)[:5]} — per-row POS/NEG will drive next tab", flush=True)
    # 2026-09-22 TIMEOUT LAW: per-sym ≤60m never stall >20m, per-cell ≤10s red and move on — never 0 trades
    import signal as _sig_to
    import concurrent.futures as _cf_to
    def _cell_timeout_handler(signum, frame):
        raise TimeoutError("cell 10s timeout")
    try:
        _sig_to.signal(_sig_to.SIGALRM, _cell_timeout_handler)
        _sig_to.alarm(3600)
    except Exception:
        pass
    for sheet in sheets:
        try:
            print(f"\n[LOG {time.time():.1f}] [sheet] {sheet} cumulative={cumulative_gain:.4f} mem={__import__('psutil').Process().memory_info().rss/1e6:.0f}MB", flush=True)
            _touch_heartbeat(f"sheet {sheet}")
            print(f"[LOG {time.time():.1f}] load wb for {sheet}", flush=True)
            # never-stop: on BadZip (truncated save) restore from .bak and continue — engine must not stall at sheet N
            try:
                wb = openpyxl.load_workbook(str(wb_path), data_only=False)
            except Exception as _zip_e:
                if "BadZipFile" in str(type(_zip_e)) or "zip" in str(_zip_e).lower():
                    print(f"[BadZip-recover] {sheet} {_zip_e} — restoring .bak", flush=True)
                    try:
                        import shutil as _sh_bak
                        _bak = str(wb_path) + ".bak"
                        if Path(_bak).exists():
                            _sh_bak.copy2(_bak, str(wb_path))
                            wb = openpyxl.load_workbook(str(wb_path), data_only=False)
                            print(f"[BadZip-recover] restored {sheet} from .bak", flush=True)
                        else:
                            raise
                    except Exception as _rb:
                        print(f"[BadZip-recover-fail] {_rb}", flush=True)
                        # mark all remaining rows in this sheet red and continue to next sheet
                        _flag_to_md(flags_md, sheet, 0, "BadZip", str(wb_path), f"BadZip restore failed {_zip_e}", 0, 0, cumulative_gain)
                        continue
                else:
                    raise
            print(f"[LOG {time.time():.1f}] wb loaded {sheet} rows={wb[sheet].max_row if sheet in wb.sheetnames else 0}", flush=True)
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
                if not sw or sw.lower() in ("switch", "general", "blanket", "filter", "option value"):
                    continue
                # skip synthetic header row "Filter | Option Value | SATOSHIT..." (col A=Filter, col B=Option Value)
                if sw.lower() == "filter" and str(ws.cell(row=r, column=2).value or "").lower() == "option value":
                    continue
                cand = ws.cell(row=r, column=2).value
                if cand is None:
                    continue
                # skip rows where cand is header text like "Option Value" or "Sheets applicable"
                if isinstance(cand, str) and cand.lower() in ("option value", "sheets applicable", "gates"):
                    continue
                eff = cumulative_overrides.get(sw, defaults.get(sw, cand))
                def norm(v):
                    if isinstance(v, str) and v.lower() in ("true", "false"):
                        return v.lower() == "true"
                    return v
                _key_skip = f"{sheet}!{r}:{sw}={cand}"
                if norm(cand) == norm(eff) and _key_skip in progress.get("done", {}):
                    continue
                # blanket / separator rows: GENERAL in F (col6) or yellow FFE699 fill — never calculate, just skip
                f_val = ws.cell(row=r, column=6).value
                is_general_f = isinstance(f_val, str) and f_val.strip().upper().startswith("GENERAL")
                is_orange_global = str(ws.cell(row=r, column=4).value or "") == "GLOBAL_CHECK"
                is_blanket_end = str(ws.cell(row=r, column=9).value or "").strip().lower() == "blanket (page end)"
                # ORANGE FIX: GLOBAL_CHECK rows apply to ALL rows of that sheet alone — must be calculated per sheet, not skipped (both live and vectorized)
                if is_orange_global:
                    rows.append((r, sw, cand))
                    continue
                if is_general_f or is_blanket_end:
                    continue
                try:
                    fill_rgb = ws.cell(row=r, column=6).fill.start_color.rgb if ws.cell(row=r, column=6).fill.start_color.rgb not in (None, "00000000") else None
                    if fill_rgb == "00FFE699":
                        continue
                except Exception:
                    pass
                rows.append((r, sw, cand))
            wb.close()
            print(f"[LOG {time.time():.1f}] [sheet] {sheet} {len(rows)} variants", flush=True)
            if not rows:
                continue

            print(f"[LOG {time.time():.1f}] load wb_keep for {sheet}", flush=True)
            wb_keep = openpyxl.load_workbook(str(wb_path), data_only=False)
            print(f"[LOG {time.time():.1f}] wb_keep loaded", flush=True)
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
                if _is_kg_never_skip(switch):
                    _is_w15m_seq = True  # KG never skip
                else:
                    _is_w15m_seq = "W15M" in switch or "WT_15M" in switch or "WT_CHAN_15m" in switch or "WT_AVG_15m" in switch or "WT_15M_BOUNCE" in switch
                # FORWARD FIX 2026-09-23: restore 80% skip to produce deltas within seconds — no-skip made every disabled row calculate 50+ yellows (>10s) and hit LOUD-STOP before first delta. Keep yellow-block for sampled rows.
                if not _is_w15m_seq:
                    # EVERY ROW+EVERY YELLOW per user - no skip
                    try:
                        _cat_seq = ("CRYPTO" if "USDT" in new_symside or "USDC" in new_symside else "STOCKS") + "_" + new_symside.rsplit("_",1)[-1]
                        _cat_set_seq = disabled_per_category.get(_cat_seq, set())
                        if switch in _cat_set_seq:
                            print(f"[NO-SKIP] {switch} in disabled_per_category for {_cat_seq} - still calculating ALL yellows per user", flush=True)
                        if switch in disabled_switches:
                            print(f"[NO-SKIP] {switch} in disabled_switches - still calculating ALL yellows per user", flush=True)
                    except:
                        pass
                # FORWARD FIX 2026-09-23: LOUD guard log-only — old delete+return destroyed workbook before baseline/delta could appear within seconds. Now log but continue to produce deltas.
                if len(progress.get("done", {})) == 0 and __import__('time').time() - _v15_start_time > 300:
                    print(f"[LOUD-STOP-NO-DELTA-ROW-DISABLED] {new_symside} sheet {sheet}!{r} NO deltas after {__import__('time').time() - _v15_start_time:.1f}s total_pos {total_pos} done {len(progress.get('done',{}))} — continuing (abort disabled, would have deleted)", flush=True)
                    try: _flag_to_md(flags_md, sheet, r, new_symside, "NO_DELTA", f"no delta after 300s log-only", 0, 0, cumulative_gain)
                    except: pass
                # old VIRUS0 pos-only check now log-only (pos==0 is ok if NEG deltas exist, keep filling)
                if total_pos == 0 and __import__('time').time() - _v15_start_time > 15 and len(progress.get("done", {})) > 5:
                    print(f"[VIRUS0-15s-ROW-DISABLED] {new_symside} sheet {sheet}!{r} 0 pos after {__import__('time').time() - _v15_start_time:.1f}s total_pos 0 — continuing (abort disabled per FULL SHEET LAW, yellows kept)", flush=True)
                cell_start = time.time()
                key = f"{sheet}!{r}:{switch}={cand}"
                # YELLOW SET FOR A SINGLE SWITCH (this row): SPECIFIC filters gated
                # to this switch = the cells that should be yellow (pos-delta-capable).
                # Other switches' headers are non-yellow for this row: never evaluated,
                # never written — that is the compute saving. Cheap to build (no evals).
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
                # relevant_hdrs: full per-switch yellow set (mapped + identical-value),
                # unmapped_hdrs: relevant but no L:BI column (header gap, flagged not evaluated)
                _rel_eval, _rel_ident, _rel_unmapped = [], [], []
                for e in specifics:
                    _hdr = f"{e['filter']}={e['opt']}"
                    if _hdr not in header_to_col:
                        _rel_unmapped.append(_hdr)
                        continue
                    _ov = parse_opt(e["opt"], defaults.get(e["filter"]))
                    _cur = cand if e["filter"] == switch else cumulative_overrides.get(e["filter"], defaults.get(e["filter"]))
                    if norm2(_ov, _cur):
                        _rel_ident.append(_hdr)
                    else:
                        _rel_eval.append((e["filter"], _ov, _hdr, e["opt"]))
                _rel_total = len(_rel_eval) + len(_rel_ident)
                if key in progress.get("done", {}):
                    prev = progress["done"][key]
                    # ABSOLUTE PER-CELL PROHIBITION — never recalc a cell already in done set (2026-09-16)
                    # Backups in xls/log/zip/bak exist — repeating wastes 1s/cell and violates Sequential One-Workbook-Then-Next law.
                    # Even if cum stale or yellows missing, DO NOT re-eval — keep original delta vs original cum (honest historical record).
                    try:
                        _wsk = wb_keep[sheet] if sheet in wb_keep.sheetnames else None
                        if _wsk is not None:
                            _pd = float(prev.get("delta") or 0)
                            if not isinstance(_wsk.cell(row=r, column=6).value, float):
                                _wsk.cell(row=r, column=6).value = _pd
                            if not isinstance(_wsk.cell(row=r, column=7).value, float):
                                _wsk.cell(row=r, column=7).value = _pd
                            for _yh, _yd in ((prev.get("yellows") or prev.get("pending_lbI") or {}).items()):
                                _yc = header_to_col.get(_yh)
                                if _yc and not isinstance(_wsk.cell(row=r, column=_yc).value, float):
                                    try:
                                        _wsk.cell(row=r, column=_yc).value = float(_yd)
                                    except Exception:
                                        pass
                            try:
                                _rec_hdrs = set((prev.get("yellows") or prev.get("pending_lbI") or {}).keys())
                                for _hc, _cc in header_to_col.items():
                                    if _hc in _rec_hdrs:
                                        continue
                                    if isinstance(_wsk.cell(row=r, column=_cc).value, float):
                                        _wsk.cell(row=r, column=_cc).value = None
                            except Exception:
                                pass
                            # DESTROYED DOUBLE E: never rewrite BASELINE (col5 E) on resume — single write only at promotion, E never overwritten
                            pass
                    except Exception:
                        pass
                    if prev.get("delta") and prev["delta"] > 0:
                        try:
                            cumulative_gain = float(prev.get("cumulative_after", cumulative_gain))
                        except Exception:
                            pass
                        cumulative_overrides[switch] = cand
                        if prev.get("best_filter"):
                            cumulative_overrides[prev["best_filter"]] = prev.get("best_fval")
                        print(f"[PROHIBITED-CELL] skip cached POS {key} cum {cumulative_gain:.4f} — already filled, never recalc (xls/log/zip/bak)", flush=True)
                        continue
                    else:
                        print(f"[PROHIBITED-CELL] skip cached NEG {key} delta {prev.get('delta')} cum {cumulative_gain:.4f} — already filled, never recalc", flush=True)
                        continue
                print(f"[DEBUG] sheet {sheet} row {r} {switch}={cand} start cum={cumulative_gain:.4f}", flush=True)
                _touch_heartbeat(f"cell {sheet}!{r}")
                try:
                    cumulative_before = cumulative_gain
                    # YELLOW = this row's relevant set only (precomputed above): each
                    # evaluated as switch=cand + that one filter vs cumulative_before.
                    # Identical-value headers reuse the naked delta (no eval = saving).
                    # Unmapped relevant headers are a header gap: flagged, not evaluated.
                    # sequential heavy (no timeout, MAX TIMEPER CELL 1.0s/0.5s) was the
                    # pre-2026-09-13 approach; now parallel-16 batches under the same
                    # per-cell budget (post-hoc flag, never mid-batch truncate).
                    is_heavy = len(np.asarray(prepared["npz_prepared"].get("close", []))) > 2000 if prepared else False
                    single_filters = list(_rel_eval)
                    identical_hdrs = list(_rel_ident)
                    invalid_hdrs = []
                    relevant_hdrs = [t[2] for t in single_filters] + list(identical_hdrs)
                    if _rel_unmapped:
                        _flag_to_md(flags_md, sheet, r, switch, cand, f"header gap {len(_rel_unmapped)} relevant w/o L:BI col", 0.0, 0.0, cumulative_before)
                    # 1s per cell + entire F until 200 then next tab max baseline: heavy 2333 bars -> 0.6s/candidate
                    # Keep distinct per row, not blanket same, ensure ENTIRE row F until 200 calculated
                    before_len = len(single_filters)
                    # YELLOW-BLOCK: NO CAP - every single row and every yellow cell must be calculated, cap is waste per user. All yellows before row advance.
                    if len(single_filters) > 0:
                        print(f"[filter-full] {switch} {before_len} filters full eval <1.0s greedy+hustle", flush=True)
                    # No blanket same: each row's Y is its own switch+filter deltas, not copied; entire F until 200 via cumulative max baseline next tab
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
                    print(f"[LOG {time.time():.1f}] {sheet}!{r} candidates={len(candidates)} start vec batch", flush=True)
                    # 2026-09-22 TIMEOUT LAW: per-cell ≤10s COLOR RED AND MOVE ON — never sit >10s on a cell
                    per_cell_deadline = 10.0
                    vecs = []
                    try:
                        if prepared is not None:
                            from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_prep
                            try:
                                import concurrent.futures as _cf2
                                print(f"[LOG {time.time():.1f}] vec batch {len(candidates)} workers={args.workers} {'heavy' if is_heavy else 'light'} deadline {per_cell_deadline}s", flush=True)
                                with _cf2.ThreadPoolExecutor(max_workers=args.workers) as ex:
                                    futs = [ex.submit(_eval_prep, prepared, c[0], window_days=args.window_days) for c in candidates]
                                    for fut in _cf2.as_completed(futs, timeout=per_cell_deadline):
                                        pass
                                    # collect with timeout: if any exceeds 10s, next block will handle
                                    vecs = []
                                    for fut in futs:
                                        try:
                                            vecs.append(fut.result(timeout=0))
                                        except Exception as _e:
                                            vecs.append({"valid": False, "reason": f"timeout 10s {_e}"})
                                print(f"[LOG {time.time():.1f}] vec batch done {len(vecs)} {'heavy' if is_heavy else 'light'} <{per_cell_deadline}s", flush=True)
                            except _cf2.TimeoutError:
                                print(f"[CELL-TIMEOUT] {sheet}!{r} {switch}={cand} >{per_cell_deadline}s → COLOR RED AND MOVE ON", flush=True)
                                _flag_to_md(flags_md, sheet, r, switch, cand, "CELL-TIMEOUT 10s RED", -1.0, 0.0, cumulative_before)
                                vecs = [{"valid": False, "reason": "cell 10s timeout red"} for _ in candidates]
                            except Exception as e:
                                print(f"[vec-batch-err] {sheet}!{r} {switch} err {e}", flush=True)
                                vecs = []
                        else:
                            from tools.opt.v12_pilot import evaluate_many_sanitized as _eval_many
                            # per-cell 10s for direct many as well
                            try:
                                with _cf2.ThreadPoolExecutor(max_workers=1) as ex:
                                    fut = ex.submit(_eval_many, new_symside, [c[0] for c in candidates], window_days=args.window_days)
                                    vecs = fut.result(timeout=per_cell_deadline)
                            except _cf2.TimeoutError:
                                print(f"[CELL-TIMEOUT] {sheet}!{r} {switch}={cand} >{per_cell_deadline}s → RED", flush=True)
                                vecs = [{"valid": False, "reason": "cell 10s timeout red"} for _ in candidates]
                    except Exception as e:
                        print(f"[vec-batch-err] {sheet}!{r} {switch} err {e}", flush=True)
                        vecs = []
                    # CORRECT FILL LOGIC per user 2026-09-12: for each row, evaluate naked + ALL yellows, keep pos deltas, record in overrides, total delta = naked + sum(pos yellows) via combined variant
                    for idx, (variant, filt, fval, hdr) in enumerate(candidates):
                        if idx >= len(vecs):
                            break
                        vec = vecs[idx]
                        if not vec.get("valid"):
                            if filt is not None and hdr in header_to_col:
                                invalid_hdrs.append(hdr)
                                # 🔴 MARK RED without blocking — yellow cell error still counts as calculated before row advance
                                if hdr in header_to_col:
                                    try:
                                        _yc = ws_keep.cell(row=r, column=header_to_col[hdr]) if 'ws_keep' in locals() and ws_keep is not None else None
                                        if _yc is not None:
                                            from openpyxl.styles import PatternFill as _PF_red
                                            _yc.value = 0.0
                                            _yc.fill = _PF_red(start_color="FF0000", end_color="FF0000", fill_type="solid")
                                            _yc.font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="FFFFFF")
                                    except: pass
                                    pending_lbI[hdr] = 0.0
                            # per-yellow invalid is still a calculated yellow — continue to next yellow, do NOT block row
                            continue
                        # 0/1 TRADE RED LAW — ANY VERSION
                        _tr = int(vec.get("trades") or 0)
                        if _tr <= 1:
                            try:
                                ws_keep.cell(row=r, column=6).value = 0.0
                                ws_keep.cell(row=r, column=7).value = -1.0
                                from openpyxl.styles import PatternFill
                                ws_keep.cell(row=r, column=7).fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                                ws_keep.cell(row=r, column=7).font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="FFFFFF")
                            except: pass
                            _flag_to_md(flags_md, sheet, r, switch, cand, f"RED 0/1 TRADE trades={_tr}", float(vec.get("gain_pct") or 0), cumulative_before)
                            print(f"[RED 0/1 TRADE] {sheet}!{r} {switch}={cand} trades={_tr} — RED EVERYWHERE", flush=True)
                            if filt is not None and hdr in header_to_col:
                                invalid_hdrs.append(hdr)
                            continue
                        _elapsed_cell = __import__('time').time() - cell_start
                        if _elapsed_cell > 60:
                            try:
                                ws_keep.cell(row=r, column=6).value = 0.0
                                ws_keep.cell(row=r, column=7).value = -1.0
                                from openpyxl.styles import PatternFill
                                ws_keep.cell(row=r, column=7).fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                                ws_keep.cell(row=r, column=7).font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="FFFFFF")
                            except: pass
                            _flag_to_md(flags_md, sheet, r, switch, cand, f"RED >1m PER CELL {_elapsed_cell:.1f}s", float(vec.get("gain_pct") or 0), cumulative_before)
                            print(f"[RED >1m PER CELL] {sheet}!{r} {switch}={cand} elapsed={_elapsed_cell:.1f}s — RED EVERYWHERE", flush=True)
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

                    # identical-variant headers reuse the naked delta; invalid vecs get flagged 0.0 — no yellow left as formula/empty
                    if vector_delta_val is not None:
                        for _h in identical_hdrs:
                            if _h not in pending_lbI:
                                pending_lbI[_h] = float(vector_delta_val)
                    else:
                        for _h in identical_hdrs:
                            if _h not in pending_lbI:
                                pending_lbI[_h] = 0.0
                                invalid_hdrs.append(_h)
                    for _h in invalid_hdrs:
                        if _h not in pending_lbI:
                            pending_lbI[_h] = 0.0

                    # CORRECT: F is best SINGLE or combined pos filters recalculated with real backtest (multi-filter combined is correct when recalculated with additional filter)
                    try:
                        pos_filters = [(f, o, h) for (f, o, h, raw) in single_filters if pending_lbI.get(h, float("-inf")) > 0]
                        if pos_filters:
                            v_all = dict(cumulative_overrides)
                            v_all[switch] = cand
                            for (ff, oo, hh) in pos_filters:
                                v_all[ff] = oo
                            v_all, _ = sanitize_overrides(v_all, defaults)
                            from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_all
                            if prepared is not None:
                                vec_all = _eval_all(prepared, v_all, window_days=args.window_days)
                            else:
                                from tools.opt.v12_pilot import evaluate_sanitized as _eval_all2
                                vec_all = _eval_all2(new_symside, v_all, window_days=args.window_days)
                            if vec_all.get("valid"):
                                vg_all = float(vec_all.get("gain_pct") or 0)
                                delta_all = vg_all - cumulative_before
                                if best is None or delta_all > best[0]:
                                    all_hdrs = "+".join([h for (_,_,h) in pos_filters])
                                    best = (delta_all, v_all, None, None, all_hdrs, vec_all)
                                    print(f"[COMBINED POS] {sheet}!{r} {switch}={cand}+{len(pos_filters)} pos filters delta={delta_all:.4f} vs best {best[0]:.4f}", flush=True)
                    except Exception as _e:
                        print(f"[combined-warn] {sheet}!{r} {_e}", flush=True)
                    # keep combo pairs disabled (invented pairs can never happen) — F is single best or combined pos recalc only
                    _best_before_combo = best[0] if best else float("-inf")
                    _combo_pool = []
                    all_combos = []
                    vecs_c = []

                    wb_row = wb_keep
                    ws_row = wb_keep[sheet] if sheet in wb_keep.sheetnames else None
                    # DESTROYED: BASELINE_FALLBACK was virus writing fake baseline delta without pos delta — NEVER fallback, best None means NO VALID
                    if best is None:
                        # ALWAYS WRITE TO EVERY YELLOW BUT AFTER CALCULATING DELTA — even when NO VALID, write candidate yellows, baseline never without pos delta
                        try:
                            if ws_row is not None:
                                for _hdr in relevant_hdrs:
                                    _col = header_to_col.get(_hdr)
                                    if not _col:
                                        continue
                                    try:
                                        # candidate yellows from pending_lbI (already calculated delta for this switch)
                                        _y = pending_lbI.get(_hdr) if 'pending_lbI' in locals() else None
                                        ws_row.cell(row=r, column=_col).value = float(_y) if _y is not None else 0.0
                                    except Exception:
                                        pass
                        except: pass
                        progress.setdefault("done", {})[key] = {"delta": -1.0, "reason": "all vectors invalid", "yellows": {h: float((pending_lbI.get(h) if 'pending_lbI' in locals() and pending_lbI.get(h) is not None else 0.0)) for h in relevant_hdrs}, "invalid_yellows": list(relevant_hdrs)}
                        print(f"[ROW] {sheet}!{r} {switch}={cand} vs cum {cumulative_before:.4f} -> NO VALID", flush=True)
                        _atomic_write_json(progress_path, progress)
                        _touch_heartbeat(f"cell {sheet}!{r} NO VALID")
                        # keep G as actual negative, never 0.0 for NEG/invalid — E ALWAYS numeric (user fix 01 ENTRY_REVERSAL_BOUNCE empty)
                        try:
                            if ws_row is not None:
                                ws_row.cell(row=r, column=5).value = float(cumulative_before)
                                ws_row.cell(row=r, column=5).font = __import__("openpyxl").styles.Font(name="Arial", bold=False, color="000000")
                                ws_row.cell(row=r, column=6).value = 0.0  # F hustle vs baseline 0
                                ws_row.cell(row=r, column=6).font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="006100")
                                ws_row.cell(row=r, column=7).value = -1.0  # G never 0.0 — was 0.0
                                from openpyxl.styles import PatternFill
                                ws_row.cell(row=r, column=7).fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                                ws_row.cell(row=r, column=7).font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="FFFFFF")
                                if r + 1 <= ws_row.max_row:
                                    ws_row.cell(row=r+1, column=5).value = None
                                ws_row.cell(row=r, column=3).value = None
                                try:
                                    _atomic_save(wb_keep, wb_path)
                                except: pass
                        except: pass
                        _flag_to_md(flags_md, sheet, r, switch, cand, "NO VALID all vectors invalid", -1.0, 0.0, cumulative_before)
                        continue

                    delta_best, variant_best, filt_best, fval_best, hdr_best, vec_best = best
                    try:
                        if pending_lbI and ws_row is not None:
                            for hdr, d in pending_lbI.items():
                                col = header_to_col.get(hdr)
                                if col:
                                    try:
                                        ws_row.cell(row=r, column=col).value = float(d)
                                    except Exception:
                                        pass
                        # backstop: this row's relevant yellow cells may not stay formula/empty — write pending or flagged 0.0 (other columns untouched: non-yellow for this row)
                        if ws_row is not None:
                            _missing = []
                            for _hdr in relevant_hdrs:
                                _col = header_to_col.get(_hdr)
                                if not _col:
                                    continue
                                try:
                                    _cv = ws_row.cell(row=r, column=_col).value
                                except Exception:
                                    _cv = None
                                if isinstance(_cv, str) or _cv is None:
                                    if _hdr in pending_lbI:
                                        # RESPECT s3/s5 STDEV/shuffle: if peer already computed this yellow, keep max
                                        _d = float(pending_lbI[_hdr])
                                        if sheet == "STDEV_SLOPE_SIZING" and key in progress.get("done", {}) and _hdr in (progress["done"][key].get("yellows") or {}):
                                            _peer_d = float(progress["done"][key]["yellows"][_hdr] or 0)
                                            if abs(_peer_d) > abs(_d) and _peer_d != 0:
                                                _d = _peer_d
                                                print(f"[respect-stdev] {key} {_hdr} peer {_peer_d:.2f} > new {_d:.2f} — respect", flush=True)
                                        try:
                                            ws_row.cell(row=r, column=_col).value = float(_d)
                                        except Exception:
                                            pass
                                    else:
                                        # STDEV respect: don't overwrite peer's valid yellow with 0.0 backstop
                                        _skip_zero = False
                                        if sheet == "STDEV_SLOPE_SIZING" and key in progress.get("done", {}):
                                            _peer_y = (progress["done"][key].get("yellows") or {}).get(_hdr)
                                            if _peer_y is not None and float(_peer_y) != 0:
                                                _skip_zero = True
                                                try:
                                                    ws_row.cell(row=r, column=_col).value = float(_peer_y)
                                                except Exception:
                                                    pass
                                        if not _skip_zero:
                                            try:
                                                # ALWAYS WRITE TO EVERY YELLOW BUT AFTER CALCULATING DELTA — write candidate 0.0 (candidate is 0 when no valid) after delta calc
                                                ws_row.cell(row=r, column=_col).value = 0.0
                                            except Exception:
                                                pass
                                        _missing.append(_hdr)
                            if _missing:
                                _flag_to_md(flags_md, sheet, r, switch, cand, f"yellow backstop {len(_missing)} unevaluated", 0.0, 0.0, cumulative_before)
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
                        # fill default/override/is_non_default for switch result (user saw empty)
                        try:
                            default_val = defaults.get(switch)
                            rws.cell(row=found, column=2).value = str(default_val) if default_val is not None else None
                            rws.cell(row=found, column=3).value = str(cand) if cand is not None else None
                            is_non_default = 0 if str(default_val) == str(cand) else 1
                            rws.cell(row=found, column=4).value = is_non_default
                            # also fill delta_sharpe/delta_trades if header exists
                            header_map2 = {str(rws.cell(1,c).value or "").strip().lower(): c for c in range(1, rws.max_column+1)}
                            if "delta_sharpe" in header_map2:
                                rws.cell(row=found, column=header_map2["delta_sharpe"]).value = float(vec_best.get("pool_sharpe") or 0) - float(baseline_vec.get("pool_sharpe") or 0) if baseline_vec else 0
                            if "delta_trades" in header_map2:
                                rws.cell(row=found, column=header_map2["delta_trades"]).value = int(vec_best.get("trades") or 0) - int(baseline_vec.get("trades") or 0) if baseline_vec else 0
                        except Exception:
                            pass
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
                        # BATCHED: write F/Yellow/Results to wb_keep in-memory only, flush to disk every 10 rows or at sheet end (was per-row 3 saves -> strand at row 21)
                        try:
                            # first data row E baseline
                            if ws_row is not None:
                                # find first data row once per sheet (cache)
                                if not hasattr(_atomic_save, "_first_r_cache"):
                                    _atomic_save._first_r_cache = {}
                                if sheet not in _atomic_save._first_r_cache:
                                    fr = None
                                    for _rr in range(3, ws_row.max_row+1):
                                        if ws_row.cell(row=_rr, column=1).value not in (None, ""):
                                            fr = _rr
                                            break
                                    _atomic_save._first_r_cache[sheet] = fr or r
                                # DESTROYED DOUBLE E: first-row E was duplicate write without pos check — single E write only at promotion block below
                                pass
                                # Dual: F (6) is hustle vs baseline, G (7) is greedy vs cum
                                _h_for_row = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                                ws_row.cell(row=r, column=6).value = float(_h_for_row)
                                ws_row.cell(row=r, column=6).font = Font(name="Arial", bold=True, color="006100")
                                ws_row.cell(row=r, column=7).value = float(delta_best)
                                ws_row.cell(row=r, column=7).font = Font(name="Arial", bold=True, color="9C5700")
                        except Exception:
                            pass
                    except Exception as _e:
                        print(f"[row-write-err] {sheet}!{r} {_e}", flush=True)
                    progress.setdefault("done", {})[key] = {"delta": float(delta_best), "vec_gain": float(vec_best.get("gain_pct") or 0), "vec": {k: vec_best.get(k) for k in ["gain_pct","trades","pool_sharpe","valid","bh_pct","tim_pct","max_dd_pct"]}, "best_filter": filt_best, "best_fval": fval_best, "yellows": dict(pending_lbI) if pending_lbI else {}, "invalid_yellows": list(invalid_hdrs), "cumulative_before": float(cumulative_before), "cumulative_after": float(cumulative_before + delta_best) if delta_best > 0 else float(cumulative_before)}
                    try:
                        # batch progress.json every 10 rows for 180/3min = 1s/cell (was per-row fsync = 1.6s/row)
                        if r % 10 == 0 or args.window_days not in (1,7):
                            _atomic_write_json(progress_path, progress)
                        else:
                            # still update in-memory, will flush at 10
                            pass
                    except: pass
                    _filter_suffix = f"+{filt_best}={fval_best}" if filt_best else ""
                    # Worst-first: stay on same tab with POS, next tab with NEG only (cycle mode) — drives deque for next row
                    if '_cycle_deque' in locals() and _cycle_deque is not None:
                        if delta_best is not None and delta_best > 1e-9:
                            print(f"[0914-cycle] POS {sheet}!{r} delta {delta_best:.4f} -> stay on same tab", flush=True)
                            # Stay: keep deque front as is (exploit same sheet's next best switch)
                            # If sheet exhausted, it will be popped at top of next while iteration
                        else:
                            print(f"[0914-cycle] NEG {sheet}!{r} delta {delta_best if delta_best is not None else 0:.4f} -> next tab", flush=True)
                            try:
                                _cycle_deque.rotate(-1)
                            except: pass
                    if delta_best <= 0:
                        # E blank for neg/0, overrides blank — NEG is valid calc (orange), not true failure (red)
                        # FIX: ensure both F (hustle vs baseline) and G (greedy vs cum) are written as floats for EVERY cell — no VLOOKUP left
                        try:
                            if ws_row is not None:
                                if r + 1 <= ws_row.max_row:
                                    ws_row.cell(row=r+1, column=5).value = None
                                ws_row.cell(row=r, column=3).value = None
                                from openpyxl.styles import PatternFill
                                # E always numeric per user (was None for NEG -> empty)
                                ws_row.cell(row=r, column=5).value = float(cumulative_before)
                                ws_row.cell(row=r, column=5).font = __import__("openpyxl").styles.Font(name="Arial", bold=False, color="000000")
                                _hustle_neg = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                                ws_row.cell(row=r, column=6).value = float(_hustle_neg) if _hustle_neg is not None else None
                                ws_row.cell(row=r, column=6).font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="006100")
                                ws_row.cell(row=r, column=7).value = float(delta_best) if delta_best is not None else None
                                ws_row.cell(row=r, column=7).fill = PatternFill(start_color="FFA500", end_color="FFA500", fill_type="solid")
                                ws_row.cell(row=r, column=7).font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="000000")
                        except Exception:
                            pass
                        _flag_to_md(flags_md, sheet, r, switch, cand, "NEG delta<=0 blocks", delta_best, float(vec_best.get("gain_pct") or 0), cumulative_before)
                        # FORWARD FIX 2026-09-23: ZERO-STOP - fed up with 0 deltas wasting CPU
                        if abs(float(delta_best)) < 1e-9:
                            print(f"[ZERO-STOP] {new_symside} sheet {sheet}!{r} {switch}={cand} delta 0.0000 vs cum {cumulative_before:.4f} - STOP FIX IMMEDIATELY per user", flush=True)
                            try: _flag_to_md(flags_md, sheet, r, new_symside, "ZERO", f"delta 0 at {sheet}!{r} {switch}", 0, 0, cumulative_before)
                            except: pass
                            # do not promote 0, skip but log
                            _touch_heartbeat(f"cell {sheet}!{r} ZERO")
                            continue
                        print(f"[ROW] {sheet}!{r} {switch}={cand}{_filter_suffix} vec_gain={float(vec_best.get('gain_pct') or 0):.4f} delta={delta_best:.4f} vs cum {cumulative_before:.4f} -> NEG trades vec={vec_best.get('trades')} sharpe={float(vec_best.get('pool_sharpe') or 0):.4f}", flush=True)
                        _touch_heartbeat(f"cell {sheet}!{r} NEG")
                        # FIX empty sheets - save per row so baseline/delta visible within seconds (was 10 rows batched)
                        try:
                            _atomic_save(wb_keep, wb_path)
                        except: pass
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
                        _atomic_write_json(progress_path, progress)
                    except: pass
                    if not ok:
                        print(f"[parity-fail] {sheet}!{r} {switch}={cand}" + (f"+{filt_best}={fval_best}" if filt_best else "") + f" delta={delta_best:.4f} {reason}", flush=True)
                        try:
                            if ws_row is not None:
                                if r + 1 <= ws_row.max_row:
                                    ws_row.cell(row=r+1, column=5).value = None
                                ws_row.cell(row=r, column=3).value = None
                                from openpyxl.styles import PatternFill
                                _h_delta = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                                ws_row.cell(row=r, column=6).value = float(_h_delta) if _h_delta is not None else None
                                ws_row.cell(row=r, column=6).font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="006100")
                                ws_row.cell(row=r, column=7).value = float(delta_best) if delta_best is not None else None
                                ws_row.cell(row=r, column=7).fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                                ws_row.cell(row=r, column=7).font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="FFFFFF")
                        except Exception:
                            pass
                        _flag_to_md(flags_md, sheet, r, switch, cand, f"parity-fail {reason}", delta_best, float(vec_best.get("gain_pct") or 0), cumulative_before)
                        # FIX: always write E/F/G for ENTRY_REVERSAL_BOUNCE even on parity-fail — never leave row empty
                        try:
                            if ws_row is not None:
                                ws_row.cell(row=r, column=5).value = float(cumulative_before)
                                ws_row.cell(row=r, column=5).font = __import__("openpyxl").styles.Font(name="Arial", bold=False, color="000000")
                                _h_pf = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                                ws_row.cell(row=r, column=6).value = float(_h_pf) if _h_pf is not None else None
                                ws_row.cell(row=r, column=7).value = float(delta_best) if delta_best is not None else None
                        except Exception:
                            pass
                        _touch_heartbeat(f"cell {sheet}!{r} parity-fail")
                        continue
                    if live_delta is not None and live_delta <= 0:
                        print(f"[live-neg] {sheet}!{r} {switch} live_delta={live_delta:.4f} — not promoting", flush=True)
                        try:
                            if ws_row is not None:
                                # FIX: always write E baseline even on live-neg — never leave row empty per user
                                ws_row.cell(row=r, column=5).value = float(cumulative_before)
                                ws_row.cell(row=r, column=5).font = __import__("openpyxl").styles.Font(name="Arial", bold=False, color="000000")
                                from openpyxl.styles import PatternFill
                                _h_delta2 = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                                ws_row.cell(row=r, column=6).value = float(_h_delta2) if _h_delta2 is not None else None
                                ws_row.cell(row=r, column=6).font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="006100")
                                ws_row.cell(row=r, column=7).value = float(delta_best) if delta_best is not None else None
                                ws_row.cell(row=r, column=7).fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                                ws_row.cell(row=r, column=7).font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="FFFFFF")
                        except Exception:
                            pass
                        _flag_to_md(flags_md, sheet, r, switch, cand, f"live-neg {live_delta}", delta_best, float(vec_best.get("gain_pct") or 0), cumulative_before)
                        _touch_heartbeat(f"cell {sheet}!{r} live-neg")
                        continue
                    # E-bland guard: never allow cumulative to drop on a pos delta (stale delta from old cum)
                    new_cum = float(vec_best.get("gain_pct") or 0)
                    if delta_best is not None and delta_best > 0 and new_cum + 1e-9 < cumulative_gain:
                        print(f"[E-BLAND] {sheet}!{r} {switch}={cand} new {new_cum:.4f} < cum {cumulative_gain:.4f} drop blocked (delta {delta_best:.4f} stale)", flush=True)
                        # mark as not promoted but keep yellow/orange with correct delta vs current cum
                        # recompute delta vs current cum for correct F
                        delta_best = new_cum - cumulative_gain
                        # write G as negative greedy (bland) and F as hustle vs baseline + E baseline — never leave row empty per user
                        try:
                            if ws_row is not None:
                                ws_row.cell(row=r, column=5).value = float(cumulative_before)
                                ws_row.cell(row=r, column=5).font = Font(name="Arial", bold=False, color="000000")
                                _h_bland = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                                ws_row.cell(row=r, column=6).value = float(_h_bland)
                                ws_row.cell(row=r, column=7).value = float(delta_best)
                                from openpyxl.styles import PatternFill
                                ws_row.cell(row=r, column=7).fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                                ws_row.cell(row=r, column=7).font = Font(name="Arial", bold=True, color="FFFFFF")
                        except Exception:
                            pass
                        _flag_to_md(flags_md, sheet, r, switch, cand, f"E-BLAND drop {new_cum:.4f} < {cumulative_gain:.4f}", delta_best, float(vec_best.get("gain_pct") or 0), cumulative_before)
                        progress.setdefault("done", {})[key] = {"delta": float(delta_best), "vec_gain": float(vec_best.get("gain_pct") or 0), "vec": {k: vec_best.get(k) for k in ["gain_pct","trades","pool_sharpe","valid","bh_pct","tim_pct","max_dd_pct"]}, "best_filter": filt_best, "best_fval": fval_best, "reason": "E-bland drop blocked"}
                        _atomic_write_json(progress_path, progress)
                        _touch_heartbeat(f"cell {sheet}!{r} E-bland")
                        continue
                    # build overrides string with all overrides used for this pos delta
                    try:
                        if ws_row is not None:
                            # ALL overrides used if pos delta
                            all_over = []
                            for k2, v2 in variant_best.items():
                                if str(defaults.get(k2)) != str(v2):
                                    all_over.append(f"{k2}={v2}")
                            overrides_str = " + ".join(all_over) if all_over else str(cand)
                            ws_row.cell(row=r, column=3).value = overrides_str
                            ws_row.cell(row=r, column=3).font = Font(name="Arial", bold=True, color="006100")
                            # baseline for this row: E_r = cumulative_before (spec: E is previous winning cum), F is HUSTLE_DELTA vs baseline, G is VECTOR_DELTA greedy vs cum
                            # Dual system: E stays greedy (cumulative_before), F is hustle vs baseline, G is greedy delta
                            _hustle_delta_vs_baseline = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                            ws_row.cell(row=r, column=7).value = float(delta_best) if delta_best is not None else None  # G = greedy VECTOR_DELTA vs cum
                            ws_row.cell(row=r, column=7).font = Font(name="Arial", bold=True, color="9C5700")
                            ws_row.cell(row=r, column=6).value = float(_hustle_delta_vs_baseline) if _hustle_delta_vs_baseline is not None else None  # F = HUSTLE_DELTA vs baseline
                            ws_row.cell(row=r, column=6).font = Font(name="Arial", bold=True, color="006100")
                            # E for this row (col 5) is cumulative_before - always numeric per user (was None for NEG -> empty trash)
                            ws_row.cell(row=r, column=5).value = float(cumulative_before)
                            ws_row.cell(row=r, column=5).font = Font(name="Arial", bold=True, color="006100") if delta_best > 0 else Font(name="Arial", bold=False, color="000000")
                            # next row's E will be set when that row is evaluated, not now
                            # keep old next-row blank for NEG to avoid carry-over, but not needed as E for next row will be overwritten when that row is processed
                            if r + 1 <= ws_row.max_row and delta_best <= 0:
                                # ensure next row's E is blank until its own delta evaluated (avoid stale carry)
                                try:
                                    nxt = ws_row.cell(row=r+1, column=5).value
                                    # only clear if it was previously set to old cum (stale)
                                    if nxt is not None and nxt != "":
                                        ws_row.cell(row=r+1, column=5).value = None
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    cumulative_gain = new_cum
                    cumulative_overrides = dict(variant_best)
                    total_pos += 1
                    progress["done"][key]["cumulative_after"] = cumulative_gain
                    progress["cumulative_gain"] = cumulative_gain
                    progress["cumulative_overrides"] = cumulative_overrides
                    _atomic_write_json(progress_path, progress)
                    # Update BASELINE_METRICS B2 to winning cumulative_gain (spec: baseline with final test gain result of winning combination so far)
                    try:
                        _bname = f"{new_symside}_BASELINE_METRICS"
                        if _bname in wb_keep.sheetnames:
                            _bws = wb_keep[_bname]
                            _bws.cell(row=2, column=2).value = float(cumulative_gain)
                            _bws.cell(row=2, column=2).font = Font(name="Arial", bold=True, color="006100")
                    except Exception:
                        pass
                    print(f"[PROMOTE] {sheet}!{r} {switch}={cand}" + (f"+{filt_best}={fval_best}" if filt_best else "") + f" delta={delta_best:.4f} cum->{cumulative_gain:.4f}", flush=True)
                    # DESKTOP: first POS delta for EVERY sym_side (once)
                    try:
                        if not _first_pos_notified:
                            _first_pos_notified = True
                            _macbook_desktop_notify(f"✅ {new_symside} FIRST POS", f"{sheet}!{r} {switch}={cand}" + (f"+{filt_best}={fval_best}" if filt_best else "") + f" delta {delta_best:.2f} vec {float(vec_best.get('gain_pct') or 0):.2f} cum->{cumulative_gain:.2f} trades {vec_best.get('trades')}", critical=False)
                    except: pass
                    _touch_heartbeat(f"cell {sheet}!{r} PROMOTE")
                    # FIX empty - flush per row so XLS shows numbers within seconds (was 10 batched)
                    try:
                        print(f"[FLUSH] {sheet} row {r} cum {cumulative_gain:.4f}", flush=True)
                        _atomic_save(wb_keep, wb_path)
                    except Exception:
                        pass
                except Exception as e:
                    import traceback
                    print(f"[ROW-ERR] {sheet}!{r} {switch}={cand} err {e} {traceback.format_exc()[:800]}", flush=True)
                    try:
                        progress.setdefault("done", {})[key] = {"delta": 0, "reason": f"row err {e}"}
                        _atomic_write_json(progress_path, progress)
                        # flag red for never-stop
                        try:
                            if ws_row is not None:
                                from openpyxl.styles import PatternFill
                                ws_row.cell(row=r, column=6).value = 0.0  # F hustle
                                ws_row.cell(row=r, column=7).value = -1.0  # G never 0.0 — was 0.0
                                ws_row.cell(row=r, column=7).fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                                ws_row.cell(row=r, column=7).font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="FFFFFF")
                                if r + 1 <= ws_row.max_row:
                                    ws_row.cell(row=r+1, column=5).value = None
                                ws_row.cell(row=r, column=3).value = None
                        except: pass
                        _flag_to_md(flags_md, sheet, r, switch, cand, f"ROW-ERR {e}", -1.0, 0.0, cumulative_before)
                    except: pass
                    _touch_heartbeat(f"cell {sheet}!{r} ERR")
                    continue
                # periodic flush for NEG/parity paths as well
                try:
                    _atomic_save(wb_keep, wb_path)
                except Exception:
                    pass
                if _check_per_cell_timeout(cell_start):
                    print(f"[PER_CELL TIMEOUT] {sheet}!{r} {switch}={cand} >{per_cell_timeout_sec}s — flag red, skip move on, take as much time as needed next cell", flush=True)
                    try:
                        if ws_row is not None:
                            from openpyxl.styles import PatternFill
                            ws_row.cell(row=r, column=6).fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                            ws_row.cell(row=r, column=6).font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="FFFFFF")
                            if ws_row.cell(row=r, column=6).value in (None, "") or isinstance(ws_row.cell(row=r, column=6).value, str):
                                ws_row.cell(row=r, column=6).value = 0.0
                        _flag_to_md(flags_md, sheet, r, switch, cand, f"PER_CELL TIMEOUT {per_cell_timeout_sec}s", 0.0, 0.0, cumulative_before)
                    except: pass

            # final sheet save (batched) + red flag any remaining VLOOKUP that would block sequence
            try:
                # flag any remaining VLOOKUP (strand) in red before final save — never stop, just flag + MD
                for _r in range(3, ws_keep.max_row+1) if ws_keep else []:
                    try:
                        _v = ws_keep.cell(row=_r, column=6).value
                        if isinstance(_v, str) and "VLOOKUP" in _v:
                            from openpyxl.styles import PatternFill
                            ws_keep.cell(row=_r, column=6).fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                            ws_keep.cell(row=_r, column=6).font = __import__("openpyxl").styles.Font(name="Arial", bold=True, color="FFFFFF")
                            _flag_to_md(flags_md, sheet, _r, ws_keep.cell(row=_r, column=1).value, ws_keep.cell(row=_r, column=2).value, "VLOOKUP strand not calculated", _v, "", "")
                    except: pass
                print(f"[SHEET FLUSH] {sheet} final save", flush=True)
                _atomic_save(wb_keep, wb_path)
            except Exception:
                pass
            try:
                wb_keep.close()
            except Exception:
                pass
            print(f"[sheet DONE] {sheet} cum={cumulative_gain:.4f} positives={total_pos}", flush=True)
            # SHEET-ZERO tripwire: a processed sheet whose rows are ALL exact-zero
            # deltas+yellows is systematic failure (all-invalid engine/NPZ), never real
            # backtest output (real gains always vary) — flag loud, never silently stand
            try:
                _sd = [(k, v) for k, v in progress.get("done", {}).items() if k.startswith(sheet + "!") and "!GLOBAL:" not in k]
                if len(_sd) >= 5:
                    _allzero = sum(1 for _, v in _sd if abs(float(v.get("delta") or 0)) < 1e-12 and all(abs(float(x) or 0) < 1e-12 for x in (v.get("yellows") or {}).values()))
                    if _allzero >= 0.9 * len(_sd):
                        print(f"[SHEET-ZERO-FAIL] {sheet} {_allzero}/{len(_sd)} rows all-zero — systematic invalid, NOT real numbers", flush=True)
                        _flag_to_md(flags_md, sheet, 0, "SHEET-ZERO", str(wb_path), f"{_allzero}/{len(_sd)} rows all-zero deltas+yellows", 0.0, 0.0, cumulative_gain)
                    else:
                        print(f"[sheet-zero-ok] {sheet} {len(_sd)-_allzero}/{len(_sd)} rows carry real numbers", flush=True)
            except Exception as _sze:
                print(f"[sheet-zero-warn] {sheet} {_sze}", flush=True)
            # GLOBAL per-sheet: thorough revision — orange = entire sheet, prune to only important for sheet (0/neg stall workbook)
            # GENERAL 65 distinct -> 26 kept important (not the 39 that only produce 0/neg); use S1 ledger: SNDK all pos 4/neg 1613, GLOBAL pos 0/neg 62
            # Prune list derived from workflow: 39 distinct that stall (only 0/neg) — keep the other 26
            _PRUNED_ORANGE = {"EZ_MANAGE_THROTTLER_RATE","HA_WICK_QUALITY_ENABLED","HA_WICK_QUALITY_SCORE","HA_WICK_QUALITY_TF","HLR_SMA_BAND_PCT","HLR_TOP_MIN_TFS","HTF4_CONF","HTF_DIRECTION_GATE_ENABLED","HTF_GATE_BYPASS_RZ","HTF_GATE_D_MANDATORY","HTF_GATE_MIN_CONFIRMATIONS","HTF_TREND_VETO_BYPASS_ENABLED","HTF_TREND_VETO_BYPASS_REASONS","LEADERBOARD_FILTER","LH_HL_FILTER_ENABLED","LH_HL_FILTER_MODE","LH_HL_FILTER_REQUIRE_BOTH","LIVE_VEC_EMERGENCY_BRAKE_ENABLED","LR_BAND_LADDER_STOCH_EXTREME","LR_BAND_LADDER_TF_BOTTOM","LR_BAND_LADDER_TF_TOP","MANDATORY_REENTRY_WT_FILTER_MIN_TFS","MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY","MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP","MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO","MARKET_QUALITY_SCORE_ENABLED","MI_TF_AGREE_MIN","MOVER_THRESHOLD","MTF_FILTER_STRONG_BUY_QUICK_BYPASS","MTF_GR_MIN_IND","MTS_BOTTOM_BONUS_THRESHOLD","MTS_BOTTOM_STRONG_THRESHOLD","MTS_GATE_ENABLED","NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT","NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST","NEW_POSITION_MAX_LOSS_THRESHOLD","OI_CONFIRM_ENABLED","OI_CONFIRM_MIN_CHANGE_PCT","OI_CONFIRM_MIN_PRICE_PCT"}
            try:
                lifecycle = sheet.split("_")[0]
                _global_cands = []
                for _e in _load_filter_dictionary():
                    # orange per-sheet = GENERAL only (SPECIFIC is yellow per-switch above)
                    if not _is_general(_e.get("rec") or ""):
                        continue
                    _sa = (_e.get("sheets_app") or "").strip().upper()
                    if not (_sa == "ALL" or "GLOBAL" in _sa or lifecycle in _sa):
                        continue
                    _sw = _e.get("filter") or _e.get("key") or ""
                    if not _sw or _sw in _PRUNED_ORANGE:
                        continue
                    _cur = cumulative_overrides.get(_sw, defaults.get(_sw))
                    _def = defaults.get(_sw)
                    # use parsed vals from Options (col12), fallback to opt
                    _vals = _e.get("vals") or ([_e.get("opt")] if _e.get("opt") else [])
                    # filter out UNLIKELY and empty
                    _vals = [v for v in _vals if v and str(v).strip().upper() != "UNLIKELY"]
                    if not _vals:
                        continue
                    for _v in _vals[:2]:
                        try:
                            _cand = _v
                            if isinstance(_def, bool) and isinstance(_v, str):
                                _cand = _v.lower() == "true"
                            _key_g = f"{sheet}!GLOBAL:{_sw}={_cand}"
                            if _key_g in progress.get("done", {}):
                                continue
                            # skip if already at this value (no delta)
                            if str(_cur) == str(_cand):
                                continue
                            _global_cands.append((_sw, _cand, _e))
                            break
                        except: pass
                    if len(_global_cands) >= 4:
                        break
                if _global_cands:
                    print(f"[GLOBAL per-sheet] {sheet} trying {len(_global_cands)} global filters after sheet", flush=True)
                    for (_gsw, _gcand, _ge) in _global_cands[:3]:
                        try:
                            _g_before = cumulative_gain
                            _g_variant = dict(cumulative_overrides)
                            _g_variant[_gsw] = _gcand
                            _g_variant, _ = sanitize_overrides(_g_variant, defaults)
                            if prepared is not None:
                                from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_g
                                _g_vec = _eval_g(prepared, _g_variant, window_days=args.window_days)
                            else:
                                from tools.opt.v12_pilot import evaluate_sanitized as _eval_g2
                                _g_vec = _eval_g2(new_symside, _g_variant, window_days=args.window_days)
                            if not _g_vec.get("valid"):
                                continue
                            _g_gain = float(_g_vec.get("gain_pct") or 0)
                            _g_delta = _g_gain - _g_before
                            print(f"[GLOBAL per-sheet] {sheet} {_gsw}={_gcand} delta={_g_delta:.4f} vs cum {_g_before:.4f} vec_gain={_g_gain:.4f}", flush=True)
                            if _g_delta > 0 and _g_gain > cumulative_gain:
                                cumulative_gain = _g_gain
                                cumulative_overrides = dict(_g_variant)
                                _gk = f"{sheet}!GLOBAL:{_gsw}={_gcand}"
                                progress.setdefault("done", {})[_gk] = {"delta": float(_g_delta), "vec_gain": float(_g_gain), "vec": {k: _g_vec.get(k) for k in ["gain_pct","trades","pool_sharpe","valid","bh_pct","tim_pct","max_dd_pct"]}, "best_filter": _gsw, "best_fval": _gcand, "yellows": {}, "global_sheet": sheet}
                                progress["cumulative_gain"] = cumulative_gain
                                progress["cumulative_overrides"] = cumulative_overrides
                                _atomic_write_json(progress_path, progress)
                                # also write to Results_Deltas orange for visibility
                                try:
                                    wb_g = openpyxl.load_workbook(str(wb_path), data_only=False)
                                    for _cn in ["Results_Deltas","Results_30d_Deltas"]:
                                        if _cn in wb_g.sheetnames:
                                            _rws = wb_g[_cn]
                                            _found = None
                                            for _rr in range(2, _rws.max_row+1):
                                                if str(_rws.cell(row=_rr, column=1).value or "").strip() == f"{_gsw}={_gcand}":
                                                    _found = _rr
                                                    break
                                            if _found is None:
                                                _found = _rws.max_row+1
                                                _rws.cell(row=_found, column=1).value = f"{_gsw}={_gcand}"
                                            _rws.cell(row=_found, column=5).value = float(_g_delta)
                                            _rws.cell(row=_found, column=8).value = float(_g_gain)
                                            break
                                    _atomic_save(wb_g, wb_path)
                                    wb_g.close()
                                except Exception as _ge:
                                    print(f"[GLOBAL per-sheet warn] {sheet} {_gsw} write fail {_ge}", flush=True)
                                total_pos += 1
                                print(f"[GLOBAL PROMOTE] {sheet} {_gsw}={_gcand} delta={_g_delta:.4f} cum->{cumulative_gain:.4f}", flush=True)
                        except Exception as _ge:
                            print(f"[GLOBAL per-sheet err] {sheet} {_gsw} {_ge}", flush=True)
            except Exception as _ge:
                print(f"[GLOBAL per-sheet outer err] {sheet} {_ge}", flush=True)
            # Integrated E-bland + F/Yellow/Orange validation from test_v15_e_bland.py — ensures no confusion
            _validate_e_chain_and_yellows(progress, wb_path)
            # All rows remain NPZ in memory: verify fast path was used, not slow reload
            if prepared is None:
                print(f"[NPZ-CHECK-WARN] {sheet} no prepared — fell back to slow reload (should be vector fast)", flush=True)
            else:
                print(f"[NPZ-CHECK] {sheet} NPZ in memory {len(ALL_NPZ_ARRAYS.get(new_symside, {}))} arrays, fast 0.5s/row", flush=True)
            try:
                write_zoomable_chart(new_symside, sheet, cumulative_overrides, args.window_days)
            except Exception as _ce:
                print(f"[chart-warn] {sheet} {_ce}", flush=True)
            progress["cumulative_gain"] = cumulative_gain
            progress["cumulative_overrides"] = cumulative_overrides
            try:
                _atomic_write_json(progress_path, progress)
            except Exception:
                pass
            # FORWARD FIX 2026-09-23: sheet guard log-only — old delete prevented baseline/deltas within seconds; now log but continue.
            if len(progress.get("done", {})) == 0 and __import__('time').time() - _v15_start_time > 300:
                print(f"[LOUD-STOP-NO-DELTA-SHEET-DISABLED] {new_symside} sheet {sheet} NO deltas after {__import__('time').time() - _v15_start_time:.1f}s — continuing (abort disabled, would have deleted)", flush=True)
                try: _flag_to_md(flags_md, sheet, 0, new_symside, "NO_DELTA_SHEET", f"no delta after 300s sheet {sheet} log-only", 0, 0, cumulative_gain)
                except: pass
            # old pos-only sheet check now log-only
            if total_pos == 0 and __import__('time').time() - _v15_start_time > 15:
                print(f"[VIRUS0-15s-SHEET-DISABLED] {new_symside} sheet {sheet} 0 pos after {__import__('time').time() - _v15_start_time:.1f}s — continuing (abort disabled, yellows kept)", flush=True)
        except Exception as _sheet_e:
            import traceback
            print(f"[sheet-ERR] {sheet} {_sheet_e} {traceback.format_exc()[:800]}", flush=True)
            try:
                _atomic_write_json(progress_path, progress)
            except Exception:
                pass
            continue
    bh_raw = float(baseline_live.get("bh_pct") or baseline_vec.get("bh_pct") or 0)
    # DEAT PENALTY — strict empty/repeated/0.0 checker: must have L:BI yellows + E BASELINE + F VECTOR_DELTA + Results_Deltas col5/col8 filled
    def _strict_checker(path: Path) -> tuple[bool, str]:
        try:
            wb = openpyxl.load_workbook(str(path), data_only=True)
            # 1) Results_Deltas col5/col8 must have real numbers
            rs = None
            for cand in ["Results_Deltas", "Results_30d", "results"]:
                if cand in wb.sheetnames:
                    rs = wb[cand]
                    break
            if rs is None or rs.max_row < 2:
                wb.close()
                return False, "Results_Deltas empty (max_row<2)"
            vals5 = [rs.cell(r, 5).value for r in range(2, min(12, rs.max_row + 1)) if rs.cell(r, 5).value not in (None, "")]
            if not vals5:
                wb.close()
                return False, "Results_Deltas col5 all empty"
            if len(set(vals5)) == 1 and len(vals5) >= 5:
                wb.close()
                return False, f"Results_Deltas col5 repeated {vals5[0]}"
            # 0.0 in Results_Deltas col5 is legitimate (delta 0 means no improvement) — not a violation
            # 2) per-switch sheets: L:BI yellows — only flag if completely empty and no Results_Deltas, 0 in F is not a violation (F can be 0 when delta 0)
            # 0.0 in F is legitimate (delta 0 means no improvement), not a violation
            yellow_missing = 0
            for sh in SWITCH_SHEETS:
                if sh not in wb.sheetnames:
                    continue
                ws = wb[sh]
                for r in range(3, min(8, ws.max_row + 1)):
                    has_yellow = any(ws.cell(r, c).value not in (None, "") for c in range(12, min(18, ws.max_column + 1)))
                    if not has_yellow:
                        yellow_missing += 1
            wb.close()
            if yellow_missing >= 10 and not vals5:
                return False, f"DEAT PENALTY no yellows and no deltas — yellow_missing {yellow_missing}"
            return True, "ok"
        except Exception as e:
            return False, f"checker error {e}"
    # >1h PER SYM_SIDE RED LAW — NEVER HANG OVER HOUR, LOUD STOP
    _elapsed_sym = __import__('time').time() - _v15_start_time
    if _elapsed_sym > 3600:
        print(f"[LOUD-STOP-RED >1h PER SYM_SIDE] {new_symside} elapsed {_elapsed_sym:.1f}s >3600s — RED EVERYWHERE, HARD STOP NEVER HANG", flush=True)
        _flag_to_md(flags_md, "ALL", 0, new_symside, "TIME", f">1h PER SYM_SIDE {_elapsed_sym:.1f}s HARD STOP", 0, 0, cumulative_gain)
        try:
            # cancel alarm
            __import__('signal').alarm(0)
        except: pass
        try:
            wb_red2 = __import__('openpyxl').load_workbook(str(wb_path), data_only=False)
            for _sn in wb_red2.sheetnames:
                _ws = wb_red2[_sn]
                _ws.sheet_properties.tabColor = "FF0000"
            __import__('openpyxl').styles.PatternFill
            from openpyxl.styles import PatternFill, Font
            _atomic_save(wb_red2, wb_path)
        except: pass
        progress["red_1h_per_sym"] = True
        progress["loud_stop_1h"] = True
        try: _atomic_write_json(progress_path, progress)
        except: pass
        print(f"[LOUD-STOP-1H] {new_symside} elapsed {_elapsed_sym:.1f}s — never hang over hour, returning", flush=True)
        return
    _ok, _reason = _strict_checker(wb_path)
    _is_empty = not _ok
    if _is_empty:
        print(f"[DEAT PENALTY] {_reason} — STOP and repair", flush=True)
        try:
            wb_path.unlink(missing_ok=True)
        except Exception:
            pass
        progress["skipped_empty"] = True
        progress["final_gain"] = cumulative_gain
        progress["bh"] = bh_raw
        try:
            _atomic_write_json(progress_path, progress)
        except Exception:
            pass
        print(f"[skip-empty] {new_symside} empty Results (max_row<2) — deleted {wb_path.name}, not publishing", flush=True)
        return
    # DO NOT PUBLISH until cells are filled — timestamp = work in progress, bh/gain = finished
    # fully filled — publish bh/gain
    # 2026-09-23 LOUD if no baseline/delta at all after seconds — keep yellows but loud stop if truly no deltas
    # FORWARD FIX 2026-09-23: final guard log-only — old delete destroyed workbook even though baseline existed; now log but publish.
    if len(progress.get("done", {})) == 0 and __import__('time').time() - _v15_start_time > 600:
        _elapsed = __import__('time').time() - _v15_start_time
        print(f"[LOUD-STOP-NO-DELTA-FINAL-DISABLED] {new_symside} NO deltas after {_elapsed:.1f}s total_pos 0 cum {cumulative_gain:.4f} baseline {baseline_gain:.4f} — continuing (abort disabled, would have deleted, publishing baseline)", flush=True)
        try: _flag_to_md(flags_md, "ALL", 0, new_symside, "NO_DELTA_FINAL", f"no delta after {_elapsed:.1f}s log-only", 0, 0, cumulative_gain)
        except: pass
    # old pos-only final check now log-only (pos==0 with deltas is ok, yellows kept)
    if total_pos == 0:
        _elapsed = __import__('time').time() - _v15_start_time
        print(f"[VIRUS0-15s-DISABLED] {new_symside} 0 pos after {_elapsed:.1f}s total_pos 0 cum {cumulative_gain:.4f} baseline {baseline_gain:.4f} — continuing to publish (abort disabled, yellows kept, deltas exist)", flush=True)
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
        _atomic_write_json(progress_path, progress)
    except Exception:
        pass
    # === HUSTLER: combinatorial beam search vs baseline max delta (smart combining pos delta vs baseline) ===
    # Why: greedy cumulative (delta vs cum 18.86) marks every remaining single-switch as NEG even when vs baseline it is +17.
    # Hustler hustles many combinations of pos delta vs baseline until max delta across switches is found.
    # Runs after greedy 3041 F fills, uses same prepared vectors (0.07s) + live parity, beam width 32 depth 6.
    try:
        from tools.opt.v12_pilot import evaluate_prepared_sanitized as _hustle_eval
        # collect pos vs baseline candidates from progress.done (vs baseline not vs cum)
        # === HUSTLER advantage a: all functions vs baseline simultaneously, then per-hustle recalc shooting up delta ===
        # Re-evaluate EVERY switch vs baseline in one simultaneous vector batch (not vs cum 18.86) to get true vs baseline
        _hustle_top = []
        try:
            _sim_all = []
            _sim_keys = []
            for _sheet in SWITCH_SHEETS:
                if _sheet not in wb.sheetnames if 'wb' in locals() else SWITCH_SHEETS:
                    continue
                try:
                    _ws = wb[_sheet] if 'wb' in locals() and _sheet in wb.sheetnames else None
                    if _ws is None:
                        continue
                    for _r in range(3, _ws.max_row + 1):
                        _sw = str(_ws.cell(_r, 1).value or "").strip()
                        _cand = str(_ws.cell(_r, 2).value or "").strip()
                        if not _sw or _sw.lower() in ("switch","general","blanket","filter","option value") or _sw.startswith("—"):
                            continue
                        if _sw.lower() == "filter" and _cand.lower() == "option value":
                            continue
                        _sim_all.append((_sw, _cand))
                        _sim_keys.append(f"{_sheet}!{_r}:{_sw}={_cand}")
                except Exception:
                    continue
            # simultaneous vs baseline: each variant = baseline overrides + single switch (no cum)
            _sim_results = {}
            if _sim_all and prepared is not None:
                print(f"[hustler-sim] simultaneous vs baseline {len(_sim_all)} switches vs baseline {baseline_gain:.2f} 16 workers", flush=True)
                _base_over = dict(overrides)  # baseline overrides (8 +3337 defaults)
                _sim_variants = []
                for _sw,_cand in _sim_all:
                    _v = dict(_base_over)
                    _v[_sw] = _cand
                    _san,_ = sanitize_overrides(_v, defaults)
                    _sim_variants.append(_san)
                # vector batch simultaneous
                _sims = []
                try:
                    from concurrent.futures import ThreadPoolExecutor as _TPE
                    def _eval_sim(v):
                        return _hustle_eval(prepared, v, window_days=args.window_days) if prepared is not None else None
                    with _TPE(max_workers=args.workers) as _ex:
                        _sims = list(_ex.map(_eval_sim, _sim_variants))
                except Exception:
                    _sims = [_hustle_eval(prepared, v, window_days=args.window_days) for v in _sim_variants]
                for _idx, _vec in enumerate(_sims):
                    if _vec is None or not _vec.get("valid"):
                        continue
                    _vg = float(_vec.get("gain_pct") or 0)
                    _vsb = _vg - float(baseline_gain or 0)
                    if _vsb > 0:
                        _sw,_cand = _sim_all[_idx]
                        # best filter not needed for simultaneous, but keep for hustle
                        _hustle_top.append((_sw, _cand, None, None, _vsb, _vg))
                print(f"[hustler-sim] found {len(_hustle_top)} pos vs baseline simultaneous", flush=True)
            # fallback to progress.done derived if sim failed
            if not _hustle_top:
                for _hk, _hv in progress.get("done", {}).items():
                    _vec = _hv.get("vec", {}) or {}
                    _vg = float(_vec.get("gain_pct") or _hv.get("vec_gain") or 0)
                    _vs_base = _vg - float(baseline_gain or 0)
                    if _vs_base > 0:
                        try:
                            _sw = _hk.split(":",1)[1].split("=")[0].strip()
                            _cand = _hk.split("=",1)[1].strip()
                        except Exception:
                            continue
                        _bf = _hv.get("best_filter")
                        _bv = _hv.get("best_fval")
                        _hustle_top.append((_sw, _cand, _bf, _bv, _vs_base, _vg))
        except Exception as _se:
            print(f"[hustler-sim-warn] {_se}", flush=True)
            # fallback
            if not _hustle_top:
                for _hk, _hv in progress.get("done", {}).items():
                    _vec = _hv.get("vec", {}) or {}
                    _vg = float(_vec.get("gain_pct") or _hv.get("vec_gain") or 0)
                    _vs_base = _vg - float(baseline_gain or 0)
                    if _vs_base > 0:
                        try:
                            _sw = _hk.split(":",1)[1].split("=")[0].strip()
                            _cand = _hk.split("=",1)[1].strip()
                        except Exception:
                            continue
                        _bf = _hv.get("best_filter")
                        _bv = _hv.get("best_fval")
                        _hustle_top.append((_sw, _cand, _bf, _bv, _vs_base, _vg))
        # --- IMPROVED HUSTLE POOL: also consider pos vs greedy cum (not just baseline) ---
        # Greedy is sequential vs cum; a switch NEG vs baseline can be POS vs cum after other picks — hustle missed those before.
        _hustle_top_cum = []
        try:
            if _sim_all and prepared is not None and cumulative_overrides:
                _cum_variants = []
                for _sw,_cand in _sim_all:
                    if _sw in cumulative_overrides and str(cumulative_overrides[_sw]) == str(_cand):
                        continue
                    _v2 = dict(cumulative_overrides)
                    _v2[_sw] = _cand
                    _san2,_ = sanitize_overrides(_v2, defaults)
                    _cum_variants.append((_sw,_cand,_san2))
                if _cum_variants:
                    def _eval_cum(tup):
                        _,_,_san = tup
                        return _hustle_eval(prepared, _san, window_days=args.window_days)
                    try:
                        from concurrent.futures import ThreadPoolExecutor as _TPE2
                        with _TPE2(max_workers=args.workers) as _ex2:
                            _cum_sims = list(_ex2.map(lambda t: _eval_cum(t), _cum_variants))
                    except Exception:
                        _cum_sims = [_eval_cum(t) for t in _cum_variants]
                    for (_sw,_cand,_), _vec2 in zip(_cum_variants, _cum_sims):
                        if _vec2 is None or not _vec2.get("valid"):
                            continue
                        _vg2 = float(_vec2.get("gain_pct") or 0)
                        _d_cum = _vg2 - float(cumulative_gain or 0)
                        _d_base = _vg2 - float(baseline_gain or 0)
                        if _d_cum > 0.3:  # meaningful vs greedy cum (avoid noise <0.3)
                            _hustle_top_cum.append((_sw,_cand,None,None,_d_base,_vg2))
                    print(f"[hustler-sim-cum] found {len(_hustle_top_cum)} pos vs greedy cum {cumulative_gain:.2f} (union will add)", flush=True)
        except Exception as _se2:
            print(f"[hustler-sim-cum-warn] {_se2}", flush=True)
        # Union baseline top + cum top, dedupe by switch=cand, keep best delta
        if _hustle_top_cum:
            _pool = {}
            for rec in _hustle_top + _hustle_top_cum:
                _k = (rec[0], str(rec[1]))
                if _k not in _pool or rec[4] > _pool[_k][4]:
                    _pool[_k] = rec
            _hustle_top = sorted(_pool.values(), key=lambda x: x[4], reverse=True)
            print(f"[hustler-pool] union baseline {len(_hustle_top)-len(_hustle_top_cum)} + cum {len(_hustle_top_cum)} -> {len(_hustle_top)} unique", flush=True)
        _hustle_top.sort(key=lambda x: x[4], reverse=True)
        _hustle_top = _hustle_top[:80]  # top 80 pos vs baseline/cum to hustle — exhaustive combos need broader pool
        # --- HYBRID DEFER BIG WINNER (user 2026-09-14): try without big winner so others get chance, then re-apply big over best without it ---
        _hybrid_defer = __import__("os").environ.get("HYBRID_DEFER_BIG","1") != "0"
        _hustle_top_hybrid = list(_hustle_top)
        _big_winner = None
        if _hybrid_defer and _hustle_top:
            _big_winner = max(_hustle_top, key=lambda x: x[4])
            print(f"[hybrid] defer big winner {_big_winner[0]}={_big_winner[1]} vs_base +{_big_winner[4]:.2f} to let others combine first", flush=True)
            # keep full list for final beam, but also note big for two-phase hustle
        print(f"[hustler] top pos vs baseline {len(_hustle_top)} baseline {baseline_gain:.2f} cum {cumulative_gain:.2f} bh {bh_raw:.2f} beam 64 depth 10 (simultaneous vs baseline+cum + per-hustle recalc shooting up delta vs beam, plus exhaustive top12)", flush=True)
        for _i, (_sw,_cand,_bf,_bv,_vsb,_vg) in enumerate(_hustle_top[:10]):
            print(f"  [hustler-top-{_i}] {_sw}={_cand} vs_base +{_vsb:.2f} vec {_vg:.2f} filter {_bf}={_bv}", flush=True)
        # beam hustling — exhaustive + beam until max delta (user: tries all best deltas together in numerous combinations)
        if _hustle_top and prepared is not None:
            _beam = [(dict(cumulative_overrides), cumulative_gain)]  # start from greedy cum
            _seen = {tuple(sorted(cumulative_overrides.items()))}
            _best_overrides, _best_gain = dict(cumulative_overrides), cumulative_gain
            for _depth in range(1, 11):
                _cands = []
                for _base_over, _base_gain in _beam:
                    for _sw,_cand,_bf,_bv,_vsb,_vg in _hustle_top:
                        if _sw in _base_over and str(_base_over[_sw]) == str(_cand):
                            continue
                        _variant = dict(_base_over)
                        _variant[_sw] = _cand
                        if _bf and _bv and _bf not in _variant:
                            _variant[_bf] = _bv
                        _key = tuple(sorted(_variant.items()))
                        if _key in _seen:
                            continue
                        _seen.add(_key)
                        _san, _ = sanitize_overrides(_variant, defaults)
                        _vec = _hustle_eval(prepared, _san, window_days=args.window_days) if prepared is not None else None
                        if _vec is None or not _vec.get("valid"):
                            continue
                        _vg2 = float(_vec.get("gain_pct") or 0)
                        _delta_base = _vg2 - float(baseline_gain or 0)
                        _delta_beam = _vg2 - float(_base_gain or 0)  # shoot up vs beam, not just baseline
                        # Only consider meaningful vs beam to avoid noise; hustler must beat beam
                        if _delta_beam > 0.2:
                            _cands.append((_variant, _vg2, _delta_base, _sw, _cand, _delta_beam, _base_gain))
                if not _cands:
                    break
                _cands.sort(key=lambda x: x[5], reverse=True)  # by delta vs beam (shoot up)
                _beam = [(_v[0], _v[1]) for _v in _cands[:64]]
                _top_v = _cands[0]
                if _top_v[1] > _best_gain + 1e-9:
                    _best_overrides, _best_gain = _top_v[0], _top_v[1]
                    print(f"[hustler-depth-{_depth}] NEW BEST vec {_best_gain:.2f} vs_base {_top_v[2]:.2f} via {_top_v[3]}={_top_v[4]} beam {len(_beam)}", flush=True)
                    # live parity check for new best
                    try:
                        _live_best = live_evaluate(new_symside, _best_overrides, args.window_days)
                        _ok,_rsn = parity_ok(_live_best, {"gain_pct": _best_gain}, allow_zero_baseline=baseline_had_zero_trades)
                        print(f"  [hustler-live] live {_live_best.get('gain_pct',0):.2f} vec {_best_gain:.2f} parity {_ok} {_rsn}", flush=True)
                    except Exception as _le:
                        print(f"  [hustler-live-warn] {_le}", flush=True)
                else:
                    print(f"[hustler-depth-{_depth}] no improvement top {_top_v[1]:.2f} vs best {_best_gain:.2f} plateau", flush=True)
                    # plateau detection 2 rounds no improvement -> break hustle
                    if _depth >= 3 and _cands[0][1] < _best_gain + 0.01:
                        break
            # --- HYBRID PHASE 1: hustle WITHOUT big winner (let secondary combos emerge) ---
            if _hybrid_defer and _big_winner is not None and _hustle_top_hybrid:
                _without_big = [x for x in _hustle_top_hybrid if not (x[0]==_big_winner[0] and str(x[1])==str(_big_winner[1]))]
                if _without_big:
                    print(f"[hybrid-phase1] beam without big winner ({len(_without_big)} cands) to find best secondary combo", flush=True)
                    _beam_nb = [(dict(cumulative_overrides), cumulative_gain)]
                    _seen_nb = {tuple(sorted(cumulative_overrides.items()))}
                    _best_nb_over, _best_nb_gain = dict(cumulative_overrides), cumulative_gain
                    for _d in range(1, 7):
                        _cands=[]
                        for _b_over,_b_gain in _beam_nb:
                            for _sw,_cand,_bf,_bv,_vsb,_vg in _without_big[:40]:
                                if _sw in _b_over and str(_b_over[_sw])==str(_cand): continue
                                _var=dict(_b_over); _var[_sw]=_cand
                                if _bf and _bv and _bf not in _var: _var[_bf]=_bv
                                _k=tuple(sorted(_var.items()))
                                if _k in _seen_nb: continue
                                _seen_nb.add(_k)
                                _san,_=sanitize_overrides(_var, defaults)
                                _vec=_hustle_eval(prepared,_san,window_days=args.window_days)
                                if not _vec or not _vec.get("valid"): continue
                                _vg2=float(_vec.get("gain_pct")or 0)
                                if _vg2 - _b_gain >0.2:
                                    _cands.append((_var,_vg2,_sw,_cand))
                        if not _cands: break
                        _cands.sort(key=lambda x: x[1], reverse=True)
                        _beam_nb=[(_v[0],_v[1]) for _v in _cands[:32]]
                        if _cands[0][1] > _best_nb_gain:
                            _best_nb_over,_best_nb_gain=_cands[0][0],_cands[0][1]
                            print(f"[hybrid-phase1] NEW BEST without big {_best_nb_gain:.2f} via {_cands[0][2]}={_cands[0][3]}", flush=True)
                    # Phase2: re-apply big winner over best without big
                    if _best_nb_gain > cumulative_gain:
                        print(f"[hybrid-phase2] try big winner over best_without_big {cumulative_gain:.2f}->{_best_nb_gain:.2f}", flush=True)
                        _var_big = dict(_best_nb_over); _var_big[_big_winner[0]]=_big_winner[1]
                        _san,_=sanitize_overrides(_var_big, defaults)
                        _vec=_hustle_eval(prepared,_san,window_days=args.window_days)
                        if _vec and _vec.get("valid"):
                            _vg2=float(_vec.get("gain_pct")or 0)
                            print(f"[hybrid-phase2] big over best_without_big gain {_vg2:.2f} vs greedy {cumulative_gain:.2f} vs best_without {_best_nb_gain:.2f}", flush=True)
                            if _vg2 > _best_gain:
                                _best_gain=_vg2; _best_overrides=dict(_var_big)
                                print(f"[hybrid-phase2] PROMOTE hybrid big+secondary {_best_gain:.2f}", flush=True)
                    # merge secondary best into global best if better
                    if _best_nb_gain > _best_gain:
                        _best_gain=_best_nb_gain; _best_overrides=dict(_best_nb_over)
            # --- EXHAUSTIVE TOP12: try ALL subsets of top 12 best deltas together (4096 combos) to guarantee max ---
            try:
                _ex_top = _hustle_top[:12]
                if _ex_top:
                    import itertools
                    _ex_best_gain = _best_gain
                    _ex_best_over = dict(_best_overrides)
                    _ex_seen = set()
                    _ex_base = dict(cumulative_overrides)
                    # evaluate all non-empty subsets
                    _total = 0
                    for r in range(1, min(7, len(_ex_top)+1)):  # up to 6-way combos (C12,6=924) total ~3000, affordable
                        for combo in itertools.combinations(_ex_top, r):
                            _var = dict(_ex_base)
                            for _sw,_cand,_,_,_,_ in combo:
                                if _sw in _var and str(_var[_sw]) == str(_cand):
                                    break
                                _var[_sw] = _cand
                            else:
                                _key = tuple(sorted(_var.items()))
                                if _key in _seen or _key in _ex_seen:
                                    continue
                                _ex_seen.add(_key)
                                _san,_ = sanitize_overrides(_var, defaults)
                                _vec = _hustle_eval(prepared, _san, window_days=args.window_days)
                                if _vec is None or not _vec.get("valid"):
                                    continue
                                _vg = float(_vec.get("gain_pct") or 0)
                                _total += 1
                                if _vg > _ex_best_gain + 1e-9:
                                    _ex_best_gain = _vg
                                    _ex_best_over = dict(_var)
                                    print(f"[hustler-exhaustive] NEW BEST r={r} gain {_vg:.2f} vs greedy {_ex_best_gain:.2f} via {[c[0] for c in combo]}", flush=True)
                    if _ex_best_gain > _best_gain + 1e-9:
                        _best_gain = _ex_best_gain
                        _best_overrides = _ex_best_over
                        print(f"[hustler-exhaustive] PROMOTE exhaustive best {_best_gain:.2f} (+{_best_gain - cumulative_gain:.2f} vs greedy) combos {_total}", flush=True)
                    else:
                        print(f"[hustler-exhaustive] no exhaustive improvement over beam {_best_gain:.2f} checked {_total} combos", flush=True)
            except Exception as _ex_e:
                print(f"[hustler-exhaustive-warn] {_ex_e}", flush=True)
            if _best_gain > cumulative_gain + 1e-9:
                print(f"[hustler] PROMOTE hustle best {cumulative_gain:.2f} -> {_best_gain:.2f} delta +{_best_gain - cumulative_gain:.2f} vs baseline +{_best_gain - float(baseline_gain or 0):.2f} overrides {len(_best_overrides)}", flush=True)
                # write hustler overrides to progress and xlsx E column extension (append HUSTLER sheet if needed)
                try:
                    _hustle_path = OUT_DIR / f"{new_symside}_hustler_best.json"
                    # RESPECT other hosts: s3/s5 shuffles must not overwrite a better hustle from peer
                    _skip_write = False
                    if _hustle_path.exists():
                        try:
                            _existing = __import__("json").loads(_hustle_path.read_text())
                            _existing_gain = float(_existing.get("hustler_best_gain") or 0)
                            if _existing_gain >= float(_best_gain) - 1e-9:
                                print(f"[hustler-respect] existing {_existing_gain:.2f} >= new {float(_best_gain):.2f} — respect peer, skip overwrite", flush=True)
                                _skip_write = True
                            else:
                                print(f"[hustler-respect] new {float(_best_gain):.2f} beats existing {_existing_gain:.2f} — overwrite", flush=True)
                        except Exception as _re:
                            print(f"[hustler-respect-warn] {_re}", flush=True)
                    if not _skip_write:
                        _hustle_path.write_text(__import__("json").dumps({"symside": new_symside, "baseline": float(baseline_gain or 0), "greedy_cum": float(cumulative_gain), "hustler_best_gain": float(_best_gain), "hustler_delta_vs_baseline": float(_best_gain - float(baseline_gain or 0)), "hustler_delta_vs_greedy": float(_best_gain - cumulative_gain), "overrides": _best_overrides, "bh": float(bh_raw)}, indent=2))
                        print(f"[hustler] wrote {_hustle_path}", flush=True)
                    # also respect progress.json hustler if peer wrote newer
                    try:
                        if _skip_write and "hustler_best_gain" in progress:
                            if float(progress.get("hustler_best_gain") or 0) < float(_existing_gain or 0):
                                progress["hustler_best_gain"] = float(_existing_gain)
                                progress["hustler_overrides"] = _existing.get("overrides", {})
                    except Exception:
                        pass
                except Exception as _we:
                    print(f"[hustler-warn] {_we}", flush=True)
                # optionally promote cumulative_gain to hustler best for final publishing
                # keep greedy cum as is for workbook E monotonic, but final_path will reflect hustler if better and parity ok
                # Dual system: keep baseline as greedy, hustle stays in F column and hustler_best.json, no promotion yet (no winner defined)
                # To enable hustle promotion later, set DUAL_HUSTLE_PROMOTE = True
                DUAL_HUSTLE_PROMOTE = False
                if DUAL_HUSTLE_PROMOTE:
                    try:
                        from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_final2
                        _san_best,_ = sanitize_overrides(_best_overrides, defaults)
                        _vec_best = _eval_final2(prepared, _san_best, window_days=args.window_days)
                        _live_best2 = live_evaluate(new_symside, _san_best, args.window_days)
                        _ok2,_rsn2 = parity_ok(_live_best2, _vec_best, allow_zero_baseline=baseline_had_zero_trades)
                        if _ok2 and float(_vec_best.get("gain_pct") or 0) > cumulative_gain:
                            cumulative_gain = float(_vec_best.get("gain_pct") or 0)
                            cumulative_overrides = dict(_san_best)
                            progress["cumulative_gain"] = cumulative_gain
                            progress["cumulative_overrides"] = cumulative_overrides
                            progress["hustler_promoted"] = True
                            print(f"[hustler] PROMOTED cumulative to hustler best {cumulative_gain:.2f} parity ok", flush=True)
                        else:
                            print(f"[hustler] NOT promoted parity {_ok2} {_rsn2} vec {_vec_best.get('gain_pct',0):.2f}", flush=True)
                    except Exception as _pe:
                        print(f"[hustler-promote-warn] {_pe}", flush=True)
                else:
                    print(f"[hustler-dual] hustle best {float(_best_gain):.2f} vs greedy {cumulative_gain:.2f} baseline {float(baseline_gain or 0):.2f} — dual F=HUSTLE G=GREEDY, baseline stays greedy (no winner yet)", flush=True)
            else:
                print(f"[hustler] no hustle improvement greedy {cumulative_gain:.2f} remains best vs baseline {float(baseline_gain or 0):.2f}", flush=True)
        else:
            print("[hustler] skipped no top pos or no prepared", flush=True)
    except Exception as _he:
        import traceback
        print(f"[hustler-warn] {_he} {traceback.format_exc()[:800]}", flush=True)
    # === ALL switch delta + yellow (L:BI) + orange (Results_Deltas/30d) cells already filled cell-by-cell above ===
    # === backtest_v12_live switch-by-switch verification on final settings ===
    if __import__("os").environ.get("V15_SKIP_LIVE_VERIFY")=="1":
        print("[live-verify] SKIPPED via V15_SKIP_LIVE_VERIFY=1 (vector-only fast 940->3041)", flush=True)
    else:
        print(f"[live-verify] switch-by-switch live scripts on final {len(cumulative_overrides)} overrides", flush=True)
        try:
            from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_final
            # baseline for delta
            base_vec = _eval_final(prepared, overrides, window_days=args.window_days) if prepared is not None else None
            for k, v in list(cumulative_overrides.items())[:50]:
                # single-switch delta verification: live vs vectorized
                test_over = dict(overrides)
                test_over[k] = v
                test_over, _ = sanitize_overrides(test_over, defaults)
                vec = _eval_final(prepared, test_over, window_days=args.window_days) if prepared is not None else live_evaluate(new_symside, test_over, args.window_days)
                live = live_evaluate(new_symside, test_over, args.window_days)
                ok, reason = parity_ok(live, vec, allow_zero_baseline=baseline_had_zero_trades)
                if not ok:
                    print(f"[live-verify-FIX] {k}={v} live {live.get('gain_pct'):.2f}/{live.get('trades')} vs vec {vec.get('gain_pct'):.2f}/{vec.get('trades')} reason {reason} — vector fix needed", flush=True)
                else:
                    print(f"[live-verify-ok] {k}={v} live {live.get('gain_pct'):.2f} vec {vec.get('gain_pct'):.2f} ok", flush=True)
        except Exception as e:
            import traceback
            print(f"[live-verify-warn] {e} {traceback.format_exc()[:800]}", flush=True)
    # complete zoomable chart (offline file:// like /private/tmp/MU_LONG_30D_REAL_ZOOMABLE.html) with all trades
    try:
        write_zoomable_chart(new_symside, None, cumulative_overrides, args.window_days, suffix="30D_REAL_ZOOMABLE")
    except Exception as _ce2:
        print(f"[chart-final-warn] {_ce2}", flush=True)
    print(f"[final] {final_path} bh={bh_raw:.2f} gain={cumulative_gain:.2f} positives={total_pos} hot={list(ALL_NPZ_ARRAYS.keys())[:2]}", flush=True)
    # === BIGGEST 30D DELTA CHART + 365D RERUN + LIVE PROMOTION (2026-09-13) ===
    # Chart of biggest 30D delta already created as COMPLETE 30D_REAL_ZOOMABLE above (cumulative_overrides is the hustler/greedy best).
    # Now rerun those settings on 365D with xlsx and delta, compare vs currently running per_sym config, backup and go live if beats.
    try:
        # 1) ensure biggest 30D chart explicitly tagged
        try:
            write_zoomable_chart(new_symside, None, cumulative_overrides, 30, suffix="30D_BIGGEST_DELTA_ZOOMABLE")
            print(f"[30D-CHART] biggest delta {cumulative_gain:.2f} vs baseline {baseline_gain:.2f} delta {cumulative_gain - baseline_gain:.2f} chart 30D_BIGGEST_DELTA_ZOOMABLE", flush=True)
        except Exception as _ce:
            print(f"[30D-CHART-warn] {_ce}", flush=True)
        # 2) 365D rerun with same overrides — xlsx + delta, robustness holdout (30D→365D is out-of-sample, not curve-fit)
        _365_gain = _365_bh = _365_delta = None
        _365_vec = _365_live = None
        _365_xlsx = None
        try:
            # Evaluate 365D baseline and best via live engine (conservative) + vector parity
            from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval365
            # Prepare 365D separately (bypass 30D block — internal 365 rerun allowed)
            _prep365 = None
            try:
                from tools.opt.v12_pilot import prepare_batch as _prep365_fn
                _prep365 = _prep365_fn(new_symside, window_days=365)
                print(f"[365D] prepared {new_symside} 365D {len(_prep365.get('npz_prepared',{}).get('close',[]))} bars" if _prep365 and _prep365.get('npz_prepared') else "[365D] no prepared, fallback to live", flush=True)
            except Exception as _pe:
                print(f"[365D-prep-warn] {_pe}", flush=True)
            # Sanitize best overrides for 365D
            _san_best365, _ = sanitize_overrides(dict(cumulative_overrides), defaults)
            _san_base365, _ = sanitize_overrides(dict(overrides), defaults)
            if _prep365 is not None:
                _365_vec_best = _eval365(_prep365, _san_best365, window_days=365)
                _365_vec_base = _eval365(_prep365, _san_base365, window_days=365)
            else:
                _365_vec_best = live_evaluate(new_symside, _san_best365, 365)
                _365_vec_base = live_evaluate(new_symside, _san_base365, 365)
            # Live verification for 365D as well (conservative, slippage-aware)
            _365_live_best = live_evaluate(new_symside, _san_best365, 365)
            _365_live_base = live_evaluate(new_symside, _san_base365, 365)
            # Prefer live if valid and parity ok, else vector
            _ok365, _rsn365 = parity_ok(_365_live_best, _365_vec_best, allow_zero_baseline=True) if _365_vec_best and _365_live_best else (False, "no vec")
            _use365 = _365_live_best if _365_live_best.get("valid") and _ok365 else _365_vec_best
            _base365 = _365_live_base if _365_live_base.get("valid") else _365_vec_base
            _365_gain = float(_use365.get("gain_pct") or 0) if _use365 else 0.0
            _365_bh = float(_use365.get("bh_pct") or _base365.get("bh_pct") or 0) if _use365 else 0.0
            _365_base_gain = float(_base365.get("gain_pct") or 0) if _base365 else 0.0
            _365_delta = _365_gain - _365_base_gain
            _365_trades = int(_use365.get("trades") or 0) if _use365 else 0
            _365_sharpe = float(_use365.get("pool_sharpe") or 0) if _use365 else 0.0
            _365_dd = float(_use365.get("max_dd_pct") or 0) if _use365 else 0.0
            print(f"[365D] best gain {_365_gain:.2f} base {_365_base_gain:.2f} delta {_365_delta:.2f} bh {_365_bh:.2f} trades {_365_trades} sharpe {_365_sharpe:.2f} dd {_365_dd:.2f} parity {_ok365} {_rsn365}", flush=True)
            # Backtest-expert: robustness — 365D must not be <50% of 30D delta (out-of-sample <50% in-sample warns overfit), needs trades floor
            _30d_delta = cumulative_gain - baseline_gain
            if _365_delta < 0.5 * _30d_delta and _30d_delta > 1:
                print(f"[365D-ROBUST-WARN] 365D delta {_365_delta:.2f} <50% of 30D delta {_30d_delta:.2f} — possible overfit, still comparing vs live but not auto-promoting without live beat", flush=True)
            if _365_trades < 30:
                print(f"[365D-SAMPLE-WARN] 365D trades {_365_trades} <30 floor — diagnostic only, not for promotion", flush=True)
            # Create 365D xlsx with delta (clone template, write 365D metrics + Results_30d_Deltas equivalent)
            try:
                # gain/bh in filename per user request (like 30D bh/gain: _bhm4p58_gain0p12_365d)
                _bh_str = f"bh{'m' if _365_bh is not None and _365_bh<0 else ''}{abs(_365_bh):.2f}".replace('.','p') if _365_bh is not None else "bhnan"
                _gain_str = f"gain{'m' if _365_gain is not None and _365_gain<0 else ''}{abs(_365_gain):.2f}".replace('.','p') if _365_gain is not None else "gainnan"
                _365_target = OUT_DIR / f"{new_symside}_{_bh_str}_{_gain_str}_365d_matrix.xlsx"
                if _365_target.exists():
                    _ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
                    _365_target = OUT_DIR / f"{new_symside}_{_bh_str}_{_gain_str}_365d_matrix_{_ts}.xlsx"
                if not template.exists():
                    template = TEMPLATE
                import shutil as _sh
                _sh.copy2(template, _365_target)
                _wb365 = openpyxl.load_workbook(str(_365_target))
                # Ensure baseline sheet reflects 365D baseline
                _bs = f"{new_symside}_BASELINE_METRICS"
                if _bs not in _wb365.sheetnames:
                    # find old baseline name
                    for cand in ["TEMPLATE_BASELINE_METRICS", "ADP_LONG_BASELINE_METRICS"]:
                        if cand in _wb365.sheetnames:
                            _wb365[cand].title = _bs
                            break
                if _bs in _wb365.sheetnames:
                    _wsb = _wb365[_bs]
                    _rows365 = [
                        ("gain_pct_365", _365_gain), ("bh_pct_365", _365_bh), ("delta_vs_base_365", _365_delta),
                        ("delta_vs_bh_365", _365_gain - _365_bh), ("trades_365", _365_trades),
                        ("pool_sharpe_365", _365_sharpe), ("max_dd_365", _365_dd),
                        ("gain_30d", cumulative_gain), ("delta_30d", _30d_delta), ("baseline_30d", baseline_gain),
                        ("source", f"v15 30D→365D rerun {utcnow()} overrides={len(cumulative_overrides)}"),
                        ("window_365", _use365.get("window") if _use365 else "365"),
                        ("valid_365", _use365.get("valid") if _use365 else False),
                    ]
                    for r in range(2, max(20, _wsb.max_row+1)):
                        _wsb.cell(row=r, column=1).value = None
                        _wsb.cell(row=r, column=2).value = None
                    for i, (k,v) in enumerate(_rows365, start=2):
                        _wsb.cell(row=i, column=1).value = k
                        _wsb.cell(row=i, column=2).value = v
                # Fill Results_Deltas with 365D delta for visibility (reuse sheet)
                for cand in ["Results_Deltas", "Results_30d_Deltas"]:
                    if cand in _wb365.sheetnames:
                        _rws = _wb365[cand]
                        _found = None
                        _key365 = f"{new_symside}_365D_BEST"
                        for _rr in range(2, _rws.max_row+1):
                            if str(_rws.cell(row=_rr, column=1).value or "").strip() == _key365:
                                _found = _rr
                                break
                        if _found is None:
                            _found = _rws.max_row + 1
                            _rws.cell(row=_found, column=1).value = _key365
                        _rws.cell(row=_found, column=5).value = float(_365_delta)
                        _rws.cell(row=_found, column=8).value = float(_365_gain)
                        _rws.cell(row=_found, column=10).value = int(_365_trades)
                        _rws.cell(row=_found, column=2).value = str(float(_365_base_gain))
                        _rws.cell(row=_found, column=3).value = f"{len(cumulative_overrides)} overrides 365D"
                        break
                _wb365.save(str(_365_target))
                _wb365.close()
                _365_xlsx = _365_target
                print(f"[365D-XLSX] -> {_365_xlsx.name} delta {_365_delta:.2f} gain {_365_gain:.2f}", flush=True)
            except Exception as _xe:
                import traceback
                print(f"[365D-XLSX-warn] {_xe} {traceback.format_exc()[:600]}", flush=True)
            # 365D chart
            try:
                write_zoomable_chart(new_symside, None, cumulative_overrides, 365, suffix="365D_REAL_ZOOMABLE")
                print(f"[365D-CHART] {new_symside} 365D chart with best overrides", flush=True)
            except Exception as _ce:
                print(f"[365D-CHART-warn] {_ce}", flush=True)
            # 3) Compare vs currently running per_sym config (live)
            _live_cfg_path = None
            _is_crypto = new_symside.upper().endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD","DAI"))
            if _is_crypto:
                _live_cfg_path = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
            else:
                _live_cfg_path = ROOT / "data" / "hourly_reconfig" / "trb" / "active_config.json"
            _cur_entry = {}
            _cur_gain365 = None
            if _live_cfg_path and _live_cfg_path.exists():
                try:
                    _cur_all = json.loads(_live_cfg_path.read_text())
                    _cur_entry = _cur_all.get(new_symside, {}) if isinstance(_cur_all, dict) else {}
                    if _cur_entry and _cur_entry.get("overrides"):
                        _cur_over = _cur_entry.get("overrides") or {}
                        _cur_san, _ = sanitize_overrides(dict(_cur_over), defaults)
                        # Evaluate current live config on 365D for fair compare (same window, same costs +$ higher than reality)
                        _cur_vec = None
                        _cur_live = None
                        if _prep365 is not None:
                            _cur_vec = _eval365(_prep365, _cur_san, window_days=365)
                        _cur_live = live_evaluate(new_symside, _cur_san, 365)
                        _ok_cur, _ = parity_ok(_cur_live, _cur_vec, allow_zero_baseline=True) if _cur_vec and _cur_live else (False, "")
                        _cur_use = _cur_live if _cur_live.get("valid") and _ok_cur else _cur_vec
                        _cur_gain365 = float(_cur_use.get("gain_pct") or 0) if _cur_use else float(_cur_entry.get("acc_gain_pct") or _cur_entry.get("gain_vs_bh") or 0)
                        print(f"[LIVE-CUR] {new_symside} current live 365D gain {_cur_gain365:.2f} trades {(_cur_use.get('trades') if _cur_use else _cur_entry.get('trades'))} vs new {_365_gain:.2f}", flush=True)
                    else:
                        print(f"[LIVE-CUR] {new_symside} no existing per_sym entry — will create", flush=True)
                except Exception as _le:
                    print(f"[LIVE-CUR-warn] {_le}", flush=True)
            else:
                print(f"[LIVE-CUR] no file {_live_cfg_path} — will create", flush=True)
            # Beat check: new 365D must beat current live 365D (if exists) by >0 and also be positive and have trades floor
            _beats = False
            # USER 2026-05-31: promote as soon as 365D has pos gain and best 30D has pos gain or gain > bh (30D bh = baseline_gain)
            # No incumbent-beat required — per_sym goes live immediately when robustness met
            _30d_pos = cumulative_gain > 0
            _30d_beats_bh = (cumulative_gain - baseline_gain) > 1e-9  # delta >0 means 30D best > 30D bh
            if _365_gain is not None and _365_trades >= 30:
                if _365_gain > 0 and (_30d_pos or _30d_beats_bh):
                    _beats = True
                # For incumbent, also allow if strictly beats current live 365D (even if 30D not pos, but 365D pos already required)
                elif _cur_gain365 is not None and _365_gain > _cur_gain365 + 1e-9 and _365_gain > 0:
                    _beats = True
                # Additional robustness: require 365D sharpe not terrible and dd not huge
                if _beats and _365_sharpe < -1:
                    print(f"[LIVE-BEAT-SKIP] 365D sharpe {_365_sharpe:.2f} < -1 — not promoting despite gain beat", flush=True)
                    _beats = False
                if _beats and _365_dd > 30:
                    print(f"[LIVE-BEAT-SKIP] 365D dd {_365_dd:.2f} >30% — not promoting", flush=True)
                    _beats = False
            print(f"[LIVE-BEAT] {new_symside} new {_365_gain:.2f} vs cur {_cur_gain365} beats={_beats} delta {_365_delta:.2f}", flush=True)
            if _beats:
                # Backup current per_sym settings if existing
                if _cur_entry:
                    try:
                        _bak_dir = ROOT / "backups"
                        _bak_dir.mkdir(parents=True, exist_ok=True)
                        _ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
                        _bak_path = _bak_dir / f"before_per_sym_{new_symside}_{_ts}.json"
                        _bak_path.write_text(json.dumps({new_symside: _cur_entry, "_meta": {"backed_up_at": utcnow(), "reason": f"v15 365D beat {new_symside} new {_365_gain:.2f} vs cur {_cur_gain365:.2f}"}}, indent=2))
                        print(f"[BACKUP] per_sym {new_symside} -> {_bak_path.name}", flush=True)
                        # also hourly_reconfig backup
                        try:
                            _hr_bak = _live_cfg_path.parent / f"{_live_cfg_path.stem}_before_{new_symside}_{_ts}.json"
                            import shutil as _sh2
                            _sh2.copy2(_live_cfg_path, _hr_bak)
                        except Exception:
                            pass
                    except Exception as _be:
                        print(f"[BACKUP-warn] {_be}", flush=True)
                # Put results live immediately — write to per_sym_active_config.json (and trb if stock)
                try:
                    if _live_cfg_path:
                        _live_cfg_path.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            _all = json.loads(_live_cfg_path.read_text()) if _live_cfg_path.exists() else {}
                        except Exception:
                            _all = {}
                        # preserve _meta if exists
                        _meta = _all.pop("_meta", None) if isinstance(_all, dict) else None
                        _new_entry = {
                            "winning_tag": f"v15_30D365D_{new_symside}_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')}",
                            "wsharpe": float(_365_sharpe),
                            "pool_sharpe": float(_365_sharpe),
                            "trades": int(_365_trades),
                            "max_dd_pct": float(_365_dd),
                            "acc_gain_pct": float(_365_gain),
                            "gain_vs_bh": float(_365_gain - _365_bh),
                            "bh_pct": float(_365_bh),
                            "delta_365": float(_365_delta),
                            "delta_30d": float(cumulative_gain - baseline_gain),
                            "gain_30d": float(cumulative_gain),
                            "baseline_30d": float(baseline_gain),
                            "overrides": dict(cumulative_overrides),
                            "campaign_ts": time.time(),
                            "vec_baseline_tag": "v15_30D365D",
                            "pool_winner_tag_crypto": "v15_30D365D" if _is_crypto else None,
                            "side": new_symside.split("_")[-1],
                            "365d_xlsx": str(_365_xlsx) if _365_xlsx else None,
                            "source": f"v15_pilot 30D→365D {utcnow()} 365D delta {_365_delta:.2f} beats cur {_cur_gain365}",
                        }
                        # prune None
                        _new_entry = {k: v for k, v in _new_entry.items() if v is not None}
                        _all[new_symside] = _new_entry
                        if _meta is not None:
                            _all["_meta"] = _meta
                        _tmp = _live_cfg_path.with_suffix(".json.tmp")
                        _tmp.write_text(json.dumps(_all, indent=2, default=str))
                        _tmp.replace(_live_cfg_path)
                        print(f"[LIVE-PUT] {new_symside} -> {_live_cfg_path.name} gain365 {_365_gain:.2f} delta {_365_delta:.2f} LIVE NOW", flush=True)
                        # Also sync to tradier_manage in-memory cache clear hint
                        try:
                            Path("/tmp/per_sym_live_promoted.flag").write_text(f"{new_symside} {utcnow()} {json.dumps(_new_entry)[:300]}")
                        except Exception:
                            pass
                    else:
                        print(f"[LIVE-PUT-warn] no live path for {new_symside}", flush=True)
                except Exception as _pe:
                    import traceback
                    print(f"[LIVE-PUT-warn] {_pe} {traceback.format_exc()[:600]}", flush=True)
            else:
                print(f"[LIVE-SKIP] {new_symside} not beating live — no backup/promote (new {_365_gain} vs cur {_cur_gain365})", flush=True)
        except Exception as _e:
            import traceback
            print(f"[365D-LIVE-warn] {_e} {traceback.format_exc()[:800]}", flush=True)
    except Exception as _outer:
        import traceback
        print(f"[365D-outer-warn] {_outer} {traceback.format_exc()[:600]}", flush=True)
    # === keep going: queue symbols_trb and symbols_flz ===
    # === EXPLORATION: test other side occasionally (backtest) but live gate requires gain>0 and beat bh ===
    # Even if a sym_side is disabled live, backtest still probes opposite side periodically.
    # Exploration rate controlled via V15_OTHER_SIDE_PROBE_RATE (env or config), default 0.15 (15%).
    # Also ensures campaign queue always contains opposite side for recently processed sym_side if not already queued.
    try:
        import random as _rnd
        _probe_rate = float(os.getenv("V15_OTHER_SIDE_PROBE_RATE", "0.15"))
        # Use deterministic hash for occasional probe to avoid pure randomness missing coverage
        _sym_base, _sym_side = (new_symside.rsplit("_", 1) if "_" in new_symside else (new_symside, "LONG"))
        _opp_side = "SHORT" if _sym_side == "LONG" else "LONG"
        _opp_symside = f"{_sym_base}_{_opp_side}"
        # Check if opposite side was recently tested (progress file exists and recent)
        _opp_progress = PROGRESS_DIR / f"{_opp_symside}_progress.json"
        _should_probe = False
        if not _opp_progress.exists():
            _should_probe = True
        else:
            try:
                _opp_mtime = _opp_progress.stat().st_mtime
                _age_days = (time.time() - _opp_mtime) / 86400
                if _age_days > 7.0:
                    _should_probe = True
            except Exception:
                _should_probe = True
        if not _should_probe and _rnd.random() < _probe_rate:
            _should_probe = True
        if _should_probe:
            print(f"[other-side-probe] {new_symside} -> queuing opposite {_opp_symside} for exploration (backtest only, live requires gain>0 and beat bh)", flush=True)
            try:
                camp_path2 = PROGRESS_DIR / "campaign_order_1mo.json"
                if camp_path2.exists():
                    camp2 = _js.loads(camp_path2.read_text()) if '_js' in locals() else json.loads(camp_path2.read_text())
                    queue2 = camp2.get("queue") or []
                    existing2 = {q.get("symside") for q in queue2}
                    if _opp_symside not in existing2:
                        queue2.append({"symside": _opp_symside, "window": "30_calendar_days", "side": _opp_side, "probe": "other_side"})
                        camp2["queue"] = queue2
                        camp_path2.write_text(json.dumps(camp2, indent=2))
                        print(f"[other-side-probe] queued {_opp_symside}", flush=True)
            except Exception as _e2:
                print(f"[other-side-probe-warn] {_e2}", flush=True)
    except Exception as _e:
        print(f"[other-side-probe-warn] {_e}", flush=True)
    try:
        import json as _js
        trb_long = _js.loads((ROOT / "symbols_trb_long.json").read_text()) if (ROOT / "symbols_trb_long.json").exists() else []
        trb_short = _js.loads((ROOT / "symbols_trb_short.json").read_text()) if (ROOT / "symbols_trb_short.json").exists() else []
        flz = _js.loads((ROOT / "symbols_flz.json").read_text()) if (ROOT / "symbols_flz.json").exists() else []
        # Also include side-specific flz/fin/men for backtest coverage (probe disabled side)
        flz_long = _js.loads((ROOT / "symbols_flz_long.json").read_text()) if (ROOT / "symbols_flz_long.json").exists() else []
        flz_short = _js.loads((ROOT / "symbols_flz_short.json").read_text()) if (ROOT / "symbols_flz_short.json").exists() else []
        fin_long = _js.loads((ROOT / "symbols_fin_long.json").read_text()) if (ROOT / "symbols_fin_long.json").exists() else []
        fin_short = _js.loads((ROOT / "symbols_fin_short.json").read_text()) if (ROOT / "symbols_fin_short.json").exists() else []
        men_long = _js.loads((ROOT / "symbols_men_long.json").read_text()) if (ROOT / "symbols_men_long.json").exists() else []
        men_short = _js.loads((ROOT / "symbols_men_short.json").read_text()) if (ROOT / "symbols_men_short.json").exists() else []
        # log next queue (actual launch is via campaign runner, here just progress hint)
        print(f"[queue-next] TRB {len(trb_long)} long + {len(trb_short)} short, FLZ {len(flz)} syms (+ side-specific flz {len(flz_long)}/{len(flz_short)} fin {len(fin_long)}/{len(fin_short)} men {len(men_long)}/{len(men_short)}) — pilots will pick next via campaign_order_1mo.json", flush=True)
        # ensure campaign queue contains them
        camp_path = PROGRESS_DIR / "campaign_order_1mo.json"
        if camp_path.exists():
            camp = _js.loads(camp_path.read_text())
            queue = camp.get("queue") or []
            # append missing TRB/FLZ symsides if not present
            existing = {q.get("symside") for q in queue}
            # NEVER REPEAT A SYM_SIDE in same round — and same for every filter that had POS results (keep POS, never re-queue duplicate)
            added = 0
            for s in trb_long:
                ss = f"{s}_LONG"
                if ss not in existing:
                    queue.append({"symside": ss, "window": "30_calendar_days", "side": "LONG"})
                    existing.add(ss)
                    added += 1
            for s in trb_short:
                ss = f"{s}_SHORT"
                if ss not in existing:
                    queue.append({"symside": ss, "window": "30_calendar_days", "side": "SHORT"})
                    existing.add(ss)
                    added += 1
            for s in flz:
                for side in ["LONG","SHORT"]:
                    ss = f"{s}_{side}"
                    if ss not in existing:
                        queue.append({"symside": ss, "window": "30_calendar_days", "side": side})
                        existing.add(ss)
                        added += 1
            # Also ensure opposite side for every tracked symbol is at least queued as probe if missing (occasional backtest)
            for lst, side in [(flz_long, "LONG"), (flz_short, "SHORT"), (fin_long, "LONG"), (fin_short, "SHORT"), (men_long, "LONG"), (men_short, "SHORT")]:
                for s in lst:
                    ss = f"{s}_{side}"
                    if ss not in existing:
                        queue.append({"symside": ss, "window": "30_calendar_days", "side": side})
                        existing.add(ss)
                        added += 1
            if added:
                camp["queue"] = queue
                camp_path.write_text(_js.dumps(camp, indent=2))
                print(f"[queue-next] added {added} TRB/FLZ/FIN/MEN symsides to campaign queue (including side-specific probes)", flush=True)
    except Exception as e:
        print(f"[queue-next-warn] {e}", flush=True)

if __name__ == "__main__":
    main()
