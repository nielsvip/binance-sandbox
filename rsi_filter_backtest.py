#!/usr/bin/env python3
"""
RSI Filter Backtest — all accounts, last 30 days
Matches OPEN/AUGMENT entries to next CLOSE/REDUCE for same position_key.
Uses gain field from close record where available; falls back to price delta.
"""

import json
import glob
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

DATA_DIR = "/Users/niels/Documents/binance/data/decisions"
CRYPTO_ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
TRADIER_ACCOUNTS = ["trb", "trc"]
ALL_ACCOUNTS = CRYPTO_ACCOUNTS + TRADIER_ACCOUNTS
CUTOFF_DAYS = 30

# ---------------------------------------------------------------------------
# Load records
# ---------------------------------------------------------------------------

def load_records(accounts, cutoff_days=30):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=cutoff_days)
    records = []
    files_read = 0
    skipped = 0
    for acct in accounts:
        pattern = os.path.join(DATA_DIR, f"decisions_{acct}_*.jsonl")
        for fpath in sorted(glob.glob(pattern)):
            # Quick date check from filename
            fname = os.path.basename(fpath)
            try:
                date_str = fname.split("_")[-1].replace(".jsonl", "")
                file_date = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
                if file_date < cutoff - timedelta(days=1):
                    skipped += 1
                    continue
            except Exception:
                pass
            files_read += 1
            try:
                fh = open(fpath, encoding="utf-8", errors="replace")
            except Exception:
                continue
            with fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    # Parse timestamp
                    ts_str = rec.get("timestamp", "")
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                    except Exception:
                        ts = now
                    if ts < cutoff:
                        continue
                    rec["_ts"] = ts
                    rec["_acct_type"] = "tradier" if acct in TRADIER_ACCOUNTS else "crypto"
                    records.append(rec)
    print(f"  Loaded {len(records)} records from {files_read} files (skipped {skipped} old files)")
    return records


# ---------------------------------------------------------------------------
# Extract RSI and Stoch fields
# ---------------------------------------------------------------------------

def get_indicators(rec):
    """Return dict of indicator fields regardless of crypto vs tradier format."""
    snap = rec.get("snapshot") or {}
    ind = rec.get("indicators") or {}
    if not isinstance(snap, dict):
        snap = {}
    if not isinstance(ind, dict):
        ind = {}
    combined = {}
    combined.update(snap)
    combined.update(ind)
    return combined


def get_rsi(rec, field):
    ind = get_indicators(rec)
    val = ind.get(field)
    if val is None or not isinstance(val, (int, float)):
        return None
    return float(val)


def get_price(rec):
    ind = get_indicators(rec)
    p = ind.get("price") or ind.get("current_price")
    if p and isinstance(p, (int, float)) and p > 0:
        return float(p)
    return None


def get_gain(rec):
    """Return gain % from close record. Gain is fractional (0.05 = 5%)."""
    ind = get_indicators(rec)
    g = ind.get("gain")
    if g is not None and isinstance(g, (int, float)):
        return float(g) * 100.0  # convert to %
    return None


def parse_position_side(pk):
    if pk.endswith("_LONG"):
        return "LONG"
    if pk.endswith("_SHORT"):
        return "SHORT"
    # Tradier: trb:AAPL_LONG or trb:AAPL_SHORT
    if "_LONG" in pk:
        return "LONG"
    if "_SHORT" in pk:
        return "SHORT"
    return None


def is_entry_action(action):
    """True if this is an OPEN or AUGMENT entry."""
    a = action.upper()
    return ("OPEN" in a or "AUGMENT" in a or "LONG BUY" in a or "SHORT SELL" in a or "HEAVY_ART" in a)


def is_close_action(action):
    """True if this is a CLOSE or REDUCE exit."""
    a = action.upper()
    return ("CLOSE" in a or "REDUCE" in a or "SELL" in a)


# ---------------------------------------------------------------------------
# Match entries to exits
# ---------------------------------------------------------------------------

def match_trades(records):
    """
    For each entry (OPEN/AUGMENT), find the next CLOSE/REDUCE for same position_key.
    Returns list of trade dicts.
    """
    # Group by position_key
    by_pk = defaultdict(list)
    for rec in records:
        pk = rec.get("position_key", "")
        if pk:
            by_pk[pk].append(rec)

    trades = []
    for pk, recs in by_pk.items():
        recs_sorted = sorted(recs, key=lambda r: r["_ts"])
        side = parse_position_side(pk)
        if not side:
            continue
        i = 0
        while i < len(recs_sorted):
            rec = recs_sorted[i]
            action = rec.get("action", "")
            if is_entry_action(action):
                # Find next close after this entry
                entry_ts = rec["_ts"]
                entry_price = get_price(rec)
                exit_rec = None
                for j in range(i + 1, len(recs_sorted)):
                    candidate = recs_sorted[j]
                    cand_action = candidate.get("action", "")
                    if is_close_action(cand_action) and candidate["_ts"] > entry_ts:
                        exit_rec = candidate
                        break
                gain_pct = None
                if exit_rec is not None:
                    gain_pct = get_gain(exit_rec)
                    if gain_pct is None:
                        # Try computing from prices
                        exit_price = get_price(exit_rec)
                        if entry_price and exit_price and entry_price > 0:
                            if side == "LONG":
                                gain_pct = (exit_price - entry_price) / entry_price * 100.0
                            else:
                                gain_pct = (entry_price - exit_price) / entry_price * 100.0
                trade = {
                    "position_key": pk,
                    "account": rec.get("account") or rec.get("account_key", ""),
                    "acct_type": rec.get("_acct_type", "crypto"),
                    "side": side,
                    "action": action,
                    "ts": entry_ts,
                    "entry_price": entry_price,
                    "gain_pct": gain_pct,
                    "has_exit": exit_rec is not None,
                    "indicators": get_indicators(rec),
                }
                trades.append(trade)
                if exit_rec is not None:
                    # Advance past the exit to avoid re-matching
                    i = recs_sorted.index(exit_rec) + 1
                else:
                    i += 1
            else:
                i += 1
    return trades


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------

def run_filter(trades, side, condition_fn, label):
    """Apply condition to entries of given side, return stats."""
    matched = []
    for t in trades:
        if t["side"] != side:
            continue
        if not condition_fn(t):
            continue
        matched.append(t)
    # Only score trades that have an exit
    with_exit = [t for t in matched if t["has_exit"] and t["gain_pct"] is not None]
    if not with_exit:
        return {"label": label, "count": len(matched), "with_exit": 0, "avg_gain": None, "win_rate": None}
    gains = [t["gain_pct"] for t in with_exit]
    avg_gain = sum(gains) / len(gains)
    wins = sum(1 for g in gains if g > 0)
    win_rate = wins / len(gains) * 100.0
    return {
        "label": label,
        "count": len(matched),
        "with_exit": len(with_exit),
        "avg_gain": avg_gain,
        "win_rate": win_rate,
    }


def verdict(result, baseline):
    if result["avg_gain"] is None or baseline["avg_gain"] is None:
        return "N/A"
    diff = result["avg_gain"] - baseline["avg_gain"]
    wr_diff = (result["win_rate"] or 0) - (baseline["win_rate"] or 0)
    if diff > 0.05 or wr_diff > 2:
        return "BETTER"
    if diff < -0.05 or wr_diff < -2:
        return "WORSE"
    return "SAME"


def fmt_result(result, baseline=None):
    count = result["count"]
    we = result["with_exit"]
    ag = f"{result['avg_gain']:+.3f}%" if result["avg_gain"] is not None else "  N/A  "
    wr = f"{result['win_rate']:.1f}%" if result["win_rate"] is not None else " N/A "
    v = verdict(result, baseline) if baseline else "BASE"
    return f"  count={count:5d}  exits={we:5d}  avg_gain={ag}  win_rate={wr:6s}  [{v}]"


def print_table(results, baseline):
    for r in results:
        b = baseline if r is not baseline else None
        v = verdict(r, baseline) if b else "BASE"
        ag = f"{r['avg_gain']:+.3f}%" if r["avg_gain"] is not None else "  N/A  "
        wr = f"{r['win_rate']:.1f}%" if r["win_rate"] is not None else " N/A "
        line = f"  {r['label']:<55s} count={r['count']:5d}  exits={r['with_exit']:5d}  avg_gain={ag}  win_rate={wr:6s}  [{v}]"
        print(line)


# ---------------------------------------------------------------------------
# Filter definitions
# ---------------------------------------------------------------------------

def no_filter(_):
    return True


def rsi_filter(field, op, threshold):
    def fn(t):
        val = t["indicators"].get(field)
        if val is None or not isinstance(val, (int, float)):
            return False
        val = float(val)
        if op == ">":
            return val > threshold
        if op == "<":
            return val < threshold
        return False
    return fn


def stoch_filter(k_field, k_op, k_thresh, compare_kd=None):
    def fn(t):
        ind = t["indicators"]
        k = ind.get(k_field)
        if k is None or not isinstance(k, (int, float)):
            return False
        k = float(k)
        if k_op == "<=" and k > k_thresh:
            return False
        if k_op == "<" and k >= k_thresh:
            return False
        if k_op == ">" and k <= k_thresh:
            return False
        if k_op == ">=" and k < k_thresh:
            return False
        if k_op == "50-70" and not (50 <= k <= 70):
            return False
        if compare_kd:
            kf, df = compare_kd
            kv = ind.get(kf)
            dv = ind.get(df)
            if kv is None or dv is None:
                return False
            if not isinstance(kv, (int, float)) or not isinstance(dv, (int, float)):
                return False
            if not (float(kv) > float(dv)):
                return False
        return True
    return fn


def combined(fn1, fn2):
    return lambda t: fn1(t) and fn2(t)


# ---------------------------------------------------------------------------
# Run all tests for a set of trades
# ---------------------------------------------------------------------------

def run_all_tests(trades, label_prefix=""):
    sep = "=" * 110
    thin = "-" * 110

    # Detect available RSI fields
    rsi_fields_present = set()
    for t in trades:
        ind = t["indicators"]
        for k in ind:
            if "rsi" in k.lower() and ind[k] is not None:
                rsi_fields_present.add(k)
    print(f"\n  RSI fields found in this dataset: {sorted(rsi_fields_present)}")

    # Determine field name mapping
    # Crypto: no RSI fields in snapshot (stoch only)
    # Tradier: rsi_5m (proxy for short-term), rsi_15m, no rsi_1h
    # Map rsi_3m -> rsi_5m for Tradier (closest available), rsi_1h -> None for both
    rsi_3m = "rsi_3m" if "rsi_3m" in rsi_fields_present else ("rsi_5m" if "rsi_5m" in rsi_fields_present else None)
    rsi_15m = "rsi_15m" if "rsi_15m" in rsi_fields_present else None
    rsi_1h = "rsi_1h" if "rsi_1h" in rsi_fields_present else None
    # Label what we're actually using
    rsi_3m_label = "rsi_3m" if "rsi_3m" in rsi_fields_present else ("rsi_5m (proxy)" if rsi_3m else "N/A")
    print(f"  RSI field mapping: rsi_3m->{rsi_3m_label}  rsi_15m->{rsi_15m}  rsi_1h->{rsi_1h}")

    def rsi_f(field, op, thr):
        if field is None:
            return lambda t: False
        return rsi_filter(field, op, thr)

    # Use human labels that reflect actual field used
    r3_lbl = rsi_3m_label  # e.g. "rsi_3m" or "rsi_5m (proxy)"

    # --- LONGS ---
    print(f"\n{sep}")
    print(f"  LONG ENTRIES — RSI Filter Tests ({label_prefix})")
    print(sep)
    long_baseline = run_filter(trades, "LONG", no_filter, "1. No filter (baseline)")
    long_tests = [
        long_baseline,
        run_filter(trades, "LONG", rsi_f(rsi_3m, ">", 50), f"2. {r3_lbl} > 50"),
        run_filter(trades, "LONG", rsi_f(rsi_3m, "<", 50), f"3. {r3_lbl} < 50"),
        run_filter(trades, "LONG", rsi_f(rsi_15m, ">", 50), f"4. rsi_15m > 50"),
        run_filter(trades, "LONG", rsi_f(rsi_15m, "<", 50), f"5. rsi_15m < 50"),
        run_filter(trades, "LONG", rsi_f(rsi_1h, ">", 50), f"6. rsi_1h > 50"),
        run_filter(trades, "LONG", rsi_f(rsi_1h, "<", 50), f"7. rsi_1h < 50"),
        run_filter(trades, "LONG", combined(rsi_f(rsi_3m, ">", 50), rsi_f(rsi_15m, ">", 50)), f"8. {r3_lbl}>50 AND rsi_15m>50 (both agree)"),
        run_filter(trades, "LONG", rsi_f(rsi_3m, "<", 20), f"9. {r3_lbl} < 20 (oversold extreme)"),
        run_filter(trades, "LONG", rsi_f(rsi_15m, "<", 30), "10. rsi_15m < 30 (oversold HTF)"),
    ]
    print_table(long_tests, long_baseline)

    # --- SHORTS ---
    print(f"\n{sep}")
    print(f"  SHORT ENTRIES — RSI Filter Tests ({label_prefix})")
    print(sep)
    short_baseline = run_filter(trades, "SHORT", no_filter, "1. No filter (baseline)")
    short_tests = [
        short_baseline,
        run_filter(trades, "SHORT", rsi_f(rsi_3m, "<", 50), f"2. {r3_lbl} < 50"),
        run_filter(trades, "SHORT", rsi_f(rsi_3m, ">", 50), f"3. {r3_lbl} > 50"),
        run_filter(trades, "SHORT", rsi_f(rsi_15m, "<", 50), "4. rsi_15m < 50"),
        run_filter(trades, "SHORT", rsi_f(rsi_15m, ">", 50), "5. rsi_15m > 50"),
        run_filter(trades, "SHORT", rsi_f(rsi_1h, "<", 50), "6. rsi_1h < 50"),
        run_filter(trades, "SHORT", rsi_f(rsi_1h, ">", 50), "7. rsi_1h > 50"),
        run_filter(trades, "SHORT", combined(rsi_f(rsi_3m, "<", 50), rsi_f(rsi_15m, "<", 50)), f"8. {r3_lbl}<50 AND rsi_15m<50 (both agree)"),
        run_filter(trades, "SHORT", rsi_f(rsi_3m, ">", 80), f"9. {r3_lbl} > 80 (overbought extreme)"),
        run_filter(trades, "SHORT", rsi_f(rsi_15m, ">", 70), "10. rsi_15m > 70 (overbought HTF)"),
    ]
    print_table(short_tests, short_baseline)

    # --- COMBINED: k_15m + RSI ---
    print(f"\n{sep}")
    print(f"  COMBINED: Stochastic k_15m + RSI filter ({label_prefix})")
    print(sep)

    # Detect stoch field names by scanning all trades
    all_ind_keys = set()
    for t in trades:
        all_ind_keys.update(t["indicators"].keys())
    # 15m stoch
    k15_field = "stoch_k_15m" if "stoch_k_15m" in all_ind_keys else "k_15m"
    # Short-term stoch: prefer k_3m (crypto), fall back to stoch_k_5m (tradier)
    if "k_3m" in all_ind_keys:
        k3_field, d3_field = "k_3m", "d_3m"
    elif "stoch_k_5m" in all_ind_keys:
        k3_field, d3_field = "stoch_k_5m", "stoch_d_5m"
    else:
        k3_field, d3_field = "k_3m", "d_3m"

    print(f"  Stoch fields used: k15={k15_field}, k3={k3_field}, d3={d3_field}")

    def k15_le32_k3_gt_d3(t):
        ind = t["indicators"]
        k15 = ind.get(k15_field)
        k3 = ind.get(k3_field)
        d3 = ind.get(d3_field)
        if any(v is None or not isinstance(v, (int, float)) for v in [k15, k3, d3]):
            return False
        return float(k15) <= 32 and float(k3) > float(d3)

    def k15_5070_k3_gt_d3(t):
        ind = t["indicators"]
        k15 = ind.get(k15_field)
        k3 = ind.get(k3_field)
        d3 = ind.get(d3_field)
        if any(v is None or not isinstance(v, (int, float)) for v in [k15, k3, d3]):
            return False
        return 50 <= float(k15) <= 70 and float(k3) > float(d3)

    def k15_lt50_k3_lt_d3(t):
        ind = t["indicators"]
        k15 = ind.get(k15_field)
        k3 = ind.get(k3_field)
        d3 = ind.get(d3_field)
        if any(v is None or not isinstance(v, (int, float)) for v in [k15, k3, d3]):
            return False
        return float(k15) < 50 and float(k3) < float(d3)

    def k15_gt70_k3_lt_d3(t):
        ind = t["indicators"]
        k15 = ind.get(k15_field)
        k3 = ind.get(k3_field)
        d3 = ind.get(d3_field)
        if any(v is None or not isinstance(v, (int, float)) for v in [k15, k3, d3]):
            return False
        return float(k15) > 70 and float(k3) < float(d3)

    ks_lbl = k3_field  # e.g. k_3m or stoch_k_5m
    combined_tests = [
        # LONG combos
        run_filter(trades, "LONG", k15_le32_k3_gt_d3, f"LONG: k15m<=32 + {ks_lbl}>d (no RSI)"),
        run_filter(trades, "LONG", combined(k15_le32_k3_gt_d3, rsi_f(rsi_3m, ">", 50)), f"LONG: k15m<=32 + {ks_lbl}>d + {r3_lbl}>50"),
        run_filter(trades, "LONG", k15_5070_k3_gt_d3, f"LONG: k15m 50-70 + {ks_lbl}>d (no RSI)"),
        run_filter(trades, "LONG", combined(k15_5070_k3_gt_d3, rsi_f(rsi_15m, ">", 50)), f"LONG: k15m 50-70 + {ks_lbl}>d + rsi_15m>50"),
        # SHORT combos
        run_filter(trades, "SHORT", k15_lt50_k3_lt_d3, f"SHORT: k15m<50 + {ks_lbl}<d (no RSI)"),
        run_filter(trades, "SHORT", combined(k15_lt50_k3_lt_d3, rsi_f(rsi_3m, "<", 50)), f"SHORT: k15m<50 + {ks_lbl}<d + {r3_lbl}<50"),
        run_filter(trades, "SHORT", k15_gt70_k3_lt_d3, f"SHORT: k15m>70 turning down (no RSI)"),
        run_filter(trades, "SHORT", combined(k15_gt70_k3_lt_d3, rsi_f(rsi_3m, ">", 70)), f"SHORT: k15m>70 turning down + {r3_lbl}>70"),
    ]

    print(f"  (Using LONG baseline for LONG rows, SHORT baseline for SHORT rows)")
    for r in combined_tests:
        side = "LONG" if "LONG" in r["label"] else "SHORT"
        base = long_baseline if side == "LONG" else short_baseline
        v = verdict(r, base)
        ag = f"{r['avg_gain']:+.3f}%" if r["avg_gain"] is not None else "  N/A  "
        wr = f"{r['win_rate']:.1f}%" if r["win_rate"] is not None else " N/A "
        print(f"  {r['label']:<58s} count={r['count']:5d}  exits={r['with_exit']:5d}  avg_gain={ag}  win_rate={wr:6s}  [{v}]")

    return {
        "long_baseline": long_baseline,
        "long_tests": long_tests,
        "short_baseline": short_baseline,
        "short_tests": short_tests,
        "combined_tests": combined_tests,
    }


# ---------------------------------------------------------------------------
# Side-by-side comparison
# ---------------------------------------------------------------------------

def side_by_side_summary(crypto_results, tradier_results):
    sep = "=" * 140
    print(f"\n{sep}")
    print("  SIDE-BY-SIDE SUMMARY: CRYPTO vs TRADIER")
    print(sep)
    print(f"  {'Filter':<50s} {'CRYPTO avg_gain':>15s} {'CRYPTO win%':>12s} {'CRYPTO cnt':>10s} | {'TRADIER avg_gain':>15s} {'TRADIER win%':>12s} {'TRADIER cnt':>10s}")
    print("-" * 140)

    def fmt(r, base):
        if r is None:
            return "    N/A    ", "  N/A  ", "    0"
        ag = f"{r['avg_gain']:+.3f}% [{verdict(r,base)}]" if r["avg_gain"] is not None else "   N/A        "
        wr = f"{r['win_rate']:.1f}%" if r["win_rate"] is not None else "  N/A "
        cnt = str(r["with_exit"])
        return ag, wr, cnt

    # Pair up long tests
    print("  -- LONG ENTRIES --")
    cl = crypto_results["long_tests"]
    tl = tradier_results["long_tests"]
    cb = crypto_results["long_baseline"]
    tb = tradier_results["long_baseline"]
    for i in range(max(len(cl), len(tl))):
        cr = cl[i] if i < len(cl) else None
        tr = tl[i] if i < len(tl) else None
        lbl = (cr or tr)["label"] if (cr or tr) else ""
        c_ag, c_wr, c_cnt = fmt(cr, cb)
        t_ag, t_wr, t_cnt = fmt(tr, tb)
        print(f"  {lbl:<50s} {c_ag:>22s} {c_wr:>12s} {c_cnt:>10s} | {t_ag:>22s} {t_wr:>12s} {t_cnt:>10s}")

    print()
    print("  -- SHORT ENTRIES --")
    cs = crypto_results["short_tests"]
    ts = tradier_results["short_tests"]
    cb2 = crypto_results["short_baseline"]
    tb2 = tradier_results["short_baseline"]
    for i in range(max(len(cs), len(ts))):
        cr = cs[i] if i < len(cs) else None
        tr = ts[i] if i < len(ts) else None
        lbl = (cr or tr)["label"] if (cr or tr) else ""
        c_ag, c_wr, c_cnt = fmt(cr, cb2)
        t_ag, t_wr, t_cnt = fmt(tr, tb2)
        print(f"  {lbl:<50s} {c_ag:>22s} {c_wr:>12s} {c_cnt:>10s} | {t_ag:>22s} {t_wr:>12s} {t_cnt:>10s}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sep = "=" * 110
    print(sep)
    print("  RSI FILTER BACKTEST — Last 30 Days — All Accounts")
    print(f"  Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(sep)

    # Load and match trades
    print("\n[1/4] Loading CRYPTO records...")
    crypto_records = load_records(CRYPTO_ACCOUNTS)
    print("\n[2/4] Loading TRADIER records...")
    tradier_records = load_records(TRADIER_ACCOUNTS)

    print("\n[3/4] Matching entries to exits...")
    crypto_trades = match_trades(crypto_records)
    tradier_trades = match_trades(tradier_records)

    # Field inventory
    print(f"\n  CRYPTO trades matched: {len(crypto_trades)}")
    crypto_with_exit = [t for t in crypto_trades if t["has_exit"] and t["gain_pct"] is not None]
    print(f"  CRYPTO trades with gain data: {len(crypto_with_exit)}")
    print(f"  TRADIER trades matched: {len(tradier_trades)}")
    tradier_with_exit = [t for t in tradier_trades if t["has_exit"] and t["gain_pct"] is not None]
    print(f"  TRADIER trades with gain data: {len(tradier_with_exit)}")

    # Per-account breakdown
    print("\n  Trades per account:")
    all_trades = crypto_trades + tradier_trades
    acct_counts = defaultdict(lambda: {"total": 0, "with_exit": 0, "long": 0, "short": 0})
    for t in all_trades:
        a = t["account"]
        acct_counts[a]["total"] += 1
        if t["has_exit"] and t["gain_pct"] is not None:
            acct_counts[a]["with_exit"] += 1
        acct_counts[a][t["side"].lower()] += 1
    for a in sorted(acct_counts):
        d = acct_counts[a]
        print(f"    {a:6s}: total={d['total']:5d}  with_exit={d['with_exit']:5d}  long={d['long']:5d}  short={d['short']:5d}")

    print("\n[4/4] Running backtest filters...\n")

    # Crypto backtest
    print(sep)
    print("  CRYPTO ACCOUNTS (ang / inf / flz / men / fin)")
    print(sep)
    crypto_results = run_all_tests(crypto_trades, label_prefix="CRYPTO")

    # Tradier backtest
    print(f"\n{sep}")
    print("  TRADIER ACCOUNTS (trb / trc)")
    print(sep)
    tradier_results = run_all_tests(tradier_trades, label_prefix="TRADIER")

    # Side-by-side
    side_by_side_summary(crypto_results, tradier_results)

    print(f"\n{sep}")
    print("  BACKTEST COMPLETE")
    print(sep)


if __name__ == "__main__":
    main()
