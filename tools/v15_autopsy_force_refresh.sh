#!/bin/bash
# v15_autopsy_force_refresh — S1 cron */10 (with flock): when the s7 autopsy-first queue
# is drained AND s7 is quiet, force-recompute the stocks autopsy bases measured under the
# pre-2026-10-08-20:00Z §78 bug (full-set evals forced masters ON, understating trades).
# Self-healing: every tick recomputes the stale list (mtime < cut); finished bases drop out,
# so a dead s7/reboot simply relaunches the remainder. Never duplicates: exits while force
# streams run. V15_AF_PULL + V15_AF_PUSH distribute refreshed bases automatically.
set -u
export PATH=/usr/bin:/bin:/usr/local/bin
OUT=$HOME/v15_autopsy_first; LOG=/tmp/v15_autopsy_force_refresh.log
S7=10.0.0.7
SSH="ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new"
CUT=$(date -u -d 2026-10-08T20:00 +%s)
log(){ echo "[$(date -u +%FT%TZ)] [af-force-refresh] $*" | tee -a $LOG; }
# 0. force streams already running on s7? ('python' in pattern excludes the batch sync loop)
$SSH niels@$S7 "pgrep -f 'python.*[v]15_autopsy_first' >/dev/null" 2>/dev/null && exit 0
# 1. stale list on S1 (stocks eff bases older than the §78 fix)
LIST=$(python3 - "$OUT" "$CUT" <<'PYEOF' 2>/dev/null
import json, glob, os, sys
out, cut = sys.argv[1], float(sys.argv[2])
ss_list = []
for f in glob.glob(os.path.join(out, "*_autopsy_base.json")):
    if os.path.getmtime(f) >= cut:
        continue
    ss = os.path.basename(f).replace("_autopsy_base.json", "")
    sym = ss.rsplit("_", 1)[0].upper()
    if sym.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD")):
        continue
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if (d.get("autopsy") or {}).get("effective_switches"):
        ss_list.append(ss)
print(",".join(sorted(ss_list)))
PYEOF
)
N=$(echo "$LIST" | tr ',' '\n' | grep -c . || true)
[ "$N" = "0" ] && exit 0
# 2. queue drained on s7?
LEFT=$($SSH niels@$S7 "comm -23 <(sort ~/v15_autopsy_first_queue_all.txt 2>/dev/null) <(ls ~/v15_autopsy_first/*_autopsy_base.json 2>/dev/null | sed 's#.*/\(.*\)_autopsy_base.json#\1#' | sort) 2>/dev/null | wc -l" 2>/dev/null) || exit 0
[ "${LEFT:-99}" != "0" ] && exit 0
# 3. dead redo quiet? (its log untouched for >=15 min; queue streams already excluded by step 0)
$SSH niels@$S7 "test -z \"\$(find /tmp/v15_autopsy_dead_redo.log -mmin -15 2>/dev/null)\"" 2>/dev/null || exit 0
log "queue drained, s7 quiet — force-refreshing $N stale stocks bases"
C1=$(echo "$LIST" | tr ',' '\n' | awk 'NR%2==1' | paste -sd,)
C2=$(echo "$LIST" | tr ',' '\n' | awk 'NR%2==0' | paste -sd,)
$SSH niels@$S7 "cd ~/binance-sandbox && setsid nohup bash -c \"V12_NPZ_CACHE=4 nice -n 10 .venv/bin/python -u tools/v15_autopsy_first.py --symsides $C1 --out ~/v15_autopsy_first --workers 3 --force >> /tmp/v15_autopsy_force_stream0.log 2>&1\" >/dev/null 2>&1 < /dev/null & setsid nohup bash -c \"V12_NPZ_CACHE=4 nice -n 10 .venv/bin/python -u tools/v15_autopsy_first.py --symsides $C2 --out ~/v15_autopsy_first --workers 3 --force >> /tmp/v15_autopsy_force_stream1.log 2>&1\" >/dev/null 2>&1 < /dev/null & echo launched" 2>/dev/null | tee -a $LOG
