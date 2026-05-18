"""tradier_struct_v4_sleeve.py — Standalone live-trading sleeve for struct_v4 (v3_no_stop).

Built 2026-05-18. Independent of tradier_manage.py — runs as its own daemon,
maintains its own state file (data/struct_v4_state_<account>.json), uses tradier_api
directly for reads + orders, and respects three halt flags.

DUAL-ACCOUNT DEPLOYMENT (USER 2026-05-18):
  --account trb  → LIVE-eligible (micro-sized); universe = symbols_trb_long ∪ symbols_trb_short
  --account trc  → PAPER ONLY (never live);     universe = sectors_tradier.json union
  A/B test: ranked (trb) vs unranked-all (trc) over 1–2 weeks.

PAPER WINDOW (USER 2026-05-18):
  Day 1, between 13:30–14:00 UTC, trb is FORCED to paper mode regardless of env.
  After 14:00 UTC, trb honours STRUCT_V4_GO_LIVE. trc stays paper indefinitely.

═══════════════════════════════════════════════════════════════════════════════
NO-LIES MANDATE ACKNOWLEDGEMENT (per CLAUDE.md)
═══════════════════════════════════════════════════════════════════════════════
The backtest the user references reported:
    n_syms = 91, pool_sharpe = 0.18846, mean_wr = 57.6%,
    0 losers <1×, 0 catastrophic <50%, gain_per_yr = 65.5%/yr.

Per CLAUDE.md: pool_sharpe 0.1885 < 1.0 = "trash. Not a promotion candidate."
Sample floor is met (≥100 stock syms — actually 91, below the 100-sym floor),
but pool_sharpe is sub-floor. Per the NO MORE LIVE-SCRIPT IMPROVEMENTS UNTIL
BACKTEST PROVEN mandate (2026-05-16), live use requires pool_sharpe > 1.0.

This module DEFAULTS TO PAPER MODE. It will refuse to place real orders unless:
    - account == "trb" (trc never goes live)
    - env STRUCT_V4_PAPER_MODE != "true"
    - env STRUCT_V4_GO_LIVE == "true"
    - current UTC time is NOT in the [13:30, 14:00) Day-1 paper window
    - file data/STRUCT_V4_HALT_ALL does NOT exist
    - file data/STRUCT_V4_HALT_ENTRIES does NOT exist (for entries only)
═══════════════════════════════════════════════════════════════════════════════

STRATEGY SPEC (validated config: cfg_safer_v3_no_stop)
    Entry gate (USER 2026-05-18): all entry candidates MUST pass
        golden_rule_htf.score_entry_htf() ACTIVATION gate first. Activation =
        breakout (bb_pctb≥0.75 OR dc_pos≥0.65) on at least one of [D,4h].
        Without activation → no entry regardless of path.
    Entry paths (fire on FIRST armed, priority A→F):
        A: HTF structure breakout (D) + 1h retest + 15m HL alignment count ≥4
        B: K_15m<30 + K_1h<40 + WT bull cross 15m + reclaim pivot lo
        C: K_1h bull cross from K<30
        D: WT_D bull cross + RSI_D > 40
        E: close > sma_200_D + RSI_D > 50
        F: WT_W bull cross
    Exit paths (fire on FIRST armed, priority order):
        X1: K_15m > 80 + bear WT 15m cross (top-catch)
        X4: bear WT_D cross
        X5: ≥3 LH/LL events in 3-bar window on trigger TF (15m)
        X2: trailing 12% from peak (only when in profit)
    Pyramid: up to 5 adds, 4h TF new HH, 0.5 add fraction, also k_1h<25+wt bull
    NEW belt-and-suspenders (USER 2026-05-18, NOT in validated backtest):
        X6: dc_low_1h(20) break — close if px < entry-bar's hourly Donchian low
        X7: absolute -8% hard floor
    Min hold: 1 trading day (no day-trading).

POSITION SIZING (Day 1):
    Position notional: $500
    Max concurrent positions: 5
    Max total deployed: $2,500
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

BASE_PATH = Path("/Users/niels/Documents/binance")
sys.path.insert(0, str(BASE_PATH))

from config_tradier import TradierConfig  # noqa: E402
from tradier_api import TradierAPIClient  # noqa: E402
from tradier_indicators import (  # noqa: E402
    rsi_series,
    stoch_rsi,
    atr_series,
    wavetrend,
)
from golden_rule_htf import score_entry_htf  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = BASE_PATH / "data"
DECISIONS_DIR = DATA_DIR / "decisions"
NPZ_DIR = BASE_PATH / "backtest_v8" / "indicators"

# Halt flag files — TWO LOCATIONS for back-compat (monitors/_common.py contract):
#   data/struct_v4/STRUCT_V4_*           = new global (both accounts)
#   data/struct_v4/STRUCT_V4_*_<acct>    = per-account
#   data/STRUCT_V4_*                     = legacy global (deprecated, still read)
STRUCT_V4_DIR = DATA_DIR / "struct_v4"
# New global (data/struct_v4/) — preferred
GLOBAL_HALT_ENTRIES_FLAG = STRUCT_V4_DIR / "STRUCT_V4_HALT_ENTRIES"
GLOBAL_HALT_ALL_FLAG = STRUCT_V4_DIR / "STRUCT_V4_HALT_ALL"
GLOBAL_PANIC_CLOSE_FLAG = STRUCT_V4_DIR / "STRUCT_V4_PANIC_CLOSE_ALL"
# Legacy global (data/) — kept for backward compatibility
LEGACY_HALT_ENTRIES_FLAG = DATA_DIR / "STRUCT_V4_HALT_ENTRIES"
LEGACY_HALT_ALL_FLAG = DATA_DIR / "STRUCT_V4_HALT_ALL"
LEGACY_PANIC_CLOSE_FLAG = DATA_DIR / "STRUCT_V4_PANIC_CLOSE_ALL"
# Legacy module-level aliases (pre-2026-05-18 callers may import these).
HALT_ENTRIES_FLAG = LEGACY_HALT_ENTRIES_FLAG
HALT_ALL_FLAG = LEGACY_HALT_ALL_FLAG
PANIC_CLOSE_FLAG = LEGACY_PANIC_CLOSE_FLAG

# Day-1 sizing
POSITION_NOTIONAL_USD = float(os.getenv("STRUCT_V4_NOTIONAL", "500"))
MAX_CONCURRENT_POSITIONS = int(os.getenv("STRUCT_V4_MAX_POSITIONS", "5"))
MAX_TOTAL_DEPLOYED_USD = float(os.getenv("STRUCT_V4_MAX_DEPLOYED", "2500"))

# ════════════════════════════════════════════════════════════════════════════
# PER-SYMBOL OVERRIDES + UNIVERSE DROPS (2026-05-18) — applied from synthesized
# 5-sector + 7-per-symbol agent reports. ALL [DIAGNOSTIC] per CLAUDE.md sample
# floor (per-sector n<100). Risk-bounded by Day-1 micro-sizing + monitors.
# Each per-sym agent independently confirmed the unified rule. See:
#   /tmp/sector_analysis_*.md (5 sectors)
#   /tmp/per_sym_{SNDK,MU,PLTR,NVDA_AMD,AVGO_LRCX_INTC_TXN,GOOGL}.md (7 deep-dives)
# Toggle via env STRUCT_V4_OVERRIDES_ENABLED=false to revert behavior.
# ════════════════════════════════════════════════════════════════════════════
OVERRIDES_ENABLED = os.getenv("STRUCT_V4_OVERRIDES_ENABLED", "true").lower() == "true"

# Symbols to DROP from universe at runtime (block all entries).
# Tagged with originating agent verdict.
UNIVERSE_DROPS = {
    # tech_ai_chips agent: B&H < 0 over window, long-only futile
    "SAP", "ACN", "PYPL", "TTD", "WDAY", "FIVN", "OLED",
    # precious_metals agent: low Sharpe + short-side blowup
    "AGI", "AG",
    # energy_oil_gas agent: sub-sample, sshp -0.625
    "BNO",
    # base_metals_mining agent: sole money-loser
    "ALB",
    # small_sectors agent: sub-sample NPZ (<0.5 yr)
    "NLR", "NUKZ", "MSTR",
}

# Per-symbol POSITION_MULT — base notional ($500) × mult.
# Boost confirmed winners (≥4× B&H or median high Sharpe sectors),
# trim chronic underperformers.
PER_SYM_POSITION_MULT = {
    # Base metals 4×B&H clearers (sector analyst)
    "CLF": 2.0, "MP": 2.0, "FCX": 2.0, "SCCO": 2.0,
    # Consumer media winners (best sector +0.031 baseline)
    "STZ": 1.5, "RBLX": 1.5, "DIS": 1.5,
    # Base metals strong (per agent)
    "NUE": 1.5, "STLD": 1.5, "TECK": 1.5, "LAC": 1.5,
    "VALE": 1.5, "BHP": 1.5, "RIO": 1.5,
    # Uranium/agri/defense/commodities boost candidates
    "UEC": 1.5, "UUUU": 1.5, "UAN": 1.5, "DAR": 1.5, "HII": 1.5, "GLD": 1.5,
    "DE": 1.2, "GM": 1.2,
    # Reduce: low-Sharpe / range-bound names
    "AGCO": 0.5, "NOC": 0.5, "ROKU": 0.5, "IBIT": 0.5, "USO": 0.5, "RS": 0.5,
}

# Per-symbol full override dict (richer config — currently informational).
# When the engine's evaluate_entry/exit consults these, it can adjust min_hold,
# disable specific exit paths, etc. Day-1 sleeve only consumes UNIVERSE_DROPS
# and PER_SYM_POSITION_MULT; the structured per-sym recipes from per_sym
# agents (TREND_HOLD_D for multi-baggers) will be wired in Day-2 after
# Tier-2 backtest validation.
PER_SYM_OVERRIDES = {
    # Tech multi-baggers — per-sym agents proved capture 95-242% with
    # DISABLE_PPL + DISABLE_WT_EXHAUST + daily-TF entry/exit. Reserved for
    # Day-2 after backtest_v8_engine validation. NOT consumed Day-1.
    "SNDK": {"_tag": "TREND_HOLD_D"},
    "MU":   {"_tag": "TREND_HOLD_D"},
    "PLTR": {"_tag": "TREND_HOLD_D"},
    "INTC": {"_tag": "TREND_HOLD_D"},
    "GOOGL":{"_tag": "TREND_HOLD_D"},
    "NVDA": {"_tag": "TREND_HOLD_D"},
    "AVGO": {"_tag": "TREND_HOLD_D"},
    "TXN":  {"_tag": "TREND_HOLD_D"},
    "MA":   {"_tag": "TREND_HOLD_D"},
    "CRWV": {"_tag": "TREND_HOLD_D"},
    "AXON": {"_tag": "TREND_HOLD_D"},
    "ASTS": {"_tag": "TREND_HOLD_D"},
}

def get_position_mult(symbol: str) -> float:
    """Return per-symbol position-size multiplier (1.0 default)."""
    if not OVERRIDES_ENABLED:
        return 1.0
    return float(PER_SYM_POSITION_MULT.get(symbol, 1.0))

def symbol_dropped(symbol: str) -> bool:
    """True if symbol should not be traded (entries blocked, existing positions can exit)."""
    if not OVERRIDES_ENABLED:
        return False
    return symbol in UNIVERSE_DROPS

# ════════════════════════════════════════════════════════════════════════════
# PER-SYMBOL 20D OVERRIDES (USER 2026-05-18, T-7h to market open)
# Wires per_sym_20d_agent_stocks.py output. The agent runs hourly and writes
# data/hourly_reconfig/<account>/active_config_20d.json based on the last
# 20 days of behavior with 50%/day exponential decay. Schema (per symbol key):
#   {"wsharpe": float, "trades_20d": int, "overrides": {...}, "_tag": ..., ...}
# Only entries with wsharpe >= STRUCT_V4_PER_SYM_20D_MIN_WSHARPE are returned.
# At entry-eval time the sleeve records presence in gate_info (Day-1 passive
# logging); Day-2 wires knob consumption downstream.
# Env switch STRUCT_V4_PER_SYM_20D_ENABLED=false reverts to static dicts only.
# ════════════════════════════════════════════════════════════════════════════
PER_SYM_20D_ENABLED = os.getenv("STRUCT_V4_PER_SYM_20D_ENABLED", "true").lower() == "true"
PER_SYM_20D_MIN_WSHARPE = float(os.getenv("STRUCT_V4_PER_SYM_20D_MIN_WSHARPE", "0.7"))
PER_SYM_20D_CACHE_SECONDS = int(os.getenv("STRUCT_V4_PER_SYM_20D_CACHE_S", "300"))  # 5 min
# Module-level cache: {account: (loaded_at_epoch, {sym: entry})}
_PER_SYM_20D_CACHE: Dict[str, Tuple[float, Dict[str, Dict[str, Any]]]] = {}


def load_per_sym_20d_overrides(account: str) -> Dict[str, Dict[str, Any]]:
    """Load per-symbol 20D overrides for `account` from
    data/hourly_reconfig/<account>/active_config_20d.json.

    Returns {symbol: full_entry_dict} for entries meeting the wsharpe gate.
    Empty dict if file missing or disabled — graceful fallback to static dicts.
    Cached for PER_SYM_20D_CACHE_SECONDS.
    """
    import time as _time
    if not PER_SYM_20D_ENABLED:
        return {}
    now = _time.time()
    cached = _PER_SYM_20D_CACHE.get(account)
    if cached is not None and (now - cached[0]) < PER_SYM_20D_CACHE_SECONDS:
        return cached[1]
    path = DATA_DIR / "hourly_reconfig" / account / "active_config_20d.json"
    if not path.exists():
        _PER_SYM_20D_CACHE[account] = (now, {})
        logger.info(f"[{account}] 20D overrides file missing ({path}) — "
                    f"running on static dicts only")
        return {}
    try:
        with path.open() as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning(f"[{account}] 20D overrides load failed ({path}): {e} — "
                       f"running on static dicts only")
        _PER_SYM_20D_CACHE[account] = (now, {})
        return {}
    if not isinstance(raw, dict):
        logger.warning(f"[{account}] 20D overrides not a dict (got {type(raw).__name__}) — ignoring")
        _PER_SYM_20D_CACHE[account] = (now, {})
        return {}
    qualified: Dict[str, Dict[str, Any]] = {}
    total = 0
    for sym, entry in raw.items():
        total += 1
        if not isinstance(entry, dict):
            continue
        try:
            ws = float(entry.get("wsharpe", float("nan")))
        except (TypeError, ValueError):
            continue
        if ws != ws:  # NaN
            continue
        if ws < PER_SYM_20D_MIN_WSHARPE:
            continue
        qualified[sym.upper()] = entry
    logger.info(f"20D overrides loaded for {account}: {len(qualified)}/{total} "
                f"symbols qualified (wsharpe >= {PER_SYM_20D_MIN_WSHARPE})")
    _PER_SYM_20D_CACHE[account] = (now, qualified)
    return qualified


def get_per_sym_20d_entry(account: str, symbol: str) -> Optional[Dict[str, Any]]:
    """Return the per-symbol 20D override entry (full dict) if `symbol` is
    qualified for `account`, else None."""
    if not PER_SYM_20D_ENABLED or not account or not symbol:
        return None
    return load_per_sym_20d_overrides(account).get(symbol.upper())


# Cycle
CYCLE_INTERVAL_SECONDS = int(os.getenv("STRUCT_V4_CYCLE_S", "300"))  # 5 min
MARKET_OPEN_UTC = dt.time(13, 30)
MARKET_CLOSE_UTC = dt.time(20, 0)
PAPER_WINDOW_END_UTC = dt.time(14, 0)  # 30-min Day-1 paper window for trb

# Strategy knobs (= cfg_safer_v3_no_stop)
PATH_A_BREAKOUT_TF = "D"
PATH_A_RETEST_TF = "1h"
PATH_A_TRIGGER_TF = "15m"
PATH_A_MIN_COUNT = 4
PATH_A_ATR_BAND = 1.0
PATH_A_REGIME_PERSIST_BARS = 500
PATH_B_K15_MAX = 30.0
PATH_B_K1H_MAX = 40.0
PATH_C_K1H_CROSS_BELOW = 30.0
PATH_D_RSI_D_MIN = 40.0
PATH_E_RSI_D_MIN = 50.0
HTF_TREND_FILTER_ENABLED = True
HTF_REQUIRE_WT_D_BULL = True
HTF_REQUIRE_RSI_D_MIN = 45.0

EXIT_X1_K15_MIN = 80.0
EXIT_X2_TRAILING_PCT = 12.0
EXIT_X3_HARDSTOP_ATR_MULT = 0.0  # disabled in v3_no_stop
EXIT_X5_MIN_COUNT = 3
EXIT_X5_WINDOW_BARS = 3

# Belt-and-suspenders (USER 2026-05-18 — additive to validated config)
EXIT_X6_DC_LOW_1H_LENGTH = 20  # break entry-bar's dc_low(20) on 1h
EXIT_X7_ABSOLUTE_FLOOR_PCT = -8.0  # absolute -8% hard floor

# LT-direction regime filter (USER 2026-05-18 — block LONG on downtrending names)
# Per-symbol daily 200SMA trend gate at entry. Symmetric to the SHORT-on-uptrend
# mistake diagnostic_agent flagged (V0_baseline lost -7,604% shorting uptrending tech);
# this side blocks LONG when close_D <= sma_200_D. Gates entries only — exits unchanged.
STRUCT_V4_LT_DIRECTION_FILTER_ENABLED = os.getenv(
    "STRUCT_V4_LT_DIRECTION_FILTER_ENABLED", "true").lower() == "true"
STRUCT_V4_LT_DIRECTION_TF = os.getenv("STRUCT_V4_LT_DIRECTION_TF", "D")
STRUCT_V4_LT_DIRECTION_ABOVE_SMA_BARS = int(
    os.getenv("STRUCT_V4_LT_DIRECTION_ABOVE_SMA_BARS", "200"))

MIN_HOLD_DAYS = 1  # no day-trading

# Indicator history requirement.
# Tradier timesales 5min API returns only ~30 days of bars; daily/weekly via
# /markets/history go back years. Sleeve fetches BOTH: short-window 5m for
# intraday TFs (5m/15m/1h/4h) and long-window daily/weekly for HTF (D/W) +
# sma_200_D.
KLINES_LOOKBACK_DAYS = 25       # 5m timesales (capped ~30d)
DAILY_LOOKBACK_DAYS = 500       # daily history (≥200 bars for sma_200_D)
WEEKLY_LOOKBACK_DAYS = 900      # weekly history

# Golden rule HTF gate knobs (USER 2026-05-18 — used by score_entry_htf).
# config_tradier.GOLDEN_RULE_REQUIRE_ACTIVATION=True forces the activation gate
# in golden_rule_htf._check_activation() to require breakout on at least 1 of
# [D, 4h] before any per-TF score is computed.
GR_MIN_TFS = int(os.getenv("STRUCT_V4_GR_MIN_TFS", "2"))
GR_MIN_IND = int(os.getenv("STRUCT_V4_GR_MIN_IND", "3"))

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

log_dir = Path(os.path.expanduser("~/logs"))
log_dir.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("struct_v4_sleeve")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = RotatingFileHandler(str(log_dir / "struct_v4_sleeve.log"),
                                   maxBytes=25 * 1024 * 1024, backupCount=10,
                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S"))
    logger.addHandler(handler)
    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                           datefmt="%H:%M:%S"))
    logger.addHandler(stream)


# ─────────────────────────────────────────────────────────────────────────────
# RUNTIME ACCOUNT CONTEXT (set from CLI in main())
# ─────────────────────────────────────────────────────────────────────────────

class AccountContext:
    """Per-account runtime state. trb=live-eligible, trc=paper-forever."""

    def __init__(self, account_key: str):
        if account_key not in ("trb", "trc"):
            raise ValueError(f"account must be 'trb' or 'trc', got {account_key!r}")
        self.account_key = account_key
        self.state_file = DATA_DIR / f"struct_v4_state_{account_key}.json"
        self.universe_file = DATA_DIR / f"struct_v4_universe_{account_key}.json"
        self.universe_label = ("trb_ranked_union" if account_key == "trb"
                               else "trc_sectors_union")
        self.refresh_paper_window()

    def refresh_paper_window(self) -> None:
        """Re-evaluate paper-mode state. Called at every cycle so trb can
        flip from paper to live at 14:00 UTC without restart."""
        env_paper = os.getenv("STRUCT_V4_PAPER_MODE", "true").lower() == "true"
        env_go_live = os.getenv("STRUCT_V4_GO_LIVE", "false").lower() == "true"
        if self.account_key == "trc":
            # trc = unranked-all paper A/B leg. NEVER live.
            self.paper_mode = True
            self.go_live = False
            self.paper_reason = "TRC_PAPER_FOREVER"
            return
        # trb — live-eligible. Day-1 paper window (13:30–14:00 UTC) forces paper.
        now_utc = dt.datetime.now(dt.timezone.utc).time()
        in_paper_window = MARKET_OPEN_UTC <= now_utc < PAPER_WINDOW_END_UTC
        if in_paper_window:
            self.paper_mode = True
            self.go_live = False
            self.paper_reason = "TRB_DAY1_PAPER_WINDOW_13_30_TO_14_00_UTC"
        else:
            self.paper_mode = env_paper
            self.go_live = env_go_live
            if env_paper:
                self.paper_reason = "ENV_STRUCT_V4_PAPER_MODE=true"
            elif not env_go_live:
                self.paper_reason = "ENV_STRUCT_V4_GO_LIVE!=true"
            else:
                self.paper_reason = "LIVE_ENABLED"


# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSE FILE GENERATION (USER 2026-05-18)
# ─────────────────────────────────────────────────────────────────────────────

def _available_npz_symbols() -> set:
    """Return set of symbols (.npz stem upper-cased) on this machine.
    Crypto perps (USDT/USDC) are filtered out so stock universes never
    accidentally include them."""
    if not NPZ_DIR.exists():
        return set()
    syms = set()
    for p in NPZ_DIR.glob("*.npz"):
        stem = p.stem
        if "USDT" in stem or "USDC" in stem:
            continue
        syms.add(stem.upper())
    return syms


def generate_universe_files(force: bool = False) -> Dict[str, int]:
    """Generate data/struct_v4_universe_{trb,trc}.json from source lists.
    Idempotent unless force=True. Filters to symbols with NPZ on the running
    machine. Returns counts by account."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    trb_file = DATA_DIR / "struct_v4_universe_trb.json"
    trc_file = DATA_DIR / "struct_v4_universe_trc.json"
    available = _available_npz_symbols()
    out_counts: Dict[str, int] = {}

    # trb: symbols_trb_long ∪ symbols_trb_short, NPZ-filtered
    if force or not trb_file.exists():
        try:
            with (BASE_PATH / "symbols_trb_long.json").open() as f:
                long_list = json.load(f)
            with (BASE_PATH / "symbols_trb_short.json").open() as f:
                short_list = json.load(f)
        except Exception as e:
            logger.error(f"trb universe source missing: {e}")
            long_list, short_list = [], []
        raw_union = sorted({s.upper() for s in (list(long_list) + list(short_list)) if s})
        if available:
            filtered = [s for s in raw_union if s in available]
        else:
            # No NPZ dir on this machine — skip filter so we don't ship an empty universe
            filtered = raw_union
        trb_file.write_text(json.dumps(filtered, indent=2))
        out_counts["trb"] = len(filtered)
        logger.info(f"Generated {trb_file.name}: {len(filtered)} syms "
                    f"(from {len(raw_union)} raw union, "
                    f"{len(available)} NPZ available)")
    else:
        try:
            out_counts["trb"] = len(json.loads(trb_file.read_text()))
        except Exception:
            out_counts["trb"] = -1

    # trc: union of all sectors in sectors_tradier.json, NPZ-filtered
    if force or not trc_file.exists():
        try:
            with (BASE_PATH / "sectors_tradier.json").open() as f:
                sectors = json.load(f)
        except Exception as e:
            logger.error(f"sectors_tradier.json missing: {e}")
            sectors = {}
        raw_union = sorted({
            s.upper() for k, v in sectors.items()
            if not k.startswith("_") and isinstance(v, list)
            for s in v if s
        })
        if available:
            filtered = [s for s in raw_union if s in available]
        else:
            filtered = raw_union
        trc_file.write_text(json.dumps(filtered, indent=2))
        out_counts["trc"] = len(filtered)
        logger.info(f"Generated {trc_file.name}: {len(filtered)} syms "
                    f"(from {len(raw_union)} raw sectors union, "
                    f"{len(available)} NPZ available)")
    else:
        try:
            out_counts["trc"] = len(json.loads(trc_file.read_text()))
        except Exception:
            out_counts["trc"] = -1

    return out_counts


# ─────────────────────────────────────────────────────────────────────────────
# HALT FLAG HANDLING (always FIRST in every cycle)
# ─────────────────────────────────────────────────────────────────────────────

def check_halt_flags(account: Optional[str] = None) -> Dict[str, Any]:
    """Returns dict of {halt_entries, halt_all, panic_close, sources}. ALWAYS first.

    Per-account-aware resolution (USER mandate 2026-05-18). Priority high→low:
      1. Per-account panic:   data/struct_v4/STRUCT_V4_PANIC_CLOSE_ALL_<acct>
      2. Per-account halt-all: data/struct_v4/STRUCT_V4_HALT_ALL_<acct>
      3. Per-account halt-ent: data/struct_v4/STRUCT_V4_HALT_ENTRIES_<acct>
      4. Global (new path):    data/struct_v4/STRUCT_V4_PANIC_CLOSE_ALL / _HALT_ALL / _HALT_ENTRIES
      5. Legacy global:        data/STRUCT_V4_PANIC_CLOSE_ALL / _HALT_ALL / _HALT_ENTRIES

    A flag at ANY tier triggers the corresponding state — first hit wins for
    logging, but the boolean OR across all tiers is what gates behaviour.
    account=None → only global + legacy tiers are checked (used by tooling).
    """
    sources: Dict[str, Optional[str]] = {"halt_entries": None,
                                          "halt_all": None,
                                          "panic_close": None}
    halt_entries = False
    halt_all = False
    panic_close = False
    candidates_panic: List[Path] = []
    candidates_halt_all: List[Path] = []
    candidates_halt_entries: List[Path] = []
    if account:
        candidates_panic.append(STRUCT_V4_DIR / f"STRUCT_V4_PANIC_CLOSE_ALL_{account}")
        candidates_halt_all.append(STRUCT_V4_DIR / f"STRUCT_V4_HALT_ALL_{account}")
        candidates_halt_entries.append(STRUCT_V4_DIR / f"STRUCT_V4_HALT_ENTRIES_{account}")
    candidates_panic.extend([GLOBAL_PANIC_CLOSE_FLAG, LEGACY_PANIC_CLOSE_FLAG])
    candidates_halt_all.extend([GLOBAL_HALT_ALL_FLAG, LEGACY_HALT_ALL_FLAG])
    candidates_halt_entries.extend([GLOBAL_HALT_ENTRIES_FLAG, LEGACY_HALT_ENTRIES_FLAG])
    for p in candidates_panic:
        if p.exists():
            panic_close = True
            if sources["panic_close"] is None:
                sources["panic_close"] = str(p)
    for p in candidates_halt_all:
        if p.exists():
            halt_all = True
            if sources["halt_all"] is None:
                sources["halt_all"] = str(p)
    for p in candidates_halt_entries:
        if p.exists():
            halt_entries = True
            if sources["halt_entries"] is None:
                sources["halt_entries"] = str(p)
    return {
        "halt_entries": halt_entries,
        "halt_all": halt_all,
        "panic_close": panic_close,
        "sources": sources,
    }


def check_per_symbol_panic(symbol: str, account: Optional[str] = None) -> Optional[str]:
    """Per-symbol panic-close flag. Returns the source path if set, else None.

    Priority: per-account → legacy global (data/STRUCT_V4_PANIC_CLOSE_<SYM>).
    """
    sym = symbol.upper()
    if account:
        per_acct = STRUCT_V4_DIR / f"STRUCT_V4_PANIC_CLOSE_{account}_{sym}"
        if per_acct.exists():
            return str(per_acct)
    legacy = DATA_DIR / f"STRUCT_V4_PANIC_CLOSE_{sym}"
    if legacy.exists():
        return str(legacy)
    return None

# ─────────────────────────────────────────────────────────────────────────────
# STATE PERSISTENCE (per-account)
# ─────────────────────────────────────────────────────────────────────────────

def load_state(ctx: AccountContext) -> Dict[str, Any]:
    if ctx.state_file.exists():
        try:
            return json.loads(ctx.state_file.read_text())
        except Exception as e:
            logger.error(f"[{ctx.account_key}] Failed to load state: {e}; starting empty")
    return {"positions": {}, "last_cycle_utc": None, "version": 1,
            "account": ctx.account_key}

def save_state(ctx: AccountContext, state: Dict[str, Any]) -> None:
    tmp = ctx.state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(ctx.state_file)

# ─────────────────────────────────────────────────────────────────────────────
# DECISION LOG (per-account, tagged with universe label)
# ─────────────────────────────────────────────────────────────────────────────

def log_decision(ctx: AccountContext, record: Dict[str, Any]) -> None:
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    path = DECISIONS_DIR / f"struct_v4_{ctx.account_key}_{date_str}.jsonl"
    record = {**record}
    record.setdefault("ts", dt.datetime.now(dt.timezone.utc).isoformat())
    record.setdefault("account", ctx.account_key)
    record.setdefault("universe", ctx.universe_label)
    record.setdefault("paper_mode", ctx.paper_mode)
    record.setdefault("go_live", ctx.go_live)
    record.setdefault("paper_reason", ctx.paper_reason)
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")

# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSE
# ─────────────────────────────────────────────────────────────────────────────

def load_universe(ctx: AccountContext) -> List[str]:
    if not ctx.universe_file.exists():
        logger.info(f"[{ctx.account_key}] Universe file missing — auto-generating")
        generate_universe_files(force=False)
    if not ctx.universe_file.exists():
        raise FileNotFoundError(f"Universe file missing after gen: {ctx.universe_file}")
    raw = json.loads(ctx.universe_file.read_text())
    if isinstance(raw, list):
        return [s.upper() for s in raw if s]
    if isinstance(raw, dict) and "symbols" in raw:
        return [s.upper() for s in raw["symbols"] if s]
    raise ValueError(f"Universe file shape unknown: {ctx.universe_file}")

# ─────────────────────────────────────────────────────────────────────────────
# KLINES — fetch via Tradier timesales (intraday) + history (daily/weekly)
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_5m_klines(api: TradierAPIClient, symbol: str,
                           lookback_days: int = KLINES_LOOKBACK_DAYS) -> Optional[pd.DataFrame]:
    """Fetch 5min bars via Tradier timesales API. Returns df with index=ts UTC,
    cols=open,high,low,close,volume. Returns None on failure.

    NOTE: Tradier timesales caps 5min/15min at ~30 days back. Use fetch_history_klines
    for long-window data.
    """
    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(days=lookback_days)
    start_str = start.strftime("%Y-%m-%d %H:%M")
    end_str = now.strftime("%Y-%m-%d %H:%M")
    try:
        raw = await api.get_timesales(symbol, interval="5min",
                                       start=start_str, end=end_str)
    except Exception as e:
        logger.warning(f"{symbol}: timesales fetch failed: {e}")
        return None
    if not raw:
        return None
    rows = []
    for r in raw:
        ts_str = r.get("time") or r.get("timestamp")
        if not ts_str:
            continue
        try:
            ts = pd.to_datetime(ts_str)
            if ts.tzinfo is None:
                # Tradier returns ET local — convert to UTC
                ts = ts.tz_localize("US/Eastern").tz_convert("UTC")
            else:
                ts = ts.tz_convert("UTC")
        except Exception:
            continue
        rows.append({
            "ts": ts,
            "open": float(r.get("open", 0) or 0),
            "high": float(r.get("high", 0) or 0),
            "low": float(r.get("low", 0) or 0),
            "close": float(r.get("close", 0) or 0),
            "volume": float(r.get("volume", 0) or 0),
        })
    if not rows:
        return None
    df = pd.DataFrame(rows).set_index("ts").sort_index()
    df = df[df["close"] > 0]
    return df

async def fetch_history_klines(api: TradierAPIClient, symbol: str, *,
                                interval: str = "daily",
                                lookback_days: int = DAILY_LOOKBACK_DAYS
                                ) -> Optional[pd.DataFrame]:
    """Fetch daily/weekly bars via /markets/history. interval ∈ {daily, weekly, monthly}."""
    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(days=lookback_days)
    try:
        raw = await api.get_history(symbol, interval=interval,
                                     start=start.strftime("%Y-%m-%d"),
                                     end=now.strftime("%Y-%m-%d"))
    except Exception as e:
        logger.warning(f"{symbol}: history fetch failed: {e}")
        return None
    if not raw:
        return None
    rows = []
    for r in raw:
        date_str = r.get("date")
        if not date_str:
            continue
        try:
            ts = pd.to_datetime(date_str).tz_localize("UTC")
        except Exception:
            continue
        rows.append({
            "ts": ts,
            "open": float(r.get("open", 0) or 0),
            "high": float(r.get("high", 0) or 0),
            "low": float(r.get("low", 0) or 0),
            "close": float(r.get("close", 0) or 0),
            "volume": float(r.get("volume", 0) or 0),
        })
    if not rows:
        return None
    df = pd.DataFrame(rows).set_index("ts").sort_index()
    df = df[df["close"] > 0]
    return df

def resample_tf(df_5m: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Resample 5m → target TF."""
    rule_map = {"5m": "5min", "15m": "15min", "1h": "60min",
                "4h": "240min", "D": "1D", "W": "1W"}
    rule = rule_map.get(tf)
    if rule is None:
        return df_5m
    if tf == "5m":
        return df_5m
    out = df_5m.resample(rule, label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna()
    return out

# ─────────────────────────────────────────────────────────────────────────────
# INDICATORS for sleeve (faithful subset of v8_struct_v4_aggressive)
# ─────────────────────────────────────────────────────────────────────────────

def _bull_cross_last(fast: pd.Series, slow: pd.Series) -> bool:
    if len(fast) < 2 or len(slow) < 2:
        return False
    return bool(fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1])

def _bear_cross_last(fast: pd.Series, slow: pd.Series) -> bool:
    if len(fast) < 2 or len(slow) < 2:
        return False
    return bool(fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1])

def _sma(series: pd.Series, length: int) -> Optional[float]:
    if len(series) < length:
        return None
    return float(series.rolling(length).mean().iloc[-1])

def _donchian_low(low: pd.Series, length: int) -> Optional[float]:
    if len(low) < length:
        return None
    return float(low.rolling(length).min().iloc[-1])

def _donchian_position(high: pd.Series, low: pd.Series, close: pd.Series,
                       length: int) -> Optional[float]:
    """Returns dc_position = (close - dc_low) / (dc_high - dc_low), 0..1."""
    if len(close) < length:
        return None
    dh = float(high.rolling(length).max().iloc[-1])
    dl = float(low.rolling(length).min().iloc[-1])
    px = float(close.iloc[-1])
    if dh > dl > 0:
        return max(0.0, min(1.0, (px - dl) / (dh - dl)))
    return None

def _bb_pctb(close: pd.Series, length: int = 20, std_mult: float = 2.0
             ) -> Optional[float]:
    """Returns BB %B = (close - lower) / (upper - lower)."""
    if len(close) < length:
        return None
    sma = close.rolling(length).mean().iloc[-1]
    sd = close.rolling(length).std().iloc[-1]
    if pd.isna(sma) or pd.isna(sd) or sd <= 0:
        return None
    upper = sma + std_mult * sd
    lower = sma - std_mult * sd
    px = float(close.iloc[-1])
    if upper > lower:
        return float((px - lower) / (upper - lower))
    return None

def compute_indicators(df_5m: pd.DataFrame,
                        df_D: Optional[pd.DataFrame] = None,
                        df_W: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """Compute the indicators needed by struct_v4 entries + exits.
    Returns flat dict; None values where data insufficient.

    df_5m: 5min bars (used for 5m/15m/1h/4h resamples).
    df_D, df_W: optional pre-fetched daily/weekly history. If None, falls back
                to resampling df_5m (which only spans ~30d → insufficient for
                sma_200_D and most HTF signals — D/W will be None in that case).

    USER 2026-05-18: also emits bb_pct_b_<TF> and dc_position_<TF> for D/4h/1h/15m/5m
    so golden_rule_htf.score_entry_htf() can read activation fields.
    """
    out: Dict[str, Any] = {}
    # Resample intraday from 5m base
    df_15m = resample_tf(df_5m, "15m")
    df_1h = resample_tf(df_5m, "1h")
    df_4h = resample_tf(df_5m, "4h")
    # Daily/weekly: prefer explicit fetch (long history); fallback to resample
    if df_D is None or len(df_D) < 30:
        df_D = resample_tf(df_5m, "D")
    if df_W is None or len(df_W) < 10:
        df_W = resample_tf(df_5m, "W")
    # Current price
    out["current_price"] = float(df_5m["close"].iloc[-1]) if len(df_5m) else None
    out["last_bar_ts"] = str(df_5m.index[-1]) if len(df_5m) else None
    # 15m: stoch_k, wt
    stoch15 = stoch_rsi(df_15m["close"], length=14, k=3, d=3)
    if stoch15 is not None and len(stoch15.dropna()) >= 2:
        out["k_15m"] = float(stoch15["k"].iloc[-1])
        out["d_15m"] = float(stoch15["d"].iloc[-1])
    else:
        out["k_15m"] = out["d_15m"] = None
    wt1_15, wt2_15 = wavetrend(df_15m, "15m")
    if wt1_15 is not None and len(wt1_15.dropna()) >= 2:
        out["wt1_15m"] = float(wt1_15.iloc[-1])
        out["wt2_15m"] = float(wt2_15.iloc[-1])
        out["bull_wt_15m"] = _bull_cross_last(wt1_15, wt2_15)
        out["bear_wt_15m"] = _bear_cross_last(wt1_15, wt2_15)
    else:
        out["wt1_15m"] = out["wt2_15m"] = None
        out["bull_wt_15m"] = out["bear_wt_15m"] = False
    # 1h
    stoch1h = stoch_rsi(df_1h["close"], length=14, k=3, d=3)
    if stoch1h is not None and len(stoch1h.dropna()) >= 2:
        out["k_1h"] = float(stoch1h["k"].iloc[-1])
        out["d_1h"] = float(stoch1h["d"].iloc[-1])
        out["k_1h_prev"] = float(stoch1h["k"].iloc[-2])
        out["d_1h_prev"] = float(stoch1h["d"].iloc[-2])
        out["bull_k_1h"] = bool(stoch1h["k"].iloc[-2] <= stoch1h["d"].iloc[-2]
                                 and stoch1h["k"].iloc[-1] > stoch1h["d"].iloc[-1])
    else:
        out["k_1h"] = out["d_1h"] = None
        out["bull_k_1h"] = False
    wt1_1h, wt2_1h = wavetrend(df_1h, "1h")
    if wt1_1h is not None and len(wt1_1h.dropna()) >= 2:
        out["wt1_1h"] = float(wt1_1h.iloc[-1])
        out["wt2_1h"] = float(wt2_1h.iloc[-1])
    # 1h dc_low — for X6
    out["dc_low_1h_20"] = _donchian_low(df_1h["low"], EXIT_X6_DC_LOW_1H_LENGTH)
    # 4h
    stoch4h = stoch_rsi(df_4h["close"], length=14, k=3, d=3)
    if stoch4h is not None and len(stoch4h.dropna()) >= 1:
        out["k_4h"] = float(stoch4h["k"].iloc[-1])
    # D
    if len(df_D) >= 2:
        rsi_D = rsi_series(df_D["close"], length=14)
        out["rsi_D"] = float(rsi_D.iloc[-1]) if rsi_D is not None and pd.notna(rsi_D.iloc[-1]) else None
        out["sma_200_D"] = _sma(df_D["close"], 200)
        out["close_D"] = float(df_D["close"].iloc[-1])
        out["prev_close_D"] = float(df_D["close"].iloc[-2])
        wt1_D, wt2_D = wavetrend(df_D, "D")
        if wt1_D is not None and len(wt1_D.dropna()) >= 2:
            out["wt1_D"] = float(wt1_D.iloc[-1])
            out["wt2_D"] = float(wt2_D.iloc[-1])
            out["bull_wt_D"] = _bull_cross_last(wt1_D, wt2_D)
            out["bear_wt_D"] = _bear_cross_last(wt1_D, wt2_D)
        else:
            out["wt1_D"] = out["wt2_D"] = None
            out["bull_wt_D"] = out["bear_wt_D"] = False
        # bull cross of close above sma_200_D
        if out["sma_200_D"] is not None and out["close_D"] is not None:
            prev_sma = _sma(df_D["close"].iloc[:-1], 200)
            if prev_sma is not None:
                out["bull_sma200_D"] = bool(out["prev_close_D"] <= prev_sma
                                             and out["close_D"] > out["sma_200_D"])
            else:
                out["bull_sma200_D"] = False
        else:
            out["bull_sma200_D"] = False
    # W
    if len(df_W) >= 2:
        wt1_W, wt2_W = wavetrend(df_W, "W")
        if wt1_W is not None and len(wt1_W.dropna()) >= 2:
            out["wt1_W"] = float(wt1_W.iloc[-1])
            out["wt2_W"] = float(wt2_W.iloc[-1])
            out["bull_wt_W"] = _bull_cross_last(wt1_W, wt2_W)
        else:
            out["bull_wt_W"] = False
    # ATR 15m
    atr15 = atr_series(df_15m, 14)
    if atr15 is not None and len(atr15.dropna()) >= 1:
        out["atr_15m"] = float(atr15.iloc[-1])

    # LT-direction filter fields (USER 2026-05-18) — generic across configured TF
    # Default (D, 200) produces same values as close_D / sma_200_D above; this
    # block makes STRUCT_V4_LT_DIRECTION_TF / _ABOVE_SMA_BARS knobs functional.
    _tf_to_df = {"5m": df_5m, "15m": df_15m, "1h": df_1h, "4h": df_4h,
                 "D": df_D, "W": df_W}
    _lt_df = _tf_to_df.get(STRUCT_V4_LT_DIRECTION_TF)
    if _lt_df is not None and len(_lt_df) >= STRUCT_V4_LT_DIRECTION_ABOVE_SMA_BARS:
        out["lt_dir_close"] = float(_lt_df["close"].iloc[-1])
        out["lt_dir_sma"] = _sma(_lt_df["close"], STRUCT_V4_LT_DIRECTION_ABOVE_SMA_BARS)
    else:
        out["lt_dir_close"] = None
        out["lt_dir_sma"] = None

    # === USER 2026-05-18 — activation fields for golden_rule_htf gate ===
    # The activation gate reads bb_pct_b_<TF> + dc_position_<TF> for D and 4h
    # (the GOLDEN_RULE_ACTIVATION_TF_LIST). Without these the gate fail-closes.
    for tf_name, df_tf in (("5m", df_5m), ("15m", df_15m), ("1h", df_1h),
                           ("4h", df_4h), ("D", df_D), ("W", df_W)):
        if df_tf is None or len(df_tf) < 20:
            continue
        bb_val = _bb_pctb(df_tf["close"], length=20, std_mult=2.0)
        dc_val = _donchian_position(df_tf["high"], df_tf["low"], df_tf["close"], 20)
        if bb_val is not None:
            out[f"bb_pct_b_{tf_name}"] = bb_val
        if dc_val is not None:
            out[f"dc_position_{tf_name}"] = dc_val

    # Path A (full structure detection) and X5 (LH/LL count) are heavy and use
    # vec_paths.structure_hh_hl. For the sleeve we approximate conservatively:
    # leave path_A_armed=False and exit_X5_armed=False unless we can compute.
    out["path_A_armed"] = False
    out["exit_X5_armed"] = False
    out["lh_ll_count_3bar_15m"] = 0
    try:
        sys.path.insert(0, str(BASE_PATH))
        npz_like: Dict[str, np.ndarray] = {}
        n_5m = len(df_5m)
        if n_5m >= 100:
            close_5m = df_5m["close"].to_numpy(dtype=np.float64)
            high_5m = df_5m["high"].to_numpy(dtype=np.float64)
            low_5m = df_5m["low"].to_numpy(dtype=np.float64)
            npz_like["close"] = close_5m
            npz_like["high"] = high_5m
            npz_like["low"] = low_5m
            # Recent LH/LL counter on 15m close (lightweight 3-bar fractal proxy):
            c15 = df_15m["close"].to_numpy(dtype=np.float64)
            if len(c15) >= 6:
                lh_ll = 0
                for k in range(max(0, len(c15) - EXIT_X5_WINDOW_BARS), len(c15)):
                    if k < 2 or k >= len(c15) - 1:
                        continue
                    if c15[k] < c15[k - 1] and c15[k - 1] > c15[k - 2]:
                        lh_ll += 1
                    if c15[k] < c15[k - 1] and c15[k - 1] < c15[k - 2]:
                        lh_ll += 1
                out["lh_ll_count_3bar_15m"] = int(lh_ll)
                out["exit_X5_armed"] = bool(lh_ll >= EXIT_X5_MIN_COUNT)
    except Exception as e:
        logger.debug(f"structure helper failed (non-fatal): {e}")
    return out

# ─────────────────────────────────────────────────────────────────────────────
# DECISION LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_entry(ind: Dict[str, Any]
                   ) -> Tuple[Optional[str], List[str], Dict[str, Any]]:
    """Return (entry_path | None, reasons[], gate_info).

    USER 2026-05-18 PATCH: All entry candidates now MUST pass
    golden_rule_htf.score_entry_htf() ACTIVATION gate before any path fires.
    The activation gate (config_tradier.GOLDEN_RULE_REQUIRE_ACTIVATION=True)
    requires at least one of [D, 4h] to show breakout
    (bb_pctb≥0.75 OR dc_pos≥0.65). Without activation, the function
    returns (False, 0, NO_ACTIVATION[...]) regardless of entry-TF score.
    """
    reasons: List[str] = []
    gate_info: Dict[str, Any] = {}
    if ind.get("current_price") is None:
        return None, ["NO_PRICE"], gate_info

    # === UNIVERSE DROP (USER 2026-05-18) ===
    # Symbols failed multi-sector-analyst gates: B&H<0, sub-sample, blowup short side, etc.
    # Block ALL entries; existing positions can still exit normally.
    symbol = ind.get("symbol", "")
    if symbol_dropped(symbol):
        reasons.append(f"UNIVERSE_DROP:{symbol}")
        gate_info["universe_dropped"] = True
        return None, reasons, gate_info
    gate_info["universe_dropped"] = False
    gate_info["position_mult"] = get_position_mult(symbol)

    # === PER-SYMBOL 20D OVERRIDES (USER 2026-05-18, T-7h to market open) ===
    # Hourly per_sym_20d_agent_stocks.py writes
    # data/hourly_reconfig/<account>/active_config_20d.json. The sleeve checks
    # the per-symbol entry here (after UNIVERSE_DROP, before LT_DIRECTION_FILTER).
    # Day-1: passive logging only — Day-2 wires downstream knob consumption.
    # Override priority is: 20D entry (wsharpe >= gate) > static PER_SYM_OVERRIDES
    # / PER_SYM_POSITION_MULT > sleeve defaults.
    account = ind.get("__account__")
    per_sym_20d_entry = get_per_sym_20d_entry(account, symbol) if account else None
    if per_sym_20d_entry is not None:
        gate_info["per_sym_20d_active"] = True
        gate_info["per_sym_20d_wsharpe"] = per_sym_20d_entry.get("wsharpe")
        gate_info["per_sym_20d_trades"] = per_sym_20d_entry.get("trades_20d")
        gate_info["per_sym_20d_tag"] = per_sym_20d_entry.get("_tag") or per_sym_20d_entry.get("winning_tag")
        _ov = per_sym_20d_entry.get("overrides") or {}
        gate_info["per_sym_20d_overrides"] = (
            list(_ov.keys()) if isinstance(_ov, dict) else [])
    else:
        gate_info["per_sym_20d_active"] = False

    # === LT-DIRECTION FILTER (USER 2026-05-18) ===
    # Block LONG entries on downtrending symbols (close_TF <= sma_N_TF).
    # Fires BEFORE GR activation gate. Symmetric to the SHORT-on-uptrend
    # mistake found in V0_baseline (-7,604% shorting uptrending tech).
    # Fail-closed: missing data → block (no entry without trend proof).
    if STRUCT_V4_LT_DIRECTION_FILTER_ENABLED:
        lt_close = ind.get("lt_dir_close")
        lt_sma = ind.get("lt_dir_sma")
        gate_info["lt_direction_filter_enabled"] = True
        gate_info["lt_direction_tf"] = STRUCT_V4_LT_DIRECTION_TF
        gate_info["lt_direction_bars"] = STRUCT_V4_LT_DIRECTION_ABOVE_SMA_BARS
        gate_info["lt_dir_close"] = lt_close
        gate_info["lt_dir_sma"] = lt_sma
        if lt_close is None or lt_sma is None:
            reasons.append(
                f"LT_DIRECTION_DOWNTREND:no_data tf={STRUCT_V4_LT_DIRECTION_TF}"
                f" bars={STRUCT_V4_LT_DIRECTION_ABOVE_SMA_BARS}")
            gate_info["lt_direction_passes"] = False
            gate_info["lt_direction_block_reason"] = "NO_DATA"
            return None, reasons, gate_info
        if lt_close <= lt_sma:
            reasons.append(
                f"LT_DIRECTION_DOWNTREND:close={lt_close:.4f}"
                f"<=sma_{STRUCT_V4_LT_DIRECTION_ABOVE_SMA_BARS}_"
                f"{STRUCT_V4_LT_DIRECTION_TF}={lt_sma:.4f}")
            gate_info["lt_direction_passes"] = False
            gate_info["lt_direction_block_reason"] = "CLOSE_LE_SMA"
            return None, reasons, gate_info
        gate_info["lt_direction_passes"] = True
    else:
        gate_info["lt_direction_filter_enabled"] = False

    # === ACTIVATION GATE (mandatory) ===
    try:
        gr_passes, gr_n_tfs, gr_detail = score_entry_htf(
            indicators=ind,
            is_long=True,
            mode="tradier",
            min_tfs=GR_MIN_TFS,
            min_ind=GR_MIN_IND,
            current_price=float(ind.get("current_price") or 0),
            invert_dc_bb=True,  # GR breakout semantics: extended = bullish
        )
    except Exception as e:
        logger.warning(f"GR gate exception (fail-closed): {e}")
        gate_info.update({"gr_passes": False, "gr_error": str(e)})
        return None, [f"GR_GATE_EXCEPTION:{e}"], gate_info

    gate_info.update({"gr_passes": gr_passes, "gr_n_tfs": gr_n_tfs,
                      "gr_detail": gr_detail, "gr_min_tfs": GR_MIN_TFS,
                      "gr_min_ind": GR_MIN_IND, "gr_invert_dc_bb": True})

    if not gr_passes:
        reasons.append(f"GR_GATE_BLOCKED:{gr_detail}")
        return None, reasons, gate_info

    # HTF filter (legacy v3_no_stop)
    htf_bull = True
    if HTF_TREND_FILTER_ENABLED:
        wt1_D = ind.get("wt1_D")
        wt2_D = ind.get("wt2_D")
        rsi_D = ind.get("rsi_D")
        if HTF_REQUIRE_WT_D_BULL:
            htf_bull = htf_bull and (wt1_D is not None and wt2_D is not None and wt1_D > wt2_D)
        if HTF_REQUIRE_RSI_D_MIN > 0:
            htf_bull = htf_bull and (rsi_D is not None and rsi_D >= HTF_REQUIRE_RSI_D_MIN)
    # Path A: structure breakout — sleeve runs conservative; only fire if armed
    if ind.get("path_A_armed"):
        return "A", ["path_A_struct_armed", f"GR_OK:{gr_detail}"], gate_info
    # Path B: oversold reversal
    if (ind.get("k_15m") is not None and ind["k_15m"] < PATH_B_K15_MAX
            and ind.get("k_1h") is not None and ind["k_1h"] < PATH_B_K1H_MAX
            and ind.get("bull_wt_15m") and htf_bull):
        return "B", [f"k15={ind['k_15m']:.1f}<{PATH_B_K15_MAX}",
                     f"k1h={ind['k_1h']:.1f}<{PATH_B_K1H_MAX}",
                     "bull_wt_15m", "htf_bull", f"GR_OK:{gr_detail}"], gate_info
    # Path C: K_1h bull cross from oversold
    if (ind.get("bull_k_1h") and ind.get("k_1h") is not None
            and ind["k_1h"] < PATH_C_K1H_CROSS_BELOW and htf_bull):
        return "C", [f"bull_k_1h", f"k1h={ind['k_1h']:.1f}<{PATH_C_K1H_CROSS_BELOW}",
                     "htf_bull", f"GR_OK:{gr_detail}"], gate_info
    # Path D: wt_D bull cross + rsi_D > 40
    if (ind.get("bull_wt_D") and ind.get("rsi_D") is not None
            and ind["rsi_D"] > PATH_D_RSI_D_MIN and htf_bull):
        return "D", ["bull_wt_D", f"rsi_D={ind['rsi_D']:.1f}>{PATH_D_RSI_D_MIN}",
                     "htf_bull", f"GR_OK:{gr_detail}"], gate_info
    # Path E: sma_200 reclaim
    if (ind.get("bull_sma200_D") and ind.get("rsi_D") is not None
            and ind["rsi_D"] > PATH_E_RSI_D_MIN):
        return "E", ["bull_sma200_D", f"rsi_D={ind['rsi_D']:.1f}>{PATH_E_RSI_D_MIN}",
                     f"GR_OK:{gr_detail}"], gate_info
    # Path F: weekly bullish flip
    if ind.get("bull_wt_W"):
        return "F", ["bull_wt_W", f"GR_OK:{gr_detail}"], gate_info
    reasons.append("NO_ENTRY_SETUP_DESPITE_GR_OK")
    return None, reasons, gate_info

def evaluate_exit(ind: Dict[str, Any], position: Dict[str, Any]
                  ) -> Tuple[Optional[str], List[str]]:
    """Return (exit_path | None, reasons[]). Priority: X7, X6, X1, X4, X5, X2."""
    reasons: List[str] = []
    px = ind.get("current_price")
    if px is None:
        return None, ["NO_PRICE"]
    entry_px = float(position.get("entry_price", 0) or 0)
    if entry_px <= 0:
        return None, ["NO_ENTRY_PRICE"]
    gain_pct = (px - entry_px) / entry_px * 100.0
    # Min hold: 1 trading day
    opened_at = position.get("opened_at")
    if opened_at:
        try:
            opened_dt = pd.to_datetime(opened_at, utc=True)
            now = pd.Timestamp.now(tz="UTC")
            held_days = (now - opened_dt).total_seconds() / 86400.0
            if held_days < MIN_HOLD_DAYS:
                # Min-hold gates ALL exits except X7 absolute floor + panic
                if gain_pct > EXIT_X7_ABSOLUTE_FLOOR_PCT:
                    return None, [f"MIN_HOLD held={held_days:.2f}d <"
                                  f" {MIN_HOLD_DAYS}d", f"gain={gain_pct:.2f}%"]
        except Exception:
            pass
    # X7: absolute hard floor (belt-and-suspenders)
    if gain_pct <= EXIT_X7_ABSOLUTE_FLOOR_PCT:
        return "X7", [f"gain={gain_pct:.2f}% <= {EXIT_X7_ABSOLUTE_FLOOR_PCT}%"]
    # X6: dc_low_1h(20) break
    dc_low_1h = ind.get("dc_low_1h_20")
    entry_dc_low = position.get("entry_bar_dc_low_1h_20")
    if entry_dc_low and px < entry_dc_low:
        return "X6", [f"px={px:.4f} < entry_bar_dc_low_1h(20)={entry_dc_low:.4f}"]
    if dc_low_1h is not None and px < dc_low_1h:
        return "X6", [f"px={px:.4f} < current dc_low_1h(20)={dc_low_1h:.4f}"]
    # X1: top-catch
    if (ind.get("k_15m") is not None and ind["k_15m"] > EXIT_X1_K15_MIN
            and ind.get("bear_wt_15m")):
        return "X1", [f"k_15m={ind['k_15m']:.1f}>{EXIT_X1_K15_MIN}", "bear_wt_15m"]
    # X4: bear WT_D
    if ind.get("bear_wt_D"):
        return "X4", ["bear_wt_D"]
    # X5: structural flip
    if ind.get("exit_X5_armed"):
        return "X5", [f"lh_ll_count={ind.get('lh_ll_count_3bar_15m')}"
                      f">={EXIT_X5_MIN_COUNT}"]
    # X2: trailing 12% (only when in profit ≥0.5%)
    peak_px = float(position.get("peak_price", entry_px) or entry_px)
    if peak_px > 0 and gain_pct > 0.5:
        trail_px = peak_px * (1 - EXIT_X2_TRAILING_PCT / 100.0)
        if px < trail_px:
            return "X2", [f"px={px:.4f} < trail={trail_px:.4f}",
                          f"peak={peak_px:.4f}", f"trail_pct={EXIT_X2_TRAILING_PCT}"]
    return None, ["NO_EXIT"]

# ─────────────────────────────────────────────────────────────────────────────
# ORDER PLACEMENT
# ─────────────────────────────────────────────────────────────────────────────

async def place_buy_order(api: TradierAPIClient, ctx: AccountContext,
                          symbol: str, qty: int, price: float) -> Dict[str, Any]:
    """Limit buy at mid price. Returns API response or paper-mode stub."""
    log_entry = {
        "intent": "BUY", "symbol": symbol, "qty": qty,
        "limit_price": price, "paper_mode": ctx.paper_mode,
        "go_live": ctx.go_live, "account": ctx.account_key,
        "paper_reason": ctx.paper_reason,
    }
    if ctx.paper_mode or not ctx.go_live:
        log_entry["result"] = "PAPER_NOOP"
        logger.info(f"[{ctx.account_key}][PAPER] BUY {symbol} qty={qty} @{price:.2f} "
                    f"reason={ctx.paper_reason}")
        return log_entry
    try:
        res = await api.place_order(ctx.account_key, symbol, side="buy",
                                     quantity=qty, order_type="limit",
                                     price=price, duration="day")
        log_entry["result"] = res
        logger.info(f"[{ctx.account_key}][LIVE] BUY {symbol} qty={qty} @{price:.2f} resp={res}")
        return log_entry
    except Exception as e:
        log_entry["result"] = {"error": str(e)}
        logger.error(f"[{ctx.account_key}][LIVE] BUY {symbol} EXCEPTION {e}")
        return log_entry

async def place_sell_order(api: TradierAPIClient, ctx: AccountContext,
                           symbol: str, qty: int, market: bool = True
                           ) -> Dict[str, Any]:
    log_entry = {
        "intent": "SELL", "symbol": symbol, "qty": qty,
        "order_type": "market" if market else "limit",
        "paper_mode": ctx.paper_mode, "go_live": ctx.go_live,
        "account": ctx.account_key, "paper_reason": ctx.paper_reason,
    }
    if ctx.paper_mode or not ctx.go_live:
        log_entry["result"] = "PAPER_NOOP"
        logger.info(f"[{ctx.account_key}][PAPER] SELL {symbol} qty={qty} "
                    f"reason={ctx.paper_reason}")
        return log_entry
    try:
        res = await api.place_order(ctx.account_key, symbol, side="sell",
                                     quantity=qty, order_type="market",
                                     duration="day")
        log_entry["result"] = res
        logger.info(f"[{ctx.account_key}][LIVE] SELL {symbol} qty={qty} resp={res}")
        return log_entry
    except Exception as e:
        log_entry["result"] = {"error": str(e)}
        logger.error(f"[{ctx.account_key}][LIVE] SELL {symbol} EXCEPTION {e}")
        return log_entry

# ─────────────────────────────────────────────────────────────────────────────
# POSITION RECONCILIATION
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_live_positions(api: TradierAPIClient, ctx: AccountContext
                               ) -> Dict[str, Dict[str, Any]]:
    """Returns dict of {symbol: {qty, cost_basis, side}}. Empty {} only on
    confirmed-empty; None response = API failure, returns {}."""
    raw = await api.get_account_positions(ctx.account_key)
    if raw is None:
        logger.warning(f"[{ctx.account_key}] Position fetch returned None (API failure)")
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for p in raw:
        sym = p.get("symbol", "").upper()
        qty = float(p.get("quantity", 0) or 0)
        cost = float(p.get("cost_basis", 0) or 0)
        if qty == 0 or not sym:
            continue
        out[sym] = {"qty": qty, "cost_basis": cost,
                    "side": "LONG" if qty > 0 else "SHORT"}
    return out

# ─────────────────────────────────────────────────────────────────────────────
# CYCLE
# ─────────────────────────────────────────────────────────────────────────────

def is_market_hours() -> bool:
    now = dt.datetime.now(dt.timezone.utc).time()
    return MARKET_OPEN_UTC <= now <= MARKET_CLOSE_UTC

async def run_one_cycle(api: TradierAPIClient, ctx: AccountContext,
                        state: Dict[str, Any], universe: List[str], *,
                        single_sym: Optional[str] = None) -> Dict[str, Any]:
    """Single pass over universe. Returns summary."""
    ctx.refresh_paper_window()
    cycle_ts = dt.datetime.now(dt.timezone.utc).isoformat()
    # 1. HALT FLAGS — ALWAYS FIRST (per-account aware)
    halt = check_halt_flags(account=ctx.account_key)
    logger.info(f"[{ctx.account_key}] Cycle start {cycle_ts} | halts={halt} | "
                f"paper={ctx.paper_mode} go_live={ctx.go_live} "
                f"reason={ctx.paper_reason}")
    log_decision(ctx, {"ts": cycle_ts, "event": "CYCLE_START",
                       "halts": halt,
                       "universe_size": len(universe)})
    # 2. Panic close — close all sleeve positions at market
    if halt["panic_close"]:
        logger.warning(f"[{ctx.account_key}] PANIC_CLOSE_ALL flag detected — closing all sleeve positions")
        live_pos = await fetch_live_positions(api, ctx)
        for sym, p in list(state.get("positions", {}).items()):
            broker_qty = live_pos.get(sym, {}).get("qty", 0)
            if broker_qty > 0:
                res = await place_sell_order(api, ctx, sym, int(broker_qty), market=True)
                log_decision(ctx, {"event": "PANIC_CLOSE", "symbol": sym,
                                   "qty": broker_qty, "order": res})
            state["positions"].pop(sym, None)
        save_state(ctx, state)
        return {"event": "PANIC_CLOSE_DONE"}
    # 3. Pull live positions for reconciliation
    live_pos = await fetch_live_positions(api, ctx)
    sleeve_positions = state.get("positions", {})
    # 4. Per-symbol pass
    summary = {"checked": 0, "entries_fired": 0, "exits_fired": 0,
               "skipped": 0, "errors": 0, "gr_blocked": 0}
    syms = [single_sym] if single_sym else universe
    for sym in syms:
        try:
            summary["checked"] += 1
            per_sym_panic_src = check_per_symbol_panic(sym, account=ctx.account_key)
            if per_sym_panic_src:
                broker_qty = int(live_pos.get(sym, {}).get("qty", 0) or 0)
                if broker_qty > 0:
                    res = await place_sell_order(api, ctx, sym, broker_qty, market=True)
                    log_decision(ctx, {"event": "PER_SYMBOL_PANIC_CLOSE", "symbol": sym,
                                       "qty": broker_qty, "order": res,
                                       "flag_source": per_sym_panic_src})
                if sym in sleeve_positions:
                    sleeve_positions.pop(sym, None)
                save_state(ctx, state)
                continue
            df_5m = await fetch_5m_klines(api, sym, lookback_days=KLINES_LOOKBACK_DAYS)
            if df_5m is None or len(df_5m) < 100:
                summary["skipped"] += 1
                log_decision(ctx, {"event": "SKIP_NO_DATA", "symbol": sym,
                                   "bars_5m": 0 if df_5m is None else len(df_5m)})
                continue
            df_D = await fetch_history_klines(api, sym, interval="daily",
                                               lookback_days=DAILY_LOOKBACK_DAYS)
            df_W = await fetch_history_klines(api, sym, interval="weekly",
                                               lookback_days=WEEKLY_LOOKBACK_DAYS)
            ind = compute_indicators(df_5m, df_D=df_D, df_W=df_W)
            # Inject identifiers so evaluate_entry can resolve UNIVERSE_DROPS,
            # per-symbol mults, and per-symbol 20D overrides (USER 2026-05-18).
            ind["symbol"] = sym
            ind["__account__"] = ctx.account_key
            in_sleeve = sym in sleeve_positions
            in_broker = sym in live_pos and live_pos[sym]["qty"] > 0
            position_state = ("LONG" if in_sleeve and in_broker else
                              "SLEEVE_ONLY" if in_sleeve and not in_broker else
                              "BROKER_ONLY" if (not in_sleeve) and in_broker else
                              "FLAT")
            if position_state == "BROKER_ONLY":
                # Not our position — IGNORE (other strategies may hold)
                log_decision(ctx, {"event": "SKIP_BROKER_ONLY", "symbol": sym,
                                   "broker_qty": live_pos[sym]["qty"]})
                continue
            if position_state == "SLEEVE_ONLY":
                # PAPER mode: broker never fills our orders, so a sleeve position
                # without broker presence is the expected state. Treat as LONG
                # so exits can still evaluate. LIVE mode: real state drift → drop.
                if ctx.paper_mode:
                    position_state = "LONG"
                else:
                    log_decision(ctx, {"event": "STATE_DRIFT_SLEEVE_ONLY", "symbol": sym,
                                       "sleeve": sleeve_positions[sym]})
                    sleeve_positions.pop(sym, None)
                    continue
            # ── LONG: try exit ─────────────────────────────────────────
            if position_state == "LONG":
                pos = sleeve_positions[sym]
                px = ind["current_price"]
                if px and px > float(pos.get("peak_price", 0) or 0):
                    pos["peak_price"] = px
                if halt["halt_all"]:
                    log_decision(ctx, {"event": "HALT_ALL_BLOCK_EXIT", "symbol": sym,
                                       "position": pos})
                    continue
                exit_path, exit_reasons = evaluate_exit(ind, pos)
                snapshot = _ind_snapshot(ind)
                if exit_path:
                    qty = (int(live_pos[sym]["qty"]) if sym in live_pos
                           else int(pos.get("qty", 0) or 0))
                    order_res = await place_sell_order(api, ctx, sym, qty, market=True)
                    log_decision(ctx, {"event": "EXIT", "symbol": sym,
                                       "position_state": position_state,
                                       "exit_path": exit_path,
                                       "reasons": exit_reasons,
                                       "qty": qty,
                                       "entry_price": pos.get("entry_price"),
                                       "exit_price": px,
                                       "peak_price": pos.get("peak_price"),
                                       "indicators_snapshot": snapshot,
                                       "order": order_res})
                    sleeve_positions.pop(sym, None)
                    summary["exits_fired"] += 1
                else:
                    log_decision(ctx, {"event": "HOLD", "symbol": sym,
                                       "position_state": position_state,
                                       "reasons": exit_reasons,
                                       "entry_price": pos.get("entry_price"),
                                       "current_price": px,
                                       "peak_price": pos.get("peak_price"),
                                       "indicators_snapshot": snapshot})
                continue
            # ── FLAT: try entry ────────────────────────────────────────
            if position_state == "FLAT":
                if halt["halt_entries"] or halt["halt_all"]:
                    log_decision(ctx, {"event": "HALT_BLOCK_ENTRY", "symbol": sym,
                                       "halt": halt})
                    continue
                # Capacity check
                n_sleeve = len(sleeve_positions)
                deployed = sum(float(p.get("cost_basis", 0) or 0)
                                for p in sleeve_positions.values())
                if n_sleeve >= MAX_CONCURRENT_POSITIONS:
                    log_decision(ctx, {"event": "BLOCK_MAX_POSITIONS", "symbol": sym,
                                       "n_sleeve": n_sleeve,
                                       "max": MAX_CONCURRENT_POSITIONS})
                    continue
                if deployed + POSITION_NOTIONAL_USD > MAX_TOTAL_DEPLOYED_USD:
                    log_decision(ctx, {"event": "BLOCK_MAX_DEPLOYED", "symbol": sym,
                                       "deployed_usd": deployed,
                                       "notional": POSITION_NOTIONAL_USD,
                                       "max": MAX_TOTAL_DEPLOYED_USD})
                    continue
                entry_path, entry_reasons, gate_info = evaluate_entry(ind)
                snapshot = _ind_snapshot(ind)
                if not gate_info.get("gr_passes", False):
                    summary["gr_blocked"] += 1
                if entry_path:
                    px = ind["current_price"]
                    if not px or px <= 0:
                        continue
                    qty = max(1, int(POSITION_NOTIONAL_USD / px))
                    cost = qty * px
                    order_res = await place_buy_order(api, ctx, sym, qty, price=px)
                    sleeve_positions[sym] = {
                        "symbol": sym, "side": "LONG", "qty": qty,
                        "entry_price": px, "cost_basis": cost,
                        "peak_price": px,
                        "entry_path": entry_path,
                        "entry_reasons": entry_reasons,
                        "opened_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "entry_bar_dc_low_1h_20": ind.get("dc_low_1h_20"),
                        "paper_mode": ctx.paper_mode,
                        "account": ctx.account_key,
                        "universe": ctx.universe_label,
                    }
                    log_decision(ctx, {"event": "ENTRY", "symbol": sym,
                                       "position_state": "FLAT",
                                       "entry_path": entry_path,
                                       "reasons": entry_reasons,
                                       "gate_info": gate_info,
                                       "qty": qty, "limit_price": px,
                                       "cost_basis": cost,
                                       "indicators_snapshot": snapshot,
                                       "order": order_res})
                    summary["entries_fired"] += 1
                else:
                    log_decision(ctx, {"event": "NO_ENTRY", "symbol": sym,
                                       "position_state": "FLAT",
                                       "reasons": entry_reasons,
                                       "gate_info": gate_info,
                                       "indicators_snapshot": snapshot})
        except Exception as e:
            summary["errors"] += 1
            logger.exception(f"[{ctx.account_key}] {sym}: cycle error: {e}")
            log_decision(ctx, {"event": "ERROR", "symbol": sym, "error": str(e)})
    state["positions"] = sleeve_positions
    state["last_cycle_utc"] = cycle_ts
    save_state(ctx, state)
    log_decision(ctx, {"event": "CYCLE_END", "summary": summary})
    logger.info(f"[{ctx.account_key}] Cycle done: {summary}")
    return summary

def _ind_snapshot(ind: Dict[str, Any]) -> Dict[str, Any]:
    """Compact indicators for decision log."""
    keys = ["current_price", "k_15m", "k_1h", "k_4h",
            "wt1_15m", "wt2_15m", "wt1_D", "wt2_D",
            "rsi_D", "sma_200_D", "close_D",
            "lt_dir_close", "lt_dir_sma",
            "bull_wt_15m", "bear_wt_15m", "bull_wt_D", "bear_wt_D",
            "bull_k_1h", "bull_sma200_D", "bull_wt_W",
            "dc_low_1h_20", "atr_15m", "exit_X5_armed",
            "lh_ll_count_3bar_15m",
            "bb_pct_b_D", "bb_pct_b_4h", "bb_pct_b_1h",
            "dc_position_D", "dc_position_4h", "dc_position_1h",
            "last_bar_ts"]
    out = {}
    for k in keys:
        v = ind.get(k)
        if v is None:
            continue
        if isinstance(v, float) and not (np.isnan(v) or np.isinf(v)):
            out[k] = round(v, 4)
        else:
            out[k] = v
    return out

# ─────────────────────────────────────────────────────────────────────────────
# DAEMON
# ─────────────────────────────────────────────────────────────────────────────

async def daemon_loop(ctx: AccountContext):
    universe = load_universe(ctx)
    logger.info(f"[{ctx.account_key}] Universe loaded: {len(universe)} syms "
                f"({ctx.universe_label})")
    state = load_state(ctx)
    cfg = TradierConfig()
    api = TradierAPIClient(config=cfg, account_key=ctx.account_key)
    await api.connect()
    try:
        while True:
            if not is_market_hours():
                logger.info(f"[{ctx.account_key}] Outside market hours (13:30–20:00 UTC) — sleeping 5min")
                await asyncio.sleep(CYCLE_INTERVAL_SECONDS)
                continue
            try:
                await run_one_cycle(api, ctx, state, universe)
            except Exception:
                logger.exception(f"[{ctx.account_key}] Cycle failed; sleeping then retrying")
            await asyncio.sleep(CYCLE_INTERVAL_SECONDS)
    finally:
        await api.close()

async def single_cycle_test(ctx: AccountContext, symbol: Optional[str] = None):
    """Run one cycle then exit. Used for paper-mode test."""
    universe = load_universe(ctx)
    if symbol:
        universe = [symbol] if symbol in universe else universe[:1]
    state = load_state(ctx)
    cfg = TradierConfig()
    api = TradierAPIClient(config=cfg, account_key=ctx.account_key)
    await api.connect()
    try:
        summary = await run_one_cycle(api, ctx, state, universe,
                                       single_sym=symbol if symbol else None)
        return summary
    finally:
        await api.close()

# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Tradier struct_v4 sleeve "
                                              "(dual-account: trb live / trc paper)")
    ap.add_argument("--account", choices=["trb", "trc"], required=False,
                     help="trb = live-eligible (ranked universe), "
                          "trc = paper only (sectors_tradier union)")
    ap.add_argument("--test", action="store_true",
                     help="Run one cycle and exit (no daemon loop)")
    ap.add_argument("--symbol", default=None,
                     help="Test on a single symbol only (used with --test)")
    ap.add_argument("--daemon", action="store_true",
                     help="Run continuous daemon loop")
    ap.add_argument("--gen-universe", action="store_true",
                     help="(Re)generate universe files for both accounts and exit")
    ap.add_argument("--force-gen", action="store_true",
                     help="With --gen-universe: overwrite existing files")
    args = ap.parse_args()

    if args.gen_universe:
        counts = generate_universe_files(force=args.force_gen)
        print(json.dumps({"generated": counts,
                          "files": [
                              str(DATA_DIR / "struct_v4_universe_trb.json"),
                              str(DATA_DIR / "struct_v4_universe_trc.json"),
                          ]}, indent=2))
        return

    if not args.account:
        ap.error("--account is required for --test/--daemon (use --gen-universe alone)")

    # Always ensure universe files exist before running
    generate_universe_files(force=False)

    ctx = AccountContext(args.account)
    banner = (f"STRUCT_V4 SLEEVE — account={ctx.account_key} "
              f"({ctx.universe_label}) | paper={ctx.paper_mode} "
              f"go_live={ctx.go_live} reason={ctx.paper_reason} | "
              f"notional=${POSITION_NOTIONAL_USD:.0f} | "
              f"max_pos={MAX_CONCURRENT_POSITIONS} | "
              f"max_deployed=${MAX_TOTAL_DEPLOYED_USD:.0f} | "
              f"gr_min_tfs={GR_MIN_TFS} gr_min_ind={GR_MIN_IND} | "
              f"lt_dir_filter={STRUCT_V4_LT_DIRECTION_FILTER_ENABLED} "
              f"tf={STRUCT_V4_LT_DIRECTION_TF} "
              f"bars={STRUCT_V4_LT_DIRECTION_ABOVE_SMA_BARS}")
    logger.info(banner)
    if ctx.account_key == "trb" and not ctx.paper_mode and not ctx.go_live:
        logger.warning("[trb] STRUCT_V4_PAPER_MODE=false but STRUCT_V4_GO_LIVE!=true → "
                       "no live orders will be placed. Set STRUCT_V4_GO_LIVE=true "
                       "to enable real trading (after explicit user sign-off).")
    if args.test:
        result = asyncio.run(single_cycle_test(ctx, symbol=args.symbol))
        logger.info(f"[{ctx.account_key}] TEST RESULT: {result}")
        print(json.dumps(result, indent=2, default=str))
        return
    if args.daemon:
        asyncio.run(daemon_loop(ctx))
        return
    ap.print_help()

if __name__ == "__main__":
    main()
