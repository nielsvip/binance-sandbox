#!/usr/bin/env python3
"""
Stochastic Filter Backtest — Crypto (ez_) + Tradier (stocks)
Tests k_15m filter conditions on OPEN→CLOSE trade pairs.
"""
import json
import os
import glob
from datetime import datetime, timezone, timedelta
from collections import defaultdict

DATA_DIR = "/Users/niels/Documents/binance/data/decisions"
CUTOFF = datetime.now(timezone.utc) - timedelta(days=30)

# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_ts(s):
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def load_records(accounts, open_actions, close_actions):
    """Load and separate OPEN/CLOSE records for given accounts."""
    opens, closes = [], []
    for acct in accounts:
        pattern = os.path.join(DATA_DIR, f"decisions_{acct}_*.jsonl")
        for fpath in sorted(glob.glob(pattern)):
            try:
                with open(fpath) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        rec = json.loads(line)
                        ts = parse_ts(rec.get("timestamp", ""))
                        if ts is None or ts < CUTOFF:
                            continue
                        action = rec.get("action", "")
                        if action in open_actions:
                            opens.append(rec)
                        elif action in close_actions:
                            closes.append(rec)
            except Exception:
                pass
    return opens, closes


def match_trades(opens, closes, price_field_open, price_field_close):
    """
    Match OPEN→CLOSE pairs by position_key.
    Returns list of dicts with entry/exit info + stoch fields.
    """
    # Sort by timestamp
    opens_sorted = sorted(opens, key=lambda r: r.get("timestamp", ""))
    closes_sorted = sorted(closes, key=lambda r: r.get("timestamp", ""))

    # Group closes by position_key
    closes_by_key = defaultdict(list)
    for rec in closes_sorted:
        pk = rec.get("position_key") or rec.get("position_key", "")
        closes_by_key[pk].append(rec)

    trades = []
    # Track which close record index we've consumed
    closes_ptr = defaultdict(int)

    for open_rec in opens_sorted:
        pk = open_rec.get("position_key", "")
        if not pk:
            continue
        is_long = pk.endswith("_LONG")
        open_ts = open_rec.get("timestamp", "")
        open_price = _get_price(open_rec, price_field_open)
        if open_price is None or open_price <= 0:
            continue
        k15 = _get_stoch(open_rec, "k_15m")
        d15 = _get_stoch(open_rec, "d_15m")
        k3 = _get_stoch(open_rec, "k_3m") or _get_stoch(open_rec, "stoch_k_3m")
        d3 = _get_stoch(open_rec, "d_3m") or _get_stoch(open_rec, "stoch_d_3m")
        k5 = _get_stoch(open_rec, "stoch_k_5m")
        d5 = _get_stoch(open_rec, "stoch_d_5m")
        if k15 is None:
            continue

        # Find next close for this position_key after open_ts
        clist = closes_by_key[pk]
        ptr = closes_ptr[pk]
        found_close = None
        for i in range(ptr, len(clist)):
            c = clist[i]
            if c.get("timestamp", "") > open_ts:
                found_close = c
                closes_ptr[pk] = i + 1
                break
        if found_close is None:
            continue

        close_price = _get_price(found_close, price_field_close)
        if close_price is None or close_price <= 0:
            continue

        # Try to use gain field from close record
        gain_pct = _get_gain(found_close)
        if gain_pct is None:
            if is_long:
                gain_pct = (close_price - open_price) / open_price * 100
            else:
                gain_pct = (open_price - close_price) / open_price * 100

        trades.append({
            "position_key": pk,
            "is_long": is_long,
            "open_ts": open_ts,
            "open_price": open_price,
            "close_price": close_price,
            "gain_pct": gain_pct,
            "k_15m": k15,
            "d_15m": d15,
            "k_3m": k3,
            "d_3m": d3,
            "k_5m": k5,
            "d_5m": d5,
        })
    return trades


def _get_price(rec, field):
    """Extract price from record, checking multiple locations."""
    # Direct field
    v = rec.get(field)
    if v is not None:
        return float(v)
    # snapshot sub-dict (crypto)
    snap = rec.get("snapshot", {})
    if snap.get("price") is not None:
        return float(snap["price"])
    # indicators sub-dict (tradier)
    ind = rec.get("indicators", {})
    if ind.get("current_price") is not None:
        return float(ind["current_price"])
    return None


def _get_stoch(rec, key):
    """Extract stochastic value, checking snapshot and indicators."""
    # Try direct
    v = rec.get(key)
    if v is not None:
        try:
            return float(v)
        except Exception:
            pass
    # snapshot
    snap = rec.get("snapshot", {})
    v = snap.get(key)
    if v is not None:
        try:
            return float(v)
        except Exception:
            pass
    # indicators (tradier uses stoch_k_15m)
    ind = rec.get("indicators", {})
    # Map short key names
    mapping = {
        "k_15m": "stoch_k_15m",
        "d_15m": "stoch_d_15m",
        "k_3m": "stoch_k_3m",
        "d_3m": "stoch_d_3m",
        "k_5m": "stoch_k_5m",
        "d_5m": "stoch_d_5m",
    }
    alt = mapping.get(key, key)
    v = ind.get(alt) or ind.get(key)
    if v is not None:
        try:
            return float(v)
        except Exception:
            pass
    return None


def _get_gain(rec):
    """Try to extract a pre-computed gain field."""
    for key in ("gain", "gain_pct", "pnl_pct", "realized_pnl_pct"):
        v = rec.get(key)
        if v is not None:
            try:
                return float(v) * (100 if abs(float(v)) < 5 else 1)
            except Exception:
                pass
    return None


# ── Filter conditions ─────────────────────────────────────────────────────────

def apply_filters(trades, system):
    """Apply all filter conditions and return stats per condition."""
    longs = [t for t in trades if t["is_long"]]
    shorts = [t for t in trades if not t["is_long"]]

    # For shorts and longs, we need k_15m_prev approximation
    # Build a per-symbol sorted open list for prev lookup
    by_sym = defaultdict(list)
    for t in trades:
        sym = t["position_key"].split(":")[1] if ":" in t["position_key"] else t["position_key"]
        by_sym[sym].append(t)
    for sym in by_sym:
        by_sym[sym].sort(key=lambda x: x["open_ts"])

    def get_prev_k15(trade):
        sym = trade["position_key"].split(":")[1] if ":" in trade["position_key"] else trade["position_key"]
        sym_trades = by_sym[sym]
        ts = trade["open_ts"]
        prev = None
        for t in sym_trades:
            if t["open_ts"] >= ts:
                break
            # Within 30 min
            try:
                dt1 = parse_ts(ts)
                dt2 = parse_ts(t["open_ts"])
                if dt1 and dt2 and abs((dt1 - dt2).total_seconds()) <= 1800:
                    prev = t["k_15m"]
            except Exception:
                pass
        return prev

    def get_htf_pair(trade, system):
        """Return (k, d) for short-timeframe: 3m for crypto, 5m for tradier."""
        if system == "tradier":
            return trade.get("k_5m"), trade.get("d_5m")
        return trade.get("k_3m"), trade.get("d_3m")

    long_conditions = [
        ("No filter (baseline)", lambda t: True),
        ("k_15m > 50 (HTF bullish)", lambda t: t["k_15m"] > 50),
        ("k_15m < 20 & turning up", lambda t: (
            t["k_15m"] < 20 and (
                (get_prev_k15(t) is not None and t["k_15m"] > get_prev_k15(t)) or
                (get_htf_pair(t, system)[0] is not None and get_htf_pair(t, system)[1] is not None and
                 get_htf_pair(t, system)[0] > get_htf_pair(t, system)[1])
            )
        )),
        ("k_15m < 30 (deeply oversold)", lambda t: t["k_15m"] < 30),
        ("k_15m 20-50 (mid range)", lambda t: 20 <= t["k_15m"] <= 50),
    ]

    short_conditions = [
        ("No filter (baseline)", lambda t: True),
        ("k_15m > 50 (HTF bullish = exhaustion)", lambda t: t["k_15m"] > 50),
        ("k_15m > 80 & turning down", lambda t: (
            t["k_15m"] > 80 and (
                (get_prev_k15(t) is not None and t["k_15m"] < get_prev_k15(t)) or
                (get_htf_pair(t, system)[0] is not None and get_htf_pair(t, system)[1] is not None and
                 get_htf_pair(t, system)[0] < get_htf_pair(t, system)[1])
            )
        )),
        ("k_15m < 50 (conventional bearish)", lambda t: t["k_15m"] < 50),
        ("k_15m 50-80 (mid-high zone)", lambda t: 50 <= t["k_15m"] <= 80),
    ]

    results = {}
    for label, cond in long_conditions:
        subset = [t for t in longs if cond(t)]
        results[f"LONG | {label}"] = stats(subset)
    for label, cond in short_conditions:
        subset = [t for t in shorts if cond(t)]
        results[f"SHORT | {label}"] = stats(subset)
    return results


def stats(trades):
    if not trades:
        return {"n": 0, "win_rate": None, "avg_gain": None, "total_gain": None}
    gains = [t["gain_pct"] for t in trades]
    wins = [g for g in gains if g > 0]
    return {
        "n": len(gains),
        "win_rate": len(wins) / len(gains) * 100,
        "avg_gain": sum(gains) / len(gains),
        "total_gain": sum(gains),
    }


def bucket_analysis(trades, system):
    """Bucket trades by k_15m level in 10-point increments."""
    buckets = defaultdict(list)
    for t in trades:
        k = t["k_15m"]
        b = int(k // 10) * 10
        b = min(b, 90)
        buckets[b].append(t["gain_pct"])
    result = {}
    for b in range(0, 100, 10):
        gains = buckets[b]
        if gains:
            wins = [g for g in gains if g > 0]
            result[f"{b}-{b+10}"] = {
                "n": len(gains),
                "win_rate": len(wins) / len(gains) * 100,
                "avg_gain": sum(gains) / len(gains),
            }
        else:
            result[f"{b}-{b+10}"] = {"n": 0, "win_rate": None, "avg_gain": None}
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def fmt(val, fmt_str=".2f", fallback="N/A"):
    if val is None:
        return fallback
    return f"{val:{fmt_str}}"


def print_comparison_table(crypto_res, tradier_res):
    conditions_long = [
        "LONG | No filter (baseline)",
        "LONG | k_15m > 50 (HTF bullish)",
        "LONG | k_15m < 20 & turning up",
        "LONG | k_15m < 30 (deeply oversold)",
        "LONG | k_15m 20-50 (mid range)",
    ]
    conditions_short = [
        "SHORT | No filter (baseline)",
        "SHORT | k_15m > 50 (HTF bullish = exhaustion)",
        "SHORT | k_15m > 80 & turning down",
        "SHORT | k_15m < 50 (conventional bearish)",
        "SHORT | k_15m 50-80 (mid-high zone)",
    ]
    all_conditions = conditions_long + conditions_short

    hdr = f"{'Condition':<45} {'Crypto N':>8} {'Win%':>6} {'Avg%':>7} | {'Tradier N':>9} {'Win%':>6} {'Avg%':>7} | {'Agree?':>8}"
    print(hdr)
    print("-" * len(hdr))
    for cond in all_conditions:
        cr = crypto_res.get(cond, {})
        tr = tradier_res.get(cond, {})
        c_n = cr.get("n", 0)
        t_n = tr.get("n", 0)
        c_win = fmt(cr.get("win_rate"))
        t_win = fmt(tr.get("win_rate"))
        c_avg = fmt(cr.get("avg_gain"))
        t_avg = fmt(tr.get("avg_gain"))

        # Agreement: both better than baseline or both worse
        c_base_key = cond.split("|")[0].strip() + " | No filter (baseline)"
        c_base = crypto_res.get(c_base_key, {}).get("avg_gain")
        t_base = tradier_res.get(c_base_key, {}).get("avg_gain")
        agree = "N/A"
        if c_base is not None and t_base is not None and cr.get("avg_gain") is not None and tr.get("avg_gain") is not None:
            c_better = cr["avg_gain"] > c_base
            t_better = tr["avg_gain"] > t_base
            agree = "AGREE" if c_better == t_better else "DISAGREE"

        label = cond.replace("LONG | ", "L: ").replace("SHORT | ", "S: ")
        print(f"{label:<45} {c_n:>8} {c_win:>6} {c_avg:>7} | {t_n:>9} {t_win:>6} {t_avg:>7} | {agree:>8}")


def print_bucket_table(crypto_buckets, tradier_buckets, side="ALL"):
    print(f"\n{'k_15m Range':<12} {'Crypto N':>9} {'Win%':>6} {'Avg%':>7} | {'Tradier N':>10} {'Win%':>6} {'Avg%':>7}")
    print("-" * 65)
    for b_label in [f"{b}-{b+10}" for b in range(0, 100, 10)]:
        cr = crypto_buckets.get(b_label, {})
        tr = tradier_buckets.get(b_label, {})
        print(f"{b_label:<12} {cr.get('n',0):>9} {fmt(cr.get('win_rate')):>6} {fmt(cr.get('avg_gain')):>7} | {tr.get('n',0):>10} {fmt(tr.get('win_rate')):>6} {fmt(tr.get('avg_gain')):>7}")


def main():
    print("=" * 80)
    print("STOCHASTIC FILTER BACKTEST — Last 30 Days")
    print(f"Data from: {CUTOFF.strftime('%Y-%m-%d')} to now")
    print("=" * 80)

    # ── CRYPTO ───────────────────────────────────────────────────────────────
    crypto_accounts = ["ang", "inf", "flz", "men", "fin"]
    crypto_open_actions = {"OPEN", "QUICK_OPEN", "REENTRY"}
    crypto_close_actions = {"CLOSE", "QUICK_CLOSE", "SCALP_PROFIT"}

    print("\n[1/4] Loading crypto records...")
    c_opens, c_closes = load_records(crypto_accounts, crypto_open_actions, crypto_close_actions)
    print(f"  Opens: {len(c_opens):,}  Closes: {len(c_closes):,}")

    print("[2/4] Matching crypto trade pairs...")
    crypto_trades = match_trades(c_opens, c_closes, "price", "price")
    print(f"  Matched pairs: {len(crypto_trades):,}")
    if crypto_trades:
        longs = sum(1 for t in crypto_trades if t["is_long"])
        shorts = len(crypto_trades) - longs
        print(f"  Longs: {longs}  Shorts: {shorts}")
        gains = [t["gain_pct"] for t in crypto_trades]
        print(f"  Avg gain: {sum(gains)/len(gains):.2f}%  Win rate: {sum(1 for g in gains if g>0)/len(gains)*100:.1f}%")

    crypto_results = apply_filters(crypto_trades, "crypto")

    # ── TRADIER ──────────────────────────────────────────────────────────────
    tradier_accounts = ["trb", "trc"]
    tradier_open_actions = {"LONG BUY", "SHORT SELL"}
    tradier_close_actions = {"💥CLOSE", "💤CLOSE"}

    print("\n[3/4] Loading Tradier records...")
    t_opens, t_closes = load_records(tradier_accounts, tradier_open_actions, tradier_close_actions)
    print(f"  Opens: {len(t_opens):,}  Closes: {len(t_closes):,}")

    print("[4/4] Matching Tradier trade pairs...")
    tradier_trades = match_trades(t_opens, t_closes, "current_price", "current_price")
    print(f"  Matched pairs: {len(tradier_trades):,}")
    if tradier_trades:
        longs = sum(1 for t in tradier_trades if t["is_long"])
        shorts = len(tradier_trades) - longs
        print(f"  Longs: {longs}  Shorts: {shorts}")
        gains = [t["gain_pct"] for t in tradier_trades]
        print(f"  Avg gain: {sum(gains)/len(gains):.2f}%  Win rate: {sum(1 for g in gains if g>0)/len(gains)*100:.1f}%")

    tradier_results = apply_filters(tradier_trades, "tradier")

    # ── COMPARISON TABLE ─────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SIDE-BY-SIDE FILTER COMPARISON")
    print("=" * 80)
    print_comparison_table(crypto_results, tradier_results)

    # ── BUCKET ANALYSIS ──────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("K_15M BUCKET ANALYSIS — ALL TRADES (LONG + SHORT COMBINED)")
    print("=" * 80)
    c_buckets = bucket_analysis(crypto_trades, "crypto")
    t_buckets = bucket_analysis(tradier_trades, "tradier")
    print_bucket_table(c_buckets, t_buckets)

    print("\n" + "=" * 80)
    print("K_15M BUCKET ANALYSIS — LONGS ONLY")
    print("=" * 80)
    c_longs = [t for t in crypto_trades if t["is_long"]]
    t_longs = [t for t in tradier_trades if t["is_long"]]
    print_bucket_table(bucket_analysis(c_longs, "crypto"), bucket_analysis(t_longs, "tradier"), "LONG")

    print("\n" + "=" * 80)
    print("K_15M BUCKET ANALYSIS — SHORTS ONLY")
    print("=" * 80)
    c_shorts = [t for t in crypto_trades if not t["is_long"]]
    t_shorts = [t for t in tradier_trades if not t["is_long"]]
    print_bucket_table(bucket_analysis(c_shorts, "crypto"), bucket_analysis(t_shorts, "tradier"), "SHORT")

    # ── DETAILED CONDITION STATS ─────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("DETAILED CONDITION BREAKDOWN")
    print("=" * 80)
    for label, res in sorted(crypto_results.items()):
        trad = tradier_results.get(label, {})
        print(f"\n{label}")
        print(f"  Crypto:  n={res['n']:4d}  win={fmt(res['win_rate'])}%  avg={fmt(res['avg_gain'])}%  total={fmt(res['total_gain'], '.1f')}%")
        print(f"  Tradier: n={trad.get('n',0):4d}  win={fmt(trad.get('win_rate'))}%  avg={fmt(trad.get('avg_gain'))}%  total={fmt(trad.get('total_gain'), '.1f')}%")

    # ── CONCLUSION ───────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("CONCLUSION")
    print("=" * 80)

    def conclude_filter(system_label, results, trades):
        if not trades:
            print(f"\n{system_label}: Insufficient data.")
            return
        baseline_long = results.get("LONG | No filter (baseline)", {})
        baseline_short = results.get("SHORT | No filter (baseline)", {})
        bl_avg = baseline_long.get("avg_gain")
        bs_avg = baseline_short.get("avg_gain")
        print(f"\n{system_label} — {len(trades)} matched trades")
        print(f"  Baseline LONG avg: {fmt(bl_avg)}%   Baseline SHORT avg: {fmt(bs_avg)}%")

        long_conds = [k for k in results if k.startswith("LONG |") and "baseline" not in k]
        short_conds = [k for k in results if k.startswith("SHORT |") and "baseline" not in k]

        best_long = max(long_conds, key=lambda k: results[k].get("avg_gain") or -999, default=None)
        best_short = max(short_conds, key=lambda k: results[k].get("avg_gain") or -999, default=None)
        if best_long:
            bl = results[best_long]
            print(f"  Best LONG condition: '{best_long.replace('LONG | ', '')}' → avg={fmt(bl.get('avg_gain'))}% (n={bl['n']})")
        if best_short:
            bs = results[best_short]
            print(f"  Best SHORT condition: '{best_short.replace('SHORT | ', '')}' → avg={fmt(bs.get('avg_gain'))}% (n={bs['n']})")

        # k_15m helpfulness for longs
        buckets = bucket_analysis([t for t in trades if t["is_long"]], "")
        low_perf = [b for b in ["0-10", "10-20", "20-30"] if buckets[b]["avg_gain"] is not None]
        high_perf = [b for b in ["70-80", "80-90", "90-100"] if buckets[b]["avg_gain"] is not None]
        if low_perf and high_perf:
            low_avg = sum(buckets[b]["avg_gain"] for b in low_perf) / len(low_perf)
            high_avg = sum(buckets[b]["avg_gain"] for b in high_perf) / len(high_perf)
            verdict = "OVERSOLD IS BETTER for longs" if low_avg > high_avg else "OVERBOUGHT is better for longs (trend following)"
            print(f"  k_15m low zone avg: {low_avg:.2f}%  high zone avg: {high_avg:.2f}%  → {verdict}")

    conclude_filter("CRYPTO (ez_)", crypto_results, crypto_trades)
    conclude_filter("TRADIER (stocks)", tradier_results, tradier_trades)

    # Agreement summary
    print("\n─ Agreement Summary ─")
    agree_count = disagree_count = na_count = 0
    conditions_long = [
        "LONG | k_15m > 50 (HTF bullish)",
        "LONG | k_15m < 20 & turning up",
        "LONG | k_15m < 30 (deeply oversold)",
        "LONG | k_15m 20-50 (mid range)",
    ]
    conditions_short = [
        "SHORT | k_15m > 50 (HTF bullish = exhaustion)",
        "SHORT | k_15m > 80 & turning down",
        "SHORT | k_15m < 50 (conventional bearish)",
        "SHORT | k_15m 50-80 (mid-high zone)",
    ]
    bl = crypto_results.get("LONG | No filter (baseline)", {}).get("avg_gain")
    bs = crypto_results.get("SHORT | No filter (baseline)", {}).get("avg_gain")
    tbl = tradier_results.get("LONG | No filter (baseline)", {}).get("avg_gain")
    tbs = tradier_results.get("SHORT | No filter (baseline)", {}).get("avg_gain")
    for cond in conditions_long:
        c = crypto_results.get(cond, {}).get("avg_gain")
        t = tradier_results.get(cond, {}).get("avg_gain")
        if c is None or t is None or bl is None or tbl is None:
            na_count += 1
            continue
        c_better = c > bl
        t_better = t > tbl
        if c_better == t_better:
            agree_count += 1
        else:
            disagree_count += 1
            print(f"  DISAGREE: {cond} — Crypto {'better' if c_better else 'worse'}, Tradier {'better' if t_better else 'worse'}")
    for cond in conditions_short:
        c = crypto_results.get(cond, {}).get("avg_gain")
        t = tradier_results.get(cond, {}).get("avg_gain")
        if c is None or t is None or bs is None or tbs is None:
            na_count += 1
            continue
        c_better = c > bs
        t_better = t > tbs
        if c_better == t_better:
            agree_count += 1
        else:
            disagree_count += 1
            print(f"  DISAGREE: {cond} — Crypto {'better' if c_better else 'worse'}, Tradier {'better' if t_better else 'worse'}")
    print(f"\n  Agreement: {agree_count}  Disagree: {disagree_count}  N/A: {na_count}")
    if agree_count + disagree_count > 0:
        pct = agree_count / (agree_count + disagree_count) * 100
        print(f"  Systems agree {pct:.0f}% of the time on filter direction.")


if __name__ == "__main__":
    main()
