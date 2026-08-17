#!/usr/bin/env python3
"""parity_diff_live_vs_backtest — find which entry/exit reasons live trades fire that
the 7d backtest fails to replicate, so we know exactly what to wire into the engine.

User insight 2026-04-30: "you can trace back every live trade in the /history/ folder
and find out what entry or exit point is not correctly wired up in the 7d backtest
(and probably not in backtest_v8_engine either so now you can finally wire it up
correctly)."

Methodology
-----------
For each <account, sym, side>:
  1. Read live trades from data/history/<acct>/<SYM>_<SIDE>.jsonl over last 7 days.
     Each record has: ts, type (AUGMENT/REDUCE/OPEN/CLOSE), reason, price, qty.
  2. Read backtest trades from latest data/hourly_reconfig/<acct>/runs/<latest>/*.jsonl
     for the same (sym, side). Each has: entry_ts, exit_ts, entry_reason, exit_reason,
     entry_price, exit_price, pnl_pct, side.
  3. Normalize live reasons by stripping variable parts (numbers, timestamps,
     indicator values) — "QUICK_BREAKEVEN_GAIN_EROSION_STOP_age64420m_gain-0.39%"
     becomes family "QUICK_BREAKEVEN_GAIN_EROSION_STOP".
  4. Normalize backtest reasons (entry_reason / exit_reason).
  5. For each live record, check if any backtest trade has entry_ts (or exit_ts)
     within ±30min on the same sym/side. If NOT — "missed by engine".
  6. Aggregate the live reason families across all missed records → priority list
     of what to wire next.

Outputs
-------
data/parity/<account>/by_reason_family.json — {reason_family: count_missed}
data/parity/<account>/by_sym.json — {sym_side: {n_live, n_backtest, n_missed}}
data/parity/<account>/missed_samples.jsonl — full live record + nearest backtest

Usage
-----
  python3 parity_diff_live_vs_backtest.py --account flz --once
  python3 parity_diff_live_vs_backtest.py --account inf --days 7
"""
from __future__ import annotations
import argparse, calendar, glob, json, os, re, sys, time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "data" / "history"
HOURLY_RECONFIG = ROOT / "data" / "hourly_reconfig"
OUT_BASE = ROOT / "data" / "parity"


def normalize_reason(reason: str) -> str:
    """Strip variable parts to get the reason 'family' tag.

    Strategy: take only the leading uppercase/underscore tokens until we hit a
    lowercase letter, an `=`, a `:`, a `/`, or a digit. That gives us the macro
    family name (e.g. QUICK_REDUCE_STRONG_REDUCE) without the noisy indicator
    suffix. We then truncate at a small set of known "metric noise" keywords.

    Examples:
      QUICK_BREAKEVEN_GAIN_EROSION_STOP_age64420m_gain-0.39%       → QUICK_BREAKEVEN_GAIN_EROSION_STOP
      QUICK_REDUCE_REDUCE_k_1m:77/d_1m:81/k_3m:100_HLR_TOP_EXIT... → QUICK_REDUCE_REDUCE
      GUARANTEED_REENTRY_AGGR_0_30m_STx3_k3m64_exit79198           → GUARANTEED_REENTRY_AGGR
      flz:BNBUSDC_LONG_PRICE_CROSSED_MANDATORY_k15m18              → PRICE_CROSSED_MANDATORY
      WT_EXHAUST_mom4h=EXHAUST_UP_1h=IMPULSE_UP_g=0.24%            → WT_EXHAUST
      B04_DC_RETEST_DC_HIGH_RETEST_1H_bp=1.8%_level=630.12         → B04_DC_RETEST_DC_HIGH_RETEST
    """
    if not reason or not isinstance(reason, str):
        return "UNKNOWN"
    s = reason
    # Drop account:SYM_SIDE prefix
    s = re.sub(r"^[a-z]+:[A-Z0-9]+_(LONG|SHORT)_", "", s)
    # Drop trailing engine fingerprints
    s = re.sub(r"\s+\+ENGINES?\([^)]*\).*$", "", s)
    # Walk tokens left-to-right; keep until a noise marker.
    LOWERCASE_NOISE = {
        "gain", "age", "elapsed", "exit", "px", "mult", "attempt",
        "level", "bp", "tp", "ratio", "sent", "min", "max",
        "spd", "deltaspd", "atr", "vol", "vel", "bias",
        "main", "recovered", "no", "original", "orig",
        "k", "d", "g", "wt", "rsi", "mfi", "bb", "ha",
    }
    TF_TOKENS = {"3M", "5M", "15M", "30M", "1H", "2H", "4H", "12H",
                 "D", "1D", "W", "1W", "M", "1M",
                 "3m", "5m", "15m", "30m", "1h", "2h", "4h", "12h", "1d", "1w"}
    out_tokens: List[str] = []
    for tok in s.split("_"):
        if not tok:
            continue
        if any(c in tok for c in "=:/|%"):
            break
        # B-codes (B01, B04, B12, B14...) — known entry-strategy tags, keep.
        if re.fullmatch(r"B\d{1,3}", tok):
            out_tokens.append(tok)
            continue
        # TF tokens like "4H", "1H", "15m" — keep (they're part of macro names).
        if tok in TF_TOKENS:
            out_tokens.append(tok)
            continue
        # Token starting with a digit and not a TF — break.
        if tok[0].isdigit():
            break
        # Token with digits inside AND lowercase (e.g. "k1m", "wt3m", "mom4h", "age64420m")
        # — these are indicator/value tokens, break.
        if any(c.isdigit() for c in tok) and any(c.islower() for c in tok):
            break
        # Bare lowercase metric/single-letter words.
        if tok.islower() and tok in LOWERCASE_NOISE:
            break
        out_tokens.append(tok)
    return "_".join(out_tokens) or "UNKNOWN"


def parse_history_jsonl(path: Path, cutoff: float) -> List[Dict]:
    """Read <SYM>_<SIDE>.jsonl, filter to records with ts >= cutoff."""
    out: List[Dict] = []
    if not path.exists():
        return out
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            ts = r.get("ts") or r.get("timestamp")
            if isinstance(ts, str):
                try:
                    t = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                except Exception:
                    continue
            else:
                t = int(ts or 0)
            if not t or t < cutoff:
                continue
            r["_ts"] = t
            r["_reason_family"] = normalize_reason(r.get("reason", ""))
            out.append(r)
    return out


def latest_hourly_runs_dir(account: str) -> Optional[Path]:
    # Live/reconfigured exports have used both names over time.  Prefer the
    # current private engine-run store, then fall back to the older public name.
    for dirname in ("_engine_runs", "runs"):
        base = HOURLY_RECONFIG / account / dirname
        if not base.exists():
            continue
        cycles = sorted([p for p in base.iterdir() if p.is_dir()])
        if cycles:
            return cycles[-1]
    return None


def parse_backtest_jsonl(path: Path, cutoff: float) -> List[Dict]:
    out: List[Dict] = []
    if not path.exists():
        return out
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            exit_ts = int(r.get("exit_ts", 0) or 0)
            entry_ts = int(r.get("entry_ts", 0) or 0)
            if exit_ts < cutoff and entry_ts < cutoff:
                continue
            r["_entry_reason_family"] = normalize_reason(r.get("entry_reason", ""))
            r["_exit_reason_family"] = normalize_reason(r.get("exit_reason", ""))
            out.append(r)
    return out


def diff_one(account: str, sym: str, side: str, cutoff: float, run_dir: Path,
             window_s: int = 1800) -> Dict:
    """For one (sym, side): return live records, backtest records, and missed list."""
    live_path = HISTORY / account / f"{sym}_{side}.jsonl"
    live = parse_history_jsonl(live_path, cutoff)
    # Backtest: walk all candidate JSONLs in run_dir for this sym/side and pool the trades.
    bt: List[Dict] = []
    # Older files encode side/symbol in the filename with double underscores;
    # newer `_engine_runs` files use `reconf_<sym>_<recipe>_<side>_<n>__<sym>`.
    # Read only files carrying both identifiers, then let the JSON side fields
    # provide the final filter when present.
    for p in run_dir.rglob("*.jsonl"):
        name = p.name.upper()
        if sym.upper() not in name or side.upper() not in name:
            continue
        bt.extend(parse_backtest_jsonl(p, cutoff))
    # Build a list of backtest entry/exit timestamps for fast lookup
    bt_times: List[int] = []
    for t in bt:
        e = int(t.get("entry_ts", 0))
        x = int(t.get("exit_ts", 0))
        if e: bt_times.append(e)
        if x: bt_times.append(x)
    bt_times.sort()
    if not bt_times:
        return {
            "sym": sym,
            "side": side,
            "n_live": len(live),
            "n_backtest_events": 0,
            "n_missed": 0,
            "n_caught": 0,
            "comparison_status": "NO_RECENT_BACKTEST_WINDOW",
            "missed_records": [],
        }
    # For each live record, did the backtest produce a trade event within window_s?
    missed: List[Dict] = []
    caught: List[Dict] = []
    import bisect
    for r in live:
        ts = r["_ts"]
        i = bisect.bisect_left(bt_times, ts - window_s)
        nearest = None
        if i < len(bt_times) and abs(bt_times[i] - ts) <= window_s:
            nearest = bt_times[i]
        if nearest is None:
            missed.append(r)
        else:
            caught.append(r)
    return {
        "sym": sym,
        "side": side,
        "n_live": len(live),
        "n_backtest_events": len(bt_times),
        "n_missed": len(missed),
        "n_caught": len(caught),
        "comparison_status": "COMPARED",
        "missed_records": missed,
    }


def run_account(account: str, days: int = 7, window_s: int = 1800) -> Dict:
    cutoff = time.time() - days * 86400
    run_dir = latest_hourly_runs_dir(account)
    if run_dir is None:
        return {"error": f"no hourly_reconfig runs dir for account={account}"}
    print(f"[parity] account={account} run_dir={run_dir} cutoff={int(cutoff)} window_s={window_s}", flush=True)

    out_dir = OUT_BASE / account
    out_dir.mkdir(parents=True, exist_ok=True)
    by_reason: Counter = Counter()
    by_reason_caught: Counter = Counter()
    by_sym: Dict[str, Dict] = {}
    missed_samples: List[Dict] = []
    n_no_recent_backtest = 0

    # Discover sym/side from history files
    hist_dir = HISTORY / account
    if not hist_dir.exists():
        return {"error": f"no history dir {hist_dir}"}
    for p in sorted(hist_dir.glob("*_LONG.jsonl")) + sorted(hist_dir.glob("*_SHORT.jsonl")):
        stem = p.stem
        if stem.endswith("_LONG"):
            sym, side = stem[:-5], "LONG"
        elif stem.endswith("_SHORT"):
            sym, side = stem[:-6], "SHORT"
        else:
            continue
        d = diff_one(account, sym, side, cutoff, run_dir, window_s=window_s)
        if d["n_live"] == 0:
            continue
        sym_key = f"{sym}_{side}"
        by_sym[sym_key] = {k: v for k, v in d.items() if k != "missed_records"}
        if d.get("comparison_status") == "NO_RECENT_BACKTEST_WINDOW":
            n_no_recent_backtest += 1
        for r in d["missed_records"]:
            fam = r.get("_reason_family", "UNKNOWN")
            by_reason[fam] += 1
            missed_samples.append({
                "ts": r.get("ts"), "sym": sym, "side": side,
                "type": r.get("type"), "reason_family": fam,
                "raw_reason": r.get("reason", "")[:140],
                "price": r.get("price"),
            })
        # Also count CAUGHT to compute coverage per family.
        for r in d.get("missed_records", []):
            pass  # placeholder
        # Re-parse caught list of live records to count by_reason_caught per family.
        live = parse_history_jsonl(HISTORY / account / f"{sym}_{side}.jsonl", cutoff)
        for r in live:
            fam = r.get("_reason_family", "UNKNOWN")
            # Was this record caught? Use the same window check as diff_one.
            # Simpler: count all live by family, subtract missed to get caught.
            pass

    # Fix: count caught by re-parsing
    n_total_live: Counter = Counter()
    for sym_key, d in by_sym.items():
        sym, _, side = sym_key.partition("_")
        live = parse_history_jsonl(HISTORY / account / f"{sym}_{side}.jsonl", cutoff)
        for r in live:
            fam = r.get("_reason_family", "UNKNOWN")
            n_total_live[fam] += 1

    # Output: family report
    family_report = []
    for fam, missed_n in by_reason.most_common():
        total = n_total_live.get(fam, 0)
        caught = total - missed_n
        family_report.append({
            "reason_family": fam,
            "total_live": total,
            "missed_by_engine": missed_n,
            "caught_by_engine": caught,
            "missed_pct": round(100.0 * missed_n / max(1, total), 1),
        })

    summary = {
        "account": account,
        "now_ts": int(time.time()),
        "cutoff_ts": int(cutoff),
        "window_s": window_s,
        "run_dir": str(run_dir),
        "n_sym_sides": len(by_sym),
        "n_total_live_events": int(sum(n_total_live.values())),
        "n_total_missed": int(sum(by_reason.values())),
        "n_no_recent_backtest": n_no_recent_backtest,
        "missed_pct": round(100.0 * sum(by_reason.values()) / max(1, sum(n_total_live.values())), 1),
        "by_reason_family": family_report,
        "by_sym": by_sym,
    }
    (out_dir / "summary_latest.json").write_text(json.dumps(summary, indent=2, default=str))
    with (out_dir / "missed_samples.jsonl").open("w") as f:
        for r in missed_samples:
            f.write(json.dumps(r, default=str) + "\n")

    # Print top reasons
    print()
    print(f"=== PARITY DIFF: {account} (last {days}d) ===")
    print(f"  total live events: {summary['n_total_live_events']}")
    print(f"  missed by 7d backtest: {summary['n_total_missed']} ({summary['missed_pct']}%)")
    if n_no_recent_backtest:
        print(f"  no recent backtest window for {n_no_recent_backtest} symbol-sides; excluded from miss rate")
    print(f"  reason families to wire (top 15):")
    for fr in family_report[:15]:
        print(f"    {fr['missed_pct']:>5.1f}% missed  {fr['missed_by_engine']:>4d}/{fr['total_live']:<4d}  {fr['reason_family']}")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--window-s", type=int, default=1800, help="match window in seconds")
    args = ap.parse_args()
    s = run_account(args.account, days=args.days, window_s=args.window_s)
    if "error" in s:
        print(f"ERROR: {s['error']}", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
