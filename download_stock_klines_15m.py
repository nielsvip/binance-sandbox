#!/usr/bin/env python3
"""Download REAL 15m stock klines from Massive.com (formerly Polygon.io) for 365+ day history.

Adapted from the proven download_stock_klines_5m.py: same API/throttle/429-handling/pagination and the
same append/merge-with-retention-audit contract (NEVER truncates retained bars). Writes
{SYM}_15m.json into klines_cache_backtest/tradier so backtest_v8_precompute unions it into the NPZ.
Token read from env MASSIVE_TOKEN or ~/.massive_token (falls back to the 5m module's key) — not
re-hardcoded here. Run on server: nohup python download_stock_klines_15m.py > ~/logs/dl15m.log 2>&1 &
"""
import argparse, json, os, platform, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests


def _token():
    t = os.environ.get("MASSIVE_TOKEN")
    if t:
        return t.strip()
    p = Path.home() / ".massive_token"
    if p.exists():
        try:
            return p.read_text().strip()
        except Exception:
            pass
    try:
        import download_stock_klines_5m as d5  # reuse the already-present key, no new copy
        return d5.API_KEY
    except Exception:
        raise SystemExit("no Massive token: set MASSIVE_TOKEN env or ~/.massive_token")


BASE_URL = "https://api.massive.com"
HEADERS = {"Authorization": f"Bearer {_token()}"}
INTERVAL = "15"
# STOCK_KLINES_DIR overrides the output dir — use klines_cache_macbook/tradier (a full-history stage dir
# precompute UNIONS, longest-file-wins) so 365d+ history is added WITHOUT polluting the symlinked rolling
# klines_cache_backtest (which on servers is a symlink to the ~6-week live cache).
if os.environ.get("STOCK_KLINES_DIR"):
    CACHE_DIR = Path(os.environ["STOCK_KLINES_DIR"])
elif platform.system() == "Darwin":
    CACHE_DIR = Path("/Users/niels/Documents/binance/klines_cache_backtest/tradier")
else:
    CACHE_DIR = Path("/home/niels/binance-sandbox/klines_cache_backtest/tradier")
PROGRESS_FILE = CACHE_DIR / "_download_15m_progress.json"
RETENTION_FILE = CACHE_DIR / "_native_15m_retention.json"
MAX_LIMIT = 50000
MIN_REQUEST_INTERVAL = 13  # ~4.6 req/min, under the free-tier 5/min cap
FROM_DATE = "2024-01-01"   # ~640d, comfortably >365d for the 365D backtests
# stock symbol universe (shared with the 5m downloader)
try:
    import download_stock_klines_5m as _d5
    SYMBOLS = list(_d5.SYMBOLS)
except Exception:
    SYMBOLS = []


def load_progress():
    try:
        p = json.loads(PROGRESS_FILE.read_text())
    except Exception:
        p = {}
    if not isinstance(p, dict):
        p = {}
    p.setdefault("completed", [])
    p.setdefault("failed", {})
    return p


def save_progress(p):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(p, indent=2, sort_keys=True))
    os.replace(tmp, PROGRESS_FILE)


def load_existing(symbol):
    path = CACHE_DIR / f"{symbol}_{INTERVAL}m.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return []


def save_klines(symbol, bars):
    path = CACHE_DIR / f"{symbol}_{INTERVAL}m.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(bars))
    os.replace(tmp, path)


def merge_bars(existing, new):
    by_ts = {b["timestamp"]: b for b in existing}
    for b in new:
        by_ts[b["timestamp"]] = b
    return sorted(by_ts.values(), key=lambda b: b["timestamp"])


def retention_audit(existing, merged):
    before = {str(b.get("timestamp") or "") for b in existing}
    after = {str(b.get("timestamp") or "") for b in merged}
    missing = sorted(before - after)
    return {"valid": not missing and len(after) >= len(before), "before": len(before),
            "after": len(after), "added": len(after - before), "missing": len(missing)}


def rate_limit_wait(last):
    elapsed = time.time() - last[0]
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)


def fetch_bars(symbol, last, from_date, to_date):
    all_bars = []
    url = f"{BASE_URL}/v2/aggs/ticker/{symbol}/range/{INTERVAL}/minute/{from_date}/{to_date}?limit={MAX_LIMIT}&sort=asc"
    page = 0
    while url:
        page += 1
        rate_limit_wait(last)
        last[0] = time.time()
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            print(f"  net error p{page}: {e}"); time.sleep(30); continue
        if resp.status_code == 429 or "exceeded the maximum" in resp.text:
            print(f"  rate limited p{page}, wait 65s"); time.sleep(65); continue
        data = resp.json()
        st = data.get("status", "")
        if st == "NOT_AUTHORIZED":
            print("  NOT_AUTHORIZED — skip"); return None
        if st == "ERROR":
            if "exceeded" in data.get("error", ""):
                time.sleep(65); continue
            print(f"  ERROR {data.get('error','?')}"); return None
        for r in data.get("results", []) or []:
            ts = datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
            all_bars.append({"timestamp": ts, "open": r["o"], "high": r["h"], "low": r["l"],
                             "close": r["c"], "volume": r.get("v", 0)})
        print(f"  p{page}: {data.get('resultsCount', 0)} bars (total {len(all_bars)})")
        url = data.get("next_url") or None
    return all_bars


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--from-date", default=FROM_DATE)
    ap.add_argument("--to-date", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--incremental", action="store_true", help="re-fetch all selected (append/merge)")
    args = ap.parse_args(argv)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    prog = load_progress()
    completed = set(prog["completed"])
    selected = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or SYMBOLS
    remaining = selected if args.incremental else [s for s in selected if s not in completed]
    print(f"=== Massive 15m downloader: {len(selected)} sel, {len(completed)} done, {len(remaining)} to do; "
          f"from {args.from_date} to {args.to_date}; {MIN_REQUEST_INTERVAL}s/req ===")
    last = [0.0]
    for symbol in remaining:
        print(f"[{symbol}]")
        existing = load_existing(symbol)
        bars = fetch_bars(symbol, last, args.from_date, args.to_date)
        if not bars:
            prog["failed"][symbol] = "no_data" if bars == [] else "fail"; save_progress(prog); continue
        merged = merge_bars(existing, bars) if existing else bars
        audit = retention_audit(existing, merged)
        if not audit["valid"]:
            raise RuntimeError(f"{symbol}: 15m retention regression {audit}")
        save_klines(symbol, merged)
        print(f"  saved {len(merged)} bars {merged[0]['timestamp'][:10]}..{merged[-1]['timestamp'][:10]} {audit}")
        completed.add(symbol)
        if symbol not in prog["completed"]:
            prog["completed"].append(symbol)
        prog["failed"].pop(symbol, None)
        save_progress(prog)
    print(f"DONE {len(completed)}/{len(SYMBOLS)}; failed {list(prog['failed'])}")


if __name__ == "__main__":
    main()
