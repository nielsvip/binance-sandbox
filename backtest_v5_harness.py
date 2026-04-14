#!/usr/bin/env python3
"""
V5 Harness — Mock infrastructure that lets actual trading functions run against .npz data.

This module provides mock implementations of:
- RedisManager (returns indicator data from .npz arrays)
- FastDataManager (get_hot_state returns .npz snapshot)
- MultiAccountTradeManager (tracks positions, prices, min_qty)
- TrackerManager (position check tracking)
- OrderQueue (captures trade actions for logging)
- HedgeEngine (full hedge tracking)
- TradingPolicy (entry/exit gates)

Usage:
    from backtest_v5_harness import BacktestHarness
    harness = BacktestHarness(npz_dir, mode="crypto")
    harness.load_symbols(["BTCUSDT", "ETHUSDT"])
    harness.step_to(bar_idx)  # advances time, updates all mocks
"""

import asyncio
import os
import platform
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger("v5_harness")

if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance-sandbox")


# ---------------------------------------------------------------------------
# Indicator Store (same as v5_simulate)
# ---------------------------------------------------------------------------
class _TsToIdx:
    """Dict-like timestamp→index lookup using numpy searchsorted. No dict = no memory bloat."""
    __slots__ = ('_ts', '_n')
    def __init__(self, ts_array):
        self._ts = ts_array
        self._n = len(ts_array)
    def get(self, ts, default=-1):
        idx = int(np.searchsorted(self._ts, ts))
        if idx < self._n and self._ts[idx] == ts:
            return idx
        return default
    def __contains__(self, ts):
        idx = int(np.searchsorted(self._ts, ts))
        return idx < self._n and self._ts[idx] == ts
    def __getitem__(self, ts):
        idx = int(np.searchsorted(self._ts, ts))
        if idx < self._n and self._ts[idx] == ts:
            return idx
        return -1


class IndicatorStore:
    def __init__(self, npz_path: str):
        data = np.load(npz_path, allow_pickle=True)
        # Only load timestamps + detect timeframes into RAM. Keep npz ref for lazy array access.
        self._npz_path = npz_path
        self._keys = list(data.files)
        self.timestamps = data["timestamps"].astype(np.int64)
        self.n_bars = len(self.timestamps)
        self.has_5m = any(k.endswith("_5m") for k in self._keys)
        self.has_3m = any(k.endswith("_3m") for k in self._keys)
        # Load arrays — for files < 100MB load all, for larger use lazy access
        file_size = os.path.getsize(npz_path) if os.path.exists(npz_path) else 0
        if file_size < 100_000_000:  # < 100MB: load all (15m files)
            self.arrays: Dict[str, np.ndarray] = {k: data[k] for k in data.files}
            data.close()
            self._npz = None
        else:  # >= 100MB: keep npz open for lazy access (3m/5m files)
            self.arrays = {"timestamps": self.timestamps, "close": data["close"].copy()}
            self._npz = data  # keep open — arrays accessed lazily via get()
        self.ts_to_idx = _TsToIdx(self.timestamps)
        # Forward-fill sparse HTF indicators (1h/4h mapped to 15m/3m have 89% zeros)
        _ffill_suffixes = ("_1h", "_4h", "_D")
        _skip_prefixes = ("open_", "high_", "low_", "close_", "volume_", "timestamps")
        for key in list(self.arrays.keys()):
            if not any(key.endswith(s) for s in _ffill_suffixes):
                continue
            if any(key.startswith(s) for s in _skip_prefixes):
                continue
            arr = self.arrays[key]
            if arr.dtype.kind not in ('f', 'i'):
                continue
            farr = arr.astype(np.float64)
            mask = (farr == 0) | np.isnan(farr)
            if mask.all() or not mask.any():
                continue
            # Forward-fill: replace 0/NaN with last non-zero value
            last_val = 0.0
            for i in range(len(farr)):
                if not mask[i]:
                    last_val = farr[i]
                elif last_val != 0.0:
                    farr[i] = last_val
            self.arrays[key] = farr.astype(arr.dtype)

    def get(self, key: str, idx: int, default: float = 0.0) -> float:
        arr = self.arrays.get(key)
        if arr is None and self._npz is not None and key in self._keys:
            arr = self._npz[key]
            self.arrays[key] = arr  # cache after first access
        if arr is None:
            return default
        if idx < 0 or idx >= len(arr):
            return default
        v = float(arr[idx])
        if np.isnan(v):
            return default
        return v

    def get_int(self, key: str, idx: int, default: int = 0) -> int:
        return int(self.get(key, idx, float(default)))

    def price(self, idx: int) -> float:
        return self.get("close", idx)

    # --- WT int8 → string maps (match ez_indicators.py wavetrend_intelligence output) ---
    _WT_CROSS_MAP = {-1: "bear_cross", 0: "none", 1: "bull_cross"}
    _WT_MOMENTUM_MAP = {-2: "strong_bearish", -1: "bearish", 0: "neutral", 1: "bullish", 2: "strong_bullish"}
    _WT_STRUCTURE_MAP = {-1: "lower", 0: "none", 1: "higher"}
    _HA_MAP = {-1: "red", 0: "neutral", 1: "green"}

    def build_indicator_dict(self, idx: int) -> Dict[str, Any]:
        """Build the EXACT indicator dict that live code expects from Redis/bridge."""
        result = {}
        for key in self.arrays:
            if key == "timestamps":
                continue
            val = self.get(key, idx)
            result[key] = val
        # Add derived fields that live code expects
        p = self.price(idx)
        result["current_price"] = p
        result["mark_price"] = p
        result["prev_price"] = self.get("close", max(0, idx - 1))
        # CRITICAL: Derive _prev fields from shifted arrays where NPZ doesn't store them
        # IBS exit needs close_5m_prev, close_3m_prev, close_15m_prev
        for tf in ["3m", "5m", "15m", "1h", "4h", "D"]:
            cpk = f"close_{tf}_prev"
            if cpk not in result and f"close_{tf}" in self.arrays:
                result[cpk] = self.get(f"close_{tf}", max(0, idx - 1))
        ts = int(self.timestamps[idx]) if idx < self.n_bars else 0
        result["_tick_ts"] = float(ts)
        result["ts"] = float(ts)
        result["timestamp"] = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        # --- CRITICAL: Convert int8 WT fields to string values that live code expects ---
        for tf in ["3m", "5m", "15m", "1h", "4h", "D"]:
            # wt_cross_{tf}: int8 → "bull_cross"/"bear_cross"/"none"
            wc_key = f"wt_cross_{tf}"
            if wc_key in result:
                result[wc_key] = self._WT_CROSS_MAP.get(int(result[wc_key]), "none")
            # wt_momentum_state_{tf}: int8 → "strong_bullish"/"bullish"/"neutral"/"bearish"/"strong_bearish"
            wm_key = f"wt_momentum_state_{tf}"
            if wm_key in result:
                result[wm_key] = self._WT_MOMENTUM_MAP.get(int(result[wm_key]), "neutral")
            # wt_peak_structure_{tf}: int8 → "higher"/"lower"/"none"
            wp_key = f"wt_peak_structure_{tf}"
            if wp_key in result:
                result[wp_key] = self._WT_STRUCTURE_MAP.get(int(result[wp_key]), "none")
            # wt_trough_structure_{tf}: int8 → "higher"/"lower"/"none"
            wt_key = f"wt_trough_structure_{tf}"
            if wt_key in result:
                result[wt_key] = self._WT_STRUCTURE_MAP.get(int(result[wt_key]), "none")
            # wt_signal_{tf}: live reads this, NPZ doesn't store it — derive from cross state
            ws_key = f"wt_signal_{tf}"
            if ws_key not in result:
                cross_val = result.get(wc_key, "none")
                if cross_val == "bull_cross":
                    result[ws_key] = "BUY"
                elif cross_val == "bear_cross":
                    result[ws_key] = "SELL"
                else:
                    result[ws_key] = "NEUTRAL"
            # wt_cross_value_{tf}, wt_cross_prev_value_{tf}: derive from wt1/wt2
            if f"wt_cross_value_{tf}" not in result:
                result[f"wt_cross_value_{tf}"] = result.get(f"wt1_{tf}", 0)
                result[f"wt_cross_prev_value_{tf}"] = result.get(f"wt2_{tf}", 0)
            # wt_cross_rising_{tf}: wt1 rising above wt2
            if f"wt_cross_rising_{tf}" not in result:
                wt1 = result.get(f"wt1_{tf}", 0)
                wt2 = result.get(f"wt2_{tf}", 0)
                result[f"wt_cross_rising_{tf}"] = wt1 > wt2
            # wt_cross_bars_ago_{tf}: default to 10 (recent but not immediate)
            if f"wt_cross_bars_ago_{tf}" not in result:
                cross_bull = result.get(f"wt_cross_bull_{tf}", 0)
                cross_bear = result.get(f"wt_cross_bear_{tf}", 0)
                result[f"wt_cross_bars_ago_{tf}"] = 0 if (cross_bull or cross_bear) else 10
            # wt_divergence_{tf}: not in NPZ, default False
            if f"wt_divergence_{tf}" not in result:
                result[f"wt_divergence_{tf}"] = False
            # wt_velocity_scalp alias (rate() reads this for 3m exit velocity)
            if tf == "3m" and "wt_velocity_scalp" not in result:
                result["wt_velocity_scalp"] = result.get("wt_velocity_3m", 0)
        # --- WT composite fields that live code reads but NPZ may not store ---
        result.setdefault("wt_composite_long", 0.0)
        result.setdefault("wt_composite_short", 0.0)
        result.setdefault("wt_composite_delta", 0.0)
        result.setdefault("wt_bull_alignment", 0)
        result.setdefault("wt_bear_alignment", 0)
        # Additional WT composite fields live code checks (from wt_composite.py)
        bull_align = int(result.get("wt_bull_alignment", 0))
        bear_align = int(result.get("wt_bear_alignment", 0))
        result.setdefault("wt_hl_count", 0)
        result.setdefault("wt_lh_count", 0)
        result.setdefault("wt_oversold_tf_count", sum(1 for tf in ["3m", "15m", "1h", "4h", "D"] if result.get(f"wt1_{tf}", 0) < -53))
        result.setdefault("wt_overbought_tf_count", sum(1 for tf in ["3m", "15m", "1h", "4h", "D"] if result.get(f"wt1_{tf}", 0) > 53))
        result.setdefault("wt_bull_cross_count", sum(1 for tf in ["3m", "15m", "1h", "4h", "D"] if result.get(f"wt_cross_bull_{tf}", 0)))
        result.setdefault("wt_bear_cross_count", sum(1 for tf in ["3m", "15m", "1h", "4h", "D"] if result.get(f"wt_cross_bear_{tf}", 0)))
        result.setdefault("wt_any_bull_div", False)
        result.setdefault("wt_any_bear_div", False)
        result.setdefault("wt_composite_bias", "NEUTRAL")
        # Add conviction fields (derived from composite scores)
        comp_long = result.get("wt_composite_long", 0)
        comp_short = result.get("wt_composite_short", 0)
        result["zconviction_long"] = max(0, comp_long)
        result["zconviction_short"] = max(0, comp_short)
        result["zconviction_augment_long"] = max(0, comp_long * 0.8)
        result["zconviction_augment_short"] = max(0, comp_short * 0.8)
        result["zconviction_reasons_augment_long"] = []
        result["zconviction_reasons_augment_short"] = []
        # Derive 1m from 3m/5m if missing (rater checks k_1m for DATA_BLIND)
        stf = "3m" if "stoch_k_3m" in result else ("5m" if "stoch_k_5m" in result else None)
        if stf and "stoch_k_1m" not in result:
            result["stoch_k_1m"] = result.get(f"stoch_k_{stf}", 50)
            result["stoch_d_1m"] = result.get(f"stoch_d_{stf}", 50)
            result["k_1m_prev"] = result.get(f"k_{stf}_prev", 50)
            result["d_1m_prev"] = result.get(f"d_{stf}_prev", 50)
        # CRITICAL: Set timestamp fields to SIMULATION time (not wall clock!)
        # Live code checks datetime.now() vs indicator timestamps for staleness.
        # In backtest, we set timestamps to the bar's time so staleness gates pass correctly.
        _bar_ts_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        for tf in ["3m", "5m", "15m", "1h", "4h", "D"]:
            result[f"timestamp_{tf}"] = _bar_ts_str
        result["timestamp"] = _bar_ts_str
        # Map 5m<->3m for compatibility (live code may ask for either)
        stf = "5m" if self.has_5m else "3m"
        otf = "3m" if self.has_5m else "5m"
        for key in list(result.keys()):
            if key.endswith(f"_{stf}"):
                alias = key.replace(f"_{stf}", f"_{otf}")
                if alias not in result:
                    result[alias] = result[key]
        # Ensure common aliases (both k_{tf}_prev AND stoch_k_{tf}_prev)
        for tf in ["1m", "3m", "5m", "15m", "1h", "4h", "D"]:
            for prefix in ["stoch_k", "stoch_d"]:
                long_key = f"{prefix}_{tf}"
                short_prefix = "k" if prefix == "stoch_k" else "d"
                if long_key in result:
                    prev_key = f"{short_prefix}_{tf}_prev"
                    stoch_prev_key = f"{prefix}_{tf}_prev"
                    if prev_key not in result:
                        result[prev_key] = self.get(prev_key, idx, 50.0)
                    # Live code reads BOTH k_{tf}_prev AND stoch_k_{tf}_prev
                    if stoch_prev_key not in result:
                        result[stoch_prev_key] = result[prev_key]
        # HA string values (live code uses "green"/"red" strings)
        for tf in ["3m", "5m", "15m", "1h", "4h", "D"]:
            ha_key = f"ha_{tf}"
            if ha_key in result:
                v = result[ha_key]
                if isinstance(v, (int, float)):
                    result[ha_key] = self._HA_MAP.get(int(v), "neutral")
            # ha_color_{tf} alias (some live code paths read this)
            ha_color_key = f"ha_color_{tf}"
            if ha_color_key not in result and ha_key in result:
                result[ha_color_key] = result[ha_key]
            # ha_{tf}_prev: derive from previous bar if not in NPZ
            ha_prev_key = f"ha_{tf}_prev"
            if ha_prev_key not in result and idx > 0:
                prev_val = self.get_int(ha_key, idx - 1, 0)
                result[ha_prev_key] = self._HA_MAP.get(prev_val, "neutral")
        # --- Missing indicator fields that live code reads ---
        # MFI: NPZ has 15m/1h/4h/D after BC_155 precompute update. Fallback for old NPZs.
        for tf in ["3m", "5m"]:
            result.setdefault(f"mfi_{tf}", 50.0)
        # MFI 4h/D: present in new NPZs, fallback to 1h for old NPZs
        if "mfi_4h" not in result:
            result["mfi_4h"] = result.get("mfi_1h", 50.0)
        if "mfi_D" not in result:
            result["mfi_D"] = result.get("mfi_1h", 50.0)
        # Choppiness: present in new NPZs (1h/4h), fallback for old NPZs
        for tf in ["1h", "4h"]:
            result.setdefault(f"choppiness_{tf}", 50.0)
        # ATR prev: derive from previous bar
        for tf in ["3m", "5m", "15m", "1h", "4h", "D"]:
            atr_prev_key = f"atr_{tf}_prev"
            if atr_prev_key not in result and idx > 0:
                result[atr_prev_key] = self.get(f"atr_{tf}", idx - 1, 0.0)
        # dc_high4/dc_low4 (4-bar Donchian) not in NPZ — approximate from 3m DC
        for tf in ["3m", "5m"]:
            if f"dc_high4_{tf}" not in result:
                result[f"dc_high4_{tf}"] = result.get(f"dc_high_{tf}", p)
            if f"dc_low4_{tf}" not in result:
                result[f"dc_low4_{tf}"] = result.get(f"dc_low_{tf}", p)
        # bar_atr_rank_{tf}: not in NPZ, default 50 (median)
        for tf in ["1h", "4h", "D"]:
            result.setdefault(f"bar_atr_rank_{tf}", 50.0)
        # relative_volume defaults (present in NPZ for most TFs but ensure)
        for tf in ["3m", "5m", "15m", "1h", "4h", "D"]:
            result.setdefault(f"relative_volume_{tf}", 1.0)
        # Cross-TF DC fields used by analyze_multi_tf_state()
        for tf in ["15m", "1h", "4h", "D"]:
            result.setdefault(f"0dc_high_{tf}", result.get(f"dc_high_{tf}", p))
            result.setdefault(f"0dc_low_{tf}", result.get(f"dc_low_{tf}", p))
            result.setdefault(f"0dc_basis_{tf}", result.get(f"dc_basis_{tf}", p))
            result.setdefault(f"0dc_position_{tf}", result.get(f"dc_position_{tf}", 0.5))
            result.setdefault(f"0dc_width_{tf}", result.get(f"dc_width_{tf}", 0.0))
        # EMA fields that live code reads
        for tf in ["15m", "1h", "4h"]:
            for length in [9, 14, 20, 50]:
                result.setdefault(f"ema_{length}_{tf}", p)
        # SMA 500 (used for SMA500_DEEP scoring)
        result.setdefault("sma_500_1h", result.get("sma_200_1h", p))
        # lr_pct_b (linear regression percent band) — live reads these
        for tf in ["15m", "1h", "4h"]:
            result.setdefault(f"lr_pct_b_{tf}", 0.5)
        # candle_body_ratio, bar_pattern — live reads but not critical
        for tf in ["3m", "5m", "15m", "1h"]:
            result.setdefault(f"candle_body_ratio_{tf}", 0.5)
            result.setdefault(f"bar_pattern_{tf}", "none")
        # Sentiment/ranking fields that rate() reads — neutral defaults
        result.setdefault("0market_sentiment_score", 0.0)
        result.setdefault("0market_sentiment_local", 0.0)
        result.setdefault("0sentiment_classification", "NEUTRAL")
        result.setdefault("0is_top_sentiment", False)
        result.setdefault("0is_bottom_sentiment", False)
        result.setdefault("0ranking_points", 0.0)
        result.setdefault("0ranking_rank", 0)
        return result


# ---------------------------------------------------------------------------
# Mock Position (matches live Position objects)
# ---------------------------------------------------------------------------
@dataclass
class MockPosition:
    symbol: str = ""
    positionAmt: float = 0.0
    entryPrice: float = 0.0
    markPrice: float = 0.0
    unRealizedProfit: float = 0.0
    leverage: int = 1
    notional: float = 0.0
    isolatedMargin: float = 0.0
    side: str = ""  # "LONG" or "SHORT"
    position_key: str = ""
    opened_at: float = 0.0
    max_gain: float = 0.0
    max_gain_price: float = 0.0
    first_gain_ts: float = 0.0
    last_augment_ts: float = 0.0
    augment_count: int = 0
    last_reduce_ts: float = 0.0
    last_reduce_price: float = 0.0
    total_reduced_qty: float = 0.0
    was_reduced: bool = False
    # Hedge fields
    is_hedge: bool = False
    hedge_for: str = ""
    has_hedge: str = ""
    last_hedge_ts: float = 0.0
    # Extra state
    entry_reason: str = ""
    cumulative_pnl: float = 0.0

    @property
    def is_long(self) -> bool:
        return self.side == "LONG" or self.position_key.endswith("_LONG")

    def gain_pct(self, price: float = None) -> float:
        p = price or self.markPrice
        if self.entryPrice <= 0 or p <= 0:
            return 0.0
        if self.is_long:
            return (p - self.entryPrice) / self.entryPrice * 100
        return (self.entryPrice - p) / self.entryPrice * 100


# ---------------------------------------------------------------------------
# Mock Trade Manager
# ---------------------------------------------------------------------------
class MockTradeManager:
    """Mimics MultiAccountTradeManager for the evaluate functions."""

    def __init__(self):
        self.positions: Dict[str, MockPosition] = {}
        self.positions_by_account: Dict[str, Dict[str, MockPosition]] = {}
        self.price_cache: Dict[str, float] = {}
        self.min_qty: Dict[str, float] = {"default": 0.001}
        self.hot_data_cache: Dict[str, Dict] = {}
        self.indicator_cache: Dict[str, Dict] = {}
        self.accounts: Dict[str, Any] = {}
        self.order_deduplication: Dict[str, float] = {}
        self.non_shortable_symbols: set = set()
        self.market_snapshot: Dict[str, Any] = {}
        # Trade log
        self.executed_trades: List[Dict] = []

    def get_position(self, position_key: str) -> Optional[MockPosition]:
        return self.positions.get(position_key)

    def get_current_price(self, symbol: str) -> Tuple[float, datetime]:
        price = self.price_cache.get(symbol, 0.0)
        return price, datetime.utcnow()

    def is_symbol_tradeable(self, symbol: str) -> bool:
        return True

    def is_symbol_allowed(self, symbol: str, account_key: str = "") -> bool:
        return True

    def validate_order_side(self, position_key: str, action: str) -> bool:
        return True

    def is_duplicate_order(self, position_key: str, action: str) -> bool:
        return False

    def get_current_portfolio_balance(self, account_key: str = "") -> Dict:
        long_val = sum(abs(p.positionAmt * p.markPrice) for p in self.positions.values() if p.is_long)
        short_val = sum(abs(p.positionAmt * p.markPrice) for p in self.positions.values() if not p.is_long)
        total = long_val + short_val
        return {"long_pct": long_val / max(1, total) * 100, "short_pct": short_val / max(1, total) * 100, "long_value": long_val, "short_value": short_val}

    def update_price(self, symbol: str, price: float):
        self.price_cache[symbol] = price
        # Update all positions for this symbol
        for pk, pos in self.positions.items():
            if pos.symbol == symbol:
                pos.markPrice = price
                gain = pos.gain_pct(price)
                pos.unRealizedProfit = pos.positionAmt * price * gain / 100
                if gain > pos.max_gain:
                    pos.max_gain = gain
                    pos.max_gain_price = price

    def update_indicators(self, symbol: str, indicators: Dict):
        self.indicator_cache[symbol] = indicators
        self.hot_data_cache[symbol] = indicators


# ---------------------------------------------------------------------------
# Mock Data Manager
# ---------------------------------------------------------------------------
class MockDataManager:
    """Mimics FastDataManager."""

    def __init__(self, trade_manager: MockTradeManager):
        self.tm = trade_manager

    def get_hot_state(self, symbol: str):
        ind = self.tm.indicator_cache.get(symbol, {})
        if not ind:
            return {}, {}, 50, 50, 50, 50, False
        k_1m = ind.get("stoch_k_1m", ind.get("stoch_k_3m", ind.get("stoch_k_5m", 50)))
        d_1m = ind.get("stoch_d_1m", ind.get("stoch_d_3m", ind.get("stoch_d_5m", 50)))
        k_3m = ind.get("stoch_k_3m", ind.get("stoch_k_5m", 50))
        d_3m = ind.get("stoch_d_3m", ind.get("stoch_d_5m", 50))
        return ind, ind, k_1m, d_1m, k_3m, d_3m, True

    def get_fresh_price(self, symbol: str):
        price = self.tm.price_cache.get(symbol, 0.0)
        return price, datetime.utcnow()


# ---------------------------------------------------------------------------
# Mock Tracker Manager
# ---------------------------------------------------------------------------
class MockTrackerManager:
    def __init__(self):
        self._last_check: Dict[str, float] = {}
        self._processing: Dict[str, bool] = {}
        self._positions: Dict[str, Any] = {}
        self.exit_candidates: Dict[str, Dict] = {}
        self.entry_candidates: Dict[str, Dict] = {}
        class _MockPosService:
            positions = {}
            positions_by_account = {}
            def get_long_short_ratio(self, account_key=""):
                longs = sum(1 for p in self.positions.values() if hasattr(p, 'is_long') and p.is_long)
                shorts = len(self.positions) - longs
                return {"long": longs, "short": shorts, "ratio": longs / max(1, shorts)}
            def get_account_positions(self, account_key=""):
                return self.positions_by_account.get(account_key, self.positions)
        self.positions_service = _MockPosService()
        self.positions = {}  # alias
        self.active_hedges: List[Dict] = []
        self.boycott_symbols: set = set()
        self.open_blocked: Dict[str, float] = {}
        self.tradeable_keys: set = set()
        self.tradeable_keys_cache: set = set()
        self.tradeable_position_keys: Dict[str, set] = {}
        self.disabled_entries: Dict[str, bool] = {}
        self.reduced_positions: Dict[str, Dict] = {}
        self.reentry_data: Dict[str, Dict] = {}
        self.hedge_candidates: Dict[str, Dict] = {}
        self.scalp_positions: Dict[str, Dict] = {}
        self.accounts: Dict[str, Any] = {}
        self.account_keys: List[str] = ["ang"]
        self.max_concurrent_hedges: int = 10
        self.hedge_mode_enabled: bool = True
        self.last_check_times: Dict[str, float] = {}
        self._processing_orders: Dict[str, float] = {}
        self.hedge_liability_cooldowns: Dict[str, float] = {}
        self.last_exit_prices: Dict[str, float] = {}
        self.last_exit_times: Dict[str, float] = {}
        self.restored_positions: set = set()
        try:
            self._hedges_lock = asyncio.Lock()
        except RuntimeError:
            self._hedges_lock = None  # no event loop yet
        self.registry = type("R", (), {"get": lambda s, *a: None, "set": lambda s, *a: None})()
        # Aliases used by server code
        self.last_check_times = self._last_check

    def _entry_template(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {'status': 'entry_candidate', 'timestamp': now, 'entry_price': 0.0, 'exit_price': 0.0, 'average_entry_price': 0.0, 'average_exit_price': 0.0, 'average_pos_value': 0.0, 'current_pos_value': 0.0, 'total_entry_qty': 0.0, 'positionAmt': 0.0, 'first_entry_price': 0.0, 'mark_price': 0.0, 'mark_price_last_updated': None, 'gain_list': [], 'gain_dollar_list': [], 'current_gain_%': 0.0, 'max_gain': 0.0, 'total_realized_pnl_$': 0.0, 'winning_trades': 0, 'losing_trades': 0, 'total_trades': 0, 'win_rate_%': 0.0, 'consecutive_wins': 0, 'consecutive_losses': 0, 'last_augmentation_amount': 0.0, 'last_augmentation_price': 0.0, 'last_augmentation_time': None, 'last_reduction_price': 0.0, 'last_reduction_amount': 0.0, 'last_reduction_time': None, 'is_reduced': False, 'was_reduced': False, 'reduced_at': None, 'was_reentered': False, 'last_exit_reentry_ready': False, 'last_exit_timestamp': None, 'unrealized_pnl_%': 0.0, 'unrealized_pnl_$': 0.0, 'total_pnl_$': 0.0, 'opened_at': None, 'last_updated': None, 'last_reason': '', 'augment_reason': '', 'reduction_reason': '', 'trade_log': []}

    def _exit_template(self, entry_price: float = 0.0, qty: float = 0.0) -> Dict[str, Any]:
        tpl = self._entry_template()
        tpl['status'] = 'active'
        if entry_price > 0:
            tpl['entry_price'] = entry_price
            tpl['average_entry_price'] = entry_price
        if qty > 0:
            tpl['positionAmt'] = qty
        return tpl

    async def get_position(self, pk):
        return self._positions.get(pk)

    def set_position_ref(self, pk, pos):
        self._positions[pk] = pos

    async def get_exit_candidate(self, pk):
        return self.exit_candidates.get(pk)

    async def get_entry_candidate(self, pk):
        return self.entry_candidates.get(pk)

    def get_last_check_time(self, pk):
        return self._last_check.get(pk, 0.0)

    async def is_processing(self, pk):
        return self._processing.get(pk, False)

    async def set_processing(self, pk):
        self._processing[pk] = True

    async def clear_processing(self, pk):
        self._processing[pk] = False

    async def register_check(self, pk):
        import time as _t
        self._last_check[pk] = _t.time()

    async def transition_to_entry(self, account_key, pk, price=0, size=0, is_long=True, status="entry_candidate", **kw):
        self.entry_candidates[pk] = self._entry_template()
        self.entry_candidates[pk]["status"] = status
        self.entry_candidates[pk]["entry_price"] = price

    async def transition_to_exit(self, account_key, pk, price=0, size=0, status="active", is_hedge=False, hedge_for=None, **kw):
        self.exit_candidates[pk] = self._exit_template(price, size)

    async def save_tracker(self, account_key="", force=False):
        pass

    async def send_webhook(self, pk="", amount=0, side="", price=0, qty=0, is_full_close=False, reason="", **kw):
        return True

    async def set_trade_cooldown(self, pk, duration=5.0):
        pass

    def is_system_lagging(self, account_key=""):
        return False

    # No __getattr__ — explicitly define everything needed


# ---------------------------------------------------------------------------
# Mock Order Queue
# ---------------------------------------------------------------------------
class MockOrderQueue:
    """Captures trade actions instead of sending them to exchange."""

    def __init__(self):
        self.actions: List[Dict] = []

    async def put(self, action: Dict):
        self.actions.append(action)

    def drain(self) -> List[Dict]:
        actions = self.actions.copy()
        self.actions.clear()
        return actions


# ---------------------------------------------------------------------------
# Mock Redis Manager
# ---------------------------------------------------------------------------
class MockRedisManager:
    def __init__(self):
        self.data: Dict[str, Any] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: Any, ex: int = None):
        self.data[key] = value

    async def hgetall(self, key: str):
        return self.data.get(key, {})

    async def publish(self, channel: str, data: str):
        pass


# ---------------------------------------------------------------------------
# Mock Hedge Engine
# ---------------------------------------------------------------------------
class MockHedgeEngine:
    """Tracks hedge relationships between positions."""

    def __init__(self, trade_manager: MockTradeManager):
        self.tm = trade_manager
        self.active_hedges: Dict[str, str] = {}  # original_pk -> hedge_pk
        self.hedge_log: List[Dict] = []

    def has_hedge(self, pk: str) -> bool:
        return pk in self.active_hedges

    def get_hedge_pk(self, pk: str) -> Optional[str]:
        return self.active_hedges.get(pk)

    def register_hedge(self, original_pk: str, hedge_pk: str):
        self.active_hedges[original_pk] = hedge_pk
        orig = self.tm.positions.get(original_pk)
        hedge = self.tm.positions.get(hedge_pk)
        if orig:
            orig.has_hedge = hedge_pk
        if hedge:
            hedge.is_hedge = True
            hedge.hedge_for = original_pk

    def check_promotions(self) -> List[Tuple[str, str, float]]:
        """Returns list of (original_pk, hedge_pk, net_pnl) where hedge covers original."""
        promotions = []
        for orig_pk, hedge_pk in list(self.active_hedges.items()):
            orig = self.tm.positions.get(orig_pk)
            hedge = self.tm.positions.get(hedge_pk)
            if orig is None or hedge is None:
                continue
            hedge_gain = hedge.gain_pct()
            orig_gain = orig.gain_pct()
            hedge_pnl = abs(hedge.positionAmt) * hedge.markPrice * hedge_gain / 100
            orig_pnl = abs(orig.positionAmt) * orig.markPrice * orig_gain / 100
            if hedge_pnl > 0 and hedge_pnl >= abs(min(0, orig_pnl)) * 0.8:
                net = hedge_pnl + orig_pnl
                promotions.append((orig_pk, hedge_pk, net))
        return promotions

    def close_hedge_pair(self, orig_pk: str, hedge_pk: str):
        self.active_hedges.pop(orig_pk, None)


# ---------------------------------------------------------------------------
# Harness (ties everything together)
# ---------------------------------------------------------------------------
class BacktestHarness:
    """Main harness that manages all mocks and feeds .npz data."""

    def __init__(self, npz_dir, mode: str = "crypto"):
        self.npz_dir = Path(npz_dir) if not isinstance(npz_dir, Path) else npz_dir
        self.mode = mode
        self.stores: Dict[str, IndicatorStore] = {}
        self.trade_manager = MockTradeManager()
        self.data_manager = MockDataManager(self.trade_manager)
        self.tracker_manager = MockTrackerManager()
        self.order_queue = MockOrderQueue()
        self.redis_manager = MockRedisManager()
        self.hedge_engine = MockHedgeEngine(self.trade_manager)
        self.current_ts: int = 0
        self.current_bar_indices: Dict[str, int] = {}

    def load_symbols(self, symbols: List[str]):
        for sym in symbols:
            path = self.npz_dir / f"{sym}.npz"
            if path.exists():
                self.stores[sym] = IndicatorStore(str(path))
                self.trade_manager.min_qty[sym] = 0.001 if self.mode == "crypto" else 1.0

    def step_to(self, ts: int):
        """Advance all mocks to timestamp ts."""
        self.current_ts = ts
        for sym, store in self.stores.items():
            idx = store.ts_to_idx.get(ts, -1)
            if idx < 0:
                continue
            self.current_bar_indices[sym] = idx
            price = store.price(idx)
            if price > 0:
                self.trade_manager.update_price(sym, price)
                indicators = store.build_indicator_dict(idx)
                self.trade_manager.update_indicators(sym, indicators)

    def get_indicators(self, symbol: str) -> Dict:
        return self.trade_manager.indicator_cache.get(symbol, {})

    def get_price(self, symbol: str) -> float:
        return self.trade_manager.price_cache.get(symbol, 0.0)

    def get_bar_idx(self, symbol: str) -> int:
        return self.current_bar_indices.get(symbol, -1)

    def open_position(self, symbol: str, side: str, qty: float, reason: str, ts: int, bar_idx: int) -> MockPosition:
        pk = f"{symbol}_{side}"
        price = self.get_price(symbol)
        pos = MockPosition(symbol=symbol, positionAmt=qty, entryPrice=price, markPrice=price, side=side, position_key=pk, opened_at=float(ts), entry_reason=reason)
        self.trade_manager.positions[pk] = pos
        return pos

    def close_position(self, pk: str) -> Optional[MockPosition]:
        return self.trade_manager.positions.pop(pk, None)

    def reduce_position(self, pk: str, reduce_qty: float, price: float):
        pos = self.trade_manager.positions.get(pk)
        if pos:
            pos.positionAmt -= reduce_qty
            pos.was_reduced = True
            pos.last_reduce_price = price
            pos.last_reduce_ts = float(self.current_ts)
            pos.total_reduced_qty += reduce_qty

    def augment_position(self, pk: str, add_qty: float, price: float):
        pos = self.trade_manager.positions.get(pk)
        if pos:
            total_qty = pos.positionAmt + add_qty
            pos.entryPrice = (pos.entryPrice * pos.positionAmt + price * add_qty) / total_qty
            pos.positionAmt = total_qty
            pos.augment_count += 1
            pos.last_augment_ts = float(self.current_ts)

    def open_hedge(self, original_pk: str, hedge_qty: float, reason: str, ts: int, bar_idx: int) -> Optional[MockPosition]:
        orig = self.trade_manager.positions.get(original_pk)
        if orig is None:
            return None
        hedge_side = "SHORT" if orig.is_long else "LONG"
        hedge_pk = f"{orig.symbol}_{hedge_side}"
        if hedge_pk in self.trade_manager.positions:
            return None
        price = self.get_price(orig.symbol)
        hedge_pos = MockPosition(symbol=orig.symbol, positionAmt=hedge_qty, entryPrice=price, markPrice=price, side=hedge_side, position_key=hedge_pk, opened_at=float(ts), entry_reason=reason, is_hedge=True, hedge_for=original_pk)
        self.trade_manager.positions[hedge_pk] = hedge_pos
        self.hedge_engine.register_hedge(original_pk, hedge_pk)
        orig.last_hedge_ts = float(ts)
        return hedge_pos
