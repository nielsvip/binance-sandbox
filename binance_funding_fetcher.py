"""
Binance Futures funding rate fetcher.
Pulls 8h funding history per symbol from /fapi/v1/fundingRate (public, no auth).
Caches per-symbol JSON in data/funding_cache/{symbol}.json with ts (ms), fundingRate.

Usage:
    python binance_funding_fetcher.py                       # fetch all symbols in symbols.json
    python binance_funding_fetcher.py BTCUSDC ETHUSDC       # specific symbols
    python binance_funding_fetcher.py --since 2022-01-01    # backfill from date
    python binance_funding_fetcher.py --update              # only fetch new since last cached entry

Improvement Framework A1 (2026-04-25). Integrates into backtest_v8_precompute.py via _inject_funding.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
import requests
BASE_PATH = Path(__file__).resolve().parent
SYMBOLS_FILE = BASE_PATH / "symbols.json"
CACHE_DIR = BASE_PATH / "data" / "funding_cache"
ENDPOINT = "https://fapi.binance.com/fapi/v1/fundingRate"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
def _to_ms(dt_str: str) -> int:
    dt = datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
def fetch_funding_window(symbol: str, start_ms: int, end_ms: Optional[int] = None) -> List[dict]:
    out: List[dict] = []
    cur = start_ms
    while True:
        params = {"symbol": symbol, "limit": 1000, "startTime": cur}
        if end_ms is not None: params["endTime"] = end_ms
        try:
            r = requests.get(ENDPOINT, params=params, timeout=20)
            r.raise_for_status()
            batch = r.json()
        except Exception as e:
            print(f"[{symbol}] error at startTime={cur}: {e}", file=sys.stderr)
            time.sleep(2.0)
            continue
        if not isinstance(batch, list) or not batch: break
        out.extend(batch)
        last_ts = int(batch[-1].get("fundingTime", 0))
        if last_ts <= cur or len(batch) < 1000: break
        cur = last_ts + 1
        time.sleep(0.15)  # API courtesy
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
    for r in sorted(records, key=lambda x: int(x.get("fundingTime", 0))):
        ts = int(r.get("fundingTime", 0))
        if ts in seen or ts == 0: continue
        seen.add(ts)
        dedup.append({"fundingTime": ts, "fundingRate": float(r.get("fundingRate", 0.0)), "symbol": symbol})
    p.write_text(json.dumps(dedup))
    print(f"  [{symbol}] cached {len(dedup)} records → {p}")
def fetch_symbol(symbol: str, since_ms: Optional[int], update_only: bool) -> int:
    existing = load_cache(symbol)
    start_ms = since_ms if since_ms is not None else _to_ms("2020-01-01")
    if update_only and existing:
        last = max(int(r.get("fundingTime", 0)) for r in existing)
        start_ms = max(start_ms, last + 1)
    print(f"[{symbol}] fetching from {datetime.fromtimestamp(start_ms / 1000, timezone.utc).isoformat()} (had {len(existing)} cached)")
    new_records = fetch_funding_window(symbol, start_ms)
    if not new_records:
        print(f"  [{symbol}] no new records")
        return 0
    save_cache(symbol, existing + new_records)
    return len(new_records)
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*", help="symbol whitelist; default = all in symbols.json")
    ap.add_argument("--since", default="2020-01-01", help="ISO date to start from")
    ap.add_argument("--update", action="store_true", help="only fetch since last cached entry")
    args = ap.parse_args()
    if args.symbols: syms = args.symbols
    else:
        with SYMBOLS_FILE.open() as f: syms = json.load(f)
        if not isinstance(syms, list): print("symbols.json not a list", file=sys.stderr); sys.exit(2)
    since_ms = _to_ms(args.since) if args.since else None
    total = 0
    for s in syms:
        try: total += fetch_symbol(s, since_ms, args.update)
        except Exception as e: print(f"[{s}] FAIL: {e}", file=sys.stderr)
    print(f"DONE. {total} new records across {len(syms)} symbols.")
if __name__ == "__main__": main()
