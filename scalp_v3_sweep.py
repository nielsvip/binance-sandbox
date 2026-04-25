"""
scalp_v3_sweep.py — Mass variant sweep for V3 scalper.

Precomputes per-symbol arrays once (bars + K per TF + Heikin-Ashi variants),
then runs thousands of variants in parallel via multiprocessing.

Variant factors:
  - TF mode:        entry bar requirement (1M_ONLY / 3M_ONLY / 1M_AND_3M)
  - exit mode:      which TF bar triggers exit (ANY / 1M_ONLY / 3M_ONLY / 15M_ONLY)
  - vol_mult:       volume spike multiplier for entry gate
  - k_1m_max:       entry K_1m oversold threshold
  - exit_k_1m_min:  exit K_1m overbought threshold
  - max_hold:       stall timeout (min)
  - stall_gain:     gain % above which stall does not fire
  - ha_1m / ha_3m:  use Heikin-Ashi bars for pattern detection
  - htf_align:      require k_15m + k_1h + k_4h all oversold
  - pair_align:     require 1m bar pattern AND 3m bar pattern (regardless of TF mode)
  - side_mode:      LONG_ONLY / SHORT_ONLY / BOTH

Progressive JSONL output: data/scalp_v3_sweep/results_<ts>.jsonl
Each line is one completed variant's metrics. Safe to tail/analyze mid-run.

Ranking metric: total_pct (accumulated gain/loss — hair-trigger scalper's primary).
Sharpe reported but secondary.

Usage:
  python3 scalp_v3_sweep.py --deadline-hours 30 --workers 10
  python3 scalp_v3_sweep.py --variants 5000 --workers 8
  python3 scalp_v3_sweep.py --grid coarse --deadline-iso 2026-04-23T14:00:00Z
"""
from __future__ import annotations
import argparse
import glob
import itertools
import json
import multiprocessing as mp
import os
import pickle
import random
import signal
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

BASE = Path(__file__).resolve().parent
KLINES_DIR = BASE / "klines_cache"
OUT_DIR = BASE / "data" / "scalp_v3_sweep"
OUT_DIR.mkdir(parents=True, exist_ok=True)
PRECOMP_CACHE = OUT_DIR / "precomp_cache.pkl"

STOCH_PERIOD = 14
FEE_PCT = 0.04  # maker round-trip (0.02% each side); user can override via --fee


def _iso_to_epoch(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def load_1m(symbol: str) -> Optional[np.ndarray]:
    path = KLINES_DIR / f"{symbol}_1m.json"
    if not path.exists(): return None
    try:
        with open(path) as f:
            raw = json.load(f)
    except Exception:
        return None
    if not raw or len(raw) < 200: return None
    out = np.zeros((len(raw), 6), dtype=np.float64)
    for i, b in enumerate(raw):
        out[i, 0] = _iso_to_epoch(b["timestamp"])
        out[i, 1] = b["open"]; out[i, 2] = b["high"]; out[i, 3] = b["low"]
        out[i, 4] = b["close"]; out[i, 5] = b["volume"]
    return out


def resample(bars_1m: np.ndarray, tf_min: int) -> np.ndarray:
    if tf_min == 1: return bars_1m
    tf_sec = tf_min * 60
    bucket = (bars_1m[:, 0] // tf_sec).astype(np.int64)
    uniq, first_idx = np.unique(bucket, return_index=True)
    if len(uniq) < 2: return np.zeros((0, 6))
    out = np.zeros((len(uniq) - 1, 6), dtype=np.float64)
    for i in range(len(uniq) - 1):
        s = first_idx[i]; e = first_idx[i + 1]
        sub = bars_1m[s:e]
        out[i, 0] = uniq[i] * tf_sec
        out[i, 1] = sub[0, 1]
        out[i, 2] = sub[:, 2].max()
        out[i, 3] = sub[:, 3].min()
        out[i, 4] = sub[-1, 4]
        out[i, 5] = sub[:, 5].sum()
    return out


def atr(bars: np.ndarray, period: int = 14) -> np.ndarray:
    """True Range ATR. Returns array of same length as bars."""
    n = len(bars)
    if n == 0: return np.array([])
    out = np.zeros(n)
    if n < 2: return out
    hi = bars[:, 2]; lo = bars[:, 3]; cl = bars[:, 4]
    prev_cl = np.concatenate([[cl[0]], cl[:-1]])
    tr = np.maximum.reduce([hi - lo, np.abs(hi - prev_cl), np.abs(lo - prev_cl)])
    if n < period: return out
    for i in range(period - 1, n):
        out[i] = tr[max(0, i - period + 1): i + 1].mean()
    return out


def vwap(bars: np.ndarray, period: int = 30) -> np.ndarray:
    """Rolling N-bar VWAP. Returns array same length as bars."""
    n = len(bars)
    if n == 0: return np.array([])
    out = np.zeros(n)
    if n < period: return out
    typ = (bars[:, 2] + bars[:, 3] + bars[:, 4]) / 3.0
    vol = bars[:, 5]
    tpv = typ * vol
    for i in range(period - 1, n):
        v_sum = vol[i - period + 1: i + 1].sum()
        if v_sum > 0:
            out[i] = tpv[i - period + 1: i + 1].sum() / v_sum
        else:
            out[i] = typ[i]
    return out


def bb_width(bars: np.ndarray, period: int = 20, std_mult: float = 2.0) -> np.ndarray:
    """Bollinger Band width (upper - lower) / middle × 100. Returns array."""
    n = len(bars)
    if n == 0: return np.array([])
    out = np.zeros(n)
    if n < period: return out
    cl = bars[:, 4]
    for i in range(period - 1, n):
        window = cl[i - period + 1: i + 1]
        mid = window.mean()
        sd = window.std()
        if mid > 0:
            out[i] = (std_mult * sd * 2) / mid * 100.0
    return out


def stoch_k(bars: np.ndarray, period: int = STOCH_PERIOD) -> np.ndarray:
    n = len(bars)
    if n == 0: return np.array([])
    k = np.full(n, 50.0)
    if n < period: return k
    highs = bars[:, 2]; lows = bars[:, 3]; closes = bars[:, 4]
    for i in range(period - 1, n):
        hi = highs[i - period + 1 : i + 1].max()
        lo = lows[i - period + 1 : i + 1].min()
        rng = hi - lo
        if rng > 0:
            k[i] = (closes[i] - lo) / rng * 100.0
    return k


def heikin_ashi(bars: np.ndarray) -> np.ndarray:
    """Convert OHLCV to HA. Timestamp + volume unchanged; OHLC transformed."""
    n = len(bars)
    if n == 0: return bars.copy()
    out = np.zeros_like(bars)
    out[:, 0] = bars[:, 0]
    out[:, 5] = bars[:, 5]
    # HA_close = (O+H+L+C)/4
    out[:, 4] = (bars[:, 1] + bars[:, 2] + bars[:, 3] + bars[:, 4]) / 4.0
    # HA_open iterative
    out[0, 1] = (bars[0, 1] + bars[0, 4]) / 2.0
    for i in range(1, n):
        out[i, 1] = (out[i - 1, 1] + out[i - 1, 4]) / 2.0
    # HA_high = max(H, HA_open, HA_close); HA_low = min(L, HA_open, HA_close)
    out[:, 2] = np.maximum(np.maximum(bars[:, 2], out[:, 1]), out[:, 4])
    out[:, 3] = np.minimum(np.minimum(bars[:, 3], out[:, 1]), out[:, 4])
    return out


def precompute_symbol(symbol: str) -> Optional[Dict]:
    bars_1m = load_1m(symbol)
    if bars_1m is None: return None
    bars_3m = resample(bars_1m, 3)
    bars_15m = resample(bars_1m, 15)
    bars_1h = resample(bars_1m, 60)
    bars_4h = resample(bars_1m, 240)
    if len(bars_3m) < 20 or len(bars_15m) < 5: return None
    bars_1m_ha = heikin_ashi(bars_1m)
    bars_3m_ha = heikin_ashi(bars_3m)
    k_1m = stoch_k(bars_1m)
    k_3m = stoch_k(bars_3m)
    k_15m = stoch_k(bars_15m)
    k_1h = stoch_k(bars_1h)
    k_4h = stoch_k(bars_4h)
    # --- 2026-04-24 new features: ATR, VWAP, BB-width ---
    atr_1m = atr(bars_1m, 14)
    atr_3m = atr(bars_3m, 14)
    atr_15m = atr(bars_15m, 14)
    vwap_3m = vwap(bars_3m, 30)  # 30 × 3m = 90min rolling VWAP
    bbw_3m = bb_width(bars_3m, 20, 2.0)
    # TF bucket origin for 1m→TF index mapping
    bucket0 = {
        3: int(bars_3m[0, 0] // 180) if len(bars_3m) else 0,
        15: int(bars_15m[0, 0] // 900) if len(bars_15m) else 0,
        60: int(bars_1h[0, 0] // 3600) if len(bars_1h) else 0,
        240: int(bars_4h[0, 0] // 14400) if len(bars_4h) else 0,
    }
    return {
        "symbol": symbol,
        "bars_1m": bars_1m, "bars_3m": bars_3m, "bars_15m": bars_15m,
        "bars_1h": bars_1h, "bars_4h": bars_4h,
        "bars_1m_ha": bars_1m_ha, "bars_3m_ha": bars_3m_ha,
        "k_1m": k_1m, "k_3m": k_3m, "k_15m": k_15m, "k_1h": k_1h, "k_4h": k_4h,
        "atr_1m": atr_1m, "atr_3m": atr_3m, "atr_15m": atr_15m,
        "vwap_3m": vwap_3m, "bbw_3m": bbw_3m,
        "bucket0": bucket0,
    }


def build_precomputed(symbols: List[str], force: bool = False) -> Dict[str, Dict]:
    if not force and PRECOMP_CACHE.exists():
        print(f"[precomp] Loading cache {PRECOMP_CACHE} ...")
        with open(PRECOMP_CACHE, "rb") as f:
            cache = pickle.load(f)
        if cache.get("_symbols") == set(symbols):
            print(f"[precomp] Cache hit: {len(cache) - 1} symbols")
            return {k: v for k, v in cache.items() if k != "_symbols"}
        print("[precomp] Cache universe mismatch — rebuilding")
    t0 = time.time()
    print(f"[precomp] Building for {len(symbols)} symbols ...")
    out = {}
    for i, sym in enumerate(symbols):
        p = precompute_symbol(sym)
        if p is not None:
            out[sym] = p
        if (i + 1) % 100 == 0:
            print(f"[precomp]   {i + 1}/{len(symbols)} done ({len(out)} kept)")
    print(f"[precomp] Done: {len(out)}/{len(symbols)} symbols kept in {time.time() - t0:.1f}s")
    to_save = {"_symbols": set(symbols), **out}
    with open(PRECOMP_CACHE, "wb") as f:
        pickle.dump(to_save, f)
    print(f"[precomp] Cached → {PRECOMP_CACHE}")
    return out


# ---------- Variant logic (tight inner loop) ----------

_PATTERN_INVERSE = {
    "HH_AND_HL": "LL_AND_LH", "HH_OR_HL": "LL_OR_LH", "HH": "LL",
    "LL_AND_LH": "HH_AND_HL", "LL_OR_LH": "HH_OR_HL", "LL": "HH",
}


def _bar_match(prev_h, prev_l, curr_h, curr_l, pattern: str, side: str) -> bool:
    p = pattern if side == "LONG" else _PATTERN_INVERSE.get(pattern, pattern)
    hh = curr_h > prev_h; hl = curr_l > prev_l
    lh = curr_h < prev_h; ll = curr_l < prev_l
    if p == "HH_AND_HL": return hh and hl
    if p == "HH_OR_HL":  return hh or hl
    if p == "HH":        return hh
    if p == "LL_AND_LH": return ll and lh
    if p == "LL_OR_LH":  return ll or lh
    if p == "LL":        return ll
    return False


def walk_symbol(pc: Dict, variant: Dict, side: str, fee_pct: float) -> List[Dict]:
    b1 = pc["bars_1m_ha"] if variant["ha_1m"] else pc["bars_1m"]
    b3 = pc["bars_3m_ha"] if variant["ha_3m"] else pc["bars_3m"]
    raw_1m = pc["bars_1m"]  # for vol
    bars_3m = pc["bars_3m"]; bars_15m = pc["bars_15m"]
    bars_1h = pc["bars_1h"]; bars_4h = pc["bars_4h"]
    k_1m = pc["k_1m"]; k_3m = pc["k_3m"]; k_15m = pc["k_15m"]
    k_1h = pc["k_1h"]; k_4h = pc["k_4h"]
    bucket0 = pc["bucket0"]
    n = len(raw_1m)
    if n < 30: return []
    use_1m_entry = variant["tf_mode"] in ("1M_ONLY", "1M_AND_3M")
    use_3m_entry = variant["tf_mode"] in ("3M_ONLY", "1M_AND_3M") or variant["pair_align"]
    check_1m_exit = variant["exit_mode"] in ("ANY", "1M_ONLY")
    check_3m_exit = variant["exit_mode"] in ("ANY", "3M_ONLY")
    check_15m_exit = variant["exit_mode"] in ("ANY", "15M_ONLY")
    htf_align = variant["htf_align"]
    htf_align_max = variant["htf_align_max"]
    k_1m_max = variant["k_1m_max"]
    k_3m_max = variant["k_3m_max"]
    k_15m_max = variant["k_15m_max"]
    k_1h_max = variant["k_1h_max"]
    k_4h_max = variant["k_4h_max"]
    vol_mult = variant["vol_mult"]
    exit_k_1m_min = variant["exit_k_1m_min"]
    exit_k_3m_min = variant["exit_k_3m_min"]
    exit_k_15m_min = variant["exit_k_15m_min"]
    exit_bar_1m = variant["exit_bar_1m"]
    exit_bar_3m = variant["exit_bar_3m"]
    exit_bar_15m = variant["exit_bar_15m"]
    entry_bar_1m = variant["entry_bar_1m"]
    entry_bar_3m = variant["entry_bar_3m"]
    max_hold_sec = variant["max_hold_min"] * 60.0
    stall_gain = variant["stall_gain"]
    require_bounce = variant["reentry_require_bounce"]
    reentry_bounce_mode = variant["reentry_bounce_mode"]
    reentry_bounce_k_15m_max = variant["reentry_bounce_k_15m_max"]
    reentry_bounce_bar_3m = variant["reentry_bounce_bar_3m"]
    reentry_cooldown_s = variant["reentry_cooldown_s"]
    len_3m = len(bars_3m); len_15m = len(bars_15m)
    len_1h = len(bars_1h); len_4h = len(bars_4h)
    trades = []
    pos_ts = -1.0; pos_price = 0.0
    pos_peak = 0.0  # running peak gain for peak-giveback exit
    exit_ts = -1.0
    exit_k15 = 50.0; exit_k15_prev = 50.0
    for i in range(30, n):
        ts = raw_1m[i, 0]; price = raw_1m[i, 4]
        bk_3m = int(ts // 180) - bucket0[3]
        bk_15m = int(ts // 900) - bucket0[15]
        bk_1h = int(ts // 3600) - bucket0[60]
        bk_4h = int(ts // 14400) - bucket0[240]
        if bk_3m < 2 or bk_3m >= len_3m: continue
        if bk_15m < 2 or bk_15m >= len_15m: continue
        if bk_1h < 0 or bk_1h >= len_1h: continue
        if bk_4h < 0 or bk_4h >= len_4h: continue
        if pos_ts > 0:
            # ---- EXIT ----
            age = ts - pos_ts
            if side == "LONG":
                gain = (price - pos_price) / pos_price * 100.0
            else:
                gain = (pos_price - price) / pos_price * 100.0
            if not variant.get("disable_stall", False):
                if age > max_hold_sec and gain <= stall_gain:
                    trades.append({"side": side, "entry_price": pos_price, "exit_price": price,
                                   "entry_ts": pos_ts, "exit_ts": ts, "gain_pct": gain - fee_pct,
                                   "reason": "STALL"})
                    exit_ts = ts
                    exit_k15 = float(k_15m[bk_15m]); exit_k15_prev = float(k_15m[bk_15m - 1])
                    pos_ts = -1.0
                    continue
            # === ATR-based take-profit / stop-loss (2026-04-24) ===
            _atr_tp_mult = variant.get("atr_tp_mult", 0.0)
            _atr_sl_mult = variant.get("atr_sl_mult", 0.0)
            if _atr_tp_mult > 0 or _atr_sl_mult > 0:
                atr_3m = pc.get("atr_3m")
                if atr_3m is not None and bk_3m < len(atr_3m):
                    atr_pct = atr_3m[bk_3m] / pos_price * 100.0 if pos_price > 0 else 0
                    if _atr_tp_mult > 0 and gain >= _atr_tp_mult * atr_pct:
                        trades.append({"side": side, "entry_price": pos_price, "exit_price": price,
                                       "entry_ts": pos_ts, "exit_ts": ts, "gain_pct": gain - fee_pct,
                                       "reason": "ATR_TP"})
                        exit_ts = ts; pos_ts = -1.0
                        exit_k15 = float(k_15m[bk_15m]); exit_k15_prev = float(k_15m[bk_15m - 1])
                        continue
                    if _atr_sl_mult > 0 and gain <= -_atr_sl_mult * atr_pct:
                        trades.append({"side": side, "entry_price": pos_price, "exit_price": price,
                                       "entry_ts": pos_ts, "exit_ts": ts, "gain_pct": gain - fee_pct,
                                       "reason": "ATR_SL"})
                        exit_ts = ts; pos_ts = -1.0
                        exit_k15 = float(k_15m[bk_15m]); exit_k15_prev = float(k_15m[bk_15m - 1])
                        continue
            # === Peak-giveback trailing exit (2026-04-24) ===
            _pg_arm = variant.get("pg_arm_pct", 0.0)
            _pg_give = variant.get("pg_giveback_pct", 0.0)
            if _pg_arm > 0 and _pg_give > 0:
                if gain > pos_peak:
                    pos_peak = gain
                if pos_peak >= _pg_arm and gain <= pos_peak - _pg_give:
                    trades.append({"side": side, "entry_price": pos_price, "exit_price": price,
                                   "entry_ts": pos_ts, "exit_ts": ts, "gain_pct": gain - fee_pct,
                                   "reason": "PEAK_GIVEBACK"})
                    exit_ts = ts; pos_ts = -1.0
                    pos_peak = 0.0
                    exit_k15 = float(k_15m[bk_15m]); exit_k15_prev = float(k_15m[bk_15m - 1])
                    continue
            triggered = False; reason = ""
            if check_1m_exit:
                if (side == "LONG" and k_1m[i] > exit_k_1m_min) or (side == "SHORT" and k_1m[i] < 100 - exit_k_1m_min):
                    if _bar_match(b1[i - 1, 2], b1[i - 1, 3], b1[i, 2], b1[i, 3], exit_bar_1m, side):
                        triggered = True; reason = "1M_BAR"
            if not triggered and check_3m_exit:
                if (side == "LONG" and k_3m[bk_3m] > exit_k_3m_min) or (side == "SHORT" and k_3m[bk_3m] < 100 - exit_k_3m_min):
                    if _bar_match(b3[bk_3m - 1, 2], b3[bk_3m - 1, 3], b3[bk_3m, 2], b3[bk_3m, 3], exit_bar_3m, side):
                        triggered = True; reason = "3M_BAR"
            if not triggered and check_15m_exit:
                if (side == "LONG" and k_15m[bk_15m] > exit_k_15m_min) or (side == "SHORT" and k_15m[bk_15m] < 100 - exit_k_15m_min):
                    if _bar_match(bars_15m[bk_15m - 1, 2], bars_15m[bk_15m - 1, 3],
                                  bars_15m[bk_15m, 2], bars_15m[bk_15m, 3], exit_bar_15m, side):
                        triggered = True; reason = "15M_BAR"
            if triggered:
                trades.append({"side": side, "entry_price": pos_price, "exit_price": price,
                               "entry_ts": pos_ts, "exit_ts": ts, "gain_pct": gain - fee_pct,
                               "reason": reason})
                exit_ts = ts
                exit_k15 = float(k_15m[bk_15m]); exit_k15_prev = float(k_15m[bk_15m - 1])
                pos_ts = -1.0
                continue
        else:
            # ---- ENTRY ----
            if exit_ts > 0 and reentry_cooldown_s > 0 and (ts - exit_ts) < reentry_cooldown_s:
                continue
            if exit_ts > 0 and require_bounce:
                k15_was_falling = exit_k15 < exit_k15_prev
                if k15_was_falling:
                    bar3_bounced = _bar_match(b3[bk_3m - 1, 2], b3[bk_3m - 1, 3], b3[bk_3m, 2], b3[bk_3m, 3],
                                              reentry_bounce_bar_3m, side)
                    if side == "LONG":
                        k15_bounced = k_15m[bk_15m] < reentry_bounce_k_15m_max and k_15m[bk_15m] > k_15m[bk_15m - 1]
                    else:
                        k15_bounced = k_15m[bk_15m] > 100 - reentry_bounce_k_15m_max and k_15m[bk_15m] < k_15m[bk_15m - 1]
                    if reentry_bounce_mode == "3M_BAR_ONLY":
                        if not bar3_bounced: continue
                    elif reentry_bounce_mode == "K15M_ONLY":
                        if not k15_bounced: continue
                    elif reentry_bounce_mode == "3M_BAR_AND_K15M":
                        if not (bar3_bounced and k15_bounced): continue
                    else:  # OR
                        if not (bar3_bounced or k15_bounced): continue
            # HTF runway
            if side == "LONG":
                if k_1h[bk_1h] >= k_1h_max: continue
                if k_4h[bk_4h] >= k_4h_max: continue
                if k_15m[bk_15m] >= k_15m_max: continue
            else:
                if k_1h[bk_1h] <= 100 - k_1h_max: continue
                if k_4h[bk_4h] <= 100 - k_4h_max: continue
                if k_15m[bk_15m] <= 100 - k_15m_max: continue
            # === 2026-04-24 NEW FILTERS: VWAP deviation, BB squeeze, pin-bar ===
            # VWAP-deviation entry: only enter when price is stretched >X% from VWAP
            _vwap_dev_min = variant.get("vwap_dev_min_pct", 0.0)
            if _vwap_dev_min > 0:
                vwap_3m_arr = pc.get("vwap_3m")
                if vwap_3m_arr is not None and bk_3m < len(vwap_3m_arr) and vwap_3m_arr[bk_3m] > 0:
                    vwap_val = vwap_3m_arr[bk_3m]
                    dev_pct = (price - vwap_val) / vwap_val * 100.0
                    # LONG wants price BELOW VWAP (oversold), SHORT wants price ABOVE
                    if side == "LONG" and dev_pct > -_vwap_dev_min: continue
                    if side == "SHORT" and dev_pct < _vwap_dev_min: continue
            # BB-squeeze entry: only enter when 3m BB width is at local minimum (compression)
            _bb_squeeze_pct = variant.get("bb_squeeze_max_pct", 0.0)
            if _bb_squeeze_pct > 0:
                bbw_arr = pc.get("bbw_3m")
                if bbw_arr is not None and bk_3m < len(bbw_arr) and bbw_arr[bk_3m] > 0:
                    if bbw_arr[bk_3m] > _bb_squeeze_pct: continue
            # Pin-bar entry (1m): upper/lower wick > N× body = rejection at extreme
            _pin_ratio = variant.get("pin_bar_ratio", 0.0)
            if _pin_ratio > 0:
                op = raw_1m[i, 1]; hi = raw_1m[i, 2]; lo = raw_1m[i, 3]; cl = raw_1m[i, 4]
                body = abs(cl - op)
                if body > 0:
                    if side == "LONG":
                        lower_wick = min(op, cl) - lo
                        if lower_wick < _pin_ratio * body: continue
                    else:
                        upper_wick = hi - max(op, cl)
                        if upper_wick < _pin_ratio * body: continue
            # HTF K alignment (stricter — all HTF TFs in deep oversold)
            if htf_align:
                if side == "LONG":
                    if k_15m[bk_15m] > htf_align_max: continue
                    if k_1h[bk_1h] > htf_align_max: continue
                    if k_4h[bk_4h] > htf_align_max: continue
                else:
                    if k_15m[bk_15m] < 100 - htf_align_max: continue
                    if k_1h[bk_1h] < 100 - htf_align_max: continue
                    if k_4h[bk_4h] < 100 - htf_align_max: continue
            # 1m entry gate
            if use_1m_entry:
                if side == "LONG":
                    if k_1m[i] >= k_1m_max: continue
                else:
                    if k_1m[i] <= 100 - k_1m_max: continue
                if not _bar_match(b1[i - 1, 2], b1[i - 1, 3], b1[i, 2], b1[i, 3], entry_bar_1m, side): continue
                vol_recent = raw_1m[i, 5]
                vol_avg = raw_1m[i - 20:i, 5].mean()
                if vol_avg > 0 and vol_recent < vol_mult * vol_avg: continue
            # 3m entry gate (also fires if pair_align on)
            if use_3m_entry:
                if side == "LONG":
                    if k_3m[bk_3m] >= k_3m_max: continue
                else:
                    if k_3m[bk_3m] <= 100 - k_3m_max: continue
                if not _bar_match(b3[bk_3m - 1, 2], b3[bk_3m - 1, 3], b3[bk_3m, 2], b3[bk_3m, 3], entry_bar_3m, side): continue
                # 3m volume gate (fixes the bug)
                if not use_1m_entry:
                    vol_recent_3m = bars_3m[bk_3m, 5]
                    vol_avg_3m = bars_3m[max(0, bk_3m - 20):bk_3m, 5].mean() if bk_3m >= 1 else 0.0
                    if vol_avg_3m > 0 and vol_recent_3m < vol_mult * vol_avg_3m: continue
            pos_ts = float(ts); pos_price = float(price); pos_peak = 0.0
    # EOT MTM
    if pos_ts > 0:
        final_price = float(raw_1m[-1, 4]); final_ts = float(raw_1m[-1, 0])
        if side == "LONG":
            gain = (final_price - pos_price) / pos_price * 100.0
        else:
            gain = (pos_price - final_price) / pos_price * 100.0
        trades.append({"side": side, "entry_price": pos_price, "exit_price": final_price,
                       "entry_ts": pos_ts, "exit_ts": final_ts, "gain_pct": gain - fee_pct,
                       "reason": "EOT_MTM"})
    return trades


_WORKER_PRECOMP: Dict[str, Dict] = {}


def _worker_init(cache_path: str):
    global _WORKER_PRECOMP
    with open(cache_path, "rb") as f:
        c = pickle.load(f)
    _WORKER_PRECOMP = {k: v for k, v in c.items() if k != "_symbols"}


def _eval_variant(args: Tuple[int, Dict, float]) -> Dict:
    vid, variant, fee = args
    t0 = time.time()
    all_trades = []
    sides = ("LONG", "SHORT") if variant["side_mode"] == "BOTH" else (variant["side_mode"].replace("_ONLY", ""),)
    for sym, pc in _WORKER_PRECOMP.items():
        try:
            for side in sides:
                trs = walk_symbol(pc, variant, side, fee)
                for t in trs:
                    t["symbol"] = sym
                    all_trades.append(t)
        except Exception:
            continue
    if not all_trades:
        return {"vid": vid, "variant": variant, "n": 0, "total_pct": 0.0, "mean_pct": 0.0,
                "wr": 0.0, "pool_sharpe": 0.0, "max_dd_pct": 0.0, "syms_with_trades": 0,
                "elapsed_s": round(time.time() - t0, 2)}
    returns = [t["gain_pct"] for t in all_trades]
    total = sum(returns)
    mean_r = statistics.mean(returns)
    std_r = statistics.stdev(returns) if len(returns) > 1 else 0.0
    sharpe = mean_r / std_r if std_r > 0 else 0.0
    wins = sum(1 for r in returns if r > 0)
    wr = wins / len(returns) * 100.0
    eq = np.cumsum(returns)
    peak = np.maximum.accumulate(eq); dd = peak - eq
    max_dd = float(dd.max()) if len(dd) else 0.0
    syms = set(t["symbol"] for t in all_trades)
    return {"vid": vid, "variant": variant,
            "n": len(returns), "total_pct": round(total, 2),
            "mean_pct": round(mean_r, 4), "wr": round(wr, 2),
            "pool_sharpe": round(sharpe, 4), "max_dd_pct": round(max_dd, 2),
            "syms_with_trades": len(syms),
            "elapsed_s": round(time.time() - t0, 2)}


# ---------- Variant grid generator ----------

def make_grid(grid_name: str) -> List[Dict]:
    base = {
        "tf_mode": "1M_ONLY", "exit_mode": "1M_ONLY",
        "vol_mult": 1.5,
        "k_1m_max": 25, "k_3m_max": 40, "k_15m_max": 65, "k_1h_max": 80, "k_4h_max": 85,
        "exit_k_1m_min": 98, "exit_k_3m_min": 95, "exit_k_15m_min": 95,
        "entry_bar_1m": "HH_AND_HL", "entry_bar_3m": "HH",
        "exit_bar_1m": "LL_AND_LH", "exit_bar_3m": "LL_OR_LH", "exit_bar_15m": "LL_OR_LH",
        "max_hold_min": 5, "stall_gain": 0.0,
        "disable_stall": True,  # 2026-04-24: STALL=0% WR across 177 trades → default OFF
        "ha_1m": False, "ha_3m": False,
        "htf_align": False, "htf_align_max": 50,
        "pair_align": False,
        "side_mode": "BOTH",
        "reentry_require_bounce": True,
        "reentry_bounce_mode": "3M_BAR_OR_K15M",
        "reentry_bounce_k_15m_max": 25,
        "reentry_bounce_bar_3m": "HH_AND_HL",
        "reentry_cooldown_s": 0,
        # --- 2026-04-24 NEW TECHNIQUES (all default OFF / zero) ---
        "atr_tp_mult": 0.0,      # exit when gain >= N × 3m ATR%
        "atr_sl_mult": 0.0,      # exit when gain <= -N × 3m ATR% (technical stop)
        "pg_arm_pct": 0.0,       # peak-giveback arms when gain reaches this %
        "pg_giveback_pct": 0.0,  # exit when gain drops by this % from peak
        "vwap_dev_min_pct": 0.0, # require price ≥X% stretched from 3m-VWAP (mean-revert scalp)
        "bb_squeeze_max_pct": 0.0,# require 3m BB width ≤X% (compression-breakout scalp)
        "pin_bar_ratio": 0.0,    # require entry bar wick ≥X× body (rejection-based scalp)
    }
    if grid_name == "coarse":
        axes = {
            "tf_mode":        ["1M_ONLY", "1M_AND_3M"],
            "exit_mode":      ["ANY", "1M_ONLY", "3M_ONLY"],
            "vol_mult":       [1.0, 2.0, 3.0],
            "k_1m_max":       [20, 30, 40],
            "exit_k_1m_min":  [90, 95, 98],
            "max_hold_min":   [5, 10, 20],
            "stall_gain":     [-0.1, 0.0, 0.3],
            "ha_1m":          [False, True],
            "ha_3m":          [False, True],
            "htf_align":      [False, True],
            "pair_align":     [False, True],
            "side_mode":      ["LONG_ONLY", "SHORT_ONLY", "BOTH"],
        }
    elif grid_name == "medium":
        axes = {
            "tf_mode":        ["1M_ONLY", "3M_ONLY", "1M_AND_3M"],
            "exit_mode":      ["ANY", "1M_ONLY", "3M_ONLY", "15M_ONLY"],
            "vol_mult":       [1.0, 1.5, 2.0, 3.0, 4.0],
            "k_1m_max":       [15, 20, 25, 30, 40],
            "exit_k_1m_min":  [85, 90, 95, 98],
            "max_hold_min":   [3, 5, 10, 15, 30],
            "stall_gain":     [-0.1, 0.0, 0.2, 0.5],
            "ha_1m":          [False, True],
            "ha_3m":          [False, True],
            "htf_align":      [False, True],
            "pair_align":     [False, True],
            "side_mode":      ["LONG_ONLY", "SHORT_ONLY", "BOTH"],
            "reentry_bounce_mode": ["3M_BAR_ONLY", "K15M_ONLY", "3M_BAR_OR_K15M", "3M_BAR_AND_K15M"],
        }
    elif grid_name == "tiny":
        axes = {
            "tf_mode": ["1M_ONLY"], "exit_mode": ["1M_ONLY", "ANY"],
            "vol_mult": [1.5, 3.0], "k_1m_max": [20, 30],
            "ha_1m": [False, True], "htf_align": [False, True],
            "side_mode": ["BOTH"],
        }
    elif grid_name == "winners_refined_v2":
        # 2026-04-24 v2: unlocks max_hold_min axis (user question: does it matter at 3m TF?)
        # + keeps BOTH in side_mode (prior sweep's LONG_ONLY bias was bull-window artifact)
        axes = {
            "disable_stall":     [True],
            "tf_mode":           ["3M_ONLY"],
            "exit_mode":         ["3M_ONLY", "ANY", "15M_ONLY"],
            "side_mode":         ["BOTH", "LONG_ONLY", "SHORT_ONLY"],
            "max_hold_min":      [5, 10, 15, 30],
            "atr_sl_mult":       [0.0],
            "k_1m_max":          [20, 30],
            "vol_mult":          [1.0, 1.5, 2.0],
            "exit_k_1m_min":     [90, 95, 98],
            "ha_3m":             [False, True],
            "atr_tp_mult":       [0.8, 1.0, 1.5, 2.0, 3.0],
            "pg_arm_pct":        [0.0, 0.3, 0.5, 1.0],
            "pg_giveback_pct":   [0.0, 0.15, 0.2, 0.3],
            "vwap_dev_min_pct":  [0.2, 0.3, 0.5, 0.7],
            "bb_squeeze_max_pct":[0.0, 1.5, 3.0],
            "pin_bar_ratio":     [0.0, 2.0, 2.5, 3.0],
        }
    elif grid_name == "winners_refined":
        # 2026-04-24 refined from top-10 winners of techniques_20260424_0410 sweep:
        # fix tf=3M_ONLY, side=LONG_ONLY, max_hold=5, atr_sl=0, disable_stall=True.
        # Sweep finer around the winning region of ATR_TP, VWAP_dev, BB_squeeze, PG.
        axes = {
            "disable_stall":     [True],
            "tf_mode":           ["3M_ONLY"],
            "exit_mode":         ["3M_ONLY", "ANY", "15M_ONLY"],
            "side_mode":         ["LONG_ONLY", "BOTH"],
            "max_hold_min":      [5],
            "atr_sl_mult":       [0.0],  # confirmed winner: no % SL
            "k_1m_max":          [20, 25, 30],
            "k_3m_max":          [30, 40, 50],
            "vol_mult":          [1.0, 1.5, 2.0, 2.5],
            "exit_k_1m_min":     [90, 95, 98],
            "exit_k_3m_min":     [90, 95, 98],
            "ha_1m":             [False, True],
            "ha_3m":             [False, True],
            "htf_align":         [False, True],
            "atr_tp_mult":       [0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0],
            "pg_arm_pct":        [0.0, 0.3, 0.5, 0.7, 1.0, 1.5],
            "pg_giveback_pct":   [0.0, 0.1, 0.15, 0.2, 0.3, 0.4],
            "vwap_dev_min_pct":  [0.2, 0.3, 0.4, 0.5, 0.7, 1.0],
            "bb_squeeze_max_pct":[0.0, 1.0, 1.5, 2.0, 3.0],
            "pin_bar_ratio":     [0.0, 1.5, 2.0, 2.5, 3.0],
            "reentry_bounce_mode": ["3M_BAR_ONLY", "K15M_ONLY", "3M_BAR_OR_K15M"],
            "reentry_cooldown_s":[0, 60, 180],
        }
    elif grid_name == "long_only_wide":
        # 2026-04-25: LONG_ONLY confirmed winner. Sweep wider K thresholds to increase trade count
        # without destroying WR. Fix 3M_ONLY entry + 15M_ONLY exit (confirmed best combo).
        # Also test removing VWAP gate vs tight VWAP to see how many trades are gated.
        axes = {
            "disable_stall":     [True],
            "tf_mode":           ["3M_ONLY"],
            "exit_mode":         ["15M_ONLY"],
            "side_mode":         ["LONG_ONLY"],
            "k1m_max":           [20, 30, 40, 50],
            "k_3m_max":          [40, 50, 60, 70, 80],
            "k_15m_max":         [65, 75, 80, 85],
            "k_1h_max":          [70, 80, 85],
            "k_4h_max":          [75, 85],
            "vol_mult":          [1.0, 1.5],
            "atr_tp_mult":       [0.8, 1.0, 1.5],
            "vwap_dev_min_pct":  [0.0, 0.2, 0.3],
        }
    elif grid_name == "exit_finegrain":
        # 2026-04-25: Fix best-known entry (3M/LONG/k1m=30/k3m=40/vwap=0.2/vol=1.0).
        # Sweep exit combinations to find highest WR exit path.
        axes = {
            "disable_stall":     [True],
            "tf_mode":           ["3M_ONLY"],
            "side_mode":         ["LONG_ONLY"],
            "k_1m_max":          [30],
            "k_3m_max":          [40],
            "vwap_dev_min_pct":  [0.2],
            "vol_mult":          [1.0],
            "exit_mode":         ["15M_ONLY", "ANY", "3M_ONLY"],
            "exit_k_1m_min":     [85, 90, 95, 98],
            "exit_k_3m_min":     [80, 90, 95, 98],
            "exit_k_15m_min":    [70, 80, 90, 95],
            "atr_tp_mult":       [0.5, 0.8, 1.0, 1.5, 2.0, 2.5],
            "pg_arm_pct":        [0.0, 0.3, 0.5, 0.8, 1.0],
            "pg_giveback_pct":   [0.0, 0.1, 0.15, 0.2, 0.3],
        }
    elif grid_name == "entry_tight":
        # 2026-04-25: Fix best-known exit (15M_ONLY/atr_tp=0.8). Sweep entry filters
        # to find tighter entry that increases WR further: VWAP, BB, pin-bar, HTF K.
        axes = {
            "disable_stall":     [True],
            "tf_mode":           ["3M_ONLY"],
            "exit_mode":         ["15M_ONLY"],
            "side_mode":         ["LONG_ONLY"],
            "atr_tp_mult":       [0.8],
            "k_1m_max":          [20, 25, 30],
            "k_3m_max":          [30, 40, 50],
            "vwap_dev_min_pct":  [0.0, 0.2, 0.3, 0.5, 0.7, 1.0],
            "bb_squeeze_max_pct":[0.0, 1.5, 2.0, 3.0, 5.0],
            "pin_bar_ratio":     [0.0, 1.5, 2.0, 2.5, 3.0],
            "htf_align":         [False, True],
            "htf_align_max":     [40, 50, 60],
            "vol_mult":          [1.0, 1.5, 2.0],
        }
    elif grid_name == "techniques":
        # 2026-04-24: NO-STALL grid focusing on new techniques (ATR TP/SL, peak-giveback,
        # VWAP-deviation, BB-squeeze, pin-bar). Disable_stall is ALWAYS True here.
        axes = {
            "disable_stall":     [True],
            "tf_mode":           ["1M_ONLY", "3M_ONLY", "1M_AND_3M"],
            "exit_mode":         ["ANY", "15M_ONLY", "3M_ONLY"],
            "k_1m_max":          [20, 30],
            "vol_mult":          [1.5, 2.5],
            "side_mode":         ["LONG_ONLY", "SHORT_ONLY", "BOTH"],
            "htf_align":         [False, True],
            "atr_tp_mult":       [0.0, 1.5, 2.0, 3.0],
            "atr_sl_mult":       [0.0, 2.0, 3.0],
            "pg_arm_pct":        [0.0, 0.3, 0.5, 1.0],
            "pg_giveback_pct":   [0.0, 0.2, 0.4],
            "vwap_dev_min_pct":  [0.0, 0.3, 0.7, 1.5],
            "bb_squeeze_max_pct":[0.0, 1.5, 3.0],
            "pin_bar_ratio":     [0.0, 1.5, 2.5],
        }
    elif grid_name == "techniques_random":
        # Random sample from the large Cartesian product of techniques axes
        axes = {
            "disable_stall":     [True],
            "tf_mode":           ["1M_ONLY", "3M_ONLY", "1M_AND_3M"],
            "exit_mode":         ["ANY", "15M_ONLY", "3M_ONLY", "1M_ONLY"],
            "k_1m_max":          [15, 20, 25, 30, 40],
            "k_3m_max":          [30, 40, 50, 60],
            "vol_mult":          [1.0, 1.5, 2.0, 2.5, 3.0, 4.0],
            "exit_k_1m_min":     [85, 90, 95, 98],
            "side_mode":         ["LONG_ONLY", "SHORT_ONLY", "BOTH"],
            "htf_align":         [False, True],
            "htf_align_max":     [25, 40, 50],
            "ha_1m":             [False, True],
            "ha_3m":             [False, True],
            "atr_tp_mult":       [0.0, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0],
            "atr_sl_mult":       [0.0, 1.5, 2.0, 2.5, 3.0, 4.0],
            "pg_arm_pct":        [0.0, 0.2, 0.3, 0.5, 0.7, 1.0, 1.5],
            "pg_giveback_pct":   [0.0, 0.15, 0.2, 0.3, 0.4, 0.5],
            "vwap_dev_min_pct":  [0.0, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0],
            "bb_squeeze_max_pct":[0.0, 1.0, 1.5, 2.0, 3.0, 5.0],
            "pin_bar_ratio":     [0.0, 1.0, 1.5, 2.0, 2.5, 3.0],
            "reentry_bounce_mode": ["3M_BAR_ONLY", "K15M_ONLY", "3M_BAR_OR_K15M", "3M_BAR_AND_K15M"],
            "reentry_cooldown_s":[0, 60, 180, 300],
        }
    else:  # "orthogonal" — each axis varies alone around base
        out = [base.copy()]
        ortho_axes = {
            "vol_mult":       [1.0, 2.0, 2.5, 3.0, 4.0],
            "k_1m_max":       [15, 20, 30, 40],
            "exit_k_1m_min":  [85, 90, 92, 95],
            "max_hold_min":   [3, 10, 15, 30],
            "stall_gain":     [-0.1, 0.2, 0.5, 1.0],
            "ha_1m":          [True],
            "ha_3m":          [True],
            "htf_align":      [True],
            "pair_align":     [True],
            "tf_mode":        ["3M_ONLY", "1M_AND_3M"],
            "exit_mode":      ["ANY", "3M_ONLY", "15M_ONLY"],
            "side_mode":      ["LONG_ONLY", "SHORT_ONLY"],
            "reentry_bounce_mode": ["3M_BAR_ONLY", "K15M_ONLY", "3M_BAR_AND_K15M"],
        }
        for k, values in ortho_axes.items():
            for v in values:
                var = base.copy(); var[k] = v
                out.append(var)
        return out
    keys = list(axes.keys())
    values = [axes[k] for k in keys]
    # Estimate cartesian size — if > 200K, use lazy per-axis random sampling instead.
    total = 1
    for vs in values:
        total *= max(1, len(vs))
    if total > 200_000:
        # Lazy: sample 20000 unique variants by picking independently from each axis.
        # Deduplicate via frozenset tuple. Cap at 20k to bound memory.
        seen = set()
        variants = []
        target = 20000
        attempts = 0
        while len(variants) < target and attempts < target * 5:
            attempts += 1
            combo = tuple(random.choice(vs) for vs in values)
            if combo in seen: continue
            seen.add(combo)
            v = base.copy()
            for k, x in zip(keys, combo): v[k] = x
            variants.append(v)
        return variants
    variants = []
    for combo in itertools.product(*values):
        v = base.copy()
        for k, x in zip(keys, combo):
            v[k] = x
        variants.append(v)
    return variants


def parse_deadline(args) -> float:
    if args.deadline_iso:
        return _iso_to_epoch(args.deadline_iso)
    return time.time() + args.deadline_hours * 3600


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", choices=["tiny", "orthogonal", "coarse", "medium", "techniques", "techniques_random", "winners_refined", "winners_refined_v2", "long_only_wide", "exit_finegrain", "entry_tight"], default="orthogonal")
    ap.add_argument("--variants", type=int, default=0, help="Cap (0 = no cap)")
    ap.add_argument("--random-sample", type=int, default=0, help="Random-sample N from the grid (0 = use full grid)")
    ap.add_argument("--workers", type=int, default=max(1, os.cpu_count() - 2))
    ap.add_argument("--max-symbols", type=int, default=0)
    ap.add_argument("--symbols", type=str, default="")
    ap.add_argument("--symbols-file", type=str, default="", help="JSON file with {top: [{symbol: X}, ...]}")
    ap.add_argument("--deadline-hours", type=float, default=30.0)
    ap.add_argument("--deadline-iso", type=str, default="")
    ap.add_argument("--fee", type=float, default=FEE_PCT)
    ap.add_argument("--out-tag", type=str, default="")
    ap.add_argument("--force-precomp", action="store_true")
    args = ap.parse_args()
    if args.symbols_file:
        with open(args.symbols_file) as f:
            d = json.load(f)
        symbols = [r["symbol"] for r in d.get("top", d)]
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = sorted({Path(p).stem.replace("_1m", "") for p in glob.glob(str(KLINES_DIR / "*_1m.json"))})
    if args.max_symbols:
        symbols = symbols[: args.max_symbols]
    print(f"[main] symbols={len(symbols)} grid={args.grid} workers={args.workers}")
    build_precomputed(symbols, force=args.force_precomp)
    variants = make_grid(args.grid)
    random.seed(42)
    if args.random_sample and args.random_sample < len(variants):
        variants = random.sample(variants, args.random_sample)
    if args.variants and args.variants < len(variants):
        variants = variants[: args.variants]
    print(f"[main] {len(variants)} variants generated")
    deadline = parse_deadline(args)
    remaining_s = max(0.0, deadline - time.time())
    print(f"[main] deadline in {remaining_s / 3600:.2f}h ({datetime.fromtimestamp(deadline, timezone.utc).isoformat()})")
    tag = args.out_tag or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = OUT_DIR / f"results_{tag}.jsonl"
    done_path = OUT_DIR / f"done_{tag}.txt"
    # Resume: read existing
    done_vids = set()
    if done_path.exists():
        with open(done_path) as f:
            done_vids = set(int(x) for x in f.read().split() if x.strip())
        print(f"[main] resuming, {len(done_vids)} already done")
    jobs = [(i, v, args.fee) for i, v in enumerate(variants) if i not in done_vids]
    print(f"[main] {len(jobs)} variants to run")
    stop_flag = mp.Event()
    def sig_handler(*_):
        print("\n[main] SIGINT received — stopping after current jobs")
        stop_flag.set()
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)
    t_start = time.time()
    completed = 0
    best_by_total = []
    with mp.Pool(processes=args.workers, initializer=_worker_init, initargs=(str(PRECOMP_CACHE),)) as pool:
        out_f = open(out_path, "a")
        done_f = open(done_path, "a")
        try:
            for result in pool.imap_unordered(_eval_variant, jobs, chunksize=1):
                out_f.write(json.dumps(result) + "\n"); out_f.flush()
                done_f.write(f"{result['vid']}\n"); done_f.flush()
                completed += 1
                best_by_total.append(result)
                best_by_total.sort(key=lambda r: r["total_pct"], reverse=True)
                best_by_total = best_by_total[:10]
                if time.time() > deadline or stop_flag.is_set():
                    print("[main] deadline or stop — halting")
                    pool.terminate(); break
                if completed % 25 == 0 or completed <= 5:
                    eps = (time.time() - t_start) / completed
                    eta = eps * (len(jobs) - completed)
                    print(f"[{completed}/{len(jobs)}] {completed/(time.time()-t_start):.2f} var/s  "
                          f"eta_h={eta/3600:.2f}  best_total={best_by_total[0]['total_pct']:+.1f}% "
                          f"(n={best_by_total[0]['n']}, tf={best_by_total[0]['variant']['tf_mode']}, "
                          f"exit={best_by_total[0]['variant']['exit_mode']}, side={best_by_total[0]['variant']['side_mode']})")
        finally:
            out_f.close(); done_f.close()
    print("\n--- TOP 20 by TOTAL_PCT ---")
    results = []
    with open(out_path) as f:
        for line in f:
            try: results.append(json.loads(line))
            except Exception: continue
    results.sort(key=lambda r: r["total_pct"], reverse=True)
    for r in results[:20]:
        v = r["variant"]
        print(f"  total={r['total_pct']:+10.2f}%  n={r['n']:5d}  WR={r['wr']:5.1f}%  mean={r['mean_pct']:+.3f}%  "
              f"Sharpe={r['pool_sharpe']:+.3f}  DD={r['max_dd_pct']:.1f}%  "
              f"tf={v['tf_mode']} exit={v['exit_mode']} vol={v['vol_mult']} k1m={v['k_1m_max']} "
              f"ek1m={v['exit_k_1m_min']} hold={v['max_hold_min']} stall={v['stall_gain']} "
              f"ha1m={v['ha_1m']} ha3m={v['ha_3m']} htf={v['htf_align']} pair={v['pair_align']} side={v['side_mode']}")
    print(f"\nResults file: {out_path}")


if __name__ == "__main__":
    main()
