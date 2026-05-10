#!/bin/bash
# s1_launch_capacity.sh — fill S1 to ≥60% utilization mandate (USER 2026-05-09).
# Each backtest_v8_sweep is mostly I/O bound, so we need MANY parallel processes
# to saturate. Targets: 8-10 process families × ~1 sweep variant each.
#
# Designed for cron (every 15min) + manual invocation. Each batch uses a unique
# symbol set to avoid NPZ overlap. setsid ensures detach survives ssh hangup.
# mem-throttle-pct=78 → workers self-exit only if memory really tight; bumped from 55 (was causing constant exit/relaunch cycles at ~70% steady-state mem). 2026-05-10 USER mandate.
#
# State: total sweep procs measured before launching. If already ≥18, skip
# (we're saturated; let watchdogs handle replacement when one dies).

LOG=/home/niels/logs/s1_launch_capacity.log
TS=$(date -u "+%Y-%m-%d %H:%M:%S UTC")
DIR=/home/niels/binance-sandbox
PYTHON=/home/niels/.conda/envs/binance_env/bin/python
TARGET_PROCS=18

NCURRENT=$(pgrep -afc "backtest_v8_sweep" 2>/dev/null || echo 0)
if [ "$NCURRENT" -ge "$TARGET_PROCS" ]; then
    echo "[$TS] saturated: $NCURRENT >= $TARGET_PROCS sweep procs, skipping" >> "$LOG"
    exit 0
fi

NEED=$((TARGET_PROCS - NCURRENT))
echo "[$TS] need $NEED more sweeps to hit $TARGET_PROCS (currently $NCURRENT)" >> "$LOG"

cd "$DIR" || exit 1

# Helper: launch one backtest_v8_sweep with setsid + mem throttle
launch() {
    local MODE=$1 TAG=$2 SYMS=$3
    local FTS=$(date +%Y%m%d_%H%M%S)
    local ACCT="trb"; [ "$MODE" = "crypto" ] && ACCT="ang"
    local TIER="tradier_param_hunt"; [ "$MODE" = "crypto" ] && TIER="system_combo"
    setsid nohup env V8_RATE_GUARD_DISABLED=1 "$PYTHON" backtest_v8_sweep.py \
        --mode "$MODE" --account "$ACCT" --start 2026-01-01 \
        --symbols "$SYMS" --tier "$TIER" --workers 1 --timeout 5400 --mem-throttle-pct 78 \
        > /tmp/bt_${MODE}_${TAG}_${FTS}.log 2>&1 < /dev/null &
    disown
    echo "[$TS] launched $MODE $TAG (${SYMS:0:50})" >> "$LOG"
}

# Stock symbol partitions (10 per batch, no overlap with watchdog CORE20).
# CORE20 (managed by watchdog_sweep_s1.sh): AAPL,AMZN,AVGO,AMD,ADBE,ABNB,ARM,ASML,AXON,BA,BABA,ABBV,ABT,ADP,ADM,AEM,AG,AGCO,ALB,ASTS
TRADIER_PARTITIONS=(
    "B|AA,ACN,AGI,AM,APA,APO,AR,ASC,ATI,APP"
    "C|BG,BHP,BIDU,BITO,BK,BKR,BLOK,BNO,BOIL,BOTZ"
    "D|BWXT,CALM,CAT,CCJ,CDE,CENX,CF,CHRD,CIBR,CLF"
    "E|CLX,CMC,CME,COIN,COP,COPX,CORN,COST,CRK,CRM"
    "F|CRWD,CRWV,CTRA,CTVA,CVS,CVX,DAC,DAR,DBA,DE"
    "G|DHR,DHT,DINO,DIS,DNN,DUOL,DVN,EGLE"
)
# Crypto partitions (no overlap with watchdog CORE4: BTCUSDC,ETHUSDC,SOLUSDC,XRPUSDC).
CRYPTO_PARTITIONS=(
    "B|ADAUSDC,BNBUSDC,AVAXUSDC,LINKUSDC,LTCUSDC,UNIUSDC"
    "C|DOGEUSDT,1INCHUSDT,ALGOUSDT,ATOMUSDT,DOTUSDT,VETUSDT"
)

# Walk partitions in ROUND-ROBIN to keep both modes alternating
ALL_PARTITIONS=()
for ((i=0; i<${#TRADIER_PARTITIONS[@]}; i++)); do
    ALL_PARTITIONS+=("tradier|${TRADIER_PARTITIONS[$i]}")
    if [ "$i" -lt "${#CRYPTO_PARTITIONS[@]}" ]; then
        ALL_PARTITIONS+=("crypto|${CRYPTO_PARTITIONS[$i]}")
    fi
done

# Launch up to NEED batches, skipping any already in proc list
launched=0
for entry in "${ALL_PARTITIONS[@]}"; do
    [ "$launched" -ge "$NEED" ] && break
    MODE=$(echo "$entry" | cut -d'|' -f1)
    TAG=$(echo "$entry" | cut -d'|' -f2)
    SYMS=$(echo "$entry" | cut -d'|' -f3)
    # Skip if a sweep with these exact symbols is already running
    FIRST_SYM=$(echo "$SYMS" | cut -d',' -f1)
    if pgrep -af "backtest_v8_sweep.*--symbols ${SYMS}" >/dev/null 2>&1; then
        continue
    fi
    launch "$MODE" "$TAG" "$SYMS"
    launched=$((launched + 1))
    sleep 8  # stagger NPZ decompression peaks
done

# Final report
sleep 5
NAFTER=$(pgrep -afc "backtest_v8_sweep" 2>/dev/null || echo 0)
CPU=$(top -bn1 | grep "Cpu(s)" | awk '{print $2}')
MEM=$(free -m | awk '/^Mem:/ {printf "%.0f", $3/$2*100}')
echo "[$TS] launched=$launched | procs $NCURRENT->$NAFTER | CPU=${CPU}% MEM=${MEM}%" >> "$LOG"
