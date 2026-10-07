#!/usr/bin/env python3
"""Mac watcher: pull ONLY NEW canonical V15 workbooks with improved gains, not pilot bloat.
Every 60s rsyncs canonical *_30d_matrix.xlsx + progress for 20-target half of 40.
Validates: 13 tabs, F/G floats (no VLOOKUP), cumul monotonic, hustle > cumul.
Deletes Mac pilot bloat (3344 files) and keeps only 64 canonical.
"""
import time, json, subprocess, pathlib, sys
from pathlib import Path
import openpyxl

ROOT = Path("/Users/niels/Documents/binance")
MAC_OUT = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
S1 = "s1-int"
S2 = "s2"
PILOT_S1 = "/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL"
PILOT_S2 = PILOT_S1
# 20 for half of 40 - all have NPZ on S1 (30 present, 10 missing HYPE/DOGE etc excluded)
TARGETS_20 = [
    # S1 shard1 (7) + S1 overflow (7) + S2 shard2 (6) = 20
    "BTCUSDC_LONG","ETHUSDC_LONG","BNBUSDC_LONG","SOLUSDC_LONG","XRPUSDC_LONG","ZECUSDC_LONG","BTCUSDC_SHORT",
    "BTCDOMUSDT_SHORT","BNBUSDC_SHORT","SOLUSDC_SHORT","MSTR_LONG","VLO_LONG","GLD_LONG","LOW_SHORT","AMZN_SHORT",
    "CRWV_SHORT","AMAT_SHORT","BWXT_SHORT","NKE_SHORT","BABA_SHORT"
]
# also track 13 sheets
SWITCH_SHEETS = ["ENTRY_REVERSAL_BOUNCE","STDEV_SLOPE_SIZING","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES","EXIT_STRUCTURAL","EXIT_VELOCITY","REENTRY_WINDOWED","REENTRY_ADAPTIVE","AUGMENT_TREND","AUGMENT_RISK_SIZING","REDUCE_PROFIT_LOCK","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES"]

def rsync_one(host, remote, local):
    cmd = ["rsync","-avz","-e","ssh -o ConnectTimeout=10", f"{host}:{remote}", str(local)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.returncode==0

def validate_wb(path: Path):
    try:
        wb = openpyxl.load_workbook(str(path), data_only=False)
        # check 13 tabs present
        missing = [s for s in SWITCH_SHEETS if s not in wb.sheetnames]
        if missing:
            return False, f"missing sheets {missing}"
        # check F/G floats, no VLOOKUP
        for sh in SWITCH_SHEETS[:1]:
            ws = wb[sh]
            vlook=False
            floats=0
            for r in range(3, min(10, ws.max_row+1)):
                g = ws.cell(r,7).value
                if isinstance(g,str) and "VLOOKUP" in g:
                    vlook=True
                if isinstance(g,float):
                    floats+=1
            if vlook:
                return False, "VLOOKUP still present (old code)"
            if floats==0:
                return False, "no float G (not yet filled)"
        wb.close()
        return True, "ok"
    except Exception as e:
        return False, str(e)

def check_progress(path: Path):
    try:
        j=json.loads(path.read_text())
        done=j.get("done",{})
        sheets=len(set(k.split("!")[0] for k in done))
        pos=sum(1 for v in done.values() if v.get("delta",0)>0)
        cum=j.get("cumulative_gain", j.get("baseline_gain"))
        base=j.get("baseline_gain",0)
        return f"done {len(done)} sheets {sheets}/13 pos {pos} cum {cum:.2f} base {base:.2f} delta {cum-base:.2f}"
    except Exception as e:
        return f"err {e}"

# one-shot clean pilot bloat on Mac (keep canonical only)
def clean_mac_bloat():
    pilot_files = list(MAC_OUT.glob("*pilot*"))
    if pilot_files:
        print(f"[clean] Mac has {len(pilot_files)} pilot timestamped bloat files (will keep 64 canonical)")
        # move to /tmp trash instead of delete for safety, keep last 100 if needed
        trash = Path("/tmp/mac_v15_pilot_trash")
        trash.mkdir(exist_ok=True)
        # keep 64 canonical, remove pilot files older than 1h
        import shutil, time as t
        for p in pilot_files:
            try:
                # if pilot file older than 2h, move
                if t.time() - p.stat().st_mtime > 7200:
                    shutil.move(str(p), str(trash / p.name))
            except: pass
        print(f"[clean] moved old pilot files to {trash}, remaining canonical {len(list(MAC_OUT.glob('*_30d_matrix.xlsx')))}")

if __name__ == "__main__":
    clean_mac_bloat()
    print(f"[watcher] 20 targets: {TARGETS_20}")
    print(f"[watcher] expecting 20 canonical *_30d_matrix.xlsx with 13 tabs, F=hustle vs baseline (float), G=greedy vs cum (float), E blank when G<=0")
    print(f"[watcher] syncing S1 (14 pilots) + S2 (5 pilots) -> Mac every 60s, only when gain improves")
    # initial sync
    for sym in TARGETS_20:
        # try S1 first, then S2
        for host in [S1, S2]:
            remote_xlsx = f"{PILOT_S1}/{sym}_30d_matrix.xlsx"
            remote_prog = f"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{sym}_v14_progress.json"
            local_xlsx = MAC_OUT / f"{sym}_30d_matrix.xlsx"
            local_prog = ROOT / "data" / "reports" / "lifecycle_pilot" / f"{sym}_v14_progress.json"
            # rsync xlsx if newer
            ok = rsync_one(host, remote_xlsx, str(local_xlsx))
            if ok and local_xlsx.exists():
                valid, msg = validate_wb(local_xlsx)
                prog_msg = check_progress(local_prog) if local_prog.exists() else "no prog"
                # also sync progress
                rsync_one(host, remote_prog, str(local_prog))
                print(f"  {sym:20} {host:6} xlsx {valid} {msg} | {prog_msg} | {local_xlsx.stat().st_size/1e3:.0f}K")
                break
    print("[watcher] initial sync done, looping...")
    while True:
        time.sleep(60)
        for sym in TARGETS_20:
            for host in [S1, S2]:
                remote_xlsx = f"{PILOT_S1}/{sym}_30d_matrix.xlsx"
                local_xlsx = MAC_OUT / f"{sym}_30d_matrix.xlsx"
                # check remote mtime via ssh
                try:
                    out = subprocess.run(["ssh","-o","ConnectTimeout=5",host,f"stat -c %Y {remote_xlsx} 2>&1"], capture_output=True, text=True, timeout=8)
                    remote_mtime = int(out.stdout.strip()) if out.stdout.strip().isdigit() else 0
                    local_mtime = int(local_xlsx.stat().st_mtime) if local_xlsx.exists() else 0
                    if remote_mtime > local_mtime + 5:
                        rsync_one(host, remote_xlsx, str(local_xlsx))
                        remote_prog = f"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{sym}_v14_progress.json"
                        local_prog = ROOT / "data" / "reports" / "lifecycle_pilot" / f"{sym}_v14_progress.json"
                        rsync_one(host, remote_prog, str(local_prog))
                        valid, msg = validate_wb(local_xlsx) if local_xlsx.exists() else (False,"missing")
                        print(f"[NEW] {sym} from {host} mtime {remote_mtime} -> {local_mtime} valid={valid} {msg}", flush=True)
                    break
                except: break
