#!/home/niels/.conda/envs/binance_env/bin/python3
"""
ez_positions_backup_account.py - Per-account backup fetcher
==============================================
- Fetches for a SINGLE account when it falls behind
- Full WebSocket support and position updates
- Runs when watchdog detects account is stale
"""
import asyncio
import json
import logging
import signal
import sys
import time
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Set, Any

from ez_positions_service import bootstrap_position_service, WebSocketManager
from utils import load_environment_from_gpg, get_simple_redis_manager, orjson_default
from ez_positions import atomic_save_positions

load_environment_from_gpg(None)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("ez_positions_backup_account")

# DISABLED: save_from_redis_to_file - reads stale Redis data and overwrites fresh files!
# Memory is source of truth - we use atomic_save_positions which saves from MEMORY (fresh API data)
# async def save_from_redis_to_file(position_service, account_key: str) -> None:
#     """Save positions from Redis to JSON files - Redis is the source of truth"""
#     ... (DISABLED - use atomic_save_positions instead)

async def backup_fetch_loop_account(position_service, account_key: str, running: bool):
    """Full fetch-broadcast-save loop for SINGLE account every 6 seconds"""
    logger.warning(f"[BACKUP_ACCOUNT][{account_key}] 🚨 Starting backup fetcher for account")
    try:
        # CRITICAL: DO NOT load_all() - bootstrap_position_service already loaded positions ONCE
        # Reloading would overwrite fresh data with old file data!
        total_loaded = sum(len(acc_pos) for acc_pos in position_service.positions_by_account.values())
        if total_loaded == 0:
            logger.warning(f"[BACKUP_ACCOUNT][{account_key}] No positions in memory - loading ONCE...")
            #await position_service.load_all(force=True, startup=True)
            total_loaded = sum(len(acc_pos) for acc_pos in position_service.positions_by_account.values())
            logger.warning(f"[BACKUP_ACCOUNT][{account_key}] ✅ Loaded {total_loaded} positions ONCE")
        else:
            logger.warning(f"[BACKUP_ACCOUNT][{account_key}] ✅ Positions already in memory ({total_loaded}) - NOT reloading")
    except Exception as e:
        logger.error(f"[BACKUP_ACCOUNT][{account_key}] ❌ Load failed: {e}", exc_info=True)
    loop_count = 0
    last_full_broadcast = 0.0
    full_broadcast_interval = 60.0
    while running:
        loop_count += 1
        try:
            if loop_count % 15 == 0 or loop_count <= 3:
                logger.warning(f"[BACKUP_ACCOUNT][{account_key}] 🔄 Loop #{loop_count}")
            account = position_service.accounts.get(account_key)
            if account:
                client_obj = getattr(account, "client", None)
                if client_obj:
                    try:
                        positions_data = await asyncio.wait_for(
                            asyncio.to_thread(client_obj.futures_position_information),
                            timeout=30.0
                        )
                        if positions_data and isinstance(positions_data, list):
                            updated_keys = await position_service.process_account_update(
                                account_key, positions_data, single=False, skip_broadcast_save=True
                            )
                            # CRITICAL: Broadcast to Redis FIRST
                            if updated_keys:
                                try:
                                    await position_service._broadcast_position_updates_to_redis(account_key, updated_keys)
                                    logger.warning(f"[BACKUP_ACCOUNT][{account_key}] ✅✅✅ Delta broadcast: {len(updated_keys)} updated keys")
                                except Exception as e:
                                    logger.critical(f"[BACKUP_ACCOUNT][{account_key}] ❌❌❌ Delta broadcast failed: {e}", exc_info=True)
                            # Full broadcast every 60 seconds
                            now = time.time()
                            should_full_broadcast = (now - last_full_broadcast >= full_broadcast_interval) or (loop_count <= 1)
                            if should_full_broadcast:
                                try:
                                    account_positions = position_service.positions_by_account.get(account_key, {})
                                    if account_positions:
                                        all_positions_by_side: Dict[str, Dict[str, Any]] = {"LONG": {}, "SHORT": {}}
                                        for pos_key, pos_obj in account_positions.items():
                                            if pos_obj and hasattr(pos_obj, "to_dict"):
                                                side = pos_obj.position_side
                                                if side in all_positions_by_side:
                                                    all_positions_by_side[side][pos_key] = pos_obj.to_dict()
                                        for side, all_positions_dict in all_positions_by_side.items():
                                            if all_positions_dict:
                                                await position_service._broadcast_positions_to_redis(account_key, side, all_positions_dict, allow_incomplete=True)
                                                logger.warning(f"[BACKUP_ACCOUNT][{account_key}] ✅✅✅ Full broadcast: {len(all_positions_dict)} {side} positions")
                                        last_full_broadcast = now
                                except Exception as full_broadcast_err:
                                    logger.critical(f"[BACKUP_ACCOUNT][{account_key}] ❌❌❌ Full broadcast failed: {full_broadcast_err}", exc_info=True)
                    except Exception as fetch_err:
                        logger.error(f"[BACKUP_ACCOUNT][{account_key}] ❌ Fetch error: {fetch_err}", exc_info=True)
            # CRITICAL: DISABLED - Only ez_positions.py should save to position files to prevent overwriting fresh data
            # Backup scripts should only update memory and broadcast to Redis - NOT save to files
            logger.debug(f"[BACKUP_ACCOUNT][{account_key}] ⏸️ Save DISABLED - only ez_positions.py should write position files")
            # try:
            #     await atomic_save_positions(position_service, account_key, force=True)
            #     logger.warning(f"[BACKUP_ACCOUNT][{account_key}] ✅✅✅ Saved from memory to files (memory is source of truth)")
            # except Exception as mem_save_err:
            #     logger.critical(f"[BACKUP_ACCOUNT][{account_key}] ❌❌❌ Memory save failed: {mem_save_err}")
            if loop_count % 15 == 0:
                logger.warning(f"[BACKUP_ACCOUNT][{account_key}] ✅ Loop #{loop_count} completed")
        except Exception as e:
            logger.error(f"[BACKUP_ACCOUNT][{account_key}] ❌ Loop error: {e}", exc_info=True)
        await asyncio.sleep(6.0)
    logger.critical(f"[BACKUP_ACCOUNT][{account_key}] 🚨 Loop exited!")

async def main():
    if len(sys.argv) < 2:
        logger.critical("[BACKUP_ACCOUNT] ❌ Usage: ez_positions_backup_account.py <account_key>")
        return
    account_key = sys.argv[1]
    logger.warning(f"[BACKUP_ACCOUNT] 🚨 Starting backup fetcher for account: {account_key}")
    try:
        # CRITICAL: skip_file_load=True to prevent loading stale file data that overwrites fresh API/Redis data
        service = await bootstrap_position_service(enable_auto_fetch=False, skip_file_load=True)
        if not service:
            logger.critical("[BACKUP_ACCOUNT] ❌ Failed to bootstrap service")
            return
        if account_key not in service.accounts:
            logger.critical(f"[BACKUP_ACCOUNT] ❌ Account {account_key} not found in service")
            return
        # Initialize required attributes
        if not hasattr(service, 'reenter_level'): service.reenter_level = {}
        if not hasattr(service, 'augmentation_cooldown_map'): service.augmentation_cooldown_map = {}
        if not hasattr(service, 'reduction_cooldown_map'): service.reduction_cooldown_map = {}
        if not hasattr(service, 'orders_in_limbo'): service.orders_in_limbo = {}
        running = True
        ws_managers: Dict[str, WebSocketManager] = {}
        def signal_handler(sig, frame):
            logger.warning(f"[BACKUP_ACCOUNT][{account_key}] 🛑 Shutdown signal received")
            nonlocal running
            running = False
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        # Start WebSocket manager for this account
        account = service.accounts.get(account_key)
        if account:
            api_key = getattr(account, 'api_key', None) or getattr(account, 'api_key_plain', None)
            api_secret = getattr(account, 'api_secret', None) or getattr(account, 'api_secret_plain', None)
            if api_key and api_secret:
                logger.warning(f"[BACKUP_ACCOUNT][{account_key}] Creating WebSocket manager")
                ws_manager = WebSocketManager(
                    account_keys=[account_key],
                    api_key=api_key,
                    api_secret=api_secret,
                    config=service.config,
                    symbols=None,
                    service=service
                )
                ws_managers[account_key] = ws_manager
                asyncio.create_task(ws_manager.start(account_key))
                logger.warning(f"[BACKUP_ACCOUNT][{account_key}] ✅ WebSocket manager started")
        # Start emergency reductions monitoring
        try:
            if hasattr(service, '_force_start_all_monitors'):
                asyncio.create_task(service._force_start_all_monitors())
        except Exception as monitor_err:
            logger.error(f"[BACKUP_ACCOUNT][{account_key}] ⚠️ Failed to start reductions monitoring: {monitor_err}")
        # Start fetch loop for this account
        await backup_fetch_loop_account(service, account_key, running)
    except Exception as e:
        logger.critical(f"[BACKUP_ACCOUNT] ❌ Fatal error: {e}", exc_info=True)
    finally:
        for account_key, ws_manager in ws_managers.items():
            try:
                await ws_manager.stop()
            except Exception as e:
                logger.error(f"[BACKUP_ACCOUNT] Error stopping WebSocket: {e}")

if __name__ == "__main__":
    asyncio.run(main())

