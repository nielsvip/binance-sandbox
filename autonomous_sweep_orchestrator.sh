#!/bin/bash
# autonomous_sweep_orchestrator.sh — chain reentry sweeps, log summary, stop at deadline.
# Runs ON S1. Independent of agent session.
#
# Usage: ./autonomous_sweep_orchestrator.sh
# Logs: /tmp/autonomous_orchestrator.log
# Summary: /home/niels/binance-sandbox/data/sweep_results/AUTONOMOUS_SUMMARY_<start>.md

set -u
SANDBOX="/home/niels/binance-sandbox"
PY="/home/niels/.conda/envs/binance_env/bin/python"
RESULTS_DIR="${SANDBOX}/data/sweep_results"
LOG="/tmp/autonomous_orchestrator.log"
START_TS=$(date -u +%Y%m%d_%H%M%S)
SUMMARY="${RESULTS_DIR}/AUTONOMOUS_SUMMARY_${START_TS}.md"

# DEADLINE: Monday 2026-04-20 06:00 UTC (= Sunday 02:00 ET)
DEADLINE_EPOCH=$(date -u -d "2026-04-20 06:00:00" +%s)

cd "${SANDBOX}" || { echo "cannot cd to sandbox" >> "${LOG}"; exit 1; }

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "${LOG}"; }

initialize_summary() {
  cat > "${SUMMARY}" <<EOF
# Autonomous Sweep Orchestrator — ${START_TS}

**Deadline**: Monday 2026-04-20 06:00 UTC (stops automatically)
**Log**: \`${LOG}\`
**Sandbox**: \`${SANDBOX}\`

## Plan

1. Finish any currently-running sweep (poll until no backtest_v8 processes).
2. Run **reentry_all_real** tier (37 variants × 4 sym × 4wk, workers=1, timeout=1500).
3. Run **hedge_reentry_ablation** tier (15 variants × 4 sym × 4wk).
4. Run **reentry_optimize** tier (44 variants × 8 sym × 6wk) if deadline allows.
5. Loop: if time remains, re-run reentry_all_real on 8 syms × 6wk for wider confirmation.
6. After each sweep, append top-5 rows (by sharpe_w) to this summary.

## Sweep Results

EOF
}

check_deadline() {
  local now
  now=$(date -u +%s)
  if [ "${now}" -ge "${DEADLINE_EPOCH}" ]; then
    log "[DEADLINE] reached ${DEADLINE_EPOCH}, stopping."
    echo "" >> "${SUMMARY}"
    echo "## DEADLINE REACHED — stopped at $(date -u +%FT%TZ)" >> "${SUMMARY}"
    exit 0
  fi
}

STUCK_TIMEOUT=1800  # 30 min — kill any engine/sweep process running longer than this

wait_for_quiet() {
  # Wait until no backtest_v8_engine / v8_quick_sweep processes remain.
  # If any process has been running >STUCK_TIMEOUT seconds with no new CSV output, kill it.
  local waited=0
  while pgrep -f "backtest_v8_engine.py" > /dev/null 2>&1 \
        || pgrep -f "backtest_v8_sweep.py" > /dev/null 2>&1 \
        || pgrep -f "v8_quick_sweep.py" > /dev/null 2>&1; do
    check_deadline
    sleep 30
    waited=$((waited + 30))
    if [ $((waited % 300)) -eq 0 ]; then
      log "[WAIT] still waiting for quiet, ${waited}s elapsed"
    fi
    # Kill any stuck processes: running >STUCK_TIMEOUT with no recent CSV output
    local last_csv_age=9999
    local newest_csv
    newest_csv=$(ls -t "${RESULTS_DIR}"/*.csv 2>/dev/null | head -1)
    if [ -n "${newest_csv}" ]; then
      local csv_mtime now
      csv_mtime=$(stat -c %Y "${newest_csv}" 2>/dev/null || echo 0)
      now=$(date +%s)
      last_csv_age=$(( now - csv_mtime ))
    fi
    if [ "${last_csv_age}" -gt "${STUCK_TIMEOUT}" ]; then
      # Find processes running longer than STUCK_TIMEOUT
      local stuck_pids
      stuck_pids=$(ps -eo pid,etimes,cmd | awk -v t="${STUCK_TIMEOUT}" '
        /backtest_v8_engine\.py|backtest_v8_sweep\.py|v8_quick_sweep\.py/ &&
        !/awk/ { if ($2 > t) print $1 }')
      if [ -n "${stuck_pids}" ]; then
        log "[STUCK] no CSV output for ${last_csv_age}s, killing PIDs: ${stuck_pids}"
        echo "${stuck_pids}" | xargs kill -9 2>/dev/null
        sleep 5
        waited=0  # reset wait counter after kill
      fi
    fi
  done
  rm -f /home/niels/SWEEP_RUNNING
  log "[QUIET] no backtest processes running"
}

check_resources() {
  # Block sweep launch if CPU or memory > 80%
  local mem_pct cpu_pct
  mem_pct=$(free | awk '/Mem:/ {printf "%.0f", $3*100/$2}')
  cpu_pct=$(top -bn1 | grep '%Cpu' | awk '{print int(100-$8)}' 2>/dev/null || echo 0)
  local waited=0
  while [ "${mem_pct}" -gt 80 ] || [ "${cpu_pct}" -gt 80 ]; do
    log "[RESOURCE] mem=${mem_pct}% cpu=${cpu_pct}% — waiting before next sweep launch"
    sleep 60
    waited=$((waited + 60))
    mem_pct=$(free | awk '/Mem:/ {printf "%.0f", $3*100/$2}')
    cpu_pct=$(top -bn1 | grep '%Cpu' | awk '{print int(100-$8)}' 2>/dev/null || echo 0)
    if [ "${waited}" -gt 3600 ]; then
      log "[RESOURCE] waited 60min for resources, proceeding anyway"
      break
    fi
  done
}

append_top5() {
  local csv="$1"
  local label="$2"
  if [ ! -f "${csv}" ]; then
    log "[APPEND_TOP5] CSV not found: ${csv}"
    return
  fi
  {
    echo ""
    echo "### ${label} — $(date -u +%FT%TZ)"
    echo "CSV: \`${csv}\`"
    echo ""
    echo '```'
    echo "Top 5 by sharpe_w:"
    awk -F, 'NR>1 && $3=="ok" {print}' "${csv}" | sort -t, -k4 -nr | head -5 \
      | awk -F, '{printf "%-40s sharpe=%s closes=%s wr=%s%%\n", $1, $4, $8, $11}'
    echo ""
    echo "Baseline:"
    awk -F, 'NR>1 && $1=="baseline"' "${csv}" \
      | awk -F, '{printf "%-40s sharpe=%s closes=%s wr=%s%%\n", $1, $4, $8, $11}'
    echo '```'
  } >> "${SUMMARY}"
}

run_sweep() {
  local tier="$1"
  local symbols="$2"
  local start="$3"
  local workers="$4"
  local timeout_s="$5"
  local label="$6"

  check_deadline
  wait_for_quiet
  check_resources

  local stamp
  stamp=$(date -u +%Y%m%d_%H%M%S)
  local run_log="/tmp/orch_${tier}_${stamp}.log"
  log "[LAUNCH] tier=${tier} symbols=${symbols} start=${start} workers=${workers} timeout=${timeout_s} label=${label}"
  echo "REENTRY_${tier^^}_${stamp}" > /home/niels/SWEEP_RUNNING

  "${PY}" -u "${SANDBOX}/backtest_v8_sweep.py" \
    --mode crypto --account ang \
    --start "${start}" --symbols "${symbols}" \
    --tier "${tier}" --workers "${workers}" --timeout "${timeout_s}" \
    > "${run_log}" 2>&1 &
  local pid=$!
  log "[LAUNCH] pid=${pid} log=${run_log}"

  # Poll until process exits, deadline, or sweep-level stuck timeout (2× STUCK_TIMEOUT)
  local sweep_waited=0
  local sweep_timeout=$(( STUCK_TIMEOUT * 2 ))
  while kill -0 "${pid}" 2>/dev/null; do
    check_deadline
    sleep 60
    sweep_waited=$(( sweep_waited + 60 ))
    if [ "${sweep_waited}" -ge "${sweep_timeout}" ]; then
      log "[SWEEP_TIMEOUT] tier=${tier} pid=${pid} ran ${sweep_waited}s — killing"
      kill -9 "${pid}" 2>/dev/null
      break
    fi
  done

  wait "${pid}" 2>/dev/null
  local rc=$?
  log "[DONE] tier=${tier} rc=${rc}"
  rm -f /home/niels/SWEEP_RUNNING

  # Find most recent CSV for this tier and append top5
  local csv
  csv=$(ls -t "${RESULTS_DIR}/backtest_v8_sweep_${tier}_"*.csv 2>/dev/null | head -1)
  if [ -n "${csv}" ]; then
    append_top5 "${csv}" "${label}"
  else
    log "[NO_CSV] tier=${tier} produced no CSV"
  fi
}

# === MAIN ===
initialize_summary
log "[START] autonomous orchestrator. deadline=$(date -u -d @${DEADLINE_EPOCH} +%FT%TZ)"

# Wait for currently-running optimize sweep
wait_for_quiet

# Step 1: reentry_all_real on 4-sym × 4wk
run_sweep "reentry_all_real" \
  "BTCUSDC,ETHUSDC,SOLUSDC,LINKUSDC" \
  "2026-03-20" \
  "1" "1500" \
  "Step 1 — reentry_all_real 4sym × 4wk"

# Step 2: hedge_reentry_ablation 4-sym × 4wk
run_sweep "hedge_reentry_ablation" \
  "BTCUSDC,ETHUSDC,SOLUSDC,LINKUSDC" \
  "2026-03-20" \
  "1" "1500" \
  "Step 2 — hedge_reentry_ablation 4sym × 4wk"

# Step 3: reentry_all_real WIDER (8 sym × 6wk) — statistical power
run_sweep "reentry_all_real" \
  "BTCUSDC,ETHUSDC,SOLUSDC,LINKUSDC,DOTUSDT,UNIUSDC,AVAXUSDC,BNBUSDC" \
  "2026-03-01" \
  "1" "2000" \
  "Step 3 — reentry_all_real 8sym × 6wk (wider)"

# Step 4: reentry_optimize on 8 sym × 6 wk — parameter tuning
run_sweep "reentry_optimize" \
  "BTCUSDC,ETHUSDC,SOLUSDC,LINKUSDC,DOTUSDT,UNIUSDC,AVAXUSDC,BNBUSDC" \
  "2026-03-01" \
  "1" "2000" \
  "Step 4 — reentry_optimize 8sym × 6wk"

# Step 5: if time remains, loop reentry_all_real on 12 sym × 8wk
while true; do
  check_deadline
  run_sweep "reentry_all_real" \
    "BTCUSDC,ETHUSDC,SOLUSDC,LINKUSDC,DOTUSDT,UNIUSDC,AVAXUSDC,BNBUSDC,ADAUSDC,XRPUSDC,LTCUSDC,MATICUSDT" \
    "2026-02-15" \
    "1" "3000" \
    "Step 5+ — reentry_all_real 12sym × 8wk (loop)"
done

log "[DONE] autonomous orchestrator finished"
