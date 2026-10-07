#!/usr/bin/env python3
"""positions_dry_check — READ-ONLY broker vs position-file reconciliation.

Crypto (Binance USD-M): men, fin, inf, ang, flz — GET positionRisk + GET openOrders only.
Stocks (Tradier): tra, trb (live), trc (sandbox) — GET positions + GET orders only.
Never places, modifies or cancels orders. Never writes position files. Secrets are masked.
Output: console tables + data/safety/positions_dry_check_<YYYYMMDDHHMM>.json

Usage: python tools/positions_dry_check.py [--crypto-only|--stocks-only] [--accounts men,trc]
"""
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
CRYPTO_ACCOUNTS = ["men", "fin", "inf", "ang", "flz"]
STOCK_ACCOUNTS = ["tra", "trb", "trc"]
QTY_REL_TOL = 1e-6
ENTRY_REL_TOL = 0.01
OPEN_TRADIER_STATUSES = {"open", "pending", "partially_filled", "submitted", "pending_cancel"}
_LOG_DIR = BASE / "data" / "safety" / "positions_dry_check_logs"


def mask(value):
    if not value:
        return "None"
    return f"{str(value)[:4]}****"


def fnum(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def iso_age(mtime):
    age = time.time() - mtime
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(timespec="seconds"), round(age, 1)


def load_position_files(account):
    rows = {}
    files = {}
    for side in ("long", "short"):
        path = BASE / account / f"{side}_positions.json"
        info = {"path": str(path), "exists": path.exists()}
        if path.exists():
            mtime_iso, age = iso_age(path.stat().st_mtime)
            info.update({"mtime": mtime_iso, "age_sec": age})
            try:
                data = json.loads(path.read_text())
            except Exception as exc:
                info["error"] = f"{type(exc).__name__}: {exc}"
                data = {}
            stamps = []
            if isinstance(data, dict):
                for key in ("written_at", "_written_at", "updated_at", "_meta"):
                    if key in data:
                        info[key] = data[key] if not isinstance(data[key], dict) else data[key].get("written_at")
                for key, row in data.items():
                    if not isinstance(row, dict) or not key.endswith(("_LONG", "_SHORT")):
                        continue
                    rows[key] = row
                    stamp = row.get("last_updated") or row.get("last_update")
                    if stamp:
                        stamps.append(str(stamp))
            info["n_rows"] = sum(1 for k in rows if k.endswith(f"_{side.upper()}"))
            info["n_nonzero"] = sum(1 for k, r in rows.items() if k.endswith(f"_{side.upper()}") and abs(fnum(r.get("positionAmt"))) > 0)
            info["max_row_stamp"] = max(stamps) if stamps else None
        files[side] = info
    return rows, files


def file_age_for(files, side):
    return files.get(side.lower(), {}).get("age_sec")


def compare(account, broker_positions, file_rows, files):
    """broker_positions: dict key->{symbol, side, qty(abs), entry} ; file_rows: dict key->row"""
    discrepancies = []
    matched = 0
    for key, bpos in sorted(broker_positions.items()):
        row = file_rows.get(key)
        file_qty = abs(fnum(row.get("positionAmt"))) if row else 0.0
        file_entry = fnum(row.get("entry_price")) if row else 0.0
        bqty = bpos["qty"]
        base = {"key": key, "symbol": bpos["symbol"], "side": bpos["side"], "broker_qty": bqty, "file_qty": file_qty, "diff": round(bqty - file_qty, 10), "broker_entry": bpos.get("entry"), "file_entry": file_entry, "file_age_sec": file_age_for(files, bpos["side"]), "row_last_updated": (row or {}).get("last_updated") or (row or {}).get("last_update")}
        if row is None:
            discrepancies.append({**base, "type": "MISSING_ROW_IN_FILE"})
            continue
        if file_qty == 0:
            discrepancies.append({**base, "type": "BROKER_OPEN_FILE_FLAT"})
            continue
        if abs(bqty - file_qty) > max(QTY_REL_TOL * max(bqty, file_qty), 1e-9):
            discrepancies.append({**base, "type": "QTY_MISMATCH"})
            continue
        bentry = fnum(bpos.get("entry"))
        if bentry > 0 and file_entry > 0 and abs(bentry - file_entry) / bentry > ENTRY_REL_TOL:
            discrepancies.append({**base, "type": "ENTRY_MISMATCH", "entry_diff_pct": round((file_entry - bentry) / bentry * 100, 3)})
            continue
        matched += 1
    for key, row in sorted(file_rows.items()):
        qty = abs(fnum(row.get("positionAmt")))
        if qty <= 0 or key in broker_positions:
            continue
        side = "LONG" if key.endswith("_LONG") else "SHORT"
        discrepancies.append({"key": key, "symbol": row.get("symbol"), "side": side, "broker_qty": 0.0, "file_qty": qty, "diff": -qty, "broker_entry": None, "file_entry": fnum(row.get("entry_price")), "file_age_sec": file_age_for(files, side), "row_last_updated": row.get("last_updated") or row.get("last_update"), "type": "FILE_OPEN_BROKER_FLAT"})
    return discrepancies, matched


def check_crypto(account):
    result = {"account": account, "venue": "binance_usdm", "ok": False}
    file_rows, files = load_position_files(account)
    result["files"] = files
    api_key = os.environ.get(f"{account}_API_KEY")
    api_secret = os.environ.get(f"{account}_API_SECRET")
    result["api_key_masked"] = mask(api_key)
    if not api_key or not api_secret:
        result["error"] = f"missing {account}_API_KEY/_API_SECRET in env (.env.gpg decrypt failed?)"
        return result
    result["ip_binding"] = "none (darwin or EZ_DISABLE_IP_BINDING=1: ez_manage._apply_ip_binding is a no-op)" if (sys.platform == "darwin" or os.environ.get("EZ_DISABLE_IP_BINDING") == "1") else "WARNING: live ez_manage binds source IP on this host; this check uses default routing"
    try:
        from binance.client import Client
        client = Client(api_key, api_secret, ping=False)
        raw_positions = client.futures_position_information(recvWindow=10000)
        raw_orders = client.futures_get_open_orders(recvWindow=10000)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
        return result
    broker = {}
    for pos in raw_positions or []:
        amt = fnum(pos.get("positionAmt"))
        if amt == 0:
            continue
        pside = (pos.get("positionSide") or "BOTH").upper()
        side = pside if pside in ("LONG", "SHORT") else ("LONG" if amt > 0 else "SHORT")
        key = f"{account}:{pos['symbol']}_{side}"
        broker[key] = {"symbol": pos["symbol"], "side": side, "qty": abs(amt), "entry": fnum(pos.get("entryPrice")), "positionSide": pside, "mark": fnum(pos.get("markPrice")), "unrealized": fnum(pos.get("unRealizedProfit"))}
    discrepancies, matched = compare(account, broker, file_rows, files)
    orders = [{"symbol": o.get("symbol"), "side": o.get("side"), "positionSide": o.get("positionSide"), "type": o.get("type"), "origQty": o.get("origQty"), "executedQty": o.get("executedQty"), "price": o.get("price"), "stopPrice": o.get("stopPrice"), "reduceOnly": o.get("reduceOnly"), "status": o.get("status"), "time": datetime.fromtimestamp(fnum(o.get("time")) / 1000, tz=timezone.utc).isoformat(timespec="seconds") if o.get("time") else None} for o in raw_orders or []]
    result.update({"ok": True, "broker_nonzero": len(broker), "broker_positions": broker, "matched": matched, "discrepancies": discrepancies, "open_orders": orders})
    return result


async def check_stock(account):
    result = {"account": account, "venue": "tradier", "ok": False}
    file_rows, files = load_position_files(account)
    result["files"] = files
    try:
        from config_tradier import TradierConfig
        from tradier_api import TradierAPIClient
        cfg = TradierConfig()
        acfg = cfg.get_account_config(account) or {}
        client = TradierAPIClient(cfg, account_key=account)
    except Exception as exc:
        result["error"] = f"init {type(exc).__name__}: {str(exc)[:400]}"
        return result
    result["base_url"] = client._current_url
    result["is_sandbox"] = "sandbox" in str(client._current_url)
    result["config_env"] = (cfg.ACCOUNTS.get(account) or {}).get("env")
    result["config_is_sandbox"] = acfg.get("is_sandbox")
    result["account_id_masked"] = mask(client._current_id)
    result["api_key_masked"] = mask(client._current_key)
    if not client._current_id or not client._current_key:
        result["error"] = "missing TRADIER account id / key in env"
        return result
    try:
        await client.connect()
        positions = await client.get_account_positions(account)
        orders_raw = await client._request("GET", f"/accounts/{client._current_id}/orders", use_data_context=False)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
        return result
    finally:
        try:
            await client.close()
        except Exception:
            pass
    if positions is None:
        result["error"] = "get_account_positions returned None (API failure / auth / network) — see data/safety/positions_dry_check_logs/tradier_api.log"
        return result
    broker = {}
    for pos in positions:
        qty = fnum(pos.get("quantity"))
        if qty == 0:
            continue
        side = "LONG" if qty > 0 else "SHORT"
        key = f"{account}:{pos.get('symbol')}_{side}"
        cost = fnum(pos.get("cost_basis"))
        broker[key] = {"symbol": pos.get("symbol"), "side": side, "qty": abs(qty), "entry": abs(cost / qty) if qty else 0.0, "cost_basis": cost, "date_acquired": pos.get("date_acquired")}
    discrepancies, matched = compare(account, broker, file_rows, files)
    order_list = []
    if isinstance(orders_raw, dict) and isinstance(orders_raw.get("orders"), dict):
        inner = orders_raw["orders"].get("order")
        order_list = inner if isinstance(inner, list) else ([inner] if inner else [])
    today = datetime.now(timezone.utc).date().isoformat()
    open_orders = []
    today_orders = 0
    for o in order_list:
        created = str(o.get("create_date") or "")
        if created.startswith(today):
            today_orders += 1
        if str(o.get("status", "")).lower() in OPEN_TRADIER_STATUSES:
            open_orders.append({"id": o.get("id"), "symbol": o.get("symbol"), "side": o.get("side"), "type": o.get("type"), "quantity": o.get("quantity"), "exec_quantity": o.get("exec_quantity"), "price": o.get("price"), "stop_price": o.get("stop_price"), "status": o.get("status"), "duration": o.get("duration"), "create_date": o.get("create_date")})
    result.update({"ok": True, "orders_endpoint_ok": isinstance(orders_raw, dict) and "orders" in orders_raw, "broker_nonzero": len(broker), "broker_positions": broker, "matched": matched, "discrepancies": discrepancies, "open_orders": open_orders, "orders_total_returned": len(order_list), "orders_created_today_utc": today_orders})
    return result


def fmt_age(sec):
    if sec is None:
        return "-"
    if sec < 120:
        return f"{sec:.0f}s"
    if sec < 7200:
        return f"{sec / 60:.0f}m"
    return f"{sec / 3600:.1f}h"


def print_report(results):
    for res in results:
        head = f"=== {res['account']} ({res['venue']}{', SANDBOX' if res.get('is_sandbox') else ''}) key={res.get('api_key_masked')}"
        print("\n" + head)
        for side, info in res.get("files", {}).items():
            print(f"  file {side}: mtime={info.get('mtime')} age={fmt_age(info.get('age_sec'))} rows={info.get('n_rows')} nonzero={info.get('n_nonzero')} max_row_stamp={info.get('max_row_stamp')} written_at={info.get('written_at', '-')}")
        if not res.get("ok"):
            print(f"  ERROR: {res.get('error')}")
            continue
        print(f"  broker non-zero={res['broker_nonzero']} matched={res['matched']} discrepancies={len(res['discrepancies'])}")
        if res["discrepancies"]:
            print(f"  {'type':<22} {'key':<30} {'broker_qty':>14} {'file_qty':>14} {'diff':>14} {'b_entry':>12} {'f_entry':>12} {'file_age':>8}")
            for d in res["discrepancies"]:
                print(f"  {d['type']:<22} {d['key']:<30} {d['broker_qty']:>14.6g} {d['file_qty']:>14.6g} {d['diff']:>14.6g} {fnum(d.get('broker_entry')):>12.6g} {fnum(d.get('file_entry')):>12.6g} {fmt_age(d.get('file_age_sec')):>8}")
        oo = res.get("open_orders", [])
        print(f"  open orders: {len(oo)}" + (f" (tradier orders returned={res.get('orders_total_returned')}, created today UTC={res.get('orders_created_today_utc')})" if res["venue"] == "tradier" else ""))
        for o in oo:
            print(f"    {json.dumps(o, default=str)}")
    print("\n=== SUMMARY")
    for res in results:
        if res.get("ok"):
            types = {}
            for d in res["discrepancies"]:
                types[d["type"]] = types.get(d["type"], 0) + 1
            print(f"  {res['account']:<4} broker_open={res['broker_nonzero']:<4} matched={res['matched']:<4} discrepancies={len(res['discrepancies']):<4} open_orders={len(res.get('open_orders', [])):<4} {types}")
        else:
            print(f"  {res['account']:<4} ERROR {res.get('error')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--crypto-only", action="store_true")
    parser.add_argument("--stocks-only", action="store_true")
    parser.add_argument("--accounts", default="")
    parser.add_argument("--no-json", action="store_true")
    args = parser.parse_args()
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["TRADIER_API_LOG_DIR"] = str(_LOG_DIR)
    os.environ["EZ_LOG_DIR"] = str(_LOG_DIR)
    try:
        from env_gpg import load_env_gpg
        load_env_gpg()
    except Exception as exc:
        print(f"[positions_dry_check] env_gpg load failed: {type(exc).__name__}", file=sys.stderr)
    wanted = {a.strip() for a in args.accounts.split(",") if a.strip()}
    crypto = [] if args.stocks_only else [a for a in CRYPTO_ACCOUNTS if not wanted or a in wanted]
    stocks = [] if args.crypto_only else [a for a in STOCK_ACCOUNTS if not wanted or a in wanted]
    results = [check_crypto(a) for a in crypto]

    async def run_stocks():
        return [await check_stock(a) for a in stocks]

    if stocks:
        results.extend(asyncio.run(run_stocks()))
    print_report(results)
    if not args.no_json:
        out_dir = BASE / "data" / "safety"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        out = out_dir / f"positions_dry_check_{stamp}.json"
        payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "read_only": True, "endpoints": {"binance": ["GET /fapi/v3/positionRisk", "GET /fapi/v1/openOrders"], "tradier": ["GET /accounts/{id}/positions", "GET /accounts/{id}/orders"]}, "results": results}
        out.write_text(json.dumps(payload, indent=2, default=str))
        print(f"\nJSON: {out}")


if __name__ == "__main__":
    main()
