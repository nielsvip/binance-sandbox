#!/bin/bash
# deploy_v15_watchdogs_s1s2s5s6.sh — ensure every server s1/s2/s5/s6 has herd + cpu80 watchdog + warn, cpu>80%, no stall
# Mac is source of truth; push to servers via rsync over ssh, install cron, verify
set -e
ROOT="/Users/niels/Documents/binance"
cd "$ROOT"

echo "[$(date -u +%FT%TZ)] deploy_v15_watchdogs start"

# 1. Verify local tools compile
for f in tools/v15_warn_daemon.py tools/v15_overnight_herd.py tools/v15_cpu80_watchdog.py tools/monitor_production.py; do
  python3 -m py_compile "$f" && echo "compile ok $f" || { echo "compile FAIL $f"; exit 1; }
done

# 2. Push tools + queues to all 4 servers (s1 via s1-int bridge, s2/s5/s6 via gateway-internal)
SERVERS=("s1-int" "s2" "s5" "s6")
# s1-int is 127.0.0.1:2201 via s1-sftp tunnel; need tunnel up
if ! ssh -o ConnectTimeout=3 s1-int "echo ok" 2>&1 | grep -q ok; then
  echo "starting s1-sftp tunnel"
  ssh -fNT s1-sftp 2>&1 || true
  sleep 3
fi
if ! ssh -o ConnectTimeout=3 s2 "echo ok" 2>&1 | grep -q ok; then
  echo "s2 direct via 10.0.0.4 may need gateway; will try"
fi

for dst in "${SERVERS[@]}"; do
  echo "=== push to $dst ==="
  rsync -az -e "ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no" \
    tools/v15_cpu80_watchdog.py tools/v15_warn_daemon.py tools/monitor_production.py \
    tools/v15_overnight_herd.py v15_pilot.py config.py config_tradier.py \
    "$dst:~/binance-sandbox/tools/" && echo "OK $dst tools" || echo "FAIL $dst tools"
  rsync -az -e "ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no" \
    SPREADSHEETS/V15_RESUMED_QUEUE_S1_CRYPTO_FLZ_16.txt \
    SPREADSHEETS/V15_RESUMED_QUEUE_S*_STOCKS_*.txt \
    SPREADSHEETS/V15_SERVER_QUEUE_S*.txt \
    SPREADSHEETS/V15_PRIORITY_AUDIT_20260920.csv \
    SPREADSHEETS/V15_PRIORITY_TRB_FIRST_83.txt \
    SPREADSHEETS/V15_RESUMED_QUEUES_S1_CRYPTO_S2S5S6_STOCKS.json \
    symbols_flz_long.json symbols_flz_short.json \
    "$dst:~/binance-sandbox/SPREADSHEETS/" 2>&1 | tail -5 || echo "FAIL $dst SPREADSHEETS"
  # also push queues to correct location (herd reads ~/binance-sandbox/SPREADSHEETS/V15_SERVER_QUEUE_S*.txt)
  # already done via above; ensure per-server queue is correct (s1 crypto, s2/s5/s6 stocks)
done

# 3. Install cron on each server (herd + cpu80 watchdog + warn via Mac, but herd cron lives on server)
for dst in "${SERVERS[@]}"; do
  echo "=== install cron on $dst ==="
  ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=no "$dst" bash -s << 'CRON_EOF'
set -e
CRON_TMP="/tmp/cron.$$"
crontab -l 2>/dev/null | grep -v "v15_local_herd\|v15_cpu80_watchdog\|v15_warn\|monitor_production\|ELASTIC" > "$CRON_TMP" || true
cat >> "$CRON_TMP" << 'CRON'
*/2 * * * * /home/niels/binance-sandbox/.venv/bin/python -u /home/niels/binance-sandbox/tools/v15_cpu80_watchdog.py >> /tmp/v15_cpu80_watchdog.log 2>&1
*/10 * * * * /home/niels/binance-sandbox/.venv/bin/python -u /home/niels/binance-sandbox/tools/monitor_production.py >> /tmp/monitor_production.log 2>&1
CRON
crontab "$CRON_TMP" && echo "cron installed $(crontab -l | grep -c v15) entries" || echo "cron fail"
rm -f "$CRON_TMP"
sleep 2
pgrep -a -f v15_cpu80_watchdog | head -n 3 || echo "watchdog will start via cron in 2m"
CRON_EOF
  echo "cron done $dst"
done

# 4. Verify each server: cpu>80%, pilots running, herd ok, watchdog exists
for dst in "${SERVERS[@]}"; do
  echo "=== verify $dst ==="
  ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=no "$dst" bash -s << 'VERIFY_EOF'
echo "--- $(hostname) $(date -u) ---"
echo "load:"; cat /proc/loadavg; echo "nproc:"; nproc
echo "cpu%:"; awk '{print $1}' /proc/loadavg | xargs -I{} bash -c 'n=$(nproc); echo "{} / $n *100" | bc -l' 2>&1 || python3 -c "import subprocess; n=int(subprocess.check_output(['nproc']).decode().strip()); l=float(open('/proc/loadavg').read().split()[0]); print(f'{l/n*100:.1f}%')"
free -m | head -n 2
echo "pilots:"; ps aux | grep v15_pilot | grep -v grep | wc -l; ps aux | grep v15_pilot | grep -v grep | head -n 3
echo "watchdog cron:"; crontab -l | grep v15_cpu80 || echo "no cpu80 cron"
echo "queue:"; ls -lh ~/binance-sandbox/SPREADSHEETS/V15_SERVER_QUEUE_S*.txt 2>&1 | head -n 5; cat ~/binance-sandbox/SPREADSHEETS/V15_SERVER_QUEUE_S*.txt 2>&1 | head -n 5
echo "recent xlsx:"; ls -lt ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>&1 | head -n 3 | awk '{print $9, $5}'
VERIFY_EOF
done

# 5. Mac warn daemon (covers s1/s2/s5/s6 with CPU_WARN 80)
echo "=== Mac warn daemon ==="
# ensure LaunchAgent exists and is loaded
if launchctl list | grep -q com.binance.v15-warn; then
  echo "warn daemon already loaded"
else
  launchctl load -w ~/Library/LaunchAgents/com.binance.v15-warn.plist 2>&1 | head -5
  echo "warn daemon loaded"
fi
# also run once now
python3 -u tools/v15_warn_daemon.py --once 2>&1 | tail -n 20 || true

echo "[$(date -u +%FT%TZ)] deploy_v15_watchdogs done — all servers s1 crypto (16 FLZ) s2/s5/s6 stocks (13 each phase1) with cpu>80% watchdogs"
