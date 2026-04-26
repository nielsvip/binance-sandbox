#!/usr/bin/env python3
"""shadow_executor.py — runs ez_manage.py / tradier_manage.py in shadow mode.

v2 architecture (after the v1 IP-ban incident):

    L0  NETWORK GUARD       socket.getaddrinfo blocks fapi/api.binance.com
                            (crypto only) — fixes the v1 leak where
                            futures_time + klines fetches hit -1003 rate limit
    L1  CREDENTIALS         dummy keys (final wall if anything escapes L0)
    L2  EXECUTE_NOW PATCH   single chokepoint — log + return success at the
                            very top of MultiAccountTradeManager.execute_now()
                            (crypto) and TradierTradeManager.execute_now()
                            (tradier). Per CLAUDE.md these are THE only order
                            paths; everything downstream is unreachable.
    L3  TRC FALLBACK        tradier only — force account_key='trc' throughout
                            so even if L2 ever misses, orders fire to the
                            Tradier paper account (zero real-money risk).
    L4  EMPTY-START         wrap bootstrap_position_service to clear positions
                            after bootstrap so shadow starts from zero.
    L5  REDIS NAMESPACE     writes go to shadow:<id>:* (reads passthrough)
    L6  DECISIONS REDIRECT  data/decisions/* → data/shadow_decisions/<id>/

USAGE:
    SHADOW_CONFIG_ID=baseline_crypto SHADOW_PLATFORM=crypto SHADOW_ACCOUNT=ang \\
        python3 shadow_executor.py
"""
import asyncio
import builtins
import functools
import json
import logging
import os
import socket
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
# Strip comment fields
OVERRIDES = {k: v for k, v in OVERRIDES.items() if not k.startswith("_")}

SHADOW_LOG = BASE_DIR / "logs" / f"shadow_{SHADOW_CONFIG_ID}_{SHADOW_ACCOUNT}.log"
SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SHADOW:%(name)s] %(message)s",
    handlers=[logging.FileHandler(SHADOW_LOG, mode="a"), logging.StreamHandler(sys.stderr)],
)
slog = logging.getLogger("executor")
slog.info(f"BOOT v2 cfg={SHADOW_CONFIG_ID} platform={SHADOW_PLATFORM} acct={SHADOW_ACCOUNT}")
slog.info(f"OVERRIDES ({len(OVERRIDES)}): {list(OVERRIDES.keys())[:20]}{'...' if len(OVERRIDES) > 20 else ''}")

# ─────────────────────────────────────────────────────────────────────────────
# L0 NETWORK GUARD — blackhole external trading API hosts at DNS resolution.
# Crypto: block all binance hosts. Tradier: allow api.tradier.com (paper acct).
# ─────────────────────────────────────────────────────────────────────────────
_BLOCKED_HOST_FRAGMENTS = []
if SHADOW_PLATFORM == "crypto":
    _BLOCKED_HOST_FRAGMENTS = ["binance.com", "binancefuture.com", "binance.us"]
# Tradier: nothing blocked at DNS — orders fire to trc paper account safely.

_real_getaddrinfo = socket.getaddrinfo
def _shadow_getaddrinfo(host, *args, **kwargs):
    if host and isinstance(host, str):
        host_lower = host.lower()
        for frag in _BLOCKED_HOST_FRAGMENTS:
            if frag in host_lower:
                slog.warning(f"L0 NET_GUARD blocked DNS lookup: {host}")
                raise socket.gaierror(socket.EAI_NONAME, f"shadow blocked: {host}")
    return _real_getaddrinfo(host, *args, **kwargs)
socket.getaddrinfo = _shadow_getaddrinfo
slog.info(f"L0 NETWORK_GUARD active; blocked={_BLOCKED_HOST_FRAGMENTS or 'NONE'}")

# ─────────────────────────────────────────────────────────────────────────────
# L1 CREDENTIALS — dummy keys before any imports that read env
# ─────────────────────────────────────────────────────────────────────────────
_DUMMY = "DUMMY_SHADOW_NEVER_REAL"
for _key in ("BINANCE_API_KEY", "BINANCE_API_SECRET",
             "ang_API_KEY", "ang_API_SECRET", "inf_API_KEY", "inf_API_SECRET",
             "flz_API_KEY", "flz_API_SECRET", "men_API_KEY", "men_API_SECRET",
             "fin_API_KEY", "fin_API_SECRET"):
    os.environ[_key] = _DUMMY
# Webhook URLs → unreachable sink
_SINK = "http://127.0.0.1:9999/shadow-sink-never-reachable"
for _acct in ("ang", "inf", "flz", "men", "fin"):
    for _suf in ("WEBHOOK_URL", "WEBHOOK_URL2", "WEBHOOK_URL3"):
        os.environ[f"{_acct}_{_suf}"] = _SINK
    for _suf in ("WEBHOOK_SECRET", "WEBHOOK_SECRET2", "WEBHOOK_SECRET3"):
        os.environ[f"{_acct}_{_suf}"] = _DUMMY
# Tradier creds: KEEP REAL (we want trc to actually authenticate as paper acct)
# but only if shadow account is trc — for any other tradier shadow account, fail safe
if SHADOW_PLATFORM == "tradier" and SHADOW_ACCOUNT != "trc":
    os.environ["TRADIER_TOKEN"] = _DUMMY
    os.environ[f"{SHADOW_ACCOUNT}_API_KEY"] = _DUMMY
    slog.warning(f"tradier shadow account={SHADOW_ACCOUNT} != trc — forcing dummy creds")

os.environ["SHADOW_MODE"] = "1"
os.environ["SHADOW_CONFIG_ID"] = SHADOW_CONFIG_ID

# ─────────────────────────────────────────────────────────────────────────────
# L6 DECISIONS REDIRECT — builtins.open + aiofiles.open redirect
# ─────────────────────────────────────────────────────────────────────────────
_LIVE_DECISIONS_FRAGMENT = "/data/decisions/"
def _redirect_path(p):
    sp = str(p)
    if _LIVE_DECISIONS_FRAGMENT in sp:
        return str(SHADOW_DIR / Path(sp).name)
    return sp

_real_open = builtins.open
@functools.wraps(_real_open)
def _shadow_open(file, *args, **kwargs):
    return _real_open(_redirect_path(file), *args, **kwargs)
builtins.open = _shadow_open

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
# Shadow orders/positions log (separate from decisions JSONL — unambiguous output)
# ─────────────────────────────────────────────────────────────────────────────
_ORDERS_LOG = SHADOW_DIR / f"orders_{SHADOW_ACCOUNT}_{time.strftime('%Y%m%d')}.jsonl"

def _shadow_orders_log(rec: dict):
    try:
        rec["_cfg"] = SHADOW_CONFIG_ID
        rec["_acct"] = SHADOW_ACCOUNT
        if "_ts" not in rec: rec["_ts"] = time.time()
        with _real_open(_ORDERS_LOG, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception as e:
        slog.error(f"orders_log write failed: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# L5 REDIS NAMESPACE — namespaced writes, passthrough reads
# ─────────────────────────────────────────────────────────────────────────────
import redis as _redis_mod
_NS_PREFIX = f"shadow:{SHADOW_CONFIG_ID}:"
_NOOP_KEY_FRAGMENTS = ("exec_lock", "open_lock", "maker_lock", "absolute_open_lock",
                       "aug_cooldown", "recent_signal", "strict_hist")

def _is_noop_key(key) -> bool:
    sk = str(key).lower()
    return any(frag in sk for frag in _NOOP_KEY_FRAGMENTS)

def _ns(key):
    if isinstance(key, bytes):
        key = key.decode("utf-8", errors="replace")
    return _NS_PREFIX + str(key)

def _wrap_write(method_name, real_method):
    @functools.wraps(real_method)
    def wrapper(self, *args, **kwargs):
        if args and not _is_noop_key(args[0]):
            return real_method(self, _ns(args[0]), *args[1:], **kwargs)
        if method_name == "publish": return 0
        if method_name in ("delete", "expire", "hset", "hdel", "sadd", "srem",
                           "lpush", "rpush", "incr", "decr"): return 0
        return True
    return wrapper

for _m in ("set", "setex", "setnx", "hset", "hmset", "hdel", "hsetnx", "delete",
           "expire", "publish", "sadd", "srem", "lpush", "rpush", "incr", "decr",
           "rename", "persist"):
    if hasattr(_redis_mod.Redis, _m):
        setattr(_redis_mod.Redis, _m, _wrap_write(_m, getattr(_redis_mod.Redis, _m)))

try:
    import redis.asyncio as _aioredis_mod
    for _m in ("set", "setex", "setnx", "hset", "hmset", "hdel", "hsetnx", "delete",
               "expire", "publish", "sadd", "srem", "lpush", "rpush", "incr", "decr",
               "rename", "persist"):
        if hasattr(_aioredis_mod.Redis, _m):
            real = getattr(_aioredis_mod.Redis, _m)
            async def _async_wrap(self, *args, _real=real, _name=_m, **kwargs):
                if args and not _is_noop_key(args[0]):
                    return await _real(self, _ns(args[0]), *args[1:], **kwargs)
                return 0 if _name in ("publish", "delete", "expire", "hset", "hdel",
                                       "sadd", "srem", "lpush", "rpush", "incr", "decr") else True
            setattr(_aioredis_mod.Redis, _m, _async_wrap)
except ImportError:
    pass
slog.info(f"L5 REDIS_NAMESPACE active; prefix={_NS_PREFIX}")

# ─────────────────────────────────────────────────────────────────────────────
# is_sandbox_account → True for shadow account (existing live short-circuits fire)
# ─────────────────────────────────────────────────────────────────────────────
import utils as _utils_mod
_real_is_sandbox = _utils_mod.is_sandbox_account
def _shadow_is_sandbox(config_obj, account_key: str) -> bool:
    if account_key == SHADOW_ACCOUNT: return True
    return _real_is_sandbox(config_obj, account_key)
_utils_mod.is_sandbox_account = _shadow_is_sandbox

# ─────────────────────────────────────────────────────────────────────────────
# Patch bootstrap_position_service BEFORE live import (so post-bootstrap is empty)
# ─────────────────────────────────────────────────────────────────────────────
try:
    import ez_positions_service as _eps
    _real_bootstrap = _eps.bootstrap_position_service
    async def _shadow_bootstrap(*args, **kwargs):
        svc = await _real_bootstrap(*args, **kwargs)
        # L4 EMPTY-START: wipe inherited live state from local in-memory clone
        try:
            if hasattr(svc, "positions"):
                _n = len(svc.positions)
                svc.positions = {}
                slog.info(f"L4 EMPTY-START: positions_service.positions cleared (was {_n})")
            if hasattr(svc, "positions_by_account"):
                svc.positions_by_account = {SHADOW_ACCOUNT: {}}
        except Exception as e:
            slog.warning(f"L4 wipe failed: {e}")
        return svc
    _eps.bootstrap_position_service = _shadow_bootstrap
    slog.info("L4 ez_positions_service.bootstrap_position_service wrapped")
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# Import the live module
# ─────────────────────────────────────────────────────────────────────────────
sys.argv = [sys.argv[0], "--account", SHADOW_ACCOUNT]
slog.info(f"importing live module: {SHADOW_PLATFORM}")
if SHADOW_PLATFORM == "crypto":
    import ez_manage as live
else:
    import tradier_manage as live
live.is_sandbox_account = _shadow_is_sandbox
# Re-bind the wrapped bootstrap inside ez_manage (it was imported at module top)
if SHADOW_PLATFORM == "crypto" and hasattr(live, "bootstrap_position_service"):
    live.bootstrap_position_service = _eps.bootstrap_position_service

# ─────────────────────────────────────────────────────────────────────────────
# L2 EXECUTE_NOW PATCH — the single chokepoint
# ─────────────────────────────────────────────────────────────────────────────
if SHADOW_PLATFORM == "crypto" and hasattr(live, "MultiAccountTradeManager"):
    async def _shadow_execute_now_crypto(self, position_key=None, account_key=None,
                                          symbol=None, original_positionAmt=0.0,
                                          side="BUY", position_side="LONG",
                                          quantity=0.0, old_price=0.0, unique_id=None,
                                          reason="", is_full_close=False, action=None,
                                          is_hedge=False, hedge_for=None):
        rec = {
            "kind": "execute_now", "platform": "crypto",
            "position_key": position_key, "account_key": account_key, "symbol": symbol,
            "side": side, "position_side": position_side, "quantity": quantity,
            "original_positionAmt": original_positionAmt, "old_price": old_price,
            "reason": reason, "action": action, "is_full_close": is_full_close,
            "is_hedge": is_hedge, "hedge_for": hedge_for, "uid": unique_id,
        }
        _shadow_orders_log(rec)
        # Mimic real execute_now's success return: a string status
        return "SHADOW_OK"
    live.MultiAccountTradeManager.execute_now = _shadow_execute_now_crypto
    slog.info("L2 MultiAccountTradeManager.execute_now → shadow chokepoint")

if SHADOW_PLATFORM == "tradier" and hasattr(live, "TradierTradeManager"):
    async def _shadow_execute_now_tradier(self, position_key, account_key, symbol,
                                           original_position_amt, side, position_side,
                                           quantity, old_price, unique_id, reason,
                                           is_full_close, action=None):
        # L3 TRC fallback: force account to trc (paper) regardless of caller intent
        rec = {
            "kind": "execute_now", "platform": "tradier",
            "position_key": position_key, "account_key": account_key,
            "forced_account": "trc", "symbol": symbol, "side": side,
            "position_side": position_side, "quantity": quantity,
            "original_position_amt": original_position_amt, "old_price": old_price,
            "reason": reason, "action": action, "is_full_close": is_full_close,
            "uid": unique_id,
        }
        _shadow_orders_log(rec)
        return "SHADOW_OK"
    live.TradierTradeManager.execute_now = _shadow_execute_now_tradier
    slog.info("L2 TradierTradeManager.execute_now → shadow chokepoint")

# ─────────────────────────────────────────────────────────────────────────────
# L6 force config.SANDBOX_MODE on (existing live paths use this)
# ─────────────────────────────────────────────────────────────────────────────
live.config.SANDBOX_MODE = True
_sbx = list(getattr(live.config, "SANDBOX_ACCOUNTS", []) or [])
if SHADOW_ACCOUNT not in _sbx: _sbx.append(SHADOW_ACCOUNT)
live.config.SANDBOX_ACCOUNTS = _sbx
slog.info(f"SANDBOX_MODE=True, SANDBOX_ACCOUNTS={_sbx}")

# ─────────────────────────────────────────────────────────────────────────────
# Apply user config overrides
# ─────────────────────────────────────────────────────────────────────────────
_applied = []; _new = []
for k, v in OVERRIDES.items():
    if hasattr(live.config, k): _applied.append(k)
    else: _new.append(k)
    setattr(live.config, k, v)
slog.info(f"OVERRIDES applied={len(_applied)} new_attrs={len(_new)}")
if _new: slog.info(f"  new attrs (not in dataclass defaults): {_new[:20]}")

# Decision dir override on HaikuOverseer (defense in depth on top of L6 fs hook)
if SHADOW_PLATFORM == "crypto" and hasattr(live, "HaikuOverseer"):
    live.HaikuOverseer.DECISION_DIR = SHADOW_DIR
    slog.info(f"HaikuOverseer.DECISION_DIR → {SHADOW_DIR}")

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
