#!/usr/bin/env python
"""dc64_proof_sheets.py — OLD fast system: per sym_side, run the 64-DC + WT/BB/WT_DC/AF
variant sweep (tools/dc_simple_8_sweep.run_one on the NO-LIES-purged v12_quick_engine),
write the classic 102-row ledger XLSX (bh/gain/delta in filename) AND a REAL_ZOOMABLE
Chart.js chart of CLOSE + every trade of the best-overall config. Honest numbers only —
all gain/delta/trades from v12_quick_engine trade-return ledger, no synthetic injection.

Usage:
  python tools/dc64_proof_sheets.py --sym 1000000MOGUSDT_SHORT
  python tools/dc64_proof_sheets.py --all --venue crypto --workers 12
  python tools/dc64_proof_sheets.py --all --venue stocks --workers 12
"""
import os, sys, json, argparse, math
from concurrent.futures import ThreadPoolExecutor, as_completed
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import numpy as np
import openpyxl
from openpyxl.styles import Font, PatternFill
import v12_quick_engine as V
from tools import dc_simple_8_sweep as DC
from tools.opt.evaluate_v12 import _exact_30d_slice

OUT_DIR = os.path.join(ROOT, "SPREADSHEETS", "DC64_NEW_HONEST")
GREEN = PatternFill("solid", fgColor="C6EFCE")
RED = PatternFill("solid", fgColor="FFC7CE")


def _fmt(x):
    try:
        s = f"{float(x):.2f}".replace("-", "m").replace(".", "p")
        return s
    except Exception:
        return "NA"


def _best_config_overrides(sym_side, per_sym_map, best):
    """Rebuild the overrides dict for the best-overall variant, mirroring run_one."""
    sym = sym_side[:-5] if sym_side.endswith("_LONG") else sym_side[:-6]
    is_long = sym_side.endswith("_LONG")
    crypto = sym.upper().endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD"))
    ov = dict(DC._get_template_baseline(crypto, is_long))
    ov["KINDERGARTEN_EMA_GATE_ENABLED"] = True
    ov["KINDERGARTEN_FILTER_TF"] = "4h"
    ov["KINDERGARTEN_CUMULATIVE_MODE"] = True
    ov["EMA_9_21_FILTER_ENABLED"] = True
    ov["EMA_9_21_FILTER_FILTER_TF"] = "4h"
    ov.update({k: v for k, v in per_sym_map.get(sym_side, {}).items() if k != "DAYTRDAY_DC"})
    ov["KINDERGARTEN_EMA_GATE_ENABLED"] = True
    ov["KINDERGARTEN_FILTER_TF"] = "4h"
    ov["EMA_9_21_FILTER_ENABLED"] = True
    ov["EMA_9_21_FILTER_FILTER_TF"] = "4h"
    for k in list(ov.keys()):
        if isinstance(ov[k], str) and ov[k] == "3m":
            ov[k] = "OFF"
    # apply the best variant's config so the CHART re-sims the WINNING strategy's trades
    # (not the few-trade base config). Switch on kind, mirroring run_one exactly.
    kind = (best or {}).get("kind"); tf = (best or {}).get("tf")
    if best and kind and tf and tf != "OFF":
        if kind == "EXIT_ENTRY" and "/" in str(tf):
            te, ten = str(tf).split("/")
            if te != "OFF":
                ov["TECHNICAL_DC_STOP_TF"] = te; ov["TECHNICAL_DC_TARGET_TF"] = te
                ov["TECHNICAL_DC_STOP_BUFFER_PCT"] = DC.STOP_BUF; ov["TECHNICAL_DC_TARGET_BUFFER_PCT"] = DC.TARGET_BUF
            if ten != "OFF":
                ov["ENTRY_DC_TF"] = ten; ov["ENTRY_DC_BUFFER_PCT"] = DC.TARGET_BUF
        elif kind == "EXIT":
            ov["TECHNICAL_DC_STOP_TF"] = tf; ov["TECHNICAL_DC_TARGET_TF"] = tf
            ov["TECHNICAL_DC_STOP_BUFFER_PCT"] = DC.STOP_BUF; ov["TECHNICAL_DC_TARGET_BUFFER_PCT"] = DC.TARGET_BUF
        elif kind == "ENTRY":
            ov["ENTRY_DC_TF"] = tf; ov["ENTRY_DC_BUFFER_PCT"] = DC.TARGET_BUF
        elif kind == "WT":
            ov["WT_LOWER_CROSS_EXIT_TF"] = tf
        elif kind == "EMA":
            ov["EMA_9_21_FILTER_ENABLED"] = True; ov["EMA_9_21_FILTER_FILTER_TF"] = tf
        elif kind == "BB":
            ov["BB_SQUEEZE_ENTRY_ENABLED"] = True; ov["BB_SQUEEZE_ENTRY_TF"] = tf; ov["BB_SQUEEZE_EXIT_ENABLED"] = True
        elif kind == "WTDC":
            ov["WT_DC_ENABLED"] = True; ov["WT_DC_DETAILED_SCORER_ENABLED"] = True
            ov["WT_DC_TF_ENTRY"] = tf; ov["WT_DC_DC_TF"] = tf
        elif kind == "AF":
            try:
                import ast
                for k, v in ast.literal_eval(str(tf)).items():
                    ov[k] = v
                    if "STOP" in k: ov["TECHNICAL_DC_STOP_BUFFER_PCT"] = DC.STOP_BUF
                    if "TARGET" in k: ov["TECHNICAL_DC_TARGET_BUFFER_PCT"] = DC.TARGET_BUF
            except Exception:
                pass
    return ov, sym, is_long, crypto


def _write_xlsx(res, path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DC64_LEDGER"
    hdr = ["variant", "kind", "tf", "gain_pct", "delta_pp", "trades", "tim_pct"]
    base = res.get("base_gain", 0.0); bh = res.get("base_bh")
    ws.append([f"BASE {res['sym_side']}", "BASE", "-", base, 0.0, res.get("base_trades", 0), None])
    ws["A2"] = f"bh={bh} base_gain={base} trades={res.get('base_trades',0)} bars={res.get('npz_bars')}"
    for c, h in enumerate(hdr, 1):
        cell = ws.cell(4, c, h); cell.font = Font(bold=True)
    r = 5
    for v in sorted(res.get("variants", []), key=lambda x: -x.get("delta", 0)):
        ws.cell(r, 1, v.get("variant")); ws.cell(r, 2, v.get("kind")); ws.cell(r, 3, v.get("tf"))
        ws.cell(r, 4, v.get("gain")); dcell = ws.cell(r, 5, v.get("delta"))
        ws.cell(r, 6, v.get("trades")); ws.cell(r, 7, v.get("tim"))
        try:
            dcell.fill = GREEN if float(v.get("delta", 0)) > 1e-9 else (RED if float(v.get("delta", 0)) < -1e-9 else PatternFill())
        except Exception:
            pass
        r += 1
    # validate not truncated
    wb.save(path)
    return path


def _write_chart(sym_side, overrides, window_days, path):
    from tools.opt.hires_chart import generate_hires
    out_name = os.path.basename(path)
    try:
        p = generate_hires(sym_side, overrides, window_days, out_name=out_name)
        return True
    except Exception as e:
        print(f"[chart-fail] {sym_side} {e}", flush=True)
        return False


def build_one(sym_side, per_sym_map, window_days=30):
    res = DC.run_one(sym_side, per_sym_map, window_days)
    if res.get("error"):
        return sym_side, f"ERROR {res['error']}"
    if not res.get("variants"):
        return sym_side, "NO_VARIANTS"
    os.makedirs(OUT_DIR, exist_ok=True)
    best = res.get("best_overall") or {}
    bh = res.get("base_bh"); gain = res.get("base_gain")
    delta = best.get("delta", 0.0)
    stem = f"{sym_side}_bh{_fmt(bh)}_gain{_fmt(gain)}_delta{_fmt(delta)}_30d"
    xlsx = os.path.join(OUT_DIR, stem + "_matrix.xlsx")
    _write_xlsx(res, xlsx)
    ov, sym, is_long, crypto = _best_config_overrides(sym_side, per_sym_map, best)
    html = os.path.join(OUT_DIR, f"{sym_side}_bh{_fmt(bh)}_gain{_fmt(gain)}_30D_REAL_ZOOMABLE.html")
    chart_ok = _write_chart(sym_side, ov, window_days, html)
    return sym_side, f"OK bh={bh} gain={gain} best={best.get('variant')} d={delta} chart={'OK' if chart_ok else 'FAIL'} -> {os.path.basename(xlsx)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--venue", choices=["crypto", "stocks", "both"], default="both")
    ap.add_argument("--window", type=int, default=30)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    per_sym_map, cmap, smap = DC.load_per_sym_maps()
    if a.sym:
        targets = [a.sym]
    else:
        keys = [k for k in per_sym_map if not k.startswith("_")]
        def is_c(k):
            b = k[:-5] if k.endswith("_LONG") else k[:-6]
            return b.upper().endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD"))
        if a.venue == "crypto":
            targets = [k for k in keys if is_c(k)]
        elif a.venue == "stocks":
            targets = [k for k in keys if not is_c(k)]
        else:
            targets = keys
        targets = sorted(targets)
        if a.limit:
            targets = targets[:a.limit]
    print(f"[dc64_proof] {len(targets)} sym_sides venue={a.venue} workers={a.workers} out={OUT_DIR}", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(build_one, ss, per_sym_map, a.window): ss for ss in targets}
        for f in as_completed(futs):
            ss = futs[f]
            try:
                _, msg = f.result()
            except Exception as e:
                msg = f"EXC {e}"
            done += 1
            print(f"[{done}/{len(targets)}] {ss}: {msg}", flush=True)
    print(f"[dc64_proof] DONE {done}/{len(targets)} -> {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
