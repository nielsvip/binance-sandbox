#!/usr/bin/env python3
"""stocks_parity_watch — RTH threshold monitor (USER 2026-10-22).

"Verify we always have ~40 pos open and 10-50 trades per hour during market
hours - if not the system is broken." Read-only. Checks: stocks opens (nonzero
broker positions), stocks trade legs in the last 60 min, indicator snapshot
age, tradeable 15m tail lag, daemon liveness, pure-board verdict freshness.
BREACH (exit 1) = book AND flow both dead in RTH (opens < --min-opens-breach
with rate < --min-rate). WARN lines otherwise. Outside RTH it reports and
exits 0 (no market, no expectation).
Usage: python3 tools/stocks_parity_watch.py [--min-opens 25] [--min-opens-breach 8] [--min-rate 10] [--max-rate 50]
Cron (RTH school run; harmless off-hours): */15 * * * * ... # STOCKS_PARITY_WATCH
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUFFIX = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
OUT = ROOT / "data" / "paper_forward_comprehensive" / "stocks_watch.json"


def _ep(s) -> float:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


def rth_now() -> bool:
    et = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-4)))
    return et.weekday() < 5 and (9, 30) <= (et.hour, et.minute) < (16, 0)


def opens() -> list:
    out = []
    for a in ("tra", "trb", "trc"):
        for side in ("long", "short"):
            try:
                d = json.loads((ROOT / a / f"{side}_positions.json").read_text())
            except Exception:
                continue
            for k, pos in d.items():
                try:
                    amt = abs(float((pos or {}).get("positionAmt") or 0))
                except Exception:
                    amt = 0
                if amt > 0 and not k.split(":")[-1].rsplit("_", 1)[0].endswith(SUFFIX):
                    out.append(k)
    return sorted(out)


def rate_h() -> int:
    now = datetime.now(timezone.utc)
    n = 0
    for acct in ("tra", "trb", "trc"):
        for f in glob.glob(str(ROOT / "data" / "history" / acct / "*.jsonl")):
            if f.split("/")[-1].rsplit("_", 2)[0].endswith(SUFFIX):
                continue
            for ln in open(f, errors="replace"):
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("type") not in ("OPEN", "CLOSE", "AUGMENT", "REDUCE"):
                    continue
                try:
                    ts = datetime.fromisoformat(str(r.get("ts", "")).replace("Z", "+00:00"))
                except Exception:
                    continue
                if now - ts <= timedelta(hours=1):
                    n += 1
    return n


def snap_age_s():
    try:
        return time.time() - (ROOT / "data" / "tradier" / "tradier_indicators_latest.json").stat().st_mtime
    except OSError:
        return None


def tail_lag_min():
    lags = []
    now = time.time()
    for name in ("symbols_trb_long.json", "symbols_trb_short.json", "symbols_tra_long.json",
                 "symbols_tra_short.json", "symbols_trc_long.json", "symbols_trc_short.json"):
        try:
            syms = json.loads((ROOT / name).read_text())
        except Exception:
            continue
        for s in syms:
            try:
                rows = json.loads((ROOT / "klines_cache" / "tradier" / f"{s}_15m.json").read_text())
                rows = rows if isinstance(rows, list) else (rows.get("data") or rows.get("klines") or [])
                lags.append((now - _ep(rows[-1]["timestamp"])) / 60)
            except Exception:
                lags.append(1e9)
    return (max(lags) if lags else None), len(lags)


def daemons() -> dict:
    try:
        ps = subprocess.run(["ps", "-eo", "command"], capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return {}
    return {a: (f"tradier_manage.py --accounts {a}" in ps) for a in ("tra", "trb", "trc")}


def verdict_age_s():
    try:
        files = sorted((ROOT / "data" / "pure_board").glob("intents_*.jsonl"))
        last = 0.0
        for fp in files[-3:]:
            for ln in open(fp, errors="replace"):
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("acct") in ("tra", "trb", "trc") and r.get("action") in ("CONVERGE_OPEN", "CONVERGE_CLOSE"):
                    last = max(last, float(r.get("ts") or 0))
        return time.time() - last if last else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-opens", type=float, default=25)
    ap.add_argument("--min-opens-breach", type=float, default=8)
    ap.add_argument("--min-rate", type=float, default=10)
    ap.add_argument("--max-rate", type=float, default=50)
    a = ap.parse_args()
    op, rate = opens(), rate_h()
    sa, (lag, nlag) = snap_age_s(), tail_lag_min()
    rep = {"ts": datetime.now(timezone.utc).isoformat(), "rth": rth_now(), "opens": len(op),
           "open_list": op, "rate_h": rate, "snap_age_s": sa, "tail_lag_max_min": lag,
           "tail_n": nlag, "daemons": daemons(), "verdict_age_s": verdict_age_s()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, indent=1))
    msgs = [f"opens={len(op)} rate={rate}/h snap_age={sa} tail_max={lag} daemons={rep['daemons']} verdict_age={rep['verdict_age_s']}"]
    breach = False
    if not rep["rth"]:
        print("OFF-HOURS " + msgs[0])
        return 0
    if len(op) < a.min_opens:
        msgs.append(f"WARN opens {len(op)} < {a.min_opens}")
    if rate < a.min_rate or rate > a.max_rate:
        msgs.append(f"WARN rate {rate}/h outside {a.min_rate}-{a.max_rate}")
    if sa is not None and sa > 900:
        msgs.append(f"WARN snapshot stale {sa:.0f}s")
    if lag is not None and lag > 45:
        msgs.append(f"WARN tail lag {lag:.0f}min")
    dead = [k for k, v in rep["daemons"].items() if not v]
    if dead:
        msgs.append(f"WARN daemons down: {dead}")
    if len(op) < a.min_opens_breach and rate < a.min_rate:
        msgs.append("BREACH book and flow both dead in RTH")
        breach = True
    print(("BREACH " if breach else "OK ") + " | ".join(msgs))
    return 1 if breach else 0


if __name__ == "__main__":
    sys.exit(main())
