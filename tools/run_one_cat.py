#!/usr/bin/env python3
import json, glob, subprocess
from pathlib import Path
import argparse
ap=argparse.ArgumentParser()
ap.add_argument("--cat", required=True)
ap.add_argument("--template", required=True)
args=ap.parse_args()
cat=args.cat
tmpl=args.template
print(f"ONE CAT {cat} via {tmpl} yellows kept grey, new orange/blank only, no retest", flush=True)
try:
    order=json.loads(Path.home().joinpath("binance-sandbox/data/campaign_order_1mo.json").read_text())
except:
    order=[]
if not order:
    files=glob.glob(str(Path.home()/ "binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*_bh*.xlsx"))
    order=[]
    for f in files:
        n=Path(f).name
        sym=n.split("_bh")[0]
        is_stock="USDC" not in sym and "USDT" not in sym
        side="LONG" if "_LONG" in n else "SHORT"
        c="STOCKS" if is_stock else "CRYPTO"
        c=c+"_"+side
        if c==cat:
            order.append(sym)
order=order[:75]
print(f"CAT {cat} order {len(order)} {order[:3]}", flush=True)
for sym in order:
    print(f"START {sym}", flush=True)
    try:
        subprocess.run(["/home/niels/binance-sandbox/.venv/bin/python","-u","v15_pilot.py","--sym-side",sym,"--window-days","30","--template","SPREADSHEETS/"+tmpl], cwd=str(Path.home()/"binance-sandbox"), timeout=3600)
    except Exception as e:
        print(f"TIMEOUT/ERR {sym} {e}", flush=True)
        continue
