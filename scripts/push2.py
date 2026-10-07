#!/usr/bin/env python3
# Lightning-fast push2 script - ez_manage and ez_positions
import subprocess
import sys
import time
from pathlib import Path

REMOTE_USER_HOST = "niels@157.180.125.52"
REMOTE_DIR = "/home/niels/binance"
WORKINGSET_DIR = "/home/niels/binance/workingset"

FILES = [
    "ez_manage.py",
    "ez_positions.py",
    "ez_positions_service.py",
    "config.py"
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

def main():
    start_time = time.time()
    base_dir = Path(__file__).resolve().parent
    
    print("⚡ LIGHTNING PUSH2 - EZ_MANAGE AND EZ_POSITIONS")
    print("=" * 50)
    
    # Verify files exist
    missing = [f for f in FILES if not (base_dir / f).exists()]
    if missing:
        print(f"❌ Missing files: {', '.join(missing)}")
        sys.exit(1)
    
    # 1. KILL EZ_MANAGE AND EZ_POSITIONS PROCESSES (including watchdog)
    print("⚡ Killing ez_manage and ez_positions on server...")
    kill_cmd = f"ssh -o ConnectTimeout=5 {REMOTE_USER_HOST} 'pkill -9 -f \"python.*ez_manage\" 2>/dev/null; pkill -9 -f \"python.*ez_positions\" 2>/dev/null; pkill -9 -f \"watchdog.*ez_manage\" 2>/dev/null; pkill -9 -f \"watchdog.*ez_positions\" 2>/dev/null; true'"
    run_cmd(kill_cmd, timeout=5)
    print("✅ ez_manage and ez_positions killed")
    
    # 2. UPLOAD FILES
    print("⚡ Uploading files...")
    files_str = " ".join(f'"{base_dir / f}"' for f in FILES)
    
    # Upload and copy to workingset
    files_to_copy = " ".join(f"{REMOTE_DIR}/{f}" for f in FILES)
    rsync_cmd = (
        f"rsync -az --compress-level=1 --inplace --no-perms --no-times "
        f"--timeout=30 {files_str} {REMOTE_USER_HOST}:{REMOTE_DIR}/ && "
        f"ssh {REMOTE_USER_HOST} 'mkdir -p {WORKINGSET_DIR} && "
        f"cp {files_to_copy} {WORKINGSET_DIR}/ 2>/dev/null'"
    )
    
    rc, stdout, stderr = run_cmd(rsync_cmd, timeout=30)
    if rc != 0:
        print(f"❌ Upload failed: {stderr}")
        sys.exit(1)
    print("✅ Files uploaded and copied to workingset")
    
    # 3. START EZ_MANAGE AND EZ_POSITIONS INSTANCES
    print("⚡ Starting ez_manage and ez_positions instances...")
    
    start_cmd = f"""ssh -o ConnectTimeout=5 {REMOTE_USER_HOST} 'cd {REMOTE_DIR} && source /home/niels/miniforge3/etc/profile.d/conda.sh && conda activate binance_env && {{
        nohup python3 -u ez_manage.py ang >> /home/niels/logs/ez_manage_ang.log 2>&1 &
        nohup python3 -u ez_manage.py inf >> /home/niels/logs/ez_manage_inf.log 2>&1 &
        nohup python3 -u ez_manage.py men >> /home/niels/logs/ez_manage_men.log 2>&1 &
        nohup python3 -u ez_manage.py flz >> /home/niels/logs/ez_manage_flz.log 2>&1 &
        nohup python3 -u ez_manage.py fin >> /home/niels/logs/ez_manage_fin.log 2>&1 &
        (sudo -n systemctl restart binance-ezpositions.service && echo "ez_positions managed via systemd") || nohup python3 -u ez_positions.py >> /home/niels/logs/ez_positions.log 2>&1 &
        (sudo -n systemctl restart binance-ezpositions-service.service && echo "ez_positions_service managed via systemd") || nohup python3 -u ez_positions_service.py >> /home/niels/logs/ez_positions_service.log 2>&1 &
        echo "ez_manage and ez_positions instances started"
    }}'"""
    
    run_cmd(start_cmd, timeout=10)
    print("✅ ez_manage and ez_positions instances started")
    
    # 4. QUICK VERIFICATION
    print("⚡ Verifying...")
    time.sleep(2)  # Brief pause for scripts to start
    
    verify_cmd = f"ssh -o ConnectTimeout=5 {REMOTE_USER_HOST} 'pgrep -f \"python.*ez_manage\" | wc -l'"
    rc, stdout, stderr = run_cmd(verify_cmd, timeout=5)
    
    if rc == 0 and stdout.strip().isdigit():
        count = int(stdout.strip())
        print(f"✅ {count} ez_manage instances running")
        
        # Show which instances
        instances_cmd = f'ssh -o ConnectTimeout=5 {REMOTE_USER_HOST} \'ps aux | grep "python.*ez_manage" | grep -v grep | awk "{{print \\$NF}}" | sort\''
        rc2, stdout2, stderr2 = run_cmd(instances_cmd, timeout=5)
        if rc2 == 0 and stdout2:
            print("  Running: " + ", ".join(stdout2.strip().split('\n')))
    
    verify_positions_cmd = f"ssh -o ConnectTimeout=5 {REMOTE_USER_HOST} 'pgrep -f \"python.*ez_positions\" | wc -l'"
    rc3, stdout3, stderr3 = run_cmd(verify_positions_cmd, timeout=5)
    if rc3 == 0 and stdout3.strip().isdigit():
        count3 = int(stdout3.strip())
        print(f"✅ {count3} ez_positions instances running")
    
    # Done!
    elapsed = time.time() - start_time
    print("=" * 50)
    print(f"⚡ PUSH2 COMPLETED IN {elapsed:.1f} SECONDS!")
    print(f"📁 {len(FILES)} files uploaded")
    print(f"📁 Workingset saved to {WORKINGSET_DIR}")
    print(f"🚀 ez_manage and ez_positions instances running on server")

if __name__ == "__main__":
    main()