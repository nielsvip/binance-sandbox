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
# pandas 3.0.5 against conda's 2.4.6, so swapping the backtest interpreter would move board numbers.
# It has to be benched through tools/v15_version_gate.py, never switched by hand. See BACKTEST_BIBLE.
#
# Usage:  PY=$(bash "$(dirname "$0")/pyinterp.sh")     # or: source it and use $PY
set -u
for _p in \
    /opt/anaconda3/envs/binance_env/bin/python \
    "$HOME/.conda/envs/binance_env_mac/bin/python" \
    "$HOME/.conda/envs/binance_env/bin/python" \
    /home/niels/.conda/envs/binance_env_mac/bin/python \
    /home/niels/.conda/envs/binance_env/bin/python \
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
