import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from pathlib import Path

import pandas as pd
import pytz

# Import from your existing setup
from config_tradier import TradierConfig
from tradier_api import TradierAPIClient

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("rescue_data")

ET = pytz.timezone("America/New_York")
UTC = pytz.UTC

config = TradierConfig()
api_client = TradierAPIClient(config, account_key='tra')

def filter_market_hours(df: pd.DataFrame, is_fast_tf: bool) -> pd.DataFrame:
    if df.empty: return df
    df = df.copy()
    
    # Ensure DatetimeIndex safely
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    elif df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
        
    dt_et = df.index.tz_convert(ET)
    time_series = dt_et.time
    
    start_time = dt_time(8, 30) if is_fast_tf else dt_time(9, 30)
    end_time = dt_time(16, 0)
    
    mask = (time_series >= start_time) & (time_series <= end_time)
    return df.loc[mask].copy()

def robust_resample(df: pd.DataFrame, timeframe_str: str) -> pd.DataFrame:
    if df.empty: return df
    df = df.copy()
    
    dt_et = df.index.tz_convert(ET)
    agg_rules = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    
    if timeframe_str == "4h":
        # Custom mapping for exactly 3 bars: 09:30, 12:45, 16:00
        def get_4h_bin(dt):
            t = dt.time()
            d = dt.date()
            if t < dt_time(9, 30):
                # Map 08:30 pre-market to the PREVIOUS day's 16:00 bar
                if dt.weekday() == 0:  # If Monday, go back to Friday
                    prev_d = (dt - pd.Timedelta(days=3)).date()
                elif dt.weekday() == 6: # If Sunday, go back to Friday
                    prev_d = (dt - pd.Timedelta(days=2)).date()
                else:
                    prev_d = (dt - pd.Timedelta(days=1)).date()
                return pd.Timestamp(datetime.combine(prev_d, dt_time(16, 0))).tz_localize(ET)
            elif t < dt_time(12, 45):
                return pd.Timestamp(datetime.combine(d, dt_time(9, 30))).tz_localize(ET)
            elif t < dt_time(16, 30):
                return pd.Timestamp(datetime.combine(d, dt_time(12, 45))).tz_localize(ET)
            else:
                return pd.Timestamp(datetime.combine(d, dt_time(16, 0))).tz_localize(ET)
                
        bins = dt_et.map(get_4h_bin)
        res = df.groupby(bins).agg(agg_rules).dropna(subset=["close"])
        res.index = pd.DatetimeIndex(res.index).tz_convert(UTC)
        return res
        
    else:
        # Standard flawless 1h binning
        shift_delta = pd.Timedelta(hours=9, minutes=30)
        df.index = dt_et - shift_delta
        res = df.resample(timeframe_str, label="left", closed="left").agg(agg_rules)
        res = res.dropna(subset=["close"]) 
        res.index = (res.index + shift_delta).tz_convert(UTC)
        return res

def pad_with_daily(df_target: pd.DataFrame, df_daily: pd.DataFrame) -> pd.DataFrame:
    if df_daily.empty: return df_target
    if df_target.empty: return df_daily.copy()
    
    first_target_time = df_target.index.min()
    first_target_date = first_target_time.tz_convert(ET).date()
    
    pad = df_daily[df_daily.index.tz_convert(ET).date < first_target_date].copy()
    if pad.empty: return df_target
    
    pad = pad[['open', 'high', 'low', 'close', 'volume']]
    combined = pd.concat([pad, df_target])
    return combined.sort_index()

def enforce_limits(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    if len(df) >= 1800:
        return df.tail(1200)
    return df

def export_json(df: pd.DataFrame, out_path: Path):
    if df.empty: return
    df = df.copy()
    
    # BULLETPROOF FIX: Force DatetimeIndex
    df.index = pd.DatetimeIndex(df.index)
    
    # FIX: Use %f for 6-digit microseconds correctly
    df['timestamp'] = df.index.tz_convert(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    records = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].to_dict('records')
    
    tmp_path = out_path.with_suffix('.tmp')
    with open(tmp_path, 'w') as f:
        json.dump(records, f, indent=4)
    os.replace(tmp_path, out_path)

async def process_symbol(symbol: str):
    logger.info(f"Rescuing data for {symbol}...")
    
    now = datetime.now(timezone.utc)
    start_1m = (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M")
    start_5m = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M")
    start_15m = (now - timedelta(days=45)).strftime("%Y-%m-%d %H:%M")
    start_D = (now - timedelta(days=3650)).strftime("%Y-%m-%d") 
    
    daily_raw = await api_client.get_history(symbol, start=start_D, interval='daily')
    raw_15m = await api_client.get_timesales(symbol, interval='15min', start=start_15m)
    raw_5m = await api_client.get_timesales(symbol, interval='5min', start=start_5m)
    raw_1m = await api_client.get_timesales(symbol, interval='1min', start=start_1m)

    df_D = pd.DataFrame(daily_raw) if daily_raw else pd.DataFrame()
    if not df_D.empty and 'date' in df_D.columns:
        df_D['dt'] = pd.to_datetime(df_D['date']).apply(
            lambda x: ET.localize(datetime.combine(x, dt_time(9, 30))).astimezone(UTC)
        )
        df_D = df_D.set_index(pd.DatetimeIndex(df_D['dt'])).sort_index()
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df_D[col] = pd.to_numeric(df_D[col], errors='coerce')
    else:
        df_D = pd.DataFrame()

    def parse_intraday(raw_data) -> pd.DataFrame:
        d = pd.DataFrame(raw_data)
        if d.empty or 'time' not in d.columns: return pd.DataFrame()
        
        # FIX: Localize Tradier's ET strings properly BEFORE converting to UTC
        d['dt'] = pd.to_datetime(d['time'])
        if d['dt'].dt.tz is None:
            d['dt'] = d['dt'].dt.tz_localize(ET, ambiguous='infer').dt.tz_convert(UTC)
        else:
            d['dt'] = d['dt'].dt.tz_convert(UTC)
            
        d = d.set_index(pd.DatetimeIndex(d['dt'])).sort_index()
        for col in['open', 'high', 'low', 'close', 'volume']:
            d[col] = pd.to_numeric(d[col], errors='coerce')
        return d

    df_15m = parse_intraday(raw_15m)
    df_5m = parse_intraday(raw_5m)
    df_1m = parse_intraday(raw_1m)

    df_D = filter_market_hours(df_D, is_fast_tf=False)
    df_15m = filter_market_hours(df_15m, is_fast_tf=True)
    df_5m = filter_market_hours(df_5m, is_fast_tf=True)
    df_1m = filter_market_hours(df_1m, is_fast_tf=True)

    df_1h = robust_resample(df_15m, "1h")
    df_4h = robust_resample(df_15m, "4h")

    df_1h = filter_market_hours(df_1h, is_fast_tf=False)
    df_4h = filter_market_hours(df_4h, is_fast_tf=False)

    df_1h = pad_with_daily(df_1h, df_D)
    df_4h = pad_with_daily(df_4h, df_D)

    data_bundle = {
        "D": enforce_limits(df_D),
        "4h": enforce_limits(df_4h),
        "1h": enforce_limits(df_1h),
        "15m": enforce_limits(df_15m),
        "5m": enforce_limits(df_5m),
        "1m": enforce_limits(df_1m),
    }

    config.KLINES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for tf, df_final in data_bundle.items():
        if not df_final.empty:
            path = config.KLINES_CACHE_DIR / f"{symbol}_{tf}.json"
            export_json(df_final, path)

async def main():
    await api_client.connect()
    symbols =[]
    if config.SYMBOLS_FILE.exists():
        with open(config.SYMBOLS_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                symbols =[str(s).upper() for s in data]
                
    if not symbols: return

    for sym in symbols:
        try:
            await process_symbol(sym)
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Failed to process {sym}: {e}")
            
    await api_client.close()
    logger.info("✅ ALL DATA RESCUED SUCCESSFULLY AND FORMATTED!")

if __name__ == "__main__":
    asyncio.run(main())