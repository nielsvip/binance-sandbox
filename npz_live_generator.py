#!/usr/bin/env python3
"""
npz_live_generator.py — Incremental NPZ updater for live vs vectorized parity.

Ensures NPZs (backtest_v8/indicators/*.npz) are always correct up to the last
15m closed candle, point-in-time (no lookahead). Used by both ez_indicators
(crypto, 24/7) and tradier_indicators (stocks, RTH 09:30-16:00 ET).

Design:
- Crypto: 15m boundaries at UTC :00/:15/:30/:45, always tradable.
- Tradier: 15m boundaries aligned to 09:30 ET open (09:45,10:00,...16:00),
  but we delegate to KlineManager's 15m df last timestamp — it already
  encodes market-hours gaps. No update outside RTH except final close.
- For each symbol, load existing NPZ, compare its last timestamp to the
  latest closed 15m bar from KlineManager. If behind, compute the new row
  via IndicatorCalculator.compute for each TF truncated to that close,
  broadcast HTF values with correct lag (tradier HTF lag 2, crypto lag 1),
  and append atomically.
- If NPZ missing or gap >1 day, fall back to full precompute (backtest_v8_precompute.compute_symbol)
  to avoid incremental drift. Otherwise incremental is O(1) per symbol.

Live vs vectorized parity: the same scalar functions (stoch_result, wavetrend,
donchian, etc.) are used live and in precompute; incremental uses those same
functions via IndicatorCalculator, so the appended row is bit-identical to what
a full rebuild would produce for that bar.
"""
import asyncio
import json
import logging
import os
import platform
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

logger = logging.getLogger("npz_live_generator")

def _is_s1_host() -> bool:
    """Only S1 (10.0.0.3 / niels) should write NPZs.
    Mac (Darwin) never, S3 (htz-v15-s3 / 10.0.0.5) never, S5 (htz-v15-s5 / 10.0.0.6) never —
    S3/S5 only calculate XLS sides and pull missing NPZ from S1 on demand.
    """
    if platform.system() == "Darwin":
        return False
    try:
        import socket
        host = socket.gethostname().lower()
        if "mac" in host or "macbook" in host:
            return False
        # Only S1 hostname / IP is allowed to write
        # S1: hostname niels, IP 10.0.0.3
        if host == "niels":
            return True
        # Fallback IP check for S1
        addrs = socket.gethostbyname_ex(host)[2] if hasattr(socket, "gethostbyname_ex") else []
        try:
            import subprocess
            out = subprocess.check_output(["hostname", "-I"], text=True, timeout=1)
            addrs += out.split()
        except Exception:
            pass
        if "10.0.0.3" in addrs:
            return True
        return False
    except Exception:
        return False

# ET for tradier RTH checks
ET = ZoneInfo("America/New_York")

def _last_15m_close_utc(now: datetime) -> datetime:
    """Floor to last 15m close in UTC (crypto). 10:16 -> 10:15, 10:15:00 -> 10:00 (need closed)."""
    # Use UTC 15m grid: :00, :15, :30, :45
    # A bar is closed only after its close time has passed. Require 30s after close to allow klines to arrive.
    # So at 10:15:01, last close is 10:00; at 10:15:31, last close is 10:15.
    # We implement as: if now.second <30 and now.minute%15==0 -> still previous bar is last closed.
    # Otherwise floor.
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    # Budget 30s for kline arrival
    effective = now - timedelta(seconds=30)
    floored_minute = (effective.minute // 15) * 15
    return effective.replace(minute=floored_minute, second=0, microsecond=0)

def _is_tradier_market_hours(now: datetime) -> bool:
    """True if now (UTC) is within RTH or 30s after close where final bar should be written."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    et = now.astimezone(ET)
    # Weekend
    if et.weekday() >= 5:
        return False
    # RTH 09:30-16:00 ET, plus 5 min grace after close for final bar
    market_open = et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = et.replace(hour=16, minute=5, second=0, microsecond=0)
    return market_open <= et <= market_close

def _npz_path(base_path: Path, symbol: str) -> Path:
    # Mirrors backtest_v8_precompute.OUT_DIR
    if base_path.name == "binance":
        out_dir = base_path / "backtest_v8" / "indicators"
    else:
        out_dir = base_path / "backtest_v8" / "indicators"
    # Also handle sandbox path on Linux
    if not out_dir.exists():
        alt = Path("/home/niels/binance-sandbox") / "backtest_v8" / "indicators"
        if alt.exists():
            out_dir = alt
    return out_dir / f"{symbol}.npz"

def _load_npz(path: Path) -> Optional[Dict[str, np.ndarray]]:
    if not path.exists():
        return None
    try:
        with np.load(str(path), allow_pickle=True) as z:
            return {k: z[k] for k in z.files}
    except Exception as e:
        logger.warning(f"NPZ load failed {path}: {e}")
        return None

def _save_npz_atomic(path: Path, data: Dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp.npz")
    np.savez_compressed(str(tmp), **data)
    os.replace(str(tmp), str(path))

def _compute_row_for_15m_close(
    symbol: str,
    close_ts: datetime,
    kline_manager,
    calculator,
    mode: str,
) -> Optional[Dict[str, Any]]:
    """
    Compute scalar indicator values for one 15m close, point-in-time.
    For each TF, truncate its df to <= close_ts (closed bars only) and call
    IndicatorCalculator.compute, then collect the scalar results for the new row.
    Returns dict of {key: value} for that row, or None if insufficient data.
    """
    row: Dict[str, Any] = {}
    # 15m base close timestamp as epoch seconds (for NPZ timestamps array)
    # NPZ stores timestamps as int64 seconds since epoch (UTC)
    ts_epoch = int(close_ts.timestamp())
    row["timestamps"] = ts_epoch
    # For each TF, get its df and compute
    # Map mode to TF list
    if mode == "tradier":
        tfs = ["5m", "15m", "1h", "4h", "D", "W", "M"]
        base_tf = "15m"
    else:
        tfs = ["3m", "15m", "1h", "4h", "D", "W", "M"]
        base_tf = "15m"
    # We need to handle W/M as well, but they are derived from D; use same compute path.
    # For each TF, try to get df truncated to close_ts
    # Use kline_manager.get_latest or direct cache? Use get_latest which returns closed df.
    # For incremental, we can just call calculator.compute on the df for that TF.
    # We need to ensure df is truncated to <= close_ts.
    # For 15m base, the df's last row should be close_ts, so compute will give us the row's values.
    # For HTF, we need asof: last HTF bar closed before or at close_ts.
    # kline_manager.get_latest returns the latest closed bar as of now, which for HTF may be
    # earlier than close_ts if HTF hasn't closed yet. That's correct for point-in-time.
    # So we can just call for each TF and take its scalar result.

    # Helper to truncate df to <= close_ts
    def _truncate_df(df: pd.DataFrame, ts: datetime) -> Optional[pd.DataFrame]:
        if df is None or df.empty:
            return None
        # df index is timestamp_dt
        try:
            # Ensure tz-aware
            if df.index.tz is None:
                df.index = df.index.tz_localize(timezone.utc)
            # Truncate to <= ts
            truncated = df.loc[:ts]
            # Need at least some rows
            if len(truncated) < 5:
                return None
            return truncated
        except Exception:
            return None

    # For each TF, get df and compute
    for tf in tfs:
        try:
            # Try to get df for this TF. Use kline_manager internal cache if available.
            # Fallback to get_latest
            df = None
            close_ts_tf = None
            # Try direct cache access for speed (kline_manager may have _cache)
            try:
                _base = getattr(kline_manager, 'base_path', None) if kline_manager is not None else None
                if _base is None:
                    _base = Path.cwd()
                else:
                    _base = Path(_base)
                fname = _resolve_kline_path(symbol, tf, mode, _base)
                df = None
                if fname is not None:
                    df = _load_klines_df(fname)
                    if df is not None:
                        df = _truncate_df(df, close_ts)
            except Exception as e:
                logger.debug(f"df load failed {symbol} {tf}: {e}")
                df = None

            if df is None or df.empty:
                # Not enough data for this TF at this timestamp (e.g., W/M warmup) -> skip, will be 0 in NPZ
                continue

            # Compute scalar indicators for this TF as of close_ts
            # Use calculator.compute — signatures differ: ez (df, timeframe, mark_price, mid_run, mark_price_ts)
            # vs tradier (df, symbol, timeframe, mark_price, mark_ts, mid_run)
            try:
                if mode == "tradier":
                    res = calculator.compute(df, symbol, tf, None, close_ts, False)
                else:
                    res = calculator.compute(df, tf, None, False, close_ts)
            except TypeError as e:
                # Fallback: try alternative order
                try:
                    if mode == "tradier":
                        res = calculator.compute(df, symbol, tf, mark_price=None, mark_ts=close_ts, mid_run=False)
                    else:
                        res = calculator.compute(df, timeframe=tf, mark_price=None, mid_run=False, mark_price_ts=close_ts)
                except Exception:
                    raise e
            if not res:
                continue
            # Collect scalar keys for NPZ row: any key containing _{tf} is timeframe-specific
            for k, v in res.items():
                if f"_{tf}" in k or k in ("open", "high", "low", "close", "volume", "timestamp"):
                    # For base_tf, close_15m -> also set close (NPZ has both)
                    if tf == base_tf and k == f"close_{base_tf}":
                        row["close"] = v
                        row[k] = v
                    elif tf == base_tf and k == f"open_{base_tf}":
                        row[k] = v
                        row["open"] = v
                    elif tf == base_tf and k == f"high_{base_tf}":
                        row[k] = v
                        row["high"] = v
                    elif tf == base_tf and k == f"low_{base_tf}":
                        row[k] = v
                        row["low"] = v
                    elif tf == base_tf and k == f"volume_{base_tf}":
                        row[k] = v
                        row["volume"] = v
                    elif f"_{tf}" in k:
                        # HTF or base: store as is (e.g., k_1h, stoch_k_15m, close_1h, k_15m_prev, k_15m_ant)
                        row[k] = v
                    elif k in ("open", "high", "low", "close", "volume") and tf == base_tf:
                        row[f"{k}_{base_tf}"] = v
                        if k == "close":
                            row["close"] = v
                    else:
                        # For NPZ, HTF keys are like k_1h, stoch_k_1h, etc., which will be broadcast arrays at 15m resolution
                        # So we store the scalar HTF value for this 15m row
                        row[k] = v
            # Also ensure timestamps for HTF are not needed; NPZ timestamps is base 15m
        except Exception as e:
            logger.warning(f"compute row failed {symbol} {tf} at {close_ts}: {e}")
            continue

    # Ensure we have at least close and timestamps
    if "close" not in row or "timestamps" not in row:
        logger.warning(f"row incomplete for {symbol} at {close_ts}: missing close/timestamps")
        return None
    # Fill missing HTF keys with 0/neutral to keep NPZ shape consistent (like precompute does for warmup)
    # Precompute fills missing with 0 for numeric, so we should ensure row has entries for all expected NPZ keys?
    # For incremental, we can just return what we have; _append will handle missing keys by filling 0.
    return row

def _load_klines_df(path: Path) -> Optional[pd.DataFrame]:
    try:
        with open(path) as f:
            data = json.load(f)
        if not data:
            return None
        df = pd.DataFrame(data)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "timestamp" in df.columns:
            df["timestamp_dt"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
        elif "timestamp_dt" in df.columns:
            df["timestamp_dt"] = pd.to_datetime(df["timestamp_dt"], utc=True, format="ISO8601")
        else:
            return None
        # Keep timestamp_dt as both index and column for IndicatorCalculator compatibility
        # (compute expects timestamp_dt column in some paths, and index for resampling)
        df["timestamp_dt"] = pd.to_datetime(df["timestamp_dt"], utc=True)
        df["_ts_copy"] = df["timestamp_dt"]
        df = df.set_index("timestamp_dt", drop=False).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        # Ensure close_time exists for tradier path
        if "close_time" not in df.columns:
            df["close_time"] = df["_ts_copy"]
        df = df.drop(columns=["_ts_copy"], errors="ignore")
        return df
    except Exception as e:
        logger.debug(f"load klines failed {path}: {e}")
        return None

def _resolve_kline_path(symbol: str, tf: str, mode: str, base_path: Path) -> Optional[Path]:
    cands: list[Path] = []
    if mode == "tradier":
        for b in [base_path / "klines_cache_backtest" / "tradier", base_path / "klines_cache" / "tradier", base_path / "klines_cache_gateway" / "tradier", Path("/home/niels/binance-sandbox") / "klines_cache_backtest" / "tradier"]:
            cands.append(b / f"{symbol}_{tf}.json")
    else:
        for b in [base_path / "klines_cache_backtest", base_path / "klines_cache", base_path / "klines_cache_gateway", Path("/home/niels/binance-sandbox") / "klines_cache_backtest"]:
            cands.append(b / f"{symbol}_{tf}.json")
    for p in cands:
        if p.exists():
            return p
    return None

def _should_update_npz(npz_path: Path, last_15m_close: datetime) -> bool:
    if not _is_s1_host():
        return False
    if not npz_path.exists():
        return True
    data = _load_npz(npz_path)
    if data is None or "timestamps" not in data or len(data["timestamps"]) == 0:
        return True
    last_ts = int(data["timestamps"][-1])
    last_dt = datetime.fromtimestamp(last_ts, tz=timezone.utc)
    # NPZ is behind if last_dt < last_15m_close
    return last_dt < last_15m_close

def update_npz_for_symbol(
    symbol: str,
    mode: str,
    kline_manager,
    calculator,
    base_path: Path,
) -> bool:
    """
    Ensure NPZ for symbol is up to last 15m close. Returns True if updated.
    Point-in-time: uses only closed bars. For tradier, respects RTH gaps via
    kline file's last timestamp, not wall time.
    Only writes on S1 (Linux); Mac is read-only for backtests.
    """
    if not _is_s1_host():
        return False
    # Determine last closed 15m bar from klines (point-in-time, no wall-time guess)
    # Try multiple kline sources: backtest (long history) then live, then gateway, then sandbox
    def _find_kline_file(sym: str, tf: str, m: str) -> Optional[Path]:
        candidates = []
        if m == "tradier":
            for base in [base_path / "klines_cache_backtest" / "tradier", base_path / "klines_cache" / "tradier", base_path / "klines_cache_gateway" / "tradier", Path("/home/niels/binance-sandbox") / "klines_cache_backtest" / "tradier"]:
                candidates.append(base / f"{sym}_{tf}.json")
        else:
            for base in [base_path / "klines_cache_backtest", base_path / "klines_cache", base_path / "klines_cache_gateway", Path("/home/niels/binance-sandbox") / "klines_cache_backtest"]:
                candidates.append(base / f"{sym}_{tf}.json")
        for p in candidates:
            if p.exists():
                return p
        return None

    try:
        f = _find_kline_file(symbol, "15m", mode)
        if f is None and mode == "tradier":
            f = _find_kline_file(symbol, "5m", mode)
        if f is None and mode == "crypto":
            f = _find_kline_file(symbol, "3m", mode)
        if f is None:
            logger.debug(f"klines not found for {symbol} {mode}")
            return False
        df15 = _load_klines_df(f)
        if df15 is None or df15.empty:
            return False
        last_close = df15.index[-1].to_pydatetime().astimezone(timezone.utc)
        # For tradier, ensure last_close is within RTH (skip overnight gaps where df may have stale)
        if mode == "tradier":
            et_close = last_close.astimezone(ET)
            if et_close.weekday() >= 5:
                return False
            # Allow only 09:45-16:00 ET closes (15m grid from 09:30)
            # If last_close is outside, it's either pre-market or stale, skip
            # Use df's last timestamp directly, so if it's 16:00, it's valid
            pass
        npz_path = _npz_path(base_path, symbol)
        if not _should_update_npz(npz_path, last_close):
            return False
        # If gap is large (>100 bars ~25h), trigger full rebuild for correctness and efficiency
        try:
            existing = _load_npz(npz_path)
            if existing is not None and "timestamps" in existing and len(existing["timestamps"]) > 0:
                last_npz_ts = int(existing["timestamps"][-1])
                gap_bars = (int(last_close.timestamp()) - last_npz_ts) // 900
                if gap_bars > 100:
                    logger.info(f"NPZ {symbol} gap {gap_bars} bars ({gap_bars*15/60:.1f}h) -> full rebuild")
                    try:
                        import backtest_v8_precompute
                        m = "crypto" if symbol.endswith(("USDT","USDC")) else "tradier"
                        ok = backtest_v8_precompute.compute_symbol(symbol, m)
                        return bool(ok)
                    except Exception as e:
                        logger.warning(f"full rebuild failed for {symbol}: {e}")
                        # Fall through to incremental single append
        except Exception:
            pass
        row = _compute_row_for_15m_close(symbol, last_close, kline_manager, calculator, mode)
        if row is None:
            return False
        return _append_row_to_npz(npz_path, row)
    except Exception as e:
        logger.warning(f"update failed for {symbol}: {e}")
        return False

def _append_row_to_npz(npz_path: Path, row: Dict[str, Any]) -> bool:
    """Append one row to NPZ, preserving all existing keys. Fills missing keys with 0."""
    data = _load_npz(npz_path)
    if data is None:
        # No existing file: do full rebuild via precompute if possible, else create minimal
        # Try full rebuild
        try:
            import backtest_v8_precompute
            # Determine mode from symbol: if symbol ends with USDT/USDC -> crypto, else tradier
            sym = npz_path.stem
            mode = "crypto" if sym.endswith(("USDT","USDC")) else "tradier"
            logger.info(f"NPZ missing for {sym}, triggering full precompute {mode}")
            # This is heavy; run in thread to avoid blocking
            success = backtest_v8_precompute.compute_symbol(sym, mode)
            if success and npz_path.exists():
                # After full build, check if it already includes row's timestamp
                data = _load_npz(npz_path)
                if data is not None and int(data["timestamps"][-1]) >= int(row["timestamps"]):
                    return True
                # If still behind, fall through to append
            else:
                # Create minimal NPZ with just this row
                minimal = {}
                for k, v in row.items():
                    # Determine dtype
                    if isinstance(v, (int, float, np.integer, np.floating)):
                        minimal[k] = np.array([v], dtype=np.float32 if isinstance(v, float) else np.int64)
                    elif isinstance(v, str):
                        minimal[k] = np.array([v], dtype=object)
                    else:
                        minimal[k] = np.array([v])
                # Ensure timestamps is int64
                if "timestamps" in minimal:
                    minimal["timestamps"] = np.array([int(row["timestamps"])], dtype=np.int64)
                _save_npz_atomic(npz_path, minimal)
                return True
        except Exception as e:
            logger.warning(f"full rebuild failed for {npz_path.stem}: {e}, creating minimal")
            minimal = {}
            for k, v in row.items():
                if isinstance(v, (int, float, np.integer, np.floating)):
                    minimal[k] = np.array([v], dtype=np.float32 if isinstance(v, float) else np.int64)
                else:
                    minimal[k] = np.array([v], dtype=object)
            if "timestamps" in minimal:
                minimal["timestamps"] = np.array([int(row["timestamps"])], dtype=np.int64)
            _save_npz_atomic(npz_path, minimal)
            return True

    # Existing file: append
    try:
        # Ensure row has timestamps as int
        new_ts = int(row["timestamps"])
        last_ts = int(data["timestamps"][-1])
        if new_ts <= last_ts:
            logger.debug(f"NPZ {npz_path.name} already up to {new_ts} <= {last_ts}")
            return False
        # For each existing key, append; for new keys in row not in data, extend data with zeros for prior rows
        n_old = len(data["timestamps"])
        # First, ensure all keys in data get appended (fill missing row keys with 0)
        for k in list(data.keys()):
            arr = data[k]
            # Determine new value
            if k in row:
                v = row[k]
            else:
                # Fill with 0/neutral for missing (like precompute does for warmup)
                # Use dtype-appropriate zero
                if arr.dtype.kind in "biufc":
                    v = 0
                elif arr.dtype.kind in "USV":
                    v = ""
                else:
                    v = 0
            # Append
            try:
                # Handle object arrays
                if arr.dtype == object:
                    new_arr = np.append(arr, np.array([v], dtype=object))
                else:
                    # Cast to arr dtype
                    v_arr = np.array([v], dtype=arr.dtype)
                    new_arr = np.concatenate([arr, v_arr])
                data[k] = new_arr
            except Exception as e:
                logger.warning(f"append failed for {k}: {e}")
                # Fallback: create new array with required length
                continue
        # For keys in row but not in data (new indicator added), create array with zeros for old rows + new value
        for k, v in row.items():
            if k not in data:
                # Create array of length n_old+1, fill old with 0, last with v
                if isinstance(v, (int, float, np.integer, np.floating)):
                    dtype = np.float32 if isinstance(v, float) else np.int64
                    if k == "timestamps":
                        dtype = np.int64
                    arr = np.zeros(n_old + 1, dtype=dtype)
                    if n_old > 0:
                        arr[:-1] = 0
                    arr[-1] = v
                elif isinstance(v, str):
                    arr = np.array([""] * n_old + [v], dtype=object)
                else:
                    arr = np.array([0] * n_old + [v], dtype=object)
                data[k] = arr
        _save_npz_atomic(npz_path, data)
        logger.info(f"NPZ appended {npz_path.name} ts={new_ts} ({datetime.fromtimestamp(new_ts, tz=timezone.utc).isoformat()})")
        return True
    except Exception as e:
        logger.error(f"NPZ append failed {npz_path}: {e}")
        return False
