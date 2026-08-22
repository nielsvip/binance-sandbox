# -*- coding: utf-8 -*-
"""per_sym_vec_engine_stocks — Unified UVE-aligned stocks per-symbol variant sweep engine.
"""
from __future__ import annotations
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from v12_wide_engine import load_npz, SweepConfig
from uve_engine import simulate_uve

def _run_variant_task_worker_stocks(args):
    sym, start_ts, variant, years_back = args
    try:
        npz, ts = load_npz(sym, "tradier", start_ts=start_ts)
    except Exception:
        return None
    cfg = SweepConfig()
    for k, v in variant.items():
        if k.startswith("_") or k == "NOLOSS_ENABLED":
            continue
        try:
            setattr(cfg, k, v)
        except Exception:
            pass
    try:
        long_res = simulate_uve(npz, is_long=True, mode="tradier", config=cfg)
        long_events, long_rets = long_res["events"], long_res["returns"]
    except Exception:
        long_events, long_rets = [], []
    try:
        short_res = simulate_uve(npz, is_long=False, mode="tradier", config=cfg)
        short_events, short_rets = short_res["events"], short_res["returns"]
    except Exception:
        short_events, short_rets = [], []
    long_ets = [int(ev.ts) for ev in long_events if ev.type in ("CLOSE", "REDUCE", "HEDGE_CLOSE")]
    short_ets = [int(ev.ts) for ev in short_events if ev.type in ("CLOSE", "REDUCE", "HEDGE_CLOSE")]
    long_rets = long_rets[:len(long_ets)]
    short_rets = short_rets[:len(short_ets)]
    pnls = np.array(long_rets + short_rets, dtype=np.float32)
    ets = np.array(long_ets + short_ets, dtype=np.int64)
    if len(pnls) > 0:
        sort_idx = np.argsort(ets)
        pnls, ets = pnls[sort_idx], ets[sort_idx]
    trades_n = len(pnls)
    ref_ts = int(ts[-1]) if len(ts) else 0
    yrs_for_pyr = max(0.01, float(years_back))
    if trades_n >= 2:
        std = float(pnls.std())
        pool_s = float(pnls.mean() / std) if std > 1e-12 else 0.0
        decay = 1.0 - np.clip((ref_ts - ets) / (365.25 * 86400.0 * 2.0), 0.0, 1.0)
        decay_sum = decay.sum()
        tw_s = float((pnls * decay).sum() / (std * decay_sum)) if std > 1e-12 and decay_sum > 1e-12 else 0.0
        cum_ret, max_dd, peak = 0.0, 0.0, 0.0
        for r in pnls:
            cum_ret += r
            peak, max_dd = max(peak, cum_ret), max(max_dd, peak - cum_ret)
        return {"pool_sharpe": pool_s, "time_weighted_sharpe": tw_s, "trades": trades_n, "win_rate_pct": float((pnls > 0).mean() * 100.0), "avg_gain_trade_pct": float(pnls.mean()), "gain_per_yr_pct": float(pnls.sum()) / yrs_for_pyr, "max_dd_pct": max_dd, "long_trades": len(long_rets), "short_trades": len(short_rets)}
    elif trades_n == 1:
        return {"pool_sharpe": 0.0, "time_weighted_sharpe": 0.0, "trades": 1, "win_rate_pct": 100.0 if pnls[0] > 0 else 0.0, "avg_gain_trade_pct": float(pnls[0]), "gain_per_yr_pct": float(pnls[0]) / yrs_for_pyr, "max_dd_pct": max(0.0, -float(pnls[0])), "long_trades": len(long_rets), "short_trades": len(short_rets)}
    return {"pool_sharpe": 0.0, "time_weighted_sharpe": 0.0, "trades": 0, "win_rate_pct": 0.0, "avg_gain_trade_pct": 0.0, "gain_per_yr_pct": 0.0, "max_dd_pct": 0.0, "long_trades": 0, "short_trades": 0}

def sweep_variants(sym: str, variants: List[Dict[str, Any]], years_back: float = 7.0 / 365.25, chunk_size: int = 10_000, verbose: bool = True, n_years_for_yr_metrics: Optional[float] = None) -> Dict[str, np.ndarray]:
    t0 = time.time()
    try:
        npz, ts = load_npz(sym, "tradier", start_ts=None)
        if len(ts) < 50:
            return _empty_scoreboard(len(variants))
    except Exception:
        return _empty_scoreboard(len(variants))
    n_v = len(variants)
    pool_sharpe, tw_sharpe, trades = np.zeros(n_v, dtype=np.float32), np.zeros(n_v, dtype=np.float32), np.zeros(n_v, dtype=np.int32)
    win_rate, avg_gain, gain_per_yr = np.zeros(n_v, dtype=np.float32), np.zeros(n_v, dtype=np.float32), np.zeros(n_v, dtype=np.float32)
    max_dd, long_trades, short_trades = np.zeros(n_v, dtype=np.float32), np.zeros(n_v, dtype=np.int32), np.zeros(n_v, dtype=np.int32)
    ts_5m = ts.astype(np.int64)
    start_ts = int(ts_5m[-1] - int(years_back * 365.25 * 86400))
    import multiprocessing
    n_workers = max(1, min(4, (multiprocessing.cpu_count() or 4) - 1))
    tasks = [(sym, start_ts, var, years_back) for var in variants]
    from concurrent.futures import ProcessPoolExecutor, as_completed
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(_run_variant_task_worker_stocks, t): idx for idx, t in enumerate(tasks)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                res = fut.result()
                if res is not None:
                    pool_sharpe[idx], tw_sharpe[idx], trades[idx] = res["pool_sharpe"], res["time_weighted_sharpe"], res["trades"]
                    win_rate[idx], avg_gain[idx], gain_per_yr[idx] = res["win_rate_pct"], res["avg_gain_trade_pct"], res["gain_per_yr_pct"]
                    max_dd[idx], long_trades[idx], short_trades[idx] = res["max_dd_pct"], res["long_trades"], res["short_trades"]
            except Exception:
                pass
    return {"pool_sharpe": pool_sharpe, "time_weighted_sharpe": tw_sharpe, "trades": trades, "win_rate_pct": win_rate, "avg_gain_trade_pct": avg_gain, "gain_per_yr_pct": gain_per_yr, "max_dd_pct": max_dd, "long_trades": long_trades, "short_trades": short_trades}

def _empty_scoreboard(n: int) -> Dict[str, np.ndarray]:
    return {"pool_sharpe": np.zeros(n, dtype=np.float32), "time_weighted_sharpe": np.zeros(n, dtype=np.float32), "trades": np.zeros(n, dtype=np.int32), "win_rate_pct": np.zeros(n, dtype=np.float32), "avg_gain_trade_pct": np.zeros(n, dtype=np.float32), "gain_per_yr_pct": np.zeros(n, dtype=np.float32), "max_dd_pct": np.zeros(n, dtype=np.float32), "long_trades": np.zeros(n, dtype=np.int32), "short_trades": np.zeros(n, dtype=np.int32)}

def top_k(scoreboard: Dict[str, np.ndarray], variants: List[Dict[str, Any]], k: int = 5, sort_by: str = 'time_weighted_sharpe') -> List[Tuple[Dict[str, Any], Dict[str, float]]]:
    n = len(scoreboard['trades'])
    if n == 0:
        return []
    sorted_idx = np.argsort(scoreboard[sort_by])[::-1]
    results = []
    for idx in sorted_idx[:k]:
        m = {col: float(scoreboard[col][idx]) for col in scoreboard.keys()}
        results.append((variants[idx], m))
    return results
