#!/bin/bash
# s2_disk_guard.sh — USER 2026-10-17: "s2 only needs to hold some old npz files it should NEVER fill up delete obsolete trash".
# Runs from cron every 15 min. When free < S2_GUARD_MIN_FREE_GB (40) or use% > S2_GUARD_MAX_USE (85) it prunes ONLY:
#   A decided version-gate runs (~/v15_bench/runs/*__gate_<ID>_*): root/ tree + per-side eval_cache.db* (results.json + per-side out/progress/pilot.log kept)
#   B decided throughput-proof arms (~/v15_throughput_proof/*/arm_*, proof.json present, idle >24h): root/ + eval_cache.db*
#   C /tmp regular files older than 3 days, not open by any process (locks/pids/sockets/dot-files skipped)
#   D ~/binance-sandbox/backups top-level regular files older than 14 days, keeping the newest of each distinct file (never *.npz, never dirs)
#   E ~/binance-sandbox/data/eval_cache_v1.db* only when the gate champion env has V15_EVAL_CACHE=0 and no process holds it open
# NEVER touches: snap_*, gate/*, the champion's run, pending/running candidates' runs, the current progress dir, NPZ stores, klines, boards, ~/binance.
# Usage: s2_disk_guard.sh [--dry-run] [--force]   (--force = prune even above the threshold)
set -u
DRY=0; FORCE=0
for a in "$@"; do case "$a" in --dry-run) DRY=1 ;; --force) FORCE=1 ;; esac; done
H=/home/niels
BENCH=$H/v15_bench
LOG=${S2_GUARD_LOG:-$H/logs/s2_disk_guard.log}
MIN_FREE_GB=${S2_GUARD_MIN_FREE_GB:-40}
MAX_USE=${S2_GUARD_MAX_USE:-85}
mkdir -p "$(dirname "$LOG")"
exec 9>/tmp/s2_disk_guard.lock
flock -n 9 || exit 0
ts() { date -u +%FT%TZ; }
log() { echo "$(ts) $*" | tee -a "$LOG"; }
free_gb() { df -BG --output=avail / | tail -1 | tr -dc 0-9; }
use_pct() { df --output=pcent / | tail -1 | tr -dc 0-9; }
F0=$(free_gb); U0=$(use_pct)
if [ "$FORCE" = 0 ] && [ "$F0" -ge "$MIN_FREE_GB" ] && [ "$U0" -le "$MAX_USE" ]; then
  [ "$DRY" = 1 ] && log "dry-run: free=${F0}G use=${U0}% above thresholds (free>=${MIN_FREE_GB}G, use<=${MAX_USE}%) -> cron run would do nothing; listing what it WOULD prune:"
  [ "$DRY" = 1 ] || exit 0
fi
MODE=$([ "$DRY" = 1 ] && echo DRY || echo DEL)
log "start mode=$MODE free=${F0}G use=${U0}%"
OPEN=$(mktemp)
for p in /proc/[0-9]*; do
  readlink "$p/cwd" 2>/dev/null
  for fd in "$p"/fd/*; do readlink "$fd" 2>/dev/null; done
done | sed 's/ (deleted)$//' | sort -u > "$OPEN"
is_open_under() { grep -qF -- "$1" "$OPEN"; }
TOTAL=0
zap() {
  local path="$1" why="$2"
  [ -e "$path" ] || return 0
  if is_open_under "$path"; then log "KEEP(open) $path"; return 0; fi
  local kb; kb=$(du -sk -- "$path" 2>/dev/null | cut -f1); kb=${kb:-0}
  TOTAL=$((TOTAL + kb))
  if [ "$DRY" = 1 ]; then log "WOULD $why $((kb / 1024))M $path"; else rm -rf -- "$path" && log "DELETED $why $((kb / 1024))M $path"; fi
}
# --- protected gate ids: champion origin + pending/running candidates
CHAMP_ID=$(python3 -c "import json;m=json.load(open('$BENCH/gate/champion/meta.json'));print(str(m.get('from','')).split()[-1])" 2>/dev/null)
LIVE_IDS=$(python3 - <<PY 2>/dev/null
import json, pathlib
for p in pathlib.Path("$BENCH/gate/candidates").glob("*/meta.json"):
    try:
        st = json.load(open(p)).get("state")
    except Exception:
        st = "unreadable"
    if st not in ("PASS", "FAIL", "HARNESS_FAIL", "IMPOSSIBLE", "deployed", "rejected", "withdrawn"):
        print(p.parent.name)
PY
)
if [ -z "$CHAMP_ID" ]; then log "ABORT category A: cannot read champion id"; fi
# --- A decided gate runs
if [ -n "$CHAMP_ID" ] && [ -d "$BENCH/runs" ]; then
  for run in "$BENCH"/runs/*__gate_*_c*; do
    [ -d "$run" ] || continue
    id=$(basename "$run" | sed -E 's/.*__gate_([0-9T]+Z)_.*/\1/')
    [ "$id" = "$CHAMP_ID" ] && continue
    echo "$LIVE_IDS" | grep -qx "$id" && continue
    [ -f "$BENCH/gate/candidates/$id/meta.json" ] || continue
    [ -f "$run/results.json" ] || continue
    zap "$run/root" "A:decided-run-root"
    for c in "$run"/*/eval_cache.db "$run"/*/eval_cache.db-wal "$run"/*/eval_cache.db-shm; do [ -e "$c" ] && zap "$c" "A:decided-run-cache"; done
  done
fi
# --- B decided throughput-proof arms
for arm in "$H"/v15_throughput_proof/*/arm_*; do
  [ -d "$arm" ] || continue
  [ -f "$(dirname "$arm")/proof.json" ] || continue
  [ -n "$(find "$arm" -maxdepth 1 -name pilot.log -mmin -1440 2>/dev/null)" ] && continue
  zap "$arm/root" "B:proof-arm-root"
  for c in "$arm"/eval_cache.db "$arm"/eval_cache.db-wal "$arm"/eval_cache.db-shm; do [ -e "$c" ] && zap "$c" "B:proof-arm-cache"; done
done
# --- C /tmp files older than 3 days
while IFS= read -r -d '' f; do zap "$f" "C:tmp>3d"; done < <(find /tmp -xdev -type f -mtime +3 -user niels ! -name '*.lock' ! -name '*.pid' ! -name '.*' ! -path '/tmp/.*' ! -path '/tmp/systemd-private*' ! -path '/tmp/tmux*' -print0 2>/dev/null)
# --- D backups >14d, newest per distinct file kept
BK=$H/binance-sandbox/backups
if [ -d "$BK" ]; then
  python3 - "$BK" <<'PY' > /tmp/s2_guard_bk.$$ 2>/dev/null
import os, re, sys, time
bk = sys.argv[1]; groups = {}
for n in os.listdir(bk):
    p = os.path.join(bk, n)
    if not os.path.isfile(p) or os.path.islink(p) or n.endswith(".npz"):
        continue
    base, ext = os.path.splitext(n)
    if base.endswith(".tar"):
        base, ext = base[:-4], ".tar" + ext
    key = re.sub(r"[_.-]?\d{8,14}([_.-]\d{4,6})?$", "", base) + ext
    groups.setdefault(key, []).append((os.path.getmtime(p), p))
cut = time.time() - 14 * 86400
for key, items in groups.items():
    items.sort()
    for mt, p in items[:-1]:
        if mt < cut:
            print(p)
PY
  while IFS= read -r f; do [ -n "$f" ] && zap "$f" "D:backup>14d-not-newest"; done < /tmp/s2_guard_bk.$$
  rm -f /tmp/s2_guard_bk.$$
fi
# --- E eval cache only when switched off in the champion env
EC=$(python3 -c "import json;m=json.load(open('$BENCH/gate/champion/meta.json'));print((m.get('env') or {}).get('V15_EVAL_CACHE',''))" 2>/dev/null)
if [ "$EC" = "0" ]; then
  for c in $H/binance-sandbox/data/eval_cache_v1.db $H/binance-sandbox/data/eval_cache_v1.db-wal $H/binance-sandbox/data/eval_cache_v1.db-shm; do [ -e "$c" ] && zap "$c" "E:eval-cache-off"; done
else
  log "skip E: champion env V15_EVAL_CACHE='${EC}' (cache still in use; V15_EVALCACHE_CAP cron caps it)"
fi
rm -f "$OPEN"
F1=$(free_gb); U1=$(use_pct)
log "end mode=$MODE candidates=$((TOTAL / 1024))M free ${F0}G->${F1}G use ${U0}%->${U1}% champion=$CHAMP_ID protected=[$(echo $LIVE_IDS)]"
if [ "$DRY" = 0 ] && [ "$F1" -lt 20 ]; then log "ALERT free ${F1}G < 20G after pruning safe categories — gate will HOLD; needs a human look (du -xsh ~/*)"; fi
