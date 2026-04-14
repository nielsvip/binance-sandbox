#!/usr/bin/env python3
"""
Backtest V5 Engine — Runs the ACTUAL process_position() from ez_manage.py.

This engine creates a complete BacktestTradeManager that satisfies every
dependency of process_position(), then steps through .npz timestamps and
triggers the real evaluate functions for each symbol.

ALL trade decisions come from the REAL code:
- AdvancedSignalRater.rate() — signal scoring
- evaluate_reentry() — position re-entry
- evaluate_augmentation() — position sizing up
- evaluate_technical_indicator_signals() — technical entries
- evaluate_ranking_momentum_trade() — ranking momentum
- evaluate_leaderboard_entry() — leaderboard entries
- evaluate_reversal_entry() — reversal entries
- HedgeEngine logic — hedge opening/closing
- queue_trade_action() — trade quantification
- execute_trade_action() — order execution (mocked to log)

Usage:
    python3 backtest_v5_engine.py --mode crypto --all --start 2022-01-01
    python3 backtest_v5_engine.py --mode tradier --all --start 2024-01-01
"""

import argparse
import asyncio
import json
import logging
import os
import platform
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Set

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from backtest_v5_harness import IndicatorStore

# Use 3m-interpolated data if available, otherwise fall back to 15m
def get_npz_dir(mode):
    """Use 3m/5m for real trigger resolution. Lazy-loaded to manage 773MB/sym memory."""
    if mode == "crypto":
        dir_3m = BASE_PATH / "backtest_v5" / "indicators_3m"
        if dir_3m.exists() and any(dir_3m.glob("*.npz")):
            return dir_3m, "3m"
        return BASE_PATH / "backtest_v4" / "indicators", "15m"
    else:
        dir_5m = BASE_PATH / "backtest_v5" / "indicators_5m_tradier"
        if dir_5m.exists() and any(dir_5m.glob("*.npz")):
            return dir_5m, "5m"
        return BASE_PATH / "backtest_v4_tradier" / "indicators", "15m"

# Suppress noisy imports
for name in ["binance", "shared_memory_env", "urllib3", "websockets"]:
    logging.getLogger(name).setLevel(logging.CRITICAL)

import config
from utils import parse_position_key, construct_position_key

# Import ACTUAL evaluate functions
from ez_manage import (
    process_position,
    queue_trade_action,
    evaluate_reentry,
    evaluate_augmentation,
    evaluate_technical_indicator_signals,
    evaluate_ranking_momentum_trade,
    evaluate_leaderboard_entry,
    evaluate_reversal_entry,
    Signal,
    OrderQueue,
)
from ez_positions_quick import AdvancedSignalRater, analyze_multi_tf_state, check_entry_candidates_for_account, check_exit_candidates_for_account, execute_trade_wrapper

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("v5_engine")
logger.setLevel(logging.INFO)
# Suppress ALL live code loggers — they produce 220K+ lines and eat memory
for _name in ["ez_manage", "ez_positions_quick", "SHARED_MEM", "hedge_engine", "trade_manager", "root"]:
    logging.getLogger(_name).setLevel(logging.ERROR)

if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance-sandbox")

CRYPTO_NPZ_DIR = BASE_PATH / "backtest_v4" / "indicators"
TRADIER_NPZ_DIR = BASE_PATH / "backtest_v4_tradier" / "indicators"
V5_DIR = BASE_PATH / "backtest_v5"


# ═══════════════════════════════════════════════════════════════════
# Mock Position — matches ez_manage.py Position class interface
# ═══════════════════════════════════════════════════════════════════
class BacktestPosition:
    def __init__(self, symbol, position_side, entry_price, qty, opened_at=None):
        self.symbol = symbol
        self.position_side = position_side
        self.entry_price = entry_price
        self.mark_price = entry_price
        self.positionAmt = qty
        self.gain = 0.0
        self.prev_gain = 0.0
        self.max_gain = 0.0
        self.max_gain_price = entry_price
        self.first_gain_ts = 0.0
        self.opened_at = opened_at or datetime.now(timezone.utc)
        self.last_updated = opened_at or datetime.now(timezone.utc)
        self.last_augmentation_time = None
        self.last_augmentation_price = 0.0
        self.last_reduction_time = None
        self.last_reduction_price = 0.0
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.augmented_count = 0
        self.entry_reason = ""
        self.mark_price_last_updated = datetime.now(timezone.utc)
        self.max_quantity = qty
        self.last_signal = ""
        self.last_signal_time = None
        self.sba_add_count = 0
        self.last_sba_time = 0
        self.scalping_mode = False
        self.exit_price = 0.0
        self.total_entry_qty = qty
        self.position_key = ""

    @property
    def is_long(self):
        return self.position_side == "LONG"

    def update_price(self, price):
        self.mark_price = price
        if self.entry_price > 0:
            if self.is_long:
                self.gain = (price - self.entry_price) / self.entry_price * 100
            else:
                self.gain = (self.entry_price - price) / self.entry_price * 100
        self.prev_gain = self.gain
        if self.gain > self.max_gain:
            self.max_gain = self.gain
            self.max_gain_price = price
        self.unrealized_pnl = abs(self.positionAmt) * price * self.gain / 100
        # last_updated set by caller (engine sets sim time after update_price)
        # mark_price_last_updated used by staleness checks
        # Note: these get overwritten by engine's _sync_sim_time each bar


# ═══════════════════════════════════════════════════════════════════
# Mock TrackerManager — satisfies all tracker_manager accesses
# ═══════════════════════════════════════════════════════════════════
class BacktestTrackerManager:
    def __init__(self):
        self._positions: Dict[str, Any] = {}
        self._processing: Dict[str, bool] = {}
        self.exit_candidates: Dict[str, Dict] = {}
        self.entry_candidates: Dict[str, Dict] = {}
        self.active_hedges: List[Dict] = []
        self.boycott_symbols: set = set()
        self.open_blocked: Dict[str, float] = {}
        self._exit_candidates_dirty: Dict[str, bool] = {}
        self._entry_candidates_dirty: Dict[str, bool] = {}
        self.tradeable_keys: set = set()
        self.tradeable_keys_cache: set = set()
        self.tradeable_position_keys: Dict[str, set] = {}
        self.disabled_entries: Dict[str, bool] = {}
        self.reduced_positions: Dict[str, Dict] = {}
        self.reentry_data: Dict[str, Dict] = {}
        self.hedge_candidates: Dict[str, Dict] = {}
        self.scalp_positions: Dict[str, Dict] = {}
        self.accounts: Dict[str, Any] = {}
        self.account_keys: List[str] = []
        self.max_concurrent_hedges: int = 10
        self.hedge_mode_enabled: bool = True
        self.positions: Dict[str, Any] = {}
        # Required by check_entry/exit_candidates
        self.last_check_times: Dict[str, float] = {}
        self._processing_orders: Dict[str, float] = {}
        self.hedge_liability_cooldowns: Dict[str, float] = {}
        self.last_exit_prices: Dict[str, float] = {}
        self.last_exit_times: Dict[str, float] = {}
        self.restored_positions: set = set()
        self._hedges_lock = None  # set to asyncio.Lock() in engine.run()
        self._exit_candidates_lock = None  # set to asyncio.Lock() in engine.run()
        self._entry_candidates_lock = None
        self._auto_hedge_cd: Dict[str, float] = {}
        self._timing_lock = asyncio.Lock()
        class _MockRegistry:
            market_panic = False
            market_euphoria = False
            market_regime = "NORMAL"
            def get(self, *a): return None
            def set(self, *a): return None
            def get_rating(self, *a, **kw): return (0, "WAIT", "NO_CACHE", 0.0)
            def set_rating(self, *a, **kw): pass
            def get_signal(self, *a, **kw): return None
            def set_signal(self, *a, **kw): pass
            def __getattr__(self, name):
                if name.startswith("__"): raise AttributeError(name)
                return {}  # return empty dict so .get() calls work
        self.registry = _MockRegistry()
        class _MockPosService:
            positions = {}
            positions_by_account = {}
            _position_update_timestamps = {}
            def update_position_timestamp(self, pk, ts=None):
                self._position_update_timestamps[pk] = ts or time.time()
            def get_long_short_ratio(self, ak=""):
                long_val, short_val = 0.0, 0.0
                src = self.positions_by_account.get(ak, self.positions) if ak else self.positions
                for pk, pos in src.items():
                    if not hasattr(pos, 'positionAmt') or abs(pos.positionAmt) < 0.0001:
                        continue
                    val = abs(pos.positionAmt) * getattr(pos, 'mark_price', getattr(pos, 'entry_price', 0))
                    if hasattr(pos, 'is_long'):
                        if pos.is_long:
                            long_val += val
                        else:
                            short_val += val
                    elif hasattr(pos, 'position_side'):
                        if pos.position_side == "LONG":
                            long_val += val
                        else:
                            short_val += val
                ratio = long_val / short_val if short_val > 0.01 else (99.0 if long_val > 0 else 1.0)
                return {"long": long_val, "short": short_val, "ratio": ratio}
            def get_account_positions(self, ak=""):
                return self.positions_by_account.get(ak, self.positions)
        self.positions_service = _MockPosService()

    def _entry_template(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {'status': 'entry_candidate', 'timestamp': now, 'entry_price': 0.0, 'exit_price': 0.0, 'average_entry_price': 0.0, 'average_exit_price': 0.0, 'average_pos_value': 0.0, 'current_pos_value': 0.0, 'total_entry_qty': 0.0, 'positionAmt': 0.0, 'first_entry_price': 0.0, 'mark_price': 0.0, 'mark_price_last_updated': None, 'gain_list': [], 'gain_dollar_list': [], 'current_gain_%': 0.0, 'max_gain': 0.0, 'total_realized_pnl_$': 0.0, 'winning_trades': 0, 'losing_trades': 0, 'total_trades': 0, 'win_rate_%': 0.0, 'consecutive_wins': 0, 'consecutive_losses': 0, 'last_augmentation_amount': 0.0, 'last_augmentation_price': 0.0, 'last_augmentation_time': None, 'last_reduction_price': 0.0, 'last_reduction_amount': 0.0, 'last_reduction_time': None, 'is_reduced': False, 'was_reduced': False, 'reduced_at': None, 'was_reentered': False, 'last_exit_reentry_ready': False, 'last_exit_timestamp': None, 'unrealized_pnl_%': 0.0, 'unrealized_pnl_$': 0.0, 'total_pnl_$': 0.0, 'opened_at': None, 'last_updated': None, 'last_reason': '', 'augment_reason': '', 'reduction_reason': '', 'trade_log': []}

    def _exit_template(self, entry_price=0.0, qty=0.0) -> Dict[str, Any]:
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

    async def get_exit_candidate(self, pk):
        return self.exit_candidates.get(pk)

    async def get_entry_candidate(self, pk):
        return self.entry_candidates.get(pk)

    def get_last_check_time(self, pk):
        return self.last_check_times.get(pk, 0.0)

    async def is_processing(self, pk):
        return self._processing.get(pk, False)

    async def set_processing(self, pk):
        self._processing[pk] = True

    async def clear_processing(self, pk):
        self._processing[pk] = False

    async def register_check(self, pk):
        self.last_check_times[pk] = 0.0  # Always allow re-check in backtest

    async def transition_to_entry(self, account_key, pk, price=0, size=0, is_long=True, status="entry_candidate", **kw):
        self.entry_candidates[pk] = self._entry_template()

    async def transition_to_exit(self, account_key, pk, price=0, size=0, status="active", is_hedge=False, hedge_for=None, **kw):
        self.exit_candidates[pk] = self._exit_template(price, size)

    async def save_tracker(self, account_key="", force=False):
        pass

    async def send_webhook(self, *a, **kw):
        return True

    async def set_trade_cooldown(self, pk, duration=5.0):
        pass

    async def is_trade_cooldown_active(self, pk):
        return False

    def get_tradeable_position_keys_for(self, account_key):
        return self.tradeable_position_keys.get(account_key, set())

    def is_system_lagging(self, account_key=""):
        return False

    async def revert_optimistic_exit(self, *a, **kw):
        pass

    async def apply_optimistic_exit(self, *a, **kw):
        pass

    class _PosService:
        positions = {}
        positions_by_account = {}
        _position_update_timestamps = {}
        def get_long_short_ratio(self, ak=""):
            return {"long": 0, "short": 0, "ratio": 1.0}
        def get_account_positions(self, ak=""):
            return self.positions_by_account.get(ak, self.positions)
        def update_position_timestamp(self, pk, ts=None):
            self._position_update_timestamps[pk] = ts or 0
    positions_service = _PosService()


# ═══════════════════════════════════════════════════════════════════
# BacktestTradeManager — fully mocked trade manager
# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════
# BacktestDataManager — serves .npz data through get_hot_state()
# ═══════════════════════════════════════════════════════════════════
class BacktestDataManager:
    """Mimics FastDataManager — returns .npz indicator data."""
    def __init__(self, trade_manager):
        self.tm = trade_manager
    async def get_hot_state(self, symbol):
        ind = self.tm._indicator_cache.get(symbol, {})
        if not ind:
            return {}, {}, 50, 50, 50, 50, False
        k_1m = float(ind.get("stoch_k_1m", ind.get("stoch_k_3m", ind.get("stoch_k_5m", 50))))
        d_1m = float(ind.get("stoch_d_1m", ind.get("stoch_d_3m", ind.get("stoch_d_5m", 50))))
        k_3m = float(ind.get("stoch_k_3m", ind.get("stoch_k_5m", 50)))
        d_3m = float(ind.get("stoch_d_3m", ind.get("stoch_d_5m", 50)))
        return ind, ind, k_1m, d_1m, k_3m, d_3m, True
    async def get_fresh_price(self, symbol):
        sim_ts = getattr(self.tm, '_sim_ts', 0)
        if sim_ts:
            dt = datetime.utcfromtimestamp(sim_ts).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.now(timezone.utc)
        return self.tm._price_cache.get(symbol, 0.0), dt


# ═══════════════════════════════════════════════════════════════════
# BacktestHedgeEngine — tracks hedge lifecycle
# ═══════════════════════════════════════════════════════════════════
class BacktestHedgeEngine:
    """Mimics HedgeEngine for hedge operations."""
    def __init__(self, trade_manager):
        self.tm = trade_manager
        self.trade_manager = trade_manager
        self.config = trade_manager.config
        self.positions_service = trade_manager.tracker_manager.positions_service
        self.tracker_manager = trade_manager.tracker_manager
        self.data_manager = None  # set after BacktestDataManager created
        self._hedge_cooldowns: Dict[str, float] = {}
    async def _manage_hedge_for_position(self, account_key=None, position_key=None, position=None, qty=0, current_price=0, pnl_pct=0, tracker_data=None, **kw):
        """Check if hedge should be opened/closed for a position based on WT structure.
        Signature matches live: (account_key, position_key, position, qty, current_price, pnl_pct, tracker_data)"""
        if not position_key:
            return
        pos = position or self.tm.positions.get(position_key)
        if not pos or abs(pos.positionAmt) < 0.0001:
            return
        if pos.gain >= -0.5:
            return
        ind = self.tm._indicator_cache.get(pos.symbol, {})
        is_long = pos.position_side == "LONG"
        wt_against = 0
        for tf in ["3m", "15m", "1h", "4h", "D"]:
            wt_bull = ind.get(f"wt_bullish_{tf}", -1)
            if wt_bull < 0:
                continue
            if is_long and not wt_bull:
                wt_against += 1
            elif not is_long and wt_bull:
                wt_against += 1
        if wt_against >= getattr(self.tm.config, 'OBLIGATORY_HEDGE_WT_TFS', 2):
            ak = position_key.split(":")[0] if ":" in position_key else self.tm.tracker_manager.account_keys[0]
            price = self.tm._price_cache.get(pos.symbol, 0)
            if price > 0:
                hedge_pct = getattr(self.tm.config, 'OBLIGATORY_HEDGE_PCT', 1.0)
                hedge_qty = abs(pos.positionAmt) * hedge_pct
                await self.execute_same_symbol_hedge(ak, pos, pos.symbol, pos.position_side, hedge_qty, price)
    async def execute_same_symbol_hedge(self, account_key, position, symbol, origin_side, qty, current_price):
        origin_pk = f"{account_key}:{symbol}_{origin_side}"
        # Cooldown: max 1 hedge per symbol per 15m bar (900s)
        now_ts = getattr(self.tm, '_sim_ts', 0) or time.time()
        last_hedge = self._hedge_cooldowns.get(origin_pk, 0)
        if now_ts - last_hedge < 420:
            return "BLOCKED_HEDGE_COOLDOWN"
        origin_pos = self.tm.positions.get(origin_pk)
        if not origin_pos or abs(origin_pos.positionAmt) * current_price < 1.0:
            return "BLOCKED_TINY_POSITION"
        hedge_side = "SHORT" if origin_side == "LONG" else "LONG"
        hedge_pk = f"{account_key}:{symbol}_{hedge_side}"
        existing = self.tm.positions.get(hedge_pk)
        if existing and abs(existing.positionAmt) * current_price > 1.0:
            return "BLOCKED_HEDGE_EXISTS"
        self._hedge_cooldowns[origin_pk] = now_ts
        side = "SELL" if hedge_side == "SHORT" else "BUY"
        result = await self.tm.execute_trade_action(account_key, hedge_pk, symbol, qty, current_price, side, hedge_side, f"hedge_{int(time.time())}", action="OPEN", reason=f"AUTO_HEDGE_FOR_{origin_side}", is_hedge=True, hedge_for=origin_pk)
        if result == "SUCCESS":
            hedge_record = {"position_key": hedge_pk, "hedge_for": origin_pk, "losing_position_key": origin_pk, "account": account_key, "is_hedge": True, "quantity": qty, "notional_usd": qty * current_price, "price": current_price, "opened_at": now_ts, "hedge_pct_of_losing": (qty * current_price) / max(0.01, abs(origin_pos.positionAmt) * origin_pos.entry_price) * 100, "losing_pnl_at_hedge": origin_pos.gain}
            self.tm.tracker_manager.active_hedges.append(hedge_record)
            if hedge_pk in self.tm.tracker_manager.exit_candidates:
                self.tm.tracker_manager.exit_candidates[hedge_pk]["is_hedge"] = True
                self.tm.tracker_manager.exit_candidates[hedge_pk]["hedge_for"] = origin_pk
        return result
    async def execute_dual_hedge(self, account_key=None, position_key=None, symbol=None, origin_side=None, loss_pct=0, current_price=0, **kw):
        """Execute same-symbol hedge (dual hedge = same + cross, we do same only in backtest)."""
        if not position_key or not symbol or not current_price:
            return {"overall_status": "SKIPPED", "reason": "missing_args"}
        ak = account_key or (position_key.split(":")[0] if ":" in position_key else self.tm.tracker_manager.account_keys[0])
        pos = self.tm.positions.get(position_key)
        if not pos:
            return {"overall_status": "SKIPPED", "reason": "no_position"}
        hedge_pct = getattr(self.tm.config, 'OBLIGATORY_HEDGE_PCT', 1.0)
        hedge_qty = abs(pos.positionAmt) * hedge_pct
        result = await self.execute_same_symbol_hedge(ak, pos, symbol, origin_side or pos.position_side, hedge_qty, current_price)
        return {"overall_status": "success" if result == "SUCCESS" else "SKIPPED", "reason": str(result)}
    def is_hedged(self, position_key):
        """Check if a hedge exists for this position (opposite side of same symbol)."""
        if ":" not in position_key:
            return False
        ak, rest = position_key.split(":", 1)
        if rest.endswith("_LONG"):
            hedge_pk = f"{ak}:{rest.replace('_LONG', '_SHORT')}"
        elif rest.endswith("_SHORT"):
            hedge_pk = f"{ak}:{rest.replace('_SHORT', '_LONG')}"
        else:
            return False
        hedge_pos = self.tm.positions.get(hedge_pk)
        return hedge_pos is not None and abs(hedge_pos.positionAmt) > 0.0001
    def get_active_hedges(self, account_key=None):
        """Return list of active hedge position keys."""
        hedges = []
        for t in self.tm.executed_trades:
            if t.get("is_hedge") and t.get("action") == "OPEN":
                pk = t.get("position_key", "")
                if pk in self.tm.positions and abs(self.tm.positions[pk].positionAmt) > 0.0001:
                    hedges.append({"position_key": pk, "hedge_for": t.get("hedge_for", "")})
        return hedges
    async def scan_and_hedge_losers(self, account_key):
        """BC_988: Same-symbol hedge when wt1_15m goes against AND position losing > 0.25%.
        Replicates the live scan_and_hedge_losers from ez_positions_quick.py HedgeEngine."""
        positions = self.tm.positions_by_account.get(account_key, {})
        _oh_pct = float(getattr(self.tm.config, 'OBLIGATORY_HEDGE_PCT', 1.0))
        _oh_min_loss = float(getattr(self.tm.config, 'OBLIGATORY_HEDGE_MIN_LOSS_PCT', -0.25))
        for pk, pos in list(positions.items()):
            if not pk.startswith(account_key):
                continue
            # Skip if this IS a hedge
            ec = self.tm.tracker_manager.exit_candidates.get(pk, {})
            if ec.get('is_hedge') or ec.get('hedge_for'):
                continue
            # Skip if already has a hedge in active_hedges
            already_hedged = any(h.get('losing_position_key') == pk for h in self.tm.tracker_manager.active_hedges if isinstance(h, dict))
            if already_hedged:
                continue
            qty = abs(pos.positionAmt)
            if qty <= 0 or pos.entry_price <= 0:
                continue
            price = self.tm._price_cache.get(pos.symbol, 0)
            if price <= 0:
                continue
            notional = qty * price
            if notional < 5.0:
                continue
            is_long = pk.endswith('_LONG')
            pnl_pct = ((price - pos.entry_price) / pos.entry_price * 100) if is_long else ((pos.entry_price - price) / pos.entry_price * 100)
            if pnl_pct >= _oh_min_loss:
                continue
            logger.debug(f"[HEDGE_SCAN] {pk}: pnl={pnl_pct:.2f}% — checking WT")
            # BC_988: 15m WT is the SOLE trigger
            ind = self.tm._indicator_cache.get(pos.symbol, {})
            wt1_15m = float(ind.get('wt1_15m', 0))
            wt2_15m = float(ind.get('wt2_15m', 0))
            wt_against = (is_long and wt1_15m < wt2_15m) or (not is_long and wt1_15m > wt2_15m)
            if not wt_against:
                continue
            # Check not already hedged (opposite side exists)
            losing_side = 'LONG' if is_long else 'SHORT'
            hedge_side = 'SHORT' if is_long else 'LONG'
            sym = pos.symbol
            hedge_pk = f"{account_key}:{sym}_{hedge_side}"
            existing = positions.get(hedge_pk)
            if existing and abs(existing.positionAmt) > 0:
                continue
            hedge_qty = qty * _oh_pct
            if hedge_qty * price < 5.0:
                continue
            logger.warning(f"[HEDGE_OPEN] {pk}: opening {hedge_side} hedge for {losing_side} loser at {pnl_pct:.2f}% loss, qty={hedge_qty:.4f}")
            await self.execute_same_symbol_hedge(account_key, pos, sym, losing_side, hedge_qty, price)
    async def try_aggressive_pyramid(self, *a, **kw):
        return False
    async def check_hedge_needed(self, *a, **kw):
        return False
    async def process_hedge_candidates(self, *a, **kw):
        pass


class BacktestTradeManager:
    """Complete mock of MultiAccountTradeManager for process_position()."""

    def __init__(self, account_keys: List[str], symbols: List[str]):
        self.accounts = {ak: type("Acc", (), {"key": ak, "name": ak})() for ak in account_keys}
        self.positions: Dict[str, BacktestPosition] = {}
        self.positions_by_account: Dict[str, Dict[str, BacktestPosition]] = {ak: {} for ak in account_keys}
        # Build config with all required attributes + auto-default for missing ones
        class BacktestConfig:
            _DEFAULTS = {"VERBOSE": False, "DEBUG": False, "DRY_RUN": False, "PAPER_MODE": False}
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)
                if name in self._DEFAULTS:
                    return self._DEFAULTS[name]
                # Fall through to real config module for any attribute not explicitly set
                if hasattr(config, name):
                    return getattr(config, name)
                # Return sensible defaults: lists for _ACCOUNTS/_SYMBOLS/_KEYS, dicts for _MAP/_MAPPING/_OVERRIDES
                if name.endswith(("_ACCOUNTS", "_SYMBOLS", "_KEYS", "_LEVELS")): return []
                if name.endswith(("_MAP", "_MAPPING", "_OVERRIDES")): return {}
                return False if name.isupper() else None
        self.config = BacktestConfig()
        # Copy existing config attrs
        for attr in dir(config):
            if attr.isupper() and not attr.startswith("_"):
                setattr(self.config, attr, getattr(config, attr))
        # Backtest-only overrides — ONLY things that MUST differ from live (paths, sizing for backtest capital, account keys)
        # ALL trading logic params come from config.py as-is. That's how backtesting works.
        self.config.ACCOUNT_KEYS = account_keys
        self.config.STRICT_NO_LOSS_ACCOUNTS = account_keys
        self.config.BLACKLIST_SYMBOLS = []
        self.config.BASE_PATH = str(BASE_PATH)
        self.config.DATA_DIR = BASE_PATH / "data"
        # Backtest sizing — scaled to backtest capital, not live account size
        self.config.MIN_POSITION_SIZE = 0.9
        self.config.START_POSITION_SIZE = 18.0
        self.config.MAX_POSITION_SIZE = 800.0
        self.config.MAX_ORDER_VALUE = 800.0
        # Apply config overrides from sweep (V5_CONFIG_OVERRIDES env var)
        override_path = os.environ.get("V5_CONFIG_OVERRIDES", "")
        if override_path and os.path.exists(override_path):
            with open(override_path) as f:
                overrides = json.load(f)
            for k, v in overrides.items():
                setattr(self.config, k, v)
            logger.info(f"Applied {len(overrides)} config overrides from {override_path}")
        _cfg_ref = self.config
        def _get_account_setting(ak, key, default=None):
            overrides = getattr(_cfg_ref, 'ACCOUNT_OVERRIDES', {})
            if ak in overrides and key in overrides[ak]:
                return overrides[ak][key]
            return getattr(_cfg_ref, key, default)
        self.config.get_account_setting = _get_account_setting
        def _getattr_fallback(key, default=None):
            return getattr(_cfg_ref, key, default)
        # Make config support .get() calls
        self.config.get = _getattr_fallback
        self.config.ACCOUNT_TP_PCT = {}  # NO fixed TP — exits via technicals ONLY
        self.recently_queued_signals: Dict[str, float] = {}
        self.processing_keys: Set[str] = set()
        self.stop_levels: Dict[str, List] = {}
        self.stop_breach_tracker: Dict[str, float] = {}
        self.reentry_data: Dict[str, Dict] = {}
        self.indicators_snapshot: Dict[str, Any] = {}
        self.min_qty: Dict[str, float] = {sym: 0.001 for sym in symbols}
        self.position_last_processed: Dict[str, float] = {}
        self.orders_in_limbo: Dict[str, Dict] = {}
        self.order_deduplication: Dict[str, Any] = {}
        self.active_order_locks: Dict[str, Any] = {}
        self.last_monitored_positions: Dict[str, float] = {}
        self.dedupe_lock = asyncio.Lock()
        self._alignment_tp_cooldown: Dict[str, float] = {}
        self._allowed_accounts = set(account_keys)
        self.positions_ready_event = asyncio.Event()
        self.positions_ready_event.set()
        self._monitor_semaphore = asyncio.Semaphore(200)
        # Augment guards required by execute_now (the REAL 652-line filter)
        self.augmented_positions: Dict[str, Any] = {}
        self.augmentation_cooldown_map: Dict[str, float] = {}
        self.recent_augmentations: Dict[str, float] = {}
        self._last_augment_save_time: float = 0.0
        self._brake_cache: Dict[str, Any] = {}
        self.active_maker_orders: Dict[str, Any] = {}
        self.managed_maker_order_registry: Dict[str, Any] = {}
        self.pending_reentries: Dict[str, Any] = {}
        self.reduced_positions: Dict[str, Any] = {}
        self.position_callback_manager = None
        self.service = None  # set after tracker_manager is created
        # Mock Binance client for execute_now's order placement
        class _MockClient:
            async def futures_create_order(self, **kw):
                return {"orderId": 99999, "status": "FILLED", "avgPrice": str(kw.get("price", "0")), "executedQty": str(kw.get("quantity", "0"))}
        self._clients = {ak: _MockClient() for ak in account_keys}
        self.accounts_clients = self._clients
        # Symbol routing (allow all)
        for ak in account_keys:
            setattr(self, f"symbols_{ak}_long", set(symbols))
            setattr(self, f"symbols_{ak}_short", set(symbols))
            setattr(self, f"symbols_{ak}", set(symbols))
        # Service mocks
        self.redis_manager = type("MockRedis", (), {"get": self._redis_get, "set": self._redis_set, "delete": self._redis_delete, "hdel": self._redis_delete, "data": {}})()
        self.tracker_manager = BacktestTrackerManager()
        self.tracker_manager.account_keys = account_keys
        self.hedge_engine = BacktestHedgeEngine(self)
        self.data_manager = BacktestDataManager(self)
        self.hedge_engine.data_manager = self.data_manager
        self.tracker_manager.hedge_engine = self.hedge_engine
        self.service = self.tracker_manager.positions_service
        self.positions_service = self.tracker_manager.positions_service
        self.symbols = set(symbols)
        self._auto_hedge_cd: Dict[str, float] = {}
        self.price_cache: Dict[str, float] = {}
        self.tradeable_keys = set()
        self._sim_ts = 0  # simulation timestamp, set each bar
        # Indicator + price caches
        self._indicator_cache: Dict[str, Dict] = {}
        self._price_cache: Dict[str, float] = {}
        # Trade capture log
        self.executed_trades: List[Dict] = []

    async def _redis_get(self, key):
        return self.redis_manager.data.get(key)

    async def _redis_set(self, key, val, ex=None):
        self.redis_manager.data[key] = val

    async def _redis_delete(self, *keys):
        for k in keys:
            self.redis_manager.data.pop(k, None)

    async def load_tradeable(self) -> List[str]:
        return list(self.tradeable_keys)

    async def get_position(self, pk: str) -> Optional[BacktestPosition]:
        return self.positions.get(pk)

    async def _recover_position_safe(self, position_key, account_key=None, symbol=None, position_side=None):
        """Fallback position recovery — check all dicts."""
        pos = self.positions.get(position_key)
        if pos:
            return pos
        if account_key:
            pos = self.positions_by_account.get(account_key, {}).get(position_key)
        return pos

    def validate_order_side(self, side, position_side, action, position_key):
        return side, True

    def is_symbol_allowed(self, account_key=None, symbol=None, position_key=None):
        return True

    def _check_htf_confirmation(self, *args, **kwargs):
        return True

    def allows_side(self, account_key=None, side=None):
        return True

    async def clear_all_cooldowns_for_position(self, position_key):
        self.recently_queued_signals.pop(position_key, None)
        self.processing_keys.discard(position_key)
        self.augmentation_cooldown_map.pop(position_key, None)
        self.recent_augmentations.pop(position_key, None)

    async def is_duplicate_order(self, position_key, quantity, side, unique_id=""):
        return False

    async def mark_order_executed(self, pk, side):
        pass

    async def clear_dedupe_key(self, pk, side, uid=None):
        pass

    async def execute_trade_action(self, account_key, position_key, symbol, quantity, current_price, side, position_side, unique_id, is_full_close=False, action='', reason='', override_qty=None, is_hedge=False, hedge_for=None, **kwargs):
        """CAPTURE trade instead of executing on exchange."""
        quantity = abs(float(quantity or 0))  # qty NEVER negative
        if quantity <= 0:
            return "BLOCKED_ZERO_QTY"
        trade = {"timestamp": self._sim_ts, "wall_time": time.time(), "account_key": account_key, "position_key": position_key, "symbol": symbol, "quantity": quantity, "price": current_price, "side": side, "position_side": position_side, "action": action, "reason": reason, "is_full_close": is_full_close, "is_hedge": is_hedge, "hedge_for": hedge_for, "unique_id": unique_id}
        if action in ("CLOSE", "REDUCE", "QUICK_CLOSE"):
            pos = self.positions.get(position_key)
            if pos and pos.entry_price > 0:
                trade["entry_price"] = pos.entry_price
                trade["gain_pct"] = pos.gain
                pnl = abs(quantity) * pos.entry_price * (pos.gain / 100.0)
                trade["pnl_usd"] = round(pnl, 4)
        self.executed_trades.append(trade)
        # Apply trade to positions
        self._apply_trade(trade)
        return "SUCCESS"

    # execute_now = the REAL one from MultiAccountTradeManager, bound in patch_indicator_provider()

    # Methods called by the REAL execute_now that need mock implementations:
    async def _close_associated_hedge(self, *a, **kw):
        pass
    async def _get_augmented_timestamp(self, pk, *a, **kw):
        return self.augmented_positions.get(pk, {}).get("timestamp", None)
    async def _handle_hedge_guard(self, *a, **kw):
        return False
    def _should_bypass_post_fill_lock(self, *a, **kw):
        return False
    async def cancel_order_with_confirmation(self, *a, **kw):
        return True
    async def cleanup_old_dedupe_keys(self, *a, **kw):
        pass
    async def clear_all_cooldowns_for_position(self, pk, *a, **kw):
        self.augmentation_cooldown_map.pop(pk, None)
        self.recent_augmentations.pop(pk, None)
    async def clear_recent_signal(self, *a, **kw):
        pass
    async def force_clear_execution_lock(self, *a, **kw):
        pass
    async def handle_filled_maker(self, *a, **kw):
        pass
    async def send_foothold_webhook(self, *a, **kw):
        pass
    async def send_webhook(self, position_key=None, account_key=None, symbol=None, positionAmt=0, quantity=0, price=0, side="", position_side="", unique_id="", is_full_close=False, reason="", **kw):
        """Mock webhook — execute trade directly (like Finandy would)."""
        if quantity > 0 and price > 0 and position_key:
            ak = account_key or position_key.split(":")[0]
            ps = position_side or (position_key.split("_")[-1] if "_" in position_key else "LONG")
            sym = symbol or (position_key.split(":")[1].split("_")[0] if ":" in position_key else "")
            await self.execute_trade_action(ak, position_key, sym, abs(quantity), price, side, ps, unique_id or f"wh_{position_key}", is_full_close=is_full_close, action="REDUCE" if not is_full_close else "CLOSE", reason=reason)
        return True
    async def try_add_order_redis(self, *a, **kw):
        return True

    async def place_maker_order(self, account_key, position_key, symbol, positionAmt, current_price, qty_abs, side, position_side, unique_id, reason):
        await self.execute_trade_action(account_key, position_key, symbol, qty_abs, current_price, side, position_side, unique_id, action="MAKER", reason=reason)
        return True, qty_abs

    def _apply_trade(self, trade):
        """Update internal position state after a trade."""
        pk = trade["position_key"]
        sym = trade["symbol"]
        side = trade["side"]  # BUY or SELL
        pos_side = trade["position_side"]
        qty = abs(trade["quantity"])
        price = trade["price"]
        action = trade.get("action", "")
        is_open = side == "BUY" and pos_side == "LONG" or side == "SELL" and pos_side == "SHORT"
        is_reduce = not is_open
        pos = self.positions.get(pk)
        is_hedge = trade.get("is_hedge", False)
        hedge_for = trade.get("hedge_for", "")
        if pos is None and is_open:
            # New position
            pos = BacktestPosition(sym, pos_side, price, qty)
            pos.entry_reason = trade.get("reason", "")
            pos.position_key = pk
            self.positions[pk] = pos
            ak = trade.get("account_key", "ang")
            if ak in self.positions_by_account:
                self.positions_by_account[ak][pk] = pos
            self.tradeable_keys.add(pk)
            self.tracker_manager._positions[pk] = pos
            self.tracker_manager.positions_service.positions[pk] = pos
            # Create exit_candidate with hedge flag
            ec = self.tracker_manager._exit_template(price, qty)
            if is_hedge:
                ec["is_hedge"] = True
                ec["hedge_for"] = hedge_for
                # Register in active_hedges — execute_now checks this before allowing more hedges
                self.tracker_manager.active_hedges.append({
                    "position_key": pk,
                    "losing_position_key": hedge_for,
                    "symbol": sym,
                    "side": pos_side,
                    "account": trade.get("account_key", "ang"),
                    "qty": qty,
                    "entry_price": price,
                    "id": f"hedge_{pk}_{int(self._sim_ts)}",
                })
            self.tracker_manager.exit_candidates[pk] = ec
        elif pos and is_open:
            # Augment — cap at MAX_POSITION_SIZE
            max_value = getattr(self.config, 'MAX_POSITION_SIZE', 800.0)
            current_value = abs(pos.positionAmt) * price
            if current_value >= max_value:
                return "BLOCKED_MAX_POSITION"
            add_qty = min(qty, (max_value - current_value) / price) if price > 0 else qty
            if add_qty <= 0:
                return "BLOCKED_MAX_POSITION"
            total_qty = abs(pos.positionAmt) + abs(add_qty)
            pos.entry_price = (pos.entry_price * abs(pos.positionAmt) + price * abs(add_qty)) / total_qty
            pos.positionAmt = total_qty
            pos.augmented_count = getattr(pos, 'augmented_count', 0) + 1
            sim_dt = datetime.utcfromtimestamp(self._sim_ts).replace(tzinfo=timezone.utc) if self._sim_ts else datetime.now(timezone.utc)
            pos.last_augmentation_time = sim_dt
            pos.last_augmentation_price = price
        elif pos and is_reduce:
            # Reduce or close — positionAmt NEVER goes negative
            reduce_qty = min(qty, abs(pos.positionAmt))
            pos.positionAmt = max(0.0, abs(pos.positionAmt) - reduce_qty)
            if pos.positionAmt < 0.0001 or trade.get("is_full_close"):
                # Fully closed — keep position as empty slot (positionAmt=0) for reentry
                # Like live system: positions STAY in dicts, just with zero qty
                pos.positionAmt = 0.0
                pos.max_gain = 0.0  # reset for next lifecycle
                pos.realized_pnl += reduce_qty * price * (pos.gain / 100) if pos.entry_price > 0 else 0
                # Remove from active_hedges if this was a hedge
                self.tracker_manager.active_hedges = [h for h in self.tracker_manager.active_hedges if h.get("position_key") != pk]
                # Save exit info for TIER1/TIER2 reentry
                self.tracker_manager.last_exit_prices[pk] = price
                self.tracker_manager.last_exit_times[pk] = float(self._sim_ts) if self._sim_ts else time.time()
            else:
                sim_dt = datetime.utcfromtimestamp(self._sim_ts).replace(tzinfo=timezone.utc) if self._sim_ts else datetime.now(timezone.utc)
                pos.last_reduction_time = sim_dt
                pos.last_reduction_price = price

    def update_indicators(self, symbol: str, indicators: Dict):
        self._indicator_cache[symbol] = indicators

    def update_price(self, symbol: str, price: float):
        self._price_cache[symbol] = price
        self.price_cache[symbol] = price
        for pk, pos in self.positions.items():
            if pos.symbol == symbol:
                pos.update_price(price)


# ═══════════════════════════════════════════════════════════════════
# Indicator provider — patches the `ii()` function in ez_manage
# ═══════════════════════════════════════════════════════════════════
def patch_indicator_provider(trade_manager: BacktestTradeManager):
    """Monkey-patch ez_manage.ii() to return .npz data instead of Redis."""
    import ez_manage
    # Bind the REAL execute_now from MultiAccountTradeManager to our BacktestTradeManager
    # This is the 652-line filter that blocks bad trades — the ACTUAL gate
    real_execute_now = ez_manage.MultiAccountTradeManager.execute_now
    import types
    trade_manager.execute_now = types.MethodType(real_execute_now, trade_manager)
    logger.info("Bound REAL execute_now (652 lines of filters) to BacktestTradeManager")
    # Mock TradeVerifier to prevent crash in execute_now
    class MockTradeVerifier:
        def __init__(self, *a, **kw):
            self.positions_service = trade_manager.tracker_manager.positions_service
            self.positions_by_account = trade_manager.positions_by_account
            self.active_maker_orders = {}
            self.managed_maker_order_registry = {}
            self.accounts = trade_manager.accounts
        async def verify_trade(self, *a, **kw):
            return True
    ez_manage.TradeVerifier = MockTradeVerifier
    # Patch global config module so ez_positions_quick reads correct values
    config.ACCOUNT_TP_PCT = trade_manager.config.ACCOUNT_TP_PCT
    original_ii = ez_manage.ii if hasattr(ez_manage, 'ii') else None
    async def backtest_ii(tm, symbol, **kwargs):
        return trade_manager._indicator_cache.get(symbol, {})
    ez_manage.ii = backtest_ii
    # Also patch price()
    original_price = ez_manage.price if hasattr(ez_manage, 'price') else None
    async def backtest_price(symbol, position=None, withts=False, **kwargs):
        p = trade_manager._price_cache.get(symbol, 0.0)
        if withts:
            sim_ts = getattr(trade_manager, '_sim_ts', 0)
            dt = datetime.utcfromtimestamp(sim_ts).replace(tzinfo=timezone.utc) if sim_ts else datetime.now(timezone.utc)
            return p, dt
        return p
    ez_manage.price = backtest_price
    # Patch get_current_price
    if hasattr(ez_manage, 'get_current_price'):
        async def backtest_get_current_price(symbol, **kwargs):
            p = trade_manager._price_cache.get(symbol, 0.0)
            sim_ts = getattr(trade_manager, '_sim_ts', 0)
            dt = datetime.utcfromtimestamp(sim_ts).replace(tzinfo=timezone.utc) if sim_ts else datetime.now(timezone.utc)
            return p, dt
        ez_manage.get_current_price = backtest_get_current_price
    # Patch queue_trade_action to execute immediately instead of queueing
    original_queue = ez_manage.queue_trade_action
    async def backtest_queue_trade_action(order_queue, tm, position_key=None, action="", reason="", conviction=0.5, override_qty=None, account_key=None):
        if not position_key or not action or action == "NO_ACTION":
            return "NO_ACTION"
        ak, symbol, position_side = parse_position_key(position_key)
        if not account_key:
            account_key = ak
        is_long = position_side == "LONG"
        current_price = tm._price_cache.get(symbol, 0.0)
        if current_price <= 0:
            return "NO_PRICE"
        pos = tm.positions.get(position_key)
        pos_amt = abs(float(getattr(pos, 'positionAmt', 0.0))) if pos else 0.0
        pos_min_qty = tm.min_qty.get(symbol, 0.001)
        if action.upper() in ("OPEN", "REENTRY", "AUGMENT"):
            side = "BUY" if is_long else "SELL"
            qty = override_qty or (tm.config.START_POSITION_SIZE / current_price)
            is_full_close = False
        elif action.upper() in ("CLOSE", "QUICK_CLOSE"):
            side = "SELL" if is_long else "BUY"
            qty = override_qty or pos_amt
            is_full_close = True
        elif action.upper() == "REDUCE":
            side = "SELL" if is_long else "BUY"
            pos_min_qty = max(tm.config.MIN_POSITION_SIZE / current_price, 1.2 * tm.min_qty.get(symbol, 0.0001)) if current_price > 0 else 0.001
            qty = override_qty or max(pos_amt - pos_min_qty, pos_amt * 0.1)
            is_full_close = False
        else:
            return f"UNKNOWN_ACTION_{action}"
        if qty <= 0:
            qty = tm.config.START_POSITION_SIZE / current_price
        # Call the REAL execute_trade_wrapper with ALL its filters
        from ez_positions_quick import execute_trade_wrapper as _real_etw
        success, msg = await _real_etw(tm, tm.tracker_manager, tm.hedge_engine, account_key, position_key, pos_amt, action, current_price, qty, reason, already_locked=True, is_hedge=False, verify_via_websocket=False, data_manager=tm.data_manager)
        return msg if success else f"BLOCKED:{msg}"
    ez_manage.queue_trade_action = backtest_queue_trade_action
    # execute_trade_wrapper runs with ALL its filters — only mock websocket and price
    import ez_positions_quick as epq
    # Patch verify_trade_via_websocket
    if hasattr(epq, 'verify_trade_via_websocket'):
        async def backtest_verify(*a, **kw):
            return True
        epq.verify_trade_via_websocket = backtest_verify
    # Patch get_current_price in ez_positions_quick
    if hasattr(epq, 'get_current_price'):
        async def epq_get_price(symbol, **kw):
            sim_ts = getattr(trade_manager, '_sim_ts', 0)
            dt = datetime.utcfromtimestamp(sim_ts).replace(tzinfo=timezone.utc) if sim_ts else datetime.now(timezone.utc)
            return trade_manager._price_cache.get(symbol, 0.0), dt
        epq.get_current_price = epq_get_price
    # Patch time.time() and datetime.now() to return simulation time.
    # This is CRITICAL: live code uses time.time() for cooldowns, staleness, and
    # minutes_since() calculations. Without this, all time-based gates are broken.
    # NO time/datetime patching — breaks safe_datetime() everywhere
    # Patch ii and price in ez_positions_quick too
    if hasattr(epq, 'ii'):
        epq.ii = backtest_ii
    if hasattr(epq, 'price'):
        epq.price = backtest_price
    # Patch symbol_tracker in both modules
    if hasattr(epq, 'symbol_tracker'):
        class MockST2:
            async def track_stage(self, *a, **kw): pass
            async def track_completion(self, *a, **kw): pass
        epq.symbol_tracker = MockST2()
    if hasattr(ez_manage, 'symbol_tracker'):
        class MockSymbolTracker:
            async def track_stage(self, *a, **kw): pass
            async def track_completion(self, *a, **kw): pass
        ez_manage.symbol_tracker = MockSymbolTracker()
    # Copy key config values into the ORIGINAL config modules (don't replace the module)
    import config as _orig_config
    for attr in dir(trade_manager.config):
        if attr.isupper() and not attr.startswith('_'):
            try:
                val = getattr(trade_manager.config, attr)
                if not isinstance(val, dict) or attr in ('ACCOUNT_TP_PCT',):
                    setattr(_orig_config, attr, val)
            except: pass
    return original_ii, original_price


# ═══════════════════════════════════════════════════════════════════
# V5 Engine
# ═══════════════════════════════════════════════════════════════════
class V5Engine:
    def __init__(self, mode: str, symbols: List[str], start_date: str, capital: float, account_key: str = "ang"):
        self.mode = mode
        self.symbols = symbols
        self.start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        self.capital = capital
        self.account_key = account_key
        npz_dir, self.resolution = get_npz_dir(mode)
        logger.info(f"Using {npz_dir} ({self.resolution} resolution)")
        self.stores: Dict[str, IndicatorStore] = {}
        for sym in symbols:
            path = npz_dir / f"{sym}.npz"
            if path.exists():
                self.stores[sym] = IndicatorStore(str(path))
        logger.info(f"Loaded {len(self.stores)}/{len(symbols)} symbols")
        self.tm = BacktestTradeManager([account_key], list(self.stores.keys()))
        self._orig_ii, self._orig_price = patch_indicator_provider(self.tm)
        self.equity_curve: List[float] = []
        self.realized_pnl_total = 0.0
        # Build tradeable keys for ALL symbols (both LONG and SHORT)
        # Positions are SEEDED at bar 0 in run() — not here (need prices first)
        for sym in self.stores:
            for side in ["LONG", "SHORT"]:
                pk = construct_position_key(account_key, sym, side)
                if pk:
                    self.tm.tradeable_keys.add(pk)
                    self.tm.tracker_manager.tradeable_keys.add(pk)
                    self.tm.tracker_manager.tradeable_keys_cache.add(pk)
                    if account_key not in self.tm.tracker_manager.tradeable_position_keys:
                        self.tm.tracker_manager.tradeable_position_keys[account_key] = set()
                    self.tm.tracker_manager.tradeable_position_keys[account_key].add(pk)
                    self.tm.tracker_manager.entry_candidates[pk] = self.tm.tracker_manager._entry_template()

    async def run(self):
        # Collect timestamps
        all_ts = set()
        for sym, store in self.stores.items():
            for t in store.timestamps:
                t_int = int(t)
                if t_int >= self.start_ts:
                    all_ts.add(t_int)
        sorted_ts = sorted(all_ts)
        if not sorted_ts:
            logger.error("No data!")
            return
        logger.info(f"Simulation: {len(sorted_ts)} bars, {len(self.stores)} symbols, {self.mode}")
        self.tm.tracker_manager._hedges_lock = asyncio.Lock()
        # === SEED ALL POSITIONS AT BAR 0 ===
        # Every symbol gets both LONG + SHORT from the first kline.
        # The system manages them (augment, reduce, hedge, reentry) — positions persist to the end.
        first_ts = sorted_ts[0]
        self.tm._sim_ts = first_ts  # Set BEFORE creating positions so datetime.now() works
        opened_at = datetime.utcfromtimestamp(first_ts).replace(tzinfo=timezone.utc)
        seeded = 0
        start_size = float(getattr(self.tm.config, 'START_POSITION_SIZE', 18.0))
        for sym, store in self.stores.items():
            idx = store.ts_to_idx.get(first_ts, -1)
            if idx < 0:
                # Find nearest bar
                for t in sorted_ts[:10]:
                    idx = store.ts_to_idx.get(t, -1)
                    if idx >= 0:
                        break
            if idx < 0:
                continue
            price = store.price(idx)
            if price <= 0:
                continue
            # Update price cache so process_position can find it
            self.tm.update_price(sym, price)
            self.tm.update_indicators(sym, store.build_indicator_dict(idx))
            for side in ["LONG", "SHORT"]:
                pk = construct_position_key(self.account_key, sym, side)
                if not pk:
                    continue
                qty = start_size / price
                pos = BacktestPosition(sym, side, price, qty, opened_at=opened_at)
                pos.position_key = pk
                pos.max_quantity = qty
                pos.total_entry_qty = qty
                pos.update_price(price)
                self.tm.positions[pk] = pos
                self.tm.positions_by_account[self.account_key][pk] = pos
                self.tm.tracker_manager._positions[pk] = pos
                self.tm.tracker_manager.positions_service.positions[pk] = pos
                self.tm.tracker_manager.positions_service.positions_by_account.setdefault(self.account_key, {})[pk] = pos
                self.tm.tracker_manager.exit_candidates[pk] = self.tm.tracker_manager._exit_template(price, qty)
                # Record as executed trade so PnL accounting works
                self.tm.executed_trades.append({"timestamp": first_ts, "wall_time": time.time(), "account_key": self.account_key, "position_key": pk, "symbol": sym, "quantity": qty, "price": price, "side": "BUY" if side == "LONG" else "SELL", "position_side": side, "action": "OPEN", "reason": "SEED_POSITION", "is_full_close": False, "is_hedge": False, "hedge_for": None, "unique_id": f"seed_{pk}"})
                seeded += 1
        # Populate tradeable_keys from ALL seeded positions — required for entries/reentries
        for pk in self.tm.positions:
            self.tm.tradeable_keys.add(pk)
            self.tm.tracker_manager.tradeable_keys.add(pk)
            self.tm.tracker_manager.tradeable_keys_cache.add(pk)
        logger.info(f"Seeded {seeded} positions across {len(self.stores)} symbols at bar 0 (${start_size}/pos), {len(self.tm.tradeable_keys)} tradeable_keys")
        self.tm.tracker_manager._exit_candidates_lock = asyncio.Lock()
        self.tm.tracker_manager._entry_candidates_lock = asyncio.Lock()
        _real_time = time.time
        t0 = _real_time()
        report_every = max(1, len(sorted_ts) // 20)
        order_queue = OrderQueue(self.tm)
        # CRITICAL: Patch time.time() AND datetime.now()/utcnow() in ez_manage + ez_positions_quick.
        # Without this, ALL staleness checks, cooldowns, and minutes_since() use wall clock.
        # minutes_since() uses datetime.now(timezone.utc) — not time.time() — so BOTH must be patched.
        # 237 calls to datetime.now/utcnow across the two modules were previously unpatched,
        # making every backtest result wrong (cooldowns instant, staleness always triggered).
        import ez_manage as _em_mod
        import ez_positions_quick as _epq_mod
        _sim_ts_ref = [0]  # mutable ref so lambda captures updates
        # Patch time.time() in both modules
        class _FakeTime:
            """Drop-in replacement for `time` module that returns sim time for time()."""
            def time(self):
                return float(_sim_ts_ref[0])
            def perf_counter(self):
                return float(_sim_ts_ref[0])
            def sleep(self, *a):
                pass
            def __getattr__(self, name):
                return getattr(time, name)
        _em_mod.time = _FakeTime()
        _epq_mod.time = _FakeTime()
        # Patch datetime.now()/utcnow() — can't subclass datetime (breaks isinstance checks).
        # Instead: create a wrapper that delegates everything EXCEPT now()/utcnow() to real datetime.
        # Use __instancecheck__ so isinstance(real_dt, _SimDT) returns True.
        _real_datetime_class = datetime
        def _sim_now(tz=None):
            ts = float(_sim_ts_ref[0])
            if ts > 0:
                try:
                    return _real_datetime_class.fromtimestamp(ts, tz=tz or timezone.utc)
                except (OSError, OverflowError, ValueError):
                    pass
            return _real_datetime_class.now(tz)
        def _sim_utcnow():
            ts = float(_sim_ts_ref[0])
            if ts > 0:
                try:
                    return _real_datetime_class.utcfromtimestamp(ts)
                except (OSError, OverflowError, ValueError):
                    pass
            return _real_datetime_class.utcnow()
        # Directly patch minutes_since (the #1 critical function — 237+ datetime.now() calls depend on this pattern)
        _orig_minutes_since = _em_mod.minutes_since
        def _sim_minutes_since(timestamp_obj, now=None):
            if now is None:
                now = _sim_now(timezone.utc)
            return _orig_minutes_since(timestamp_obj, now=now)
        _em_mod.minutes_since = _sim_minutes_since
        # Also patch it in ez_positions_quick if it imported minutes_since
        if hasattr(_epq_mod, 'minutes_since'):
            _epq_mod.minutes_since = _sim_minutes_since
        # Patch all datetime.now() and datetime.utcnow() call sites by replacing the module-level
        # `datetime` with a proxy that overrides only now/utcnow but passes isinstance checks.
        # We use a metaclass trick: the proxy IS datetime (same __instancecheck__) but overrides class methods.
        class _DatetimeProxy:
            """Proxy that wraps datetime class. Overrides now()/utcnow(), delegates everything else.
            isinstance(x, _DatetimeProxy) works because we override __instancecheck__."""
            def __instancecheck__(cls, instance):
                return isinstance(instance, _real_datetime_class)
            def __subclasscheck__(cls, subclass):
                return issubclass(subclass, _real_datetime_class)
            def __call__(cls, *args, **kwargs):
                return _real_datetime_class(*args, **kwargs)
        class _SimDatetimeMeta(type):
            def __instancecheck__(cls, instance):
                return isinstance(instance, _real_datetime_class)
            def __subclasscheck__(cls, subclass):
                if subclass is _real_datetime_class:
                    return True
                return issubclass(subclass, _real_datetime_class)
        class _SimDatetime(metaclass=_SimDatetimeMeta):
            """Drop-in replacement for datetime class with sim time for now()/utcnow()."""
            now = staticmethod(_sim_now)
            utcnow = staticmethod(_sim_utcnow)
            def __class_getitem__(cls, item):
                return _real_datetime_class.__class_getitem__(item)
            def __new__(cls, *args, **kwargs):
                return _real_datetime_class(*args, **kwargs)
            def __getattr__(name):
                return getattr(_real_datetime_class, name)
        # Proxy all other class methods/attributes from real datetime
        for _attr in ('fromtimestamp', 'utcfromtimestamp', 'fromisoformat', 'strptime',
                       'combine', 'min', 'max', 'resolution', 'today',
                       'fromordinal', 'isoformat'):
            if hasattr(_real_datetime_class, _attr):
                setattr(_SimDatetime, _attr, getattr(_real_datetime_class, _attr))
        _em_mod.__dict__['datetime'] = _SimDatetime
        _epq_mod.__dict__['datetime'] = _SimDatetime
        for step, ts in enumerate(sorted_ts):
            _sim_ts_ref[0] = float(ts)
            # Update indicators + prices for all symbols at this bar
            for sym, store in self.stores.items():
                idx = store.ts_to_idx.get(ts, -1)
                if idx < 0:
                    continue
                price = store.price(idx)
                if price <= 0:
                    continue
                indicators = store.build_indicator_dict(idx)
                self.tm.update_price(sym, price)
                self.tm.update_indicators(sym, indicators)
            self.tm._sim_ts = ts
            # Clear reduce lock so each bar can reduce (live checks seconds apart, we check bars apart)
            # Each 3m bar = 180s which is >> 15s HARD_REDUCE_LOCK cooldown
            from ez_manage import _recent_reduces
            _recent_reduces.clear()
            # Sync sim time to all positions so time-based checks (minutes_since, cooldowns) work
            sim_dt = datetime.utcfromtimestamp(ts).replace(tzinfo=timezone.utc)
            # Refresh position gains AND sync tracker_manager positions
            for pk, pos in list(self.tm.positions.items()):
                if abs(pos.positionAmt) > 0 and pos.entry_price > 0:
                    p = self.tm._price_cache.get(pos.symbol, 0)
                    if p > 0:
                        pos.update_price(p)
                pos.last_updated = sim_dt
                pos.mark_price_last_updated = sim_dt
                self.tm.tracker_manager._positions[pk] = pos
                self.tm.tracker_manager.positions_service.positions[pk] = pos
                self.tm.tracker_manager.positions_service.positions_by_account.setdefault(self.account_key, {})[pk] = pos
            # === STEP A: process_position() on ALL active positions (exits, reduces, augments, hedges) ===
            active_pks = [pk for pk, pos in self.tm.positions.items() if abs(pos.positionAmt) > 0.0001]
            for pk in active_pks:
                self.tm.processing_keys.discard(pk)
                self.tm.recently_queued_signals.pop(pk, None)
                try:
                    result = await process_position(account_key=self.account_key, position_key=pk, order_queue=order_queue, trade_manager=self.tm, force=True)
                    if step < 5 and result:
                        logger.info(f"  [DBG] process_position({pk}) = {str(result)[:120]}")
                except Exception as e:
                    if step < 20:
                        logger.warning(f"  [DBG] process_position EXIT error {pk}: {type(e).__name__}: {str(e)[:200]}")
                    elif "NoneType" not in str(e) and "ZERO_AMT" not in str(e):
                        logger.debug(f"process_position exit error {pk}: {type(e).__name__}: {str(e)[:120]}")
            # NOTE: process_position() returns ZERO_AMT for empty keys — entries come ONLY from
            # check_entry_candidates_for_account (Step D). This matches live where entries come from
            # ez_positions_quick rate()-based signals, NOT from process_position.
            # === STEP C: check_exit_candidates (rate()-based: CYCLE_TP, ACCOUNT_TP, STOCH_CROSS, SBA) ===
            active_pks = [pk for pk, pos in self.tm.positions.items() if abs(pos.positionAmt) > 0.0001]
            if active_pks:
                if step < 3:
                    logger.warning(f"[STEP C] check_exit on {len(active_pks)} active positions: {active_pks[:3]}")
                n_before_exit = len(self.tm.executed_trades)
                try:
                    for pk in active_pks:
                        self.tm.tracker_manager._processing[pk] = False
                        self.tm.tracker_manager.last_check_times[pk] = 0.0
                    await check_exit_candidates_for_account(trade_manager=self.tm, account_key=self.account_key, redis_manager=self.tm.redis_manager, tracker_manager=self.tm.tracker_manager, order_queue=order_queue, data_manager=self.tm.data_manager, hedge_engine=self.tm.hedge_engine, position_keys=active_pks, force=True)
                    n_after = len(self.tm.executed_trades) - n_before_exit
                    if n_after > 0:
                        logger.warning(f"[STEP C] check_exit produced {n_after} trades!")
                except Exception as e:
                    logger.warning(f"check_exit CRASH: {type(e).__name__}: {str(e)[:300]}")
                    import traceback; traceback.print_exc()
            # === STEP D: check_entry_candidates (every bar — live checks continuously) ===
            if True:
                entry_pks = [pk for pk in self.tm.tradeable_keys if abs(self.tm.positions.get(pk, BacktestPosition("","",0,0)).positionAmt) < 0.001]
                if entry_pks:
                    try:
                        for pk in entry_pks:
                            self.tm.tracker_manager._processing[pk] = False
                            self.tm.tracker_manager.last_check_times[pk] = 0.0
                        # Limit to 6 entry candidates per bar (like live's max_opens_per_hour throttle)
                        import random as _rng
                        _rng.shuffle(entry_pks)
                        await check_entry_candidates_for_account(trade_manager=self.tm, account_key=self.account_key, redis_manager=self.tm.redis_manager, tracker_manager=self.tm.tracker_manager, order_queue=order_queue, data_manager=self.tm.data_manager, hedge_engine=self.tm.hedge_engine, position_keys=entry_pks[:6], force=True)
                    except Exception as e:
                        if "NoneType" not in str(e):
                            logger.debug(f"check_entry error: {type(e).__name__}: {str(e)[:100]}")
            # === STEP E: BC_162 SPIKE FADE — contrarian P&D entry + exhaustion exit ===
            if getattr(self.tm.config, 'CRYPTO_SPIKE_FADE_ENABLED', False):
                _sf_thresh = getattr(self.tm.config, 'CRYPTO_SPIKE_FADE_THRESHOLD_PCT', 3.0)
                _sf_lb = getattr(self.tm.config, 'CRYPTO_SPIKE_FADE_LOOKBACK_BARS', 10)  # 10 bars × 3m = 30min
                _sf_max = getattr(self.tm.config, 'CRYPTO_SPIKE_FADE_MAX_POSITIONS', 6)
                _sf_cd = getattr(self.tm.config, 'CRYPTO_SPIKE_FADE_COOLDOWN_BARS', 5)
                if not hasattr(self, '_sf_cooldowns'): self._sf_cooldowns = {}
                if not hasattr(self, '_sf_active'): self._sf_active = 0
                self._sf_active = sum(1 for pk, pos in self.tm.positions.items() if abs(pos.positionAmt) > 0 and 'SPIKE_FADE' in str(getattr(pos, 'last_signal', '')))
                for sym, store in self.stores.items():
                    idx = store.ts_to_idx.get(ts, -1)
                    if idx < _sf_lb or self._sf_active >= _sf_max:
                        continue
                    if self._sf_cooldowns.get(sym, 0) > step:
                        continue
                    price_now = store.price(idx)
                    price_lb = store.price(idx - _sf_lb)
                    if price_now <= 0 or price_lb <= 0:
                        continue
                    ret = (price_now - price_lb) / price_lb * 100
                    if abs(ret) < _sf_thresh:
                        continue
                    h3 = store.get('high_3m', idx)
                    h3p = store.get('high_3m_prev', idx)
                    l3 = store.get('low_3m', idx)
                    l3p = store.get('low_3m_prev', idx)
                    k3 = store.get('stoch_k_3m', idx, 50)
                    if ret > _sf_thresh and h3 > 0 and h3p > 0 and (h3 < h3p or price_now < h3p):
                        pk = f"{self.account_key}:{sym}_SHORT"
                        pos = self.tm.positions.get(pk)
                        pos_amt = abs(pos.positionAmt) if pos else 0
                        if pos_amt == 0:
                            qty = max(self.config.START_POSITION_SIZE / price_now, self.tm.min_qty.get(sym, 0.001) * 1.2)
                            await _em_mod.queue_trade_action(order_queue, self.tm, pk, "OPEN", f"SPIKE_FADE_SHORT_ret={ret:+.1f}%_h3LH_k={k3:.0f}", 85.0, override_qty=qty)
                            self._sf_cooldowns[sym] = step + _sf_cd; self._sf_active += 1
                        elif pos_amt * price_now < self.config.START_POSITION_SIZE * 2:
                            qty = max(self.config.START_POSITION_SIZE / price_now, self.tm.min_qty.get(sym, 0.001) * 1.2)
                            await _em_mod.queue_trade_action(order_queue, self.tm, pk, "AUGMENT", f"SPIKE_FADE_AUG_SHORT_ret={ret:+.1f}%", 80.0, override_qty=qty)
                            self._sf_cooldowns[sym] = step + _sf_cd
                    elif ret < -_sf_thresh and l3 > 0 and l3p > 0 and (l3 > l3p or price_now > l3p):
                        pk = f"{self.account_key}:{sym}_LONG"
                        pos = self.tm.positions.get(pk)
                        pos_amt = abs(pos.positionAmt) if pos else 0
                        if pos_amt == 0:
                            qty = max(self.config.START_POSITION_SIZE / price_now, self.tm.min_qty.get(sym, 0.001) * 1.2)
                            await _em_mod.queue_trade_action(order_queue, self.tm, pk, "OPEN", f"SPIKE_FADE_LONG_ret={ret:+.1f}%_l3HL_k={k3:.0f}", 85.0, override_qty=qty)
                            self._sf_cooldowns[sym] = step + _sf_cd; self._sf_active += 1
                        elif pos_amt * price_now < self.config.START_POSITION_SIZE * 2:
                            qty = max(self.config.START_POSITION_SIZE / price_now, self.tm.min_qty.get(sym, 0.001) * 1.2)
                            await _em_mod.queue_trade_action(order_queue, self.tm, pk, "AUGMENT", f"SPIKE_FADE_AUG_LONG_ret={ret:+.1f}%", 80.0, override_qty=qty)
                            self._sf_cooldowns[sym] = step + _sf_cd
                    # Exhaustion exit on existing spike fade positions
                    for side in ('LONG', 'SHORT'):
                        pk = f"{self.account_key}:{sym}_{side}"
                        pos = self.tm.positions.get(pk)
                        if not pos or abs(pos.positionAmt) < 0.001:
                            continue
                        if 'SPIKE_FADE' not in str(getattr(pos, 'last_signal', '')):
                            continue
                        gain = pos.gain if hasattr(pos, 'gain') else 0
                        if gain <= 0:
                            continue
                        is_long = side == 'LONG'
                        if is_long and h3p > 0 and h3 < h3p and price_now < h3p and k3 > 75:
                            await _em_mod.queue_trade_action(order_queue, self.tm, pk, "CLOSE", f"SF_EXHAUST_LONG_LH_g={gain:.2f}%_k={k3:.0f}", 0.95)
                        elif not is_long and l3p > 0 and l3 > l3p and price_now > l3p and k3 < 25:
                            await _em_mod.queue_trade_action(order_queue, self.tm, pk, "CLOSE", f"SF_EXHAUST_SHORT_HL_g={gain:.2f}%_k={k3:.0f}", 0.95)
            # Track equity every bar: capital + realized PnL + unrealized PnL of open positions
            unrealized = 0.0
            for pk, pos in self.tm.positions.items():
                if abs(pos.positionAmt) > 0 and pos.entry_price > 0:
                    p = self.tm._price_cache.get(pos.symbol, 0)
                    if p > 0:
                        unrealized += abs(pos.positionAmt) * pos.entry_price * (pos.gain / 100.0)
            self.equity_curve.append(self.capital + self._compute_realized_pnl() + unrealized)
            # CRITICAL: Drain pending asyncio tasks (create_task Redis writes for cooldowns).
            # Without this, augmentation cooldown writes via create_task never execute before next bar.
            await asyncio.sleep(0)
            # Trade summary every 5% progress
            if step > 0 and step % report_every == 0:
                _n_trades = len(self.tm.executed_trades)
                _n_opens = sum(1 for t in self.tm.executed_trades if t.get('action') == 'OPEN' and t.get('reason') != 'SEED_POSITION')
                _n_closes = sum(1 for t in self.tm.executed_trades if t.get('action') in ('CLOSE', 'REDUCE'))
                _n_hedges = sum(1 for t in self.tm.executed_trades if t.get('is_hedge'))
                _n_active = sum(1 for p in self.tm.positions.values() if abs(p.positionAmt) > 0.0001)
                _eq = self.equity_curve[-1] if self.equity_curve else self.capital
                logger.warning(f"[PROGRESS] {step}/{len(sorted_ts)} ({step*100//len(sorted_ts)}%) | equity=${_eq:,.0f} | trades={_n_trades} (opens={_n_opens} closes={_n_closes} hedges={_n_hedges}) | active={_n_active}")
            # Progress
                elapsed = time.time() - t0
                n_pos = len(self.tm.positions)
                n_trades = len(self.tm.executed_trades)
                eq_now = self.equity_curve[-1] if self.equity_curve else self.capital
                logger.info(f"  {step/len(sorted_ts)*100:.0f}% | {n_pos} pos | {n_trades} trades | equity ${eq_now:.0f} | {elapsed:.0f}s")
        # No time/datetime cleanup needed (no patching)
        elapsed = time.time() - t0
        logger.info(f"Done in {elapsed:.1f}s | {len(self.tm.executed_trades)} total trades | {len(self.tm.positions)} open")
        self.save_results()

    def _compute_realized_pnl(self):
        """Sum realized PnL from all CLOSE/REDUCE trades. Uses _last_rpnl_idx for incremental computation."""
        if not hasattr(self, '_rpnl_cache'):
            self._rpnl_cache = 0.0
            self._last_rpnl_idx = 0
            self._entry_prices = {}
        trades = self.tm.executed_trades
        for i in range(self._last_rpnl_idx, len(trades)):
            t = trades[i]
            pk = t.get("position_key", "")
            action = t.get("action", "")
            price = t.get("price", 0)
            if action in ("OPEN", "REENTRY", "AUGMENT", "MAKER"):
                self._entry_prices[pk] = price
            elif action in ("CLOSE", "REDUCE", "QUICK_CLOSE"):
                entry_price = self._entry_prices.get(pk, 0)
                if entry_price > 0 and price > 0:
                    qty = abs(t.get("quantity", 0))
                    pos_side = t.get("position_side", "LONG")
                    if pos_side == "LONG":
                        self._rpnl_cache += qty * (price - entry_price)
                    else:
                        self._rpnl_cache += qty * (entry_price - price)
        self._last_rpnl_idx = len(trades)
        return self._rpnl_cache

    def save_results(self):
        V5_DIR.mkdir(parents=True, exist_ok=True)
        (V5_DIR / "logs").mkdir(exist_ok=True)
        ts_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = V5_DIR / "logs" / f"engine_{self.mode}_{ts_str}.jsonl"
        with open(path, "w") as f:
            for t in self.tm.executed_trades:
                d = {k: (str(v) if isinstance(v, datetime) else v) for k, v in t.items()}
                f.write(json.dumps(d, default=str) + "\n")
        logger.info(f"Wrote {len(self.tm.executed_trades)} trades to {path}")
        # === PORTFOLIO METRICS (equity curve, unrealized, Sharpe) ===
        trades = self.tm.executed_trades
        opens = [t for t in trades if t.get("action") in ("OPEN", "MAKER", "REENTRY", "AUGMENT") and t.get("action") not in ("REDUCE", "CLOSE")]
        closes = [t for t in trades if t.get("action") in ("CLOSE", "REDUCE", "QUICK_CLOSE")]
        hedges = [t for t in trades if t.get("is_hedge")]
        # Compute realized PnL per closed trade (separate hedge vs regular)
        realized_pnl = 0.0
        hedge_realized_pnl = 0.0
        winning_closes = 0
        losing_closes = 0
        hedge_wins = 0
        hedge_losses = 0
        for t in closes:
            pk = t.get("position_key", "")
            pos_side = t.get("position_side", "LONG")
            qty = abs(t.get("quantity", 0))
            price = t.get("price", 0)
            t_is_hedge = t.get("is_hedge", False)
            entry_price = 0.0
            for o in reversed(trades):
                if o.get("position_key") == pk and o.get("action") in ("OPEN", "REENTRY", "AUGMENT", "MAKER") and o.get("timestamp", 0) <= t.get("timestamp", 0):
                    entry_price = o.get("price", 0)
                    break
            if entry_price > 0 and price > 0:
                if pos_side == "LONG":
                    pnl = qty * (price - entry_price)
                else:
                    pnl = qty * (entry_price - price)
                realized_pnl += pnl
                if t_is_hedge:
                    hedge_realized_pnl += pnl
                    if pnl > 0: hedge_wins += 1
                    else: hedge_losses += 1
                else:
                    if pnl > 0: winning_closes += 1
                    else: losing_closes += 1
        total_closes = winning_closes + losing_closes
        win_rate = winning_closes / max(1, total_closes) * 100
        total_hedge_closes = hedge_wins + hedge_losses
        # Unrealized PnL of still-open positions
        unrealized_pnl = 0.0
        open_positions_detail = []
        for pk, pos in self.tm.positions.items():
            if abs(pos.positionAmt) > 0 and pos.entry_price > 0:
                p = self.tm._price_cache.get(pos.symbol, 0)
                if p > 0:
                    pos_pnl = abs(pos.positionAmt) * pos.entry_price * (pos.gain / 100.0)
                    unrealized_pnl += pos_pnl
                    open_positions_detail.append({"pk": pk, "entry": round(pos.entry_price, 4), "mark": round(p, 4), "gain%": round(pos.gain, 2), "pnl$": round(pos_pnl, 2), "qty": round(pos.positionAmt, 6)})
        # Equity curve metrics
        eq = np.array(self.equity_curve) if self.equity_curve else np.array([self.capital])
        final_equity = float(eq[-1]) if len(eq) > 0 else self.capital
        total_pnl_pct = (final_equity - self.capital) / self.capital * 100
        sharpe = 0.0
        if len(eq) > 1 and total_closes >= 10:
            returns = np.diff(eq) / (eq[:-1] + 1e-10)
            if np.std(returns) > 1e-6:
                if self.mode == "crypto":
                    bars_per_year = (20 if self.resolution == "3m" else 4) * 24 * 365
                else:
                    bars_per_day = 78 if self.resolution == "5m" else 26
                    bars_per_year = bars_per_day * 252
                sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(bars_per_year))
        max_dd = 0.0
        if len(eq) > 1:
            peak = np.maximum.accumulate(eq)
            drawdown = (peak - eq) / (peak + 1e-10) * 100
            max_dd = float(np.max(drawdown))
        # Function breakdown
        func_counts = {}
        for t in trades:
            r = t.get("reason", "unknown")
            tag = r.split("(")[0].split(":")[0][:40]
            func_counts[tag] = func_counts.get(tag, 0) + 1
        print("\n" + "=" * 90)
        print(f"  V5 ENGINE RESULTS — {self.mode.upper()}")
        print("=" * 90)
        print(f"  Capital: ${self.capital:,.0f} → Final equity: ${final_equity:,.0f} (incl. unrealized)")
        print(f"  Total PnL:     {total_pnl_pct:+.2f}%")
        print(f"  Realized PnL:  ${realized_pnl:+,.2f}")
        print(f"  Unrealized PnL: ${unrealized_pnl:+,.2f}  ({len(self.tm.positions)} open positions)")
        print(f"  Sharpe:        {sharpe:.3f}")
        print(f"  Max Drawdown:  {max_dd:.2f}%")
        print("-" * 90)
        print(f"  Trades: {len(trades)} total | {len(opens)} opens | {total_closes} closes ({winning_closes}W/{losing_closes}L) | WR: {win_rate:.1f}%")
        print(f"  Hedges: {len(hedges)} actions | {total_hedge_closes} closes ({hedge_wins}W/{hedge_losses}L) | Hedge PnL: ${hedge_realized_pnl:+,.2f}")
        if open_positions_detail:
            open_positions_detail.sort(key=lambda x: x["pnl$"])
            open_winners = [p for p in open_positions_detail if p["pnl$"] > 0]
            open_losers = [p for p in open_positions_detail if p["pnl$"] <= 0]
            open_winner_pnl = sum(p["pnl$"] for p in open_winners)
            open_loser_pnl = sum(p["pnl$"] for p in open_losers)
            print(f"\n  OPEN POSITIONS: {len(open_positions_detail)} total")
            print(f"    Winners: {len(open_winners)} (${open_winner_pnl:+,.2f})")
            print(f"    Losers:  {len(open_losers)} (${open_loser_pnl:+,.2f})")
            print(f"    Worst 5:")
            for p in open_positions_detail[:5]:
                print(f"      {p['pk']:40s} gain={p['gain%']:+6.2f}% pnl=${p['pnl$']:+8.2f}")
            print(f"    Best 5:")
            for p in open_positions_detail[-5:]:
                print(f"      {p['pk']:40s} gain={p['gain%']:+6.2f}% pnl=${p['pnl$']:+8.2f}")
        print("-" * 90)
        print("  TRADE REASONS (top 20):")
        for tag, count in sorted(func_counts.items(), key=lambda x: -x[1])[:20]:
            print(f"    {tag:50s}  n={count}")
        print("=" * 90)
        # Save summary JSON
        summary = {"mode": self.mode, "capital": self.capital, "final_equity": round(final_equity, 2), "total_pnl_pct": round(total_pnl_pct, 2), "realized_pnl": round(realized_pnl, 2), "unrealized_pnl": round(unrealized_pnl, 2), "sharpe": round(sharpe, 3), "max_drawdown_pct": round(max_dd, 2), "total_trades": len(trades), "opens": len(opens), "closes": total_closes, "winning_closes": winning_closes, "losing_closes": losing_closes, "win_rate": round(win_rate, 2), "hedge_actions": len(hedges), "hedge_realized_pnl": round(hedge_realized_pnl, 2), "hedge_wins": hedge_wins, "hedge_losses": hedge_losses, "open_positions": len(self.tm.positions), "open_winners": len([p for p in open_positions_detail if p["pnl$"] > 0]), "open_losers": len([p for p in open_positions_detail if p["pnl$"] <= 0]), "open_winner_pnl": round(sum(p["pnl$"] for p in open_positions_detail if p["pnl$"] > 0), 2), "open_loser_pnl": round(sum(p["pnl$"] for p in open_positions_detail if p["pnl$"] <= 0), 2), "open_positions_detail": open_positions_detail}
        summary_path = V5_DIR / "logs" / f"summary_{self.mode}_{ts_str}.json"
        summary_path.write_text(json.dumps(summary, indent=2))
        logger.info(f"Summary saved to {summary_path}")


def main():
    parser = argparse.ArgumentParser(description="V5 Engine — ACTUAL process_position()")
    parser.add_argument("--mode", choices=["crypto", "tradier"], required=True)
    parser.add_argument("--symbols", type=str, default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--account", type=str, default="ang")
    parser.add_argument("--seed-positions", type=str, default="", help="JSON file with initial positions")
    parser.add_argument("--max-symbols", type=int, default=0, help="Limit number of symbols (0=all)")
    args = parser.parse_args()
    if args.start is None:
        args.start = "2022-01-01" if args.mode == "crypto" else "2024-01-01"
    if args.capital is None:
        args.capital = 10000.0 if args.mode == "crypto" else 70000.0
    npz_dir, _ = get_npz_dir(args.mode)
    if args.all:
        symbols = sorted([p.stem for p in npz_dir.glob("*.npz")])
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        symbols = sorted([p.stem for p in npz_dir.glob("*.npz")])
    if args.max_symbols > 0:
        symbols = symbols[:args.max_symbols]
    engine = V5Engine(mode=args.mode, symbols=symbols, start_date=args.start, capital=args.capital, account_key=args.account)
    engine.seed_file = args.seed_positions
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
