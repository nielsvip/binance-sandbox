"""v8_vec_structure_sweep.py — HH/HL/LH/LL multi-series structure sweep harness.

USER MANDATE 2026-05-17: backtest a NEW framework where entries require an
HTF breakout to a higher-high (LONG) or lower-low (SHORT), then an HL/LH retest
on a mid-TF, then >= N of {price, wt1, wt2, stoch_k, dc_basis} aligning on the
trigger TF. Exits fire when >= M series flip to opposite structure.

This is a STANDALONE sweep harness on top of v8_vec_sweep.py's load_npz +
vec_paths/structure_hh_hl.py compute. It does NOT touch live code, does NOT
modify NPZ precompute, and routes every Sharpe through metrics_guard.

Sub-floor samples (<48 crypto / <100 stocks / <1yr / <30 trades/sym) land in
data/_diagnostic/struct_hh_hl_*. Sample-floor-compliant runs land in
data/sweep_results/struct_hh_hl_*.

USAGE
=====
    # smoke test (Mac, 1 sym, narrow knobs, last 3 months)
    python v8_vec_structure_sweep.py --smoke --mode crypto --symbols BTC --start 2026-02-01

    # full sweep grid (S1)
    python v8_vec_structure_sweep.py --mode crypto --symbols BTC,ETH,SOL,XRP,AVAX,ADA,BNB,LINK,BAT,ALT,NOT,1000LUNC \\
        --start 2022-01-01 --grid default --account flz

CLAUDE.md HONORED
=================
- NO LIES MANDATE  : every sharpe via metrics_guard
- IMPOSTER BLOCK   : multi-sym pool only; per-sym tagged DIAGNOSTIC
- SAMPLE FLOOR     : sub-floor outputs explicitly tagged
- NEW STRATEGY     : default OFF in any live config; this script writes BACKTEST only
- locked file 'backtest_v8_precompute.py' NOT modified; structure computed on-the-fly
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics_guard
from vec_paths.structure_hh_hl import (
    compute_all_structure,
    compose_entry_exit_fires,
    summarize_pivots,
)
from v8_vec_sweep import load_npz as _full_load_npz, _max_dd_pct, _gain_pct, TradeEvent, _resolve_npz_path


def load_npz_slim(symbol: str, mode: str, start_ts: Optional[int] = None) -> Tuple[Dict[str, np.ndarray], np.ndarray]:
    """Memory-efficient NPZ loader — only retains the ~56 fields this sweep uses.

    Cuts RSS by ~13x vs full v8_vec_sweep.load_npz (which keeps all ~400 NPZ
    fields). S1 has been OOM-killing the full loader on 4 syms × 4yr crypto NPZs.

    Keeps: timestamps, close (base), high_{tf}, low_{tf}, close_{tf}, wt1_{tf},
    wt2_{tf}, stoch_k_{tf}, dc_high_{tf}, dc_low_{tf}, atr_{tf} for each TF.
    """
    if mode == "tradier":
        tfs = ("5m", "15m", "1h", "4h", "D")
    else:
        tfs = ("3m", "15m", "1h", "4h", "D", "W")
    needed_per_tf = ("high", "low", "close", "wt1", "wt2", "stoch_k", "dc_high", "dc_low", "atr", "bb_lower")
    keep = {f"{f}_{tf}" for tf in tfs for f in needed_per_tf}
    keep.add("close")
    keep.add("timestamps")
    p = _resolve_npz_path(symbol, mode)
    with np.load(p, allow_pickle=True) as z:
        ts_full = np.asarray(z["timestamps"])
        i0 = int(np.searchsorted(ts_full, start_ts, side="left")) if start_ts is not None else 0
        out: Dict[str, np.ndarray] = {}
        for k in z.files:
            if k not in keep:
                continue
            try:
                arr = np.asarray(z[k])
            except Exception:
                continue
            if arr.ndim == 0:
                continue
            if arr.shape[0] >= len(ts_full):
                out[k] = arr[i0:]
            else:
                out[k] = arr
        ts = ts_full[i0:]
    return out, ts


load_npz = load_npz_slim  # use slim by default in this module

# ════════════════════════════════════════════════════════════════════════════════
# Paths
# ════════════════════════════════════════════════════════════════════════════════
import platform
IS_SERVER = platform.system() == "Linux"
if IS_SERVER:
    BASE = Path("/home/niels/binance-sandbox")
else:
    BASE = Path("/Users/niels/Documents/binance")
SWEEP_RESULTS_DIR = BASE / "data" / "sweep_results"
DIAGNOSTIC_DIR = BASE / "data" / "_diagnostic"
HISTORY_OUT_DIR = BASE / "data" / "history_struct_sweep"

SAMPLE_FLOOR_CRYPTO = 48
SAMPLE_FLOOR_STOCKS = 100
MIN_TRADES_PER_SYM = 30
MIN_YEARS = 1.0


@dataclass
class StructCell:
    """One cell in the structure sweep grid."""
    breakout_tf: str = "D"
    retest_tf: str = "1h"
    trigger_tf: str = "15m"
    min_count: int = 4
    atr_retest_band: float = 0.5
    exit_flip_min: int = 2
    side_mode: str = "BOTH"
    round_trip_cost_pct: float = 0.10
    cooldown_bars: int = 5
    regime_persist_bars: int = 0
    exit_recent_window_bars: int = 0

    def key(self) -> str:
        suffix = ""
        if self.regime_persist_bars > 0:
            suffix += f"_rp{self.regime_persist_bars}"
        if self.exit_recent_window_bars > 0:
            suffix += f"_xw{self.exit_recent_window_bars}"
        if self.cooldown_bars != 5:
            suffix += f"_cd{self.cooldown_bars}"
        if self.round_trip_cost_pct != 0.10:
            suffix += f"_c{self.round_trip_cost_pct:g}"
        return (f"bk{self.breakout_tf}_rt{self.retest_tf}_tg{self.trigger_tf}"
                f"_mc{self.min_count}_ab{self.atr_retest_band:g}"
                f"_xf{self.exit_flip_min}_side{self.side_mode}{suffix}")


def default_grid(mode: str) -> List[StructCell]:
    """Evidence-driven default grid (2026-05-17).

    Crypto:
      - breakout: {D, W}            (W will be sparse — flagged DIAGNOSTIC if so)
      - retest:   {1h, 4h}
      - trigger:  {15m}             (3m too noisy on multi-yr; cheap to add later)
    Stocks:
      - breakout: {D} only          (no W in NPZ)
      - retest:   {15m, 1h}         (NEVER 4h-alone — 100.md Part 2: -$186 PnL)
      - trigger:  {15m, 1h}

    Shared:
      - min_count:        {3, 4}    (user 2026-05-17: "DEFINITELY can not be all 5")
      - atr_retest_band:  {0.3, 0.5, 1.0}
      - exit_flip_min:    {1, 2, 3}
      - side_mode:        {LONG_ONLY, SHORT_ONLY, BOTH}
    """
    if mode == "tradier":
        breakouts = ("D",)
        retests = ("15m", "1h")
        triggers = ("15m", "1h")
    else:
        breakouts = ("D",)        # W skipped in v1 — sparse on 4yr; add later
        retests = ("1h", "4h")
        triggers = ("15m",)
    cells: List[StructCell] = []
    for bk, rt, tg, mc, ab, xf, sm in itertools.product(
        breakouts, retests, triggers,
        (3, 4),
        (0.5, 1.0),
        (2, 3),
        ("BOTH", "LONG_ONLY", "SHORT_ONLY"),
    ):
        if bk == rt or rt == tg or bk == tg:
            continue
        cells.append(StructCell(
            breakout_tf=bk, retest_tf=rt, trigger_tf=tg,
            min_count=mc, atr_retest_band=ab, exit_flip_min=xf, side_mode=sm,
        ))
    return cells


def v2_focus_grid(mode: str) -> List[StructCell]:
    """v2 focused grid (2026-05-17 post-v1). Locks winning combo from v1
    (bk=D, rt=4h crypto / 1h stocks, tg=15m, mc=4, ab=1.0, LONG_ONLY) and
    sweeps the two knobs that v1 said matter most: exit_flip_min (higher is
    better) and cooldown_bars (5 was likely too aggressive). Also tests
    gross alpha (commission=0)."""
    rt = "1h" if mode == "tradier" else "4h"
    cells: List[StructCell] = []
    for xf, cd, cost, sm in itertools.product(
        (3, 4, 5),
        (5, 30, 100),
        (0.0, 0.10),
        ("LONG_ONLY", "BOTH"),
    ):
        cells.append(StructCell(
            breakout_tf="D", retest_tf=rt, trigger_tf="15m",
            min_count=4, atr_retest_band=1.0, exit_flip_min=xf,
            side_mode=sm, cooldown_bars=cd, round_trip_cost_pct=cost,
        ))
    return cells


def v3_grid(mode: str) -> List[StructCell]:
    """v3 (2026-05-17 post-v2). Tests two architectural improvements:
      1. exit_recent_window_bars > 0: exit fires only on NEW pivot events in
         the last K trigger-TF bars (not the carried struct that stays armed
         for hundreds of bars after a single bearish pivot).
      2. regime_persist_bars > 0: breakout TF struct must HOLD at HH/LL for
         N base-TF bars before entry arms — filters out fresh-pivot whipsaws.
    Locks v2 winning combo: bk=D, rt=4h crypto / 1h stocks, tg=15m, mc=4,
    ab=1.0, LONG_ONLY, cooldown=30. Cost both 0 and 0.10.
    """
    rt = "1h" if mode == "tradier" else "4h"
    cells: List[StructCell] = []
    for xw, rp, xf, cost in itertools.product(
        # Pivot-event window — smaller than typical pivot inter-arrival so it
        # detects SIMULTANEOUS flips, not "any recent flip" (which is ~always
        # true for 5 series). xw=1 = current bar only; xw=10 = 2.5 hr cluster.
        (0, 1, 3, 5, 10),
        (0, 200, 500, 1000),    # 0 = no regime filter; >0 = require streak
        (3, 4),
        (0.0, 0.10),
    ):
        # Skip the (0, 0) cell — that's already v2 best, not new info.
        if xw == 0 and rp == 0:
            continue
        cells.append(StructCell(
            breakout_tf="D", retest_tf=rt, trigger_tf="15m",
            min_count=4, atr_retest_band=1.0, exit_flip_min=xf,
            side_mode="LONG_ONLY", cooldown_bars=30,
            regime_persist_bars=rp, exit_recent_window_bars=xw,
            round_trip_cost_pct=cost,
        ))
    return cells


def v3_expansion_grid(mode: str) -> List[StructCell]:
    """v3 expansion (2026-05-17 post-v3). Locks v3 best knob shape and
    expands the region around the winning cells for the larger-sym run."""
    rt = "1h" if mode == "tradier" else "4h"
    cells: List[StructCell] = []
    for xf, rp, xw, sm, cost in itertools.product(
        (3, 4),
        (500, 1000, 2000),
        (1, 3),
        ("LONG_ONLY", "BOTH"),
        (0.0, 0.10),
    ):
        cells.append(StructCell(
            breakout_tf="D", retest_tf=rt, trigger_tf="15m",
            min_count=4, atr_retest_band=1.0, exit_flip_min=xf,
            side_mode=sm, cooldown_bars=30,
            regime_persist_bars=rp, exit_recent_window_bars=xw,
            round_trip_cost_pct=cost,
        ))
    return cells


def wide_grid(mode: str) -> List[StructCell]:
    """Full grid for follow-up sweep after validation passes. Includes W
    breakout (crypto), 0.3 ATR band tier, exit_flip_min=1 for fast exits."""
    if mode == "tradier":
        breakouts = ("D",)
        retests = ("15m", "1h")
        triggers = ("15m", "1h")
    else:
        breakouts = ("D", "W")
        retests = ("1h", "4h")
        triggers = ("15m",)
    cells: List[StructCell] = []
    for bk, rt, tg, mc, ab, xf, sm in itertools.product(
        breakouts, retests, triggers,
        (3, 4),
        (0.3, 0.5, 1.0),
        (1, 2, 3),
        ("BOTH", "LONG_ONLY", "SHORT_ONLY"),
    ):
        if bk == rt or rt == tg or bk == tg:
            continue
        cells.append(StructCell(
            breakout_tf=bk, retest_tf=rt, trigger_tf=tg,
            min_count=mc, atr_retest_band=ab, exit_flip_min=xf, side_mode=sm,
        ))
    return cells


def smoke_grid(mode: str) -> List[StructCell]:
    """6-cell minimal grid for Mac smoke test (1 sym, narrow params)."""
    if mode == "tradier":
        return [
            StructCell("D", "1h", "15m", 3, 0.5, 2, "BOTH"),
            StructCell("D", "1h", "15m", 4, 0.5, 2, "BOTH"),
            StructCell("D", "15m", "1h", 3, 0.5, 2, "LONG_ONLY"),
        ]
    return [
        StructCell("D", "1h", "15m", 3, 0.5, 2, "BOTH"),
        StructCell("D", "1h", "15m", 4, 0.5, 2, "BOTH"),
        StructCell("D", "4h", "15m", 3, 1.0, 2, "LONG_ONLY"),
        StructCell("D", "4h", "15m", 3, 1.0, 2, "SHORT_ONLY"),
        StructCell("W", "1h", "15m", 3, 0.5, 2, "BOTH"),
        StructCell("D", "1h", "15m", 4, 0.3, 1, "BOTH"),
    ]


# ════════════════════════════════════════════════════════════════════════════════
# Per-symbol simulator
# ════════════════════════════════════════════════════════════════════════════════

def simulate_struct_one_symbol(
    symbol: str,
    mode: str,
    cell: StructCell,
    *,
    start_ts: Optional[int] = None,
    structure_cache: Optional[Dict[Tuple[str, str], Dict[str, np.ndarray]]] = None,
    npz_cache: Optional[Tuple[Dict[str, np.ndarray], np.ndarray]] = None,
) -> Tuple[List[TradeEvent], List[float], Dict[str, Any]]:
    """Simulate trades for one (symbol, cell). Returns (events, trade_returns, meta)."""
    if npz_cache is None:
        npz, ts = load_npz(symbol, mode, start_ts=start_ts)
    else:
        npz, ts = npz_cache
    close = np.asarray(npz.get("close"))
    if close is None or close.size == 0:
        return [], [], {"reason": "empty_npz", "n_bars": 0}
    n = close.shape[0]
    if n < 500:
        return [], [], {"reason": "too_few_bars", "n_bars": n}
    structure = structure_cache if structure_cache is not None else compute_all_structure(npz, mode)
    fires = compose_entry_exit_fires(
        npz, structure,
        breakout_tf=cell.breakout_tf,
        retest_tf=cell.retest_tf,
        trigger_tf=cell.trigger_tf,
        min_count=cell.min_count,
        atr_retest_band=cell.atr_retest_band,
        exit_flip_min=cell.exit_flip_min,
        side_mode=cell.side_mode,
        regime_persist_bars=cell.regime_persist_bars,
        exit_recent_window_bars=cell.exit_recent_window_bars,
    )
    entry_long = fires["entry_long"]
    entry_short = fires["entry_short"]
    exit_long = fires["exit_long"]
    exit_short = fires["exit_short"]
    events: List[TradeEvent] = []
    trade_returns: List[float] = []
    pos = 0
    entry_price = 0.0
    entry_bar = -1
    cooldown_until = 0
    for i in range(n):
        if i < cooldown_until:
            continue
        px = float(close[i])
        if not np.isfinite(px) or px <= 0:
            continue
        if pos == 0:
            if entry_long[i]:
                pos = 1
                entry_price = px
                entry_bar = i
                events.append(TradeEvent(
                    ts=float(ts[i]), type="OPEN", qty=1.0, price=px, value=px,
                    reason=f"STRUCT_LONG_HH_BK{cell.breakout_tf}_HL_RT{cell.retest_tf}_TG{cell.trigger_tf}_mc{cell.min_count}",
                ))
            elif entry_short[i]:
                pos = -1
                entry_price = px
                entry_bar = i
                events.append(TradeEvent(
                    ts=float(ts[i]), type="OPEN", qty=1.0, price=px, value=px,
                    reason=f"STRUCT_SHORT_LL_BK{cell.breakout_tf}_LH_RT{cell.retest_tf}_TG{cell.trigger_tf}_mc{cell.min_count}",
                ))
        elif pos == 1:
            if exit_long[i]:
                pnl = _gain_pct(entry_price, px, True)
                events.append(TradeEvent(
                    ts=float(ts[i]), type="CLOSE", qty=1.0, price=px, value=px,
                    reason=f"STRUCT_EXIT_LONG_FLIP{cell.exit_flip_min}", pnl_pct=pnl,
                ))
                trade_returns.append(pnl)
                pos = 0
                entry_price = 0.0
                entry_bar = -1
                cooldown_until = i + cell.cooldown_bars
        elif pos == -1:
            if exit_short[i]:
                pnl = _gain_pct(entry_price, px, False)
                events.append(TradeEvent(
                    ts=float(ts[i]), type="CLOSE", qty=1.0, price=px, value=px,
                    reason=f"STRUCT_EXIT_SHORT_FLIP{cell.exit_flip_min}", pnl_pct=pnl,
                ))
                trade_returns.append(pnl)
                pos = 0
                entry_price = 0.0
                entry_bar = -1
                cooldown_until = i + cell.cooldown_bars
    if pos != 0:
        mark = float(close[-1])
        pnl = _gain_pct(entry_price, mark, pos == 1)
        events.append(TradeEvent(
            ts=float(ts[-1]), type="CLOSE", qty=1.0, price=mark, value=mark,
            reason="MTM_FINAL_BAR_NOLIES_RULE2", pnl_pct=pnl,
        ))
        trade_returns.append(pnl)
    if cell.round_trip_cost_pct != 0.0:
        trade_returns = [r - cell.round_trip_cost_pct for r in trade_returns]
    meta = {
        "n_bars": n,
        "first_ts": int(ts[0]) if n else 0,
        "last_ts": int(ts[-1]) if n else 0,
    }
    return events, trade_returns, meta


# ════════════════════════════════════════════════════════════════════════════════
# Multi-symbol cell runner
# ════════════════════════════════════════════════════════════════════════════════

def run_cell(
    cell: StructCell,
    symbols: List[str],
    mode: str,
    start_ts: Optional[int],
    npz_by_sym: Dict[str, Tuple[Dict[str, np.ndarray], np.ndarray]],
    structure_by_sym: Dict[str, Dict[Tuple[str, str], Dict[str, np.ndarray]]],
) -> Dict[str, Any]:
    returns_by_sym: Dict[str, List[float]] = {}
    total_trades = 0
    total_events: List[TradeEvent] = []
    n_bars_max = 0
    first_ts = None
    last_ts = None
    for sym in symbols:
        npz_pair = npz_by_sym.get(sym)
        if npz_pair is None:
            continue
        struct = structure_by_sym.get(sym)
        if struct is None:
            continue
        events, returns, meta = simulate_struct_one_symbol(
            sym, mode, cell, start_ts=start_ts,
            structure_cache=struct, npz_cache=npz_pair,
        )
        if not returns:
            continue
        returns_by_sym[sym] = returns
        total_trades += len(returns)
        total_events.extend(events)
        n_bars_max = max(n_bars_max, meta["n_bars"])
        if first_ts is None or meta["first_ts"] < first_ts:
            first_ts = meta["first_ts"]
        if last_ts is None or meta["last_ts"] > last_ts:
            last_ts = meta["last_ts"]
    n_syms_with_trades = sum(1 for v in returns_by_sym.values() if v)
    if not returns_by_sym:
        return {
            "cell": cell.key(), "trades": 0, "n_syms": 0,
            "skip": True, "reason": "zero_trades_any_sym",
        }
    years = ((last_ts - first_ts) / (365.25 * 24 * 3600)) if (first_ts and last_ts) else 0.0
    std = metrics_guard.standard_metric_set(returns_by_sym, years=years)
    all_returns = [r for rets in returns_by_sym.values() for r in rets]
    dd = _max_dd_pct(all_returns)
    out = {
        "cell": cell.key(),
        **asdict(cell),
        "pool_sharpe": std["pool_sharpe"],
        "sym_sharpe": std["sym_sharpe"],
        "avg_gain_trade": std["avg_gain_trade"],
        "gain_per_yr": std["gain_per_yr"],
        "gain_sym_yr": std["gain_sym_yr"],
        "max_dd_pct": dd,
        "trades": int(total_trades),
        "n_syms": int(n_syms_with_trades),
        "years": round(years, 3),
        "events": total_events,
        "returns_by_sym": returns_by_sym,
    }
    return out


# ════════════════════════════════════════════════════════════════════════════════
# Sweep driver
# ════════════════════════════════════════════════════════════════════════════════

def run_sweep(
    *,
    mode: str,
    symbols: List[str],
    start: str,
    grid: List[StructCell],
    account: str = "struct_sweep",
    out_label: Optional[str] = None,
) -> Dict[str, Any]:
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ts = int(start_dt.timestamp())
    npz_by_sym: Dict[str, Tuple[Dict[str, np.ndarray], np.ndarray]] = {}
    structure_by_sym: Dict[str, Dict[Tuple[str, str], Dict[str, np.ndarray]]] = {}
    t0 = time.time()
    for sym in symbols:
        try:
            npz, ts = load_npz(sym, mode, start_ts=start_ts)
        except FileNotFoundError as e:
            print(f"  [SKIP] {sym}: {e}", file=sys.stderr)
            continue
        except Exception as e:
            print(f"  [SKIP] {sym}: load failed: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if "close" not in npz or np.asarray(npz["close"]).size < 500:
            print(f"  [SKIP] {sym}: insufficient bars", file=sys.stderr)
            continue
        npz_by_sym[sym] = (npz, ts)
        structure_by_sym[sym] = compute_all_structure(npz, mode)
    t_npz = time.time() - t0
    print(f"[NPZ+STRUCT] loaded {len(npz_by_sym)} sym in {t_npz:.1f}s "
          f"({sum(int(np.asarray(p[0]['close']).shape[0]) for p in npz_by_sym.values()):,} total bars)")
    if not npz_by_sym:
        print("[ABORT] no symbols loaded", file=sys.stderr)
        return {"error": "no_symbols"}
    # Pivot summary on first symbol (sanity check)
    first_sym = next(iter(structure_by_sym))
    pivots = summarize_pivots(structure_by_sym[first_sym])
    print(f"[PIVOT SUMMARY {first_sym}] sample: " + ", ".join(
        f"{k}={v}" for k, v in list(pivots.items())[:6]
    ))
    rows: List[Dict[str, Any]] = []
    for i, cell in enumerate(grid, 1):
        t_cell = time.time()
        result = run_cell(cell, list(npz_by_sym.keys()), mode, start_ts, npz_by_sym, structure_by_sym)
        elapsed = time.time() - t_cell
        if result.get("skip"):
            print(f"[CELL {i}/{len(grid)} {cell.key()}] SKIP {result.get('reason')} ({elapsed:.1f}s)")
            continue
        result["elapsed_s"] = round(elapsed, 2)
        # Pop the heavy intermediates before CSV row
        events = result.pop("events", [])
        returns_by_sym = result.pop("returns_by_sym", {})
        rows.append(result)
        tier = metrics_guard.tier_name(result["pool_sharpe"])
        floor = SAMPLE_FLOOR_CRYPTO if mode == "crypto" else SAMPLE_FLOOR_STOCKS
        diag = result["n_syms"] < floor or result["years"] < MIN_YEARS or (result["trades"] / max(result["n_syms"], 1)) < MIN_TRADES_PER_SYM
        tag = "[DIAGNOSTIC]" if diag else "[REAL]"
        print(f"[CELL {i}/{len(grid)} {cell.key()}] {tag} {tier} "
              f"pool_sharpe={result['pool_sharpe']:+.4f} "
              f"avg_gain={result['avg_gain_trade']:+.3f}%/tr "
              f"trades={result['trades']} n_syms={result['n_syms']} "
              f"dd={result['max_dd_pct']:.2f}% ({elapsed:.1f}s)")
    if not rows:
        print("[ABORT] zero cells produced trades", file=sys.stderr)
        return {"error": "all_cells_empty"}
    # Decide output dir based on sample-floor of BEST cell
    best = max(rows, key=lambda r: r["pool_sharpe"])
    floor = SAMPLE_FLOOR_CRYPTO if mode == "crypto" else SAMPLE_FLOOR_STOCKS
    is_diagnostic = (best["n_syms"] < floor or best["years"] < MIN_YEARS
                     or (best["trades"] / max(best["n_syms"], 1)) < MIN_TRADES_PER_SYM)
    out_dir = DIAGNOSTIC_DIR if is_diagnostic else SWEEP_RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_run = int(time.time())
    label = out_label or f"struct_hh_hl_{mode}_{ts_run}"
    csv_path = out_dir / f"{label}.csv"
    # Write canonical rows through metrics_guard.write_sharpe_row (one row per cell)
    for r in rows:
        row_for_guard = {
            "iter": r["cell"],
            "pool_sharpe": r["pool_sharpe"],
            "sym_sharpe": r["sym_sharpe"],
            "avg_gain_trade": r["avg_gain_trade"],
            "gain_per_yr": r["gain_per_yr"],
            "gain_sym_yr": r["gain_sym_yr"],
            "max_dd_pct": r["max_dd_pct"],
            "trades": r["trades"],
            "n_syms": r["n_syms"],
            "years": r["years"],
            "breakout_tf": r["breakout_tf"],
            "retest_tf": r["retest_tf"],
            "trigger_tf": r["trigger_tf"],
            "min_count": r["min_count"],
            "atr_retest_band": r["atr_retest_band"],
            "exit_flip_min": r["exit_flip_min"],
            "side_mode": r["side_mode"],
            "elapsed_s": r["elapsed_s"],
            "tier": metrics_guard.tier_name(r["pool_sharpe"]),
        }
        try:
            metrics_guard.write_sharpe_row(csv_path, row_for_guard, mode=mode, append=True)
        except metrics_guard.FakeMetricRefused as e:
            print(f"[REFUSED] {r['cell']}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[ERROR] write_sharpe_row {r['cell']}: {e}", file=sys.stderr)
    summary_path = out_dir / f"{label}_summary.json"
    summary = {
        "mode": mode,
        "start": start,
        "symbols": symbols,
        "n_cells": len(rows),
        "best_cell": best["cell"],
        "best_pool_sharpe": best["pool_sharpe"],
        "best_avg_gain_trade": best["avg_gain_trade"],
        "best_trades": best["trades"],
        "best_n_syms": best["n_syms"],
        "diagnostic": is_diagnostic,
        "sample_floor_required": floor,
        "csv_path": str(csv_path),
        "ts": ts_run,
    }
    with summary_path.open("w") as fh:
        json.dump(summary, fh, indent=2)
    print(f"\n[DONE] {len(rows)} cells written → {csv_path}")
    print(f"[DONE] summary → {summary_path}")
    canon_line = (
        f"pool_sharpe={best['pool_sharpe']:+.4f} ({metrics_guard.tier_name(best['pool_sharpe'])}) | "
        f"sym_sharpe={best['sym_sharpe']:+.4f} | "
        f"avg_gain_trade={best['avg_gain_trade']:+.3f}%/trade | "
        f"gain_per_yr={best['gain_per_yr']:+.2f}%/yr | "
        f"gain_sym_yr={best['gain_sym_yr']:+.4f}%/sym/yr | "
        f"trades={best['trades']} | dd={best['max_dd_pct']:.2f}% | "
        f"n_syms={best['n_syms']} | years={best['years']}"
    )
    if is_diagnostic:
        canon_line = f"[DIAGNOSTIC · n_syms={best['n_syms']} · years={best['years']}] " + canon_line
    print(f"[BEST] {best['cell']}")
    print(f"[BEST] {canon_line}")
    return summary


# ════════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="HH/HL/LH/LL multi-series structure sweep")
    ap.add_argument("--mode", choices=("crypto", "tradier"), required=True)
    ap.add_argument("--symbols", required=True,
                    help="Comma-separated. Crypto bare: BTC,ETH,SOL. Stocks: AAPL,MSFT.")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--grid", choices=("default", "smoke", "wide", "v2_focus", "v3", "v3_expansion"), default="default")
    ap.add_argument("--smoke", action="store_true",
                    help="Use 6-cell smoke grid (Mac small test).")
    ap.add_argument("--account", default="struct_sweep")
    ap.add_argument("--out-label", default=None)
    from vector_mandatory_coverage import add_coverage_claim_arguments, enforce_coverage_claim
    add_coverage_claim_arguments(ap)
    args = ap.parse_args()
    coverage_contract = enforce_coverage_claim(args, runner="v8_vec_structure_sweep.py")
    print(f"V8_VECTOR_GROUND_RULE: {coverage_contract['coverage_status']} shortlist_sha256={coverage_contract['shortlist_sha256']}", flush=True)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if args.smoke or args.grid == "smoke":
        grid = smoke_grid(args.mode)
    elif args.grid == "wide":
        grid = wide_grid(args.mode)
    elif args.grid == "v2_focus":
        grid = v2_focus_grid(args.mode)
    elif args.grid == "v3":
        grid = v3_grid(args.mode)
    elif args.grid == "v3_expansion":
        grid = v3_expansion_grid(args.mode)
    else:
        grid = default_grid(args.mode)
    print(f"[CFG] mode={args.mode} symbols={len(symbols)} start={args.start} cells={len(grid)}")
    run_sweep(
        mode=args.mode, symbols=symbols, start=args.start, grid=grid,
        account=args.account, out_label=args.out_label,
    )


if __name__ == "__main__":
    main()
