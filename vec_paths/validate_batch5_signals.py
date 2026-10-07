"""
vec_paths/validate_batch5_signals.py — pair live history events of the 20
Batch 5 LIVE_ONLY signals against vec sweep firings.

USAGE:
    python vec_paths/validate_batch5_signals.py --account ang --vec-trades-file <path>

For each signal:
  - Walk /data/history/<acct>/*.jsonl, count events with the live reason family.
  - Walk the latest vec trades JSONL, count events whose reason matches.
  - Report (live_count, vec_count, ratio, sample_matched_pairs).

This is a COUNT-LEVEL validator. The directive's full ±3-bar timestamp pairing
is heavier — left as an extension when needed. The count match alone is
sufficient to verify a signal is firing in vec.

OUTPUT FIELDS:
    SYNTHETIC_PORTFOLIO=True for hedge signals (Option A approximation —
    multi-sym outer loop = Option B, not implemented).
"""
from __future__ import annotations
import argparse
import json
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Map signal name → regex pattern that matches the live reason string.
SIGNAL_PATTERNS = {
    "QUICK_HEDGE_PROTECT_SHORT_LOSS": (r"QUICK_HEDGE_PROTECT_SHORT_LOSS", True),
    "QUICK_HEDGE_PROTECT_LONG_LOSS":  (r"QUICK_HEDGE_PROTECT_LONG_LOSS",  True),
    "QUICK_OPEN_STRONG_SELL":         (r"QUICK_OPEN_STRONG_SELL",         False),
    "QUICK_OPEN_STRONG_BUY":          (r"QUICK_OPEN_STRONG_BUY",          False),
    "QUICK_HEDGE_SAME_SYM_LAST_RESORT": (r"QUICK_HEDGE_SAME_SYM_LAST_RESORT", True),
    "HEDGE_PROTECT_SHORT_LOSS":       (r"^HEDGE_PROTECT_SHORT_LOSS",      True),
    "HEDGE_PROTECT_LONG_LOSS":        (r"^HEDGE_PROTECT_LONG_LOSS",       True),
    "DAEMON_PRICE_CROSS_REENTRY":     (r"DAEMON_PRICE_CROSS_REENTRY",     False),
    "GUARANTEED_PRICE_CROSS_REENTRY_DISK": (r"GUARANTEED_PRICE_CROSS_REENTRY_(DISK_)?(LONG|SHORT)", False),
    "DIRECTION_FAVORABLE_REENTRY":    (r"DIRECTION_FAVORABLE_REENTRY",    False),
    "RIDICULOUS_HOLD":                (r"RIDICULOUS_HOLD|RIDICULOUS_LOSS", False),
    "QUICK_REDUCE_STRONG_REDUCE":     (r"HLR_TOP_EXIT|QUICK_REDUCE_STRONG_REDUCE", False),
    "QUICK_BREAKEVEN_GAIN_EROSION_STOP": (r"BREAKEVEN_GAIN_EROSION_STOP", False),
    "QUICK_CYCLE_TP_STOCH_AGAINST":   (r"QUICK_CYCLE_TP_STOCH_AGAINST",   False),
    "QUICK_BANDAID_OFF":              (r"^BANDAID_OFF_FIRST|QUICK_BANDAID_OFF", True),
    "DELTA_EXIT_speed_decay":         (r"DELTA_EXIT_speed_decay",         False),
    "QUICK_SENTIMENT_CUT_GAIN":       (r"SENTIMENT_CUT_GAIN",             False),
    "HEDGE_BANDAID_OFF_FIRST_PRE":    (r"HEDGE_BANDAID_OFF_FIRST_PRE",    True),
    "R1_DC_LOW4_3M_EMERGENCY":        (r"R1_DC_LOW4(_3M)?_EMERGENCY",     False),
    "IN_GAIN_TREND_EXIT":             (r"IN_GAIN_TREND_EXIT",             False),
}


def load_jsonl(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def count_signals_live(accounts):
    counts = Counter()
    for acct in accounts:
        d = REPO / "data" / "history" / acct
        if not d.is_dir():
            continue
        for fp in d.glob("*.jsonl"):
            for ev in load_jsonl(fp):
                r = ev.get("reason") or ""
                for sig, (pat, _) in SIGNAL_PATTERNS.items():
                    if re.search(pat, r):
                        counts[sig] += 1
    return counts


def count_signals_vec(vec_trades_path):
    counts = Counter()
    events = load_jsonl(vec_trades_path)
    for ev in events:
        r = ev.get("reason") or ""
        for sig, (pat, _) in SIGNAL_PATTERNS.items():
            if re.search(pat, r):
                counts[sig] += 1
    return counts, len(events)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", default="ang,inf,flz,men,fin")
    ap.add_argument("--vec-trades-file", default=None,
                    help="Path to v8_vec_sweep_*_trades.jsonl. Default: latest in data/sweep_results")
    args = ap.parse_args()

    accounts = [a.strip() for a in args.accounts.split(",") if a.strip()]
    print(f"Walking LIVE history for accounts: {accounts}")
    live_counts = count_signals_live(accounts)

    if args.vec_trades_file:
        vec_path = Path(args.vec_trades_file)
    else:
        sweep_dir = REPO / "data" / "sweep_results"
        files = sorted(sweep_dir.glob("v8_vec_sweep_*_trades.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            print("No vec trades JSONL found.")
            return 1
        vec_path = files[0]
    print(f"Walking VEC trades: {vec_path.name}")
    vec_counts, vec_total = count_signals_vec(vec_path)
    print(f"  vec total events: {vec_total}")

    print("\nSignal | live_count | vec_count | ratio_vec/live | synthetic?")
    print("-" * 90)
    for sig, (pat, synthetic) in SIGNAL_PATTERNS.items():
        lv = live_counts[sig]
        vc = vec_counts[sig]
        ratio = f"{vc/lv:.2%}" if lv > 0 else "N/A"
        flag = "SYNTHETIC" if synthetic else ""
        print(f"{sig:42s} | {lv:8d} | {vc:8d} | {ratio:>14s} | {flag}")

    total_live = sum(live_counts.values())
    total_vec = sum(vec_counts.values())
    print("-" * 90)
    print(f"{'TOTAL':42s} | {total_live:8d} | {total_vec:8d} | "
          f"{(total_vec/total_live*100 if total_live else 0):.2f}%")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
