#!/usr/bin/env python3
"""
monitor_mega — s2-mega CCX33 (62.238.125.113 via s2-mega alias + 10.0.0.4 private)
Every 30s: tail indicators count (must stay 552), /tmp/*.log,
           data/reports/lifecycle_pilot/*, SPREADSHEETS/*.xlsx timestamps.
If NPZ copy stalls, rsync fails, or mega sweep shows 0/60 loop,
UnboundLocalError, or no progress >3min -> intervene:
  sed -i "/10.0.0.4/d" ~/.ssh/known_hosts, pkill, relaunch with PYTHONPATH + --workers 8 per-sheet.
Log to /tmp/monitor_mega.log. Ensures 1Y mega sweep (TRB per_sym first: 29L+16S) never hangs.
"""
import subprocess
import time
import json
import os
import sys
import re
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = Path("/tmp/monitor_mega.log")
CHECK_INTERVAL = 30
STALL_THRESHOLD_S = 180  # 3 min
INDICATORS_EXPECTED = 552  # total including tmp .*.npz

# s2-mega hosts
S2_MEGA = "s2-mega"  # 62.238.125.113
S2_PRIVATE = "10.0.0.4"

# TRB per_sym first: 29 long + 16 short for 1Y
TRB_PER_SYM_FIRST_LONG = 29
TRB_PER_SYM_FIRST_SHORT = 16

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

def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if e.stdout else "" if isinstance(e.stdout, bytes) else (e.stdout or "")
        return out, "timeout", 124
    except Exception as e:
        return "", str(e), 1

def ssh(host, cmd, timeout=20):
    # use BatchMode yes, ConnectTimeout 10
    full = f"ssh -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=accept-new {host} {cmd!r}"
    # Use bash -c for proper quoting
    shell = f"ssh -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=accept-new {host} {cmd}"
    try:
        r = subprocess.run(["bash", "-c", shell], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "ssh timeout", 124
    except Exception as e:
        return "", str(e), 1

def check_indicators():
    """Check ~/binance-sandbox/backtest_v8/indicators count must stay 552"""
    result = {"ok": False, "count": None, "npz": None, "details": "", "stall": False}
    out, err, rc = ssh(S2_MEGA, "'ls ~/binance-sandbox/backtest_v8/indicators 2>&1 | wc -l; echo ---NPZ---; ls ~/binance-sandbox/backtest_v8/indicators/*.npz 2>&1 | wc -l; echo ---LS---; ls ~/binance-sandbox/backtest_v8/indicators 2>&1 | head -5'", timeout=15)
    combined = out + " " + err
    try:
        lines = out.splitlines()
        if len(lines) >= 1:
            total = int(lines[0].strip()) if lines[0].strip().isdigit() else None
            result["count"] = total
            if total == INDICATORS_EXPECTED:
                result["ok"] = True
            else:
                result["details"] = f"count {total} != {INDICATORS_EXPECTED}"
        if len(lines) >= 2:
            # second count after ---NPZ---
            for i, l in enumerate(lines):
                if "---NPZ---" in l and i+1 < len(lines):
                    npz = lines[i+1].strip()
                    if npz.isdigit():
                        result["npz"] = int(npz)
                    break
        result["raw"] = out[:800]
    except Exception as e:
        result["details"] = f"parse err {e} raw:{out[:300]}"
    # Try private host too
    out2, err2, rc2 = ssh(S2_PRIVATE, "'ls ~/binance-sandbox/backtest_v8/indicators 2>&1 | wc -l'", timeout=10)
    result["private_raw"] = (out2 + err2)[:300]
    if "Host key verification failed" in (out2 + err2):
        result["private_hostkey_fail"] = True
        result["details"] += " private Host key verification failed;"
    return result

def check_tmp_logs():
    """tail /tmp/*.log on s2-mega"""
    result = {"exists": False, "files": [], "stall": False, "errors": [], "raw": ""}
    out, err, rc = ssh(S2_MEGA, "'ls -lt /tmp/*.log 2>&1 | head -20; echo ---LS_TMP_DONE---; ls /tmp/ 2>&1 | head -30'", timeout=15)
    result["raw"] = out[:3000]
    if "No such file" in out and "ls: cannot access" in out and "/tmp/*.log" in out:
        # check alternative log locations
        out2, _, _ = ssh(S2_MEGA, "'ls -lt /tmp/v12*.log /tmp/trb*.log /tmp/*mega*.log 2>&1 | head -20'", timeout=10)
        if "No such file" not in out2:
            result["raw"] += " ALT:" + out2[:1000]
            if out2.strip():
                result["exists"] = True
                result["files"] = [l for l in out2.splitlines() if l.strip()]
        else:
            result["details"] = "no /tmp/*.log"
            # Not necessarily error - mega may not have started
            # Check if any logs at all
            result["exists"] = False
    else:
        if out.strip() and "cannot access" not in out:
            result["exists"] = True
            result["files"] = [l for l in out.splitlines() if l.strip() and "---" not in l][:10]
    # Check for error patterns in logs - include mega_1y.log explicitly
    tail_out, _, _ = ssh(S2_MEGA, "'tail -200 /tmp/*.log /tmp/mega_1y.log 2>&1 | head -300; echo ---TAIL2---; cat /tmp/v12*.log /tmp/mega_1y.log 2>&1 | tail -100 | head -100; echo ---CAT_DONE---'", timeout=15)
    result["tail"] = tail_out[:4000]
    if "UnboundLocalError" in tail_out:
        result["errors"].append("UnboundLocalError")
    if "0/60" in tail_out:
        result["errors"].append("0/60 loop")
    if "rsync" in tail_out.lower() and ("failed" in tail_out.lower() or "error" in tail_out.lower()):
        result["errors"].append("rsync fail")
    if "Host key verification failed" in tail_out:
        result["errors"].append("Host key fail")
    # stall: check mtime age of newest log
    mtime_out, _, _ = ssh(S2_MEGA, "'stat -c %Y /tmp/*.log 2>&1 | sort -n | tail -1; echo ---; date +%s'", timeout=10)
    try:
        lines = mtime_out.splitlines()
        nums = [int(x) for x in lines if x.strip().isdigit()]
        if len(nums) >= 2:
            newest = nums[-2] if len(nums) >=2 else nums[0]
            now = nums[-1]
            age = now - newest
            result["mtime_age"] = age
            if age > STALL_THRESHOLD_S:
                result["stall"] = True
                result["details"] = f"log stall {age}s"
    except Exception:
        pass
    return result

def check_lifecycle_pilot():
    """Check ~/binance-sandbox/data/reports/lifecycle_pilot/* timestamps"""
    result = {"exists": False, "files": [], "stall": False, "mtime_age": None, "details": ""}
    out, err, rc = ssh(S2_MEGA, "'ls -lt ~/binance-sandbox/data/reports/lifecycle_pilot/ 2>&1 | head -30; echo ---STAT---; stat ~/binance-sandbox/data/reports/lifecycle_pilot/* 2>&1 | head -40'", timeout=15)
    result["raw"] = out[:3000]
    if "No such file" in out and "cannot access" in out:
        result["details"] = "no lifecycle_pilot dir"
        # Try alternative path
        out2, _, _ = ssh(S2_MEGA, "'ls -la ~/binance-sandbox/data/reports/ 2>&1 | head -20; echo ---; ls -la ~/binance-sandbox/data/ 2>&1 | head -20'", timeout=10)
        result["alt"] = out2[:1000]
    else:
        if out.strip():
            result["exists"] = True
            result["files"] = [l for l in out.splitlines() if l.strip()][:10]
        # Check mtime of newest progress json
        mtime_out, _, _ = ssh(S2_MEGA, "'ls -t ~/binance-sandbox/data/reports/lifecycle_pilot/*.json 2>&1 | head -1 | xargs -I{} stat -c %Y {} 2>&1; echo ---NOW---; date +%s'", timeout=10)
        try:
            lines = mtime_out.splitlines()
            nums = [int(x) for x in lines if x.strip().isdigit()]
            if len(nums) >= 2:
                age = nums[-1] - nums[-2]
                result["mtime_age"] = age
                if age > STALL_THRESHOLD_S:
                    result["stall"] = True
                    result["details"] = f"pilot stall {age}s"
            elif len(nums) == 1:
                # Only now, file not found
                result["mtime_age"] = None
        except Exception:
            pass
    return result

def check_spreadsheets():
    """Check SPREADSHEETS/*.xlsx timestamps"""
    result = {"exists": False, "count": 0, "stall": False, "mtime_age": None, "details": "", "raw": ""}
    out, err, rc = ssh(S2_MEGA, "'ls -lt ~/binance-sandbox/SPREADSHEETS/*.xlsx 2>&1 | head -20; echo ---STAT_XLSX---; stat -c \"%n %y\" ~/binance-sandbox/SPREADSHEETS/*.xlsx 2>&1 | head -20; echo ---COUNT---; ls ~/binance-sandbox/SPREADSHEETS/*.xlsx 2>&1 | wc -l'", timeout=15)
    result["raw"] = out[:3000]
    if "No such file" in out and "cannot access" in out and "0" in out:
        result["details"] = "no xlsx"
    else:
        # count
        try:
            m = re.search(r"---COUNT---\s*\n(\d+)", out)
            if m:
                result["count"] = int(m.group(1))
                result["exists"] = result["count"] > 0
        except Exception:
            pass
        # mtime - find newest xlsx Modify
        mtime_out, _, _ = ssh(S2_MEGA, "'ls -t ~/binance-sandbox/SPREADSHEETS/*.xlsx 2>&1 | head -1 | xargs -I{} stat -c %Y {} 2>&1; echo ---NOW---; date +%s'", timeout=10)
        try:
            lines = mtime_out.splitlines()
            nums = [int(x) for x in lines if x.strip().isdigit()]
            if len(nums) >= 2:
                age = nums[-1] - nums[-2]
                result["mtime_age"] = age
                if age > STALL_THRESHOLD_S:
                    # Only stall if sweep should be running
                    result["stall"] = True
                    result["details"] = f"xlsx stall {age}s"
        except Exception:
            pass
    return result

def check_mega_sweep_ps():
    """Check if mega sweep is running, progress, errors"""
    result = {"running": False, "pids": [], "cmdline": "", "progress_stall": False, "errors": [], "raw": "", "log_tail": ""}
    out, err, rc = ssh(S2_MEGA, "'ps aux 2>&1 | grep -E \"v12_pilot|sheet_runner|lifecycle_pilot|NPZ|rsync\" | grep -v grep | head -20; echo ---PS_DONE---; ps aux --sort=-%cpu 2>&1 | head -15'", timeout=15)
    result["raw"] = out[:4000]
    if "v12_pilot" in out or "sheet_runner" in out or "lifecycle_pilot" in out:
        result["running"] = True
        for line in out.splitlines():
            if "v12" in line or "sheet" in line:
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    result["pids"].append(parts[1])
        result["cmdline"] = out[:1500]
    # Tail logs for error patterns
    tail_out, _, _ = ssh(S2_MEGA, "'tail -300 /tmp/v12*.log /tmp/*pilot*.log /tmp/trb*.log /tmp/mega_1y.log 2>&1 | tail -300; echo ---TAIL_DONE---; cat /tmp/monitor_mega.log 2>&1 | tail -20'", timeout=15)
    # Try broader
    if not tail_out.strip() or "No such file" in tail_out:
        tail_out2, _, _ = ssh(S2_MEGA, "'tail -300 ~/binance-sandbox/*.log 2>&1 | tail -100; echo ---; journalctl --user -n 20 2>&1 | head -20'", timeout=10)
        tail_out = tail_out + "\n" + tail_out2
    result["log_tail"] = tail_out[:5000]
    if "UnboundLocalError" in tail_out:
        result["errors"].append("UnboundLocalError")
    if "0/60" in tail_out:
        result["errors"].append("0/60 loop")
    # 0/60 could also be in progress json - check progress files
    prog_out, _, _ = ssh(S2_MEGA, "'cat ~/binance-sandbox/data/reports/lifecycle_pilot/*progress*.json 2>&1 | grep -c \"0/60\\|UnboundLocal\" | head -5; echo ---; ls -lt ~/binance-sandbox/data/reports/lifecycle_pilot/*.json 2>&1 | head -5'", timeout=10)
    if "UnboundLocal" in prog_out:
        result["errors"].append("UnboundLocalError in progress")
    # No progress >3min: check progress json mtime vs now
    prog_mtime, _, _ = ssh(S2_MEGA, "'ls -t ~/binance-sandbox/data/reports/lifecycle_pilot/*.json 2>&1 | head -1 | xargs -I{} sh -c \"stat -c %Y {}; echo ---; cat {} | grep -c done 2>&1 | head -5\" 2>&1 | head -20; echo ---NOW---; date +%s'", timeout=10)
    # Also check xlsx progress for stall
    if result["running"]:
        # If running but no log/progress update for 3min, it's stall
        # Check last progress json mtime
        mtime_check, _, _ = ssh(S2_MEGA, "'ls -t ~/binance-sandbox/data/reports/lifecycle_pilot/*.json ~/binance-sandbox/SPREADSHEETS/*.xlsx 2>&1 | head -1 | xargs -I{} stat -c %Y {} 2>&1; echo ---NOW---; date +%s'", timeout=10)
        try:
            nums = [int(x) for x in mtime_check.splitlines() if x.strip().isdigit()]
            if len(nums) >= 2:
                age = nums[-1] - nums[-2]
                if age > STALL_THRESHOLD_S:
                    result["progress_stall"] = True
                    result["progress_age"] = age
        except Exception:
            pass
        # Also check ps etimes for hang >3min with no output
        if result["pids"]:
            pid0 = result["pids"][0]
            etimes_out, _, _ = ssh(S2_MEGA, f"'ps -o etimes= -p {pid0} 2>&1; echo ---; ps -o etime= -p {pid0} 2>&1'", timeout=10)
            try:
                et = etimes_out.split()[0].strip() if etimes_out.split() else "0"
                if et.isdigit() and int(et) > STALL_THRESHOLD_S:
                    # If etimes >3min and progress stall, then stall
                    if result.get("progress_stall"):
                        result["errors"].append(f"no progress >3min (pid age {et}s)")
            except Exception:
                pass
    return result

def check_rsyc_and_npz_stall():
    """Check NPZ copy / rsync stall"""
    result = {"stall": False, "rsync_fail": False, "details": "", "raw": ""}
    out, err, rc = ssh(S2_MEGA, "'ps aux 2>&1 | grep rsync | grep -v grep | head -10; echo ---RSYNC_PS---; tail -50 /tmp/rsync*.log 2>&1 | head -50; echo ---NPZ_COUNT---; ls ~/binance-sandbox/backtest_v8/indicators/*.npz 2>&1 | wc -l; ls ~/binance-sandbox/backtest_v8/indicators/ 2>&1 | wc -l'", timeout=15)
    result["raw"] = out[:3000]
    if "rsync" in out.lower() and ("failed" in out.lower() or "error" in out.lower() or "Host key" in out):
        result["rsync_fail"] = True
        result["details"] = "rsync failed"
    # If NPZ copy stalls: count shows 476 vs 552 (as seen) - not stalled, but if total stays <552 and rsync ps stuck
    if "rsync" in out and result.get("npz", 0) and result.get("npz", 0) < 552:
        # Check rsync age
        rsync_age_out, _, _ = ssh(S2_MEGA, "'ps -o etimes= -C rsync 2>&1 | head -5'", timeout=10)
        try:
            age = int(rsync_age_out.split()[0].strip()) if rsync_age_out.split() else 0
            if age > STALL_THRESHOLD_S:
                result["stall"] = True
                result["details"] += f" NPZ rsync stall {age}s"
        except Exception:
            pass
    # Check for copy stall via tmp files not moving
    tmp_age_out, _, _ = ssh(S2_MEGA, "'ls -t ~/binance-sandbox/backtest_v8/indicators/.tmp* 2>&1 | head -1 | xargs -I{} stat -c %Y {} 2>&1; echo ---NOW---; date +%s; echo ---TMP_COUNT---; ls ~/binance-sandbox/backtest_v8/indicators/.tmp* 2>&1 | wc -l'", timeout=10)
    try:
        nums = [int(x) for x in tmp_age_out.splitlines() if x.strip().isdigit()]
        if len(nums) >= 2:
            # last is now, second last is newest tmp
            age = nums[-1] - nums[-2] if len(nums) >=2 else 0
            if age > STALL_THRESHOLD_S and nums[-2] != nums[-1]:
                # Check if tmp files exist and stall
                if "TMP_COUNT" in tmp_age_out:
                    result["stall"] = True
                    result["details"] += f" tmp NPZ stall {age}s"
    except Exception:
        pass
    return result

def intervene(reason):
    log(f"[INTERVENE] {reason} -> fix known_hosts, pkill, relaunch --workers 8 per-sheet")
    # 1. Fix known_hosts on Mac (local) and s2-mega
    out1, _, _ = run("sed -i '' '/10.0.0.4/d' ~/.ssh/known_hosts 2>&1; echo FIXED_LOCAL; wc -l ~/.ssh/known_hosts 2>&1", timeout=5)
    log(f"[fix] local known_hosts sed: {out1[:300]}")
    # Also fix on s2-mega (if s2-mega connects to 10.0.0.4)
    out2, err2, _ = ssh(S2_MEGA, "'sed -i \"/10.0.0.4/d\" ~/.ssh/known_hosts 2>&1; echo FIXED_MEGA; wc -l ~/.ssh/known_hosts 2>&1; cat ~/.ssh/known_hosts 2>&1 | head -5'", timeout=10)
    log(f"[fix] s2-mega known_hosts sed: {out2[:500]} {err2[:200]}")
    # Also fix alt path: ssh-keygen -R 10.0.0.4
    out3, _, _ = ssh(S2_MEGA, "'ssh-keygen -R 10.0.0.4 2>&1 | head -5; echo GEN_DONE'", timeout=10)
    log(f"[fix] ssh-keygen -R: {out3[:300]}")
    # 2. pkill on s2-mega
    out4, err4, _ = ssh(S2_MEGA, "'pkill -9 -f v12_pilot 2>&1; pkill -9 -f sheet_runner 2>&1; pkill -9 -f lifecycle_pilot 2>&1; echo PKILL_DONE; ps aux 2>&1 | grep -E \"v12|pilot\" | grep -v grep | head -5'", timeout=15)
    log(f"[pkill] s2-mega: {out4[:600]} {err4[:200]}")
    # Also pkill stuck rsync if NPZ stall
    out5, _, _ = ssh(S2_MEGA, "'pkill -9 -f rsync 2>&1; echo RSYNC_PKILL_DONE'", timeout=10)
    log(f"[pkill] rsync: {out5[:300]}")
    # Clear hang sentinel if exists
    out6, _, _ = ssh(S2_MEGA, "'rm -f /tmp/v12_hang_sentinel /tmp/trb_matrix.lock 2>&1; echo SENTINEL_CLEARED'", timeout=10)
    log(f"[clear] sentinel: {out6[:300]}")
    # 3. Relaunch with PYTHONPATH and --workers 8 per-sheet
    # 1Y mega sweep: TRB per_sym first 29 long +16 short, window 365, workers 8
    # Use the next missing TRB sym_side strategy
    relaunch_cmd = (
        "cd ~/binance-sandbox && "
        "PYTHONPATH=/home/niels/binance-sandbox:/home/niels/binance-sandbox/tools "
        "nohup python3 -u tools/opt/v12_pilot_sheet_runner.py "
        "--window-days 365 --workers 8 > /tmp/mega_1y.log 2>&1 & "
        "echo LAUNCHED_MEGA_1Y; sleep 2; "
        "ps aux 2>&1 | grep -E \"v12_pilot|sheet_runner\" | grep -v grep | head -10; "
        "echo ---LOG_HEAD---; head -30 /tmp/mega_1y.log 2>&1 | head -30"
    )
    out7, err7, _ = ssh(S2_MEGA, f"'{relaunch_cmd}'", timeout=20)
    log(f"[relaunch] 1Y mega --workers 8: {out7[:2000]} err:{err7[:500]}")
    # Also handle NPZ rsync if indicators stall: relaunch rsync from S1 (552) or Mac
    if "NPZ" in reason or "indicators" in reason or "rsync" in reason:
        # Fix known_hosts for 10.0.0.4 on both Mac and s2-mega (Host key verification failed)
        out8, _, _ = run("sed -i '' '/10.0.0.4/d' ~/.ssh/known_hosts 2>&1; sed -i '' '/62.238.125.113/d' ~/.ssh/known_hosts 2>&1; sed -i '' '/10.0.0.3/d' ~/.ssh/known_hosts 2>&1; echo FIXED_BOTH", timeout=5)
        log(f"[npz-fix] known_hosts both: {out8[:300]}")
        # Primary: S1 (552) -> Mac relay -> s2-mega (canonical 552 source is S1 via s1-int tunnel)
        # Direct s2-mega pull from S1 fails (Permission denied - no key via gateway), so use Mac relay
        # FIX: Use SSD2T for relay to avoid filling Data volume (/tmp is on Data, was 27G leak); auto-cleanup after push
        relay_cmd = (
            "mkdir -p /Volumes/SSD2T/tmp_s1_relay 2>/dev/null || mkdir -p /tmp/s1_indicators_relay && "
            "RELAY_DIR=$( [ -d /Volumes/SSD2T/tmp_s1_relay ] && echo /Volumes/SSD2T/tmp_s1_relay || echo /tmp/s1_indicators_relay ); "
            "rsync -avz -e 'ssh -o StrictHostKeyChecking=accept-new' s1-int:~/binance-sandbox/backtest_v8/indicators/*.npz $RELAY_DIR/ > /tmp/npz_relay_pull.log 2>&1; "
            "echo PULL_S1_DONE $(ls $RELAY_DIR/*.npz 2>&1 | wc -l); "
            "rsync -avz -e 'ssh -o StrictHostKeyChecking=accept-new' $RELAY_DIR/*.npz s2-mega:~/binance-sandbox/backtest_v8/indicators/ > /tmp/npz_relay_push.log 2>&1 & "
            "echo PUSH_S2_STARTED; sleep 2; ps aux | grep rsync | grep -v grep | head -5; "
            "tail -5 /tmp/npz_relay_push.log 2>&1 | head -10; "
            # Cleanup relay after 1h to prevent 27G accumulation (keep only last hour)
            "( sleep 3600; find $RELAY_DIR -type f -mmin +60 -delete 2>/dev/null; echo CLEANED_RELAY ) &"
        )
        out9b, _, _ = run(relay_cmd, timeout=90)
        log(f"[npz-rsync] S1->Mac->s2-mega relay: {out9b[:1500]}")
        # Also direct s2-mega pull attempt for completeness (may fail but try)
        s1_pull_cmd = "rsync -avz -e 'ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10' niels@10.0.0.3:~/binance-sandbox/backtest_v8/indicators/*.npz ~/binance-sandbox/backtest_v8/indicators/ > /tmp/npz_pull_s1.log 2>&1 & echo PULL_S1_STARTED; sleep 2; ps aux | grep rsync | grep -v grep | head -5"
        out9c, _, _ = ssh(S2_MEGA, f"'{s1_pull_cmd}'", timeout=20)
        log(f"[npz-rsync] s2-mega direct pull from S1: {out9c[:600]}")
        # Fallback: Mac -> s2-mega via public IP (152 fallback, not 552 but better than 1)
        rsync_cmd = (
            "nohup bash -c '"
            "rsync -avz -e \"ssh -o StrictHostKeyChecking=accept-new\" "
            "~/binance/backtest_v8/indicators/*.npz s2-mega:~/binance-sandbox/backtest_v8/indicators/ "
            "> /tmp/npz_rsync.log 2>&1 & echo RSYNC_STARTED; "
            "sleep 2; ps aux | grep rsync | grep -v grep | head -5; "
            "tail -5 /tmp/npz_rsync.log 2>&1 | head -10' 2>&1 | head -30"
        )
        out9, _, _ = run(rsync_cmd, timeout=20)
        log(f"[npz-rsync] via s2-mega public (fallback 152): {out9[:1000]}")
        out10, _, _ = ssh(S2_MEGA, "'ls ~/binance-sandbox/backtest_v8/indicators/*.npz 2>&1 | wc -l; ls ~/binance-sandbox/backtest_v8/indicators/ 2>&1 | wc -l; echo NPZ_AFTER_FIX'", timeout=10)
        log(f"[npz-check] after fix: {out10[:300]}")
    log(f"[INTERVENE DONE] {reason}")

def one_check(n):
    log(f"=== check #{n} ===")
    # Run checks in parallel to meet 30s interval (each ssh ~10s, serial would be 60s+)
    import concurrent.futures as cf
    ind = tmp = pilot = xlsx = sweep = rsync = {}
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        f_ind = ex.submit(check_indicators)
        f_tmp = ex.submit(check_tmp_logs)
        f_pilot = ex.submit(check_lifecycle_pilot)
        f_xlsx = ex.submit(check_spreadsheets)
        f_sweep = ex.submit(check_mega_sweep_ps)
        f_rsync = ex.submit(check_rsyc_and_npz_stall)
        try:
            ind = f_ind.result(timeout=25)
        except Exception as e:
            ind = {"ok": False, "count": None, "details": f"ind timeout {e}", "raw": ""}
        try:
            tmp = f_tmp.result(timeout=25)
        except Exception as e:
            tmp = {"exists": False, "stall": False, "errors": [], "tail": "", "details": f"tmp timeout {e}"}
        try:
            pilot = f_pilot.result(timeout=25)
        except Exception as e:
            pilot = {"exists": False, "stall": False, "details": f"pilot timeout {e}"}
        try:
            xlsx = f_xlsx.result(timeout=25)
        except Exception as e:
            xlsx = {"exists": False, "count": 0, "stall": False, "details": f"xlsx timeout {e}"}
        try:
            sweep = f_sweep.result(timeout=25)
        except Exception as e:
            sweep = {"running": False, "pids": [], "errors": [], "progress_stall": False, "log_tail": f"timeout {e}"}
        try:
            rsync = f_rsync.result(timeout=25)
        except Exception as e:
            rsync = {"stall": False, "rsync_fail": False, "details": f"rsync timeout {e}", "raw": ""}
    log(f"[indicators] count={ind.get('count')} npz={ind.get('npz')} ok={ind.get('ok')} details={ind.get('details')} raw={ind.get('raw','')[:400]}")
    if ind.get("private_hostkey_fail"):
        log(f"[indicators] private 10.0.0.4 Host key verification failed -> will fix")
    log(f"[tmp-logs] exists={tmp.get('exists')} files={len(tmp.get('files',[]))} stall={tmp.get('stall')} errors={tmp.get('errors')} age={tmp.get('mtime_age')} details={tmp.get('details','')}")
    if tmp.get("tail"):
        for e in tmp.get("errors", []):
            log(f"[tmp-logs] ERROR pattern: {e}")
    log(f"[pilot] exists={pilot.get('exists')} stall={pilot.get('stall')} age={pilot.get('mtime_age')} details={pilot.get('details','')} files={len(pilot.get('files',[]))}")
    log(f"[xlsx] exists={xlsx.get('exists')} count={xlsx.get('count')} stall={xlsx.get('stall')} age={xlsx.get('mtime_age')} details={xlsx.get('details','')}")
    log(f"[sweep] running={sweep.get('running')} pids={sweep.get('pids')} errors={sweep.get('errors')} progress_stall={sweep.get('progress_stall')} age={sweep.get('progress_age')}")
    if sweep.get("log_tail"):
        tail_snip = sweep["log_tail"][-1500:]
        if "UnboundLocalError" in tail_snip or "0/60" in tail_snip:
            log(f"[sweep] tail error snip: {tail_snip[-600:]}")
    log(f"[rsync-npz] stall={rsync.get('stall')} rsync_fail={rsync.get('rsync_fail')} details={rsync.get('details','')}")
    # Decision
    reasons = []
    if not ind.get("ok"):
        reasons.append(f"indicators count {ind.get('count')} != {INDICATORS_EXPECTED} NPZ {ind.get('npz')} {ind.get('details')}")
    if ind.get("private_hostkey_fail"):
        reasons.append("10.0.0.4 Host key verification failed")
    if tmp.get("errors"):
        reasons.append(f"tmp log errors {tmp['errors']}")
    if tmp.get("stall"):
        reasons.append(f"tmp log stall {tmp.get('mtime_age')}s")
    if pilot.get("stall"):
        reasons.append(f"pilot progress stall {pilot.get('mtime_age')}s")
    if xlsx.get("stall") and sweep.get("running"):
        reasons.append(f"xlsx stall {xlsx.get('mtime_age')}s while sweep running")
    if sweep.get("errors"):
        reasons.append(f"sweep errors {sweep['errors']}")
    if sweep.get("progress_stall"):
        reasons.append(f"no progress >3min age {sweep.get('progress_age')}s pids {sweep.get('pids')}")
    if rsync.get("stall") or rsync.get("rsync_fail"):
        reasons.append(f"NPZ copy stall/rsync fail {rsync.get('details')} {rsync.get('raw','')[:200]}")
    # Also explicit 0/60 and UnboundLocalError anywhere
    all_raw = (tmp.get("tail","") + sweep.get("log_tail","") + rsync.get("raw",""))
    if "UnboundLocalError" in all_raw and "UnboundLocalError" not in str(reasons):
        reasons.append("UnboundLocalError detected")
    if "0/60" in all_raw and "0/60" not in str(reasons):
        # Check if it's a loop: repeated 0/60
        if all_raw.count("0/60") >= 2:
            reasons.append("0/60 loop detected")
    # If sweep should be running but not running (and not done), relaunch
    # Check if 1Y sweep expected but not running and not done
    if not sweep.get("running"):
        # Check if TRB 1Y xlsx done: 29+16 =45 handled first, then rest
        # If not all done, should be running
        xlsx_count = xlsx.get("count", 0)
        # Expected 45 for first phase, 121 total for all TRB (63+65=128 but task says 121?)
        # Use 45 as first gate
        if xlsx_count < 45:
            # Check if we expected it to be running (recent launch)
            # Look at pilot dir existence - if no pilot but xlsx <45, maybe need launch
            # Only auto-relaunch if we have not launched recently (avoid spam)
            # Check last intervene time via log
            try:
                age_last_intervene = 0
                # Check mtime of /tmp/mega_1y.log on s2-mega
                mtime_out, _, _ = ssh(S2_MEGA, "'stat -c %Y /tmp/mega_1y.log 2>&1; echo ---NOW---; date +%s'", timeout=10)
                nums = [int(x) for x in mtime_out.splitlines() if x.strip().isdigit()]
                if len(nums) >= 2:
                    age_last = nums[-1] - nums[-2]
                    # If log age >5min and not running and not done -> need relaunch
                    if age_last > 300:
                        reasons.append(f"1Y mega not running but only {xlsx_count}/45 xlsx (expected 29L+16S per_sym first) age {age_last}s")
                elif "No such file" in mtime_out:
                    # Don't auto-launch on first-ever run with no log - just warn, user will launch 1Y mega
                    log(f"[warn] 1Y mega not running, no /tmp/mega_1y.log yet, xlsx {xlsx_count}/45 - not auto-launching, awaiting manual start")
                    pass
            except Exception:
                pass
    if reasons:
        log(f"[ALERT] triggers: {'; '.join(reasons)}")
        intervene("; ".join(reasons))
        return False
    else:
        log(f"[ok] all checks pass - indicators 552, logs fresh, xlsx {xlsx.get('count')}, sweep {'running' if sweep.get('running') else 'idle/done'}")
        return True

def main():
    log(f"[monitor_mega] start pid={os.getpid()} interval={CHECK_INTERVAL}s stall={STALL_THRESHOLD_S}s log={LOG_PATH} hosts={S2_MEGA},{S2_PRIVATE}")
    log(f"[monitor_mega] watching: indicators 552, /tmp/*.log, lifecycle_pilot/*, SPREADSHEETS/*.xlsx")
    log(f"[monitor_mega] triggers: NPZ stall, rsync fail, 0/60 loop, UnboundLocalError, no progress >3min")
    log(f"[monitor_mega] intervene: sed -i '/10.0.0.4/d' known_hosts, pkill, PYTHONPATH + --workers 8 per-sheet")
    log(f"[monitor_mega] 1Y mega sweep TRB per_sym first 29L+16S never hangs")
    n = 0
    while True:
        try:
            n += 1
            one_check(n)
        except Exception as e:
            import traceback
            log(f"[error] iteration {n} {e} {traceback.format_exc()[:1200]}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
