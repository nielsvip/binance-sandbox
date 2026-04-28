#!/bin/bash
# crypto_watchdog_cron.sh — 24/7 crypto system watchdog
# Runs every 2 minutes via crontab
# 1. Ensures ALL crypto processes are alive
# 2. Checks recent trade decisions for "stupid" actions (closing at loss, hedge churn)
# 3. Alerts if any account is losing money against STRICT_NO_LOSS rules
# 4. Restarts crashed processes immediately

WORKDIR="/Users/niels/Documents/binance"
PYTHON="/opt/anaconda3/envs/binance_env/bin/python"
LOGDIR="/Users/niels/logs"
LOG="$LOGDIR/crypto_watchdog_cron.log"
ALERT_LOG="$LOGDIR/crypto_watchdog_alerts.log"
DECISIONS_DIR="$WORKDIR/data/decisions"

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }
alert() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] ALERT: $1" >> "$ALERT_LOG"; echo "[$(date +'%Y-%m-%d %H:%M:%S')] ALERT: $1" >> "$LOG"; }

# === 1. PROCESS HEALTH ===
# NOTE: ez_positions_quick.py and ez_positions.py are managed by ez_positions_watchdog.py
# Do NOT add them here — duplicates cause memory explosion and MacBook crashes.
# Similarly, ez_positions_realtime_*.py and tradier_rankings.py are watchdog-managed.
CRITICAL_PROCESSES=(
    "ez_manage.py --account ang"
    "ez_manage.py --account inf"
    "ez_manage.py --account men"
    "ez_manage.py --account flz"
    "ez_manage.py --account fin"
    "ez_prices.py"
    "ez_indicators.py"
    "ez_market_data.py"
    "ez_klines.py"
    "setup_ssh_tunnels.py"
    "ez_orderbook.py"  # 2026-04-27 added — was dying silently w/o auto-restart, killing V3 OB scanner
)

# Scripts that ALREADY have a run_with_watchdog.sh wrapper in an iTerm tab.
# For these: only restart if BOTH the python process AND the wrapper are dead.
# If the wrapper is alive, it will restart the python process itself.
# When the cron does need to start one of these, it launches them WRAPPED in
# run_with_watchdog.sh (see launch loop below) so they auto-restart on RSS/crash.
WATCHDOG_MANAGED=(
    "ez_prices.py"
    "ez_indicators.py"
    "ez_market_data.py"
    "ez_klines.py"
    "ez_orderbook.py"  # 2026-04-27 added — RSS recycle ceiling 1.5GB applies via watchdog
    "ez_manage.py"     # 2026-04-27 added — RSS recycle ceiling 1.5GB applies via watchdog (was bare)
)

MISSING=""
for proc in "${CRITICAL_PROCESSES[@]}"; do
    if ! pgrep -f "$proc" >/dev/null 2>&1; then
        script_name="${proc%% *}"
        args="${proc#* }"
        if [ "$script_name" = "$args" ]; then args=""; fi
        # Check if this script has a live run_with_watchdog wrapper
        is_watchdog_managed=false
        for wm in "${WATCHDOG_MANAGED[@]}"; do
            if [ "$script_name" = "$wm" ]; then
                is_watchdog_managed=true
                break
            fi
        done
        if $is_watchdog_managed && pgrep -f "run_with_watchdog.*$script_name" >/dev/null 2>&1; then
            # Wrapper alive — it will restart the process. Don't interfere.
            log "SKIP: $proc — run_with_watchdog wrapper alive, will self-heal"
            continue
        fi
        MISSING="$MISSING $proc"
        log "RESTARTING: $proc"
        cd "$WORKDIR"
        # Log to file instead of /dev/null so we can see crash reasons
        LOG_NAME=$(echo "$script_name" | sed 's/\.py//')
        if [ -n "$args" ]; then
            LOG_NAME="${LOG_NAME}_$(echo "$args" | tr ' ' '_' | tr '-' '_')"
        fi
        # 2026-04-27: launch under run_with_watchdog.sh for WATCHDOG_MANAGED scripts so
        # they get RSS-recycle protection (1.5GB ceiling) and auto-restart on crash.
        # Bare python only for non-managed scripts (e.g. setup_ssh_tunnels.py).
        if $is_watchdog_managed; then
            log "  → launching under run_with_watchdog.sh"
            nohup /bin/bash "$WORKDIR/run_with_watchdog.sh" $script_name $args >> "$LOGDIR/${LOG_NAME}_cron.log" 2>&1 &
        else
            nohup $PYTHON -u $script_name $args >> "$LOGDIR/${LOG_NAME}_cron.log" 2>&1 &
        fi
        sleep 2
    fi
done

if [ -z "$MISSING" ]; then
    # Only log heartbeat every 10 min (not every 2 min)
    MINUTE=$(date +%M)
    if [ $((MINUTE % 10)) -eq 0 ]; then
        log "HEARTBEAT: All crypto processes healthy"
    fi
else
    alert "Restarted missing processes:$MISSING"
fi

# === 2. REDIS HEALTH ===
if ! /opt/homebrew/bin/redis-cli -p 6379 ping >/dev/null 2>&1; then
    alert "LOCAL REDIS (6379) DOWN!"
fi
if ! /opt/homebrew/bin/redis-cli -p 6381 ping >/dev/null 2>&1; then
    # Restart tunnels
    if ! pgrep -f "setup_ssh_tunnels.py" >/dev/null 2>&1; then
        cd "$WORKDIR"
        nohup python3 -u setup_ssh_tunnels.py > /dev/null 2>&1 &
        alert "SSH tunnels were dead — restarted"
    fi
fi

# === 2b. TRADIER — MARKET HOURS ONLY (9:15 AM - 4:15 PM ET) ===
ET_HOUR=$(TZ="America/New_York" date +"%H")
ET_MIN=$(TZ="America/New_York" date +"%M")
ET_MINS=$((ET_HOUR * 60 + ET_MIN))
DOW=$(date +%u)
if [ "$DOW" -le 5 ] && [ "$ET_MINS" -ge 555 ] && [ "$ET_MINS" -le 975 ]; then
    for tproc in "tradier_manage.py --accounts trb" "tradier_manage.py --accounts trc"; do
        if ! pgrep -f "$tproc" >/dev/null 2>&1; then
            script_name="${tproc%% *}"
            args="${tproc#* }"
            # Don't create bare process if wrapper is alive
            if pgrep -f "run_with_watchdog.*$script_name.*$args" >/dev/null 2>&1; then
                log "SKIP TRADIER: $tproc — wrapper alive"
                continue
            fi
            log "RESTARTING TRADIER: $tproc"
            cd "$WORKDIR"
            nohup $PYTHON -u $script_name $args > /dev/null 2>&1 &
            sleep 2
            alert "Tradier $args was dead — restarted"
        fi
    done
fi

# === 2c. TRADIER OPTIONS OI FETCHER (READ-ONLY 24/7 daemon) ===
# Refreshes data/stocks_oi_cache/{sym}.json hourly via Tradier options chain endpoint.
# READ-ONLY — never opens options orders. Per feedback_oi_signal_only_no_options_trading_20260427.md.
# Runs 24/7 (not market-hours gated) — OI publishes daily and we want overnight refreshes too.
if ! pgrep -f "tradier_options_oi_fetcher.py" >/dev/null 2>&1; then
    log "RESTARTING: tradier_options_oi_fetcher.py"
    cd "$WORKDIR"
    nohup $PYTHON -u tradier_options_oi_fetcher.py > "$LOGDIR/tradier_oi_fetcher_cron.log" 2>&1 &
    sleep 2
    alert "tradier_options_oi_fetcher was dead — restarted"
fi

# === 2d. EZ_VOLUME_PROFILE (crypto deep heatmap, READ-ONLY 24/7 daemon) ===
# Builds vol_profile:<sym> Redis keys (1500×15m bars × ±50% range × 1% buckets, top-5 HVNs).
# Consumed by VP_GATE in ez_positions_quick (currently default OFF after sweep validation).
# Has been dying repeatedly without auto-restart — adding here per "regress to qualifying setup" rule.
if ! pgrep -f "ez_volume_profile.py" >/dev/null 2>&1; then
    log "RESTARTING: ez_volume_profile.py"
    cd "$WORKDIR"
    nohup $PYTHON -u ez_volume_profile.py > "$LOGDIR/ez_volume_profile_cron.log" 2>&1 &
    sleep 2
    alert "ez_volume_profile was dead — restarted"
fi

# === 3. TRADE QUALITY CHECK ===
# Check last 5 minutes of decisions for STRICT_NO_LOSS violations
TODAY=$(date +%Y%m%d)
for acct in ang inf men flz fin; do
    DECISIONS_FILE="$DECISIONS_DIR/decisions_${acct}_${TODAY}.jsonl"
    if [ -f "$DECISIONS_FILE" ]; then
        # Count closes/reduces in last 5 min with negative gain
        RECENT_LOSSES=$($PYTHON -c "
import json, time
from datetime import datetime, timezone, timedelta
cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
count = 0
with open('$DECISIONS_FILE') as f:
    for line in f:
        try:
            d = json.loads(line)
            ts = d.get('timestamp', '')
            if isinstance(ts, str) and 'T' in ts:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                if dt > cutoff:
                    action = d.get('action', '')
                    reason = str(d.get('reason', ''))
                    if action in ('CLOSE', 'REDUCE', 'QUICK_CLOSE', 'QUICK_REDUCE'):
                        import re
                        gain_match = re.search(r'[Gg]ain[=_]?(-?\d+\.?\d*)', reason)
                        if gain_match and float(gain_match.group(1)) < 0:
                            count += 1
        except: pass
print(count)
" 2>/dev/null)
        if [ "$RECENT_LOSSES" -gt 0 ] 2>/dev/null; then
            alert "$acct: $RECENT_LOSSES positions closed at LOSS in last 5 min! STRICT_NO_LOSS VIOLATION"
        fi
        # Count hedge churn (>10 hedge actions in 5 min)
        HEDGE_COUNT=$($PYTHON -c "
import json
from datetime import datetime, timezone, timedelta
cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
count = 0
with open('$DECISIONS_FILE') as f:
    for line in f:
        try:
            d = json.loads(line)
            ts = d.get('timestamp', '')
            if isinstance(ts, str) and 'T' in ts:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                if dt > cutoff and 'HEDGE' in str(d.get('reason', '')):
                    count += 1
        except: pass
print(count)
" 2>/dev/null)
        if [ "$HEDGE_COUNT" -gt 10 ] 2>/dev/null; then
            alert "$acct: $HEDGE_COUNT hedge actions in 5 min — HEDGE CHURN DETECTED"
        fi
    fi
done

# === 4. LOG HEALTH CHECK — detect error patterns in running processes ===
# Scans last 100 lines of each log for critical patterns. Restarts if fixable.

# 4a. Watchdog crash loops — reset the restart counter so cron can recover
for wlog in "$LOGDIR"/ez_manage_*_watchdog.log; do
    [ ! -f "$wlog" ] && continue
    acct=$(basename "$wlog" | sed 's/ez_manage_//;s/_watchdog.log//')
    if tail -5 "$wlog" 2>/dev/null | grep -q "Too many rapid restarts"; then
        # Watchdog gave up. Check if the python process is alive anyway (cron may have restarted it).
        if ! pgrep -f "ez_manage.py --account $acct" >/dev/null 2>&1; then
            alert "$acct: Watchdog gave up AND python dead — starting bare process"
            cd "$WORKDIR"
            nohup $PYTHON -u ez_manage.py --account $acct > /dev/null 2>&1 &
            sleep 2
        fi
    fi
done

# 4b. DNS failures in ez_market_data — restart if stuck
if pgrep -f "ez_market_data.py" >/dev/null 2>&1; then
    DNS_ERRS=$(tail -50 "$LOGDIR/ez_market_data.log" 2>/dev/null | grep -c "DNS\|resolve\|Connection refused" 2>/dev/null)
    if [ "$DNS_ERRS" -gt 3 ] 2>/dev/null; then
        alert "ez_market_data: $DNS_ERRS DNS/connection errors — restarting"
        pkill -f "ez_market_data.py" 2>/dev/null
        sleep 2
        cd "$WORKDIR"
        nohup $PYTHON -u ez_market_data.py > /dev/null 2>&1 &
    fi
fi

# 4c. ez_indicators producing stale data — check Redis freshness
STALE_CHECK=$($PYTHON -c "
import redis, json, time
try:
    r = redis.Redis(port=6379, socket_timeout=2)
    hm = r.get('hot_metrics:1000FLOKIUSDT')
    if hm:
        d = json.loads(hm)
        age = time.time() - d.get('_tick_ts', 0)
        print(int(age))
    else:
        print(9999)
    r.close()
except:
    print(9999)
" 2>/dev/null)
if [ "$STALE_CHECK" -gt 7200 ] 2>/dev/null; then
    alert "hot_metrics stale (${STALE_CHECK}s) — but NOT killing (kill-loop disabled 2026-04-28). Investigate manually."
fi

# 4d. IP ban detection — check for -1003 errors
for acct in ang inf men flz fin; do
    BAN_CHECK=$(tail -200 "$LOGDIR/ez_positions_quick_general_${acct}.log" 2>/dev/null | grep -c "\-1003\|banned\|IP ban" 2>/dev/null)
    if [ "$BAN_CHECK" -gt 0 ] 2>/dev/null; then
        alert "$acct: IP BAN DETECTED ($BAN_CHECK occurrences) — MANUAL INTERVENTION NEEDED"
    fi
done
