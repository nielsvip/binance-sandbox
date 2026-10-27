#!/usr/bin/env python3
"""vector_direct_fill_export — runs ON S1 (crypto) / S5 (stocks); prints the real fills of tools/vector_direct_trader.py as JSON lines.

USER 2026-10-27 (verbatim): "Every REAL trade has to be added to the /history/ folder on macbook with the quantity $ value and
reason for the trade."

The trader's ledger (data/vector_direct/ledger_<venue>_<day>.jsonl) records every order it SENT with the reason (`why`), but the
Binance response carries avgPrice=null and the ledger `price` is the PRE-trade mark, not the fill. This helper resolves the real
fill of each SENT order once from the broker (Binance GET /fapi/v1/order, weight 1; Tradier GET /accounts/{id}/orders/{id}) and
caches it in data/vector_direct/fills_<venue>.json, so a steady-state run makes ZERO broker calls. An ERROR row whose failure was
not a definite 4xx rejection (timeout / 5xx / connection) may still have filled: it is looked up once by the trader's own
deterministic client id (crypto newClientOrderId "vd"+sha1(key)[:24], stocks tag "vd-"+sha1(key)[:20]).

Read-only on the broker. Never sends, cancels or changes anything. Honors the trader's REST block file (rest_block_crypto.json,
written after a 418/429) and writes it on a 418/429 itself (the IP was banned before). At most --max-resolve broker calls per run.
Consumer: tools/vector_direct_history_import.py on the Mac (one ssh per host per cycle) appends the rows to data/history.
Does NOT import or modify tools/vector_direct_trader.py (owned by another agent).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

FINAL_STATES = {
    "FILLED",
    "CANCELED",
    "EXPIRED",
    "REJECTED",
    "EXPIRED_IN_MATCH",
    "filled",
    "canceled",
    "expired",
    "rejected",
}


def read_json(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def atomic_write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=0, default=str))
    os.replace(tmp, path)


def load_env(root: Path) -> dict:
    env = {}
    for p in (
        root / ".env",
        Path.home() / ".env",
        Path.home() / "binance-sandbox" / ".env",
        Path.home() / "binance" / ".env",
    ):
        try:
            for line in Path(p).expanduser().read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip().replace("export ", "").strip()
                v = v.strip().strip('"').strip("'")
                if k and v and k not in env:
                    env[k] = v
        except OSError:
            continue
    return env


def trader_local_ip() -> str | None:
    """The running trader's --local-ip (S1 binds a secondary IP for Binance); read from /proc, None when absent."""
    try:
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                argv = (d / "cmdline").read_bytes().split(b"\0")
            except OSError:
                continue
            if (
                any(a.endswith(b"vector_direct_trader.py") for a in argv)
                and b"--local-ip" in argv
            ):
                i = argv.index(b"--local-ip")
                if i + 1 < len(argv) and argv[i + 1]:
                    return argv[i + 1].decode()
    except OSError:
        pass
    return None


def ledger_rows(out: Path, venue: str, since: float, now: float) -> list:
    rows = []
    day = dt.datetime.fromtimestamp(since, dt.timezone.utc).date()
    last = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
    while day <= last:
        p = out / f"ledger_{venue}_{day:%Y-%m-%d}.jsonl"
        try:
            with open(p) as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if (
                        isinstance(r, dict)
                        and r.get("event") == "ORDER"
                        and not r.get("dry_run")
                        and float(r.get("ts") or 0) >= since
                    ):
                        rows.append(r)
        except OSError:
            pass
        day += dt.timedelta(days=1)
    return rows


def client_id(venue: str, key: str) -> str:
    h = hashlib.sha1(str(key).encode()).hexdigest()
    return ("vd" + h[:24]) if venue == "crypto" else ("vd-" + h[:20])


def ambiguous_error(r: dict) -> bool:
    """ERROR rows that may still have reached the broker: anything but a definite HTTP 4xx rejection or a local refusal."""
    if r.get("status") != "ERROR":
        return False
    d = str(r.get("detail") or "")
    if d.startswith("HTTP 4") or "missing " in d or "sizing failed" in d:
        return False
    return True


def split_ss(ss: str):
    sym, side = str(ss).rsplit("_", 1)
    return sym, side


class Blocked(Exception):
    pass


class BinanceRO:
    BASE = "https://fapi.binance.com"

    def __init__(self, env, out: Path, local_ip=None):
        import requests
        from requests.adapters import HTTPAdapter

        self.env, self.out = env, out
        self.block_path = out / "rest_block_crypto.json"
        self.s = requests.Session()
        if local_ip:

            class _Bound(HTTPAdapter):
                def init_poolmanager(self, *a, **k):
                    k["source_address"] = (local_ip, 0)
                    return super().init_poolmanager(*a, **k)

            self.s.mount("https://", _Bound())

    def _get(self, acct, path, params):
        until = float((read_json(self.block_path, {}) or {}).get("until") or 0)
        if time.time() < until:
            raise Blocked(f"REST blocked until {until:.0f}")
        key, sec = self.env.get(f"{acct}_API_KEY"), self.env.get(f"{acct}_API_SECRET")
        if not key or not sec:
            raise RuntimeError(f"missing {acct} keys")
        params = dict(params, timestamp=int(time.time() * 1000), recvWindow=10000)
        q = urllib.parse.urlencode(params)
        sig = hmac.new(sec.encode(), q.encode(), hashlib.sha256).hexdigest()
        r = self.s.get(
            f"{self.BASE}{path}?{q}&signature={sig}",
            headers={"X-MBX-APIKEY": key},
            timeout=15,
        )
        if r.status_code in (418, 429):
            ra = float(
                r.headers.get("Retry-After") or (900 if r.status_code == 418 else 120)
            )
            atomic_write_json(
                self.block_path,
                {
                    "until": time.time() + ra,
                    "status": r.status_code,
                    "at": time.time(),
                    "path": path,
                    "by": "vector_direct_fill_export",
                },
            )
            raise Blocked(f"HTTP {r.status_code}")
        if r.status_code == 400 and '"code":-2013' in r.text:
            return None
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} {r.text[:160]}")
        return r.json()

    def resolve(self, acct, ss, order_id=None, client=None):
        sym, _side = split_ss(ss)
        params = {"symbol": sym}
        if order_id:
            params["orderId"] = int(order_id)
        else:
            params["origClientOrderId"] = client
        o = self._get(acct, "/fapi/v1/order", params)
        if o is None:
            return {"found": False}
        q = float(o.get("executedQty") or 0)
        px = float(o.get("avgPrice") or 0)
        cq = float(o.get("cumQuote") or 0) or q * px
        return {
            "found": True,
            "order_id": str(o.get("orderId")),
            "client_order_id": o.get("clientOrderId"),
            "status": o.get("status"),
            "exec_qty": q,
            "avg_price": px,
            "quote": cq,
            "fill_ms": int(o.get("updateTime") or o.get("time") or 0),
            "side": o.get("side"),
            "position_side": o.get("positionSide"),
            "src": "binance GET /fapi/v1/order",
        }


class TradierRO:
    def __init__(self, env):
        import requests

        self.env, self.s = env, requests.Session()
        self._lists = {}

    def account(self, acct):
        a = acct.upper()
        if acct == "trc":
            aid = self.env.get("TRADIER_ACCOUNT_ID_TRC") or self.env.get(
                "TRADIER_SANDBOX_ID"
            )
            key = (
                self.env.get("TRADIER_API_KEY_TRC")
                or self.env.get("TRADIER_SANDBOX_ACCESS_TOKEN")
                or self.env.get("TRADIER_SANDBOX_KEY")
            )
            return aid, key, "https://sandbox.tradier.com/v1"
        aid = self.env.get(f"TRADIER_ACCOUNT_ID_{a}") or (
            self.env.get("TRADIER_ACCOUNT_ID") if acct == "tra" else None
        )
        key = (
            self.env.get(f"TRADIER_API_KEY_{a}")
            or self.env.get("TRADIER_API_KEY_TRA")
            or self.env.get("TRADIER_API_KEY")
        )
        return aid, key, "https://api.tradier.com/v1"

    def _get(self, acct, path):
        _aid, key, base = self.account(acct)
        r = self.s.get(
            base + path,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=15,
        )
        if r.status_code == 429:
            raise Blocked("HTTP 429")
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} {r.text[:160]}")
        return r.json()

    @staticmethod
    def _fill(o):
        q = float(o.get("exec_quantity") or 0)
        px = float(o.get("avg_fill_price") or 0)
        ts = o.get("transaction_date") or o.get("create_date")
        try:
            ms = int(
                dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                * 1000
            )
        except Exception:
            ms = 0
        return {
            "found": True,
            "order_id": str(o.get("id")),
            "client_order_id": o.get("tag"),
            "status": o.get("status"),
            "exec_qty": q,
            "avg_price": px,
            "quote": q * px,
            "fill_ms": ms,
            "side": o.get("side"),
            "src": "tradier GET /accounts/{id}/orders/{id}",
        }

    def resolve(self, acct, ss, order_id=None, client=None):
        aid, _k, _b = self.account(acct)
        if order_id:
            doc = self._get(acct, f"/accounts/{aid}/orders/{order_id}")
            o = (doc or {}).get("order")
            return self._fill(o) if isinstance(o, dict) else {"found": False}
        if acct not in self._lists:
            doc = self._get(acct, f"/accounts/{aid}/orders") or {}
            rows = (
                (doc.get("orders") or {}) if isinstance(doc.get("orders"), dict) else {}
            )
            rows = rows.get("order") or []
            self._lists[acct] = [rows] if isinstance(rows, dict) else rows
        for o in self._lists[acct]:
            if str(o.get("tag") or "") == client:
                return self._fill(o)
        return {"found": False}


def needs_resolve(cached) -> bool:
    if not cached:
        return True
    if cached.get("found") is False:
        return False
    return str(cached.get("status") or "") not in FINAL_STATES or (
        float(cached.get("exec_qty") or 0) > 0
        and float(cached.get("avg_price") or 0) <= 0
    )


def export(
    venue: str,
    root: Path,
    since: float,
    max_resolve: int,
    broker=None,
    now=None,
    emit=print,
) -> dict:
    now = now or time.time()
    out = root / "data" / "vector_direct"
    cache_p = out / f"fills_{venue}.json"
    cache = read_json(cache_p, {}) or {}
    rows = ledger_rows(out, venue, since, now)
    calls, changed, stats = (
        0,
        False,
        {
            "ledger_orders": len(rows),
            "sent": 0,
            "ambiguous_errors": 0,
            "resolved_now": 0,
            "pending": 0,
            "blocked": None,
        },
    )
    for r in rows:
        status = r.get("status")
        if status == "SENT":
            stats["sent"] += 1
            oid = str((r.get("resp") or {}).get("orderId") or "")
            ck = (
                f"oid:{r.get('acct')}:{oid}"
                if oid
                else f"cid:{r.get('acct')}:{client_id(venue, r.get('key'))}"
            )
        elif ambiguous_error(r):
            stats["ambiguous_errors"] += 1
            oid = ""
            ck = f"cid:{r.get('acct')}:{client_id(venue, r.get('key'))}"
        else:
            continue
        cached = cache.get(ck)
        if needs_resolve(cached) and calls < max_resolve and stats["blocked"] is None:
            if broker is None:
                env = load_env(root)
                broker = (
                    BinanceRO(env, out, trader_local_ip())
                    if venue == "crypto"
                    else TradierRO(env)
                )
            try:
                calls += 1
                got = broker.resolve(
                    r["acct"],
                    r["ss"],
                    order_id=oid or None,
                    client=None if oid else client_id(venue, r.get("key")),
                )
                got["resolved_at"] = time.time()
                cache[ck] = got
                cached, changed = got, True
                stats["resolved_now"] += 1
                time.sleep(0.25)
            except Blocked as exc:
                stats["blocked"] = str(exc)
            except Exception as exc:
                stats.setdefault("errors", []).append(
                    f"{r.get('acct')} {r.get('ss')}: {str(exc)[:120]}"
                )
        if status != "SENT" and not (cached or {}).get("found"):
            continue
        fill = cached if (cached or {}).get("found") else None
        if fill is None or needs_resolve(cached):
            stats["pending"] += 1
        emit(
            json.dumps(
                {
                    "venue": venue,
                    "acct": r.get("acct"),
                    "ss": r.get("ss"),
                    "act": r.get("act"),
                    "why": r.get("why"),
                    "key": r.get("key"),
                    "ledger_ts": r.get("ts"),
                    "ledger_iso": r.get("iso"),
                    "ledger_status": status,
                    "sent_mark": r.get("price"),
                    "sent_qty": r.get("qty"),
                    "resp": r.get("resp"),
                    "fill": fill,
                    "final": bool(fill) and not needs_resolve(cached),
                },
                default=str,
            )
        )
    if changed:
        atomic_write_json(cache_p, cache)
    stats["broker_calls"] = calls
    emit(json.dumps({"_stats": stats, "venue": venue, "host_now": now}))
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--venue", choices=("crypto", "stocks"), required=True)
    ap.add_argument("--root", default="~/binance")
    ap.add_argument("--since", type=float, default=0.0, help="epoch; default now-2d")
    ap.add_argument("--max-resolve", type=int, default=12)
    a = ap.parse_args(argv)
    now = time.time()
    export(
        a.venue,
        Path(a.root).expanduser(),
        a.since or now - 2 * 86400,
        a.max_resolve,
        now=now,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
