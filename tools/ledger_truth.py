#!/usr/bin/env python
"""ledger_truth.py — REAL trade truth from data/history/{account}/{SYM_SIDE}.jsonl.

The backtest engines (v12_quick_engine, backtest_v12_engine) have been shown to diverge
massively from reality (e.g. CRWD_LONG: engine 501 trades/-25.74% vs real ~12 opens/30d,
≈breakeven). NEVER trust an engine number that this tool contradicts. This reconstructs
avg-cost round-trip P&L from the actual executed-trade ledger — the source of truth.

Usage:
  python tools/ledger_truth.py trb CRWD_LONG            # full history
  python tools/ledger_truth.py trb CRWD_LONG --days 30  # last N days
  python tools/ledger_truth.py --list trb               # list ledgers for an account
"""
import os, sys, json, argparse, datetime as dt
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST = os.path.join(ROOT, "data", "history")


def _parse(ts):
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def reconstruct(account, sym_side, days=0):
    path = os.path.join(HIST, account, f"{sym_side}.jsonl")
    if not os.path.exists(path):
        return {"account": account, "sym_side": sym_side, "error": f"no ledger at {path}"}
    is_long = sym_side.endswith("_LONG")
    evs = []
    for l in open(path):
        l = l.strip()
        if not l:
            continue
        try:
            e = json.loads(l)
        except Exception:
            continue
        if _parse(e.get("ts")) is not None:
            evs.append(e)
    evs.sort(key=lambda e: e["ts"])
    if not evs:
        return {"account": account, "sym_side": sym_side, "error": "empty ledger"}
    d1 = _parse(evs[-1]["ts"])
    if days:
        cut = d1 - dt.timedelta(days=days)
        evs = [e for e in evs if _parse(e["ts"]) >= cut]
    if not evs:
        return {"account": account, "sym_side": sym_side, "error": f"no events in last {days}d"}
    d0 = _parse(evs[0]["ts"]); d1 = _parse(evs[-1]["ts"])
    qty = 0.0; cost = 0.0; realized = 0.0
    opens = augs = reduces = closes = 0
    rts = []
    for e in evs:
        t = str(e.get("type", "")).upper()
        q = float(e.get("qty", 0) or 0); p = float(e.get("price", 0) or 0)
        if t in ("OPEN", "AUGMENT"):
            opens += (t == "OPEN"); augs += (t == "AUGMENT")
            cost += q * p; qty += q
        elif t in ("REDUCE", "CLOSE"):
            reduces += (t == "REDUCE"); closes += (t == "CLOSE")
            if qty > 1e-9:
                avg = cost / qty; sold = min(q, qty)
                # long: pnl=(exit-avg); short side is logged with the same convention in these
                # ledgers (entry then exit); sign handled by is_long.
                pnl = (p - avg) * sold * (1.0 if is_long else -1.0)
                realized += pnl; rts.append(pnl)
                cost -= avg * sold; qty -= sold
    wins = sum(1 for r in rts if r > 0)
    return {"account": account, "sym_side": sym_side,
            "window": f"{d0.date()}..{d1.date()} ({(d1 - d0).days}d)" if days == 0 else f"last {days}d ({d0.date()}..{d1.date()})",
            "events": len(evs), "opens": opens, "augments": augs, "reduces": reduces, "closes": closes,
            "round_trips": len(rts), "wins": wins,
            "win_rate_pct": round(100 * wins / max(1, len(rts)), 1),
            "realized_pnl_usd": round(realized, 2),
            "still_open_qty": round(qty, 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("account", nargs="?"); ap.add_argument("sym_side", nargs="?")
    ap.add_argument("--days", type=int, default=0); ap.add_argument("--list", dest="lst", default="")
    a = ap.parse_args()
    if a.lst:
        d = os.path.join(HIST, a.lst)
        if not os.path.isdir(d):
            print(f"no account dir {d}"); return
        for f in sorted(os.listdir(d)):
            if f.endswith(".jsonl"):
                print(f[:-6])
        return
    if not a.account or not a.sym_side:
        ap.print_help(); return
    r = reconstruct(a.account, a.sym_side, a.days)
    print(json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
