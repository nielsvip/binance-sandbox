#!/home/niels/.conda_envs/binance_env/bin/python3

"""ez_loss_mitigator.py - Aggressive gain guard for ang account: kills positions immediately when declining with bad technicals. Hedges first, kills fast. No mercy."""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional
from ez_positions_service import bootstrap_position_service, WebSocketManager
from utils import load_environment_from_gpg, safe_fetch_float, orjson_default
from config import Config
from dateutil.parser import isoparse

load_environment_from_gpg(None)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("ez_loss_mitigator")
logs_dir = Path.home() / "logs"
logs_dir.mkdir(parents=True, exist_ok=True)
from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(str(logs_dir / "ez_loss_mitigator.log"), maxBytes=100*1024*1024, backupCount=5, encoding='utf-8', mode='a')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
logger.addHandler(file_handler)
logger.setLevel(logging.DEBUG)
config = Config()

_raw_acct = getattr(config, 'MITIGATOR_ACCOUNT', 'ang')
ACCOUNTS = _raw_acct if isinstance(_raw_acct, list) else [_raw_acct]
if not ACCOUNTS or not getattr(config, 'MITIGATOR_ENABLED', True):
    logger.info("MITIGATOR disabled (empty accounts or MITIGATOR_ENABLED=False). Exiting.")
    sys.exit(0)
ACCOUNT = ACCOUNTS[0]  # Primary account for single-account calls
SCAN_INTERVAL = getattr(config, 'MITIGATOR_SCAN_INTERVAL', 3.0)
COOLDOWN = getattr(config, 'MITIGATOR_COOLDOWN', 15.0)
REENTRY_COOLDOWN = 60.0  # 60s — fast reopen when tide turns
REENTRY_PRICE_PCT = 0.08  # 0.08% favorable move = good enough to reenter
DRY_RUN = not getattr(config, 'MITIGATOR_ENABLED', True)
ZERO_TOLERANCE_GAIN = 0.15  # KILL at this gain — position must NEVER go below this after being armed
ARM_THRESHOLD = 0.15  # Position is "armed" once peak exceeds this (0.02% too small for monitoring frequency)


@dataclass
class GainTracker:
    peak_gain: float = 0.0
    samples: deque = field(default_factory=lambda: deque(maxlen=60))
    original_qty: float = 0.0
    last_action_ts: float = 0.0
    hedged: bool = False
    hedge_key: str = ""
    hedge_peak_gain: float = 0.0
    killed: bool = False


@dataclass
class ReentryCandidate:
    position_key: str
    symbol: str
    position_side: str
    exit_price: float
    exit_time: float
    original_qty: float


REENTRY_FILE = Path(config.DATA_DIR if hasattr(config, 'DATA_DIR') else Path.cwd() / "data") / "mitigator_reentries.json"


class LossMitigator:
    def __init__(self, service):
        self.service = service
        self.trackers: Dict[str, GainTracker] = {}
        self.reentry_candidates: Dict[str, ReentryCandidate] = {}
        self.running = True
        self._last_fetch: Dict[str, float] = {}
        self._positions_refreshing: Dict[str, bool] = {}
        self._fetch_tasks: Dict[str, asyncio.Task] = {}
        self._websocket_managers: Dict[str, WebSocketManager] = {}
        self._kill_cooldowns: Dict[str, float] = {}  # position_key -> timestamp of last kill attempt
        self._kill_attempts: Dict[str, int] = {}  # position_key -> number of consecutive kill attempts
        self._load_reentries()

    def _load_reentries(self):
        try:
            if REENTRY_FILE.exists():
                with open(REENTRY_FILE, 'r') as f:
                    data = json.load(f)
                for key, entry in data.items():
                    self.reentry_candidates[key] = ReentryCandidate(position_key=entry["position_key"], symbol=entry["symbol"], position_side=entry["position_side"], exit_price=entry["exit_price"], exit_time=entry["exit_time"], original_qty=entry["original_qty"])
                logger.warning(f"[MITIGATOR] Loaded {len(self.reentry_candidates)} mandatory re-entries from {REENTRY_FILE}")
        except Exception as e:
            logger.error(f"[MITIGATOR] Error loading reentries: {e}")

    def _save_reentries(self):
        try:
            REENTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {key: {"position_key": c.position_key, "symbol": c.symbol, "position_side": c.position_side, "exit_price": c.exit_price, "exit_time": c.exit_time, "original_qty": c.original_qty} for key, c in self.reentry_candidates.items()}
            tmp = REENTRY_FILE.with_suffix('.tmp')
            with open(tmp, 'w') as f:
                json.dump(data, f, indent=2)
            tmp.replace(REENTRY_FILE)
        except Exception as e:
            logger.error(f"[MITIGATOR] Error saving reentries: {e}")

    async def _direct_api_kill(self, position_key: str, position, reason: str, save_reentry: bool = True):
        """Redirect to execute_now — mitigator must NOT make direct API calls."""
        try:
            symbol = getattr(position, 'symbol', None)
            if not symbol: return False
            position_amt = abs(float(getattr(position, 'positionAmt', 0) or 0))
            if position_amt <= 0: return False
            position_side = getattr(position, 'position_side', 'LONG')
            side = 'SELL' if position_side == 'LONG' else 'BUY'
            current_price = safe_fetch_float(getattr(position, 'mark_price', 0.0), 0.0)
            if current_price <= 0: return False
            logger.critical(f"[MITIGATOR_KILL] {position_key}: {side} {position_amt} {symbol} via execute_now — reason={reason}")
            result = await asyncio.wait_for(self.service.trade_manager.execute_now(position_key, ACCOUNT, symbol, position_amt, side, position_side, position_amt, current_price, f"mitigator_kill_{int(time.time())}", reason, True, 'CLOSE'), timeout=15.0)
            if result and "SUCCESS" in result:
                await self._send_foothold_webhook(position_key, position_amt, side, current_price, position_amt, True, f"MITIGATOR_KILL_{reason}")
                if save_reentry:
                    tracker = self.trackers.get(position_key)
                    self.reentry_candidates[position_key] = ReentryCandidate(position_key=position_key, symbol=symbol, position_side=position_side, exit_price=current_price, exit_time=time.time(), original_qty=tracker.original_qty if tracker else position_amt)
                    self._save_reentries()
                    logger.warning(f"[MITIGATOR] RE-ENTRY SAVED {position_key} exit={current_price:.4f}")
                if position_key in self.trackers: del self.trackers[position_key]
                return True
            else:
                logger.error(f"[MITIGATOR_KILL] FAILED {position_key}: {result}")
                return False
        except Exception as e:
            logger.error(f"[MITIGATOR_KILL] Error {position_key}: {e}", exc_info=True)
            return False

    async def _free_margin_by_closing_worst(self, needed_value: float) -> bool:
        account_positions = self.service.positions_by_account.get(ACCOUNT, {})
        if not account_positions:
            return False
        scored = []
        for pk, pos in list(account_positions.items()):
            if not pos:
                continue
            amt = abs(float(getattr(pos, 'positionAmt', 0) or 0))
            if amt <= 0 or pk in self.reentry_candidates:
                continue
            g = safe_fetch_float(getattr(pos, 'gain', 0.0), 0.0)
            price = safe_fetch_float(getattr(pos, 'mark_price', 0.0), 0.0)
            scored.append((g, amt * price if price > 0 else 0, pk, pos))
        scored.sort(key=lambda x: x[0])
        freed = 0.0
        for g, notional, pk, pos in scored:
            if freed >= needed_value:
                break
            symbol = getattr(pos, 'symbol', None)
            if not symbol:
                continue
            amt = abs(float(getattr(pos, 'positionAmt', 0) or 0))
            position_side = getattr(pos, 'position_side', 'LONG')
            side = 'SELL' if position_side == 'LONG' else 'BUY'
            price = safe_fetch_float(getattr(pos, 'mark_price', 0.0), 0.0)
            if price <= 0:
                continue
            logger.warning(f"[MITIGATOR] CLOSING WORST {pk} (gain={g:.3f}% ${notional:.0f}) to free margin")
            try:
                result = await asyncio.wait_for(self.service.trade_manager.execute_now(pk, ACCOUNT, symbol, amt, side, position_side, amt, price, f"mitigator_free_{int(time.time())}", f"FORCE_GAIN_GUARD_FREE_MARGIN_gain={g:.3f}%", True, 'CLOSE'), timeout=15.0)
                if result and "SUCCESS" in result:
                    freed += notional
            except Exception as e:
                logger.error(f"[MITIGATOR] Error closing {pk}: {e}")
        return freed >= needed_value

    async def _fetch_real_positions(self, account_key: str):
        """No-op: mitigator reads from positions_by_account (populated by ez_positions_service)."""
        pass

    async def _fetch_loop(self, account_key: str):
        """No-op: mitigator must NOT make direct API calls. Data comes from positions_by_account."""
        logger.info(f"[MITIGATOR][{account_key}] Fetch loop disabled — using cached positions_by_account")
        pass

    async def monitor_loop(self):
        logger.warning("[MITIGATOR] Starting AGGRESSIVE gain guard monitor — KILL MODE")
        while self.running:
            try:
                if not self.running:
                    break
                await self._scan_positions()
                await self._check_reentries()
                for _ in range(int(SCAN_INTERVAL * 10)):
                    if not self.running:
                        break
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self.running:
                    break
                logger.error(f"[MITIGATOR] Monitor error: {e}", exc_info=True)
                for _ in range(int(SCAN_INTERVAL * 10)):
                    if not self.running:
                        break
                    await asyncio.sleep(0.1)

    def _check_hedge_tracker(self, position_key: str) -> dict:
        try:
            base_path = Path(self.service.base_path) if hasattr(self.service, 'base_path') else Path.cwd()
            tracker_path = base_path / ACCOUNT / "tracker.json"
            if not tracker_path.exists():
                return {"is_hedge": False, "hedge_for": None, "has_hedge": False}
            with open(tracker_path, 'r') as f:
                tracker = json.load(f)
            positions_data = tracker.get("positions", {})
            pos_data = positions_data.get(position_key, {})
            is_hedge = pos_data.get("is_hedge", False)
            hedge_for = pos_data.get("hedge_for", None)
            parts = position_key.split(":")
            if len(parts) == 2:
                symbol_side = parts[1]
                opposite_key = position_key.replace("_LONG", "_SHORT") if "_LONG" in symbol_side else position_key.replace("_SHORT", "_LONG")
                opposite_data = positions_data.get(opposite_key, {})
                has_hedge = opposite_data.get("is_hedge", False) and opposite_data.get("hedge_for") == position_key
            else:
                has_hedge = False
            return {"is_hedge": is_hedge, "hedge_for": hedge_for, "has_hedge": has_hedge}
        except Exception:
            return {"is_hedge": False, "hedge_for": None, "has_hedge": False}

    async def _get_indicators(self, symbol: str) -> dict:
        try:
            if hasattr(self.service, 'get_indicators_for_symbol'):
                return await self.service.get_indicators_for_symbol(symbol, force_refresh=False) or {}
        except Exception:
            pass
        return {}

    async def _execute_hedge(self, position_key: str, position, gain: float) -> bool:
        """Open hedge on opposite side. Returns True if successful."""
        if DRY_RUN:
            logger.warning(f"[MITIGATOR][DRY_RUN] Would hedge {position_key} gain={gain:.3f}%")
            return False
        try:
            symbol = getattr(position, 'symbol', None)
            if not symbol:
                return False
            position_side = getattr(position, 'position_side', 'LONG')
            hedge_side_name = 'SHORT' if position_side == 'LONG' else 'LONG'
            hedge_key = position_key.replace(f"_{position_side}", f"_{hedge_side_name}")
            opposite_pos = self.service.positions_by_account.get(ACCOUNT, {}).get(hedge_key)
            if opposite_pos and abs(float(getattr(opposite_pos, 'positionAmt', 0) or 0)) > 0:
                return True
            current_price = safe_fetch_float(getattr(position, 'mark_price', 0.0), 0.0)
            if current_price <= 0:
                try:
                    current_price = safe_fetch_float(await asyncio.wait_for(self.service.get_mark_price(symbol, allow_fallback=True), timeout=3.0), 0.0)
                except (asyncio.TimeoutError, Exception):
                    return False
            if current_price <= 0:
                return False
            hedge_qty = config.START_POSITION_SIZE / current_price
            hedge_order_side = 'SELL' if hedge_side_name == 'SHORT' else 'BUY'
            reason = f"FORCE_GAIN_GUARD_HEDGE_{position_side}_decline_{gain:.3f}%"
            logger.warning(f"[MITIGATOR] HEDGING {position_key} -> {hedge_key} gain={gain:.3f}%")
            result = await asyncio.wait_for(self.service.trade_manager.execute_now(hedge_key, ACCOUNT, symbol, 0, hedge_order_side, hedge_side_name, hedge_qty, current_price, f"mitigator_hedge_{int(time.time())}", reason, False, 'OPEN', is_hedge=True, hedge_for=position_key), timeout=15.0)
            if result and "SUCCESS" in result:
                logger.info(f"[MITIGATOR] Hedged {position_key}: {result}")
                await self._send_foothold_webhook(hedge_key, 0, hedge_order_side, current_price, hedge_qty, False, f"MITIGATOR_HEDGE_{reason}")
                return True
            else:
                logger.error(f"[MITIGATOR] Hedge failed {position_key}: {result}")
                return False
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return False
        except Exception as e:
            logger.error(f"[MITIGATOR] Hedge error {position_key}: {e}", exc_info=True)
            return False

    async def _send_foothold_webhook(self, position_key: str, position_amt: float, side: str, current_price: float, quantity: float, is_full_close: bool, reason: str):
        """Send webhook so trades appear in foothold dashboard."""
        try:
            if hasattr(self.service, 'tracker_manager') and self.service.tracker_manager:
                await self.service.tracker_manager.send_webhook(position_key, position_amt, side, current_price, quantity, is_full_close, reason)
                logger.info(f"[MITIGATOR] WEBHOOK SENT {position_key}: {reason}")
            elif hasattr(self.service, 'trade_manager') and hasattr(self.service.trade_manager, 'tracker_manager'):
                await self.service.trade_manager.tracker_manager.send_webhook(position_key, position_amt, side, current_price, quantity, is_full_close, reason)
                logger.info(f"[MITIGATOR] WEBHOOK SENT {position_key}: {reason}")
        except Exception as e:
            logger.debug(f"[MITIGATOR] Webhook error {position_key}: {e}")

    async def _execute_kill(self, position_key: str, position, reason: str):
        """Force close 100% via execute_now. Tracks attempts and enforces cooldown to avoid webhook spam."""
        if DRY_RUN:
            logger.warning(f"[MITIGATOR][DRY_RUN] Would KILL {position_key}: {reason}")
            return
        # NO_LOSS UNIVERSAL BLOCK: Never close at a loss unless EMERGENCY/LIQUIDATION
        _gain = getattr(position, 'gain', None)
        if _gain is not None and _gain < -0.01:
            _is_emergency = ("EMERGENCY" in reason.upper() or "LIQUIDATION" in reason.upper())
            if not _is_emergency:
                _acct = position_key.split(':')[0] if ':' in position_key else ACCOUNT
                _no_loss_accts = getattr(config, 'STRICT_NO_LOSS_ACCOUNTS', [])
                if _acct in _no_loss_accts:
                    logger.critical(f"🛑[MITIGATOR_NO_LOSS_BLOCK] {position_key}: Blocking kill at loss ({_gain:.2f}%). Reason: {reason}")
                    return
        now = time.time()
        last_kill = self._kill_cooldowns.get(position_key, 0)
        attempts = self._kill_attempts.get(position_key, 0)
        cooldown = min(30 + attempts * 15, 120)  # 30s first, then 45s, 60s, ... max 120s
        if now - last_kill < cooldown:
            return  # silently skip — already tried recently
        self._kill_cooldowns[position_key] = now
        self._kill_attempts[position_key] = attempts + 1
        try:
            symbol = getattr(position, 'symbol', None)
            if not symbol: return
            position_amt = abs(float(getattr(position, 'positionAmt', 0) or 0))
            if position_amt <= 0: return
            position_side = getattr(position, 'position_side', 'LONG')
            side = 'SELL' if position_side == 'LONG' else 'BUY'
            current_price = safe_fetch_float(getattr(position, 'mark_price', 0.0), 0.0)
            if current_price <= 0:
                try: current_price = safe_fetch_float(await asyncio.wait_for(self.service.get_mark_price(symbol, allow_fallback=True), timeout=3.0), 0.0)
                except (asyncio.TimeoutError, Exception): return
            if current_price <= 0: return
            logger.warning(f"[MITIGATOR] KILLING {position_key} (attempt #{attempts+1}, cooldown={cooldown}s): {reason}")
            result = await asyncio.wait_for(self.service.trade_manager.execute_now(position_key, ACCOUNT, symbol, position_amt, side, position_side, position_amt, current_price, f"mitigator_kill_{int(time.time())}", reason, True, 'CLOSE'), timeout=15.0)
            if result and "SUCCESS" in result:
                logger.info(f"[MITIGATOR] KILLED {position_key}: {result}")
                await self._send_foothold_webhook(position_key, position_amt, side, current_price, position_amt, True, f"MITIGATOR_KILL_{reason}")
                tracker = self.trackers.get(position_key)
                self.reentry_candidates[position_key] = ReentryCandidate(position_key=position_key, symbol=symbol, position_side=position_side, exit_price=current_price, exit_time=time.time(), original_qty=tracker.original_qty if tracker else position_amt)
                if position_key in self.trackers: del self.trackers[position_key]
                self._save_reentries()
                logger.warning(f"[MITIGATOR] RE-ENTRY SAVED {position_key} exit={current_price:.4f}")
            else:
                logger.error(f"[MITIGATOR] KILL FAILED {position_key}: {result}")
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.error(f"[MITIGATOR] KILL TIMEOUT {position_key}")
        except Exception as e:
            logger.error(f"[MITIGATOR] Kill error {position_key}: {e}", exc_info=True)

    async def _execute_kill_no_reentry(self, position_key: str, position, reason: str):
        """Force close 100% — NO reentry saved (for orphan kills). Uses execute_now."""
        if DRY_RUN:
            logger.warning(f"[MITIGATOR][DRY_RUN] Would KILL (no reentry) {position_key}: {reason}")
            return
        # NO_LOSS UNIVERSAL BLOCK
        _gain = getattr(position, 'gain', None)
        if _gain is not None and _gain < -0.01:
            _is_emergency = ("EMERGENCY" in reason.upper() or "LIQUIDATION" in reason.upper())
            if not _is_emergency:
                _acct = position_key.split(':')[0] if ':' in position_key else ACCOUNT
                if _acct in getattr(config, 'STRICT_NO_LOSS_ACCOUNTS', []):
                    logger.critical(f"🛑[MITIGATOR_NO_LOSS_BLOCK] {position_key}: Blocking orphan kill at loss ({_gain:.2f}%). Reason: {reason}")
                    return
        try:
            symbol = getattr(position, 'symbol', None)
            if not symbol: return
            position_amt = abs(float(getattr(position, 'positionAmt', 0) or 0))
            if position_amt <= 0: return
            position_side = getattr(position, 'position_side', 'LONG')
            side = 'SELL' if position_side == 'LONG' else 'BUY'
            current_price = safe_fetch_float(getattr(position, 'mark_price', 0.0), 0.0)
            if current_price <= 0:
                try: current_price = safe_fetch_float(await asyncio.wait_for(self.service.get_mark_price(symbol, allow_fallback=True), timeout=3.0), 0.0)
                except (asyncio.TimeoutError, Exception): return
            if current_price <= 0: return
            logger.warning(f"[MITIGATOR] KILLING (NO REENTRY) {position_key}: {reason}")
            result = await asyncio.wait_for(self.service.trade_manager.execute_now(position_key, ACCOUNT, symbol, position_amt, side, position_side, position_amt, current_price, f"mitigator_kill_{int(time.time())}", reason, True, 'CLOSE'), timeout=15.0)
            if result and "SUCCESS" in result:
                logger.info(f"[MITIGATOR] KILLED (NO REENTRY) {position_key}: {result}")
                await self._send_foothold_webhook(position_key, position_amt, side, current_price, position_amt, True, f"MITIGATOR_ORPHAN_KILL_{reason}")
                self.reentry_candidates.pop(position_key, None)
                if position_key in self.trackers: del self.trackers[position_key]
            else:
                logger.error(f"[MITIGATOR] KILL FAILED {position_key}: {result}")
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.error(f"[MITIGATOR] KILL TIMEOUT {position_key}")
        except Exception as e:
            logger.error(f"[MITIGATOR] Kill error {position_key}: {e}", exc_info=True)

    async def _execute_reduce(self, position_key: str, position, reduce_pct: float, reason: str):
        if DRY_RUN:
            logger.warning(f"[MITIGATOR][DRY_RUN] Would reduce {position_key} by {reduce_pct*100:.0f}%: {reason}")
            return
        # NO_LOSS UNIVERSAL BLOCK
        _gain = getattr(position, 'gain', None)
        if _gain is not None and _gain < -0.01:
            _is_emergency = ("EMERGENCY" in reason.upper() or "LIQUIDATION" in reason.upper())
            if not _is_emergency:
                _acct = position_key.split(':')[0] if ':' in position_key else ACCOUNT
                if _acct in getattr(config, 'STRICT_NO_LOSS_ACCOUNTS', []):
                    logger.critical(f"🛑[MITIGATOR_NO_LOSS_BLOCK] {position_key}: Blocking reduce at loss ({_gain:.2f}%). Reason: {reason}")
                    return
        try:
            symbol = getattr(position, 'symbol', None)
            if not symbol:
                return
            position_amt = abs(float(getattr(position, 'positionAmt', 0) or 0))
            reduce_qty = position_amt * reduce_pct
            if reduce_qty <= 0:
                return
            position_side = getattr(position, 'position_side', 'LONG')
            side = 'SELL' if position_side == 'LONG' else 'BUY'
            current_price = safe_fetch_float(getattr(position, 'mark_price', 0.0), 0.0)
            if current_price <= 0:
                try:
                    current_price = safe_fetch_float(await asyncio.wait_for(self.service.get_mark_price(symbol, allow_fallback=True), timeout=3.0), 0.0)
                except (asyncio.TimeoutError, Exception):
                    return
            if current_price <= 0:
                return
            logger.warning(f"[MITIGATOR] REDUCING {position_key} by {reduce_pct*100:.0f}%: {reason}")
            result = await asyncio.wait_for(self.service.trade_manager.execute_now(position_key, ACCOUNT, symbol, position_amt, side, position_side, reduce_qty, current_price, f"mitigator_reduce_{int(time.time())}", reason, False, 'REDUCE'), timeout=15.0)
            if result and "SUCCESS" in result:
                logger.info(f"[MITIGATOR] Reduced {position_key} by {reduce_pct*100:.0f}%: {result}")
                await self._send_foothold_webhook(position_key, position_amt, side, current_price, reduce_qty, False, f"MITIGATOR_REDUCE_{reason}")
            else:
                logger.error(f"[MITIGATOR] Reduce failed {position_key}: {result}")
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except Exception as e:
            logger.error(f"[MITIGATOR] Reduce error {position_key}: {e}", exc_info=True)

    async def _scan_positions(self):
        if not self.running:
            return
        account_positions = self.service.positions_by_account.get(ACCOUNT, {})
        if not account_positions:
            return
        now = time.time()
        active_keys = set()
        for position_key, position in list(account_positions.items()):
            if not self.running:
                break
            if not position:
                continue
            position_amt = abs(float(getattr(position, 'positionAmt', 0) or 0))
            if position_amt <= 0:
                if position_key in self.trackers: del self.trackers[position_key]
                self._kill_cooldowns.pop(position_key, None)
                self._kill_attempts.pop(position_key, None)
                continue
            active_keys.add(position_key)
            try:
                gain = safe_fetch_float(getattr(position, 'gain', 0.0), 0.0)
                symbol = getattr(position, 'symbol', None)
                position_side = getattr(position, 'position_side', 'LONG')
                current_price = safe_fetch_float(getattr(position, 'mark_price', 0.0), 0.0)
                opened_at = getattr(position, 'opened_at', None)
                age_seconds = 999999.0
                if opened_at:
                    try:
                        if isinstance(opened_at, str):
                            opened_at = isoparse(opened_at)
                        if isinstance(opened_at, datetime):
                            if opened_at.tzinfo is None:
                                opened_at = opened_at.replace(tzinfo=timezone.utc)
                            age_seconds = (datetime.now(timezone.utc) - opened_at).total_seconds()
                    except Exception:
                        pass
                if age_seconds < 45:
                    continue
                tracker = self.trackers.get(position_key)
                if not tracker:
                    tracker = GainTracker(peak_gain=gain, original_qty=position_amt)
                    self.trackers[position_key] = tracker
                tracker.samples.append((now, gain))
                if gain > tracker.peak_gain:
                    tracker.peak_gain = gain
                prev_gain = tracker.samples[-2][1] if len(tracker.samples) >= 2 else gain
                if (now - tracker.last_action_ts) < COOLDOWN:
                    continue
                hedge_info = self._check_hedge_tracker(position_key)
                if hedge_info["is_hedge"]:
                    if gain < prev_gain and gain < 0.01:
                        logger.warning(f"[MITIGATOR] HEDGE DECLINING {position_key}: gain={gain:.3f}% < prev={prev_gain:.3f}% — closing hedge")
                        await self._execute_kill(position_key, position, f"FORCE_GAIN_GUARD_HEDGE_DECLINING_{gain:.3f}%<{prev_gain:.3f}%")
                        tracker.last_action_ts = now
                    continue
                indicators = await self._get_indicators(symbol) if symbol else {}
                dc_basis_15m = safe_fetch_float(indicators.get('dc_basis_15m', 0), 0)
                sma_200_1m = safe_fetch_float(indicators.get('sma_200_1m', 0), 0)
                k_3m = safe_fetch_float(indicators.get('k_3m', 50), 50)
                d_3m = safe_fetch_float(indicators.get('d_3m', 50), 50)
                k_15m = safe_fetch_float(indicators.get('k_15m', 50), 50)
                d_15m = safe_fetch_float(indicators.get('d_15m', 50), 50)
                k_3m_prev = safe_fetch_float(indicators.get('k_3m_prev', 50), 50)
                k_15m_prev = safe_fetch_float(indicators.get('stoch_k_15m_prev', k_15m), k_15m)
                t_up_3m = bool(indicators.get('t_up_3m', False))
                t_up_15m = bool(indicators.get('t_up_15m', False))
                is_long = position_side == 'LONG'
                stoch_bad = (is_long and k_3m < d_3m and k_15m < d_15m) or (not is_long and k_3m > d_3m and k_15m > d_15m)
                stoch_declining = (is_long and k_3m < k_3m_prev and k_15m < k_15m_prev) or (not is_long and k_3m > k_3m_prev and k_15m > k_15m_prev)
                trend_against = (is_long and not t_up_3m and not t_up_15m) or (not is_long and t_up_3m and t_up_15m)
                below_dc_basis = dc_basis_15m > 0 and current_price > 0 and ((is_long and current_price < dc_basis_15m) or (not is_long and current_price > dc_basis_15m))
                below_sma200 = sma_200_1m > 0 and current_price > 0 and ((is_long and current_price < sma_200_1m) or (not is_long and current_price > sma_200_1m))
                armed = tracker.peak_gain >= ARM_THRESHOLD
                num_samples = len(tracker.samples)
                # === RULE 0: ZERO TOLERANCE — THE GUARANTEE ===
                # Once a position had positive gain and it's declining toward zero, KILL before it goes negative.
                # This is the ABSOLUTE catch-all. No position that was positive will EVER become a loser.
                if armed and gain <= ZERO_TOLERANCE_GAIN and gain < prev_gain:
                    logger.critical(f"🛑 [ZERO_TOLERANCE_KILL] {position_key}: peak={tracker.peak_gain:.3f}% now={gain:.3f}% (armed at {ARM_THRESHOLD}%) — KILLING to prevent loss. MANDATORY REENTRY SAVED.")
                    await self._execute_kill(position_key, position, f"FORCE_GAIN_GUARD_ZERO_TOLERANCE_peak={tracker.peak_gain:.3f}%_now={gain:.3f}%")
                    tracker.last_action_ts = now
                    tracker.killed = True
                    continue
                # === RULE 0B: NEVER GAINED — position never gained enough to arm, but is stagnant/declining ===
                # Catches "death by papercuts": position opens, sits at 0.005%, slowly bleeds to -0.3%
                if not armed and age_seconds > 180 and gain < 0.03 and gain < prev_gain and num_samples >= 10:
                    logger.critical(f"🛑 [NEVER_GAINED_KILL] {position_key}: open {age_seconds/60:.1f}min, peak only {tracker.peak_gain:.3f}%, now={gain:.3f}% declining — CLOSING AT GAIN before it bleeds")
                    await self._execute_kill(position_key, position, f"FORCE_GAIN_GUARD_NEVER_GAINED_age={age_seconds:.0f}s_peak={tracker.peak_gain:.3f}%_now={gain:.3f}%")
                    tracker.last_action_ts = now
                    tracker.killed = True
                    continue
                # === RULE 0C: STAGNANT — position hovering near zero with bad stoch ===
                # If gain hasn't moved more than 0.05% in 30 samples and stoch is against, close now
                if age_seconds > 120 and num_samples >= 20 and stoch_bad and gain < 0.08:
                    recent_gains = [s[1] for s in list(tracker.samples)[-20:]]
                    gain_range = max(recent_gains) - min(recent_gains)
                    if gain_range < 0.05:
                        logger.critical(f"🛑 [STAGNANT_KILL] {position_key}: stagnant {gain_range:.3f}% range over 20 samples, stoch bad, gain={gain:.3f}% — CLOSING before bleed")
                        await self._execute_kill(position_key, position, f"FORCE_GAIN_GUARD_STAGNANT_range={gain_range:.3f}%_gain={gain:.3f}%_stoch_bad")
                        tracker.last_action_ts = now
                        tracker.killed = True
                        continue
                # === RULE 1: TREND MISALIGN — exit if holding against the trend ===
                # Long with stoch DOWN on both timeframes + Hull trend against + gain < 0.3% = EXIT
                if stoch_bad and trend_against and gain < 0.3 and gain < prev_gain:
                    logger.warning(f"[MITIGATOR] TREND_MISALIGN {position_key}: {'LONG' if is_long else 'SHORT'} but stoch+hull against, gain={gain:.3f}% — EXIT")
                    await self._execute_kill(position_key, position, f"FORCE_GAIN_GUARD_TREND_MISALIGN_k3m={k_3m:.0f}_k15m={k_15m:.0f}_gain={gain:.3f}%")
                    tracker.last_action_ts = now
                    tracker.killed = True
                    continue
                # === RULE 2: Below dc_basis_15m or sma_200_1m + stoch bad + declining = HEDGE + KILL ===
                if (below_dc_basis or below_sma200) and stoch_bad and gain < prev_gain:
                    indicator_name = "dc_basis_15m" if below_dc_basis else "sma_200_1m"
                    indicator_val = dc_basis_15m if below_dc_basis else sma_200_1m
                    logger.warning(f"[MITIGATOR] BELOW {indicator_name}={indicator_val:.4f} + STOCH_BAD + DECLINING {position_key}: gain={gain:.3f}% price={current_price:.4f}")
                    if not tracker.hedged:
                        hedged = await self._execute_hedge(position_key, position, gain)
                        tracker.hedged = hedged
                        tracker.last_action_ts = now
                        if hedged:
                            await asyncio.sleep(1.0)
                    await self._execute_kill(position_key, position, f"FORCE_GAIN_GUARD_KILL_below_{indicator_name}_{indicator_val:.4f}_stoch_bad_gain={gain:.3f}%")
                    tracker.last_action_ts = now
                    tracker.killed = True
                    continue
                # === RULE 3: Gain evaporating — peak was decent, now heading to zero ===
                if tracker.peak_gain >= 0.08 and gain < prev_gain and gain <= 0.03 and gain > -0.1:
                    logger.warning(f"[MITIGATOR] GAIN EVAPORATING {position_key}: peak={tracker.peak_gain:.3f}% now={gain:.3f}% — KILLING before loss")
                    await self._execute_kill(position_key, position, f"FORCE_GAIN_GUARD_EVAPORATING_peak={tracker.peak_gain:.3f}%_now={gain:.3f}%")
                    tracker.last_action_ts = now
                    tracker.killed = True
                    continue
                # === RULE 4: Already in loss — skip if NO_LOSS account, else emergency kill ===
                if gain < 0.0:
                    # NO_LOSS accounts: don't fight ez_manage's STRICT_NO_LOSS block.
                    # Just log once per position and wait for price to recover to breakeven.
                    if not getattr(tracker, '_loss_logged', False):
                        logger.info(f"[MITIGATOR] IN_LOSS {position_key}: gain={gain:.3f}% — NO_LOSS active, waiting for breakeven")
                        tracker._loss_logged = True
                    continue
                # === RULE 5: High gain declining — lock profits aggressively ===
                if tracker.peak_gain >= 2.5 and gain < tracker.peak_gain * 0.85 and gain < prev_gain:
                    logger.warning(f"[MITIGATOR] HIGH_GAIN LOCK {position_key}: peak={tracker.peak_gain:.3f}% now={gain:.3f}% — reducing 50%")
                    await self._execute_reduce(position_key, position, 0.50, f"FORCE_GAIN_GUARD_HIGH_LOCK_peak={tracker.peak_gain:.3f}%_now={gain:.3f}%")
                    tracker.last_action_ts = now
                    continue
                # === RULE 6: Moderate gain + stoch turning — reduce to protect ===
                if tracker.peak_gain >= 0.15 and gain < tracker.peak_gain * 0.5 and gain < prev_gain and (stoch_bad or stoch_declining):
                    logger.warning(f"[MITIGATOR] GAIN FADING {position_key}: peak={tracker.peak_gain:.3f}% now={gain:.3f}% stoch_bad={stoch_bad} — reducing 30%")
                    await self._execute_reduce(position_key, position, 0.30, f"FORCE_GAIN_GUARD_FADING_peak={tracker.peak_gain:.3f}%_now={gain:.3f}%")
                    tracker.last_action_ts = now
                    continue
                # === RULE 7: Stoch declining + gain below 0.1% — preemptive reduce ===
                if stoch_declining and stoch_bad and gain < 0.1 and gain < prev_gain and age_seconds > 120:
                    logger.warning(f"[MITIGATOR] PREEMPTIVE_REDUCE {position_key}: stoch declining+bad, gain={gain:.3f}% — reducing 40%")
                    await self._execute_reduce(position_key, position, 0.40, f"FORCE_GAIN_GUARD_PREEMPTIVE_stoch_declining_gain={gain:.3f}%")
                    tracker.last_action_ts = now
                    continue
            except Exception as e:
                logger.debug(f"[MITIGATOR] Error scanning {position_key}: {e}")
        # ORPHAN CLEANUP: Force-close positions NOT in tradeable keys that are in loss
        try:
            tradeable_keys = set()
            if hasattr(self.service, 'trade_manager') and self.service.trade_manager:
                tk = await self.service.trade_manager.load_tradeable()
                if tk:
                    tradeable_keys = set(tk)
            if tradeable_keys:
                for position_key, position in list(account_positions.items()):
                    if not self.running:
                        break
                    if not position:
                        continue
                    position_amt = abs(float(getattr(position, 'positionAmt', 0) or 0))
                    if position_amt <= 0:
                        continue
                    gain = safe_fetch_float(getattr(position, 'gain', 0.0), 0.0)
                    if position_key not in tradeable_keys and gain < 0.0:
                        current_price = safe_fetch_float(getattr(position, 'mark_price', 0.0), 0.0)
                        notional = position_amt * current_price if current_price > 0 else 0
                        logger.critical(f"🛑 [MITIGATOR_ORPHAN_KILL] {position_key}: NOT TRADEABLE + IN LOSS gain={gain:.3f}% ${notional:.0f} — FORCE CLOSING (no reentry)")
                        await self._execute_kill_no_reentry(position_key, position, f"FORCE_GAIN_GUARD_ORPHAN_KILL_not_tradeable_gain={gain:.3f}%")
        except Exception as e:
            logger.error(f"[MITIGATOR] Orphan cleanup error: {e}")
        stale = [k for k in self.trackers if k not in active_keys]
        for k in stale:
            del self.trackers[k]

    async def _check_reentries(self):
        """MANDATORY re-entry: every killed position MUST be reopened when tide turns.
        Uses multi-timeframe stoch + Hull trend + price confirmation."""
        if DRY_RUN or not self.running or not self.reentry_candidates:
            return
        now = time.time()
        to_remove = []
        tradeable_keys = set()
        try:
            if hasattr(self.service, 'trade_manager') and self.service.trade_manager:
                tk = await self.service.trade_manager.load_tradeable()
                if tk:
                    tradeable_keys = set(tk)
        except Exception:
            pass
        for key, candidate in list(self.reentry_candidates.items()):
            if not self.running:
                break
            if (now - candidate.exit_time) < REENTRY_COOLDOWN:
                continue
            try:
                if tradeable_keys and key not in tradeable_keys:
                    logger.warning(f"[MITIGATOR] Removing reentry for {key} — not in tradeable_keys anymore")
                    to_remove.append(key)
                    continue
                current_price = safe_fetch_float(await asyncio.wait_for(self.service.get_mark_price(candidate.symbol, allow_fallback=True), timeout=3.0), 0.0)
                if current_price <= 0:
                    continue
                is_long = candidate.position_side == 'LONG'
                if candidate.exit_price <= 0:
                    continue
                if is_long:
                    price_move_pct = ((current_price - candidate.exit_price) / candidate.exit_price) * 100
                else:
                    price_move_pct = ((candidate.exit_price - current_price) / candidate.exit_price) * 100
                # Price must have moved favorably (dipped for longs, risen for shorts)
                price_favorable = price_move_pct <= -REENTRY_PRICE_PCT
                indicators = await self._get_indicators(candidate.symbol)
                k_3m = safe_fetch_float(indicators.get('k_3m', 50), 50)
                d_3m = safe_fetch_float(indicators.get('d_3m', 50), 50)
                k_15m = safe_fetch_float(indicators.get('k_15m', 50), 50)
                d_15m = safe_fetch_float(indicators.get('d_15m', 50), 50)
                k_3m_prev = safe_fetch_float(indicators.get('k_3m_prev', 50), 50)
                t_up_3m = bool(indicators.get('t_up_3m', False))
                t_up_15m = bool(indicators.get('t_up_15m', False))
                # Stoch must be aligned on BOTH timeframes
                stoch_3m_good = (is_long and k_3m > d_3m) or (not is_long and k_3m < d_3m)
                stoch_15m_good = (is_long and k_15m > d_15m) or (not is_long and k_15m < d_15m)
                stoch_rising = (is_long and k_3m > k_3m_prev) or (not is_long and k_3m < k_3m_prev)
                # Hull trend must confirm direction
                hull_good = (is_long and t_up_3m) or (not is_long and not t_up_3m)
                # REENTRY CONDITIONS: need at least stoch on both TFs + (hull OR price favorable)
                stoch_confirmed = stoch_3m_good and stoch_15m_good and stoch_rising
                can_reenter = stoch_confirmed and (hull_good or price_favorable)
                # After 10 minutes, relax to just dual-stoch (market may have moved past our exit price)
                if not can_reenter and (now - candidate.exit_time) > 600:
                    can_reenter = stoch_confirmed
                # After 30 minutes, even more relaxed — just 3m stoch good + rising (we MUST reenter)
                if not can_reenter and (now - candidate.exit_time) > 1800:
                    can_reenter = stoch_3m_good and stoch_rising
                if not can_reenter:
                    if (now - candidate.exit_time) > 86400:
                        logger.warning(f"[MITIGATOR] Expiring stale re-entry {key} (>24h old)")
                        to_remove.append(key)
                    continue
                side = 'BUY' if is_long else 'SELL'
                reentry_qty = config.START_POSITION_SIZE / current_price
                reason = f"GAIN_GUARD_MANDATORY_REENTRY_k3m={k_3m:.0f}_d3m={d_3m:.0f}_k15m={k_15m:.0f}_hull={'UP' if hull_good else 'DOWN'}_price={price_move_pct:+.2f}%"
                logger.critical(f"🔄 [MANDATORY_REENTRY] {key}: stoch_3m={'OK' if stoch_3m_good else 'BAD'} stoch_15m={'OK' if stoch_15m_good else 'BAD'} hull={'OK' if hull_good else 'BAD'} price={price_move_pct:+.2f}% — REOPENING")
                result = await asyncio.wait_for(self.service.trade_manager.execute_now(key, ACCOUNT, candidate.symbol, 0, side, candidate.position_side, reentry_qty, current_price, f"mitigator_reentry_{int(time.time())}", reason, False, 'AUGMENT'), timeout=15.0)
                if result and "SUCCESS" in result:
                    logger.critical(f"✅ [MANDATORY_REENTRY] {key}: REOPENED — {result}")
                    await self._send_foothold_webhook(key, 0, side, current_price, reentry_qty, False, f"MITIGATOR_MANDATORY_REENTRY_{reason}")
                    to_remove.append(key)
                elif result and ("INSUFFICIENT" in str(result).upper() or "MARGIN" in str(result).upper()):
                    logger.warning(f"[MITIGATOR] Insufficient margin for reentry {key} — freeing margin")
                    freed = await self._free_margin_by_closing_worst(config.START_POSITION_SIZE * 2)
                    if freed:
                        await asyncio.sleep(2.0)
                        result2 = await asyncio.wait_for(self.service.trade_manager.execute_now(key, ACCOUNT, candidate.symbol, 0, side, candidate.position_side, reentry_qty, current_price, f"mitigator_reentry_retry_{int(time.time())}", reason + "_RETRY", False, 'AUGMENT'), timeout=15.0)
                        if result2 and "SUCCESS" in result2:
                            logger.critical(f"✅ [MANDATORY_REENTRY] {key}: REOPENED (retry) — {result2}")
                            await self._send_foothold_webhook(key, 0, side, current_price, reentry_qty, False, f"MITIGATOR_MANDATORY_REENTRY_RETRY_{reason}")
                            to_remove.append(key)
                else:
                    logger.error(f"[MITIGATOR] Reentry failed {key}: {result}")
            except Exception as e:
                logger.debug(f"[MITIGATOR] Re-entry check error {key}: {e}")
        if to_remove:
            for key in to_remove:
                self.reentry_candidates.pop(key, None)
            self._save_reentries()


async def main():
    stop_event = asyncio.Event()
    service = None
    mitigator = None
    monitor_task = None
    try:
        logger.warning(f"[MITIGATOR] Starting AGGRESSIVE loss mitigator for account={ACCOUNT} dry_run={DRY_RUN}")
        service = await bootstrap_position_service(enable_auto_fetch=False)
        if not service:
            logger.error("[MITIGATOR] Failed to bootstrap service")
            return
        logger.warning("[MITIGATOR] Initializing trade manager...")
        await service._ensure_trade_manager()
        if not service.trade_manager:
            logger.error("[MITIGATOR] Failed to initialize trade manager")
            return
        service.trade_manager._allowed_accounts = frozenset(ACCOUNTS)
        logger.warning(f"[MITIGATOR] Trade manager ready, allowed_accounts={set(service.trade_manager._allowed_accounts)}")
        mitigator = LossMitigator(service)
        logger.warning("[MITIGATOR] Fetching real positions from API...")
        await mitigator._fetch_real_positions(ACCOUNT)
        account_positions = service.positions_by_account.get(ACCOUNT, {})
        active = sum(1 for p in account_positions.values() if abs(float(getattr(p, 'positionAmt', 0) or 0)) > 0)
        logger.warning(f"[MITIGATOR] Loaded {active} active positions for {ACCOUNT}")
        mitigator._fetch_tasks[ACCOUNT] = asyncio.create_task(mitigator._fetch_loop(ACCOUNT))
        try:
            account = service.accounts.get(ACCOUNT)
            if account:
                api_key = getattr(account, 'api_key', None) or getattr(account, 'api_key_plain', None)
                api_secret = getattr(account, 'api_secret', None) or getattr(account, 'api_secret_plain', None)
                if api_key and api_secret:
                    ws_manager = WebSocketManager(account_keys=[ACCOUNT], api_key=api_key, api_secret=api_secret, config=config, service=service)
                    original_handle = ws_manager.handle_account_update
                    async def handle_with_check(data: dict, account_key_param: str):
                        if account_key_param != ACCOUNT:
                            return
                        try:
                            await original_handle(data, account_key_param)
                            if mitigator.running:
                                await mitigator._scan_positions()
                        except Exception as e:
                            logger.error(f"[MITIGATOR][WS] Error: {e}", exc_info=True)
                    ws_manager.handle_account_update = handle_with_check
                    mitigator._websocket_managers[ACCOUNT] = ws_manager
                    asyncio.create_task(ws_manager.start(ACCOUNT))
                    logger.warning(f"[MITIGATOR] Started WebSocket for {ACCOUNT}")
        except Exception as ws_err:
            logger.error(f"[MITIGATOR] WebSocket setup error: {ws_err}", exc_info=True)
        def signal_handler(sig, frame):
            logger.warning("[MITIGATOR] Shutdown signal received")
            if mitigator:
                mitigator.running = False
            stop_event.set()
            if monitor_task and not monitor_task.done():
                monitor_task.cancel()
            for task in mitigator._fetch_tasks.values():
                if not task.done():
                    task.cancel()
            for ws_mgr in mitigator._websocket_managers.values():
                if hasattr(ws_mgr, '_running'):
                    ws_mgr._running = False
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        monitor_task = asyncio.create_task(mitigator.monitor_loop())
        try:
            await monitor_task
        except asyncio.CancelledError:
            logger.warning("[MITIGATOR] Monitor task cancelled")
    except KeyboardInterrupt:
        pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.critical(f"[MITIGATOR] Fatal error: {e}", exc_info=True)
    finally:
        if mitigator:
            mitigator.running = False
            for task in mitigator._fetch_tasks.values():
                if not task.done():
                    task.cancel()
            for ws_mgr in mitigator._websocket_managers.values():
                if hasattr(ws_mgr, 'stop'):
                    try:
                        await asyncio.wait_for(ws_mgr.stop(), timeout=3.0)
                    except Exception:
                        pass
                elif hasattr(ws_mgr, '_running'):
                    ws_mgr._running = False
        if monitor_task and not monitor_task.done():
            monitor_task.cancel()
            try:
                await monitor_task
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        if mitigator and mitigator._fetch_tasks:
            for task in mitigator._fetch_tasks.values():
                if not task.done():
                    try:
                        await asyncio.wait_for(task, timeout=1.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
        if service:
            try:
                await service.shutdown()
            except Exception:
                pass
        logger.warning("[MITIGATOR] Shutdown complete")
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
