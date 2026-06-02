#!/bin/bash
# start_stocks.sh — bring up the STOCK stack ONLY (does NOT touch crypto), headless under the watchdog.
# 2026-06-01: trb/trc were down for the weekend; this launches the full feed→manage chain in order.
# Run from a persistent shell:  ! bash start_stocks.sh
cd /Users/niels/Documents/binance || exit 1
WD="bash /Users/niels/Documents/binance/run_with_watchdog.sh"
launch () {
  local name="$1"; shift
  if pgrep -af "run_with_watchdog.sh $name" | grep -v grep >/dev/null; then
    echo "ALREADY UP: $name"; return
  fi
  nohup $WD "$@" > "/Users/niels/logs/wd_${name%.py}.out" 2>&1 < /dev/null &
  disown
  echo "launched: $name $*"
}
# 1) data feeds first
launch tradier_prices.py tradier_prices.py
launch tradier_indicators.py tradier_indicators.py
launch tradier_rankings.py tradier_rankings.py
launch tradier_positions.py tradier_positions.py --accounts tra trb trc
echo "feeds up — waiting 40s for prices/indicators to populate Redis before manage..."
sleep 40
# 2) managers (book + Rule B + ema-ladder + per_sym gate live)
launch tradier_manage.py tradier_manage.py --accounts trb
launch tradier_manage.py tradier_manage.py --accounts trc
sleep 5
echo "=== stock stack status ==="
for s in tradier_prices tradier_indicators tradier_rankings tradier_positions tradier_manage; do
  echo "  $s: $(pgrep -afc "$s.py")"
done
echo "done — market opens 13:30 UTC; procs idle until then."
