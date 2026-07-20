#!/bin/bash
LOGS=/home/niels/logs
PY=/home/niels/.conda/envs/binance_env/bin/python
SANDBOX=/home/niels/binance-sandbox
TS=$(date -u +%Y%m%d_%H%M%S)
LOG=$LOGS/wtp3_${TS}.log

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }
wait_mem() {
  local target_mb=${1:-8000}
  local avail
  while true; do
    avail=$(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
    if [ "$avail" -ge "$target_mb" ]; then log "Memory OK: ${avail}MB"; break; fi
    log "Waiting mem: ${avail}MB < ${target_mb}MB needed"
    sleep 30
  done
}
set_oom() { echo 900 | sudo -n tee /proc/$1/oom_score_adj > /dev/null 2>&1 || true; }
run_step() {
  local label=$1; shift
  local lf=$LOGS/wtp3_${label}_${TS}.log
  log "START: $label => $*"
  "$@" > "$lf" 2>&1 &
  local pid=$!
  set_oom $pid
  log "$label PID=$pid log=$lf"
  wait $pid
  local rc=$?
  log "DONE: $label rc=$rc"
  return $rc
}

log "=== WTP3 START $(hostname) ==="
log "Memory: $(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo)MB available"

log "STEP1: pause watchdog_sweep cron"
(crontab -l 2>/dev/null | sed 's|^\(\*/5 \* \* \* \* /bin/bash \$SANDBOX/watchdog_sweep_s1\.sh\)$|#WTP3_PAUSED \1|') | crontab - && log "cron paused" || log "WARN cron pause failed"

log "STEP2: per_sym trb"
wait_mem 6000
run_step persym_trb "$PY" "$SANDBOX/per_sym_20d_agent_stocks.py" --account trb --workers 2 || log "WARN trb non-zero"

log "STEP3: per_sym trc"
wait_mem 6000
run_step persym_trc "$PY" "$SANDBOX/per_sym_20d_agent_stocks.py" --account trc --workers 2 || log "WARN trc non-zero"

log "STEP4: tradier sector_baseline 2024-01-01 w=1"
wait_mem 10000
run_step tradier_sector_baseline "$PY" "$SANDBOX/backtest_v8_sweep.py" --mode tradier --tier tradier_sector_baseline --start 2024-01-01 --workers 1 || log "WARN sector_baseline non-zero"

log "STEP5: tradier param_hunt 2024-01-01 w=1"
wait_mem 10000
run_step tradier_param_hunt "$PY" "$SANDBOX/backtest_v8_sweep.py" --mode tradier --tier tradier_param_hunt --start 2024-01-01 --workers 1 || log "WARN param_hunt non-zero"

log "STEP6: crypto sector_baseline 2024-01-01 w=2"
wait_mem 8000
run_step crypto_baseline "$PY" "$SANDBOX/backtest_v8_sweep.py" --mode crypto --tier crypto_sector_baseline --start 2024-01-01 --workers 2 || log "WARN crypto_baseline non-zero"

log "STEP7: crypto system_combo 2024-01-01 w=2"
wait_mem 8000
run_step crypto_system_combo "$PY" "$SANDBOX/backtest_v8_sweep.py" --mode crypto --tier system_combo --start 2024-01-01 --workers 2 || log "WARN system_combo non-zero"

log "STEP8: re-enable watchdog cron"
(crontab -l 2>/dev/null | sed 's|^#WTP3_PAUSED \(.*watchdog_sweep_s1\.sh\)$|\1|') | crontab - && log "cron re-enabled" || log "WARN cron re-enable failed"

log "=== WTP3 COMPLETE ==="
