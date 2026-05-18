#!/bin/bash
# launch_samplefloor_coords.sh
# Waits for current crypto coordinator (PID 259492) to finish, plus mem headroom,
# then launches sample-floor crypto (60-sym) + tradier (113-sym) coordinators.
# Per CLAUDE.md sample-floor mandate: crypto >=48, tradier >=100.
# Generated 2026-05-18 — queue-mode (don't kill running sweeps).
set -u

WAIT_PID="${1:-259492}"
TS=$(date -u +%Y%m%d_%H%M%S)
WATCH_LOG="/home/niels/logs/coord_samplefloor_watcher_${TS}.log"
MIN_FREE_MB=8000
POLL_S=120

exec > >(tee -a "$WATCH_LOG") 2>&1

echo "[watcher] start ts=$TS pid_to_wait=$WAIT_PID min_free_mb=$MIN_FREE_MB poll_s=$POLL_S"

# (1) Wait for coord PID to exit
while kill -0 "$WAIT_PID" 2>/dev/null; do
  echo "[watcher] $(date -u +%H:%M:%S) PID $WAIT_PID still alive, sleeping ${POLL_S}s"
  sleep "$POLL_S"
done
echo "[watcher] $(date -u +%H:%M:%S) PID $WAIT_PID exited"

# (2) Wait for mem headroom (free + buff/cache available >= MIN_FREE_MB)
while true; do
  AVAIL=$(free -m | awk '/^Mem:/{print $7}')
  if [ "$AVAIL" -ge "$MIN_FREE_MB" ]; then
    echo "[watcher] $(date -u +%H:%M:%S) mem available=${AVAIL}MB >= ${MIN_FREE_MB}MB, proceed"
    break
  fi
  echo "[watcher] $(date -u +%H:%M:%S) mem available=${AVAIL}MB < ${MIN_FREE_MB}MB, sleeping ${POLL_S}s"
  sleep "$POLL_S"
done

cd /home/niels/binance-sandbox || { echo "[watcher] FATAL cd failed"; exit 1; }

# Crypto sample-floor: 10 USDC majors + 50 USDT legacy = 60 syms
CRYPTO_SYMS='ETHUSDC,BTCUSDC,SOLUSDC,ADAUSDC,BNBUSDC,AVAXUSDC,XRPUSDC,LINKUSDC,LTCUSDC,UNIUSDC,1INCHUSDT,ALGOUSDT,ANKRUSDT,ATOMUSDT,AXSUSDT,BANDUSDT,BATUSDT,BELUSDT,BTCDOMUSDT,C98USDT,CELRUSDT,CHRUSDT,COMPUSDT,COTIUSDT,DASHUSDT,DOTUSDT,EGLDUSDT,ENJUSDT,ETCUSDT,GRTUSDT,GTCUSDT,HOTUSDT,IOSTUSDT,IOTAUSDT,IOTXUSDT,KAVAUSDT,KNCUSDT,KSMUSDT,LRCUSDT,MANAUSDT,MTLUSDT,NKNUSDT,QTUMUSDT,RLCUSDT,RSRUSDT,RVNUSDT,SANDUSDT,SKLUSDT,SNXUSDT,STORJUSDT,SUSHIUSDT,SXPUSDT,THETAUSDT,TRXUSDT,VETUSDT,XLMUSDT,XMRUSDT,XTZUSDT,YFIUSDT,ZENUSDT'

# Tradier sample-floor: union(trb_long|trb_short|trc_long|trc_short) intersected with NPZ-present
TRADIER_SYMS='ABT,ACN,ADBE,AEM,AG,AGCO,AGI,ALB,AMD,AMZN,AR,ARM,ASTS,AU,AVGO,AXON,BG,BHP,BNO,BOTZ,CDE,CF,CHRD,CIBR,CLF,CMC,COP,CRK,CRWD,CTRA,CVX,DBA,DBC,DE,DKL,DLTR,DVN,EOG,EPD,EQT,ETN,EWZ,EXPE,FANG,FCEL,FCG,FCX,FHI,FNV,FTI,GE,GLD,GOOG,GOOGL,HAL,HD,HES,IBM,INTC,IOT,IWM,KMI,KOLD,KOPN,LIT,LOW,MARA,MCO,META,MNST,MOS,MP,MRVL,MSFT,MSTR,MU,NEM,NEM_LONG,NFLX,NUE,NVDA,OIH,OKE,ORCL,OVV,OXY,PAA,PANW,PCT,PLUG,PR,PSX,PXD,QCOM,QQQ,REGN,ROBO,ROIV,RTX,SAN,SHEL,SHOP,SLB,SMCI,SMH,SQ,STX,SU,SWN,TLT,TMUS,TQQQ,TSLA,TSM,TXN,U,UAN,UNG,UNH,USO,V,VRT,WMT,WTI,XLE,XLF,XOM,XOP,Z,ZIM'

# Filter both lists to only syms with NPZ present
filter_present() {
  local list="$1"
  local out=""
  IFS=',' read -ra arr <<< "$list"
  for s in "${arr[@]}"; do
    if [ -f "/home/niels/binance-sandbox/backtest_v8/indicators/${s}.npz" ]; then
      if [ -z "$out" ]; then out="$s"; else out="${out},${s}"; fi
    fi
  done
  echo "$out"
}
CRYPTO_SYMS=$(filter_present "$CRYPTO_SYMS")
TRADIER_SYMS=$(filter_present "$TRADIER_SYMS")
CRYPTO_N=$(echo "$CRYPTO_SYMS" | tr ',' '\n' | wc -l)
TRADIER_N=$(echo "$TRADIER_SYMS" | tr ',' '\n' | wc -l)
echo "[watcher] crypto_syms=${CRYPTO_N} tradier_syms=${TRADIER_N}"

if [ "$CRYPTO_N" -lt 48 ]; then
  echo "[watcher] FATAL crypto NPZ count ${CRYPTO_N} below sample-floor 48 — abort"
  exit 1
fi
if [ "$TRADIER_N" -lt 100 ]; then
  echo "[watcher] FATAL tradier NPZ count ${TRADIER_N} below sample-floor 100 — abort"
  exit 1
fi

# (3) Launch crypto coord
CRYPTO_LOG="/home/niels/logs/coord_crypto_samplefloor_${TS}.log"
echo "[watcher] $(date -u +%H:%M:%S) launching CRYPTO coord (n_syms=${CRYPTO_N}) → $CRYPTO_LOG"
nohup /home/niels/.conda/envs/binance_env/bin/python sweep_coordinator.py \
  --mode crypto --account ang --start 2022-01-01 \
  --symbols "$CRYPTO_SYMS" \
  --capital 10000.0 --mem-throttle 85 \
  > "$CRYPTO_LOG" 2>&1 < /dev/null & disown
CRYPTO_PID=$!
echo "[watcher] CRYPTO_COORD_PID=$CRYPTO_PID"

# (4) Stagger 60s, check mem again before tradier
sleep 60
AVAIL=$(free -m | awk '/^Mem:/{print $7}')
if [ "$AVAIL" -lt 4000 ]; then
  echo "[watcher] mem available=${AVAIL}MB <4000 after crypto launch — DEFER tradier coord (rerun script later or launch manually)"
  echo "[watcher] CRYPTO_COORD_PID=$CRYPTO_PID log=$CRYPTO_LOG"
  exit 0
fi

# (5) Launch tradier coord
TRADIER_LOG="/home/niels/logs/coord_tradier_samplefloor_${TS}.log"
echo "[watcher] $(date -u +%H:%M:%S) launching TRADIER coord (n_syms=${TRADIER_N}) → $TRADIER_LOG"
nohup /home/niels/.conda/envs/binance_env/bin/python sweep_coordinator.py \
  --mode tradier --account trb --start 2024-01-01 \
  --symbols "$TRADIER_SYMS" \
  --capital 10000.0 --mem-throttle 85 \
  > "$TRADIER_LOG" 2>&1 < /dev/null & disown
TRADIER_PID=$!
echo "[watcher] TRADIER_COORD_PID=$TRADIER_PID"

echo "[watcher] DONE crypto_pid=$CRYPTO_PID tradier_pid=$TRADIER_PID"
echo "[watcher] crypto_log=$CRYPTO_LOG"
echo "[watcher] tradier_log=$TRADIER_LOG"
