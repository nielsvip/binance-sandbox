#!/usr/bin/env python3
"""LIVE FAILOVER S1 state check (2026-10-14 USER: "s1 ... starts trading whenever macbook doesnt" + S1 held takeover-time
opened_at on ~34 stock rows and phantom March crypto rows after today's takeovers). READ-ONLY: never writes a position
file, never places or cancels an order. Broker calls: one signed positions read per account (Binance /fapi/v2/positionRisk
weight 5 + /fapi/v1/time weight 1; Tradier GET /v1/accounts/{id}/positions).

Modes (run on S1, live dir LF_LIVE_DIR=/home/niels/binance):
  --probe --venue crypto|stocks [--json]  S1's OWN broker access (it may share the Mac's IP ban). Exit 0 when >= 60 % of
                                          the venue's accounts with credentials answered (each tried via S1's bindable
                                          REST source IPs like ez_positions_service). Used by live_failover_s1.py
                                          before every not-trading takeover.
  --gate --account A                      takeover gate used by tools/s1_failover_start.sh before a trader starts: S1's
                                          own writer wrote A's files since the lease (meta written_at >= lease since,
                                          not a manage writer) AND held rows == broker held rows (no phantom, no missing,
                                          equal qty) AND every held row has opened_at. Last line READY / NOT_READY.
                                          opened_at != the Mac's staged row -> WARN + ALERTS.jsonl, not blocking
                                          (opened_at is immutable once written; blocking would only stop S1 trading).
  --report [--json-out P] [--venue V]     DRY RUN of a takeover now: for every account broker vs S1 files vs the Mac's
                                          staged files (data/live_failover/mac_state/<acct>/, pushed by the Mac agent
                                          every 30 s) + simulation of what S1's single writer would hold: the identity
                                          seed from the Mac rows (live_failover_common.write_identity_seed) through the
                                          REAL position_writer_guard.seeded_disk_rows (earliest same-entry wins) +
                                          enforce_identity, new broker rows stamped with a TAKEOVER sentinel.
Fixture: LF_STATE_FAKE_BROKER=<json file {acct: {key: {positionAmt, entry_price}}}> replaces the broker reads.
"""
import argparse
import hashlib
import hmac
import json
import math
import os
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_failover_common as C  # noqa: E402

LIVE = Path(os.environ.get("LF_LIVE_DIR", "/home/niels/binance"))
LF = LIVE / "data" / "live_failover"
REST_IPS = ("5.75.211.216", "49.13.39.233", "157.180.125.52")  # == ez_positions_service._init_ip_cycle
FAPI = "https://fapi.binance.com"
TRADIER_ENV = {"tra": (("TRADIER_ACCOUNT_ID_TRA", "TRADIER_ACCOUNT_ID"), ("TRADIER_API_KEY_TRA", "TRADIER_ACCESS_TOKEN", "TRADIER_API_KEY"), "live"),
               "trb": (("TRADIER_ACCOUNT_ID_TRB",), ("TRADIER_API_KEY_TRB", "TRADIER_API_KEY_TRA", "TRADIER_API_KEY"), "live"),
               "trc": (("TRADIER_ACCOUNT_ID_TRC", "TRADIER_SANDBOX_ID"), ("TRADIER_API_KEY_TRC", "TRADIER_SANDBOX_ACCESS_TOKEN", "TRADIER_SANDBOX_KEY"), "paper")}  # == config_tradier.ACCOUNTS
QTY_REL = 1e-6


def load_env():
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None
    for path in (LIVE / ".env", Path.home() / ".env"):
        if path.exists():
            if load_dotenv:
                load_dotenv(path, override=False)
            else:
                for line in path.read_text().splitlines():
                    if "=" in line and not line.lstrip().startswith("#"):
                        key, _, value = line.partition("=")
                        os.environ.setdefault(key.strip().removeprefix("export ").strip(), value.strip().strip('"').strip("'"))


def amt(row):
    try:
        return abs(float((row or {}).get("positionAmt") or 0))
    except (TypeError, ValueError):
        return 0.0


def held(rows):
    return {k: r for k, r in (rows or {}).items() if isinstance(r, dict) and amt(r) > 0}


def qty_equal(a, b):
    return abs(a - b) <= max(1e-9, QTY_REL * max(a, b))


def _session(ip=None):
    import requests
    from requests.adapters import HTTPAdapter
    session = requests.Session()
    if ip:
        class Bound(HTTPAdapter):
            def init_poolmanager(self, connections, maxsize, block=False, **kw):
                kw["source_address"] = (ip, 0)
                super().init_poolmanager(connections, maxsize, block=block, **kw)
        session.mount("https://", Bound(max_retries=0))
    return session


def bindable_ips():
    if sys.platform == "darwin" or os.environ.get("EZ_DISABLE_IP_BINDING") == "1":
        return [None]
    ips = []
    for ip in REST_IPS:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind((ip, 0))
            probe.close()
            ips.append(ip)
        except OSError:
            continue
    return ips + [None]


def binance_positions(acct):
    key, secret = os.environ.get(f"{acct}_API_KEY"), os.environ.get(f"{acct}_API_SECRET")
    if not key or not secret:
        return None, {"ok": False, "error": "no credentials", "tries": []}
    tries = []
    for ip in bindable_ips():
        try:
            session = _session(ip)
            server = session.get(f"{FAPI}/fapi/v1/time", timeout=8).json().get("serverTime")
            params = {"timestamp": int(server or time.time() * 1000), "recvWindow": 10000}
            query = urlencode(params)
            sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            resp = session.get(f"{FAPI}/fapi/v2/positionRisk?{query}&signature={sig}", headers={"X-MBX-APIKEY": key}, timeout=10)
            if resp.status_code != 200:
                tries.append({"ip": ip, "status": resp.status_code, "error": resp.text[:160]})
                continue
            rows = {}
            for item in resp.json():
                qty = float(item.get("positionAmt") or 0)
                if qty == 0:
                    continue
                side = item.get("positionSide") or "BOTH"
                if side == "BOTH":
                    side = "LONG" if qty > 0 else "SHORT"
                rows[f"{acct}:{item['symbol']}_{side}"] = {"positionAmt": abs(qty), "entry_price": float(item.get("entryPrice") or 0)}
            tries.append({"ip": ip, "status": 200})
            return rows, {"ok": True, "ip": ip, "tries": tries}
        except Exception as exc:
            tries.append({"ip": ip, "error": repr(exc)[:160]})
    return None, {"ok": False, "error": (tries[-1] if tries else {}).get("error") or "all source IPs failed", "tries": tries}


def tradier_positions(acct):
    ids, keys, env = TRADIER_ENV[acct]
    account_id = next((os.environ[n] for n in ids if os.environ.get(n)), None)
    token = next((os.environ[n] for n in keys if os.environ.get(n)), None)
    if not account_id or not token:
        return None, {"ok": False, "error": "no credentials"}
    base = "https://api.tradier.com" if env == "live" else "https://sandbox.tradier.com"
    try:
        resp = _session().get(f"{base}/v1/accounts/{account_id}/positions", headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=15)
        if resp.status_code != 200:
            return None, {"ok": False, "status": resp.status_code, "error": resp.text[:160]}
        body = (resp.json() or {}).get("positions")
        items = body.get("position", []) if isinstance(body, dict) else []
        items = [items] if isinstance(items, dict) else items
        rows = {}
        for item in items:
            symbol, qty = str(item.get("symbol", "")), float(item.get("quantity") or 0)
            if not qty or any(ch.isdigit() for ch in symbol):  # options (OCC symbols) are not managed position rows
                continue
            side = "LONG" if qty > 0 else "SHORT"
            rows[f"{acct}:{symbol}_{side}"] = {"positionAmt": abs(qty), "entry_price": abs(float(item.get("cost_basis") or 0)) / abs(qty), "date_acquired": item.get("date_acquired")}
        return rows, {"ok": True, "env": env}
    except Exception as exc:
        return None, {"ok": False, "error": repr(exc)[:160]}


def broker_positions(acct):
    fake = os.environ.get("LF_STATE_FAKE_BROKER")
    if fake:
        data = C.read_json(fake, {}) or {}
        if acct not in data:
            return None, {"ok": False, "error": "fixture: no broker answer"}
        return data[acct], {"ok": True, "fixture": True}
    return binance_positions(acct) if acct in C.CRYPTO_ACCOUNTS else tradier_positions(acct)


def accounts_for(venue):
    return list(C.CRYPTO_ACCOUNTS if venue == "crypto" else C.STOCK_ACCOUNTS)


def probe(venue):
    accounts, ok = {}, 0
    for acct in accounts_for(venue):
        rows, info = broker_positions(acct)
        if info.get("error") == "no credentials":
            accounts[acct] = {"ok": None, "error": "no credentials on S1"}
            continue
        accounts[acct] = {"ok": bool(info.get("ok")), "n_held": None if rows is None else len(rows), "ip": info.get("ip"), "error": info.get("error"), "tries": info.get("tries")}
        ok += 1 if info.get("ok") else 0
    tried = sum(1 for a in accounts.values() if a["ok"] is not None)
    passed = bool(tried) and ok >= max(1, math.ceil(0.6 * tried - 1e-9))
    return {"ok": passed, "accounts": accounts, "summary": f"{venue}: {ok}/{tried} accounts reached the broker from S1"}


def side_files(base, acct):
    return {side: Path(base) / acct / f"{side}_positions.json" for side in ("long", "short")}


def load_rows(base, acct):
    rows, mtimes = {}, {}
    for side, path in side_files(base, acct).items():
        rows.update(C.read_json(path, {}) or {})
        mtimes[side] = path.stat().st_mtime if path.exists() else None
    return rows, mtimes


def simulate_takeover(acct, broker, now):
    """What S1's single writer would hold right after a takeover NOW, using the real guard functions."""
    sys.path.insert(0, str(LIVE))
    import position_writer_guard as pwg
    sentinel = C.iso(now) + "#TAKEOVER"
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        tdir = Path(tmp) / acct
        tdir.mkdir()
        for side, src in side_files(LIVE, acct).items():
            if src.exists():
                shutil.copy2(src, tdir / src.name)
        mac_files = [p for p in side_files(LF / "mac_state", acct).values()]
        if any(p.exists() for p in mac_files):
            C.write_identity_seed(tdir, mac_files, "dry-run")
            seed = C.read_json(tdir / ".identity_seed.json", {})
            seed["written_epoch"] = max(time.time(), max([(tdir / f).stat().st_mtime for f in ("long_positions.json", "short_positions.json") if (tdir / f).exists()] or [0]) + 1)
            C.atomic_write_json(tdir / ".identity_seed.json", seed)
        for side in ("long", "short"):
            disk = pwg.seeded_disk_rows(tdir / f"{side}_positions.json")
            new = {k: {"positionAmt": r["positionAmt"], "entry_price": r.get("entry_price"), "opened_at": sentinel} for k, r in broker.items() if k.upper().endswith("_" + side.upper())}
            pwg.enforce_identity(new, disk)
            out.update(new)
    return out, sentinel


def account_report(acct, now):
    broker, info = broker_positions(acct)
    s1_rows, s1_mtimes = load_rows(LIVE, acct)
    mac_rows, mac_mtimes = load_rows(LF / "mac_state", acct)
    rep = {"broker_ok": bool(info.get("ok")), "broker_error": info.get("error"), "broker_ip": info.get("ip"),
           "mac_staged_age_s": {k: (None if v is None else round(now - v)) for k, v in mac_mtimes.items()},
           "s1_file_age_s": {k: (None if v is None else round(now - v)) for k, v in s1_mtimes.items()}}
    if broker is None:
        return rep
    s1_h, mac_h = held(s1_rows), held(mac_rows)
    rep.update(broker_held=len(broker), s1_held=len(s1_h), mac_held=len(mac_h),
               s1_phantoms=sorted(set(s1_h) - set(broker)), s1_missing=sorted(set(broker) - set(s1_h)),
               s1_qty_mismatch=sorted(k for k in set(s1_h) & set(broker) if not qty_equal(amt(s1_h[k]), broker[k]["positionAmt"])),
               mac_phantoms=sorted(set(mac_h) - set(broker)), mac_missing=sorted(set(broker) - set(mac_h)))
    rep["s1_opened_at_vs_mac"] = sorted([k, s1_h[k].get("opened_at"), mac_h[k].get("opened_at")] for k in set(s1_h) & set(mac_h) & set(broker)
                                        if not _same(s1_h[k].get("opened_at"), mac_h[k].get("opened_at")))
    sim, sentinel = simulate_takeover(acct, broker, now)
    carried, earlier, takeover, unseeded, other = [], [], [], [], []
    for k, row in sorted(sim.items()):
        final, mac_oa = row.get("opened_at"), (mac_h.get(k) or {}).get("opened_at")
        if final == sentinel:
            (takeover if mac_oa else unseeded).append(k)
        elif mac_oa and _same(final, mac_oa):
            carried.append(k)
        elif mac_oa and C.parse_ts(str(final)) is not None and C.parse_ts(str(mac_oa)) is not None and C.parse_ts(str(final)) < C.parse_ts(str(mac_oa)):
            earlier.append([k, final, mac_oa])
        else:
            other.append([k, final, mac_oa])
    rep["simulated_after_takeover"] = {"held": len(sim), "opened_at_carried_from_mac": len(carried), "opened_at_earlier_than_mac_same_entry": earlier,
                                       "opened_at_would_be_takeover_time": takeover, "broker_held_not_on_mac_new_lifecycle": unseeded, "opened_at_other": other,
                                       "phantoms_held": 0, "note": "phantoms: S1 writer zeroes broker-absent rows (API_CONFIRMED_CLOSED); s1_failover_start --gate refuses traders until it has"}
    return rep


def _same(a, b):
    ea, eb = C.parse_ts(str(a)) if a else None, C.parse_ts(str(b)) if b else None
    return ea is not None and eb is not None and abs(ea - eb) <= 1.0


def gate(acct, now):
    lease = C.read_json(LF / "lease.json", {}) or {}
    since = float(lease.get("since_epoch") or 0) if lease.get("holder") == "s1" else 0.0
    problems, warns = [], []
    for side in ("long", "short"):
        meta = C.read_json(LIVE / acct / f".{side}_positions.json.meta.json", {}) or {}
        written = float(meta.get("written_at") or 0)
        if not written or written < since:
            problems.append(f"{side} file not written by S1's writer since the lease ({C.iso(written) if written else 'never'} < {C.iso(since)})")
        if ":manage:" in str(meta.get("writer", "")):
            problems.append(f"{side} last writer is a manage process")
    broker, info = broker_positions(acct)
    if broker is None:
        problems.append(f"broker read failed: {info.get('error')}")
    else:
        s1_h = held(load_rows(LIVE, acct)[0])
        mac_h = held(load_rows(LF / "mac_state", acct)[0])
        phantoms, missing = sorted(set(s1_h) - set(broker)), sorted(set(broker) - set(s1_h))
        qty = sorted(k for k in set(s1_h) & set(broker) if not qty_equal(amt(s1_h[k]), broker[k]["positionAmt"]))
        no_oa = sorted(k for k, r in s1_h.items() if C.parse_ts(str(r.get("opened_at") or "")) is None)
        for label, keys in (("phantom", phantoms), ("missing", missing), ("qty_mismatch", qty), ("no_opened_at", no_oa)):
            if keys:
                problems.append(f"{label}={keys[:8]}{'...' if len(keys) > 8 else ''}")
        diff = sorted(k for k in set(s1_h) & set(mac_h) if not _same(s1_h[k].get("opened_at"), mac_h[k].get("opened_at")))
        if diff:
            warns.append(f"opened_at differs from Mac on {diff[:8]}")
            row = {"ts": C.iso(now), "epoch": now, "host": socket.gethostname(), "kind": "S1_OPENED_AT_DIFFERS_FROM_MAC", "account": acct, "keys": diff}
            try:
                with open(LF / "ALERTS.jsonl", "a") as handle:
                    handle.write(json.dumps(row) + "\n")
            except OSError:
                pass
    verdict = "READY" if not problems else "NOT_READY"
    print(f"{verdict} {acct} broker_held={None if broker is None else len(broker)} problems={problems} warns={warns}")
    return 0 if not problems else 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--venue", choices=["crypto", "stocks", "all"], default="all")
    parser.add_argument("--account")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--json-out")
    args = parser.parse_args()
    load_env()
    now = time.time()
    venues = list(C.VENUES) if args.venue == "all" else [args.venue]
    if args.gate:
        return gate(args.account, now)
    if args.probe:
        report = {"at": C.iso(now), "venues": {v: probe(v) for v in venues}}
        print(json.dumps(report, indent=1, default=str) if args.json else "\n".join(r["summary"] for r in report["venues"].values()))
        return 0 if all(r["ok"] for r in report["venues"].values()) else 2
    report = {"at": C.iso(now), "host": socket.gethostname(), "live": str(LIVE), "lease": C.read_json(LF / "lease.json"),
              "accounts": {a: account_report(a, now) for v in venues for a in accounts_for(v)}}
    text = json.dumps(report, indent=1, default=str)
    if args.json_out:
        Path(args.json_out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
