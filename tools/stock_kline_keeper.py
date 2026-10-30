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
    """Collect only regular-session bars: a closed bar starting 13:30-19:45 UTC on a weekday. The 12:45, 13:00 and 13:15 bars
    are temporary premarket bars owned by npz_premarket_temp_bars (erased at 13:40 UTC), and nothing outside market hours is collected."""
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 13 * 60 + 30 <= minutes < 20 * 60


def bar_epoch(stamp):
    return int(dt.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def last_bar(symbol):
    path = CACHE / f"{symbol}_15m.json"
    try:
        return bar_epoch(json.loads(path.read_text())[-1]["timestamp"])
    except Exception:
        return None


TEMP_STARTS = ("12:45:00", "13:00:00", "13:15:00")
TEMP_ERASE_AFTER = 13 * 60 + 40


def enforce_window(now):
    """Remove today's bars outside market hours: any bar starting at or after 20:00 UTC, and the 12:45/13:00/13:15 temporary
    premarket bars once it is 13:40 UTC. Runs after every fill, because the gap-fill merges Tradier history back in."""
    day = now.strftime("%Y-%m-%d")
    removed = 0
    for path in CACHE.glob("*_15m.json"):
        try:
            rows = json.loads(path.read_text())
        except Exception:
            continue
        keep = []
        for row in rows:
            stamp = str(row.get("timestamp", ""))
            if stamp.startswith(day):
                hhmm = stamp[11:16]
                off_hours = hhmm >= "20:00"
                temp = False  # USER 2026-10-30: temp premarket bars are kept for backtesting; their erase is owned by npz_premarket_temp_bars
                if off_hours or temp:
                    removed += 1
                    continue
            keep.append(row)
        if len(keep) != len(rows):
            gone = [str(r.get("timestamp", "")) for r in rows if r not in keep]
            with open(os.path.expanduser("~/logs/kline_enforce_removed.log"), "a") as log:
                for stamp in gone:
                    log.write(f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')} removed {path.name} {stamp}\n")
            tmp = path.with_name("." + path.name + ".enforce")
            tmp.write_text(json.dumps(keep))
            os.replace(tmp, path)
    return removed


def main():
    now = dt.datetime.now(timezone.utc)
    if "--enforce-only" in sys.argv:
        print(json.dumps({"enforce_removed": enforce_window(now)}), flush=True)
        return 0
    latest = (int(time.time()) // BAR_S) * BAR_S - BAR_S
    report = {"at": now.isoformat(), "latest_closed": latest, "in_session": in_session(now)}
    if not in_session(now) or not in_session(dt.datetime.fromtimestamp(latest, timezone.utc)):
        report["enforce_removed"] = enforce_window(now)
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
        report["enforce_removed"] = enforce_window(now)
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
