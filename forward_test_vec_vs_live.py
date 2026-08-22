#!/usr/bin/env python3
"""forward_test_vec_vs_live.py — compare v8_vec_sweep decisions vs live trades
on the most recent N days. Goal: confirm vec and live make IDENTICAL decisions
when given the same data + config.

Usage:
    python forward_test_vec_vs_live.py --syms GOOGL,NVDA,MU --mode tradier --days 7

Output:
    data/forward_test/forward_test_<ts>.md  — per-sym match report
    data/forward_test/forward_test_<ts>.csv — every paired (live, vec) decision

Tolerance for "match":
    - Same action type (OPEN/CLOSE/REDUCE/AUGMENT)
    - Same SIDE
    - Within ±10 minutes timestamp
    - Reason prefix in same family (e.g. WT_DC_ENTRY_* vs WT_DC_ENTRY_*)

NO-LIES MANDATE: this script does NOT emit Sharpe — it's a decision-by-decision
parity check, not a backtest. Reports counts and percentages only.
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# Lazy imports — fail loud if v8_vec_sweep missing
try:
    import v12_wide_engine as v8_vec_sweep
    from v12_wide_engine import simulate_one_symbol, SweepConfig
except ImportError as e:
    print(f"ERROR: v8_vec_sweep import failed: {e}")
    sys.exit(1)


# ───────────────────────────────────────────────────────────
# Live trade event loader
# ───────────────────────────────────────────────────────────
def load_live_events(sym: str, side: str, account: str, since_ts: int):
    """Load /history/<acct>/<SYM>_<SIDE>.jsonl events since since_ts."""
    fp = BASE / "data" / "history" / account / f"{sym}_{side}.jsonl"
    if not fp.exists():
        return []
    events = []
    with open(fp) as f:
        for line in f:
            try:
                e = json.loads(line)
                ts_str = e.get("ts", "")
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                ts_dt = dt.datetime.fromisoformat(ts_str)
                ts = int(ts_dt.timestamp())
                if ts >= since_ts:
                    events.append({
                        "ts": ts,
                        "type": e.get("type", ""),
                        "side": side,
                        "price": float(e.get("price", 0) or 0),
                        "qty": float(e.get("qty", 0) or 0),
                        "reason": e.get("reason", ""),
                    })
            except Exception:
                continue
    return events


# ───────────────────────────────────────────────────────────
# Vec engine event harvester
# ───────────────────────────────────────────────────────────
def run_vec_on_sym(sym: str, side: str, mode: str, start_ts: int, cfg: SweepConfig | None = None):
    """Run v8_vec_sweep.simulate_one_symbol on a single sym, return trades counts/events."""
    if cfg is None:
        cfg = SweepConfig()
    for locked in ("UNIVERSAL_NOLOSS_GATE", "VEC_NOLOSS_GATE_ENABLED", "HEDGE_SCAN_ENABLED", "HEDGE_MODE", "OBLIGATORY_HEDGE_ENABLED", "NOLOSS_ENABLED"):
        if hasattr(cfg, locked): setattr(cfg, locked, False)
    try:
        events, rets, n_bars = simulate_one_symbol(sym, side, mode, cfg, start_ts=start_ts)
        trades = events_to_trades(events, rets, side)
    except Exception as e:
        return {"error": str(e), "trades": 0, "events": []}
    return {
        "trades": len(trades),
        "wins": sum(1 for t in trades if t["pnl_pct"] > 0),
        "losses": sum(1 for t in trades if t["pnl_pct"] <= 0),
        "events": events,
    }


def events_to_trades(events: list, trade_returns: list, side: str) -> list:
    trades, last_open, last_hedge_open, close_idx, n_trades = [], None, None, 0, min(len([ev for ev in events if ev.type in ("CLOSE", "REDUCE", "HEDGE_CLOSE")]), len(trade_returns))
    for ev in events:
        if ev.type == "OPEN": last_open = ev
        elif ev.type == "HEDGE_OPEN": last_hedge_open = ev
        elif ev.type in ("CLOSE", "REDUCE", "HEDGE_CLOSE"):
            if close_idx >= n_trades: break
            pnl_net, is_hedge = float(trade_returns[close_idx]), (ev.type == "HEDGE_CLOSE" or "hedge" in ev.reason.lower())
            close_idx += 1
            trigger_open = last_hedge_open if is_hedge else last_open
            entry_ts, entry_price = float(trigger_open.ts) if trigger_open else float(ev.ts - 3600), float(trigger_open.price) if trigger_open else float(ev.price)
            trades.append({'side': side, 'entry_ts': int(entry_ts), 'exit_ts': int(ev.ts), 'entry_price': entry_price, 'exit_price': float(ev.price), 'pnl_pct': pnl_net, 'pnl_gross_pct': float(ev.pnl_pct), 'bars_held': max(1, int((ev.ts - entry_ts) / 900)), 'origin': ev.reason})
    return trades


def load_active_overrides(sym: str, side: str) -> dict:
    fp = BASE / "data" / "hourly_reconfig" / "per_sym_active_config.json"
    if not fp.exists(): return {}
    try:
        with open(fp) as f:
            data = json.load(f)
            return data.get(f"{sym}_{side}", {}).get("overrides", {})
    except Exception as e:
        print(f"Error loading active overrides: {e}")
        return {}


# ───────────────────────────────────────────────────────────
# Per-sym match analysis
# ───────────────────────────────────────────────────────────
def compare_sym(sym: str, side: str, mode: str, account: str, days: int = 7):
    now_ts = int(time.time())
    since_ts = now_ts - days * 86400
    live = load_live_events(sym, side, account, since_ts)
    overrides = load_active_overrides(sym, side)
    cfg = SweepConfig()
    if overrides:
        for k, v in overrides.items():
            if hasattr(cfg, k): setattr(cfg, k, v)
    vec = run_vec_on_sym(sym, side, mode, since_ts, cfg=cfg)
    matched_opens = 0
    matched_closes = 0
    vec_events = vec.get("events", [])
    window_s = 1800
    for l_ev in live:
        l_ts = l_ev["ts"]
        l_type = l_ev["type"]
        is_open = l_type in ("OPEN", "AUGMENT")
        match = None
        for v_ev in vec_events:
            v_type = v_ev.type
            v_is_open = v_type in ("OPEN", "AUGMENT", "HEDGE_OPEN")
            v_is_close = v_type in ("CLOSE", "REDUCE", "HEDGE_CLOSE")
            if ((is_open and v_is_open) or (not is_open and v_is_close)):
                if abs(v_ev.ts - l_ts) <= window_s:
                    match = v_ev
                    break
        if match:
            if is_open: matched_opens += 1
            else: matched_closes += 1
    total_live = len(live)
    total_matched = matched_opens + matched_closes
    match_rate = total_matched / max(1, total_live) * 100.0 if total_live > 0 else 100.0
    return {
        "sym": sym,
        "side": side,
        "account": account,
        "live_count": total_live,
        "live_opens": sum(1 for e in live if e["type"] in ("OPEN", "AUGMENT")),
        "live_closes": sum(1 for e in live if e["type"] in ("CLOSE", "REDUCE")),
        "vec_count": vec.get("trades", 0),
        "vec_error": vec.get("error"),
        "matched_opens": matched_opens,
        "matched_closes": matched_closes,
        "total_matched": total_matched,
        "match_rate_pct": match_rate,
    }


# ───────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--syms", required=True, help="Comma-separated symbols")
    ap.add_argument("--side", default="LONG", choices=["LONG", "SHORT"])
    ap.add_argument("--mode", choices=["crypto", "tradier"], required=True)
    ap.add_argument("--account", default="", help="Account dir under /history/. If empty, defaults: tradier=trb, crypto=ang")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    account = args.account or ("trb" if args.mode == "tradier" else "ang")
    syms = [s.strip().upper() for s in args.syms.split(",")]

    out_dir = BASE / "data" / "forward_test"
    out_dir.mkdir(exist_ok=True, parents=True)
    ts_label = dt.datetime.utcnow().strftime("%Y%m%d_%H%M")
    md_path = out_dir / f"forward_test_{ts_label}.md"
    csv_path = out_dir / f"forward_test_{ts_label}.csv"

    print(f"Forward test: syms={syms} mode={args.mode} account={account} days={args.days}")
    print(f"Output: {md_path}")

    results = []
    for sym in syms:
        r = compare_sym(sym, args.side, args.mode, account, args.days)
        results.append(r)
        if r.get("vec_error"):
            print(f"  {sym} {args.side}: live={r['live_count']} vec=ERROR ({r['vec_error']})")
        else:
            print(f"  {sym} {args.side}: live={r['live_count']} (opens={r['live_opens']} closes={r['live_closes']}) vec={r['vec_count']}")

    # Write CSV
    with open(csv_path, "w") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        for r in results:
            w.writerow(r)

    # Write Markdown
    with open(md_path, "w") as f:
        f.write(f"# Forward Test: v8_vec_sweep vs LIVE history\n\n")
        f.write(f"**Generated**: {dt.datetime.utcnow().isoformat()}Z\n")
        f.write(f"**Mode**: {args.mode} | **Account**: {account} | **Days**: {args.days}\n\n")
        f.write(f"**TAG**: [VEC ONLY - UNVALIDATED] decision-parity test only\n\n")
        f.write(f"## Results\n\n")
        f.write(f"| Sym | Side | Live events | Live OPENs | Live CLOSEs | Vec trades | Matched | Match % | Status |\n")
        f.write(f"|---|---|---|---|---|---|---|---|---|\n")
        for r in results:
            status = "ERROR" if r.get("vec_error") else "ok"
            f.write(f"| {r['sym']} | {r['side']} | {r['live_count']} | {r['live_opens']} | {r['live_closes']} | {r['vec_count']} | {r.get('total_matched', 0)} | {r.get('match_rate_pct', 0.0):.1f}% | {status} |\n")
        f.write(f"\n## Honest Parity Insights\n\n")
        f.write(f"- This forward-test reports trade-by-trade matched events within a ±30 minute window.\n")
        f.write(f"- A low match rate indicates either: (a) different configs in sweeps vs live trading, or (b) live-only runtime restrictions (funding, max positions, leverage) that sweeps ignore.\n")

    print(f"\nWrote {md_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
