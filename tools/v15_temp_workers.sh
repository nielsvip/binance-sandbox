#!/bin/bash
# v15_temp_workers — lifecycle of the temporary workers (USER 2026-10-08: "add 4, make sure they are gone before market open").
# Mac side (hcloud token here). Subcommands:
#   register <name:ip> ...   add to fleet_hosts_final.json on S1 + Mac (copy of s6's entry), sync_indicators.sh IPs on S1
#   collect                  rsync every temp worker's progress dir + sheets + data/<name>_365 to S1 (~/tempworker_<name>/ and the
#                            shared round progress dir with --update) — run before the 12:15Z chain and before delete
#   delete                   collect, then `hcloud server delete` each temp worker, remove them from fleet_hosts + sync_indicators
# Temp workers are listed in /Users/niels/Documents/binance/data/daily_chain/TEMP_WORKERS.txt (name:ip per line).
set -u
export PATH=/Users/niels/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
ROOT=/Users/niels/Documents/binance; LIST=$ROOT/data/daily_chain/TEMP_WORKERS.txt; LOG=/tmp/v15_temp_workers.log
S1=${V15_S1:-s1-int}; SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
log(){ echo "[$(date -u +%FT%TZ)] [temp-workers] $*" | tee -a $LOG; }
cmd=${1:-}; shift || true
case "$cmd" in
register)
  for HS in "$@"; do N=${HS%%:*}; IP=${HS#*:}; grep -q "^$N:" $LIST 2>/dev/null || echo "$N:$IP" >> $LIST; done
  for T in "$S1:/home/niels/binance-sandbox" "mac:$ROOT"; do H=${T%%:*}; R=${T#*:}
    PY='import json,sys
p=sys.argv[1]; d=json.load(open(p)); base=[h for h in d["hosts"] if h["name"]=="s6"][0]
for hs in sys.argv[2:]:
    n,ip=hs.split(":")
    if any(h["name"]==n for h in d["hosts"]): continue
    e=dict(base); e["name"]=n; e["ssh"]=[ip]; e["venues"]=["crypto","stocks"]; e["temp"]=True; d["hosts"].append(e)
json.dump(d,open(p,"w"),indent=1); print("registered", [hs.split(":")[0] for hs in sys.argv[2:]], "in", p)'
    if [ "$H" = mac ]; then cp $R/tools/fleet_hosts_final.json $R/backups/fleet_hosts_before_temp_$(date +%Y%m%d%H%M).json; $R/.venv/bin/python -c "$PY" $R/tools/fleet_hosts_final.json "$@" | tee -a $LOG
    else $SSH $S1 "cd $R && cp tools/fleet_hosts_final.json backups/fleet_hosts_before_temp_\$(date -u +%Y%m%d%H%M).json && python3 -c '$PY' tools/fleet_hosts_final.json $* && for hs in $*; do ip=\${hs#*:}; grep -q \"\$ip\" tools/sync_indicators.sh || sed -i \"s#for ip in 10.0.0.6#for ip in \$ip 10.0.0.6#\" tools/sync_indicators.sh; ssh-keyscan -T 5 \$ip >> ~/.ssh/known_hosts 2>/dev/null; done; grep -n 'for ip in' tools/sync_indicators.sh" | tee -a $LOG
    fi
  done ;;
collect)
  [ -f $LIST ] || exit 0
  while IFS=: read -r N IP; do [ -z "$N" ] && continue
    $SSH $S1 "PD=\$(cat ~/v15_current_progress_dir.txt); D=~/tempworker_$N; mkdir -p \$D/progress \$D/sheets \$D/365 \$PD; \
      rsync -az --update -e 'ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new' niels@$IP:\$PD/ \$D/progress/ 2>/dev/null; \
      rsync -az --update -e 'ssh -o BatchMode=yes -o ConnectTimeout=8' niels@$IP:\$PD/ \$PD/ 2>/dev/null; \
      rsync -az --update -e 'ssh -o BatchMode=yes -o ConnectTimeout=8' niels@$IP:binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ \$D/sheets/ 2>/dev/null; \
      rsync -az --update -e 'ssh -o BatchMode=yes -o ConnectTimeout=8' niels@$IP:binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>/dev/null; \
      rsync -az --update -e 'ssh -o BatchMode=yes -o ConnectTimeout=8' niels@$IP:binance-sandbox/data/${N}_365/ \$D/365/ 2>/dev/null; \
      echo \"$N collected: progress \$(ls \$D/progress | wc -l) sheets \$(ls \$D/sheets | wc -l) 365 \$(ls \$D/365 | wc -l)\"" | tee -a $LOG
  done < $LIST ;;
delete)
  bash $0 collect
  [ -f $LIST ] || exit 0
  while IFS=: read -r N IP; do [ -z "$N" ] && continue
    case "$N" in s1|niels|gateway|s2|s5|s6|"") log "refusing $N"; continue;; esac
    ID=$(hcloud server list -o noheader -o columns=id,name | awk -v h="$N" '$2==h {print $1}')
    [ -n "$ID" ] && { hcloud server delete "$ID" >> $LOG 2>&1 && log "$N ($ID) DELETED" || log "$N delete FAILED"; }
    PY='import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["hosts"]=[h for h in d["hosts"] if h["name"]!=sys.argv[2]]; json.dump(d,open(p,"w"),indent=1)'
    $ROOT/.venv/bin/python -c "$PY" $ROOT/tools/fleet_hosts_final.json "$N"
    $SSH $S1 "cd ~/binance-sandbox && python3 -c '$PY' tools/fleet_hosts_final.json $N && sed -i 's#for ip in $IP #for ip in #' tools/sync_indicators.sh"
  done < $LIST
  mv $LIST $LIST.deleted_$(date -u +%Y%m%d%H%M); log "temp workers removed from fleet_hosts (S1+Mac) and sync_indicators"
  osascript -e 'display notification "temp workers s7-s10 deleted before open; results collected on S1" with title "v15 temp workers"' 2>/dev/null ;;
*) echo "usage: $0 register name:ip ... | collect | delete"; exit 2;;
esac
