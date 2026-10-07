#!/usr/bin/env python3
"""bb_wt_template_sweep — all BB/WT per new TEMPLATE per cat/side, ONLY CROSSES, from scratch"""
import argparse, json, sys, pathlib
from concurrent.futures import ThreadPoolExecutor, as_completed
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import openpyxl
def load_template_switches(cat_side):
    p=ROOT/f"SPREADSHEETS/TEMPLATE_{cat_side}.xlsx"
    wb=openpyxl.load_workbook(p, read_only=True, data_only=True)
    uniq={}
    for ws in wb:
        if ws.title in ["LEGEND_FILTERS","INSTRUCTIONS","FILTERS_EXPLAINED","INSTRUCTIONS_V2"]:
            continue
        for row in ws.iter_rows(min_row=3, max_col=3, values_only=True):
            if not row[0]: continue
            name=str(row[0]).strip()
            if "BB" not in name.upper() and "WT" not in name.upper():
                continue
            default=row[1]
            # collect unique names, keep first default
            if name not in uniq:
                uniq[name]=default
    return uniq

def is_crypto_sym(sym):
    return sym.upper().endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD"))

def run_one(sym_side, window_days=30):
    sym=sym_side[:-5] if sym_side.endswith("_LONG") else sym_side[:-6]
    is_long=sym_side.endswith("_LONG")
    is_crypto=is_crypto_sym(sym)
    cat_side=f"{'CRYPTO' if is_crypto else 'STOCKS'}_{'LONG' if is_long else 'SHORT'}"
    tmpl=load_template_switches(cat_side)
    # base overrides = all BB/WT OFF + KG 4h ON + only crosses
    import v12_quick_engine as V
    from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized
    import dataclasses
    base={}
    for f in dataclasses.fields(V.QuickConfig):
        if f.name.endswith("_ENABLED"):
            base[f.name]=False
    # enable only needed
    base.update({
        "KINDERGARTEN_EMA_GATE_ENABLED": True,
        "KINDERGARTEN_FILTER_TF": "4h",
        "KINDERGARTEN_CUMULATIVE_MODE": True,
        "KINDERGARTEN_CUMULATIVE_MIN_TFS": 1,
        "EMA_9_21_FILTER_ENABLED": False,
        "COOLDOWN_BARS": 0,
        "DC_DAYTRADE_ENABLED": True,
        "TRADIER_DC_DAYTRADE_ENABLED": True,
        "TRADIER_DC_DAYTRADE_STOP_PCT": 100.0,
        "TRADIER_DC_DAYTRADE_TARGET_PCT": 100.0,
        "DC_DAYTRADE_STOP_PCT": 100.0,
        "DC_DAYTRADE_TARGET_PCT": 100.0,
    })
    # set all BB/WT from template to OFF/False
    for name, default in tmpl.items():
        if isinstance(default, bool):
            base[name]=False
        elif isinstance(default, str) and default in ["OFF","15m","1h","4h","D","W"]:
            base[name]="OFF"
        else:
            # threshold numeric -> set to default but will be swept
            base[name]=default
    # now evaluate base
    prep=prepare_batch(sym_side, window_days)
    import dataclasses as dc
    def eval_with(overrides):
        cfg_over=dict(base)
        cfg_over.update(overrides)
        # map to QuickConfig fields that exist
        return evaluate_prepared_sanitized(prep, cfg_over, window_days)
    base_r=eval_with({})
    if base_r is None:
        return {"sym_side": sym_side, "error": "base None"}
    base_gain=base_r.get("gain_pct_2000norm",0)
    variants=[]
    for name, default in tmpl.items():
        # determine test values per name
        candidates=[]
        if isinstance(default, bool):
            candidates=[False, True]
        elif isinstance(default, str):
            # TF gate
            vals=["OFF","15m","1h","4h"]
            if "D" in str(default) or "W" in str(default):
                vals+=["D","W"]
            candidates=vals
        elif isinstance(default, (int,float)):
            # numeric threshold: test default *0.5, *1, *1.5 + default
            try:
                v=float(default)
                candidates=[v*0.5, v, v*1.5] if v!=0 else [0,0.5,1]
            except:
                candidates=[default]
        else:
            candidates=[default]
        for cand in candidates:
            ov={name: cand}
            r=eval_with(ov)
            if r is not None:
                g=r.get("gain_pct_2000norm",0)
                variants.append({"switch": name, "value": cand, "gain": round(g,4), "delta": round(g-base_gain,4), "trades": r.get("trades",0)})
    best=max(variants, key=lambda x: x["delta"]) if variants else None
    return {"sym_side": sym_side, "cat_side": cat_side, "base_gain": round(base_gain,4), "base_trades": base_r.get("trades",0), "variants": variants, "best": best, "n_switches": len(tmpl)}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--sym", default="")
    p.add_argument("--all", action="store_true")
    p.add_argument("--venue", default="both", choices=["crypto","stocks","both"])
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, default=0)
    args=p.parse_args()
    import json, pathlib
    # load per_sym list
    import json as js
    per_sym_path=ROOT/"data"/"hourly_reconfig"/"per_sym_active_config.json"
    stocks_path=ROOT/"data"/"hourly_reconfig"/"per_sym_active_config_stocks.json"
    d1=json.loads(per_sym_path.read_text()) if per_sym_path.exists() else {}
    d2=json.loads(stocks_path.read_text()) if stocks_path.exists() else {}
    merged={**d1, **d2}
    targets=[]
    for k in merged.keys():
        if k.startswith("_"): continue
        sym=k[:-5] if k.endswith("_LONG") else k[:-6]
        is_crypto=is_crypto_sym(sym)
        if args.venue=="crypto" and not is_crypto: continue
        if args.venue=="stocks" and is_crypto: continue
        targets.append(k)
    targets=sorted(set(targets))
    if args.sym:
        targets=[args.sym]
    if args.limit:
        targets=targets[:args.limit]
    print(f"[targets] {len(targets)}")
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time
    t0=time.time()
    results=[]
    if args.workers>1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            fut={ex.submit(run_one, ss): ss for ss in targets}
            for f in as_completed(fut):
                ss=fut[f]
                try:
                    r=f.result()
                    results.append(r)
                    print(f"[{ss}] base {r.get('base_gain')} best {r.get('best',{}).get('switch')} {r.get('best',{}).get('value')} delta {r.get('best',{}).get('delta')}")
                except Exception as e:
                    import traceback; traceback.print_exc()
                    results.append({"sym_side": ss, "error": str(e)})
    else:
        for ss in targets:
            r=run_one(ss)
            results.append(r)
            print(r)
    out=pathlib.Path(f"data/reports/bb_wt_template_{args.venue}.json")
    out.write_text(json.dumps(results, indent=2))
    print(f"wrote {out} {len(results)}")
