#!/usr/bin/env python3
"""Continuous trade-analysis + novel-suggestion engine.

For each run JSONL in $V8_TRADES_OUT_DIR:
  1. Pull entry-time + exit-time indicator state for every trade (via NPZ snapshot)
  2. Cluster: winners vs losers by indicator state — find which indicator values
     systematically separate good outcomes from bad
  3. For each `entry_reason × exit_reason` cell: identify bleeding cells (low WR + many trades)
  4. Detect MISSED ENTRIES: scan klines for bars where price moved >X% in next M bars
     and NO run took an entry — extract indicator state — cluster these
  5. Cross-compare clusters: where does winners' indicator state differ from losers' /
     missed opportunities → propose novel gates ("if X then SKIP" or "if Y then ENTER")
  6. Output ranked markdown report at $V8_TRADES_OUT_DIR/SUGGESTIONS_<ts>.md
  7. Optionally loop forever with --interval N

Usage:
  python3 suggestion_engine.py                              # one-shot, default dir
  python3 suggestion_engine.py --runs btc_BEST,5SYM_BEST    # specific runs
  python3 suggestion_engine.py --syms BTCUSDT,ETHUSDT       # specific symbols
  python3 suggestion_engine.py --loop --interval 900        # every 15 min forever
  python3 suggestion_engine.py --gain-threshold 0.5 --lookahead-bars 20  # tune missed-entry detection
"""
import argparse
import glob
import json
import math
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path("/Users/niels/Documents/binance")
NPZ_DIR = ROOT / "backtest_v8" / "indicators"
DEFAULT_TRADES_DIR = Path(os.environ.get("V8_TRADES_OUT_DIR", "/tmp/v8_trades"))

# Indicator fields to pull at entry/exit for cluster analysis.
# Mirrors a subset of chart_server's SNAPSHOT_FIELDS — keep tight for speed.
ANALYSIS_FIELDS = [
    "stoch_k_3m", "stoch_d_3m", "stoch_k_15m", "stoch_d_15m", "stoch_k_1h", "stoch_d_1h",
    "stoch_k_4h", "stoch_d_4h",
    "wt1_3m", "wt2_3m", "wt1_15m", "wt2_15m", "wt1_1h", "wt2_1h", "wt1_4h", "wt2_4h", "wt1_D", "wt2_D",
    "wt_velocity_3m", "wt_velocity_15m", "wt_velocity_1h", "wt_velocity_4h", "wt_velocity_D",
    "wt_acceleration_3m", "wt_acceleration_15m", "wt_acceleration_1h",
    "rsi_15m", "rsi_1h", "rsi_4h",
    "mfi_15m", "mfi_1h", "mfi_4h",
    "atr_15m", "atr_1h", "atr_4h",
    "adx_1h", "adx_4h",
    "funding_rate_3m", "oi_3m", "oi_change_15m_3m", "oi_change_1h_3m",
    "ha_3m", "ha_15m", "ha_1h", "ha_4h",
    "div_reg_bull_wt_15m", "div_reg_bear_wt_15m", "div_reg_bull_wt_1h", "div_reg_bear_wt_1h",
    "squeeze_3m", "squeeze_15m", "squeeze_1h",
]


def _safe_npz(sym):
    p = NPZ_DIR / f"{sym}.npz"
    if not p.exists(): return None
    try: return np.load(p, mmap_mode="r")
    except Exception: return None


def _bar_idx(ts_arr, ts):
    return int(np.searchsorted(ts_arr, ts))


def _read_field_at(npz, field, idx):
    if field not in npz.files: return None
    try:
        v = npz[field][idx]
        v = v.item() if hasattr(v, "item") else v
        if isinstance(v, float) and not np.isfinite(v): return None
        return v
    except Exception:
        return None


def _trade_features(npz, ts_arr, trade, fields):
    en_idx = _bar_idx(ts_arr, int(trade.get("entry_ts", 0)))
    ex_idx = _bar_idx(ts_arr, int(trade.get("exit_ts", 0)))
    n = len(ts_arr)
    if en_idx >= n or ex_idx >= n or en_idx < 0:
        return None
    feats = {"_pnl": float(trade.get("pnl_pct", 0) or 0),
             "_side": trade.get("side", "LONG"),
             "_entry_reason": trade.get("entry_reason", ""),
             "_exit_reason": trade.get("exit_reason", ""),
             "_entry_ts": int(trade.get("entry_ts", 0)),
             "_dur_bars": ex_idx - en_idx,
             "_win": int(trade.get("pnl_pct", 0) > 0)}
    for f in fields:
        feats["en_" + f] = _read_field_at(npz, f, en_idx)
        feats["ex_" + f] = _read_field_at(npz, f, ex_idx)
    return feats


def _summary(values):
    nums = [v for v in values if isinstance(v, (int, float)) and v == v]
    if len(nums) < 3:
        return {"n": len(nums)}
    nums.sort()
    return {
        "n": len(nums),
        "min": nums[0],
        "p25": nums[len(nums)//4],
        "median": nums[len(nums)//2],
        "p75": nums[(3*len(nums))//4],
        "max": nums[-1],
        "mean": sum(nums)/len(nums),
        "std": statistics.pstdev(nums) if len(nums) > 1 else 0.0,
    }


def _cohen_d(a_vals, b_vals):
    """Effect size — how separated two distributions are."""
    a = [v for v in a_vals if isinstance(v, (int, float)) and v == v]
    b = [v for v in b_vals if isinstance(v, (int, float)) and v == v]
    if len(a) < 5 or len(b) < 5: return None
    ma, mb = sum(a)/len(a), sum(b)/len(b)
    sa = statistics.pstdev(a)
    sb = statistics.pstdev(b)
    pooled = math.sqrt((sa*sa + sb*sb) / 2)
    if pooled < 1e-12: return None
    return (ma - mb) / pooled


def analyze_run(run_id, sym, trades_dir, fields, missed_lookahead_bars, missed_gain_threshold):
    """Return analysis dict for one (run × symbol) pairing."""
    path = Path(trades_dir) / f"{run_id}__{sym}.jsonl"
    if not path.exists(): return None
    trades = []
    for line in path.read_text().splitlines():
        if line.strip():
            try: trades.append(json.loads(line))
            except Exception: pass
    if not trades: return None

    npz = _safe_npz(sym)
    if npz is None: return None
    ts_arr = npz["timestamps"]

    # Pull feature snapshots
    feats = []
    for t in trades:
        f = _trade_features(npz, ts_arr, t, fields)
        if f: feats.append(f)
    if not feats: return None

    winners = [f for f in feats if f["_win"]]
    losers = [f for f in feats if not f["_win"] and f["_pnl"] < 0]

    # ── 1. Per-cell (entry × exit reason) bleeding analysis ─────────────────
    cell_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "sum_pnl": 0.0, "pnls": []})
    for f in feats:
        k = (f["_entry_reason"], f["_exit_reason"])
        cell_stats[k]["trades"] += 1
        cell_stats[k]["wins"] += f["_win"]
        cell_stats[k]["sum_pnl"] += f["_pnl"]
        cell_stats[k]["pnls"].append(f["_pnl"])
    cells_out = []
    for (er, xr), s in cell_stats.items():
        if s["trades"] < 5: continue
        wr = s["wins"] / s["trades"]
        avg = s["sum_pnl"] / s["trades"]
        std = statistics.pstdev(s["pnls"]) if len(s["pnls"]) > 1 else 0
        sharpe_pt = avg / std if std > 0 else 0
        cells_out.append({
            "entry_reason": er, "exit_reason": xr,
            "trades": s["trades"], "win_rate": round(wr, 3),
            "total_gain": round(s["sum_pnl"], 2), "avg_pnl": round(avg, 4),
            "sharpe_pt": round(sharpe_pt, 3),
            "is_bleeding": (wr < 0.5 and avg < 0) or (avg < -0.05),
            "is_jewel": wr > 0.7 and avg > 0.1,
        })
    cells_out.sort(key=lambda c: c["sharpe_pt"])

    # ── 2. Winner vs loser indicator separability ──────────────────────────
    sep = []
    for f in fields:
        win_vals = [w["en_"+f] for w in winners]
        lose_vals = [l["en_"+f] for l in losers]
        d = _cohen_d(win_vals, lose_vals)
        if d is None or abs(d) < 0.2: continue   # only material effects
        sep.append({
            "field": f,
            "cohen_d": round(d, 3),
            "winner": _summary(win_vals),
            "loser": _summary(lose_vals),
        })
    sep.sort(key=lambda s: -abs(s["cohen_d"]))

    # ── 3. Missed entries: bars where price moved >threshold in next N bars
    #     AND no trade in this run was opened around then ────────────────────
    close = npz["close_3m"] if "close_3m" in npz.files else None
    missed = []
    if close is not None and len(close) > missed_lookahead_bars:
        n = len(close)
        # quick lookup of entry timestamps for this run
        entry_ts_set = set(int(t.get("entry_ts", 0)) for t in trades)
        # match within ±2 bars (~6 min)
        ahead = missed_lookahead_bars
        # vectorized future-max / future-min
        from numpy.lib.stride_tricks import sliding_window_view as _swv
        try:
            wins = _swv(close, ahead)
            fmax = wins.max(axis=-1)
            fmin = wins.min(axis=-1)
        except Exception:
            fmax = fmin = None
        sample = 0
        for i in range(0, n - ahead - 1, 5):  # every 5 bars to limit work
            if close[i] <= 0: continue
            future_high = float(fmax[i]) if fmax is not None else float(close[i:i+ahead].max())
            future_low = float(fmin[i]) if fmin is not None else float(close[i:i+ahead].min())
            up_pct = (future_high - close[i]) / close[i] * 100.0
            dn_pct = (future_low - close[i]) / close[i] * 100.0
            this_ts = int(ts_arr[i])
            # Check if any entry within ±540s (3 bars)
            had_entry = any(abs(this_ts - e) <= 540 for e in entry_ts_set if abs(e - this_ts) <= 1800)
            if had_entry: continue
            # Long opportunity?
            if up_pct >= missed_gain_threshold:
                feat = {"_side": "LONG", "_pnl_potential": up_pct, "_ts": this_ts}
                for f in fields:
                    feat[f] = _read_field_at(npz, f, i)
                missed.append(feat)
                sample += 1
            elif -dn_pct >= missed_gain_threshold:
                feat = {"_side": "SHORT", "_pnl_potential": -dn_pct, "_ts": this_ts}
                for f in fields:
                    feat[f] = _read_field_at(npz, f, i)
                missed.append(feat)
                sample += 1
            if sample >= 500: break  # cap per-symbol

    # Compare missed-entry indicator state vs taken-winners state
    missed_separability = []
    if missed and winners:
        for f in fields:
            taken_win = [w["en_"+f] for w in winners]
            miss_vals = [m[f] for m in missed]
            d = _cohen_d(taken_win, miss_vals)
            if d is None or abs(d) < 0.3: continue
            missed_separability.append({
                "field": f, "cohen_d": round(d, 3),
                "taken_winners": _summary(taken_win),
                "missed": _summary(miss_vals),
            })
        missed_separability.sort(key=lambda s: -abs(s["cohen_d"]))

    return {
        "run": run_id,
        "symbol": sym,
        "trade_count": len(trades),
        "winner_count": len(winners),
        "loser_count": len(losers),
        "missed_count": len(missed),
        "cells_bleeding": [c for c in cells_out if c["is_bleeding"]][:10],
        "cells_jewel":   [c for c in cells_out if c["is_jewel"]][:10],
        "winner_vs_loser_separators": sep[:15],
        "missed_vs_winner_separators": missed_separability[:15],
    }


def render_suggestions(analyses):
    """Turn analyses into a markdown report with novel-gate suggestions."""
    out = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    out.append(f"# Suggestion Report  \n*generated {now}*\n")
    out.append(f"Analyzed **{len(analyses)}** run × symbol pairings.\n")

    # ── Pattern 1: bleeding cells across runs ─────────────────────────────
    out.append("## 1. Bleeding cells — entry × exit pairs that lose money\n")
    out.append("| Run | Sym | Entry reason | Exit reason | N | WR | avgPnL | Sharpe/T |")
    out.append("|---|---|---|---|---:|---:|---:|---:|")
    bleed_rows = []
    for a in analyses:
        for c in a["cells_bleeding"]:
            bleed_rows.append((a["run"], a["symbol"], c))
    bleed_rows.sort(key=lambda r: r[2]["sharpe_pt"])
    for run, sym, c in bleed_rows[:20]:
        out.append(f"| {run} | {sym} | `{c['entry_reason'][:32]}` | `{c['exit_reason'][:32]}` | {c['trades']} | {c['win_rate']*100:.0f}% | {c['avg_pnl']:+.3f}% | {c['sharpe_pt']:+.3f} |")
    out.append("")

    # ── Pattern 2: winner-vs-loser indicator separability ────────────────
    out.append("## 2. Indicator values that separate WINNERS from LOSERS at entry\n")
    out.append("If `cohen_d` is large, the indicator at entry-time is systematically different between trades that won vs lost. Strong candidates for new gates.\n")
    for a in analyses:
        if not a["winner_vs_loser_separators"]: continue
        out.append(f"### {a['run']} — {a['symbol']}  ({a['winner_count']}W / {a['loser_count']}L)")
        out.append("| Field | Cohen d | Winner median | Loser median | Suggested gate |")
        out.append("|---|---:|---:|---:|---|")
        for s in a["winner_vs_loser_separators"][:10]:
            wm = s["winner"].get("median")
            lm = s["loser"].get("median")
            wm_s = f"{wm:.3f}" if wm is not None else "—"
            lm_s = f"{lm:.3f}" if lm is not None else "—"
            # propose a gate threshold midway between distributions
            if wm is not None and lm is not None:
                if s["cohen_d"] > 0:
                    gate = f"BLOCK if {s['field']} < {(wm+lm)/2:.3f}"
                else:
                    gate = f"BLOCK if {s['field']} > {(wm+lm)/2:.3f}"
            else:
                gate = ""
            out.append(f"| `{s['field']}` | {s['cohen_d']:+.2f} | {wm_s} | {lm_s} | {gate} |")
        out.append("")

    # ── Pattern 3: missed entries — what the script ISN'T taking ──────────
    out.append("## 3. Missed opportunities — bars no run took, that moved >threshold\n")
    out.append("If `cohen_d` is large, missed entries happen in indicator states distinguishable from where you DO take winning trades. Suggests entry conditions are too narrow.\n")
    for a in analyses:
        if not a["missed_vs_winner_separators"]: continue
        out.append(f"### {a['run']} — {a['symbol']}  ({a['missed_count']} missed opportunities sampled)")
        out.append("| Field | Cohen d | Taken-winner median | Missed median | Hypothesis |")
        out.append("|---|---:|---:|---:|---|")
        for s in a["missed_vs_winner_separators"][:10]:
            wm = s["taken_winners"].get("median")
            mm = s["missed"].get("median")
            wm_s = f"{wm:.3f}" if wm is not None else "—"
            mm_s = f"{mm:.3f}" if mm is not None else "—"
            if wm is not None and mm is not None:
                if s["cohen_d"] > 0:
                    h = f"Loosen entry: ALSO TAKE when {s['field']} < {wm:.3f} (currently only entered above)"
                else:
                    h = f"Loosen entry: ALSO TAKE when {s['field']} > {wm:.3f} (currently only entered below)"
            else:
                h = ""
            out.append(f"| `{s['field']}` | {s['cohen_d']:+.2f} | {wm_s} | {mm_s} | {h} |")
        out.append("")

    # ── Pattern 4: jewel cells — what's working great ─────────────────────
    out.append("## 4. Jewels — entry × exit pairs that win consistently\n")
    out.append("| Run | Sym | Entry reason | Exit reason | N | WR | avgPnL | Sharpe/T |")
    out.append("|---|---|---|---|---:|---:|---:|---:|")
    jewel_rows = []
    for a in analyses:
        for c in a["cells_jewel"]:
            jewel_rows.append((a["run"], a["symbol"], c))
    jewel_rows.sort(key=lambda r: -r[2]["sharpe_pt"])
    for run, sym, c in jewel_rows[:20]:
        out.append(f"| {run} | {sym} | `{c['entry_reason'][:32]}` | `{c['exit_reason'][:32]}` | {c['trades']} | {c['win_rate']*100:.0f}% | {c['avg_pnl']:+.3f}% | {c['sharpe_pt']:+.3f} |")
    out.append("")

    # ── Pattern 5: top-level summary ──────────────────────────────────────
    out.append("## 5. Summary\n")
    out.append(f"- Total bleeding cells (≥5 trades, sharpe<0): **{len(bleed_rows)}**")
    out.append(f"- Total jewel cells (WR>70%, avg>0.1%): **{len(jewel_rows)}**")
    out.append(f"- Per-run separator findings:")
    for a in analyses:
        out.append(f"  - **{a['run']}/{a['symbol']}**: {a['trade_count']} trades, "
                  f"{len(a['winner_vs_loser_separators'])} W/L separators, "
                  f"{len(a['missed_vs_winner_separators'])} missed-entry separators")
    return "\n".join(out)


def discover_targets(trades_dir):
    """Returns list of (run_id, symbol) tuples from JSONL filenames."""
    out = []
    for p in sorted(Path(trades_dir).glob("*__*.jsonl")):
        run, _, sym = p.stem.partition("__")
        if run and sym:
            out.append((run, sym))
    return out


def run_once(args):
    targets = discover_targets(args.trades_dir)
    # Filter
    if args.runs:
        wanted = set(args.runs.split(","))
        targets = [(r, s) for r, s in targets if r in wanted]
    if args.syms:
        wanted = set(s.upper() for s in args.syms.split(","))
        targets = [(r, s) for r, s in targets if s.upper() in wanted]
    if not targets:
        print(f"[suggestion_engine] no trade files matched in {args.trades_dir}")
        return
    print(f"[suggestion_engine] analyzing {len(targets)} run×sym pairings...")
    analyses = []
    for run, sym in targets:
        t0 = time.time()
        try:
            a = analyze_run(run, sym, args.trades_dir, ANALYSIS_FIELDS,
                            args.lookahead_bars, args.gain_threshold)
            if a:
                analyses.append(a)
                print(f"  [{run}/{sym}] {a['trade_count']} trades  W:{a['winner_count']} L:{a['loser_count']} miss:{a['missed_count']}  bleed_cells:{len(a['cells_bleeding'])}  W/L_sep:{len(a['winner_vs_loser_separators'])}  miss_sep:{len(a['missed_vs_winner_separators'])}  ({time.time()-t0:.1f}s)")
        except Exception as e:
            print(f"  [{run}/{sym}] ERROR: {e}")
    if not analyses:
        print("[suggestion_engine] nothing to write")
        return
    md = render_suggestions(analyses)
    out_path = Path(args.trades_dir) / f"SUGGESTIONS_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.md"
    out_path.write_text(md)
    # Also overwrite a stable "latest" symlink/copy
    latest = Path(args.trades_dir) / "SUGGESTIONS_latest.md"
    latest.write_text(md)
    print(f"[suggestion_engine] wrote {out_path}")
    print(f"[suggestion_engine] symlink {latest}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades-dir", default=str(DEFAULT_TRADES_DIR))
    ap.add_argument("--runs", help="comma-sep run ids (default: all)")
    ap.add_argument("--syms", help="comma-sep symbols (default: all)")
    ap.add_argument("--lookahead-bars", type=int, default=20, help="missed-entry: bars to look ahead (default 20 = 60min on 3m)")
    ap.add_argument("--gain-threshold", type=float, default=0.5, help="missed-entry: minimum %% move to count (default 0.5%%)")
    ap.add_argument("--loop", action="store_true", help="run forever, refreshing every --interval seconds")
    ap.add_argument("--interval", type=int, default=900, help="loop interval seconds (default 900 = 15 min)")
    args = ap.parse_args()
    if args.loop:
        print(f"[suggestion_engine] loop mode — every {args.interval}s")
        while True:
            t0 = time.time()
            try: run_once(args)
            except Exception as e: print(f"[suggestion_engine] iteration error: {e}")
            elapsed = time.time() - t0
            sleep_s = max(60, args.interval - int(elapsed))
            print(f"[suggestion_engine] sleeping {sleep_s}s")
            time.sleep(sleep_s)
    else:
        run_once(args)


if __name__ == "__main__":
    main()
