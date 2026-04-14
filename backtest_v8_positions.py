"""
V8 Backtest Position Manager — manages positions for EVERY sweep run, resets between runs.

Replaces TradierPositionManager for backtesting. Same interface, no disk/Redis/API.
Creates positions on OPEN, updates on AUGMENT, reduces on REDUCE/CLOSE.
Updates gains every bar from NPZ prices.
Resets to zero with .reset() between sweep runs.

Used by backtest_v8_engine.py — drops in as manager.position_manager.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import logging
import time

logger = logging.getLogger("v8_positions")


@dataclass
class BacktestPosition:
    """Position object with same interface as TradierPosition."""
    symbol: str = ""
    position_side: str = "LONG"
    positionAmt: float = 0.0
    quantity: float = 0.0  # alias
    entry_price: float = 0.0
    mark_price: float = 0.0
    current_price: float = 0.0
    gain: float = 0.0
    prev_gain: float = 0.0
    max_gain: float = 0.0
    max_gain_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    opened_at: Optional[datetime] = None
    last_updated: Optional[datetime] = None
    last_augmentation_time: Optional[datetime] = None
    last_augmentation_price: float = 0.0
    last_augmentation_amount: float = 0.0
    augment_reason: str = ""
    augmented_count: int = 0
    last_reduction_time: Optional[datetime] = None
    last_reduction_price: float = 0.0
    last_reduction_amount: float = 0.0
    reduction_reason: str = ""
    was_reduced: bool = False
    max_quantity: float = 0.0
    entry_reason: str = ""
    last_signal: str = ""
    cost_basis: float = 0.0
    total_cost: float = 0.0

    @property
    def is_long(self):
        return self.position_side == "LONG"

    def update_price(self, price: float, sim_dt: Optional[datetime] = None):
        """Update mark price and recalculate gain."""
        if price <= 0 or self.entry_price <= 0:
            return
        self.prev_gain = self.gain
        self.mark_price = price
        self.current_price = price
        if self.is_long:
            self.gain = (price - self.entry_price) / self.entry_price * 100
        else:
            self.gain = (self.entry_price - price) / self.entry_price * 100
        if self.gain > self.max_gain:
            self.max_gain = self.gain
            self.max_gain_price = price
        self.unrealized_pnl = abs(self.positionAmt) * self.entry_price * (self.gain / 100)
        if sim_dt:
            self.last_updated = sim_dt


class BacktestPositionManager:
    """Drop-in replacement for TradierPositionManager. No disk, no API, no Redis."""

    def __init__(self, account_keys: List[str] = None):
        self.account_keys = account_keys or ["trb"]
        self.positions: Dict[str, BacktestPosition] = {}
        self.positions_by_account: Dict[str, Dict[str, BacktestPosition]] = defaultdict(dict)
        self.symbols: List[str] = []
        self.running = True
        self.price_cache: Dict[str, Any] = {}
        self._price_cache = self.price_cache
        self.reenter_data: Dict[str, Any] = {}
        self.augmented_positions: Dict[str, datetime] = {}
        self.reduced_positions: Dict[str, datetime] = {}
        self.augmentation_cooldown_map: Dict[str, Any] = {}
        self.reduction_cooldown_map: Dict[str, datetime] = {}
        self.orders_in_limbo: Dict[str, Any] = {}
        self.position_reasons: Dict[str, Any] = {}
        self.zero_report_tracker: Dict[str, Any] = {}
        self.ladder_levels: Dict[str, Any] = {}
        self.min_qty: Dict[str, float] = {}
        self._positions_lock = None  # set by engine
        self._position_update_timestamps: Dict[str, datetime] = {}
        self.failed_price_counts: Dict[str, int] = defaultdict(int)
        self.positions_last_sync: Optional[datetime] = None
        self.positions_live = True
        self._positions_dirty = False
        # Trade history for this sweep run
        self.trade_history: List[Dict] = []
        self.total_realized_pnl: float = 0.0
        self.total_fees: float = 0.0
        self.winning_trades: int = 0
        self.losing_trades: int = 0
        self.last_price_cache: Dict[str, float] = {}

    def reset(self):
        """Reset ALL state for next sweep run. Called between parameter sweeps."""
        self.positions.clear()
        self.positions_by_account.clear()
        for ak in self.account_keys:
            self.positions_by_account[ak] = {}
        self.trade_history.clear()
        self.total_realized_pnl = 0.0
        self.total_fees = 0.0
        self.winning_trades = 0
        self.losing_trades = 0
        self.reenter_data.clear()
        self.augmented_positions.clear()
        self.reduced_positions.clear()
        self.augmentation_cooldown_map.clear()
        self.reduction_cooldown_map.clear()
        self.orders_in_limbo.clear()
        self.position_reasons.clear()
        self.zero_report_tracker.clear()

    def get_position(self, position_key: str) -> Optional[BacktestPosition]:
        return self.positions.get(position_key)

    def get_positions_by_account(self, account_key: str) -> Dict[str, BacktestPosition]:
        return self.positions_by_account.get(account_key, {})

    def open_position(self, account_key: str, position_key: str, symbol: str,
                      position_side: str, quantity: float, price: float,
                      reason: str = "", sim_dt: datetime = None) -> BacktestPosition:
        """Create a new position. Called when execute_trade_action succeeds on OPEN."""
        pos = BacktestPosition(
            symbol=symbol,
            position_side=position_side,
            positionAmt=abs(quantity),
            quantity=abs(quantity),
            entry_price=price,
            mark_price=price,
            current_price=price,
            gain=0.0,
            max_gain=0.0,
            opened_at=sim_dt or datetime.now(timezone.utc),
            last_updated=sim_dt or datetime.now(timezone.utc),
            entry_reason=reason,
            max_quantity=abs(quantity),
            cost_basis=price,
            total_cost=abs(quantity) * price,
        )
        self.positions[position_key] = pos
        self.positions_by_account[account_key][position_key] = pos
        self.trade_history.append({
            "action": "OPEN", "position_key": position_key, "symbol": symbol,
            "side": position_side, "quantity": abs(quantity), "price": price,
            "reason": reason, "timestamp": (sim_dt or datetime.now(timezone.utc)).isoformat(),
        })
        return pos

    def augment_position(self, position_key: str, quantity: float, price: float,
                         reason: str = "", sim_dt: datetime = None) -> bool:
        """Add to existing position."""
        pos = self.positions.get(position_key)
        if not pos or pos.positionAmt <= 0:
            return False
        old_qty = abs(pos.positionAmt)
        add_qty = abs(quantity)
        new_qty = old_qty + add_qty
        pos.entry_price = (pos.entry_price * old_qty + price * add_qty) / new_qty
        pos.positionAmt = new_qty
        pos.quantity = new_qty
        pos.max_quantity = max(pos.max_quantity, new_qty)
        pos.last_augmentation_time = sim_dt or datetime.now(timezone.utc)
        pos.last_augmentation_price = price
        pos.last_augmentation_amount = add_qty
        pos.augment_reason = reason
        pos.augmented_count += 1
        pos.update_price(price, sim_dt)
        self.trade_history.append({
            "action": "AUGMENT", "position_key": position_key, "symbol": pos.symbol,
            "side": pos.position_side, "quantity": add_qty, "price": price,
            "reason": reason, "timestamp": (sim_dt or datetime.now(timezone.utc)).isoformat(),
        })
        return True

    def reduce_position(self, position_key: str, quantity: float, price: float,
                        is_full_close: bool = False, reason: str = "",
                        sim_dt: datetime = None, fee_rate: float = 0.0) -> float:
        """Reduce or close position. Returns realized PnL."""
        pos = self.positions.get(position_key)
        if not pos or pos.positionAmt <= 0:
            return 0.0
        reduce_qty = min(abs(quantity), abs(pos.positionAmt))
        if is_full_close:
            reduce_qty = abs(pos.positionAmt)
        # Calculate PnL
        if pos.is_long:
            pnl = reduce_qty * (price - pos.entry_price)
        else:
            pnl = reduce_qty * (pos.entry_price - price)
        fee = reduce_qty * price * fee_rate
        pnl -= fee
        self.total_realized_pnl += pnl
        self.total_fees += fee
        if pnl > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1
        pos.positionAmt = max(0.0, abs(pos.positionAmt) - reduce_qty)
        pos.quantity = pos.positionAmt
        pos.last_reduction_time = sim_dt or datetime.now(timezone.utc)
        pos.last_reduction_price = price
        pos.last_reduction_amount = reduce_qty
        pos.was_reduced = True
        pos.reduction_reason = reason
        pos.realized_pnl += pnl
        if pos.positionAmt <= 0:
            pos.positionAmt = 0.0
            pos.max_gain = 0.0
        self.trade_history.append({
            "action": "CLOSE" if is_full_close or pos.positionAmt <= 0 else "REDUCE",
            "position_key": position_key, "symbol": pos.symbol,
            "side": pos.position_side, "quantity": reduce_qty, "price": price,
            "pnl": round(pnl, 4), "fee": round(fee, 4), "gain_pct": round(pos.gain, 2),
            "reason": reason, "timestamp": (sim_dt or datetime.now(timezone.utc)).isoformat(),
        })
        return pnl

    def update_all_prices(self, price_cache: Dict[str, float], sim_dt: datetime = None):
        """Update ALL position gains from current prices. Called every bar."""
        for sym, price in price_cache.items():
            if price > 0:
                self.last_price_cache[sym.upper()] = price
        if sim_dt is not None:
            self.last_price_cache["_last_ts"] = sim_dt.timestamp()
        for pk, pos in self.positions.items():
            if pos.positionAmt <= 0:
                continue
            price = price_cache.get(pos.symbol.upper(), 0)
            if price > 0:
                pos.update_price(price, sim_dt)

    def get_active_count(self) -> int:
        return sum(1 for p in self.positions.values() if p.positionAmt > 0)

    def get_summary(self) -> Dict[str, Any]:
        """Summary stats for this sweep run."""
        active = [(pk, p) for pk, p in self.positions.items() if p.positionAmt > 0]
        total_trades = self.winning_trades + self.losing_trades
        return {
            "active_positions": len(active),
            "total_trades": total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.winning_trades / max(total_trades, 1) * 100,
            "total_realized_pnl": round(self.total_realized_pnl, 2),
            "total_fees": round(self.total_fees, 2),
            "unrealized_pnl": round(sum(p.unrealized_pnl for _, p in active), 2),
        }

    # === Compatibility methods for TradierPositionManager interface ===
    def load_all_symbols(self): pass
    async def save_positions(self, *a, **kw): return True
    async def sync_positions_from_redis(self, *a, **kw): pass
    async def sync_real_positions_from_api(self, *a, **kw): pass
    async def update_positions_loop(self): pass
    def get_position_file(self, side): return None
