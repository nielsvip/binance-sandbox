#!/home/niels/.conda/envs/binance_env/bin/python3
"""
ez_positions_realtime.py - Real-time position updater
- Fetches positions from API every 4 seconds
- Listens to WebSocket for real-time updates
- Saves ALL positions to JSON files
"""
import asyncio
import logging
import signal
import sys
import time
import os
import fcntl
from pathlib import Path
from contextvars import ContextVar
from ez_positions_service import bootstrap_position_service, WebSocketManager
from utils import load_environment_from_gpg, orjson_default
from ez_positions import atomic_save_positions
load_environment_from_gpg(None)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("ez_positions_realtime")
import os
from pathlib import Path
logs_dir = Path.home() / "logs"
logs_dir.mkdir(parents=True, exist_ok=True)
from logging.handlers import RotatingFileHandler
# Will set account-specific log file in main() after account_key is known
_initial_file_handler = RotatingFileHandler(str(logs_dir / "ez_positions_realtime.log"), maxBytes=1*1024*1024, backupCount=3, encoding='utf-8', mode='a')
_initial_file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s")
_initial_file_handler.setFormatter(file_formatter)
logger.addHandler(_initial_file_handler)
logger.setLevel(logging.DEBUG)

class RealtimePositionUpdater:
    def __init__(self, position_service, account_key: str):
        self.position_service = position_service
        self.account_key = account_key
        self.running = True
        self.has_fetched_once = False
        self._last_file_refresh = time.time()
        
    async def fetch_loop(self) -> None:
        """CRITICAL: Fetch positions from API every 6 seconds and save ALL positions"""
        logger.warning(f"[FETCH_LOOP][{self.account_key}] 🚀 Starting fetch loop")
        while self.running:
            try:
                account = self.position_service.accounts.get(self.account_key)
                if not account:
                    logger.error(f"[FETCH_LOOP][{self.account_key}] ❌ Account not found")
                    await asyncio.sleep(4.0)
                    continue
                if not getattr(account, "client", None):
                    logger.warning(f"[FETCH_LOOP][{self.account_key}] Client missing - creating...")
                    try:
                        client = await self.position_service._ensure_account_client(self.account_key)
                        if not client:
                            logger.error(f"[FETCH_LOOP][{self.account_key}] Failed to create client")
                            await asyncio.sleep(4.0)
                            continue
                    except Exception as client_err:
                        logger.error(f"[FETCH_LOOP][{self.account_key}] Exception creating client: {client_err}", exc_info=True)
                        await asyncio.sleep(4.0)
                        continue
                try:
                    client_obj = getattr(account, "client", None)
                    if not client_obj:
                        logger.error(f"[FETCH_LOOP][{self.account_key}] ❌ Client still missing")
                        await asyncio.sleep(4.0)
                        continue
                    logger.warning(f"[FETCH_LOOP][{self.account_key}] 📡 Fetching positions from API...")
                    async with self.position_service.maker_semaphore:
                        positions_data = await asyncio.wait_for(
                            asyncio.to_thread(client_obj.futures_position_information), timeout=30.0    )
                    logger.warning(f"[FETCH_LOOP][{self.account_key}] 📡 API returned {len(positions_data) if isinstance(positions_data, list) else 0} positions")
                    if isinstance(positions_data, list):
                        logger.warning(f"[FETCH_LOOP][{self.account_key}] 📡 Processing {len(positions_data)} positions...")
                        # CRASH-ON-HANG: process_account_update is the CORE — handle_ functions feed
                        # the entire system. If it stalls > 60s the worker is dead in all but name;
                        # exit so the watchdog respawns within 5s instead of trading on stale data.
                        try:
                            updated_keys = await asyncio.wait_for(self.position_service.process_account_update(self.account_key, positions_data, single=False, skip_broadcast_save=False), timeout=60.0)
                        except asyncio.TimeoutError:
                            logger.critical(f"[FETCH_LOOP][{self.account_key}] 🚨🚨🚨 process_account_update HUNG > 60s — CRASHING realtime worker so watchdog respawns.")
                            try: sys.stdout.flush(); sys.stderr.flush()
                            except Exception: pass
                            os._exit(42)
                        except Exception as _pau_err:
                            logger.critical(f"[FETCH_LOOP][{self.account_key}] 🚨🚨🚨 process_account_update RAISED — CRASHING realtime worker so watchdog respawns. Error: {_pau_err}", exc_info=True)
                            try: sys.stdout.flush(); sys.stderr.flush()
                            except Exception: pass
                            os._exit(43)
                        logger.info(f"[FETCHFETCH]ez_pos_realtime {self.account_key}[ 🚨 Fetched {positions_data}")
                        updated_count = len(updated_keys) if isinstance(updated_keys, set) else 0
                        logger.warning(f"[FETCH_LOOP][{self.account_key}] ✅ FETCHED and SYNCED {updated_count} positions")
                        # CRITICAL: Always save after fetch (atomic_save_positions has debouncing built-in)
                        account_positions = self.position_service.positions_by_account.get(self.account_key, {})
                        if account_positions:
                            try:
                                await atomic_save_positions(self.position_service, self.account_key)
                                logger.warning(f"[FETCH_LOOP][{self.account_key}] ✅ SAVED {len(account_positions)} positions")
                            except Exception as save_err:
                                logger.error(f"[FETCH_LOOP][{self.account_key}] ❌ Save failed: {save_err}", exc_info=True)
                        else:
                            logger.error(f"[FETCH_LOOP][{self.account_key}] ❌❌❌ CRITICAL: No positions in memory after fetch!")
                        self.has_fetched_once = True
                    else:
                        logger.error(f"[FETCH_LOOP][{self.account_key}] ❌ Invalid positions data: {type(positions_data)}")
                except asyncio.TimeoutError:
                    logger.error(f"[FETCH_LOOP][{self.account_key}] ❌ API TIMEOUT after 30s")
                except Exception as e:
                    logger.error(f"[FETCH_LOOP][{self.account_key}] ❌ Fetch error: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"[FETCH_LOOP][{self.account_key}] ❌ Loop error: {e}", exc_info=True)
            await asyncio.sleep(6.0)

async def main():
    if len(sys.argv) < 2:
        logger.critical("Usage: ez_positions_realtime.py <account_key>")
        logger.critical(f"[REALTIME] ❌❌❌ CRITICAL: Missing account_key argument! sys.argv={sys.argv}")
        sys.exit(1)
    account_key = sys.argv[1]
    if account_key == "tra":
        logger.critical(f"[REALTIME][{account_key}] ⏭️ Skipping 'tra' account - handled by tradier_positions.py")
        sys.exit(0)
    pid_file = Path.home() / "logs" / f"ez_positions_realtime_{account_key}.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_fd = None
    try:
        pid_fd = os.open(str(pid_file), os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
        try:
            fcntl.flock(pid_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.critical(f"[REALTIME][{account_key}] ❌ Another instance is already running (PID file locked)")
            if pid_fd: os.close(pid_fd)
            sys.exit(1)
        os.write(pid_fd, str(os.getpid()).encode())
        os.fsync(pid_fd)
    except Exception as pid_err:
        logger.warning(f"[REALTIME][{account_key}] ⚠️ Could not create PID file: {pid_err}")
        if pid_fd: os.close(pid_fd)
        pid_fd = None
    account_file_handler = None
    try:
        account_log_file = str(logs_dir / f"ez_positions_realtime_{account_key}.log")
        existing_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler) and h.baseFilename == account_log_file]
        if not existing_handlers:
            account_file_handler = RotatingFileHandler(account_log_file, maxBytes=1*1024*1024, backupCount=3, encoding='utf-8', mode='a')
            account_file_handler.setLevel(logging.DEBUG)
            account_file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
            logger.addHandler(account_file_handler)
        logger.critical(f"[REALTIME] 🚨🚨🚨 Starting for: {account_key} (sys.argv={sys.argv})")
    except Exception as log_err:
        logger.warning(f"[REALTIME][{account_key}] ⚠️ Log setup error: {log_err}")
    
    try:
        logger.critical(f"[REALTIME][{account_key}] 🚨🚨🚨 Calling bootstrap_position_service (single account)...")
        from ez_positions_service import load_accounts_from_config
        from config import Config as _Cfg
        _cfg = _Cfg()
        _all_accounts = await load_accounts_from_config(_cfg, logger)
        _single_accounts = {account_key: _all_accounts[account_key]} if account_key in _all_accounts else {}
        if not _single_accounts:
            logger.critical(f"[REALTIME][{account_key}] Account not found in config — exiting")
            return
        service = await bootstrap_position_service(enable_auto_fetch=False, accounts=_single_accounts, start_maintenance=False)
        logger.critical(f"[REALTIME][{account_key}] 🚨🚨🚨 bootstrap_position_service returned: service={service}, accounts={list(service.accounts.keys()) if service else 'None'}")
        if not service or account_key not in service.accounts:
            logger.critical(f"Account {account_key} not found")
            return
        
        # CRITICAL: DO NOT load_all() here - bootstrap_position_service already loaded positions ONCE
        # Reloading would overwrite fresh data with old file data!
        total_loaded = sum(len(acc_pos) for acc_pos in service.positions_by_account.values())
        if total_loaded == 0:
            logger.warning(f"[REALTIME][{account_key}] No positions in memory - loading ONCE...")
            try:
                # CRITICAL: DO NOT load_all() - positions already loaded above, reloading would overwrite fresh data
                total_loaded = sum(len(acc_pos) for acc_pos in service.positions_by_account.values())
                logger.warning(f"[REALTIME][{account_key}] ✅ Loaded {total_loaded} positions ONCE")
            except Exception as load_err:
                logger.error(f"[REALTIME][{account_key}] ❌ Load failed: {load_err}", exc_info=True)
        else:
            logger.warning(f"[REALTIME][{account_key}] ✅ Positions already in memory ({total_loaded}) - NOT reloading")
        
        # Initialize attributes
        if not hasattr(service, 'reenter_level'): service.reenter_level = {}
        if not hasattr(service, 'augmentation_cooldown_map'): service.augmentation_cooldown_map = {}
        if not hasattr(service, 'reduction_cooldown_map'): service.reduction_cooldown_map = {}
        if not hasattr(service, 'orders_in_limbo'): service.orders_in_limbo = {}
        
        # CRITICAL: Positions already loaded above - DO NOT reload (would overwrite fresh data with old files)
        account_positions = service.positions_by_account.get(account_key, {})
        logger.warning(f"[REALTIME][{account_key}] Using {len(account_positions)} positions already in memory (NOT reloading)")
        
        updater = RealtimePositionUpdater(service, account_key)
        
        # Setup WebSocket
        account = service.accounts.get(account_key)
        api_key = getattr(account, 'api_key', None) or getattr(account, 'api_key_plain', None)
        api_secret = getattr(account, 'api_secret', None) or getattr(account, 'api_secret_plain', None)
        
        if not api_key or not api_secret:
            logger.critical(f"Missing API credentials for {account_key}")
            return
        
        ws_manager = WebSocketManager(
            account_keys=[account_key],
            api_key=api_key,
            api_secret=api_secret,
            config=service.config,
            symbols=None,
            service=service  )
        
        # Wrap WebSocket handler to properly process updates via handle_account_update
        original_handle = ws_manager.handle_account_update
        
        async def handle_with_save(data: dict, account_key_param: str):
            # CRITICAL: Only process/save for THIS script's account - ignore updates for other accounts
            if account_key_param != account_key:
                logger.debug(f"[WS][{account_key}] ⏸️ Ignoring WebSocket update for {account_key_param} (this script handles {account_key})")
                return
            # CRITICAL: Use handle_account_update to properly process WebSocket updates
            # This ensures all position logic (augmentations, reductions, etc.) is applied correctly
            logger.warning(f"[WS][{account_key}] 🔄 Processing WebSocket update via handle_account_update...")
            # CRASH-ON-HANG: same chain as _service. If it stalls > 60s we are blind to position
            # changes — exit so the watchdog respawns within 5s instead of trading on stale data.
            try:
                await asyncio.wait_for(original_handle(data, account_key_param), timeout=60.0)
                logger.warning(f"[WS][{account_key}] ✅ handle_account_update completed")
            except asyncio.TimeoutError:
                logger.critical(f"[WS][{account_key}] 🚨🚨🚨 handle_account_update HUNG > 60s — CRASHING realtime worker so watchdog respawns.")
                try: sys.stdout.flush(); sys.stderr.flush()
                except Exception: pass
                os._exit(44)
            except Exception as handle_err:
                logger.critical(f"[WS][{account_key}] 🚨🚨🚨 handle_account_update RAISED — CRASHING realtime worker. Error: {handle_err}", exc_info=True)
                try: sys.stdout.flush(); sys.stderr.flush()
                except Exception: pass
                os._exit(45)
            
            try:
                account_positions = service.positions_by_account.get(account_key, {})
                if len(account_positions) == 0:
                    logger.warning(f"[WS][{account_key}] ⚠️ No positions after WebSocket update!")
                    return
                
                # Extract updated keys from WebSocket data for Redis broadcast
                try:
                    from utils import safe_fetch_float, orjson_default
                    updated_keys = set()
                    if isinstance(data, dict) and "a" in data:
                        positions_list = data.get("a", {}).get("P", [])
                        for pos_data in positions_list:
                            symbol = pos_data.get("s") or pos_data.get("symbol", "")
                            raw_amt = safe_fetch_float(pos_data.get("pa") or pos_data.get("positionAmt"), 0.0)
                            side = (pos_data.get("ps") or pos_data.get("positionSide") or ("LONG" if raw_amt >= 0 else "SHORT")).upper()
                            if symbol:
                                pos_key = f"{account_key}:{symbol}_{side}"
                                updated_keys.add(pos_key)
                    
                    if updated_keys:
                        await service._broadcast_position_updates_to_redis(account_key, updated_keys)
                        logger.warning(f"[WS][{account_key}] ✅ Broadcasted {len(updated_keys)} positions to Redis")
                except Exception as broadcast_err:
                    logger.error(f"[WS][{account_key}] ❌ Broadcast failed: {broadcast_err}", exc_info=True)
                
                # CRITICAL: Save ALL positions from memory after WebSocket update
                # handle_account_update already updated memory, now save to files
                try:
                    await atomic_save_positions(service, account_key, force=True)
                    logger.warning(f"[WS][{account_key}] ✅✅✅ Saved all positions from memory after WebSocket update")
                except Exception as save_err:
                    logger.error(f"[WS][{account_key}] ❌ Failed to save positions: {save_err}", exc_info=True)
            except Exception as e:
                logger.error(f"[WS][{account_key}] ❌ Error processing WebSocket update: {e}", exc_info=True)
        
        ws_manager.handle_account_update = handle_with_save
        running = True
        tasks = []
        cleanup_done = False
        
        _force_exit_requested = False
        def signal_handler(sig, frame):
            nonlocal running, cleanup_done, _force_exit_requested
            if _force_exit_requested: os._exit(1)
            _force_exit_requested = True
            if cleanup_done: os._exit(0)
            cleanup_done = True
            logger.warning(f"[REALTIME][{account_key}] 🛑 Received signal {sig}, shutting down...")
            running = False
            updater.running = False
            if hasattr(ws_manager, '_running'):
                ws_manager._running = False
            for task in tasks:
                if not task.done():
                    task.cancel()
            if pid_fd:
                try:
                    fcntl.flock(pid_fd, fcntl.LOCK_UN)
                    os.close(pid_fd)
                    if pid_file.exists():
                        pid_file.unlink()
                except Exception: pass
            import threading
            def force_exit():
                time.sleep(1.5)
                logger.warning(f"[REALTIME][{account_key}] 🛑 Force exiting...")
                os._exit(0)
            threading.Thread(target=force_exit, daemon=True).start()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        ws_task = asyncio.create_task(ws_manager.start(account_key))
        tasks.append(ws_task)
        fetch_task = asyncio.create_task(updater.fetch_loop())
        tasks.append(fetch_task)
        
        try:
            await fetch_task
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            cleanup_done = True
            updater.running = False
            if hasattr(ws_manager, '_running'):
                ws_manager._running = False
            if hasattr(ws_manager, 'stop'):
                try:
                    await asyncio.wait_for(ws_manager.stop(), timeout=3.0)
                except Exception:
                    pass
            for task in tasks:
                if not task.done():
                    task.cancel()
            for task in tasks:
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except Exception:
                    pass
            if account_file_handler:
                logger.removeHandler(account_file_handler)
                account_file_handler.close()
            if pid_fd:
                try:
                    fcntl.flock(pid_fd, fcntl.LOCK_UN)
                    os.close(pid_fd)
                    if pid_file.exists():
                        pid_file.unlink()
                except Exception: pass
            logger.warning(f"[REALTIME][{account_key}] Stopped")
            
    except Exception as e:
        logger.critical(f"[REALTIME] Fatal error: {e}", exc_info=True)
    finally:
        if 'account_file_handler' in locals() and account_file_handler:
            logger.removeHandler(account_file_handler)
            account_file_handler.close()
        if 'pid_fd' in locals() and pid_fd:
            try:
                fcntl.flock(pid_fd, fcntl.LOCK_UN)
                os.close(pid_fd)
                if 'pid_file' in locals() and pid_file.exists():
                    pid_file.unlink()
            except Exception: pass

if __name__ == "__main__":
    asyncio.run(main())
