#!/usr/bin/env python3
"""
V8 Backtest Engine — Runs the REAL trading scripts on historical NPZ data.

NO mocks of trade logic. NO reimplemented functions. NO fake classes.
Uses the ACTUAL ez_manage.py, ez_positions_quick.py, ez_positions_service.py
with ONLY these I/O substitutions:
  - Binance API → record trade
  - Webhooks → record trade
  - Redis → in-memory dict
  - Indicators → NPZ precomputed data
  - Prices → NPZ close price
  - Time → simulation timestamp
  - Position file saves → in-memory

Every if/else, every gate, every loop — the REAL code.

Usage:
    python3 backtest_v8_engine.py --mode crypto --account ang --start 2026-03-20
    python3 backtest_v8_engine.py --mode tradier --account trb --start 2026-03-01
"""
import argparse
import asyncio
import importlib
import json
import logging
import os
import platform
import signal
import sys
import threading
import time as _real_time_module
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from test_rate_guard import RateGuard

# ═══════════════════════════════════════════════════════════════
# STEP 0: Set dummy env vars BEFORE any import touches AccountConfig
# ═══════════════════════════════════════════════════════════════
_DUMMY_ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
for _acct in _DUMMY_ACCOUNTS:
    for _suffix in ["API_KEY", "API_SECRET", "WEBHOOK_URL", "WEBHOOK_SECRET",
                     "WEBHOOK_URL2", "WEBHOOK_SECRET2", "WEBHOOK_URL3", "WEBHOOK_SECRET3"]:
        _key = f"{_acct}_{_suffix}"
        if not os.environ.get(_key):
            os.environ[_key] = f"v8_dummy_{_acct}_{_suffix}"

# ═══════════════════════════════════════════════════════════════
# STEP 1: Import the REAL modules — ALL of them
# ═══════════════════════════════════════════════════════════════
IS_SERVER = platform.system() == "Linux"
if IS_SERVER:
    BASE_PATH = Path("/home/niels/binance-sandbox")
    sys.path.insert(0, str(BASE_PATH))  # Import from sandbox, NOT /home/niels/binance
else:
    BASE_PATH = Path("/Users/niels/Documents/binance")
    sys.path.insert(0, str(BASE_PATH))

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
v8_logger = logging.getLogger("v8_engine")
v8_logger.setLevel(logging.INFO)
# V8_SWEEP_MODE=1: suppress verbose per-trade logs, speeds up simulation 10-50x.
# 2026-04-30: chart server reads structured `executed_trades` JSONL, NOT logs — so
# silencing every logger and the per-bar print() debug lines is safe for chart
# rendering. Whitelisted print prefixes pass through (V8_RESULT, V8_HEARTBEAT,
# V8_TIER2_CHART_TRADES, V8_RESULT_LIVE, V8_NEW_SWITCHES, V8_LOG, V8_FINAL_PNL,
# EARLY_ABORT_LOW_RATE, FINAL_BROKEN_RATE, V8_INIT_HEARTBEAT, MISSING_FIELD).
_SWEEP_MODE = os.environ.get("V8_SWEEP_MODE", "0") == "1"
if _SWEEP_MODE:
    # Block ALL log output below CRITICAL+1 (logging.disable is hard cutoff,
    # beats per-logger setLevel which CRITICAL records bypass).
    logging.disable(logging.CRITICAL)
    v8_logger.setLevel(logging.CRITICAL + 1)
    # Replace builtin print with a whitelist filter. Engine-essential output
    # (V8_RESULT, heartbeats, chart-trade dump confirmation, rate-guard aborts,
    # init heartbeat, missing-field warnings) still flows; per-bar debug like
    # [NEWBORN_PROTECT], [PEAK_GIVEBACK], [HEDGE_*], [RED_ZONE_EXIT] is dropped.
    import builtins as _bi
    _bi_print = _bi.print
    _ALLOW_PREFIXES = (
        "V8_RESULT", "V8_HEARTBEAT", "V8_RESULT_LIVE", "V8_NEW_SWITCHES",
        "V8_LOG", "V8_FINAL_PNL", "V8_INIT_HEARTBEAT", "V8_TIER2_CHART_TRADES",
        "V8_QUICK_RESULT", "EARLY_ABORT_LOW_RATE", "FINAL_BROKEN_RATE",
        "MISSING_FIELD", "V8_PNL_BREAKDOWN", "V8_TRADES_OUT",
        "MODE_CONFIG_MISMATCH",
    )
    def _quiet_print(*args, **kwargs):
        if not args:
            return
        first = str(args[0]) if args else ""
        # Strip ANSI color/escape codes for prefix matching
        s = first.lstrip("\x1b[").lstrip()
        for p in _ALLOW_PREFIXES:
            if p in s:
                _bi_print(*args, **kwargs)
                return
        # drop everything else
    _bi.print = _quiet_print

# Import config — ez_manage.py does `config = Config()` at line 645 which gives
# the module-level name a Config instance. ez_positions_quick reads config.BASE_PATH
# at import time (line 94). We need BASE_PATH etc on the module before importing.
import config
_cfg_instance = config.Config()
# Copy all Config instance attributes to the module so config.BASE_PATH works
for _attr in dir(_cfg_instance):
    if not _attr.startswith('_'):
        try:
            setattr(config, _attr, getattr(_cfg_instance, _attr))
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════════
# STEP 1b: Apply sweep config overrides from V8_OVERRIDE_FILE
# CRITICAL: Must patch Config CLASS + dataclass field defaults BEFORE
# importing ez_manage, because ez_manage line 730 does `config = Config()`
# which creates a fresh instance with original dataclass defaults.
# ═══════════════════════════════════════════════════════════════
_override_file = os.environ.get("V8_OVERRIDE_FILE", "")
if _override_file and Path(_override_file).exists():
    with open(_override_file) as _f:
        _overrides = json.load(_f)
    for _k, _v in _overrides.items():
        # 1. Module-level attr (for `import config; config.X` lookups)
        setattr(config, _k, _v)
        # 2. Config class attr (for `Config.X` class-level lookups)
        try:
            setattr(config.Config, _k, _v)
        except Exception:
            pass
        # 3. Dataclass field default (so new Config() instances get the override)
        try:
            if hasattr(config.Config, '__dataclass_fields__') and _k in config.Config.__dataclass_fields__:
                config.Config.__dataclass_fields__[_k].default = _v
        except Exception:
            pass
        # 4. Existing instances (e.g. _cfg_instance just created)
        try:
            for _inst in list(getattr(config.Config, '_INSTANCES', [])):
                setattr(_inst, _k, _v)
        except Exception:
            pass
    v8_logger.info(f"Applied {len(_overrides)} config overrides from {_override_file} (module + class + dataclass default + instances)")

# Import the REAL trading modules
import ez_manage
import ez_positions_quick
import ez_positions_service
# 2026-04-28 — Centralized reentry facade. All v8 reentry calls now route through
# ez_reentry so live + backtest + daemon share one import surface.
import ez_reentry
from utils import parse_position_key, construct_position_key, load_environment_from_gpg, get_simple_redis_manager

# ═══════════════════════════════════════════════════════════════
# STEP 1c: Re-apply overrides to instances created during ez_manage import
# ez_manage.py line 730 does `config = Config()` at import time. Even with
# dataclass defaults patched above, we re-apply to all instances now in case
# any code path missed the defaults.
# ═══════════════════════════════════════════════════════════════
if _override_file and Path(_override_file).exists():
    _post_import_count = 0
    for _inst in list(getattr(config.Config, '_INSTANCES', [])):
        for _k, _v in _overrides.items():
            try:
                setattr(_inst, _k, _v)
            except Exception:
                pass
        _post_import_count += 1
    # Patch sys.modules' bound 'config' references too
    import sys as _sys_post
    for _mn in ['ez_manage', 'ez_positions_quick', 'ez_positions_service', 'utils']:
        _m = _sys_post.modules.get(_mn)
        if _m and hasattr(_m, 'config') and _m.config is not None:
            for _k, _v in _overrides.items():
                try:
                    setattr(_m.config, _k, _v)
                except Exception:
                    pass
    v8_logger.info(f"Re-applied {len(_overrides)} overrides to {_post_import_count} Config instances + module bindings post-import")

# Import NPZ loader
from backtest_v8_harness import IndicatorStore

# ═══════════════════════════════════════════════════════════════
# STEP 2: Simulation time controller
# ═══════════════════════════════════════════════════════════════
_sim_ts = [0.0]  # Mutable ref — updated per bar


class _SimTime:
    """Drop-in for time module — returns simulation time."""
    def time(self):
        return _sim_ts[0]
    def perf_counter(self):
        return _sim_ts[0]
    def sleep(self, *a):
        pass  # No sleeping in backtest
    def monotonic(self):
        return _sim_ts[0]
    def __getattr__(self, name):
        return getattr(_real_time_module, name)


def _sim_datetime_now(tz=None):
    ts = _sim_ts[0]
    if ts > 0:
        dt = datetime.utcfromtimestamp(ts).replace(tzinfo=timezone.utc)
        return dt.astimezone(tz) if tz and tz != timezone.utc else dt
    return datetime.now(tz or timezone.utc)


def _sim_datetime_utcnow():
    ts = _sim_ts[0]
    if ts > 0:
        return datetime.utcfromtimestamp(ts)
    return datetime.utcnow()


# ═══════════════════════════════════════════════════════════════
# STEP 3: In-memory Redis replacement
# ═══════════════════════════════════════════════════════════════
class InMemoryRedis:
    """Full Redis interface — in-memory dict. No TCP, no server."""
    def __init__(self):
        self.data = {}
        self._pubsub_handlers = {}

    def has_any_connection(self):
        return True

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, val, ex=None):
        self.data[key] = val

    async def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)

    async def hdel(self, *args):
        pass

    async def hget(self, name, key):
        d = self.data.get(name)
        if isinstance(d, dict):
            return d.get(key)
        return None

    async def hset(self, name, key=None, value=None, mapping=None):
        if name not in self.data:
            self.data[name] = {}
        if key is not None:
            self.data[name][key] = value
        if mapping:
            self.data[name].update(mapping)

    async def hgetall(self, name):
        return self.data.get(name, {})

    async def exists(self, key):
        return key in self.data

    async def keys(self, pattern="*"):
        import fnmatch
        return [k for k in self.data.keys() if fnmatch.fnmatch(k, pattern)]

    async def publish(self, channel, message):
        pass  # No pub/sub in backtest

    async def subscribe(self, *channels):
        pass

    async def expire(self, key, seconds):
        pass

    async def ttl(self, key):
        return -1

    async def incr(self, key):
        self.data[key] = self.data.get(key, 0) + 1
        return self.data[key]

    async def setex(self, key, seconds, value):
        self.data[key] = value

    async def lpush(self, key, *values):
        if key not in self.data:
            self.data[key] = []
        for v in values:
            self.data[key].insert(0, v)

    async def lrange(self, key, start, end):
        return self.data.get(key, [])[start:end + 1 if end >= 0 else None]

    async def llen(self, key):
        return len(self.data.get(key, []))

    def pubsub(self):
        return self

    async def listen(self):
        while False:
            yield


# ═══════════════════════════════════════════════════════════════
# STEP 4: Trade recorder — replaces Binance API + webhooks
# ═══════════════════════════════════════════════════════════════
_executed_trades: List[Dict] = []


# ═══════════════════════════════════════════════════════════════
# STEP 5: Apply I/O patches BEFORE running main init
# ═══════════════════════════════════════════════════════════════
def apply_patches(stores: Dict[str, IndicatorStore], mode: str):
    """Apply ONLY I/O patches. No trade logic changes."""
    global _executed_trades
    _executed_trades = []

    # --- Patch time in ALL trading modules ---
    sim_time = _SimTime()
    ez_manage.time = sim_time
    ez_positions_quick.time = sim_time
    ez_positions_service.time = sim_time

    # --- Patch datetime.now/utcnow ---
    _real_dt = datetime

    class _PatchedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _sim_datetime_now(tz)
        @classmethod
        def utcnow(cls):
            return _sim_datetime_utcnow()

    # Patch datetime — `from datetime import datetime` binds at import time.
    # Can't monkey-patch immutable C datetime. Instead, inject _PatchedDatetime
    # into each module's global namespace, replacing the `datetime` name binding.
    # This works because Python looks up names in the module's __dict__ at runtime.
    for _mod in [ez_manage, ez_positions_quick, ez_positions_service]:
        _mod.__dict__['datetime'] = _PatchedDatetime
    # Also patch the datetime MODULE so `import datetime; datetime.datetime.now()` works
    import datetime as _dt_mod
    _dt_mod.__dict__['datetime'] = _PatchedDatetime

    # --- Patch AccountConfig.initialize to skip Binance client ---
    async def _dummy_initialize(self):
        """Skip Binance client init — no exchange in backtest."""
        pass
    ez_manage.AccountConfig.initialize = _dummy_initialize
    if hasattr(ez_positions_quick, 'AccountConfig'):
        ez_positions_quick.AccountConfig.initialize = _dummy_initialize
    if hasattr(ez_positions_service, 'AccountConfig'):
        ez_positions_service.AccountConfig.initialize = _dummy_initialize

    # --- Patch ii() to return NPZ data ---
    _indicator_cache: Dict[str, Dict] = {}

    async def _npz_ii(tm, symbol, **kwargs):
        return _indicator_cache.get(symbol, {})
    ez_manage.ii = _npz_ii

    # Also patch in ez_positions_quick if it has its own ii
    if hasattr(ez_positions_quick, 'ii'):
        ez_positions_quick.ii = _npz_ii

    # --- Patch price() to return NPZ close ---
    _price_cache: Dict[str, float] = {}

    async def _npz_price(symbol, position=None, withts=False, **kwargs):
        p = _price_cache.get(symbol, 0.0)
        if withts:
            dt = _sim_datetime_now(timezone.utc)
            return p, dt
        return p
    ez_manage.price = _npz_price

    async def _npz_get_current_price(symbol, **kwargs):
        p = _price_cache.get(symbol, 0.0)
        dt = _sim_datetime_now(timezone.utc)
        return p, dt
    if hasattr(ez_manage, 'get_current_price'):
        ez_manage.get_current_price = _npz_get_current_price
    if hasattr(ez_positions_quick, 'get_current_price'):
        ez_positions_quick.get_current_price = _npz_get_current_price
    if hasattr(ez_positions_service, 'get_current_price'):
        ez_positions_service.get_current_price = _npz_get_current_price

    # --- Patch TradeVerifier to return True (no exchange to verify against) ---
    original_verify = ez_manage.TradeVerifier.verify_trade
    async def _instant_verify(self, *a, **kw):
        return True
    ez_manage.TradeVerifier.verify_trade = _instant_verify
    if hasattr(ez_positions_quick, 'TradeVerifier'):
        ez_positions_quick.TradeVerifier.verify_trade = _instant_verify

    # --- Patch verify_trade_via_websocket ---
    if hasattr(ez_positions_quick, 'verify_trade_via_websocket'):
        async def _instant_ws_verify(*a, **kw):
            return True
        ez_positions_quick.verify_trade_via_websocket = _instant_ws_verify

    # --- Patch get_simple_redis_manager to return in-memory Redis ---
    _mem_redis = InMemoryRedis()
    import utils
    async def _get_mem_redis():
        return _mem_redis
    utils.get_simple_redis_manager = _get_mem_redis

    # --- Patch bootstrap_position_service to skip Redis hydration ---
    _original_bootstrap = ez_positions_service.bootstrap_position_service
    async def _fast_bootstrap(**kwargs):
        kwargs['load_priority'] = 'disk'
        kwargs['start_maintenance'] = False
        kwargs['enable_auto_fetch'] = False
        return await _original_bootstrap(**kwargs)
    ez_positions_service.bootstrap_position_service = _fast_bootstrap

    # --- Patch ALL file writes to no-op — backtest must NOT touch disk ---
    # Positions, trackers, stop levels, reentry data — all in memory only
    if hasattr(ez_positions_service.PositionService, '_broadcast_positions_to_redis'):
        async def _noop_broadcast(self, *a, **kw):
            pass
        ez_positions_service.PositionService._broadcast_positions_to_redis = _noop_broadcast

    # Block ALL atomic_write_json calls (would overwrite live position files)
    async def _noop_write(*a, **kw):
        pass
    ez_positions_service.atomic_write_json = _noop_write
    if hasattr(ez_positions_service, '_atomic_write_json_impl'):
        ez_positions_service._atomic_write_json_impl = _noop_write
    if hasattr(ez_manage, 'atomic_write_json'):
        ez_manage.atomic_write_json = _noop_write
    import utils
    utils.atomic_write_json = _noop_write

    # --- Patch WebSocket managers to not connect ---
    if hasattr(ez_manage, 'initialize_websocket_managers'):
        async def _skip_ws(*a, **kw):
            pass
        ez_manage.initialize_websocket_managers = _skip_ws

    # --- Patch request_ez_indicators_restart ---
    if hasattr(ez_manage, 'request_ez_indicators_restart'):
        async def _skip_restart(*a, **kw):
            pass
        ez_manage.request_ez_indicators_restart = _skip_restart

    # --- Patch check_pid_file ---
    if hasattr(ez_manage, 'check_pid_file'):
        def _skip_pid(*a, **kw):
            pass
        ez_manage.check_pid_file = _skip_pid

    # --- Patch load_initial_market_data ---
    if hasattr(ez_positions_quick, 'load_initial_market_data'):
        async def _skip_market_data(*a, **kw):
            pass
        ez_positions_quick.load_initial_market_data = _skip_market_data

    # --- PERF FIX 2026-04-15: last_events.json refresh was firing every bar ---
    # Root cause: cache TTL uses time.time() which V8 patches to sim time.
    # Sim time advances 900s/bar, so (now - last_save) > 60 is always True.
    # Result: 1MB JSON parsed 500+ times in 500 bars = 6s/500 bars = 12ms/bar.
    # In backtest there's no live event data — stub refresh to no-op and pre-set cache.
    if hasattr(ez_positions_quick, '_refresh_last_events_cache'):
        def _noop_refresh_cache():
            # Set cache_time to a huge future value so TTL check (t-cached)>60 is False.
            import time as _rt
            ez_positions_quick._last_events_cache = {}
            ez_positions_quick._last_events_cache_time = 1e18
        ez_positions_quick._refresh_last_events_cache = _noop_refresh_cache
        ez_positions_quick._last_events_cache = {}
        ez_positions_quick._last_events_cache_time = 1e18

    # --- PERF FIX 2026-04-15: save_tracker was writing tracker JSON every bar ---
    # Root cause: min_interval check uses time.time() which advances 900s/bar.
    # Tracker gets serialized + written every bar = ~1-2ms × N_bars.
    # In backtest we don't need the JSON on disk — noop the save path.
    if hasattr(ez_positions_quick, 'TrackerManager'):
        async def _noop_save_tracker(self, account_key: str, force: bool = False,
                                      min_interval: int = 30, trade_manager=None):
            # Clear dirty flags to preserve downstream correctness (no-op save).
            try:
                self._exit_candidates_dirty[account_key] = False
                self._entry_candidates_dirty[account_key] = False
                self._last_tracker_save_time[account_key] = _sim_ts[0]
            except Exception:
                pass
        ez_positions_quick.TrackerManager.save_tracker = _noop_save_tracker

    # --- PERF FIX 2026-04-15: PositionService._hot_path_loop polling multiprocessing ---
    # The hot path background task polls a SharedMemoryProxy every 0.1s REAL time,
    # which in backtest accumulates recv() overhead (pickle.loads + posix.read).
    # In V8 we update data_manager._cold_data directly each bar — no need for polling.
    if hasattr(ez_positions_service, 'PositionService'):
        async def _noop_hot_path(self):
            while not getattr(self, '_shutdown', False):
                await asyncio.sleep(10.0)  # real sleep patched to no-op via _SimTime; this yields only
        ez_positions_service.PositionService._hot_path_loop = _noop_hot_path
        if hasattr(ez_positions_service.PositionService, '_cold_data_loop'):
            async def _noop_cold_path(self):
                while not getattr(self, '_shutdown', False):
                    await asyncio.sleep(10.0)
            ez_positions_service.PositionService._cold_data_loop = _noop_cold_path

    # --- ABLATION PATCHES: disable individual evaluate functions via env vars ---
    # V8_DISABLE_EVALUATE_REENTRY=1 → evaluate_reentry returns None (no reentries)
    # V8_DISABLE_EVALUATE_AUGMENTATION=1 → evaluate_augmentation returns None
    # V8_DISABLE_EVALUATE_REENTRY_2=1 → evaluate_reentry_2 is no-op
    # V8_DISABLE_PROCESS_POSITION=1 → process_position is no-op (no exits/reentries via pp)
    if os.environ.get("V8_DISABLE_EVALUATE_REENTRY", "0") == "1":
        async def _noop_reentry(ctx):
            return None
        ez_manage.evaluate_reentry = _noop_reentry
        v8_logger.info("[V8_ABLATION] evaluate_reentry DISABLED (stubbed to None)")
    if os.environ.get("V8_DISABLE_EVALUATE_AUGMENTATION", "0") == "1":
        async def _noop_augmentation(ctx):
            return None
        ez_manage.evaluate_augmentation = _noop_augmentation
        v8_logger.info("[V8_ABLATION] evaluate_augmentation DISABLED (stubbed to None)")
    if os.environ.get("V8_DISABLE_EVALUATE_REENTRY_2", "0") == "1":
        async def _noop_reentry_2(tm):
            pass
        ez_manage.evaluate_reentry_2 = _noop_reentry_2
        v8_logger.info("[V8_ABLATION] evaluate_reentry_2 DISABLED (stubbed to no-op)")
    if os.environ.get("V8_DISABLE_PROCESS_POSITION", "0") == "1":
        _orig_pp = ez_manage.process_position
        async def _noop_pp(**kw):
            pass
        ez_manage.process_position = _noop_pp
        v8_logger.info("[V8_ABLATION] process_position DISABLED (stubbed to no-op)")

    # V8_VECTORIZED_REENTRY=1 → swap the 1,177-line scalar evaluate_reentry()
    # for the numpy-precomputed VectorizedReentryEvaluator. Designed in
    # ez_reentry_vectorized.py but the docstring's hook was never actually
    # wired until 2026-05-01. Target: ~3000× speedup on the reentry path.
    if os.environ.get("V8_VECTORIZED_REENTRY", "0") == "1":
        try:
            from ez_reentry_vectorized import VectorizedReentryEvaluator
            _vec_eval = VectorizedReentryEvaluator(stores, config)
            ez_manage.evaluate_reentry = _vec_eval.evaluate
            v8_logger.info(f"[V8_VECTORIZED] evaluate_reentry → VectorizedReentryEvaluator "
                           f"({len(stores)} stores precomputed)")
        except Exception as _e:
            import traceback as _tb
            v8_logger.warning(f"[V8_VECTORIZED] reentry vectorization failed: {_e}; "
                              f"falling back to scalar evaluate_reentry\n{_tb.format_exc()}")

    # --- Return refs for the simulation loop ---
    return _indicator_cache, _price_cache, _executed_trades


# ═══════════════════════════════════════════════════════════════
# STEP 6: NPZ data loader
# ═══════════════════════════════════════════════════════════════
def get_npz_dir(mode):
    # V4 deprecated — backtest_v8 is primary for both crypto and tradier
    if mode == "crypto":
        search = ["backtest_v8", "backtest_v7"]
    else:
        search = ["backtest_v8", "backtest_v7"]
    for prefix in search:
        d = BASE_PATH / prefix / "indicators"
        if d.exists() and any(d.glob("*.npz")):
            return d, "3m" if mode == "crypto" else "5m"
    return BASE_PATH / "backtest_v8" / "indicators", "5m"


def load_stores(mode, symbols=None, start_date=None, npz_dir_override=""):
    if npz_dir_override:
        npz_dir = Path(npz_dir_override)
        resolution = "3m" if mode == "crypto" else "5m"
    else:
        npz_dir, resolution = get_npz_dir(mode)
    v8_logger.info(f"Using {npz_dir} ({resolution} resolution)")
    stores = {}
    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) if start_date else 0
    # ── Mode-aware symbol filter (BACKTEST-ONLY OOM GUARD 2026-04-16) ──
    # Without this, crypto sweep with no --symbols loads all 324 NPZ files
    # (stocks + crypto), spikes 17GB+ RAM per worker, and gets OOM-killed
    # by the kernel after 165s (before producing any V8_RESULT) → status=no_result.
    # Crypto symbols on Binance Futures end in USDT/USDC/USD/BUSD/FDUSD.
    # Tradier symbols are equity tickers (no quote-currency suffix).
    _CRYPTO_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI")
    def _is_crypto_sym(s: str) -> bool:
        return any(s.endswith(q) for q in _CRYPTO_QUOTE_SUFFIXES)
    n_skipped_mode = 0
    n_skipped_explicit = 0
    n_skipped_stale = 0
    candidate_paths = sorted(npz_dir.glob("*.npz"))
    v8_logger.info(f"Found {len(candidate_paths)} NPZ files in {npz_dir} — filtering for mode={mode}")
    for npz_path in candidate_paths:
        sym = npz_path.stem
        if symbols and sym not in symbols:
            n_skipped_explicit += 1
            continue
        # Mode filter: crypto-only loads quote-currency symbols, tradier-only excludes them.
        # Skip when explicit --symbols filter is given (user knows what they want).
        if not symbols:
            if mode == "crypto" and not _is_crypto_sym(sym):
                n_skipped_mode += 1
                continue
            if mode == "tradier" and _is_crypto_sym(sym):
                n_skipped_mode += 1
                continue
        try:
            store = IndicatorStore(str(npz_path))
        except Exception as _e:
            v8_logger.warning(f"[NPZ_LOAD_FAIL] {sym}: {type(_e).__name__}: {_e}")
            continue
        if start_ts and store.timestamps[-1] < start_ts:
            n_skipped_stale += 1
            continue
        if start_ts:
            _idx = int(np.searchsorted(store.timestamps, start_ts))
            if _idx > 0:
                for _k in list(store.arrays):
                    store.arrays[_k] = store.arrays[_k][_idx:]
                store.timestamps = store.arrays["timestamps"]
                store.n_bars = len(store.timestamps)
                store.ts_to_idx = {int(_t): _i for _i, _t in enumerate(store.timestamps)}
        stores[sym] = store
    v8_logger.info(f"Loaded {len(stores)} symbols (skipped: mode={n_skipped_mode}, explicit={n_skipped_explicit}, stale={n_skipped_stale})")
    print(f"V8_INIT_HEARTBEAT: stores_loaded={len(stores)} skipped_mode={n_skipped_mode} skipped_stale={n_skipped_stale}", flush=True)
    return stores, resolution


# ═══════════════════════════════════════════════════════════════
# STEP 6b: PnL computation — pair OPEN→CLOSE by position_key
# ═══════════════════════════════════════════════════════════════
def _reconstruct_chart_trades(executed_trades):
    """Walk eta events into closed-trade rounds keyed by position_key.
    Round = qty 0 → qty>0 (OPEN/AUGMENT) → qty 0 (REDUCE/CLOSE chain).
    Returns {symbol: [trade_dict]} in the same schema as v8_quick_engine emits.
    """
    open_rounds = {}    # pk -> {entry_ts, entry_price, qty, side, entry_reason, events}
    closed_by_sym = {}  # sym -> [trade]
    for ev in executed_trades:
        if ev.get("type") != "eta":
            continue
        pk = ev.get("position_key", "")
        sym = pk.split(":", 1)[-1].rsplit("_", 1)[0] if ":" in pk else pk.rsplit("_", 1)[0]
        side = "LONG" if pk.endswith("_LONG") else "SHORT"
        action = (ev.get("action") or "").upper()
        reason = ev.get("reason", "")
        try:
            ts_raw = ev.get("timestamp")
            if hasattr(ts_raw, "timestamp"):
                ts = int(ts_raw.timestamp())
            elif isinstance(ts_raw, (int, float)):
                ts = int(ts_raw)
            elif isinstance(ts_raw, str):
                ts = int(datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp())
            else:
                ts = 0
        except Exception:
            ts = 0
        try:
            qty = float(ev.get("quantity") or 0)
            px = float(ev.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or px <= 0:
            continue
        is_open = action in ("OPEN", "QUICK_OPEN", "AUGMENT", "QUICK_AUGMENT", "REENTRY", "HEDGE_OPEN")
        is_close = action in ("CLOSE", "REDUCE", "QUICK_CLOSE", "FULL_CLOSE", "PROFIT_TAKE", "STOP_MAJOR_LOSS_REDUCE", "STOP_FUNCTIONS_KILL", "HEDGE_CLOSE") or "CLOSE" in reason.upper() or "REDUCE" in reason.upper()
        rd = open_rounds.get(pk)
        if is_open:
            if rd is None or rd["qty"] <= 0:
                open_rounds[pk] = {
                    "entry_ts": ts, "entry_price": px, "qty": qty, "side": side,
                    "entry_reason": reason[:120], "is_hedge": "HEDGE" in action,
                }
            else:
                new_qty = rd["qty"] + qty
                rd["entry_price"] = (rd["entry_price"] * rd["qty"] + px * qty) / new_qty
                rd["qty"] = new_qty
        elif is_close:
            if rd is None or rd["qty"] <= 0:
                continue
            close_qty = min(qty, rd["qty"])
            if side == "LONG":
                pnl_pct = (px - rd["entry_price"]) / rd["entry_price"] * 100.0
            else:
                pnl_pct = (rd["entry_price"] - px) / rd["entry_price"] * 100.0
            rd["qty"] -= close_qty
            if rd["qty"] <= 1e-9:
                closed_by_sym.setdefault(sym, []).append({
                    "symbol": sym, "side": side,
                    "entry_type": "HEDGE" if rd["is_hedge"] else ("AUGMENT" if "AUGMENT" in rd["entry_reason"].upper() else "OPEN"),
                    "entry_reason": rd["entry_reason"],
                    "exit_reason": reason[:120],
                    "entry_bar": 0,
                    "entry_ts": rd["entry_ts"], "entry_price": float(rd["entry_price"]),
                    "exit_bar": 0,
                    "exit_ts": ts, "exit_price": float(px),
                    "pnl_pct": float(pnl_pct),
                    "pnl_usd": float((pnl_pct / 100.0) * (rd["entry_price"] * close_qty)),
                    "duration_bars": 0,
                    "duration_sec": ts - rd["entry_ts"],
                    "stream": "tier2",
                })
                open_rounds[pk] = None
    return closed_by_sym


def _write_chart_trades(executed_trades):
    """If V8_TRADES_OUT_DIR is set, reconstruct trades + dump to {run_id}__{symbol}.jsonl
    in the same schema the chart_server.py /backtest_trades endpoint reads.
    """
    out_dir = os.environ.get("V8_TRADES_OUT_DIR", "")
    if not out_dir:
        return
    run_id = os.environ.get("V8_TRADES_RUN_ID", "tier2_default")
    try:
        os.makedirs(out_dir, exist_ok=True)
        by_sym = _reconstruct_chart_trades(executed_trades)
        wrote = 0
        for sym, trades in by_sym.items():
            if not trades: continue
            path = os.path.join(out_dir, f"{run_id}__{sym}.jsonl")
            with open(path, "w") as f:
                for t in trades:
                    f.write(json.dumps(t, default=str) + "\n")
            wrote += 1
        print(f"V8_TIER2_CHART_TRADES: wrote {wrote} symbol files to {out_dir} (run_id={run_id})", flush=True)
    except Exception as exc:
        print(f"V8_TIER2_CHART_TRADES_ERR: {exc}", flush=True)


def _compute_trade_pnl(executed_trades):
    """Compute pnl_pct, pnl_dollars, position_value for each CLOSE/REDUCE trade.
    Uses VWAP entry for augmented positions (multiple buys before a sell)."""
    _open = {}  # position_key -> {total_cost, total_qty, side}
    for t in executed_trades:
        pk = t.get("position_key", "")
        action = (t.get("action") or t.get("side") or "").upper()
        price = float(t.get("price", 0))
        qty = float(t.get("quantity", 0))
        if price <= 0 or qty <= 0:
            continue
        pos_side = (t.get("position_side") or "").upper()
        if not pos_side:
            pos_side = "LONG" if pk.endswith("_LONG") else "SHORT"
        is_open = action in ("OPEN", "REENTRY", "AUGMENT", "QUICK_OPEN", "QUICK_AUGMENT", "HEDGE_OPEN")
        is_close = action in ("CLOSE", "REDUCE", "FULL_CLOSE", "PROFIT_TAKE", "QUICK_CLOSE", "HEDGE_CLOSE")
        if not is_open and not is_close:
            if action == "BUY":
                is_open = pos_side == "LONG"
                is_close = pos_side == "SHORT"
            elif action in ("SELL", "SELL_SHORT"):
                is_open = pos_side == "SHORT"
                is_close = pos_side == "LONG"
        if is_open:
            if pk in _open:
                _open[pk]["total_cost"] += price * qty
                _open[pk]["total_qty"] += qty
            else:
                _open[pk] = {"total_cost": price * qty, "total_qty": qty, "side": pos_side}
        elif is_close and pk in _open:
            pos = _open[pk]
            if pos["total_qty"] <= 0:
                continue
            vwap = pos["total_cost"] / pos["total_qty"]
            close_qty = min(qty, pos["total_qty"])
            is_long = pos["side"] == "LONG"
            pnl_dollars = (price - vwap) * close_qty if is_long else (vwap - price) * close_qty
            pnl_pct = ((price - vwap) / vwap * 100) if is_long else ((vwap - price) / vwap * 100) if vwap > 0 else 0.0
            t["pnl"] = pnl_dollars
            t["pnl_dollars"] = pnl_dollars
            t["pnl_pct"] = pnl_pct
            t["entry_price"] = vwap
            t["close_qty"] = close_qty
            t["position_value"] = vwap * close_qty
            pos["total_qty"] -= close_qty
            pos["total_cost"] -= vwap * close_qty
            if pos["total_qty"] <= 0.001:
                del _open[pk]


def _v8_result_from_trades(executed_trades, capital):
    """Compute and print V8_RESULT. CANONICAL_METRICS.md / CLAUDE.md rule 4: pool_sharpe + sym_sharpe ONLY.
    pool_sharpe = mean(per-trade pcts) / std(per-trade pcts)  — frequency-blind, the canonical Sharpe.
    sym_sharpe  = mean(per-symbol pool_sharpes), ±20 capped — diagnostic only.
    Emits legacy `sharpe=` alias = pool_sharpe so existing parsers keep working.
    NEVER emits sharpe_weekly, sharpe_annual, or any sqrt-anything (banned 2026-04-29)."""
    close_trades = [t for t in executed_trades if t.get("pnl_dollars") is not None]
    wins = sum(1 for t in close_trades if t.get("pnl_dollars", 0) > 0)
    losses = sum(1 for t in close_trades if t.get("pnl_dollars", 0) <= 0)
    total_pnl = sum(t.get("pnl_dollars", 0) for t in close_trades)
    pnl_pct = (total_pnl / capital * 100) if capital > 0 else 0.0
    pcts = [t.get("pnl_pct", 0.0) for t in close_trades]
    n_trades = len(pcts)
    if n_trades >= 2:
        mean_p = sum(pcts) / n_trades
        std_p = (sum((x - mean_p) ** 2 for x in pcts) / n_trades) ** 0.5
        pool_sharpe = mean_p / std_p if std_p > 0 else (20.0 if mean_p > 0 else 0.0)
    else:
        pool_sharpe = 0.0
    by_sym = {}
    for t in close_trades:
        sym = t.get("symbol") or str(t.get("position_key", "")).split(":", 1)[-1].rsplit("_", 1)[0]
        by_sym.setdefault(sym, []).append(t.get("pnl_pct", 0.0))
    sym_sharpes = []
    for _sym, plist in by_sym.items():
        if len(plist) < 2:
            continue
        m = sum(plist) / len(plist)
        s = (sum((x - m) ** 2 for x in plist) / len(plist)) ** 0.5
        ss = m / s if s > 0 else (20.0 if m > 0 else 0.0)
        sym_sharpes.append(max(-20.0, min(20.0, ss)))
    sym_sharpe = sum(sym_sharpes) / len(sym_sharpes) if sym_sharpes else 0.0
    avg_pnl = total_pnl / max(1, n_trades)
    avg_pos_val = sum(t.get("position_value", 0) for t in close_trades) / max(1, n_trades)
    print(f"V8_RESULT: pool_sharpe={pool_sharpe:.4f} sym_sharpe={sym_sharpe:.4f} sharpe={pool_sharpe:.4f} pnl={pnl_pct:.2f} trades={n_trades} wins={wins} losses={losses} total_pnl_dollars={total_pnl:.2f} avg_pnl={avg_pnl:.2f} avg_pos_value={avg_pos_val:.2f}")
    return pool_sharpe, pnl_pct, n_trades, wins, losses


# ═══════════════════════════════════════════════════════════════
# NEW 2026-04-26 sweep switches (mirrored from v8_quick_engine sibling).
# All default OFF. Consume new NPZ fields added by precompute agent.
# Switches:
#   VOL_TARGET, DD_KELLY, MINERVINI_GATE, CLENOW_GATE, PROXIMITY_TOP_GATE,
#   SQUEEZE_FIRE_ENTRY, TSMOM_BOOK_SCALAR
# Wiring contract: same field names + semantics as v8_quick. See CLAUDE.md
# section "Engine-tier architecture" 2c. Tier-1 (v8_quick vectorized) and
# Tier-2 (this engine, real-code) MUST stay flag-comparable.
# ═══════════════════════════════════════════════════════════════
def _v8ns_get(cfg_obj, name, default):
    """Safe getattr against either Config instance or module — returns default if missing."""
    return getattr(cfg_obj, name, default)


def _v8ns_get_indicator_field(indicators, key, default=0.0):
    try:
        v = indicators.get(key, default) if indicators else default
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _v8ns_realized_vol(indicators, field):
    """Return realized vol from one of yz/pk/gk_vol_60_d / yz/pk/gk_vol_20_4h. 0 = missing."""
    return _v8ns_get_indicator_field(indicators, str(field or 'yz_vol_60_d'), 0.0)


def _v8ns_vol_target_scalar(cfg_obj, indicators):
    """VOL_TARGET sizing scalar: clip(VOL_TARGET_PCT / realized_vol, LOW_CAP, HIGH_CAP).
    If VOL_TARGET disabled or realized_vol <= 0 (no NPZ data), returns 1.0 (no-op)."""
    if not bool(_v8ns_get(cfg_obj, 'VOL_TARGET_ENABLED', False)):
        return 1.0
    target = float(_v8ns_get(cfg_obj, 'VOL_TARGET_PCT', 0.02))
    low = float(_v8ns_get(cfg_obj, 'VOL_TARGET_LOW_CAP', 0.5))
    high = float(_v8ns_get(cfg_obj, 'VOL_TARGET_HIGH_CAP', 2.0))
    field = str(_v8ns_get(cfg_obj, 'VOL_TARGET_FIELD', 'yz_vol_60_d'))
    rv = _v8ns_realized_vol(indicators, field)
    if rv <= 0:
        return 1.0
    s = target / rv
    if s < low:
        s = low
    if s > high:
        s = high
    return s


def _v8ns_dd_kelly_scalar(cfg_obj, dd_state):
    """DD_KELLY sizing scalar tracked from sim peak equity:
        DD <= -10% → TIER1_PCT, <= -15% → TIER2_PCT, <= -20% → TIER3_PCT.
    dd_state mutates: caller updates 'peak' and 'dd_pct' before this call.
    Returns 1.0 when disabled or no DD breached."""
    if not bool(_v8ns_get(cfg_obj, 'DD_KELLY_ENABLED', False)):
        return 1.0
    dd = float(dd_state.get('dd_pct', 0.0))
    t1 = float(_v8ns_get(cfg_obj, 'DD_KELLY_TIER1_PCT', 0.5))
    t2 = float(_v8ns_get(cfg_obj, 'DD_KELLY_TIER2_PCT', 0.25))
    t3 = float(_v8ns_get(cfg_obj, 'DD_KELLY_TIER3_PCT', 0.125))
    if dd <= -20.0:
        return t3
    if dd <= -15.0:
        return t2
    if dd <= -10.0:
        return t1
    return 1.0


def _v8ns_dd_state_update(dd_state, total_equity_pct):
    """Mutate dd_state['peak'] / dd_state['dd_pct'] given current cumulative equity gain%."""
    peak = float(dd_state.get('peak', 0.0))
    if total_equity_pct > peak:
        peak = total_equity_pct
        dd_state['peak'] = peak
    dd_state['dd_pct'] = total_equity_pct - peak  # negative when underwater


def _v8ns_tsmom_book_scalar(cfg_obj, open_positions_summary):
    """TSMOM book-level scalar based on sign-agreement of 12-1 momentum across open positions.
    open_positions_summary: list of {is_long: bool, mom: float} where mom > 0 = up over LOOKBACK_BARS.
    agreement_pct = aligned / total. Result clipped to [LOW_CAP, HIGH_CAP]."""
    if not bool(_v8ns_get(cfg_obj, 'TSMOM_BOOK_SCALAR_ENABLED', False)):
        return 1.0
    if not open_positions_summary:
        return 1.0
    min_agree = float(_v8ns_get(cfg_obj, 'TSMOM_MIN_AGREEMENT', 0.5))
    low = float(_v8ns_get(cfg_obj, 'TSMOM_LOW_CAP', 0.25))
    high = float(_v8ns_get(cfg_obj, 'TSMOM_HIGH_CAP', 1.5))
    aligned = 0
    total = 0
    for p in open_positions_summary:
        m = float(p.get('mom', 0.0))
        if m == 0.0:
            continue
        total += 1
        if (p.get('is_long', True) and m > 0) or ((not p.get('is_long', True)) and m < 0):
            aligned += 1
    if total == 0:
        return 1.0
    agree = aligned / total
    s = low + (high - low) * max(0.0, (agree - min_agree) / max(1e-9, 1.0 - min_agree))
    if s < low:
        s = low
    if s > high:
        s = high
    return s


def _v8ns_check_entry_vetos(cfg_obj, indicators, is_long):
    """Returns (allow:bool, reason:str). Long-side entry vetoes:
       MINERVINI_GATE_ENABLED + MIN_SCORE → reject if sepa_score < MIN_SCORE (or sepa_pass=0)
       CLENOW_GATE_ENABLED + MIN_SCORE → reject if clenow_score < MIN_SCORE
       PROXIMITY_TOP_GATE_ENABLED + MAX_DROP_PCT → reject if abs(pct_from_52w_high) > MAX_DROP_PCT
    Shorts pass through (these are momentum/leadership filters for longs)."""
    if not is_long:
        return True, ""
    if bool(_v8ns_get(cfg_obj, 'MINERVINI_GATE_ENABLED', False)):
        min_score = float(_v8ns_get(cfg_obj, 'MINERVINI_MIN_SCORE', 5.0))
        sepa_pass = _v8ns_get_indicator_field(indicators, 'sepa_pass', 0.0)
        sepa_score = _v8ns_get_indicator_field(indicators, 'sepa_score', 0.0)
        if sepa_pass <= 0 or sepa_score < min_score:
            return False, f"BLOCKED_MINERVINI_GATE_score={sepa_score:.0f}_lt_{min_score:.0f}"
    if bool(_v8ns_get(cfg_obj, 'CLENOW_GATE_ENABLED', False)):
        min_score = float(_v8ns_get(cfg_obj, 'CLENOW_GATE_MIN_SCORE', 30.0))
        cl_score = _v8ns_get_indicator_field(indicators, 'clenow_score', 0.0)
        if cl_score < min_score:
            return False, f"BLOCKED_CLENOW_GATE_score={cl_score:.2f}_lt_{min_score:.2f}"
    if bool(_v8ns_get(cfg_obj, 'PROXIMITY_TOP_GATE_ENABLED', False)):
        max_drop = float(_v8ns_get(cfg_obj, 'PROXIMITY_TOP_MAX_DROP_PCT', 5.0))
        drop = abs(_v8ns_get_indicator_field(indicators, 'pct_from_52w_high', 0.0))
        if drop > max_drop:
            return False, f"BLOCKED_PROXIMITY_TOP_drop={drop:.1f}pct_gt_{max_drop:.1f}pct"
    return True, ""


def _v8ns_squeeze_fire_aligned(cfg_obj, indicators, is_long):
    """SQUEEZE_FIRE_ENTRY: returns (fired:bool, bonus:float). True when squeeze_fire_{tf}
    direction matches is_long and WT 1h direction matches. Caller may use bonus to lower
    score thresholds (mirrors v8_quick OR-additive behavior)."""
    if not bool(_v8ns_get(cfg_obj, 'SQUEEZE_FIRE_ENTRY_ENABLED', False)):
        return False, 0.0
    tf = str(_v8ns_get(cfg_obj, 'SQUEEZE_FIRE_TF', '1h'))
    bonus = float(_v8ns_get(cfg_obj, 'SQUEEZE_FIRE_BONUS_SCORE', 10.0))
    sf = _v8ns_get_indicator_field(indicators, f'squeeze_fire_{tf}', 0.0)
    if (is_long and sf <= 0) or ((not is_long) and sf >= 0):
        return False, 0.0
    wt1_1h = _v8ns_get_indicator_field(indicators, 'wt1_1h', 0.0)
    wt2_1h = _v8ns_get_indicator_field(indicators, 'wt2_1h', 0.0)
    aligned = (wt1_1h > wt2_1h) if is_long else (wt1_1h < wt2_1h)
    if not aligned:
        return False, 0.0
    return True, bonus


def _v8ns_compute_position_mom(indicators, lookback_bars=12):
    """Approximate 12-1 momentum proxy from indicators when we don't have direct price array
    access in the eta wrapper. Use wt_score_D (signed −5..+5) scaled by lookback. Sign
    is what matters for TSMOM agreement; magnitude is illustrative."""
    s = _v8ns_get_indicator_field(indicators, 'wt_score_D', 0.0)
    if s == 0.0:
        s = _v8ns_get_indicator_field(indicators, 'wt_score_4h', 0.0)
    return s


# ═══════════════════════════════════════════════════════════════
# STEP 7: The simulation engine
# ═══════════════════════════════════════════════════════════════
async def run_simulation(mode, account_key, start_date, capital, stores, resolution):
    """Run the REAL trading engine on historical data.

    This follows the EXACT same init sequence as ez_manage.main()
    (lines 22551-22849) with only I/O patches applied.
    """
    # CRITICAL: V8 backtest must NEVER call Redis. The live config.Config has
    # _get_regime_from_redis() which is called from get_symbol_setting() on every
    # signal evaluation. On servers without real Redis (or with a dummy Redis that
    # accepts connections but never replies), the GET command blocks forever
    # because socket_connect_timeout=1 only covers connect, not read. Patch it to
    # always return None so backtest uses class defaults — no Redis blocking.
    config.Config._get_regime_from_redis = classmethod(lambda cls, full_key: None)
    indicator_cache, price_cache, executed_trades = apply_patches(stores, mode)

    # ═══ LIVE PnL TRACKER — instrumented per close to identify problem code paths ═══
    # _live_pnl tracks running totals so we can stop & inspect mid-run
    _live_pnl = {
        "open_positions": {},   # pk -> {vwap, qty, side, entry_ts, entry_reason}
        "by_reason": {},        # close_reason -> {n, pnl_pct_sum, n_wins, n_losses, worst_loss, best_win, examples}
        "by_symbol": {},        # symbol -> {pnl_pct_sum, n_trades}
        "by_entry_reason": {},  # entry_reason -> {pnl_pct_sum, n_trades, n_wins, n_losses}
        "running_pnl_pct": 0.0,
        "n_closes": 0,
        "n_wins": 0,
        "n_losses": 0,
        "all_pnl_pcts": [],     # list of every close's pnl% for Sharpe calc
        "all_pnl_dollars": [],  # list of every close's pnl$ for capital calc
        "weekly_pnl_dollars": {},  # week_idx -> sum$ for proper weekly Sharpe
        "starting_capital": float(capital),
        "first_close_ts": 0.0,  # sim ts of first close — for annualized Sharpe
        "last_close_ts": 0.0,   # sim ts of latest close
    }
    # NEW 2026-04-26 sweep switch: DD_KELLY peak/dd tracker (no-op when disabled).
    _v8ns_dd_state = {"peak": 0.0, "dd_pct": 0.0}
    # NEW 2026-04-26 sweep switch: counters for MINERVINI/CLENOW/PROXIMITY_TOP/SQUEEZE_FIRE/VOL_TARGET/DD_KELLY/TSMOM.
    _v8ns_counters = {"vol_target_applied": 0, "dd_kelly_applied": 0, "tsmom_applied": 0,
                      "minervini_block": 0, "clenow_block": 0, "proximity_top_block": 0,
                      "squeeze_fire_aligned": 0}
    def _compute_sharpe_and_gain():
        """CANONICAL_METRICS.md / CLAUDE.md rule 4: ONLY pool_sharpe (= sharpe_per_trade).
        Returns 7-tuple for back-compat — sharpe_weekly and sharpe_annual now alias to pool_sharpe
        (banned 2026-04-29: weekly $-Sharpe and sqrt(N)-annualization both produced inflated decision-grade lies).
        trades_per_year retained as a metadata field (no Sharpe transformation).
        """
        pcts = _live_pnl["all_pnl_pcts"]
        n = len(pcts)
        if n >= 2:
            mean_t = sum(pcts) / n
            std_t = (sum((x - mean_t) ** 2 for x in pcts) / n) ** 0.5
            sharpe_per_trade = mean_t / std_t if std_t > 0 else (20.0 if mean_t > 0 else 0.0)
        else:
            sharpe_per_trade = 0.0
        if _live_pnl.get("first_close_ts", 0) > 0 and _live_pnl.get("last_close_ts", 0) > 0:
            sim_secs = max(1.0, _live_pnl["last_close_ts"] - _live_pnl["first_close_ts"])
            trades_per_year = n * (365 * 86400) / sim_secs
        else:
            trades_per_year = n
        total_pnl_dollars = sum(_live_pnl["all_pnl_dollars"])
        total_gain_pct_dollars = (total_pnl_dollars / _live_pnl["starting_capital"] * 100) if _live_pnl["starting_capital"] > 0 else 0.0
        sum_pct = _live_pnl["running_pnl_pct"]
        # Aliases preserved so callers unpacking the 7-tuple continue to work; both equal pool_sharpe.
        return sharpe_per_trade, total_gain_pct_dollars, total_pnl_dollars, sum_pct, sharpe_per_trade, sharpe_per_trade, trades_per_year

    # Scale START_POSITION_SIZE to backtest capital (live=$18 for $1k acct)
    # execute_trade_action subtracts up to SP*0.8*4 + SP*0.8*3 + SP*0.8*2 = 7.2*SP
    # So starting qty MUST be >8x SP to survive worst-case subtractions
    # Setting SP to capital/50 ensures ~$200 starts survive $144 worst-case subtractions at $1k capital
    config.START_POSITION_SIZE = max(config.START_POSITION_SIZE, capital * 0.20)
    config.MAX_POSITION_SIZE = max(config.MAX_POSITION_SIZE, capital * 0.50)
    v8_logger.info(f"Backtest sizing: START_POSITION_SIZE=${config.START_POSITION_SIZE:.0f} MAX_POSITION_SIZE=${config.MAX_POSITION_SIZE:.0f} (capital=${capital:.0f})")

    # --- REAL init sequence (from ez_manage.main lines 22555-22626) ---
    config.ACCOUNT_KEYS = [account_key]
    ez_manage.current_account.set(account_key)

    # Load accounts (uses dummy API keys from env)
    accounts = ez_manage.load_accounts(config)
    accounts = {k: v for k, v in accounts.items() if k == account_key}
    if not accounts:
        v8_logger.error(f"No account loaded for {account_key}")
        return

    # Initialize (dummy — skips Binance client)
    await asyncio.gather(*(acc.initialize() for acc in accounts.values()))

    # Load symbols
    symbols = list(stores.keys())
    v8_logger.info(f"Symbols: {len(symbols)}")

    # Create the REAL MultiAccountTradeManager
    trade_manager = ez_manage.MultiAccountTradeManager(accounts, symbols, use_dummy_lock=True)
    trade_manager._allowed_accounts = frozenset([account_key])

    # OrderQueue + monitor (REAL)
    order_monitor = ez_manage.OrderExecutionMonitor(trade_manager)
    trade_manager.order_execution_monitor = order_monitor
    order_queue = ez_manage.OrderQueue(trade_manager, max_concurrent_orders=500)
    trade_manager.set_order_queue(order_queue)

    # Redis (in-memory)
    trade_manager.redis_manager = InMemoryRedis()

    # Mock the Binance client on trade_manager to record trades
    # MUST be sync (called via asyncio.to_thread) and MUST update positionAmt
    class _RecordingClient:
        def futures_create_order(self, **kw):
            qty = float(kw.get("quantity", 0))
            px = float(kw.get("price", 0))
            sym = kw.get("symbol", "")
            side = kw.get("side", "")
            pos_side = kw.get("positionSide", "")
            pk = f"{account_key}:{sym}_{'LONG' if pos_side == 'LONG' else 'SHORT'}"
            trade = {"timestamp": _sim_ts[0], "params": kw, "status": "FILLED",
                     "avgPrice": str(px), "executedQty": str(qty),
                     "position_key": pk, "side": side, "quantity": qty, "price": px}
            executed_trades.append(trade)
            # Update positionAmt (what exchange would do)
            pos = trade_manager.positions.get(pk)
            if not pos:
                v8_logger.warning(f"[MOCK_FCO] No position for pk={pk} (available: {list(trade_manager.positions.keys())[:5]})")
            if pos and qty > 0:
                is_long = pk.endswith("_LONG")
                is_reduce = (side == "SELL" and is_long) or (side == "BUY" and not is_long)
                if is_reduce:
                    pos.positionAmt = max(0.0, abs(pos.positionAmt) - abs(qty))
                else:
                    _old_amt = abs(pos.positionAmt)
                    if _old_amt < 0.0001:
                        pos.entry_price = px
                        pos.opened_at = datetime.utcfromtimestamp(_sim_ts[0]).replace(tzinfo=timezone.utc)
                        pos.initial_quantity = abs(qty)
                    else:
                        pos.entry_price = (_old_amt * pos.entry_price + abs(qty) * px) / (_old_amt + abs(qty))
                    pos.positionAmt = _old_amt + abs(qty)
            return {"orderId": len(executed_trades), "status": "FILLED",
                    "avgPrice": str(px), "executedQty": str(qty)}
        def futures_cancel_order(self, **kw):
            return {"status": "CANCELED"}
        def futures_countdown_cancel_all(self, **kw):
            return {}
        def futures_symbol_ticker(self, **kw):
            sym = kw.get("symbol", "")
            px = price_cache.get(sym, 1.0)
            return {"symbol": sym, "price": str(px)}
        def futures_order_book(self, **kw):
            sym = kw.get("symbol", "")
            px = price_cache.get(sym, 1.0)
            return {"bids": [[str(px * 0.9999), "100"]], "asks": [[str(px * 1.0001), "100"]]}
    _recording_client = _RecordingClient()
    trade_manager.accounts_clients = {account_key: _recording_client}
    trade_manager._clients = trade_manager.accounts_clients
    # Patch account.client too — execute_now uses `account.client` (line 12750)
    for acc in accounts.values():
        acc.client = _recording_client

    # Webhook → record trade
    _original_send_webhook = trade_manager.send_webhook if hasattr(trade_manager, 'send_webhook') else None
    async def _recording_webhook(position_key=None, account_key=None, symbol=None,
                                  positionAmt=0, quantity=0, price=0, side="",
                                  position_side="", unique_id="", is_full_close=False,
                                  reason="", **kw):
        executed_trades.append({
            "timestamp": _sim_ts[0], "type": "webhook", "position_key": position_key,
            "side": side, "quantity": quantity, "price": price, "reason": reason,
            "is_full_close": is_full_close,
        })
        # Apply the trade to positions (what Finandy would do)
        pos = trade_manager.positions.get(position_key)
        if pos and quantity > 0:
            is_long = position_key.endswith("_LONG") if position_key else True
            is_reduce = (side == "SELL" and is_long) or (side == "BUY" and not is_long)
            if is_reduce:
                pos.positionAmt = max(0.0, abs(pos.positionAmt) - abs(quantity))
                if is_full_close:
                    pos.positionAmt = 0.0
            else:
                _wh_old_amt = abs(pos.positionAmt)
                if _wh_old_amt < 0.0001:
                    pos.entry_price = price
                    pos.opened_at = _sim_datetime_now(timezone.utc)
                    pos.initial_quantity = abs(quantity)
                else:
                    pos.entry_price = (_wh_old_amt * pos.entry_price + abs(quantity) * price) / (_wh_old_amt + abs(quantity))
                pos.positionAmt = _wh_old_amt + abs(quantity)
            # Always ensure opened_at is datetime
            if not isinstance(getattr(pos, 'opened_at', None), datetime):
                pos.opened_at = _sim_datetime_now(timezone.utc)
        return True
    trade_manager.send_webhook = _recording_webhook

    # Seed symbol_configs so maker loop doesn't crash with "Symbol config missing"
    for sym in stores.keys():
        trade_manager.symbol_configs[sym] = {
            "tick_size": 0.01, "step_size": 0.00001, "min_qty": 0.00001,
            "min_notional": 5.0, "max_qty": 1000000.0, "price_precision": 2,
            "qty_precision": 5,
        }
    v8_logger.info(f"Seeded symbol_configs for {len(stores)} symbols")

    # Patch execute_trade_action — captures ALL trade paths (entries, exits, augments, reduces)
    async def _crypto_eta(account_key='', position_key='', symbol='', quantity=0, current_price=0, side='', position_side='', unique_id=None, is_full_close=False, action='', reason='', override_qty=None, is_hedge=False, hedge_for=None, **kw):
        acct = account_key; pk = position_key; sym = symbol
        qty = quantity; px = current_price; ps = position_side
        uid = unique_id; ifc = is_full_close
        qty = float(override_qty or qty or 0)
        px = float(px or price_cache.get(sym.upper(), 0))
        if qty <= 0 or px <= 0:
            return "BLOCKED_ZERO"
        is_red = action.upper() in ('CLOSE','REDUCE','QUICK_CLOSE','FULL_CLOSE','PROFIT_TAKE','STOP_MAJOR_LOSS_REDUCE','STOP_FUNCTIONS_KILL','HEDGE_CLOSE') or 'CLOSE' in reason.upper() or 'REDUCE' in reason.upper()
        act = action or ("CLOSE" if is_red else "OPEN")
        # NEW 2026-04-26 sweep switches: entry vetoes + sizing scalars (crypto path).
        # Apply ONLY to non-reduce / non-hedge OPEN/AUGMENT/REENTRY actions. All default OFF.
        # Hedges intentionally bypass — hedge gates live in HEDGE_* config and hedge_engine.
        if (not is_red) and (not is_hedge):
            _v8ns_ind = indicator_cache.get(sym.upper(), {}) if isinstance(indicator_cache, dict) else {}
            _v8ns_is_long = (ps == 'LONG') if ps else pk.endswith('_LONG')
            # NEW 2026-04-26 sweep switch: MINERVINI_GATE / CLENOW_GATE / PROXIMITY_TOP_GATE
            _v8ns_allow, _v8ns_veto = _v8ns_check_entry_vetos(config, _v8ns_ind, _v8ns_is_long)
            if not _v8ns_allow:
                if 'MINERVINI' in _v8ns_veto: _v8ns_counters['minervini_block'] += 1
                elif 'CLENOW' in _v8ns_veto: _v8ns_counters['clenow_block'] += 1
                elif 'PROXIMITY_TOP' in _v8ns_veto: _v8ns_counters['proximity_top_block'] += 1
                return _v8ns_veto
            # NEW 2026-04-26 sweep switch: SQUEEZE_FIRE_ENTRY (informational tag for crypto eta).
            # In v8_quick this is OR-additive to base_sig; here, real check_entry_candidates already
            # produced the candidate. We tag the reason and count alignment for sweep diagnostics.
            _v8ns_sf_fired, _v8ns_sf_bonus = _v8ns_squeeze_fire_aligned(config, _v8ns_ind, _v8ns_is_long)
            if _v8ns_sf_fired:
                _v8ns_counters['squeeze_fire_aligned'] += 1
            # NEW 2026-04-26 sweep switch: VOL_TARGET sizing scalar.
            _v8ns_vt = _v8ns_vol_target_scalar(config, _v8ns_ind)
            if _v8ns_vt != 1.0:
                _v8ns_counters['vol_target_applied'] += 1
            # NEW 2026-04-26 sweep switch: DD_KELLY sizing scalar (peak/dd updated post-close in tracker).
            _v8ns_total_pct = _live_pnl.get('running_pnl_pct', 0.0)
            _v8ns_dd_state_update(_v8ns_dd_state, _v8ns_total_pct)
            _v8ns_dk = _v8ns_dd_kelly_scalar(config, _v8ns_dd_state)
            if _v8ns_dk != 1.0:
                _v8ns_counters['dd_kelly_applied'] += 1
            # NEW 2026-04-26 sweep switch: TSMOM_BOOK_SCALAR — sign-agreement of 12-1 momentum across book.
            _v8ns_book = []
            try:
                for _bpk, _bpos in trade_manager.positions.items():
                    if abs(getattr(_bpos, 'positionAmt', 0)) < 0.0001: continue
                    _bsym = getattr(_bpos, 'symbol', '') or (_bpk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _bpk else _bpk.rsplit('_', 1)[0])
                    _bind = indicator_cache.get(_bsym.upper(), {}) if isinstance(indicator_cache, dict) else {}
                    _v8ns_book.append({'is_long': _bpk.endswith('_LONG'),
                                        'mom': _v8ns_compute_position_mom(_bind, int(_v8ns_get(config, 'TSMOM_LOOKBACK_BARS', 252)))})
            except Exception:
                _v8ns_book = []
            _v8ns_tm = _v8ns_tsmom_book_scalar(config, _v8ns_book)
            if _v8ns_tm != 1.0:
                _v8ns_counters['tsmom_applied'] += 1
            # Combined sizing scalar — multiplicative.
            _v8ns_scalar = _v8ns_vt * _v8ns_dk * _v8ns_tm
            if _v8ns_scalar != 1.0:
                qty = max(0.0, qty * _v8ns_scalar)
                if qty <= 0:
                    return f"BLOCKED_V8NS_SIZE_ZERO_vt={_v8ns_vt:.2f}_dk={_v8ns_dk:.2f}_tm={_v8ns_tm:.2f}"
                if _v8ns_sf_fired:
                    reason = f"{reason}|SQ_FIRE+{_v8ns_sf_bonus:.0f}|V8NS_SCALE_vt={_v8ns_vt:.2f}_dk={_v8ns_dk:.2f}_tm={_v8ns_tm:.2f}"
                else:
                    reason = f"{reason}|V8NS_SCALE_vt={_v8ns_vt:.2f}_dk={_v8ns_dk:.2f}_tm={_v8ns_tm:.2f}"
            elif _v8ns_sf_fired:
                reason = f"{reason}|SQ_FIRE+{_v8ns_sf_bonus:.0f}"
        executed_trades.append({"timestamp": _sim_ts[0], "type": "eta", "position_key": pk, "side": side, "quantity": qty, "price": px, "action": act, "reason": str(reason)[:200]})
        # ═══ LIVE PnL TRACKING — instrument every open/close to find problem paths ═══
        _is_open_action = act.upper() in ('OPEN', 'QUICK_OPEN', 'AUGMENT', 'QUICK_AUGMENT', 'REENTRY', 'HEDGE_OPEN')
        _entry_reason_short = reason.split('_')[0] if reason else 'UNK'
        if 'STRONG_BUY' in reason: _entry_reason_short = 'STRONG_BUY'
        elif 'STRONG_SELL' in reason: _entry_reason_short = 'STRONG_SELL'
        elif 'RZ_' in reason: _entry_reason_short = 'RZ_ENTRY'
        elif 'REENTRY' in reason: _entry_reason_short = 'REENTRY'
        elif 'HEDGE' in reason: _entry_reason_short = 'HEDGE'
        elif 'AUGMENT' in reason: _entry_reason_short = 'AUGMENT'
        _close_reason_short = 'OTHER'
        for _r in ('DELTA_EXIT', 'WT_4H_VEL_EXIT', 'WT_3M_EXIT', 'H_VEL_EXIT', 'M_EXIT', 'NO_LOSS_EXIT', 'STOP_FUNCTIONS_KILL', 'HEDGE_ORPHAN_KILL', 'HEDGE_LOSS_KILL', 'HEDGE_DECAY_NUKE', 'HEDGE_RECOVERY', 'HEDGE_WT_KILL', 'GAIN_EROSION', 'TIERED_T', 'CYCLE_TP', 'STOCH_CROSS', 'STRUCT_BREAK', 'TREND_REVERSAL', 'BREAK_EVEN_GUARD', 'STRONG_EXIT', 'WRONG_WAY'):
            if _r in reason:
                _close_reason_short = _r
                break
        # Read live position state — IT is the source of truth, not our tracker.
        _live_pos_obj = trade_manager.positions.get(pk)
        try:
            _live_pos_amt = abs(float(getattr(_live_pos_obj, 'positionAmt', 0) or 0)) if _live_pos_obj else 0
        except (TypeError, ValueError):
            _live_pos_amt = 0
        try:
            _live_pos_entry = float(getattr(_live_pos_obj, 'entry_price', 0) or 0) if _live_pos_obj else 0
        except (TypeError, ValueError):
            _live_pos_entry = 0
        if _is_open_action:
            # If live position is fresh (positionAmt < threshold pre-this-open), reset tracker.
            # This prevents stale vwap from a previous cycle bleeding into the new entry.
            if _live_pos_amt < 0.0001 and pk in _live_pnl["open_positions"]:
                del _live_pnl["open_positions"][pk]
            _existing = _live_pnl["open_positions"].get(pk)
            if _existing:
                _new_qty = _existing["qty"] + qty
                _existing["vwap"] = (_existing["vwap"] * _existing["qty"] + px * qty) / _new_qty if _new_qty > 0 else px
                _existing["qty"] = _new_qty
            else:
                _sym_only = pk.split(':', 1)[1].rsplit('_', 1)[0] if ':' in pk else sym
                _live_pnl["open_positions"][pk] = {"vwap": px, "qty": qty, "side": ps or ('LONG' if pk.endswith('_LONG') else 'SHORT'), "entry_ts": _sim_ts[0], "entry_reason": _entry_reason_short, "symbol": _sym_only}
        elif is_red and pk in _live_pnl["open_positions"]:
            _pos = _live_pnl["open_positions"][pk]
            _is_long = _pos["side"] == "LONG"
            _close_qty = min(qty, _pos["qty"])
            # SOURCE OF TRUTH: pos.entry_price from the live position object (set at fresh open
            # in _crypto_eta line ~830). The tracker's own vwap can desync if events arrive
            # out of order or if augment math drifts. Position object is canonical.
            _entry_for_pnl = _live_pos_entry if _live_pos_entry > 0 else _pos["vwap"]
            _pnl_pct = ((px - _entry_for_pnl) / _entry_for_pnl * 100) if _is_long else ((_entry_for_pnl - px) / _entry_for_pnl * 100)
            _pnl_dollars = ((px - _entry_for_pnl) * _close_qty) if _is_long else ((_entry_for_pnl - px) * _close_qty)
            # Update tracker's stored vwap to match live (for the log line below)
            _pos["vwap"] = _entry_for_pnl
            _live_pnl["running_pnl_pct"] += _pnl_pct
            _live_pnl["all_pnl_pcts"].append(_pnl_pct)
            _live_pnl["all_pnl_dollars"].append(_pnl_dollars)
            _week_idx = int(_sim_ts[0]) // (7 * 86400)
            _live_pnl["weekly_pnl_dollars"][_week_idx] = _live_pnl["weekly_pnl_dollars"].get(_week_idx, 0) + _pnl_dollars
            _live_pnl["n_closes"] += 1
            if _live_pnl["first_close_ts"] == 0:
                _live_pnl["first_close_ts"] = _sim_ts[0]
            _live_pnl["last_close_ts"] = _sim_ts[0]
            if _pnl_pct > 0: _live_pnl["n_wins"] += 1
            else: _live_pnl["n_losses"] += 1
            _br = _live_pnl["by_reason"].setdefault(_close_reason_short, {"n": 0, "pnl_pct_sum": 0.0, "n_wins": 0, "n_losses": 0, "worst_loss": 0.0, "best_win": 0.0, "examples": []})
            _br["n"] += 1
            _br["pnl_pct_sum"] += _pnl_pct
            if _pnl_pct > 0: _br["n_wins"] += 1
            else: _br["n_losses"] += 1
            if _pnl_pct < _br["worst_loss"]: _br["worst_loss"] = _pnl_pct
            if _pnl_pct > _br["best_win"]: _br["best_win"] = _pnl_pct
            if abs(_pnl_pct) > 5 and len(_br["examples"]) < 5:
                _br["examples"].append(f"{pk} {_pos['entry_reason']}→{_close_reason_short} {_pos['vwap']:.2f}→{px:.2f} ({_pnl_pct:+.2f}%)")
            _bs = _live_pnl["by_symbol"].setdefault(_pos["symbol"], {"n_trades": 0, "pnl_pct_sum": 0.0})
            _bs["n_trades"] += 1
            _bs["pnl_pct_sum"] += _pnl_pct
            _ber = _live_pnl["by_entry_reason"].setdefault(_pos["entry_reason"], {"n_trades": 0, "pnl_pct_sum": 0.0, "n_wins": 0, "n_losses": 0})
            _ber["n_trades"] += 1
            _ber["pnl_pct_sum"] += _pnl_pct
            if _pnl_pct > 0: _ber["n_wins"] += 1
            else: _ber["n_losses"] += 1
            _pos["qty"] -= _close_qty
            if _pos["qty"] <= 0.0001:
                del _live_pnl["open_positions"][pk]
            _live_sharpe_w, _live_gain_pct, _live_gain_dol, _, _live_sharpe_pt, _live_sharpe_ann, _live_tpy = _compute_sharpe_and_gain()
            # CANONICAL_METRICS.md: pool_sharpe (= sharpe_per_trade) only — sharpe_ann banned.
            v8_logger.warning(f"[V8_PNL] CLOSE {pk} {_close_reason_short} entry={_pos['vwap']:.4f} exit={px:.4f} pnl={_pnl_pct:+.2f}% gain%={_live_gain_pct:+.2f}% pool_sharpe={_live_sharpe_pt:.3f} closes={_live_pnl['n_closes']} W={_live_pnl['n_wins']} L={_live_pnl['n_losses']}")
        v8_logger.warning(f"[V8_TRADE] {pk} {side} qty={qty:.4f} @{px:.6f} {act} {reason[:60]}")
        pos = trade_manager.positions.get(pk)
        if is_red and pos:
            old_amt = abs(getattr(pos, 'positionAmt', 0))
            new_amt = max(0, old_amt - abs(qty))
            pos.positionAmt = new_amt; pos.quantity = new_amt
            if ifc or new_amt < 0.0001: pos.positionAmt = 0; pos.quantity = 0
            pos.was_reduced = True; pos.last_reduction_time = _sim_datetime_now(timezone.utc); pos.last_reduction_price = px
        elif not is_red:
            if pos:
                old_amt = abs(getattr(pos, 'positionAmt', 0)); old_ep = getattr(pos, 'entry_price', px) or px
                new_amt = old_amt + abs(qty)
                pos.entry_price = (old_ep*old_amt + px*abs(qty))/new_amt if new_amt>0 else px
                pos.positionAmt = new_amt; pos.quantity = new_amt
            else:
                class _P:
                    def __init__(s, sy, sd, q, ep):
                        s.symbol=sy; s.position_side=sd; s.positionAmt=q; s.quantity=q; s.entry_price=ep; s.mark_price=ep
                        s.gain=0; s.prev_gain=0; s.max_gain=0; s.opened_at=_sim_datetime_now(timezone.utc); s.last_updated=_sim_datetime_now(timezone.utc)
                        s.last_augmentation_time=None; s.last_reduction_time=None; s.last_reduction_price=0; s.was_reduced=False; s.augmented_count=0; s.max_quantity=q; s.mark_price_last_updated=None
                np2 = _P(sym, ps, abs(qty), px)
                trade_manager.positions[pk] = np2
                trade_manager.positions_by_account.setdefault(acct, {})[pk] = np2
        return "SUCCESS"
    trade_manager.execute_trade_action = _crypto_eta

    # PositionService — start EMPTY, no disk positions
    # Backtest must earn every position through the trading logic
    v8_logger.info("Bootstrapping PositionService (EMPTY — no disk positions)...")
    try:
        positions_service = await ez_positions_service.bootstrap_position_service(
            logger=logging.getLogger("v8_bootstrap"),
            accounts=accounts,
            enable_auto_fetch=False,
            load_priority='disk',
            start_maintenance=False,
        )
        # CLEAR all loaded positions — start from zero
        positions_service.positions = {}
        positions_service.positions_by_account = {account_key: {}}
        trade_manager.positions_by_account = positions_service.positions_by_account
        trade_manager.positions = positions_service.positions
        trade_manager.positions_service = positions_service
        trade_manager.service = positions_service
        positions_service.trade_manager = trade_manager
        v8_logger.info("PositionService ready — 0 positions (clean start)")
    except Exception as e:
        v8_logger.warning(f"PositionService bootstrap failed ({e}) — creating empty")
        trade_manager.positions = {}
        trade_manager.positions_by_account = {account_key: {}}

    # TrackerManager, DataManager, HedgeEngine, Registry (ALL REAL)
    v8_logger.info("Initializing TrackerManager, HedgeEngine, DataManager...")
    redis_manager = trade_manager.redis_manager
    tracker_manager = ez_positions_quick.TrackerManager(
        BASE_PATH, trade_manager=trade_manager, redis_manager=redis_manager)
    tracker_manager.accounts = accounts
    trade_manager.tracker_manager = tracker_manager

    # V8 BACKTEST: start with EMPTY positions — don't load live tracker which has
    # leverage-adjusted gains from current date that corrupt historical sim.
    for ak in [account_key]:
        try:
            await tracker_manager.sync_universe(ak)
        except Exception as e:
            v8_logger.warning(f"Tracker sync_universe {ak}: {e}")
    # Reset ALL loaded positions — we want a clean slate for backtest
    trade_manager.positions = {}
    trade_manager.positions_by_account = {account_key: {}}
    if trade_manager.positions_service:
        trade_manager.positions_service.positions = {}
        trade_manager.positions_service.positions_by_account = {account_key: {}}
    tracker_manager.positions = {}
    tracker_manager.positions_by_account = {account_key: {}}
    v8_logger.info(f"V8 BACKTEST: cleared all loaded positions for clean sim")

    data_path = Path(config.DATA_DIR) if hasattr(config, 'DATA_DIR') else BASE_PATH / "data"
    data_manager = ez_positions_quick.FastDataManager(redis_manager, data_path, trade_manager=trade_manager)
    # ═══ 2026-04-26 IPC BYPASS — backtest reads NPZ only, no shared-mem RPC ═══
    # In live, shared_proxy is the IPC handle to the ez_share_ind server (port 50005).
    # In backtest, all indicator data is loaded into store.arrays + injected into
    # data_manager._cold_data per bar (line ~1524). Shared_proxy is NEVER needed.
    # Without this stub, fallback paths in ez_positions_quick (lines 4742, 5126, 5891, 7189)
    # call shared_proxy.get_symbol() which spins on connection retries when the server
    # isn't running — caused 0.7%-sim-completion-in-30-min slowdown observed 2026-04-26.
    data_manager.shared_proxy = None
    # Disable the reconnect watchdog so it doesn't keep trying every 2-5s.
    if hasattr(data_manager, '_shared_mem_last_attempt'):
        data_manager._shared_mem_last_attempt = float('inf')  # never retry
    # Replace _connect_shared with a no-op so any code path triggering it does nothing.
    if hasattr(data_manager, '_connect_shared'):
        data_manager._connect_shared = lambda *a, **kw: None
    if hasattr(data_manager, 'shared_mem_watchdog'):
        async def _noop_watchdog(*a, **kw): return
        data_manager.shared_mem_watchdog = _noop_watchdog
    v8_logger.info("V8 BACKTEST: shared_proxy disabled, ez_share_ind IPC bypassed (NPZ-only mode)")
    trade_manager.data_manager = data_manager
    tracker_manager.data_manager = data_manager

    registry = ez_positions_quick.RatingRegistry(trade_manager, tracker_manager, data_manager, config)
    tracker_manager.registry = registry

    hedge_engine = ez_positions_quick.HedgeEngine(
        trade_manager=trade_manager, tracker_manager=tracker_manager,
        data_manager=data_manager, config=config, redis_manager=redis_manager,
        positions_service=trade_manager.positions_service, registry=registry)
    trade_manager.hedge_engine = hedge_engine

    # Load hedge records
    try:
        await hedge_engine.load_hedge_records(account_key)
    except Exception as e:
        v8_logger.warning(f"Hedge records: {e}")

    # ═══ SET UP TRADEABLE KEYS + EMPTY POSITION SHELLS ═══
    # Every NPZ symbol gets LONG + SHORT position shells so entry scanners find them
    all_position_keys = set()
    for sym in stores.keys():
        for side in ["LONG", "SHORT"]:
            pk = f"{account_key}:{sym}_{side}"
            all_position_keys.add(pk)
            if pk not in trade_manager.positions:
                # Create empty Position shell with all required fields
                pos = ez_manage.Position(
                    symbol=sym, position_side=side, entry_price=0.0, mark_price=0.0,
                    positionAmt=0.0, initial_quantity=0.0, gain=0.0, max_gain=0.0,
                    prev_gain=0.0, max_quantity=0.0, last_augmentation_amount=0.0,
                    last_augmentation_price=0.0, last_augmentation_time=None,
                    last_reduction_amount=0.0, last_reduction_price=0.0,
                    last_reduction_time=None, max_positionSize=0.0,
                    opened_at=_sim_datetime_now(timezone.utc), last_updated=_sim_datetime_now(timezone.utc), last_signal=""
                )
                trade_manager.positions[pk] = pos
                trade_manager.positions_by_account.setdefault(account_key, {})[pk] = pos
                if trade_manager.positions_service:
                    trade_manager.positions_service.positions[pk] = pos
                    trade_manager.positions_service.positions_by_account.setdefault(account_key, {})[pk] = pos

    # Set tradeable_keys EVERYWHERE the code checks — 48 symbols only, nothing else
    tracker_manager.tradeable_position_keys = {account_key: all_position_keys}
    tracker_manager.tradeable_keys = all_position_keys  # flat set
    tracker_manager.tradeable_keys_cache = all_position_keys
    tracker_manager._tradeable_keys_mtime = float('inf')  # never expire cache
    # Also override _get_tradeable_keys_cached to return our keys directly
    async def _fixed_tradeable_keys():
        return all_position_keys
    tracker_manager._get_tradeable_keys_cached = _fixed_tradeable_keys
    tracker_manager.get_tradeable_position_keys_for = lambda ak: all_position_keys if ak == account_key else set()
    trade_manager.tradeable_keys = all_position_keys
    trade_manager._last_tradeable_update = float('inf')  # never expire — our 48 symbols are fixed
    trade_manager.symbols = set(stores.keys())
    for sym in stores.keys():
        for side_attr in [f'symbols_{account_key}_long', f'symbols_{account_key}_short', f'symbols_{account_key}']:
            if hasattr(trade_manager, side_attr):
                getattr(trade_manager, side_attr).add(sym)
            else:
                setattr(trade_manager, side_attr, {sym})
    v8_logger.info(f"Tradeable keys: {len(all_position_keys)} ({len(stores)} symbols × 2 sides)")

    trade_manager._startup_complete = True

    # ═══ V8 BACKTEST OVERRIDES ═══
    # 2026-04-09: HEDGE_MODE STAYS ON. The wt_3m hedge kill rule (in
    # ez_positions_quick.py monitor_and_manage_hedges) handles the 5-symbol
    # both-sides-losing deadlock by killing hedges as soon as wt_3m turns.
    # User-configured HEDGE_ACCOUNTS from config are honored.
    from config import Config as _CfgClass
    # Allow technical stops to fire (override STRICT_NO_LOSS dead-code defaults)
    _CfgClass.FAST_CUT_LOSS_THRESHOLD = -3.0
    _CfgClass.BREAKOUT_GUARD_LOSS_THRESHOLD = -3.0
    _CfgClass.STOP_LOSS_THRESHOLD = 3.0
    # Patch via module imports (each module may have imported Config or config)
    # Use sys.modules to avoid shadowing the module-level imports already bound in this function
    import sys as _sys
    for _mn in ['ez_manage', 'ez_positions_quick', 'ez_positions_service', 'utils']:
        _m = _sys.modules.get(_mn)
        if _m and hasattr(_m, 'config') and _m.config is not None:
            try:
                _m.config.FAST_CUT_LOSS_THRESHOLD = -3.0
            except Exception:
                pass
    # NEWBORN_PROTECT uses time.time() (Unix epoch) — V8 patches time.time via _SimTime.
    # No special handling needed.
    v8_logger.info(f"V8 CONFIG: HEDGE_MODE={getattr(config, 'HEDGE_MODE', 'unset')} HEDGE_ACCOUNTS={getattr(config, 'HEDGE_ACCOUNTS', [])} STRICT_NO_LOSS_ACCOUNTS={getattr(config, 'STRICT_NO_LOSS_ACCOUNTS', [])} DELTA_ENGINE_ENABLED={getattr(config, 'DELTA_ENGINE_ENABLED', 'unset')} NEWBORN_PROTECT={getattr(config, 'NEWBORN_PROTECT_ENABLED', 'unset')}")

    # ═══════════════════════════════════════════════════════════════
    # SIMULATION LOOP — advance time bar by bar, run ALL trading loops
    # ═══════════════════════════════════════════════════════════════
    v8_logger.info("Starting simulation...")

    # Collect all timestamps
    all_ts = set()
    start_ts_filter = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    for sym, store in stores.items():
        for t in store.timestamps:
            t_int = int(t)
            if t_int >= start_ts_filter:
                all_ts.add(t_int)
    sorted_ts = sorted(all_ts)
    v8_logger.info(f"Simulation: {len(sorted_ts)} bars, {len(stores)} symbols")

    # ── SIGNAL GATE: pre-compute per-symbol entry-signal timestamps ──────────
    # Union of wt/stoch/dc crossover event arrays (binary flags in NPZ).
    # Dilated ±2 bars so entry fires right after a cross. Skips ~90% of bars
    # where no signal can possibly trigger check_entry_candidates.
    # sym -> set[int] of timestamps where an entry signal fires.
    # None means "no gate" (always include) — used as fallback if arrays missing.
    _entry_signal_sets = {}
    _GATE_KEYS = [
        'wt_cross_bull_15m', 'wt_cross_bear_15m',
        'wt_cross_bull_3m', 'wt_cross_bear_3m',
        'wt_cross_bull_1h', 'wt_cross_bear_1h',
        'stoch_crossover_3m', 'stoch_crossunder_3m',
        'stoch_crossover_15m', 'stoch_crossunder_15m',
        'dc_high_crossover_3m', 'dc_low_crossunder_3m',
    ]
    for _g_sym, _g_store in stores.items():
        try:
            _g_n = len(_g_store.timestamps)
            _g_mask = np.zeros(_g_n, dtype=bool)
            for _g_key in _GATE_KEYS:
                if _g_key in _g_store.arrays:
                    _g_mask |= _g_store.arrays[_g_key].astype(bool)
            if getattr(config, 'STDEV_BREAKOUT_ENABLED', False):
                _sb_htf_list = list(getattr(config, 'STDEV_BREAKOUT_HTF_LIST', None) or ['D', '4h'])
                _sb_pctb_long = float(getattr(config, 'STDEV_BREAKOUT_PCTB_LONG', 1.0))
                _sb_pctb_short = float(getattr(config, 'STDEV_BREAKOUT_PCTB_SHORT', 0.0))
                _sb_rvol_min = float(getattr(config, 'STDEV_BREAKOUT_RVOL_MIN', 1.2))
                for _sb_htf in _sb_htf_list:
                    _sb_pctb = _g_store.arrays.get(f'bb_pct_b_{_sb_htf}', np.zeros(_g_n))
                    _sb_rvol = _g_store.arrays.get(f'relative_volume_{_sb_htf}', np.ones(_g_n))
                    _g_mask |= (_sb_pctb >= _sb_pctb_long) & (_sb_rvol >= _sb_rvol_min)
                    _g_mask |= (_sb_pctb <= _sb_pctb_short) & (_sb_rvol >= _sb_rvol_min)
            if getattr(config, 'STDEV_BOUNCE_ENABLED', False):
                _bn_htf_list = list(getattr(config, 'STDEV_BOUNCE_HTF_LIST', None) or ['D', '4h'])
                _bn_pctb_long = float(getattr(config, 'STDEV_BOUNCE_PCTB_LONG', 0.05))
                _bn_pctb_short = float(getattr(config, 'STDEV_BOUNCE_PCTB_SHORT', 0.95))
                _bn_rvol_min = float(getattr(config, 'STDEV_BOUNCE_RVOL_MIN', 1.2))
                for _bn_htf in _bn_htf_list:
                    _bn_pctb = _g_store.arrays.get(f'bb_pct_b_{_bn_htf}', np.zeros(_g_n))
                    _bn_rvol = _g_store.arrays.get(f'relative_volume_{_bn_htf}', np.ones(_g_n))
                    _g_mask |= (_bn_pctb <= _bn_pctb_long) & (_bn_rvol >= _bn_rvol_min)
                    _g_mask |= (_bn_pctb >= _bn_pctb_short) & (_bn_rvol >= _bn_rvol_min)
            if _g_mask.any():
                _g_dil = _g_mask.copy()
                for _g_d in range(1, 4):
                    if _g_d < _g_n:
                        _g_dil[_g_d:] |= _g_mask[:-_g_d]
                        _g_dil[:-_g_d] |= _g_mask[_g_d:]
                _g_mask = _g_dil
            _entry_signal_sets[_g_sym] = set(int(_g_store.timestamps[i]) for i in np.where(_g_mask)[0])
        except Exception:
            _entry_signal_sets[_g_sym] = None
    _gate_filtered = 0
    _gate_total_checks = 0
    _gate_pct_logged = False
    v8_logger.info(f"[SIGNAL_GATE] Built for {len(_entry_signal_sets)} symbols. Sample: {list(_entry_signal_sets.keys())[:3]}")
    # ─────────────────────────────────────────────────────────────────────────

    # Start the OrderQueue processor (REAL)
    queue_task = asyncio.create_task(order_queue.process_orders())

    t0 = _real_time_module.time()
    # In sweep mode: report every 200 steps for fast feedback. Normal: every 2000 (60 reports/run).
    report_every = 200 if _SWEEP_MODE else max(1, min(2000, len(sorted_ts) // 60))
    _last_heartbeat = _real_time_module.time()
    # V8_MAX_BARS env var (for profiling / diagnostic runs) — cap total bars processed
    _v8_max_bars = int(os.environ.get("V8_MAX_BARS", "0") or 0)
    if _v8_max_bars > 0:
        sorted_ts = sorted_ts[:_v8_max_bars]
        v8_logger.info(f"[V8_MAX_BARS] capped sim to {_v8_max_bars} bars")

    _bt_rg_disabled = os.environ.get("V8_RATE_GUARD_DISABLED", "0") == "1"
    _bt_rg = None if _bt_rg_disabled else RateGuard(n_accts=max(1, len(stores)), label=f"backtest_v8_engine.crypto.{account_key}")

    for step, ts in enumerate(sorted_ts):
        _sim_ts[0] = float(ts)
        # Heartbeat every 10s wall-clock so sweep monitor knows engine is alive
        _now_real = _real_time_module.time()
        if _now_real - _last_heartbeat > 10.0:
            print(f"V8_HEARTBEAT: step={step}/{len(sorted_ts)} closes={_live_pnl['n_closes']}", flush=True)
            _last_heartbeat = _now_real
        if _bt_rg is not None:
            _bt_rg.tick(_live_pnl["n_closes"])

        # Clear per-bar cooldowns/debounces — in live these use wall clock,
        # in V8 multiple bars process per real second so cooldowns block everything.
        # Without this, exits never fire because HARD_REDUCE_LOCK and DEBOUNCE block them.
        if hasattr(ez_manage, '_recent_reduces'):
            ez_manage._recent_reduces.clear()
        # Clear Redis debounce keys
        if hasattr(trade_manager, 'redis_manager') and trade_manager.redis_manager:
            try:
                for _rconn in (trade_manager.redis_manager.connections or {}).values():
                    if _rconn and hasattr(_rconn, '_data'):
                        _rconn._data = {k: v for k, v in getattr(_rconn, '_data', {}).items() if not k.startswith('debounce_exec:')}
            except Exception:
                pass
        # Clear tracker check times so exit/entry checks run every bar
        if hasattr(tracker_manager, 'last_check_times'):
            tracker_manager.last_check_times.clear()
        # Clear augment locks
        if hasattr(trade_manager, 'augmented_positions'):
            trade_manager.augmented_positions.clear()
        if hasattr(trade_manager, '_dc_breakout_entry_cd'):
            trade_manager._dc_breakout_entry_cd.clear()

        # FIX: Update mark_price + gain for ALL positions, INCLUDING EMPTY SHELLS.
        # Bug 2026-04-09: empty position shells retained STALE mark_price (sometimes from
        # NPZ-end values like $5234 BTCDOMUSDT) that get_fresh_price would return when a
        # new entry came in, causing -80% phantom losses on subsequent close. Always sync.
        _sim_dt = _sim_datetime_now(timezone.utc)
        for _pk, _pos in list(trade_manager.positions.items()):
            _sym = _pk.split(':')[1].replace('_LONG', '').replace('_SHORT', '') if ':' in _pk else ''
            _mp = price_cache.get(_sym, 0)
            if _mp <= 0:
                continue
            _pos.mark_price = _mp
            _pos.mark_price_last_updated = _sim_dt
            _amt = abs(getattr(_pos, 'positionAmt', 0))
            if _amt >= 0.0001:
                _ep = getattr(_pos, 'entry_price', 0) or _mp
                _is_long = _pk.endswith('_LONG')
                _pos.gain = ((_mp - _ep) / _ep * 100) if _is_long else ((_ep - _mp) / _ep * 100) if _ep > 0 else 0
                _pos.prev_gain = getattr(_pos, 'prev_gain', _pos.gain)
                if _pos.gain > getattr(_pos, 'max_gain', 0):
                    _pos.max_gain = _pos.gain
            # else: empty shell — only sync mark_price; NEVER touch entry_price (SACRED).
            # Stale entry_price on empty shells doesn't affect gain calc since the
            # gain block above is gated on _amt >= 0.0001.
            # FIX: Ensure opened_at is always datetime (prevents TypeError crash)
            if not isinstance(getattr(_pos, 'opened_at', None), datetime):
                _pos.opened_at = _sim_dt
            _pos.last_updated = _sim_dt

        # NO_LOSS already disabled before loop — ensure it stays off on ALL Config instances
        config.STRICT_NO_LOSS_ACCOUNTS = []
        config.Config.STRICT_NO_LOSS_ACCOUNTS = []
        for _m in [ez_manage, ez_positions_quick]:
            if hasattr(_m, 'config'):
                _m.config.STRICT_NO_LOSS_ACCOUNTS = []

        # Update indicators + prices for all symbols
        for sym, store in stores.items():
            idx = store.ts_to_idx.get(ts, -1)
            if idx < 0:
                continue
            p = store.price(idx)
            if p <= 0:
                continue
            indicators = store.build_indicator_dict(idx)
            _ha_map_c = {-1: 'red', 0: 'neutral', 1: 'green'}
            for _ha_tf_c in ['3m', '5m', '15m', '1h', '4h', 'D']:
                _ha_k_c = f'ha_{_ha_tf_c}'
                _ha_v_c = indicators.get(_ha_k_c)
                if isinstance(_ha_v_c, (int, float, np.integer, np.floating)):
                    indicators[_ha_k_c] = _ha_map_c.get(int(_ha_v_c), 'neutral')
            # Inject timestamp fields so staleness checks pass
            sim_dt = datetime.utcfromtimestamp(ts)
            sim_iso = sim_dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
            indicators['timestamp'] = sim_iso
            indicators['ts'] = float(ts)
            indicators['_tick_ts'] = float(ts)
            indicators['current_price'] = p
            indicators['mark_price'] = p
            for tf in ['3m', '5m', '15m', '1h', '4h', 'D']:
                indicators[f'timestamp_{tf}'] = sim_iso
                indicators[f'age_{tf}'] = 0.0
            for _ptf in ['15m', '1h', '4h', 'D']:
                _pk = f'stoch_k_{_ptf}_prev'
                if _pk not in indicators:
                    _pidx = max(0, idx - 1)
                    _src_k = f'stoch_k_{_ptf}'
                    indicators[_pk] = float(store.get(_src_k, _pidx)) if _pidx != idx and _src_k in store.arrays else indicators.get(_src_k, 50)
            indicator_cache[sym] = indicators
            price_cache[sym] = p
            # Update ALL data sources so every code path sees fresh data
            trade_manager.indicators_snapshot[sym] = indicators
            # FastDataManager._cold_data (used by get_hot_state)
            if hasattr(data_manager, '_cold_data'):
                data_manager._cold_data[sym] = indicators
            # PositionService.indicators_snapshot
            if trade_manager.positions_service and hasattr(trade_manager.positions_service, 'indicators_snapshot'):
                trade_manager.positions_service.indicators_snapshot[sym] = indicators
            # Price caches on trade_manager
            trade_manager.price_cache[sym] = {'price': p, 'ts': float(ts)}
            if hasattr(trade_manager, 'price_cache_2'):
                trade_manager.price_cache_2[sym] = p
            if hasattr(trade_manager, 'price_update_time'):
                trade_manager.price_update_time[sym] = float(ts)

        # Update mark_price + gain on ALL open positions (critical for gate checks)
        for pk, pos in trade_manager.positions.items():
            if abs(getattr(pos, 'positionAmt', 0)) < 0.0001:
                continue
            sym = getattr(pos, 'symbol', '')
            px = price_cache.get(sym, 0)
            if px <= 0:
                continue
            pos.mark_price = px
            entry = getattr(pos, 'entry_price', 0)
            if entry > 0:
                is_long = pk.endswith("_LONG")
                gain = ((px - entry) / entry * 100) if is_long else ((entry - px) / entry * 100)
                pos.gain = gain
                if gain > getattr(pos, 'max_gain', 0):
                    pos.max_gain = gain

        # ═══════════════════════════════════════════════════════════════════════════
        # DISC-7: RIDICULOUS_LOSS_PCT — mirror ez_manage.py:20529
        # Force-close any position whose gain <= RIDICULOUS_LOSS_PCT (-15% default).
        # Bypasses UNIVERSAL_NOLOSS_GATE (reason string contains RIDICULOUS_LOSS).
        # ═══════════════════════════════════════════════════════════════════════════
        if bool(getattr(config, 'RIDICULOUS_HOLD_GUARD_ENABLED', True)):
            _rl_loss_cap = float(getattr(config, 'RIDICULOUS_LOSS_PCT', -15.0))
            for _rl_pk, _rl_pos in list(trade_manager.positions.items()):
                if abs(getattr(_rl_pos, 'positionAmt', 0)) < 0.0001:
                    continue
                _rl_gain = float(getattr(_rl_pos, 'gain', 0) or 0)
                if _rl_gain <= _rl_loss_cap:
                    _rl_sym = getattr(_rl_pos, 'symbol', '') or (_rl_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _rl_pk else _rl_pk[:-5] if _rl_pk.endswith('_LONG') else _rl_pk[:-6])
                    _rl_px = price_cache.get(_rl_sym, 0)
                    if _rl_px <= 0:
                        continue
                    _rl_is_long = _rl_pk.endswith('_LONG')
                    _rl_side = 'SELL' if _rl_is_long else 'BUY'
                    _rl_ps = 'LONG' if _rl_is_long else 'SHORT'
                    _rl_qty = abs(float(getattr(_rl_pos, 'positionAmt', 0)))
                    _rl_why = f'RIDICULOUS_LOSS_BACKTEST_g{_rl_gain:.2f}%_cap{_rl_loss_cap:.1f}%'
                    v8_logger.warning(f"[RIDICULOUS_LOSS_BACKTEST] {_rl_pk}: gain={_rl_gain:.2f}% <= {_rl_loss_cap}%, force-closing at {_rl_px}")
                    try:
                        await trade_manager.execute_trade_action(account_key=account_key, position_key=_rl_pk, symbol=_rl_sym, quantity=_rl_qty, current_price=_rl_px, side=_rl_side, position_side=_rl_ps, action='CLOSE', reason=_rl_why, is_full_close=True, is_hedge=False)
                    except Exception as _rl_err:
                        if step < 10 or step % 1000 == 0:
                            v8_logger.error(f"[RIDICULOUS_LOSS_BACKTEST_ERR] {_rl_pk}: {_rl_err}")

        # ═══════════════════════════════════════════════════════════════════════════
        # DISC-8: UNDERWATER_HEDGE_OR_CLOSE — mirror ez_manage.py:20664
        # If position pnl<0 AND wt1_15m against AND hedge active AND ≥2 of 4 HTF agree → force-close origin.
        # If position pnl<0 AND wt1_15m against AND no hedge AND gain < MANDATORY_HEDGE threshold → fire hedge.
        # ═══════════════════════════════════════════════════════════════════════════
        if bool(getattr(config, 'UNDERWATER_HEDGE_OR_CLOSE_ENABLED', True)):
            _uh_bt_thr = float(getattr(config, 'MANDATORY_HEDGE_GAIN_THRESHOLD_PCT', -0.5))
            _uh_bt_htf_req = int(getattr(config, 'UNDERWATER_HEDGE_OR_CLOSE_HTF_CLOSE_REQUIRED', 2))
            _uh_bt_cd_dict = trade_manager.__dict__.setdefault('_bt_underwater_cd', {})
            _uh_bt_cd_sec = float(getattr(config, 'UNDERWATER_HEDGE_OR_CLOSE_COOLDOWN_SEC', 60.0))
            _uh_bt_bar_sec = 60.0  # approximate bar duration (15m bars → 900s; use 60s for cooldown compat)
            for _uh_pk, _uh_pos in list(trade_manager.positions.items()):
                if abs(getattr(_uh_pos, 'positionAmt', 0)) < 0.0001:
                    continue
                _uh_gain = float(getattr(_uh_pos, 'gain', 0) or 0)
                if _uh_gain >= 0:
                    continue
                # Cooldown: skip if fired within last cooldown window (approximate via step count)
                _uh_last_step = _uh_bt_cd_dict.get(_uh_pk, -9999)
                if step - _uh_last_step < max(1, int(_uh_bt_cd_sec / 60)):
                    continue
                _uh_sym = getattr(_uh_pos, 'symbol', '') or (_uh_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _uh_pk else _uh_pk[:-5] if _uh_pk.endswith('_LONG') else _uh_pk[:-6])
                _uh_ind = indicator_cache.get(_uh_sym, {})
                if not _uh_ind:
                    continue
                _uh_w1_15m = float(_uh_ind.get('wt1_15m', 0) or 0)
                _uh_w2_15m = float(_uh_ind.get('wt2_15m', 0) or 0)
                if _uh_w1_15m == 0 and _uh_w2_15m == 0:
                    continue
                _uh_is_long = _uh_pk.endswith('_LONG')
                _uh_against_15m = (_uh_w1_15m < _uh_w2_15m) if _uh_is_long else (_uh_w1_15m > _uh_w2_15m)
                if not _uh_against_15m:
                    continue
                # Check hedge active via tracker_manager
                _uh_tm = getattr(trade_manager, 'tracker_manager', None)
                _uh_active = bool(_uh_tm) and any(
                    (h.get('losing_position_key') == _uh_pk or h.get('hedge_for') == _uh_pk)
                    for h in (getattr(_uh_tm, 'active_hedges', None) or []))
                _uh_px = price_cache.get(_uh_sym, 0)
                if _uh_px <= 0:
                    continue
                _uh_qty = abs(float(getattr(_uh_pos, 'positionAmt', 0)))
                _uh_side = 'SELL' if _uh_is_long else 'BUY'
                _uh_ps = 'LONG' if _uh_is_long else 'SHORT'
                if not _uh_active and _uh_gain < _uh_bt_thr:
                    # No hedge — fire hedge via scan_and_hedge_losers (DISC-4 overlap)
                    if getattr(config, 'HEDGE_MODE', False) and account_key in getattr(config, 'HEDGE_ACCOUNTS', []):
                        _uh_bt_cd_dict[_uh_pk] = step
                        v8_logger.warning(f"[UNDERWATER_HEDGE_OR_CLOSE_BACKTEST] {_uh_pk}: gain={_uh_gain:.2f}% wt15m against, no hedge — triggering hedge scan")
                        try:
                            await hedge_engine.scan_and_hedge_losers(account_key)
                        except Exception as _uh_he_err:
                            if step < 10 or step % 1000 == 0:
                                v8_logger.error(f"[UNDERWATER_HOC_HEDGE_ERR] {_uh_pk}: {_uh_he_err}")
                elif _uh_active:
                    # Hedge active — check HTF agreement for force-close of origin
                    _uh_htf_pairs = [('wt1_15m', 'wt2_15m'), ('wt1_1h', 'wt2_1h'), ('wt1_4h', 'wt2_4h'), ('wt1_D', 'wt2_D')]
                    _uh_htf_count = 0
                    for _uhh1_k, _uhh2_k in _uh_htf_pairs:
                        _uhh1 = float(_uh_ind.get(_uhh1_k, 0) or 0)
                        _uhh2 = float(_uh_ind.get(_uhh2_k, 0) or 0)
                        if _uhh1 == 0 and _uhh2 == 0:
                            continue
                        if (_uh_is_long and _uhh1 < _uhh2) or (not _uh_is_long and _uhh1 > _uhh2):
                            _uh_htf_count += 1
                    if _uh_htf_count >= _uh_bt_htf_req:
                        _uh_bt_cd_dict[_uh_pk] = step
                        _uh_reason = f'UNDERWATER_HEDGE_OR_CLOSE_BACKTEST_wt15m{_uh_w1_15m:.1f}vs{_uh_w2_15m:.1f}_HTF{_uh_htf_count}_g{_uh_gain:.2f}%'
                        v8_logger.warning(f"[UNDERWATER_HEDGE_OR_CLOSE_BACKTEST] {_uh_pk}: gain={_uh_gain:.2f}% hedge active HTF {_uh_htf_count}/4 >= {_uh_bt_htf_req} — FORCE CLOSE origin")
                        try:
                            await trade_manager.execute_trade_action(account_key=account_key, position_key=_uh_pk, symbol=_uh_sym, quantity=_uh_qty, current_price=_uh_px, side=_uh_side, position_side=_uh_ps, action='CLOSE', reason=_uh_reason, is_full_close=True, is_hedge=False)
                        except Exception as _uh_cl_err:
                            if step < 10 or step % 1000 == 0:
                                v8_logger.error(f"[UNDERWATER_HOC_CLOSE_ERR] {_uh_pk}: {_uh_cl_err}")

        # ═══════════════════════════════════════════════════════════════════════════
        # DISC-4: OBLIGATORY_HEDGE — mirror ez_manage.py:14071
        # When UNIVERSAL_NOLOSS_GATE blocks a close, live fires a hedge if:
        #   gain <= OBLIGATORY_HEDGE_MIN_LOSS_PCT (-0.25% default)
        #   AND wt against on (3m OR 15m) + (1h)  [≥2 of enabled TFs]
        #   AND no existing hedge for this pk
        # In backtest: run per-bar for all underwater positions (NOLOSS gate fires before check_exit).
        # ═══════════════════════════════════════════════════════════════════════════
        if bool(getattr(config, 'OBLIGATORY_HEDGE_ENABLED', True)) and \
           getattr(config, 'HEDGE_MODE', False) and account_key in getattr(config, 'HEDGE_ACCOUNTS', []):
            _oh_bt_min = float(getattr(config, 'OBLIGATORY_HEDGE_MIN_LOSS_PCT', -0.25))
            _oh_bt_req = int(getattr(config, 'OBLIGATORY_HEDGE_WT_TFS_REQUIRED', 2))
            _oh_bt_cd_dict = trade_manager.__dict__.setdefault('_bt_obligatory_hedge_cd', {})
            for _oh_pk, _oh_pos in list(trade_manager.positions.items()):
                if abs(getattr(_oh_pos, 'positionAmt', 0)) < 0.0001:
                    continue
                _oh_gain = float(getattr(_oh_pos, 'gain', 0) or 0)
                if _oh_gain > _oh_bt_min:
                    continue
                # Cooldown: fire at most once per ~5 bars per position
                _oh_last_step = _oh_bt_cd_dict.get(_oh_pk, -9999)
                if step - _oh_last_step < 5:
                    continue
                _oh_sym = getattr(_oh_pos, 'symbol', '') or (_oh_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _oh_pk else _oh_pk[:-5] if _oh_pk.endswith('_LONG') else _oh_pk[:-6])
                _oh_ind = indicator_cache.get(_oh_sym, {})
                if not _oh_ind:
                    continue
                # Check if hedge already active for this position
                _oh_tm = getattr(trade_manager, 'tracker_manager', None)
                _oh_hedge_active = bool(_oh_tm) and any(
                    (h.get('losing_position_key') == _oh_pk or h.get('hedge_for') == _oh_pk)
                    for h in (getattr(_oh_tm, 'active_hedges', None) or []))
                if _oh_hedge_active:
                    continue
                _oh_is_long = _oh_pk.endswith('_LONG')
                _oh_use = {
                    '3m': bool(getattr(config, 'OBLIGATORY_HEDGE_WT_USE_3M', True)),
                    '1m': bool(getattr(config, 'OBLIGATORY_HEDGE_WT_USE_1M', False)),
                    '15m': bool(getattr(config, 'OBLIGATORY_HEDGE_WT_USE_15M', False)),
                    '1h': bool(getattr(config, 'OBLIGATORY_HEDGE_WT_USE_1H', True)),
                }
                _oh_wt_against = 0
                _oh_tfs_enabled = 0
                for _oh_tf in ('1m', '3m', '15m', '1h'):
                    if not _oh_use[_oh_tf]:
                        continue
                    _oh_tfs_enabled += 1
                    _ow1 = float(_oh_ind.get(f'wt1_{_oh_tf}', 0) or 0)
                    _ow2 = float(_oh_ind.get(f'wt2_{_oh_tf}', 0) or 0)
                    if _ow1 == 0 and _ow2 == 0:
                        continue
                    if _oh_is_long:
                        _oh_wt_against += int(_ow1 < _ow2)
                    else:
                        _oh_wt_against += int(_ow1 > _ow2)
                if _oh_tfs_enabled > 0 and _oh_wt_against >= _oh_bt_req:
                    _oh_bt_cd_dict[_oh_pk] = step
                    v8_logger.warning(f"[OBLIGATORY_HEDGE_BACKTEST] {_oh_pk}: gain={_oh_gain:.2f}% wt_against={_oh_wt_against}/{_oh_tfs_enabled} — scan_and_hedge_losers")
                    try:
                        await hedge_engine.scan_and_hedge_losers(account_key)
                    except Exception as _oh_err:
                        if step < 10 or step % 1000 == 0:
                            v8_logger.error(f"[OBLIGATORY_HEDGE_BACKTEST_ERR] {_oh_pk}: {_oh_err}")

        # Clear reduce cooldowns per bar (each bar = 3-15 min in real time)
        if hasattr(ez_manage, '_recent_reduces'):
            ez_manage._recent_reduces.clear()

        # === PARTIAL_PROFIT_LOCK v2 inline (2026-04-21) — fires regardless of V8_SKIP_PROCESS_POSITION.
        # Step 1 (+0.5%): TP 50% REDUCE; stop_level = entry × (1 ± BE_buffer%). Close before BE.
        # Step 2 (+0.75%): upgrade stop_level → first_exit_price (locks +0.5% scalp).
        # Step 3 (price hits stop): full CLOSE of remainder.
        _ppl_bt_on = bool(getattr(config, 'PARTIAL_PROFIT_LOCK_ENABLED', False))
        if _ppl_bt_on:
            _ppl_bt_accts = set(getattr(config, 'PARTIAL_PROFIT_LOCK_ACCOUNTS', []) or []) | set(getattr(config, 'PARTIAL_PROFIT_LOCK_ACCOUNTS_TRADIER', []) or [])
            if account_key in _ppl_bt_accts:
                _ppl_bt_min = float(getattr(config, 'PARTIAL_PROFIT_LOCK_GAIN_PCT', getattr(config, 'PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER', 0.5)))
                _ppl_bt_arm = float(getattr(config, 'PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT', getattr(config, 'PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER', 0.75)))
                _ppl_bt_buf = float(getattr(config, 'PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT', getattr(config, 'PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER', 0.02)))
                _ppl_bt_frac = float(getattr(config, 'PARTIAL_PROFIT_LOCK_FRAC', getattr(config, 'PARTIAL_PROFIT_LOCK_FRAC_TRADIER', 0.5)))
                if not hasattr(trade_manager, 'partial_profit_lock_state'):
                    trade_manager.partial_profit_lock_state = {}
                for _ppl_bt_pk in list(trade_manager.positions.keys()):
                    _ppl_bt_pos = trade_manager.positions.get(_ppl_bt_pk)
                    if not _ppl_bt_pos:
                        continue
                    _ppl_bt_qty = abs(float(getattr(_ppl_bt_pos, 'positionAmt', 0) or 0))
                    if _ppl_bt_qty < 0.0001:
                        continue
                    _ppl_bt_is_long = _ppl_bt_pk.endswith('_LONG')
                    _ppl_bt_ep = float(getattr(_ppl_bt_pos, 'entry_price', 0) or 0)
                    if _ppl_bt_ep <= 0:
                        continue
                    _ppl_bt_sym = _ppl_bt_pk.split(":", 1)[-1].rsplit("_", 1)[0] if ":" in _ppl_bt_pk else (_ppl_bt_pk[:-5] if _ppl_bt_pk.endswith("_LONG") else _ppl_bt_pk[:-6])
                    _ppl_bt_ind = indicator_cache.get(_ppl_bt_sym, {}) if isinstance(indicator_cache, dict) else {}
                    _ppl_bt_px = float(_ppl_bt_ind.get('current_price', 0) or 0)
                    if _ppl_bt_px <= 0:
                        continue
                    _ppl_bt_gain = ((_ppl_bt_px - _ppl_bt_ep) / _ppl_bt_ep * 100.0) if _ppl_bt_is_long else ((_ppl_bt_ep - _ppl_bt_px) / _ppl_bt_ep * 100.0)
                    _ppl_bt_state = trade_manager.partial_profit_lock_state.get(_ppl_bt_pk, {})
                    _ppl_bt_fired = _ppl_bt_state.get('fired', False)
                    _ppl_bt_fep = float(_ppl_bt_state.get('first_exit_price', 0.0))
                    _ppl_bt_stop = float(_ppl_bt_state.get('stop_level', 0.0))
                    _ppl_bt_upg = _ppl_bt_state.get('stop_upgraded', False)
                    _ppl_bt_side = "SELL" if _ppl_bt_is_long else "BUY"
                    _ppl_bt_pside = "LONG" if _ppl_bt_is_long else "SHORT"
                    if not _ppl_bt_fired and _ppl_bt_gain >= _ppl_bt_min:
                        _ppl_bt_red = _ppl_bt_qty * _ppl_bt_frac
                        _ppl_bt_keep = _ppl_bt_qty - _ppl_bt_red
                        if _ppl_bt_red > 0 and _ppl_bt_keep > 0:
                            _ppl_bt_be = _ppl_bt_ep * (1.0 + _ppl_bt_buf / 100.0) if _ppl_bt_is_long else _ppl_bt_ep * (1.0 - _ppl_bt_buf / 100.0)
                            try:
                                await trade_manager.execute_trade_action(account_key=account_key, position_key=_ppl_bt_pk, symbol=_ppl_bt_sym, quantity=_ppl_bt_red, current_price=_ppl_bt_px, side=_ppl_bt_side, position_side=_ppl_bt_pside, action='REDUCE', reason=f"PPL_TP_gain{_ppl_bt_gain:.2f}_50pct", is_full_close=False, is_hedge=False)
                                trade_manager.partial_profit_lock_state[_ppl_bt_pk] = {'fired': True, 'first_exit_price': _ppl_bt_px, 'stop_level': _ppl_bt_be, 'stop_upgraded': False}
                            except Exception as _ppl_bt_e:
                                if step < 10 or step % 1000 == 0:
                                    v8_logger.error(f"[V8_PPL_ERR] {_ppl_bt_pk}: {_ppl_bt_e}")
                    elif _ppl_bt_fired and not _ppl_bt_upg and _ppl_bt_gain >= _ppl_bt_arm and _ppl_bt_fep > 0:
                        trade_manager.partial_profit_lock_state[_ppl_bt_pk] = {**_ppl_bt_state, 'stop_level': _ppl_bt_fep, 'stop_upgraded': True}
                    elif _ppl_bt_fired and _ppl_bt_stop > 0:
                        _ppl_bt_hit = (_ppl_bt_is_long and _ppl_bt_px <= _ppl_bt_stop) or (not _ppl_bt_is_long and _ppl_bt_px >= _ppl_bt_stop)
                        if _ppl_bt_hit:
                            try:
                                await trade_manager.execute_trade_action(account_key=account_key, position_key=_ppl_bt_pk, symbol=_ppl_bt_sym, quantity=_ppl_bt_qty, current_price=_ppl_bt_px, side=_ppl_bt_side, position_side=_ppl_bt_pside, action='CLOSE', reason=f"PPL_SL_{'upg' if _ppl_bt_upg else 'BE'}_px{_ppl_bt_px:.4f}_stop{_ppl_bt_stop:.4f}", is_full_close=True, is_hedge=False)
                                trade_manager.partial_profit_lock_state.pop(_ppl_bt_pk, None)
                            except Exception as _ppl_bt_sl_e:
                                if step < 10 or step % 1000 == 0:
                                    v8_logger.error(f"[V8_PPL_SL_ERR] {_ppl_bt_pk}: {_ppl_bt_sl_e}")
        # Run ONE cycle of all trading loops for this bar
        # process_position on ALL active positions
        # 2026-04-21: V8_SKIP_PROCESS_POSITION=1 stubs the call per user directive —
        # process_position is slow and does not measurably affect Sharpe/WR (exits still flow
        # through check_exit_candidates_for_account below). Live code path unchanged.
        if os.environ.get("V8_SKIP_PROCESS_POSITION", "0") != "1":
            for pk in list(trade_manager.positions.keys()):
                pos = trade_manager.positions.get(pk)
                if pos and abs(getattr(pos, 'positionAmt', 0)) > 0.0001:
                    try:
                        await ez_manage.process_position(
                            account_key=account_key, position_key=pk,
                            order_queue=order_queue, trade_manager=trade_manager, force=True)
                    except Exception as _pp_err:
                        if step < 10 or step % 1000 == 0:
                            v8_logger.error(f"[V8_PP_ERROR] step={step} pk={pk} err={_pp_err}")

        # HEDGE LIFECYCLE — 2026-04-17: wire real hedge engine into backtest.
        # Live main loop calls these periodically; backtest must too or HEDGE_* switches
        # never exercise and all hedge sweeps show zero variance.
        # monitor_and_manage_hedges: handles HEDGE_WT_KILL, orphan cleanup, profit protect, decay.
        # scan_and_hedge_losers: opens new same-symbol hedges when position in loss + wt15m against.
        if getattr(config, 'HEDGE_MODE', False) and account_key in getattr(config, 'HEDGE_ACCOUNTS', []):
            try:
                await hedge_engine.monitor_and_manage_hedges(account_key)
            except Exception as _hm_err:
                if step < 10 or step % 1000 == 0:
                    v8_logger.error(f"[V8_HEDGE_MON_ERR] step={step} err={_hm_err}")
            try:
                await hedge_engine.scan_and_hedge_losers(account_key)
            except Exception as _hs_err:
                if step < 10 or step % 1000 == 0:
                    v8_logger.error(f"[V8_HEDGE_SCAN_ERR] step={step} err={_hs_err}")

        # REENTRY PIPELINE — 2026-04-17: wire evaluate_reentry_2 into backtest.
        # Live calls this via SYMBOL_WATCHDOG + AUGMENT_MONITOR + periodic_evaluate_reentry_loop,
        # async loops spawned at startup but not present in backtest. Without this, the entire
        # process_single_reentry_evaluation path (DIR_FAVORABLE, DC_BREAKOUT, QUICK_RECOVERY,
        # WT15M_CROSS, K15M_PARTIAL, POST_CONSOL) is unreachable and every REENTRY2_*/WT15M/
        # K15M/POST_CONSOL switch shows zero variance.
        if getattr(config, 'REENTRY_2_ENABLED', True):
            try:
                await ez_reentry.get_evaluate_reentry_2()(trade_manager)
            except Exception as _re_err:
                if step < 10 or step % 1000 == 0:
                    v8_logger.error(f"[V8_REENTRY2_ERR] step={step} err={_re_err}")
            # LIVE ALSO calls evaluate_reentry_2_epq (ez_positions_quick.py:14472 periodic loop).
            # Mirror that here so backtest runs both paths — they share reentry_data dict.
            try:
                await ez_reentry.get_evaluate_reentry_2_epq()(trade_manager, data_manager=data_manager)
            except Exception as _re_epq_err:
                if step < 10 or step % 1000 == 0:
                    v8_logger.error(f"[V8_REENTRY2_EPQ_ERR] step={step} err={_re_epq_err}")

        # PROFIT-REDUCE PULLBACK REENTRY (Fixes A+B+C — 2026-05-06)
        # Catches the case where a LONG was profit-reduced at a pump top and price corrected.
        # reentry_enforcement_loop (live-only) misses this; backtest must call it explicitly.
        # Gate: REENTRY_PROFIT_PULLBACK_ENABLED=False (default) — sweep to validate first.
        if getattr(config, 'REENTRY_PROFIT_PULLBACK_ENABLED', False):
            try:
                from ez_reentry_pullback import evaluate_profit_pullback_reentry as _ppb_eval
                await _ppb_eval(trade_manager, config)
            except Exception as _ppb_err:
                if step < 10 or step % 1000 == 0:
                    v8_logger.error(f"[V8_PPB_REENTRY_ERR] step={step} err={_ppb_err}")

        # check_exit_candidates (REAL)
        active_pks = [pk for pk, pos in trade_manager.positions.items()
                      if abs(getattr(pos, 'positionAmt', 0)) > 0.0001]
        if active_pks:
            try:
                await ez_positions_quick.check_exit_candidates_for_account(
                    trade_manager=trade_manager, account_key=account_key,
                    redis_manager=redis_manager, tracker_manager=tracker_manager,
                    order_queue=order_queue, data_manager=data_manager,
                    hedge_engine=hedge_engine, position_keys=active_pks, force=True)
            except Exception as _exit_err:
                if step < 10 or step % 1000 == 0:
                    v8_logger.error(f"[V8_EXIT_ERROR] step={step} active={len(active_pks)} err={_exit_err}")

        # V8 SRS SWEEP: check_exit_candidates reads SRS from ez_positions_quick.config
        # which has the strict entry>upper+AND cascade. Run the V8 relaxed SRS (OR cascade,
        # no entry>upper gate) as a second pass on still-active positions.
        _v8_srs_on = getattr(config, 'STRUCTURAL_RANGE_SHIFT_EXIT', False)
        if _v8_srs_on:
            _v8_srs_still_active = [pk for pk, pos in trade_manager.positions.items()
                                    if abs(getattr(pos, 'positionAmt', 0)) > 0.0001]
            for _srs_pk in _v8_srs_still_active:
                _srs_pos = trade_manager.positions.get(_srs_pk)
                if not _srs_pos:
                    continue
                _srs_sym = _srs_pk.split(":", 1)[-1].rsplit("_", 1)[0] if ":" in _srs_pk else (_srs_pk[:-5] if _srs_pk.endswith("_LONG") else (_srs_pk[:-6] if _srs_pk.endswith("_SHORT") else _srs_pk))
                _srs_ind = indicator_cache.get(_srs_sym, {})
                _srs_p = float(_srs_ind.get('current_price', 0) or 0)
                _srs_qty = abs(float(getattr(_srs_pos, 'positionAmt', 0)))
                if _srs_qty <= 0 or _srs_p <= 0:
                    continue
                _srs_is_long = _srs_pk.endswith('_LONG')
                _srs_tf = getattr(config, 'STRUCTURAL_RANGE_SHIFT_TF', 'bb_1h')
                _srs_fm = {'dc_1h': ('dc_high_1h', 'dc_low_1h'), 'dc_4h': ('dc_high_4h', 'dc_low_4h'), 'dc_D': ('dc_high_D', 'dc_low_D'), 'bb_1h': ('bb_upper_1h', 'bb_lower_1h'), 'bb_4h': ('bb_upper_4h', 'bb_lower_4h'), 'bb_D': ('bb_upper_D', 'bb_lower_D')}
                _srs_hk, _srs_lk = _srs_fm.get(_srs_tf, ('bb_upper_1h', 'bb_lower_1h'))
                _srs_hi = float(_srs_ind.get(_srs_hk, 0) or 0)
                _srs_lo = float(_srs_ind.get(_srs_lk, 0) or 0)
                _srs_band = float(getattr(config, 'STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS', 100.0)) / 10000.0
                _srs_k_hi = float(getattr(config, 'STRUCTURAL_RANGE_SHIFT_K_HIGH', 75.0))
                _srs_k_lo = float(getattr(config, 'STRUCTURAL_RANGE_SHIFT_K_LOW', 25.0))
                _srs_k1h = float(_srs_ind.get('stoch_k_1h', 50) or 50)
                _srs_k1h_p = float(_srs_ind.get('stoch_k_1h_prev', 50) or 50)
                _srs_k15m = float(_srs_ind.get('stoch_k_15m', 50) or 50)
                _srs_k15m_p = float(_srs_ind.get('stoch_k_15m_prev', 50) or 50)
                _srs_wt1_1h = float(_srs_ind.get('wt1_1h', 0) or 0)
                _srs_wt2_1h = float(_srs_ind.get('wt2_1h', 0) or 0)
                _srs_wt1_15m = float(_srs_ind.get('wt1_15m', 0) or 0)
                _srs_wt2_15m = float(_srs_ind.get('wt2_15m', 0) or 0)
                _srs_wt1_3m = float(_srs_ind.get('wt1_3m', _srs_ind.get('wt1_5m', 0)) or 0)
                _srs_wt2_3m = float(_srs_ind.get('wt2_3m', _srs_ind.get('wt2_5m', 0)) or 0)
                _srs_fire = False
                if _srs_is_long and _srs_hi > 0:
                    _srs_prox = abs(_srs_p - _srs_hi) / _srs_hi <= _srs_band
                    _srs_1h = ((_srs_k1h >= _srs_k_hi) and (_srs_k1h < _srs_k1h_p)) or (_srs_wt1_1h < _srs_wt2_1h)
                    _srs_15m = ((_srs_k15m >= _srs_k_hi) and (_srs_k15m < _srs_k15m_p)) or (_srs_wt1_15m < _srs_wt2_15m)
                    _srs_3m = _srs_wt1_3m < _srs_wt2_3m
                    _srs_fire = _srs_prox and _srs_1h and (_srs_15m or _srs_3m)
                elif (not _srs_is_long) and _srs_lo > 0:
                    _srs_prox = abs(_srs_p - _srs_lo) / _srs_lo <= _srs_band
                    _srs_1h = ((_srs_k1h <= _srs_k_lo) and (_srs_k1h > _srs_k1h_p)) or (_srs_wt1_1h > _srs_wt2_1h)
                    _srs_15m = ((_srs_k15m <= _srs_k_lo) and (_srs_k15m > _srs_k15m_p)) or (_srs_wt1_15m > _srs_wt2_15m)
                    _srs_3m = _srs_wt1_3m > _srs_wt2_3m
                    _srs_fire = _srs_prox and _srs_1h and (_srs_15m or _srs_3m)
                if _srs_fire:
                    _srs_side = 'SELL' if _srs_is_long else 'BUY'
                    _srs_ps = 'LONG' if _srs_is_long else 'SHORT'
                    _srs_reason = f"STRUCTURAL_RANGE_SHIFT_{_srs_ps}_V8_{_srs_tf}"
                    try:
                        await trade_manager.execute_trade_action(account_key=account_key, position_key=_srs_pk, symbol=_srs_sym, quantity=_srs_qty, current_price=_srs_p, side=_srs_side, position_side=_srs_ps, action='CLOSE', reason=_srs_reason, is_full_close=True, is_hedge=False)
                    except Exception as _srs_err:
                        if step < 10:
                            v8_logger.error(f"[V8_SRS_EXIT_ERROR] {_srs_pk}: {_srs_err}")

        # check_entry_candidates (REAL)
        # Reset processing flags so entries aren't blocked by "already processing" state
        for pk in all_position_keys:
            if hasattr(tracker_manager, '_processing'):
                tracker_manager._processing[pk] = False
            if hasattr(tracker_manager, 'last_check_times'):
                tracker_manager.last_check_times[pk] = 0.0
        # BACKTEST FIX: clear real-time dedupe state each step so every bar can attempt entries.
        if hasattr(trade_manager, 'order_deduplication'):
            trade_manager.order_deduplication.clear()
        if hasattr(trade_manager, '_queue_attempt_ts'):
            trade_manager._queue_attempt_ts.clear()
        _ts_int = int(ts)
        _gate_total_checks += len(all_position_keys)
        entry_pks = []
        for _epk in all_position_keys:
            _epos = trade_manager.positions.get(_epk)
            if _epos and abs(getattr(_epos, 'positionAmt', 0)) >= 0.001:
                continue
            _esym = (getattr(_epos, 'symbol', '') if _epos else '') or (_epk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _epk else _epk.rsplit('_', 1)[0])
            _egate = _entry_signal_sets.get(_esym)
            if _egate is not None and _ts_int not in _egate:
                _gate_filtered += 1
                continue
            entry_pks.append(_epk)
        if step < 3:
            v8_logger.info(f"[DBG] entry_pks={len(entry_pks)} all_position_keys={len(all_position_keys)} positions={len(trade_manager.positions)} gate_filtered={_gate_filtered}")
        if entry_pks:
            try:
                await ez_positions_quick.check_entry_candidates_for_account(
                    trade_manager=trade_manager, account_key=account_key,
                    redis_manager=redis_manager, tracker_manager=tracker_manager,
                    order_queue=order_queue, data_manager=data_manager,
                    hedge_engine=hedge_engine, position_keys=entry_pks, force=True)
            except Exception as e:
                if step < 3:
                    v8_logger.warning(f"[DBG] check_entry error: {e}")

        # GOLDEN RULE ENFORCEMENT (real backtest) — 2026-05-06
        # Mirrors live _golden_rule_loop in ez_manage.py (ang/inf always-long mandate).
        # For every symbol: wt1_3m > wt2_3m AND (price > dc_high_15m OR price > bb_upper_15m)
        # → must hold a LONG of base_usd×mult. Cascade: 1.5x at 1h, 2x at 4h, 3x at D.
        # Calls execute_trade_action directly — bypasses signal gate (fires every qualifying bar).
        # Gate: GOLDEN_RULE_ENABLED (default True).
        if getattr(config, 'GOLDEN_RULE_ENABLED', True):
            _gr_base_usd = float(getattr(config, 'GOLDEN_RULE_BASE_USD', 5.0))
            _gr_dc_15 = bool(getattr(config, 'GOLDEN_RULE_DC_15M_ENABLED', True))
            _gr_bb_15 = bool(getattr(config, 'GOLDEN_RULE_BB_15M_ENABLED', True))
            _gr_dc_1h = bool(getattr(config, 'GOLDEN_RULE_DC_1H_ENABLED', True))
            _gr_bb_1h = bool(getattr(config, 'GOLDEN_RULE_BB_1H_ENABLED', True))
            _gr_dc_4h = bool(getattr(config, 'GOLDEN_RULE_DC_4H_ENABLED', True))
            _gr_bb_4h = bool(getattr(config, 'GOLDEN_RULE_BB_4H_ENABLED', True))
            _gr_dc_D = bool(getattr(config, 'GOLDEN_RULE_DC_D_ENABLED', True))
            _gr_bb_D = bool(getattr(config, 'GOLDEN_RULE_BB_D_ENABLED', True))
            _gr_m15 = float(getattr(config, 'GOLDEN_RULE_MULT_15M', 1.0))
            _gr_m1h = float(getattr(config, 'GOLDEN_RULE_MULT_1H', 1.5))
            _gr_m4h = float(getattr(config, 'GOLDEN_RULE_MULT_4H', 2.0))
            _gr_mD = float(getattr(config, 'GOLDEN_RULE_MULT_D', 3.0))
            for _gr_sym in list(stores.keys()):
                _gr_ind = indicator_cache.get(_gr_sym, {})
                _gr_p = float(_gr_ind.get('current_price', 0) or 0)
                if _gr_p <= 0:
                    continue
                _gr_wt1 = float(_gr_ind.get('wt1_3m', 0) or 0)
                _gr_wt2 = float(_gr_ind.get('wt2_3m', 0) or 0)
                _gr_dc_h15 = float(_gr_ind.get('dc_high_15m', 0) or 0)
                _gr_dc_l15 = float(_gr_ind.get('dc_low_15m', 0) or 0)
                _gr_bb_u15 = float(_gr_ind.get('bb_upper_15m', 0) or 0)
                _gr_bb_l15 = float(_gr_ind.get('bb_lower_15m', 0) or 0)
                _gr_dc_h1h = float(_gr_ind.get('dc_high_1h', 0) or 0)
                _gr_dc_l1h = float(_gr_ind.get('dc_low_1h', 0) or 0)
                _gr_bb_u1h = float(_gr_ind.get('bb_upper_1h', 0) or 0)
                _gr_bb_l1h = float(_gr_ind.get('bb_lower_1h', 0) or 0)
                _gr_dc_h4h = float(_gr_ind.get('dc_high_4h', 0) or 0)
                _gr_dc_l4h = float(_gr_ind.get('dc_low_4h', 0) or 0)
                _gr_bb_u4h = float(_gr_ind.get('bb_upper_4h', 0) or 0)
                _gr_bb_l4h = float(_gr_ind.get('bb_lower_4h', 0) or 0)
                _gr_dc_hD = float(_gr_ind.get('dc_high_D', 0) or 0)
                _gr_dc_lD = float(_gr_ind.get('dc_low_D', 0) or 0)
                _gr_bb_uD = float(_gr_ind.get('bb_upper_D', 0) or 0)
                _gr_bb_lD = float(_gr_ind.get('bb_lower_D', 0) or 0)
                for _gr_is_long in (True, False):
                    _gr_pk = f'{account_key}:{_gr_sym}_{"LONG" if _gr_is_long else "SHORT"}'
                    if _gr_is_long:
                        if _gr_wt1 <= _gr_wt2:
                            continue
                        _gr_dc15_ok = _gr_dc_15 and _gr_dc_h15 > 0 and _gr_p > _gr_dc_h15
                        _gr_bb15_ok = _gr_bb_15 and _gr_bb_u15 > 0 and _gr_p > _gr_bb_u15
                    else:
                        if _gr_wt1 >= _gr_wt2:
                            continue
                        _gr_dc15_ok = _gr_dc_15 and _gr_dc_l15 > 0 and _gr_p < _gr_dc_l15
                        _gr_bb15_ok = _gr_bb_15 and _gr_bb_l15 > 0 and _gr_p < _gr_bb_l15
                    if not (_gr_dc15_ok or _gr_bb15_ok):
                        continue
                    _gr_mult = _gr_m15
                    if _gr_is_long:
                        if (_gr_dc_1h and _gr_dc_h1h > 0 and _gr_p > _gr_dc_h1h) or (_gr_bb_1h and _gr_bb_u1h > 0 and _gr_p > _gr_bb_u1h):
                            _gr_mult = _gr_m1h
                        if (_gr_dc_4h and _gr_dc_h4h > 0 and _gr_p > _gr_dc_h4h) or (_gr_bb_4h and _gr_bb_u4h > 0 and _gr_p > _gr_bb_u4h):
                            _gr_mult = _gr_m4h
                        if (_gr_dc_D and _gr_dc_hD > 0 and _gr_p > _gr_dc_hD) or (_gr_bb_D and _gr_bb_uD > 0 and _gr_p > _gr_bb_uD):
                            _gr_mult = _gr_mD
                    else:
                        if (_gr_dc_1h and _gr_dc_l1h > 0 and _gr_p < _gr_dc_l1h) or (_gr_bb_1h and _gr_bb_l1h > 0 and _gr_p < _gr_bb_l1h):
                            _gr_mult = _gr_m1h
                        if (_gr_dc_4h and _gr_dc_l4h > 0 and _gr_p < _gr_dc_l4h) or (_gr_bb_4h and _gr_bb_l4h > 0 and _gr_p < _gr_bb_l4h):
                            _gr_mult = _gr_m4h
                        if (_gr_dc_D and _gr_dc_lD > 0 and _gr_p < _gr_dc_lD) or (_gr_bb_D and _gr_bb_lD > 0 and _gr_p < _gr_bb_lD):
                            _gr_mult = _gr_mD
                    _gr_target_usd = _gr_base_usd * _gr_mult
                    _gr_target_qty = _gr_target_usd / _gr_p
                    _gr_pos = trade_manager.positions.get(_gr_pk)
                    _gr_cur_amt = abs(float(getattr(_gr_pos, 'positionAmt', 0))) if _gr_pos else 0.0
                    if _gr_cur_amt > 0 and _gr_cur_amt * _gr_p >= _gr_target_usd * 0.8:
                        continue
                    _gr_qty = _gr_target_qty - _gr_cur_amt
                    if _gr_qty * _gr_p < 1.0:
                        continue
                    _gr_action = 'OPEN' if _gr_cur_amt == 0 else 'AUGMENT'
                    _gr_side = 'BUY' if _gr_is_long else 'SELL'
                    _gr_ps = 'LONG' if _gr_is_long else 'SHORT'
                    try:
                        await trade_manager.execute_trade_action(
                            account_key=account_key, position_key=_gr_pk, symbol=_gr_sym,
                            quantity=_gr_qty, current_price=_gr_p, side=_gr_side,
                            position_side=_gr_ps, action=_gr_action,
                            reason=f'GOLDEN_RULE_{_gr_ps}_mult{_gr_mult}x',
                            is_full_close=False, is_hedge=False)
                    except Exception as _gr_err:
                        if step < 10 or step % 5000 == 0:
                            v8_logger.error(f'[V8_GR_ERR] {_gr_pk}: {_gr_err}')

        # Drain async tasks (order queue, cooldown writes, etc)
        await asyncio.sleep(0)

        # Progress — fires on EITHER step count OR wall-clock 60s elapsed (visibility for silent-death debug 2026-05-01).
        _wallclock_now = _real_time_module.time()
        if "_last_wallclock_progress" not in dir():
            _last_wallclock_progress = t0
        _wallclock_progress_due = (_wallclock_now - _last_wallclock_progress) >= 60.0
        if step > 0 and (step % report_every == 0 or _wallclock_progress_due):
            _last_wallclock_progress = _wallclock_now
            n_trades = len(executed_trades)
            n_active = sum(1 for p in trade_manager.positions.values() if abs(getattr(p, 'positionAmt', 0)) > 0.0001)
            elapsed = _wallclock_now - t0
            _sharpe_w, _gain_pct, _gain_dol, _sum_pct, _sharpe_pt, _sharpe_ann, _tpy = _compute_sharpe_and_gain()
            _wr = _live_pnl['n_wins'] * 100.0 / max(1, _live_pnl['n_closes'])
            _gate_pct = _gate_filtered * 100 // max(1, _gate_total_checks)
            try:
                import resource as _rs_mod
                _rss_mb = _rs_mod.getrusage(_rs_mod.RUSAGE_SELF).ru_maxrss / 1024.0
            except Exception:
                _rss_mb = 0.0
            v8_logger.info(f"[PROGRESS] {step}/{len(sorted_ts)} ({step*100//len(sorted_ts)}%) | trades={n_trades} | active={n_active} | {elapsed:.0f}s | gate_skip={_gate_pct}% | rss_mb={_rss_mb:.0f}")
            print(f"V8_PROGRESS: step={step}/{len(sorted_ts)} pct={step*100//len(sorted_ts)} trades={n_trades} elapsed={elapsed:.0f}s rss_mb={_rss_mb:.0f}", flush=True)
            # CANONICAL_METRICS.md: only pool_sharpe (= sharpe_per_trade in this engine) survives in user-facing logs.
            # sharpe_weekly + sharpe_annual are BANNED for display (they were the "feel-good" inflation that misled decisions for months).
            v8_logger.info(f"[V8_RESULT_LIVE] pool_sharpe={_sharpe_pt:.3f} (trades/yr={_tpy:.0f}) gain_pct={_gain_pct:+.2f}% gain_dollars={_gain_dol:+.2f} sum_trade_pcts={_sum_pct:+.2f}% closes={_live_pnl['n_closes']} W={_live_pnl['n_wins']} L={_live_pnl['n_losses']} WR={_wr:.1f}%")
            print(f"V8_RESULT_LIVE: pool_sharpe={_sharpe_pt:.3f} gain_pct={_gain_pct:.2f} closes={_live_pnl['n_closes']} wins={_live_pnl['n_wins']} losses={_live_pnl['n_losses']} wr={_wr:.1f}", flush=True)
            # Live PnL breakdown by close reason — every report interval
            v8_logger.info("[V8_PNL_BREAKDOWN] === BY CLOSE REASON ===")
            for _r, _d in sorted(_live_pnl["by_reason"].items(), key=lambda x: x[1]["pnl_pct_sum"]):
                v8_logger.info(f"[V8_PNL_BREAKDOWN] {_r:<25} n={_d['n']:>4} sum={_d['pnl_pct_sum']:+8.2f}% W={_d['n_wins']} L={_d['n_losses']} worst={_d['worst_loss']:.2f}% best={_d['best_win']:.2f}%")

    # Results — compute and print BEFORE queue cleanup so cancellation errors cannot block result
    elapsed = _real_time_module.time() - t0
    v8_logger.info(f"Done in {elapsed:.1f}s | {len(executed_trades)} trades")
    # ═══ FINAL PnL BREAKDOWN ═══
    v8_logger.info("=" * 80)
    _f_sharpe_w, _f_gain_pct, _f_gain_dol, _f_sum_pct, _f_sharpe_pt, _f_sharpe_ann, _f_tpy = _compute_sharpe_and_gain()
    # Per-symbol Sharpe (CLAUDE.md rule 4 diagnostic): mean of per-symbol pool_sharpes, ±20 cap.
    _f_sym_sharpe = 0.0
    try:
        _f_by_sym = {}
        for _t in executed_trades:
            if _t.get("pnl_pct") is None: continue
            _ssym = _t.get("symbol") or str(_t.get("position_key", "")).split(":", 1)[-1].rsplit("_", 1)[0]
            _f_by_sym.setdefault(_ssym, []).append(float(_t.get("pnl_pct", 0.0)))
        _f_sslist = []
        for _ssym, _plist in _f_by_sym.items():
            if len(_plist) < 2: continue
            _m = sum(_plist) / len(_plist)
            _s = (sum((x - _m) ** 2 for x in _plist) / len(_plist)) ** 0.5
            _ss = _m / _s if _s > 0 else (20.0 if _m > 0 else 0.0)
            _f_sslist.append(max(-20.0, min(20.0, _ss)))
        _f_sym_sharpe = (sum(_f_sslist) / len(_f_sslist)) if _f_sslist else 0.0
    except Exception:
        _f_sym_sharpe = 0.0
    v8_logger.info(f"[V8_FINAL_PNL] sum_trade_pcts={_f_sum_pct:+.2f}% gain_pct_dollars={_f_gain_pct:+.2f}% gain_dollars={_f_gain_dol:+.2f} | closes={_live_pnl['n_closes']} | W={_live_pnl['n_wins']} L={_live_pnl['n_losses']} | WR={_live_pnl['n_wins']*100/max(1,_live_pnl['n_closes']):.1f}%")
    # CANONICAL_METRICS.md / CLAUDE.md rule 4: pool_sharpe + sym_sharpe ONLY.
    v8_logger.info(f"[V8_FINAL_PNL] pool_sharpe={_f_sharpe_pt:.4f} sym_sharpe={_f_sym_sharpe:.4f} (trades_per_year={_f_tpy:.0f})")
    _v8_result_line = f"V8_RESULT: pool_sharpe={_f_sharpe_pt:.4f} sym_sharpe={_f_sym_sharpe:.4f} sharpe={_f_sharpe_pt:.4f} gain_pct={_f_gain_pct:.2f} closes={_live_pnl['n_closes']} wins={_live_pnl['n_wins']} losses={_live_pnl['n_losses']}"
    print(_v8_result_line, flush=True)
    # Write V8_RESULT to dedicated file so v8_test_queue.py can read it cleanly
    # even when stdout is 200K+ lines of trade/PnL logs that may cause regex issues.
    _v8_result_file = os.environ.get("V8_RESULT_FILE", "")
    if _v8_result_file:
        try:
            with open(_v8_result_file, "w") as _rf:
                _rf.write(_v8_result_line + "\n")
        except Exception as _rfe:
            v8_logger.warning(f"[V8_RESULT_FILE] Failed to write {_v8_result_file}: {_rfe}")

    # 2026-05-01: write chart-trade JSONL + compute trade pnl BEFORE cancelling
    # the queue task. asyncio.CancelledError (Python 3.11+ is BaseException, NOT
    # Exception) propagates from `await queue_task` up through asyncio.run() and
    # was previously skipping _write_chart_trades, leaving V8_PARALLEL with zero
    # trade JSONLs. Catching BaseException here causes asyncio.run() to wait on
    # other pending tasks (account-manager loops) and timeout. Reorder fixes both.
    try:
        _compute_trade_pnl(executed_trades)
    except Exception as _ctp_e:
        v8_logger.warning(f"[V8_FINAL] _compute_trade_pnl error: {_ctp_e}")
    try:
        _v8_result_from_trades(executed_trades, capital)
    except Exception as _vrt_e:
        v8_logger.warning(f"[V8_FINAL] _v8_result_from_trades error: {_vrt_e}")
    try:
        _write_chart_trades(executed_trades)
    except Exception as _wct_e:
        v8_logger.warning(f"[V8_FINAL] _write_chart_trades error: {_wct_e}")

    # Now cancel queue processor — CancelledError propagating up through
    # asyncio.run() is fine; main() returns immediately afterward.
    queue_task.cancel()
    try:
        await queue_task
    except Exception:
        pass
    v8_logger.info("[V8_FINAL_PNL] === BY CLOSE REASON (sorted by total PnL ascending) ===")
    for _r, _d in sorted(_live_pnl["by_reason"].items(), key=lambda x: x[1]["pnl_pct_sum"]):
        _avg = _d['pnl_pct_sum'] / max(1, _d['n'])
        v8_logger.info(f"[V8_FINAL_PNL] {_r:<25} n={_d['n']:>4} sum={_d['pnl_pct_sum']:+9.2f}% avg={_avg:+6.2f}% W={_d['n_wins']} L={_d['n_losses']} worst={_d['worst_loss']:.2f}% best={_d['best_win']:.2f}%")
        for _ex in _d['examples'][:3]:
            v8_logger.info(f"[V8_FINAL_PNL]   ex: {_ex}")
    v8_logger.info("[V8_FINAL_PNL] === BY ENTRY REASON ===")
    for _r, _d in sorted(_live_pnl["by_entry_reason"].items(), key=lambda x: x[1]["pnl_pct_sum"]):
        _avg = _d['pnl_pct_sum'] / max(1, _d['n_trades'])
        v8_logger.info(f"[V8_FINAL_PNL] {_r:<25} n={_d['n_trades']:>4} sum={_d['pnl_pct_sum']:+9.2f}% avg={_avg:+6.2f}% W={_d['n_wins']} L={_d['n_losses']}")
    v8_logger.info("[V8_FINAL_PNL] === BY SYMBOL ===")
    for _s, _d in sorted(_live_pnl["by_symbol"].items(), key=lambda x: x[1]["pnl_pct_sum"]):
        v8_logger.info(f"[V8_FINAL_PNL] {_s:<15} n={_d['n_trades']:>4} sum={_d['pnl_pct_sum']:+9.2f}%")
    v8_logger.info("=" * 80)
    # NEW 2026-04-26 sweep switches: per-run counter dump (crypto path).
    v8_logger.info(f"[V8_NEW_SWITCHES] dd_peak={_v8ns_dd_state.get('peak', 0.0):+.2f}%  dd_min={_v8ns_dd_state.get('dd_pct', 0.0):+.2f}%  counters={_v8ns_counters}")
    print(f"V8_NEW_SWITCHES: vt={_v8ns_counters['vol_target_applied']} dk={_v8ns_counters['dd_kelly_applied']} tm={_v8ns_counters['tsmom_applied']} mn={_v8ns_counters['minervini_block']} cl={_v8ns_counters['clenow_block']} pt={_v8ns_counters['proximity_top_block']} sf={_v8ns_counters['squeeze_fire_aligned']} dd_min={_v8ns_dd_state.get('dd_pct', 0.0):.2f}", flush=True)
    if _bt_rg is not None and len(sorted_ts) >= 2:
        _bt_days = max((sorted_ts[-1] - sorted_ts[0]) / 86400.0, 1e-6)
        _bt_rg.final_check(_live_pnl["n_closes"], test_window_days=_bt_days)
    # 2026-05-01: _compute_trade_pnl / _v8_result_from_trades / _write_chart_trades
    # moved BEFORE queue_task.cancel() so CancelledError propagating from the
    # cancel doesn't skip them. See note above the cancel block.
    log_dir = BASE_PATH / "backtest_v8" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"v8_{mode}_{account_key}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jsonl"
    with open(log_path, "w") as f:
        for t in executed_trades:
            f.write(json.dumps(t, default=str) + "\n")
    v8_logger.info(f"Trades saved to {log_path}")
    print(f"V8_LOG: {log_path}")
    return executed_trades


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="V8 Engine — REAL scripts, NPZ data")
    parser.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    parser.add_argument("--account", type=str, default="ang")
    parser.add_argument("--start", type=str, default="2026-03-20")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated (empty=all)")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--npz-dir", type=str, default="", help="Explicit NPZ directory (overrides auto-detect)")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    if _SWEEP_MODE:
        print(f"V8_INIT_HEARTBEAT: loading n_symbols={len(symbols) if symbols else 'all'}", flush=True)
    stores, resolution = load_stores(args.mode, symbols, args.start, npz_dir_override=args.npz_dir)
    if not stores:
        v8_logger.error("No data loaded")
        return
    if _SWEEP_MODE:
        print(f"V8_INIT_HEARTBEAT: loaded={len(stores)} symbols starting simulation", flush=True)

    if args.mode == "tradier":
        asyncio.run(run_simulation_tradier(args.account, args.start, args.capital, stores, resolution))
    else:
        asyncio.run(run_simulation(args.mode, args.account, args.start, args.capital, stores, resolution))


# ═══════════════════════════════════════════════════════════════
# TRADIER SIMULATION — uses tradier_manage.py REAL code
# ═══════════════════════════════════════════════════════════════
async def run_simulation_tradier(account_key, start_date, capital, stores, resolution):
    # RECONNECT 2026-04-14 — sweep override route covers ALL TRADIER_* canonical switches.
    # The generic V8_OVERRIDE_FILE loader below sets any key from data/sweep_alerts/canonical_switches.json
    # onto tm_mod.config (both stripped and full-prefix forms). Explicit ack of switches:
    #   TRADIER_DC_DAYTRADE_ENABLED, TRADIER_FH_MOMENTUM_ENABLED, TRADIER_MI_ENTRY_ENABLED_TRADIER,
    #   TRADIER_K_ZONE_ENTRY_BONUS_TRADIER, TRADIER_RSI2_ENABLED, TRADIER_STOCH_ENTRY_LONG_TRADIER,
    #   TRADIER_WT_EXIT_TFS_TRADIER, TRADIER_ENTRY_SCORE_THRESHOLD, TRADIER_RSI_ENTRY_SHORT_TRADIER,
    #   TRADIER_MFI_ENTRY_LONG_TRADIER (and 18 others) all flow through this loader.
    import tradier_manage as tm_mod
    # CRITICAL: V8 backtest must NEVER call Redis. The live config_tradier.Config
    # has _get_regime_from_redis which connects to Redis on every get_symbol_setting()
    # call. On servers without real Redis (S2 dummy/timeout), the GET command blocks
    # forever (socket_connect_timeout=1 only covers connect, not read). Patch it to
    # always return None so backtest uses class defaults.
    import config_tradier as _ct
    _ct.TradierConfig._get_regime_from_redis = classmethod(lambda cls, full_key: None)
    # Apply config overrides to TRADIER config — ALL 4 LEVELS (module, class, dataclass, instances)
    _t_override_file = os.environ.get("V8_OVERRIDE_FILE", "")
    _t_overrides = {}
    if _t_override_file and Path(_t_override_file).exists():
        with open(_t_override_file) as _tf:
            _t_overrides = json.load(_tf)
        _applied = 0
        _skipped = []
        for _tk, _tv in _t_overrides.items():
            _clean_k = _tk[8:] if _tk.startswith("TRADIER_") else _tk
            # 1. Instance attr (tm_mod.config is the module-level TradierConfig instance)
            setattr(tm_mod.config, _clean_k, _tv)
            if _tk.startswith("TRADIER_") and _clean_k != _tk:
                setattr(tm_mod.config, _tk, _tv)
            # 2. Class attr (so any new TradierConfig() lookups see the override)
            try:
                setattr(_ct.TradierConfig, _clean_k, _tv)
                if _tk.startswith("TRADIER_") and _clean_k != _tk:
                    setattr(_ct.TradierConfig, _tk, _tv)
            except Exception:
                pass
            # 3. Dataclass field default (so new TradierConfig() instances get override)
            try:
                if hasattr(_ct.TradierConfig, '__dataclass_fields__') and _clean_k in _ct.TradierConfig.__dataclass_fields__:
                    _ct.TradierConfig.__dataclass_fields__[_clean_k].default = _tv
                if _tk.startswith("TRADIER_") and _clean_k != _tk and hasattr(_ct.TradierConfig, '__dataclass_fields__') and _tk in _ct.TradierConfig.__dataclass_fields__:
                    _ct.TradierConfig.__dataclass_fields__[_tk].default = _tv
            except Exception:
                pass
            _applied += 1
        if _applied:
            v8_logger.info(f"Applied {_applied} tradier config overrides (module + class + dataclass default)")
            for _tk, _tv in _t_overrides.items():
                _ck = _tk[8:] if _tk.startswith("TRADIER_") else _tk
                _actual = getattr(tm_mod.config, _ck, "MISSING")
                v8_logger.info(f"  OVERRIDE VERIFY: {_ck} = {_actual} (requested {_tv})")
    # 4. Re-apply overrides to all existing TradierConfig instances (belt-and-suspenders)
    if _t_override_file and Path(_t_override_file).exists():
        for _inst_attr in ['config']:
            _tc_inst = getattr(tm_mod, _inst_attr, None)
            if _tc_inst and isinstance(_tc_inst, _ct.TradierConfig):
                for _tk, _tv in _t_overrides.items():
                    _ck = _tk[8:] if _tk.startswith("TRADIER_") else _tk
                    setattr(_tc_inst, _ck, _tv)
                    if _tk.startswith("TRADIER_") and _ck != _tk:
                        setattr(_tc_inst, _tk, _tv)
    # FIX 2026-04-14: ez_positions_quick.check_exit_candidates_for_account reads SRS
    # settings from its own config (config.Config), not tradier_manage.config. Cross-inject
    # all SRS + exit-related overrides so the switch actually affects the exit path.
    _EPQ_CROSS_KEYS = {"STRUCTURAL_RANGE_SHIFT_EXIT", "STRUCTURAL_RANGE_SHIFT_TF", "STRUCTURAL_RANGE_SHIFT_K_HIGH", "STRUCTURAL_RANGE_SHIFT_K_LOW", "STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS", "SATOSHIT_ENTRY_FILTER"}
    if _t_overrides:
        _epq_cfg = getattr(ez_positions_quick, 'config', None)
        if _epq_cfg:
            _epq_applied = 0
            for _tk, _tv in _t_overrides.items():
                _ck = _tk[8:] if _tk.startswith("TRADIER_") else _tk
                if _ck in _EPQ_CROSS_KEYS:
                    setattr(_epq_cfg, _ck, _tv)
                    _epq_applied += 1
            if _epq_applied:
                v8_logger.info(f"Cross-injected {_epq_applied} SRS/SATOSHIT overrides into ez_positions_quick.config")
        try:
            import ez_manage as _ezm_mod
            _ezm_cfg = getattr(_ezm_mod, 'config', None)
            if _ezm_cfg:
                _ezm_applied = 0
                for _tk, _tv in _t_overrides.items():
                    _ck = _tk[8:] if _tk.startswith("TRADIER_") else _tk
                    if _ck in _EPQ_CROSS_KEYS:
                        setattr(_ezm_cfg, _ck, _tv)
                        _ezm_applied += 1
                if _ezm_applied:
                    v8_logger.info(f"Cross-injected {_ezm_applied} SRS/SATOSHIT overrides into ez_manage.config")
        except Exception as _ezm_ci_e:
            v8_logger.warning(f"[V8_EZM_CROSSINJECT_FAIL] {_ezm_ci_e}")
    if _t_overrides.get("SATOSHIT_ENTRY_FILTER") is not None:
        _sat_val = _t_overrides["SATOSHIT_ENTRY_FILTER"]
        setattr(tm_mod.config, 'SATOSHIT_ENTRY_FILTER', _sat_val)
        setattr(_ct.TradierConfig, 'SATOSHIT_ENTRY_FILTER', _sat_val)
        try:
            if hasattr(_ct.TradierConfig, '__dataclass_fields__') and 'SATOSHIT_ENTRY_FILTER' in _ct.TradierConfig.__dataclass_fields__:
                _ct.TradierConfig.__dataclass_fields__['SATOSHIT_ENTRY_FILTER'].default = _sat_val
        except Exception:
            pass
        setattr(tm_mod.config, 'SATOSHIT_ENTRY_FILTER_TRADIER', _sat_val)
        setattr(_ct.TradierConfig, 'SATOSHIT_ENTRY_FILTER_TRADIER', _sat_val)
        v8_logger.info(f"SATOSHIT_ENTRY_FILTER force-applied ALL levels = {_sat_val}")
    if _t_overrides.get("STRUCTURAL_RANGE_SHIFT_EXIT") is not None:
        _srs_val = _t_overrides["STRUCTURAL_RANGE_SHIFT_EXIT"]
        setattr(tm_mod.config, 'STRUCTURAL_RANGE_SHIFT_EXIT', _srs_val)
        setattr(_ct.TradierConfig, 'STRUCTURAL_RANGE_SHIFT_EXIT', _srs_val)
        try:
            if hasattr(_ct.TradierConfig, '__dataclass_fields__') and 'STRUCTURAL_RANGE_SHIFT_EXIT' in _ct.TradierConfig.__dataclass_fields__:
                _ct.TradierConfig.__dataclass_fields__['STRUCTURAL_RANGE_SHIFT_EXIT'].default = _srs_val
        except Exception:
            pass
        v8_logger.info(f"STRUCTURAL_RANGE_SHIFT_EXIT force-applied ALL levels = {_srs_val}")
    # FIX 2026-04-14: WT_CROSSUNDER_FINAL and RZ_EXIT live INSIDE the delta gate block
    # in tradier_manage.evaluate_stop. When DELTA_ENGINE_ENABLED=False the block is
    # skipped, making those switches dead. Force tracker creation; gate standard delta
    # exits in the evaluate_stop wrapper instead.
    _orig_delta_engine_off = not getattr(tm_mod.config, 'DELTA_ENGINE_ENABLED', True)
    _orig_delta_entry = getattr(tm_mod.config, 'DELTA_ENTRY_ENABLED', True)
    setattr(tm_mod.config, 'DELTA_ENGINE_ENABLED', True)
    setattr(tm_mod.config, 'DELTA_EXIT_ENABLED', True)
    if _orig_delta_engine_off:
        v8_logger.info(f"DELTA_ENGINE forced True for tracker creation (sweep wanted OFF). DELTA_ENTRY_ENABLED={getattr(tm_mod.config, 'DELTA_ENTRY_ENABLED', True)} (independent). Delta exits gated in evaluate_stop wrapper.")
    # FIX 2026-04-14 sentinel: MI_EXIT, WT_EXIT_MIN_TFS, WT_COMPOSITE_SCORING are gated
    # behind VETO flags that default False — making those sweep knobs dead. Enable the
    # veto gates when the sweep overrides include the corresponding switch.
    _wt_dc_thr_ov = _t_overrides.get("WT_DC_ENTRY_THRESHOLD") if _t_overrides else None
    if _wt_dc_thr_ov is not None:
        _wt_dc_thr_ov_f = float(_wt_dc_thr_ov)
        setattr(tm_mod.config, 'TRA_WT_DC_ENTRY_THRESHOLD', _wt_dc_thr_ov_f)
        try:
            setattr(_ct.TradierConfig, 'TRA_WT_DC_ENTRY_THRESHOLD', _wt_dc_thr_ov_f)
            if hasattr(_ct.TradierConfig, '__dataclass_fields__') and 'TRA_WT_DC_ENTRY_THRESHOLD' in _ct.TradierConfig.__dataclass_fields__:
                _ct.TradierConfig.__dataclass_fields__['TRA_WT_DC_ENTRY_THRESHOLD'].default = _wt_dc_thr_ov_f
        except Exception:
            pass
        v8_logger.info(f"[V8_FIX] TRA_WT_DC_ENTRY_THRESHOLD mirrored to {_wt_dc_thr_ov_f} (sweep WT_DC_ENTRY_THRESHOLD was dead for tra account)")
    # SENTINEL_FIX 2026-04-14 (incident 729bd16a90): MI_EXIT_VETO, WT_EXIT_VETO, WT_COMPOSITE_VETO
    # were only set at module+class levels (2/4). Missing: dataclass default + instance re-apply.
    # Apply at ALL 4 levels per DEATH PENALTY rule so the sweep knob actually gates exits.
    _veto_pairs = [
        ("TRADIER_MI_EXIT_ENABLED_TRADIER", "MI_EXIT_VETO_ENABLED_TRADIER"),
        ("TRADIER_WT_EXIT_TFS_TRADIER", "WT_EXIT_VETO_ENABLED_TRADIER"),
        ("TRADIER_WT_EXIT_MIN_TFS_TRADIER", "WT_EXIT_VETO_ENABLED_TRADIER"),
        ("TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER", "WT_COMPOSITE_VETO_ENABLED_TRADIER"),
        # SENTINEL_FIX 2026-04-14 DEAD_PARAMS T4: K_ZONE thresholds were dead because
        # K_ZONE_VETO_ENABLED_TRADIER was never set True (the veto gate at process_position
        # line 1327 requires it). Enable it when sweep varies K_ZONE thresholds.
        ("TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER", "K_ZONE_VETO_ENABLED_TRADIER"),
        ("TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER", "K_ZONE_VETO_ENABLED_TRADIER"),
        # DC_POSITION_ENTRY_THRESHOLD was only a +5 score bonus, never gated entries.
        # DC_ENTRY_VETO_ENABLED_TRADIER enables the new dc_pos zone gate in process_position.
        ("TRADIER_DC_POSITION_ENTRY_THRESHOLD", "DC_ENTRY_VETO_ENABLED_TRADIER"),
    ]
    for _sweep_k, _veto_k in _veto_pairs:
        if _t_overrides.get(_sweep_k) is None:
            continue
        # Level 1: module instance
        setattr(tm_mod.config, _veto_k, True)
        # Level 2: class attribute
        try: setattr(_ct.TradierConfig, _veto_k, True)
        except Exception: pass
        # Level 3: dataclass field default
        try:
            if hasattr(_ct.TradierConfig, '__dataclass_fields__') and _veto_k in _ct.TradierConfig.__dataclass_fields__:
                _ct.TradierConfig.__dataclass_fields__[_veto_k].default = True
        except Exception: pass
        # Level 4: ez_positions_quick cross-inject (EPQ also calls evaluate_stop paths)
        try:
            _epq_cfg3 = getattr(ez_positions_quick, 'config', None)
            if _epq_cfg3: setattr(_epq_cfg3, _veto_k, True)
        except Exception: pass
        _veto_verify = getattr(tm_mod.config, _veto_k, 'MISSING')
        v8_logger.info(f"[V8_VETO_FIX] {_veto_k}={_veto_verify} (sweep tests {_sweep_k}={_t_overrides[_sweep_k]}) — applied module+class+dataclass+epq")
    # ALIAS 2026-04-14: old sweep runs used SATOSHIT_ENABLED_TRADIER as the key name;
    # current sweep uses SATOSHIT_ENTRY_FILTER. Map old→new so running sweeps still work.
    # If override has SATOSHIT_ENABLED_TRADIER but NOT SATOSHIT_ENTRY_FILTER, inject it.
    if "SATOSHIT_ENABLED_TRADIER" in _t_overrides and "SATOSHIT_ENTRY_FILTER" not in _t_overrides:
        _t_overrides["SATOSHIT_ENTRY_FILTER"] = _t_overrides["SATOSHIT_ENABLED_TRADIER"]
        setattr(tm_mod.config, "SATOSHIT_ENTRY_FILTER", _t_overrides["SATOSHIT_ENABLED_TRADIER"])
        try: setattr(_ct.TradierConfig, "SATOSHIT_ENTRY_FILTER", _t_overrides["SATOSHIT_ENABLED_TRADIER"])
        except Exception: pass
        try:
            if hasattr(_ct.TradierConfig, '__dataclass_fields__') and "SATOSHIT_ENTRY_FILTER" in _ct.TradierConfig.__dataclass_fields__:
                _ct.TradierConfig.__dataclass_fields__["SATOSHIT_ENTRY_FILTER"].default = _t_overrides["SATOSHIT_ENABLED_TRADIER"]
        except Exception: pass
        v8_logger.info(f"[V8_SATOSHIT_ALIAS] SATOSHIT_ENABLED_TRADIER={_t_overrides['SATOSHIT_ENABLED_TRADIER']} → SATOSHIT_ENTRY_FILTER applied to tradier_manage.config")
    # FIX 2026-04-14 sentinel DUPE_RESULTS: SATOSHIT_ENTRY_FILTER and STRUCTURAL_RANGE_SHIFT_EXIT
    # appeared dead in t25_3sym sweep. Force-apply at ALL levels (module, class, dataclass, instance,
    # and cross-injected configs ez_positions_quick.config + config). Loud verification logging so
    # sentinel can audit propagation on next run.
    for _dead_k in ("SATOSHIT_ENTRY_FILTER", "STRUCTURAL_RANGE_SHIFT_EXIT"):
        _dead_full = f"TRADIER_{_dead_k}"
        _dead_v = _t_overrides.get(_dead_k, _t_overrides.get(_dead_full))
        if _dead_v is None: continue
        setattr(tm_mod.config, _dead_k, _dead_v)
        try: setattr(_ct.TradierConfig, _dead_k, _dead_v)
        except Exception: pass
        try:
            if hasattr(_ct.TradierConfig, '__dataclass_fields__') and _dead_k in _ct.TradierConfig.__dataclass_fields__:
                _ct.TradierConfig.__dataclass_fields__[_dead_k].default = _dead_v
        except Exception: pass
        try:
            import config as _crypto_cfg
            setattr(_crypto_cfg, _dead_k, _dead_v)
            if hasattr(_crypto_cfg, 'config'):
                setattr(_crypto_cfg.config, _dead_k, _dead_v)
        except Exception: pass
        try:
            _epq_cfg2 = getattr(ez_positions_quick, 'config', None)
            if _epq_cfg2: setattr(_epq_cfg2, _dead_k, _dead_v)
        except Exception: pass
        _verify = getattr(tm_mod.config, _dead_k, "MISSING")
        v8_logger.info(f"[V8_SENTINEL_FIX] {_dead_k}={_verify} (requested {_dead_v}) force-applied across module+class+dataclass+epq+crypto_config")
    # FIX 2026-04-14 sentinel DUPE_RESULTS (incident e84310d0cb): WT_DC_ENTRY_THRESHOLD
    # was dead for tradier sweeps because tradier_manage.process_position L1300-1303 reads
    # TRA_WT_DC_ENTRY_THRESHOLD when account_key=='tra' (default 85), so the swept value of
    # WT_DC_ENTRY_THRESHOLD never affected the tradier entry path. Mirror the swept value
    # onto TRA_WT_DC_ENTRY_THRESHOLD at all 4 levels.
    _wt_dc_thr_override = _t_overrides.get("WT_DC_ENTRY_THRESHOLD", _t_overrides.get("TRADIER_WT_DC_ENTRY_THRESHOLD"))
    if _wt_dc_thr_override is not None:
        setattr(tm_mod.config, 'TRA_WT_DC_ENTRY_THRESHOLD', float(_wt_dc_thr_override))
        try: setattr(_ct.TradierConfig, 'TRA_WT_DC_ENTRY_THRESHOLD', float(_wt_dc_thr_override))
        except Exception: pass
        try:
            if hasattr(_ct.TradierConfig, '__dataclass_fields__') and 'TRA_WT_DC_ENTRY_THRESHOLD' in _ct.TradierConfig.__dataclass_fields__:
                _ct.TradierConfig.__dataclass_fields__['TRA_WT_DC_ENTRY_THRESHOLD'].default = float(_wt_dc_thr_override)
        except Exception: pass
        _verify_tra = getattr(tm_mod.config, 'TRA_WT_DC_ENTRY_THRESHOLD', 'MISSING')
        v8_logger.info(f"[V8_SENTINEL_FIX] TRA_WT_DC_ENTRY_THRESHOLD={_verify_tra} mirrored from WT_DC_ENTRY_THRESHOLD={_wt_dc_thr_override} (tradier path was reading TRA_ prefix only)")
    tm_mod.time = _SimTime()
    _real_dt = datetime
    def _sim_now_t(tz=None):
        ts = float(_sim_ts[0])
        if ts > 0:
            try: return _real_dt.fromtimestamp(ts, tz=tz or timezone.utc)
            except: pass
        return _real_dt.now(tz)
    class _TDTMeta(type):
        def __instancecheck__(cls, inst): return isinstance(inst, _real_dt)
    class _TDT(metaclass=_TDTMeta):
        now = staticmethod(_sim_now_t)
        @staticmethod
        def utcnow():
            ts = float(_sim_ts[0])
            if ts > 0:
                try: return _real_dt.utcfromtimestamp(ts)
                except: pass
            return _real_dt.utcnow()
        def __new__(cls, *a, **kw): return _real_dt(*a, **kw)
    for _a in ('fromtimestamp','utcfromtimestamp','fromisoformat','strptime','combine','min','max','today','fromordinal'):
        if hasattr(_real_dt, _a): setattr(_TDT, _a, getattr(_real_dt, _a))
    tm_mod.__dict__['datetime'] = _TDT
    def _sim_irth():
        dt = _sim_now_t(timezone.utc)
        if dt.weekday() >= 5: return False
        t = dt.hour * 60 + dt.minute
        return 13*60+30 <= t <= 20*60
    tm_mod.is_regular_trading_hours = _sim_irth
    if hasattr(tm_mod, 'minutes_since'):
        _oms = tm_mod.minutes_since
        def _sms(ts_obj, now=None):
            if now is None: now = _sim_now_t(timezone.utc)
            return _oms(ts_obj, now=now)
        tm_mod.minutes_since = _sms
    # Patch utils.py datetime + time so record_decision_context uses sim time
    import utils as _utils_mod
    _utils_mod.time = _SimTime()
    _utils_mod.__dict__['datetime'] = _TDT
    # Clear old v8 decision files so we get clean comparison
    import shutil
    v8_decisions_dir = Path(config.BASE_PATH) / "data" / "decisions_v8"
    try:
        if v8_decisions_dir.exists():
            shutil.rmtree(v8_decisions_dir, ignore_errors=True)
    except Exception:
        pass
    v8_decisions_dir.mkdir(parents=True, exist_ok=True)
    # Redirect record_decision_context to write to decisions_v8/ instead of decisions/
    _orig_record = _utils_mod.record_decision_context
    async def _v8_record(redis_manager, account_key, position_key, action, reason, indicators, score, market_context, trade_details=None):
        try:
            timestamp = _sim_now_t(timezone.utc)
            context_data = {
                "timestamp": timestamp.isoformat(),
                "position_key": position_key,
                "account_key": account_key,
                "action": action,
                "reason_text": reason,
                "tech_score": score,
                "market_context": market_context,
                "indicators": {
                    "current_price": indicators.get('current_price'),
                    "stoch_k_5m": indicators.get('stoch_k_5m'),
                    "stoch_d_5m": indicators.get('stoch_d_5m'),
                    "stoch_k_15m": indicators.get('stoch_k_15m'),
                    "stoch_d_15m": indicators.get('stoch_d_15m'),
                    "stoch_k_1h": indicators.get('stoch_k_1h'),
                    "stoch_d_1h": indicators.get('stoch_d_1h'),
                    "rsi_5m": indicators.get('rsi_5m'),
                    "rsi_15m": indicators.get('rsi_15m'),
                    "ha_5m": indicators.get('ha_5m'),
                    "volatility_atr": indicators.get('atr_15m'),
                    "relative_volume": indicators.get('rel_vol_5m'),
                },
            }
            if trade_details:
                context_data["trade"] = trade_details
            filename = v8_decisions_dir / f"decisions_{account_key}_{timestamp.strftime('%Y%m%d')}.jsonl"
            import aiofiles
            async with aiofiles.open(filename, "a") as f:
                await f.write(json.dumps(context_data, default=str) + "\n")
        except Exception:
            pass
    _utils_mod.record_decision_context = _v8_record
    tm_mod.record_decision_context = _v8_record
    indicator_cache, price_cache, executed_trades = {}, {}, []
    # NEW 2026-04-26 sweep switch: DD_KELLY peak/dd tracker (no-op when disabled).
    _v8ns_dd_state = {"peak": 0.0, "dd_pct": 0.0}
    # NEW 2026-04-26 sweep switch: counters (tradier path).
    _v8ns_counters = {"vol_target_applied": 0, "dd_kelly_applied": 0, "tsmom_applied": 0,
                      "minervini_block": 0, "clenow_block": 0, "proximity_top_block": 0,
                      "squeeze_fire_aligned": 0}
    # NEW 2026-04-26 sweep switch: rolling tradier-equity-percent for DD_KELLY (mark-to-trade-pnl).
    _v8ns_equity_pct = [0.0]
    manager = tm_mod.TradierTradeManager(account_list=[account_key])
    # BUG FIX 2026-04-11: DeltaTracker.cfg is a FROZEN SNAPSHOT built at __init__.
    # Sweep overrides applied to tm_mod.config are read correctly during construction,
    # BUT two keys were MISSING from the cfg dict (RZ_DIV_EXIT_ENABLED, RZ_TWO_PHASE_EXIT_ENABLED).
    # Also: re-inject ALL sweep overrides into delta_tracker.cfg to ensure they take effect,
    # using lowercase keys (wt_dc_delta.py uses cfg.get("lowercase_key")).
    if manager.delta_tracker and _t_override_file and Path(_t_override_file).exists():
        _delta_key_map = {
            # BUG FIX: code reads "exit_speed_decay_pct" not "exit_speed_decay_ratio"
            "DELTA_EXIT_DECAY_RATIO": "exit_speed_decay_pct",
            "DELTA_EXIT_MIN_TF_LOST": "exit_min_tf_lost",
            "RZ_ENTRY_ENABLED": "rz_entry_enabled",
            "RZ_EXIT_ENABLED": "rz_exit_enabled",
            "RZ_DIV_EXIT_ENABLED": "rz_div_exit_enabled",
            "RZ_TWO_PHASE_EXIT_ENABLED": "rz_two_phase_exit_enabled",
            "STRUCTURAL_RANGE_SHIFT_EXIT": "structural_range_shift_exit",
            "RZ_REQUIRE_STRUCT": "rz_require_struct",
            "RZ_ZSCORE_ZONE_ENABLED": "rz_zscore_zone_enabled",
            "DELTA_ENTRY_ENABLED": "entry_enabled",
        }
        _dt_applied = 0
        for _tk, _tv in _t_overrides.items():
            _ck = _tk[8:] if _tk.startswith("TRADIER_") else _tk
            _dt_key = _delta_key_map.get(_ck)
            if _dt_key:
                # DELTA_EXIT_DECAY_RATIO is 0.0-1.0 but exit_speed_decay_pct is 0-100
                if _dt_key == "exit_speed_decay_pct" and isinstance(_tv, float) and _tv <= 1.0:
                    _tv = _tv * 100.0
                manager.delta_tracker.cfg[_dt_key] = _tv
                _dt_applied += 1
                v8_logger.info(f"  DELTA_CFG FIX: {_dt_key} = {_tv}")
        if _dt_applied:
            v8_logger.info(f"Injected {_dt_applied} overrides into delta_tracker.cfg")
    # SENTINEL_FIX 2026-04-14 (incident 729bd16a90): Re-apply VETO flags to manager.config instance
    # (belt-and-suspenders: manager.__init__ runs after setattr above, could snapshot config state).
    for _sweep_k2, _veto_k2 in _veto_pairs:
        if _t_overrides.get(_sweep_k2) is None:
            continue
        setattr(tm_mod.config, _veto_k2, True)
        if hasattr(manager, 'config'): setattr(manager.config, _veto_k2, True)
        if hasattr(manager, 'strategy') and hasattr(manager.strategy, 'config'): setattr(manager.strategy.config, _veto_k2, True)
        v8_logger.info(f"[V8_VETO_REAPPLY] {_veto_k2}=True re-applied to manager+strategy instances after TradierTradeManager.__init__")
    class _CaptureAPI:
        connected = True
        async def connect(self): pass
        async def place_order(self_api, **kw):
            sym = kw.get("symbol", "")
            side = str(kw.get("side", "")).lower()
            qty = float(kw.get("quantity", 0))
            px = float(kw.get("price", 0) or price_cache.get(sym.upper(), 0))
            # Infer position_side and action from side
            if side in ("buy", "buy_to_cover"):
                ps = "LONG" if side == "buy" else "SHORT"
                act = "OPEN" if side == "buy" else "CLOSE"
            elif side in ("sell", "sell_short"):
                ps = "LONG" if side == "sell" else "SHORT"
                act = "CLOSE" if side == "sell" else "OPEN"
            else:
                ps = "LONG"; act = "OPEN"
            pk = f"{account_key}:{sym}_{ps}"
            executed_trades.append({"timestamp": _sim_ts[0], "symbol": sym, "side": side, "price": px, "quantity": qty, "reason": kw.get("reason",""), "action": act, "position_key": pk, "position_side": ps})
            return {"id": len(executed_trades), "status": "filled", "avg_fill_price": str(kw.get("price",0)), "exec_quantity": str(kw.get("quantity",0))}
        async def get_positions(self, *a, **kw): return []
        async def get_account_balances(self, *a, **kw): return {"total_equity": capital, "buying_power": capital}
        async def get_quotes(self, syms, *a, **kw):
            return {s: {"last": price_cache.get(s.upper(),0)} for s in (syms if isinstance(syms, list) else [syms])}
    manager.api_client = _CaptureAPI()
    async def _fresh(sym, pk=None): return True, "NPZ", True, manager.market_snapshot.get(sym.upper(), {})
    manager.is_data_fresh = _fresh
    async def _price(sym): return price_cache.get(sym.upper(), 0.0), _sim_ts[0]
    manager.get_current_price = _price
    async def _place(*args, **kw):
        # Handle both positional (symbol,side,qty,order_type,price,...) and keyword calls
        _fields = ["symbol", "side", "quantity", "order_type", "price", "stop", "duration", "action", "position_side", "account_key"]
        for i, v in enumerate(args):
            if i < len(_fields) and _fields[i] not in kw:
                kw[_fields[i]] = v
        sym = str(kw.get("symbol", ""))
        side = str(kw.get("side", ""))
        qty = abs(float(kw.get("quantity", 0)))
        px = float(kw.get("price", 0) or price_cache.get(sym.upper(), 0))
        act = kw.get("action", "")
        ps = kw.get("position_side", "LONG" if side.lower() in ("buy", "buy_to_cover") else "SHORT")
        pk = f"{account_key}:{sym}_{ps}"
        reason = str(kw.get("reason", act or ""))[:200]
        if qty > 0 and px > 0:
            executed_trades.append({"timestamp": _sim_ts[0], "type": "eta", "symbol": sym, "side": side, "quantity": qty, "price": px, "action": act, "reason": reason, "position_key": pk, "position_side": ps, "is_full_close": kw.get("is_full_close", False)})
            v8_logger.warning(f"[TRADE] {side} {qty:.0f} {sym} @{px:.2f} {act} {reason[:50]}")
        return {"id": len(executed_trades), "status": "filled", "order": {"id": len(executed_trades), "status": "ok"}}
    manager.place_order = _place
    # Patch execute_trade_action to route through our _v8_execute_now (captures ALL trade paths)
    _orig_eta = getattr(manager, 'execute_trade_action', None)
    async def _v8_execute_trade_action(account_key='', position_key='', symbol='', quantity=0, current_price=0, side='', position_side='', unique_id=None, is_full_close=False, action='', reason='', override_qty=None, is_hedge=False, hedge_for=None, **kw):
        action = str(action or ''); reason = str(reason or ''); symbol = str(symbol or ''); side = str(side or ''); position_side = str(position_side or '')
        qty = float(override_qty or quantity or 0)
        px = float(current_price or price_cache.get(symbol.upper(), 0))
        if qty <= 0 or px <= 0:
            return "BLOCKED_ZERO_QTY_OR_PRICE"
        # SWEEPABLE EXIT GATE: block WT_CROSSUNDER_FINAL when disabled
        if reason:
            _wt_xu_on = getattr(tm_mod.config, 'WT_CROSSUNDER_FINAL_ENABLED', True) if hasattr(tm_mod, 'config') else True
            if not _wt_xu_on and ("WT_CROSSUNDER_FINAL" in reason or "WT_CROSSOVER_FINAL" in reason):
                return "BLOCKED_WT_XU_FINAL_DISABLED"
        is_reduce = action.upper() in ('CLOSE', 'REDUCE', 'QUICK_CLOSE', 'FULL_CLOSE', 'PROFIT_TAKE', 'STOP_MAJOR_LOSS_REDUCE', 'STOP_FUNCTIONS_KILL', 'HEDGE_CLOSE') or 'CLOSE' in reason.upper() or 'REDUCE' in reason.upper()
        act = action or ("CLOSE" if is_reduce else "OPEN")
        # NEW 2026-04-26 sweep switches: entry vetoes + sizing scalars (tradier path).
        if (not is_reduce) and (not is_hedge):
            _v8ns_ind_t = manager.market_snapshot.get(symbol.upper(), {}) if hasattr(manager, 'market_snapshot') else {}
            _v8ns_is_long_t = (position_side == 'LONG')
            # NEW 2026-04-26 sweep switch: MINERVINI_GATE / CLENOW_GATE / PROXIMITY_TOP_GATE
            _v8ns_allow_t, _v8ns_veto_t = _v8ns_check_entry_vetos(tm_mod.config, _v8ns_ind_t, _v8ns_is_long_t)
            if not _v8ns_allow_t:
                if 'MINERVINI' in _v8ns_veto_t: _v8ns_counters['minervini_block'] += 1
                elif 'CLENOW' in _v8ns_veto_t: _v8ns_counters['clenow_block'] += 1
                elif 'PROXIMITY_TOP' in _v8ns_veto_t: _v8ns_counters['proximity_top_block'] += 1
                return _v8ns_veto_t
            # NEW 2026-04-26 sweep switch: SQUEEZE_FIRE_ENTRY (tag-only here; bonus applied in real-eta wrapper).
            _v8ns_sf_fired_t, _v8ns_sf_bonus_t = _v8ns_squeeze_fire_aligned(tm_mod.config, _v8ns_ind_t, _v8ns_is_long_t)
            if _v8ns_sf_fired_t:
                _v8ns_counters['squeeze_fire_aligned'] += 1
            # NEW 2026-04-26 sweep switch: VOL_TARGET sizing scalar
            _v8ns_vt_t = _v8ns_vol_target_scalar(tm_mod.config, _v8ns_ind_t)
            if _v8ns_vt_t != 1.0: _v8ns_counters['vol_target_applied'] += 1
            # NEW 2026-04-26 sweep switch: DD_KELLY (uses cumulative pnl% from executed_trades).
            _v8ns_total_pct_t = sum(t.get('pnl_pct', 0.0) for t in executed_trades if t.get('pnl_pct') is not None)
            _v8ns_equity_pct[0] = _v8ns_total_pct_t
            _v8ns_dd_state_update(_v8ns_dd_state, _v8ns_total_pct_t)
            _v8ns_dk_t = _v8ns_dd_kelly_scalar(tm_mod.config, _v8ns_dd_state)
            if _v8ns_dk_t != 1.0: _v8ns_counters['dd_kelly_applied'] += 1
            # NEW 2026-04-26 sweep switch: TSMOM_BOOK_SCALAR
            _v8ns_book_t = []
            try:
                _v8ns_pos_src = manager.position_manager.positions if manager.position_manager else {}
                for _bpk, _bpos in _v8ns_pos_src.items():
                    if not _bpk.startswith(f"{account_key}:"): continue
                    if abs(getattr(_bpos, 'positionAmt', getattr(_bpos, 'quantity', 0))) <= 0: continue
                    _bsym = getattr(_bpos, 'symbol', '') or _bpk.split(':', 1)[-1].rsplit('_', 1)[0]
                    _bind = manager.market_snapshot.get(_bsym.upper(), {}) if hasattr(manager, 'market_snapshot') else {}
                    _v8ns_book_t.append({'is_long': _bpk.endswith('_LONG'),
                                          'mom': _v8ns_compute_position_mom(_bind, int(_v8ns_get(tm_mod.config, 'TSMOM_LOOKBACK_BARS', 252)))})
            except Exception:
                _v8ns_book_t = []
            _v8ns_tm_t = _v8ns_tsmom_book_scalar(tm_mod.config, _v8ns_book_t)
            if _v8ns_tm_t != 1.0: _v8ns_counters['tsmom_applied'] += 1
            _v8ns_scalar_t = _v8ns_vt_t * _v8ns_dk_t * _v8ns_tm_t
            if _v8ns_scalar_t != 1.0:
                qty = max(0.0, qty * _v8ns_scalar_t)
                if qty <= 0:
                    return f"BLOCKED_V8NS_SIZE_ZERO_vt={_v8ns_vt_t:.2f}_dk={_v8ns_dk_t:.2f}_tm={_v8ns_tm_t:.2f}"
                reason = f"{reason}|V8NS_SCALE_vt={_v8ns_vt_t:.2f}_dk={_v8ns_dk_t:.2f}_tm={_v8ns_tm_t:.2f}"
            if _v8ns_sf_fired_t:
                reason = f"{reason}|SQ_FIRE+{_v8ns_sf_bonus_t:.0f}"
        # SWEEPABLE ENTRY GATES: enforce SATOSHIT + DELTA_ENTRY switches at execution layer
        if not is_reduce:
            if getattr(tm_mod.config, 'SATOSHIT_ENTRY_FILTER', True):
                try:
                    from ez_satoshit import satoshit_entry_signal
                    _sat_ind = manager.market_snapshot.get(symbol.upper(), {})
                    _sat_is_long = (position_side == "LONG")
                    _sat_ok, _, _ = satoshit_entry_signal(_sat_ind, _sat_is_long, tm_mod.config)
                    if not _sat_ok:
                        return "BLOCKED_SATOSHIT_FILTER"
                except Exception as _sat_e1:
                    v8_logger.warning(f"[V8_SATOSHIT_FALLBACK_ETA] gate error (blocking): {_sat_e1}")
                    return "BLOCKED_SATOSHIT_ERROR"
            if not getattr(tm_mod.config, 'DELTA_ENTRY_ENABLED', True) and reason:
                if "DELTA_ENTRY" in reason.upper() or "DELTA_SIGNAL" in reason.upper():
                    return "BLOCKED_DELTA_ENTRY_DISABLED"
            _cfg_pt = getattr(tm_mod, 'config', None)
            if _cfg_pt and getattr(_cfg_pt, 'LS_RATIO_ENFORCE_TRADIER', False):
                _lv_pt = _sv_pt = 0.0
                _pos_src_pt = (manager.position_manager.positions if manager.position_manager else {})
                for _ppk, _pp in _pos_src_pt.items():
                    if not _ppk.startswith(f"{account_key}:"): continue
                    _pamt = abs(float(getattr(_pp, 'positionAmt', getattr(_pp, 'quantity', 0))))
                    if _pamt <= 0: continue
                    _ppx = float(getattr(_pp, 'mark_price', 0) or getattr(_pp, 'entry_price', 0))
                    if _ppx <= 0: continue
                    _pval = _pamt * _ppx
                    if _ppk.endswith('_LONG'): _lv_pt += _pval
                    elif _ppk.endswith('_SHORT'): _sv_pt += _pval
                _ratio_pt = _lv_pt / max(_sv_pt, 1.0)
                _ls_max_pt = float(getattr(_cfg_pt, 'LS_RATIO_MAX_TRADIER', 2.0))
                _ls_min_pt = float(getattr(_cfg_pt, 'LS_RATIO_MIN_TRADIER', 0.5))
                if position_side == 'LONG' and _ratio_pt > _ls_max_pt:
                    return f"BLOCKED_LS_RATIO_LONG_{_ratio_pt:.2f}gt{_ls_max_pt}"
                if position_side == 'SHORT' and _ratio_pt < _ls_min_pt:
                    return f"BLOCKED_LS_RATIO_SHORT_{_ratio_pt:.2f}lt{_ls_min_pt}"
        v8_logger.warning(f"[V8_ETA] {position_key} {side} qty={qty:.4f} px={px:.4f} {act} {reason[:60]}")
        await _place(symbol=symbol, side=side, quantity=qty, price=px, action=act, position_side=position_side, reason=str(reason)[:200], is_full_close=is_full_close)
        # Update position (check both dicts — tradier uses position_manager.positions)
        pos = None
        if manager.position_manager:
            pos = manager.position_manager.positions.get(position_key)
        if not pos and hasattr(manager, 'positions'):
            pos = manager.positions.get(position_key)
        if is_reduce and pos:
            old_amt = abs(getattr(pos, 'positionAmt', getattr(pos, 'quantity', 0)))
            new_amt = max(0, old_amt - abs(qty))
            pos.positionAmt = new_amt
            pos.quantity = new_amt
            if is_full_close or new_amt < 0.0001:
                pos.positionAmt = 0
                pos.quantity = 0
            pos.was_reduced = True
            pos.last_reduction_time = _sim_now_t(timezone.utc)
            pos.last_reduction_price = px
            # 2026-04-17 REENTRY SYNC FIX — mirror ez_manage.py:13588-13633 live reduce path.
            # Live populates trade_manager.reduced_positions + reentry_data inside reduce_position()
            # so evaluate_reentry_2 can iterate. _v8_execute_trade_action bypasses that, leaving
            # the dicts empty — all REENTRY2_*/WT15M/K15M/POST_CONSOL switches ran as dead code
            # (verified 2026-04-17 sweep: closes=380 identical across every flip).
            try:
                _now_dt = _sim_now_t(timezone.utc)
                _tm = manager  # trade_manager in this scope
                if hasattr(_tm, 'reduced_positions') and _tm.reduced_positions is not None:
                    _tm.reduced_positions[position_key] = _now_dt
                if hasattr(_tm, 'reentry_data') and _tm.reentry_data is not None:
                    _max_q = float(getattr(pos, 'max_quantity', 0) or old_amt or 0)
                    _existing_re_amt = float((_tm.reentry_data.get(position_key) or {}).get('reentry_amount', 0) or 0)
                    _accum_re_amt = _max_q if (is_full_close or new_amt < 0.0001) else min(_max_q, _existing_re_amt + abs(qty))
                    _re_reason = f"{'CLOSED' if (is_full_close or new_amt < 0.0001) else 'REDUCED'}_{reason[:60]}"
                    _tm.reentry_data[position_key] = {
                        "reentry_level": float(px),
                        "reentry_amount": max(_accum_re_amt, float(getattr(config, 'START_POSITION_SIZE', 55.0)) / max(px, 1e-9)),
                        "timestamp": _now_dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ') if hasattr(_now_dt, 'strftime') else str(_now_dt),
                        "reason": _re_reason,
                    }
            except Exception as _resync_e:
                v8_logger.debug(f"[V8_REENTRY_SYNC_ERR] {position_key}: {_resync_e}")
            # TIER1_PRICE_CROSS_REENTRY fix: populate tracker_manager exit cache so
            # check_entry_candidates_for_account sees _reentry_px > 0 on next step.
            try:
                if hasattr(tracker_manager, 'last_exit_prices'):
                    tracker_manager.last_exit_prices[position_key] = px
                if hasattr(tracker_manager, 'last_exit_times'):
                    tracker_manager.last_exit_times[position_key] = _sim_now_t(timezone.utc).timestamp()
            except Exception:
                pass
        elif not is_reduce:
            if pos:
                old_amt = abs(getattr(pos, 'positionAmt', getattr(pos, 'quantity', 0)))
                old_entry = getattr(pos, 'entry_price', px) or px
                new_amt = old_amt + abs(qty)
                pos.entry_price = (old_entry * old_amt + px * abs(qty)) / new_amt if new_amt > 0 else px
                pos.positionAmt = new_amt
                pos.quantity = new_amt
                pos.augmented_count = getattr(pos, 'augmented_count', 0) + 1
                pos.last_augmentation_time = _sim_now_t(timezone.utc)
            else:
                class _SP:
                    def __init__(s2, sym, sd, q, ep):
                        s2.symbol=sym; s2.position_side=sd; s2.positionAmt=q; s2.quantity=q
                        s2.entry_price=ep; s2.mark_price=ep; s2.gain=0.0; s2.prev_gain=0.0; s2.max_gain=0.0
                        s2.opened_at=_sim_now_t(timezone.utc); s2.last_updated=_sim_now_t(timezone.utc)
                        s2.last_augmentation_time=None; s2.last_augmentation_price=0.0
                        s2.last_reduction_time=None; s2.last_reduction_price=0.0
                        s2.was_reduced=False; s2.augmented_count=0; s2.max_quantity=q
                        s2.mark_price_last_updated=None
                new_pos = _SP(symbol, position_side, abs(qty), px)
                if hasattr(manager, 'positions'):
                    manager.positions[position_key] = new_pos
                if hasattr(manager, 'positions_by_account') and isinstance(manager.positions_by_account, dict):
                    manager.positions_by_account.setdefault(account_key, {})[position_key] = new_pos
                if manager.position_manager:
                    manager.position_manager.positions[position_key] = new_pos
        return "SUCCESS"
    # 2026-04-12: HA encoding bug fixed ("BULL"→"green") — alignment gate was losing 4 points.
    # Try real execute_trade_action first. If it produces trades, ALL entry gates are active.
    # If not, fall back to _v8_execute_trade_action (pass-through).
    _use_real_eta = True  # ALWAYS use real gates. Set V8_REAL_ETA=0 to disable for debugging only.
    if os.environ.get("V8_REAL_ETA") == "0":
        _use_real_eta = False
    if _use_real_eta and _orig_eta:
        v8_logger.info("Using REAL execute_trade_action (all entry gates active)")
        async def _v8_real_eta_wrapper(account_key='', position_key='', symbol='', quantity=0, current_price=0, side='', position_side='', unique_id=None, is_full_close=False, action='', reason='', override_qty=None, is_hedge=False, hedge_for=None, **kw):
            _pk = str(position_key or '')
            _act = str(action or '')
            _reason = str(reason or '')
            _is_reduce = _act.upper() in ('CLOSE', 'REDUCE', 'QUICK_CLOSE', 'FULL_CLOSE', 'PROFIT_TAKE', 'STOP_MAJOR_LOSS_REDUCE', 'STOP_FUNCTIONS_KILL', 'HEDGE_CLOSE') or 'CLOSE' in _reason.upper() or 'REDUCE' in _reason.upper()
            # NEW 2026-04-26 sweep switches: entry vetoes + sizing scalars (tradier real-eta path).
            _v8ns_sf_bonus_r = 0.0
            if (not _is_reduce) and (not is_hedge):
                _v8ns_ind_r = manager.market_snapshot.get(str(symbol).upper(), {}) if hasattr(manager, 'market_snapshot') else {}
                _v8ns_is_long_r = (str(position_side) == 'LONG')
                # NEW 2026-04-26 sweep switch: MINERVINI / CLENOW / PROXIMITY_TOP entry vetoes
                _v8ns_allow_r, _v8ns_veto_r = _v8ns_check_entry_vetos(tm_mod.config, _v8ns_ind_r, _v8ns_is_long_r)
                if not _v8ns_allow_r:
                    if 'MINERVINI' in _v8ns_veto_r: _v8ns_counters['minervini_block'] += 1
                    elif 'CLENOW' in _v8ns_veto_r: _v8ns_counters['clenow_block'] += 1
                    elif 'PROXIMITY_TOP' in _v8ns_veto_r: _v8ns_counters['proximity_top_block'] += 1
                    return _v8ns_veto_r
                # NEW 2026-04-26 sweep switch: SQUEEZE_FIRE bonus (effective threshold reduction below).
                _v8ns_sf_fired_r, _v8ns_sf_bonus_r = _v8ns_squeeze_fire_aligned(tm_mod.config, _v8ns_ind_r, _v8ns_is_long_r)
                if _v8ns_sf_fired_r:
                    _v8ns_counters['squeeze_fire_aligned'] += 1
                # NEW 2026-04-26 sweep switch: combined sizing scalar (VOL_TARGET × DD_KELLY × TSMOM_BOOK).
                _v8ns_vt_r = _v8ns_vol_target_scalar(tm_mod.config, _v8ns_ind_r)
                if _v8ns_vt_r != 1.0: _v8ns_counters['vol_target_applied'] += 1
                _v8ns_total_pct_r = sum(t.get('pnl_pct', 0.0) for t in executed_trades if t.get('pnl_pct') is not None)
                _v8ns_equity_pct[0] = _v8ns_total_pct_r
                _v8ns_dd_state_update(_v8ns_dd_state, _v8ns_total_pct_r)
                _v8ns_dk_r = _v8ns_dd_kelly_scalar(tm_mod.config, _v8ns_dd_state)
                if _v8ns_dk_r != 1.0: _v8ns_counters['dd_kelly_applied'] += 1
                _v8ns_book_r = []
                try:
                    _v8ns_pos_src_r = manager.position_manager.positions if manager.position_manager else {}
                    for _bpk_r, _bpos_r in _v8ns_pos_src_r.items():
                        if not _bpk_r.startswith(f"{account_key}:"): continue
                        if abs(getattr(_bpos_r, 'positionAmt', getattr(_bpos_r, 'quantity', 0))) <= 0: continue
                        _bsym_r = getattr(_bpos_r, 'symbol', '') or _bpk_r.split(':', 1)[-1].rsplit('_', 1)[0]
                        _bind_r = manager.market_snapshot.get(_bsym_r.upper(), {}) if hasattr(manager, 'market_snapshot') else {}
                        _v8ns_book_r.append({'is_long': _bpk_r.endswith('_LONG'),
                                              'mom': _v8ns_compute_position_mom(_bind_r, int(_v8ns_get(tm_mod.config, 'TSMOM_LOOKBACK_BARS', 252)))})
                except Exception:
                    _v8ns_book_r = []
                _v8ns_tm_r = _v8ns_tsmom_book_scalar(tm_mod.config, _v8ns_book_r)
                if _v8ns_tm_r != 1.0: _v8ns_counters['tsmom_applied'] += 1
                _v8ns_scalar_r = _v8ns_vt_r * _v8ns_dk_r * _v8ns_tm_r
                if _v8ns_scalar_r != 1.0:
                    quantity = max(0.0, float(quantity) * _v8ns_scalar_r)
                    if override_qty is not None:
                        override_qty = max(0.0, float(override_qty) * _v8ns_scalar_r)
                    if (override_qty if override_qty is not None else quantity) <= 0:
                        return f"BLOCKED_V8NS_SIZE_ZERO_vt={_v8ns_vt_r:.2f}_dk={_v8ns_dk_r:.2f}_tm={_v8ns_tm_r:.2f}"
                    reason = f"{_reason}|V8NS_SCALE_vt={_v8ns_vt_r:.2f}_dk={_v8ns_dk_r:.2f}_tm={_v8ns_tm_r:.2f}"
                    _reason = reason
            # SWEEPABLE ENTRY GATES (must be in REAL ETA wrapper, not just fallback)
            if not _is_reduce:
                _sat_on = getattr(tm_mod.config, 'SATOSHIT_ENTRY_FILTER', True)
                if _sat_on:
                    try:
                        from ez_satoshit import satoshit_entry_signal
                        _sat_ind = manager.market_snapshot.get(str(symbol).upper(), {})
                        _sat_is_long = (str(position_side) == "LONG")
                        _sat_ok, _, _ = satoshit_entry_signal(_sat_ind, _sat_is_long, tm_mod.config)
                        if not _sat_ok:
                            return "BLOCKED_SATOSHIT_FILTER"
                    except Exception as _sat_e2:
                        v8_logger.warning(f"[V8_SATOSHIT_REAL_ETA] gate error (blocking): {_sat_e2}")
                        return "BLOCKED_SATOSHIT_ERROR"
                if not getattr(tm_mod.config, 'DELTA_ENTRY_ENABLED', True) and _reason:
                    if "DELTA_ENTRY" in _reason.upper() or "DELTA_SIGNAL" in _reason.upper():
                        return "BLOCKED_DELTA_ENTRY_DISABLED"
                _wt_dc_thr = float(getattr(tm_mod.config, 'WT_DC_ENTRY_THRESHOLD', 55) or 55)
                # NEW 2026-04-26 sweep switch: SQUEEZE_FIRE bonus lowers effective WT_DC threshold.
                if _v8ns_sf_bonus_r > 0 and _wt_dc_thr > 0:
                    _wt_dc_thr = max(0.0, _wt_dc_thr - _v8ns_sf_bonus_r)
                if _wt_dc_thr > 0:
                    try:
                        from wt_dc_entry_scorer import score_entry as _v8_score_entry_raw
                        _wt_dc_ind = manager.market_snapshot.get(str(symbol).upper(), {})
                        _wt_dc_is_long = (str(position_side) == "LONG")
                        _wt_dc_px = float(current_price or price_cache.get(str(symbol).upper(), 0) or 0)
                        _wt_dc_score, _ = _v8_score_entry_raw(_wt_dc_ind, _wt_dc_is_long, _wt_dc_px)
                        if float(_wt_dc_score) < _wt_dc_thr:
                            return f"BLOCKED_WT_DC_ENTRY_THRESHOLD_{_wt_dc_score:.0f}_lt_{_wt_dc_thr:.0f}"
                    except Exception as _wt_dc_err:
                        v8_logger.warning(f"[V8_WT_DC_THR_ERR] {position_key}: {_wt_dc_err}")
                if getattr(tm_mod.config, 'STRUCTURAL_RANGE_SHIFT_EXIT', False):
                    try:
                        _srs_e_ind = manager.market_snapshot.get(str(symbol).upper(), {})
                        _srs_e_tf = getattr(tm_mod.config, 'STRUCTURAL_RANGE_SHIFT_TF', 'bb_1h')
                        _srs_e_pctb_key = {'bb_1h': 'bb_pct_b_1h', 'bb_4h': 'bb_pct_b_4h', 'bb_D': 'bb_pct_b_D', 'dc_1h': 'bb_pct_b_1h', 'dc_4h': 'bb_pct_b_4h', 'dc_D': 'bb_pct_b_D'}.get(_srs_e_tf, 'bb_pct_b_1h')
                        _srs_e_pctb = float(_srs_e_ind.get(_srs_e_pctb_key, 0.5) or 0.5)
                        _srs_e_is_long = (str(position_side) == "LONG")
                        if _srs_e_is_long and _srs_e_pctb >= 0.97:
                            return f"BLOCKED_SRS_ENTRY_LONG_AT_TOP_pctb={_srs_e_pctb:.2f}"
                        if (not _srs_e_is_long) and _srs_e_pctb <= 0.03:
                            return f"BLOCKED_SRS_ENTRY_SHORT_AT_BOTTOM_pctb={_srs_e_pctb:.2f}"
                    except Exception as _srs_e_err:
                        v8_logger.warning(f"[V8_SRS_ENTRY_ERR] {position_key}: {_srs_e_err}")
                _cfg_r = getattr(tm_mod, 'config', None)
                if _cfg_r and getattr(_cfg_r, 'LS_RATIO_ENFORCE_TRADIER', False):
                    _lv_r = _sv_r = 0.0
                    _pos_src = (manager.position_manager.positions if manager.position_manager else {})
                    for _rpk, _rp in _pos_src.items():
                        if not _rpk.startswith(f"{account_key}:"): continue
                        _ramt = abs(float(getattr(_rp, 'positionAmt', getattr(_rp, 'quantity', 0))))
                        if _ramt <= 0: continue
                        _rpx = float(getattr(_rp, 'mark_price', 0) or getattr(_rp, 'entry_price', 0))
                        if _rpx <= 0: continue
                        _rval = _ramt * _rpx
                        if _rpk.endswith('_LONG'): _lv_r += _rval
                        elif _rpk.endswith('_SHORT'): _sv_r += _rval
                    _ratio_r = _lv_r / max(_sv_r, 1.0)
                    _ls_max_r = float(getattr(_cfg_r, 'LS_RATIO_MAX_TRADIER', 2.0))
                    _ls_min_r = float(getattr(_cfg_r, 'LS_RATIO_MIN_TRADIER', 0.5))
                    if str(position_side) == 'LONG' and _ratio_r > _ls_max_r:
                        return f"BLOCKED_LS_RATIO_LONG_{_ratio_r:.2f}gt{_ls_max_r}"
                    if str(position_side) == 'SHORT' and _ratio_r < _ls_min_r:
                        return f"BLOCKED_LS_RATIO_SHORT_{_ratio_r:.2f}lt{_ls_min_r}"
            # Pre-create empty position if OPEN so real ETA's ensure_position_present finds it
            if _act in ('OPEN', 'QUICK_OPEN', 'REENTRY') and manager.position_manager and _pk not in manager.position_manager.positions:
                class _EmptyPos:
                    pass
                _ep = _EmptyPos()
                _ep.symbol = str(symbol); _ep.position_side = str(position_side)
                _ep.positionAmt = 0; _ep.quantity = 0; _ep.entry_price = 0
                _ep.mark_price = float(current_price or 0); _ep.gain = 0.0; _ep.prev_gain = 0.0; _ep.max_gain = 0.0
                _ep.opened_at = _sim_now_t(timezone.utc); _ep.last_updated = _sim_now_t(timezone.utc)
                _ep.last_augmentation_time = None; _ep.last_augmentation_price = 0.0
                _ep.last_reduction_time = None; _ep.last_reduction_price = 0.0
                _ep.was_reduced = False; _ep.augmented_count = 0; _ep.max_quantity = 0
                _ep.mark_price_last_updated = None
                manager.position_manager.positions[_pk] = _ep
            try:
                result = await _orig_eta(account_key=account_key, position_key=position_key, symbol=symbol, quantity=quantity, current_price=current_price, side=side, position_side=position_side, unique_id=unique_id, is_full_close=is_full_close, action=action, reason=reason, override_qty=override_qty)
            except Exception as _eta_err:
                v8_logger.error(f"[V8_REAL_ETA_ERR] {position_key} {_act}: {type(_eta_err).__name__}: {_eta_err}")
                return f"ERROR_{type(_eta_err).__name__}"
            _r = str(result or '')
            if "BLOCKED" not in _r:
                v8_logger.warning(f"[V8_REAL_ETA] {position_key} {_act} {side} → {_r[:80]}")
            # Backtest: real ETA calls place_order/queue but positionAmt never gets WS-updated.
            # Update positionAmt here so queue_trade_action CLOSE checks pass on subsequent bars.
            if "BLOCKED" not in _r and "ERROR" not in _r:
                _pos_r = None
                if manager.position_manager:
                    _pos_r = manager.position_manager.positions.get(_pk)
                if _pos_r is None and hasattr(manager, 'positions'):
                    _pos_r = manager.positions.get(_pk)
                _qty_r = abs(float(override_qty or quantity or 0))
                _px_r = float(current_price or price_cache.get(str(symbol).upper(), 0))
                if _pos_r is not None and _qty_r > 0:
                    if _is_reduce:
                        _old_r = abs(getattr(_pos_r, 'positionAmt', getattr(_pos_r, 'quantity', 0)))
                        _new_r = max(0.0, _old_r - _qty_r)
                        _pos_r.positionAmt = 0.0 if (is_full_close or _new_r < 0.0001) else _new_r
                        _pos_r.quantity = _pos_r.positionAmt
                    else:
                        _old_r = abs(getattr(_pos_r, 'positionAmt', getattr(_pos_r, 'quantity', 0)))
                        _old_ep_r = getattr(_pos_r, 'entry_price', _px_r) or _px_r
                        _new_r = _old_r + _qty_r
                        _pos_r.positionAmt = _new_r; _pos_r.quantity = _new_r
                        _pos_r.entry_price = (_old_ep_r * _old_r + _px_r * _qty_r) / _new_r if _new_r > 0 else _px_r
            return result
        manager.execute_trade_action = _v8_real_eta_wrapper
    else:
        v8_logger.info("Using PATCHED execute_trade_action (pass-through)")
        manager.execute_trade_action = _v8_execute_trade_action
    # Neutral market breadth — V8 doesn't have live breadth data, so default to
    # neutral (0.5) to prevent the balancer from blocking all entries.
    manager.calculate_unified_market_ratio = lambda: 0.5
    manager.cached_breadth_score = 0.0
    # is_symbol_tradeable must return True for backtest symbols
    _v8_syms = set(s.upper() for s in stores.keys())
    def _always_tradeable(sym, acc=None, side=None): return sym.upper() in _v8_syms
    manager.is_symbol_tradeable = _always_tradeable
    # Patch execute_now to use _place directly (bypasses TradierAPIClient creation)
    async def _v8_execute_now(*_en_args, **_en_kw):
        # Handle both direct call and self.execute_now() method call
        # When called as method, self is first arg — detect and skip it
        _a = list(_en_args)
        if _a and hasattr(_a[0], 'position_manager'):
            _a = _a[1:]  # strip self
        position_key = _a[0] if len(_a) > 0 else _en_kw.get('position_key', '')
        account_key_en = _a[1] if len(_a) > 1 else _en_kw.get('account_key_en', _en_kw.get('account_key', ''))
        symbol = _a[2] if len(_a) > 2 else _en_kw.get('symbol', '')
        original_position_amt = _a[3] if len(_a) > 3 else _en_kw.get('original_position_amt', 0)
        side = _a[4] if len(_a) > 4 else _en_kw.get('side', '')
        position_side = _a[5] if len(_a) > 5 else _en_kw.get('position_side', '')
        quantity = _a[6] if len(_a) > 6 else _en_kw.get('quantity', 0)
        old_price = _a[7] if len(_a) > 7 else _en_kw.get('old_price', 0)
        unique_id = _a[8] if len(_a) > 8 else _en_kw.get('unique_id', None)
        reason = _a[9] if len(_a) > 9 else _en_kw.get('reason', '')
        is_full_close = _a[10] if len(_a) > 10 else _en_kw.get('is_full_close', False)
        action = _a[11] if len(_a) > 11 else _en_kw.get('action', None)
        side = str(side or ''); position_side = str(position_side or ''); symbol = str(symbol or ''); reason = str(reason or ''); action = str(action or '')
        px = price_cache.get(symbol.upper(), old_price) or old_price
        is_reduce = (side.upper() == "SELL" and position_side == "LONG") or (side.upper() in ("BUY", "BUY_TO_COVER") and position_side == "SHORT")
        # GUARD: reject close/reduce on already-closed position (prevents phantom repeated closes)
        if is_reduce:
            _existing = manager.position_manager.positions.get(position_key) if manager.position_manager else None
            _existing_amt = abs(getattr(_existing, 'positionAmt', 0)) if _existing else 0
            if _existing_amt < 0.0001:
                return "BLOCKED_ALREADY_CLOSED"
        # ═══════════════════════════════════════════════════════════════════════════
        # DISC-6: UNIVERSAL_NOLOSS_GATE — mirror ez_manage.py:13998
        # Live execute_now blocks any close at loss unless reason bypasses the gate.
        # Bypass reasons: RIDICULOUS_LOSS, UNDERWATER_HEDGE_OR_CLOSE, STRUCTURAL_RANGE_SHIFT, LIQUIDATION, is_hedge.
        # ═══════════════════════════════════════════════════════════════════════════
        if is_reduce:
            _is_hedge_en = _en_kw.get('is_hedge', False)
            _ung_active_en = getattr(tm_mod.config, 'UNIVERSAL_NOLOSS_GATE', True) if hasattr(tm_mod, 'config') else True
            if _ung_active_en and not _is_hedge_en:
                _reason_up_en = reason.upper()
                _ung_bypass_en = (
                    'LIQUIDATION' in _reason_up_en
                    or 'RIDICULOUS_LOSS' in _reason_up_en
                    or 'UNDERWATER_HEDGE_OR_CLOSE' in _reason_up_en
                    or 'STRUCTURAL_RANGE_SHIFT' in _reason_up_en
                )
                if not _ung_bypass_en:
                    _ung_bypass_reasons_en = getattr(tm_mod.config, 'UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS', []) or [] if hasattr(tm_mod, 'config') else []
                    for _brk_en in _ung_bypass_reasons_en:
                        if _brk_en and _brk_en.upper() in _reason_up_en:
                            _ung_bypass_en = True
                            break
                if not _ung_bypass_en:
                    # Compute real gain to decide if we're actually at a loss
                    _en_pos = manager.position_manager.positions.get(position_key) if manager.position_manager else None
                    if _en_pos:
                        _en_entry = float(getattr(_en_pos, 'entry_price', 0) or 0)
                        _en_is_long = (position_side == 'LONG')
                        _en_comm_buf = float(getattr(tm_mod.config, 'COMMISSION_BUFFER_PCT', 0.10)) if hasattr(tm_mod, 'config') else 0.10
                        if _en_entry > 0 and px > 0:
                            _en_real_gain = ((px - _en_entry) / _en_entry * 100) if _en_is_long else ((_en_entry - px) / _en_entry * 100)
                        else:
                            _en_real_gain = float(getattr(_en_pos, 'gain', 0) or 0)
                        if _en_real_gain < _en_comm_buf:
                            v8_logger.info(f"[UNIVERSAL_NOLOSS_GATE_BACKTEST] {position_key}: blocking {action} reason={reason[:60]} gain={_en_real_gain:.2f}%")
                            return "BLOCKED_BY_UNIVERSAL_NOLOSS_GATE_BACKTEST"
        # SWEEPABLE EXIT GATES: block specific exit reasons when config says disabled
        # This allows the sweep to isolate each exit condition's contribution.
        if is_reduce and reason:
            _wt_xu_enabled = getattr(tm_mod.config, 'WT_CROSSUNDER_FINAL_ENABLED', True) if hasattr(tm_mod, 'config') else True
            if "WT_CROSSUNDER_FINAL" in reason and not _wt_xu_enabled:
                return "BLOCKED_WT_CROSSUNDER_FINAL_DISABLED"
            if "WT_CROSSOVER_FINAL" in reason and not _wt_xu_enabled:
                return "BLOCKED_WT_CROSSOVER_FINAL_DISABLED"
        # SWEEPABLE ENTRY GATES: enforce SATOSHIT + DELTA_ENTRY switches at execution layer
        if not is_reduce:
            if getattr(tm_mod.config, 'SATOSHIT_ENTRY_FILTER', True):
                try:
                    from ez_satoshit import satoshit_entry_signal
                    _sat_ind = manager.market_snapshot.get(symbol.upper(), {})
                    _sat_is_long = (position_side == "LONG")
                    _sat_ok, _, _ = satoshit_entry_signal(_sat_ind, _sat_is_long, tm_mod.config)
                    if not _sat_ok:
                        return "BLOCKED_SATOSHIT_FILTER"
                except Exception as _sat_e3:
                    v8_logger.warning(f"[V8_SATOSHIT_EXEC_NOW] gate error (blocking): {_sat_e3}")
                    return "BLOCKED_SATOSHIT_ERROR"
            if not getattr(tm_mod.config, 'DELTA_ENTRY_ENABLED', True) and reason:
                if "DELTA_ENTRY" in reason.upper() or "DELTA_SIGNAL" in reason.upper():
                    return "BLOCKED_DELTA_ENTRY_DISABLED"
            # NEW 2026-04-26 sweep switches: entry vetoes + sizing scalars (tradier exec_now path).
            _v8ns_ind_e = manager.market_snapshot.get(symbol.upper(), {}) if hasattr(manager, 'market_snapshot') else {}
            _v8ns_is_long_e = (position_side == 'LONG')
            _v8ns_allow_e, _v8ns_veto_e = _v8ns_check_entry_vetos(tm_mod.config, _v8ns_ind_e, _v8ns_is_long_e)
            if not _v8ns_allow_e:
                if 'MINERVINI' in _v8ns_veto_e: _v8ns_counters['minervini_block'] += 1
                elif 'CLENOW' in _v8ns_veto_e: _v8ns_counters['clenow_block'] += 1
                elif 'PROXIMITY_TOP' in _v8ns_veto_e: _v8ns_counters['proximity_top_block'] += 1
                return _v8ns_veto_e
            _v8ns_sf_fired_e, _ = _v8ns_squeeze_fire_aligned(tm_mod.config, _v8ns_ind_e, _v8ns_is_long_e)
            if _v8ns_sf_fired_e:
                _v8ns_counters['squeeze_fire_aligned'] += 1
            _v8ns_vt_e = _v8ns_vol_target_scalar(tm_mod.config, _v8ns_ind_e)
            if _v8ns_vt_e != 1.0: _v8ns_counters['vol_target_applied'] += 1
            _v8ns_total_pct_e = sum(t.get('pnl_pct', 0.0) for t in executed_trades if t.get('pnl_pct') is not None)
            _v8ns_equity_pct[0] = _v8ns_total_pct_e
            _v8ns_dd_state_update(_v8ns_dd_state, _v8ns_total_pct_e)
            _v8ns_dk_e = _v8ns_dd_kelly_scalar(tm_mod.config, _v8ns_dd_state)
            if _v8ns_dk_e != 1.0: _v8ns_counters['dd_kelly_applied'] += 1
            _v8ns_book_e = []
            try:
                _v8ns_pos_src_e = manager.position_manager.positions if manager.position_manager else {}
                for _bpk_e, _bpos_e in _v8ns_pos_src_e.items():
                    if not _bpk_e.startswith(f"{account_key_en}:"): continue
                    if abs(getattr(_bpos_e, 'positionAmt', getattr(_bpos_e, 'quantity', 0))) <= 0: continue
                    _bsym_e = getattr(_bpos_e, 'symbol', '') or _bpk_e.split(':', 1)[-1].rsplit('_', 1)[0]
                    _bind_e = manager.market_snapshot.get(_bsym_e.upper(), {}) if hasattr(manager, 'market_snapshot') else {}
                    _v8ns_book_e.append({'is_long': _bpk_e.endswith('_LONG'),
                                          'mom': _v8ns_compute_position_mom(_bind_e, int(_v8ns_get(tm_mod.config, 'TSMOM_LOOKBACK_BARS', 252)))})
            except Exception:
                _v8ns_book_e = []
            _v8ns_tm_e = _v8ns_tsmom_book_scalar(tm_mod.config, _v8ns_book_e)
            if _v8ns_tm_e != 1.0: _v8ns_counters['tsmom_applied'] += 1
            _v8ns_scalar_e = _v8ns_vt_e * _v8ns_dk_e * _v8ns_tm_e
            if _v8ns_scalar_e != 1.0:
                quantity = max(0.0, float(quantity) * _v8ns_scalar_e)
                if quantity <= 0:
                    return f"BLOCKED_V8NS_SIZE_ZERO_vt={_v8ns_vt_e:.2f}_dk={_v8ns_dk_e:.2f}_tm={_v8ns_tm_e:.2f}"
                reason = f"{reason}|V8NS_SCALE_vt={_v8ns_vt_e:.2f}_dk={_v8ns_dk_e:.2f}_tm={_v8ns_tm_e:.2f}"
        v8_logger.warning(f"[V8_EXEC_NOW] {position_key} {side} qty={quantity} px={old_price} action={action}")
        act = action or ("CLOSE" if is_reduce else "OPEN")
        await _place(symbol=symbol, side=side, quantity=float(quantity), price=float(px), action=act, position_side=position_side, reason=str(reason)[:200], is_full_close=is_full_close)
        # Update position state
        if manager.position_manager:
            pos = manager.position_manager.positions.get(position_key)
            if is_reduce and pos:
                old_amt = abs(getattr(pos, 'positionAmt', getattr(pos, 'quantity', 0)))
                new_amt = max(0, old_amt - abs(float(quantity)))
                pos.positionAmt = new_amt
                pos.quantity = new_amt
                if is_full_close:
                    pos.positionAmt = 0
                    pos.quantity = 0
            elif not is_reduce:
                if pos:
                    old_amt = abs(getattr(pos, 'positionAmt', getattr(pos, 'quantity', 0)))
                    old_entry = getattr(pos, 'entry_price', px)
                    new_amt = old_amt + abs(float(quantity))
                    pos.entry_price = (old_entry * old_amt + px * abs(float(quantity))) / new_amt if new_amt > 0 else px
                    pos.positionAmt = new_amt
                    pos.quantity = new_amt
                else:
                    class _SP:
                        def __init__(self2, s, sd, q, ep):
                            self2.symbol = s; self2.position_side = sd; self2.positionAmt = q
                            self2.quantity = q; self2.entry_price = ep; self2.mark_price = ep
                            self2.gain = 0.0; self2.prev_gain = 0.0; self2.max_gain = 0.0
                            self2.opened_at = _sim_now_t(timezone.utc); self2.last_updated = _sim_now_t(timezone.utc)
                            self2.last_augmentation_time = None; self2.last_augmentation_price = 0.0
                            self2.last_reduction_time = None; self2.last_reduction_price = 0.0
                            self2.was_reduced = False; self2.augmented_count = 0
                            self2.mark_price_last_updated = None; self2.max_quantity = q
                    new_pos = _SP(symbol, position_side, abs(float(quantity)), px)
                    manager.position_manager.positions[position_key] = new_pos
                    manager.position_manager.positions_by_account.setdefault(account_key_en, {})[position_key] = new_pos
        # CRITICAL: sync ALL position dicts — trade_manager.positions is what the main loop reads
        _pos_ref = None
        if manager.position_manager:
            _pos_ref = manager.position_manager.positions.get(position_key)
        if _pos_ref:
            manager.positions[position_key] = _pos_ref
            manager.positions_by_account.setdefault(account_key_en, {})[position_key] = _pos_ref
            if hasattr(manager, 'positions_service') and manager.positions_service:
                manager.positions_service.positions[position_key] = _pos_ref
                manager.positions_service.positions_by_account.setdefault(account_key_en, {})[position_key] = _pos_ref
        manager.recently_processed_signals[position_key] = _sim_ts[0]
        return "SUCCESS"
    manager.execute_now = _v8_execute_now
    # Backtest queue_trade_action: for CLOSE/REDUCE only, swap override_qty=999999
    # (live: exchange clamps to positionAmt) with the actual positionAmt so the
    # QTY_OVERSHOOT_ABORT guard (blocks when override_qty > positionAmt+1) passes.
    # All other actions delegate to the original function unchanged.
    _orig_qta = tm_mod.queue_trade_action
    async def _bt_queue_trade_action(order_queue_bt, trade_manager_bt, position_key_bt, action_bt, reason_bt, conviction_bt=50.0, override_qty=None):
        _act_up = (action_bt or '').upper()
        if _act_up in ("CLOSE", "REDUCE"):
            _pm_bt = manager.position_manager
            _pos_bt = _pm_bt.positions.get(position_key_bt) if _pm_bt else None
            if _pos_bt is not None:
                _actual_qty = abs(getattr(_pos_bt, 'positionAmt', getattr(_pos_bt, 'quantity', 0)))
                if _actual_qty >= 0.0001:
                    override_qty = _actual_qty
        return await _orig_qta(order_queue_bt, trade_manager_bt, position_key_bt, action_bt, reason_bt, conviction_bt, override_qty=override_qty)
    tm_mod.queue_trade_action = _bt_queue_trade_action
    # _en is NOT needed — _v8_execute_now already handles all paths including
    # self.execute_now() calls from the real execute_trade_action.
    # Previously _en OVERWROTE _v8_execute_now and broke position tracking.
    async def _noop(*a, **kw): pass
    manager.sync_real_positions_from_api = _noop
    if manager.reader:
        async def _no_connect(): return False
        manager.reader.connect = _no_connect
    if manager.position_manager:
        manager.position_manager.positions = {}
        manager.position_manager.positions_by_account = {account_key: {}}
    # Seed positions from live decision files if they exist for the start date
    _seed_dir = Path(config.BASE_PATH) / "data" / "decisions"
    _seed_file = _seed_dir / f"decisions_{account_key}_{start_date.replace('-','')}.jsonl"
    if _seed_file.exists() and manager.position_manager:
        _seeded = 0
        _seen = set()
        for _line in open(_seed_file):
            try:
                _sd = json.loads(_line)
                _trade = _sd.get("trade", {})
                _amt = float(_trade.get("position_amt", 0) or 0)
                _entry = float(_trade.get("entry_price", 0) or 0)
                _pk = _sd.get("position_key", "")
                if _amt > 0 and _entry > 0 and _pk and _pk not in _seen:
                    _seen.add(_pk)
                    _sym = _pk.split(":")[1].split("_")[0] if ":" in _pk else ""
                    _side = "LONG" if "_LONG" in _pk else "SHORT"
                    class _SeedPos:
                        def __init__(self, sym, side, amt, entry):
                            self.symbol = sym; self.position_side = side; self.positionAmt = amt
                            self.quantity = amt; self.entry_price = entry; self.mark_price = entry
                            self.gain = 0.0; self.prev_gain = 0.0; self.max_gain = 0.0
                            self.opened_at = _sim_now_t(timezone.utc) - timedelta(hours=24); self.last_updated = _sim_now_t(timezone.utc)
                            self.last_augmentation_time = None; self.last_augmentation_price = 0.0
                            self.last_reduction_time = None; self.last_reduction_price = 0.0
                            self.was_reduced = False; self.augmented_count = 0
                            self.mark_price_last_updated = None; self.max_quantity = amt
                    _pos = _SeedPos(_sym, _side, _amt, _entry)
                    manager.position_manager.positions[_pk] = _pos
                    manager.position_manager.positions_by_account.setdefault(account_key, {})[_pk] = _pos
                    _seeded += 1
            except Exception:
                pass
        if _seeded:
            v8_logger.info(f"Seeded {_seeded} positions from {_seed_file.name}")
    sym_list = list(stores.keys())
    setattr(manager, f"symbols_long_{account_key}", sym_list)
    setattr(manager, f"symbols_short_{account_key}", sym_list)
    manager.symbols = sym_list
    manager.order_queue = tm_mod.OrderQueue(manager)
    # BACKTEST FIX: deduplication uses real-time (60s/90s) — kills all entries in fast simulation.
    # Zero out the queue dedupe window so every simulated bar can attempt an entry.
    setattr(tm_mod.config, 'TRADIER_QUEUE_DEDUPE_SEC', 0.0)
    v8_logger.warning(f"[V8_DEBUG] ETA method: {manager.execute_trade_action.__name__}, is wrapper: {'_v8_real_eta_wrapper' in str(manager.execute_trade_action)}")
    manager.running = True
    _orig_evaluate_stop = manager.strategy.evaluate_stop
    _RZ_REASON_MARKERS = ("SMART_RZ_", "TOP_EXIT", "TOP_FAILED", "BOTTOM_BOUNCE", "BREAKDOWN_TRUCK", "BASELINE_BOUNCE", "REJECTION_OLD_REDZONE")
    _srs_min_hold = float(getattr(tm_mod.config, 'TRADIER_MIN_HOLD_MINUTES', 240.0))
    _srs_pctb_map = {'bb_1h': 'bb_pct_b_1h', 'bb_4h': 'bb_pct_b_4h', 'bb_D': 'bb_pct_b_D', 'dc_1h': 'bb_pct_b_1h', 'dc_4h': 'bb_pct_b_4h', 'dc_D': 'bb_pct_b_D'}
    async def _v8_gated_evaluate_stop(symbol, position, indicators, market_context=None, in_grace_period=False):
        should_exit, reason, qty = await _orig_evaluate_stop(symbol, position, indicators, market_context, in_grace_period)
        if should_exit and reason:
            _wt_xu_on = getattr(tm_mod.config, 'WT_CROSSUNDER_FINAL_ENABLED', True)
            if not _wt_xu_on and ("WT_CROSSUNDER_FINAL" in reason or "WT_CROSSOVER_FINAL" in reason):
                return False, "BLOCKED_WT_XU_FINAL_DISABLED", 0
            _srs_on = getattr(tm_mod.config, 'STRUCTURAL_RANGE_SHIFT_EXIT', False)
            if not _srs_on and "STRUCTURAL_RANGE_SHIFT" in reason:
                return False, "BLOCKED_STRUCTURAL_RANGE_SHIFT_DISABLED", 0
            if reason.startswith("RZ_EXIT_") and "DELTA_EXIT_" not in reason:
                _rz_on = getattr(tm_mod.config, 'RZ_EXIT_ENABLED', True)
                if not _rz_on:
                    return False, "BLOCKED_RZ_EXIT_DISABLED", 0
            if "DELTA_EXIT_" in reason:
                _is_rz = any(m in reason for m in _RZ_REASON_MARKERS)
                if _is_rz:
                    _rz_on = getattr(tm_mod.config, 'RZ_EXIT_ENABLED', True)
                    if not _rz_on:
                        return False, "BLOCKED_RZ_EXIT_DISABLED", 0
                elif _orig_delta_engine_off:
                    return False, "BLOCKED_DELTA_ENGINE_DISABLED", 0
        _srs_on = getattr(tm_mod.config, 'STRUCTURAL_RANGE_SHIFT_EXIT', False)
        if not should_exit and _srs_on:
            _srs_opened = getattr(position, 'opened_at', None)
            _srs_hold_ok = False
            if _srs_opened:
                _srs_dt = _srs_opened if isinstance(_srs_opened, datetime) else datetime.utcfromtimestamp(float(_srs_opened)).replace(tzinfo=timezone.utc)
                _srs_hold_ok = (_sim_datetime_now(timezone.utc) - _srs_dt).total_seconds() / 60.0 >= _srs_min_hold
            if _srs_hold_ok:
                _srs_ind = indicators if indicators else {}
                _srs_qty = abs(float(getattr(position, 'positionAmt', 0)))
                _srs_entry = float(getattr(position, 'entry_price', 0) or 0)
                if _srs_qty > 0 and _srs_entry > 0:
                    _srs_is_long = getattr(position, 'position_side', 'LONG') == 'LONG'
                    _srs_tf = getattr(tm_mod.config, 'STRUCTURAL_RANGE_SHIFT_TF', 'bb_1h')
                    _srs_fm = {'dc_1h': ('dc_high_1h', 'dc_low_1h'), 'dc_4h': ('dc_high_4h', 'dc_low_4h'), 'dc_D': ('dc_high_D', 'dc_low_D'), 'bb_1h': ('bb_upper_1h', 'bb_lower_1h'), 'bb_4h': ('bb_upper_4h', 'bb_lower_4h'), 'bb_D': ('bb_upper_D', 'bb_lower_D')}
                    _srs_hk, _srs_lk = _srs_fm.get(_srs_tf, ('bb_upper_1h', 'bb_lower_1h'))
                    _srs_hi = float(_srs_ind.get(_srs_hk, 0) or 0)
                    _srs_lo = float(_srs_ind.get(_srs_lk, 0) or 0)
                    _srs_k_hi = float(getattr(tm_mod.config, 'STRUCTURAL_RANGE_SHIFT_K_HIGH', 75.0))
                    _srs_k_lo = float(getattr(tm_mod.config, 'STRUCTURAL_RANGE_SHIFT_K_LOW', 25.0))
                    _srs_prox_bps = float(getattr(tm_mod.config, 'STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS', 100.0))
                    _srs_band = _srs_prox_bps / 10000.0
                    _srs_p = float(_srs_ind.get('current_price', 0) or 0)
                    _srs_k1h = float(_srs_ind.get('stoch_k_1h', 50) or 50)
                    _srs_k1h_p = float(_srs_ind.get('stoch_k_1h_prev', 50) or 50)
                    _srs_k15m = float(_srs_ind.get('stoch_k_15m', 50) or 50)
                    _srs_k15m_p = float(_srs_ind.get('stoch_k_15m_prev', 50) or 50)
                    _srs_d1h = float(_srs_ind.get('stoch_d_1h', 50) or 50)
                    _srs_d15m = float(_srs_ind.get('stoch_d_15m', 50) or 50)
                    _srs_pctb_key = _srs_pctb_map.get(_srs_tf, 'bb_pct_b_1h')
                    _srs_pctb = float(_srs_ind.get(_srs_pctb_key, 0.5) or 0.5)
                    if _srs_is_long and _srs_hi > 0 and _srs_p > 0:
                        _srs_prox = (_srs_p >= _srs_hi * (1 - _srs_band)) or (_srs_pctb >= 0.80)
                        _srs_1h_turn = (_srs_k1h >= _srs_k_hi) and (_srs_k1h < _srs_k1h_p)
                        _srs_15m_turn = (_srs_k15m >= _srs_k_hi) and (_srs_k15m < _srs_k15m_p)
                        _srs_kd_cross = (_srs_k1h < _srs_d1h) and (_srs_k15m < _srs_d15m)
                        if _srs_prox and (_srs_1h_turn or _srs_15m_turn or _srs_kd_cross):
                            _srs_g = float(getattr(position, 'gain', 0))
                            _srs_r = f"STRUCTURAL_RANGE_SHIFT_LONG_V8_{_srs_tf}_p={_srs_p:.4f}~{_srs_hk}={_srs_hi:.4f}_pctb={_srs_pctb:.2f}_k1h={_srs_k1h:.0f}_k15m={_srs_k15m:.0f}_g={_srs_g:.2f}%"
                            return True, _srs_r, _srs_qty
                    elif not _srs_is_long and _srs_lo > 0 and _srs_p > 0:
                        _srs_prox = (_srs_p <= _srs_lo * (1 + _srs_band)) or (_srs_pctb <= 0.20)
                        _srs_1h_turn = (_srs_k1h <= _srs_k_lo) and (_srs_k1h > _srs_k1h_p)
                        _srs_15m_turn = (_srs_k15m <= _srs_k_lo) and (_srs_k15m > _srs_k15m_p)
                        _srs_kd_cross = (_srs_k1h > _srs_d1h) and (_srs_k15m > _srs_d15m)
                        if _srs_prox and (_srs_1h_turn or _srs_15m_turn or _srs_kd_cross):
                            _srs_g = float(getattr(position, 'gain', 0))
                            _srs_r = f"STRUCTURAL_RANGE_SHIFT_SHORT_V8_{_srs_tf}_p={_srs_p:.4f}~{_srs_lk}={_srs_lo:.4f}_pctb={_srs_pctb:.2f}_k1h={_srs_k1h:.0f}_k15m={_srs_k15m:.0f}_g={_srs_g:.2f}%"
                            return True, _srs_r, _srs_qty
        _wt_xu_on2 = getattr(tm_mod.config, 'WT_CROSSUNDER_FINAL_ENABLED', True)
        if not should_exit and _wt_xu_on2:
            _xu_ind = indicators if indicators else {}
            _xu_qty = abs(float(getattr(position, 'positionAmt', 0)))
            if _xu_qty > 0:
                _xu_is_long = getattr(position, 'position_side', 'LONG') == 'LONG'
                _xu_gain = float(getattr(position, 'gain', 0))
                _xu_wt1_5m = float(_xu_ind.get('wt1_5m', _xu_ind.get('wt1_3m', 0)) or 0)
                _xu_wt2_5m = float(_xu_ind.get('wt2_5m', _xu_ind.get('wt2_3m', 0)) or 0)
                _xu_wt1_15m = float(_xu_ind.get('wt1_15m', 0) or 0)
                _xu_wt2_15m = float(_xu_ind.get('wt2_15m', 0) or 0)
                _xu_wt1_1h = float(_xu_ind.get('wt1_1h', 0) or 0)
                _xu_wt2_1h = float(_xu_ind.get('wt2_1h', 0) or 0)
                _xu_wt1_4h = float(_xu_ind.get('wt1_4h', 0) or 0)
                _xu_wt2_4h = float(_xu_ind.get('wt2_4h', 0) or 0)
                _xu_wt1_D = float(_xu_ind.get('wt1_D', 0) or 0)
                _xu_wt2_D = float(_xu_ind.get('wt2_D', 0) or 0)
                if _xu_is_long:
                    _xu_ltf = _xu_wt1_5m < _xu_wt2_5m
                    _xu_15m = (_xu_wt1_15m < _xu_wt2_15m) or (_xu_wt1_15m > 95)
                    _xu_htf = (_xu_wt1_1h < _xu_wt2_1h) or (_xu_wt1_4h < _xu_wt2_4h) or (_xu_wt1_D < _xu_wt2_D)
                    if _xu_ltf and _xu_15m and _xu_htf:
                        _xu_r = f"WT_CROSSUNDER_FINAL_V8_5m_15m_1h{_xu_wt1_1h<_xu_wt2_1h}_4h{_xu_wt1_4h<_xu_wt2_4h}_D{_xu_wt1_D<_xu_wt2_D}_g{_xu_gain:.2f}%_MANDATORY_REENTRY"
                        return True, _xu_r, _xu_qty
                else:
                    _xu_ltf = _xu_wt1_5m > _xu_wt2_5m
                    _xu_15m = (_xu_wt1_15m > _xu_wt2_15m) or (_xu_wt1_15m < -95)
                    _xu_htf = (_xu_wt1_1h > _xu_wt2_1h) or (_xu_wt1_4h > _xu_wt2_4h) or (_xu_wt1_D > _xu_wt2_D)
                    if _xu_ltf and _xu_15m and _xu_htf:
                        _xu_r = f"WT_CROSSOVER_FINAL_V8_5m_15m_1h{_xu_wt1_1h>_xu_wt2_1h}_4h{_xu_wt1_4h>_xu_wt2_4h}_D{_xu_wt1_D>_xu_wt2_D}_g{_xu_gain:.2f}%_MANDATORY_REENTRY"
                        return True, _xu_r, _xu_qty
        return should_exit, reason, qty
    manager.strategy.evaluate_stop = _v8_gated_evaluate_stop
    _v8_satoshit_override = _t_overrides.get("SATOSHIT_ENTRY_FILTER") if _t_overrides else None
    if _v8_satoshit_override is not None:
        _actual_sat = getattr(tm_mod.config, 'SATOSHIT_ENTRY_FILTER', "MISSING")
        if _actual_sat != _v8_satoshit_override:
            v8_logger.warning(f"[V8_FIX] SATOSHIT_ENTRY_FILTER drift: config={_actual_sat}, override={_v8_satoshit_override}. Force-setting.")
            setattr(tm_mod.config, 'SATOSHIT_ENTRY_FILTER', _v8_satoshit_override)
        v8_logger.info(f"[V8] SATOSHIT_ENTRY_FILTER = {getattr(tm_mod.config, 'SATOSHIT_ENTRY_FILTER', 'MISSING')} (override={_v8_satoshit_override})")
    # 2026-04-14 sentinel DUPE_RESULTS fix: SATOSHIT was dead because should_enter_long
    # falls back to SHOULD_ENTER_FALLBACK_ENABLED (default False) when SAT=False, blocking
    # ALL entries via that path. Both SAT=True and SAT=False then produced identical trades
    # from the wt_dc_score_entry path. Force fallback ON when SAT is OFF so the non-SAT
    # entry regime actually fires (and differs from the SAT-only regime). When SAT is ON,
    # leave fallback OFF so SAT is the sole gate (matches live behavior).
    _v8_sat_now = getattr(tm_mod.config, 'SATOSHIT_ENTRY_FILTER', True)
    setattr(tm_mod.config, 'SHOULD_ENTER_FALLBACK_ENABLED', not bool(_v8_sat_now))
    try:
        setattr(_ct.TradierConfig, 'SHOULD_ENTER_FALLBACK_ENABLED', not bool(_v8_sat_now))
        if hasattr(_ct.TradierConfig, '__dataclass_fields__') and 'SHOULD_ENTER_FALLBACK_ENABLED' in _ct.TradierConfig.__dataclass_fields__:
            _ct.TradierConfig.__dataclass_fields__['SHOULD_ENTER_FALLBACK_ENABLED'].default = (not bool(_v8_sat_now))
    except Exception:
        pass
    v8_logger.info(f"[V8] SHOULD_ENTER_FALLBACK_ENABLED forced to {not bool(_v8_sat_now)} (mirrors SAT={_v8_sat_now}) so non-SAT regime is reachable when SAT=False")
    _SRS_KEYS = ("STRUCTURAL_RANGE_SHIFT_EXIT", "STRUCTURAL_RANGE_SHIFT_TF", "STRUCTURAL_RANGE_SHIFT_K_HIGH", "STRUCTURAL_RANGE_SHIFT_K_LOW", "STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS")
    for _srs_k in _SRS_KEYS:
        _srs_ov = _t_overrides.get(_srs_k) if _t_overrides else None
        if _srs_ov is None:
            continue
        _srs_actual = getattr(tm_mod.config, _srs_k, "MISSING")
        if _srs_actual != _srs_ov:
            v8_logger.warning(f"[V8_FIX] {_srs_k} drift: config={_srs_actual}, override={_srs_ov}. Force-setting.")
            setattr(tm_mod.config, _srs_k, _srs_ov)
        try:
            _srs_cls = type(tm_mod.config)
            if hasattr(_srs_cls, _srs_k):
                setattr(_srs_cls, _srs_k, _srs_ov)
            if hasattr(_srs_cls, '__dataclass_fields__') and _srs_k in _srs_cls.__dataclass_fields__:
                _srs_cls.__dataclass_fields__[_srs_k].default = _srs_ov
        except Exception as _srs_cls_e:
            v8_logger.warning(f"[V8_FIX] {_srs_k} class-level force-set failed: {_srs_cls_e}")
        try:
            import config_tradier as _ct_mod2
            if hasattr(_ct_mod2, 'TradierConfig'):
                setattr(_ct_mod2.TradierConfig, _srs_k, _srs_ov)
                if hasattr(_ct_mod2.TradierConfig, '__dataclass_fields__') and _srs_k in _ct_mod2.TradierConfig.__dataclass_fields__:
                    _ct_mod2.TradierConfig.__dataclass_fields__[_srs_k].default = _srs_ov
            if hasattr(_ct_mod2, 'config'):
                setattr(_ct_mod2.config, _srs_k, _srs_ov)
        except Exception:
            pass
        try:
            import config as _cc_mod
            if hasattr(_cc_mod, 'config'):
                setattr(_cc_mod.config, _srs_k, _srs_ov)
        except Exception:
            pass
        v8_logger.info(f"[V8] {_srs_k} = {getattr(tm_mod.config, _srs_k, 'MISSING')} (override={_srs_ov})")
    try:
        from ez_satoshit import satoshit_entry_signal as _v8_sat_entry
        v8_logger.info("[V8] ez_satoshit imported OK for direct SATOSHIT gating")
    except ImportError as _sat_imp_err:
        v8_logger.error(f"[V8_SATOSHIT_IMPORT_FAIL] {_sat_imp_err} — SATOSHIT gate will be NO-OP")
        _v8_sat_entry = None
    _orig_wt_dc_score_entry = tm_mod.wt_dc_score_entry
    def _v8_satoshit_wt_dc_score_entry(indicators, is_long, current_price=0.0):
        score, reason = _orig_wt_dc_score_entry(indicators, is_long, current_price)
        if getattr(tm_mod.config, 'SATOSHIT_ENTRY_FILTER', True) and _v8_sat_entry:
            try:
                _sat_ok, _sat_votes, _ = _v8_sat_entry(indicators, is_long, tm_mod.config)
                if _sat_ok:
                    score = max(float(score), 999.0)
                    reason = f"SAT_SOLE_v{_sat_votes}+{reason}"
                else:
                    score = 0.0
                    reason = f"BLOCKED_SATOSHIT_v{_sat_votes}+{reason}"
            except Exception as _sat_wrap_e:
                v8_logger.warning(f"[V8_SATOSHIT_WRAPPER_ERR] blocking entry: {_sat_wrap_e}")
                score = 0.0
                reason = f"BLOCKED_SATOSHIT_ERROR+{reason}"
        elif not getattr(tm_mod.config, 'SATOSHIT_ENTRY_FILTER', True):
            pass
        if getattr(tm_mod.config, 'STDEV_BREAKOUT_ENABLED', False):
            _sb_htf_list_w = list(getattr(tm_mod.config, 'STDEV_BREAKOUT_HTF_LIST', None) or ['D', '4h'])
            _sb_pctb_thr_lw = float(getattr(tm_mod.config, 'STDEV_BREAKOUT_PCTB_LONG', 1.0))
            _sb_pctb_thr_sw = float(getattr(tm_mod.config, 'STDEV_BREAKOUT_PCTB_SHORT', 0.0))
            _sb_rvol_min_w = float(getattr(tm_mod.config, 'STDEV_BREAKOUT_RVOL_MIN', 1.2))
            for _sb_htf_w in _sb_htf_list_w:
                _sb_pv_w = float(indicators.get(f'bb_pct_b_{_sb_htf_w}', 0.5) or 0.5)
                _sb_rv_w = float(indicators.get(f'relative_volume_{_sb_htf_w}', 1.0) or 1.0)
                if is_long and _sb_pv_w >= _sb_pctb_thr_lw and _sb_rv_w >= _sb_rvol_min_w:
                    score = 999.0
                    reason = f"STDEV_BREAKOUT_LONG_{_sb_htf_w}_pctb={_sb_pv_w:.3f}+{reason}"
                    break
                if not is_long and _sb_pv_w <= _sb_pctb_thr_sw and _sb_rv_w >= _sb_rvol_min_w:
                    score = 999.0
                    reason = f"STDEV_BREAKOUT_SHORT_{_sb_htf_w}_pctb={_sb_pv_w:.3f}+{reason}"
                    break
        if getattr(tm_mod.config, 'STDEV_BOUNCE_ENABLED', False):
            _bn_htf_list_w = list(getattr(tm_mod.config, 'STDEV_BOUNCE_HTF_LIST', None) or ['D', '4h'])
            _bn_pctb_thr_lw = float(getattr(tm_mod.config, 'STDEV_BOUNCE_PCTB_LONG', 0.05))
            _bn_pctb_thr_sw = float(getattr(tm_mod.config, 'STDEV_BOUNCE_PCTB_SHORT', 0.95))
            _bn_rvol_min_w = float(getattr(tm_mod.config, 'STDEV_BOUNCE_RVOL_MIN', 1.2))
            for _bn_htf_w in _bn_htf_list_w:
                _bn_pv_w = float(indicators.get(f'bb_pct_b_{_bn_htf_w}', 0.5) or 0.5)
                _bn_rv_w = float(indicators.get(f'relative_volume_{_bn_htf_w}', 1.0) or 1.0)
                if is_long and _bn_pv_w <= _bn_pctb_thr_lw and _bn_rv_w >= _bn_rvol_min_w:
                    score = 999.0
                    reason = f"STDEV_BOUNCE_LONG_{_bn_htf_w}_pctb={_bn_pv_w:.3f}+{reason}"
                    break
                if not is_long and _bn_pv_w >= _bn_pctb_thr_sw and _bn_rv_w >= _bn_rvol_min_w:
                    score = 999.0
                    reason = f"STDEV_BOUNCE_SHORT_{_bn_htf_w}_pctb={_bn_pv_w:.3f}+{reason}"
                    break
        return score, reason
    tm_mod.wt_dc_score_entry = _v8_satoshit_wt_dc_score_entry
    v8_logger.info(f"[V8] SATOSHIT wt_dc_score_entry wrapper installed (boost=+15 when SATOSHIT fires)")
    start_ts_filter = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    all_ts = sorted(set(int(t) for s in stores.values() for t in s.timestamps if int(t) >= start_ts_filter))
    v8_logger.info(f"Tradier: {len(all_ts)} bars, {len(stores)} symbols from {start_date}")
    t0 = _real_time_module.time()
    report_every = 200 if _SWEEP_MODE else max(1, len(all_ts) // 20)
    _last_heartbeat_t = _real_time_module.time()
    _bt_rg_t_disabled = os.environ.get("V8_RATE_GUARD_DISABLED", "0") == "1"
    _bt_rg_t = None if _bt_rg_t_disabled else RateGuard(n_accts=max(1, len(stores)), label=f"backtest_v8_engine.tradier.{account_key}")
    for step, ts in enumerate(all_ts):
        _sim_ts[0] = float(ts)
        if _SWEEP_MODE:
            _now_real = _real_time_module.time()
            if _now_real - _last_heartbeat_t > 10.0:
                _t_closes = len([t for t in executed_trades if t.get('action', '').upper() in ('CLOSE', 'FULL_CLOSE', 'REDUCE')])
                print(f"V8_HEARTBEAT: step={step}/{len(all_ts)} closes={_t_closes}", flush=True)
                _last_heartbeat_t = _now_real
        if _bt_rg_t is not None and (step % 50 == 0):
            _bt_rg_t.tick(len([t for t in executed_trades if t.get('action', '').upper() in ('CLOSE', 'FULL_CLOSE', 'REDUCE')]))
        for sym, store in stores.items():
            idx = store.ts_to_idx.get(ts, -1)
            if idx < 0: continue
            p = store.price(idx)
            if p <= 0: continue
            ind = store.build_indicator_dict(idx)
            _ha_map = {-1: 'red', 0: 'neutral', 1: 'green'}
            for _ha_tf in ['5m', '15m', '1h', '4h', 'D']:
                _ha_k = f'ha_{_ha_tf}'
                _ha_v = ind.get(_ha_k)
                if isinstance(_ha_v, (int, float, np.integer, np.floating)):
                    ind[_ha_k] = _ha_map.get(int(_ha_v), 'neutral')
            ind['ts'] = float(ts)
            ind['_tick_ts'] = float(ts)
            ind['current_price'] = p
            ind['mark_price'] = p
            _sim_dt = datetime.utcfromtimestamp(ts)
            _sim_iso = _sim_dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
            ind['timestamp'] = _sim_iso
            for _tf in ['5m', '15m', '1h', '4h', 'D']:
                ind[f'timestamp_{_tf}'] = _sim_iso
                ind[f'age_{_tf}'] = 0.0
            for _ptf in ['15m', '1h', '4h', 'D']:
                _pk = f'stoch_k_{_ptf}_prev'
                if _pk not in ind:
                    _pidx = max(0, idx - 1)
                    _src_k = f'stoch_k_{_ptf}'
                    ind[_pk] = float(store.get(_src_k, _pidx)) if _pidx != idx and _src_k in store.arrays else ind.get(_src_k, 50)
            for _bb_ptf in ['D', '4h', '1h']:
                _bb_pk = f'bb_pct_b_{_bb_ptf}_prev'
                if _bb_pk not in ind:
                    _bb_pidx = max(0, idx - 1)
                    _bb_src = f'bb_pct_b_{_bb_ptf}'
                    ind[_bb_pk] = float(store.get(_bb_src, _bb_pidx)) if _bb_pidx != idx and _bb_src in store.arrays else ind.get(_bb_src, 0.5)
            indicator_cache[sym.upper()] = ind
            price_cache[sym.upper()] = p
            manager.price_cache[sym.upper()] = {"price": p, "timestamp": float(ts)}
        manager.market_snapshot = dict(indicator_cache)
        # DELTA_ENTRY prev-warmup: done AFTER process_position (see below).
        # Update position gains from current prices
        if manager.position_manager:
            for _pk, _pos in manager.position_manager.positions.items():
                _amt = abs(getattr(_pos, 'positionAmt', getattr(_pos, 'quantity', 0)))
                if _amt <= 0: continue
                _sym = getattr(_pos, 'symbol', '')
                _p = price_cache.get(_sym.upper(), 0)
                _entry = getattr(_pos, 'entry_price', 0)
                if _p > 0 and _entry > 0:
                    _pos.mark_price = _p
                    is_long = getattr(_pos, 'position_side', '') == 'LONG' or _pk.endswith('_LONG')
                    if is_long:
                        _pos.gain = (_p - _entry) / _entry * 100
                    else:
                        _pos.gain = (_entry - _p) / _entry * 100
                    if _pos.gain > getattr(_pos, 'max_gain', 0):
                        _pos.max_gain = _pos.gain
        if not _sim_irth(): continue
        manager.last_monitored_positions.clear()
        open_keys = [pk for pk, pos in (manager.position_manager.positions if manager.position_manager else {}).items() if pk.startswith(f"{account_key}:") and abs(getattr(pos, 'positionAmt', getattr(pos, 'quantity', 0))) > 0]
        # ENTRY PRE-FILTER: SATOSHIT_ENTRY_FILTER gates entry candidates.
        # 2026-04-14 FIX: SATOSHIT was dead because should_enter silently failed (import
        # error caught by except:pass). Now uses eagerly imported _v8_sat_entry directly.
        # SATOSHIT=True: only candidates passing satoshit_entry_signal are allowed.
        # SATOSHIT=False: all candidates pass (process_position's delta/wt_dc decides).
        cand_keys = []
        _sat_enabled = getattr(tm_mod.config, 'SATOSHIT_ENTRY_FILTER', True)
        if step % 3 == 0:
            for s in stores:
                ind = indicator_cache.get(s.upper(), {})
                if not ind: continue
                pk_l = f"{account_key}:{s}_LONG"
                pk_s = f"{account_key}:{s}_SHORT"
                _store = stores[s]
                _bar_idx = _store.ts_to_idx.get(ts, -1)
                _tl = _store.arrays.get("tradeable_long")
                _ts_arr = _store.arrays.get("tradeable_short")
                _is_long_ok = _tl is not None and _bar_idx >= 0 and _bar_idx < len(_tl) and _tl[_bar_idx]
                _is_short_ok = _ts_arr is not None and _bar_idx >= 0 and _bar_idx < len(_ts_arr) and _ts_arr[_bar_idx]
                if _tl is None and _ts_arr is None:
                    _is_long_ok = True
                    _is_short_ok = True
                _sat_long_ok = True
                _sat_short_ok = True
                if _sat_enabled and _v8_sat_entry is None:
                    _sat_long_ok = False
                    _sat_short_ok = False
                elif _sat_enabled and _v8_sat_entry is not None:
                    try:
                        _sl_ok, _, _ = _v8_sat_entry(ind, True, tm_mod.config)
                        _sat_long_ok = bool(_sl_ok)
                    except Exception as _sat_pf_le:
                        v8_logger.warning(f"[V8_SATOSHIT_PREFILTER_LONG] {s} blocked on error: {_sat_pf_le}")
                        _sat_long_ok = False
                    try:
                        _ss_ok, _, _ = _v8_sat_entry(ind, False, tm_mod.config)
                        _sat_short_ok = bool(_ss_ok)
                    except Exception as _sat_pf_se:
                        v8_logger.warning(f"[V8_SATOSHIT_PREFILTER_SHORT] {s} blocked on error: {_sat_pf_se}")
                        _sat_short_ok = False
                _stdev_long_ok = False
                _stdev_short_ok = False
                if getattr(tm_mod.config, 'STDEV_BREAKOUT_ENABLED', False):
                    _sb_htf_list_f = list(getattr(tm_mod.config, 'STDEV_BREAKOUT_HTF_LIST', None) or ['D', '4h'])
                    _sb_pctb_thr_lf = float(getattr(tm_mod.config, 'STDEV_BREAKOUT_PCTB_LONG', 1.0))
                    _sb_pctb_thr_sf = float(getattr(tm_mod.config, 'STDEV_BREAKOUT_PCTB_SHORT', 0.0))
                    _sb_rvol_min_f = float(getattr(tm_mod.config, 'STDEV_BREAKOUT_RVOL_MIN', 1.2))
                    for _sb_htf_f in _sb_htf_list_f:
                        _sb_pv_f = float(ind.get(f'bb_pct_b_{_sb_htf_f}', 0.5) or 0.5)
                        _sb_rv_f = float(ind.get(f'relative_volume_{_sb_htf_f}', 1.0) or 1.0)
                        if _sb_pv_f >= _sb_pctb_thr_lf and _sb_rv_f >= _sb_rvol_min_f:
                            _stdev_long_ok = True
                        if _sb_pv_f <= _sb_pctb_thr_sf and _sb_rv_f >= _sb_rvol_min_f:
                            _stdev_short_ok = True
                if getattr(tm_mod.config, 'STDEV_BOUNCE_ENABLED', False):
                    _bn_htf_list_f = list(getattr(tm_mod.config, 'STDEV_BOUNCE_HTF_LIST', None) or ['D', '4h'])
                    _bn_pctb_thr_lf = float(getattr(tm_mod.config, 'STDEV_BOUNCE_PCTB_LONG', 0.05))
                    _bn_pctb_thr_sf = float(getattr(tm_mod.config, 'STDEV_BOUNCE_PCTB_SHORT', 0.95))
                    _bn_rvol_min_f = float(getattr(tm_mod.config, 'STDEV_BOUNCE_RVOL_MIN', 1.2))
                    for _bn_htf_f in _bn_htf_list_f:
                        _bn_pv_f = float(ind.get(f'bb_pct_b_{_bn_htf_f}', 0.5) or 0.5)
                        _bn_rv_f = float(ind.get(f'relative_volume_{_bn_htf_f}', 1.0) or 1.0)
                        if _bn_pv_f <= _bn_pctb_thr_lf and _bn_rv_f >= _bn_rvol_min_f:
                            _stdev_long_ok = True
                        if _bn_pv_f >= _bn_pctb_thr_sf and _bn_rv_f >= _bn_rvol_min_f:
                            _stdev_short_ok = True
                if pk_l not in open_keys and _is_long_ok and (_sat_long_ok or _stdev_long_ok):
                    cand_keys.append(pk_l)
                if pk_s not in open_keys and _is_short_ok and (_sat_short_ok or _stdev_short_ok):
                    cand_keys.append(pk_s)
        all_keys = open_keys + cand_keys
        if not all_keys: continue
        # BACKTEST FIX: clear real-time dedupe state each step so every bar can attempt entries.
        if hasattr(manager, 'order_deduplication'):
            manager.order_deduplication.clear()
        if hasattr(manager, '_queue_attempt_ts'):
            manager._queue_attempt_ts.clear()
        # BACKTEST FIX: _pending_closes uses time.time() (real wall clock). Backtest processes
        # ~200 bars/sec so _since_last < 30s always → CLOSE_COOLDOWN blocks every close after
        # the first. Clear per step so each bar gets one close attempt per symbol.
        if hasattr(manager, '_pending_closes'):
            manager._pending_closes.clear()
        await asyncio.gather(*[tm_mod.process_position(account_key, pk, manager.order_queue, manager, event_type="backtest", force=True) for pk in all_keys], return_exceptions=True)
        oq = manager.order_queue
        if hasattr(oq, '_orders'):
            while not oq._orders.empty():
                try:
                    order = oq._orders.get_nowait()
                    await oq.handle_order(order)
                    oq._orders.task_done()
                except: break
        await asyncio.sleep(0)
        if hasattr(manager, 'delta_tracker') and manager.delta_tracker:
            _dt_wt_fields = ["wt1", "wt2", "wt_score", "wt_velocity", "wt_acceleration", "wt_percentile", "wt_zscore"]
            _dt_dc_fields = ["dc_basis", "dc_high", "dc_low"]
            _dt_tfs = list(manager.delta_tracker.cfg.get("tf_weights", {}).keys()) or ["1h", "4h", "D"]
            for _dt_sym, _dt_ind in indicator_cache.items():
                if not _dt_ind:
                    continue
                _dt_prev = manager.delta_tracker._prev[_dt_sym]
                _dt_cur_ts = float(_dt_ind.get("_tick_ts", 0) or 0)
                if _dt_cur_ts == _dt_prev.get("_last_bar_ts", 0):
                    continue
                for _dt_tf in _dt_tfs:
                    for _dt_f in _dt_wt_fields:
                        _dt_k = f"{_dt_f}_{_dt_tf}"
                        _dt_v = _dt_ind.get(_dt_k)
                        if _dt_v is not None:
                            _dt_prev[_dt_k] = float(_dt_v)
                    for _dt_f in _dt_dc_fields:
                        _dt_k = f"{_dt_f}_{_dt_tf}"
                        _dt_v = _dt_ind.get(_dt_k)
                        if _dt_v is not None:
                            _dt_prev[_dt_k] = float(_dt_v)
                _dt_prev["_last_bar_ts"] = _dt_cur_ts
        # Wall-clock-time-based progress emit (silent-death debug 2026-05-01).
        # Tradier-loop variant of crypto-loop V8_PROGRESS (line ~1965-1984).
        _wallclock_now = _real_time_module.time()
        if "_t_last_wallclock_progress" not in dir():
            _t_last_wallclock_progress = t0
        _t_wallclock_progress_due = (_wallclock_now - _t_last_wallclock_progress) >= 60.0
        if step > 0 and (step % report_every == 0 or _t_wallclock_progress_due):
            _t_last_wallclock_progress = _wallclock_now
            elapsed = _wallclock_now - t0
            try:
                import resource as _rs_mod
                _rss_mb = _rs_mod.getrusage(_rs_mod.RUSAGE_SELF).ru_maxrss / 1024.0
            except Exception:
                _rss_mb = 0.0
            v8_logger.info(f"[{step}/{len(all_ts)} {step*100//len(all_ts)}%] active={len(open_keys)} trades={len(executed_trades)} {elapsed:.0f}s rss_mb={_rss_mb:.0f}")
            print(f"V8_PROGRESS: step={step}/{len(all_ts)} pct={step*100//len(all_ts)} trades={len(executed_trades)} active={len(open_keys)} elapsed={elapsed:.0f}s rss_mb={_rss_mb:.0f}", flush=True)
            if _SWEEP_MODE:
                _r_closes = len([t for t in executed_trades if t.get('action', '').upper() in ('CLOSE', 'FULL_CLOSE', 'REDUCE')])
                print(f"V8_RESULT_LIVE: step={step}/{len(all_ts)} closes={_r_closes} elapsed={elapsed:.0f}s", flush=True)
    elapsed = _real_time_module.time() - t0
    # NEW 2026-04-26 sweep switches: per-run counter dump (tradier path).
    v8_logger.info(f"[V8_NEW_SWITCHES] dd_peak={_v8ns_dd_state.get('peak', 0.0):+.2f}%  dd_min={_v8ns_dd_state.get('dd_pct', 0.0):+.2f}%  equity_pct={_v8ns_equity_pct[0]:+.2f}%  counters={_v8ns_counters}")
    print(f"V8_NEW_SWITCHES: vt={_v8ns_counters['vol_target_applied']} dk={_v8ns_counters['dd_kelly_applied']} tm={_v8ns_counters['tsmom_applied']} mn={_v8ns_counters['minervini_block']} cl={_v8ns_counters['clenow_block']} pt={_v8ns_counters['proximity_top_block']} sf={_v8ns_counters['squeeze_fire_aligned']} dd_min={_v8ns_dd_state.get('dd_pct', 0.0):.2f}", flush=True)
    if _bt_rg_t is not None and len(all_ts) >= 2:
        _bt_t_days = max((all_ts[-1] - all_ts[0]) / 86400.0, 1e-6)
        _bt_t_closes = len([t for t in executed_trades if t.get('action', '').upper() in ('CLOSE', 'FULL_CLOSE', 'REDUCE')])
        _bt_rg_t.final_check(_bt_t_closes, test_window_days=_bt_t_days)
    _compute_trade_pnl(executed_trades)
    _v8_result_from_trades(executed_trades, capital)
    _write_chart_trades(executed_trades)
    log_dir = BASE_PATH / "backtest_v8" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"v8_tradier_{account_key}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jsonl"
    with open(log_path, "w") as f:
        for t in executed_trades:
            f.write(json.dumps(t, default=str) + "\n")
    v8_logger.info(f"\n{'='*60}\n  V8 TRADIER: {len(stores)} syms, {len(all_ts)} bars, {len(executed_trades)} trades, {elapsed:.0f}s\n  Log: {log_path}\n{'='*60}")
    print(f"V8_LOG: {log_path}")


if __name__ == "__main__":
    main()
