#!/usr/bin/env python3
import asyncio
import json
import os
import sys
from pathlib import Path
from datetime import datetime

# Import your config/utils
try:
    from config_tradier import TradierConfig
    from utils import get_simple_redis_manager, load_environment_from_gpg
except ImportError as e:
    print(f"❌ CRITICAL: Could not import project modules: {e}")
    print("Run this script from the project root directory.")
    sys.exit(1)

# Init Config
load_environment_from_gpg(None)
config = TradierConfig()

# Setup Paths
BASE_PATH = config.BASE_PATH
DATA_DIR = config.DATA_DIR

print(f"\n{'='*60}")
print(f"🔍 TRADIER INDICATORS DIAGNOSTIC")
print(f"{'='*60}")
print(f"📂 BASE_PATH: {BASE_PATH}")
print(f"📂 DATA_DIR:  {DATA_DIR}")

async def load_master_symbols():
    """Load the master list of symbols we EXPECT to find."""
    print(f"\n--- STEP 1: LOADING MASTER SYMBOLS ---")
    
    # Try symbols_tradier.json
    fpath = BASE_PATH / "symbols_tradier.json"
    symbols = set()
    
    if fpath.exists():
        try:
            with open(fpath, 'r') as f:
                data = json.load(f)
                if isinstance(data, list):
                    symbols.update(str(s).upper().strip() for s in data)
                elif isinstance(data, dict):
                    symbols.update(str(k).upper().strip() for k in data.keys())
            print(f"✅ Loaded {len(symbols)} symbols from symbols_tradier.json")
        except Exception as e:
            print(f"❌ Error reading symbols_tradier.json: {e}")
    else:
        print(f"⚠️ symbols_tradier.json NOT FOUND. Attempting to aggregate account files...")
        # Fallback: Aggregate account files
        for acc in ['tra', 'trb', 'trc']:
            p = BASE_PATH / f"symbols_long_{acc}.json"
            if p.exists():
                try:
                    with open(p, 'r') as f:
                        d = json.load(f)
                        symbols.update(str(s).upper() for s in d)
                except: pass
            p = BASE_PATH / f"symbols_short_{acc}.json"
            if p.exists():
                try:
                    with open(p, 'r') as f:
                        d = json.load(f)
                        symbols.update(str(s).upper() for s in d)
                except: pass
        print(f"ℹ️  Aggregated {len(symbols)} symbols from account files.")

    if not symbols:
        print("❌ CRITICAL: No symbols found to check against.")
        sys.exit(1)
        
    return sorted(list(symbols))

async def check_redis(symbols):
    print(f"\n--- STEP 2: CHECKING REDIS ---")
    try:
        redis = await get_simple_redis_manager()
        if not redis:
            print("❌ Failed to initialize Redis Manager.")
            return {}
            
        print("✅ Redis Manager Initialized.")
        
        # 1. Check Key Existence
        keys = ["tradier_indicators_latest"]
        found_data = None
        
        for k in keys:
            val = await redis.get(k)
            if val:
                print(f"✅ Found Redis Key: '{k}'")
                if isinstance(val, dict):
                    found_data = val
                elif isinstance(val, str):
                    try:
                        found_data = json.loads(val)
                    except:
                        print(f"❌ Failed to parse Redis value string.")
                break
        
        if not found_data:
            print("❌ Redis Key 'tradier_indicators_latest' is EMPTY or MISSING.")
            return {}
            
        print(f"✅ Redis Data Contains {len(found_data)} symbols.")
        
        # Analyze
        missing = [s for s in symbols if s not in found_data]
        if missing:
            print(f"⚠️  MISSING IN REDIS ({len(missing)}): {missing[:10]}...")
        else:
            print("✅ ALL master symbols found in Redis.")
            
        return found_data
        
    except Exception as e:
        print(f"❌ Redis Connection Error: {e}")
        return {}

def check_files(symbols):
    print(f"\n--- STEP 3: CHECKING FILES (DATA_DIR) ---")
    
    # 1. Find Files
    files = list(DATA_DIR.glob("tradier_indicators_*.json"))
    if not files:
        print(f"❌ No indicator files found in {DATA_DIR}")
        return {}
        
    # 2. Sort by Name Descending (Newest Timestamp First)
    files.sort(key=lambda p: p.name, reverse=True)
    
    target_file = files[0]
    print(f"📂 Found {len(files)} files.")
    print(f"👉 Reading Newest: {target_file.name}")
    print(f"   (Modified: {datetime.fromtimestamp(target_file.stat().st_mtime)})")

    try:
        with open(target_file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            data = json.loads(content)
            
        if not isinstance(data, dict):
            print("❌ File content is not a dictionary.")
            return {}
            
        print(f"✅ File Data Contains {len(data)} symbols.")
        
        missing = [s for s in symbols if s not in data]
        if missing:
            print(f"⚠️  MISSING IN FILE ({len(missing)}): {missing[:10]}...")
        else:
            print("✅ ALL master symbols found in File.")
            
        return data

    except Exception as e:
        print(f"❌ Error reading file: {e}")
        return {}

def inspect_symbol(symbol, redis_data, file_data):
    print(f"\n--- STEP 4: INSPECTING '{symbol}' ---")
    
    r_entry = redis_data.get(symbol)
    f_entry = file_data.get(symbol)
    
    if not r_entry and not f_entry:
        print(f"❌ Symbol {symbol} not found in either source.")
        return

    # Pick one source to check structure
    source = "Redis" if r_entry else "File"
    data = r_entry if r_entry else f_entry
    
    print(f"✅ Source: {source}")
    
    # Check Critical Keys
    keys_to_check = ['price', 'current_price', 'stoch_k_5m', 'timestamp', '0sentiment_rank']
    print("   Field Check:")
    for k in keys_to_check:
        val = data.get(k)
        print(f"   - {k:<20}: {val} (Type: {type(val).__name__})")

async def main():
    # 1. Load Symbols
    symbols = await load_master_symbols()
    
    # 2. Check Redis
    redis_data = await check_redis(symbols)
    
    # 3. Check Files
    file_data = check_files(symbols)
    
    # 4. Inspect Sample
    sample = symbols[0] if symbols else "AAPL"
    inspect_symbol(sample, redis_data, file_data)
    
    # 5. Conclusion
    print(f"\n{'='*60}")
    print("DIAGNOSIS:")
    
    redis_ok = len(redis_data) > 0
    file_ok = len(file_data) > 0
    
    if redis_ok:
        print("✅ Redis is ACTIVE and responding.")
    else:
        print("❌ Redis is EMPTY or UNREACHABLE.")
        
    if file_ok:
        print("✅ JSON Files are PRESENT and READABLE.")
    else:
        print("❌ JSON Files are MISSING or CORRUPT.")
        
    if not redis_ok and not file_ok:
        print("🔥 TOTAL SYSTEM FAILURE: No data source available.")
    elif redis_ok and not file_ok:
        print("⚠️  Running on REDIS ONLY (File system failing).")
    elif not redis_ok and file_ok:
        print("⚠️  Running on FILES ONLY (Redis failing).")
        
    print(f"{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(main())