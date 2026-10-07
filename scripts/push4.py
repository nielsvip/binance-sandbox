import os
import subprocess
import sys
import time

# --- CONFIGURATION ---
SERVER_IP = "49.13.39.233" 
SERVER_USER = "niels"
REMOTE_BASE_PATH = "/home/niels/binance"
CONDA_SH = "/home/niels/miniforge3/etc/profile.d/conda.sh"
CONDA_ENV = "binance_env"
LOG_DIR = "/home/niels/logs" 
KLINES_DIR = f"{REMOTE_BASE_PATH}/klines_poly"

FILES_TO_SEND = [
    "poly_scanner.py", "poly_prices.py", "poly_indicators.py", "poly_manage.py",
    "utils.py", "ez_indicators.py", "config_tradier.py", "tradier_api.py"
]

# Aggressive SSH Options
SSH_OPTS = (
    "-o StrictHostKeyChecking=no "
    "-o UserKnownHostsFile=/dev/null "
    "-o ConnectTimeout=20"
)

def run_local(cmd):
    print(f"Executing: {cmd}")
    return subprocess.run(cmd, shell=True, check=True)

def push_and_launch():
    print(f"🚀 Deploying Polymarket to {SERVER_IP}...")

    # 1. RSYNC (Update files)
    files_str = " ".join(FILES_TO_SEND)
    rsync_cmd = f"rsync -avz -e 'ssh {SSH_OPTS}' {files_str} {SERVER_USER}@{SERVER_IP}:{REMOTE_BASE_PATH}/"
    run_local(rsync_cmd)

    # 2. BUILD THE REMOTE SCRIPT
    # We remove the semicolons after the '&' symbols.
    # We activate conda once at the top instead of repeating it.
    remote_script = f"""
source {CONDA_SH}
conda activate {CONDA_ENV}
cd {REMOTE_BASE_PATH}

echo "🧹 Cleaning environment..."
mkdir -p {LOG_DIR}
mkdir -p {KLINES_DIR}
pkill -9 -f poly_ || true
sleep 2

# Test if the module is actually visible before starting
python -c "import py_clob_client" || echo "🚨 CRITICAL: py_clob_client NOT FOUND IN ENV"

echo "🛰️ Launching poly_scanner..."
nohup python -u poly_scanner.py >> {LOG_DIR}/poly_scanner.log 2>&1 &
sleep 8

echo "🛰️ Launching poly_prices..."
nohup python -u poly_prices.py >> {LOG_DIR}/poly_prices.log 2>&1 &
sleep 5

echo "🛰️ Launching poly_indicators..."
nohup python -u poly_indicators.py >> {LOG_DIR}/poly_indicators.log 2>&1 &
sleep 5

echo "🛰️ Launching poly_manage..."
nohup python -u poly_manage.py >> {LOG_DIR}/poly_manage.log 2>&1 &
echo "✅ All processes launched."
"""


    print("🛰️ Executing remote launch sequence...")
    try:
        # We pipe the script string into 'bash' on the remote server
        # This is much more reliable than trying to pass it as a command line argument
        final_cmd = f"ssh {SSH_OPTS} {SERVER_USER}@{SERVER_IP} 'bash -s' << 'EOF'\n{remote_script}\nEOF"
        run_local(final_cmd)
        print(f"\n✨ System Online at {SERVER_IP}.")
    except Exception as e:
        print(f"\n❌ Remote execution failed.")
        print(f"Error details: {e}")

if __name__ == "__main__":
    push_and_launch()