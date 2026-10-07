#!/bin/bash
# fleet_sym_check.sh <BASE> — MANDATORY before ANY manual pilot/probe/battery/parity run.
# Fails (rc=1) if BASE_LONG or BASE_SHORT is running on ANY fleet host. The
# scheduler pins a base sym to one host (owner map), but manual runs bypass it —
# this check closes that hole. 2026-10-04: GDX ran on s1+s2 simultaneously.
set -u
BASE="${1:?usage: fleet_sym_check.sh <BASE>}"
rc=0
for h in s1-int s2 s5; do
  hits=$(ssh -o ConnectTimeout=8 "$h" "pgrep -af 'v15_pilot.py.*${BASE}_[LS]|parity_check.py.*${BASE}_[LS]|parity_repair.py.*${BASE}_[LS]|cell_.*${BASE}_[LS]|precompute.py.*${BASE}[^a-zA-Z]'" 2>/dev/null | head -4)
  if [ -n "$hits" ]; then
    echo "HOLD $h runs $BASE:"
    echo "$hits" | cut -c1-160
    rc=1
  else
    echo "clear $h"
  fi
done
[ $rc -eq 0 ] && echo "FLEET-CLEAR $BASE" || echo "FLEET-HOLD $BASE"
exit $rc
