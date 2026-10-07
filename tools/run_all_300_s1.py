#!/usr/bin/env python3
import subprocess, sys
from pathlib import Path
cats = [("STOCKS_LONG","TEMPLATE_STOCKS_LONG.xlsx"),("CRYPTO_LONG","TEMPLATE_CRYPTO_LONG.xlsx"),("STOCKS_SHORT","TEMPLATE_STOCKS_SHORT.xlsx"),("CRYPTO_SHORT","TEMPLATE_CRYPTO_SHORT.xlsx")]
for cat, tmpl in cats:
    print(f"=== CAT {cat} via {tmpl} ===", flush=True)
    try:
        subprocess.run(["/home/niels/binance-sandbox/.venv/bin/python","-u","tools/run_one_cat.py","--cat",cat,"--template",tmpl], cwd=str(Path.home()/"binance-sandbox"), timeout=3600)
    except Exception as e:
        print(f"ERR {cat} {e}", flush=True)
        continue
print("=== ALL 300 DONE on s1 ===", flush=True)
