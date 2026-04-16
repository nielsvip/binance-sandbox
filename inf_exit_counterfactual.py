#!/usr/bin/env python3
"""Per-trigger 'what if we had held' counterfactual for inf exits.

For each completed inf round-trip in last 60 days (from history jsonl,
VWAP-aware), pull the FULL exit reason from decisions_inf_*.jsonl,
classify by trigger prefix, then compute counterfactual PnL if the
position had been held +2h / +6h / +24h after the actual close, using
NPZ 3m close prices.

Output: data/inf_exit_counterfactual.csv  +  per-trigger summary table.

Use this to identify which exit triggers cut winners short:
  if avg_held_+6h > avg_actual_pnl by a lot, the trigger is over-firing.
"""
import csv, json, time
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict
import numpy as np

HIST = Path("data/history/inf")
DEC = Path("data/decisions")
NPZ_DIR = Path("backtest_v5/indicators_3m")
LOOKBACK_DAYS = 60
HORIZONS_HRS = [2, 6, 24]

def parse_ts(s):
    if not s: return None
    if s.endswith("Z"): s = s[:-1] + "+00:00"
    try: return datetime.fromisoformat(s)
    except Exception: return None

# ---------- 1. Price lookup — NPZ (48 syms, 3m) + klines_cache fallback (470 syms, 15m) ----------
KCACHE = Path("klines_cache")
_npz_cache = {}
_kcache = {}
def _load_kcache(sym):
    """Load 15m klines as (ts_sec_array, close_array)."""
    fp = KCACHE / f"{sym}_15m.json"
    if not fp.exists(): return None
    try:
        with open(fp) as f: d = json.load(f)
    except Exception: return None
    if not isinstance(d, list) or not d: return None
    ts = []; close = []
    for bar in d:
        try:
            tstr = bar["timestamp"]
            if tstr.endswith("Z"): tstr = tstr[:-1] + "+00:00"
            ts.append(int(datetime.fromisoformat(tstr).timestamp()))
            close.append(float(bar["close"]))
        except Exception: continue
    if not ts: return None
    return (np.array(ts, dtype=np.int64), np.array(close, dtype=np.float64))

def npz_lookup(sym, ts_sec):
    """Return close price at-or-just-before ts_sec, prefer NPZ 3m, fallback to klines 15m."""
    if sym not in _npz_cache:
        fp = NPZ_DIR / f"{sym}.npz"
        if not fp.exists():
            _npz_cache[sym] = None
        else:
            d = np.load(fp, allow_pickle=True)
            ts = d["timestamps"].astype(np.int64)
            if ts[-1] > 1e12: ts = ts // 1000
            _npz_cache[sym] = (ts, d["close_3m"].astype(np.float64))
    p = _npz_cache[sym]
    if p is not None:
        ts, close = p
        i = int(np.searchsorted(ts, ts_sec))
        if i < len(ts): return float(close[i])
    if sym not in _kcache:
        _kcache[sym] = _load_kcache(sym)
    p = _kcache[sym]
    if p is None: return None
    ts, close = p
    i = int(np.searchsorted(ts, ts_sec))
    if i >= len(ts): return None
    return float(close[i])

# ---------- 2. Build round-trip list from history (VWAP) ----------
def build_round_trips():
    cutoff = int(time.time()) - LOOKBACK_DAYS * 86400
    trips = []
    for fp in sorted(HIST.glob("*.jsonl")):
        stem = fp.stem
        if "_" not in stem: continue
        sym, side = stem.rsplit("_", 1)
        events = []
        with open(fp, "rb") as f:
            for raw in f:
                try: r = json.loads(raw.decode("utf-8", errors="replace"))
                except Exception: continue
                dtp = parse_ts(r.get("ts"))
                if dtp is None: continue
                events.append({
                    "ts": int(dtp.timestamp()), "type": r.get("type",""),
                    "qty": float(r.get("qty") or 0), "price": float(r.get("price") or 0),
                    "reason": (r.get("reason") or ""),
                })
        events.sort(key=lambda x: x["ts"])
        qty = 0.0; cost = 0.0; first_open_ts = None; last_reason = ""
        for ev in events:
            t = ev["type"]; q = ev["qty"]; p = ev["price"]
            if t == "AUGMENT":
                if qty < 1e-9: first_open_ts = ev["ts"]
                qty += q; cost += q * p
            elif t == "REDUCE":
                if qty < 1e-9: continue
                q_take = min(q, qty)
                avg_entry = cost / qty if qty > 0 else 0
                qty -= q_take; cost -= q_take * avg_entry
                last_reason = ev["reason"]
                if qty < 1e-6 and first_open_ts and first_open_ts >= cutoff:
                    trips.append({
                        "symbol": sym, "side": side,
                        "open_ts": first_open_ts, "close_ts": ev["ts"],
                        "avg_entry": avg_entry, "exit_px": p,
                        "hist_reason": last_reason,
                    })
                    qty = 0.0; cost = 0.0; first_open_ts = None
    return trips

# ---------- 3. Load full close reasons from decisions ----------
def load_decision_reasons():
    """Index {(account, position_key, ts_rounded_to_min): full_reason}"""
    idx = {}
    for fp in sorted(DEC.glob("decisions_inf_*.jsonl")):
        with open(fp, "rb") as f:
            for raw in f:
                try: r = json.loads(raw.decode("utf-8", errors="replace"))
                except Exception: continue
                a = r.get("action","")
                if a not in ("QUICK_CLOSE","CLOSE","STRONG_REDUCE","REDUCE","QUICK_REDUCE","QUICK_QUICK_CLOSE","LIQUIDATE"): continue
                pk = r.get("position_key","")
                ts_str = r.get("timestamp"); dtp = parse_ts(ts_str)
                if dtp is None: continue
                ts_min = int(dtp.timestamp()) // 60
                # store the LATEST/most-detailed reason for this minute bucket
                key = (pk, ts_min)
                cur = idx.get(key, "")
                new = r.get("reason","") or ""
                if len(new) > len(cur): idx[key] = new
    return idx

def lookup_reason(idx, sym, side, close_ts):
    pk = f"inf:{sym}_{side}"
    ts_min = close_ts // 60
    # widen window to ±10 min to catch async write-delay between exchange fill and decision log
    best = ""
    for delta in range(-10, 11):
        r = idx.get((pk, ts_min + delta))
        if r and len(r) > len(best): best = r
    return best

def reason_prefix(reason):
    """Take 3-token prefix of reason; ignore trailing numeric/qty fragments."""
    if not reason: return "(none)"
    tok = reason.split("_")
    # drop tokens that look like numeric values
    keep = [t for t in tok[:5] if not (t.replace(".","").replace("-","").isdigit() or "=" in t)]
    return "_".join(keep[:3]) if keep else reason[:30]

# ---------- 4. Counterfactual PnL ----------
def cf_pnl(side, avg_entry, ts_sec_offset_close, sym, hours):
    """PnL if held until close_ts + hours."""
    px = npz_lookup(sym, ts_sec_offset_close + hours * 3600)
    if px is None or avg_entry <= 0: return None
    if side == "LONG":
        return (px - avg_entry) / avg_entry * 100
    else:
        return (avg_entry - px) / avg_entry * 100

def main():
    print("Building round-trips from history jsonl...")
    trips = build_round_trips()
    print(f"  {len(trips)} round-trips in last {LOOKBACK_DAYS} days")
    print("Indexing decision reasons...")
    reason_idx = load_decision_reasons()
    print(f"  {len(reason_idx)} indexed close events")

    # Try counterfactual for ALL trips — npz_lookup falls back to klines_cache 15m
    print(f"  trying counterfactual on all {len(trips)} trips (NPZ first, klines_cache fallback)")
    rows = []
    skipped_no_data = 0
    for t in trips:
        full_reason = lookup_reason(reason_idx, t["symbol"], t["side"], t["close_ts"])
        if not full_reason: full_reason = t["hist_reason"]
        prefix = reason_prefix(full_reason)
        if t["avg_entry"] <= 0 or t["exit_px"] <= 0: continue
        if t["side"] == "LONG":
            actual_pnl = (t["exit_px"] - t["avg_entry"]) / t["avg_entry"] * 100
        else:
            actual_pnl = (t["avg_entry"] - t["exit_px"]) / t["avg_entry"] * 100
        cf = {h: cf_pnl(t["side"], t["avg_entry"], t["close_ts"], t["symbol"], h) for h in HORIZONS_HRS}
        if all(v is None for v in cf.values()):
            skipped_no_data += 1; continue
        held_min = (t["close_ts"] - t["open_ts"]) / 60
        rows.append({
            "symbol": t["symbol"], "side": t["side"],
            "open_ts": datetime.fromtimestamp(t["open_ts"], tz=timezone.utc).isoformat(),
            "close_ts": datetime.fromtimestamp(t["close_ts"], tz=timezone.utc).isoformat(),
            "held_min": round(held_min, 1),
            "avg_entry": t["avg_entry"], "exit_px": t["exit_px"],
            "actual_pnl": round(actual_pnl, 4),
            "pnl_+2h": round(cf[2], 4) if cf[2] is not None else "",
            "pnl_+6h": round(cf[6], 4) if cf[6] is not None else "",
            "pnl_+24h": round(cf[24], 4) if cf[24] is not None else "",
            "reason_prefix": prefix,
            "full_reason": full_reason[:160],
        })

    Path("data").mkdir(exist_ok=True)
    with open("data/inf_exit_counterfactual.csv","w",newline="") as f:
        cols = ["symbol","side","open_ts","close_ts","held_min","avg_entry","exit_px","actual_pnl","pnl_+2h","pnl_+6h","pnl_+24h","reason_prefix","full_reason"]
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows: w.writerow(r)

    # ---------- Per-trigger summary ----------
    by_prefix = defaultdict(list)
    for r in rows: by_prefix[r["reason_prefix"]].append(r)

    def avg(xs):
        xs = [x for x in xs if x != "" and x is not None]
        return sum(xs)/len(xs) if xs else None

    print(f"\n=== PER-TRIGGER COUNTERFACTUAL TABLE (sorted by count desc) ===")
    print(f"{'trigger_prefix':<32} {'N':>4} {'actual':>8} {'+2h':>8} {'+6h':>8} {'+24h':>8} {'+24h-act':>9}")
    summary = []
    for prefix, rs in by_prefix.items():
        if len(rs) < 5: continue
        a_act = avg([r["actual_pnl"] for r in rs])
        a_2 = avg([r["pnl_+2h"] for r in rs])
        a_6 = avg([r["pnl_+6h"] for r in rs])
        a_24 = avg([r["pnl_+24h"] for r in rs])
        delta = (a_24 - a_act) if (a_24 is not None and a_act is not None) else None
        summary.append((prefix, len(rs), a_act, a_2, a_6, a_24, delta))
    summary.sort(key=lambda r: -r[1])
    for prefix, n, a_act, a2, a6, a24, dlt in summary:
        print(f"{prefix:<32} {n:>4} {a_act:>+7.3f}% "
              f"{(f'{a2:+7.3f}%' if a2 is not None else '   n/a '):>8} "
              f"{(f'{a6:+7.3f}%' if a6 is not None else '   n/a '):>8} "
              f"{(f'{a24:+7.3f}%' if a24 is not None else '   n/a '):>8} "
              f"{(f'{dlt:+8.3f}%' if dlt is not None else '   n/a '):>9}")

    print(f"\n=== TRIGGERS WHERE +24h HOLD WOULD HAVE BEATEN ACTUAL EXIT (cut-winners-short signature) ===")
    bad = sorted([s for s in summary if s[6] is not None and s[6] > 0.5], key=lambda r: -r[6])
    print(f"{'trigger_prefix':<32} {'N':>4} {'actual':>8} {'+24h':>8} {'leak':>8}")
    for prefix, n, a_act, a2, a6, a24, dlt in bad:
        print(f"{prefix:<32} {n:>4} {a_act:>+7.3f}% {a24:>+7.3f}% {dlt:>+7.3f}%")

    print(f"\nrows with usable counterfactual: {len(rows)}  skipped (no fwd price): {skipped_no_data}")
    print(f"csv: data/inf_exit_counterfactual.csv")

if __name__ == "__main__":
    main()
