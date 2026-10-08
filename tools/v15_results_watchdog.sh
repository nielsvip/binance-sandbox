#!/bin/bash
# v15_results_watchdog — ZERO TOLERANCE enforcer (USER 2026-10-08: "stuck servers get rebooted, stuck sym_sides get (partially)
# skipped, but the show goes on - worst case destroy any server except s1 and gateway if no final results come out of it for >40min").
# Runs on S1 every 10 min. For every host in tools/fleet_hosts_final.json:
#   STUCK SYM_SIDE: a running pilot whose progress file has not changed for >= SYM_STALL_MIN -> kill that pilot tree
#                   (scheduler relaunches; after --max-attempts the sym_side is terminal = skipped; the show goes on)
#   STUCK SERVER  : 0 boards finished in the last RESULT_WINDOW_MIN AND no progress file written in the last PROGRESS_STALL_MIN
#                   (a host mid-board that keeps writing rows is WORKING, never punished)
#       strike 1 -> copy progress/sheets/logs to S1 ~/stalled_<host>_<ts>/ -> REBOOT (ssh sudo reboot; ALERT_REBOOT_<host> for the Mac hcloud fallback)
#       strike 2 -> copy again -> ALERT_HOST_<host> (the Mac deleter destroys it via hcloud) -> out of rotation
#   S1 itself: copy + kill pilots + ALERT only (never rebooted/deleted here; INFRA law). gateway never touched.
#   A host booted < BOOT_GRACE_MIN ago gets no server verdict (pilots are still coming back).
set -u
cd /home/niels/binance-sandbox || exit 1
RESULT_WINDOW_MIN=${V15_RESULTS_WINDOW_MIN:-40}; PROGRESS_STALL_MIN=${V15_PROGRESS_STALL_MIN:-20}; SYM_STALL_MIN=${V15_SYM_STALL_MIN:-30}; BOOT_GRACE_MIN=${V15_BOOT_GRACE_MIN:-20}
LOG=/tmp/v15_results_watchdog.log; ST=/tmp/v15_results_watchdog_state.json; AL=data/daily_chain
log(){ echo "[$(date -u +%FT%TZ)] [results-wd] $*" >> $LOG; }
[ -f $ST ] || echo '{}' > $ST
NOW=$(date +%s); TS=$(date -u +%Y%m%d%H%M)
HOSTS=$(python3 -c "import json;print(' '.join(h['name']+':'+h['ssh'][0] for h in json.load(open('tools/fleet_hosts_final.json'))['hosts']))")
for HS in $HOSTS; do
  H=${HS%%:*}; IP=${HS#*:}
  R=$(ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new "$IP" "
    PD=\$(cat ~/v15_current_progress_dir.txt 2>/dev/null); NOWR=\$(date +%s)
    D=0; for f in /tmp/sweep_*_30D.log; do [ -f \$f ] && [ \$(stat -c %Y \$f) -gt \$((NOWR-${RESULT_WINDOW_MIN}*60)) ] && grep -q '\[spec-fill\] DONE' \$f && D=\$((D+1)); done
    P=\$(ps -eo args | grep -c '[v]15_pilot.py --sym-side')
    NEW=\$(ls -t \$PD/*_v14_progress.json 2>/dev/null | head -1 | xargs -I{} stat -c %Y {} 2>/dev/null)
    UP=\$(cut -d. -f1 /proc/uptime)
    STUCK=''; for ss in \$(ps -eo args | grep '[v]15_pilot.py --sym-side' | sed -E 's/.*--sym-side ([A-Z0-9_]+).*/\1/' | sort -u); do f=\$PD/\${ss}_v14_progress.json; if [ -f \$f ] && [ \$(stat -c %Y \$f) -lt \$((NOWR-${SYM_STALL_MIN}*60)) ]; then STUCK=\"\$STUCK \$ss\"; fi; done
    echo \"\$D \$P \${NEW:-0} \$UP|\$STUCK\"" 2>/dev/null)
  if [ -z "$R" ]; then log "$H UNREACHABLE"; continue; fi
  META=${R%%|*}; STUCK=${R#*|}; set -- $META; DONE=$1; PIL=$2; NEWEST=$3; UP=$4
  # stuck sym_sides -> kill their pilot trees (the scheduler relaunches / quarantines)
  for ss in $STUCK; do
    ssh -o BatchMode=yes "$IP" "pkill -TERM -f '[v]15_pilot.py --sym-side $ss'; sleep 3; pkill -KILL -f '[v]15_pilot.py --sym-side $ss'; true" 2>/dev/null
    log "$H STUCK SYM_SIDE $ss (progress silent >= ${SYM_STALL_MIN}m) -> pilot killed (scheduler attempts++ -> skipped after max-attempts)"
  done
  if [ "$UP" -lt $((BOOT_GRACE_MIN*60)) ]; then log "$H booted $((UP/60))m ago -> grace"; continue; fi
  PROG_AGE=$(( (NOW - NEWEST) / 60 ))
  if [ "$DONE" -ge 1 ] || [ "$PROG_AGE" -lt "$PROGRESS_STALL_MIN" ]; then
    python3 -c "import json;s=json.load(open('$ST'));s['$H']={'strikes':0,'last_ok':$NOW};json.dump(s,open('$ST','w'))"
    log "$H ok done_${RESULT_WINDOW_MIN}m=$DONE pilots=$PIL progress_age=${PROG_AGE}m"; continue
  fi
  STRIKES=$(python3 -c "import json;s=json.load(open('$ST'));e=s.setdefault('$H',{'strikes':0});e['strikes']=e.get('strikes',0)+1;json.dump(s,open('$ST','w'));print(e['strikes'])")
  DEST=/home/niels/stalled_${H}_${TS}; mkdir -p "$DEST"
  PD=$(ssh -o BatchMode=yes "$IP" "cat ~/v15_current_progress_dir.txt" 2>/dev/null)
  rsync -az -e "ssh -o BatchMode=yes -o ConnectTimeout=8" "$IP:${PD:-/nonexistent}/" "$DEST/progress/" 2>/dev/null
  rsync -az --update -e "ssh -o BatchMode=yes -o ConnectTimeout=8" "$IP:${PD:-/nonexistent}/" "${PD:-/nonexistent}/" 2>/dev/null
  rsync -az -e "ssh -o BatchMode=yes -o ConnectTimeout=8" "$IP:/tmp/sweep_*_30D.log" "$DEST/logs/" 2>/dev/null
  rsync -az -e "ssh -o BatchMode=yes -o ConnectTimeout=8" "$IP:binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/" "$DEST/sheets/" 2>/dev/null
  log "$H STALLED (0 finished in ${RESULT_WINDOW_MIN}m, progress silent ${PROG_AGE}m, pilots=$PIL) strike=$STRIKES -> data copied to $DEST ($(du -sh $DEST 2>/dev/null | cut -f1))"
  if [ "$H" = "s1" ]; then
    pkill -TERM -f '[v]15_pilot.py --sym-side'; echo "$(date -u +%FT%TZ) S1 stalled strike $STRIKES (never rebooted/deleted here): pilots killed, data in $DEST" > "$AL/ALERT_S1_STALL.txt"; log "s1: pilots killed + ALERT_S1_STALL (INFRA law)"; continue
  fi
  if [ "$STRIKES" -ge 2 ]; then
    cp tools/fleet_hosts_final.json "backups/fleet_hosts_before_strike_${H}_${TS}.json"
    python3 -c "import json;p='tools/fleet_hosts_final.json';d=json.load(open(p));[h.__setitem__('venues',[]) for h in d['hosts'] if h['name']=='$H'];json.dump(d,open(p,'w'),indent=1)"
    echo "$(date -u +%FT%TZ) $H STALLED twice (>${RESULT_WINDOW_MIN}m no final results, progress silent) -> DESTROY; data in $DEST" > "$AL/ALERT_HOST_${H}.txt"
    log "$H strike 2 -> ALERT_HOST_${H} (Mac deleter destroys via hcloud) + out of rotation"
  else
    ssh -o BatchMode=yes -o ConnectTimeout=8 "$IP" "sudo -n reboot" >/dev/null 2>&1 && log "$H strike 1 -> REBOOT issued (ssh sudo reboot)" || { echo "$(date -u +%FT%TZ) $H reboot via ssh failed -> hcloud reboot" > "$AL/ALERT_REBOOT_${H}.txt"; log "$H strike 1 -> ssh reboot failed, ALERT_REBOOT_${H} for the Mac hcloud fallback"; }
    python3 -c "import json;s=json.load(open('/tmp/v15_fleet_sched.log' if False else '$ST'));s['$H']['last_ok']=$NOW;json.dump(s,open('$ST','w'))"
  fi
done
