#!/bin/bash
# v15_filter_discovery_cron.sh v2 — per-host supervisor of the filter discovery (Agent Y). flock singleton; cron */3. Shares the host with the SWEEP:
# discovery budget ~50% CPU = 2 processes: B (binding rows x active filters, all sym_sides, 3 workers) + F (FULL GRID every row x every wired filter on a few
# sym_sides, 5 workers). Never kills anything. MemAvailable >= 6 GB to launch. Incremental: template md5 (FD_TEMPLATE_DIR) in the marker names, results keyed by
# override-set hash (never recomputed); engine change -> tools/v15_fd_invalidate.py drops only the affected filters' results (see LOG.md).
cd ~/binance-sandbox 2>/dev/null || exit 3
exec 7>/tmp/v15_fd_cron.lock
flock -n 7 || exit 0
DATE=${FD_DATE:-20261001}
DIR=data/yellow_discovery/$DATE/fd
MAN=data/yellow_discovery/manifest.json
[ -f "$MAN" ] || exit 0
export FD_TEMPLATE_DIR=${FD_TEMPLATE_DIR:-$HOME/binance-sandbox/SPREADSHEETS/TEMPLATE_FD}
for _c in CRYPTO_LONG CRYPTO_SHORT STOCKS_LONG STOCKS_SHORT; do [ -s "$FD_TEMPLATE_DIR/TEMPLATE_$_c.xlsx" ] || { FD_TEMPLATE_DIR=$HOME/binance-sandbox/SPREADSHEETS; break; }; done
export FD_TEMPLATE_DIR
TM=$(md5sum $FD_TEMPLATE_DIR/TEMPLATE_CRYPTO_LONG.xlsx $FD_TEMPLATE_DIR/TEMPLATE_CRYPTO_SHORT.xlsx $FD_TEMPLATE_DIR/TEMPLATE_STOCKS_LONG.xlsx $FD_TEMPLATE_DIR/TEMPLATE_STOCKS_SHORT.xlsx 2>/dev/null | md5sum | cut -c1-8)
EM=$(md5sum v12_quick_engine.py data/vec_unwired.json 2>/dev/null | md5sum | cut -c1-8)
mkdir -p "$DIR" logs
# engine change detection (v12_quick_engine.py + vec_unwired.json md5): selective invalidation if the wiring agent listed the changed filters, else archive + full recompute
if [ -f "$DIR/../engine_state" ] && [ "$(cat $DIR/../engine_state)" != "$EM" ]; then
  if ! pgrep -f "v15_filter_discovery.py" >/dev/null; then
    if [ -f data/engine_deploy/changed_filters.json ]; then .venv/bin/python tools/v15_fd_invalidate.py --dir $DIR --filters-file data/engine_deploy/changed_filters.json >> logs/fd_supervisor.log 2>&1
    else .venv/bin/python tools/v15_fd_invalidate.py --dir $DIR --all --tag eng_$(cat $DIR/../engine_state) >> logs/fd_supervisor.log 2>&1; mkdir -p $DIR; fi
    echo $EM > $DIR/../engine_state
  else echo "$(date -u +%FT%TZ) engine changed but discovery procs still running: waiting" >> logs/fd_supervisor.log; exit 0; fi
fi
[ -f "$DIR/../engine_state" ] || echo $EM > $DIR/../engine_state
AV=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
WB=${FD_WORKERS_B:-3}; WF=${FD_WORKERS_F:-5}
FL=${FD_F_LIMIT:-crypto:1,stocks:2}
launch() { # name stage workers extra
  local name=$1 stage=$2 w=$3; shift 3
  [ -f "$DIR/${name}_${TM}_${EM}.done" ] && return
  pgrep -f "v15_filter_discovery.py .*--stage $stage .*--shard 0/1" >/dev/null && return
  [ "$AV" -lt 6000 ] && { echo "$(date -u +%FT%TZ) skip $name: MemAvailable ${AV}MB" >> logs/fd_supervisor.log; return; }
  echo "$(date -u +%FT%TZ) launch $name stage $stage workers $w tpl $TM eng $EM $*" >> logs/fd_supervisor.log
  setsid nohup bash -c ".venv/bin/python -u tools/v15_filter_discovery.py --manifest $MAN --stage $stage --window 365 --workers $w --shard 0/1 --nice 5 --dir $DIR $* >> logs/fd_${name}.log 2>&1 && touch $DIR/${name}_${TM}_${EM}.done" > /dev/null 2>&1 < /dev/null &
}
launch B B "$WB"
launch F F "$WF" --f-limit "$FL"
exit 0
