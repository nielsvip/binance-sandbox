"""
kline_loader.py — Universal kline loading with MANDATORY TF derivation from 15m.

RULE: 15m klines = ALL timeframes since 2020. Period.
If a TF file is missing or too short, resample from 15m. NEVER return None
when 15m data exists.

Usage:
    from kline_loader import load_klines_for_backtest
    df = load_klines_for_backtest('DOTUSDT', '1h')  # Loads from file or resamples from 15m
    df = load_klines_for_backtest('DOTUSDT', '4h')  # Same — guaranteed data if 15m exists
"""
import json
import os
from pathlib import Path
from typing import Optional
import numpy as np
import pandas as pd

# Auto-detect klines directory
_SCRIPT_DIR = Path(__file__).resolve().parent
if Path("/home/niels/binance-sandbox/klines_cache").exists():
    KLINES_DIRS = [Path("/home/niels/binance-sandbox/klines_cache")]
elif (_SCRIPT_DIR / "klines_cache_backtest").exists():
    KLINES_DIRS = [_SCRIPT_DIR / "klines_cache_backtest", _SCRIPT_DIR / "klines_cache", _SCRIPT_DIR / "klines_cache_gateway"]
else:
    KLINES_DIRS = [_SCRIPT_DIR / "klines_cache", _SCRIPT_DIR / "klines_cache_gateway"]

RESAMPLE_RULES = {"1m": None, "3m": None, "5m": None, "15m": None, "1h": "1h", "4h": "4h", "D": "1D", "W": "1W", "M": "1ME"}
MIN_BARS = 50


def _load_raw(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    """Load klines from JSON file. Returns DataFrame with timestamp_dt or None."""
    for kdir in KLINES_DIRS:
        p = kdir / f"{symbol}_{tf}.json"
        if not p.exists():
            continue
        try:
            raw = json.loads(p.read_text())
            if not raw:
                continue
            rows = []
            for k in raw:
                if isinstance(k, dict):
                    rows.append({"timestamp": k["timestamp"], "open": float(k["open"]), "high": float(k["high"]), "low": float(k["low"]), "close": float(k["close"]), "volume": float(k.get("volume", 0))})
                elif isinstance(k, list):
                    rows.append({"timestamp": k[0], "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]) if len(k) > 5 else 0})
            if not rows:
                continue
            df = pd.DataFrame(rows)
            if df["timestamp"].dtype == object:
                df["timestamp_dt"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
            else:
                df["timestamp_dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.sort_values("timestamp_dt").reset_index(drop=True)
            return df
        except Exception:
            continue
    return None


def _resample_from_15m(symbol: str, target_tf: str) -> Optional[pd.DataFrame]:
    """Derive any TF from 15m klines by resampling. This is the fallback that NEVER fails if 15m exists."""
    df_15m = _load_raw(symbol, "15m")
    if df_15m is None or len(df_15m) < MIN_BARS:
        return None
    rule = RESAMPLE_RULES.get(target_tf)
    if rule is None:
        return df_15m
    try:
        resampled = df_15m.set_index("timestamp_dt").resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
        if len(resampled) >= MIN_BARS:
            return resampled
    except Exception:
        pass
    return None


def load_klines_for_backtest(symbol: str, tf: str, min_bars: int = MIN_BARS) -> Optional[pd.DataFrame]:
    """Load klines with MANDATORY 15m fallback. NEVER returns None when 15m data exists.

    Priority:
    1. Load native TF file (fastest, exact data)
    2. If native file missing or too short: resample from 15m
    3. Only return None if 15m also has no data
    """
    df = _load_raw(symbol, tf)
    if tf == "15m":
        return df
    df_15m = _load_raw(symbol, "15m")
    if df_15m is None or len(df_15m) < min_bars:
        return df
    if df is not None and len(df) >= min_bars:
        native_span = (df["timestamp_dt"].iloc[-1] - df["timestamp_dt"].iloc[0]).total_seconds()
        source_span = (df_15m["timestamp_dt"].iloc[-1] - df_15m["timestamp_dt"].iloc[0]).total_seconds()
        if native_span >= source_span * 0.8:
            return df
    resampled = _resample_from_15m(symbol, tf)
    if resampled is not None and len(resampled) >= min_bars:
        return resampled
    return df if df is not None and len(df) > 0 else None


def get_all_tfs(symbol: str, min_bars: int = MIN_BARS) -> dict:
    """Load ALL timeframes for a symbol, deriving from 15m as needed.
    Returns dict: {tf: DataFrame} for 15m, 1h, 4h, D.
    """
    result = {}
    for tf in ["15m", "1h", "4h", "D"]:
        df = load_klines_for_backtest(symbol, tf, min_bars=min_bars)
        if df is not None and len(df) >= min_bars:
            result[tf] = df
    return result


def get_deep_symbols(min_15m_bars: int = 100000) -> list:
    """Find symbols with deep 15m data (4+ years)."""
    symbols = []
    for kdir in KLINES_DIRS:
        for f in sorted(kdir.glob("*_15m.json")):
            sym = f.name.replace("_15m.json", "")
            if sym in [s for s, _ in symbols]:
                continue
            try:
                data = json.loads(f.read_text())
                bars = len(data) if isinstance(data, list) else 0
                if bars >= min_15m_bars:
                    symbols.append((sym, bars))
            except Exception:
                continue
    symbols.sort(key=lambda x: -x[1])
    return [s for s, _ in symbols]
