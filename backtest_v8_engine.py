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
# STEP -1: Deterministic execution (NO-LIES 2026-05-12)
# ═══════════════════════════════════════════════════════════════
# Python uses a random PYTHONHASHSEED each invocation → set() iteration
# order varies across runs → same inputs produce different trades. Sweep
# results become un-comparable. Re-exec ourselves with PYTHONHASHSEED=0
# if it isn't already set. Also seed Python random + numpy random for any
# downstream code that uses them.
if os.environ.get("PYTHONHASHSEED") != "0" and os.environ.get("V8_HASHSEED_LOCKED") != "1":
    os.environ["PYTHONHASHSEED"] = "0"
    os.environ["V8_HASHSEED_LOCKED"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)
import random as _v8_random
_v8_random.seed(0)
try:
    import numpy as _v8_np_seed
    _v8_np_seed.random.seed(0)
except Exception:
    pass

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
    # V8_ARROW_DEBUG: allow WARNING+ through so entry-veto guard logs (all
    # WARNING/CRITICAL) are visible for forensics — INFO per-bar spam stays off.
    if os.environ.get("V8_ARROW_DEBUG"):
        logging.disable(logging.INFO)
    else:
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
        "MISSING_FIELD", "V8_PNL_BREAKDOWN", "V8_TRADES_OUT", "V8_ARROW",
        "MODE_CONFIG_MISMATCH", "V8_VEC_SHADOW", "V8_VEC_STATS",
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

# ═══════════════════════════════════════════════════════════════
# 2026-07-07 FULL CONFIG SNAPSHOT (user mandate: "EVERY single setting for
# every single symbol in every single test" must be recorded — CLAUDE.md
# NO-LIES MANDATE extended to config provenance, not just metrics). Snapshot
# the complete resolved Config dataclass (defaults + any override applied
# above) next to the override file (or under data/_config_snapshots/ for
# baseline/no-override runs), and expose the path via env so any caller —
# even the ~50 orchestrator scripts that invoke this engine directly and
# haven't individually been wired for this — can pick it up. Pure side
# effect: does not read from or alter trading/signal state, so it cannot
# change engine output.
# ═══════════════════════════════════════════════════════════════
try:
    from dataclasses import asdict as _v8_asdict
    if _override_file:
        _snap_dir = Path(_override_file).resolve().parent
        _snap_stem = Path(_override_file).stem
    else:
        _snap_dir = Path(getattr(config, "BASE_PATH", ".")) / "data" / "_config_snapshots"
        _snap_stem = f"backtest_v8_engine_noOverride_{os.getpid()}_{int(_real_time_module.time())}"
    _snap_dir.mkdir(parents=True, exist_ok=True)
    _snap_path = _snap_dir / f"{_snap_stem}.resolved_config.json"
    with open(_snap_path, "w") as _sf:
        json.dump(
            {
                "engine": "backtest_v8_engine",
                "override_file": _override_file or None,
                "overrides_applied": _overrides if _override_file and Path(_override_file).exists() else {},
                "resolved_config": _v8_asdict(_cfg_instance),
            },
            _sf, default=str,
        )
    os.environ["V8_CONFIG_SNAPSHOT_PATH"] = str(_snap_path)
except Exception as _snap_exc:
    v8_logger.warning(f"config snapshot write failed (non-fatal, does not affect trading logic): {_snap_exc}")

# Import the REAL trading modules
import ez_manage
import ez_positions_quick
import ez_positions_service
# 2026-04-28 — Centralized reentry facade. All v8 reentry calls now route through
# ez_reentry so live + backtest + daemon share one import surface.
import ez_reentry
from utils import parse_position_key, construct_position_key, load_environment_from_gpg, get_simple_redis_manager

# ═══════════════════════════════════════════════════════════════
# 2026-05-12 — VEC PARITY MODULES (5 paired _core/_vec gates, all 100% bit-identical)
# Each opt-in via env var. Default OFF; live behavior unchanged.
# Smoke: tools/test_*_parity.py (1000–7000 synthetic bars per gate, 0 diffs each)
#
# Integration recipes per module:
#   evaluate_noloss_gate_core(...)                # exit-side noloss + OBLIGATORY_HEDGE
#                                                 # call before placing reduce/close
#                                                 # returns (action, reason, hedge_fire, hedge_qty)
#   evaluate_augment_eligibility_core(...)        # augment/reentry/open gate cascade
#                                                 # call before AUGMENT execution
#                                                 # returns (allowed, reason, qty, sub_gate)
#   BrakeLookup(events, config).is_throttled(...) # EMERGENCY_BRAKE rate limiter
#                                                 # call once at sim start, lookup per-bar
#   evaluate_hedge_scan_gates_core(...)           # 7-gate hedge-scan cluster
#                                                 # call in scan loop per position
#                                                 # returns (fire, qty, reason, blocked_by)
#   backtest_should_block(bar_ts, cfg, ...)       # stale-mark-price gate (default no-op)
#                                                 # only fires under V8_BACKTEST_SIMULATE_STALE_MARK=1
# ═══════════════════════════════════════════════════════════════
try:
    from position_evaluator import (
        evaluate_noloss_gate_core,
        evaluate_noloss_gate_vec,
        evaluate_augment_eligibility_core,
        evaluate_augment_eligibility_vec,
        evaluate_emergency_brake_core,
        evaluate_emergency_brake_vec,
        BrakeLookup,
        NOLOSS_ACTION_HOLD, NOLOSS_ACTION_ALLOW_REDUCE, NOLOSS_ACTION_CLOSE_HEDGE_FAILED,
        SUB_GATE_NONE_ALLOWED,
    )
    from vec_paths.hedge_engine import evaluate_hedge_scan_gates_core
    from vec_paths.stale_mark_price import backtest_should_block as evaluate_stale_mark_block_backtest
    # 2026-05-12 — Stage 1 wiring: 7 additional gate clusters (all parity-tested,
    # 100% bit-identical to live scalar twins over 1000–7000 synthetic cases each).
    from vec_paths.newborn_protect import (
        evaluate_newborn_protect_core,
        evaluate_newborn_protect_vec,
    )
    from vec_paths.cooldown_locks import (
        evaluate_cooldown_locks_core,
        evaluate_cooldown_locks_vec,
    )
    from vec_paths.protect_balance_overtrade import (
        evaluate_protect_balance_overtrade_core,
        evaluate_protect_balance_overtrade_vec,
    )
    from vec_paths.circuit_sharpe_gates import (
        evaluate_circuit_sharpe_gates_core,
        evaluate_circuit_sharpe_gates_vec,
    )
    from vec_paths.tradeable_state_gates import (
        evaluate_tradeable_state_gates_core,
        evaluate_tradeable_state_gates_vec,
    )
    from vec_paths.open_intent_size_gates import (
        evaluate_open_intent_size_gates_core,
        evaluate_open_intent_size_gates_vec,
    )
    from vec_paths.quarantine_strategy_validation import (
        evaluate_quarantine_core,
        evaluate_quarantine_vec,
        evaluate_vec_strategy_gates_parity_core,
        evaluate_vec_strategy_gates_parity_vec,
    )
    V8_VEC_PARITY_AVAILABLE = True
except ImportError as _e:
    V8_VEC_PARITY_AVAILABLE = False
    print(f"[WARN] vec parity modules not available: {_e}")

V8_USE_VEC_NOLOSS_GATE         = os.environ.get("V8_USE_VEC_NOLOSS_GATE",         "0") == "1"
V8_USE_VEC_AUGMENT_GATE        = os.environ.get("V8_USE_VEC_AUGMENT_GATE",        "0") == "1"
V8_USE_VEC_EMERGENCY_BRAKE     = os.environ.get("V8_USE_VEC_EMERGENCY_BRAKE",     "0") == "1"
V8_USE_VEC_HEDGE_SCAN_GATES    = os.environ.get("V8_USE_VEC_HEDGE_SCAN_GATES",    "0") == "1"
V8_USE_VEC_STALE_MARK          = os.environ.get("V8_USE_VEC_STALE_MARK",          "0") == "1"
# 2026-05-12 — Stage 1: 7 new opt-in flags for the just-built parity modules.
V8_USE_VEC_NEWBORN_PROTECT     = os.environ.get("V8_USE_VEC_NEWBORN_PROTECT",     "0") == "1"
V8_USE_VEC_COOLDOWN_LOCKS      = os.environ.get("V8_USE_VEC_COOLDOWN_LOCKS",      "0") == "1"
V8_USE_VEC_PROTECT_BALANCE     = os.environ.get("V8_USE_VEC_PROTECT_BALANCE",     "0") == "1"
V8_USE_VEC_CIRCUIT_SHARPE      = os.environ.get("V8_USE_VEC_CIRCUIT_SHARPE",      "0") == "1"
V8_USE_VEC_TRADEABLE_STATE     = os.environ.get("V8_USE_VEC_TRADEABLE_STATE",     "0") == "1"
V8_USE_VEC_OPEN_INTENT_SIZE    = os.environ.get("V8_USE_VEC_OPEN_INTENT_SIZE",    "0") == "1"
V8_USE_VEC_QUARANTINE_STRATEGY = os.environ.get("V8_USE_VEC_QUARANTINE_STRATEGY", "0") == "1"
# Shadow-validator: non-invasive audit mode — engine behavior unchanged, but every
# decision point is also evaluated by the corresponding vec module and divergences
# logged to /tmp/v8_vec_divergences.jsonl. Safe to leave on in CI / sweeps.
V8_VEC_SHADOW_VALIDATE         = os.environ.get("V8_VEC_SHADOW_VALIDATE",         "0") == "1"
# 2026-05-29 — MTF ARMED-STATE ENTRY GATE (parity with live ez_manage.py:22791-22839).
# Live config.MTF_ARMED_ENTRY_ENABLED defaults True (active protection): every
# OPEN/AUGMENT/ENTRY (NOT REENTRY/hedge) is gated by mtf_live_evaluator's armed-state
# + GR multi-confirm filter. backtest_v8_engine had 0 MTF refs → it over-fired entries
# the live system blocks. This env flag mirrors the live config default: the gate fires
# whenever config.MTF_ARMED_ENTRY_ENABLED is True. Set V8_DISABLE_MTF_GATE=1 to A/B it OFF.
V8_DISABLE_MTF_GATE            = os.environ.get("V8_DISABLE_MTF_GATE",            "0") == "1"
V8_USE_VEC_ALL                 = os.environ.get("V8_USE_VEC_ALL",                 "0") == "1"
V8_BACKTEST_END_DATE           = os.environ.get("V8_BACKTEST_END_DATE",            "")  # YYYY-MM-DD; if set, simulation stops at this date
if V8_USE_VEC_ALL:
    V8_USE_VEC_NOLOSS_GATE = V8_USE_VEC_AUGMENT_GATE = V8_USE_VEC_EMERGENCY_BRAKE = True
    V8_USE_VEC_HEDGE_SCAN_GATES = V8_USE_VEC_STALE_MARK = True
    V8_USE_VEC_NEWBORN_PROTECT = V8_USE_VEC_COOLDOWN_LOCKS = V8_USE_VEC_PROTECT_BALANCE = True
    V8_USE_VEC_CIRCUIT_SHARPE = V8_USE_VEC_TRADEABLE_STATE = True
    V8_USE_VEC_OPEN_INTENT_SIZE = V8_USE_VEC_QUARANTINE_STRATEGY = True

# 2026-05-12 — SIGNAL-ONLY / DECISION-ONLY MODE (USER MANDATE)
# Bypass execute_trade_action quantity/sizing/balance math AND most of execute_now.
# Keep only: (a) double-open reclassification, (b) hedge-obligation logging,
# (c) JSONL decision log compatible with data/history/<acct>/*.jsonl schema.
# Live is trading min-qty only until backtest entry/exit TIMING matches /history.
# qty becomes a 1.0 placeholder; gain/sharpe become noisy but ENTRY/EXIT TIMES are
# the only thing that matters in this mode. Expected speedup: 10-50x.
V8_DECISION_ONLY               = os.environ.get("V8_DECISION_ONLY",               "0") == "1"
V8_DECISION_OUT_DIR            = os.environ.get("V8_DECISION_OUT_DIR",            "")  # required when DECISION_ONLY=1
# Per-(account, position_key) decision log handle cache. One JSONL file per
# (acct, sym, side) mirroring data/history/<acct>/<SYMBOL>_<SIDE>.jsonl schema.
_V8_DECISION_FILE_HANDLES: Dict[str, Any] = {}
_V8_DECISION_COUNTERS: Dict[str, int] = {"opens": 0, "augments": 0, "reduces": 0, "closes": 0, "hedges": 0, "doubleopen_reclass": 0, "blocks": 0}

def _v8_decision_log(*, account_key: str, position_key: str, symbol: str, side_long: bool,
                     action: str, reason: str, price: float, sim_ts, indicators: dict = None):
    """Append a decision event to data/<DECISION_OUT_DIR>/<acct>/<SYMBOL>_<SIDE>.jsonl
    in the schema used by data/history/. qty is a 1.0 placeholder (we ignore size).
    No-op if V8_DECISION_OUT_DIR is unset."""
    if not V8_DECISION_OUT_DIR:
        return
    try:
        side_str = "LONG" if side_long else "SHORT"
        acct_dir = os.path.join(V8_DECISION_OUT_DIR, account_key)
        if not os.path.isdir(acct_dir):
            os.makedirs(acct_dir, exist_ok=True)
        fname = f"{symbol}_{side_str}.jsonl"
        cache_key = f"{account_key}:{fname}"
        fh = _V8_DECISION_FILE_HANDLES.get(cache_key)
        if fh is None:
            fh = open(os.path.join(acct_dir, fname), "a", buffering=1)
            _V8_DECISION_FILE_HANDLES[cache_key] = fh
        # Convert sim_ts (float epoch) to ISO format
        try:
            ts_dt = datetime.fromtimestamp(float(sim_ts), tz=timezone.utc) if sim_ts else datetime.now(timezone.utc)
            ts_iso = ts_dt.isoformat()
        except Exception:
            ts_iso = str(sim_ts)
        ind_clean = {}
        if indicators:
            for k in ("k_1m", "d_1m", "k_3m", "d_3m", "k_15m", "d_15m",
                      "wt1_15m", "wt2_15m", "wt1_3m", "wt2_3m", "wt1_1h", "wt2_1h",
                      "wt1_4h", "wt2_4h", "wt1_D", "wt2_D", "rsi_15m", "rsi_1h"):
                v = indicators.get(k)
                if v is not None:
                    try:
                        ind_clean[k] = float(v)
                    except (TypeError, ValueError):
                        pass
        evt = {
            "ts": ts_iso,
            "type": action.upper(),
            "qty": 1.0,
            "price": float(price) if price else 0.0,
            "value": float(price) if price else 0.0,
            "reason": (reason or "")[:200],
            "indicators": ind_clean,
            "_v8_decision_only": True,
        }
        fh.write(json.dumps(evt) + "\n")
    except Exception as _dl_err:
        if os.environ.get("V8_DECISION_LOG_DEBUG") == "1":
            print(f"[V8_DECISION_LOG_ERR] {account_key}:{position_key}: {_dl_err}", flush=True)

def _v8_decision_close_files():
    """Close all decision log file handles. Called at sim end."""
    for fh in list(_V8_DECISION_FILE_HANDLES.values()):
        try: fh.close()
        except Exception: pass
    _V8_DECISION_FILE_HANDLES.clear()

# ═══════════════════════════════════════════════════════════════
# 2026-05-12 — VEC SHORT-CIRCUIT HELPERS
# Centralized vec-path early-return logic shared by _crypto_eta,
# _v8_execute_trade_action and _v8_execute_now. Each helper returns
# (blocked: bool, reason: str). Blocked → caller returns reason immediately.
#
# When the corresponding V8_USE_VEC_* flag is 0 → the helper returns
# (False, "") and the engine continues to its original scalar gate.
# All helpers are wrapped in defensive try/except — failures fail-open
# so a broken vec module never breaks the sim.
# ═══════════════════════════════════════════════════════════════
_V8_VEC_STATS: Dict[str, int] = {
    "noloss_blocks": 0, "augment_blocks": 0, "brake_blocks": 0,
    "stale_blocks": 0, "cooldown_blocks": 0, "open_intent_blocks": 0,
    "protect_balance_blocks": 0, "circuit_sharpe_blocks": 0,
    "tradeable_state_blocks": 0, "quarantine_blocks": 0,
    "hedge_scan_blocks": 0,
    "noloss_calls": 0, "augment_calls": 0, "brake_calls": 0,
    "stale_calls": 0, "cooldown_calls": 0, "open_intent_calls": 0,
    "protect_balance_calls": 0, "circuit_sharpe_calls": 0,
    "tradeable_state_calls": 0, "quarantine_calls": 0,
    "hedge_scan_calls": 0,
}

# Lazy BrakeLookup singleton — built on first call per sim. Backtest mode
# does not maintain a /decisions/ file (live-only artifact). When empty
# event source: load_decision_events returns [] and brake is fail-open.
_V8_BRAKE_LOOKUP: Dict[str, Any] = {"obj": None, "built": False}

# Lazy quarantine set — load once from data/_quarantine/strategy.json if present.
_V8_QUARANTINE_SET: Dict[str, Any] = {"set": None, "loaded": False}

def _v8_get_quarantine_set():
    if _V8_QUARANTINE_SET["loaded"]:
        return _V8_QUARANTINE_SET["set"] or []
    try:
        from vec_paths.quarantine_strategy_validation import load_quarantine_list
        _V8_QUARANTINE_SET["set"] = load_quarantine_list(getattr(config, "BASE_PATH", None))
    except Exception:
        _V8_QUARANTINE_SET["set"] = []
    _V8_QUARANTINE_SET["loaded"] = True
    return _V8_QUARANTINE_SET["set"] or []

def _v8_vec_short_circuit(
    *, action, position_key, symbol, account_key,
    qty, px, side, position_side, reason,
    is_reduce, is_hedge, is_full_close,
    pos_obj, tradeable_keys, positions_dict,
    indicators, sim_ts, cfg,
    last_augment_ts=0.0, last_reduce_ts=0.0, last_open_ts=0.0,
):
    """Single early-return checkpoint that fires every enabled V8_USE_VEC_* gate.

    Returns (blocked: bool, reason: str). When blocked=True the caller MUST
    return `reason` immediately — equivalent to the scalar gate firing.

    Order mirrors live precedence: stale_mark → emergency_brake →
    cooldown_locks → open_intent_size → noloss → augment_eligibility →
    protect_balance_overtrade → circuit_sharpe → tradeable_state →
    quarantine → hedge_scan_gates.
    """
    if not V8_VEC_PARITY_AVAILABLE:
        return False, ""
    act = (action or "").upper()
    pos_amt = 0.0
    pos_gain = 0.0
    pos_entry = 0.0
    pos_max_gain = 0.0
    pos_initial_qty = 0.0
    pos_augmented_count = 0.0
    pos_last_aug_t = float(last_augment_ts or 0.0)
    pos_opened = None
    if pos_obj is not None:
        try:
            pos_amt = abs(float(getattr(pos_obj, 'positionAmt', 0) or 0))
            pos_gain = float(getattr(pos_obj, 'gain', 0) or 0)
            pos_entry = float(getattr(pos_obj, 'entry_price', 0) or 0)
            pos_max_gain = float(getattr(pos_obj, 'max_gain', 0) or 0)
            pos_initial_qty = float(getattr(pos_obj, 'initial_quantity', 0) or 0)
            pos_augmented_count = float(getattr(pos_obj, 'augmented_count', 0) or 0)
            pos_opened = getattr(pos_obj, 'last_augmentation_time', None) or getattr(pos_obj, 'opened_at', None)
            if pos_last_aug_t == 0.0:
                _lat = getattr(pos_obj, 'last_augmentation_time', None)
                if _lat is not None:
                    try:
                        pos_last_aug_t = float(_lat.timestamp()) if hasattr(_lat, 'timestamp') else float(_lat)
                    except Exception:
                        pos_last_aug_t = 0.0
        except Exception:
            pass
    # Compute real_gain from entry+mark (mirrors scalar gates)
    is_long = (position_side == 'LONG') if position_side else (position_key or '').endswith('_LONG')
    if pos_entry > 0 and px > 0:
        real_gain = ((px - pos_entry) / pos_entry * 100.0) if is_long else ((pos_entry - px) / pos_entry * 100.0)
    else:
        real_gain = pos_gain

    # ─── 1) STALE_MARK_PRICE — backtest is fail-open unless V8_BACKTEST_SIMULATE_STALE_MARK=1
    if V8_USE_VEC_STALE_MARK:
        try:
            _V8_VEC_STATS["stale_calls"] += 1
            _sb_blocked, _sb_reason, _ = evaluate_stale_mark_block_backtest(
                bar_close_ts=float(sim_ts or 0),
                cfg=cfg,
                action=act,
                reason=reason or "",
            )
            if _sb_blocked:
                _V8_VEC_STATS["stale_blocks"] += 1
                return True, f"BLOCKED_STALE_MARK_PRICE_{_sb_reason}"
        except Exception:
            pass

    # ─── 2) EMERGENCY_BRAKE — lazy lookup; fail-open without /decisions/ file
    if V8_USE_VEC_EMERGENCY_BRAKE:
        try:
            _V8_VEC_STATS["brake_calls"] += 1
            if not _V8_BRAKE_LOOKUP["built"]:
                _V8_BRAKE_LOOKUP["obj"] = BrakeLookup({account_key: []}, cfg)
                _V8_BRAKE_LOOKUP["built"] = True
            _bl = _V8_BRAKE_LOOKUP["obj"]
            if _bl is not None and sim_ts:
                _ts_dt = datetime.utcfromtimestamp(float(sim_ts)).replace(tzinfo=timezone.utc)
                _is_prof = is_reduce and real_gain > 0.1
                _br_thr, _br_reason = _bl.is_throttled(
                    account_key, _ts_dt, position_key or "", action=act,
                    is_profitable_close=_is_prof, config=cfg,
                )
                if _br_thr:
                    _V8_VEC_STATS["brake_blocks"] += 1
                    return True, f"BLOCKED_EMERGENCY_BRAKE_{_br_reason}"
        except Exception:
            pass

    # ─── 3) COOLDOWN_LOCKS — HARD_REDUCE_LOCK / HARD_AUGMENT_LOCK / AUGMENTATION_COOLDOWN
    if V8_USE_VEC_COOLDOWN_LOCKS:
        try:
            _V8_VEC_STATS["cooldown_calls"] += 1
            _cl_blocked, _cl_reason, _cl_gate = evaluate_cooldown_locks_core(
                action=act,
                now_ts=float(sim_ts or 0),
                last_reduce_ts=float(last_reduce_ts or 0),
                last_augment_ts=float(pos_last_aug_t or 0),
                reason=reason or "",
                position_amt=pos_amt,
                is_hedge=bool(is_hedge),
                current_gain_pct=real_gain,
                cfg=cfg,
            )
            if _cl_blocked:
                _V8_VEC_STATS["cooldown_blocks"] += 1
                return True, _cl_reason
        except Exception:
            pass

    # ─── 4) OPEN_INTENT_SIZE_GATES — ABSOLUTE_OPEN_LOCK / PREFLIGHT_INTENT / HARD_SIZE
    if V8_USE_VEC_OPEN_INTENT_SIZE and not is_reduce:
        try:
            _V8_VEC_STATS["open_intent_calls"] += 1
            _oi_blocked, _oi_reason, _oi_gate = evaluate_open_intent_size_gates_core(
                action=act,
                position_key=position_key or "",
                now_ts=float(sim_ts or 0),
                last_open_attempt_ts=float(last_open_ts or 0),
                intent_locks_map=None,
                proposed_qty=float(qty or 0),
                gain=real_gain,
                config=cfg,
                position_amt=pos_amt,
                mark_price=float(px or 0),
                is_hedge=bool(is_hedge),
                reason=reason or "",
            )
            if _oi_blocked:
                _V8_VEC_STATS["open_intent_blocks"] += 1
                return True, _oi_reason
        except Exception:
            pass

    # ─── 5) NOLOSS_GATE — exit-side noloss + OBLIGATORY_HEDGE
    if V8_USE_VEC_NOLOSS_GATE and is_reduce:
        try:
            _V8_VEC_STATS["noloss_calls"] += 1
            _nl_action, _nl_reason, _nl_hedge_fire, _nl_hedge_qty = evaluate_noloss_gate_core(
                indicators=indicators or {},
                is_long=is_long,
                real_gain_pct=real_gain,
                positionAmt=pos_amt,
                mark_price=float(px or 0),
                reason=reason or "",
                config=cfg,
                account_key=account_key or "",
                is_hedge=bool(is_hedge),
                is_reduce=True,
            )
            # If noloss says HOLD or CLOSE_HEDGE_FAILED → block this reduce
            if _nl_action == NOLOSS_ACTION_HOLD:
                _V8_VEC_STATS["noloss_blocks"] += 1
                return True, "BLOCKED_BY_UNIVERSAL_NOLOSS_GATE_VEC"
            # ALLOW_REDUCE / CLOSE_HEDGE_FAILED → fall through (scalar will or already did record close)
        except Exception:
            pass

    # ─── 6) AUGMENT_ELIGIBILITY — augment/reentry/open gate cascade
    if V8_USE_VEC_AUGMENT_GATE and not is_reduce:
        try:
            _V8_VEC_STATS["augment_calls"] += 1
            _ae_allowed, _ae_reason, _ae_qty, _ae_sub = evaluate_augment_eligibility_core(
                action=act,
                position_amt=pos_amt,
                real_gain=real_gain,
                entry_price=pos_entry,
                mark_price=float(px or 0),
                proposed_qty=float(qty or 0),
                config=cfg,
                last_augmentation_time=pos_last_aug_t if pos_last_aug_t > 0 else None,
                augmented_count=pos_augmented_count,
                initial_quantity=pos_initial_qty,
                max_gain=pos_max_gain,
                now_ts=float(sim_ts or 0),
                is_hedge=bool(is_hedge),
                is_long=is_long,
                reason=reason or "",
            )
            if not _ae_allowed:
                _V8_VEC_STATS["augment_blocks"] += 1
                return True, _ae_reason
        except Exception:
            pass

    # ─── 7) PROTECT_BALANCE_OVERTRADE — BALANCE_FLOOR_HALT / OVERTRADE / HEDGE_PROTECT_OPPOSITE
    if V8_USE_VEC_PROTECT_BALANCE:
        try:
            _V8_VEC_STATS["protect_balance_calls"] += 1
            _pb_blocked, _pb_reason, _pb_gate = evaluate_protect_balance_overtrade_core(
                action=act,
                position_key=position_key or "",
                account_key=account_key or "",
                positions_dict=positions_dict or {},
                balance_sentinel_path=None,
                decisions_events=None,
                now_ts=int(sim_ts or 0),
                config=cfg,
                reason=reason or "",
                is_full_close=bool(is_full_close),
                is_hedge=bool(is_hedge),
            )
            if _pb_blocked:
                _V8_VEC_STATS["protect_balance_blocks"] += 1
                return True, _pb_reason
        except Exception:
            pass

    # ─── 8) CIRCUIT_SHARPE_GATES — CIRCUIT / SHARPE_HOUR / REGIME / VOLUME
    if V8_USE_VEC_CIRCUIT_SHARPE and not is_reduce:
        try:
            _V8_VEC_STATS["circuit_sharpe_calls"] += 1
            _cs_blocked, _cs_reason, _cs_gate = evaluate_circuit_sharpe_gates_core(
                action=act,
                position_key=position_key or "",
                now_ts=float(sim_ts or 0),
                hour_of_day_sharpe=float((indicators or {}).get('hour_of_day_sharpe', float('nan'))),
                regime_score=float((indicators or {}).get('regime_score', float('nan'))),
                volume_score=float((indicators or {}).get('volume_score', float('nan'))),
                last_circuit_open_ts=0.0,
                position_amt=pos_amt,
                config=cfg,
            )
            if _cs_blocked:
                _V8_VEC_STATS["circuit_sharpe_blocks"] += 1
                return True, f"BLOCKED_{_cs_reason}"
        except Exception:
            pass

    # ─── 9) TRADEABLE_STATE_GATES — NON_TRADEABLE / FLAGGED / POSITION_EXISTS / OPEN_ON_OPEN
    # USER 2026-05-12: tradeable_keys are IRRELEVANT for backtesting (sweep needs every sym tradeable).
    # Skip this gate entirely in V8_SWEEP_MODE; tradeable_keys is only enforced for live-comparison
    # filtering, NOT for decision-making during the sim.
    if V8_USE_VEC_TRADEABLE_STATE and not is_reduce and not _SWEEP_MODE:
        try:
            _V8_VEC_STATS["tradeable_state_calls"] += 1
            _ts_allowed, _ts_reason, _ts_act, _ts_gate = evaluate_tradeable_state_gates_core(
                action=act,
                symbol=symbol or "",
                position_key=position_key or "",
                tradeable_keys_set=tradeable_keys or set(),
                positions_dict=positions_dict or {},
                quarantine_set={},
                config=cfg,
                is_hedge=bool(is_hedge),
                reason=reason or "",
                fallback_price=float(px or 0),
            )
            if not _ts_allowed:
                _V8_VEC_STATS["tradeable_state_blocks"] += 1
                return True, _ts_reason
        except Exception:
            pass

    # ─── 10) QUARANTINE — substring match against quarantined strategy names
    if V8_USE_VEC_QUARANTINE_STRATEGY:
        try:
            _V8_VEC_STATS["quarantine_calls"] += 1
            _qset = _v8_get_quarantine_set()
            if _qset:
                _qb_blocked, _qb_reason = evaluate_quarantine_core(
                    position_key=position_key or "",
                    reason=reason or "",
                    action=act,
                    is_hedge=bool(is_hedge),
                    position_amt=pos_amt,
                    quarantine_set=_qset,
                    config=cfg,
                )
                if _qb_blocked:
                    _V8_VEC_STATS["quarantine_blocks"] += 1
                    return True, _qb_reason
        except Exception:
            pass

    return False, ""

def _v8_reentry_cooldown_check(
    *, pk, position_side, mark_price, last_reduce_ts, now_ts,
    indicators, cfg, state_dict, exit_price=0.0,
):
    """2026-05-12 FIX 3 — sim-time price-cross gate for REENTRY actions."""
    try:
        # If we already unlocked this pk in a previous bar, stay unlocked.
        unblocked = state_dict.setdefault('_bt_reentry_unblock', {}).get(pk, False)
        if unblocked:
            return (False, "")
        # Minimum gap-since-close (mirrors EZ_REENTRY_PRICE_CROSS_MIN_GAP_S).
        min_gap = float(getattr(cfg, 'EZ_REENTRY_PRICE_CROSS_MIN_GAP_S', 60.0) or 60.0)
        gap = (now_ts or 0.0) - (last_reduce_ts or 0.0)
        if last_reduce_ts and gap < min_gap:
            return (True, f"BLOCKED_V8_REENTRY_GAP_{gap:.0f}s_lt_{min_gap:.0f}s")
        is_long = (str(position_side or "").upper() == "LONG") or (pk or "").endswith("_LONG")
        ind = indicators or {}
        def _f(k, d=0.0):
            try:
                v = ind.get(k)
                return float(v) if v is not None else d
            except (TypeError, ValueError):
                return d
        # Tier 1: SMA_200_15m (or ema_50_15m fallback)
        sma_now = _f('sma_200_15m') or _f('ema_50_15m')
        # Tier 2: DC break — 5m for stocks (tradier), 3m for crypto; fall back to 20-bar if 4-bar missing
        if is_long:
            dc_lvl = _f('dc_high4_5m') or _f('dc_high4_3m') or _f('dc_high_5m') or _f('dc_high_3m')
        else:
            dc_lvl = _f('dc_low4_5m') or _f('dc_low4_3m') or _f('dc_low_5m') or _f('dc_low_3m')
        # Tier 3: K_15m extreme
        k_15m = _f('stoch_k_15m', 50.0)
        # Cross conditions
        cond_sma = False
        cond_dc = False
        cond_k = False
        cond_exit = False
        bp_thr = float(getattr(cfg, "REENTRY_BYPASS_CONFIRMATION_THRESHOLD_PCT", 0.002))
        if mark_price > 0:
            if sma_now > 0:
                cond_sma = (mark_price > sma_now) if is_long else (mark_price < sma_now)
            if dc_lvl > 0:
                cond_dc = (mark_price >= dc_lvl) if is_long else (mark_price <= dc_lvl)
            if exit_price > 0:
                cond_exit = (mark_price >= exit_price * (1.0 + bp_thr)) if is_long else (mark_price <= exit_price * (1.0 - bp_thr))
        cond_k = (k_15m < 5.0) if is_long else (k_15m > 95.0)
        # No data at all → fail-open (don't fabricate a block from zeros).
        if sma_now == 0.0 and dc_lvl == 0.0 and k_15m == 50.0 and exit_price == 0.0:
            return (False, "")
        crossed = cond_sma or cond_dc or cond_k or cond_exit
        if not crossed:
            return (True, f"BLOCKED_V8_REENTRY_NO_PRICE_CROSS_sma={int(cond_sma)}_dc={int(cond_dc)}_k={int(cond_k)}_exit={int(cond_exit)}")
        # Mark unblocked — stays unlocked until next CLOSE clears it.
        state_dict.setdefault('_bt_reentry_unblock', {})[pk] = True
        return (False, "")
    except Exception:
        return (False, "")


def _v8_vec_hedge_scan_check(
    *, position_key, account_key, pos_obj, indicators, tracker_state,
    cfg, sim_ts, mode="crypto",
):
    """Vec replacement for the 7-gate hedge-scan cluster. Returns
    (fire: bool, qty: float, reason: str, blocked_by: Optional[str]).

    Caller decides what to do with the result — typically only used to
    short-circuit the scalar hedge-scan path inside ez_positions_quick.

    Currently NOT auto-invoked from the dispatch sites because the
    scalar hedge_scan runs inside ez_positions_quick, not in execute_now.
    Exposed for use by sweep harnesses + future engine hooks.
    """
    if not V8_VEC_PARITY_AVAILABLE or not V8_USE_VEC_HEDGE_SCAN_GATES:
        return (False, 0.0, "VEC_HEDGE_SCAN_DISABLED", None)
    try:
        _V8_VEC_STATS["hedge_scan_calls"] += 1
        fire, qty, reason, blocked = evaluate_hedge_scan_gates_core(
            position_key=position_key,
            account_key=account_key,
            pos=pos_obj,
            indicators=indicators or {},
            tracker_state=tracker_state or {},
            cfg=cfg,
            now_ts=float(sim_ts or 0),
            mode=mode,
        )
        if not fire:
            _V8_VEC_STATS["hedge_scan_blocks"] += 1
        return (fire, qty, reason, blocked)
    except Exception:
        return (False, 0.0, "VEC_HEDGE_SCAN_ERROR", None)

# ═══════════════════════════════════════════════════════════════
# 2026-05-12 — SHADOW VALIDATOR
# Non-invasive audit: when V8_VEC_SHADOW_VALIDATE=1 every accepted/refused trade
# is also evaluated by the parity vec modules and any disagreement is appended to
# /tmp/v8_vec_divergences.jsonl. Engine behavior is unchanged — this is observation
# only. Comparison is best-effort (vec modules require specific input shapes; we
# build them from the live state available at the eta call site).
# ═══════════════════════════════════════════════════════════════
_V8_SHADOW_DIVERGENCE_PATH = os.environ.get("V8_VEC_DIVERGENCE_LOG", "/tmp/v8_vec_divergences.jsonl")
_V8_SHADOW_STATE = {
    "checks": 0,
    "divergences": 0,
    "by_gate": {},  # gate_name -> {checks, divergences, last_example}
    "last_flush_n": 0,
}

def _v8_shadow_log_divergence(gate: str, payload: dict) -> None:
    """Append one divergence record to the audit log."""
    if not V8_VEC_SHADOW_VALIDATE:
        return
    try:
        rec = {"gate": gate, **payload}
        with open(_V8_SHADOW_DIVERGENCE_PATH, "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass

def _v8_shadow_bump(gate: str, diverged: bool, example: dict) -> None:
    st = _V8_SHADOW_STATE
    st["checks"] += 1
    g = st["by_gate"].setdefault(gate, {"checks": 0, "divergences": 0, "last_example": None})
    g["checks"] += 1
    if diverged:
        st["divergences"] += 1
        g["divergences"] += 1
        g["last_example"] = example
        _v8_shadow_log_divergence(gate, example)

def _v8_shadow_validate_eta(scalar_outcome, *, action, position_key, reason, qty, px,
                            is_hedge, is_reduce, position_amt, gain, last_open_ts,
                            last_reduce_ts, last_augment_ts, now_ts, cfg) -> None:
    """Run the available vec modules against the same inputs and audit divergences.

    scalar_outcome — None  → engine accepted (executed_trades.append fired)
                     "BLOCKED_…"|"…REFUSED…" → engine refused, return string carried.
    """
    if not (V8_VEC_SHADOW_VALIDATE and V8_VEC_PARITY_AVAILABLE):
        return
    engine_blocked = scalar_outcome is not None and isinstance(scalar_outcome, str) and (
        scalar_outcome.startswith("BLOCKED") or "REFUSED" in scalar_outcome
    )
    common_ctx = {
        "action": action,
        "position_key": position_key,
        "reason": reason,
        "qty": float(qty or 0),
        "px": float(px or 0),
        "is_hedge": bool(is_hedge),
        "is_reduce": bool(is_reduce),
        "now_ts": float(now_ts or 0),
        "engine_outcome": scalar_outcome,
        "engine_blocked": engine_blocked,
    }
    # ─── Gate 1: cooldown_locks (HARD_REDUCE_LOCK / HARD_AUGMENT_LOCK / AUGMENTATION_COOLDOWN) ──
    try:
        v_blk, v_reason, v_gate = evaluate_cooldown_locks_core(
            action=action,
            now_ts=float(now_ts or 0),
            last_reduce_ts=float(last_reduce_ts or 0),
            last_augment_ts=float(last_augment_ts or 0),
            reason=reason or '',
            position_amt=float(position_amt or 0),
            is_hedge=bool(is_hedge),
            current_gain_pct=float(gain or 0),
            cfg=cfg,
        )
        # We only flag a divergence when vec says BLOCK but engine accepted (false-allow).
        # The inverse (vec ALLOW, engine BLOCK) is expected — the engine has many other gates
        # the vec module doesn't know about — so we don't flag those.
        if v_blk and not engine_blocked:
            _v8_shadow_bump("cooldown_locks", True, {**common_ctx, "vec_reason": v_reason, "vec_gate": v_gate})
        else:
            _v8_shadow_bump("cooldown_locks", False, {})
    except Exception as _e:
        _v8_shadow_log_divergence("cooldown_locks_ERROR", {"err": str(_e), **common_ctx})
    # ─── Gate 2: open_intent_size_gates (ABSOLUTE_OPEN_LOCK / PREFLIGHT_INTENT / HARD_SIZE) ──
    try:
        v_blk2, v_reason2, v_gate2 = evaluate_open_intent_size_gates_core(
            action=action,
            position_key=position_key or '',
            now_ts=float(now_ts or 0),
            last_open_attempt_ts=float(last_open_ts or 0),
            intent_locks_map=None,
            proposed_qty=float(qty or 0),
            gain=float(gain or 0),
            config=cfg,
            position_amt=float(position_amt or 0),
            mark_price=float(px or 0),
            is_hedge=bool(is_hedge),
            reason=reason or '',
        )
        if v_blk2 and not engine_blocked:
            _v8_shadow_bump("open_intent_size_gates", True,
                            {**common_ctx, "vec_reason": v_reason2, "vec_gate": v_gate2})
        else:
            _v8_shadow_bump("open_intent_size_gates", False, {})
    except Exception as _e:
        _v8_shadow_log_divergence("open_intent_size_gates_ERROR", {"err": str(_e), **common_ctx})
    # ─── Gate 3: augment_eligibility (DUP_GUARD + LOSING_POSITION + AUGMENT_LEVEL) ──
    # 2026-05-14: expanded from 2→10 gates to find all divergence sources.
    try:
        v_blk3, v_reason3, _, _ = evaluate_augment_eligibility_core(
            action=action,
            position_amt=float(position_amt or 0),
            real_gain=float(gain or 0),
            entry_price=0.0,
            mark_price=float(px or 0),
            proposed_qty=float(qty or 0),
            config=cfg,
            last_augmentation_time=float(last_augment_ts) if last_augment_ts else None,
            augmented_count=0.0,
            now_ts=float(now_ts or 0),
            is_hedge=bool(is_hedge),
            reason=reason or '',
        )
        if v_blk3 and not engine_blocked:
            _v8_shadow_bump("augment_eligibility", True, {**common_ctx, "vec_reason": v_reason3})
        else:
            _v8_shadow_bump("augment_eligibility", False, {})
    except Exception as _e:
        _v8_shadow_log_divergence("augment_eligibility_ERROR", {"err": str(_e), **common_ctx})
    # ─── Gate 4: noloss_gate (OBLIGATORY_HEDGE + UNIVERSAL_NOLOSS) — reduce only ──
    if is_reduce:
        try:
            from vec_paths.noloss_obligatory_hedge import evaluate_noloss_gate_core, NOLOSS_ACTION_HOLD
            _nl_act, _nl_reason, _, _ = evaluate_noloss_gate_core(
                indicators={},
                is_long=position_key.endswith('_LONG') if position_key else True,
                real_gain_pct=float(gain or 0),
                positionAmt=float(position_amt or 0),
                mark_price=float(px or 0),
                reason=reason or '',
                config=cfg,
                account_key='',
                is_hedge=bool(is_hedge),
                is_reduce=True,
            )
            if _nl_act == NOLOSS_ACTION_HOLD and not engine_blocked:
                _v8_shadow_bump("noloss_gate", True, {**common_ctx, "vec_reason": _nl_reason})
            else:
                _v8_shadow_bump("noloss_gate", False, {})
        except Exception as _e:
            _v8_shadow_log_divergence("noloss_gate_ERROR", {"err": str(_e), **common_ctx})
    # ─── Gate 5: protect_balance_overtrade ──
    try:
        _pb_blk, _pb_reason, _ = evaluate_protect_balance_overtrade_core(
            action=action,
            position_key=position_key or '',
            account_key='',
            positions_dict={},
            balance_sentinel_path=None,
            decisions_events=None,
            now_ts=int(now_ts or 0),
            config=cfg,
            reason=reason or '',
            is_full_close=False,
            is_hedge=bool(is_hedge),
        )
        if _pb_blk and not engine_blocked:
            _v8_shadow_bump("protect_balance_overtrade", True, {**common_ctx, "vec_reason": _pb_reason})
        else:
            _v8_shadow_bump("protect_balance_overtrade", False, {})
    except Exception as _e:
        _v8_shadow_log_divergence("protect_balance_overtrade_ERROR", {"err": str(_e), **common_ctx})
    # ─── Gate 6: circuit_sharpe_gates (non-reduce only) ──
    if not is_reduce:
        try:
            _cs_blk, _cs_reason, _ = evaluate_circuit_sharpe_gates_core(
                action=action,
                position_key=position_key or '',
                now_ts=float(now_ts or 0),
                hour_of_day_sharpe=float('nan'),
                regime_score=float('nan'),
                volume_score=float('nan'),
                last_circuit_open_ts=0.0,
                position_amt=float(position_amt or 0),
                config=cfg,
            )
            if _cs_blk and not engine_blocked:
                _v8_shadow_bump("circuit_sharpe_gates", True, {**common_ctx, "vec_reason": _cs_reason})
            else:
                _v8_shadow_bump("circuit_sharpe_gates", False, {})
        except Exception as _e:
            _v8_shadow_log_divergence("circuit_sharpe_gates_ERROR", {"err": str(_e), **common_ctx})

def _v8_shadow_summary() -> str:
    if not V8_VEC_SHADOW_VALIDATE:
        return ""
    st = _V8_SHADOW_STATE
    parts = [f"checks={st['checks']} divergences={st['divergences']}"]
    for gate, g in sorted(st["by_gate"].items()):
        parts.append(f"{gate}={g['divergences']}/{g['checks']}")
    return " | ".join(parts)

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
from backtest_v8_harness import (
    IndicatorStore,
    apply_tradier_backtest_capital_contract,
    accumulate_partial_close,
    is_tradier_rth_ts,
    open_sizing_telemetry,
)

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

    @property
    def connections(self):
        # ez_positions_quick.py uses redis_manager.connections.get("local") to
        # fetch a client, then calls await client.get(key). Return self so those
        # paths hit our in-memory store instead of silently failing to None and
        # falling back to disk on every bar (which caused 10x slowdown + missing
        # wt_/stoch indicator data → negative Sharpe).
        return {"local": self, "gateway": self, "server": self}

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
# STEP 4b: Deterministic task scheduling (NO-LIES — 2026-05-12)
# ═══════════════════════════════════════════════════════════════
# Live code uses asyncio.create_task(...) heavily for fire-and-forget side
# effects (redis.set, persist_hedge_record, stop_manager.manage, etc.). The
# task-execution ORDER under the asyncio scheduler is not deterministic across
# Python invocations: same inputs → 41/46/41 trades on back-to-back runs. This
# kills sweep credibility — every "improvement" is masked by run-to-run noise.
#
# Strategy: in apply_patches(), monkey-patch asyncio.create_task to enqueue
# coroutines into a FIFO list rather than handing them to the scheduler. The
# simulation loop then drains the list after each bar via drain_pending_v8_tasks(),
# awaiting each coroutine in insertion order. Long-running tasks (queue
# processor, monitor loops, keepalives, _maintain_*) are detected by name and
# pass through to the real create_task — they need to remain alive across bars.
#
# Side benefits:
#   * Eliminates 'coroutine never awaited' warnings (fire-and-forget coros now
#     get drained deterministically each bar).
#   * call_later() callbacks (single live use at ez_manage.py:13305) are
#     no-op'd — wall-clock delays don't match sim-time and would otherwise
#     fire mid-bar non-deterministically.
_pending_v8_coros: List = []
# WHITELIST approach (safer): only specific fire-and-forget side-effect coroutines
# get queued for deterministic drain. Everything else stays on the real scheduler.
# This avoids deadlock from long-running tasks accidentally landing in the queue.
# Patterns are substring-matched against coroutine name (co_name / __qualname__).
_V8_FIRE_AND_FORGET_PATTERNS = (
    # Redis writes (cooldowns, locks) — ez_manage.py:13286, 13303, 22407-22408 etc.
    # These are guarded by attribute lookup `coro_name == 'set' or 'delete' or 'get'`
    # plus context: only on InMemoryRedis instances. We use the function names.
    'InMemoryRedis.set', 'InMemoryRedis.delete', 'InMemoryRedis.get',
    'InMemoryRedis.hset', 'InMemoryRedis.hdel', 'InMemoryRedis.setex',
    # Hedge persistence — ez_manage.py:15354, 15573
    'persist_hedge_record',
    # Stop level manager — ez_manage.py:13312, 10762, 10844
    'StopLevelManager.manage', '_flush_registry',
    # Hedge execution — ez_manage.py:13847, 12791, 6186, 13880, 13907, etc.
    'execute_dual_hedge', 'execute_same_symbol_hedge', '_manage_hedge_for_position',
    # Position service fire-and-forget
    'fetch_positions', 'save_reduced_positions', 'save_augmented_positions',
    'save_direct_high_gain_augmented', 'save_reversed_positions',
    # Cancel order side-channel — ez_manage.py:10912, 2064
    'futures_cancel_order',
    # Verify reduction fields — ez_manage.py:13305 (call_later target, but if it ever
    # gets wrapped in create_task)
    '_verify_reduction_fields_set',
)
_v8_orig_create_task = None  # captured in apply_patches


def _v8_det_create_task(coro, *, name=None, context=None):
    """Backtest deterministic create_task replacement.
    Known fire-and-forget side-effect coros queue into _pending_v8_coros for
    FIFO drain. EVERYTHING ELSE falls through to the real scheduler so
    long-running tasks (process_orders, monitor loops, keepalives) keep running."""
    try:
        coro_name = ''
        cr_code = getattr(coro, 'cr_code', None)
        if cr_code is not None:
            coro_name = getattr(cr_code, 'co_name', '') or ''
        # Walk the qualname which includes class for bound methods
        qual = getattr(coro, '__qualname__', '') or ''
        full_name = f"{qual}.{coro_name}" if qual and coro_name and not qual.endswith(coro_name) else (qual or coro_name)
    except Exception:
        full_name = ''
    if any(p in full_name for p in _V8_FIRE_AND_FORGET_PATTERNS):
        _pending_v8_coros.append(coro)
        return _V8DummyTask(coro)
    # Default: real scheduler — preserves long-running loops + unknown spawns.
    if context is not None:
        return _v8_orig_create_task(coro, name=name, context=context)
    return _v8_orig_create_task(coro, name=name)


class _V8DummyTask:
    """Stand-in for asyncio.Task returned by _v8_det_create_task.
    Callers that store/await the returned task get a Future-like object that
    yields once it has been drained. We mark it done immediately because the
    coro is guaranteed to be drained before the NEXT await in the sim loop
    (drain happens at the end of each bar)."""
    __slots__ = ('_coro', '_done', '_result', '_exception')

    def __init__(self, coro):
        self._coro = coro
        self._done = False
        self._result = None
        self._exception = None

    def done(self):
        return self._done

    def cancel(self, *a, **k):
        return False

    def cancelled(self):
        return False

    def result(self):
        return self._result

    def exception(self):
        return self._exception

    def add_done_callback(self, *a, **k):
        pass

    def remove_done_callback(self, *a, **k):
        return 0

    def get_loop(self):
        try:
            return asyncio.get_event_loop()
        except Exception:
            return None

    def __await__(self):
        # If the task hasn't been drained yet, drain inline now.
        if not self._done:
            if self._coro in _pending_v8_coros:
                try:
                    _pending_v8_coros.remove(self._coro)
                except ValueError:
                    pass
                try:
                    yield from self._coro.__await__()
                    self._done = True
                except Exception as _e:
                    self._exception = _e
                    self._done = True
        return self._result


async def drain_pending_v8_tasks():
    """Drain ALL queued fire-and-forget coroutines in FIFO insertion order.
    Recursive: coroutines that schedule more coroutines get those drained too.
    Safe to call from anywhere in the sim loop — replaces ad-hoc
    `await asyncio.sleep(0)` drains which only yield ONE loop tick."""
    # Bounded loop to prevent runaway recursion if some tail-coro keeps spawning
    _max_passes = 64
    _pass = 0
    while _pending_v8_coros and _pass < _max_passes:
        _pass += 1
        # Snapshot to preserve FIFO across nested spawns
        _batch = list(_pending_v8_coros)
        _pending_v8_coros.clear()
        for _coro in _batch:
            try:
                await _coro
            except asyncio.CancelledError:
                pass  # Swallow — backtest tasks are fire-and-forget; CancelledError is BaseException in 3.11+
            except Exception as _e:
                # Swallow — fire-and-forget side effects (redis writes,
                # persist_hedge_record, etc.) must not crash the sim loop.
                if _pass <= 2:
                    v8_logger.debug(f"[V8_DET_TASK] drain ex: {type(_e).__name__}: {_e}")
    if _pending_v8_coros:
        v8_logger.warning(f"[V8_DET_TASK] drain capped at {_max_passes} passes; {len(_pending_v8_coros)} coros remain")
        _pending_v8_coros.clear()


# ═══════════════════════════════════════════════════════════════
# STEP 5: Apply I/O patches BEFORE running main init
# ═══════════════════════════════════════════════════════════════
def apply_patches(stores: Dict[str, IndicatorStore], mode: str):
    """Apply ONLY I/O patches. No trade logic changes."""
    global _executed_trades, _v8_orig_create_task
    _executed_trades = []
    # Reset pending-coro list — apply_patches is called once per run.
    _pending_v8_coros.clear()

    # ─── DETERMINISTIC ASYNCIO PATCHES (NO-LIES 2026-05-12) ─────────────
    # Replace asyncio.create_task with FIFO-queueing wrapper so fire-and-forget
    # coroutines (redis writes, persist_hedge_record, stop_manager.manage, etc.)
    # execute in deterministic insertion order during drain_pending_v8_tasks().
    # Long-running loops (process_orders, _monitor_*, _maintain_*) bypass the
    # queue and remain on the real scheduler — see _V8_LONG_RUNNING_TASK_PATTERNS.
    if _v8_orig_create_task is None:
        _v8_orig_create_task = asyncio.create_task
    asyncio.create_task = _v8_det_create_task

    # ─── NO-OP loop.call_later (NO-LIES 2026-05-12) ─────────────────────
    # ez_manage.py:13305 schedules a wall-clock-delayed callback (4s).
    # Wall-clock delays don't match sim-time; would fire mid-bar non-deterministically.
    # Patch is best-effort on the currently-running loop. uvloop / immutable
    # event loops are tolerated — the single live call site is a file verifier
    # that backtest doesn't care about.
    try:
        _v8_running_loop = asyncio.get_event_loop()
        def _noop_call_later(*a, **kw):
            class _NoopHandle:
                def cancel(self): return False
                def cancelled(self): return False
                def when(self): return 0.0
            return _NoopHandle()
        try:
            _v8_running_loop.call_later = _noop_call_later
        except (AttributeError, TypeError):
            pass
    except Exception:
        pass


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

    # --- 2026-05-12 USER FIX: short-circuit execute_dual_hedge when HEDGE_DUAL_IF_HEDGE_MODE=False ---
    # Without this, the engine hits a HEDGE_ELECTED_DISABLED log spam every bar for every losing
    # position with an opposite-side counterpart. Engine "stuck" at 0% CPU due to log I/O.
    # In sweep/decision-only mode we just no-op the call entirely (no log, no work).
    # 2026-05-21 FIX: original patch targeted `LossManager` which DOES NOT EXIST in
    # ez_positions_quick.py. The actual class is `HedgeEngine` (line ~4887). Wrong-class
    # patch silently skipped → live execute_dual_hedge ran every bar → infinite-loop hang
    # on BREAKEVEN_GAIN_EROSION_STOP → HEDGE_ELECTED_DISABLED → next bar same trigger.
    # Return shape matches live's `{'overall_status': 'cross_symbol_disabled', ...}` so
    # downstream callers checking `.get('overall_status') in ['success','partial']` see
    # False and fall through to the proper close path.
    if hasattr(ez_positions_quick, 'HedgeEngine') and getattr(config, 'HEDGE_DUAL_IF_HEDGE_MODE', False) is False:
        _orig_execute_dual = getattr(ez_positions_quick.HedgeEngine, 'execute_dual_hedge', None)
        if _orig_execute_dual is not None:
            async def _noop_execute_dual_hedge(self, *args, **kwargs):
                return {
                    'overall_status': 'cross_symbol_disabled',
                    'elected_symbol': {'status': 'disabled'},
                    'actual_symbol': {'status': 'disabled'},
                    'status': 'skipped',
                    'reason': 'HEDGE_DUAL_IF_HEDGE_MODE=False (backtest-engine short-circuit)',
                }
            ez_positions_quick.HedgeEngine.execute_dual_hedge = _noop_execute_dual_hedge

    # --- 2026-05-29 USER: HARD hedge kill for sweeps — no-op the SAME-SYMBOL hedge executor too.
    # The runaway HEDGE_OPEN/HEDGE_CLOSE/REOPEN cycle (AVAXUSDC halt) is same-symbol hedging.
    # Mirrors the dual-hedge short-circuit above so NO hedge knob (HEDGE_MODE/OBLIGATORY/SCAN) can
    # emit a hedge in a backtest. Default ON; set V8_SWEEP_DISABLE_HEDGING=0 only for a dedicated
    # hedge-validation run. Return shape matches dual no-op → callers fall through to close path.
    if os.environ.get("V8_SWEEP_DISABLE_HEDGING", "1") == "1" and hasattr(ez_positions_quick, 'HedgeEngine'):
        _orig_same_hedge = getattr(ez_positions_quick.HedgeEngine, 'execute_same_symbol_hedge', None)
        if _orig_same_hedge is not None:
            async def _noop_execute_same_symbol_hedge(self, *args, **kwargs):
                return {
                    'overall_status': 'hedging_disabled_sweep',
                    'status': 'skipped',
                    'reason': 'V8_SWEEP_DISABLE_HEDGING=1 (backtest hedge hard-kill)',
                }
            ez_positions_quick.HedgeEngine.execute_same_symbol_hedge = _noop_execute_same_symbol_hedge

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
        _start_idx = 0
        if start_ts:
            try:
                _pre = np.load(str(npz_path), mmap_mode='r', allow_pickle=True)
                _pre_ts = _pre["timestamps"]
                if _pre_ts[-1] < start_ts:
                    _pre.close()
                    n_skipped_stale += 1
                    continue
                _start_idx = int(np.searchsorted(_pre_ts, start_ts))
                _pre.close()
            except Exception as _pre_e:
                v8_logger.warning(f"[NPZ_PRE_FAIL] {sym}: {_pre_e}")
        try:
            store = IndicatorStore(str(npz_path), start_idx=_start_idx)
            # Preserve the exact file actually opened so research provenance
            # checks cannot be redirected by a path declared inside a spec.
            store.source_npz_path = str(npz_path.resolve())
        except Exception as _e:
            v8_logger.warning(f"[NPZ_LOAD_FAIL] {sym}: {type(_e).__name__}: {_e}")
            continue
        stores[sym] = store
    v8_logger.info(f"Loaded {len(stores)} symbols (skipped: mode={n_skipped_mode}, explicit={n_skipped_explicit}, stale={n_skipped_stale})")
    print(f"V8_INIT_HEARTBEAT: stores_loaded={len(stores)} skipped_mode={n_skipped_mode} skipped_stale={n_skipped_stale}", flush=True)
    return stores, resolution


# ═══════════════════════════════════════════════════════════════
# NET PnL HELPER — 2026-05-18 user mandate: every pnl_pct emitted by this
# engine to JSONL or to _live_pnl["all_pnl_pcts"] MUST be NET of round-trip
# cost (bid-ask spread + slippage + crypto commission). The raw price-only
# value is preserved as pnl_pct_gross for audit. Without this, the chart and
# all downstream sweeps showed gross PnL that overstates by ~10 bps/trade —
# many low-gain "winners" turn into net losers when traded live.
#
# Per-symbol asset detection: USDC/USDT-suffix = crypto, else stocks.
# Defaults: crypto 0.08% (Binance perp realistic maker/taker mix),
#           stocks 0.05% (Tradier 0-commission, bid-ask + impact only).
# Override via config.ROUND_TRIP_COST_PCT / config_tradier.ROUND_TRIP_COST_PCT.
# ═══════════════════════════════════════════════════════════════
def _round_trip_cost_for_sym(sym):
    s = (sym or "").upper()
    if s.endswith("USDC") or s.endswith("USDT"):
        try:
            return float(getattr(config, "ROUND_TRIP_COST_PCT", 0.08))
        except Exception:
            return 0.08
    try:
        import config_tradier as _ct
        # read the CLASS attr first: config_tradier declares knobs on TradierConfig, not at
        # module level, so a bare module getattr silently fell through to the hardcoded 0.05
        # and no config or sweep override could ever change the stocks churn cost.
        _v = getattr(getattr(_ct, "TradierConfig", None), "ROUND_TRIP_COST_PCT", None)
        if _v is None:
            _v = getattr(_ct, "ROUND_TRIP_COST_PCT", 0.06)
        return float(_v)
    except Exception:
        return 0.05


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
            _rt_cost = _round_trip_cost_for_sym(sym)
            _partial_summary = accumulate_partial_close(
                rd, close_qty, px, _rt_cost, side == "LONG"
            )
            if _partial_summary is not None:
                closed_by_sym.setdefault(sym, []).append({
                    "symbol": sym, "side": side,
                    "entry_type": "HEDGE" if rd["is_hedge"] else ("AUGMENT" if "AUGMENT" in rd["entry_reason"].upper() else "OPEN"),
                    "entry_reason": rd["entry_reason"],
                    "exit_reason": reason[:120],
                    "entry_bar": 0,
                    "entry_ts": rd["entry_ts"], "entry_price": float(rd["entry_price"]),
                    "exit_bar": 0,
                    "exit_ts": ts, "exit_price": _partial_summary["exit_price"],
                    "pnl_pct": _partial_summary["pnl_pct"],
                    "pnl_pct_gross": _partial_summary["pnl_pct_gross"],
                    "round_trip_cost_pct": _rt_cost,
                    "pnl_usd": _partial_summary["pnl_dollars"],
                    "pnl_usd_gross": _partial_summary["pnl_dollars_gross"],
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
            _research_fee_bps = t.get("research_commission_bps_one_way")
            _research_open_fee = (
                float(_research_fee_bps) / 10_000.0 * price * qty
                if _research_fee_bps is not None
                else 0.0
            )
            if pk in _open:
                _open[pk]["total_cost"] += price * qty
                _open[pk]["total_qty"] += qty
                _open[pk]["research_open_fee"] = (
                    float(_open[pk].get("research_open_fee", 0.0))
                    + _research_open_fee
                )
            else:
                _open[pk] = {
                    "total_cost": price * qty,
                    "total_qty": qty,
                    "side": pos_side,
                    "research_open_fee": _research_open_fee,
                }
        elif is_close and pk in _open:
            pos = _open[pk]
            if pos["total_qty"] <= 0:
                continue
            vwap = pos["total_cost"] / pos["total_qty"]
            close_qty = min(qty, pos["total_qty"])
            is_long = pos["side"] == "LONG"
            pnl_dollars_gross = (price - vwap) * close_qty if is_long else (vwap - price) * close_qty
            pnl_pct_gross = ((price - vwap) / vwap * 100) if is_long else ((vwap - price) / vwap * 100) if vwap > 0 else 0.0
            _sym = t.get("symbol") or str(pk).split(":", 1)[-1].rsplit("_", 1)[0]
            _research_fee_bps = t.get("research_commission_bps_one_way")
            if _research_fee_bps is not None:
                _open_fee_total = float(pos.get("research_open_fee", 0.0))
                _open_fee_alloc = _open_fee_total * close_qty / pos["total_qty"]
                _close_fee = (
                    float(_research_fee_bps) / 10_000.0 * price * close_qty
                )
                _cost_dollars = _open_fee_alloc + _close_fee
                _rt_cost = 100.0 * _cost_dollars / max(
                    1e-12, vwap * close_qty
                )
                t["commission_dollars_open"] = _open_fee_alloc
                t["commission_dollars_close"] = _close_fee
                pos["research_open_fee"] = max(
                    0.0, _open_fee_total - _open_fee_alloc
                )
            else:
                _rt_cost = _round_trip_cost_for_sym(_sym)
                _cost_dollars = (_rt_cost / 100.0) * vwap * close_qty
            pnl_dollars = pnl_dollars_gross - _cost_dollars
            pnl_pct = pnl_pct_gross - _rt_cost
            t["pnl"] = pnl_dollars
            t["pnl_dollars"] = pnl_dollars
            t["pnl_dollars_gross"] = pnl_dollars_gross
            t["pnl_pct"] = pnl_pct
            t["pnl_pct_gross"] = pnl_pct_gross
            t["round_trip_cost_pct"] = _rt_cost
            t["entry_price"] = vwap
            t["close_qty"] = close_qty
            t["position_value"] = vwap * close_qty
            pos["total_qty"] -= close_qty
            pos["total_cost"] -= vwap * close_qty
            if pos["total_qty"] <= 0.001:
                del _open[pk]


def _v8_result_from_trades(executed_trades, capital, extra_fields=None):
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
    result_line = (
        f"V8_RESULT: pool_sharpe={pool_sharpe:.4f} sym_sharpe={sym_sharpe:.4f} "
        f"sharpe={pool_sharpe:.4f} pnl={pnl_pct:.2f} trades={n_trades} "
        f"wins={wins} losses={losses} total_pnl_dollars={total_pnl:.2f} "
        f"avg_pnl={avg_pnl:.2f} avg_pos_value={avg_pos_val:.2f}"
    )
    for key, value in (extra_fields or {}).items():
        result_line += f" {key}={value}"
    print(result_line, flush=True)
    result_file = os.environ.get("V8_RESULT_FILE", "")
    if result_file:
        try:
            Path(result_file).write_text(result_line + "\n")
        except Exception as exc:
            v8_logger.warning(f"[V8_RESULT_FILE] Failed to write {result_file}: {exc}")
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
    if dd_state['dd_pct'] < dd_state.get('min_dd', 0.0):
        dd_state['min_dd'] = dd_state['dd_pct']  # track worst (most negative) ever


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
    if bool(_v8ns_get(cfg_obj, 'STDEV_MACRO_ENTRY_VETO_ENABLED', False)):
        try:
            import stdev_macro as _sm_mod
            _sm_state = _sm_mod.compute_stdev_macro_state(indicators, config_obj=cfg_obj)
            _sm_side = "LONG" if is_long else "SHORT"
            _sm_block, _sm_reason = _sm_mod.entry_veto(_sm_side, _sm_state, cfg_obj)
            if _sm_block:
                return False, f"BLOCKED_{_sm_reason}_pctbD={_sm_state.get('bb_pct_b_D', 0.5):.2f}_pctb4h={_sm_state.get('bb_pct_b_4h', 0.5):.2f}"
        except Exception:
            pass
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
    # P3-#4: CircuitBreaker stub — real SmartCircuitBreaker needs live price history not in NPZ
    if not hasattr(trade_manager, 'circuit_breaker'):
        class _CBStub:
            def should_halt(self, *a, **kw): return False
            def record_loss(self, *a, **kw): pass
            enabled = False
        trade_manager.circuit_breaker = _CBStub()

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
        # 2026-05-12 — V8_DECISION_ONLY: ignore qty (set to 1.0 placeholder). User mandate:
        # live is trading min-qty only until backtest entry/exit TIMING matches /history.
        if V8_DECISION_ONLY:
            qty = 1.0
        else:
            qty = float(override_qty or qty or 0)
        px = float(px or price_cache.get(sym.upper(), 0))
        if qty <= 0 or px <= 0:
            return "BLOCKED_ZERO"
        is_red = action.upper() in ('CLOSE','REDUCE','QUICK_CLOSE','FULL_CLOSE','PROFIT_TAKE','STOP_MAJOR_LOSS_REDUCE','STOP_FUNCTIONS_KILL','HEDGE_CLOSE') or 'CLOSE' in reason.upper() or 'REDUCE' in reason.upper()
        act = action or ("CLOSE" if is_red else "OPEN")
        is_aug_action = (not is_red) and (not is_hedge)
        # ═══════════════════════════════════════════════════════════════════════════
        # 🛡️ TOP_OF_RANGE_BLOCK (parity with ez_manage.py execute_now ~22720) —
        # block OPEN/AUGMENT/ENTRY/REENTRY (NOT hedge) when price sits in the top
        # THRESHOLD% of the DC channel on ALL listed TFs (default 1h,4h,D @ 0.95).
        # LONG blocks at top, SHORT blocks at bottom. Fresh breakout (price beyond
        # the PREVIOUS-bar channel via dc_high/low_{tf}_prev) is exempted. Crypto-only
        # (this is _crypto_eta). Reads config.TOP_OF_RANGE_BLOCK_* so vec / per_sym /
        # engine stay in lockstep. Placed BEFORE the V8_DECISION_ONLY fast path so it
        # applies in both decision-only and full modes. Fail-open on any exception.
        # ═══════════════════════════════════════════════════════════════════════════
        _act_up_tor = (act or "").upper()
        if (
            bool(getattr(config, "TOP_OF_RANGE_BLOCK_ENABLED", False))
            and ("OPEN" in _act_up_tor or "AUGMENT" in _act_up_tor or "ENTRY" in _act_up_tor or "REENTRY" in _act_up_tor)
            and not is_hedge
            and "HEDGE" not in (reason or "").upper()
        ):
            try:
                _tor_threshold = float(getattr(config, "TOP_OF_RANGE_BLOCK_THRESHOLD", 0.95) or 0.95)
                _tor_tfs_raw = getattr(config, "TOP_OF_RANGE_BLOCK_TF_LIST", "1h,4h,D")
                _tor_tfs = [t.strip() for t in (_tor_tfs_raw if isinstance(_tor_tfs_raw, (list, tuple)) else str(_tor_tfs_raw).split(","))]
                _tor_tfs = [t for t in _tor_tfs if t]
                _tor_require_all = bool(getattr(config, "TOP_OF_RANGE_BLOCK_REQUIRE_ALL", True))
                _tor_is_long = (ps == "LONG") if ps else pk.endswith("_LONG")
                _tor_price = float(px or 0)
                _tor_ind = indicator_cache.get(sym.upper(), {}) if isinstance(indicator_cache, dict) else {}
                if _tor_tfs and _tor_price > 0 and _tor_ind:
                    _tor_long_extremes = []
                    _tor_short_extremes = []
                    _tor_breakout_long = False
                    _tor_breakout_short = False
                    for _tor_tf in _tor_tfs:
                        _tor_low = float(_tor_ind.get(f"dc_low_{_tor_tf}", 0) or 0)
                        _tor_high = float(_tor_ind.get(f"dc_high_{_tor_tf}", 0) or 0)
                        _tor_high_prev = float(_tor_ind.get(f"dc_high_{_tor_tf}_prev", 0) or 0)
                        _tor_low_prev = float(_tor_ind.get(f"dc_low_{_tor_tf}_prev", 0) or 0)
                        if _tor_low <= 0 or _tor_high <= 0 or _tor_high <= _tor_low:
                            continue
                        if _tor_high_prev > 0 and _tor_price > _tor_high_prev:
                            _tor_breakout_long = True
                        if _tor_low_prev > 0 and _tor_price < _tor_low_prev:
                            _tor_breakout_short = True
                        _tor_pos = max(0.0, min(1.0, (_tor_price - _tor_low) / (_tor_high - _tor_low)))
                        _tor_long_extremes.append(_tor_pos >= _tor_threshold)
                        _tor_short_extremes.append(_tor_pos <= (1.0 - _tor_threshold))
                    if _tor_long_extremes:
                        if _tor_is_long:
                            _tor_blocked = (all(_tor_long_extremes) if _tor_require_all else any(_tor_long_extremes)) and not _tor_breakout_long
                            _tor_side_str = "LONG"
                        else:
                            _tor_blocked = (all(_tor_short_extremes) if _tor_require_all else any(_tor_short_extremes)) and not _tor_breakout_short
                            _tor_side_str = "SHORT"
                        if _tor_blocked:
                            return f"BLOCKED_TOP_OF_RANGE_{_tor_side_str}_thr{_tor_threshold}"
            except Exception:
                pass
        # ═══════════════════════════════════════════════════════════════════════════
        # 2026-05-12 — V8_DECISION_ONLY FAST PATH (crypto)
        # Skip ALL _v8ns_* sizing scalars and most heavy gates. Keep:
        #   (a) Double-open reclassification: OPEN on existing position → AUGMENT
        #   (b) UNIVERSAL_NOLOSS_GATE (so loss-exits respect the same bypass list as live)
        #   (c) Position dict update + executed_trades append for downstream metrics
        #   (d) JSONL decision log compatible with data/history/<acct>/*.jsonl schema
        # Hedge obligation logging: when wt_3m against AND gain<0 the engine ALREADY
        # routes hedge calls through _crypto_eta with is_hedge=True; we log them here.
        # ═══════════════════════════════════════════════════════════════════════════
        if V8_DECISION_ONLY:
            _do_pos = trade_manager.positions.get(pk)
            _do_pos_amt = abs(float(getattr(_do_pos, 'positionAmt', 0) or 0)) if _do_pos else 0.0
            _do_is_long = (ps == 'LONG') if ps else pk.endswith('_LONG')
            # (a) Double-open reclassification
            if act.upper() in ('OPEN', 'QUICK_OPEN', 'REENTRY') and _do_pos_amt > 0.0001:
                _V8_DECISION_COUNTERS["doubleopen_reclass"] += 1
                act = "AUGMENT"
                reason = f"DECISION_ONLY_DOUBLEOPEN_RECLASS|{reason}"[:200]
            # (a2) MTF ARMED-STATE ENTRY GATE — mirror ez_manage.py:22791-22839.
            # Gates OPEN/AUGMENT/ENTRY (NOT REENTRY/hedge) on armed-state + GR filter.
            # Same STRONG_BUY/QUICK_OPEN parameter-bypass as live. Fail-open on exception.
            _mtf_act_do = (act or "").upper()
            if (not V8_DISABLE_MTF_GATE
                    and ("OPEN" in _mtf_act_do or "AUGMENT" in _mtf_act_do or "ENTRY" in _mtf_act_do)
                    and not is_hedge and "HEDGE" not in (reason or "").upper()
                    and not ((not _do_is_long) and bool(getattr(config, "MTF_ARMED_ENTRY_SKIP_SHORT", True)))
                    and bool(getattr(config, "MTF_ARMED_ENTRY_ENABLED", False))):
                try:
                    import mtf_live_evaluator as _mle_do
                    _mtf_rup_do = (reason or "").upper()
                    _mtf_bypass_do = (
                        ("STRONG_BUY" in _mtf_rup_do or "QUICK_OPEN" in _mtf_rup_do or "FORCE_HA_4H_ABOVE_BASIS" in _mtf_rup_do)
                        and "NOT A TRADEABLE KEY" not in _mtf_rup_do
                        and bool(getattr(config, "MTF_FILTER_STRONG_BUY_QUICK_BYPASS", True))
                    )
                    if not _mtf_bypass_do:
                        _mtf_states_do = trade_manager.__dict__.setdefault("_bt_mtf_states", {})
                        _mtf_side_do = "LONG" if _do_is_long else "SHORT"
                        _mtf_ind_do = indicator_cache.get(sym.upper(), {}) if isinstance(indicator_cache, dict) else {}
                        if _mtf_ind_do:
                            _mtf_key_do = f"{sym.upper()}_{_mtf_side_do}"
                            _mtf_st_do = _mle_do.ensure_state(_mtf_states_do, _mtf_key_do)
                            _mle_do.update_armed_state(_mtf_st_do, _mtf_ind_do, _mtf_side_do, config)
                            _mtf_block_do = False
                            _mtf_rsn_do = ""
                            if bool(getattr(config, "MTF_REQUIRE_ARMED_ANY", True)) and not _mle_do._armed_any_effective(_mtf_st_do, _mtf_ind_do, _mtf_side_do, config):
                                _mtf_block_do = True; _mtf_rsn_do = "MTF_NO_ARMED_STATE"
                            elif bool(getattr(config, "MTF_ENTRY_REQUIRE_GR_FILTER", True)):
                                _gr_tfs_do = int(getattr(config, "MTF_GR_MIN_TFS", 3))
                                _gr_ind_do = int(getattr(config, "MTF_GR_MIN_IND", 5))
                                if not _mle_do.gr_filter_pass(_mtf_ind_do, _mtf_side_do, "crypto", config, min_tfs=_gr_tfs_do, min_ind=_gr_ind_do):
                                    _mtf_block_do = True; _mtf_rsn_do = f"MTF_GR_FILTER_FAIL_{_gr_tfs_do}tf_{_gr_ind_do}ind"
                            if _mtf_block_do:
                                _V8_DECISION_COUNTERS["blocks"] += 1
                                return f"BLOCKED_{_mtf_rsn_do}"
                except Exception:
                    pass
            # (a3) GR_FILTER_ALL_ENTRIES — mirror ez_manage.py execute_now gate.
            # Blocks OPEN/ENTRY (not REENTRY, not OBLIGATORY, not hedge) when GR filter fails.
            # Independent of MTF_ARMED_ENTRY_ENABLED (which defaults OFF).
            _grf_act_up = (act or "").upper()
            if (bool(getattr(config, "GR_FILTER_ALL_ENTRIES", False))
                    and _do_is_long
                    and ("OPEN" in _grf_act_up or "ENTRY" in _grf_act_up)
                    and "REENTRY" not in _grf_act_up
                    and "OBLIGATORY" not in (reason or "").upper()
                    and not is_hedge and "HEDGE" not in (reason or "").upper()):
                try:
                    import mtf_live_evaluator as _mle_grf
                    _grf_ind = indicator_cache.get(sym.upper(), {}) if isinstance(indicator_cache, dict) else {}
                    if _grf_ind:
                        _grf_side = "LONG" if _do_is_long else "SHORT"
                        _grf_min_tfs = int(getattr(config, "MTF_GR_MIN_TFS", 3))
                        _grf_min_ind = int(getattr(config, "MTF_GR_MIN_IND", 7))
                        _grf_sma200 = float(_grf_ind.get("sma_200_15m", 0) or 0)
                        if _grf_sma200 > 0:
                            if _grf_side == "LONG" and float(px) > _grf_sma200 * 1.05:
                                _grf_min_ind = min(_grf_min_ind, 4)
                            elif _grf_side == "SHORT" and float(px) < _grf_sma200 * 0.95:
                                _grf_min_ind = min(_grf_min_ind, 4)
                        if not _mle_grf.gr_filter_pass(_grf_ind, _grf_side, "crypto", config, min_tfs=_grf_min_tfs, min_ind=_grf_min_ind):
                            _V8_DECISION_COUNTERS["blocks"] += 1
                            return f"BLOCKED_GR_FILTER_{_grf_side}_min{_grf_min_ind}"
                except Exception:
                    pass
            # (b) UNIVERSAL_NOLOSS_GATE — compute real gain at this px, block if at loss
            if is_red and not is_hedge:
                _do_ung_on = bool(getattr(config, 'UNIVERSAL_NOLOSS_GATE', True))
                if _do_ung_on:
                    _do_reason_up = (reason or '').upper()
                    _do_bypass = ('LIQUIDATION' in _do_reason_up or 'RIDICULOUS_LOSS' in _do_reason_up
                                  or 'UNDERWATER_HEDGE_OR_CLOSE' in _do_reason_up
                                  or 'STRUCTURAL_RANGE_SHIFT' in _do_reason_up)
                    if not _do_bypass:
                        for _brk in (getattr(config, 'UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS', []) or []):
                            if _brk and _brk.upper() in _do_reason_up:
                                _do_bypass = True; break
                    if not _do_bypass and _do_pos:
                        _do_entry = float(getattr(_do_pos, 'entry_price', 0) or 0)
                        _do_comm = float(getattr(config, 'COMMISSION_BUFFER_PCT', 0.10))
                        if _do_entry > 0:
                            _do_gain = ((px - _do_entry) / _do_entry * 100) if _do_is_long else ((_do_entry - px) / _do_entry * 100)
                            if _do_gain < _do_comm:
                                _V8_DECISION_COUNTERS["blocks"] += 1
                                return "BLOCKED_BY_UNIVERSAL_NOLOSS_GATE_DECISION_ONLY"
            # (c) Update position dict + executed_trades — mirror full-mode path
            executed_trades.append({"timestamp": _sim_ts[0], "type": "eta", "position_key": pk,
                                    "side": side, "quantity": qty, "price": px, "action": act,
                                    "reason": str(reason)[:200], "decision_only": True})
            # 2026-05-12 FIX 2 — sim-time state hooks (DECISION_ONLY crypto path).
            try:
                _bt_now_do = float(_sim_ts[0]) if _sim_ts else 0.0
                _bt_act_do = (act or "").upper()
                if _bt_act_do in ('OPEN', 'QUICK_OPEN', 'AUGMENT', 'QUICK_AUGMENT', 'REENTRY', 'HEDGE_OPEN'):
                    trade_manager.__dict__.setdefault('_bt_augment_lock', {})[pk] = _bt_now_do
                    trade_manager.__dict__.setdefault('_bt_open_attempt', {})[pk] = _bt_now_do
                    if is_hedge or 'HEDGE' in (reason or '').upper():
                        trade_manager.__dict__.setdefault('_bt_hedge_completed', {})[pk] = _bt_now_do
                elif _bt_act_do in ('CLOSE', 'REDUCE', 'QUICK_CLOSE', 'FULL_CLOSE', 'PROFIT_TAKE', 'HEDGE_CLOSE'):
                    trade_manager.__dict__.setdefault('_bt_reduce_lock', {})[pk] = _bt_now_do
                    trade_manager.__dict__.setdefault('_bt_reduce_price', {})[pk] = px
                    trade_manager.__dict__.setdefault('_bt_reentry_unblock', {}).pop(pk, None)
            except Exception:
                pass
            if is_red and _do_pos:
                _old_amt = _do_pos_amt
                _new_amt = max(0.0, _old_amt - abs(qty))
                _do_pos.positionAmt = _new_amt
                _do_pos.quantity = _new_amt
                if ifc or _new_amt < 0.0001:
                    _do_pos.positionAmt = 0; _do_pos.quantity = 0
                _do_pos.was_reduced = True
                try: _do_pos.last_reduction_time = _sim_datetime_now(timezone.utc)
                except Exception: pass
                _do_pos.last_reduction_price = px
                if is_hedge: _V8_DECISION_COUNTERS["hedges"] += 1
                elif ifc or _new_amt < 0.0001: _V8_DECISION_COUNTERS["closes"] += 1
                else: _V8_DECISION_COUNTERS["reduces"] += 1
            elif not is_red:
                if _do_pos:
                    _old_amt = _do_pos_amt
                    _old_ep = getattr(_do_pos, 'entry_price', px) or px
                    _new_amt = _old_amt + abs(qty)
                    _do_pos.entry_price = (_old_ep * _old_amt + px * abs(qty)) / _new_amt if _new_amt > 0 else px
                    _do_pos.positionAmt = _new_amt; _do_pos.quantity = _new_amt
                    # 2026-05-21 USER FIX: any 0→positive transition is a NEW position lifecycle.
                    # Without this opened_at sticks at the original-open value → age 999999m sentinel
                    # → BREAKEVEN_GAIN_EROSION_STOP fires immediately on tiny gains → orphan churn.
                    if _old_amt < 0.0001:
                        _do_pos.entry_price = px
                        try: _do_pos.opened_at = _sim_datetime_now(timezone.utc)
                        except Exception: pass
                        if _bt_act_do == 'REENTRY':
                            _do_pos.was_reentered = True; _do_pos.augment_reason = 'REENTRY'
                    _do_pos.augmented_count = getattr(_do_pos, 'augmented_count', 0) + 1
                    try: _do_pos.last_augmentation_time = _sim_datetime_now(timezone.utc)
                    except Exception: pass
                    _V8_DECISION_COUNTERS["augments" if not is_hedge else "hedges"] += 1
                else:
                    class _DP:
                        def __init__(s, sy, sd, q, ep):
                            s.symbol=sy; s.position_side=sd; s.positionAmt=q; s.quantity=q; s.entry_price=ep; s.mark_price=ep
                            s.gain=0; s.prev_gain=0; s.max_gain=0
                            s.opened_at=_sim_datetime_now(timezone.utc); s.last_updated=_sim_datetime_now(timezone.utc)
                            s.last_augmentation_time=None; s.last_reduction_time=None; s.last_reduction_price=0
                            s.was_reduced=False; s.augmented_count=0; s.max_quantity=q; s.mark_price_last_updated=None
                            s.was_reentered=False; s.augment_reason=""
                    _np = _DP(sym, ps, abs(qty), px)
                    _np.augment_reason = (reason or "")
                    if _bt_act_do == 'REENTRY':
                        _np.was_reentered = True
                    trade_manager.positions[pk] = _np
                    trade_manager.positions_by_account.setdefault(acct, {})[pk] = _np
                    _V8_DECISION_COUNTERS["opens" if not is_hedge else "hedges"] += 1
            # (d) JSONL decision log
            _ind = indicator_cache.get(sym.upper(), {}) if isinstance(indicator_cache, dict) else {}
            _v8_decision_log(account_key=acct, position_key=pk, symbol=sym,
                             side_long=_do_is_long, action=act, reason=reason,
                             price=px, sim_ts=_sim_ts[0], indicators=_ind)
            return "SUCCESS_DECISION_ONLY"
        # ── Shadow validator hook (V8_VEC_SHADOW_VALIDATE=1) — audit only, no behavior change.
        # Engine outcome here is unknown yet (we have not run the gates). We pass
        # scalar_outcome=None and let the validator flag any vec module that says
        # BLOCK on inputs the engine is about to evaluate. If the engine ALSO blocks
        # for the same reason — no harm; we log it as a non-divergence.
        if V8_VEC_SHADOW_VALIDATE and V8_VEC_PARITY_AVAILABLE:
            try:
                _shadow_pos = trade_manager.positions.get(pk)
                _shadow_pos_amt = abs(float(getattr(_shadow_pos, 'positionAmt', 0) or 0)) if _shadow_pos else 0.0
                _shadow_gain = float(getattr(_shadow_pos, 'gain', 0) or 0) if _shadow_pos else 0.0
                _shadow_aug_lock = (trade_manager.__dict__.get('_bt_augment_lock') or {}).get(pk, 0.0)
                _shadow_now = float(_sim_ts[0]) if _sim_ts else 0.0
                _v8_shadow_validate_eta(
                    scalar_outcome=None,
                    action=act, position_key=pk, reason=reason or '',
                    qty=qty, px=px, is_hedge=is_hedge, is_reduce=is_red,
                    position_amt=_shadow_pos_amt, gain=_shadow_gain,
                    last_open_ts=0.0, last_reduce_ts=0.0,
                    last_augment_ts=_shadow_aug_lock,
                    now_ts=_shadow_now, cfg=config,
                )
            except Exception:
                pass
        # ═══════════════════════════════════════════════════════════════════════════
        # 2026-05-12 — VEC SHORT-CIRCUIT (crypto eta path)
        # When any V8_USE_VEC_* flag is set, this checkpoint runs the corresponding
        # vec parity module BEFORE the scalar gates below. A BLOCKED verdict returns
        # the canonical reason string immediately. When all flags are OFF this is a
        # near-zero-cost noop (single function call + early returns inside).
        # ═══════════════════════════════════════════════════════════════════════════
        if V8_VEC_PARITY_AVAILABLE and (
            V8_USE_VEC_STALE_MARK or V8_USE_VEC_EMERGENCY_BRAKE or V8_USE_VEC_COOLDOWN_LOCKS
            or V8_USE_VEC_OPEN_INTENT_SIZE or V8_USE_VEC_NOLOSS_GATE or V8_USE_VEC_AUGMENT_GATE
            or V8_USE_VEC_PROTECT_BALANCE or V8_USE_VEC_CIRCUIT_SHARPE
            or V8_USE_VEC_TRADEABLE_STATE or V8_USE_VEC_QUARANTINE_STRATEGY
        ):
            _vec_pos_c = trade_manager.positions.get(pk)
            _vec_aug_lock_c = (trade_manager.__dict__.get('_bt_augment_lock') or {}).get(pk, 0.0)
            # 2026-05-12 FIX 2 — read sim-time state maps populated post-fill.
            _vec_reduce_lock_c = (trade_manager.__dict__.get('_bt_reduce_lock') or {}).get(pk, 0.0)
            _vec_open_attempt_c = (trade_manager.__dict__.get('_bt_open_attempt') or {}).get(pk, 0.0)
            _vec_tk_c = set()
            try:
                _ac = trade_manager.accounts.get(acct)
                if _ac is not None:
                    _vec_tk_c = set(getattr(_ac, 'tradeable_keys', set()) or set())
            except Exception:
                pass
            _vec_ind_c = indicator_cache.get(sym.upper(), {}) if isinstance(indicator_cache, dict) else {}
            _vec_blk_c, _vec_reason_c = _v8_vec_short_circuit(
                action=act, position_key=pk, symbol=sym, account_key=acct,
                qty=qty, px=px, side=side, position_side=ps, reason=reason or "",
                is_reduce=is_red, is_hedge=is_hedge, is_full_close=ifc,
                pos_obj=_vec_pos_c, tradeable_keys=_vec_tk_c,
                positions_dict=trade_manager.positions,
                indicators=_vec_ind_c, sim_ts=_sim_ts[0] if _sim_ts else 0,
                cfg=config, last_augment_ts=_vec_aug_lock_c,
                last_reduce_ts=_vec_reduce_lock_c, last_open_ts=_vec_open_attempt_c,
            )
            if _vec_blk_c:
                # Lock-bypass fix: update state locks so subsequent bars still respect
                # the 900s HARD_AUGMENT_LOCK / HARD_REDUCE cooldown. Without this, a
                # vec-blocked augment never sets _bt_augment_lock, bypassing the scalar
                # cooldown on the next bar attempt (causes AUGMENT_GATE +30, NOLOSS +16).
                _vb_now_c = float(_sim_ts[0]) if _sim_ts else 0.0
                if is_aug_action:
                    trade_manager.__dict__.setdefault('_bt_augment_lock', {})[pk] = _vb_now_c
                    trade_manager.__dict__.setdefault('_bt_open_attempt', {})[pk] = _vb_now_c
                elif is_red and not is_hedge:
                    trade_manager.__dict__.setdefault('_bt_reduce_lock', {})[pk] = _vb_now_c
                    trade_manager.__dict__.setdefault('_bt_reduce_price', {})[pk] = px
                return _vec_reason_c
        # ═══════════════════════════════════════════════════════════════════════════
        # REENTRY price-cross cooldown (mirrors ez_reentry_daemon) — always-on.
        # Was previously inside the vec block; moved out so scalar baseline also
        # enforces the live reentry gate (price must cross SMA/DC/K before reentry).
        # ═══════════════════════════════════════════════════════════════════════════
        if (act or '').upper() == 'REENTRY':
            try:
                _rx_reduce_lock = (trade_manager.__dict__.get('_bt_reduce_lock') or {}).get(pk, 0.0)
                _rx_ind = indicator_cache.get(sym.upper(), {}) if isinstance(indicator_cache, dict) else {}
                _rx_reduce_price = (trade_manager.__dict__.get('_bt_reduce_price') or {}).get(pk, 0.0)
                _rx_blk, _rx_reason = _v8_reentry_cooldown_check(
                    pk=pk, position_side=ps, mark_price=px,
                    last_reduce_ts=_rx_reduce_lock,
                    now_ts=float(_sim_ts[0]) if _sim_ts else 0.0,
                    indicators=_rx_ind, cfg=config,
                    state_dict=trade_manager.__dict__,
                    exit_price=_rx_reduce_price,
                )
                if _rx_blk:
                    return _rx_reason
            except Exception:
                pass
        # ═══════════════════════════════════════════════════════════════════════════
        # 2026-05-09 PARITY AUDIT — DUP_GUARD_GAIN — mirror ez_manage.py:10970-10988
        # Live blocks AUGMENT below 0.5*MIN_GAIN (=1.5%). v8 was emitting 870 AUGMENT
        # events on GOLDEN_RULE every 3min bar without this gate.
        # ═══════════════════════════════════════════════════════════════════════════
        if is_aug_action and bool(getattr(config, 'DUP_GUARD_USE_GAIN_GATE', True)):
            _dg_min_gain = float(getattr(config, 'MIN_GAIN', 3.0))
            _dg_mult = float(getattr(config, 'DUP_GUARD_GAIN_MULTIPLIER', 0.5))
            _dg_thr = _dg_min_gain * _dg_mult
            _dg_pos = trade_manager.positions.get(pk)
            if _dg_pos:
                _dg_gain = float(getattr(_dg_pos, 'gain', 0) or 0)
                _dg_pos_amt = abs(float(getattr(_dg_pos, 'positionAmt', 0) or 0))
                _dg_pos_val = _dg_pos_amt * px if px > 0 else 0.0
                _dg_min_pos_val = float(getattr(config, 'MIN_POSITION_SIZE', 1.0))
                if _dg_pos_val > _dg_min_pos_val and _dg_gain <= _dg_thr:
                    return f"BLOCKED_DUP_GUARD_GAIN_{_dg_gain:.2f}pct_lt_{_dg_thr:.2f}pct"
        # ═══════════════════════════════════════════════════════════════════════════
        # 2026-05-26 BATCH 3 — UNIVERSAL_AUGMENT_GAIN_GATE — mirror ez_manage.py:23956
        # Live default ON (config.py:744). Blocks AUGMENT when gain since last
        # augmentation price < MIN_GAIN_TO_BUY_AGGRESSIVELY (default 3.0%).
        # Strict superset of DUP_GUARD_GAIN: this checks price delta since the
        # *last add* (not since entry), so it blocks pyramiding-down even when
        # cumulative gain happens to be positive due to an earlier rally.
        # ═══════════════════════════════════════════════════════════════════════════
        if is_aug_action and bool(getattr(config, 'UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED', True)):
            _uag_pos = trade_manager.positions.get(pk)
            if _uag_pos is not None:
                _uag_amt = abs(float(getattr(_uag_pos, 'positionAmt', 0) or 0))
                _uag_min_qty = float(trade_manager.min_qty.get(sym, 0.0001)) if hasattr(trade_manager, 'min_qty') else 0.0001
                if _uag_amt > _uag_min_qty:
                    _uag_min_gain = float(getattr(config, 'MIN_GAIN_TO_BUY_AGGRESSIVELY', 3.0))
                    _uag_last_px = float(getattr(_uag_pos, 'last_augmentation_price', 0) or 0)
                    if _uag_last_px <= 0:
                        _uag_last_px = float(getattr(_uag_pos, 'entry_price', 0) or 0)
                    if _uag_last_px > 0 and px > 0:
                        _uag_is_long = (str(ps).upper() == 'LONG') if ps else pk.endswith('_LONG')
                        _uag_gain_since = (
                            (px - _uag_last_px) / _uag_last_px * 100.0
                            if _uag_is_long
                            else (_uag_last_px - px) / _uag_last_px * 100.0
                        )
                        if _uag_gain_since < _uag_min_gain:
                            return f"BLOCKED_UAGAIN_gain_since_last_add={_uag_gain_since:+.2f}%_lt_{_uag_min_gain:.1f}%"
        # ═══════════════════════════════════════════════════════════════════════════
        # 2026-05-29 PARITY — MTF ARMED-STATE ENTRY GATE — mirror ez_manage.py:22791-22839.
        # Gates OPEN/AUGMENT/ENTRY (NOT REENTRY/hedge) on armed-state + GR filter. Same
        # STRONG_BUY/QUICK_OPEN parameter-bypass as live. Fires when config.MTF_ARMED_ENTRY_ENABLED
        # (live default True). Fail-open on exception. Set V8_DISABLE_MTF_GATE=1 to A/B off.
        # ═══════════════════════════════════════════════════════════════════════════
        _mtf_act_full = (act or "").upper()
        if (not V8_DISABLE_MTF_GATE
                and ("OPEN" in _mtf_act_full or "AUGMENT" in _mtf_act_full or "ENTRY" in _mtf_act_full)
                and not is_hedge and "HEDGE" not in (reason or "").upper()
                and not (((ps if ps in ("LONG", "SHORT") else ("LONG" if pk.endswith("_LONG") else "SHORT")) == "SHORT") and bool(getattr(config, "MTF_ARMED_ENTRY_SKIP_SHORT", True)))
                and bool(getattr(config, "MTF_ARMED_ENTRY_ENABLED", False))):
            try:
                import mtf_live_evaluator as _mle_f
                _mtf_rup_f = (reason or "").upper()
                _mtf_bypass_f = (
                    ("STRONG_BUY" in _mtf_rup_f or "QUICK_OPEN" in _mtf_rup_f or "FORCE_HA_4H_ABOVE_BASIS" in _mtf_rup_f)
                    and "NOT A TRADEABLE KEY" not in _mtf_rup_f
                    and bool(getattr(config, "MTF_FILTER_STRONG_BUY_QUICK_BYPASS", True))
                ) or "LR_BAND" in _mtf_rup_f or "MTF_ARROW" in _mtf_rup_f  # USER 2026-07-20: band entries are band+slope gated upstream; MTF armed-state starves swing bottoms
                if not _mtf_bypass_f:
                    _mtf_states_f = trade_manager.__dict__.setdefault("_bt_mtf_states", {})
                    _mtf_side_f = ps if ps in ("LONG", "SHORT") else ("LONG" if pk.endswith("_LONG") else "SHORT")
                    _mtf_ind_f = indicator_cache.get(sym.upper(), {}) if isinstance(indicator_cache, dict) else {}
                    if _mtf_ind_f:
                        _mtf_key_f = f"{sym.upper()}_{_mtf_side_f}"
                        _mtf_st_f = _mle_f.ensure_state(_mtf_states_f, _mtf_key_f)
                        _mle_f.update_armed_state(_mtf_st_f, _mtf_ind_f, _mtf_side_f, config)
                        if bool(getattr(config, "MTF_REQUIRE_ARMED_ANY", True)) and not _mle_f._armed_any_effective(_mtf_st_f, _mtf_ind_f, _mtf_side_f, config):
                            return "BLOCKED_MTF_NO_ARMED_STATE"
                        if bool(getattr(config, "MTF_ENTRY_REQUIRE_GR_FILTER", True)):
                            _gr_tfs_f = int(getattr(config, "MTF_GR_MIN_TFS", 3))
                            _gr_ind_f = int(getattr(config, "MTF_GR_MIN_IND", 5))
                            if not _mle_f.gr_filter_pass(_mtf_ind_f, _mtf_side_f, "crypto", config, min_tfs=_gr_tfs_f, min_ind=_gr_ind_f):
                                return f"BLOCKED_MTF_GR_FILTER_FAIL_{_gr_tfs_f}tf_{_gr_ind_f}ind"
            except Exception:
                pass
        # ═══════════════════════════════════════════════════════════════════════════
        # 2026-05-09 PARITY AUDIT — HARD_AUGMENT_LOCK — mirror ez_manage.py:13946-13955
        # Live blocks AUGMENT/OPEN/REENTRY within 900s of last position-increase unless
        # gain >= MIN_GAIN. Per today's user mandate also fires on TRUE OPEN on empty.
        # ═══════════════════════════════════════════════════════════════════════════
        if is_aug_action:
            _al_min_sec = int(getattr(config, 'HARD_AUGMENT_LOCK_SECONDS', getattr(config, '_AUGMENT_LOCK_MIN_SECONDS', 900)))
            _al_pos = trade_manager.positions.get(pk)
            _al_gain = float(getattr(_al_pos, 'gain', 0) or 0) if _al_pos else 0.0
            _al_pos_amt = abs(float(getattr(_al_pos, 'positionAmt', 0) or 0)) if _al_pos else 0.0
            _al_has_existing = _al_pos_amt > 0.0001
            _al_gain_ok = (_al_gain >= float(getattr(config, 'MIN_GAIN', 3.0))) if _al_has_existing else False
            _al_lock = trade_manager.__dict__.setdefault('_bt_augment_lock', {})
            _al_now = float(_sim_ts[0]) if _sim_ts else 0.0
            _al_last = _al_lock.get(pk, 0.0)
            _al_since = _al_now - _al_last
            if _al_since < _al_min_sec and not _al_gain_ok:
                return f"BLOCKED_HARD_AUGMENT_LOCK_{_al_since:.0f}s"
            _al_lock[pk] = _al_now
        # ═══════════════════════════════════════════════════════════════════════════
        # 2026-05-09 PARITY AUDIT: UNIVERSAL_NOLOSS_GATE — mirror ez_manage.py:14140
        # Live execute_now blocks any close-at-loss unless reason bypasses the gate.
        # v8 crypto path was missing this — the audit found WT_EXHAUST_EXIT firing 318x
        # on BTCUSDC_LONG over 7d (live: 0x), causing churn-trading. Port the gate so
        # v8 crypto matches live execute_now semantics.
        # ═══════════════════════════════════════════════════════════════════════════
        if is_red and not is_hedge:
            _ung_active_c = bool(getattr(config, 'UNIVERSAL_NOLOSS_GATE', True))
            if _ung_active_c:
                _reason_up_c = (reason or '').upper()
                _ung_bypass_c = (
                    'LIQUIDATION' in _reason_up_c
                    or 'RIDICULOUS_LOSS' in _reason_up_c
                    or 'UNDERWATER_HEDGE_OR_CLOSE' in _reason_up_c
                    or 'STRUCTURAL_RANGE_SHIFT' in _reason_up_c
                )
                if not _ung_bypass_c:
                    _ung_bypass_reasons_c = getattr(config, 'UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS', []) or []
                    for _brk_c in _ung_bypass_reasons_c:
                        if _brk_c and _brk_c.upper() in _reason_up_c:
                            _ung_bypass_c = True
                            break
                if not _ung_bypass_c:
                    _pos_for_gain = trade_manager.positions.get(pk)
                    if _pos_for_gain:
                        _entry_c = float(getattr(_pos_for_gain, 'entry_price', 0) or 0)
                        _is_long_c = (ps == 'LONG') if ps else pk.endswith('_LONG')
                        _comm_buf_c = float(getattr(config, 'COMMISSION_BUFFER_PCT', 0.10))
                        if _entry_c > 0 and px > 0:
                            _real_gain_c = ((px - _entry_c) / _entry_c * 100) if _is_long_c else ((_entry_c - px) / _entry_c * 100)
                        else:
                            _real_gain_c = float(getattr(_pos_for_gain, 'gain', 0) or 0)
                        if _real_gain_c < _comm_buf_c:
                            return "BLOCKED_BY_UNIVERSAL_NOLOSS_GATE_BACKTEST_CRYPTO"
        # ── NEWBORN_PROTECT (crypto path) — mirror ez_manage.py:14210-14262 ──
        # Live execute_now blocks closes on positions < 15min old unless DC broken.
        # Age < 0 = position opened after sim start (tracker artifact) → pass through.
        # 2026-05-12 — flag-gated; was unconditionally firing before. With flag OFF
        # the gate is skipped (matches engine behaviour pre-newborn-wire).
        if V8_USE_VEC_NEWBORN_PROTECT and is_red and not is_hedge:
            try:
                _nbc_pos = trade_manager.positions.get(pk)
                if _nbc_pos:
                    _nbc_opened = getattr(_nbc_pos, 'last_augmentation_time', None) or getattr(_nbc_pos, 'opened_at', None)
                    _nbc_ind = indicator_cache.get(sym.upper(), {}) if isinstance(indicator_cache, dict) else {}
                    _nbc_dc_low_3m = float(_nbc_ind.get('dc_low_3m', 0) or 0)
                    _nbc_dc_high_3m = float(_nbc_ind.get('dc_high_3m', 0) or 0)
                    _nbc_is_long = (ps == 'LONG') if ps else pk.endswith('_LONG')
                    _nbc_now_ts = float(_sim_ts[0]) if _sim_ts[0] else _real_time_module.time()
                    _nbc_blocked, _nbc_reason, _nbc_age, _ = evaluate_newborn_protect_core(
                        action=act,
                        position_opened_at=_nbc_opened,
                        now_ts=_nbc_now_ts,
                        mark_price=px,
                        dc_low_3m=_nbc_dc_low_3m,
                        dc_high_3m=_nbc_dc_high_3m,
                        is_long=_nbc_is_long,
                        reason=reason,
                        is_hedge=is_hedge,
                        config=config,
                    )
                    if _nbc_blocked and _nbc_age >= 0:
                        v8_logger.debug(f"[V8_NEWBORN_PROTECT] {pk}: BLOCKED {act} — {_nbc_reason} age={_nbc_age:.0f}s")
                        return _nbc_reason
            except Exception as _nbc_err:
                v8_logger.debug(f"[V8_NEWBORN_PROTECT_ERR] {pk}: fail-open ({_nbc_err})")
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
        # 2026-05-12 FIX 2 — sim-time state hooks for the vec cooldown gates.
        # Every accepted fill updates the corresponding sim_ts map. Read by
        # _v8_vec_short_circuit (last_augment_ts / last_reduce_ts / last_open_ts).
        try:
            _bt_now_ts_c = float(_sim_ts[0]) if _sim_ts else 0.0
            _bt_act_up_c = (act or "").upper()
            if _bt_act_up_c in ('OPEN', 'QUICK_OPEN', 'AUGMENT', 'QUICK_AUGMENT', 'REENTRY', 'HEDGE_OPEN'):
                trade_manager.__dict__.setdefault('_bt_augment_lock', {})[pk] = _bt_now_ts_c
                trade_manager.__dict__.setdefault('_bt_open_attempt', {})[pk] = _bt_now_ts_c
                if is_hedge or 'HEDGE' in (reason or '').upper():
                    trade_manager.__dict__.setdefault('_bt_hedge_completed', {})[pk] = _bt_now_ts_c
                # P2-D: tag _reentry_breakout_level when reopening after a DC_BREAK close
                if getattr(config, 'REENTRY_BREAKOUT_ENABLED', False):
                    _rbl_key = f"_p2d_dc_break_exit_{pk}"
                    _rbl_saved = trade_manager.__dict__.get(_rbl_key)
                    if _rbl_saved is not None:
                        _live_pt = trade_manager.positions.get(pk)
                        if _live_pt is not None:
                            try:
                                _live_pt._reentry_breakout_level = float(_rbl_saved)
                            except Exception:
                                pass
                        del trade_manager.__dict__[_rbl_key]
            elif _bt_act_up_c in ('CLOSE', 'REDUCE', 'QUICK_CLOSE', 'FULL_CLOSE', 'PROFIT_TAKE', 'HEDGE_CLOSE'):
                trade_manager.__dict__.setdefault('_bt_reduce_lock', {})[pk] = _bt_now_ts_c
                trade_manager.__dict__.setdefault('_bt_reduce_price', {})[pk] = px
                # Reset reentry unblock: closing means price-cross check resets
                trade_manager.__dict__.setdefault('_bt_reentry_unblock', {}).pop(pk, None)
                # P2-D: record DC_BREAK exit price so next open can tag _reentry_breakout_level
                if getattr(config, 'REENTRY_BREAKOUT_ENABLED', False) and 'DC_BREAK' in (reason or '').upper():
                    trade_manager.__dict__[f"_p2d_dc_break_exit_{pk}"] = px
        except Exception:
            pass
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
            # Record fixed DC stop price on position at time of open/augment
            if _live_pos_obj:
                _is_long_open = pk.endswith('_LONG')
                _dc_stop_ind = indicator_cache.get(sym.upper(), {}) if isinstance(indicator_cache, dict) else {}
                _dc_stop_mode = getattr(config, 'mode', 'crypto') if hasattr(config, 'mode') else os.environ.get('V8_MODE', 'crypto')
                if str(_dc_stop_mode).lower() == 'tradier':
                    _dc_stop_val = float(_dc_stop_ind.get('dc_low4_5m' if _is_long_open else 'dc_high4_5m') or 0)
                    if _dc_stop_val == 0:
                        _dc_stop_val = float(_dc_stop_ind.get('dc_low_5m' if _is_long_open else 'dc_high_5m') or 0)
                else:
                    _dc_stop_val = float(_dc_stop_ind.get('dc_low4_3m' if _is_long_open else 'dc_high4_3m') or 0)
                    if _dc_stop_val == 0:
                        _dc_stop_val = float(_dc_stop_ind.get('dc_low_3m' if _is_long_open else 'dc_high_3m') or 0)
                if _dc_stop_val > 0:
                    try:
                        _live_pos_obj.r1_stop_price = _dc_stop_val
                    except Exception:
                        pass
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
            # 2026-05-11 ROUND-TRIP WR FIX: don't count win/loss on each REDUCE.
            # PARTIAL_PROFIT_LOCK creates many small REDUCE events at +0.5% that get
            # counted as "wins" while subsequent full-close losses are also counted but
            # outweighed in count. Result: 97-100% WR with NEGATIVE total gain — the
            # exact pattern the user warned about. Fix: accumulate per-position cum_pnl,
            # only count ONE win or ONE loss per round-trip on full close (qty -> 0).
            _pos["cum_pnl_pct"] = _pos.get("cum_pnl_pct", 0.0) + _pnl_pct
            _pos["cum_pnl_dollars"] = _pos.get("cum_pnl_dollars", 0.0) + _pnl_dollars
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
                # 2026-05-11 ROUND-TRIP WR FIX: position fully closed — count ONE win/loss
                # based on cumulative pnl across all REDUCE events on this position.
                _rt_cum_pnl_pct = _pos.get("cum_pnl_pct", _pnl_pct)
                if _rt_cum_pnl_pct > 0:
                    _live_pnl["n_wins"] += 1
                else:
                    _live_pnl["n_losses"] += 1
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
                # 2026-05-21 USER FIX: any 0→positive transition is a NEW position lifecycle.
                if old_amt < 0.0001:
                    pos.entry_price = px
                    try: pos.opened_at = _sim_datetime_now(timezone.utc)
                    except Exception: pass
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

    # 2026-05-18 FIX: Patch execute_now to route through _crypto_eta.
    # process_position (ez_manage.py) calls trade_manager.execute_now() directly (not
    # execute_trade_action) for GR_HTF_DIRECT_EXIT, R1, R2, RIDICULOUS_HOLD, ALL_TF_AGAINST,
    # OBLIGATORY_HEDGE, MICRO_SCALP, PARTIAL_PROFIT_LOCK, etc. — 30+ call sites.
    # Without this patch, each execute_now call runs the REAL 3,500-line function
    # (debounce, ii() indicator fetch, queue_trade_action, send_webhook) which causes
    # GR-enabled variants to TIMEOUT at 90min with 0 trades.
    async def _v8_execute_now_crypto(position_key=None, account_key=None, symbol=None,
                                     original_positionAmt=0.0, side='BUY', position_side='LONG',
                                     quantity=0.0, old_price=0.0, unique_id=None, reason='',
                                     is_full_close=False, action=None, is_hedge=False,
                                     hedge_for=None, url_variant='', **kw):
        return await _crypto_eta(
            account_key=account_key or '',
            position_key=position_key or '',
            symbol=symbol or '',
            quantity=quantity,
            current_price=old_price,
            side=side,
            position_side=position_side,
            unique_id=unique_id,
            is_full_close=is_full_close,
            action=action or ('CLOSE' if is_full_close else 'OPEN'),
            reason=reason,
            is_hedge=is_hedge,
            hedge_for=hedge_for,
        )
    trade_manager.execute_now = _v8_execute_now_crypto

    # 2026-05-12 — V8_DECISION_ONLY: stub calculate_final_order_quantity so queue_trade_action
    # doesn't spin up the expensive `ii()` indicator fetcher to compute sizing. We don't care
    # about qty in decision-only mode; we just want entry/exit TIMING to match /history.
    if V8_DECISION_ONLY:
        async def _v8_do_calc_qty(position_key=None, account_key=None, symbol=None, position=None,
                                  action="", trade_manager=None, conviction=0.5, base_quantity=0.0,
                                  reason="", signal_data=None):
            # Return a small non-zero qty so add_order doesn't reject. Actual qty is overwritten
            # to 1.0 inside _crypto_eta when DECISION_ONLY is on.
            return max(0.001, float(base_quantity or 0.001))
        ez_manage.calculate_final_order_quantity = _v8_do_calc_qty
        v8_logger.info("[V8_DECISION_ONLY] calculate_final_order_quantity stubbed (crypto path)")

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
    # Reset ALL loaded positions — clean slate (or seed from snapshot below)
    trade_manager.positions = {}
    trade_manager.positions_by_account = {account_key: {}}
    if trade_manager.positions_service:
        trade_manager.positions_service.positions = {}
        trade_manager.positions_service.positions_by_account = {account_key: {}}
    tracker_manager.positions = {}
    tracker_manager.positions_by_account = {account_key: {}}
    # ─── V8 SEED-POSITIONS (forward-parity loop) ─────────────────────────────
    # When --seed-positions <path> is set (or env V8_SEED_POSITIONS_FILE), load
    # the live open positions / hedges / exit_candidates / tradeable_keys from
    # the snapshot and inject them into trade_manager + positions_service +
    # tracker_manager BEFORE the empty-shell scaffolding runs. The shell loop
    # (~line 1551 below) will then merge — it only creates a shell when the pk
    # is not already present, so seeded positions survive.
    _seed_file = os.environ.get("V8_SEED_POSITIONS_FILE", "").strip()
    if _seed_file and Path(_seed_file).exists():
        try:
            with open(_seed_file) as _sf:
                _seed = json.load(_sf)
            _seed_account = _seed.get("account")
            if _seed_account and _seed_account != account_key:
                v8_logger.warning(f"[V8_SEED] snapshot account={_seed_account} != run account={account_key} — skipping seed")
            else:
                _n_pos = 0
                # 2026-05-12 FIX 1 — Sim-time anchor for position-state timestamps.
                # Live `from_dict` carries wall-clock datetimes (today) on the loaded
                # position. The vec-gate cooldown evaluators do `now_ts - last_aug_ts`
                # against sim_ts (e.g. 2026-04-15), which yields a wildly negative or
                # ~0-bounded value → every cooldown gate fails open. Anchor every
                # state-timestamp to sim_start so deltas are well-defined.
                _sim_start_ts = float(start_ts_filter) if 'start_ts_filter' in dir() else float(_sim_ts[0] or 0)
                if _sim_start_ts <= 0:
                    try:
                        _sim_start_ts = float(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
                    except Exception:
                        _sim_start_ts = 0.0
                _sim_start_dt = datetime.utcfromtimestamp(_sim_start_ts).replace(tzinfo=timezone.utc) if _sim_start_ts > 0 else None
                for _pk, _pdict in (_seed.get("positions") or {}).items():
                    try:
                        _pos = ez_manage.Position.from_dict(_pdict)
                    except Exception as _e:
                        v8_logger.warning(f"[V8_SEED] failed to reconstruct {_pk}: {_e}")
                        continue
                    try:
                        object.__setattr__(_pos, "_position_key", _pk)
                    except Exception:
                        pass
                    # Recompute gain at sim-start using first available bar price for the
                    # position's symbol. Live `gain` was current-time; backtest must align
                    # to the bar the sim opens on.
                    _sym = getattr(_pos, "symbol", "") or _pk.split(":", 1)[-1].rsplit("_", 1)[0]
                    _store = stores.get(_sym)
                    if _store is not None and getattr(_pos, "entry_price", 0):
                        try:
                            _p0 = float(_store.price(0))
                            if _p0 > 0:
                                _entry = float(_pos.entry_price)
                                if _pos.position_side == "LONG":
                                    _g = (_p0 - _entry) / _entry * 100.0
                                else:
                                    _g = (_entry - _p0) / _entry * 100.0
                                try:
                                    object.__setattr__(_pos, "gain", _g)
                                    object.__setattr__(_pos, "prev_gain", _g)
                                    object.__setattr__(_pos, "mark_price", _p0)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    # 2026-05-12 FIX 1 — clear wall-clock cooldown timestamps so vec
                    # gates compute correct sim-time deltas. Setting to None means
                    # "never augmented during this sim" — the gates fall through to
                    # default-permissive on first attempt, then start tracking from
                    # the first sim-time fill onwards.
                    for _ts_attr in ("last_augmentation_time", "last_reduction_time",
                                       "opened_at", "mark_price_last_updated", "last_updated"):
                        try:
                            object.__setattr__(_pos, _ts_attr, _sim_start_dt)
                        except Exception:
                            pass
                    trade_manager.positions[_pk] = _pos
                    trade_manager.positions_by_account.setdefault(account_key, {})[_pk] = _pos
                    if trade_manager.positions_service:
                        trade_manager.positions_service.positions[_pk] = _pos
                        trade_manager.positions_service.positions_by_account.setdefault(account_key, {})[_pk] = _pos
                    _n_pos += 1
                _hedges = _seed.get("active_hedges") or []
                tracker_manager.active_hedges = list(_hedges)
                _ec = _seed.get("exit_candidates") or {}
                tracker_manager.exit_candidates = dict(_ec)
                _tk_list = _seed.get("tradeable_keys") or []
                tracker_manager.tradeable_position_keys[account_key] = set(_tk_list)
                v8_logger.info(f"[V8_SEED] loaded {_n_pos} positions, {len(_hedges)} hedges, {len(_ec)} exit_candidates, {len(_tk_list)} tradeable_keys from {_seed_file}")
        except Exception as _e:
            v8_logger.error(f"[V8_SEED] failed to load {_seed_file}: {_e}")
    else:
        if _seed_file:
            v8_logger.warning(f"[V8_SEED] file not found: {_seed_file}")
        v8_logger.info(f"V8 BACKTEST: cleared all loaded positions for clean sim")

    # 2026-05-12 FIX 1 — clear sim-time state maps that the vec gates read.
    # These hold (position_key → sim_ts) entries and MUST start empty so the
    # first event is naturally "no prior cooldown". Seeded positions get state
    # only from sim-time fills (see Fix 2 in _crypto_eta / _v8_real_eta_wrapper).
    trade_manager.__dict__['_bt_augment_lock'] = {}     # last AUGMENT/OPEN/REENTRY ts per pk
    trade_manager.__dict__['_bt_reduce_lock'] = {}      # last REDUCE/CLOSE ts per pk
    trade_manager.__dict__['_bt_open_attempt'] = {}     # last OPEN-attempt ts per pk
    trade_manager.__dict__['_bt_hedge_completed'] = {}  # last successful hedge ts per pk
    trade_manager.__dict__['_bt_reentry_unblock'] = {}  # pk → bool (price-cross unlocked)

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
    # 2026-05-21 PER-SIDE ALLOWLIST FIX (user-mandated): respect symbols_<acct>_long/short.json
    # instead of broadcasting every NPZ sym to both sides. Live tradier_positions.py reads the
    # same files; BT engine must mirror or it simulates wrong-side phantom trades that live
    # correctly refuses (root cause of "BT-only entries" discrepancy on trb 2026-05-21).
    _long_allow_path = BASE_PATH / f"symbols_{account_key}_long.json"
    _short_allow_path = BASE_PATH / f"symbols_{account_key}_short.json"
    try:
        _long_allow = set(json.load(open(_long_allow_path))) if _long_allow_path.exists() else None
    except Exception:
        _long_allow = None
    try:
        _short_allow = set(json.load(open(_short_allow_path))) if _short_allow_path.exists() else None
    except Exception:
        _short_allow = None
    _uat = os.environ.get("V8_UNIVERSE_AT", "")
    if _uat:
        try:
            sys.path.insert(0, str(BASE_PATH / "tools"))
            import universe_registry as _ur
            _long_allow = set(_ur.get_universe(account_key, "long", _uat)) or _long_allow
            _short_allow = set(_ur.get_universe(account_key, "short", _uat)) or _short_allow
            v8_logger.info(f"V8_UNIVERSE_AT={_uat}: point-in-time allowlists long={len(_long_allow or [])} short={len(_short_allow or [])}")
        except Exception as _ue:
            v8_logger.warning(f"V8_UNIVERSE_AT lookup failed ({_ue}) — falling back to live files")
    for sym in stores.keys():
        _long_ok = (_long_allow is None) or (sym in _long_allow)
        _short_ok = (_short_allow is None) or (sym in _short_allow)
        _attrs_to_add = []
        if _long_ok:
            _attrs_to_add.append(f'symbols_long_{account_key}')
        if _short_ok:
            _attrs_to_add.append(f'symbols_short_{account_key}')
        if _long_ok or _short_ok:
            _attrs_to_add.append(f'symbols_{account_key}')
        for side_attr in _attrs_to_add:
            if hasattr(trade_manager, side_attr):
                getattr(trade_manager, side_attr).add(sym)
            else:
                setattr(trade_manager, side_attr, {sym})
    _na_long = len(_long_allow) if _long_allow is not None else len(stores)
    _na_short = len(_short_allow) if _short_allow is not None else len(stores)
    v8_logger.info(f"Tradeable keys: {len(all_position_keys)} ({len(stores)} symbols, allow long={_na_long} short={_na_short})")

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
    _end_date_str = V8_BACKTEST_END_DATE
    end_ts_filter = int(datetime.strptime(_end_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) if _end_date_str else 0
    for sym, store in stores.items():
        for t in store.timestamps:
            t_int = int(t)
            if t_int >= start_ts_filter and (not end_ts_filter or t_int <= end_ts_filter):
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
            # ENGINE_ENTRY_GATE_WIDEN_TRIGGERS (2026-05-29 entry-parity fix):
            # The _GATE_KEYS prefilter only admits flat-position bars at wt/stoch/dc crosses.
            # LIVE check_entry_candidates ALSO fires non-cross trigger families on flat bars:
            # RSI/RSI2/ConnorsRSI extremes, MFI extremes, BB-squeeze fire, bar volume spikes,
            # dc_basis crossovers, and PRICE_CROSS reentries. Those bars were SKIPPED here,
            # suppressing entries the live system takes (largest Tier-2 entry-parity gap).
            # Default True = parity; set False to reproduce the legacy narrow-gate numbers.
            # Each mask only ADMITS a bar to check_entry_candidates, which still makes the
            # final real-code entry decision per admitted bar (the gate never opens trades).
            if bool(getattr(config, 'ENGINE_ENTRY_GATE_WIDEN_TRIGGERS', True)):
                _wt_base_tf = '3m' if mode == 'crypto' else '5m'
                _wt_rsi_lo = float(getattr(config, 'GATE_RSI_LONG_MAX', 35.0))
                _wt_rsi_hi = float(getattr(config, 'GATE_RSI_SHORT_MIN', 65.0))
                _wt_mfi_lo = float(getattr(config, 'GATE_MFI_LONG_MAX', 30.0))
                _wt_mfi_hi = float(getattr(config, 'GATE_MFI_SHORT_MIN', 70.0))
                for _wt_tf in (_wt_base_tf, '15m', '1h'):
                    _wt_rsi = _g_store.arrays.get(f'rsi_{_wt_tf}', None)
                    if _wt_rsi is not None:
                        _g_mask |= (_wt_rsi <= _wt_rsi_lo) | (_wt_rsi >= _wt_rsi_hi)
                    _wt_mfi = _g_store.arrays.get(f'mfi_{_wt_tf}', None)
                    if _wt_mfi is not None:
                        _g_mask |= (_wt_mfi <= _wt_mfi_lo) | (_wt_mfi >= _wt_mfi_hi)
                    _wt_sf = _g_store.arrays.get(f'squeeze_fire_{_wt_tf}', None)
                    if _wt_sf is not None:
                        _g_mask |= (_wt_sf.astype(np.float32) != 0.0)
                    _wt_vs = _g_store.arrays.get(f'bar_vol_spike_{_wt_tf}', None)
                    if _wt_vs is not None:
                        _g_mask |= _wt_vs.astype(bool)
                    _wt_dbc = _g_store.arrays.get(f'dc_basis_crossover_{_wt_tf}', None)
                    if _wt_dbc is not None:
                        _g_mask |= _wt_dbc.astype(bool)
                    _wt_dbu = _g_store.arrays.get(f'dc_basis_crossunder_{_wt_tf}', None)
                    if _wt_dbu is not None:
                        _g_mask |= _wt_dbu.astype(bool)
                _wt_r2 = _g_store.arrays.get(f'rsi2_{_wt_base_tf}', _g_store.arrays.get('rsi_2_1h', None))
                if _wt_r2 is not None:
                    _g_mask |= (_wt_r2 <= float(getattr(config, 'GATE_RSI2_LONG_MAX', 10.0))) | (_wt_r2 >= float(getattr(config, 'GATE_RSI2_SHORT_MIN', 90.0)))
                _wt_cr = _g_store.arrays.get('connors_rsi_D', None)
                if _wt_cr is not None:
                    _g_mask |= (_wt_cr <= float(getattr(config, 'GATE_CRSI_LONG_MAX', 20.0))) | (_wt_cr >= float(getattr(config, 'GATE_CRSI_SHORT_MIN', 80.0)))
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
    _w_exit_prev = {}  # sym → bool: was wt1_W > wt2_W last bar (for cross detection)

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
        # 2026-05-16 — BT_PRESERVE_DEBOUNCE_ACROSS_BARS (default False): when True,
        # skip the per-bar wipe so cooldowns expire by simulated wall-clock. Address
        # of A6 finding #1 (live_vs_backtest_tradier.md): BT fires WT_DC_ENTRY 150x
        # / GR_HTF_DIRECT_ENTRY 618x per acct because wipe runs every 5min bar.
        # Read from both config + config_tradier (config_tradier used for tradier
        # path; this block in run_simulation_crypto but flag respected on either).
        _bt_preserve_debounce = bool(getattr(config, 'BT_PRESERVE_DEBOUNCE_ACROSS_BARS', False))
        if not _bt_preserve_debounce:
            try:
                import config_tradier as _ct_dbg
                _bt_preserve_debounce = bool(getattr(_ct_dbg, 'BT_PRESERVE_DEBOUNCE_ACROSS_BARS', False)) or bool(getattr(_ct_dbg.TradierConfig, 'BT_PRESERVE_DEBOUNCE_ACROSS_BARS', False))
            except Exception:
                pass
        if not _bt_preserve_debounce:
            if hasattr(ez_manage, '_recent_reduces'):
                ez_manage._recent_reduces.clear()
            # Clear Redis debounce keys
            if hasattr(trade_manager, 'redis_manager') and trade_manager.redis_manager:
                try:
                    _rm = trade_manager.redis_manager
                    if hasattr(_rm, 'data'):
                        # InMemoryRedis — no TTL support; clear debounce keys directly from .data
                        _rm.data = {k: v for k, v in _rm.data.items() if not k.startswith('debounce_exec:')}
                    else:
                        for _rconn in (getattr(_rm, 'connections', None) or {}).values():
                            if _rconn and hasattr(_rconn, '_data'):
                                _rconn._data = {k: v for k, v in getattr(_rconn, '_data', {}).items() if not k.startswith('debounce_exec:')}
                except Exception:
                    pass
            # Clear tracker check times so exit/entry checks run every bar
            if hasattr(tracker_manager, 'last_check_times'):
                tracker_manager.last_check_times.clear()
            # 2026-05-09 USER MANDATE: augmented_positions persists across bars until
            # position is reduced or closed (it's per-position state, not per-bar dedup).
            # Removed clear() so live + backtest share same persistence semantics.
            # if hasattr(trade_manager, 'augmented_positions'):
            #     trade_manager.augmented_positions.clear()
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
            # ═══ R2 PARITY FIX 2026-05-12: inject wt_velocity_{tf}_prev ═══
            # NPZ has wt_velocity_{tf} but not _prev. Without _prev, R2's decel check
            # (|vel| < |vel_prev| * WT_VEL_DECEL_RATIO) falls back to _vp=_v → always
            # False → R2 never fires in backtest. Fetch previous bar's velocity here.
            if idx > 0:
                _vp_pidx = idx - 1
                for _vtf in ['3m', '5m', '15m', '1h', '4h', 'D']:
                    _vk = f'wt_velocity_{_vtf}'
                    _vk_prev = f'wt_velocity_{_vtf}_prev'
                    if _vk_prev not in indicators and _vk in store.arrays:
                        indicators[_vk_prev] = float(store.get(_vk, _vp_pidx))
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

        # ═══════════════════════════════════════════════════════════════════════════
        # PORTFOLIO-AWARE SENTIMENT INJECTION (crypto path) — 2026-05-12
        # Live: 0market_sentiment_local = per-symbol WT score normalized vs global max
        #        0market_sentiment_score = global average WT score across all symbols
        # These drive SENTIMENT_FADE/BOOST via calculate_quantity_complex in execute_trade_action.
        # In backtest we approximate using wt1_15m - wt2_15m (the primary WT oscillator) as
        # the raw sentiment score for each symbol, then normalize against the max absolute value
        # across all symbols in the universe — mirroring ez_indicators.py:4232-4250.
        # ═══════════════════════════════════════════════════════════════════════════
        _pf_wt_scores = {}
        for _pf_sym, _pf_ind in indicator_cache.items():
            _pf_wt1 = float(_pf_ind.get('wt1_15m', _pf_ind.get('wt1_3m', 0)) or 0)
            _pf_wt2 = float(_pf_ind.get('wt2_15m', _pf_ind.get('wt2_3m', 0)) or 0)
            _pf_wt_scores[_pf_sym] = _pf_wt1 - _pf_wt2
        if _pf_wt_scores:
            _pf_max_abs = max(abs(v) for v in _pf_wt_scores.values()) or 50.0
            if _pf_max_abs < 50.0:
                _pf_max_abs = 50.0
            _pf_raw_vals = list(_pf_wt_scores.values())
            _pf_global_avg = sum(_pf_raw_vals) / len(_pf_raw_vals)
            _pf_global_score = (_pf_global_avg / _pf_max_abs) * 100.0
            for _pf_sym, _pf_raw in _pf_wt_scores.items():
                _pf_local = (_pf_raw / _pf_max_abs) * 100.0
                indicator_cache[_pf_sym]['0market_sentiment_local'] = _pf_local
                indicator_cache[_pf_sym]['0market_sentiment_score'] = _pf_global_score
                # Also update the mirrored data sources
                if _pf_sym in trade_manager.indicators_snapshot:
                    trade_manager.indicators_snapshot[_pf_sym]['0market_sentiment_local'] = _pf_local
                    trade_manager.indicators_snapshot[_pf_sym]['0market_sentiment_score'] = _pf_global_score
                if hasattr(data_manager, '_cold_data') and _pf_sym in data_manager._cold_data:
                    data_manager._cold_data[_pf_sym]['0market_sentiment_local'] = _pf_local
                    data_manager._cold_data[_pf_sym]['0market_sentiment_score'] = _pf_global_score
                if trade_manager.positions_service and hasattr(trade_manager.positions_service, 'indicators_snapshot') and _pf_sym in trade_manager.positions_service.indicators_snapshot:
                    trade_manager.positions_service.indicators_snapshot[_pf_sym]['0market_sentiment_local'] = _pf_local
                    trade_manager.positions_service.indicators_snapshot[_pf_sym]['0market_sentiment_score'] = _pf_global_score
        # ═══════════════════════════════════════════════════════════════════════════
        # PORTFOLIO L/S RATIO STATE — compute from open positions and inject into indicators.
        # Live: ratio_rebalance_loop reads positions_by_account, computes long_value/short_value,
        # then fires RATIO_REDUCE/RATIO_CLOSE on overweight side.
        # In backtest we compute the same ratio and attach it to each symbol's indicator dict
        # as '0ls_ratio' and '0long_pct'/'0short_pct' for any gate that reads these.
        # ═══════════════════════════════════════════════════════════════════════════
        _pf_long_val = 0.0
        _pf_short_val = 0.0
        # P2-F: count-based L/S tracking (in addition to notional) — for future gate gating
        _pool_long_count = 0
        _pool_short_count = 0
        for _pf_pk, _pf_pos in trade_manager.positions.items():
            _pf_amt = abs(float(getattr(_pf_pos, 'positionAmt', 0) or 0))
            if _pf_amt < 0.0001:
                continue
            _pf_mp = float(getattr(_pf_pos, 'mark_price', 0) or getattr(_pf_pos, 'entry_price', 0) or 0)
            _pf_notional = _pf_amt * _pf_mp
            if _pf_pk.endswith('_LONG'):
                _pf_long_val += _pf_notional
                _pool_long_count += 1
            elif _pf_pk.endswith('_SHORT'):
                _pf_short_val += _pf_notional
                _pool_short_count += 1
        _pf_total_val = _pf_long_val + _pf_short_val
        _pf_long_pct = (_pf_long_val / _pf_total_val * 100.0) if _pf_total_val > 0 else 50.0
        _pf_short_pct = (_pf_short_val / _pf_total_val * 100.0) if _pf_total_val > 0 else 50.0
        _pf_ls_ratio = _pf_long_val / max(_pf_short_val, 1.0)
        for _pf_sym in list(indicator_cache.keys()):
            indicator_cache[_pf_sym]['0long_pct'] = _pf_long_pct
            indicator_cache[_pf_sym]['0short_pct'] = _pf_short_pct
            indicator_cache[_pf_sym]['0ls_ratio'] = _pf_ls_ratio
        # L/S ratio count diagnostic log — P2-F (log only; V8_USE_LS_RATIO_GATE=1 reserved for future blocking)
        if step % 1000 == 0 and (_pool_long_count > 0 or _pool_short_count > 0):
            _ls_count_ratio = _pool_long_count / max(_pool_short_count, 1)
            _ls_ratio_max = float(getattr(config, 'RATIO_MULTIPLIER', 3.0))
            if _ls_count_ratio > _ls_ratio_max or ((_pool_short_count > 0) and _ls_count_ratio < 1.0 / _ls_ratio_max):
                v8_logger.debug(f"[LS_RATIO] step={step} L={_pool_long_count} S={_pool_short_count} ratio={_ls_count_ratio:.2f} (max={_ls_ratio_max:.1f})")
        # RATIO gate — block entries that worsen imbalance beyond RATIO_MULTIPLIER
        # Active when V8_USE_LS_RATIO_GATE=1
        if os.environ.get("V8_USE_LS_RATIO_GATE", "0") == "1":
            _ls_ratio_max = float(getattr(config, 'RATIO_MULTIPLIER', 3.0))
            if _pool_long_count > 0 and _pool_short_count > 0:
                _ls_ratio_now = _pool_long_count / _pool_short_count
                if _ls_ratio_now > _ls_ratio_max:
                    _ls_block_side = 'LONG'
                elif _ls_ratio_now < 1.0 / _ls_ratio_max:
                    _ls_block_side = 'SHORT'
                else:
                    _ls_block_side = None
                if _ls_block_side:
                    if not hasattr(trade_manager, '_ls_ratio_block'):
                        trade_manager._ls_ratio_block = {}
                    trade_manager._ls_ratio_block[account_key] = _ls_block_side
            elif _pool_long_count == 0 or _pool_short_count == 0:
                if hasattr(trade_manager, '_ls_ratio_block'):
                    trade_manager._ls_ratio_block.pop(account_key, None)

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
        # 2026-05-10 PARITY MODE: V8_PARITY_MODE=1 disables this v8-only mirror so
        # only live's process_position-driven RIDICULOUS_LOSS path fires.
        # ═══════════════════════════════════════════════════════════════════════════
        if os.environ.get("V8_PARITY_MODE") != "1" and bool(getattr(config, 'RIDICULOUS_HOLD_GUARD_ENABLED', True)):
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
        # DISC-7b: RIDICULOUS_HOLD_GUARD age-based — revised 2026-05-14 per user mandate:
        #   gain >= 0: NEVER close on age alone — technicals (WT/DC) or DISC-GR15 handle exit.
        #   gain < 0 + age > RIDICULOUS_HOLD_HOURS + GR score >=15 against: flag hedge needed.
        #   (DISC-7 loss floor still catches gain <= RIDICULOUS_LOSS_PCT via separate loop above.)
        # ═══════════════════════════════════════════════════════════════════════════
        if os.environ.get("V8_PARITY_MODE") != "1" and bool(getattr(config, 'RIDICULOUS_HOLD_GUARD_ENABLED', True)):
            _rh_hours = float(getattr(config, 'RIDICULOUS_HOLD_HOURS', 720.0))
            _rh_gr_score_thr = float(getattr(config, 'GR_HTF_DIRECT_EXIT_SCORE', 15.5))
            _rh_min_ind = int(getattr(config, 'GOLDEN_RULE_MIN_IND', 1))
            _rh_min_tfs = int(getattr(config, 'GOLDEN_RULE_HTF_MIN_TFS', 1))
            for _rh_pk, _rh_pos in list(trade_manager.positions.items()):
                if abs(getattr(_rh_pos, 'positionAmt', 0)) < 0.0001:
                    continue
                _rh_gain = float(getattr(_rh_pos, 'gain', 0) or 0)
                if _rh_gain >= 0:
                    continue  # profitable — hold until technicals or DISC-GR15, never age-close
                _rh_opened = getattr(_rh_pos, 'opened_at', None)
                if not isinstance(_rh_opened, datetime):
                    continue
                _rh_age_hours = (_sim_dt - _rh_opened).total_seconds() / 3600.0
                if _rh_age_hours < _rh_hours:
                    continue
                # Losing position past age threshold — check GR score against it
                _rh_sym = getattr(_rh_pos, 'symbol', '') or (_rh_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _rh_pk else _rh_pk[:-5] if _rh_pk.endswith('_LONG') else _rh_pk[:-6])
                _rh_ind = indicator_cache.get(_rh_sym, {})
                if not _rh_ind:
                    continue
                _rh_is_long = _rh_pk.endswith('_LONG')
                _rh_gr_score = 0
                try:
                    from golden_rule_htf import score_entry_htf as _rh_score_fn
                    _rh_pass, _rh_n_tfs, _ = _rh_score_fn(_rh_ind, not _rh_is_long, mode, min_tfs=_rh_min_tfs, min_ind=_rh_min_ind, current_price=price_cache.get(_rh_sym, 0))
                    _rh_gr_score = _rh_n_tfs * _rh_min_ind
                except Exception:
                    pass
                if _rh_gr_score < _rh_gr_score_thr:
                    continue  # GR not strongly against yet — OBLIGATORY_HEDGE and DISC-8 will handle when ready
                # GR >=15 against a losing position past hold cap → ensure hedge is queued
                _rh_already_hedged = _rh_pk in getattr(trade_manager, 'active_hedges', {})
                if not _rh_already_hedged:
                    v8_logger.info(f"[RIDICULOUS_HOLD_HEDGE_NEEDED] {_rh_pk}: age={_rh_age_hours:.1f}h gain={_rh_gain:.2f}% gr_score={_rh_gr_score:.1f} — hedge required, signalling DISC-8")
                    if not hasattr(trade_manager, '_rh_hedge_priority'):
                        trade_manager._rh_hedge_priority = set()
                    trade_manager._rh_hedge_priority.add(_rh_pk)

        # ═══════════════════════════════════════════════════════════════════════════
        # DISC-8: UNDERWATER_HEDGE_OR_CLOSE — mirror ez_manage.py:20664
        # If position pnl<0 AND wt1_15m against AND hedge active AND ≥2 of 4 HTF agree → force-close origin.
        # If position pnl<0 AND wt1_15m against AND no hedge AND gain < MANDATORY_HEDGE threshold → fire hedge.
        # 2026-05-10 PARITY MODE gated.
        # ═══════════════════════════════════════════════════════════════════════════
        if os.environ.get("V8_PARITY_MODE") != "1" and bool(getattr(config, 'UNDERWATER_HEDGE_OR_CLOSE_ENABLED', True)):
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
        # DISC-GR15: GR_HTF_DIRECT_EXIT — mirror ez_manage.py:37980 (2026-05-14)
        # Close when opposite-direction GR HTF score >= GR_HTF_DIRECT_EXIT_SCORE (default 15.5).
        # Score = n_confirmed_tfs × GOLDEN_RULE_MIN_IND. Bypasses UNIVERSAL_NOLOSS_GATE via reason.
        # Uses score_entry_htf with is_long inverted (same as live: opposite direction = exit signal).
        # 2026-05-10 PARITY MODE gated.
        # ═══════════════════════════════════════════════════════════════════════════
        if os.environ.get("V8_PARITY_MODE") != "1" and bool(getattr(config, 'GR_HTF_DIRECT_EXIT_ENABLED', False)):
            _grde_score_min = float(getattr(config, 'GR_HTF_DIRECT_EXIT_SCORE', 15.5))
            _grde_min_ind = int(getattr(config, 'GOLDEN_RULE_MIN_IND', 1))
            _grde_min_tfs = int(getattr(config, 'GOLDEN_RULE_HTF_MIN_TFS', 1))
            for _grde_pk, _grde_pos in list(trade_manager.positions.items()):
                if abs(getattr(_grde_pos, 'positionAmt', 0)) < 0.0001:
                    continue
                _grde_sym = getattr(_grde_pos, 'symbol', '') or (_grde_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _grde_pk else _grde_pk[:-5] if _grde_pk.endswith('_LONG') else _grde_pk[:-6])
                _grde_ind = indicator_cache.get(_grde_sym, {})
                if not _grde_ind:
                    continue
                _grde_px = price_cache.get(_grde_sym, 0)
                if _grde_px <= 0:
                    continue
                _grde_is_long = _grde_pk.endswith('_LONG')
                try:
                    from golden_rule_htf import score_entry_htf as _grde_score_fn
                    _grde_pass, _grde_n_tfs, _grde_detail = _grde_score_fn(
                        _grde_ind,
                        not _grde_is_long,
                        mode,
                        min_tfs=_grde_min_tfs,
                        min_ind=_grde_min_ind,
                        current_price=_grde_px,
                    )
                    _grde_score = float(_grde_n_tfs) * float(_grde_min_ind)
                    if _grde_score >= _grde_score_min:
                        _grde_gain = float(getattr(_grde_pos, 'gain', 0) or 0)
                        _grde_qty = abs(float(getattr(_grde_pos, 'positionAmt', 0)))
                        _grde_side = 'SELL' if _grde_is_long else 'BUY'
                        _grde_ps = 'LONG' if _grde_is_long else 'SHORT'
                        _grde_why = f'GR_HTF_DIRECT_EXIT_{_grde_score:.0f}_g{_grde_gain:.2f}%_{_grde_detail[:50]}'
                        v8_logger.info(f"[DISC-GR15] {_grde_pk}: opp_score={_grde_score:.0f} (tfs={_grde_n_tfs}×ind={_grde_min_ind}) >= {_grde_score_min:.0f} gain={_grde_gain:.2f}% — CLOSE")
                        try:
                            await trade_manager.execute_trade_action(account_key=account_key, position_key=_grde_pk, symbol=_grde_sym, quantity=_grde_qty, current_price=_grde_px, side=_grde_side, position_side=_grde_ps, action='CLOSE', reason=_grde_why, is_full_close=True, is_hedge=False)
                        except Exception as _grde_cl_err:
                            if step < 10 or step % 1000 == 0:
                                v8_logger.error(f"[DISC-GR15_ERR] {_grde_pk}: {_grde_cl_err}")
                except Exception as _grde_outer:
                    pass

        # ═══════════════════════════════════════════════════════════════════════════
        # DISC-HTF_AGAINST: HTF_AGAINST_FORCE_CLOSE — mirror ez_manage.py:~39021 (Fix 7 2026-06-09)
        # Close when 1h+15m+3m+D all against. Same non-zero guard as live.
        # Knobs: HTF_AGAINST_FORCE_CLOSE_ENABLED (default True), _CONFIRM_3M (True), _CONFIRM_D (True).
        # ═══════════════════════════════════════════════════════════════════════════
        if bool(getattr(config, 'HTF_AGAINST_FORCE_CLOSE_ENABLED', True)) and mode == "crypto":
            try:
                for _hac_pk, _hac_pos in list(trade_manager.positions.items()):
                    if abs(getattr(_hac_pos, 'positionAmt', 0)) < 0.0001:
                        continue
                    _hac_sym = getattr(_hac_pos, 'symbol', '') or (_hac_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _hac_pk else _hac_pk[:-5] if _hac_pk.endswith('_LONG') else _hac_pk[:-6])
                    _hac_ind = indicator_cache.get(_hac_sym, {})
                    if not _hac_ind:
                        continue
                    _hac_px = price_cache.get(_hac_sym, 0)
                    if _hac_px <= 0:
                        continue
                    _hac_is_long = _hac_pk.endswith('_LONG')
                    _hac_w1_1h = float(_hac_ind.get('wt1_1h', 0) or 0)
                    _hac_w2_1h = float(_hac_ind.get('wt2_1h', 0) or 0)
                    _hac_1h_against = (_hac_w1_1h < _hac_w2_1h) if _hac_is_long else (_hac_w1_1h > _hac_w2_1h)
                    if not _hac_1h_against:
                        continue
                    _hac_ok = True
                    _hac_w1_15m = float(_hac_ind.get('wt1_15m', 0) or 0)
                    _hac_w2_15m = float(_hac_ind.get('wt2_15m', 0) or 0)
                    if abs(_hac_w1_15m) > 1e-9 or abs(_hac_w2_15m) > 1e-9:
                        _hac_ok = (_hac_w1_15m < _hac_w2_15m) if _hac_is_long else (_hac_w1_15m > _hac_w2_15m)
                    if not _hac_ok:
                        continue
                    if bool(getattr(config, 'HTF_AGAINST_FORCE_CLOSE_CONFIRM_3M', True)):
                        _hac_w1_3m = float(_hac_ind.get('wt1_3m', 0) or 0)
                        _hac_w2_3m = float(_hac_ind.get('wt2_3m', 0) or 0)
                        if abs(_hac_w1_3m) > 1e-9 or abs(_hac_w2_3m) > 1e-9:
                            _hac_ok = (_hac_w1_3m < _hac_w2_3m) if _hac_is_long else (_hac_w1_3m > _hac_w2_3m)
                        if not _hac_ok:
                            continue
                    if bool(getattr(config, 'HTF_AGAINST_FORCE_CLOSE_CONFIRM_D', True)):
                        _hac_w1_D = float(_hac_ind.get('wt1_D', 0) or 0)
                        _hac_w2_D = float(_hac_ind.get('wt2_D', 0) or 0)
                        if abs(_hac_w1_D) > 1e-9 or abs(_hac_w2_D) > 1e-9:
                            _hac_ok = (_hac_w1_D < _hac_w2_D) if _hac_is_long else (_hac_w1_D > _hac_w2_D)
                        if not _hac_ok:
                            continue
                    _hac_gain = float(getattr(_hac_pos, 'gain', 0) or 0)
                    _hac_qty = abs(float(getattr(_hac_pos, 'positionAmt', 0)))
                    _hac_side = 'SELL' if _hac_is_long else 'BUY'
                    _hac_ps = 'LONG' if _hac_is_long else 'SHORT'
                    _hac_why = f'HTF_AGAINST_FORCE_CLOSE_1h15m3mD_g{_hac_gain:.2f}%'
                    try:
                        await trade_manager.execute_trade_action(account_key=account_key, position_key=_hac_pk, symbol=_hac_sym, quantity=_hac_qty, current_price=_hac_px, side=_hac_side, position_side=_hac_ps, action='CLOSE', reason=_hac_why, is_full_close=True, is_hedge=False)
                    except Exception:
                        pass
            except Exception:
                pass

        # ═══════════════════════════════════════════════════════════════════════════
        # DISC-STALL: STALL_SUB — close profitable stalled positions (P1-A)
        # Active when V8_USE_VEC_ALL=1 and STALL_SUB_ENABLED=True (default False).
        # ═══════════════════════════════════════════════════════════════════════════
        if os.environ.get("V8_USE_VEC_ALL", "0") == "1" and bool(getattr(config, 'STALL_SUB_ENABLED', False)):
            try:
                from vec_paths.stall_sub import check_stall_sub_exit as _stall_check
                for _stall_pk, _stall_pos in list(trade_manager.positions.items()):
                    if abs(getattr(_stall_pos, 'positionAmt', 0)) < 0.0001:
                        continue
                    _stall_result = _stall_check(None, step, _stall_pos, mode, config)
                    if _stall_result:
                        _stall_sym = getattr(_stall_pos, 'symbol', '') or (_stall_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _stall_pk else _stall_pk[:-5] if _stall_pk.endswith('_LONG') else _stall_pk[:-6])
                        _stall_px = price_cache.get(_stall_sym, 0)
                        if _stall_px <= 0:
                            continue
                        _stall_is_long = _stall_pk.endswith('_LONG')
                        await trade_manager.execute_trade_action(account_key=account_key, position_key=_stall_pk, symbol=_stall_sym, quantity=abs(float(getattr(_stall_pos, 'positionAmt', 0))), current_price=_stall_px, side='SELL' if _stall_is_long else 'BUY', position_side='LONG' if _stall_is_long else 'SHORT', action='CLOSE', reason=_stall_result['reason'], is_full_close=True, is_hedge=False)
            except Exception:
                pass

        # ═══════════════════════════════════════════════════════════════════════════
        # DISC-BE_EROSION: BREAKEVEN_GAIN_EROSION — close positions that eroded to zero (P1-B)
        # Active when V8_USE_VEC_ALL=1 and BREAKEVEN_GAIN_EROSION_ENABLED=True (default False).
        # ═══════════════════════════════════════════════════════════════════════════
        if os.environ.get("V8_USE_VEC_ALL", "0") == "1" and bool(getattr(config, 'BREAKEVEN_GAIN_EROSION_ENABLED', False)):
            try:
                from vec_paths.breakeven_gain_erosion import check_breakeven_gain_erosion as _be_check
                for _be_pk, _be_pos in list(trade_manager.positions.items()):
                    if abs(getattr(_be_pos, 'positionAmt', 0)) < 0.0001:
                        continue
                    _be_result = _be_check(None, step, _be_pos, mode, config)
                    if _be_result:
                        _be_sym = getattr(_be_pos, 'symbol', '') or (_be_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _be_pk else _be_pk[:-5] if _be_pk.endswith('_LONG') else _be_pk[:-6])
                        _be_px = price_cache.get(_be_sym, 0)
                        if _be_px <= 0:
                            continue
                        _be_is_long = _be_pk.endswith('_LONG')
                        await trade_manager.execute_trade_action(account_key=account_key, position_key=_be_pk, symbol=_be_sym, quantity=abs(float(getattr(_be_pos, 'positionAmt', 0))), current_price=_be_px, side='SELL' if _be_is_long else 'BUY', position_side='LONG' if _be_is_long else 'SHORT', action='CLOSE', reason=_be_result['reason'], is_full_close=True, is_hedge=False)
            except Exception:
                pass

        # ═══════════════════════════════════════════════════════════════════════════
        # DISC-SENTIMENT: SENTIMENT_FADE — tradier-only reduce on RSI proxy signal (P2-E)
        # Active when V8_USE_VEC_ALL=1 and SENTIMENT_FADE_PROXY_ENABLED=True (default False).
        # ═══════════════════════════════════════════════════════════════════════════
        if mode == "tradier" and os.environ.get("V8_USE_VEC_ALL", "0") == "1" and bool(getattr(config, 'SENTIMENT_FADE_PROXY_ENABLED', False)):
            try:
                from vec_paths.sentiment_fade import check_sentiment_fade as _sent_check
                # 2026-05-21 SENTIMENT_FADE_MODE — test-matrix knob ("REDUCE"|"CLOSE"|"DISABLED")
                _sent_mode = str(getattr(config, 'SENTIMENT_FADE_MODE', 'REDUCE')).upper()
                if _sent_mode != 'DISABLED':
                    for _sent_pk, _sent_pos in list(trade_manager.positions.items()):
                        if abs(getattr(_sent_pos, 'positionAmt', 0)) < 0.0001:
                            continue
                        _sent_result = _sent_check(None, step, _sent_pos, mode, config)
                        if _sent_result:
                            _sent_sym = getattr(_sent_pos, 'symbol', '') or (_sent_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _sent_pk else _sent_pk[:-5] if _sent_pk.endswith('_LONG') else _sent_pk[:-6])
                            _sent_px = price_cache.get(_sent_sym, 0)
                            if _sent_px <= 0:
                                continue
                            _sent_is_long = _sent_pk.endswith('_LONG')
                            _sent_full_qty = abs(float(getattr(_sent_pos, 'positionAmt', 0)))
                            if _sent_mode == 'CLOSE':
                                _sent_qty = _sent_full_qty
                                _sent_action = 'CLOSE'
                                _sent_full_close = True
                                _sent_reason_tag = _sent_result['reason'] + '_MODE_CLOSE'
                            else:  # REDUCE (default)
                                _sent_qty = _sent_full_qty * 0.5
                                _sent_action = 'REDUCE'
                                _sent_full_close = False
                                _sent_reason_tag = _sent_result['reason']
                            await trade_manager.execute_trade_action(account_key=account_key, position_key=_sent_pk, symbol=_sent_sym, quantity=_sent_qty, current_price=_sent_px, side='SELL' if _sent_is_long else 'BUY', position_side='LONG' if _sent_is_long else 'SHORT', action=_sent_action, reason=_sent_reason_tag, is_full_close=_sent_full_close, is_hedge=False)
            except Exception:
                pass

        # ═══════════════════════════════════════════════════════════════════════════
        # DISC-ATR_TRAIL: ATR trailing stop sweep test (P2-B)
        # Active when V8_USE_VEC_ALL=1 and ATR_TRAIL_SWEEP_ENABLED=True (default False).
        # ═══════════════════════════════════════════════════════════════════════════
        if os.environ.get("V8_USE_VEC_ALL", "0") == "1" and bool(getattr(config, 'ATR_TRAIL_SWEEP_ENABLED', False)):
            try:
                from vec_paths.atr_trail import update_atr_trail_level as _atr_update, check_atr_trail_stop as _atr_check
                for _atr_pk, _atr_pos in list(trade_manager.positions.items()):
                    if abs(getattr(_atr_pos, 'positionAmt', 0)) < 0.0001:
                        continue
                    _atr_update(_atr_pos, step, None, mode, config)
                    _atr_result = _atr_check(None, step, _atr_pos, mode, config)
                    if _atr_result:
                        _atr_sym = getattr(_atr_pos, 'symbol', '') or (_atr_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _atr_pk else _atr_pk[:-5] if _atr_pk.endswith('_LONG') else _atr_pk[:-6])
                        _atr_px = price_cache.get(_atr_sym, 0)
                        if _atr_px <= 0:
                            continue
                        _atr_is_long = _atr_pk.endswith('_LONG')
                        await trade_manager.execute_trade_action(account_key=account_key, position_key=_atr_pk, symbol=_atr_sym, quantity=abs(float(getattr(_atr_pos, 'positionAmt', 0))), current_price=_atr_px, side='SELL' if _atr_is_long else 'BUY', position_side='LONG' if _atr_is_long else 'SHORT', action='CLOSE', reason=_atr_result['reason'], is_full_close=True, is_hedge=False)
            except Exception:
                pass

        # ═══════════════════════════════════════════════════════════════════════════
        # DISC-MTF_ATR_TRAIL: live-parity MTF_ATR_TRAIL ratchet (2026-05-29 USER MANDATE)
        # Faithful replication of tradier_manage.py:2318-2333 via shared
        # vec_paths/mtf_atr_trail.py. NOT the sweep stub above (ATR_TRAIL_SWEEP_*).
        # Gated on MTF_EXIT_USE_COMPOUND AND MTF_ATR_TRAIL_ENABLED (both live-default True).
        # TF: crypto=MTF_ATR_TRAIL_TF(15m), stocks=MTF_ATR_TRAIL_TF_TRADIER(5m). mult=2.0.
        # Reason MTF_ATR_TRAIL already in UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS / LOSS_EXIT_TECHNICAL_BYPASS.
        # ═══════════════════════════════════════════════════════════════════════════
        if bool(getattr(config, 'MTF_EXIT_USE_COMPOUND', False)) and bool(getattr(config, 'MTF_ATR_TRAIL_ENABLED', False)):
            try:
                from vec_paths.mtf_atr_trail import update_and_check as _mtfat_check, mtf_atr_trail_tf as _mtfat_tf
                if not hasattr(trade_manager, 'mtf_compound_exit_state'):
                    trade_manager.mtf_compound_exit_state = {}
                _mtfat_atr_tf = _mtfat_tf(config, mode)
                _mtfat_mult = float(getattr(config, 'MTF_ATR_TRAIL_MULT', 2.0))
                for _mtfat_pk, _mtfat_pos in list(trade_manager.positions.items()):
                    if abs(getattr(_mtfat_pos, 'positionAmt', 0)) < 0.0001:
                        continue
                    _mtfat_sym = getattr(_mtfat_pos, 'symbol', '') or (_mtfat_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _mtfat_pk else _mtfat_pk[:-5] if _mtfat_pk.endswith('_LONG') else _mtfat_pk[:-6])
                    _mtfat_ind = indicator_cache.get(_mtfat_sym, {})
                    if not _mtfat_ind:
                        continue
                    _mtfat_px = price_cache.get(_mtfat_sym, 0)
                    if _mtfat_px <= 0:
                        continue
                    _mtfat_atr = float(_mtfat_ind.get(f'atr_{_mtfat_atr_tf}', 0) or 0)
                    _mtfat_entry = float(getattr(_mtfat_pos, 'entry_price', 0) or 0)
                    _mtfat_is_long = _mtfat_pk.endswith('_LONG')
                    _mtfat_state = trade_manager.mtf_compound_exit_state.setdefault(_mtfat_pk, {'trail': 0.0})
                    _mtfat_fire, _mtfat_reason, _ = _mtfat_check(_mtfat_state, _mtfat_entry, _mtfat_px, _mtfat_atr, _mtfat_is_long, _mtfat_mult, _mtfat_atr_tf)
                    if _mtfat_fire:
                        await trade_manager.execute_trade_action(account_key=account_key, position_key=_mtfat_pk, symbol=_mtfat_sym, quantity=abs(float(getattr(_mtfat_pos, 'positionAmt', 0))), current_price=_mtfat_px, side='SELL' if _mtfat_is_long else 'BUY', position_side='LONG' if _mtfat_is_long else 'SHORT', action='CLOSE', reason=_mtfat_reason, is_full_close=True, is_hedge=False)
            except Exception:
                pass

        # ═══════════════════════════════════════════════════════════════════════════
        # DISC-BB_FROZEN_STOP: live-parity R1d BB_FROZEN_STOP (2026-07-10 stdev-arm wire)
        # Faithful replication of ez_manage.py:39538-39576 (R1d): bb_{lower|upper}_{TF}
        # value FROZEN at first bar after open (stored on position as _frozen_bb_act),
        # fires CLOSE only while gain < 0 and price beyond the frozen level.
        # Live crypto CONSUMES config.BB_FROZEN_STOP_ENABLED (config.py:1039, currently
        # True) — wiring here is PARITY RESTORATION; engine was inert on this knob.
        # NOTE: reason BB_FROZEN_STOP_BREACH is NOT in UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS,
        # so with the gate ON the close attempt is BLOCKED in live AND here — identical
        # behavior. Sweep arms enable it by overriding the bypass list in V8_OVERRIDE_FILE.
        # Knobs: BB_FROZEN_STOP_ENABLED / BB_FROZEN_STOP_TF / BB_FROZEN_STOP_FIELD.
        # ═══════════════════════════════════════════════════════════════════════════
        if bool(getattr(config, 'BB_FROZEN_STOP_ENABLED', False)):
            try:
                _bbfs_tf = str(getattr(config, 'BB_FROZEN_STOP_TF', '1h'))
                _bbfs_field_opt = str(getattr(config, 'BB_FROZEN_STOP_FIELD', 'lower'))
                for _bbfs_pk, _bbfs_pos in list(trade_manager.positions.items()):
                    if abs(getattr(_bbfs_pos, 'positionAmt', 0)) < 0.0001:
                        if getattr(_bbfs_pos, '_frozen_bb_act', None) is not None:
                            try: _bbfs_pos._frozen_bb_act = None
                            except Exception: pass
                        continue
                    _bbfs_sym = getattr(_bbfs_pos, 'symbol', '') or (_bbfs_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _bbfs_pk else _bbfs_pk[:-5] if _bbfs_pk.endswith('_LONG') else _bbfs_pk[:-6])
                    _bbfs_ind = indicator_cache.get(_bbfs_sym, {})
                    if not _bbfs_ind:
                        continue
                    _bbfs_px = price_cache.get(_bbfs_sym, 0)
                    if _bbfs_px <= 0:
                        continue
                    _bbfs_is_long = _bbfs_pk.endswith('_LONG')
                    _bbfs_entry = float(getattr(_bbfs_pos, 'entry_price', 0) or 0)
                    if _bbfs_entry <= 0:
                        continue
                    _bbfs_frozen = getattr(_bbfs_pos, '_frozen_bb_act', None)
                    if _bbfs_frozen is None:
                        _bbfs_field_name = ('lower' if _bbfs_is_long else 'upper') if _bbfs_field_opt in ('lower', 'upper') else _bbfs_field_opt
                        _bbfs_raw = _bbfs_ind.get(f'bb_{_bbfs_field_name}_{_bbfs_tf}')
                        if _bbfs_raw not in (None, 0, 0.0):
                            try:
                                _bbfs_frozen = float(_bbfs_raw)
                                _bbfs_pos._frozen_bb_act = _bbfs_frozen
                            except (TypeError, ValueError):
                                _bbfs_frozen = None
                    _bbfs_gain = ((_bbfs_px - _bbfs_entry) / _bbfs_entry * 100.0) if _bbfs_is_long else ((_bbfs_entry - _bbfs_px) / _bbfs_entry * 100.0)
                    if _bbfs_frozen is not None and _bbfs_gain < 0:
                        if (_bbfs_is_long and _bbfs_px < _bbfs_frozen) or (not _bbfs_is_long and _bbfs_px > _bbfs_frozen):
                            _bbfs_reason = f"BB_FROZEN_STOP_BREACH_g{_bbfs_gain:.2f}%_frozen{_bbfs_tf}={_bbfs_frozen}_cur={_bbfs_px}"
                            await trade_manager.execute_trade_action(account_key=account_key, position_key=_bbfs_pk, symbol=_bbfs_sym, quantity=abs(float(getattr(_bbfs_pos, 'positionAmt', 0))), current_price=_bbfs_px, side='SELL' if _bbfs_is_long else 'BUY', position_side='LONG' if _bbfs_is_long else 'SHORT', action='CLOSE', reason=_bbfs_reason, is_full_close=True, is_hedge=False)
            except Exception:
                pass

        # ═══════════════════════════════════════════════════════════════════════════
        # DISC-R4_STDEV: R4_STDEV_MACRO_TOP/BOT — long-window log-price z exit (2026-05-17)
        # BB is for short-window breakouts; this is for REAL macro tops/bottoms on D/W.
        # Runs AFTER R1/R2/R3 (R3 wired in live; engine has DISC-GR15 as parallel HTF exit).
        # Bypass reasons R4_STDEV_MACRO_TOP/BOT added to UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS.
        # Default OFF behind STDEV_MACRO_R4_EXIT_ENABLED. Fail-open on missing macro_z fields.
        # ═══════════════════════════════════════════════════════════════════════════
        if bool(getattr(config, 'STDEV_MACRO_R4_EXIT_ENABLED', False)):
            try:
                import stdev_macro as _r4_sm
                for _r4_pk, _r4_pos in list(trade_manager.positions.items()):
                    if abs(getattr(_r4_pos, 'positionAmt', 0)) < 0.0001:
                        continue
                    _r4_sym = getattr(_r4_pos, 'symbol', '') or (_r4_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _r4_pk else _r4_pk[:-5] if _r4_pk.endswith('_LONG') else _r4_pk[:-6])
                    _r4_ind = indicator_cache.get(_r4_sym, {})
                    if not _r4_ind:
                        continue
                    _r4_px = price_cache.get(_r4_sym, 0)
                    if _r4_px <= 0:
                        continue
                    _r4_is_long = _r4_pk.endswith('_LONG')
                    _r4_side = 'LONG' if _r4_is_long else 'SHORT'
                    _r4_state = _r4_sm.compute_stdev_macro_state(_r4_ind, config_obj=config)
                    _r4_close, _r4_reason = _r4_sm.r4_exit(_r4_side, _r4_state, _r4_ind, config)
                    if _r4_close:
                        _r4_gain = float(getattr(_r4_pos, 'gain', 0) or 0)
                        _r4_qty = abs(float(getattr(_r4_pos, 'positionAmt', 0)))
                        _r4_why = f"{_r4_reason}_pctbD={_r4_state.get('bb_pct_b_D', 0.5):.2f}_pctb4h={_r4_state.get('bb_pct_b_4h', 0.5):.2f}_g{_r4_gain:.2f}%"
                        v8_logger.info(f"[DISC-R4_STDEV] {_r4_pk}: state={_r4_state.get('macro_state')} pctbD={_r4_state.get('bb_pct_b_D'):.3f} pctb4h={_r4_state.get('bb_pct_b_4h'):.3f} gain={_r4_gain:.2f}% — CLOSE")
                        try:
                            await trade_manager.execute_trade_action(account_key=account_key, position_key=_r4_pk, symbol=_r4_sym, quantity=_r4_qty, current_price=_r4_px, side='SELL' if _r4_is_long else 'BUY', position_side=_r4_side, action='CLOSE', reason=_r4_why, is_full_close=True, is_hedge=False)
                        except Exception as _r4_cl_err:
                            if step < 10 or step % 1000 == 0:
                                v8_logger.error(f"[DISC-R4_STDEV_ERR] {_r4_pk}: {_r4_cl_err}")
            except Exception:
                pass

        # ═══════════════════════════════════════════════════════════════════════════
        # DISC-4: OBLIGATORY_HEDGE — mirror ez_manage.py:14380 (updated 2026-05-12)
        # When UNIVERSAL_NOLOSS_GATE blocks a close, live fires a hedge if:
        #   gain <= OBLIGATORY_HEDGE_MIN_LOSS_PCT (-0.25% default)
        #   AND cascade: 3m AND (15m OR 1h) → 3m AND 1h → 3m alone → legacy count
        #   AND no existing hedge for this pk
        # In backtest: run per-bar for all underwater positions (NOLOSS gate fires before check_exit).
        # 2026-05-10 PARITY MODE gated.
        # ═══════════════════════════════════════════════════════════════════════════
        if os.environ.get("V8_PARITY_MODE") != "1" and bool(getattr(config, 'OBLIGATORY_HEDGE_ENABLED', True)) and \
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
                _oh_3m_against = False
                _oh_15m_against = False
                _oh_1h_against = False
                for _oh_tf in ('1m', '3m', '15m', '1h'):
                    if not _oh_use[_oh_tf]:
                        continue
                    _oh_tfs_enabled += 1
                    _ow1 = float(_oh_ind.get(f'wt1_{_oh_tf}', 0) or 0)
                    _ow2 = float(_oh_ind.get(f'wt2_{_oh_tf}', 0) or 0)
                    if _ow1 == 0 and _ow2 == 0:
                        continue
                    _oh_ag = (_ow1 < _ow2) if _oh_is_long else (_ow1 > _ow2)
                    _oh_wt_against += int(_oh_ag)
                    if _oh_tf == '3m': _oh_3m_against = bool(_oh_ag)
                    elif _oh_tf == '15m': _oh_15m_against = bool(_oh_ag)
                    elif _oh_tf == '1h': _oh_1h_against = bool(_oh_ag)
                # USER 2026-05-12: compute 15m independently when not in loop (mirrors ez_manage.py:14422-14425)
                if not _oh_use['15m']:
                    _ow1_15m = float(_oh_ind.get('wt1_15m', 0) or 0)
                    _ow2_15m = float(_oh_ind.get('wt2_15m', 0) or 0)
                    _oh_15m_against = (_ow1_15m < _ow2_15m) if _oh_is_long else (_ow1_15m > _ow2_15m)
                # Cascade: 3m AND (15m OR 1h) → 3m AND 1h → 3m alone → legacy count (mirrors ez_manage.py:14432-14443)
                _oh_req_3m_15m_or_1h = bool(getattr(config, 'HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H', True))
                _oh_req_3m_1h = bool(getattr(config, 'HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H', False))
                _oh_use_3m_alone = bool(getattr(config, 'HEDGE_TRIGGER_USE_WT_3M_ALONE', True))
                if _oh_req_3m_15m_or_1h:
                    _oh_user_trigger = _oh_3m_against and (_oh_15m_against or _oh_1h_against)
                    _oh_trigger_label = "3m_AND_(15m_OR_1h)"
                elif _oh_req_3m_1h:
                    _oh_user_trigger = _oh_3m_against and _oh_1h_against
                    _oh_trigger_label = "3m_AND_1h"
                elif _oh_use_3m_alone:
                    _oh_user_trigger = _oh_3m_against
                    _oh_trigger_label = "3m_only"
                else:
                    _oh_user_trigger = _oh_15m_against or (_oh_3m_against and _oh_1h_against)
                    _oh_trigger_label = "15m_OR_(3m_AND_1h)"
                # STDEV_MACRO_HEDGE_BOOST: additive trigger when origin held against macro extreme.
                # Never removes existing 3m/15m/1h triggers — only adds an OR path. Default OFF.
                _oh_macro_trigger = False
                if bool(getattr(config, 'STDEV_MACRO_HEDGE_BOOST_ENABLED', False)):
                    try:
                        import stdev_macro as _ohm_sm
                        _ohm_state = _ohm_sm.compute_stdev_macro_state(_oh_ind, config_obj=config)
                        _ohm_side = 'LONG' if _oh_is_long else 'SHORT'
                        _ohm_fire, _ohm_reason = _ohm_sm.hedge_trigger_boost(_ohm_side, _ohm_state, config)
                        if _ohm_fire:
                            _oh_macro_trigger = True
                            _oh_trigger_label = f"{_oh_trigger_label}_OR_{_ohm_reason}"
                    except Exception:
                        pass
                if _oh_tfs_enabled > 0 and (_oh_wt_against >= _oh_bt_req or _oh_user_trigger or _oh_macro_trigger):
                    _oh_bt_cd_dict[_oh_pk] = step
                    v8_logger.warning(f"[OBLIGATORY_HEDGE_BACKTEST] {_oh_pk}: gain={_oh_gain:.2f}% trigger={_oh_trigger_label} wt_against={_oh_wt_against}/{_oh_tfs_enabled} (3m={_oh_3m_against} 15m={_oh_15m_against} 1h={_oh_1h_against}) — scan_and_hedge_losers")
                    try:
                        await hedge_engine.scan_and_hedge_losers(account_key)
                    except Exception as _oh_err:
                        if step < 10 or step % 1000 == 0:
                            v8_logger.error(f"[OBLIGATORY_HEDGE_BACKTEST_ERR] {_oh_pk}: {_oh_err}")

        # P3-DC_BREACH: DC_BREACH_REDUCE — reduce losing positions that breach 15m DC channel
        # Mirrors ez_manage.py:27002-27224 monitor_dc_breach_reduce() (hedged + unhedged paths).
        # Fires AFTER DISC-4 (OBLIGATORY_HEDGE) so hedges are already managed first.
        # Gated by V8_USE_VEC_ALL=1 and DC_BREACH_REDUCE_ENABLED (default False).
        if V8_VEC_PARITY_AVAILABLE and os.environ.get("V8_USE_VEC_ALL", "0") == "1":
            try:
                from vec_paths.dc_breach_reduce import check_dc_breach_reduce as _dcbr_check
                for _dcbr_pk, _dcbr_pos in list(trade_manager.positions.items()):
                    if abs(getattr(_dcbr_pos, 'positionAmt', 0)) < 0.0001:
                        continue
                    if float(getattr(_dcbr_pos, 'gain', 0) or 0) >= 0:
                        continue
                    _dcbr_sym = (getattr(_dcbr_pos, 'symbol', '') or
                                 (_dcbr_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _dcbr_pk
                                  else (_dcbr_pk[:-5] if _dcbr_pk.endswith('_LONG') else _dcbr_pk[:-6])))
                    _dcbr_ind = indicator_cache.get(_dcbr_sym, {}) if isinstance(indicator_cache, dict) else {}
                    _dcbr_result = _dcbr_check(_dcbr_ind, None, _dcbr_pos, mode, config)
                    if _dcbr_result:
                        _dcbr_qty = abs(float(getattr(_dcbr_pos, 'positionAmt', 0))) * 0.5
                        _dcbr_is_long = _dcbr_pk.endswith('_LONG')
                        _dcbr_px = float(_dcbr_ind.get('current_price', 0) or 0)
                        if _dcbr_qty > 0 and _dcbr_px > 0:
                            try:
                                await trade_manager.execute_trade_action(
                                    account_key=account_key,
                                    position_key=_dcbr_pk,
                                    symbol=_dcbr_sym,
                                    quantity=_dcbr_qty,
                                    current_price=_dcbr_px,
                                    side='SELL' if _dcbr_is_long else 'BUY',
                                    position_side='LONG' if _dcbr_is_long else 'SHORT',
                                    action='REDUCE',
                                    reason=_dcbr_result['reason'],
                                    is_hedge=False,
                                )
                                v8_logger.warning(f"[P3_DC_BREACH_REDUCE] {_dcbr_pk}: gain={getattr(_dcbr_pos, 'gain', 0):.2f}% — {_dcbr_result['reason']}")
                            except Exception as _dcbr_exec_e:
                                if step < 10 or step % 1000 == 0:
                                    v8_logger.error(f"[P3_DC_BREACH_REDUCE_ERR] {_dcbr_pk}: {_dcbr_exec_e}")
            except ImportError:
                pass
            except Exception as _dcbr_outer_e:
                if step < 10 or step % 1000 == 0:
                    v8_logger.error(f"[P3_DC_BREACH_REDUCE_OUTER_ERR] step={step}: {_dcbr_outer_e}")

        # DISC-RATIO_REDUCE: portfolio L/S rebalance — mirrors ez_manage.py:28693 ratio_rebalance_loop()
        # Fires when one side exceeds RATIO_MULTIPLIER×the other. Closes lowest-gain overweight positions.
        # Gated by V8_USE_VEC_ALL=1 + RATIO_REBALANCE_ENABLED (default True from ratio_reduce.py).
        if V8_VEC_PARITY_AVAILABLE and os.environ.get("V8_USE_VEC_ALL", "0") == "1" and \
                bool(getattr(config, 'RATIO_REBALANCE_ENABLED', True)):
            try:
                from vec_paths.ratio_reduce import select_ratio_reduce_targets as _rr_select
                _rr_targets = _rr_select(trade_manager.positions, _pool_long_count, _pool_short_count, mode, config)
                for _rr_pk in _rr_targets:
                    _rr_pos = trade_manager.positions.get(_rr_pk)
                    if not _rr_pos or abs(getattr(_rr_pos, 'positionAmt', 0)) < 0.0001:
                        continue
                    _rr_sym = (getattr(_rr_pos, 'symbol', '') or
                               (_rr_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _rr_pk
                                else (_rr_pk[:-5] if _rr_pk.endswith('_LONG') else _rr_pk[:-6])))
                    _rr_ind = indicator_cache.get(_rr_sym, {}) if isinstance(indicator_cache, dict) else {}
                    _rr_px = float(_rr_ind.get('current_price', 0) or 0)
                    if _rr_px <= 0:
                        continue
                    _rr_is_long = _rr_pk.endswith('_LONG')
                    _rr_gain = float(getattr(_rr_pos, 'gain', 0) or 0)
                    _rr_qty = abs(float(getattr(_rr_pos, 'positionAmt', 0)))
                    if _pool_short_count > 0:
                        _rr_ratio = _pool_long_count / _pool_short_count
                    elif _pool_long_count > 0:
                        _rr_ratio = float(_pool_long_count)
                    else:
                        _rr_ratio = 0.0
                    _rr_reason = (f"RATIO_REDUCE_{'LONG' if _rr_is_long else 'SHORT'}"
                                  f"_L{_pool_long_count}_S{_pool_short_count}_ratio{_rr_ratio:.2f}")
                    try:
                        await trade_manager.execute_trade_action(account_key=account_key, position_key=_rr_pk, symbol=_rr_sym, quantity=_rr_qty, current_price=_rr_px, side='SELL' if _rr_is_long else 'BUY', position_side='LONG' if _rr_is_long else 'SHORT', action='CLOSE', reason=_rr_reason, is_full_close=True, is_hedge=False)
                        v8_logger.warning(f"[DISC-RATIO_REDUCE] {_rr_pk}: L={_pool_long_count} S={_pool_short_count} ratio={_rr_ratio:.2f} gain={_rr_gain:.2f}%")
                    except Exception as _rr_exec_e:
                        if step < 10 or step % 1000 == 0:
                            v8_logger.error(f"[DISC-RATIO_REDUCE_ERR] {_rr_pk}: {_rr_exec_e}")
            except ImportError:
                pass
            except Exception as _rr_outer_e:
                if step < 10 or step % 1000 == 0:
                    v8_logger.error(f"[DISC-RATIO_REDUCE_OUTER_ERR] step={step}: {_rr_outer_e}")

        # DISC-WT5OF5: NOLOSS_BYPASS_WT_5OF5 — bypass NOLOSS gate when all N WT TFs against position.
        # Mirrors tradier_manage.py NOLOSS_BYPASS_WT_5OF5 block (default OFF).
        # Uses indicator_cache directly (DISC blocks run in engine mode, no NPZStore available).
        # Gated by V8_USE_VEC_ALL=1 + NOLOSS_BYPASS_WT_5OF5_ENABLED (default False).
        if V8_VEC_PARITY_AVAILABLE and os.environ.get("V8_USE_VEC_ALL", "0") == "1" and \
                bool(getattr(config, 'NOLOSS_BYPASS_WT_5OF5_ENABLED', False)):
            _wt5_min_tfs = max(1, min(5, int(getattr(config, 'WT5OF5_MIN_TFS', 5))))
            _wt5_require_loser = bool(getattr(config, 'WT5OF5_REQUIRE_OPEN_LOSER', True))
            _wt5_tfs = ['5m', '15m', '1h', '4h', 'D'] if mode == 'tradier' else ['3m', '15m', '1h', '4h', 'D']
            for _wt5_pk, _wt5_pos in list(trade_manager.positions.items()):
                if abs(getattr(_wt5_pos, 'positionAmt', 0)) < 0.0001:
                    continue
                _wt5_gain = float(getattr(_wt5_pos, 'gain', 0) or 0)
                if _wt5_require_loser and _wt5_gain >= 0:
                    continue
                _wt5_sym = (getattr(_wt5_pos, 'symbol', '') or
                            (_wt5_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _wt5_pk
                             else (_wt5_pk[:-5] if _wt5_pk.endswith('_LONG') else _wt5_pk[:-6])))
                _wt5_ind = indicator_cache.get(_wt5_sym, {}) if isinstance(indicator_cache, dict) else {}
                if not _wt5_ind:
                    continue
                _wt5_is_long = _wt5_pk.endswith('_LONG')
                _wt5_against = 0
                for _wt5_tf in _wt5_tfs:
                    _w1 = float(_wt5_ind.get(f'wt1_{_wt5_tf}', 0) or 0)
                    _w2 = float(_wt5_ind.get(f'wt2_{_wt5_tf}', 0) or 0)
                    if _w1 == 0 and _w2 == 0:
                        continue
                    if (_wt5_is_long and _w1 < _w2) or (not _wt5_is_long and _w1 > _w2):
                        _wt5_against += 1
                if _wt5_against < _wt5_min_tfs:
                    continue
                _wt5_side = 'LONG' if _wt5_is_long else 'SHORT'
                _wt5_px = float(_wt5_ind.get('current_price', 0) or 0)
                if _wt5_px <= 0:
                    continue
                _wt5_reason = f"NOLOSS_BYPASS_WT_5OF5_{_wt5_against}of5_tfs_against_{_wt5_side}_g{_wt5_gain:.3f}%"
                try:
                    await trade_manager.execute_trade_action(account_key=account_key, position_key=_wt5_pk, symbol=_wt5_sym, quantity=abs(float(getattr(_wt5_pos, 'positionAmt', 0))), current_price=_wt5_px, side='SELL' if _wt5_is_long else 'BUY', position_side=_wt5_side, action='CLOSE', reason=_wt5_reason, is_full_close=True, is_hedge=False)
                    v8_logger.warning(f"[DISC-WT5OF5] {_wt5_pk}: {_wt5_against}/{_wt5_min_tfs} TFs against gain={_wt5_gain:.2f}%")
                except Exception as _wt5_exec_e:
                    if step < 10 or step % 1000 == 0:
                        v8_logger.error(f"[DISC-WT5OF5_ERR] {_wt5_pk}: {_wt5_exec_e}")

        # DISC-W_EXIT: exit when weekly WaveTrend crosses against position (2026-05-15)
        # Long exits when wt1_W crosses below wt2_W; short exits when crosses above.
        # Cross = prev bar was opposite sign; fail-open when both wt values are 0.
        if bool(getattr(config, 'WT_W_EXIT_ENABLED', False)):
            for _we_pk, _we_pos in list(trade_manager.positions.items()):
                if abs(getattr(_we_pos, 'positionAmt', 0) or 0) < 0.0001:
                    continue
                _we_sym = (getattr(_we_pos, 'symbol', '') or
                           (_we_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _we_pk
                            else (_we_pk[:-5] if _we_pk.endswith('_LONG') else _we_pk[:-6])))
                _we_ind = indicator_cache.get(_we_sym, {}) if isinstance(indicator_cache, dict) else {}
                if not _we_ind:
                    continue
                _we_wt1 = float(_we_ind.get('wt1_W', 0) or 0)
                _we_wt2 = float(_we_ind.get('wt2_W', 0) or 0)
                if _we_wt1 == 0 and _we_wt2 == 0:
                    continue
                _we_now_bull = _we_wt1 > _we_wt2
                _we_prev_bull = _w_exit_prev.get(_we_sym)
                _w_exit_prev[_we_sym] = _we_now_bull
                if _we_prev_bull is None:
                    continue
                _we_is_long = _we_pk.endswith('_LONG')
                _we_cross = (_we_is_long and _we_prev_bull and not _we_now_bull) or \
                            (not _we_is_long and not _we_prev_bull and _we_now_bull)
                if not _we_cross:
                    continue
                _we_gain = float(getattr(_we_pos, 'gain', 0) or 0)
                _we_px = float(_we_ind.get('current_price', 0) or 0)
                if _we_px <= 0:
                    continue
                _we_side = 'LONG' if _we_is_long else 'SHORT'
                _we_reason = f"WT_W_CROSS_EXIT_{_we_side}_wt1={_we_wt1:.1f}_wt2={_we_wt2:.1f}_g{_we_gain:.3f}%"
                try:
                    await trade_manager.execute_trade_action(account_key=account_key, position_key=_we_pk, symbol=_we_sym, quantity=abs(float(getattr(_we_pos, 'positionAmt', 0))), current_price=_we_px, side='SELL' if _we_is_long else 'BUY', position_side=_we_side, action='CLOSE', reason=_we_reason, is_full_close=True, is_hedge=False)
                    v8_logger.info(f"[DISC-W_EXIT] {_we_pk}: W cross exit gain={_we_gain:.2f}%")
                except Exception as _we_err:
                    if step < 10 or step % 1000 == 0:
                        v8_logger.error(f"[DISC-W_EXIT_ERR] {_we_pk}: {_we_err}")

        # DISC-HAIKU_WINNER: winner pyramid + giveback-reduce — numeric portion of HaikuOverseer.manage_winners()
        # Mirrors ez_manage.py:46331 manage_winners():
        #   gain > HAIKU_AUGMENT_GAIN_THRESHOLD (3%) → augment HAIKU_AUGMENT_FRACTION (10%) of position.
        #   gain drops < HAIKU_REDUCE_GAIN_THRESHOLD (2.5%) after augment → reduce by augmented qty.
        #   gain recovers above threshold after reduce → re-augment same qty.
        # AI reversal (scan_decisions/call_haiku) is NOT implemented — non-deterministic in backtest.
        # Gated by V8_USE_VEC_ALL=1 + HAIKU_WINNER_ENABLED (default False).
        if V8_VEC_PARITY_AVAILABLE and os.environ.get("V8_USE_VEC_ALL", "0") == "1" and \
                bool(getattr(config, 'HAIKU_WINNER_ENABLED', False)):
            if not hasattr(trade_manager, '_haiku_state'):
                trade_manager._haiku_state = {}
            try:
                from vec_paths.haiku_winner import _evaluate as _hw_eval
                _hw_aug_thr = float(getattr(config, 'HAIKU_AUGMENT_GAIN_THRESHOLD', 3.0))
                _hw_red_thr = float(getattr(config, 'HAIKU_REDUCE_GAIN_THRESHOLD', 2.5))
                _hw_frac = float(getattr(config, 'HAIKU_AUGMENT_FRACTION', 0.10))
                for _hw_pk, _hw_pos in list(trade_manager.positions.items()):
                    _hw_amt = abs(float(getattr(_hw_pos, 'positionAmt', 0) or 0))
                    if _hw_amt < 0.0001:
                        continue
                    _hw_ep = float(getattr(_hw_pos, 'entry_price', 0) or 0)
                    if _hw_ep <= 0:
                        continue
                    _hw_sym = (getattr(_hw_pos, 'symbol', '') or
                               (_hw_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _hw_pk
                                else (_hw_pk[:-5] if _hw_pk.endswith('_LONG') else _hw_pk[:-6])))
                    _hw_ind = indicator_cache.get(_hw_sym, {}) if isinstance(indicator_cache, dict) else {}
                    _hw_px = float(_hw_ind.get('current_price', 0) or 0)
                    if _hw_px <= 0:
                        continue
                    _hw_is_long = _hw_pk.endswith('_LONG')
                    _hw_gain = ((_hw_px - _hw_ep) / _hw_ep * 100.0) if _hw_is_long else ((_hw_ep - _hw_px) / _hw_ep * 100.0)
                    _hw_st = trade_manager._haiku_state.get(_hw_pk, {'augmented_qty': 0.0, 'reduced': False})
                    _hw_result = _hw_eval(_hw_gain, _hw_amt, _hw_px, _hw_aug_thr, _hw_red_thr, _hw_frac, _hw_is_long, _hw_st.get('augmented_qty', 0.0), _hw_st.get('reduced', False))
                    if not _hw_result:
                        continue
                    _hw_action = _hw_result['action']
                    _hw_qty = _hw_result['qty']
                    _hw_reason = _hw_result['reason']
                    try:
                        await trade_manager.execute_trade_action(account_key=account_key, position_key=_hw_pk, symbol=_hw_sym, quantity=_hw_qty, current_price=_hw_px, side=('SELL' if _hw_is_long else 'BUY') if _hw_action in ('REDUCE', 'CLOSE') else ('BUY' if _hw_is_long else 'SELL'), position_side='LONG' if _hw_is_long else 'SHORT', action=_hw_action, reason=_hw_reason, is_full_close=False, is_hedge=False)
                        _hw_upd = _hw_result.get('haiku_state_update', {})
                        if _hw_pk not in trade_manager._haiku_state:
                            trade_manager._haiku_state[_hw_pk] = {'augmented_qty': 0.0, 'reduced': False}
                        if 'augmented_qty_delta' in _hw_upd:
                            trade_manager._haiku_state[_hw_pk]['augmented_qty'] += _hw_upd['augmented_qty_delta']
                        if 'reduced' in _hw_upd:
                            trade_manager._haiku_state[_hw_pk]['reduced'] = _hw_upd['reduced']
                        v8_logger.warning(f"[DISC-HAIKU_WINNER] {_hw_pk}: {_hw_action} qty={_hw_qty:.4f} gain={_hw_gain:.2f}% — {_hw_reason}")
                    except Exception as _hw_exec_e:
                        if step < 10 or step % 1000 == 0:
                            v8_logger.error(f"[DISC-HAIKU_WINNER_ERR] {_hw_pk}: {_hw_exec_e}")
            except ImportError:
                pass
            except Exception as _hw_outer_e:
                if step < 10 or step % 1000 == 0:
                    v8_logger.error(f"[DISC-HAIKU_WINNER_OUTER_ERR] step={step}: {_hw_outer_e}")

        # Clear reduce cooldowns per bar (each bar = 3-15 min in real time)
        # 2026-05-16 — gated by BT_PRESERVE_DEBOUNCE_ACROSS_BARS (default False).
        # See run_simulation_crypto for full rationale.
        _bt_preserve_debounce_t = bool(getattr(config, 'BT_PRESERVE_DEBOUNCE_ACROSS_BARS', False))
        if not _bt_preserve_debounce_t:
            try:
                import config_tradier as _ct_dbg_t
                _bt_preserve_debounce_t = bool(getattr(_ct_dbg_t, 'BT_PRESERVE_DEBOUNCE_ACROSS_BARS', False)) or bool(getattr(_ct_dbg_t.TradierConfig, 'BT_PRESERVE_DEBOUNCE_ACROSS_BARS', False))
            except Exception:
                pass
        if not _bt_preserve_debounce_t:
            if hasattr(ez_manage, '_recent_reduces'):
                ez_manage._recent_reduces.clear()

        # === PARTIAL_PROFIT_LOCK v2 inline (2026-04-21) — fires regardless of V8_SKIP_PROCESS_POSITION.
        # Step 1 (+0.5%): TP 50% REDUCE; stop_level = entry × (1 ± BE_buffer%). Close before BE.
        # Step 2 (+0.75%): upgrade stop_level → first_exit_price (locks +0.5% scalp).
        # Step 3 (price hits stop): full CLOSE of remainder.
        # 2026-05-10 PARITY MODE gated.
        _ppl_bt_on = bool(getattr(config, 'PARTIAL_PROFIT_LOCK_ENABLED', False)) and os.environ.get("V8_PARITY_MODE") != "1"
        if _ppl_bt_on:
            _ppl_bt_accts = set(getattr(config, 'PARTIAL_PROFIT_LOCK_ACCOUNTS', []) or []) | set(getattr(config, 'PARTIAL_PROFIT_LOCK_ACCOUNTS_TRADIER', []) or [])
            if account_key in _ppl_bt_accts:
                # P2-G: PPL sweep param substitution (backtest only — never touches live tradier_manage.py)
                _ppl_sweep_on = getattr(config, 'PARTIAL_PROFIT_LOCK_SWEEP_ENABLED', False)
                if _ppl_sweep_on:
                    _ppl_bt_min = float(getattr(config, 'PARTIAL_PROFIT_LOCK_SWEEP_GAIN_PCT', 0.3))
                    _ppl_bt_arm = float(getattr(config, 'PARTIAL_PROFIT_LOCK_SWEEP_ARM_PCT', 0.5))
                else:
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
                    _ppl_bt_gain_raw = ((_ppl_bt_px - _ppl_bt_ep) / _ppl_bt_ep * 100.0) if _ppl_bt_is_long else ((_ppl_bt_ep - _ppl_bt_px) / _ppl_bt_ep * 100.0)
                    # P2-G: deduct slippage from effective gain when PPL sweep mode active
                    _ppl_sweep_slip = float(getattr(config, 'PARTIAL_PROFIT_LOCK_SLIPPAGE_PCT', 0.0)) if _ppl_sweep_on else 0.0
                    _ppl_bt_gain = _ppl_bt_gain_raw - _ppl_sweep_slip
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

        # DC stop loss sweep test (DC_LOW4_STOP_ENABLED / DC_LOW_STOP_ENABLED).
        # Uses fixed r1_stop_price recorded at open/augment time.
        _dc4_stop_on = getattr(config, 'DC_LOW4_STOP_ENABLED', False)
        _dc1_stop_on = getattr(config, 'DC_LOW_STOP_ENABLED', False)
        if _dc4_stop_on or _dc1_stop_on:
            _dc_stop_mode = getattr(config, 'mode', 'crypto') if hasattr(config, 'mode') else os.environ.get('V8_MODE', 'crypto')
            for _ds_pk, _ds_pos in list(trade_manager.positions.items()):
                if abs(getattr(_ds_pos, 'positionAmt', 0)) < 0.0001:
                    continue
                _ds_sym = getattr(_ds_pos, 'symbol', '') or _ds_pk.split(':', 1)[-1].rsplit('_', 1)[0]
                _ds_is_long = _ds_pk.endswith('_LONG')
                _ds_ind = indicator_cache.get(_ds_sym.upper(), {}) if isinstance(indicator_cache, dict) else {}
                _ds_px = float(getattr(_ds_pos, 'mark_price', 0) or _ds_ind.get('current_price', 0) or 0)
                if _ds_px <= 0:
                    continue
                _ds_stop = float(getattr(_ds_pos, 'r1_stop_price', 0.0) or 0.0)
                if _ds_stop <= 0:
                    if str(_dc_stop_mode).lower() == 'tradier':
                        if _dc4_stop_on:
                            _ds_stop = float(_ds_ind.get('dc_low4_5m' if _ds_is_long else 'dc_high4_5m') or 0)
                        if _ds_stop <= 0 and _dc1_stop_on:
                            _ds_stop = float(_ds_ind.get('dc_low_5m' if _ds_is_long else 'dc_high_5m') or 0)
                    else:
                        if _dc4_stop_on:
                            _ds_stop = float(_ds_ind.get('dc_low4_3m' if _ds_is_long else 'dc_high4_3m') or 0)
                        if _ds_stop <= 0 and _dc1_stop_on:
                            _ds_stop = float(_ds_ind.get('dc_low_3m' if _ds_is_long else 'dc_high_3m') or 0)
                if _ds_stop <= 0:
                    continue
                _ds_breached = (_ds_is_long and _ds_px <= _ds_stop) or ((not _ds_is_long) and _ds_px >= _ds_stop)
                if _ds_breached:
                    _ds_amt = abs(float(getattr(_ds_pos, 'positionAmt', 0)))
                    _ds_side = 'SELL' if _ds_is_long else 'BUY'
                    _ds_gr_hedge_on = getattr(config, 'DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED', False)
                    _ds_did_hedge = False
                    if _ds_gr_hedge_on:
                        try:
                            from golden_rule_htf import score_entry_htf as _ds_gr_fn
                            _ds_gr_min_tfs = int(getattr(config, 'DC4_STOP_GR_SCORE_MIN_TFS', 3))
                            _ds_gr_min_ind = int(getattr(config, 'DC4_STOP_GR_SCORE_MIN_IND', 5))
                            _ds_gr_passes, _ds_gr_n, _ds_gr_detail = _ds_gr_fn(
                                _ds_ind, not _ds_is_long, str(_dc_stop_mode).lower(),
                                _ds_gr_min_tfs, _ds_gr_min_ind, _ds_px)
                            if _ds_gr_passes:
                                await hedge_engine.scan_and_hedge_losers(account_key)
                                _ds_did_hedge = True
                                v8_logger.warning(f"[DC4_STOP_GR_HEDGE] {_ds_pk}: DC4 breach but GR={_ds_gr_n}tfs>={_ds_gr_min_tfs} against — HEDGE not stop. {_ds_gr_detail}")
                        except Exception as _ds_ge:
                            v8_logger.debug(f"[DC4_STOP_GR_ERR] {_ds_pk}: {_ds_ge}")
                    if not _ds_did_hedge:
                        try:
                            await trade_manager.execute_now(
                                position_key=_ds_pk, account_key=account_key, symbol=_ds_sym,
                                original_positionAmt=_ds_amt, side=_ds_side,
                                position_side='LONG' if _ds_is_long else 'SHORT',
                                quantity=_ds_amt, old_price=_ds_px,
                                unique_id=f"DC_STOP_{int(_sim_ts[0])}",
                                reason=f"DC_STOP_BREACH_px{_ds_px:.6f}_stop{_ds_stop:.6f}",
                                is_full_close=True, action='CLOSE')
                        except Exception:
                            pass
        for _se_pk, _se_pos in list(trade_manager.positions.items()):
            if abs(getattr(_se_pos, 'positionAmt', 0)) < 0.0001: continue
            _se_sym = getattr(_se_pos, 'symbol', '') or _se_pk.split(':', 1)[-1].rsplit('_', 1)[0]
            _se_is_long = _se_pk.endswith('_LONG')
            _se_tf = getattr(config, 'EXIT_STRUCT_TF', 'None')
            if _se_tf == 'None' or not _se_tf: _se_tf = getattr(config, 'LONG_STRUCT_EXIT_TF' if _se_is_long else 'SHORT_STRUCT_EXIT_TF', 'None')
            if _se_tf != 'None' and _se_tf:
                _se_ind = indicator_cache.get(_se_sym.upper(), {}) if isinstance(indicator_cache, dict) else {}
                _se_open = float(_se_ind.get(f"open_{_se_tf}", 0.0) or 0.0)
                _se_high_prev = float(_se_ind.get(f"high_{_se_tf}_prev", 0.0) or 0.0)
                _se_low_prev = float(_se_ind.get(f"low_{_se_tf}_prev", 0.0) or 0.0)
                if _se_open > 0 and _se_high_prev > 0 and _se_low_prev > 0:
                    _last_open = getattr(_se_pos, "_last_struct_open", None)
                    if _last_open is None:
                        setattr(_se_pos, "_last_struct_open", _se_open); setattr(_se_pos, "_last_high_prev", _se_high_prev); setattr(_se_pos, "_last_low_prev", _se_low_prev)
                    elif abs(_se_open - _last_open) > 1e-8:
                        _last_hp = getattr(_se_pos, "_last_high_prev", _se_high_prev); _last_lp = getattr(_se_pos, "_last_low_prev", _se_low_prev)
                        _se_fire = (_se_high_prev < _last_hp and _se_low_prev < _last_lp) if _se_is_long else (_se_high_prev > _last_hp and _se_low_prev > _last_lp)
                        setattr(_se_pos, "_last_struct_open", _se_open); setattr(_se_pos, "_last_high_prev", _se_high_prev); setattr(_se_pos, "_last_low_prev", _se_low_prev)
                        if _se_fire:
                            try:
                                _se_amt = abs(float(getattr(_se_pos, 'positionAmt', 0)))
                                v8_logger.warning(f"⛔ [HYBRID_STRUCT_EXIT] {_se_pk}: structure breakdown on {_se_tf} (high_prev={_se_high_prev:.6f} vs last_hp={_last_hp:.6f}, low_prev={_se_low_prev:.6f} vs last_lp={_last_lp:.6f}) → CLOSE")
                                await trade_manager.execute_now(position_key=_se_pk, account_key=account_key, symbol=_se_sym, original_positionAmt=_se_amt, side=("SELL" if _se_is_long else "BUY"), position_side=('LONG' if _se_is_long else 'SHORT'), quantity=_se_amt, old_price=float(_se_ind.get('current_price', 0) or 0), unique_id=f"HYBRID_STRUCT_EXIT_{int(_sim_ts[0])}", reason=f"HYBRID_STRUCT_EXIT_{_se_tf}_g{getattr(_se_pos, 'gain', 0):.2f}%", is_full_close=True, action="CLOSE")
                            except Exception: pass


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
        # 2026-05-09 PARITY AUDIT — re-enabled (was disabled at 20:00, broke v8 by removing
        # the only fast escape from NOLOSS-locked positions: positions stuck → AUGMENT_LOCK
        # blocks reopens → deadlock → v8 dormant). Live has SRS active too (config:1218).
        # The over-firing vs live needs a different fix: tighten the OR cascade to live's
        # AND cascade in a follow-up patch, not disable. Set V8_DISABLE_RELAXED_SRS=1 to
        # opt out (e.g. for sweep isolation runs).
        _v8_srs_on = (getattr(config, 'STRUCTURAL_RANGE_SHIFT_EXIT', False)
                      and os.environ.get("V8_DISABLE_RELAXED_SRS") != "1"
                      and os.environ.get("V8_PARITY_MODE") != "1")
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
                # 2026-05-10 PARITY AUDIT: tightened OR cascade → live's STRICT cascade
                # (mirror of ez_positions_quick.py:13586-13601). Added entry>upper
                # precondition and full AND-gating across 1h, 15m, 3m so v8 SRS fires
                # only when live's SRS would fire. Was 505 V8_ONLY closes / 7d → expected
                # near-zero with strict cascade.
                _srs_entry_v8 = float(getattr(_srs_pos, 'entry_price', 0) or 0)
                _srs_fire = False
                if _srs_is_long and _srs_hi > 0 and _srs_entry_v8 > _srs_hi:
                    _srs_prox = abs(_srs_p - _srs_hi) / _srs_hi <= _srs_band
                    _srs_1h = (_srs_k1h >= _srs_k_hi) and (_srs_k1h < _srs_k1h_p) and (_srs_wt1_1h < _srs_wt2_1h)
                    _srs_15m = (_srs_k15m >= _srs_k_hi) and (_srs_k15m < _srs_k15m_p) and (_srs_wt1_15m < _srs_wt2_15m)
                    _srs_3m = _srs_wt1_3m < _srs_wt2_3m
                    _srs_fire = _srs_prox and _srs_1h and _srs_15m and _srs_3m
                elif (not _srs_is_long) and _srs_lo > 0 and _srs_entry_v8 > _srs_lo:
                    _srs_prox = abs(_srs_p - _srs_lo) / _srs_lo <= _srs_band
                    _srs_1h = (_srs_k1h <= _srs_k_lo) and (_srs_k1h > _srs_k1h_p) and (_srs_wt1_1h > _srs_wt2_1h)
                    _srs_15m = (_srs_k15m <= _srs_k_lo) and (_srs_k15m > _srs_k15m_p) and (_srs_wt1_15m > _srs_wt2_15m)
                    _srs_3m = _srs_wt1_3m > _srs_wt2_3m
                    _srs_fire = _srs_prox and _srs_1h and _srs_15m and _srs_3m
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
            # WT_BOTTOM_CROSS_GATE — only enter near fresh WT crosses (2026-05-14).
            # DC hard stops on dc_low4_3m/5m mean bad-timed entries get stopped out immediately.
            # Gate off by default; enabled via V8_OVERRIDE_FILE in sweep variants.
            # Knobs: WT_BOTTOM_CROSS_GATE_ENABLED, WT_BOTTOM_MAX_BARS_AGO_BASE (3m crypto/5m tradier),
            #        WT_BOTTOM_MAX_BARS_AGO_15M, WT_BOTTOM_REQUIRE_BOTH_TFS (AND vs OR logic).
            if getattr(config, 'WT_BOTTOM_CROSS_GATE_ENABLED', False):
                _wtb_ind = indicator_cache.get(_esym, {})
                _wtb_is_long = _epk.endswith('_LONG')
                _wtb_base_tf = '3m' if mode == 'crypto' else '5m'
                _wtb_max_base = int(getattr(config, 'WT_BOTTOM_MAX_BARS_AGO_BASE', 5) or 0)
                _wtb_max_15m = int(getattr(config, 'WT_BOTTOM_MAX_BARS_AGO_15M', 0) or 0)
                _wtb_require_both = bool(getattr(config, 'WT_BOTTOM_REQUIRE_BOTH_TFS', False))
                _wtb_bars_base = int(_wtb_ind.get(f'wt_cross_bars_ago_{_wtb_base_tf}', 999) or 999)
                _wtb_rising_base = bool(_wtb_ind.get(f'wt_cross_rising_{_wtb_base_tf}', False))
                _wtb_bars_15m = int(_wtb_ind.get('wt_cross_bars_ago_15m', 999) or 999)
                _wtb_rising_15m = bool(_wtb_ind.get('wt_cross_rising_15m', False))
                _wtb_fresh_base = (
                    _wtb_max_base > 0 and _wtb_bars_base <= _wtb_max_base
                    and (_wtb_rising_base if _wtb_is_long else not _wtb_rising_base)
                )
                _wtb_fresh_15m = (
                    _wtb_max_15m > 0 and _wtb_bars_15m <= _wtb_max_15m
                    and (_wtb_rising_15m if _wtb_is_long else not _wtb_rising_15m)
                )
                if _wtb_max_base > 0 or _wtb_max_15m > 0:
                    if _wtb_require_both and _wtb_max_base > 0 and _wtb_max_15m > 0:
                        _wtb_pass = _wtb_fresh_base and _wtb_fresh_15m
                    else:
                        _wtb_pass = _wtb_fresh_base or _wtb_fresh_15m
                    if not _wtb_pass:
                        continue
            entry_pks.append(_epk)
        # WT_3M_FORCE_OPEN — mirror ez_manage.py:35343-35393 (2026-05-16 wire-up)
        # Without this, the WT_3M_FORCE_OPEN_GR_* knobs are dead in backtest (sweeping
        # GR_MIN_TFS / MIN_IND_PER_TF returns identical results across variants — the
        # gate code never executes). Mirrors live producer B (zero-position branch).
        if bool(getattr(config, 'WT_3M_FORCE_OPEN_ENABLED', True)) and all_position_keys:
            try:
                from golden_rule_htf import _ind_score as _wt3m_score
                _wt3m_size = float(getattr(config, 'WT_3M_FORCE_OPEN_SIZE_USD', 25.0))
                _wt3m_gate_on = bool(getattr(config, 'WT_3M_FORCE_OPEN_GR_GATE_ENABLED', True))
                _wt3m_vote_min = int(getattr(config, 'WT_3M_FORCE_OPEN_GR_VOTE_MIN', 15))
                _wt3m_min_tfs = int(getattr(config, 'WT_3M_FORCE_OPEN_GR_MIN_TFS', 0))
                _wt3m_min_ind = int(getattr(config, 'WT_3M_FORCE_OPEN_GR_MIN_IND_PER_TF', 5))
                _wt3m_tfs = ("3m", "15m", "1h", "4h", "D") if mode == 'crypto' else ("5m", "15m", "1h", "4h", "D")
                _wt3m_fired = 0
                _wt3m_req_hh = bool(getattr(config, 'WT_3M_FORCE_OPEN_REQUIRE_HH_CROSS', True))
                for _wf_pk in list(all_position_keys):
                    # Only fire for ZERO-position keys — mirrors live "zero-position branch"
                    _wf_pos_obj = trade_manager.positions.get(_wf_pk)
                    if _wf_pos_obj and abs(float(getattr(_wf_pos_obj, 'positionAmt', 0) or 0)) > 0.0001:
                        continue
                    _wf_is_long = _wf_pk.endswith('_LONG')
                    _wf_sym = _wf_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _wf_pk else _wf_pk.rsplit('_', 1)[0]
                    _wf_ind = indicator_cache.get(_wf_sym, {}) if isinstance(indicator_cache, dict) else {}
                    _wf_px = float(price_cache.get(_wf_sym, _wf_ind.get('current_price', 0)) or 0)
                    _wf_mode_crypto = (mode == 'crypto')
                    _wf_anchor_val = float(_wf_ind.get('sma_200_15m' if _wf_mode_crypto else 'ema_200_15m', 0) or 0)
                    _wf_wt1_ltf = float(_wf_ind.get('wt1_3m' if _wf_mode_crypto else 'wt1_5m', 0) or 0)
                    # 2026-07-01 PARITY FIX: mirror ez_manage.py:36656-36668 exactly. Prior reimpl was
                    # DEAD (wt1_ltf > wt1_ltf_prev with nonexistent _prev → x>x → always False, same bug
                    # live already fixed) + hardcoded ±1% (ignored SMA_PCT) + a wick filter live lacks.
                    # Live predicate = sma_200_15m ±SMA_PCT% distance AND wt1_3m vs wt2_3m cross. No wick.
                    _wf_wt2_ltf = float(_wf_ind.get('wt2_3m' if _wf_mode_crypto else 'wt2_5m', 0) or 0)
                    _wf_fo_pct = float(getattr(config, 'WT_3M_FORCE_OPEN_SMA_PCT', 1.0)) / 100.0
                    _wf_dist_ok = (_wf_is_long and _wf_anchor_val > 0 and _wf_px > _wf_anchor_val * (1.0 + _wf_fo_pct)) or ((not _wf_is_long) and _wf_anchor_val > 0 and _wf_px < _wf_anchor_val * (1.0 - _wf_fo_pct))
                    _wf_wt_dir_ok = (_wf_is_long and _wf_wt1_ltf > _wf_wt2_ltf) or ((not _wf_is_long) and _wf_wt1_ltf < _wf_wt2_ltf)
                    # 2026-07-04 USER: WT_3M reentry fires ONLY if the crossover PRICE is a higher-high
                    # (LONG) / lower-low (SHORT) vs the previous cross. True field from NPZ/live indicators.
                    _wf_xtf = '3m' if _wf_mode_crypto else '5m'
                    _wf_xv = float(_wf_ind.get(f'wt_crossover_value_{_wf_xtf}', 0) or 0)
                    _wf_xvp = float(_wf_ind.get(f'wt_crossover_value_{_wf_xtf}_prev', 0) or 0)
                    _wf_hh_ok = (not _wt3m_req_hh) or ((_wf_xv > 0 and _wf_xvp > 0) and ((_wf_is_long and _wf_xv > _wf_xvp) or ((not _wf_is_long) and _wf_xv < _wf_xvp)))
                    _wf_trig = _wf_dist_ok and _wf_wt_dir_ok and _wf_hh_ok
                    if not _wf_trig:
                        continue
                    if _wf_px <= 0:
                        continue
                    _wf_ok = True
                    if _wt3m_gate_on and (_wt3m_vote_min > 0 or _wt3m_min_tfs > 0):
                        try:
                            _wf_scores = [(_tf, _wt3m_score(_wf_ind, _tf, _wf_is_long, _wf_px)[0]) for _tf in _wt3m_tfs]
                            _wf_votes = sum(s for _, s in _wf_scores)
                            if _wt3m_vote_min > 0 and _wf_votes < _wt3m_vote_min:
                                _wf_ok = False
                            if _wf_ok and _wt3m_min_tfs > 0:
                                _wf_tfs_ok = sum(1 for _, s in _wf_scores if s >= _wt3m_min_ind)
                                if _wf_tfs_ok < _wt3m_min_tfs:
                                    _wf_ok = False
                        except Exception:
                            _wf_ok = False
                    if not _wf_ok:
                        continue
                    _wf_qty = _wt3m_size / _wf_px
                    _wf_reason = f"WT_3M_FORCE_OPEN_{'LONG' if _wf_is_long else 'SHORT'}_wt1={_wf_wt1_ltf:.1f}_wt2={_wf_wt2_ltf:.1f}"
                    try:
                        await trade_manager.execute_trade_action(
                            account_key=account_key, position_key=_wf_pk, symbol=_wf_sym,
                            quantity=_wf_qty, current_price=_wf_px,
                            side='BUY' if _wf_is_long else 'SELL',
                            position_side='LONG' if _wf_is_long else 'SHORT',
                            action='OPEN', reason=_wf_reason, is_full_close=False, is_hedge=False)
                        _wt3m_fired += 1
                    except Exception as _wf_err:
                        if step < 10:
                            v8_logger.error(f"[V8_WT3M_FORCE_OPEN_ERR] {_wf_pk}: {_wf_err}")
                if step < 5 and _wt3m_fired > 0:
                    v8_logger.info(f"[V8_WT3M_FORCE_OPEN] step={step} fired={_wt3m_fired} gate_on={_wt3m_gate_on} vote_min={_wt3m_vote_min} tfs={_wt3m_min_tfs}×{_wt3m_min_ind}")
            except Exception as _wt3m_outer:
                if step < 10:
                    v8_logger.error(f"[V8_WT3M_FORCE_OPEN_OUTER_ERR] {_wt3m_outer}")
        if step < 3:
            v8_logger.info(f"[DBG] entry_pks={len(entry_pks)} all_position_keys={len(all_position_keys)} positions={len(trade_manager.positions)} gate_filtered={_gate_filtered}")
        # VEC_STRATEGY_GATES — P0-B wiring (2026-05-14)
        # Mirrors live ez_manage.py:13467-13526 VEC_STRATEGY_GATES block.
        # Only enforces (blocks entries) when V8_USE_VEC_STRATEGY_GATES=1 (or V8_USE_VEC_ALL=1)
        # AND VEC_GATES_LOG_ONLY=False. Default LOG_ONLY=True → shadow evaluation only, no blocking.
        _vsg_env = os.environ.get("V8_USE_VEC_STRATEGY_GATES", "1" if os.environ.get("V8_USE_VEC_ALL") == "1" else "0")
        if _vsg_env == "1" and V8_VEC_PARITY_AVAILABLE and not bool(getattr(config, 'VEC_GATES_LOG_ONLY', True)):
            _vsg_filtered = []
            for _vsg_pk in entry_pks:
                _vsg_pos = trade_manager.positions.get(_vsg_pk)
                _vsg_sym = (getattr(_vsg_pos, 'symbol', '') if _vsg_pos else '') or (_vsg_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _vsg_pk else _vsg_pk.rsplit('_', 1)[0])
                _vsg_side = 'LONG' if _vsg_pk.endswith('_LONG') else 'SHORT'
                _vsg_ind = indicator_cache.get(_vsg_sym, {})
                _vsg_acct = account_key.split(':')[0] if ':' in account_key else account_key
                try:
                    _vsg_blocked, _vsg_code, _ = evaluate_vec_strategy_gates_parity_core(
                        account=_vsg_acct,
                        symbol=_vsg_sym,
                        side=_vsg_side,
                        action='OPEN',
                        is_hedge=False,
                        is_full_close=False,
                        ind_data=_vsg_ind,
                        config=config,
                    )
                    if _vsg_blocked:
                        if step % 500 == 0:
                            v8_logger.debug(f"[VEC_STRATEGY_GATE_BLOCKED] {_vsg_pk}: {_vsg_code}")
                        continue
                except Exception:
                    pass
                _vsg_filtered.append(_vsg_pk)
            entry_pks = _vsg_filtered
        # RATIO gate check — skip imbalanced entries (P3-#1 extends P2-F)
        if os.environ.get("V8_USE_LS_RATIO_GATE", "0") == "1":
            _ls_blocked = getattr(trade_manager, '_ls_ratio_block', {}).get(account_key)
            if _ls_blocked:
                entry_pks = [pk for pk in entry_pks if not (
                    (_ls_blocked == 'LONG' and pk.endswith('_LONG')) or
                    (_ls_blocked == 'SHORT' and pk.endswith('_SHORT'))
                )]
        # ENTRY: HAIKU_ENTRY_GATE — block overbought LONG entries (K_15m>85) and oversold SHORT entries (K_15m<15).
        # Deterministic subset of HaikuOverseer.build_judgement_prompt() rules #1-2 — no API call needed.
        # Gated by V8_USE_VEC_ALL=1 + HAIKU_ENTRY_GATE_ENABLED (default False).
        if V8_VEC_PARITY_AVAILABLE and os.environ.get("V8_USE_VEC_ALL", "0") == "1" and \
                bool(getattr(config, 'HAIKU_ENTRY_GATE_ENABLED', False)) and entry_pks:
            try:
                from vec_paths.haiku_winner import check_haiku_entry_gate as _heg_check
                _heg_max_k = float(getattr(config, 'HAIKU_ENTRY_GATE_LONG_MAX_K', 85.0))
                _heg_min_k = float(getattr(config, 'HAIKU_ENTRY_GATE_SHORT_MIN_K', 15.0))
                _heg_filtered = []
                for _heg_pk in entry_pks:
                    _heg_side = 'LONG' if _heg_pk.endswith('_LONG') else 'SHORT'
                    _heg_sym = _heg_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _heg_pk else (_heg_pk[:-5] if _heg_pk.endswith('_LONG') else _heg_pk[:-6])
                    _heg_ind = indicator_cache.get(_heg_sym, {}) if isinstance(indicator_cache, dict) else {}
                    if _heg_check(_heg_ind, _heg_side, mode, config):
                        _heg_k = float(_heg_ind.get('k_15m', 50) or 50)
                        v8_logger.debug(f"[HAIKU_ENTRY_GATE_BLOCK] {_heg_pk}: K_15m={_heg_k:.1f} blocked")
                    else:
                        _heg_filtered.append(_heg_pk)
                entry_pks = _heg_filtered
            except Exception:
                pass

        # ENTRY: BB_RECOVERY_ENTRY — failed BB breakout/breakdown entry signal (P2-A)
        # Active when V8_USE_VEC_ALL=1 and BB_RECOVERY_ENTRY_ENABLED[_TRADIER]=True (default False).
        if os.environ.get("V8_USE_VEC_ALL", "0") == "1":
            _bbr_enabled = getattr(config, 'BB_RECOVERY_ENTRY_ENABLED_TRADIER' if mode == 'tradier' else 'BB_RECOVERY_ENTRY_ENABLED', False)
            if _bbr_enabled:
                try:
                    from vec_paths.bb_recovery_entry import check_bb_recovery_entry as _bbr_check
                    _bbr_result = _bbr_check(None, step, mode, config)
                    if _bbr_result:
                        _bbr_side = _bbr_result['side']
                        _bbr_sym = symbol if 'symbol' in dir() else account_key.split(':')[-1] if ':' in account_key else ''
                        if _bbr_sym:
                            _bbr_px = price_cache.get(_bbr_sym, 0)
                            _bbr_pk = f"{account_key}:{_bbr_sym}_{_bbr_side}" if ':' not in _bbr_sym else f"{_bbr_sym}_{_bbr_side}"
                            _bbr_qty = float(getattr(config, 'START_POSITION_SIZE', 1.0))
                            if _bbr_px > 0 and _bbr_qty > 0:
                                await trade_manager.execute_trade_action(account_key=account_key, position_key=_bbr_pk, symbol=_bbr_sym, quantity=_bbr_qty, current_price=_bbr_px, side='BUY' if _bbr_side == 'LONG' else 'SELL', position_side=_bbr_side, action='OPEN', reason=_bbr_result['reason'], is_full_close=False, is_hedge=False)
                except Exception:
                    pass
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

        # WT_15M_BOUNCE_OPEN — initial OPEN on fresh 15m WT cross within BB + 4h/1h in favor.
        # ROOT-CAUSE FIX (2026-05-14): 15m bounce is REENTRY-only in live code (B02/B04 in epq).
        # This backtest path validates the signal before unlocking ez_manage.py for live deployment.
        # Signal fires when ALL of:
        #   (1) wt1_15m recently crossed (wt_cross_bars_ago_15m <= max_bars) in trade direction
        #   (2) price within Bollinger Bands (bb_pct_b_15m in [BB_MIN, BB_MAX])
        #   (3) 4h OR 1h WT state still in favor (wt_cross_rising_4h / _1h for LONG; inverted for SHORT)
        # Config (all via V8_OVERRIDE_FILE, default OFF):
        #   WT_15M_BOUNCE_OPEN_ENABLED, WT_15M_BOUNCE_MAX_BARS_AGO (default 2),
        #   WT_15M_BOUNCE_BB_MIN/MAX (0.05/0.95), WT_15M_BOUNCE_REQUIRE_BOTH_HTF (False=OR).
        if getattr(config, 'WT_15M_BOUNCE_OPEN_ENABLED', False):
            _b15_max_bars = int(getattr(config, 'WT_15M_BOUNCE_MAX_BARS_AGO', 2))
            _b15_bb_min = float(getattr(config, 'WT_15M_BOUNCE_BB_MIN', 0.05))
            _b15_bb_max = float(getattr(config, 'WT_15M_BOUNCE_BB_MAX', 0.95))
            _b15_req_both_htf = bool(getattr(config, 'WT_15M_BOUNCE_REQUIRE_BOTH_HTF', False))
            for _b15_sym in list(stores.keys()):
                _b15_ind = indicator_cache.get(_b15_sym, {})
                _b15_bars_15m = int(_b15_ind.get('wt_cross_bars_ago_15m', 999) or 999)
                if _b15_bars_15m > _b15_max_bars:
                    continue
                _b15_bb = float(_b15_ind.get('bb_pct_b_15m', 0.5) or 0.5)
                if not (_b15_bb_min <= _b15_bb <= _b15_bb_max):
                    continue
                _b15_rising_15m = bool(_b15_ind.get('wt_cross_rising_15m', False))
                _b15_rising_4h = bool(_b15_ind.get('wt_cross_rising_4h', False))
                _b15_rising_1h = bool(_b15_ind.get('wt_cross_rising_1h', False))
                _b15_price = float(price_cache.get(_b15_sym, 0) or 0)
                if _b15_price <= 0:
                    continue
                for _b15_is_long in (True, False):
                    _b15_pk = f"{account_key}:{_b15_sym}_{'LONG' if _b15_is_long else 'SHORT'}"
                    _b15_pos = trade_manager.positions.get(_b15_pk)
                    if _b15_pos and abs(getattr(_b15_pos, 'positionAmt', 0)) >= 0.001:
                        continue
                    if _b15_is_long:
                        if not _b15_rising_15m:
                            continue
                        _b15_htf_ok = (_b15_rising_4h and _b15_rising_1h) if _b15_req_both_htf \
                            else (_b15_rising_4h or _b15_rising_1h)
                    else:
                        if _b15_rising_15m:
                            continue
                        _b15_htf_ok = (not _b15_rising_4h and not _b15_rising_1h) if _b15_req_both_htf \
                            else (not _b15_rising_4h or not _b15_rising_1h)
                    if not _b15_htf_ok:
                        continue
                    _b15_qty = float(getattr(config, 'START_POSITION_SIZE', 1.0)) / _b15_price
                    if _b15_qty <= 0:
                        continue
                    try:
                        await trade_manager.execute_trade_action(
                            account_key=account_key, position_key=_b15_pk, symbol=_b15_sym,
                            quantity=_b15_qty, current_price=_b15_price,
                            side='BUY' if _b15_is_long else 'SELL',
                            position_side='LONG' if _b15_is_long else 'SHORT',
                            action='OPEN',
                            reason=f'WT_15M_BOUNCE_OPEN_bars={_b15_bars_15m}_bb={_b15_bb:.2f}',
                            is_full_close=False, is_hedge=False)
                    except Exception:
                        pass

        # GOLDEN RULE ENFORCEMENT (real backtest) — 2026-05-06
        # Mirrors live _golden_rule_loop in ez_manage.py (ang/inf always-long mandate).
        # For every symbol: wt1_3m > wt2_3m AND (price > dc_high_15m OR price > bb_upper_15m)
        # → must hold a LONG of base_usd×mult. Cascade: 1.5x at 1h, 2x at 4h, 3x at D.
        # Calls execute_trade_action directly — bypasses signal gate (fires every qualifying bar).
        # Gate: GOLDEN_RULE_ENABLED (default True).
        # BUG FIX 2026-05-09: tm_mod is tradier-only; crypto path uses ez_manage directly.
        if getattr(ez_manage.config, 'GOLDEN_RULE_ENABLED', True) and os.environ.get("V8_PARITY_MODE") != "1":
            _gr_base_usd = float(getattr(ez_manage.config, 'GOLDEN_RULE_BASE_USD', 5.0))
            _gr_dc_15 = bool(getattr(ez_manage.config, 'GOLDEN_RULE_DC_15M_ENABLED', True))
            _gr_bb_15 = bool(getattr(ez_manage.config, 'GOLDEN_RULE_BB_15M_ENABLED', True))
            _gr_dc_1h = bool(getattr(ez_manage.config, 'GOLDEN_RULE_DC_1H_ENABLED', True))
            _gr_bb_1h = bool(getattr(ez_manage.config, 'GOLDEN_RULE_BB_1H_ENABLED', True))
            _gr_dc_4h = bool(getattr(ez_manage.config, 'GOLDEN_RULE_DC_4H_ENABLED', True))
            _gr_bb_4h = bool(getattr(ez_manage.config, 'GOLDEN_RULE_BB_4H_ENABLED', True))
            _gr_dc_D = bool(getattr(ez_manage.config, 'GOLDEN_RULE_DC_D_ENABLED', True))
            _gr_bb_D = bool(getattr(ez_manage.config, 'GOLDEN_RULE_BB_D_ENABLED', True))
            _gr_m15 = float(getattr(ez_manage.config, 'GOLDEN_RULE_MULT_15M', 1.0))
            _gr_m1h = float(getattr(ez_manage.config, 'GOLDEN_RULE_MULT_1H', 1.5))
            _gr_m4h = float(getattr(ez_manage.config, 'GOLDEN_RULE_MULT_4H', 2.0))
            _gr_mD = float(getattr(ez_manage.config, 'GOLDEN_RULE_MULT_D', 3.0))
            for _gr_sym in list(stores.keys()):
                _gr_ind = indicator_cache.get(_gr_sym, {})
                # FIX 2026-05-08: tradier NPZ has no 'current_price' field → price was always 0 → rule never fired.
                _gr_p = float(_gr_ind.get('close_5m', _gr_ind.get('close', _gr_ind.get('current_price', 0))) or 0)
                if _gr_p <= 0:
                    continue
                # FIX 2026-05-08: tradier base TF is 5m not 3m — wt1_3m=0 in tradier NPZ → rule never fired.
                _gr_wt1 = float(_gr_ind.get('wt1_5m', _gr_ind.get('wt1_3m', 0)) or 0)
                _gr_wt2 = float(_gr_ind.get('wt2_5m', _gr_ind.get('wt2_3m', 0)) or 0)
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
                    # ═══ 2026-05-12 USER MANDATE: GOLDEN_RULE IS A SIGNAL NOT A FILTER ═══
                    # GR must GENERATE MORE entries, not block them. Two trigger paths now fire
                    # entries (UNION, not intersection):
                    #   1) DC/BB break path (legacy) — fires when _gr_dc15_ok OR _gr_bb15_ok
                    #   2) GR_TOTAL_VOTE_SCORE_MIN signal — fires when sum-across-TFs of
                    #      indicators-agreeing >= threshold. Adds entries the breakout path missed.
                    # No filtering: if neither triggers, we skip; if either triggers, we enter.
                    _vote_signal_fire = False
                    try:
                        _vote_min = int(getattr(ez_manage.config, 'GR_TOTAL_VOTE_SCORE_MIN', 0) or 0)
                        if _vote_min > 0:
                            from golden_rule_htf import score_entry_htf as _gr_score_entry_x
                            _gr_mode_x = "tradier" if mode == "tradier" else "crypto"
                            _gr_vote_min_tfs = int(getattr(ez_manage.config, 'GOLDEN_RULE_HTF_MIN_TFS', 1))
                            _gr_vote_min_ind = int(getattr(ez_manage.config, 'GOLDEN_RULE_MIN_IND', 1))
                            # vote-sum mode: score_entry_htf internally uses GR_TOTAL_VOTE_SCORE_MIN threshold
                            _vote_passes, _vote_n, _ = _gr_score_entry_x(_gr_ind, _gr_is_long, _gr_mode_x, _gr_vote_min_tfs, _gr_vote_min_ind, _gr_p)
                            _vote_signal_fire = bool(_vote_passes)
                    except Exception as _gr_vote_err:
                        if step < 5:
                            v8_logger.warning(f'[GR_VOTE_SIGNAL] {_gr_pk}: {_gr_vote_err}')
                    # SIGNAL UNION: skip ONLY when neither path triggers
                    if not (_gr_dc15_ok or _gr_bb15_ok or _vote_signal_fire):
                        continue
                    # Tag the entry reason so we can distinguish vote-signal vs breakout origin
                    _gr_signal_origin = "VOTE" if (_vote_signal_fire and not (_gr_dc15_ok or _gr_bb15_ok)) else ("BREAKOUT" if (_gr_dc15_ok or _gr_bb15_ok) else "BOTH")
                    if bool(getattr(ez_manage.config, 'GOLDEN_RULE_HTF_VETO_ENABLED', False)):
                        _gr_w1_D_v = float(_gr_ind.get('wt1_D', 0) or 0)
                        _gr_w2_D_v = float(_gr_ind.get('wt2_D', 0) or 0)
                        if _gr_w1_D_v != 0 or _gr_w2_D_v != 0:
                            if _gr_is_long and _gr_w1_D_v < _gr_w2_D_v:
                                continue
                            if (not _gr_is_long) and _gr_w1_D_v > _gr_w2_D_v:
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
                    # FIX 2026-05-08: tradier stocks require integer shares — $5 base → 0.018 AAPL → rounds to 0 → never fires.
                    # For tradier: use integer lot (minimum 1 share). base_usd in config is crypto-sized; override in sweep.
                    _gr_target_qty_int = max(1.0, round(_gr_target_qty))
                    _gr_target_usd_int = _gr_target_qty_int * _gr_p
                    _gr_pos = trade_manager.positions.get(_gr_pk)
                    _gr_cur_amt = abs(float(getattr(_gr_pos, 'positionAmt', 0))) if _gr_pos else 0.0
                    if _gr_cur_amt > 0 and _gr_cur_amt * _gr_p >= _gr_target_usd_int * 0.8:
                        continue
                    _gr_qty = _gr_target_qty_int - _gr_cur_amt
                    if _gr_qty < 0.5:
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

        # Drain async tasks (order queue, cooldown writes, etc).
        # NO-LIES 2026-05-12: replaced `await asyncio.sleep(0)` (one-tick yield,
        # non-deterministic ordering) with deterministic FIFO drain. See
        # _v8_det_create_task / drain_pending_v8_tasks() for design.
        await drain_pending_v8_tasks()
        await asyncio.sleep(0)  # one extra tick for the OrderQueue.process_orders long-running task

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
            # 2026-05-11 ROUND-TRIP WR FIX: denominator is n_wins+n_losses (round trips),
            # NOT n_closes (which counts every partial REDUCE event including PARTIAL_PROFIT_LOCK fires).
            _wr = _live_pnl['n_wins'] * 100.0 / max(1, _live_pnl['n_wins'] + _live_pnl['n_losses'])
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
            _live_dd = abs(_v8ns_dd_state.get('min_dd', 0.0))
            print(f"V8_RESULT_LIVE: pool_sharpe={_sharpe_pt:.3f} gain_pct={_gain_pct:.2f} closes={_live_pnl['n_closes']} wins={_live_pnl['n_wins']} losses={_live_pnl['n_losses']} wr={_wr:.1f} dd={_live_dd:.2f} step={step} total_steps={len(sorted_ts)}", flush=True)
            # Live PnL breakdown by close reason — every report interval
            v8_logger.info("[V8_PNL_BREAKDOWN] === BY CLOSE REASON ===")
            for _r, _d in sorted(_live_pnl["by_reason"].items(), key=lambda x: x[1]["pnl_pct_sum"]):
                v8_logger.info(f"[V8_PNL_BREAKDOWN] {_r:<25} n={_d['n']:>4} sum={_d['pnl_pct_sum']:+8.2f}% W={_d['n_wins']} L={_d['n_losses']} worst={_d['worst_loss']:.2f}% best={_d['best_win']:.2f}%")

    # Results — compute and print BEFORE queue cleanup so cancellation errors cannot block result
    elapsed = _real_time_module.time() - t0
    v8_logger.info(f"Done in {elapsed:.1f}s | {len(executed_trades)} trades")
    # 2026-05-12 — Shadow validator summary (no-op unless V8_VEC_SHADOW_VALIDATE=1)
    _shadow_sum = _v8_shadow_summary()
    if _shadow_sum:
        v8_logger.warning(f"[V8_VEC_SHADOW] {_shadow_sum} (log: {_V8_SHADOW_DIVERGENCE_PATH})")
    # 2026-05-12 — Vec short-circuit stats (only emit when any vec flag is on).
    if V8_VEC_PARITY_AVAILABLE and (
        V8_USE_VEC_STALE_MARK or V8_USE_VEC_EMERGENCY_BRAKE or V8_USE_VEC_COOLDOWN_LOCKS
        or V8_USE_VEC_OPEN_INTENT_SIZE or V8_USE_VEC_NOLOSS_GATE or V8_USE_VEC_AUGMENT_GATE
        or V8_USE_VEC_PROTECT_BALANCE or V8_USE_VEC_CIRCUIT_SHARPE
        or V8_USE_VEC_TRADEABLE_STATE or V8_USE_VEC_QUARANTINE_STRATEGY
        or V8_USE_VEC_NEWBORN_PROTECT or V8_USE_VEC_HEDGE_SCAN_GATES
    ):
        _vs = _V8_VEC_STATS
        print(
            f"V8_VEC_STATS: noloss={_vs['noloss_blocks']}/{_vs['noloss_calls']} "
            f"augment={_vs['augment_blocks']}/{_vs['augment_calls']} "
            f"brake={_vs['brake_blocks']}/{_vs['brake_calls']} "
            f"stale={_vs['stale_blocks']}/{_vs['stale_calls']} "
            f"cooldown={_vs['cooldown_blocks']}/{_vs['cooldown_calls']} "
            f"open_intent={_vs['open_intent_blocks']}/{_vs['open_intent_calls']} "
            f"protect_balance={_vs['protect_balance_blocks']}/{_vs['protect_balance_calls']} "
            f"circuit_sharpe={_vs['circuit_sharpe_blocks']}/{_vs['circuit_sharpe_calls']} "
            f"tradeable_state={_vs['tradeable_state_blocks']}/{_vs['tradeable_state_calls']} "
            f"quarantine={_vs['quarantine_blocks']}/{_vs['quarantine_calls']} "
            f"hedge_scan={_vs['hedge_scan_blocks']}/{_vs['hedge_scan_calls']}",
            flush=True,
        )
    # ═══ NO-LIES RULE #2 — MARK-TO-MARKET OPEN POSITIONS AT FINAL BAR ═══
    # CLAUDE.md: "Open losing positions MUST be marked-to-market at the final bar
    # and appended to the return distribution BEFORE computing Sharpe. Skipping
    # this is fraudulent — it hides losses behind held positions and was the
    # proximate cause of the live blow-up." (Discovered 2026-05-10: crypto Tier-1
    # showed 100% WR / pool_sharpe=1.16 across all variants because residual open
    # positions never got their unrealized losses appended to all_pnl_pcts.)
    _mtm_count = 0
    _mtm_wins = 0
    _mtm_losses = 0
    _mtm_sum_pct = 0.0
    _mtm_final_ts = int(_sim_ts[0]) if '_sim_ts' in dir() and _sim_ts else 0
    for _pk, _pos in list(trade_manager.positions.items()):
        _amt = float(getattr(_pos, 'positionAmt', 0) or 0)
        if abs(_amt) < 0.0001:
            continue
        _entry = float(getattr(_pos, 'entry_price', 0) or 0)
        _mark = float(getattr(_pos, 'mark_price', 0) or 0)
        if _entry <= 0 or _mark <= 0:
            continue
        _is_long = _amt > 0
        _mtm_pnl_pct_gross = ((_mark - _entry) / _entry * 100.0) if _is_long else ((_entry - _mark) / _entry * 100.0)
        _mtm_pnl_dollars_gross = ((_mark - _entry) * abs(_amt)) if _is_long else ((_entry - _mark) * abs(_amt))
        _sym_for_mtm = _pk.split(':', 1)[1].rsplit('_', 1)[0] if ':' in _pk else _pk.rsplit('_', 1)[0]
        _mtm_rt_cost = _round_trip_cost_for_sym(_sym_for_mtm)
        _mtm_pnl_pct = _mtm_pnl_pct_gross - _mtm_rt_cost
        _mtm_pnl_dollars = _mtm_pnl_dollars_gross - (_mtm_rt_cost / 100.0) * (_entry * abs(_amt))
        _live_pnl["all_pnl_pcts"].append(_mtm_pnl_pct)
        _live_pnl["all_pnl_dollars"].append(_mtm_pnl_dollars) if "all_pnl_dollars" in _live_pnl else None
        _live_pnl["running_pnl_pct"] += _mtm_pnl_pct
        _live_pnl["n_closes"] += 1
        if _mtm_pnl_pct > 0:
            _live_pnl["n_wins"] += 1
            _mtm_wins += 1
        else:
            _live_pnl["n_losses"] += 1
            _mtm_losses += 1
        executed_trades.append({
            "timestamp": _mtm_final_ts,
            "symbol": _sym_for_mtm,
            "position_key": _pk,
            "side": "SELL" if _is_long else "BUY",
            "position_side": "LONG" if _is_long else "SHORT",
            "price": _mark,
            "quantity": abs(_amt),
            "action": "MTM_FINAL_BAR_NOLIES_RULE2",
            "reason": "MTM_FINAL_BAR_NOLIES_RULE2",
            "pnl_pct": _mtm_pnl_pct,
            "pnl_pct_gross": _mtm_pnl_pct_gross,
            "round_trip_cost_pct": _mtm_rt_cost,
            "pnl_dollars": _mtm_pnl_dollars,
            "is_full_close": True,
        })
        _mtm_count += 1
        _mtm_sum_pct += _mtm_pnl_pct
    if _mtm_count > 0:
        v8_logger.info(f"[NOLIES_MTM] Marked-to-market {_mtm_count} open positions at final bar | wins={_mtm_wins} losses={_mtm_losses} sum_pnl_pct={_mtm_sum_pct:+.2f}% (CLAUDE.md NO-LIES rule #2)")
        print(f"V8_MTM_FINAL: count={_mtm_count} wins={_mtm_wins} losses={_mtm_losses} sum_pnl_pct={_mtm_sum_pct:.2f}", flush=True)
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

    # 2026-05-09 live-vs-sandbox parity audit: dump RAW per-event executed_trades
    # (every OPEN / AUGMENT / REDUCE / CLOSE) to a JSONL file when
    # V8_RAW_EVENTS_FILE is set. Schema: one event dict per line, fields preserved
    # as the engine recorded them. Used by tools/live_v8_parity_diff.py to
    # compare event-by-event against data/history/<acct>/SYM_SIDE.jsonl.
    _raw_events_path = os.environ.get("V8_RAW_EVENTS_FILE", "")
    if _raw_events_path:
        try:
            with open(_raw_events_path, "w") as _ef:
                for _ev in executed_trades:
                    _ef.write(json.dumps(_ev, default=str) + "\n")
            print(f"V8_RAW_EVENTS: wrote {len(executed_trades)} events to {_raw_events_path}", flush=True)
        except Exception as _re:
            print(f"V8_RAW_EVENTS_ERR: {_re}", flush=True)

    # 2026-05-12 — V8_DECISION_ONLY: close decision-log file handles + print counter summary.
    if V8_DECISION_ONLY:
        try: _v8_decision_close_files()
        except Exception: pass
        _doc = _V8_DECISION_COUNTERS
        print(f"V8_DECISION_ONLY_SUMMARY: opens={_doc['opens']} augments={_doc['augments']} reduces={_doc['reduces']} closes={_doc['closes']} hedges={_doc['hedges']} doubleopen_reclass={_doc['doubleopen_reclass']} ung_blocks={_doc['blocks']} out_dir={V8_DECISION_OUT_DIR}", flush=True)

    # Now cancel queue processor.
    # 2026-05-09 fix: in Python 3.11+, asyncio.CancelledError inherits from
    # BaseException (NOT Exception), so `except Exception:` does NOT catch it.
    # The bare CancelledError leaked up through asyncio.run() in main(), and
    # Python exited rc=1 even though V8_RESULT had already been emitted.
    # The sweep harness saw rc=1 and stamped reason="rc=1" on every variant
    # row in the CSV. Catching BaseException here keeps the engine's rc=0
    # and yields clean canonical CSV rows.
    queue_task.cancel()
    try:
        await queue_task
    except BaseException:
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
    parser.add_argument("--seed-positions", type=str, default="", help="Path to snapshot JSON from tools/snapshot_live_state.py — seed open positions/hedges/exit_candidates instead of clean-slate")
    parser.add_argument(
        "--research-top-exit-spec",
        type=str,
        default="",
        help=(
            "BACKTEST ONLY: replay a frozen VEC_RESEARCH E02/E10/E11 event "
            "schedule. Default-off; never read by live processes."
        ),
    )
    parser.add_argument(
        "--research-ladder-spec",
        type=str,
        default="",
        help=(
            "BACKTEST ONLY: replay one frozen VEC_RESEARCH band-ladder "
            "validation schedule. Default-off; never read by live processes."
        ),
    )
    args = parser.parse_args()
    if args.seed_positions:
        os.environ["V8_SEED_POSITIONS_FILE"] = args.seed_positions
    if args.research_top_exit_spec:
        os.environ["V8_RESEARCH_TOP_EXIT_SPEC"] = args.research_top_exit_spec
    if args.research_ladder_spec:
        os.environ["V8_RESEARCH_LADDER_SPEC"] = args.research_ladder_spec

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    if _SWEEP_MODE:
        print(f"V8_INIT_HEARTBEAT: loading n_symbols={len(symbols) if symbols else 'all'}", flush=True)
    stores, resolution = load_stores(args.mode, symbols, args.start, npz_dir_override=args.npz_dir)
    if not stores:
        v8_logger.error("No data loaded")
        return
    if _SWEEP_MODE:
        print(f"V8_INIT_HEARTBEAT: loaded={len(stores)} symbols starting simulation", flush=True)

    # 2026-05-09 fix: wrap asyncio.run() to swallow CancelledError that may
    # propagate up from queue_task.cancel() in run_simulation (Python 3.11+
    # CancelledError is BaseException, not Exception). The V8_RESULT line has
    # already been emitted before that point, so a clean rc=0 exit is correct.
    try:
        if args.mode == "tradier":
            asyncio.run(run_simulation_tradier(args.account, args.start, args.capital, stores, resolution))
        else:
            asyncio.run(run_simulation(args.mode, args.account, args.start, args.capital, stores, resolution))
    except asyncio.CancelledError:
        pass


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
        # 2026-05-17 A3 #6 HTF_MIN_TFS alias-mirror: phase4 sweeps emitted the short
        # name (`HTF_MIN_TFS`) which nothing reads — engine reads `GOLDEN_RULE_HTF_MIN_TFS`.
        # Mirror short→canonical BEFORE the generic setattr loop so the canonical
        # attribute is applied through the same 4-level path (instance/class/dataclass).
        # Inert when the alias is not in the override JSON; never overwrites an
        # explicit canonical value already set in the same override.
        _HTF_ALIASES = {
            "HTF_MIN_TFS": "GOLDEN_RULE_HTF_MIN_TFS",
            "MIN_IND": "GOLDEN_RULE_MIN_IND",
        }
        for _src, _dst in _HTF_ALIASES.items():
            if _src in _t_overrides and _dst not in _t_overrides:
                _t_overrides[_dst] = _t_overrides[_src]
                v8_logger.info(f"[V8_HTF_ALIAS] {_src}={_t_overrides[_src]} → {_dst} mirrored (phase4 short-name alias)")
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
    # 2026-05-16 A3 fix #5 (HTF_DIRECTION_GATE_ENABLED) + bundled tradier→crypto-config bridge.
    # ez_positions_quick.config (the CRYPTO Config instance) is what _EPQ paths read at runtime;
    # sweep overrides land on tm_mod.config (TradierConfig). Adding these keys to the cross-injector
    # makes HTF_DIRECTION_GATE_ENABLED, MFI_ENTRY_ENABLED, TRADIER_ENTRY_SCORE_THRESHOLD,
    # GR_HTF_GATE_ENABLED responsive to tradier sweep overrides. All gates default-OFF behind their
    # _ENABLED/threshold values; mirroring is inert until sweep explicitly sets them.
    _EPQ_CROSS_KEYS = {
        "STRUCTURAL_RANGE_SHIFT_EXIT", "STRUCTURAL_RANGE_SHIFT_TF",
        "STRUCTURAL_RANGE_SHIFT_K_HIGH", "STRUCTURAL_RANGE_SHIFT_K_LOW",
        "STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS", "SATOSHIT_ENTRY_FILTER",
        # A3 #5: HTF_DIRECTION_GATE family — live read at ez_positions_quick.py:12242
        "HTF_DIRECTION_GATE_ENABLED",
        "HTF_GATE_APPLY_TO_OPEN", "HTF_GATE_APPLY_TO_AUGMENT",
        "HTF_GATE_BYPASS_RZ", "HTF_GATE_REQUIRED_TFS",
        # A3 #2 (GR_HTF_GATE) + #7 (MFI) + #10 (ENTRY_SCORE): mirror to both configs so
        # engine gate inserts below can read from either crypto or tradier instance.
        "GR_HTF_GATE_ENABLED", "GR_HTF_REQUIRE_BULL", "GR_HTF_REQUIRE_BEAR",
        "MFI_ENTRY_ENABLED", "MFI_LONG_THRESHOLD_D",
        "TRADIER_ENTRY_SCORE_THRESHOLD", "ENTRY_SCORE_THRESHOLD",
    }
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
    # BUG FIX 2026-05-08: sweep sends keys WITHOUT "TRADIER_" prefix (e.g. "WT_EXIT_MIN_TFS_TRADIER")
    # but original pairs only checked "TRADIER_WT_EXIT_MIN_TFS_TRADIER" → veto gates NEVER enabled →
    # all WT_EXIT/K_ZONE/DC variants produced identical results. Added non-prefixed aliases.
    _veto_pairs = [
        ("TRADIER_MI_EXIT_ENABLED_TRADIER", "MI_EXIT_VETO_ENABLED_TRADIER"),
        ("MI_EXIT_ENABLED_TRADIER", "MI_EXIT_VETO_ENABLED_TRADIER"),
        ("TRADIER_WT_EXIT_TFS_TRADIER", "WT_EXIT_VETO_ENABLED_TRADIER"),
        ("WT_EXIT_TFS_TRADIER", "WT_EXIT_VETO_ENABLED_TRADIER"),
        ("TRADIER_WT_EXIT_MIN_TFS_TRADIER", "WT_EXIT_VETO_ENABLED_TRADIER"),
        ("WT_EXIT_MIN_TFS_TRADIER", "WT_EXIT_VETO_ENABLED_TRADIER"),
        ("TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER", "WT_COMPOSITE_VETO_ENABLED_TRADIER"),
        ("WT_COMPOSITE_SCORING_ENABLED_TRADIER", "WT_COMPOSITE_VETO_ENABLED_TRADIER"),
        # SENTINEL_FIX 2026-04-14 DEAD_PARAMS T4: K_ZONE thresholds were dead because
        # K_ZONE_VETO_ENABLED_TRADIER was never set True (the veto gate at process_position
        # line 1327 requires it). Enable it when sweep varies K_ZONE thresholds.
        ("TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER", "K_ZONE_VETO_ENABLED_TRADIER"),
        ("K_ZONE_LONG_THRESHOLD_TRADIER", "K_ZONE_VETO_ENABLED_TRADIER"),
        ("TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER", "K_ZONE_VETO_ENABLED_TRADIER"),
        ("K_ZONE_SHORT_THRESHOLD_TRADIER", "K_ZONE_VETO_ENABLED_TRADIER"),
        # DC_POSITION_ENTRY_THRESHOLD was only a +5 score bonus, never gated entries.
        # DC_ENTRY_VETO_ENABLED_TRADIER enables the new dc_pos zone gate in process_position.
        ("TRADIER_DC_POSITION_ENTRY_THRESHOLD", "DC_ENTRY_VETO_ENABLED_TRADIER"),
        ("DC_POSITION_ENTRY_THRESHOLD", "DC_ENTRY_VETO_ENABLED_TRADIER"),
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
    # FIX 2026-05-08: DELTA_ENTRY_ENABLED defaults False (sweep proof). But when sweep explicitly
    # tests DELTA_ENGINE_ENABLED=True or DELTA_HTF_GATE variants, delta entries must be enabled or
    # the HTF gate can never filter anything → identical results. Auto-enable unless sweep
    # explicitly tests DELTA_ENTRY_ENABLED=False.
    if (_t_overrides.get("DELTA_ENGINE_ENABLED") is True or _t_overrides.get("DELTA_HTF_GATE") is not None) and "DELTA_ENTRY_ENABLED" not in _t_overrides:
        setattr(tm_mod.config, "DELTA_ENTRY_ENABLED", True)
        try: setattr(_ct.TradierConfig, "DELTA_ENTRY_ENABLED", True)
        except Exception: pass
        v8_logger.info(f"[V8_DELTA_FIX] DELTA_ENTRY_ENABLED auto-forced True (sweep tests DELTA_ENGINE_ENABLED/DELTA_HTF_GATE but didn't set DELTA_ENTRY_ENABLED)")
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
        return is_tradier_rth_ts(_sim_ts[0])
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
    # Historical positions must be eligible for MTF compound exits. The live
    # absolute restart-protection timestamp is operational state, not a strategy
    # parameter, so replace it only inside this simulated module/config scope.
    from mtf_exit_timing import effective_mtf_min_open_ts
    _mtf_sim_start_ts_t = float(
        _real_dt.strptime(start_date, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )
    _mtf_live_min_open_ts_t = float(
        getattr(tm_mod.config, "MTF_EXIT_MIN_OPEN_TS", 0.0) or 0.0
    )
    _mtf_backtest_min_open_ts_t = effective_mtf_min_open_ts(
        _mtf_live_min_open_ts_t,
        simulation_start_ts=_mtf_sim_start_ts_t,
    )
    setattr(tm_mod.config, "MTF_EXIT_MIN_OPEN_TS", _mtf_backtest_min_open_ts_t)
    try:
        setattr(_ct.TradierConfig, "MTF_EXIT_MIN_OPEN_TS", _mtf_backtest_min_open_ts_t)
        if (
            hasattr(_ct.TradierConfig, "__dataclass_fields__")
            and "MTF_EXIT_MIN_OPEN_TS" in _ct.TradierConfig.__dataclass_fields__
        ):
            _ct.TradierConfig.__dataclass_fields__["MTF_EXIT_MIN_OPEN_TS"].default = (
                _mtf_backtest_min_open_ts_t
            )
    except Exception:
        pass
    v8_logger.info(
        "[V8_MTF_SIM_START] MTF_EXIT_MIN_OPEN_TS=%s "
        "(live configured cutoff %s; backtest-only simulation start)",
        _mtf_backtest_min_open_ts_t,
        _mtf_live_min_open_ts_t,
    )
    # Harness-only capital contract (never written to config_tradier.py/live):
    # $2k benchmark unit and $16k strategy capacity at the canonical $10k capital.
    _capital_contract_t = apply_tradier_backtest_capital_contract(tm_mod.config, capital)
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
            "STRUCTURAL_EXIT_GATE_ENABLED": "structural_exit_gate_enabled",
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
            # 2026-05-12 FIX 2 — sim-time state hooks (tradier _place path).
            try:
                _bt_now_pl = float(_sim_ts[0]) if _sim_ts else 0.0
                _bt_act_pl = (act or "").upper()
                _bt_is_hedge_pl = bool(kw.get("is_hedge", False)) or 'HEDGE' in reason.upper()
                if _bt_act_pl in ('OPEN', 'QUICK_OPEN', 'AUGMENT', 'QUICK_AUGMENT', 'REENTRY', 'HEDGE_OPEN'):
                    manager.__dict__.setdefault('_bt_augment_lock', {})[pk] = _bt_now_pl
                    manager.__dict__.setdefault('_bt_open_attempt', {})[pk] = _bt_now_pl
                    if _bt_is_hedge_pl:
                        manager.__dict__.setdefault('_bt_hedge_completed', {})[pk] = _bt_now_pl
                elif _bt_act_pl in ('CLOSE', 'REDUCE', 'QUICK_CLOSE', 'FULL_CLOSE', 'PROFIT_TAKE', 'HEDGE_CLOSE'):
                    manager.__dict__.setdefault('_bt_reduce_lock', {})[pk] = _bt_now_pl
                    manager.__dict__.setdefault('_bt_reduce_price', {})[pk] = price
                    manager.__dict__.setdefault('_bt_reentry_unblock', {}).pop(pk, None)
            except Exception:
                pass
        return {"id": len(executed_trades), "status": "filled", "order": {"id": len(executed_trades), "status": "ok"}}
    manager.place_order = _place
    # Patch execute_trade_action to route through our _v8_execute_now (captures ALL trade paths)
    _orig_eta = getattr(manager, 'execute_trade_action', None)
    async def _v8_execute_trade_action(account_key='', position_key='', symbol='', quantity=0, current_price=0, side='', position_side='', unique_id=None, is_full_close=False, action='', reason='', override_qty=None, is_hedge=False, hedge_for=None, **kw):
        action = str(action or ''); reason = str(reason or ''); symbol = str(symbol or ''); side = str(side or ''); position_side = str(position_side or '')
        if V8_DECISION_ONLY:
            qty = 1.0
        else:
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
        # The private research prefix is accepted only by this backtest engine
        # and only when the explicit replay adapter invokes this local helper.
        # It bypasses strategy-entry vetoes so the audit measures execution and
        # accounting parity of the frozen schedule.  No live module recognizes it.
        _is_research_replay = reason.startswith(
            (
                "V8_RESEARCH_TOP_EXIT_REPLAY",
                "V8_RESEARCH_BAND_LADDER_REPLAY",
            )
        )
        _is_ladder_seed = reason == "V8_LADDER_INITIAL_BH_SEED" or _is_research_replay
        # ═══════════════════════════════════════════════════════════════════════════
        # 2026-05-12 — VEC SHORT-CIRCUIT (tradier eta path). Same checkpoint as
        # crypto eta. With all flags OFF this is a near-zero-cost noop.
        # ═══════════════════════════════════════════════════════════════════════════
        if not _is_ladder_seed and V8_VEC_PARITY_AVAILABLE and (
            V8_USE_VEC_STALE_MARK or V8_USE_VEC_EMERGENCY_BRAKE or V8_USE_VEC_COOLDOWN_LOCKS
            or V8_USE_VEC_OPEN_INTENT_SIZE or V8_USE_VEC_NOLOSS_GATE or V8_USE_VEC_AUGMENT_GATE
            or V8_USE_VEC_PROTECT_BALANCE or V8_USE_VEC_CIRCUIT_SHARPE
            or V8_USE_VEC_TRADEABLE_STATE or V8_USE_VEC_QUARANTINE_STRATEGY
        ):
            _vec_cfg_t = getattr(tm_mod, 'config', None) or config
            _vec_pos_t = None
            _vec_tk_t = set()
            try:
                if manager.position_manager:
                    _vec_pos_t = manager.position_manager.positions.get(position_key)
                _ac_t = manager.accounts.get(account_key) if hasattr(manager, 'accounts') else None
                if _ac_t is not None:
                    _vec_tk_t = set(getattr(_ac_t, 'tradeable_keys', set()) or set())
            except Exception:
                pass
            _vec_ind_t = manager.market_snapshot.get(symbol.upper(), {}) if hasattr(manager, 'market_snapshot') else {}
            # 2026-05-12 FIX 2 — read sim-time state maps populated post-fill.
            _vec_aug_lock_t = (manager.__dict__.get('_bt_augment_lock') or {}).get(position_key, 0.0)
            _vec_reduce_lock_t = (manager.__dict__.get('_bt_reduce_lock') or {}).get(position_key, 0.0)
            _vec_open_attempt_t = (manager.__dict__.get('_bt_open_attempt') or {}).get(position_key, 0.0)
            _vec_blk_t, _vec_reason_t = _v8_vec_short_circuit(
                action=act, position_key=position_key, symbol=symbol, account_key=account_key,
                qty=qty, px=px, side=side, position_side=position_side, reason=reason or "",
                is_reduce=is_reduce, is_hedge=is_hedge, is_full_close=is_full_close,
                pos_obj=_vec_pos_t, tradeable_keys=_vec_tk_t,
                positions_dict=(manager.position_manager.positions if manager.position_manager else {}),
                indicators=_vec_ind_t, sim_ts=_sim_ts[0] if _sim_ts else 0,
                cfg=_vec_cfg_t, last_augment_ts=_vec_aug_lock_t,
                last_reduce_ts=_vec_reduce_lock_t, last_open_ts=_vec_open_attempt_t,
            )
            if _vec_blk_t:
                _vb_now_t = float(_sim_ts[0]) if _sim_ts else 0.0
                _vb_is_aug_t = (not is_reduce) and (not is_hedge)
                if _vb_is_aug_t:
                    manager.__dict__.setdefault('_bt_augment_lock', {})[position_key] = _vb_now_t
                    manager.__dict__.setdefault('_bt_open_attempt', {})[position_key] = _vb_now_t
                elif is_reduce and not is_hedge:
                    manager.__dict__.setdefault('_bt_reduce_lock', {})[position_key] = _vb_now_t
                    manager.__dict__.setdefault('_bt_reduce_price', {})[position_key] = px
                return _vec_reason_t
        # REENTRY price-cross cooldown (mirrors ez_reentry_daemon) — always-on tradier.
        if (act or '').upper() == 'REENTRY' and not _is_research_replay:
            try:
                _rx_cfg_t = getattr(tm_mod, 'config', None) or config
                _rx_reduce_lock_t = (manager.__dict__.get('_bt_reduce_lock') or {}).get(position_key, 0.0)
                _rx_ind_t = manager.market_snapshot.get(symbol.upper(), {}) if hasattr(manager, 'market_snapshot') else {}
                _rx_reduce_price_t = (manager.__dict__.get('_bt_reduce_price') or {}).get(position_key, 0.0)
                _rx_blk_t, _rx_reason_t = _v8_reentry_cooldown_check(
                    pk=position_key, position_side=position_side, mark_price=px,
                    last_reduce_ts=_rx_reduce_lock_t,
                    now_ts=float(_sim_ts[0]) if _sim_ts else 0.0,
                    indicators=_rx_ind_t, cfg=_rx_cfg_t,
                    state_dict=manager.__dict__,
                    exit_price=_rx_reduce_price_t,
                )
                if _rx_blk_t:
                    return _rx_reason_t
            except Exception:
                pass
        # NEW 2026-04-26 sweep switches: entry vetoes + sizing scalars (tradier path).
        if (not is_reduce) and (not is_hedge) and not _is_ladder_seed:
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
        if not is_reduce and not _is_ladder_seed:
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
                if position_side == 'LONG' and _ratio_pt > _ls_max_pt and "WT_3M_FORCE_OPEN" not in (reason or "").upper():
                    return f"BLOCKED_LS_RATIO_LONG_{_ratio_pt:.2f}gt{_ls_max_pt}"
                if position_side == 'SHORT' and _ratio_pt < _ls_min_pt and "WT_3M_FORCE_OPEN" not in (reason or "").upper():
                    return f"BLOCKED_LS_RATIO_SHORT_{_ratio_pt:.2f}lt{_ls_min_pt}"
        if (
            not is_reduce
            and not _is_ladder_seed
            and not ('MTF_ARROW' in (reason or '') or 'LR_BAND' in (reason or ''))
        ):
            _gr_min_tfs_pt = int(getattr(tm_mod.config, 'GOLDEN_RULE_HTF_MIN_TFS', 0) if hasattr(tm_mod, 'config') else 0)
            if _gr_min_tfs_pt > 0:
                try:
                    from golden_rule_htf import score_entry_htf as _gr_score_entry_pt
                    _gr_min_ind_pt = int(getattr(tm_mod.config, 'GOLDEN_RULE_MIN_IND', 2))
                    _gr_mode_pt = "tradier" if account_key.startswith(("trb", "trc", "tra")) else "crypto"
                    _gr_ind_pt = manager.market_snapshot.get(str(symbol).upper(), {})
                    _gr_px_pt = float(current_price or price_cache.get(str(symbol).upper(), 0) or 0)
                    _gr_is_long_pt = (str(position_side) == "LONG")
                    _gr_invert_pt = str(reason or '').startswith('GOLDEN_RULE')
                    _gr_ok_pt, _gr_tfs_pt, _ = _gr_score_entry_pt(_gr_ind_pt, _gr_is_long_pt, _gr_mode_pt, _gr_min_tfs_pt, _gr_min_ind_pt, _gr_px_pt, invert_dc_bb=_gr_invert_pt)
                    if not _gr_ok_pt:
                        return f"BLOCKED_GOLDEN_RULE_{_gr_tfs_pt}of{_gr_min_tfs_pt}tfs_need{_gr_min_ind_pt}ind"
                except Exception:
                    pass
        # ── NEWBORN_PROTECT (15-min grace) — mirror ez_manage.py:14210-14262 ──
        # evaluate_newborn_protect_core is imported at the top of the file but was
        # never called here, so the engine never blocked close attempts on freshly
        # opened positions. Live execute_now applies this gate on every is_reduce.
        # BACKTEST CAVEAT: positions pre-loaded from a live tracker have opened_at
        # timestamps from the real system (e.g. 2026-05-12) but sim time starts at
        # the backtest start date (e.g. 2026-01-01). This produces negative age.
        # Negative age = position NOT opened during this sim run → skip the gate.
        # 2026-05-12 — flag-gated; with flag OFF the gate is skipped (matches
        # engine behaviour pre-newborn-wire).
        if V8_USE_VEC_NEWBORN_PROTECT and is_reduce and not is_hedge and not _is_research_replay:
            try:
                _nb_pos = None
                if manager.position_manager:
                    _nb_pos = manager.position_manager.positions.get(position_key)
                if not _nb_pos and hasattr(manager, 'positions'):
                    _nb_pos = manager.positions.get(position_key)
                if _nb_pos:
                    _nb_opened = getattr(_nb_pos, 'last_augmentation_time', None) or getattr(_nb_pos, 'opened_at', None)
                    _nb_ind = manager.market_snapshot.get(symbol.upper(), {}) if hasattr(manager, 'market_snapshot') else {}
                    _nb_dc_low_3m = float(_nb_ind.get('dc_low_3m', 0) or 0)
                    _nb_dc_high_3m = float(_nb_ind.get('dc_high_3m', 0) or 0)
                    _nb_is_long = (position_side == 'LONG')
                    _nb_now_ts = float(_sim_ts[0]) if _sim_ts[0] else _real_time_module.time()
                    _nb_blocked, _nb_reason, _nb_age, _ = evaluate_newborn_protect_core(
                        action=action,
                        position_opened_at=_nb_opened,
                        now_ts=_nb_now_ts,
                        mark_price=px,
                        dc_low_3m=_nb_dc_low_3m,
                        dc_high_3m=_nb_dc_high_3m,
                        is_long=_nb_is_long,
                        reason=reason,
                        is_hedge=is_hedge,
                        config=getattr(tm_mod, 'config', None) or ez_manage.config,
                    )
                    if _nb_blocked and _nb_age >= 0:
                        v8_logger.debug(f"[V8_NEWBORN_PROTECT] {position_key}: BLOCKED {action} — {_nb_reason} age={_nb_age:.0f}s")
                        return _nb_reason
            except Exception as _nb_err:
                v8_logger.debug(f"[V8_NEWBORN_PROTECT_ERR] {position_key}: fail-open ({_nb_err})")
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
                        "reentry_amount": max(_accum_re_amt, float(getattr(tm_mod.config, 'START_POSITION_SIZE', 55.0)) / max(px, 1e-9)),
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
            if "MANDATORY_REENTRY" in str(reason or "").upper():
                _trace_row = manager.__dict__.setdefault(
                    "_mandatory_reentry_trace", {}
                ).setdefault(position_key, {})
                _trace_row["pending"] = False
                _trace_row["filled_ts"] = float(_sim_now_t(timezone.utc).timestamp())
                _trace_row["filled_price"] = float(px)
            if pos:
                old_amt = abs(getattr(pos, 'positionAmt', getattr(pos, 'quantity', 0)))
                old_entry = getattr(pos, 'entry_price', px) or px
                new_amt = old_amt + abs(qty)
                pos.entry_price = (old_entry * old_amt + px * abs(qty)) / new_amt if new_amt > 0 else px
                pos.positionAmt = new_amt
                pos.quantity = new_amt
                # 2026-05-21 USER FIX: any 0→positive transition is a NEW position lifecycle.
                if old_amt < 0.0001:
                    pos.entry_price = px
                    try: pos.opened_at = _sim_now_t(timezone.utc)
                    except Exception: pass
                    # 2026-06-22: if this is a reentry after a MANDATORY_REENTRY exit, set
                    # last_reentry_time so DELTA_EXIT 45-min cooldown (tradier_manage) fires correctly.
                    try:
                        _pk_key = str(position_key)
                        if manager.__dict__.get('_bt_reentry_unblock', {}).get(_pk_key):
                            pos.last_reentry_time = _sim_now_t(timezone.utc)
                    except Exception: pass
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
                        s2.mark_price_last_updated=None; s2.last_reentry_time=None; s2.r1_stop_price=0.0
                new_pos = _SP(symbol, position_side, abs(qty), px)
                # 2026-06-22: tag last_reentry_time if this new open follows a MANDATORY_REENTRY close
                try:
                    if manager.__dict__.get('_bt_reentry_unblock', {}).get(str(position_key)):
                        new_pos.last_reentry_time = _sim_now_t(timezone.utc)
                except Exception: pass
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
            # ═══════════════════════════════════════════════════════════════════════════
            # 2026-05-12 — V8_DECISION_ONLY FAST PATH (tradier real-eta)
            # Bypass _orig_eta (which calls calculate_final_order_quantity and many gates).
            # Same semantics as crypto fast path: double-open reclass, UNIVERSAL_NOLOSS_GATE,
            # position-dict update, decision log.
            # ═══════════════════════════════════════════════════════════════════════════
            if V8_DECISION_ONLY:
                _do_qty = 1.0
                _do_px = float(current_price or price_cache.get(str(symbol).upper(), 0))
                if _do_px <= 0:
                    return "BLOCKED_ZERO_PX_DECISION_ONLY"
                _do_pos = None
                if manager.position_manager:
                    _do_pos = manager.position_manager.positions.get(_pk)
                if _do_pos is None and hasattr(manager, 'positions'):
                    _do_pos = manager.positions.get(_pk)
                _do_pos_amt = abs(float(getattr(_do_pos, 'positionAmt', 0) or 0)) if _do_pos else 0.0
                _do_is_long = (str(position_side) == 'LONG') if position_side else _pk.endswith('_LONG')
                # (a) Double-open reclass
                if _act.upper() in ('OPEN', 'QUICK_OPEN', 'REENTRY') and _do_pos_amt > 0.0001:
                    _V8_DECISION_COUNTERS["doubleopen_reclass"] += 1
                    _act = "AUGMENT"; _reason = f"DECISION_ONLY_DOUBLEOPEN_RECLASS|{_reason}"[:200]
                # (b) UNIVERSAL_NOLOSS_GATE
                if _is_reduce and not is_hedge:
                    _do_cfg = getattr(tm_mod, 'config', None) or config
                    _do_ung_on = bool(getattr(_do_cfg, 'UNIVERSAL_NOLOSS_GATE', True))
                    if _do_ung_on:
                        _do_reason_up = (_reason or '').upper()
                        _do_bypass = ('LIQUIDATION' in _do_reason_up or 'RIDICULOUS_LOSS' in _do_reason_up
                                      or 'UNDERWATER_HEDGE_OR_CLOSE' in _do_reason_up
                                      or 'STRUCTURAL_RANGE_SHIFT' in _do_reason_up)
                        if not _do_bypass:
                            for _brk in (getattr(_do_cfg, 'UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS', []) or []):
                                if _brk and _brk.upper() in _do_reason_up:
                                    _do_bypass = True; break
                        if not _do_bypass and _do_pos:
                            _do_entry = float(getattr(_do_pos, 'entry_price', 0) or 0)
                            _do_comm = float(getattr(_do_cfg, 'COMMISSION_BUFFER_PCT', 0.10))
                            if _do_entry > 0:
                                _do_gain = ((_do_px - _do_entry) / _do_entry * 100) if _do_is_long else ((_do_entry - _do_px) / _do_entry * 100)
                                if _do_gain < _do_comm:
                                    _V8_DECISION_COUNTERS["blocks"] += 1
                                    return "BLOCKED_BY_UNIVERSAL_NOLOSS_GATE_DECISION_ONLY"
                # (c) Update positions + executed_trades
                executed_trades.append({"timestamp": _sim_ts[0], "type": "eta_real",
                                        "position_key": _pk, "symbol": str(symbol), "side": str(side),
                                        "quantity": _do_qty, "price": _do_px, "action": _act,
                                        "reason": str(_reason)[:200], "position_side": str(position_side),
                                        "is_full_close": is_full_close, "decision_only": True})
                # 2026-05-12 FIX 2 — sim-time state hooks (tradier real-eta DECISION_ONLY).
                try:
                    _bt_now_re = float(_sim_ts[0]) if _sim_ts else 0.0
                    _bt_act_re = (_act or "").upper()
                    if _bt_act_re in ('OPEN', 'QUICK_OPEN', 'AUGMENT', 'QUICK_AUGMENT', 'REENTRY', 'HEDGE_OPEN'):
                        manager.__dict__.setdefault('_bt_augment_lock', {})[_pk] = _bt_now_re
                        manager.__dict__.setdefault('_bt_open_attempt', {})[_pk] = _bt_now_re
                        if is_hedge or 'HEDGE' in (_reason or '').upper():
                            manager.__dict__.setdefault('_bt_hedge_completed', {})[_pk] = _bt_now_re
                    elif _bt_act_re in ('CLOSE', 'REDUCE', 'QUICK_CLOSE', 'FULL_CLOSE', 'PROFIT_TAKE', 'HEDGE_CLOSE'):
                        manager.__dict__.setdefault('_bt_reduce_lock', {})[_pk] = _bt_now_re
                        manager.__dict__.setdefault('_bt_reduce_price', {})[_pk] = _do_px
                        manager.__dict__.setdefault('_bt_reentry_unblock', {}).pop(_pk, None)
                except Exception:
                    pass
                if _is_reduce and _do_pos:
                    _old = _do_pos_amt
                    _new = max(0.0, _old - abs(_do_qty))
                    _do_pos.positionAmt = 0.0 if (is_full_close or _new < 0.0001) else _new
                    _do_pos.quantity = _do_pos.positionAmt
                    if is_hedge: _V8_DECISION_COUNTERS["hedges"] += 1
                    elif is_full_close or _new < 0.0001: _V8_DECISION_COUNTERS["closes"] += 1
                    else: _V8_DECISION_COUNTERS["reduces"] += 1
                elif not _is_reduce:
                    if _do_pos:
                        _old = _do_pos_amt
                        _old_ep = getattr(_do_pos, 'entry_price', _do_px) or _do_px
                        _new = _old + abs(_do_qty)
                        _do_pos.entry_price = (_old_ep * _old + _do_px * abs(_do_qty)) / _new if _new > 0 else _do_px
                        _do_pos.positionAmt = _new; _do_pos.quantity = _new
                        _V8_DECISION_COUNTERS["augments" if not is_hedge else "hedges"] += 1
                    else:
                        class _DPT:
                            def __init__(s2, sy, sd, q, ep):
                                s2.symbol=sy; s2.position_side=sd; s2.positionAmt=q; s2.quantity=q
                                s2.entry_price=ep; s2.mark_price=ep; s2.gain=0; s2.prev_gain=0; s2.max_gain=0
                                s2.opened_at=_sim_now_t(timezone.utc); s2.last_updated=_sim_now_t(timezone.utc)
                                s2.last_augmentation_time=None; s2.last_augmentation_price=0
                                s2.last_reduction_time=None; s2.last_reduction_price=0
                                s2.was_reduced=False; s2.augmented_count=0; s2.max_quantity=q; s2.mark_price_last_updated=None
                        _np = _DPT(str(symbol), str(position_side), abs(_do_qty), _do_px)
                        if hasattr(manager, 'positions'): manager.positions[_pk] = _np
                        if hasattr(manager, 'positions_by_account') and isinstance(manager.positions_by_account, dict):
                            manager.positions_by_account.setdefault(account_key, {})[_pk] = _np
                        if manager.position_manager: manager.position_manager.positions[_pk] = _np
                        _V8_DECISION_COUNTERS["opens" if not is_hedge else "hedges"] += 1
                # (d) JSONL decision log
                _ind_t = manager.market_snapshot.get(str(symbol).upper(), {}) if hasattr(manager, 'market_snapshot') else {}
                _v8_decision_log(account_key=account_key, position_key=_pk, symbol=str(symbol),
                                 side_long=_do_is_long, action=_act, reason=_reason,
                                 price=_do_px, sim_ts=_sim_ts[0], indicators=_ind_t)
                return "SUCCESS_DECISION_ONLY"
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
                try:
                    _wf_force_bypass_r = (
                        "WT_3M_FORCE_OPEN" in str(_reason or reason or "").upper()
                        and bool(tm_mod._cfg(
                            "WT_3M_FORCE_OPEN_BYPASS_GATES", True,
                            account_key, symbol, position_side,
                        ))
                    )
                except Exception:
                    _wf_force_bypass_r = (
                        "WT_3M_FORCE_OPEN" in str(_reason or reason or "").upper()
                        and bool(getattr(
                            tm_mod.config, "WT_3M_FORCE_OPEN_BYPASS_GATES", True
                        ))
                    )
                _sat_on = (
                    getattr(tm_mod.config, 'SATOSHIT_ENTRY_FILTER', True)
                    and not _wf_force_bypass_r
                )
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
                # 2026-05-16 A3 fix #2 — GR_HTF_GATE_ENABLED (default False).
                # Mirrors live wiring at tradier_manage.py:2215-2225. Reads
                # wt_bull_alignment / wt_bear_alignment from snapshot. Blocks LONG
                # when alignment < REQUIRE_BULL (and mirror for SHORT). Default False —
                # sweep-validated before promotion.
                if account_key.startswith(("trb", "trc", "tra")) and not _wf_force_bypass_r:
                    if bool(getattr(tm_mod.config, 'GR_HTF_GATE_ENABLED', False)):
                        _gh_ind = manager.market_snapshot.get(str(symbol).upper(), {}) if hasattr(manager, 'market_snapshot') else {}
                        _gh_is_long = (str(position_side) == "LONG")
                        _gh_bull = int((_gh_ind or {}).get('wt_bull_alignment', 0) or 0)
                        _gh_bear = int((_gh_ind or {}).get('wt_bear_alignment', 0) or 0)
                        _gh_req_bull = int(getattr(tm_mod.config, 'GR_HTF_REQUIRE_BULL', 1))
                        _gh_req_bear = int(getattr(tm_mod.config, 'GR_HTF_REQUIRE_BEAR', 1))
                        if _gh_is_long and _gh_bull < _gh_req_bull:
                            return f"BLOCKED_GR_HTF_BULL_{_gh_bull}lt{_gh_req_bull}"
                        if (not _gh_is_long) and _gh_bear < _gh_req_bear:
                            return f"BLOCKED_GR_HTF_BEAR_{_gh_bear}lt{_gh_req_bear}"
                # MFI_ENTRY_ENABLED — OVERBOUGHT FILTER (fixed 2026-05-18; prior semantics inverted/dead-gate).
                # Mirrors live wiring at tradier_manage.py:11156-11162 (inside should_enter_long).
                # Blocks LONG when mfi_D > MFI_LONG_THRESHOLD_D (default 80 — overbought zone, reversal expected).
                if account_key.startswith(("trb", "trc", "tra")) and not _wf_force_bypass_r:
                    if bool(getattr(tm_mod.config, 'MFI_ENTRY_ENABLED', False)):
                        _mfi_ind = manager.market_snapshot.get(str(symbol).upper(), {}) if hasattr(manager, 'market_snapshot') else {}
                        _mfi_is_long = (str(position_side) == "LONG")
                        if _mfi_is_long:
                            _mfi_d = float((_mfi_ind or {}).get('mfi_D', 50) or 50)
                            _mfi_thr = float(getattr(tm_mod.config, 'MFI_LONG_THRESHOLD_D', 80.0))
                            if _mfi_d > _mfi_thr:
                                return f"BLOCKED_MFI_ENTRY_D_{_mfi_d:.0f}gt{_mfi_thr:.0f}"
                _wt_dc_thr = float(getattr(tm_mod.config, 'WT_DC_ENTRY_THRESHOLD', 55) or 55)
                # NEW 2026-04-26 sweep switch: SQUEEZE_FIRE bonus lowers effective WT_DC threshold.
                if _v8ns_sf_bonus_r > 0 and _wt_dc_thr > 0:
                    _wt_dc_thr = max(0.0, _wt_dc_thr - _v8ns_sf_bonus_r)
                _wt_dc_score = None
                # 2026-07-21: MTF_ARROW/LR_BAND entries are band+slope gated upstream — the
                # wt_dc momentum score is structurally LOW at the swing bottoms they buy, so
                # this gate silently starved them (305 blocks in the ARM forensic run).
                if _wt_dc_thr > 0 and not _wf_force_bypass_r and not ('MTF_ARROW' in (reason or '') or 'LR_BAND' in (reason or '')):
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
                # 2026-05-16 A3 fix #10 — TRADIER_ENTRY_SCORE_THRESHOLD (default 0 → inert).
                # Mirrors live post-entry veto at tradier_manage.py:2300 (account_key != 'tra').
                # Reuses _wt_dc_score above as the entry-confidence proxy (conceptually identical
                # on tradier path). When ENTRY_SCORE_THRESHOLD > 0 and score below, refuse entry.
                if account_key.startswith(("trb", "trc")) and not account_key.startswith("tra") and not _wf_force_bypass_r:
                    _es_thr = float(getattr(tm_mod.config, 'ENTRY_SCORE_THRESHOLD', 0) or 0)
                    if _es_thr <= 0:
                        _es_thr = float(getattr(tm_mod.config, 'TRADIER_ENTRY_SCORE_THRESHOLD', 0) or 0)
                    if _es_thr > 0:
                        _es_score = float(_wt_dc_score) if _wt_dc_score is not None else 0.0
                        if _wt_dc_score is None:
                            # Recompute if WT_DC_ENTRY_THRESHOLD was 0 (gate above skipped).
                            try:
                                from wt_dc_entry_scorer import score_entry as _es_score_fn
                                _es_ind = manager.market_snapshot.get(str(symbol).upper(), {})
                                _es_is_long = (str(position_side) == "LONG")
                                _es_px = float(current_price or price_cache.get(str(symbol).upper(), 0) or 0)
                                _es_score, _ = _es_score_fn(_es_ind, _es_is_long, _es_px)
                                _es_score = float(_es_score)
                            except Exception:
                                _es_score = 0.0
                        if _es_score < _es_thr:
                            return f"BLOCKED_ENTRY_SCORE_THRESHOLD_{_es_score:.0f}lt{_es_thr:.0f}"
                if getattr(tm_mod.config, 'STRUCTURAL_RANGE_SHIFT_EXIT', False):
                    try:
                        _srs_e_ind = manager.market_snapshot.get(str(symbol).upper(), {})
                        _srs_e_tf = getattr(tm_mod.config, 'STRUCTURAL_RANGE_SHIFT_TF', 'bb_1h')
                        _srs_e_pctb_key = {'bb_1h': 'bb_pct_b_1h', 'bb_4h': 'bb_pct_b_4h', 'bb_D': 'bb_pct_b_D', 'dc_1h': 'bb_pct_b_1h', 'dc_4h': 'bb_pct_b_4h', 'dc_D': 'bb_pct_b_D'}.get(_srs_e_tf, 'bb_pct_b_1h')
                        _srs_e_pctb = float(_srs_e_ind.get(_srs_e_pctb_key, 0.5) or 0.5)
                        _srs_e_is_long = (str(position_side) == "LONG")
                        if _srs_e_is_long and _srs_e_pctb >= 0.97 and "WT_3M_FORCE_OPEN" not in (reason or "").upper():
                            return f"BLOCKED_SRS_ENTRY_LONG_AT_TOP_pctb={_srs_e_pctb:.2f}"
                        if (not _srs_e_is_long) and _srs_e_pctb <= 0.03 and "WT_3M_FORCE_OPEN" not in (reason or "").upper():
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
                    if str(position_side) == 'LONG' and _ratio_r > _ls_max_r and "WT_3M_FORCE_OPEN" not in (reason or "").upper():
                        return f"BLOCKED_LS_RATIO_LONG_{_ratio_r:.2f}gt{_ls_max_r}"
                    if str(position_side) == 'SHORT' and _ratio_r < _ls_min_r and "WT_3M_FORCE_OPEN" not in (reason or "").upper():
                        return f"BLOCKED_LS_RATIO_SHORT_{_ratio_r:.2f}lt{_ls_min_r}"
                # GOLDEN_RULE HTF gate — requires N timeframes each with M bullish indicators.
                # min_tfs=0 means disabled (default). Wire GOLDEN_RULE_HTF_MIN_TFS≥1 to activate.
                # invert_dc_bb=True for GR-sourced entries: DC/BB extension = bullish (breakout mode).
                _gr_min_tfs_r = int(getattr(tm_mod.config, 'GOLDEN_RULE_HTF_MIN_TFS', 0) if hasattr(tm_mod, 'config') else 0)
                if _gr_min_tfs_r > 0 and not ('MTF_ARROW' in (reason or '') or 'LR_BAND' in (reason or '')):
                    try:
                        from golden_rule_htf import score_entry_htf as _gr_score_entry
                        _gr_min_ind_r = int(getattr(tm_mod.config, 'GOLDEN_RULE_MIN_IND', 2))
                        _gr_mode_r = "tradier" if account_key.startswith(("trb", "trc", "tra")) else "crypto"
                        _gr_ind_r = manager.market_snapshot.get(str(symbol).upper(), {})
                        _gr_px_r = float(current_price or price_cache.get(str(symbol).upper(), 0) or 0)
                        _gr_is_long_r = (str(position_side) == "LONG")
                        _gr_invert_r = str(reason or '').startswith('GOLDEN_RULE')
                        _gr_ok_r, _gr_tfs_r, _gr_detail_r = _gr_score_entry(_gr_ind_r, _gr_is_long_r, _gr_mode_r, _gr_min_tfs_r, _gr_min_ind_r, _gr_px_r, invert_dc_bb=_gr_invert_r)
                        if not _gr_ok_r:
                            return f"BLOCKED_GOLDEN_RULE_{_gr_tfs_r}of{_gr_min_tfs_r}tfs_need{_gr_min_ind_r}ind"
                    except Exception as _gr_err_r:
                        v8_logger.warning(f"[V8_GOLDEN_RULE_ERR] {position_key}: {_gr_err_r}")
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
    _side_gate_off = bool(os.environ.get("V8_SIDE_GATE_DISABLED", ""))
    _ladder_only_side = os.environ.get("V8_LADDER_ONLY_SIDE", "").lower()
    def _always_tradeable(sym, acc=None, side=None):
        if sym.upper() not in _v8_syms:
            return False
        if _ladder_only_side in ("long", "short"):
            _requested_side = str(side or "").lower()
            if _requested_side in ("long", "short") and _requested_side != _ladder_only_side:
                return False
        if _side_gate_off:
            return True
        _sd = str(side or "").lower()
        if _sd in ("long", "short"):
            # The static symbol list is only the universe. The accepted
            # per-symbol recipe may disable one side (for example MU_SHORT).
            # Ignoring that flag made a LONG-only replay evaluate and sometimes
            # open the forbidden SHORT side.
            try:
                _side_enabled = bool(tm_mod._cfg(
                    f"{_sd.upper()}_ENABLED", True,
                    str(acc or account_key), sym.upper(), _sd.upper(),
                ))
            except Exception:
                _side_enabled = bool(getattr(
                    tm_mod.config, f"{_sd.upper()}_ENABLED", True
                ))
            if not _side_enabled:
                return False
            _allow = getattr(manager, f"symbols_{_sd}_{account_key}", None)
            if _allow:
                return sym in _allow or sym.upper() in _allow
        return True
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
        # 2026-05-12 — V8_DECISION_ONLY FAST PATH (tradier exec_now)
        # Mirrors crypto + real-eta fast path. exec_now is the broker-routing layer in live;
        # in decision-only mode we just log + update position dict.
        # ═══════════════════════════════════════════════════════════════════════════
        if V8_DECISION_ONLY:
            _do_qty = 1.0
            _do_px = float(px or 0)
            if _do_px <= 0:
                return "BLOCKED_ZERO_PX_DECISION_ONLY"
            _do_act = action or ("CLOSE" if is_reduce else "OPEN")
            _do_is_hedge = _en_kw.get('is_hedge', False)
            _do_pos = manager.position_manager.positions.get(position_key) if manager.position_manager else None
            _do_pos_amt = abs(float(getattr(_do_pos, 'positionAmt', 0) or 0)) if _do_pos else 0.0
            _do_is_long = (position_side == 'LONG') if position_side else position_key.endswith('_LONG')
            # Double-open reclass
            if _do_act.upper() in ('OPEN', 'QUICK_OPEN', 'REENTRY') and _do_pos_amt > 0.0001:
                _V8_DECISION_COUNTERS["doubleopen_reclass"] += 1
                _do_act = "AUGMENT"; reason = f"DECISION_ONLY_DOUBLEOPEN_RECLASS|{reason}"[:200]
            # UNIVERSAL_NOLOSS_GATE
            if is_reduce and not _do_is_hedge:
                _do_cfg = getattr(tm_mod, 'config', None) or config
                if bool(getattr(_do_cfg, 'UNIVERSAL_NOLOSS_GATE', True)):
                    _do_reason_up = reason.upper()
                    _do_bypass = ('LIQUIDATION' in _do_reason_up or 'RIDICULOUS_LOSS' in _do_reason_up
                                  or 'UNDERWATER_HEDGE_OR_CLOSE' in _do_reason_up
                                  or 'STRUCTURAL_RANGE_SHIFT' in _do_reason_up)
                    if not _do_bypass:
                        for _brk in (getattr(_do_cfg, 'UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS', []) or []):
                            if _brk and _brk.upper() in _do_reason_up:
                                _do_bypass = True; break
                    if not _do_bypass and _do_pos:
                        _do_entry = float(getattr(_do_pos, 'entry_price', 0) or 0)
                        _do_comm = float(getattr(_do_cfg, 'COMMISSION_BUFFER_PCT', 0.10))
                        if _do_entry > 0:
                            _do_gain = ((_do_px - _do_entry) / _do_entry * 100) if _do_is_long else ((_do_entry - _do_px) / _do_entry * 100)
                            if _do_gain < _do_comm:
                                _V8_DECISION_COUNTERS["blocks"] += 1
                                return "BLOCKED_BY_UNIVERSAL_NOLOSS_GATE_DECISION_ONLY"
            executed_trades.append({"timestamp": _sim_ts[0], "type": "exec_now", "position_key": position_key,
                                    "symbol": symbol, "side": side, "quantity": _do_qty, "price": _do_px,
                                    "action": _do_act, "reason": str(reason)[:200],
                                    "position_side": position_side, "is_full_close": is_full_close,
                                    "decision_only": True})
            # 2026-05-12 FIX 2 — sim-time state hooks (tradier exec_now DECISION_ONLY).
            try:
                _bt_now_xn = float(_sim_ts[0]) if _sim_ts else 0.0
                _bt_act_xn = (_do_act or "").upper()
                if _bt_act_xn in ('OPEN', 'QUICK_OPEN', 'AUGMENT', 'QUICK_AUGMENT', 'REENTRY', 'HEDGE_OPEN'):
                    manager.__dict__.setdefault('_bt_augment_lock', {})[position_key] = _bt_now_xn
                    manager.__dict__.setdefault('_bt_open_attempt', {})[position_key] = _bt_now_xn
                    if _do_is_hedge or 'HEDGE' in (reason or '').upper():
                        manager.__dict__.setdefault('_bt_hedge_completed', {})[position_key] = _bt_now_xn
                elif _bt_act_xn in ('CLOSE', 'REDUCE', 'QUICK_CLOSE', 'FULL_CLOSE', 'PROFIT_TAKE', 'HEDGE_CLOSE'):
                    manager.__dict__.setdefault('_bt_reduce_lock', {})[position_key] = _bt_now_xn
                    manager.__dict__.setdefault('_bt_reduce_price', {})[position_key] = _do_px
                    manager.__dict__.setdefault('_bt_reentry_unblock', {}).pop(position_key, None)
            except Exception:
                pass
            if is_reduce and _do_pos:
                _new = max(0.0, _do_pos_amt - abs(_do_qty))
                _do_pos.positionAmt = 0.0 if (is_full_close or _new < 0.0001) else _new
                _do_pos.quantity = _do_pos.positionAmt
                if _do_is_hedge: _V8_DECISION_COUNTERS["hedges"] += 1
                elif is_full_close or _new < 0.0001: _V8_DECISION_COUNTERS["closes"] += 1
                else: _V8_DECISION_COUNTERS["reduces"] += 1
            elif not is_reduce:
                if _do_pos:
                    _old = _do_pos_amt
                    _old_ep = getattr(_do_pos, 'entry_price', _do_px) or _do_px
                    _new = _old + abs(_do_qty)
                    _do_pos.entry_price = (_old_ep * _old + _do_px * abs(_do_qty)) / _new if _new > 0 else _do_px
                    _do_pos.positionAmt = _new; _do_pos.quantity = _new
                    _V8_DECISION_COUNTERS["augments" if not _do_is_hedge else "hedges"] += 1
                else:
                    class _DEN:
                        def __init__(s3, sy, sd, q, ep):
                            s3.symbol=sy; s3.position_side=sd; s3.positionAmt=q; s3.quantity=q
                            s3.entry_price=ep; s3.mark_price=ep; s3.gain=0; s3.prev_gain=0; s3.max_gain=0
                            s3.opened_at=_sim_now_t(timezone.utc); s3.last_updated=_sim_now_t(timezone.utc)
                            s3.last_augmentation_time=None; s3.last_augmentation_price=0
                            s3.last_reduction_time=None; s3.last_reduction_price=0
                            s3.was_reduced=False; s3.augmented_count=0; s3.max_quantity=q; s3.mark_price_last_updated=None
                    _np_en = _DEN(symbol, position_side, abs(_do_qty), _do_px)
                    if manager.position_manager:
                        manager.position_manager.positions[position_key] = _np_en
                        manager.position_manager.positions_by_account.setdefault(account_key_en, {})[position_key] = _np_en
                    if hasattr(manager, 'positions'): manager.positions[position_key] = _np_en
                    _V8_DECISION_COUNTERS["opens" if not _do_is_hedge else "hedges"] += 1
            _ind_en = manager.market_snapshot.get(symbol.upper(), {}) if hasattr(manager, 'market_snapshot') else {}
            _v8_decision_log(account_key=account_key_en, position_key=position_key, symbol=symbol,
                             side_long=_do_is_long, action=_do_act, reason=reason,
                             price=_do_px, sim_ts=_sim_ts[0], indicators=_ind_en)
            manager.recently_processed_signals[position_key] = _sim_ts[0]
            return "SUCCESS_DECISION_ONLY"
        # ═══════════════════════════════════════════════════════════════════════════
        # 2026-05-12 — VEC SHORT-CIRCUIT (tradier exec_now path). Mirrors the
        # checkpoints in _crypto_eta and _v8_execute_trade_action.
        # ═══════════════════════════════════════════════════════════════════════════
        if V8_VEC_PARITY_AVAILABLE and (
            V8_USE_VEC_STALE_MARK or V8_USE_VEC_EMERGENCY_BRAKE or V8_USE_VEC_COOLDOWN_LOCKS
            or V8_USE_VEC_OPEN_INTENT_SIZE or V8_USE_VEC_NOLOSS_GATE or V8_USE_VEC_AUGMENT_GATE
            or V8_USE_VEC_PROTECT_BALANCE or V8_USE_VEC_CIRCUIT_SHARPE
            or V8_USE_VEC_TRADEABLE_STATE or V8_USE_VEC_QUARANTINE_STRATEGY
        ):
            _vec_cfg_en = getattr(tm_mod, 'config', None) or config
            _vec_act_en = action or ("CLOSE" if is_reduce else "OPEN")
            _vec_is_hedge_en = _en_kw.get('is_hedge', False)
            _vec_pos_en = None
            _vec_tk_en = set()
            try:
                if manager.position_manager:
                    _vec_pos_en = manager.position_manager.positions.get(position_key)
                _ac_en = manager.accounts.get(account_key_en) if hasattr(manager, 'accounts') else None
                if _ac_en is not None:
                    _vec_tk_en = set(getattr(_ac_en, 'tradeable_keys', set()) or set())
            except Exception:
                pass
            _vec_ind_en = manager.market_snapshot.get(symbol.upper(), {}) if hasattr(manager, 'market_snapshot') else {}
            # 2026-05-12 FIX 2 — read sim-time state maps populated post-fill.
            _vec_aug_lock_en = (manager.__dict__.get('_bt_augment_lock') or {}).get(position_key, 0.0)
            _vec_reduce_lock_en = (manager.__dict__.get('_bt_reduce_lock') or {}).get(position_key, 0.0)
            _vec_open_attempt_en = (manager.__dict__.get('_bt_open_attempt') or {}).get(position_key, 0.0)
            _vec_blk_en, _vec_reason_en = _v8_vec_short_circuit(
                action=_vec_act_en, position_key=position_key, symbol=symbol,
                account_key=account_key_en, qty=float(quantity or 0), px=float(px or 0),
                side=side, position_side=position_side, reason=reason or "",
                is_reduce=is_reduce, is_hedge=_vec_is_hedge_en, is_full_close=is_full_close,
                pos_obj=_vec_pos_en, tradeable_keys=_vec_tk_en,
                positions_dict=(manager.position_manager.positions if manager.position_manager else {}),
                indicators=_vec_ind_en, sim_ts=_sim_ts[0] if _sim_ts else 0,
                cfg=_vec_cfg_en, last_augment_ts=_vec_aug_lock_en,
                last_reduce_ts=_vec_reduce_lock_en, last_open_ts=_vec_open_attempt_en,
            )
            if _vec_blk_en:
                _vb_now_en = float(_sim_ts[0]) if _sim_ts else 0.0
                _vb_is_aug_en = (not is_reduce) and (not _vec_is_hedge_en)
                if _vb_is_aug_en:
                    manager.__dict__.setdefault('_bt_augment_lock', {})[position_key] = _vb_now_en
                    manager.__dict__.setdefault('_bt_open_attempt', {})[position_key] = _vb_now_en
                elif is_reduce and not _vec_is_hedge_en:
                    manager.__dict__.setdefault('_bt_reduce_lock', {})[position_key] = _vb_now_en
                    manager.__dict__.setdefault('_bt_reduce_price', {})[position_key] = float(px or 0)
                return _vec_reason_en
        # REENTRY price-cross cooldown (tradier exec_now) — always-on.
        if (action or '').upper() == 'REENTRY':
            try:
                _rx_cfg_en = getattr(tm_mod, 'config', None) or config
                _rx_reduce_lock_en = (manager.__dict__.get('_bt_reduce_lock') or {}).get(position_key, 0.0)
                _rx_ind_en = manager.market_snapshot.get(symbol.upper(), {}) if hasattr(manager, 'market_snapshot') else {}
                _rx_reduce_price_en = (manager.__dict__.get('_bt_reduce_price') or {}).get(position_key, 0.0)
                _rx_blk_e, _rx_reason_e = _v8_reentry_cooldown_check(
                    pk=position_key, position_side=position_side, mark_price=float(px or 0),
                    last_reduce_ts=_rx_reduce_lock_en,
                    now_ts=float(_sim_ts[0]) if _sim_ts else 0.0,
                    indicators=_rx_ind_en, cfg=_rx_cfg_en,
                    state_dict=manager.__dict__,
                    exit_price=_rx_reduce_price_en,
                )
                if _rx_blk_e:
                    return _rx_reason_e
            except Exception:
                pass
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
        # ── NEWBORN_PROTECT (tradier exec_now path) — mirror ez_manage.py:14210-14262 ──
        # Age < 0 = position opened after sim start (tracker artifact) → pass through.
        # 2026-05-12 — flag-gated; with flag OFF the gate is skipped (matches
        # engine behaviour pre-newborn-wire).
        if V8_USE_VEC_NEWBORN_PROTECT and is_reduce:
            _is_hedge_en2 = _en_kw.get('is_hedge', False)
            if not _is_hedge_en2:
                try:
                    _nbe_pos = manager.position_manager.positions.get(position_key) if manager.position_manager else None
                    if _nbe_pos:
                        _nbe_opened = getattr(_nbe_pos, 'last_augmentation_time', None) or getattr(_nbe_pos, 'opened_at', None)
                        _nbe_ind = manager.market_snapshot.get(symbol.upper(), {}) if hasattr(manager, 'market_snapshot') else {}
                        _nbe_dc_low_3m = float(_nbe_ind.get('dc_low_3m', 0) or 0)
                        _nbe_dc_high_3m = float(_nbe_ind.get('dc_high_3m', 0) or 0)
                        _nbe_is_long = (position_side == 'LONG')
                        _nbe_now_ts = float(_sim_ts[0]) if _sim_ts[0] else _real_time_module.time()
                        _nbe_blocked, _nbe_reason, _nbe_age, _ = evaluate_newborn_protect_core(
                            action=action or ("CLOSE" if is_reduce else "OPEN"),
                            position_opened_at=_nbe_opened,
                            now_ts=_nbe_now_ts,
                            mark_price=float(px),
                            dc_low_3m=_nbe_dc_low_3m,
                            dc_high_3m=_nbe_dc_high_3m,
                            is_long=_nbe_is_long,
                            reason=reason,
                            is_hedge=_is_hedge_en2,
                            config=getattr(tm_mod, 'config', None),
                        )
                        if _nbe_blocked and _nbe_age >= 0:
                            v8_logger.debug(f"[V8_NEWBORN_PROTECT] {position_key}: BLOCKED (exec_now) — {_nbe_reason} age={_nbe_age:.0f}s")
                            return _nbe_reason
                except Exception as _nbe_err:
                    v8_logger.debug(f"[V8_NEWBORN_PROTECT_ERR] {position_key}: exec_now fail-open ({_nbe_err})")
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
    # 2026-05-12 — V8_DECISION_ONLY: stub tradier sizing/qty calculator (tradier_manage version
    # mirrors ez_manage.calculate_final_order_quantity). Also stub ez_manage's in case the
    # tradier mode shares the crypto helper (it shouldn't, but defense-in-depth).
    if V8_DECISION_ONLY:
        async def _v8_do_calc_qty_t(position_key=None, account_key=None, symbol=None, position=None,
                                    action="", trade_manager=None, conviction=0.5, base_quantity=0.0,
                                    reason="", signal_data=None):
            return max(0.001, float(base_quantity or 0.001))
        if hasattr(tm_mod, 'calculate_final_order_quantity'):
            tm_mod.calculate_final_order_quantity = _v8_do_calc_qty_t
        ez_manage.calculate_final_order_quantity = _v8_do_calc_qty_t
        v8_logger.info("[V8_DECISION_ONLY] calculate_final_order_quantity stubbed (tradier path)")
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
    # 2026-05-21 PER-SIDE ALLOWLIST FIX (user-mandated): respect symbols_<acct>_long/short.json.
    # Live attr names are symbols_long_<acct> / symbols_short_<acct> (read by is_symbol_tradeable
    # in tradier_positions.py:907-908). Previously both attrs were set to full sym_list so the
    # BT engine simulated NVDA_SHORT / PAAS_LONG etc. that live correctly refuses.
    _la_long_path = BASE_PATH / f"symbols_{account_key}_long.json"
    _la_short_path = BASE_PATH / f"symbols_{account_key}_short.json"
    try:
        _la_long = set(json.load(open(_la_long_path))) if _la_long_path.exists() else None
    except Exception:
        _la_long = None
    try:
        _la_short = set(json.load(open(_la_short_path))) if _la_short_path.exists() else None
    except Exception:
        _la_short = None
    _uat2 = os.environ.get("V8_UNIVERSE_AT", "")
    if _uat2:
        try:
            sys.path.insert(0, str(BASE_PATH / "tools"))
            import universe_registry as _ur2
            _la_long = set(_ur2.get_universe(account_key, "long", _uat2)) or _la_long
            _la_short = set(_ur2.get_universe(account_key, "short", _uat2)) or _la_short
        except Exception:
            pass
    _long_list = [s for s in sym_list if (_la_long is None) or (s in _la_long)]
    _short_list = [s for s in sym_list if (_la_short is None) or (s in _la_short)]
    setattr(manager, f"symbols_long_{account_key}", _long_list)
    setattr(manager, f"symbols_short_{account_key}", _short_list)
    v8_logger.info(f"PER_SIDE_ALLOWLIST {account_key}: long={len(_long_list)}/{len(sym_list)} short={len(_short_list)}/{len(sym_list)}")
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
                else:
                    # 2026-06-22: DELTA_EXIT 45-min cooldown after reentry fill (mirrors tradier_manage)
                    _bt_delta_last_rt = getattr(position, 'last_reentry_time', None)
                    _bt_delta_cool = float(getattr(tm_mod.config, 'DELTA_EXIT_REENTRY_COOLDOWN_MIN', 45.0))
                    if isinstance(_bt_delta_last_rt, datetime) and _bt_delta_cool > 0:
                        try:
                            _bt_delta_age = (_sim_datetime_now(timezone.utc) - _bt_delta_last_rt).total_seconds() / 60.0
                            if _bt_delta_age < _bt_delta_cool:
                                return False, f"BT_DELTA_EXIT_COOLDOWN_{_bt_delta_age:.0f}m_lt_{_bt_delta_cool:.0f}m", 0
                        except Exception: pass
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
    _end_date_str_tr = V8_BACKTEST_END_DATE
    end_ts_filter_tr = int(datetime.strptime(_end_date_str_tr, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) if _end_date_str_tr else 0
    all_ts = sorted(set(int(t) for s in stores.values() for t in s.timestamps if int(t) >= start_ts_filter and (not end_ts_filter_tr or int(t) <= end_ts_filter_tr)))
    _research_adapter_t = None
    _research_audit_t = None
    _research_spec_path_t = os.environ.get("V8_RESEARCH_TOP_EXIT_SPEC", "").strip()
    _research_ladder_adapter_t = None
    _research_ladder_audit_t = None
    _research_ladder_spec_path_t = os.environ.get(
        "V8_RESEARCH_LADDER_SPEC", ""
    ).strip()
    if _research_spec_path_t and _research_ladder_spec_path_t:
        raise RuntimeError(
            "top-exit and band-ladder research replays are mutually exclusive"
        )
    if _research_spec_path_t:
        from tools.v8_research_top_exit_adapter import TopExitReplayAdapter

        _research_adapter_t = TopExitReplayAdapter(_research_spec_path_t)
        _research_loaded_store_t = stores.get(_research_adapter_t.symbol)
        _research_loaded_npz_t = getattr(
            _research_loaded_store_t, "source_npz_path", ""
        )
        if not _research_loaded_npz_t:
            raise RuntimeError("V8_RESEARCH_TOP_EXIT loaded NPZ path is unavailable")
        _research_adapter_t.validate_npz(_research_loaded_npz_t)
        _research_adapter_t.validate_runtime(
            account=account_key,
            symbols=list(stores),
            mode="tradier",
            seed_positions_file=os.environ.get("V8_SEED_POSITIONS_FILE", ""),
            round_trip_cost_pct=_round_trip_cost_for_sym(_research_adapter_t.symbol),
        )
        _missing_research_ts_t = sorted(
            {action.fill_ts for action in _research_adapter_t.actions} - set(all_ts)
        )
        if _missing_research_ts_t:
            raise RuntimeError(
                "V8_RESEARCH_TOP_EXIT schedule timestamps absent from loaded NPZ: "
                f"{_missing_research_ts_t[:10]}"
            )
        print(
            "V8_RESEARCH_TOP_EXIT_INIT: "
            f"symbol={_research_adapter_t.symbol} "
            f"side={_research_adapter_t.position_side} "
            f"actions={len(_research_adapter_t.actions)} "
            f"spec={_research_adapter_t.spec_path}",
            flush=True,
        )
    if _research_ladder_spec_path_t:
        from tools.v8_research_ladder_adapter import LadderReplayAdapter

        _research_ladder_adapter_t = LadderReplayAdapter(
            _research_ladder_spec_path_t
        )
        _research_ladder_loaded_store_t = stores.get(
            _research_ladder_adapter_t.symbol
        )
        _research_ladder_loaded_npz_t = getattr(
            _research_ladder_loaded_store_t, "source_npz_path", ""
        )
        if not _research_ladder_loaded_npz_t:
            raise RuntimeError(
                "V8_RESEARCH_LADDER loaded NPZ path is unavailable"
            )
        _research_ladder_adapter_t.validate_npz(
            _research_ladder_loaded_npz_t
        )
        _research_ladder_adapter_t.validate_runtime(
            account=account_key,
            symbols=list(stores),
            mode="tradier",
            seed_positions_file=os.environ.get("V8_SEED_POSITIONS_FILE", ""),
            round_trip_cost_pct=_round_trip_cost_for_sym(
                _research_ladder_adapter_t.symbol
            ),
        )
        _missing_ladder_ts_t = sorted(
            {
                action.fill_ts
                for action in _research_ladder_adapter_t.actions
            }
            - set(all_ts)
        )
        if _missing_ladder_ts_t:
            raise RuntimeError(
                "V8_RESEARCH_LADDER schedule timestamps absent from loaded NPZ: "
                f"{_missing_ladder_ts_t[:10]}"
            )
        print(
            "V8_RESEARCH_LADDER_INIT: "
            f"symbol={_research_ladder_adapter_t.symbol} "
            f"side={_research_ladder_adapter_t.position_side} "
            f"actions={len(_research_ladder_adapter_t.actions)} "
            f"semantics={_research_ladder_adapter_t.spec['semantics']} "
            f"cap=${_research_ladder_adapter_t.capacity:.2f} "
            f"spec={_research_ladder_adapter_t.spec_path}",
            flush=True,
        )
    v8_logger.info(f"Tradier: {len(all_ts)} bars, {len(stores)} symbols from {start_date}")
    t0 = _real_time_module.time()
    report_every = 200 if _SWEEP_MODE else max(1, len(all_ts) // 20)
    _last_heartbeat_t = _real_time_module.time()
    _bt_rg_t_disabled = os.environ.get("V8_RATE_GUARD_DISABLED", "0") == "1"
    _bt_rg_t = None if _bt_rg_t_disabled else RateGuard(n_accts=max(1, len(stores)), label=f"backtest_v8_engine.tradier.{account_key}")
    _w_exit_prev_t = {}  # sym → bool: was wt1_W > wt2_W last bar (for cross detection)
    _dc_fstop_tr: dict = {}   # pk → frozen dc_low_{tf} level at first open bar
    _bb_fstop_tr: dict = {}
    _dyn_strail_tr: dict = {}  # pk -> {'lvl','armed'} monotone structure-trail (2026-07-19)   # pk → frozen bb_lower/upper/basis_{tf} level at first open bar
    _exposure_seconds_t = {"LONG": 0.0, "SHORT": 0.0}
    _ladder_initial_seeded_t = False
    _candidate_diag_t = 0
    # ═══════════════════════════════════════════════════════════════════════════
    # 2026-07-09 MINERVINI/CLENOW FAITHFUL TIER-2 HOOK — calls the REAL
    # tradier_manage.evaluate_minervini_entry / evaluate_clenow_entry (no
    # reimplementation; parity mandate). Live driver = _rotation_rsi2_loop
    # (restored 2026-07-09): each runs every 1800s during RTH, suppressed by
    # PARITY_COMPARISON_MODE. NPZ snapshot provides sepa_pass/sepa_score/
    # clenow_score per bar (build_indicator_dict exposes all arrays), so
    # _get_extras is neutralized — live it recomputes from klines_cache which
    # would be LOOKAHEAD in a sim. Zero behavior change when both flags are
    # False (config_tradier base default: MINERVINI_ENABLED=False, CLENOW_ENABLED=False).
    # ═══════════════════════════════════════════════════════════════════════════
    _v8_minervini_on = bool(getattr(tm_mod.config, 'MINERVINI_ENABLED', False))
    _v8_clenow_on = bool(getattr(tm_mod.config, 'CLENOW_ENABLED', False))
    _v8_strat_last = {'minervini': 0.0, 'clenow': 0.0}
    if _v8_minervini_on or _v8_clenow_on:
        manager._get_extras = lambda symbol: {}
        v8_logger.info(f"[V8_STRATEGY_HOOK] MINERVINI_ENABLED={_v8_minervini_on} CLENOW_ENABLED={_v8_clenow_on} — real evaluate_* wired at live cadence (1800s), _get_extras neutralized (NPZ snapshot is the no-lookahead source)")
    for step, ts in enumerate(all_ts):
        if step > 0 and manager.position_manager:
            _dt_t = max(0.0, float(ts) - float(all_ts[step - 1]))
            _active_sides_t = set()
            for _epk_t, _epos_t in manager.position_manager.positions.items():
                try:
                    _eamt_t = abs(float(getattr(_epos_t, "positionAmt",
                                                getattr(_epos_t, "quantity", 0)) or 0))
                except (TypeError, ValueError):
                    continue
                if _eamt_t < 0.0001:
                    continue
                if str(_epk_t).endswith("_LONG"):
                    _active_sides_t.add("LONG")
                elif str(_epk_t).endswith("_SHORT"):
                    _active_sides_t.add("SHORT")
            for _eside_t in _active_sides_t:
                _exposure_seconds_t[_eside_t] += _dt_t
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
                _tf_ts_key = f'timestamp_{_tf}'
                if not ind.get(_tf_ts_key):
                    ind[_tf_ts_key] = _sim_iso
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
        # ═══════════════════════════════════════════════════════════════════════════
        # PORTFOLIO-AWARE SENTIMENT INJECTION (tradier path) — 2026-05-12
        # Mirror of crypto path above. Stocks use wt1_15m/wt1_1h hybrid as sentiment signal.
        # ═══════════════════════════════════════════════════════════════════════════
        _tpf_wt_scores = {}
        for _tpf_sym, _tpf_ind in indicator_cache.items():
            _tpf_wt1 = float(_tpf_ind.get('wt1_15m', _tpf_ind.get('wt1_5m', 0)) or 0)
            _tpf_wt2 = float(_tpf_ind.get('wt2_15m', _tpf_ind.get('wt2_5m', 0)) or 0)
            _tpf_wt1h = float(_tpf_ind.get('wt1_1h', 0) or 0)
            _tpf_wt2h = float(_tpf_ind.get('wt2_1h', 0) or 0)
            _tpf_wt_scores[_tpf_sym] = 0.5 * (_tpf_wt1 - _tpf_wt2) + 0.5 * (_tpf_wt1h - _tpf_wt2h)
        if _tpf_wt_scores:
            _tpf_max_abs = max(abs(v) for v in _tpf_wt_scores.values()) or 50.0
            if _tpf_max_abs < 50.0:
                _tpf_max_abs = 50.0
            _tpf_global_avg = sum(_tpf_wt_scores.values()) / len(_tpf_wt_scores)
            _tpf_global_score = (_tpf_global_avg / _tpf_max_abs) * 100.0
            for _tpf_sym, _tpf_raw in _tpf_wt_scores.items():
                _tpf_local = (_tpf_raw / _tpf_max_abs) * 100.0
                indicator_cache[_tpf_sym]['0market_sentiment_local'] = _tpf_local
                indicator_cache[_tpf_sym]['0market_sentiment_score'] = _tpf_global_score
        # ═══════════════════════════════════════════════════════════════════════════
        # PORTFOLIO L/S RATIO (tradier path) — same as crypto path above.
        # ═══════════════════════════════════════════════════════════════════════════
        _tpf_long_val = 0.0
        _tpf_short_val = 0.0
        _tpf_positions = (manager.position_manager.positions if manager.position_manager else {})
        for _tpf_pk, _tpf_pos in _tpf_positions.items():
            _tpf_amt = abs(float(getattr(_tpf_pos, 'positionAmt', getattr(_tpf_pos, 'quantity', 0)) or 0))
            if _tpf_amt < 0.0001:
                continue
            _tpf_mp = float(getattr(_tpf_pos, 'mark_price', 0) or getattr(_tpf_pos, 'entry_price', 0) or 0)
            _tpf_notional = _tpf_amt * _tpf_mp
            if _tpf_pk.endswith('_LONG'):
                _tpf_long_val += _tpf_notional
            elif _tpf_pk.endswith('_SHORT'):
                _tpf_short_val += _tpf_notional
        _tpf_total_val = _tpf_long_val + _tpf_short_val
        _tpf_long_pct = (_tpf_long_val / _tpf_total_val * 100.0) if _tpf_total_val > 0 else 50.0
        _tpf_short_pct = (_tpf_short_val / _tpf_total_val * 100.0) if _tpf_total_val > 0 else 50.0
        _tpf_ls_ratio = _tpf_long_val / max(_tpf_short_val, 1.0)
        for _tpf_sym in list(indicator_cache.keys()):
            indicator_cache[_tpf_sym]['0long_pct'] = _tpf_long_pct
            indicator_cache[_tpf_sym]['0short_pct'] = _tpf_short_pct
            indicator_cache[_tpf_sym]['0ls_ratio'] = _tpf_ls_ratio
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
        if _research_ladder_adapter_t is not None:
            _ladder_replay_pk_t = (
                f"{account_key}:{_research_ladder_adapter_t.symbol}_"
                f"{_research_ladder_adapter_t.position_side}"
            )
            _ladder_replay_store_t = stores[
                _research_ladder_adapter_t.symbol
            ]
            _ladder_replay_idx_t = _ladder_replay_store_t.ts_to_idx[int(ts)]
            _ladder_replay_open_t = float(
                _ladder_replay_store_t.get(
                    "open_5m",
                    _ladder_replay_idx_t,
                    _ladder_replay_store_t.get(
                        "open", _ladder_replay_idx_t, 0.0
                    ),
                )
            )
            _ladder_replay_close_t = float(
                _ladder_replay_store_t.get(
                    "close_5m",
                    _ladder_replay_idx_t,
                    _ladder_replay_store_t.get(
                        "close", _ladder_replay_idx_t, 0.0
                    ),
                )
            )
            _ladder_actions_t = _research_ladder_adapter_t.actions_at(ts)

            def _ladder_runtime_qty_t():
                _position_t = (
                    manager.position_manager.positions.get(
                        _ladder_replay_pk_t
                    )
                    if manager.position_manager
                    else None
                )
                return abs(
                    float(
                        getattr(
                            _position_t,
                            "positionAmt",
                            getattr(_position_t, "quantity", 0.0),
                        )
                        or 0.0
                    )
                ) if _position_t is not None else 0.0

            # The vector ledger marks the final validation row before its
            # explicit end-of-window liquidation.
            if any(
                action.event_type == "MTM_FINAL"
                for action in _ladder_actions_t
            ):
                _research_ladder_adapter_t.observe_bar(
                    close_price=_ladder_replay_close_t,
                    position_qty=_ladder_runtime_qty_t(),
                )
            for _ladder_action_t in _ladder_actions_t:
                _ladder_raw_t = (
                    _ladder_replay_close_t
                    if _ladder_action_t.event_type == "MTM_FINAL"
                    else _ladder_replay_open_t
                )
                _ladder_actual_fill_t = (
                    _research_ladder_adapter_t.expected_fill_from_loaded_bar(
                        _ladder_action_t, _ladder_raw_t
                    )
                )
                if abs(
                    _ladder_actual_fill_t - _ladder_action_t.fill_price
                ) > max(
                    1e-9, abs(_ladder_action_t.fill_price) * 1e-10
                ):
                    raise RuntimeError(
                        "V8_RESEARCH_LADDER loaded-bar fill mismatch: "
                        f"ts={ts} event={_ladder_action_t.event_type}"
                    )
                _ladder_result_t = await _v8_execute_trade_action(
                    account_key=account_key,
                    position_key=_ladder_replay_pk_t,
                    symbol=_research_ladder_adapter_t.symbol,
                    quantity=_ladder_action_t.quantity,
                    current_price=_ladder_actual_fill_t,
                    side=_ladder_action_t.order_side,
                    position_side=_research_ladder_adapter_t.position_side,
                    action=_ladder_action_t.action,
                    reason=_ladder_action_t.reason,
                    is_full_close=_ladder_action_t.full_close,
                    is_hedge=False,
                )
                _ladder_emitted_t = (
                    executed_trades[-1] if executed_trades else {}
                )
                if not str(_ladder_emitted_t.get("reason", "")).startswith(
                    "V8_RESEARCH_BAND_LADDER_REPLAY"
                ):
                    raise RuntimeError(
                        "V8_RESEARCH_LADDER engine did not emit requested fill"
                    )
                _ladder_emitted_t["research_commission_bps_one_way"] = (
                    float(
                        _research_ladder_adapter_t.spec[
                            "commission_bps_one_way"
                        ]
                    )
                )
                _ladder_post_qty_t = _ladder_runtime_qty_t()
                _research_ladder_adapter_t.record_result(
                    _ladder_action_t,
                    result=_ladder_result_t,
                    actual_quantity=float(
                        _ladder_emitted_t.get("quantity", 0.0) or 0.0
                    ),
                    actual_price=float(
                        _ladder_emitted_t.get("price", 0.0) or 0.0
                    ),
                    post_position_qty=_ladder_post_qty_t,
                )
                if _ladder_result_t != "SUCCESS":
                    raise RuntimeError(
                        "V8_RESEARCH_LADDER action refused: "
                        f"ts={ts} event={_ladder_action_t.event_type} "
                        f"result={_ladder_result_t}"
                    )
            if not any(
                action.event_type == "MTM_FINAL"
                for action in _ladder_actions_t
            ):
                _research_ladder_adapter_t.observe_bar(
                    close_price=_ladder_replay_close_t,
                    position_qty=_ladder_runtime_qty_t(),
                )
            # Exact ladder replay owns the complete lifecycle for this run.
            continue
        if _research_adapter_t is not None:
            for _research_action_t in _research_adapter_t.actions_at(ts):
                _research_pk_t = (
                    f"{account_key}:{_research_adapter_t.symbol}_"
                    f"{_research_adapter_t.position_side}"
                )
                _research_store_t = stores[_research_adapter_t.symbol]
                _research_idx_t = _research_store_t.ts_to_idx[int(ts)]
                _research_raw_open_t = float(
                    _research_store_t.get(
                        "open_5m",
                        _research_idx_t,
                        _research_store_t.get("open", _research_idx_t, 0.0),
                    )
                )
                _research_raw_close_t = float(
                    _research_store_t.get("close", _research_idx_t, 0.0)
                )
                _research_actual_fill_t = (
                    _research_adapter_t.expected_fill_from_loaded_bar(
                        _research_action_t,
                        raw_open=_research_raw_open_t,
                        raw_close=_research_raw_close_t,
                    )
                )
                if abs(
                    _research_actual_fill_t - _research_action_t.fill_price
                ) > max(1e-9, abs(_research_action_t.fill_price) * 1e-10):
                    raise RuntimeError(
                        "V8_RESEARCH_TOP_EXIT loaded-bar fill mismatch: "
                        f"ts={ts} event={_research_action_t.event_type} "
                        f"schedule={_research_action_t.fill_price} "
                        f"loaded={_research_actual_fill_t} "
                        f"raw_open={_research_raw_open_t} raw_close={_research_raw_close_t}"
                    )
                _research_current_qty_t = 0.0
                if _research_action_t.full_close:
                    _research_pos_t = (
                        manager.position_manager.positions.get(_research_pk_t)
                        if manager.position_manager
                        else None
                    )
                    if _research_pos_t is None:
                        raise RuntimeError(
                            f"V8_RESEARCH_TOP_EXIT close while missing {_research_pk_t}"
                        )
                    _research_current_qty_t = abs(
                        float(
                            getattr(
                                _research_pos_t,
                                "positionAmt",
                                getattr(_research_pos_t, "quantity", 0),
                            )
                            or 0
                        )
                    )
                _research_qty_t = _research_adapter_t.quantity_for_action(
                    _research_action_t,
                    actual_fill_price=_research_actual_fill_t,
                    current_position_qty=_research_current_qty_t,
                )
                _research_result_t = await _v8_execute_trade_action(
                    account_key=account_key,
                    position_key=_research_pk_t,
                    symbol=_research_adapter_t.symbol,
                    quantity=float(_research_qty_t or 0),
                    current_price=_research_actual_fill_t,
                    side=_research_action_t.order_side,
                    position_side=_research_adapter_t.position_side,
                    action=_research_action_t.action,
                    reason=_research_action_t.reason,
                    is_full_close=_research_action_t.full_close,
                    is_hedge=False,
                )
                _research_emitted_t = executed_trades[-1] if executed_trades else {}
                _research_emitted_reason_t = str(
                    _research_emitted_t.get("reason", "")
                )
                if not _research_emitted_reason_t.startswith(
                    "V8_RESEARCH_TOP_EXIT_REPLAY"
                ):
                    raise RuntimeError(
                        "V8_RESEARCH_TOP_EXIT engine did not emit the requested fill"
                    )
                _research_emitted_qty_t = float(
                    _research_emitted_t.get("quantity", 0) or 0
                )
                _research_emitted_px_t = float(
                    _research_emitted_t.get("price", 0) or 0
                )
                _research_adapter_t.record_result(
                    _research_action_t,
                    result=_research_result_t,
                    actual_quantity=_research_emitted_qty_t,
                    actual_price=_research_emitted_px_t,
                )
                if _research_result_t != "SUCCESS":
                    raise RuntimeError(
                        "V8_RESEARCH_TOP_EXIT action refused: "
                        f"ts={ts} event={_research_action_t.event_type} "
                        f"result={_research_result_t}"
                    )
            # Exact replay owns the complete position lifecycle.  All ordinary
            # strategy, exit, and reentry paths remain dormant for this run.
            continue
        # Ladder stage 0/1 needs an unambiguous buy-and-hold floor. The old
        # closes==0 shortcut could not distinguish "held" from "never opened".
        # This test-only hook seeds one benchmark unit on the first RTH bar.
        _ladder_side_t = os.environ.get("V8_LADDER_FORCE_INITIAL_SIDE", "").upper()
        if _ladder_side_t in ("LONG", "SHORT") and not _ladder_initial_seeded_t:
            for _seed_sym_t in stores:
                _seed_px_t = float(price_cache.get(_seed_sym_t.upper(), 0) or 0)
                if _seed_px_t <= 0:
                    continue
                _seed_pk_t = f"{account_key}:{_seed_sym_t}_{_ladder_side_t}"
                # Capital contract: B&H deploys $2k at $10k accounting capital.
                _seed_qty_t = float(
                    _capital_contract_t["benchmark_deployed_usd"]
                ) / _seed_px_t
                _seed_result_t = await _v8_execute_trade_action(
                    account_key=account_key,
                    position_key=_seed_pk_t,
                    symbol=_seed_sym_t,
                    quantity=_seed_qty_t,
                    current_price=_seed_px_t,
                    side="BUY" if _ladder_side_t == "LONG" else "SELL",
                    position_side=_ladder_side_t,
                    action="OPEN",
                    reason="V8_LADDER_INITIAL_BH_SEED",
                    is_full_close=False,
                    is_hedge=False,
                )
                if _seed_result_t != "SUCCESS":
                    v8_logger.error(
                        f"[V8_LADDER_SEED_REFUSED] {_seed_pk_t}: "
                        f"result={_seed_result_t}"
                    )
                    continue
                _ladder_initial_seeded_t = True
                print(
                    f"V8_LADDER_SEED: symbol={_seed_sym_t} side={_ladder_side_t} "
                    f"price={_seed_px_t:.6f} qty={_seed_qty_t:.6f}",
                    flush=True,
                )
                break
        # 2026-07-09 MINERVINI/CLENOW hook (see block above the loop): real evaluate_*
        # at live 1800s cadence, then drain the order queue so fires execute at THIS
        # bar's price (the main drain below is skipped when all_keys is empty).
        if (_v8_minervini_on or _v8_clenow_on) and not bool(getattr(tm_mod.config, 'PARITY_COMPARISON_MODE', False)):
            _v8_strat_fired = False
            if _v8_clenow_on and ts - _v8_strat_last['clenow'] >= 1800:
                _v8_strat_last['clenow'] = ts
                try:
                    await manager.evaluate_clenow_entry(account_key)
                    _v8_strat_fired = True
                except Exception as _v8_cln_e:
                    v8_logger.warning(f"[V8_CLENOW] evaluate_clenow_entry error: {_v8_cln_e}")
            if _v8_minervini_on and ts - _v8_strat_last['minervini'] >= 1800:
                _v8_strat_last['minervini'] = ts
                try:
                    await manager.evaluate_minervini_entry(account_key)
                    _v8_strat_fired = True
                except Exception as _v8_min_e:
                    v8_logger.warning(f"[V8_MINERVINI] evaluate_minervini_entry error: {_v8_min_e}")
            if _v8_strat_fired:
                _v8_strat_oq = manager.order_queue
                if hasattr(_v8_strat_oq, '_orders'):
                    while not _v8_strat_oq._orders.empty():
                        try:
                            _v8_strat_ord = _v8_strat_oq._orders.get_nowait()
                            await _v8_strat_oq.handle_order(_v8_strat_ord)
                            _v8_strat_oq._orders.task_done()
                        except Exception:
                            break
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
                # WT force-open is evaluated inside the real
                # tradier_manage.process_position zero-position branch. The old
                # prefilter required SATOSHIT to pass before process_position
                # was called, so WT force-open could never evaluate the flat
                # keys it was designed to open (all normal backtests reported
                # zero trades). Route enabled sides to the real function and
                # let its SMA/WT/fresh-cross/gate checks make the decision.
                try:
                    _wf_long_enabled = bool(tm_mod._cfg(
                        'WT_3M_FORCE_OPEN_ENABLED', True,
                        account_key, s, 'LONG',
                    ))
                    _wf_short_enabled = bool(tm_mod._cfg(
                        'WT_3M_FORCE_OPEN_ENABLED', True,
                        account_key, s, 'SHORT',
                    ))
                except Exception:
                    _wf_long_enabled = bool(getattr(
                        tm_mod.config, 'WT_3M_FORCE_OPEN_ENABLED', True
                    ))
                    _wf_short_enabled = _wf_long_enabled
                if pk_l not in open_keys and _is_long_ok and manager.is_symbol_tradeable(
                    s, account_key, "LONG"
                ) and (
                    _sat_long_ok or _stdev_long_ok or _wf_long_enabled
                ):
                    cand_keys.append(pk_l)
                if pk_s not in open_keys and _is_short_ok and manager.is_symbol_tradeable(
                    s, account_key, "SHORT"
                ) and (
                    _sat_short_ok or _stdev_short_ok or _wf_short_enabled
                ):
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
        # DC stop loss sweep test (DC_LOW4_STOP_ENABLED / DC_LOW_STOP_ENABLED) — tradier path.
        _dc4_stop_on_tr = getattr(tm_mod.config, 'DC_LOW4_STOP_ENABLED', False)
        _dc1_stop_on_tr = getattr(tm_mod.config, 'DC_LOW_STOP_ENABLED', False)
        if (_dc4_stop_on_tr or _dc1_stop_on_tr) and manager.position_manager:
            for _ds_pk_tr, _ds_pos_tr in list(manager.position_manager.positions.items()):
                if abs(getattr(_ds_pos_tr, 'positionAmt', 0)) < 0.0001:
                    continue
                _ds_sym_tr = getattr(_ds_pos_tr, 'symbol', '') or _ds_pk_tr.split(':', 1)[-1].rsplit('_', 1)[0]
                _ds_is_long_tr = _ds_pk_tr.endswith('_LONG')
                _ds_ind_tr = indicator_cache.get(_ds_sym_tr.upper(), {}) if isinstance(indicator_cache, dict) else {}
                _ds_px_tr = float(price_cache.get(_ds_sym_tr.upper(), 0) or _ds_ind_tr.get('current_price', 0) or 0)
                if _ds_px_tr <= 0:
                    continue
                _ds_stop_tr = float(getattr(_ds_pos_tr, 'r1_stop_price', 0.0) or 0.0)
                if _ds_stop_tr <= 0:
                    if _dc4_stop_on_tr:
                        _ds_stop_tr = float(_ds_ind_tr.get('dc_low4_5m' if _ds_is_long_tr else 'dc_high4_5m') or 0)
                    if _ds_stop_tr <= 0 and _dc1_stop_on_tr:
                        _ds_stop_tr = float(_ds_ind_tr.get('dc_low_5m' if _ds_is_long_tr else 'dc_high_5m') or 0)
                if _ds_stop_tr <= 0:
                    continue
                _ds_breached_tr = (_ds_is_long_tr and _ds_px_tr <= _ds_stop_tr) or ((not _ds_is_long_tr) and _ds_px_tr >= _ds_stop_tr)
                if _ds_breached_tr:
                    _ds_amt_tr = abs(float(getattr(_ds_pos_tr, 'positionAmt', 0)))
                    _ds_side_tr = 'SELL' if _ds_is_long_tr else 'BUY'
                    _ds_gr_hedge_on_tr = getattr(tm_mod.config, 'DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED', False)
                    _ds_did_hedge_tr = False
                    if _ds_gr_hedge_on_tr:
                        try:
                            from golden_rule_htf import score_entry_htf as _ds_gr_fn_tr
                            _ds_gr_min_tfs_tr = int(getattr(tm_mod.config, 'DC4_STOP_GR_SCORE_MIN_TFS', 3))
                            _ds_gr_min_ind_tr = int(getattr(tm_mod.config, 'DC4_STOP_GR_SCORE_MIN_IND', 5))
                            _ds_gr_passes_tr, _ds_gr_n_tr, _ds_gr_det_tr = _ds_gr_fn_tr(
                                _ds_ind_tr, not _ds_is_long_tr, 'tradier',
                                _ds_gr_min_tfs_tr, _ds_gr_min_ind_tr, _ds_px_tr)
                            if _ds_gr_passes_tr:
                                await hedge_engine.scan_and_hedge_losers(account_key)
                                _ds_did_hedge_tr = True
                                v8_logger.warning(f"[DC4_STOP_GR_HEDGE_TR] {_ds_pk_tr}: DC4 breach but GR={_ds_gr_n_tr}tfs>={_ds_gr_min_tfs_tr} against — HEDGE not stop. {_ds_gr_det_tr}")
                        except Exception as _ds_ge_tr:
                            v8_logger.debug(f"[DC4_STOP_GR_ERR_TR] {_ds_pk_tr}: {_ds_ge_tr}")
                    if not _ds_did_hedge_tr:
                        try:
                            await manager.execute_now(
                                position_key=_ds_pk_tr, account_key=account_key, symbol=_ds_sym_tr,
                                original_positionAmt=_ds_amt_tr, side=_ds_side_tr,
                                position_side='LONG' if _ds_is_long_tr else 'SHORT',
                                quantity=_ds_amt_tr, old_price=_ds_px_tr,
                                unique_id=f"DC_STOP_TR_{int(step)}",
                                reason=f"DC_STOP_BREACH_px{_ds_px_tr:.4f}_stop{_ds_stop_tr:.4f}",
                                is_full_close=True, action='CLOSE')
                        except Exception:
                            pass
        if manager.position_manager:
            for _se_pk_tr, _se_pos_tr in list(manager.position_manager.positions.items()):
                if abs(getattr(_se_pos_tr, 'positionAmt', 0)) < 0.0001: continue
                _se_sym_tr = getattr(_se_pos_tr, 'symbol', '') or _se_pk_tr.split(':', 1)[-1].rsplit('_', 1)[0]
                _se_is_long_tr = _se_pk_tr.endswith('_LONG')
                _se_tf_tr = getattr(tm_mod.config, 'EXIT_STRUCT_TF', 'None')
                if _se_tf_tr == 'None' or not _se_tf_tr: _se_tf_tr = getattr(tm_mod.config, 'LONG_STRUCT_EXIT_TF' if _se_is_long_tr else 'SHORT_STRUCT_EXIT_TF', 'None')
                if _se_tf_tr != 'None' and _se_tf_tr:
                    _se_ind_tr = indicator_cache.get(_se_sym_tr.upper(), {}) if isinstance(indicator_cache, dict) else {}
                    _se_open_tr = float(_se_ind_tr.get(f"open_{_se_tf_tr}", 0.0) or 0.0)
                    _se_high_prev_tr = float(_se_ind_tr.get(f"high_{_se_tf_tr}_prev", 0.0) or 0.0)
                    _se_low_prev_tr = float(_se_ind_tr.get(f"low_{_se_tf_tr}_prev", 0.0) or 0.0)
                    if _se_open_tr > 0 and _se_high_prev_tr > 0 and _se_low_prev_tr > 0:
                        _last_open_tr = getattr(_se_pos_tr, "_last_struct_open", None)
                        if _last_open_tr is None:
                            setattr(_se_pos_tr, "_last_struct_open", _se_open_tr); setattr(_se_pos_tr, "_last_high_prev", _se_high_prev_tr); setattr(_se_pos_tr, "_last_low_prev", _se_low_prev_tr)
                        elif abs(_se_open_tr - _last_open_tr) > 1e-8:
                            _last_hp_tr = getattr(_se_pos_tr, "_last_high_prev", _se_high_prev_tr); _last_lp_tr = getattr(_se_pos_tr, "_last_low_prev", _se_low_prev_tr)
                            _se_fire_tr = (_se_high_prev_tr < _last_hp_tr and _se_low_prev_tr < _last_lp_tr) if _se_is_long_tr else (_se_high_prev_tr > _last_hp_tr and _se_low_prev_tr > _last_lp_tr)
                            setattr(_se_pos_tr, "_last_struct_open", _se_open_tr); setattr(_se_pos_tr, "_last_high_prev", _se_high_prev_tr); setattr(_se_pos_tr, "_last_low_prev", _se_low_prev_tr)
                            if _se_fire_tr:
                                try:
                                    _se_amt_tr = abs(float(getattr(_se_pos_tr, 'positionAmt', 0)))
                                    v8_logger.warning(f"⛔ [HYBRID_STRUCT_EXIT_TR] {_se_pk_tr}: structure breakdown on {_se_tf_tr} (high_prev={_se_high_prev_tr:.6f} vs last_hp={_last_hp_tr:.6f}, low_prev={_se_low_prev_tr:.6f} vs last_lp={_last_lp_tr:.6f}) → CLOSE")
                                    await manager.execute_now(position_key=_se_pk_tr, account_key=account_key, symbol=_se_sym_tr, original_positionAmt=_se_amt_tr, side=("SELL" if _se_is_long_tr else "BUY"), position_side=('LONG' if _se_is_long_tr else 'SHORT'), quantity=_se_amt_tr, old_price=float(price_cache.get(_se_sym_tr.upper(), 0) or _se_ind_tr.get('current_price', 0) or 0), unique_id=f"HYBRID_STRUCT_EXIT_TR_{int(step)}", reason=f"HYBRID_STRUCT_EXIT_{_se_tf_tr}_g{getattr(_se_pos_tr, 'gain', 0):.2f}%", is_full_close=True, action="CLOSE")
                                except Exception: pass
        # ═══════════════════════════════════════════════════════════════════════
        # Generalized frozen stop (tradier only) — DC and BB variants.
        # DC: DC_LOW_FROZEN_STOP_ENABLED + DC_LOW_FROZEN_STOP_TF + DC_LOW_FROZEN_STOP_USE_4BAR
        #     Backward compat: DC_LOW_4H_FROZEN_STOP_ENABLED still works (maps to TF='4h').
        # BB: BB_FROZEN_STOP_ENABLED + BB_FROZEN_STOP_TF + BB_FROZEN_STOP_FIELD (lower/upper/basis)
        # Both: DC_LOW_FROZEN_STOP_FLOOR_PCT applies as absolute loss floor.
        # Fires BEFORE process_position (R1/R2) to isolate stop mechanism in sweep.
        # ═══════════════════════════════════════════════════════════════════════
        # 2026-07-20 PARITY FIX: this is the TRADIER sim — knobs MUST come from tm_mod.config
        # (config_tradier), NOT the crypto config module. Crypto BB_FROZEN_STOP_ENABLED=True/1h
        # was silently stopping every losing stock long at bb_lower_1h while live stocks
        # (config_tradier: False) held — poisoned all campaign baselines and shadowed D-stop arms.
        _dc_fstop_on = getattr(tm_mod.config, 'DC_LOW_FROZEN_STOP_ENABLED', False) or getattr(tm_mod.config, 'DC_LOW_4H_FROZEN_STOP_ENABLED', False)
        _bb_fstop_on = getattr(tm_mod.config, 'BB_FROZEN_STOP_ENABLED', False)
        _dyn_strail_on = bool(getattr(tm_mod.config, 'DYN_STRUCT_TRAIL_ENABLED', False))
        _dyn_strail_tf = str(getattr(tm_mod.config, 'DYN_STRUCT_TRAIL_TF', '4h'))
        _dyn_strail_min_gain = float(getattr(tm_mod.config, 'DYN_STRUCT_TRAIL_MIN_GAIN_PCT', 0.0))
        _fstop_floor = float(getattr(tm_mod.config, 'DC_LOW_FROZEN_STOP_FLOOR_PCT', getattr(tm_mod.config, 'DC_LOW_4H_ABS_LOSS_FLOOR_PCT', -999.0)))
        if (_dc_fstop_on or _bb_fstop_on or _dyn_strail_on) and manager.position_manager:
            _dc_fstop_tf = str(getattr(tm_mod.config, 'DC_LOW_FROZEN_STOP_TF', '4h'))
            _dc_fstop_4bar = bool(getattr(tm_mod.config, 'DC_LOW_FROZEN_STOP_USE_4BAR', False))
            _bb_fstop_tf = str(getattr(tm_mod.config, 'BB_FROZEN_STOP_TF', '1h'))
            _bb_fstop_field = str(getattr(tm_mod.config, 'BB_FROZEN_STOP_FIELD', 'lower'))
            for _fsp_pk, _fsp_pos in list(manager.position_manager.positions.items()):
                if abs(getattr(_fsp_pos, 'positionAmt', 0)) < 0.0001:
                    _dc_fstop_tr.pop(_fsp_pk, None)
                    _bb_fstop_tr.pop(_fsp_pk, None)
                    _dyn_strail_tr.pop(_fsp_pk, None)
                    continue
                _fsp_sym = getattr(_fsp_pos, 'symbol', '') or _fsp_pk.split(':', 1)[-1].rsplit('_', 1)[0]
                _fsp_ind = indicator_cache.get(_fsp_sym.upper(), {}) if isinstance(indicator_cache, dict) else {}
                _fsp_px = float(price_cache.get(_fsp_sym.upper(), 0) or _fsp_ind.get('current_price', 0) or 0)
                if _fsp_px <= 0:
                    continue
                _fsp_is_long = _fsp_pk.endswith('_LONG')
                _fsp_ep = float(getattr(_fsp_pos, 'entry_price', 0) or 0)
                if _fsp_ep <= 0:
                    continue
                _fsp_gain = ((_fsp_px - _fsp_ep) / _fsp_ep * 100.0) if _fsp_is_long else ((_fsp_ep - _fsp_px) / _fsp_ep * 100.0)
                _fsp_fire = _fsp_gain < _fstop_floor
                _fsp_tag = 'ABS_FLOOR'
                if _dc_fstop_on and not _fsp_fire:
                    if _fsp_pk not in _dc_fstop_tr:
                        _dcf_key = (f'dc_low4_{_dc_fstop_tf}' if (_fsp_is_long and _dc_fstop_4bar) else
                                    f'dc_high4_{_dc_fstop_tf}' if (not _fsp_is_long and _dc_fstop_4bar) else
                                    f'dc_low_{_dc_fstop_tf}' if _fsp_is_long else f'dc_high_{_dc_fstop_tf}')
                        _dcf_val = float(_fsp_ind.get(_dcf_key, 0) or 0)
                        if _dcf_val > 0:
                            _dc_fstop_tr[_fsp_pk] = _dcf_val
                    _dc_level = _dc_fstop_tr.get(_fsp_pk, 0)
                    if _dc_level > 0 and _fsp_gain < 0:
                        if (_fsp_is_long and _fsp_px < _dc_level) or (not _fsp_is_long and _fsp_px > _dc_level):
                            _fsp_fire = True
                            _fsp_tag = f'DC{"4" if _dc_fstop_4bar else ""}_{_dc_fstop_tf}'
                if _bb_fstop_on and not _fsp_fire:
                    if _fsp_pk not in _bb_fstop_tr:
                        if _bb_fstop_field == 'basis':
                            _bbf_u = float(_fsp_ind.get(f'bb_upper_{_bb_fstop_tf}', 0) or 0)
                            _bbf_l = float(_fsp_ind.get(f'bb_lower_{_bb_fstop_tf}', 0) or 0)
                            _bbf_val = (_bbf_u + _bbf_l) / 2.0 if _bbf_u > 0 and _bbf_l > 0 else 0.0
                            _bbf_key = f'bb_basis_{_bb_fstop_tf}'
                        else:
                            _bbf_field_name = ('lower' if _fsp_is_long else 'upper') if _bb_fstop_field in ('lower', 'upper') else _bb_fstop_field
                            _bbf_key = f'bb_{_bbf_field_name}_{_bb_fstop_tf}'
                            _bbf_val = float(_fsp_ind.get(_bbf_key, 0) or 0)
                        if _bbf_val > 0:
                            _bb_fstop_tr[_fsp_pk] = _bbf_key
                    _bb_key = _bb_fstop_tr.get(_fsp_pk)
                    if _bb_key:
                        if _bb_fstop_field == 'basis':
                            _bbu = float(_fsp_ind.get(f'bb_upper_{_bb_fstop_tf}', 0) or 0)
                            _bbl = float(_fsp_ind.get(f'bb_lower_{_bb_fstop_tf}', 0) or 0)
                            _cur_bb = (_bbu + _bbl) / 2.0 if _bbu > 0 and _bbl > 0 else 0.0
                        else:
                            _cur_bb = float(_fsp_ind.get(_bb_key, 0) or 0)
                        if _cur_bb > 0 and _fsp_gain < 0:
                            if (_fsp_is_long and _fsp_px < _cur_bb) or (not _fsp_is_long and _fsp_px > _cur_bb):
                                _fsp_fire = True
                                _fsp_tag = f'BB_{_bb_fstop_field.upper()}_{_bb_fstop_tf}'
                if _dyn_strail_on and not _fsp_fire:
                    _dst_key = f'dc_low_{_dyn_strail_tf}' if _fsp_is_long else f'dc_high_{_dyn_strail_tf}'
                    _dst_cur = float(_fsp_ind.get(_dst_key, 0) or 0)
                    _dst_st = _dyn_strail_tr.get(_fsp_pk)
                    if _dst_st is None:
                        _dst_st = {'lvl': 0.0, 'armed': _dyn_strail_min_gain <= 0.0}
                        _dyn_strail_tr[_fsp_pk] = _dst_st
                    if _fsp_gain >= _dyn_strail_min_gain:
                        _dst_st['armed'] = True
                    if _dst_cur > 0:
                        if _dst_st['lvl'] <= 0:
                            _dst_st['lvl'] = _dst_cur
                        elif _fsp_is_long and _dst_cur > _dst_st['lvl']:
                            _dst_st['lvl'] = _dst_cur
                        elif (not _fsp_is_long) and _dst_cur < _dst_st['lvl']:
                            _dst_st['lvl'] = _dst_cur
                    if _dst_st['armed'] and _dst_st['lvl'] > 0:
                        if (_fsp_is_long and _fsp_px < _dst_st['lvl']) or ((not _fsp_is_long) and _fsp_px > _dst_st['lvl']):
                            _fsp_fire = True
                            _fsp_tag = f'DYN_STRUCT_TRAIL_{_dyn_strail_tf}'
                if _fsp_fire:
                    _dc_fstop_tr.pop(_fsp_pk, None)
                    _bb_fstop_tr.pop(_fsp_pk, None)
                    _dyn_strail_tr.pop(_fsp_pk, None)
                    try:
                        _fsp_amt = abs(float(getattr(_fsp_pos, 'positionAmt', 0)))
                        await manager.execute_now(
                            position_key=_fsp_pk, account_key=account_key, symbol=_fsp_sym,
                            original_positionAmt=_fsp_amt,
                            side='SELL' if _fsp_is_long else 'BUY',
                            position_side='LONG' if _fsp_is_long else 'SHORT',
                            quantity=_fsp_amt, old_price=_fsp_px,
                            unique_id=f"FSTOP_{_fsp_tag}_{int(step)}",
                            reason=f"FROZEN_STOP_{_fsp_tag}_px{_fsp_px:.4f}_g{_fsp_gain:.2f}pct",
                            is_full_close=True, action='CLOSE')
                    except Exception:
                        pass
        # ═══════════════════════════════════════════════════════════════════════
        # DISC-MTF_ATR_TRAIL (tradier) — live-parity MTF_ATR_TRAIL ratchet (2026-07-10)
        # Mirror of the crypto DISC block (run_simulation ~:4183) via shared
        # vec_paths/mtf_atr_trail.py. Live site: tradier_manage.py:2491-2505 inside
        # process_position — INERT in backtest because its gate compares
        # _mtfce_startup_ts=time.time() (wall clock) against opened_at=sim time,
        # so this DISC block is the ONLY firer here (no double-fire).
        # Gates identical to live: MTF_EXIT_USE_COMPOUND AND MTF_ATR_TRAIL_ENABLED
        # (config_tradier.py:2321/2322 — BOTH currently True → live stocks HAS this
        # exit; engine lacked it until now = parity restoration).
        # TF=MTF_ATR_TRAIL_TF_TRADIER (config '5m'), mult=MTF_ATR_TRAIL_MULT (2.0).
        # Reason MTF_ATR_TRAIL_* is in config_tradier.LOSS_EXIT_TECHNICAL_BYPASS.
        # Knobs read from tm_mod.config (TradierConfig instance = what live _cfg reads
        # and what the tradier override loader above sets) — NOT the module-level
        # crypto `config`, whose MTF_ATR_TRAIL_TF would wrongly yield 15m here.
        # ═══════════════════════════════════════════════════════════════════════
        if bool(getattr(tm_mod.config, 'MTF_EXIT_USE_COMPOUND', False)) and bool(getattr(tm_mod.config, 'MTF_ATR_TRAIL_ENABLED', False)) and manager.position_manager:
            try:
                from vec_paths.mtf_atr_trail import update_and_check as _mtfat_check_tr, mtf_atr_trail_tf as _mtfat_tf_fn_tr
                _mtfat_states_tr = manager.__dict__.setdefault('mtf_compound_exit_state', {})
                _mtfat_atr_tf_tr = _mtfat_tf_fn_tr(tm_mod.config, 'tradier')
                _mtfat_mult_tr = float(getattr(tm_mod.config, 'MTF_ATR_TRAIL_MULT', 2.5))
                for _mtfat_pk_tr, _mtfat_pos_tr in list(manager.position_manager.positions.items()):
                    if abs(getattr(_mtfat_pos_tr, 'positionAmt', 0)) < 0.0001:
                        _mtfat_states_tr.pop(_mtfat_pk_tr, None)
                        continue
                    _mtfat_sym_tr = getattr(_mtfat_pos_tr, 'symbol', '') or _mtfat_pk_tr.split(':', 1)[-1].rsplit('_', 1)[0]
                    _mtfat_ind_tr = indicator_cache.get(_mtfat_sym_tr.upper(), {}) if isinstance(indicator_cache, dict) else {}
                    if not _mtfat_ind_tr:
                        continue
                    _mtfat_px_tr = float(price_cache.get(_mtfat_sym_tr.upper(), 0) or _mtfat_ind_tr.get('current_price', 0) or 0)
                    if _mtfat_px_tr <= 0:
                        continue
                    _mtfat_atr_tr = float(_mtfat_ind_tr.get(f'atr_{_mtfat_atr_tf_tr}', 0) or 0)
                    _mtfat_entry_tr = float(getattr(_mtfat_pos_tr, 'entry_price', 0) or 0)
                    _mtfat_is_long_tr = _mtfat_pk_tr.endswith('_LONG')
                    _mtfat_state_tr = _mtfat_states_tr.setdefault(_mtfat_pk_tr, {'trail': 0.0})
                    _mtfat_fire_tr, _mtfat_reason_tr, _ = _mtfat_check_tr(_mtfat_state_tr, _mtfat_entry_tr, _mtfat_px_tr, _mtfat_atr_tr, _mtfat_is_long_tr, _mtfat_mult_tr, _mtfat_atr_tf_tr)
                    if _mtfat_fire_tr:
                        _mtfat_states_tr.pop(_mtfat_pk_tr, None)
                        try:
                            _mtfat_amt_tr = abs(float(getattr(_mtfat_pos_tr, 'positionAmt', 0)))
                            await manager.execute_now(position_key=_mtfat_pk_tr, account_key=account_key, symbol=_mtfat_sym_tr, original_positionAmt=_mtfat_amt_tr, side='SELL' if _mtfat_is_long_tr else 'BUY', position_side='LONG' if _mtfat_is_long_tr else 'SHORT', quantity=_mtfat_amt_tr, old_price=_mtfat_px_tr, unique_id=f"MTF_ATR_TRAIL_{int(step)}", reason=_mtfat_reason_tr, is_full_close=True, action='CLOSE')
                        except Exception:
                            pass
            except Exception:
                pass
        _pp_results_t = await asyncio.gather(*[
            tm_mod.process_position(
                account_key, pk, manager.order_queue, manager,
                event_type="backtest", force=True,
            )
            for pk in all_keys
        ], return_exceptions=True)
        if _candidate_diag_t < 5:
            v8_logger.info(
                f"[V8_CANDIDATE_DIAG] step={step} open={len(open_keys)} "
                f"cand={len(cand_keys)} keys={all_keys} "
                f"results={[str(x)[:120] for x in _pp_results_t]} "
                f"queue_size={manager.order_queue._orders.qsize() if hasattr(manager.order_queue, '_orders') else 'NA'}"
            )
            _candidate_diag_t += 1
        for _pp_pk_t, _pp_result_t in zip(all_keys, _pp_results_t):
            if isinstance(_pp_result_t, BaseException):
                # Never silently convert a broken entry/exit path into a
                # zero-trade market result. The prior gather discarded every
                # process_position exception, hiding mandatory WT entry bugs.
                v8_logger.error(
                    f"[V8_PROCESS_POSITION_ERROR] {_pp_pk_t}: "
                    f"{type(_pp_result_t).__name__}: {_pp_result_t}"
                )
        oq = manager.order_queue
        if hasattr(oq, '_orders'):
            while not oq._orders.empty():
                try:
                    order = oq._orders.get_nowait()
                    await oq.handle_order(order)
                    oq._orders.task_done()
                except: break
        # ═══════════════════════════════════════════════════════════════════════════
        # SENTIMENT_REBALANCER (tradier path) — 2026-05-12
        # Live: periodic_sentiment_rebalancing runs every 60s → fires SENTIMENT_FADE
        # and SENTIMENT_BOOST by comparing ideal_qty (from calculate_quantity_complex)
        # to current positionAmt. Run every ~12 bars (≈60s at 5m bars) to replicate
        # live cadence. Gate: V8_SENTIMENT_REBALANCER_ENABLED (default 1).
        # ═══════════════════════════════════════════════════════════════════════════
        _v8_sent_rebal_enabled = os.environ.get("V8_SENTIMENT_REBALANCER_ENABLED", "1") == "1" and bool(getattr(tm_mod.config, "SENTIMENT_REBALANCER_ENABLED", True))  # 2026-05-29 PARITY FIX #1: mirror live — tradier config SENTIMENT_REBALANCER_ENABLED=False (rebalancer KILLED) => 0 phantom stock SENTIMENT_FADE/BOOST; crypto (attr undefined => True) unchanged
        _v8_sent_rebal_freq = int(os.environ.get("V8_SENTIMENT_REBALANCER_FREQ_BARS", "12"))
        if _v8_sent_rebal_enabled and step % _v8_sent_rebal_freq == 0 and hasattr(manager, 'periodic_sentiment_rebalancing'):
            try:
                _v8_rebal_positions = list((manager.position_manager.positions if manager.position_manager else {}).items())
                for _v8_rebal_pk, _v8_rebal_pos in _v8_rebal_positions:
                    try:
                        if not _v8_rebal_pos or abs(getattr(_v8_rebal_pos, 'positionAmt', 0)) == 0:
                            continue
                        _v8_rebal_sym = getattr(_v8_rebal_pos, 'symbol', '')
                        if not _v8_rebal_sym:
                            continue
                        _v8_rebal_side = getattr(_v8_rebal_pos, 'position_side', 'LONG')
                        _v8_rebal_cur_qty = abs(float(getattr(_v8_rebal_pos, 'positionAmt', 0)))
                        _v8_rebal_px = price_cache.get(_v8_rebal_sym.upper(), 0)
                        if _v8_rebal_px <= 0:
                            continue
                        _v8_rebal_i = indicator_cache.get(_v8_rebal_sym.upper(), {})
                        if not _v8_rebal_i:
                            continue
                        _v8_rebal_base_qty = tm_mod.config.START_POSITION_SIZE / _v8_rebal_px
                        if not hasattr(manager, 'strategy') or manager.strategy is None:
                            continue
                        _v8_ideal_qty = await manager.strategy.calculate_quantity_complex(
                            _v8_rebal_sym, "REBALANCE", _v8_rebal_side, _v8_rebal_base_qty, _v8_rebal_i, _v8_rebal_pos)
                        if _v8_rebal_cur_qty == 0:
                            continue
                        _v8_dev_pct = (_v8_ideal_qty - _v8_rebal_cur_qty) / _v8_rebal_cur_qty
                        if _v8_dev_pct < -0.20:
                            _v8_qty_to_reduce = _v8_rebal_cur_qty - _v8_ideal_qty
                            if _v8_qty_to_reduce * _v8_rebal_px > 100:
                                _v8_gain_pct = float(getattr(_v8_rebal_pos, 'gain', 0) or 0)
                                if _v8_gain_pct < 0.3:
                                    continue
                                _v8_wt_b5 = float(_v8_rebal_i.get('wt1_5m', 0) or 0) > float(_v8_rebal_i.get('wt2_5m', 0) or 0)
                                _v8_wt_b15 = float(_v8_rebal_i.get('wt1_15m', 0) or 0) > float(_v8_rebal_i.get('wt2_15m', 0) or 0)
                                _v8_wt_b1h = float(_v8_rebal_i.get('wt1_1h', 0) or 0) > float(_v8_rebal_i.get('wt2_1h', 0) or 0)
                                _v8_wt_b4h = float(_v8_rebal_i.get('wt1_4h', 0) or 0) > float(_v8_rebal_i.get('wt2_4h', 0) or 0)
                                if _v8_rebal_side == "LONG":
                                    _v8_tf_against = sum([1 for b in [_v8_wt_b5, _v8_wt_b15, _v8_wt_b1h, _v8_wt_b4h] if not b])
                                else:
                                    _v8_tf_against = sum([1 for b in [_v8_wt_b5, _v8_wt_b15, _v8_wt_b1h, _v8_wt_b4h] if b])
                                if _v8_tf_against < 2:
                                    continue
                                _v8_fade_reason = f"SENTIMENT_FADE ideal={int(_v8_ideal_qty)} cur={int(_v8_rebal_cur_qty)} loc={_v8_rebal_i.get('0market_sentiment_local', 0):.1f} WT{_v8_tf_against}TF"
                                _v8_acct = _v8_rebal_pk.split(':')[0] if ':' in _v8_rebal_pk else account_key
                                await manager.execute_trade_action(
                                    _v8_acct, _v8_rebal_pk, _v8_rebal_sym, _v8_qty_to_reduce, _v8_rebal_px,
                                    "SELL" if _v8_rebal_side == "LONG" else "BUY",
                                    _v8_rebal_side, f"rebal_{step}",
                                    action="REDUCE", reason=_v8_fade_reason, override_qty=_v8_qty_to_reduce)
                        elif _v8_dev_pct > 0.25:
                            _v8_qty_to_add = _v8_ideal_qty - _v8_rebal_cur_qty
                            _v8_gain_pct = float(getattr(_v8_rebal_pos, 'gain', 0) or 0)
                            if _v8_gain_pct > 0.5 and _v8_qty_to_add * _v8_rebal_px > 100:
                                _v8_boost_reason = f"SENTIMENT_BOOST ideal={int(_v8_ideal_qty)} cur={int(_v8_rebal_cur_qty)} loc={_v8_rebal_i.get('0market_sentiment_local', 0):.1f}"
                                _v8_acct = _v8_rebal_pk.split(':')[0] if ':' in _v8_rebal_pk else account_key
                                await manager.execute_trade_action(
                                    _v8_acct, _v8_rebal_pk, _v8_rebal_sym, _v8_qty_to_add, _v8_rebal_px,
                                    "BUY" if _v8_rebal_side == "LONG" else "SELL",
                                    _v8_rebal_side, f"rebal_{step}",
                                    action="AUGMENT", reason=_v8_boost_reason, override_qty=_v8_qty_to_add)
                    except Exception as _v8_rebal_inner_err:
                        if step < 10 or step % 1000 == 0:
                            v8_logger.error(f"[V8_SENTIMENT_REBAL_INNER] {_v8_rebal_pk}: {_v8_rebal_inner_err}")
            except Exception as _v8_rebal_err:
                if step < 10 or step % 1000 == 0:
                    v8_logger.error(f"[V8_SENTIMENT_REBAL_ERR] step={step}: {_v8_rebal_err}")
        # DISC-W_EXIT (tradier): exit when weekly WaveTrend crosses against position (2026-05-15)
        if bool(getattr(tm_mod.config, 'WT_W_EXIT_ENABLED', False)):
            for _we_pk, _we_pos in list(manager.positions.items()):
                if abs(getattr(_we_pos, 'positionAmt', 0) or 0) < 0.0001:
                    continue
                _we_sym = (getattr(_we_pos, 'symbol', '') or
                           (_we_pk.split(':', 1)[-1].rsplit('_', 1)[0] if ':' in _we_pk
                            else (_we_pk[:-5] if _we_pk.endswith('_LONG') else _we_pk[:-6])))
                _we_ind_t = indicator_cache.get(_we_sym.upper(), {}) if isinstance(indicator_cache, dict) else {}
                if not _we_ind_t:
                    continue
                _we_wt1 = float(_we_ind_t.get('wt1_W', 0) or 0)
                _we_wt2 = float(_we_ind_t.get('wt2_W', 0) or 0)
                if _we_wt1 == 0 and _we_wt2 == 0:
                    continue
                _we_now_bull = _we_wt1 > _we_wt2
                _we_prev_bull = _w_exit_prev_t.get(_we_sym)
                _w_exit_prev_t[_we_sym] = _we_now_bull
                if _we_prev_bull is None:
                    continue
                _we_is_long = _we_pk.endswith('_LONG')
                _we_cross = (_we_is_long and _we_prev_bull and not _we_now_bull) or \
                            (not _we_is_long and not _we_prev_bull and _we_now_bull)
                if not _we_cross:
                    continue
                _we_gain = float(getattr(_we_pos, 'gain', 0) or 0)
                _we_px = float(_we_ind_t.get('current_price', 0) or 0)
                if _we_px <= 0:
                    continue
                _we_side = 'LONG' if _we_is_long else 'SHORT'
                _we_reason = f"WT_W_CROSS_EXIT_{_we_side}_wt1={_we_wt1:.1f}_wt2={_we_wt2:.1f}_g{_we_gain:.3f}%"
                try:
                    await manager.execute_trade_action(account_key=account_key, position_key=_we_pk, symbol=_we_sym, quantity=abs(float(getattr(_we_pos, 'positionAmt', 0))), current_price=_we_px, side='SELL' if _we_is_long else 'BUY', position_side=_we_side, action='CLOSE', reason=_we_reason, is_full_close=True, is_hedge=False)
                    v8_logger.info(f"[DISC-W_EXIT_T] {_we_pk}: W cross exit gain={_we_gain:.2f}%")
                except Exception as _we_err_t:
                    if step < 10 or step % 1000 == 0:
                        v8_logger.error(f"[DISC-W_EXIT_T_ERR] {_we_pk}: {_we_err_t}")
        # NO-LIES 2026-05-12: deterministic drain replaces single-tick yield.
        await drain_pending_v8_tasks()
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
    _real_closes_t = len([
        t for t in executed_trades
        if str(t.get("action", "")).upper() in ("CLOSE", "FULL_CLOSE", "REDUCE", "QUICK_CLOSE")
    ])
    _opens_by_side_t = {"LONG": 0, "SHORT": 0}
    for _event_t in executed_trades:
        if str(_event_t.get("action", "")).upper() not in ("OPEN", "QUICK_OPEN", "REENTRY"):
            continue
        _event_side_t = str(
            _event_t.get("position_side")
            or ("LONG" if str(_event_t.get("position_key", "")).endswith("_LONG") else "SHORT")
        ).upper()
        if _event_side_t in _opens_by_side_t:
            _opens_by_side_t[_event_side_t] += 1

    # Tradier previously had no final mark-to-market pass, so a genuine hold and
    # a dead entry path both returned zero. Materialize open positions at the final bar.
    _mtm_count_t = 0
    _final_ts_t = int(all_ts[-1]) if all_ts else 0
    _positions_t = manager.position_manager.positions if manager.position_manager else {}
    for _mtm_pk_t, _mtm_pos_t in list(_positions_t.items()):
        try:
            _mtm_amt_t = abs(float(getattr(_mtm_pos_t, "positionAmt",
                                           getattr(_mtm_pos_t, "quantity", 0)) or 0))
            _mtm_entry_t = float(getattr(_mtm_pos_t, "entry_price", 0) or 0)
            _mtm_mark_t = float(getattr(_mtm_pos_t, "mark_price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if _mtm_amt_t < 0.0001 or _mtm_entry_t <= 0 or _mtm_mark_t <= 0:
            continue
        _mtm_side_t = "LONG" if str(_mtm_pk_t).endswith("_LONG") else "SHORT"
        _mtm_sym_t = (
            getattr(_mtm_pos_t, "symbol", "")
            or str(_mtm_pk_t).split(":", 1)[-1].rsplit("_", 1)[0]
        )
        _mtm_gross_pct_t = (
            (_mtm_mark_t - _mtm_entry_t) / _mtm_entry_t * 100.0
            if _mtm_side_t == "LONG"
            else (_mtm_entry_t - _mtm_mark_t) / _mtm_entry_t * 100.0
        )
        _mtm_cost_t = _round_trip_cost_for_sym(_mtm_sym_t)
        _mtm_pct_t = _mtm_gross_pct_t - _mtm_cost_t
        _mtm_dollars_t = _mtm_pct_t / 100.0 * _mtm_entry_t * _mtm_amt_t
        executed_trades.append({
            "type": "eta",
            "timestamp": _final_ts_t,
            "exit_ts": _final_ts_t,
            "symbol": _mtm_sym_t,
            "position_key": _mtm_pk_t,
            "side": _mtm_side_t,
            "position_side": _mtm_side_t,
            "price": _mtm_mark_t,
            "quantity": _mtm_amt_t,
            "action": "FULL_CLOSE",
            "reason": "MTM_FINAL_BAR_NOLIES_RULE2",
            "pnl_pct": _mtm_pct_t,
            "pnl_pct_gross": _mtm_gross_pct_t,
            "round_trip_cost_pct": _mtm_cost_t,
            "pnl_dollars": _mtm_dollars_t,
            "pnl": _mtm_dollars_t,
            "entry_price": _mtm_entry_t,
            "position_value": _mtm_entry_t * _mtm_amt_t,
            "is_full_close": True,
        })
        _mtm_count_t += 1

    _compute_trade_pnl(executed_trades)
    if _research_adapter_t is not None:
        import hashlib as _research_hashlib_t

        _research_schedule_audit_t = _research_adapter_t.final_audit()
        _research_accounting_audit_t = _research_adapter_t.accounting_audit(
            executed_trades
        )
        _research_tim_audit_t = _research_adapter_t.tim_audit()
        _research_audit_t = {
            "status": (
                "PASS"
                if _research_schedule_audit_t["status"] == "PASS"
                and _research_accounting_audit_t["status"] == "PASS"
                and _research_tim_audit_t["status"] == "PASS"
                else "FAIL"
            ),
            "tier": "VEC_RESEARCH_EXACT_ENGINE_ROUTE_SMOKE",
            "schedule": _research_schedule_audit_t,
            "accounting": _research_accounting_audit_t,
            "time_in_market": _research_tim_audit_t,
            "fingerprints": {
                "npz_sha256": _research_adapter_t.spec["expected_npz_sha256"],
                "backtest_v8_engine_sha256": _research_hashlib_t.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
                "tradier_manage_sha256": _research_hashlib_t.sha256(
                    (BASE_PATH / "tradier_manage.py").read_bytes()
                ).hexdigest(),
            },
            "causality": {
                "exit_and_reentry_latency_bars": 1,
                "end_of_sample_mtm_latency_bars": 0,
                "all_schedule_events_validated": True,
                "exact_next_rth_index_validated": True,
            },
            "signal_parity": False,
            "signal_parity_reason": (
                "Frozen schedule replay does not independently recompute E02/E10/E11 signals."
            ),
            "matrix_written": False,
            "promotion_allowed": False,
        }
        # Use the module-level helper without exposing it as adapter state.
        from tools.v8_research_top_exit_adapter import sha256_file as _research_sha256_t

        _research_audit_t["fingerprints"]["event_schedule_sha256"] = (
            _research_sha256_t(_research_adapter_t.schedule_path)
        )
        _research_audit_path_t = os.environ.get(
            "V8_RESEARCH_TOP_EXIT_AUDIT_FILE", ""
        ).strip()
        if _research_audit_path_t:
            _research_audit_out_t = Path(_research_audit_path_t)
            _research_audit_out_t.parent.mkdir(parents=True, exist_ok=True)
            _research_audit_out_t.write_text(
                json.dumps(_research_audit_t, sort_keys=True, indent=2) + "\n"
            )
        print(
            "V8_RESEARCH_TOP_EXIT_AUDIT: "
            + json.dumps(_research_audit_t, sort_keys=True),
            flush=True,
        )
    if _research_ladder_adapter_t is not None:
        import hashlib as _research_ladder_hashlib_t

        _research_ladder_schedule_audit_t = (
            _research_ladder_adapter_t.final_audit()
        )
        _research_ladder_accounting_audit_t = (
            _research_ladder_adapter_t.accounting_audit(executed_trades)
        )
        _research_ladder_audit_t = {
            "status": (
                "PASS"
                if (
                    _research_ladder_schedule_audit_t["status"] == "PASS"
                    and _research_ladder_accounting_audit_t["status"]
                    == "PASS"
                )
                else "FAIL"
            ),
            "tier": "VEC_RESEARCH_EXACT_ENGINE_LADDER_PARITY",
            "schedule": _research_ladder_schedule_audit_t,
            "accounting": _research_ladder_accounting_audit_t,
            "fingerprints": {
                "npz_sha256": _research_ladder_adapter_t.spec[
                    "expected_npz_sha256"
                ],
                "event_schedule_sha256": (
                    _research_ladder_adapter_t.spec[
                        "expected_schedule_sha256"
                    ]
                ),
                "backtest_v8_engine_sha256": (
                    _research_ladder_hashlib_t.sha256(
                        Path(__file__).read_bytes()
                    ).hexdigest()
                ),
                "tradier_manage_sha256": (
                    _research_ladder_hashlib_t.sha256(
                        (BASE_PATH / "tradier_manage.py").read_bytes()
                    ).hexdigest()
                ),
            },
            "causality": {
                "completed_htf_only": True,
                "future_htf_source_count": 0,
                "signal_to_fill_latency_rth_bars": 1,
                "final_mtm_latency_rth_bars": 0,
                "exact_source_events_recomputed_from_loaded_npz": True,
            },
            "signal_parity": True,
            "matrix_written": False,
            "promotion_allowed": False,
        }
        _research_ladder_audit_path_t = os.environ.get(
            "V8_RESEARCH_LADDER_AUDIT_FILE", ""
        ).strip()
        if _research_ladder_audit_path_t:
            _research_ladder_audit_out_t = Path(
                _research_ladder_audit_path_t
            )
            _research_ladder_audit_out_t.parent.mkdir(
                parents=True, exist_ok=True
            )
            _research_ladder_audit_out_t.write_text(
                json.dumps(
                    _research_ladder_audit_t,
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            )
        print(
            "V8_RESEARCH_LADDER_AUDIT: "
            + json.dumps(_research_ladder_audit_t, sort_keys=True),
            flush=True,
        )
    _span_seconds_t = max(1.0, float(all_ts[-1] - all_ts[0])) if len(all_ts) >= 2 else 1.0
    _extra_result_t = {
        "real_closes": _real_closes_t,
        "mtm_count": _mtm_count_t,
        "opens_long": _opens_by_side_t["LONG"],
        "opens_short": _opens_by_side_t["SHORT"],
        "time_in_mkt_long_pct": f"{100.0 * _exposure_seconds_t['LONG'] / _span_seconds_t:.4f}",
        "time_in_mkt_short_pct": f"{100.0 * _exposure_seconds_t['SHORT'] / _span_seconds_t:.4f}",
    }
    _extra_result_t.update(
        open_sizing_telemetry(
            executed_trades,
            float(getattr(tm_mod.config, "START_POSITION_SIZE", 1.0) or 1.0),
        )
    )
    _extra_result_t.update({
        key: f"{value:.4f}" for key, value in _capital_contract_t.items()
    })
    _reentry_trace_t = getattr(manager, "_mandatory_reentry_trace", {}) or {}
    _reentry_rows_t = list(_reentry_trace_t.values())
    _reentry_material_pct_t = float(
        getattr(tm_mod.config, "PRICE_CROSS_BACK_BAND_PCT", 0.3) or 0.3
    )
    _extra_result_t.update({
        "reentry_pending": sum(1 for row in _reentry_rows_t if row.get("pending", False)),
        "reentry_flat_bars": sum(int(row.get("flat_bars", 0) or 0) for row in _reentry_rows_t),
        "reentry_max_overshoot_pct": f"{max([float(row.get('max_overshoot_pct', 0) or 0) for row in _reentry_rows_t] or [0.0]):.4f}",
        "reentry_violations": sum(
            1 for row in _reentry_rows_t
            if float(row.get("max_overshoot_pct", 0) or 0) > _reentry_material_pct_t
        ),
    })
    _v8_result_from_trades(executed_trades, capital, _extra_result_t)
    _write_chart_trades(executed_trades)
    log_dir = BASE_PATH / "backtest_v8" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"v8_tradier_{account_key}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jsonl"
    with open(log_path, "w") as f:
        for t in executed_trades:
            f.write(json.dumps(t, default=str) + "\n")
    # 2026-05-09 live-vs-sandbox parity audit: also dump to V8_RAW_EVENTS_FILE if set
    _raw_events_path = os.environ.get("V8_RAW_EVENTS_FILE", "")
    if _raw_events_path:
        try:
            with open(_raw_events_path, "w") as _ef:
                for _ev in executed_trades:
                    _ef.write(json.dumps(_ev, default=str) + "\n")
            print(f"V8_RAW_EVENTS: wrote {len(executed_trades)} events to {_raw_events_path}", flush=True)
        except Exception as _re:
            print(f"V8_RAW_EVENTS_ERR: {_re}", flush=True)
    if V8_DECISION_ONLY:
        try: _v8_decision_close_files()
        except Exception: pass
        _doc = _V8_DECISION_COUNTERS
        print(f"V8_DECISION_ONLY_SUMMARY: opens={_doc['opens']} augments={_doc['augments']} reduces={_doc['reduces']} closes={_doc['closes']} hedges={_doc['hedges']} doubleopen_reclass={_doc['doubleopen_reclass']} ung_blocks={_doc['blocks']} out_dir={V8_DECISION_OUT_DIR}", flush=True)
    v8_logger.info(f"\n{'='*60}\n  V8 TRADIER: {len(stores)} syms, {len(all_ts)} bars, {len(executed_trades)} trades, {elapsed:.0f}s\n  Log: {log_path}\n{'='*60}")
    print(f"V8_LOG: {log_path}")
    if _research_audit_t is not None and _research_audit_t["status"] != "PASS":
        raise RuntimeError("V8_RESEARCH_TOP_EXIT exact replay audit failed")
    if (
        _research_ladder_audit_t is not None
        and _research_ladder_audit_t["status"] != "PASS"
    ):
        raise RuntimeError("V8_RESEARCH_LADDER exact replay audit failed")


if __name__ == "__main__":
    main()
