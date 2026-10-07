#!/bin/bash
# switch_bible_cycle — rebuild + verify the SWITCH BIBLE against the FINAL templates; run from cron every 10 min. Rebuilds when (a) templates changed,
# (b) data/engine_deploy newest note / CURRENT.json changed, (c) older than 6h. macOS has no flock: mkdir lockdir. One line per cycle -> data/wiring/LOG.md.
cd /Users/niels/Documents/binance || exit 3
LOCK=/tmp/switch_bible_cycle.lockdir
if ! mkdir "$LOCK" 2>/dev/null; then
  _o_pid=$(cat "$LOCK/pid" 2>/dev/null)
  _dead=0; { [ -z "$_o_pid" ] || ! kill -0 "$_o_pid" 2>/dev/null; } && _dead=1
  _ver=0; [ -n "$_o_pid" ] && ps -o command= -p "$_o_pid" 2>/dev/null | grep -q "switch_bible_cycle" && _ver=1
  if [ "$_dead" = 1 ] || { [ $(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) )) -gt 2100 ] && [ "$_ver" = 1 ]; }; then
    if [ "$_ver" = 1 ]; then
      if [ "$(ps -o pgid= -p "$_o_pid" 2>/dev/null | tr -d ' ')" = "$_o_pid" ]; then kill -9 -"$_o_pid" 2>/dev/null; else kill -9 "$_o_pid" 2>/dev/null; fi
      sleep 2
    fi
    rm -rf "$LOCK" 2>/dev/null; mkdir "$LOCK" 2>/dev/null || exit 0
  else exit 0; fi
fi
echo $$ > "$LOCK/pid"
trap 'rm -f "$LOCK/pid" 2>/dev/null; rmdir "$LOCK" 2>/dev/null' EXIT
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
STATE=data/switch_bible_cycle.state
M5=$(command -v md5 2>/dev/null || echo /sbin/md5)
SIG=$( ("$M5" -q SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx; \
      [ -f data/engine_deploy/CURRENT.json ] && "$M5" -q data/engine_deploy/CURRENT.json; ls data/engine_deploy 2>/dev/null; "$M5" -q v12_quick_engine.py) | "$M5" -q )
OLD=$(cut -d' ' -f1 "$STATE" 2>/dev/null); AGE=$(( $(date +%s) - $(stat -f %m "$STATE" 2>/dev/null || echo 0) ))
[ "$SIG" = "$OLD" ] && [ "$AGE" -lt 21600 ] && exit 0
$PY tools/build_switch_bible.py > /tmp/switch_bible_build.log 2>&1
$PY tools/orange_placement_check.py > /tmp/switch_bible_orange.log 2>&1
$PY tools/verify_switch_bible.py --json data/reports/switch_bible_verify_latest.json > /tmp/switch_bible_verify.log 2>&1; RC=$?
echo "$SIG $(date -u +%FT%TZ)" > "$STATE"
SUM=$($PY - <<'P'
import json,collections
v=json.load(open('data/reports/switch_bible_verify_latest.json'))
c={k:len(x) for k,x in v['checks'].items() if x}
import csv
o=collections.Counter(r['placement'] for r in csv.DictReader(open('data/wiring/orange_placement.csv')))
print(f"BROKEN={v['broken']} {c} orange={dict(o)}")
P
)
echo "$(date -u +%FT%TZ) [E/bible] rc=$RC $SUM" >> data/wiring/LOG.md
[ $RC -eq 0 ] && $PY tools/verify_switch_bible.py --accept-on-pass >/dev/null 2>&1
exit 0
