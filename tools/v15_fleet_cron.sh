#!/bin/bash
# Mac cron/launchd entry (every 2 min + @reboot). Singletons are enforced inside the python tools (fcntl flock), so overlap is harmless.
#   */2 * * * * /bin/bash /Users/niels/Documents/binance/tools/v15_fleet_cron.sh >> /tmp/v15_fleet_cron.log 2>&1
cd /Users/niels/Documents/binance || exit 3
PY=${PY:-/Users/niels/Documents/binance/.venv/bin/python}; [ -x "$PY" ] || PY=/usr/bin/python3
"$PY" tools/v15_fleet_scheduler.py --once
"$PY" tools/v15_daily_pipeline.py --once
