#!/usr/bin/env python3
"""per_sym_engine_crypto — vectorized per-symbol crypto backtest engine.

Reads 3m base OHLC from NPZ, resamples to {15m,1h,4h,D,W} in numpy, computes BB/WT/DC
at any (length, std/chan/avg/period) per TF, runs vectorized multi-TF entry/exit with
≥MIN_TFS_AGREE_FLOOR (=2) hard floor, deducts commission per side, returns trade list +
canonical metrics. Crypto-only — keep stocks completely separate per user 2026-05-05.

Source: backtest_v8/indicators/{SYM}.npz (3m granularity, ~6.3yr coverage).
NOT a full mirror of ez_manage live logic — multi-TF DC-break + WT-cross + BB-position
core. Additional v8 paths (RZ_EXIT, SATOSHIT, hedge, reentry, augment) ported in phases.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.signal import lfilter

from wt_dc_hierarchy import compute_hierarchy_full

# Import v8_quick_engine vectorized signal aggregators — already include all entry/exit paths
# (BTC_BREAKOUT, ACCEL_RAMP, BB_SQUEEZE, AUGMENT, DELTA_ENGINE, SATOSHIT, FH_MOMENTUM, DC_DAYTRADE,
# K_ZONE, MFI_ENTRY, VWAP_FILTER, STDEV_BREAKOUT/BOUNCE, RZ_BREAKOUT, ATR_ADAPTIVE_SIZING,
# WINNER_PROTECT, RANK_CONVICTION, etc.). User 2026-05-05: "use all functions we have built — years of work".
def _import_v8():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from v8_quick_engine import (
        compute_entry_signals as _ce,
        compute_exit_signals as _cx,
        compute_rz_cascade_signals as _rz,
        QuickConfig as _QC,
        simulate as _sim,
    )
    return _ce, _cx, _rz, _QC, _sim

_v8_compute_entry = None
_v8_compute_exit = None
_rz_cascade_signals = None
_v8_QuickConfig = None
_v8_simulate = None

def _ensure_v8_loaded():
    global _v8_compute_entry, _v8_compute_exit, _rz_cascade_signals, _v8_QuickConfig, _v8_simulate
    if _v8_compute_entry is None:
        _v8_compute_entry, _v8_compute_exit, _rz_cascade_signals, _v8_QuickConfig, _v8_simulate = _import_v8()

ROOT = Path(__file__).resolve().parent

# Crypto commission: 0.02% Binance USDC maker + 0.02% slippage = 0.04% per side
CRYPTO_COMMISSION_PER_SIDE = 0.0004
COMMISSION_RT_PCT = 100.0 * 2.0 * CRYPTO_COMMISSION_PER_SIDE  # 0.08% RT in % units

# Hard floor: never trade on a single TF — multi-TF agreement required (user 2026-05-05).
MIN_TFS_AGREE_FLOOR = 2

# 3m bars per HTF bar
TF_BARS_3M = {'15m': 5, '1h': 20, '4h': 80, 'D': 480, 'W': 480 * 7, 'M': 480 * 30}
DECISION_TFS = ('15m', '1h', '4h', 'D')  # User 2026-05-05: include D so it can vote


@dataclass
class SymParams:
    BB_LEN_15m: int = 20
    BB_STD_15m: float = 2.0
    BB_LEN_1h: int = 20
    BB_STD_1h: float = 2.0
    BB_LEN_4h: int = 20
    BB_STD_4h: float = 2.0
    BB_LEN_D: int = 20
    BB_STD_D: float = 2.0
    WT_CHAN_15m: int = 10
    WT_AVG_15m: int = 21
    WT_CHAN_1h: int = 10
    WT_AVG_1h: int = 21
    WT_CHAN_4h: int = 10
    WT_AVG_4h: int = 21
    WT_CHAN_D: int = 10
    WT_AVG_D: int = 21
    DC_PERIOD_15m: int = 20
    DC_PERIOD_1h: int = 20
    DC_PERIOD_4h: int = 20
    DC_PERIOD_D: int = 20
    # User 2026-05-05: split MIN_TFS_AGREE for entry vs exit vs reentry. Entry tightest, exit looser, reentry looser still.
    # MIN_TFS_AGREE kept for back-compat (used as default for entry); engine reads the per-action knobs below.
    MIN_TFS_AGREE: int = 2
    MIN_TFS_AGREE_ENTRY: int = 3        # 3 of 4 TFs (15m/1h/4h/D) for entry — sweep 1-4
    MIN_TFS_AGREE_EXIT: int = 2         # 2 of 4 for exit — easier to leave (cap loss)
    MIN_TFS_AGREE_REENTRY: int = 2      # 2 of 4 for reentry path
    MIN_HOLD_BARS_15m: int = 5
    COOLDOWN_BARS_15m: int = 3
    USE_BB_FILTER: bool = True
    USE_WT_CROSS: bool = True
    USE_DC_BREAK: bool = True
    BB_TOP_THRESHOLD: float = 0.95
    BB_BOT_THRESHOLD: float = 0.05
    # USER 2026-05-05 + wt_dc_entry_scorer data: bb_pctb<0.2 = 90% WR for LONG. Default tight, sweep wider.
    BB_LONG_ENTRY_MAX: float = 0.20
    BB_SHORT_ENTRY_MIN: float = 0.80
    # BB auto-tune: per-symbol/per-TF flexible stdev that maximizes balanced touches (bb_auto_tune logic).
    BB_AUTO_TUNE_ENABLED: bool = False  # default off; sweep ON to test per-sym
    BB_AUTO_TUNE_LOOKBACK: int = 100   # bars for tune window
    BB_AUTO_TUNE_MIN_MULT: float = 1.5
    BB_AUTO_TUNE_MAX_MULT: float = 3.5
    REQUIRE_D_TREND: bool = False  # daily trend gate (HTF context filter)
    REQUIRE_W_TREND: bool = False  # weekly trend gate
    # Entry mode: 'dc_break' (close > prev dc_high) | 'pullback' (bb_pctb<EMA crossing) | 'or' (either)
    ENTRY_MODE: str = 'or'  # 'or' is loosest; profiler may set tighter
    # Additional entry paths (parallel signals — any may trigger; gated by WT for direction).
    ENTRY_WT_CROSS_EVENT_ENABLED: bool = True   # entry on WT just-crossed event (last N bars)
    ENTRY_WT_CROSS_LOOKBACK: int = 3            # bars to consider "just crossed"
    ENTRY_BB_EXTREME_BOUNCE_ENABLED: bool = True  # entry on bb_pctb crossing back from extreme
    ENTRY_BB_EXTREME_THRESHOLD: float = 0.10    # extreme = pctb<threshold (LONG) / pctb>(1-threshold) (SHORT)
    ENTRY_BB_SQUEEZE_RELEASE_ENABLED: bool = True  # entry on BB width expansion after compression
    ENTRY_BB_SQUEEZE_RATIO: float = 1.5         # current BB width > prior min × ratio = release
    ENTRY_BB_SQUEEZE_LOOKBACK: int = 20         # bars to find prior min width
    # Strategies sourced from internet research not present in v8 (see profiler doc):
    # 1) Liquidity sweep reversal (ICT/Wyckoff): wick below prev-low then reclaim
    ENTRY_LIQ_SWEEP_ENABLED: bool = True
    ENTRY_LIQ_SWEEP_LOOKBACK: int = 10  # rolling N bars to define "recent" low/high
    # 2) NR7 breakout (Crabel): narrowest range in 7 bars → next-bar break
    ENTRY_NR7_ENABLED: bool = True
    ENTRY_NR_LOOKBACK: int = 7  # bars; bar i has narrowest range of last N → break on i+1
    # 3) Volume spike + directional bar (VSA)
    ENTRY_VOL_SPIKE_ENABLED: bool = True
    ENTRY_VOL_SPIKE_RATIO: float = 2.0  # vol > rolling_mean(vol, lookback) × ratio
    ENTRY_VOL_SPIKE_LOOKBACK: int = 20
    # 4) EMA ribbon pullback (9/21/50 stacked + price reclaiming 21 from below for LONG)
    ENTRY_EMA_RIBBON_ENABLED: bool = True
    ENTRY_EMA_FAST: int = 9
    ENTRY_EMA_MID: int = 21
    ENTRY_EMA_SLOW: int = 50
    # 5) Williams %R extreme reclaim
    ENTRY_WILLR_ENABLED: bool = True
    ENTRY_WILLR_LOOKBACK: int = 14
    ENTRY_WILLR_OS_THRESHOLD: float = -85.0   # LONG: %R was below this then crosses above
    ENTRY_WILLR_OB_THRESHOLD: float = -15.0   # SHORT: %R was above this then crosses below
    # 6) Fair Value Gap (FVG) fill — ICT 3-bar imbalance
    ENTRY_FVG_ENABLED: bool = True
    # Exit refinements (v8 BTC_DEDICATED parity):
    EXIT_REQUIRE_BOTH: bool = False  # exit needs WT-flip AND BB-extreme (AND not OR) — holds winners longer
    EXIT_WT_ACCEL_ONLY: bool = False  # only exit when WT actually decelerating against side (vs simple cross)
    PARTIAL_PROFIT_LOCK_ENABLED: bool = False
    PARTIAL_PROFIT_LOCK_GAIN_PCT: float = 1.0  # at +1% gain, lock breakeven
    # PPL v2 fields (Phase 2A patch 1) — wired in walk_trades_dual when ENABLED
    PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT: float = 0.75
    PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT: float = 0.10
    PARTIAL_PROFIT_LOCK_FRAC: float = 0.5
    # X7 frozen-DC + abs-floor stop (Phase 2A patch 2)
    X7_FROZEN_DC_STOP_ENABLED: bool = False
    X7_FREEZE_DC_TF: str = '4h'
    X7_FREEZE_BB_TF: str = ''
    X7_ABS_FLOOR_PCT: float = -8.0
    # SMA50_D LT-direction filter (Phase 2A patch 3)
    REQUIRE_ABOVE_SMA50_D: bool = False
    # HEDGE_HTF_VETO + HTF_TREND_VETO (Phase 2A patch 4)
    HEDGE_HTF_VETO_ENABLED: bool = False
    HTF_TREND_VETO_ENABLED: bool = False
    # Reentry / reverse paths:
    REVERSE_ON_EXIT_ENABLED: bool = False   # at exit, open opposite side if its signal fires same bar
    FOLLOW_THROUGH_REENTRY_ENABLED: bool = False  # after exit, re-enter same side on continuation
    FOLLOW_THROUGH_MIN_MOVE_PCT: float = 0.05    # required favorable move % within window
    FOLLOW_THROUGH_WINDOW_BARS: int = 5
    # Separate breakout entry path (v8 BTC_BREAKOUT_*) — looser gates for momentum continuation:
    BREAKOUT_PATH_ENABLED: bool = False
    BREAKOUT_HTF_MIN_ALIGNED: int = 1  # vs main MIN_TFS_AGREE; this path can use looser HTF rule
    BREAKOUT_MIN_HOLD_BARS: int = 1     # short min-hold for breakouts (mirror v8 BTC_BREAKOUT_MIN_HOLD_BARS)
    # Booster filters (NPZ-precomputed — no per-bar compute required)
    FUNDING_GATE_ENABLED: bool = True   # block LONG when funding paid heavy (> threshold), block SHORT inverse
    FUNDING_GATE_LONG_MAX: float = 0.0005   # 0.05% per 8h: above this, longs are "paying" — block
    FUNDING_GATE_SHORT_MIN: float = -0.0005  # below this, shorts paying — block
    OI_GATE_ENABLED: bool = True
    OI_GATE_OI_CHANGE_MIN: float = -10.0  # block LONG if oi_change_1h_3m < X%; block SHORT if > -X%
    WT_ACCEL_GATE_ENABLED: bool = True   # require wt_acceleration on side direction (LONG: accel_1h>0)
    WT_ACCEL_GATE_TF: str = '1h'   # tf to read acceleration from
    DIVERGENCE_BLOCK_ENABLED: bool = True  # block LONG entries if bearish divergence (price HH + WT LH); block SHORT inverse
    DIVERGENCE_LB: int = 20   # bars to look back for prior swing
    # AUGMENT pyramid (user directive 2026-05-05: NO EXCEPTIONS — must be in test even if rare on short-hold strategies).
    # At each gain level, add a leg at current price. Each leg = separate trade for accounting.
    AUGMENT_ENABLED: bool = True
    AUGMENT_LEVELS_PCT: tuple = (1.0, 2.0, 3.0, 4.0)  # gain % thresholds for L1..L4
    # Mean-reversion REENTRY (above OR below exit price — distinct from FOLLOW_THROUGH which is favorable-only).
    REENTRY_MEAN_REV_ENABLED: bool = False  # off by default — sweep finds when to enable
    REENTRY_MEAN_REV_TOLERANCE_PCT: float = 0.30  # price returns within ±X% of exit price → reentry same side
    REENTRY_MEAN_REV_WINDOW_BARS: int = 10
    # HEDGE: open opposite-side position when wt1_3m flips against position direction.
    # USER 2026-05-05: required pair for NOLOSS — default ON. Trigger is signal-driven, not %.
    HEDGE_ENABLED: bool = True
    HEDGE_SIZE_FRAC: float = 0.5    # hedge size as fraction of primary (only "magnitude" param — not a trigger)
    # NOLOSS: block loss exits unless technicals fire — signal-driven only, no fixed %.
    # USER 2026-05-05: NOLOSS *requires* HEDGE_ENABLED — without hedging, ONE bad trade ruins the account.
    # NEVER use fixed % anywhere — exits are technical-signal-driven, hedge is signal-driven.
    NOLOSS_ENABLED: bool = True
    # SIGNAL-DRIVEN PEAK PROTECT (replaces fixed % peak-giveback):
    # Once gain peaks, exit when wt1_15m flips against position (momentum loss after peak).
    PEAK_PROTECT_ENABLED: bool = True
    PEAK_PROTECT_REQUIRE_GAIN: bool = True  # only arm after gain has been positive at least once
    # SIGNAL-DRIVEN HEDGE TRIGGER (replaces fixed % drawdown trigger):
    # Hedge LONG when wt1_<TF> < wt2_<TF> on the chosen TF. Sweep TF among {3m, 15m, 1h}.
    HEDGE_WT_TRIGGER: bool = True
    HEDGE_WT_TF: str = '3m'             # one of: '3m', '15m', '1h' — TEST per user 2026-05-05
    HEDGE_CYCLES_ENABLED: bool = True   # when True: every flip-against = new hedge cycle (close on flip-back)
    # User 2026-05-05: allow fixed-% peak giveback (reluctantly) if needed for high WR.
    PEAK_GIVEBACK_FIXED_PCT_ENABLED: bool = False
    PEAK_GIVEBACK_FIXED_DROP_PCT: float = 0.5
    PEAK_GIVEBACK_FIXED_MIN_PEAK_PCT: float = 0.10
    # User 2026-05-05: re-allow HEDGE_TRIGGER_GAIN_PCT (% drawdown for hedge eligibility on top of WT trigger).
    HEDGE_TRIGGER_GAIN_PCT_ENABLED: bool = False
    HEDGE_TRIGGER_GAIN_PCT: float = -0.5
    # HARD_LOSS_PCT — equivalent of v8 BTC_HARD_LOSS_USD_PER_TRADE.
    # On override_btc_BEST: $5.4 per trade on $13.75 min size = -39%, but on typical $1k position = -0.54%.
    # This is the SOURCE of 86.8% WR — caps individual losses tightly so wins>>losses by count.
    HARD_LOSS_PCT_ENABLED: bool = True
    HARD_LOSS_PCT: float = 0.5  # exit if loss reaches this % — caps loss tightly
    # WT_DC HIERARCHY (port of wt_dc_hierarchy.py — pre-built vectorized cascade state machine).
    # When ON, replaces my per-tf signal aggregation with the cascade rule:
    #   LONG entry = LTF at_lower + delta_bull + cascade-confirmed by HTF
    # User 2026-05-05: "use all wt_dc functions we have built — years of work".
    USE_WT_DC_HIERARCHY: bool = False  # default off; profiler sweeps ON/OFF per-(sym, side)
    # RZ_CASCADE — port from v8_quick_engine.compute_rz_cascade_signals.
    # Detects per-TF: at_upper/at_lower (RZ proximity), breakout_up/down, reverse_up/down.
    # Entry = LTF breakout + alignment across HTFs. Exit = HTF rejection at resistance.
    USE_RZ_CASCADE: bool = False
    # USE_V8_AGGREGATORS — directly use v8_quick_engine.compute_entry_signals + compute_exit_signals.
    # These contain ALL the v8 paths (BTC_BREAKOUT, BB_SQUEEZE, DELTA, SATOSHIT, FH_MOM, DC_DAYTRADE,
    # K_ZONE, MFI, VWAP, STDEV, RZ_BREAKOUT, ATR_SIZING, WINNER_PROTECT, etc.) already vectorized.
    USE_V8_AGGREGATORS: bool = True
    # USE_V8_SIMULATE_DIRECT: when True, bypass MY walker entirely and call v8_quick_engine.simulate()
    # directly. This guarantees v8-equivalent sharpe numbers (matches historical sweep results).
    # User 2026-05-05 directive: use ALL vectorized funcs. Default ON when BASELINE_OVERRIDES present.
    USE_V8_SIMULATE_DIRECT: bool = True
    # K-zone params (consumed by v8 aggregators)
    K3M_FLOOR: float = 25.0
    CT_WT_VELOCITY_1H_MIN: float = 0.0
    # BASELINE_OVERRIDES — arbitrary dict of v8 knobs loaded from a proven override JSON.
    # User 2026-05-05: per_sym must START from proven >0.7 baselines and optimize UP.
    # When set, all keys are forwarded to QuickConfig before running v8 aggregators.
    # Profiler: load from override_btc_BEST.json (BTC) or override_v5_rz_loose.json (multi-sym), etc.
    BASELINE_OVERRIDES: dict = field(default_factory=dict)
    # USER 2026-05-06 mandate — backtest must reflect live safety guards or it's worthless.
    # Each guard is a sweepable knob; defaults match live config.py (all ON).
    BT_DC_BB_D_BREAK_REVERSE_ENABLED: bool = True   # close wrong-side on D-band break, open opposite
    BT_WT15M_AGAINST_FORCE_HEDGE_ENABLED: bool = True   # wt1_15m against → hedge no matter what
    BT_ALL_TF_AGAINST_CLOSE_ENABLED: bool = True   # all TFs against → close primary, hedge promotes
    BT_RIDICULOUS_HOLD_GUARD_ENABLED: bool = True   # gain≤-15% OR age>48h underwater → close
    BT_RIDICULOUS_LOSS_PCT: float = -15.0
    BT_RIDICULOUS_HOLD_HOURS: float = 48.0
    BT_UNDERWATER_HEDGE_OR_CLOSE_ENABLED: bool = True  # gain<0 + wt1_3m against → hedge or close
    RZ_CASCADE_AT_RZ_BAND_PCT: float = 1.0
    RZ_CASCADE_WT_DELTA_MIN: float = 0.1
    RZ_CASCADE_VEL_MIN: float = 0.1
    RZ_CASCADE_HIGH_LOOKBACK: int = 20
    RZ_CASCADE_REQUIRE_NEW_HIGH: bool = False
    RZ_CASCADE_MIN_TF_ALIGN: int = 1
    RZ_CASCADE_EXIT_ANY_TF: bool = True
    RZ_CASCADE_EXIT_MIN_REV_TFS: int = 2
    RZ_CASCADE_USE_W_M: bool = False
    LTF: str = '3m'  # used by RZ cascade — match crypto LTF
    HIER_RZ_TOP_BB: float = 0.85
    HIER_RZ_BOT_BB: float = 0.15
    HIER_DC_BAND_PCT: float = 0.2  # DC extreme = within X% of prev high/low
    HIER_WT_DELTA_MIN: float = 0.0
    HIER_WT_VEL_MIN: float = 0.0
    HIER_USE_W_M: bool = False
    MODE: str = 'crypto'  # consumed by wt_dc_hierarchy._resolve_tfs
    # HH/HL price-action entry (replaces WT 15m as primary trigger when toggled):
    USE_PRICE_ACTION_ENTRY: bool = False
    PRICE_ACTION_LB: int = 3

    def to_dict(self) -> Dict:
        return asdict(self)

    def copy(self) -> 'SymParams':
        return SymParams(**self.to_dict())


# ───────────────────────── vectorized indicator primitives ─────────────────

def _ema(x: np.ndarray, span: int) -> np.ndarray:
    if span <= 1:
        return x.astype(np.float64)
    a = 2.0 / (span + 1.0)
    return lfilter([a], [1.0, -(1.0 - a)], x.astype(np.float64))


def _rolling_mean_csum(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < w or w < 1:
        return out
    csum = np.concatenate([[0.0], np.cumsum(x.astype(np.float64))])
    out[w - 1:] = (csum[w:] - csum[:-w]) / w
    return out


def _rolling_std_csum(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < w or w < 1:
        return out
    xf = x.astype(np.float64)
    csum = np.concatenate([[0.0], np.cumsum(xf)])
    csum2 = np.concatenate([[0.0], np.cumsum(xf * xf)])
    mean = (csum[w:] - csum[:-w]) / w
    var = (csum2[w:] - csum2[:-w]) / w - mean * mean
    out[w - 1:] = np.sqrt(np.maximum(var, 0.0))
    return out


def _rolling_max(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < w or w < 1:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    out[w - 1:] = sliding_window_view(x.astype(np.float64), w).max(axis=1)
    return out


def _rolling_min(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < w or w < 1:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    out[w - 1:] = sliding_window_view(x.astype(np.float64), w).min(axis=1)
    return out


def compute_bb(close: np.ndarray, length: int, std: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    m = _rolling_mean_csum(close, length)
    s = _rolling_std_csum(close, length)
    upper = m + std * s
    lower = m - std * s
    rng = upper - lower
    rng_safe = np.where(rng > 1e-12, rng, 1e-12)
    pct_b = np.clip((close - lower) / rng_safe, -2.0, 3.0)
    return upper, lower, pct_b


def bb_auto_tune_mult(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                      length: int, lookback: int = 200, touch_pct: float = 0.002,
                      mult_min: float = 1.5, mult_max: float = 3.5) -> float:
    """Vectorized port of ez_indicators.bb_auto_tune. Sweeps σ multipliers, picks one that
    maximizes balanced upper+lower band touches over the last `lookback` bars.
    Returns best σ multiplier."""
    n = len(close)
    if n < length + lookback:
        return 2.0
    m = _rolling_mean_csum(close, length)
    s = _rolling_std_csum(close, length)
    h_w = high[-lookback:]
    l_w = low[-lookback:]
    m_w = m[-lookback:]
    s_w = s[-lookback:]
    valid = np.isfinite(m_w) & np.isfinite(s_w)
    best_mult = 2.0
    best_score = -1.0
    for mult_10 in range(int(mult_min * 10), int(mult_max * 10) + 1):
        mult = mult_10 / 10.0
        upper = m_w + mult * s_w
        lower = m_w - mult * s_w
        upper_touch = ((h_w >= upper * (1 - touch_pct)) & (h_w <= upper * (1 + touch_pct)) & valid).sum()
        lower_touch = ((l_w <= lower * (1 + touch_pct)) & (l_w >= lower * (1 - touch_pct)) & valid).sum()
        total = upper_touch + lower_touch
        if total == 0:
            continue
        balance = min(upper_touch, lower_touch) / max(upper_touch, lower_touch, 1)
        score = total * (0.5 + 0.5 * balance)
        if score > best_score:
            best_score = score
            best_mult = mult
    return best_mult


def compute_wt(high: np.ndarray, low: np.ndarray, close: np.ndarray, chan: int, avg: int) -> Tuple[np.ndarray, np.ndarray]:
    typical = (high + low + close) / 3.0
    esa = _ema(typical, chan)
    d = _ema(np.abs(typical - esa), chan)
    ci = (typical - esa) / (0.015 * np.maximum(d, 1e-10))
    wt1 = _ema(ci, avg)
    # WT2 = 3-bar SMA of WT1
    wt2 = _rolling_mean_csum(wt1, 3)
    wt2[:2] = wt1[:2]  # backfill startup
    return wt1, wt2


def compute_dc(high: np.ndarray, low: np.ndarray, period: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    dc_high = _rolling_max(high, period)
    dc_low = _rolling_min(low, period)
    dc_high_prev = np.empty_like(dc_high)
    dc_high_prev[0] = np.nan
    dc_high_prev[1:] = dc_high[:-1]
    dc_low_prev = np.empty_like(dc_low)
    dc_low_prev[0] = np.nan
    dc_low_prev[1:] = dc_low[:-1]
    return dc_high, dc_low, dc_high_prev, dc_low_prev


# ───────────────────────── data loader / resampler ─────────────────────────

NPZ_DIR = ROOT / 'backtest_v8' / 'indicators'

_npz_cache: Dict[str, Dict[str, np.ndarray]] = {}


def load_3m_base(sym: str, years_back: float = 4.0) -> Optional[Dict[str, np.ndarray]]:
    """Load FULL NPZ dict + 3m OHLC(+V) + ts. Sliced to last N years. Cached.
    Returns BOTH the engine-shape dict ('open','high','low','close','volume','ts') AND all original
    NPZ fields (close_15m, wt1_3m, dc_high_4h, etc.) so wt_dc_hierarchy can read them directly.
    """
    cache_key = f'{sym}__y{years_back:.2f}'
    if cache_key in _npz_cache:
        return _npz_cache[cache_key]
    # Cap cache at 1 entry per worker — full NPZ dict is ~5GB; prevents OOM with multiple syms.
    if _npz_cache:
        _npz_cache.clear()
    p = NPZ_DIR / f'{sym}.npz'
    if not p.exists():
        return None
    z = np.load(str(p))
    needed = ['open_3m', 'high_3m', 'low_3m', 'close_3m', 'timestamps']
    if not all(k in z.files for k in needed):
        z.close()
        return None
    full: Dict[str, np.ndarray] = {}
    for k in z.files:
        full[k] = z[k][:]
    z.close()
    ts_full = full['timestamps'].astype(np.int64)
    full['timestamps'] = ts_full
    cutoff = ts_full[-1] - int(years_back * 365.25 * 86400)
    si = int(np.searchsorted(ts_full, cutoff))
    sliced: Dict[str, np.ndarray] = {}
    for k, arr in full.items():
        if isinstance(arr, np.ndarray) and arr.ndim == 1 and len(arr) == len(ts_full):
            sliced[k] = arr[si:]
        else:
            sliced[k] = arr
    # Engine-shape aliases (back-compat for the rest of the engine code)
    sliced['open'] = sliced['open_3m'].astype(np.float64)
    sliced['high'] = sliced['high_3m'].astype(np.float64)
    sliced['low'] = sliced['low_3m'].astype(np.float64)
    sliced['close'] = sliced['close_3m'].astype(np.float64)
    sliced['volume'] = sliced.get('volume_3m', np.ones(len(sliced['close']))).astype(np.float64)
    sliced['ts'] = sliced['timestamps']
    _npz_cache[cache_key] = sliced
    return sliced


def resample_3m_to_htf(base: Dict[str, np.ndarray], tf_bars: int) -> Dict[str, np.ndarray]:
    """Resample 3m OHLC arrays to HTF (tf_bars=3m bars per HTF bar). Pure numpy."""
    n = len(base['close'])
    n_hf = n // tf_bars
    if n_hf == 0:
        return {'open': np.array([]), 'high': np.array([]), 'low': np.array([]), 'close': np.array([]), 'volume': np.array([]), 'ts': np.array([], dtype=np.int64)}
    cut = n_hf * tf_bars
    o = base['open'][:cut][::tf_bars]
    h = base['high'][:cut].reshape(n_hf, tf_bars).max(axis=1)
    l = base['low'][:cut].reshape(n_hf, tf_bars).min(axis=1)
    c = base['close'][:cut][tf_bars - 1::tf_bars][:n_hf]
    ts = base['ts'][:cut][tf_bars - 1::tf_bars][:n_hf]
    if 'volume' in base and len(base['volume']) >= cut:
        v = base['volume'][:cut].reshape(n_hf, tf_bars).sum(axis=1)
    else:
        v = np.ones(n_hf, dtype=np.float64)
    return {'open': o, 'high': h, 'low': l, 'close': c, 'volume': v, 'ts': ts}


def build_tf_data(base: Dict[str, np.ndarray]) -> Dict[str, Dict[str, np.ndarray]]:
    """Build {tf: ohlc-dict} for 15m, 1h, 4h, D, W. M skipped — too few samples on 4yr."""
    out: Dict[str, Dict[str, np.ndarray]] = {}
    for tf, ratio in TF_BARS_3M.items():
        if tf == 'M':
            continue
        d = resample_3m_to_htf(base, ratio)
        if len(d['close']) > 0:
            out[tf] = d
    if '15m' in out:
        d15 = out['15m']
        ratio15 = 5
        n_hf15 = len(base['close']) // ratio15
        cut15 = n_hf15 * ratio15
        for tf in ('3m', '15m', '1h', '4h', 'D'):
            for fld in (f'wt1_{tf}', f'wt2_{tf}', f'dc_high_{tf}', f'dc_low_{tf}', f'bb_upper_{tf}', f'bb_lower_{tf}'):
                if fld in base:
                    d15[fld] = base[fld][:cut15][ratio15 - 1::ratio15][:n_hf15]
            if f'bb_upper_{tf}' in d15 and f'bb_lower_{tf}' in d15:
                diff = d15[f'bb_upper_{tf}'] - d15[f'bb_lower_{tf}']
                diff_safe = np.where(diff > 1e-8, diff, 1e-8)
                d15[f'bb_pctb_{tf}'] = (d15['close'] - d15[f'bb_lower_{tf}']) / diff_safe
    return out


# ───────────────────────── per-TF directional signal ───────────────────────
def per_tf_signals(ohlc: Dict[str, np.ndarray], side: str, tf: str, params: SymParams) -> Tuple[np.ndarray, np.ndarray]:
    """Return (entry_sig, exit_sig) boolean arrays for this TF and side."""
    o, h, l, c = ohlc['open'], ohlc['high'], ohlc['low'], ohlc['close']
    v = ohlc.get('volume', np.ones(len(c)))
    n = len(c)
    bb_len = int(getattr(params, f'BB_LEN_{tf}'))
    bb_std = float(getattr(params, f'BB_STD_{tf}'))
    wt_chan = int(getattr(params, f'WT_CHAN_{tf}'))
    wt_avg = int(getattr(params, f'WT_AVG_{tf}'))
    dc_per = int(getattr(params, f'DC_PERIOD_{tf}'))
    if f'wt1_{tf}' in ohlc:
        wt1, wt2 = ohlc[f'wt1_{tf}'], ohlc[f'wt2_{tf}']
        bb_u, bb_l, bb_pctb = ohlc[f'bb_upper_{tf}'], ohlc[f'bb_lower_{tf}'], ohlc[f'bb_pctb_{tf}']
        dc_h, dc_l = ohlc[f'dc_high_{tf}'], ohlc[f'dc_low_{tf}']
        dc_h_prev = np.concatenate([[dc_h[0]], dc_h[:-1]])
        dc_l_prev = np.concatenate([[dc_l[0]], dc_l[:-1]])
    else:
        if getattr(params, 'BB_AUTO_TUNE_ENABLED', False): bb_std = bb_auto_tune_mult(h, l, c, bb_len, lookback=int(getattr(params, 'BB_AUTO_TUNE_LOOKBACK', 200)), mult_min=float(getattr(params, 'BB_AUTO_TUNE_MIN_MULT', 1.5)), mult_max=float(getattr(params, 'BB_AUTO_TUNE_MAX_MULT', 3.5)))
        bb_u, bb_l, bb_pctb = compute_bb(c, bb_len, bb_std)
        wt1, wt2 = compute_wt(h, l, c, wt_chan, wt_avg)
        dc_h, dc_l, dc_h_prev, dc_l_prev = compute_dc(h, l, dc_per)

    # ── Internet-research strategy paths ──────────────────────────────────
    # 1) Liquidity sweep: low taps below rolling-N low then close reclaims (LONG); mirror SHORT.
    lb = int(params.ENTRY_LIQ_SWEEP_LOOKBACK)
    rmin_l = _rolling_min(l, lb)  # rolling min of LOW
    rmin_l_prev = np.concatenate([[rmin_l[0] if n else 0.0], rmin_l[:-1]])
    rmax_h = _rolling_max(h, lb)
    rmax_h_prev = np.concatenate([[rmax_h[0] if n else 0.0], rmax_h[:-1]])
    liq_sweep_long = (l < rmin_l_prev) & (c > rmin_l_prev) & np.isfinite(rmin_l_prev)
    liq_sweep_short = (h > rmax_h_prev) & (c < rmax_h_prev) & np.isfinite(rmax_h_prev)

    # 2) NR7: bar i has narrowest range of last NR_LOOKBACK; entry on i+1 break of NR bar's H/L.
    nr_lb = int(params.ENTRY_NR_LOOKBACK)
    bar_range = h - l
    range_min = _rolling_min(bar_range, nr_lb)
    is_nr = (bar_range == range_min) & np.isfinite(range_min)
    is_nr_prev = np.concatenate([[False], is_nr[:-1]])
    nr_high_prev = np.concatenate([[h[0] if n else 0.0], h[:-1]])
    nr_low_prev = np.concatenate([[l[0] if n else 0.0], l[:-1]])
    nr7_break_long = is_nr_prev & (c > nr_high_prev)
    nr7_break_short = is_nr_prev & (c < nr_low_prev)

    # 3) Volume spike: vol > rolling_mean(vol, vlb) × ratio AND directional bar
    vlb = int(params.ENTRY_VOL_SPIKE_LOOKBACK)
    vol_mean = _rolling_mean_csum(v, vlb)
    vol_ratio = float(params.ENTRY_VOL_SPIKE_RATIO)
    vol_spike = (v > vol_mean * vol_ratio) & np.isfinite(vol_mean)
    bull_bar = c > o
    bear_bar = c < o
    vol_spike_long = vol_spike & bull_bar
    vol_spike_short = vol_spike & bear_bar

    # 4) EMA ribbon stack: ema_fast > ema_mid > ema_slow (LONG stack); pullback = c crosses ema_mid up.
    ema_fast = _ema(c, int(params.ENTRY_EMA_FAST))
    ema_mid  = _ema(c, int(params.ENTRY_EMA_MID))
    ema_slow = _ema(c, int(params.ENTRY_EMA_SLOW))
    stack_up = (ema_fast > ema_mid) & (ema_mid > ema_slow)
    stack_dn = (ema_fast < ema_mid) & (ema_mid < ema_slow)
    c_prev = np.concatenate([[c[0] if n else 0.0], c[:-1]])
    ema_mid_prev = np.concatenate([[ema_mid[0] if n else 0.0], ema_mid[:-1]])
    pullback_long = stack_up & (c > ema_mid) & (c_prev <= ema_mid_prev)
    pullback_short = stack_dn & (c < ema_mid) & (c_prev >= ema_mid_prev)

    # 5) Williams %R: -100 × (HH - close) / (HH - LL); reclaim from extreme = entry
    wlb = int(params.ENTRY_WILLR_LOOKBACK)
    wr_hh = _rolling_max(h, wlb)
    wr_ll = _rolling_min(l, wlb)
    wr_rng = wr_hh - wr_ll
    wr_rng_safe = np.where(wr_rng > 1e-12, wr_rng, 1e-12)
    willr = -100.0 * (wr_hh - c) / wr_rng_safe
    willr_prev = np.concatenate([[willr[0] if n else 0.0], willr[:-1]])
    os_th = float(params.ENTRY_WILLR_OS_THRESHOLD)
    ob_th = float(params.ENTRY_WILLR_OB_THRESHOLD)
    willr_reclaim_long  = (willr > os_th) & (willr_prev <= os_th)
    willr_reclaim_short = (willr < ob_th) & (willr_prev >= ob_th)

    # 6) Fair Value Gap: bullish FVG = bar[i-1].high < bar[i+1].low at bar i (3-bar imbalance);
    # entry on later bar when close returns to fill the gap (touches the gap zone).
    # We mark the gap on the middle bar then check for fill within next K bars.
    if n >= 3:
        h_prev2 = np.concatenate([[h[0], h[0]], h[:-2]])  # bar[i-1].high lined up with bar[i]+1 effectively bar i (gap formed by bars i-1,i,i+1)
        l_next2 = np.concatenate([l[2:], [l[-1], l[-1]]])  # bar[i+1].low aligned with bar i
        bull_fvg = (h_prev2 < l_next2) & np.isfinite(h_prev2) & np.isfinite(l_next2)
        h_next2 = np.concatenate([h[2:], [h[-1], h[-1]]])
        l_prev2 = np.concatenate([[l[0], l[0]], l[:-2]])
        bear_fvg = (l_prev2 > h_next2) & np.isfinite(l_prev2) & np.isfinite(h_next2)
        # Fill detection: next 1 bar after FVG touches the gap zone
        fvg_long_signal = np.concatenate([[False, False, False], bull_fvg[:-3]])  # entry 3 bars after gap formed
        fvg_short_signal = np.concatenate([[False, False, False], bear_fvg[:-3]])
        fvg_long_signal = fvg_long_signal[:n]
        fvg_short_signal = fvg_short_signal[:n]
    else:
        fvg_long_signal = np.zeros(n, dtype=bool)
        fvg_short_signal = np.zeros(n, dtype=bool)

    # WT cross EVENT detection: True at bars where (wt1>wt2) just transitioned from (wt1<=wt2) in last N bars
    wt_bull_now = wt1 > wt2
    wt_bull_event = wt_bull_now & ~np.concatenate([[False], wt_bull_now[:-1]])  # transition this bar
    wt_bear_event = (~wt_bull_now) & np.concatenate([[False], wt_bull_now[:-1]])
    if params.ENTRY_WT_CROSS_LOOKBACK > 1:
        # Smear the event forward by lookback bars (any bar within window is "just crossed")
        lb = int(params.ENTRY_WT_CROSS_LOOKBACK)
        kernel = np.ones(lb, dtype=bool)
        # Use cumulative AND-windowed: event is True if any of last lb bars had a transition
        wt_bull_event_w = np.zeros(n, dtype=bool)
        wt_bear_event_w = np.zeros(n, dtype=bool)
        cs_bull = np.concatenate([[0], np.cumsum(wt_bull_event.astype(np.int32))])
        cs_bear = np.concatenate([[0], np.cumsum(wt_bear_event.astype(np.int32))])
        wt_bull_event_w[lb - 1:] = (cs_bull[lb:] - cs_bull[:-lb]) > 0
        wt_bear_event_w[lb - 1:] = (cs_bear[lb:] - cs_bear[:-lb]) > 0
        wt_bull_event = wt_bull_event_w
        wt_bear_event = wt_bear_event_w

    # BB squeeze: compute BB width = upper - lower; release = current width > rolling_min(width, lookback) * ratio
    bb_width = bb_u - bb_l
    bb_width_min = _rolling_min(bb_width, int(params.ENTRY_BB_SQUEEZE_LOOKBACK))
    bb_squeeze_release = (bb_width > bb_width_min * params.ENTRY_BB_SQUEEZE_RATIO) & (bb_width_min > 0)

    # BB extreme bounce: bb_pctb just crossed UP through ENTRY_BB_EXTREME_THRESHOLD (LONG) or DOWN through (1-thresh) (SHORT)
    pctb_prev = np.concatenate([[bb_pctb[0]], bb_pctb[:-1]])
    bb_extreme_long = (bb_pctb > params.ENTRY_BB_EXTREME_THRESHOLD) & (pctb_prev <= params.ENTRY_BB_EXTREME_THRESHOLD)
    bb_extreme_short = (bb_pctb < (1 - params.ENTRY_BB_EXTREME_THRESHOLD)) & (pctb_prev >= (1 - params.ENTRY_BB_EXTREME_THRESHOLD))

    Z = np.zeros(n, dtype=bool)
    # HH/HL price-action gate (user 2026-05-05): replace WT 15m requirement with HH (LONG) / LL (SHORT).
    pa_lb = max(2, int(params.PRICE_ACTION_LB))
    h_prev = np.concatenate([[h[0]], h[:-1]])
    l_prev = np.concatenate([[l[0]], l[:-1]])
    hh = h > h_prev
    ll = l < l_prev
    # Cumulative N consecutive HH/LL — vectorized
    csum_hh = np.concatenate([[0], np.cumsum(hh.astype(np.int32))])
    csum_ll = np.concatenate([[0], np.cumsum(ll.astype(np.int32))])
    n_hh_in_lb = csum_hh[pa_lb:] - csum_hh[:-pa_lb]
    n_ll_in_lb = csum_ll[pa_lb:] - csum_ll[:-pa_lb]
    pa_long = np.zeros(n, dtype=bool)
    pa_short = np.zeros(n, dtype=bool)
    pa_long[pa_lb - 1:] = n_hh_in_lb >= pa_lb
    pa_short[pa_lb - 1:] = n_ll_in_lb >= pa_lb

    if side == 'LONG':
        if params.USE_PRICE_ACTION_ENTRY:
            wt_ok = pa_long  # HH-based gate replaces WT-cross
        else:
            wt_ok = (wt1 > wt2) if params.USE_WT_CROSS else np.ones(n, dtype=bool)
        dc_break = (c > dc_h_prev) & (dc_h_prev > 0)
        pullback = (bb_pctb < params.BB_LONG_ENTRY_MAX)
        if params.ENTRY_MODE == 'dc_break':
            base_path = dc_break
        elif params.ENTRY_MODE == 'pullback':
            base_path = pullback
        else:
            base_path = dc_break | pullback
        wt_event_p = wt_bull_event if params.ENTRY_WT_CROSS_EVENT_ENABLED else Z
        bb_bounce_p = bb_extreme_long if params.ENTRY_BB_EXTREME_BOUNCE_ENABLED else Z
        bb_squeeze_p = (bb_squeeze_release & wt_ok) if params.ENTRY_BB_SQUEEZE_RELEASE_ENABLED else Z
        liq_p = liq_sweep_long if params.ENTRY_LIQ_SWEEP_ENABLED else Z
        nr7_p = nr7_break_long if params.ENTRY_NR7_ENABLED else Z
        vol_p = vol_spike_long if params.ENTRY_VOL_SPIKE_ENABLED else Z
        ema_p = pullback_long if params.ENTRY_EMA_RIBBON_ENABLED else Z
        wr_p  = willr_reclaim_long if params.ENTRY_WILLR_ENABLED else Z
        fvg_p = fvg_long_signal if params.ENTRY_FVG_ENABLED else Z
        any_path = (base_path | wt_event_p | bb_bounce_p | bb_squeeze_p |
                    liq_p | nr7_p | vol_p | ema_p | wr_p | fvg_p)
        ceiling_ok = (bb_pctb < params.BB_TOP_THRESHOLD) if params.USE_BB_FILTER else np.ones(n, dtype=bool)
        entry = wt_ok & any_path & ceiling_ok
        wt_bear_strong = (wt1 < wt2) & (wt1 < np.concatenate([[wt1[0]], wt1[:-1]])) if params.EXIT_WT_ACCEL_ONLY else (wt1 < wt2)
        bb_extreme = bb_pctb > params.BB_TOP_THRESHOLD
        breakdown = (c < dc_l_prev) & (dc_l_prev > 0)
        if params.EXIT_REQUIRE_BOTH:
            exit_ = (wt_bear_strong & bb_extreme) | breakdown
        else:
            exit_ = wt_bear_strong | bb_extreme | breakdown
    else:  # SHORT
        if params.USE_PRICE_ACTION_ENTRY:
            wt_ok = pa_short
        else:
            wt_ok = (wt1 < wt2) if params.USE_WT_CROSS else np.ones(n, dtype=bool)
        dc_break = (c < dc_l_prev) & (dc_l_prev > 0)
        pullback = (bb_pctb > params.BB_SHORT_ENTRY_MIN)
        if params.ENTRY_MODE == 'dc_break':
            base_path = dc_break
        elif params.ENTRY_MODE == 'pullback':
            base_path = pullback
        else:
            base_path = dc_break | pullback
        wt_event_p = wt_bear_event if params.ENTRY_WT_CROSS_EVENT_ENABLED else Z
        bb_bounce_p = bb_extreme_short if params.ENTRY_BB_EXTREME_BOUNCE_ENABLED else Z
        bb_squeeze_p = (bb_squeeze_release & wt_ok) if params.ENTRY_BB_SQUEEZE_RELEASE_ENABLED else Z
        liq_p = liq_sweep_short if params.ENTRY_LIQ_SWEEP_ENABLED else Z
        nr7_p = nr7_break_short if params.ENTRY_NR7_ENABLED else Z
        vol_p = vol_spike_short if params.ENTRY_VOL_SPIKE_ENABLED else Z
        ema_p = pullback_short if params.ENTRY_EMA_RIBBON_ENABLED else Z
        wr_p  = willr_reclaim_short if params.ENTRY_WILLR_ENABLED else Z
        fvg_p = fvg_short_signal if params.ENTRY_FVG_ENABLED else Z
        any_path = (base_path | wt_event_p | bb_bounce_p | bb_squeeze_p |
                    liq_p | nr7_p | vol_p | ema_p | wr_p | fvg_p)
        ceiling_ok = (bb_pctb > params.BB_BOT_THRESHOLD) if params.USE_BB_FILTER else np.ones(n, dtype=bool)
        entry = wt_ok & any_path & ceiling_ok
        wt_bull_strong = (wt1 > wt2) & (wt1 > np.concatenate([[wt1[0]], wt1[:-1]])) if params.EXIT_WT_ACCEL_ONLY else (wt1 > wt2)
        bb_extreme = bb_pctb < params.BB_BOT_THRESHOLD
        breakup = (c > dc_h_prev) & (dc_h_prev > 0)
        if params.EXIT_REQUIRE_BOTH:
            exit_ = (wt_bull_strong & bb_extreme) | breakup
        else:
            exit_ = wt_bull_strong | bb_extreme | breakup
    # NaN safety: treat NaN-context bars as no-signal
    valid = ~(np.isnan(bb_pctb) | np.isnan(wt1) | np.isnan(dc_h_prev))
    return entry & valid, exit_ & valid


def htf_trend_pass(ohlc_d: Dict[str, np.ndarray], ohlc_w: Optional[Dict[str, np.ndarray]],
                   side: str, params: SymParams, n_15m: int) -> np.ndarray:
    """Build a 15m-grid boolean array: True when D (and optionally W) trend confirms side.
    D trend = close > rolling_mean(close, 20). W trend = same on weekly.
    Returns array of length n_15m.
    """
    out = np.ones(n_15m, dtype=bool)
    if not (params.REQUIRE_D_TREND or params.REQUIRE_W_TREND):
        return out

    def _trend_arr(d: Dict[str, np.ndarray], side: str, ratio_to_15m: int) -> np.ndarray:
        c = d['close']
        m = _rolling_mean_csum(c, 20)
        if side == 'LONG':
            up = c > m
        else:
            up = c < m
        # repeat to 15m grid
        rep = np.repeat(up, ratio_to_15m)
        if len(rep) < n_15m:
            rep = np.concatenate([rep, np.zeros(n_15m - len(rep), dtype=bool)])
        return rep[:n_15m]

    if params.REQUIRE_D_TREND:
        out &= _trend_arr(ohlc_d, side, ratio_to_15m=TF_BARS_3M['D'] // TF_BARS_3M['15m'])  # 96
    if params.REQUIRE_W_TREND and ohlc_w is not None and len(ohlc_w['close']) >= 10:
        out &= _trend_arr(ohlc_w, side, ratio_to_15m=TF_BARS_3M['W'] // TF_BARS_3M['15m'])  # 672
    return out


# ───────────────────────── trade walker (vectorized state) ────────────────

def walk_trades(enter_15m: np.ndarray, leave_15m: np.ndarray, c15: np.ndarray, ts15: np.ndarray,
                side: str, min_hold: int, cooldown: int) -> List[Dict]:
    """Walk the 15m grid with one position at a time. Returns trade list.
    O(num_trades) using np.flatnonzero scans.
    """
    trades: List[Dict] = []
    n = len(c15)
    i = 0
    rt_comm = COMMISSION_RT_PCT
    while i < n:
        e_remaining = enter_15m[i:]
        e_idxs = np.flatnonzero(e_remaining)
        if not len(e_idxs):
            break
        entry_i = i + int(e_idxs[0])
        x_start = entry_i + min_hold
        if x_start >= n:
            break
        x_remaining = leave_15m[x_start:]
        x_idxs = np.flatnonzero(x_remaining)
        if len(x_idxs):
            exit_i = x_start + int(x_idxs[0])
        else:
            exit_i = n - 1
        ep = float(c15[entry_i])
        xp = float(c15[exit_i])
        if ep <= 0 or xp <= 0:
            i = exit_i + cooldown + 1
            continue
        if side == 'LONG':
            pnl_gross = (xp - ep) / ep * 100.0
        else:
            pnl_gross = (ep - xp) / ep * 100.0
        pnl_net = pnl_gross - rt_comm
        trades.append({
            'side': side,
            'entry_idx': entry_i, 'exit_idx': exit_i,
            'entry_ts': int(ts15[entry_i]), 'exit_ts': int(ts15[exit_i]),
            'entry_price': ep, 'exit_price': xp,
            'pnl_gross_pct': pnl_gross, 'pnl_pct': pnl_net,
            'bars_held': exit_i - entry_i,
        })
        i = exit_i + cooldown + 1
    return trades


def walk_trades_dual(enter_long: np.ndarray, leave_long: np.ndarray,
                     enter_short: np.ndarray, leave_short: np.ndarray,
                     c15: np.ndarray, ts15: np.ndarray,
                     min_hold: int, cooldown: int,
                     wt1_15m: Optional[np.ndarray] = None,
                     wt2_15m: Optional[np.ndarray] = None,
                     wt1_3m_at_15m: Optional[np.ndarray] = None,
                     wt2_3m_at_15m: Optional[np.ndarray] = None,
                     reverse_on_exit: bool = False,
                     follow_through: bool = False,
                     ft_min_move_pct: float = 0.05,
                     ft_window_bars: int = 5,
                     augment_enabled: bool = False,
                     augment_levels_pct: Tuple[float, ...] = (1.0, 2.0, 3.0, 4.0),
                     mean_rev_enabled: bool = False,
                     mean_rev_tol_pct: float = 0.30,
                     mean_rev_window: int = 10,
                     hedge_enabled: bool = True,
                     hedge_size_frac: float = 0.5,
                     noloss_enabled: bool = True,
                     peak_protect_enabled: bool = True,
                     peak_protect_require_gain: bool = True,
                     hard_loss_enabled: bool = True,
                     hard_loss_pct: float = 0.5,
                     peak_giveback_fixed_enabled: bool = False,
                     peak_giveback_fixed_drop_pct: float = 0.5,
                     peak_giveback_fixed_min_peak_pct: float = 0.10,
                     ppl_v2_enabled: bool = False,
                     ppl_v2_step1_gain_pct: float = 0.5,
                     ppl_v2_arm_gain_pct: float = 0.75,
                     ppl_v2_be_buffer_pct: float = 0.10,
                     ppl_v2_frac: float = 0.5,
                     x7_dc_freeze_15m: Optional[np.ndarray] = None,
                     x7_bb_freeze_15m: Optional[np.ndarray] = None,
                     x7_abs_floor_pct: float = -8.0,
                     hedge_htf_ok_long: Optional[np.ndarray] = None,
                     hedge_htf_ok_short: Optional[np.ndarray] = None) -> List[Dict]:
    """Unified walker over BOTH sides. Allows REVERSE_ON_EXIT and FOLLOW_THROUGH_REENTRY.
    Single position at a time (no augment yet); flips between LONG/SHORT on exit if reverse path fires.
    Returns combined trade list.
    """
    trades: List[Dict] = []
    n = len(c15)
    rt_comm = COMMISSION_RT_PCT
    i = 0
    while i < n:
        # Find next entry of either side
        long_remaining = enter_long[i:]
        short_remaining = enter_short[i:]
        long_idxs = np.flatnonzero(long_remaining)
        short_idxs = np.flatnonzero(short_remaining)
        if not len(long_idxs) and not len(short_idxs):
            break
        next_long = i + int(long_idxs[0]) if len(long_idxs) else n + 1
        next_short = i + int(short_idxs[0]) if len(short_idxs) else n + 1
        if next_long <= next_short:
            entry_i = next_long
            side = 'LONG'
        else:
            entry_i = next_short
            side = 'SHORT'
        # Find exit for this side
        x_start = entry_i + min_hold
        if x_start >= n:
            break
        if side == 'LONG':
            x_idxs = np.flatnonzero(leave_long[x_start:])
        else:
            x_idxs = np.flatnonzero(leave_short[x_start:])
        if len(x_idxs):
            exit_i = x_start + int(x_idxs[0])
        else:
            exit_i = n - 1
        ep = float(c15[entry_i])
        xp = float(c15[exit_i])
        if ep <= 0 or xp <= 0:
            i = exit_i + cooldown + 1
            continue
        # HARD_LOSS_PCT exit (the source of v8 BTC_DEDICATED's 87% WR per audit):
        # If position drops to -HARD_LOSS_PCT, force-exit. Caps individual loss tightly so wins>>losses by count.
        forced_exit_origin = ''
        if hard_loss_enabled and exit_i > entry_i + min_hold:
            traj_h = c15[entry_i:exit_i + 1]
            if side == 'LONG':
                gain_h = (traj_h - ep) / ep * 100.0
            else:
                gain_h = (ep - traj_h) / ep * 100.0
            hl_hit = gain_h <= -hard_loss_pct
            hl_hit[:min_hold] = False
            hl_idxs = np.flatnonzero(hl_hit)
            if len(hl_idxs):
                hl_exit_i = entry_i + int(hl_idxs[0])
                if hl_exit_i < exit_i:
                    exit_i = hl_exit_i
                    xp = float(c15[exit_i])
                    forced_exit_origin = 'hard_loss_pct'
        # X7 FROZEN-DC + abs-floor stop (Phase 2A patch 2 — cfg_v7_final zero-loser source).
        # Stop at frozen dc_low at entry OR abs-floor pct, whichever fires first.
        if not forced_exit_origin and x7_dc_freeze_15m is not None and exit_i > entry_i + min_hold:
            traj_x7 = c15[entry_i:exit_i + 1]
            # frozen dc level at the entry bar (the user spec: dc_low at entry, locked)
            frozen_dc = float(x7_dc_freeze_15m[entry_i]) if entry_i < len(x7_dc_freeze_15m) else 0.0
            frozen_bb = float(x7_bb_freeze_15m[entry_i]) if (x7_bb_freeze_15m is not None and entry_i < len(x7_bb_freeze_15m)) else 0.0
            if side == 'LONG':
                hit_dc = (frozen_dc > 0) & (traj_x7 < frozen_dc) if frozen_dc > 0 else np.zeros_like(traj_x7, dtype=bool)
                hit_bb = (frozen_bb > 0) & (traj_x7 < frozen_bb) if frozen_bb > 0 else np.zeros_like(traj_x7, dtype=bool)
                gain_x7 = (traj_x7 - ep) / ep * 100.0
            else:
                hit_dc = (frozen_dc > 0) & (traj_x7 > frozen_dc) if frozen_dc > 0 else np.zeros_like(traj_x7, dtype=bool)
                hit_bb = (frozen_bb > 0) & (traj_x7 > frozen_bb) if frozen_bb > 0 else np.zeros_like(traj_x7, dtype=bool)
                gain_x7 = (ep - traj_x7) / ep * 100.0
            abs_floor_hit = gain_x7 <= float(x7_abs_floor_pct)
            x7_hit = hit_dc | hit_bb | abs_floor_hit
            if isinstance(x7_hit, np.ndarray) and x7_hit.size > 0:
                x7_hit[:min_hold] = False
                x7_idxs = np.flatnonzero(x7_hit)
                if len(x7_idxs):
                    x7_exit_i = entry_i + int(x7_idxs[0])
                    if x7_exit_i < exit_i:
                        exit_i = x7_exit_i
                        xp = float(c15[exit_i])
                        forced_exit_origin = 'x7_frozen_dc'
        # FIXED-% PEAK GIVEBACK (v8 QUICK_PEAK_GIVEBACK style — re-enabled per user 2026-05-05 if needed):
        if peak_giveback_fixed_enabled and not forced_exit_origin and exit_i > entry_i + min_hold:
            traj_pg = c15[entry_i:exit_i + 1]
            if side == 'LONG':
                gain_pg = (traj_pg - ep) / ep * 100.0
            else:
                gain_pg = (ep - traj_pg) / ep * 100.0
            running_peak = np.maximum.accumulate(gain_pg)
            armed = running_peak >= peak_giveback_fixed_min_peak_pct
            drop_from_peak = running_peak - gain_pg
            give_hit = armed & (drop_from_peak >= peak_giveback_fixed_drop_pct)
            give_hit[:min_hold] = False
            give_idxs = np.flatnonzero(give_hit)
            if len(give_idxs):
                pg_exit_i = entry_i + int(give_idxs[0])
                if pg_exit_i < exit_i:
                    exit_i = pg_exit_i
                    xp = float(c15[exit_i])
                    forced_exit_origin = 'peak_giveback_fixed'
        # SIGNAL-DRIVEN PEAK PROTECT: after gain has been positive, exit when wt1_15m flips against side.
        if peak_protect_enabled and wt1_15m is not None and wt2_15m is not None and exit_i > entry_i + min_hold:
            traj = c15[entry_i:exit_i + 1]
            if side == 'LONG':
                gain_traj = (traj - ep) / ep * 100.0
                wt_against = wt1_15m[entry_i:exit_i + 1] < wt2_15m[entry_i:exit_i + 1]
            else:
                gain_traj = (ep - traj) / ep * 100.0
                wt_against = wt1_15m[entry_i:exit_i + 1] > wt2_15m[entry_i:exit_i + 1]
            had_gain = np.maximum.accumulate(gain_traj) > 0 if peak_protect_require_gain else np.ones_like(gain_traj, dtype=bool)
            pp_hit = wt_against & had_gain
            pp_hit[:min_hold] = False
            pp_idxs = np.flatnonzero(pp_hit)
            if len(pp_idxs):
                pp_exit_i = entry_i + int(pp_idxs[0])
                if pp_exit_i < exit_i:
                    exit_i = pp_exit_i
                    xp = float(c15[exit_i])
                    forced_exit_origin = 'peak_protect_wt15m'
        # User 2026-05-05 rule: "close at a loss when technicals go against OR hedge on wt1_3m flip".
        # Technical exit (multi-TF leave) IS the legitimate cut-loss. Hedge runs in parallel below.
        # No NOLOSS recovery-walk — that would override the technical exit and bleed the position.
        # NOLOSS_ENABLED kept as a config flag but it does NOT force-hold past technical exits.
        pnl_gross = (xp - ep) / ep * 100.0 if side == 'LONG' else (ep - xp) / ep * 100.0
        # PARTIAL_PROFIT_LOCK_v2 (Phase 2A patch 1): step-1 partial @ ppl_v2_step1_gain_pct, arm @ arm_gain_pct,
        # final stop = first_exit_price if armed else ep × (1 ± BE_BUFFER_PCT/100). Blended pnl.
        ppl_origin_tag = ''
        if ppl_v2_enabled and exit_i > entry_i + min_hold:
            traj_ppl = c15[entry_i:exit_i + 1]
            if side == 'LONG':
                gain_traj_ppl = (traj_ppl - ep) / ep * 100.0
            else:
                gain_traj_ppl = (ep - traj_ppl) / ep * 100.0
            gain_traj_ppl[:min_hold] = -1e9
            step1_idxs = np.flatnonzero(gain_traj_ppl >= float(ppl_v2_step1_gain_pct))
            if len(step1_idxs):
                step1_local = int(step1_idxs[0])
                step1_idx = entry_i + step1_local
                step1_px = float(c15[step1_idx])
                # Initial stop = breakeven buffer
                be_buf = float(ppl_v2_be_buffer_pct) / 100.0
                stop_lvl = ep * (1.0 + be_buf) if side == 'LONG' else ep * (1.0 - be_buf)
                # Check for arm (upgrade stop to step1_px)
                arm_idxs = np.flatnonzero(gain_traj_ppl >= float(ppl_v2_arm_gain_pct))
                if len(arm_idxs):
                    arm_local = int(arm_idxs[0])
                    if arm_local > step1_local:
                        stop_lvl = step1_px
                # Find stop-hit bar (after step1)
                tail = traj_ppl[step1_local + 1:]
                if side == 'LONG':
                    stop_hit = (tail <= stop_lvl)
                else:
                    stop_hit = (tail >= stop_lvl)
                stop_hit_idxs = np.flatnonzero(stop_hit)
                if len(stop_hit_idxs):
                    stop_local = step1_local + 1 + int(stop_hit_idxs[0])
                    stop_px = float(c15[entry_i + stop_local])
                    # Blended: FRAC at step1_px, (1-FRAC) at stop_px
                    frac = max(0.0, min(1.0, float(ppl_v2_frac)))
                    if side == 'LONG':
                        leg1 = (step1_px - ep) / ep * 100.0
                        leg2 = (stop_px - ep) / ep * 100.0
                    else:
                        leg1 = (ep - step1_px) / ep * 100.0
                        leg2 = (ep - stop_px) / ep * 100.0
                    pnl_gross = frac * leg1 + (1.0 - frac) * leg2
                    exit_i = entry_i + stop_local
                    xp = stop_px
                    if not forced_exit_origin:
                        ppl_origin_tag = 'ppl_v2'
        trades.append({
            'side': side, 'entry_idx': entry_i, 'exit_idx': exit_i,
            'entry_ts': int(ts15[entry_i]), 'exit_ts': int(ts15[exit_i]),
            'entry_price': ep, 'exit_price': xp,
            'pnl_gross_pct': pnl_gross, 'pnl_pct': pnl_gross - rt_comm,
            'bars_held': exit_i - entry_i,
            'origin': forced_exit_origin or ppl_origin_tag or 'primary',
        })
        # AUGMENT 1-2-3-4: pyramid into winner at +1/2/3/4% gain levels (each = own trade).
        # Compute pnl trajectory from entry_i to exit_i; mark first crossing of each level.
        if augment_enabled and exit_i > entry_i:
            window_close = c15[entry_i:exit_i + 1]
            if side == 'LONG':
                gain_traj = (window_close - ep) / ep * 100.0
            else:
                gain_traj = (ep - window_close) / ep * 100.0
            for lvl_pct in augment_levels_pct:
                hit = np.flatnonzero(gain_traj >= lvl_pct)
                if not len(hit):
                    break  # no higher level can hit if this one didn't
                aug_idx = entry_i + int(hit[0])
                aug_ep = float(c15[aug_idx])
                if aug_ep <= 0 or aug_idx >= exit_i:
                    continue
                if side == 'LONG':
                    aug_pnl = (xp - aug_ep) / aug_ep * 100.0
                else:
                    aug_pnl = (aug_ep - xp) / aug_ep * 100.0
                trades.append({
                    'side': side, 'entry_idx': aug_idx, 'exit_idx': exit_i,
                    'entry_ts': int(ts15[aug_idx]), 'exit_ts': int(ts15[exit_i]),
                    'entry_price': aug_ep, 'exit_price': xp,
                    'pnl_gross_pct': aug_pnl, 'pnl_pct': aug_pnl - rt_comm,
                    'bars_held': exit_i - aug_idx, 'origin': f'augment_L{int(lvl_pct)}',
                })
        # SIGNAL-DRIVEN HEDGE CYCLES (user 2026-05-05): hedge LONG when wt1_3m < wt2_3m.
        # Cycle: open hedge on adverse flip, close hedge on flip back.
        # Multiple hedge legs over the position's life — each is a tracked trade.
        if hedge_enabled and wt1_3m_at_15m is not None and wt2_3m_at_15m is not None and exit_i > entry_i:
            wt3m_window_1 = wt1_3m_at_15m[entry_i:exit_i + 1]
            wt3m_window_2 = wt2_3m_at_15m[entry_i:exit_i + 1]
            traj_h = c15[entry_i:exit_i + 1]
            if side == 'LONG':
                pnl_traj = (traj_h - ep) / ep * 100.0
                hedge_state = (wt3m_window_1 < wt3m_window_2) & (pnl_traj < 0.0)
            else:
                pnl_traj = (ep - traj_h) / ep * 100.0
                hedge_state = (wt3m_window_1 > wt3m_window_2) & (pnl_traj < 0.0)
            hedge_state[:min_hold] = False
            # User 2026-05-05: hedge fires on EVERY wt-against-position event during primary's lifetime.
            # ONE concurrent hedge: open on flip-against, close on flip-back, can repeat through life of primary.
            prev_state = np.concatenate([[False], hedge_state[:-1]])
            opens = hedge_state & ~prev_state
            # HEDGE_HTF_VETO (Phase 2A patch 4): block hedge opens unless HTF (Daily wt) supports them.
            # hedge_htf_ok_long: True at bars where Daily wt supports SHORT-side hedges (i.e. when primary is LONG and HTF flipped bearish).
            # hedge_htf_ok_short: mirror — supports LONG-side hedges when primary is SHORT.
            if side == 'LONG' and hedge_htf_ok_long is not None and len(hedge_htf_ok_long) >= exit_i + 1:
                htf_window = hedge_htf_ok_long[entry_i:exit_i + 1]
                opens = opens & htf_window
            elif side == 'SHORT' and hedge_htf_ok_short is not None and len(hedge_htf_ok_short) >= exit_i + 1:
                htf_window = hedge_htf_ok_short[entry_i:exit_i + 1]
                opens = opens & htf_window
            open_idxs = np.flatnonzero(opens)
            for open_local in open_idxs:
                h_idx = entry_i + int(open_local)
                close_search = hedge_state[int(open_local) + 1:]
                close_rel = np.flatnonzero(~close_search)
                if len(close_rel):
                    h_close_idx = h_idx + 1 + int(close_rel[0])
                else:
                    h_close_idx = exit_i
                h_close_idx = min(h_close_idx, exit_i)
                if h_close_idx <= h_idx:
                    continue
                h_ep = float(c15[h_idx]); h_xp = float(c15[h_close_idx])
                if h_ep <= 0 or h_xp <= 0:
                    continue
                h_side = 'SHORT' if side == 'LONG' else 'LONG'
                h_pnl = (h_xp - h_ep) / h_ep * 100.0 if h_side == 'LONG' else (h_ep - h_xp) / h_ep * 100.0
                h_pnl_net = (h_pnl - rt_comm) * hedge_size_frac
                trades.append({
                    'side': h_side, 'entry_idx': h_idx, 'exit_idx': h_close_idx,
                    'entry_ts': int(ts15[h_idx]), 'exit_ts': int(ts15[h_close_idx]),
                    'entry_price': h_ep, 'exit_price': h_xp,
                    'pnl_gross_pct': h_pnl, 'pnl_pct': h_pnl_net,
                    'bars_held': h_close_idx - h_idx, 'origin': 'hedge_wt',
                })
            h_idx_local = -1
            origin = ''
            if False:
                h_idx = entry_i + h_idx_local
                h_ep = float(c15[h_idx])
                h_xp = float(c15[exit_i])
                if h_ep > 0 and h_xp > 0 and h_idx < exit_i:
                    h_side = 'SHORT' if side == 'LONG' else 'LONG'
                    h_pnl = (h_xp - h_ep) / h_ep * 100.0 if h_side == 'LONG' else (h_ep - h_xp) / h_ep * 100.0
                    h_pnl_net = (h_pnl - rt_comm) * hedge_size_frac
                    trades.append({
                        'side': h_side, 'entry_idx': h_idx, 'exit_idx': exit_i,
                        'entry_ts': int(ts15[h_idx]), 'exit_ts': int(ts15[exit_i]),
                        'entry_price': h_ep, 'exit_price': h_xp,
                        'pnl_gross_pct': h_pnl, 'pnl_pct': h_pnl_net,
                        'bars_held': exit_i - h_idx, 'origin': origin,
                    })
        # MEAN-REV REENTRY: after exit at xp, watch next K bars for price returning to ±tol% of xp; reenter same side.
        if mean_rev_enabled and exit_i + mean_rev_window < n:
            we = min(exit_i + 1 + mean_rev_window, n)
            window_close = c15[exit_i + 1:we]
            tol = mean_rev_tol_pct / 100.0
            within_tol = np.abs(window_close - xp) / max(xp, 1e-12) <= tol
            hit_idx = np.flatnonzero(within_tol)
            if len(hit_idx):
                mr_entry_i = exit_i + 1 + int(hit_idx[0])
                mr_x_start = mr_entry_i + min_hold
                if mr_x_start < n:
                    mr_leave = leave_long if side == 'LONG' else leave_short
                    mr_x_idxs = np.flatnonzero(mr_leave[mr_x_start:])
                    mr_exit_i = mr_x_start + int(mr_x_idxs[0]) if len(mr_x_idxs) else n - 1
                    mr_ep = float(c15[mr_entry_i]); mr_xp = float(c15[mr_exit_i])
                    if mr_ep > 0 and mr_xp > 0:
                        mr_pnl = (mr_xp - mr_ep) / mr_ep * 100.0 if side == 'LONG' else (mr_ep - mr_xp) / mr_ep * 100.0
                        trades.append({
                            'side': side, 'entry_idx': mr_entry_i, 'exit_idx': mr_exit_i,
                            'entry_ts': int(ts15[mr_entry_i]), 'exit_ts': int(ts15[mr_exit_i]),
                            'entry_price': mr_ep, 'exit_price': mr_xp,
                            'pnl_gross_pct': mr_pnl, 'pnl_pct': mr_pnl - rt_comm,
                            'bars_held': mr_exit_i - mr_entry_i, 'origin': 'mean_rev_reentry',
                        })
        # REVERSE_ON_EXIT: at the exit bar, if opposite side's entry signal fires → enter opposite immediately
        next_i = exit_i + cooldown + 1
        if reverse_on_exit and exit_i + 1 < n:
            opp_side = 'SHORT' if side == 'LONG' else 'LONG'
            opp_enter = enter_short if opp_side == 'SHORT' else enter_long
            opp_leave = leave_short if opp_side == 'SHORT' else leave_long
            # check window: opposite signal at exit_i or exit_i+1
            check_idx = None
            if opp_enter[exit_i]:
                check_idx = exit_i
            elif exit_i + 1 < n and opp_enter[exit_i + 1]:
                check_idx = exit_i + 1
            if check_idx is not None:
                opp_entry_i = check_idx
                opp_x_start = opp_entry_i + min_hold
                if opp_x_start < n:
                    opp_x_idxs = np.flatnonzero(opp_leave[opp_x_start:])
                    opp_exit_i = opp_x_start + int(opp_x_idxs[0]) if len(opp_x_idxs) else n - 1
                    opp_ep = float(c15[opp_entry_i]); opp_xp = float(c15[opp_exit_i])
                    if opp_ep > 0 and opp_xp > 0:
                        opp_pnl = (opp_xp - opp_ep) / opp_ep * 100.0 if opp_side == 'LONG' else (opp_ep - opp_xp) / opp_ep * 100.0
                        trades.append({
                            'side': opp_side, 'entry_idx': opp_entry_i, 'exit_idx': opp_exit_i,
                            'entry_ts': int(ts15[opp_entry_i]), 'exit_ts': int(ts15[opp_exit_i]),
                            'entry_price': opp_ep, 'exit_price': opp_xp,
                            'pnl_gross_pct': opp_pnl, 'pnl_pct': opp_pnl - rt_comm,
                            'bars_held': opp_exit_i - opp_entry_i, 'origin': 'reverse_on_exit',
                        })
                        next_i = opp_exit_i + cooldown + 1
        # FOLLOW_THROUGH_REENTRY: after exit, watch next K bars for favorable move; if hit, re-enter same side
        if follow_through and exit_i + ft_window_bars < n:
            window_end = min(exit_i + 1 + ft_window_bars, n)
            window_close = c15[exit_i + 1:window_end]
            if side == 'LONG':
                fav = (window_close - xp) / xp * 100.0  # positive = favorable
            else:
                fav = (xp - window_close) / xp * 100.0
            hit_idx = np.flatnonzero(fav >= ft_min_move_pct * 100.0)
            if len(hit_idx):
                ft_entry_i = exit_i + 1 + int(hit_idx[0])
                ft_x_start = ft_entry_i + min_hold
                if ft_x_start < n:
                    ft_leave = leave_long if side == 'LONG' else leave_short
                    ft_x_idxs = np.flatnonzero(ft_leave[ft_x_start:])
                    ft_exit_i = ft_x_start + int(ft_x_idxs[0]) if len(ft_x_idxs) else n - 1
                    ft_ep = float(c15[ft_entry_i]); ft_xp = float(c15[ft_exit_i])
                    if ft_ep > 0 and ft_xp > 0 and ft_entry_i >= next_i:
                        ft_pnl = (ft_xp - ft_ep) / ft_ep * 100.0 if side == 'LONG' else (ft_ep - ft_xp) / ft_ep * 100.0
                        trades.append({
                            'side': side, 'entry_idx': ft_entry_i, 'exit_idx': ft_exit_i,
                            'entry_ts': int(ts15[ft_entry_i]), 'exit_ts': int(ts15[ft_exit_i]),
                            'entry_price': ft_ep, 'exit_price': ft_xp,
                            'pnl_gross_pct': ft_pnl, 'pnl_pct': ft_pnl - rt_comm,
                            'bars_held': ft_exit_i - ft_entry_i, 'origin': 'follow_through',
                        })
                        next_i = max(next_i, ft_exit_i + cooldown + 1)
        i = next_i
    # Sort trades chronologically (origins may interleave)
    trades.sort(key=lambda t: t['entry_idx'])
    return trades


# ───────────────────────── top-level simulate ──────────────────────────────

def _build_signals(tf_data: Dict, side: str, params: SymParams) -> Tuple[np.ndarray, np.ndarray]:
    """Build (enter_15m, leave_15m) for a side on the 15m grid."""
    n_15m = len(tf_data['15m']['close'])
    e_count = np.zeros(n_15m, dtype=np.int16)
    x_count = np.zeros(n_15m, dtype=np.int16)
    for tf in DECISION_TFS:
        e_sig, x_sig = per_tf_signals(tf_data['15m'], side, tf, params)
        e_count += e_sig.astype(np.int16)
        x_count += x_sig.astype(np.int16)
    min_tfs_entry = max(MIN_TFS_AGREE_FLOOR, int(getattr(params, 'MIN_TFS_AGREE_ENTRY', params.MIN_TFS_AGREE)))
    min_tfs_exit = max(MIN_TFS_AGREE_FLOOR, int(getattr(params, 'MIN_TFS_AGREE_EXIT', params.MIN_TFS_AGREE)))
    enter = e_count >= min_tfs_entry
    leave = x_count >= min_tfs_exit
    return enter, leave


def simulate_dual(sym: str, params: SymParams, years_back: float = 4.0,
                  only_side: Optional[str] = None) -> Optional[Dict]:
    """Run BOTH LONG and SHORT through unified walker. Supports REVERSE_ON_EXIT + FOLLOW_THROUGH.
    Returns symbol-level metrics + per-side breakdown.
    `only_side='LONG'/'SHORT'` disables the other side at signal level (true per-side isolation).
    """
    base = load_3m_base(sym, years_back=years_back)
    # Min bars proportional to window: ~500/day at 3m. 7-day window = 3360 OK; require ≥1000 minimum.
    min_bars = max(1000, int(min(years_back, 0.05) * 365.25 * 480 * 0.7))
    if base is None or len(base['close']) < min_bars:
        return None
    tf_data = build_tf_data(base)
    # Min HTF bars proportional. 7-day window has D=7, 4h=42, 1h=168, 15m=672. Be lenient.
    if years_back < 0.1:
        # short-window: just need D≥5 and 4h≥20 (otherwise no signal)
        min_per_tf = {'15m': 100, '1h': 50, '4h': 20, 'D': 5}
    else:
        min_per_tf = {tf: 50 for tf in list(DECISION_TFS) + ['D']}
    for tf in list(DECISION_TFS) + ['D']:
        if tf not in tf_data or len(tf_data[tf]['close']) < min_per_tf.get(tf, 50):
            return None
    # User 2026-05-05 mandate: NOLOSS without HEDGE = account-killer. Force-couple here.
    if params.NOLOSS_ENABLED and not params.HEDGE_ENABLED:
        params = params.copy()
        params.NOLOSS_ENABLED = False
        params.__dict__['_noloss_auto_disabled'] = 'NOLOSS requires HEDGE — auto-disabled'
    # WT_DC HIERARCHY (vectorized cascade state machine — primary entry/exit when enabled).
    # Use 'tradier' MODE for crypto so hierarchy skips 3m (no close_5m for crypto → auto-skipped to 15m as LTF).
    # This aligns hierarchy signals with my 15m walker grid.
    # RZ_CASCADE — v8_quick_engine vectorized port. Adds LTF-breakout-with-HTF-alignment entries.
    if getattr(params, 'USE_RZ_CASCADE', False):
        global _rz_cascade_signals
        if _rz_cascade_signals is None:
            _rz_cascade_signals = _import_rz_cascade()
        n_3m = len(base['close_3m']) if 'close_3m' in base else len(base['close'])
        # Use LTF=15m for crypto in RZ cascade (matches walker grid, avoids 3m noise per hierarchy fix)
        rz_cfg = params.copy()
        rz_cfg.LTF = '15m'
        rz_long_3m, _rz_long_exit = _rz_cascade_signals(base, n_3m, True, rz_cfg)
        rz_short_3m, _rz_short_exit = _rz_cascade_signals(base, n_3m, False, rz_cfg)
        # Subsample 3m → 15m grid
        n_15m = len(tf_data['15m']['close'])
        def _ss_rz(arr_3m: np.ndarray) -> np.ndarray:
            cut = (len(arr_3m) // 5) * 5
            sub = arr_3m[:cut][4::5]
            if len(sub) >= n_15m: return sub[:n_15m]
            return np.concatenate([np.zeros(n_15m - len(sub), dtype=bool), sub])
        rz_long_15m = _ss_rz(rz_long_3m)
        rz_short_15m = _ss_rz(rz_short_3m)
    else:
        rz_long_15m = None
        rz_short_15m = None

    # USE_V8_AGGREGATORS — pull entry/exit from v8_quick_engine which already has ALL paths integrated.
    # This is the single biggest jump in entry quality — bringing in ALL of BTC_BREAKOUT, ACCEL_RAMP,
    # DELTA, SATOSHIT, K_ZONE, MFI, VWAP, STDEV, RZ_BREAKOUT, ATR_SIZING, WINNER_PROTECT, etc.
    if getattr(params, 'USE_V8_AGGREGATORS', False):
        _ensure_v8_loaded()
        n_3m = len(base['close_3m']) if 'close_3m' in base else len(base['close'])
        # Build a QuickConfig and overlay our params
        qcfg = _v8_QuickConfig()
        qcfg.MODE = 'crypto'
        qcfg.LTF = '3m'
        # 1. Apply baseline override (proven config from sweep — e.g. override_btc_BEST.json keys)
        baseline_ovr = getattr(params, 'BASELINE_OVERRIDES', {}) or {}
        for k, v in baseline_ovr.items():
            if k.startswith('_'): continue
            try: setattr(qcfg, k, v)
            except Exception: pass
        # If BTC_DEDICATED_ENABLED is True (override_btc_BEST sets this), per-sym scope it.
        # Mirrors flz8 profile pattern: cfg.BTC_DEDICATED_SYMBOLS = (sym,) so each sym uses
        # its own BTC-dedicated config in isolation, not the global tuple.
        if getattr(qcfg, 'BTC_DEDICATED_ENABLED', False):
            qcfg.BTC_DEDICATED_SYMBOLS = (sym,)
        # 2. Then overlay SymParams (per-sym sweep variations on top of baseline)
        for pk, pv in params.to_dict().items():
            if pk == 'BASELINE_OVERRIDES': continue
            if hasattr(qcfg, pk):
                try: setattr(qcfg, pk, pv)
                except Exception: pass
        try:
            v8_enter_long = _v8_compute_entry(base, n_3m, True, qcfg, sym=sym)
            v8_enter_short = _v8_compute_entry(base, n_3m, False, qcfg, sym=sym)
            v8_leave_long = _v8_compute_exit(base, n_3m, True, qcfg)
            v8_leave_short = _v8_compute_exit(base, n_3m, False, qcfg)
        except Exception as e:
            print(f"[per_sym_engine] v8 aggregators error: {e}", flush=True)
            v8_enter_long = v8_enter_short = v8_leave_long = v8_leave_short = None
        # Subsample 3m → 15m (every 5th index)
        n_15m = len(tf_data['15m']['close'])
        def _ss_v8(arr_3m: np.ndarray) -> np.ndarray:
            cut = (len(arr_3m) // 5) * 5
            sub = arr_3m[:cut][4::5]
            if len(sub) >= n_15m: return sub[:n_15m]
            return np.concatenate([np.zeros(n_15m - len(sub), dtype=bool), sub])
        if v8_enter_long is not None:
            enter_long = _ss_v8(v8_enter_long)
            enter_short = _ss_v8(v8_enter_short)
            leave_long = _ss_v8(v8_leave_long)
            leave_short = _ss_v8(v8_leave_short)
        else:
            enter_long, leave_long = _build_signals(tf_data, 'LONG', params)
            enter_short, leave_short = _build_signals(tf_data, 'SHORT', params)
    elif getattr(params, 'USE_WT_DC_HIERARCHY', False):
        n_3m = len(base['close_3m']) if 'close_3m' in base else len(base['close'])
        # Trick: clone params with MODE='tradier' so _resolve_tfs returns ('5m','15m','1h','4h','D');
        # crypto NPZ has no close_5m → hierarchy auto-skips 5m, effectively LTF=15m.
        hier_cfg = params.copy()
        hier_cfg.MODE = 'tradier'
        hier_long = compute_hierarchy_full(base, n_3m, True, hier_cfg)
        hier_short = compute_hierarchy_full(base, n_3m, False, hier_cfg)
        # Subsample 3m → 15m (every 5th index is the 15m bar close)
        def _ss(arr_3m: np.ndarray) -> np.ndarray:
            cut = (len(arr_3m) // 5) * 5
            sub = arr_3m[:cut][4::5]
            n_target = len(tf_data['15m']['close'])
            if len(sub) >= n_target:
                return sub[:n_target]
            return np.concatenate([np.zeros(n_target - len(sub), dtype=bool), sub])
        if hier_long.get('tfs'):
            enter_long = _ss(hier_long['entry'])
            leave_long = _ss(hier_long['exit'])
        else:
            enter_long, leave_long = _build_signals(tf_data, 'LONG', params)
        if hier_short.get('tfs'):
            enter_short = _ss(hier_short['entry'])
            leave_short = _ss(hier_short['exit'])
        else:
            enter_short, leave_short = _build_signals(tf_data, 'SHORT', params)
    else:
        enter_long, leave_long = _build_signals(tf_data, 'LONG', params)
        enter_short, leave_short = _build_signals(tf_data, 'SHORT', params)
    # OR in RZ_CASCADE entry signals if enabled (additive entry path)
    if rz_long_15m is not None:
        if len(rz_long_15m) == len(enter_long):
            enter_long = enter_long | rz_long_15m
    if rz_short_15m is not None:
        if len(rz_short_15m) == len(enter_short):
            enter_short = enter_short | rz_short_15m
    if only_side == 'LONG':
        enter_short = np.zeros_like(enter_short)
    elif only_side == 'SHORT':
        enter_long = np.zeros_like(enter_long)
    # ─── USER 2026-05-06 mandate: backtest must mirror live safety guards ───
    # Compute bar-level forced-exit masks for: ALL_TF_AGAINST, WT15M_AGAINST, DC_BB_D_BREAK_REVERSE.
    # OR'd into the leave masks so the walker treats them as exit signals (with origin tagged).
    n_15m_safety = len(tf_data['15m']['close'])
    def _ss_3m_to_15m(arr_3m):
        """Subsample 3m npz array to 15m grid by taking every 5th bar."""
        if arr_3m is None or len(arr_3m) == 0:
            return np.zeros(n_15m_safety, dtype=bool)
        cut = (len(arr_3m) // 5) * 5
        sub = arr_3m[:cut][4::5]
        if len(sub) >= n_15m_safety: return sub[:n_15m_safety]
        return np.concatenate([np.zeros(n_15m_safety - len(sub), dtype=bool), sub])
    # All TF against (LONG: every TF wt1<wt2; SHORT mirror)
    if getattr(params, 'BT_ALL_TF_AGAINST_CLOSE_ENABLED', True):
        tf_npz_keys = [('wt1_3m','wt2_3m'),('wt1_15m','wt2_15m'),('wt1_1h','wt2_1h'),('wt1_4h','wt2_4h'),('wt1_D','wt2_D')]
        n_3m_loc = len(base.get('close_3m', base['close']))
        all_against_long_3m = np.ones(n_3m_loc, dtype=bool)
        all_against_short_3m = np.ones(n_3m_loc, dtype=bool)
        for w1k, w2k in tf_npz_keys:
            w1 = base.get(w1k); w2 = base.get(w2k)
            if w1 is None or w2 is None or len(w1) != n_3m_loc:
                all_against_long_3m = np.zeros(n_3m_loc, dtype=bool); break
            all_against_long_3m &= (w1 < w2)
            all_against_short_3m &= (w1 > w2)
        all_against_long_15m = _ss_3m_to_15m(all_against_long_3m)
        all_against_short_15m = _ss_3m_to_15m(all_against_short_3m)
        leave_long = leave_long | all_against_long_15m
        leave_short = leave_short | all_against_short_15m
    # WT15M against (looser than ALL_TF — just 15m)
    if getattr(params, 'BT_WT15M_AGAINST_FORCE_HEDGE_ENABLED', True):
        w1_15m = base.get('wt1_15m'); w2_15m = base.get('wt2_15m')
        if w1_15m is not None and w2_15m is not None and len(w1_15m) == len(base.get('close_3m', base['close'])):
            wt15_against_long_3m = w1_15m < w2_15m
            wt15_against_short_3m = w1_15m > w2_15m
            leave_long = leave_long | _ss_3m_to_15m(wt15_against_long_3m)
            leave_short = leave_short | _ss_3m_to_15m(wt15_against_short_3m)
    # DC_BB_D_BREAK_REVERSE — close wrong-side on D-band break
    if getattr(params, 'BT_DC_BB_D_BREAK_REVERSE_ENABLED', True):
        close_3m = base.get('close_3m', base['close'])
        n_3m_loc = len(close_3m)
        dc_hi_d = base.get('dc_high_D')
        dc_lo_d = base.get('dc_low_D')
        bb_up_d = base.get('bb_upper_D')
        bb_lo_d = base.get('bb_lower_D')
        d_break_up_3m = np.zeros(n_3m_loc, dtype=bool)
        d_break_dn_3m = np.zeros(n_3m_loc, dtype=bool)
        if dc_hi_d is not None and len(dc_hi_d) == n_3m_loc:
            prev = np.roll(dc_hi_d, 1); prev[0] = dc_hi_d[0]
            d_break_up_3m |= (close_3m > prev) & (prev > 0)
        if dc_lo_d is not None and len(dc_lo_d) == n_3m_loc:
            prev = np.roll(dc_lo_d, 1); prev[0] = dc_lo_d[0]
            d_break_dn_3m |= (close_3m < prev) & (prev > 0)
        if bb_up_d is not None and len(bb_up_d) == n_3m_loc:
            prev = np.roll(bb_up_d, 1); prev[0] = bb_up_d[0]
            d_break_up_3m |= (close_3m > prev) & (prev > 0)
        if bb_lo_d is not None and len(bb_lo_d) == n_3m_loc:
            prev = np.roll(bb_lo_d, 1); prev[0] = bb_lo_d[0]
            d_break_dn_3m |= (close_3m < prev) & (prev > 0)
        # Break-UP kills SHORT positions, break-DOWN kills LONG
        leave_short = leave_short | _ss_3m_to_15m(d_break_up_3m)
        leave_long = leave_long | _ss_3m_to_15m(d_break_dn_3m)
    h15 = tf_data['15m']['high']
    l15 = tf_data['15m']['low']
    c15_close = tf_data['15m']['close']
    if 'wt1_15m' in tf_data['15m']:
        wt1_15m_arr = tf_data['15m']['wt1_15m']
        wt2_15m_arr = tf_data['15m']['wt2_15m']
    else:
        wt1_15m_arr, wt2_15m_arr = compute_wt(h15, l15, c15_close, int(params.WT_CHAN_15m), int(params.WT_AVG_15m))
    n_15m = len(c15_close)
    hedge_tf = getattr(params, 'HEDGE_WT_TF', '3m')
    if f'wt1_{hedge_tf}' in tf_data['15m']:
        wt1_at_15m = tf_data['15m'][f'wt1_{hedge_tf}']
        wt2_at_15m = tf_data['15m'][f'wt2_{hedge_tf}']
    else:
        if hedge_tf == '3m':
            h_src, l_src, c_src = base['high'], base['low'], base['close']
            wt1_full, wt2_full = compute_wt(h_src, l_src, c_src, int(params.WT_CHAN_15m), int(params.WT_AVG_15m))
            ratio = 5
            cut = (len(c_src) // ratio) * ratio
            wt1_at_15m = wt1_full[:cut][ratio - 1::ratio][:n_15m]
            wt2_at_15m = wt2_full[:cut][ratio - 1::ratio][:n_15m]
        elif hedge_tf in ('15m', '1h', '4h', 'D'):
            tfd = tf_data[hedge_tf]
            wt1_tf, wt2_tf = compute_wt(tfd['high'], tfd['low'], tfd['close'], int(getattr(params, f'WT_CHAN_{hedge_tf}')), int(getattr(params, f'WT_AVG_{hedge_tf}')))
            repeat_ratio = TF_BARS_3M[hedge_tf] // TF_BARS_3M['15m']
            if repeat_ratio == 1:
                wt1_at_15m = wt1_tf[:n_15m]
                wt2_at_15m = wt2_tf[:n_15m]
            else:
                wt1_at_15m = np.repeat(wt1_tf, repeat_ratio)[:n_15m]
                wt2_at_15m = np.repeat(wt2_tf, repeat_ratio)[:n_15m]
        else:
            wt1_at_15m = wt1_15m_arr.copy()
            wt2_at_15m = wt2_15m_arr.copy()
    if len(wt1_at_15m) < n_15m:
        pad = n_15m - len(wt1_at_15m)
        wt1_at_15m = np.concatenate([np.zeros(pad), wt1_at_15m])
        wt2_at_15m = np.concatenate([np.zeros(pad), wt2_at_15m])
    wt1_3m_at_15m = wt1_at_15m
    wt2_3m_at_15m = wt2_at_15m
    htf_long = htf_trend_pass(tf_data['D'], tf_data.get('W'), 'LONG', params, len(enter_long))
    htf_short = htf_trend_pass(tf_data['D'], tf_data.get('W'), 'SHORT', params, len(enter_short))
    enter_long = enter_long & htf_long
    enter_short = enter_short & htf_short
    # Booster gates from NPZ-precomputed fields (subsample 3m→15m: every 5th index aligned with 15m bar end).
    n_15m = len(enter_long)
    def _resample_3m_to_15m(arr_3m: np.ndarray) -> np.ndarray:
        if arr_3m is None or len(arr_3m) == 0:
            return np.zeros(n_15m, dtype=np.float64)
        # tf_data['15m']['ts'][i] corresponds to (i+1)*5-1 in the 3m index (the 5th 3m bar of the 15m window).
        # base['ts'] was sliced to last_N_years; tf_data resamples to the same year range.
        # Simplest: take last n_15m × 5 of arr_3m; resample by taking close-of-bin (every 5th).
        cut = (len(arr_3m) // 5) * 5
        out = arr_3m[:cut][4::5]
        if len(out) >= n_15m:
            return out[:n_15m]
        # Pad short
        return np.concatenate([np.zeros(n_15m - len(out), dtype=np.float64), out])

    fund_3m = base.get('funding_rate_3m')
    if params.FUNDING_GATE_ENABLED and fund_3m is not None:
        f15 = _resample_3m_to_15m(fund_3m)
        long_fund_ok = f15 <= params.FUNDING_GATE_LONG_MAX
        short_fund_ok = f15 >= params.FUNDING_GATE_SHORT_MIN
        enter_long = enter_long & long_fund_ok
        enter_short = enter_short & short_fund_ok

    oi_chg_3m = base.get('oi_change_1h_3m')
    if params.OI_GATE_ENABLED and oi_chg_3m is not None:
        oi15 = _resample_3m_to_15m(oi_chg_3m)
        # LONG: skip when OI dropping hard (signals long unwind/distribution)
        # SHORT: skip when OI dropping hard the other way (short squeeze setup)
        long_oi_ok = oi15 >= params.OI_GATE_OI_CHANGE_MIN
        short_oi_ok = oi15 >= params.OI_GATE_OI_CHANGE_MIN
        enter_long = enter_long & long_oi_ok
        enter_short = enter_short & short_oi_ok

    accel_field = f'wt_acceleration_{params.WT_ACCEL_GATE_TF}'
    wt_acc = base.get(accel_field)
    if params.WT_ACCEL_GATE_ENABLED and wt_acc is not None:
        a15 = _resample_3m_to_15m(wt_acc)
        long_acc_ok = a15 > 0  # wt accelerating up
        short_acc_ok = a15 < 0
        enter_long = enter_long & long_acc_ok
        enter_short = enter_short & short_acc_ok

    # Divergence block: bearish div = price HH but WT LH over LB bars → block LONG entry
    if params.DIVERGENCE_BLOCK_ENABLED:
        c15 = tf_data['15m']['close']
        h15 = tf_data['15m']['high']
        l15 = tf_data['15m']['low']
        wt1_15m, _ = compute_wt(h15, l15, c15,
                                 int(params.WT_CHAN_15m), int(params.WT_AVG_15m))
        lb = int(params.DIVERGENCE_LB)
        # Rolling max/min of price and WT over LB bars
        price_max_lb = _rolling_max(c15, lb)
        price_min_lb = _rolling_min(c15, lb)
        wt_max_lb = _rolling_max(wt1_15m, lb)
        wt_min_lb = _rolling_min(wt1_15m, lb)
        # Bearish div: current bar at price max but WT NOT at max → bearish
        bearish_div = (c15 >= price_max_lb) & (wt1_15m < wt_max_lb)
        bullish_div = (c15 <= price_min_lb) & (wt1_15m > wt_min_lb)
        # Block LONG when bearish div present (price topping but momentum weakening)
        enter_long = enter_long & ~bearish_div
        # Block SHORT when bullish div (price bottoming but momentum strengthening)
        enter_short = enter_short & ~bullish_div
    c15 = tf_data['15m']['close']
    ts15 = tf_data['15m']['ts']
    # ─── Phase 2A patch 3: REQUIRE_ABOVE_SMA50_D (LT-direction filter) ───
    if getattr(params, 'REQUIRE_ABOVE_SMA50_D', False):
        sma50_3m = base.get('sma_50_D')
        if sma50_3m is not None:
            sma50_15m = _resample_3m_to_15m(sma50_3m)
            if len(sma50_15m) >= len(c15):
                above50 = c15 > sma50_15m[:len(c15)]
                enter_long = enter_long & above50
                enter_short = enter_short & ~above50
    # ─── Phase 2A patch 4: HTF_TREND_VETO (block entries against Daily WT) ───
    if getattr(params, 'HTF_TREND_VETO_ENABLED', False):
        wt1_d_3m = base.get('wt1_D')
        wt2_d_3m = base.get('wt2_D')
        if wt1_d_3m is not None and wt2_d_3m is not None:
            wt1_d_15m = _resample_3m_to_15m(wt1_d_3m)
            wt2_d_15m = _resample_3m_to_15m(wt2_d_3m)
            n_min = min(len(wt1_d_15m), len(wt2_d_15m), len(enter_long))
            if n_min > 0:
                bull_d = wt1_d_15m[:n_min] > wt2_d_15m[:n_min]
                # pad to enter_long length if shorter
                if n_min < len(enter_long):
                    pad = len(enter_long) - n_min
                    bull_d = np.concatenate([np.zeros(pad, dtype=bool), bull_d])
                enter_long = enter_long & bull_d
                enter_short = enter_short & ~bull_d
    # ─── Phase 2A patch 4: HEDGE_HTF_VETO precompute (Daily wt supports hedge?) ───
    hedge_htf_ok_long = None
    hedge_htf_ok_short = None
    if getattr(params, 'HEDGE_HTF_VETO_ENABLED', False):
        wt1_d_3m = base.get('wt1_D')
        wt2_d_3m = base.get('wt2_D')
        if wt1_d_3m is not None and wt2_d_3m is not None:
            wt1_d_15m_h = _resample_3m_to_15m(wt1_d_3m)
            wt2_d_15m_h = _resample_3m_to_15m(wt2_d_3m)
            n_min_h = min(len(wt1_d_15m_h), len(wt2_d_15m_h), len(c15))
            if n_min_h > 0:
                bull_d_h = wt1_d_15m_h[:n_min_h] > wt2_d_15m_h[:n_min_h]
                # hedge-of-LONG = SHORT, needs HTF bearish (wt1_D < wt2_D)
                hedge_htf_ok_long = ~bull_d_h
                # hedge-of-SHORT = LONG, needs HTF bullish
                hedge_htf_ok_short = bull_d_h
                if n_min_h < len(c15):
                    pad = len(c15) - n_min_h
                    hedge_htf_ok_long = np.concatenate([np.zeros(pad, dtype=bool), hedge_htf_ok_long])
                    hedge_htf_ok_short = np.concatenate([np.zeros(pad, dtype=bool), hedge_htf_ok_short])
    # ─── Phase 2A patch 2: X7 frozen-DC precompute (subsample to 15m grid) ───
    x7_dc_freeze_15m = None
    x7_bb_freeze_15m = None
    if getattr(params, 'X7_FROZEN_DC_STOP_ENABLED', False):
        dc_field = f'dc_low_{params.X7_FREEZE_DC_TF}' if 'LONG' or True else None  # both sides use dc_low for stop; SHORT uses dc_high
        # NOTE: for SHORT, the natural stop is dc_high. But our walker uses one freeze array per call.
        # Solution: walker reads frozen_dc and decides side semantics there. We just supply both.
        dc_lo_3m = base.get(f'dc_low_{params.X7_FREEZE_DC_TF}')
        dc_hi_3m = base.get(f'dc_high_{params.X7_FREEZE_DC_TF}')
        # Walker uses single freeze: for crypto we use dc_low for LONG; SHORT uses dc_high. But walker takes one array.
        # Compromise: build a side-aware freeze inside walker; here we pass dc_low (for LONG) and let walker mirror via abs_floor only for SHORT,
        # OR set freeze=None for SHORT. Simpler: just pass dc_lo; abs_floor still applies symmetrically.
        if dc_lo_3m is not None:
            x7_dc_freeze_15m = _resample_3m_to_15m(dc_lo_3m)
            if len(x7_dc_freeze_15m) < len(c15):
                pad = len(c15) - len(x7_dc_freeze_15m)
                x7_dc_freeze_15m = np.concatenate([np.zeros(pad), x7_dc_freeze_15m])
        bb_tf = getattr(params, 'X7_FREEZE_BB_TF', '') or ''
        if bb_tf:
            bb_lo_3m = base.get(f'bb_lower_{bb_tf}')
            if bb_lo_3m is not None:
                x7_bb_freeze_15m = _resample_3m_to_15m(bb_lo_3m)
                if len(x7_bb_freeze_15m) < len(c15):
                    pad = len(c15) - len(x7_bb_freeze_15m)
                    x7_bb_freeze_15m = np.concatenate([np.zeros(pad), x7_bb_freeze_15m])
    trades = walk_trades_dual(
        enter_long, leave_long, enter_short, leave_short, c15, ts15,
        int(params.MIN_HOLD_BARS_15m), int(params.COOLDOWN_BARS_15m),
        wt1_15m=wt1_15m_arr, wt2_15m=wt2_15m_arr,
        wt1_3m_at_15m=wt1_3m_at_15m, wt2_3m_at_15m=wt2_3m_at_15m,
        reverse_on_exit=params.REVERSE_ON_EXIT_ENABLED,
        follow_through=params.FOLLOW_THROUGH_REENTRY_ENABLED,
        ft_min_move_pct=float(params.FOLLOW_THROUGH_MIN_MOVE_PCT),
        ft_window_bars=int(params.FOLLOW_THROUGH_WINDOW_BARS),
        augment_enabled=params.AUGMENT_ENABLED,
        augment_levels_pct=tuple(params.AUGMENT_LEVELS_PCT),
        mean_rev_enabled=params.REENTRY_MEAN_REV_ENABLED,
        mean_rev_tol_pct=float(params.REENTRY_MEAN_REV_TOLERANCE_PCT),
        mean_rev_window=int(params.REENTRY_MEAN_REV_WINDOW_BARS),
        hedge_enabled=params.HEDGE_ENABLED,
        hedge_size_frac=float(params.HEDGE_SIZE_FRAC),
        noloss_enabled=params.NOLOSS_ENABLED,
        peak_protect_enabled=params.PEAK_PROTECT_ENABLED,
        peak_protect_require_gain=params.PEAK_PROTECT_REQUIRE_GAIN,
        hard_loss_enabled=params.HARD_LOSS_PCT_ENABLED,
        hard_loss_pct=float(params.HARD_LOSS_PCT),
        peak_giveback_fixed_enabled=params.PEAK_GIVEBACK_FIXED_PCT_ENABLED,
        peak_giveback_fixed_drop_pct=float(params.PEAK_GIVEBACK_FIXED_DROP_PCT),
        peak_giveback_fixed_min_peak_pct=float(params.PEAK_GIVEBACK_FIXED_MIN_PEAK_PCT),
        ppl_v2_enabled=getattr(params, 'PARTIAL_PROFIT_LOCK_ENABLED', False),
        ppl_v2_step1_gain_pct=float(getattr(params, 'PARTIAL_PROFIT_LOCK_GAIN_PCT', 0.5)),
        ppl_v2_arm_gain_pct=float(getattr(params, 'PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT', 0.75)),
        ppl_v2_be_buffer_pct=float(getattr(params, 'PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT', 0.10)),
        ppl_v2_frac=float(getattr(params, 'PARTIAL_PROFIT_LOCK_FRAC', 0.5)),
        x7_dc_freeze_15m=x7_dc_freeze_15m,
        x7_bb_freeze_15m=x7_bb_freeze_15m,
        x7_abs_floor_pct=float(getattr(params, 'X7_ABS_FLOOR_PCT', -8.0)),
        hedge_htf_ok_long=hedge_htf_ok_long,
        hedge_htf_ok_short=hedge_htf_ok_short,
    )
    span_days = max(1.0, (ts15[-1] - ts15[0]) / 86400.0)
    yrs = max(0.01, span_days / 365.25)
    # Buy-and-hold baseline over the same window (short-horizon agents need this).
    bh_pct = float((c15[-1] / c15[0] - 1.0) * 100.0) if c15[0] > 0 else 0.0
    if not trades:
        empty = {'sym': sym, 'trades': 0, 'trades_per_day': 0.0, 'pool_sharpe': 0.0,
                 'sym_sharpe': 0.0, 'wr_pct': 0.0, 'max_dd_pct': 0.0,
                 'total_gain_pct': 0.0, 'avg_gain_trade': 0.0, 'gain_per_yr': 0.0,
                 'gain_per_week': 0.0, 'bh_pct_window': bh_pct,
                 'gain_sym_yr': 0.0, 'years': yrs, 'n_syms': 1,
                 'tag': f'per_sym_dual_{sym}', 'trade_list': [],
                 'params': params.to_dict(), 'long_trades': 0, 'short_trades': 0}
        return empty
    # Mark-to-market: any trade where exit_idx == n-1 is a position that didn't have a real exit signal —
    # it was force-closed at last bar. Tag those for transparency. The pnl_pct already reflects MTM since
    # walker uses xp = c15[exit_i] = last bar close. User 2026-05-05: "always add gain/loss of open positions".
    n_15m_total = len(c15)
    open_at_end = [t for t in trades if t.get('exit_idx', 0) == n_15m_total - 1]
    for t in trades:
        t['mtm_at_end'] = (t.get('exit_idx', 0) == n_15m_total - 1)
    rets = np.array([t['pnl_pct'] for t in trades], dtype=np.float64)
    n = len(rets)
    sd = float(rets.std())
    pool = float(rets.mean() / sd) if sd > 1e-12 else 0.0
    wr = float((rets > 0).mean() * 100.0)
    eq = np.cumsum(rets); peak = np.maximum.accumulate(eq); dd = float((peak - eq).max())
    total = float(rets.sum())
    n_long = sum(1 for t in trades if t['side'] == 'LONG')
    n_short = n - n_long
    n_open_at_end = len(open_at_end)
    mtm_pnl_open = float(sum(t['pnl_pct'] for t in open_at_end))
    n_reverse = sum(1 for t in trades if t.get('origin') == 'reverse_on_exit')
    n_ft = sum(1 for t in trades if t.get('origin') == 'follow_through')
    n_aug = sum(1 for t in trades if (t.get('origin') or '').startswith('augment_'))
    n_hedge = sum(1 for t in trades if (t.get('origin') or '').startswith('hedge'))
    n_hedge_wt3m = sum(1 for t in trades if (t.get('origin') or '').startswith('hedge_wt'))
    n_peak_protect = sum(1 for t in trades if t.get('origin') == 'peak_protect_wt15m')
    n_mtm_end = sum(1 for t in trades if t.get('origin') == 'mtm_at_end')
    n_hard_loss = sum(1 for t in trades if t.get('origin') == 'hard_loss_pct')
    n_peak_giveback_fixed = sum(1 for t in trades if t.get('origin') == 'peak_giveback_fixed')
    n_mr = sum(1 for t in trades if t.get('origin') == 'mean_rev_reentry')
    weeks = max(1.0 / 7.0, span_days / 7.0)
    return {
        'sym': sym, 'trades': n, 'trades_per_day': n / span_days,
        'pool_sharpe': pool, 'sym_sharpe': max(-5.0, min(5.0, pool)),
        'wr_pct': wr, 'max_dd_pct': dd, 'total_gain_pct': total,
        'avg_gain_trade': total / n, 'gain_per_yr': total / yrs, 'gain_sym_yr': total / yrs,
        'gain_per_week': total / weeks, 'bh_pct_window': bh_pct,
        'years': yrs, 'n_syms': 1, 'tag': f'per_sym_dual_{sym}',
        'trade_list': trades, 'params': params.to_dict(),
        'long_trades': n_long, 'short_trades': n_short,
        'reverse_on_exit_count': n_reverse, 'follow_through_count': n_ft,
        'augment_count': n_aug, 'hedge_count': n_hedge, 'mean_rev_reentry_count': n_mr,
        'open_at_end_count': n_open_at_end, 'mtm_pnl_open_pct': mtm_pnl_open,
        'hedge_wt3m_count': n_hedge_wt3m,
        'peak_protect_count': n_peak_protect,
        'mtm_at_end_count': n_mtm_end,
        'hard_loss_count': n_hard_loss,
        'peak_giveback_fixed_count': n_peak_giveback_fixed,
        'noloss_auto_disabled': params.__dict__.get('_noloss_auto_disabled', ''),
    }


def simulate(sym: str, side: str, params: SymParams, years_back: float = 4.0) -> Optional[Dict]:
    """Returns dict with per-(sym,side) trades + canonical metrics, or None on data miss."""
    if side not in ('LONG', 'SHORT'): raise ValueError(f"side must be LONG or SHORT, got {side}")
    base = load_3m_base(sym, years_back=years_back)
    if base is None or len(base['close']) < 5000: return None
    tf_data = build_tf_data(base)
    needed = list(DECISION_TFS) + ['D']
    for tf in needed:
        if tf not in tf_data or len(tf_data[tf]['close']) < 50: return None
    n_15m = len(tf_data['15m']['close'])
    e_count = np.zeros(n_15m, dtype=np.int16)
    x_count = np.zeros(n_15m, dtype=np.int16)
    for tf in DECISION_TFS:
        e_sig, x_sig = per_tf_signals(tf_data['15m'], side, tf, params)
        e_count += e_sig.astype(np.int16)
        x_count += x_sig.astype(np.int16)
    min_tfs = max(MIN_TFS_AGREE_FLOOR, int(params.MIN_TFS_AGREE))
    enter = e_count >= min_tfs
    leave = x_count >= MIN_TFS_AGREE_FLOOR
    htf_pass = htf_trend_pass(tf_data['D'], tf_data.get('W'), side, params, n_15m)
    enter = enter & htf_pass
    c15 = tf_data['15m']['close']
    ts15 = tf_data['15m']['ts']
    trades = walk_trades(enter, leave, c15, ts15, side, int(params.MIN_HOLD_BARS_15m), int(params.COOLDOWN_BARS_15m))
    bh_pct = float((c15[-1] / c15[0] - 1.0) * 100.0) if c15[0] > 0 else 0.0
    if not trades: return {'sym': sym, 'side': side, 'trades': 0, 'trades_per_day': 0.0, 'pool_sharpe': 0.0, 'sym_sharpe': 0.0, 'wr_pct': 0.0, 'max_dd_pct': 0.0, 'total_gain_pct': 0.0, 'avg_gain_trade': 0.0, 'gain_per_yr': 0.0, 'gain_per_week': 0.0, 'bh_pct_window': bh_pct, 'gain_sym_yr': 0.0, 'years': (ts15[-1] - ts15[0]) / 86400 / 365.25, 'n_syms': 1, 'tag': f'per_sym_{sym}_{side}', 'trade_list': [], 'params': params.to_dict()}
    rets = np.array([t['pnl_pct'] for t in trades], dtype=np.float64)
    n = len(rets)
    span_days = max(1.0, (ts15[-1] - ts15[0]) / 86400.0)
    yrs = max(0.01, span_days / 365.25)
    weeks = max(1.0 / 7.0, span_days / 7.0)
    sd = float(rets.std())
    pool = float(rets.mean() / sd) if sd > 1e-12 else 0.0
    wr = float((rets > 0).mean() * 100.0)
    eq = np.cumsum(rets)
    peak = np.maximum.accumulate(eq)
    dd = float((peak - eq).max())
    total = float(rets.sum())
    return {'sym': sym, 'side': side, 'trades': n, 'trades_per_day': n / span_days, 'pool_sharpe': pool, 'sym_sharpe': max(-5.0, min(5.0, pool)), 'wr_pct': wr, 'max_dd_pct': dd, 'total_gain_pct': total, 'avg_gain_trade': total / n, 'gain_per_yr': total / yrs, 'gain_sym_yr': total / yrs, 'gain_per_week': total / weeks, 'bh_pct_window': bh_pct, 'years': yrs, 'n_syms': 1, 'tag': f'per_sym_{sym}_{side}', 'trade_list': trades, 'params': params.to_dict()}


# ───────────────────────── CLI smoke test ──────────────────────────────────

def _smoke(syms: List[str]) -> None:
    print(f"smoke test on {syms}")
    for s in syms:
        for side in ('LONG', 'SHORT'):
            r = simulate(s, side, SymParams(), years_back=4.0)
            if r is None:
                print(f"  {s} {side}: NPZ MISS or insufficient data")
                continue
            print(f"  {s:12s} {side:5s} pool={r['pool_sharpe']:+.4f} "
                  f"wr={r['wr_pct']:5.1f}% trades={r['trades']:>5d} "
                  f"tpd={r['trades_per_day']:5.2f} dd={r['max_dd_pct']:5.2f}% "
                  f"yrs={r['years']:.2f}")


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--syms', default='BTCUSDC,ETHUSDC,SOLUSDC')
    args = ap.parse_args()
    _smoke([s.strip() for s in args.syms.split(',') if s.strip()])
