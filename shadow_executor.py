#!/usr/bin/env python3
"""shadow_executor.py — runs ez_manage.py / tradier_manage.py in shadow mode.

Live signal computation, NO real orders, namespaced Redis writes, decisions
redirected to data/shadow_decisions/<SHADOW_CONFIG_ID>/.

USAGE:
    SHADOW_CONFIG_ID=baseline_crypto \\
    SHADOW_PLATFORM=crypto \\
    SHADOW_ACCOUNT=ang \\
        python3 shadow_executor.py

Reads overrides from shadows/<SHADOW_CONFIG_ID>.json and applies them to the
live config singleton AFTER import (top-level code already ran).

SAFETY LAYERS (any one alone would prevent real orders):
    L1. BINANCE_API_KEY / TRADIER_TOKEN forced to dummy values pre-import
    L2. *_WEBHOOK_URL / *_WEBHOOK_URL2 env vars rewritten to localhost sink
    L3. binance.client.Client.futures_create_order/_cancel_order patched to no-op
    L4. tradier_api.TradierAPIClient.place_order patched to no-op
    L5. is_sandbox_account forced True for shadow account → live short-circuits
    L6. SANDBOX_MODE=True, SANDBOX_ACCOUNTS includes shadow account
    L7. redis.Redis writes namespaced to shadow:<id>:* (reads passthrough)
    L8. data/decisions/* writes redirected to data/shadow_decisions/<id>/
"""
import asyncio
import builtins
import functools
import json
import logging
import os
import sys
import time
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# 0. Read shadow params
# ─────────────────────────────────────────────────────────────────────────────
SHADOW_CONFIG_ID = os.environ.get("SHADOW_CONFIG_ID")
SHADOW_PLATFORM = os.environ.get("SHADOW_PLATFORM", "").lower()
SHADOW_ACCOUNT = os.environ.get("SHADOW_ACCOUNT")

if not SHADOW_CONFIG_ID or SHADOW_PLATFORM not in ("crypto", "tradier") or not SHADOW_ACCOUNT:
    print("FATAL: SHADOW_CONFIG_ID, SHADOW_PLATFORM (crypto|tradier), SHADOW_ACCOUNT required", file=sys.stderr)
    sys.exit(2)

BASE_DIR = Path(__file__).resolve().parent
OVERRIDE_FILE = BASE_DIR / "shadows" / f"{SHADOW_CONFIG_ID}.json"
SHADOW_DIR = BASE_DIR / "data" / "shadow_decisions" / SHADOW_CONFIG_ID
SHADOW_DIR.mkdir(parents=True, exist_ok=True)

if not OVERRIDE_FILE.exists():
    print(f"FATAL: override file not found: {OVERRIDE_FILE}", file=sys.stderr)
    sys.exit(2)

with open(OVERRIDE_FILE) as _f:
    OVERRIDES = json.load(_f)

# Dedicated shadow logger — writes to a per-shadow log file, separate from live logs
SHADOW_LOG = BASE_DIR / "logs" / f"shadow_{SHADOW_CONFIG_ID}_{SHADOW_ACCOUNT}.log"
SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SHADOW:%(name)s] %(message)s",
    handlers=[logging.FileHandler(SHADOW_LOG, mode="a"), logging.StreamHandler(sys.stderr)],
)
slog = logging.getLogger("executor")
slog.info(f"BOOT cfg={SHADOW_CONFIG_ID} platform={SHADOW_PLATFORM} acct={SHADOW_ACCOUNT}")
slog.info(f"OVERRIDES ({len(OVERRIDES)}): {list(OVERRIDES.keys())[:20]}{'...' if len(OVERRIDES) > 20 else ''}")

# ─────────────────────────────────────────────────────────────────────────────
# L1+L2. Bogus credentials + webhook sink BEFORE any import that reads them
# ─────────────────────────────────────────────────────────────────────────────
_DUMMY = "DUMMY_SHADOW_NEVER_REAL"
for _key in (
    "BINANCE_API_KEY", "BINANCE_API_SECRET",
    "TRADIER_TOKEN", "TRADIER_API_KEY",
    "ang_API_KEY", "ang_API_SECRET",
    "inf_API_KEY", "inf_API_SECRET",
    "flz_API_KEY", "flz_API_SECRET",
    "men_API_KEY", "men_API_SECRET",
    "fin_API_KEY", "fin_API_SECRET",
    "trb_API_KEY", "trb_API_SECRET",
    "trc_API_KEY", "trc_API_SECRET",
):
    os.environ[_key] = _DUMMY

_SINK = "http://127.0.0.1:9999/shadow-sink-never-reachable"
for _acct in ("ang", "inf", "flz", "men", "fin", "trb", "trc"):
    for _suf in ("WEBHOOK_URL", "WEBHOOK_URL2", "WEBHOOK_URL3", "WEBHOOK_SECRET", "WEBHOOK_SECRET2", "WEBHOOK_SECRET3"):
        os.environ[f"{_acct}_{_suf}"] = _SINK if "URL" in _suf else _DUMMY

# Shadow processes must not auto-restart launchd or kill live processes
os.environ["SHADOW_MODE"] = "1"
os.environ["SHADOW_CONFIG_ID"] = SHADOW_CONFIG_ID

# ─────────────────────────────────────────────────────────────────────────────
# L8. Decision JSONL redirect via builtins.open + aiofiles.open patch
# ─────────────────────────────────────────────────────────────────────────────
_LIVE_DECISIONS_FRAGMENT = "/data/decisions/"
_SHADOW_DECISIONS_DIR = SHADOW_DIR  # data/shadow_decisions/<id>/

def _redirect_path(p):
    sp = str(p)
    if _LIVE_DECISIONS_FRAGMENT in sp:
        # data/decisions/decisions_ang_20260426.jsonl → shadow_decisions/<id>/decisions_ang_20260426.jsonl
        fname = Path(sp).name
        new_p = _SHADOW_DECISIONS_DIR / fname
        return str(new_p)
    return sp

_real_open = builtins.open
@functools.wraps(_real_open)
def _shadow_open(file, *args, **kwargs):
    return _real_open(_redirect_path(file), *args, **kwargs)
builtins.open = _shadow_open

# Patch aiofiles.open similarly (after it's imported)
try:
    import aiofiles
    _real_aio_open = aiofiles.open
    @functools.wraps(_real_aio_open)
    def _shadow_aio_open(file, *args, **kwargs):
        return _real_aio_open(_redirect_path(file), *args, **kwargs)
    aiofiles.open = _shadow_aio_open
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# L7. Redis namespacing — wrap redis.Redis.set/hset/publish/delete/expire
# ─────────────────────────────────────────────────────────────────────────────
import redis as _redis_mod

_NS_PREFIX = f"shadow:{SHADOW_CONFIG_ID}:"
# Keys we silently NO-OP on (live cooldowns/locks shadow has no business setting)
_NOOP_KEY_FRAGMENTS = (
    "exec_lock", "open_lock", "maker_lock", "absolute_open_lock",
    "aug_cooldown", "recent_signal", "strict_hist",
)

def _is_noop_key(key: str) -> bool:
    sk = str(key).lower()
    return any(frag in sk for frag in _NOOP_KEY_FRAGMENTS)

def _ns(key):
    if isinstance(key, bytes):
        key = key.decode("utf-8", errors="replace")
    return _NS_PREFIX + str(key)

def _wrap_write(method_name, real_method):
    @functools.wraps(real_method)
    def wrapper(self, *args, **kwargs):
        # First positional is typically the key
        if args and not _is_noop_key(args[0]):
            new_args = (_ns(args[0]),) + args[1:]
            return real_method(self, *new_args, **kwargs)
        # Either no args or noop-key → return a benign success value
        if method_name == "publish": return 0
        if method_name in ("delete", "expire", "hset", "hdel", "sadd", "srem", "lpush", "rpush", "incr", "decr"): return 0
        return True  # set, setex, etc.
    return wrapper

for _m in ("set", "setex", "setnx", "hset", "hmset", "hdel", "hsetnx", "delete", "expire", "publish",
          "sadd", "srem", "lpush", "rpush", "incr", "decr", "rename", "persist"):
    if hasattr(_redis_mod.Redis, _m):
        setattr(_redis_mod.Redis, _m, _wrap_write(_m, getattr(_redis_mod.Redis, _m)))

# Same for asyncio Redis if installed
try:
    import redis.asyncio as _aioredis_mod
    for _m in ("set", "setex", "setnx", "hset", "hmset", "hdel", "hsetnx", "delete", "expire", "publish",
              "sadd", "srem", "lpush", "rpush", "incr", "decr", "rename", "persist"):
        if hasattr(_aioredis_mod.Redis, _m):
            real = getattr(_aioredis_mod.Redis, _m)
            async def _async_wrap(self, *args, _real=real, _name=_m, **kwargs):
                if args and not _is_noop_key(args[0]):
                    return await _real(self, _ns(args[0]), *args[1:], **kwargs)
                return 0 if _name in ("publish", "delete", "expire", "hset", "hdel", "sadd", "srem", "lpush", "rpush", "incr", "decr") else True
            setattr(_aioredis_mod.Redis, _m, _async_wrap)
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# L3. Patch binance Client.futures_create_order + futures_cancel_order
# ─────────────────────────────────────────────────────────────────────────────
def _shadow_futures_create_order(self, **kwargs):
    """Returns a synthetic FILLED response, logs to shadow_orders log."""
    sym = kwargs.get("symbol")
    side = kwargs.get("side")
    pos_side = kwargs.get("positionSide")
    qty = kwargs.get("quantity")
    price = kwargs.get("price")
    rec = {
        "ts": time.time(), "kind": "futures_create_order", "symbol": sym, "side": side,
        "positionSide": pos_side, "quantity": qty, "price": price,
        "type": kwargs.get("type"), "timeInForce": kwargs.get("timeInForce"),
    }
    _shadow_orders_log(rec)
    return {
        "orderId": int(time.time() * 1000),
        "symbol": sym, "side": side, "positionSide": pos_side,
        "status": "FILLED", "executedQty": str(qty),
        "avgPrice": str(price) if price else "0",
        "origQty": str(qty), "type": kwargs.get("type", "LIMIT"),
        "_shadow": True,
    }

def _shadow_futures_cancel_order(self, **kwargs):
    return {"status": "CANCELED", "_shadow": True, "orderId": kwargs.get("orderId")}

# ─────────────────────────────────────────────────────────────────────────────
# Shadow orders / positions log (separate from decisions JSONL)
# ─────────────────────────────────────────────────────────────────────────────
_ORDERS_LOG = SHADOW_DIR / f"orders_{SHADOW_ACCOUNT}_{time.strftime('%Y%m%d')}.jsonl"

def _shadow_orders_log(rec: dict):
    """Append-only JSONL of every intercepted order call."""
    try:
        rec["_cfg"] = SHADOW_CONFIG_ID
        rec["_acct"] = SHADOW_ACCOUNT
        with _real_open(_ORDERS_LOG, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception as e:
        slog.error(f"orders_log write failed: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# Apply the binance patches now (before live module imports)
# ─────────────────────────────────────────────────────────────────────────────
try:
    from binance.client import Client as _BinanceClient
    _BinanceClient.futures_create_order = _shadow_futures_create_order
    _BinanceClient.futures_cancel_order = _shadow_futures_cancel_order
    # No-op other order-side methods that might be used
    for _m in ("futures_change_leverage", "futures_change_margin_type", "futures_change_position_mode"):
        if hasattr(_BinanceClient, _m):
            setattr(_BinanceClient, _m, lambda self, **kw: {"_shadow": True})
    slog.info("L3 binance.Client patched")
except ImportError:
    slog.warning("binance not installed — skipping L3")

try:
    from binance import AsyncClient as _BinanceAsyncClient
    async def _async_create(self, **kw):
        return _shadow_futures_create_order(self, **kw)
    async def _async_cancel(self, **kw):
        return _shadow_futures_cancel_order(self, **kw)
    _BinanceAsyncClient.futures_create_order = _async_create
    _BinanceAsyncClient.futures_cancel_order = _async_cancel
    slog.info("L3 binance.AsyncClient patched")
except (ImportError, AttributeError):
    pass

# ─────────────────────────────────────────────────────────────────────────────
# L5. Force is_sandbox_account → True for shadow account (in utils + ez_manage)
# ─────────────────────────────────────────────────────────────────────────────
import utils as _utils_mod
_real_is_sandbox = _utils_mod.is_sandbox_account
def _shadow_is_sandbox(config_obj, account_key: str) -> bool:
    if account_key == SHADOW_ACCOUNT: return True
    return _real_is_sandbox(config_obj, account_key)
_utils_mod.is_sandbox_account = _shadow_is_sandbox

# ─────────────────────────────────────────────────────────────────────────────
# Now import the live module — top-level code runs (config = Config(), etc.)
# ─────────────────────────────────────────────────────────────────────────────
sys.argv = [sys.argv[0], "--account", SHADOW_ACCOUNT]
slog.info(f"importing live module: {SHADOW_PLATFORM}")

if SHADOW_PLATFORM == "crypto":
    import ez_manage as live
else:
    import tradier_manage as live

# Re-bind the patched is_sandbox_account inside the live module's namespace too
live.is_sandbox_account = _shadow_is_sandbox

# ─────────────────────────────────────────────────────────────────────────────
# L4. Patch TradierAPIClient.place_order (post-import; tradier only)
# ─────────────────────────────────────────────────────────────────────────────
if SHADOW_PLATFORM == "tradier":
    try:
        from tradier_api import TradierAPIClient as _TAC
        async def _shadow_place_order(self, symbol, side, quantity, order_type="limit",
                                       price=None, stop=None, duration="day",
                                       action=None, position_side=None, account_key="trb"):
            rec = {
                "ts": time.time(), "kind": "tradier_place_order", "symbol": symbol,
                "side": side, "quantity": quantity, "order_type": order_type,
                "price": price, "stop": stop, "duration": duration,
                "action": action, "position_side": position_side, "account_key": account_key,
            }
            _shadow_orders_log(rec)
            # Tradier API success-shaped response
            return {"order": {"id": int(time.time() * 1000), "status": "filled", "_shadow": True}}
        _TAC.place_order = _shadow_place_order
        # also no-op connect / cancel to be safe
        async def _shadow_connect(self, *a, **kw): return True
        async def _shadow_cancel(self, *a, **kw): return {"_shadow": True}
        _TAC.connect = _shadow_connect
        if hasattr(_TAC, "cancel_order"): _TAC.cancel_order = _shadow_cancel
        # Also patch the manager's place_order if present
        if hasattr(live, "TradierTradeManager") and hasattr(live.TradierTradeManager, "place_order"):
            live.TradierTradeManager.place_order = _shadow_place_order
        slog.info("L4 tradier_api.TradierAPIClient patched")
    except ImportError as e:
        slog.warning(f"tradier_api not importable: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# Patch crypto-side place_maker_order to log + return success without network
# ─────────────────────────────────────────────────────────────────────────────
if SHADOW_PLATFORM == "crypto" and hasattr(live, "MultiAccountTradeManager"):
    async def _shadow_place_maker(self, account_key, position_key, symbol, positionAmt,
                                   current_price, qty_abs, side, position_side, unique_id, reason):
        rec = {
            "ts": time.time(), "kind": "place_maker_order", "position_key": position_key,
            "symbol": symbol, "side": side, "position_side": position_side,
            "qty": qty_abs, "price": current_price, "reason": reason, "uid": unique_id,
        }
        _shadow_orders_log(rec)
        return True, qty_abs
    live.MultiAccountTradeManager.place_maker_order = _shadow_place_maker
    # send_webhook is already short-circuited by is_sandbox_account → True, but
    # log the attempt so the report can see what would have webhook'd
    _real_send_webhook = live.MultiAccountTradeManager.send_webhook
    async def _logging_send_webhook(self, position_key, account_key, symbol, positionAmt, amount,
                                     current_price, side, position_side, unique_id, is_full_close,
                                     reason, level=None, stoch_required=False, order_ids_to_cancel=None,
                                     url_variant=""):
        rec = {
            "ts": time.time(), "kind": "send_webhook", "position_key": position_key,
            "symbol": symbol, "side": side, "position_side": position_side,
            "amount": amount, "price": current_price, "reason": reason,
            "is_full_close": is_full_close, "url_variant": url_variant, "uid": unique_id,
        }
        _shadow_orders_log(rec)
        return True
    live.MultiAccountTradeManager.send_webhook = _logging_send_webhook
    slog.info("L3 MultiAccountTradeManager.place_maker_order + send_webhook patched")

# ─────────────────────────────────────────────────────────────────────────────
# Empty-start enforcement: shadow positions accumulate from zero (user choice)
# ─────────────────────────────────────────────────────────────────────────────
if SHADOW_PLATFORM == "crypto" and hasattr(live, "MultiAccountTradeManager"):
    _MATM = live.MultiAccountTradeManager
    # Block snapshot ingestion (positions_service hand-off would carry live state)
    _MATM._positions_from_snapshot_payload = lambda self, account_key, snapshot_payload: {}
    # Block disk load
    async def _shadow_load_with_lock(self, master_allowed_symbols):
        self.positions_by_account.setdefault(SHADOW_ACCOUNT, {})
        slog.info(f"empty-start: positions_by_account[{SHADOW_ACCOUNT}] initialized empty")
        return
    _MATM._load_positions_with_lock = _shadow_load_with_lock
    # Defense in depth: also no-op the (already-killed) Redis loader and load_all_positions
    async def _empty_redis_load(self, account_key=None): return {}
    async def _empty_load_all(self): return
    _MATM._load_positions_from_redis = _empty_redis_load
    _MATM.load_all_positions = _empty_load_all
    slog.info("empty-start: position loaders patched (snapshot/disk/redis/all)")

if SHADOW_PLATFORM == "tradier" and hasattr(live, "TradierTradeManager"):
    _TTM = live.TradierTradeManager
    # Tradier loads positions in __init__ + via TradierAPIClient.get_account_positions.
    # Patch the API call to return empty so shadow starts with zero stock positions.
    try:
        from tradier_api import TradierAPIClient as _TAC2
        async def _empty_get_positions(self, account_key=None): return []
        _TAC2.get_account_positions = _empty_get_positions
        slog.info("empty-start: TradierAPIClient.get_account_positions → []")
    except ImportError:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# L6. SANDBOX_MODE on + add shadow account to SANDBOX_ACCOUNTS
# ─────────────────────────────────────────────────────────────────────────────
live.config.SANDBOX_MODE = True
_sbx = list(getattr(live.config, "SANDBOX_ACCOUNTS", []) or [])
if SHADOW_ACCOUNT not in _sbx: _sbx.append(SHADOW_ACCOUNT)
live.config.SANDBOX_ACCOUNTS = _sbx
slog.info(f"L6 SANDBOX_MODE=True, SANDBOX_ACCOUNTS={_sbx}")

# ─────────────────────────────────────────────────────────────────────────────
# Apply user config overrides
# ─────────────────────────────────────────────────────────────────────────────
_applied = []; _missing = []
for k, v in OVERRIDES.items():
    if hasattr(live.config, k):
        setattr(live.config, k, v); _applied.append(k)
    else:
        # Still set it — Config uses getattr with defaults extensively
        setattr(live.config, k, v); _missing.append(k)
slog.info(f"OVERRIDES applied={len(_applied)} new_attrs={len(_missing)}")
if _missing:
    slog.info(f"  new attrs (not in dataclass defaults): {_missing[:20]}")

# Also override HaikuOverseer.DECISION_DIR for crypto (defense in depth on top of L8)
if SHADOW_PLATFORM == "crypto" and hasattr(live, "HaikuOverseer"):
    live.HaikuOverseer.DECISION_DIR = _SHADOW_DECISIONS_DIR
    slog.info(f"HaikuOverseer.DECISION_DIR → {_SHADOW_DECISIONS_DIR}")

# ─────────────────────────────────────────────────────────────────────────────
# Run live main()
# ─────────────────────────────────────────────────────────────────────────────
slog.info("HANDOFF → live.main()")
try:
    asyncio.run(live.main())
except KeyboardInterrupt:
    slog.info("interrupted")
except Exception as e:
    slog.exception(f"live.main() crashed: {e}")
    sys.exit(1)
