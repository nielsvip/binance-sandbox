
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
from ez_positions import atomic_save_positions  # Use the same save function as main script

load_environment_from_gpg(None)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("ez_positions_backup")

async def save_from_redis_to_file(position_service, account_key: str) -> None:
    """Save positions from Redis to JSON files - Redis is the source of truth"""
    try:
        redis_manager = await get_simple_redis_manager()
        if not redis_manager:
            logger.warning(f"[BACKUP_REDIS_SAVE][{account_key}] ⚠️ No Redis manager - skipping Redis save")
            return
        all_positions_by_side: Dict[str, Dict[str, Any]] = {"LONG": {}, "SHORT": {}}
        # Try main Redis key first (positions:account_key)
        main_redis_key = f"positions:{account_key}"
        data = await redis_manager.get(main_redis_key)
        if data:
            try:
                parsed = json.loads(data)
                if isinstance(parsed, dict) and "positions" in parsed:
                    # Parse positions by side
                    for pos_key, pos_data in parsed["positions"].items():
                        if isinstance(pos_data, dict):
                            side = pos_data.get("position_side", "LONG")
                            if side in all_positions_by_side:
                                all_positions_by_side[side][pos_key] = pos_data
                    logger.info(f"[BACKUP_REDIS_SAVE][{account_key}] ✅ Loaded {sum(len(v) for v in all_positions_by_side.values())} positions from Redis key {main_redis_key}")
            except Exception as parse_err:
                logger.error(f"[BACKUP_REDIS_SAVE][{account_key}] ❌ Failed to parse Redis data: {parse_err}")
        # Save to files
        for side, positions_dict in all_positions_by_side.items():
            if not positions_dict:
                continue
            main_file = position_service.get_position_file(account_key, side)
            sorted_positions = position_service._sort_positions_dict(positions_dict)
            temp_file = main_file.parent / f".{main_file.name}.tmp.{int(time.time() * 1000000)}"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(sorted_positions, f, indent=2, ensure_ascii=False, default=str)
            if main_file.exists():
                backup_dir = main_file.parent / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
                backup_file = backup_dir / f"{main_file.stem}_backup_{timestamp}.json"
                shutil.copy2(main_file, backup_file)
                # Cleanup old backups
                try:
                    from ez_positions_service import PositionService
                    if hasattr(position_service, 'prune_old_backups'):
                        asyncio.create_task(position_service.prune_old_backups(str(backup_dir), main_file.stem))
                except Exception:
                    pass
            shutil.move(str(temp_file), str(main_file))
            logger.warning(f"[BACKUP_REDIS_SAVE][{account_key}:{side}] ✅✅✅ Saved {len(sorted_positions)} positions from Redis to {main_file.name}")
    except Exception as e:
        logger.error(f"[BACKUP_REDIS_SAVE][{account_key}] ❌ Redis save failed: {e}", exc_info=True)

async def backup_fetch_loop(position_service, account_key: str, running: Dict[str, bool], ws_managers: Dict[str, WebSocketManager]):
    """Full fetch-broadcast-save loop every 6 seconds with WebSocket support"""
    logger.warning(f"[BACKUP_FETCH][{account_key}] 🚨 Starting backup fetcher loop")
    # CRITICAL: DO NOT call load_all() here - it will overwrite fresh data with old file data!
    # We fetch fresh from API and update memory - never reload old files after fetching
    loop_count = 0
    last_full_broadcast = 0.0
    full_broadcast_interval = 60.0
    while running.get(account_key, True):
        loop_count += 1
        try:
            if loop_count % 15 == 0 or loop_count <= 3:
                logger.warning(f"[BACKUP_FETCH][{account_key}] 🔄 Loop #{loop_count}")
            # CRITICAL: Use process_account_update to get ALL position fields and handle augmentations/reductions
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
                            logger.info(f"[FETCHFETCH]ez_pos_backup[{account_key}] 🚨 Fetched {positions_data}")
                            # CRITICAL: process_account_update handles ALL fields, augmentations, reductions
                            updated_keys = await position_service.process_account_update(
                                account_key, positions_data, single=False, skip_broadcast_save=True
                            )
                            # CRITICAL: Broadcast to Redis FIRST (this is what fails first in main script)
                            # ALWAYS broadcast delta updates for each updated key immediately
                            if updated_keys:
                                try:
                                    await position_service._broadcast_position_updates_to_redis(account_key, updated_keys)
                                    logger.warning(f"[BACKUP_FETCH][{account_key}] ✅✅✅ Delta broadcast: {len(updated_keys)} updated keys to Redis (CRITICAL)")
                                except Exception as e:
                                    logger.critical(f"[BACKUP_FETCH][{account_key}] ❌❌❌ CRITICAL: Delta Redis broadcast failed: {e}", exc_info=True)
                            # Full broadcast every 60 seconds (or immediately if no updates but first loop)
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
                                                logger.warning(f"[BACKUP_FETCH][{account_key}] ✅✅✅ Full Redis broadcast: {len(all_positions_dict)} {side} positions (every 60s)")
                                        last_full_broadcast = now
                                except Exception as full_broadcast_err:
                                    logger.critical(f"[BACKUP_FETCH][{account_key}] ❌❌❌ CRITICAL: Full Redis broadcast failed: {full_broadcast_err}", exc_info=True)
                    except Exception as fetch_err:
                        logger.error(f"[BACKUP_FETCH][{account_key}] ❌ Fetch error: {fetch_err}", exc_info=True)
            # Full broadcast is now handled above after fetch (every 60s)
            # CRITICAL: Save to file AFTER Redis broadcast (Redis now has latest data)
            # Method 1: Try Redis first (after broadcasting, Redis has latest data)
            try:
                # CRITICAL: Use atomic_save_positions which saves from MEMORY (fresh data from API)
                await atomic_save_positions(position_service, account_key)
                logger.warning(f"[BACKUP_FETCH][{account_key}] ✅✅✅ Saved from MEMORY to files (fresh API data)")
            except Exception as mem_save_err:
                logger.critical(f"[BACKUP_FETCH][{account_key}] ❌❌❌ Memory save failed: {mem_save_err}")
            if loop_count % 15 == 0:
                logger.warning(f"[BACKUP_FETCH][{account_key}] ✅ Loop #{loop_count} completed")
        except Exception as e:
            logger.error(f"[BACKUP_FETCH][{account_key}] ❌ Loop error: {e}", exc_info=True)
        await asyncio.sleep(6.0)
    logger.critical(f"[BACKUP_FETCH][{account_key}] 🚨 Loop exited!")

async def main():
    logger.warning("[BACKUP] 🚨 Starting backup position fetcher with WebSocket support")
    try:
        service = await bootstrap_position_service(enable_auto_fetch=False, skip_file_load=True)  # skip_file_load=True - positions already loaded at boot
        if not service:
            logger.critical("[BACKUP] ❌ Failed to bootstrap service")
            return
        # CRITICAL: Ensure service is fully initialized for handle_augmentation/handle_reduction
        # These methods need: min_qty, reenter_level, augmentation_cooldown_map, reduction_cooldown_map, etc.
        if not hasattr(service, 'min_qty') or not service.min_qty:
            logger.warning("[BACKUP] ⚠️ Service min_qty not initialized - handle_augmentation/handle_reduction may fail")
        if not hasattr(service, 'reenter_level'):
            service.reenter_level = {}
        if not hasattr(service, 'augmentation_cooldown_map'):
            service.augmentation_cooldown_map = {}
        if not hasattr(service, 'reduction_cooldown_map'):
            service.reduction_cooldown_map = {}
        if not hasattr(service, 'orders_in_limbo'):
            service.orders_in_limbo = {}
        logger.warning("[BACKUP] ✅ Service initialized for full position updates (augmentations/reductions)")
        running = {ak: True for ak in service.accounts.keys()}
        ws_managers: Dict[str, WebSocketManager] = {}
        def signal_handler(sig, frame):
            logger.warning("[BACKUP] 🛑 Shutdown signal received")
            for ak in running:
                running[ak] = False
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        # CRITICAL: Start WebSocket managers for real-time updates (same as main script)
        ws_start_tasks = []
        for account_key, account in service.accounts.items():
            api_key = getattr(account, 'api_key', None) or getattr(account, 'api_key_plain', None)
            api_secret = getattr(account, 'api_secret', None) or getattr(account, 'api_secret_plain', None)
            if api_key and api_secret:
                logger.warning(f"[BACKUP] Creating WebSocket manager for {account_key}")
                ws_manager = WebSocketManager(
                    account_keys=[account_key],
                    api_key=api_key,
                    api_secret=api_secret,
                    config=service.config,
                    symbols=None,
                    service=service
                )
                ws_managers[account_key] = ws_manager
                ws_task = asyncio.create_task(ws_manager.start(account_key))
                ws_start_tasks.append((account_key, ws_task))
                logger.warning(f"[BACKUP] ✅ WebSocket task created for {account_key}")
        # Start WebSocket managers
        for account_key, ws_task in ws_start_tasks:
            try:
                await asyncio.sleep(0.1)
                if ws_task.done():
                    try:
                        await ws_task
                    except Exception as e:
                        logger.error(f"[BACKUP][{account_key}] WebSocket manager failed: {e}", exc_info=True)
                else:
                    logger.warning(f"[BACKUP][{account_key}] ✅ WebSocket manager task is running")
            except Exception as e:
                logger.error(f"[BACKUP][{account_key}] WebSocket manager error: {e}", exc_info=True)
        # CRITICAL: Start emergency reductions monitoring (same as main script)
        # This ensures emergency reductions are executed even in backup mode
        try:
            if hasattr(service, '_force_start_all_monitors'):
                asyncio.create_task(service._force_start_all_monitors())
                logger.warning("[BACKUP] ✅ Started emergency reductions monitoring (via _force_start_all_monitors)")
            elif hasattr(service, 'monitor_reductions_for_account'):
                for account_key in service.accounts.keys():
                    asyncio.create_task(service.monitor_reductions_for_account(account_key))
                    logger.warning(f"[BACKUP] ✅ Started emergency reductions monitoring for {account_key}")
        except Exception as monitor_err:
            logger.error(f"[BACKUP] ⚠️ Failed to start reductions monitoring: {monitor_err}", exc_info=True)
        # Start fetch loops
        tasks = [backup_fetch_loop(service, ak, running, ws_managers) for ak in service.accounts.keys()]
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.critical(f"[BACKUP] ❌ Fatal error: {e}", exc_info=True)
    finally:
        # Cleanup WebSocket managers
        for account_key, ws_manager in ws_managers.items():
            try:
                await ws_manager.stop()
                logger.warning(f"[BACKUP] ✅ Stopped WebSocket manager for {account_key}")
            except Exception as e:
                logger.error(f"[BACKUP] Error stopping WebSocket for {account_key}: {e}")

if __name__ == "__main__":
    asyncio.run(main())

