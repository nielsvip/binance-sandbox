import pandas as pd
import numpy as np
import json
import logging
from datetime import datetime, timedelta, timezone

# --- MOCK DEPENDENCIES ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Test")

class UnifiedTechIndicators:
    @staticmethod
    def calculate_stoch_rsi(series, period=14, k=3, d=3):
        # Exact StochRSI logic standard
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        min_rsi = rsi.rolling(window=period).min()
        max_rsi = rsi.rolling(window=period).max()
        stoch = (rsi - min_rsi) / (max_rsi - min_rsi)
        k_val = stoch.rolling(window=k).mean() * 100
        d_val = k_val.rolling(window=d).mean()
        return pd.DataFrame({'k': k_val, 'd': d_val}, index=series.index)

# --- THE LOGIC TO TEST (COPIED FROM YOUR CODE) ---
def test_logic(cold_data_list, hot_ticks_list, interval_str):
    print(f"\n🔬 TESTING: Interval={interval_str}")
    print(f"   Input Cold Rows: {len(cold_data_list)}")
    print(f"   Input Hot Ticks: {len(hot_ticks_list)}")

    # 1. LOAD COLD
    if cold_data_list:
        df = pd.DataFrame(cold_data_list)
        # Emulate file loading logic
        if 'timestamp' in df.columns:
            # CHECK: Does this actually handle the ISO string correctly?
            if df['timestamp'].dtype == object: 
                df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
            else: 
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df.set_index('timestamp', inplace=True)
        cold_df = df.sort_index()
    else:
        cold_df = pd.DataFrame()

    # 2. LOAD HOT
    if hot_ticks_list:
        df_hot_raw = pd.DataFrame(hot_ticks_list, columns=['ts', 'price'])
        # Emulate hot data prep
        df_hot_raw['ts'] = pd.to_datetime(df_hot_raw['ts'], utc=True, errors='coerce')
        df_hot_raw.set_index('ts', inplace=True)
    else:
        df_hot_raw = pd.DataFrame()

    # 3. STITCH (Your Function)
    k, d, kp, ts = _stitch_and_calc(cold_df, df_hot_raw, interval_str)
    
    print(f"   👉 RESULT: k1={k}, d1={d}")
    if k is None:
        print("   ❌ FAILED TO CALCULATE")
    else:
        print("   ✅ SUCCESS")

def _stitch_and_calc(cold_df, hot_raw_df, interval_str):
    # exact copy of your provided function
    if cold_df.empty and hot_raw_df.empty: 
        print("      [Debug] Both DFs empty")
        return None, None, None, 0.0

    hot_ohlc = pd.DataFrame()
    if not hot_raw_df.empty:
        try: 
            hot_ohlc = hot_raw_df['price'].resample(interval_str).ohlc().dropna()
            print(f"      [Debug] Resampled Hot Data: {len(hot_ohlc)} candles")
        except Exception as e: 
            print(f"      [Debug] Resample Error: {e}")
            pass
    
    full_df = pd.DataFrame()

    if cold_df.empty:
        full_df = hot_ohlc
    elif hot_ohlc.empty:
        full_df = cold_df
    else:
        # Timezone check
        if cold_df.index.tz is None: cold_df.index = cold_df.index.tz_localize('UTC')
        if hot_ohlc.index.tz is None: hot_ohlc.index = hot_ohlc.index.tz_localize('UTC')
        
        # LOGIC CHECK: Is this logic dropping data?
        if not cold_df.empty:
            last_cold_ts = cold_df.index[-1]
            print(f"      [Debug] Last Cold TS: {last_cold_ts}")
            # Only take hot data NEWER than last cold data
            hot_ohlc = hot_ohlc[hot_ohlc.index >= last_cold_ts]
            print(f"      [Debug] Hot Candles after filter: {len(hot_ohlc)}")
        
        full_df = pd.concat([cold_df, hot_ohlc])
        full_df = full_df[~full_df.index.duplicated(keep='last')]

    print(f"      [Debug] Total Rows for Calc: {len(full_df)}")

    if len(full_df) < 14: 
        print(f"      [Debug] Not enough data (<14)")
        return None, None, None, 0.0
    
    try:
        s = UnifiedTechIndicators.calculate_stoch_rsi(full_df['close'])
        if s.empty: return None, None, None, 0.0
        
        k = float(s.iloc[-1]['k'])
        d = float(s.iloc[-1]['d'])
        kp = float(s.iloc[-2]['k']) if len(s) > 1 else k
        return k, d, kp, full_df.index[-1].timestamp()
    except Exception as e: 
        print(f"      [Debug] Calc Exception: {e}")
        return None, None, None, 0.0

# --- RUN SCENARIOS ---

now = datetime.now(timezone.utc)

# 1. GENERATE COLD DATA (3m Candles, ISO String as per your JSON)
cold_data = []
for i in range(50):
    t = now - timedelta(minutes=(50-i)*3)
    cold_data.append({
        "timestamp": t.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
        "open": 100+i, "high": 105+i, "low": 95+i, "close": 102+i, "volume": 1000
    })

# 2. GENERATE HOT DATA (Ticks for the last 5 minutes)
hot_data = []
for i in range(300): # 300 seconds
    t = now - timedelta(seconds=(300-i))
    hot_data.append((t, 150 + (i*0.1)))

print("=== STARTING DIAGNOSTIC ===")

# SCENARIO A: Normal Operation (3m Cold + Seconds Hot -> fallback 3min logic)
# This simulates your fallback: interval_str='3min'
test_logic(cold_data, hot_data, '3min')

# SCENARIO B: Missing Hot Data (Should rely on Cold)
test_logic(cold_data, [], '3min')

# SCENARIO C: 1min Logic (If 1m file existed)
# Simulating 1m cold data existing
cold_data_1m = []
for i in range(50):
    t = now - timedelta(minutes=(50-i))
    cold_data_1m.append({
        "timestamp": t.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
        "open": 100, "high": 100, "low": 100, "close": 100, "volume": 100
    })
test_logic(cold_data_1m, hot_data, '1min')

# SCENARIO D: The "Seconds Only" Crash (If file load fails)
# If cold is empty, and we only have 5 mins of hot data
test_logic([], hot_data, '1min')