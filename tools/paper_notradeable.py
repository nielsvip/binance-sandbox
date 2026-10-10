#!/usr/bin/env python3
"""paper_notradeable — forward-paper the vec decisions live REFUSES as NOT_TRADEABLE.

Watches live [VEC_EXACT] decision lines + refusal lines, papers refused keys at live marks,
tracks realized PnL net of taker fees. Answers: should the tradeable gate be overridden?
Verdict rule (operator): override only keys with >=10 paper trips AND net>0 AND beating fees 2x.

State: data/paper_notradeable/{state.json,fills.jsonl,status.json}. Log: stdout (-> plist log).
 ZERO live impact: read-only on logs/Redis/trackers, writes only its own dir.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data" / "paper_notradeable"
DATA.mkdir(parents=True, exist_ok=True)
STATE_P = DATA / "state.json"
FILLS_P = DATA / "fills.jsonl"
STATUS_P = DATA / "status.json"

ACCTS = ("ang", "fin", "men", "inf", "flz")
TAKER = 0.0005
PAPER_NOTIONAL_USD = 25.0
LOOP_S = 20.0

ACTS_RE = re.compile(r"\[VEC_EXACT\] (\S+) bar=(\d+) k=\d+ acts=\[(.*)\]")
ACT_RE = re.compile(r"\('([A-Z]+)', '([^']*)'\)")
REFUSE_RE = re.compile(r"(BLOCKED NOT TRADEABLE|NON_TRADEABLE_HARD_BLOCK)\s+(\w+):(\S+?)[\s(]")


def log(msg):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} [PAPER_NT] {msg}", flush=True)


def load_state():
    try:
        return json.loads(STATE_P.read_text())
    except Exception:
        return {"offsets": {}, "pos": {}, "stats": {}}


def save_state(st):
    tmp = STATE_P.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, default=str))
    os.replace(tmp, STATE_P)


def tradeable_set():
    out = set()
    try:
        raw = (ROOT / "tradeable_keys.json").read_text().strip()
        obj, _ = json.JSONDecoder().raw_decode(raw)
        out.update(str(k).strip() for k in obj)
    except Exception as e:
        log(f"tradeable file unreadable: {e}")
    try:
        import redis as _r

        cli = _r.Redis(host="localhost", port=6379, decode_responses=True, socket_timeout=2)
        raw = cli.get("tradeable_keys")
        if raw:
            out.update(str(k).strip() for k in json.loads(raw))
    except Exception:
        pass
    return out


def mark_prices(syms):
    px = {}
    try:
        import redis as _r

        cli = _r.Redis(host="localhost", port=6379, decode_responses=True, socket_timeout=2)
        raw = cli.get("latest_market_data")
        if raw:
            md = json.loads(raw)
            inds = md.get("indicators", md) if isinstance(md, dict) else {}
            for s in syms:
                d = inds.get(s) or {}
                for k in ("mark_price", "markPrice", "close", "price", "last_price"):
                    v = d.get(k)
                    if v:
                        px[s] = float(v)
                        break
    except Exception as e:
        log(f"redis marks unavailable: {e}")
    missing = [s for s in syms if s not in px]
    for s in missing:
        try:
            with urllib.request.urlopen(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={s}", timeout=5) as r:
                px[s] = float(json.loads(r.read().decode())["markPrice"])
        except Exception:
            pass
    return px


def tail_new(path, off):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if off is None or off > size:
                off = max(0, size - 200000)
            f.seek(off)
            data = f.read().decode("utf-8", errors="replace")
            return data.splitlines(), f.tell()
    except FileNotFoundError:
        return [], off or 0


def main():
    st = load_state()
    log("started (refused-key forward paper)")
    while True:
        try:
            tset = tradeable_set()
            refused = set()
            for a in ACCTS:
                try:
                    t = json.loads((ROOT / a / "tracker.json").read_text())
                    for k in t.get("tradeable_position_keys", []):
                        if k not in tset:
                            refused.add(k)
                except Exception:
                    pass
            evts = []
            for a in ACCTS:
                p = Path.home() / "logs" / f"ez_manage_{a}.log"
                lines, off = tail_new(p, st["offsets"].get(a))
                st["offsets"][a] = off
                for ln in lines:
                    m = ACTS_RE.search(ln)
                    if m:
                        ss, bar = m.group(1), int(m.group(2))
                        for typ, rsn in ACT_RE.findall(m.group(3)):
                            evts.append((f"{a}:{ss}", typ, rsn[:60], bar))
                    m2 = REFUSE_RE.search(ln)
                    if m2:
                        refused.add(f"{m2.group(2)}:{m2.group(3)}")
            syms = {k.split(":", 1)[1].rsplit("_", 1)[0] for k in refused}
            px = mark_prices(syms)
            nf = 0
            for key, typ, rsn, bar in evts:
                if key not in refused:
                    continue
                sym = key.split(":", 1)[1].rsplit("_", 1)[0]
                side = key.rsplit("_", 1)[-1]
                price = px.get(sym, 0.0)
                if price <= 0:
                    continue
                pos = st["pos"].get(key)
                stat = st["stats"].setdefault(key, {"trips": 0, "gross": 0.0, "fees": 0.0, "net": 0.0})
                if typ == "OPEN" and pos is None:
                    qty = PAPER_NOTIONAL_USD / price
                    st["pos"][key] = {"qty": qty, "entry": price, "side": side, "bar": bar, "rsn": rsn}
                    stat["fees"] += PAPER_NOTIONAL_USD * TAKER
                    stat["net"] -= PAPER_NOTIONAL_USD * TAKER
                    nf += 1
                elif typ in ("CLOSE", "REDUCE") and pos is not None:
                    frac = 1.0 if typ == "CLOSE" else 0.5
                    q = pos["qty"] * frac
                    pnl = (price - pos["entry"]) * q * (1 if pos["side"] == "LONG" else -1)
                    fee = q * price * TAKER
                    stat["gross"] += pnl
                    stat["fees"] += fee
                    stat["net"] += pnl - fee
                    if typ == "CLOSE":
                        stat["trips"] += 1
                        del st["pos"][key]
                    else:
                        pos["qty"] -= q
                    with open(FILLS_P, "a") as f:
                        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "key": key, "typ": typ, "qty": round(q, 6), "px": price, "pnl": round(pnl - fee, 4), "rsn": rsn}) + "\n")
                    nf += 1
            open_unreal = 0.0
            for key, pos in st["pos"].items():
                sym = key.split(":", 1)[1].rsplit("_", 1)[0]
                price = px.get(sym, 0.0)
                if price > 0:
                    open_unreal += (price - pos["entry"]) * pos["qty"] * (1 if pos["side"] == "LONG" else -1)
            tot_trips = sum(s["trips"] for s in st["stats"].values())
            tot_net = sum(s["net"] for s in st["stats"].values())
            tot_fees = sum(s["fees"] for s in st["stats"].values())
            STATUS_P.write_text(json.dumps({
                "updated": datetime.now(timezone.utc).isoformat(),
                "refused_keys": len(refused), "open_paper": len(st["pos"]),
                "trips": tot_trips, "net": round(tot_net, 2), "fees": round(tot_fees, 2),
                "open_unreal": round(open_unreal, 2),
                "verdict": "OVERRIDE_ONLY_IF trips>=10 and net>0 and net>2*fees (now: %d trips, net $%.2f, fees $%.2f)" % (tot_trips, tot_net, tot_fees),
                "per_key": {k: {kk: round(v, 2) if isinstance(v, float) else v for kk, v in s.items()} for k, s in sorted(st["stats"].items())},
            }, indent=1))
            save_state(st)
            if nf:
                log(f"refused={len(refused)} open={len(st['pos'])} trips={tot_trips} net=${tot_net:.2f} (fills logged: {nf} events)")
        except Exception as e:
            log(f"loop err: {e}")
        time.sleep(LOOP_S)


if __name__ == "__main__":
    main()
