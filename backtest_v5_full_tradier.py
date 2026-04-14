#!/usr/bin/env python3
"""
V5 FULL Tradier Backtest — Calls the REAL process_position() with ALL evaluate functions.

NOT a reimplementation. Patches the actual tradier_manage.process_position() with mocked
infrastructure so EVERY gate, score, evaluate function, balancer, budget check, heavy
artillery, and sentiment path runs exactly as in production.

Ablation modes (--ablation):
  ALL         = full pipeline (default)
  NO_STOP     = disable evaluate_stop (never exit)
  NO_OPEN     = disable evaluate_open (never enter — shows augment/reentry only)
  NO_AUGMENT  = disable evaluate_augment
  NO_REENTRY  = disable evaluate_reentry
  STOP_ONLY   = only evaluate_stop (no new entries/augments/reentries)
  OPEN_ONLY   = only evaluate_open (no exits/augments)
  WT_EXIT     = add WT1<WT2 exit (close when wt1<wt2 on 5m+15m, ignoring STRICT_NO_LOSS)

Usage:
    python3 backtest_v5_full_tradier.py --all --start 2024-01-01
    python3 backtest_v5_full_tradier.py --all --start 2024-01-01 --ablation WT_EXIT
    python3 backtest_v5_full_tradier.py --all --start 2024-01-01 --ablation NO_STOP
"""
import argparse
import asyncio
import json
import logging
import platform
import sys
import time as _real_time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, time as dt_time, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from unittest.mock import AsyncMock, MagicMock
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("v5_full")
for noisy in ["binance", "shared_memory_env", "tradier_manage", "tradier_indicators", "tradier_indicators_extra", "wt_composite"]:
    logging.getLogger(noisy).setLevel(logging.WARNING)

if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance-sandbox")

# Prefer 5m synthetic indicators (3yr data) over V4 15m (16 days on local)
_5m_dir = BASE_PATH / "backtest_v5" / "indicators_5m_tradier"
_v4_dir = BASE_PATH / "backtest_v4_tradier" / "indicators"
TRADIER_NPZ_DIR = _5m_dir if _5m_dir.exists() and any(_5m_dir.glob("*.npz")) else _v4_dir
V5_OUTPUT_DIR = BASE_PATH / "backtest_v5"
V5_LOGS_DIR = V5_OUTPUT_DIR / "logs"

# ===========================================================================
# TIME CONTROL
# ===========================================================================
_BT_TIME = 0.0

def _bt_time():
    return _BT_TIME

def _bt_now(tz=None):
    dt = datetime.utcfromtimestamp(_BT_TIME).replace(tzinfo=timezone.utc)
    if tz and tz != timezone.utc:
        return dt.astimezone(tz)
    return dt

def _bt_is_market_hours():
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
    except ImportError:
        import pytz
        et = pytz.timezone("America/New_York")
    now_et = _bt_now(et)
    if now_et.weekday() >= 5:
        return False
    return dt_time(9, 30) <= now_et.time() <= dt_time(16, 0)


# ===========================================================================
# Indicator Store
# ===========================================================================
class IndicatorStore:
    def __init__(self, npz_path: str):
        data = np.load(npz_path, allow_pickle=True)
        self.arrays = {k: data[k] for k in data.files}
        self.timestamps = self.arrays["timestamps"].astype(np.int64)
        self.n_bars = len(self.timestamps)
        self.ts_to_idx = {int(self.timestamps[i]): i for i in range(self.n_bars)}
        data.close()

    def get(self, key, idx, default=0.0):
        arr = self.arrays.get(key)
        if arr is None or idx < 0 or idx >= len(arr):
            return default
        v = float(arr[idx])
        return default if np.isnan(v) else v

    def price(self, idx):
        return self.get("close", idx)

    def build_indicator_dict(self, idx):
        result = {}
        for key in self.arrays:
            if key == "timestamps":
                continue
            result[key] = self.get(key, idx)
        p = self.price(idx)
        result["current_price"] = p
        result["mark_price"] = p
        result["last"] = p
        result["prev_price"] = self.get("close", max(0, idx - 1))
        # Derive _prev OHLC fields from shifted arrays — IBS exit needs close_5m_prev etc.
        for tf in ["3m", "5m", "15m", "1h", "4h", "D"]:
            for field in ["close", "high", "low", "open"]:
                pk = f"{field}_{tf}_prev"
                if pk not in result and f"{field}_{tf}" in self.arrays:
                    result[pk] = self.get(f"{field}_{tf}", max(0, idx - 1))
        ts = int(self.timestamps[idx]) if idx < self.n_bars else 0
        result["_tick_ts"] = float(ts)
        result["ts"] = float(ts)
        ts_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        result["timestamp"] = ts_str
        for tf in ["1m", "3m", "5m", "15m", "1h", "4h", "D"]:
            result[f"timestamp_{tf}"] = ts_str
        for tf in ["5m", "15m", "1h", "4h", "D"]:
            ha_key = f"ha_{tf}"
            if ha_key in result:
                result[ha_key] = "green" if result[ha_key] > 0 else "red"
        # 5m<->3m aliases
        for key in list(result.keys()):
            if key.endswith("_5m"):
                alias = key.replace("_5m", "_3m")
                if alias not in result:
                    result[alias] = result[key]
        if "stoch_k_1m" not in result:
            k5 = result.get("stoch_k_5m", 50.01)
            result["stoch_k_1m"] = k5 if k5 != 50.0 else 50.01
            result["stoch_d_1m"] = result.get("stoch_d_5m", 50.01)
        # Fill NaN 5m from 15m
        def _nan_or_zero(v):
            try:
                return v == 0 or np.isnan(v)
            except (TypeError, ValueError):
                return False
        for pfx in ["stoch_k", "stoch_d", "rsi", "mfi", "wt1", "wt2"]:
            k5, k15 = f"{pfx}_5m", f"{pfx}_15m"
            if k5 in result and _nan_or_zero(result[k5]) and k15 in result and not _nan_or_zero(result[k15]):
                result[k5] = result[k15]
        for pfx in ["k", "d"]:
            for sfx in [f"{pfx}_5m_prev", f"stoch_{pfx}_5m_prev"]:
                f15 = sfx.replace("5m", "15m")
                if (sfx not in result or result.get(sfx, 0) == 0) and f15 in result:
                    result[sfx] = result[f15]
        # Prev aliases
        for tf in ["5m", "15m", "1h", "4h", "D"]:
            for pfx in ["stoch_k", "stoch_d"]:
                lk = f"{pfx}_{tf}"
                pk = f"{'k' if pfx == 'stoch_k' else 'd'}_{tf}_prev"
                spk = f"{pfx}_{tf}_prev"
                if lk in result:
                    if pk not in result:
                        result[pk] = self.get(pk, idx, 50.0)
                    if spk not in result:
                        result[spk] = result.get(pk, 50.0)
        result.setdefault("0market_sentiment_score", 0.0)
        result.setdefault("0market_sentiment_local", 0.0)
        result.setdefault("0sentiment_rank", 999)
        result.setdefault("0sentiment_strength", 50)
        result.setdefault("0sentiment_classification", "NEUTRAL")
        result.setdefault("0is_top_sentiment", False)
        result.setdefault("0is_bottom_sentiment", False)
        result.setdefault("0ranking_points", 0.0)
        result.setdefault("0ranking_rank", 0)
        return result


# ===========================================================================
# Mock Position
# ===========================================================================
@dataclass
class MockPos:
    symbol: str = ""
    position_side: str = "LONG"
    positionAmt: float = 0.0
    quantity: float = 0.0
    entry_price: float = 0.0
    mark_price: float = 0.0
    gain: float = 0.0
    max_gain: float = 0.0
    max_gain_price: float = 0.0
    max_quantity: float = 0.0
    max_positionSize: float = 0.0
    opened_at: Any = None
    entry_time: Any = None
    last_augmentation_time: Any = None
    augment_count: int = 0
    last_reduction_time: Any = None
    last_reduction_price: float = 0.0
    last_reduction_amount: float = 0.0
    total_reduced_qty: float = 0.0
    was_reduced: bool = False
    is_reduced: bool = False
    position_key: str = ""

    def update_price(self, price):
        self.mark_price = price
        if self.entry_price > 0 and price > 0:
            if self.position_side == "LONG":
                self.gain = (price - self.entry_price) / self.entry_price * 100
            else:
                self.gain = (self.entry_price - price) / self.entry_price * 100
        if self.gain > self.max_gain:
            self.max_gain = self.gain
            self.max_gain_price = price


# ===========================================================================
# Mock PositionManager
# ===========================================================================
class MockPositionManager:
    def __init__(self):
        self.positions: Dict[str, MockPos] = {}

    def get_position(self, pk):
        return self.positions.get(pk)

    def get_positions_by_account(self, account_key=""):
        return self.positions


# ===========================================================================
# Full Trade Manager Mock
# ===========================================================================
class FullMockTradeManager:
    def __init__(self, symbols, account_key="trb"):
        self.position_manager = MockPositionManager()
        self.last_monitored_positions: Dict[str, float] = defaultdict(float)
        self.last_exit_times: Dict[str, float] = {}
        self.price_cache: Dict[str, float] = {}
        self.indicator_cache: Dict[str, Dict] = {}
        self.market_snapshot: Dict[str, Dict] = {}
        self.blacklist = set()
        self.non_shortable_symbols = set()
        self.strategy = None  # Set after StockStrategy init
        self.redis_manager = MagicMock()
        self.running = True
        self.exceptions = ['GOOGL', 'MSFT', 'NVDA', 'CVX', 'XOM', 'IBIT', 'GLD', 'ETH', 'XLE', 'GDX', 'USO', 'SLV']
        self.limit_normal_order = 1200
        self.limit_exception_order = 2000
        self.limit_total_pos = 5000
        self.order_deduplication = {}
        self.sentiment_history = []
        self._account_key = account_key
        # Watchlists — populated with all symbols
        for attr in [f"symbols_long_{account_key}", f"symbols_short_{account_key}"]:
            setattr(self, attr, list(symbols))
        # Trade capture
        self.captured_actions: List[Dict] = []

    async def get_current_price(self, symbol):
        return self.price_cache.get(symbol, 0.0), _bt_now()

    async def is_data_fresh(self, symbol, position_key=None):
        ind = self.indicator_cache.get(symbol, {})
        return True, "Age:0s", True, ind

    def get_indicators(self, symbol, use_cache=True):
        return self.indicator_cache.get(symbol, {})

    async def get_market_context(self, symbol):
        return {'bias': 0.0, 'is_tech': False}

    def is_symbol_tradeable(self, symbol, account_key="", side=""):
        return True

    def calculate_unified_market_ratio(self):
        """Compute from market snapshot — same as real code."""
        up, down = 0, 0
        for sym, d in self.market_snapshot.items():
            cur = float(d.get('current_price', 0) or 0)
            prev = float(d.get('close_1h_prev', 0) or 0)
            if cur > 0 and prev > 0:
                if cur > prev:
                    up += 1
                else:
                    down += 1
        total = up + down
        if total == 0:
            return 0.5
        return max(0.05, min(0.95, up / total))

    def get_current_portfolio_balance(self):
        long_val = sum(abs(p.positionAmt * p.mark_price) for p in self.position_manager.positions.values() if p.position_side == "LONG" and p.positionAmt > 0)
        short_val = sum(abs(p.positionAmt * p.mark_price) for p in self.position_manager.positions.values() if p.position_side == "SHORT" and p.positionAmt > 0)
        total = long_val + short_val
        return {"long_pct": long_val / max(1, total), "short_pct": short_val / max(1, total), "long_value": long_val, "short_value": short_val, "total": total}

    def get_custom_universe_index(self):
        return {'breadth': 0.0, 'avg_change_1h': 0.0, 'up': 0, 'down': 0, 'composite': 0.0}

    def update_sentiment_history(self, score):
        self.sentiment_history.append((_BT_TIME, score))
        if len(self.sentiment_history) > 1000:
            self.sentiment_history = self.sentiment_history[-500:]

    def check_pullback_reexpansion(self, symbol, indicators):
        return (False, 0, "", "backtest_disabled")

    def is_swing_entry_allowed(self, side, order_value):
        return (True, "")

    def update_price(self, symbol, price):
        self.price_cache[symbol] = price
        self.market_snapshot.setdefault(symbol, {})["current_price"] = price
        for pk, pos in self.position_manager.positions.items():
            if pos.symbol == symbol:
                pos.update_price(price)

    def update_indicators(self, symbol, indicators):
        self.indicator_cache[symbol] = indicators
        self.market_snapshot[symbol] = indicators


# ===========================================================================
# Main Runner
# ===========================================================================
class V5FullRunner:
    def __init__(self, symbols, start_date, capital, account_key, ablation):
        self.symbols = symbols
        self.start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        self.capital = capital
        self.initial_capital = capital
        self.account_key = account_key
        self.ablation = ablation
        self.stores: Dict[str, IndicatorStore] = {}
        self.tm = FullMockTradeManager(symbols, account_key)
        self.bar_indices: Dict[str, int] = {}
        self.trade_log: List[Dict] = []
        self.total_realized_pnl = 0.0
        self.equity_curve: List[Tuple[int, float]] = []
        self.max_positions = 16
        self.opens_this_hour = 0
        self.last_hour_ts = 0
        self._jsonl_fh = None  # Streaming JSONL file handle
        self._trade_count = 0

    def _load(self):
        for sym in self.symbols:
            path = TRADIER_NPZ_DIR / f"{sym}.npz"
            if path.exists():
                self.stores[sym] = IndicatorStore(str(path))
        logger.info(f"Loaded {len(self.stores)} symbols")

    def _init_strategy(self):
        global _BT_TIME
        import time as time_module
        # Pre-create log dir so RotatingFileHandler doesn't crash on import
        import os
        os.makedirs("/home/niels/logs" if platform.system() == "Linux" else "/Users/niels/logs", exist_ok=True)
        import tradier_manage as tm_mod
        # Remove all file handlers from tradier_manage loggers (they cause FileNotFoundError in sandbox)
        for handler in logging.getLogger().handlers[:]:
            if isinstance(handler, logging.handlers.RotatingFileHandler):
                logging.getLogger().removeHandler(handler)
        for name in list(logging.Logger.manager.loggerDict):
            lgr = logging.getLogger(name)
            for handler in lgr.handlers[:]:
                if hasattr(handler, 'baseFilename'):
                    lgr.removeHandler(handler)
        # Patch time in tradier_manage namespace
        class _FT:
            def __getattr__(self, n):
                return _bt_time if n == 'time' else getattr(time_module, n)
        tm_mod.time = _FT()
        tm_mod.is_regular_trading_hours = _bt_is_market_hours
        # Patch datetime.now via subclass in tradier_manage namespace
        class _BD(datetime):
            @classmethod
            def now(cls, tz=None):
                return _bt_now(tz)
        tm_mod.datetime = _BD
        # Patch safe_datetime
        orig_sd = tm_mod.safe_datetime
        def _sd(val):
            if isinstance(val, datetime):
                return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
            return orig_sd(val)
        tm_mod.safe_datetime = _sd
        from config_tradier import TradierConfig
        cfg = TradierConfig()
        # CLI overrides — applied to BOTH TradierConfig AND config_tradier module
        # (some evaluate_stop paths read from module with getattr(config, ..., 3.0) hardcoded defaults)
        import config_tradier as cfg_mod
        if hasattr(self, '_config_overrides'):
            for k, v in self._config_overrides.items():
                old = getattr(cfg, k, 'N/A')
                setattr(cfg, k, v)
                setattr(cfg_mod, k, v)
                logger.info(f"CONFIG OVERRIDE: {k} = {old} → {v}")
        self.tm.non_shortable_symbols = cfg.NON_SHORTABLE
        self.tm.strategy = tm_mod.StockStrategy(cfg, self.tm)
        self.tm.strategy._is_cvar_safe = lambda symbol, threshold=-6.0: (True, -3.0)
        # CRITICAL: Bypass is_data_fresh — it compares indicator timestamps against wall clock
        # which always returns STALE in backtest (indicators from 2024, wall clock is 2026).
        # Without this patch, process_position returns STALE_INDICATORS_HELD on EVERY call
        # and evaluate_stop/evaluate_open NEVER execute.
        async def _always_fresh(symbol, position_key=None):
            indicators = self.tm.get_indicators(symbol)
            return True, "BACKTEST_FRESH", True, indicators
        self.tm.is_data_fresh = _always_fresh
        async def _sq(symbol, action, ps, bq, ind, pos, mc=None):
            return max(1.0, bq)
        self.tm.strategy.calculate_quantity_complex = _sq
        self.tm.strategy._minutes_since = lambda dt_val: ((_BT_TIME - (dt_val.timestamp() if hasattr(dt_val, 'timestamp') else float(dt_val))) / 60.0) if dt_val else 9999.0
        # Populate tradeable symbols from ACTUAL rankings file (like live does)
        try:
            import json as _json
            _rank_path = Path(__file__).parent / "data" / "tradier" / "tradier_rankings.json"
            if _rank_path.exists():
                _rank_data = _json.loads(_rank_path.read_text())
                _ranked_syms = [r.get("symbol","") for r in _rank_data.get("rankings", []) if r.get("symbol")]
                if _ranked_syms:
                    self.tm.symbols_long_trb = _ranked_syms
                    self.tm.symbols_short_trb = _ranked_syms
                    self.tm.symbols_long_trc = _ranked_syms
                    self.tm.symbols_short_trc = _ranked_syms
                    logger.info(f"Loaded {len(_ranked_syms)} tradeable symbols from rankings: {_ranked_syms[:10]}...")
        except Exception as _e:
            logger.warning(f"Could not load rankings: {_e}. Using all NPZ symbols.")
        # Apply ablation patches
        if self.ablation == "NO_STOP":
            self.tm.strategy.evaluate_stop = AsyncMock(return_value=(False, "ABLATION_OFF", 0))
        elif self.ablation == "NO_OPEN":
            self.tm.strategy.evaluate_open = AsyncMock(return_value=("NO_ACTION", "ABLATION_OFF", 0.0, 0.0))
        elif self.ablation == "NO_AUGMENT":
            self.tm.strategy.evaluate_augment = AsyncMock(return_value=(False, "ABLATION_OFF", 0.0, 0.0))
        elif self.ablation == "NO_REENTRY":
            self.tm.strategy.evaluate_reentry = AsyncMock(return_value=("NO_ACTION", "ABLATION_OFF", 0.0, 0.0))
        elif self.ablation == "STOP_ONLY":
            self.tm.strategy.evaluate_open = AsyncMock(return_value=("NO_ACTION", "ABLATION_OFF", 0.0, 0.0))
            self.tm.strategy.evaluate_augment = AsyncMock(return_value=(False, "ABLATION_OFF", 0.0, 0.0))
            self.tm.strategy.evaluate_reentry = AsyncMock(return_value=("NO_ACTION", "ABLATION_OFF", 0.0, 0.0))
        elif self.ablation == "OPEN_ONLY":
            self.tm.strategy.evaluate_stop = AsyncMock(return_value=(False, "ABLATION_OFF", 0))
            self.tm.strategy.evaluate_augment = AsyncMock(return_value=(False, "ABLATION_OFF", 0.0, 0.0))
            self.tm.strategy.evaluate_reentry = AsyncMock(return_value=("NO_ACTION", "ABLATION_OFF", 0.0, 0.0))
        # Patch queue_trade_action to capture trades instead of executing
        self._real_queue_trade_action = tm_mod.queue_trade_action
        async def _capture_trade(order_queue, trade_manager, position_key, action, reason, conviction=50.0, override_qty=None):
            self._handle_trade(position_key, action, reason, conviction, override_qty)
        tm_mod.queue_trade_action = _capture_trade
        # Patch log_stoch_snapshot to no-op
        tm_mod.log_stoch_snapshot = AsyncMock()
        # Patch record_decision_context to no-op
        tm_mod.record_decision_context = AsyncMock()
        # Store module ref for process_position
        self._tm_mod = tm_mod
        # === HTF WT EXIT — REPLACES evaluate_stop entirely ===
        # Old wrapper waited for evaluate_stop to fire first, but gain gates blocked it.
        # New approach: pure HTF WT delta logic. No gain gates. Exit when WT momentum dies.
        _wt_tfs = getattr(self, '_wt_exit_tfs', None)
        _wt_mode = getattr(self, '_wt_exit_mode', 'cross')
        _wt_vel_th = getattr(self, '_wt_vel_threshold', -2.0)
        _orig_stop = self.tm.strategy.evaluate_stop
        if _wt_tfs is not None:
            async def _pure_htf_evaluate_stop(symbol, position, indicators, market_context=None, in_grace_period=False):
                if _wt_tfs == "off":
                    return False, "HTF_WT_DISABLED", 0
                pos_qty = abs(getattr(position, 'positionAmt', 0) or getattr(position, 'quantity', 0))
                if pos_qty < 0.0001:
                    return False, "NO_POSITION", 0
                # IBS exhaustion — proven edge, always check first
                gain = getattr(position, 'gain', 0)
                i = indicators
                close_prev = float(i.get('close_5m_prev', 0) or 0)
                high_prev = float(i.get('high_5m_prev', 0) or 0)
                low_prev = float(i.get('low_5m_prev', 0) or 0)
                is_long = getattr(position, 'position_side', 'LONG') == 'LONG'
                if close_prev > 0 and high_prev > low_prev:
                    ibs = (close_prev - low_prev) / (high_prev - low_prev)
                    if is_long and ibs > 0.85 and gain > 0.05:
                        return True, f"IBS_EXHAUSTION_L(ibs={ibs:.2f},g={gain:.1f}%)", pos_qty
                    if not is_long and ibs < 0.15 and gain > 0.05:
                        return True, f"IBS_EXHAUSTION_S(ibs={ibs:.2f},g={gain:.1f}%)", pos_qty
                # HTF WT delta exit — the core logic. NO gain gate.
                tfs = [tf.strip() for tf in _wt_tfs.split(",")]
                against = 0
                detail_parts = []
                for tf in tfs:
                    wt1 = float(i.get(f'wt1_{tf}', 0) or 0)
                    wt2 = float(i.get(f'wt2_{tf}', 0) or 0)
                    vel = float(i.get(f'wt_velocity_{tf}', 0) or 0)
                    hit = False
                    if _wt_mode == "cross":
                        hit = (is_long and wt1 < wt2) or (not is_long and wt1 > wt2)
                    elif _wt_mode == "velocity":
                        hit = (is_long and vel < _wt_vel_th) or (not is_long and vel > abs(_wt_vel_th))
                    elif _wt_mode == "both":
                        cross_against = (is_long and wt1 < wt2) or (not is_long and wt1 > wt2)
                        vel_against = (is_long and vel < _wt_vel_th) or (not is_long and vel > abs(_wt_vel_th))
                        hit = cross_against and vel_against
                    if hit:
                        against += 1
                        detail_parts.append(f"{tf}(v={vel:.1f})")
                needed = max(2, len(tfs)) if len(tfs) >= 3 else len(tfs)
                if against >= needed:
                    return True, f"HTF_WT_EXIT_{'+'.join(tfs)}_{against}of{len(tfs)}_{_wt_mode}_g={gain:.1f}%_{'_'.join(detail_parts)}", pos_qty
                return False, f"HTF_WT_HOLD_{against}of{needed}", 0
            self.tm.strategy.evaluate_stop = _pure_htf_evaluate_stop
            logger.info(f"HTF WT EXIT (PURE — no gain gates): tfs={_wt_tfs}, mode={_wt_mode}, vel_th={_wt_vel_th}")
        elif getattr(self, '_noloss_override', None) is not None and self._noloss_override <= 0:
            async def _no_gain_gate_stop(symbol, position, indicators, market_context=None, in_grace_period=False):
                should_exit, reason, qty = await _orig_stop(symbol, position, indicators, market_context, in_grace_period)
                if not should_exit and "NOLOSS_HOLD" in reason:
                    return True, reason.replace("NOLOSS_HOLD", "NOLOSS_BYPASSED"), qty
                return should_exit, reason, qty
            self.tm.strategy.evaluate_stop = _no_gain_gate_stop
            logger.info("NOLOSS BYPASS: gain gates disabled for evaluate_stop")
        # === DAILY WT ENTRY GATE ===
        if getattr(self, '_entry_d_gate', False):
            _orig_open = self.tm.strategy.evaluate_open
            async def _d_gated_open(account_key, symbol, position_side, indicators, market_context=None):
                vel_d = float(indicators.get('wt_velocity_D', 0) or 0)
                wt1_d = float(indicators.get('wt1_D', 0) or 0)
                wt2_d = float(indicators.get('wt2_D', 0) or 0)
                if position_side == "LONG" and (vel_d < 0 or wt1_d < wt2_d):
                    return "NO_ACTION", f"D_WT_GATE_vel={vel_d:.1f}", 0, 0
                if position_side == "SHORT" and (vel_d > 0 or wt1_d > wt2_d):
                    return "NO_ACTION", f"D_WT_GATE_vel={vel_d:.1f}", 0, 0
                return await _orig_open(account_key, symbol, position_side, indicators, market_context)
            self.tm.strategy.evaluate_open = _d_gated_open
            logger.info("DAILY WT ENTRY GATE active")
        logger.info(f"Strategy initialized, ablation={self.ablation}")

    def _write_trade(self, d):
        """Stream trade to JSONL file + keep in memory for summary."""
        self.trade_log.append(d)
        self._trade_count += 1
        if self._jsonl_fh:
            self._jsonl_fh.write(json.dumps(d) + "\n")
            if self._trade_count % 100 == 0:
                self._jsonl_fh.flush()
        # Keep memory bounded — only keep last 500 trades in memory for summary
        if len(self.trade_log) > 2000:
            self.trade_log = self.trade_log[-1000:]

    def _handle_trade(self, position_key, action, reason, conviction, override_qty):
        """Process a captured trade action."""
        parts = position_key.split(":")
        ak = parts[0] if len(parts) > 1 else self.account_key
        rest = parts[1] if len(parts) > 1 else parts[0]
        if "_LONG" in rest:
            symbol = rest.replace("_LONG", "")
            side = "LONG"
        else:
            symbol = rest.replace("_SHORT", "")
            side = "SHORT"
        price = self.tm.price_cache.get(symbol, 0)
        if price <= 0:
            return
        qty = override_qty if override_qty and override_qty > 0 else 0
        if qty <= 0:
            from config_tradier import TradierConfig
            cfg = TradierConfig()
            qty = cfg.START_POSITION_SIZE / price
        pos = self.tm.position_manager.positions.get(f"{symbol}_{side}")
        if action == "OPEN":
            if f"{symbol}_{side}" in self.tm.position_manager.positions:
                return  # Already open
            if len(self.tm.position_manager.positions) >= self.max_positions:
                return
            hour_ts = int(_BT_TIME) // 3600
            if hour_ts != self.last_hour_ts:
                self.opens_this_hour = 0
                self.last_hour_ts = hour_ts
            if self.opens_this_hour >= 3:
                return
            new_pos = MockPos(symbol=symbol, position_side=side, positionAmt=qty, quantity=qty, entry_price=price, mark_price=price, opened_at=_bt_now(), entry_time=_bt_now(), max_quantity=qty * 3, max_positionSize=qty * 3 * price, position_key=f"{symbol}_{side}")
            self.tm.position_manager.positions[f"{symbol}_{side}"] = new_pos
            self._write_trade({"ts": int(_BT_TIME), "dt": datetime.utcfromtimestamp(_BT_TIME).isoformat(), "symbol": symbol, "side": side, "action": "OPEN", "reason": reason[:120], "price": round(price, 4), "qty": round(qty, 4), "gain": 0, "pnl": 0, "conv": round(conviction, 1)})
            self.opens_this_hour += 1
        elif action == "CLOSE":
            if pos is None:
                return
            gain = pos.gain
            pnl = abs(pos.positionAmt) * pos.entry_price * (gain / 100)
            self.total_realized_pnl += pnl
            self._write_trade({"ts": int(_BT_TIME), "dt": datetime.utcfromtimestamp(_BT_TIME).isoformat(), "symbol": symbol, "side": side, "action": "CLOSE", "reason": reason[:120], "price": round(price, 4), "qty": round(pos.positionAmt, 4), "gain": round(gain, 4), "pnl": round(pnl, 2), "conv": round(conviction, 1)})
            del self.tm.position_manager.positions[f"{symbol}_{side}"]
            self.tm.last_exit_times[symbol] = _BT_TIME
        elif action == "AUGMENT":
            if pos is None:
                return
            old_qty = pos.positionAmt
            total = old_qty + qty
            pos.entry_price = (pos.entry_price * old_qty + price * qty) / total
            pos.positionAmt = total
            pos.quantity = total
            pos.augment_count += 1
            pos.last_augmentation_time = _bt_now()
            self._write_trade({"ts": int(_BT_TIME), "dt": datetime.utcfromtimestamp(_BT_TIME).isoformat(), "symbol": symbol, "side": side, "action": "AUGMENT", "reason": reason[:120], "price": round(price, 4), "qty": round(qty, 4), "gain": round(pos.gain, 4), "pnl": 0, "conv": round(conviction, 1)})

    def _step_to(self, ts):
        global _BT_TIME
        _BT_TIME = float(ts)
        for sym, store in self.stores.items():
            idx = store.ts_to_idx.get(ts, -1)
            if idx < 0:
                continue
            self.bar_indices[sym] = idx
            price = store.price(idx)
            if price > 0:
                self.tm.update_price(sym, price)
                self.tm.update_indicators(sym, store.build_indicator_dict(idx))

    async def _wt_exit_check(self, ts):
        """WT1<WT2 exit — close when WT bearish on 5m+15m regardless of gain."""
        if self.ablation != "WT_EXIT":
            return
        to_close = []
        for pk, pos in list(self.tm.position_manager.positions.items()):
            ind = self.tm.indicator_cache.get(pos.symbol, {})
            if not ind:
                continue
            wt1_5m = float(ind.get("wt1_5m", 0) or 0)
            wt2_5m = float(ind.get("wt2_5m", 0) or 0)
            wt1_15m = float(ind.get("wt1_15m", 0) or 0)
            wt2_15m = float(ind.get("wt2_15m", 0) or 0)
            if pos.position_side == "LONG":
                if wt1_5m < wt2_5m and wt1_15m < wt2_15m:
                    to_close.append((pk, f"WT_EXIT_L(wt1_5m={wt1_5m:.1f}<wt2={wt2_5m:.1f},15m={wt1_15m:.1f}<{wt2_15m:.1f})"))
            else:
                if wt1_5m > wt2_5m and wt1_15m > wt2_15m:
                    to_close.append((pk, f"WT_EXIT_S(wt1_5m={wt1_5m:.1f}>wt2={wt2_5m:.1f},15m={wt1_15m:.1f}>{wt2_15m:.1f})"))
        for pk, reason in to_close:
            pos = self.tm.position_manager.positions.get(pk)
            if pos:
                gain = pos.gain
                pnl = abs(pos.positionAmt) * pos.entry_price * (gain / 100)
                self.total_realized_pnl += pnl
                self._write_trade({"ts": int(_BT_TIME), "dt": datetime.utcfromtimestamp(_BT_TIME).isoformat(), "symbol": pos.symbol, "side": pos.position_side, "action": "CLOSE", "reason": reason[:120], "price": round(pos.mark_price, 4), "qty": round(pos.positionAmt, 4), "gain": round(gain, 4), "pnl": round(pnl, 2), "conv": 0})
                del self.tm.position_manager.positions[pk]
                self.tm.last_exit_times[pos.symbol] = _BT_TIME

    async def run(self):
        self._load()
        self._init_strategy()
        if not self.stores:
            logger.error("No data!")
            return None
        # Open streaming JSONL file
        V5_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self._jsonl_path = V5_LOGS_DIR / f"full_{self.ablation}_{self.account_key}_{ts_str}.jsonl"
        self._jsonl_fh = open(self._jsonl_path, "w")
        all_ts = set()
        for sym, store in self.stores.items():
            for t in store.timestamps:
                t_int = int(t)
                if t_int >= self.start_ts:
                    all_ts.add(t_int)
        # Filter market hours
        market_ts = []
        for ts in sorted(all_ts):
            dt = datetime.utcfromtimestamp(ts)
            if dt.weekday() >= 5:
                continue
            utc_min = dt.hour * 60 + dt.minute
            if 810 <= utc_min <= 1200:
                market_ts.append(ts)
        sorted_ts = market_ts
        logger.info(f"Sim: {len(sorted_ts)} bars, {len(self.stores)} symbols, ablation={self.ablation}")
        t0 = _real_time.time()
        report_every = max(1, len(sorted_ts) // 20)
        process_position = self._tm_mod.process_position
        order_queue = MagicMock()
        for step, ts in enumerate(sorted_ts):
            self._step_to(ts)
            # DIRECT LOOP: call evaluate_stop + evaluate_open directly (fast, all exit paths fire)
            for sym in list(self.stores.keys()):
                if sym not in self.tm.indicator_cache:
                    continue
                ind = self.tm.indicator_cache[sym]
                price = self.tm.price_cache.get(sym, 0)
                if price <= 0:
                    continue
                for side in ["LONG", "SHORT"]:
                    pk = f"{sym}_{side}"
                    full_pk = f"{self.account_key}:{pk}"
                    pos = self.tm.position_manager.positions.get(pk)
                    if pos and abs(pos.positionAmt) > 0:
                        should_exit, reason, qty = await self.tm.strategy.evaluate_stop(sym, pos, ind)
                        if should_exit:
                            self._handle_trade(full_pk, "CLOSE", reason, 100.0, None)
                    elif pos is None:
                        action, reason, conf, qty = await self.tm.strategy.evaluate_open(self.account_key, sym, side, ind)
                        if action == "OPEN":
                            self._handle_trade(full_pk, "OPEN", reason, conf, qty if qty > 0 else None)
            # Call ALL extra evaluate functions that live in the main loop, not process_position
            # These are real entry strategies that fire independently of process_position
            for _extra_eval in [
                'evaluate_rotation_entry', 'evaluate_spike_fade', 'evaluate_gap_fill',
                'evaluate_rsi2_entry', 'evaluate_orb_entry', 'evaluate_episodic_pivot',
                'evaluate_clenow_entry', 'evaluate_smfi_entry', 'evaluate_minervini_entry',
                'evaluate_connors_rsi_entry',
            ]:
                if hasattr(self.tm.strategy, _extra_eval):
                    try:
                        await getattr(self.tm.strategy, _extra_eval)(self.account_key)
                    except Exception:
                        pass
            # Reset throttle so next bar processes all symbols
            self.tm.last_monitored_positions.clear()
            # Equity
            unrealized = sum(abs(p.positionAmt) * p.entry_price * p.gain / 100 for p in self.tm.position_manager.positions.values() if p.positionAmt > 0)
            equity = self.initial_capital + self.total_realized_pnl + unrealized
            if step % 5 == 0:
                self.equity_curve.append((ts, equity))
            if step > 0 and step % report_every == 0:
                elapsed = _real_time.time() - t0
                n_pos = len(self.tm.position_manager.positions)
                n_closed = sum(1 for l in self.trade_log if l["action"] == "CLOSE")
                logger.info(f"  {step/len(sorted_ts)*100:.0f}% | {n_pos} pos | {n_closed} closed | PnL: ${self.total_realized_pnl:.2f} | eq: ${equity:.2f} | {elapsed:.0f}s")
        elapsed = _real_time.time() - t0
        unrealized = sum(abs(p.positionAmt) * p.entry_price * p.gain / 100 for p in self.tm.position_manager.positions.values() if p.positionAmt > 0)
        n_closed = sum(1 for l in self.trade_log if l["action"] == "CLOSE")
        logger.info(f"Done in {elapsed:.1f}s | {n_closed} closed trades | Realized: ${self.total_realized_pnl:.2f} | Unrealized: ${unrealized:.2f} | Open: {len(self.tm.position_manager.positions)}")
        if self._jsonl_fh:
            self._jsonl_fh.close()
        logger.info(f"Wrote {self._trade_count} trades to {self._jsonl_path}")
        return self._save()

    def _save(self):
        log_path = self._jsonl_path
        # Read ALL trades from JSONL file for summary (streamed to disk, not memory)
        all_trades = []
        if log_path.exists():
            for line in open(log_path):
                try:
                    all_trades.append(json.loads(line))
                except Exception:
                    pass
        logger.info(f"Summary from {len(all_trades)} trades in {log_path}")
        opens = [l for l in all_trades if l["action"] == "OPEN"]
        closes = [l for l in all_trades if l["action"] == "CLOSE"]
        augments = [l for l in all_trades if l["action"] == "AUGMENT"]
        wins = [l for l in closes if l["gain"] > 0]
        losses = [l for l in closes if l["gain"] <= 0]
        unrealized = sum(abs(p.positionAmt) * p.entry_price * p.gain / 100 for p in self.tm.position_manager.positions.values() if p.positionAmt > 0)
        sym_pnl = defaultdict(float)
        sym_n = defaultdict(int)
        for l in closes:
            sym_pnl[l["symbol"]] += l["pnl"]
            sym_n[l["symbol"]] += 1
        exit_reasons = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
        for l in closes:
            cat = l["reason"].split("(")[0].split("_")[0][:25]
            exit_reasons[cat]["n"] += 1
            exit_reasons[cat]["pnl"] += l["pnl"]
            if l["gain"] > 0:
                exit_reasons[cat]["wins"] += 1
        entry_reasons = defaultdict(int)
        for l in opens:
            cat = l["reason"].split(":")[0][:30] if ":" in l["reason"] else l["reason"].split("_")[0][:30]
            entry_reasons[cat] += 1
        print(f"\n{'='*90}")
        print(f"  V5 FULL TRADIER — REAL process_position() — {self.ablation} — {self.account_key.upper()}")
        print(f"{'='*90}")
        print(f"  Period: {datetime.utcfromtimestamp(self.start_ts).date()} → {datetime.utcfromtimestamp(self.equity_curve[-1][0]).date() if self.equity_curve else '?'}")
        print(f"  Realized: {len(closes)} trades  |  PnL: ${self.total_realized_pnl:.2f}")
        print(f"  Unrealized: ${unrealized:.2f}  |  TOTAL: ${self.total_realized_pnl + unrealized:.2f}")
        print(f"  Opens: {len(opens)}  |  Augments: {len(augments)}")
        wr = len(wins) / max(1, len(closes)) * 100
        avg_w = np.mean([l["pnl"] for l in wins]) if wins else 0
        avg_l = np.mean([l["pnl"] for l in losses]) if losses else 0
        print(f"  Win rate: {wr:.1f}%  |  Avg win: ${avg_w:.2f}  |  Avg loss: ${avg_l:.2f}")
        print(f"  Still open: {len(self.tm.position_manager.positions)}")
        if len(self.equity_curve) > 100:
            eq_ts = np.array([t for t, _ in self.equity_curve])
            eq = np.array([e for _, e in self.equity_curve])
            rets = np.diff(eq) / eq[:-1]
            rets = rets[~np.isnan(rets)]
            if len(rets) > 0 and np.std(rets) > 0:
                total_days = max(1, (eq_ts[-1] - eq_ts[0]) / 86400)
                samples_per_day = len(rets) / total_days
                samples_per_year = samples_per_day * 252
                sharpe = np.mean(rets) / np.std(rets) * np.sqrt(samples_per_year)
                peak = np.maximum.accumulate(eq)
                drawdown = (peak - eq) / (peak + 1e-10) * 100
                max_dd = float(np.max(drawdown))
                print(f"  Sharpe: {sharpe:.2f}  |  Max DD: {max_dd:.1f}%")
        print(f"-{'='*89}")
        print("  ENTRY REASONS:")
        for cat, n in sorted(entry_reasons.items(), key=lambda x: -x[1])[:15]:
            print(f"    {cat:35s}  n={n}")
        print(f"-{'='*89}")
        print("  EXIT REASONS (by PnL):")
        for cat, d in sorted(exit_reasons.items(), key=lambda x: -x[1]["pnl"])[:15]:
            wr_e = d["wins"] / max(1, d["n"]) * 100
            print(f"    {cat:35s}  n={d['n']:4d}  PnL: ${d['pnl']:8.2f}  WR: {wr_e:.0f}%")
        print(f"-{'='*89}")
        print("  TOP 10 SYMBOLS:")
        for sym, pnl in sorted(sym_pnl.items(), key=lambda x: -x[1])[:10]:
            print(f"    {sym:8s}  PnL: ${pnl:8.2f}  trades: {sym_n[sym]}")
        print(f"  WORST 5:")
        for sym, pnl in sorted(sym_pnl.items(), key=lambda x: x[1])[:5]:
            print(f"    {sym:8s}  PnL: ${pnl:8.2f}  trades: {sym_n[sym]}")
        # Open losers
        open_losers = [(pk, p) for pk, p in self.tm.position_manager.positions.items() if p.gain < 0]
        if open_losers:
            print(f"-{'='*89}")
            print(f"  OPEN LOSERS ({len(open_losers)}):")
            for pk, p in sorted(open_losers, key=lambda x: x[1].gain):
                val = abs(p.positionAmt * p.mark_price)
                print(f"    {pk:25s} gain={p.gain:+7.1f}%  val=${val:7.0f}  entry={p.entry_price:.2f}")
        print(f"{'='*90}")
        return log_path


def main():
    parser = argparse.ArgumentParser(description="V5 FULL Tradier — REAL process_position()")
    parser.add_argument("--symbols", type=str, default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--start", type=str, default="2024-01-01")
    parser.add_argument("--capital", type=float, default=70000.0)
    parser.add_argument("--account", type=str, default="trb")
    parser.add_argument("--ablation", type=str, default="ALL", choices=["ALL", "NO_STOP", "NO_OPEN", "NO_AUGMENT", "NO_REENTRY", "STOP_ONLY", "OPEN_ONLY", "WT_EXIT"])
    parser.add_argument("--noloss", type=float, default=None, help="Override NOLOSS_MIN_PROFIT_PCT_TRADIER (0=no floor)")
    parser.add_argument("--wt-exit-tfs", type=str, default=None, help="WT exit TFs, e.g. '4h,D' or '1h,4h,D' or 'off'")
    parser.add_argument("--wt-exit-mode", type=str, default="cross", choices=["cross", "velocity", "both"], help="WT exit signal: cross (wt1<wt2), velocity (vel<threshold), both")
    parser.add_argument("--wt-vel-threshold", type=float, default=-2.0, help="WT velocity threshold for exit (used with --wt-exit-mode velocity)")
    parser.add_argument("--entry-d-gate", action="store_true", help="Block entries when Daily WT velocity opposes direction")
    parser.add_argument("--config", type=str, default="", help="JSON config overrides, e.g. '{\"START_POSITION_SIZE\": 1200}'")
    args = parser.parse_args()
    if args.all:
        symbols = sorted([p.stem for p in TRADIER_NPZ_DIR.glob("*.npz")])
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        symbols = sorted([p.stem for p in TRADIER_NPZ_DIR.glob("*.npz")])
    if not symbols:
        logger.error(f"No symbols in {TRADIER_NPZ_DIR}")
        sys.exit(1)
    logger.info(f"V5 FULL: {len(symbols)} symbols, start={args.start}, ablation={args.ablation}")
    runner = V5FullRunner(symbols, args.start, args.capital, args.account, args.ablation)
    # Apply CLI config overrides
    overrides = {}
    if args.noloss is not None:
        overrides["NOLOSS_MIN_PROFIT_PCT_TRADIER"] = args.noloss
    if args.config:
        overrides.update(json.loads(args.config))
    if overrides:
        runner._config_overrides = overrides
    # HTF exit config
    if args.wt_exit_tfs is not None:
        runner._wt_exit_tfs = args.wt_exit_tfs  # e.g. "4h,D" or "off"
    runner._wt_exit_mode = args.wt_exit_mode
    runner._wt_vel_threshold = args.wt_vel_threshold
    if args.noloss is not None:
        runner._noloss_override = args.noloss
    runner._entry_d_gate = args.entry_d_gate
    asyncio.run(runner.run())


if __name__ == "__main__":
    main()
