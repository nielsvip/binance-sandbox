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


def fetch_before(sym: str, end_ms: int, limit: int = 1500) -> list:
    q = urllib.parse.urlencode({"symbol": sym, "interval": "15m", "limit": limit, "endTime": end_ms})
    with urllib.request.urlopen(f"{URL}?{q}", timeout=20) as r:
        rows = json.loads(r.read().decode())
    return [{"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(int(k[0]) / 1000)), "open": float(k[1]), "high": float(k[2]),
             "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]


def _ms(ts: str) -> int:
    import calendar
    return int(calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) * 1000)


def backfill(sym: str, target: int, apply: bool) -> dict:
    p = ROOT / "klines_cache" / f"{sym}_15m.json"
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
    while len(by_ts) < target and pages < 6:
        got = fetch_before(sym, oldest - 1)
        pages += 1
        if not got:
            break
        for r in got:
            by_ts.setdefault(r["timestamp"], r)
        oldest = min(_ms(r["timestamp"]) for r in got)
        time.sleep(0.25)
    merged = [by_ts[k] for k in sorted(by_ts)]
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, indent=2))
    os.replace(tmp, p)
    return {"sym": sym, "have": have, "now": len(merged), "pages": pages, "action": "written"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=3600, help="15m bars to hold (30D window 2880 + warm-up)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--symbols", nargs="*")
    a = ap.parse_args()
    for s in a.symbols or universe():
        tgt = max(a.target, TOKENISED_TARGET) if _is_tokenised(s) else a.target
        try:
            print(json.dumps(backfill(s, tgt, a.apply)), flush=True)
        except Exception as e:
            print(json.dumps({"sym": s, "error": repr(e)[:160]}), flush=True)


if __name__ == "__main__":
    main()
