#!/usr/bin/env python3
"""Read-only Binance USD-M futures fills/income fetcher (Mac only, GET endpoints only).

Fetches /fapi/v1/income (REALIZED_PNL, COMMISSION) and /fapi/v1/userTrades per traded symbol for the
crypto accounts over the last N days. Sequential requests, sleep between calls, used-weight back-off,
hard stop on HTTP 418/429. Never places, modifies or cancels orders.

Usage: python3 tools/binance_fills_fetch.py --days 7 --out data/reports/binance_fills_20261014.json
"""
import argparse
import hashlib
import hmac
import json
import os
import socket
import sys
import time
import urllib.parse
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = "https://fapi.binance.com"
ACCOUNTS = ["ang", "fin", "flz", "inf", "men"]
SLEEP_S = 0.7
WEIGHT_PAUSE = 900


class BanStop(Exception):
    pass


def _get(session, key, secret, path, params):
    if socket.gethostname().lower().startswith(("s1", "niels", "ubuntu")):
        raise SystemExit("refusing to run off the Mac")
    resp = None
    for attempt in range(3):
        signed = dict(params)
        signed["timestamp"] = int(time.time() * 1000)
        signed["recvWindow"] = 10000
        query = urllib.parse.urlencode(signed)
        sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        try:
            resp = session.get(f"{BASE}{path}?{query}&signature={sig}", headers={"X-MBX-APIKEY": key}, timeout=15)
            break
        except requests.exceptions.RequestException:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    if resp.status_code in (418, 429):
        raise BanStop(f"HTTP {resp.status_code} {resp.text[:200]}")
    resp.raise_for_status()
    used = int(resp.headers.get("X-MBX-USED-WEIGHT-1M", "0") or 0)
    time.sleep(SLEEP_S)
    if used > WEIGHT_PAUSE:
        time.sleep(61)
    return resp.json()


def fetch_income(session, key, secret, income_type, start_ms, end_ms):
    rows, cursor = [], start_ms
    while cursor < end_ms:
        batch = _get(session, key, secret, "/fapi/v1/income", {"incomeType": income_type, "startTime": cursor, "endTime": end_ms, "limit": 1000})
        rows.extend(batch)
        if len(batch) < 1000:
            break
        cursor = max(int(r["time"]) for r in batch) + 1
    return rows


def fetch_trades(session, key, secret, symbol, start_ms, end_ms):
    rows, cursor = [], start_ms
    while cursor < end_ms:
        batch = _get(session, key, secret, "/fapi/v1/userTrades", {"symbol": symbol, "startTime": cursor, "endTime": min(end_ms, cursor + 7 * 86400000 - 1), "limit": 1000})
        rows.extend(batch)
        if len(batch) < 1000:
            break
        cursor = max(int(r["time"]) for r in batch) + 1
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--accounts", default=",".join(ACCOUNTS))
    ap.add_argument("--orders", action="store_true", help="also GET /fapi/v1/allOrders per traded symbol (order type + clientOrderId)")
    args = ap.parse_args()
    import env_gpg

    env_gpg.load_env_gpg()
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(args.days * 86400000) + 60000
    out = {"generated_ms": end_ms, "start_ms": start_ms, "end_ms": end_ms, "accounts": {}}
    session = requests.Session()
    try:
        for acct in args.accounts.split(","):
            key = os.environ.get(f"{acct}_API_KEY") or os.environ.get(f"{acct.upper()}_API_KEY")
            secret = os.environ.get(f"{acct}_API_SECRET") or os.environ.get(f"{acct.upper()}_API_SECRET")
            if not key or not secret:
                out["accounts"][acct] = {"error": "NO_KEYS"}
                continue
            positions = _get(session, key, secret, "/fapi/v2/positionRisk", {})
            end_ms = int(time.time() * 1000)
            pnl = fetch_income(session, key, secret, "REALIZED_PNL", start_ms, end_ms)
            fee = fetch_income(session, key, secret, "COMMISSION", start_ms, end_ms)
            symbols = sorted({r["symbol"] for r in pnl + fee if r.get("symbol")})
            trades, orders = {}, {}
            for symbol in symbols:
                trades[symbol] = fetch_trades(session, key, secret, symbol, start_ms, end_ms)
                if args.orders and trades[symbol]:
                    orders[symbol] = _get(session, key, secret, "/fapi/v1/allOrders", {"symbol": symbol, "startTime": max(start_ms, end_ms - 7 * 86400000 + 60000), "endTime": end_ms, "limit": 1000})
            out["accounts"][acct] = {"income_realized": pnl, "income_commission": fee, "trades": trades, "orders": orders, "positions": [r for r in positions if abs(float(r.get("positionAmt") or 0)) > 0], "end_ms": end_ms}
            print(f"{acct}: {len(pnl)} pnl rows, {len(fee)} fee rows, {len(symbols)} symbols, {sum(len(v) for v in trades.values())} fills, {sum(len(v) for v in orders.values())} orders", flush=True)
    except BanStop as exc:
        out["stopped"] = str(exc)
        print(f"STOPPED: {exc}", flush=True)
    Path(args.out).write_text(json.dumps(out))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
