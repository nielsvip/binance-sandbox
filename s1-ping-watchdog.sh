#!/bin/bash
# S1 ping watchdog on gateway 157.90.168.35 -> reboots S1 at 10.0.0.3 if stalls/OOMs/crashes
# Reliable: ping + ssh health, 3 consecutive failures -> reboot. Timer-friendly + loop fallback.
set -euo pipefail
S1=10.0.0.3
S1_USER=niels
THRESH=3
INTERVAL=30
PING_COUNT=2
PING_TIMEOUT=3
SSH_TIMEOUT=5
FAIL_FILE=/var/tmp/s1-watchdog.failcount
LOG_TAG=s1-ping-watchdog
log() { logger -t "$LOG_TAG" "$*"; echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $LOG_TAG: $*" >&2; }
write_fail() {
  # try direct, then sudo, then chmod route
  echo "$1" > "$FAIL_FILE" 2>/dev/null || echo "$1" | sudo tee "$FAIL_FILE" >/dev/null 2>&1 || sudo bash -c "echo $1 > $FAIL_FILE; chmod 666 $FAIL_FILE" 2>/dev/null || true
  sudo chmod 666 "$FAIL_FILE" 2>/dev/null || true
}
do_one_check() {
  local OK=0
  if ping -c "$PING_COUNT" -W "$PING_TIMEOUT" "$S1" >/dev/null 2>&1; then
    if ssh -o ConnectTimeout="$SSH_TIMEOUT" -o StrictHostKeyChecking=no -o BatchMode=yes "${S1_USER}@${S1}" 'echo ok' >/dev/null 2>&1; then
      OK=1
    else
      log "S1 $S1 ping ok but ssh health failed (OOM/crash/stall)"
    fi
  else
    log "S1 $S1 ping failed"
  fi
  local FAIL=0
  if [[ -f "$FAIL_FILE" ]]; then
    FAIL=$(cat "$FAIL_FILE" 2>/dev/null || echo 0)
    [[ "$FAIL" =~ ^[0-9]+$ ]] || FAIL=0
  fi
  if [[ $OK -eq 1 ]]; then
    if [[ $FAIL -ne 0 ]]; then
      log "S1 $S1 recovered, resetting fail count $FAIL -> 0"
    fi
    write_fail 0
    return 0
  else
    FAIL=$((FAIL+1))
    write_fail "$FAIL"
    log "S1 unreachable $FAIL/$THRESH"
  fi
  if [[ $FAIL -ge $THRESH ]]; then
    log "REBOOTING S1 $S1 after $FAIL failures (stall/OOM/crash)"
    ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "${S1_USER}@${S1}" 'sudo -n reboot' 2>&1 || true
    sleep 30
    sleep 60
    if ! ping -c2 -W3 "$S1" >/dev/null 2>&1; then
      log "S1 still down after soft reboot, hard reset required (configure Hetzner API/IPMI)"
    else
      log "S1 $S1 back online after reboot"
    fi
    write_fail 0
    if [[ "${1:-}" != "--no-sleep" ]]; then
      sleep 30
    fi
  fi
}
if [[ "${1:-}" == "--loop" ]]; then
  log "starting loop watchdog S1=$S1 thresh=$THRESH interval=${INTERVAL}s"
  while true; do
    do_one_check --no-sleep
    sleep "$INTERVAL"
  done
else
  do_one_check
fi
