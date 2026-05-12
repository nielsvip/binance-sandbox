#!/usr/bin/env python3
"""
Tail-freshness append: copy new bars from klines_cache (live) into klines_cache_backtest.
Per CLAUDE.md NPZ REGEN rule: append latest klines from klines_cache/ into klines_cache_backtest/
so 15m base goes up to within 1 hour of NOW before regenerating NPZ.

Backs up modified files to backups/ first.
Does NOT delete original data. Only appends bars newer than the existing last bar.
"""
import json
import os
import shutil
import datetime
import sys

BASE_PATH = "/Users/niels/Documents/binance"
BACKUP_DIR = os.path.join(BASE_PATH, "backups")
BT_TRADIER_DIR = os.path.join(BASE_PATH, "klines_cache_backtest", "tradier")
LIVE_TRADIER_DIR = os.path.join(BASE_PATH, "klines_cache", "tradier")
BT_CRYPTO_DIR = os.path.join(BASE_PATH, "klines_cache_backtest")
LIVE_CRYPTO_DIR = os.path.join(BASE_PATH, "klines_cache")

TS_LABEL = datetime.datetime.utcnow().strftime("%Y%m%d%H%M")


def backup_file(path):
    fname = os.path.basename(path)
    dest = os.path.join(BACKUP_DIR, f"before_klines_tail_append_{TS_LABEL}_{fname}")
    shutil.copy2(path, dest)
    return dest


def normalize_bar(bar):
    """Keep only canonical fields."""
    return {
        "timestamp": bar["timestamp"],
        "open": bar["open"],
        "high": bar["high"],
        "low": bar["low"],
        "close": bar["close"],
        "volume": bar["volume"],
    }


def append_new_bars(bt_path, live_path, sym, tf):
    """Append bars from live that are newer than the last bar in bt. Returns n_appended."""
    if not os.path.exists(live_path):
        print(f"  [{sym} {tf}] live MISSING: {live_path}")
        return 0
    if not os.path.exists(bt_path):
        print(f"  [{sym} {tf}] bt MISSING (no source): {bt_path}")
        return 0

    with open(bt_path) as f:
        bt_data = json.load(f)
    with open(live_path) as f:
        live_data = json.load(f)

    if not bt_data or not live_data:
        print(f"  [{sym} {tf}] empty data")
        return 0

    bt_last_ts = bt_data[-1]["timestamp"]
    # Find bars in live that are strictly newer
    new_bars = [normalize_bar(bar) for bar in live_data if bar["timestamp"] > bt_last_ts]

    if not new_bars:
        print(f"  [{sym} {tf}] already current (bt={bt_last_ts[:10]})")
        return 0

    # Backup before modifying
    backup_file(bt_path)

    # Append
    bt_data.extend(new_bars)

    with open(bt_path, "w") as f:
        json.dump(bt_data, f, separators=(",", ":"))

    live_last = live_data[-1]["timestamp"]
    print(f"  [{sym} {tf}] appended {len(new_bars)} bars ({bt_last_ts[:10]} -> {live_last[:10]}), total={len(bt_data)}")
    return len(new_bars)


def main():
    # Load tradier symbols
    with open(os.path.join(BASE_PATH, "symbols_trb_long.json")) as f:
        longs = json.load(f)
    with open(os.path.join(BASE_PATH, "symbols_trb_short.json")) as f:
        shorts = json.load(f)

    tradier_syms = sorted(set(longs + shorts))
    # Filter out options-like symbols (very long names)
    tradier_syms = [s for s in tradier_syms if len(s) <= 10]

    print(f"=== Tradier tail-append: {len(tradier_syms)} symbols ===")
    total_appended = 0
    for sym in tradier_syms:
        for tf in ["15m", "5m"]:
            bt_path = os.path.join(BT_TRADIER_DIR, f"{sym}_{tf}.json")
            live_path = os.path.join(LIVE_TRADIER_DIR, f"{sym}_{tf}.json")
            n = append_new_bars(bt_path, live_path, sym, tf)
            total_appended += n

    print(f"\nTradier: total bars appended = {total_appended}")

    # Crypto symbols
    crypto_syms = [
        "BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC", "XRPUSDC",
        "ADAUSDC", "LINKUSDC", "LTCUSDC", "AVAXUSDC", "UNIUSDC",
        "DOGEUSDC",
    ]

    print(f"\n=== Crypto tail-append: {len(crypto_syms)} symbols ===")
    crypto_appended = 0
    for sym in crypto_syms:
        for tf in ["15m"]:
            bt_path = os.path.join(BT_CRYPTO_DIR, f"{sym}_{tf}.json")
            live_path = os.path.join(LIVE_CRYPTO_DIR, f"{sym}_{tf}.json")
            n = append_new_bars(bt_path, live_path, sym, tf)
            crypto_appended += n

    print(f"\nCrypto: total bars appended = {crypto_appended}")
    print(f"\nDone. Total appended: {total_appended + crypto_appended}")


if __name__ == "__main__":
    main()
