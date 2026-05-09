#!/usr/bin/env python3
"""tradier_klines_append.py — fetch fresh Tradier 15-min bars and append into
klines_cache_backtest/tradier/{SYMBOL}_15m.json (idempotent, timestamp-deduped).

Format on disk = list of dicts: {timestamp, open, high, low, close, volume}
Tradier `markets/timesales` returns: {"time": "...ET", "timestamp": <unix>, "open", "high", "low", "close", "volume"}

We pull last ~60 days for each symbol. Tradier limits ~10 days per timesales call.
Each call: 5min sleep × 0 (rate limit handled inline). Multiple calls chained.

Usage:
    python3 tradier_klines_append.py --symbols AAPL,AMZN,...  [--days-back 60]
    python3 tradier_klines_append.py --symbols-file symbols_trb_long.json  [...]

Reads TRADIER_API_KEY_TRB / _TRA / _ACCESS_TOKEN from env (gpg --decrypt .env.gpg first).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from io import StringIO
from pathlib import Path

import requests

IS_SERVER = platform.system() == "Linux"
BASE = Path("/home/niels/binance-sandbox") if IS_SERVER else Path("/Users/niels/Documents/binance")
TRADIER_DIR = BASE / "klines_cache_backtest" / "tradier"
TRADIER_DIR.mkdir(parents=True, exist_ok=True)

LIVE_URL = "https://api.tradier.com/v1"
INTERVAL = "15min"
SESSION_FILTER = "all"   # Tradier param: "all" includes pre/post; "open" only RTH


def load_env_from_gpg():
    env_gpg = BASE / ".env.gpg"
    if not env_gpg.exists():
        return
    try:
        res = subprocess.run(
            ["gpg", "--batch", "--yes", "--decrypt", str(env_gpg)],
            capture_output=True, text=True, timeout=20,
        )
        if res.returncode != 0:
            print(f"[warn] gpg decrypt failed: {res.stderr.strip()[:120]}", file=sys.stderr)
            return
        # Lightweight parser: KEY=VALUE per line, skip # and blanks.
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip("'").strip('"')
            if k and v and k not in os.environ:
                os.environ[k] = v
    except Exception as e:
        print(f"[warn] env load failed: {e}", file=sys.stderr)


def get_tradier_token() -> str | None:
    for k in ("TRADIER_API_KEY_TRB", "TRADIER_API_KEY_TRA", "TRADIER_ACCESS_TOKEN", "TRADIER_API_KEY"):
        v = os.environ.get(k)
        if v:
            return v.strip()
    return None


def fetch_chunk(token: str, symbol: str, start_dt: datetime, end_dt: datetime) -> list[dict]:
    """Fetch one chunk via /markets/timesales. Returns list of dict bars."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    # Tradier accepts "YYYY-MM-DD HH:MM" in ET (Eastern). We keep UTC and let server interpret.
    # Tradier docs: start/end are session-local time; using ISO8601 also works.
    params = {
        "symbol": symbol,
        "interval": INTERVAL,
        "start": start_dt.strftime("%Y-%m-%d %H:%M"),
        "end": end_dt.strftime("%Y-%m-%d %H:%M"),
        "session_filter": SESSION_FILTER,
    }
    try:
        r = requests.get(f"{LIVE_URL}/markets/timesales", params=params, headers=headers, timeout=30)
    except Exception as e:
        print(f"[err] {symbol} {start_dt}->{end_dt}: {e}", file=sys.stderr)
        return []
    if r.status_code == 401:
        print(f"[fatal] 401 unauthorized — token bad/banned", file=sys.stderr)
        sys.exit(1)
    if r.status_code == 429:
        print(f"[warn] 429 rate-limit — sleep 30s", file=sys.stderr)
        time.sleep(30)
        return []
    if r.status_code != 200:
        print(f"[warn] {symbol} status={r.status_code}: {r.text[:120]}", file=sys.stderr)
        return []
    try:
        body = r.json()
    except Exception as e:
        print(f"[warn] {symbol} json parse: {e}", file=sys.stderr)
        return []
    series = body.get("series") if isinstance(body, dict) else None
    if not series or series.get("data") is None:
        return []
    data = series["data"]
    if isinstance(data, dict):
        data = [data]
    return data


def to_canonical_bar(rec: dict) -> dict | None:
    """Convert Tradier timesales record -> existing JSON format.
    Tradier rec: {time, timestamp, price, open, high, low, close, volume, vwap}
    Existing target: {timestamp: ISO8601 Z, open, high, low, close, volume}"""
    ts_unix = rec.get("timestamp")
    time_str = rec.get("time")
    if ts_unix is not None:
        try:
            iso = datetime.fromtimestamp(int(ts_unix), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        except Exception:
            iso = None
    else:
        iso = None
    if iso is None and time_str:
        # time format "YYYY-MM-DDTHH:MM" ET — convert to UTC by adding 5 hours (EST) or 4 (EDT).
        # Safer: trust Tradier's `timestamp` field exclusively. Skip records lacking it.
        return None
    if iso is None:
        return None
    try:
        o = float(rec.get("open"))
        h = float(rec.get("high"))
        lo = float(rec.get("low"))
        c = float(rec.get("close"))
        v = float(rec.get("volume") or 0.0)
    except (TypeError, ValueError):
        return None
    return {"timestamp": iso, "open": o, "high": h, "low": lo, "close": c, "volume": v}


def load_existing(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def save_atomic(path: Path, data: list[dict]):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    tmp.replace(path)


def append_one(symbol: str, token: str, days_back: int, verbose: bool) -> tuple[int, str]:
    path = TRADIER_DIR / f"{symbol}_15m.json"
    existing = load_existing(path)
    seen = {b.get("timestamp") for b in existing if isinstance(b, dict)}
    last_existing = None
    if existing:
        try:
            last_existing = max(b.get("timestamp", "") for b in existing)
        except Exception:
            last_existing = None

    now = datetime.now(timezone.utc)
    if last_existing:
        try:
            t0 = datetime.fromisoformat(last_existing.replace("Z", "+00:00"))
            t0 -= timedelta(hours=2)  # backstop: re-pull last 2h to fill gaps
        except Exception:
            t0 = now - timedelta(days=days_back)
    else:
        t0 = now - timedelta(days=days_back)

    if t0 >= now:
        return (0, f"{symbol}: already current ({last_existing})")

    # Tradier limits intraday timesales to ~40 days per call. Chunk weekly to be safe.
    new_bars = []
    cursor = t0
    while cursor < now:
        chunk_end = min(cursor + timedelta(days=7), now)
        recs = fetch_chunk(token, symbol, cursor, chunk_end)
        if verbose:
            print(f"  {symbol} {cursor.strftime('%m-%d')} -> {chunk_end.strftime('%m-%d')}: {len(recs)} bars")
        for r in recs:
            b = to_canonical_bar(r)
            if b is None:
                continue
            if b["timestamp"] in seen:
                continue
            seen.add(b["timestamp"])
            new_bars.append(b)
        cursor = chunk_end
        time.sleep(0.25)  # gentle on the rate limit (Tradier 120 req/min on data context)

    if not new_bars:
        return (0, f"{symbol}: no new bars (last={last_existing})")

    merged = existing + new_bars
    merged.sort(key=lambda b: b.get("timestamp", ""))
    save_atomic(path, merged)
    last = merged[-1].get("timestamp", "?")
    return (len(new_bars), f"{symbol}: +{len(new_bars)} bars (last={last})")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="", help="Comma-separated tickers")
    p.add_argument("--symbols-file", default="", help="JSON list of tickers")
    p.add_argument("--days-back", type=int, default=60)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    load_env_from_gpg()
    token = get_tradier_token()
    if not token:
        print("[fatal] no TRADIER_API_KEY_* in env", file=sys.stderr)
        sys.exit(1)

    syms: list[str] = []
    if args.symbols:
        syms.extend(s.strip().upper() for s in args.symbols.split(",") if s.strip())
    if args.symbols_file:
        with open(args.symbols_file) as f:
            d = json.load(f)
            if isinstance(d, list):
                syms.extend(s.strip().upper() for s in d if isinstance(s, str) and s.strip())
            elif isinstance(d, dict):
                syms.extend(s.strip().upper() for s in d.keys() if isinstance(s, str) and s.strip())

    # Drop options-style symbols (e.g. WDAY260618P00120000 = ticker+YYMMDD+P/C+strike), dedup, sort
    import re as _re
    _option_re = _re.compile(r"^[A-Z]+[0-9]{6}[CP][0-9]+$")
    syms = sorted(set(s for s in syms if s and not any(c in s for c in ("/", " ")) and not _option_re.match(s)))
    print(f"[info] fetching {len(syms)} symbols, days_back={args.days_back}")

    t_start = time.time()
    total_added = 0
    for i, sym in enumerate(syms, 1):
        try:
            n, msg = append_one(sym, token, args.days_back, args.verbose)
        except Exception as e:
            print(f"[err] {sym}: {e}", file=sys.stderr)
            continue
        total_added += n
        print(f"[{i:3d}/{len(syms)}] {msg}")

    elapsed = time.time() - t_start
    print(f"\n[done] {len(syms)} symbols, +{total_added} bars total, {elapsed:.0f}s")


if __name__ == "__main__":
    main()
