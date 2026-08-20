import asyncio
import hashlib
import json
import logging
import math
import os
import shutil
import sys
import tempfile
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dt_time
from datetime import timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from zoneinfo import ZoneInfo

import aiofiles
import aiohttp
import numpy as np
import orjson
import pandas as pd
from classic_formations import formation_fields_from_ohlcv, latest_formation_fields
import pytz
from dateutil.parser import isoparse

from config_tradier import TradierConfig
from tradier_api import TradierAPIClient
# wt_composite logic inlined into _inject_wt_composite() — no external dependency
from utils import (clean_nans, get_current_environment,
                   get_simple_redis_manager, load_environment_from_gpg,
                   safe_datetime)

try:
    import orjson
    def default_json_serializer(obj):
        if isinstance(obj, (datetime, pd.Timestamp)):
            return obj.isoformat()
        if isinstance(obj, pd.DataFrame):
            return obj.to_dict(orient='records')
        if isinstance(obj, pd.Series):
            return obj.to_list()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    def json_dumps(obj: Any, **kwargs) -> bytes:
        option = orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS
        return orjson.dumps(obj, default=default_json_serializer, option=option)
    def safe_json_loads(s: Union[bytes, bytearray, memoryview, str], **kwargs) -> Any:
        if not s: return {}
        if isinstance(s, str):
            if not s.strip(): return {}
            s = s.encode('utf-8')
        elif isinstance(s, bytes):
            if not s.strip(): return {}
        return orjson.loads(s)
    JSONDecodeError = orjson.JSONDecodeError
except ImportError:  
    import json
    def default_json_serializer(obj):
        if isinstance(obj, datetime):
            return obj.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        return str(obj)
    def json_dumps(obj: Any, **kwargs) -> str:
        if 'indent' not in kwargs:
            kwargs['indent'] = 2
        return json.dumps(obj, default=default_json_serializer, **kwargs)
    def safe_json_loads(s: Union[bytes, bytearray, memoryview, str], **kwargs) -> Any:
        if not s: return {}
        if isinstance(s, (bytes, bytearray, memoryview)):
            s = s.decode('utf-8')
        if not s.strip(): return {}
        return json.loads(s, **kwargs)
    JSONDecodeError = json.JSONDecodeError

load_environment_from_gpg(None)
config = TradierConfig()
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("tradier_indicators")
os.makedirs(config.LOG_DIR, exist_ok=True)

file_handler = RotatingFileHandler(config.LOG_DIR / "tradier_indicators.log", maxBytes=config.LOG_MAX_BYTES, backupCount=config.LOG_BACKUP_COUNT, encoding='utf-8', mode='a')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s")
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)
logger.setLevel(logging.DEBUG)
ET = pytz.timezone("America/New_York")

def _to_utc(series_or_val, errors="coerce"):
    """UTC system-wide: naive Tradier ET strings -> ET localize -> UTC. Z/-04:00 preserved."""
    try:
        # First parse without forcing utc, so we can detect naive
        parsed = pd.to_datetime(series_or_val, errors=errors)
        # If it's a Series/Index
        if hasattr(parsed, 'dt'):
            # Series case
            if parsed.dt.tz is None:
                # Naive -> assume ET (Tradier) -> UTC
                return parsed.dt.tz_localize(ET, ambiguous='infer', nonexistent='shift_forward').dt.tz_convert(pytz.UTC)
            else:
                return parsed.dt.tz_convert(pytz.UTC)
        else:
            # Scalar case
            if parsed.tzinfo is None:
                return parsed.tz_localize(ET).tz_convert(pytz.UTC) if hasattr(parsed, 'tz_localize') else pd.Timestamp(parsed).tz_localize(ET).tz_convert(pytz.UTC)
            else:
                return parsed.tz_convert(pytz.UTC)
    except Exception:
        try:
            return pd.to_datetime(series_or_val, utc=True, errors=errors)
        except Exception:
            return pd.to_datetime(series_or_val, format="mixed", utc=True, errors=errors)

def filter_strict_market_hours(df: pd.DataFrame, is_fast_tf: bool) -> pd.DataFrame:
    if df.empty: return df
    df = df.copy()
    source_col = "timestamp" if "timestamp" in df.columns else "time"
    dt_series = _to_utc(df[source_col]).dt.tz_convert(ET)
    time_series = dt_series.dt.time
    start_time = dt_time(8, 30) if is_fast_tf else dt_time(9, 30)
    end_time = dt_time(16, 0)
    mask = (time_series >= start_time) & (time_series <= end_time)
    return df.loc[mask].reset_index(drop=True)
def filter_regular_session(df):
    if df.empty: return df
    df = df.copy()
    ts_col = 'timestamp' if 'timestamp' in df.columns else 'time'
    source_col = "close_time" if "close_time" in df.columns else ts_col

    # Standardize to UTC-aware datetime
    df["close_time"] = pd.to_datetime(df[source_col], errors='coerce')
    if df["close_time"].dt.tz is None:
        df["close_time"] = df["close_time"].dt.tz_localize(ET, ambiguous='infer').dt.tz_convert('UTC')
    else:
        df["close_time"] = df["close_time"].dt.tz_convert('UTC')

    df = df.dropna(subset=['close_time'])
    if df.empty: return df

    dt_et = df["close_time"].dt.tz_convert(ET)
    # Strictly 09:30 to 16:30
    mask = (dt_et.dt.time >= pd.Timestamp("09:30").time()) & (dt_et.dt.time <= pd.Timestamp("16:30").time())
    return df.loc[mask].reset_index(drop=True)
def merge_klines(old_df, new_df):
    o = old_df if isinstance(old_df, pd.DataFrame) else pd.DataFrame(old_df)
    n = new_df if isinstance(new_df, pd.DataFrame) else pd.DataFrame(new_df)
    if o.empty: return n.copy() if not n.empty else n
    if n.empty: return o.copy()
    if 'timestamp' not in o.columns or 'timestamp' not in n.columns: return o.copy()
    
    o['timestamp'] = o['timestamp'].astype(str)
    n['timestamp'] = n['timestamp'].astype(str)
    try:
        o = o.dropna(axis=1, how='all')
        n = n.dropna(axis=1, how='all')        
        combined = pd.concat([o, n], axis=0, ignore_index=True, sort=False)
        combined = combined.drop_duplicates(subset=['timestamp'], keep='last')
        return combined.sort_values('timestamp').reset_index(drop=True)
    except Exception as e:
        logger.error(f"Merge failure: {e}")
        return o.copy()

def resample_tf(df, tf): 
    if df.empty: return pd.DataFrame()
    df = df.copy()
    
    source_col = "close_time" if "close_time" in df.columns else "timestamp"
    df["close_time"] = _to_utc(df[source_col])
    df = df.dropna(subset=['close_time'])
    if df.empty: return pd.DataFrame()
    
    for col in["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        
    df = df.set_index("close_time").sort_index()
    dt_et = df.index.tz_convert(ET)
    
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    
    if tf == "4h":
        def get_4h_bin(dt):
            t = dt.time()
            d = dt.date()
            if t < dt_time(9, 30):
                if dt.weekday() == 0: 
                    prev_d = (dt - timedelta(days=3)).date()
                elif dt.weekday() == 6: 
                    prev_d = (dt - timedelta(days=2)).date()
                else:
                    prev_d = (dt - timedelta(days=1)).date()
                return pd.Timestamp(datetime.combine(prev_d, dt_time(16, 0))).tz_localize(ET)
            elif t < dt_time(12, 45):
                return pd.Timestamp(datetime.combine(d, dt_time(9, 30))).tz_localize(ET)
            elif t < dt_time(16, 0):
                return pd.Timestamp(datetime.combine(d, dt_time(12, 45))).tz_localize(ET)
            else:
                return pd.Timestamp(datetime.combine(d, dt_time(16, 0))).tz_localize(ET)
                
        bins = dt_et.map(get_4h_bin)
        out = df.groupby(bins).agg(agg).dropna(subset=["close"])
        out.index.name = "close_time"
        out.index = pd.DatetimeIndex(out.index).tz_convert(pytz.UTC)
        
    elif tf == "1h":
        shift_delta = pd.Timedelta(hours=9, minutes=30)
        df.index = dt_et - shift_delta
        out = df.resample("1h", label="left", closed="left").agg(agg).dropna(subset=["close"])
        out.index.name = "close_time"
        out.index = (out.index + shift_delta).tz_convert(pytz.UTC)
        
    else:
        if tf == "3m": res_rule = "3min"
        elif tf == "5m": res_rule = "5min"
        else: res_rule = "15min"
        
        df.index = dt_et
        out = df.resample(res_rule, label="left", closed="left").agg(agg).dropna(subset=["close"])
        out.index.name = "close_time"
        out.index = out.index.tz_convert(pytz.UTC)
        
    out = out.reset_index()
    out["close_time"] = _to_utc(out["close_time"])
    out["timestamp"] = out["close_time"].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return out


def _ordinary_parent_available_at(label_dt: datetime, timeframe: str) -> datetime:
    """Return the conservative stock-session completion for a parent label."""
    label = pd.Timestamp(label_dt)
    if label.tzinfo is None:
        label = label.tz_localize(timezone.utc)
    label_et = label.tz_convert(ET)
    session_date = label_et.date()
    if timeframe == "D":
        available_et = ET.localize(
            datetime.combine(session_date, dt_time(16, 0))
        )
    elif timeframe == "4h":
        if label_et.time() < dt_time(12, 45):
            available_et = ET.localize(
                datetime.combine(session_date, dt_time(12, 45))
            )
        elif label_et.time() < dt_time(16, 0):
            available_et = ET.localize(
                datetime.combine(session_date, dt_time(16, 0))
            )
        else:
            # Any retained after-hours parent is deliberately delayed rather
            # than treated as a completed RTH parent at its opening label.
            available_et = label_et + pd.Timedelta(hours=4)
    else:
        available_et = min(
            label_et + pd.Timedelta(hours=1),
            pd.Timestamp(
                ET.localize(datetime.combine(session_date, dt_time(16, 0)))
            ),
        )
    return pd.Timestamp(available_et).tz_convert(timezone.utc).to_pydatetime()


def apply_session_compression(df): 
    if df.empty: return df
    df=df.copy().sort_values("close_time").reset_index(drop=True); df["plot_idx"]=np.arange(len(df)); return df


def is_intraday_data(df, expected_tf):
    if df.empty or len(df) < 3:
        return True
    if "time" in df.columns and df["time"].isna().all():
        return False
    ts_col = "timestamp" if "timestamp" in df.columns else None
    if not ts_col:
        return True
    try:
        dts = _to_utc(df[ts_col]).dropna()
        if len(dts) < 3:
            return True
        median_gap = dts.diff().dropna().dt.total_seconds().median()
        return median_gap < 14400
    except Exception:
        return True

def _ensure_float_series(series: pd.Series) -> pd.Series:
    return series.astype(float)

def rsi_series(series: pd.Series, length: int) -> Optional[pd.Series]:
    if series is None or len(series) < length + 1:
        return None
    data = _ensure_float_series(series)
    delta = data.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    alpha = 1.0 / float(length)
    avg_gain = gain.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi

def stoch_rsi(series: pd.Series, length: int, k: int, d: int) -> Optional[pd.DataFrame]:
    rsi = rsi_series(series, length)
    if rsi is None or rsi.dropna().empty:
        return None
    lowest = rsi.rolling(length, min_periods=length).min()
    highest = rsi.rolling(length, min_periods=length).max()
    range_span = (highest - lowest).replace(0.0, np.nan)
    stoch = ((rsi - lowest) / range_span).clip(lower=0.0, upper=1.0) * 100.0
    k_series = stoch.rolling(k, min_periods=k).mean()
    d_series = k_series.rolling(d, min_periods=d).mean()
    result = pd.DataFrame({"k": k_series, "d": d_series})
    return result

def atr_series(df: pd.DataFrame, length: int) -> Optional[pd.Series]:
    if df is None or df.empty or len(df) < length + 1:
        return None
    high = _ensure_float_series(df["high"])
    low = _ensure_float_series(df["low"])
    close = _ensure_float_series(df["close"])
    prev_close = close.shift(1)
    tr_components = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
    true_range = tr_components.max(axis=1)
    alpha = 1.0 / float(length)
    atr = true_range.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    return atr

def adx_value(df: pd.DataFrame, length: int = 14) -> Optional[float]:
    if df is None or df.empty or len(df) < length * 2 + 1: return None
    high = _ensure_float_series(df["high"])
    low = _ensure_float_series(df["low"])
    plus_dm = high.diff().clip(lower=0.0)
    minus_dm = (-low.diff()).clip(lower=0.0)
    mask = plus_dm < minus_dm
    plus_dm = plus_dm.where(~mask, 0.0)
    minus_dm = minus_dm.where(mask, 0.0)
    atr = atr_series(df, length)
    if atr is None: return None
    alpha = 1.0 / float(length)
    plus_di = 100.0 * (plus_dm.ewm(alpha=alpha, adjust=False, min_periods=length).mean() / atr.replace(0.0, np.nan))
    minus_di = 100.0 * (minus_dm.ewm(alpha=alpha, adjust=False, min_periods=length).mean() / atr.replace(0.0, np.nan))
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = ((plus_di - minus_di).abs() / di_sum) * 100.0
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    val = adx.iloc[-1]
    return float(val) if pd.notna(val) else None

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def isoformat(ts) -> str:
    if ts is None: return ""
    if isinstance(ts, str):
        try:
            ts = pd.to_datetime(ts, utc=True).to_pydatetime()
        except Exception:
            if ts.endswith("+00:00"): return ts.replace("+00:00", "Z")
            return ts
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    if isinstance(ts, datetime):
        if getattr(ts, 'tzinfo', None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return str(ts)

def with_mark_price(df: pd.DataFrame, mark_price: float, mark_ts) -> pd.DataFrame:
    if df.empty: return df
    last_idx = df.index[-1]
    df.at[last_idx, 'close'] = float(mark_price)
    if df.at[last_idx, 'high'] < float(mark_price):
        df.at[last_idx, 'high'] = float(mark_price)
    if df.at[last_idx, 'low'] > float(mark_price):
        df.at[last_idx, 'low'] = float(mark_price)
    if isinstance(mark_ts, str):
        ts_string = mark_ts
    elif hasattr(mark_ts, 'isoformat'):
        ts_string = mark_ts.isoformat()
    else:
        ts_string = str(mark_ts)
    if "timestamp_dt" in df.columns:
        try:
            df.at[last_idx, "timestamp_dt"] = pd.to_datetime(ts_string, utc=True)
        except Exception: pass  
    if "timestamp" in df.columns:
        df.at[last_idx, "timestamp"] = ts_string
    if "close_time" in df.columns:
        df.at[last_idx, "close_time"] = ts_string
    if "time" in df.columns:
        df.at[last_idx, "time"] = ts_string   
    return df

def linreg_features(series: pd.Series, length: int) -> Tuple[Optional[float], Optional[float]]:
    if series is None or len(series) < length:
        return None, None
    window = series.iloc[-length:]
    x = np.arange(len(window))
    y = window.values.astype(float)
    x_mean = np.mean(x)
    y_mean = np.mean(y)
    denominator = np.sum((x - x_mean) ** 2)
    if denominator == 0:
        return None, None
    slope = np.sum((x - x_mean) * (y - y_mean)) / denominator
    # 2026-04-29: bug fix — y_fit was using x_mean instead of y_mean. See _helpers.py.
    y_fit = y_mean + slope * (x - x_mean)
    residuals = y - y_fit
    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((y - y_mean) ** 2)
    linearity = 1 - ss_res / ss_tot if ss_tot != 0 else 0
    return float(slope), float(linearity)

def bb_features(series: pd.Series, length: int = 20, std_mult: float = 2.0) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if series is None or len(series) < length:
        return None, None, None
    window = series.iloc[-length:]
    mid = float(window.mean())
    std = float(window.std(ddof=0))
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    price = float(series.iloc[-1])
    band_width = upper - lower
    pct_b = (price - lower) / band_width if band_width > 0 else 0.5
    return upper, lower, max(0.0, min(1.0, pct_b))

def linreg_channel(series: pd.Series, length: int, std_mult: float = 2.5) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if series is None or len(series) < length:
        return None, None, None
    window = series.iloc[-length:]
    x = np.arange(len(window))
    y = window.values.astype(float)
    x_mean = np.mean(x); y_mean = np.mean(y)
    denom = np.sum((x - x_mean) ** 2)
    if denom == 0:
        return None, None, None
    slope = np.sum((x - x_mean) * (y - y_mean)) / denom
    y_fit = y_mean + slope * (x - x_mean)
    residual_std = float(np.std(y - y_fit, ddof=0))
    price = float(series.iloc[-1])
    fit_end = float(y_fit[-1])
    upper = fit_end + std_mult * residual_std
    lower = fit_end - std_mult * residual_std
    band_width = upper - lower
    pct_b = (price - lower) / band_width if band_width > 0 else 0.5
    return upper, lower, max(0.0, min(1.0, pct_b))

def donchian(high: pd.Series, low: pd.Series, window: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(high) < window or len(low) < window:
        return None, None, None
    high_series = high.rolling(window, min_periods=window).max()
    low_series = low.rolling(window, min_periods=window).min()
    if high_series.empty or low_series.empty:
        return None, None, None
    high_val = high_series.iloc[-1]
    low_val = low_series.iloc[-1]
    if pd.isna(high_val) or pd.isna(low_val):
        return None, None, None
    basis_val = (high_val + low_val) / 2.0
    return float(high_val), float(low_val), float(basis_val)

def donchian_prev(high: pd.Series, low: pd.Series, window: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(high) < window + 1 or len(low) < window + 1:
        return None, None, None
    high_series = high.rolling(window, min_periods=window).max()
    low_series = low.rolling(window, min_periods=window).min()
    if len(high_series) < 2 or len(low_series) < 2:
        return None, None, None
    high_val = high_series.iloc[-2]
    low_val = low_series.iloc[-2]
    if pd.isna(high_val) or pd.isna(low_val):
        return None, None, None
    basis_val = (high_val + low_val) / 2.0
    return float(high_val), float(low_val), float(basis_val)

# Per-TF WaveTrend parameters for stocks
# Validated by test_wt_optimization.py: 48 stocks, 3+ years, 1h TF sweep (2026-03-25)
# Stocks: EMA > DEMA (less noise), D_dom TF weights (daily TF matters most), CI=0.010
# Phase 0: esa=6-14 (flat), sig=15-25 (flat), EMA wins (62% top 50)
# Phase 3: CI=0.010 (+5.59), smooth=3-4 (close)
# Phase 4: D_dom weights = Sharpe 5.91 (vs default 5.42)
WT_TF_PARAMS_STOCK = {
    "5m": {"esa": 8, "chan": 12, "sig": 15, "smooth": 3, "esa_ma": "ema", "chan_ma": "ema", "ci": 0.010},
    "15m": {"esa": 8, "chan": 14, "sig": 21, "smooth": 3, "esa_ma": "ema", "chan_ma": "ema", "ci": 0.010},
    "1h": {"esa": 10, "chan": 10, "sig": 21, "smooth": 3, "esa_ma": "ema", "chan_ma": "ema", "ci": 0.010},
    "4h": {"esa": 10, "chan": 14, "sig": 21, "smooth": 4, "esa_ma": "ema", "chan_ma": "ema", "ci": 0.010},
    "D": {"esa": 10, "chan": 18, "sig": 25, "smooth": 4, "esa_ma": "ema", "chan_ma": "ema", "ci": 0.010},
}
def _dema_pd_stock(series: pd.Series, span: int) -> pd.Series:
    e1 = series.ewm(span=span, adjust=False).mean()
    e2 = e1.ewm(span=span, adjust=False).mean()
    return 2 * e1 - e2
def _ma_pd_stock(series: pd.Series, span: int, ma_type: str) -> pd.Series:
    if ma_type == "dema": return _dema_pd_stock(series, span)
    elif ma_type == "sma": return series.rolling(window=span, min_periods=1).mean()
    return series.ewm(span=span, adjust=False).mean()
def wavetrend(df: pd.DataFrame, timeframe: str = "") -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
    p = WT_TF_PARAMS_STOCK.get(timeframe, {"esa": 10, "chan": 10, "sig": 21, "smooth": 3, "esa_ma": "ema", "chan_ma": "ema", "ci": 0.010})
    if len(df) < max(p["esa"], p["sig"]):
        return None, None
    typical = (df["high"] + df["low"] + df["close"]) / 3
    esa = _ma_pd_stock(typical, p["esa"], p["esa_ma"])
    d = _ma_pd_stock((typical - esa).abs(), p["chan"], p["chan_ma"])
    ci = (typical - esa) / (p["ci"] * d.replace(0, 1e-10))
    wt1 = ci.ewm(span=p["sig"], adjust=False).mean()
    wt2 = wt1.rolling(window=p["smooth"], min_periods=1).mean()
    return wt1, wt2

def analyze_multi_tf_state_tradier(ind: dict, is_long: bool, current_price: float, config=None) -> dict:
    """Unified K+WT+DC multi-TF analysis for STOCKS. Three signal layers per TF:
    1. K zones: WHERE in the stochastic range
    2. WT intelligence: WHAT's happening (crosses, velocity, divergence)
    3. DC position: WHERE in the actual price range (0=bottom, 1=top)
    Stock-specific: D_dom weighting, EMA-based WT, wider channels."""
    _sf = lambda v, d=0.0: float(v) if v is not None else d
    _sb = lambda v, d=False: v if v is not None else d
    _w = lambda tf, d: getattr(config, f'MTS_WEIGHT_{tf}', d) if config and hasattr(config, f'MTS_WEIGHT_{tf}') else d
    _tf_cfg = [('5m', _w('5m', 2), 'k_5m', 'd_5m'), ('15m', _w('15m', 4), 'k_15m', 'd_15m'), ('1h', _w('1h', 3), 'k_1h', 'd_1h'), ('4h', _w('4h', 5), 'k_4h', 'd_4h'), ('D', _w('D', 12), 'k_D', 'd_D')]
    tf_breakdown = {}
    total_bottom = 0.0; total_entry = 0.0; total_dir = 0.0; total_range = 0.0; total_weight = 0.0
    extreme_count = 0; htf_bullish_count = 0
    details = []
    for tf_name, weight, k_key, d_key in _tf_cfg:
        k_val = _sf(ind.get(k_key), 50.0); d_val = _sf(ind.get(d_key), 50.0)
        k_prev = _sf(ind.get(f'{k_key}_prev'), k_val)
        wt1 = _sf(ind.get(f'wt1_{tf_name}')); wt2 = _sf(ind.get(f'wt2_{tf_name}'))
        wt_score = wt1 - wt2
        wt_cross = ind.get(f'wt_cross_{tf_name}')
        wt_cross_val = _sf(ind.get(f'wt_cross_value_{tf_name}'), None)
        wt_cross_prev = _sf(ind.get(f'wt_cross_prev_value_{tf_name}'), None)
        wt_cross_rising = _sb(ind.get(f'wt_cross_rising_{tf_name}'))
        wt_velocity = _sf(ind.get(f'wt_velocity_{tf_name}'))
        wt_bars_ago = _sf(ind.get(f'wt_cross_bars_ago_{tf_name}'), 999)
        wt_divergence = ind.get(f'wt_divergence_{tf_name}', '')
        wt_momentum = ind.get(f'wt_momentum_state_{tf_name}', '')
        wt_bullish = wt1 > wt2
        # DC position per TF: 0=bottom, 1=top of channel
        dc_pos = _sf(ind.get(f'dc_position_{tf_name}'), 0.5)
        if dc_pos > 1.0: dc_pos = dc_pos / 100.0
        dc_pos = max(0.0, min(1.0, dc_pos))
        dc_width = _sf(ind.get(f'dc_width_{tf_name}'), 0)
        k_extreme_long = k_val < 10; k_extreme_short = k_val > 90
        k_zone_long = k_val < 30; k_zone_short = k_val > 70
        k_rising = k_val > k_prev
        if is_long and k_extreme_long: extreme_count += 1
        elif not is_long and k_extreme_short: extreme_count += 1
        tf_bottom = 0.0
        if is_long:
            if k_extreme_long: tf_bottom += 20
            if k_zone_long: tf_bottom += 10
            if wt_cross == "BULL" and wt_bars_ago < 5: tf_bottom += 15
            if wt_cross_val is not None and wt_cross_val < -40: tf_bottom += 15
            elif wt_cross_val is not None and wt_cross_val < -20: tf_bottom += 8
            if wt_cross_rising: tf_bottom += 10
            if wt_divergence in ('BULL', 'HIDDEN_BULL'): tf_bottom += 20
            if wt_momentum == 'EXHAUST_DOWN': tf_bottom += 10
            if wt_velocity > 0 and k_rising: tf_bottom += 5
            if dc_pos < 0.10: tf_bottom += 25
            elif dc_pos < 0.20: tf_bottom += 18
            elif dc_pos < 0.30: tf_bottom += 12
            elif dc_pos < 0.40: tf_bottom += 5
            elif dc_pos > 0.80: tf_bottom -= 10
        else:
            if k_extreme_short: tf_bottom += 20
            if k_zone_short: tf_bottom += 10
            if wt_cross == "BEAR" and wt_bars_ago < 5: tf_bottom += 15
            if wt_cross_val is not None and wt_cross_val > 40: tf_bottom += 15
            elif wt_cross_val is not None and wt_cross_val > 20: tf_bottom += 8
            if wt_cross_rising: tf_bottom += 10
            if wt_divergence in ('BEAR', 'HIDDEN_BEAR'): tf_bottom += 20
            if wt_momentum == 'EXHAUST_UP': tf_bottom += 10
            if wt_velocity < 0 and not k_rising: tf_bottom += 5
            if dc_pos > 0.90: tf_bottom += 25
            elif dc_pos > 0.80: tf_bottom += 18
            elif dc_pos > 0.70: tf_bottom += 12
            elif dc_pos > 0.60: tf_bottom += 5
            elif dc_pos < 0.20: tf_bottom -= 10
        tf_entry = 0.0
        if wt_cross_val is not None and wt_cross_prev is not None:
            if is_long and wt_cross_val > wt_cross_prev: tf_entry += 15
            elif not is_long and wt_cross_val < wt_cross_prev: tf_entry += 15
        if wt_bars_ago < 3: tf_entry += 15
        elif wt_bars_ago < 10: tf_entry += 5
        if wt_cross_rising: tf_entry += 10
        if is_long and dc_pos < 0.25 and wt_bullish: tf_entry += 15
        elif not is_long and dc_pos > 0.75 and not wt_bullish: tf_entry += 15
        tf_range = ((1.0 - dc_pos) * 100) if is_long else (dc_pos * 100)
        if dc_width > 5.0: tf_range *= 1.3
        elif dc_width < 2.0: tf_range *= 0.5
        tf_dir = 0.0
        if wt_bullish: tf_dir += 30; htf_bullish_count += 1
        if wt_score > 10: tf_dir += 20
        elif wt_score > 0: tf_dir += 10
        elif wt_score < -10: tf_dir -= 20
        elif wt_score < 0: tf_dir -= 10
        if k_val > d_val: tf_dir += 10
        else: tf_dir -= 10
        if wt_velocity > 2: tf_dir += 15
        elif wt_velocity < -2: tf_dir -= 15
        tf_breakdown[tf_name] = {'bottom': tf_bottom, 'entry': tf_entry, 'direction': tf_dir, 'range': tf_range, 'k': k_val, 'd': d_val, 'dc_pos': dc_pos, 'dc_width': dc_width, 'wt_score': wt_score, 'wt_bullish': wt_bullish, 'wt_velocity': wt_velocity, 'wt_bars_ago': wt_bars_ago}
        total_bottom += tf_bottom * weight; total_entry += tf_entry * weight; total_dir += tf_dir * weight; total_range += tf_range * weight; total_weight += weight
    if total_weight > 0:
        bottom_score = total_bottom / total_weight; entry_quality = total_entry / total_weight; direction = total_dir / total_weight; range_score = total_range / total_weight
    else:
        bottom_score = 0.0; entry_quality = 0.0; direction = 0.0; range_score = 50.0
    if extreme_count >= 4: bottom_score *= 2.0; details.append(f"K_EXTREME_4+({extreme_count}TF)")
    elif extreme_count >= 3: bottom_score *= 1.5; details.append(f"K_EXTREME_3({extreme_count}TF)")
    entry_quality += htf_bullish_count * 5
    bottom_score = max(-100, min(100, bottom_score)); entry_quality = max(-100, min(100, entry_quality)); direction = max(-100, min(100, direction))
    # Gain potential from DC width + range score (how much room to profit)
    _dc_h_1h = _sf(ind.get('dc_high_1h'), 0); _dc_l_1h = _sf(ind.get('dc_low_1h'), 0)
    dc_width_pct = ((_dc_h_1h - _dc_l_1h) / current_price * 100) if current_price > 0 and _dc_l_1h > 0 else 2.0
    gain_potential = min(100, dc_width_pct * 15)  # Stocks: wider channels = 15x (vs 20x crypto)
    gain_potential = gain_potential * (range_score / 50.0)
    gain_potential = min(100, max(0, gain_potential))
    if abs(direction) > 50: gain_potential *= 1.2
    gain_potential = min(100, gain_potential)
    size_multiplier = max(0.3, min(3.0, 0.5 + gain_potential / 50))
    # === MULTI-TF CONVERGENCE — same logic as crypto (see ez_positions_quick.py) ===
    # Phase 1: detect bottom/top convergence (WT low + K low + DC near extreme = 2+ signals per TF)
    # Phase 2: WITH convergence = bounce/reversal play. THROUGH convergence = breakout/breakdown play.
    _conv_dc_thresh = 0.15; _conv_wt_bars = 12
    bottom_conv_count = 0; bottom_conv_tfs = []; bottom_conv_score = 0.0
    top_conv_count = 0; top_conv_tfs = []; top_conv_score = 0.0
    for tf_name, tfd in tf_breakdown.items():
        dc_p = tfd.get('dc_pos', 0.5); k = tfd.get('k', 50); wt_sc = tfd.get('wt_score', 0); wt_ba = tfd.get('wt_bars_ago', 999)
        _bot_sigs = int(dc_p < _conv_dc_thresh) + int(wt_sc < -15) + int(k < 25) + int(wt_ba < _conv_wt_bars and tfd.get('wt_bullish', False))
        if _bot_sigs >= 2:
            bottom_conv_count += 1; bottom_conv_tfs.append(tf_name)
            bottom_conv_score += min(1.0, _bot_sigs / 4.0) * (1.0 - dc_p / max(_conv_dc_thresh, 0.01))
        _top_sigs = int(dc_p > 1.0 - _conv_dc_thresh) + int(wt_sc > 15) + int(k > 75) + int(wt_ba < _conv_wt_bars and not tfd.get('wt_bullish', True))
        if _top_sigs >= 2:
            top_conv_count += 1; top_conv_tfs.append(tf_name)
            top_conv_score += min(1.0, _top_sigs / 4.0) * (1.0 - (1.0 - dc_p) / max(_conv_dc_thresh, 0.01))
    _focus_dc = 0.5
    for _ftf in ['5m', '15m', '1h']:
        _v = float(ind.get(f'dc_position_{_ftf}', 0.5) or 0.5)
        if _v != 0.5: _focus_dc = _v; break
    if _focus_dc > 1.0: _focus_dc /= 100.0
    _price_below = _focus_dc < 0.05; _price_above = _focus_dc > 0.95
    _price_near_low = _focus_dc < 0.15; _price_near_high = _focus_dc > 0.85
    btb_multiplier = 1.0; convergence_count = 0; convergence_tfs = []; convergence_score = 0.0; _price_through_extreme = False
    if is_long:
        if bottom_conv_count >= 2 and (_price_near_low or _price_below):
            convergence_count = bottom_conv_count; convergence_tfs = bottom_conv_tfs; convergence_score = bottom_conv_score
            btb_multiplier = 3.0 if bottom_conv_count >= 4 else (2.5 if bottom_conv_count >= 3 else 1.8)
            if _price_below: btb_multiplier *= 1.2; _price_through_extreme = True
            details.append(f"BTB_LONG_BOUNCE({bottom_conv_count}TF:{'+'.join(bottom_conv_tfs)},dc={_focus_dc:.3f},x{btb_multiplier:.1f})")
            bottom_score += 10 * bottom_conv_count; entry_quality += 8 * bottom_conv_count
        elif top_conv_count >= 2 and _price_above:
            convergence_count = top_conv_count; convergence_tfs = top_conv_tfs; convergence_score = top_conv_score; _price_through_extreme = True
            btb_multiplier = 3.5 if top_conv_count >= 4 else (3.0 if top_conv_count >= 3 else 2.0)
            details.append(f"BTB_LONG_BREAKOUT({top_conv_count}TF_RES_BROKEN:{'+'.join(top_conv_tfs)},dc={_focus_dc:.3f},x{btb_multiplier:.1f})")
            bottom_score += 15 * top_conv_count; entry_quality += 12 * top_conv_count
    else:
        if top_conv_count >= 2 and (_price_near_high or _price_above):
            convergence_count = top_conv_count; convergence_tfs = top_conv_tfs; convergence_score = top_conv_score
            btb_multiplier = 3.0 if top_conv_count >= 4 else (2.5 if top_conv_count >= 3 else 1.8)
            if _price_above: btb_multiplier *= 1.2; _price_through_extreme = True
            details.append(f"BTB_SHORT_REVERSAL({top_conv_count}TF:{'+'.join(top_conv_tfs)},dc={_focus_dc:.3f},x{btb_multiplier:.1f})")
            bottom_score += 10 * top_conv_count; entry_quality += 8 * top_conv_count
        elif bottom_conv_count >= 2 and _price_below:
            convergence_count = bottom_conv_count; convergence_tfs = bottom_conv_tfs; convergence_score = bottom_conv_score; _price_through_extreme = True
            btb_multiplier = 3.5 if bottom_conv_count >= 4 else (3.0 if bottom_conv_count >= 3 else 2.0)
            details.append(f"BTB_SHORT_BREAKDOWN({bottom_conv_count}TF_SUP_BROKEN:{'+'.join(bottom_conv_tfs)},dc={_focus_dc:.3f},x{btb_multiplier:.1f})")
            bottom_score += 15 * bottom_conv_count; entry_quality += 12 * bottom_conv_count
    size_multiplier = max(0.3, min(5.0, size_multiplier * btb_multiplier))
    bottom_score = max(-100, min(100, bottom_score)); entry_quality = max(-100, min(100, entry_quality))
    _top3 = sorted(tf_breakdown.items(), key=lambda x: x[1]['bottom'], reverse=True)[:3]
    for tf, d in _top3:
        if d['bottom'] > 0: details.append(f"{tf}:b{d['bottom']:.0f}/dc{d['dc_pos']:.2f}/wt{d['wt_score']:.0f}")
    details.append(f"dir={direction:.0f}/eq={entry_quality:.0f}/gp={gain_potential:.0f}/rng={range_score:.0f}/sz={size_multiplier:.1f}x/conv={convergence_count}")
    return {"bottom_score": bottom_score, "entry_quality": entry_quality, "gain_potential": gain_potential, "direction": direction, "size_multiplier": size_multiplier, "range_score": range_score, "convergence_count": convergence_count, "convergence_score": convergence_score, "btb_multiplier": btb_multiplier, "price_through_extreme": _price_through_extreme, "details": "|".join(details), "tf_breakdown": tf_breakdown}

def wavetrend_intelligence(wt1_series: pd.Series, wt2_series: pd.Series, close_series: pd.Series, high_series: pd.Series, low_series: pd.Series, timeframe: str) -> Dict[str, Any]:
    result = {}
    tf = timeframe
    wt1_arr = wt1_series.values.astype(float)
    wt2_arr = wt2_series.values.astype(float)
    close_arr = close_series.values.astype(float)
    high_arr = high_series.values.astype(float)
    low_arr = low_series.values.astype(float)
    n = len(wt1_arr)
    if n < 5:
        return result
    wt1_val = float(wt1_arr[-1])
    wt2_val = float(wt2_arr[-1])
    result[f"wt1_{tf}"] = wt1_val
    result[f"wt2_{tf}"] = wt2_val
    result[f"wt_score_{tf}"] = wt1_val - wt2_val
    cross_above = (wt1_arr[1:] > wt2_arr[1:]) & (wt1_arr[:-1] <= wt2_arr[:-1])
    cross_below = (wt1_arr[1:] < wt2_arr[1:]) & (wt1_arr[:-1] >= wt2_arr[:-1])
    cross_above_idx = np.where(cross_above)[0] + 1
    cross_below_idx = np.where(cross_below)[0] + 1
    # Most recent cross (persists after cross bar — not just exact bar)
    recent_cross = None; cross_value = None; cross_prev_value = None; cross_rising = None; bars_ago = 999
    if len(cross_above_idx) > 0 and len(cross_below_idx) > 0:
        if cross_above_idx[-1] > cross_below_idx[-1]:
            recent_cross = "BULL"; bars_ago = n - 1 - cross_above_idx[-1]; cross_value = float(wt1_arr[cross_above_idx[-1]])
            if len(cross_above_idx) >= 2: cross_prev_value = float(wt1_arr[cross_above_idx[-2]]); cross_rising = cross_value > cross_prev_value
        else:
            recent_cross = "BEAR"; bars_ago = n - 1 - cross_below_idx[-1]; cross_value = float(wt1_arr[cross_below_idx[-1]])
            if len(cross_below_idx) >= 2: cross_prev_value = float(wt1_arr[cross_below_idx[-2]]); cross_rising = cross_value < cross_prev_value
    elif len(cross_above_idx) > 0:
        recent_cross = "BULL"; bars_ago = n - 1 - cross_above_idx[-1]; cross_value = float(wt1_arr[cross_above_idx[-1]])
        if len(cross_above_idx) >= 2: cross_prev_value = float(wt1_arr[cross_above_idx[-2]]); cross_rising = cross_value > cross_prev_value
    elif len(cross_below_idx) > 0:
        recent_cross = "BEAR"; bars_ago = n - 1 - cross_below_idx[-1]; cross_value = float(wt1_arr[cross_below_idx[-1]])
        if len(cross_below_idx) >= 2: cross_prev_value = float(wt1_arr[cross_below_idx[-2]]); cross_rising = cross_value < cross_prev_value
    result[f"wt_cross_{tf}"] = recent_cross
    result[f"wt_cross_value_{tf}"] = cross_value
    result[f"wt_cross_prev_value_{tf}"] = cross_prev_value
    result[f"wt_cross_rising_{tf}"] = cross_rising
    result[f"wt_cross_bars_ago_{tf}"] = bars_ago
    lookback = min(50, n - 1)
    start = n - 1 - lookback
    result[f"wt_cross_count_bull_{tf}"] = int(np.sum(cross_above[max(0, start - 1):]))
    result[f"wt_cross_count_bear_{tf}"] = int(np.sum(cross_below[max(0, start - 1):]))
    peaks_mask = np.zeros(n, dtype=bool)
    troughs_mask = np.zeros(n, dtype=bool)
    if n >= 3:
        peaks_mask[1:-1] = (wt1_arr[1:-1] > wt1_arr[:-2]) & (wt1_arr[1:-1] > wt1_arr[2:])
        troughs_mask[1:-1] = (wt1_arr[1:-1] < wt1_arr[:-2]) & (wt1_arr[1:-1] < wt1_arr[2:])
    peak_idx = np.where(peaks_mask)[0]
    trough_idx = np.where(troughs_mask)[0]
    wt_peak = float(wt1_arr[peak_idx[-1]]) if len(peak_idx) >= 1 else None
    wt_peak_prev = float(wt1_arr[peak_idx[-2]]) if len(peak_idx) >= 2 else None
    wt_trough = float(wt1_arr[trough_idx[-1]]) if len(trough_idx) >= 1 else None
    wt_trough_prev = float(wt1_arr[trough_idx[-2]]) if len(trough_idx) >= 2 else None
    result[f"wt_peak_{tf}"] = wt_peak
    result[f"wt_peak_prev_{tf}"] = wt_peak_prev
    result[f"wt_trough_{tf}"] = wt_trough
    result[f"wt_trough_prev_{tf}"] = wt_trough_prev
    peak_structure = ("HH" if wt_peak > wt_peak_prev else "LH") if wt_peak is not None and wt_peak_prev is not None else None
    trough_structure = ("HL" if wt_trough > wt_trough_prev else "LL") if wt_trough is not None and wt_trough_prev is not None else None
    result[f"wt_peak_structure_{tf}"] = peak_structure
    result[f"wt_trough_structure_{tf}"] = trough_structure
    result[f"wt_structure_{tf}"] = peak_structure if peak_structure is not None else trough_structure
    divergence, divergence_strength = None, 0.0
    price_peaks_mask = np.zeros(n, dtype=bool)
    price_troughs_mask = np.zeros(n, dtype=bool)
    if n >= 3:
        price_peaks_mask[1:-1] = (high_arr[1:-1] > high_arr[:-2]) & (high_arr[1:-1] > high_arr[2:])
        price_troughs_mask[1:-1] = (low_arr[1:-1] < low_arr[:-2]) & (low_arr[1:-1] < low_arr[2:])
    ppeak_idx = np.where(price_peaks_mask)[0]
    ptrough_idx = np.where(price_troughs_mask)[0]
    if len(ptrough_idx) >= 2 and wt_trough is not None and wt_trough_prev is not None:
        pt, ptp = float(low_arr[ptrough_idx[-1]]), float(low_arr[ptrough_idx[-2]])
        if pt < ptp and wt_trough > wt_trough_prev: divergence = "BULL"; divergence_strength = min(1.0, (abs(ptp - pt) / (abs(ptp) + 1e-10) + abs(wt_trough - wt_trough_prev) / (abs(wt_trough_prev) + 1e-10)) / 2.0)
        elif pt > ptp and wt_trough < wt_trough_prev: divergence = "HIDDEN_BULL"; divergence_strength = min(1.0, abs(wt_trough_prev - wt_trough) / (abs(wt_trough_prev) + 1e-10))
    if divergence is None and len(ppeak_idx) >= 2 and wt_peak is not None and wt_peak_prev is not None:
        pp, ppp = float(high_arr[ppeak_idx[-1]]), float(high_arr[ppeak_idx[-2]])
        if pp > ppp and wt_peak < wt_peak_prev: divergence = "BEAR"; divergence_strength = min(1.0, (abs(pp - ppp) / (abs(ppp) + 1e-10) + abs(wt_peak_prev - wt_peak) / (abs(wt_peak_prev) + 1e-10)) / 2.0)
        elif pp < ppp and wt_peak > wt_peak_prev: divergence = "HIDDEN_BEAR"; divergence_strength = min(1.0, abs(wt_peak - wt_peak_prev) / (abs(wt_peak_prev) + 1e-10))
    result[f"wt_divergence_{tf}"] = divergence
    result[f"wt_divergence_strength_{tf}"] = round(divergence_strength, 4)
    lag = 3
    velocity = wt1_arr[-1] - wt1_arr[-1 - lag] if n > lag else 0.0
    velocity_prev = wt1_arr[-1 - lag] - wt1_arr[-1 - 2 * lag] if n > 2 * lag else 0.0
    acceleration = velocity - velocity_prev
    result[f"wt_velocity_{tf}"] = round(velocity, 4)
    result[f"wt_acceleration_{tf}"] = round(acceleration, 4)
    wt_rising = velocity > 0
    momentum_state = "IMPULSE_UP" if wt_rising and acceleration > 0 else "EXHAUST_UP" if wt_rising else "IMPULSE_DOWN" if acceleration < 0 else "EXHAUST_DOWN"
    result[f"wt_momentum_state_{tf}"] = momentum_state
    window = min(200, n)
    wt1_window = wt1_arr[-window:]
    percentile = float(np.sum(wt1_window <= wt1_val) / window * 100.0)
    mean_w, std_w = float(np.mean(wt1_window)), float(np.std(wt1_window))
    zscore = (wt1_val - mean_w) / std_w if std_w > 1e-10 else 0.0
    result[f"wt_percentile_{tf}"] = round(percentile, 2)
    result[f"wt_zscore_{tf}"] = round(zscore, 4)
    result[f"wt_extreme_{tf}"] = abs(zscore) > 2.0
    zero_cross_idx = np.where((wt1_arr[1:] * wt1_arr[:-1]) < 0)[0] + 1
    wave_phase = "TRANSITIONING"
    if len(zero_cross_idx) >= 1 and zero_cross_idx[-1] == n - 1: wave_phase = "TRANSITIONING"
    elif len(peak_idx) >= 2: wave_phase = "EXPANDING" if abs(wt1_arr[peak_idx[-1]]) > abs(wt1_arr[peak_idx[-2]]) else "CONTRACTING"
    elif len(trough_idx) >= 2: wave_phase = "EXPANDING" if abs(wt1_arr[trough_idx[-1]]) > abs(wt1_arr[trough_idx[-2]]) else "CONTRACTING"
    result[f"wt_wave_phase_{tf}"] = wave_phase
    prev_wt1 = float(wt1_arr[-2]) if n > 1 else wt1_val
    prev_wt2 = float(wt2_arr[-2]) if n > 1 else wt2_val
    if prev_wt1 <= prev_wt2 and wt1_val > wt2_val and wt1_val < -50: result[f"wt_signal_{tf}"] = "BUY"
    elif prev_wt1 >= prev_wt2 and wt1_val < wt2_val and wt1_val > 50: result[f"wt_signal_{tf}"] = "SELL"
    else: result[f"wt_signal_{tf}"] = "NEUTRAL"
    return result

def heikin_ashi(df: pd.DataFrame) -> Tuple[str, Optional[str]]:
    if df.empty:
        return "neutral", None
    ha_close = (df["open"].iloc[-1] + df["high"].iloc[-1] + df["low"].iloc[-1] + df["close"].iloc[-1]) / 4.0
    if len(df) >= 2:
        prev_close = (df["open"].iloc[-2] + df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 4.0
        prev_open = (df["open"].iloc[-2] + df["close"].iloc[-2]) / 2.0
    else:
        prev_close = df["close"].iloc[-1]
        prev_open = df["open"].iloc[-1]
    ha_open = (prev_close + prev_open) / 2.0
    current_color = "green" if ha_close >= ha_open else "red"
    prev_color = None
    if len(df) >= 2:
        if len(df) >= 3:
            p_close = (df["open"].iloc[-3] + df["high"].iloc[-3] + df["low"].iloc[-3] + df["close"].iloc[-3]) / 4.0
            p_open = (df["open"].iloc[-3] + df["close"].iloc[-3]) / 2.0
        else:
            p_close = df["close"].iloc[-2]
            p_open = df["open"].iloc[-2]
        prev_color = "green" if p_close >= p_open else "red"
    return current_color, prev_color

def wma(series: pd.Series, length: int) -> pd.Series:
    if len(series) < length or length <= 0: return pd.Series(index=series.index, dtype=float)
    weights = np.arange(1, length + 1)
    return series.rolling(length).apply(lambda x: np.sum(weights * x) / np.sum(weights), raw=True)

def hma(series: pd.Series, length: int) -> pd.Series:
    if len(series) < length: return pd.Series(index=series.index, dtype=float)
    half_length = max(1, int(length / 2))
    sqrt_length = max(1, int(np.sqrt(length)))
    wma_half = wma(series, half_length)
    wma_full = wma(series, length)
    diff = 2 * wma_half - wma_full
    return wma(diff, sqrt_length)

def thma(series: pd.Series, length: int) -> pd.Series:
    if len(series) < length: return pd.Series(index=series.index, dtype=float)
    len_6, len_4, len_2 = max(1, length // 6), max(1, length // 4), max(1, length // 2)
    wma_6, wma_4, wma_2 = wma(series, len_6), wma(series, len_4), wma(series, len_2)
    return wma(3 * wma_6 - wma_4 - wma_2, len_2)

def hull_trend_indicators(close_series: pd.Series, length_short: int = 9, length_long: int = 21) -> Tuple[Optional[bool], Optional[bool], Optional[bool]]:
    if len(close_series) < max(length_long, 3): return None, None, None
    try:
        thma_short = thma(close_series, length_short)
        thma_long = thma(close_series, length_long)
        hulle_short = hma(thma_short, 3)
        hulle_long = hma(thma_long, 3)
        if len(hulle_short) < 3: return None, None, None
        
        t_up = bool(hulle_short.iloc[-1] > hulle_long.iloc[-1])
        cur, prev, prev2 = hulle_short.iloc[-1], hulle_short.iloc[-2], hulle_short.iloc[-3]
        swingbuy = (cur >= prev) and (prev < prev2)
        swingsell = (cur <= prev) and (prev > prev2)
        
        return t_up, swingbuy, swingsell
    except Exception: return None, None, None

STOCH_LEN = 14
STOCH_K = 7  # BACKTEST_CHANGE_101: marathon winner PF 2.81 Sharpe 5.04 for stocks (was 3)
STOCH_D = 7  # BACKTEST_CHANGE_101: slower smoothing (was 5)

def stoch_result(series: pd.Series) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], bool, bool]:
    def fallback(window: pd.Series) -> Tuple[float, float, float, float, bool, bool]:
        if window.empty:
            return 50.0, 50.0, 50.0, 50.0, False, False
        max_val = float(window.max())
        min_val = float(window.min())
        if max_val == min_val:
            k_curr = d_curr = 50.0
            k_prev = d_prev = 50.0
        else:
            k_curr = float((window.iloc[-1] - min_val) / (max_val - min_val) * 100.0)
            prev_idx = -2 if len(window) > 1 else -1
            prev_val = window.iloc[prev_idx]
            k_prev = float((prev_val - min_val) / (max_val - min_val) * 100.0)
            d_curr = k_curr
            d_prev = k_prev
        crossover = k_prev <= d_prev and k_curr > d_curr
        crossunder = k_prev >= d_prev and k_curr < d_curr
        return k_curr, d_curr, k_prev, d_prev, crossover, crossunder
    length = STOCH_LEN
    if len(series) >= length: 
        stoch = stoch_rsi(series, length=length, k=STOCH_K, d=STOCH_D)
        if stoch is not None and not stoch.empty:
            k = stoch.iloc[:, 0].clip(lower=0, upper=100)
            d = stoch.iloc[:, 1].clip(lower=0, upper=100)
            
            if not k.empty and pd.notna(k.iloc[-1]):
                k_curr = float(k.iloc[-1])
                d_curr = float(d.iloc[-1]) if not d.empty and pd.notna(d.iloc[-1]) else k_curr
                
                if len(k) > 1 and pd.notna(k.iloc[-2]):
                    k_prev = float(k.iloc[-2])
                else:
                    k_prev = k_curr
                    
                if len(d) > 1 and pd.notna(d.iloc[-2]):
                    d_prev = float(d.iloc[-2])
                else:
                    d_prev = d_curr

                crossover = k_prev <= d_prev and k_curr > d_curr
                crossunder = k_prev >= d_prev and k_curr < d_curr
                
                return k_curr, d_curr, k_prev, d_prev, crossover, crossunder
    window = series.iloc[-min(len(series), max(2, STOCH_LEN)) :]
    return fallback(window)

def atr_values(df: pd.DataFrame, short_len: int, long_len: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(df) < short_len:
        return None, None, None
    atr_short = atr_series(df, short_len)
    atr_long = atr_series(df, long_len) if len(df) >= long_len else None
    if atr_short is None or atr_short.empty:
        return None, None, None
    curr = atr_short.iloc[-1]
    prev = atr_short.iloc[-2] if len(atr_short) > 1 else curr
    long_val = atr_long.iloc[-1] if atr_long is not None and not atr_long.empty else None
    return (float(curr) if pd.notna(curr) else None, float(prev) if pd.notna(prev) else None, float(long_val) if long_val is not None and pd.notna(long_val) else None)

def mfi_value(df: pd.DataFrame) -> Optional[float]:
    if len(df) < 15:
        return None
    data = df.loc[:,["high", "low", "close", "volume"]].copy().apply(pd.to_numeric, errors="coerce").astype(np.float64)
    data = data.dropna()
    if len(data) < 15:
        return None
    typical_price = (data["high"] + data["low"] + data["close"]) / 3.0
    money_flow = typical_price * data["volume"]
    price_delta = typical_price.diff()
    positive_flow = money_flow.where(price_delta > 0.0, 0.0)
    negative_flow = money_flow.where(price_delta < 0.0, 0.0)
    positive_sum = positive_flow.rolling(14, min_periods=14).sum()
    negative_sum = negative_flow.rolling(14, min_periods=14).sum()
    if positive_sum.empty or negative_sum.empty:
        return None
    pos = positive_sum.iloc[-1]
    neg = negative_sum.iloc[-1]
    if pd.isna(pos) or pd.isna(neg):
        return None
    if pos == 0.0 and neg == 0.0:
        return 50.0
    if neg == 0.0:
        return 100.0
    ratio = pos / neg
    mfi = 100.0 - (100.0 / (1.0 + ratio))
    return float(mfi)

def ema_pair(series: pd.Series, length: int) -> Tuple[Optional[float], Optional[float]]:
    if len(series) < length:
        return None, None
    ema_series = series.ewm(span=length, adjust=False).mean()
    curr = ema_series.iloc[-1]
    prev = ema_series.iloc[-2] if len(ema_series) > 1 else curr
    return (float(curr) if pd.notna(curr) else None, float(prev) if pd.notna(prev) else None)

def ema_std(series: pd.Series, ema_length: int, std_window: int = 20) -> Optional[float]:
    if len(series) < max(ema_length, std_window):
        return None
    ema_series = series.ewm(span=ema_length, adjust=False).mean()
    deviation = (series - ema_series).abs()
    std_series = deviation.rolling(window=std_window, min_periods=std_window).std()
    if std_series.empty or pd.isna(std_series.iloc[-1]):
        return None
    return float(std_series.iloc[-1])

def sma_pair(series: pd.Series, length: int) -> Tuple[Optional[float], Optional[float]]:
    if len(series) < length:
        return None, None
    sma_series = series.rolling(length, min_periods=length).mean()
    if sma_series.empty:
        return None, None
    curr = sma_series.iloc[-1]
    prev = sma_series.iloc[-2] if len(sma_series) > 1 else curr
    return (float(curr) if pd.notna(curr) else None, float(prev) if pd.notna(prev) else None)

def relative_volume(df: pd.DataFrame, length: int) -> Optional[float]:
    if len(df) < length + 1:
        return None
    volumes = df["volume"].astype(float)
    numerator = volumes.iloc[-2]
    denominator = volumes.iloc[-(length+1):-1].mean()
    if pd.isna(denominator) or denominator == 0:
        return None
    return float(numerator / denominator)

def rsi_value(series: pd.Series, length: int) -> Optional[float]:
    if len(series) < length:
        return None
    rsi_series_obj = rsi_series(series, length)
    if rsi_series_obj is None or rsi_series_obj.empty:
        return None
    value = rsi_series_obj.iloc[-1]
    return float(value) if pd.notna(value) else None

def crossover_flags(price: float, price_prev: float, ref: Optional[float], ref_prev: Optional[float]) -> Tuple[bool, bool]:
    if ref is None or ref_prev is None:
        return False, False
    cross_over = price_prev <= ref_prev and price > ref
    cross_under = price_prev >= ref_prev and price < ref
    return cross_over, cross_under

TIMEFRAMES = {
    "1m":  {"sec": 60,   "half": 30, "dc_window": 20, "atr": 14, "ema":[20, 50, 200], "sma":[("sma_200_1m", 70)]},
    "3m":  {"sec": 180,  "half": 90, "dc_window": 20, "atr": 14, "ema":[20, 50, 200], "sma":[]},
    "5m":  {"sec": 300,  "half": 150, "dc_window": 20, "atr": 14, "ema":[20, 50, 200], "sma":[]},
    "15m": {"sec": 900,  "half": 450, "dc_window": 20, "atr": 14, "ema":[20, 50, 200], "sma":[]},
    "1h":  {"sec": 3600, "half": 1800, "dc_window": 20, "atr": 14, "ema":[20, 50, 200], "sma":[]},
    "4h":  {"sec": 14400,"half": 7200, "dc_window": 20, "atr": 14, "ema": [20, 50, 200], "sma":[]},
    "D":   {"sec": 86400,"half": 43200, "dc_window": 20, "atr": 14, "ema":[20, 50, 200], "sma":[]}
}

# Prepared direct routes must never consume the mark-adjusted dataframe used by
# legacy display/scoring fields.  This producer is deliberately separate from
# that path: it returns only the last candle whose *completion availability*
# is no later than the caller's as-of clock.  No order path reads these fields
# until a separately reviewed adapter is installed.
COMPLETED_SNAPSHOT_TIMEFRAMES = frozenset({"5m", "15m", "1h", "4h", "D"})
COMPLETED_SNAPSHOT_DEMAND_SWITCHES = (
    "ENTRY_STOCH_HHHL_DIRECT_ENABLED",
    "ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED",
    "ENTRY_STOCH_PARENT_DIRECT_ENABLED",
    "WT_DC_DIRECT_COMPLETED_ENABLED",
    "BB_RECOVERY_DIRECT_ENABLED",
    "LONG_WAIT_DIRECT_ENABLED",
    "BOTTOM_B_DELAYED_LOWER_TOP_ENABLED",
    "MTF_ATR_MULTITF_DIRECT_ENABLED",
)


def completed_snapshot_demanded(cfg: Any = None) -> bool:
    """Keep completed-feature calculation off the ordinary live hot path."""
    cfg = config if cfg is None else cfg
    return bool(
        getattr(cfg, "COMPLETED_CANDLE_SNAPSHOT_DIRECT_ENABLED", False)
        or any(bool(getattr(cfg, name, False)) for name in COMPLETED_SNAPSHOT_DEMAND_SWITCHES)
    )


@dataclass(frozen=True)
class CompletedCandleSnapshot:
    timeframe: str
    source_ts: int
    previous_source_ts: Optional[int]
    open: float
    high: float
    low: float
    close: float
    high_prev: Optional[float]
    low_prev: Optional[float]
    stoch_k: Optional[float]
    stoch_d: Optional[float]
    stoch_k_prev: Optional[float]
    stoch_d_prev: Optional[float]
    wt1: Optional[float]
    wt2: Optional[float]
    wt1_prev: Optional[float]
    wt2_prev: Optional[float]
    wt_cross: str
    dc_high: Optional[float]
    dc_low: Optional[float]
    dc_high_prev: Optional[float]
    dc_low_prev: Optional[float]
    bb_upper: Optional[float]
    bb_lower: Optional[float]
    atr: Optional[float]

    def as_indicator_fields(self) -> Dict[str, Any]:
        """Flatten under an explicit completed namespace for future adapters."""
        tf = self.timeframe
        return {
            f"_completed_source_ts_{tf}": self.source_ts,
            f"_completed_source_ts_{tf}_prev": self.previous_source_ts,
            f"_completed_open_{tf}": self.open,
            f"_completed_high_{tf}": self.high,
            f"_completed_low_{tf}": self.low,
            f"_completed_close_{tf}": self.close,
            f"_completed_high_{tf}_prev": self.high_prev,
            f"_completed_low_{tf}_prev": self.low_prev,
            f"_completed_stoch_k_{tf}": self.stoch_k,
            f"_completed_stoch_d_{tf}": self.stoch_d,
            f"_completed_stoch_k_{tf}_prev": self.stoch_k_prev,
            f"_completed_stoch_d_{tf}_prev": self.stoch_d_prev,
            f"_completed_wt1_{tf}": self.wt1,
            f"_completed_wt2_{tf}": self.wt2,
            f"_completed_wt1_{tf}_prev": self.wt1_prev,
            f"_completed_wt2_{tf}_prev": self.wt2_prev,
            f"_completed_wt_cross_{tf}": self.wt_cross,
            f"_completed_dc_high_{tf}": self.dc_high,
            f"_completed_dc_low_{tf}": self.dc_low,
            f"_completed_dc_high_{tf}_prev": self.dc_high_prev,
            f"_completed_dc_low_{tf}_prev": self.dc_low_prev,
            f"_completed_bb_upper_{tf}": self.bb_upper,
            f"_completed_bb_lower_{tf}": self.bb_lower,
            f"_completed_atr_{tf}": self.atr,
        }


def _snapshot_asof(asof_ts: Any) -> pd.Timestamp:
    value = pd.to_datetime(asof_ts, utc=True, errors="coerce")
    if pd.isna(value):
        raise ValueError("asof_ts must be a valid UTC timestamp")
    return pd.Timestamp(value)


def _snapshot_available_at(label: Any, timeframe: str) -> pd.Timestamp:
    label_ts = pd.Timestamp(pd.to_datetime(label, utc=True, errors="raise"))
    if timeframe in {"1h", "4h", "D"}:
        return pd.Timestamp(_ordinary_parent_available_at(label_ts.to_pydatetime(), timeframe))
    return label_ts + pd.Timedelta(seconds=TIMEFRAMES[timeframe]["sec"])


def completed_candle_frame(
    df: pd.DataFrame, timeframe: str, asof_ts: Any
) -> pd.DataFrame:
    """Return a copied raw frame truncated before its current/in-progress bar.

    The input is never mutated and no mark price is accepted.  Callers that
    have a live mark must pass their original kline frame, not the result of
    :func:`with_mark_price`; ``IndicatorCalculator`` does this explicitly.
    """
    tf = str(timeframe)
    if tf not in COMPLETED_SNAPSHOT_TIMEFRAMES:
        raise ValueError(f"unsupported completed snapshot timeframe: {tf}")
    if df is None or df.empty:
        return pd.DataFrame()
    timestamp_col = next((name for name in ("timestamp_dt", "close_time", "timestamp", "time") if name in df.columns), None)
    if timestamp_col is None or any(name not in df.columns for name in ("open", "high", "low", "close")):
        return pd.DataFrame()
    raw = df.copy(deep=True)
    raw["_completed_snapshot_label"] = _to_utc(raw[timestamp_col])
    for name in ("open", "high", "low", "close"):
        raw[name] = pd.to_numeric(raw[name], errors="coerce")
    raw = raw.dropna(subset=["_completed_snapshot_label", "open", "high", "low", "close"])
    if raw.empty:
        return raw
    raw = raw.sort_values("_completed_snapshot_label").drop_duplicates(
        subset=["_completed_snapshot_label"], keep="last"
    )
    asof = _snapshot_asof(asof_ts)
    raw["_completed_snapshot_available"] = raw["_completed_snapshot_label"].map(
        lambda label: _snapshot_available_at(label, tf)
    )
    return raw.loc[raw["_completed_snapshot_available"] <= asof].reset_index(drop=True)


def produce_completed_candle_snapshot(
    df: pd.DataFrame, timeframe: str, asof_ts: Any
) -> Optional[CompletedCandleSnapshot]:
    """Produce one completed-only feature snapshot for a prepared direct route."""
    tf = str(timeframe)
    completed = completed_candle_frame(df, tf, asof_ts)
    if completed.empty:
        return None
    source_ts = int(pd.Timestamp(completed["_completed_snapshot_available"].iloc[-1]).timestamp())
    previous_source_ts = (
        int(pd.Timestamp(completed["_completed_snapshot_available"].iloc[-2]).timestamp())
        if len(completed) > 1 else None
    )
    close = completed["close"].astype(float)
    high = completed["high"].astype(float)
    low = completed["low"].astype(float)
    k, d, k_prev, d_prev, _, _ = stoch_result(close)
    wt1, wt2 = wavetrend(completed, timeframe=tf)
    wt1_value = float(wt1.iloc[-1]) if wt1 is not None and pd.notna(wt1.iloc[-1]) else None
    wt2_value = float(wt2.iloc[-1]) if wt2 is not None and pd.notna(wt2.iloc[-1]) else None
    wt1_prev = float(wt1.iloc[-2]) if wt1 is not None and len(wt1) > 1 and pd.notna(wt1.iloc[-2]) else None
    wt2_prev = float(wt2.iloc[-2]) if wt2 is not None and len(wt2) > 1 and pd.notna(wt2.iloc[-2]) else None
    if wt1_prev is not None and wt2_prev is not None and wt1_value is not None and wt2_value is not None:
        wt_cross = "BULL" if wt1_prev <= wt2_prev and wt1_value > wt2_value else (
            "BEAR" if wt1_prev >= wt2_prev and wt1_value < wt2_value else "NONE"
        )
    else:
        wt_cross = "NONE"
    dc_high, dc_low, _ = donchian(high, low, TIMEFRAMES[tf]["dc_window"])
    dc_high_prev, dc_low_prev, _ = donchian_prev(
        high, low, TIMEFRAMES[tf]["dc_window"]
    )
    bb_upper, bb_lower, _ = bb_features(close)
    atr, _, _ = atr_values(completed, TIMEFRAMES[tf]["atr"], ATR_LONG_LENGTH)
    return CompletedCandleSnapshot(
        timeframe=tf,
        source_ts=source_ts,
        previous_source_ts=previous_source_ts,
        open=float(completed["open"].iloc[-1]),
        high=float(high.iloc[-1]),
        low=float(low.iloc[-1]),
        close=float(close.iloc[-1]),
        high_prev=(float(high.iloc[-2]) if len(high) > 1 else None),
        low_prev=(float(low.iloc[-2]) if len(low) > 1 else None),
        stoch_k=k,
        stoch_d=d,
        stoch_k_prev=k_prev,
        stoch_d_prev=d_prev,
        wt1=wt1_value,
        wt2=wt2_value,
        wt1_prev=wt1_prev,
        wt2_prev=wt2_prev,
        wt_cross=wt_cross,
        dc_high=dc_high,
        dc_low=dc_low,
        dc_high_prev=dc_high_prev,
        dc_low_prev=dc_low_prev,
        bb_upper=bb_upper,
        bb_lower=bb_lower,
        atr=atr,
    )


def produce_completed_candle_snapshots(
    frames: Dict[str, pd.DataFrame], asof_ts: Any
) -> Dict[str, CompletedCandleSnapshot]:
    """Build snapshots for 5m/15m/1h/4h/D without mixing availability clocks."""
    return {
        tf: snapshot
        for tf, frame in (frames or {}).items()
        if str(tf) in COMPLETED_SNAPSHOT_TIMEFRAMES
        for snapshot in [produce_completed_candle_snapshot(frame, str(tf), asof_ts)]
        if snapshot is not None
    }

ATR_LONG_LENGTH = 100
REL_VOL_LENGTH = 20
LINREG_LENGTH = 50

def detect_bar_patterns(df: pd.DataFrame, timeframe: str) -> Dict[str, Any]:
    """Detect candlestick patterns, multi-bar structure, vol regime, streak on HTF."""
    result: Dict[str, Any] = {}
    if df is None or len(df) < 10:
        return result
    o = df["open"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)
    v = df["volume"].astype(float)
    n = len(df)
    def _b(i): return float(o.iloc[i]), float(h.iloc[i]), float(l.iloc[i]), float(c.iloc[i]), float(v.iloc[i])
    o1, h1, l1, c1, v1 = _b(-1)
    o2, h2, l2, c2, v2 = _b(-2)
    o3, h3, l3, c3, v3 = _b(-3)
    o4, h4, l4, c4, v4 = _b(-4)
    o5, h5, l5, c5, v5 = _b(-5)
    body1, body2, body3, body4 = abs(c1 - o1), abs(c2 - o2), abs(c3 - o3), abs(c4 - o4)
    range1 = max(h1 - l1, 1e-10)
    range2 = max(h2 - l2, 1e-10)
    range3 = max(h3 - l3, 1e-10)
    upper_wick1 = h1 - max(o1, c1)
    lower_wick1 = min(o1, c1) - l1
    body_ratio1 = body1 / range1
    is_bull1, is_bear1 = c1 > o1, c1 < o1
    is_bull2, is_bear2 = c2 > o2, c2 < o2
    is_bull3, is_bear3 = c3 > o3, c3 < o3
    vol_window = min(20, n - 1)
    vol_avg = float(v.iloc[-vol_window - 1:-1].mean()) if vol_window > 0 else v1
    vol_ratio = v1 / vol_avg if vol_avg > 0 else 1.0
    vol_confirm = vol_ratio >= 1.3
    vol_spike = vol_ratio >= 2.0
    vol_dry = vol_ratio < 0.6
    vol_expanding = all(float(v.iloc[-i]) > float(v.iloc[-i - 1]) for i in range(1, min(4, n)))
    atr_arr = (h - l).astype(float)
    atr_curr = float(atr_arr.iloc[-1])
    atr_window = min(50, n)
    atr_hist = atr_arr.iloc[-atr_window:].sort_values()
    atr_rank = float((atr_hist < atr_curr).sum()) / max(len(atr_hist), 1)
    vol_regime = "low" if atr_rank < 0.25 else ("high" if atr_rank > 0.75 else "normal")
    streak = 0
    for i in range(1, min(8, n)):
        ci_v = float(c.iloc[-i])
        oi_v = float(o.iloc[-i])
        if ci_v > oi_v:
            if streak >= 0: streak += 1
            else: break
        elif ci_v < oi_v:
            if streak <= 0: streak -= 1
            else: break
        else:
            break
    hh = h1 > h2 and h2 > h3
    hl = l1 > l2 and l2 > l3
    ll = l1 < l2 and l2 < l3
    lh = h1 < h2 and h2 < h3
    swing_bull = hh and hl
    swing_bear = ll and lh
    ranges_5 = [max(float(h.iloc[-i]) - float(l.iloc[-i]), 1e-10) for i in range(1, min(6, n))]
    compression = all(ranges_5[i] <= ranges_5[i + 1] for i in range(len(ranges_5) - 1)) if len(ranges_5) >= 3 else False
    compression_ratio = ranges_5[0] / ranges_5[-1] if len(ranges_5) >= 3 and ranges_5[-1] > 0 else 1.0
    inside_count = 0
    for i in range(1, min(5, n - 1)):
        if float(h.iloc[-i]) < float(h.iloc[-i - 1]) and float(l.iloc[-i]) > float(l.iloc[-i - 1]):
            inside_count += 1
        else:
            break
    pattern = "none"
    direction = 0
    strength = 0.0
    if is_bear3 and body3 > range3 * 0.5 and body2 < range2 * 0.3 and is_bull1 and body1 > range1 * 0.5 and c1 > (o3 + c3) / 2:
        pattern = "morning_star"; direction = 1; strength = min(1.0, (body1 + body3) / (2 * range1 + 1e-10))
    elif is_bull3 and body3 > range3 * 0.5 and body2 < range2 * 0.3 and is_bear1 and body1 > range1 * 0.5 and c1 < (o3 + c3) / 2:
        pattern = "evening_star"; direction = -1; strength = min(1.0, (body1 + body3) / (2 * range1 + 1e-10))
    elif is_bull1 and is_bull2 and is_bull3 and c1 > c2 > c3 and body1 > range1 * 0.5 and body2 > range2 * 0.5 and body3 > range3 * 0.5:
        pattern = "three_white_soldiers"; direction = 1; strength = min(1.0, min(body1, body2, body3) / max(range1, range2, range3))
    elif is_bear1 and is_bear2 and is_bear3 and c1 < c2 < c3 and body1 > range1 * 0.5 and body2 > range2 * 0.5 and body3 > range3 * 0.5:
        pattern = "three_black_crows"; direction = -1; strength = min(1.0, min(body1, body2, body3) / max(range1, range2, range3))
    elif is_bull1 and is_bear2 and c1 > o2 and o1 < c2 and body1 > body2:
        pattern = "bull_engulfing"; direction = 1; strength = min(1.0, (body1 / (body2 + 1e-10)) * 0.5)
    elif is_bear1 and is_bull2 and c1 < o2 and o1 > c2 and body1 > body2:
        pattern = "bear_engulfing"; direction = -1; strength = min(1.0, (body1 / (body2 + 1e-10)) * 0.5)
    elif is_bull1 and abs(l1 - l2) < range1 * 0.05 and l1 < min(l3, l4):
        pattern = "tweezer_bottom"; direction = 1; strength = min(1.0, 1.0 - abs(l1 - l2) / range1)
    elif is_bear1 and abs(h1 - h2) < range1 * 0.05 and h1 > max(h3, h4):
        pattern = "tweezer_top"; direction = -1; strength = min(1.0, 1.0 - abs(h1 - h2) / range1)
    elif body_ratio1 < 0.35 and lower_wick1 > body1 * 2.0 and upper_wick1 < body1 * 0.5:
        pattern = "hammer"; direction = 1; strength = min(1.0, lower_wick1 / range1)
    elif body_ratio1 < 0.35 and upper_wick1 > body1 * 2.0 and lower_wick1 < body1 * 0.5:
        pattern = "shooting_star"; direction = -1; strength = min(1.0, upper_wick1 / range1)
    elif is_bull1 and is_bear2 and body1 < body2 * 0.5 and h1 < h2 and l1 > l2:
        pattern = "bull_harami"; direction = 1; strength = 0.5 * (1.0 - body1 / (body2 + 1e-10))
    elif is_bear1 and is_bull2 and body1 < body2 * 0.5 and h1 < h2 and l1 > l2:
        pattern = "bear_harami"; direction = -1; strength = 0.5 * (1.0 - body1 / (body2 + 1e-10))
    elif inside_count >= 2:
        pattern = "multi_inside"; direction = 0; strength = min(1.0, inside_count * 0.3)
    elif h1 < h2 and l1 > l2:
        pattern = "inside_bar"; direction = 0; strength = 1.0 - (range1 / range2)
    elif h1 > h2 and l1 < l2 and body_ratio1 > 0.6:
        pattern = "outside_bar"; direction = 1 if is_bull1 else -1; strength = body_ratio1
    elif lower_wick1 > range1 * 0.6 and body_ratio1 < 0.25:
        pattern = "pin_bar_bull"; direction = 1; strength = lower_wick1 / range1
    elif upper_wick1 > range1 * 0.6 and body_ratio1 < 0.25:
        pattern = "pin_bar_bear"; direction = -1; strength = upper_wick1 / range1
    elif is_bull1 and is_bear2 and is_bear3 and c1 > h2:
        pattern = "three_bar_bull"; direction = 1; strength = min(1.0, body1 / (body2 + body3 + 1e-10))
    elif is_bear1 and is_bull2 and is_bull3 and c1 < l2:
        pattern = "three_bar_bear"; direction = -1; strength = min(1.0, body1 / (body2 + body3 + 1e-10))
    elif body_ratio1 < 0.1:
        pattern = "doji"; direction = 0; strength = 0.3 + (0.4 if vol_confirm else 0.0)
    if vol_confirm and direction != 0: strength = min(1.0, strength * 1.3)
    if vol_spike and direction != 0: strength = min(1.0, strength * 1.2)
    if vol_dry and direction != 0: strength *= 0.6
    result[f"bar_pattern_{timeframe}"] = pattern
    result[f"bar_direction_{timeframe}"] = direction
    result[f"bar_strength_{timeframe}"] = round(strength, 3)
    result[f"bar_vol_confirm_{timeframe}"] = vol_confirm
    result[f"bar_vol_ratio_{timeframe}"] = round(vol_ratio, 2)
    result[f"bar_body_ratio_{timeframe}"] = round(body_ratio1, 3)
    result[f"bar_upper_wick_{timeframe}"] = round(upper_wick1 / range1, 3) if range1 > 0 else 0.0
    result[f"bar_lower_wick_{timeframe}"] = round(lower_wick1 / range1, 3) if range1 > 0 else 0.0
    result[f"bar_streak_{timeframe}"] = streak
    result[f"bar_swing_bull_{timeframe}"] = swing_bull
    result[f"bar_swing_bear_{timeframe}"] = swing_bear
    result[f"bar_compression_{timeframe}"] = compression
    result[f"bar_compression_ratio_{timeframe}"] = round(compression_ratio, 3)
    result[f"bar_inside_count_{timeframe}"] = inside_count
    result[f"bar_vol_spike_{timeframe}"] = vol_spike
    result[f"bar_vol_expanding_{timeframe}"] = vol_expanding
    result[f"bar_vol_regime_{timeframe}"] = vol_regime
    result[f"bar_atr_rank_{timeframe}"] = round(atr_rank, 3)
    return result

class IndicatorCalculator:
    def compute(self, df: pd.DataFrame, symbol, timeframe: str, mark_price: Optional[float], mark_ts: Optional[datetime], mid_run: bool) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        if df is None or df.empty:
            return result
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in ["timestamp", "close_time", "time"]:
            if col in df.columns:
                df[col] = df[col].astype(object)
                
        if "timestamp_dt" not in df.columns:
            if "timestamp" in df.columns:
                df["timestamp_dt"] = _to_utc(df["timestamp"])
            elif "close_time" in df.columns:
                df["timestamp_dt"] = _to_utc(df["close_time"])
            elif "time" in df.columns:
                df["timestamp_dt"] = _to_utc(df["time"])
            else:
                return result
        else:
            if not pd.api.types.is_datetime64_any_dtype(df["timestamp_dt"]):
                df["timestamp_dt"] = _to_utc(df["timestamp_dt"])
        
        df = df.dropna(subset=["timestamp_dt"])
        if df.empty: return result
        last_val = df["timestamp_dt"].iloc[-1]
        try:
            last_bar_dt = pd.to_datetime(last_val, utc=True).to_pydatetime()
        except Exception:
            last_bar_dt = datetime.now(timezone.utc)
        if getattr(last_bar_dt, 'tzinfo', None) is None: 
            last_bar_dt = last_bar_dt.replace(tzinfo=timezone.utc)

        best_price = float(df["close"].iloc[-1])
        final_timestamp = last_bar_dt
        use_mark = False

        # --- 2. STRICT VALIDATION OF EXTERNAL PRICE ---
        if mark_price is not None and float(mark_price) > 0 and mark_ts is not None:
            try:
                parsed_ts = pd.to_datetime(mark_ts, utc=True).to_pydatetime()
                if getattr(parsed_ts, 'tzinfo', None) is None:
                    parsed_ts = parsed_ts.replace(tzinfo=timezone.utc)
                
                now_utc = datetime.now(timezone.utc)
                age_seconds = (now_utc - parsed_ts).total_seconds()
                
                if age_seconds < 120:
                    use_mark = True
                    best_price = float(mark_price)
                    final_timestamp = parsed_ts
                # else: mark_price, mark_ts = price_cacheman.get_price(symbol)
            except Exception:
                pass 
        # Preserve an immutable raw copy before legacy mark adjustment mutates
        # its dataframe in place.  Completed snapshots below must never see
        # that adjusted current row.
        raw_snapshot_df = df.copy(deep=True)
        if use_mark:
            adjusted_df = with_mark_price(df, best_price, final_timestamp)
        else:
            adjusted_df = df

        # Published only for future prepared direct adapters.  This is built
        # from ``df`` before the optional mark adjustment above, so its OHLC,
        # stochastic, WT, prior-Donchian, BB and ATR inputs are never derived
        # from a current/in-progress candle or an external mark.
        if completed_snapshot_demanded(config):
            try:
                completed_snapshot = produce_completed_candle_snapshot(
                    raw_snapshot_df, timeframe, mark_ts or utc_now()
                )
                if completed_snapshot is not None:
                    result.update(completed_snapshot.as_indicator_fields())
            except (TypeError, ValueError):
                # Absence is intentional fail-closed behavior; no action path
                # may substitute generic/current indicators for this snapshot.
                pass

        # Shared ordinary ladder inputs are calculated from the last completed
        # parent dataframe, never from the optional live mark appended above.
        # The ordinary adapter prefers these fields over generic indicators.
        if timeframe in ("D", "4h", "1h"):
            try:
                _completed_df = df
                _asof = pd.Timestamp(mark_ts or utc_now())
                if _asof.tzinfo is None:
                    _asof = _asof.tz_localize(timezone.utc)
                else:
                    _asof = _asof.tz_convert(timezone.utc)
                _last_available = _ordinary_parent_available_at(
                    last_bar_dt, timeframe
                )
                if pd.Timestamp(_last_available) > _asof:
                    _completed_df = df.iloc[:-1]
                if len(_completed_df) < 2:
                    raise ValueError("not enough completed parents")
                _completed_label = pd.to_datetime(
                    _completed_df["timestamp_dt"].iloc[-1], utc=True
                ).to_pydatetime()
                _completed_available = _ordinary_parent_available_at(
                    _completed_label, timeframe
                )
                _completed_close = _completed_df["close"].astype(float)
                _completed_high = _completed_df["high"].astype(float)
                _completed_low = _completed_df["low"].astype(float)
                result[f"_completed_source_ts_{timeframe}"] = int(
                    _completed_available.timestamp()
                )
                result[f"_ladder_high_{timeframe}"] = float(
                    _completed_high.iloc[-1]
                )
                result[f"_ladder_low_{timeframe}"] = float(
                    _completed_low.iloc[-1]
                )
                result[f"_ladder_high_{timeframe}_prev"] = float(
                    _completed_high.iloc[-2]
                )
                result[f"_ladder_low_{timeframe}_prev"] = float(
                    _completed_low.iloc[-2]
                )
                _completed_stoch = stoch_result(_completed_close)
                result[f"_ladder_stoch_k_{timeframe}"] = float(
                    _completed_stoch[0]
                )
                result[f"_ladder_stoch_k_{timeframe}_prev"] = float(
                    _completed_stoch[2]
                )
                _completed_wt1, _completed_wt2 = wavetrend(
                    _completed_df, timeframe
                )
                _completed_cross = "NONE"
                if (
                    _completed_wt1 is not None
                    and _completed_wt2 is not None
                    and len(_completed_wt1) >= 2
                ):
                    if (
                        _completed_wt1.iloc[-2] <= _completed_wt2.iloc[-2]
                        and _completed_wt1.iloc[-1] > _completed_wt2.iloc[-1]
                    ):
                        _completed_cross = "BULL"
                    elif (
                        _completed_wt1.iloc[-2] >= _completed_wt2.iloc[-2]
                        and _completed_wt1.iloc[-1] < _completed_wt2.iloc[-1]
                    ):
                        _completed_cross = "BEAR"
                result[f"_ladder_wt_cross_{timeframe}"] = _completed_cross
                _lr_length = (
                    getattr(config, "LR_CHANNEL_LONG_LENGTHS", None) or {}
                ).get(timeframe)
                if _lr_length and len(_completed_close) >= int(_lr_length):
                    _, _, _completed_pb = linreg_channel(
                        _completed_close,
                        int(_lr_length),
                        std_mult=2.5,
                    )
                    if _completed_pb is not None:
                        result[f"_ladder_lrL_pct_b_{timeframe}"] = float(
                            _completed_pb
                        )
                if timeframe == "4h" and len(_completed_high) >= 31:
                    result["e02_completed_close_4h"] = float(
                        _completed_close.iloc[-1]
                    )
                    result["e02_prior_high_4h_n30"] = float(
                        _completed_high.iloc[-31:-1].max()
                    )
                    result["e02_prior_low_4h_n30"] = float(
                        _completed_low.iloc[-31:-1].min()
                    )
            except Exception:
                # Generic completed indicators remain available as a fail-closed
                # fallback; the adapter will reject missing source identity.
                pass
        
        # --- 4. OUTPUT THE GUARANTEED BEST PRICE ---
        result["current_price"] = best_price
        result["mark_price"] = best_price

        # --- 5. OUTPUT THE VERIFIED TIMESTAMPS ---
        ts_iso = isoformat(final_timestamp)
        result[f"timestamp_{timeframe}"] = ts_iso
        result["timestamp"] = ts_iso
        
        # Always use current real time for mid timestamps to avoid staleness confusion
        real_now_iso = isoformat(utc_now())
        if mid_run:
            result[f"timestamp_{timeframe}_mid"] = real_now_iso

        # --- 6. EXTRACT SERIES FOR INDICATORS ---
        close_series = adjusted_df["close"].astype(float)
        high_series = adjusted_df["high"].astype(float)
        low_series = adjusted_df["low"].astype(float)
        open_series = adjusted_df["open"].astype(float)
        volume_series = adjusted_df["volume"].astype(float)
        
        current_price = result["current_price"]
        prev_price = float(close_series.iloc[-2]) if len(close_series) > 1 else current_price
        result["prev_price"] = prev_price
        result[f"high_{timeframe}"] = float(high_series.iloc[-1])
        result[f"low_{timeframe}"] = float(low_series.iloc[-1])
        result[f"high_{timeframe}_prev"] = float(high_series.iloc[-2]) if len(high_series) > 1 else float(high_series.iloc[-1])
        result[f"low_{timeframe}_prev"] = float(low_series.iloc[-2]) if len(low_series) > 1 else float(low_series.iloc[-1])
        result[f"close_{timeframe}_prev"] = float(close_series.iloc[-2]) if len(close_series) > 1 else current_price
        # [2026-07-03] DG_10 SHORT-BLOCK FIX: open_{tf}/close_{tf} were never emitted, so the
        # disaster-guard HTF bias (close_D vs open_D, close_4h vs open_4h) was structurally 0 and
        # blocked EVERY short since 2026-05-13. sma_200_15m likewise absent (only ema existed),
        # so the below-sma200 bypass could never fire.
        result[f"open_{timeframe}"] = float(open_series.iloc[-1]) if len(open_series) > 0 else current_price
        result[f"close_{timeframe}"] = float(close_series.iloc[-1]) if len(close_series) > 0 else current_price
        if (
            timeframe == "4h"
            and "e02_prior_high_4h_n30" not in result
            and len(high_series) >= 31
            and len(low_series) >= 31
        ):
            # E02/N30 uses the thirty parents strictly preceding the newly
            # completed 4h close.  Excluding iloc[-1] prevents the signal bar
            # from moving its own Donchian threshold.
            result["e02_prior_high_4h_n30"] = float(
                high_series.iloc[-31:-1].max()
            )
            result["e02_prior_low_4h_n30"] = float(
                low_series.iloc[-31:-1].min()
            )
        if timeframe == "15m" and len(close_series) >= 200:
            result["sma_200_15m"] = float(close_series.iloc[-200:].mean())

        tf_config = TIMEFRAMES.get(timeframe, {"sec": 60, "half": 30, "dc_window": 20, "atr": 14, "ema": [20, 50, 200], "sma":[]})
        dc_window = tf_config["dc_window"]
        dc_high, dc_low, dc_basis = donchian(high_series, low_series, dc_window)
        dc_high_prev, dc_low_prev, dc_basis_prev = donchian_prev(high_series, low_series, dc_window)
        
        if dc_high is not None:
            result[f"dc_high_{timeframe}"] = dc_high
            result[f"dc_low_{timeframe}"] = dc_low
            result[f"dc_basis_{timeframe}"] = dc_basis
        if dc_high_prev is not None:
            result[f"dc_high_{timeframe}_prev"] = dc_high_prev
            result[f"dc_low_{timeframe}_prev"] = dc_low_prev
            result[f"dc_basis_{timeframe}_prev"] = dc_basis_prev
            
        if len(high_series) >= dc_window + 30:
            high_hist = high_series.rolling(dc_window, min_periods=dc_window).max()
            low_hist = low_series.rolling(dc_window, min_periods=dc_window).min()
            idx = -30
            if len(high_hist) >= 30 and len(low_hist) >= 30:
                result[f"dc_high_{timeframe}_ant"] = float(high_hist.iloc[idx])
                result[f"dc_low_{timeframe}_ant"] = float(low_hist.iloc[idx])
                result[f"dc_basis_{timeframe}_ant"] = (result[f"dc_high_{timeframe}_ant"] + result[f"dc_low_{timeframe}_ant"]) / 2.0
                
        dch4, dcl4, _ = donchian(high_series, low_series, 4)
        if dch4 is not None:
            result[f"dc_high4_{timeframe}"] = dch4
            result[f"dc_low4_{timeframe}"] = dcl4
            
        if dc_basis is not None and dc_basis_prev is not None:
            cross_up, cross_down = crossover_flags(current_price, prev_price, dc_basis, dc_basis_prev)
            result[f"dc_basis_crossover_{timeframe}"] = cross_up
            result[f"dc_basis_crossunder_{timeframe}"] = cross_down
            
        if dc_high is not None and dc_high_prev is not None:
            cross_up, cross_down = crossover_flags(current_price, prev_price, dc_high, dc_high_prev)
            result[f"dc_high_crossover_{timeframe}"] = cross_up
            result[f"dc_high_crossunder_{timeframe}"] = cross_down
            
        if dc_low is not None and dc_low_prev is not None:
            cross_up, cross_down = crossover_flags(current_price, prev_price, dc_low, dc_low_prev)
            result[f"dc_low_crossover_{timeframe}"] = cross_up
            result[f"dc_low_crossunder_{timeframe}"] = cross_down
            
        k_curr, d_curr, k_prev, d_prev, stoch_cross, stoch_cross_under = stoch_result(close_series)
        result[f"stoch_crossover_{timeframe}"] = stoch_cross if k_curr is not None else False
        result[f"stoch_crossunder_{timeframe}"] = stoch_cross_under if k_curr is not None else False
        if k_curr is not None:
            result[f"k_{timeframe}"] = k_curr
            result[f"d_{timeframe}"] = d_curr if d_curr is not None else k_curr
            result[f"k_{timeframe}_prev"] = k_prev if k_prev is not None else k_curr
            result[f"d_{timeframe}_prev"] = d_prev if d_prev is not None else d_curr
            
        wt1, wt2 = wavetrend(adjusted_df, timeframe=timeframe)
        if wt1 is not None and wt2 is not None and not wt1.empty and not wt2.empty:
            result[f"wt1_{timeframe}"] = float(wt1.iloc[-1]) if pd.notna(wt1.iloc[-1]) else 0
            result[f"wt2_{timeframe}"] = float(wt2.iloc[-1]) if pd.notna(wt2.iloc[-1]) else 0
            result[f"wt_score_{timeframe}"] = result[f"wt1_{timeframe}"] - result[f"wt2_{timeframe}"]
            prev_wt1 = float(wt1.iloc[-2]) if len(wt1) > 1 and pd.notna(wt1.iloc[-2]) else result[f"wt1_{timeframe}"]
            prev_wt2 = float(wt2.iloc[-2]) if len(wt2) > 1 and pd.notna(wt2.iloc[-2]) else result[f"wt2_{timeframe}"]
            if prev_wt1 <= prev_wt2 and result[f"wt1_{timeframe}"] > result[f"wt2_{timeframe}"] and result[f"wt1_{timeframe}"] < -50: result[f"wt_signal_{timeframe}"] = "BUY"
            elif prev_wt1 >= prev_wt2 and result[f"wt1_{timeframe}"] < result[f"wt2_{timeframe}"] and result[f"wt1_{timeframe}"] > 50: result[f"wt_signal_{timeframe}"] = "SELL"
            else: result[f"wt_signal_{timeframe}"] = "NEUTRAL"
            try:
                wt_intel = wavetrend_intelligence(wt1, wt2, close_series, _ensure_float_series(adjusted_df["high"]), _ensure_float_series(adjusted_df["low"]), timeframe)
                for k, v in wt_intel.items():
                    if k not in result: result[k] = v
            except Exception:
                pass
        else:
            result[f"wt_signal_{timeframe}"] = "NEUTRAL"
            
        ha_color, ha_prev = heikin_ashi(adjusted_df)
        result[f"ha_{timeframe}"] = ha_color
        if ha_prev is not None:
            result[f"ha_{timeframe}_prev"] = ha_prev
            
        atr_curr, atr_prev, atr_long = atr_values(adjusted_df, tf_config["atr"], ATR_LONG_LENGTH)
        if atr_curr is not None:
            result[f"atr_{timeframe}"] = atr_curr
        if atr_prev is not None:
            result[f"atr_{timeframe}_prev"] = atr_prev
        if atr_long is not None:
            result[f"atr_long_{timeframe}"] = atr_long
            
        rel_vol = relative_volume(adjusted_df, REL_VOL_LENGTH)
        if rel_vol is not None:
            result[f"relative_volume_{timeframe}"] = rel_vol
            
        mfi_val = mfi_value(adjusted_df)
        if mfi_val is not None:
            result[f"mfi_{timeframe}"] = mfi_val
            
        rsi_val = rsi_value(close_series, 14)
        if rsi_val is not None:
            result[f"rsi_{timeframe}"] = rsi_val
        # ADX + BB width + DC width — needed for market quality scoring (BACKTEST_CHANGE_146)
        if timeframe in ("1h", "4h", "D"):
            _adx_val = adx_value(adjusted_df, 14)
            if _adx_val is not None:
                result[f"adx_{timeframe}"] = _adx_val
            _bb_u = result.get(f"bb_upper_{timeframe}")
            _bb_l = result.get(f"bb_lower_{timeframe}")
            if _bb_u and _bb_l:
                _bb_mid = (_bb_u + _bb_l) / 2.0 if (_bb_u + _bb_l) > 0 else 0
                result[f"bb_width_{timeframe}"] = round((_bb_u - _bb_l) / _bb_mid * 100.0, 3) if _bb_mid > 0 else 0.0
            _dc_h = result.get(f"dc_high_{timeframe}")
            _dc_l = result.get(f"dc_low_{timeframe}")
            if _dc_h and _dc_l and _dc_l > 0:
                result[f"dc_width_{timeframe}"] = round((_dc_h - _dc_l) / _dc_l * 100, 4)
                _dc_range = _dc_h - _dc_l
                if _dc_range > 0:
                    result[f"dc_position_{timeframe}"] = round(max(0.0, min(1.0, (current_price - _dc_l) / _dc_range)), 4)
            
        for ema_length in tf_config["ema"]:
            ema_curr, ema_prev = ema_pair(close_series, ema_length)
            if ema_curr is not None:
                result[f"ema_{ema_length}_{timeframe}"] = ema_curr
            if ema_prev is not None:
                result[f"ema_{ema_length}_{timeframe}_prev"] = ema_prev
            if timeframe == "1m" and ema_length == 20:
                ema_std_val = ema_std(close_series, ema_length, std_window=20)
                if ema_std_val is not None:
                    result[f"ema_{ema_length}_std_{timeframe}"] = ema_std_val
                    
        for field_name, length in tf_config["sma"]:
            sma_curr, sma_prev = sma_pair(close_series, length)
            if sma_curr is not None:
                result[field_name] = sma_curr
            if sma_prev is not None:
                result[f"{field_name}_prev"] = sma_prev
            if sma_curr is not None and sma_prev is not None:
                cross_over, cross_under = crossover_flags(current_price, prev_price, sma_curr, sma_prev)
                suffix = field_name.split("_")[-1]
                result[f"sma_crossover_{suffix}"] = cross_over
                result[f"sma_crossunder_{suffix}"] = cross_under
                
        slope, linearity = linreg_features(close_series, LINREG_LENGTH)
        if slope is not None:
            result[f"lr_trend_{timeframe}"] = slope
        # 2026-04-29: write linearity + slope_close for ALL timeframes (was 1h-only).
        # Per user: linearity_4h plus all lr numbers' signs is the new entry filter.
        if linearity is not None:
            result[f"linearity_{timeframe}"] = linearity
            result[f"slope_close_{timeframe}"] = slope
        if timeframe in ("1h", "4h", "D"):
            _bb_u, _bb_l, _bb_pb = bb_features(close_series, length=20, std_mult=2.0)
            if _bb_pb is not None:
                result[f"bb_upper_{timeframe}"] = _bb_u
                result[f"bb_lower_{timeframe}"] = _bb_l
                result[f"bb_pct_b_{timeframe}"] = _bb_pb
            _lr_u, _lr_l, _lr_pb = linreg_channel(close_series, LINREG_LENGTH, std_mult=2.5)
            if _lr_pb is not None:
                result[f"lr_upper_{timeframe}"] = _lr_u
                result[f"lr_lower_{timeframe}"] = _lr_l
                result[f"lr_pct_b_{timeframe}"] = _lr_pb
            _lrL_len = (getattr(config, "LR_CHANNEL_LONG_LENGTHS", None) or {}).get(timeframe)
            if _lrL_len and len(close_series) >= int(_lrL_len):
                _lrL_u, _lrL_l, _lrL_pb = linreg_channel(close_series, int(_lrL_len), std_mult=2.5)
                _lrL_slope, _lrL_lin = linreg_features(close_series, int(_lrL_len))
                _lrL_px = float(close_series.iloc[-1])
                if _lrL_pb is not None and _lrL_slope is not None and _lrL_px > 0:
                    result[f"lrL_pct_b_{timeframe}"] = _lrL_pb
                    result[f"lrL_slope_{timeframe}"] = round(_lrL_slope / _lrL_px * 100.0, 6)
                    if _lrL_lin is not None:
                        result[f"lrL_r2_{timeframe}"] = round(float(_lrL_lin), 6)
            bar_pat = detect_bar_patterns(adjusted_df, timeframe)
            result.update(bar_pat)
            # Candle body comparison (current vs prev) — needed by Strategy 3 (EMA200+StochRSI)
            _cb_body = abs(float(close_series.iloc[-1]) - float(open_series.iloc[-1]))
            _cb_body_prev = abs(float(close_series.iloc[-2]) - float(open_series.iloc[-2])) if len(close_series) > 1 else _cb_body
            result[f"candle_body_{timeframe}"] = round(_cb_body, 8)
            result[f"candle_body_prev_{timeframe}"] = round(_cb_body_prev, 8)
            result[f"candle_body_ratio_{timeframe}"] = round(_cb_body / _cb_body_prev, 3) if _cb_body_prev > 0 else 1.0
        if timeframe in ("15m", "1h", "4h", "D"):
            # Use kline bars only (not the optional mark-price append) so the
            # same causal detector and breakout timestamp are shared with NPZ
            # backtests.  Only the latest scalars are published to live JSON.
            _formation_fields = formation_fields_from_ohlcv(
                df["open"].astype(float).values,
                df["high"].astype(float).values,
                df["low"].astype(float).values,
                df["close"].astype(float).values,
                df["volume"].astype(float).values,
                timeframe,
            )
            result.update(latest_formation_fields(_formation_fields))
        t_up, tco, tcu = hull_trend_indicators(close_series, length_short=9, length_long=21)
        if t_up is not None: result[f"t_up_{timeframe}"] = t_up
        if tco is not None: result[f"tco_{timeframe}"] = tco
        if tcu is not None: result[f"tcu_{timeframe}"] = tcu

        # Use real time for updated_at to satisfy freshness filters in save/broadcasting
        ts_real_now = isoformat(utc_now())
        result["1m_updated_at"] = ts_real_now
        result["timestamp_1m"] = ts_real_now

        if timeframe == "1m":
            result["current_price_1m"] = current_price
            if use_mark and mark_ts:
                result["timestamp"] = isoformat(mark_ts)
            else:
                result["timestamp"] = result.get(f"timestamp_{timeframe}", ts_real_now)
                
            if mid_run:
                 result["mid_run_1m"] = True
                 
        if timeframe == "D":
            result["timestamp_D"] = result.get(f"timestamp_{timeframe}", result.get("timestamp", ""))
            
        if mid_run:
            result[f"timestamp_{timeframe}_mid"] = isoformat(utc_now())

        return result
        
class TradierPriceCacheManager:
    def __init__(self, data_dir: Path, redis_manager=None):
        self.data_dir = data_dir
        self.redis_manager = redis_manager
        self._lock = asyncio.Lock()

    async def get_price(self, symbol: str) -> Tuple[Optional[float], Optional[datetime]]:
        try:
            prices = None
            
            # 1. Check Redis First (Fastest)
            if self.redis_manager:
                prices_raw = await self.redis_manager.get("tradier_prices_latest")
                if isinstance(prices_raw, str): 
                    prices = json.loads(prices_raw)
                else:
                    prices = prices_raw

            # 2. Fallback to Disk
            if not prices:
                latest_file = self.data_dir / "tradier_prices_latest.json"
                if latest_file.exists():
                    import time

                    # Only trust file if it was modified in the last 30 seconds
                    if (time.time() - latest_file.stat().st_mtime) < 30:
                        async with aiofiles.open(latest_file, 'r') as f:
                            content = await f.read()
                            if content.strip():
                                prices = json.loads(content)

            # 3. Extract and Validate
            if prices:
                # Handle new nested format {"_metadata": ..., "data": {...}}
                symbol_data = prices.get("data", prices)
                
                if symbol in symbol_data:
                    entry = symbol_data[symbol]
                    p_val = entry.get('price') or entry.get('last')
                    
                    if p_val is not None and float(p_val) > 0:
                        # Try to get timestamp from the entry itself, fallback to bundle metadata
                        t_raw = entry.get('timestamp') or entry.get('date') or prices.get('_metadata', {}).get('updated_at')
                        
                        # Parse Timestamp safely
                        t_obj = None
                        if t_raw:
                            try:
                                import pandas as pd
                                if isinstance(t_raw, (int, float)):
                                    val = t_raw / 1000.0 if t_raw > 1e11 else t_raw
                                    t_obj = datetime.fromtimestamp(val, tz=timezone.utc)
                                else:
                                    t_obj = pd.to_datetime(t_raw, utc=True).to_pydatetime()
                            except Exception:
                                pass
                        return float(p_val), t_obj
        except Exception as e:
            logger.debug(f"[PriceCacheManager] Error fetching {symbol}: {e}")
        return None, None
        

    async def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            prices = None
            if self.redis_manager:
                prices = await self.redis_manager.get("tradier_prices_latest")
                if isinstance(prices, str): prices = json.loads(prices)
            
            if not prices:
                path = self.data_dir / "tradier_prices_latest.json"
                if path.exists() and path.stat().st_size > 0:
                    async with aiofiles.open(path, "rb") as f:
                        content = await f.read()
                        if content.strip():
                            prices = safe_json_loads(content)
                            
            if prices:
                if "data" in prices: prices = prices["data"]
                if symbol in prices:
                    return prices[symbol]
        except Exception as e:
            logger.debug(f"Price cache error: {e}")
        return None

class TradierBarManager:
    def __init__(self, api_client: TradierAPIClient, cache_dir: Path, price_cacheman: 'TradierPriceCacheManager'):
        self.api_client = api_client
        self.cache_dir = cache_dir
        self.price_cacheman = price_cacheman
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._semaphore = asyncio.Semaphore(10)
        env_info = get_current_environment()
        self.env = env_info.get("env", "macbook") if isinstance(env_info, dict) else "macbook"
        self.max_bars = 1800
        self._memory_cache = {}
        self._last_fetch_time = {}
        self._cache_lock = asyncio.Lock()
    
    async def get_latest(self, symbol: str, timeframe: str) -> Tuple[Optional[pd.DataFrame], Optional[datetime], Optional[str]]:
        path = self.cache_dir / f"{symbol}_{timeframe}.json"
        
        if not path.exists() or path.stat().st_size == 0:
            await self.get_bundle(symbol)
        
        if path.exists() and path.stat().st_size > 0:
            async with aiofiles.open(path, "rb") as f:
                content = await f.read()
                if not content.strip():
                    return None, None, None
                data = safe_json_loads(content)
                if data:
                    df = pd.DataFrame(data)
                    ts_col = "timestamp" if "timestamp" in df.columns else ("time" if "time" in df.columns else None)
                    if ts_col:
                        df["close_time"] = _to_utc(df[ts_col])
                        df["timestamp_dt"] = df["close_time"]
                        df = df.dropna(subset=["close_time"])
                    else:
                        return None, None, None
                    if df.empty: return None, None, None
                    last_ts = df["close_time"].iloc[-1]
                    if hasattr(last_ts, "to_pydatetime"):
                        close_ts = last_ts.to_pydatetime()
                    elif isinstance(last_ts, datetime):
                        close_ts = last_ts
                    else:
                        close_ts = pd.to_datetime(last_ts, utc=True).to_pydatetime()
                    return df, close_ts, str(path)
        return None, None, None
    
    async def get_bundle(self, symbol: str, force_api: bool = False) -> Dict[str, pd.DataFrame]:
        now_ts = time.time()
        # Use a more relaxed historical cache (300s) as history doesn't change often
        async with self._cache_lock:
            last_fetch = self._last_fetch_time.get(symbol, 0)
            if not force_api and symbol in self._memory_cache and (now_ts - last_fetch) < 20.0:
                return {tf: df.copy() for tf, df in self._memory_cache[symbol].items()}
            self._last_fetch_time[symbol] = now_ts

        bundle = {}
        disk_counts = {}
        now = datetime.now(timezone.utc)
        for tf in["1m", "5m", "15m", "1h", "4h", "D"]:
            hist = await self._read_json(self.cache_dir / f"{symbol}_{tf}.json")
            bundle[tf] = pd.DataFrame(hist) if hist else pd.DataFrame()
            disk_counts[tf] = len(bundle[tf])
            # Purge intraday caches that contain daily bars
            if tf in ("1m", "5m", "15m") and not bundle[tf].empty:
                if not is_intraday_data(bundle[tf], tf):
                    logger.warning(f"SKIPPING {symbol}_{tf}: cache has daily bars ({len(bundle[tf])} rows) — not deleting, will refetch")
                    bundle[tf] = pd.DataFrame()
                    disk_counts[tf] = 0

        def get_start_time(df_hist, days_back, is_daily=False):
            if not df_hist.empty and len(df_hist) >= 50 and 'timestamp' in df_hist.columns:
                try:
                    last_dt = pd.to_datetime(df_hist['timestamp'].iloc[-1], utc=True)
                    # Tradier API expects ET timestamps — convert from UTC
                    et_tz = pytz.timezone('America/New_York')
                    last_dt_et = last_dt.astimezone(et_tz)
                    if is_daily: return last_dt_et.strftime("%Y-%m-%d")
                    return last_dt_et.strftime("%Y-%m-%d %H:%M")
                except Exception: pass
            return (now - timedelta(days=days_back)).strftime("%Y-%m-%d")

        def clip_bars(df, tf=''):
             """TF-aware trim: server keeps ALL bars for backtesting. MacBook clips to save disk."""
             if df.empty: return df
             env_info = get_current_environment()
             _env = env_info.get("env", "macbook") if isinstance(env_info, dict) else "macbook"
             if _env == 'server':
                 return df
             if tf in ('1m', '3m', '5m'):
                 if len(df) > 4800:
                     return df.tail(3600).reset_index(drop=True)
             elif tf == 'D':
                 return df
             else:
                 if len(df) > 1800:
                     return df.tail(1200).reset_index(drop=True)
             return df

        def pad_with_higher(df_lower, df_higher, target_tf="1h"):
             if df_higher.empty: return df_lower
             if df_lower.empty and df_higher.empty: return pd.DataFrame()
             if not df_higher.empty and 'timestamp' not in df_higher.columns: return df_lower
             if not df_lower.empty and 'timestamp' not in df_lower.columns: return df_lower
             try:
                 core_cols = ["timestamp", "open", "high", "low", "close", "volume"]
                 repeats = 7 if target_tf == "1h" else 2
                 first_ts = _to_utc(df_lower['timestamp'].iloc[0]) if not df_lower.empty else pd.Timestamp.now(tz='UTC')
                 df_higher_dt = _to_utc(df_higher['timestamp'])
                 pad = df_higher[df_higher_dt < first_ts].copy()
                 if pad.empty: return df_lower
                 expanded_rows = []
                 for _, row in pad.iterrows():
                     base_ts = pd.to_datetime(row['timestamp'], utc=True)
                     for i in range(repeats):
                         new_row = row.copy()
                         new_row['timestamp'] = (base_ts + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                         expanded_rows.append(new_row)
                 if not expanded_rows: return df_lower
                 expanded = pd.DataFrame(expanded_rows)
                 common = [c for c in core_cols if c in expanded.columns]
                 if df_lower.empty:
                     return expanded[common].reset_index(drop=True)
                 common = [c for c in core_cols if c in df_lower.columns and c in expanded.columns]
                 combined = pd.concat([expanded[common], df_lower[common]], ignore_index=True)
                 combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
                 return combined.sort_values('timestamp').reset_index(drop=True)
             except Exception: return df_lower

        def merge_klines(df_old: pd.DataFrame, df_new: pd.DataFrame, is_intraday: bool = False) -> pd.DataFrame:
            if df_new.empty: return df_old
            if df_old.empty: return df_new
            if 'timestamp' not in df_old.columns: return df_new
            if 'timestamp' not in df_new.columns: return df_old
            combined = pd.concat([df_old, df_new], ignore_index=True).drop_duplicates(subset=["timestamp"], keep="last")
            return combined.sort_values("timestamp").reset_index(drop=True)

        async with self._semaphore:
            try:
                start_d = get_start_time(bundle["D"], 3000, is_daily=True)
                new_d = await self.api_client.get_history(symbol, start=start_d, interval='daily')
                if new_d:
                    df_new_d = pd.DataFrame(new_d)
                    if 'date' in df_new_d.columns:
                        # Set Daily bar to exactly 09:30 ET then convert to UTC
                        df_new_d['timestamp_dt'] = pd.to_datetime(df_new_d['date']).apply(lambda x: ET.localize(datetime.combine(x, dt_time(9, 30))).astimezone(pytz.UTC))
                        df_new_d['timestamp'] = df_new_d['timestamp_dt'].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                        bundle["D"] = merge_klines(bundle["D"], df_new_d, is_intraday=False)
            except Exception as e:
                logger.error(f"Daily fetch error {symbol}: {e}")

            for tf, days in [("1m", 7), ("5m", 30), ("15m", 90)]:
                try:
                    start = get_start_time(bundle[tf], days)
                    api_tf = tf.replace("m", "min")
                    new_bars = await self.api_client.get_timesales(symbol, interval=api_tf, start=start)
                    if new_bars:
                        df_new = pd.DataFrame(new_bars)
                        if not is_intraday_data(df_new, tf):
                            logger.warning(f"REJECTED {symbol} {tf}: timesales returned daily bars")
                        elif 'time' in df_new.columns:
                            df_new['timestamp_dt'] = _to_utc(df_new['time'])
                                
                            df_new = df_new.dropna(subset=['timestamp_dt'])
                            # df_new['timestamp'] = df_new['timestamp_dt'].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                            df_new['timestamp'] = df_new['timestamp_dt'].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                            df_new = filter_strict_market_hours(df_new, is_fast_tf=True)
                            bundle[tf] = merge_klines(bundle[tf], df_new, is_intraday=True)


                except Exception as e:
                    logger.error(f"{tf} fetch error {symbol}: {e}")
                
                # Fallback resampling between intraday resolutions only
                if tf == "3m" and (bundle["3m"].empty or len(bundle["3m"]) < 10):
                    res_3m = resample_tf(bundle["1m"], "3m")
                    if not res_3m.empty: bundle["3m"] = merge_klines(bundle["3m"], res_3m)
                elif tf == "5m" and (bundle["5m"].empty or len(bundle["5m"]) < 10):
                    res_5m = resample_tf(bundle["1m"], "5m")
                    if not res_5m.empty: bundle["5m"] = merge_klines(bundle["5m"], res_5m)
                elif tf == "15m" and (bundle["15m"].empty or len(bundle["15m"]) < 10):
                    res_15m = resample_tf(bundle["5m"], "15m")
                    if not res_15m.empty: bundle["15m"] = merge_klines(bundle["15m"], res_15m)

            try:
                # Merge freshly resampled intraday 1h/4h into existing disk data — never overwrite with fake padded bars
                new_1h = resample_tf(bundle["15m"], "1h")
                if not new_1h.empty:
                    bundle["1h"] = merge_klines(bundle["1h"], new_1h)
                new_4h = resample_tf(bundle["1h"], "4h")
                if not new_4h.empty:
                    bundle["4h"] = merge_klines(bundle["4h"], new_4h)
            except Exception as e:
                logger.error(f"Resample error {symbol}: {e}")

            for tf in bundle:
                bundle[tf] = clip_bars(bundle[tf], tf=tf)

        # ONLY WRITE TO DISK — guard against writing near-empty data
        # disk_counts was captured pre-clip from the raw file read. After merge+clip, the bundle
        # is trimmed by clip_bars(). Compare new count against the PRE-CLIP count only if the new
        # data is suspiciously small (< 50 bars), not against bloated legacy files.
        for tf, df in bundle.items():
            if not df.empty:
                new_count = len(df)
                if new_count < 50 and disk_counts.get(tf, 0) > 200:
                    logger.error(f"BLOCKED write {symbol}_{tf}: only {new_count} bars (disk had {disk_counts.get(tf, 0)}) — refusing to destroy data")
                    continue
                await self._write_json(self.cache_dir / f"{symbol}_{tf}.json", df.to_dict("records"))
            
        async with self._cache_lock:
            self._memory_cache[symbol] = {tf: df.copy() for tf, df in bundle.items()}
            
        return bundle

    async def _read_json(self, path: Path) -> List[Dict]:
        if not path.exists() or path.stat().st_size == 0: return[]
        try:
            async with aiofiles.open(path, "rb") as f:
                content = await f.read()
                if not content.strip(): return[]
                return safe_json_loads(content)
        except Exception: return []

    async def _write_json(self, path: Path, data: List[Dict]):
        try:
            if not data:
                logger.warning(f"Refusing to write empty data to {path.name}")
                return
            def _write():
                path.parent.mkdir(parents=True, exist_ok=True)
                write_data = data
                if path.exists():
                    existing_size = path.stat().st_size
                    tentative = json_dumps(data)
                    new_size = len(tentative) if isinstance(tentative, bytes) else len(tentative.encode('utf-8'))
                    env_info = get_current_environment()
                    _env = env_info.get("env", "macbook") if isinstance(env_info, dict) else "macbook"
                    min_ratio = 0.1 if _env == "macbook" else 0.3
                    if existing_size > 1000 and new_size < existing_size * min_ratio:
                        try:
                            with open(path, 'rb') as f:
                                raw = f.read()
                            try:
                                existing = safe_json_loads(raw)
                            except Exception:
                                import json as _json
                                existing = _json.loads(raw)
                            if existing:
                                merged_map = {r['timestamp']: r for r in existing if 'timestamp' in r}
                                for r in data:
                                    if 'timestamp' in r:
                                        merged_map[r['timestamp']] = r
                                write_data = sorted(merged_map.values(), key=lambda r: r['timestamp'])
                                logger.info(f"MERGED {path.name}: {len(existing)} existing + {len(data)} new → {len(write_data)} bars")
                            else:
                                logger.error(f"BLOCKED write to {path.name}: unreadable existing, new size {new_size} < {int(min_ratio*100)}% of {existing_size}")
                                return
                        except Exception as e:
                            logger.error(f"BLOCKED write to {path.name}: merge failed ({e})")
                            return
                json_bytes = json_dumps(write_data)
                random_suffix = uuid.uuid4().hex
                tmp = path.with_name(f".{path.name}.{random_suffix}.tmp")
                with open(tmp, 'wb') as f:
                    if isinstance(json_bytes, str):
                        f.write(json_bytes.encode('utf-8'))
                    else:
                        f.write(json_bytes)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            await asyncio.to_thread(_write)
        except Exception as e:
            logger.error(f"Write error {path.name}: {e}")


@dataclass
class SymbolTimeframeState:
    timeframe: str
    last_close: Optional[datetime] = None
    full_done: bool = False
    mid_done: bool = False
    trivial_done: bool = False
    mid_due: Optional[datetime] = None
    last_full_finish: Optional[datetime] = None
    last_mid_finish: Optional[datetime] = None
    last_df: Optional[pd.DataFrame] = None


class TradierIndicatorOrchestrator:    
    def __init__(self):
        self.config = config
        env_info = get_current_environment()
        self.env = env_info.get("env", "macbook") if isinstance(env_info, dict) else "macbook"
        self.symbols = self._load_symbols()
        self.symbol_set: Set[str] = set(self.symbols)
        self.api_client = TradierAPIClient(self.config, account_key='tra') 
        self.price_cacheman = TradierPriceCacheManager(self.config.DATA_DIR)
        
        self.bar_manager = TradierBarManager(
            self.api_client, 
            self.config.KLINES_CACHE_DIR,
            self.price_cacheman  )
        
        self.calculator = IndicatorCalculator()
        self.redis_manager = None
        self.state: Dict[str, Dict[str, SymbolTimeframeState]] = {}
        self.data: Dict[str, Dict[str, Any]] = {}
        self.pending_mid: Dict[str, List[Tuple[str, datetime]]] = {}
        self.pending_trivial: Dict[str, bool] = {}
        self.final_scores: Dict[str, float] = {}
        self.ranking_scores: Dict[str, float] = {}
        self._save_lock = asyncio.Lock()
        self._last_dirty = utc_now()
        self._dirty = False
        self._save_due = False
        self._last_payload_hash: Optional[str] = None
        self._schedule_order =["D", "4h", "1h", "15m", "5m", "1m"]
        self._shutdown = asyncio.Event()
        self._last_symbol_refresh = utc_now()
        self.executor = ThreadPoolExecutor(max_workers=4)
        # 2026-04-27 user rule: indicators max 1 min stale. Bumped from 4→24.
        # If we hit Tradier 429s we back off via _http_semaphore (which still throttles total in-flight).
        self.cycle_semaphore = asyncio.Semaphore(int(getattr(self.config, 'TRADIER_INDICATORS_CYCLE_CONCURRENCY', 24)))
        ensure_directory(self.config.DATA_DIR)
        self._load_existing_data()
        self._init_state()
        self._http_semaphore = asyncio.Semaphore(int(getattr(self.config, 'TRADIER_INDICATORS_HTTP_CONCURRENCY', 48)))  # was 15 — 2026-04-27 speedup

    def _load_symbols(self) -> List[str]:
        collected: Set[str] = set()
        # 2026-04-27 user rule: indicators max 1 min stale. Narrow universe to
        # tradeable_keys watchlist + active position symbols (~120) instead of
        # the 270-symbol master list. 270 × 7 timeframes × 4 concurrency = ~12 min cycle.
        # Narrow + bumped concurrency hits the <60s target.
        # 2026-08-20 RESILIENCE: also include every symbol from active_config.json (per_sym 169)
        # so promoted winners never miss indicators — user mandate: all keys in mandatory/TRB are tradeable.
        narrow = bool(getattr(self.config, 'TRADIER_INDICATORS_NARROW_UNIVERSE', True))
        if narrow:
            base_path = Path(getattr(self.config, 'BASE_PATH', '/Users/niels/Documents/binance'))
            for fn in ('symbols_trb_long.json', 'symbols_trb_short.json',
                       'symbols_trc_long.json', 'symbols_trc_short.json',
                       'symbols_tra_long.json', 'symbols_tra_short.json'):
                p = base_path / fn
                if not p.exists():
                    continue
                try:
                    with open(p) as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        collected.update(str(s).upper() for s in data if isinstance(s, str) and s.strip())
                except Exception as e:
                    logger.warning(f"_load_symbols: {fn} read err: {e}")
            # 2026-08-20: include active_config symbols (per_sym winners) — fixes 96 missing from 169
            for _ac in ('data/hourly_reconfig/trb/active_config.json', 'data/hourly_reconfig/trc/active_config.json'):
                try:
                    _ac_p = base_path / _ac
                    if _ac_p.exists():
                        with open(_ac_p) as f:
                            _ac_data = json.load(f)
                        if isinstance(_ac_data, dict):
                            for k in _ac_data.keys():
                                _sym = str(k).rsplit('_', 1)[0].upper()
                                if _sym and _sym.isalpha():
                                    collected.add(_sym)
                except Exception as e:
                    logger.warning(f"_load_symbols: {_ac} read err: {e}")
            for acct in ('trb', 'trc', 'tra'):
                for side in ('long_positions.json', 'short_positions.json'):
                    p = base_path / acct / side
                    if not p.exists():
                        continue
                    try:
                        with open(p) as f:
                            data = json.load(f)
                        if isinstance(data, dict):
                            for k in data.keys():
                                # keys look like "AAPL_LONG" / "MSFT_SHORT" / option OCC
                                sym = str(k).split('_', 1)[0].upper()
                                # Exclude option OCCs (PLTR260717C00150000) — those have digits
                                if sym and sym.isalpha():
                                    collected.add(sym)
                    except Exception as e:
                        logger.warning(f"_load_symbols: {acct}/{side} read err: {e}")
            if collected:
                logger.info(f"_load_symbols (narrow): {len(collected)} symbols (watchlist + active positions + active_config)")
                return sorted(collected)
            logger.warning("_load_symbols (narrow): empty — falling back to SYMBOLS_FILE master list")
        if self.config.SYMBOLS_FILE.exists():
            try:
                with open(self.config.SYMBOLS_FILE, "r") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    collected.update(str(symbol).upper() for symbol in data)
            except Exception:
                pass
        return sorted(collected)

    def _load_existing_data(self) -> None:
        latest_file = self.config.DATA_DIR / "tradier_indicators_latest.json"
        self.data = {}
        
        if latest_file.exists():
            try:
                with open(latest_file, "rb") as f:
                    content = f.read()
                
                try:
                    self.data = safe_json_loads(content)
                except Exception:
                    try:
                        txt = content.decode('utf-8', errors='ignore').strip()
                        end_idx = txt.rfind('}')
                        if end_idx != -1:
                            fixed_txt = txt[:end_idx+1]
                            self.data = json.loads(fixed_txt)
                            
                            json_bytes = json_dumps(self.data)
                            
                            with open(latest_file, "wb") as f:
                                if isinstance(json_bytes, str):
                                    f.write(json_bytes.encode('utf-8'))
                                else:
                                    f.write(json_bytes)
                                f.flush()
                                os.fsync(f.fileno())
                            logger.warning(f"✅ Repaired corrupted indicators file: {latest_file.name}")
                    except Exception as repair_err:
                        logger.error(f"❌ Failed to repair {latest_file.name}: {repair_err}")
                        self.data = {}

                if not isinstance(self.data, dict):
                    self.data = {}
            except Exception as e:
                logger.error(f"❌ Critical error loading existing data: {e}")
                self.data = {}

    def save_indicators_to_json(self, indicators: Dict[str, Dict]):
        try:
            self.config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            if not indicators: indicators = {}
            json_bytes = json_dumps(indicators)
            
            latest_file = self.config.DATA_DIR / "tradier_indicators_latest.json"
            
            def _atomic_write_sync(path: Path, data_bytes: bytes):
                random_suffix = uuid.uuid4().hex
                tmp = path.with_name(f".{path.name}.{random_suffix}.tmp")
                try:
                    with open(tmp, 'wb') as f:
                        if isinstance(data_bytes, str):
                            f.write(data_bytes.encode('utf-8'))
                        else:
                            f.write(data_bytes)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, path)
                except Exception as e:
                    if tmp.exists():
                        try: os.remove(tmp)
                        except Exception: pass
                    raise e
                    
            _atomic_write_sync(latest_file, json_bytes)
            # Also write timestamped file so tradier_manage SAFE-PATH reads freshest (latest gets stuck in OS cache) — DO NOT USE _latest EVER for trading
            ts_file = self.config.DATA_DIR / f"tradier_indicators_{int(time.time())}.json"
            _atomic_write_sync(ts_file, json_bytes)
            # Hierarchical retention: last 5 (~3min at 39s), then 1 per 15m until 1h, 1 per hour until 4h, 1 per day — tossed rest
            try:
                now_ts = time.time()
                all_ts = sorted(self.config.DATA_DIR.glob("tradier_indicators_[0-9]*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
                keep = set(all_ts[:5])
                # 15m buckets until 1h
                cutoff_1h = now_ts - 3600
                by_15m = {}
                for p in all_ts[5:]:
                    mtime = p.stat().st_mtime
                    if mtime < cutoff_1h:
                        continue
                    if mtime >= now_ts - 3*60:
                        continue
                    bucket = int(mtime // 900)
                    by_15m.setdefault(bucket, []).append(p)
                for bucket, lst in by_15m.items():
                    keep.add(max(lst, key=lambda p: p.stat().st_mtime))
                # 1h buckets until 4h
                cutoff_4h = now_ts - 4*3600
                by_hour = {}
                for p in all_ts:
                    if p in keep:
                        continue
                    mtime = p.stat().st_mtime
                    if mtime < cutoff_4h or mtime >= cutoff_1h:
                        continue
                    bucket = int(mtime // 3600)
                    by_hour.setdefault(bucket, []).append(p)
                for bucket, lst in by_hour.items():
                    keep.add(max(lst, key=lambda p: p.stat().st_mtime))
                # Daily beyond 4h
                by_day = {}
                for p in all_ts:
                    if p in keep:
                        continue
                    mtime = p.stat().st_mtime
                    if mtime >= cutoff_4h:
                        continue
                    bucket = int(mtime // 86400)
                    by_day.setdefault(bucket, []).append(p)
                for bucket, lst in by_day.items():
                    keep.add(max(lst, key=lambda p: p.stat().st_mtime))
                for p in all_ts:
                    if p not in keep:
                        try: p.unlink()
                        except Exception: pass
            except Exception as _e:
                logger.debug(f"timestamp retention error: {_e}")
            logger.info(f"✅ Saved indicators for {len(indicators)} symbols to latest + timestamped JSON")
        except Exception as e:
            logger.error(f"❌ Error saving indicators to JSON: {e}", exc_info=True)
    
    def _init_state(self) -> None:
        for symbol in self.symbols:
            self.data.setdefault(symbol, {})
            self.state[symbol] = {}
            for tf in TIMEFRAMES.keys():
                state = SymbolTimeframeState(timeframe=tf)
                ts_str = self.data.get(symbol, {}).get(f"timestamp_{tf}") if isinstance(self.data.get(symbol, {}), dict) else None
                if isinstance(ts_str, str):
                    try:
                        parsed = ts_str.replace("Z", "+00:00") if ts_str.endswith("Z") else ts_str
                        state.last_close = datetime.fromisoformat(parsed)
                    except Exception:
                        state.last_close = None
                self.state[symbol][tf] = state
            self.pending_mid[symbol] =[]
            self.pending_trivial[symbol] = False

    async def run_cycle(self):
        _cycle_total = len(self.symbols)
        logger.info(f"Update cycle starting for {_cycle_total} symbols (Env: {self.bar_manager.env})")
        all_prices = {}
        if self.redis_manager:
            try:
                prices_raw = await self.redis_manager.get("tradier_prices_latest")
                if isinstance(prices_raw, dict):
                    all_prices = prices_raw.get("data", prices_raw)
            except Exception as e:
                logger.warning(f"Failed to fetch prices for cycle: {e}")

        _cycle_done = [0]

        async def process_symbol_parallel(symbol):
            async with self.cycle_semaphore:
                try:
                    s_data = self.data.setdefault(symbol, {})
                    # Always stamp heartbeat at cycle start regardless of bundle availability
                    real_now_iso = isoformat(utc_now())
                    s_data["1m_updated_at"] = real_now_iso
                    s_data["timestamp_1m"] = real_now_iso
                    bundle = await self.bar_manager.get_bundle(symbol)
                    if not bundle: return
                    # Clip to recent bars for live indicator computation — server mode reads years of 1m/5m data.
                    # 600 bars is sufficient for all indicators (SMA-200, ATR, RSI, WT).
                    _clip = int(getattr(self.config, 'LIVE_INDICATOR_MAX_BARS_PER_TF', 600))
                    if _clip > 0:
                        bundle = {tf: (df.tail(_clip).reset_index(drop=True) if len(df) > _clip else df) for tf, df in bundle.items()}
                    _cycle_done[0] += 1
                    n = _cycle_done[0]
                    if n % 10 == 0 or n == _cycle_total:
                        logger.info(f"[CYCLE_PROGRESS] {n}/{_cycle_total} bundles clipped and ready")
                    quote = all_prices.get(symbol)
                    m_price = None
                    m_ts = None
                    if quote:
                        p_raw = quote.get('price') or quote.get('last')
                        if p_raw: m_price = float(p_raw)
                        ts_raw = quote.get('timestamp') or quote.get('date')
                        if ts_raw:
                            try:
                                if isinstance(ts_raw, (int, float)):
                                    val = ts_raw / 1000.0 if ts_raw > 1e11 else ts_raw
                                    m_ts = datetime.fromtimestamp(val, tz=timezone.utc)
                                else:
                                    m_ts = pd.to_datetime(ts_raw, errors='coerce')
                                    if m_ts is not pd.NaT:
                                        if getattr(m_ts, "tzinfo", None) is None:
                                            m_ts = m_ts.tz_localize(ET, ambiguous='infer').tz_convert(timezone.utc).to_pydatetime()
                                        else:
                                            m_ts = m_ts.tz_convert(timezone.utc).to_pydatetime()
                                    else: m_ts = None
                            except Exception: m_ts = None
                    else: m_price, m_ts = await self.price_cacheman.get_price(symbol)
                    loop = asyncio.get_running_loop()
                    for tf, df in bundle.items():
                        if len(df) < 1: continue
                        res = await loop.run_in_executor(self.executor, self.calculator.compute, df, symbol, tf, m_price, m_ts, True)
                        s_data.update(res)
                        # Stamp compute time per-TF so staleness detection has accurate timestamps
                        s_data[f"timestamp_{tf}"] = real_now_iso
                    self.data[symbol] = s_data
                except Exception as e:
                    logger.error(f"Cycle error for {symbol}: {e}", exc_info=True)

        tasks = [asyncio.create_task(process_symbol_parallel(s)) for s in self.symbols]
        await asyncio.gather(*tasks)
        # === BACKTEST WINNERS: Enrich all symbols with Clenow/SMFI/Minervini/Connors ===
        await asyncio.to_thread(self._enrich_backtest_winners)
        self._refresh_sentiment()
        self._save_due = True
        self._dirty = True
        await self._save_data()
           
        
    async def _process_timeframe(self, timeframe: str, force: bool = False) -> None:
        async def process_symbol(symbol: str) -> None:
            async with self._http_semaphore:
                try:
                    df, close_ts, source = await self.bar_manager.get_latest(symbol, timeframe)
                    symbol_data = self.data.setdefault(symbol, {})
                    state = self.state[symbol][timeframe]
                    reuse_previous = False
                    
                    if df is None or close_ts is None:
                        if state.last_df is not None and state.last_close is not None:
                            df = state.last_df.copy()
                            close_ts = state.last_close
                            reuse_previous = True
                        else:
                            state.full_done = False
                            return
                    
                    required_keys = self._required_by_timeframe(timeframe)
                    needs_refresh = any(symbol_data.get(key) is None for key in required_keys)
                    mark_price = None
                    if required_keys or force or needs_refresh or reuse_previous:
                        mark_price,mark_ts = await self.price_cacheman.get_price(symbol)

                    new_bar = state.last_close is None or (close_ts is not None and close_ts > state.last_close)
                    
                    if new_bar or needs_refresh or (required_keys and force):
                        await self._run_full(symbol, timeframe, df, close_ts, mark_price, state, mark_ts=mark_ts)
                        state.last_close = close_ts
                        state.full_done = True
                        state.mid_done = False
                        state.trivial_done = False
                        duration = TIMEFRAMES.get(timeframe, {}).get("sec", 60)
                        state.mid_due = close_ts + timedelta(seconds=duration + TIMEFRAMES.get(timeframe, {}).get("half", 30))
                        
                    elif df is not None and close_ts is not None:
                        state.full_done = True
                        if await self._maybe_run_mid(symbol, timeframe, df, close_ts, mark_price):
                            pass
                    elif state.last_df is not None and mark_price is not None:
                        state.full_done = True
                except Exception as e:
                    logger.error(f"Error processing {symbol} {timeframe}: {e}")

        # QUICK OPEN FIX 2026-08-17 13:17 UTC 17m before open: skip D/4h/1h/15m unless at correct ET window, otherwise 115 symbols D/1h hangs 60s+ and blocks 1m/5m fresh needed for open. Market open needs 1m/5m <40s, not D.
        now_et = utc_now().astimezone(ET)
        if timeframe == "D" and not (now_et.hour == 16 and now_et.minute == 0):
            logger.info(f"[{timeframe}] Skipped (not 16:00 ET, now {now_et.hour:02d}:{now_et.minute:02d} ET) for quick open")
            return
        if timeframe == "4h" and not ((now_et.hour == 9 and now_et.minute == 30) or (now_et.hour == 13 and now_et.minute == 0) or (now_et.hour == 16 and now_et.minute == 0)):
            logger.info(f"[{timeframe}] Skipped (not 9:30/13:00/16:00 ET) for quick open")
            return
        if timeframe == "1h" and not (now_et.minute == 0):
            logger.info(f"[{timeframe}] Skipped (not :00, now {now_et.hour:02d}:{now_et.minute:02d} ET) for quick open")
            return
        if timeframe == "15m" and now_et.minute not in (0, 15, 30, 45):
            logger.info(f"[{timeframe}] Skipped (not :00/:15/:30/:45, now {now_et.hour:02d}:{now_et.minute:02d} ET) for quick open")
            return
        logger.info(f"[{timeframe}] Starting concurrent update for {len(self.symbols)} symbols...")
        tasks =[asyncio.create_task(process_symbol(sym)) for sym in self.symbols]
        if tasks:
            await asyncio.gather(*tasks)

        if timeframe == self._schedule_order[-1]:
            self._save_due = True
            
    async def _refresh_symbols_if_needed(self) -> None:
        if (utc_now() - self._last_symbol_refresh).total_seconds() < 360:
            return
        new_symbols = self._load_symbols()
        if not new_symbols:
            self._last_symbol_refresh = utc_now()
            return
        new_set = set(new_symbols)
        current_set = set(self.symbols)
        if new_set == current_set:
            self._last_symbol_refresh = utc_now()
            return
        for symbol in new_symbols:
            if symbol not in self.data:
                self.data[symbol] = {}
            if symbol not in self.state:
                self.state[symbol] = {tf: SymbolTimeframeState(timeframe=tf) for tf in TIMEFRAMES.keys()}
                self.pending_mid[symbol] = []
                self.pending_trivial[symbol] = False
        for symbol in current_set - new_set:
            self.pending_mid.pop(symbol, None)
            self.pending_trivial.pop(symbol, None)
            self.state.pop(symbol, None)
        self.symbols = new_symbols
        self.symbol_set = set(new_symbols)
        self._last_symbol_refresh = utc_now()

    async def _schedule_loop(self) -> None:
        # 2026-08-14 FIX: Tradier market hours ET 9:30-16:00: 4h=9:30/13:00/16:00, 1h=9:00-16:00, D=16:00 ET. Was run_cycle bundle 30min ABSURD -> <40s like ez + ET clocks.
        interval = 39.0
        while not self._shutdown.is_set():
            now = utc_now()
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            elapsed = (now - midnight).total_seconds()
            wait = interval - (elapsed % interval)
            if wait <= 0.1:
                wait += interval
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=wait)
                break
            except asyncio.TimeoutError:
                pass
            await self._refresh_symbols_if_needed()
            now_et = now.astimezone(ET)
            et_h = now_et.hour
            et_m = now_et.minute
            for tf in self._schedule_order:
                if self._shutdown.is_set():
                    break
                if tf == "15m" and now_et.minute not in (0, 15, 30, 45):
                    continue
                if tf == "1h" and not (9 <= et_h <= 16 and et_m == 0):
                    continue
                if tf == "4h" and not ((et_h == 9 and et_m == 30) or (et_h == 13 and et_m == 0) or (et_h == 16 and et_m == 0)):
                    continue
                if tf == "D" and not (et_h == 16 and et_m == 0):
                    continue
                await self._process_timeframe(tf, force=False)
            self._save_due = True

    def _required_by_timeframe(self, timeframe: str) -> List[str]:
        base_indicators =[f"timestamp_{timeframe}", f"dc_high_{timeframe}", f"dc_low_{timeframe}", f"dc_basis_{timeframe}",
                          f"k_{timeframe}", f"d_{timeframe}", f"atr_{timeframe}", f"rsi_{timeframe}"]
        return base_indicators

    async def _run_full(self, symbol: str, timeframe: str, df: pd.DataFrame, close_ts: datetime, mark_price: Optional[float], state: SymbolTimeframeState, mark_ts=None) -> None:
        required_keys = self._required_by_timeframe(timeframe)
        if mark_ts is None and required_keys and mark_price is None:
            q = await self.price_cacheman.get_quote(symbol)
            if q:
                mark_price = float(q.get('price') or q.get('last') or 0.0)
                ts_raw = q.get('timestamp') or q.get('date')
                if ts_raw:
                    try:
                        if isinstance(ts_raw, (int, float)):
                            val = ts_raw / 1000.0 if ts_raw > 1e11 else ts_raw
                            mark_ts = datetime.fromtimestamp(val, tz=timezone.utc)

                        else:
                            mark_ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                    except Exception: pass
        if 'timestamp' in df.columns:
            df['timestamp'] = _to_utc(df['timestamp'])
        if 'close_time' in df.columns:
            df['close_time'] = _to_utc(df['close_time'])

        result = self.calculator.compute(df, symbol, timeframe, mark_price, mark_ts, mid_run=False)
        if not result: return
        symbol_data = self.data.setdefault(symbol, {})
        symbol_data.update(result)
        self._inject_dc_moment(symbol, symbol_data)
        self._inject_wt_composite(symbol, symbol_data)
        self._update_master_timestamp(symbol_data)
        state.last_df = df.copy()
        await self._mark_dirty()
    
    async def _maybe_run_mid(self, symbol: str, timeframe: str, df: pd.DataFrame, close_ts: datetime, mark_price: Optional[float]) -> bool:
        state = self.state[symbol][timeframe]
        if state.mid_due is None or state.mid_done:
            return False
        if utc_now() < state.mid_due:
            return False
        if not await self._trivial_ready(symbol):
            return False
        mark_ts = None
        quote = await self.price_cacheman.get_quote(symbol)
        if quote:
            p = quote.get('price') or quote.get('last')
            if p:
                mark_price = float(p)
                ts_raw = quote.get('timestamp') or quote.get('date')
                if ts_raw:
                    try:
                        if isinstance(ts_raw, (int, float)):
                            val = ts_raw / 1000.0 if ts_raw > 1e11 else ts_raw
                            mark_ts = datetime.fromtimestamp(val, tz=timezone.utc)
                        else:
                            mark_ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                    except Exception: pass
        if 'timestamp' in df.columns:
            df['timestamp'] = _to_utc(df['timestamp'])
        if 'close_time' in df.columns:
            df['close_time'] = _to_utc(df['close_time'])
        result = self.calculator.compute(df, symbol, timeframe, mark_price, mark_ts, mid_run=True)
        if not result:
            return False
        symbol_data = self.data.setdefault(symbol, {})
        symbol_data.update(result)
        self._inject_dc_moment(symbol, symbol_data)
        self._inject_wt_composite(symbol, symbol_data)
        self._update_master_timestamp(symbol_data)
        state.mid_done = True
        state.last_mid_finish = utc_now()
        state.last_df = df.copy()
        await self._mark_dirty()
        return True
    
    async def _try_trivial(self, symbol: str) -> None:
        if self.pending_trivial[symbol]:
            return
        if await self._trivial_ready(symbol):
            await self._run_trivial(symbol)
    
    async def _trivial_ready(self, symbol: str) -> bool:
        return all(self.state[symbol][tf].full_done for tf in TIMEFRAMES.keys())
    
    async def _run_trivial(self, symbol: str) -> None:
        self.pending_trivial[symbol] = True
        for tf in TIMEFRAMES.keys():
            self.state[symbol][tf].trivial_done = True
        self.pending_trivial[symbol] = False
        await self._mark_dirty()
    
    def _symbol_complete(self, symbol: str) -> bool:
        if symbol not in self.state:
            return False
        completed_timeframes = sum(1 for tf in self._schedule_order if self.state[symbol].get(tf, SymbolTimeframeState(tf)).full_done)
        return completed_timeframes >= 3
    
    def _all_symbols_complete(self) -> bool:
        if not self.symbols:
            return False
        return all(self._symbol_complete(sym) for sym in self.symbols)
    
    def _inject_wt_composite(self, symbol: str, symbol_data: Dict[str, Any]) -> None:
        """Cross-TF WaveTrend composite — inlined. Stock TFs: 5m, 15m, 1h, 4h, D."""
        try:
            tfs = ["5m", "15m", "1h", "4h", "D"]
            _sf = lambda v, d=0.0: float(v) if v is not None else d
            n_tfs = len(tfs)
            bull_count = sum(1 for tf in tfs if _sf(symbol_data.get(f"wt1_{tf}")) > _sf(symbol_data.get(f"wt2_{tf}")))
            symbol_data["wt_bull_alignment"] = bull_count
            symbol_data["wt_bear_alignment"] = n_tfs - bull_count
            htf_tfs = [tf for tf in tfs if tf not in ("3m", "5m")]
            hl_count = sum(1 for tf in htf_tfs if symbol_data.get(f"wt_trough_structure_{tf}") == "HL")
            lh_count = sum(1 for tf in htf_tfs if symbol_data.get(f"wt_peak_structure_{tf}") == "LH")
            ll_count = sum(1 for tf in htf_tfs if symbol_data.get(f"wt_trough_structure_{tf}") == "LL")
            hh_count = sum(1 for tf in htf_tfs if symbol_data.get(f"wt_peak_structure_{tf}") == "HH")
            symbol_data["wt_hl_count"] = hl_count
            symbol_data["wt_lh_count"] = lh_count
            symbol_data["wt_ll_count"] = ll_count
            symbol_data["wt_hh_count"] = hh_count
            symbol_data["wt_any_bull_div"] = False
            symbol_data["wt_any_bear_div"] = False
            symbol_data["wt_strongest_bull_div_tf"] = None
            symbol_data["wt_strongest_bear_div_tf"] = None
            for tf in tfs:
                div = symbol_data.get(f"wt_divergence_{tf}")
                if div in ("BULL", "HIDDEN_BULL"):
                    symbol_data["wt_any_bull_div"] = True
                    symbol_data["wt_strongest_bull_div_tf"] = tf
                if div in ("BEAR", "HIDDEN_BEAR"):
                    symbol_data["wt_any_bear_div"] = True
                    symbol_data["wt_strongest_bear_div_tf"] = tf
            states = {tf: (symbol_data.get(f"wt_momentum_state_{tf}") or "") for tf in tfs}
            symbol_data["wt_momentum_narrative"] = states
            rising_crosses = sum(1 for tf in tfs if symbol_data.get(f"wt_cross_rising_{tf}") is True)
            falling_crosses = sum(1 for tf in tfs if symbol_data.get(f"wt_cross_rising_{tf}") is False)
            symbol_data["wt_rising_cross_count"] = rising_crosses
            symbol_data["wt_falling_cross_count"] = falling_crosses
            os_count = sum(1 for tf in tfs if _sf(symbol_data.get(f"wt_percentile_{tf}"), 50) < 20)
            ob_count = sum(1 for tf in tfs if _sf(symbol_data.get(f"wt_percentile_{tf}"), 50) > 80)
            symbol_data["wt_oversold_tf_count"] = os_count
            symbol_data["wt_overbought_tf_count"] = ob_count
            vel_up = sum(1 for tf in tfs if _sf(symbol_data.get(f"wt_velocity_{tf}")) > 0)
            vel_down = sum(1 for tf in tfs if _sf(symbol_data.get(f"wt_velocity_{tf}")) < 0)
            symbol_data["wt_velocity_up_count"] = vel_up
            symbol_data["wt_velocity_down_count"] = vel_down
            bull_cross_count = sum(1 for tf in tfs if symbol_data.get(f"wt_cross_{tf}") == "BULL")
            bear_cross_count = sum(1 for tf in tfs if symbol_data.get(f"wt_cross_{tf}") == "BEAR")
            symbol_data["wt_bull_cross_count"] = bull_cross_count
            symbol_data["wt_bear_cross_count"] = bear_cross_count
            _tw = {"D": 5, "4h": 4, "1h": 3, "15m": 2, "5m": 1}
            long_score, short_score = 0.0, 0.0
            long_score += (bull_count - n_tfs / 2.0) * 10
            short_score += ((n_tfs - bull_count) - n_tfs / 2.0) * 10
            long_score += hl_count * 5 - lh_count * 5 + hh_count * 3 - ll_count * 3
            short_score += lh_count * 5 - hl_count * 5 + ll_count * 3 - hh_count * 3
            if symbol_data["wt_any_bull_div"]: long_score += 20
            if symbol_data["wt_any_bear_div"]: short_score += 20
            long_score += os_count * 5 - ob_count * 3
            short_score += ob_count * 5 - os_count * 3
            long_score += rising_crosses * 3
            short_score += falling_crosses * 3
            long_score += bull_cross_count * 3 - bear_cross_count * 2
            short_score += bear_cross_count * 3 - bull_cross_count * 2
            long_score += vel_up * 2 - vel_down
            short_score += vel_down * 2 - vel_up
            for tf in tfs:
                w = _tw.get(tf, 1)
                st = states.get(tf, "")
                if st == "EXHAUST_DOWN": long_score += w
                elif st == "IMPULSE_UP": long_score += w * 0.7
                elif st == "EXHAUST_UP": short_score += w
                elif st == "IMPULSE_DOWN": short_score += w * 0.7
            symbol_data["wt_composite_long"] = max(-100.0, min(100.0, long_score))
            symbol_data["wt_composite_short"] = max(-100.0, min(100.0, short_score))
            symbol_data["wt_composite_bias"] = "LONG" if long_score > short_score + 10 else ("SHORT" if short_score > long_score + 10 else "NEUTRAL")
            symbol_data["wt_composite_delta"] = round(long_score - short_score, 2)
        except Exception:
            pass

    def _inject_dc_moment(self, symbol: str, symbol_data: Dict[str, Any]) -> None:
        """Compute DC composite fields for stocks — same logic as ez_indicators."""
        tfs_all = ['5m', '15m', '1h', '4h', 'D']
        w, p = {}, {}
        cp = float(symbol_data.get('current_price') or 0)
        if cp <= 0: return
        for tf in tfs_all:
            dw = float(symbol_data.get(f'dc_width_{tf}') or 0)
            dp = float(symbol_data.get(f'dc_position_{tf}') or 0)
            if dp > 1.0: dp = dp / 100.0
            if dw <= 0 or dp < 0:
                dch = float(symbol_data.get(f'dc_high_{tf}') or 0)
                dcl = float(symbol_data.get(f'dc_low_{tf}') or 0)
                if dch > 0 and dcl > 0 and dch > dcl:
                    if dw <= 0: dw = ((dch - dcl) / dcl) * 100
                    if dp < 0: dp = max(0.0, min(1.0, (cp - dcl) / (dch - dcl)))
            if dw > 0: w[tf] = dw
            if 0 <= dp <= 1.0: p[tf] = dp
        if len(w) < 2 or len(p) < 2: return
        wc = w.get('D', 0) * 0.50 + w.get('4h', 0) * 0.30 + w.get('1h', 0) * 0.20
        ltf_w = sum(w.get(t, 0) for t in ['5m', '15m']) / max(1, sum(1 for t in ['5m', '15m'] if t in w))
        htf_w = sum(w.get(t, 0) for t in ['D', '4h', '1h']) / max(1, sum(1 for t in ['D', '4h', '1h'] if t in w))
        exp = ltf_w / htf_w if htf_w > 0 else 0.0
        hp = p.get('D', 0.5) * 0.5 + p.get('4h', 0.5) * 0.3 + p.get('1h', 0.5) * 0.2
        lp = p.get('5m', 0.5) * 0.5 + p.get('15m', 0.5) * 0.5
        trend = (hp - 0.5) * 2.0
        if trend > 0:
            pbd = max(0.0, hp - lp) / max(hp, 0.01)
        else:
            pbd = max(0.0, lp - hp) / max(1.0 - hp, 0.01)
        pbd = min(1.0, pbd)
        eb = min(1.3, max(1.0, exp * 0.65 + 0.35)) if exp > 1.0 else max(0.7, exp)
        moment = max(-100.0, min(100.0, trend * pbd * eb * 100.0))
        symbol_data['0dc_moment'] = round(moment, 1)
        symbol_data['0dc_width_composite'] = round(wc, 2)
        symbol_data['0dc_expansion'] = round(exp, 3)
        symbol_data['0dc_htf_pos'] = round(hp, 3)
        symbol_data['0dc_ltf_pos'] = round(lp, 3)

    def _update_master_timestamp(self, symbol_data: Dict[str, Any]) -> None:
        latest = None
        ts_1m_str = symbol_data.get("timestamp_1m")
        if ts_1m_str:
            try:
                if ts_1m_str.endswith("Z"): ts_1m_str = ts_1m_str.replace("Z", "+00:00")
                latest = datetime.fromisoformat(ts_1m_str)
            except Exception: pass
            
        for tf in TIMEFRAMES.keys():
            if tf == "1m": continue 
            ts_str = symbol_data.get(f"timestamp_{tf}")
            if not ts_str:
                continue
            try:
                if ts_str.endswith("Z"):
                    ts_str = ts_str.replace("Z", "+00:00")
                ts = datetime.fromisoformat(ts_str)
            except Exception:
                continue
            if latest is None or ts > latest:
                latest = ts
        if latest is not None:
            symbol_data["timestamp"] = isoformat(latest)
            if "timestamp_1m" not in symbol_data:
                symbol_data["timestamp_1m"] = symbol_data["timestamp"]
    
    def _refresh_sentiment(self) -> None:
        entries: List[Dict[str, Any]] =[]
        local_sum = 0.0
        local_count = 0
        local_max_abs = 0.0

        for symbol, values in self.data.items():
            if not isinstance(values, dict): continue
            
            def g(k, default=0.0):
                v = values.get(k)
                if v is None: return default
                try: return float(v)
                except Exception: return default

            current_price = g("current_price", 0.0)
            
            wt_diff_5m = g("wt1_5m") - g("wt2_5m")
            wt_diff_15m = g("wt1_15m") - g("wt2_15m")
            wt_diff_1h = g("wt1_1h") - g("wt2_1h")
            wt_score = (wt_diff_5m * 1.0) + (wt_diff_15m * 1.5) + (wt_diff_1h * 2.0)

            hull_score = 0.0
            if values.get("t_up_5m") is True: hull_score += 10.0
            elif values.get("t_up_5m") is False: hull_score -= 10.0
            if values.get("t_up_15m") is True: hull_score += 15.0
            elif values.get("t_up_15m") is False: hull_score -= 15.0
            if values.get("t_up_1h") is True: hull_score += 25.0
            elif values.get("t_up_1h") is False: hull_score -= 25.0

            sma_score = 0.0
            sma_1m = g("sma_200_1m")
            if current_price > 0 and sma_1m > 0:
                dist_pct = (current_price - sma_1m) / sma_1m
                sma_score = dist_pct * 2000.0 

            k5, d5 = g("k_5m", 50), g("d_5m", 50)
            k15, d15 = g("k_15m", 50), g("d_15m", 50)
            stoch_mix = ((k5 - d5) * 1.0) + ((k15 - d15) * 1.5)
            
            ha_score = 0.0
            ha_colors =[values.get(f"ha_{tf}") for tf in["5m", "15m", "1h"]]
            ha_score += ha_colors.count("green") * 5.0
            ha_score -= ha_colors.count("red") * 5.0

            rv5 = g("relative_volume_5m", 1.0)
            rv15 = g("relative_volume_15m", 1.0)
            rv_input = min(3.0, (rv5 * 0.6 + rv15 * 0.4))
            vol_base = (rv_input - 1.0) * 20.0 

            vol_unit = g("volume_15m", 0.0) 
            liquidity_mult = 1.0
            if current_price > 0 and vol_unit > 0:
                usd_vol = vol_unit * current_price
                try:
                    log_val = math.log10(usd_vol)
                    liquidity_mult = 1.0 + ((log_val - 5.7) * 0.2)
                    liquidity_mult = max(0.5, min(2.5, liquidity_mult)) 
                except Exception: pass

            direction = 1.0 if values.get("ha_15m") == "green" else -1.0
            vol_score = direction * abs(vol_base) * liquidity_mult

            lr_score = 0.0
            if current_price > 0:
                pct_slope_5m = (g("lr_trend_5m") / current_price) * 100 
                pct_slope_15m = (g("lr_trend_15m") / current_price) * 100
                lr_score = (pct_slope_5m * 1000.0) + (pct_slope_15m * 1500.0)

            raw_sum = wt_score + hull_score + sma_score + stoch_mix + ha_score + vol_score + lr_score
            raw_sum += self.final_scores.get(symbol, 0.0) * 0.5 

            entries.append({"symbol": symbol, "raw": raw_sum})
            local_sum += raw_sum
            local_count += 1
            if abs(raw_sum) > local_max_abs:
                local_max_abs = abs(raw_sum)

        if local_max_abs < 1.0: local_max_abs = 1.0
        
        raw_global_avg = (local_sum / local_count) if local_count > 0 else 0.0
        global_score_normalized = (raw_global_avg / local_max_abs) * 100.0
        
        prev_ema = None
        first_sym_data = next(iter(self.data.values()), {}) if self.data else {}
        if isinstance(first_sym_data, dict):
            p = first_sym_data.get("0market_sentiment_score_ema")
            if p is not None: prev_ema = float(p)
            
        if prev_ema is None: ema_val = global_score_normalized
        else:
            alpha = 2.0 / 51.0 
            ema_val = (global_score_normalized - prev_ema) * alpha + prev_ema

        ranking_points = {}
        sorted_entries = sorted(entries, key=lambda x: x["raw"], reverse=True)
        top_symbols = [x["symbol"] for x in sorted_entries[:10]]
        bottom_symbols =[x["symbol"] for x in sorted_entries[-10:]]
        total_symbols = len(sorted_entries)

        for rank, entry in enumerate(sorted_entries, start=1):
            symbol = entry["symbol"]
            raw = entry["raw"]
            values = self.data[symbol]

            norm_val = (raw / local_max_abs) * 100.0
            
            prev_val = values.get("0market_sentiment_local", norm_val)
            velocity = norm_val - prev_val
            
            values["0market_sentiment_local"] = norm_val
            values["0market_sentiment_score"] = global_score_normalized
            values["0market_sentiment_score_ema"] = ema_val
            values["0sentiment_rank"] = rank
            values["0is_top_sentiment"] = symbol in top_symbols
            values["0is_bottom_sentiment"] = symbol in bottom_symbols
            values["0sentiment_classification"] = self._classify_sentiment(norm_val)
            values["velocity"] = velocity

            if total_symbols > 1:
                points = max(0.0, 100.0 - ((rank - 1) / (total_symbols - 1)) * 100.0)
            else: points = 100.0
            
            values["0ranking_points"] = round(points, 4)
            values["0ranking_points_global"] = round(points, 4)
            ranking_points[symbol] = points

        self.ranking_scores = dict(sorted(ranking_points.items(), key=lambda kv: kv[1], reverse=True))
        
        try:
            self.config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(self.config.DATA_DIR / "sentiment_top_20.json", "w") as f:
                json.dump([x["symbol"] for x in sorted_entries[:20]], f)
            with open(self.config.DATA_DIR / "sentiment_bottom_20.json", "w") as f:
                json.dump([x["symbol"] for x in sorted_entries[-20:]], f)
        except Exception: pass

    def _classify_sentiment(self, value: float) -> str:
        if value > 75:
            return "EXTREME_BULLISH"
        if value > 50:
            return "STRONG_BULLISH"
        if value > 25:
            return "MODERATE_BULLISH"
        if value > -25:
            return "NEUTRAL"
        if value > -50:
            return "MODERATE_BEARISH"
        if value > -75:
            return "STRONG_BEARISH"
        return "EXTREME_BEARISH"
    
    async def _mark_dirty(self) -> None:
        self._last_dirty = utc_now()
        self._dirty = True
    
    def cleanup_old_indicator_files(self):
        try:
            now = datetime.now(timezone.utc)
            cutoff_15m = now - timedelta(minutes=15)
            cutoff_7d = now - timedelta(days=7)
            cutoff_30d = now - timedelta(days=30)

            indicator_files = sorted(
                self.config.DATA_DIR.glob("tradier_indicators_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )

            latest_file = self.config.DATA_DIR / "tradier_indicators_latest.json"
            indicator_files =[f for f in indicator_files if f.name != "tradier_indicators_latest.json"]

            files_to_keep = set()
            per_hour: Dict[str, List[Path]] = {}
            per_4hour: Dict[str, List[Path]] = {}
            per_day: Dict[str, List[Path]] = {}

            for file_path in indicator_files:
                try:
                    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
                    if file_mtime >= cutoff_15m:
                        files_to_keep.add(file_path)
                    elif file_mtime >= cutoff_7d:
                        # Last 7 days: keep one per hour
                        hour_key = file_mtime.strftime("%Y%m%d_%H")
                        per_hour.setdefault(hour_key,[]).append(file_path)
                    elif file_mtime >= cutoff_30d:
                        # 7–30 days: keep one per 4 hours
                        four_hour_slot = file_mtime.hour // 4
                        four_hour_key = f"{file_mtime.strftime('%Y%m%d')}_{four_hour_slot}"
                        per_4hour.setdefault(four_hour_key,[]).append(file_path)
                    else:
                        # Older than 30 days: keep one per day
                        day_key = file_mtime.strftime("%Y%m%d")
                        per_day.setdefault(day_key,[]).append(file_path)
                except Exception:
                    continue

            for files in per_hour.values():
                if files: files_to_keep.add(max(files, key=lambda p: p.stat().st_mtime))
            for files in per_4hour.values():
                if files: files_to_keep.add(max(files, key=lambda p: p.stat().st_mtime))
            for files in per_day.values():
                if files: files_to_keep.add(max(files, key=lambda p: p.stat().st_mtime))
            
            deleted_count = 0
            for file_path in indicator_files:
                if file_path not in files_to_keep and file_path != latest_file:
                    try:
                        file_path.unlink()
                        deleted_count += 1
                    except Exception:
                        pass
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old indicator files")
        except Exception as e:
            logger.error(f"Error cleaning up old indicator files: {e}")
    
    async def _broadcast_to_redis(self, indicators: Dict[str, Dict], payload_bytes: bytes):
        l_file = self.config.DATA_DIR / "tradier_indicators_latest.json"
        def _write_file(path: Path):
            tmp = path.with_name(f".{path.name}.tmp")
            try:
                with open(tmp, 'wb') as f:
                    if isinstance(payload_bytes, str):
                        f.write(payload_bytes.encode('utf-8'))
                    else:
                        f.write(payload_bytes)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            except Exception as e:
                logger.error(f"File write error: {e}")
                if tmp.exists():
                    try: os.remove(tmp)
                    except Exception: pass
                    
        await asyncio.to_thread(_write_file, l_file)
        
        if self.redis_manager:
            # We strictly pass the DICT to set() so simple_redis_manager formats it, preventing double encoding
            await self.redis_manager.set("tradier_indicators_latest", indicators)
            
            payload_str = payload_bytes.decode('utf-8') if isinstance(payload_bytes, bytes) else payload_bytes
            await self.redis_manager.publish("tradier_indicators_channel", payload_str)
            
    def _enrich_backtest_winners(self) -> None:
        """Enrich self.data with Clenow/SMFI/Minervini/Connors indicators from daily klines on disk."""
        enriched = 0
        for symbol in self.symbols:
            try:
                d_path = self.cache_dir / f"{symbol}_D.json"
                if not d_path.exists():
                    continue
                raw = json.loads(d_path.read_text())
                bars = raw if isinstance(raw, list) else raw.get('bars', raw.get('candles', []))
                if not bars or len(bars) < 100:
                    continue
                bars = bars[-300:]
                closes = [float(b.get('close') or b.get('c') or 0) for b in bars if float(b.get('close') or b.get('c') or 0) > 0]
                opens = [float(b.get('open') or b.get('o') or 0) for b in bars if float(b.get('close') or b.get('c') or 0) > 0]
                highs = [float(b.get('high') or b.get('h') or 0) for b in bars if float(b.get('close') or b.get('c') or 0) > 0]
                lows = [float(b.get('low') or b.get('l') or 0) for b in bars if float(b.get('close') or b.get('c') or 0) > 0]
                vols = [float(b.get('volume') or b.get('v') or 0) for b in bars if float(b.get('close') or b.get('c') or 0) > 0]
                if len(closes) < 100:
                    continue
                s_data = self.data.get(symbol, {})
                clenow = compute_clenow_score(closes, 90)
                if clenow:
                    s_data.update(clenow)
                sepa = compute_minervini_sepa(closes, highs, lows, vols)
                if sepa:
                    s_data.update(sepa)
                smfi = compute_smfi(opens, highs, lows, closes, 20)
                if smfi:
                    s_data.update(smfi)
                self.data[symbol] = s_data
                enriched += 1
            except Exception:
                continue
        if enriched > 0:
            logger.info(f"📊 [BACKTEST_WINNERS] Enriched {enriched}/{len(self.symbols)} symbols with Clenow/SMFI/Minervini")

    async def _save_data(self) -> None:
        async with self._save_lock:
            self._refresh_sentiment()
            safe: Dict[str, Dict[str, Any]] = {}
            symbols_with_data = 0
            
            now_utc = utc_now()
            for symbol, values in self.data.items():
                if symbol not in self.symbol_set: continue
                if not isinstance(values, dict): continue
                 
                dt_upd = safe_datetime(values.get('1m_updated_at'))
                if not dt_upd or (now_utc - dt_upd).total_seconds() > 1200.0:
                    continue
                _TF_MAX_AGE = {"1m": 90.0, "5m": 540.0, "15m": 1200.0, "1h": 1200.0, "4h": 1200.0, "D": 1200.0}
                stale_tfs = {tf for tf, max_age in _TF_MAX_AGE.items() if (lambda d: not d or (now_utc - d).total_seconds() > max_age)(safe_datetime(values.get(f"timestamp_{tf}")))}
                filtered: Dict[str, Any] = {}
                for key, value in values.items():
                    tf_match = next((tf for tf in ("1m", "5m", "15m", "1h", "4h", "D") if key.endswith(f"_{tf}")), None)
                    if tf_match in stale_tfs: continue
                    cleaned = clean_nans(value)
                    if cleaned is not None:
                        if hasattr(cleaned, "item") and not isinstance(cleaned, (list, dict)): cleaned = cleaned.item()
                        filtered[key] = cleaned
                if filtered:
                    safe[symbol] = OrderedDict(sorted(filtered.items()))
                    symbols_with_data += 1
            
            incomplete_count = len(self.symbols) - symbols_with_data
            if incomplete_count > 0:
                logger.info(f"💾 Saving {symbols_with_data} symbols (stale/incomplete: {incomplete_count})")
            
            try:
                json_bytes = json_dumps(safe)
            except Exception as e:
                logger.error(f"❌ orjson failure in _save_data: {e}. Falling back to standard json.")
                def np_default(obj):
                    if hasattr(obj, "item"): return obj.item()
                    return str(obj)
                json_str = json.dumps(safe, indent=2, default=np_default)
                json_bytes = json_str.encode('utf-8')
                
            payload_hash = hashlib.sha1(json_bytes if isinstance(json_bytes, bytes) else json_bytes.encode('utf-8')).hexdigest()
            force_save = self._last_payload_hash is None or payload_hash != self._last_payload_hash
            if not force_save and not self._save_due:
                logger.debug(f"⏸️ Skipping save: data unchanged (hash match)")
                self._dirty = False
                return
                
            # Pass BOTH to cleanly persist and safely Redis store without double encoding
            await self._broadcast_to_redis(safe, json_bytes)
            self._last_payload_hash = payload_hash
            self._dirty = False
            self._save_due = False
    
    async def _save_loop(self) -> None:
        save_interval = float(getattr(config, "INDICATORS_SAVE_INTERVAL_SECONDS", 30.0))
        cleanup_interval = 300.0
        last_periodic_save = time.time()
        last_cleanup = time.time()
        while not self._shutdown.is_set():
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=1.0)
                break
            except asyncio.TimeoutError:
                pass
            now = time.time()
            if any(self.data.get(sym, {}) for sym in self.symbols) and now - last_periodic_save >= save_interval:
                self._save_due = True
                self._dirty = True
                last_periodic_save = now
            if self._save_due or self._dirty:
                await self._save_data()
                self._save_due = False
                self._dirty = False
            if now - last_cleanup >= cleanup_interval:
                self.cleanup_old_indicator_files()
                last_cleanup = now
    
    
    async def run(self) -> None:
        logger.info(f"Starting Tradier indicator orchestrator for {len(self.symbols)} symbols")
        await self.api_client.connect()
        self.price_cacheman.redis_manager = await get_simple_redis_manager()
        self.redis_manager = await get_simple_redis_manager()
        
        latest_file = self.config.DATA_DIR / "tradier_indicators_latest.json"
        if not latest_file.exists():
            self.save_indicators_to_json({})
            logger.info("Created initial empty indicators file")
        
        indicator_files_count = len(list(self.config.DATA_DIR.glob("tradier_indicators_*.json")))
        if indicator_files_count > 100:
            logger.info(f"Found {indicator_files_count} indicator files, running cleanup...")
            self.cleanup_old_indicator_files()
        
        # 2026-08-14 FIX: initial <40s like ez — per-TF not bundle clip 30min. Was await run_cycle() (106 bundles ~30min)
        for tf in self._schedule_order:
            await self._process_timeframe(tf, force=False)
        self._save_due = True
        await self._save_data()
        tasks = [asyncio.create_task(self._schedule_loop()), asyncio.create_task(self._save_loop())]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self._shutdown.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            try:
                # Only attempt to gather tasks if the loop is verified open
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    await asyncio.gather(*tasks, return_exceptions=True)
            except RuntimeError:
                # Loop is already dying/closed; suppress error and exit cleanly
                pass



        # tasks =[asyncio.create_task(self._schedule_loop()), asyncio.create_task(self._save_loop())]
        # try:
        #     await asyncio.gather(*tasks)
        # except asyncio.CancelledError:
        #     pass
        # finally:
        #     self._shutdown.set()
        #     for task in tasks:
        #         task.cancel()
        #     await asyncio.gather(*tasks, return_exceptions=True)
        #     await self.api_client.close()

def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

def signal_handler(signum, frame, orchestrator=None):
    logger.info(f"Received signal {signum}, scheduling graceful shutdown via event loop...")
    if orchestrator and hasattr(orchestrator, '_shutdown'):
        # Safely flag the asyncio.Event loop from outside the async loop context
        orchestrator._shutdown.set()
    else:
        # Fallback if orchestrator isn't initialized yet
        sys.exit(0)

async def main():
    import signal
    from functools import partial
    orchestrator = TradierIndicatorOrchestrator()
    sig_handler_with_ctx = partial(signal_handler, orchestrator=orchestrator)
    
    signal.signal(signal.SIGINT, sig_handler_with_ctx)
    signal.signal(signal.SIGTERM, sig_handler_with_ctx)
    
    try:
        await orchestrator.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user via keyboard")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        # Guard against closing a client that wasn't successfully opened
        if hasattr(orchestrator, 'api_client') and orchestrator.api_client:
            await orchestrator.api_client.close()

# ============================================================================
# CONSENSUS INDICATORS — migrated from tradier_indicators_extra.py 2026-04-26
# YouTube Consensus Indicators — VWAP, 9/21 EMA, Keltner, TTM Squeeze, ORB,
# Episodic Pivot, Clenow, Minervini SEPA, SMFI. RSI primitives BANNED.
# ============================================================================
def compute_vwap_from_bars(bars: List[Dict], reset_daily: bool = True) -> Dict[str, float]:
    """Compute session-anchored VWAP + bands from intraday bars (5m or 15m).
    Returns: {vwap, vwap_upper1, vwap_lower1, vwap_upper2, vwap_lower2, vwap_distance_pct}
    """
    if not bars or len(bars) < 2:
        return {}
    try:
        highs, lows, closes, volumes = [], [], [], []
        for b in bars:
            h = float(b.get('high') or b.get('h') or 0)
            l = float(b.get('low') or b.get('l') or 0)
            c = float(b.get('close') or b.get('c') or 0)
            v = float(b.get('volume') or b.get('v') or 0)
            if c <= 0 or v <= 0:
                continue
            highs.append(h)
            lows.append(l)
            closes.append(c)
            volumes.append(v)
        if len(closes) < 2:
            return {}
        tp = np.array([(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)])
        vol = np.array(volumes)
        cum_tp_vol = np.cumsum(tp * vol)
        cum_vol = np.cumsum(vol)
        vwap_arr = cum_tp_vol / np.maximum(cum_vol, 1e-10)
        vwap = float(vwap_arr[-1])
        if vwap <= 0:
            return {}
        sq_diff = np.cumsum(((tp - vwap_arr) ** 2) * vol)
        variance = sq_diff / np.maximum(cum_vol, 1e-10)
        std = float(np.sqrt(max(variance[-1], 0)))
        current_price = closes[-1]
        dist_pct = ((current_price - vwap) / vwap) * 100.0 if vwap > 0 else 0
        return {
            "vwap": round(vwap, 4),
            "vwap_upper1": round(vwap + std, 4),
            "vwap_lower1": round(vwap - std, 4),
            "vwap_upper2": round(vwap + 2 * std, 4),
            "vwap_lower2": round(vwap - 2 * std, 4),
            "vwap_distance_pct": round(dist_pct, 4),
        }
    except Exception as e:
        logger.debug(f"[VWAP] Error: {e}")
        return {}


def compute_ema(values: List[float], period: int) -> float:
    """Compute EMA of the last `period` values. Returns latest EMA value."""
    if not values or len(values) < period:
        return 0.0
    mult = 2.0 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * mult + ema * (1.0 - mult)
    return ema


def compute_ema_9_21(closes: List[float]) -> Dict[str, float]:
    """Compute EMA 9 and EMA 21 from close prices."""
    if not closes or len(closes) < 21:
        return {}
    ema9 = compute_ema(closes, 9)
    ema21 = compute_ema(closes, 21)
    if ema21 <= 0:
        return {}
    dist = ((ema9 - ema21) / ema21) * 100.0
    return {
        "ema_9": round(ema9, 4),
        "ema_21": round(ema21, 4),
        "ema_9_above_21": 1.0 if ema9 > ema21 else 0.0,
        "ema_9_21_dist_pct": round(dist, 4),
    }


def compute_keltner_channels(closes: List[float], highs: List[float], lows: List[float], ema_period: int = 20, atr_period: int = 20, atr_mult: float = 1.5) -> Dict[str, float]:
    """Compute Keltner Channels (EMA ± mult * ATR)."""
    if not closes or len(closes) < max(ema_period, atr_period):
        return {}
    try:
        mid = compute_ema(closes, ema_period)
        trs = []
        for j in range(1, len(closes)):
            h = highs[j] if j < len(highs) else closes[j]
            l = lows[j] if j < len(lows) else closes[j]
            pc = closes[j - 1]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        if len(trs) < atr_period:
            return {}
        atr = compute_ema(trs[-atr_period * 3:], atr_period) if len(trs) >= atr_period else sum(trs[-atr_period:]) / atr_period
        return {
            "kc_upper": round(mid + atr_mult * atr, 4),
            "kc_middle": round(mid, 4),
            "kc_lower": round(mid - atr_mult * atr, 4),
            "kc_atr": round(atr, 4),
        }
    except Exception:
        return {}


def detect_squeeze(bb_upper: float, bb_lower: float, kc_upper: float, kc_lower: float) -> Dict[str, Any]:
    """TTM Squeeze detection: BB inside KC = squeeze on."""
    if not all([bb_upper, bb_lower, kc_upper, kc_lower]):
        return {"squeeze_on": False}
    squeeze_on = bb_upper < kc_upper and bb_lower > kc_lower
    return {"squeeze_on": squeeze_on}


def detect_opening_range(bars_5m: List[Dict], market_open_minutes: int = 15) -> Dict[str, float]:
    """Detect Opening Range from first N minutes of 5m bars."""
    if not bars_5m or len(bars_5m) < 2:
        return {}
    try:
        orb_bars_needed = max(1, market_open_minutes // 5)
        today_bars = []
        for b in bars_5m:
            ts = b.get('timestamp') or b.get('time') or b.get('open_time') or b.get('t') or 0
            if isinstance(ts, str):
                try:
                    ts = isoparse(ts).timestamp()
                except Exception:
                    ts = 0
            elif isinstance(ts, (int, float)) and ts > 1e12:
                ts = ts / 1000.0
            today_bars.append({**b, '_ts': float(ts)})
        if not today_bars:
            return {}
        today_bars.sort(key=lambda x: x['_ts'])
        orb_subset = today_bars[:orb_bars_needed]
        if len(orb_subset) < orb_bars_needed:
            return {}
        orb_high = max(float(b.get('high') or b.get('h') or 0) for b in orb_subset)
        orb_low = min(float(b.get('low') or b.get('l') or b.get('high') or 999999) for b in orb_subset)
        if orb_high <= 0 or orb_low <= 0 or orb_high <= orb_low:
            return {}
        return {
            "orb_high": round(orb_high, 4),
            "orb_low": round(orb_low, 4),
            "orb_range": round(orb_high - orb_low, 4),
            "orb_midpoint": round((orb_high + orb_low) / 2.0, 4),
        }
    except Exception as e:
        logger.debug(f"[ORB] Error: {e}")
        return {}


def detect_episodic_pivot(daily_bars: List[Dict], min_gap_pct: float = 5.0, min_vol_mult: float = 3.0, max_consolidation_days: int = 8, max_retrace_pct: float = 25.0) -> Dict[str, Any]:
    """Qullamaggie Episodic Pivot: gap up 5%+ on 3x volume, then tight consolidation, then breakout."""
    if not daily_bars or len(daily_bars) < 10:
        return {"ep_detected": False}
    try:
        closes = [float(b.get('close') or b.get('c') or 0) for b in daily_bars]
        volumes = [float(b.get('volume') or b.get('v') or 0) for b in daily_bars]
        opens = [float(b.get('open') or b.get('o') or 0) for b in daily_bars]
        highs = [float(b.get('high') or b.get('h') or 0) for b in daily_bars]
        lows = [float(b.get('low') or b.get('l') or 0) for b in daily_bars]
        if any(c <= 0 for c in closes[-10:]):
            return {"ep_detected": False}
        avg_vol_20 = np.mean(volumes[-25:-5]) if len(volumes) >= 25 else np.mean(volumes[:-5]) if len(volumes) > 5 else 0
        if avg_vol_20 <= 0:
            return {"ep_detected": False}
        for gap_idx in range(-15, -2):
            if abs(gap_idx) >= len(closes):
                continue
            prev_close = closes[gap_idx - 1]
            gap_open = opens[gap_idx]
            gap_close = closes[gap_idx]
            gap_vol = volumes[gap_idx]
            if prev_close <= 0:
                continue
            gap_pct = ((gap_open - prev_close) / prev_close) * 100.0
            vol_mult = gap_vol / avg_vol_20 if avg_vol_20 > 0 else 0
            if abs(gap_pct) >= min_gap_pct and vol_mult >= min_vol_mult:
                is_bullish = gap_pct > 0
                gap_high = highs[gap_idx]
                post_bars = list(range(gap_idx + 1, 0)) if gap_idx < -1 else []
                if not post_bars or len(post_bars) < 2:
                    continue
                if len(post_bars) > max_consolidation_days:
                    post_bars = post_bars[:max_consolidation_days]
                post_highs = [highs[j] for j in post_bars]
                post_lows = [lows[j] for j in post_bars]
                post_closes = [closes[j] for j in post_bars]
                if is_bullish:
                    max_retrace = ((gap_high - min(post_lows)) / (gap_high - prev_close)) * 100.0 if (gap_high - prev_close) > 0 else 100
                    if max_retrace <= max_retrace_pct:
                        breakout_level = max(post_highs)
                        current_price = closes[-1]
                        if current_price >= breakout_level * 0.99:
                            return {
                                "ep_detected": True,
                                "ep_gap_pct": round(gap_pct, 2),
                                "ep_vol_mult": round(vol_mult, 2),
                                "ep_consolidation_days": len(post_bars),
                                "ep_breakout_level": round(breakout_level, 4),
                                "ep_direction": "LONG",
                                "ep_max_retrace_pct": round(max_retrace, 2),
                            }
                else:
                    gap_low = lows[gap_idx]
                    max_retrace = ((max(post_highs) - gap_low) / (prev_close - gap_low)) * 100.0 if (prev_close - gap_low) > 0 else 100
                    if max_retrace <= max_retrace_pct:
                        breakout_level = min(post_lows)
                        current_price = closes[-1]
                        if current_price <= breakout_level * 1.01:
                            return {
                                "ep_detected": True,
                                "ep_gap_pct": round(gap_pct, 2),
                                "ep_vol_mult": round(vol_mult, 2),
                                "ep_consolidation_days": len(post_bars),
                                "ep_breakout_level": round(breakout_level, 4),
                                "ep_direction": "SHORT",
                                "ep_max_retrace_pct": round(max_retrace, 2),
                            }
        return {"ep_detected": False}
    except Exception as e:
        logger.debug(f"[EPISODIC_PIVOT] Error: {e}")
        return {"ep_detected": False}


def compute_clenow_score(closes: List[float], lookback: int = 90) -> Dict[str, float]:
    """Clenow: annualized exponential regression slope * R². Sharpe 5.17 on 2yr backtest."""
    if len(closes) < lookback + 5:
        return {}
    try:
        arr = np.array(closes[-lookback:], dtype=np.float64)
        if np.any(arr <= 0):
            return {}
        y = np.log(arr)
        x = np.arange(lookback, dtype=np.float64)
        mx, my = np.mean(x), np.mean(y)
        xc = x - mx
        yc = y - my
        ssxx = np.dot(xc, xc)
        if ssxx == 0:
            return {}
        b = np.dot(xc, yc) / ssxx
        slope_ann = (np.exp(b * 252) - 1) * 100
        ss_res = np.sum((yc - b * xc) ** 2)
        ss_tot = np.sum(yc ** 2)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0
        score = slope_ann * r2
        return {
            "clenow_slope": round(slope_ann, 2),
            "clenow_r2": round(r2, 4),
            "clenow_score": round(score, 2),
        }
    except Exception:
        return {}


def compute_minervini_sepa(closes: List[float], highs: List[float], lows: List[float], volumes: List[float]) -> Dict[str, Any]:
    """Minervini SEPA trend template. Sharpe 3.68 on 2yr backtest."""
    n = len(closes)
    if n < 252:
        return {}
    try:
        c = np.array(closes)
        h = np.array(highs)
        v = np.array(volumes)
        sma50 = float(np.mean(c[-50:]))
        sma150 = float(np.mean(c[-150:])) if n >= 150 else float(np.mean(c))
        sma200 = float(np.mean(c[-200:])) if n >= 200 else float(np.mean(c))
        sma200_prev = float(np.mean(c[-222:-22])) if n >= 222 else sma200
        price = float(c[-1])
        high_52w = float(np.max(h[-252:]))
        low_52w = float(np.min(c[-252:]))
        avg_vol = float(np.mean(v[-20:])) if n >= 20 else 1
        cur_vol = float(v[-1])
        cond1 = price > sma150 and price > sma200
        cond2 = sma150 > sma200
        cond3 = sma200 > sma200_prev
        cond4 = price >= low_52w * 1.25 if low_52w > 0 else False
        cond5 = price >= high_52w * 0.75 if high_52w > 0 else False
        cond6 = price > sma50
        vol_surge = cur_vol > avg_vol * 1.5 if avg_vol > 0 else False
        score = sum([cond1, cond2, cond3, cond4, cond5, cond6])
        sepa_pass = score >= 5 and vol_surge
        return {
            "sepa_pass": sepa_pass,
            "sepa_score": score,
            "sepa_vol_surge": vol_surge,
            "sepa_price_vs_sma150": round((price / sma150 - 1) * 100, 2) if sma150 > 0 else 0,
            "sepa_pct_from_52w_high": round((price / high_52w - 1) * 100, 2) if high_52w > 0 else 0,
        }
    except Exception:
        return {}


def compute_smfi(opens: List[float], highs: List[float], lows: List[float], closes: List[float], period: int = 20) -> Dict[str, float]:
    """Smart Money Flow Index. Sharpe 4.20 on 2yr backtest. Divergence = signal."""
    n = len(closes)
    if n < period + 5:
        return {}
    try:
        o = np.array(opens)
        h = np.array(highs)
        l = np.array(lows)
        c = np.array(closes)
        midrange = (h + l) / 2.0
        smfi = np.zeros(n)
        for i in range(1, n):
            smart = c[i] - midrange[i]
            retail = midrange[i] - o[i]
            smfi[i] = smfi[i - 1] + smart - retail
        smfi_sma = float(np.mean(smfi[-period:])) if n >= period else float(smfi[-1])
        price_sma = float(np.mean(c[-period:])) if n >= period else float(c[-1])
        smfi_val = float(smfi[-1])
        price_val = float(c[-1])
        bull_div = price_val < price_sma and smfi_val > smfi_sma
        bear_div = price_val > price_sma and smfi_val < smfi_sma
        return {
            "smfi": round(smfi_val, 4),
            "smfi_sma": round(smfi_sma, 4),
            "smfi_bull_divergence": bull_div,
            "smfi_bear_divergence": bear_div,
            "smfi_signal": "LONG" if bull_div else ("SHORT" if bear_div else "NEUTRAL"),
        }
    except Exception:
        return {}


def _consensus_load_klines(klines_cache_dir: Path, symbol: str, tf: str) -> List[Dict]:
    """Load klines from disk cache."""
    paths = [
        klines_cache_dir / f"{symbol.upper()}_{tf}.json",
        klines_cache_dir / f"{symbol.upper()}_{tf}.json.bak",
    ]
    for p in paths:
        if p.exists():
            try:
                raw = json.loads(p.read_text())
                bars = raw if isinstance(raw, list) else raw.get('bars', raw.get('candles', []))
                if isinstance(bars, list) and len(bars) > 0:
                    return bars
            except Exception:
                continue
    return []


def compute_extra_indicators(symbol: str, klines_cache_dir: Path) -> Dict[str, Any]:
    """Compute ALL extra indicators for a symbol.
    Reads klines from disk cache. Returns enriched dict to merge into indicators.
    """
    result = {}
    bars_5m = _consensus_load_klines(klines_cache_dir, symbol, "5m")
    if bars_5m and len(bars_5m) >= 21:
        closes_5m = [float(b.get('close') or b.get('c') or 0) for b in bars_5m if float(b.get('close') or b.get('c') or 0) > 0]
        highs_5m = [float(b.get('high') or b.get('h') or 0) for b in bars_5m if float(b.get('close') or b.get('c') or 0) > 0]
        lows_5m = [float(b.get('low') or b.get('l') or 0) for b in bars_5m if float(b.get('close') or b.get('c') or 0) > 0]
        today_bars = bars_5m[-78:] if len(bars_5m) >= 78 else bars_5m
        vwap_data = compute_vwap_from_bars(today_bars)
        if vwap_data:
            result.update(vwap_data)
        ema_data_5m = compute_ema_9_21(closes_5m)
        if ema_data_5m:
            result.update({f"{k}_5m": v for k, v in ema_data_5m.items()})
        kc_5m = compute_keltner_channels(closes_5m, highs_5m, lows_5m)
        if kc_5m:
            result.update({f"{k}_5m": v for k, v in kc_5m.items()})
        orb_data = detect_opening_range(bars_5m)
        if orb_data:
            result.update(orb_data)
    bars_15m = _consensus_load_klines(klines_cache_dir, symbol, "15m")
    if bars_15m and len(bars_15m) >= 21:
        closes_15m = [float(b.get('close') or b.get('c') or 0) for b in bars_15m if float(b.get('close') or b.get('c') or 0) > 0]
        ema_data_15m = compute_ema_9_21(closes_15m)
        if ema_data_15m:
            result.update({f"{k}_15m": v for k, v in ema_data_15m.items()})
    bars_1h = _consensus_load_klines(klines_cache_dir, symbol, "1h")
    if bars_1h and len(bars_1h) >= 20:
        closes_1h = [float(b.get('close') or b.get('c') or 0) for b in bars_1h if float(b.get('close') or b.get('c') or 0) > 0]
        highs_1h = [float(b.get('high') or b.get('h') or 0) for b in bars_1h if float(b.get('close') or b.get('c') or 0) > 0]
        lows_1h = [float(b.get('low') or b.get('l') or 0) for b in bars_1h if float(b.get('close') or b.get('c') or 0) > 0]
        kc_1h = compute_keltner_channels(closes_1h, highs_1h, lows_1h)
        if kc_1h:
            result.update({f"{k}_1h": v for k, v in kc_1h.items()})
        ema_data_1h = compute_ema_9_21(closes_1h)
        if ema_data_1h:
            result.update({f"{k}_1h": v for k, v in ema_data_1h.items()})
    bars_d = _consensus_load_klines(klines_cache_dir, symbol, "D")
    if bars_d and len(bars_d) >= 15:
        ep_data = detect_episodic_pivot(bars_d)
        if ep_data:
            result.update(ep_data)
    if bars_d and len(bars_d) >= 100:
        closes_d = [float(b.get('close') or b.get('c') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        opens_d = [float(b.get('open') or b.get('o') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        highs_d = [float(b.get('high') or b.get('h') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        lows_d = [float(b.get('low') or b.get('l') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        vols_d = [float(b.get('volume') or b.get('v') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        clenow = compute_clenow_score(closes_d, 90)
        if clenow:
            result.update(clenow)
        sepa = compute_minervini_sepa(closes_d, highs_d, lows_d, vols_d)
        if sepa:
            result.update(sepa)
        smfi = compute_smfi(opens_d, highs_d, lows_d, closes_d, 20)
        if smfi:
            result.update(smfi)
    return result


def is_lunch_dead_zone() -> bool:
    """Returns True during 11:30-14:00 ET (15:30-18:00 UTC) — low volume chop zone."""
    try:
        now_utc = datetime.now(timezone.utc)
        et_offset = timezone(timedelta(hours=-4))
        now_et = now_utc.astimezone(et_offset)
        t = now_et.hour * 60 + now_et.minute
        return 690 <= t < 840
    except Exception:
        return False


def get_rvol_gate_for_strategy(strategy: str) -> float:
    """Returns minimum RVOL required for each strategy type."""
    gates = {
        "MOMENTUM": 1.5,
        "ORB": 1.5,
        "DC_BREAKOUT": 1.5,
        "SCALP": 1.0,
        "HODL": 0.5,
        "SATOSHIT": 0.3,
        "RSI2": 0.3,
        "GAP_FILL": 0.3,
        "ROTATION": 0.3,
        "EPISODIC_PIVOT": 2.0,
    }
    return gates.get(strategy, 0.5)


if __name__ == "__main__":
    asyncio.run(main())
