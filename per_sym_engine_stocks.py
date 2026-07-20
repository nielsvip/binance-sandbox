#!/usr/bin/env python3
"""per_sym_engine_stocks — stocks variant of per_sym_engine_crypto.

Same architecture as crypto engine, with stocks-specific deltas per CLAUDE.md
"Crypto vs Stock Parameters — OPPOSITE — NEVER copy between them" table:

| Parameter | Crypto Best | Stock Best |
|-----------|------------|------------|
| Entry score | 18 | 24 |
| Reentry stoch gate | K<50 | K<80 |
| HTF alignment | ≥1 | ≥2 |
| ADX in sizing | Disable | Keep |
| Sizing indicator | RSI ok | MFI only |
| WT cross alignment | ≥2 | ≥3 |
| Combined stoch gate | 50 | 60 |
| LTF | 3m | 5m |
| Commission RT | 0.04%/side (0.08% RT) | 0.02%/side (0.04% RT) |

Stocks NPZ has close_5m (no close_3m). Tradier has 0% commission + 2bp slippage.
HTF focus: stocks favor 4h/D-driven setups; profiler grids weight HTF higher.

Reuses crypto engine's vectorized infrastructure via import — only the defaults,
LTF, commission, and DECISION_TFS differ.
"""
from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Import crypto engine internals — re-use everything except commission constant + defaults.
import per_sym_engine_crypto as _crypto
from per_sym_engine_crypto import (
    compute_bb, compute_wt, compute_dc, bb_auto_tune_mult,
    walk_trades, walk_trades_dual, _build_signals,
    _import_v8, _ensure_v8_loaded as _crypto_v8_ensure,
    compute_hierarchy_full, NPZ_DIR,
)

# Stocks-specific commission
STOCKS_COMMISSION_PER_SIDE = 0.0002  # 0% fee + 0.02% slippage = 0.02% per side
COMMISSION_RT_PCT_STOCKS = 100.0 * 2.0 * STOCKS_COMMISSION_PER_SIDE  # 0.04% RT

# Hard floor — same multi-TF rule as crypto (≥2 TFs agree)
MIN_TFS_AGREE_FLOOR = 2

# Stocks decision TFs — focus higher per user 2026-05-05 directive
DECISION_TFS_STOCKS = ('15m', '1h', '4h', 'D')

# 5m bars per HTF bar (NOT 3m — stocks have no 3m)
TF_BARS_5M = {'5m': 1, '15m': 3, '1h': 12, '4h': 48, 'D': 78, 'W': 78 * 5, 'M': 78 * 21}
# Stocks trade ~6.5h/day (RTH) at 5m = 78 bars/day
BARS_PER_DAY_STOCKS = 78

_npz_cache_stocks: Dict[str, Dict[str, np.ndarray]] = {}


def _vec_wtdc_gr_gate(base: Dict[str, np.ndarray], n_5m: int,
                      wtdc_threshold: float, gr_min_tfs: int, gr_min_ind: int) -> Tuple[np.ndarray, np.ndarray]:
    """Vectorized WT_DC entry score + GR_HTF gate — mirrors tradier_manage.py entry gates.

    Returns (long_ok_5m, short_ok_5m) boolean arrays at 5m resolution.
    LONG entry allowed where both gates pass; same for SHORT.
    Bars with missing data return True (gate disabled for that bar) to avoid killing
    legitimate entries where NPZ has gaps — the engine's own signal already handles that.
    """
    n = n_5m
    def _g(k): return base.get(k)
    def _safe_arr(k, fill=np.nan):
        a = _g(k)
        if a is None:
            return np.full(n, fill, dtype=np.float32)
        return np.asarray(a, dtype=np.float32)[:n]

    # ── WT_DC multi-TF score (mirrors score_entry_multitf) ──────────────────
    # LONG: D+4h WT bullish + 1h cross BULL + dc_1h<0.5 + k_5m<40  → max 100
    # SHORT: D+4h WT bearish + 1h cross BEAR + dc_1h>0.5 + k_5m>60 → max 100
    wt1_D  = _safe_arr('wt1_D');  wt2_D  = _safe_arr('wt2_D')
    wt1_4h = _safe_arr('wt1_4h'); wt2_4h = _safe_arr('wt2_4h')
    dc_1h  = _safe_arr('dc_position_1h', 0.5)
    k_5m   = _safe_arr('stoch_k_5m', 50)
    bull_1h = (_g('wt_cross_bull_1h') is not None and
               np.asarray(_g('wt_cross_bull_1h'), dtype=np.int8)[:n].astype(bool))
    bear_1h = (_g('wt_cross_bear_1h') is not None and
               np.asarray(_g('wt_cross_bear_1h'), dtype=np.int8)[:n].astype(bool))
    if isinstance(bull_1h, bool): bull_1h = np.zeros(n, dtype=bool)
    if isinstance(bear_1h, bool): bear_1h = np.zeros(n, dtype=bool)

    score_long  = (np.float32(25) * (wt1_D  > wt2_D).astype(np.float32) +
                   np.float32(25) * (wt1_4h > wt2_4h).astype(np.float32) +
                   np.float32(30) * bull_1h.astype(np.float32) +
                   np.float32(10) * (dc_1h  < np.float32(0.5)).astype(np.float32) +
                   np.float32(10) * (k_5m   < np.float32(40)).astype(np.float32))
    score_short = (np.float32(25) * (wt1_D  < wt2_D).astype(np.float32) +
                   np.float32(25) * (wt1_4h < wt2_4h).astype(np.float32) +
                   np.float32(30) * bear_1h.astype(np.float32) +
                   np.float32(10) * (dc_1h  > np.float32(0.5)).astype(np.float32) +
                   np.float32(10) * (k_5m   > np.float32(60)).astype(np.float32))

    # If ALL driver fields are NaN at a bar → data gap → don't block (gate disabled)
    all_nan = (np.isnan(wt1_D) & np.isnan(wt2_D) & np.isnan(wt1_4h) & np.isnan(wt2_4h))
    thr = np.float32(wtdc_threshold) if wtdc_threshold > 0 else np.float32(0.0)
    wtdc_long_ok  = (score_long  >= thr) | all_nan
    wtdc_short_ok = (score_short >= thr) | all_nan

    # ── GR_HTF gate (mirrors golden_rule_htf.score_entry_htf, mode='tradier') ──
    # TFs: 5m, 15m, 1h, 4h, D, W — for each TF count how many indicators agree
    # Indicators (7 per TF, matching golden_rule_htf feature set):
    #   wt1>wt2, RSI>50, MFI>50, DC_pos<0.65/>.35, bb_pct_b<0.5/>0.5, rvol>1, stoch_k<50/>50
    # A TF "confirms" when ≥ gr_min_ind indicators agree.
    # NOTE: live golden_rule_htf counts 8 indicators (wt1+wt2 as separate features).
    #   Here wt1>wt2 counts as 1 combined indicator → 7 total.
    #   With gr_min_ind=5 (historical baseline): 5/7=71% ≈ 5/8=62.5% live equivalent.
    gr_long_ok  = np.ones(n, dtype=bool)
    gr_short_ok = np.ones(n, dtype=bool)
    if gr_min_tfs > 0:
        GR_TFS = ('5m', '15m', '1h', '4h', 'D', 'W')
        confirmed_long  = np.zeros(n, dtype=np.int8)
        confirmed_short = np.zeros(n, dtype=np.int8)
        for tf in GR_TFS:
            w1 = _safe_arr(f'wt1_{tf}'); w2 = _safe_arr(f'wt2_{tf}')
            rs = _safe_arr(f'rsi_{tf}',  -1.0)
            mf = _safe_arr(f'mfi_{tf}',  -1.0)
            dc = _safe_arr(f'dc_position_{tf}', -1.0)
            bb = _safe_arr(f'bb_pct_b_{tf}', -1.0)
            rv = _safe_arr(f'relative_volume_{tf}', -1.0)
            sk = _safe_arr(f'stoch_k_{tf}', -1.0)
            ind_l = ((w1 > w2).astype(np.int8) +
                     (rs > 50).astype(np.int8) * (rs >= 0).astype(np.int8) +
                     (mf > 50).astype(np.int8) * (mf >= 0).astype(np.int8) +
                     ((dc < 0.65).astype(np.int8) * (dc >= 0).astype(np.int8)) +
                     ((bb < 0.50).astype(np.int8) * (bb >= 0).astype(np.int8)) +
                     ((rv > 1.0).astype(np.int8) * (rv >= 0).astype(np.int8)) +
                     ((sk < 50.0).astype(np.int8) * (sk >= 0).astype(np.int8)))
            ind_s = ((w1 < w2).astype(np.int8) +
                     (rs < 50).astype(np.int8) * (rs >= 0).astype(np.int8) +
                     (mf < 50).astype(np.int8) * (mf >= 0).astype(np.int8) +
                     ((dc > 0.35).astype(np.int8) * (dc >= 0).astype(np.int8)) +
                     ((bb > 0.50).astype(np.int8) * (bb >= 0).astype(np.int8)) +
                     ((rv > 1.0).astype(np.int8) * (rv >= 0).astype(np.int8)) +
                     ((sk > 50.0).astype(np.int8) * (sk >= 0).astype(np.int8)))
            confirmed_long  += (ind_l >= gr_min_ind).astype(np.int8)
            confirmed_short += (ind_s >= gr_min_ind).astype(np.int8)
        gr_long_ok  = confirmed_long  >= gr_min_tfs
        gr_short_ok = confirmed_short >= gr_min_tfs

    return (wtdc_long_ok & gr_long_ok), (wtdc_short_ok & gr_short_ok)


@dataclass
class SymParamsStocks:
    """Stocks SymParams — mirrors crypto SymParams structure with stocks-tuned defaults."""
    # Indicator params (same names as crypto, different defaults — sweep finds per-sym best)
    BB_LEN_15m: int = 20; BB_STD_15m: float = 2.0
    BB_LEN_1h: int = 20;  BB_STD_1h: float = 2.0
    BB_LEN_4h: int = 20;  BB_STD_4h: float = 2.0
    BB_LEN_D: int = 20;   BB_STD_D: float = 2.0
    WT_CHAN_15m: int = 10; WT_AVG_15m: int = 21
    WT_CHAN_1h: int = 10;  WT_AVG_1h: int = 21
    WT_CHAN_4h: int = 10;  WT_AVG_4h: int = 21
    WT_CHAN_D: int = 10;   WT_AVG_D: int = 21
    DC_PERIOD_15m: int = 20; DC_PERIOD_1h: int = 20
    DC_PERIOD_4h: int = 20;  DC_PERIOD_D: int = 20

    # MULTI-TF — stocks default tighter per CLAUDE.md (HTF >= 2, WT cross >= 3)
    MIN_TFS_AGREE: int = 3              # was 2 for crypto
    MIN_TFS_AGREE_ENTRY: int = 3        # 3 of 4 TFs (15m/1h/4h/D) for entry
    MIN_TFS_AGREE_EXIT: int = 2         # 2 of 4 for exit
    MIN_TFS_AGREE_REENTRY: int = 2

    # Hold/cooldown — stocks at 5m granularity. Default longer hold than crypto (slower price action).
    MIN_HOLD_BARS_15m: int = 5          # 5 × 5m = 25 min — proxy for "min hold"
    COOLDOWN_BARS_15m: int = 3

    # Entry filters
    USE_BB_FILTER: bool = True
    USE_WT_CROSS: bool = True
    USE_DC_BREAK: bool = True
    BB_TOP_THRESHOLD: float = 0.95
    BB_BOT_THRESHOLD: float = 0.05
    BB_LONG_ENTRY_MAX: float = 0.20     # same — buy oversold
    BB_SHORT_ENTRY_MIN: float = 0.80
    BB_AUTO_TUNE_ENABLED: bool = False
    BB_AUTO_TUNE_LOOKBACK: int = 100
    BB_AUTO_TUNE_MIN_MULT: float = 1.5
    BB_AUTO_TUNE_MAX_MULT: float = 3.5
    REQUIRE_D_TREND: bool = True        # stocks default ON (trend-following bias)
    REQUIRE_W_TREND: bool = False
    ENTRY_MODE: str = 'or'

    # Parallel entry paths (same as crypto)
    ENTRY_WT_CROSS_EVENT_ENABLED: bool = True
    ENTRY_WT_CROSS_LOOKBACK: int = 3
    ENTRY_BB_EXTREME_BOUNCE_ENABLED: bool = True
    ENTRY_BB_EXTREME_THRESHOLD: float = 0.10
    ENTRY_BB_SQUEEZE_RELEASE_ENABLED: bool = True
    ENTRY_BB_SQUEEZE_RATIO: float = 1.5
    ENTRY_BB_SQUEEZE_LOOKBACK: int = 20
    ENTRY_LIQ_SWEEP_ENABLED: bool = True
    ENTRY_LIQ_SWEEP_LOOKBACK: int = 10
    ENTRY_NR7_ENABLED: bool = True
    ENTRY_NR_LOOKBACK: int = 7
    ENTRY_VOL_SPIKE_ENABLED: bool = True
    ENTRY_VOL_SPIKE_RATIO: float = 2.0
    ENTRY_VOL_SPIKE_LOOKBACK: int = 20
    ENTRY_EMA_RIBBON_ENABLED: bool = True
    ENTRY_EMA_FAST: int = 9
    ENTRY_EMA_MID: int = 21
    ENTRY_EMA_SLOW: int = 50
    ENTRY_WILLR_ENABLED: bool = True
    ENTRY_WILLR_LOOKBACK: int = 14
    ENTRY_WILLR_OS_THRESHOLD: float = -85.0
    ENTRY_WILLR_OB_THRESHOLD: float = -15.0
    ENTRY_FVG_ENABLED: bool = True

    # Exit refinements
    EXIT_REQUIRE_BOTH: bool = False
    EXIT_WT_ACCEL_ONLY: bool = False
    PARTIAL_PROFIT_LOCK_ENABLED: bool = False
    PARTIAL_PROFIT_LOCK_GAIN_PCT: float = 1.0
    # PPL v2 fields (Phase 2A patch 1) — wired in walk_trades_dual when ENABLED
    PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT: float = 0.75
    PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT: float = 0.10
    PARTIAL_PROFIT_LOCK_FRAC: float = 0.5
    # X7 frozen-DC + abs-floor stop (Phase 2A patch 2)
    X7_FROZEN_DC_STOP_ENABLED: bool = False
    X7_FREEZE_DC_TF: str = '4h'
    X7_FREEZE_BB_TF: str = ''
    X7_ABS_FLOOR_PCT: float = -8.0
    # SPY-regime gate (Phase 2A patch 3 — stocks-specific)
    SPY_REGIME_GATE_ENABLED: bool = False
    SPY_REGIME_BLOCK_LONGS_BELOW: bool = True
    SPY_REGIME_BLOCK_SHORTS_ABOVE: bool = False
    # SMA50_D LT-direction filter (Phase 2A patch 3 — shared)
    REQUIRE_ABOVE_SMA50_D: bool = False
    # HEDGE_HTF_VETO + HTF_TREND_VETO (Phase 2A patch 4)
    HEDGE_HTF_VETO_ENABLED: bool = False
    HTF_TREND_VETO_ENABLED: bool = False
    EXIT_STRUCT_TF: str = 'None'
    LONG_STRUCT_EXIT_TF: str = 'D'
    SHORT_STRUCT_EXIT_TF: str = '15m'

    # Reentry / reverse / augment / hedge / NOLOSS
    REVERSE_ON_EXIT_ENABLED: bool = False
    FOLLOW_THROUGH_REENTRY_ENABLED: bool = False
    FOLLOW_THROUGH_MIN_MOVE_PCT: float = 0.05
    FOLLOW_THROUGH_WINDOW_BARS: int = 5
    BREAKOUT_PATH_ENABLED: bool = False
    BREAKOUT_HTF_MIN_ALIGNED: int = 1
    BREAKOUT_MIN_HOLD_BARS: int = 1
    AUGMENT_ENABLED: bool = True
    AUGMENT_LEVELS_PCT: tuple = (1.0, 2.0, 3.0, 4.0)
    REENTRY_MEAN_REV_ENABLED: bool = False
    REENTRY_MEAN_REV_TOLERANCE_PCT: float = 0.30
    REENTRY_MEAN_REV_WINDOW_BARS: int = 10
    HEDGE_ENABLED: bool = True
    HEDGE_SIZE_FRAC: float = 0.5
    HEDGE_WT_TRIGGER: bool = True
    HEDGE_WT_TF: str = '15m'            # stocks default 15m (no 3m available)
    HEDGE_CYCLES_ENABLED: bool = True
    HEDGE_TRIGGER_GAIN_PCT_ENABLED: bool = False
    HEDGE_TRIGGER_GAIN_PCT: float = -0.5
    NOLOSS_ENABLED: bool = True
    PEAK_PROTECT_ENABLED: bool = True
    PEAK_PROTECT_REQUIRE_GAIN: bool = True
    PEAK_GIVEBACK_FIXED_PCT_ENABLED: bool = False
    PEAK_GIVEBACK_FIXED_DROP_PCT: float = 0.5
    PEAK_GIVEBACK_FIXED_MIN_PEAK_PCT: float = 0.10
    HARD_LOSS_PCT_ENABLED: bool = True
    HARD_LOSS_PCT: float = 0.5

    # Booster gates — stocks have no funding rate (not perpetuals); OI optional via OI_CONFIRM
    FUNDING_GATE_ENABLED: bool = False  # stocks have no funding
    FUNDING_GATE_LONG_MAX: float = 0.0005
    FUNDING_GATE_SHORT_MIN: float = -0.0005
    OI_GATE_ENABLED: bool = False       # stocks options OI is different — TBD
    OI_GATE_OI_CHANGE_MIN: float = -10.0
    WT_ACCEL_GATE_ENABLED: bool = True
    WT_ACCEL_GATE_TF: str = '1h'
    DIVERGENCE_BLOCK_ENABLED: bool = True
    DIVERGENCE_LB: int = 20

    # HH/HL price-action
    USE_PRICE_ACTION_ENTRY: bool = False
    PRICE_ACTION_LB: int = 3

    # WT_DC HIERARCHY + RZ_CASCADE + V8 AGGREGATORS
    USE_WT_DC_HIERARCHY: bool = False
    HIER_RZ_TOP_BB: float = 0.85
    HIER_RZ_BOT_BB: float = 0.15
    HIER_DC_BAND_PCT: float = 0.2
    HIER_WT_DELTA_MIN: float = 0.0
    HIER_WT_VEL_MIN: float = 0.0
    HIER_USE_W_M: bool = False
    USE_RZ_CASCADE: bool = False
    RZ_CASCADE_AT_RZ_BAND_PCT: float = 1.0
    RZ_CASCADE_WT_DELTA_MIN: float = 0.1
    RZ_CASCADE_VEL_MIN: float = 0.1
    RZ_CASCADE_HIGH_LOOKBACK: int = 20
    RZ_CASCADE_REQUIRE_NEW_HIGH: bool = False
    RZ_CASCADE_MIN_TF_ALIGN: int = 1
    RZ_CASCADE_EXIT_ANY_TF: bool = True
    RZ_CASCADE_EXIT_MIN_REV_TFS: int = 2
    RZ_CASCADE_USE_W_M: bool = False
    USE_V8_AGGREGATORS: bool = True     # default ON for stocks too

    # Stocks-specific
    MODE: str = 'tradier'
    LTF: str = '5m'
    K3M_FLOOR: float = 25.0             # K-zone floor — sweep
    CT_WT_VELOCITY_1H_MIN: float = 0.0
    # USER 2026-05-06 mandate — backtest must mirror live safety guards (same as crypto).
    BT_DC_BB_D_BREAK_REVERSE_ENABLED: bool = True
    BT_WT15M_AGAINST_FORCE_HEDGE_ENABLED: bool = True
    BT_ALL_TF_AGAINST_CLOSE_ENABLED: bool = True
    BT_RIDICULOUS_HOLD_GUARD_ENABLED: bool = True
    BT_RIDICULOUS_LOSS_PCT: float = -15.0
    BT_RIDICULOUS_HOLD_HOURS: float = 48.0
    BT_UNDERWATER_HEDGE_OR_CLOSE_ENABLED: bool = True

    def to_dict(self) -> Dict:
        return asdict(self)

    def copy(self) -> 'SymParamsStocks':
        return SymParamsStocks(**self.to_dict())


# ─────── stocks-specific data loader ───────

def load_5m_base(sym: str, years_back: float = 4.0) -> Optional[Dict[str, np.ndarray]]:
    """Load FULL NPZ dict + 5m OHLC + ts. Sliced to last N years. Cached (1 sym max)."""
    cache_key = f'{sym}__y{years_back:.2f}'
    if cache_key in _npz_cache_stocks:
        return _npz_cache_stocks[cache_key]
    p = NPZ_DIR / f'{sym}.npz'
    if not p.exists():
        return None
    z = np.load(str(p))
    needed = ['open_5m', 'high_5m', 'low_5m', 'close_5m', 'timestamps']
    if not all(k in z.files for k in needed):
        z.close()
        return None
    full: Dict[str, np.ndarray] = {}
    for k in z.files:
        full[k] = z[k][:]
    z.close()
    if _npz_cache_stocks:
        _npz_cache_stocks.clear()
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
    sliced['open'] = sliced['open_5m'].astype(np.float64)
    sliced['high'] = sliced['high_5m'].astype(np.float64)
    sliced['low'] = sliced['low_5m'].astype(np.float64)
    sliced['close'] = sliced['close_5m'].astype(np.float64)
    sliced['volume'] = sliced.get('volume_5m', np.ones(len(sliced['close']))).astype(np.float64)
    sliced['ts'] = sliced['timestamps']
    _npz_cache_stocks[cache_key] = sliced
    return sliced


def resample_5m_to_htf(base: Dict[str, np.ndarray], tf_bars: int) -> Dict[str, np.ndarray]:
    """Resample 5m OHLC to HTF (tf_bars=5m bars per HTF bar)."""
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


def build_tf_data_stocks(base: Dict[str, np.ndarray]) -> Dict[str, Dict[str, np.ndarray]]:
    """Build {tf: ohlc-dict} for 15m, 1h, 4h, D from 5m base."""
    out: Dict[str, Dict[str, np.ndarray]] = {}
    out['5m'] = {k: base[k] for k in ('open','high','low','close','volume','ts')}
    for tf in ('15m', '1h', '4h', 'D'):
        ratio = TF_BARS_5M[tf]
        d = resample_5m_to_htf(base, ratio)
        if len(d['close']) > 0:
            out[tf] = d
    return out


# ─────── stocks simulate (uses crypto walker but stock commission) ───────

def simulate_dual_stocks(sym: str, params: SymParamsStocks, years_back: float = 4.0,
                         only_side: Optional[str] = None) -> Optional[Dict]:
    """Stocks dual-side simulation. Mirrors crypto's simulate_dual but with 5m base + stocks commission."""
    # Couple NOLOSS↔HEDGE
    if params.NOLOSS_ENABLED and not params.HEDGE_ENABLED:
        params = params.copy()
        params.NOLOSS_ENABLED = False
        params.__dict__['_noloss_auto_disabled'] = 'NOLOSS requires HEDGE — auto-disabled'

    base = load_5m_base(sym, years_back=years_back)
    min_bars = max(1000, int(min(years_back, 0.05) * BARS_PER_DAY_STOCKS * 0.7))
    if base is None or len(base['close']) < min_bars:
        return None
    tf_data = build_tf_data_stocks(base)
    min_per_tf = {'15m': 100, '1h': 50, '4h': 20, 'D': 5} if years_back < 0.1 else {tf: 50 for tf in ('15m','1h','4h','D')}
    for tf in ('15m', '1h', '4h', 'D'):
        if tf not in tf_data or len(tf_data[tf]['close']) < min_per_tf.get(tf, 50):
            return None

    n_15m = len(tf_data['15m']['close'])

    # Build entry/exit signals — try v8 aggregators first (works on 5m base too via cfg.LTF='5m')
    enter_long = enter_short = leave_long = leave_short = None
    if getattr(params, 'USE_V8_AGGREGATORS', True):
        _crypto_v8_ensure()
        from per_sym_engine_crypto import _v8_compute_entry, _v8_compute_exit, _v8_QuickConfig
        n_5m = len(base['close_5m']) if 'close_5m' in base else len(base['close'])
        qcfg = _v8_QuickConfig()
        qcfg.MODE = 'tradier'
        qcfg.LTF = '5m'
        for pk, pv in params.to_dict().items():
            if hasattr(qcfg, pk):
                try: setattr(qcfg, pk, pv)
                except Exception: pass
        try:
            v8_el = _v8_compute_entry(base, n_5m, True, qcfg, sym=sym)
            v8_es = _v8_compute_entry(base, n_5m, False, qcfg, sym=sym)
            v8_xl = _v8_compute_exit(base, n_5m, True, qcfg)
            v8_xs = _v8_compute_exit(base, n_5m, False, qcfg)
            # Subsample 5m → 15m (every 3rd index for stocks)
            ratio = TF_BARS_5M['15m']  # 3
            def _ss(arr):
                cut = (len(arr) // ratio) * ratio
                sub = arr[:cut][ratio - 1::ratio]
                if len(sub) >= n_15m: return sub[:n_15m]
                return np.concatenate([np.zeros(n_15m - len(sub), dtype=bool), sub])
            enter_long = _ss(v8_el); enter_short = _ss(v8_es)
            leave_long = _ss(v8_xl); leave_short = _ss(v8_xs)
        except Exception as e:
            print(f"[stocks] v8 aggregators error: {e}", flush=True)

    if enter_long is None:
        enter_long, leave_long = _build_signals(tf_data, 'LONG', params)
        enter_short, leave_short = _build_signals(tf_data, 'SHORT', params)

    if only_side == 'LONG':
        enter_short = np.zeros_like(enter_short)
    elif only_side == 'SHORT':
        enter_long = np.zeros_like(enter_long)

    # ─── WT_DC + GR_HTF entry gates — must mirror tradier_manage.py live gates ───
    # Read thresholds from config_tradier so per_sym backtest = live quality gates.
    # SymParamsStocks overrides take precedence (allow per-sym sweep to vary these).
    try:
        import config_tradier as _ct
        _wtdc_thr   = float(getattr(params, 'WT_DC_ENTRY_THRESHOLD',
                                     getattr(_ct, 'WT_DC_ENTRY_THRESHOLD', 45)))
        _gr_min_tfs = int(getattr(params, 'GOLDEN_RULE_HTF_MIN_TFS',
                                   getattr(_ct, 'GOLDEN_RULE_HTF_MIN_TFS', 3)))
        _gr_min_ind = int(getattr(params, 'GOLDEN_RULE_MIN_IND',
                                   getattr(_ct, 'GOLDEN_RULE_MIN_IND', 3)))
    except Exception:
        _wtdc_thr = 45.0; _gr_min_tfs = 3; _gr_min_ind = 3
    _n_5m_gate = len(base.get('close_5m', base['close']))
    _gate_long_5m, _gate_short_5m = _vec_wtdc_gr_gate(
        base, _n_5m_gate, _wtdc_thr, _gr_min_tfs, _gr_min_ind)
    # Subsample gate from 5m → 15m grid (take the LAST 5m bar of each 15m window)
    _ratio_gate = TF_BARS_5M['15m']  # 3
    def _ss_gate(arr_5m):
        cut = (len(arr_5m) // _ratio_gate) * _ratio_gate
        sub = arr_5m[:cut][_ratio_gate - 1::_ratio_gate]
        if len(sub) >= n_15m: return sub[:n_15m]
        return np.concatenate([np.ones(n_15m - len(sub), dtype=bool), sub])
    enter_long  = enter_long  & _ss_gate(_gate_long_5m)
    enter_short = enter_short & _ss_gate(_gate_short_5m)

    # ─── USER 2026-05-06 mandate: stocks backtest mirrors live safety guards ───
    # Same masks as crypto engine. Stocks 5m base; subsample every 3rd to 15m grid.
    n_15m_safety = len(tf_data['15m']['close'])
    def _ss_5m_to_15m(arr_5m):
        if arr_5m is None or len(arr_5m) == 0:
            return np.zeros(n_15m_safety, dtype=bool)
        ratio = TF_BARS_5M['15m']  # 3
        cut = (len(arr_5m) // ratio) * ratio
        sub = arr_5m[:cut][ratio - 1::ratio]
        if len(sub) >= n_15m_safety: return sub[:n_15m_safety]
        return np.concatenate([np.zeros(n_15m_safety - len(sub), dtype=bool), sub])
    if getattr(params, 'BT_ALL_TF_AGAINST_CLOSE_ENABLED', True):
        # Stocks NPZ has wt1/wt2 fields per TF (5m, 15m, 1h, 4h, D)
        n_5m_loc = len(base.get('close_5m', base['close']))
        all_against_long = np.ones(n_5m_loc, dtype=bool)
        all_against_short = np.ones(n_5m_loc, dtype=bool)
        for w1k, w2k in [('wt1_5m','wt2_5m'),('wt1_15m','wt2_15m'),('wt1_1h','wt2_1h'),('wt1_4h','wt2_4h'),('wt1_D','wt2_D')]:
            w1 = base.get(w1k); w2 = base.get(w2k)
            if w1 is None or w2 is None or len(w1) != n_5m_loc:
                all_against_long = np.zeros(n_5m_loc, dtype=bool); break
            all_against_long &= (w1 < w2)
            all_against_short &= (w1 > w2)
        leave_long = leave_long | _ss_5m_to_15m(all_against_long)
        leave_short = leave_short | _ss_5m_to_15m(all_against_short)
    if getattr(params, 'BT_WT15M_AGAINST_FORCE_HEDGE_ENABLED', True):
        w1_15m = base.get('wt1_15m'); w2_15m = base.get('wt2_15m')
        if w1_15m is not None and w2_15m is not None and len(w1_15m) == len(base.get('close_5m', base['close'])):
            leave_long = leave_long | _ss_5m_to_15m(w1_15m < w2_15m)
            leave_short = leave_short | _ss_5m_to_15m(w1_15m > w2_15m)
    if getattr(params, 'BT_DC_BB_D_BREAK_REVERSE_ENABLED', True):
        close_5m = base.get('close_5m', base['close'])
        n_5m_loc = len(close_5m)
        d_break_up = np.zeros(n_5m_loc, dtype=bool)
        d_break_dn = np.zeros(n_5m_loc, dtype=bool)
        for hk, target in [('dc_high_D', 'up'), ('bb_upper_D', 'up')]:
            arr = base.get(hk)
            if arr is not None and len(arr) == n_5m_loc:
                prev = np.roll(arr, 1); prev[0] = arr[0]
                d_break_up |= (close_5m > prev) & (prev > 0)
        for lk, target in [('dc_low_D', 'down'), ('bb_lower_D', 'down')]:
            arr = base.get(lk)
            if arr is not None and len(arr) == n_5m_loc:
                prev = np.roll(arr, 1); prev[0] = arr[0]
                d_break_dn |= (close_5m < prev) & (prev > 0)
        leave_short = leave_short | _ss_5m_to_15m(d_break_up)
        leave_long = leave_long | _ss_5m_to_15m(d_break_dn)

    # WT for peak-protect + hedge
    h15 = tf_data['15m']['high']; l15 = tf_data['15m']['low']; c15_close = tf_data['15m']['close']
    wt1_15m_arr, wt2_15m_arr = compute_wt(h15, l15, c15_close,
                                            int(params.WT_CHAN_15m), int(params.WT_AVG_15m))
    # Hedge WT trigger TF — stocks default 15m
    hedge_tf = getattr(params, 'HEDGE_WT_TF', '15m')
    if hedge_tf in ('15m', '1h', '4h', 'D'):
        tfd = tf_data[hedge_tf]
        wt1_tf, wt2_tf = compute_wt(tfd['high'], tfd['low'], tfd['close'],
                                      int(getattr(params, f'WT_CHAN_{hedge_tf}')),
                                      int(getattr(params, f'WT_AVG_{hedge_tf}')))
        repeat_ratio = TF_BARS_5M[hedge_tf] // TF_BARS_5M['15m']
        if repeat_ratio == 1:
            wt1_at_15m = wt1_tf[:n_15m]; wt2_at_15m = wt2_tf[:n_15m]
        else:
            wt1_at_15m = np.repeat(wt1_tf, repeat_ratio)[:n_15m]
            wt2_at_15m = np.repeat(wt2_tf, repeat_ratio)[:n_15m]
    else:  # 5m → 15m
        wt1_5m, wt2_5m = compute_wt(base['high'], base['low'], base['close'],
                                      int(params.WT_CHAN_15m), int(params.WT_AVG_15m))
        ratio = TF_BARS_5M['15m']
        cut = (len(wt1_5m) // ratio) * ratio
        wt1_at_15m = wt1_5m[:cut][ratio - 1::ratio][:n_15m]
        wt2_at_15m = wt2_5m[:cut][ratio - 1::ratio][:n_15m]
    if len(wt1_at_15m) < n_15m:
        pad = n_15m - len(wt1_at_15m)
        wt1_at_15m = np.concatenate([np.zeros(pad), wt1_at_15m])
        wt2_at_15m = np.concatenate([np.zeros(pad), wt2_at_15m])

    c15 = tf_data['15m']['close']
    ts15 = tf_data['15m']['ts']

    # ─── Phase 2A patches 3-4 for stocks (SMA50_D / SPY-regime / HTF_TREND_VETO / HEDGE_HTF_VETO / X7 freeze) ───
    n_c15 = len(c15)
    def _ss_5m_arr_to_15m(arr_5m):
        if arr_5m is None or len(arr_5m) == 0:
            return None
        ratio = TF_BARS_5M['15m']  # 3
        cut = (len(arr_5m) // ratio) * ratio
        sub = arr_5m[:cut][ratio - 1::ratio]
        if len(sub) >= n_c15:
            return sub[:n_c15]
        pad = n_c15 - len(sub)
        return np.concatenate([np.zeros(pad), sub])
    # Patch 3 — REQUIRE_ABOVE_SMA50_D
    if getattr(params, 'REQUIRE_ABOVE_SMA50_D', False):
        sma50_5m = base.get('sma_50_D')
        sma50_15m = _ss_5m_arr_to_15m(sma50_5m)
        if sma50_15m is not None and len(sma50_15m) >= n_c15:
            above50 = c15 > sma50_15m[:n_c15]
            enter_long = enter_long & above50
            enter_short = enter_short & ~above50
    # Patch 3 — SPY_REGIME_GATE (stocks-only — read SPY NPZ, build above/below 200SMA mask, broadcast)
    if getattr(params, 'SPY_REGIME_GATE_ENABLED', False):
        try:
            spy_base = load_5m_base('SPY', years_back=years_back)
        except Exception:
            spy_base = None
        if spy_base is not None and 'close_5m' in spy_base:
            spy_close = spy_base['close_5m'].astype(np.float64)
            # 200-bar SMA on daily; resample SPY to daily, compute SMA, broadcast back to 5m, then 15m.
            d_spy = resample_5m_to_htf(spy_base, TF_BARS_5M['D'])
            spy_close_d = d_spy['close']
            spy_ts_d = d_spy['ts']
            if len(spy_close_d) >= 200:
                # Manual rolling mean 200 — np convolve
                kernel = np.ones(200, dtype=np.float64) / 200.0
                spy_sma200_d = np.full(len(spy_close_d), np.nan)
                spy_sma200_d[199:] = np.convolve(spy_close_d, kernel, mode='valid')
                spy_above_d = (spy_close_d > spy_sma200_d) & np.isfinite(spy_sma200_d)
                # Broadcast back to symbol's 5m ts grid via searchsorted on spy_ts_d
                sym_ts_5m = base['ts']
                # For each sym 5m bar, find the most-recent SPY daily bar
                idxs = np.searchsorted(spy_ts_d, sym_ts_5m, side='right') - 1
                idxs = np.clip(idxs, 0, len(spy_above_d) - 1)
                above_5m = spy_above_d[idxs]
                # Subsample to 15m grid
                ratio = TF_BARS_5M['15m']
                cut = (len(above_5m) // ratio) * ratio
                sub = above_5m[:cut][ratio - 1::ratio]
                if len(sub) >= n_c15:
                    spy_regime_15m = sub[:n_c15]
                else:
                    spy_regime_15m = np.concatenate([np.zeros(n_c15 - len(sub), dtype=bool), sub])
                if getattr(params, 'SPY_REGIME_BLOCK_LONGS_BELOW', True):
                    enter_long = enter_long & spy_regime_15m
                if getattr(params, 'SPY_REGIME_BLOCK_SHORTS_ABOVE', False):
                    enter_short = enter_short & ~spy_regime_15m
    # Patch 4 — HTF_TREND_VETO (block entries against Daily WT)
    if getattr(params, 'HTF_TREND_VETO_ENABLED', False):
        wt1_d_5m = base.get('wt1_D')
        wt2_d_5m = base.get('wt2_D')
        if wt1_d_5m is not None and wt2_d_5m is not None:
            wt1_d_15m = _ss_5m_arr_to_15m(wt1_d_5m)
            wt2_d_15m = _ss_5m_arr_to_15m(wt2_d_5m)
            if wt1_d_15m is not None and wt2_d_15m is not None:
                n_min = min(len(wt1_d_15m), len(wt2_d_15m), len(enter_long))
                bull_d = wt1_d_15m[:n_min] > wt2_d_15m[:n_min]
                if n_min < len(enter_long):
                    pad = len(enter_long) - n_min
                    bull_d = np.concatenate([np.zeros(pad, dtype=bool), bull_d])
                enter_long = enter_long & bull_d
                enter_short = enter_short & ~bull_d
    # Patch 4 — HEDGE_HTF_VETO precompute
    hedge_htf_ok_long = None
    hedge_htf_ok_short = None
    if getattr(params, 'HEDGE_HTF_VETO_ENABLED', False):
        wt1_d_5m = base.get('wt1_D')
        wt2_d_5m = base.get('wt2_D')
        if wt1_d_5m is not None and wt2_d_5m is not None:
            wt1_d_15m_h = _ss_5m_arr_to_15m(wt1_d_5m)
            wt2_d_15m_h = _ss_5m_arr_to_15m(wt2_d_5m)
            if wt1_d_15m_h is not None and wt2_d_15m_h is not None:
                n_min_h = min(len(wt1_d_15m_h), len(wt2_d_15m_h), n_c15)
                bull_d_h = wt1_d_15m_h[:n_min_h] > wt2_d_15m_h[:n_min_h]
                hedge_htf_ok_long = ~bull_d_h
                hedge_htf_ok_short = bull_d_h
                if n_min_h < n_c15:
                    pad = n_c15 - n_min_h
                    hedge_htf_ok_long = np.concatenate([np.zeros(pad, dtype=bool), hedge_htf_ok_long])
                    hedge_htf_ok_short = np.concatenate([np.zeros(pad, dtype=bool), hedge_htf_ok_short])
    # Patch 2 — X7 frozen-DC precompute
    x7_dc_freeze_15m = None
    x7_bb_freeze_15m = None
    if getattr(params, 'X7_FROZEN_DC_STOP_ENABLED', False):
        dc_lo_5m = base.get(f'dc_low_{params.X7_FREEZE_DC_TF}')
        if dc_lo_5m is not None:
            x7_dc_freeze_15m = _ss_5m_arr_to_15m(dc_lo_5m)
        bb_tf = getattr(params, 'X7_FREEZE_BB_TF', '') or ''
        if bb_tf:
            bb_lo_5m = base.get(f'bb_lower_{bb_tf}')
            if bb_lo_5m is not None:
                x7_bb_freeze_15m = _ss_5m_arr_to_15m(bb_lo_5m)
    struct_long_open = struct_long_high_prev = struct_long_low_prev = None
    struct_short_open = struct_short_high_prev = struct_short_low_prev = None
    long_tf = getattr(params, 'EXIT_STRUCT_TF', 'None')
    if long_tf == 'None' or not long_tf: long_tf = getattr(params, 'LONG_STRUCT_EXIT_TF', 'D')
    if long_tf != 'None' and long_tf in tf_data:
        tfd = tf_data[long_tf]; o_arr = tfd['open']; h_arr = tfd['high']; l_arr = tfd['low']
        h_prev = np.concatenate([[h_arr[0]], h_arr[:-1]]); l_prev = np.concatenate([[l_arr[0]], l_arr[:-1]])
        rep_ratio = TF_BARS_5M[long_tf] // TF_BARS_5M['15m']
        struct_long_open = np.repeat(o_arr, rep_ratio)[:n_15m]
        struct_long_high_prev = np.repeat(h_prev, rep_ratio)[:n_15m]
        struct_long_low_prev = np.repeat(l_prev, rep_ratio)[:n_15m]
        if len(struct_long_open) < n_15m:
            pad = n_15m - len(struct_long_open)
            struct_long_open = np.concatenate([struct_long_open, np.full(pad, o_arr[-1])])
            struct_long_high_prev = np.concatenate([struct_long_high_prev, np.full(pad, h_prev[-1])])
            struct_long_low_prev = np.concatenate([struct_long_low_prev, np.full(pad, l_prev[-1])])
    short_tf = getattr(params, 'EXIT_STRUCT_TF', 'None')
    if short_tf == 'None' or not short_tf: short_tf = getattr(params, 'SHORT_STRUCT_EXIT_TF', '15m')
    if short_tf != 'None' and short_tf in tf_data:
        tfd = tf_data[short_tf]; o_arr = tfd['open']; h_arr = tfd['high']; l_arr = tfd['low']
        h_prev = np.concatenate([[h_arr[0]], h_arr[:-1]]); l_prev = np.concatenate([[l_arr[0]], l_arr[:-1]])
        rep_ratio = TF_BARS_5M[short_tf] // TF_BARS_5M['15m']
        struct_short_open = np.repeat(o_arr, rep_ratio)[:n_15m]
        struct_short_high_prev = np.repeat(h_prev, rep_ratio)[:n_15m]
        struct_short_low_prev = np.repeat(l_prev, rep_ratio)[:n_15m]
        if len(struct_short_open) < n_15m:
            pad = n_15m - len(struct_short_open)
            struct_short_open = np.concatenate([struct_short_open, np.full(pad, o_arr[-1])])
            struct_short_high_prev = np.concatenate([struct_short_high_prev, np.full(pad, h_prev[-1])])
            struct_short_low_prev = np.concatenate([struct_short_low_prev, np.full(pad, l_prev[-1])])

    # Use crypto walker but inject stocks commission via monkey patch
    orig_comm = _crypto.COMMISSION_RT_PCT
    _crypto.COMMISSION_RT_PCT = COMMISSION_RT_PCT_STOCKS
    try:
        trades = walk_trades_dual(
            enter_long, leave_long, enter_short, leave_short, c15, ts15,
            int(params.MIN_HOLD_BARS_15m), int(params.COOLDOWN_BARS_15m),
            wt1_15m=wt1_15m_arr, wt2_15m=wt2_15m_arr,
            wt1_3m_at_15m=wt1_at_15m, wt2_3m_at_15m=wt2_at_15m,
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
            struct_long_open=struct_long_open,
            struct_long_high_prev=struct_long_high_prev,
            struct_long_low_prev=struct_long_low_prev,
            struct_short_open=struct_short_open,
            struct_short_high_prev=struct_short_high_prev,
            struct_short_low_prev=struct_short_low_prev,
        )
    finally:
        _crypto.COMMISSION_RT_PCT = orig_comm

    span_days = max(1.0, (ts15[-1] - ts15[0]) / 86400.0)
    yrs = max(0.01, span_days / 365.25)
    bh_pct = float((c15[-1] / c15[0] - 1.0) * 100.0) if c15[0] > 0 else 0.0
    if not trades:
        return {'sym': sym, 'trades': 0, 'trades_per_day': 0.0, 'pool_sharpe': 0.0,
                'sym_sharpe': 0.0, 'wr_pct': 0.0, 'max_dd_pct': 0.0,
                'total_gain_pct': 0.0, 'avg_gain_trade': 0.0, 'gain_per_yr': 0.0,
                'gain_per_week': 0.0, 'bh_pct_window': bh_pct,
                'gain_sym_yr': 0.0, 'years': yrs, 'n_syms': 1,
                'tag': f'per_sym_stocks_dual_{sym}', 'trade_list': [],
                'params': params.to_dict(), 'long_trades': 0, 'short_trades': 0}
    # MTM tagging
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
    weeks = max(1.0 / 7.0, span_days / 7.0)
    return {
        'sym': sym, 'trades': n, 'trades_per_day': n / span_days,
        'pool_sharpe': pool, 'sym_sharpe': max(-5.0, min(5.0, pool)),
        'wr_pct': wr, 'max_dd_pct': dd, 'total_gain_pct': total,
        'avg_gain_trade': total / n, 'gain_per_yr': total / yrs, 'gain_sym_yr': total / yrs,
        'gain_per_week': total / weeks, 'bh_pct_window': bh_pct,
        'years': yrs, 'n_syms': 1, 'tag': f'per_sym_stocks_dual_{sym}',
        'trade_list': trades, 'params': params.to_dict(),
        'long_trades': n_long, 'short_trades': n_short,
        'augment_count': sum(1 for t in trades if (t.get('origin') or '').startswith('augment_')),
        'hedge_wt3m_count': sum(1 for t in trades if (t.get('origin') or '').startswith('hedge_wt')),
        'peak_protect_count': sum(1 for t in trades if t.get('origin') == 'peak_protect_wt15m'),
        'mean_rev_reentry_count': sum(1 for t in trades if t.get('origin') == 'mean_rev_reentry'),
        'follow_through_count': sum(1 for t in trades if t.get('origin') == 'follow_through'),
        'reverse_on_exit_count': sum(1 for t in trades if t.get('origin') == 'reverse_on_exit'),
        'hard_loss_count': sum(1 for t in trades if t.get('origin') == 'hard_loss_pct'),
        'peak_giveback_fixed_count': sum(1 for t in trades if t.get('origin') == 'peak_giveback_fixed'),
        'open_at_end_count': len(open_at_end),
        'mtm_pnl_open_pct': float(sum(t['pnl_pct'] for t in open_at_end)),
    }


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--syms', default='AAPL,MSFT,NVDA')
    args = ap.parse_args()
    print(f"smoke test stocks engine on {args.syms}")
    for s in [x.strip() for x in args.syms.split(',') if x.strip()]:
        p = SymParamsStocks(); p.USE_V8_AGGREGATORS = False
        r = simulate_dual_stocks(s, p, years_back=4.0)
        if r is None:
            print(f"  {s}: NPZ MISS or insufficient data"); continue
        print(f"  {s:8s} pool={r['pool_sharpe']:+.4f} wr={r['wr_pct']:5.1f}% tpd={r['trades_per_day']:5.2f} tr={r['trades']:>5d} dd={r['max_dd_pct']:5.2f}")
