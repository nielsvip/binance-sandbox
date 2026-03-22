import asyncio
import os
import platform
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar, Dict, List, Optional
from weakref import WeakSet

import aiofiles
import certifi


@dataclass(eq=False)
class Config:
    _INSTANCES: ClassVar[WeakSet] = WeakSet()
    _CURRENT_MARKET_MODE: ClassVar[str] = "NORMAL_MODE"

    def __hash__(self):
        return id(self)
    MIN_POSITION_SIZE: float =              0.3
    MAX_POSITION_SIZE: float =              800.0
    MAX_POSITION_SIZE_BTC: float =          6000.0
    MAX_POSITION_SIZE_MEN: float =          1200.0
    MAX_POSITION_SIZE_FIN: float =          4000.0
    HIGH_GAIN_AUGMENTATION_MIN_SIZE=        200

    MAX_ORDER_VALUE:float      =            280
    MAX_ORDER_VALUE_MEN: float =            240.0
    MAX_ORDER_VALUE_FIN: float =            120.0
    START_POSITION_SIZE: float =            55.0

    # PnL Deterioration Settings
    PNL_DECAY_START_HOURS: int =            1 #HOURS
    PNL_DECAY_COMPLETE_DAYS: int =          5 #DAYS
    
    PNL_DECAY_FINAL_PERCENTAGE: float =     0.1  # Keep 10% after full decay
    MAX_DECAY_START_HOURS=                  1
    MAX_DECAY_COMPLETE_DAYS=                7
    MAX_GAIN_DECAY_COMPLETE_DAYS=           7
    # PNL_PERFORMANCE_WINDOW_HOURS: int =     48   # Window for recent performance calculation
    ZERO_CONFIRMATION_THRESHOLD_WS:    int =   2
    ZERO_CONFIRMATION_THRESHOLD_API:    int =  1
    MIN_PERC_FROM_SMA_1: float          =   1.0 /100   #SMA_1
    MIN_PERC_FROM_SMA_15: float         =   3.0 /100   #SMA_15
    MIN_GAIN_TO_BUY_AGGRESSIVELY: float =   0.7
    # MIN_PROFIT_FOR_PROFIT_TAKING: float =   0.4

    EXTREME_MODE: bool =                    False
    LIGHT_MODE: bool =                      False
    MARKET_MODE: str =                      "NORMAL_MODE"
    REV_MODE: bool =                        False

    SERVICE_STOP=                           True
    SERVICE_REDUCE=                         True
    MANAGE_REDUCE=                          True
    HEDGE_MODE:bool =                       False
    SCALP_MODE =                            False  # OPTIMAL_V1: OFF (Phase 3 winner)
    SANDBOX_MODE: bool =                    False
    SANDBOX_ACCOUNTS: List[str] = field(default_factory=lambda: ['sbx'])

    # SERVICE_STOP=                           True
    # SERVICE_REDUCE=                         True
    # MANAGE_REDUCE=                          True
    # HEDGE_MODE:bool =                       False
    # SCALP_MODE =                            False

    SCALP_ACCOUNTS=                         ['ang','men', 'flz']
    HEDGE_ACCOUNTS=                         ['inf','fin','men','flz']
    STRICT_NO_LOSS_ACCOUNTS=                ['ang','inf','men','fin','flz']  # UNIVERSAL: never sell at a loss on ANY account
    SCALP_OVERRIDE=                         False
    ACCOUNT_OVERRIDES: Dict[str, Dict] = field(default_factory=lambda: {'ang': {}, 'inf': {}, 'men': {}, 'fin': {}, 'flz': {}})

    # --- Timeframe Focus Architecture (SANDBOX) ---
    TF_FOCUS: str =                         '15m'               # Primary decision timeframe
    TF_FOCUS_WEIGHT: float =                5.0                 # Weight multiplier for focus TF
    TF_FOCUS_ENTRY_HARD_GATE: bool =        True                # Focus TF must agree for entry
    TF_FOCUS_EXIT_HARD_GATE: bool =         True                # Focus TF crossunder = immediate exit
    TF_ALIGNMENT_MIN_TOTAL: int =           4                   # Min TFs aligned (out of 7)
    TF_ALIGNMENT_MIN_SHORT: int =           2                   # Min short TFs aligned (1m, 3m, 5m, 15m)
    TF_ALIGNMENT_MIN_LONG: int =            2                   # Min long TFs aligned (1h, 4h, D)

    ENABLE_LOSS_PROTECTION: bool =          True   # Block closing positions with gain <= 0.12%
    NEW_POSITION_MIN_AGE_SECONDS: float =   180.0   # Consider position "new" if opened within this time
    NEW_POSITION_MAX_LOSS_THRESHOLD: float= -0.7  # New positions can only reduce if loss > this threshold (loss > -0.2% allows reduction)
    # === STOP-THE-BLEED: Centralized Loss Prevention ===
    STOP_TIMEFRAME: str =                   '15m'  # Controls dc_low/dc_high used for stops (was hardcoded 3m)
    FAST_CUT_LOSS_THRESHOLD: float =        -1.5   # Was -0.65 (too tight)
    FAST_CUT_LOSS_MIN_AGE_MINUTES: float =  15.0   # Was 6 min (too short)
    AGGRESSIVE_LOSS_CUT_ENABLED: bool =     False  # Was implicitly True
    BREAKOUT_GUARD_LOSS_THRESHOLD: float =  -0.5   # Was -0.1 (instant death)
    BREAKOUT_GUARD_MOMENTUM_CHECK_ENABLED: bool = False  # Disables 1-sec momentum kills
    EXIT_ON_ALL_ENABLED: bool =             False  # Was True via EXIT_ON_ALL
    REDUCE_HUGE_LOSS_THRESHOLD: float =     -2.0   # Was -0.5
    HEDGE_TRIGGER_LOSS_PCT: float =         -0.3   # Was -1.0 (hedge earlier)
    ORPHAN_HEDGE_CHECK_GAIN: bool =         True   # Check gain before killing orphans
    REENTRY_MANDATORY: bool =               True   # Enforce reentry after every exit
    LOSS_EXIT_REQUIRES_HEDGE: bool =        True   # Master: can only exit at loss if hedge >= losing value
    HEDGE_OVERSIZE_RATIO: float =           1.1    # Hedge must be this * losing_value (110%)
    # === L/S RATIO ENFORCEMENT ===
    LS_RATIO_ENFORCE: bool =                True   # Master toggle for L/S ratio enforcement
    LS_RATIO_MIN: float =                   0.40   # Block new shorts if ratio drops below this
    LS_RATIO_MAX: float =                   2.50   # Block new longs if ratio rises above this
    LS_RATIO_HARD_MIN: float =              0.25   # HARD block: no shorts at all below this
    LS_RATIO_HARD_MAX: float =              4.00   # HARD block: no longs at all above this
    LS_RATIO_REBALANCE_THRESHOLD: float =   0.35   # Below this, actively open longs to rebalance
    LS_RATIO_LOG_INTERVAL: int =            60     # Seconds between ratio warning logs
    STORM_REDUCE_ENABLED: bool =            True   # Allow reducing losing position when HTF confirms storm
    # === NEWS SENTIMENT ===
    NEWS_SENTIMENT_ENABLED: bool =         True    # Master toggle for news sentiment in rankings
    NEWS_SENTIMENT_WEIGHT: float =         0.10    # Max +/-10% score adjustment from news
    NEWS_POLL_INTERVAL_CRYPTO: int =       300     # 5 min (CryptoPanic)
    NEWS_POLL_INTERVAL_SOCIAL: int =       900     # 15 min (Reddit + Twitter)
    NEWS_SENTIMENT_DECAY_HOURS: int =      4       # Older articles decay to 0
    NEWS_SENTIMENT_MIN_ARTICLES: int =     2       # Min sources to form a score
    # TREND_GATES: bool =                     False
    # HTF1_CONF:bool =                        False
    # HTF4_CONF:bool =                        False
    TREND_GATES: bool =                     True
    HTF1_CONF:bool =                        False  # OPTIMAL_V1: OFF (marginal improvement)
    HTF4_CONF:bool =                        True
    BASIS_CONDITION: bool =                 True #No opening on wrong side of dc_basis_15m + 1h + 4h

    MAX_HEDGE_BALANCE_VALUE_USD: float =    500.0  # Maximum USD value for hedge balance adjustments
    MAX_HEDGE_BALANCE_MULTIPLIER: float =   1.8  # Maximum hedge size multiplier (1.5x = hedge can be 1.5x regular position)
    HEDGE_BALANCE_COOLDOWN_SECONDS: float = 180.0  # Cooldown between hedge balance adjustments (5 minutes)



    # Loss Mitigator Settings (ez_loss_mitigator.py — ang account gain guard)
    MITIGATOR_ENABLED: bool =               True
    MITIGATOR_ACCOUNT: list = field(default_factory=lambda: ['ang','men'])
    MITIGATOR_SCAN_INTERVAL: float =        3.0
    MITIGATOR_TIER1_PEAK: float =           0.15   # Peak gain must reach this before tier 1 arms
    MITIGATOR_TIER1_DROP: float =           0.08   # Reduce 25% when gain drops to this
    MITIGATOR_TIER1_REDUCE_PCT: float =     0.25
    MITIGATOR_TIER2_DROP: float =           0.02   # Reduce 50% of remaining at breakeven
    MITIGATOR_TIER2_REDUCE_PCT: float =     0.50
    MITIGATOR_TIER3_DROP: float =           -0.05  # Full close — tiny loss better than big loss
    MITIGATOR_AUGMENT_THRESHOLD: float =    0.30   # Augment winners above this gain
    MITIGATOR_AUGMENT_CONSECUTIVE: int =    3      # Must rise for 3+ scans
    MITIGATOR_COOLDOWN: float =             15.0   # Seconds between actions per position
    MITIGATOR_REENTRY_COOLDOWN: float =     180.0  # 3 min before re-entry
    MITIGATOR_REENTRY_PRICE_PCT: float =    0.15   # Favorable price move for re-entry
    STOP_LOSS_THRESHOLD =                   0.2  # Minimum gain percentage that must be reached before stop loss can be set to 0. Before this, stop loss cannot come closer than dc_low_3m (LONG) or dc_high_3m (SHORT)
    GAIN_THRESHOLD_LOW =                    0.11  # Close position if it drops below this AFTER having reached STOP_LOSS_THRESHOLD (accounts for commissions) - protects profits by closing before they erode
    CHECK_INTERVAL =                        3.0  # Check every 4 seconds
    ENABLE_FAST_RISER_REDUCE: bool =        True  # Enable fast riser logic: DOUBLE when k_3m < 70 and quick jump (momentum), REDUCE when k_3m > 70 and overbought (take profit)
    LEADERBOARD_FILTER:bool =               True

    VALIDATE_REFRESH: int =                 2 #seconds
    VERBOSE: bool =                         True
    VERBOSE2: bool =                        False
    VERBOSE_STOPS: bool =                   False
    VERBOSE_TIMER: bool =                   False
    VERBOSE_FETCH_LOGGING: bool =           False
    DEBUG: bool =                           False
    REDUCTION_COOLDOWN_SECONDS            = 90.0
    AUGMENTATION_COOLDOWN_SECONDS         = 480.0

    PERSIST                              =  0.0 #minutes to stay in tradeable_keys after deletion
    # STOP_ORDERS_FULL_UPDATE:         int =   40  #STOP ORDERS FULL UPDATE
    # THREE_MIN_STRATEGY: Dict[str, Any] = field(default_factory=lambda: {
    #     'ENABLED': True,
    #     'MARK_PRICE_INTEGRATION': True,
    #     'ENHANCED_3M_INDICATORS': True,
    #     'ATR_VOLATILITY_SCORING': True,
    #     'ZSCORE_THRESHOLDS': {
    #         'EXTREME_VOLATILITY': 2.5,
    #         'HIGH_VOLATILITY': 1.5,
    #         'LOW_VOLATILITY': -1.5 } })
    def __post_init__(self):
        self._INSTANCES.add(self)
        self._apply_mode(self._resolve_initial_mode())
        if self.SANDBOX_MODE:
            for sa in self.SANDBOX_ACCOUNTS:
                if sa not in self.ACCOUNT_KEYS: self.ACCOUNT_KEYS.append(sa)

    def _resolve_initial_mode(self) -> str:
        if self.EXTREME_MODE and not self.LIGHT_MODE:
            return "EXTREME_MODE"
        if self.LIGHT_MODE and not self.EXTREME_MODE:
            return "LIGHT_MODE"
        if self.MARKET_MODE in {"EXTREME_MODE","LIGHT_MODE","NORMAL_MODE"}:
            return self.MARKET_MODE
        return self._CURRENT_MARKET_MODE

    def _apply_mode(self, mode: str):#emergency mode
        light = {
            # "START_POSITION_SIZE": 11.0,#emergency mode
            # "MAX_POSITION_SIZE": 250.0,
            # "MAX_ORDER_VALUE": 110.0,
            # "MAX_ORDER_VALUE_MEN": 180.0,#emergency mode
            # "MAX_ORDER_VALUE_FIN": 200.0,#emergency mode
            # "HIGH_GAIN_AUGMENTATION_MIN_SIZE": 200,
            # "REDUCTION_COOLDOWN_SECONDS": 480.0,
            # "AUGMENTATION_COOLDOWN_SECONDS": 480.0,  # REDUCED: From 480 to 240 for faster reactions
            # "MIN_GAIN_TO_BUY_AGGRESSIVELY": 1.2,#emergency mode
            # "MAX_POSITION_SIZE_MEN": 800.0,#emergency mode
            # "MAX_POSITION_SIZE_FIN": 900.0#emergency mode
            "START_POSITION_SIZE": 6.0,#emergency mode
            "MAX_POSITION_SIZE": 155.0,
            "MAX_ORDER_VALUE": 80.0,
            "MAX_ORDER_VALUE_MEN": 440.0,
            "MAX_ORDER_VALUE_FIN": 18.0,
            "HIGH_GAIN_AUGMENTATION_MIN_SIZE": 100,
            "REDUCTION_COOLDOWN_SECONDS": 30.0,
            "AUGMENTATION_COOLDOWN_SECONDS": 660.0,
            "MIN_GAIN_TO_BUY_AGGRESSIVELY": 1.4,
            "MAX_POSITION_SIZE_MEN": 220.0,
            "MAX_POSITION_SIZE_FIN": 120.0
        }
        base = {
            # "START_POSITION_SIZE": 15.0,#emergency mode
            # "MAX_POSITION_SIZE": 220.0,
            # "MAX_ORDER_VALUE": 40.0,
            # "MAX_ORDER_VALUE_MEN": 50.0,#emergency mode
            # "MAX_ORDER_VALUE_FIN": 60.0,#emergency mode
            # "LIMIT_HIGH_GAIN_AUGMENTATION": 200,
            # "REDUCTION_COOLDOWN_SECONDS": 240.0,
            # "AUGMENTATION_COOLDOWN_SECONDS": 240.0,
            # "MIN_GAIN_TO_BUY_AGGRESSIVELY": 0.8,#emergency mode
            # "MAX_POSITION_SIZE_MEN": 160.0,#emergency mode
            # "MAX_POSITION_SIZE_FIN": 300.0#emergency mode
            "START_POSITION_SIZE": 8.0,
            "MAX_POSITION_SIZE": 880.0,
            "MAX_ORDER_VALUE": 92,
            "MAX_ORDER_VALUE_MEN": 121.0,
            "MAX_ORDER_VALUE_FIN": 175.0,
            "HIGH_GAIN_AUGMENTATION_MIN_SIZE": 100,
            "REDUCTION_COOLDOWN_SECONDS": 30.0,
            "AUGMENTATION_COOLDOWN_SECONDS": 160.0,
            "MIN_GAIN_TO_BUY_AGGRESSIVELY": 0.5,
            "MAX_POSITION_SIZE_MEN": 860.0,
            "MAX_POSITION_SIZE_FIN": 620.0
        }
        extreme = {
            "START_POSITION_SIZE": 70.0,
            "MAX_POSITION_SIZE": 2800.0,
            "MAX_ORDER_VALUE": 180.0,
            "MAX_ORDER_VALUE_MEN": 500.0,
            "MAX_ORDER_VALUE_FIN": 1200.0,
            "HIGH_GAIN_AUGMENTATION_MIN_SIZE": 100,
            "REDUCTION_COOLDOWN_SECONDS": 30.0,
            "AUGMENTATION_COOLDOWN_SECONDS": 190.0,  # REDUCED: From 240 to 120 for faster reactions
            "MIN_GAIN_TO_BUY_AGGRESSIVELY": 1.4,
            "MAX_POSITION_SIZE_MEN": 3200.0,
            "MAX_POSITION_SIZE_FIN": 4000.0
        }

        for key, value in base.items():
            setattr(self, key, value)
        if mode == "EXTREME_MODE":
            for key, value in extreme.items():
                setattr(self, key, value)
        elif mode == "LIGHT_MODE":
            for key, value in light.items():
                setattr(self, key, value)
        self.MARKET_MODE = mode
        self.EXTREME_MODE = mode == "EXTREME_MODE"
        self.LIGHT_MODE = mode == "LIGHT_MODE"
        Config._CURRENT_MARKET_MODE = mode

    @classmethod
    def set_market_mode(cls, mode: str):
        if mode not in {"NORMAL_MODE","EXTREME_MODE","LIGHT_MODE"}:
            mode = "NORMAL_MODE"
        cls._CURRENT_MARKET_MODE = mode
        for instance in list(cls._INSTANCES):
            instance._apply_mode(mode)

    def get_account_setting(self, account_key: str, setting_name: str):
        return self.ACCOUNT_OVERRIDES.get(account_key, {}).get(setting_name, getattr(self, setting_name, None))
    def get_stop_indicator_keys(self, account_key: str) -> tuple:
        tf = self.get_account_setting(account_key, 'STOP_TIMEFRAME')
        return (f'dc_low_{tf}', f'dc_high_{tf}', f'dc_low4_{tf}', f'dc_high4_{tf}', f'dc_basis_{tf}')
    MARKET_MODE_FILE: str = "data/market_mode.json"
    async def save_market_mode(self):
        import json as _json
        path = self.BASE_PATH / self.MARKET_MODE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, 'w') as f:
            await f.write(_json.dumps({'market_mode': self.MARKET_MODE, 'updated_at': datetime.now(timezone.utc).isoformat()}))
    async def load_market_mode(self):
        import json as _json
        path = self.BASE_PATH / self.MARKET_MODE_FILE
        try:
            async with aiofiles.open(path, 'r') as f:
                data = _json.loads(await f.read())
                mode = data.get('market_mode', 'NORMAL_MODE')
                if mode in {"NORMAL_MODE", "EXTREME_MODE", "LIGHT_MODE"}:
                    self.set_market_mode(mode)
        except (FileNotFoundError, _json.JSONDecodeError):
            pass
    PAPER_TRADING: bool =                   False  # Enable paper trading mode - records trades without executing
    PAPER_TRADING_QUICK: bool =             False  # Enable paper trading mode - records trades without executing

    SLEEP_TIME_PER_TASKS: int =             3 #FETCH and leaderboards
    SLEEP_TIME_PROC_ACCT: float =           5 #Check symbols - 100 times per minute minimum
    DIRECT_HIGH_GAIN_COOLDOWN_SECONDS =     15
    # =============================================================================
    # TIMING CONFIGURATION - ALL TIMING SETTINGS IN ONE PLACE FOR EASY TWEAKING
    # =============================================================================
    MAX_CONCURRENT_ORDERS: float =          186
    # MAX_CONCURRENT_TASKS: float =           75 #SEMAPHORE (under 40 limit for API throttling)
    FORCE_REFRESH_SECONDS: float =          16
    MAX_MARKET_DATA_FILE_AGE_SECONDS:float= 1200
    MARKET_DATA_REFRESH_INTERVAL_SECONDS: float = 45.0
    # --- CACHE TIMING (seconds) ---
    # POSITION_CACHE_TTL: int =               5  # Position data cache duration
    ORDER_CACHE_TTL: int =                  10  # Open orders cache duration
    POSITIONS_SNAPSHOT_MAX_AGE: float =     6.0  # Max age (seconds) accepted for RPC snapshots
    LOCAL_DATA_MAX_AGE: float =             200.0  # Max age (seconds) accepted for file-based fallbacks
    INDICATOR_MAX_AGE_SECONDS =             200.0
    STALE_WARNING_INTERVAL_SECONDS: float = 30.0
    POSITION_SAVE_INTERVAL: float =         6.0
    INDICATORS_SAVE_INTERVAL_SECONDS: float = 10.0
    POSITION_REDIS_REFRESH_INTERVAL: float = 6.0
    LADDER_AUTO_SAVE_SECONDS: float =       60.0
    MONITOR_REDUCTION_STALE_THRESHOLD: float = 180.0
    POSITION_STALE_THRESHOLD_SECONDS: float = 60.0
    # MARK_PRICE_GUARD_INTERVAL: float =      1.0  # Seconds between websocket staleness checks
    MARK_PRICE_MAX_STALENESS: float =       2  # Maximum acceptable age of cached mark price
    # PRICE_FALLBACK_INTERVAL: float =        1.0  # Interval for REST/Redis mark-price fallback loop
    EZ_INDICATORS_SHUTDOWN_CMD: Optional[str] = None  # shell command to stop ez_indicators gracefully
    EZ_INDICATORS_START_CMD: Optional[str] = None  # shell command to (re)start ez_indicators
    EZ_INDICATORS_RESTART_COOLDOWN: float = 60.0  # Minimum seconds between ez_indicators restart attempts
    EZ_INDICATORS_CMD_TIMEOUT: float =      10.0  # Timeout for ez_indicators start/stop commands
    POSITIONS_SERVICE_START_CMD: Optional[str] = None  # shell command to start ez_positions_service
    POSITIONS_SERVICE_HEALTH_TIMEOUT: float = 4.0  # Seconds to wait for RPC ping
    POSITIONS_SERVICE_HEALTH_RETRIES: int = 3  # Number of retries before giving up on RPC ping
    #STOP_ORDERS_CACHE_TTL: int =            15  # Stop orders cache duration
    # INDICATOR_CACHE_TTL: int =              10   # Indicator cache duration (seconds, keep data sub-second)

    # --- API RATE LIMITING ---
    # POSITION_RATE_LIMIT_SECONDS: int =      2  # Min seconds between position API calls (4 per minute)
    CIRCUIT_BREAKER_COOLDOWN: int =         120 # 5 minutes cooldown after rate limit

    # --- PROCESSING INTERVALS (seconds) ---
    # FETCH_POSITIONS_SLEEP: int =            2   # Sleep between position fetches
    # FETCH_POSITIONS_ERROR_SLEEP: int =      30  # Sleep after fetch error
    # PRICE_CACHE_SAVE_INTERVAL: int =        18  # Price cache save interval
    # PRICE_CACHE_LOAD_INTERVAL: int =        10  # Price cache load interval
    # PNL_DETERIORATION_INTERVAL: int =       3600 # PnL deterioration check (1 hour)
    # PNL_DETERIORATION_ERROR_SLEEP: int =    300 # Sleep after PnL error
    # PREV_GAIN_UPDATE_INTERVAL: int =        300 # Prev gain update (6 minutes)
    # PREV_GAIN_ERROR_SLEEP: int =            60  # Sleep after prev gain error
    # GATEWAY_REDIS_MONITOR_INTERVAL: int =   30  # Gateway Redis monitor interval
    # GATEWAY_REDIS_ERROR_SLEEP: int =        10  # Sleep after gateway Redis error

    # --- ORDER EXECUTION TIMING ---
    # ORDER_CANCEL_VERIFY_SLEEP: int =        0.5 # Sleep between order cancellation attempts
    # ORDER_CANCEL_RETRY_SLEEP: int =         1   # Sleep after order cancellation
    # MAKER_ORDER_SLEEP: int =                0.2   # Sleep after maker order placement
    # ORDER_VERIFICATION_SLEEP: int =         2   # Sleep for order verification
    # STOP_TRIGGER_GAP: float =               0.002  # Buffer between script stop and exchange STOP_MARKET
    # STOP_SYNC_COOLDOWN_SECONDS: int =       12    # Minimum seconds between exchange stop syncs per position
    # --- WEBSOCKET TIMING ---
    # WEBSOCKET_RECONNECT_SLEEP: int =        5   # WebSocket reconnection delay
    # WEBSOCKET_KEEPALIVE_TIMEOUT: int =      30  # WebSocket keepalive timeout

    # --- REDIS TIMING ---
    # REDIS_CONNECT_TIMEOUT: int =            15  # Redis connection timeout
    # REDIS_SOCKET_TIMEOUT: int =             3   # Redis socket timeout

    # --- API TIMEOUTS ---
    # POSITION_API_TIMEOUT: int =             20  # Position API timeout
    # POSITION_FETCH_TIMEOUT: int =           30  # Position fetch timeout
    # ORDER_API_TIMEOUT: int =                30  # Order API timeout

    # --- COOLDOWNS (seconds) ---

    # COOLDOWN_SECS: int =                    5  # Process position cooldown
    REENTER_SAVE_DEBOUNCE_SECONDS: int =    50   # Reenter save debounce

    # --- OTHER TIMING ---
    #FORCE_REFRESH_SECONDS: int =            600
    #FORCE_SYMBOL_REFRESH_SECONDS: int =     600
    LADDER_TTL_MINUTES: int =               24*60  # Ladder order TTL
    # SAVE_INTERVAL: int =                    60
    # RETRY_DELAY: int =                      5
    REDIS_EXPIRY_SECONDS: int =             180
    MIN_USD_DELTA_CONFIRM: float =          1.0

    FAPI_BASE_URL: str = "https://fapi.binance.com/fapi/v1"
    FSTREAM_WS_URL_BASE: str = "wss://fstream.binance.com/stream"
    WS_URL: str = "wss://fstream.binance.com/ws"
    USE_WS_3M: bool = True          # If True, consume 3m klines directly from Binance WS (no local resampling)
    REDIS_1M_TAIL: int = 1500        # Number of most recent 1m bars to keep/publish in Redis payload
    # EXTERNAL_3M_PRODUCER: bool = True  # If True, ez_prices skips internal 3m generation (handled by WS or external)
    # MARK_PRICES_ONLY: bool = True    # If True, ez_mark_prices only publishes mark prices (no 1m Redis publish), but still writes 1m JSON
    # FETCH_1M_FROM_API: bool = False
    # FETCH_3M_FROM_API: bool = True
    # ENABLE_WS_BACKUP_CONSOLIDATION: bool = True  # Enable backup consolidation in ez_prices_ws
    INDICATORS_DATA_CACHE_SIZE         =    2048
    # ORDER_WORKERS                       =   20
    LOG_MAX_BYTES = 1024*1024*20
    LOG_BACKUP_COUNT = 30
    # ENABLE_CONVICTION: bool =               True
    USE_INDICATOR_SNAPSHOT: bool =          True
    POSITION_REFRESH_MIN_INTERVAL: int =    1 #\seconds
    #SINGLE_FETCH_COOLDOWN: int =            15

    # PERIODIC_STOP_ORDERS_ENABLED: bool =    False
    # Enable periodic stop order management
    #PERIODIC_STOP_ORDERS_INTERVAL: int =    20   # Run every 20 seconds (check for missing stops)    SYMBOL_TRACKER_ENABLED: bool = False
    # RATE_LIMIT_DUPLICATE_FILTER_ENABLED: bool = True
    # MAKER_ORDER_CONFIG: Dict[str, Any] = field(default_factory=lambda: {
    # "TIMEOUT_SECONDS": 30, })

    def _resolve_base_path() -> Path:
        env_base = os.environ.get("BASE_PATH")
        if env_base:
            base = Path(env_base).expanduser()
            candidates = [base]
            if base.name.lower() != "binance":
                candidates.append(base / "binance")
            for candidate in candidates:
                try:
                    if candidate.exists():
                        return candidate
                except Exception:
                    pass
            return candidates[0]
        system_name = platform.system()
        if system_name == "Darwin":
            return Path("/Users/niels/Documents/binance")
        if system_name == "Linux":
            return Path("/home/niels/binance")
        return Path.home() / "Documents" / "binance"

    # LADDER_LEVELS: int = 6        # Number of post-exit ladder orders
    # LADDER_SPLIT: List[float] = field(default_factory=lambda: [0.33, 0.33, 0.34])
    # MIN_LADDER_POSITION_SIZE: float = 0.5 * START_POSITION_SIZE
    LOG_DIR: Path = Path.home() / "logs"
    LOG_FILE_EZ_MANAGE: Path = LOG_DIR / "ez_manage.log"
    LOG_FILE_EZ_PRICES: Path = LOG_DIR / "ez_prices.log"
    LOG_FILE_EZ_BACKUP: Path = LOG_DIR / "ez_backup.log"
    LOG_FILE_EZ_PRICES_WS: Path = LOG_DIR / "ez_prices_ws.log"
    LOG_FILE_EZ_MARK_PRICES: Path = LOG_DIR / "ez_mark_prices.log"
    LOG_FILE_EZ_INDICATORS: Path = LOG_DIR / "ez_indicators.log"
    LOG_FILE_EZ_CROSSES: Path = LOG_DIR / "ez_crosses.log"
    BASE_PATH: Path = _resolve_base_path()
    DATA_DIR: Path = BASE_PATH / "data"
    KLINES_CACHE_DIR: Path = BASE_PATH / "klines_cache"
    RANKINGS_DIR: Path = BASE_PATH / "rankings"
    PLOTS_DIR: Path = BASE_PATH / "plots"
    HD_ROOT: Path = Path("/Volumes/SSD2T")
    BACKUP_KLINES_CACHE: Path = HD_ROOT / "backup/klines_cache"
    CONSOLIDATED_KLINES_CACHE: Path = HD_ROOT / "backup/klines_cache_consolidated"
    # BACKUP_DATA: Path = HD_ROOT / "backups/data"
    # BACKUP_PLOTS: Path = HD_ROOT / "backups/plots"

    SYMBOLS_FILE: Path = BASE_PATH / "symbols.json"
    SYMBOLS: Path =     BASE_PATH / "symbols.json"
    SYMBOLS_ACTIVE_FILE: Path = BASE_PATH / "symbols_active.json"
    SYMBOLS_ANG_LONG: Path = BASE_PATH / "symbols_ang_long.json"
    SYMBOLS_INF_LONG: Path = BASE_PATH / "symbols_inf_long.json"
    SYMBOLS_INF_SHORT: Path = BASE_PATH / "symbols_inf_short.json"
    SYMBOLS_FLZ: Path = BASE_PATH / "symbols_flz.json"
    SYMBOLS_MEN: Path = BASE_PATH / "symbols_men.json"
    SYMBOLS_ANG_SHORT: Path = BASE_PATH / "symbols_ang_short.json"
    SYMBOLS_FIN: Path = BASE_PATH / "symbols_fin.json"
    SYMBOLS_ACTIVE: Path = BASE_PATH / "symbols_active.json"
    PREVIOUS_SYMBOLS_MEN: Path = BASE_PATH / "previous_symbols_men.json"
    PREVIOUS_SYMBOLS_FIN: Path = BASE_PATH / "previous_symbols_fin.json"
    PREVIOUS_SYMBOLS_FLZ: Path = BASE_PATH / "previous_symbols_flz.json"
    DATA_READY_FLAG_FILE: Path = BASE_PATH / "data_ready.flag"

    LIVE_USDC_PAIRS_FILE: Path = BASE_PATH / "live_usdc_pairs.json"
    PRICE_CACHE_FILE: Path = BASE_PATH / "price_cache_1.json"
    PRICE_CACHE_FILE_2: Path = BASE_PATH / "price_cache_2.json"
    PRICE_CACHE_FILE_3: Path = BASE_PATH / "price_cache_3.json"
    MIN_QTY_FILE: Path = BASE_PATH / "min_qty.json"
    MULT_FILE: Path = BASE_PATH / "multipliers.json"
    SYMBOL_CONFIGS_FILE: Path = BASE_PATH / "symbol_configs.json"
    RANKING_RESULTS_FILE: Path = BASE_PATH / "ranking_results.json"
    TRADEABLE_KEYS : Path = BASE_PATH / "tradeable_keys.json"

    WINNERS_20_FILE: Path = DATA_DIR / "winners_20_final_score"
    LOSERS_20_FILE: Path = DATA_DIR / "losers_20_final_score"
    WINNERS_15M_FILE: Path = DATA_DIR / "winners_30r"
    LOSERS_15M_FILE: Path = DATA_DIR / "losers_30r"
    CROSSES_FILE: Path = DATA_DIR / "last_events.json"
    SIGNALS_FILE: Path = BASE_PATH / "signals.json"
    BAND_FILE: Path = DATA_DIR / "band_score.json"
    FINAL_SCORE_FILE: Path = DATA_DIR / "final_score_norm.json"
    PROX_FILE: Path = DATA_DIR / "prox_score.json"
    SCORE_RANGES_FILE: Path = DATA_DIR / "score_ranges.json"
    RANKING_POINTS_FILE: Path = DATA_DIR / "ranking_points.json"
    LAST_EVENTS_FILE: Path = DATA_DIR / "last_events.json"
    indicators_filepath: Path = DATA_DIR /"latest_market_data.json"
    LATEST_MARKET_DATA_FILE: Path = DATA_DIR / "latest_market_data.json"
    GRACEFUL_EXIT_FILE_TEMPLATE: Path = BASE_PATH / "graceful_exit_{account_key}.json"
    LOG_FILE_EZ_RANKINGS: Path = Path.home() / "logs" / "ez_rankings.log"

    # --- KLINE SETTINGS ---
    # TARGET_BAR_COUNT: int = 1500  # Increased from 1500 to preserve more historical data
    # KLINE_API_FETCH_LIMIT: int = 800
    # KLINES_CACHE_MAX_ITEMS: int = 400  # Reduced to prevent file handle accumulation
    # KLINE_STALENESS_BARS: Dict[str, int] = field(default_factory=lambda: {
        # "3m": 5, "15m": 4, "1h": 3, "4h": 2, "D": 2
    # })
    # DEPRECATED: PRICE_STALENESS_THRESHOLD is no longer used - replaced with Redis Pub/Sub-driven timestamp extraction
    # PRICE_STALENESS_THRESHOLD: int = 60
    # MARKET_DATA_FILES_KEEP: float = 300.0
    MAX_MEMORY_GB: int = 8
    REACTIVE_MODE: bool = False

    # --- RETRY SETTINGS ---
    # MAX_RETRIES: int = 3

    # --- WEIGHTS ---
    # LINEARITY_WEIGHT: float = 0.7
    # SLOPE_WEIGHT: float = 0.3

    # --- TIME WINDOWS ---
    # ORPHAN_STOP_LEVEL_THRESHOLD: timedelta = field(default_factory=lambda: timedelta(days=2))
    REENTER_ORPHAN_THRESHOLD: timedelta = field(default_factory=lambda: timedelta(days=70))

    # --- ANALYZER SETTINGS ---
    # ANALYZER_DAYS_BACK: int = 30  # How many days back the analyzer should look
    # ANALYZER_MIN_TRADES_FOR_ANALYSIS: int = 5  # Minimum trades needed for meaningful analysis
    # ANALYZER_SHOW_TOP_N_TRADES: int = 5  # Number of top/bottom trades to show

    ACCOUNT_SIDE_MAPPING = {
        'ang': ['LONG', 'SHORT'],
        'inf': ['LONG', 'SHORT'],
        'men': ['LONG', 'SHORT'],
        'flz': ['LONG', 'SHORT'],
        'fin': ['LONG', 'SHORT']}

    # --- REDIS SETTINGS ---
    REDIS_HOST: str = 'localhost'
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_CHANNEL_SIGNALS: str = "signals_channel"
    REDIS_KEY_MARKET_DATA: str = "latest_market_data"  # Redis key for market data (not a channel)
    WORKER_INSTANCE_ID: int = int(os.getenv("WORKER_INSTANCE_ID", "0"))  # 0-based instance ID for symbol splitting (0, 1, 2, ...)
    WORKER_TOTAL_INSTANCES: int = int(os.getenv("WORKER_TOTAL_INSTANCES", "1"))  # Total number of worker instances (1=single, 2=dual, etc.)
    ENABLE_MULTI_INSTANCE_ON_MACBOOK: bool = False  # If False, forces single instance on macbook even if WORKER_TOTAL_INSTANCES > 1

    # --- CONSTANTS ---
    KLINE_COLUMNS: List[str] = field(default_factory=lambda: ['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    # ALL_TIMEFRAMES: List[str] = field(default_factory=lambda: ["3m", "15m", "1h", "4h", "D"])
    ACCOUNT_KEYS: List[str] = field(default_factory=lambda: ["ang", "inf", "men", "flz", "fin"])

    # Volatility normalization
    ATR_LONG_WINDOW = 100
    # ATR_Z_WINDOW = 200

    # Sizing / vol punishments
    # LOW_REL_VOL_THRESHOLD = 0.65
    # HIGH_REL_VOL_THRESHOLD = 1.20
    # LOW_VOL_STRONG_PENALTY = 40.0
    # LOW_VOL_MILD_PENALTY = 20.0
    # VOL_SPIKE_BONUS = 15.0

    # Trailing stop
    # TRAIL_ATR_MULTIPLIER = 1.2

    # --- SSL Context ---
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    # --- RANKINGS SPECIFIC SETTINGS ---
    MAX_FILES: int = 20
    DAYS_PLOT: int = 20  # days to save plots
    # THROTTLER_RATE_LIMIT: int = 500
    BINANCE_API_BASE: str = "https://fapi.binance.com"

    # =============================================================================
    # CENTRALIZED SEMAPHORE & THROTTLER CONTROLS
    # =============================================================================

    # --- EZ_KLINES CONTROLS (gateway, server, macbook) ---
    EZ_KLINES_API_MAX_PER_SECOND: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 22, "server": 20, "macbook": 22
    })
    EZ_KLINES_API_MAX_PER_MINUTE: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 200, "server": 200, "macbook": 300
    })
    EZ_KLINES_MAX_CONCURRENT: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 10, "server": 10, "macbook": 15
    })
    EZ_KLINES_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 10, "server": 10, "macbook": 15
    })

    # # --- EZ_POSITIONS CONTROLS (gateway, server, macbook) ---
    # EZ_POSITIONS_API_RATE_LIMIT: Dict[str, float] = field(default_factory=lambda: {
    #     "gateway": 27.0, "server": 27.0, "macbook": 92.0
    # })
    # EZ_POSITIONS_MARK_PRICE_WS_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
    #     "gateway": 14, "server": 14, "macbook": 22
    # })

    # # EZ_POSITIONS API CALL LIMITS (CRITICAL: prevent rate limits)
    # EZ_POSITIONS_API_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
    #     "gateway": 14, "server": 14, "macbook": 140#NE concurrent API call max!
    # })
    # EZ_POSITIONS_API_FETCH_COOLDOWN: int = 10  # Minimum seconds between API fetches per account
    # EZ_POSITIONS_THROTTLER_RATE: Dict[str, int] = field(default_factory=lambda: {
    #     "gateway": 95, "server": 95, "macbook": 155#000000000000000000
    # })
    # EZ_POSITIONS_CONNECTOR_LIMIT: Dict[str, int] = field(default_factory=lambda: {
    #     "gateway": 60, "server": 60, "macbook": 60
    # })
    # EZ_POSITIONS_LIMIT_PER_HOST: Dict[str, int] = field(default_factory=lambda: {
    #     "gateway": 60, "server": 60, "macbook": 65
    # })

    # --- EZ_MANAGE CONTROLS (gateway, macbook) ---
    EZ_MANAGE_WS_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 140, "macbook": 140
    })
    EZ_MANAGE_RATE_LIMIT_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 35, "macbook": 50
    })
    EZ_MANAGE_MAKER_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 45, "macbook": 65
    })
    EZ_MANAGE_THROTTLER_RATE: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 200, "macbook": 250  # INCREASED: From 50/125 to 200/250 for faster position processing
    })
    EZ_MANAGE_CONCURRENCY_LIMIT: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 190, "macbook": 180
    })
    # --- EZ_PRICEWS CONTROLS (gateway, server, macbook) ---
    EZ_PRICEWS_API_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 2, "server": 2, "macbook": 2
    })
    EZ_PRICEWS_CONNECTOR_LIMIT: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 5, "server": 5, "macbook": 5
    })
    EZ_PRICEWS_LIMIT_PER_HOST: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 3, "server": 3, "macbook": 3
    })
    EZ_PRICEWS_API_DELAY: Dict[str, float] = field(default_factory=lambda: {
        "gateway": 0.5, "server": 0.5, "macbook": 0.5
    })

    # --- EZ_INDICATORS CONTROLS (gateway, macbook) ---
    # EZ_INDICATORS_FILE_READ_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
        # "gateway": 10000, "macbook": 5000
    # })
    # EZ_INDICATORS_STAGE_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
        # "gateway": 15000, "macbook": 7500
    # })
    # EZ_INDICATORS_UPDATE_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
        # "gateway": 5000, "macbook": 2500
    # })
    # EZ_INDICATORS_THROTTLER_RATE: Dict[str, int] = field(default_factory=lambda: {
        # "gateway": 2000, "macbook": 5000
    # })

    # --- EZ_RANKINGS CONTROLS (gateway, macbook) ---
    EZ_RANKINGS_THROTTLER_RATE: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 500, "macbook": 250
    })

    # --- EZ_MARK_PRICES CONTROLS (gateway, server, macbook) ---
    EZ_MARK_PRICES_API_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 35, "server": 35, "macbook": 35
    })
    EZ_MARK_PRICES_STARTUP_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 30, "server": 30, "macbook": 30
    })

    # --- EZ_PRICES CONTROLS (already configured above) ---
    # FILE_IO_CONCURRENCY, API_CONCURRENCY, fapi_semaphore in ResamplingAndGapFillEngine.__init__
    EZ_PRICES_FILE_IO_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 100, "server": 100, "macbook": 50
    })
    EZ_PRICES_API_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 3, "server": 3, "macbook": 3
    })
    EZ_PRICES_FAPI_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 2, "server": 2, "macbook": 2
    })
    EZ_PRICES_API_DELAY: Dict[str, float] = field(default_factory=lambda: {
        "gateway": 0.5, "server": 0.5, "macbook": 0.5
    })
    EZ_PRICES_API_SLEEP_AFTER: Dict[str, float] = field(default_factory=lambda: {
        "gateway": 0.3, "server": 0.3, "macbook": 0.3
    })
    EZ_PRICES_LIMIT_PER_HOST: Dict[str, int] = field(default_factory=lambda: {
        "gateway": 10, "server": 10, "macbook": 5
    })
    # EZ_PRICES_SYNC_KEY_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {
        # "gateway": 160, "server": 160, "macbook": 80
    # })

    # --- RANKINGS TIMING SETTINGS ---
    DATA_READY_TIMEOUT_SECONDS: int = 20  # 5 minutes timeout for data_ready.flag
    TASK_STAGGER_SECONDS: int = 15  # Stagger between starting background tasks
    # MAIN_LOOP_SLEEP_SECONDS: int = 3600  # Main loop sleep interval (1 hour)
    LOG_INTERVAL_SECONDS: int = 10  # Log interval for waiting operations

    # --- LOOP INTERVALS ---
    # MEMORY_CHECK_INTERVAL_SECONDS: int = 60  # Memory monitoring interval
    # INITIAL_SETUP_WAIT_SECONDS: int = 30  # Wait for initial setup
    RANKING_LOOP_SLEEP_SECONDS: int = 180  # Ranking loop sleep (3 minutes)
    # SIGNALS_LOOP_SLEEP_SECONDS: int = 300  # Signals loop sleep (5 minutes)
    # PLOT_LOOP_SLEEP_SECONDS: int = 3600  # Plot loop sleep (1 hour)
    # REDIS_HEALTH_CHECK_INTERVAL_SECONDS: int = 120  # Redis health check interval
    MEMORY_MONITOR_SLEEP_SECONDS: int = 60  # Memory monitor loop sleep
    PLOT_LOOP_INTERVAL_SECONDS: int = 1800  # Plot loop interval
    SIGNALS_LOOP_INTERVAL_SECONDS: int = 300  # Signals loop interval
    ERROR_RECOVERY_SLEEP_SECONDS: int = 60  # Sleep after errors
    # INITIAL_WAIT_SECONDS: int = 30  # Initial wait for setup

    # REQUIRED_INDICATORS: List[str] = field(default_factory=lambda: list(REQUIRED_INDICATORS))
    # FINAL_SCORING_INDICATORS: List[str] = field(default_factory=lambda: list(_DEFAULT_FINAL_SCORING_INDICATORS))
    # CORE_TECHNICAL_INDICATORS: List[str] = field(default_factory=lambda: list(_DEFAULT_CORE_TECHNICAL_INDICATORS))

# REQUIRED_INDICATORS: List[str] = [#v1
#     # === RANKING & SCORING (MANDATORY) from dicts ===
#     '0ranking_points', '0ranking_points_local', '0ranking_points_global', '0band_score',
#     '0final_score_norm', '0prox_norm', '0market_sentiment_score', '0market_sentiment_local', '0sentiment_classification', '0sentiment_strength', '0is_top_sentiment', '0is_bottom_sentiment', '0sentiment_rank',
#     # === BASIC PRICE DATA (MANDATORY) current_pricer == redis mark_price  ===
#     'current_price', 'prev_price', 'timestamp',
#     # === 3M TIMEFRAME FOR 1M TIMEFRAME (sma_200_1m = sma_70_3m)===
#     'sma_crossover_1m', 'sma_crossunder_1m',
#     'sma_200_1m', 'sma_200_1m_prev',
#     # === 3M TIMEFRAME ===
#     'lr_trend_3m',
#     'dc_high_3m', 'dc_low_3m', 'dc_basis_3m', 'dc_high_3m_prev', 'dc_low_3m_prev',
#     'dc_high_3m_ant', 'dc_low_3m_ant', 'dc_basis_3m_ant', 'dc_high4_3m', 'dc_low4_3m',
#     #'relative_volatility_3m', #'atr_zscore_3m', 'relvol_z_3m',
#     'wt1_3m', 'wt2_3m', 'wt_signal_3m', 'wt_score_3m',
#     'stoch_k_3m', 'stoch_d_3m', 'k_3m_prev', 'd_3m_prev',
#     'stoch_crossover_3m', 'stoch_crossunder_3m',
#     'dc_basis_crossover_3m', 'dc_basis_crossunder_3m',
#     'dc_high_crossover_3m', 'dc_low_crossunder_3m','dc_high_crossunder_3m','dc_low_crossover_3m',
#     #'crossover_price_3m', 'crossover_price_previous_3m', 'crossunder_price_3m', 'crossunder_price_previous_3m',

#     'ha_3m', 'ha_3m_prev',
#     'atr_3m', 'atr_3m_prev', 'atr_long_3m',
#     'relative_volume_3m',
#     'high_3m', 'low_3m', 'high_3m_prev', 'low_3m_prev', 'high_1h', 'low_1h', 'high_1h_prev', 'low_1h_prev',
#     'close_3m', 'prev_close_3m', 'timestamp_3m',
#     'mfi_3m', 'mfi_trend_3m', 'next_peak_bar_3m', 'next_peak_price_3m', 'next_trough_bar_3m', 'next_trough_price_3m',

#     # === 15M TIMEFRAME ===
#     'lr_trend_15m',
#     'dc_high_15m', 'dc_low_15m', 'dc_basis_15m', 'dc_high_15m_prev', 'dc_low_15m_prev',
#     'dc_high_15m_ant', 'dc_low_15m_ant', 'dc_basis_15m_ant', 'dc_high4_15m', 'dc_low4_15m',
#     'stoch_k_15m', 'stoch_d_15m', 'stoch_k_15m_prev', 'd_15m_prev',
#     'stoch_crossover_15m', 'stoch_crossunder_15m',
#     'sma_crossover_15m', 'sma_crossunder_15m',
#     'dc_basis_crossover_15m', 'dc_basis_crossunder_15m',
#     'dc_high_crossover_15m', 'dc_low_crossunder_15m','dc_high_crossunder_15m','dc_low_crossover_15m',
#     #'crossover_price_15m', 'crossover_price_previous_15m', 'crossunder_price_15m', 'crossunder_price_previous_15m',
#     #'relative_volatility_15m', #'atr_zscore_15m', 'relvol_z_15m',
#     'wt1_15m', 'wt2_15m', 'wt_signal_15m', 'wt_score_15m',
#     'ha_15m', 'ha_15m_prev',
#     'atr_15m', 'atr_15m_prev', 'atr_long_15m',
#     'relative_volume_15m',
#     'high_15m', 'high_15m_prev', 'low_15m', 'low_15m_prev', 'timestamp_15m',
#     'sma_200_15m', 'sma_200_15m_prev',
#     'mfi_15m', 'mfi_ob_15m', 'mfi_os_15m', 'mfi_trend_15m', 'next_peak_bar_15m', 'next_peak_price_15m', 'next_trough_bar_15m', 'next_trough_price_15m',

#     # === 1H TIMEFRAME ===
#     'lr_trend_1h',
#     'dc_high_1h', 'dc_low_1h', 'dc_basis_1h', 'dc_high_1h_prev', 'dc_low_1h_prev',
#     'dc_high_1h_ant', 'dc_low_1h_ant', 'dc_basis_1h_ant', 'dc_high4_1h', 'dc_low4_1h',
#     'stoch_k_1h', 'stoch_d_1h', 'k_1h_prev', 'd_1h_prev',
#     'stoch_crossover_1h', 'stoch_crossunder_1h',
#     'sma_crossover_1h', 'sma_crossunder_1h',
#     'dc_basis_crossover_1h', 'dc_basis_crossunder_1h',
#     'dc_high_crossover_1h', 'dc_low_crossunder_1h','dc_high_crossunder_1h','dc_low_crossover_1h',
#     #'crossover_price_1h', 'crossover_price_previous_1h', 'crossunder_price_1h', 'crossunder_price_previous_1h',
#     #'relative_volatility_1h', #'atr_zscore_1h',
#     'wt1_1h', 'wt2_1h', 'wt_signal_1h', 'wt_score_1h',
#     'ha_1h',
#     'atr_1h', 'atr_1h_prev', 'atr_long_1h',
#     'sma_200_1h', 'sma_200_1h_prev', 'timestamp_1h',
#     'mfi_1h', 'mfi_ob_1h', 'mfi_os_1h', 'mfi_trend_1h', 'next_peak_bar_1h', 'next_peak_price_1h', 'next_trough_bar_1h', 'next_trough_price_1h',

#     # === 4H TIMEFRAME ===
#     'lr_trend_4h',
#     'slope_close_4h', 'linearity_4h',
#     'dc_high_4h', 'dc_low_4h', 'dc_basis_4h', 'dc_high_4h_prev', 'dc_low_4h_prev',
#     'dc_high_4h_ant', 'dc_low_4h_ant', 'dc_basis_4h_ant', 'dc_high4_4h', 'dc_low4_4h',
#     'stoch_k_4h', 'stoch_d_4h', 'k_4h_prev', 'stoch_d_4h_prev',
#     'stoch_crossover_4h', 'stoch_crossunder_4h',
#     'sma_crossover_4h', 'sma_crossunder_4h',
#     'dc_basis_crossover_4h', 'dc_basis_crossunder_4h',
#     'dc_high_crossover_4h', 'dc_low_crossunder_4h','dc_high_crossunder_4h','dc_low_crossover_4h',
#     #'crossover_price_4h', 'crossover_price_previous_4h', 'crossunder_price_4h', 'crossunder_price_previous_4h',
#     #'relative_volatility_4h', #'atr_zscore_4h',
#     'wt1_4h', 'wt2_4h', 'wt_signal_4h', 'wt_score_4h',
#     'ha_4h',
#     'atr_4h', 'atr_4h_prev', 'atr_long_4h',
#     'sma_200_4h', 'sma_200_4h_prev', 'timestamp_4h',
#     'mfi_4h', 'mfi_trend_4h', 'next_peak_bar_4h', 'next_peak_price_4h', 'next_trough_bar_4h', 'next_trough_price_4h',

#     # === DAILY (D) TIMEFRAME ===
#     'dc_high_D', 'dc_low_D', 'dc_basis_D', 'dc_high_D_prev', 'dc_low_D_prev', 'dc_basis_D_prev',
#     'dc_high_D_ant', 'dc_low_D_ant', 'dc_basis_D_ant',
#     'dc_basis_crossover_D', 'dc_basis_crossunder_D',
#     'dc_high_crossover_D', 'dc_low_crossunder_D',
#     'stoch_k_D', 'stoch_d_D', 'k_D_prev', 'stoch_d_D_prev', 'stoch_crossover_D', 'stoch_crossunder_D',
#     #'crossover_price_D', 'crossover_price_previous_D', 'crossunder_price_D', 'crossunder_price_previous_D',
#     'wt1_D', 'wt2_D', 'wt_signal_D', 'wt_score_D',
#     'ha_D', 'atr_D', 'atr_D_prev', 'atr_long_D', 'sma_200_D', 'sma_200_D_prev',
#     'mfi_D', 'mfi_trend_D', 'rsi_D',# 'relative_volatility_D', #'atr_zscore_D',
#     'high_D', 'low_D', 'timestamp_D','dc_high_crossunder_D','dc_low_crossover_D',

#     # === BOLLINGER BANDS (ALL TIMEFRAMES) ===
#     'bb_upper_3m', 'bb_lower_3m', 'bb_middle_3m',
#     'bb_upper_15m', 'bb_lower_15m', 'bb_middle_15m',
#     'bb_upper_1h', 'bb_lower_1h', 'bb_middle_1h',
#     'bb_upper_4h', 'bb_lower_4h', 'bb_middle_4h',

#     # === EMA (ALL TIMEFRAMES) ===
#     'ema_20_3m', 'ema_50_3m', 'ema_200_3m',
#     'ema_20_15m', 'ema_50_15m', 'ema_200_15m',
#     'ema_20_1h', 'ema_50_1h', 'ema_200_1h',
#     'ema_20_4h', 'ema_50_4h', 'ema_200_4h',

#     # === RSI (ALL TIMEFRAMES) ===
#     'rsi_3m', 'rsi_15m', 'rsi_1h', 'rsi_4h', 'rsi_D',

#     # === MISSING DC INDICATORS ===
#     'dc_high4_D', 'dc_low4_D',

#     # === MISSING PRICE DATA ===
#     'high_4h', 'low_4h', 'high_4h_prev', 'low_4h_prev',

#     # === MISSING CROSSOVER INDICATORS ===
#     'dc_low_crossover_3m', 'dc_low_crossunder_3m',
#     'dc_low_crossover_15m', 'dc_low_crossunder_15m',
#     'dc_low_crossover_1h', 'dc_low_crossunder_1h',
#     'dc_low_crossover_4h', 'dc_low_crossunder_4h',
#     'dc_low_crossover_D', 'dc_low_crossunder_D',
#     'timestamp_D',

#     # === CONVICTION SCORES (CALCULATED IN PIPELINE) ===
#     'zconviction_augment_long', 'zconviction_augment_short', #'conviction_reduce_long', 'conviction_reduce_short',
#     'zconviction_reasons_augment_long', 'zconviction_reasons_augment_short',# 'conviction_reasons_reduce_long', 'conviction_reasons_reduce_short'
# # ]
# REQUIRED_INDICATORS: List[str] = [#v2
#     # === RANKING & SCORING (MANDATORY) ===
#     '0ranking_points', '0ranking_points_global', '0market_sentiment_score', '0market_sentiment_local',
#     '0sentiment_classification', '0sentiment_strength', '0is_top_sentiment', '0is_bottom_sentiment',
#     '0sentiment_rank', '0final_score_norm',

#     # === CONVICTION SCORES (CALCULATED IN PIPELINE) ===
#     'zconviction_augment_long', 'zconviction_augment_short',
#     'zconviction_reasons_augment_long', 'zconviction_reasons_augment_short',

#     # === BASIC PRICE DATA (MANDATORY) ===
#     'current_price', 'prev_price', 'timestamp',
#     'timestamp_3m', 'timestamp_15m', 'timestamp_1h', 'timestamp_4h', 'timestamp_D',

#     # === 1M TIMEFRAME ===
#     'sma_200_1m', 'sma_200_1m_prev',
#     'sma_crossover_1m', 'sma_crossunder_1m',

#     # === 3M TIMEFRAME ===
#     'lr_trend_3m',
#     'dc_high_3m', 'dc_low_3m', 'dc_basis_3m', 'dc_high_3m_prev', 'dc_low_3m_prev',
#     'dc_high_3m_ant', 'dc_low_3m_ant', 'dc_basis_3m_ant', 'dc_high4_3m', 'dc_low4_3m',
#     'wt1_3m', 'wt2_3m', 'wt_signal_3m', 'wt_score_3m',
#     'stoch_k_3m', 'stoch_d_3m', 'k_3m_prev', 'd_3m_prev',
#     'stoch_crossover_3m', 'stoch_crossunder_3m',
#     'dc_basis_crossover_3m', 'dc_basis_crossunder_3m',
#     'dc_high_crossover_3m', 'dc_low_crossunder_3m', 'dc_high_crossunder_3m', 'dc_low_crossover_3m',
#     'ha_3m', 'ha_3m_prev',
#     'atr_3m', 'atr_3m_prev',
#     'relative_volume_3m',
#     'high_3m', 'low_3m', 'high_3m_prev', 'low_3m_prev',
#     'mfi_3m', 'rsi_3m',
#     'ema_20_3m','t_up_3m', 'tco_3m', 'tcu_3m',

#     # === 15M TIMEFRAME ===
#     'lr_trend_15m',
#     'dc_high_15m', 'dc_low_15m', 'dc_basis_15m', 'dc_high_15m_prev', 'dc_low_15m_prev',
#     'dc_high_15m_ant', 'dc_low_15m_ant', 'dc_basis_15m_ant',
#     'dc_high4_15m', 'dc_low4_15m',
#     'stoch_k_15m', 'stoch_d_15m', 'stoch_k_15m_prev', 'd_15m_prev',
#     'stoch_crossover_15m', 'stoch_crossunder_15m',
#     'sma_crossover_15m', 'sma_crossunder_15m',
#     'dc_basis_crossover_15m', 'dc_basis_crossunder_15m',
#     'dc_high_crossover_15m', 'dc_low_crossunder_15m', 'dc_high_crossunder_15m', 'dc_low_crossover_15m',
#     'wt1_15m', 'wt2_15m', 'wt_signal_15m', 'wt_score_15m',
#     'ha_15m', 'ha_15m_prev',
#     'atr_15m', 'atr_15m_prev',
#     'relative_volume_15m',
#     'high_15m', 'low_15m', 'high_15m_prev', 'low_15m_prev',
#     'sma_200_15m', 'sma_200_15m_prev',
#     'mfi_15m', 'rsi_15m',
#     'ema_20_15m', 't_up_15m',

#     # === 1H TIMEFRAME ===
#     'lr_trend_1h',
#     'dc_high_1h', 'dc_low_1h', 'dc_basis_1h', 'dc_high_1h_ant', 'dc_low_1h_ant',
#     'dc_basis_1h_ant', 'dc_high4_1h', 'dc_low4_1h',
#     'stoch_k_1h', 'stoch_d_1h', 'k_1h_prev', 'd_1h_prev',
#     'stoch_crossover_1h', 'stoch_crossunder_1h',
#     'sma_crossover_1h', 'sma_crossunder_1h',
#     'dc_basis_crossover_1h', 'dc_basis_crossunder_1h',
#     'dc_high_crossover_1h', 'dc_low_crossunder_1h', 'dc_high_crossunder_1h', 'dc_low_crossover_1h',
#     'wt1_1h', 'wt2_1h', 'wt_signal_1h', 'wt_score_1h',
#     'ha_1h',
#     'atr_1h', 'atr_1h_prev',
#     'sma_200_1h', 'sma_200_1h_prev',
#     'high_1h', 'low_1h',
#     'mfi_1h', 'rsi_1h',
#     'ema_20_1h',

#     # === 4H TIMEFRAME ===
#     'lr_trend_4h',
#     'slope_close_4h', 'linearity_4h',
#     'dc_high_4h', 'dc_low_4h', 'dc_basis_4h', 'dc_high_4h_ant', 'dc_low_4h_ant',
#     'dc_basis_4h_ant', 'dc_high4_4h', 'dc_low4_4h',
#     'stoch_k_4h', 'stoch_d_4h', 'k_4h_prev', 'stoch_d_4h_prev',
#     'stoch_crossover_4h', 'stoch_crossunder_4h',
#     'sma_crossover_4h', 'sma_crossunder_4h',
#     'dc_basis_crossover_4h', 'dc_basis_crossunder_4h',
#     'dc_high_crossover_4h', 'dc_low_crossunder_4h', 'dc_high_crossunder_4h', 'dc_low_crossover_4h',
#     'wt1_4h', 'wt2_4h', 'wt_signal_4h', 'wt_score_4h',
#     'ha_4h',
#     'atr_4h', 'atr_4h_prev',
#     'sma_200_4h', 'sma_200_4h_prev',
#     'high_4h', 'low_4h',
#     'mfi_4h', 'rsi_4h',
#     'ema_20_4h',

#     # === DAILY (D) TIMEFRAME ===
#     'dc_high_D', 'dc_low_D', 'dc_basis_D', 'dc_high_D_prev', 'dc_low_D_prev', 'dc_basis_D_prev',
#     'dc_high_D_ant', 'dc_low_D_ant', 'dc_basis_D_ant',
#     'dc_basis_crossover_D', 'dc_basis_crossunder_D',
#     'dc_high_crossover_D', 'dc_low_crossunder_D', 'dc_high_crossunder_D', 'dc_low_crossover_D',
#     'stoch_k_D', 'stoch_d_D', 'k_D_prev', 'stoch_d_D_prev', 'stoch_crossover_D', 'stoch_crossunder_D',
#     'wt1_D', 'wt2_D', 'wt_signal_D', 'wt_score_D',
#     'ha_D', 'atr_D', 'atr_D_prev', 'sma_200_D', 'sma_200_D_prev',
#     'mfi_D', 'rsi_D',
#     'high_D', 'low_D',
# ]

REQUIRED_INDICATORS: List[str] = [
    # === RANKING & SCORING (MANDATORY) ===
    '0ranking_points', '0ranking_points_global', '0market_sentiment_score', '0market_sentiment_score_ema', '0market_sentiment_local',
    '0sentiment_classification', '0sentiment_strength', '0is_top_sentiment', '0is_bottom_sentiment',
    '0sentiment_rank', '0final_score_norm',

    # === CONVICTION SCORES (CALCULATED IN PIPELINE) ===
    'zconviction_augment_long', 'zconviction_augment_short',
    'zconviction_reasons_augment_long', 'zconviction_reasons_augment_short',

    # === BASIC PRICE DATA (MANDATORY) ===
    'current_price', 'prev_price', 'timestamp',
    'timestamp_3m', 'timestamp_15m', 'timestamp_1h', 'timestamp_4h', 'timestamp_D',

    # === 1M TIMEFRAME ===
    'stoch_k_1m', 'stoch_d_1m', 'k_1m_prev', 'd_1m_prev',
    'sma_200_1m', 'sma_200_1m_prev',
    'sma_crossover_1m', 'sma_crossunder_1m',

    # === 3M TIMEFRAME ===
    'lr_trend_3m',
    'dc_high_3m', 'dc_low_3m', 'dc_basis_3m', 'dc_high_3m_prev', 'dc_low_3m_prev',
    'dc_high_3m_ant', 'dc_low_3m_ant', 'dc_basis_3m_ant', 'dc_high4_3m', 'dc_low4_3m',
    'wt1_3m', 'wt2_3m', 'wt_signal_3m', 'wt_score_3m',
    'stoch_k_3m', 'stoch_d_3m', 'k_3m_prev', 'd_3m_prev',
    'stoch_crossover_3m', 'stoch_crossunder_3m',
    'dc_basis_crossover_3m', 'dc_basis_crossunder_3m',
    'dc_high_crossover_3m', 'dc_low_crossunder_3m', 'dc_high_crossunder_3m', 'dc_low_crossover_3m',
    'ha_3m', 'ha_3m_prev',
    'atr_3m', 'atr_3m_prev',
    'relative_volume_3m',
    'high_3m', 'low_3m', 'high_3m_prev', 'low_3m_prev',
    'mfi_3m', 'rsi_3m',
    'ema_20_3m', 'ema_20_std_3m','t_up_3m', 'tco_3m', 'tcu_3m',

    # === 15M TIMEFRAME ===
    'lr_trend_15m',
    'dc_high_15m', 'dc_low_15m', 'dc_basis_15m', 'dc_high_15m_prev', 'dc_low_15m_prev',
    'dc_high_15m_ant', 'dc_low_15m_ant', 'dc_basis_15m_ant',
    'dc_high4_15m', 'dc_low4_15m',
    'stoch_k_15m', 'stoch_d_15m', 'stoch_k_15m_prev', 'd_15m_prev',
    'stoch_crossover_15m', 'stoch_crossunder_15m',
    'sma_crossover_15m', 'sma_crossunder_15m',
    'dc_basis_crossover_15m', 'dc_basis_crossunder_15m',
    'dc_high_crossover_15m', 'dc_low_crossunder_15m', 'dc_high_crossunder_15m', 'dc_low_crossover_15m',
    'wt1_15m', 'wt2_15m', 'wt_signal_15m', 'wt_score_15m', 'ha_15m',
    'atr_15m', 'atr_15m_prev',
    'relative_volume_15m',
    'high_15m', 'low_15m', 'high_15m_prev', 'low_15m_prev',
    'sma_200_15m', 'sma_200_15m_prev',
    'mfi_15m', 'rsi_15m',
    'ema_20_15m', 't_up_15m',

    # === 1H TIMEFRAME ===
    'lr_trend_1h',
    'dc_high_1h', 'dc_low_1h', 'dc_basis_1h', 'dc_high_1h_ant', 'dc_low_1h_ant',
    'dc_basis_1h_ant', 'dc_high4_1h', 'dc_low4_1h',
    'stoch_k_1h', 'stoch_d_1h', 'k_1h_prev', 'd_1h_prev',
    'stoch_crossover_1h', 'stoch_crossunder_1h',
    'sma_crossover_1h', 'sma_crossunder_1h',
    'dc_basis_crossover_1h', 'dc_basis_crossunder_1h',
    'dc_high_crossover_1h', 'dc_low_crossunder_1h', 'dc_high_crossunder_1h', 'dc_low_crossover_1h',
    'wt1_1h', 'wt2_1h', 'wt_signal_1h', 'wt_score_1h',
    'ha_1h',
    'atr_1h', 'atr_1h_prev',
    'sma_200_1h', 'sma_200_1h_prev',
    'high_1h', 'low_1h', 'high_1h_prev', 'low_1h_prev',
    'mfi_1h', 'rsi_1h',
    'ema_20_1h',

    # === 4H TIMEFRAME ===
    'lr_trend_4h', 
    'slope_close_4h', 'linearity_4h',
    'dc_high_4h', 'dc_low_4h', 'dc_basis_4h', 'dc_high_4h_ant', 'dc_low_4h_ant',
    'dc_basis_4h_ant', 'dc_high4_4h', 'dc_low4_4h',
    'stoch_k_4h', 'stoch_d_4h', 'k_4h_prev', 'stoch_d_4h_prev',
    'stoch_crossover_4h', 'stoch_crossunder_4h',
    'sma_crossover_4h', 'sma_crossunder_4h',
    'dc_basis_crossover_4h', 'dc_basis_crossunder_4h',
    'dc_high_crossover_4h', 'dc_low_crossunder_4h', 'dc_high_crossunder_4h', 'dc_low_crossover_4h',
    'wt1_4h', 'wt2_4h', 'wt_signal_4h', 'wt_score_4h',
    'ha_4h',
    'atr_4h', 'atr_4h_prev',
    'sma_200_4h', 'sma_200_4h_prev', 'ema_20_std_4h',
    'high_4h', 'low_4h', 'high_4h_prev', 'low_4h_prev',
    'mfi_4h', 'rsi_4h',
    'ema_20_4h',

    # === DAILY (D) TIMEFRAME ===
    'dc_high_D', 'dc_low_D', 'dc_basis_D', 'dc_high_D_prev', 'dc_low_D_prev', 'dc_basis_D_prev',
    'dc_high_D_ant', 'dc_low_D_ant', 'dc_basis_D_ant',
    'dc_basis_crossover_D', 'dc_basis_crossunder_D',
    'dc_high_crossover_D', 'dc_low_crossunder_D', 'dc_high_crossunder_D', 'dc_low_crossover_D',
    'stoch_k_D', 'stoch_d_D', 'k_D_prev', 'stoch_d_D_prev', 'stoch_crossover_D', 'stoch_crossunder_D',
    'wt1_D', 'wt2_D', 'wt_signal_D', 'wt_score_D',
    'ha_D', 'atr_D', 'atr_D_prev', 'sma_200_D', 'sma_200_D_prev',
    'mfi_D', 'rsi_D',
    'high_D', 'low_D',
]
_DEFAULT_FINAL_SCORING_INDICATORS: List[str] = [
    indicator for indicator in REQUIRED_INDICATORS if indicator.startswith('0')
]

_DEFAULT_CORE_TECHNICAL_INDICATORS: List[str] = [
    indicator for indicator in REQUIRED_INDICATORS if not indicator.startswith('0')
]
