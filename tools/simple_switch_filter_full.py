#!/usr/bin/env python3
"""Full book: every F cell in all trading tabs — baseline defaults, then every switch x every filter, vector + live where possible.
Writes CSV with every F delta for one stock + one crypto.
Uses batched evaluate_many for speed (28 workers vector style).
"""
from pathlib import Path
import sys, csv, time
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl
from tools.opt.evaluate_v12 import evaluate, evaluate_many

def load_switches(template_path: Path):
    wb = openpyxl.load_workbook(str(template_path), data_only=True, read_only=True)
    switches = []
    for sheet in wb.sheetnames:
        if sheet in ("INSTRUCTIONS","INSTRUCTIONS_V2","FILTER_DICTIONARY_V2","FILTER_DICTIONARY","FILTER_DICTIONARY_V8","Results_30d_Deltas","TEMPLATE_BASELINE_METRICS","12SYM_PARITY","FORMULAS"):
            continue
        ws = wb[sheet]
        for r in range(3, ws.max_row+1):
            sw = ws.cell(r,1).value
            default = ws.cell(r,2).value
            if not sw or str(sw).strip() == "":
                continue
            if str(sw).startswith("Live"):
                continue
            switches.append((sheet, str(sw).strip(), default))
    wb.close()
    return switches

def load_filters(template_path: Path):
    wb = openpyxl.load_workbook(str(template_path), data_only=True, read_only=True)
    fsheet = None
    for cand in ("FILTER_DICTIONARY_V2","FILTER_DICTIONARY_V8","FILTER_DICTIONARY"):
        if cand in wb.sheetnames:
            fsheet = cand
            break
    ws = wb[fsheet]
    hdr = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    try:
        filt_idx = hdr.index("Filter")
        opt_idx = hdr.index("Option Value")
    except:
        filt_idx, opt_idx = 1, 2
    filters = []
    for r in range(2, ws.max_row+1):
        row = list(ws.iter_rows(min_row=r, max_row=r, values_only=True))[0]
        filt = row[filt_idx]
        opt = row[opt_idx]
        if not filt or opt is None:
            continue
        filters.append((str(filt).strip(), str(opt).strip()))
    wb.close()
    return filters

def coerce(v: str):
    vs = str(v).strip()
    if vs.upper() == "TRUE": return True
    if vs.upper() == "FALSE": return False
    if vs.upper() == "OFF": return "OFF"
    try:
        if "," in vs and vs.replace(",","").replace(".","",1).isdigit():
            vs = vs.replace(",", ".")
        if vs.replace(".","",1).replace("-","",1).isdigit():
            return float(vs) if "." in vs else int(vs)
    except:
        pass
    return vs

def alt_for(default):
    if isinstance(default, bool):
        return not default
    if isinstance(default, str):
        d = default.strip().upper()
        if d == "TRUE": return False
        if d == "FALSE": return True
        if d == "OFF": return "15m"
        if d in ("15M","1H","4H","D"): return "OFF"
    try:
        if isinstance(default, (int,float)):
            return float(default)*1.5
    except:
        pass
    return default

def run_one_symbol(symside, window, template_path, out_path):
    switches = load_switches(template_path)
    filters = load_filters(template_path)
    print(f"{symside} {window}d: {len(switches)} switches x {len(filters)} filters = {len(switches)*len(filters)} F cells in {len(set(s for s,_,_ in switches))} trading tabs")
    base = evaluate(symside, {}, window_days=window)
    bg = float(base.get("gain_pct") or 0)
    bt = int(base.get("trades") or 0)
    print(f"  baseline gain {bg:.4f} trades {bt} bh {base.get('bh_pct')} valid {base.get('valid')}")
    # Build overrides list for batch
    overrides_list = []
    meta = []  # parallel list of (sheet, switch, alt, filt, fval)
    for sheet, sw, default in switches:
        alt = alt_for(default)
        if alt == default:
            continue
        for filt, fval in filters:
            overrides_list.append({sw: alt, filt: coerce(fval)})
            meta.append((sheet, sw, alt, filt, fval))
    print(f"  evaluating {len(overrides_list)} combos via evaluate_many ...")
    t0=time.time()
    # batch in chunks of 200 to avoid OOM
    chunk=200
    results=[]
    for i in range(0, len(overrides_list), chunk):
        chunk_ovs = overrides_list[i:i+chunk]
        res = evaluate_many(symside, chunk_ovs, window_days=window)
        results.extend(res)
        print(f"    chunk {i//chunk+1}/{(len(overrides_list)+chunk-1)//chunk} done {len(results)}/{len(overrides_list)} elapsed {time.time()-t0:.1f}s", flush=True)
    print(f"  done {len(results)} evals in {time.time()-t0:.1f}s")
    # Write CSV row by row, column by column
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w=csv.writer(f)
        w.writerow(["switch_sheet","switch","switch_value","filter","filter_value","baseline_gain","new_gain","delta","baseline_trades","new_trades","delta_trades","window_days","symside","valid","pool_sharpe","max_dd_pct"])
        for (sheet, sw, alt, filt, fval), res in zip(meta, results):
            ng=float(res.get("gain_pct") or 0)
            nt=int(res.get("trades") or 0)
            w.writerow([sheet, sw, alt, filt, fval, f"{bg:.6f}", f"{ng:.6f}", f"{ng-bg:.6f}", bt, nt, nt-bt, window, symside, res.get("valid"), res.get("pool_sharpe"), res.get("max_dd_pct")])
    print(f"Wrote {out_path} {out_path.stat().st_size//1024}K")
    return out_path

if __name__ == "__main__":
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument("--window-days", type=int, default=30)
    args=ap.parse_args()
    tpl = ROOT / "SPREADSHEETS/TEMPLATE.xlsx"
    # Stock + crypto as requested
    for sym in ["SNDK_LONG","ZECUSDC_LONG"]:
        out = ROOT / f"data/reports/simple_full_{sym}_{args.window_days}d.csv"
        run_one_symbol(sym, args.window_days, tpl, out)
