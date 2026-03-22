"""
k_15m Band Analysis for LONG and SHORT Entries
================================================
Breaks down trade performance by k_15m bands (10-point increments)
for stochastic crossover entries on 1m and 3m timeframes.

Crossover detection:
- LONG: stoch_crossover = k_Xm > d_Xm AND |k-d| < 20 (bullish, fresh)
- SHORT: stoch_crossunder = k_Xm < d_Xm AND |k-d| < 20 (bearish, fresh)
"""

import json
import glob
import os
from collections import defaultdict
from datetime import datetime, timedelta

DATA_DIR = "/Users/niels/Documents/binance/data/decisions"
ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
LOOKBACK_DAYS = 14

BANDS = [
    ("<20",   lambda v: v < 20),
    ("20-30", lambda v: 20 <= v < 30),
    ("30-40", lambda v: 30 <= v < 40),
    ("40-50", lambda v: 40 <= v < 50),
    ("50-60", lambda v: 50 <= v < 60),
    ("60-70", lambda v: 60 <= v < 70),
    ("70-80", lambda v: 70 <= v < 80),
    (">80",   lambda v: v >= 80),
]


def load_decisions(accounts, lookback_days):
    cutoff = datetime.now() - timedelta(days=lookback_days)
    cutoff_str = cutoff.strftime("%Y%m%d")
    records = []
    for acct in accounts:
        pattern = os.path.join(DATA_DIR, f"decisions_{acct}_*.jsonl")
        for f in sorted(glob.glob(pattern)):
            date_part = f.split("_")[-1].replace(".jsonl", "")
            if date_part < cutoff_str:
                continue
            with open(f, errors="ignore") as fh:
                for line in fh:
                    try:
                        d = json.loads(line.strip())
                        if isinstance(d, dict):
                            d["_file_date"] = date_part
                            records.append(d)
                    except (json.JSONDecodeError, ValueError):
                        continue
    return records


def parse_side(position_key):
    if position_key.endswith("_LONG"):
        return "LONG"
    elif position_key.endswith("_SHORT"):
        return "SHORT"
    return None


OPEN_ACTIONS = {"OPEN", "QUICK_OPEN"}
CLOSE_ACTIONS = {
    "CLOSE", "REDUCE", "QUICK_CLOSE", "QUICK_REDUCE",
    "QUICK_QUICK_CLOSE", "NOW_REDUCE", "STRONG_REDUCE",
    "WEAK_REDUCE", "SCALP_REDUCE", "SCALP_PROFIT",
}


def match_trades(records):
    by_key = defaultdict(list)
    for r in records:
        pk = r.get("position_key", "")
        if pk:
            by_key[pk].append(r)
    trades = []
    for pk, events in by_key.items():
        side = parse_side(pk)
        if not side:
            continue
        events.sort(key=lambda x: x.get("timestamp", ""))
        i = 0
        while i < len(events):
            ev = events[i]
            action = ev.get("action", "")
            if action in OPEN_ACTIONS:
                snap = ev.get("snapshot", {}) or {}
                entry_price = snap.get("price") or 0
                if entry_price <= 0:
                    i += 1
                    continue
                j = i + 1
                while j < len(events):
                    nev = events[j]
                    naction = nev.get("action", "")
                    # also catch partial action strings that contain close keywords
                    is_close = naction in CLOSE_ACTIONS or any(
                        kw in naction for kw in ("CLOSE", "REDUCE", "EXIT")
                    )
                    if is_close:
                        nsnap = nev.get("snapshot", {}) or {}
                        exit_price = nsnap.get("price") or 0
                        if exit_price > 0:
                            if side == "LONG":
                                pnl_pct = (exit_price - entry_price) / entry_price * 100
                            else:
                                pnl_pct = (entry_price - exit_price) / entry_price * 100
                            trades.append({
                                "pk": pk,
                                "side": side,
                                "entry_snap": snap,
                                "entry_ts": ev.get("timestamp", ""),
                                "exit_ts": nev.get("timestamp", ""),
                                "pnl_pct": pnl_pct,
                                "entry_reason": ev.get("reason", ""),
                                "exit_reason": nev.get("reason", ""),
                            })
                        break
                    j += 1
                i = j + 1 if j < len(events) else i + 1
            else:
                i += 1
    return trades


def has_crossover(snap, tf):
    """Bullish crossover: k > d and fresh (|k-d| < 20)."""
    k = snap.get(f"k_{tf}")
    d = snap.get(f"d_{tf}")
    if k is None or d is None:
        return False
    return k > d and abs(k - d) < 20


def has_crossunder(snap, tf):
    """Bearish crossunder: k < d and fresh (|k-d| < 20)."""
    k = snap.get(f"k_{tf}")
    d = snap.get(f"d_{tf}")
    if k is None or d is None:
        return False
    return k < d and abs(k - d) < 20


def stats(pnls, label):
    if not pnls:
        return {"label": label, "n": 0, "avg": None, "wr": None}
    wins = sum(1 for p in pnls if p > 0)
    avg = sum(pnls) / len(pnls)
    wr = wins / len(pnls) * 100
    return {"label": label, "n": len(pnls), "avg": avg, "wr": wr}


def rating(avg, wr, n, min_n=10):
    if n < min_n:
        return "LOW-N"
    if avg is None:
        return "N/A"
    score = avg * 0.6 + (wr - 50) * 0.04
    if score > 0.4 and wr >= 55:
        return "BEST"
    elif score > 0.1 and wr >= 50:
        return "GOOD"
    elif score > -0.1:
        return "OK"
    else:
        return "BAD"


def print_band_table(rows, title):
    print(f"\n{'='*90}")
    print(f"  {title}")
    print(f"{'='*90}")
    hdr = f"  {'Band':<10} {'Count':>6} {'AvgGain%':>10} {'WinRate%':>10} {'Rating':>8}"
    print(hdr)
    print(f"  {'-'*10} {'-'*6} {'-'*10} {'-'*10} {'-'*8}")
    for r in rows:
        n = r["n"]
        if n == 0:
            print(f"  {r['label']:<10} {0:>6} {'---':>10} {'---':>10} {'---':>8}")
        else:
            avg_s = f"{r['avg']:+.4f}"
            wr_s = f"{r['wr']:.1f}%"
            rat = rating(r["avg"], r["wr"], n)
            print(f"  {r['label']:<10} {n:>6} {avg_s:>10} {wr_s:>10} {rat:>8}")


def print_hypothesis_table(rows, title):
    print(f"\n{'='*90}")
    print(f"  {title}")
    print(f"{'='*90}")
    print(f"  {'Hypothesis':<45} {'Count':>6} {'AvgGain%':>10} {'WinRate%':>10} {'Rating':>8}")
    print(f"  {'-'*45} {'-'*6} {'-'*10} {'-'*10} {'-'*8}")
    for r in rows:
        n = r["n"]
        if n == 0:
            print(f"  {r['label']:<45} {0:>6} {'---':>10} {'---':>10} {'---':>8}")
        else:
            avg_s = f"{r['avg']:+.4f}"
            wr_s = f"{r['wr']:.1f}%"
            rat = rating(r["avg"], r["wr"], n)
            print(f"  {r['label']:<45} {n:>6} {avg_s:>10} {wr_s:>10} {rat:>8}")


def band_analysis(trades, direction, timeframes=("1m", "3m")):
    """
    For each timeframe and each k_15m band, compute performance stats.
    direction = "LONG" or "SHORT"
    crossover_fn = function that returns True if the entry signal matches.
    """
    results = {}
    for tf in timeframes:
        if direction == "LONG":
            signal_fn = lambda snap, tf=tf: has_crossover(snap, tf)
        else:
            signal_fn = lambda snap, tf=tf: has_crossunder(snap, tf)
        # Filter to trades with the crossover signal on this TF
        signal_trades = [t for t in trades if signal_fn(t["entry_snap"])]
        # Also collect all (no signal filter) for full-range bands
        all_trades = trades
        # Per band
        band_rows = []
        for band_label, band_fn in BANDS:
            pnls = [
                t["pnl_pct"] for t in signal_trades
                if (v := t["entry_snap"].get("k_15m")) is not None and band_fn(v)
            ]
            band_rows.append(stats(pnls, band_label))
        results[(direction, tf, "signal")] = (signal_trades, band_rows)
    return results


def main():
    print("Loading decisions (14-day lookback)...")
    records = load_decisions(ACCOUNTS, LOOKBACK_DAYS)
    print(f"  Total records: {len(records)}")

    action_counts = defaultdict(int)
    for r in records:
        action_counts[r.get("action", "?")] += 1
    opens = sum(v for k, v in action_counts.items() if k in OPEN_ACTIONS)
    closes = sum(v for k, v in action_counts.items() if any(kw in k for kw in ("CLOSE", "REDUCE", "EXIT")))
    print(f"  OPEN-type actions: {opens}  |  CLOSE-type actions: {closes}")

    print("\nMatching OPEN -> CLOSE trades...")
    trades = match_trades(records)
    print(f"  Matched trades: {len(trades)}")

    long_trades = [t for t in trades if t["side"] == "LONG"]
    short_trades = [t for t in trades if t["side"] == "SHORT"]
    print(f"  LONG: {len(long_trades)}  |  SHORT: {len(short_trades)}")

    # Count how many have k_15m available
    long_with_k15 = sum(1 for t in long_trades if t["entry_snap"].get("k_15m") is not None)
    short_with_k15 = sum(1 for t in short_trades if t["entry_snap"].get("k_15m") is not None)
    print(f"  LONG with k_15m: {long_with_k15}  |  SHORT with k_15m: {short_with_k15}")

    # =========================================================================
    # SECTION 1: SHORT entries — k_15m bands, per crossunder timeframe
    # =========================================================================
    for tf in ("1m", "3m"):
        signal_shorts = [t for t in short_trades if has_crossunder(t["entry_snap"], tf)]
        all_shorts_with_k15 = [t for t in short_trades if t["entry_snap"].get("k_15m") is not None]
        rows = []
        for band_label, band_fn in BANDS:
            pnls = [
                t["pnl_pct"] for t in signal_shorts
                if (v := t["entry_snap"].get("k_15m")) is not None and band_fn(v)
            ]
            rows.append(stats(pnls, band_label))
        print_band_table(rows, f"SHORT entries — {tf} stoch_crossunder — k_15m bands")
        print(f"  (signal trades on {tf}: {len(signal_shorts)} of {len(short_trades)} total shorts)")

    # =========================================================================
    # SECTION 2: LONG entries — k_15m bands, per crossover timeframe
    # =========================================================================
    for tf in ("1m", "3m"):
        signal_longs = [t for t in long_trades if has_crossover(t["entry_snap"], tf)]
        rows = []
        for band_label, band_fn in BANDS:
            pnls = [
                t["pnl_pct"] for t in signal_longs
                if (v := t["entry_snap"].get("k_15m")) is not None and band_fn(v)
            ]
            rows.append(stats(pnls, band_label))
        print_band_table(rows, f"LONG entries — {tf} stoch_crossover — k_15m bands")
        print(f"  (signal trades on {tf}: {len(signal_longs)} of {len(long_trades)} total longs)")

    # =========================================================================
    # SECTION 3: ALL SHORT entries (no signal filter) — k_15m bands
    # (wider view, more data)
    # =========================================================================
    rows = []
    for band_label, band_fn in BANDS:
        pnls = [
            t["pnl_pct"] for t in short_trades
            if (v := t["entry_snap"].get("k_15m")) is not None and band_fn(v)
        ]
        rows.append(stats(pnls, band_label))
    print_band_table(rows, "SHORT — ALL entries (no signal filter) — k_15m bands")

    rows = []
    for band_label, band_fn in BANDS:
        pnls = [
            t["pnl_pct"] for t in long_trades
            if (v := t["entry_snap"].get("k_15m")) is not None and band_fn(v)
        ]
        rows.append(stats(pnls, band_label))
    print_band_table(rows, "LONG — ALL entries (no signal filter) — k_15m bands")

    # =========================================================================
    # SECTION 4: Hypothesis comparison
    # =========================================================================
    def hypo(pnls, label):
        return stats(pnls, label)

    # --- SHORT hypotheses ---
    def short_k15_pnls(fn):
        return [t["pnl_pct"] for t in short_trades if (v := t["entry_snap"].get("k_15m")) is not None and fn(v)]

    def short_k15_pnls_sig(fn, tf):
        return [t["pnl_pct"] for t in short_trades
                if has_crossunder(t["entry_snap"], tf)
                and (v := t["entry_snap"].get("k_15m")) is not None and fn(v)]

    hypo_short_rows = [
        hypo(short_k15_pnls(lambda v: True), "SHORT: ALL k_15m (baseline)"),
        hypo(short_k15_pnls(lambda v: v > 50), "SHORT: k_15m > 50"),
        hypo(short_k15_pnls(lambda v: v > 60), "SHORT: k_15m > 60"),
        hypo(short_k15_pnls(lambda v: v > 70), "SHORT: k_15m > 70"),
        hypo(short_k15_pnls(lambda v: v > 80), "SHORT: k_15m > 80"),
        hypo(short_k15_pnls(lambda v: v < 50), "SHORT: k_15m < 50"),
        hypo(short_k15_pnls(lambda v: v < 30), "SHORT: k_15m < 30"),
        hypo(short_k15_pnls(lambda v: 20 <= v < 50), "SHORT: k_15m 20-50"),
        hypo(short_k15_pnls(lambda v: 40 <= v < 70), "SHORT: k_15m 40-70"),
        hypo(short_k15_pnls(lambda v: 50 <= v < 80), "SHORT: k_15m 50-80"),
    ]
    print_hypothesis_table(hypo_short_rows, "Hypothesis: SHORT — k_15m threshold comparison (ALL entries)")

    hypo_short_1m_rows = [
        hypo(short_k15_pnls_sig(lambda v: True, "1m"), "SHORT 1m cross: ALL k_15m"),
        hypo(short_k15_pnls_sig(lambda v: v > 50, "1m"), "SHORT 1m cross: k_15m > 50"),
        hypo(short_k15_pnls_sig(lambda v: v > 60, "1m"), "SHORT 1m cross: k_15m > 60"),
        hypo(short_k15_pnls_sig(lambda v: v > 70, "1m"), "SHORT 1m cross: k_15m > 70"),
        hypo(short_k15_pnls_sig(lambda v: v < 30, "1m"), "SHORT 1m cross: k_15m < 30"),
        hypo(short_k15_pnls_sig(lambda v: 20 <= v < 50, "1m"), "SHORT 1m cross: k_15m 20-50"),
    ]
    print_hypothesis_table(hypo_short_1m_rows, "Hypothesis: SHORT — 1m crossunder + k_15m filter")

    hypo_short_3m_rows = [
        hypo(short_k15_pnls_sig(lambda v: True, "3m"), "SHORT 3m cross: ALL k_15m"),
        hypo(short_k15_pnls_sig(lambda v: v > 50, "3m"), "SHORT 3m cross: k_15m > 50"),
        hypo(short_k15_pnls_sig(lambda v: v > 60, "3m"), "SHORT 3m cross: k_15m > 60"),
        hypo(short_k15_pnls_sig(lambda v: v > 70, "3m"), "SHORT 3m cross: k_15m > 70"),
        hypo(short_k15_pnls_sig(lambda v: v < 30, "3m"), "SHORT 3m cross: k_15m < 30"),
        hypo(short_k15_pnls_sig(lambda v: 20 <= v < 50, "3m"), "SHORT 3m cross: k_15m 20-50"),
    ]
    print_hypothesis_table(hypo_short_3m_rows, "Hypothesis: SHORT — 3m crossunder + k_15m filter")

    # --- LONG hypotheses ---
    def long_k15_pnls(fn):
        return [t["pnl_pct"] for t in long_trades if (v := t["entry_snap"].get("k_15m")) is not None and fn(v)]

    def long_k15_pnls_sig(fn, tf):
        return [t["pnl_pct"] for t in long_trades
                if has_crossover(t["entry_snap"], tf)
                and (v := t["entry_snap"].get("k_15m")) is not None and fn(v)]

    hypo_long_rows = [
        hypo(long_k15_pnls(lambda v: True), "LONG: ALL k_15m (baseline)"),
        hypo(long_k15_pnls(lambda v: v > 50), "LONG: k_15m > 50"),
        hypo(long_k15_pnls(lambda v: v > 60), "LONG: k_15m > 60"),
        hypo(long_k15_pnls(lambda v: v > 70), "LONG: k_15m > 70"),
        hypo(long_k15_pnls(lambda v: v < 50), "LONG: k_15m < 50"),
        hypo(long_k15_pnls(lambda v: v < 30), "LONG: k_15m < 30"),
        hypo(long_k15_pnls(lambda v: v < 20), "LONG: k_15m < 20 (deep oversold)"),
        hypo(long_k15_pnls(lambda v: 20 <= v < 50), "LONG: k_15m 20-50"),
        hypo(long_k15_pnls(lambda v: 30 <= v < 70), "LONG: k_15m 30-70"),
        hypo(long_k15_pnls(lambda v: 40 <= v <= 80), "LONG: k_15m 40-80"),
    ]
    print_hypothesis_table(hypo_long_rows, "Hypothesis: LONG — k_15m threshold comparison (ALL entries)")

    hypo_long_1m_rows = [
        hypo(long_k15_pnls_sig(lambda v: True, "1m"), "LONG 1m cross: ALL k_15m"),
        hypo(long_k15_pnls_sig(lambda v: v > 50, "1m"), "LONG 1m cross: k_15m > 50"),
        hypo(long_k15_pnls_sig(lambda v: v < 50, "1m"), "LONG 1m cross: k_15m < 50"),
        hypo(long_k15_pnls_sig(lambda v: v < 30, "1m"), "LONG 1m cross: k_15m < 30"),
        hypo(long_k15_pnls_sig(lambda v: v < 20, "1m"), "LONG 1m cross: k_15m < 20"),
        hypo(long_k15_pnls_sig(lambda v: 20 <= v < 50, "1m"), "LONG 1m cross: k_15m 20-50"),
    ]
    print_hypothesis_table(hypo_long_1m_rows, "Hypothesis: LONG — 1m crossover + k_15m filter")

    hypo_long_3m_rows = [
        hypo(long_k15_pnls_sig(lambda v: True, "3m"), "LONG 3m cross: ALL k_15m"),
        hypo(long_k15_pnls_sig(lambda v: v > 50, "3m"), "LONG 3m cross: k_15m > 50"),
        hypo(long_k15_pnls_sig(lambda v: v < 50, "3m"), "LONG 3m cross: k_15m < 50"),
        hypo(long_k15_pnls_sig(lambda v: v < 30, "3m"), "LONG 3m cross: k_15m < 30"),
        hypo(long_k15_pnls_sig(lambda v: v < 20, "3m"), "LONG 3m cross: k_15m < 20"),
        hypo(long_k15_pnls_sig(lambda v: 20 <= v < 50, "3m"), "LONG 3m cross: k_15m 20-50"),
    ]
    print_hypothesis_table(hypo_long_3m_rows, "Hypothesis: LONG — 3m crossover + k_15m filter")

    # =========================================================================
    # SECTION 5: Diagnostic — k_15m distribution in data
    # =========================================================================
    print(f"\n{'='*90}")
    print("  DIAGNOSTIC: k_15m distribution across all matched trades")
    print(f"{'='*90}")
    for direction, tdlist in [("LONG", long_trades), ("SHORT", short_trades)]:
        vals = [t["entry_snap"].get("k_15m") for t in tdlist if t["entry_snap"].get("k_15m") is not None]
        if vals:
            avg_k = sum(vals) / len(vals)
            buckets = defaultdict(int)
            for v in vals:
                for bl, bfn in BANDS:
                    if bfn(v):
                        buckets[bl] += 1
                        break
            dist = "  |  ".join(f"{bl}: {buckets[bl]}" for bl, _ in BANDS)
            print(f"  {direction}: avg_k15m={avg_k:.1f}  [{dist}]  (n={len(vals)})")
        else:
            print(f"  {direction}: NO k_15m data")

    # =========================================================================
    # SECTION 6: FINAL RECOMMENDATION
    # =========================================================================
    print(f"\n{'='*90}")
    print("  FINAL RECOMMENDATION SUMMARY")
    print(f"{'='*90}")

    def best_bands(trades_list, direction_label, signal_label=None, tf=None):
        if tf:
            if direction_label == "SHORT":
                filtered = [t for t in trades_list if has_crossunder(t["entry_snap"], tf)]
            else:
                filtered = [t for t in trades_list if has_crossover(t["entry_snap"], tf)]
        else:
            filtered = trades_list
        band_stats = []
        for band_label, band_fn in BANDS:
            pnls = [
                t["pnl_pct"] for t in filtered
                if (v := t["entry_snap"].get("k_15m")) is not None and band_fn(v)
            ]
            s = stats(pnls, band_label)
            band_stats.append(s)
        ranked = sorted(
            [s for s in band_stats if s["n"] >= 5],
            key=lambda x: (x["avg"] or -99),
            reverse=True,
        )
        prefix = f"{direction_label}"
        if signal_label:
            prefix += f" ({signal_label})"
        if ranked:
            top = ranked[0]
            worst = ranked[-1]
            print(f"\n  {prefix}")
            print(f"    BEST band:  k_15m {top['label']:>6}  → avg={top['avg']:+.4f}%  wr={top['wr']:.1f}%  n={top['n']}")
            print(f"    WORST band: k_15m {worst['label']:>6}  → avg={worst['avg']:+.4f}%  wr={worst['wr']:.1f}%  n={worst['n']}")
        else:
            print(f"\n  {prefix}: insufficient data per band")

    best_bands(short_trades, "SHORT", signal_label=None, tf=None)
    best_bands(short_trades, "SHORT", signal_label="1m crossunder", tf="1m")
    best_bands(short_trades, "SHORT", signal_label="3m crossunder", tf="3m")
    best_bands(long_trades, "LONG", signal_label=None, tf=None)
    best_bands(long_trades, "LONG", signal_label="1m crossover", tf="1m")
    best_bands(long_trades, "LONG", signal_label="3m crossover", tf="3m")

    print(f"\n{'='*90}\n")


if __name__ == "__main__":
    main()
