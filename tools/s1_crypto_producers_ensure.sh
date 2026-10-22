#!/bin/bash
# runs ON S1 (cron */1). USER 2026-10-22: "Run ez_indicators on S1 24/7 (two sources), the S1 version must NEVER restart, just run."
# The crypto producers live in live-failover-crypto-producers.service (ez_s1_stack). tools/live_failover_s1.py used to
# `systemctl stop` that unit whenever the Mac had been fresh for RECOVER_S, so every Mac recovery threw S1's warm
# producers away and the NEXT Mac blackout had to wait out a cold warm-up (the 2026-10-22 00:40-04:50Z outage was exactly
# that: both sources cold at the same time). The drop-in live-failover-crypto-producers.service.d/10-always-on.conf now
# sets RefuseManualStop=yes (the agent's stop is refused, the processes keep running). This script is the other half:
# if the unit is not active for any reason (reboot, crash storm, someone removed the drop-in and stopped it) start it.
# Kill switch: touch ~/binance/ez_s1_stack/DISABLED (the stack's own health script honours the same file).
set -u
UNIT=live-failover-crypto-producers.service
STACK=/home/niels/binance/ez_s1_stack
LOG=/home/niels/logs/s1_crypto_producers_ensure.log
[ -f "$STACK/DISABLED" ] && exit 0
systemctl is-active --quiet "$UNIT" && exit 0
rc=$(sudo -n systemctl start "$UNIT" >/dev/null 2>&1; echo $?)
echo "[$(date -u +%FT%TZ)] $UNIT was NOT active -> systemctl start rc=$rc" >> "$LOG"
exit 0
