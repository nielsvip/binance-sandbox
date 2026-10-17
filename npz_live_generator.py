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

def _shrink_check(path: Path, data: Dict[str, np.ndarray]):
    """(allowed, reason) — a store is only ever extended, never replaced by a smaller one.

    USER 2026-10-16: "s2 has backups of the npzs so no complete reruns are ever needed just topups; make sure
    they get appended to never overwritten with smaller versions". MEASURED: CDW/ADP/AEM/AXTI were full stock
    stores until 2026-10-13 and were then replaced by 1-row live stubs from this module (the pre-BIBLE-§109
    minimal branch below), which froze them out of every top-up for 3 days (`cannot extend 618 row keys`) and
    fed 1-bar NPZs to 30D boards. npz_guard.should_allow_overwrite would have refused it — this writer never
    called it. NPZ_ALLOW_SHRINK=1 restores the old unguarded behaviour."""
    if os.environ.get("NPZ_ALLOW_SHRINK") == "1" or not path.exists():
        return True, "no existing — allow"
    try:
        with np.load(str(path), allow_pickle=True) as z:
            if "timestamps" not in z.files:
                return True, "existing has no timestamps — allow"
            ts_old = np.asarray(z["timestamps"]).astype(np.int64).reshape(-1)
    except Exception as e:
        return True, f"existing unreadable ({type(e).__name__}) — allow"
    n_old = len(ts_old)
    ts_new = np.asarray(data.get("timestamps", np.array([], dtype=np.int64))).astype(np.int64).reshape(-1)
    n_new = len(ts_new)
    if n_new < n_old:
        return False, f"REFUSED bar-loss write: existing {n_old} rows vs candidate {n_new}"
    if n_old and n_new and int(ts_new[-1]) < int(ts_old[-1]):
        return False, f"REFUSED time-regression write: existing last {int(ts_old[-1])} vs candidate {int(ts_new[-1])}"
    return True, "allow"


def _save_npz_atomic(path: Path, data: Dict[str, np.ndarray]) -> None:
    allowed, reason = _shrink_check(path, data)
    if not allowed:
        logger.error(f"NPZ WRITE {reason} for {path.name} — file left untouched (never-shrink invariant)")
        raise ValueError(f"{path.name}: {reason}")
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
    # 2026-10-17: this row NEVER carries a W/M key, on ANY host. Not a trickle-only gate -- the W/M source this
    # function reads is stale everywhere. MEASURED on both hosts the same day: AAPL_W.json ends 2026-03-23 (7 months
    # back, on an 05:00-ET grid, not the builder's Monday-00:00Z left-labelled W-MON bucket) and BNBUSDC_W.json ends
    # 2026-08-24, in klines_cache AND klines_cache_backtest, on the Mac AND on S1. backtest_v8_precompute abandoned
    # those files for exactly this reason (_crypto_wm_frame: "the old 'append-only contract' kept the stale
    # authentic *_W.json/*_M.json files (last written 2026-08-26) over the 15m resample ... so W/M arrays froze")
    # and now builds the frame as authentic-older-history + fresh 15m buckets, which needs history this function
    # does not have: the 15m span here is 95d for stocks and 18d for crypto = 14 and 3 weekly bars, against the 200
    # that ema_200_W needs.
    # It is not a loss. _append_row_to_npz carries an absent per-bar key FORWARD, which is the exact value until the
    # bucket closes, and tools/npz_bar_refresh.py (deep klines + the real builder) is the only writer that moves a
    # W/M key. Computing them here only ever wrote a March weekly high onto an October row -- 3 staged stock bars
    # (ABBV/ACN/ADP) had already done it, timestamp_W=2026-03-23, before tools/npz_bar_ingest.py refused them.
    tfs = [t for t in tfs if t not in ("W", "M")]
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

def bar_outbox(base_path: Path) -> Optional[Path]:
    """Staging dir for computed-but-not-applied NPZ bars, or None when export is off.

    USER 2026-10-17: "we want to use macbook for the latest npz bar calculations for both tradier and ez ->
    rsync to s1 -> s1 adds it immediately to the existing npz's. No compute wasted as macbook already did the
    computing within the indicator scripts; s1 never has to rebuild npz's it just uses the updates just like it
    appends the klines without fetching them."
    NPZ_BAR_EXPORT=0 disables the export entirely."""
    if os.environ.get("NPZ_BAR_EXPORT", "1") != "1":
        return None
    d = os.environ.get("NPZ_BAR_OUTBOX")
    return Path(d) if d else (Path(base_path) / "data" / "npz_bar_outbox")


def export_bar_for_symbol(
    symbol: str,
    mode: str,
    kline_manager,
    calculator,
    base_path: Path,
) -> bool:
    """Compute the latest closed 15m NPZ row with the LIVE calculator and stage it as JSON. Writes no NPZ.

    This is the Mac's half of the trickle: the indicator scripts already hold the calculator and the klines, so
    the row costs nothing extra here, and S1 then appends it instead of recomputing the bar with the builder.
    One file per symbol+bar, written atomically, idempotent (an existing file for the same bar is left alone)."""
    out_dir = bar_outbox(base_path)
    if out_dir is None:
        return False
    try:
        f = _resolve_kline_path(symbol, "15m", mode, Path(base_path))
        if f is None:
            return False
        df15 = _load_klines_df(f)
        if df15 is None or df15.empty:
            return False
        last_close = df15.index[-1].to_pydatetime().astimezone(timezone.utc)
        ts_epoch = int(last_close.timestamp())
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{symbol}_{ts_epoch}.json"
        if out.exists():
            return False
        row = _compute_row_for_15m_close(symbol, last_close, kline_manager, calculator, mode)
        if not row:
            return False
        payload = {"symbol": symbol, "mode": mode, "ts": ts_epoch,
                   "computed_at": datetime.now(timezone.utc).isoformat(),
                   "host": platform.node(), "keys": len(row), "row": row}
        tmp = out.with_suffix(".json.tmp")
        with open(tmp, "w") as fh:
            json.dump(payload, fh, default=str)
        os.replace(tmp, out)
        logger.info(f"[NPZ-EXPORT] {symbol} bar {ts_epoch} staged ({len(row)} keys) -> {out.name}")
        return True
    except Exception as e:
        logger.warning(f"[NPZ-EXPORT] {symbol} failed: {type(e).__name__}: {e}")
        return False


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
        # USER 2026-10-17: the Mac must not WRITE an NPZ, but it is the host that already computed the bar inside
        # ez_indicators / tradier_indicators — so it stages the row for S1 instead of returning empty-handed.
        # Before this, both indicator loops called this function for every symbol every 15 min and it returned
        # False on the first line, so the Mac "checked 199 symbols" and produced nothing, and the only way to
        # keep an NPZ current was to run the indicator scripts ON S1 — the compute this is meant to avoid.
        return export_bar_for_symbol(symbol, mode, kline_manager, calculator, base_path)
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
    """Append one row to NPZ, preserving all existing keys.

    NOT "fills missing keys with 0" (what this docstring said until 2026-10-17, left behind by the 2026-09-29
    alignment fix below): a per-bar key the row does not carry CARRIES THE LAST VALUE FORWARD. That is what makes
    the Mac->S1 trickle correct for every HTF key -- a weekly or daily value is unchanged until its bucket closes,
    so carrying it is the true value, not a filler -- and zeroing them instead would wipe 137 W + 137 M keys on
    every 15m bar."""
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
            elif mode == "tradier":
                # 2026-10-14 (BIBLE §109): never create a 1-row stock stub (ALMU/CDW/AXTI/AEM/ADP were 1-bar NPZs fed to 30D boards)
                logger.error(f"NPZ missing for {sym} and full precompute failed — NOT creating a 1-row stock stub")
                try:
                    from tools.stock_npz_gap_guard import alert as _gap_alert
                    _gap_alert(sym, {"ok": False, "sessions": 0, "max_gap_days": None, "gaps": [], "last": None, "reason": "NPZ missing and full precompute failed (1-row stub refused)"}, "npz_live_generator", True)
                except Exception:
                    pass
                return False
            elif os.environ.get("NPZ_ALLOW_CRYPTO_STUB") != "1":
                # 2026-10-16: the §109 stock rule applies to crypto too — a 1-row stub is read by nobody (live crypto
                # reads the indicator JSON) and it LOCKS the symbol out of every later top-up, because a 1-row store
                # cannot be tailed. Refuse and queue a real repair instead. NPZ_ALLOW_CRYPTO_STUB=1 restores it.
                logger.error(f"NPZ missing for {sym} and full precompute failed — NOT creating a 1-row crypto stub (queue tools/npz_repair.py)")
                return False
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
            if not npz_path.stem.endswith(("USDT", "USDC")):
                logger.error(f"full rebuild failed for {npz_path.stem}: {e} — NOT creating a 1-row stock stub (BIBLE §109)")
                return False
            if os.environ.get("NPZ_ALLOW_CRYPTO_STUB") != "1":
                logger.error(f"full rebuild failed for {npz_path.stem}: {e} — NOT creating a 1-row crypto stub (2026-10-16: a stub cannot be tailed)")
                return False
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
        # 2026-09-29 ALIGNMENT FIX: the old per-key loop `continue`d on any append error (object/str arrays such as
        # wt_cross_15m, timestamp_15m) -> those keys ended 1 row short -> evaluate_v12._compact_to_15m "NPZ lacks
        # aligned timestamp_15m" (95 stock NPZs on s1 19:17-19:34Z), and it back-filled 576 brand-new keys with zeros
        # for the whole history (fake data). Now: all-or-nothing append of per-bar arrays only; keys missing from the
        # row carry the last value forward (timestamp_{base} = new bar ts); new keys are NOT invented; lengths verified.
        n_old = len(data["timestamps"])
        out = {}
        for k in list(data.keys()):
            arr = data[k]
            if getattr(arr, "ndim", 0) != 1 or len(arr) != n_old:
                out[k] = arr  # not a per-bar series (scalars/metadata) — keep as is
                continue
            if k in row:
                v = row[k]
            elif k.startswith("timestamp_") and k[len("timestamp_"):] in ("15m", "5m", "3m", "1m"):
                v = new_ts  # base-TF availability timestamp = the new bar itself
            else:
                v = arr[-1] if n_old else 0  # carry forward (HTF values/availability, object labels)
            try:
                out[k] = np.append(arr, np.array([v], dtype=object)) if arr.dtype == object else np.concatenate([arr, np.array([v], dtype=arr.dtype)])
            except Exception as e:
                logger.error(f"NPZ append ABORTED {npz_path.name}: key {k} ({arr.dtype}) value {v!r}: {e} — file left untouched")
                return False
        _new_keys = [k for k in row if k not in data]
        if _new_keys:
            logger.warning(f"NPZ {npz_path.name}: {len(_new_keys)} row keys not in NPZ schema skipped (no zero-backfilled history): {_new_keys[:8]}")
        _bad = [k for k, a in out.items() if getattr(a, "ndim", 0) == 1 and len(data[k]) == n_old and len(a) != n_old + 1]
        if _bad:
            logger.error(f"NPZ append ABORTED {npz_path.name}: misaligned after append {_bad[:8]} — file left untouched")
            return False
        data = out
        _save_npz_atomic(npz_path, data)
        logger.info(f"NPZ appended {npz_path.name} ts={new_ts} ({datetime.fromtimestamp(new_ts, tz=timezone.utc).isoformat()})")
        return True
    except Exception as e:
        logger.error(f"NPZ append failed {npz_path}: {e}")
        return False
