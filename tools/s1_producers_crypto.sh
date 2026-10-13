#!/bin/bash
# LIVE FAILOVER S1 crypto producers (ExecStart of live-failover-crypto-producers.service, on demand,
# started by tools/live_failover_s1.py when the Mac's crypto data is >180 s stale). The crypto
# producers ARE the isolated ez_s1_stack (ez_klines/ez_prices/ez_mark_prices/ez_prices_ws/
# ez_indicators/ez_indicators_merger, own Redis, S1 egress IP); this loop guarantees they run
# (the EZ_S1_STACK cron does the same every 2 min). Outputs are delivered by the watchdog outbox.
set -u
while true; do
    /bin/bash /home/niels/binance/ez_s1_stack/ez_s1_stack_health.sh >> /home/niels/binance/logs/ez_s1_stack_health.log 2>&1
    sleep 30
done
