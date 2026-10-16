#!/usr/bin/env python3
"""tradier_order_storm_watch — detect an order storm within seconds of a restart, instead of after the damage.

USER 2026-10-15: "restarting all now tradier_ will probably refuse but if it starts firing orders crazily again we
will find out immediately". This makes "immediately" true. Three storms are known to have happened here:
  * 2026-10-14: ~5000 rejected GTC stop orders overnight (qty mismatch -> reject -> retry), which is why the broker
    FIXED_STOP backstop was switched off. The backstop is back ON as of 2026-10-15 with the qty/listing fixes, so
    this watch is the safety net for exactly that path.
  * 2026-10-15: 109 lifecycles in one session, 12 sym_sides with >=3 round-trips, 11 of them negative (-$291).
  * 581 rejected stops in 90 min on one symbol (GOOGL trb, stops sent for 2/4/12/24 while the broker held 6).

Read-only. Watches three independent signals so one quiet source cannot mask a storm:
  1. CLOSES   — new rows in data/per_trade_returns/tr{a,b,c}_<day>.jsonl
  2. STOPS    — FIXED_STOP_PLACED_TR / _RETRY_TR / _BREAKER_TR / "no order id" in ~/logs/tradier_manage_tr*.log
  3. CHURN    — the same sym_side round-tripping repeatedly inside the window

    python3 tools/tradier_order_storm_watch.py                 # one snapshot, exit 1 if a threshold is breached
    python3 tools/tradier_order_storm_watch.py --watch         # loop until Ctrl-C, print only on change
    python3 tools/tradier_order_storm_watch.py --window-min 5 --max-closes 12 --max-stop-retries 8
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = Path(os.path.expanduser("~/logs"))
STOP_PAT = re.compile(r"FIXED_STOP_(PLACED|RETRY|BREAKER|QTY_UNVERIFIED|GONE|ADOPTED)_TR|broker returned no order id")


def _epoch(ts) -> float:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except Exception:
        return 0.0


def closes(window_s: float) -> list:
    """Confirmed closes inside the window, newest last. One row per ledger line (a storm shows as many rows)."""
    now = time.time()
    day = time.strftime("%Y%m%d", time.gmtime())
    out = []
    for f in glob.glob(str(ROOT / "data" / "per_trade_returns" / f"tr?_{day}.jsonl")):
        try:
            lines = Path(f).read_text(errors="replace").splitlines()
        except OSError:
            continue
        for ln in lines[-4000:]:
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            t = _epoch(r.get("ts"))
            if t and now - t <= window_s:
                out.append({
                    "ts": str(r.get("ts"))[11:19], "acct": r.get("account"), "symbol": r.get("symbol"),
                    "side": r.get("side"), "pnl_pct": r.get("pnl_pct"),
                    "key": f"{r.get('symbol')}_{r.get('side')}", "reason": str(r.get("reason"))[:60],
                })
    return sorted(out, key=lambda x: x["ts"])


def stop_events(window_s: float) -> dict:
    """FIXED_STOP log lines in the window, counted by kind. Logs are '%m-%d %H:%M:%S' local-to-the-process UTC."""
    now = time.time()
    counts: dict = {}
    for f in glob.glob(str(LOGS / "tradier_manage_tr*.log")):
        try:
            lines = Path(f).read_text(errors="replace").splitlines()[-6000:]
        except OSError:
            continue
        for ln in lines:
            m = STOP_PAT.search(ln)
            if not m:
                continue
            tm = re.match(r"(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)", ln)
            if tm:
                mo, dd, hh, mi, ss = (int(x) for x in tm.groups())
                y = time.gmtime().tm_year
                try:
                    t = datetime(y, mo, dd, hh, mi, ss, tzinfo=timezone.utc).timestamp()
                except ValueError:
                    continue
                if now - t > window_s or t - now > 3600:
                    continue
            counts[m.group(0)] = counts.get(m.group(0), 0) + 1
    return counts


def assess(window_min: float, max_closes: int, max_stop_retries: int, max_roundtrips: int) -> dict:
    w = window_min * 60.0
    c = closes(w)
    s = stop_events(w)
    per_key: dict = {}
    for r in c:
        per_key[r["key"]] = per_key.get(r["key"], 0) + 1
    churn = {k: v for k, v in per_key.items() if v >= max_roundtrips}
    retries = sum(v for k, v in s.items() if "RETRY" in k or "BREAKER" in k or "no order id" in k)
    alerts = []
    if len(c) > max_closes:
        alerts.append(f"CLOSE_STORM: {len(c)} closes in {window_min:g} min (limit {max_closes})")
    if retries > max_stop_retries:
        alerts.append(f"STOP_REJECT_STORM: {retries} stop retry/breaker/no-id events in {window_min:g} min (limit {max_stop_retries}) — this is the 2026-10-14 signature")
    if churn:
        alerts.append(f"CHURN: {churn} round-tripped >={max_roundtrips}x in {window_min:g} min")
    if s.get("FIXED_STOP_BREAKER_TR"):
        alerts.append(f"BREAKER TRIPPED x{s['FIXED_STOP_BREAKER_TR']} — placement is being rejected; read the reason before touching the flag")
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "window_min": window_min,
            "n_closes": len(c), "stop_events": s, "stop_retries": retries, "churn": churn,
            "alerts": alerts, "verdict": "ALERT" if alerts else "quiet", "recent": c[-10:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-min", type=float, default=10.0)
    ap.add_argument("--max-closes", type=int, default=25, help="2026-10-15 ran 109 closes/session; 25 in 10 min is already abnormal")
    ap.add_argument("--max-stop-retries", type=int, default=10)
    ap.add_argument("--max-roundtrips", type=int, default=3, help="same sym_side closing >=N times in the window")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    last = None
    while True:
        r = assess(a.window_min, a.max_closes, a.max_stop_retries, a.max_roundtrips)
        sig = (r["n_closes"], r["stop_retries"], tuple(sorted(r["churn"].items())), tuple(r["alerts"]))
        if not a.watch or sig != last:
            if a.json:
                print(json.dumps(r, indent=1))
            else:
                print(f"[{r['at']}] closes={r['n_closes']} stop_retries={r['stop_retries']} "
                      f"stop_events={r['stop_events'] or '{}'} churn={r['churn'] or '{}'} -> {r['verdict']}")
                for x in r["alerts"]:
                    print(f"   🔴 {x}")
                if r["alerts"] and r["recent"]:
                    for q in r["recent"]:
                        print(f"      {q['ts']} {q['acct']} {q['key']:16} {q['pnl_pct']}%  {q['reason']}")
            last = sig
        if not a.watch:
            return 1 if r["alerts"] else 0
        try:
            time.sleep(a.interval)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
