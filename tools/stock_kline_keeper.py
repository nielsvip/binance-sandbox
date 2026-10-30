#!/usr/bin/env python3
"""stock_kline_keeper: keeps Tradier 15m klines current for every ticker in symbols_tradier.json on the host it runs on.

Run from cron every minute under flock on S5 (long side) and S6 (short side). Inside the regular session window only
(weekdays 12:45-20:15 UTC), it finds tickers whose klines_cache/tradier/<SYM>_15m.json last bar is older than the latest
closed 15m bar, and runs tools/stock_klines_gapfill.py on exactly those tickers. Writes data/reports/stock_kline_keeper.json.
Append-only through the gap-fill's own atomic merge. Never rewrites an existing bar.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from datetime import timezone

BAR_S = 900
BASE = Path(__file__).resolve().parents[1]
CACHE = BASE / "klines_cache" / "tradier"
TICKERS = BASE / "symbols_tradier.json"
REPORT = BASE / "data" / "reports" / "stock_kline_keeper.json"
STALE = BASE / "data" / "stock_kline_keeper_stale.txt"


def in_session(now):
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 12 * 60 + 45 <= minutes <= 20 * 60 + 15


def bar_epoch(stamp):
    return int(dt.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def last_bar(symbol):
    path = CACHE / f"{symbol}_15m.json"
    try:
        return bar_epoch(json.loads(path.read_text())[-1]["timestamp"])
    except Exception:
        return None


def main():
    now = dt.datetime.now(timezone.utc)
    latest = (int(time.time()) // BAR_S) * BAR_S - BAR_S
    report = {"at": now.isoformat(), "latest_closed": latest, "in_session": in_session(now)}
    if not in_session(now):
        REPORT.write_text(json.dumps(report))
        return 0
    tickers = json.loads(TICKERS.read_text())
    stale = [s for s in tickers if (last_bar(s) or 0) < latest]
    report.update({"tickers": len(tickers), "stale": stale})
    if stale:
        STALE.write_text("\n".join(stale) + "\n")
        result = subprocess.run([sys.executable, str(BASE / "tools" / "stock_klines_gapfill.py"),
                                 "--symbols-file", str(STALE), "--conc", "4"],
                                capture_output=True, text=True, cwd=str(BASE), timeout=540)
        report["gapfill_rc"] = result.returncode
        report["gapfill_tail"] = result.stdout.strip().splitlines()[-1:] if result.stdout else []
        report["still_stale"] = [s for s in stale if (last_bar(s) or 0) < latest]
    else:
        report["still_stale"] = []
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report))
    print(json.dumps(report), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
