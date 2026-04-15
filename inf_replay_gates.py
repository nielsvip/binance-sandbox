#!/usr/bin/env python3
"""Replay entry gates at each missed-big-mover timestamp.

For each (symbol, side, episode_start) in data/inf_miss_analysis.csv that:
  - is in inf's universe (history file exists)
  - had |peak_move| >= 3%
  - was NOT opened

snap the NPZ 3m indicator row at episode_start, evaluate each gate, and
record which gate(s) blocked.  Also check data/history/inf for any CLOSE
in the 6h preceding the miss — the user hypothesizes early exits +
bad reentries are the root cause.

Outputs:
  data/inf_replay_gates.csv — per-miss gate decision breakdown
  data/inf_early_exits.csv  — closes preceding misses (early-exit pattern)
"""
import csv, json, os
from pathlib import Path
from datetime import datetime, timezone, timedelta
import numpy as np
from collections import Counter

NPZ_DIR = Path("backtest_v5/indicators_3m")
HIST = Path("data/history/inf")
MISS_CSV = Path("data/inf_miss_analysis.csv")

# Gate thresholds mirroring live inf config (see config.py K3M_CAP=80, score=18 ref)
K3M_LONG_CAP = 80.0
K3M_SHORT_FLOOR = 20.0
K15M_LONG_CAP = 85.0
K15M_SHORT_FLOOR = 15.0
MIN_ENTRY_SCORE = 18.0
MIN_ENTRY_SCORE_BYPASS = 12.0
HTF_TFS = ["1h", "4h", "D"]
MIN_HTF_ALIGN = 2  # 2/3 required
MIN_HTF_ALIGN_BYPASS = 1

def parse_ts(s):
    if s.endswith("Z"): s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)

_npz_cache = {}
def npz(sym):
    if sym in _npz_cache: return _npz_cache[sym]
    fp = NPZ_DIR / f"{sym}.npz"
    if not fp.exists():
        _npz_cache[sym] = None; return None
    d = np.load(fp, allow_pickle=True)
    ts = d["timestamps"].astype(np.int64)
    if ts[-1] > 1e12: ts = ts // 1000
    _npz_cache[sym] = (d, ts); return _npz_cache[sym]

def snap(sym, ts_sec):
    payload = npz(sym)
    if payload is None: return None
    d, ts = payload
    i = int(np.searchsorted(ts, ts_sec))
    if i >= len(ts): i = len(ts) - 1
    if i < 0: i = 0
    out = {"_idx": i, "_ts": int(ts[i])}
    for k in d.keys():
        if k == "timestamps": continue
        try:
            out[k] = d[k][i].item() if hasattr(d[k][i], "item") else d[k][i]
        except Exception: out[k] = None
    return out

def _f(v, default=0.0):
    try: return float(v)
    except Exception: return default

def htf_aligned(snap_row, is_long):
    """Count HTF agreement on direction.  LONG: wt1>wt2 or ha green.  SHORT: reverse."""
    agree = 0
    for tf in HTF_TFS:
        wt1 = _f(snap_row.get(f"wt1_{tf}")); wt2 = _f(snap_row.get(f"wt2_{tf}"))
        ha = snap_row.get(f"ha_{tf}", "")
        if is_long:
            if wt1 > wt2 or str(ha).lower() == "green": agree += 1
        else:
            if wt1 < wt2 or str(ha).lower() == "red": agree += 1
    return agree

def score_entry(snap_row, is_long):
    """Simplified entry score mirroring inf live: stoch crosses + WT bull/bear + HA + MFI."""
    s = 0.0
    if is_long:
        if snap_row.get("stoch_crossover_3m"): s += 5
        if snap_row.get("wt_cross_bull_3m"): s += 5
        if str(snap_row.get("ha_3m","")).lower() == "green": s += 3
        if str(snap_row.get("ha_15m","")).lower() == "green": s += 3
        if _f(snap_row.get("mfi_15m"), 50) > 50: s += 2
        if _f(snap_row.get("wt_velocity_3m"), 0) > 0: s += 2
        if _f(snap_row.get("adx_1h"), 0) > 20: s += 2
    else:
        if snap_row.get("stoch_crossunder_3m"): s += 5
        if snap_row.get("wt_cross_bear_3m"): s += 5
        if str(snap_row.get("ha_3m","")).lower() == "red": s += 3
        if str(snap_row.get("ha_15m","")).lower() == "red": s += 3
        if _f(snap_row.get("mfi_15m"), 50) < 50: s += 2
        if _f(snap_row.get("wt_velocity_3m"), 0) < 0: s += 2
        if _f(snap_row.get("adx_1h"), 0) > 20: s += 2
    return s

def evaluate_gates(s, is_long):
    """Return list of blocking gate names — deterministic gates only.
    SCORE gate is excluded from blocking list because live scoring
    is too complex to replicate faithfully; score is reported for info only."""
    blocks = []
    k3 = _f(s.get("stoch_k_3m"), 50)
    k15 = _f(s.get("stoch_k_15m"), 50)
    if is_long:
        if k3 > K3M_LONG_CAP: blocks.append("STOCH_K3M_CAP")
        if k15 > K15M_LONG_CAP: blocks.append("STOCH_K15M_CAP")
    else:
        if k3 < K3M_SHORT_FLOOR: blocks.append("STOCH_K3M_FLOOR")
        if k15 < K15M_SHORT_FLOOR: blocks.append("STOCH_K15M_FLOOR")
    align = htf_aligned(s, is_long)
    if align < MIN_HTF_ALIGN: blocks.append(f"HTF_ALIGN({align}/3)")
    score = score_entry(s, is_long)  # reported, not gated
    return blocks, {"k3": k3, "k15": k15, "align": align, "score": score}

def evaluate_gates_with_bypass(s, is_long, bypasses):
    """bypasses: set of {'stoch','htf'}  — score gate not simulated"""
    blocks = []
    k3 = _f(s.get("stoch_k_3m"), 50); k15 = _f(s.get("stoch_k_15m"), 50)
    if "stoch" not in bypasses:
        if is_long:
            if k3 > K3M_LONG_CAP: blocks.append("STOCH_K3M_CAP")
            if k15 > K15M_LONG_CAP: blocks.append("STOCH_K15M_CAP")
        else:
            if k3 < K3M_SHORT_FLOOR: blocks.append("STOCH_K3M_FLOOR")
            if k15 < K15M_SHORT_FLOOR: blocks.append("STOCH_K15M_FLOOR")
    align = htf_aligned(s, is_long)
    min_align = MIN_HTF_ALIGN_BYPASS if "htf" in bypasses else MIN_HTF_ALIGN
    if align < min_align: blocks.append(f"HTF_ALIGN({align}/3)")
    return blocks

def preceding_close(sym, side, ts_sec, hours=24):
    """Find closes in history jsonl (same-side + opposite-side) in (ts_sec - hours, ts_sec) window."""
    hits = []
    for s_side in ("LONG", "SHORT"):
        fp = HIST / f"{sym}_{s_side}.jsonl"
        if not fp.exists(): continue
        cutoff = ts_sec - hours * 3600
        with open(fp) as f:
            for line in f:
                try: r = json.loads(line)
                except Exception: continue
                t = r.get("type", "")
                if t not in ("CLOSE", "QUICK_CLOSE", "REDUCE", "STRONG_REDUCE", "LIQUIDATE"): continue
                tsr = r.get("ts")
                if not tsr: continue
                if tsr.endswith("Z"): tsr = tsr[:-1] + "+00:00"
                try: dtp = datetime.fromisoformat(tsr)
                except Exception: continue
                e_ts = int(dtp.timestamp())
                if cutoff <= e_ts < ts_sec:
                    hits.append({"ts": e_ts, "close_side": s_side, "type": t, "price": r.get("price"), "reason": (r.get("reason") or "")[:70], "mins_before": (ts_sec - e_ts) // 60})
    return hits or None

def main():
    all_rows = list(csv.DictReader(open(MISS_CSV)))
    # filter: in-universe, not opened, |move|>=3%
    missed = []
    for r in all_rows:
        if int(r["opened"]): continue
        sym, side = r["symbol"], r["side"]
        if not (HIST / f"{sym}_{side}.jsonl").exists(): continue
        move = max(abs(float(r["ret15_peak"])), abs(float(r["ret3_peak"])))
        if move < 3.0: continue
        missed.append(r)
    print(f"Replaying {len(missed)} missed big-mover episodes")

    out_rows = []
    block_counter = Counter()
    bypass_stats = {
        "baseline": 0, "stoch_only": 0, "htf_only": 0, "stoch+htf": 0,
    }
    early_exit_rows = []

    for r in missed:
        sym, side = r["symbol"], r["side"]
        is_long = (side == "LONG")
        ep_ts = int(parse_ts(r["episode_start"]).timestamp())
        s = snap(sym, ep_ts)
        if s is None: continue
        blocks, meta = evaluate_gates(s, is_long)
        for b in blocks: block_counter[b.split("(")[0]] += 1
        # bypass counterfactuals
        if not blocks: bypass_stats["baseline"] += 1
        if not evaluate_gates_with_bypass(s, is_long, {"stoch"}): bypass_stats["stoch_only"] += 1
        if not evaluate_gates_with_bypass(s, is_long, {"htf"}): bypass_stats["htf_only"] += 1
        if not evaluate_gates_with_bypass(s, is_long, {"stoch","htf"}): bypass_stats["stoch+htf"] += 1

        pc = preceding_close(sym, side, ep_ts)
        out_rows.append({
            "symbol": sym, "side": side, "episode": r["episode_start"],
            "r15": r["ret15_peak"], "r3": r["ret3_peak"],
            "k3": f'{meta["k3"]:.1f}', "k15": f'{meta["k15"]:.1f}',
            "htf_align": f'{meta["align"]}/3', "score": f'{meta["score"]:.0f}',
            "blocks": ",".join(blocks) or "NONE",
            "preceding_close_count": len(pc) if pc else 0,
        })
        if pc:
            for h in pc:
                early_exit_rows.append({
                    "symbol": sym, "side": side, "episode": r["episode_start"],
                    "peak_move": max(abs(float(r["ret15_peak"])), abs(float(r["ret3_peak"]))),
                    "close_type": h["type"], "mins_before_miss": h["mins_before"],
                    "close_price": h["price"], "close_reason": h["reason"],
                })

    with open("data/inf_replay_gates.csv", "w", newline="") as f:
        cols = ["symbol","side","episode","r15","r3","k3","k15","htf_align","score","blocks","preceding_close_count"]
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in out_rows: w.writerow(r)
    with open("data/inf_early_exits.csv", "w", newline="") as f:
        cols = ["symbol","side","episode","peak_move","close_type","mins_before_miss","close_price","close_reason"]
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in early_exit_rows: w.writerow(r)

    total = len(out_rows)
    print(f"\n=== BLOCKING GATES (across {total} missed big movers) ===")
    for gate, n in block_counter.most_common():
        print(f"  {gate:<20} {n:>4}  ({n/total*100:.1f}%)")
    print(f"\n=== BYPASS COUNTERFACTUALS — how many would pass with each bypass set ===")
    for name, n in bypass_stats.items():
        print(f"  {name:<22} {n:>4}  ({n/total*100:.1f}%)")
    print(f"\n=== EARLY EXIT SIGNAL (closes in 6h before miss) ===")
    with_exit = sum(1 for r in out_rows if r["preceding_close_count"] > 0)
    print(f"  missed episodes with >=1 close in prior 6h: {with_exit}/{total} ({with_exit/total*100:.1f}%)")
    # Close reason distribution
    reasons = Counter(r["close_reason"].split("_")[0] for r in early_exit_rows)
    print(f"  close reason prefix distribution (top 10):")
    for reason, n in reasons.most_common(10):
        print(f"    {reason:<25} {n}")
    print(f"\nfiles: data/inf_replay_gates.csv  data/inf_early_exits.csv")

if __name__ == "__main__":
    main()
