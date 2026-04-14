#!/home/niels/.conda/envs/binance_env/bin/python3
"""
ez_positions_active.py - ACTIVE POSITIONS ONLY
- Only updates positions that are in symbols_active.json
- Reads from position service (already updated by ez_positions.py)
- Saves to active_positions.json
- Updates Redis
"""
import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Dict, Set
from config import Config
from ez_positions_service import bootstrap_position_service, load_accounts_from_config, PositionService
from utils import load_environment_from_gpg, orjson_default

load_environment_from_gpg(None)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("ez_positions_active")
_logs_dir = Path.home() / "logs"
_logs_dir.mkdir(parents=True, exist_ok=True)
from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(str(_logs_dir / "ez_positions_active.log"), maxBytes=100*1024*1024, backupCount=5, encoding='utf-8', mode='a')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s")
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)
logger.setLevel(logging.DEBUG)

def load_active_symbols() -> Set[str]:
    """Load active symbols from symbols_active.json"""
    try:
        active_file = Path("symbols_active.json")
        if not active_file.exists():
            logger.error("❌ symbols_active.json not found")
            return set()
        with open(active_file) as f:
            data = json.load(f)
            if isinstance(data, dict) and "symbols" in data:
                symbols = set(data["symbols"])
            elif isinstance(data, list):
                symbols = set(data)
            else:
                logger.error("❌ Invalid symbols_active.json format")
                return set()
        logger.warning(f"✅ Loaded {len(symbols)} active symbols")
        return symbols
    except Exception as e:
        logger.error(f"❌ Error loading active symbols: {e}", exc_info=True)
        return set()

async def save_active_positions(service: PositionService, account_key: str, active_symbols: Set[str]):
    """Save active positions to active_positions.json"""
    try:
        account_positions = service.positions_by_account.get(account_key, {})
        active_positions_dict = {}
        for pos_key, pos_obj in account_positions.items():
            if pos_obj and hasattr(pos_obj, 'symbol') and pos_obj.symbol in active_symbols:
                if hasattr(pos_obj, 'to_dict'):
                    active_positions_dict[pos_key] = pos_obj.to_dict()
        account_dir = Path(account_key)
        account_dir.mkdir(exist_ok=True)
        active_file = account_dir / "active_positions.json"
        temp_file = account_dir / "active_positions.json.tmp"
        with open(temp_file, 'w') as f:
            json.dump(active_positions_dict, f, indent=2)
        temp_file.replace(active_file)
        logger.warning(f"[ACTIVE][{account_key}] ✅ SAVED {len(active_positions_dict)} active positions")
        return set(active_positions_dict.keys())
    except Exception as e:
        logger.error(f"[ACTIVE][{account_key}] ❌ Save error: {e}", exc_info=True)
        return set()

async def fetch_positions_from_api(service: PositionService, account_key: str):
    """CRITICAL: Fetch positions from Binance API"""
    try:
        account = service.accounts.get(account_key)
        if not account:
            logger.error(f"[ACTIVE][{account_key}] ❌ Account not found")
            return set()
        if not getattr(account, "client", None):
            logger.warning(f"[ACTIVE][{account_key}] Client missing - creating...")
            try:
                client = await service._ensure_account_client(account_key)
                if not client:
                    logger.error(f"[ACTIVE][{account_key}] Failed to create client")
                    return set()
            except Exception as client_err:
                logger.error(f"[ACTIVE][{account_key}] Exception creating client: {client_err}", exc_info=True)
                return set()
        try:
            client_obj = getattr(account, "client", None)
            if not client_obj:
                logger.error(f"[ACTIVE][{account_key}] ❌ Client still missing")
                return set()
            logger.warning(f"[ACTIVE][{account_key}] 📡 Fetching positions from API...")
            async with service.maker_semaphore:
                positions_data = await asyncio.wait_for(asyncio.to_thread(client_obj.futures_position_information), timeout=30.0)
            logger.warning(f"[ACTIVE][{account_key}] 📡 API returned {len(positions_data) if isinstance(positions_data, list) else 0} positions")
            if positions_data is None or not isinstance(positions_data, list):
                logger.error(f"[ACTIVE][{account_key}] ❌ API returned invalid data")
                return set()
            logger.warning(f"[ACTIVE][{account_key}] 📡 Processing {len(positions_data)} positions...")
            updated_keys = await service.process_account_update(account_key, positions_data, single=False, skip_broadcast_save=True)
            logger.info(f"[FETCHFETCH]ez_pos_active[{account_key}] 🚨 Fetched {positions_data}")

            updated_count = len(updated_keys) if isinstance(updated_keys, set) else 0
            logger.warning(f"[ACTIVE][{account_key}] ✅ FETCHED and SYNCED {updated_count} positions")
            return updated_keys if isinstance(updated_keys, set) else set()
        except asyncio.TimeoutError:
            logger.error(f"[ACTIVE][{account_key}] ❌ API TIMEOUT after 30s")
            return set()
        except Exception as e:
            logger.error(f"[ACTIVE][{account_key}] ❌ API error: {e}", exc_info=True)
            return set()
    except Exception as e:
        logger.error(f"[ACTIVE][{account_key}] ❌ Fetch error: {e}", exc_info=True)
        return set()

async def sync_loop(service: PositionService, account_key: str, active_symbols: Set[str]):
    """Sync loop for one account - FETCHES positions from API and saves ACTIVE positions only"""
    logger.warning(f"[ACTIVE][{account_key}] 🚀 SYNC LOOP STARTED - FETCHING POSITIONS")
    while True:
        try:
            updated_keys = await fetch_positions_from_api(service, account_key)
            active_keys = await save_active_positions(service, account_key, active_symbols)
            if active_keys:
                try:
                    await service._broadcast_position_updates_to_redis(account_key, active_keys)
                    logger.warning(f"[ACTIVE][{account_key}] ✅ Redis updated: {len(active_keys)} positions")
                except Exception as e:
                    logger.error(f"[ACTIVE][{account_key}] ❌ Redis error: {e}")
            await asyncio.sleep(10.0)
        except Exception as e:
            logger.error(f"[ACTIVE][{account_key}] ❌ Loop error: {e}", exc_info=True)
            await asyncio.sleep(10.0)

async def main():
    """Main - sync active positions only"""
    logger.warning("[MAIN] 🚀 Starting ACTIVE positions sync")
    try:
        config = Config()
        accounts = await load_accounts_from_config(config, logger)
        if not accounts:
            logger.error("[MAIN] ❌ No accounts")
            return
        # CRITICAL: skip_file_load=True to prevent loading stale file data that overwrites fresh API/Redis data
        service = await bootstrap_position_service(logger=logger, accounts=accounts, enable_auto_fetch=False, skip_file_load=True)
        # CRITICAL: DO NOT call load_all() - it would overwrite fresh API data with stale file data!
        # Positions are already loaded by bootstrap_position_service if needed, or should come from API/Redis
        # await service.load_all(force=True, startup=True)  # DISABLED - would overwrite fresh data
        active_symbols = load_active_symbols()
        if not active_symbols:
            logger.error("[MAIN] ❌ No active symbols")
            return
        # CRITICAL: Start sync loops for ALL accounts - ang, inf, men, flz, fin
        all_account_keys = list(service.accounts.keys())
        logger.warning(f"[MAIN] 🚀 Starting sync loops for ALL {len(all_account_keys)} accounts: {all_account_keys}")
        if len(all_account_keys) != 5:
            logger.error(f"[MAIN] ❌❌❌ CRITICAL: Expected 5 accounts, got {len(all_account_keys)}: {all_account_keys}")
        tasks = []
        for account_key in all_account_keys:
            logger.warning(f"[MAIN] 🚀 Creating sync loop for {account_key}...")
            tasks.append(asyncio.create_task(sync_loop(service, account_key, active_symbols)))
            logger.warning(f"[MAIN] ✅ Sync loop created for {account_key}")
        if len(tasks) != len(all_account_keys):
            logger.error(f"[MAIN] ❌❌❌ CRITICAL: Created {len(tasks)} sync loops but have {len(all_account_keys)} accounts!")
        stop_event = asyncio.Event()
        def _handle_shutdown(*_):
            logger.warning("[MAIN] Shutdown signal received")
            stop_event.set()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _handle_shutdown)
            except NotImplementedError:
                signal.signal(sig, _handle_shutdown)
        logger.warning("[MAIN] ✅ ACTIVE positions sync running")
        await stop_event.wait()
    except Exception as e:
        logger.critical(f"[MAIN] ❌ Fatal error: {e}", exc_info=True)
    finally:
        logger.warning("[MAIN] Stopping...")

if __name__ == "__main__":
    asyncio.run(main())
