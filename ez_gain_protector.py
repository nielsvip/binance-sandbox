#!/home/niels/.conda_envs/binance_env/bin/python3

"""ez_gain_protector.py - Protects positions that reached >0.15% gain by closing before they drop below 0.08%"""

import asyncio

import json

import logging

import os

import signal

import sys

import time

from datetime import datetime, timezone

from pathlib import Path

from typing import Dict, Set

from ez_positions_service import bootstrap_position_service, WebSocketManager

from utils import load_environment_from_gpg, safe_fetch_float, orjson_default

from config import Config

from dateutil.parser import isoparse

load_environment_from_gpg(None)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")

logger = logging.getLogger("ez_gain_protector")

logs_dir = Path.home() / "logs"

logs_dir.mkdir(parents=True, exist_ok=True)

from logging.handlers import RotatingFileHandler

file_handler = RotatingFileHandler(str(logs_dir / "ez_gain_protector.log"), maxBytes=100*1024*1024, backupCount=5, encoding='utf-8', mode='a')

file_handler.setLevel(logging.DEBUG)

file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))

logger.addHandler(file_handler)

logger.setLevel(logging.DEBUG)

config=Config()

class GainProtector:
    def __init__(self, service):
        self.service = service
        self.protected_positions: Dict[str, float] = {}  # position_key -> highest_gain_seen
        self.running = True
        self._last_fetch: Dict[str, float] = {}
        self._positions_refreshing: Dict[str, bool] = {}
        self._fetch_tasks: Dict[str, asyncio.Task] = {}
        self._websocket_managers: Dict[str, WebSocketManager] = {}
    async def _fetch_real_positions(self, account_key: str):
        """No-op: gain protector must NOT make direct API calls. Reads from positions_by_account."""
        pass

    async def _fetch_real_positions_DISABLED(self, account_key: str):
        """DISABLED — was making direct Binance API calls causing IP bans."""
        if not self.running or account_key not in self.service.accounts:
            return
        if self._positions_refreshing.get(account_key, False):
            return
        now = time.time()
        last = self._last_fetch.get(account_key, 0.0)
        if last > 0 and (now - last) < 3.0:
            return
        account = self.service.accounts.get(account_key)
        if not account or not getattr(account, "client", None):
            if not self.running:
                return
            try:
                client = await asyncio.wait_for(self.service._ensure_account_client(account_key), timeout=5.0)
                if not client or not self.running:
                    return
            except (asyncio.TimeoutError, Exception):
                return
        if not self.running:
            return
        self._positions_refreshing[account_key] = True
        try:
            client_obj = getattr(account, "client", None)
            if not client_obj or not self.running:
                return
            async with self.service.maker_semaphore:
                if not self.running:
                    return
                positions_data = await asyncio.wait_for(asyncio.to_thread(client_obj.futures_position_information), timeout=10.0)
            if not self.running:
                return
            if positions_data and isinstance(positions_data, list):
                await asyncio.wait_for(self.service.process_account_update(account_key, positions_data, single=False, skip_broadcast_save=True), timeout=5.0)
                if self.running:
                    self._last_fetch[account_key] = time.time()
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if not self.running:
                return
        except Exception as e:
            logger.debug(f"[GAIN_PROTECTOR] Fetch error for {account_key}: {e}")
        finally:
            self._positions_refreshing[account_key] = False
    async def _fetch_loop(self, account_key: str):
        """Continuous fetch loop for one account"""
        logger.warning(f"[GAIN_PROTECTOR][{account_key}] 🚀 Starting fetch loop")
        await asyncio.sleep(1.0)
        while self.running:
            try:
                if not self.running:
                    break
                await self._fetch_real_positions(account_key)
                if not self.running:
                    break
                await asyncio.sleep(6.0)
            except asyncio.CancelledError:
                logger.warning(f"[GAIN_PROTECTOR][{account_key}] Fetch loop cancelled")
                break
            except Exception as e:
                if not self.running:
                    break
                logger.error(f"[GAIN_PROTECTOR][{account_key}] Error in fetch loop: {e}", exc_info=True)
                await asyncio.sleep(6.0)
        logger.warning(f"[GAIN_PROTECTOR][{account_key}] Fetch loop stopped")
    async def monitor_loop(self):
        logger.warning("[GAIN_PROTECTOR] 🚀 Starting gain protection monitor")
        while self.running:
            try:
                if not self.running:
                    break
                await self._check_all_positions()
                for _ in range(int(config.CHECK_INTERVAL * 10)):
                    if not self.running:
                        break
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                logger.warning("[GAIN_PROTECTOR] Monitor loop cancelled")
                break
            except Exception as e:
                if not self.running:
                    break
                logger.error(f"[GAIN_PROTECTOR] Error in monitor loop: {e}", exc_info=True)
                for _ in range(int(config.CHECK_INTERVAL * 10)):
                    if not self.running:
                        break
                    await asyncio.sleep(0.1)
        logger.warning("[GAIN_PROTECTOR] Monitor loop stopped")
    async def _check_all_positions(self):
        if not self.running:
            return
        for account_key in list(self.service.accounts.keys()):
            if not self.running:
                break
            account_positions = self.service.positions_by_account.get(account_key, {})
            if not account_positions:
                continue
            positions_by_side = {"LONG": {}, "SHORT": {}}
            for position_key, position in list(account_positions.items()):
                if not self.running:
                    break
                if not position:
                    continue
                position_amt = abs(float(getattr(position, 'positionAmt', 0) or 0))
                if position_amt <= 0:
                    if position_key in self.protected_positions:
                        del self.protected_positions[position_key]
                    continue
                try:
                    gain = safe_fetch_float(getattr(position, 'gain', 0.0), 0.0)
                    opened_at = getattr(position, 'opened_at', None)
                    is_new_position = False
                    age_seconds = 999999.0
                    if opened_at:
                        try:
                            if isinstance(opened_at, str):
                                opened_at = isoparse(opened_at)
                            if isinstance(opened_at, datetime):
                                if opened_at.tzinfo is None:
                                    opened_at = opened_at.replace(tzinfo=timezone.utc)
                                age_seconds = (datetime.now(timezone.utc) - opened_at).total_seconds()
                                is_new_position = age_seconds < config.NEW_POSITION_MIN_AGE_SECONDS
                        except Exception:
                            pass
                    if gain >= config.STOP_LOSS_THRESHOLD:
                        if position_key not in self.protected_positions:
                            self.protected_positions[position_key] = gain
                            if hasattr(self.service, 'stop_manager') and self.service.stop_manager:
                                symbol_stop = getattr(position, 'symbol', None)
                                if symbol_stop:
                                    try:
                                        current_price_stop = safe_fetch_float(getattr(position, 'mark_price', 0.0), 0.0)
                                        if current_price_stop <= 0:
                                            current_price_stop = safe_fetch_float(await asyncio.wait_for(self.service.get_mark_price(symbol_stop, allow_fallback=True), timeout=3.0), 0.0)
                                        if current_price_stop > 0:
                                            await self.service.cancel_empty_stop(account_key, symbol_stop, position_key, current_price_stop, position_amt, require_tiny=False)
                                            if hasattr(self.service.stop_manager, 'stop_levels'):
                                                self.service.stop_manager.stop_levels.pop(position_key, None)
                                            logger.warning(f"[GAIN_PROTECTOR] 🛡️ Disabled stop loss for {position_key}: gain={gain:.3f}% >= threshold {config.STOP_LOSS_THRESHOLD:.3f}%")
                                    except Exception as e:
                                        logger.debug(f"[GAIN_PROTECTOR] Error disabling stop loss for {position_key}: {e}")
                    if position_key in self.protected_positions:
                        if gain < config.GAIN_THRESHOLD_LOW:
                            if is_new_position and gain >= config.NEW_POSITION_MAX_LOSS_THRESHOLD:
                                logger.warning(f"[GAIN_PROTECTOR] 🚫 BLOCKED CLOSE {position_key}: Position is new (age={age_seconds:.1f}s < {config.NEW_POSITION_MIN_AGE_SECONDS}s) and gain {gain:.3f}% >= threshold {config.NEW_POSITION_MAX_LOSS_THRESHOLD:.3f}%")
                            elif self.running:
                                await self._close_position(position_key, position, account_key, gain)
                    if self.running:
                        position_side = getattr(position, 'position_side', 'LONG')
                        if position_side in positions_by_side and hasattr(position, 'to_dict'):
                            positions_by_side[position_side][position_key] = position.to_dict()
                except Exception as e:
                    logger.debug(f"[GAIN_PROTECTOR] Error checking {position_key}: {e}")
            if self.running:
                await self._save_positions_to_file(account_key, positions_by_side)
    async def _save_positions_to_file(self, account_key: str, positions_by_side: Dict[str, Dict]):
        try:
            base_path = Path(self.service.base_path) if hasattr(self.service, 'base_path') else Path.cwd()
            account_dir = base_path / account_key
            account_dir.mkdir(parents=True, exist_ok=True)
            for side, positions_dict in positions_by_side.items():
                if not positions_dict:
                    continue
                file_path = account_dir / f"{side.lower()}_positions.updated.json"
                temp_file = account_dir / f".{side.lower()}_positions.updated.tmp.{int(time.time() * 1000000)}"
                if hasattr(self.service, '_sort_positions_dict'):
                    sorted_positions = self.service._sort_positions_dict(positions_dict, sort_by_value=True)
                else:
                    sorted_positions = dict(sorted(positions_dict.items(), key=lambda x: abs(float(x[1].get('positionAmt', 0) or 0) * float(x[1].get('mark_price', 0) or 0)), reverse=True))
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(sorted_positions, f, indent=2, ensure_ascii=False, default=str)
                temp_file.replace(file_path)
                logger.debug(f"[GAIN_PROTECTOR] Saved {len(sorted_positions)} {side} positions to {file_path.name}")
        except Exception as e:
            logger.debug(f"[GAIN_PROTECTOR] Error saving positions to file: {e}")
    async def _close_position(self, position_key: str, position, account_key: str, current_gain: float):
        if not self.running:
            return
        try:
            symbol = getattr(position, 'symbol', None)
            if not symbol or not self.running:
                return
            highest_gain = self.protected_positions.get(position_key, current_gain)
            logger.warning(f"[GAIN_PROTECTOR] 🚨 CLOSING {position_key}: gain dropped to {current_gain:.3f}% (was {highest_gain:.3f}%)")
            position_side = getattr(position, 'position_side', 'LONG')
            side = 'SELL' if position_side == 'LONG' else 'BUY'
            current_price = safe_fetch_float(getattr(position, 'mark_price', 0.0), 0.0)
            if current_price <= 0:
                if not self.running:
                    return
                try:
                    current_price = safe_fetch_float(await asyncio.wait_for(self.service.get_mark_price(symbol, allow_fallback=True), timeout=3.0), 0.0)
                except (asyncio.TimeoutError, Exception):
                    logger.error(f"[GAIN_PROTECTOR] ❌ Could not get price for {position_key}")
                    return
            if current_price <= 0 or not self.running:
                logger.error(f"[GAIN_PROTECTOR] ❌ Invalid price for {position_key}")
                return
            unique_id = f"gain_protector_{int(time.time())}"
            result = await asyncio.wait_for(self.service.execute_now(position_key, account_key, symbol, position.positionAmt, side, position_side, position.positionAmt, current_price, unique_id, f"GAIN_PROTECTION_{highest_gain:.3f}%->{current_gain:.3f}%", True, 'CLOSE'), timeout=15.0)
            if not self.running:
                return
            if result and result in ["SUCCESS", "SUCCESS_MAKER", "SUCCESS_MARKET", "SUCCESS_WEBHOOK"]:
                logger.info(f"[GAIN_PROTECTOR] ✅ Closed {position_key} successfully: {result}")
                if position_key in self.protected_positions:
                    del self.protected_positions[position_key]
            else:
                logger.error(f"[GAIN_PROTECTOR] ❌ Close failed for {position_key}: {result}")
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if not self.running:
                return
        except Exception as e:
            logger.error(f"[GAIN_PROTECTOR] ❌ Error closing {position_key}: {e}", exc_info=True)

async def main():
    stop_event = asyncio.Event()
    service = None
    protector = None
    monitor_task = None
    try:
        logger.warning("[GAIN_PROTECTOR] 🚀 Starting gain protection service")
        service = await bootstrap_position_service(enable_auto_fetch=False)
        if not service:
            logger.error("[GAIN_PROTECTOR] ❌ Failed to bootstrap service")
            return
        logger.warning("[GAIN_PROTECTOR] ⏳ Fetching real positions from API...")
        protector = GainProtector(service)
        config = Config()
        for account_key in list(service.accounts.keys()):
            if not protector.running:
                break
            await protector._fetch_real_positions(account_key)
        total_positions = sum(len(acc_pos) for acc_pos in service.positions_by_account.values())
        logger.warning(f"[GAIN_PROTECTOR] ✅ Loaded {total_positions} real positions from API")
        logger.warning("[GAIN_PROTECTOR] 🚀 Starting 5 fetch loops (one per account)...")
        for account_key in list(service.accounts.keys()):
            if not protector.running:
                break
            protector._fetch_tasks[account_key] = asyncio.create_task(protector._fetch_loop(account_key))
            logger.warning(f"[GAIN_PROTECTOR] ✅ Started fetch loop for {account_key}")
        logger.warning("[GAIN_PROTECTOR] 🚀 Starting 5 WebSocket managers (one per account)...")
        for account_key in list(service.accounts.keys()):
            if not protector.running:
                break
            try:
                account = service.accounts.get(account_key)
                if not account:
                    logger.error(f"[GAIN_PROTECTOR] ❌ Account {account_key} object is None - skipping websocket")
                    continue
                api_key = getattr(account, 'api_key', None) or getattr(account, 'api_key_plain', None)
                api_secret = getattr(account, 'api_secret', None) or getattr(account, 'api_secret_plain', None)
                if not api_key or not api_secret:
                    logger.error(f"[GAIN_PROTECTOR] ❌ Account {account_key} missing API credentials - skipping websocket")
                    continue
                ws_manager = WebSocketManager(account_keys=[account_key], api_key=api_key, api_secret=api_secret, config=config, service=service)
                original_handle = ws_manager.handle_account_update
                async def handle_with_check(data: dict, account_key_param: str):
                    if account_key_param != account_key:
                        return
                    try:
                        await original_handle(data, account_key_param)
                        if protector.running:
                            await protector._check_all_positions()
                    except Exception as e:
                        logger.error(f"[GAIN_PROTECTOR][WS][{account_key}] Error: {e}", exc_info=True)
                ws_manager.handle_account_update = handle_with_check
                protector._websocket_managers[account_key] = ws_manager
                asyncio.create_task(ws_manager.start(account_key))
                logger.warning(f"[GAIN_PROTECTOR] ✅ Started WebSocket manager for {account_key}")
            except Exception as ws_err:
                logger.critical(f"[GAIN_PROTECTOR] 🚨 Failed to start websocket for {account_key}: {ws_err}", exc_info=True)
        logger.warning(f"[GAIN_PROTECTOR] ✅ Started {len(protector._websocket_managers)} WebSocket managers")
        monitor_task = None
        def signal_handler(sig, frame):
            logger.warning("[GAIN_PROTECTOR] Shutdown signal received")
            if protector:
                protector.running = False
            stop_event.set()
            if monitor_task and not monitor_task.done():
                monitor_task.cancel()
            for task in protector._fetch_tasks.values():
                if not task.done():
                    task.cancel()
            for ws_mgr in protector._websocket_managers.values():
                if hasattr(ws_mgr, '_running'):
                    ws_mgr._running = False
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        monitor_task = asyncio.create_task(protector.monitor_loop())
        try:
            await monitor_task
        except asyncio.CancelledError:
            logger.warning("[GAIN_PROTECTOR] Monitor task cancelled")
    except KeyboardInterrupt:
        logger.info("[GAIN_PROTECTOR] Keyboard interrupt received")
    except asyncio.CancelledError:
        logger.info("[GAIN_PROTECTOR] Task cancelled")
    except Exception as e:
        logger.critical(f"[GAIN_PROTECTOR] ❌ Fatal error: {e}", exc_info=True)
    finally:
        if protector:
            protector.running = False
            for account_key, task in protector._fetch_tasks.items():
                if not task.done():
                    task.cancel()
            for ws_mgr in protector._websocket_managers.values():
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
            except asyncio.CancelledError:
                pass
        if protector and protector._fetch_tasks:
            for task in protector._fetch_tasks.values():
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
        logger.warning("[GAIN_PROTECTOR] Shutdown complete")
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
