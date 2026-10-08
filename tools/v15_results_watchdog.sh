#!/bin/bash
# v15_results_watchdog — USER 2026-10-08 ("every server gets at least 1-6 sym results per hour and gets deleted (all data
# copied) if stalled — we are not playing games anymore"). Runs on S1 (cron */15).
#
# Per worker host (tools/fleet_hosts_final.json): count boards that finished in the last WINDOW_MIN minutes
# ([spec-fill] DONE in /tmp/sweep_*_30D.log). A host with pilots running but 0 finished boards for >= STALL_MIN minutes
# is STALLED:
#   1. COPY: rsync its current progress dir + sweep logs + SPREADSHEETS/V15_V16_CELL_BY_CELL to S1 ~/stalled_<host>_<ts>/ (data never lost)
#   2. RESET: kill every pilot tree on it (the scheduler relaunches fresh boards within 2 min; attempts are reset)
#   3. STRIKE: after 2 consecutive strikes the host is taken OUT of rotation (venues=[] in fleet_hosts_final.json, backup kept)
#      and an ALERT is written (data/daily_chain/ALERT_HOST_<host>.txt) — re-imaging from S1 is the operator's call (INFRA law:
#      S4/S5/S6 are images of S1, never provisioned from scratch).
# S1 itself is never deleted (INFRA law); a stalled S1 gets COPY+RESET+ALERT only.
# Log: /tmp/v15_results_watchdog.log ; state: /tmp/v15_results_watchdog_state.json
set -u
cd /home/niels/binance-sandbox || exit 1
WINDOW_MIN=${V15_RESULTS_WINDOW_MIN:-60}
STALL_MIN=${V15_RESULTS_STALL_MIN:-120}
MIN_RESULTS=${V15_RESULTS_MIN:-1}
LOG=/tmp/v15_results_watchdog.log; ST=/tmp/v15_results_watchdog_state.json
log(){ echo "[$(date -u +%FT%TZ)] [results-wd] $*" >> $LOG; }
[ -f $ST ] || echo '{}' > $ST
NOW=$(date +%s)
HOSTS=$(python3 -c "import json;print(' '.join(h['name']+':'+h['ssh'][0] for h in json.load(open('tools/fleet_hosts_final.json'))['hosts']))")
for HS in $HOSTS; do
  H=${HS%%:*}; IP=${HS#*:}
  R=$(ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new "$IP" "T=\$(( \$(date +%s) - ${WINDOW_MIN}*60 )); D=0; for f in /tmp/sweep_*_30D.log; do [ -f \$f ] && [ \$(stat -c %Y \$f) -gt \$T ] && grep -q '\[spec-fill\] DONE' \$f && D=\$((D+1)); done; P=\$(ps -eo args | grep -c '[v]15_pilot.py --sym-side'); NEW=\$(ls -t \$(cat ~/v15_current_progress_dir.txt 2>/dev/null)/*_v14_progress.json 2>/dev/null | head -1 | xargs -I{} stat -c %Y {} 2>/dev/null); echo \"\$D \$P \${NEW:-0}\"" 2>/dev/null)
  if [ -z "$R" ]; then log "$H UNREACHABLE"; python3 - "$ST" "$H" "$NOW" <<'EOF'
import json,sys; p,h,now=sys.argv[1],sys.argv[2],int(sys.argv[3]); s=json.load(open(p)); e=s.setdefault(h,{}); e["unreachable_since"]=e.get("unreachable_since") or now; json.dump(s,open(p,"w"))
EOF
    continue; fi
  set -- $R; DONE=$1; PIL=$2; NEWEST=$3
  UP=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$IP" "cut -d. -f1 /proc/uptime" 2>/dev/null); if [ -n "$UP" ] && [ "$UP" -lt 2700 ]; then log "$H booted ${UP}s ago -> grace, no verdict"; continue; fi
  VERDICT=$(python3 - "$ST" "$H" "$NOW" "$DONE" "$PIL" "$NEWEST" "$STALL_MIN" "$MIN_RESULTS" <<'EOF'
import json,sys
p,h,now,done,pil,newest,stall_min,min_res=sys.argv[1],sys.argv[2],int(sys.argv[3]),int(sys.argv[4]),int(sys.argv[5]),int(sys.argv[6]),int(sys.argv[7]),int(sys.argv[8])
s=json.load(open(p)); e=s.setdefault(h,{"strikes":0}); e.pop("unreachable_since",None)
if done>=min_res or pil==0 and newest and now-newest<900:
    e["last_ok"]=now; e["strikes"]=0; v="OK"
else:
    e.setdefault("last_ok",now)
    idle_min=(now-e["last_ok"])//60
    v="STALLED" if (pil>0 and idle_min>=stall_min) else f"WATCH idle={idle_min}m"
e["last"]={"done":done,"pilots":pil,"at":now}
json.dump(s,open(p,"w")); print(v)
EOF
)
  log "$H done_${WINDOW_MIN}m=$DONE pilots=$PIL verdict=$VERDICT"
  if [ "$VERDICT" = "STALLED" ]; then
    TS=$(date -u +%Y%m%d%H%M); DEST=/home/niels/stalled_${H}_${TS}; mkdir -p "$DEST"
    PD=$(ssh -o BatchMode=yes "$IP" "cat ~/v15_current_progress_dir.txt" 2>/dev/null)
    rsync -az -e "ssh -o BatchMode=yes -o ConnectTimeout=8" "$IP:${PD:-/nonexistent}/" "$DEST/progress/" 2>/dev/null
    rsync -az -e "ssh -o BatchMode=yes -o ConnectTimeout=8" "$IP:/tmp/sweep_*_30D.log" "$DEST/logs/" 2>/dev/null
    rsync -az -e "ssh -o BatchMode=yes -o ConnectTimeout=8" "$IP:binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/" "$DEST/sheets/" 2>/dev/null
    log "$H STALLED -> data copied to $DEST ($(du -sh $DEST 2>/dev/null | cut -f1)); killing pilot trees"
    ssh -o BatchMode=yes "$IP" "pkill -TERM -f '[v]15_pilot.py --sym-side'; sleep 5; pkill -KILL -f '[v]15_pilot.py --sym-side'; echo killed" >> $LOG 2>&1
    STRIKES=$(python3 -c "import json;s=json.load(open('$ST'));e=s['$H'];e['strikes']=e.get('strikes',0)+1;e['last_ok']=$NOW;json.dump(s,open('$ST','w'));print(e['strikes'])")
    if [ "$STRIKES" -ge 2 ] && [ "$H" != "s1" ]; then
      cp tools/fleet_hosts_final.json "backups/fleet_hosts_before_strike_${H}_${TS}.json"
      python3 -c "import json;p='tools/fleet_hosts_final.json';d=json.load(open(p));[h.__setitem__('venues',[]) for h in d['hosts'] if h['name']=='$H'];json.dump(d,open(p,'w'),indent=1)"
      echo "$(date -u +%FT%TZ) $H STALLED twice (0 results/${WINDOW_MIN}m for >=${STALL_MIN}m with pilots running) -> OUT OF ROTATION; data in $DEST; re-image from S1 (INFRA law) or fix and restore venues in tools/fleet_hosts_final.json" > "data/daily_chain/ALERT_HOST_${H}.txt"
      log "$H OUT OF ROTATION after $STRIKES strikes (ALERT_HOST_${H}.txt)"
    fi
  fi
done
