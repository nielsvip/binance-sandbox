import subprocess
import os

# --- CONFIGURATION ---
REMOTE_HOST = "niels@157.180.125.52"
SERVICE_NAME = "binance-market-data.service" # Naming it this so deploy.py auto-restarts it
REMOTE_PATH = f"/home/niels/.config/systemd/user/{SERVICE_NAME}"

# --- THE SERVICE FILE CONTENT ---
# Adapted from your ez_klines example
SERVICE_CONTENT = """[Unit]
Description=Binance Bot - Market Data Producer (L1/L2 Cache)
After=network.target redis.service
Wants=redis.service

[Service]
Type=simple
WorkingDirectory=/home/niels/binance
# Using the wrapper to handle logging/envs setup
ExecStart=/home/niels/binance/run_with_watchdog_LINUX.sh ez_market_data.py
Restart=always
RestartSec=5s
StandardOutput=journal
StandardError=journal

# Environment Variables
Environment="HOME=/home/niels"
Environment=PYTHONUNBUFFERED=1
Environment="PATH=/home/niels/miniforge3/envs/binance_env/bin:/usr/bin:/usr/local/bin:/bin"

# Security / Protection
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/home/niels/logs /home/niels/binance

[Install]
WantedBy=default.target
"""

def run_cmd(cmd):
    print(f"Executing: {cmd}")
    subprocess.check_call(cmd, shell=True)

def main():
    print(f"--- Installing {SERVICE_NAME} to {REMOTE_HOST} ---")

    # 1. Write content to a local temporary file
    local_file = "temp_service_file.service"
    with open(local_file, "w") as f:
        f.write(SERVICE_CONTENT)

    try:
        # 2. Ensure the user systemd directory exists
        run_cmd(f"ssh {REMOTE_HOST} 'mkdir -p /home/niels/.config/systemd/user/'")

        # 3. Upload the file
        print("Uploading service file...")
        run_cmd(f"scp {local_file} {REMOTE_HOST}:{REMOTE_PATH}")

        # 4. Reload Systemd (User level)
        print("Reloading systemd daemon...")
        run_cmd(f"ssh {REMOTE_HOST} 'systemctl --user daemon-reload'")

        # 5. Enable and Start
        print("Enabling and starting service...")
        run_cmd(f"ssh {REMOTE_HOST} 'systemctl --user enable {SERVICE_NAME}'")
        run_cmd(f"ssh {REMOTE_HOST} 'systemctl --user restart {SERVICE_NAME}'")

        # 6. Check Status
        print("--- STATUS CHECK ---")
        subprocess.run(f"ssh {REMOTE_HOST} 'systemctl --user status {SERVICE_NAME} --no-pager'", shell=True)

    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        # Cleanup local temp file
        if os.path.exists(local_file):
            os.remove(local_file)

if __name__ == "__main__":
    main()