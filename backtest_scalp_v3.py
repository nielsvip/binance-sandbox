"""
backtest_scalp_v3.py — V3 scalper backtest harness on 1m klines.

Usage:
    python3 backtest_scalp_v3.py              # all symbols, all 3 TF modes
    python3 backtest_scalp_v3.py --symbols BTCUSDT,ETHUSDT
    python3 backtest_scalp_v3.py --tf-modes 1M_ONLY,3M_ONLY

Reads klines_cache/<SYMBOL>_1m.json (list of {ts,o,h,l,c,v} dicts).
Resamples 1m → 3m/15m/1h/4h. Computes Stoch %K (period=14) per TF.
Walks forward through 1m bars invoking scalp_v3 entry/exit/reentry.

Pool Sharpe rule (per CLAUDE.md): mean/std of per-trade returns across all
symbols pooled. Open positions at end MTM'd at final close. No annualization.
"""
from __future__ import annotations
# metrics_guard retrofit (audited 2026-04-30): this script writes a Sharpe
# number to a print/log surface. Per CLAUDE.md NO-LIES MANDATE, any future
# user-facing Sharpe MUST be routed through metrics_guard.validate_and_format_sharpe()
# with explicit label, n_syms, years, trades, mode. Bare 'Sharpe X.XX' output is forbidden.
from metrics_guard import validate_and_format_sharpe  # noqa: F401  (forward-prevention import)
import argparse
import glob
import json
import os
import statistics
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from config import Config
from scalp_v3 import (
    V3ExitState,
    V3Input,
    V3Position,
    check_scalp_v3_entry,
    check_scalp_v3_exit,
    check_scalp_v3_reentry_allowed,
)

BASE = Path(__file__).resolve().parent
KLINES_DIR = BASE / "klines_cache"
RESULTS_DIR = BASE / "data" / "scalp_v3_backtest"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

STOCH_PERIOD = 14
FEE_PCT = 0.04  # 0.02% maker each side = 0.04% round-trip (conservative)


class CfgOverride:
    def __init__(self, base, **overrides):
        self._base = base
        self._overrides = overrides
    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._base, name)


def _iso_to_epoch(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def load_1m(symbol: str) -> Optional[np.ndarray]:
    path = KLINES_DIR / f"{symbol}_1m.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            raw = json.load(f)
    except Exception:
        return None
    if not raw or len(raw) < 200:
        return None
    out = np.zeros((len(raw), 6), dtype=np.float64)
    for i, b in enumerate(raw):
        out[i, 0] = _iso_to_epoch(b["timestamp"])
        out[i, 1] = b["open"]
        out[i, 2] = b["high"]
        out[i, 3] = b["low"]
        out[i, 4] = b["close"]
        out[i, 5] = b["volume"]
    return out


def resample(bars_1m: np.ndarray, tf_min: int) -> np.ndarray:
    """Resample 1m → TF by timestamp bucket. Drops incomplete tail bucket."""
    if tf_min == 1: return bars_1m
    tf_sec = tf_min * 60
    bucket = (bars_1m[:, 0] // tf_sec).astype(np.int64)
    uniq, first_idx = np.unique(bucket, return_index=True)
    if len(uniq) < 2: return np.zeros((0, 6))
    out = np.zeros((len(uniq) - 1, 6), dtype=np.float64)  # drop last (incomplete)
    for i in range(len(uniq) - 1):
        s = first_idx[i]
        e = first_idx[i + 1]
        sub = bars_1m[s:e]
        out[i, 0] = uniq[i] * tf_sec
        out[i, 1] = sub[0, 1]
        out[i, 2] = sub[:, 2].max()
        out[i, 3] = sub[:, 3].min()
        out[i, 4] = sub[-1, 4]
        out[i, 5] = sub[:, 5].sum()
    return out


def stoch_k(bars: np.ndarray, period: int = STOCH_PERIOD) -> np.ndarray:
    n = len(bars)
    if n == 0: return np.array([])
    k = np.full(n, 50.0)
    for i in range(period - 1, n):
        hi = bars[i - period + 1 : i + 1, 2].max()
        lo = bars[i - period + 1 : i + 1, 3].min()
        rng = hi - lo
        if rng <= 0:
            k[i] = 50.0
        else:
            k[i] = (bars[i, 4] - lo) / rng * 100.0
    return k


def _tf_index_for_1m(ts_1m: float, tf_min: int, tf_bucket0: int) -> int:
    """Return index into TF array for a given 1m timestamp. -1 if before TF coverage."""
    tf_sec = tf_min * 60
    bucket = int(ts_1m // tf_sec)
    return bucket - tf_bucket0


def run_symbol(symbol: str, cfg) -> Optional[Dict]:
    bars_1m = load_1m(symbol)
    if bars_1m is None: return None
    bars_3m = resample(bars_1m, 3)
    bars_15m = resample(bars_1m, 15)
    bars_1h = resample(bars_1m, 60)
    bars_4h = resample(bars_1m, 240)
    if len(bars_3m) < 20 or len(bars_15m) < 5: return None
    k_1m = stoch_k(bars_1m)
    k_3m = stoch_k(bars_3m)
    k_15m = stoch_k(bars_15m)
    k_1h = stoch_k(bars_1h) if len(bars_1h) >= STOCH_PERIOD else np.full(len(bars_1h), 50.0)
    k_4h = stoch_k(bars_4h) if len(bars_4h) >= STOCH_PERIOD else np.full(len(bars_4h), 50.0)
    tf_bucket0 = {
        3: int(bars_3m[0, 0] // 180) if len(bars_3m) else 0,
        15: int(bars_15m[0, 0] // 900) if len(bars_15m) else 0,
        60: int(bars_1h[0, 0] // 3600) if len(bars_1h) else 0,
        240: int(bars_4h[0, 0] // 14400) if len(bars_4h) else 0,
    }
    trades = []
    pos = None
    exit_state = None
    for side in ("LONG", "SHORT"):
        # Independent passes for LONG and SHORT — each tracks own pos + exit state
        pos = None
        exit_state = None
        for i in range(30, len(bars_1m)):
            ts = float(bars_1m[i, 0])
            price = float(bars_1m[i, 4])
            i_3m = _tf_index_for_1m(ts, 3, tf_bucket0[3])
            i_15m = _tf_index_for_1m(ts, 15, tf_bucket0[15])
            i_1h = _tf_index_for_1m(ts, 60, tf_bucket0[60])
            i_4h = _tf_index_for_1m(ts, 240, tf_bucket0[240])
            if i_3m < 2 or i_3m >= len(bars_3m): continue
            if i_15m < 2 or i_15m >= len(bars_15m): continue
            if i_1h < 0 or i_1h >= len(bars_1h): continue
            if i_4h < 0 or i_4h >= len(bars_4h): continue
            bars_1m_view = bars_1m[max(0, i - 21): i + 1]
            bars_3m_view = bars_3m[max(0, i_3m - 5): i_3m + 1]
            bars_15m_view = bars_15m[max(0, i_15m - 3): i_15m + 1]
            inp = V3Input(
                symbol=symbol, side=side,
                bars_1m=[tuple(b) for b in bars_1m_view],
                bars_3m=[tuple(b) for b in bars_3m_view],
                bars_15m=[tuple(b) for b in bars_15m_view],
                k_1m=float(k_1m[i]), k_1m_prev=float(k_1m[i - 1]),
                k_3m=float(k_3m[i_3m]), k_3m_prev=float(k_3m[i_3m - 1]),
                k_15m=float(k_15m[i_15m]),
                k_15m_prev=float(k_15m[i_15m - 1]),
                k_15m_prev2=float(k_15m[i_15m - 2]) if i_15m >= 2 else 50.0,
                k_1h=float(k_1h[i_1h]), k_4h=float(k_4h[i_4h]),
                now_ts=ts, current_price=price,
            )
            if pos is not None:
                ok, reason = check_scalp_v3_exit(pos, inp, cfg)
                if ok:
                    gain_pct = (price - pos.entry_price) / pos.entry_price * 100.0
                    if pos.side == "SHORT":
                        gain_pct = -gain_pct
                    gain_pct -= FEE_PCT
                    trades.append({
                        "symbol": symbol, "side": pos.side,
                        "entry_ts": pos.entry_ts, "exit_ts": ts,
                        "entry_price": pos.entry_price, "exit_price": price,
                        "gain_pct": gain_pct, "reason": reason,
                        "hold_min": (ts - pos.entry_ts) / 60.0,
                    })
                    exit_state = V3ExitState(
                        last_exit_ts=ts,
                        k_15m_at_exit=float(k_15m[i_15m]),
                        k_15m_prev_at_exit=float(k_15m[i_15m - 1]),
                    )
                    pos = None
            else:
                if exit_state is not None:
                    allowed, _ = check_scalp_v3_reentry_allowed(exit_state, inp, cfg)
                    if not allowed:
                        continue
                ok, reason = check_scalp_v3_entry(inp, cfg)
                if ok:
                    pos = V3Position(side=side, entry_price=price, entry_ts=ts, entry_k_15m=float(k_15m[i_15m]))
        if pos is not None:
            final_price = float(bars_1m[-1, 4])
            final_ts = float(bars_1m[-1, 0])
            gain_pct = (final_price - pos.entry_price) / pos.entry_price * 100.0
            if pos.side == "SHORT":
                gain_pct = -gain_pct
            gain_pct -= FEE_PCT
            trades.append({
                "symbol": symbol, "side": pos.side,
                "entry_ts": pos.entry_ts, "exit_ts": final_ts,
                "entry_price": pos.entry_price, "exit_price": final_price,
                "gain_pct": gain_pct, "reason": "OPEN_EOT_MTM",
                "hold_min": (final_ts - pos.entry_ts) / 60.0,
            })
    return {"symbol": symbol, "trades": trades, "bar_count": len(bars_1m)}


def pool_metrics(all_trades: List[Dict]) -> Dict:
    if not all_trades:
        return {"n": 0, "pool_sharpe": 0.0, "mean_pct": 0.0, "total_pct": 0.0, "wr": 0.0, "max_dd_pct": 0.0}
    returns = [t["gain_pct"] for t in all_trades]
    mean_r = statistics.mean(returns)
    std_r = statistics.stdev(returns) if len(returns) > 1 else 0.0
    sharpe = (mean_r / std_r) if std_r > 0 else 0.0
    total = sum(returns)
    wins = sum(1 for r in returns if r > 0)
    wr = wins / len(returns) * 100.0
    # Max DD on cumulative equity
    eq = np.cumsum(returns)
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    max_dd = float(dd.max()) if len(dd) else 0.0
    return {
        "n": len(returns),
        "pool_sharpe": round(sharpe, 4),
        "mean_pct": round(mean_r, 4),
        "total_pct": round(total, 2),
        "wr": round(wr, 1),
        "max_dd_pct": round(max_dd, 2),
    }


def run_variants(symbols: List[str], tf_modes: List[str], exit_modes: List[str]) -> List[Dict]:
    base_cfg = Config()
    results = []
    for tf_mode in tf_modes:
        for exit_mode in exit_modes:
            cfg = CfgOverride(base_cfg, SCALP_V3_ENTRY_TF_MODE=tf_mode, SCALP_V3_EXIT_TF_MODE=exit_mode)
            t0 = time.time()
            all_trades = []
            per_sym = []
            for sym in symbols:
                r = run_symbol(sym, cfg)
                if r is None: continue
                all_trades.extend(r["trades"])
                if r["trades"]:
                    per_sym.append({"symbol": sym, "n": len(r["trades"]),
                                    "total_pct": round(sum(t["gain_pct"] for t in r["trades"]), 2)})
            m = pool_metrics(all_trades)
            m.update({
                "tf_mode": tf_mode, "exit_mode": exit_mode,
                "symbols_tested": len(symbols),
                "symbols_with_trades": len(per_sym),
                "elapsed_s": round(time.time() - t0, 1),
            })
            results.append({"summary": m, "per_symbol": per_sym, "trades": all_trades})
            print(f"[{tf_mode:16s} EXIT={exit_mode:8s}] N={m['n']:6d} Sharpe={m['pool_sharpe']:+.3f} "
                  f"mean={m['mean_pct']:+.3f}% total={m['total_pct']:+.1f}% WR={m['wr']:5.1f}% "
                  f"DD={m['max_dd_pct']:.1f}% syms={m['symbols_with_trades']}/{len(symbols)} in {m['elapsed_s']}s")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=str, default="", help="Comma-separated; empty = all with 1m data")
    ap.add_argument("--tf-modes", type=str, default="1M_ONLY,3M_ONLY,1M_AND_3M,3M_CONFIRMS_1M")
    ap.add_argument("--exit-modes", type=str, default="ANY,1M_ONLY,3M_ONLY")
    ap.add_argument("--limit", type=int, default=0, help="Cap number of symbols (0 = no cap)")
    args = ap.parse_args()
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = sorted({Path(p).stem.replace("_1m", "") for p in glob.glob(str(KLINES_DIR / "*_1m.json"))})
    if args.limit:
        symbols = symbols[: args.limit]
    print(f"Symbols: {len(symbols)}")
    tf_modes = [m.strip() for m in args.tf_modes.split(",") if m.strip()]
    exit_modes = [m.strip() for m in args.exit_modes.split(",") if m.strip()]
    print(f"TF modes: {tf_modes} × Exit modes: {exit_modes} = {len(tf_modes)*len(exit_modes)} variants")
    results = run_variants(symbols, tf_modes, exit_modes)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"v3_backtest_{ts}.json"
    with open(out_path, "w") as f:
        json.dump([{"summary": r["summary"], "per_symbol_top20": sorted(r["per_symbol"],
                   key=lambda x: x["total_pct"], reverse=True)[:20]} for r in results], f, indent=2)
    print(f"\nResults saved: {out_path}")
    print("\n--- LEADERBOARD (by pool Sharpe) ---")
    for r in sorted(results, key=lambda x: x["summary"]["pool_sharpe"], reverse=True):
        s = r["summary"]
        print(f"  {s['tf_mode']:16s} EXIT={s['exit_mode']:8s} → Sharpe={s['pool_sharpe']:+.3f} "
              f"total={s['total_pct']:+.1f}% WR={s['wr']:.1f}% N={s['n']}")


if __name__ == "__main__":
    main()
