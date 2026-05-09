#!/usr/bin/env python3
"""TRENDER+BREAKOUT injector forward-replay audit.

Walks historical 15m klines for the configured symbols across a date range.
At each 1h step, computes what TRENDER_INJECT / BREAKOUT_INJECT would have
flagged using only data available at that point, then records forward
1h/4h/24h realized returns for each pick across a grid of knob variants.

Output: CSV with one row per (variant, ts, sym, injector, side).

This is INJECTOR QUALITY AUDIT, not a trade backtest. No Sharpe is computed
here. For trade-engine arm comparison, run backtest_v8_engine.py separately
on the symbols this audit identifies as high-precision picks.

Usage:
    python trender_breakout_replay_audit.py \\
        --start 2026-01-09 --end 2026-05-08 \\
        --klines-dir /home/niels/binance-sandbox/klines_cache_backtest \\
        --output data/inject_audit/replay_$(date -u +%Y%m%dT%H%M).csv
"""
import argparse, csv, json, math, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np

DEFAULT_SYMS = ["BTCUSDC","ETHUSDC","SOLUSDC","BNBUSDC","XRPUSDC","ADAUSDC","AVAXUSDC","LINKUSDC","ZECUSDC","TONUSDT","PENDLEUSDT","ARUSDT"]
TIER_MULT = {"A":1.5,"B":1.2,"C":1.0,"D":0.7}
DEFAULT_TIER = "B"  # all 12 syms treated as B-tier for audit (live uses get_symbol_tier)

# ── knob defaults match config.py ────────────────────────────────────────────────
DEFAULT_KNOBS = {
    "T_LIN_MIN": 0.55, "T_RET24_MIN": 1.5, "T_QV_FLOOR": 25_000_000.0,
    "T_QV_ANCHOR": 50_000_000.0, "T_QV_MAX_BOOST": 1.5, "T_DD_RATIO_MAX": 0.40,
    "B_MAD_MULT": 2.0, "B_MIN_RET": 3.0, "B_MAX_DD_RATIO": 0.5,
}

# ── audit variants: baseline + single-knob sweeps + freshness ────────────────────
DEFAULT_KNOBS["FRESH_BARS"] = 0  # 0=disabled, N=require sym was NOT picked at any step in last N×15m bars

def make_variants():
    out = [("baseline", DEFAULT_KNOBS.copy())]
    for v in [0.45, 0.65, 0.75]:                        out.append((f"T_LIN_{int(v*100)}", {**DEFAULT_KNOBS, "T_LIN_MIN": v}))
    for v in [1.0, 2.5, 4.0]:                            out.append((f"T_RET_{v:.1f}", {**DEFAULT_KNOBS, "T_RET24_MIN": v}))
    for v in [10_000_000, 50_000_000, 100_000_000]:      out.append((f"T_QV_{int(v/1e6)}M", {**DEFAULT_KNOBS, "T_QV_FLOOR": float(v)}))
    for v in [0.25, 0.55, 0.70]:                         out.append((f"T_DD_{int(v*100)}", {**DEFAULT_KNOBS, "T_DD_RATIO_MAX": v}))
    for v in [1.5, 2.5, 3.0]:                            out.append((f"B_MAD_{v:.1f}", {**DEFAULT_KNOBS, "B_MAD_MULT": v}))
    for v in [2.0, 4.0, 5.0]:                            out.append((f"B_MIN_{v:.1f}", {**DEFAULT_KNOBS, "B_MIN_RET": v}))
    # Freshness sweep — fix the late-entry problem (sym must be NEW pick, not stale)
    for v in [1, 2, 4, 8, 16]:                            out.append((f"FRESH_{v}", {**DEFAULT_KNOBS, "FRESH_BARS": v}))
    # Combined: tightest gates + freshness
    out.append(("STRICT_FRESH4", {**DEFAULT_KNOBS, "T_LIN_MIN": 0.65, "T_RET24_MIN": 2.5, "B_MIN_RET": 5.0, "FRESH_BARS": 4}))
    out.append(("STRICT_FRESH16", {**DEFAULT_KNOBS, "T_LIN_MIN": 0.65, "T_RET24_MIN": 2.5, "B_MIN_RET": 5.0, "FRESH_BARS": 16}))
    return out


def load_15m(klines_dir: Path, sym: str) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    p = klines_dir / f"{sym}_15m.json"
    if not p.exists(): return None
    with open(p) as f:
        rows = json.load(f)
    if not rows: return None
    ts = np.array([np.datetime64(r["timestamp"][:19]) for r in rows], dtype="datetime64[s]")
    close = np.array([float(r["close"]) for r in rows], dtype=np.float64)
    volume = np.array([float(r["volume"]) for r in rows], dtype=np.float64)
    order = np.argsort(ts)
    return ts[order], close[order], volume[order]


def pearson_r(y: np.ndarray) -> float:
    """|Pearson r| of y vs linear time index — linearity proxy."""
    n = len(y)
    if n < 8: return 0.0
    x = np.arange(n, dtype=np.float64)
    xm = x.mean(); ym = y.mean()
    num = ((x - xm) * (y - ym)).sum()
    den = math.sqrt(((x - xm)**2).sum() * ((y - ym)**2).sum())
    if den <= 0: return 0.0
    return abs(num / den)


def linearity_proxy(close: np.ndarray) -> float:
    """Avg |r| across 4 lookbacks mirroring live {15m, 1h, 4h, D} TF set."""
    rs = []
    for lb in (24, 96, 384, 1536):  # 6h, 24h, 96h (4d), 384h (16d) of 15m bars
        if len(close) >= lb:
            rs.append(pearson_r(close[-lb:]))
    return float(sum(rs) / len(rs)) if rs else 0.0


def compute_horizons(close_15m: np.ndarray, vol_15m: np.ndarray, t_idx: int) -> Optional[Dict]:
    """Return {ret_1h, ret_4h, ret_24h, dd_long, dd_short, qv_24h_usd, lin}.
    All windows are 15m-based: 4 bars=1h, 16=4h, 96=24h.
    """
    if t_idx < 96: return None
    last = float(close_15m[t_idx])
    if last <= 0: return None
    c5 = float(close_15m[t_idx - 4])    # 1h ago
    c17 = float(close_15m[t_idx - 16])  # 4h ago
    c97 = float(close_15m[t_idx - 96])  # 24h ago
    if min(c5, c17, c97) <= 0: return None
    c24h = close_15m[t_idx-95:t_idx+1]   # last 24h of 15m bars
    v24h = vol_15m[t_idx-95:t_idx+1]
    peak = float(c24h.max())
    trough = float(c24h.min())
    return {
        "ret_1h": (last/c5 - 1.0) * 100.0,
        "ret_4h": (last/c17 - 1.0) * 100.0,
        "ret_24h": (last/c97 - 1.0) * 100.0,
        "dd_long_pct": ((peak - last)/peak * 100.0) if peak > 0 else 0.0,
        "dd_short_pct": ((last - trough)/trough * 100.0) if trough > 0 else 0.0,
        "qv_24h_usd": float((v24h * c24h).sum()),
        "lin": linearity_proxy(close_15m[max(0, t_idx-1535):t_idx+1]),
    }


def trender_pick(h: Dict, k: Dict) -> Optional[Tuple[str, float, float]]:
    """Return (side, score, dd_ratio) if qualifies, else None."""
    if h["lin"] < k["T_LIN_MIN"]: return None
    if h["qv_24h_usd"] < k["T_QV_FLOOR"]: return None
    qv_boost = max(0.0, min(k["T_QV_MAX_BOOST"], math.log10(max(h["qv_24h_usd"], 1.0) / k["T_QV_ANCHOR"])))
    tm = TIER_MULT[DEFAULT_TIER]
    if h["ret_24h"] >= k["T_RET24_MIN"] and h["ret_4h"] >= 0 and h["ret_1h"] >= 0:
        ddr = h["dd_long_pct"] / max(h["ret_24h"], 0.01)
        if ddr <= k["T_DD_RATIO_MAX"]:
            stab = max(0.0, 1.0 - ddr)
            score = h["ret_24h"] * (h["lin"]**2) * qv_boost * tm * stab
            return ("LONG", score, ddr)
    if h["ret_24h"] <= -k["T_RET24_MIN"] and h["ret_4h"] <= 0 and h["ret_1h"] <= 0:
        ddr = h["dd_short_pct"] / max(abs(h["ret_24h"]), 0.01)
        if ddr <= k["T_DD_RATIO_MAX"]:
            stab = max(0.0, 1.0 - ddr)
            score = abs(h["ret_24h"]) * (h["lin"]**2) * qv_boost * tm * stab
            return ("SHORT", score, ddr)
    return None


def breakout_picks(horizons: Dict[str, Dict], k: Dict) -> Dict[str, Tuple[str, float, float]]:
    """Returns {sym: (side, z_from_median, dd_ratio)}."""
    out = {}
    rets = [h["ret_4h"] for h in horizons.values() if h]
    if len(rets) < 8: return out
    med = float(np.median(rets))
    mad = float(np.median(np.abs(np.array(rets) - med)))
    mad = max(mad, 0.3)
    upper = med + k["B_MAD_MULT"] * mad
    lower = med - k["B_MAD_MULT"] * mad
    for sym, h in horizons.items():
        if not h: continue
        r4 = h["ret_4h"]
        if r4 >= upper and r4 >= k["B_MIN_RET"]:
            ddr = h["dd_long_pct"] / max(h["ret_24h"], 0.01) if h["ret_24h"] > 0 else 1.0
            if ddr <= k["B_MAX_DD_RATIO"]:
                out[sym] = ("LONG", (r4 - med)/mad, ddr)
        elif r4 <= lower and r4 <= -k["B_MIN_RET"]:
            ddr = h["dd_short_pct"] / max(abs(h["ret_24h"]), 0.01) if h["ret_24h"] < 0 else 1.0
            if ddr <= k["B_MAX_DD_RATIO"]:
                out[sym] = ("SHORT", (med - r4)/mad, ddr)
    return out


def fwd_returns(close: np.ndarray, t_idx: int, side: str) -> Dict[str, Optional[float]]:
    """Forward 1h/4h/24h returns from t_idx, signed for the trade side."""
    last = float(close[t_idx])
    if last <= 0: return {"fwd_1h": None, "fwd_4h": None, "fwd_24h": None}
    out = {}
    for label, bars in (("fwd_1h", 4), ("fwd_4h", 16), ("fwd_24h", 96)):
        if t_idx + bars >= len(close):
            out[label] = None
        else:
            fwd = (close[t_idx + bars]/last - 1.0) * 100.0
            out[label] = fwd if side == "LONG" else -fwd
    return out


def run_audit(klines_dir: Path, syms: List[str], start_iso: str, end_iso: str, output_csv: Path, variants: List[Tuple[str, Dict]]):
    print(f"[AUDIT] loading {len(syms)} syms from {klines_dir}")
    data = {}
    for sym in syms:
        d = load_15m(klines_dir, sym)
        if d is None:
            print(f"[AUDIT] WARN: no klines for {sym}, skipping")
            continue
        data[sym] = d
    if len(data) < 8:
        print(f"[AUDIT] only {len(data)} syms loaded — aborting (need ≥8 for cross-sectional MAD)"); return

    start_dt = np.datetime64(start_iso[:19], 's')
    end_dt = np.datetime64(end_iso[:19], 's')

    # build a unified 1h timestamp grid (every 4 15m bars from start to end)
    sample_ts = data[next(iter(data))][0]
    grid_mask = (sample_ts >= start_dt) & (sample_ts <= end_dt)
    grid_ts = sample_ts[grid_mask]
    if len(grid_ts) < 100:
        print(f"[AUDIT] grid too small ({len(grid_ts)} bars) — abort"); return
    grid_idxs = np.arange(0, len(grid_ts), 4)  # every 4th 15m bar = 1h step

    # per-sym index offset (where grid starts in each sym's array)
    sym_start_idx = {s: int(np.searchsorted(d[0], start_dt)) for s, d in data.items()}

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fout = open(output_csv, "w", newline="")
    writer = csv.writer(fout)
    writer.writerow(["variant","ts","sym","injector","side","score_or_z","ret_1h","ret_4h","ret_24h","qv_M","lin","dd_ratio","fwd_1h","fwd_4h","fwd_24h"])

    # Per-variant freshness cache: (variant, sym, injector, side) -> last_step_picked
    last_pick_step = {}

    n_steps = len(grid_idxs); n_picks_total = 0; t0 = time.time()
    for step_i, gi in enumerate(grid_idxs):
        # progress
        if step_i % 200 == 0 and step_i > 0:
            pct = step_i/n_steps*100; el = time.time()-t0; eta = el/step_i*(n_steps-step_i)
            print(f"[AUDIT] step {step_i}/{n_steps} ({pct:.1f}%) elapsed={el:.0f}s eta={eta:.0f}s picks_so_far={n_picks_total}")
        # compute horizons for each sym at this step
        horizons = {}
        for sym, (s_ts, s_close, s_vol) in data.items():
            t_idx = sym_start_idx[sym] + gi
            if t_idx >= len(s_close): continue
            h = compute_horizons(s_close, s_vol, t_idx)
            if h: horizons[sym] = h
        if len(horizons) < 8: continue
        ts_iso = str(grid_ts[gi])
        # apply each variant
        for var_name, k in variants:
            fresh_bars = int(k.get("FRESH_BARS", 0))
            for sym, h in horizons.items():
                t_idx = sym_start_idx[sym] + gi
                s_close = data[sym][1]
                # TRENDER
                tr = trender_pick(h, k)
                if tr:
                    side, score, ddr = tr
                    cache_k = (var_name, sym, "TRENDER", side)
                    last = last_pick_step.get(cache_k)
                    if fresh_bars == 0 or last is None or (gi - last) > fresh_bars:
                        fwd = fwd_returns(s_close, t_idx, side)
                        writer.writerow([var_name, ts_iso, sym, "TRENDER", side, f"{score:.4f}", f"{h['ret_1h']:.2f}", f"{h['ret_4h']:.2f}", f"{h['ret_24h']:.2f}", f"{h['qv_24h_usd']/1e6:.1f}", f"{h['lin']:.3f}", f"{ddr:.3f}", f"{fwd['fwd_1h']:.2f}" if fwd['fwd_1h'] is not None else "", f"{fwd['fwd_4h']:.2f}" if fwd['fwd_4h'] is not None else "", f"{fwd['fwd_24h']:.2f}" if fwd['fwd_24h'] is not None else ""])
                        n_picks_total += 1
                        last_pick_step[cache_k] = gi
            # BREAKOUT (cross-sectional, computed once per variant per step)
            bk = breakout_picks(horizons, k)
            for sym, (side, z, ddr) in bk.items():
                cache_k = (var_name, sym, "BREAKOUT", side)
                last = last_pick_step.get(cache_k)
                if fresh_bars > 0 and last is not None and (gi - last) <= fresh_bars: continue
                t_idx = sym_start_idx[sym] + gi
                s_close = data[sym][1]
                h = horizons[sym]
                fwd = fwd_returns(s_close, t_idx, side)
                writer.writerow([var_name, ts_iso, sym, "BREAKOUT", side, f"{z:.2f}", f"{h['ret_1h']:.2f}", f"{h['ret_4h']:.2f}", f"{h['ret_24h']:.2f}", f"{h['qv_24h_usd']/1e6:.1f}", f"{h['lin']:.3f}", f"{ddr:.3f}", f"{fwd['fwd_1h']:.2f}" if fwd['fwd_1h'] is not None else "", f"{fwd['fwd_4h']:.2f}" if fwd['fwd_4h'] is not None else "", f"{fwd['fwd_24h']:.2f}" if fwd['fwd_24h'] is not None else ""])
                n_picks_total += 1
                last_pick_step[cache_k] = gi
    fout.close()
    el = time.time() - t0
    print(f"[AUDIT] DONE: {n_steps} steps × {len(variants)} variants → {n_picks_total} picks in {el:.0f}s → {output_csv}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-09")
    ap.add_argument("--end", default="2026-05-08")
    ap.add_argument("--klines-dir", default="/home/niels/binance-sandbox/klines_cache_backtest")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMS))
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    out = Path(args.output) if args.output else Path(f"data/inject_audit/replay_{datetime.utcnow().strftime('%Y%m%dT%H%M')}.csv")
    variants = make_variants()
    print(f"[AUDIT] start={args.start} end={args.end} syms={len(syms)} variants={len(variants)} → {out}")
    run_audit(Path(args.klines_dir), syms, args.start, args.end, out, variants)


if __name__ == "__main__":
    main()
