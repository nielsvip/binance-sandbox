#!/bin/bash
# v15_overnight_monitor.sh — keep s1/s2/s3/s5 saturated overnight for V15 104 sym_sides
# - Ensures herder is running, s1 tunnel up, and each server at >95% CPU / >80% RAM / no OOM
# - Companion to tools/v15_overnight_herd.py — this script is the "no-pausing" watchdog
# - Run from MacBook:  nohup bash tools/v15_overnight_monitor.sh > /tmp/herd_watchdog.log 2>&1 &
#
# What it does every 45s:
#   1) Ensure s1-sftp tunnel (s1-int) is up, else re-establish
#   2) Ensure herd.py is running (restarts if dead)
#   3) For each server (s1/s2/s3/s5): log CPU%/RAM%/load/OOM, warn if <95% CPU or <80% RAM
#   4) Pull completed sheets back to Mac every 5min via sync_s1_to_mac.sh
#   5) Never pauses the herd — if a pilot dies, herd requeues; this watchdog just restarts herd if needed

set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORDER="SPREADSHEETS/V15_RUNNING_ORDER_TRB_FLZ.txt"
HERD_LOG="/tmp/herd.log"
WATCH_LOG="/tmp/herd_watchdog.log"
SYNC_INTERVAL_SEC=300
LAST_SYNC=0

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$WATCH_LOG"; }

ensure_s1_tunnel() {
  if ssh -o ConnectTimeout=5 -o BatchMode=yes s1-int "echo ok" 2>/dev/null | grep -q ok; then
    return 0
  fi
  log "WATCH s1-int down — bootstrapping ssh -fNT s1-sftp"
  ssh -fNT s1-sftp 2>&1 | tee -a "$WATCH_LOG" || true
  sleep 2
  if ssh -o ConnectTimeout=5 s1-int "echo ok" 2>/dev/null | grep -q ok; then
    log "WATCH s1-int recovered via s1-sftp"
  else
    log "WATCH s1-int still down — herd will use s1-pub fallback"
  fi
}

ensure_herd() {
  if pgrep -f "v15_overnight_herd" >/dev/null 2>&1; then
    return 0
  fi
  log "WATCH herd not running — starting: python3 -u tools/v15_overnight_herd.py"
  nohup python3 -u "$ROOT/tools/v15_overnight_herd.py" > "$HERD_LOG" 2>&1 &
  sleep 3
  if pgrep -f "v15_overnight_herd" >/dev/null 2>&1; then
    log "WATCH herd started pid=$(pgrep -f "v15_overnight_herd" | head -1)"
  else
    log "WATCH herd FAILED to start — see $HERD_LOG"
    tail -n 50 "$HERD_LOG" | tee -a "$WATCH_LOG"
  fi
}

poll_server() {
  local alias="$1" nproc="$2"
  local host
  case "$alias" in
    s1) host="s1-int" ;;
    s2) host="s2" ;;
    s3) host="s3" ;;
    s5) host="s5" ;;
  esac
  # try fallback hosts
  local out rc
  out=$(ssh -o ConnectTimeout=8 "$host" "cat /proc/loadavg; echo ---; free -m; echo ---; ps aux | grep -c '[v]15_pilot'" 2>&1)
  rc=$?
  if [[ $rc -ne 0 ]]; then
    # try pub
    case "$alias" in
      s1) host="s1-pub" ;;
      s2) host="s2-pub" ;;
      s3) host="s3-pub" ;;
      s5) host="s5-pub" ;;
    esac
    out=$(ssh -o ConnectTimeout=8 "$host" "cat /proc/loadavg; echo ---; free -m; echo ---; ps aux | grep -c '[v]15_pilot'" 2>&1) || true
  fi
  local load1 cpu_pct ram_pct avail running oom
  load1=$(echo "$out" | head -1 | awk '{print $1}')
  avail=$(echo "$out" | grep -A2 "Mem:" | awk 'NR==1{print $7}')
  running=$(echo "$out" | tail -1 | tr -d '[:space:]')
  # cpu/ram calc in python for accuracy
  python3 - <<PY 2>/dev/null | tee -a "$WATCH_LOG"
import re
out = """$out"""
m = re.search(r"Mem:\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+(\d+)", out)
total = int(m.group(1)) if m else 1
avail_m = int(m.group(2)) if m else 0
load1 = float("$load1" or 0)
nproc = int("$nproc" or 1)
cpu = load1 / nproc * 100 if nproc else 0
ram_used = (total - avail_m) / total * 100 if total else 0
running = "$running"
oom = "out of memory" in out.lower() or "oom-killer" in out.lower()
flag = ""
if cpu < 95: flag += " CPU_LOW"
if ram_used < 80: flag += " RAM_LOW"
if oom: flag += " OOM!"
if "timeout" in out.lower(): flag += " SSH_TIMEOUT"
print(f"[watch] $alias load {load1:.2f}/{nproc} cpu {cpu:.1f}% ram {ram_used:.1f}% avail {avail_m}M running {running}{flag}")
PY
  # return warning code if low
}

log "WATCH starting — order $ORDER ($(wc -l < "$ROOT/$ORDER" 2>/dev/null | tr -d ' ') syms) — herding s1/s2/s3/s5 to >95% cpu >80% ram no OOM"
log "WATCH logs: herd=$HERD_LOG watchdog=$WATCH_LOG  —  tail -F $HERD_LOG $WATCH_LOG"

# initial
ensure_s1_tunnel
ensure_herd
log "WATCH initial server snapshot:"
for srv in "s1 16" "s2 4" "s3 16" "s5 16"; do poll_server $srv; done

while true; do
  sleep 45
  ensure_s1_tunnel
  ensure_herd
  for srv in "s1 16" "s2 4" "s3 16" "s5 16"; do poll_server $srv; done

  now=$(date +%s)
  if (( now - LAST_SYNC > SYNC_INTERVAL_SEC )); then
    log "WATCH periodic sync: tools/sync_s1_to_mac.sh (V15 sheets -> Mac)"
    bash "$ROOT/tools/sync_s1_to_mac.sh" 2>&1 | tail -n 15 | tee -a "$WATCH_LOG" || log "WATCH sync failed"
    LAST_SYNC=$now
    # also report combined progress
    python3 - <<'PY' 2>&1 | tee -a "$WATCH_LOG"
from pathlib import Path
import subprocess
order = Path("/Users/niels/Documents/binance/SPREADSHEETS/V15_RUNNING_ORDER_TRB_FLZ.txt").read_text().splitlines()
order = [l.strip() for l in order if l.strip()]
for host in ["s1-int","s2","s3","s5"]:
    try:
        out = subprocess.check_output(["ssh","-o","ConnectTimeout=8",host,"ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>/dev/null | xargs -I{} basename {}"], timeout=10).decode()
        names = out.strip().splitlines()
        done = sum(1 for s in order if any(s in n for n in names))
        print(f"[combined-check] {host} {done}/{len(order)}")
    except Exception as e:
        print(f"[combined-check] {host} err {e}")
# global combined
import subprocess as sp
combined=set()
for host in ["s1-int","s2","s3","s5"]:
    try:
        out=sp.check_output(["ssh","-o","ConnectTimeout=8",host,"ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>/dev/null | xargs -I{} basename {}"],timeout=10).decode()
        combined.update(out.strip().splitlines())
    except: pass
done_g = sum(1 for s in order if any(s in n for n in combined))
print(f"[combined] GLOBAL {done_g}/{len(order)} done — todo {len(order)-done_g}")
PY
  fi
done
