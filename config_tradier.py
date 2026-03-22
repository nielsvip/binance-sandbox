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
    REDUCTION_COOLDOWN_SECONDS: float = 30.0  # BACKTEST_CHANGE_T38 was 60 → 30s faster rotation
    AUGMENTATION_COOLDOWN_SECONDS: float = 300.0
    MIN_GAIN_TO_BUY_AGGRESSIVELY: float = 5.0  # ABLATION_BACKTEST: was 1.15. Only augment confirmed 5%+ winners
    MIN_POSITION_SIZE: float = 100.0
    MAX_POSITION_SIZE: float = 5000.0
    START_POSITION_SIZE: float = 600.0  # BACKTEST_CHANGE_T26 was 400 → 600 larger base size
    # === WING BUDGETS ===
    SWING_LONG_BUDGET: float = 2000.0       # Max $ in swing longs
    SWING_SHORT_BUDGET: float = 2000.0      # Max $ in swing shorts
    SWING_MAX_POSITION_SIZE: float = 2000.0  # Per-symbol cap for swing
    SWING_START_SIZE: float = 800.0          # Base order value for swing
    SCALP_LONG_BUDGET: float = 1000.0       # Max $ in scalp longs
    SCALP_SHORT_BUDGET: float = 1000.0      # Max $ in scalp shorts
    SCALP_MAX_POSITION_SIZE: float = 2000.0  # Per-symbol cap for scalp
    SCALP_START_SIZE: float = 600.0          # BACKTEST_CHANGE_T27 was 800 → 600 align with START_POSITION_SIZE
    SCALP_MAX_HOLD_MINUTES: float = 180.0     # URGENT_FIX: shorter holds, take profits/losses faster (was 300)
    SCALP_STOP_PCT: float = 9.99             # BACKTEST_CHANGE_T12 was 1.5% → 999% effectively disabled NO_LOSS mode
    SCALP_TARGET_PCT: float = 0.005           # URGENT_FIX: tighter TP in choppy market, take profits faster (was 0.01 = 1.0% → 0.005 = 0.5%)
    SCALP_MAX_POSITIONS_PER_SIDE: int = 6    # BACKTEST_CHANGE_T34 was 8 → 6 concentrate capital
    SCALP_TOP_MOVERS_N: int = 14             # Candidate pool size
    SCALP_MIN_REL_VOL: float = 1.1           # Min relative volume to qualify
    SCALP_MIN_MOVE_PCT: float = 0.003        # Min 0.3% 5m deviation from ema_20_5m
    MAX_ORDER_VALUE: float = 2000.0
    ACCOUNT_SIDE_MAPPING: Dict[str, List[str]] = field(default_factory=lambda: {"tra": ["LONG", "SHORT"]})
    BLACKLIST = ['PLTR' ,'MSTR', 'BTC', 'ETHE', 'GOOGL', 'XIACF',"AAPL"] #Tradingview
    ALWAYS_TRADEABLE =["MSTR", "NVDA", "GOOG", "META", "MSFT", "GLD", "SKY"]
    NON_SHORTABLE = {"ETHE", "TCEHY", "XIACF", "BITO", "GBTC", "MSTR", "MARA", "RIOT", "CLSK", "HIVE", "CAN", "BTBT", "CUBT", "ETH", "BTC","QUBT","DUOL", "GLD", "USAR", "ETHD", "SBIT", "INOD", "BTCL", "DIME"}
    EXCEPTIONS = ['GOOGL', 'MSFT', 'NVDA', 'CVX', 'SNDK', 'IBIT', 'MSTR', 'GLD', 'ETH'] #4* max order size and max pos size
    # === ENTRY ZONE GATES (backtest: 1h primary for stocks, SMA200 Sharpe 89, MFI_D Sharpe 78) ===
    ENTRY_ZONE_LONG: float =               25.0   # BACKTEST_CHANGE_T1 was 22 → 25 wider entry zone
    ENTRY_ZONE_SHORT: float =              75.0   # BACKTEST_CHANGE_T2 was 78 → 75 wider entry zone
    ENTRY_MIN_ALIGNMENT: int =             8      # BACKTEST_CHANGE_T6 was 10 → 8 relaxed alignment
    ENTRY_PRIMARY_TF: str =                '4h'   # BACKTEST_CHANGE_T7 was 1h → 4h slower primary TF
    ENTRY_TRIGGER_TF: str =                '15m'  # Trigger TF for crossover (was 5m, shifted to 15m for stocks)
    # === SMA200 DISTANCE FILTER (backtest) ===
    SMA200_DIST_ENTRY_ENABLED: bool = True  # BACKTEST_CHANGE_T3 SMA200 distance gate for entries
    SMA200_DIST_LONG_THRESHOLD_4H: float = -10.0  # BACKTEST_CHANGE_T3 only long when price within -10% of SMA200 on 4h
    # === MFI ENTRY FILTER (backtest) ===
    MFI_ENTRY_ENABLED: bool = True  # BACKTEST_CHANGE_T4 MFI gate for entries
    MFI_LONG_THRESHOLD_D: float = 20.0  # BACKTEST_CHANGE_T4 only long when daily MFI < 20 (oversold)
    # === WT CROSSUNDER SHORT (backtest) ===
    WT_CROSSUNDER_15M_SHORT: bool = True  # BACKTEST_CHANGE_T5 enable WT crossunder on 15m for short entries
    # === ALIGNMENT GATE (backtest) ===
    ALIGNMENT_GATE_MIN: int = 4  # BACKTEST_CHANGE_T8 minimum indicators aligned
    ALIGNMENT_GATE_TOTAL: int = 12  # BACKTEST_CHANGE_T8 total alignment score required
    # === CONVICTION THRESHOLDS (backtest) ===
    CONVICTION_SHORT_THRESHOLD: int = 20  # BACKTEST_CHANGE_T9 min conviction score for short entries
    # === LR PCTB SHORT (backtest) ===
    LR_PCTB_D_SHORT_THRESHOLD: float = 0.1  # BACKTEST_CHANGE_T10 daily LR %B threshold for shorts
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
    INDICATOR_UPDATE_INTERVAL: float = 30.0  # BACKTEST_CHANGE_T49 was 60 → 30 faster indicator refresh
    RANKING_UPDATE_INTERVAL: float = 180.0  # BACKTEST_CHANGE_T50 was 300 → 180 faster ranking refresh
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
    # === ROTATION STRATEGY (5yr backtest: +71.2%, Sharpe 0.60, 3,669 trades, 10d lookback) ===
    ROTATION_ENABLED: bool = True
    ROTATION_TOP_N: int = 3       # URGENT_FIX: fewer long positions in bear market (was 5)
    ROTATION_BOTTOM_N: int = 8   # URGENT_FIX: more short candidates (was 5)
    ROTATION_HOLD_DAYS: int = 7  # BACKTEST_CHANGE_T44 was 5 → 7 longer hold
    ROTATION_LOOKBACK_DAYS: int = 10  # 10-day return lookback (5yr optimal, was 3)
    ROTATION_POSITION_SIZE: float = 1200.0  # BACKTEST_CHANGE_T28 was 800 → 1200 larger rotation size
    ROTATION_SMA200_FILTER: bool = True  # BACKTEST_CHANGE_T47 filter rotation candidates by SMA200
    # === RSI(2) MEAN REVERSION (Sharpe 2.05, 60.8% WR, 804 trades) ===
    RSI2_ENABLED: bool = True
    RSI2_ENTRY_THRESHOLD: float = 3.0  # BACKTEST_CHANGE_T48 was 5.0 → 3.0 stricter entry
    RSI2_EXIT_THRESHOLD_LONG: float = 70.0  # BACKTEST_CHANGE_T13 was 65 → 70 hold longer
    RSI2_EXIT_THRESHOLD_SHORT: float = 30.0  # BACKTEST_CHANGE_T14 was 35 → 30 hold longer
    RSI2_POSITION_SIZE: float = 600.0  # BACKTEST_CHANGE_T29 was 800 → 600 align sizing
    # === GAP FILL STRATEGY (OOS Sharpe 9.05, 66.1% WR, 0.5% max DD) ===
    GAP_FILL_ENABLED: bool = True
    GAP_FILL_MIN_GAP_PCT: float = 1.0
    GAP_FILL_MAX_GAP_PCT: float = 5.0
    GAP_FILL_STOP_MULT: float = 0.3
    GAP_FILL_TP_FILL_PCT: float = 0.7  # BACKTEST_CHANGE_T18 was 0.5 → 0.7 capture more of gap
    GAP_FILL_POSITION_SIZE: float = 600.0  # BACKTEST_CHANGE_T30 was 400 → 600 align sizing

    # === EXIT ENHANCEMENTS (backtest) ===
    ATR_TRAIL_2X_EXIT_ENABLED: bool = False  # BACKTEST_CHANGE_T58: was True. ATR trail = #1 stock PnL destroyer (-2557% cumulative). Disabled.
    STOCH_CROSS_1H_EXIT_ENABLED: bool = True  # BACKTEST_CHANGE_T17 stoch cross on 1h triggers exit
    # === TIME ZONE SIZING (backtest) ===
    TIME_ZONE_ENABLED: bool = True  # BACKTEST_CHANGE_T19 enable time-of-day zone sizing
    ZONE_OPEN_THRESHOLD: int = 25  # BACKTEST_CHANGE_T20 minutes after open = "open zone"
    ZONE_MID_THRESHOLD: int = 30  # BACKTEST_CHANGE_T21 minutes into session = "mid zone" start
    ZONE_CLOSE_THRESHOLD: int = 20  # BACKTEST_CHANGE_T22 minutes before close = "close zone"
    CLOSE_ZONE_SIZE_MULT: float = 1.5  # BACKTEST_CHANGE_T23 size multiplier in close zone
    MID_ZONE_SHORT_EXTRA_IND: str = "wt_crossunder_15m"  # BACKTEST_CHANGE_T24 extra indicator for mid-zone shorts
    HOLD_BARS_OPEN: int = 200  # BACKTEST_CHANGE_T25 max hold bars during open zone
    HOLD_BARS_MID: int = 500  # BACKTEST_CHANGE_T25 max hold bars during mid zone
    HOLD_BARS_CLOSE: int = 50  # BACKTEST_CHANGE_T25 max hold bars during close zone
    # === NO-LOSS NATURAL EXIT + K-ZONE ENTRY + BOUNCE REENTRY (2026-03-21 — 121 sym × D/4h/1h, 9192 combos) ===
    NOLOSS_MIN_PROFIT_PCT_TRADIER: float = 1.0  # BACKTEST_CHANGE_T51: Min profit % before exit. Stocks: 1% (wider than crypto 0.5% due to gaps). HODL LONG D: 95.7% WR at 2% TP.
    K_ZONE_ENTRY_ENABLED_TRADIER: bool = True  # BACKTEST_CHANGE_T52: K-zone entry — K in zone + turning + candle confirms. No crossover wait.
    K_ZONE_LONG_THRESHOLD_TRADIER: int = 35  # K must be below this for LONG entry
    K_ZONE_SHORT_THRESHOLD_TRADIER: int = 65  # K must be above this for SHORT entry
    K_ZONE_ENTRY_BONUS_TRADIER: int = 25  # Score bonus for K-zone + candle
    BOUNCE_REENTRY_ENABLED_TRADIER: bool = True  # BACKTEST_CHANGE_T53: After profitable exit, K must reset to zone before reentry.
    BOUNCE_REENTRY_K_RESET_LONG_TRADIER: int = 35
    BOUNCE_REENTRY_K_RESET_SHORT_TRADIER: int = 65
    HODL_LONG_ONLY: bool = True  # BACKTEST_CHANGE_T54: HODL strategy is LONG only. SHORT on stocks = negative returns (upward bias kills hold-forever shorts).
    # === ABLATION BACKTEST RESULTS (2026-03-21 — 2453 configs × 121 sym, D bars, P1+P2 OOS-validated) ===
    RSI_ENTRY_PERIOD_TRADIER: int = 10  # BACKTEST_CHANGE_T55: was 2. RSI(10) = OOS champion. Deeper mean-reversion captures bigger moves. Sharpe 6.43, WR 73.9%, PF 8.18
    RSI_ENTRY_LONG_TRADIER: float = 30.0  # BACKTEST_CHANGE_T55: entry when RSI(10) < 30. Wider than crypto due to daily TF
    RSI_ENTRY_SHORT_TRADIER: float = 70.0  # BACKTEST_CHANGE_T55: entry when RSI(10) > 70
    RSI_EXIT_LONG_TRADIER: float = 85.0  # BACKTEST_CHANGE_T56: was 70. Exit at RSI>85 = let winners run longer. +311% PnL over 4.8yr
    RSI_EXIT_SHORT_TRADIER: float = 15.0  # BACKTEST_CHANGE_T56: exit when RSI < 15
    SMA_FILTER_PERIOD_TRADIER: int = 100  # BACKTEST_CHANGE_T57: was 200. SMA100 filter = best OOS. Only LONG above SMA, SHORT below
    ATR_TRAIL_ENABLED_TRADIER: bool = False  # BACKTEST_CHANGE_T58: was True. ATR trailing stop = #1 stock PnL destroyer (-2557%). Disabled.
    STOCH_CROSS_ENTRY_TRADIER: bool = False  # BACKTEST_CHANGE_T59: was True. Stoch crossover = noise on daily bars. RSI(10) is the real entry.
    AUGMENT_PYRAMID_TRADIER: bool = False  # BACKTEST_CHANGE_T60: Pyramiding barely fires on stocks (0-10 trades). Disabled.
    # === BEAR MARKET MODE ===
    BEAR_MARKET_MODE_TRADIER: bool = True  # URGENT_FIX: favor shorts in current bear market
    AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER: bool = True  # URGENT_FIX: never augment losing positions
    # === HEDGE vs RATIO SWEEP (2026-03-21 — 65 configs, both systems) ===
    RATIO_MULTIPLIER_TRADIER: float = 3.5  # BACKTEST_CHANGE_T61: was 2.0. 3.5x ratio exaggeration = Sharpe 260 (vs 249 at 2x). Best: 3.5-4x.
    HEDGE_CROSS_SYMBOL_TRADIER: bool = True  # BACKTEST_CHANGE_T62: Cross-symbol hedge enabled. 25% size, trigger -1%, no momentum gate.
    HEDGE_SIZE_RATIO_TRADIER: float = 0.25  # BACKTEST_CHANGE_T62: Hedge at 25% of losing value. Sweet spot in sweep.
    HEDGE_TRIGGER_LOSS_TRADIER: float = -1.0  # BACKTEST_CHANGE_T62: Trigger hedge at -1% loss (stocks: tighter than crypto -2% due to daily gaps).
    HEDGE_SAME_SYMBOL_TRADIER: bool = False  # BACKTEST_CHANGE_T63: Same-symbol hedge DISABLED for stocks. Cross-symbol only.
    # === HEDGE MODE (backtest) ===
    HEDGE_MODE_TRADIER: bool = False  # BACKTEST_CHANGE_T31 hedge mode disabled for stocks
    # === POSITION LIMITS (backtest) ===
    MAX_CONCURRENT_POSITIONS: int = 16  # BACKTEST_CHANGE_T35 total max positions across all strategies
    # === L/S RATIO ENFORCEMENT (backtest) ===
    LS_RATIO_ENFORCE_TRADIER: bool = True  # BACKTEST_CHANGE_T36 enforce long/short ratio
    LS_RATIO_MIN_TRADIER: float = 0.50  # BACKTEST_CHANGE_T36 min L/S ratio
    LS_RATIO_MAX_TRADIER: float = 2.00  # BACKTEST_CHANGE_T36 max L/S ratio
    # === DAILY LOSS LIMIT (backtest) ===
    MAX_DAILY_LOSS_PCT: float = 3.0  # BACKTEST_CHANGE_T37 halt trading at 3% daily loss

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