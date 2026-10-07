#!/usr/bin/env python3
# push3.py - Push Tradier scripts to server
# CONFIGURATION:
# - Data/Positions: Running for ALL accounts (tra, trb, trc)
# - Active Trading: Running DUAL INSTANCES (TRA=Live, TRC=Sandbox)

import os
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

REMOTE_USER_HOST = "niels@157.180.125.52"
REMOTE_DIR = "/home/niels/binance"
LOG_DIR = "/home/niels/logs"
WORKINGSET_DIR = "/home/niels/binance/workingset"

FILES = [
    "tradier_api.py",
    "tradier_prices.py",
    "tradier_positions.py",
    "tradier_indicators.py",
    "tradier_rankings.py",
    "tradier_manage.py",
    "config_tradier.py",
    "symbols_tradier.json",
    "utils.py",
]

def run_cmd(cmd, timeout=30):
    """Execute command with timeout"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"Command timed out after {timeout}s"
    except Exception as e:
        return 1, "", str(e)

def cleanup_old_workingsets():
    """Delete workingset folders older than 3 weeks"""
    cutoff_date = datetime.now() - timedelta(weeks=3)
    cleanup_cmd = f"ssh -o ConnectTimeout=5 {REMOTE_USER_HOST} 'cd {WORKINGSET_DIR} && find . -maxdepth 1 -type d -name \"workingset_tradier_*\" -exec sh -c \"if [ \\\"\\$(stat -c %Y \\\"\\$1\\\")\\\" -lt \\\"\\$(date -d \\\"{cutoff_date.strftime('%Y-%m-%d')}\\\" +%s)\\\" ]; then rm -rf \\\"\\$1\\\"; echo \\\"Deleted \\$1\\\"; fi\" _ {{}} \\;'"
    rc, stdout, stderr = run_cmd(cleanup_cmd, timeout=10)
    if stdout.strip():
        print(f"🗑️ Cleaned up: {stdout.strip()}")

def main():
    start_time = time.time()
    base_dir = Path(__file__).resolve().parent
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    workingset_folder = f"{WORKINGSET_DIR}/workingset_tradier_{timestamp}"
    
    print("🚀 TRADIER PUSH TO SERVER (DUAL INSTANCE MODE)")
    print(f"🎯 CONFIG: TRB (Live) + TRC (Sandbox) running simultaneously")
    print("=" * 50)
    
    # Verify files exist
    missing = [f for f in FILES if not (base_dir / f).exists()]
    if missing:
        print(f"❌ Missing files: {', '.join(missing)}")
        sys.exit(1)
    
    # Clean up old workingsets
    print("🗑️ Cleaning up old workingsets...")
    cleanup_old_workingsets()
    
    # 1. KILL ALL TRADIER PYTHON SCRIPTS
    print("⚡ Killing all Tradier scripts on server...")
    # Updated kill logic to ensure both manage instances die
    kill_cmd = f"ssh -o ConnectTimeout=5 {REMOTE_USER_HOST} 'pkill -9 -f \"python.*tradier_\" 2>/dev/null || true'"
    run_cmd(kill_cmd, timeout=5)
    time.sleep(1)
    
    print("✅ All Tradier scripts killed")
    
    # 2. UPLOAD FILES
    print("⚡ Uploading files...")
    files_str = " ".join(f'"{base_dir / f}"' for f in FILES)
    files_to_copy = " ".join(f"{REMOTE_DIR}/{f}" for f in FILES)
    
    rsync_cmd = f"rsync -az --compress-level=1 --inplace --no-perms --no-times --timeout=30 {files_str} {REMOTE_USER_HOST}:{REMOTE_DIR}/"
    rc, stdout, stderr = run_cmd(rsync_cmd, timeout=60)
    if rc != 0:
        print(f"❌ Upload failed: {stderr}")
        sys.exit(1)
    print("✅ Files uploaded")

    # 3. BACKUP & PERMISSIONS
    print("💾 Backing up to workingset...")
    run_cmd(f"ssh {REMOTE_USER_HOST} 'mkdir -p {workingset_folder}'", timeout=10)
    rsync_workingset_cmd = f"rsync -az --compress-level=1 --inplace --no-perms --no-times --partial --timeout=60 {files_str} {REMOTE_USER_HOST}:{workingset_folder}/"
    run_cmd(rsync_workingset_cmd, timeout=180)
    
    print("🔧 Fixing permissions...")
    run_cmd(f"ssh {REMOTE_USER_HOST} 'cd {REMOTE_DIR} && sudo chown -R niels:niels {files_to_copy} 2>/dev/null || true && sudo chmod 644 {files_to_copy} 2>/dev/null || true'", timeout=10)
    
    # 4. START SCRIPTS (DUAL INSTANCE)
    print("⚡ Starting Tradier scripts (Dual Instances)...")
    
    # Note: tradier_positions runs for ALL accounts to feed data to both managers
    # Note: tradier_manage is launched TWICE. Once for tra, once for trc.
    start_cmd = f"""ssh -o ConnectTimeout=5 {REMOTE_USER_HOST} 'cd {REMOTE_DIR} && source /home/niels/miniforge3/etc/profile.d/conda.sh && conda activate binance_env && {{
        nohup python3 -u tradier_prices.py >> {LOG_DIR}/tradier_prices.log 2>&1 &
        nohup python3 -u tradier_indicators.py >> {LOG_DIR}/tradier_indicators.log 2>&1 &
        nohup python3 -u tradier_rankings.py >> {LOG_DIR}/tradier_rankings.log 2>&1 &
        nohup python3 -u tradier_positions.py --accounts tra trb trc >> {LOG_DIR}/tradier_positions.log 2>&1 &
        nohup python3 -u server.py >> {LOG_DIR}/server.log 2>&1 &
        
        echo "Starting TRB (Live) Manager..."
        nohup python3 -u tradier_manage.py --accounts trb >> {LOG_DIR}/tradier_manage_trb.log 2>&1 &
        
        echo "Starting TRC (Sandbox) Manager..."
        nohup python3 -u tradier_manage.py --accounts trc >> {LOG_DIR}/tradier_manage_trc.log 2>&1 &
    }}'"""
    
    run_cmd(start_cmd, timeout=15)
    print("✅ Scripts started")
    
    # 5. VERIFICATION
    print("⚡ Verifying processes...")
    time.sleep(3)
    # Check specifically for manage scripts
    verify_cmd = f"ssh -o ConnectTimeout=5 {REMOTE_USER_HOST} 'ps aux | grep tradier_manage.py | grep -v grep'"
    rc, stdout, stderr = run_cmd(verify_cmd, timeout=5)
    
    trb_running = "trb" in stdout
    trc_running = "trc" in stdout
    
    if trb_running and trc_running:
        print(f"✅ Both Managers Running (TRB & TRC)")
    elif trb_running:
        print(f"⚠️ Only TRB is running (TRC failed)")
    elif trc_running:
        print(f"⚠️ Only TRC is running (TRA failed)")
    else:
        print(f"❌ No managers running!")

    # 6. TAIL LOGS
    print("\n📋 To watch TRB (Live) weather:")
    print(f"   ssh {REMOTE_USER_HOST} 'tail -f {LOG_DIR}/tradier_manage_trb.log'")
    print("\n📋 To watch TRC (Sandbox) weather:")
    print(f"   ssh {REMOTE_USER_HOST} 'tail -f {LOG_DIR}/tradier_manage_trc.log'")

    elapsed = time.time() - start_time
    print("=" * 50)
    print(f"⚡ PUSH COMPLETED IN {elapsed:.1f} SECONDS!")

if __name__ == "__main__":
    main()