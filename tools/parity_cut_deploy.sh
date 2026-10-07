#!/bin/bash
# PARITY CUT DEPLOY (director, 2026-10-06). DRY-RUN by default; `--apply` executes. Run on the Mac from the repo root.
# Order: gate check -> backups on every host -> rsync files -> md5 verify -> snapshot cleanups -> restore paused crons/launchd
#        -> new defaults round. Live process restarts are a separate explicit step (--restart-live), never implicit.
set -u
APPLY=0; RESTART=0
for a in "$@"; do [ "$a" = "--apply" ] && APPLY=1; [ "$a" = "--restart-live" ] && RESTART=1; done
TS=$(date +%Y%m%d%H%M)
HOSTS="s1-pub s2 s5"
SSH="ssh -o ConnectTimeout=20"
RS="rsync -az -e 'ssh -S none -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20'"
FILES="v12_quick_engine.py backtest_v12_engine.py ez_manage.py ez_positions_quick.py config.py config_tradier.py tradier_manage.py tradier_filter_tf_twins.py tradier_indicators.py v15_pilot.py cat_side_defaults.py"
DIRS="vec_decisions live_twins tools"
TPL="SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx"
run() { echo "+ $*"; [ $APPLY = 1 ] && eval "$@"; }

echo "== 0. GATE: trade-level parity must PASS on the frozen sample with PARITY_VEC_EXACT_MODE (data/parity/loop_*_log.md)"
grep -h "VERDICT_SAMPLE" data/parity/loop_crypto_log.md data/parity/loop_stocks_log.md 2>/dev/null | tail -2
echo "   (director confirms the gate before --apply)"

echo "== 1. compile on Mac"
for f in $FILES; do python -c "import py_compile; py_compile.compile('$f', doraise=True)" || { echo "COMPILE FAIL $f"; exit 1; }; done

echo "== 2. backups + rsync + md5 per host"
for h in $HOSTS; do
  run "$SSH $h 'cd ~/binance-sandbox && mkdir -p backups/parity_cut_$TS && cp -p $FILES backups/parity_cut_$TS/ 2>/dev/null; tar czf backups/parity_cut_$TS/dirs.tgz vec_decisions live_twins tools 2>/dev/null; cp -p $TPL backups/parity_cut_$TS/ 2>/dev/null; true'"
  for f in $FILES; do run "$RS $f $h:binance-sandbox/$f"; done
  for d in $DIRS; do run "$RS $d/ $h:binance-sandbox/$d/"; done
  for t in $TPL; do run "$RS $t $h:binance-sandbox/$t"; done
  if [ $APPLY = 1 ]; then
    for f in $FILES; do L=$(md5 -q $f); R=$($SSH $h "md5sum ~/binance-sandbox/$f" | cut -c1-32); [ "$L" = "$R" ] || echo "MD5 MISMATCH $h $f"; done
    $SSH $h "cd ~/binance-sandbox && .venv/bin/python -c 'import v12_quick_engine, v15_pilot; print(\"import ok\")'"
  fi
done

echo "== 3. stale per-sym snapshot cleanups (promotions untouched)"
run "python tools/parity_persym_snapshot_cleanup.py --apply"
run "python tools/parity_persym_snapshot_cleanup_stocks.py --apply"

echo "== 4. restore paused crons + launchd (fleet coordinator, herds, gain pusher)"
for h in s1-pub s2; do run "$SSH $h 'crontab -l | sed -E \"s/^#PARITY_PAUSE_20261006# //\" | crontab - && crontab -l | grep -c PARITY_PAUSE_20261006'"; done
run "crontab -l | sed -E 's/^#PARITY_PAUSE_20261006# //' | crontab -"
run "launchctl load ~/Library/LaunchAgents/com.niels.fleet-healer.plist"
echo "   new defaults round id for the relaunched sweeps: V15_DEFAULTS_ROUND=parity_cut_$TS (fresh progress dir; never chain across the cut)"

if [ $RESTART = 1 ]; then
  echo "== 5. live restart (explicit): crypto accounts via run_with_watchdog (SIGTERM, watchdog respawns), tradier stack via its watchdog"
  for p in $(pgrep -f "ez_manage[.]py --account"); do run "kill -TERM $p"; done
  echo "   tradier: start via tradier watchdog / run_with_watchdog.sh tradier_manage.py (check crontab tradier_watchdog_cron)"
fi
echo "done (APPLY=$APPLY RESTART=$RESTART)"
