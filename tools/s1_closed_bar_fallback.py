#!/usr/bin/env python3
"""s1_closed_bar_fallback — per-symbol, only-when-missing closed 15m bars for the forward vector lane on S1.

USER 2026-10-27: "unreliable ssh is another reason s1 needs to fall back to its own direct vector trading script immediately
when it misses a trade - and calculate its own klines when that happens so the forward vector trading remains intact."

S1 normally receives every closed 15m bar from the Mac (tools/closed_15m_feed.py -> data/closed_15m_bars/crypto/<SYM>.json ->
tools/npz_tail_service.py appends the NPZ -> data/forward_state). This script does NOTHING while those bars arrive. GRACE_S after
each 15m close it looks, per symbol of the forward universe, for the just-closed bar in data/closed_15m_bars/crypto/<SYM>.json;
only the symbols whose bar is MISSING are fetched from Binance (GET /fapi/v1/klines, weight 1, the exchange's own closed bar)
and handed to closed_bar_feed.receive_closed_15m — the same function the Mac lane lands through, so the row sits exactly where a
Mac row sits and preferred_closed_row arbitrates if the Mac row still arrives. A second look at RETRY_S catches fetch failures.
Unlike tools/s1_klines_self_produce.py (DISABLED 2026-10-27 "wrong design": unconditional, outside the ordered path) it never
touches klines_cache* and never writes a bar that is already there.

Run:  tools/s1_closed_bar_fallback.py --loop            (systemd s1-closed-bar-fallback.service)
      tools/s1_closed_bar_fallback.py --once --bar <bar_start_epoch> [--dry-run]
Report: data/reports/s1_closed_bar_fallback_latest.json, history data/reports/s1_closed_bar_fallback.jsonl
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BAR_S = 900
GRACE_S = float(os.environ.get("S1_BAR_FALLBACK_GRACE_S", "40"))
RETRY_S = float(os.environ.get("S1_BAR_FALLBACK_RETRY_S", "120"))
THREADS = int(os.environ.get("S1_BAR_FALLBACK_THREADS", "6"))
MARK_FIRST = os.environ.get("S1_BAR_MARK_FIRST", "0") == "1"
MARK_PASSES_S = tuple(float(x) for x in os.environ.get("S1_BAR_MARK_PASSES_S", "1,3,6").split(",") if x)
MARK_THREADS = int(os.environ.get("S1_BAR_MARK_THREADS", "24"))
HEAL_S = float(os.environ.get("S1_BAR_HEAL_S", "15"))
FAPI = "https://fapi.binance.com/fapi/v1/klines"
SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
REPORT = ROOT / "data" / "reports" / "s1_closed_bar_fallback_latest.json"
HISTORY = ROOT / "data" / "reports" / "s1_closed_bar_fallback.jsonl"
BLOCK = ROOT / "data" / "reports" / "s1_closed_bar_fallback_rest_block.json"


def log(msg):
    print(f"[{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}", flush=True)


def universe(sides_files):
    syms = set()
    for f in sides_files:
        try:
            for tok in Path(f).expanduser().read_text().split():
                if tok.endswith(("_LONG", "_SHORT")):
                    sym = tok.rsplit("_", 1)[0].upper()
                    if sym.endswith(SUFFIXES):
                        syms.add(sym)
        except OSError:
            continue
    return sorted(syms)


def has_bar(base: Path, symbol: str, start: int) -> bool:
    from closed_bar_feed import timestamp_epoch
    path = base / "data" / "closed_15m_bars" / "crypto" / f"{symbol}.json"
    try:
        tail = path.read_bytes()[-6000:].decode("utf-8", "ignore")
    except OSError:
        return False
    stamp = dt.datetime.fromtimestamp(start, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")
    if stamp not in tail:
        return False
    try:
        return any(timestamp_epoch(r.get("timestamp")) == start for r in json.loads(path.read_text())[-4:])
    except Exception:
        return True


def session(local_ip):
    import requests
    from requests.adapters import HTTPAdapter

    class _Bound(HTTPAdapter):
        def init_poolmanager(self, *a, **k):
            k["source_address"] = (local_ip, 0)
            return super().init_poolmanager(*a, **k)

    s = requests.Session()
    if local_ip:
        try:
            probe = socket.socket()
            probe.bind((local_ip, 0))
            probe.close()
            s.mount("https://", _Bound())
        except OSError:
            log(f"{local_ip} not bindable; default routing")
    return s


def fetch_bar(s, symbol, start):
    try:
        until = float(json.loads(BLOCK.read_text()).get("until") or 0)
        if time.time() < until:
            return None, f"rest blocked until {until:.0f}"
    except Exception:
        pass
    r = s.get(FAPI, params={"symbol": symbol, "interval": "15m", "startTime": start * 1000, "limit": 1}, timeout=10)
    if r.status_code in (418, 429):
        ra = float(r.headers.get("Retry-After") or (900 if r.status_code == 418 else 120))
        BLOCK.parent.mkdir(parents=True, exist_ok=True)
        BLOCK.write_text(json.dumps({"until": time.time() + ra, "status": r.status_code}))
        return None, f"HTTP {r.status_code}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code} {r.text[:80]}"
    rows = r.json()
    if not rows or int(rows[0][0]) != start * 1000:
        return None, "bar not returned"
    k = rows[0]
    now = time.time()
    if int(k[6]) >= now * 1000:
        return None, "bar not closed on exchange"
    return {"timestamp": dt.datetime.fromtimestamp(start, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
            "_closed": True, "_provisional": False, "_closed_verified_at": now, "_source": "binance_rest_kline_s1_fallback",
            "_producer": "s1_closed_bar_fallback"}, ""


def stored_provisional(base: Path, symbol: str, start: int) -> bool:
    from closed_bar_feed import timestamp_epoch
    path = base / "data" / "closed_15m_bars" / "crypto" / f"{symbol}.json"
    try:
        return any(timestamp_epoch(r.get("timestamp")) == start and r.get("_provisional") for r in json.loads(path.read_text())[-4:])
    except Exception:
        return False


def mark_row(symbol, start):
    """Provisional bar at :15:00: native 1m :00-:13 + mark-price 1m :14 (closes exactly at the bar close)."""
    import mark_kline_bar_crypto as M
    b = M.build(symbol, start)
    if not b.get("complete"):
        return None, b.get("reason", "mark bar incomplete")
    return {"timestamp": dt.datetime.fromtimestamp(start, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
            "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"], "volume": b["volume_native_0_12"],
            "_closed": True, "_provisional": True, "_source": "binance_mark_1m_s1_fallback", "_mark_built_at": time.time(),
            "_producer": "s1_closed_bar_fallback"}, ""


def fill(base, symbols, start, s, dry_run=False, mode="real"):
    """mode mark: provisional mark bar for symbols with no bar. heal: real kline for symbols with no bar or a
    provisional one (arbitration in closed_bar_feed replaces the provisional row). real: plain fallback."""
    from closed_bar_feed import receive_closed_15m

    def wanted(x):
        if not has_bar(base, x, start):
            return True
        return mode == "heal" and stored_provisional(base, x, start)

    missing = [x for x in symbols if wanted(x)]
    res = {"bar_start": start, "checked": len(symbols), "missing": len(missing), "filled": [], "failed": {}}
    if not missing:
        return res

    def one(symbol):
        row, why = mark_row(symbol, start) if mode == "mark" else fetch_bar(s, symbol, start)
        if row is None:
            return symbol, why
        if has_bar(base, symbol, start) and not stored_provisional(base, symbol, start):
            return symbol, "arrived_meanwhile"
        if dry_run:
            return symbol, "dry"
        ev = {"symbol": symbol, "mode": "crypto", "bar_start_ts": start, "bar_close_ts": start + BAR_S, "published_at": time.time(), "row": row}
        receive_closed_15m(str(base), ev)
        return symbol, ""

    with cf.ThreadPoolExecutor(max_workers=MARK_THREADS if mode == "mark" else THREADS) as ex:
        for symbol, why in ex.map(lambda x: _safe(one, x), missing):
            if why in ("", "dry"):
                res["filled"].append(symbol)
            elif why != "arrived_meanwhile":
                res["failed"][symbol] = why
    return res


def _safe(fn, x):
    try:
        return fn(x)
    except Exception as exc:
        return x, f"{type(exc).__name__}: {str(exc)[:100]}"


def report(res):
    res = dict(res, at=time.time(), iso=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, indent=1))
    os.replace(tmp, REPORT)
    with open(HISTORY, "a") as fh:
        fh.write(json.dumps(res, separators=(",", ":")) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(ROOT))
    ap.add_argument("--sides-file", action="append", default=None)
    ap.add_argument("--local-ip", default=os.environ.get("VD_LOCAL_IP", "5.75.211.216"))
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--bar", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    base = Path(a.base).expanduser()
    sides = a.sides_file or [str(Path.home() / "fwdstate_syms.txt")]
    s = session(a.local_ip)
    if a.once:
        start = a.bar or int(time.time() // BAR_S) * BAR_S - BAR_S
        res = fill(base, universe(sides), start, s, a.dry_run)
        log(f"bar {start}: checked {res['checked']} missing {res['missing']} filled {len(res['filled'])} failed {res['failed']}")
        return 0
    log(f"s1_closed_bar_fallback loop: grace {GRACE_S}s retry {RETRY_S}s sides {sides}")
    while True:
        close = (int(time.time() // BAR_S) + 1) * BAR_S
        plan = ([(d, "mark") for d in MARK_PASSES_S] + [(HEAL_S, "heal"), (RETRY_S, "real")]) if MARK_FIRST else [(GRACE_S, "real"), (RETRY_S, "real")]
        for delay, mode in plan:
            wait = close + delay - time.time()
            if wait > 0:
                time.sleep(wait)
            start = close - BAR_S
            try:
                symbols = universe(sides)
                res = fill(base, symbols, start, s, mode=mode)
                res["mode"] = mode
                res["pass_delay_s"] = delay
                report(res)
                if res["missing"]:
                    log(f"bar {dt.datetime.fromtimestamp(start, dt.timezone.utc):%H:%M}Z +{delay:.0f}s: {res['missing']}/{res['checked']} Mac bars missing -> filled {len(res['filled'])} {res['filled'][:8]} failed {dict(list(res['failed'].items())[:5])}")
            except Exception as exc:
                log(f"pass failed: {type(exc).__name__}: {str(exc)[:200]}")


if __name__ == "__main__":
    sys.exit(main())
