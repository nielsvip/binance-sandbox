import sys
import os
import json
import glob
from pathlib import Path
from datetime import datetime, timezone

# ------------------------------------------------------------------
# 1. LOAD CONFIGURATION (Source of Truth)
# ------------------------------------------------------------------
try:
    sys.path.append(os.getcwd()) # Ensure current dir is in path
    from config import Config
    config = Config()
    
    BASE_PATH = Path(config.BASE_PATH)
    SYMBOLS_FILE = Path(config.SYMBOLS_FILE)
    ACCOUNTS = config.ACCOUNT_KEYS
    
    print(f"📍 BASE_PATH: {BASE_PATH}")
    print(f"📍 SYMBOLS_FILE: {SYMBOLS_FILE}")
    print(f"📍 ACCOUNTS: {ACCOUNTS}")
    
except ImportError:
    print("❌ CRITICAL: Could not import 'config.py'. Run this from the project root.")
    sys.exit(1)
except Exception as e:
    print(f"❌ CRITICAL: Error reading config: {e}")
    sys.exit(1)

# ------------------------------------------------------------------
# 2. LOAD MASTER SYMBOLS
# ------------------------------------------------------------------
try:
    if not SYMBOLS_FILE.exists():
        # Fallback to base_path/symbols.json if config path is relative or wrong
        SYMBOLS_FILE = BASE_PATH / "symbols.json"
        
    with open(SYMBOLS_FILE, "r") as f:
        content = json.load(f)
        if isinstance(content, dict):
            MASTER_SYMBOLS = set(content.get("symbols", []))
        elif isinstance(content, list):
            MASTER_SYMBOLS = set(content)
        else:
            MASTER_SYMBOLS = set()
            
    # Normalize
    MASTER_SYMBOLS = {str(s).strip().upper() for s in MASTER_SYMBOLS if s}
    
    if len(MASTER_SYMBOLS) < 10:
        print(f"❌ CRITICAL: symbols.json has only {len(MASTER_SYMBOLS)} items. Is it corrupted?")
        sys.exit(1)
        
    print(f"✅ Loaded {len(MASTER_SYMBOLS)} master symbols.")
    
except Exception as e:
    print(f"❌ Failed to load symbols file: {e}")
    sys.exit(1)

# ------------------------------------------------------------------
# 3. RESTORATION LOGIC
# ------------------------------------------------------------------

def get_data_quality_score(pos_data):
    """
    Score the data quality.
    Score 100: Active Position (amt != 0)
    Score  10: Closed Position but valid history (entry_price > 0)
    Score   0: Empty/Zeroed Position
    """
    if not isinstance(pos_data, dict): return -1
    
    amt = float(pos_data.get("positionAmt", 0.0) or 0.0)
    if abs(amt) > 0: return 100
    
    entry = float(pos_data.get("entry_price", 0.0) or 0.0)
    if entry > 0: return 10
    
    return 0

def restore_account(account):
    acc_dir = BASE_PATH / account
    backup_dir = acc_dir / "backups"
    
    if not acc_dir.exists():
        print(f"⚠️  Account {account} directory not found at {acc_dir}")
        return

    for side in ["LONG", "SHORT"]:
        target_file = acc_dir / f"{side.lower()}_positions.json"
        
        # 1. Gather Candidates (Active File + All Backups)
        candidates = []
        
        # Active file
        if target_file.exists():
            try:
                with open(target_file, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict): candidates.append(("ACTIVE", data))
            except: pass
            
        # Backups
        if backup_dir.exists():
            # Pattern: long_positions.json_backup_... OR long_positions_backup_...
            pats = [
                str(backup_dir / f"{side.lower()}_positions*backup*.json"),
                str(backup_dir / f"{side.lower()}*backup*.json")
            ]
            files = []
            for p in pats: files.extend(glob.glob(p))
            
            # Sort newest first
            files.sort(key=os.path.getmtime, reverse=True)
            
            # Load top 50 backups (deep scan)
            for bk in files[:50]:
                try:
                    with open(bk, "r") as f:
                        data = json.load(f)
                        if isinstance(data, dict): candidates.append((os.path.basename(bk), data))
                except: pass

        if not candidates:
            print(f"❌ {account} {side}: No data found (no active file, no backups).")
            continue

        # 2. Build The Sacred Dictionary
        sacred_data = {}
        restored_count = 0
        active_positions_count = 0
        
        for symbol in sorted(list(MASTER_SYMBOLS)):
            key = f"{account}:{symbol}_{side}"
            
            best_entry = None
            best_score = -1
            source_file = "NONE"
            
            # Find best version of this symbol across all history
            for filename, dataset in candidates:
                # Try exact key match
                if key in dataset:
                    entry = dataset[key]
                # Try symbol match (if key format differed in past)
                else:
                    entry = None
                    for val in dataset.values():
                        if isinstance(val, dict) and val.get("symbol") == symbol:
                            entry = val
                            break
                
                if entry:
                    score = get_data_quality_score(entry)
                    # If we find an active position (score 100), we take it immediately unless we already have one from a newer file
                    # Since candidates are sorted Newest -> Oldest, the first 100 score we hit is the most recent active state.
                    if score > best_score:
                        best_score = score
                        best_entry = entry
                        source_file = filename
                        
                        # Optimization: If we found active data from the ACTIVE file or very recent backup, stop looking
                        if score == 100: 
                            break 
            
            if best_entry:
                # Ensure structure
                best_entry["symbol"] = symbol
                best_entry["position_side"] = side
                sacred_data[key] = best_entry
                restored_count += 1
                if best_score >= 100: active_positions_count += 1
            else:
                # Initialize Empty (Must exist to satisfy "No less than master list")
                sacred_data[key] = {
                    "symbol": symbol, "position_side": side,
                    "positionAmt": 0.0, "entry_price": 0.0, "mark_price": 0.0,
                    "gain": 0.0, "max_gain": 0.0, "prev_gain": 0.0,
                    "last_updated": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    "last_signal": "SACRED_INIT_EMPTY"
                }
                restored_count += 1

        # 3. Final Verification & Save
        expected = len(MASTER_SYMBOLS)
        actual = len(sacred_data)
        
        if actual < expected:
            print(f"❌ {account} {side}: CRITICAL FAIL. Generated {actual} positions, expected {expected}. NOT SAVING.")
            continue
            
        print(f"✅ {account} {side}: Saving {actual} positions. ({active_positions_count} Active / {actual-active_positions_count} Empty).")
        
        # Atomic Write
        tmp_path = target_file.with_suffix(".tmp_sacred")
        try:
            with open(tmp_path, "w") as f:
                json.dump(sacred_data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, target_file)
        except Exception as e:
            print(f"❌ Error writing file: {e}")

if __name__ == "__main__":
    print("--- STARTING CONFIG-AWARE SACRED RESTORE ---")
    for acc in ACCOUNTS:
        restore_account(acc)
    print("\n🏁 RESTORE COMPLETE.")