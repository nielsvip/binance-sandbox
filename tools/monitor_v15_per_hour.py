#!/usr/bin/env python3
"""v15 per-hour health check: logs progress per server"""
import subprocess, pathlib, datetime
hosts = [("s1-pub","s1"),("s2","s2"),("s3","s3"),("s5","s5")]
for h,label in hosts:
    try:
        r = subprocess.run(["ssh","-o","ConnectTimeout=5",h,"ls /home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>&1 | wc -l; ps aux | grep v15_local_herd | grep -v grep | wc -l"], capture_output=True, text=True, timeout=10)
        print(f"{datetime.datetime.utcnow().isoformat()} {label} {r.stdout.strip()[:80]} err:{r.stderr.strip()[:40]}")
    except Exception as e:
        print(f"{datetime.datetime.utcnow().isoformat()} {label} ERR {e}")
