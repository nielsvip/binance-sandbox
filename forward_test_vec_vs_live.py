#!/usr/bin/env python3
"""forward_test_vec_vs_live.py — compare vec_engine_v1 decisions vs live trades
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

# Lazy imports — fail loud if vec engine missing
try:
    import vec_engine_v1
    from vec_engine_v1 import VecEngine, VecConfig
except ImportError as e:
    print(f"ERROR: vec_engine_v1 import failed: {e}")
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
def run_vec_on_sym(sym: str, mode: str, start_ts: int, cfg: VecConfig | None = None):
    """Run vec_engine_v1 on a single sym, return all trade events as dicts."""
    if cfg is None:
        cfg = VecConfig()
    engine = VecEngine(mode=mode)
    # Run as single-sym simulation
    try:
        result = engine.simulate(symbols=[sym], cfg=cfg, start_ts=start_ts)
    except Exception as e:
        return {"error": str(e), "events": []}
    # vec_engine returns aggregate result — we need per-bar trade events.
    # For now, approximate by reading store and replaying entry/exit logic
    # would require deeper instrumentation. As a v1 forward test, return the
    # aggregate counts so user can see "vec made N trades, live made M trades".
    return {
        "trades": int(result.get("trades", 0) or 0),
        "wins": int(result.get("wins", 0) or 0),
        "losses": int(result.get("losses", 0) or 0),
        "pool_sharpe": float(result.get("pool_sharpe", 0) or 0),
        "max_dd_pct": float(result.get("max_dd_pct", 0) or 0),
        "events": [],  # placeholder — needs engine instrumentation
    }

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
    cfg = VecConfig().update_from_dict(overrides) if overrides else VecConfig()
    vec = run_vec_on_sym(sym, mode, since_ts, cfg=cfg)
    return {
        "sym": sym,
        "side": side,
        "account": account,
        "live_count": len(live),
        "live_opens": sum(1 for e in live if e["type"] in ("OPEN", "AUGMENT")),
        "live_closes": sum(1 for e in live if e["type"] in ("CLOSE", "REDUCE")),
        "vec_count": vec.get("trades", 0),
        "vec_error": vec.get("error"),
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
        f.write(f"# Forward Test: vec_engine_v1 vs LIVE history\n\n")
        f.write(f"**Generated**: {dt.datetime.utcnow().isoformat()}Z\n")
        f.write(f"**Mode**: {args.mode} | **Account**: {account} | **Days**: {args.days}\n\n")
        f.write(f"**TAG**: [VEC ONLY - UNVALIDATED] decision-parity test only\n\n")
        f.write(f"## Results\n\n")
        f.write(f"| Sym | Side | Live events | Live OPENs | Live CLOSEs | Vec trades | Status |\n")
        f.write(f"|---|---|---|---|---|---|---|\n")
        for r in results:
            status = "ERROR" if r.get("vec_error") else "ok"
            f.write(f"| {r['sym']} | {r['side']} | {r['live_count']} | {r['live_opens']} | {r['live_closes']} | {r['vec_count']} | {status} |\n")
        f.write(f"\n## Honest Limitations\n\n")
        f.write(f"- This v1 forward-test reports AGGREGATE COUNTS only — vec trade count vs live trade count.\n")
        f.write(f"- Trade-by-trade timestamp matching requires deeper engine instrumentation (planned v2).\n")
        f.write(f"- A large count mismatch = vec and live are NOT making identical decisions.\n")
        f.write(f"- Use this output to identify which syms have the biggest divergence, then dig deeper.\n")

    print(f"\nWrote {md_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
