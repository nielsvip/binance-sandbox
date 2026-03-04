import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

@dataclass
class TradierConfig:
    HIGH_GAIN_AUGMENTATION_MIN_SIZE: float = 200.0
    REDUCTION_COOLDOWN_SECONDS: float = 60.0
    AUGMENTATION_COOLDOWN_SECONDS: float = 300.0
    MIN_GAIN_TO_BUY_AGGRESSIVELY: float = 1.15
    MIN_POSITION_SIZE: float = 100.0
    MAX_POSITION_SIZE: float = 5000.0
    START_POSITION_SIZE: float = 400.0
    MAX_ORDER_VALUE: float = 2000.0
    ACCOUNT_SIDE_MAPPING: Dict[str, List[str]] = field(default_factory=lambda: {"tra": ["LONG", "SHORT"]})
    BLACKLIST = ['PLTR' ,'MSTR', 'BTC', 'ETHE', 'GOOG', 'XIACF'] #Tradingview
    ALWAYS_TRADEABLE =["AAPL", "MSTR", "NVDA", "GOOGL", "META", "MSFT", "GLD"]
    NON_SHORTABLE = {"ETHE", "TCEHY", "XIACF", "BITO", "GBTC", "MSTR", "MARA", "RIOT", "CLSK", "HIVE", "CAN", "BTBT", "CUBT", "ETH", "BTC","QUBT","DUOL", "GLD", "USAR", "ETHD", "SBIT", "INOD", "BTCL", "DIME"}
    EXCEPTIONS = ['AAPL', 'GOOGL', 'MSFT', 'NVDA', 'CVX', 'SNDK', 'IBIT', 'MSTR', 'GLD', 'ETH'] #4* max order size and max pos size
    # === NEWS SENTIMENT ===
    NEWS_SENTIMENT_ENABLED: bool =         True
    NEWS_SENTIMENT_WEIGHT: float =         0.10
    # --- 2. Verbose Settings ---
    VERBOSE: bool = True
    VERBOSE2: bool = False
    VERBOSE_STOPS: bool = True
    VERBOSE_TIMER: bool = False
    VERBOSE_FETCH_LOGGING: bool = False
    DEBUG: bool = False

    # --- 3. API Configuration ---
    TRADIER_API_BASE_URL: str = "https://api.tradier.com/v1"
    TRADIER_SANDBOX_URL: str = "https://sandbox.tradier.com/v1"
    TRADIER_STREAMING_URL: str = "https://stream.tradier.com/v1"
    TRADIER_WS_URL: str = "wss://ws.tradier.com/v1"

    # TRADIER_WS_URL: str = "wss://ws.tradier.com/v1/markets/events"
    
    # --- 4. Global Settings ---
    USE_SANDBOX: bool = os.getenv("TRADIER_USE_SANDBOX", "false").lower() == "true"

    # --- 5. Rate Limits ---
    API_RATE_LIMIT_PER_SECOND: int = 10
    API_RATE_LIMIT_PER_MINUTE: int = 300
    WS_RECONNECT_DELAY: float = 5.0

    # --- 6. PATH RESOLUTION ---
    def _resolve_base_path() -> Path:
        if os.getenv("BASE_PATH"): return Path(os.getenv("BASE_PATH"))
        system = platform.system()
        home = Path.home()
        candidates = [home / "Documents" / "binance", home / "binance", Path("/binance")]
        for p in candidates:
            if p.exists(): return p
        return home / "binance"

    AVAILABLE_IPS: List[str] = field(default_factory=lambda: ["49.13.39.233"])
       # "5.75.211.216", "49.13.32.80",  "157.180.125.52" ])
    
    ENABLE_IP_ROTATION: bool = field(init=False)

    ACCOUNT_KEYS = ['tra','trb','trc']
    BASE_PATH: Path = _resolve_base_path()
    DATA_DIR: Path = BASE_PATH / "data" / "tradier"
    KLINES_CACHE_DIR: Path = BASE_PATH / "klines_cache" / "tradier"
    SYMBOLS_FILE: Path = BASE_PATH / "symbols_tradier.json"
    TRADIER_SYMBOLS_FILE: Path = BASE_PATH / "symbols_tradier.json"
    LEADERBOARD_LONG: Path = BASE_PATH / "symbols_long_tr.json"
    LEADERBOARD_SHORT: Path = BASE_PATH / "symbols_short_tr.json"
    INDICATORS_FILE: Path = DATA_DIR / "tradier_indicators_latest.json"
    PRICE_CACHE_FILE: Path = DATA_DIR / "price_cache_tradier.json"
    PRICE_CACHE_FILE_2: Path = DATA_DIR / "price_cache_tradier_2.json"
    PRICE_CACHE_FILE_3: Path = DATA_DIR / "price_cache_tradier_3.json"
    SYMBOL_CONFIGS_FILE: Path = DATA_DIR / "symbol_configs_tradier.json"
    MULT_FILE: Path = DATA_DIR / "multipliers_tradier.json"
    RANKING_RESULTS_FILE: Path = DATA_DIR / "ranking_results_tradier.json"
    REDIS_HOST: str = os.getenv("REDIS_HOST", 'localhost')
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
    REDIS_DB: int = 0
    REDIS_KEY_MARKET_DATA: str = "tradier_indicators_latest" 
    REDIS_CHANNEL_MARKET_DATA: str = "tradier_indicators_channel"
    REDIS_CHANNEL_PRICES: str = "tradier_prices_channel"
    REDIS_CHANNEL_POSITIONS: str = "tradier_positions_channel"

    PRICE_REFRESH_INTERVAL: float = 3.0
    POSITION_REFRESH_INTERVAL: float = 6.0
    ORDER_CACHE_TTL: int = 10
    POSITION_CACHE_TTL: int = 5
    PRICE_UPDATE_INTERVAL: float = 1.0
    INDICATOR_UPDATE_INTERVAL: float = 60.0
    RANKING_UPDATE_INTERVAL: float = 300.0
    TIMEFRAMES: List[str] = field(default_factory=lambda: ["1m", "5m", "15m", "1h", "4h", "D"])
    MARKET_OPEN_HOUR: int = 9
    MARKET_OPEN_MINUTE: int = 30
    MARKET_CLOSE_HOUR: int = 16
    MARKET_CLOSE_MINUTE: int = 0
    EXTREME_MODE: bool = False
    LIGHT_MODE: bool = False
    MARKET_MODE: str = "NORMAL_MODE"
    REV_MODE: bool = False
    EXIT_ON_ALL: bool = True
    TREND_GATES: bool = True
    LEADERBOARD_FILTER: bool = False
    HTF1_CONF: bool = False
    HTF4_CONF: bool = False
    ENABLE_FAST_RISER_REDUCE: bool = False
    
    # --- 11. Logging ---
    LOG_DIR: Path = Path.home() / "logs"
    LOG_FILE_TRADIER_PRICES: Path = LOG_DIR / "tradier_prices.log"
    LOG_FILE_TRADIER_POSITIONS: Path = LOG_DIR / "tradier_positions.log"
    LOG_FILE_TRADIER_MANAGE: Path = LOG_DIR / "tradier_manage.log"
    LOG_MAX_BYTES: int = 1024 * 1024 * 20
    LOG_BACKUP_COUNT: int = 30
    

    ACCOUNTS: Dict[str, Dict[str, str]] = field(default_factory=lambda: {
        "tra": {
            "id": os.getenv("TRADIER_ACCOUNT_ID_TRA") or os.getenv("TRADIER_ACCOUNT_ID"),
            "key": os.getenv("TRADIER_API_KEY_TRA") or os.getenv("TRADIER_ACCESS_TOKEN") or os.getenv("TRADIER_API_KEY"),
            "env": "live" },
        "trb": {
            # FIX: Corrected Env Var name from 'TRADIER_account_key_TRB' and added fallback keys
            "id": os.getenv("TRADIER_ACCOUNT_ID_TRB"),
            "key": os.getenv("TRADIER_API_KEY_TRB") or os.getenv("TRADIER_API_KEY_TRA") or os.getenv("TRADIER_API_KEY"),
            "env": "live"  
        },
        "trc": {
            "id": os.getenv("TRADIER_ACCOUNT_ID_TRC")  or os.getenv("TRADIER_SANDBOX_ID"),
            "key": os.getenv("TRADIER_API_KEY_TRC") or os.getenv("TRADIER_SANDBOX_ACCESS_TOKEN") or os.getenv("TRADIER_SANDBOX_KEY"),
            "env": "paper" } })

    TRADIER_ACCOUNT_ID: str = os.getenv("TRADIER_ACCOUNT_ID_TRC", "")
    TRADIER_API_KEY: str = os.getenv("TRADIER_API_KEY_TRC", "")

    def get_account_config(self, input_val: str) -> Optional[Dict[str, Any]]:
        clean_input = str(input_val).strip()
        if clean_input.lower() in self.ACCOUNTS:
            alias = clean_input.lower()
            data = self.ACCOUNTS[alias]
        else:
            found_alias = None
            data = None
            for a, info in self.ACCOUNTS.items():
                if str(info.get('id', '')).strip() == clean_input:
                    found_alias = a
                    data = info
                    break
            
            if not found_alias:
                return None
            
            alias = found_alias

        env_type = 'live' if input_val.lower() in ['tra', 'trb'] else 'sandbox'

        if env_type == 'live':
            target_url = self.TRADIER_API_BASE_URL
            is_sandbox = False
        else:
            target_url = self.TRADIER_SANDBOX_URL
            is_sandbox = True
            
        # Determine Directories
        account_dir = self.BASE_PATH / alias
        account_dir.mkdir(parents=True, exist_ok=True)
        
        return {
            "account_key": alias,           # The ALIAS (tra/trc)
            "account_id": data['id'],       # The NUMBER (6YA...)
            "api_key": data['key'],         # The TOKEN
            "api_url": target_url,          # The URL
            "data_dir": self.DATA_DIR,        
            "account_dir": account_dir, 
            "is_sandbox": is_sandbox         }

    def __post_init__(self):
        self._apply_mode(self._resolve_initial_mode())
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.KLINES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.ENABLE_IP_ROTATION = (  sys.platform != "darwin"   and len(self.AVAILABLE_IPS) > 0  and os.getenv("EZ_DISABLE_IP_BINDING") != "1" )

    def _resolve_initial_mode(self) -> str:
        if self.EXTREME_MODE and not self.LIGHT_MODE: return "EXTREME_MODE"
        if self.LIGHT_MODE and not self.EXTREME_MODE: return "LIGHT_MODE"
        return "NORMAL_MODE"
    
    def _apply_mode(self, mode: str):
        pass

    @property
    def api_url(self) -> str: 
        return self.TRADIER_SANDBOX_URL if self.USE_SANDBOX else self.TRADIER_API_BASE_URL