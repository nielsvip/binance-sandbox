#!/usr/bin/env python3
"""
SCALP_V2 Fast Backtest — minimal harness around htf_breakout_scalper.

Skips the full V8 engine (which spends most time on the live infrastructure
that scalping doesn't need) and instead iterates NPZ bars directly, calling
the SAME live `check_scalp_v2_entry` and `check_scalp_v2_exit` functions.
This is NOT a reimplementation — it is a thin loop around the live strategy
module, which is the only V8-permissible shortcut here.

Per-symbol cost: ~5-15 sec for 4yr of 3m bars (≈400k rows) on a single core.
Per-combo cost (48 symbols): ~4-12 min sequential, ~1-3 min with workers.
Full 288-combo sweep: ~3-12 hours total. Compare to ~6 days on raw V8.

Usage:
    # Full sweep — 288 combos × 48 symbols × 4yr (~3-6h)
    python3 scalp_v2_fast_backtest.py --start 2022-01-01

    # Quick smoke test — 8 variants × 12 symbols (~5min)
    python3 scalp_v2_fast_backtest.py --quick

    # Single combo
    python3 scalp_v2_fast_backtest.py --variant V1_WT_CONFIRM --max-hold 30 --dc-tfs 15m,1h
"""
import argparse
import itertools
import json
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

BASE_PATH = Path("/Users/niels/Documents/binance")
NPZ_DIR = BASE_PATH / "backtest_v4" / "indicators"  # crypto v4 has 4yr 3m data
SWEEP_DIR = BASE_PATH / "backtest_v8" / "sweeps"
SWEEP_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_PATH))
from htf_breakout_scalper import (check_scalp_v2_entry, check_scalp_v2_exit,
                                  VARIANTS, SCALP_V2_REASON_PREFIX)

SYMBOLS_48 = json.load(open(BASE_PATH / "backtest_48_symbols.json"))
SYMBOLS_QUICK = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "LINKUSDT",
                 "DOGEUSDT", "MATICUSDT", "DOTUSDT", "ATOMUSDT", "NEARUSDT",
                 "TRXUSDT", "LTCUSDT"]

# Fields the strategy module reads — only load these from NPZ
NEEDED_FIELDS = {
    "current_price",  # synthetic, set per bar from close
    "dc_high_15m_prev", "dc_high_1h_prev", "dc_high_4h_prev",
    "dc_low_15m_prev", "dc_low_1h_prev", "dc_low_4h_prev",
    "dc_high_15m", "dc_high_1h", "dc_high_4h",
    "dc_low_15m", "dc_low_1h", "dc_low_4h",
    "dc_high_3m", "dc_low_3m",
    "wt1_3m", "wt2_3m", "wt1_3m_prev", "wt2_3m_prev", "wt_cross_3m",
    "high_3m", "high_3m_prev", "low_3m", "low_3m_prev",
    "ha_3m", "close_3m",
}


@dataclass
class FakeConfig:
    """Stand-in for the live config object that htf_breakout_scalper reads."""
    SCALP_MODE: bool = True
    SCALP_ACCOUNTS: list = field(default_factory=lambda: ["ang"])
    SCALP_V2_VARIANT: str = "V1_WT_CONFIRM"
    SCALP_V2_DC_HTF_LIST: list = field(default_factory=lambda: ["15m", "1h"])
    SCALP_V2_DC_HTF_REQUIRE_ALL: bool = True
    SCALP_V2_MAX_CONCURRENT: int = 5
    SCALP_V2_MAX_HOLD_MINUTES: float = 30.0


@dataclass
class FakePosition:
    """Stand-in for the live Position dataclass."""
    positionAmt: float = 0.0
    augment_reason: str = ""
    opened_at: Optional[datetime] = None
    entry_price: float = 0.0
    side: str = "LONG"


def load_npz(symbol: str) -> Optional[Dict[str, np.ndarray]]:
    """Load only the fields we need from a single symbol's NPZ."""
    path = NPZ_DIR / f"{symbol}.npz"
    if not path.exists():
        return None
    with np.load(path, allow_pickle=True) as f:
        keys = set(f.files)
        present = NEEDED_FIELDS & keys
        out = {}
        for k in present:
            try:
                out[k] = f[k][:].astype(np.float32) if f[k].dtype != object else np.array([str(v) for v in f[k]])
            except Exception:
                out[k] = f[k][:]
        # Need timestamps if available
        for k in ("ts", "timestamp", "open_time"):
            if k in keys:
                try:
                    out["_ts"] = f[k][:].astype(np.float64)
                    break
                except Exception:
                    pass
        # Length sanity
        if "close_3m" not in out:
            return None
    return out


def simulate_symbol(symbol: str, data: Dict[str, np.ndarray], cfg: FakeConfig,
                   side: str, start_idx: int = 0) -> List[Dict]:
    """Simulate one side (LONG or SHORT) on one symbol's bar series.
    Returns list of trade dicts: {entry_idx, exit_idx, entry_px, exit_px, pnl_pct, reason}.
    """
    n = len(data["close_3m"])
    if n == 0:
        return []
    closes = data["close_3m"]
    is_long = side == "LONG"
    pk_suffix = "_LONG" if is_long else "_SHORT"
    position_key = f"ang:{symbol}{pk_suffix}"

    trades = []
    pos = FakePosition(side=side)
    bar_indicators = {}

    def fill_indicators(idx: int):
        bar_indicators.clear()
        for k, arr in data.items():
            if k in ("_ts",):
                continue
            try:
                bar_indicators[k] = float(arr[idx])
            except (TypeError, ValueError, IndexError):
                pass
        bar_indicators["current_price"] = float(closes[idx])

    cooldown_until = -1  # bar idx until which we won't reopen

    for i in range(start_idx, n):
        fill_indicators(i)
        px = float(closes[i])
        if px <= 0:
            continue

        if pos.positionAmt > 0:
            # Currently in a position — check exit
            decision = check_scalp_v2_exit(position_key, bar_indicators, px, pos, cfg)
            if decision:
                # Close
                if is_long:
                    pnl = (px - pos.entry_price) / pos.entry_price * 100.0
                else:
                    pnl = (pos.entry_price - px) / pos.entry_price * 100.0
                trades.append({
                    "symbol": symbol, "side": side,
                    "entry_idx": pos_entry_idx, "exit_idx": i,
                    "entry_px": pos.entry_price, "exit_px": px,
                    "pnl_pct": pnl, "reason": decision["reason"][:60],
                    "bars_held": i - pos_entry_idx,
                })
                pos = FakePosition(side=side)
                cooldown_until = i + 5  # 5 bars = 15 min cooldown after exit
                continue
            # Max hold safety net (in bars)
            max_bars = max(1, int(cfg.SCALP_V2_MAX_HOLD_MINUTES / 3))  # 3min bars
            if i - pos_entry_idx >= max_bars:
                if is_long:
                    pnl = (px - pos.entry_price) / pos.entry_price * 100.0
                else:
                    pnl = (pos.entry_price - px) / pos.entry_price * 100.0
                trades.append({
                    "symbol": symbol, "side": side,
                    "entry_idx": pos_entry_idx, "exit_idx": i,
                    "entry_px": pos.entry_price, "exit_px": px,
                    "pnl_pct": pnl, "reason": "MAX_HOLD_BARS",
                    "bars_held": i - pos_entry_idx,
                })
                pos = FakePosition(side=side)
                cooldown_until = i + 5
        else:
            if i < cooldown_until:
                continue
            # Empty — check entry
            decision = check_scalp_v2_entry(symbol, position_key, bar_indicators, px, pos, "ang", cfg)
            if decision:
                pos.positionAmt = 1.0
                pos.entry_price = px
                pos.augment_reason = decision["reason"]
                pos_entry_idx = i

    return trades


def run_combo(combo: Dict, symbols: List[str], start_date: str) -> Dict:
    """Run one parameter combo across all symbols."""
    cfg = FakeConfig(
        SCALP_V2_VARIANT=combo["variant"],
        SCALP_V2_DC_HTF_LIST=list(combo["dc_tf_list"]),
        SCALP_V2_DC_HTF_REQUIRE_ALL=bool(combo["require_all"]),
        SCALP_V2_MAX_HOLD_MINUTES=float(combo["max_hold_min"]),
    )
    all_trades = []
    t0 = time.time()
    for sym in symbols:
        data = load_npz(sym)
        if not data:
            continue
        # Run BOTH sides — strategy decides which fires per bar
        for side in ("LONG", "SHORT"):
            trades = simulate_symbol(sym, data, cfg, side)
            all_trades.extend(trades)
    elapsed = time.time() - t0

    # Compute summary stats
    if not all_trades:
        return {**combo, "trades": 0, "wins": 0, "losses": 0, "wr_pct": 0,
                "total_pnl_pct": 0.0, "avg_pnl_pct": 0.0, "sharpe": 0.0,
                "max_dd_pct": 0.0, "elapsed_s": round(elapsed, 1), "status": "no_trades"}

    pnls = np.array([t["pnl_pct"] for t in all_trades]) / 100.0  # convert to fractions
    wins = int((pnls > 0).sum())
    losses = int((pnls <= 0).sum())
    avg = float(pnls.mean())
    std = float(pnls.std()) if len(pnls) > 1 else 1e-9
    # Per-trade Sharpe (not annualized — these are scalp trades, not days).
    # Comparable across combos for ranking purposes.
    sharpe = avg / std if std > 0 else 0.0
    # Profit factor — the scalper's most meaningful metric
    gross_win = float(pnls[pnls > 0].sum()) if (pnls > 0).any() else 0.0
    gross_loss = abs(float(pnls[pnls < 0].sum())) if (pnls < 0).any() else 1e-9
    pf = gross_win / gross_loss if gross_loss > 0 else 0.0
    # Compounded return — assume each trade reinvests notional from a fixed pool
    # of size 1.0. This caps the metric at sane values regardless of trade count.
    compound = float(np.prod(1.0 + pnls) - 1.0) * 100.0
    # Equity-curve max DD (compounded)
    eq = np.cumprod(1.0 + pnls)
    peak = np.maximum.accumulate(eq)
    dd = float((1.0 - eq / peak).max()) * 100.0 if len(eq) > 0 else 0.0
    return {
        **combo,
        "trades": len(all_trades),
        "wins": wins, "losses": losses,
        "wr_pct": round(wins / len(all_trades) * 100, 1),
        "compound_pct": round(compound, 2),
        "avg_pnl_pct": round(avg * 100, 4),
        "sharpe": round(float(sharpe), 4),
        "pf": round(float(pf), 3),
        "max_dd_pct": round(float(dd), 2),
        "elapsed_s": round(elapsed, 1),
        "status": "ok",
    }


# Reuse grid definitions from the V8 sweep driver
DEFAULT_GRID = {
    "variant": list(VARIANTS),
    "dc_tf_list": [
        ("15m", "1h"),
        ("15m",),
        ("1h",),
        ("15m", "1h", "4h"),
    ],
    "require_all": [True],
    "max_hold_min": [15, 30, 60],
    "max_concurrent": [5],
}

EXPLORE_GRID = {
    "variant": list(VARIANTS),
    "dc_tf_list": [
        ("15m", "1h"),
        ("15m",),
        ("1h",),
        ("15m", "1h", "4h"),
        ("3m", "15m"),
    ],
    "require_all": [True, False],
    "max_hold_min": [5, 15, 30, 60, 120],
    "max_concurrent": [5],
}


def build_combos(grid: dict, variants_filter=None) -> list:
    keys = list(grid.keys())
    out = []
    for vals in itertools.product(*[grid[k] for k in keys]):
        combo = dict(zip(keys, vals))
        if variants_filter and combo["variant"] not in variants_filter:
            continue
        combo["dc_tf_list"] = list(combo["dc_tf_list"])
        out.append(combo)
    return out


def combo_name(c: Dict) -> str:
    return f"{c['variant']}|tf={'+'.join(c['dc_tf_list'])}|req={'ALL' if c['require_all'] else 'ANY'}|hold{c['max_hold_min']}"


def print_report(results: List[Dict], top_n: int = 25):
    ok = [r for r in results if r.get("status") == "ok" and r.get("trades", 0) > 0]
    print(f"\n{'='*120}")
    print(f"  SCALP_V2 FAST BACKTEST REPORT — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*120}")
    print(f"  Total combos: {len(results)} | OK with trades: {len(ok)} | Empty: {len(results) - len(ok)}")
    if not ok:
        return
    print(f"\n  TOP {top_n} BY PROFIT FACTOR:")
    print(f"  {'#':<3} {'PF':>7} {'Sharpe':>8} {'AvgPnL%':>9} {'WR%':>6} {'Trades':>7} {'Compd%':>8} {'MaxDD%':>7}  Combo")
    print(f"  {'-'*116}")
    for i, r in enumerate(sorted(ok, key=lambda x: -x.get("pf", 0))[:top_n], 1):
        print(f"  {i:<3} {r['pf']:>7.3f} {r['sharpe']:>+8.4f} {r['avg_pnl_pct']:>+9.4f} {r['wr_pct']:>5.1f}% {r['trades']:>7} {r['compound_pct']:>+8.2f} {r['max_dd_pct']:>7.2f}  {combo_name(r)}")
    print(f"\n  TOP 10 BY SHARPE (per-trade):")
    for i, r in enumerate(sorted(ok, key=lambda x: -x["sharpe"])[:10], 1):
        print(f"  {i:<3} {r['pf']:>7.3f} {r['sharpe']:>+8.4f} {r['avg_pnl_pct']:>+9.4f} {r['wr_pct']:>5.1f}% {r['trades']:>7}  {combo_name(r)}")
    print(f"\n  TOP 10 BY AVG PnL %:")
    for i, r in enumerate(sorted(ok, key=lambda x: -x.get("avg_pnl_pct", 0))[:10], 1):
        print(f"  {i:<3} {r['pf']:>7.3f} {r['sharpe']:>+8.4f} {r['avg_pnl_pct']:>+9.4f} {r['wr_pct']:>5.1f}% {r['trades']:>7}  {combo_name(r)}")
    # Per-variant aggregate
    by_var = defaultdict(list)
    for r in ok:
        by_var[r["variant"]].append((r["pf"], r["sharpe"], r["avg_pnl_pct"]))
    print(f"\n  PER-VARIANT (mean across all parameter combos):")
    print(f"    {'Variant':<24} {'PF':>7} {'Sharpe':>9} {'AvgPnL':>10} {'BestPF':>8}")
    rows = []
    for v in by_var.keys():
        s = by_var[v]
        rows.append((v, sum(x[0] for x in s)/len(s), sum(x[1] for x in s)/len(s),
                     sum(x[2] for x in s)/len(s), max(x[0] for x in s)))
    for v, mpf, mshrp, mavg, bpf in sorted(rows, key=lambda x: -x[1]):
        print(f"    {v:<24} {mpf:>7.3f} {mshrp:>+9.4f} {mavg:>+10.4f} {bpf:>8.3f}")
    print(f"{'='*120}\n")


def _run_combo_worker(args):
    return run_combo(*args)


def main():
    p = argparse.ArgumentParser(description="SCALP_V2 fast backtest sweep")
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--quick", action="store_true", help="8 variants × default params (8 combos)")
    p.add_argument("--explore", action="store_true", help="Deep EXPLORE_GRID")
    p.add_argument("--quick-symbols", action="store_true", help="12 symbols instead of 48")
    p.add_argument("--variants", type=str, default="")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()

    symbols = SYMBOLS_QUICK if args.quick_symbols else SYMBOLS_48
    variants_filter = [v.strip() for v in args.variants.split(",") if v.strip()] if args.variants else None
    if args.quick:
        combos = [{"variant": v, "dc_tf_list": ["15m", "1h"], "require_all": True,
                   "max_hold_min": 30, "max_concurrent": 5} for v in (variants_filter or VARIANTS)]
    elif args.explore:
        combos = build_combos(EXPLORE_GRID, variants_filter)
    else:
        combos = build_combos(DEFAULT_GRID, variants_filter)
    if args.limit > 0:
        combos = combos[:args.limit]

    print(f"\n{'='*120}")
    print(f"  SCALP_V2 FAST BACKTEST")
    print(f"  Symbols: {len(symbols)} ({'QUICK_12' if args.quick_symbols else 'FULL_48'})")
    print(f"  Combos:  {len(combos)} | Workers: {args.workers}")
    print(f"  NPZ:     {NPZ_DIR}")
    print(f"  Start:   {args.start}")
    print(f"{'='*120}\n")

    ts_run = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    progress_path = SWEEP_DIR / f"scalp_v2_fast_progress_{ts_run}.json"

    results = []
    t_start = time.time()
    tasks = [(c, symbols, args.start) for c in combos]

    if args.workers <= 1:
        for i, t in enumerate(tasks, 1):
            r = run_combo(*t)
            results.append(r)
            print(f"  [{i}/{len(tasks)}] {r['status']:<10} PF={r.get('pf',0):>5.3f} S={r['sharpe']:>+.4f} avg={r.get('avg_pnl_pct',0):>+.4f}% WR={r.get('wr_pct',0):>5.1f}% n={r['trades']:>5} ({r['elapsed_s']:>5.0f}s) | {combo_name(r)}")
            with open(progress_path, "w") as f:
                json.dump({"completed": i, "total": len(tasks), "results": results}, f, indent=2)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(_run_combo_worker, t): t for t in tasks}
            for i, fut in enumerate(as_completed(futures), 1):
                r = fut.result()
                results.append(r)
                print(f"  [{i}/{len(tasks)}] {r['status']:<10} PF={r.get('pf',0):>5.3f} S={r['sharpe']:>+.4f} avg={r.get('avg_pnl_pct',0):>+.4f}% WR={r.get('wr_pct',0):>5.1f}% n={r['trades']:>5} ({r['elapsed_s']:>5.0f}s) | {combo_name(r)}")
                with open(progress_path, "w") as f:
                    json.dump({"completed": i, "total": len(tasks), "results": results}, f, indent=2)

    print_report(results)
    out_path = SWEEP_DIR / f"scalp_v2_fast_sweep_{ts_run}.json"
    with open(out_path, "w") as f:
        json.dump({"args": vars(args), "results": results,
                   "elapsed_total_s": round(time.time() - t_start, 1)}, f, indent=2)
    print(f"  Saved: {out_path}\n")


if __name__ == "__main__":
    main()
