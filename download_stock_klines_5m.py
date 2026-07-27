#!/usr/bin/env python3
"""Download REAL 5m stock klines from Massive.com (formerly Polygon.io).

Same API as 15m downloader but with 5/minute interval.
Saves to klines_cache_backtest/tradier/{SYMBOL}_5m.json.
Run on server: nohup python download_stock_klines_5m.py > /home/niels/logs/download_5m.log 2>&1 &
"""

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

API_KEY = "2kX_tMy4PWx4JZMQZxqSJxQnznuOYAC0"
BASE_URL = "https://api.massive.com"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
if platform.system() == "Darwin":
    CACHE_DIR = Path("/Users/niels/Documents/binance/klines_cache_backtest/tradier")
else:
    CACHE_DIR = Path("/home/niels/binance-sandbox/klines_cache_backtest/tradier")
PROGRESS_FILE = CACHE_DIR / "_download_5m_progress.json"
MAX_LIMIT = 50000
MIN_REQUEST_INTERVAL = 13
FROM_DATE = "2024-01-01"
TO_DATE = "2026-03-30"
INCREMENTAL_OVERLAP_DAYS = 2

SYMBOLS = ["VT", "AAPL", "ABBV", "ABT", "ACN", "ADBE", "ADP", "AMD", "AMZN", "APA", "APO", "ARM", "ASML", "ASTS", "AVGO", "BA", "BABA", "BIDU", "BITO", "BK", "BLOK", "BTCL", "BWXT", "CAT", "CLX", "CME", "COPX", "COST", "CRM", "CRWD", "CRWV", "CVS", "CVX", "DHR", "DIME", "DUOL", "ETH", "ETHD", "FDX", "FIVN", "GE", "GILD", "GLD", "GM", "GME", "GOLD", "GOOGL", "HD", "HON", "IBIT", "IBM", "INOD", "JNJ", "JOBY", "JPM", "KO", "LLY", "LMT", "LOW", "LRCX", "LYFT", "MA", "MCD", "MDT", "META", "MO", "MRK", "MRVL", "MSFT", "MU", "NFLX", "NKE", "NVDA", "OLED", "ORCL", "OXY", "PATH", "PEP", "PFE", "PYPL", "QBTS", "QCOM", "QLYS", "QQQ", "QUBT", "RBLX", "RDDT", "RKLB", "ROKU", "RTX", "SAP", "SBIT", "SBUX", "SCHW", "SHOP", "SHY", "SLV", "SMCI", "SNDK", "SNOW", "SPOT", "SPY", "STZ", "T", "TCEHY", "TGT", "TMO", "TSLA", "TSM", "TTD", "TXN", "ULTA", "UNH", "UPS", "USAR", "USO", "V", "VZ", "WDAY", "WMT", "XOM", "ZETA"]


def load_progress():
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"completed": [], "failed": {}}


def save_progress(progress):
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


def load_existing(symbol):
    path = CACHE_DIR / f"{symbol}_5m.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return []


def save_klines(symbol, bars):
    path = CACHE_DIR / f"{symbol}_5m.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(bars))
    os.replace(tmp, path)


def merge_bars(existing, new_bars):
    by_ts = {}
    for bar in existing:
        by_ts[bar["timestamp"]] = bar
    for bar in new_bars:
        by_ts[bar["timestamp"]] = bar
    return sorted(by_ts.values(), key=lambda b: b["timestamp"])


def incremental_start(existing, floor_date=FROM_DATE, overlap_days=INCREMENTAL_OVERLAP_DAYS):
    """Start shortly before the newest retained native bar for overlap repair."""
    if not existing:
        return floor_date
    newest = max(str(bar.get("timestamp") or "") for bar in existing)
    try:
        newest_dt = datetime.fromisoformat(newest.replace("Z", "+00:00"))
    except ValueError:
        return floor_date
    floor_dt = datetime.fromisoformat(f"{floor_date}T00:00:00+00:00")
    return max(floor_dt, newest_dt - timedelta(days=overlap_days)).date().isoformat()


def retention_audit(existing, merged):
    """Prove an incremental refresh did not lose any previously retained timestamp."""
    before = {str(bar.get("timestamp") or "") for bar in existing}
    after = {str(bar.get("timestamp") or "") for bar in merged}
    missing = sorted(before - after)
    return {
        "valid": not missing and len(after) >= len(before),
        "before_count": len(before),
        "after_count": len(after),
        "added_count": len(after - before),
        "missing_count": len(missing),
        "missing_sample": missing[:10],
        "first_timestamp": min(after) if after else None,
        "last_timestamp": max(after) if after else None,
    }


def rate_limit_wait(last_request_time):
    elapsed = time.time() - last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)


def fetch_5m_bars(symbol, last_req_time, from_date=FROM_DATE, to_date=TO_DATE):
    """Fetch all available 5m bars for a symbol with pagination."""
    all_bars = []
    url = f"{BASE_URL}/v2/aggs/ticker/{symbol}/range/5/minute/{from_date}/{to_date}?limit={MAX_LIMIT}&sort=asc"
    page = 0
    while url:
        page += 1
        rate_limit_wait(last_req_time[0])
        last_req_time[0] = time.time()
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            print(f"  Network error page {page}: {e}")
            time.sleep(30)
            continue
        if resp.status_code == 429 or "exceeded the maximum" in resp.text:
            print(f"  Rate limited on page {page}, waiting 65s...")
            time.sleep(65)
            continue
        data = resp.json()
        status = data.get("status", "")
        if status == "NOT_AUTHORIZED":
            print(f"  NOT_AUTHORIZED — skipping")
            return None
        if status == "ERROR":
            print(f"  ERROR: {data.get('error', '?')}")
            if "exceeded" in data.get("error", ""):
                time.sleep(65)
                continue
            return None
        results = data.get("results", [])
        if results:
            for r in results:
                ts_ms = r["t"]
                ts_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
                all_bars.append({
                    "timestamp": ts_str,
                    "open": r["o"],
                    "high": r["h"],
                    "low": r["l"],
                    "close": r["c"],
                    "volume": r.get("v", 0),
                })
        count = data.get("resultsCount", 0)
        print(f"  Page {page}: {count} bars (total so far: {len(all_bars)})")
        next_url = data.get("next_url")
        if next_url:
            url = next_url
        else:
            break
    return all_bars


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Append/merge native 5m stock bars without truncating retained history"
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="refresh every selected symbol from its latest retained bar minus a two-day overlap",
    )
    parser.add_argument(
        "--repair-range",
        action="store_true",
        help=(
            "fetch exactly --from-date..--to-date even when the symbol is marked "
            "complete, then append/merge without deleting retained timestamps"
        ),
    )
    parser.add_argument("--symbols", default="", help="comma-separated symbol override")
    parser.add_argument("--from-date", default=FROM_DATE)
    parser.add_argument(
        "--to-date",
        default=datetime.now(timezone.utc).date().isoformat(),
    )
    parser.add_argument(
        "--retention-report",
        default=str(CACHE_DIR / "_native_5m_retention.json"),
    )
    args = parser.parse_args(argv)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    progress = load_progress()
    completed = set(progress["completed"])
    selected = [
        value.strip().upper()
        for value in args.symbols.split(",")
        if value.strip()
    ] or SYMBOLS
    remaining = (
        selected
        if args.incremental or args.repair_range
        else [s for s in selected if s not in completed]
    )
    print(f"=== Massive.com REAL 5m Stock Klines Downloader ===")
    print(f"Symbols: {len(selected)} selected, {len(completed)} done, {len(remaining)} scheduled")
    print(f"Mode: {'incremental append/merge' if args.incremental else 'initial backfill'}")
    print(f"Date ceiling: {args.to_date}")
    print(f"Output: {CACHE_DIR}")
    print(f"Rate limit: 1 request per {MIN_REQUEST_INTERVAL}s")
    print(f"=" * 50)
    last_req_time = [0.0]
    retention_rows = {}
    for i, symbol in enumerate(remaining):
        print(f"\n[{len(completed)+1}/{len(SYMBOLS)}] {symbol}...")
        existing = load_existing(symbol)
        from_date = (
            args.from_date
            if args.repair_range
            else incremental_start(existing, args.from_date)
            if args.incremental
            else args.from_date
        )
        bars = fetch_5m_bars(
            symbol,
            last_req_time,
            from_date=from_date,
            to_date=args.to_date,
        )
        if bars is None:
            progress["failed"][symbol] = datetime.now(timezone.utc).isoformat()
            save_progress(progress)
            continue
        if not bars:
            print(f"  No data returned")
            progress["failed"][symbol] = "no_data"
            save_progress(progress)
            continue
        if existing:
            merged = merge_bars(existing, bars)
            print(f"  Merged: {len(existing)} existing + {len(bars)} new = {len(merged)} total")
        else:
            merged = bars
        audit = retention_audit(existing, merged)
        if not audit["valid"]:
            raise RuntimeError(
                f"{symbol}: native 5m retention regression: {audit}"
            )
        save_klines(symbol, merged)
        retention_rows[symbol] = {
            **audit,
            "fetch_from": from_date,
            "fetch_to": args.to_date,
            "updated_utc": datetime.now(timezone.utc).isoformat(),
        }
        first_ts = merged[0]["timestamp"][:10]
        last_ts = merged[-1]["timestamp"][:10]
        print(f"  Saved {len(merged)} bars ({first_ts} → {last_ts})")
        completed.add(symbol)
        if symbol not in progress["completed"]:
            progress["completed"].append(symbol)
        progress["failed"].pop(symbol, None)
        save_progress(progress)
        report_path = Path(args.retention_report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_report = report_path.with_suffix(report_path.suffix + ".tmp")
        tmp_report.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_utc": datetime.now(timezone.utc).isoformat(),
                    "contract": "native 5m timestamps are append/merge-only; no truncation",
                    "symbols": retention_rows,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        os.replace(tmp_report, report_path)
    print(f"\n{'=' * 50}")
    print(f"DONE. {len(completed)}/{len(SYMBOLS)} symbols downloaded.")
    if progress["failed"]:
        print(f"Failed ({len(progress['failed'])}): {list(progress['failed'].keys())}")


if __name__ == "__main__":
    main()
