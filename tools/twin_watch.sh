#!/bin/bash
# twin_watch.sh — re-run the wire-push twin suite every 10 min until 30 min
# before NYSE open (2026-10-05 13:00 UTC). Logs every run; prints ONLY failures
# (monitor wakes on output). Run: ./tools/twin_watch.sh (any session/machine).
cd "$(dirname "$0")/.." || exit 1
END=$(date -j -f "%Y-%m-%d %H:%M %z" "2026-10-05 13:00 +0000" +%s 2>/dev/null || date -d "2026-10-05 13:00 UTC" +%s)
LOG=data/reports/twin_watch_20261005.log
SUITE="test_wire_push_contract_20261005.py test_bb_stoch_exits_twin.py test_noloss_hold_twin.py test_technical_dc_twin.py test_entry_ports_a_twin.py test_entry_ports_b_twin.py test_entries_dead_a_twin.py test_entries_dead_b_twin.py test_exits_dead_twin.py test_gates_sizing_a_twin.py test_yellow_filters_twin.py test_stdev_twin.py test_sizing_reduce_twin.py test_reentry_staged_twin.py test_vec_special_twin.py test_orange_ports_b_twin.py test_p0_stocks_twin.py test_p0_crypto_a_twin.py test_p0_crypto_b_twin.py"
while [ "$(date +%s)" -lt "$END" ]; do
  TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  OUT=$(python3 -m pytest $SUITE -q 2>&1 | tail -1)
  echo "$TS $OUT" >> "$LOG"
  case "$OUT" in
    *failed*|*error*) echo "TWIN_WATCH_FAIL $TS $OUT" ;;
  esac
  sleep 600
done
echo "TWIN_WATCH_DONE $(date -u +%Y-%m-%dT%H:%M:%SZ) window ended"
