#!/usr/bin/env python3
"""Protect klines from overwrite + redownload max data for all stocks.
RULE: klines are APPEND-ONLY. Never overwrite with fewer bars."""
import json, time, sys
from pathlib import Path
from datetime import datetime, timezone

# Paths
KLINES_DIRS = [
    Path("/home/niels/binance-sandbox/klines_cache"),
    Path("/home/niels/binance/klines_cache/tradier"),
]

def merge_klines(path_a, path_b, output):
    """Merge two klines files, keeping ALL unique bars from both."""
    a = json.loads(path_a.read_text()) if path_a.exists() else []
    b = json.loads(path_b.read_text()) if path_b.exists() else []
    # Dedup by timestamp
    seen = {}
    for k in a + b:
        ts = k.get("timestamp", "")
        if ts not in seen:
            seen[ts] = k
    merged = sorted(seen.values(), key=lambda x: x.get("timestamp", ""))
    output.write_text(json.dumps(merged))
    return len(a), len(b), len(merged)

def protect_dir(kd):
    """Create a bar-count manifest for protection."""
    manifest = {}
    for f in kd.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            manifest[f.name] = len(data)
        except:
            manifest[f.name] = 0
    manifest_path = kd / "_klines_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Protected {len(manifest)} files in {kd}")
    return manifest

def safe_write_klines(path, new_data):
    """Only write if new data has MORE bars than existing."""
    if path.exists():
        existing = json.loads(path.read_text())
        if len(existing) >= len(new_data):
            print(f"  BLOCKED: {path.name} has {len(existing)} bars, new has {len(new_data)} — keeping existing")
            return False
    path.write_text(json.dumps(new_data))
    return True

if __name__ == "__main__":
    import yfinance as yf

    # Step 1: Protect existing klines
    print("=== STEP 1: Protecting existing klines ===")
    for kd in KLINES_DIRS:
        if kd.exists():
            protect_dir(kd)

    # Step 2: Merge sandbox ↔ tradier (bidirectional — keep the MOST bars)
    print("\n=== STEP 2: Merging sandbox ↔ tradier klines ===")
    sandbox = KLINES_DIRS[0]
    tradier = KLINES_DIRS[1]
    if sandbox.exists() and tradier.exists():
        merged_count = 0
        for f in sandbox.glob("*.json"):
            sym = f.stem.split("_")[0]
            if not sym.isalpha() or len(sym) > 5 or "USDT" in sym or "USDC" in sym:
                continue
            tradier_f = tradier / f.name
            if tradier_f.exists():
                a_bars, b_bars, merged_bars = merge_klines(f, tradier_f, tradier_f)
                if merged_bars > max(a_bars, b_bars):
                    # Also update sandbox copy
                    merge_klines(tradier_f, f, f)
                    merged_count += 1
                    if merged_count <= 5:
                        print(f"  {f.name}: sandbox={a_bars} tradier={b_bars} → merged={merged_bars}")
            elif sym.isalpha() and len(sym) <= 5:
                # Copy to tradier if not exists
                data = json.loads(f.read_text())
                if data:
                    tradier_f.write_text(json.dumps(data))
        print(f"  Merged {merged_count} files")

    # Step 3: Redownload ALL stock klines via Yahoo Finance
    print("\n=== STEP 3: Downloading max stock klines ===")
    # Get stock symbols
    stock_syms = set()
    for kd in KLINES_DIRS:
        if not kd.exists(): continue
        for f in kd.glob("*_1h.json"):
            sym = f.stem.replace("_1h", "")
            if sym.isalpha() and len(sym) <= 5 and "USDT" not in sym:
                stock_syms.add(sym)
    stock_syms = sorted(stock_syms)
    print(f"  {len(stock_syms)} stock symbols")

    yahoo_tfs = [
        ("1h", "max", "1h"),
        ("15m", "60d", "15m"),
        ("5m", "60d", "5m"),
        ("1m", "7d", "1m"),
        ("1d", "max", "D"),
        ("1wk", "max", "W"),
    ]

    downloaded = 0
    for sym in stock_syms:
        for yf_interval, yf_period, local_tf in yahoo_tfs:
            for kd in KLINES_DIRS:
                if not kd.exists(): continue
                out_path = kd / f"{sym}_{local_tf}.json"
                existing = json.loads(out_path.read_text()) if out_path.exists() else []
                existing_ts = {k["timestamp"] for k in existing}

                try:
                    ticker = yf.Ticker(sym)
                    data = ticker.history(period=yf_period, interval=yf_interval)
                    if len(data) < 10: continue

                    new_bars = []
                    for ts, row in data.iterrows():
                        bar = {
                            "timestamp": ts.isoformat(),
                            "open": float(row["Open"]),
                            "high": float(row["High"]),
                            "low": float(row["Low"]),
                            "close": float(row["Close"]),
                            "volume": float(row["Volume"]),
                        }
                        if bar["timestamp"] not in existing_ts:
                            new_bars.append(bar)

                    if new_bars:
                        merged = sorted(existing + new_bars, key=lambda x: x["timestamp"])
                        out_path.write_text(json.dumps(merged))
                        downloaded += 1
                        if downloaded <= 10:
                            print(f"  {sym}_{local_tf}: {len(existing)} → {len(merged)} (+{len(new_bars)} new)")

                    time.sleep(0.2)
                except Exception as e:
                    if downloaded <= 3:
                        print(f"  {sym}_{local_tf}: ERROR {e}")
                    time.sleep(0.5)

        if (stock_syms.index(sym) + 1) % 20 == 0:
            print(f"  [{stock_syms.index(sym)+1}/{len(stock_syms)}] {downloaded} files updated")

    print(f"\n=== DONE: {downloaded} files updated ===")

    # Step 4: Re-protect
    print("\n=== STEP 4: Re-protecting ===")
    for kd in KLINES_DIRS:
        if kd.exists():
            protect_dir(kd)
