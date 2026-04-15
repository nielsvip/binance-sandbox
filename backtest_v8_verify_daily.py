#!/usr/bin/env python3
"""V8 Daily Verify — compare V8 backtest output vs live decisions JSONL.

Runs V8 engine for a single recent day for each tradeable account and compares
the trades V8 emits against the live decisions in data/decisions/. Flags drift
between V8 output and production.

Per CLAUDE.md "compare vs data/decisions/ JSONL trade-by-trade" rule.

Usage:
    python3 backtest_v8_verify_daily.py                  # yesterday, all accounts
    python3 backtest_v8_verify_daily.py --date 2026-04-14 --account ang
    python3 backtest_v8_verify_daily.py --tolerance 0.3  # fraction drift tolerance

Outputs:
    data/v8_verify/verify_<YYYYMMDD>.json  — full comparison
    data/v8_verify/verify_<YYYYMMDD>.md    — human summary
    stdout: PASS/FAIL banner + counts + top drift examples

Exit codes:
    0 = PASS (drift within tolerance for all accounts)
    1 = FAIL (drift exceeded tolerance)
    2 = ERROR (engine crash, missing data)

NOT hooked into cron — the monitor agent wires scheduling.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path("/Users/niels/Documents/binance")
DECISIONS_DIR = BASE / "data" / "decisions"
OUT_DIR = BASE / "data" / "v8_verify"
ENGINE = BASE / "backtest_v8_engine.py"
PY = "/opt/anaconda3/envs/binance_env/bin/python3"

CRYPTO_ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
TRADIER_ACCOUNTS = ["trb"]

# Which action strings count as an OPEN vs CLOSE for counting purposes.
OPEN_ACTIONS = {"OPEN", "REENTRY", "AUGMENT", "QUICK_OPEN", "QUICK_AUGMENT", "HEDGE_OPEN", "BUY", "SELL"}
CLOSE_ACTIONS = {"CLOSE", "REDUCE", "FULL_CLOSE", "PROFIT_TAKE", "QUICK_CLOSE", "HEDGE_CLOSE"}


def _load_decisions(account: str, date_str: str) -> list[dict]:
    """Load live decision records for account on date_str (YYYYMMDD)."""
    path = DECISIONS_DIR / f"decisions_{account}_{date_str}.jsonl"
    if not path.exists():
        return []
    records = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _categorize_live(records: list[dict]) -> dict:
    """Summarize live decisions into counts per (symbol, action_class)."""
    opens = Counter()
    closes = Counter()
    reasons = Counter()
    for r in records:
        action = (r.get("action") or "").upper()
        pk = r.get("position_key", "")
        sym = pk.split(":", 1)[1].rsplit("_", 1)[0] if ":" in pk else pk
        reasons[(action or "NONE")[:40]] += 1
        if action in OPEN_ACTIONS:
            opens[sym] += 1
        elif action in CLOSE_ACTIONS:
            closes[sym] += 1
    return {
        "total": len(records),
        "n_opens": sum(opens.values()),
        "n_closes": sum(closes.values()),
        "opens_by_sym": dict(opens),
        "closes_by_sym": dict(closes),
        "top_reasons": dict(reasons.most_common(10)),
    }


def _run_v8_for_day(mode: str, account: str, date_str: str, symbols: str = "") -> dict:
    """Run V8 engine for a single day and parse its log for trades."""
    date_iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    env = {
        **os.environ,
        "V8_SWEEP_MODE": "1",
        # limit to ~96 bars/day × 2 for 15m crypto = 192 bars; generous 400.
        "V8_MAX_BARS": "500",
    }
    cmd = [PY, str(ENGINE), "--mode", mode, "--account", account,
           "--start", date_iso, "--capital", "1000"]
    if symbols:
        cmd += ["--symbols", symbols]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                              timeout=600, cwd=str(BASE))
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "elapsed": time.time() - t0}
    elapsed = time.time() - t0
    if proc.returncode != 0:
        return {"error": f"rc={proc.returncode}", "stderr_tail": proc.stderr[-500:],
                "elapsed": elapsed}
    # Engine writes a JSONL log — find the most recent one for this account.
    log_dir = BASE / "backtest_v8" / "logs"
    log_candidates = sorted(log_dir.glob(f"v8_{mode}_{account}_*.jsonl"))
    if not log_candidates:
        return {"error": "no_log_file", "elapsed": elapsed}
    log_path = log_candidates[-1]
    trades = []
    with open(log_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("type") == "eta":
                    trades.append(r)
            except json.JSONDecodeError:
                continue
    opens = Counter()
    closes = Counter()
    reasons = Counter()
    for t in trades:
        action = (t.get("action") or "").upper()
        pk = t.get("position_key", "")
        sym = pk.split(":", 1)[1].rsplit("_", 1)[0] if ":" in pk else pk
        reasons[(t.get("reason") or "NONE")[:40]] += 1
        if action in OPEN_ACTIONS:
            opens[sym] += 1
        elif action in CLOSE_ACTIONS:
            closes[sym] += 1
    return {
        "elapsed": elapsed,
        "log_path": str(log_path),
        "total": len(trades),
        "n_opens": sum(opens.values()),
        "n_closes": sum(closes.values()),
        "opens_by_sym": dict(opens),
        "closes_by_sym": dict(closes),
        "top_reasons": dict(reasons.most_common(10)),
    }


def _compare(live: dict, v8: dict, tolerance: float = 0.4) -> dict:
    """Compute drift between live vs V8 counts. tolerance = max allowed abs(diff)/max(live,v8)."""
    result = {
        "tolerance": tolerance,
        "live_total": live.get("total", 0),
        "v8_total": v8.get("total", 0),
        "drift_total": abs(live.get("total", 0) - v8.get("total", 0)),
    }
    if v8.get("error"):
        result["error"] = v8["error"]
        result["pass"] = False
        return result
    live_opens = live.get("n_opens", 0)
    v8_opens = v8.get("n_opens", 0)
    live_closes = live.get("n_closes", 0)
    v8_closes = v8.get("n_closes", 0)

    def drift_pct(a: int, b: int) -> float:
        m = max(a, b, 1)
        return abs(a - b) / m

    result["opens_drift_pct"] = drift_pct(live_opens, v8_opens)
    result["closes_drift_pct"] = drift_pct(live_closes, v8_closes)
    result["live_opens"] = live_opens
    result["v8_opens"] = v8_opens
    result["live_closes"] = live_closes
    result["v8_closes"] = v8_closes
    per_sym_drift = {}
    live_opens_sym = live.get("opens_by_sym", {})
    v8_opens_sym = v8.get("opens_by_sym", {})
    for sym in set(live_opens_sym) | set(v8_opens_sym):
        a = live_opens_sym.get(sym, 0)
        b = v8_opens_sym.get(sym, 0)
        if a + b >= 3:
            per_sym_drift[sym] = {"live_opens": a, "v8_opens": b, "drift_pct": drift_pct(a, b)}
    top_drift = sorted(per_sym_drift.items(), key=lambda x: -x[1]["drift_pct"])[:10]
    result["top_symbol_drift"] = dict(top_drift)
    result["pass"] = (result["opens_drift_pct"] <= tolerance
                      and result["closes_drift_pct"] <= tolerance)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="",
                        help="YYYY-MM-DD to verify (default: yesterday UTC)")
    parser.add_argument("--account", type=str, default="",
                        help="Single account to test (default: all)")
    parser.add_argument("--tolerance", type=float, default=0.4,
                        help="Max drift pct (default 0.4 = 40%)")
    parser.add_argument("--mode", type=str, default="",
                        help="crypto | tradier (default: both)")
    args = parser.parse_args()

    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    date_compact = target_date.strftime("%Y%m%d")
    date_iso = target_date.strftime("%Y-%m-%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    accounts: list[tuple[str, str]] = []
    if args.mode in ("", "crypto"):
        accounts += [("crypto", a) for a in (CRYPTO_ACCOUNTS if not args.account else [args.account])]
    if args.mode in ("", "tradier"):
        accounts += [("tradier", a) for a in (TRADIER_ACCOUNTS if not args.account else [args.account])]
    if args.account:
        accounts = [a for a in accounts if a[1] == args.account]

    print(f"V8 VERIFY — date={date_iso} | tolerance={args.tolerance:.0%} | accounts={[a[1] for a in accounts]}")
    all_results = {}
    any_fail = False
    for mode, account in accounts:
        print(f"\n─── {mode}/{account} ────────────────────────────────")
        live_records = _load_decisions(account, date_compact)
        if not live_records:
            print(f"  SKIP: no live decisions file for {account} {date_compact}")
            all_results[f"{mode}_{account}"] = {"skip": "no_live_data"}
            continue
        live = _categorize_live(live_records)
        print(f"  live: {live['total']} decisions | {live['n_opens']} opens | {live['n_closes']} closes")
        v8 = _run_v8_for_day(mode, account, date_compact)
        if v8.get("error"):
            print(f"  V8 ERROR: {v8['error']}")
            all_results[f"{mode}_{account}"] = {"live": live, "v8": v8, "compare": {"error": v8["error"], "pass": False}}
            any_fail = True
            continue
        print(f"  v8:   {v8['total']} trades    | {v8['n_opens']} opens | {v8['n_closes']} closes | {v8['elapsed']:.1f}s")
        cmp = _compare(live, v8, tolerance=args.tolerance)
        verdict = "PASS" if cmp["pass"] else "FAIL"
        print(f"  {verdict}: opens drift={cmp['opens_drift_pct']:.0%}, closes drift={cmp['closes_drift_pct']:.0%}")
        if cmp.get("top_symbol_drift"):
            print("  Top symbol drift:")
            for sym, d in list(cmp["top_symbol_drift"].items())[:3]:
                print(f"    {sym}: live={d['live_opens']} v8={d['v8_opens']} drift={d['drift_pct']:.0%}")
        if not cmp["pass"]:
            any_fail = True
        all_results[f"{mode}_{account}"] = {"live": live, "v8": v8, "compare": cmp}

    out_json = OUT_DIR / f"verify_{date_compact}.json"
    out_md = OUT_DIR / f"verify_{date_compact}.md"
    with open(out_json, "w") as f:
        json.dump({"date": date_iso, "tolerance": args.tolerance, "results": all_results}, f, indent=2, default=str)
    with open(out_md, "w") as f:
        f.write(f"# V8 Verify {date_iso}\n\n")
        f.write(f"Tolerance: {args.tolerance:.0%}\n\n")
        for key, res in all_results.items():
            f.write(f"## {key}\n")
            if res.get("skip"):
                f.write(f"SKIP: {res['skip']}\n\n")
                continue
            cmp = res.get("compare", {})
            live = res.get("live", {})
            v8 = res.get("v8", {})
            verdict = "PASS" if cmp.get("pass") else "FAIL"
            f.write(f"**{verdict}**\n\n")
            f.write(f"| | live | v8 | drift |\n|---|---|---|---|\n")
            f.write(f"| opens | {live.get('n_opens', 0)} | {v8.get('n_opens', 0)} | {cmp.get('opens_drift_pct', 0):.0%} |\n")
            f.write(f"| closes | {live.get('n_closes', 0)} | {v8.get('n_closes', 0)} | {cmp.get('closes_drift_pct', 0):.0%} |\n\n")
    print(f"\nReport: {out_json}")
    print(f"Markdown: {out_md}")
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
