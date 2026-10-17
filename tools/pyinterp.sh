#!/bin/bash
# pyinterp.sh — the one place that answers "which python runs project code on THIS host".
#
# USER 2026-10-17: "everything should be on conda so the binance venv can be discarded it is flawed".
# The `.venv` rung is deliberately absent. Mac `.venv` held 15 packages against conda's 240, and S1
# `~/binance/.venv` 9 against conda's 253 — which is why 9 of 10 live scripts could not import under it
# and S1 could not take over. The preference order below is the proven one from run_with_watchdog.sh,
# minus `.venv`:
#
#   Mac : /opt/anaconda3/envs/binance_env/bin/python
#   S1  : ~/.conda/envs/binance_env_mac  ->  ~/.conda/envs/binance_env
#   last: whatever python3 is on PATH (never silently a venv)
#
# NOTE: this does NOT cover ~/binance-sandbox/.venv on S1. That environment carries numpy 2.5.3 /
# pandas 3.0.5 against conda's 2.4.6 -- that was the CAUTION. It has since been MEASURED and is not true:
# backtest_v8_precompute.compute_symbol('BNBUSDC','crypto', return_arrays=True) under the sandbox venv and under
# conda binance_env_mac gave 946 numeric arrays over 42,451 rows that are BIT-IDENTICAL (0 moved values, 0 dtype
# differences), and conda additionally HAS scipy, whose absence made the venv fail every stocks row recompute.
# USER 2026-10-17: "we will be using conda only for everything" -- so the rungs below are conda only.
# The stale ~/.conda/envs/binance_env rung was REMOVED: created 2025-06-03, its numpy 1.26.4 was pip-installed
# outside conda and is pinned <2 by pyarrow 15 + tensorflow-cpu-aws 2.15, so it can never be current. It was also
# listed AHEAD of a duplicated binance_env_mac line, so a host missing binance_env_mac silently got old numpy.
#
# Usage:  PY=$(bash "$(dirname "$0")/pyinterp.sh")     # or: source it and use $PY
set -u
for _p in \
    /opt/anaconda3/envs/binance_env/bin/python \
    "$HOME/.conda/envs/binance_env_mac/bin/python" \
    /home/niels/.conda/envs/binance_env_mac/bin/python \
    /usr/bin/python3
do
    if [ -x "$_p" ]; then
        PY="$_p"
        echo "$PY"
        return 0 2>/dev/null || exit 0
    fi
done
PY="python3"
echo "$PY"
return 0 2>/dev/null || exit 0
