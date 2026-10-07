#!/bin/bash
# Mac cron wrapper (no flock on macOS): mkdir lock, stale after 20 min
L=/tmp/v15_row_coverage.lockdir
if ! mkdir "$L" 2>/dev/null; then
  _o_pid=$(cat "$L/pid" 2>/dev/null)
  _dead=0; { [ -z "$_o_pid" ] || ! kill -0 "$_o_pid" 2>/dev/null; } && _dead=1
  _ver=0; [ -n "$_o_pid" ] && ps -o command= -p "$_o_pid" 2>/dev/null | grep -q "v15_row_coverage" && _ver=1
  if [ "$_dead" = 1 ] || { [ -n "$(find "$L" -maxdepth 0 -mmin +25 2>/dev/null)" ] && [ "$_ver" = 1 ]; }; then
    if [ "$_ver" = 1 ]; then
      if [ "$(ps -o pgid= -p "$_o_pid" 2>/dev/null | tr -d ' ')" = "$_o_pid" ]; then kill -9 -"$_o_pid" 2>/dev/null; else kill -9 "$_o_pid" 2>/dev/null; fi
      sleep 2
    fi
    rm -rf "$L" 2>/dev/null; mkdir "$L" 2>/dev/null || exit 0
  else exit 0; fi
fi
echo $$ > "$L/pid"
trap 'rm -f "$L/pid" 2>/dev/null; rmdir "$L" 2>/dev/null' EXIT
cd /Users/niels/Documents/binance || exit 1
/opt/anaconda3/envs/binance_env/bin/python tools/v15_row_coverage.py --run 2>&1 | grep -v INFO | tail -3
