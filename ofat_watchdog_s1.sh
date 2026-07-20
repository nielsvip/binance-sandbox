#!/bin/bash
# ofat_watchdog_s1.sh — keep crypto+tradier OFAT param screens alive, resumable, OOM-gated.
# Live trading runs on the MacBook ONLY; this box does sweeps. Each runner mem-gates
# internally (free_mb<min_avail -> wait) and marks its workers oom_score_adj=900 so a
# memory spike sacrifices a backtest worker, never anything important.
SBX=/home/niels/binance-sandbox
PY=/home/niels/.conda/envs/binance_env/bin/python
LOGS=/home/niels/logs
mkdir -p "$LOGS"
avail=$(free -m | awk '/Mem:/{print $7}')
launch(){
  mode=$1; acct=$2; par=$3; start=$4
  if pgrep -f "engine_ofat_screen.py --mode $mode" >/dev/null; then
    echo "$(date -u +%FT%TZ) $mode already running"; return; fi
  if [ "${avail:-0}" -lt 5000 ]; then
    echo "$(date -u +%FT%TZ) low mem ${avail}MB -> skip $mode"; return; fi
  cd "$SBX" || return
  setsid nohup "$PY" -u engine_ofat_screen.py --mode "$mode" --account "$acct" \
    --syms-file "$SBX/data/ofat_syms_${mode}.txt" --syms 16 --max-par "$par" --min-avail 6000 \
    --start "$start" \
    >> "$LOGS/ofat_${mode}.log" 2>&1 < /dev/null &
  disown
  echo "$(date -u +%FT%TZ) launched $mode par=$par start=$start avail=${avail}MB"
}
# representative window: 2024-01-01 for BOTH (diagnostic ranking screen; winners are
# Tier-2-reconfirmed on full floor + full history before any promotion). Crypto 4yr was
# ~28min/sym = impractical; 2.5yr keeps it representative and tractable. 16 cores avail.
launch crypto ang 6 2024-01-01
launch tradier trb 4 2024-01-01
