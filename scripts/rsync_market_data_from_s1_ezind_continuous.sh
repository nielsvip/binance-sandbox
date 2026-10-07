#!/bin/bash
# 2026-10-06 USER: "macbook always has 2 sources of crypto indicators".
# Second source = S1's own crypto indicator stack (/home/niels/binance/ez_s1_stack, see
# ez_s1_stack_health.sh there). Every 2s: if S1's latest_market_data.json changed, pull it
# (atomic on S1 via os.replace) into a staging dir OUTSIDE the repo, then hardlink it into
# data/ as market_data_<S1 write time>_s1.json with S1's mtime preserved (rsync -t).
# Naming: the _s1 SUFFIX (not prefix) keeps both selection styles honest:
#   - mtime-sorted readers (ez_manage._get_latest_market_data_file, ez_positions_quick, utils)
#   - name-sorted readers (ez_manage snapshot loader, ez_positions_service) — a prefix like
#     market_data_s1_* would sort above every Mac file forever ('s' > '2') even when stale.
# "worker" is not in the name, so the ez_manage worker exclusion does not skip it.
# The Mac ez_indicators prune deletes unparseable market_data_* names on each Mac save; the
# newest S1 snapshot is re-linked from staging every cycle so it is always available.
# Every pulled snapshot must pass s1_snapshot_fresh_gate.py (content, not mtime): S1 median
# 3m-bar age <= Mac's own newest snapshot + 60s. A rejected snapshot withdraws ALL *_s1.json
# from data/ so a frozen S1 can never outrank the Mac by name/mtime.
# Keeps the newest 20 *_s1.json in data/. Removes *_s1.json copies the Mac->S1 push loop
# (rsync_market_data_to_server_continuous.sh) drops into S1 ~/binance/data after 3 min.
WORKDIR="/Users/niels/Documents/binance"
DATA_DIR="$WORKDIR/data"
STAGE="/Users/niels/s1_ezind_pull"
REMOTE_FILE="/home/niels/binance/ez_s1_stack/data/latest_market_data.json"
HOSTS=("s1-pub" "s1-int")  # s1-pub direct ~0.4s/session; s1-int gateway tunnel measured 19-25s/session under load
KEEP=20
INTERVAL=2
LOG_FILE="$HOME/logs/rsync_market_data_from_s1_ezind.log"
PY="/opt/anaconda3/envs/binance_env/bin/python"
GATE="$WORKDIR/scripts/s1_snapshot_fresh_gate.py"  # content-freshness gate (3m bar age vs Mac's own snapshot)
SSH_OPTS=(-o ConnectTimeout=5 -o BatchMode=yes -o ServerAliveInterval=10)
mkdir -p "$STAGE" "$(dirname "$LOG_FILE")"
log(){
    if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt 20971520 ]; then mv -f "$LOG_FILE" "$LOG_FILE.1"; fi
    echo "$(date -u '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}
host_idx=0
last_mtime=""
last_published=""
last_remote_clean=0
log "START pull loop hosts=${HOSTS[*]} interval=${INTERVAL}s keep=$KEEP"
while true; do
    host="${HOSTS[$host_idx]}"
    remote_mtime=$(timeout 10 ssh "${SSH_OPTS[@]}" "$host" "stat -c %Y $REMOTE_FILE" 2>/dev/null)
    if ! [[ "$remote_mtime" =~ ^[0-9]+$ ]]; then
        host_idx=$(( (host_idx + 1) % ${#HOSTS[@]} ))
        log "WARN stat failed on $host -> switching to ${HOSTS[$host_idx]}"
        sleep "$INTERVAL"
        continue
    fi
    if [ "$remote_mtime" != "$last_mtime" ]; then
        ts_label=$(date -u -r "$remote_mtime" '+%Y%m%d_%H%M%S')
        name="market_data_${ts_label}_s1.json"
        if timeout 40 rsync -az --timeout=20 -e "ssh ${SSH_OPTS[*]}" "$host:$REMOTE_FILE" "$STAGE/$name" 2>>"$LOG_FILE"; then
            size=$(wc -c < "$STAGE/$name" | tr -d ' ')
            tail_char=$(tail -c 64 "$STAGE/$name" | tr -d ' \n\r\t' | tail -c 1)
            mac_ref=$(ls -t "$DATA_DIR"/market_data_2*.json 2>/dev/null | grep -v '_s1\.json$' | grep -v worker | head -1)
            if [ "$size" -gt 1000000 ] && [ "$tail_char" = "}" ] && gate=$("$PY" "$GATE" "$STAGE/$name" "$mac_ref" 2>&1); then
                ln -f "$STAGE/$name" "$DATA_DIR/$name"
                last_mtime="$remote_mtime"
                last_published="$name"
                log "PULL $host $name size=$size s1_age_at_publish=$(( $(date +%s) - remote_mtime ))s $gate"
            elif [ "$size" -gt 1000000 ] && [ "$tail_char" = "}" ]; then
                last_mtime="$remote_mtime"
                last_published=""
                rm -f "$DATA_DIR"/market_data_*_s1.json
                log "GATE_REJECT $name — S1 content staler than Mac; all *_s1.json withdrawn from data/: $gate"
                rm -f "$STAGE/$name"
            else
                log "WARN invalid snapshot $name size=$size tail='$tail_char' — not published"
                rm -f "$STAGE/$name"
            fi
        else
            log "WARN rsync failed from $host"
        fi
    fi
    if [ -n "$last_published" ] && [ ! -f "$DATA_DIR/$last_published" ] && [ -f "$STAGE/$last_published" ]; then
        ln -f "$STAGE/$last_published" "$DATA_DIR/$last_published"
    fi
    ls -1 "$DATA_DIR"/market_data_*_s1.json 2>/dev/null | sort -r | tail -n +$((KEEP + 1)) | while read -r f; do rm -f "$f"; done
    ls -1 "$STAGE"/market_data_*_s1.json 2>/dev/null | sort -r | tail -n +4 | while read -r f; do rm -f "$f"; done
    now=$(date +%s)
    if [ $((now - last_remote_clean)) -ge 30 ]; then
        timeout 10 ssh "${SSH_OPTS[@]}" "$host" "find /home/niels/binance/data -maxdepth 1 -name 'market_data_*_s1.json' -mmin +3 -delete" 2>/dev/null
        last_remote_clean=$now
    fi
    # Cross-symbol ranking inputs (written on Mac by ez_rankings, read by S1 ez_indicators for
    # 0dc_qty / final-score fields) — S1 does not run ez_rankings, so feed it every 60s.
    if [ $((now - ${last_rank_push:-0})) -ge 60 ]; then
        timeout 30 rsync -az --timeout=20 -e "ssh ${SSH_OPTS[*]}" "$DATA_DIR/rankings.json" "$DATA_DIR/final_score_norm.json" "$host:/home/niels/binance/ez_s1_stack/data/" 2>>"$LOG_FILE"
        last_rank_push=$now
    fi
    sleep "$INTERVAL"
done
