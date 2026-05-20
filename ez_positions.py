import asyncio
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional, Set

import aiofiles

from config import Config
from ez_positions_service import (Position, PositionService, WebSocketManager, bootstrap_position_service, ii, load_accounts_from_config, minutes_since)
from utils import (REDIS_CHANNELS, get_simple_redis_manager, is_sandbox_account, load_environment_from_gpg, orjson_default, parse_position_key, safe_fetch_float)

try:
    from typing import Any, Dict, List, Optional, Union

    import orjson
    def json_dumps(obj: Any, **kwargs) -> bytes:
        option = orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_INDENT_2  # pylint: disable=no-member
        return orjson.dumps(obj, option=option)  # pylint: disable=no-member
    def safe_json_loads(s: Union[bytes, bytearray, memoryview, str], **kwargs) -> Any:
        if isinstance(s, str): s = s.encode('utf-8')
        return orjson.loads(s)  # pylint: disable=no-member
    JSONDecodeError = orjson.JSONDecodeError  # pylint: disable=no-member
except ImportError:
    import json
    from typing import Any, Dict, List, Optional, Union
    def json_dumps(obj: Any, **kwargs) -> str:
        if 'indent' not in kwargs: kwargs['indent'] = 2
        return json.dumps(obj, **kwargs)
    def safe_json_loads(s: Union[bytes, bytearray, memoryview, str], **kwargs) -> Any:
        if isinstance(s, (bytes, bytearray, memoryview)): s = s.decode('utf-8')
        return json.loads(s, **kwargs)
    JSONDecodeError = json.JSONDecodeError
load_environment_from_gpg(None)

# 1. Define the Short Date Format (No Year)
DATE_FORMAT = '%m-%d %H:%M:%S'
LOG_FORMAT = '[%(asctime)s] %(levelname)s %(message)s'

# 2. Setup the logger
logger = logging.getLogger("ez_positions")
logger.setLevel(logging.DEBUG)
logger.propagate = False # Prevent logs from leaking to the root logger

# 3. CRITICAL: Clear existing handlers to prevent rotation locks/duplicates
if logger.hasHandlers():
    for handler in list(logger.handlers):
        try:
            handler.close()
        except Exception:
            pass
        logger.removeHandler(handler)

_logs_dir = Path.home() / "logs"
_logs_dir.mkdir(parents=True, exist_ok=True)
log_path = str(_logs_dir / "ez_positions.log")
file_handler = RotatingFileHandler(log_path, maxBytes=100 * 1024 * 1024, backupCount=5, encoding="utf-8", mode="a")
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(file_formatter)
logger.addHandler(console_handler)
_save_locks: Dict[str, asyncio.Lock] = {}
_last_save_time: Dict[str, float] = {}
_save_debounce_seconds = 2.0
_last_backup_time = {}

def _atomic_save_blocking(account_key: str, all_positions_by_side: Dict[str, Dict[str, Any]], file_paths_by_side: Dict[str, Path], do_backup_by_side: Dict[str, bool]) -> Dict[str, str]:
    # WHY: keeps fsync + shutil.copy2 + temp-file write off the asyncio event loop. Pre-2026-05-20 this block ran inline and stalled process_account_update past the 120s wait_for tripwire (3 pau_stall events for flz/men/inf in 30 min on 2026-05-20).
    import os as _os, shutil as _shutil, time as _t
    from datetime import datetime as _dt, timezone as _tz
    results: Dict[str, str] = {}
    for side, positions_dict in all_positions_by_side.items():
        if not positions_dict:
            results[side] = "EMPTY"
            continue
        main_file = file_paths_by_side[side]
        main_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = main_file.parent / f".{main_file.name}.tmp.{int(_t.time() * 1000000)}"
        try:
            json_bytes = json_dumps(positions_dict)
            if isinstance(json_bytes, str):
                json_bytes = json_bytes.encode('utf-8')
            with open(temp_file, "wb") as f:
                f.write(json_bytes)
                f.flush()
                _os.fsync(f.fileno())
            if not temp_file.exists() or temp_file.stat().st_size == 0:
                raise IOError(f"Temp file not created or empty: {temp_file}")
            if do_backup_by_side.get(side) and main_file.exists():
                backup_dir = main_file.parent / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                timestamp = _dt.now(_tz.utc).strftime("%Y%m%d_%H%M%S")
                backup_file = backup_dir / f"{main_file.stem}_backup_{timestamp}.json"
                _shutil.copy2(main_file, backup_file)
            _shutil.move(str(temp_file), str(main_file))
            results[side] = "OK" if main_file.exists() else "MISSING_AFTER_MOVE"
        except Exception as e:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass
            results[side] = f"FAILED:{e}"
    return results
# async def atomic_save_positions(position_service: PositionService, account_key: str, force=None) -> None:
#     if account_key not in _save_locks:_save_locks[account_key] = asyncio.Lock()
#     lock = _save_locks[account_key]
#     # Debounce: skip if saved recently
#     now = time.time()
#     last_save = _last_save_time.get(account_key, 0.0)
#     if (now - last_save) < _save_debounce_seconds:
#         logger.debug(f"[atomic_save][{account_key}] ⏱️ Debounced - last save {now-last_save:.1f}s ago")
#         return
#     # async with lock:
#     now = time.time()
#     last_save = _last_save_time.get(account_key, 0.0)
#     if (now - _last_save_time.get(account_key, 0.0)) < _save_debounce_seconds: return
#     try:
#         account_positions = position_service.positions_by_account.get( account_key, {}   )
#         if not account_positions:
#             logger.error(f"[atomic_save][{account_key}] ❌❌❌ CRITICAL: No positions in memory - CANNOT SAVE!")
#             return
#         from datetime import datetime, timezone
#         now_utc = datetime.now(timezone.utc)
#         newest_position_time = None
#         for pos_key, pos_obj in account_positions.items():
#             if 'FLMUSDT' in pos_key: account_positions.pop(pos_key,None)
#             if pos_obj and hasattr(pos_obj, "last_updated"):
#                 try:
#                     pos_time = pos_obj.last_updated
#                     if isinstance(pos_time, str):
#                         pos_time = datetime.fromisoformat( pos_time.replace("Z", "+00:00") )
#                     if pos_time.tzinfo is None:
#                         pos_time = pos_time.replace(tzinfo=timezone.utc)
#                     if ( newest_position_time is None or pos_time > newest_position_time ):
#                         newest_position_time = pos_time
#                 except:
#                     pass
#         fresh_positions = (  account_positions    )
#         logger.info(  f"[atomic_save][{account_key}] 💾 Saving {len(fresh_positions)} positions to files"  )
#         for side in ["LONG", "SHORT"]:
#             main_file = position_service.get_position_file(account_key, side)
#             if main_file.exists():
#                 file_mtime = datetime.fromtimestamp( main_file.stat().st_mtime, tz=timezone.utc  )
#                 file_age = (now_utc - file_mtime).total_seconds()
#                 if file_age < 1.0:
#                     logger.warning( f"[atomic_save][{account_key}:{side}] ⚠️ File was just updated {file_age:.2f}s ago - waiting 0.5s to prevent race condition"  )
#                     await asyncio.sleep(0.5)
#                     if main_file.exists():
#                         file_mtime = datetime.fromtimestamp( main_file.stat().st_mtime, tz=timezone.utc   )
#                         file_age = ( datetime.now(timezone.utc) - file_mtime ).total_seconds()
#                         if file_age < 1.0:
#                             logger.warning(   f"[atomic_save][{account_key}:{side}] ⚠️ File still being written ({file_age:.2f}s) - skipping this save" )
#                             continue
#         # Group by side (use fresh_positions, not account_positions)
#         all_positions_by_side: Dict[str, Dict[str, Any]] = {"LONG": {}, "SHORT": {}}
#         for pos_key, pos_obj in fresh_positions.items():
#             if not pos_obj or not hasattr(pos_obj, "to_dict"):continue
#             side = getattr(pos_obj, "position_side", None)
#             if side not in ["LONG", "SHORT"]:  continue
#             try:
#                 pos_dict = pos_obj.to_dict()
#                 if isinstance(pos_dict, dict):
#                     all_positions_by_side[side][pos_key] = pos_dict
#             except Exception as e:
#                 logger.error(f"[atomic_save][{account_key}] to_dict failed for {pos_key}: {e}")
#                 continue
#         # CRITICAL: Save each side - ALWAYS save fresh data from memory (source of truth)
#         for side, positions_dict in all_positions_by_side.items():
#             if not positions_dict:
#                 logger.warning(f"[atomic_save][{account_key}:{side}] ⚠️ No positions to save for {side} side")
#                 continue
#             main_file = position_service.get_position_file(account_key, side)
#             main_file.parent.mkdir(parents=True, exist_ok=True)
#             sorted_positions = position_service._sort_positions_dict(positions_dict)
#             temp_file = main_file.parent / f".{main_file.name}.tmp.{int(time.time() * 1000000)}"
#             try:
#                 json_bytes = json_dumps(sorted_positions)
#                 if isinstance(json_bytes, str): json_bytes = json_bytes.encode('utf-8')
#                 logger.debug(f"[atomic_save][{account_key}:{side}] 💾 Writing {len(sorted_positions)} positions to temp file...")
#                 with open(temp_file, "wb") as f:
#                     f.write(json_bytes)
#                     f.flush()
#                     os.fsync(f.fileno())
#                 if not temp_file.exists() or temp_file.stat().st_size == 0:
#                     raise IOError(f"Temp file not created or empty: {temp_file}")
#                 if main_file.exists():
#                     backup_dir = main_file.parent / "backups"
#                     backup_dir.mkdir(parents=True, exist_ok=True)
#                     timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
#                     backup_file = backup_dir / f"{main_file.stem}_backup_{timestamp}.json"
#                     shutil.copy2(main_file, backup_file)
#                 shutil.move(str(temp_file), str(main_file))
#                 if main_file.exists():
#                     logger.info(f"[atomic_save][{account_key}:{side}] ✅ SAVED {len(sorted_positions)} positions to {main_file.name}")
#                 else:
#                     logger.error(f"[atomic_save][{account_key}:{side}] ❌ File missing after move!")
#             except Exception as e:
#                 logger.error(f"[atomic_save][{account_key}:{side}] ❌ Write failed: {e}", exc_info=True)
#                 if temp_file.exists():
#                     try: temp_file.unlink()
#                     except: pass
#                 raise
#         logger.info(f"[atomic_save][{account_key}] ✅ COMPLETED - All positions saved")
#         _last_save_time[account_key] = time.time()
#     except Exception as e:
#         logger.error(f"[atomic_save][{account_key}] ❌ FAILED: {e}", exc_info=True)
#         raise

async def atomic_save_positions(position_service: PositionService, account_key: str, force=None) -> None:
    if account_key not in _save_locks:_save_locks[account_key] = asyncio.Lock()
    lock = _save_locks[account_key]
    # Debounce: skip if saved recently
    now = time.time()
    last_save = _last_save_time.get(account_key, 0.0)
    if (now - last_save) < _save_debounce_seconds:
        logger.debug(f"[atomic_save][{account_key}] ⏱️ Debounced - last save {now-last_save:.1f}s ago")
        return
    async with lock:
        now = time.time()
        last_save = _last_save_time.get(account_key, 0.0)
        if not force and (now - last_save) < _save_debounce_seconds:
            logger.debug(f"[atomic_save][{account_key}] ⏱️ Debounced - last save {now-last_save:.1f}s ago")
            return
        try:
            account_positions = position_service.positions_by_account.get( account_key, {}   )
            if not account_positions:
                logger.error(f"[atomic_save][{account_key}] ❌❌❌ CRITICAL: No positions in memory - CANNOT SAVE!")
                return
            from datetime import datetime, timezone
            now_utc = datetime.now(timezone.utc)
            newest_position_time = None
            for pos_key, pos_obj in account_positions.items():
                if pos_obj and hasattr(pos_obj, "last_updated"):
                    try:
                        pos_time = pos_obj.last_updated
                        if isinstance(pos_time, str):
                            pos_time = datetime.fromisoformat( pos_time.replace("Z", "+00:00") )
                        if pos_time.tzinfo is None:
                            pos_time = pos_time.replace(tzinfo=timezone.utc)
                        if ( newest_position_time is None or pos_time > newest_position_time ):
                            newest_position_time = pos_time
                    except Exception:
                        pass
            fresh_positions = (  account_positions    )
            logger.info(  f"[atomic_save][{account_key}] 💾 Saving {len(fresh_positions)} positions to files")
            for side in ["LONG", "SHORT"]:
                main_file = position_service.get_position_file(account_key, side)
                if main_file.exists() and not force: # Add 'and not force'
                    file_mtime = datetime.fromtimestamp(main_file.stat().st_mtime, tz=timezone.utc)
                    file_age = (now_utc - file_mtime).total_seconds()
                    if file_age < 1.0:
                        logger.warning(f"File recently updated, skipping debounce save")
                        continue

            # for side in ["LONG", "SHORT"]:
            #     main_file = position_service.get_position_file(account_key, side)
            #     if main_file.exists():
            #         file_mtime = datetime.fromtimestamp( main_file.stat().st_mtime, tz=timezone.utc  )
            #         file_age = (now_utc - file_mtime).total_seconds()
            #         if file_age < 1.0:
            #             logger.warning( f"[atomic_save][{account_key}:{side}] ⚠️ File was just updated {file_age:.2f}s ago - waiting 0.5s to prevent race condition"  )
            #             await asyncio.sleep(0.5)
            #             if main_file.exists():
            #                 file_mtime = datetime.fromtimestamp( main_file.stat().st_mtime, tz=timezone.utc   )
            #                 file_age = ( datetime.now(timezone.utc) - file_mtime ).total_seconds()
            #                 if file_age < 1.0:
            #                     logger.warning(   f"[atomic_save][{account_key}:{side}] ⚠️ File still being written ({file_age:.2f}s) - skipping this save" )
            #                     continue
            # Group by side (use fresh_positions, not account_positions)
            all_positions_by_side: Dict[str, Dict[str, Any]] = {"LONG": {}, "SHORT": {}}
            for pos_key, pos_obj in fresh_positions.items():
                if not pos_obj or not hasattr(pos_obj, "to_dict"):continue
                side = getattr(pos_obj, "position_side", None)
                if side not in ["LONG", "SHORT"]:  continue
                try:
                    pos_dict = pos_obj.to_dict()
                    if isinstance(pos_dict, dict):
                        amt = abs(float(pos_dict.get('positionAmt', 0)))
                        ep = float(pos_dict.get('entry_price', 0))
                        oa = pos_dict.get('opened_at')
                        mg = float(pos_dict.get('max_gain', 0))
                        # Guard: if entry_price zeroed but other fields exist, restore from disk
                        if ep == 0 and (oa is not None or mg > 0):
                            try:
                                disk_file = position_service.get_position_file(account_key, side)
                                if disk_file.exists():
                                    with open(disk_file) as _df2:
                                        _disk2 = json.load(_df2)
                                    _dpos = _disk2.get(pos_key, {})
                                    _dep = float(_dpos.get('entry_price', 0))
                                    if _dep > 0:
                                        pos_dict['entry_price'] = _dep
                                        logger.critical(f"[atomic_save][{account_key}] 🛡️ ENTRY_PRICE_GUARD: {pos_key} entry_price was 0 in memory, restored {_dep} from disk")
                            except Exception: pass
                        if amt == 0 and ep == 0 and oa is None and mg == 0:
                            existing_on_disk = None
                            try:
                                disk_file = position_service.get_position_file(account_key, side)
                                if disk_file.exists():
                                    with open(disk_file) as _df:
                                        disk_data = json.load(_df)
                                    existing_on_disk = disk_data.get(pos_key, {})
                            except Exception: pass
                            if existing_on_disk and isinstance(existing_on_disk, dict):
                                disk_amt = abs(float(existing_on_disk.get('positionAmt', 0)))
                                disk_ep = float(existing_on_disk.get('entry_price', 0))
                                disk_oa = existing_on_disk.get('opened_at')
                                if disk_amt > 0 or disk_ep > 0 or disk_oa:
                                    import traceback
                                    stack = "".join(traceback.format_stack()[-8:])
                                    logger.critical(f"[SAVE_WATCHDOG] {pos_key}: ABOUT TO OVERWRITE REAL DATA WITH ZEROS! disk_amt={disk_amt} disk_ep={disk_ep} disk_oa={disk_oa} | mem_amt={amt} mem_ep={ep} mem_oa={oa} | BLOCKING SAVE. Stack:\n{stack}")
                                    pos_dict = existing_on_disk
                        all_positions_by_side[side][pos_key] = pos_dict
                except Exception as e:
                    logger.error(f"[atomic_save][{account_key}] to_dict failed for {pos_key}: {e}")
                    continue
            # CRITICAL: Save each side - ALWAYS save fresh data from memory (source of truth)
            # 2026-05-20: prep paths + backup decisions on event loop; do the heavy file work in a worker thread so fsync/shutil.copy2/move/JSON write don't stall asyncio. Pre-fix this block ran inline and tripped the 120s PAU watchdog.
            sorted_by_side: Dict[str, Dict[str, Any]] = {}
            file_paths_by_side: Dict[str, Path] = {}
            do_backup_by_side: Dict[str, bool] = {}
            backup_dirs_by_side: Dict[str, Path] = {}
            now_ts = time.time()
            for side, positions_dict in all_positions_by_side.items():
                if not positions_dict:
                    logger.warning(f"[atomic_save][{account_key}:{side}] ⚠️ No positions to save for {side} side")
                    continue
                main_file = position_service.get_position_file(account_key, side)
                sorted_by_side[side] = position_service._sort_positions_dict(positions_dict)
                file_paths_by_side[side] = main_file
                bk_key = f"{account_key}_{side}"
                do_backup = (now_ts - _last_backup_time.get(bk_key, 0)) > 300
                do_backup_by_side[side] = do_backup
                if do_backup:
                    backup_dirs_by_side[side] = main_file.parent / "backups"
                    _last_backup_time[bk_key] = now_ts
            if sorted_by_side:
                results = await asyncio.to_thread(_atomic_save_blocking, account_key, sorted_by_side, file_paths_by_side, do_backup_by_side)
                for side, status in results.items():
                    if status == "OK":
                        logger.info(f"[atomic_save][{account_key}:{side}] ✅ SAVED {len(sorted_by_side.get(side, {}))} positions to {file_paths_by_side[side].name}")
                    elif status == "EMPTY":
                        continue
                    elif status == "MISSING_AFTER_MOVE":
                        logger.error(f"[atomic_save][{account_key}:{side}] ❌ File missing after move!")
                    else:
                        logger.error(f"[atomic_save][{account_key}:{side}] ❌ {status}")
                for side, backup_dir in backup_dirs_by_side.items():
                    main_file = file_paths_by_side[side]
                    asyncio.create_task(position_service.prune_old_backups(str(backup_dir), main_file.stem))
            logger.info(f"[atomic_save][{account_key}] ✅ COMPLETED - All positions saved")
            _last_save_time[account_key] = time.time()
        except Exception as e:
            logger.error(f"[atomic_save][{account_key}] ❌ FAILED: {e}", exc_info=True)
            raise

class PositionsFetcher:
    """Fetches positions from Binance API and WebSocket, updates position service - INTEGRATED INTO ez_positions.py"""
    def __init__(self, position_service: PositionService):
        self.position_service = position_service
        self._running = True
        self.ws_managers: Dict[str, WebSocketManager] = {}
        self._positions_refreshing: Dict[str, bool] = {}
        self._last_positions_fetch: Dict[str, float] = {}
        self._last_full_broadcast: Dict[str, float] = {}
        self._full_broadcast_interval = 60.0
    async def start(self):
        """CRITICAL ORDER: 1. Load positions ONCE, 2. Fetch, 3. Save, 4. Start loops"""
        logger.info(f"[PositionsFetcher.start] Starting for {len(self.position_service.accounts)} accounts")
        max_wait, waited = 30.0, 0.0
        while waited < max_wait:
            total_loaded = sum(len(acc_pos) for acc_pos in self.position_service.positions_by_account.values())
            if total_loaded > 0: break
            await asyncio.sleep(0.5); waited += 0.5
        total_loaded = sum(len(acc_pos) for acc_pos in self.position_service.positions_by_account.values())
        if total_loaded == 0:
            logger.critical(f"[PositionsFetcher.start] ❌❌❌ CRITICAL: No positions in memory after {waited}s wait! Cannot proceed!")
            return
        logger.info(f"[PositionsFetcher.start] ✅ {total_loaded} positions in memory")
        if not self.position_service._loading_complete_event.is_set():
            self.position_service._loading_complete_event.set()
        all_account_keys = list(self.position_service.accounts.keys())
        logger.warning(f"[PositionsFetcher.start] STEP 2: Starting initial fetch for ALL {len(all_account_keys)} accounts: {all_account_keys}")
        async def initial_fetch_background():
            try:
                initial_fetch_tasks = []
                for account_key in all_account_keys:
                    self._last_positions_fetch[account_key] = 0.0
                    initial_fetch_tasks.append(self._fetch_positions_via_api(account_key))
                if initial_fetch_tasks:
                    results = await asyncio.wait_for(asyncio.gather(*initial_fetch_tasks, return_exceptions=True), timeout=90.0)
                    successful, save_tasks = 0, []
                    for account_key, result in zip(all_account_keys, results):
                        if isinstance(result, Exception):
                            logger.error(f"[PositionsFetcher.start] Background: ❌ Initial fetch FAILED for {account_key}: {result}", exc_info=True)
                        elif isinstance(result, set):
                            successful += 1
                            logger.warning(f"[PositionsFetcher.start] Background: ✅ Initial fetch SUCCESS for {account_key}: {len(result)} positions")
                            save_tasks.append(self._broadcast_and_save(account_key, result))
                    if save_tasks: await asyncio.gather(*save_tasks, return_exceptions=True)
            except Exception as e:
                logger.error(f"[PositionsFetcher.start] Background: Error during initial fetch: {e}", exc_info=True)
        asyncio.create_task(initial_fetch_background())
        total_positions = sum(len(acc_pos) for acc_pos in self.position_service.positions_by_account.values())
        if total_positions == 0:
            logger.error(f"[PositionsFetcher.start] CRITICAL: No positions loaded! Cannot start WebSocket or fetch loops!")
            return
        logger.info(f"[PositionsFetcher.start] 🚀🚀🚀 STEP 4: Starting fetch loops and WebSockets for {len(self.position_service.accounts)} accounts")
        fetch_tasks, websocket_tasks = [], []
        for account_key in self.position_service.accounts.keys():
            try:
                fetch_task = asyncio.create_task(self._fetch_loop(account_key))
                fetch_tasks.append((account_key, fetch_task))
            except Exception as loop_err:
                logger.critical(f"[PositionsFetcher.start] ❌ Failed to create fetch loop for {account_key}: {loop_err}", exc_info=True)
        for account_key, account in self.position_service.accounts.items():
            if is_sandbox_account(self.position_service.config, account_key):
                logger.info(f"[PositionsFetcher.start] 🧪 Skipping WebSocket for sandbox account {account_key}")
                continue
            api_key = getattr(account, "api_key", None) or getattr(account, "api_key_plain", None)
            api_secret = getattr(account, "api_secret", None) or getattr(account, "api_secret_plain", None)
            if api_key and api_secret:
                try:
                    ws_manager = WebSocketManager(account_keys=[account_key], api_key=api_key, api_secret=api_secret, config=self.position_service.config, symbols=None, service=self.position_service)
                    original_handle = ws_manager.handle_account_update
                    async def handle_with_save_broadcast(data: dict, account_key_param: str):
                        try:
                            await original_handle(data, account_key_param)
                            account_positions = self.position_service.positions_by_account.get(account_key_param, {})
                            if not account_positions: return
                            updated_keys = set()
                            if isinstance(data, dict) and "a" in data:
                                positions_list = data.get("a", {}).get("P", [])
                                for pos_data in positions_list:
                                    symbol = pos_data.get("s")
                                    raw_amt = safe_fetch_float(pos_data.get("pa"), 0.0)
                                    side = (pos_data.get("ps") or ("LONG" if raw_amt >= 0 else "SHORT")).upper()
                                    if symbol: updated_keys.add(f"{account_key_param}:{symbol}_{side}")
                            if updated_keys:
                                await self.position_service._broadcast_positions_to_redis(account_key_param, updated_keys)
                        except Exception as e:
                            logger.error(f"[WS_UPDATE][{account_key_param}] ❌ Error: {e}", exc_info=True)
                    ws_manager.handle_account_update = handle_with_save_broadcast
                    self.ws_managers[account_key] = ws_manager
                    ws_task = asyncio.create_task(ws_manager.start(account_key))
                    websocket_tasks.append((account_key, ws_task))
                except Exception as ws_create_err:
                    logger.error(f"[PositionsFetcher.start] ❌ Failed to create WebSocket for {account_key}: {ws_create_err}", exc_info=True)
        await asyncio.sleep(1.0)
        logger.info(f"[PositionsFetcher.start] ✅ Started: {sum(1 for _, t in fetch_tasks if not t.done())} fetch loops, {sum(1 for _, t in websocket_tasks if not t.done())} WebSockets")
    async def _fetch_loop(self, account_key: str):
        logger.info(f"[_fetch_loop][{account_key}] BACKUP FETCH LOOP — only fetches when data is stale >6s")
        loop_count = 0
        while self._running:
            loop_count += 1
            try:
                is_stale = True
                try:
                    acc_positions = self.position_service.positions_by_account.get(account_key, {})
                    if acc_positions:
                        for pk, pos in acc_positions.items():
                            if hasattr(pos, 'last_updated') and pos.last_updated:
                                from datetime import datetime, timezone
                                age = (datetime.now(timezone.utc) - pos.last_updated).total_seconds() if isinstance(pos.last_updated, datetime) else 999
                                if age < 6.0:
                                    is_stale = False
                                    break
                except Exception:
                    pass
                if is_stale:
                    logger.warning(f"[_fetch_loop][{account_key}] Data stale >6s — backup fetch activating")
                    updated_keys = await self._fetch_positions_via_api(account_key)
                    await self._broadcast_and_save(account_key, updated_keys)
                    if len(updated_keys) > 0:
                        logger.info(f"[_fetch_loop][{account_key}] ✅ Backup: Updated {len(updated_keys)} positions")
            except Exception as e:
                logger.error(f"[_fetch_loop][{account_key}] ❌ Sync error: {e}", exc_info=True)
            await asyncio.sleep(6.0)
    async def _fetch_positions_via_api(self, account_key: str) -> Set[str]:
        if is_sandbox_account(self.position_service.config, account_key): return set()
        now = time.time()
        last = self._last_positions_fetch.get(account_key, 0.0)
        if last > 0.0 and (now - last) < 5.0: return set()
        account = self.position_service.accounts.get(account_key)
        if not account: return set()
        if not getattr(account, "client", None):
            try:
                await self.position_service._ensure_account_client(account_key)
            except Exception as e:
                logger.error(f"[_fetch_positions_via_api][{account_key}] Client creation failed: {e}")
                return set()
        try:
            client_obj = getattr(account, "client", None)
            if not client_obj: return set()
            async with self.position_service.maker_semaphore:
                positions_data = await asyncio.wait_for(asyncio.to_thread(client_obj.futures_position_information), timeout=30.0)
            if not isinstance(positions_data, list): return set()
            updated_keys = await self.position_service.process_account_update(account_key, positions_data, single=False, skip_broadcast_save=True)
            self._last_positions_fetch[account_key] = time.time()
            return updated_keys if isinstance(updated_keys, set) else set()
        except Exception as e:
            logger.error(f"[_fetch_positions_via_api][{account_key}] API error: {e}", exc_info=True)
            return set()
    async def _broadcast_and_save(self, account_key: str, updated_keys: Set[str]):
        try:
            account_positions = self.position_service.positions_by_account.get(account_key, {})
            if not account_positions: return
            if updated_keys:
                try: await self.position_service._broadcast_positions_to_redis(account_key, updated_keys)
                except Exception as e: logger.error(f"[_broadcast_and_save][{account_key}] Redis update failed: {e}")
            try:
                await atomic_save_positions(self.position_service, account_key)
            except Exception as save_err:
                logger.error(f"[_broadcast_and_save][{account_key}] SAVE FAILED: {save_err}", exc_info=True)
            now = time.time()
            if (now - self._last_full_broadcast.get(account_key, 0.0)) >= self._full_broadcast_interval:
                all_positions_by_side = {"LONG": {}, "SHORT": {}}
                for pos_key, pos_obj in account_positions.items():
                    if pos_obj and hasattr(pos_obj, "to_dict"):
                        side = pos_obj.position_side
                        if side in all_positions_by_side:
                            all_positions_by_side[side][pos_key] = pos_obj.to_dict()
                for side, pos_dict in all_positions_by_side.items():
                    if pos_dict:
                        await self.position_service._broadcast_positions_to_redis(account_key, pos_dict)
                self._last_full_broadcast[account_key] = now
        except Exception as e:
            logger.error(f"[_broadcast_and_save][{account_key}] Error: {e}", exc_info=True)
    async def stop(self):
        self._running = False
        for ws_manager in self.ws_managers.values():
            try: await ws_manager.stop()
            except Exception: pass
        self.ws_managers.clear()

async def run_service() -> None:
    logger.info("[ez_positions] Starting position management service")
    config = Config()
    accounts = await load_accounts_from_config(config, logger)
    if not accounts:
        logger.error("[ez_positions] CRITICAL: No accounts loaded!")
        return
    service = await bootstrap_position_service(logger=logger, accounts=accounts, enable_auto_fetch=False, load_priority='disk')
    logger.info(f"[ez_positions] Service created with {len(service.accounts)} accounts")
    if config.SANDBOX_MODE:
        for sa in config.SANDBOX_ACCOUNTS:
            sa_dir = config.BASE_PATH / sa
            sa_dir.mkdir(parents=True, exist_ok=True)
            for fn in ['long_positions.json', 'short_positions.json', 'long_ladder.json', 'short_ladder.json', 'long_reentry.json', 'short_reentry.json', 'long_stop_levels.json', 'short_stop_levels.json', 'tracker.json']:
                fp = sa_dir / fn
                if not fp.exists(): fp.write_text('{}')
            logger.info(f"[ez_positions] 🧪 Sandbox account directory ready: {sa_dir}")
    fetcher = PositionsFetcher(service)
    logger.warning("[ez_positions] Starting PositionsFetcher - WAITING for positions...")
    await fetcher.start()
    logger.info("[ez_positions] Starting service operations...")
    try:
        asyncio.create_task(service._force_start_all_monitors())
        asyncio.create_task(service._ensure_monitors_running())
        asyncio.create_task(service.start_housekeeping_tasks())
    except Exception as e:
        logger.error(f"[ez_positions] Service start failed: {e}", exc_info=True)
    # NOTE: Realtime updaters are launched by ez_positions_watchdog.py when data is stale — NOT here
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: stop_event.set())
    async def monitor_reductions_priority_loop(acc_key: str):
        await asyncio.sleep(5)
        while not stop_event.is_set():
            try:
                await service.monitor_reductions_priority(acc_key)
                await asyncio.sleep(15.0)
            except Exception as e:
                logger.error(f"[MONITOR_PRIORITY_LOOP][{acc_key}] Error: {e}")
                await asyncio.sleep(15.0)
    for acc_key in list(service.accounts.keys()):
        asyncio.create_task(monitor_reductions_priority_loop(acc_key))
        
    # async def emergency_monitor_loop():
    #     await asyncio.sleep(15)
    #     while not stop_event.is_set():
    #         try:
    #             now_dt = datetime.now(timezone.utc)
    #             for account_key in list(service.accounts.keys()):
    #                 for position_key, position in list(service.positions_by_account.get(account_key, {}).items()):
    #                     if not position or abs(float(getattr(position, "positionAmt", 0))) <= 0: continue
    #                     gain = safe_fetch_float(getattr(position, "gain", 0.0), 0.0)
    #                     if gain >= 0.2: continue
    #                     symbol = getattr(position, "symbol", None)
    #                     current_price = safe_fetch_float(getattr(position, "mark_price", 0.0), 0.0)
    #                     if current_price <= 0 and symbol:
    #                          try: current_price = safe_fetch_float(await service.get_mark_price(symbol, allow_fallback=True), 0.0)
    #                          except: continue
    #                     if current_price <= 0: continue
    #                     indicators = ii(service, symbol)
    #                     if not indicators: continue
    #                     is_long = getattr(position, "position_side", "LONG") == "LONG"
    #                     indicator_price = safe_fetch_float(indicators.get("current_price", 0), current_price)
    #                     price_going_down = False
    #                     if indicator_price > 0:
    #                         price_going_down = (is_long and current_price < indicator_price) or (not is_long and current_price > indicator_price)
    #                     if price_going_down and minutes_since(getattr(position, "last_reduction_time", None), now_dt) >= 3:
    #                         logger.warning(f"[EMERGENCY_CLOSE] {position_key}: FULL CLOSE - gain={gain:.2f}% < 0.2% going down")
    #                         side = "SELL" if is_long else "BUY"
    #                         unique_id = f"emergency_close_{int(time.time())}"
    #                         await service.execute_now(position_key, account_key, symbol, position.positionAmt, side, position.position_side, position.positionAmt, current_price, unique_id, "EMERGENCY_CLOSE_GAIN_LOW", True, "CLOSE")
    #         except Exception as e:
    #             logger.error(f"[EMERGENCY_MONITOR] Error: {e}")
    #             await asyncio.sleep(10)
    #         await asyncio.sleep(10)
    # asyncio.create_task(emergency_monitor_loop())
    while not stop_event.is_set():
        await asyncio.sleep(1)
    await fetcher.stop()
    await service.shutdown()
    for account in accounts.values():
        try: await account.close()
        except Exception: pass
    try:
        total_positions = sum(len(acc_pos) for acc_pos in service.positions_by_account.values())
        if total_positions >= 230:
            logger.info(f"[ez_positions] Final save: {total_positions} positions")
            # DISABLED: ez_positions.py handles all saving - atomic_save _positions is called above
            # await service._save_all_accounts(force=True)
    except Exception as e:
        logger.error(f"[ez_positions] Final save failed: {e}")

if __name__ == "__main__":
    asyncio.run(run_service())
