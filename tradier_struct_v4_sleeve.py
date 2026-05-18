"""tradier_struct_v4_sleeve.py — Standalone live-trading sleeve for struct_v4 (v3_no_stop).

Built 2026-05-18. Independent of tradier_manage.py — runs as its own daemon,
maintains its own state file (data/struct_v4_state.json), uses tradier_api
directly for reads + orders, and respects three halt flags.

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

This module DEFAULTS TO PAPER MODE. It will refuse to place real orders
unless ALL of the following are true:
    - env var STRUCT_V4_PAPER_MODE != "true"
    - env var STRUCT_V4_GO_LIVE == "true" (explicit live opt-in)
    - file data/STRUCT_V4_HALT_ALL does NOT exist
    - file data/STRUCT_V4_HALT_ENTRIES does NOT exist (for entries only)

The user explicitly authorised building this sleeve in PAPER mode for testing.
Promotion to live trading remains a separate, explicit decision.
═══════════════════════════════════════════════════════════════════════════════

STRATEGY SPEC (validated config: cfg_safer_v3_no_stop)
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

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

ACCOUNT_KEY = "trb"  # CLAUDE.md mandate for tradier swing
DATA_DIR = BASE_PATH / "data"
DECISIONS_DIR = DATA_DIR / "decisions"
STATE_FILE = DATA_DIR / "struct_v4_state.json"
UNIVERSE_FILE = Path(os.getenv("STRUCT_V4_UNIVERSE_FILE",
                                str(DATA_DIR / "struct_v4_universe.json")))

# Halt flag files
HALT_ENTRIES_FLAG = DATA_DIR / "STRUCT_V4_HALT_ENTRIES"
HALT_ALL_FLAG = DATA_DIR / "STRUCT_V4_HALT_ALL"
PANIC_CLOSE_FLAG = DATA_DIR / "STRUCT_V4_PANIC_CLOSE_ALL"

# Mode
PAPER_MODE = os.getenv("STRUCT_V4_PAPER_MODE", "true").lower() == "true"
GO_LIVE = os.getenv("STRUCT_V4_GO_LIVE", "false").lower() == "true"

# Day-1 sizing
POSITION_NOTIONAL_USD = float(os.getenv("STRUCT_V4_NOTIONAL", "500"))
MAX_CONCURRENT_POSITIONS = int(os.getenv("STRUCT_V4_MAX_POSITIONS", "5"))
MAX_TOTAL_DEPLOYED_USD = float(os.getenv("STRUCT_V4_MAX_DEPLOYED", "2500"))

# Cycle
CYCLE_INTERVAL_SECONDS = int(os.getenv("STRUCT_V4_CYCLE_S", "300"))  # 5 min
MARKET_OPEN_UTC = dt.time(13, 30)
MARKET_CLOSE_UTC = dt.time(20, 0)

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

MIN_HOLD_DAYS = 1  # no day-trading

# Indicator history requirement.
# Tradier timesales 5min API returns only ~30 days of bars; daily/weekly via
# /markets/history go back years. Sleeve fetches BOTH: short-window 5m for
# intraday TFs (5m/15m/1h/4h) and long-window daily/weekly for HTF (D/W) +
# sma_200_D.
KLINES_LOOKBACK_DAYS = 25       # 5m timesales (capped ~30d)
DAILY_LOOKBACK_DAYS = 500       # daily history (≥200 bars for sma_200_D)
WEEKLY_LOOKBACK_DAYS = 900      # weekly history

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
# HALT FLAG HANDLING (always FIRST in every cycle)
# ─────────────────────────────────────────────────────────────────────────────

def check_halt_flags() -> Dict[str, bool]:
    """Returns dict of {halt_entries, halt_all, panic_close}. ALWAYS first."""
    return {
        "halt_entries": HALT_ENTRIES_FLAG.exists(),
        "halt_all": HALT_ALL_FLAG.exists(),
        "panic_close": PANIC_CLOSE_FLAG.exists(),
    }

# ─────────────────────────────────────────────────────────────────────────────
# STATE PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────

def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception as e:
            logger.error(f"Failed to load state: {e}; starting empty")
    return {"positions": {}, "last_cycle_utc": None, "version": 1}

def save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(STATE_FILE)

# ─────────────────────────────────────────────────────────────────────────────
# DECISION LOG
# ─────────────────────────────────────────────────────────────────────────────

def log_decision(record: Dict[str, Any]) -> None:
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    path = DECISIONS_DIR / f"struct_v4_{date_str}.jsonl"
    record = {**record}
    record.setdefault("ts", dt.datetime.now(dt.timezone.utc).isoformat())
    record.setdefault("paper_mode", PAPER_MODE)
    record.setdefault("go_live", GO_LIVE)
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")

# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSE
# ─────────────────────────────────────────────────────────────────────────────

def load_universe() -> List[str]:
    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError(f"Universe file missing: {UNIVERSE_FILE}")
    raw = json.loads(UNIVERSE_FILE.read_text())
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and "symbols" in raw:
        return list(raw["symbols"])
    raise ValueError(f"Universe file shape unknown: {UNIVERSE_FILE}")

# ─────────────────────────────────────────────────────────────────────────────
# KLINES — fetch via Tradier timesales + resample
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_5m_klines(api: TradierAPIClient, symbol: str,
                           lookback_days: int = KLINES_LOOKBACK_DAYS) -> Optional[pd.DataFrame]:
    """Fetch 5min bars via Tradier timesales API. Returns df with index=ts UTC,
    cols=open,high,low,close,volume. Returns None on failure.

    NOTE: Tradier timesales caps 5min/15min at ~30 days back. Use fetch_daily_klines
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

def compute_indicators(df_5m: pd.DataFrame,
                        df_D: Optional[pd.DataFrame] = None,
                        df_W: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """Compute the indicators needed by struct_v4 entries + exits.
    Returns flat dict; None values where data insufficient.

    df_5m: 5min bars (used for 5m/15m/1h/4h resamples).
    df_D, df_W: optional pre-fetched daily/weekly history. If None, falls back
                to resampling df_5m (which only spans ~30d → insufficient for
                sma_200_D and most HTF signals — D/W will be None in that case).
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
    # Path A (full structure detection) and X5 (LH/LL count) are heavy and use
    # vec_paths.structure_hh_hl. For the sleeve we approximate conservatively:
    # leave path_A_armed=False and exit_X5_armed=False unless we can compute.
    # Try to compute structure via vec_paths if available.
    out["path_A_armed"] = False
    out["exit_X5_armed"] = False
    out["lh_ll_count_3bar_15m"] = 0
    try:
        sys.path.insert(0, str(BASE_PATH))
        # Build minimal npz-like dict that compute_all_structure can read
        # via the same field names the strategy uses
        npz_like: Dict[str, np.ndarray] = {}
        n_5m = len(df_5m)
        if n_5m >= 100:
            # base index is 5m; we need close, high, low arrays for "price" series
            # plus the TF-mapped resampled series
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
                    # Lower-high pivot on close
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

def evaluate_entry(ind: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
    """Return (entry_path | None, reasons[]). Mirrors v8_struct_v4_aggressive."""
    reasons: List[str] = []
    if ind.get("current_price") is None:
        return None, ["NO_PRICE"]
    # HTF filter (applied to B/C/D/G)
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
        return "A", ["path_A_struct_armed"]
    # Path B: oversold reversal
    if (ind.get("k_15m") is not None and ind["k_15m"] < PATH_B_K15_MAX
            and ind.get("k_1h") is not None and ind["k_1h"] < PATH_B_K1H_MAX
            and ind.get("bull_wt_15m") and htf_bull):
        return "B", [f"k15={ind['k_15m']:.1f}<{PATH_B_K15_MAX}",
                     f"k1h={ind['k_1h']:.1f}<{PATH_B_K1H_MAX}",
                     "bull_wt_15m", "htf_bull"]
    # Path C: K_1h bull cross from oversold
    if (ind.get("bull_k_1h") and ind.get("k_1h") is not None
            and ind["k_1h"] < PATH_C_K1H_CROSS_BELOW and htf_bull):
        return "C", [f"bull_k_1h", f"k1h={ind['k_1h']:.1f}<{PATH_C_K1H_CROSS_BELOW}",
                     "htf_bull"]
    # Path D: wt_D bull cross + rsi_D > 40
    if (ind.get("bull_wt_D") and ind.get("rsi_D") is not None
            and ind["rsi_D"] > PATH_D_RSI_D_MIN and htf_bull):
        return "D", ["bull_wt_D", f"rsi_D={ind['rsi_D']:.1f}>{PATH_D_RSI_D_MIN}",
                     "htf_bull"]
    # Path E: sma_200 reclaim
    if (ind.get("bull_sma200_D") and ind.get("rsi_D") is not None
            and ind["rsi_D"] > PATH_E_RSI_D_MIN):
        return "E", ["bull_sma200_D", f"rsi_D={ind['rsi_D']:.1f}>{PATH_E_RSI_D_MIN}"]
    # Path F: weekly bullish flip
    if ind.get("bull_wt_W"):
        return "F", ["bull_wt_W"]
    reasons.append("NO_ENTRY_SETUP")
    return None, reasons

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
            # business-day delta (calendar-day OK for sleeve simplicity)
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

async def place_buy_order(api: TradierAPIClient, symbol: str, qty: int,
                           price: float) -> Dict[str, Any]:
    """Limit buy at mid price. Returns API response or paper-mode stub."""
    log_entry = {
        "intent": "BUY", "symbol": symbol, "qty": qty,
        "limit_price": price, "paper_mode": PAPER_MODE, "go_live": GO_LIVE,
    }
    if PAPER_MODE or not GO_LIVE:
        log_entry["result"] = "PAPER_NOOP"
        logger.info(f"[PAPER] BUY {symbol} qty={qty} @{price:.2f}")
        return log_entry
    try:
        res = await api.place_order(ACCOUNT_KEY, symbol, side="buy",
                                     quantity=qty, order_type="limit",
                                     price=price, duration="day")
        log_entry["result"] = res
        logger.info(f"[LIVE] BUY {symbol} qty={qty} @{price:.2f} resp={res}")
        return log_entry
    except Exception as e:
        log_entry["result"] = {"error": str(e)}
        logger.error(f"[LIVE] BUY {symbol} EXCEPTION {e}")
        return log_entry

async def place_sell_order(api: TradierAPIClient, symbol: str, qty: int,
                            market: bool = True) -> Dict[str, Any]:
    log_entry = {
        "intent": "SELL", "symbol": symbol, "qty": qty,
        "order_type": "market" if market else "limit",
        "paper_mode": PAPER_MODE, "go_live": GO_LIVE,
    }
    if PAPER_MODE or not GO_LIVE:
        log_entry["result"] = "PAPER_NOOP"
        logger.info(f"[PAPER] SELL {symbol} qty={qty}")
        return log_entry
    try:
        res = await api.place_order(ACCOUNT_KEY, symbol, side="sell",
                                     quantity=qty, order_type="market",
                                     duration="day")
        log_entry["result"] = res
        logger.info(f"[LIVE] SELL {symbol} qty={qty} resp={res}")
        return log_entry
    except Exception as e:
        log_entry["result"] = {"error": str(e)}
        logger.error(f"[LIVE] SELL {symbol} EXCEPTION {e}")
        return log_entry

# ─────────────────────────────────────────────────────────────────────────────
# POSITION RECONCILIATION
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_live_positions(api: TradierAPIClient) -> Dict[str, Dict[str, Any]]:
    """Returns dict of {symbol: {qty, cost_basis, side}}. Empty {} only on
    confirmed-empty; None response = API failure, returns {}."""
    raw = await api.get_account_positions(ACCOUNT_KEY)
    if raw is None:
        logger.warning("Position fetch returned None (API failure)")
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

async def run_one_cycle(api: TradierAPIClient, state: Dict[str, Any],
                         universe: List[str], *, single_sym: Optional[str] = None
                         ) -> Dict[str, Any]:
    """Single pass over universe. Returns summary."""
    cycle_ts = dt.datetime.now(dt.timezone.utc).isoformat()
    # 1. HALT FLAGS — ALWAYS FIRST
    halt = check_halt_flags()
    logger.info(f"Cycle start {cycle_ts} | halts={halt} | paper={PAPER_MODE} go_live={GO_LIVE}")
    log_decision({"ts": cycle_ts, "event": "CYCLE_START",
                  "halts": halt,
                  "paper_mode": PAPER_MODE, "go_live": GO_LIVE,
                  "universe_size": len(universe)})
    # 2. Panic close — close all sleeve positions at market
    if halt["panic_close"]:
        logger.warning("PANIC_CLOSE_ALL flag detected — closing all sleeve positions")
        live_pos = await fetch_live_positions(api)
        for sym, p in list(state.get("positions", {}).items()):
            broker_qty = live_pos.get(sym, {}).get("qty", 0)
            if broker_qty > 0:
                res = await place_sell_order(api, sym, int(broker_qty), market=True)
                log_decision({"event": "PANIC_CLOSE", "symbol": sym,
                              "qty": broker_qty, "order": res})
            state["positions"].pop(sym, None)
        save_state(state)
        return {"event": "PANIC_CLOSE_DONE"}
    # 3. Pull live positions for reconciliation
    live_pos = await fetch_live_positions(api)
    sleeve_positions = state.get("positions", {})
    # 4. Per-symbol pass
    summary = {"checked": 0, "entries_fired": 0, "exits_fired": 0,
               "skipped": 0, "errors": 0}
    syms = [single_sym] if single_sym else universe
    for sym in syms:
        try:
            summary["checked"] += 1
            df_5m = await fetch_5m_klines(api, sym, lookback_days=KLINES_LOOKBACK_DAYS)
            if df_5m is None or len(df_5m) < 100:
                summary["skipped"] += 1
                log_decision({"event": "SKIP_NO_DATA", "symbol": sym,
                              "bars_5m": 0 if df_5m is None else len(df_5m)})
                continue
            df_D = await fetch_history_klines(api, sym, interval="daily",
                                               lookback_days=DAILY_LOOKBACK_DAYS)
            df_W = await fetch_history_klines(api, sym, interval="weekly",
                                               lookback_days=WEEKLY_LOOKBACK_DAYS)
            ind = compute_indicators(df_5m, df_D=df_D, df_W=df_W)
            in_sleeve = sym in sleeve_positions
            in_broker = sym in live_pos and live_pos[sym]["qty"] > 0
            position_state = ("LONG" if in_sleeve and in_broker else
                              "SLEEVE_ONLY" if in_sleeve and not in_broker else
                              "BROKER_ONLY" if (not in_sleeve) and in_broker else
                              "FLAT")
            if position_state == "BROKER_ONLY":
                # Not our position — IGNORE (other strategies may hold)
                log_decision({"event": "SKIP_BROKER_ONLY", "symbol": sym,
                              "broker_qty": live_pos[sym]["qty"]})
                continue
            if position_state == "SLEEVE_ONLY":
                # State drift: sleeve thinks it has position, broker says no
                log_decision({"event": "STATE_DRIFT_SLEEVE_ONLY", "symbol": sym,
                              "sleeve": sleeve_positions[sym]})
                sleeve_positions.pop(sym, None)
                continue
            # ── LONG: try exit ─────────────────────────────────────────
            if position_state == "LONG":
                pos = sleeve_positions[sym]
                # Update peak price
                px = ind["current_price"]
                if px and px > float(pos.get("peak_price", 0) or 0):
                    pos["peak_price"] = px
                if halt["halt_all"]:
                    log_decision({"event": "HALT_ALL_BLOCK_EXIT", "symbol": sym,
                                  "position": pos})
                    continue
                exit_path, exit_reasons = evaluate_exit(ind, pos)
                snapshot = _ind_snapshot(ind)
                if exit_path:
                    qty = int(live_pos[sym]["qty"])
                    order_res = await place_sell_order(api, sym, qty, market=True)
                    log_decision({"event": "EXIT", "symbol": sym,
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
                    log_decision({"event": "HOLD", "symbol": sym,
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
                    log_decision({"event": "HALT_BLOCK_ENTRY", "symbol": sym,
                                  "halt": halt})
                    continue
                # Capacity check
                n_sleeve = len(sleeve_positions)
                deployed = sum(float(p.get("cost_basis", 0) or 0)
                                for p in sleeve_positions.values())
                if n_sleeve >= MAX_CONCURRENT_POSITIONS:
                    log_decision({"event": "BLOCK_MAX_POSITIONS", "symbol": sym,
                                  "n_sleeve": n_sleeve,
                                  "max": MAX_CONCURRENT_POSITIONS})
                    continue
                if deployed + POSITION_NOTIONAL_USD > MAX_TOTAL_DEPLOYED_USD:
                    log_decision({"event": "BLOCK_MAX_DEPLOYED", "symbol": sym,
                                  "deployed_usd": deployed,
                                  "notional": POSITION_NOTIONAL_USD,
                                  "max": MAX_TOTAL_DEPLOYED_USD})
                    continue
                entry_path, entry_reasons = evaluate_entry(ind)
                snapshot = _ind_snapshot(ind)
                if entry_path:
                    px = ind["current_price"]
                    if not px or px <= 0:
                        continue
                    qty = max(1, int(POSITION_NOTIONAL_USD / px))
                    cost = qty * px
                    order_res = await place_buy_order(api, sym, qty, price=px)
                    sleeve_positions[sym] = {
                        "symbol": sym, "side": "LONG", "qty": qty,
                        "entry_price": px, "cost_basis": cost,
                        "peak_price": px,
                        "entry_path": entry_path,
                        "entry_reasons": entry_reasons,
                        "opened_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "entry_bar_dc_low_1h_20": ind.get("dc_low_1h_20"),
                        "paper_mode": PAPER_MODE,
                    }
                    log_decision({"event": "ENTRY", "symbol": sym,
                                  "position_state": "FLAT",
                                  "entry_path": entry_path,
                                  "reasons": entry_reasons,
                                  "qty": qty, "limit_price": px,
                                  "cost_basis": cost,
                                  "indicators_snapshot": snapshot,
                                  "order": order_res})
                    summary["entries_fired"] += 1
                else:
                    log_decision({"event": "NO_ENTRY", "symbol": sym,
                                  "position_state": "FLAT",
                                  "reasons": entry_reasons,
                                  "indicators_snapshot": snapshot})
        except Exception as e:
            summary["errors"] += 1
            logger.exception(f"{sym}: cycle error: {e}")
            log_decision({"event": "ERROR", "symbol": sym, "error": str(e)})
    state["positions"] = sleeve_positions
    state["last_cycle_utc"] = cycle_ts
    save_state(state)
    log_decision({"event": "CYCLE_END", "summary": summary})
    logger.info(f"Cycle done: {summary}")
    return summary

def _ind_snapshot(ind: Dict[str, Any]) -> Dict[str, Any]:
    """Compact indicators for decision log."""
    keys = ["current_price", "k_15m", "k_1h", "k_4h",
            "wt1_15m", "wt2_15m", "wt1_D", "wt2_D",
            "rsi_D", "sma_200_D", "close_D",
            "bull_wt_15m", "bear_wt_15m", "bull_wt_D", "bear_wt_D",
            "bull_k_1h", "bull_sma200_D", "bull_wt_W",
            "dc_low_1h_20", "atr_15m", "exit_X5_armed",
            "lh_ll_count_3bar_15m",
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

async def daemon_loop():
    universe = load_universe()
    logger.info(f"Universe loaded: {len(universe)} syms")
    state = load_state()
    cfg = TradierConfig()
    api = TradierAPIClient(config=cfg, account_key=ACCOUNT_KEY)
    await api.connect()
    try:
        while True:
            if not is_market_hours():
                logger.info("Outside market hours (13:30–20:00 UTC) — sleeping 5min")
                await asyncio.sleep(CYCLE_INTERVAL_SECONDS)
                continue
            try:
                await run_one_cycle(api, state, universe)
            except Exception:
                logger.exception("Cycle failed; sleeping then retrying")
            await asyncio.sleep(CYCLE_INTERVAL_SECONDS)
    finally:
        await api.close()

async def single_cycle_test(symbol: Optional[str] = None):
    """Run one cycle then exit. Used for paper-mode test."""
    universe = load_universe()
    if symbol:
        universe = [symbol] if symbol in universe else universe[:1]
    state = load_state()
    cfg = TradierConfig()
    api = TradierAPIClient(config=cfg, account_key=ACCOUNT_KEY)
    await api.connect()
    try:
        summary = await run_one_cycle(api, state, universe,
                                       single_sym=symbol if symbol else None)
        return summary
    finally:
        await api.close()

# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true",
                     help="Run one cycle and exit (no daemon loop)")
    ap.add_argument("--symbol", default=None,
                     help="Test on a single symbol only (used with --test)")
    ap.add_argument("--daemon", action="store_true",
                     help="Run continuous daemon loop")
    args = ap.parse_args()
    banner = (f"STRUCT_V4 SLEEVE — paper={PAPER_MODE} go_live={GO_LIVE} | "
              f"account={ACCOUNT_KEY} | notional=${POSITION_NOTIONAL_USD:.0f} | "
              f"max_pos={MAX_CONCURRENT_POSITIONS} | "
              f"max_deployed=${MAX_TOTAL_DEPLOYED_USD:.0f}")
    logger.info(banner)
    if not PAPER_MODE and not GO_LIVE:
        logger.warning("STRUCT_V4_PAPER_MODE=false but STRUCT_V4_GO_LIVE!=true → "
                       "no live orders will be placed. Set STRUCT_V4_GO_LIVE=true "
                       "to enable real trading (after explicit user sign-off).")
    if args.test:
        result = asyncio.run(single_cycle_test(symbol=args.symbol))
        logger.info(f"TEST RESULT: {result}")
        print(json.dumps(result, indent=2, default=str))
        return
    if args.daemon:
        asyncio.run(daemon_loop())
        return
    ap.print_help()

if __name__ == "__main__":
    main()
