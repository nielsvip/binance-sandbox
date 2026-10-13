#!/usr/bin/env python3
"""parity-loop-crypto 2026-10-06: startup backfill of live 15m klines for the PARITY_VEC_EXACT live_klines twin.

The twin rebuilds the NPZ arrays in memory from klines_cache/{SYM}_15m.json (backtest_v8_precompute.compute_symbol(...,
return_arrays=True)). Live keeps only 1500-2000 15m bars on the Mac (ez_klines._trim '15m': (1500, 2000)), about 16-21 days,
while the sheet window is 30D (2880 bars) plus indicator warm-up. Tokenised stock proxies (GOOGLUSDT, ARUSDT, ...) need
>= 30 RTH sessions, so evaluate_v12._exact_30d_slice refuses 20 sessions.

This tool pages Binance USD-M REST GET /fapi/v1/klines (interval=15m, limit=1500, endTime=<oldest-1>) backwards until
--target bars exist, merges by timestamp (existing rows win), and with --apply writes atomically (tmp + os.replace) in the
same record format as ez_klines. The default is a DRY RUN that writes nothing.

NOTE: without raising ez_klines' Mac 15m retention (staged: data/parity/staged/ez_klines_15m_retention.md), the next
ez_klines write trims the file back to 1500 bars.

  python3 tools/klines_backfill_15m.py                       # dry run over the live crypto universe
  python3 tools/klines_backfill_15m.py --apply --target 3600 # backfill (Mac startup, before ez_manage)
"""
import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://fapi.binance.com/fapi/v1/klines"
ACCOUNTS = ("ang", "fin", "flz", "inf", "men")
TOKENISED_TARGET = 4500


def universe() -> list:
    syms = set()
    try:
        d = json.loads((ROOT / "data" / "vec_live" / "vec_driven.json").read_text())
        syms.update(k.rsplit("_", 1)[0] for k in d if k.endswith(("_LONG", "_SHORT")))
    except Exception:
        pass
    for a in ACCOUNTS:
        for s in ("long", "short"):
            try:
                lst = json.loads((ROOT / f"symbols_{a}_{s}.json").read_text())
                syms.update(str(x).upper() for x in (lst if isinstance(lst, list) else list(lst)))
            except Exception:
                pass
    return sorted(syms)


def _is_tokenised(sym: str) -> bool:
    try:
        import sys
        sys.path.insert(0, str(ROOT))
        from tools.opt.evaluate_v12 import resolve_tokenised
        return bool(resolve_tokenised(sym)[1])
    except Exception:
        return False


def fetch_after(sym: str, start_ms: int, limit: int = 1500, interval: str = "15m") -> list:
    q = urllib.parse.urlencode({"symbol": sym, "interval": interval, "limit": limit, "startTime": start_ms})
    with urllib.request.urlopen(f"{URL}?{q}", timeout=20) as r:
        rows = json.loads(r.read().decode())
    return [{"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(int(k[0]) / 1000)), "open": float(k[1]), "high": float(k[2]),
             "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]


def fetch_before(sym: str, end_ms: int, limit: int = 1500, interval: str = "15m") -> list:
    q = urllib.parse.urlencode({"symbol": sym, "interval": interval, "limit": limit, "endTime": end_ms})
    with urllib.request.urlopen(f"{URL}?{q}", timeout=20) as r:
        rows = json.loads(r.read().decode())
    return [{"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(int(k[0]) / 1000)), "open": float(k[1]), "high": float(k[2]),
             "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]


def _ms(ts: str) -> int:
    import calendar
    return int(calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) * 1000)


TF_SUFFIX = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "D"}


def backfill(sym: str, target: int, apply: bool, cache_dir: str = "klines_cache", max_pages: int = 6, interval: str = "15m", suffix: str = "") -> dict:
    suf = TF_SUFFIX.get(interval, interval)
    p = ROOT / cache_dir / f"{sym}_{suf}{suffix}.json"
    try:
        rows = json.loads(p.read_text())
    except Exception:
        rows = []
    have = len(rows)
    if have >= target:
        return {"sym": sym, "have": have, "target": target, "action": "ok"}
    if not apply:
        return {"sym": sym, "have": have, "target": target, "action": f"would fetch ~{target - have} bars ({-(-(target - have) // 1500)} REST pages)"}
    by_ts = {r["timestamp"]: r for r in rows}
    oldest = min((_ms(t) for t in by_ts), default=int(time.time() * 1000))
    pages = 0
    while len(by_ts) < target and pages < max_pages:
        got = fetch_before(sym, oldest - 1, interval=interval)
        pages += 1
        if not got:
            break
        for r in got:
            by_ts.setdefault(r["timestamp"], r)
        oldest = min(_ms(r["timestamp"]) for r in got)
        time.sleep(0.25)
    merged = [by_ts[k] for k in sorted(by_ts)]
    reached = len(merged) >= target
    if len(merged) > have:
        from klines_guard import guarded_write
        ok, why = guarded_write(p, merged)
        if not ok:
            return {"sym": sym, "have": have, "now": len(merged), "pages": pages, "action": "guard-blocked", "reason": why}
    return {"sym": sym, "have": have, "now": len(merged), "pages": pages, "action": "written" if reached else "short"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=3600, help="15m bars to hold (30D window 2880 + warm-up)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--cache-dir", default="klines_cache", help="cache subdir under ROOT (e.g. klines_cache_backtest for 400D canonical)")
    ap.add_argument("--pages", type=int, default=6, help="max REST pages (1500 bars each) to fetch backwards")
    ap.add_argument("--interval", default="15m", choices=("15m", "1h", "4h", "1d"), help="Binance interval to backfill")
    ap.add_argument("--forward", action="store_true", help="forward-fill tails newer than cache tail (default: backward backfill older than cache head)")
    ap.add_argument("--suffix", default="", help="filename suffix before .json (e.g. .usdc_pure)")
    a = ap.parse_args()
    if not a.symbols:
        suf = TF_SUFFIX.get(a.interval, a.interval)
        pat = f"*_{suf}{a.suffix}.json"
        a.symbols = sorted(p.name[: -len(f"_{suf}{a.suffix}.json")] for p in (ROOT / a.cache_dir).glob(pat))
    for s in a.symbols or universe():
        tgt = max(a.target, TOKENISED_TARGET) if _is_tokenised(s) else a.target
        try:
            if a.forward:
                print(json.dumps(forward_fill(s, a.apply, a.cache_dir, a.pages, a.interval, a.suffix)), flush=True)
            else:
                print(json.dumps(backfill(s, tgt, a.apply, a.cache_dir, a.pages, a.interval, a.suffix)), flush=True)
        except Exception as e:
            print(json.dumps({"sym": s, "error": repr(e)[:160]}), flush=True)


def forward_fill(sym: str, apply: bool, cache_dir: str = "klines_cache_backtest", max_pages: int = 6, interval: str = "15m", suffix: str = "") -> dict:
    """Fetch bars NEWER than the cache tail (startTime=tail+1). Missing file -> no-op (nothing to extend)."""
    suf = TF_SUFFIX.get(interval, interval)
    p = ROOT / cache_dir / f"{sym}_{suf}{suffix}.json"
    try:
        rows = json.loads(p.read_text())
    except Exception:
        rows = []
    if not rows:
        return {"sym": sym, "have": 0, "action": "no-file"}
    by_ts = {r["timestamp"]: r for r in rows}
    have = len(by_ts)
    newest = max((_ms(t) for t in by_ts), default=0)
    if not apply:
        return {"sym": sym, "have": have, "tail": max(by_ts), "action": "would forward-fill"}
    pages = added = 0
    while pages < max_pages:
        got = fetch_after(sym, newest + 1, interval=interval)
        pages += 1
        if not got:
            break
        for r in got:
            if r["timestamp"] not in by_ts:
                by_ts[r["timestamp"]] = r
                added += 1
        newest = max(_ms(r["timestamp"]) for r in got)
        if len(got) < 1500:
            break
        time.sleep(0.25)
    if added:
        merged = [by_ts[k] for k in sorted(by_ts)]
        from klines_guard import guarded_write
        ok, why = guarded_write(p, merged)
        if not ok:
            return {"sym": sym, "have": have, "now": len(by_ts), "added": 0, "pages": pages, "action": "guard-blocked", "reason": why}
    return {"sym": sym, "have": have, "now": len(by_ts), "added": added, "pages": pages, "action": "extended" if added else "fresh"}


if __name__ == "__main__":
    main()
