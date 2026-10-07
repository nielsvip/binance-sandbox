#!/usr/bin/env python3
"""Correct: baseline = defaults (no overrides), then one switch = delta. Baseline blank if not pos, else value. <1s per 30D."""
import pathlib, sys, time, csv
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import openpyxl
from tools.opt.v12_pilot import evaluate_sanitized

tpl=ROOT/"SPREADSHEETS/TEMPLATE.xlsx"
wb=openpyxl.load_workbook(str(tpl), read_only=True, data_only=False)
switches=[]
for name in wb.sheetnames:
    if name.startswith("INSTRUCTIONS") or name in ("Results_30d_Deltas","TEMPLATE_BASELINE_METRICS","FILTER_DICTIONARY_V2","12SYM_PARITY"):
        continue
    ws=wb[name]
    for row in ws.iter_rows(min_row=3, max_col=2, values_only=True):
        sw,cand=row[0],row[1]
        if sw and isinstance(sw,str):
            switches.append((name, sw.strip(), cand))
print(f"switches {len(switches)}")

sym_sides=[("BNBUSDC","LONG"),("SNDK","LONG"),("MU","LONG"),("NVDA","LONG"),("CRWD","SHORT"),("NKE","SHORT")]

out=ROOT/"SPREADSHEETS/simple_one_switch_30D_correct.xlsx"
wb_out=openpyxl.Workbook()
ws_out=wb_out.active
ws_out.title="Results_30d_Deltas"
ws_out.append(["sym","switch","candidate","window","baseline_gain","switch_gain","F_delta","baseline_trades","switch_trades","F_filling","baseline_blank?"])

for sym, side in sym_sides:
    symside=f"{sym}_{side}"
    print(f"\n{symside} baseline defaults (no overrides) 30D...")
    t0=time.time()
    base=evaluate_sanitized(symside, {}, window_days=30)
    dt=time.time()-t0
    base_gain=float(base.get("gain_pct") or 0)
    base_tr=int(base.get("trades") or 0)
    print(f" base {base_gain:.4f} tr{base_tr} valid{base.get('valid')} in {dt:.2f}s")
    for sheet, sw, cand in switches:
        if cand is None:
            continue
        ov={}
        if isinstance(cand, str) and cand in ("True","False"):
            ov[sw]=cand=="True"
        else:
            ov[sw]=cand
        t1=time.time()
        r=evaluate_sanitized(symside, ov, window_days=30)
        dt2=time.time()-t1
        gain=float(r.get("gain_pct") or 0)
        tr=int(r.get("trades") or 0)
        delta=gain - base_gain
        # Baseline blank if not pos, else value - so F_filling only if delta>0 and tr>=2 and valid
        filling = delta>0 and tr>=2 and bool(r.get("valid"))
        # Also baseline blank logic: if delta<=0, baseline stays blank
        blank = "BLANK" if not filling else f"{delta:.4f}"
        ws_out.append([symside, sw, str(cand)[:20], 30, round(base_gain,4), round(gain,4), round(delta,4) if filling else 0, base_tr, tr, filling, blank])
        if filling:
            print(f"  POS {sw} {delta:+.4f} tr{tr} {dt2:.2f}s")
        # For demo, limit to 20 per sym then break
        if ws_out.max_row>50:
            break
    break

wb_out.save(out)
print(f"wrote {out} {out.stat().st_size/1024:.0f}K rows {ws_out.max_row-1}")
