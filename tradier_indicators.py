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
import pytz
from dateutil.parser import isoparse

from config_tradier import TradierConfig
from tradier_api import TradierAPIClient
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

def filter_strict_market_hours(df: pd.DataFrame, is_fast_tf: bool) -> pd.DataFrame:
    if df.empty: return df
    df = df.copy()
    source_col = "timestamp" if "timestamp" in df.columns else "time"
    dt_series = pd.to_datetime(df[source_col], utc=True).dt.tz_convert(ET)
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
    df["close_time"] = pd.to_datetime(df[source_col], utc=True, errors='coerce')
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
    out["close_time"] = pd.to_datetime(out["close_time"], utc=True)
    out["timestamp"] = out["close_time"].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return out

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
        dts = pd.to_datetime(df[ts_col], utc=True, errors="coerce").dropna()
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
    y_fit = x_mean + slope * (x - x_mean)
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

def wavetrend(df: pd.DataFrame) -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
    WT_N1 = 10
    WT_N2 = 21
    if len(df) < max(WT_N1, WT_N2):
        return None, None
    typical = (df["high"] + df["low"] + df["close"]) / 3
    esa = typical.ewm(span=WT_N1, adjust=False).mean()
    d = (typical - esa).abs().ewm(span=WT_N1, adjust=False).mean()
    ci = (typical - esa) / (0.015 * d.replace(0, 1e-10))
    wt1 = ci.ewm(span=WT_N2, adjust=False).mean()
    wt2 = wt1.rolling(window=3, min_periods=1).mean()
    return wt1, wt2

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
STOCH_K = 3
STOCH_D = 5

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

ATR_LONG_LENGTH = 100
REL_VOL_LENGTH = 20
LINREG_LENGTH = 50

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
                df["timestamp_dt"] = pd.to_datetime(df["timestamp"], utc=True, errors='coerce')
            elif "close_time" in df.columns:
                df["timestamp_dt"] = pd.to_datetime(df["close_time"], utc=True, errors='coerce')
            elif "time" in df.columns:
                df["timestamp_dt"] = pd.to_datetime(df["time"], utc=True, errors='coerce')
            else:
                return result
        else:
            if not pd.api.types.is_datetime64_any_dtype(df["timestamp_dt"]):
                df["timestamp_dt"] = pd.to_datetime(df["timestamp_dt"], utc=True, errors='coerce')
        
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
        if use_mark:
            adjusted_df = with_mark_price(df, best_price, final_timestamp)
        else:
            adjusted_df = df
        
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
            result[f"stoch_k_{timeframe}"] = k_curr
            result[f"stoch_d_{timeframe}"] = d_curr if d_curr is not None else k_curr
            result[f"stoch_k_{timeframe}_prev"] = k_prev if k_prev is not None else k_curr
            result[f"stoch_d_{timeframe}_prev"] = d_prev if d_prev is not None else d_curr
            
        wt1, wt2 = wavetrend(adjusted_df)
        if wt1 is not None and wt2 is not None and not wt1.empty and not wt2.empty:
            wt1_val = float(wt1.iloc[-1]) if pd.notna(wt1.iloc[-1]) else None
            wt2_val = float(wt2.iloc[-1]) if pd.notna(wt2.iloc[-1]) else None
            if wt1_val is not None:
                result[f"wt1_{timeframe}"] = wt1_val
            if wt2_val is not None:
                result[f"wt2_{timeframe}"] = wt2_val
            if wt1_val is not None and wt2_val is not None:
                result[f"wt_score_{timeframe}"] = wt1_val - wt2_val
                prev_wt1 = float(wt1.iloc[-2]) if len(wt1) > 1 and pd.notna(wt1.iloc[-2]) else wt1_val
                prev_wt2 = float(wt2.iloc[-2]) if len(wt2) > 1 and pd.notna(wt2.iloc[-2]) else wt2_val
                if prev_wt1 <= prev_wt2 and wt1_val > wt2_val and wt1_val < -50:
                    result[f"wt_signal_{timeframe}"] = "BUY"
                elif prev_wt1 >= prev_wt2 and wt1_val < wt2_val and wt1_val > 50:
                    result[f"wt_signal_{timeframe}"] = "SELL"
                else:
                    result[f"wt_signal_{timeframe}"] = "NEUTRAL"
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
        if timeframe == "1h" and linearity is not None:
            result["linearity_1h"] = linearity
            result["slope_close_1h"] = slope
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
                        df["close_time"] = pd.to_datetime(df[ts_col], utc=True, errors='coerce')
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
                 first_ts = pd.to_datetime(df_lower['timestamp'].iloc[0], utc=True) if not df_lower.empty else pd.Timestamp.now(tz='UTC')
                 df_higher_dt = pd.to_datetime(df_higher['timestamp'], utc=True)
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
                            df_new['timestamp_dt'] = pd.to_datetime(df_new['time'], errors='coerce')
                            if not df_new.empty and df_new['timestamp_dt'].dt.tz is None:
                                df_new['timestamp_dt'] = df_new['timestamp_dt'].dt.tz_localize(ET, ambiguous='infer').dt.tz_convert('UTC')
                            else:
                                df_new['timestamp_dt'] = df_new['timestamp_dt'].dt.tz_convert('UTC')
                                
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

        # ONLY WRITE TO DISK ON NEW HISTORICAL FETCH — never shrink files
        for tf, df in bundle.items():
            if not df.empty:
                old_count = disk_counts.get(tf, 0)
                new_count = len(df)
                if old_count > 50 and new_count < old_count * 0.5:
                    logger.error(f"BLOCKED write {symbol}_{tf}: would shrink from {old_count} to {new_count} bars — refusing to destroy data")
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
            json_bytes = json_dumps(data)
            def _write():
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    existing_size = path.stat().st_size
                    new_size = len(json_bytes) if isinstance(json_bytes, bytes) else len(json_bytes.encode('utf-8'))
                    if existing_size > 1000 and new_size < existing_size * 0.3:
                        logger.error(f"BLOCKED write to {path.name}: new size {new_size} is <30% of existing {existing_size} — refusing to destroy data")
                        return
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
        self._schedule_order =["D", "4h", "1h", "15m", "5m", "3m", "1m"]
        self._shutdown = asyncio.Event()
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.cycle_semaphore = asyncio.Semaphore(4)
        ensure_directory(self.config.DATA_DIR)
        self._load_existing_data()
        self._init_state()
        self._http_semaphore = asyncio.Semaphore(15) 

    def _load_symbols(self) -> List[str]:
        collected: Set[str] = set()
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
            logger.info(f"✅ Saved indicators for {len(indicators)} symbols to latest JSON")
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
        logger.info(f"Update cycle starting for {len(self.symbols)} symbols (Env: {self.bar_manager.env})")
        all_prices = {}
        if self.redis_manager:
            try:
                prices_raw = await self.redis_manager.get("tradier_prices_latest")
                if isinstance(prices_raw, dict):
                    all_prices = prices_raw.get("data", prices_raw)
            except Exception as e:
                logger.warning(f"Failed to fetch prices for cycle: {e}")

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
                        await self._run_full(symbol, timeframe, df, close_ts, mark_price, state)
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
                    elif state.last_df is not None:
                        state.full_done = True
                except Exception as e:
                    logger.error(f"Error processing {symbol} {timeframe}: {e}")

        logger.info(f"[{timeframe}] Starting concurrent update for {len(self.symbols)} symbols...")
        tasks =[asyncio.create_task(process_symbol(sym)) for sym in self.symbols]
        if tasks:
            await asyncio.gather(*tasks)

        if timeframe == self._schedule_order[-1]:
            self._save_due = True
            
    async def _schedule_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await self.run_cycle()
                await asyncio.sleep(15)
            except Exception as e:
                logger.error(f"Schedule Loop Error: {e}")
                await asyncio.sleep(15)

    def _required_by_timeframe(self, timeframe: str) -> List[str]:
        base_indicators =[f"timestamp_{timeframe}", f"dc_high_{timeframe}", f"dc_low_{timeframe}", f"dc_basis_{timeframe}",
                          f"stoch_k_{timeframe}", f"stoch_d_{timeframe}", f"atr_{timeframe}", f"rsi_{timeframe}"]
        return base_indicators

    async def _run_full(self, symbol: str, timeframe: str, df: pd.DataFrame, close_ts: datetime, mark_price: Optional[float], state: SymbolTimeframeState) -> None:
        required_keys = self._required_by_timeframe(timeframe)
        mark_ts = None
        if required_keys and mark_price is None:
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
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True, errors='coerce')
        if 'close_time' in df.columns:
            df['close_time'] = pd.to_datetime(df['close_time'], utc=True, errors='coerce')
        
        result = self.calculator.compute(df, symbol, timeframe, mark_price, mark_ts, mid_run=False)
        if not result: return
        symbol_data = self.data.setdefault(symbol, {})
        symbol_data.update(result)
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
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True, errors='coerce')
        if 'close_time' in df.columns:
            df['close_time'] = pd.to_datetime(df['close_time'], utc=True, errors='coerce')
        result = self.calculator.compute(df, symbol, timeframe, mark_price, mark_ts, mid_run=True)
        if not result:
            return False
        symbol_data = self.data.setdefault(symbol, {})
        symbol_data.update(result)
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

            k5, d5 = g("stoch_k_5m", 50), g("stoch_d_5m", 50)
            k15, d15 = g("stoch_k_15m", 50), g("stoch_d_15m", 50)
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
        
        await self.run_cycle()

        tasks =[asyncio.create_task(self._schedule_loop()), asyncio.create_task(self._save_loop())]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self._shutdown.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.api_client.close()

def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down...")
    sys.exit(0)

async def main():
    import signal
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    orchestrator = TradierIndicatorOrchestrator()
    try:
        await orchestrator.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        await orchestrator.api_client.close()

if __name__ == "__main__":
    asyncio.run(main())