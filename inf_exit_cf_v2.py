#!/usr/bin/env python3
"""V2 counterfactual: short horizons + MFE + 1m scalp simulation, pooled ang+inf, 120 days.

For each completed round-trip in last 120 days from data/history/{inf,ang}/:
  - actual exit PnL (VWAP entry vs exit price)
  - MFE within {15m, 30m, 1h, 2h, 4h} forward of exit (max favorable excursion)
  - Scalp-after-exit PnL: 1m WT cross-based entries for next 4h, summed PnL
  - Time-to-peak after exit
  - Full close reason (joined from decisions_*.jsonl with widened ±10min window)

Uses NPZ 3m primary, klines_cache 15m fallback. For 1m granularity (scalp sim
+ MFE), uses NPZ 3m as best available (3m bars approximate 1m moves close enough
for trigger comparison; true 1m is in klines_cache_backtest only for some syms).

Outputs:
  data/exit_cf_v2.csv
"""
import csv, json, time
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict
import numpy as np

ACCOUNTS = ["inf", "ang"]
HIST_BASE = Path("data/history")
DEC_DIR = Path("data/decisions")
NPZ_DIR = Path("backtest_v5/indicators_3m")
KCACHE = Path("klines_cache")
LOOKBACK_DAYS = 120
MFE_HORIZONS_MIN = [15, 30, 60, 120, 240]
SCALP_WINDOW_MIN = 240   # 4h post-exit scalp window

def parse_ts(s):
    if not s: return None
    if s.endswith("Z"): s = s[:-1] + "+00:00"
    try: return datetime.fromisoformat(s)
    except Exception: return None

# ---------- Price arrays (NPZ + kline fallback) ----------
_npz_cache = {}
_kcache = {}
def get_price_array(sym):
    """Return (ts_array_sec, close_array, source_label).  Prefer NPZ 3m; fallback klines 15m."""
    if sym in _npz_cache and _npz_cache[sym] is not None:
        ts, close = _npz_cache[sym]; return ts, close, "npz3m"
    if sym not in _npz_cache:
        fp = NPZ_DIR / f"{sym}.npz"
        if fp.exists():
            d = np.load(fp, allow_pickle=True)
            ts = d["timestamps"].astype(np.int64)
            if ts[-1] > 1e12: ts = ts // 1000
            _npz_cache[sym] = (ts, d["close_3m"].astype(np.float64))
            return _npz_cache[sym][0], _npz_cache[sym][1], "npz3m"
        _npz_cache[sym] = None
    if sym not in _kcache:
        fp = KCACHE / f"{sym}_15m.json"
        if not fp.exists():
            _kcache[sym] = None
        else:
            try:
                with open(fp) as f: bars = json.load(f)
                ts = []; close = []
                for b in bars:
                    tstr = b["timestamp"]
                    if tstr.endswith("Z"): tstr = tstr[:-1] + "+00:00"
                    ts.append(int(datetime.fromisoformat(tstr).timestamp()))
                    close.append(float(b["close"]))
                _kcache[sym] = (np.array(ts, dtype=np.int64), np.array(close, dtype=np.float64))
            except Exception:
                _kcache[sym] = None
    p = _kcache.get(sym)
    if p is None: return None, None, None
    return p[0], p[1], "kc15m"

def slice_forward(sym, start_ts, end_ts):
    ts, close, src = get_price_array(sym)
    if ts is None: return None, None, None
    a = int(np.searchsorted(ts, start_ts))
    b = int(np.searchsorted(ts, end_ts))
    if a >= len(ts): return None, None, None
    if b > len(ts): b = len(ts)
    if b <= a + 1: return None, None, None
    return ts[a:b], close[a:b], src

# ---------- Round trips from history ----------
def build_round_trips(account):
    cutoff = int(time.time()) - LOOKBACK_DAYS * 86400
    base = HIST_BASE / account
    trips = []
    for fp in sorted(base.glob("*.jsonl")):
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
                events.append({"ts": int(dtp.timestamp()), "type": r.get("type",""),
                               "qty": float(r.get("qty") or 0), "price": float(r.get("price") or 0),
                               "reason": (r.get("reason") or "")})
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
                    trips.append({"account": account, "symbol": sym, "side": side,
                                  "open_ts": first_open_ts, "close_ts": ev["ts"],
                                  "avg_entry": avg_entry, "exit_px": p, "hist_reason": last_reason})
                    qty = 0.0; cost = 0.0; first_open_ts = None
    return trips

# ---------- Decision reason index ----------
def load_decision_reasons(account):
    idx = {}
    for fp in sorted(DEC_DIR.glob(f"decisions_{account}_*.jsonl")):
        with open(fp, "rb") as f:
            for raw in f:
                try: r = json.loads(raw.decode("utf-8", errors="replace"))
                except Exception: continue
                a = r.get("action","")
                if a not in ("QUICK_CLOSE","CLOSE","STRONG_REDUCE","REDUCE","QUICK_REDUCE","QUICK_QUICK_CLOSE","LIQUIDATE"): continue
                pk = r.get("position_key",""); ts_str = r.get("timestamp")
                dtp = parse_ts(ts_str)
                if dtp is None: continue
                ts_min = int(dtp.timestamp()) // 60
                key = (pk, ts_min)
                cur = idx.get(key, "")
                new = r.get("reason","") or ""
                if len(new) > len(cur): idx[key] = new
    return idx

def lookup_reason(idx, account, sym, side, close_ts):
    pk = f"{account}:{sym}_{side}"
    ts_min = close_ts // 60
    best = ""
    for delta in range(-10, 11):
        r = idx.get((pk, ts_min + delta))
        if r and len(r) > len(best): best = r
    return best

def reason_prefix(reason):
    if not reason: return "(none)"
    tok = reason.split("_")
    keep = [t for t in tok[:5] if not (t.replace(".","").replace("-","").isdigit() or "=" in t)]
    return "_".join(keep[:3]) if keep else reason[:30]

# ---------- MFE + scalp simulation ----------
def compute_mfe(side, avg_entry, sym, close_ts, horizon_min):
    ts, close, _ = slice_forward(sym, close_ts + 60, close_ts + horizon_min * 60)
    if ts is None or avg_entry <= 0: return None, None
    if side == "LONG":
        peak = float(np.max(close)); pnl = (peak - avg_entry) / avg_entry * 100
        peak_idx = int(np.argmax(close))
    else:
        peak = float(np.min(close)); pnl = (avg_entry - peak) / avg_entry * 100
        peak_idx = int(np.argmin(close))
    mins_to_peak = int((ts[peak_idx] - close_ts) / 60)
    return pnl, mins_to_peak

def scalp_sim(side, sym, start_ts, window_min):
    """Naive scalp: enter at each 3m bar, exit when price drifts +/- 0.3% (TP) or 0.5% (SL).
    Direction matches original side. Returns (n_trades, total_pnl_pct, win_rate)."""
    ts, close, _ = slice_forward(sym, start_ts + 60, start_ts + window_min * 60)
    if ts is None or len(close) < 3: return 0, 0.0, 0.0
    TP = 0.3 / 100; SL = -0.5 / 100
    n_trades = 0; total_pnl = 0.0; wins = 0
    i = 0
    while i < len(close) - 1:
        entry = close[i]; exit_at = None
        for j in range(i + 1, min(i + 20, len(close))):  # max 20 bars (60min) per scalp
            if side == "LONG":
                ret = (close[j] - entry) / entry
            else:
                ret = (entry - close[j]) / entry
            if ret >= TP or ret <= SL:
                exit_at = j; break
        if exit_at is None: break  # ran out of horizon mid-trade
        if side == "LONG":
            pnl_pct = (close[exit_at] - entry) / entry * 100
        else:
            pnl_pct = (entry - close[exit_at]) / entry * 100
        n_trades += 1; total_pnl += pnl_pct
        if pnl_pct > 0: wins += 1
        i = exit_at + 1
    return n_trades, total_pnl, (wins / n_trades * 100 if n_trades else 0)

# ---------- Main ----------
def main():
    t0 = time.time()
    all_trips = []
    reason_idx_by_acct = {}
    for acct in ACCOUNTS:
        trips = build_round_trips(acct)
        all_trips.extend(trips)
        reason_idx_by_acct[acct] = load_decision_reasons(acct)
        print(f"[{acct}] {len(trips)} round-trips, {len(reason_idx_by_acct[acct])} indexed close-decisions")
    print(f"TOTAL pooled round-trips ({LOOKBACK_DAYS}d): {len(all_trips)}")

    rows = []
    skipped = 0
    for k, t in enumerate(all_trips):
        if k % 200 == 0 and k > 0: print(f"  processed {k}/{len(all_trips)}  elapsed {time.time()-t0:.1f}s")
        full_reason = lookup_reason(reason_idx_by_acct[t["account"]], t["account"], t["symbol"], t["side"], t["close_ts"])
        if not full_reason: full_reason = t["hist_reason"]
        prefix = reason_prefix(full_reason)
        if t["avg_entry"] <= 0 or t["exit_px"] <= 0: continue
        if t["side"] == "LONG":
            actual = (t["exit_px"] - t["avg_entry"]) / t["avg_entry"] * 100
        else:
            actual = (t["avg_entry"] - t["exit_px"]) / t["avg_entry"] * 100

        mfe = {}; ttp = {}
        any_data = False
        for h in MFE_HORIZONS_MIN:
            pnl, t2p = compute_mfe(t["side"], t["avg_entry"], t["symbol"], t["close_ts"], h)
            mfe[h] = pnl; ttp[h] = t2p
            if pnl is not None: any_data = True
        if not any_data:
            skipped += 1; continue
        n_sc, tot_sc, wr_sc = scalp_sim(t["side"], t["symbol"], t["close_ts"], SCALP_WINDOW_MIN)

        held_min = (t["close_ts"] - t["open_ts"]) / 60
        rows.append({
            "account": t["account"], "symbol": t["symbol"], "side": t["side"],
            "open_ts": datetime.fromtimestamp(t["open_ts"], tz=timezone.utc).isoformat(),
            "close_ts": datetime.fromtimestamp(t["close_ts"], tz=timezone.utc).isoformat(),
            "held_min": round(held_min, 1),
            "actual_pnl": round(actual, 4),
            "mfe_15m": round(mfe[15], 4) if mfe[15] is not None else "",
            "mfe_30m": round(mfe[30], 4) if mfe[30] is not None else "",
            "mfe_1h": round(mfe[60], 4) if mfe[60] is not None else "",
            "mfe_2h": round(mfe[120], 4) if mfe[120] is not None else "",
            "mfe_4h": round(mfe[240], 4) if mfe[240] is not None else "",
            "ttp_4h_min": ttp[240] if ttp[240] is not None else "",
            "scalp_n": n_sc, "scalp_pnl": round(tot_sc, 4), "scalp_wr": round(wr_sc, 1),
            "reason_prefix": prefix, "full_reason": full_reason[:160],
        })

    Path("data").mkdir(exist_ok=True)
    with open("data/exit_cf_v2.csv","w",newline="") as f:
        cols = ["account","symbol","side","open_ts","close_ts","held_min","actual_pnl",
                "mfe_15m","mfe_30m","mfe_1h","mfe_2h","mfe_4h","ttp_4h_min",
                "scalp_n","scalp_pnl","scalp_wr","reason_prefix","full_reason"]
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows: w.writerow(r)

    # ---------- Per-trigger summary ----------
    by_prefix = defaultdict(list)
    for r in rows: by_prefix[r["reason_prefix"]].append(r)

    def avg(xs):
        xs = [x for x in xs if x not in ("", None)]
        return sum(xs)/len(xs) if xs else None

    print(f"\n=== PER-TRIGGER COUNTERFACTUAL — short horizons + MFE + scalp (N>=10) ===")
    print(f"{'trigger_prefix':<32} {'N':>4} {'actual':>8} {'mfe15m':>8} {'mfe1h':>8} {'mfe4h':>8} {'leak4h':>8} {'scalp4h':>8}")
    summary = []
    for prefix, rs in by_prefix.items():
        if len(rs) < 10: continue
        a_act = avg([r["actual_pnl"] for r in rs])
        m15 = avg([r["mfe_15m"] for r in rs])
        m1h = avg([r["mfe_1h"] for r in rs])
        m4h = avg([r["mfe_4h"] for r in rs])
        leak = (m4h - a_act) if (m4h is not None and a_act is not None) else None
        sc = avg([r["scalp_pnl"] for r in rs])
        summary.append((prefix, len(rs), a_act, m15, m1h, m4h, leak, sc))
    summary.sort(key=lambda r: -r[1])
    for prefix, n, a, m15, m1, m4, lk, sc in summary:
        f1 = lambda x: f'{x:+7.3f}%' if x is not None else '   n/a '
        print(f"{prefix:<32} {n:>4} {f1(a):>8} {f1(m15):>8} {f1(m1):>8} {f1(m4):>8} {f1(lk):>8} {f1(sc):>8}")

    print(f"\n=== TOP CUT-WINNERS-SHORT (mfe_4h - actual >= 1.0 pp, N>=10) ===")
    bad = sorted([s for s in summary if s[6] is not None and s[6] >= 1.0], key=lambda r: -r[6])
    for prefix, n, a, m15, m1, m4, lk, sc in bad:
        f1 = lambda x: f'{x:+7.3f}%' if x is not None else '   n/a '
        print(f"  {prefix:<32} N={n:>3}  actual={f1(a)}  mfe4h={f1(m4)}  leak={lk:+5.2f}pp  scalp4h={f1(sc)}")

    print(f"\n=== EXITS THAT EARNED THEIR KEEP (mfe_4h <= actual, N>=10) ===")
    good = sorted([s for s in summary if s[6] is not None and s[6] <= 0], key=lambda r: r[6])
    for prefix, n, a, m15, m1, m4, lk, sc in good:
        f1 = lambda x: f'{x:+7.3f}%' if x is not None else '   n/a '
        print(f"  {prefix:<32} N={n:>3}  actual={f1(a)}  mfe4h={f1(m4)}  leak={lk:+5.2f}pp")

    print(f"\nrows usable: {len(rows)}  skipped (no fwd price): {skipped}  elapsed: {time.time()-t0:.1f}s")
    print(f"csv: data/exit_cf_v2.csv")

if __name__ == "__main__":
    main()
