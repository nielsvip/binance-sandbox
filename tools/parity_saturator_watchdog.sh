#!/bin/bash
# Keeps v15_parity_saturator alive (backfills spare compute with live-vs-vector parity runs).
# Niced + headroom-gated inside the saturator, so it never starves the herds/forks.
pgrep -f "tools/v15_parity_saturator.py" >/dev/null && exit 0
cd "$HOME/binance-sandbox" || exit 0
setsid nohup .venv/bin/python -u tools/v15_parity_saturator.py >> /tmp/v15_parity_saturator.log 2>&1 </dev/null &
