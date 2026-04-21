#!/usr/bin/env python3
"""
wt_dc_hierarchy.py — TF-HIERARCHY engine (2026-04-21).

User directive: REWRITE THE ENTIRE wt_dc system so it understands TF relationships.
The core rule:
    "if a small tf is at extreme levels the next one's delta gives the answer
     as to where this is going; if also at extreme the next one; up to W/M"

Per-TF signals from:  dc (donchian), wt_delta (wt1-wt2), wt_velocity, price highs/lows.
Per-bar hierarchical decision cascades from LTF up through 15m/1h/4h/D/W/M.

ENTRY (LONG): LTF at LOWER_RZ with bullish delta. Cascading confirmation:
  - If any HTF at UPPER_RZ → REJECT (overhead resistance).
  - If HTF at LOWER_RZ but delta NOT bullish → REJECT (HTF not confirming bounce).
  - Otherwise ACCEPT (entering into LTF bounce with HTF room or confirmation).

EXIT (LONG): iterate TFs from LTF upward:
  - TF NOT at UPPER_RZ: price has room in this TF → HOLD (not at resistance yet).
  - TF at UPPER_RZ, delta bullish: breakout continuing → cascade to next TF.
  - TF at UPPER_RZ, delta bearish: REVERSAL confirmed at this level → EXIT.
  - Cascade through all TFs all extreme-with-bullish-delta: HOLD (rare extreme trend).

Mirror for SHORT.

Signal definitions per TF:
  at_upper_rz[k,i]:  close[k,i] >= dc_high_prev[k,i] * (1 - band%)
                     OR bb_pctb[k,i] >= RZ_TOP (default 0.85)
  at_lower_rz[k,i]:  mirror (dc_low + bb_pctb <= 0.15)
  delta_bullish[k,i]: wt1[k,i] > wt2[k,i] AND wt_velocity[k,i] > wt_delta_min
                      (wt_delta_min default 0.0 — any positive)
  delta_bearish[k,i]: mirror (wt1<wt2 AND wt_vel < -wt_delta_min)

Vectorized via numpy + per-bar hierarchical loop (Python — early-exit). Trades off
some speed for correctness of the hierarchy logic.
"""
from typing import Dict, List, Tuple

import numpy as np


TF_ORDER_CRYPTO = ("3m", "15m", "1h", "4h", "D")
TF_ORDER_TRADIER = ("5m", "15m", "1h", "4h", "D")


def _safe(npz: dict, key: str, n: int, default: float = 0.0) -> np.ndarray:
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


def _resolve_tfs(cfg) -> Tuple[str, ...]:
    mode = str(getattr(cfg, "MODE", "crypto"))
    use_w_m = bool(getattr(cfg, "HIER_USE_W_M", False))
    base = TF_ORDER_TRADIER if mode == "tradier" else TF_ORDER_CRYPTO
    if use_w_m:
        return tuple(list(base) + ["W", "M"])
    return base


def compute_per_tf_signals(npz: dict, n: int, cfg) -> Dict[str, Dict[str, np.ndarray]]:
    """Precompute per-TF boolean signals for hierarchy cascade."""
    tfs = _resolve_tfs(cfg)
    rz_top = float(getattr(cfg, "HIER_RZ_TOP_BB", 0.85))
    rz_bot = float(getattr(cfg, "HIER_RZ_BOT_BB", 0.15))
    dc_band_pct = float(getattr(cfg, "HIER_DC_BAND_PCT", 0.2)) / 100.0
    wt_delta_min = float(getattr(cfg, "HIER_WT_DELTA_MIN", 0.0))
    vel_min = float(getattr(cfg, "HIER_WT_VEL_MIN", 0.0))

    out: Dict[str, Dict[str, np.ndarray]] = {}
    for tf in tfs:
        close = _safe(npz, f"close_{tf}", n)
        dc_hi = _safe(npz, f"dc_high_{tf}", n)
        dc_lo = _safe(npz, f"dc_low_{tf}", n)
        wt1 = _safe(npz, f"wt1_{tf}", n)
        wt2 = _safe(npz, f"wt2_{tf}", n)
        wt_vel = _safe(npz, f"wt_velocity_{tf}", n)
        bb_pctb = _safe(npz, f"bb_pctb_{tf}", n, 0.5)
        # Skip TFs with no usable data (all zero after _safe default)
        if wt1.sum() == 0 and wt2.sum() == 0 and dc_hi.sum() == 0 and dc_lo.sum() == 0:
            continue
        dc_hi_prev = np.roll(dc_hi, 1); dc_hi_prev[0] = dc_hi[0]
        dc_lo_prev = np.roll(dc_lo, 1); dc_lo_prev[0] = dc_lo[0]
        at_upper_dc = (dc_hi_prev > 0) & (close >= dc_hi_prev * (1.0 - dc_band_pct))
        at_lower_dc = (dc_lo_prev > 0) & (close <= dc_lo_prev * (1.0 + dc_band_pct))
        at_upper_bb = bb_pctb >= rz_top
        at_lower_bb = bb_pctb <= rz_bot
        at_upper = at_upper_dc | at_upper_bb
        at_lower = at_lower_dc | at_lower_bb
        wt_delta = wt1 - wt2
        delta_bull = (wt_delta > wt_delta_min) & (wt_vel > vel_min)
        delta_bear = (wt_delta < -wt_delta_min) & (wt_vel < -vel_min)
        out[tf] = {
            "at_upper": at_upper,
            "at_lower": at_lower,
            "delta_bull": delta_bull,
            "delta_bear": delta_bear,
        }
    return out


def compute_hierarchical_exit_long(per_tf: Dict[str, Dict[str, np.ndarray]], n: int) -> np.ndarray:
    """LONG exit — iterate TFs from LTF upward:
      - TF not at upper RZ: HOLD (price has room).
      - TF at upper + delta bull: breakout continuing, cascade up.
      - TF at upper + delta bear: EXIT.
      - Cascade exhausted (all at upper with bull): HOLD (rare strong trend).
    Vectorized via cumulative AND on at_upper axis.
    """
    tfs = list(per_tf.keys())
    if not tfs:
        return np.zeros(n, dtype=bool)
    at_upper = np.stack([per_tf[tf]["at_upper"] for tf in tfs])   # (k, n)
    delta_bear = np.stack([per_tf[tf]["delta_bear"] for tf in tfs])
    # cum_upper[k,i] = all TFs 0..k at upper at bar i
    cum_upper = np.cumprod(at_upper.astype(int), axis=0).astype(bool)
    # EXIT fires at bar i if ANY TF k: (cum_upper[k,i] AND delta_bear[k,i])
    # Meaning: cascade through all lower TFs at upper, and at TF k delta turned bear → exit.
    exit_mask = (cum_upper & delta_bear).any(axis=0)
    return exit_mask


def compute_hierarchical_exit_short(per_tf: Dict[str, Dict[str, np.ndarray]], n: int) -> np.ndarray:
    """SHORT exit — mirror of LONG: cascade from LTF at lower RZ, exit on delta bullish."""
    tfs = list(per_tf.keys())
    if not tfs:
        return np.zeros(n, dtype=bool)
    at_lower = np.stack([per_tf[tf]["at_lower"] for tf in tfs])
    delta_bull = np.stack([per_tf[tf]["delta_bull"] for tf in tfs])
    cum_lower = np.cumprod(at_lower.astype(int), axis=0).astype(bool)
    exit_mask = (cum_lower & delta_bull).any(axis=0)
    return exit_mask


def compute_hierarchical_entry_long(per_tf: Dict[str, Dict[str, np.ndarray]], n: int) -> np.ndarray:
    """LONG entry — LTF at lower RZ + bullish delta, confirmed by HTFs:
      - Any HTF at upper RZ → REJECT (overhead).
      - Any HTF at lower RZ but NOT delta bull → REJECT (HTF not confirming bounce).
      - Otherwise ACCEPT.
    Vectorized.
    """
    tfs = list(per_tf.keys())
    if not tfs:
        return np.zeros(n, dtype=bool)
    ltf = tfs[0]
    ltf_setup = per_tf[ltf]["at_lower"] & per_tf[ltf]["delta_bull"]
    if len(tfs) == 1:
        return ltf_setup
    htf_upper_any = np.zeros(n, dtype=bool)
    htf_lower_noconfirm_any = np.zeros(n, dtype=bool)
    for tf in tfs[1:]:
        htf_upper_any |= per_tf[tf]["at_upper"]
        htf_lower_noconfirm_any |= (per_tf[tf]["at_lower"] & ~per_tf[tf]["delta_bull"])
    entry = ltf_setup & ~htf_upper_any & ~htf_lower_noconfirm_any
    return entry


def compute_hierarchical_entry_short(per_tf: Dict[str, Dict[str, np.ndarray]], n: int) -> np.ndarray:
    """SHORT entry — mirror."""
    tfs = list(per_tf.keys())
    if not tfs:
        return np.zeros(n, dtype=bool)
    ltf = tfs[0]
    ltf_setup = per_tf[ltf]["at_upper"] & per_tf[ltf]["delta_bear"]
    if len(tfs) == 1:
        return ltf_setup
    htf_lower_any = np.zeros(n, dtype=bool)
    htf_upper_noconfirm_any = np.zeros(n, dtype=bool)
    for tf in tfs[1:]:
        htf_lower_any |= per_tf[tf]["at_lower"]
        htf_upper_noconfirm_any |= (per_tf[tf]["at_upper"] & ~per_tf[tf]["delta_bear"])
    entry = ltf_setup & ~htf_lower_any & ~htf_upper_noconfirm_any
    return entry


def compute_hierarchy_signals(npz: dict, n: int, is_long: bool, cfg) -> Tuple[np.ndarray, np.ndarray]:
    """Top-level entry point: (entry_mask, exit_mask) for given direction."""
    per_tf = compute_per_tf_signals(npz, n, cfg)
    if is_long:
        entry = compute_hierarchical_entry_long(per_tf, n)
        exit_ = compute_hierarchical_exit_long(per_tf, n)
    else:
        entry = compute_hierarchical_entry_short(per_tf, n)
        exit_ = compute_hierarchical_exit_short(per_tf, n)
    return entry, exit_


if __name__ == "__main__":
    # Smoke test
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from v8_quick_engine import QuickConfig, load_npz
    stores = load_npz("crypto", ["BTCUSDT", "ETHUSDT"], "2022-01-01", "/Users/niels/Documents/binance/backtest_v8/indicators")
    cfg = QuickConfig()
    for sym, npz in stores.items():
        n = len(npz.get("timestamps", npz.get("timestamp_3m", [])))
        if n < 100:
            continue
        e_l, x_l = compute_hierarchy_signals(npz, n, True, cfg)
        e_s, x_s = compute_hierarchy_signals(npz, n, False, cfg)
        print(f"{sym}: n={n} LONG entries={e_l.sum()}/{n} ({e_l.sum()/n*100:.2f}%) exits={x_l.sum()}/{n} ({x_l.sum()/n*100:.2f}%) | SHORT entries={e_s.sum()} exits={x_s.sum()}")
