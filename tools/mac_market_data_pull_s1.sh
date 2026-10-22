#!/bin/bash
# mac_market_data_pull_s1.sh - cron wrapper (marker MAC_MD_PULL_S1, every minute) for tools/mac_market_data_pull.py.
#
# USER 2026-10-22: "no market_data file pushed to s1 starts ez_indicators/klines/prices/mark_prices/prices_ws on s1
# IMMEDIATELY and syncs to macbook so it never has a >90s market data file NO MATTER WHAT THE FUCK HAPPENS."
#
# 2026-10-22 REWRITE (the first version pulled nothing, measured): it fetched S1's ~/binance/data/latest_market_data.json,
# but S1's producer writes ~/binance/ez_s1_stack/data/latest_market_data.json - the former was 00:49Z, 4 h old, "not
# fresher", so every round ended in an ALERT while a 17 s old snapshot sat one directory over. It also only knew
# latest_market_data.json, while ez_manage (the engines) read the newest-mtime data/market_data_*.json. The logic now lives
# in mac_market_data_pull.py (sources S1 stack + S2 stack, install as market_data_<t>_s1.json + latest_market_data.json,
# explicit never-older compare, content gate, alerts). This wrapper only serialises runs: ~55 s of 5 s rounds per minute.
set -u
BASE="/Users/niels/Documents/binance"
PY="/opt/anaconda3/envs/binance_env/bin/python"
LOCK=/tmp/.mac_market_data_pull.lock
/usr/bin/shlock -f "$LOCK" -p $$ || exit 0
trap 'rm -f "$LOCK"' EXIT INT TERM
cd "$BASE" || exit 1
exec_out=$("$PY" -u "$BASE/tools/mac_market_data_pull.py" 2>&1); rc=$?
[ -n "$exec_out" ] && echo "[$(date -u +%FT%TZ)] pull.py rc=$rc output: $exec_out" >> /Users/niels/logs/mac_market_data_pull_s1.log
exit 0
