import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional
from dotenv import load_dotenv
load_dotenv()

@dataclass
class TradierConfig:
    SYMBOL_PERF_ENABLED: bool = False  # Symbol performance tracking (requires ez_symbol_performance)
    SYMBOL_PERF_REFRESH_SECONDS: float = 300.0  # How often to refresh symbol performance cache
    OUTLIER_DETECTOR_ENABLED: bool = False  # Outlier detection for unusual price moves
    OUTLIER_SCAN_INTERVAL: float = 300.0  # Outlier scan interval in seconds
    OUTLIER_STALE_HOURS: float = 24.0  # Hours before outlier data is considered stale
    SLEEP_TIME_PER_TASKS: float = 3.0  # Sleep between task cycles
    WT_COMPOSITE_SCORING_ENABLED_TRADIER: bool = True  # Use WT composite cross-TF scoring for stocks
    # === MTS TF WEIGHTS FOR STOCKS — D_dom wins (Sharpe 5.91 vs default 5.42) ===
    # Sweep: 48 stocks, 3yr, test_wt_optimization.py Phase 4 (2026-03-25)
    # Stocks are slower than crypto — daily TF matters most, 1h less dominant
    MTS_WEIGHT_5m: float = 2.0
    MTS_WEIGHT_15m: float = 4.0
    MTS_WEIGHT_1h: float = 3.0   # Crypto=12, stocks=3 (D matters more for stocks)
    MTS_WEIGHT_4h: float = 5.0
    MTS_WEIGHT_D: float = 12.0   # DOMINANT for stocks (vs 1h dominant for crypto)
    # Stocks: MTS as score bonus only, NOT hard gate (real test: gate over-filters, WT entry alone +9-13%)
    MTS_GATE_ENABLED_TRADIER: bool = False  # OFF — stocks benefit from WT entry, not MTS filtering
    MTS_BOTTOM_MIN_TRADIER: float = 5.0  # If re-enabled: loose threshold
    MTS_ENTRY_QUALITY_MIN_TRADIER: float = 0.0  # If re-enabled: no eq gate
    HIGH_GAIN_AUGMENTATION_MIN_SIZE: float = 200.0
    REDUCTION_COOLDOWN_SECONDS: float = 30.0  # BACKTEST_CHANGE_T38 was 60 → 30s faster rotation
    AUGMENTATION_COOLDOWN_SECONDS: float = 300.0
    MIN_GAIN_TO_BUY_AGGRESSIVELY: float = 3.0  # was 5.0. 3.0% survives 1.5% reversal after 50% aug
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
    ACCOUNT_SIDE_MAPPING: Dict[str, List[str]] = field(default_factory=lambda: {"tra": ["LONG"]})
    # tra = SATOSHIT-only account. Block all other strategies (HODL, rotation, RSI2, gap fill, ORB, EP).
    TRA_SATOSHIT_ONLY: bool = True
    # ───────────────────────────────────────────────────────────────────────────
    # tra ACCOUNT — LONG-TERM HOLD MODE (rewritten 2026-04-09)
    #
    # tra is a CASH (non-margin) account. It cannot short. It is supposed to
    # have a long-term bias and few trades. Prior behaviour:
    #   - $20k → $16k in a week (constant losing exits + failed shorts)
    #   - 19 LONG opens-and-closes in days, 10 SHORT entries all rejected by API
    # Root causes:
    #   - WT/DC exit scorer threshold 20 → fired on 5m/15m noise
    #   - SHORT candidates were being scanned every 90s and signals fired
    #   - cash account cannot short, so the orders died at the API
    #   - long/short ratio rules don't apply, but the engine tried to balance
    #
    # The flags below let `tradier_manage.py` apply tra-specific gates without
    # disturbing trb/trc behaviour.
    # ───────────────────────────────────────────────────────────────────────────
    TRA_LONG_ONLY: bool = True                       # tra is cash account → no shorts EVER
    TRA_NO_LOSS_EXIT: bool = True                    # tra never closes a position at a loss
    TRA_STRICT_EXIT_ONLY: bool = True                # only the 5-of-5 STRICT_EXIT gate counts
    TRA_DISABLE_DELTA_ENTRY: bool = True             # delta engine is too fast for long-term hold
    TRA_DISABLE_AUGMENT: bool = True                 # no churn from augments either
    TRA_WT_DC_ENTRY_THRESHOLD: float = 85.0          # high bar — only the strongest HTF setups
    TRA_MIN_HOLD_MINUTES: float = 1440.0             # 24h hold floor before any exit considered
    # tra preferred symbols (user-specified). The actual list is in
    # symbols_tra_satoshit_long.json — these are the "core 9" the user named.
    TRA_PREFERRED_SYMBOLS: List[str] = field(default_factory=lambda: ["AAPL", "MSFT", "GOOGL", "MSTR", "PLTR", "NEM", "MU", "SNDK", "NVDA"])
    BLACKLIST = []#'PLTR' ,'MSTR', 'BTC', 'ETHE', 'GOOGL', 'XIACF',"AAPL"] #Tradingview
    ALWAYS_TRADEABLE = ["NVDA", "GOOG", "META", "MSFT", "GLD", "XLE", "XOP", "GDX", "USO", "CVX", "XOM", "SLV", "NEM", "FCX"]
    NON_SHORTABLE = {"ETHE", "TCEHY", "XIACF", "BITO", "GBTC", "MARA", "CLSK", "HIVE", "CAN", "BTBT", "CUBT", "ETH", "BTC", "QUBT", "GLD", "ETHD", "SBIT", "INOD", "BTCL", "DIME", "UCO", "PDBC", "COPX", "BLOK", "USO", "UNG", "BOIL", "WEAT", "CORN", "DBA", "GDXJ", "XME", "XOP", "OIH", "URA", "URNM", "ITA", "PPA", "MOO", "REMX", "IPI", "LSB", "UAN", "ASC", "EGLE", "GNK", "NAT", "TNK", "NNE", "DNN", "PLL", "SGML", "MAG", "BTG", "ICL", "SQM", "GOGL", "SBLK", "DAC", "FRO", "ZIM", "GOLD"}
    EXCEPTIONS = ['GOOGL', 'MSFT', 'NVDA', 'CVX', 'XOM', 'IBIT', 'GLD', 'ETH', 'XLE', 'GDX', 'USO', 'SLV'] #4* max order size and max pos size
    # === SECTOR CLASSIFICATION — for options diversification engine ===
    # Each symbol maps to a sector. Used by options agent to enforce hedging + diversification.
    SECTOR_MAP: Dict[str, str] = field(default_factory=lambda: {
        # TECH — Mega cap
        "AAPL": "TECH", "MSFT": "TECH", "GOOGL": "TECH", "META": "TECH", "AMZN": "TECH", "NVDA": "TECH",
        "AVGO": "TECH", "ASML": "TECH", "TSM": "TECH", "INTC": "TECH", "AMD": "TECH", "QCOM": "TECH",
        "ARM": "TECH", "MRVL": "TECH", "MU": "TECH", "LRCX": "TECH", "TXN": "TECH",
        # TECH — Software/Cloud
        "CRM": "TECH_SW", "ADBE": "TECH_SW", "ORCL": "TECH_SW", "SNOW": "TECH_SW", "WDAY": "TECH_SW",
        "PATH": "TECH_SW", "SHOP": "TECH_SW", "SPOT": "TECH_SW", "TTD": "TECH_SW", "QLYS": "TECH_SW",
        "CRWD": "TECH_SW", "CRWV": "TECH_SW", "ZETA": "TECH_SW", "FIVN": "TECH_SW", "OLED": "TECH_SW",
        # TECH — Internet/Consumer
        "NFLX": "TECH_CONS", "RBLX": "TECH_CONS", "RDDT": "TECH_CONS", "ROKU": "TECH_CONS", "BABA": "TECH_CONS",
        "BIDU": "TECH_CONS", "TCEHY": "TECH_CONS", "UBER": "TECH_CONS", "LYFT": "TECH_CONS", "SQ": "TECH_CONS",
        "PYPL": "TECH_CONS", "COIN": "TECH_CONS", "ABNB": "TECH_CONS", "DUOL": "TECH_CONS",
        # SMCI + hardware
        "SMCI": "TECH",
        # ENERGY — Oil & Gas
        "XOM": "ENERGY_OIL", "CVX": "ENERGY_OIL", "COP": "ENERGY_OIL", "EOG": "ENERGY_OIL", "OXY": "ENERGY_OIL",
        "MPC": "ENERGY_OIL", "VLO": "ENERGY_OIL", "PSX": "ENERGY_OIL", "PBF": "ENERGY_OIL", "DINO": "ENERGY_OIL",
        "DVN": "ENERGY_OIL", "FANG": "ENERGY_OIL", "APA": "ENERGY_OIL", "MRO": "ENERGY_OIL", "PR": "ENERGY_OIL",
        "HAL": "ENERGY_OIL", "SLB": "ENERGY_OIL", "BKR": "ENERGY_OIL", "HES": "ENERGY_OIL", "CHRD": "ENERGY_OIL",
        "CRK": "ENERGY_OIL", "AR": "ENERGY_OIL", "RRC": "ENERGY_OIL", "CTRA": "ENERGY_OIL", "AM": "ENERGY_OIL",
        "EQT": "ENERGY_OIL", "EPD": "ENERGY_OIL", "ET": "ENERGY_OIL", "KMI": "ENERGY_OIL", "WMB": "ENERGY_OIL",
        "TRGP": "ENERGY_OIL", "OKE": "ENERGY_OIL", "LNG": "ENERGY_OIL",
        # ENERGY ETFs
        "XLE": "ENERGY_OIL", "XOP": "ENERGY_OIL", "OIH": "ENERGY_OIL", "USO": "ENERGY_OIL",
        "UNG": "ENERGY_NAT", "BOIL": "ENERGY_NAT",
        # NUCLEAR
        "UEC": "NUCLEAR", "NXE": "NUCLEAR", "CCJ": "NUCLEAR", "DNN": "NUCLEAR", "LEU": "NUCLEAR",
        "NNE": "NUCLEAR", "SMR": "NUCLEAR", "OKLO": "NUCLEAR", "BWXT": "NUCLEAR",
        "URA": "NUCLEAR", "URNM": "NUCLEAR", "UUUU": "NUCLEAR",
        # MINING — Precious metals
        "NEM": "MINING_GOLD", "AEM": "MINING_GOLD", "FNV": "MINING_GOLD", "WPM": "MINING_GOLD", "RGLD": "MINING_GOLD",
        "GOLD": "MINING_GOLD", "KGC": "MINING_GOLD", "AG": "MINING_GOLD", "AGI": "MINING_GOLD", "EGO": "MINING_GOLD",
        "BTG": "MINING_GOLD", "CDE": "MINING_GOLD", "HL": "MINING_GOLD", "MAG": "MINING_GOLD", "PAAS": "MINING_GOLD",
        "AU": "MINING_GOLD", "GDX": "MINING_GOLD", "GDXJ": "MINING_GOLD", "GLD": "MINING_GOLD", "SLV": "MINING_GOLD",
        # MINING — Industrial/Base metals
        "FCX": "MINING_BASE", "SCCO": "MINING_BASE", "RIO": "MINING_BASE", "BHP": "MINING_BASE", "VALE": "MINING_BASE",
        "AA": "MINING_BASE", "NUE": "MINING_BASE", "STLD": "MINING_BASE", "CLF": "MINING_BASE", "X": "MINING_BASE",
        "RS": "MINING_BASE", "CMC": "MINING_BASE", "ATI": "MINING_BASE", "CENX": "MINING_BASE", "MP": "MINING_BASE",
        "LAC": "MINING_BASE", "PLL": "MINING_BASE", "SGML": "MINING_BASE", "SQM": "MINING_BASE",
        "COPX": "MINING_BASE", "XME": "MINING_BASE", "REMX": "MINING_BASE",
        # DEFENSE & AEROSPACE
        "LMT": "DEFENSE", "RTX": "DEFENSE", "GD": "DEFENSE", "NOC": "DEFENSE", "BA": "DEFENSE",
        "LHX": "DEFENSE", "HII": "DEFENSE", "LDOS": "DEFENSE", "KTOS": "DEFENSE", "HWM": "DEFENSE",
        "AXON": "DEFENSE", "RKLB": "DEFENSE", "JOBY": "DEFENSE", "TDG": "DEFENSE", "GE": "DEFENSE",
        "ITA": "DEFENSE", "PPA": "DEFENSE",
        # CRYPTO / DIGITAL ASSETS
        "IBIT": "CRYPTO", "BITO": "CRYPTO", "COIN": "CRYPTO", "BLOK": "CRYPTO", "SBIT": "CRYPTO",
        "BTCL": "CRYPTO", "ETHD": "CRYPTO", "ETH": "CRYPTO", "DIME": "CRYPTO", "QBTS": "CRYPTO",
        # AGRICULTURE
        "ADM": "AGRICULTURE", "BG": "AGRICULTURE", "CTVA": "AGRICULTURE", "FMC": "AGRICULTURE",
        "DE": "AGRICULTURE", "AGCO": "AGRICULTURE", "CNHI": "AGRICULTURE", "CF": "AGRICULTURE",
        "MOS": "AGRICULTURE", "NTR": "AGRICULTURE", "ICL": "AGRICULTURE", "SMG": "AGRICULTURE",
        "IPI": "AGRICULTURE", "LSB": "AGRICULTURE", "UAN": "AGRICULTURE", "INGR": "AGRICULTURE",
        "CALM": "AGRICULTURE", "TSN": "AGRICULTURE", "DAR": "AGRICULTURE",
        "MOO": "AGRICULTURE", "WEAT": "AGRICULTURE", "CORN": "AGRICULTURE", "DBA": "AGRICULTURE", "PDBC": "AGRICULTURE",
        # SHIPPING
        "ZIM": "SHIPPING", "SBLK": "SHIPPING", "DAC": "SHIPPING", "FRO": "SHIPPING", "GOGL": "SHIPPING",
        "EGLE": "SHIPPING", "GNK": "SHIPPING", "NAT": "SHIPPING", "TNK": "SHIPPING", "STNG": "SHIPPING",
        "DHT": "SHIPPING", "INSW": "SHIPPING", "ASC": "SHIPPING",
        # HEALTHCARE / PHARMA
        "LLY": "HEALTH", "JNJ": "HEALTH", "PFE": "HEALTH", "MRK": "HEALTH", "ABBV": "HEALTH",
        "ABT": "HEALTH", "TMO": "HEALTH", "DHR": "HEALTH", "GILD": "HEALTH", "MDT": "HEALTH",
        "UNH": "HEALTH",
        # CONSUMER / RETAIL
        "COST": "CONSUMER", "WMT": "CONSUMER", "TGT": "CONSUMER", "HD": "CONSUMER", "LOW": "CONSUMER",
        "NKE": "CONSUMER", "SBUX": "CONSUMER", "MCD": "CONSUMER", "PEP": "CONSUMER", "KO": "CONSUMER",
        "CLX": "CONSUMER", "ULTA": "CONSUMER", "DIS": "CONSUMER", "MO": "CONSUMER",
        # FINANCIALS
        "JPM": "FINANCIAL", "BK": "FINANCIAL", "SCHW": "FINANCIAL", "CME": "FINANCIAL",
        "MA": "FINANCIAL", "V": "FINANCIAL", "ACN": "FINANCIAL", "ADP": "FINANCIAL",
        "APO": "FINANCIAL", "EXE": "FINANCIAL", "IBM": "FINANCIAL",
        # INDUSTRIAL
        "CAT": "INDUSTRIAL", "GM": "INDUSTRIAL", "FDX": "INDUSTRIAL", "UPS": "INDUSTRIAL",
        # TELECOM
        "T": "TELECOM", "VZ": "TELECOM",
        # BROAD MARKET ETFs
        "SPY": "INDEX", "QQQ": "INDEX", "SHY": "INDEX",
        # GME / MEME
        "GME": "MEME", "TSLA": "MEME", "PLTR": "MEME", "QUBT": "MEME", "ASTS": "MEME",
        "SNDK": "MEME", "STZ": "CONSUMER",
    })
    # Sector groups for diversification — sectors in the same group are correlated
    SECTOR_GROUPS: Dict[str, str] = field(default_factory=lambda: {
        "TECH": "GROWTH", "TECH_SW": "GROWTH", "TECH_CONS": "GROWTH",
        "ENERGY_OIL": "COMMODITIES", "ENERGY_NAT": "COMMODITIES",
        "MINING_GOLD": "COMMODITIES", "MINING_BASE": "COMMODITIES",
        "NUCLEAR": "ENERGY_ALT",
        "DEFENSE": "DEFENSE",
        "CRYPTO": "CRYPTO",
        "AGRICULTURE": "COMMODITIES",
        "SHIPPING": "SHIPPING",
        "HEALTH": "DEFENSIVE", "CONSUMER": "DEFENSIVE", "TELECOM": "DEFENSIVE",
        "FINANCIAL": "FINANCIAL",
        "INDUSTRIAL": "CYCLICAL",
        "INDEX": "INDEX",
        "MEME": "SPECULATIVE",
    })
    # === OPTIONS DIVERSIFICATION BUDGET TIERS ===
    # Base cap: $5k unhedged. Hedged + diversified = up to $15k.
    OPTIONS_BASE_CAP: float = 5000.0          # Max with zero diversification
    OPTIONS_HEDGED_CAP: float = 10000.0       # Max with call+put hedging within sectors
    OPTIONS_FULL_DIV_CAP: float = 15000.0     # Max with hedging + 3+ sector groups
    OPTIONS_MAX_PER_SECTOR: float = 0.40      # Max 40% of portfolio in one sector
    OPTIONS_MAX_PER_GROUP: float = 0.60       # Max 60% of portfolio in one sector group
    OPTIONS_MAX_PER_SYMBOL: float = 0.25      # Max 25% of portfolio in one symbol
    OPTIONS_MIN_SECTORS: int = 2              # Min sectors for hedged tier
    OPTIONS_MIN_GROUPS: int = 3               # Min groups for full diversification tier
    OPTIONS_HEDGE_RATIO_MIN: float = 0.25     # Min puts/(puts+calls) to qualify as hedged
    OPTIONS_MAX_CONTRACTS_PER_ORDER: int = 10  # Hard cap: never buy >N contracts in one order
    # Market direction ratio: bull_exposure / (bull + bear). Too high = over-long market.
    OPTIONS_MARKET_RATIO_MIN: float = 0.25    # Min fraction of exposure that is bull-market-bets
    OPTIONS_MARKET_RATIO_MAX: float = 0.75    # Max fraction of exposure that is bull-market-bets
    # === BEAR_SCENARIO_SYMBOLS — symbols that go UP when markets go DOWN ===
    # A CALL on a bear_scenario symbol = bearish market bet (like a PUT on SPY).
    # A PUT on a bear_scenario symbol = bullish market bet (like a CALL on SPY).
    # Used to compute true market-direction ratio of the options portfolio.
    BEAR_SCENARIO_SYMBOLS = {
        # Gold / Silver / Miners — fear/inflation hedge, inverse market correlation
        "GLD", "SLV", "GDX", "GDXJ", "NEM", "AEM", "FNV", "WPM", "RGLD", "GOLD",
        "KGC", "AG", "AGI", "EGO", "BTG", "CDE", "HL", "MAG", "PAAS", "AU",
        # Oil & Energy ETFs — spike on geopolitical/inflation risk, often inverse market
        "USO", "UCO", "XLE", "XOP", "OIH", "UNG", "BOIL",
        # Volatility — explicitly inverse market
        "VXX", "UVXY",
    }
    # === ENTRY ZONE GATES (backtest: 1h primary for stocks, SMA200 Sharpe 89, MFI_D Sharpe 78) ===
    ENTRY_ZONE_LONG: float =               35.0   # V8 ABLATION 2026-04-13: Sharpe 1.437 (best combo). Was 25.
    ENTRY_ZONE_SHORT: float =              65.0   # Mirror of ZONE_LONG (100-35=65). Was 75.
    ENTRY_MIN_ALIGNMENT: int =             10     # V8 ABLATION 2026-04-13: Sharpe 1.0, WR 53.9%. Was 8.
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
    # === SATOSHIT2024 STRATEGY — stocks (15m mean-reversion, 5m instead of 3m) ===
    SATOSHIT_ENTRY_FILTER: bool = False  # T25 sweep: False avg_sharpe=0.529 vs True=0.381 (-28%). Best tested.
    SATOSHIT_ACCOUNTS_TRADIER: List[str] = field(default_factory=lambda: ["tra", "trb", "trc"])
    SATOSHIT_MIN_VOTES_TRADIER: int = 3  # 3-of-5 voting
    # Entry thresholds (same as crypto — stocks use 5m/15m instead of 3m/15m)
    SATOSHIT_LONG_RSI_MAX_TRADIER: float = 50.0
    SATOSHIT_LONG_STOCH_K_MAX_TRADIER: float = 60.0
    SATOSHIT_LONG_MFI_MAX_TRADIER: float = 60.0
    SATOSHIT_SHORT_RSI_MIN_TRADIER: float = 55.0
    SATOSHIT_SHORT_STOCH_K_MIN_TRADIER: float = 50.0
    SATOSHIT_SHORT_MFI_MIN_TRADIER: float = 50.0
    SATOSHIT_HTF_MFI_D_MIN_TRADIER: float = 30.0
    SATOSHIT_HTF_RVOL_1H_MIN_TRADIER: float = 0.3
    # Exit thresholds
    SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER: float = 55.0
    SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER: float = 60.0
    SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER: float = 42.0
    SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER: float = 50.0
    # === NEWS SENTIMENT ===
    NEWS_SENTIMENT_ENABLED: bool =         True
    NEWS_SENTIMENT_WEIGHT: float =         0.10
    # === CONGRESS CONVICTION SIZING ===
    # Boost sizing for symbols with proven-politician conviction from stock_trader_scanner.
    # Only counts trades from backtest-proven politicians (55%+ WR at 30d).
    # Congress SELLS excluded (46.6% WR = anti-signal). BUYS only.
    # Disable by setting to 1.0.
    CONGRESS_CONVICTION_SIZING_BOOST: float = 1.3
    CONGRESS_CONVICTION_MIN_SOURCES: int = 2
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
    HTF1_CONF: bool = True  # BC_154_T: ENABLED — stock ablation (121sym/2yr): htf2 Sharpe 0.600 vs default -0.177. Stocks need HTF confirmation.
    HTF4_CONF: bool = False  # Keep off — htf1 alone is sufficient, htf4 too restrictive
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
    GAP_FILL_MIN_GAP_PCT: float = 0.5  # BACKTEST_CHANGE_MT1: was 1.0. T6 sweep: Sharpe +0.120 (GAP=0.5) vs -0.138 (GAP=1.0). 180 configs, 2yr, 10 symbols. Smaller gaps fill more reliably.
    GAP_FILL_MAX_GAP_PCT: float = 5.0
    GAP_FILL_STOP_MULT: float = 0.3
    GAP_FILL_TP_FILL_PCT: float = 0.7  # BACKTEST_CHANGE_T18 was 0.5 → 0.7 capture more of gap
    GAP_FILL_POSITION_SIZE: float = 600.0  # BACKTEST_CHANGE_T30 was 400 → 600 align sizing

    # === FIRST-HOUR MOMENTUM (BACKTEST_CHANGE_MT3) ===
    # Research: First 30min > ±0.5% predicts day direction 82% of time (3,560 days, 10 symbols)
    # Momentum WITH trend (FH↑ + MFI>50 + DC>0.5) → 56-60% WR, +0.20%/day avg
    # Mean-reversion AGAINST trend → 38% WR, -0.41%/day avg (LOSER)
    FH_MOMENTUM_ENABLED: bool = True  # VALIDATED: Sharpe 1.54, +100% PnL, 25/25 configs profitable. V8 T10 sweep 2026-04-07.
    FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5  # Sweep: 0.3-1.0% all Sharpe>1.47. 0.5% = sweet spot (82% day-follows rate).
    FH_MOMENTUM_POSITION_SIZE: float = 600.0
    FH_MOMENTUM_EVAL_MINUTES: int = 30  # 30min after open. Research: first 30min predicts day 82%.
    FH_MOMENTUM_MAX_POSITIONS: int = 5
    FH_MOMENTUM_MFI_CONFIRM: bool = False  # Sweep: MFI barely matters (1.314 vs 1.313). OFF = more entries.
    FH_MOMENTUM_DC_CONFIRM: bool = True  # DC retest logic handles smart filtering now
    FH_MOMENTUM_DC_MAX_LONG: float = 0.5  # Sweep: 0.25-1.0 all Sharpe>1.36. 0.5 = balanced.

    # === EXIT ENHANCEMENTS (backtest) ===
    WT_CROSSUNDER_FINAL_ENABLED: bool = True  # Sweepable: WT cross final-resort exit (multi-TF crossunder/crossover)
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
    NOLOSS_MIN_PROFIT_PCT_TRADIER: float = 0.0  # 2026-04-08: LET WT/DC SCORER EXIT AT ANY GAIN. Scorer fires on velocity death — that IS the cut signal. 3.0% was BLOCKING technical exits on losers. Test: Sharpe 6.36 at 0% vs 1.43 at 3%.
    K_ZONE_ENTRY_ENABLED_TRADIER: bool = True  # BACKTEST_CHANGE_T52: K-zone entry — K in zone + turning + candle confirms. No crossover wait.
    K_ZONE_VETO_ENABLED_TRADIER: bool = False  # VARIANCE_FIX 2026-04-14: when True, K_ZONE_LONG/SHORT_THRESHOLD veto entries on wt_dc path (proves switch gates trades). Default False = live unchanged.
    WT_COMPOSITE_VETO_ENABLED_TRADIER: bool = False  # VARIANCE_FIX 2026-04-14: when True, WT_COMPOSITE_SCORING_ENABLED vetoes wt_dc entries lacking composite alignment. Default False = live unchanged.
    MI_EXIT_VETO_ENABLED_TRADIER: bool = False  # SENTINEL_FIX 2026-04-14: when True, MI_EXIT_ENABLED_TRADIER actually gates exits. Default False = live unchanged.
    WT_EXIT_VETO_ENABLED_TRADIER: bool = False  # SENTINEL_FIX 2026-04-14: when True, WT_EXIT_MIN_TFS_TRADIER actually gates exits. Default False = live unchanged.
    DC_ENTRY_VETO_ENABLED_TRADIER: bool = False  # SENTINEL_FIX 2026-04-14: when True, DC_POSITION_ENTRY_THRESHOLD gates entries (require dc_pos in zone). Default False = live unchanged.
    K_ZONE_LONG_THRESHOLD_TRADIER: int = 80   # T4 sweep: 80 avg=6.563 vs 35=5.567 (+18%). Best tested.
    K_ZONE_SHORT_THRESHOLD_TRADIER: int = 20  # T4 sweep: 20 avg=6.216 vs 65=5.681 (+10%). Best tested.
    K_ZONE_ENTRY_BONUS_TRADIER: int = 20  # 2026-04-08 SWEEP: 20 → Sharpe 11.12 vs 25 → 6.92 (+61%). Biggest single config win.
    BOUNCE_REENTRY_ENABLED_TRADIER: bool = True  # BACKTEST_CHANGE_T53: After profitable exit, K must reset to zone before reentry.
    BOUNCE_REENTRY_K_RESET_LONG_TRADIER: int = 35
    BOUNCE_REENTRY_K_RESET_SHORT_TRADIER: int = 65
    # === TWO-TIER MANDATORY REENTRY — STOCKS (BC_155) ===
    REENTRY_TIER1_SIZE_MULT_TRADIER: float = 1.5  # Tier 1: 150% of closed qty
    REENTRY_TIER2_SIZE_MULT_TRADIER: float = 0.8  # Tier 2: 80% of closed qty
    REENTRY_TIER2_PRICE_PCT_TRADIER: float = 0.003  # 0.3% price move triggers Tier 2
    REENTRY_TIER2_MIN_MINUTES_TRADIER: float = 10.0  # Min minutes before Tier 2
    REENTRY_TIER2_MAX_MINUTES_TRADIER: float = 120.0  # Force entry after 120min
    # MINIMUM HOLD TIME — prevents churning/death-by-1000-cuts on stocks
    MIN_HOLD_MINUTES_TRADIER: float = 30.0  # No exits before 30 min. Bypassed only if loss > -5%.
    # MULTI-TF EXIT CONFIRMATION — exits must mirror entry strength
    # Entry needs multi-TF WT alignment → exit needs multi-TF WT disalignment
    # Prevents 5m noise from killing positions that 15m/1h/4h still support
    MIN_EXIT_TF_AGAINST_TRADIER: int = 2  # Need 2+ TFs (of 5m/15m/1h/4h) with WT against position before exit
    # BOUNCE-TOP EXIT — V4 backtest proven: Sharpe -0.5 → +0.42 on 121 stocks 2yr
    # Exits losing positions at the TOP of a bounce (not the bottom like a stop loss).
    # Mandatory reentry follows: 150% at pullback, 200% at rising WT cross.
    BOUNCE_TOP_EXIT_ENABLED: bool = False  # KILLED 2026-03-30: percentage stop loss in disguise. Exits ONLY on technicals.
    BOUNCE_TOP_MIN_HOLD_MINUTES: float = 1440.0  # 24h min hold before bounce exit eligible
    BOUNCE_TOP_MIN_LOSS_PCT: float = -3.0  # Only fires when loss is between -3% and -50%
    BOUNCE_TOP_MAX_LOSS_PCT: float = -50.0  # Don't exit positions beyond -50% (too late)
    BOUNCE_TOP_REENTRY_MULT: float = 1.5  # 150% qty on pullback reentry
    BOUNCE_TOP_RISING_CROSS_MULT: float = 2.0  # 200% qty on rising WT cross reentry
    HODL_LONG_ONLY: bool = True  # BACKTEST_CHANGE_T54: HODL strategy is LONG only. SHORT on stocks = negative returns (upward bias kills hold-forever shorts).
    # === MOMENTUM FADE — STOCKS (2026-03-23 — crypto-validated, adapted for stocks) ===
    MOMENTUM_FADE_ENABLED_TRADIER: bool = False  # DISABLED: Gate ablation 2026-03-26 proved zero impact (Sharpe +0.00, +0 trades). Was T55b.
    MOMENTUM_FADE_BODY_ATR_MIN_TRADIER: float = 2.0  # BACKTEST_CHANGE_T55b: Candle range must be >= 2x ATR. Stocks move less so 2x is still significant.
    MOMENTUM_FADE_VOL_MIN_TRADIER: float = 2.0  # BACKTEST_CHANGE_T55b: Volume must be >= 2x avg. Same threshold as crypto.
    MOMENTUM_FADE_K_ZONE_TRADIER: bool = True  # BACKTEST_CHANGE_T55b: Only fade when K is overbought/oversold. Improves Sharpe significantly.
    MOMENTUM_FADE_SCORE_BONUS_TRADIER: int = 5  # BACKTEST_CHANGE_T55b: Score bonus (stock score capped at 30, so smaller bonus than crypto)
    # === ABLATION BACKTEST RESULTS (2026-03-21 — 2453 configs × 121 sym, D bars, P1+P2 OOS-validated) ===
    RSI_ENTRY_PERIOD_TRADIER: int = 10  # BACKTEST_CHANGE_T55: was 2. RSI(10) = OOS champion. Deeper mean-reversion captures bigger moves. Sharpe 6.43, WR 73.9%, PF 8.18
    RSI_ENTRY_LONG_TRADIER: float = 42.0  # BACKTEST_CHANGE_T64: was 30. RSI<42 = Sharpe 33.6, +18.6% return, 142 trades. Wider = more trades on stocks.
    RSI_ENTRY_SHORT_TRADIER: float = 58.0  # BACKTEST_CHANGE_T64: was 70. RSI>58 for shorts.
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
    # === TRC AGGRESSIVE SANDBOX — "after-sandbox sandbox" ===
    # trc is paper-money. Push extreme settings here to prove before applying to trb.
    TRC_START_POSITION_SIZE: float = 1980.0  # 3.3x trb ($600)
    TRC_MAX_ORDER_VALUE: float = 5000.0  # 2.5x trb ($2000)
    TRC_MAX_POSITION_SIZE: float = 15000.0  # 3x trb ($5000)
    TRC_SCALP_START_SIZE: float = 1980.0  # 3.3x trb ($600)
    TRC_SCALP_MAX_POSITIONS_PER_SIDE: int = 12  # 2x trb (6)
    TRC_MAX_CONCURRENT_POSITIONS: int = 32  # ~2x trb (16)
    TRC_ROTATION_POSITION_SIZE: float = 3000.0  # 2.5x trb ($1200)
    TRC_RSI2_POSITION_SIZE: float = 1980.0  # 3.3x trb ($600)
    TRC_GAP_FILL_POSITION_SIZE: float = 1980.0  # 3.3x trb ($600)
    TRC_DC_DAYTRADE_START_SIZE: float = 1980.0  # 3.3x trb ($600)
    TRC_DC_DAYTRADE_LONG_BUDGET: float = 9900.0  # 3.3x trb ($3000)
    TRC_DC_DAYTRADE_SHORT_BUDGET: float = 9900.0  # 3.3x trb ($3000)
    TRC_SWING_LONG_BUDGET: float = 8000.0  # 4x trb ($2000)
    TRC_SWING_SHORT_BUDGET: float = 8000.0  # 4x trb ($2000)
    TRC_SCALP_LONG_BUDGET: float = 5000.0  # 5x trb ($1000)
    TRC_SCALP_SHORT_BUDGET: float = 5000.0  # 5x trb ($1000)
    TRC_BEAR_MARKET_MODE: bool = False  # No bear penalty — test both directions equally
    TRC_ENTRY_ZONE_LONG: float = 30.0  # Wider than trb (25)
    TRC_ENTRY_ZONE_SHORT: float = 70.0  # Wider than trb (75)
    TRC_ENTRY_MIN_ALIGNMENT: int = 6  # Looser than trb (8)
    TRC_LS_RATIO_MIN: float = 0.30  # Wider than trb (0.50)
    TRC_LS_RATIO_MAX: float = 3.00  # Wider than trb (2.00)
    TRC_MAX_DAILY_LOSS_PCT: float = 10.0  # 3.3x trb (3%) — paper money, let it run
    TRC_SCALP_TARGET_PCT: float = 0.01  # 2x trb (0.005) — let winners run further
    TRB_NOLOSS_MIN_PROFIT_PCT: float = 0.0  # 2026-04-08: TECHNICALS ONLY. Was 3.0% which blocked all exits on losers.
    TRC_NOLOSS_MIN_PROFIT_PCT: float = 0.0  # 2026-04-08: TECHNICALS ONLY.
    # === CONCENTRATION CAP — prevent single-symbol overexposure ===
    MAX_SYMBOL_VALUE_TRADIER: float = 15000.0  # Max $ value per symbol. USO hit $352K, IBIT $119K — caused disaster losses.
    TRC_MAX_SYMBOL_VALUE: float = 15000.0  # trc cap (was uncapped — USO grew to $352K)
    TRB_MAX_SYMBOL_VALUE: float = 10000.0  # trb cap (smaller account)
    # === POSITION LIMITS (backtest) ===
    MAX_CONCURRENT_POSITIONS: int = 16  # BACKTEST_CHANGE_T35 total max positions across all strategies
    # === AUGMENT GUARD (parity with crypto) ===
    MIN_GAIN: float = 3.0  # NEVER augment below 3% gain — same rule as crypto
    # === L/S RATIO ENFORCEMENT (backtest) ===
    LS_RATIO_ENFORCE_TRADIER: bool = True  # BACKTEST_CHANGE_T36 enforce long/short ratio
    LS_RATIO_MIN_TRADIER: float = 0.50  # BACKTEST_CHANGE_T36 min L/S ratio
    LS_RATIO_MAX_TRADIER: float = 2.00  # BACKTEST_CHANGE_T36 max L/S ratio
    # === DAILY LOSS LIMIT (backtest) ===
    MAX_DAILY_LOSS_PCT: float = 3.0  # BACKTEST_CHANGE_T37 halt trading at 3% daily loss
    # === DAYTRADE WING — DC Breakout on lower TFs (5m/15m), open AM, flatten before close ===
    DC_DAYTRADE_ENABLED: bool = True  # Enable DC breakout daytrade system (parallel to HODL)
    DC_DAYTRADE_ACCOUNT: str = "trb"  # Account for daytrade positions
    DC_DAYTRADE_LONG_BUDGET: float = 3000.0  # Max $ exposure in daytrade longs
    DC_DAYTRADE_SHORT_BUDGET: float = 3000.0  # Max $ exposure in daytrade shorts
    DC_DAYTRADE_START_SIZE: float = 600.0  # Base order value per daytrade entry
    DC_DAYTRADE_MAX_POSITION_SIZE: float = 2000.0  # Per-symbol cap
    DC_DAYTRADE_MAX_PER_SIDE: int = 5  # Max concurrent daytrade positions per side
    DC_DAYTRADE_REQUIRE_1H_EXPANSION: bool = True  # Only trade DC breaks when 1h channel is expanding in same direction
    DC_DAYTRADE_BUFFER: float = 0.001  # Min % outside channel to confirm break (0.1%)
    DC_DAYTRADE_STOP_PCT: float = 0.015  # 1.5% hard stop for daytrades
    DC_DAYTRADE_TARGET_PCT: float = 0.01  # 1% profit target
    DC_DAYTRADE_MAX_HOLD_MINUTES: float = 240.0  # 4h max hold (flatten before close regardless)
    DC_DAYTRADE_PRE_CLOSE_MINUTES: int = 120  # Start flattening 2h before market close (14:00 ET)
    DC_DAYTRADE_STOCH_FILTER: bool = True  # Require stoch not exhausted in entry direction
    DC_DAYTRADE_K_EXHAUSTED_LONG: float = 85.0  # Don't go long if 15m K > this (chasing)
    DC_DAYTRADE_K_EXHAUSTED_SHORT: float = 15.0  # Don't go short if 15m K < this (chasing)

    # TF HIERARCHY — Tradier stocks (no 1m/3m candles, use 5m/15m)
    TF_MICRO: str = "5m"    # Fastest available for stocks
    TF_SCALP: str = "15m"   # Scalp TF for stocks
    TF_HTF1: str = "1h"     # First confirmation
    TF_HTF2: str = "4h"     # Second confirmation
    TF_HTF3: str = "D"      # Daily — strongest trend
    TF_MACRO: str = "D"     # Same as HTF3 for stocks (no weekly in live)
    # === BACKTEST-VALIDATED GATES (121 stocks, train/test confirmed) ===
    BACKTEST_VALIDATED_GATES_TRADIER: bool = True  # Block entries on signals confirmed -EV on both train+test
    DC_POSITION_ENTRY_THRESHOLD: float = 0.25  # Backtest: DC pos < 0.25 = Sharpe 18.57 on 61 test stocks (STRICT_NO_LOSS)
    MFI_FLIP_EXIT_ENABLED: bool = True  # BACKTEST_CHANGE_148: Exit when MFI exhausts (+3.91% avg vs +1.09% fixed TP, 44 trades)
    MFI_FLIP_EXIT_LONG_THRESHOLD: float = 70.0  # Exit LONG when MFI_1h > 70 (overbought = sell)
    MFI_FLIP_EXIT_SHORT_THRESHOLD: float = 30.0  # Exit SHORT when MFI_1h < 30 (oversold = cover)
    # === MARKET QUALITY + SBA + EOD (BACKTEST_CHANGE_146/147) — 11K-trade validated ===
    MARKET_QUALITY_SCORE_ENABLED_TRADIER: bool = False  # BACKTEST_CHANGE_146: Market quality scorer for stock entries
    EOD_RATIO_ENFORCE_TRADIER: bool = False  # BACKTEST_CHANGE_147: Scale down entries 30min before close, block at 5min
    SBA_ENABLED_TRADIER: bool = False  # BACKTEST_CHANGE_145: Strategic Bounce Averaging for stock hold positions
    SBA_MIN_LOSS_PCT_TRADIER: float = -3.0  # Stocks are less volatile, wider threshold than crypto -2%
    SBA_MAX_LOSS_PCT_TRADIER: float = -12.0  # Stop averaging beyond -12%
    SBA_SIZE_FRACTION_TRADIER: float = 0.25  # 25% of START_POSITION_SIZE per add (smaller than crypto 35%)
    SBA_MAX_ADDS_TRADIER: int = 2  # Max recovery adds per stock position
    SBA_COOLDOWN_S_TRADIER: int = 7200  # 2hr between adds (stocks move slower, 2x crypto's 1hr)
    SBA_ADX_MAX_TRADIER: float = 22.0  # Stocks trend more cleanly, slightly higher ADX threshold
    # === YOUTUBE CONSENSUS STRATEGIES (2026-03-27 — research: 16 verified profitable traders) ===
    # === STRATEGY ALLOCATION: trb=PROVEN only, trc=EXPERIMENTAL (paper) ===
    # --- VWAP Filter — PROVEN, on trb+trc ---
    VWAP_FILTER_ENABLED: bool = True  # LONG only above VWAP, SHORT only below
    VWAP_BOUNCE_ENTRY_ENABLED: bool = True  # Enter on VWAP bounce (pullback to VWAP + reversal)
    VWAP_BOUNCE_DIST_PCT: float = 0.3  # Price must be within 0.3% of VWAP for bounce entry
    VWAP_SCORE_BONUS: int = 10  # Score bonus when price is on correct side of VWAP
    # --- Opening Range Breakout — EXPERIMENTAL, trc only (TRC_ override enables) ---
    ORB_ENABLED: bool = False  # OFF for trb (real $). TRC overrides to True.
    ORB_WINDOW_MINUTES: int = 15
    ORB_RVOL_MIN: float = 1.5
    ORB_TARGET_MULT: float = 1.5
    ORB_STOP_MIDPOINT: bool = True
    ORB_MAX_HOLD_MINUTES: float = 150.0
    ORB_POSITION_SIZE: float = 600.0
    ORB_MAX_PER_DAY: int = 3
    ORB_LONG_BUDGET: float = 2000.0
    ORB_SHORT_BUDGET: float = 2000.0
    # --- Lunch Dead Zone — PROVEN, on trb+trc ---
    LUNCH_DEADZONE_ENABLED: bool = True
    LUNCH_DEADZONE_MODE: str = "BLOCK_MOMENTUM"
    LUNCH_DEADZONE_SIZE_MULT: float = 0.5
    # --- RVOL Gate Tightening — PROVEN, on trb+trc ---
    RVOL_MOMENTUM_MIN: float = 1.5
    RVOL_SCALP_MIN: float = 1.0
    RVOL_SCORE_BOOST_THRESHOLD: float = 2.0
    RVOL_SCORE_BOOST_PCT: float = 0.20
    # --- 9/21 EMA — PROVEN, on trb+trc ---
    EMA_9_21_FILTER_ENABLED: bool = True
    EMA_9_21_TIMEFRAME: str = "5m"
    EMA_9_21_SCORE_BONUS: int = 5
    # --- TTM Squeeze — EXPERIMENTAL, trc only ---
    SQUEEZE_ENABLED: bool = False  # OFF for trb. TRC overrides to True.
    SQUEEZE_SCORE_BONUS: int = 15
    # --- Episodic Pivot — EXPERIMENTAL, trc only ---
    EPISODIC_PIVOT_ENABLED: bool = False  # OFF for trb. TRC overrides to True.
    EP_MIN_GAP_PCT: float = 5.0
    EP_MIN_VOL_MULT: float = 3.0
    EP_POSITION_SIZE: float = 800.0
    EP_MAX_CONSOLIDATION_DAYS: int = 8
    EP_MAX_RETRACE_PCT: float = 25.0
    # --- Bounce-Top Exit — EXPERIMENTAL, trc only (exits losers = risky) ---
    # Note: BOUNCE_TOP_EXIT_ENABLED above applies to trb. TRC override below.
    # --- TRC Overrides: enable ALL experimental strategies on paper account ---
    TRC_ORB_ENABLED: bool = True  # ORB on paper only
    TRC_EPISODIC_PIVOT_ENABLED: bool = True  # EP on paper only
    TRC_SQUEEZE_ENABLED: bool = True  # Squeeze on paper only
    TRC_MOMENTUM_FADE_ENABLED: bool = True  # Re-enable momentum fade on paper
    TRC_ORB_POSITION_SIZE: float = 1980.0
    TRC_EP_POSITION_SIZE: float = 2640.0
    TRC_ORB_LONG_BUDGET: float = 6600.0
    TRC_ORB_SHORT_BUDGET: float = 6600.0
    # === CONTRARIAN SPIKE FADE (BC_161 — 2026-03-30, 24 stocks × 2yr, +480%, 67% WR, 4.69 W/L) ===
    # SHORT big spikers (>2% in 30min + K>70), LONG big fallers (<-2% in 30min + K<30)
    # 2/3 WT must confirm fade direction. Exit on 2/3 WT against + mandatory reentry.
    SPIKE_FADE_ENABLED: bool = True
    SPIKE_FADE_THRESHOLD_PCT: float = 2.0  # Min % move in lookback to qualify as spike
    SPIKE_FADE_LOOKBACK_BARS: int = 6  # 6 bars × 5m = 30min lookback
    SPIKE_FADE_K_EXHAUSTION: float = 70.0  # K5m must be > this (spike up) or < 100-this (spike down)
    SPIKE_FADE_POSITION_SIZE: float = 600.0  # Per-entry size
    SPIKE_FADE_MAX_POSITIONS: int = 10  # Max concurrent spike fade positions
    SPIKE_FADE_COOLDOWN_BARS: int = 6  # Min bars between entries on same symbol
    # === 2.5σ STDEV BREAKOUT (HTF breakout + LTF retest scaling) ===
    # Stocks: same logic as crypto but with stock-tuned thresholds
    STDEV_BREAKOUT_ENABLED: bool = False  # Kill switch OFF — backtest sweep first
    STDEV_BREAKOUT_PCTB_LONG: float = 1.125  # bb_pctb threshold for LONG breakout (2.5σ)
    STDEV_BREAKOUT_PCTB_SHORT: float = -0.125  # bb_pctb threshold for SHORT breakout (2.5σ)
    STDEV_BREAKOUT_HTF_LIST: List[str] = field(default_factory=lambda: ["D", "4h"])
    STDEV_BREAKOUT_RETEST_TF_LIST: List[str] = field(default_factory=lambda: ["1h", "15m"])
    STDEV_BREAKOUT_RETEST_PCTB_MIN: float = 0.85
    STDEV_BREAKOUT_RETEST_PCTB_MAX: float = 1.05
    STDEV_BREAKOUT_MAX_RETESTS: int = 3
    STDEV_BREAKOUT_RETEST_SIZE_MULT: float = 1.5
    STDEV_BREAKOUT_COOLDOWN: float = 600.0
    STDEV_BREAKOUT_RETEST_COOLDOWN: float = 300.0
    STDEV_BREAKOUT_SCORE: int = 25
    STDEV_BREAKOUT_RETEST_SCORE: int = 22
    STDEV_BREAKOUT_RVOL_MIN: float = 1.2
    STDEV_BREAKOUT_MAX_AGE_BARS: int = 50
    STDEV_BREAKOUT_EXIT_PCTB_FAIL: float = 0.75
    STDEV_BREAKOUT_EXIT_WT_ENABLED: bool = True
    # === MOMENTUM INTERCEPTION (MI) — Early exit/entry via slowing deltas, LH/LL structure, divergence ===
    MI_EXIT_ENABLED_TRADIER: bool = False  # Master switch — OFF until sweep-proven
    MI_ENTRY_ENABLED_TRADIER: bool = False  # Entry scoring bonus for favorable MI signals
    MI_STRUCT_EXIT_ENABLED_TRADIER: bool = True  # WT peak LH / trough HL = structural weakening
    MI_EXHAUST_EXIT_ENABLED_TRADIER: bool = True  # EXHAUST_UP/DOWN on 1h/4h
    MI_DIV_EXIT_ENABLED_TRADIER: bool = True  # Divergence on 1h/4h
    MI_VELOCITY_EXIT_ENABLED_TRADIER: bool = True  # Velocity declining across 2+ TFs
    MI_WAVE_EXIT_ENABLED_TRADIER: bool = True  # Wave phase CONTRACTING on 1h
    MI_TF_AGREE_MIN_TRADIER: int = 3  # Minimum sub-signals to trigger MI exit
    MI_MIN_GAIN_EXIT_TRADIER: float = 0.50  # Stocks: higher min gain (0.50%)
    MI_ENTRY_STRUCT_BONUS_TRADIER: int = 10  # Score bonus for favorable structure on entry
    MI_ENTRY_EXHAUST_BONUS_TRADIER: int = 8  # Score bonus for opposing TF exhaustion on entry
    # === WT/DC DATA-DRIVEN SCORERS (2026-04-08 — OOS: Sharpe 11.46, 74.8% WR, PF 8.64x) ===
    WT_DC_ENTRY_THRESHOLD: float = 55  # V8 ABLATION 2026-04-13: Sharpe 1.276 (best of 35/55/75/95). Was 80.
    WT_DC_EXIT_THRESHOLD: float = 30  # SERVER 204: exit>=25 optimal across all entry thresholds
    # === EXIT PATH SWITCHES (2026-04-08 — scorer is SOLE authority, all legacy paths OFF) ===
    # To re-enable any path: set to True, restart tradier_manage
    EXIT_K5M_BOUNCE_ENABLED: bool = False       # K5M stoch bounce turn + low break. Was closing on 5m noise.
    EXIT_HARD_DROP_5M_ENABLED: bool = False      # Price < prev 5m low. Too aggressive — kills options on minor dips.
    EXIT_ALGO_SCORE_ENABLED: bool = False        # Old calculate_signal_score exit. Bypassed scorer, closed PLTR.
    EXIT_STRUCT_BREAK_5M_ENABLED: bool = False   # 5m LH/HL structure exit. Too noisy for swing/options.
    EXIT_IBS_EXHAUSTION_ENABLED: bool = False    # Internal Bar Strength extreme. Minor signal, not worth standalone exit.
    EXIT_SENTIMENT_ENABLED: bool = False         # Sentiment collapse exit. Unreliable signal source.
    EXIT_MI_ENABLED: bool = False                # Momentum Interception sub-signals. Tested: marginal value.
    EXIT_CONV_FAIL_ENABLED: bool = False         # Convergence failure early exit. Was closing at tiny gains.
    EXIT_BOUNCE_TOP_ENABLED: bool = False        # Bounce-top loss exit. Percentage-based in disguise.
    EXIT_HTF_QUICK_TP_ENABLED: bool = True       # HTF Quick TP: 1h exhausted + LTFs turning + 4h intact. KEEP — proven.
    EXIT_STRUCT_DC_BREAK_ENABLED: bool = True    # DC structural break (multi-TF). KEEP — catches real breakdowns.
    EXIT_MAX_HOLD_ENABLED: bool = False          # Max hold timeout. OFF — technicals decide, not clocks.
    EXIT_MAX_HOLD_MINUTES: float = 99999         # If enabled: max minutes before force-close.
    # === DELTA ENGINE — FINAL WINNERS (2026-04-09, full sweeps) ===
    # Stocks ST WINNER: Sharpe 0.710, WR 77.7%, 100% profitable (24 diverse stocks, 2yr)
    # Entry: mtf=3, ez=2.5, ea=0.3, tz=1.5, htf=4h, cd=60 | Exit: wt_cross on 15m, max_hold=30
    # Stocks LT WINNER: Sharpe 0.487, WR 70.7%, 100% profitable
    # Entry: mtf=2, ez=2.0, htf=4h_D, cd=120 | Exit: combined_wt_speed on 4h, sp=50, max_hold=240
    # Stocks Broad (121 sym): Sharpe 0.485, WR 70.7%, mtf=2, ez=2.5, tw=equal
    DELTA_ENGINE_ENABLED: bool = True  # 121 sym/2yr: Sharpe 0.038→0.485, WR 51%→70.7%, 100% profitable
    DELTA_ENTRY_ENABLED: bool = False  # T25 sweep: False avg=0.527 vs True=0.507 (-4%). Best tested.
    DELTA_EXIT_ENABLED: bool = True  # Re-enabled — real fix is in REENTRY_MONITOR (checks exit score before reopen)
    # WT_DC scorer exit guards
    WT_DC_EXIT_STALE_MAX_S: int = 600  # Don't exit on indicators > 10min stale (protects against stale data firing exits)
    DELTA_PYRAMID_ENABLED: bool = False
    DELTA_SPEED_SMOOTH: int = 5  # WINNER: sm=5
    DELTA_ACCEL_LOOKBACK: int = 5
    DELTA_TF_WEIGHTS: dict = None  # Set in __post_init__
    DELTA_TF_Z_THRESHOLD: float = 1.5  # WINNER: tz=1.5
    DELTA_ENTRY_Z_THRESHOLD: float = 2.5  # WINNER: ez=2.5
    DELTA_ENTRY_ACCEL_THRESHOLD: float = 0.3  # WINNER ST: ea=0.3 (LT: 0.0)
    DELTA_ENTRY_MIN_TF: int = 3  # WINNER ST: mtf=3 (LT: 2)
    DELTA_EXIT_DECAY_RATIO: float = 0.30  # WINNER: sp=70 → 30% decay from peak (stocks ST sweep #1)
    DELTA_EXIT_TF: str = "15m"  # WINNER ST: 15m exit TF (sweep top 8 all used 15m)
    DELTA_EXIT_TYPE: str = "speed_decay"  # WINNER ST: speed_decay sp=70 Sharpe 0.710 WR 77.7% (corrected from wt_cross)
    DELTA_EXIT_ACCEL_THRESHOLD: float = -0.1
    DELTA_EXIT_OPPOSING_RATIO: float = 1.5
    DELTA_EXIT_MIN_TF_LOST: int = 2
    DELTA_EXIT_MIN_HOLD: int = 4
    DELTA_PYRAMID_MAX: int = 8
    DELTA_PYRAMID_MIN_BARS: int = 8
    DELTA_PYRAMID_PRICE_TOL: float = 0.02
    DELTA_PYRAMID_QTY_MULT: float = 1.5
    DELTA_PYRAMID_ACCEL_THRESHOLD: float = 0.2
    DELTA_Z_WINDOW: int = 200
    DELTA_MAX_HOLD_BARS: int = 0  # DISABLED — ride winners until technicals turn. No fixed time exits.
    DELTA_COOLDOWN_BARS: int = 60  # WINNER ST: 60 bars (5h)
    DELTA_HTF_GATE: str = "4h"  # WINNER ST: 4h must confirm (LT: 4h_D)
    DELTA_ATR_ENTRY_FILTER: bool = True  # ATR filter on for stocks
    # Stocks LT overrides (for longer holds, ez_positions_quick can switch to these)
    DELTA_LT_ENTRY_MIN_TF: int = 2
    DELTA_LT_ENTRY_Z_THRESHOLD: float = 2.0
    DELTA_LT_ENTRY_ACCEL_THRESHOLD: float = 0.0
    DELTA_LT_EXIT_TF: str = "4h"
    DELTA_LT_EXIT_TYPE: str = "combined_wt_speed"
    DELTA_LT_EXIT_SPEED_PCT: int = 50
    DELTA_LT_COOLDOWN_BARS: int = 120  # ~10h
    DELTA_LT_HTF_GATE: str = "4h_D"
    # STRUCTURAL RANGE SHIFT EXIT (stocks) — hold losers, cut at 4h DC boundary when range shifts
    STRUCTURAL_RANGE_SHIFT_EXIT: bool = True  # bb_4h Apr-13 DISASTER avg -0.95% 16%WR. bb_1h is correct for stocks (user directive). dc_4h is for crypto only.
    STRUCTURAL_RANGE_SHIFT_TF: str = "bb_1h"  # STOCKS: bb_1h (user directive — bb_upper_1h/bb_lower_1h). CRYPTO: dc_4h. bb_4h was wrong and caused April-13 losses.
    # Cascade params (stocks) — same knobs as crypto unless overridden
    STRUCTURAL_RANGE_SHIFT_K_HIGH: float = 75.0  # T25 sweep: 75.0 best tested (was 80.0 user directive)
    STRUCTURAL_RANGE_SHIFT_K_LOW: float = 25.0   # T25 sweep: 25.0 best tested (was 20.0 user directive)
    STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS: float = 100.0
    # === RED ZONE (stocks) — structural levels with HTF confirmation ===
    RZ_ENTRY_ENABLED: bool = True
    RZ_EXIT_ENABLED: bool = False  # T25 sweep: False avg=0.548 vs True=0.481 (-12%). Best tested.
    RZ_TOP_BB_THRESHOLD: float = 0.85
    RZ_BOT_BB_THRESHOLD: float = 0.15
    RZ_LEGS_MIN: float = 20.0
    RZ_REQUIRE_STRUCT: bool = False
    RZ_K_EXIT: float = 80.0  # Stocks: exit long when k_1h > 80
    RZ_MFI_EXIT: float = 85.0
    RZ_K_ENTRY_MAX: float = 50.0  # Stocks: enter long only when k_1h < 50
    # Stock-specific RZ tuning — needs to differ from crypto since base TF is 5m not 3m,
    # intraday volatility is much smaller, and bars/day is RTH-limited (78 vs 480).
    RZ_LTF_MICRO: str = "5m"  # Stocks: 5m base; crypto uses 3m
    RZ_BASELINE_TOL: float = 0.05  # Stocks: 5% proximity to mean (vs crypto 3%)
    # ═══ STOCK HOLD MINIMUM — user directive 2026-04-10 ═══
    # "STOCKS CAN [get into a loss briefly] THEY ARE HELD AT LEAST 4H OR SO".
    # Stocks are swing trades, not scalps. Must wait for HTF (1h/4h/D) delta slowdown
    # before considering any exit. Below this hold time, return HOLD regardless.
    TRADIER_MIN_HOLD_MINUTES: float = 240.0  # 4 hours minimum hold
    # ═══ STOCK DELTA EXIT TF WEIGHTS — HTF only ═══
    # Stocks exit ONLY on 1h/4h/D slowdown. LTF (5m/15m) noise must NOT move the
    # delta speed calculation. This dict is passed to DeltaTracker.tf_weights.
    DELTA_TF_WEIGHTS_STOCK: dict = None  # set in __post_init__
    # ═══ REENTRY — user directive "reenter ASAP" ═══
    TRADIER_REOPEN_WAIT_S: float = 0.0       # Was 300s (5 min); zero for instant reentry
    # === OPTIONS-SPECIFIC OVERRIDES (when position is in trb_long/trb_short options) ===
    DELTA_OPTIONS_ENTRY_Z: float = 3.0  # Stricter: ez=3.0 for options (wider spreads)
    DELTA_OPTIONS_HTF_GATE: str = "4h_D"  # Both 4h AND D must confirm
    DELTA_OPTIONS_COOLDOWN: int = 120  # 10h between trades
    DELTA_OPTIONS_MAX_HOLD: int = 240  # 20h max hold
    DELTA_OPTIONS_EXIT_TYPE: str = "giveback"  # V2 sweep: giveback wins for options
    DELTA_OPTIONS_GIVEBACK_PCT: float = 30.0  # Close when 30% of max gain given back
    # Legacy (superseded by scorers but kept for V8 sweep compatibility)
    WT_EXIT_TFS_TRADIER: str = "5m+15m+1h+4h+D"
    WT_EXIT_MIN_TFS_TRADIER: int = 5
    WT_EXIT_VELOCITY_TRADIER: bool = False  # SWEEP: velocity makes zero difference. Cross is simpler.
    MIN_HOLD_BARS_TRADIER: int = 32  # Grace period only (160min). Actual avg hold = 239 bars (20hrs) — WT exit rides the full wave.
    COOLDOWN_BARS_TRADIER: int = 8  # 2026-04-08 SWEEP: 8 bars (40min) → Sharpe 8.22 (+1.30 vs 0 cooldown). Was 16 (80min).
    # === YOUTUBE STRATEGIES (2026-03-27) — DISABLED on trb 2026-03-30 ===
    # These were implemented from YouTube research with FAKE backtests (reimplemented logic, not real functions).
    # "Sharpe 5.17" etc were fabricated numbers. Connors RSI augmented MRVL at -6.74% on real money.
    # MUST be validated via V5 backtest with real evaluate functions before re-enabling on trb.
    # Paper-only on trc via TRC_ overrides below.
    # --- Clenow Exp Regression — DISABLED on trb, paper on trc ---
    CLENOW_ENABLED: bool = False  # DISABLED 2026-03-30: fake backtest Sharpe. Needs V5 validation.
    CLENOW_LOOKBACK: int = 90  # Regression window (Clenow default)
    CLENOW_TOP_N: int = 20  # Buy top N% of ranked symbols
    CLENOW_POSITION_SIZE: float = 800.0  # Per-entry size
    CLENOW_REBALANCE_DAYS: int = 21  # Monthly rebalance
    CLENOW_MIN_SCORE: float = 5.0  # Min score (slope * R²) to qualify
    CLENOW_REGIME_FILTER: bool = True  # Only hold when SPY > SMA200
    # --- Smart Money Flow Index — DISABLED on trb, paper on trc ---
    SMFI_ENABLED: bool = False  # DISABLED 2026-03-30: fake backtest Sharpe. Needs V5 validation.
    SMFI_POSITION_SIZE: float = 600.0
    SMFI_MAX_HOLD_DAYS: int = 10  # Exit when price > 20SMA or 10d hold
    SMFI_LONG_BUDGET: float = 3000.0
    SMFI_SHORT_BUDGET: float = 3000.0
    SMFI_MAX_PER_SIDE: int = 5  # Max concurrent SMFI positions per side
    # --- Minervini SEPA Screen — DISABLED on trb, paper on trc ---
    MINERVINI_ENABLED: bool = False  # DISABLED 2026-03-30: fake backtest Sharpe. Needs V5 validation.
    MINERVINI_POSITION_SIZE: float = 800.0
    MINERVINI_MIN_SEPA_SCORE: int = 5  # Need 5 of 6 conditions
    MINERVINI_MAX_HOLD_DAYS: int = 40  # Swing trade hold
    MINERVINI_TARGET_PCT: float = 25.0  # Take profit at 25%
    MINERVINI_LONG_BUDGET: float = 4000.0
    # --- Connors RSI Composite — DISABLED on trb, paper on trc ---
    CONNORS_RSI_ENABLED: bool = False  # DISABLED 2026-03-30: augmented MRVL at -6.74% on real money. Needs V5 validation.
    CONNORS_RSI_ENTRY_THRESHOLD: float = 10.0  # Buy when CRSI < 10
    CONNORS_RSI_EXIT_THRESHOLD: float = 70.0  # Sell when CRSI > 70
    CONNORS_RSI_POSITION_SIZE: float = 600.0
    CONNORS_RSI_MAX_HOLD_DAYS: int = 20
    # --- VIX Regime Filter (Sharpe 2.00) — overlay on ALL entries, trb+trc ---
    VIX_REGIME_FILTER_ENABLED: bool = True  # Block entries when SPY < SMA200
    # --- TRC overrides for backtest winners ---
    TRC_CLENOW_ENABLED: bool = True  # Paper-only: needs V5 validation before trb
    TRC_SMFI_ENABLED: bool = True  # Paper-only: needs V5 validation before trb
    TRC_MINERVINI_ENABLED: bool = True  # Paper-only: needs V5 validation before trb
    TRC_CONNORS_RSI_ENABLED: bool = True  # Paper-only: needs V5 validation before trb
    TRC_CLENOW_POSITION_SIZE: float = 2640.0
    TRC_SMFI_POSITION_SIZE: float = 1980.0
    TRC_MINERVINI_POSITION_SIZE: float = 2640.0
    TRC_CONNORS_RSI_POSITION_SIZE: float = 1980.0
    TRC_SMFI_LONG_BUDGET: float = 9900.0
    TRC_SMFI_SHORT_BUDGET: float = 9900.0
    TRC_MINERVINI_LONG_BUDGET: float = 13200.0

    # ========================================================================
    # --- 10b. RECONNECTED STRATEGY SWITCHES (2026-04-14) ---
    # All 28 switches below were previously tested by backtest_v8_sweep.py but
    # had NO live implementation (verified against all local + server backups).
    # Re-declared here per user directive 2026-04-14 under HANDS_OFF + DEATH
    # PENALTY anti-revert rule. Each has corresponding entry/exit wiring in
    # tradier_manage.py (search by switch name for the gate site).
    # ========================================================================

    # First-Hour Momentum (FH) — opening-range breakout detection, minutes 0-60
    # after market open. KB: Sharpe 1.36-1.54, 25/25 profitable pre-revert.
    TRADIER_FH_MOMENTUM_ENABLED: bool = True
    TRADIER_FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5       # min gap move % to qualify
    TRADIER_FH_MOMENTUM_DC_CONFIRM: bool = True         # require DC breakout confirm
    TRADIER_FH_MOMENTUM_DC_MAX_LONG: float = 0.33       # only longs in bottom third of DC range
    TRADIER_FH_MOMENTUM_MFI_CONFIRM: bool = True        # require MFI > threshold confirm
    TRADIER_FH_MOMENTUM_MFI_MIN: float = 55.0           # min MFI for FH long entry
    TRADIER_FH_MOMENTUM_WINDOW_MINUTES: int = 60        # FH window in minutes after 13:30 UTC

    # Momentum Interception (MI) — 5 sub-signal momentum degradation detector
    TRADIER_MI_ENTRY_ENABLED_TRADIER: bool = False      # wait for MI reset before entry
    TRADIER_MI_EXIT_ENABLED_TRADIER: bool = False       # close when MI detects intercept down
    TRADIER_MI_SUBSIGNAL_MIN_COUNT: int = 3             # N of 5 sub-signals must fire

    # DC Daytrade — buy DC upper-quarter breakouts on 5m/15m with 1h expansion
    TRADIER_DC_DAYTRADE_ENABLED: bool = True
    TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES: int = 240
    TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION: bool = True
    TRADIER_DC_DAYTRADE_STOP_PCT: float = 0.005         # 0.5% hard stop
    TRADIER_DC_DAYTRADE_TARGET_PCT: float = 0.005       # 0.5% target (winner per 100.md:1352)
    TRADIER_DC_POSITION_ENTRY_THRESHOLD: float = 0.25   # T4 sweep: 0.25 avg=10.230 best tested (was 0.2)

    # K-Zone — stochastic K-zone entry filter
    TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER: int = 80     # T4 sweep: 80 avg=6.563 vs 35=5.567 (+18%). Best tested.
    TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER: int = 20    # T4 sweep: 20 avg=6.216 vs 65=5.681 (+10%). Best tested.
    TRADIER_K_ZONE_ENTRY_BONUS_TRADIER: int = 25        # score add when K in zone

    # RSI2 — 2-period RSI exit gate
    TRADIER_RSI2_ENABLED: bool = True
    TRADIER_RSI2_EXIT_THRESHOLD_LONG: float = 90.0      # exit long when RSI2 > this
    TRADIER_RSI2_EXIT_THRESHOLD_SHORT: float = 10.0     # exit short when RSI2 < this

    # RSI Entry — SHORTS ONLY (2026-04-14 rule). Longs use MFI only.
    # Short side: RSI + relative volume gate (high short volume distorts MFI).
    TRADIER_RSI_ENTRY_LONG_TRADIER: float = -1.0        # SENTINEL: <0 => DISABLED (long uses MFI)
    TRADIER_RSI_ENTRY_SHORT_TRADIER: float = 70.0       # RSI > this to consider short
    TRADIER_RSI_SHORT_REL_VOLUME_MIN: float = 1.2       # relative vol > 1.2× avg required

    # MFI Entry — LONGS ONLY (2026-04-14 rule, companion to RSI-shorts rule)
    TRADIER_MFI_ENTRY_LONG_TRADIER: float = 60.0        # MFI > this for long entry
    TRADIER_MFI_ENTRY_LONG_ENABLED: bool = True

    # Stoch entry filters (non-K-zone)
    TRADIER_STOCH_ENTRY_LONG_TRADIER: int = 30          # K < this for normal long entry
    TRADIER_STOCH_ENTRY_SHORT_TRADIER: int = 70         # K > this for normal short entry
    TRADIER_STOCH_EXTREME_LONG_TRADIER: int = 15        # deeper K for high-conviction long
    TRADIER_STOCH_EXTREME_SHORT_TRADIER: int = 85       # deeper K for high-conviction short

    # WaveTrend composite scoring + exit TF config
    TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER: bool = True
    TRADIER_WT_EXIT_TFS_TRADIER: str = "5m+15m+1h+4h+D"  # T4 sweep: 5m+15m+1h+4h+D avg=5.961 best tested (was "3m,15m,1h")
    TRADIER_WT_EXIT_MIN_TFS_TRADIER: int = 4             # T4 sweep: 4 avg=5.889 best tested (was 2)

    # Entry score aggregate threshold
    TRADIER_ENTRY_SCORE_THRESHOLD: int = 24             # min aggregate signal score for entry
    # ========================================================================
    # --- END RECONNECTED SWITCHES ---
    # ========================================================================

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
        if self.DELTA_TF_WEIGHTS is None:
            self.DELTA_TF_WEIGHTS = {"5m": 2.0, "15m": 3.0, "1h": 2.0, "4h": 1.0, "D": 0.5}  # V2 sweep winner: 15m dominant
        if self.DELTA_TF_WEIGHTS_STOCK is None:
            # User directive 2026-04-10: stocks exit ONLY on 1h/4h/D slowdown.
            # LTF (5m/15m) excluded from delta computation so intraday noise can't fire exits.
            self.DELTA_TF_WEIGHTS_STOCK = {"1h": 2.0, "4h": 3.0, "D": 2.0}
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

    # ═══ PER-SYMBOL REGIME OVERRIDES (rolling_config_optimizer → Redis → here) ═══
    _REGIME_OVERRIDES: ClassVar[Dict[str, Dict]] = {}
    _REGIME_REDIS_CACHE: ClassVar[Dict[str, Dict]] = {}

    def get_symbol_setting(self, account_key: str, position_key: str, setting_name: str):
        """Hot-path config lookup: regime override → global default.
        Checks in-process _REGIME_OVERRIDES first, then Redis cache (refreshed every 5s)."""
        pk = position_key if ":" not in position_key else position_key.split(":", 1)[1]
        full_key = f"{account_key}:{pk}"
        regime = self._REGIME_OVERRIDES.get(full_key)
        if regime and setting_name in regime and not regime.get("_paper", False):
            return regime[setting_name]
        regime = self._get_regime_from_redis(full_key)
        if regime and setting_name in regime and not regime.get("_paper", False):
            return regime[setting_name]
        return getattr(self, setting_name, None)

    @classmethod
    def _get_regime_from_redis(cls, full_key: str) -> Optional[Dict]:
        """Load single regime override from Redis. 5s cache per key."""
        import time as _time
        now = _time.time()
        cached = cls._REGIME_REDIS_CACHE.get(full_key)
        if cached and now - cached.get("_cache_ts", 0) < 5.0:
            return cached
        try:
            import redis as _redis
            import json as _json
            r = _redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=1)
            raw = r.get(f"regime_cfg:{full_key}")
            if raw:
                data = _json.loads(raw)
                data["_cache_ts"] = now
                cls._REGIME_REDIS_CACHE[full_key] = data
                return data
        except Exception:
            pass
        return None

    @classmethod
    def set_regime_override(cls, position_key: str, overrides: Dict[str, object], source: str = "regime"):
        """Called by rolling_config_optimizer to hot-inject per-symbol config. position_key = 'trb:NVDA_LONG'."""
        cls._REGIME_OVERRIDES[position_key] = overrides

    @classmethod
    def clear_regime_override(cls, position_key: str):
        cls._REGIME_OVERRIDES.pop(position_key, None)

    @classmethod
    def get_all_regime_overrides(cls) -> Dict[str, Dict]:
        return dict(cls._REGIME_OVERRIDES)

    @property
    def api_url(self) -> str:
        return self.TRADIER_SANDBOX_URL if self.USE_SANDBOX else self.TRADIER_API_BASE_URL