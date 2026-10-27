#!/usr/bin/env python3
"""Authoritative crypto fills straight from Binance userTrades.

Every row is a real exchange fill: ms timestamp, orderId, price, qty,
realizedPnl. No ledger, no double-logs, no fragments to guess at — partial
fills of one order share its orderId and merge exactly. Round trips come
from FIFO exposure per (symbol, positionSide); trip win comes from the
exchange's own realizedPnl with a FIFO price fallback.

Cache: data/exchange_fills/{acct}/{SYM}.json, incremental (7d chunks, newest
first backfill to lookback). A failed refresh keeps serving stale cache.
"""
import hashlib
import hmac
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data" / "exchange_fills"
FAPI = "https://fapi.binance.com"
CHUNK_MS = 7 * 86400 * 1000
RATE_PAUSE_S = 0.15


def _keys(acct):
    import os
    key = os.getenv(f"{acct}_API_KEY")
    sec = os.getenv(f"{acct}_API_SECRET")
    if not key or not sec:
        try:
            import sys
            sys.path.insert(0, str(ROOT / "tools"))
            sys.path.insert(0, str(ROOT))
            from utils import load_environment_from_gpg
            load_environment_from_gpg()
            key = os.getenv(f"{acct}_API_KEY")
            sec = os.getenv(f"{acct}_API_SECRET")
        except Exception:
            pass
    return key, sec


def _signed_get(acct, path, params, tries=3):
    import os
    key, sec = _keys(acct)
    if not key or not sec:
        raise RuntimeError(f"no API key for account {acct}")
    last = None
    for _ in range(tries):
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        qs = f"{qs}&timestamp={int(time.time() * 1000)}"
        sig = hmac.new(sec.encode(), qs.encode(), hashlib.sha256).hexdigest()
        req = urllib.request.Request(f"{FAPI}{path}?{qs}&signature={sig}",
                                     headers={"X-MBX-APIKEY": key})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                time.sleep(RATE_PAUSE_S)
                return json.loads(r.read())
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise RuntimeError(f"userTrades failed for {acct}: {last}")


def _cache_path(acct, sym):
    return CACHE_DIR / acct / f"{str(sym).upper()}.json"


def _load_cache(acct, sym):
    try:
        d = json.loads(_cache_path(acct, sym).read_text())
        return d.get("rows") or [], int(d.get("cached_to_ms") or 0)
    except Exception:
        return [], 0


def _save_cache(acct, sym, rows, cached_to_ms):
    try:
        p = _cache_path(acct, sym)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"cached_to_ms": cached_to_ms, "rows": rows}))
        tmp.replace(p)
    except Exception:
        pass


def refresh_symbol(acct, sym, lookback_days=40):
    """Incremental pull; returns all cached rows (oldest-first)."""
    sym = str(sym).upper()
    rows, cached_to = _load_cache(acct, sym)
    now_ms = int(time.time() * 1000)
    start_ms = max(cached_to + 1, now_ms - lookback_days * 86400 * 1000) if rows \
        else now_ms - lookback_days * 86400 * 1000
    if start_ms >= now_ms - 60000 and rows:
        return rows
    try:
        new_rows, lo = [], start_ms
        while lo < now_ms:
            hi = min(lo + CHUNK_MS, now_ms)
            chunk = _signed_get(acct, "/fapi/v1/userTrades",
                                {"symbol": sym, "startTime": lo, "endTime": hi, "limit": 1000})
            if chunk:
                new_rows.extend(chunk)
                lo = int(chunk[-1]["time"]) + 1
                if len(chunk) >= 1000:
                    continue
            if lo < hi:
                lo = hi
            else:
                break
        if new_rows:
            seen = {(r.get("id"), r.get("orderId")) for r in rows}
            rows = rows + [r for r in new_rows if (r.get("id"), r.get("orderId")) not in seen]
            rows.sort(key=lambda r: (int(r.get("time") or 0), int(r.get("id") or 0)))
        # 2026-10-27: ALWAYS checkpoint after a clean walk to now_ms, even when
        # the exchange returned nothing. `cached_to_ms` means "verified complete
        # up to here", which is what every consumer assumes, but the save used
        # to be inside `if new_rows:` — so a symbol that simply had not traded
        # never recorded that it was checked and looked permanently stale. That
        # is why 235 of 337 caches read stale (median 16.8h) while the rows
        # themselves were current, and why broker_truth refused to self-certify.
        # A re-check costs one request because start_ms advances with it.
        _save_cache(acct, sym, rows, now_ms)
    except Exception:
        # A FAILED walk must NOT advance the checkpoint: leaving cached_to_ms
        # behind is what makes the next run retry the same span.
        pass
    return rows


def _to_events(rows):
    """Raw rows -> one order-fill each (partials of one orderId merged)."""
    by_order, order = {}, []
    for r in rows:
        try:
            oid = int(r.get("orderId"))
            ts = datetime.fromtimestamp(int(r["time"]) / 1000, tz=timezone.utc)
            qty = abs(float(r.get("qty") or 0))
            px = float(r.get("price") or 0)
            rp = float(r.get("realizedPnl") or 0)
        except (TypeError, ValueError, KeyError):
            continue
        if qty <= 0:
            continue
        side = str(r.get("positionSide") or "BOTH").upper()
        direction = "buy" if str(r.get("side") or "").upper() == "BUY" else "sell"
        if oid not in by_order:
            by_order[oid] = {"timestamp": ts, "side": side, "direction": direction,
                             "qty": 0.0, "cost": 0.0, "pnl": 0.0, "order": oid}
            order.append(oid)
        o = by_order[oid]
        o["timestamp"] = min(o["timestamp"], ts)
        o["qty"] += qty
        o["cost"] += px * qty
        o["pnl"] += rp
    out = []
    for oid in order:
        o = by_order[oid]
        if o["qty"] <= 0:
            continue
        out.append({"timestamp": o["timestamp"], "side": o["side"], "direction": o["direction"],
                    "qty": o["qty"], "price": o["cost"] / o["qty"], "pnl": o["pnl"],
                    "order": oid, "phase": None, "win": None})
    out.sort(key=lambda e: e["timestamp"])
    return out


def trip_tagged(events):
    """FIFO trips per positionSide; win from exchange realizedPnl (FIFO fallback)."""
    by_side, out = {}, []
    for e in events:
        by_side.setdefault(e["side"], []).append(e)
    for side, evs in by_side.items():
        lots, trip_pnl, trip_open, exposure = [], 0.0, [], 0.0
        sign = -1.0 if side == "SHORT" else 1.0
        for e in evs:
            d = e["qty"] if (e["direction"] == "buy") == (sign > 0) else -e["qty"]
            if exposure == 0 or (d > 0) == (exposure > 0):
                e["phase"] = "entry"
                lots.append([e["qty"], e["price"]])
                exposure += d
                trip_open.append(e)
                trip_pnl += e["pnl"]
            else:
                need, matched_cost, matched_q = abs(d), 0.0, 0.0
                while need > 1e-9 and lots:
                    lq, lp = lots[0]
                    take = min(lq, need)
                    matched_cost += take * lp
                    matched_q += take
                    lq -= take
                    need -= take
                    if lq <= 1e-9:
                        lots.pop(0)
                    else:
                        lots[0][0] = lq
                exposure += d
                trip_pnl += e["pnl"]
                if abs(exposure) <= max(1e-9, 1e-6 * abs(d)):
                    if abs(trip_pnl) > 0:
                        win = trip_pnl > 0
                    elif matched_q > 0 and matched_cost > 0:
                        avg = matched_cost / matched_q
                        win = ((e["price"] - avg) / avg * sign) > 0
                    else:
                        win = None
                    e["phase"] = "exit"
                    e["win"] = win
                    for o in trip_open:
                        o["win"] = win
                    lots, trip_pnl, trip_open, exposure = [], 0.0, [], 0.0
                else:
                    e["phase"] = "adapt"
            out.append(e)
    out.sort(key=lambda e: e["timestamp"])
    return out


def _broker_open(accts, sym):
    """(acct, side) currently holding per live position files (fresh only)."""
    import json as _json
    import time as _time
    sym = str(sym).upper()
    open_set = set()
    for acct in accts or []:
        for side, fname in (("LONG", "long_positions.json"), ("SHORT", "short_positions.json")):
            fp = ROOT / acct / fname
            try:
                if not fp.is_file() or _time.time() - fp.stat().st_mtime > 86400:
                    continue
                data = _json.loads(fp.read_text())
            except Exception:
                continue
            if isinstance(data, dict):
                for k, v in data.items():
                    try:
                        if abs(float((v or {}).get("positionAmt") or 0)) <= 1e-9:
                            continue
                        s = str((v or {}).get("symbol") or "").upper()
                        if not s:
                            parts = str(k).split(":")
                            s = parts[1] if len(parts) > 1 else parts[0]
                            import re as _re
                            s = _re.sub(r"_(LONG|SHORT)$", "", s.upper())
                        if s == sym:
                            open_set.add((acct, side))
                    except (TypeError, ValueError):
                        continue
    return open_set


def fetch_exchange_events(sym, accts, lookback_days=40):
    """{acct: [entry/exit/adapt events]} from exchange fills. Plots filter.

    Trailing unclosed entries older than 30min on a broker-flat side go
    stale (same rule as the ledger path): the close is missing from API
    coverage, and a phantom open arrow is worse than none.
    """
    import live_trade_history as lth
    sym_u = str(sym).upper()
    open_set = _broker_open(accts, sym_u)
    out = {}
    for acct in accts or []:
        try:
            rows = refresh_symbol(acct, sym, lookback_days)
        except Exception:
            continue
        if not rows:
            continue
        evs = trip_tagged(_to_events(rows))
        by_side = {}
        for e in evs:
            by_side.setdefault(e["side"], []).append(e)
        for side, recs in by_side.items():
            if (acct, side) not in open_set:
                lth._mark_stale_trailing(recs)
        out[acct] = evs
    return out
