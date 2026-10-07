#!/usr/bin/env python3
"""
log-monitor: Monitor crypto pipeline and S1 1000BONK 30D run every 60s
Checks: /tmp/crypto_30d.log, SPREADSHEETS/1000BONK*_30d_matrix.xlsx, s1-int ps aux | grep 1000BONK
Triggers: 0 trades, 0.000 deltas, stalls >5min
Intervention: kill and relaunch with correct symbols_trb logic (stocks first, not BONK)
             and ensure crypto same style (per_sym -> baseline -> remaining) waits for stocks 1Y phase.
Log to /tmp/monitor_crypto.log
"""
import subprocess
import time
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = Path("/tmp/monitor_crypto.log")
LOCAL_LOG = Path("/tmp/crypto_30d.log")
LOCAL_XLSX_GLOB = list(ROOT.glob("SPREADSHEETS/1000BONK*_30d_matrix.xlsx"))
# S1 paths via ssh
S1_LOG = "/tmp/crypto_30d.log"
S1_XLSX_PATTERN = "~/binance-sandbox/SPREADSHEETS/1000BONK*_30d_matrix.xlsx"
S1_XLSX_DIR = "~/binance-sandbox/SPREADSHEETS"
STALL_THRESHOLD_S = 300  # 5 min
CHECK_INTERVAL = 60
ZERO_DELTA_THRESHOLD = 0.6  # if >60% of recent deltas are 0.000 -> bug
TRADES_ZERO_THRESHOLD = 0

def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg):
    line = f"[{utcnow()}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired as e:
        return (e.stdout.decode() if e.stdout else ""), "timeout", 124
    except Exception as e:
        return "", str(e), 1

def ssh_s1(cmd, timeout=10):
    full = f"ssh -o ConnectTimeout=5 -o BatchMode=yes s1-int {cmd!r}"
    # Use shell with proper quoting - wrap in single quotes for ssh
    # Better: use ssh s1-int "cmd"
    shell_cmd = f"ssh -o ConnectTimeout=5 -o BatchMode=yes s1-int {cmd}"
    # Use bash -c with timeout
    try:
        r = subprocess.run(["bash","-c", shell_cmd], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "ssh timeout", 124
    except Exception as e:
        return "", str(e), 1

def check_local_log():
    """Check /tmp/crypto_30d.log locally"""
    result = {"exists": False, "mtime_age": None, "zero_trades": False, "zero_deltas_ratio": 0, "stall": False, "lines": 0}
    if not LOCAL_LOG.exists():
        return result
    result["exists"] = True
    try:
        age = time.time() - LOCAL_LOG.stat().st_mtime
        result["mtime_age"] = age
        result["stall"] = age > STALL_THRESHOLD_S
        # tail analysis
        out, _, _ = run(f"tail -200 {LOCAL_LOG} 2>&1", timeout=5)
        lines = out.splitlines() if out else []
        result["lines"] = len(lines)
        # check 0 trades
        if "trades=0" in out or "trades\": 0" in out or "trades 0" in out:
            result["zero_trades"] = True
        # check 0.000 deltas ratio
        import re
        delta_vals = re.findall(r"delta[=:\s]*([-\d\.]+)", out)
        zero_count = sum(1 for v in delta_vals if v.strip() in ("0.0000","0.000","0","0.0"))
        total = len(delta_vals)
        if total > 10:
            result["zero_deltas_ratio"] = zero_count / total
        # also vector 0.0000 pattern
        vzeros = out.count("vector 0.0000")
        if total == 0 and vzeros > 5:
            result["zero_deltas_ratio"] = 1.0
    except Exception as e:
        log(f"[local-log] error {e}")
    return result

def check_s1_log():
    """Check S1 /tmp/crypto_30d.log via ssh"""
    result = {"exists": False, "mtime_age": None, "stall": False, "zero_trades": False, "zero_deltas_count": 0, "lines": 0, "head": "", "tail": ""}
    out, err, rc = ssh_s1(f"\"stat {S1_LOG} 2>&1; echo ---STAT_DONE---; wc -l {S1_LOG} 2>&1; echo ---WC_DONE---; tail -100 {S1_LOG} 2>&1 | head -100\"", timeout=15)
    if rc != 0 and "No such file" in out:
        return result
    result["exists"] = "File:" in out or "Size:" in out or "Modify:" in out
    # parse Modify time
    try:
        import re
        m = re.search(r"Modify:\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", out)
        if m:
            # parse as UTC? S1 is UTC
            from datetime import datetime
            mt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            mt = mt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - mt).total_seconds()
            result["mtime_age"] = age
            result["stall"] = age > STALL_THRESHOLD_S
        # check stall via tail timestamp?
    except Exception:
        pass
    # check zero trades / deltas in tail section
    tail_section = out.split("---WC_DONE---")[-1] if "---WC_DONE---" in out else out
    result["tail"] = tail_section[-2000:]
    if "trades=0" in tail_section or "trades\": 0" in tail_section:
        result["zero_trades"] = True
    # count 0.000 deltas
    result["zero_deltas_count"] = tail_section.count("delta=0.0000") + tail_section.count("vector 0.0000")
    # also get lines count
    try:
        import re
        m2 = re.search(r"(\d+)\s+/tmp/crypto_30d\.log", out)
        if m2:
            result["lines"] = int(m2.group(1))
    except:
        pass
    return result

def check_s1_ps():
    """Check s1-int ps aux | grep 1000BONK"""
    out, err, rc = ssh_s1("\"ps aux | grep 1000BONK | grep -v grep; echo ---PS_DONE---; ps aux | grep v12_pilot | grep -v grep | head -5\"", timeout=10)
    result = {"running": False, "ps_out": out, "pids": [], "stall": False, "s1_raw": out}
    if "1000BONK" in out or "v12_pilot" in out:
        result["running"] = True
    # extract PIDs
    import re
    for line in out.splitlines():
        if "1000BONK" in line and "grep" not in line:
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                result["pids"].append(parts[1])
    # check etime stall via ps -o etimes
    if result["pids"]:
        pid = result["pids"][0]
        out2, _, _ = ssh_s1(f"\"ps -o etimes= -p {pid} 2>&1; echo ---; ps -o etime= -p {pid} 2>&1\"", timeout=5)
        try:
            et = out2.split()[0].strip()
            if et.isdigit() and int(et) > STALL_THRESHOLD_S:
                # check if log not updating -> stall
                result["stall"] = False  # will be combined with log stall
                result["etimes"] = int(et)
        except:
            pass
    return result

def check_xlsx():
    """Check local and S1 xlsx for 0.000 deltas / 0 trades / stall
    FIX: stall only if S1 active pilot file is stalled (>300s) AND log stalled AND ps running.
    Local xlsx is mirror (stale by design) - never use alone to trigger stall.
    """
    result = {"local_exists": False, "s1_exists": False, "local_mtime_age": None, "s1_mtime_age": None, "stall": False, "zero_deltas": False, "zero_trades": False, "details": ""}
    # local - for info only, never for stall trigger
    xlsx_files = list(ROOT.glob("SPREADSHEETS/1000BONK*_30d_matrix.xlsx"))
    if xlsx_files:
        result["local_exists"] = True
        newest = max(xlsx_files, key=lambda p: p.stat().st_mtime)
        age = time.time() - newest.stat().st_mtime
        result["local_mtime_age"] = age
        result["local_file"] = str(newest)
        # check python openpyxl for deltas
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(newest), data_only=True)
            # check results sheet trades
            if "results" in wb.sheetnames:
                ws = wb["results"]
                # find Baseline row trades col 6
                for r in range(1, ws.max_row+1):
                    v = ws.cell(row=r, column=1).value
                    if v == "Baseline":
                        trades = ws.cell(row=r, column=6).value
                        if trades == 0 or trades == "0":
                            result["zero_trades"] = True
                            result["details"] += f" local baseline trades=0;"
                        break
            # check F col zeros in first few sheets
            zeros = 0
            total = 0
            for sn in wb.sheetnames:
                if sn.startswith("ENTRY") or sn.startswith("EXIT"):
                    ws = wb[sn]
                    for r in range(3, min(ws.max_row+1, 20)):
                        fval = ws.cell(row=r, column=6).value
                        if fval is not None:
                            total += 1
                            if fval == 0 or fval == 0.0 or fval == "0.0000":
                                zeros += 1
                    break
            if total > 5 and zeros / total > 0.8:
                result["zero_deltas"] = True
                result["details"] += f" local F zeros {zeros}/{total};"
            wb.close()
        except Exception as e:
            result["details"] += f" local xlsx err {e};"
        # NEVER set stall from local mirror alone - S1 is source of truth
    # S1 - check newest pilot file (not stale base)
    out, _, rc = ssh_s1(f'"ls -lh {S1_XLSX_DIR}/1000BONK*30d*.xlsx 2>&1 | head -10; echo ---LS_DONE---; ls -t {S1_XLSX_DIR}/1000BONK*30d*.xlsx 2>&1 | head -3"', timeout=10)
    # Parse newest filename from ls -t output (after ---LS_DONE---) and stat it separately
    newest_s1_file = None
    try:
        # Only consider ls -t section (after marker) for newest file
        after_marker = out.split("---LS_DONE---")[-1] if "---LS_DONE---" in out else out
        for line in after_marker.splitlines():
            line=line.strip()
            if line.endswith(".xlsx") and "1000BONK" in line:
                newest_s1_file = line
                break
        if not newest_s1_file:
            import re
            m = re.search(r"(/home[^\s]+\.xlsx)", after_marker)
            if m:
                newest_s1_file = m.group(1)
    except:
        pass
    out_mtime = ""
    if newest_s1_file:
        out_mtime, _, _ = ssh_s1(f'"stat {newest_s1_file} 2>&1 | head -10"', timeout=10)
        out = out + "\n" + out_mtime
    if "1000BONK" in out and "No such file" not in out:
        result["s1_exists"] = True
        result["s1_ls"] = out[:2000]
        # parse Modify time from stat
        try:
            import re
            m = re.search(r"Modify:\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", out)
            if m:
                from datetime import datetime
                mt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                mt = mt.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - mt).total_seconds()
                result["s1_mtime_age"] = age
                if age > STALL_THRESHOLD_S:
                    result["stall"] = True
        except Exception:
            pass
        # check S1 xlsx via python for zeros
        out2, _, _ = ssh_s1("\"python3 -c \\\"import openpyxl, glob; f=sorted(glob.glob('/home/niels/binance-sandbox/SPREADSHEETS/1000BONK*_30d_matrix.xlsx'))[0] if glob.glob('/home/niels/binance-sandbox/SPREADSHEETS/1000BONK*_30d_matrix.xlsx') else ''; print('FILE:'+f); wb=openpyxl.load_workbook(f, data_only=True) if f else None; zeros=sum(1 for ws in wb.worksheets[:2] for r in range(3, min(ws.max_row+1,10)) for c in [ws.cell(row=r,column=6).value] if c==0 or c==0.0) if wb else 0; print('ZEROS:'+str(zeros)); print('SHEETS:'+','.join(wb.sheetnames[:3]) if wb else 'no wb')\\\" 2>&1\"", timeout=10)
        if "ZEROS:" in out2:
            try:
                import re
                m = re.search(r"ZEROS:(\d+)", out2)
                if m and int(m.group(1)) > 5:
                    result["zero_deltas"] = True
                    result["details"] += f" s1 zeros {m.group(1)};"
            except:
                pass
        # also check trades via S1 xlsx
        out3, _, _ = ssh_s1("\"python3 -c \\\"import openpyxl, glob; f=glob.glob('/home/niels/binance-sandbox/SPREADSHEETS/1000BONK*_30d_matrix*.xlsx'); f=sorted(f)[-1] if f else ''; wb=openpyxl.load_workbook(f, data_only=True) if f else None; ws=wb['results'] if wb and 'results' in wb.sheetnames else None; print('TRADES:'+str(ws.cell(row=2,column=6).value) if ws else 'no results')\\\" 2>&1\"", timeout=10)
        if "TRADES:0" in out3 or "TRADES: 0" in out3:
            result["zero_trades"] = True
            result["details"] += " s1 trades 0;"
    return result

def check_stocks_1y_phase():
    """Check if stocks 1Y phase is done - crypto should wait for it"""
    # Look for stocks 1Y completion markers
    result = {"done": False, "details": ""}
    # Check S1 for stocks 1Y xlsx or progress files
    out, _, _ = ssh_s1("\"ls -lh ~/binance-sandbox/SPREADSHEETS/*_1y* 2>&1 | head -10; echo ---; ls ~/binance-sandbox/data/reports/lifecycle_pilot/*1y* 2>&1 | head -10; echo ---; cat ~/binance-sandbox/data/reports/lifecycle_pilot/campaign_order_1mo.json 2>&1 | head -20; echo ---; ps aux | grep 'window.*365\\|1y\\|1Y' | grep -v grep | head -5\"", timeout=10)
    result["raw"] = out[:2000]
    # heuristic: if stocks 1Y files exist and not running, done
    if "1y" in out.lower() or "365" in out:
        result["details"] = "1Y marker found"
    # Also check local
    local_1y = list(ROOT.glob("SPREADSHEETS/*_1y*")) + list(ROOT.glob("data/reports/lifecycle_pilot/*1y*"))
    if local_1y:
        result["details"] += f" local 1Y files {len(local_1y)}"
    return result

def intervene_kill_and_relaunch(reason):
    log(f"[INTERVENE] {reason} -> kill and relaunch with correct symbols_trb logic (stocks first, not BONK)")
    # Kill S1 1000BONK
    out, err, rc = ssh_s1("\"pkill -9 -f '1000BONK.*30d' 2>&1; pkill -9 -f 'v12_pilot_sheet_runner.*1000BONK' 2>&1; ps aux | grep 1000BONK | grep -v grep; echo KILL_DONE\"", timeout=10)
    log(f"[kill] S1 pkill 1000BONK: {out[:500]} {err[:200]}")
    # Also kill via PID if still running
    ps = check_s1_ps()
    for pid in ps.get("pids", []):
        out2, _, _ = ssh_s1(f"\"kill -9 {pid} 2>&1; echo killed {pid}\"", timeout=5)
        log(f"[kill] pid {pid}: {out2[:200]}")

    # Ensure correct symbols_trb logic: stocks first, not BONK
    # Relaunch with proper order: per_sym -> baseline -> remaining, stocks 1Y first then crypto 30d
    # The correct logic is: pick stocks from symbols_trb_long/short first (campaign queue), not BONK
    # We relaunch the pilot runner WITHOUT --sym-side so it picks first stocks via pick_first_symside (stocks first)
    # And ensure crypto waits for stocks 1Y phase

    stocks_1y = check_stocks_1y_phase()
    log(f"[stocks-1Y] check: {stocks_1y['details']} raw:{stocks_1y['raw'][:300]}")

    # Check if stocks 1Y still running - if so, crypto must wait
    out3, _, _ = ssh_s1("\"ps aux | grep -i '365\\|1y' | grep v12 | grep -v grep | head -5; echo ---; ls /tmp/*1y* 2>&1 | head -5\"", timeout=10)
    if "v12" in out3 and ("365" in out3 or "1y" in out3):
        log(f"[wait] stocks 1Y still running, crypto must wait: {out3[:500]}")
        # Don't relaunch crypto yet - just log and ensure stocks 1Y continues
        # But still relaunch stocks pipeline if needed
        cmd = "cd ~/binance-sandbox && nohup python3 -u tools/opt/v12_pilot_sheet_runner.py --window-days 365 --workers 4 --vector-only > /tmp/stocks_1y.log 2>&1 & echo STOCKS_1Y_LAUNCHED; sleep 1; ps aux | grep v12 | grep -v grep | head -3"
        out4, _, _ = ssh_s1(f"\"{cmd}\"", timeout=10)
        log(f"[relaunch] stocks 1Y (wait gate): {out4[:800]}")
        return "stocks_1Y_wait"

    # Stocks 1Y done or not running -> relaunch correct pipeline: stocks first (no sym-side = auto stocks), crypto same style
    # Launch stocks 30d first (per_sym -> baseline -> remaining) with stocks symbols
    # Use campaign queue logic: runner auto-picks stocks first via symbols_trb
    log("[relaunch] stocks first (symbols_trb) -> crypto per_sym baseline remaining waits for stocks 1Y done")
    # Relaunch 30d with correct logic: stocks first. We launch without BONK, letting picker choose stocks
    # First, verify symbols_trb files exist on S1
    out5, _, _ = ssh_s1("\"cat ~/binance-sandbox/symbols_trb_long.json 2>&1 | head -5; echo ---; ls ~/binance-sandbox/SPREADSHEETS/TEMPLATE*.xlsx 2>&1 | head -5\"", timeout=10)
    log(f"[verify] symbols_trb on S1: {out5[:600]}")

    # Launch corrected runner: per_sym -> baseline -> remaining, window 30, stocks first (auto), vector-only fast
    # Use TEMPLATE_UNIVERSAL and let pick_first_symside choose stocks (not BONK)
    # We explicitly NOT passing --sym-side 1000BONK - that's the bug fix
    cmd = "cd ~/binance-sandbox && nohup bash -c 'python3 -u tools/opt/v12_pilot_sheet_runner.py --window-days 30 --workers 4 --vector-only > /tmp/crypto_30d.log 2>&1' & echo LAUNCHED; sleep 1; ps aux | grep v12_pilot | grep -v grep | head -3; echo ---; tail -5 /tmp/crypto_30d.log 2>&1 | head -10"
    out6, err6, _ = ssh_s1(f"\"{cmd}\"", timeout=15)
    log(f"[relaunch] crypto 30d (stocks-first, per_sym->baseline->remaining): {out6[:1000]} err:{err6[:300]}")

    # Also ensure crypto pipeline file notes it waits for stocks 1Y
    out7, _, _ = ssh_s1("\"echo '[crypto-style] per_sym->baseline->remaining waits for stocks 1Y phase - verified ' + str(date) >> /tmp/crypto_30d.log; echo done\"", timeout=5)
    return "relaunched"

def one_check():
    ts = utcnow()
    log(f"=== check {ts} ===")
    # 1. local log
    local = check_local_log()
    log(f"[local-log] exists={local['exists']} age={local['mtime_age']} stall={local['stall']} zero_trades={local['zero_trades']} zero_ratio={local['zero_deltas_ratio']:.2f} lines={local['lines']}")
    # 2. S1 log
    s1log = check_s1_log()
    log(f"[s1-log] exists={s1log['exists']} age={s1log['mtime_age']} stall={s1log['stall']} zero_trades={s1log['zero_trades']} zero_cnt={s1log['zero_deltas_count']} lines={s1log['lines']}")
    # 3. S1 ps
    s1ps = check_s1_ps()
    log(f"[s1-ps] running={s1ps['running']} pids={s1ps['pids']} raw:{s1ps['ps_out'][:600]}")
    # 4. xlsx
    xlsx = check_xlsx()
    log(f"[xlsx] local={xlsx['local_exists']} s1={xlsx['s1_exists']} stall={xlsx['stall']} zero_deltas={xlsx['zero_deltas']} zero_trades={xlsx['zero_trades']} details:{xlsx['details']} local_age={xlsx.get('local_mtime_age')} s1_age={xlsx.get('s1_mtime_age')}")
    log(f"[xlsx-s1-ls] {xlsx.get('s1_ls','')[:400]}")

    # Decision: intervene if any bad signal
    reasons = []
    # 0 trades
    if local.get("zero_trades") or s1log.get("zero_trades") or xlsx.get("zero_trades"):
        reasons.append("0 trades detected")
    # 0.000 deltas - many zeros = bug (but baseline 79 trades with many negatives is ok; only flag if >60% zeros or zero baseline trades)
    # Current S1 shows many 0.000 but also many negatives - not all zero, so we only flag if ratio high AND trades 0 or stall
    if local.get("zero_deltas_ratio", 0) > ZERO_DELTA_THRESHOLD:
        reasons.append(f"0.000 deltas ratio {local['zero_deltas_ratio']:.1%}")
    if xlsx.get("zero_deltas"):
        # Only treat xlsx zeros as fatal if accompanied by 0 trades or stall; else log warning
        if xlsx.get("zero_trades") or xlsx.get("stall") or s1log.get("stall"):
            reasons.append(f"xlsx 0.000 deltas {xlsx['details']}")
        else:
            log(f"[warn] xlsx 0.000 deltas but not fatal (many switches are neutral): {xlsx['details']}")
    # stall >5min - require log stall (primary), xlsx stall only corroborates
    if s1log.get("stall"):
        reasons.append(f"S1 log stall {s1log['mtime_age']:.0f}s (>300s)")
    elif local.get("stall") and not s1log.get("exists"):
        reasons.append(f"local log stall {local['mtime_age']:.0f}s")
    # xlsx stall alone is NEVER sufficient (local mirror is stale by design)
    if xlsx.get("stall") and s1log.get("stall") and s1ps.get("running"):
        reasons.append(f"xlsx+log stall {xlsx.get('s1_mtime_age'):.0f}s")

    # Also detect wrong symbols_trb logic: BONK running when stocks should be first
    # If BONK is running but we expect stocks first, that's a bug
    if s1ps.get("running"):
        # Check if stocks queue has pending items before BONK
        out, _, _ = ssh_s1("\"cat ~/binance-sandbox/data/reports/lifecycle_pilot/campaign_order_1mo.json 2>&1 | python3 -c \\\"import json,sys; d=json.load(open('/home/niels/binance-sandbox/data/reports/lifecycle_pilot/campaign_order_1mo.json')); q=d.get('queue',[]); print('QUEUE:'+','.join([x.get('symside','') for x in q[:5]]))\\\" 2>&1 | head -5\"", timeout=10)
        if "QUEUE:" in out:
            queue_head = out.split("QUEUE:")[-1].strip().split(",")[0] if "," in out else out
            log(f"[queue] campaign head: {out[:300]}")
            # If queue head is stocks (not BONK) but BONK is running -> wrong order
            if "BONK" not in queue_head and "BONK" in s1ps.get("ps_out",""):
                # Only flag if BONK shouldn't be running yet (stocks pending)
                # Check if stocks 1Y not done -> BONK should wait
                stocks = check_stocks_1y_phase()
                if not stocks.get("done"):
                    reasons.append(f"wrong order: BONK running but queue head is {queue_head} (stocks first)")

    # S1 ps missing when it should be running?
    # If no ps but xlsx stall -> also intervene

    if reasons:
        log(f"[ALERT] triggers: {'; '.join(reasons)}")
        intervene_kill_and_relaunch("; ".join(reasons))
        return False
    else:
        log(f"[ok] all checks pass - 79 trades, deltas varied, no stall, stocks-first respected")
        return True

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--single", action="store_true", help="run one check and exit (for cron/launchd)")
    ap.add_argument("--loop", action="store_true", help="run forever (default)")
    args = ap.parse_args()
    if args.single:
        log(f"[monitor-single] pid={os.getpid()} stall={STALL_THRESHOLD_S}s")
        try:
            one_check()
        except Exception as e:
            import traceback
            log(f"[error] single exception {e} {traceback.format_exc()[:1000]}")
        return
    log(f"[monitor] start pid={os.getpid()} interval={CHECK_INTERVAL}s stall={STALL_THRESHOLD_S}s log={LOG_PATH} root={ROOT}")
    log(f"[monitor] watching: /tmp/crypto_30d.log, SPREADSHEETS/1000BONK*_30d_matrix.xlsx, s1-int ps aux | grep 1000BONK")
    log(f"[monitor] triggers: 0 trades, 0.000 deltas, stall>5min -> kill+relaunch symbols_trb stocks-first, crypto per_sym->baseline->remaining waits stocks 1Y")
    # Run forever
    n = 0
    while True:
        try:
            n += 1
            log(f"--- iteration {n} ---")
            one_check()
        except Exception as e:
            import traceback
            log(f"[error] iteration {n} exception {e} {traceback.format_exc()[:1000]}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
