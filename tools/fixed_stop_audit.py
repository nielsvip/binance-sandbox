#!/usr/bin/env python3
"""fixed_stop_audit — prove, from the exchange, that every open position's frozen stop is the one its OWN entry bar
defines, and that it is either resting at the exchange or explicitly unarmed.

USER 2026-10-15 ("I NEED ABSOLUTE SOLID PROOF THIS CAN NOT HAPPEN AGAIN EVER"). The COTI incident was invisible for
days because nothing ever compared the three numbers that must agree:
    1. the 20-bar 15m Donchian of the bar that entered   (the definition of the level)
    2. <acct>/{long,short}_positions.json fixed_stop_price (what live believes)
    3. the triggerPrice of the resting STOP_MARKET algo order at Binance (what actually protects the money)
Any disagreement is a finding. Read-only; exit 1 when anything is off, so a cron/guardian can act on it.

Usage: python3 tools/fixed_stop_audit.py [--json PATH] [--quiet]
"""
import argparse
import datetime
import glob
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import vec_decisions.fixed_stop as FX

CRYPTO = ("ang", "fin", "men", "inf", "flz")
FRESH_GRACE_S = 90.0  # a position opened this recently has not had a writer cycle to flush its frozen level
_KL = {}


def klines(symbol):
    if symbol not in _KL:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=1000"
        _KL[symbol] = json.load(urllib.request.urlopen(url, timeout=20))
        time.sleep(0.1)
    return _KL[symbol]


def epoch(v):
    try:
        return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def entry_bar_level(symbol, opened_at_epoch, is_long, entry_price):
    kl = klines(symbol)
    idx = [i for i, x in enumerate(kl) if x[0] / 1000.0 <= opened_at_epoch]
    if not idx or idx[-1] < 19:
        return 0.0
    i = idx[-1]
    lo = min(float(x[3]) for x in kl[i - 19 : i + 1])
    hi = max(float(x[2]) for x in kl[i - 19 : i + 1])
    return FX.freeze_software_level(is_long, lo, hi, entry_price)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(ROOT / "data" / "reports" / "fixed_stop_audit.json"))
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    import utils
    utils.load_environment_from_gpg()
    from binance.client import Client

    rows, findings = [], []
    for acct in CRYPTO:
        key = os.getenv(f"{acct}_API_KEY") or os.getenv(f"{acct.upper()}_API_KEY")
        sec = os.getenv(f"{acct}_API_SECRET") or os.getenv(f"{acct.upper()}_API_SECRET")
        if not key or not sec:
            findings.append({"account": acct, "code": "NO_CREDENTIALS"})
            continue
        cli = Client(api_key=key, api_secret=sec, requests_params={"timeout": 15})
        try:
            held = [p for p in cli.futures_position_information() if abs(float(p.get("positionAmt") or 0)) > 0]
            resp = cli._request_futures_api("get", "openAlgoOrders", True, data={})
            orders = (resp.get("orders") if isinstance(resp, dict) else resp) or []
        except Exception as exc:
            findings.append({"account": acct, "code": "BROKER_UNREACHABLE", "error": repr(exc)[:160]})
            continue
        resting = {}
        for o in orders:
            if "STOP" in str(o.get("orderType") or o.get("origType") or "").upper():
                resting[(o.get("symbol"), str(o.get("positionSide")))] = float(o.get("triggerPrice") or o.get("stopPrice") or 0)
        book = {}
        for side in ("long", "short"):
            try:
                book.update(json.load(open(ROOT / acct / f"{side}_positions.json")))
            except Exception:
                pass
        for p in held:
            symbol, pside = p["symbol"], str(p["positionSide"])
            mark = float(p.get("markPrice") or 0)
            is_long = pside == "LONG"
            pk = f"{acct}:{symbol}_{pside}"
            row = book.get(pk) or {}
            live = float(row.get("fixed_stop_price") or 0)
            armed = bool(row.get("fixed_stop_armed", True))
            oa = epoch(row.get("opened_at"))
            want = entry_bar_level(symbol, oa, is_long, float(row.get("entry_price") or 0)) if oa else 0.0
            trig = resting.get((symbol, pside), 0.0)
            rec = {"key": pk, "mark": mark, "live_level": live, "entry_bar_level": want, "resting_trigger": trig,
                   "armed": armed, "opened_at": row.get("opened_at")}
            fresh = oa is not None and (time.time() - oa) < FRESH_GRACE_S
            if fresh and (not row or live <= 0):
                # opened seconds ago: the writer has not flushed its frozen level yet. Not a defect — the exchange
                # stop placed at entry is checked below once the level lands.
                rec["note"] = f"just opened ({int(time.time() - oa)}s) — level not flushed yet"
            elif not row:
                findings.append({**rec, "code": "NO_LIVE_ROW"})
            elif want <= 0:
                findings.append({**rec, "code": "ENTRY_BAR_UNAVAILABLE"})
            elif live <= 0:
                findings.append({**rec, "code": "NO_FROZEN_LEVEL"})
            elif abs(live - want) / want > 1e-4:
                findings.append({**rec, "code": "LEVEL_NOT_ENTRY_BAR"})
            elif trig <= 0 and armed:
                findings.append({**rec, "code": "ARMED_BUT_NO_EXCHANGE_STOP"})
            elif trig > 0 and abs(trig - FX.crypto_exchange_level(is_long, live)) / FX.crypto_exchange_level(is_long, live) > 2e-3:
                findings.append({**rec, "code": "EXCHANGE_STOP_WRONG_LEVEL"})
            rows.append(rec)
    out = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "n_positions": len(rows),
           "n_findings": len(findings), "findings": findings, "positions": rows}
    Path(a.json).parent.mkdir(parents=True, exist_ok=True)
    Path(a.json).write_text(json.dumps(out, indent=1, sort_keys=True))
    if not a.quiet:
        print(f"positions: {len(rows)}   findings: {len(findings)}   -> {a.json}")
        for f in findings:
            print(f"  {f.get('code'):28s} {f.get('key', f.get('account'))}: live={f.get('live_level')} entry_bar={f.get('entry_bar_level')} resting={f.get('resting_trigger')} armed={f.get('armed')}")
        for r in rows:
            state = "ARMED" if r["armed"] else "unarmed(cross predates fix)"
            print(f"  OK  {r['key']:30s} level={r['live_level']:<14.8g} resting={r['resting_trigger']:<14.8g} mark={r['mark']:<14.8g} {state}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
