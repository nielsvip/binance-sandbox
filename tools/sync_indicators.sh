#!/bin/bash
# sync_indicators.sh — S1 is source for backtest_v8/indicators, push to s2/s5/s6 24/7 (s7 deleted 2026-10-09)
# USER 2026-10-12 GATE (partial-push design): the push flows MINUS stale/bad NPZs (rsync --exclude-from the
# gate's violator list). All-or-nothing starved 770 fresh files over a handful of violators (proven outage:
# GATE_REFUSED every push 90+ min on a keys[0] phantom + unfixable 43d listings). SHORT-but-current NPZs DO
# sync (workers run 30D sweeps on them fine); the scheduler's tail dispatch gate holds short+stale tails on
# workers, so correctness never depends on this push being total. Fail-closed ONLY when the gate itself
# crashes or the dir is missing (rc=2 / no exclude file) — never on per-file violations (rc=1).
set -e
GATE_DIR="$HOME/binance-sandbox/backtest_v8/indicators"
EXCL=/tmp/sync_indicators_exclude.txt
rm -f "$EXCL"
if [ -f "$HOME/binance-sandbox/tools/npz_freshness_gate.py" ]; then
  set +e
  python3 "$HOME/binance-sandbox/tools/npz_freshness_gate.py" --dir "$GATE_DIR" --exclude-file "$EXCL" >> /tmp/sync_indicators.log 2>&1
  _rc=$?
  set -e
  if [ "$_rc" = "0" ]; then
    echo "$(date -u +%FT%TZ) GATE_CLEAN all NPZ pass — full push" >> /tmp/sync_indicators.log
  elif [ "$_rc" = "1" ] && [ -f "$EXCL" ]; then
    echo "$(date -u +%FT%TZ) GATE_PARTIAL pushing minus $(wc -l < "$EXCL") stale/bad files (dispatch holds them on workers)" >> /tmp/sync_indicators.log
  else
    echo "$(date -u +%FT%TZ) GATE_REFUSED gate crashed or dir missing (rc=$_rc, no exclude file) — no push (fail-closed)" >> /tmp/sync_indicators.log
    exit 1
  fi
else
  echo "$(date -u +%FT%TZ) GATE_MISSING npz_freshness_gate.py not on S1 — pushing ungated (deploy gate!)" >> /tmp/sync_indicators.log
fi
# 2026-10-13 RULE#4 resume guard: never mirror a newer NPZ under an unfinished board / running pilot (the pilot
# archives the whole board on NPZ change at resume — 44 boards archived in 24h). tools/v15_npz_freeze.py --list.
_FRZ=$(python3 "$HOME/binance-sandbox/tools/v15_npz_freeze.py" --list 2>/dev/null)
if [ -n "$_FRZ" ]; then
  for _s in $_FRZ; do echo "${_s}.npz"; done >> "$EXCL"
  echo "$(date -u +%FT%TZ) FROZEN $(echo $_FRZ | wc -w) symbols excluded (unfinished boards)" >> /tmp/sync_indicators.log
fi
# USER 2026-10-13: TradFi perps (AAPLUSDT etc., data/excluded_tokenized_stocks.json) are not tradeable — never push their NPZs
_DENY=$(python3 -c 'import json,os;print("\n".join(json.load(open(os.path.expanduser("~/binance-sandbox/data/excluded_tokenized_stocks.json"))).get("symbols",[])))' 2>/dev/null || true)
if [ -n "$_DENY" ]; then
  for _s in $_DENY; do echo "${_s}.npz"; echo "${_s}_*"; done >> "$EXCL"
  echo "$(date -u +%FT%TZ) DENY $(echo $_DENY | wc -w) TradFi symbols excluded" >> /tmp/sync_indicators.log
fi
for ip in 10.0.0.4; do  # 2026-10-13 s5/s6 destroyed
  _excl_arg=""
  [ -f "$EXCL" ] && _excl_arg="--exclude-from=$EXCL"
  # shellcheck disable=SC2086
  # 2026-10-16 `-u`: never overwrite a NEWER NPZ on the worker. S1 cannot refresh a STOCK NPZ at all
  # (npz_bar_refresh returns EXCHANGE_BAR_NOT_READY for every stock here — only s2 reaches Tradier), so s2 is the
  # only host that can produce a current stock tail. Proven 2026-10-16 00:10:08Z (ctime): this push replaced 12
  # freshly rebuilt stock NPZs on s2 with S1's 20:21Z copies, mtime preserved, and every board then refused
  # STALE_NPZ_SKIPPED. --update keeps S1 the source for everything it is newer on and lets the worker's own
  # fresher build stand.
  rsync -azu --timeout=60 $_excl_arg ~/binance-sandbox/backtest_v8/indicators/ niels@$ip:~/binance-sandbox/backtest_v8/indicators/ > /tmp/sync_ind_${ip##*.}.log 2>&1 || echo "$(date) fail $ip" >> /tmp/sync_indicators.log
  echo "$(date -u +%FT%TZ) $ip OK $(ls ~/binance-sandbox/backtest_v8/indicators 2>&1 | wc -l) files" >> /tmp/sync_indicators.log
done
