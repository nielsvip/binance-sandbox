#!/usr/bin/env python3
"""build_binance_symbol_filters — refresh symbol_configs.json + min_qty.json from Binance Futures exchangeInfo.

symbol_configs.json holds the per-symbol EXCHANGE FILTERS (price_precision,
quantity_precision, step_size, tick_size) that place_maker_order() needs to round
prices and quantities. It is NOT a strategy config.
min_qty.json holds the per-symbol {minQty, minNotional} that _min_viable_qty() uses
to floor orders UP to a placeable size (USER 2026-10-11: losers trade, never vetoed).

Missing entry => ez_manage.place_maker_order() aborts with MAKER_CRITICAL_FAIL and
places no order at all. On 2026-08-07 the file was 18 days stale and 122 of the 326
symbols in symbols.json (the stock-perp block: AAPL/AMD/NVDA/SOXS/...) had no entry,
so every maker order for those symbols was refused.

Authority is /fapi/v1/exchangeInfo. Symbols present in the current file but absent
from exchangeInfo (delisted) are preserved so nothing is ever lost.

Usage:
    python3 build_binance_symbol_filters.py            # write symbol_configs.json + min_qty.json
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
TARGET_MIN_QTY = ROOT / "min_qty.json"
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

def derive_min_qty(entry: dict) -> dict:
    filters = {item.get("filterType"): item for item in entry.get("filters", [])}
    lot_size = filters.get("LOT_SIZE") or {}
    min_notional = filters.get("MIN_NOTIONAL") or {}
    return {
        "minQty": float(lot_size["minQty"]),
        "minNotional": float(min_notional.get("notional", min_notional.get("minNotional", 5.0)) or 5.0),
    }


def build(dry_run: bool = False) -> int:
    current = {}
    if TARGET.exists():
        current = json.loads(TARGET.read_text(encoding="utf-8"))
    current_mq = {}
    if TARGET_MIN_QTY.exists():
        current_mq = json.loads(TARGET_MIN_QTY.read_text(encoding="utf-8"))
    payload = fetch_exchange_info()
    fresh = {}
    fresh_mq = {}
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
        try:
            fresh_mq[symbol] = derive_min_qty(entry)
        except (KeyError, TypeError, ValueError) as exc:
            print(f"SKIP {symbol}: cannot derive min_qty ({exc})", file=sys.stderr)
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
    added_mq = sorted(set(fresh_mq) - set(current_mq))
    updated_mq = sorted(symbol for symbol in fresh_mq if symbol in current_mq and current_mq[symbol] != fresh_mq[symbol])
    preserved_mq = sorted(set(current_mq) - set(fresh_mq))
    merged_mq = dict(current_mq)
    merged_mq.update(fresh_mq)
    print(f"min_qty perpetuals={len(fresh_mq)} added={len(added_mq)} updated={len(updated_mq)} preserved_delisted={len(preserved_mq)} total={len(merged_mq)}")
    if dry_run:
        print("DRY RUN — symbol_configs.json + min_qty.json not written")
        return 0
    tmp = TARGET.with_name(f"{TARGET.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(TARGET)
    print(f"wrote {TARGET} ({len(merged)} symbols)")
    tmp_mq = TARGET_MIN_QTY.with_name(f"{TARGET_MIN_QTY.name}.tmp.{os.getpid()}")
    tmp_mq.write_text(json.dumps(merged_mq, indent=2, sort_keys=True), encoding="utf-8")
    tmp_mq.replace(TARGET_MIN_QTY)
    print(f"wrote {TARGET_MIN_QTY} ({len(merged_mq)} symbols)")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing")
    args = parser.parse_args()
    return build(dry_run=args.dry_run)

if __name__ == "__main__":
    raise SystemExit(main())
