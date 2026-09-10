#!/usr/bin/env python3
"""
Watchdog: ensures every tradeable symbol has >=1200 bars (prefer 1800) on Gateway (and S1 gateway mirror).
Runs on Mac, polls Gateway via SSH, restarts ez_klines if dead, waits for ban, reports progress.
Logs to ~/logs/ensure_klines_depth.log
"""
import json, time, subprocess, os
from pathlib import Path
from datetime import datetime, timezone

BASE = Path("/Users/niels/Documents/binance")
LOG = Path.home() / "logs" / "ensure_klines_depth.log"
LOG.parent.mkdir(parents=True, exist_ok=True)

def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def ssh_run(host, cmd, timeout=15):
    try:
        r = subprocess.run(["ssh","-o","ConnectTimeout=8",host,cmd], capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.stderr, r.returncode
    except Exception as e:
        return "", str(e), 99

def check_gateway_depth():
    # Use single SSH to count <1200 per TF
    cmd = r"""python3 - << 'PY'
import json, glob
from pathlib import Path
BASE=Path("/home/niels/binance")
import json as js
try:
    syms=js.load(open(BASE/"symbols.json"))
except: syms=[]
try:
    trad_syms=js.load(open(BASE/"symbols_tradier.json"))
except: trad_syms=[]
def cnt(syms, sub, tfs):
    out={}
    for tf in tfs:
        ok=0
        for s in syms:
            p=BASE/f"klines_cache/{sub}{s}_{tf}.json" if sub else BASE/f"klines_cache/{s}_{tf}.json"
            try:
                d=js.load(open(p))
                if len(d)>=1200: ok+=1
            except: pass
        out[tf]=(ok,len(syms))
    return out

import json as js
bin_syms=js.load(open("/home/niels/binance/symbols.json"))
# tradeable subset for reporting: use symbols.json itself (327) but also check tradable union via local? For simplicity check all active
bin_tfs=["1m","3m","15m","1h","4h","D"]
trad_tfs=["1m","5m","15m","1h","4h","D"]
# bin
for tf in bin_tfs:
    cnt_ok=sum(1 for s in bin_syms if len(js.load(open(f"/home/niels/binance/klines_cache/{s}_{tf}.json")) if Path(f"/home/niels/binance/klines_cache/{s}_{tf}.json").exists() else [])>=1200) if False else 0
PY
"""
    # Simpler: just run Python on gateway to count
    py = r"""
import json, glob
from pathlib import Path
BASE=Path("/home/niels/binance")
def load(p):
    try: return json.load(open(p))
    except: return []
bin_syms=json.load(open(BASE/"symbols.json"))
trad_syms=json.load(open(BASE/"symbols_tradier.json"))
for tf in ["1m","3m","15m","1h","4h","D"]:
    ok=sum(1 for s in bin_syms if len(load(BASE/f"klines_cache/{s}_{tf}.json"))>=1200)
    print(f"GW bin {tf}: {ok}/{len(bin_syms)} >=1200")
for tf in ["1m","5m","15m","1h","4h","D"]:
    ok=sum(1 for s in trad_syms if len(load(BASE/f"klines_cache/tradier/{s}_{tf}.json"))>=1200)
    print(f"GW tradier {tf}: {ok}/{len(trad_syms)} >=1200")
# also 1800
for tf in ["15m","1h","4h","D"]:
    ok=sum(1 for s in bin_syms if len(load(BASE/f"klines_cache/{s}_{tf}.json"))>=1800)
    print(f"GW bin {tf} 1800: {ok}/{len(bin_syms)}")
for tf in ["15m"]:
    ok=sum(1 for s in trad_syms if len(load(BASE/f"klines_cache/tradier/{s}_{tf}.json"))>=1800)
    print(f"GW tradier {tf} 1800: {ok}/{len(trad_syms)}")
"""
    out, err, rc = ssh_run("gateway-internal", f"python3 - << 'PY'\n{py}\nPY")
    return out

def check_ban(host):
    out, _, _ = ssh_run(host, "cat /home/niels/binance/data/ez_klines_ban_state.json 2>&1 | head -c 500")
    if "ban_until_ms" in out:
        try:
            import json as js
            d=js.loads(out)
            ban_ms=d.get("ban_until_ms",0)
            rem=(ban_ms/1000)-time.time()
            return max(0, rem)
        except: return 0
    return 0

def ensure_ez_running(host):
    out, _, _ = ssh_run(host, "ps aux | grep '[e]z_klines.py' | head -n 5")
    if "ez_klines.py" not in out:
        log(f"{host} ez_klines NOT running — restarting")
        # live path is /home/niels/binance/ez_klines.py on gateway, sandbox on S1
        bin_path = "/home/niels/binance/ez_klines.py" if host=="gateway-internal" else "/home/niels/binance-sandbox/ez_klines.py"
        # check exists
        out2, _, _ = ssh_run(host, f"ls {bin_path} 2>&1 | head -n 2")
        if "No such file" in out2:
            bin_path="/home/niels/binance-sandbox/ez_klines.py"
        ssh_run(host, f"nohup /home/niels/.conda/envs/binance_env/bin/python -u {bin_path} > /home/niels/logs/ez_klines.log 2>&1 & echo started", timeout=10)
        time.sleep(3)
    else:
        log(f"{host} ez_klines running: {out.strip().splitlines()[0][:120]}")

def main():
    log("=== ensure_klines_depth watchdog started ===")
    log(f"Mac symbols {len(json.load(open(BASE/'symbols.json')))} tradier {len(json.load(open(BASE/'symbols_tradier.json')))}")
    # Ensure gateway and S1 have correct symbols (already pushed, but check)
    for host in ["gateway-internal","s1-int"]:
        out, _, _ = ssh_run(host, "python3 -c 'import json; print(len(json.load(open(\"/home/niels/binance/symbols.json\"))))' 2>&1 | head -n 2")
        log(f"{host} symbols.json len: {out.strip()}")
    iteration=0
    while True:
        iteration+=1
        log(f"--- iteration {iteration} ---")
        # check bans
        for host in ["gateway-internal"]:
            rem=check_ban(host)
            if rem>0:
                log(f"{host} BAN {rem:.0f}s remaining — throttling, will retry after")
            else:
                log(f"{host} not banned")
        # ensure processes
        for host in ["gateway-internal"]:
            ensure_ez_running(host)
        # S1 tradier
        out, _, _ = ssh_run("s1-int", "ps aux | grep '[t]radier_klines' | head -n 5")
        if "tradier_klines" not in out:
            log("S1 tradier_klines not running — starting sandbox")
            ssh_run("s1-int", "nohup /usr/bin/python3 -u /home/niels/binance-sandbox/tradier_klines.py > /home/niels/logs/tradier_klines.log 2>&1 &", timeout=10)
        # depth report
        out = check_gateway_depth()
        for line in out.strip().splitlines():
            log(line)
        # check if all >=1200
        # parse
        all_ok=True
        for line in out.splitlines():
            if "GW bin" in line or "GW tradier" in line:
                if ">=1200" in line:
                    try:
                        ok, total = line.split(":")[1].strip().split(" ")[0].split("/")
                        if int(ok) < int(total):
                            all_ok=False
                    except: pass
        if all_ok:
            log("ALL >=1200 achieved ✓ — will keep monitoring for 1800")
            # also show 1800
            for line in out.splitlines():
                if "1800" in line:
                    log(line)
        else:
            log("Not yet 1200 — will check again in 5 min (gateway fetch continues, ban will lift)")
        # also check gateway AAOI file creation
        out, _, _ = ssh_run("gateway-internal", "ls /home/niels/binance/klines_cache/AAOIUSDT_*.json 2>&1 | head -n 5; ls /home/niels/binance/klines_cache/AAOIUSDT_15m.json 2>&1 | xargs -I {} sh -c 'python3 -c \"import json; print(len(json.load(open(\"{}\"))))\"' 2>&1 | head -n 2")
        log(f"AAOI check: {out.strip()[:200]}")
        time.sleep(300)  # 5 min

if __name__=="__main__":
    main()
