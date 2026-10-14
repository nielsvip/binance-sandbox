#!/usr/bin/env python3
"""v15_loss_streak_watch — USER 2026-10-13 NEW RULE, part 2.

"We now apply live BEFORE 365D and backtest_v12_live verification, which does not make them obsolete — they need to run
also, but meanwhile the newest settings trade immediately, and as soon as 2 consecutive losing trades occur with the new
settings, the 365D gets prioritized and optimized until new settings replace the current ones."

Part 1 (apply-before-verify) lives in tools/v15_persym_golive.py: the 365D verdict is recorded, never a blocker.
This tool is part 2, the escalation trigger. It runs where the LIVE trades are (the MacBook):

  1. reads when each sym_side's CURRENT live settings were applied  -> per_sym_active.updated_at (per_sym_store.db)
  2. reads the live closed trades since that moment               -> data/per_trade_returns/{acct}_{YYYYMMDD}.jsonl
     net = pnl_pct - fees_pct (identical to tools/v15_causal_learner.ingest_live)
  3. counts the TRAILING consecutive losers per sym_side
  4. >= V15_LOSS_STREAK_N (default 2) -> writes the sym_side into data/v15_365_priority.json so the fleet director
     puts its 365D verification/optimization FIRST, and (optionally) pushes that file to the orchestrator.

"Until new settings replace the current ones" is automatic: the streak is only ever counted over trades newer than
`applied_at`, so the moment new settings land the counter restarts from zero and the entry drops out.

Read-only with respect to live trading: it never blocks, closes or sizes anything. It only ranks verification work.

Usage:
  python3 tools/v15_loss_streak_watch.py [--days 3] [--min-streak 2] [--push s1-pub] [--dry-run]
"""
import argparse
import datetime
import glob
import json
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("PER_SYM_STORE_DB") or os.path.join(ROOT, "data", "hourly_reconfig", "per_sym_store.db")
OUT = os.path.join(ROOT, "data", "v15_365_priority.json")


def _iso(ts):
    """ISO-8601 (with or without Z/offset) -> epoch seconds, else None. Pure."""
    try:
        t = str(ts or "").strip()
        if not t:
            return None
        t = t.replace("Z", "+00:00")
        d = datetime.datetime.fromisoformat(t)
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def net_of(trade):
    """Net return of one live closed trade, fees included. Pure (same formula as the causal learner)."""
    try:
        return float(trade.get("pnl_pct") or 0.0) - float(trade.get("fees_pct") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def trailing_loss_streak(trades):
    """Trades for ONE sym_side, any order -> (streak, [the losing trades, oldest first]).

    The streak is the run of consecutive losers at the END of the time-ordered list: a win anywhere after a loss
    resets it. Pure, unit-tested (tests/test_v15_loss_streak.py)."""
    rows = sorted([t for t in trades if _iso(t.get("ts")) is not None], key=lambda t: _iso(t.get("ts")))
    run = []
    for t in rows:
        if net_of(t) < 0:
            run.append(t)
        else:
            run = []
    return len(run), run


def applied_at_map(db_path=DB):
    """{sym_side: (epoch_applied, updated_at_iso, winning_tag)} for the settings that are LIVE right now."""
    out = {}
    try:
        con = sqlite3.connect(db_path, timeout=20.0)
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            return out
        for ss, ua, tag in con.execute("SELECT sym_side, updated_at, winning_tag FROM per_sym_active"):
            e = _iso(ua)
            if e is not None:
                out[str(ss)] = (e, str(ua), str(tag or ""))
        con.close()
    except Exception:
        return out
    return out


def load_trades(days, root=ROOT):
    """{sym_side: [trade, ...]} from the live per-trade ledgers of the last `days` days — DISTINCT confirmed trades
    (2026-10-14: per_trade_ledger collapses close-retry storms to one trade per position lifecycle; dust < $5 dropped)."""
    import sys as _sys
    _sys.path.insert(0, str(root))
    import per_trade_ledger as _ptl
    by_ss = {}
    for e in _ptl.final_closes(_ptl.load_distinct(root, None, int(days) + (1 if days % 1 else 0))):
        sym, side = e.get("symbol"), str(e.get("side") or "").upper()
        if not sym or side not in ("LONG", "SHORT"):
            continue
        by_ss.setdefault(f"{sym}_{side}", []).append(_ptl.strip(e))
    return by_ss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=float(os.environ.get("V15_LOSS_STREAK_DAYS", "3")))
    ap.add_argument("--min-streak", type=int, default=int(os.environ.get("V15_LOSS_STREAK_N", "2")))
    ap.add_argument("--db", default=DB)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--push", default=os.environ.get("V15_LOSS_STREAK_PUSH", ""), help="scp/rsync the priority file to this ssh host (orchestrator)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    applied = applied_at_map(args.db)
    trades = load_trades(args.days)
    flagged, scanned, no_anchor = {}, 0, 0
    for ss, ts in trades.items():
        anchor = applied.get(ss)
        if anchor is None:
            no_anchor += 1
            continue
        since, since_iso, tag = anchor
        fresh = [t for t in ts if (_iso(t.get("ts")) or 0) > since]
        if not fresh:
            continue
        scanned += 1
        streak, losers = trailing_loss_streak(fresh)
        if streak >= args.min_streak:
            flagged[ss] = {
                "state": "PRIORITIZE_365D",
                "streak": streak,
                "applied_at": since_iso,
                "winning_tag": tag,
                "trades_since_apply": len(fresh),
                "losers": [{"ts": t.get("ts"), "net_pct": round(net_of(t), 4), "reason": str(t.get("reason") or "")[:90],
                            "hold_minutes": t.get("hold_minutes"), "account": t.get("account")} for t in losers[-args.min_streak:]],
                "note": "live settings applied before 365D (USER 2026-10-13) — 365D verification/optimization prioritized until new settings replace these",
            }
    doc = {"_meta": {"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "host": os.uname().nodename, "min_streak": args.min_streak, "days": args.days,
                     "sym_sides_with_live_trades": len(trades), "scanned_since_apply": scanned,
                     "no_apply_anchor": no_anchor, "flagged": len(flagged),
                     "rule": "USER 2026-10-13: apply live before 365D; 2 consecutive losers -> prioritize 365D until new settings replace"},
           "sym_sides": flagged}
    if args.dry_run:
        print(json.dumps(doc, indent=1)[:4000])
        return 0
    tmp = args.out + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    os.replace(tmp, args.out)
    if args.push:
        try:
            subprocess.run(["rsync", "-az", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", args.out,
                            f"{args.push}:binance-sandbox/data/v15_365_priority.json"], timeout=120, check=False)
        except Exception as e:
            print(f"[loss-streak] push to {args.push} failed: {e}", flush=True)
    print(json.dumps({**doc["_meta"], "out": args.out, "flagged_sym_sides": sorted(flagged)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
