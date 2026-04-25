"""
Binance Futures Open Interest fetcher.
Pulls 15m OI history per symbol from /fapi/v1/openInterestHist.
Binance limits OI history to ~30 days regardless of startTime — backfill is bounded.
For longer history, this script must be run periodically (e.g. daily cron) to accumulate.

Caches per-symbol JSON in data/oi_cache/{symbol}.json with ts (ms), sumOpenInterest, sumOpenInterestValue.

Usage:
    python binance_oi_fetcher.py                            # all symbols, 30d backfill
    python binance_oi_fetcher.py BTCUSDT ETHUSDT            # specific symbols
    python binance_oi_fetcher.py --update                   # only fetch since last cached entry

Improvement Framework A2 (2026-04-25). Integrates into backtest_v8_precompute.py via _inject_oi.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional
import requests
BASE_PATH = Path(__file__).resolve().parent
SYMBOLS_FILE = BASE_PATH / "symbols.json"
CACHE_DIR = BASE_PATH / "data" / "oi_cache"
ENDPOINT = "https://fapi.binance.com/futures/data/openInterestHist"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
def fetch_oi_window(symbol: str, start_ms: int, end_ms: int) -> List[dict]:
    # Binance: limit=500 max, period=15m. To cover 30 days = 2880 bars, need ~6 paginated calls.
    out: List[dict] = []
    cur = start_ms
    while cur < end_ms:
        params = {"symbol": symbol, "period": "15m", "limit": 500, "startTime": cur, "endTime": min(cur + 500 * 15 * 60 * 1000, end_ms)}
        try:
            r = requests.get(ENDPOINT, params=params, timeout=20)
            r.raise_for_status()
            batch = r.json()
        except Exception as e:
            print(f"[{symbol}] error at startTime={cur}: {e}", file=sys.stderr)
            time.sleep(2.0)
            cur += 500 * 15 * 60 * 1000
            continue
        if not isinstance(batch, list) or not batch: break
        out.extend(batch)
        last_ts = int(batch[-1].get("timestamp", 0))
        if last_ts <= cur: break
        cur = last_ts + 1
        time.sleep(0.15)
    return out
def load_cache(symbol: str) -> List[dict]:
    p = CACHE_DIR / f"{symbol}.json"
    if not p.exists(): return []
    try: return json.loads(p.read_text())
    except Exception: return []
def save_cache(symbol: str, records: List[dict]) -> None:
    p = CACHE_DIR / f"{symbol}.json"
    seen = set()
    dedup = []
    for r in sorted(records, key=lambda x: int(x.get("timestamp", 0))):
        ts = int(r.get("timestamp", 0))
        if ts in seen or ts == 0: continue
        seen.add(ts)
        dedup.append({"timestamp": ts, "sumOpenInterest": float(r.get("sumOpenInterest", 0.0)), "sumOpenInterestValue": float(r.get("sumOpenInterestValue", 0.0))})
    p.write_text(json.dumps(dedup))
    print(f"  [{symbol}] cached {len(dedup)} records → {p}")
def fetch_symbol(symbol: str, update_only: bool) -> int:
    existing = load_cache(symbol)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - 30 * 24 * 60 * 60 * 1000  # 30 days back (Binance limit)
    if update_only and existing:
        last = max(int(r.get("timestamp", 0)) for r in existing)
        start_ms = max(start_ms, last + 1)
    print(f"[{symbol}] fetching OI from {datetime.fromtimestamp(start_ms / 1000, timezone.utc).isoformat()} (had {len(existing)} cached)")
    new_records = fetch_oi_window(symbol, start_ms, now_ms)
    if not new_records:
        print(f"  [{symbol}] no new records")
        return 0
    save_cache(symbol, existing + new_records)
    return len(new_records)
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*", help="symbol whitelist; default = all in symbols.json")
    ap.add_argument("--update", action="store_true", help="only fetch since last cached entry")
    args = ap.parse_args()
    if args.symbols: syms = args.symbols
    else:
        with SYMBOLS_FILE.open() as f: syms = json.load(f)
        if not isinstance(syms, list): print("symbols.json not a list", file=sys.stderr); sys.exit(2)
    total = 0
    for s in syms:
        try: total += fetch_symbol(s, args.update)
        except Exception as e: print(f"[{s}] FAIL: {e}", file=sys.stderr)
    print(f"DONE. {total} new records across {len(syms)} symbols.")
if __name__ == "__main__": main()
