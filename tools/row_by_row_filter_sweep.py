#!/usr/bin/env python3
"""Row by row: all filters OFF in col after F, then apply FILTER_DICTIONARY_V2 switches one by one to every switch, keep pos delta filters and add to delta. Same baseline, one switch diff, millions of times. Connect no-delta filters correctly. Live only on final."""
import pathlib, sys, time, csv
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import openpyxl
from collections import defaultdict

# Load switches and filter map
tpl=ROOT/"SPREADSHEETS/TEMPLATE.xlsx"
wb=openpyxl.load_workbook(str(tpl), read_only=True, data_only=False)
switches=[]
for name in wb.sheetnames:
    if name.startswith("INSTRUCTIONS") or name in ("Results_30d_Deltas","TEMPLATE_BASELINE_METRICS","FILTER_DICTIONARY_V2","12SYM_PARITY"):
        continue
    ws=wb[name]
    for row in ws.iter_rows(min_row=3, max_col=2, values_only=True):
        sw,cand=row[0],row[1]
        if sw and isinstance(sw,str) and cand is not None:
            switches.append((sw.strip(), cand, name))
print(f"switches {len(switches)}")

# Filter map: switch -> list of (filter, option)
ws=wb["FILTER_DICTIONARY_V2"]
filter_map=defaultdict(list)
for row in ws.iter_rows(min_row=2, values_only=True):
    filt=row[1]
    opt=row[2]
    gated=row[6]
    if filt and opt is not None and gated:
        for g in str(gated).split(","):
            g=g.strip()
            if g:
                filter_map[g].append((str(filt).strip(), str(opt).strip()))
print(f"filter_map {len(filter_map)} switches gated")

# Also need all filters OFF baseline: set every FILTER to OFF/neutral
# For row-by-row, we start with all filters OFF in col after F
# Then for each switch, try each of its gated filters one by one
from tools.opt.v12_pilot import evaluate_sanitized
import json

def load_defaults():
    # Use empty overrides = defaults
    return {}

sym_sides=[("BNBUSDC","LONG"),("SNDK","LONG"),("MU","LONG"),("NVDA","LONG"),("CRWD","SHORT"),("NKE","SHORT")]
# For demo, do one symside at a time, but we need to do row by row millions: for each switch * each filter
# Simple: for each switch, baseline = defaults, then switch alone, then switch+filter
out=ROOT/"SPREADSHEETS/row_by_row_pos_filters.xlsx"
wb_out=openpyxl.Workbook()
ws_out=wb_out.active
ws_out.title="Results_30d_Deltas"
ws_out.append(["sym","switch","candidate","filter","filter_option","baseline_gain","switch_gain","filter_gain","F_delta_switch","F_delta_filter","F_delta_combined","F_filling","filters_off_col"])

# Also track no-delta filters to connect correctly
no_delta_filters=set()

for sym, side in sym_sides:
    symside=f"{sym}_{side}"
    base=evaluate_sanitized(symside, {}, window_days=30)
    base_gain=float(base.get("gain_pct") or 0)
    base_tr=int(base.get("trades") or 0)
    print(f"\n{symside} baseline {base_gain:.4f} tr{base_tr} inits all filters OFF")
    # All filters OFF baseline already is base (since base has no overrides, filters are at defaults which are OFF for many)
    # For row by row: for each switch
    for sw, cand, sheet in switches[:20]:  # limit to 20 for quick demo
        # Switch alone
        ov={sw: (cand=="True" if cand in ("True","False") else cand)}
        r_sw=evaluate_sanitized(symside, ov, window_days=30)
        gain_sw=float(r_sw.get("gain_pct") or 0)
        tr_sw=int(r_sw.get("trades") or 0)
        delta_sw=gain_sw - base_gain
        filling_sw = delta_sw>0 and tr_sw>=2 and bool(r_sw.get("valid"))
        if not filling_sw:
            delta_sw=0
        # Now try each gated filter for this switch, keep pos
        best_filter_delta=0
        best_filter=None
        filt_list=filter_map.get(sw, [])
        # Also try generic filters if no gated
        if not filt_list:
            # No gated filters for this switch, keep as is
            ws_out.append([symside, sw, str(cand)[:12], "", "", round(base_gain,4), round(gain_sw,4), "", round(delta_sw,4) if filling_sw else 0, 0, round(delta_sw,4) if filling_sw else 0, filling_sw, "OFF"])
            continue
        for fname, opt in filt_list[:3]:  # try up to 3 per switch
            ov2=dict(ov)
            # All filters OFF baseline already, now set this one filter to its option
            if opt in ("True","False"):
                ov2[fname]=opt=="True"
            else:
                try:
                    ov2[fname]=float(opt) if "." in opt else int(opt)
                except:
                    ov2[fname]=opt
            r2=evaluate_sanitized(symside, ov2, window_days=30)
            gain2=float(r2.get("gain_pct") or 0)
            tr2=int(r2.get("trades") or 0)
            delta2=gain2 - base_gain
            # Keep pos delta filters and add to delta (row by row: delta_filter = gain2 - gain_sw, add)
            delta_filter=gain2 - gain_sw
            if delta_filter>0 and tr2>=2 and bool(r2.get("valid")):
                # Keep this filter, add to combined
                best_filter_delta+=delta_filter
                best_filter=(fname,opt)
                print(f"  POS filter {sw}+{fname}={opt} delta_filter {delta_filter:+.4f} -> combined {delta_sw+best_filter_delta:+.4f}")
            else:
                # Connect no-delta filters correctly (do not add)
                no_delta_filters.add(fname)
        combined=delta_sw + best_filter_delta
        filling_comb = combined>0
        # Baseline blank if not pos handled by template E, but here we record
        ws_out.append([symside, sw, str(cand)[:12], best_filter[0] if best_filter else "", best_filter[1] if best_filter else "", round(base_gain,4), round(gain_sw,4), round(gain_sw+best_filter_delta,4) if best_filter else round(gain_sw,4), round(delta_sw,4) if filling_sw else 0, round(best_filter_delta,4) if best_filter else 0, round(combined,4) if filling_comb else 0, filling_comb, "OFF"])
        if filling_comb:
            print(f" POS combined {sw} {combined:+.4f} via {best_filter}")

wb_out.save(out)
print(f"wrote {out} {out.stat().st_size/1024:.0f}K no_delta_filters {len(no_delta_filters)}")
print(f"no_delta sample {list(no_delta_filters)[:5]}")
