import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytz

# --- CONFIGURATION ---
CACHE_DIR = Path("klines_cache/tradier") 
ET = pytz.timezone("America/New_York")
# Modern naming convention
TFS = ["1m", "5min", "15min", "1h", "4h", "D"]
MIN_TARGET = 1200
def verify_all_klines():
    if not CACHE_DIR.exists():
        print(f"❌ Cache directory not found: {CACHE_DIR}")
        return

    print(f"🔍 Verifying Tradier Klines (Modern Naming) in: {CACHE_DIR.absolute()}\n")

    files = list(CACHE_DIR.glob("*.json"))
    symbols = sorted(list(set(f.name.split('_')[0] for f in files)))

    for sym in symbols:
        print(f"--- Symbol: {sym.upper()} ---")
        data_bundle = {}
        
        for tf in TFS:
            f_path = CACHE_DIR / f"{sym}_{tf}.json"
            if f_path.exists():
                with open(f_path, 'r') as f:
                    try:
                        raw_data = json.load(f)
                        df = pd.DataFrame(raw_data)
                        if not df.empty and ('close' in df.columns):
                            ts_col = 'timestamp' if 'timestamp' in df.columns else 'time'
                            df["close_time"] = pd.to_datetime(df[ts_col], utc=True)
                            data_bundle[tf] = df
                        else:
                            data_bundle[tf] = pd.DataFrame()
                    except Exception:
                        data_bundle[tf] = pd.DataFrame()
            else:
                data_bundle[tf] = pd.DataFrame()

        if "1m" not in data_bundle or data_bundle["1m"].empty:
            print(f"  ❌ Missing or empty 1m base file.")
            continue

        # 1. Check 1m Noise
        df1m = data_bundle["1m"]
        df1m['et_time'] = df1m["close_time"].dt.tz_convert(ET).dt.time
        noise = df1m[~((df1m['et_time'] >= pd.Timestamp("09:30").time()) & 
                       (df1m['et_time'] <= pd.Timestamp("16:00").time()))]
        
        if not noise.empty:
            print(f"  ⚠️ 1m: Found {len(noise)} bars outside RTH (NOISE!)")
        else:
            print(f"  ✅ 1m: Clean session data ({len(df1m)} bars)")

        # 2. Check Chain Drift
        chain = [("5min", "1m"), ("15min", "5min"), ("1h", "15min"), ("4h", "1h"), ("D", "1h")]
        
        for target, source in chain:
            df_t = data_bundle.get(target, pd.DataFrame())
            df_s = data_bundle.get(source, pd.DataFrame())
            
            if df_t.empty:
                print(f"  ❌ {target}: File missing or empty")
                continue
            if df_s.empty:
                print(f"  ❌ {target}: Cannot verify (Source {source} is empty)")
                continue

            last_t = df_t['close'].iloc[-1]
            last_s = df_s['close'].iloc[-1]
            
            if not np.isclose(last_t, last_s):
                print(f"  ❌ {target}: DRIFT! Source ({source}): {last_s} Target ({target}): {last_t}")
            else:
                last_ts_et = df_t["close_time"].iloc[-1].astimezone(ET)
                if target == "D":
                    if last_ts_et.time() != pd.Timestamp("16:00").time():
                        print(f"  ❌ D: Wrong Close Time: {last_ts_et.time()} (Needs 16:00)")
                    else:
                        print(f"  ✅ D: Perfect 16:00 Close ({len(df_t)} bars)")
                else:
                    status = "✅" if len(df_t) >= MIN_TARGET else "⚠️"
                    print(f"  {status} {target}: Validated ({len(df_t)} bars)")

    print(f"\n✅ Verification Complete.")
    

if __name__ == "__main__":
    verify_all_klines()