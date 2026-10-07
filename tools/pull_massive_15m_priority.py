#!/usr/bin/env python3
"""Priority pull of 15m klines from Massive.com — most important first, never overwrites 917d with 77d."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
def _load_api_key() -> str:
    k = os.environ.get("MASSIVE_API_KEY")
    if k:
        return k.strip()
    for c in (Path.home() / ".config" / "massive_api_key", Path.home() / ".massive_api_key"):
        if c.exists():
            return c.read_text().strip()
    raise RuntimeError("MASSIVE_API_KEY not set and no ~/.config/massive_api_key")


API_KEY = _load_api_key()
BASE_URL = "https://api.massive.com/v2/aggs"
# Keep pulling — rate limit ~5/s, paginate limit=50000
PRIORITY_TIER_1 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK.B", "LLY", "AVGO", "JPM",
    "UNH", "V", "MA", "HD", "COST", "PG", "JNJ", "BAC", "XOM", "CVX",
    "SPY", "QQQ", "GLD", "SLV", "TLT", "IWM", "DIA", "XLE", "XLF", "XLK",
]

def load_tradier_symbols() -> list[str]:
    p = ROOT / "symbols_tradier.json"
    if not p.exists():
        p = Path.home() / "binance-sandbox" / "symbols_tradier.json"
    return json.loads(p.read_text())

def prioritized_symbols() -> list[str]:
    all_syms = load_tradier_symbols()
    # Rank: tier1 first, then missing/short/bad (needs fix), then rest
    # Determine bad via existing NPZ span
    bad = set()
    for sym in all_syms:
        npz = ROOT / "backtest_v8" / "indicators" / f"{sym}.npz"
        # Also check S1 path if running on Mac
        if not npz.exists():
            npz = Path.home() / "binance-sandbox" / "backtest_v8" / "indicators" / f"{sym}.npz"
        if not npz.exists():
            bad.add(sym)
            continue
        try:
            d = np.load(npz, allow_pickle=True)
            ts = d["timestamps"].astype(np.int64)
            if ts[-1] > 1e11:
                ts = ts // 1000
            span = (float(ts[-1]) - float(ts[0])) / 86400
            dt = float(np.median(np.diff(ts.astype(float)))) if len(ts) > 1 else 0
            if span < 365 or not (600 < dt < 1200) or len(d.files) < 800 or "wt1_W" not in d.files:
                bad.add(sym)
        except:
            bad.add(sym)
    tier1_set = set(PRIORITY_TIER_1)
    tier1_present = [s for s in PRIORITY_TIER_1 if s in all_syms]
    bad_sorted = sorted(bad - tier1_set)
    rest = [s for s in all_syms if s not in tier1_set and s not in bad]
    return tier1_present + bad_sorted + rest

def fetch_massive_15m(symbol: str, from_date: str, to_date: str) -> list[dict] | None:
    url = f"{BASE_URL}/ticker/{symbol}/range/15/minute/{from_date}/{to_date}"
    params = {"limit": 50000, "sort": "asc", "apiKey": API_KEY}
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=30)
        if r.status_code == 403:
            return None
        r.raise_for_status()
        j = r.json()
        return j.get("results", [])
    except requests.RequestException:
        return None

if __name__ == "__main__":
    syms = prioritized_symbols()
    print(json.dumps({"prioritized": syms[:10], "total": len(syms)}))
