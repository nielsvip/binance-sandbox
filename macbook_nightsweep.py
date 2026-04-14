# pylint: disable=W,C,R,I
#!/usr/bin/env python3
"""MacBook Night-Time Sweep Worker.

Runs V8 sweep on MacBook during closed-market windows (20:00-13:30 UTC).
Idle during market hours (13:30-20:00 UTC) so live trading has CPU.

Launched every 10 minutes via LaunchAgent. Each invocation:
  1. Checks current UTC hour against market window.
  2. If market open → exit immediately.
  3. If market closed AND no sweep already running → start one in the background.

Configuration: edits to the sweep command below — set --tier, --symbols, etc.
"""
import datetime as _dt
import logging
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG_DIR = Path(os.path.expanduser("~/logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "macbook_nightsweep.log"), logging.StreamHandler()],
)
log = logging.getLogger("nightsweep")

# Market hours in UTC (per CLAUDE.md): 13:30 open, 20:00 close.
# Sweep window = outside those hours.
SWEEP_START_HOUR = 20  # inclusive
SWEEP_END_HOUR = 13    # exclusive (means 13:00 still OK, 13:30 is cutoff)
SWEEP_END_MIN = 30     # sharp cutoff


def _market_closed(now: _dt.datetime) -> bool:
    h, m = now.hour, now.minute
    if h >= SWEEP_START_HOUR: return True       # 20:00-23:59 closed
    if h < SWEEP_END_HOUR: return True           # 00:00-12:59 closed
    if h == SWEEP_END_HOUR and m < SWEEP_END_MIN: return True  # 13:00-13:29 closed
    return False


def _already_running() -> bool:
    try:
        r = subprocess.run(["pgrep", "-f", "backtest_v8_sweep.py"], capture_output=True, text=True, timeout=5)
        return bool(r.stdout.strip())
    except Exception:
        return False


def _start_sweep():
    # Conservative config — 4 workers, small symbol set, t25 (the current active tier)
    cmd = [
        "/opt/anaconda3/envs/binance_env/bin/python",
        "-u",
        str(BASE / "backtest_v8_sweep.py"),
        "--mode", "tradier",
        "--account", "trb",
        "--start", "2024-06-01",
        "--capital", "2000",
        "--workers", "4",
        "--tier", "25",
        "--symbols", "AAPL,MSFT,NVDA,AMZN,AMD,XOM,QQQ,SPY,DIS,META",
        "--resume",
    ]
    log_path = LOG_DIR / "macbook_nightsweep_last.log"
    log.info(f"starting sweep: {' '.join(cmd)}")
    with open(log_path, "w") as f:
        subprocess.Popen(cmd, cwd=str(BASE), stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    log.info(f"sweep kicked off — see {log_path}")


def main():
    now = _dt.datetime.utcnow()
    closed = _market_closed(now)
    running = _already_running()
    log.info(f"tick {now.strftime('%H:%M UTC')} closed={closed} running={running}")
    if not closed:
        return  # market open — stay out of the way
    if running:
        return  # something is already sweeping
    _start_sweep()


if __name__ == "__main__":
    main()
