#!/usr/bin/env python3
"""Measure post-entry outcome for every OPEN in inf history last 60 days.

For each OPEN event in data/history/inf/{SYM}_{SIDE}.jsonl in last 60 days:
  - find the next CLOSE/QUICK_CLOSE on the same position
  - compute PnL% from entry price to close price
  - compute time held
  - classify exit (QUICK_CLOSE, CLOSE, STRONG_REDUCE) and reason prefix
  - check if position was reopened within 2h (reentry) — same side vs opposite

Outputs:
  data/inf_exit_outcomes.csv
"""
import csv, json, os
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict

HIST = Path("data/history/inf")
LOOKBACK_DAYS = 60

def parse_ts(s):
    if s is None: return None
    if s.endswith("Z"): s = s[:-1] + "+00:00"
    try: return datetime.fromisoformat(s)
    except Exception: return None

def load_events(fp):
    out = []
    with open(fp) as f:
        for line in f:
            try: r = json.loads(line)
            except Exception: continue
            dtp = parse_ts(r.get("ts"))
            if dtp is None: continue
            out.append({
                "ts": int(dtp.timestamp()), "type": r.get("type",""),
                "qty": float(r.get("qty") or 0),
                "price": float(r.get("price") or 0), "reason": (r.get("reason") or "")[:80],
            })
    out.sort(key=lambda x: x["ts"])
    return out

def main():
    import time
    now_ts = int(time.time())
    cutoff = now_ts - LOOKBACK_DAYS * 86400
    rows = []
    counts = defaultdict(int)
    pnl_buckets = Counter()
    reason_pnl = defaultdict(list)

    for fp in sorted(HIST.glob("*.jsonl")):
        stem = fp.stem
        if "_" not in stem: continue
        sym, side = stem.rsplit("_", 1)
        events = load_events(fp)
        # walk through — track cumulative qty & VWAP entry price.
        # New position starts when qty crosses 0->positive.
        # Position closes when qty returns to ~0.
        qty = 0.0; cost = 0.0; first_open_ts = None; last_close_reason = ""
        for ev in events:
            t = ev["type"]; q = ev["qty"]; p = ev["price"]
            if t == "AUGMENT":
                if qty < 1e-9:
                    first_open_ts = ev["ts"]
                qty += q; cost += q * p
            elif t == "REDUCE":
                if qty < 1e-9: continue
                q_take = min(q, qty)
                avg_entry = cost / qty if qty > 0 else 0
                qty -= q_take
                cost -= q_take * avg_entry
                last_close_reason = ev["reason"]
                if qty < 1e-6 and first_open_ts and first_open_ts >= cutoff:
                    held_min = (ev["ts"] - first_open_ts) / 60
                    exit_px = p
                    if avg_entry > 0 and exit_px > 0:
                        if side == "LONG":
                            pnl_pct = (exit_px - avg_entry) / avg_entry * 100
                        else:
                            pnl_pct = (avg_entry - exit_px) / avg_entry * 100
                    else:
                        pnl_pct = None
                    reason_prefix = last_close_reason.split("_")[0] if last_close_reason else ""
                    counts["CLOSE"] += 1
                    if pnl_pct is not None:
                        bucket = (
                            "winner>3%" if pnl_pct > 3 else
                            "winner1-3%" if pnl_pct > 1 else
                            "winner0-1%" if pnl_pct > 0 else
                            "loser0-(-1)%" if pnl_pct > -1 else
                            "loser(-1)-(-3)%" if pnl_pct > -3 else
                            "loser<-3%"
                        )
                        pnl_buckets[bucket] += 1
                        reason_pnl[reason_prefix].append(pnl_pct)
                    rows.append({
                        "symbol": sym, "side": side,
                        "open_ts": datetime.fromtimestamp(first_open_ts, tz=timezone.utc).isoformat(),
                        "close_ts": datetime.fromtimestamp(ev["ts"], tz=timezone.utc).isoformat(),
                        "held_min": f"{held_min:.1f}",
                        "entry_px": f"{avg_entry:.6g}", "exit_px": f"{exit_px:.6g}",
                        "pnl_pct": f"{pnl_pct:.3f}" if pnl_pct is not None else "",
                        "close_type": "CLOSE", "close_reason": last_close_reason[:60],
                    })
                    qty = 0.0; cost = 0.0; first_open_ts = None; last_close_reason = ""

    # write csv
    with open("data/inf_exit_outcomes.csv", "w", newline="") as f:
        cols = ["symbol","side","open_ts","close_ts","held_min","entry_px","exit_px","pnl_pct","close_type","close_reason"]
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows: w.writerow(r)

    # summary
    print(f"=== INF EXIT OUTCOMES — {LOOKBACK_DAYS} days, {len(rows)} completed round-trips ===")
    if not rows:
        print("(none)"); return
    # held_min distribution
    held = sorted(float(r["held_min"]) for r in rows)
    n = len(held)
    def pct(p): return held[min(int(n*p), n-1)]
    print(f"\nHELD duration (min):  p10={pct(0.1):.0f}  p25={pct(0.25):.0f}  p50={pct(0.5):.0f}  p75={pct(0.75):.0f}  p90={pct(0.9):.0f}")
    under_5 = sum(1 for h in held if h < 5); under_15 = sum(1 for h in held if h < 15); under_60 = sum(1 for h in held if h < 60)
    print(f"  <5 min: {under_5} ({under_5/n*100:.1f}%)  <15 min: {under_15} ({under_15/n*100:.1f}%)  <60 min: {under_60} ({under_60/n*100:.1f}%)")

    print(f"\nPNL BUCKETS:")
    total_pnl_rows = sum(pnl_buckets.values())
    for b in ["winner>3%","winner1-3%","winner0-1%","loser0-(-1)%","loser(-1)-(-3)%","loser<-3%"]:
        n_b = pnl_buckets.get(b, 0)
        print(f"  {b:<20} {n_b:>4} ({n_b/total_pnl_rows*100:.1f}%)" if total_pnl_rows else "  -")

    pnls_valid = [float(r["pnl_pct"]) for r in rows if r["pnl_pct"]]
    if pnls_valid:
        import statistics as st
        print(f"\n  mean PnL: {st.mean(pnls_valid):+.3f}%   median: {st.median(pnls_valid):+.3f}%   total: {sum(pnls_valid):+.1f}%")
        winners = [p for p in pnls_valid if p > 0]; losers = [p for p in pnls_valid if p <= 0]
        print(f"  winners: {len(winners)} ({len(winners)/len(pnls_valid)*100:.1f}%) avg {st.mean(winners) if winners else 0:+.2f}%")
        print(f"  losers:  {len(losers)}  ({len(losers)/len(pnls_valid)*100:.1f}%) avg {st.mean(losers) if losers else 0:+.2f}%")

    print(f"\nTOP CLOSE-REASON PREFIXES (avg PnL per reason, >=5 samples):")
    reason_summary = []
    for reason, pnls in reason_pnl.items():
        if len(pnls) < 5: continue
        import statistics as st
        reason_summary.append((reason, len(pnls), st.mean(pnls), st.median(pnls)))
    reason_summary.sort(key=lambda r: -r[1])
    print(f"  {'reason':<26} {'count':>6} {'avg':>8} {'median':>8}")
    for r in reason_summary[:15]:
        print(f"  {r[0]:<26} {r[1]:>6} {r[2]:>+7.2f}% {r[3]:>+7.2f}%")

    # Early-close losers: held <15min and PnL <0
    early_losers = [r for r in rows if r["pnl_pct"] and float(r["held_min"]) < 15 and float(r["pnl_pct"]) < 0]
    print(f"\nEARLY-CLOSE LOSERS (held <15 min and PnL<0): {len(early_losers)}/{n} ({len(early_losers)/n*100:.1f}%)")
    reason_cnt = Counter(r["close_reason"].split("_")[0] for r in early_losers)
    for reason, cn in reason_cnt.most_common(10):
        print(f"    {reason:<26} {cn}")

if __name__ == "__main__":
    main()
