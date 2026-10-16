#!/usr/bin/env python3
"""tradier_position_stop_audit — READ-ONLY proof that, for every Tradier account:

  1. every broker-held position exists in {acct}/{long,short}_positions.json with positionAmt == |broker qty|
  2. every broker-held position has EXACTLY ONE resting protective stop, sized to the broker quantity
  3. no resting protective stop exists on a symbol the broker is flat in (an orphan reserves shares and makes the
     next backstop on that symbol get rejected)
  4. data/fixed_stop_registry_tradier.json agrees with the broker (no dead order ids, no unrecorded live stops)
  5. the derived files (ladder_levels_*, reenter_data_*, reentry_candidates, invalidation_levels) are no older than
     the position files they are derived from

USER 2026-10-16: "positions need to keep TOTAL AND COMPLETE SYNCHRONICITY with what is on the exchange ... Whenever a
positionAmt changes that needs to reflect in binance/trb/long_positions or short_positions immediately, and if a stop
loss order on the exchange or in tradier_manage is for a different amount it needs to be adjusted immediately."

Nothing here reimplements a level or places an order. It reads the exchange and the files and states the difference.

    python3 tools/tradier_position_stop_audit.py            # human table
    python3 tools/tradier_position_stop_audit.py --json     # machine readable
    python3 tools/tradier_position_stop_audit.py --accounts trb

Exit 0 = clean. Exit 1 = at least one finding (an unprotected position, a qty mismatch, a duplicate, an orphan).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tradier_api import TradierAPIClient, tradier_recent_orders  # noqa: E402
import order_dedupe_guard as _odg  # noqa: E402

LIVE_STATUSES = ("open", "pending", "partially_filled")
REGISTRY = ROOT / "data" / "fixed_stop_registry_tradier.json"
DERIVED = ("ladder_levels_long.json", "ladder_levels_short.json", "reenter_data_long.json", "reenter_data_short.json", "reentry_candidates.json", "invalidation_levels.json")
POSITION_FILE_MAX_AGE_S = float(os.getenv("TRADIER_AUDIT_POSITION_MAX_AGE_S", "120"))
DERIVED_LAG_MAX_S = float(os.getenv("TRADIER_AUDIT_DERIVED_LAG_MAX_S", "900"))


def _f(x, d=0.0) -> float:
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def load_registry() -> dict:
    try:
        return (json.loads(REGISTRY.read_text()) or {}).get("orders", {}) or {}
    except (OSError, ValueError):
        return {}


def load_files(acct: str) -> dict:
    out = {}
    for side in ("long", "short"):
        path = ROOT / acct / f"{side}_positions.json"
        try:
            book = json.loads(path.read_text()) or {}
        except (OSError, ValueError):
            continue
        for key, pos in book.items():
            if not isinstance(pos, dict):
                continue
            amt = abs(_f(pos.get("positionAmt")))
            if amt <= 0:
                continue
            sym = str(pos.get("symbol") or str(key).split(":")[-1].rsplit("_", 1)[0]).upper()
            pside = str(pos.get("position_side") or ("SHORT" if side == "short" else "LONG")).upper()
            out[(sym, pside)] = {"key": key, "amt": amt, "entry": _f(pos.get("entry_price")), "opened_at": pos.get("opened_at")}
    return out


def derived_ages(acct: str) -> dict:
    ages = {}
    for name in DERIVED:
        path = ROOT / acct / name
        ages[name] = round(time.time() - path.stat().st_mtime, 1) if path.exists() else None
    for side in ("long", "short"):
        path = ROOT / acct / f"{side}_positions.json"
        ages[f"{side}_positions.json"] = round(time.time() - path.stat().st_mtime, 1) if path.exists() else None
    return ages


async def audit_account(acct: str, registry: dict) -> dict:
    client = TradierAPIClient(account_key=acct)
    await client.connect()
    try:
        rows = await client.get_account_positions(acct)
        orders = await tradier_recent_orders(client._request, client._current_id, pages=20)
    finally:
        await client.close()

    res = {"account": acct, "findings": [], "held": 0, "ok": 0, "broker_read_ok": rows is not None and orders is not None}
    if rows is None or orders is None:
        res["findings"].append({"kind": "BROKER_UNREADABLE", "detail": f"positions={'ok' if rows is not None else 'FAILED'} orders={'ok' if orders is not None else 'FAILED'}"})
        return res

    broker = {}
    for row in rows:
        if isinstance(row, dict):
            sym = str(row.get("symbol", "")).upper()
            if sym:
                broker[sym] = broker.get(sym, 0.0) + _f(row.get("quantity"))
    broker = {s: q for s, q in broker.items() if q}

    resting = {}
    for o in orders:
        if not isinstance(o, dict):
            continue
        if str(o.get("status", "")).lower() not in LIVE_STATUSES:
            continue
        if not _odg.tradier_protective_stop(o.get("type"), o.get("side")):
            continue
        resting.setdefault(str(o.get("symbol", "")).upper(), []).append(o)

    files = load_files(acct)
    res["held"] = len(broker)
    res["rows"] = []

    for sym, bq in sorted(broker.items()):
        pside = "LONG" if bq > 0 else "SHORT"
        want_side = "sell" if bq > 0 else "buy_to_cover"
        stops = [o for o in resting.get(sym, []) if str(o.get("side", "")).lower() == want_side]
        key = f"{acct}:{sym}_{pside}"
        reg = registry.get(key) or {}
        reg_id = str(reg.get("orderId") or "")
        reg_live = bool(reg_id) and any(str(o.get("id")) == reg_id for o in stops)
        frow = files.get((sym, pside))
        row = {
            "symbol": sym, "side": pside, "broker_qty": bq,
            "file_qty": frow["amt"] if frow else None,
            "stops": [{"id": o.get("id"), "qty": _f(o.get("quantity")), "stop": _f(o.get("stop_price")), "status": o.get("status")} for o in stops],
            "registry": reg_id or None, "registry_live": reg_live,
        }
        clean = True
        if frow is None:
            res["findings"].append({"kind": "POSITION_MISSING_FROM_FILES", "symbol": sym, "side": pside, "detail": f"broker holds {bq} — no row with positionAmt>0 in {acct}/{pside.lower()}_positions.json"})
            clean = False
        elif abs(frow["amt"] - abs(bq)) > 1e-9:
            res["findings"].append({"kind": "POSITION_QTY_MISMATCH", "symbol": sym, "side": pside, "detail": f"file positionAmt {frow['amt']} != broker {abs(bq)}"})
            clean = False
        if not stops:
            res["findings"].append({"kind": "UNPROTECTED", "symbol": sym, "side": pside, "detail": f"broker holds {bq} with NO resting protective stop"})
            clean = False
        elif len(stops) > 1:
            res["findings"].append({"kind": "DUPLICATE_STOPS", "symbol": sym, "side": pside, "detail": f"{len(stops)} resting stops: {[o.get('id') for o in stops]} — they reserve the same shares and reject each other"})
            clean = False
        elif abs(_f(stops[0].get("quantity")) - abs(bq)) > 1e-9:
            res["findings"].append({"kind": "STOP_QTY_MISMATCH", "symbol": sym, "side": pside, "detail": f"stop {stops[0].get('id')} qty {_f(stops[0].get('quantity'))} != broker {abs(bq)}"})
            clean = False
        if reg_id and not reg_live:
            res["findings"].append({"kind": "REGISTRY_DEAD_ID", "symbol": sym, "side": pside, "detail": f"registry records {reg_id}, broker does not list it as resting"})
            clean = False
        if stops and not reg_id:
            res["findings"].append({"kind": "REGISTRY_MISSING", "symbol": sym, "side": pside, "detail": f"stop {stops[0].get('id')} rests at the broker but the registry has no entry — the ensure path will try to place a second one"})
            clean = False
        if clean:
            res["ok"] += 1
        res["rows"].append(row)

    for sym, lst in sorted(resting.items()):
        if sym not in broker:
            res["findings"].append({"kind": "ORPHAN_STOP", "symbol": sym, "side": "-", "detail": f"broker holds 0 but {[o.get('id') for o in lst]} still rest ({[_f(o.get('quantity')) for o in lst]} sh)"})

    for (sym, pside), frow in sorted(files.items()):
        bq = broker.get(sym)
        if bq is None or (pside == "LONG") != (bq > 0):
            res["findings"].append({"kind": "FILE_POSITION_NOT_AT_BROKER", "symbol": sym, "side": pside, "detail": f"{acct}/{pside.lower()}_positions.json claims {frow['amt']} but the broker holds {bq if bq is not None else 0}"})

    res["derived_ages_s"] = ages = derived_ages(acct)
    # USER 2026-10-16: "any change has to lead to adjustments of ladder_levels, reenter_data, reentry_candidates,
    # invalidation_levels". tradier_positions.process_account_update rewrites those on every augment/reduce, so a
    # derived file far older than the position file it is derived from means the writer stopped (on 2026-10-16 it was
    # dead 14:52->19:51Z) or that chain broke. Only meaningful while the market is open and we hold something.
    pos_age = min([a for a in (ages.get("long_positions.json"), ages.get("short_positions.json")) if a is not None] or [None])
    if broker and pos_age is not None:
        if pos_age > POSITION_FILE_MAX_AGE_S:
            res["findings"].append({"kind": "POSITION_FILE_STALE", "detail": f"newest of {acct}/(long|short)_positions.json is {pos_age:.0f}s old (> {POSITION_FILE_MAX_AGE_S}s) while the broker holds {len(broker)} positions — the position writer is not running"})
        for name in DERIVED:
            age = ages.get(name)
            if age is not None and age - pos_age > DERIVED_LAG_MAX_S:
                res["findings"].append({"kind": "DERIVED_FILE_LAGGING", "detail": f"{acct}/{name} is {age - pos_age:.0f}s older than the position files it is derived from"})
    return res


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", nargs="+", default=["tra", "trb", "trc"])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    registry = load_registry()
    results = []
    for acct in args.accounts:
        try:
            results.append(await audit_account(acct, registry))
        except Exception as exc:
            results.append({"account": acct, "findings": [{"kind": "AUDIT_FAILED", "detail": repr(exc)}], "held": 0, "ok": 0, "broker_read_ok": False})

    total = sum(len(r["findings"]) for r in results)
    if args.json:
        print(json.dumps({"ts": time.time(), "findings_total": total, "accounts": results}, indent=2, default=str))
        return 1 if total else 0

    for r in results:
        print(f"\n===== {r['account']}  held={r['held']} clean={r['ok']} findings={len(r['findings'])} =====")
        for row in r.get("rows", []):
            stops = ",".join(f"{s['id']}:{s['qty']:g}@{s['stop']:g}" for s in row["stops"]) or "NONE"
            print(f"  {row['symbol']:<7} {row['side']:<5} broker={row['broker_qty']:>9g} file={row['file_qty']} reg={'LIVE' if row['registry_live'] else (row['registry'] or '-')} stops=[{stops}]")
        for f in r["findings"]:
            print(f"  !! {f['kind']:<28} {f.get('symbol','-'):<7} {f['detail']}")
        ages = r.get("derived_ages_s") or {}
        if ages:
            print("  file ages (s): " + " ".join(f"{k.replace('.json','')}={v}" for k, v in ages.items()))
    print(f"\nTOTAL FINDINGS: {total}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
