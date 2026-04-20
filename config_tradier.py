# ═══════════════════════════════════════════════════════════════════════
# SWEEP REFERENCE: data/sweep_tiers.json → "tradier" section — prioritized
# switches with ranges and tiers. Agents: read that file before sweeping.
# KEY RULE: Stock params are OPPOSITE to crypto. NEVER copy between them.
#   entry_score=24 (not 18), stoch_gate=60 (not 50), HTF_align>=2 (not 1),
#   MFI only (never RSI), ATR_TRAIL=OFF (#1 PnL destroyer).
#   SRS TF = bb_1h (NEVER bb_4h or dc_4h — caused April-13 disaster).
# ═══════════════════════════════════════════════════════════════════════
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional
from weakref import WeakSet
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
    SWING_MAX_POSITION_SIZE: float = 2000.0  # Per-symbol cap for swing ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    SWING_START_SIZE: float = 800.0          # Base order value for swing ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
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
    ACCOUNT_SIDE_MAPPING: Dict[str, List[str]] = field(default_factory=lambda: {"tra": ["LONG"]})  # DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
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
    TRA_PREFERRED_SYMBOLS: List[str] = field(default_factory=lambda: ["AAPL", "MSFT", "GOOGL", "MSTR", "PLTR", "NEM", "MU", "SNDK", "NVDA"])  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
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
    # === OPTIONS EXIT THRESHOLDS (was -80/-60/-40 — too lenient; losers bled Apr 13-16) ===
    OPTIONS_MAX_LOSS_PCT_DTE_30: float = -40.0   # DTE > 30: exit when down 40%+ (was -80) ; WIRED 2026-04-16 (priority 85/100) — tradier_options_analyzer.py:1244 (audit miss)
    OPTIONS_MAX_LOSS_PCT_DTE_14: float = -30.0   # 14 < DTE <= 30: exit when down 30%+ (was -60) ; WIRED 2026-04-16 (priority 85/100) — tradier_options_analyzer.py:1245 (audit miss)
    OPTIONS_MAX_LOSS_PCT_DTE_LOW: float = -20.0  # DTE <= 14: exit when down 20%+ (was -40) ; WIRED 2026-04-16 (priority 85/100) — tradier_options_analyzer.py:1246 (audit miss)
    # WT-velocity (NOT greeks delta) acceleration exit:
    # CALL exits if wt_velocity_D flips negative AND |velocity| grows bar-over-bar.
    # PUT exits if wt_velocity_D flips positive AND velocity grows bar-over-bar.
    OPTIONS_WT_ACCEL_MIN_ABS: float = 10.0       # min |wt_velocity_D| to count as "accelerating"
    OPTIONS_WT_ACCEL_GROWTH_PCT: float = 25.0    # velocity must grow at least 25% bar-over-bar
    # Support/resistance break exit — symmetric to DC-High Reversal rule on stocks side
    OPTIONS_LEVEL_BREAK_BUFFER: float = 0.01     # 1% buffer past dc_low_D (call) / dc_high_D (put)
    OPTIONS_LEVEL_BREAK_MIN_DTE: int = 14        # Don't fire on sub-14-DTE (noise dominates)
    # Continuous sector/put-call enforcement (applied in daily + premarket cycles)
    OPTIONS_CONTINUOUS_SECTOR_GATE: bool = True  # Block new buys that widen existing sector/group/symbol violation
    OPTIONS_USER_CANCEL_COOLDOWN_HOURS: float = 4.0  # Don't re-propose a user-canceled OCC for N hours
    # === CASH-SECURED PUT (CSP) STRATEGY — SELL SIDE ===
    # For LONG-thesis candidates, compare buying a call vs selling a cash-secured put.
    # Seller collects premium (theta-positive), wins in flat/up tape; assigned stock at strike if ITM.
    # Naked calls disabled at config level — CSP only in v1.
    OPTIONS_CSP_ENABLED: bool = False            # Master switch — keep False until backtest + forward-test proven
    OPTIONS_CSP_NAKED_CALL_ENABLED: bool = False # HARD-disabled. Unlimited upside risk. Never flip without Level-4 margin + explicit approval.
    OPTIONS_CSP_MIN_IV_RANK: float = 40.0        # Only sell premium when IV rank >= 40 (rich premium)
    OPTIONS_CSP_MAX_DELTA: float = 0.30          # Max |delta| on the put sold (30Δ ≈ 70% win rate empirically)
    OPTIONS_CSP_MIN_DELTA: float = 0.15          # Min |delta| — don't sell puts too far OTM (premium too thin)
    OPTIONS_CSP_DTE_MIN: int = 60                # Min days-to-expiry — at least 2 months ahead (time-premium strategy)
    OPTIONS_CSP_DTE_MAX: int = 90                # Max DTE (3 months — keeps liquidity + balances theta capture)
    # Time-decay capture: open long-dated (60-90 DTE), close on profit target or max hold days
    OPTIONS_CSP_PROFIT_TARGET_PCT: float = 0.50  # Close at 50% of premium collected (≈ 2-week avg hold on 60-DTE position)
    OPTIONS_CSP_MAX_HOLD_DAYS: int = 21          # Force close after 21 days open regardless (~50% through a 60-DTE window)
    OPTIONS_CSP_MAX_CAPITAL_PCT: float = 0.30    # Max fraction of available cash tied up in CSPs at once
    OPTIONS_CSP_MIN_EXTRINSIC_PCT: float = 0.015 # Min extrinsic value as % of strike (1.5%) — premium must be worth it
    OPTIONS_CSP_EDGE_MARGIN: float = 1.15        # Sell-structure must beat buy-structure edge by 15% to be picked
    # ── HARD ACCOUNT-WIPEOUT CAP ──
    # Per-CSP worst-case exposure (strike × 100 × qty, i.e. full assignment if stock → 0)
    # MUST NOT exceed this fraction of total_equity. Enforced at 3 layers: analyzer filter,
    # agent pre-trade gate, monitor audit alarm. On a $70k account, 0.03 = $2,100 notional cap.
    OPTIONS_CSP_MAX_POS_PCT_OF_ACCOUNT: float = 0.03
    # === NON-SKIPPABLE RISK MONITOR — SOLD POSITIONS ===
    # Background daemon (launchd) runs every N seconds. NO config flag disables it.
    # Layered defense: soft close on (P&L down + technicals) + ABSOLUTE cuts that bypass
    # everything when an underlying move threatens account-wipeout (deep ITM, gap crash).
    # Matches btc_crash_safety_net.py pattern — state machine, state file, auto-restart.
    OPTIONS_CSP_MONITOR_POLL_SEC: int = 60                # Poll interval in seconds
    # Soft gate — lets positions breathe through IV/price noise
    OPTIONS_CSP_MONITOR_LOSS_TRIGGER_PCT: float = -0.20   # Arm close gate only on 20%+ premium drawdown (was -5%)
    OPTIONS_CSP_MONITOR_MAX_LOSS_PCT: float = -1.50       # Hard premium cut: pnl <= -150% (buy-back costs 2.5x premium) — catastrophic only
    OPTIONS_CSP_MONITOR_REQUIRE_WT_D_TURN: bool = True    # Require wt_D turn against position to confirm soft close
    OPTIONS_CSP_MONITOR_LOG_EVERY_TICK: bool = True       # Log every poll for audit trail (required for non-skippable)
    # ── ABSOLUTE WIPEOUT GUARDS — BYPASS ALL OTHER GATES ──
    # Fire unconditionally (no technical filter) to prevent account wipeout from catastrophic underlying moves.
    # For SHORT PUT: underlying dropping ITM + through strike = assignment loss grows linearly with further drop.
    # For SHORT CALL (disabled v1): underlying rising above strike = unlimited upside loss.
    OPTIONS_CSP_MONITOR_STRIKE_BREACH_PCT: float = 0.05   # SHORT PUT: close if underlying drops 5%+ BELOW strike (put is 5% ITM)
    OPTIONS_CSP_MONITOR_GAP_FROM_ENTRY_PCT: float = 0.15  # SHORT PUT: close if underlying drops 15%+ from entry spot (catches gap-down / earnings crash)
    OPTIONS_CSP_MONITOR_CALL_BREACH_PCT: float = 0.05     # SHORT CALL: close if underlying rises 5%+ ABOVE strike (disabled v1 but gate wired)
    OPTIONS_CSP_MONITOR_CALL_GAP_FROM_ENTRY_PCT: float = 0.15  # SHORT CALL: close on 15%+ upside gap from entry
    # Correlated-event emergency: if N+ positions all breach absolute guards in same tick, escalate alert
    OPTIONS_CSP_MONITOR_CORRELATED_BREACH_N: int = 3      # N positions breaching simultaneously triggers emergency log/alert
    # === BULL PUT CREDIT SPREAD STRATEGY (Tier 1 — primary bullish structure) ===
    # 7yr backtest (2019-2026, 15 syms daily): Sharpe 0.62, Win 80%, worst year (2022) -$1.3k.
    # Defined-risk, crisis-resistant, scales to 10-15 concurrent positions.
    OPTIONS_SPREAD_ENABLED: bool = True           # Master gate — flip when ready
    OPTIONS_SPREAD_WIDTH: float = 10.0             # $ between short and long strike
    OPTIONS_SPREAD_SHORT_DELTA: float = 0.25       # Short-put target delta
    OPTIONS_SPREAD_IV_RANK_MIN: float = 75.0       # Chain-relative IV rank gate (biggest backtest edge)
    OPTIONS_SPREAD_DTE_MIN: int = 55               # Min DTE (~2 months out)
    OPTIONS_SPREAD_DTE_MAX: int = 75               # Max DTE (~10 weeks)
    OPTIONS_SPREAD_PROFIT_TARGET_PCT: float = 0.50 # Close at 50% of credit captured
    OPTIONS_SPREAD_MAX_HOLD_DAYS: int = 21         # Force close after 21 days
    OPTIONS_SPREAD_MAX_CONCURRENT: int = 15        # Max simultaneous spread positions
    # Universe whitelist — backtest-positive names only (CLF/FIVN/XLE excluded: negative 7yr Sharpe)
    OPTIONS_SPREAD_UNIVERSE: tuple = ("SPY", "QQQ", "AAPL", "AMD", "AMZN", "META", "NVDA", "JPM", "CAT", "XLK", "XLF", "GLD")
    # === TIER 2: STOCK+CSP combo (high-capital, optional) ===
    # Buy 100 shares + sell 25Δ put. Capital-heavy ($30-70k per position). Use on 1-2 top names.
    OPTIONS_STOCK_CSP_ENABLED: bool = False        # Disabled by default; enable explicitly
    OPTIONS_STOCK_CSP_IV_RANK_MIN: float = 85.0    # Stricter than spreads
    OPTIONS_STOCK_CSP_MIN_CASH: float = 30000.0    # Only proceed if cash available >= this
    OPTIONS_STOCK_CSP_MAX_CONCURRENT: int = 2      # Hard cap on concurrent positions
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
    ENTRY_ZONE_SHORT: float = 100.0# Mirror of ZONE_LONG (100-35=65). Was 75. ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:7106 short-side entry zone gate
    ENTRY_MIN_ALIGNMENT: int =             10     # V8 ABLATION 2026-04-13: Sharpe 1.0, WR 53.9%. Was 8.
    ENTRY_PRIMARY_TF: str =                '4h'   # BACKTEST_CHANGE_T7 was 1h → 4h slower primary TF
    ENTRY_TRIGGER_TF: str =                '15m'  # Trigger TF for crossover (was 5m, shifted to 15m for stocks) ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:2680 referenced in entry eval
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
    ALIGNMENT_GATE_TOTAL: int = 12  # BACKTEST_CHANGE_T8 total alignment score required ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:8009,8187 alignment gate log denominator
    # === CONVICTION THRESHOLDS (backtest) ===
    CONVICTION_SHORT_THRESHOLD: int = 20  # BACKTEST_CHANGE_T9 min conviction score for short entries
    # === LR PCTB SHORT (backtest) ===
    LR_PCTB_D_SHORT_THRESHOLD: float = 0.1  # BACKTEST_CHANGE_T10 daily LR %B threshold for shorts
    # === SATOSHIT2024 STRATEGY — stocks (15m mean-reversion, 5m instead of 3m) ===
    SATOSHIT_ENTRY_FILTER: bool = False  # T25 2026-04-14 (fixed gates): True=0.388 vs False=0.328 (+18%). Previous stale result (False=0.529) was broken-gate run. Marginal — leaving False until larger sweep.
    SATOSHIT_ACCOUNTS_TRADIER: List[str] = field(default_factory=lambda: ["tra", "trb", "trc"])  # DEAD_CONFIRMED (priority 35/100) — no plausible wiring site found 20260416
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
    VERBOSE_TIMER: bool = False  # DEAD_CONFIRMED (priority 5/100) — no plausible wiring site found 20260416
    VERBOSE_FETCH_LOGGING: bool = False
    DEBUG: bool = False

    # --- 3. API Configuration ---
    TRADIER_API_BASE_URL: str = "https://api.tradier.com/v1"  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416
    TRADIER_SANDBOX_URL: str = "https://sandbox.tradier.com/v1"  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416
    TRADIER_STREAMING_URL: str = "https://stream.tradier.com/v1"  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416
    TRADIER_WS_URL: str = "wss://ws.tradier.com/v1"

    # TRADIER_WS_URL: str = "wss://ws.tradier.com/v1/markets/events"
    
    # --- 4. Global Settings ---
    USE_SANDBOX: bool = os.getenv("TRADIER_USE_SANDBOX", "false").lower() == "true"  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416

    # --- 5. Rate Limits ---
    API_RATE_LIMIT_PER_SECOND: int = 10
    API_RATE_LIMIT_PER_MINUTE: int = 300  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416
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
    
    ENABLE_IP_ROTATION: bool = field(init=False)  # DEAD_CONFIRMED (priority 15/100) — no plausible wiring site found 20260416

    ACCOUNT_KEYS = ['tra','trb','trc']
    BASE_PATH: Path = _resolve_base_path()
    DATA_DIR: Path = BASE_PATH / "data" / "tradier"
    KLINES_CACHE_DIR: Path = BASE_PATH / "klines_cache" / "tradier"
    SYMBOLS_FILE: Path = BASE_PATH / "symbols_tradier.json"
    TRADIER_SYMBOLS_FILE: Path = BASE_PATH / "symbols_tradier.json"
    LEADERBOARD_LONG: Path = BASE_PATH / "symbols_long_tr.json"  # DEAD_CONFIRMED (priority 15/100) — no plausible wiring site found 20260416
    LEADERBOARD_SHORT: Path = BASE_PATH / "symbols_short_tr.json"  # DEAD_CONFIRMED (priority 15/100) — no plausible wiring site found 20260416
    INDICATORS_FILE: Path = DATA_DIR / "tradier_indicators_latest.json"  # WIRED 2026-04-16 (priority 15/100) — tradier_rankings.py:144
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
    REDIS_CHANNEL_MARKET_DATA: str = "tradier_indicators_channel"  # DEAD_CONFIRMED (priority 15/100) — no plausible wiring site found 20260416
    REDIS_CHANNEL_PRICES: str = "tradier_prices_channel"  # DEAD_CONFIRMED (priority 15/100) — no plausible wiring site found 20260416
    REDIS_CHANNEL_POSITIONS: str = "tradier_positions_channel"

    PRICE_REFRESH_INTERVAL: float = 3.0  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416
    POSITION_REFRESH_INTERVAL: float = 6.0  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416
    ORDER_CACHE_TTL: int = 10
    POSITION_CACHE_TTL: int = 5  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416
    PRICE_UPDATE_INTERVAL: float = 1.0
    INDICATOR_UPDATE_INTERVAL: float = 30.0  # BACKTEST_CHANGE_T49 was 60 → 30 faster indicator refresh ; DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416
    RANKING_UPDATE_INTERVAL: float = 180.0  # BACKTEST_CHANGE_T50 was 300 → 180 faster ranking refresh
    TIMEFRAMES: List[str] = field(default_factory=lambda: ["1m", "5m", "15m", "1h", "4h", "D"])
    MARKET_OPEN_HOUR: int = 9  # DEAD_CONFIRMED (priority 40/100) — no plausible wiring site found 20260416
    MARKET_OPEN_MINUTE: int = 30  # DEAD_CONFIRMED (priority 40/100) — no plausible wiring site found 20260416
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
    WT_CROSSUNDER_FINAL_ENABLED: bool = True  # T25 2026-04-14: True=0.357 vs False=0.363 (Δ=0.006) — essentially noise. Keeping True for live WT exit coverage.
    ATR_TRAIL_2X_EXIT_ENABLED: bool = False  # BACKTEST_CHANGE_T58: was True. ATR trail = #1 stock PnL destroyer (-2557% cumulative). Disabled.
    STOCH_CROSS_1H_EXIT_ENABLED: bool = True  # BACKTEST_CHANGE_T17 stoch cross on 1h triggers exit
    # === TIME ZONE SIZING (backtest) ===
    TIME_ZONE_ENABLED: bool = True  # BACKTEST_CHANGE_T19 enable time-of-day zone sizing
    ZONE_OPEN_THRESHOLD: int = 25  # BACKTEST_CHANGE_T20 minutes after open = "open zone"
    ZONE_MID_THRESHOLD: int = 30  # BACKTEST_CHANGE_T21 minutes into session = "mid zone" start
    ZONE_CLOSE_THRESHOLD: int = 20  # BACKTEST_CHANGE_T22 minutes before close = "close zone"
    CLOSE_ZONE_SIZE_MULT: float = 1.5  # BACKTEST_CHANGE_T23 size multiplier in close zone
    MID_ZONE_SHORT_EXTRA_IND: str = "wt_crossunder_15m"  # BACKTEST_CHANGE_T24 extra indicator for mid-zone shorts ; DEAD_CONFIRMED (priority 35/100) — no plausible wiring site found 20260416
    HOLD_BARS_OPEN: int = 200  # BACKTEST_CHANGE_T25 max hold bars during open zone ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    HOLD_BARS_MID: int = 500  # BACKTEST_CHANGE_T25 max hold bars during mid zone ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    HOLD_BARS_CLOSE: int = 50  # BACKTEST_CHANGE_T25 max hold bars during close zone ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    # === NO-LOSS NATURAL EXIT + K-ZONE ENTRY + BOUNCE REENTRY (2026-03-21 — 121 sym × D/4h/1h, 9192 combos) ===
    NOLOSS_MIN_PROFIT_PCT_TRADIER: float = 3.0  # REVERTED 2026-04-17: 0% caused exits at 0.3% gain (avg gain dropped, lost $1k/day). MAR-30 baseline = 3.0 = was making 10%/week. The Sharpe-6.36-at-0% claim was bogus.
    # === BB RECOVERY-TO-ENTRY EXIT BYPASS (2026-04-15, stocks) ===
    # When True: if entry_price > bb_high_1h (LONG) or < bb_low_1h (SHORT),
    # AND current 3m close has recovered within tolerance of entry_price,
    # AND current 3m bar shows reversal, ALLOW close at loss (bypass NOLOSS gate).
    # Defaults OFF — sweep first.
    BB_RECOVERY_EXIT_ENABLED_TRADIER: bool = True  # 2026-04-20 sweep: unlocks stranded positions stuck above bb_1h
    BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER: float = 0.30  # stock pct tolerance around entry_price
    BB_RECOVERY_EXIT_TOLERANCE_ATR_MULT_TRADIER: float = 0.0  # if >0, uses N * atr_3m instead of pct
    # wt_D bounce augment — add to losing position when daily WT turns, bypasses gain gates
    WT_D_BOUNCE_AUG_ENABLED: bool = True   # 2026-04-20: applied live per user directive
    WT_D_BOUNCE_AUG_MULTIPLIER: float = 2.0  # 2026-04-20: 2x (add 1x to existing) per user directive. Was 4x.
    WT_D_BOUNCE_AUG_REQUIRE_HIGHER_WT: bool = True  # 2026-04-20: require bounce WT > last aug WT (was False)
    WT_D_BOUNCE_AUG_REQUIRE_HIGHER_PRICE: bool = True  # 2026-04-20: require bounce price > last aug price (higher low for LONG)
    WT_D_BOUNCE_AUG_COOLDOWN_HOURS: float = 1.0  # min hours between wt_D augments per position
    WT_D_BOUNCE_DD_STOP_ENABLED: bool = True  # 2026-04-20: cut extra DD leg if price continues below aug price
    K_ZONE_ENTRY_ENABLED_TRADIER: bool = True  # BACKTEST_CHANGE_T52: K-zone entry — K in zone + turning + candle confirms. No crossover wait.
    K_ZONE_VETO_ENABLED_TRADIER: bool = False  # VARIANCE_FIX 2026-04-14: when True, K_ZONE_LONG/SHORT_THRESHOLD veto entries on wt_dc path (proves switch gates trades). Default False = live unchanged.
    WT_COMPOSITE_VETO_ENABLED_TRADIER: bool = False  # VARIANCE_FIX 2026-04-14: when True, WT_COMPOSITE_SCORING_ENABLED vetoes wt_dc entries lacking composite alignment. Default False = live unchanged.
    MI_EXIT_VETO_ENABLED_TRADIER: bool = False  # SENTINEL_FIX 2026-04-14: when True, MI_EXIT_ENABLED_TRADIER actually gates exits. Default False = live unchanged.
    WT_EXIT_VETO_ENABLED_TRADIER: bool = False  # SENTINEL_FIX 2026-04-14: when True, WT_EXIT_MIN_TFS_TRADIER actually gates exits. Default False = live unchanged.
    DC_ENTRY_VETO_ENABLED_TRADIER: bool = False  # SENTINEL_FIX 2026-04-14: when True, DC_POSITION_ENTRY_THRESHOLD gates entries (require dc_pos in zone). Default False = live unchanged.
    K_ZONE_LONG_THRESHOLD_TRADIER: int = 35   # S1_SWEEP_2026-04-15: 35 top S1 cfg Sharpe=4.23 on 20605 trades (was 80)
    K_ZONE_SHORT_THRESHOLD_TRADIER: int = 65  # S1_SWEEP_2026-04-15: 65 top S1 cfg Sharpe=4.23 on 20605 trades (was 20)
    K_ZONE_ENTRY_BONUS_TRADIER: int = 20  # 2026-04-08 SWEEP: 20 → Sharpe 11.12 vs 25 → 6.92 (+61%). Biggest single config win.
    BOUNCE_REENTRY_ENABLED_TRADIER: bool = True  # BACKTEST_CHANGE_T53: After profitable exit, K must reset to zone before reentry.
    BOUNCE_REENTRY_K_RESET_LONG_TRADIER: int = 35  # DEAD_CONFIRMED (priority 90/100) — no plausible wiring site found 20260416
    BOUNCE_REENTRY_K_RESET_SHORT_TRADIER: int = 65  # DEAD_CONFIRMED (priority 90/100) — no plausible wiring site found 20260416
    # ═══ SAFETY SWITCHES (2026-04-16 audit) ═══
    TRADIER_REQUIRE_TRADEABLE_KEY: bool = True     # Gate entry at execute_now if not in tradeable_keys
    TRADIER_RATIO_REQUIRE_MIN_GAIN: bool = False   # Block RATIO_BOOST on positions with gain < min
    TRADIER_RATIO_BOOST_MIN_GAIN_PCT: float = 1.0  # Min gain for ratio boost to fire
    TRADIER_REENTRY_OVERDUE_BYPASS_ENABLED: bool = True  # True=legacy (bypass after 48h); False=always enforce stoch
    TRADIER_NOLOSS_SRS_BYPASS: bool = True          # True=SRS reason bypasses NOLOSS; False=no reason bypass
    # === TWO-TIER MANDATORY REENTRY — STOCKS (BC_155) ===
    REENTRY_TIER1_SIZE_MULT_TRADIER: float = 1.5  # Tier 1: 150% of closed qty ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    REENTRY_TIER2_SIZE_MULT_TRADIER: float = 0.8  # Tier 2: 80% of closed qty
    REENTRY_TIER2_PRICE_PCT_TRADIER: float = 0.003  # 0.3% price move triggers Tier 2
    REENTRY_TIER2_MIN_MINUTES_TRADIER: float = 10.0  # Min minutes before Tier 2
    REENTRY_TIER2_MAX_MINUTES_TRADIER: float = 120.0  # Force entry after 120min
    # RALLY REENTRY GATE (0-3h after exit): k5m+k15m rising + HTF WT aligned
    # REENTRY_RALLY_K15M_MAX: additional k15m level cap — 100=disabled, 40=moderate, 20=strict oversold
    # REENTRY_RALLY_HTF_MIN: min HTF TFs (1h/4h/D) aligned — 1=loose, 2=default, 3=strict
    REENTRY_RALLY_K15M_MAX: float = 100.0# sweep: 100 (off) / 40 / 20
    REENTRY_RALLY_HTF_MIN: int = 3          # 2026-04-18: sqlite reentry analysis — wt_all3 avg_sharpe 0.1036 vs wt_2of3 -0.0468. Was 2.
    # MINIMUM HOLD TIME — prevents churning/death-by-1000-cuts on stocks
    MIN_HOLD_MINUTES_TRADIER: float = 30.0  # No exits before 30 min. Bypassed only if loss > -5%. ; WIRED 2026-04-16 (priority 90/100) — tradier_manage.py:3891 stock min hold fallback
    # MULTI-TF EXIT CONFIRMATION — exits must mirror entry strength
    # Entry needs multi-TF WT alignment → exit needs multi-TF WT disalignment
    # Prevents 5m noise from killing positions that 15m/1h/4h still support
    MIN_EXIT_TF_AGAINST_TRADIER: int = 2  # Need 2+ TFs (of 5m/15m/1h/4h) with WT against position before exit
    # BOUNCE-TOP EXIT — V4 backtest proven: Sharpe -0.5 → +0.42 on 121 stocks 2yr
    # Exits losing positions at the TOP of a bounce (not the bottom like a stop loss).
    # Mandatory reentry follows: 150% at pullback, 200% at rising WT cross.
    BOUNCE_TOP_EXIT_ENABLED: bool = False  # KILLED 2026-03-30: percentage stop loss in disguise. Exits ONLY on technicals. ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    BOUNCE_TOP_MIN_HOLD_MINUTES: float = 1440.0  # 24h min hold before bounce exit eligible
    BOUNCE_TOP_MIN_LOSS_PCT: float = -3.0  # Only fires when loss is between -3% and -50%
    BOUNCE_TOP_MAX_LOSS_PCT: float = -50.0  # Don't exit positions beyond -50% (too late)
    BOUNCE_TOP_REENTRY_MULT: float = 1.5  # 150% qty on pullback reentry ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    BOUNCE_TOP_RISING_CROSS_MULT: float = 2.0  # 200% qty on rising WT cross reentry ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    HODL_LONG_ONLY: bool = True  # BACKTEST_CHANGE_T54: HODL strategy is LONG only. SHORT on stocks = negative returns (upward bias kills hold-forever shorts). ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    # === MOMENTUM FADE — STOCKS (2026-03-23 — crypto-validated, adapted for stocks) ===
    MOMENTUM_FADE_ENABLED_TRADIER: bool = False  # DISABLED: Gate ablation 2026-03-26 proved zero impact (Sharpe +0.00, +0 trades). Was T55b.
    MOMENTUM_FADE_BODY_ATR_MIN_TRADIER: float = 2.0  # BACKTEST_CHANGE_T55b: Candle range must be >= 2x ATR. Stocks move less so 2x is still significant.
    MOMENTUM_FADE_VOL_MIN_TRADIER: float = 2.0  # BACKTEST_CHANGE_T55b: Volume must be >= 2x avg. Same threshold as crypto.
    MOMENTUM_FADE_K_ZONE_TRADIER: bool = True  # BACKTEST_CHANGE_T55b: Only fade when K is overbought/oversold. Improves Sharpe significantly.
    MOMENTUM_FADE_SCORE_BONUS_TRADIER: int = 5  # BACKTEST_CHANGE_T55b: Score bonus (stock score capped at 30, so smaller bonus than crypto)
    # === ABLATION BACKTEST RESULTS (2026-03-21 — 2453 configs × 121 sym, D bars, P1+P2 OOS-validated) ===
    RSI_ENTRY_PERIOD_TRADIER: int = 10  # BACKTEST_CHANGE_T55: was 2. RSI(10) = OOS champion. Deeper mean-reversion captures bigger moves. Sharpe 6.43, WR 73.9%, PF 8.18
    RSI_ENTRY_LONG_TRADIER: float = 40.0  # A/B 2026-04-17 full 109-sym × 3yr: rsi15<40 Sharpe=0.477 beats <42 and <35. Was 42. Evidence: MOM_rsi15_lt40_rsi1h_lt22 peak.
    RSI_ENTRY_SHORT_TRADIER: float = 58.0  # BACKTEST_CHANGE_T64: was 70. RSI>58 for shorts.
    RSI_EXIT_LONG_TRADIER: float = 85.0  # BACKTEST_CHANGE_T56: was 70. Exit at RSI>85 = let winners run longer. +311% PnL over 4.8yr
    RSI_EXIT_SHORT_TRADIER: float = 15.0  # BACKTEST_CHANGE_T56: exit when RSI < 15
    SMA_FILTER_PERIOD_TRADIER: int = 100  # BACKTEST_CHANGE_T57: was 200. SMA100 filter = best OOS. Only LONG above SMA, SHORT below
    ATR_TRAIL_ENABLED_TRADIER: bool = False  # BACKTEST_CHANGE_T58: was True. ATR trailing stop = #1 stock PnL destroyer (-2557%). Disabled. ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    STOCH_CROSS_ENTRY_TRADIER: bool = False  # BACKTEST_CHANGE_T59: was True. Stoch crossover = noise on daily bars. RSI(10) is the real entry.
    AUGMENT_PYRAMID_TRADIER: bool = False  # BACKTEST_CHANGE_T60: Pyramiding barely fires on stocks (0-10 trades). Disabled. ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    # === BEAR MARKET MODE ===
    BEAR_MARKET_MODE_TRADIER: bool = True  # URGENT_FIX: favor shorts in current bear market
    AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER: bool = True  # URGENT_FIX: never augment losing positions
    # === HEDGE vs RATIO SWEEP (2026-03-21 — 65 configs, both systems) ===
    RATIO_MULTIPLIER_TRADIER: float = 3.5  # BACKTEST_CHANGE_T61: was 2.0. 3.5x ratio exaggeration = Sharpe 260 (vs 249 at 2x). Best: 3.5-4x.
    HEDGE_CROSS_SYMBOL_TRADIER: bool = True  # BACKTEST_CHANGE_T62: Cross-symbol hedge enabled. 25% size, trigger -1%, no momentum gate. ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416
    HEDGE_SIZE_RATIO_TRADIER: float = 0.25  # BACKTEST_CHANGE_T62: Hedge at 25% of losing value. Sweet spot in sweep. ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416
    HEDGE_TRIGGER_LOSS_TRADIER: float = -1.0  # BACKTEST_CHANGE_T62: Trigger hedge at -1% loss (stocks: tighter than crypto -2% due to daily gaps). ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416
    HEDGE_SAME_SYMBOL_TRADIER: bool = False  # BACKTEST_CHANGE_T63: Same-symbol hedge DISABLED for stocks. Cross-symbol only. ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416
    # === HEDGE MODE (backtest) ===
    HEDGE_MODE_TRADIER: bool = False  # BACKTEST_CHANGE_T31 hedge mode disabled for stocks
    # === TRC AGGRESSIVE SANDBOX — "after-sandbox sandbox" ===
    # trc is paper-money. Push extreme settings here to prove before applying to trb.
    TRC_START_POSITION_SIZE: float = 1000.0  # Local extremes: base size — scorer overrides per trade ($50-$5000)
    TRC_MAX_ORDER_VALUE: float = 5000.0  # Max single order (cap at $5000)
    TRC_MAX_POSITION_SIZE: float = 5000.0  # Max per position = $5000 (was 15000)
    TRC_SCALP_START_SIZE: float = 1000.0  # Scalp base size
    TRC_SCALP_MAX_POSITIONS_PER_SIDE: int = 20  # 20 long + 20 short = 40 total (was 12)
    TRC_MAX_CONCURRENT_POSITIONS: int = 40  # 40 total = 20 per side (was 32)
    TRC_ROTATION_POSITION_SIZE: float = 3000.0  # 2.5x trb ($1200)
    TRC_RSI2_POSITION_SIZE: float = 1980.0  # 3.3x trb ($600)
    TRC_GAP_FILL_POSITION_SIZE: float = 1980.0  # 3.3x trb ($600)
    TRC_DC_DAYTRADE_START_SIZE: float = 1980.0  # 3.3x trb ($600)
    TRC_DC_DAYTRADE_LONG_BUDGET: float = 9900.0  # 3.3x trb ($3000)
    TRC_DC_DAYTRADE_SHORT_BUDGET: float = 9900.0  # 3.3x trb ($3000)
    TRC_SWING_LONG_BUDGET: float = 100000.0  # Local extremes: unlimited paper budget for 20 longs at $5000 (was 8000)
    TRC_SWING_SHORT_BUDGET: float = 100000.0  # Local extremes: unlimited paper budget for 20 shorts at $5000 (was 8000)
    TRC_SCALP_LONG_BUDGET: float = 5000.0  # 5x trb ($1000)
    TRC_SCALP_SHORT_BUDGET: float = 5000.0  # 5x trb ($1000)
    TRC_BEAR_MARKET_MODE: bool = False  # No bear penalty — test both directions equally
    TRC_ENTRY_ZONE_LONG: float = 25.0  # Local extremes: deeper oversold bottom (was 30)
    TRC_ENTRY_ZONE_SHORT: float = 75.0  # Local extremes: deeper overbought top (was 70)
    TRC_ENTRY_MIN_ALIGNMENT: int = 6  # Looser than trb (8)
    TRC_LS_RATIO_MIN: float = 0.30  # Wider than trb (0.50)
    TRC_LS_RATIO_MAX: float = 3.00  # Wider than trb (2.00)
    TRC_MAX_DAILY_LOSS_PCT: float = 10.0  # 3.3x trb (3%) — paper money, let it run
    TRC_SCALP_TARGET_PCT: float = 0.01  # 2x trb (0.005) — let winners run further
    TRB_NOLOSS_MIN_PROFIT_PCT: float = 0.0  # 2026-04-08: TECHNICALS ONLY. Was 3.0% which blocked all exits on losers. ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    TRC_NOLOSS_MIN_PROFIT_PCT: float = 0.0  # 2026-04-08: TECHNICALS ONLY.
    # === CONCENTRATION CAP — prevent single-symbol overexposure ===
    MAX_SYMBOL_VALUE_TRADIER: float = 15000.0  # Max $ value per symbol. USO hit $352K, IBIT $119K — caused disaster losses.
    TRC_MAX_SYMBOL_VALUE: float = 5000.0  # Local extremes: cap per symbol at $5000 (was 15000)
    TRC_LOCAL_EXTREMES_SCORER_ENABLED: bool = True  # Use local_extremes_scorer for dynamic $50-$5000 sizing
    TRADIER_LOCAL_EXTREMES_SCORING_ENABLED: bool = True  # LE scorer for ALL tradier accounts (trb+trc): 25-indicator gate + $50-$5000 tier sizing
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
    TF_HTF1: str = "1h"     # First confirmation ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:7131 entry eval
    TF_HTF2: str = "4h"     # Second confirmation
    TF_HTF3: str = "D"      # Daily — strongest trend ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:7132 entry eval
    TF_MACRO: str = "D"     # Same as HTF3 for stocks (no weekly in live) ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:7133 entry eval
    # === BACKTEST-VALIDATED GATES (121 stocks, train/test confirmed) ===
    BACKTEST_VALIDATED_GATES_TRADIER: bool = True  # Block entries on signals confirmed -EV on both train+test
    DC_POSITION_ENTRY_THRESHOLD: float = 0.25  # REVERTED 2026-04-17: 0.15 was too tight. Mar-30 baseline 0.25 = Sharpe 18.57 on 61 stocks.
    MFI_FLIP_EXIT_ENABLED: bool = True  # BACKTEST_CHANGE_148: Exit when MFI exhausts (+3.91% avg vs +1.09% fixed TP, 44 trades)
    MFI_FLIP_EXIT_LONG_THRESHOLD: float = 70.0  # Exit LONG when MFI_1h > 70 (overbought = sell)
    MFI_FLIP_EXIT_SHORT_THRESHOLD: float = 30.0  # Exit SHORT when MFI_1h < 30 (oversold = cover)
    # === MARKET QUALITY + SBA + EOD (BACKTEST_CHANGE_146/147) — 11K-trade validated ===
    MARKET_QUALITY_SCORE_ENABLED_TRADIER: bool = True   # ENABLED 2026-04-17: activates MFI-for-LONG / RSI+rel_vol-for-SHORT directional scorer + BB/ADX bonuses.
    EOD_RATIO_ENFORCE_TRADIER: bool = False  # BACKTEST_CHANGE_147: Scale down entries 30min before close, block at 5min
    SBA_ENABLED_TRADIER: bool = False  # BACKTEST_CHANGE_145: Strategic Bounce Averaging for stock hold positions ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    SBA_MIN_LOSS_PCT_TRADIER: float = -3.0  # Stocks are less volatile, wider threshold than crypto -2% ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    SBA_MAX_LOSS_PCT_TRADIER: float = -12.0  # Stop averaging beyond -12% ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    SBA_SIZE_FRACTION_TRADIER: float = 0.25  # 25% of START_POSITION_SIZE per add (smaller than crypto 35%) ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    SBA_MAX_ADDS_TRADIER: int = 2  # Max recovery adds per stock position ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    SBA_COOLDOWN_S_TRADIER: int = 7200  # 2hr between adds (stocks move slower, 2x crypto's 1hr) ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    SBA_ADX_MAX_TRADIER: float = 22.0  # Stocks trend more cleanly, slightly higher ADX threshold ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    # === YOUTUBE CONSENSUS STRATEGIES (2026-03-27 — research: 16 verified profitable traders) ===
    # === STRATEGY ALLOCATION: trb=PROVEN only, trc=EXPERIMENTAL (paper) ===
    # --- VWAP Filter — PROVEN, on trb+trc ---
    VWAP_FILTER_ENABLED: bool = False  # 2026-04-20 sweep: VWAP filter suppresses valid trades — top 20 mega configs all False
    VWAP_BOUNCE_ENTRY_ENABLED: bool = True  # Enter on VWAP bounce (pullback to VWAP + reversal) ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    VWAP_BOUNCE_DIST_PCT: float = 0.3  # Price must be within 0.3% of VWAP for bounce entry ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    VWAP_SCORE_BONUS: int = 10  # Score bonus when price is on correct side of VWAP ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    # --- Opening Range Breakout — EXPERIMENTAL, trc only (TRC_ override enables) ---
    ORB_ENABLED: bool = False  # OFF for trb (real $). TRC overrides to True.
    ORB_WINDOW_MINUTES: int = 15  # DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    ORB_RVOL_MIN: float = 1.5
    ORB_TARGET_MULT: float = 1.5
    ORB_STOP_MIDPOINT: bool = True
    ORB_MAX_HOLD_MINUTES: float = 150.0
    ORB_POSITION_SIZE: float = 600.0
    ORB_MAX_PER_DAY: int = 3
    ORB_LONG_BUDGET: float = 2000.0  # WIRED 2026-04-16 (priority 85/100) — tradier_manage.py:5286 TRC override destination
    ORB_SHORT_BUDGET: float = 2000.0  # WIRED 2026-04-16 (priority 85/100) — tradier_manage.py:5286 TRC override destination
    # --- Lunch Dead Zone — PROVEN, on trb+trc ---
    LUNCH_DEADZONE_ENABLED: bool = True
    LUNCH_DEADZONE_MODE: str = "BLOCK_MOMENTUM"
    LUNCH_DEADZONE_SIZE_MULT: float = 0.5  # DEAD_CONFIRMED (priority 55/100) — no plausible wiring site found 20260416
    # --- RVOL Gate Tightening — PROVEN, on trb+trc ---
    RVOL_MOMENTUM_MIN: float = 1.5
    RVOL_SCALP_MIN: float = 1.0  # DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    RVOL_SCORE_BOOST_THRESHOLD: float = 2.0  # DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    RVOL_SCORE_BOOST_PCT: float = 0.20  # DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    # --- 9/21 EMA — PROVEN, on trb+trc ---
    EMA_9_21_FILTER_ENABLED: bool = True
    EMA_9_21_TIMEFRAME: str = "5m"
    EMA_9_21_SCORE_BONUS: int = 5  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    # --- TTM Squeeze — EXPERIMENTAL, trc only ---
    SQUEEZE_ENABLED: bool = False  # OFF for trb. TRC overrides to True. ; WIRED 2026-04-16 (priority 80/100) — tradier_manage.py:5286 TRC override destination
    SQUEEZE_SCORE_BONUS: int = 15  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    # --- Episodic Pivot — EXPERIMENTAL, trc only ---
    EPISODIC_PIVOT_ENABLED: bool = False  # OFF for trb. TRC overrides to True.
    EP_MIN_GAP_PCT: float = 5.0  # DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    EP_MIN_VOL_MULT: float = 3.0  # DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    EP_POSITION_SIZE: float = 800.0
    EP_MAX_CONSOLIDATION_DAYS: int = 8  # DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    EP_MAX_RETRACE_PCT: float = 25.0  # DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    # --- Bounce-Top Exit — EXPERIMENTAL, trc only (exits losers = risky) ---
    # Note: BOUNCE_TOP_EXIT_ENABLED above applies to trb. TRC override below.
    # --- TRC Overrides: enable ALL experimental strategies on paper account ---
    TRC_ORB_ENABLED: bool = False  # Disabled — trc now runs local extremes only
    TRC_EPISODIC_PIVOT_ENABLED: bool = False  # Disabled — trc now runs local extremes only
    TRC_SQUEEZE_ENABLED: bool = False  # Disabled — trc now runs local extremes only
    TRC_MOMENTUM_FADE_ENABLED: bool = False  # Disabled — trc now runs local extremes only
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
    STDEV_BREAKOUT_COOLDOWN: float = 600.0  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    STDEV_BREAKOUT_RETEST_COOLDOWN: float = 300.0  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    STDEV_BREAKOUT_SCORE: int = 25
    STDEV_BREAKOUT_RETEST_SCORE: int = 22
    STDEV_BREAKOUT_RVOL_MIN: float = 1.2
    STDEV_BREAKOUT_MAX_AGE_BARS: int = 50
    STDEV_BREAKOUT_EXIT_PCTB_FAIL: float = 0.75
    STDEV_BREAKOUT_EXIT_WT_ENABLED: bool = True
    # === MOMENTUM INTERCEPTION (MI) — Early exit/entry via slowing deltas, LH/LL structure, divergence ===
    MI_EXIT_ENABLED_TRADIER: bool = False  # REVERTED 2026-04-17: MI_EXIT was triggering early exits at 0.3%. Mar-30 baseline OFF.
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
    WT_DC_ENTRY_THRESHOLD: float = 55  # V8 ABLATION 2026-04-13: Sharpe 1.276 (best of 35/55/75/95). T25 2026-04-14: DC=55 avg_sharpe=0.386 best among 35(0.356)/43(0.342)/55(0.386). DC=75 fires 0 trades.
    WT_DC_EXIT_THRESHOLD: float = 30  # SERVER 204: exit>=25 optimal across all entry thresholds
    # === EXIT PATH SWITCHES (2026-04-08 — scorer is SOLE authority, all legacy paths OFF) ===
    # To re-enable any path: set to True, restart tradier_manage
    EXIT_K5M_BOUNCE_ENABLED: bool = False       # K5M stoch bounce turn + low break. Was closing on 5m noise.
    EXIT_HARD_DROP_5M_ENABLED: bool = False      # Price < prev 5m low. Too aggressive — kills options on minor dips.
    EXIT_ALGO_SCORE_ENABLED: bool = False        # Old calculate_signal_score exit. Bypassed scorer, closed PLTR.
    EXIT_STRUCT_BREAK_5M_ENABLED: bool = False   # 5m LH/HL structure exit. Too noisy for swing/options.
    EXIT_IBS_EXHAUSTION_ENABLED: bool = False    # Internal Bar Strength extreme. Minor signal, not worth standalone exit.
    EXIT_SENTIMENT_ENABLED: bool = False         # Sentiment collapse exit. Unreliable signal source. ; WIRED 2026-04-16 (priority 60/100) — tradier_manage.py:4149 exit guard
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
    DELTA_ENGINE_ENABLED: bool = True  # 121 sym/2yr: Sharpe 0.038→0.485. T25 2026-04-14: True=0.346 vs False=0.373 (-7%). False marginally better but diff is small; keeping True for live delta tracking.
    DELTA_ENTRY_ENABLED: bool = False  # T25 sweep: False avg=0.527 vs True=0.507 (-4%). Best tested.
    DELTA_EXIT_ENABLED: bool = True  # Re-enabled — real fix is in REENTRY_MONITOR (checks exit score before reopen)
    # WT_DC scorer exit guards
    WT_DC_EXIT_STALE_MAX_S: int = 600  # Don't exit on indicators > 10min stale (protects against stale data firing exits)
    DELTA_PYRAMID_ENABLED: bool = False  # DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    DELTA_SPEED_SMOOTH: int = 5  # WINNER: sm=5
    DELTA_ACCEL_LOOKBACK: int = 5
    DELTA_TF_WEIGHTS: dict = None  # Set in __post_init__
    DELTA_TF_Z_THRESHOLD: float = 1.5  # WINNER: tz=1.5
    DELTA_ENTRY_Z_THRESHOLD: float = 2.5  # WINNER: ez=2.5
    DELTA_ENTRY_ACCEL_THRESHOLD: float = 0.3  # WINNER ST: ea=0.3 (LT: 0.0)
    DELTA_ENTRY_MIN_TF: int = 3  # WINNER ST: mtf=3 (LT: 2)
    DELTA_EXIT_DECAY_RATIO: float = 0.30  # WINNER: sp=70 → 30% decay from peak (stocks ST sweep #1)
    DELTA_EXIT_TF: str = "15m"  # WINNER ST: 15m exit TF (sweep top 8 all used 15m)
    DELTA_EXIT_TYPE: str = "speed_decay"  # WINNER ST: speed_decay sp=70 Sharpe 0.710 WR 77.7% (corrected from wt_cross) ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
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
    DELTA_MAX_HOLD_BARS: int = 0  # DISABLED — ride winners until technicals turn. No fixed time exits. ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_COOLDOWN_BARS: int = 60  # WINNER ST: 60 bars (5h) ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_HTF_GATE: str = "4h"  # WINNER ST: 4h must confirm (LT: 4h_D)
    DELTA_ATR_ENTRY_FILTER: bool = True  # ATR filter on for stocks
    # Stocks LT overrides (for longer holds, ez_positions_quick can switch to these)
    DELTA_LT_ENTRY_MIN_TF: int = 2  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_LT_ENTRY_Z_THRESHOLD: float = 2.0  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_LT_ENTRY_ACCEL_THRESHOLD: float = 0.0  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_LT_EXIT_TF: str = "4h"  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_LT_EXIT_TYPE: str = "combined_wt_speed"  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_LT_EXIT_SPEED_PCT: int = 50  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_LT_COOLDOWN_BARS: int = 120  # ~10h ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_LT_HTF_GATE: str = "4h_D"  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    # STRUCTURAL RANGE SHIFT EXIT (stocks) — hold losers, cut at 4h DC boundary when range shifts
    STRUCTURAL_RANGE_SHIFT_EXIT: bool = True  # bb_4h Apr-13 DISASTER avg -0.95% 16%WR. bb_1h is correct for stocks (user directive). dc_4h is for crypto only.
    STRUCTURAL_RANGE_SHIFT_TF: str = "bb_1h"  # STOCKS: bb_1h (user directive — bb_upper_1h/bb_lower_1h). CRYPTO: dc_4h. bb_4h was wrong and caused April-13 losses.
    # Cascade params (stocks) — same knobs as crypto unless overridden
    STRUCTURAL_RANGE_SHIFT_K_HIGH: float = 85.0  # 2026-04-20: tightened from 75 — only exit at extreme overbought
    STRUCTURAL_RANGE_SHIFT_K_LOW: float = 15.0   # 2026-04-20: tightened from 25 — only exit at extreme oversold
    SRS_K_EXIT_1H: float = 85.0                  # v8 engine: SRS exit k_1h threshold (matches K_HIGH above)
    STOCH_1H_EXIT_K_MIN: float = 85.0            # v8 engine: stoch cross exit requires k_1h >= 85
    EXIT_SCORER_K_EXTREME: float = 85.0          # wt_dc_exit_scorer: extreme k threshold (was hardcoded 75)
    K_LOWER_HIGH_EXIT_ENABLED: bool = True        # v8 engine: exit if k peaks below extreme and turns down
    K_LOWER_HIGH_LTF_THRESHOLD: float = 65.0     # k_5m must reach >= 65 to qualify as failed rally
    K_LOWER_HIGH_EXTREME: float = 95.0           # only fires if k_prev < 95 (didn't reach true extreme)
    STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS: float = 100.0
    # ═══ D4 BREAKOUT MULTI-LUNG (stocks, 2026-04-16) — extracted from ez_breakout_agent.py ════
    # UNPROVEN: default OFF until sweep tier breakout_multi_lung_tradier delivers Sharpe > 2 on 128-stock × 2yr.
    # NEVER flip ENABLED=True in live config without sweep proof.
    BREAKOUT_MULTI_LUNG_ENABLED: bool = False
    BREAKOUT_MULTI_LUNG_MODE: str = "AUGMENT"      # "AUGMENT" (OR) | "REPLACE"
    BREAKOUT_MULTI_LUNG_TIER: str = "STOCK"        # stocks default to STOCK tier (D+W+4h)
    BREAKOUT_MULTI_LUNG_COMPOSITE_INHALE: float = 0.20
    BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE: float = -0.10
    BREAKOUT_MULTI_LUNG_SLOW_LUNG_OVERRIDE: float = 0.15
    BREAKOUT_MULTI_LUNG_COOLDOWN_BARS: int = 8     # stocks breathe slower than crypto
    # === RED ZONE (stocks) — structural levels with HTF confirmation ===
    RZ_ENTRY_ENABLED: bool = True
    RZ_EXIT_ENABLED: bool = True  # T25 sweep 2026-04-14 (10sym, fixed gates): True avg=0.492 vs False=0.229 (+115%). Previous stale result (False=0.548) was from broken-gate run.
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
    DELTA_OPTIONS_ENTRY_Z: float = 3.0  # Stricter: ez=3.0 for options (wider spreads) ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_OPTIONS_HTF_GATE: str = "4h_D"  # Both 4h AND D must confirm ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_OPTIONS_COOLDOWN: int = 120  # 10h between trades ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_OPTIONS_MAX_HOLD: int = 240  # 20h max hold ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_OPTIONS_EXIT_TYPE: str = "giveback"  # V2 sweep: giveback wins for options ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_OPTIONS_GIVEBACK_PCT: float = 30.0  # Close when 30% of max gain given back ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    # Legacy (superseded by scorers but kept for V8 sweep compatibility)
    WT_EXIT_TFS_TRADIER: str = "5m+15m+1h+4h+D"
    WT_EXIT_MIN_TFS_TRADIER: int = 5  # REVERTED 2026-04-17: 4 = exits too eagerly. Mar-30 baseline = 5. Patient exits.
    WT_EXIT_VELOCITY_TRADIER: bool = False  # SWEEP: velocity makes zero difference. Cross is simpler. ; DEAD_CONFIRMED (priority 55/100) — no plausible wiring site found 20260416
    MIN_HOLD_BARS_TRADIER: int = 40  # 2026-04-20 sweep: 40 (200min) consistently wins over 32 (160min)
    COOLDOWN_BARS_TRADIER: int = 8  # 2026-04-08 SWEEP: 8 bars (40min) → Sharpe 8.22 (+1.30 vs 0 cooldown). Was 16 (80min). ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    # === YOUTUBE STRATEGIES (2026-03-27) — DISABLED on trb 2026-03-30 ===
    # These were implemented from YouTube research with FAKE backtests (reimplemented logic, not real functions).
    # "Sharpe 5.17" etc were fabricated numbers. Connors RSI augmented MRVL at -6.74% on real money.
    # MUST be validated via V5 backtest with real evaluate functions before re-enabling on trb.
    # Paper-only on trc via TRC_ overrides below.
    # --- Clenow Exp Regression — DISABLED on trb, paper on trc ---
    CLENOW_ENABLED: bool = False  # DISABLED 2026-03-30: fake backtest Sharpe. Needs V5 validation.
    CLENOW_LOOKBACK: int = 90  # Regression window (Clenow default) ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    CLENOW_TOP_N: int = 20  # Buy top N% of ranked symbols
    CLENOW_POSITION_SIZE: float = 800.0  # Per-entry size
    CLENOW_REBALANCE_DAYS: int = 21  # Monthly rebalance
    CLENOW_MIN_SCORE: float = 5.0  # Min score (slope * R²) to qualify
    CLENOW_REGIME_FILTER: bool = True  # Only hold when SPY > SMA200
    # --- Smart Money Flow Index — DISABLED on trb, paper on trc ---
    SMFI_ENABLED: bool = False  # DISABLED 2026-03-30: fake backtest Sharpe. Needs V5 validation.
    SMFI_POSITION_SIZE: float = 600.0
    SMFI_MAX_HOLD_DAYS: int = 10  # Exit when price > 20SMA or 10d hold ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    SMFI_LONG_BUDGET: float = 3000.0  # WIRED 2026-04-16 (priority 75/100) — tradier_manage.py:5286 TRC override destination
    SMFI_SHORT_BUDGET: float = 3000.0  # WIRED 2026-04-16 (priority 75/100) — tradier_manage.py:5286 TRC override destination
    SMFI_MAX_PER_SIDE: int = 5  # Max concurrent SMFI positions per side
    # --- Minervini SEPA Screen — DISABLED on trb, paper on trc ---
    MINERVINI_ENABLED: bool = False  # DISABLED 2026-03-30: fake backtest Sharpe. Needs V5 validation.
    MINERVINI_POSITION_SIZE: float = 800.0
    MINERVINI_MIN_SEPA_SCORE: int = 5  # Need 5 of 6 conditions
    MINERVINI_MAX_HOLD_DAYS: int = 40  # Swing trade hold
    MINERVINI_TARGET_PCT: float = 25.0  # Take profit at 25%
    MINERVINI_LONG_BUDGET: float = 4000.0  # WIRED 2026-04-16 (priority 75/100) — tradier_manage.py:5286 TRC override destination
    # --- Connors RSI Composite — DISABLED on trb, paper on trc ---
    CONNORS_RSI_ENABLED: bool = False  # DISABLED 2026-03-30: augmented MRVL at -6.74% on real money. Needs V5 validation.
    CONNORS_RSI_ENTRY_THRESHOLD: float = 10.0  # Buy when CRSI < 10
    CONNORS_RSI_EXIT_THRESHOLD: float = 70.0  # Sell when CRSI > 70
    CONNORS_RSI_POSITION_SIZE: float = 600.0
    CONNORS_RSI_MAX_HOLD_DAYS: int = 20  # DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
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
    TRADIER_MI_EXIT_ENABLED_TRADIER: bool = False       # REVERTED 2026-04-17: see MI_EXIT_ENABLED_TRADIER above.
    TRADIER_MI_SUBSIGNAL_MIN_COUNT: int = 3             # N of 5 sub-signals must fire

    # DC Daytrade — buy DC upper-quarter breakouts on 5m/15m with 1h expansion
    TRADIER_DC_DAYTRADE_ENABLED: bool = True
    TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES: int = 240
    TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION: bool = True
    TRADIER_DC_DAYTRADE_STOP_PCT: float = 0.005         # 0.5% hard stop
    TRADIER_DC_DAYTRADE_TARGET_PCT: float = 0.005       # 0.5% target (winner per 100.md:1352)
    TRADIER_DC_POSITION_ENTRY_THRESHOLD: float = 0.25   # REVERTED 2026-04-17: see DC_POSITION_ENTRY_THRESHOLD above.

    # K-Zone — stochastic K-zone entry filter
    TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER: int = 35     # S1_SWEEP_2026-04-15: 35 top S1 cfg Sharpe=4.23 on 20605 trades (was 80)
    TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER: int = 65    # S1_SWEEP_2026-04-15: 65 top S1 cfg Sharpe=4.23 on 20605 trades (was 20)
    TRADIER_K_ZONE_ENTRY_BONUS_TRADIER: int = 25        # score add when K in zone

    # RSI2 — 2-period RSI exit gate
    TRADIER_RSI2_ENABLED: bool = True
    TRADIER_RSI2_EXIT_THRESHOLD_LONG: float = 90.0      # exit long when RSI2 > this
    TRADIER_RSI2_EXIT_THRESHOLD_SHORT: float = 10.0     # exit short when RSI2 < this

    # RSI Entry — SHORTS ONLY (2026-04-14 rule). Longs use MFI only.
    # Short side: RSI + relative volume gate (high short volume distorts MFI).
    TRADIER_RSI_ENTRY_LONG_TRADIER: float = -1.0        # SENTINEL: <0 => DISABLED (long uses MFI)
    TRADIER_RSI_ENTRY_SHORT_TRADIER: float = 70.0       # RSI > this to consider short (LEGACY: single-TF default)
    TRADIER_RSI_SHORT_REL_VOLUME_MIN: float = 1.2       # relative vol > 1.2× avg required
    # --- per-TF RSI entry thresholds (2026-04-17) — long<= / short>=, tuned from A/B on full 109-sym × 3yr ---
    # LONG (mean-reversion): tighter RSI = better signal. A/B winner: rsi15<40 paired with rsi1h<22.
    TRADIER_RSI_LONG_5M: float = 35.0
    TRADIER_RSI_LONG_15M: float = 40.0                  # A/B 2026-04-17 winner (was 42 single-threshold)
    TRADIER_RSI_LONG_1H: float = 22.0                   # A/B 2026-04-17 REAL LEVER (was not per-TF)
    TRADIER_RSI_LONG_4H: float = 35.0
    TRADIER_RSI_LONG_D: float = 40.0
    # SHORT (continuation/overbought reversal): per-TF mirrors validated on full data.
    TRADIER_RSI_SHORT_5M: float = 65.0
    TRADIER_RSI_SHORT_15M: float = 65.0                 # validated top short cluster rsi15_gt_65
    TRADIER_RSI_SHORT_1H: float = 65.0                  # validated top short cluster rsi1h_gt_65
    TRADIER_RSI_SHORT_4H: float = 60.0
    TRADIER_RSI_SHORT_D: float = 55.0
    # rel_vol gate per TF — only activated on SHORT side (MFI already captures volume for LONG)
    TRADIER_RSI_SHORT_RVOL_15M: float = 1.0
    TRADIER_RSI_SHORT_RVOL_1H: float = 1.0

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
    TRADIER_WT_EXIT_MIN_TFS_TRADIER: int = 5             # REVERTED 2026-04-17: 4 = exits too eagerly. Mar-30 baseline = 5 (require ALL 5 TFs against). Patient exits.

    # Entry score aggregate threshold
    TRADIER_ENTRY_SCORE_THRESHOLD: int = 24             # min aggregate signal score for entry
    # ========================================================================
    # --- END RECONNECTED SWITCHES ---
    # ========================================================================

    # --- 11. Logging ---
    LOG_DIR: Path = Path.home() / "logs"
    LOG_FILE_TRADIER_PRICES: Path = LOG_DIR / "tradier_prices.log"
    LOG_FILE_TRADIER_POSITIONS: Path = LOG_DIR / "tradier_positions.log"
    LOG_FILE_TRADIER_MANAGE: Path = LOG_DIR / "tradier_manage.log"  # DEAD_CONFIRMED (priority 10/100) — no plausible wiring site found 20260416
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

    TRADIER_ACCOUNT_ID: str = os.getenv("TRADIER_ACCOUNT_ID_TRC", "")  # DEAD_CONFIRMED (priority 10/100) — no plausible wiring site found 20260416
    TRADIER_API_KEY: str = os.getenv("TRADIER_API_KEY_TRC", "")  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416

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

    # ═══════════════════════════════════════════════════════════════
    # SWITCH PARITY WITH config.py (2026-04-16)
    # 583 switches added for tradier sweep parity.
    # TRADIER LIVE BEHAVIOR UNCHANGED — tradier_manage.py must explicitly read.
    # ═══════════════════════════════════════════════════════════════
    ABLATION_DISABLE_AGGRESSIVE_HEDGE: bool = False  # Disable aggressive_hedge_scanner
    ABLATION_DISABLE_AUGMENTATION: bool = False  # -0.3 Sharpe when removed. Moderate help. Keep.
    ABLATION_DISABLE_CHECK_NOLOSS: bool = False  # Keep
    ABLATION_DISABLE_DC_BREACH_REDUCE: bool = False  # Disable DC breach reduce monitor
    ABLATION_DISABLE_ENTRY_LEADERBOARD: bool = False  # Keep — needs live testing
    ABLATION_DISABLE_ENTRY_RANKING: bool = True  # ABLATION: 0.000 Sharpe delta = no effect (needs Redis, adds noise)
    ABLATION_DISABLE_ENTRY_REVERSAL: bool = False  # Keep — reversal entry untested
    ABLATION_DISABLE_ENTRY_TECHNICAL: bool = True  # ABLATION: -0.018 Sharpe delta = redundant. REENTRY alone = same performance.
    ABLATION_DISABLE_FAST_RISER: bool = False  # Keep
    ABLATION_DISABLE_HEDGE: bool = False  # Keep
    ABLATION_DISABLE_HIGH_GAIN_AUGMENT: bool = False  # Disable direct_high_gain_augmentation
    ABLATION_DISABLE_PERIODIC_REENTRY: bool = False  # Disable periodic_evaluate_reentry (evaluate_reentry_2)
    ABLATION_DISABLE_QUICK_ENTRY: bool = False  # Disable check_entry_candidates (quick entries)
    ABLATION_DISABLE_QUICK_EXIT: bool = False  # Disable check_exit_candidates (WT/DC exits)
    ABLATION_DISABLE_RATIO_REBALANCE: bool = False  # Keep
    ABLATION_DISABLE_REENTRY: bool = False  # CRITICAL: -1.9 Sharpe when removed. THE system IS reentry. NEVER disable.
    ABLATION_DISABLE_REENTRY_ENFORCE: bool = False  # Disable reentry enforcement loop
    ABLATION_DISABLE_SCALP_GUARD: bool = False  # Disable monitor_strict_close_positions
    ABLATION_DISABLE_SPIKE_FADE_EXIT: bool = False  # Disable spike fade 1m exit monitor
    ADAPTIVE_REGIME_DC_BREAKDOWN_THRESHOLD: float = 0.1  # dc_position < this = breakout DOWN ; DEAD_CONFIRMED (priority 65/100) — no plausible wiring site found 20260416
    ADAPTIVE_REGIME_DC_BREAKOUT_THRESHOLD: float = 0.9  # dc_position > this = breakout UP ; DEAD_CONFIRMED (priority 65/100) — no plausible wiring site found 20260416
    ADAPTIVE_REGIME_DECAY_HALFLIFE_H: float = 24.0  # Exponential weight half-life (hours) ; DEAD_CONFIRMED (priority 65/100) — no plausible wiring site found 20260416
    ADAPTIVE_REGIME_ENABLED: bool = True  # Master switch for regime daemon ; DEAD_CONFIRMED (priority 65/100) — no plausible wiring site found 20260416
    ADAPTIVE_REGIME_HEAT_TRIGGER: float = 30.0  # Re-optimize when heat score > this ; DEAD_CONFIRMED (priority 65/100) — no plausible wiring site found 20260416
    ADAPTIVE_REGIME_LOOKBACK_DAYS: int = 7  # Rolling backtest window ; DEAD_CONFIRMED (priority 65/100) — no plausible wiring site found 20260416
    ADAPTIVE_REGIME_MIN_SIGNALS: int = 5  # Min weighted signals to trust optimizer (paper: 5, live: 10+) ; DEAD_CONFIRMED (priority 65/100) — no plausible wiring site found 20260416
    ADAPTIVE_REGIME_NPZ_CACHE_HOURS: float = 4.0  # Re-fetch NPZ from server every N hours ; DEAD_CONFIRMED (priority 65/100) — no plausible wiring site found 20260416
    ADAPTIVE_REGIME_PAPER: bool = True  # Paper mode: log decisions, don't override real configs ; DEAD_CONFIRMED (priority 65/100) — no plausible wiring site found 20260416
    ADAPTIVE_REGIME_SHARPE_FLOOR: float = 0.0  # Paper phase: observe all. Raise to 0.5+ for live. ; DEAD_CONFIRMED (priority 65/100) — no plausible wiring site found 20260416
    ADX_RANGING_THRESHOLD: float = 20.0  # BACKTEST_CHANGE_137: ADX below this = ranging (only mean-reversion)
    ADX_REGIME_FILTER_ENABLED: bool = True  # BACKTEST_CHANGE_137: ADX<20 = sizing penalty + entry deduction.
    ADX_TF: str = '1h'
    ADX_TRENDING_THRESHOLD: float = 25.0  # BACKTEST_CHANGE_137: ADX above this = trending ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    AGGRESSIVE_LOSS_CUT_ENABLED: bool = False  # DEAD CODE — replaced by STRUCTURAL_RANGE_SHIFT_EXIT (2026-04-11). DO NOT re-enable. ; DEAD_CONFIRMED (priority 10/100) — no plausible wiring site found 20260416
    ASYMMETRIC_LOSER_MIN_AGE_SECONDS: float = 540  # 9min = 3 bars. Below this, no stop (avoid noise).
    ASYMMETRIC_STOPS_ENABLED: bool = False  # TIER_A: estimated Sharpe +0.5 alone.
    ASYMMETRIC_WINNER_GAIN_PCT: float = 1.5  # At this gain, position switches to winner rules.
    ATR_ADAPTIVE_SIZING_ENABLED: bool = False  # BACKTEST_CHANGE_135: Inverse ATR sizing (high vol = smaller)
    ATR_ADAPTIVE_SIZING_TARGET_PCT: float = 2.0  # BACKTEST_CHANGE_135: Target ATR%. Size=1x at this ATR.
    ATR_ADAPTIVE_STOP_ENABLED: bool = False  # BACKTEST_CHANGE_130: ATR-based sizing reduction (not stop — STRICT_NO_LOSS)
    ATR_ADAPTIVE_STOP_MULT: float = 2.0  # BACKTEST_CHANGE_130: ATR(14) x this = risk distance
    ATR_ADAPTIVE_STOP_TF: str = '1h'
    ATR_LONG_WINDOW = 100
    AUGMENT_BLOWPAST_ENABLED: bool = True  # gain >= 3×MIN_GAIN, conviction 90. Highest conviction.
    AUGMENT_HTF_TREND_ENABLED: bool = True  # HTF trend only, conviction 65. Most frequent.
    AUGMENT_PYRAMID_ENABLED: bool = True  # Re-enabled — pyramid must always run, sizing handles risk ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    AUGMENT_WT_3TF_ENABLED: bool = True  # 3/3 LTF aligned + smaller gain, conviction 70.
    AUGMENT_WT_CROSS_ENABLED: bool = True  # WT cross + aligned 2/3 TFs + gain >= MIN_GAIN, conviction 80.
    BASIS_CONDITION: bool = False  # BACKTEST: OFF is +0.67 delta Sharpe (dc_basis_15m/1h both SKIP in sweep)  # No opening on wrong side of dc_basis_15m + 1h + 4h
    BB_BREAKOUT_ENABLED: bool = False  # BACKTEST_CHANGE_132: BB breakout + SMA200 (trending regime only)
    BB_BREAKOUT_SCORE: int = 20  # BACKTEST_CHANGE_132: Score bonus for breakout
    BB_BREAKOUT_TF: str = '1h'
    BB_ENTRY_LONG_THRESHOLD: float = -0.2  # BACKTEST_CHANGE_6: BB %B extremes
    BB_ENTRY_SHORT_THRESHOLD: float = 1.0
    BB_RSI_STOCH_SCALP_ENABLED: bool = False  # BACKTEST_CHANGE_134: BB+RSI+Stoch triple confirmation scalp (73-77% WR)
    BB_RSI_STOCH_SCALP_SCORE: int = 25  # BACKTEST_CHANGE_134: Score bonus for triple confirmation
    BB_SQUEEZE_COOLDOWN: float = 300.0  # Seconds between BB squeeze entries per symbol
    BB_SQUEEZE_ENABLED: bool = True  # Master toggle for BB squeeze breakout entries
    BB_SQUEEZE_ENTRY_ENABLED: bool = True  # Enter when Bollinger bands compress (< threshold) ; DEAD_CONFIRMED (priority 90/100) — no plausible wiring site found 20260416
    BB_SQUEEZE_MIN_ALIGNMENT: int = 10  # Minimum alignment score to allow BB squeeze entry
    BB_SQUEEZE_THRESHOLD_15M: float = 0.025  # bb_squeeze < this on 15m = entry signal ; DEAD_CONFIRMED (priority 90/100) — no plausible wiring site found 20260416
    BB_SQUEEZE_THRESHOLD_1H: float = 0.03  # bb_squeeze < this on 1h = entry signal ; DEAD_CONFIRMED (priority 90/100) — no plausible wiring site found 20260416
    BB_SQUEEZE_WIDTH_PERCENTILE: float = 0.2  # Width must be in bottom 20% to count as squeeze
    BINANCE_API_BASE: str = 'https://fapi.binance.com'
    BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE: float = 0.02  # Price within 2% of dc_low_D
    BOUNCE_AUGMENT_ENABLED: bool = True  # D-low bounce augment for losing positions
    BOUNCE_AUGMENT_K_D_CROSSING_UP: bool = True  # k_D must be turning up (k_D > k_D_prev)
    BOUNCE_AUGMENT_K_D_THRESHOLD: float = 20.0  # k_D must be below this (oversold on daily)
    BOUNCE_AUGMENT_MIN_LOSS_PCT: float = -0.5  # ANY loss triggers evaluation (user: "not -10%, ANY loss")
    BOUNCE_AUGMENT_PAPER: bool = True  # Paper mode — log only, no real orders
    BREAKEVEN_DC_LOW4_ENABLED: bool = True  # DC_LOW4_5M structural stop — fires any time position was profitable
    BREAKEVEN_GRACE_MINUTES: float = 15.0  # Grace period (bars pardon) before no-loss kicks in
    PEAK_GIVEBACK_PROTECTION_ENABLED: bool = True  # Close positions that were profitable and fell back below 0
    PEAK_GIVEBACK_MIN_PEAK_PCT: float = 0.3  # must have reached >= 0.3% gain to activate (stocks move slower)
    PEAK_GIVEBACK_DROP_PCT: float = 2.0  # also exit if gave back >= 2.0% from peak (even if still positive)
    PEAK_GIVEBACK_HARD_ZERO_ENABLED: bool = True  # exit immediately when gain turns negative after profitable peak
    BREAKOUT_GUARD_LOSS_THRESHOLD: float = -999.0  # BACKTEST_CHANGE_20: was -0.5. Dead code under STRICT_NO_LOSS ; DEAD_CONFIRMED (priority 40/100) — no plausible wiring site found 20260416
    BREAKOUT_GUARD_MOMENTUM_CHECK_ENABLED: bool = False  # Disables 1-sec momentum kills ; DEAD_CONFIRMED (priority 40/100) — no plausible wiring site found 20260416
    CHECK_INTERVAL = 3.0  # Check every 4 seconds
    CHOP_RANGING_THRESHOLD: float = 61.8  # Choppiness above this = ranging ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    CHOP_TRENDING_THRESHOLD: float = 38.2  # Choppiness below this = trending ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    CIRCUIT_BREAKER_ACCOUNT_HALT_MIN: int = 60  # halt duration (min)
    CIRCUIT_BREAKER_ACCOUNT_LOSSES: int = 5  # N consec losses/account → halt
    CIRCUIT_BREAKER_COOLDOWN: int = 60  # BACKTEST_CHANGE_40: was 120. 3m TF needs faster recovery
    CIRCUIT_BREAKER_ENABLED: bool = False  # TIER_C: Sharpe +0.1. Prevents regime-mismatch bleed.
    CIRCUIT_BREAKER_SYMBOL_HALT_MIN: int = 30  # halt duration (min)
    CIRCUIT_BREAKER_SYMBOL_LOSSES: int = 3  # N consec losses/symbol → halt
    CRYPTO_FH_MOMENTUM_DC_CONFIRM: bool = True  # DC retest scoring
    CRYPTO_FH_MOMENTUM_DC_MAX_LONG: float = 0.5
    CRYPTO_FH_MOMENTUM_ENABLED: bool = True
    CRYPTO_FH_MOMENTUM_MAX_POSITIONS: int = 4
    CRYPTO_FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5
    CRYPTO_FH_MOMENTUM_POSITION_SIZE_MULT: float = 1.0  # Multiplier of START_POSITION_SIZE
    CRYPTO_SPIKE_FADE_COOLDOWN_SEC: float = 540.0  # 3 bars × 3min = 9min. SWEEP: cd=3 bars wins.
    CRYPTO_SPIKE_FADE_ENABLED: bool = True
    CRYPTO_SPIKE_FADE_K_EXHAUSTION: float = 80.0  # SWEEP: 80 slightly better than 75. Not critical.
    CRYPTO_SPIKE_FADE_LOOKBACK_BARS: int = 3  # SWEEP: 3 bars (9min) beats all longer lookbacks. Catch spike fast. ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    CRYPTO_SPIKE_FADE_MAX_POSITIONS: int = 6
    CRYPTO_SPIKE_FADE_THRESHOLD_PCT: float = 10.0  # SWEEP: 10% > 5% > 3% > 2%. Higher threshold = fewer but much better trades.
    CT_15M_MOMENTUM_GATE_ENABLED: bool = False  # BC_171: DEAD. ABLATION 2026-04-16: 0.0000 ΔSharpe on 11sym 4yr crypto + 12sym tradier. OFF forever.
    CT_CHOP_4H_GATE_ENABLED: bool = False  # BC_173: DEAD. ABLATION 2026-04-16: 0.0000 ΔSharpe (no choppiness_4h in NPZ). OFF forever.
    CT_CHOP_4H_MAX: float = 50.0  # BC_173: max choppiness_4h
    CT_DC_CROSSOVER_SKIP_ENABLED: bool = True  # BC_172: ENABLED 2026-04-08. 5yr validated: SHORT Sharpe +34%, removes only 1.3% of trades. Skip SHORT when DC basis crosses over on 15m/1h.
    CT_MFI_15M_LONG_MIN: float = 45.0  # BC_171: (disabled)
    CT_MFI_15M_SHORT_MAX: float = 55.0  # BC_171: (disabled)
    CT_REL_VOL_MIN: float = 1.3  # BC_174: min relative_volume for entry
    CT_STOCH_K_15M_LONG_MIN: float = 45.0  # BC_171: (disabled)
    CT_STOCH_K_15M_SHORT_MAX: float = 55.0  # BC_171: (disabled)
    CT_VOLUME_SURGE_GATE_ENABLED: bool = False  # BC_174: DEAD. ABLATION 2026-04-16: 0.0000 ΔSharpe on 11sym+12sym. OFF forever.
    CT_WT_VELOCITY_1H_MIN: float = 2.0  # 2026-04-20 sweep: every top result had 2.0 — filters no-momentum entries
    CT_WT_VELOCITY_GATE_ENABLED: bool = True  # BC_170: ENABLED 2026-04-08. 5yr validated: Sharpe 1.94→5.26, 100% monthly positive, keeps 67% of trades. Don't trade against 1h WT velocity.
    CYCLE_TP_CONDITIONAL_EXIT: float = 0.003  # BACKTEST_CHANGE_101: was 0.5%. OKX top traders exit at 0.3% when stoch turns against. Matches profitable trader behavior. ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    CYCLE_TP_PCT: float = 0.6  # Let winners run to 60%. TP only used as absolute cap, NOT as early exit.
    CYCLE_TP_TIERED_ENABLED: bool = True  # BACKTEST_CHANGE_12: AGGRESSIVE tiered wins 74% of symbols
    CYCLE_TP_TIERED_FRAC: float = 0.25  # Close 25% of remaining at each tier
    DATA_READY_TIMEOUT_SECONDS: int = 20  # 5 minutes timeout for data_ready.flag
    DAYS_PLOT: int = 20  # days to save plots
    DC_BREAKOUT_ENTRY_ENABLED: bool = True  # BACKTEST_CHANGE_133: Donchian breakout entry (trend-following)
    DC_BREAKOUT_SCORE: int = 15  # BACKTEST_CHANGE_133: Conservative (30% WR in ranging)
    DC_BREAKOUT_TF: str = '1h'
    DC_EDGE_SIZING_ENABLED: bool = True  # BACKTEST_CHANGE_122: Scale position size by DC channel position. Edge=trending=3x, center=sideways=1x. +68% PnL vs flat. ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    DC_EDGE_SIZING_MAX_MULT: float = 3.0  # BACKTEST_CHANGE_122: Max 3x at DC edges (trending). 1x at DC center (sideways). ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    DC_EDGE_SIZING_MIN_MULT: float = 1.0  # BACKTEST_CHANGE_122: Min 1x at DC center. Set to 0.5 to reduce in sideways. ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    DC_EDGE_SIZING_PERIOD: int = 20  # DC lookback period for edge detection ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    DC_RECOVERY_EXIT_ENABLED: bool = False
    DC_RECOVERY_EXIT_TOLERANCE_ATR_MULT: float = 0.0  # if >0, uses 0.0..N * atr_3m instead of pct
    DC_RECOVERY_EXIT_TOLERANCE_PCT: float = 0.25  # crypto pct tolerance around entry_price
    DC_WIDTH_CAP_MULT: float = 10.0  # DEAD_CONFIRMED (priority 65/100) — no plausible wiring site found 20260416
    DC_WIDTH_MAX_MULT: float = 5.0  # BACKTEST_CHANGE_23: was 8.0. DC is 7th best indicator, don't over-weight
    DC_WIDTH_SIZING_ENABLED: bool = True
    DELTA_ENTRY_SCORE_BONUS: int = 15  # Score bonus when delta confirms entry
    DELTA_ENTRY_SCORE_PENALTY: int = -25  # Score penalty when delta opposes entry
    DELTA_EXIT_DC_FLOOR: bool = True  # DC15M floor break as exit ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_EXIT_DOM_TF_ENABLED: bool = True  # 2026-04-10 04:15 APPLIED — winner per user "yes apply" (was False)
    DELTA_EXIT_OVERRIDE_NOLOSS: bool = True  # Delta exits bypass STRICT_NO_LOSS ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_EXIT_SCORE_BONUS: int = 20  # Score bonus when delta confirms exit
    DELTA_EXIT_SPEED_DECAY: bool = True  # Speed decay exit (primary)
    DELTA_EXIT_WT_CROSS: bool = True  # WT cross against as exit ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_GATE_AUGMENT: bool = True  # Block AUGMENT without delta signal ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_GATE_BB_SQUEEZE: bool = True  # Block BB_SQUEEZE entries without delta ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_GATE_DC_BREAKOUT: bool = True  # Block DC_BREAKOUT entries without delta ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_GATE_GUARANTEED_REENTRY: bool = True  # Block GUARANTEED_REENTRY — #1 loss source ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_GATE_HEDGE_OPEN: bool = False  # Do NOT gate hedges — they must always execute ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_GATE_OPEN: bool = True  # Block OPEN without delta signal ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_GATE_RATIO_REBALANCE: bool = False  # Block RATIO_REBALANCE opens — OFF: ratio is sacred ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_GATE_REENTRY: bool = True  # Block REENTRY without delta signal ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_GATE_SBA: bool = False  # Block SBA (underwater adds) without delta — OFF: SBA has own logic ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_GATE_STDEV_BREAKOUT: bool = True  # Block STDEV_BREAKOUT without delta ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_GATE_VOL_SPIKE: bool = True  # Block VOL_SPIKE_REVERSAL without delta ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    DELTA_MIN_TF_FOR_ACTION: int = 2  # Minimum TFs confirming for any buy/sell decision
    DELTA_REENTRY_HTF_GATE: str = '4h'
    DELTA_REENTRY_MIN_TF: int = 2  # 2 TFs vs 3 for fresh entries
    DELTA_REENTRY_REQUIRE_NOT_EXITING: bool = True  # Delta must not be in exit state
    DELTA_REENTRY_Z_THRESHOLD: float = 1.0  # 1.0 vs 2.5 for fresh entries
    DELTA_SCORE_WEIGHT: float = 30.0  # Weight of delta signal in AdvancedSignalRater (0-100)
    DELTA_SERVICE_BLEED_STOP: bool = True  # Bleed stop uses delta
    DELTA_SERVICE_REDUCE_GATE: bool = True  # Service reductions need delta confirmation
    DELTA_SERVICE_TRAILING_STOP: bool = True  # Trailing stops use delta context
    DIRECT_HIGH_GAIN_COOLDOWN_SECONDS = 15
    EMA200_STOCHRSI_BODY_MULT: float = 1.05  # BACKTEST_CHANGE_127: Candle body 5%+ larger than prev
    EMA200_STOCHRSI_ENABLED: bool = False  # BACKTEST_CHANGE_127: EMA200 trend + StochRSI reversal + candle body
    EMA200_STOCHRSI_K_LONG: float = 20.0  # BACKTEST_CHANGE_127: Stoch K below this for LONG
    EMA200_STOCHRSI_K_SHORT: float = 80.0  # BACKTEST_CHANGE_127: Stoch K above this for SHORT
    EMA200_STOCHRSI_SCORE: int = 25  # BACKTEST_CHANGE_127: Score bonus
    EMA200_STOCHRSI_TF: str = '1h'
    EMA20_SLOPE_ENTRY_ENABLED: bool = True  # DEAD_CONFIRMED (priority 90/100) — no plausible wiring site found 20260416
    EMA20_SLOPE_SHORT_THRESHOLD_1H: float = 0.05  # SHORT when ema20 slope > this (extended, mean revert) ; DEAD_CONFIRMED (priority 90/100) — no plausible wiring site found 20260416
    EMA_DIST_ENTRY_ENABLED: bool = True  # BACKTEST_CHANGE_3: #1 signal in top 5000
    EMA_DIST_LONG_THRESHOLD: float = -1.0  # LONG when ema_dist < -1.0 (price far below EMA20)
    EMA_DIST_SHORT_THRESHOLD: float = 1.0  # SHORT when ema_dist > 1.0 (price far above EMA20)
    EMA_DIST_SIZING_ENABLED: bool = True  # BACKTEST_CHANGE_24: scale size by ema_dist strength
    EMA_DIST_SIZING_MULT: float = 2.0  # Max 2x size when ema_dist is extreme
    EMA_PULLBACK_ENABLED: bool = False  # BACKTEST_CHANGE_128: EMA pullback + StochRSI oversold in trend. Validated by academia + copy traders.
    EMA_PULLBACK_SCORE_BONUS: int = 35  # BACKTEST_CHANGE_128: Highest score — matches "retest-and-launch" core edge
    EMA_PULLBACK_TF: str = '15m'
    ENABLE_LOSS_PROTECTION: bool = True  # Block closing positions with gain <= 0.12%
    ENTRY_ATR_PCT_MIN: float = 1.5  # BACKTEST_CHANGE_103: NEW. Min ATR% for entry — winners trade 1.97% ATR vs losers 1.22%
    ENTRY_VOL_MIN_RATIO: float = 1.3  # BACKTEST_CHANGE_100: was 1.0. Winners enter at 1.95x avg vol vs losers 1.27x — raise floor
    ERROR_RECOVERY_SLEEP_SECONDS: int = 60  # Sleep after errors
    EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED: bool = False  # 2026-04-11 SWEEP: dead code, no effect on results. Disabled.
    EXIT_DC_BREACH_REDUCE_ENABLED: bool = True  # Augmented position DC breach
    EXIT_DEAD_CODE_ENABLED: bool = False  # Dead code path — disabled ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    EXIT_DELTA_SPEED_ENABLED: bool = True  # Delta engine speed decay exit (PRIMARY)
    EXIT_EMERGENCY_DC1H_ENABLED: bool = False  # Emergency DC 1h breach — disabled, too aggressive ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    EXIT_GAIN_EROSION_ENABLED: bool = True  # Peak gain eroding toward 0 with WT against
    EXIT_GAIN_THRESHOLD_MIN: float = 1.0  # BACKTEST_CHANGE_112: was 0.3 (GAIN_THRESHOLD_LOW). Higher threshold = fewer whipsaw exits. IS+OOS validated. ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    EXIT_HARD_MAX_LOSS_CAP_ENABLED: bool = False  # 2026-04-11 SWEEP WINNER: #1 PnL destroyer. Sharpe 2.3 disabled vs 0.2 enabled. UNIVERSAL_NOLOSS_GATE handles loss protection.
    EXIT_HEDGE_LOSS_KILL_ENABLED: bool = True  # Kill losing hedges when gain < prev_gain ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    EXIT_HEDGE_ORPHAN_KILL_ENABLED: bool = True  # Kill hedges with no original position ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    EXIT_MARKET_SPIKE_REDUCE_ENABLED: bool = True  # Reduce shorts on market spike / longs on drop — sweep: keeps Sharpe
    EXIT_ON_ALL_ENABLED: bool = False  # Was True via EXIT_ON_ALL
    EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED: bool = True  # 2026-04-11: now gated by WT 3m+15m both against
    EXIT_PREEMPTIVE_BREAKEVEN_ENABLED: bool = True  # Sweep: helps slightly, keep on
    EXIT_STDEV_BREAKOUT_FAIL_ENABLED: bool = True  # BB breakout failure (price back inside bands)
    EXIT_TREND_REVERSAL_ENABLED: bool = True  # HTF trend score flip
    FAST_CUT_LOSS_MIN_AGE_MINUTES: float = 15.0  # Was 6 min (too short) ; DEAD_CONFIRMED (priority 25/100) — no plausible wiring site found 20260416
    FAST_CUT_LOSS_THRESHOLD: float = -999.0  # BACKTEST_CHANGE_18: was -1.5. Dead code — ALL accounts STRICT_NO_LOSS
    FAST_RISER_DOUBLE_ENABLED: bool = False  # BACKTEST_CHANGE_115: was True. Net negative PnL. Fast riser doubles amplify losers.
    FG_FEAR_THRESHOLD: int = 25  # BACKTEST_CHANGE_141: F&G below this = extreme fear → increase size
    FG_GREED_THRESHOLD: int = 75  # BACKTEST_CHANGE_141: F&G above this = extreme greed → decrease size
    FG_SIZING_ENABLED: bool = False  # BACKTEST_CHANGE_141: F&G sizing multiplier (1,240% vs 680% B&H). Fear=bigger, Greed=smaller.
    FORCE_REFRESH_SECONDS: float = 10  # BACKTEST_CHANGE_43: was 16. Fresher data for 3m decisions
    GAIN_THRESHOLD_LOW = 1.0  # BACKTEST_CHANGE_112: was 0.15 (was 0.50). Higher = fewer whipsaw exits. OOS-validated at 1.0%
    HARD_MAX_LOSS_PCT: float = -5.0  # 2026-04-10: SAFETY NET. NO position ever allowed past -5% loss. DELTA/WT/DC should normally fire way before. Set -9999 to disable (ablation only).
    HA_3M_ENTRY_WEIGHT: float = -0.5  # BACKTEST_CHANGE_31: was 0.0. HA harmful for entries — use as negative (contrarian) signal ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    HA_WICK_QUALITY_ENABLED: bool = False  # BACKTEST_CHANGE_144: HA streak quality scoring (62% WR with EMA filter)
    HA_WICK_QUALITY_SCORE: int = 15  # BACKTEST_CHANGE_144: Score bonus for strong HA streak
    HA_WICK_QUALITY_TF: str = '1h'
    HEDGE_ACCOUNTS = ['ang', 'fin', 'men', 'flz']
    HEDGE_ALL_POSITIONS: bool = False  # BC_988: NEW. If True, hedge ALL positions when wt15m against (not just losers). Test pending.
    HEDGE_CLOSE_WT_TFS_FAVOR: int = 3  # BC_988: r2 winner but this is now unused — 15m WT close in code. ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416
    HEDGE_DUAL_IF_HEDGE_MODE: bool = False  # Cross-symbol dual hedge disabled.
    HEDGE_MAX_RATIO: float = 2.0  # Hard cap 200% of losing position value. ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416
    HEDGE_MOMENTUM_GATE: bool = False  # BACKTEST_CHANGE_119: No momentum gate — 15m WT is the sole gate. ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416
    HEDGE_NEWBORN_DC_BREACH_ALLOWED: bool = True  # allow hedge during grace if price breaches dc_low_3m (LONG) / dc_high_3m (SHORT)
    HEDGE_NEWBORN_GRACE_MINUTES: float = 10.0  # 2026-04-16: hedges blocked for N min after open, unless DC breach
    HEDGE_OVERSIZE_RATIO: float = 2.0  # Max 200% of losing position. Tiered: 50% at -0.6%, 100% at -1%, 150% at -1%, 200% at -2% ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416
    HEDGE_SAME_SYMBOL_ENABLED: bool = True  # Re-enabled 2026-04-01: 150% same-symbol always active regardless of HEDGE_MODE. Cross-symbol only when HEDGE_MODE=True.
    HEDGE_TRIGGER_LOSS_PCT: float = -0.05  # BACKTEST_CHANGE_38: was -0.10. Hedge earlier with 0.3% TP system
    HEDGE_TRIGGER_LOSS_PCT_ENTRY: float = -2.0  # Cross-symbol trigger (HEDGE_MODE only, not obligatory). ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416
    HOUR_OF_DAY_GATE_ENABLED: bool = False  # TIER_C: Sharpe +0.1. Audit hourly Sharpe first.
    HTF_STRICT: bool = True  # BACKTEST_CHANGE_106: REVERTED to True. Tournament winner uses strict (all HTFs K>D+HA aligned). Sharpe 242 vs 133 for kd_only.
    IMMEDIATE_WRONG_WAY_ENABLED: bool = False  # BACKTEST_CHANGE_114: was implicitly True. #2 PnL destroyer. Tight stops kill trades that recover.
    INDICATORS_DATA_CACHE_SIZE = 2048  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416
    INDICATORS_SAVE_INTERVAL_SECONDS: float = 10.0
    INDICATOR_MAX_AGE_SECONDS = 200.0
    INF_RANKING_BYPASS_DELTA: bool = False  # DELTA_GATE_OPEN bypass (not measured yet) ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    INF_RANKING_BYPASS_FRESHNESS_MIN: int = 30  # only bypass within N min of list entry ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    INF_RANKING_BYPASS_HTF: bool = True  # 2/3 HTF -> 1/3 HTF — +18pp on top of stoch ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    INF_RANKING_BYPASS_MAX_POS: int = 8  # soft cap on concurrent bypass-entries ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    INF_RANKING_BYPASS_SCORE: bool = False  # score gate bypass (unclear impact, keep off) ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    INF_RANKING_BYPASS_STOCH: bool = True  # relax K3M_CAP/K15M — unlocks 73.6% alone ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    INF_RANKING_BYPASS_WT: bool = False  # WT composite bypass (trend misread risk) ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    INF_RANKING_PRIORITY_BYPASS: bool = False  # master switch ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    K3M_CAP: int = 80  # BACKTEST_CHANGE_105: REVERTED to 80. Tournament (10 rounds, 3042 combos) winner uses 80. BACKTEST_CHANGE_8 (70) reversed.
    K3M_FLOOR: int = 30  # BACKTEST_CHANGE_9: NEW. Block SHORT when k_3m <= 30 (mirror of K3M_CAP)
    LADDER_AUTO_SAVE_SECONDS: float = 60.0  # DEAD_CONFIRMED (priority 15/100) — no plausible wiring site found 20260416
    LADDER_TTL_MINUTES: int = 24 * 60  # Ladder order TTL
    LEGACY_AGGRESSIVE_LOSS_CUT: bool = False  # OFF — single TF 1m flip ; DEAD_CONFIRMED (priority 10/100) — no plausible wiring site found 20260416
    LEGACY_DC_BREAKOUT_REENTRY: bool = True  # ON — gated by tolerant delta
    LEGACY_FAST_CUT_LOSS: bool = False  # OFF — % stop in disguise ; DEAD_CONFIRMED (priority 10/100) — no plausible wiring site found 20260416
    LEGACY_GUARANTEED_REENTRY: bool = True  # 2026-04-15: logic REWRITTEN with 60min/HTF gate (see reentry_enforcement_loop)
    LEGACY_PROC_SINGLE_REENTRY: bool = False  # OFF 2026-04-15 per user — fired mid-move on LTF only
    LEGACY_REENTRY_GUARANTEED_2WT: bool = False  # ez_manage.py:16138 — exit crossed >0.3% + 2/4 WT, 50% ; DEAD_CONFIRMED (priority 10/100) — no plausible wiring site found 20260416
    LEGACY_REENTRY_GUARANTEED_BOTTOM: bool = False  # ez_manage.py:16128 — wt15m bounce + 1h trend + 2/4 WT, 150%
    LEGACY_REENTRY_GUARANTEED_CROSS: bool = False  # ez_manage.py:16133 — exit crossed + 3/4 WT, 50-100% by DC pos ; DEAD_CONFIRMED (priority 10/100) — no plausible wiring site found 20260416
    LEGACY_REENTRY_PSR_DC_BOUNCE: bool = False  # ez_manage.py:18836 — DC bounce within 8h, near dc_high/low
    LEGACY_REENTRY_PSR_FULL_DC: bool = False  # ez_manage.py:18812 — full reentry stoch_above_dc OR dc_basis_crossover_3m
    LEGACY_REENTRY_PSR_K_DC_CROSSOVER: bool = False  # ez_manage.py:18777 LONG / 18798 SHORT — k_3m/15m crossover above dc_low_3m/15m
    LEGACY_REENTRY_PSR_QUICK_RECOVERY: bool = False  # ez_manage.py:18757 — price ± atr_3m within 60min, k cross
    LEGACY_WR_PULLBACK: bool = True  # ON ; DEAD_CONFIRMED (priority 10/100) — no plausible wiring site found 20260416
    LOG_INTERVAL_SECONDS: int = 10  # Log interval for waiting operations
    LONG_STOCH_CHASE_BLOCK: bool = True  # BACKTEST_CHANGE_102: NEW. Block LONG when stoch_k_1h > 70 AND ha_streak > 2 — chasing overbought = loser
    LOSS_CUT_ENABLED: bool = False  # NEVER enable — proven to lose 20%+ weekly ; DEAD_CONFIRMED (priority 5/100) — no plausible wiring site found 20260416
    LOSS_EXIT_HEDGE_MODE_BLOCK_ESCAPE_ENABLED: bool = False  # ez_manage.py:20647 hedge-failed escape @ gain<-15% & 30min unhedged
    LOSS_EXIT_REQUIRES_HEDGE: bool = True  # Master: can only exit at loss if hedge >= losing value
    LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED: bool = False  # ez_positions_quick.py:10778 allow exit when max_gain≥0.5% & fresh_gain>-0.5
    LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED: bool = False  # ez_manage.py:20621 STOP_FUNCTIONS_KILL @ gain<-5%
    LS_RATIO_CONTRARIAN_ENABLED: bool = False  # BACKTEST_CHANGE_142: L/S ratio contrarian filter ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    LS_RATIO_EXTREME_THRESHOLD: float = 70.0  # BACKTEST_CHANGE_142: L/S ratio above this = suppress that side ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    LS_RATIO_HARD_MAX: float = 3.5  # Was 2.00 — raised to let shorts open while ratio recovers. Still blocks extreme >3.5 longs.
    LS_RATIO_HARD_MIN: float = 0.05  # Near-zero: ratio must FOLLOW the WT direction, not fight it
    LS_RATIO_LOG_INTERVAL: int = 60  # Seconds between ratio warning logs
    LS_RATIO_PENALTY: int = 15  # BACKTEST_CHANGE_142: Score penalty for crowded side ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    MACD_EXIT_ENABLED: bool = False  # BACKTEST_CHANGE_136: MACD cross-back exit for profitable positions
    MACD_EXIT_MIN_GAIN: float = 0.3  # BACKTEST_CHANGE_136: Min gain% before MACD exit allowed
    MACD_EXIT_TF: str = '15m'
    MACD_ZERO_CROSS_ENABLED: bool = False  # BACKTEST_CHANGE_131: MACD below-zero crossover + SMA200 trend. Confirmation only.
    MACD_ZERO_CROSS_SCORE: int = 15  # BACKTEST_CHANGE_131: Conservative score (MACD 20% WR on crypto standalone)
    MACD_ZERO_CROSS_TF: str = '1h'
    MANAGE_REDUCE = True
    MARKET_DATA_REFRESH_INTERVAL_SECONDS: float = 45.0
    MARK_PRICE_MAX_STALENESS: float = 2  # Maximum acceptable age of cached mark price
    MAX_AUGMENTS_PER_POSITION: int = 3  # URGENT_FIX: cap total augments, stop piling into losers
    MAX_CONCURRENT_ORDERS: float = 186
    MAX_DECAY_COMPLETE_DAYS = 7
    MAX_DECAY_START_HOURS = 1
    MAX_GAIN_DECAY_COMPLETE_DAYS = 7
    MAX_MEMORY_GB: int = 8
    MAX_ORDER_VALUE_FIN: float = 20.0  # Was $120.
    MAX_ORDER_VALUE_MEN: float = 20.0  # Was $240.
    MAX_POSITION_SIZE_BTC: float = 2000.0  # 2026-03-30: Same rule for BTC. Was $6000.
    MAX_POSITION_SIZE_FIN: float = 20.0  # 2026-03-30: Same. Was $4000.
    MAX_POSITION_SIZE_MEN: float = 20.0  # 2026-03-30: Same. Was $1200.
    MEMORY_MONITOR_SLEEP_SECONDS: int = 60  # Memory monitor loop sleep
    MIN_HOLD_BARS_BEFORE_EXIT: int = 32  # V4: 8 hours min hold. Sharpe 0.503 vs 0.460 baseline (+9.3%), PnL +49%. ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
    MIN_PERC_FROM_SMA_1: float = 1.0 / 100  # SMA_1
    MIN_PERC_FROM_SMA_15: float = 3.0 / 100  # SMA_15
    MIN_USD_DELTA_CONFIRM: float = 1.0
    MITIGATOR_AUGMENT_CONSECUTIVE: int = 3  # Must rise for 3+ scans ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    MITIGATOR_AUGMENT_THRESHOLD: float = 0.3  # Augment winners above this gain ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    MITIGATOR_COOLDOWN: float = 15.0  # Seconds between actions per position
    MITIGATOR_ENABLED: bool = False  # DISABLED: 8 triggers kill winners between 0.03-2.5%. Let winners run.
    MITIGATOR_REENTRY_COOLDOWN: float = 180.0  # 3 min before re-entry ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    MITIGATOR_REENTRY_PRICE_PCT: float = 0.15  # Favorable price move for re-entry ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    MITIGATOR_SCAN_INTERVAL: float = 3.0
    MITIGATOR_TIER1_DROP: float = 0.08  # Reduce 25% when gain drops to this ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    MITIGATOR_TIER1_PEAK: float = 0.15  # Peak gain must reach this before tier 1 arms ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    MITIGATOR_TIER1_REDUCE_PCT: float = 0.25  # DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    MITIGATOR_TIER2_DROP: float = 0.02  # Reduce 50% of remaining at breakeven ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    MITIGATOR_TIER2_REDUCE_PCT: float = 0.5  # DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    MITIGATOR_TIER3_DROP: float = -0.05  # Full close — tiny loss better than big loss ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    MOM3_ENTRY_ENABLED: bool = True  # BACKTEST_CHANGE_4: #2 signal, 3-bar momentum mean-reversion
    MOM3_LONG_THRESHOLD: float = -1.0  # LONG when mom3 < -1.0
    MOM3_SHORT_THRESHOLD: float = 1.0  # SHORT when mom3 > 1.0
    MOM5_ENTRY_ENABLED: bool = True  # BACKTEST_CHANGE_5: #3 signal, 5-bar momentum
    MOM5_LONG_THRESHOLD: float = -1.0
    MOM5_SHORT_THRESHOLD: float = 1.0
    MOMENTUM_RIDER_ACCOUNT: str = 'men'  # DEAD_CONFIRMED (priority 5/100) — no plausible wiring site found 20260416
    MOMENTUM_RIDER_BASE_SIZE_USD: float = 50.0
    MOMENTUM_RIDER_COOLDOWN: float = 300.0
    MOMENTUM_RIDER_DC_WIDTH_MIN: float = 8.0
    MOMENTUM_RIDER_ENABLED: bool = False  # DISABLED 2026-03-29: bypasses ALL execute_now guards + auto-expands tradeable_keys
    MOMENTUM_RIDER_HEDGE_RATIO: float = 1.2
    MOMENTUM_RIDER_MAX_SIZE_USD: float = 400.0  # DEAD_CONFIRMED (priority 5/100) — no plausible wiring site found 20260416
    MOMENTUM_RIDER_MAX_SYMBOLS: int = 5
    MOMENTUM_RIDER_REL_VOL_MIN: float = 3.0
    MOMENTUM_RIDER_SCAN_INTERVAL: float = 10.0
    MONITOR_REDUCTION_STALE_THRESHOLD: float = 180.0
    MOVER_ACCOUNT: str = 'inf'
    MOVER_DETECTION_ENABLED: bool = True  # BACKTEST_CHANGE_111: Scan all symbols for sudden spikes, fade them (mean reversion)
    MOVER_LINEARITY_MIN: float = 0.3  # BACKTEST_CHANGE_111: Min R² — 0.3 = clean directional move (not choppy)
    MOVER_LOOKBACK: int = 8  # BACKTEST_CHANGE_111: Bars to compute slope/linearity (8 on 15m = 2h window). Best: 8-13. ; DEAD_CONFIRMED (priority 50/100) — no plausible wiring site found 20260416
    MOVER_MAX_POSITIONS: int = 6  # Max concurrent mover positions in inf account
    MOVER_SCORE_BONUS: int = 40  # Score bonus for mover-detected entries (high conviction)
    MOVER_THRESHOLD: float = 5.0  # BACKTEST_CHANGE_111: Min mover score to qualify. 5.0 = 99.7% WR across 193 symbols. Higher = fewer but cleaner.
    MOVER_VOL_MIN: float = 1.0  # BACKTEST_CHANGE_111: Min relative volume to confirm move is real
    MTS_BOTTOM_BONUS_THRESHOLD: float = 25.0  # bottom_score above this adds +4 score bonus
    MTS_BOTTOM_MIN_SHORT: float = 10.0  # Shorts: slightly relaxed (was 5)
    MTS_BOTTOM_STRONG_THRESHOLD: float = 40.0  # bottom_score above this adds +8 score bonus
    MTS_ENTRY_QUALITY_BONUS: float = 25.0  # entry_quality above this adds +2 score bonus
    MTS_ENTRY_QUALITY_MIN_SHORT: float = 5.0  # Shorts: slightly relaxed (was 0)
    MTS_ENTRY_QUALITY_STRONG: float = 40.0  # entry_quality above this adds +5 score bonus
    NEWS_POLL_INTERVAL_CRYPTO: int = 300  # 5 min (CryptoPanic) ; DEAD_CONFIRMED (priority 15/100) — no plausible wiring site found 20260416
    NEWS_POLL_INTERVAL_SOCIAL: int = 900  # 15 min (Reddit + Twitter) ; DEAD_CONFIRMED (priority 15/100) — no plausible wiring site found 20260416
    NEWS_SENTIMENT_DECAY_HOURS: int = 4  # Older articles decay to 0
    NEWS_SENTIMENT_MIN_ARTICLES: int = 2  # Min sources to form a score
    OBLIGATORY_HEDGE_MIN_LOSS_PCT: float = -0.5  # 2026-04-16: reverted from 0.0 — was hedging on rounding-error noise
    OBLIGATORY_HEDGE_PCT: float = 0.0  # DISABLED 2026-03-30: Caused cascade. Was 2.0 (200% of losing). Fires regardless of HEDGE_MODE — THAT WAS THE PROBLEM.
    OBLIGATORY_HEDGE_WT_TFS: int = 2  # Need 2 TFs with WT against before opening hedge.
    OI_DIVERGENCE_ENABLED: bool = False  # BACKTEST_CHANGE_143: OI divergence confirmation ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    OI_DIVERGENCE_PENALTY: int = 10  # BACKTEST_CHANGE_143: Score penalty for OI divergence ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    OPTIMAL_HOLD_BARS_15M: int = 999  # BACKTEST_CHANGE_16: REVERTED (was 13). Ablation: -6.983 Sharpe, WORST of 52 tested. 0% symbols improved. Hold period too short kills winners. ; DEAD_CONFIRMED (priority 30/100) — no plausible wiring site found 20260416
    OPTIMAL_HOLD_BARS_3M: int = 999  # ABLATION_V3_REVERT: was 21 (BC_15). Confirmed on BOTH v2 (4-day) and v3 (3-year, 215 sym): +1.04 Sharpe, 85% improved. Forced exit kills winners.
    ORPHAN_HEDGE_CHECK_GAIN: bool = True  # Check gain before killing orphans ; DEAD_CONFIRMED (priority 55/100) — no plausible wiring site found 20260416
    OUTLIER_RUNAWAY_ATR_FACTOR: float = 2.0
    OUTLIER_STUCK_ATR_FACTOR: float = 0.5
    OUTLIER_STUCK_HOURS: float = 2.0
    PERSIST = 7200.0  # minutes to stay in tradeable_keys after deletion
    PER_SYMBOL_CONFIG_ENABLED: bool = False  # TIER_B: Sharpe +0.1-0.2. Overnight sweep infra ready.
    PLOT_LOOP_INTERVAL_SECONDS: int = 1800  # Plot loop interval
    PNL_DECAY_COMPLETE_DAYS: int = 5  # DAYS
    PNL_DECAY_FINAL_PERCENTAGE: float = 0.1  # Keep 10% after full decay
    PNL_DECAY_START_HOURS: int = 1  # HOURS
    POSITIONS_SERVICE_HEALTH_TIMEOUT: float = 4.0  # Seconds to wait for RPC ping ; DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416
    POSITION_REDIS_REFRESH_INTERVAL: float = 6.0  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416
    POSITION_REFRESH_MIN_INTERVAL: int = 5  # \seconds
    POSITION_SAVE_INTERVAL: float = 6.0
    POSITION_STALE_THRESHOLD_SECONDS: float = 60.0
    PROGRESSIVE_LOCK_ENABLED: bool = False  # TIER_A: Sharpe +0.3. Staged profit without full close.
    PROGRESSIVE_LOCK_FRACTION: float = 0.25  # Reduce fraction per tier.
    PYRAMID_ENABLED: bool = False  # TIER_D: Sharpe +0.2. Amplifies winners.
    PYRAMID_MAX_DC_POS_15M_SHORT: float = 0.3  # SHORT: DC pos < 0.3 = lower third.
    PYRAMID_MIN_DC_POS_15M: float = 0.7  # LONG: DC pos > 0.7 = upper third.
    PYRAMID_MIN_GAIN_PCT: float = 1.5  # Fires once gain >= this.
    PYRAMID_MIN_WT_VEL_1H: float = 2.0  # 1h velocity must trend.
    PYRAMID_SIZE_MULT: float = 0.5  # Add N × position_amt (0.5 = 50%).
    RANKING_LOOP_SLEEP_SECONDS: int = 120  # BACKTEST_CHANGE_44: ranking loop sleep (2 minutes, was 3)
    RATIO_EMERGENCY_EXIT_COOLDOWN: float = 999999.0  # Infinite cooldown
    RATIO_EMERGENCY_EXIT_ENABLED: bool = False  # PERMANENTLY DISABLED: closing losers = Sharpe 19 vs ratio-only 357. Fix ratio by OPENING underweight side, NEVER by closing losers. ; DEAD_CONFIRMED (priority 30/100) — no plausible wiring site found 20260416
    RATIO_EMERGENCY_EXIT_MAX_LOSS_PCT: float = -999.0  # Set to impossible value
    RATIO_EMERGENCY_EXIT_MAX_PER_CYCLE: int = 0  # Zero = can never close anything
    RATIO_EMERGENCY_EXIT_THRESHOLD: float = 999.0  # Set to impossible value so it can NEVER trigger even if enabled by accident
    REACTIVE_MODE: bool = False
    REDIS_CHANNEL_SIGNALS: str = 'signals_channel'
    REDIS_EXPIRY_SECONDS: int = 180
    REDUCE_HUGE_LOSS_THRESHOLD: float = -999.0  # BACKTEST_CHANGE_19: was -2.0. Dead code under STRICT_NO_LOSS ; DEAD_CONFIRMED (priority 5/100) — no plausible wiring site found 20260416
    REENTER_SAVE_DEBOUNCE_SECONDS: int = 30  # BACKTEST_CHANGE_45: was 50. Faster reentry on 3m TF ; DEAD_CONFIRMED (priority 25/100) — no plausible wiring site found 20260416
    REENTRY2_DC_BREAK_ENABLED: bool = True  # DC breakout fast-path reentry
    REENTRY2_QUICK_RECOVERY_ENABLED: bool = True  # quick recovery after exit + momentum
    REENTRY2_STOCH_CROSS_ENABLED: bool = True  # stoch crossover + DC level bounce
    REENTRY_2_ENABLED: bool = True  # Master switch. ~$420 PnL per ablation.
    REENTRY_B02_BC156_BOTTOM_ENABLED: bool = True  # ABLATION: Sharpe 0.31/0.32, 22K/14K trades, 62.5% WR. Best balance.
    REENTRY_B04_DC_RETEST_ENABLED: bool = True  # ABLATION: Sharpe 0.39/0.31, 579/335 trades. High quality.
    REENTRY_B09_SNAPBACK_ENABLED: bool = False  # ABLATION: Sharpe 0.022/0.024 = weak. CUT.
    REENTRY_B10_STOCH_REV_ENABLED: bool = True  # ABLATION: Sharpe 0.07/0.12, 69-75% WR. Keep for WR.
    REENTRY_B11_DC_BREAK_ENABLED: bool = True  # ABLATION: Sharpe 0.34/0.31, 94-97% WR. Top quality.
    REENTRY_B12_WT_MOM_ENABLED: bool = True  # ABLATION: Sharpe 0.15/0.17, 112K/73K trades. Volume king.
    REENTRY_B14_HA_TREND_ENABLED: bool = True  # ABLATION: Sharpe 0.11/0.13. Moderate.
    REENTRY_B15_STRONG_TREND_ENABLED: bool = True  # ABLATION: Sharpe 0.89/0.72, 94-97% WR. Sniper.
    REENTRY_COOLDOWN_S: float = 0.0  # Was 15s; zero for instant reentry
    REENTRY_MIN_GAP_MINUTES: float = 15.0  # Absolute floor between exit and reentry (stocks). Fires before any tier gate.
    # Aggressive tier window (2026-04-17 reentry sweep: stocks peak at delay=1 bar = 5min on 5m).
    # Stocks reward URGENCY after stoch/DC exit clears. Crypto uses 30min in config.py.
    REENTRY_AGGRESSIVE_WINDOW_MIN: float = 5.0  # 5 min on stocks (5m base = 1 bar — matches Sharpe peak)
    # PATHWAY F — FAVORABLE MOVE force-reentry (2026-04-17, matches crypto config.py)
    REENTRY_60MIN_UNCONDITIONAL_ENABLED: bool = False  # Pathway G: DISABLED — sweep shows -22% Sharpe (0.38→0.30); catches tops not pullbacks
    REENTRY_60MIN_WINDOW_MIN: float = 60.0            # window after exit in minutes
    REENTRY_60MIN_MIN_PCT: float = 0.3                # price must move ≥0.3% in trade direction from exit to trigger
    REENTRY_FAVORABLE_MOVE_PCT: float = 1.0          # reenter if price moved ≥1% in our direction since exit
    REENTRY_FAVORABLE_HTF_MIN: int = 1                # require ≥1 of (1h,4h,D) WT aligned — was 2, but 1h is bearish after any WT exit
    REENTRY_FAVORABLE_QTY_MULT: float = 1.0           # base size when rally continues (100%)
    REENTRY_K15M_PARTIAL_ENABLED: bool = True         # enforce size-down in overheat zone
    REENTRY_K15M_PARTIAL_THRESHOLD: float = 90.0      # LONG k_15m ≥ 90 (SHORT ≤ 10) = overheat
    REENTRY_K15M_PARTIAL_MULT: float = 0.5            # reenter at 50% in overheat zone (rally may be ending)
    REENTRY_SYMGATE_ENABLED: bool = False  # 2026-04-19 FIX: engine default=False; t4 sweeps (Apr-16) all 0-trade pre-DC-band-fix — no clean tradier evidence.
    REENTRY_SYMGATE_SPEED_MIN: float = 1.0  # Min bull_speed (LONG) / bear_speed (SHORT). Below = momentum slowing -> block.
    ENTRY_SYMGATE_ENABLED: bool = False    # 2026-04-19 FIX: same — no clean sweep proof for tradier.
    # --- Rank-conviction / DC-moment / winner-protect (mirrors config.py Feature 1-3) ---
    # All default OFF — crypto proof exists (Sharpe 2.554) but tradier sweeps all 0-trade pre-DC-band-fix.
    # Re-sweep on tradier with clean engine before enabling any of these.
    RANK_CONVICTION_ENABLED: bool = False
    RP_STRONG_THRESHOLD: float = 70.0
    RP_STRONG_BONUS: float = 15.0
    RP_WEAK_THRESHOLD: float = 30.0
    RP_WEAK_PENALTY: float = -10.0
    RP_OPPOSITE_PENALTY: float = -20.0
    DC_MOMENT_ENABLED: bool = False
    DC_MOMENT_STRONG_THRESHOLD: float = 40.0
    DC_MOMENT_STRONG_BONUS: float = 10.0
    DC_MOMENT_OPPOSITE_PENALTY: float = -15.0
    WINNER_PROTECT_ENABLED: bool = False
    RP_PROTECT_THRESHOLD: float = 70.0
    RP_PROTECT_MIN_GAIN: float = 1.0
    NOLOSS_DC4H_GATE_ENABLED: bool = True   # HARD RULE: never close at a loss inside bb_1h (stocks) / dc_4h (crypto) — hedge instead.
    NOLOSS_BB1H_GATE_ENABLED: bool = True   # Stock structural break: price outside bb_1h in wrong direction → override NO_LOSS and close at loss
    LOSS_EXIT_TECHNICAL_BYPASS: tuple = ('LIQUIDATION', 'EMERGENCY_DC1H_BREACH', 'PARABOLIC_EXIT', 'GAIN_EROSION')  # GAIN_EROSION added 2026-04-20: DC_LOW4_5M structural stop closes at loss instead of hedging
    REENTRY_ESCALATION_CRIT_MIN: float = 60.0  # CRITICAL log if reentry pending > 60min
    REENTRY_ESCALATION_WARN_MIN: float = 30.0  # WARNING log if reentry pending > 30min
    REENTRY_MANDATORY: bool = True  # Enforce reentry after every exit
    REGIME_ADAPTIVE_ENABLED: bool = False  # Regime-adaptive strategy selection (ADX+CHOP) ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    REGIME_ATR_RATIO_MIN: float = 0.25  # atr_3m/atr_1h min. Below = compressed.
    REGIME_BB_WIDTH_PCT_MIN: float = 2.0  # bb_width_1h as % of price. Below = squeeze.
    REGIME_BTC_MARKET_WEIGHT: float = 0.5  # BTC influence on market-wide regime ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    REGIME_DC_ATR_RATIO_MIN: float = 1.5  # dc_width_15m / atr_3m. Below = no room.
    REGIME_DETECTION_ENABLED: bool = False  # Master switch — OFF until backtest-proven
    REGIME_ENTER_TRENDING_THRESHOLD: float = 30.0  # Score > 30 to enter TRENDING_UP (< -30 for DOWN)
    REGIME_EXIT_TRENDING_THRESHOLD: float = 15.0  # Score < 15 to exit back to RANGING (hysteresis)
    REGIME_GATE_ENABLED: bool = False  # TIER_A: Sharpe +0.3. Kills low-WR chop tail.
    REGIME_MIN_DWELL_BARS: int = 16  # 4h at 15m — minimum bars before regime switch
    REGIME_RANGING_DC_BREAKOUT_SCORE: int = 0  # DC breakout disabled in ranging
    REGIME_RANGING_EXIT_GAIN_MIN: float = 0.15  # Exit at 0.15% gain
    REGIME_RANGING_K_ZONE_BONUS: int = 40  # Mean reversion K-zone bonus (was 25)
    REGIME_RANGING_MIN_HOLD_BARS: int = 8  # 2h at 15m — fast turnover
    REGIME_RANGING_NOLOSS_MIN: float = 0.05  # Take ANY profit in ranging
    REGIME_RANGING_POSITION_SIZE_MULT: float = 0.5  # Half-size, more slots
    REGIME_RANGING_REENTRY_SIZE_MULT: float = 1.0  # Standard reentry
    REGIME_RANGING_SLOT_RESERVE_PCT: float = 0.6  # Reserve 60% slots for new entries
    REGIME_RANGING_STALE_HOURS: float = 48.0  # Evict breakeven positions after 48h ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    REGIME_RANGING_STALE_MIN_PROFIT: float = 0.02  # Must be slightly profitable to evict ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    REGIME_RANGING_WT_EXIT_VEL: float = -3.0  # Exit on lighter reversal
    REGIME_RANGING_WT_REDUCE_FRAC_LOW: float = 0.4  # 0.3-0.5% gain → reduce 40%
    REGIME_RANGING_WT_REDUCE_FRAC_MED: float = 0.6  # 0.5-1.0% gain → reduce 60%
    REGIME_TRENDING_DC_BREAKOUT_SCORE: int = 30  # DC breakout valuable in trends
    REGIME_TRENDING_EXIT_GAIN_MIN: float = 2.0  # Only exit at 2%+ gain
    REGIME_TRENDING_K_RESET_THRESHOLD: float = 40.0  # Shallower pullback K reset ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    REGIME_TRENDING_K_ZONE_BONUS: int = 15  # K-zone less important
    REGIME_TRENDING_MIN_HOLD_BARS: int = 48  # 12h at 15m — hold longer
    REGIME_TRENDING_NOLOSS_MIN: float = 0.5  # Let winners run in trends
    REGIME_TRENDING_POSITION_SIZE_MULT: float = 1.5  # Full-size, fewer trades
    REGIME_TRENDING_REENTRY_SIZE_MULT: float = 2.0  # Aggressive reentry in trends
    REGIME_TRENDING_SLOT_RESERVE_PCT: float = 0.4  # Reserve 40% slots
    REGIME_TRENDING_WT_EXIT_VEL: float = -12.0  # Only exit on strong reversal
    REGIME_TRENDING_WT_REDUCE_FRAC_LOW: float = 0.1  # Trim gently
    REGIME_TRENDING_WT_REDUCE_FRAC_MED: float = 0.15  # Still gentle
    RSI2_MEAN_REVERSION_ENABLED: bool = False  # BACKTEST_CHANGE_126: RSI(2) ultra-oversold (91% WR daily, tiny gains)
    RSI2_SCORE_BONUS: int = 20  # BACKTEST_CHANGE_126: Score bonus for RSI(2) extreme
    RSI2_THRESHOLD_LONG: float = 15.0  # BACKTEST_CHANGE_126: RSI(2) below this = LONG signal
    RSI2_THRESHOLD_SHORT: float = 85.0  # BACKTEST_CHANGE_126: RSI(2) above this = SHORT signal
    RSI_ENTRY_GATE_ENABLED: bool = False  # BC_154: DISABLED — 67-config ablation (48sym/4yr): stoch_gate_50 does the filtering. no_filter+stoch50 = Sharpe 0.790 (#1) vs RSI37 = 0.638
    RSI_ENTRY_MAX_LONG: float = 37.0  # BC_154: kept for reference but gate is disabled
    RSI_ENTRY_MIN_SHORT: float = 63.0  # BC_154: kept for reference but gate is disabled
    RSI_MACD_EMA_ENABLED: bool = False  # BACKTEST_CHANGE_129: RSI+MACD+EMA9 cross combined entry
    RSI_MACD_EMA_RSI_LONG: float = 35.0  # BACKTEST_CHANGE_129: Relaxed RSI — 35 not 30
    RSI_MACD_EMA_RSI_SHORT: float = 65.0  # BACKTEST_CHANGE_129: Relaxed RSI — 65 not 70
    RSI_MACD_EMA_SCORE: int = 25  # BACKTEST_CHANGE_129: Score bonus
    RSI_MACD_EMA_TF: str = '1h'
    RSI_MOMENTUM_MODE: bool = False  # BACKTEST_CHANGE_138: Toggle RSI gate to momentum (>50=buy). Crypto-specific. ; DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416
    RZ_DIV_BLOCK_MIN: int = 2
    RZ_DIV_EXIT_ENABLED: bool = True
    RZ_K_ENTRY_BOTTOM: float = 10.0  # Stoch K below this at BOTTOM = exit short (mirror) ; DEAD_CONFIRMED (priority 88/100) — no plausible wiring site found 20260416
    RZ_MFI_ENTRY_BOTTOM: float = 15.0  # MFI below this at BOTTOM = exit short (mirror) ; DEAD_CONFIRMED (priority 88/100) — no plausible wiring site found 20260416
    RZ_TWO_PHASE_EXIT_ENABLED: bool = True
    RZ_ZSCORE_EXIT_ENABLED: bool = True
    RZ_ZSCORE_ZONE_ENABLED: bool = True
    SANDBOX_MODE: bool = False
    SATOSHIT_ENABLED: bool = True
    SATOSHIT_EXIT_ENABLED: bool = True  # Fixed 2026-04-07 — now exits at 1m/3m TOP (stoch cross down from OB), never at higher low
    SATOSHIT_EXIT_PARTIAL_PCT: float = 0.7  # Close 70% of position, keep 30% as runner
    SATOSHIT_EXIT_USE_MAKER: bool = True  # Use maker order for partial close (bypasses Finandy full close)
    SATOSHIT_LONG_BB_PCTB_MAX: float = 0.5  # 1h BB%B proxy (his 15m median: 0.08)
    SATOSHIT_LONG_HA_STREAK_MAX: int = 1  # HA must be bearish/neutral (his median: -3)
    SATOSHIT_PROTECT_TRADES: bool = True  # ON — only Satoshit exit can close Satoshit-opened positions
    SATOSHIT_QTY_MULT: float = 3.0  # 3x position size for satoshit entries — 100% WR, avg +5.58% gain
    SATOSHIT_SCORE_BONUS: int = 30  # Score bonus when Satoshit fires (below mover=40)
    SATOSHIT_SHORT_BB_PCTB_MIN: float = 0.55  # 1h BB%B proxy (his 15m median: 1.16)
    SATOSHIT_SHORT_HA_STREAK_MIN: int = 0  # HA must be bullish (his median: 4)
    SBA_ADX_TF: str = '1h'  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    SBA_COOLDOWN_GLOBAL_S: int = 300  # BACKTEST_CHANGE_145: 5min between ANY SBA add (crash guard)
    SBA_COOLDOWN_POSITION_S: int = 3375  # BACKTEST_CHANGE_145: ~56min between adds (backtest: 15 bars × 15m = 3375s optimal)
    SBA_MAX_CONCURRENT: int = 3  # BACKTEST_CHANGE_145: Max positions receiving SBA at once
    SBA_MAX_TOTAL_MULT: float = 2.5  # BACKTEST_CHANGE_145: Position can't exceed 2.5x START_POSITION_SIZE
    SBA_MIN_SCORE: float = 3.5  # BACKTEST_CHANGE_145: Min bounce score to trigger (backtest: 3.5 > 4.0/4.5, 66% SBA WR)
    SCALP_ACCOUNTS = ['inf']
    SCALP_MODE: bool = True  # P0: ON for inf. V8 showed -0.30 Sharpe BUT that was with ISOLATE=False (main exits interfered). Now ISOLATE=True + inf excluded from hedging.
    SCALP_OVERRIDE = False  # DEAD_CONFIRMED (priority 40/100) — no plausible wiring site found 20260416
    SCALP_V2_DC_HTF_REQUIRE_ALL: bool = True  # P0: quality gate. True = fewer but better. Keep True.
    SCALP_V2_ISOLATE: bool = True  # 2026-04-16: ON for live — V2 positions ONLY use V2 exits, main pipeline exits skip them. V8 -0.30 Sharpe was from main exits trampling V2 positions.
    SCALP_V2_LH_LL_EXIT: bool = True  # P2: #4 in sweep (Sharpe 68). +23 extra exits in smoke test. 15m structure break.
    SCALP_V2_LH_LL_TF: str = '15m'
    SCALP_V2_MAX_CONCURRENT: int = 5  # P3: fine for now, only tune if hitting position limits
    SCALP_V2_MAX_HOLD_MINUTES: float = 15.0  # P0: sweep-proven, 60m universally worse for inf
    SCALP_V2_REDZONE_EXIT: bool = True  # P1: #2 in sweep (Sharpe 97). Catches exits V1_WT misses. +6 extra exits in smoke test.
    SCALP_V2_REDZONE_K_THRESHOLD: int = 90  # P1: sweep winner=90. Try 80 only after 90 tested.
    SCALP_V2_REENTRY_COOLDOWN_S: int = 300  # P3: 5 min reasonable, shorter = more chop ; DEAD_CONFIRMED (priority 65/100) — no plausible wiring site found 20260416
    SCALP_V2_VARIANT: str = 'V1_WT_CONFIRM'
    SERVICE_REDUCE = True
    SERVICE_STOP = True
    SHORT_ABOVE_SMA20_BONUS: int = 15  # BACKTEST_CHANGE_104: NEW. Bonus for SHORT when price above EMA20 — mean-reversion shorts win (3.15% above vs losers 0.10%)
    SHORT_RSI_MIN_1H: float = 40.0  # BACKTEST_CHANGE_101: Block SHORT when rsi_1h < 40
    SIGNALS_LOOP_INTERVAL_SECONDS: int = 300  # Signals loop interval
    SIMPLE_TP_EXIT_ENABLED: bool = False  # BACKTEST_CHANGE_139: Simple fixed TP% exit. 567k backtests: simple > complex trailing.
    SIMPLE_TP_PCT: float = 0.5  # BACKTEST_CHANGE_139: Fixed TP percentage
    SLEEP_TIME_PROC_ACCT: float = 5  # Check symbols - 100 times per minute minimum
    SMA200_DIST_LONG_THRESHOLD: float = -3.0  # BACKTEST_CHANGE_7: was -2.0. Wider captures more mean-reversion setups ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    STALE_WARNING_INTERVAL_SECONDS: float = 30.0
    STOCH_CROSS_3M_EXIT_ENABLED: bool = False  # 2026-04-10: REMOVED — stoch is way lagging vs DELTA/WT. DELTA→WT priority means this never reaches if upstream works.
    STOP_LOSS_THRESHOLD = 999.0  # BACKTEST_CHANGE_17: was 1.0. Dead code under STRICT_NO_LOSS — disabled
    STOP_MAJOR_LOSS_BLOCK_ENABLED: bool = True  # BACKTEST_CHANGE_113: Block the STOP_MAJOR_LOSS reduce path entirely. #1 PnL destroyer (-125k% cumulative). L/S ratio IS the hedge.
    STOP_MAJOR_LOSS_ENABLED: bool = False  # ABLATION_BACKTEST: was implicitly True. #1 PnL destroyer (-125k%). L/S ratio hedge handles risk ; DEAD_CONFIRMED (priority 5/100) — no plausible wiring site found 20260416
    STRICT_NO_LOSS_ACCOUNTS = ['ang', 'inf', 'flz', 'men', 'fin']
    SWEEP_OPTIMAL_ENTRY_TF: str = '1h'  # DEAD_CONFIRMED (priority 30/100) — no plausible wiring site found 20260416
    SWEEP_OPTIMAL_HOLD_BARS: int = 8  # Most common winning hold period ; DEAD_CONFIRMED (priority 30/100) — no plausible wiring site found 20260416
    SYMBOL_PERF_DECAY_HOURS: float = 12.0  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    SYMBOL_PERF_MAX_MULT: float = 10.0  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    SYMBOL_PERF_MIN_MULT: float = 0.1  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    SYMBOL_PERF_MIN_TRADES: int = 5
    SYMBOL_PERF_WINDOW_DAYS: int = 14
    TASK_STAGGER_SECONDS: int = 15  # Stagger between starting background tasks
    TF_ALIGNMENT_MIN_LONG: int = 2  # Exits need 2 TFs turning against ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:7128 entry eval
    TF_ALIGNMENT_MIN_SHORT: int = 2  # Exits need 2 TFs turning against ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:7129 entry eval
    TF_ALIGNMENT_MIN_TOTAL: int = 4  # 2026-03-30: Entries need 3/3 LTF + D mandatory + 2/3 HTF = 4+ TFs. Hardcoded in check_entry_alignment.
    TF_ALL: list = None  # Auto-populated: [TF_MICRO, TF_SCALP, TF_HTF1, TF_HTF2, TF_HTF3, TF_MACRO] ; DEAD_CONFIRMED (priority 40/100) — auto-populated placeholder, no wiring needed
    TF_FOCUS: str = '3m'
    TF_FOCUS_ENTRY_HARD_GATE: bool = True  # Focus TF must agree for entry ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:7134 entry eval
    TF_FOCUS_EXIT_HARD_GATE: bool = True  # Focus TF crossunder = immediate exit ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:7135 entry eval
    TF_FOCUS_WEIGHT: float = 8.0  # BACKTEST_CHANGE_2: was 5.0. 3m is 1.9x better than 15m ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:7130 entry eval
    TIER_A_MIN_GAIN: float = 0.3  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    TIER_A_MIN_TRADES: int = 10  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    TIER_A_MULTIPLIER: float = 1.2  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    TIER_A_WIN_RATE: float = 0.6  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    TIER_B_MIN_TRADES: int = 5  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    TIER_B_WIN_RATE: float = 0.45  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    TIER_C_MULTIPLIER: float = 0.7  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    TIER_ENABLED: bool = True
    TREND_EXIT_SCORE_FLIP: int = 0
    TREND_HEDGE_MAX_SEC: int = 180
    TREND_HTF_MIN_BEAR: int = 7
    TREND_HTF_MIN_BULL: int = 7
    TREND_MIN_GAIN_EXIT: float = 0.1
    TRIPLE_CONF_ENABLED: bool = False  # BACKTEST_CHANGE_125: MACD+RSI+Stoch triple confirmation entry
    TRIPLE_CONF_RSI_LONG: float = 30.0  # BACKTEST_CHANGE_125: RSI(14) below this for LONG
    TRIPLE_CONF_RSI_SHORT: float = 70.0  # BACKTEST_CHANGE_125: RSI(14) above this for SHORT
    TRIPLE_CONF_SCORE: int = 30  # BACKTEST_CHANGE_125: Score bonus when all 3 align
    TRIPLE_CONF_STOCH_LONG: float = 20.0  # BACKTEST_CHANGE_125: Stoch K below this for LONG
    TRIPLE_CONF_STOCH_SHORT: float = 80.0  # BACKTEST_CHANGE_125: Stoch K above this for SHORT
    TRIPLE_CONF_TF: str = '1h'
    TR_ADX4H_BOYCOTT_SCORE: int = -40  # BC_155a: Severe. Stacked with BB_width: 81% OOS WR
    TR_ADX4H_GATE_ENABLED: bool = True  # BC_155a: Boycott when ADX_4h trending (bad for mean-reversion system)
    TR_ADX4H_MAX: float = 20.0  # BC_155a: Conservative (16 optimal). ADX_4h above this = heavy penalty
    TR_BBWIDTH4H_BOYCOTT_SCORE: int = -35  # BC_155b: Severe penalty when too volatile
    TR_BBWIDTH4H_GATE_ENABLED: bool = True  # BC_155b: Boycott wide BBands (high vol = bad entries)
    TR_BBWIDTH4H_MAX: float = 10.0  # BC_155b: Conservative (7.94 optimal)
    TR_CHOP4H_BONUS: int = 15  # BC_155c: Mean-reversion sweet spot
    TR_CHOP4H_GATE_ENABLED: bool = True  # BC_155c: Bonus choppy, penalty trending. Our system IS mean-reversion.
    TR_CHOP4H_MIN: float = 50.0  # BC_155c: Choppy above this = bonus
    TR_CHOP4H_PENALTY: int = -20  # BC_155c: Penalty in trending regime
    TR_CHOP4H_TREND_MAX: float = 38.0  # BC_155c: Strong trend below this = penalty
    TR_DCWIDTH4H_SHORT_BOYCOTT_SCORE: int = -25  # BC_155e: Moderate penalty
    TR_DCWIDTH4H_SHORT_ENABLED: bool = True  # BC_155e: SHORT boycott in wide DC channel
    TR_DCWIDTH4H_SHORT_MAX: float = 15.0  # BC_155e: Conservative (winners median ~11)
    TR_MFI4H_LONG_BOYCOTT_SCORE: int = -25  # BC_155d: Moderate penalty
    TR_MFI4H_LONG_ENABLED: bool = True  # BC_155d: LONG boycott when MFI_4h too low (no buying pressure)
    TR_MFI4H_LONG_MIN: float = 40.0  # BC_155d: Conservative (41 was loser mean)
    UNIVERSAL_NOLOSS_GATE: bool = True
    USE_INDICATOR_SNAPSHOT: bool = True  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416
    V8Q_COOLDOWN_BARS: int = 3  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    V8Q_D_TREND_REQUIRED: bool = True  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    V8Q_HTF_MIN_ALIGNED: int = 1  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    V8Q_K3M_FLOOR: int = 30  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    V8Q_MIN_HOLD_BARS: int = 10  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    V8Q_PROFIT_TARGET_ENABLED: bool = True  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    V8Q_PROFIT_TARGET_PCT: float = 1.6  # v3 PEAK: 1.6 = Sharpe 1.93 on TOP3 (was 1.5 = 1.90) ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    V8Q_STRENGTH_FILTER_ENABLED: bool = True  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    V8Q_STRENGTH_MIN_SCORE: float = 5.0  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    V8Q_SYMBOL_TIER_TOP3: tuple = ('LINKUSDT', 'ETHUSDT', 'DOTUSDT')  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    V8Q_SYMBOL_TIER_TOP4: tuple = ('LINKUSDT', 'ETHUSDT', 'DOTUSDT', 'BTCUSDT')  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    V8Q_SYMBOL_TIER_TOP5: tuple = ('LINKUSDT', 'ETHUSDT', 'DOTUSDT', 'BTCUSDT', 'UNIUSDT')  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    V8Q_SYMBOL_TIER_TOP6: tuple = ('LINKUSDT', 'ETHUSDT', 'DOTUSDT', 'BTCUSDT', 'UNIUSDT', 'SOLUSDT')  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    V8Q_WT_EXIT_MIN_TFS: int = 2  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    VALIDATE_REFRESH: int = 2  # seconds
    VOLUME_CONFIRMATION_ENABLED: bool = False  # TIER_C: Sharpe +0.1. Kills dead-zone entries.
    VOLUME_CONFIRMATION_MULT: float = 1.2  # volume_3m > N × avg_20_3m required.
    VOL_SPIKE_BODY_RATIO: float = 0.7  # Candle body must be > 70% of total range
    VOL_SPIKE_COOLDOWN: float = 300.0  # Seconds between vol spike entries per symbol
    VOL_SPIKE_ENABLED: bool = True  # Volume spike reversal: 93.5% WR, Sharpe 13.9
    VOL_SPIKE_LS_MAX_IMBALANCE: float = 1.5  # Max L/S ratio imbalance before blocking
    VOL_SPIKE_MIN_ALIGNMENT: int = 3  # Lower alignment threshold for spike entries
    VOL_SPIKE_RELVOL_THRESHOLD: float = 3.0  # Relative volume must be > 3x 20-bar avg
    WIN_TRAIL_EROSION_PCT: float = 0.5  # BACKTEST_CHANGE_105: Tournament v2: 50% trail slightly better than 30% (let winners run more). Was 0.30. ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416
    WT_15M_SAME_HEDGE_COOLDOWN_SEC: int = 1800  # 2026-04-16: Redis-backed cooldown (survives restarts — old 300s in-memory wiped on process restart).
    WT_15M_SAME_HEDGE_DAILY_CAP: int = 2  # 2026-04-16: max SAME_HEDGE opens per symbol per day. 45× BAT/DOT/ATOM firestorm = daily cap missing.
    WT_15M_SAME_HEDGE_ENABLED: bool = True  # RE-ENABLED 2026-04-16: root cause was hedge exemption in DUPLICATE_OPEN_GUARD (line 11000) + size gate (line 11165). Both exemptions REMOVED. Hedges now subject to 900s cooldown like all other opens.
    WT_EXIT_VEL_THRESHOLD: float = -6.0  # V4: was -2.0 hardcoded. Calmer exits = let winners run longer.
    WT_REDUCE_FRAC_HIGH: float = 0.5  # V4: at gains 1-3%, reduce 50% (was 70%).
    WT_REDUCE_FRAC_LOW: float = 0.15  # V4: was 0.30. At gains 0.3-0.5%, only reduce 15% (was 30%).
    WT_REDUCE_FRAC_MED: float = 0.25  # V4: was 0.50. At gains 0.5-1.0%, only reduce 25% (was 50%).
    ZERO_CONFIRMATION_THRESHOLD_API: int = 2  # FIX 2026-03-29: was 1, killed real hedges on fin. Need 2 misses to confirm phantom.
    ZERO_CONFIRMATION_THRESHOLD_WS: int = 1  # Single WS positionAmt=0 is authoritative — was 2, caused 81 phantom positions
    _CURRENT_MARKET_MODE: ClassVar[str] = 'NORMAL_MODE'  # WIRED 2026-04-16 (priority 5/100) — tradier_rankings.py:2338 regime tracking
    _INSTANCES: ClassVar[WeakSet] = WeakSet()  # DEAD_CONFIRMED (priority 5/100) — no plausible wiring site found 20260416
    _REGIME_LOG: ClassVar[list] = []  # DEAD_CONFIRMED (priority 5/100) — no plausible wiring site found 20260416
    _REGIME_REDIS_TS: ClassVar[float] = 0.0  # DEAD_CONFIRMED (priority 5/100) — no plausible wiring site found 20260416

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
    _REGIME_OVERRIDES: ClassVar[Dict[str, Dict]] = {}  # DEAD_CONFIRMED (priority 5/100) — no plausible wiring site found 20260416
    _REGIME_REDIS_CACHE: ClassVar[Dict[str, Dict]] = {}  # DEAD_CONFIRMED (priority 5/100) — no plausible wiring site found 20260416

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