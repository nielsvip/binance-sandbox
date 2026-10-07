#!/usr/bin/env python3
"""Refetch the last N days of Tradier 15m timesales for each symbol and union-merge into a klines dir.

tradier_klines_append.py only extends before the first bar / after the last bar, so interior holes
(COP 5d, TSLA 7.7d) and stale tails survive. This refetches the whole window in 10-day chunks and
adds every missing timestamp; existing bars are kept on overlap.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tradier_klines_append as T


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", required=True, help="comma list or @file")
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--out", required=True)
    parser.add_argument("--session-ref", required=True, help="dir whose {SYM}_15m.json defines the regular-session HH:MM set")
    args = parser.parse_args()
    symbols = [s.strip() for s in (open(args.symbols[1:]).read().split() if args.symbols.startswith("@") else args.symbols.split(",")) if s.strip()]
    T.load_env_from_gpg()
    token = T.get_tradier_token()
    if not token:
        print("no tradier token", flush=True)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    end = datetime.now(timezone.utc)
    for symbol in symbols:
        path = out / f"{symbol}_15m.json"
        existing = T.load_existing(path)
        merged = {b["timestamp"]: b for b in existing if isinstance(b, dict) and b.get("timestamp")}
        added = 0
        cursor = end - timedelta(days=args.days)
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=10), end)
            try:
                recs = T.fetch_chunk(token, symbol, cursor, chunk_end)
            except Exception as exc:
                print(f"  {symbol} chunk {cursor:%m-%d} err {exc}", flush=True)
                recs = []
            for rec in recs or []:
                bar = T.to_canonical_bar(rec)
                if bar and bar.get("timestamp") and bar["timestamp"] not in merged:
                    merged[bar["timestamp"]] = bar
                    added += 1
            cursor = chunk_end
            time.sleep(0.6)
        # keep only the symbol's established regular-session bar times (Tradier returns 04:00-20:00 ET extended hours)
        ref = [b.get("timestamp", "") for b in T.load_existing(Path(args.session_ref) / f"{symbol}_15m.json") if isinstance(b, dict)]
        slots = {}
        for stamp in ref:
            if stamp < "2026-09-20":
                slots[stamp[11:16]] = slots.get(stamp[11:16], 0) + 1
        days = len({stamp[:10] for stamp in ref if stamp < "2026-09-20"}) or 1
        session = {hm for hm, c in slots.items() if c >= 0.3 * days}
        if not session:
            print(f"{symbol}: no session reference — skipped (not writing unfiltered extended hours)", flush=True)
            continue
        dropped = [k for k in merged if k[11:16] not in session]
        for k in dropped:
            merged.pop(k)
        added -= sum(1 for k in dropped if k not in {b.get("timestamp") for b in existing})
        bars = [merged[k] for k in sorted(merged)]
        if bars:
            T.save_atomic(path, bars)
        print(f"{symbol}: existing {len(existing)} added {added} total {len(bars)} {bars[0]['timestamp'][:16] if bars else ''} -> {bars[-1]['timestamp'][:16] if bars else ''}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
