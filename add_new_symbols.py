import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Try importing redis
try:
    import redis
except ImportError:
    print("❌ 'redis' module not found. Run: pip install redis")
    sys.exit(1)

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
try:
    from config import Config
    config = Config()
    BASE_PATH = Path(config.BASE_PATH)
    SYMBOLS_FILE = Path(config.SYMBOLS_FILE)
    TRADEABLE_KEYS_FILE = BASE_PATH / "tradeable_keys.json"
    ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
except ImportError:
    print("⚠️  Config not found, using defaults.")
    BASE_PATH = Path.home() / "binance"
    SYMBOLS_FILE = BASE_PATH / "symbols.json"
    TRADEABLE_KEYS_FILE = BASE_PATH / "tradeable_keys.json"
    ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]

# Redis Config
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
REDIS_DB = 0

# ---------------------------------------------------------
# EXACT SCHEMA TEMPLATE
# ---------------------------------------------------------
def get_empty_position_struct(symbol, side):
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "symbol": symbol,
        "position_side": side,
        "entry_price": 0.0,
        "mark_price": 0.0,
        "positionAmt": 0.0,
        "initial_quantity": 0.0,
        "gain": 0.0,
        "max_gain": 0.0,
        "prev_gain": 0.0,
        "max_quantity": 0.0,
        "last_augmentation_amount": 0.0,
        "last_augmentation_price": 0.0,
        "last_augmentation_time": None,
        "last_reduction_amount": 0.0,
        "last_reduction_price": 0.0,
        "last_reduction_time": None,
        "max_positionSize": 0.0,
        "opened_at": None,
        "last_updated": now_iso,
        "last_signal": "",
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "was_reentered": False,
        "was_reduced": False,
        "prev_gain_last_updated": None,
        "augment_reason": "",
        "reduction_reason": "",
        "mark_price_last_updated": now_iso
    }

# ---------------------------------------------------------
# MAIN LOGIC
# ---------------------------------------------------------
def main():
    print(f"📂 Base Path: {BASE_PATH}")
    
    # 1. Load Master Symbol List
    if not SYMBOLS_FILE.exists():
        print(f"❌ CRITICAL: {SYMBOLS_FILE} not found.")
        return

    with open(SYMBOLS_FILE, 'r') as f:
        try:
            content = json.load(f)
            if isinstance(content, list):
                master_symbols = [s.strip().upper() for s in content]
            elif isinstance(content, dict) and 'symbols' in content:
                master_symbols = [s.strip().upper() for s in content['symbols']]
            else:
                print("❌ Unknown format in symbols.json")
                return
        except Exception as e:
            print(f"❌ Error reading symbols.json: {e}")
            return

    print(f"✅ Loaded {len(master_symbols)} master symbols.")

    all_generated_keys = set()
    master_symbols_set = set(master_symbols)

    # 2. Iterate Accounts (File Updates)
    for account in ACCOUNTS:
        account_dir = BASE_PATH / account
        if not account_dir.exists():
            account_dir.mkdir(parents=True, exist_ok=True)

        for side in ["LONG", "SHORT"]:
            filename = f"{side.lower()}_positions.json"
            file_path = account_dir / filename
            
            data_map = {}
            file_is_valid = False

            if file_path.exists():
                try:
                    if os.path.getsize(file_path) == 0:
                        data_map = {}
                    else:
                        with open(file_path, 'r') as f:
                            raw = f.read().strip()
                            if not raw:
                                data_map = {}
                            else:
                                loaded = json.loads(raw)
                                # FLATTEN: If previously wrapped, unwrap it now
                                if isinstance(loaded, dict) and "positions" in loaded:
                                    data_map = loaded["positions"]
                                else:
                                    data_map = loaded
                                file_is_valid = True
                except json.JSONDecodeError:
                    print(f"    ⚠️  {filename} corrupted. Backing up.")
                    shutil.copy(file_path, str(file_path) + ".corrupt.bak")
                    data_map = {}
                except Exception as e:
                    print(f"    ❌ Error reading {filename}: {e}")
                    continue

            added_count = 0
            removed_count = 0
            
            # ADD missing symbols
            for symbol in master_symbols:
                position_key = f"{account}:{symbol}_{side}"
                all_generated_keys.add(position_key)
                
                if position_key not in data_map:
                    data_map[position_key] = get_empty_position_struct(symbol, side)
                    added_count += 1
            
            # REMOVE obsolete symbols with 0 balance
            keys_to_remove = []
            for pos_key, pos_data in data_map.items():
                sym = pos_data.get('symbol', '').upper()
                amt = float(pos_data.get('positionAmt', 0.0))
                # Only remove if it's not in master list AND has 0 position
                if sym and sym not in master_symbols_set and abs(amt) == 0.0:
                    keys_to_remove.append(pos_key)
            
            for k in keys_to_remove:
                del data_map[k]
                removed_count += 1

            if added_count > 0 or removed_count > 0 or not file_is_valid:
                # Backup
                if file_is_valid:
                    shutil.copy(file_path, str(file_path) + ".bak")
                
                print(f"🛠️  [{account}:{side}] Added {added_count} new symbols, Removed {removed_count} obsolete.")
                
                # CRITICAL FIX: Save as FLAT dictionary, NOT wrapped in "positions"
                final_payload = data_map 
                
                try:
                    temp_path = str(file_path) + ".tmp"
                    with open(temp_path, 'w') as f:
                        json.dump(final_payload, f, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(temp_path, file_path)
                except Exception as e:
                    print(f"    ❌ Failed to write {filename}: {e}")

    # # 3. UPDATE TRADEABLE KEYS
    # print("\n📜 Updating tradeable_keys.json...")
    # try:
    #     existing_keys = set()
    #     if TRADEABLE_KEYS_FILE.exists():
    #         with open(TRADEABLE_KEYS_FILE, 'r') as f:
    #             try:
    #                 content = json.load(f)
    #                 if isinstance(content, list):
    #                     existing_keys = set(content)
    #             except Exception: pass
        
    #     # Merge new keys, but drop removed keys based on our master loop
    #     updated_keys = sorted(list((existing_keys | all_generated_keys)))
    #     # Strictly, only keep keys that correspond to current master_symbols
    #     strict_updated_keys = [k for k in updated_keys if any(k.endswith(f":{sym}_LONG") or k.endswith(f":{sym}_SHORT") for sym in master_symbols_set)]
        
    #     with open(TRADEABLE_KEYS_FILE, 'w') as f:
    #         json.dump(strict_updated_keys, f, indent=2)
    #     print(f"✅ Updated {TRADEABLE_KEYS_FILE.name} with {len(strict_updated_keys)} keys.")
        
    # except Exception as e:
    #     print(f"❌ Failed to update tradeable_keys.json: {e}")

    # 4. REDIS FLUSH
    print("\n🧹 Flushing Redis Cache...")
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)
        r.ping()
        
        keys_to_delete = []
        for acc in ACCOUNTS:
            keys_to_delete.append(f"positions:{acc}")
        keys_to_delete.append("tradeable_keys")
        
        deleted_count = 0
        for key in keys_to_delete:
            if r.exists(key):
                r.delete(key)
                print(f"   🗑️  Deleted Redis Key: {key}")
                deleted_count += 1
        
        print(f"✅ Redis Cleaned. Removed {deleted_count} keys.")
        
    except redis.ConnectionError:
        print("⚠️  Could not connect to Redis. Ensure it is running or flush manually.")
    except Exception as e:
        print(f"❌ Redis Error: {e}")

    print("\n✅ Synchronization Complete. Changes are picked up dynamically by ez_positions_service.")

if __name__ == "__main__":
    main()