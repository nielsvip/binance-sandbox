#!/bin/bash
# mac_market_data_pull_s1.sh — the RETURN half of the market-data contract.
#
# USER 2026-10-22: "no market_data file pushed to s1 starts ez_indicators/klines/prices/mark_prices/prices_ws on s1
# IMMEDIATELY and syncs to macbook so it never has a >90s market data file NO MATTER WHAT THE FUCK HAPPENS."
#
# The push half existed (MAC_MD_PUSH_S1, mac->s1 every minute). The return half did NOT: tools/mac_posfailover_pull.sh
# pulls only {acct}/{long,short}_positions.json and was not even in crontab. So when the Mac stopped producing, S1 had
# nothing newer to give back and the Mac's engines kept reading their own stale file.
#
# MEASURED 2026-10-22 04:1xZ, which is why this exists: data/latest_market_data.json — the snapshot EVERY live engine
# reads for entry AND exit decisions — was last written 00:49, i.e. 12,357 s (3 h 26 m) stale, because ez_indicators
# was crawling through warm-up (1h 150/201) on a Mac at load 190-240. Every account traded on 3-hour-old indicators:
# positions opened and did not close in time. S1's copy was byte-identical and equally old (8,491,354 bytes), because
# all it ever had was the Mac's own push.
#
# WHAT THIS DOES, every 10 s: if the Mac's snapshot is older than MAX_AGE_S, pull S1's and install it ONLY when S1's
# is strictly fresher. Never the other way round — serving an older file over a newer one is how 26-day-old prices
# reached live once before (data_never_miss 10-21), so the age comparison is explicit and rsync runs with -u.
set -u
BASE="/Users/niels/Documents/binance"
REL="data/latest_market_data.json"
MAX_AGE_S="${MAC_MD_MAX_AGE_S:-90}"
HOSTS="${MAC_MD_PULL_HOSTS:-s1-int s1-pub}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new"
LOG="/Users/niels/logs/mac_market_data_pull_s1.log"
ALERT="$BASE/data/alerts/market_data_stale.jsonl"
ROUNDS="${MAC_MD_PULL_ROUNDS:-5}"      # 5 rounds x 10 s ≈ one cron minute, so the effective check cadence is 10 s
SLEEP_S="${MAC_MD_PULL_SLEEP_S:-10}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >> "$LOG"; }

age_of() { local f="$1"; [ -f "$f" ] || { echo 999999; return; }; echo $(( $(date +%s) - $(stat -f %m "$f") )); }

for _r in $(seq 1 "$ROUNDS"); do
  mac_age=$(age_of "$BASE/$REL")
  if [ "$mac_age" -le "$MAX_AGE_S" ]; then
    [ "$_r" = 1 ] && log "ok: mac $REL age ${mac_age}s <= ${MAX_AGE_S}s"
    sleep "$SLEEP_S"; continue
  fi
  got=0
  for H in $HOSTS; do
    # Remote age first: never pay for a transfer that cannot help, and never install something older.
    r_age=$(timeout 20 ssh $SSH_OPTS "$H" "f=~/binance/$REL; [ -f \$f ] && echo \$(( \$(date +%s) - \$(stat -c %Y \$f) )) || echo 999999" 2>/dev/null | tr -dc '0-9')
    [ -z "${r_age:-}" ] && continue
    if [ "$r_age" -ge "$mac_age" ]; then
      log "skip $H: its $REL is ${r_age}s old, not fresher than the Mac's ${mac_age}s (never serve older data)"
      continue
    fi
    tmp="$BASE/$REL.pull.$$"
    if timeout 60 rsync -az -u --timeout=25 -e "ssh $SSH_OPTS" "$H:~/binance/$REL" "$tmp" >>"$LOG" 2>&1 && [ -s "$tmp" ]; then
      # Validate before installing: a truncated or non-JSON file would blind every engine that reads it.
      if /opt/anaconda3/envs/binance_env/bin/python -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if isinstance(d,dict) and d else 1)" "$tmp" 2>/dev/null; then
        mv -f "$tmp" "$BASE/$REL"
        log "INSTALLED $REL from $H: mac was ${mac_age}s, $H was ${r_age}s"
        got=1
        break
      fi
      log "REJECTED $REL from $H: not valid non-empty JSON"
    fi
    rm -f "$tmp" 2>/dev/null
  done
  if [ "$got" = 0 ]; then
    mkdir -p "$(dirname "$ALERT")" 2>/dev/null
    printf '{"ts":%s,"kind":"market_data_stale","mac_age_s":%s,"max_age_s":%s,"msg":"no host had a fresher %s — live engines are deciding on stale indicators"}\n' \
      "$(date +%s)" "$mac_age" "$MAX_AGE_S" "$REL" >> "$ALERT" 2>/dev/null
    log "ALERT: mac $REL ${mac_age}s stale and NO host had a fresher copy (S1 producers may still be warming up)"
  fi
  sleep "$SLEEP_S"
done
