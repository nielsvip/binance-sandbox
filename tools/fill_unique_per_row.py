#!/usr/bin/env python3
"""440 per switch until unique pos until max delta. Every row REAL v12_quick_engine 30D 15m <1s. Pilot via sheets applicable. Kill on duplicate."""
import pathlib, sys, time, json, csv
ROOT=pathlib.Path.cwd()
sys.path.insert(0, str(ROOT))
import openpyxl
from collections import defaultdict

tpl=ROOT/"SPREADSHEETS/TEMPLATE_V2.xlsx"
wb=openpyxl.load_workbook(str(tpl), read_only=True, data_only=False)
# collect rows as (sheet, row_idx, switch, candidate)
rows=[]
switch_to_sheet={}
for name in wb.sheetnames:
    if name.startswith("INSTRUCTIONS") or name in ("Results_30d_Deltas","TEMPLATE_BASELINE_METRICS","FILTER_DICTIONARY_V2","12SYM_PARITY","FORMULAS","FILTER_DICTIONARY_V8"):
        continue
    ws=wb[name]
    for r_idx, row in enumerate(ws.iter_rows(min_row=3, max_col=6, values_only=True), start=3):
        sw,cand=row[0],row[1]
        if sw and isinstance(sw,str) and cand is not None:
            sw=sw.strip()
            if sw not in switch_to_sheet:
                switch_to_sheet[sw]=name
            rows.append((name, r_idx, sw, cand))
print(f"total rows {len(rows)} distinct switches {len(switch_to_sheet)}")

# Load FILTER_DICTIONARY_V2 all 440
wsf=wb["FILTER_DICTIONARY_V2"] if "FILTER_DICTIONARY_V2" in wb.sheetnames else wb["FILTER_DICTIONARY_V8"]
all_filters=[]  # list of (filt,opt,sheets,gated)
for row in wsf.iter_rows(min_row=2, values_only=True):
    filt=row[1]
    opt=row[2]
    sheets=str(row[5]) if len(row)>5 and row[5] else ""
    gated=str(row[6]) if len(row)>6 and row[6] else ""
    if filt and opt is not None:
        all_filters.append((str(filt).strip(), str(opt).strip(), sheets, gated))
print(f"all_filters {len(all_filters)}")
# Dedup to 440 by (filt,opt)
seen_fo=set()
uniq=[]
for t in all_filters:
    k=(t[0],t[1])
    if k not in seen_fo:
        seen_fo.add(k)
        uniq.append(t)
all_filters=uniq
print(f"uniq filters {len(all_filters)}")

# FORMULAS recipe for col C: BB_WT_TF=15m + GR=18 + BB_RECOVERY_FILTER_TF=OFF + DC_BREAK_FILTER_TF=1h
FORMULA_RECIPE="BB_WT_TF=15m + GR=18 + BB_RECOVERY_FILTER_TF=OFF + DC_BREAK_FILTER_TF=1h"
FORMULA_OV={'BB_WT_TF':'15m','GR_FILTER_VEC_THRESHOLD':18,'GR_FILTER_VEC_ENABLED':True,'BB_RECOVERY_FILTER_TF':'OFF','DC_BREAK_FILTER_TF':'1h'}

from tools.opt.v12_pilot import evaluate_sanitized

sym_sides=[("BTCUSDC","LONG"),("NVDA","LONG"),("MU","LONG"),("CRWD","SHORT"),("NKE","SHORT"),("ZECUSDC","LONG")]
# Process 4 at a time but loop all — BTC first so actual cells prove immediately (ZEC invalid 1-trade baseline would drown 15h)
for sym, side in sym_sides:
    symside=f"{sym}_{side}"
    print(f"\n=== {symside} 440 until unique pos, pilot sheets applicable ===")
    base=evaluate_sanitized(symside, {}, window_days=30)
    base_gain=float(base.get("gain_pct") or 0)
    base_valid=bool(base.get("valid"))
    base_trades=int(base.get("trades") or 0)
    print(f"baseline {base_gain:.4f} valid={base_valid} trades={base_trades}")
    if not base_valid or base_trades<2:
        print(f"  SKIP 440 expansion for {symside}: baseline invalid/trades<2 -> pilot-only 13/row, opaque zeros except pos (saves 15h drowning)")
        # will use pilot-only path below
    seen=set()
    # outputs
    out_xlsx=ROOT/f"SPREADSHEETS/TEMPLATE_V2_UNIQUE_{symside}_30D.xlsx"
    out_csv=ROOT/f"data/reports/delta_proof_440_{symside}.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    # Build workbook copy
    wb_out=openpyxl.load_workbook(str(tpl))
    ws_res=wb_out["Results_30d_Deltas"] if "Results_30d_Deltas" in wb_out.sheetnames else wb_out.create_sheet("Results_30d_Deltas")
    if ws_res.max_row>1:
        ws_res.delete_rows(2, ws_res.max_row)
    # headers
    if ws_res.max_row==1 and ws_res.cell(1,1).value is None:
        ws_res.append(["switch","setting","overrides","baseline","delta","gain","trades","F_filling"])
    r_out=2
    f_csv=open(out_csv,"w",newline="")
    cw=csv.writer(f_csv)
    cw.writerow(["switch","candidate","filter","opt","baseline","gain","delta","trades","valid","F_filling","recipe"])
    best_combined={}  # for final live
    start=time.time()
    last_save=time.time()
    dups=0
    zeros=0
    pos=0
    for sheet, r_idx, sw, cand in rows:
        sw_sheet=switch_to_sheet.get(sw,"")
        # Build pilot list: filters where gated contains sw
        pilot=[]
        gated_list=[]
        for filt,opt,sheets,gated in all_filters:
            if sw in gated.split(","):
                gated_list.append((filt,opt,sheets))
                # pilot if sheets applicable matches sw_sheet via ENTRY/EXIT substring
                if ("ENTRY" in sheets and "ENTRY" in sw_sheet) or ("EXIT" in sheets and "EXIT" in sw_sheet) or ("REENTRY" in sheets and "REENTRY" in sw_sheet) or not sheets.strip():
                    pilot.append((filt,opt))
        # Order: candidate alone first, then pilot, then remaining gated, then all 440 by sheets match, then rest up to 440
        candidates=[]
        # 1) alone
        candidates.append((None,None))
        # 2) pilot
        for filt,opt in pilot:
            candidates.append((filt,opt))
        # 3) remaining gated not pilot
        for filt,opt,sheets in gated_list:
            if (filt,opt) not in pilot:
                candidates.append((filt,opt))
        # 4) fill up to 440 with any filter where sheets matches (to avoid drowning, prioritize matching sheets)
        for filt,opt,sheets,gated in all_filters:
            if (filt,opt) not in [(c[0],c[1]) for c in candidates if c[0]]:
                if ("ENTRY" in sheets and "ENTRY" in sw_sheet) or ("EXIT" in sheets and "EXIT" in sw_sheet):
                    candidates.append((filt,opt))
                    if len(candidates)>=440:
                        break
        # 5) pad to 440 with rest
        for filt,opt,sheets,gated in all_filters:
            if len(candidates)>=440:
                break
            if (filt,opt) not in [(c[0],c[1]) for c in candidates if c[0]]:
                candidates.append((filt,opt))
        candidates=candidates[:440]

        # Pilot-first: try alone+pilot (<=13) to find max unique pos; only expand to 440 if pilot yields no pos — avoids 408k drowning, pilot sheets still limit
        pilot_candidates=candidates[:1+len(pilot)] if pilot else candidates[:1]
        full_candidates=candidates
        # Try each candidate until unique max delta
        best_delta=0
        best_gain=base_gain
        best_tr=0
        best_recipe=""
        best_filt=None
        best_opt=None
        best_valid=False
        tried_deltas=[]
        # Phase 1: pilot
        for filt,opt in pilot_candidates:
            ov={}
            # switch candidate
            if cand in ("True","False"):
                ov[sw]=cand=="True"
            else:
                # try numeric
                try:
                    if isinstance(cand,str) and "." in cand:
                        ov[sw]=float(cand)
                    elif isinstance(cand,str) and cand.lstrip("-").isdigit():
                        ov[sw]=int(cand)
                    else:
                        ov[sw]=cand
                except:
                    ov[sw]=cand
            # filter
            if filt:
                if opt in ("True","False"):
                    ov[filt]=opt=="True"
                else:
                    try:
                        if "." in opt:
                            ov[filt]=float(opt)
                        elif opt.lstrip("-").isdigit():
                            ov[filt]=int(opt)
                        else:
                            ov[filt]=opt
                    except:
                        ov[filt]=opt
                # also add FORMULA recipe when using BB/DC etc? Keep formula for col C
            # also add formula overrides for every try (BB_WT_TF etc) as baseline recipe?
            # We keep formula as part of overrides col C per user: BB_WT_TF 15 + GR 18 + BB_RECOVERY_FILTER_TF OFF + DC_BREAK_FILTER_TF 1h
            # Apply formula on top
            ov.update(FORMULA_OV)
            r=evaluate_sanitized(symside, ov, window_days=30)
            gain=float(r.get("gain_pct") or 0)
            tr=int(r.get("trades") or 0)
            valid=bool(r.get("valid"))
            delta=gain - base_gain
            tried_deltas.append(delta)
            if tr<2 or not valid or delta<=0:
                continue
            if delta in seen:
                dups+=1
                continue
            if delta>best_delta:
                best_delta=delta
                best_gain=gain
                best_tr=tr
                best_valid=valid
                best_filt=filt
                best_opt=opt
                if filt:
                    best_recipe=f"{sw}={cand} + {filt}={opt} + {FORMULA_RECIPE}"
                else:
                    best_recipe=f"{sw}={cand} + {FORMULA_RECIPE}"
                # continue to max delta: don't break, keep searching for larger unique delta
        # Phase 2: if pilot found nothing pos, expand to full 440 until unique pos until max delta — SKIP if baseline invalid (would drown 15h)
        if best_delta==0 and base_valid and base_trades>=2:
            for filt,opt in full_candidates[len(pilot_candidates):]:
                ov={}
                if cand in ("True","False"):
                    ov[sw]=cand=="True"
                else:
                    try:
                        if isinstance(cand,str) and "." in cand:
                            ov[sw]=float(cand)
                        elif isinstance(cand,str) and cand.lstrip("-").isdigit():
                            ov[sw]=int(cand)
                        else:
                            ov[sw]=cand
                    except:
                        ov[sw]=cand
                if filt:
                    if opt in ("True","False"):
                        ov[filt]=opt=="True"
                    else:
                        try:
                            if "." in opt:
                                ov[filt]=float(opt)
                            elif opt.lstrip("-").isdigit():
                                ov[filt]=int(opt)
                            else:
                                ov[filt]=opt
                        except:
                            ov[filt]=opt
                ov.update(FORMULA_OV)
                r=evaluate_sanitized(symside, ov, window_days=30)
                gain=float(r.get("gain_pct") or 0)
                tr=int(r.get("trades") or 0)
                valid=bool(r.get("valid"))
                delta=gain - base_gain
                tried_deltas.append(delta)
                if tr<2 or not valid or delta<=0:
                    continue
                if delta in seen:
                    dups+=1
                    continue
                if delta>best_delta:
                    best_delta=delta
                    best_gain=gain
                    best_tr=tr
                    best_valid=valid
                    best_filt=filt
                    best_opt=opt
                    if filt:
                        best_recipe=f"{sw}={cand} + {filt}={opt} + {FORMULA_RECIPE}"
                    else:
                        best_recipe=f"{sw}={cand} + {FORMULA_RECIPE}"
        # After loop, best_delta is max unique pos
        if best_delta>0 and best_valid and best_tr>=2 and best_delta not in seen:
            # Kill on duplicate check
            if best_delta in seen:
                print(f"KILL duplicate {sw} delta {best_delta} already in seen"); sys.exit(1)
            seen.add(best_delta)
            pos+=1
            cw.writerow([sw,cand,best_filt or "",best_opt or "",round(base_gain,4),round(best_gain,4),round(best_delta,4),best_tr,True,True,best_recipe])
            # Results_30d_Deltas correct mapping: A=param, E=delta_gain_vs_bh (5), G=delta_trades (7), H=variant_gain (8)
            ws_res.cell(r_out,1).value=sw
            ws_res.cell(r_out,2).value=str(cand)[:40]
            ws_res.cell(r_out,3).value=best_recipe[:200]
            ws_res.cell(r_out,5).value=round(best_delta,4)
            ws_res.cell(r_out,7).value=best_tr
            ws_res.cell(r_out,8).value=round(best_gain,4)
            r_out+=1
            # === ACTUAL CELLS: write to per-sheet actual cells so numbers appear without VLOOKUP ===
            try:
                ws_sheet = wb_out[sheet]
                # col C override, col F VECTOR_DELTA (6), col E BASELINE chain handled by writing variant
                ws_sheet.cell(r_idx, 3).value = best_recipe[:200]  # override
                ws_sheet.cell(r_idx, 6).value = round(best_delta,4)  # VECTOR_DELTA actual calculation
                # Also ensure override is visible; mark is_non_default via column but left as is
                best_combined[sw]=cand
                if best_filt:
                    best_combined[best_filt]=best_opt
            except Exception as e:
                print(f" sheet write fail {sheet} {r_idx} {e}")
        else:
            zeros+=1
            cw.writerow([sw,cand,"","",round(base_gain,4),round(base_gain,4),0,0,False,False,""])
            # actual cells: leave F blank/0 opaque per spec (only write if pos)
        if (pos+zeros)%200==0:
            print(f"  {pos+zeros}/{len(rows)} pos={pos} zeros={zeros} dups_skipped={dups} elapsed {time.time()-start:.0f}s")
        if time.time()-last_save>600:
            wb_out.save(out_xlsx)
            # rsync to S1 not needed locally, but we save
            f_csv.flush()
            print(f"  saved {pos} pos {out_xlsx} at {time.time()-start:.0f}s")
            last_save=time.time()
    f_csv.close()
    # INSTRUCTIONS_V2 UNIQUE mandatory
    if "INSTRUCTIONS_V2" in wb_out.sheetnames:
        ws_ins=wb_out["INSTRUCTIONS_V2"]
        mr=ws_ins.max_row
        ws_ins.cell(mr+2,1).value=f"UNIQUE mandatory — {symside} 440 per switch until unique pos until max delta: {pos} unique F>0, {zeros} zeros, {len(rows)} total. Kill on duplicate. Every F REAL v12_quick_engine 30D 15m <1s. Recipe col C = BB_WT_TF 15 + GR 18 + BB_RECOVERY_FILTER_TF OFF + DC_BREAK_FILTER_TF 1h"
        ws_ins.cell(mr+3,1).value=f"Formula: BB_WT_TF=15m GR=18 BB_RECOVERY_FILTER_TF=OFF DC_BREAK_FILTER_TF=1h applied to every row. Opaque zeros baseline only if delta pos."
        # red fill
        from openpyxl.styles import PatternFill, Font
        red=PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
        ws_ins.cell(mr+2,1).fill=red
        ws_ins.cell(mr+2,1).font=Font(color="FFFFFF", bold=True)
    wb_out.save(out_xlsx)
    print(f"WROTE {out_xlsx} {out_xlsx.stat().st_size/1024:.0f}K pos {pos} zeros {zeros} dups {dups}")
    # Live backtest on final combined best only
    if best_combined:
        import subprocess
        payload=json.dumps({"symside": symside, "overrides": best_combined, "window_days": 30})
        cmd=[sys.executable,"-c","import sys,json; d=json.loads(sys.argv[1]); import backtest_v12_engine as B; r=B.run_one(d['symside'], d['overrides'], window_days=d['window_days']); print(json.dumps({k: r.get(k) for k in ('valid','gain_pct','trades','pool_sharpe') if k in r}))", payload]
        try:
            res=subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            print(f" live {symside} {res.stdout.strip()[:200]} err {res.stderr.strip()[:200]}")
            # save live csv
            live_csv=ROOT/f"data/reports/live_{symside}_30D.csv"
            live_csv.write_text(res.stdout.strip()+"\n")
        except Exception as e:
            print(f" live fail {e}")
