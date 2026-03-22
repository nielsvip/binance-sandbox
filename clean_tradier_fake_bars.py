#!/usr/bin/env python3
"""
One-time cleanup: remove fake pad_with_higher bars from tradier 1h/4h cache files.
Fake bars = consecutive runs of 7+ bars with IDENTICAL open/high/low/close (daily data repeated as hourly).
Run once, then delete.
"""
import json
import os
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "klines_cache" / "tradier"
TARGET_TFS = ["1h", "4h"]


def has_identical_ohlcv(bar1: dict, bar2: dict) -> bool:
    return (
        bar1.get("open") == bar2.get("open")
        and bar1.get("high") == bar2.get("high")
        and bar1.get("low") == bar2.get("low")
        and bar1.get("close") == bar2.get("close")
    )


def strip_fake_bars(bars: list) -> list:
    """Remove bars that are part of a run of 3+ consecutive identical-OHLCV bars."""
    if len(bars) < 3:
        return bars
    n = len(bars)
    fake_indices = set()
    i = 0
    while i < n:
        j = i + 1
        while j < n and has_identical_ohlcv(bars[i], bars[j]):
            j += 1
        run_len = j - i
        if run_len >= 3:
            fake_indices.update(range(i, j))
        i = j
    cleaned = [b for idx, b in enumerate(bars) if idx not in fake_indices]
    return cleaned


def main():
    if not CACHE_DIR.exists():
        print(f"Cache dir not found: {CACHE_DIR}")
        return
    files = sorted(CACHE_DIR.glob("*_1h.json")) + sorted(CACHE_DIR.glob("*_4h.json"))
    total_removed = 0
    files_changed = 0
    for fpath in files:
        try:
            with open(fpath) as f:
                bars = json.load(f)
            if not isinstance(bars, list) or len(bars) < 3:
                continue
            cleaned = strip_fake_bars(bars)
            removed = len(bars) - len(cleaned)
            if removed > 0:
                with open(fpath, "w") as f:
                    json.dump(cleaned, f)
                print(f"  {fpath.name}: removed {removed} fake bars ({len(bars)} -> {len(cleaned)})")
                total_removed += removed
                files_changed += 1
        except Exception as e:
            print(f"  ERROR {fpath.name}: {e}")
    print(f"\nDone. {files_changed} files cleaned, {total_removed} fake bars removed total.")


if __name__ == "__main__":
    main()
