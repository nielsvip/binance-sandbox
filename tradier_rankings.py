# pylint: disable=W,C,R,I

import asyncio
import fcntl
import json
import logging
import os
import re
import shutil
import signal
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import aiofiles
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import pytz
from matplotlib.ticker import MaxNLocator

from config import Config
from config_tradier import TradierConfig
from tradier_api import TradierAPIClient
from tradier_indicators import TradierBarManager, TradierPriceCacheManager, _to_utc
from utils import get_simple_redis_manager, load_environment_from_gpg, orjson_default

_NYSE_CAL = mcal.get_calendar("XNYS")
NY_TZ = "America/New_York"
RTH_OPEN = (9, 30)
RTH_CLOSE = (16, 0)
MAX_BARS = 1800
MIN_BARS = 1200
# Long-term ranking guardrails.  The previous 1 + 3*|R| multiplier let
# linearity overwhelm the signed slope and treated a bearish daily trend as
# useful long-side confirmation.  Keep slope dominant: at most a 1.5x quality
# adjustment, and never admit a clearly bearish/severely drawn-down stock to
# the long candidate list.
LT_LINEARITY_WEIGHT = 0.5
LT_MAX_LINEARITY_MULTIPLIER = 1.5
LONG_MAX_PEAK_DRAWDOWN_PCT = -75.0
LT_RETURN_SCORE_WEIGHT = 5.0
LT_RETURN_CLIP_PCT = 50.0
SPLIT_GAP_RATIO = 3.0
try:
    from typing import Any, Dict, List, Optional, Union

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
        raise TypeError
    def json_dumps(obj: Any, **kwargs) -> bytes:
        option = orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS  # type: ignore # pylint: disable=no-member,c-extension-no-member
        return orjson.dumps(obj, default=default_json_serializer, option=option)  # type: ignore # pylint: disable=no-member,c-extension-no-member
    def safe_json_loads(s: Union[bytes, bytearray, memoryview, str], **kwargs) -> Any:
            if not s: return {}
            if isinstance(s, str):
                if not s.strip(): return {}
                s = s.encode('utf-8')
            elif isinstance(s, (bytes, bytearray, memoryview)):
                if not s.strip(): return {}
            return orjson.loads(s)  # In the except block, use json.loads(s, **kwargs)
    JSONDecodeError = orjson.JSONDecodeError  # type: ignore # pylint: disable=no-member,c-extension-no-member
except ImportError:  
    import json
    from typing import Any, Dict, List, Optional, Union
    def default_json_serializer(obj):
        if isinstance(obj, (datetime, pd.Timestamp)):
            return obj.isoformat()
        if isinstance(obj, pd.DataFrame):
            return obj.to_dict(orient='records')
        if isinstance(obj, pd.Series):
            return obj.to_list()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        raise TypeError
    def json_dumps(obj: Any, **kwargs) -> str:
        if 'indent' not in kwargs:
            kwargs['indent'] = 2
        return json.dumps(obj, default=default_json_serializer, **kwargs)
    def safe_json_loads(s: Union[bytes, bytearray, memoryview, str], **kwargs) -> Any:
            if not s: return {}
            if isinstance(s, (bytes, bytearray, memoryview)):
                s = s.decode('utf-8', errors='replace')
            if not isinstance(s, str) or not s.strip(): return {}
            return json.loads(s, **kwargs)
    JSONDecodeError = json.JSONDecodeError
# Load environment from .env.gpg
def json_safe(obj): 
    import numpy as np
    import pandas as pd
    if isinstance(obj,pd.DataFrame): return obj.to_dict("records")
    if isinstance(obj,pd.Series): return obj.to_list()
    if isinstance(obj,(np.integer,np.floating)): return float(obj)
    return str(obj)
load_environment_from_gpg(None)
client = TradierAPIClient()
from ez_rankings import (
    DAYS_PLOT,  # Ranking functions; Utility functions
    add_gradient,
    add_stochrsi_zones,
    assign_points_proximity_3m,
    build_ranking_info,
    calculate_3min_returns_for_symbols,
    calculate_15min_returns_for_symbols,
    calculate_atr,
    calculate_min_max,
    calculate_multi_timeframe_band_score,
    calculate_regression_band,
    calculate_regression_slope_line,
    calculate_relative_volume,
    calculate_weighted_gains,
    cleanup_old_plots,
    detect_stoch_crossovers,
    detect_tops_bottoms,
    find_rank_in_list,
    get_legend_handles_labels,
    get_proximity_range,
    get_ranking_data,
    get_ranking_multiplier,
    initialize_caches,
    load_cache,
    load_signals,
    merge_htf_band_into_ltf,
    normalize_log_signed,
    parse_timestamp,
    save_cache,
    save_rankings_json,
    to_json_safe,
)

_global_file_write_semaphore = asyncio.Semaphore(40)
config = TradierConfig()
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("tradier_rankings")
os.makedirs(config.LOG_DIR, exist_ok=True)
from logging.handlers import RotatingFileHandler

file_handler = RotatingFileHandler(config.LOG_DIR / "tradier_rankings.log", maxBytes=config.LOG_MAX_BYTES, backupCount=config.LOG_BACKUP_COUNT, encoding='utf-8', mode='a')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s")
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)
logger.setLevel(logging.DEBUG)

# ===== TRADIER-SPECIFIC OVERRIDES =====
TRADIER_TIMEFRAMES = ["D", "4h", "1h", "15m", "5m", "1m"]  # Different from crypto timeframes
PLOTS_DIR = config.BASE_PATH / "plots_tradier"
PLOTS_DIR.mkdir(parents=True, exist_ok=True)

# Update ez_rankings to use TradierConfig instead of crypto Config
import ez_rankings

ez_rankings.config = config  # Override to use TradierConfig
ez_rankings.RANKINGS_FILE = config.DATA_DIR / "tradier_rankings.json"
ez_rankings.PLOTS_DIR = PLOTS_DIR
ez_rankings.INDICATORS_FILE = config.DATA_DIR / "tradier_indicators_latest.json"

# ===== GLOBAL STATE =====
RANKING_DATA = []
RANKING_INFO = {}
LAST_RANKING_TIME = None
indicators_data = {}
price_cache = {}
signals_data = {}
api_client = None
bar_manager = None
redis_manager = None
mark_price_cache = {}
_news_sentiment_cache_tradier: Dict[str, float] = {}
_NEWS_INJECTION_FILE = Path(config.BASE_PATH) / "data" / "news_injections.json"

def _merge_news_injections(symbols: list, account: str, side: str) -> list:
    try:
        if not _NEWS_INJECTION_FILE.exists():
            return symbols
        with open(_NEWS_INJECTION_FILE, 'r') as f:
            injections = json.load(f)
        for inj in injections.get('active', []):
            if inj.get('account') == account and inj.get('side') == side and inj.get('symbol') not in symbols:
                symbols.append(inj['symbol'])
    except Exception:
        pass
    return symbols


def _merge_ai_injections(symbols: list, account: str, side: str) -> list:
    """Merge AI premarket picks into TRC only (TRB stays control). Reads
    data/ai_premarket/YYYY-MM-DD/decisions.json written by tradier_ai_premarket.py
    at 12:00 UTC. Each decision has {symbol, side, conviction, bias}.
    Guarded by AI_PREMARKET_ENABLED_{TRB,TRC} and conviction floor."""
    try:
        cfg = TradierConfig()
        enabled_trb = bool(getattr(cfg, 'AI_PREMARKET_ENABLED_TRB', False))
        enabled_trc = bool(getattr(cfg, 'AI_PREMARKET_ENABLED_TRC', False))
        if account == 'trb' and not enabled_trb:
            return symbols
        if account == 'trc' and not enabled_trc:
            return symbols
        # Resolve today's decisions file (UTC date)
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        decisions_dir = getattr(cfg, 'AI_PREMARKET_DECISIONS_DIR', 'data/ai_premarket')
        decisions_file = cfg.BASE_PATH / decisions_dir / today / 'decisions.json'
        if not decisions_file.exists():
            # fallback: latest file under decisions_dir
            try:
                candidates = sorted((cfg.BASE_PATH / decisions_dir).glob('*/decisions.json'))
                if candidates:
                    decisions_file = candidates[-1]
                else:
                    return symbols
            except Exception:
                return symbols
            if not decisions_file.exists():
                return symbols
        with open(decisions_file, 'r') as f:
            data = json.load(f)
        decisions = data.get('decisions', data) if isinstance(data, dict) else data
        if not isinstance(decisions, list):
            return symbols
        min_conv = float(getattr(cfg, 'AI_PREMARKET_MIN_CONVICTION', 0.55))
        max_new = int(getattr(cfg, 'AI_PREMARKET_MAX_NEW_PER_SIDE', 8))
        added = 0
        for dec in decisions:
            if not isinstance(dec, dict):
                continue
            if dec.get('side', '').upper() != side.upper():
                continue
            # account filter: decision may specify target_account
            tgt = dec.get('target_account', 'trc')
            if tgt != account and tgt != 'all':
                continue
            sym = str(dec.get('symbol', '')).upper().strip()
            if not sym or sym in symbols:
                continue
            try:
                conv = float(dec.get('conviction', 0))
            except Exception:
                conv = 0
            if conv < min_conv:
                continue
            if added >= max_new:
                break
            symbols.append(sym)
            added += 1
        if added:
            logger.info(f"[AI_RANKINGS] Injected {added} AI symbols into {account} {side}: {[d.get('symbol') for d in decisions[:added]]}")
    except Exception as e:
        logger.debug(f"[AI_RANKINGS] merge skipped: {e}")
    return symbols

async def _load_news_sentiment_tradier(rm=None):
    global _news_sentiment_cache_tradier
    try:
        if rm and hasattr(rm, 'connections'):
            for name, conn in rm.connections.items():
                if conn is None: continue
                try:
                    # Prefer stocks-only key; fall back to bulk which may include crypto
                    raw = await conn.get('news_sentiment_stocks') or await conn.get('news_sentiment_bulk')
                    if raw:
                        _news_sentiment_cache_tradier = {k: float(v) for k, v in json.loads(raw).items()}
                        return
                except Exception: pass
        fallback = Path(config.BASE_PATH) / 'data' / 'news_sentiment.json'
        if fallback.exists():
            with open(fallback, 'r') as f: _news_sentiment_cache_tradier = {k: float(v) for k, v in json.load(f).items()}
    except Exception as e:
        logger.debug(f"News sentiment load: {e}")


def calculate_lt_return_metrics(df: pd.DataFrame, num_bars: int = 45) -> Tuple[float, float]:
    """Return cumulative LT return and full-history peak-to-current drawdown.

    The old LT gain signal was a weighted average of bar-to-bar returns.  That
    can turn a single rebound, split/repricing gap, or other discontinuity into
    a large bullish score even when the stock remains deeply below its prior
    peak.  Cumulative return is bounded for scoring, while peak drawdown is
    retained as an explicit long-side eligibility guard.
    """
    if df is None or df.empty or "close" not in df.columns:
        return 0.0, 0.0

    work = df.copy()
    if "timestamp" in work.columns:
        work = work.assign(_sort_ts=pd.to_datetime(work["timestamp"], utc=True, errors="coerce"))
        work = work.sort_values("_sort_ts")
    closes = pd.to_numeric(work["close"], errors="coerce").dropna()
    closes = closes[closes > 0]
    if len(closes) < 2:
        return 0.0, 0.0

    # Tradier history can contain unadjusted reverse/forward-split gaps.  Put
    # the post-gap series back on the pre-gap scale for return/drawdown math.
    # This is deliberately limited to the LT return metric; live quote prices
    # and execution data remain in the broker's current-price scale.
    adjusted = closes.to_numpy(dtype=float, copy=True)
    for idx in range(1, len(adjusted)):
        ratio = adjusted[idx] / adjusted[idx - 1] if adjusted[idx - 1] > 0 else 1.0
        if ratio >= SPLIT_GAP_RATIO or ratio <= (1.0 / SPLIT_GAP_RATIO):
            adjusted[idx:] /= ratio
    closes = pd.Series(adjusted, index=closes.index)

    recent = closes.tail(num_bars + 1)
    start_price = float(recent.iloc[0])
    current_price = float(recent.iloc[-1])
    cumulative_return_pct = ((current_price / start_price) - 1.0) * 100.0 if start_price > 0 else 0.0

    peak_price = float(closes.max())
    peak_drawdown_pct = ((current_price / peak_price) - 1.0) * 100.0 if peak_price > 0 else 0.0
    return float(cumulative_return_pct), float(peak_drawdown_pct)
# New global to hold the price cache manager instance
price_cache_manager = None


# ===== DATA FUNCTIONS =====
def _prune_tiered(data_dir: Path, prefix: str):
    """Tiered retention: keep 1 file per bucket (0-15m, 15m-1h, 1h-4h, 4h-1d, 1d-1W), delete the rest."""
    try:
        all_files = [f for f in os.listdir(data_dir) if f.startswith(prefix) and f.endswith(".json")]
        if len(all_files) <= 5:
            return
        now = time.time()
        buckets = [(0, 900), (900, 3600), (3600, 14400), (14400, 86400), (86400, 604800)]
        def extract_ts(fname):
            try:
                return int(fname.replace(prefix, "").replace(".json", ""))
            except ValueError:
                return 0
        all_files.sort(key=extract_ts, reverse=True)
        keep = set()
        for bs, be in buckets:
            for f in all_files:
                age = now - extract_ts(f)
                if bs <= age < be:
                    keep.add(f)
                    break
        if all_files:
            keep.add(all_files[0])
        deleted = 0
        for f in all_files:
            if f not in keep:
                try:
                    os.remove(os.path.join(data_dir, f))
                    deleted += 1
                except Exception:
                    break
        if deleted > 0:
            logger.info(f"[prune] {prefix}*: deleted {deleted}, kept {len(keep)}")
    except Exception as e:
        logger.error(f"[prune] Error for {prefix}: {e}")

async def atomic_write_json(file_path: Union[str, Path], data: Any):
    """Atomically write JSON using orjson for speed and reliability."""
    file_path_obj = Path(file_path)
    random_suffix = uuid.uuid4().hex
    temp_file = file_path_obj.with_name(f".{file_path_obj.name}.{random_suffix}.tmp")
    try:
        file_path_obj.parent.mkdir(parents=True, exist_ok=True)
        
        # orjson.dumps returns bytes directly
        json_bytes = json_dumps(data)

        async with aiofiles.open(temp_file, 'wb') as f:
            await f.write(json_bytes)
            await f.flush()
            await asyncio.to_thread(os.fsync, f.fileno())
            
        await asyncio.to_thread(os.replace, str(temp_file), str(file_path_obj))
        return True
    except Exception as e:
        logger.error(f"Atomic write failed for {file_path_obj.name}: {e}")
        if temp_file.exists(): temp_file.unlink()
        return False
# async def atomic_write_json(file_path: Union[str, Path], data: dict):
#     """Atomically write JSON file with unique temp name and fsync"""
#     file_path_obj = Path(file_path)
#     random_suffix = uuid.uuid4().hex
#     temp_file = file_path_obj.with_name(f".{file_path_obj.name}.{random_suffix}.tmp")
    
#     try:
#         file_path_obj.parent.mkdir(parents=True, exist_ok=True)
        
#         # Serialize to bytes
#         try:
#             json_bytes = json_dumps(data)
#             if isinstance(json_bytes, str):
#                 json_bytes = json_bytes.encode('utf-8')
#         except Exception as e:
#             logger.warning(f"Optimization serialization failed, using default: {e}")
#             json_bytes = json.dumps(json_safe).encode('utf-8')

#         async with aiofiles.open(temp_file, 'wb') as f:
#             await f.write(json_bytes)
#             await f.flush()
#             await asyncio.to_thread(os.fsync, f.fileno())
            
#         await asyncio.to_thread(os.replace, str(temp_file), str(file_path_obj))
#         return True
        
#     except Exception as e:
#         logger.error(f"Atomic write failed for {file_path_obj}: {e}")
#         if temp_file.exists():
        #     try: temp_file.unlink()
        #     except: pass
        # return False

async def load_json_safe(file_path: Union[str, Path]) -> dict:
    file_path = Path(file_path)
    async with _global_file_write_semaphore:
        content = b""
        try:
            if not file_path.exists(): return {}
            async with aiofiles.open(file_path, "rb") as f:
                content = await f.read()
            if not content.strip(): return {}
            data = safe_json_loads(content)
        except Exception:
            # Recovery Mode
            try:
                txt = content.decode('utf-8', errors='ignore').strip()
                end_idx = txt.rfind('}')
                if end_idx != -1:
                    data = json.loads(txt[:end_idx+1])
                    await atomic_write_json(file_path, data)
                    return _convert_timestamp_strings(data)
            except Exception: pass
            return {}
        return _convert_timestamp_strings(data) if isinstance(data, (dict, list)) else {}


def filter_rth(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only Regular Trading Hours bars."""
    if df.empty:
        return df
    dt = df["timestamp_dt"].dt.tz_convert(NY_TZ)
    start = dt.dt.hour * 60 + dt.dt.minute >= (RTH_OPEN[0]*60 + RTH_OPEN[1])
    end   = dt.dt.hour * 60 + dt.dt.minute <= (RTH_CLOSE[0]*60 + RTH_CLOSE[1])
    return df[start & end].copy()
def build_4h_rth(df: pd.DataFrame) -> pd.DataFrame:
    """Build session-aligned 4h bars (one always ends at 16:00 ET)."""
    if df.empty:
        return df

    df = df.copy()
    df["timestamp_dt"] = df["timestamp_dt"].dt.tz_convert(NY_TZ)

    # keep only RTH
    df = filter_rth(df)

    df = df.set_index("timestamp_dt")

    # anchor so one bar closes at 16:00
    res = (
        df.resample("4h", offset="30min")  # key trick
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum" })
        .dropna()
        .reset_index() )
    res["timestamp_dt"] = res["timestamp_dt"].dt.tz_convert("UTC")
    res["close_time"] = res["timestamp_dt"]
    return res

def build_daily_from_intraday(df: pd.DataFrame) -> pd.DataFrame:
    """Build proper 16:00 ET daily bars from intraday."""
    if df.empty:
        return df

    df = df.copy()
    df["timestamp_dt"] = df["timestamp_dt"].dt.tz_convert(NY_TZ)

    # keep only RTH first
    df = filter_rth(df)

    daily = (
        df.set_index("timestamp_dt")
        .resample("1D", offset="16h")  # anchor to 16:00
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum"
        })
        .dropna()
        .reset_index() )
    daily["timestamp_dt"] = daily["timestamp_dt"].dt.tz_convert("UTC")
    daily["close_time"] = daily["timestamp_dt"]
    return daily


def _prepare_calendar_schedule(start_ts, end_ts):
    """Get NYSE schedule covering the data range."""
    start = pd.Timestamp(start_ts).tz_convert(NY_TZ).date() - pd.Timedelta(days=5)
    end = pd.Timestamp(end_ts).tz_convert(NY_TZ).date() + pd.Timedelta(days=5)

    sched = _NYSE_CAL.schedule(start_date=start, end_date=end)
    if sched.empty:
        return sched

    # Convert to NY time for comparisons
    sched = sched.copy()
    sched["market_open"] = sched["market_open"].dt.tz_convert(NY_TZ)
    sched["market_close"] = sched["market_close"].dt.tz_convert(NY_TZ)
    return sched


def _filter_rth_calendar(df: pd.DataFrame) -> pd.DataFrame:
    """Filter strictly to NYSE regular session using calendar."""
    if df.empty:
        return df

    df = df.copy()
    df["ts_ny"] = df["timestamp_dt"].dt.tz_convert(NY_TZ)

    sched = _prepare_calendar_schedule(
        df["timestamp_dt"].min(),
        df["timestamp_dt"].max(),
    )

    if sched.empty:
        return df.iloc[0:0]

    # Build mask per day (fast vectorized)
    df["session_date"] = df["ts_ny"].dt.date
    sched = sched.copy()
    sched["session_date"] = sched.index.date

    merged = df.merge(
        sched[["session_date", "market_open", "market_close"]],
        on="session_date",
        how="left",
    )

    mask = (
        (merged["ts_ny"] >= merged["market_open"]) &
        (merged["ts_ny"] <= merged["market_close"])
    )

    out = merged.loc[mask].copy()
    out.drop(columns=["ts_ny", "session_date", "market_open", "market_close"], inplace=True)
    return out

def _build_daily_calendar(df: pd.DataFrame) -> pd.DataFrame:
    """Build daily bars that always end at official session close."""
    if df.empty:
        return df

    df = df.copy()
    df["ts_ny"] = df["timestamp_dt"].dt.tz_convert(NY_TZ)
    df["session_date"] = df["ts_ny"].dt.date

    daily = (
        df.groupby("session_date")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            timestamp_dt=("ts_ny", "last"),
        )
        .dropna()
        .reset_index(drop=True)  )
    daily["timestamp_dt"] = daily["timestamp_dt"].dt.tz_convert("UTC")
    daily["close_time"] = daily["timestamp_dt"]
    return daily

def _build_4h_calendar(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["ts_ny"] = df["timestamp_dt"].dt.tz_convert(NY_TZ)
    df["session_date"] = df["ts_ny"].dt.date

    sched = _prepare_calendar_schedule(
        df["timestamp_dt"].min(),
        df["timestamp_dt"].max(),  )
    results = []

    for session_date, day_df in df.groupby("session_date"):
        if session_date not in sched.index.date:
            continue

        row = sched.loc[sched.index.date == session_date].iloc[0]
        open_ts = row["market_open"]
        close_ts = row["market_close"]

        day_df = day_df.sort_values("ts_ny")
        split_ts = open_ts + pd.Timedelta(hours=4)

        blocks = [
            (open_ts, min(split_ts, close_ts)),
            (min(split_ts, close_ts), close_ts),  ]

        for start, end in blocks:
            seg = day_df[(day_df["ts_ny"] >= start) & (day_df["ts_ny"] <= end)]
            if seg.empty:
                continue

            bar = {
                "open": seg["open"].iloc[0],
                "high": seg["high"].max(),
                "low": seg["low"].min(),
                "close": seg["close"].iloc[-1],
                "volume": seg["volume"].sum(),
                "timestamp_dt": end.tz_convert("UTC"),
            }
            results.append(bar)

    if not results:
        return pd.DataFrame()

    out = pd.DataFrame(results)
    out["close_time"] = out["timestamp_dt"]
    return out

# def apply_session_compression(df):
#     df = df.copy()
#     df = df.sort_values("close_time").reset_index(drop=True)
#     df["plot_idx"] = np.arange(len(df))
#     return df
def apply_session_compression(df): 
    if df.empty: return df
    df=df.copy().sort_values("close_time").reset_index(drop=True); df["plot_idx"]=np.arange(len(df)); return df

async def convert_tradier_bars_to_analysis_df(symbol: str, timeframe: str) -> pd.DataFrame:
    if not bar_manager:
        return pd.DataFrame()
    try:
        result = await bar_manager.get_latest(symbol, timeframe)
        if not result:
            return pd.DataFrame()

        df, close_ts, source = result
        if df is None or df.empty:
            return pd.DataFrame()
        df_result = df.copy()
        if "timestamp_dt" not in df_result.columns:
            ts_col = "timestamp" if "timestamp" in df_result.columns else ("time" if "time" in df_result.columns else None)
            if ts_col:
                df_result["timestamp_dt"] = _to_utc(df_result[ts_col])
            else:
                return pd.DataFrame()
        else:
            # Column exists, but might be strings
            if not pd.api.types.is_datetime64_any_dtype(df_result["timestamp_dt"]):
                df_result["timestamp_dt"] = _to_utc(df_result["timestamp_dt"])

        df_result = df_result.dropna(subset=["timestamp_dt"])
        df_result = df_result.sort_values("timestamp_dt")

        # numeric safety
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df_result.columns:
                df_result[col] = pd.to_numeric(df_result[col], errors="coerce")

        df_result = df_result.dropna(subset=["close"])

        if df_result.empty:
            return pd.DataFrame()

        df_result["close_time"] = df_result["timestamp_dt"]
        df_result = df_result.dropna(subset=["close_time"])
        df_result = df_result.sort_values("close_time").reset_index(drop=True)
        return df_result
    except Exception as e:
        logger.debug(f"Error converting bars for {symbol} {timeframe}: {e}", exc_info=True)
        return pd.DataFrame()



async def load_indicators_data():
    """Load Tradier indicators data from Redis (instantly) or JSON file (fallback)"""
    global indicators_data
    try:
        # 1. Try Redis (Ultra-short timeout for "instant" access)
        if redis_manager:
            try:
                data = await asyncio.wait_for(redis_manager.get("tradier_indicators_latest"), timeout=0.2)
                if data and isinstance(data, dict):
                    indicators_data = data
                    ez_rankings.indicators_data = indicators_data
                    return
            except Exception: pass
            
        # 2. Immediate Fallback to JSON
        latest_file = config.DATA_DIR / "tradier_indicators_latest.json"
        if latest_file.exists():
            data = await load_json_safe(latest_file)
            if data and isinstance(data, dict):
                indicators_data = data
                ez_rankings.indicators_data = indicators_data
    except Exception as e:
        logger.debug(f"Error loading indicators: {e}")

async def indicators_sync_loop():
    """Background task to sync indicators every 2s"""
    while True:
        try:
            await load_indicators_data()
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[indicators_sync] Error: {e}")
            await asyncio.sleep(5)

async def get_current_price(symbol: str) -> Tuple[Optional[float], Optional[datetime]]:
    """Get current price for symbol with mandatory 15s freshness check"""
    try:
        now = datetime.now(timezone.utc)
        MAX_AGE = 15.0

        def is_fresh(ts_obj):
            if not ts_obj: return False
            if ts_obj.tzinfo is None: ts_obj = ts_obj.replace(tzinfo=timezone.utc)
            return (now - ts_obj).total_seconds() < MAX_AGE

        # 1. Check mark_price_cache
        if symbol in mark_price_cache:
            price_data = mark_price_cache[symbol]
            price = price_data.get("price")
            ts_str = price_data.get("timestamp")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if price and is_fresh(ts):
                        return float(price), ts
                except Exception: pass
        
        # 2. Check indicators_data
        if symbol in indicators_data:
            entry = indicators_data[symbol]
            if isinstance(entry, dict):
                price = entry.get("current_price")
                ts_str = entry.get("timestamp")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if price and is_fresh(ts):
                            # Update mark_price_cache if fresh
                            mark_price_cache[symbol] = {"price": price, "timestamp": ts_str}
                            return float(price), ts
                    except Exception: pass
        
        # 3. Check bar_manager (LATEST BARS)
        if bar_manager:
            for tf in ["1m", "5m"]:
                res = await bar_manager.get_latest(symbol, tf)
                if res:
                    df, close_ts, _ = res
                    if df is not None and not df.empty and "close" in df.columns:
                        if is_fresh(close_ts):
                            price = float(df["close"].iloc[-1])
                            mark_price_cache[symbol] = {"price": price, "timestamp": close_ts.isoformat()}
                            return price, close_ts
    except Exception as e:
        logger.debug(f"Error getting price for {symbol}: {e}")
    return None, None

def _convert_timestamp_strings(data: Any) -> Any:
    """Recursively convert all timestamp strings in dict/list to datetime objects."""
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if isinstance(value, str) and any(ts_key in key.lower() for ts_key in ['time', 'timestamp', 'updated', 'at', 'date']):
                try:
                    if 'T' in value and ('Z' in value or '+' in value or value.count('-') >= 2):
                        from dateutil.parser import isoparse
                        parsed = isoparse(value)
                        result[key] = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
                    else:
                        result[key] = value
                except (ValueError, TypeError):
                    result[key] = value
            elif isinstance(value, (dict, list)):
                result[key] = _convert_timestamp_strings(value)
            else:
                result[key] = value
        return result
    elif isinstance(data, list):
        return [_convert_timestamp_strings(item) for item in data]
    return data

async def load_dfs_for_plotting(symbol, cache_dir):
    dfs = {}
    BARS_PER_TF = 300
    for tf in ["D", "4h", "1h", "15m", "5m"]:
        path = cache_dir / f"{symbol}_{tf}.json"
        if not path.exists(): continue
        try:
            import json
            with open(path, 'r') as f:
                data = json.load(f)
            df = pd.DataFrame(data)
            if not df.empty:
                df['close_time'] = _to_utc(df['timestamp'])
                df = df.sort_values('close_time').tail(BARS_PER_TF).reset_index(drop=True)
                df['plot_idx'] = range(len(df))
                dfs[tf] = df
        except Exception: continue
    return dfs

def filter_market_hours(df, tf):
    if df is None or df.empty: return df
    df = df.copy()
    if tf == "D":
        return df[df['close_time'].dt.dayofweek < 5]
    df_est = df.set_index('close_time').tz_convert('US/Eastern')
    df_est = df_est.between_time('09:30', '16:00')
    df_est = df_est[df_est.index.dayofweek < 5]
    return df_est.reset_index()

async def fetch_trb_trades_from_log(symbol: str, log_path: str = "~/logs/tradier_actions.log"):
    """
    Extremely defensive log parser.
    """
    trades = []
    expanded_path = os.path.expanduser(log_path)
    if not os.path.exists(expanded_path):
        return trades

    # Matches: 2026-02-06 20:56:09 ... trb:MSTR_LONG: ... augmented. ... Augment by: 12175.20
    # Note: We capture the timestamp string
    pattern = re.compile(
        r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*trb:(?P<sym>[A-Z]+)_.*"
        r"Position was (?P<action>augmented|reduced).*"
        r"(?:Augment|Reduced) by: (?P<amount>[\d.]+)"
    )

    try:
        with open(expanded_path, 'r') as f:
            for line in f:
                if symbol in line and "trb:" in line:
                    match = pattern.search(line)
                    if match:
                        # Convert to Timestamp, localize to UTC
                        trade_dt = pd.to_datetime(match.group('ts'))
                        trade_dt = trade_dt.tz_localize('UTC')
                        
                        action = match.group('action') 
                        amount = float(match.group('amount'))
                        
                        trades.append({
                            'timestamp': trade_dt,
                            'type': 'buy' if action == 'augmented' else 'sell',
                            'value': amount
                        })
    except Exception as e:
        pass
    return trades

def find_plot_index(df, ts):
            pos = df["close_time"].searchsorted(ts)
            pos = min(max(int(pos), 0), len(df) - 1)
            return df["plot_idx"].iloc[pos]

def plot_candles(ax, df):
    """
    Fast matplotlib candlestick renderer using compressed index.
    Assumes df already has:
        - plot_idx
        - open/high/low/close
    """
    if df.empty:
        return

    x = df["plot_idx"].values
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values

    up = c >= o
    down = ~up

    width = 0.6
    wick_lw = 0.8

    # --- wicks ---
    ax.vlines(x, l, h, color="#cfd8dc", linewidth=wick_lw, alpha=0.9, zorder=2)

    ax.bar(        x[up],
        (c[up] - o[up]),
        bottom=o[up],
        width=width,
        align="center",
        color="#26a69a",
        edgecolor="#26a69a",
        linewidth=0.5,
        alpha=0.95,
        zorder=3,    )

    # --- down candles (red) ---
    ax.bar(        x[down],
        (c[down] - o[down]),
        bottom=o[down],
        width=width,
        align="center",
        color="#ef5350",
        edgecolor="#ef5350",
        linewidth=0.5,
        alpha=0.95,
        zorder=3,    )
async def plot_dfs_subplots(
    rank_label: str, symbol: str, dfs: dict, final_score: float, 
    final_score_norm_arg: float, final_score_recent_arg: float,
    prox_norm_arg: float, band_score_arg: float, trend_val_arg: float, rp_global_arg: float,
    proximity_score_norm: float = 0.0,
    rel_volume: float = 0.0, slope_str: str = "", highlight_heatmap: bool = True,
    linear_val: float = 0.0, rel_gains: float = 0.0,
    output_dir: str = "./plots", RANKING_INFO_ARG: dict = None):
    tfs = ["D", "4h", "1h", "15m", "5m"]
    valid_tfs_with_data = [tf_loop for tf_loop in tfs if tf_loop in dfs and isinstance(dfs[tf_loop], pd.DataFrame) and not dfs[tf_loop].empty]
    
    if not valid_tfs_with_data:
        logger.warning(f"[plot_dfs_subplots] => No valid TF data for {rank_label} {symbol}, skipping.")
        return

    trades = await fetch_trb_trades_from_log(symbol)

    fig, axes = plt.subplots(len(valid_tfs_with_data), 1, figsize=(14, 4 * len(valid_tfs_with_data)), dpi=110, sharex=False)
    if len(valid_tfs_with_data) == 1:
        axes = [axes]
    
    fig.patch.set_facecolor("#272d30")
    for ax_plot in axes:
        ax_plot.set_facecolor("#272d30")
        for spine in ax_plot.spines.values(): spine.set_color("#d0d0d0")
        ax_plot.tick_params(axis="x", colors="#d0d0d0"); ax_plot.tick_params(axis="y", colors="#d0d0d0")
        ax_plot.xaxis.label.set_color("#d0d0d0"); ax_plot.yaxis.label.set_color("#d0d0d0")
        ax_plot.title.set_color("#9c864e")
        ax_plot.grid(color="#4a4a4a", alpha=0.6, linestyle=':')

    def fmt_f(val_fmt):
        try: return f"{float(val_fmt):.2f}"
        except (TypeError, ValueError): return "NaN"

    global signals_data

    for i, tf_plot_loop in enumerate(valid_tfs_with_data):
        ax_plot = axes[i]
        df_tf = dfs[tf_plot_loop].copy()
        if len(df_tf) < 2:
            ax_plot.set_title(f"{symbol} {tf_plot_loop}: Insufficient data to plot.")
            continue
        for col_check in ["high", "low"]:
            if col_check not in df_tf.columns:
                if 'close' in df_tf.columns: df_tf[col_check] = df_tf['close']
                else: continue
        df_tf['close_time'] = _to_utc(df_tf['close_time'])
        df_tf.dropna(subset=['close_time'], inplace=True)
        df_tf.sort_values("close_time", inplace=True)
        df_tf = df_tf.reset_index(drop=True)
        _TARGET = 600
        # LTF map: for each TF, the lower TF we can resample into it (more detail, shorter history)
        _LTF_MAP = {"5m": ("1m", "5min"), "15m": ("5m", "15min"), "1h": ("15m", "1h"), "4h": ("1h", "4h")}
        # HTF map: fallback after LTF exhausted — every 4th bar, drawn as line (less detail, longer history)
        _HTF_MAP = {"5m": "15m", "15m": "1h", "1h": "4h", "4h": "D"}
        df_tf = df_tf.tail(_TARGET).reset_index(drop=True)
        df_tf['_synthetic'] = False
        _htf_fill_label = None
        # Step 1: fill from lower TF by resampling — proper OHLC candles, shorter reach
        _ltf_info = _LTF_MAP.get(tf_plot_loop)
        if len(df_tf) < _TARGET and _ltf_info:
            _ltf_key, _resample_freq = _ltf_info
            if _ltf_key in dfs and isinstance(dfs[_ltf_key], pd.DataFrame) and not dfs[_ltf_key].empty:
                _df_ltf = dfs[_ltf_key].copy()
                _df_ltf['close_time'] = _to_utc(_df_ltf['close_time'])
                _df_ltf = _df_ltf.dropna(subset=['close_time']).sort_values('close_time')
                if not df_tf.empty:
                    _df_ltf = _df_ltf[_df_ltf['close_time'] < df_tf['close_time'].iloc[0]]
                if not _df_ltf.empty and all(c in _df_ltf.columns for c in ['open', 'high', 'low', 'close']):
                    _rs = _df_ltf.set_index('close_time')[['open', 'high', 'low', 'close', 'volume']].resample(_resample_freq, label='right', closed='right').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna(subset=['close']).reset_index()
                    _rs.rename(columns={_rs.columns[0]: 'close_time'}, inplace=True)
                    _n_need = _TARGET - len(df_tf)
                    _rs = _rs.tail(_n_need).copy()
                    if not _rs.empty:
                        _rs['_synthetic'] = False
                        df_tf = pd.concat([_rs, df_tf], ignore_index=True).sort_values('close_time').reset_index(drop=True)
        # Step 2: if still short, fill remaining gap from higher TF — every 4th bar, drawn as line
        _htf_key = _HTF_MAP.get(tf_plot_loop)
        if len(df_tf) < _TARGET and _htf_key and _htf_key in dfs and isinstance(dfs[_htf_key], pd.DataFrame) and not dfs[_htf_key].empty:
            _df_htf = dfs[_htf_key].copy()
            _df_htf['close_time'] = _to_utc(_df_htf['close_time'])
            _df_htf = _df_htf.dropna(subset=['close_time']).sort_values('close_time').reset_index(drop=True)
            _df_htf_s = _df_htf.iloc[::4].copy()
            if not df_tf.empty:
                _df_htf_s = _df_htf_s[_df_htf_s['close_time'] < df_tf['close_time'].iloc[0]]
            _n_need = _TARGET - len(df_tf)
            _avail = [c for c in ['close_time', 'open', 'high', 'low', 'close', 'volume'] if c in _df_htf_s.columns]
            _df_fill = _df_htf_s.tail(_n_need)[_avail].copy()
            if not _df_fill.empty:
                for _col in ['open', 'high', 'low', 'volume']:
                    if _col not in _df_fill.columns:
                        _df_fill[_col] = _df_fill['close']
                _df_fill['_synthetic'] = True
                df_tf = pd.concat([_df_fill, df_tf], ignore_index=True).sort_values('close_time').reset_index(drop=True)
                _htf_fill_label = _htf_key
        df_tf['plot_idx'] = range(len(df_tf))
        x_indices = df_tf['plot_idx'].values
        n_bars = len(df_tf)
        ax_plot.set_xlim(-0.5, n_bars - 0.5)
        x_left_ts = df_tf["close_time"].iloc[0]
        x_right_ts = df_tf["close_time"].iloc[-1]
        ymin_data = pd.to_numeric(df_tf["low"], errors='coerce').min()
        ymax_data = pd.to_numeric(df_tf["high"], errors='coerce').max()
        if pd.isna(ymin_data) or pd.isna(ymax_data) or np.isclose(ymin_data, ymax_data):
            current_close_val = pd.to_numeric(df_tf["close"], errors='coerce').iloc[-1] if not df_tf.empty and 'close' in df_tf.columns else 1.0
            if pd.isna(current_close_val): current_close_val = 1.0
            ymin_data = current_close_val * 0.98
            ymax_data = current_close_val * 1.02
        range_span = ymax_data - ymin_data
        margin_abs_val = (0.012 * range_span) if range_span > 1e-9 else 0.02
        ax_plot.set_ylim(ymin_data - margin_abs_val, ymax_data + margin_abs_val)
        _df_real = df_tf[~df_tf['_synthetic']].copy() if '_synthetic' in df_tf.columns else df_tf
        _df_synth = df_tf[df_tf['_synthetic']].copy() if '_synthetic' in df_tf.columns else pd.DataFrame()
        plot_candles(ax_plot, _df_real)
        if not _df_synth.empty and 'close' in _df_synth.columns:
            ax_plot.plot(_df_synth['plot_idx'].values, pd.to_numeric(_df_synth['close'], errors='coerce').values, color='#78909c', lw=1.2, alpha=0.80, zorder=1, solid_capstyle='round', label=f'{_htf_fill_label}↗ fill')
        def get_x_idx(ts, _df=df_tf):
            pos = _df["close_time"].searchsorted(ts)
            pos = min(max(int(pos), 0), len(_df) - 1)
            return _df["plot_idx"].iloc[pos]
        if trades:
            for trade in trades:
                t_ts = pd.to_datetime(trade['timestamp'], utc=True)
                if x_left_ts <= t_ts <= x_right_ts:
                    l_color = '#00bfff' if trade['type'].lower() in ['buy', 'augmented'] else '#ff4444'
                    l_width = min(max(trade['value'] / 5000, 0.8), 6.0)
                    t_idx = get_x_idx(t_ts)
                    ax_plot.axvline(x=t_idx, color=l_color, linewidth=l_width, alpha=0.6, zorder=2)
        if symbol in signals_data and tf_plot_loop in signals_data[symbol]:
            for etype, ev_list in signals_data[symbol][tf_plot_loop].items():
                for event in ev_list:
                    ts_event = event.get("timestamp")
                    if not isinstance(ts_event, pd.Timestamp):
                        try: ts_event = pd.to_datetime(ts_event)
                        except Exception: continue
                    if ts_event.tzinfo is None: ts_event = ts_event.tz_localize('UTC')
                    if ts_event < x_left_ts or ts_event > x_right_ts: continue
                    price_event = event.get("price")
                    reason_event = event.get("reason", etype).lower()
                    action_event = event.get("action", "").upper()
                    marker_style = None; color_style = 'orange'; line_style = 'dotted'; marker_size = 6
                    is_vline = True
                    if "stoch_crossover" in reason_event: color_style, marker_style, is_vline, marker_size = "cyan", 'o', False, 4
                    elif "stoch_crossunder" in reason_event: color_style, marker_style, is_vline, marker_size = "magenta", 'o', False, 4
                    elif "wt_crossover" in reason_event or "wt crossover" in reason_event: color_style, marker_style, is_vline, marker_size = "lime", '^', False, 8
                    elif "wt_crossunder" in reason_event or "wt crossunder" in reason_event: color_style, marker_style, is_vline, marker_size = "red", 'v', False, 8
                    elif "wt strong alert" in reason_event: color_style = "lime" if action_event == "BUY" else "red"
                    elif "winners_up" in reason_event or "losers_down" in reason_event: is_vline, marker_style, color_style = False, '^', 'lime'
                    elif "winners_down" in reason_event or "losers_up" in reason_event: is_vline, marker_style, color_style = False, 'v', 'red'
                    t_idx = get_x_idx(ts_event)
                    if is_vline:
                        ax_plot.axvline(x=t_idx, color=color_style, linestyle=line_style, lw=1, zorder=5)
                    elif marker_style and pd.notna(price_event):
                        y_pos = price_event
                        try:
                            row_idx = df_tf['close_time'].searchsorted(ts_event)
                            row_idx = max(0, min(row_idx, len(df_tf) - 1))
                            candle_row = df_tf.iloc[row_idx]
                            if marker_style == '^': y_pos = candle_row['low'] - margin_abs_val * 0.5
                            elif marker_style == 'v': y_pos = candle_row['high'] + margin_abs_val * 0.5
                        except IndexError: pass
                        ax_plot.plot(t_idx, y_pos, marker=marker_style, color=color_style, ms=marker_size, linestyle='None', zorder=7)
        if len(df_tf) > 2 and 'close' in df_tf.columns:
            try:
                from ez_rankings import (
                    calculate_regression_band,
                    calculate_regression_slope_line,
                )
                slope_pct_local, rvv_local, yhat_abs_local = calculate_regression_slope_line(df_tf[['close_time','close']].copy())
                if len(yhat_abs_local) == len(df_tf):
                    slope_str_local_title = f"Slope={fmt_f(slope_pct_local)}%, R={fmt_f(rvv_local)}"
                    ax_plot.plot(x_indices, yhat_abs_local, color="blue", linestyle="--", lw=1, label=f"Reg ({slope_str_local_title})")
                    band_df_local = calculate_regression_band(df_tf.copy())
                    if not band_df_local.empty:
                        ax_plot.fill_between(x_indices, band_df_local["upperb"], ymax_data + margin_abs_val, color="grey", alpha=0.25, where=(band_df_local["upperb"] < (ymax_data + margin_abs_val)), zorder=-5, linewidth=0.0)
                        ax_plot.fill_between(x_indices, ymin_data - margin_abs_val, band_df_local["lowerb"], color="grey", alpha=0.25, where=(band_df_local["lowerb"] > (ymin_data - margin_abs_val)), zorder=-5, linewidth=0.0)
            except Exception: pass
        if "stoch_rsi" in df_tf.columns and not df_tf["stoch_rsi"].isnull().all():
            try:
                stoch_norm = df_tf["stoch_rsi"].fillna(50) / 100.0
                stoch_norm = np.clip(stoch_norm, 0, 1)
                img_data = np.zeros((1, len(stoch_norm), 4))
                img_data[0, :, 0] = stoch_norm.values
                img_data[0, :, 1] = 1.0 - stoch_norm.values
                img_data[0, :, 2] = 0.0
                img_data[0, :, 3] = 0.12
                ax_plot.imshow(img_data, extent=[-0.5, n_bars - 0.5, ymin_data - margin_abs_val, ymax_data + margin_abs_val], aspect='auto', origin='lower', zorder=-15, interpolation='bilinear')
            except Exception: pass
        try:
            from ez_rankings import detect_tops_bottoms
            df_tf_tops_bottoms = detect_tops_bottoms(df_tf.copy(), distance=10 if tf_plot_loop == "5m" else 20, prominence=1e-5)
            topdf = df_tf_tops_bottoms.dropna(subset=["top"])
            botdf = df_tf_tops_bottoms.dropna(subset=["bottom"])
            top_size = 15 if tf_plot_loop == "5m" else 20
            bottom_size = 15 if tf_plot_loop == "5m" else 20
            if not topdf.empty: ax_plot.scatter(topdf["plot_idx"], topdf["top"], color="red", marker="v", s=top_size, zorder=3, label="Top")
            if not botdf.empty: ax_plot.scatter(botdf["plot_idx"], botdf["bottom"], color="lime", marker="^", s=bottom_size, zorder=3, label="Bottom")
        except Exception: pass
        if highlight_heatmap and "proximity_score_norm" in df_tf.columns and not df_tf["proximity_score_norm"].isnull().all():
            prox_gradient_vals = pd.to_numeric(df_tf["proximity_score_norm"], errors='coerce').fillna(0).values
            if len(prox_gradient_vals) > 1:
                try:
                    from ez_rankings import add_gradient
                    add_gradient(ax_plot, -0.5, n_bars - 0.5, ymin_data - margin_abs_val, ymax_data + margin_abs_val, gradient_vals=prox_gradient_vals, slices=200, alpha=0.025, zorder=-10, is_vertical=True)
                except Exception: pass
        bigger_tfs_map = {"1h": ["4h", "D"], "15m": ["1h", "4h"], "5m": ["15m", "1h"]}
        htfs_to_overlay = bigger_tfs_map.get(tf_plot_loop, [])
        for btf_overlay in htfs_to_overlay:
            if btf_overlay in dfs and isinstance(dfs[btf_overlay], pd.DataFrame) and not dfs[btf_overlay].empty:
                df_btf_data = dfs[btf_overlay].copy()
                if 'close_time' in df_btf_data.columns and 'close' in df_btf_data.columns:
                    try:
                        from ez_rankings import (
                            calculate_regression_band,
                            merge_htf_band_into_ltf,
                        )
                        band_htf_overlay = calculate_regression_band(df_btf_data)
                        if not band_htf_overlay.empty:
                            df_tf_merged_htf_band = merge_htf_band_into_ltf(df_tf.copy(), band_htf_overlay)
                            ax_plot.plot(x_indices, df_tf_merged_htf_band["upperb"], color="blue", linestyle=":", lw=0.8, alpha=0.5, label=f"{btf_overlay} UB")
                            ax_plot.plot(x_indices, df_tf_merged_htf_band["lowerb"], color="blue", linestyle=":", lw=0.8, alpha=0.5, label=f"{btf_overlay} LB")
                            ax_plot.fill_between(x_indices, df_tf_merged_htf_band["upperb"], df_tf_merged_htf_band["lowerb"], color="blue", alpha=0.05, zorder=-8, linewidth=0.0)
                    except Exception: pass
        title_str = f"{symbol} {tf_plot_loop} ({n_bars} bars)"
        if tf_plot_loop == "5m":
            title_str = (f"{rank_label} {symbol} {tf_plot_loop} ({n_bars}) FS_N:{fmt_f(final_score_norm_arg)} FS_R:{fmt_f(final_score_recent_arg)} | "
                         f"Prox:{fmt_f(prox_norm_arg)} Band:{fmt_f(band_score_arg)} | "
                         f"Trend:{fmt_f(trend_val_arg)} RPg:{fmt_f(rp_global_arg)}")
        elif tf_plot_loop == "D":
            title_str = (f"{rank_label} {symbol} {tf_plot_loop} ({n_bars}) FS_N:{fmt_f(final_score_norm_arg)} FS_R:{fmt_f(final_score_recent_arg)} | "
                         f"RawFS:{fmt_f(final_score)} Lin:{fmt_f(linear_val)} | Slopes: {slope_str[:50]}")
        ax_plot.set_title(title_str, fontsize=14)
        import pytz
        et_tz = pytz.timezone("America/New_York")
        timestamps_et = df_tf['close_time'].dt.tz_convert(et_tz)
        tf_captured = tf_plot_loop
        def make_formatter(tf_fmt, ts_series, n):
            def format_date(x, pos=None):
                idx = int(round(x))
                if idx < 0 or idx >= n: return ""
                dt = ts_series.iloc[idx]
                if tf_fmt == "D": return dt.strftime('%d-%b-%y')
                elif tf_fmt in ["4h", "1h"]: return dt.strftime('%m-%d %H:%M')
                else: return dt.strftime('%m-%d %H:%M')
            return format_date
        ax_plot.xaxis.set_major_locator(mticker.MaxNLocator(nbins=14, prune='both', integer=True))
        ax_plot.xaxis.set_major_formatter(mticker.FuncFormatter(make_formatter(tf_captured, timestamps_et, n_bars)))
        ax_plot.tick_params(axis='x', rotation=15, labelsize=8)
        ax_plot.tick_params(axis='y', labelsize=8)

    # Wrap up Global legend
    try:
        from ez_rankings import get_legend_handles_labels
        handles_combined, labels_combined = get_legend_handles_labels(axes)
        if labels_combined:
            fig.legend(handles_combined, labels_combined, loc='upper center', 
                       bbox_to_anchor=(0.5, 1.00), ncol=min(len(labels_combined), 8),
                       fontsize=7, frameon=False, labelcolor='#d0d0d0')
    except Exception: pass

    fig.tight_layout(rect=[0, 0.03, 1, 0.95])
    os.makedirs(output_dir, exist_ok=True)
    safe_rank_label = rank_label.replace("#","_").replace(":","_")
    fpath = Path(output_dir) / f"{safe_rank_label}_{symbol}.png"
    
    try:
        fig.savefig(fpath)
    except Exception as e_save:
        logger.error(f"Failed to save plot {fpath}: {e_save}")
    finally:
        plt.close(fig)



# async def plot_dfs_subplots( rank_label: str, symbol: str, dfs: dict, final_score: float, final_score_norm_arg: float, final_score_recent_arg: float, prox_norm_arg: float, band_score_arg: float, trend_val_arg: float, rp_global_arg: float, proximity_score_norm: float = 0.0, rel_volume: float = 0.0, slope_str: str = "", highlight_heatmap: bool = True, linear_val: float = 0.0, rel_gains: float = 0.0, output_dir: str = "./plots", RANKING_INFO_ARG: dict = None ):
#     target_tfs = ["D", "4h", "1h", "15m", "5m"]
#     active_dfs = {}
#     for tf in target_tfs:
#         df_tf = None
#         if tf in dfs and isinstance(dfs[tf], pd.DataFrame) and not dfs[tf].empty and len(dfs[tf]) > 2:
#             df_tf = dfs[tf].copy()
#         elif bar_manager:
#             try:
#                 res = await bar_manager.get_latest(symbol, tf)
#                 if res and res[0] is not None and not res[0].empty and len(res[0]) > 2:
#                     df_tf = res[0]
#             except Exception as e:
#                 logger.debug(f"Could not fetch {tf} for {symbol}: {e}")
#         if df_tf is not None and not df_tf.empty:
#             if 'timestamp_dt' not in df_tf.columns and 'timestamp' in df_tf.columns:
#                 df_tf['timestamp_dt'] = pd.to_datetime(df_tf['timestamp'], utc=True, errors='coerce')
#             df_tf['close_time'] = pd.to_datetime(df_tf.get('close_time', df_tf['timestamp_dt']),  utc=True, errors='coerce')
#             df_tf['close_time'] = df_tf['close_time'].dt.tz_convert('UTC')
#             df_tf.dropna(subset=['close_time'], inplace=True)
#             df_tf.sort_values("close_time", inplace=True)
#             df_tf.reset_index(drop=True, inplace=True)
        
#             for col_check in ["high", "low"]:
#                 if col_check not in df_tf.columns and 'close' in df_tf.columns:
#                     df_tf[col_check] = df_tf['close']
#             if 'close' in df_tf.columns and len(df_tf) >= 2:
#                 # Keep latest 1200 bars
#                 active_dfs[tf] = df_tf.tail(1200).reset_index(drop=True)
#     valid_plot_tfs = [tf for tf in target_tfs if tf in active_dfs]
#     if not valid_plot_tfs:
#         return
#     trb_trades = await fetch_trb_trades_from_log(symbol)
#     fig, axes = plt.subplots(len(valid_plot_tfs), 1, figsize=(14, 4 * len(valid_plot_tfs)), dpi=110, sharex=False)
#     if len(valid_plot_tfs) == 1:
#         axes = [axes]
#     fig.patch.set_facecolor("#272d30")
#     for ax_plot in axes:
#         ax_plot.set_facecolor("#272d30")
#         for spine in ax_plot.spines.values(): spine.set_color("#d0d0d0")
#         ax_plot.tick_params(axis="x", colors="#d0d0d0"); ax_plot.tick_params(axis="y", colors="#d0d0d0")
#         ax_plot.xaxis.label.set_color("#d0d0d0"); ax_plot.yaxis.label.set_color("#d0d0d0")
#         ax_plot.title.set_color("#f0b90b")
#         ax_plot.grid(color="#4a4a4a", alpha=0.6, linestyle=':')
#     def fmt_f(val_fmt):
#         try: return f"{float(val_fmt):.2f}"
#         except (TypeError, ValueError): return "NaN"
#     global signals_data
#     for i, tf in enumerate(valid_plot_tfs):
#         ax = axes[i]
#         df_tf = active_dfs[tf]
#         df_tf = apply_session_compression(df_tf)
#         bar_width = 0.7 if len(df_tf) < 300 else 0.55
#         x_idx = df_tf["plot_idx"].values
#         x_left_ts = df_tf["close_time"].iloc[0]
#         x_right_ts = df_tf["close_time"].iloc[-1]
#         plot_candles(ax, df_tf)

#         ax.set_xlim(x_idx[0], x_idx[-1])

#         print(tf, len(df_tf),
#             df_tf['close_time'].isna().sum(),
#             df_tf['close_time'].dt.tz)

#         ax.set_xlim(0, len(df_tf) - 1)
#         ymin, ymax = df_tf["low"].min(), df_tf["high"].max()
#         p_range = ymax - ymin if ymax > ymin else 1.0

#         if p_range < 1e-6:
#             ymin -= 0.5
#             ymax += 0.5
#             p_range = ymax - ymin

#         margin = 0.012 * p_range
#         ax.set_ylim(ymin - margin, ymax + margin)
#         # ax.plot( x_idx,df_tf["close"], color="#d0d0d0", lw=1.3, solid_capstyle="round", solid_joinstyle="round", label="Close",) #TEMP OUT
#         if trb_trades:
#             for trade in trb_trades:
#                 t_ts = pd.to_datetime(trade['timestamp'], utc=True)
#                 if x_left_ts <= t_ts <= x_right_ts:
#                     idx = find_plot_index(df_tf, t_ts)
#                     l_color = '#00bfff' if trade['type'].lower() in ['buy', 'augmented'] else '#ff4444'
#                     l_width = min(max(trade['value'] / 5000, 0.8), 6.0)
#                     ax.axvline(x=idx, color=l_color, linewidth=l_width, alpha=0.6, zorder=2)
#         if symbol in signals_data and tf in signals_data[symbol]:
#             for etype, ev_list in signals_data[symbol][tf].items():
#                 for event in ev_list:
#                     ts_event = pd.to_datetime(event.get("timestamp"), utc=True)
#                     if ts_event < x_left_ts or ts_event > x_right_ts: continue
#                     idx = find_plot_index(df_tf, t_ts)
#                     price_event = event.get("price")
#                     reason_event = event.get("reason", etype).lower()
#                     action_event = event.get("action", "").upper()
#                     marker_style = None; color_style = 'orange'; line_style = 'dotted'; marker_size = 6
#                     is_vline = True
#                     if "stoch_crossover" in reason_event: 
#                         color_style = "cyan"; marker_style = 'o'; is_vline = False; marker_size = 4
#                     elif "stoch_crossunder" in reason_event: 
#                         color_style = "magenta"; marker_style = 'o'; is_vline = False; marker_size = 4
#                     elif "wt_crossover" in reason_event:
#                         color_style = "lime"; marker_style = '^'; is_vline = False; marker_size = 8
#                     elif "wt_crossunder" in reason_event:
#                         color_style = "red"; marker_style = 'v'; is_vline = False; marker_size = 8
#                     elif "wt strong alert" in reason_event:
#                         color_style = "lime" if action_event == "BUY" else "red"
#                     elif "winners_up" in reason_event or "losers_down" in reason_event:
#                         is_vline = False; marker_style = '^'; color_style = 'lime'
#                     elif "winners_down" in reason_event or "losers_up" in reason_event:
#                         is_vline = False; marker_style = 'v'; color_style = 'red'
#                     if is_vline:
#                         ax.axvline(x=idx, color=color_style, linestyle=line_style, lw=1, zorder=5)
#                     elif marker_style and pd.notna(price_event):
#                         y_pos = price_event
#                         try:
#                             candle_row = df_tf.iloc[min(idx, len(df_tf)-1)]
#                             if marker_style == '^':
#                                 y_pos = candle_row['low'] - margin * 0.5
#                             elif marker_style == 'v':
#                                 y_pos = candle_row['high'] + margin * 0.5
#                         except: pass
#                         ax.plot(idx, y_pos, marker=marker_style, color=color_style, ms=marker_size, linestyle='None', zorder=7)
#         if len(df_tf) > 2 and 'close' in df_tf.columns:
#             try:
#                 from ez_rankings import (calculate_regression_band,
#                                          calculate_regression_slope_line,
#                                          merge_htf_band_into_ltf)
#                 slope_pct_local, rvv_local, yhat_abs_local = calculate_regression_slope_line(df_tf[['close_time','close']].copy())
#                 if len(yhat_abs_local) == len(df_tf):
#                     slope_str_local_title = f"Slope={fmt_f(slope_pct_local)}%, R={fmt_f(rvv_local)}"
#                     ax.plot(x_idx, yhat_abs_local, color="blue", linestyle="--", lw=1, label=f"Reg ({slope_str_local_title})")
#                     band_df_local = calculate_regression_band(df_tf.copy())
#                     if not band_df_local.empty:
#                         ax.fill_between(x_idx, band_df_local["upperb"], ymax + margin, color="grey", alpha=0.25, where=(band_df_local["upperb"] < (ymax + margin)), zorder=-5, linewidth=0.0)
#                         ax.fill_between(x_idx, ymin - margin, band_df_local["lowerb"], color="grey", alpha=0.25, where=(band_df_local["lowerb"] > (ymin - margin)), zorder=-5, linewidth=0.0)
#             except Exception as e_slope:
#                 pass
#         if "stoch_rsi" in df_tf.columns and not df_tf["stoch_rsi"].isnull().all():
#             try:
#                 stoch_norm = df_tf["stoch_rsi"].fillna(50) / 100.0
#                 stoch_norm = np.clip(stoch_norm, 0, 1)
#                 img_data = np.zeros((1, len(stoch_norm), 4))
#                 img_data[0, :, 0] = stoch_norm.values
#                 img_data[0, :, 1] = 1.0 - stoch_norm.values
#                 img_data[0, :, 2] = 0.0
#                 img_data[0, :, 3] = 0.12
#                 ax.imshow(img_data, extent=[0, len(df_tf)-1, ymin - margin, ymax + margin], aspect='auto', origin='lower', zorder=-15, interpolation='bilinear')
#             except: pass
#         try:
#             from ez_rankings import detect_tops_bottoms
#             df_tb = detect_tops_bottoms(df_tf.copy(), distance=10 if tf == "5m" else 20, prominence=1e-5)
#             tops = df_tb.dropna(subset=["top"])
#             bots = df_tb.dropna(subset=["bottom"])
#             if not tops.empty:
#                 tops["idx"] = tops["close_time"].apply(lambda ts: find_plot_index(df_tf, ts))
#                 ax.scatter(tops["idx"], tops["top"], color="red", marker="v", s=15 if tf == "5m" else 24, zorder=3, label="Top")
#             if not bots.empty:
#                 bots["idx"] = bots["close_time"].apply(lambda ts: find_plot_index(df_tf, ts))
#                 ax.scatter(bots["idx"], bots["bottom"], color="lime", marker="^", s=15 if tf == "5m" else 24, zorder=3, label="Bottom")
#             distance = 2 if tf == "5m" else 4
#             df_recent = df_tf.tail(30).copy()
#             df_tb_recent = detect_tops_bottoms(df_recent, distance=distance, prominence=1e-7)
#             recent_tops = df_tb_recent.dropna(subset=["top"])
#             if not recent_tops.empty:
#                 last_idx = recent_tops.index[-1]
#                 if (df_tb_recent.index[-1] - last_idx) <= 2:
#                     rt = recent_tops.iloc[-1]
#                     r_idx = find_plot_index(df_tf, rt["close_time"])
#                     ax.scatter(r_idx, rt["top"], color="#ff0000", marker="v", s=35, zorder=12, label="Hist Red Arrow", alpha=0.98, edgecolors="white", linewidth=1.5)
#                     d_idx = len(df_tf) - 1
#                     ax.scatter(d_idx, df_tf["close"].iloc[-1], color="#ff4444", marker="v", s=25, zorder=11, label="Real Red Arrow", alpha=0.85, edgecolors="yellow", linewidth=1)
#             recent_bots = df_tb_recent.dropna(subset=["bottom"])
#             if not recent_bots.empty:
#                 last_idx = recent_bots.index[-1]
#                 if (df_tb_recent.index[-1] - last_idx) <= 2:
#                     rb = recent_bots.iloc[-1]
#                     r_idx = find_plot_index(df_tf, rb["close_time"])
#                     ax.scatter(r_idx, rb["bottom"], color="#00ff00", marker="^", s=35, zorder=12, label="Hist Green Arrow", alpha=0.98, edgecolors="white", linewidth=1.5)
#                     d_idx = len(df_tf) - 1
#                     ax.scatter(d_idx, df_tf["close"].iloc[-1], color="#44ff44", marker="^", s=25, zorder=11, label="Real Green Arrow", alpha=0.85, edgecolors="yellow", linewidth=1)
#         except: pass
#         if highlight_heatmap and "proximity_score_norm" in df_tf.columns and not df_tf["proximity_score_norm"].isnull().all():
#             prox_gradient_vals = pd.to_numeric(df_tf["proximity_score_norm"], errors='coerce').fillna(0).values
#             if len(prox_gradient_vals) > 1:
#                 from ez_rankings import add_gradient
#                 try:
#                     add_gradient(ax, 0, len(df_tf)-1, ymin - margin, ymax + margin, gradient_vals=prox_gradient_vals, slices=200, alpha=0.025, zorder=-10, is_vertical=True)
#                 except: pass
#         htf_map = {"5m": ["15m", "1h"], "15m": ["1h", "4h"], "1h": ["4h", "D"], "4h": ["D"], "D": []}
#         for btf in htf_map.get(tf, []):
#             if btf in active_dfs:
#                 try:
#                     from ez_rankings import (calculate_regression_band,
#                                              merge_htf_band_into_ltf)
#                     htf_band = calculate_regression_band(active_dfs[btf].copy())
#                     if not htf_band.empty:
#                         merged = merge_htf_band_into_ltf(df_tf.copy(), htf_band)
#                         ax.plot(x_idx, merged["upperb"], color="blue", linestyle=":", lw=0.8, alpha=0.5, label=f"{btf} UB")
#                         ax.plot(x_idx, merged["lowerb"], color="blue", linestyle=":", lw=0.8, alpha=0.5, label=f"{btf} LB")
#                         ax.fill_between(x_idx, merged["upperb"], merged["lowerb"], color="blue", alpha=0.05, zorder=-8, linewidth=0.0)
#                 except: pass
#         title_str = f"{symbol} {tf} | Rank:{rank_label} | Score:{fmt_f(final_score_norm_arg)} Prox:{fmt_f(prox_norm_arg)} Band:{fmt_f(band_score_arg)} RPg:{fmt_f(rp_global_arg)}"
#         ax.set_title(title_str, fontsize=12, color="#f0b90b", fontweight='bold', pad=10)
#         def date_fmt(x, pos=None):
#             try:
#                 idx = int(round(x))
#                 if 0 <= idx < len(df_tf):
#                     dt = df_tf.iloc[idx]["close_time"].tz_convert("US/Eastern")

#                     if tf == "D":
#                         return dt.strftime("%b %d")

#                     if tf in ("4h", "1h"):
#                         return dt.strftime("%d %b\n%H:%M")

#                     return dt.strftime("%H:%M")
#             except:
#                 pass
#             return ""
#         import matplotlib.ticker as mticker
#         ax.xaxis.set_major_locator(mticker.MaxNLocator(nbins=8, prune="both"))
#         ax.xaxis.set_major_formatter(mticker.FuncFormatter(date_fmt))
#         ax.tick_params(axis='x', rotation=15, labelsize=9)
#         ax.tick_params(axis='y', labelsize=9)
#     from ez_rankings import get_legend_handles_labels
#     handles, labels = get_legend_handles_labels(axes)
#     if labels:
#         fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 1.00), ncol=min(len(labels), 8), fontsize=8, frameon=False, labelcolor='#d0d0d0')
#     fig.tight_layout(rect=[0, 0.03, 1, 0.95])
#     if isinstance(output_dir, str):
#         output_dir = Path(output_dir)
#     output_dir.mkdir(parents=True, exist_ok=True)
#     safe_rank = str(rank_label).replace("#","_").replace(":","_").zfill(2)
#     fpath = output_dir / f"{safe_rank}_{symbol}.png"
#     try: fig.savefig(fpath, facecolor=fig.get_facecolor(), bbox_inches='tight')
#     except Exception as e: logger.error(f"Failed to save plot {fpath}: {e}")
#     finally: plt.close(fig)
# async def plot_dfs_subplots(rank_label: str, symbol: str, dfs: dict, final_score: float, 
#     final_score_norm_arg: float, final_score_recent_arg: float,
#     prox_norm_arg: float, band_score_arg: float, trend_val_arg: float, rp_global_arg: float,
#     proximity_score_norm: float = 0.0,
#     rel_volume: float = 0.0, slope_str: str = "", highlight_heatmap: bool = True,
#     linear_val: float = 0.0, rel_gains: float = 0.0,
#     output_dir: str = "./plots", RANKING_INFO_ARG: dict = None
# ):
#     trb_trades = await fetch_trb_trades_from_log(symbol)
#     # Define exact Durations
#     lookbacks = {
#         "D": timedelta(days=365), "4h": timedelta(days=120),
#         "1h": timedelta(days=30), "15m": timedelta(days=7), "5m": timedelta(days=2)
#     }
#     tfs = ["D", "4h", "1h", "15m", "5m"]
#     filtered_dfs = {}
#     now_utc = datetime.now(timezone.utc)
#     # timeframe_days = {"D": 365, "4h": 66, "1h": 14, "15m": 5, "5m": 1} 
#     for tf in tfs:
#         if tf in dfs:
#             # Drop overnight gaps
#             df_c = filter_market_hours(dfs[tf], tf)
#             # Trim to exact duration
#             cutoff = now_utc - lookbacks.get(tf)
#             df_c = df_c[df_c['close_time'] >= cutoff]
#             if not df_c.empty:
#                 filtered_dfs[tf] = df_c
#     valid_tfs = [tf for tf in tfs if tf in filtered_dfs]
#     if not valid_tfs: return
#     fig, axes = plt.subplots(len(valid_tfs), 1, figsize=(16, 5 * len(valid_tfs)), dpi=180)
#     if len(valid_tfs) == 1: axes = [axes]
#     fig.patch.set_facecolor("#1a1d1e")
#     for i, tf_plot in enumerate(valid_tfs):
#         ax = axes[i]
#         df = filtered_dfs[tf_plot].reset_index(drop=True)
#         x_indices = np.arange(len(df))
#         # --- A. PRICE PLOT ---
#         ax.set_facecolor("#1a1d1e")
#         ax.plot(x_indices, df["close"], color="#d1d1d1", lw=1.8, zorder=5)
#         # --- B. STOCH RSI GRADIENT (The Background Heatmap) ---
#         if "stoch_rsi" in df.columns and not df["stoch_rsi"].isnull().all():
#             st_vals = np.clip(df["stoch_rsi"].fillna(50).values / 100.0, 0, 1)
#             img = np.zeros((1, len(st_vals), 4))
#             img[0, :, 0] = st_vals       # Red
#             img[0, :, 1] = 1.0 - st_vals # Green
#             img[0, :, 3] = 0.12          # Alpha
#             ymin, ymax = df["close"].min(), df["close"].max()
#             ax.imshow(img, extent=[0, len(df)-1, ymin*0.9, ymax*1.1], aspect='auto', origin='lower', zorder=1)
#         # --- C. TRADES (BLUE=Buy, RED=Sell) ---
#         if trb_trades:
#             for trade in trb_trades:
#                 # Ensure UTC comparison
#                 trade_ts = pd.to_datetime(trade['timestamp'], utc=True)
#                 # Binary search for the index
#                 idx = df['close_time'].searchsorted(trade_ts)
#                 if 0 <= idx < len(df):
#                     # Tolerance: Broad for Daily, Strict for Intraday
#                     max_gap = 86400 if tf_plot == "D" else 3600
#                     diff = abs((df.iloc[idx]['close_time'] - trade_ts).total_seconds())
#                     if diff <= max_gap:
#                         # Logic check: 'buy' or 'augmented' = blue, 'sell' or 'reduced' = red
#                         is_buy = trade['type'].lower() in ['buy', 'augmented']
#                         l_color = '#00bfff' if is_buy else '#ff3333'
#                         # Scale thickness by value / 5000
#                         thickness = min(max(trade['value'] / 5000, 1.5), 7.0)
#                         ax.axvline(x=idx, color=l_color, lw=thickness, alpha=0.7, zorder=6)
#         # --- D. REGRESSION LINE ---
#         if len(df) > 10:
#             try:
#                 # Assuming this function is available and returns (slope, rvv, yhat)
#                 from utils_plotting import calculate_regression_slope_line
#                 _, _, yhat = calculate_regression_slope_line(df[['close_time','close']])
#                 ax.plot(x_indices, yhat, color="#4169E1", linestyle="--", lw=1, alpha=0.8, zorder=7)
#             except: pass
#         # --- E. THE X-AXIS TIME SCALE (THE FIX) ---
#         def x_formatter(val, pos):
#             idx = int(val)
#             if 0 <= idx < len(df):
#                 dt = df.iloc[idx]['close_time'].tz_convert('US/Eastern')
#                 if tf_plot == "D":
#                     # Yearly: show Month Year (e.g. Oct 24)
#                     return dt.strftime('%b %y') if idx % 22 == 0 else ""
#                 elif tf_plot in ["4h", "1h"]:
#                     # Monthly: show Month-Day
#                     return dt.strftime('%m-%d') if idx % 15 == 0 else ""
#                 else:
#                     # Intraday: show HH:MM
#                     return dt.strftime('%H:%M')
#             return ""
#         ax.xaxis.set_major_locator(mticker.MaxNLocator(nbins=12 if tf_plot == "D" else 8))
#         ax.xaxis.set_major_formatter(mticker.FuncFormatter(x_formatter))
#         ax.tick_params(colors='#d0d0d0', labelsize=9)
#         ax.grid(color="#4a4a4a", alpha=0.3, linestyle=':')
#         # --- F. HEADER LABELS ---
#         def fmt(v): return f"{float(v):.2f}" if v is not None else "0.00"
#         score = final_score_norm_arg
#         lin = linear_val
#         trend = trend_val_arg
#         ax.set_title(f"{symbol} {tf_plot} | Score:{fmt(score)} | Trend:{fmt(trend)} | Lin:{fmt(lin)} | Trades:{len(trb_trades)}", 
#                      color="#9c864e", fontsize=12, loc='left', pad=10)
#     plt.tight_layout()
#     # Save Logic
#     output_dir.mkdir(exist_ok=True)
#     safe_rank = str(rank_label).zfill(2)
#     fpath = output_dir / f"{safe_rank}_{symbol}.png"
#     fig.savefig(fpath, facecolor=fig.get_facecolor())
#     plt.close(fig)
# async def plot_dfs_subplots(
#     rank_label: str, symbol: str, dfs: dict, final_score: float, 
#     final_score_norm_arg: float, final_score_recent_arg: float,
#     prox_norm_arg: float, band_score_arg: float, trend_val_arg: float, rp_global_arg: float,
#     proximity_score_norm: float = 0.0,
#     rel_volume: float = 0.0, slope_str: str = "", highlight_heatmap: bool = True,
#     linear_val: float = 0.0, rel_gains: float = 0.0,
#     output_dir: str = "./plots", RANKING_INFO_ARG: dict = None
# ):
#     # 1. Fetch Trades from Log (Defensive Parser)
#     trb_trades = await fetch_trb_trades_from_log(symbol)
#     # 2. Setup Timeframes and Lookbacks
#     # D: 1 Year, 4h: 4 Months, 1h: 1 Month, 15m: 1 Week, 5m: 2 Days
#     lookbacks = {
#         "D": timedelta(days=365), "4h": timedelta(days=120),
#         "1h": timedelta(days=30), "15m": timedelta(days=7), "5m": timedelta(days=2)
#     }
#     tfs = ["D", "4h", "1h", "15m", "5m"]
#     # 3. Clean and Trim Data (Market Hours Only)
#     filtered_dfs = {}
#     now_utc = datetime.now(timezone.utc)
#     for tf in tfs:
#         if tf not in dfs or dfs[tf].empty: continue
#         df = dfs[tf].copy()
#         df['close_time'] = pd.to_datetime(df['timestamp'], utc=True)
#         # Filter overnight gaps for intraday (1m - 4h)
#         if tf != "D":
#             df_est = df.set_index('close_time').tz_convert('US/Eastern')
#             df_est = df_est.between_time('09:30', '16:00')
#             df_est = df_est[df_est.index.dayofweek < 5]
#             df = df_est.reset_index()
#         else:
#             # Just remove weekends for Daily
#             df = df[df['close_time'].dt.dayofweek < 5]
#         # Trim to the exact lookback duration requested
#         cutoff = now_utc - lookbacks.get(tf, timedelta(days=1))
#         df = df[df['close_time'] >= cutoff].reset_index(drop=True)
#         if not df.empty:
#             filtered_dfs[tf] = df
#     valid_tfs = [tf for tf in tfs if tf in filtered_dfs]
#     if not valid_tfs: return
#     # 4. Initialize Plot
#     fig, axes = plt.subplots(len(valid_tfs), 1, figsize=(14, 4 * len(valid_tfs)), dpi=110)
#     if len(valid_tfs) == 1: axes = [axes]
#     fig.patch.set_facecolor("#272d30")
#     def fmt_f(val):
#         try: return f"{float(val):.2f}"
#         except: return "0.00"
#     # 5. Loop Timeframes
#     for i, tf_plot in enumerate(valid_tfs):
#         ax = axes[i]
#         df_tf = filtered_dfs[tf_plot]
#         # Important: Use integer indices for x to eliminate gaps
#         x_indices = np.arange(len(df_tf))
#         ax.set_facecolor("#272d30")
#         for spine in ax.spines.values(): spine.set_color("#d0d0d0")
#         ax.grid(color="#4a4a4a", alpha=0.4, linestyle=':')
#         # --- A. STOCH RSI GRADIENT ---
#         if "stoch_rsi" in df_tf.columns and not df_tf["stoch_rsi"].isnull().all():
#             stoch_vals = df_tf["stoch_rsi"].fillna(50).values / 100.0
#             stoch_vals = np.clip(stoch_vals, 0, 1)
#             # Create a 1-pixel high RGBA image for the background
#             img_data = np.zeros((1, len(stoch_vals), 4))
#             img_data[0, :, 0] = stoch_vals       # Red component
#             img_data[0, :, 1] = 1.0 - stoch_vals # Green component
#             img_data[0, :, 3] = 0.12             # Alpha
#             ymin, ymax = df_tf["low"].min(), df_tf["high"].max()
#             margin = (ymax - ymin) * 0.1 if ymax > ymin else 0.1
#             ax.imshow(img_data, extent=[x_indices[0], x_indices[-1], ymin-margin, ymax+margin], 
#                       aspect='auto', origin='lower', zorder=-10)
#         # --- B. PRICE LINE ---
#         ax.plot(x_indices, df_tf["close"], color="#c0c0c0", lw=1.5, zorder=5, label="Close")
#         # --- C. REGRESSION LINE ---
#         if len(df_tf) > 5:
#             try:
#                 # Assuming calculate_regression_slope_line is available globally
#                 _, _, yhat = calculate_regression_slope_line(df_tf[['close_time','close']].copy())
#                 if len(yhat) == len(df_tf):
#                     ax.plot(x_indices, yhat, color="#0000ff", linestyle="--", lw=1, alpha=0.8, zorder=6)
#             except: pass
#         # --- D. TOPS / BOTTOMS ---
#         try:
#             # Assuming detect_tops_bottoms is available globally
#             df_tb = detect_tops_bottoms(df_tf.copy(), distance=10, prominence=1e-5)
#             tops = df_tb.dropna(subset=["top"])
#             bots = df_tb.dropna(subset=["bottom"])
#             ax.scatter(tops.index, tops["top"], color="red", marker="v", s=25, zorder=10)
#             ax.scatter(bots.index, bots["bottom"], color="lime", marker="^", s=25, zorder=10)
#         except: pass
#         # --- E. TRADE VERTICAL LINES ---
#         if trb_trades:
#             for trade in trb_trades:
#                 trade_dt = pd.to_datetime(trade['timestamp'], utc=True)
#                 # Map trade time to the closest available index in our filtered DF
#                 idx = df_tf['close_time'].searchsorted(trade_dt)
#                 if 0 <= idx < len(df_tf):
#                     # For Daily allow matching any time that day. For intraday, match within 1 hour.
#                     max_diff = 86400 if tf_plot == "D" else 3600
#                     if abs((df_tf.iloc[idx]['close_time'] - trade_dt).total_seconds()) <= max_diff:
#                         l_color = '#00bfff' if trade['type'] == 'buy' else '#ff4444'
#                         # Width scaled by USD value / 5000
#                         l_width = min(max(trade['value'] / 5000, 1.0), 6.0)
#                         ax.axvline(x=idx, color=l_color, linewidth=l_width, alpha=0.6, zorder=1)
#         # --- F. X-AXIS FORMATTING ---
#         def date_formatter(val, pos):
#             idx = int(val)
#             if 0 <= idx < len(df_tf):
#                 dt = df_tf.iloc[idx]['close_time'].tz_convert('US/Eastern')
#                 if tf_plot == "D":
#                     return dt.strftime('%b %y') if idx % 22 == 0 else ""
#                 elif tf_plot in ["4h", "1h"]:
#                     return dt.strftime('%m-%d') if idx % 15 == 0 else ""
#                 else:
#                     return dt.strftime('%H:%M')
#             return ""
#         ax.xaxis.set_major_locator(mticker.MaxNLocator(nbins=12 if tf_plot == "D" else 8))
#         ax.xaxis.set_major_formatter(mticker.FuncFormatter(date_formatter))
#         ax.tick_params(colors='#d0d0d0', labelsize=8)
#         # --- G. TITLE / HEADERS ---
#         # Fixed Score selection logic
#         score = final_score_norm_arg if final_score_norm_arg != 0 else final_score
#         hdr = (f"n{fmt_f(score)} r{fmt_f(final_score_recent_arg)} "
#                f"p{fmt_f(prox_norm_arg)} b{fmt_f(band_score_arg)}")
#         title_str = f"{symbol} {tf_plot} | Trades: {len(trb_trades)}"
#         if tf_plot in ["5m", "D"]:
#             title_str += (f" | Rank:{rank_label} | {hdr} | "
#                           f"Trend:{fmt_f(trend_val_arg)} | Lin:{fmt_f(linear_val)} | "
#                           f"Rv:{fmt_f(rel_volume)}")
#         ax.set_title(title_str, color="#9c864e", fontsize=11, loc='left')
#     # 6. Save and Cleanup
#     fig.tight_layout()
#     safe_rank_label = str(rank_label).zfill(2)
#     fpath = Path(output_dir) / f"{safe_rank_label}_{symbol}.png"
#     try:
#         fig.savefig(fpath)
#         # logger.info(f"✅ Plotted {symbol} to {fpath}")
#     except Exception as e:
#         logger.error(f"Failed to save plot {fpath}: {e}")
#     finally:
#         plt.close(fig)
# async def plot_dfs_subplots(rank_label, symbol, dfs, **kwargs):
#     trb_trades = await fetch_trb_trades_from_log(symbol)
#     tfs = ["D", "4h", "1h", "15m", "5m"]
#     filtered_dfs = {}
#     for tf in tfs:
#         if tf in dfs:
#             filtered_dfs[tf] = filter_market_hours(dfs[tf], tf)
#     valid_tfs = [tf for tf in tfs if tf in filtered_dfs and not filtered_dfs[tf].empty]
#     if not valid_tfs: return
#     fig, axes = plt.subplots(len(valid_tfs), 1, figsize=(14, 4 * len(valid_tfs)), dpi=180)
#     if len(valid_tfs) == 1: axes = [axes]
#     fig.patch.set_facecolor("#272d30")
#     for i, tf_plot in enumerate(valid_tfs):
#         ax = axes[i]
#         df = filtered_dfs[tf_plot].reset_index(drop=True)
#         x_indices = np.arange(len(df))
#         # --- PLOT PRICE ---
#         ax.set_facecolor("#272d30")
#         ax.plot(x_indices, df["close"], color="#c0c0c0", lw=1.5, zorder=2)
#         ax.grid(color="#4a4a4a", alpha=0.3, linestyle=':')
#         # --- PLOT ALL TRADES (Binary Search for Index) ---
#         if trb_trades:
#             for trade in trb_trades:
#                 trade_ts = pd.to_datetime(trade['timestamp'], utc=True)
#                 idx = df['close_time'].searchsorted(trade_ts)
#                 if 0 <= idx < len(df):
#                     # Tolerance: 1 day for Daily, 1 hour for Intraday
#                     max_gap = 86400 if tf_plot == "D" else 3600
#                     if abs((df.iloc[idx]['close_time'] - trade_ts).total_seconds()) <= max_gap:
#                         color = '#00bfff' if trade['type'] == 'buy' else '#ff4444'
#                         thickness = min(max(trade['value'] / 5000, 1.2), 6.0)
#                         ax.axvline(x=idx, color=color, lw=thickness, alpha=0.6, zorder=1)
#         # --- X-AXIS TIME SCALE FIX ---
#         def date_formatter(val, pos):
#             idx = int(val)
#             if 0 <= idx < len(df):
#                 dt = df.iloc[idx]['close_time'].tz_convert('US/Eastern')
#                 if tf_plot == "D":
#                     # Yearly: Month + Year (e.g., Feb 25)
#                     return dt.strftime('%b %y') if idx % 22 == 0 else ""
#                 elif tf_plot in ["4h", "1h"]:
#                     # Intermediate: Date
#                     return dt.strftime('%m-%d') if idx % 15 == 0 else ""
#                 else:
#                     # Intraday: Time
#                     return dt.strftime('%H:%M')
#             return ""
#         ax.xaxis.set_major_locator(mticker.MaxNLocator(nbins=12 if tf_plot == "D" else 8))
#         ax.xaxis.set_major_formatter(mticker.FuncFormatter(date_formatter))
#         ax.tick_params(colors='#d0d0d0', labelsize=8)
#     # filtered_dfs = {}
#     # for tf in tfs:
#     #     if tf not in dfs or dfs[tf].empty:
#     #         continue
#     #     filtered_dfs[tf] = filter_market_hours(dfs[tf])
#     # valid_tfs_with_data = [tf for tf in tfs if tf in filtered_dfs and not filtered_dfs[tf].empty]
#     # if not valid_tfs_with_data:
#     #     return
#     # fig, axes = plt.subplots(len(valid_tfs_with_data), 1,  figsize=(14, 4 * len(valid_tfs_with_data)),   dpi=140)
#     # if len(valid_tfs_with_data) == 1:
#     #     axes = [axes]
# # async def plot_dfs_subplots(
# #     rank_label: str, symbol: str, dfs: dict, final_score: float, 
# #     final_score_norm_arg: float, final_score_recent_arg: float,
# #     prox_norm_arg: float, band_score_arg: float, trend_val_arg: float, rp_global_arg: float,
# #     proximity_score_norm: float = 0.0,
# #     rel_volume: float = 0.0, slope_str: str = "", highlight_heatmap: bool = True,
# #     linear_val: float = 0.0, rel_gains: float = 0.0,
# #     output_dir: str = "./plots", RANKING_INFO_ARG: dict = None):
# #     # 1. Fetch Trades for Overlay
# #     trb_trades = await fetch_trb_trades_from_log(symbol)
# #     logger.info(f'PLOTTING {rank_label} {symbol}')
# #     tfs = ["D", "4h", "1h", "15m", "5m"]  
# #     valid_tfs_with_data = [tf for tf in tfs if tf in dfs and not dfs[tf].empty]
# #     if not valid_tfs_with_data:
# #         return
# #     # Filter data for Market Hours (Gaps Handling Step 1)
# #     filtered_dfs = {}
# #     for tf in valid_tfs_with_data:
# #         # Only apply strict market hours to intraday timeframes to avoid breaking Daily charts
# #         if tf in ["15m", "5m", "1m"]:
# #             filtered_dfs[tf] = filter_market_hours(dfs[tf])
# #         else:
# #             filtered_dfs[tf] = dfs[tf].copy() # Keep Daily/4h intact or apply lighter filter
# #     valid_tfs_with_data = [tf for tf in tfs if tf in filtered_dfs and not filtered_dfs[tf].empty]
# #     if not valid_tfs_with_data:
# #         return
# #     fig, axes = plt.subplots(len(valid_tfs_with_data), 1, figsize=(14, 4 * len(valid_tfs_with_data)), dpi=250)
# #     if len(valid_tfs_with_data) == 1:
# #         axes = [axes]
#     fig.patch.set_facecolor("#272d30")
#     # Common formatting helper
#     def fmt_f(val_fmt):
#         try: return f"{float(val_fmt):.2f}"
#         except (TypeError, ValueError): return "NaN"
#     for i, tf_plot_loop in enumerate(valid_tfs_with_data):
#         ax_plot = axes[i]
#         df_tf = filtered_dfs[tf_plot_loop].copy()
#         # IMPORTANT: Use index-based plotting to jump over gaps
#         df_tf = df_tf.reset_index(drop=True)
#         x_indices = np.arange(len(df_tf))
#     # for i, tf_plot_loop in enumerate(valid_tfs_with_data):
#     #     ax_plot = axes[i]
#     #     df_tf = filtered_dfs[tf_plot_loop].copy()
#     #     # --- SETUP INDEX-BASED PLOTTING (Gaps Handling Step 2) ---
#     #     # Reset index to create a sequential range (0, 1, 2, ... N)
#     #     df_tf = df_tf.reset_index(drop=True)
#     #     x_indices = np.arange(len(df_tf)) 
#         # Ensure we have data
#         if len(df_tf) < 2: continue
#         # --- PLOT PRICE (Using Index x_indices) ---
#         ax_plot.set_facecolor("#272d30")
#         ax_plot.grid(color="#4a4a4a", alpha=0.6, linestyle=':')
#         # Plot Close line
#         ax_plot.plot(x_indices, df_tf["close"], color="#c0c0c0", lw=1.2, label="Close")
#         # Calculate Y Limits
#         ymin = df_tf["low"].min()
#         ymax = df_tf["high"].max()
#         if pd.isna(ymin) or pd.isna(ymax): 
#             ymin, ymax = df_tf["close"].min(), df_tf["close"].max()
#         margin = (ymax - ymin) * 0.05
#         ax_plot.set_ylim(ymin - margin, ymax + margin)
#         ax_plot.set_xlim(x_indices[0], x_indices[-1])
#         if trb_trades:
#             for trade in trb_trades:
#                 # 1. Use a unique variable name (not 'time')
#                 trade_ts = trade['timestamp']
#                 # 2. Ensure it's a full datetime
#                 if not isinstance(trade_ts, pd.Timestamp) and not isinstance(trade_ts, datetime):
#                     trade_ts = pd.to_datetime(trade_ts)
#                 try:
#                     # Searchsorted is fine
#                     closest_idx = df_tf['close_time'].searchsorted(trade_ts)
#                     if 0 <= closest_idx < len(df_tf):
#                         candle_ts = df_tf.iloc[closest_idx]['close_time']
#                         # 3. Defensive check: Ensure we have datetimes before subtracting
#                         if hasattr(candle_ts, 'hour') and hasattr(trade_ts, 'hour'):
#                             # Calculate difference safely
#                             diff = (candle_ts - trade_ts).total_seconds()
#                             time_diff = abs(diff)
#                             max_gap = {"D": 86400, "4h": 14400, "1h": 3600}.get(tf_plot_loop, 900)
#                             if time_diff <= max_gap:
#                                 l_color = '#00bfff' if trade['type'] == 'buy' else '#ff4444'
#                                 usd_val = float(trade.get('value', 0))
#                                 # Use / 100 for Crypto, / 5000 for Tradier
#                                 divisor = 100 if "3m" in valid_tfs_with_data else 5000
#                                 l_width = min(max(usd_val / divisor, 0.8), 6.0) 
#                                 ax_plot.axvline(x=closest_idx, color=l_color, linewidth=l_width, alpha=0.7, zorder=1)
#                 except Exception as e:
#                     logger.debug(f"Trade plot skip: {e}")
#                     pass
#         # Regression Line
#         if len(df_tf) > 2:
#             try:
#                 slope, rvv, yhat = calculate_regression_slope_line(df_tf[['close_time','close']].copy())
#                 if len(yhat) == len(df_tf):
#                     ax_plot.plot(x_indices, yhat, color="blue", linestyle="--", lw=1, alpha=0.7)
#             except: pass
#         # Tops/Bottoms
#         # Re-calculate on filtered data so markers align
#         df_tb = detect_tops_bottoms(df_tf.copy(), distance=5, prominence=1e-5)
#         tops = df_tb.dropna(subset=["top"])
#         bots = df_tb.dropna(subset=["bottom"])
#         # We must map the tops/bots timestamps back to our integer indices
#         if not tops.empty:
#             top_indices = tops.index 
#             ax_plot.scatter(top_indices, tops["top"], color="red", marker="v", s=15, zorder=5)
#         if not bots.empty:
#             bot_indices = bots.index
#             ax_plot.scatter(bot_indices, bots["bottom"], color="lime", marker="^", s=15, zorder=5)
#         # --- FORMAT X-AXIS (The Magic Step) ---
#         # Since we plotted 0..N, we need to manually write the dates on the ticks
#         def format_date(x, pos=None):
#             idx = int(x)
#             if 0 <= idx < len(df_tf):
#                 ts = df_tf.iloc[idx]['close_time']
#                 return ts.strftime('%H:%M\n%m-%d')
#             return ""
#         # Use this inside the loop in plot_dfs_subplots
#         def format_date_dynamic(x, pos=None):
#             idx = int(x)
#             if 0 <= idx < len(df_tf):
#                 dt = df_tf.iloc[idx]['close_time']
#                 # If D, show Date. If Intraday, show Time + Date
#                 if tf_plot_loop == "D":
#                     return dt.strftime('%b %Y') if idx % 20 == 0 else "" # Month every ~20 bars
#                 else:
#                     return dt.strftime('%m-%d\n%H:%M')
#             return ""
#         # Apply to axis
#         ax_plot.xaxis.set_major_locator(plt.MaxNLocator(nbins=10))
#         ax_plot.xaxis.set_major_formatter(plt.FuncFormatter(format_date_dynamic))
#         # ax_plot.xaxis.set_major_locator(MaxNLocator(nbins=10, integer=True))
#         # ax_plot.xaxis.set_major_formatter(plt.FuncFormatter(format_date))
#         ax_plot.tick_params(axis='x', colors='#d0d0d0', labelsize=8)
#         ax_plot.tick_params(axis='y', colors='#d0d0d0', labelsize=8)
#         if tf_plot_loop == "D":
#             # 1 year has ~252 trading days. 12 ticks = roughly 1 per month
#             ax_plot.xaxis.set_major_locator(plt.MaxNLocator(nbins=12))
#         else:
#             ax_plot.xaxis.set_major_locator(plt.MaxNLocator(nbins=8))
#         header_score = f'n{fmt_f(final_score_norm_arg)}  r{fmt_f(final_score_recent_arg)} p{fmt_f(prox_norm_arg)} b{fmt_f(band_score_arg)}'  #if final_score_norm_arg != 0 else final_score
#         title_str = f"{symbol} {tf_plot_loop} | Trades: {len(trb_trades)}"
#         if tf_plot_loop in ["5m", "D"]:
#             title_str += f" | Rank:{rank_label} | Score:{fmt_f(header_score)} | Trend:{fmt_f(trend_val_arg)} | Lin:{fmt_f(linear_val)} Rv{fmt_f(rel_volume)} Sl {fmt_f(slope_str)}"
#         ax_plot.set_title(title_str, color="#9c864e", fontsize=12)
#         # Format X-Axis dates properly
#         ax_plot.xaxis.set_major_locator(plt.MaxNLocator(nbins=8))
#         ax_plot.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: df_tf.iloc[int(x)]['close_time'].strftime('%m-%d %H:%M') if 0 <= int(x) < len(df_tf) else ""))
#     fig.tight_layout()
#         # title_str = f"{symbol} {tf_plot_loop} (Trades: {len(trb_trades)})"
#         # if tf_plot_loop == "5m":
#         #      title_str += f" | Rank:{rank_label} | Score:{fmt_f(final_score_norm_arg)}"
#         # ax_plot.set_title(title_str, color="#9c864e", fontsize=10)
#     # Save
#     # fig.tight_layout()
#     safe_rank_label = str(rank_label).zfill(2)
#     fpath = Path(output_dir) / f"{safe_rank_label}_{symbol}.png"
#     try:
#         fig.savefig(fpath)
#     except Exception as e:
#         logger.error(f"Failed to save plot {fpath}: {e}")
#     finally:
#         plt.close(fig)
# # ===== TRADIER-SPECIFIC PLOTTING FUNCTION =====
# async def plot_dfs_subplots(
#     rank_label: str, symbol: str, dfs: dict, final_score: float, 
#     final_score_norm_arg: float, final_score_recent_arg: float,
#     prox_norm_arg: float, band_score_arg: float, trend_val_arg: float, rp_global_arg: float,
#     proximity_score_norm: float = 0.0,
#     rel_volume: float = 0.0, slope_str: str = "", highlight_heatmap: bool = True,
#     linear_val: float = 0.0, rel_gains: float = 0.0,
#     output_dir: str = "./plots", RANKING_INFO_ARG: dict = None
# ):
#     tfs = ["D", "4h", "1h", "15m", "5m"]  
#     valid_tfs_with_data = [tf for tf in tfs if tf in dfs and not dfs[tf].empty]
#     if not valid_tfs_with_data:
#         return
#     timeframe_days = {"D": 365, "4h": 66, "1h": 14, "15m": 5, "5m": 1} 
#     # Updated overlay logic: D is now the master HTF
#     bigger_tfs_map = {
#         "5m": ["15m"], 
#         "15m": ["1h"], 
#         "1h": ["4h"], 
#         "4h": ["D"], 
#         "D": []     }
#     valid_tfs_with_data = [tf_loop for tf_loop in tfs if tf_loop in dfs and isinstance(dfs[tf_loop], pd.DataFrame) and not dfs[tf_loop].empty]
#     price_result, _ = await get_current_price(symbol)
#     current_price = price_result if price_result else None
#     if len(valid_tfs_with_data) < len(tfs):
#         missing_tfs = [tf for tf in tfs if tf not in valid_tfs_with_data]
#         logger.warning(f"[plot_dfs_subplots] => Only {len(valid_tfs_with_data)}/{len(tfs)} timeframes have data for {symbol}. Missing: {missing_tfs}")
#     if not valid_tfs_with_data:
#         logger.warning(f"[plot_dfs_subplots] => No valid TF data for {symbol}, skipping.")
#         return
#     fig, axes = plt.subplots(len(valid_tfs_with_data), 1, figsize=(14, 4 * len(valid_tfs_with_data)), dpi=250)
#     if len(valid_tfs_with_data) == 1:
#         axes = [axes]
#     fig.patch.set_facecolor("#272d30")
#     for ax_plot in axes:
#         ax_plot.set_facecolor("#272d30")
#         for spine in ax_plot.spines.values(): spine.set_color("#d0d0d0")
#         ax_plot.tick_params(axis="x", colors="#d0d0d0"); ax_plot.tick_params(axis="y", colors="#d0d0d0")
#         ax_plot.xaxis.label.set_color("#d0d0d0"); ax_plot.yaxis.label.set_color("#d0d0d0")
#         ax_plot.title.set_color("#9c864e")
#         ax_plot.grid(color="#4a4a4a", alpha=0.6, linestyle=':')
#     def fmt_f(val_fmt):
#         try: return f"{float(val_fmt):.2f}"
#         except (TypeError, ValueError): return "NaN"
#     timeframe_days = {"4h": 66, "1h": 14, "15m": 5, "5m": 1, "1m": 0.5}  # Adapted for Tradier timeframes (D removed)
#     plot_signals = True
#     for i, tf_plot_loop in enumerate(valid_tfs_with_data):
#         ax_plot = axes[i]
#         df_tf = dfs[tf_plot_loop].copy()
#         # Plot all available data - no filtering to avoid gaps
#         # Natural gaps (weekends, holidays) will show but won't be artificially created
#         if len(df_tf) < 2:
#             ax_plot.set_title(f"{symbol} {tf_plot_loop}: Insufficient data to plot.")
#             continue
#         for col_check in ["high", "low"]:
#             if col_check not in df_tf.columns:
#                 if 'close' in df_tf.columns: df_tf[col_check] = df_tf['close']
#                 else: 
#                     ax_plot.set_title(f"{symbol} {tf_plot_loop}: Missing price data."); continue 
#         df_tf['close_time'] = pd.to_datetime(df_tf['close_time'], errors='coerce')
#         df_tf.dropna(subset=['close_time'], inplace=True)
#         df_tf.sort_values("close_time", inplace=True)
#         x_left_ts = df_tf["close_time"].iloc[0]
#         x_right_ts = df_tf["close_time"].iloc[-1]
#         ax_plot.set_xlim(x_left_ts, x_right_ts)
#         ymin_data = pd.to_numeric(df_tf["low"], errors='coerce').min()
#         ymax_data = pd.to_numeric(df_tf["high"], errors='coerce').max()
#         if pd.isna(ymin_data) or pd.isna(ymax_data) or np.isclose(ymin_data, ymax_data):
#             current_close_val = pd.to_numeric(df_tf["close"], errors='coerce').iloc[-1] if not df_tf.empty and 'close' in df_tf.columns else 1.0
#             if pd.isna(current_close_val):
#                 current_close_val = 1.0
#             ymin_data = current_close_val * 0.98
#             ymax_data = current_close_val * 1.02
#             if np.isclose(ymin_data, ymax_data):
#                 ymin_data = 0.9
#                 ymax_data = 1.1
#         range_span = ymax_data - ymin_data
#         margin_abs_val = (0.012 * range_span) if range_span > 1e-9 else 0.02
#         ax_plot.set_ylim(ymin_data - margin_abs_val, ymax_data + margin_abs_val)
#         main_close_line = ax_plot.plot(df_tf["close_time"], df_tf["close"], color="#c0c0c0", lw=1.2, label=f"Close")
#         if symbol in ez_rankings.signals_data and tf_plot_loop in ez_rankings.signals_data[symbol]:
#             logger.info(f"[plot_dfs_subplots] Plotting signals for {symbol}-{tf_plot_loop}: {list(ez_rankings.signals_data[symbol][tf_plot_loop].keys())}")
#             for etype, ev_list in ez_rankings.signals_data[symbol][tf_plot_loop].items():
#                 for event in ev_list:
#                     ts_event = event.get("timestamp")
#                     if not isinstance(ts_event, pd.Timestamp):
#                         try: ts_event = pd.to_datetime(ts_event); 
#                         except: continue
#                     if ts_event.tzinfo is None: ts_event = ts_event.tz_localize('UTC')
#                     if ts_event < x_left_ts or ts_event > x_right_ts: continue 
#                     price_event = event.get("price")
#                     reason_event = event.get("reason", etype).lower()
#                     action_event = event.get("action", "").upper()
#                     marker_style = None; color_style = 'orange'; line_style = 'dotted'; marker_size = 6
#                     is_vline = True
#                     if "stoch_crossover" in reason_event: 
#                         color_style = "cyan"
#                         marker_style = 'o'; is_vline = False; marker_size = 4
#                     elif "stoch_crossunder" in reason_event: 
#                         color_style = "magenta"
#                         marker_style = 'o'; is_vline = False; marker_size = 4
#                     elif "wt_crossover" in reason_event or "wt crossover" in reason_event:
#                         color_style = "lime"
#                         marker_style = '^'; is_vline = False; marker_size = 8
#                     elif "wt_crossunder" in reason_event or "wt crossunder" in reason_event:
#                         color_style = "red"
#                         marker_style = 'v'; is_vline = False; marker_size = 8
#                     elif "wt strong alert" in reason_event:
#                         color_style = "lime" if action_event == "BUY" else "red"
#                     elif "winners_up" in reason_event or "losers_down" in reason_event:
#                         is_vline = False; marker_style = '^'; color_style = 'lime'
#                     elif "winners_down" in reason_event or "losers_up" in reason_event:
#                         is_vline = False; marker_style = 'v'; color_style = 'red'
#                     if is_vline:
#                         ax_plot.axvline(x=ts_event, color=color_style, linestyle=line_style, lw=1, zorder=5)
#                     elif marker_style and pd.notna(price_event):
#                         y_pos = price_event
#                         try:
#                             candle_row = df_tf[df_tf['close_time'] >= ts_event].iloc[0]
#                             if marker_style == '^':
#                                 y_pos = candle_row['low'] - margin_abs_val * 0.5
#                             elif marker_style == 'v':
#                                 y_pos = candle_row['high'] + margin_abs_val * 0.5
#                         except IndexError:
#                             pass
#                         ax_plot.plot(ts_event, y_pos, marker=marker_style, color=color_style, ms=marker_size, linestyle='None', zorder=7)
#         if len(df_tf) > 2 and 'close' in df_tf.columns:
#             try:
#                 slope_pct_local, rvv_local, yhat_abs_local = calculate_regression_slope_line(df_tf[['close_time','close']].copy())
#                 if len(yhat_abs_local) == len(df_tf):
#                     slope_str_local_title = f"Slope={fmt_f(slope_pct_local)}%, R={fmt_f(rvv_local)}"
#                     ax_plot.plot(df_tf["close_time"], yhat_abs_local, color="blue", linestyle="--", lw=1, label=f"Reg ({slope_str_local_title})")
#                     band_df_local = calculate_regression_band(df_tf.copy())
#                     if not band_df_local.empty:
#                         ax_plot.fill_between(
#                             band_df_local["time"], band_df_local["upperb"], ymax_data + margin_abs_val,
#                             color="grey", alpha=0.25, where=(band_df_local["upperb"] < (ymax_data + margin_abs_val)), zorder=-5, linewidth=0.0)
#                         ax_plot.fill_between(
#                             band_df_local["time"], ymin_data - margin_abs_val, band_df_local["lowerb"],
#                             color="grey", alpha=0.25, where=(band_df_local["lowerb"] > (ymin_data - margin_abs_val)), zorder=-5, linewidth=0.0)
#             except Exception as e_slope:
#                 logger.debug(f"[plot_dfs_subplots] => Slope/Band line error /{tf_plot_loop}: {e_slope}")
#         if "stoch_rsi" in df_tf.columns and not df_tf["stoch_rsi"].isnull().all():
#             stoch_vals = df_tf["stoch_rsi"].fillna(0.5).values
#             for j in range(1, len(stoch_vals)):
#                 s_val_color = np.clip(stoch_vals[j], 0, 1)
#                 color_fill = (s_val_color, 1.0 - s_val_color, 0.0)
#                 alpha_stoch_g = 0.12
#                 x0, x1 = df_tf["close_time"].iloc[j-1], df_tf["close_time"].iloc[j]
#                 ax_plot.fill_betweenx([ymin_data - margin_abs_val, ymax_data + margin_abs_val], x0, x1, color=color_fill, alpha=alpha_stoch_g, zorder=-15, linewidth=0.0)
#         distance_val = 5 if tf_plot_loop == "1m" else (10 if tf_plot_loop == "5m" else 20)
#         df_tf_tops_bottoms = detect_tops_bottoms(df_tf.copy(), distance=distance_val, prominence=1e-5)
#         topdf = df_tf_tops_bottoms.dropna(subset=["top"])
#         botdf = df_tf_tops_bottoms.dropna(subset=["bottom"])
#         top_size = 12 if tf_plot_loop == "1m" else (15 if tf_plot_loop == "5m" else 20)
#         bottom_size = 12 if tf_plot_loop == "1m" else (15 if tf_plot_loop == "5m" else 20)
#         ax_plot.scatter(topdf["close_time"], topdf["top"], color="red", marker="v", s=top_size, zorder=3, label="Top")
#         ax_plot.scatter(botdf["close_time"], botdf["bottom"], color="lime", marker="^", s=bottom_size, zorder=3, label="Bottom")
#         if highlight_heatmap and "proximity_score_norm" in df_tf.columns and not df_tf["proximity_score_norm"].isnull().all():
#             prox_gradient_vals = pd.to_numeric(df_tf["proximity_score_norm"], errors='coerce').fillna(0).values
#             if len(prox_gradient_vals) > 1:
#                  add_gradient(ax_plot, x_left_ts, x_right_ts,
#                              ymin_data - margin_abs_val, ymax_data + margin_abs_val,
#                              gradient_vals=prox_gradient_vals, slices=200, alpha=0.025,
#                              zorder=-10, is_vertical=True)
#         bigger_tfs_map = { "15m": ["5m"], "1h": ["15m"], "4h": ["1h", "15m", "5m"]}  # Adapted for Tradier timeframes (D removed)"1m": [],
#         htfs_to_overlay = bigger_tfs_map.get(tf_plot_loop, [])
#         for btf_overlay in htfs_to_overlay:
#             if btf_overlay in dfs and isinstance(dfs[btf_overlay], pd.DataFrame) and not dfs[btf_overlay].empty:
#                 df_btf_data = dfs[btf_overlay].copy()
#                 if 'close_time' in df_btf_data.columns and 'close' in df_btf_data.columns:
#                     band_htf_overlay = calculate_regression_band(df_btf_data)
#                     if not band_htf_overlay.empty:
#                         df_tf_merged_htf_band = merge_htf_band_into_ltf(df_tf.copy(), band_htf_overlay)
#                         ax_plot.plot(df_tf_merged_htf_band["close_time"], df_tf_merged_htf_band["upperb"], color="blue", linestyle=":", lw=0.8, alpha=0.5, label=f"{btf_overlay} UB")
#                         ax_plot.plot(df_tf_merged_htf_band["close_time"], df_tf_merged_htf_band["lowerb"], color="blue", linestyle=":", lw=0.8, alpha=0.5, label=f"{btf_overlay} LB")
#                         ax_plot.fill_between(
#                             df_tf_merged_htf_band["close_time"],
#                             df_tf_merged_htf_band["upperb"], df_tf_merged_htf_band["lowerb"],
#                             color="blue", alpha=0.05, zorder=-8, linewidth=0.0
#                         )
#         title_str = f"{symbol} {tf_plot_loop}"
#         if tf_plot_loop == "1m":  # Use 1m for most detailed title
#             title_str = (f"{rank_label} {symbol} {tf_plot_loop} FS_N:{fmt_f(final_score_norm_arg)} FS_R:{fmt_f(final_score_recent_arg)} | "
#                          f"Prox:{fmt_f(prox_norm_arg)} Band:{fmt_f(band_score_arg)} | "
#                          f"Trend:{fmt_f(trend_val_arg)} RPg:{fmt_f(rp_global_arg)}")
#         elif tf_plot_loop == "5m":  # Use 5m for detailed title
#             title_str = (f"{rank_label} {symbol} {tf_plot_loop} FS_N:{fmt_f(final_score_norm_arg)} FS_R:{fmt_f(final_score_recent_arg)} | "
#                          f"Prox:{fmt_f(prox_norm_arg)} Band:{fmt_f(band_score_arg)} | "
#                          f"Trend:{fmt_f(trend_val_arg)} RPg:{fmt_f(rp_global_arg)}")
#         elif tf_plot_loop == "D":  # Use D instead of 4h for summary title
#             title_str = (f"{rank_label} {symbol} {tf_plot_loop} FS_N:{fmt_f(final_score_norm_arg)} FS_R:{fmt_f(final_score_recent_arg)} | "
#                          f"RawFS:{fmt_f(final_score)} Lin:{fmt_f(linear_val)} | Slopes(summ): {slope_str[:50]}")
#         ax_plot.set_title(title_str, fontsize=14)
#         ax_plot.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M %d-%b'))
#         ax_plot.tick_params(axis='x', rotation=15, labelsize=8)
#         ax_plot.tick_params(axis='y', labelsize=8)
#     handles_combined, labels_combined = get_legend_handles_labels(axes)
#     if labels_combined:
#         fig.legend(handles_combined, labels_combined, loc='upper center', 
#                    bbox_to_anchor=(0.5, 1.00),
#                    ncol=min(len(labels_combined), 8),
#                    fontsize=7, frameon=False, labelcolor='#d0d0d0')
#     fig.tight_layout(rect=[0, 0.03, 1, 0.95])
#     os.makedirs(output_dir, exist_ok=True)
#     safe_rank_label = rank_label.zfill(2) # Ensures 01, 02 for sorting
#     fpath = Path(output_dir) / f"{safe_rank_label}_{symbol}.png"
#     try:
#         fig.savefig(fpath)
#     except Exception as e_save:
#         logger.error(f"Failed to save plot {fpath}: {e_save}")
#     finally:
#         plt.close(fig)
TRADIER_MASTER_MIN_SYMBOLS = 100
TRADIER_MASTER_BACKUP = Path(config.BASE_PATH) / "symbols_tradier.last_known_good.json"
TRADIER_DERIVED_FILES_LOCK = Path(config.BASE_PATH) / ".symbols_trb_trc.lock"
_INSTANCE_LOCK_HANDLE = None


def _validated_symbol_list(path: Path) -> List[str]:
    with open(path, "r") as f:
        raw_symbols = json.load(f)
    if not isinstance(raw_symbols, list):
        raise ValueError("top-level JSON value is not a list")
    symbols = list(dict.fromkeys(
        str(symbol).strip().upper() for symbol in raw_symbols if str(symbol).strip()
    ))
    if len(symbols) < TRADIER_MASTER_MIN_SYMBOLS:
        raise ValueError(
            f"only {len(symbols)} symbols; expected at least {TRADIER_MASTER_MIN_SYMBOLS}"
        )
    return symbols


def _atomic_write_symbol_list(path: Path, symbols: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp_path, "w") as f:
            json.dump(symbols, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def load_symbols() -> List[str]:
    """Load the authoritative Tradier allowlist, restoring it if it vanished."""
    master_path = Path(config.SYMBOLS_FILE)
    try:
        symbols = _validated_symbol_list(master_path)
        # Preserve the largest valid list seen. A truncated master must never
        # overwrite the recovery seed.
        try:
            backup_symbols = _validated_symbol_list(TRADIER_MASTER_BACKUP)
        except Exception:
            backup_symbols = []
        if len(symbols) >= len(backup_symbols):
            _atomic_write_symbol_list(TRADIER_MASTER_BACKUP, symbols)
        return symbols
    except Exception as master_error:
        logger.critical(
            f"[MASTER_SYMBOLS] {master_path} is missing/invalid ({master_error}); "
            f"restoring from {TRADIER_MASTER_BACKUP}"
        )
    try:
        symbols = _validated_symbol_list(TRADIER_MASTER_BACKUP)
        _atomic_write_symbol_list(master_path, symbols)
        logger.critical(
            f"[MASTER_SYMBOLS] Restored {len(symbols)} symbols to {master_path}"
        )
        return symbols
    except Exception as backup_error:
        logger.critical(
            f"[MASTER_SYMBOLS] Recovery failed; backup is missing/invalid: {backup_error}"
        )
        return []


def _acquire_instance_lock() -> bool:
    """Prevent direct launches and multiple watchdogs from ranking concurrently."""
    global _INSTANCE_LOCK_HANDLE
    lock_path = Path(config.BASE_PATH) / "data" / ".tradier_rankings.instance.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        logger.critical(
            "[SINGLETON] Another tradier_rankings.py process already holds the instance lock; exiting"
        )
        return False
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    _INSTANCE_LOCK_HANDLE = handle
    return True


@contextmanager
def _tradier_derived_files_lock():
    """Serialize ranking publication with news-scanner TRB mutations."""
    with open(TRADIER_DERIVED_FILES_LOCK, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
# ===== OVERRIDE initial_fetch_and_ranking for Tradier =====
async def initial_fetch_and_ranking(symbols, timeframes=None):
    """Tradier-specific ranking - uses Tradier bars instead of Binance klines"""
    if timeframes is None:
        timeframes = TRADIER_TIMEFRAMES
    global indicators_data, mark_price_cache
    await load_indicators_data()
    logger.info(f"🔍 Processing {len(symbols)} symbols for Tradier ranking...")
    intermediate_symbol_data = []
    processed_symbols = []
    start_time = datetime.now(timezone.utc)
    async def process_single_symbol(sym):
        """Process a single symbol and return its data"""
        try:
            symbol_start = datetime.now(timezone.utc)
            dfs_current_sym = {}
            for tf in timeframes:
                # Use Tradier bar converter instead of Binance klines converter
                dftf = await convert_tradier_bars_to_analysis_df(sym, tf)               
                if tf == "D":
                    if dftf.empty:
                        # Missing daily data is common with API errors
                        continue
                    if len(dftf) < 20:
                        # Not enough data for reliable regression/ranking
                        continue
                if dftf.empty:
                    continue
                dftf = detect_tops_bottoms(dftf, distance=5, prominence=1e-4)
                dftf = add_stochrsi_zones(dftf)  # Add stochastic RSI zones
                dfs_current_sym[tf] = dftf
            if not dfs_current_sym:
                return None
            slopes_raw = {}
            r_values_raw = {}
            for tf, dfv in dfs_current_sym.items():
                # Use the same all-close regression that plot_dfs_subplots draws.
                # The previous tops/bottoms-only regression could flip the sign
                # of a timeframe (notably MNTS 1h) and made the plotted slopes
                # disagree with the ranking slopes.
                slp, rvv, _ = calculate_regression_slope_line(dfv[["close"]].copy())
                slopes_raw[tf] = slp
                r_values_raw[tf] = rvv
            # Keep the diagnostic trend normalization on the same weighting
            # scheme used by the final score below, including 4h when daily
            # data is present.  Previously these were two different LT trends.
            weights_lt = {"D": 200, "4h": 150, "1h": 200, "15m": 200, "5m": 300, "1m": 350}
            trend_val_raw_lt = sum(slopes_raw.get(tf, 0.0) * w for tf, w in weights_lt.items())
            trend_val_raw_st = sum(slopes_raw.get(tf, 0.0) * w for tf, w in {"D": 80, "4h": 120, "1h": 150, "15m": 300, "5m": 500, "1m": 700}.items())
            symbol_time = (datetime.now(timezone.utc) - symbol_start).total_seconds()
            if symbol_time > 1.0:
                logger.debug(f"⏱️ {sym} took {symbol_time:.2f}s")
            return {
                "symbol": sym,
                "dfs_for_calc": dfs_current_sym,
                "slopes_raw": slopes_raw,
                "r_values_raw": r_values_raw,
                "trend_val_raw_lt": trend_val_raw_lt,
                "trend_val_raw_st": trend_val_raw_st
            }
        except Exception as e:
            logger.debug(f"Error processing {sym}: {e}")
            return None
    batch_size = 31
    logger.info(f"🔄 Processing symbols in batches of {batch_size}...")
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        batch_start = datetime.now(timezone.utc)
        results = await asyncio.gather(*[process_single_symbol(sym) for sym in batch], return_exceptions=True)
        for result in results:
            if result is not None and not isinstance(result, Exception):
                intermediate_symbol_data.append(result)
                processed_symbols.append(result["symbol"])
        batch_time = (datetime.now(timezone.utc) - batch_start).total_seconds()
        batch_num = (i // batch_size) + 1
        total_batches = (len(symbols) + batch_size - 1) // batch_size
        logger.info(f"✅ Batch {batch_num}/{total_batches} completed in {batch_time:.2f}s ({len(batch)} symbols)")
    total_time = (datetime.now(timezone.utc) - start_time).total_seconds()
    logger.info(f"✅ Processed {len(processed_symbols)} symbols successfully in {total_time:.2f}s")
    if not intermediate_symbol_data:
        return [], {}
    logger.info(f"[ranking] Processing returns for {len(intermediate_symbol_data)} symbols...")
    all_dfs_for_returns_calc = {item["symbol"]: item["dfs_for_calc"] for item in intermediate_symbol_data}
    returns_15m = await calculate_15min_returns_for_symbols(all_dfs_for_returns_calc)
    returns_5m = await calculate_3min_returns_for_symbols(all_dfs_for_returns_calc)  # Reuse 3m function for 5m
    logger.info(f"[ranking] Returns calculated: 15m={len(returns_15m)}, 5m={len(returns_5m)}")
    # Pre-fetch prices
    symbols_needing_price = []
    for item in intermediate_symbol_data:
        sym = item["symbol"]
        if sym not in mark_price_cache:
            df_1m = item.get("dfs_for_calc", {}).get("1m")
            if df_1m is not None and not df_1m.empty and "close" in df_1m.columns:
                latest_close = df_1m["close"].iloc[-1]
                mark_price_cache[sym] = {"price": latest_close, "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}
            else:
                symbols_needing_price.append(sym)
    if symbols_needing_price:
        logger.debug(f"[ranking] Pre-fetching {len(symbols_needing_price)} missing prices...")
        results = await asyncio.gather(*[get_current_price(sym) for sym in symbols_needing_price], return_exceptions=True)
        for sym, result in zip(symbols_needing_price, results):
            if isinstance(result, Exception) or result[0] is None:
                continue
            mark_price_cache[sym] = {"price": result[0], "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}
    # Normalize trend values
    all_trend_raw_lt_vals = [item["trend_val_raw_lt"] for item in intermediate_symbol_data if pd.notna(item["trend_val_raw_lt"])]
    all_trend_raw_st_vals = [item["trend_val_raw_st"] for item in intermediate_symbol_data if pd.notna(item["trend_val_raw_st"])]
    global_min_trend_lt, global_max_trend_lt = calculate_min_max(all_trend_raw_lt_vals)
    global_min_trend_st, global_max_trend_st = calculate_min_max(all_trend_raw_st_vals)
    for item in intermediate_symbol_data:
        item["trend_val_norm_lt"] = normalize_log_signed(item["trend_val_raw_lt"], global_min_trend_lt, global_max_trend_lt)
        item["trend_val_norm_st"] = normalize_log_signed(item["trend_val_raw_st"], global_min_trend_st, global_max_trend_st)
    # Calculate final scores (use same logic as ez_rankings but with Tradier timeframes)
    final_ranking_data_scalars = []
    all_raw_proximity_scores_1m_for_norm = []
    _score_loop_start = time.time()
    for _score_idx, item in enumerate(intermediate_symbol_data):
        sym = item["symbol"]
        if _score_idx % 10 == 0:
            logger.info(f"[ranking] Scoring {_score_idx}/{len(intermediate_symbol_data)} (next={sym}) elapsed={time.time() - _score_loop_start:.1f}s")
        _sym_score_start = time.time()
        dfs_calc = item["dfs_for_calc"]
        df_1m = dfs_calc.get("1m", pd.DataFrame())
        df_5m = dfs_calc.get("5m", pd.DataFrame())
        df_15m = dfs_calc.get("15m", pd.DataFrame())
        df_1h = dfs_calc.get("1h", pd.DataFrame())
        df_D = dfs_calc.get("D", pd.DataFrame())
        lin_val_raw = sum(r for r in item["r_values_raw"].values() if pd.notna(r)) / len([r for r in item["r_values_raw"].values() if pd.notna(r)]) if item["r_values_raw"] and any(pd.notna(r) for r in item["r_values_raw"].values()) else 0.0
        abs_lin_val_raw = sum(abs(r) for r in item["r_values_raw"].values() if pd.notna(r)) / len([r for r in item["r_values_raw"].values() if pd.notna(r)]) if item["r_values_raw"] and any(pd.notna(r) for r in item["r_values_raw"].values()) else 0.0
        # HTF R (D+1h) = trend quality gate; LTF R (15m+5m+1m) = momentum quality
        _htf_tfs = [tf for tf in ["D", "1h"] if tf in item["r_values_raw"] and pd.notna(item["r_values_raw"][tf])]
        _ltf_tfs = [tf for tf in ["15m", "5m", "1m"] if tf in item["r_values_raw"] and pd.notna(item["r_values_raw"][tf])]
        htf_r = sum(abs(item["r_values_raw"][tf]) for tf in _htf_tfs) / len(_htf_tfs) if _htf_tfs else abs_lin_val_raw
        ltf_r = sum(abs(item["r_values_raw"][tf]) for tf in _ltf_tfs) / len(_ltf_tfs) if _ltf_tfs else abs_lin_val_raw
        rel_vol_h1 = calculate_relative_volume(df_1h, 50)
        rel_vol_m15 = calculate_relative_volume(df_15m, 50)
        rel_vol_m5 = calculate_relative_volume(df_5m, 50)
        rel_vol_m1 = calculate_relative_volume(df_1m, 50)
        rel_vol_tot_raw = (rel_vol_h1 * 2 + rel_vol_m15 * 8 + rel_vol_m5 * 14 + rel_vol_m1 * 14) / 38.0
        rel_vol_tot_raw_r = (rel_vol_h1 * 2 + rel_vol_m15 * 12 + rel_vol_m5 * 24 + rel_vol_m1 * 24) / 62.0
        rel_vol_tot_norm_factor = 1.4 if rel_vol_tot_raw > 1.5 else (1.1 if rel_vol_tot_raw > 1.1 else (0.8 if rel_vol_tot_raw < 0.85 else 1.0))
        rel_vol_tot_norm_factor_r = 1.4 if rel_vol_tot_raw_r > 1.5 else (1.1 if rel_vol_tot_raw_r > 1.1 else (0.8 if rel_vol_tot_raw_r < 0.85 else 1.0))
        current_price = mark_price_cache.get(sym, {}).get("price") if sym in mark_price_cache else None
        price_vs_sma_score = 0.0
        if current_price and df_1h is not None and not df_1h.empty and len(df_1h) >= 200:
            sma_1h = df_1h['close'].rolling(200).mean().iloc[-1] if 'close' in df_1h.columns else None
            if sma_1h and sma_1h > 0:
                price_vs_sma_score = ((current_price - sma_1h) / sma_1h) * 100 * 15
        band_data = {}
        band_score = 0.0
        # Use same band calculation approach as ez_rankings (4h equivalent = 1h for stocks, daily = D)
        if current_price and df_1h is not None and df_D is not None:
            band_data = calculate_multi_timeframe_band_score(df_1h, df_1h, df_D, current_price)  # 1h as mid-term, D as daily
            band_score = band_data.get("score", 0.0)
        # Calculate proximity score (use 5m instead of 1m for less noise, same as ez_rankings uses 3m)
        mean_prox_score_raw_1m = 0.0
        if df_5m is not None and not df_5m.empty:
            df_5m_with_prox = assign_points_proximity_3m(df_5m.copy(), df_15m, df_1h, df_D)  # Reuse 3m function for 5m
            if "proximity_score" in df_5m_with_prox.columns:
                valid_prox_scores = df_5m_with_prox["proximity_score"].dropna()
                if not valid_prox_scores.empty:
                    mean_prox_score_raw_1m = valid_prox_scores.mean()
                    all_raw_proximity_scores_1m_for_norm.extend(valid_prox_scores.tolist())
        breakthrough_bonus = 0.0
        if current_price and df_1h is not None and not df_1h.empty:
            atr_1h = calculate_atr(df_1h, period=14)
            upper_band = band_data.get("upper_band")
            lower_band = band_data.get("lower_band")
            prox_range_df = pd.concat([df_1h, df_D]) if df_D is not None else df_1h
            prox_range = get_proximity_range(prox_range_df)
            if upper_band and prox_range.get("top") and atr_1h > 0 and current_price > upper_band and current_price > prox_range.get("top"):
                distance_above = current_price - max(upper_band, prox_range.get("top"))
                if distance_above > (atr_1h * 0.75):
                    breakthrough_bonus = (distance_above / atr_1h) * 75
            elif lower_band and prox_range.get("bottom") and atr_1h > 0 and current_price < lower_band and current_price < prox_range.get("bottom"):
                distance_below = min(lower_band, prox_range.get("bottom")) - current_price
                if distance_below > (atr_1h * 0.75):
                    breakthrough_bonus = -((distance_below / atr_1h) * 75)
        # Long-term return: use cumulative return plus an explicit full-history
        # drawdown metric.  A weighted average of daily returns is not a
        # meaningful drawdown-aware LT signal and can be dominated by one gap.
        lt_return_pct, lt_peak_drawdown_pct = calculate_lt_return_metrics(df_D, 45)
        lt_return_score = np.clip(lt_return_pct, -LT_RETURN_CLIP_PCT, LT_RETURN_CLIP_PCT) * LT_RETURN_SCORE_WEIGHT
        daily_slope = item["slopes_raw"].get("D")
        long_veto_reasons = []
        if "D" not in dfs_calc:
            long_veto_reasons.append("missing_daily")
        elif daily_slope is None or daily_slope <= 0:
            long_veto_reasons.append("daily_slope_not_positive")
        if lt_peak_drawdown_pct <= LONG_MAX_PEAK_DRAWDOWN_PCT:
            long_veto_reasons.append("severe_peak_drawdown")
        long_eligible = not long_veto_reasons

        # Short-term return remains a fast bar-level signal.
        # Short-term: 1 day of 5m data (288 bars), decay factor 0.98 for faster decay (use 5m instead of 1m for less noise)
        weighted_gains_st = calculate_weighted_gains(df_5m, 288, decay_factor=0.98) if df_5m is not None else 0.0
        # Final trend calculation - compensate for slope scale (shorter TF = smaller slopes = higher weights)
        # 4h slope included when available; weights rebalanced
        trend_val_lt = sum(item["slopes_raw"].get(tf, 0.0) * w for tf, w in {"D": 200, "4h": 150, "1h": 200, "15m": 200, "5m": 300, "1m": 350}.items())
        trend_val_st = sum(item["slopes_raw"].get(tf, 0.0) * w for tf, w in {"D": 80, "4h": 120, "1h": 150, "15m": 300, "5m": 500, "1m": 700}.items())
        # HTF R is only a modest quality adjustment.  Slope remains the
        # dominant directional signal; |R| must not turn bearish HTF structure
        # into long-side confirmation.
        linearity_multiplier_lt = min(1.0 + (LT_LINEARITY_WEIGHT * htf_r), LT_MAX_LINEARITY_MULTIPLIER)
        linearity_multiplier_st = 1.0 + (2.0 * htf_r) + (0.5 * ltf_r)
        long_term_score_raw = (trend_val_lt * linearity_multiplier_lt) + price_vs_sma_score + band_score * 0.5 + breakthrough_bonus + lt_return_score
        # ST: band 0.7→0.9, proximity 0.5→0.8
        short_term_score_raw = (trend_val_st * linearity_multiplier_st) + price_vs_sma_score * 1.5 + band_score * 0.9 + breakthrough_bonus * 1.2 + mean_prox_score_raw_1m * 0.8 + (weighted_gains_st * 80)
        final_score_raw_lt = long_term_score_raw * rel_vol_tot_norm_factor
        final_score_raw_st = short_term_score_raw * rel_vol_tot_norm_factor_r
        # BACKTEST_CHANGE_T39: 0xxx sweep showed sentiment useless (3/200 positive Sharpe)
        # if config.NEWS_SENTIMENT_ENABLED and _news_sentiment_cache_tradier:
        #     _ns = _news_sentiment_cache_tradier.get(sym, _news_sentiment_cache_tradier.get(sym.replace('USDT', '').replace('USDC', ''), 0.0))
        #     if _ns != 0.0:
        #         _ns_mult = 1.0 + (_ns * config.NEWS_SENTIMENT_WEIGHT)
        #         final_score_raw_lt *= _ns_mult
        #         final_score_raw_st *= _ns_mult
        final_ranking_data_scalars.append({
            "symbol": sym, "slopes_raw": item['slopes_raw'], "r_values_raw": item['r_values_raw'],
            "trend_val_norm_lt": item["trend_val_norm_lt"], "trend_val_norm_st": item["trend_val_norm_st"],
            "linearity_raw": lin_val_raw, "abs_linearity_raw": abs_lin_val_raw, "rel_vol_raw": rel_vol_tot_raw, "rel_vol_norm_factor": rel_vol_tot_norm_factor,
            "final_score_raw_lt": final_score_raw_lt, "final_score_raw_st": final_score_raw_st,
            "mean_proximity_score_raw_5m": mean_prox_score_raw_1m, "band_score": band_score,
            "weighted_gains_lt": lt_return_pct, "lt_return_pct": lt_return_pct,
            "lt_return_score": lt_return_score, "lt_peak_drawdown_pct": lt_peak_drawdown_pct,
            "long_eligible": long_eligible,
            "long_veto_reason": ",".join(long_veto_reasons),
            "linearity_multiplier_lt": linearity_multiplier_lt,
            "trend_val_lt_raw": trend_val_lt,
            "weighted_gains_st": weighted_gains_st,
            "dfs_for_calc": dfs_calc })
        _sym_score_elapsed = time.time() - _sym_score_start
        if _sym_score_elapsed > 5.0:
            logger.warning(f"[ranking] SLOW scoring {sym} took {_sym_score_elapsed:.1f}s")
    logger.info(f"[ranking] Scoring loop done: {len(final_ranking_data_scalars)} symbols in {time.time() - _score_loop_start:.1f}s")
    if not final_ranking_data_scalars:
        return [], {}
    # Normalize final scores (same approach as ez_rankings)
    all_scores_combined = [entry["final_score_raw_lt"] for entry in final_ranking_data_scalars] + [entry["final_score_raw_st"] for entry in final_ranking_data_scalars]
    global_min_score, global_max_score = calculate_min_max(all_scores_combined)
    for entry in final_ranking_data_scalars:
        sym = entry["symbol"]
        # Normalize final scores (same as ez_rankings)
        entry["final_score_norm"] = normalize_log_signed(entry["final_score_raw_lt"], global_min_score, global_max_score)
        entry["final_score_recent_norm"] = normalize_log_signed(entry["final_score_raw_st"], global_min_score, global_max_score)
        # Normalize proximity score (same as ez_rankings - use fixed range -100 to 100)
        entry["proximity_score_norm_5m"] = normalize_log_signed(entry["mean_proximity_score_raw_5m"], -100, 100) if entry["mean_proximity_score_raw_5m"] else 0.0
        # Additional fields for compatibility
        entry["final_score_norm_lt"] = entry["final_score_norm"]
        entry["final_score_norm_st"] = entry["final_score_recent_norm"]
        entry["trend_val_norm"] = entry["trend_val_norm_lt"]
        entry["trend_val_recent_norm"] = entry["trend_val_norm_st"]
        entry["linearity_norm"] = normalize_log_signed(entry["abs_linearity_raw"], 0.0, 1.0) * 100.0
    # Calculate returns (adapted for Tradier timeframes)
    returns_data = {}
    for entry in final_ranking_data_scalars:
        sym = entry["symbol"]
        df_1m = entry.get("dfs_for_calc", {}).get("1m")
        df_15m = entry.get("dfs_for_calc", {}).get("15m")
        if df_15m is not None and not df_15m.empty and "close" in df_15m.columns:
            if len(df_15m) >= 2:
                p15 = df_15m["close"].iloc[-2]; entry["return_15m"] = float((df_15m["close"].iloc[-1] - p15) / p15 * 100.0) if p15 else 0.0
        if df_1m is not None and not df_1m.empty and "close" in df_1m.columns:
            if len(df_1m) >= 2:
                p1 = df_1m["close"].iloc[-2]; entry["return_1m"] = float((df_1m["close"].iloc[-1] - p1) / p1 * 100.0) if p1 else 0.0
    # Sort/dedup by raw score before applying the long-side eligibility guard.
    # Dedup: if symbol appears twice keep highest-scoring entry
    _seen_syms = {}
    for _entry in final_ranking_data_scalars:
        _s = _entry["symbol"]
        if _s not in _seen_syms or _entry.get("final_score_norm", 0.0) > _seen_syms[_s].get("final_score_norm", 0.0):
            _seen_syms[_s] = _entry
    final_ranking_data_scalars = list(_seen_syms.values())
    final_ranking_data_scalars.sort(
        key=lambda x: (bool(x.get("long_eligible", False)), x.get("final_score_norm", 0.0)),
        reverse=True,
    )
    minimum_safe_coverage = max(50, int(len(symbols) * 0.50))
    if len(final_ranking_data_scalars) < minimum_safe_coverage:
        logger.critical(
            f"[rankings] REFUSING leaderboard overwrite: only "
            f"{len(final_ranking_data_scalars)}/{len(symbols)} master symbols ranked; "
            f"minimum safe coverage is {minimum_safe_coverage}. Existing winners/losers "
            "and symbols_trb_* files are preserved."
        )
        return final_ranking_data_scalars, {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "symbol_count": len(final_ranking_data_scalars),
            "timeframes": timeframes,
            "source": "tradier_rankings_partial_preserved",
        }
    # Create leaderboard lists (winners/losers) like ez_rankings
    long_eligible_entries = [e for e in final_ranking_data_scalars if e.get("long_eligible", False)]
    top_winners_lt = sorted(long_eligible_entries, key=lambda x: x.get("final_score_norm", 0.0), reverse=True)
    top_losers_lt = sorted(final_ranking_data_scalars, key=lambda x: x.get("final_score_norm", 0.0))
    top_winners_st = sorted(long_eligible_entries, key=lambda x: x.get("final_score_recent_norm", 0.0), reverse=True)
    top_losers_st = sorted(final_ranking_data_scalars, key=lambda x: x.get("final_score_recent_norm", 0.0))
    to_save_top20 = [{"symbol": e["symbol"], "score": e["final_score_norm"]} for e in top_winners_lt[:20]]
    to_save_bottom20 = [{"symbol": e["symbol"], "score": e["final_score_norm"]} for e in top_losers_lt[:20]]
    to_save_top30 = [{"symbol": e["symbol"], "score": e["final_score_norm"]} for e in top_winners_lt[:50]]
    to_save_bottom30 = [{"symbol": e["symbol"], "score": e["final_score_norm"]} for e in top_losers_lt[:50]]
    to_save_top30_r = [{"symbol": e["symbol"], "score": e["final_score_recent_norm"]} for e in top_winners_st[:30]]
    to_save_bottom30_r = [{"symbol": e["symbol"], "score": e["final_score_recent_norm"]} for e in top_losers_st[:30]]
    to_save_top15_r = [{"symbol": e["symbol"], "score": e["final_score_recent_norm"]} for e in top_winners_st[:5]]
    to_save_bottom15_r = [{"symbol": e["symbol"], "score": e["final_score_recent_norm"]} for e in top_losers_st[:5]]
    # Calculate returns for 15m filtering (similar to ez_rankings)
    all_dfs_for_returns = {item["symbol"]: item.get("dfs_for_calc", {}) for item in final_ranking_data_scalars}
    returns_15m = await calculate_15min_returns_for_symbols(all_dfs_for_returns)
    returns_5m = await calculate_3min_returns_for_symbols(all_dfs_for_returns)
    # Filter top 100 by returns (like ez_rankings does)
    # winners_15m: from top 100 LT winners; losers_15m: from top 100 LT LOSERS (fix: was using winners)
    top_100_winners_set = set([item["symbol"] for item in top_winners_lt[:100]])
    top_100_losers_set  = set([item["symbol"] for item in top_losers_lt[:100]])
    to_save_top15_15m = []
    to_save_bottom15_15m = []
    for sym in top_100_winners_set:
        ret_15 = returns_15m.get(sym, 0)
        ret_5 = returns_5m.get(sym, 0)
        if (pd.notna(ret_15) and ret_15 > 1.0) or (pd.notna(ret_5) and ret_5 > 0.9):
            to_save_top15_15m.append({"symbol": sym, "returns_15m": float(ret_15) if pd.notna(ret_15) else None, "returns_5m": float(ret_5) if pd.notna(ret_5) else None})
    for sym in top_100_losers_set:
        ret_15 = returns_15m.get(sym, 0)
        ret_5 = returns_5m.get(sym, 0)
        if (pd.notna(ret_15) and ret_15 < -0.9) or (pd.notna(ret_5) and ret_5 < -0.75):
            to_save_bottom15_15m.append({"symbol": sym, "returns_15m": float(ret_15) if pd.notna(ret_15) else None, "returns_5m": float(ret_5) if pd.notna(ret_5) else None})
    # Create symbol lists — enforce parity, strip non-shortable + options from shorts
    import re as _re
    _options_re = _re.compile(r'\d{6}[CP]\d+')
    _non_shortable = set(s.upper() for s in getattr(config, 'NON_SHORTABLE', set()))
    _blacklist = set(s.upper() for s in getattr(config, 'BLACKLIST', []))
    _raw_longs  = list(dict.fromkeys([item["symbol"] for item in to_save_top30]    + [item["symbol"] for item in to_save_top15_r]))
    _raw_shorts = list(dict.fromkeys([item["symbol"] for item in to_save_bottom30] + [item["symbol"] for item in to_save_bottom15_r]))
    _raw_longs  = [s for s in _raw_longs  if not _options_re.search(s) and s not in _blacklist]
    _raw_shorts = [s for s in _raw_shorts if not _options_re.search(s) and s not in _non_shortable and s not in _blacklist]
    # Pad shorts from full losers pool to match longs count
    if len(_raw_shorts) < len(_raw_longs):
        _extra_pool = [item["symbol"] for item in top_losers_lt if item["symbol"] not in _non_shortable and item["symbol"] not in _raw_shorts and not _options_re.search(item["symbol"])]
        _raw_shorts += _extra_pool[:len(_raw_longs) - len(_raw_shorts)]
    if len(_raw_longs) < len(_raw_shorts):
        _extra_longs = [item["symbol"] for item in top_winners_lt if item["symbol"] not in _raw_longs and not _options_re.search(item["symbol"])]
        _raw_longs += _extra_longs[:len(_raw_shorts) - len(_raw_longs)]
    logger.info(f"[rankings] Symbol parity: {len(_raw_longs)}L / {len(_raw_shorts)}S")
    # Use independent lists: optional injections into trb/trc must not mutate
    # tra through shared list aliases.
    symbols_trc_long  = list(_raw_longs)
    symbols_trc_short = list(_raw_shorts)
    symbols_tra_long  = list(_raw_longs)
    symbols_tra_short = list(_raw_shorts)
    symbols_trb_long  = list(_raw_longs)
    symbols_trb_short = list(_raw_shorts)
    # === 2026-04-27 STOCKS OPTIONS-OI INJECTION (mirror crypto FUNDING_OI_INJECT) ===
    # Read data/stocks_oi_cache/{sym}.json populated by tradier_options_oi_fetcher.py (READ-ONLY).
    # Inject extreme-P/C symbols (call-dominant → LONG, put-dominant → SHORT) into the trb/trc lists
    # so options-market consensus directional bias surfaces in entry candidates. Cap per side, dedup.
    try:
        if bool(getattr(config, 'TRADIER_OI_INJECT_ENABLED', False)):
            _pc_bull = float(getattr(config, 'TRADIER_OI_INJECT_PC_BULLISH', 0.6))
            _pc_bear = float(getattr(config, 'TRADIER_OI_INJECT_PC_BEARISH', 1.4))
            _prefer_near = bool(getattr(config, 'TRADIER_OI_INJECT_NEAR_MONEY_PREFER', True))
            _min_total_oi = int(getattr(config, 'TRADIER_OI_INJECT_MIN_TOTAL_OI', 1000))
            _max_each = int(getattr(config, 'TRADIER_OI_INJECT_MAX_EACH', 10))
            _stale_h = float(getattr(config, 'TRADIER_OI_INJECT_STALE_MAX_HOURS', 4.0))
            _now = time.time()
            _cache_dir = Path(config.BASE_PATH) / "data" / "stocks_oi_cache"
            _toi_long = []   # [(sym, reason, score)] — sort by P/C deviation from neutral
            _toi_short = []
            if _cache_dir.is_dir():
                for _p in _cache_dir.glob("*.json"):
                    try:
                        with open(_p) as _fh: _doi = json.load(_fh)
                        _sym = _doi.get("sym")
                        if not _sym or _sym in _blacklist:
                            continue
                        _ts = _doi.get("ts") or 0
                        if _now - _ts > _stale_h * 3600:
                            continue
                        _total_oi = (_doi.get("total_call_oi") or 0) + (_doi.get("total_put_oi") or 0)
                        if _total_oi < _min_total_oi:
                            continue
                        _pc = _doi.get("near_money_pc_ratio") if (_prefer_near and _doi.get("near_money_pc_ratio") is not None) else _doi.get("pc_ratio")
                        if _pc is None:
                            continue
                        _pc = float(_pc)
                        if _pc <= _pc_bull:
                            # call-dominant → LONG bias. Score = how extreme below threshold.
                            _toi_long.append((_sym, f"pc={_pc:.2f}_call_dominant_oi={_total_oi}", _pc_bull - _pc))
                        elif _pc >= _pc_bear:
                            if _sym in _non_shortable:
                                continue
                            _toi_short.append((_sym, f"pc={_pc:.2f}_put_dominant_oi={_total_oi}", _pc - _pc_bear))
                    except Exception:
                        continue
            # Sort by extremeness (most extreme first), dedup, cap
            _toi_long.sort(key=lambda x: -x[2])
            _toi_short.sort(key=lambda x: -x[2])
            _seen_l = set(); _seen_s = set()
            _added_l = 0; _added_s = 0
            _injected_l_log = []; _injected_s_log = []
            for _s, _r, _ in _toi_long:
                if _s in _seen_l: continue
                _seen_l.add(_s)
                if _added_l >= _max_each: break
                if _s not in symbols_trb_long: symbols_trb_long.append(_s)
                if _s not in symbols_trc_long: symbols_trc_long.append(_s)
                _added_l += 1
                if len(_injected_l_log) < 5: _injected_l_log.append((_s, _r))
            for _s, _r, _ in _toi_short:
                if _s in _seen_s: continue
                _seen_s.add(_s)
                if _added_s >= _max_each: break
                if _s not in symbols_trb_short: symbols_trb_short.append(_s)
                if _s not in symbols_trc_short: symbols_trc_short.append(_s)
                _added_s += 1
                if len(_injected_s_log) < 5: _injected_s_log.append((_s, _r))
            if _added_l or _added_s:
                logger.info(f"💰 [TRADIER_OI_INJECT] +LONG {_added_l}: {_injected_l_log} | +SHORT {_added_s}: {_injected_s_log}")
            else:
                logger.debug(f"[TRADIER_OI_INJECT] no extreme-P/C candidates (cache={len(list(_cache_dir.glob('*.json'))) if _cache_dir.is_dir() else 0} files)")
    except Exception as _toi_err:
        logger.warning(f"[TRADIER_OI_INJECT] error: {_toi_err}")
    # === END STOCKS OPTIONS-OI INJECTION ====================================================
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time())
        async def _save_json_async(file_p: Path, data_to_save: Any):
            await atomic_write_json(file_p, data_to_save)
        await _save_json_async(config.DATA_DIR / f"winners_20_{timestamp}.json", to_save_top20)
        await _save_json_async(config.DATA_DIR / f"losers_20_{timestamp}.json", to_save_bottom20)
        await _save_json_async(config.DATA_DIR / f"winners_30_{timestamp}.json", to_save_top30)
        await _save_json_async(config.DATA_DIR / f"losers_30_{timestamp}.json", to_save_bottom30)
        await _save_json_async(config.DATA_DIR / f"winners_30r_{timestamp}.json", to_save_top30_r)
        await _save_json_async(config.DATA_DIR / f"losers_30r_{timestamp}.json", to_save_bottom30_r)
        await _save_json_async(config.DATA_DIR / f"winners_15m_{timestamp}.json", to_save_top15_15m)
        await _save_json_async(config.DATA_DIR / f"losers_15m_{timestamp}.json", to_save_bottom15_15m)
        shutil.copy2(config.DATA_DIR / f"winners_20_{timestamp}.json", config.DATA_DIR / "winners_20")
        shutil.copy2(config.DATA_DIR / f"losers_20_{timestamp}.json", config.DATA_DIR / "losers_20")
        shutil.copy2(config.DATA_DIR / f"winners_30_{timestamp}.json", config.DATA_DIR / "winners_30")
        shutil.copy2(config.DATA_DIR / f"losers_30_{timestamp}.json", config.DATA_DIR / "losers_30")
        shutil.copy2(config.DATA_DIR / f"winners_30r_{timestamp}.json", config.DATA_DIR / "winners_30r")
        shutil.copy2(config.DATA_DIR / f"losers_30r_{timestamp}.json", config.DATA_DIR / "losers_30r")
        shutil.copy2(config.DATA_DIR / f"winners_15m_{timestamp}.json", config.DATA_DIR / "winners_15m")
        shutil.copy2(config.DATA_DIR / f"losers_15m_{timestamp}.json", config.DATA_DIR / "losers_15m")
        for _pfx in ["winners_20_", "losers_20_", "winners_30_", "losers_30_", "winners_30r_", "losers_30r_", "winners_15m_", "losers_15m_"]:
            _prune_tiered(config.DATA_DIR, _pfx)
        for _ms in getattr(config, 'TRADIER_MANDATORY_LONG_TRB', []):
            if _ms not in symbols_trb_long: symbols_trb_long.append(_ms)
        for _ms in getattr(config, 'TRADIER_MANDATORY_SHORT_TRB', []):
            if _ms not in symbols_trb_short: symbols_trb_short.append(_ms)
        _merge_news_injections(symbols_trb_long, 'trb', 'LONG')
        _merge_news_injections(symbols_trb_short, 'trb', 'SHORT')
        _merge_ai_injections(symbols_trb_long, 'trb', 'LONG')
        _merge_ai_injections(symbols_trb_short, 'trb', 'SHORT')
        _long_eligibility_by_symbol = {
            str(item.get("symbol", "")).upper(): bool(item.get("long_eligible", False))
            for item in final_ranking_data_scalars
        }
        _blocked_long_candidates = [
            s for s in symbols_trb_long
            if s in _long_eligibility_by_symbol and not _long_eligibility_by_symbol[s]
        ]
        symbols_trb_long = [
            s for s in symbols_trb_long
            if _long_eligibility_by_symbol.get(s, True)
        ]
        if _blocked_long_candidates:
            logger.warning(
                f"[LONG_ELIGIBILITY] Removed vetoed long candidates: {_blocked_long_candidates}"
            )
        # symbols_tradier.json is the final trading allowlist. Optional OI,
        # mandatory, and news injections may prioritize a symbol, but may not
        # add an unapproved symbol to an account's derived trading list.
        _allowed_symbols = set(symbols)
        _dropped_long = [s for s in symbols_trb_long if s not in _allowed_symbols]
        _dropped_short = [s for s in symbols_trb_short if s not in _allowed_symbols]
        symbols_trb_long = [s for s in symbols_trb_long if s in _allowed_symbols]
        symbols_trb_short = [s for s in symbols_trb_short if s in _allowed_symbols]
        if _dropped_long or _dropped_short:
            logger.warning(
                f"[MASTER_SYMBOLS] Blocked non-allowlisted injections: "
                f"long={_dropped_long}, short={_dropped_short}"
            )
        if len(symbols_trb_long) < 20 or len(symbols_trb_short) < 20:
            raise RuntimeError(
                f"refusing undersized symbols_trb overwrite: "
                f"{len(symbols_trb_long)} long / {len(symbols_trb_short)} short"
            )
        # 2026-07-19 USER: trc now mirrors trb's fully-processed symbol universe (same
        # per_sym baseline + daily 7D reconfig methodology, run as its own independent
        # instance) so the two accounts are a live apples-to-apples comparison instead
        # of trading disjoint symbol sets. See BACKTEST_BIBLE.md §trb/trc parity.
        await _save_json_async(config.BASE_PATH / "symbols_tra_long.json", symbols_tra_long)
        await _save_json_async(config.BASE_PATH / "symbols_tra_short.json", symbols_tra_short)
        with _tradier_derived_files_lock():
            # Re-read active news injections while holding the same lock used
            # by ez_news_scanner. This closes the last-update-wins race: an
            # injection either lands before this publication and is merged
            # here, or lands atomically after publication.
            _merge_news_injections(symbols_trb_long, 'trb', 'LONG')
            _merge_news_injections(symbols_trb_short, 'trb', 'SHORT')
            _merge_ai_injections(symbols_trb_long, 'trb', 'LONG')
            _merge_ai_injections(symbols_trb_short, 'trb', 'SHORT')
            symbols_trb_long = list(dict.fromkeys(
                s for s in symbols_trb_long
                if s in _allowed_symbols and _long_eligibility_by_symbol.get(s, True)
            ))
            symbols_trb_short = list(dict.fromkeys(
                s for s in symbols_trb_short if s in _allowed_symbols
            ))
            if len(symbols_trb_long) < 20 or len(symbols_trb_short) < 20:
                raise RuntimeError(
                    f"refusing undersized locked symbols_trb overwrite: "
                    f"{len(symbols_trb_long)} long / {len(symbols_trb_short)} short"
                )
            await _save_json_async(config.BASE_PATH / "symbols_trb_long.json", symbols_trb_long)
            await _save_json_async(config.BASE_PATH / "symbols_trb_short.json", symbols_trb_short)
            # 2026-07-20 USER: trc base = trb, then AI diverges for paper A/B.
            # TRB stays control (no AI). TRC = TRB + AI picks (when enabled) so trc
            # can be compared to trb to measure AI lift. Mirrors 2026-07-19 trc==trb
            # parity but deliberately diverges via _merge_ai_injections.
            with open(config.BASE_PATH / "symbols_trb_long.json") as _trb_l_fh:
                symbols_trc_long = json.load(_trb_l_fh)
            with open(config.BASE_PATH / "symbols_trb_short.json") as _trb_s_fh:
                symbols_trc_short = json.load(_trb_s_fh)
            # AI injection into TRC only (TRB stays pure control)
            _ai_before_l = len(symbols_trc_long)
            _ai_before_s = len(symbols_trc_short)
            _merge_ai_injections(symbols_trc_long, 'trc', 'LONG')
            _merge_ai_injections(symbols_trc_short, 'trc', 'SHORT')
            # Re-apply allowlist + dedupe after AI inject (AI symbols must be tradeable)
            symbols_trc_long = list(dict.fromkeys(s for s in symbols_trc_long if s in _allowed_symbols))
            symbols_trc_short = list(dict.fromkeys(s for s in symbols_trc_short if s in _allowed_symbols))
            if len(symbols_trc_long) != _ai_before_l or len(symbols_trc_short) != _ai_before_s:
                logger.info(f"[AI_RANKINGS] TRC diverged from TRB: trb {len(symbols_trb_long)}L/{len(symbols_trb_short)}S → trc {len(symbols_trc_long)}L/{len(symbols_trc_short)}S")
            await _save_json_async(config.BASE_PATH / "symbols_trc_long.json", symbols_trc_long)
            await _save_json_async(config.BASE_PATH / "symbols_trc_short.json", symbols_trc_short)
        logger.info(f"[rankings] Saved leaderboards: winners_20={len(to_save_top20)}, winners_30r={len(to_save_top30_r)}, symbols_trc_long={len(symbols_trc_long)}, symbols_trc_short={len(symbols_trc_short)}")
    except Exception as e:
        logger.error(f"[rankings] Error saving leaderboards: {e}", exc_info=True)
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol_count": len(final_ranking_data_scalars),
        "timeframes": timeframes,
        "source": "tradier_rankings"
    }
    return final_ranking_data_scalars, metadata

async def market_mode_poll_loop():
    """Poll data/market_mode.json every 30s to pick up EXTREME_MODE changes from news scanner or ez_rankings."""
    last_mode = Config._CURRENT_MARKET_MODE
    mode_file = Path(config.BASE_PATH) / "data" / "market_mode.json"
    while True:
        try:
            if mode_file.exists():
                async with aiofiles.open(mode_file, "r") as f:
                    data = json.loads(await f.read())
                mode = data.get("mode", "NORMAL_MODE")
                if mode in {"NORMAL_MODE", "EXTREME_MODE", "LIGHT_MODE"} and mode != last_mode:
                    Config.set_market_mode(mode)
                    news_reason = data.get("news_reason", "")
                    source = "news_scanner" if data.get("news_trigger") else "ez_rankings"
                    logger.warning(f"[MARKET_MODE] Mode changed: {last_mode} -> {mode} (source={source}) {news_reason}")
                    last_mode = mode
        except Exception as e:
            logger.debug(f"[MARKET_MODE] Poll error: {e}")
        await asyncio.sleep(30)

async def ranking_loop(symbols_list_arg: List[str]):
    """Tradier ranking loop - uses Tradier timeframes"""
    global RANKING_DATA, RANKING_INFO, LAST_RANKING_TIME, mark_price_cache
    while True:
        try:
            logger.info(f"[rankings] Starting ranking cycle for {len(symbols_list_arg)} symbols...")
            ranking_data_scalars_list_loop, metadata_current_run_loop = await initial_fetch_and_ranking(symbols_list_arg, TRADIER_TIMEFRAMES)
            # --- SUCCESS PATH ---
            if ranking_data_scalars_list_loop:
                RANKING_DATA = ranking_data_scalars_list_loop
                temp_ranking_info_loop = build_ranking_info(RANKING_DATA, TRADIER_TIMEFRAMES)
                metadata_loop = metadata_current_run_loop.copy() if metadata_current_run_loop else {}
                metadata_loop.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
                metadata_loop.setdefault("symbol_count", len(RANKING_DATA))
                metadata_loop.setdefault("source", "tradier_ranking_loop")
                temp_ranking_info_loop["metadata"] = metadata_loop
                RANKING_INFO = temp_ranking_info_loop
                LAST_RANKING_TIME = datetime.now(timezone.utc)
                try:
                    # config.DATA_DIR.mkdir(parents=True, exist_ok=True)
                    # rankings_file = config.DATA_DIR / "tradier_rankings.json"
                    # # FIX: Construct payload locally and use atomic_write_json
                    # # Do not rely on save_rankings_json from imported module
                    # final_payload = {
                    #     "rankings": RANKING_DATA,
                    #     "info": RANKING_INFO,
                    #     "metadata": metadata_loop
                    # }

                    serializable_rankings = []
                    for item in RANKING_DATA:
                        # Create a copy and remove the heavy DF object
                        clean_item = {k: v for k, v in item.items() if k != 'dfs_for_calc'}
                        serializable_rankings.append(clean_item)

                    await atomic_write_json(
                        config.DATA_DIR / "tradier_rankings.json", 
                        {"rankings": serializable_rankings, "info": RANKING_INFO}   )
                    await asyncio.sleep(config.RANKING_UPDATE_INTERVAL)
                except Exception as e:
                    logger.error(f"Ranking loop error: {e}", exc_info=True)
                    await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("[rankings] Ranking loop cancelled")
            break
        except Exception as e:
            logger.error(f"[rankings] Critical error in ranking_loop: {e}", exc_info=True)
            await asyncio.sleep(60)

                #     await atomic_write_json(rankings_file, final_payload)
                #     logger.info(f"[rankings] ✅ Ranking completed and saved: {len(RANKING_DATA)} symbols")
                # except Exception as e:
                #     logger.error(f"[rankings] ❌ Error saving rankings: {e}", exc_info=True)
            # --- FAILURE/EMPTY PATH ---
        #     else:
        #         logger.warning("[rankings] => No ranking data generated - check logs for errors")
        #         try:
        #             config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        #             rankings_file = config.DATA_DIR / "tradier_rankings.json"
        #             # FIX: Write empty dict atomically. 
        #             # Don't write stale RANKING_DATA here.
        #             await atomic_write_json(rankings_file, {})
        #             logger.info(f"[rankings] ⚠️ Wrote empty rankings file due to generation failure")
        #         except Exception as e:
        #             logger.error(f"[rankings] ❌ Error saving empty rankings: {e}", exc_info=True)
        #     await asyncio.sleep(config.RANKING_UPDATE_INTERVAL)
        # except asyncio.CancelledError:
        #     logger.info("[rankings] Ranking loop cancelled")
        #     break
        # except Exception as e_rank_loop_exc:
        #     logger.error(f"[rankings] Error in ranking_loop: {e_rank_loop_exc}", exc_info=True)
        #     await asyncio.sleep(60)


# async def ranking_loop(symbols_list_arg: List[str]):
#     """Tradier ranking loop - uses Tradier timeframes"""
#     global RANKING_DATA, RANKING_INFO, LAST_RANKING_TIME, mark_price_cache
#     while True:
#         try:
#             logger.info(f"[rankings] Starting ranking cycle for {len(symbols_list_arg)} symbols...")
#             ranking_data_scalars_list_loop, metadata_current_run_loop = await initial_fetch_and_ranking(symbols_list_arg, TRADIER_TIMEFRAMES)
#             if ranking_data_scalars_list_loop:
#                 RANKING_DATA = ranking_data_scalars_list_loop
#                 temp_ranking_info_loop = build_ranking_info(RANKING_DATA, TRADIER_TIMEFRAMES)
#                 metadata_loop = metadata_current_run_loop.copy() if metadata_current_run_loop else {}
#                 metadata_loop.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
#                 metadata_loop.setdefault("symbol_count", len(RANKING_DATA))
#                 metadata_loop.setdefault("source", "tradier_ranking_loop")
#                 temp_ranking_info_loop["metadata"] = metadata_loop
#                 RANKING_INFO = temp_ranking_info_loop
#                 LAST_RANKING_TIME = datetime.now(timezone.utc)
#                 try:
#                     config.DATA_DIR.mkdir(parents=True, exist_ok=True)
#                     await save_rankings_json(RANKING_DATA)
#                     logger.info(f"[rankings] ✅ Ranking completed: {len(RANKING_DATA)} symbols")
#                 except Exception as e:
#                     logger.error(f"[rankings] ❌ Error saving rankings: {e}", exc_info=True)
#             else:
#                 logger.warning("[rankings] => No ranking data generated - check logs for errors")
#                 try:
#                     config.DATA_DIR.mkdir(parents=True, exist_ok=True)
#                     rankings_file = config.DATA_DIR / "tradier_rankings.json"
#                     with open(rankings_file, 'w') as f:
#                         json.dump({}, f, indent=2)
#                     logger.info("[rankings] Created empty rankings file")
#                 except Exception as e:
#                     logger.error(f"[rankings] Error creating empty file: {e}")
#             await asyncio.sleep(config.RANKING_UPDATE_INTERVAL)
#         except asyncio.CancelledError:
#             logger.info("[rankings] Ranking loop cancelled")
#             break
#         except Exception as e_rank_loop_exc:
#             logger.error(f"[rankings] Error in ranking_loop: {e_rank_loop_exc}", exc_info=True)
#             await asyncio.sleep(60)
# ===== OVERRIDE plot_loop for Tradier (use Tradier timeframes and plots_tradier) =====
# async def plot_loop():
#     """Optimized: Only plots top 20 and bottom 20"""
#     while True:
#         try:
#             if not RANKING_DATA:
#                 await asyncio.sleep(30); continue
#             # 1. SELECT ONLY TARGETS
#             top_n = 20
#             # RANKING_DATA is assumed sorted High -> Low
#             winners = RANKING_DATA[:top_n]
#             losers = RANKING_DATA[-top_n:]
#             targets = []
#             # Add Winners with 1-based index (01, 02...)
#             for i, item in enumerate(winners):
#                 item['plot_rank'] = str(i + 1).zfill(2)
#                 targets.append(item)
#             # Add Losers with correct bottom index (e.g. 80, 81... 100)
#             total_count = len(RANKING_DATA)
#             for i, item in enumerate(losers):
#                 item['plot_rank'] = str(total_count - top_n + i + 1).zfill(2)
#                 targets.append(item)
#             logger.info(f"[plot_loop] Plotting {len(targets)} symbols (Top/Bottom 20)")
#             for item in targets:
#                 sym = item.get("symbol")
#                 dfs = item.get("dfs_for_calc", {})
#                 # Ensure "D" is fetched
#                 if "D" not in dfs:
#                     dfs["D"] = await convert_tradier_bars_to_analysis_df(sym, "D")
#                 await plot_dfs_subplots(
#                     item['plot_rank'], sym, dfs, 
#                     # ... pass other scores
#                 )
#             cleanup_old_plots(str(PLOTS_DIR), days_to_keep=1)
#             await asyncio.sleep(900) # Wait 15 mins
#         except Exception as e:
#             logger.error(f"Plot loop error: {e}")
#             await asyncio.sleep(60)
# async def plot_loop():
#     """Tradier plot loop - Includes D timeframe and plots Top 20 / Bottom 20"""
#     global RANKING_DATA, RANKING_INFO, LAST_RANKING_TIME
#     logger.info("[plot_loop] => Starting Tradier plot_loop...")
#     last_plotted_ranking_time = None
#     while True:
#         try:
#             if not RANKING_DATA:
#                 await asyncio.sleep(60); continue
#             # Wait logic for ranking completion...
#             if LAST_RANKING_TIME and (not last_plotted_ranking_time or LAST_RANKING_TIME > last_plotted_ranking_time):
#                 time_since_ranking = (datetime.now(timezone.utc) - LAST_RANKING_TIME).total_seconds()
#                 if time_since_ranking < 10:
#                     await asyncio.sleep(10 - time_since_ranking)
#                 last_plotted_ranking_time = LAST_RANKING_TIME
#             # 1. Identify Targets (Top 20 and Bottom 20)
#             top_n = 20
#             total_rankings = len(RANKING_DATA)
#             winners = RANKING_DATA[:top_n]
#             losers = RANKING_DATA[-top_n:] if total_rankings >= top_n else []
#             # Create a unified list of (rank_label, item)
#             targets = []
#             for i, item in enumerate(winners):
#                 targets.append((str(i + 1).zfill(2), item))
#             for i, item in enumerate(losers):
#                 actual_rank = total_rankings - len(losers) + i + 1
#                 targets.append((str(actual_rank).zfill(2), item))
#             logger.info(f"[plot_loop] Plotting {len(targets)} total symbols (Winners + Losers)")
#             # 2. Iterate through targets
#             for rank_label, item in targets:
#                 sym = item.get("symbol")
#                 if not sym: continue
#                 dfs = item.get("dfs_for_calc", {}) or {}
#                 # ADDED "D" BACK HERE
#                 plot_tfs = ["D", "4h", "1h", "15m", "5m"] 
#                 # Fetch missing data including D
#                 for tf in plot_tfs:
#                     if tf not in dfs or not isinstance(dfs.get(tf), pd.DataFrame) or dfs.get(tf).empty:
#                         try:
#                             df_tf = await convert_tradier_bars_to_analysis_df(sym, tf)
#                             if not df_tf.empty:
#                                 dfs[tf] = df_tf
#                         except Exception as e:
#                             logger.error(f"Error fetching {sym} {tf}: {e}")
#                 if not dfs: continue
#                 try:
#                     # Call the subplot function (Make sure D is handled inside)
#                     await plot_dfs_subplots(
#                         rank_label, sym, dfs, 
#                         item.get("final_score_norm", 0.0),
#                         item.get("final_score_norm_lt", 0.0), 
#                         item.get("final_score_norm_st", 0.0),
#                         0.0, 0.0, 
#                         item.get("trend_val_norm_lt", 0.0), 0.0,
#                         proximity_score_norm=item.get("proximity_score_norm_5m", 0.0),
#                         rel_volume=item.get("rel_vol_raw", 0.0), 
#                         slope_str="",
#                         highlight_heatmap=True, 
#                         linear_val=item.get("linearity_norm", 0.0),
#                         rel_gains=item.get("weighted_gains_lt", 0.0),
#                         output_dir=PLOTS_DIR, 
#                         RANKING_INFO_ARG=RANKING_INFO
#                     )
#                 except Exception as plot_err:
#                     logger.error(f"Error plotting {sym}: {plot_err}")
#             cleanup_old_plots(str(PLOTS_DIR), days_to_keep=1)
#             await asyncio.sleep(1800) # Plot every 10 mins
#         except Exception as e:
#             logger.error(f"[plot_loop] Global error: {e}")
#             await asyncio.sleep(60)


async def plot_loop():
    """Tradier plot loop - Plots Top 20 / Bottom 20 using in-memory dfs_for_calc"""
    global RANKING_DATA, LAST_RANKING_TIME
    logger.info("[plot_loop] => Starting Tradier plot_loop background task...")
    last_plotted_ranking_time = None
    
    while True:
        try:
            if not RANKING_DATA:
                await asyncio.sleep(30)
                continue
            if LAST_RANKING_TIME == last_plotted_ranking_time:
                await asyncio.sleep(30)
                continue

            logger.info("[plot_loop] New ranking detected. Preparing plots...")
            await asyncio.sleep(5)

            top_n = 20
            total_rankings = len(RANKING_DATA)
            # Plot the actual long candidate set, not raw high scores that were
            # vetoed for bearish daily direction or severe drawdown.
            winners = [item for item in RANKING_DATA if item.get("long_eligible", False)][:top_n]
            losers = RANKING_DATA[-top_n:] if total_rankings >= top_n else []
            targets = []

            for i, item in enumerate(winners):
                targets.append((str(i + 1).zfill(2), item))

            for i, item in enumerate(losers):
                actual_rank = total_rankings - len(losers) + i + 1
                targets.append((str(actual_rank).zfill(2), item))

            logger.info(f"[plot_loop] Generating {len(targets)} plots...")

            for rank_label, item in targets:
                sym = item.get("symbol")
                if not sym:
                    continue

                # FIX: Use the rich dfs_for_calc that is already in memory from the ranking step!
                # This avoids API rate limits, empty DataFrames, and missing indicator data.
                dfs = item.get("dfs_for_calc", {})

                if not dfs:
                    logger.warning(f"[plot_loop] No dfs_for_calc found in memory for {sym}")
                    continue

                try:
                    await plot_dfs_subplots(
                        rank_label, 
                        sym, 
                        dfs, 
                        item.get("final_score_norm", 0.0),
                        item.get("final_score_norm_lt", 0.0), 
                        item.get("final_score_norm_st", 0.0),
                        item.get("proximity_score_norm_5m", 0.0), # prox_norm_arg
                        item.get("band_score", 0.0),              # band_score_arg
                        item.get("trend_val_norm_lt", 0.0), 
                        0.0, # rp_g
                        proximity_score_norm=item.get("proximity_score_norm_5m", 0.0),
                        rel_volume=item.get("rel_vol_raw", 0.0), 
                        slope_str="",
                        highlight_heatmap=True, 
                        linear_val=item.get("linearity_norm", 0.0),
                        rel_gains=item.get("weighted_gains_lt", 0.0),
                        output_dir=PLOTS_DIR )
                except Exception as e:
                    logger.error(f"[plot_loop] Error plotting {sym}: {e}")

            last_plotted_ranking_time = LAST_RANKING_TIME
            cleanup_old_plots(str(PLOTS_DIR), days_to_keep=1)
            logger.info(f"[plot_loop] Cycle complete. Waiting for next ranking update...")

        except Exception as e:
            logger.error(f"[plot_loop] Critical Loop Error: {e}")
            await asyncio.sleep(60)

        #         # RECONSTRUCTION: Re-fetch DataFrames for plotting. 
        #         # We do this because RANKING_DATA was stripped of DataFrames 
        #         # to keep the JSON output serializable and small.
        #         dfs = {}
        #         for tf in ["D", "4h", "1h", "15m", "5m"]:
        #             try:
        #                 df_tf = await convert_tradier_bars_to_analysis_df(sym, tf)
        #                 if not df_tf.empty:
        #                     dfs[tf] = df_tf
        #             except Exception as e:
        #                 logger.debug(f"[plot_loop] Failed to re-fetch {tf} for {sym}: {e}")

        #         if not dfs:
        #             continue
        #         try:
        #             await plot_dfs_subplots(
        #                 rank_label, 
        #                 sym, 
        #                 dfs, 
        #                 item.get("final_score_norm", 0.0),
        #                 item.get("final_score_norm_lt", 0.0), 
        #                 item.get("final_score_norm_st", 0.0),
        #                 0.0, # p0
        #                 0.0, # p1
        #                 item.get("trend_val_norm_lt", 0.0), 
        #                 0.0, # rp_g
        #                 proximity_score_norm=item.get("proximity_score_norm_5m", 0.0),
        #                 rel_volume=item.get("rel_vol_raw", 0.0), 
        #                 slope_str="",
        #                 highlight_heatmap=True, 
        #                 linear_val=item.get("linearity_norm", 0.0),
        #                 rel_gains=item.get("weighted_gains_lt", 0.0),
        #                 output_dir=PLOTS_DIR )
        #         except Exception as e:
        #             logger.error(f"[plot_loop] Error plotting {sym}: {e}")

        #     # 4. Mark this ranking as done
        #     last_plotted_ranking_time = LAST_RANKING_TIME
        #     cleanup_old_plots(str(PLOTS_DIR), days_to_keep=1)
        #     logger.info(f"[plot_loop] Cycle complete. Waiting for next ranking update...")

        # except Exception as e:
        #     logger.error(f"[plot_loop] Critical Loop Error: {e}")
        #     await asyncio.sleep(60)

async def main():
    """Main entry point for Tradier rankings"""
    global api_client, bar_manager, redis_manager, price_cache_manager
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        sys.exit(0)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    if not _acquire_instance_lock():
        return
    try:
        logger.info("🚀 Starting Tradier rankings...")
        # Initialize API client and bar manager
        api_client = TradierAPIClient(config)
        await api_client.connect()
        # Initialize PriceCacheManager first (required for BarManager)
        price_cache_manager = TradierPriceCacheManager(config.DATA_DIR)
        # Initialize Bar Manager with Price Cache
        bar_manager = TradierBarManager(api_client, config.KLINES_CACHE_DIR, price_cache_manager)
        logger.info("✅ API client and bar manager initialized")
        # Initialize Redis
        try:
            redis_manager = await get_simple_redis_manager()
            logger.info("✅ Redis manager initialized")
            # Link redis to price cache if available
            if redis_manager:
                price_cache_manager.redis_manager = redis_manager
        except Exception as e:
            logger.warning(f"⚠️ Redis not available: {e}")
        await _load_news_sentiment_tradier(redis_manager)
        # Load symbols
        symbols_list = load_symbols()
        if not symbols_list:
            logger.error("❌ No symbols loaded!")
            return
        logger.info(f"✅ Loaded {len(symbols_list)} symbols")
        # Create initial empty rankings file to show script is running
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            rankings_file = config.DATA_DIR / "tradier_rankings.json"
            if not rankings_file.exists():
                with open(rankings_file, 'w') as f:
                    json.dump({}, f, indent=2)
                logger.info("[rankings] Created initial empty rankings file")
        except Exception as e:
            logger.error(f"[rankings] Error creating initial file: {e}")
        # Cleanup old plots on startup
        cleanup_old_plots(str(PLOTS_DIR), days_to_keep=DAYS_PLOT)
        # Initialize caches (from ez_rankings)
        await initialize_caches()
        # Run initial ranking immediately
        logger.info("🔄 Running initial ranking...")
        try:
            initial_ranking, initial_metadata = await initial_fetch_and_ranking(symbols_list, TRADIER_TIMEFRAMES)
            if initial_ranking:
                global RANKING_DATA, RANKING_INFO, LAST_RANKING_TIME
                RANKING_DATA = initial_ranking
                RANKING_INFO = build_ranking_info(RANKING_DATA, TRADIER_TIMEFRAMES)
                if initial_metadata:
                    RANKING_INFO["metadata"] = initial_metadata
                RANKING_INFO.setdefault("metadata", {}).update({
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "symbol_count": len(RANKING_DATA),
                    "source": "tradier_ranking_initial"
                })
                LAST_RANKING_TIME = datetime.now(timezone.utc)
                await save_rankings_json(RANKING_DATA)
                logger.info(f"✅ Initial ranking completed: {len(RANKING_DATA)} symbols")
            else:
                logger.warning("⚠️ Initial ranking produced no data")
        except Exception as e:
            logger.error(f"❌ Error in initial ranking: {e}", exc_info=True)
        # Start background tasks
        tasks = [
            asyncio.create_task(ranking_loop(symbols_list)),
            asyncio.create_task(plot_loop()),
            asyncio.create_task(indicators_sync_loop()),
            asyncio.create_task(market_mode_poll_loop()),
        ]
        logger.info("✅ All systems go! Running Tradier ranking and plot loops...")
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        if api_client:
            await api_client.close()
        logger.info("🛑 Shutting down...")
if __name__ == "__main__":
    asyncio.run(main())
