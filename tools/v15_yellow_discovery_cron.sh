#!/bin/bash
# v15_yellow_discovery_cron.sh — per-host supervisor of the 365D yellow-cell discovery (Agent Y, 2026-10-01). Idempotent, flock singleton, safe to run
# from cron every 5 min on EACH host (s2 = stocks, s5 = crypto, s1 = nothing unless YD_CATS is set). Never kills anything; never touches sweep processes.
# Stages (marker files in $DIR): P1 (--sym-slice 0:2, tier yellow|all) -> P2 (--sym-slice 2:5 --only-pos-cells) -> crypto only: T1 token tier pass-1 (when the sweep is quiet).
# Guards: MemAvailable >= 6 GB to launch; workers <= N (default 4); one audit process per host at a time.
cd ~/binance-sandbox 2>/dev/null || exit 3
exec 7>/tmp/v15_yellow_disc.lock
flock -n 7 || exit 0
HOST=$(hostname)
DATE=${YD_DATE:-20261001}
DIR=data/yellow_discovery/$DATE/audit365
mkdir -p "$DIR" logs
case "$HOST" in
  s2) CATS=${YD_CATS:-"STOCKS_LONG STOCKS_SHORT"}; TIER1=all;;
  s5) CATS=${YD_CATS:-"CRYPTO_LONG CRYPTO_SHORT"}; TIER1=yellow;;
  *)  CATS=${YD_CATS:-""}; TIER1=yellow;;
esac
[ -z "$CATS" ] && exit 0
N=${YD_WORKERS:-4}
# something already running -> nothing to do
pgrep -f "[v]15_yellow_365_audit.py" >/dev/null 2>&1 && exit 0
AV=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
[ "$AV" -lt 6000 ] && { echo "$(date -u +%FT%TZ) skip: MemAvailable ${AV}MB < 6000" >> logs/yellow_disc_supervisor.log; exit 0; }
CATARGS=""; for c in $CATS; do CATARGS="$CATARGS --cat-side $c"; done
launch() { # $1 marker $2.. args
  local marker=$1; shift
  echo "$(date -u +%FT%TZ) launch $marker: $*" >> logs/yellow_disc_supervisor.log
  setsid nohup bash -c ".venv/bin/python -u tools/v15_yellow_365_audit.py $CATARGS --syms 5 --workers $N --include-token-cells --dir $DIR $* >> logs/yellow_disc_${HOST}.log 2>&1 && touch $DIR/$marker" > /dev/null 2>&1 < /dev/null &
}
if [ ! -f "$DIR/P1_${HOST}.done" ]; then launch "P1_${HOST}.done" --sym-slice 0:2 --tier $TIER1; exit 0; fi
if [ ! -f "$DIR/P2_${HOST}.done" ]; then launch "P2_${HOST}.done" --sym-slice 2:5 --only-pos-cells --tier $TIER1; exit 0; fi
if [ "$TIER1" = "yellow" ] && [ ! -f "$DIR/T1_${HOST}.done" ]; then
  PILOTS=$(pgrep -fc "[v]15_pilot.py --sym-side")
  [ "$PILOTS" -ge 8 ] && exit 0   # token tier on crypto is heavy: only when the sweep is quiet
  launch "T1_${HOST}.done" --sym-slice 0:2 --tier token; exit 0
fi
exit 0
