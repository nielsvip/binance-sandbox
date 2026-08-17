#!/usr/bin/env python3
"""build_binance_symbol_filters — refresh symbol_configs.json from Binance Futures exchangeInfo.

symbol_configs.json holds the per-symbol EXCHANGE FILTERS (price_precision,
quantity_precision, step_size, tick_size) that place_maker_order() needs to round
prices and quantities. It is NOT a strategy config.

Missing entry => ez_manage.place_maker_order() aborts with MAKER_CRITICAL_FAIL and
places no order at all. On 2026-08-07 the file was 18 days stale and 122 of the 326
symbols in symbols.json (the stock-perp block: AAPL/AMD/NVDA/SOXS/...) had no entry,
so every maker order for those symbols was refused.

Authority is /fapi/v1/exchangeInfo. Symbols present in the current file but absent
from exchangeInfo (delisted) are preserved so nothing is ever lost.

Usage:
    python3 build_binance_symbol_filters.py            # write symbol_configs.json
    python3 build_binance_symbol_filters.py --dry-run  # report only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "symbol_configs.json"
EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
# Binance lists the stock perps (AAPL/AMD/NVDA/SOXS/...) as TRADIFI_PERPETUAL, not
# PERPETUAL. Filtering on PERPETUAL alone is what left all 122 of them without
# exchange filters and hard-failed every maker order for them.
PERPETUAL_CONTRACT_TYPES = ("PERPETUAL", "TRADIFI_PERPETUAL")

def fetch_exchange_info(url: str = EXCHANGE_INFO_URL, timeout: float = 30.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "binance-symbol-filters/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def derive_filters(entry: dict) -> dict:
    filters = {item.get("filterType"): item for item in entry.get("filters", [])}
    lot_size = filters.get("LOT_SIZE") or {}
    price_filter = filters.get("PRICE_FILTER") or {}
    return {
        "price_precision": int(entry["pricePrecision"]),
        "quantity_precision": int(entry["quantityPrecision"]),
        "step_size": float(lot_size["stepSize"]),
        "tick_size": float(price_filter["tickSize"]),
    }

def build(dry_run: bool = False) -> int:
    current = {}
    if TARGET.exists():
        current = json.loads(TARGET.read_text(encoding="utf-8"))
    payload = fetch_exchange_info()
    fresh = {}
    for entry in payload.get("symbols", []):
        if entry.get("contractType") not in PERPETUAL_CONTRACT_TYPES or entry.get("status") != "TRADING":
            continue
        symbol = entry.get("symbol")
        if not symbol:
            continue
        try:
            fresh[symbol] = derive_filters(entry)
        except (KeyError, TypeError, ValueError) as exc:
            print(f"SKIP {symbol}: cannot derive filters ({exc})", file=sys.stderr)
    if not fresh:
        print("REFUSING: exchangeInfo returned no usable perpetual symbols", file=sys.stderr)
        return 1
    added = sorted(set(fresh) - set(current))
    updated = sorted(symbol for symbol in fresh if symbol in current and current[symbol] != fresh[symbol])
    preserved = sorted(set(current) - set(fresh))
    merged = dict(current)
    merged.update(fresh)
    print(f"exchangeInfo perpetuals={len(fresh)} added={len(added)} updated={len(updated)} preserved_delisted={len(preserved)} total={len(merged)}")
    if added:
        print(f"added: {', '.join(added[:20])}{' ...' if len(added) > 20 else ''}")
    if updated:
        print(f"updated: {', '.join(updated[:20])}{' ...' if len(updated) > 20 else ''}")
    if dry_run:
        print("DRY RUN — symbol_configs.json not written")
        return 0
    tmp = TARGET.with_name(f"{TARGET.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(TARGET)
    print(f"wrote {TARGET} ({len(merged)} symbols)")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing")
    args = parser.parse_args()
    return build(dry_run=args.dry_run)

if __name__ == "__main__":
    raise SystemExit(main())
