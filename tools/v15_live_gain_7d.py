#!/usr/bin/env python3
"""LIVE realised gain per sym_side over the trailing N days (USER 2026-10-20: "the biggest gainers get the most frequent updates").

Source = per_trade_ledger.final_closes (distinct lifecycle-final, non-dust closes of data/per_trade_returns, retry storms already
collapsed). Rows with entry_price <= 0 (broker echoes / legacy collapse rows, no real entry) are dropped. pnl_usd =
notional_usd * (pnl_pct - fees_pct) / 100 (net of the logged fees). The board's gain is NOT an input; the scheduler uses it only as
a tie-breaker. Also lists ACTIVELY TRADING sym_sides: an open position (data/open_position_sym_sides.json) or a close in the last 24 h.
Runs on the Mac (the live ledger lives there); --push copies the JSON to the scheduler host (S1 ~/binance-sandbox/data/).
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import per_trade_ledger as P  # noqa: E402


def build(days=7, now=None, root=ROOT):
    now = now or time.time()
    rows = P.final_closes(P.load_distinct(root=__import__("pathlib").Path(root), days=days, now=now))
    out = {}
    for r in rows:
        if float(r.get("entry_price") or 0) <= 0:
            continue
        ss = f"{r.get('symbol')}_{r.get('side')}"
        e = out.setdefault(ss, {"pnl_usd": 0.0, "trades": 0, "last_close_ts": 0.0})
        e["pnl_usd"] += float(r.get("notional_usd") or 0) * (float(r.get("pnl_pct") or 0) - float(r.get("fees_pct") or 0)) / 100.0
        e["trades"] += 1
        e["last_close_ts"] = max(e["last_close_ts"], float(r.get("_epoch") or 0))
    opens = []
    try:
        opens = list(json.load(open(os.path.join(root, "data", "open_position_sym_sides.json"))).get("sym_sides") or [])
    except Exception:
        pass
    trading = sorted(set(opens) | {k for k, v in out.items() if now - v["last_close_ts"] < 86400})
    for v in out.values():
        v["pnl_usd"] = round(v["pnl_usd"], 4)
    return {"generated_ts": now, "window_d": days, "source": "per_trade_ledger.final_closes, entry_price>0, net of fees_pct", "sym_sides": out, "open": sorted(opens), "trading": trading}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--push", default="")
    a = ap.parse_args()
    d = build(a.days)
    p = os.path.join(ROOT, "data", "v15_live_gain_7d.json")
    tmp = p + ".tmp"
    json.dump(d, open(tmp, "w"), indent=1)
    os.replace(tmp, p)
    print(f"live gain: {len(d['sym_sides'])} sym_sides, {len(d['trading'])} trading, top: " + ", ".join(f"{k} {v['pnl_usd']}" for k, v in sorted(d['sym_sides'].items(), key=lambda kv: -kv[1]['pnl_usd'])[:5]))
    if a.push:
        subprocess.run(["rsync", "-az", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", p, f"{a.push}:binance-sandbox/data/v15_live_gain_7d.json"], check=False, timeout=60)


if __name__ == "__main__":
    main()
