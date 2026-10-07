import os
import asyncio
from dataclasses import dataclass, field
from typing import Optional, Dict
import threading
from collections import deque

# --- MOCKING IMPORTS (So we don't need the full bot dependencies) ---
try:
    from utils import load_environment_from_gpg
    print("✅ Found utils.load_environment_from_gpg")
except ImportError:
    load_environment_from_gpg = None
    print("⚠️  Could not import utils. (Ensure this script is in the bot folder)")

# --- PASTE THE CORRECTED CLASS HERE ---
@dataclass
class AccountConfig:
    prefix: str
    api_key: str = field(init=False)
    api_secret: str = field(init=False)
    webhook_url: str = field(init=False)
    webhook_secret: str = field(init=False)
    
    webhook_url_2: Optional[str] = field(default=None, init=False)
    webhook_secret_2: Optional[str] = field(default=None, init=False)
    webhook_url_3: Optional[str] = field(default=None, init=False)
    webhook_secret_3: Optional[str] = field(default=None, init=False)
    
    client: Optional[object] = field(default=None, init=False)
    _last_used_weight: int = field(default=0, init=False) 
    _connector_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _ip_cycle: Optional[deque] = field(default=None, init=False)
    _current_ip: Optional[str] = field(default=None, init=False)
    _banned_ips: dict = field(default_factory=dict, init=False)
    
    def __post_init__(self):
        self.time_offset = 0.0
        self._last_time_sync = 0.0
        
        p = self.prefix.lower()
        P = self.prefix.upper()

        # THE LOGIC WE FIXED
        def get_val(base_name, suffix=""):
            # 1. Try lowercase prefix (ang_WEBHOOK_URL2)
            v1 = os.getenv(f"{p}_{base_name}{suffix}")
            if v1: return v1
            # 2. Try uppercase prefix (ANG_WEBHOOK_URL2)
            v2 = os.getenv(f"{P}_{base_name}{suffix}")
            if v2: return v2
            return None

        self.api_key = get_val("API_KEY")
        self.api_secret = get_val("API_SECRET")
        self.webhook_url = get_val("WEBHOOK_URL")
        self.webhook_secret = get_val("WEBHOOK_SECRET")

        self.webhook_url_2 = get_val("WEBHOOK_URL", "2")
        self.webhook_secret_2 = get_val("WEBHOOK_SECRET", "2")
        self.webhook_url_3 = get_val("WEBHOOK_URL", "3")
        self.webhook_secret_3 = get_val("WEBHOOK_SECRET", "3")

        self._init_ip_cycle()

    def _init_ip_cycle(self): pass # Mocked

def load_accounts_local() -> Dict[str, AccountConfig]:
    accounts = {}
    # TEST THESE KEYS
    keys_to_load = ['ang', 'flz'] 

    for key in keys_to_load:
        try:
            acc = AccountConfig(key)
            accounts[key] = acc
        except Exception as e:
            print(f"❌ Failed to init {key}: {e}")
    return accounts

# --- RUN TEST ---
if __name__ == "__main__":
    print("-" * 60)
    
    # 1. LOAD ENV
    if load_environment_from_gpg:
        print("🔓 Loading GPG Environment...")
        load_environment_from_gpg(None)
    else:
        print("⚠️  Running with current shell environment variables only.")

    print("-" * 60)
    
    # 2. RUN LOADER
    accounts = load_accounts_local()
    
    # 3. INSPECT RESULTS
    for key, acc in accounts.items():
        print(f"\n🔎 INSPECTING: {key.upper()}")
        
        # Helper to mask secrets
        def mask(s): return f"{s[:10]}...{s[-5:]}" if s and len(s) > 3 else "MISSING ❌"

        print(f"   Webhook 1: {mask(acc.webhook_url)}")
        print(f"   Secret  1: {mask(acc.webhook_secret)}")
        
        print(f"   Webhook 2: {mask(acc.webhook_url_2)}  <-- {'OK ✅' if acc.webhook_url_2 else 'EMPTY'}")
        print(f"   Webhook 3: {mask(acc.webhook_url_3)}  <-- {'OK ✅' if acc.webhook_url_3 else 'EMPTY'}")

    print("-" * 60)