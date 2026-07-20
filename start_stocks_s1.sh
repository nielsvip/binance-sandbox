#!/bin/bash
# S1 stock launcher/watchdog (2026-06-04 cutover) — premarket start at 13:00 UTC (30m before 13:30 open).
# Starts tradier feeds + tradier_manage trb/trc. tradier_manage self-gates market hours (idles when closed).
# Idempotent: only starts what is down — safe to run as both the 13:00 starter AND an every-few-min watchdog.
# Mac stocks are decommissioned; S1 is the sole live owner.
cd /home/niels/binance || exit 1
PY=/home/niels/.conda/envs/binance_env/bin/python
LOG=/home/niels/logs
up(){ pgrep -f "$1" >/dev/null 2>&1; }
ts(){ date -u +%FT%TZ; }
started_feed=0
up "tradier_prices.py"     || { setsid nohup $PY tradier_prices.py     >> "$LOG/live_tradier_prices.log" 2>&1 </dev/null & disown; started_feed=1; }
up "tradier_indicators.py" || { setsid nohup $PY tradier_indicators.py >> "$LOG/live_tradier_indicators.log" 2>&1 </dev/null & disown; started_feed=1; }
up "tradier_rankings.py"   || { setsid nohup $PY tradier_rankings.py   >> "$LOG/live_tradier_rankings.log" 2>&1 </dev/null & disown; started_feed=1; }
up "tradier_positions.py"  || { setsid nohup $PY tradier_positions.py --accounts trb trc >> "$LOG/live_tradier_positions.log" 2>&1 </dev/null & disown; started_feed=1; }
[ "$started_feed" = "1" ] && sleep 40
up "tradier_manage.py --accounts trb" || { setsid nohup $PY tradier_manage.py --accounts trb >> "$LOG/live_tradier_trb.log" 2>&1 </dev/null & disown; echo "[$(ts)] started trb" >> "$LOG/start_stocks_s1.log"; }
up "tradier_manage.py --accounts trc" || { setsid nohup $PY tradier_manage.py --accounts trc >> "$LOG/live_tradier_trc.log" 2>&1 </dev/null & disown; echo "[$(ts)] started trc" >> "$LOG/start_stocks_s1.log"; }
