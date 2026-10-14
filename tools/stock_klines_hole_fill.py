#!/usr/bin/env python3
"""stock_klines_hole_fill — re-fetch MISSING RTH sessions inside klines_cache/tradier/{SYM}_{15m,5m}.json (2026-10-14).

Root cause it repairs: tradier_klines.process_symbol() fetches each intraday TF from the cache's LAST bar date (cached_start).
When no writer ran 2026-08-26 -> 2026-09-24 (the documented D/15m stall, see refresh_daily docstring) and the first resumed
cycle only produced the newest session, the last bar jumped past the hole; every later cycle starts from that newer bar, so the
hole is never re-requested (the after-close REPAIR pass only covers the trailing 7 days).

This tool is hole-only and append-only: expected sessions = the symbol's own Tradier D bars (same file the builder reads) in the
window; for every expected session with no 15m (or 5m) bar it requests Tradier Time&Sales (repo client, sequential, sleeps) for
the [first_missing, last_missing] span and inserts ONLY timestamps that are absent from the file (existing bars never touched).
Writes via tradier_klines.export_atomic (atomic, merge). 1min history is not served by Tradier beyond ~20 days -> not filled.

Usage: python tools/stock_klines_hole_fill.py --symbols GOOGL,NVDA [--days 60] [--tfs 15m,5m] [--dry-run] [--sleep 1.5]
"""
import argparse
import asyncio
import json
import os
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import tradier_klines as TK
INTERVAL = {"15m": "15min", "5m": "5min"}


def load_frame(symbol, tf):
    path = TK.config.KLINES_CACHE_DIR / f"{symbol}_{tf}.json"
    try:
        with path.open(encoding="utf-8") as handle:
            rows = json.load(handle)
    except Exception:
        return pd.DataFrame()
    return TK.parse_intraday(rows)


def expected_sessions(symbol, days):
    daily = TK.daily_frame(json.load((TK.config.KLINES_CACHE_DIR / f"{symbol}_D.json").open(encoding="utf-8")))
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    daily = daily[(daily.index >= cutoff) & (daily["volume"] > 0)]
    return sorted(set(daily.index.tz_convert(TK.ET).date))


def missing_sessions(symbol, tf, days):
    frame = load_frame(symbol, tf)
    have = set(frame.index.tz_convert(TK.ET).date) if len(frame) else set()
    return [d for d in expected_sessions(symbol, days) if d not in have], frame


async def fill_symbol(symbol, tfs, days, dry_run, pause):
    out = {}
    for tf in tfs:
        miss, frame = missing_sessions(symbol, tf, days)
        rec = {"missing_before": [str(d) for d in miss], "inserted": 0, "sessions_filled": []}
        out[tf] = rec
        if not miss:
            continue
        start = (pd.Timestamp(miss[0]) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        end = (pd.Timestamp(miss[-1]) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            raw = await asyncio.wait_for(TK.api_client.get_timesales(symbol, interval=INTERVAL[tf], start=start, end=end), timeout=90)
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"
            print(f"[hole_fill] {symbol} {tf} fetch failed {rec['error']}", flush=True)
            await asyncio.sleep(pause)
            continue
        await asyncio.sleep(pause)
        got = TK.market_hours(TK.parse_intraday(raw), True)
        if got.empty:
            rec["error"] = "vendor returned 0 bars"
            print(f"[hole_fill] {symbol} {tf} vendor returned 0 bars for {start}..{end}", flush=True)
            continue
        got_days = got.index.tz_convert(TK.ET).date
        missing_set = set(miss)
        got = got[[d in missing_set for d in got_days]]
        if len(frame):
            got = got[~got.index.isin(frame.index)]
        got = got[got["close"].notna()]
        rec["sessions_filled"] = sorted({str(d) for d in got.index.tz_convert(TK.ET).date})
        rec["inserted"] = int(len(got))
        rec["still_missing"] = [str(d) for d in miss if str(d) not in rec["sessions_filled"]]
        print(f"[hole_fill] {symbol} {tf} missing {len(miss)} sessions -> vendor rows {len(got)} covering {len(rec['sessions_filled'])} sessions (still missing {len(rec['still_missing'])}){' DRY' if dry_run else ''}", flush=True)
        if not dry_run and len(got):
            ok = TK.export_atomic(got[["open", "high", "low", "close", "volume"]], TK.config.KLINES_CACHE_DIR / f"{symbol}_{tf}.json")
            rec["written"] = bool(ok)
            after, _ = missing_sessions(symbol, tf, days)
            rec["missing_after"] = [str(d) for d in after]
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--tfs", default="15m,5m")
    ap.add_argument("--sleep", type=float, default=1.5)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default="")
    a = ap.parse_args()
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    tfs = [t.strip() for t in a.tfs.split(",") if t.strip() in INTERVAL]
    await TK.api_client.connect()
    res = {}
    try:
        for symbol in symbols:
            try:
                res[symbol] = await fill_symbol(symbol, tfs, a.days, a.dry_run, a.sleep)
            except Exception as exc:
                res[symbol] = {"error": f"{type(exc).__name__}: {exc}"}
                print(f"[hole_fill] {symbol} error {res[symbol]['error']}", flush=True)
    finally:
        await TK.api_client.close()
    if a.report:
        with open(a.report, "w", encoding="utf-8") as handle:
            json.dump({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "dir": str(TK.config.KLINES_CACHE_DIR), "dry_run": a.dry_run, "symbols": res}, handle, indent=1)
    print(f"[hole_fill] done {len(res)} symbols", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
