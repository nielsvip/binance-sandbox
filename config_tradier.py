# 2026-08-19 STOCKS vs CRYPTO split — config_tradier.py = STOCKS (TRB 167). config.py = CRYPTO (inf 100).
# Intentionally divergent (150 vs 858 keys). v8_vec_sweep.SweepConfig.for_mode(tradier) maps this file's live values
# (DELTA False, WT_DC 4h_D, TRA_DISABLE_DELTA True). Do not copy crypto defaults blindly. See §16.71-16.72.
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
    # HARD SAFETY LOCK: the options stack may research, score, and report, but
    # it must not submit/cancel live orders until the paper system is explicitly
    # fine-tuned and this flag is deliberately changed by the owner.
    OPTIONS_LIVE_TRADING_ENABLED: bool = False
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
    BB_PCTB_ENTRY_ENABLED: bool = False  # parity 2026-08-17: vector->live (was vector-only)
    COOLDOWN_BARS: int = 3  # parity 2026-08-17: vector->live (was vector-only)
    ENTRY_SCORE_THRESHOLD: float = 18.0  # parity 2026-08-17: vector->live (was vector-only)
    MIN_HOLD_BARS: int = 10  # parity 2026-08-17: vector->live (was vector-only)
    MODE: str = "tradier"
    STOCH_ENTRY_ENABLED: bool = False  # parity 2026-08-17: SWITCH tested per_sym + 7D crypto+stocks (bypass removed)
    WT_ENTRY_ENABLED: bool = False  # parity 2026-08-17: SWITCH tested per_sym + 7D crypto+stocks (bypass removed)
    REENTRY_PULL1_ENABLED: bool = False  # parity 2026-08-17: vector->live (was vector-only)
    REENTRY_PULL2_ENABLED: bool = False  # parity 2026-08-17: vector->live (was vector-only)
    REENTRY_PULL3_ENABLED: bool = False  # parity 2026-08-17: vector->live (was vector-only)
    REENTRY_PULL4_ENABLED: bool = False  # parity 2026-08-17: vector->live (was vector-only)
    SATOSHIT_ENTRY_ENABLED: bool = False  # parity 2026-08-17: vector->live (was vector-only)
    WT_EXIT_MIN_TFS: int = 2  # parity 2026-08-17: vector->live (was vector-only)
    WT_VEL_DECAY_EXIT_ENABLED: bool = True  # parity 2026-08-17: vector->live (was vector-only)  # RECONNECT 2026-09-01 per user mandate: each WT exit True by default
    WT_VEL_DECAY_THRESHOLD: float = 1.0  # parity 2026-08-17: vector->live (was vector-only)
    MIN_POSITION_SIZE: float = 100
    # Per-entry timeframe sizing (stock analogue of config.BREAKOUT_TF_SIZE_*).
    # Tradier's native lower timeframe is 5m, which corresponds to crypto 3m.
    # Keep the new behavior opt-in until a Tier-2 backtest promotes it.
    BREAKOUT_TF_SIZE_ENABLED: bool = False
    BREAKOUT_TF_SIZE_MULT_5M: float = 0.5
    BREAKOUT_TF_SIZE_MULT_15M: float = 1.0
    BREAKOUT_TF_SIZE_MULT_1H: float = 2.0
    BREAKOUT_TF_SIZE_MULT_4H: float = 3.0
    BREAKOUT_TF_SIZE_MULT_D: float = 4.0
    BREAKOUT_TF_SIZE_CAP_MULT: float = 5.0
    # 2026-04-27 EMERGENCY SIZE CUT — user at -25% / 10d. Halve all caps until bleed stops.
    # Original values preserved in inline comment in case we need to revert.
    # 2026-04-27 SECOND CUT — user at -30%/week, headless-chicken MSTR loop. Now 1/4 of original.
    MAX_POSITION_SIZE: float = 2250.0   # was 2500 / orig 5000
    START_POSITION_SIZE: float = 500.0  # was 150 (2026-04-27 emergency cut); 2026-06-14 raised: $150 can't buy 1 share of $300+ stocks

    # Hard account-risk ceiling.  Entries are refused at/above this measured
    # peak-to-current equity drawdown; exits remain permitted.
    MAX_ALLOWED_DRAWDOWN_PCT: float = 50.0
    # Frozen WDAY_SHORT vector finalist. Newly named because LONG_WAIT is a
    # removed compound label (BACKTEST_BIBLE.md section 15.15). Default OFF;
    # V8 overrides must opt in explicitly using the same fields as live Tradier.
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED: bool = False
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SYMBOLS: tuple[str, ...] = ("WDAY",)
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SIDE: str = "SHORT"
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_BOUNCE_TIMEFRAME: str = "5m"
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_BOUNCE_DISTANCE: float = 0.015
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_DEEP_K4H: float = 50.0
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_TURN_K1H: float = 40.0
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_CONFIRMATION_MIN: int = 2
    # === WING BUDGETS ===
    SWING_LONG_BUDGET: float = 40000.0     # 2026-08-20 USER: raised 2.5k->40k for >1k/share (NVDA/ASML etc) was 50000 / orig 100000
    SWING_SHORT_BUDGET: float = 40000.0    # 2026-08-20 USER: raised 2.5k->40k for >1k/share was 50000 / orig 100000
    SWING_MAX_POSITION_SIZE: float = 1100.0   # was 1000 (DEAD)
    SWING_START_SIZE: float = 200.0          # was 400 (DEAD)
    SCALP_LONG_BUDGET: float = 250.0         # was 500 / orig 1000
    SCALP_SHORT_BUDGET: float = 250.0        # was 500 / orig 1000
    SCALP_MAX_POSITION_SIZE: float = 500.0   # was 1000 / orig 2000
    SCALP_START_SIZE: float = 150.0          # was 300 / orig 600
    SCALP_MAX_HOLD_MINUTES: float = 180.0     # URGENT_FIX: shorter holds, take profits/losses faster (was 300)
    SCALP_STOP_PCT: float = 9.99             # BACKTEST_CHANGE_T12 was 1.5% → 999% effectively disabled NO_LOSS mode
    SCALP_TARGET_PCT: float = 0.005           # URGENT_FIX: tighter TP in choppy market, take profits faster (was 0.01 = 1.0% → 0.005 = 0.5%)
    SCALP_MAX_POSITIONS_PER_SIDE: int = 6    # BACKTEST_CHANGE_T34 was 8 → 6 concentrate capital
    SCALP_TOP_MOVERS_N: int = 14             # Candidate pool size
    SCALP_MIN_REL_VOL: float = 1.1           # Min relative volume to qualify
    SCALP_MIN_MOVE_PCT: float = 0.003      # Min 0.3% 5m deviation from ema_20_5m — 2026-07-08 GAINMO triage: 0.3→0.003 (consumer treats as FRACTION; 0.3 = 30% = scalps never qualify, unit bug)
    MAX_ORDER_VALUE: float = 2500.0  # was 1000 / orig 2000 — 2026-04-27 second cut
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
    TRA_ALLOW_BUYS: bool = False                   # USER 2026-08-14: tra NEVER buys, only sells before loss in selloff                       # tra is cash account → no shorts EVER
    TRA_NO_LOSS_EXIT: bool = False                    # tra never closes a position at a loss
    TRA_STRICT_EXIT_ONLY: bool = True                # only the 5-of-5 STRICT_EXIT gate counts
    TRA_DISABLE_DELTA_ENTRY: bool = True             # delta engine is too fast for long-term hold
    TRA_DISABLE_AUGMENT: bool = True                 # no churn from augments either
    TRA_MAX_BUYS_PER_DAY: int = 1                   # GFV guard: at most 1 buy per calendar day for tra (cash acct, 5 flags)
    TRA_BUY_COOLDOWN_AFTER_SELL_HOURS: float = 96.0     # 4 DAYS no buying after a sell on cash (prevent GFV 6th flag = ban)
    TRA_WT_DC_ENTRY_THRESHOLD: float = 85.0          # REVERTED 2026-08-11 per SWITCH_LAB_VECTOR_LIVE_AUDIT.md M3 — live bypass removed, vector+live parity restored; re-promote only via 1yr Tier-2
    TRA_MIN_HOLD_MINUTES: float = 5760.0             # 4 DAYS hold floor - cash GFV 5 flags, prevent 6th ban
    # 2026-04-27 — live entry-engine boost (defaults OFF for safety; user flips when ready).
    # Engines are pure-function additive triggers in entry_engine_{wt,stoch,dc,htf}.py — they
    # boost the existing entry score when they fire above LIVE_ENTRY_ENGINE_MIN_SCORE; they
    # NEVER block existing entries. Worst case is a few extra entries fire.
    LIVE_ENTRY_ENGINE_ENABLED: bool = True          # 2026-04-29 PATH A REVERTED: 12sym×6mo sample below 100sym×1yr published-Sharpe floor (rule 4b) and 0.874<1.0 trash floor (rule 6). Both numbers were undersize noise. Re-enable only after 114-stock × ≥1yr Tier-2 clears pool_sharpe ≥1.0.
    WT_DC_HTF_GATE: str = "4h_D"                     # 2026-07-14 RESTORED 1h->4h_D: the 2026-05-21 loosening to "1h" (for trade-frequency reasons) silently re-opened the EXACT "SHORT-into-uptrend" gap this gate was built to close on 2026-04-27 (see WT_DC_ENTRY comment ~3131) -- a 1h-only check can't see a multi-week Daily uptrend. Live proof: PLTR_SHORT (trb, opened 2026-07-09, reason WT_DC_ENTRY_60_4h_bear|1h_cross_BEAR) and IBIT_SHORT (trb, opened 2026-07-13, same pattern) both entered on 1h/4h bearish WT crosses DURING pullbacks inside established multi-week uptrends (PLTR +25% off its 06-25 low, IBIT +9% off its 06-30 low, both still rising at entry) -- textbook countertrend entries, not "top of a bounce in a downtrend". Both ran hard against (PLTR -6.8%, IBIT required manual close). Trade-off: "4h" alone caused 28/day blocks on trb per the 05-21 note; "4h_D" is the strongest documented setting and is the one the original 04-27 fix intended. ROLLBACK: "1h" (accepts the uptrend-short risk for more trade frequency) or "4h" (partial). Values: 'none' / '1h' / '4h' / '4h_D'
    LIVE_ENTRY_ENGINE_WT_ENABLED: bool = True       # convergent: wt_all3 dominates tradier winners (Sharpe 7.71 @ 79 trades)
    LIVE_ENTRY_ENGINE_STOCH_ENABLED: bool = True    # convergent: k4h<20 paired with wt_all3
    LIVE_ENTRY_ENGINE_DC_ENABLED: bool = True       # convergent on crypto side; harmless on tradier when no dc_x signal
    LIVE_ENTRY_ENGINE_HTF_ENABLED: bool = True      # convergent: sma200 alignment
    LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED: bool = True   # 2026-05-19 PATH D: STDEV_D200_HIGH+WT_D_BEAR composite (68.5% WR, +41 bps fwd60 per signal-fire audit). Default OFF until Tier-2 multi-symbol backtest passes sample-floor + pool_sharpe>1.0.
    LIVE_ENTRY_ENGINE_MIN_SCORE: float = 0.5        # 2026-04-27: lowered 0.6→0.5 per user (way too few trades across the board). Same change as crypto.
    LIVE_ENTRY_ENGINE_BOOST_SCORE: float = 8.0      # additive bump to entry score when an engine fires above threshold
    # 2026-04-27 — REENTRY engine hook: engines NEVER block reentries, only ADD size + tag reason.
    # 1.0 = pass-through (engines run, log +ENGINES tag for observability, NO size change). 1.5 = up to +50% size at max conviction.
    LIVE_ENTRY_ENGINE_REENTRY_SIZE_MULT: float = 1.0
    # tra preferred symbols (user-specified). The actual list is in
    # symbols_tra_satoshit_long.json — these are the "core 9" the user named.
    TRA_PREFERRED_SYMBOLS: List[str] = field(default_factory=lambda: ["AAPL", "MSFT", "GOOGL", "MSTR", "PLTR", "NEM", "MU", "SNDK", "NVDA"])  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    BLACKLIST = [] #'BTC', 'ETHE', 'GOOGL', 'XIACF',"AAPL","MSTR",'PLTR',"ABT","JNJ"] #Tradingview
    # 2026-08-01 deadline vector lane: these staged candidates are reaffirmed
    # as Tradier always-tradeable symbols.  The central side-specific discovery
    # allowlist remains authoritative for new entries; this list must not be
    # interpreted as exact-V8 validation or as permission to cross LONG/SHORT
    # direction boundaries.
    ALWAYS_TRADEABLE = ["NVDA",  "GOOG", "META", "MSFT", "GLD", "XOP", "GDX","USO", "CVX", "XOM", "SLV", "SNDK", "MU", "VT", "XLE", "AXTI", "MNTS", "LSCC", "MP", "AG", "HL", "AU", "PAAS", "COPX", "MU", "SPCX","NEM", "FCX"] #LEGACY 
    # 2026-07-10 USER MANDATE: these must be in the trb universes every rankings cycle
    # ("need to be trading no matter what"); injected by tradier_rankings before save.
    TRADIER_MANDATORY_LONG_TRB = ["MU", "SNDK", "NVDA", "GOOGL", "META", "MSFT", "AAPL", "ASML", "TSLA", "AMZN", "MRVL", "RBLX", "VLO", "INTC", "MSTR", "IBIT", "HOOD", "VT", "GLD", "SLV", "COPX", "QQQ", "SPY","BMNR"]  # VT added USER 2026-07-11; OLED(wsh0.80) USAR(wsh0.59) added USER 2026-07-21; GLD/COPX added USER 2026-08-15 TradingView long (INTC already there, RBLX stays short, PLTR both); QQQ/SPY added USER 2026-08-17 index steady
    TRADIER_MANDATORY_SHORT_TRB = ["MSTR", "HOOD", "MU", "NVDA", "WDAY", "HAO", "PLTR", "TSLA", "AMZN", "AAPL", "RBLX","BMNR"]  # PLTR both sides per USER 2026-08-15 (was long only, now also short)
    NON_SHORTABLE = {"FIX", "AXTI", "FCN", "ASML", "HAO", "ETHE", "TCEHY", "ALMU", "XIACF", "BITO", "GBTC", "MARA", "CLSK", "HIVE", "CAN", "BTBT", "CUBT", "ETH", "BTC", "QUBT", "GLD", "ETHD", "AGCO", "SBIT", "INOD", "BTCL", "DIME", "UCO", "PDBC", "COPX", "BLOK", "USO", "UNG", "BOIL", "WEAT", "CORN", "DBA", "GDXJ", "XME", "XOP", "OIH", "URA", "URNM", "ITA", "PPA", "MOO", "REMX", "IPI", "LSB", "UAN", "ASC", "EGLE", "GNK", "NAT", "TNK", "NNE", "DNN", "PLL", "SGML", "MAG", "BTG", "ICL", "SQM", "GOGL", "SBLK", "DAC", "FRO", "ZIM", "GOLD", "UNG"}
    EXCEPTIONS = ['GOOGL', 'MSFT', 'NVDA', 'CVX', 'XOM', 'IBIT', 'GLD', 'ETH', 'XLE', 'GDX', 'USO', 'SLV'] #4* max order size and max pos size
    # === 2026-04-27 STOCKS OPTIONS-OI INJECTION (READ-ONLY) ===
    # Source: tradier_options_oi_fetcher.py → data/stocks_oi_cache/{sym}.json (P/C ratio + max-OI strikes).
    # Mirror of crypto FUNDING_OI_INJECT in ez_rankings.py:~4567. Inject extreme-P/C symbols into trb/trc winners/losers
    # so trader directional bias from options market consensus shows up in entry candidate lists.
    # NEVER places options orders — pure sentiment signal per feedback_oi_signal_only_no_options_trading_20260427.md.
    # === 2026-08-09 AI PREMARKET — TRC PAPER A/B (stocks only, crypto follows later) ===
    # Daily 12:00 UTC analyzer writes data/ai_premarket/YYYY-MM-DD/decisions.json + trc_advisories.
    # Rankings injects AI picks ONLY into TRC (paper) so TRB stays as pure control.
    # Manage consumes via ~/binance-agent-handoff/trc_advisories.json (existing consumer).
    # TradingView MCP provides extended historical/technical coverage per user mandate.
    AI_PREMARKET_ENABLED_TRB: bool = False       # control — never inject into TRB
    AI_PREMARKET_ENABLED_TRC: bool = True        # paper — TRC = TRB + AI picks
    AI_PREMARKET_MIN_CONVICTION: float = 0.55    # minimum LLM conviction to inject symbol
    AI_PREMARKET_MAX_NEW_PER_SIDE: int = 8       # cap new AI symbols per side per day
    AI_PREMARKET_SIZE_MULT_MAX: float = 1.5      # advisory size_override cap
    AI_PREMARKET_TRADINGVIEW_ENABLED: bool = True  # use TradingView MCP when available, fallback to local indicators
    AI_PREMARKET_EXPIRES_ET: str = "20:00"       # advisory expires at market close same day
    AI_PREMARKET_DECISIONS_DIR: str = "data/ai_premarket"  # relative to BASE_PATH
    TRADIER_OI_INJECT_ENABLED: bool = True         # 2026-04-28 restored — probe one-by-one
    TRADIER_OI_INJECT_PC_BULLISH: float = 0.6      # P/C below this → call OI dominates → LONG bias inject
    TRADIER_OI_INJECT_PC_BEARISH: float = 1.4      # P/C above this → put OI dominates → SHORT bias inject
    TRADIER_OI_INJECT_NEAR_MONEY_PREFER: bool = True   # use near_money_pc_ratio (±5% strikes) when present — purer near-term sentiment
    TRADIER_OI_INJECT_MIN_TOTAL_OI: int = 1000     # require ≥1000 contracts open across all monitored exps (filters illiquid names)
    TRADIER_OI_INJECT_MAX_EACH: int = 10            # cap per side
    TRADIER_OI_INJECT_STALE_MAX_HOURS: float = 4.0 # skip cache files older than 4h (fetcher missed last cycle)
    # === 2026-04-27 STOCKS RED_ZONE_GATE — options-OI walls as proxy for L2 depth (Tradier has no L2) ===
    # Block LONG entries when underlying is within RED_ZONE_TRADIER_MIN_DISTANCE_PCT BELOW max_call_oi_strike (resistance ceiling).
    # Block SHORT entries when underlying is within RED_ZONE_TRADIER_MIN_DISTANCE_PCT ABOVE max_put_oi_strike (support floor).
    # The strike with heaviest call OI = price point above which dealers' delta-hedging crushes momentum.
    # The strike with heaviest put OI = price point below which dealers absorb sell-pressure ("max pain" theory.)
    RED_ZONE_TRADIER_GATE_ENABLED: bool = True      # 2026-04-28 restored — probe one-by-one
    RED_ZONE_TRADIER_MIN_DISTANCE_PCT: float = 0.5  # block entry when underlying within 0.5% of wall strike
    RED_ZONE_TRADIER_MIN_OI_AT_WALL: int = 1000     # require wall strike to have ≥1000 OI (filters spurious thin strikes)
    RED_ZONE_TRADIER_AUGMENT_GATE_ENABLED: bool = True   # apply to AUGMENT actions (don't add into resistance)
    RED_ZONE_TRADIER_STALE_MAX_HOURS: float = 4.0   # skip wall check if cache older than 4h
    # === 2026-04-27 LOWER-HIGHS / HIGHER-LOWS FILTER (sweep-testable, default OFF — STOCKS) ===
    # User: "block long trades while 1h/4h charts make lower highs (shorts vv) instead of the sma_200_D filter (or on top of it)".
    # LONG blocked when 1h+4h make lower highs (LH); optionally also require lower lows (LL).
    # SHORT blocked when 1h+4h make higher lows (HL); optionally also require higher highs (HH).
    # When REPLACE_SMA200D=True, also turns OFF SMA200_DIST_ENTRY_ENABLED so this filter REPLACES the SMA200_4h dist filter rather than ADDS.
    LH_HL_FILTER_ENABLED: bool = False
    LH_HL_FILTER_MODE: str = "STRICT_2BAR"             # "STRICT_2BAR" | "DC_REGRESS"
    LH_HL_FILTER_TF_REQ: int = 2                       # 1=either 1h/4h, 2=both must confirm
    LH_HL_FILTER_DC_THRESHOLD_PCT: float = 0.5         # only used in DC_REGRESS mode
    LH_HL_FILTER_REPLACE_SMA200D: bool = False         # if True, turns off SMA200_DIST_ENTRY_ENABLED
    LH_HL_FILTER_AUGMENT_GATE_ENABLED: bool = True     # apply to AUGMENT actions
    LH_HL_FILTER_REQUIRE_BOTH: bool = False            # False=LH-only/HL-only; True=LH+LL/HL+HH (full channel)
    # === CLASSIC CHART FORMATIONS (causal NPZ + live-kline parity) ===
    # Each family is an independent matrix path.  ENTRY follows a bullish
    # formation for LONG / bearish for SHORT; EXIT requires the opposite
    # formation. Detection fields remain published for observation/audit, but
    # action defaults stay OFF pending the 115-cell full + holdout campaign.
    FORMATION_TFS: str = "15m,1h,4h,D"
    FORMATION_MIN_SCORE: float = 0.65
    FORMATION_POSITION_SIZE_MULT: float = 1.0
    FORMATION_EXIT_MIN_GAIN_PCT: float = 0.0
    FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED: bool = False
    FORMATION_HEAD_SHOULDERS_EXIT_ENABLED: bool = False
    FORMATION_DOUBLE_TOP_BOTTOM_ENTRY_ENABLED: bool = False
    FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED: bool = False
    FORMATION_WEDGE_ENTRY_ENABLED: bool = False
    FORMATION_WEDGE_EXIT_ENABLED: bool = False
    FORMATION_TRIANGLE_ENTRY_ENABLED: bool = False
    FORMATION_TRIANGLE_EXIT_ENABLED: bool = False
    FORMATION_FLAG_PENNANT_ENTRY_ENABLED: bool = False
    FORMATION_FLAG_PENNANT_EXIT_ENABLED: bool = False
    FORMATION_CUP_HANDLE_ENTRY_ENABLED: bool = False
    FORMATION_CUP_HANDLE_EXIT_ENABLED: bool = False
    FORMATION_TREND_STRUCTURE_ENTRY_ENABLED: bool = False
    FORMATION_TREND_STRUCTURE_EXIT_ENABLED: bool = False
    # === 2026-04-30 HTF PORT FROM CRYPTO — tradier-only HTF (W/M/4h) entry+exit anchors ===
    # Crypto baseline (pool_sharpe 0.7770) uses W and M timeframes via ALL_TF_BRAKE; tradier
    # baseline ignores them entirely. These three switches port the highest-leverage HTF idea.
    # ALL DEFAULT OFF — flip in sweep / live promotion only after verified ≥0.05 sharpe lift.
    HTF_W_M_ALIGN_GATE_TRADIER_ENABLED: bool = False   # F1: entry GATE — N of 2 (W, M) WT must agree with side
    HTF_W_M_ALIGN_TRADIER_REQUIRED: int = 2            # 1=either; 2=both
    HTF_DC_BREAKOUT_TRADIER_ENABLED: bool = False      # F2: additive entry — close > dc_high_4h * (1+thr) AND W WT on side
    HTF_DC_BREAKOUT_TRADIER_TF: str = "4h"             # 4h | D | W (DC band timeframe)
    HTF_DC_BREAKOUT_TRADIER_THRESHOLD_PCT: float = 0.0 # 0 = exact break; 0.1 = +0.1% confirm
    HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT: bool = True  # require W WaveTrend on side (HTF anchor)
    HTF_W_REVERSAL_EXIT_TRADIER_ENABLED: bool = False  # F3: exit when wt1_W against side AND wt1_D against side
    HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D: bool = True # also require D against (2-TF anchor; if False, W alone suffices)
    # === 2026-04-27 FUNDING/OI ENTRY-GATE ANALOGUES (mirror crypto FUNDING_GATE + OI_CONFIRM) ===
    # Stocks have no native funding rate; options put/call OI ratio is the bullish/bearish flow analogue.
    # Reads data/stocks_oi_cache/{sym}.json (populated by tradier_options_oi_fetcher.py, READ-ONLY).
    # Defaults OFF — flip in sweep / live promotion only after validation.
    # Pattern mirrors ez_positions_quick.py:11515-11574 (crypto FUNDING_GATE + OI_CONFIRM 4-quadrant).
    FUNDING_GATE_ENABLED_TRADIER: bool = False        # default OFF; flip in sweep
    FUNDING_GATE_PC_RATIO_LONG_MAX: float = 1.2       # block LONG when put/call ratio >= this (bearish flow)
    FUNDING_GATE_PC_RATIO_SHORT_MIN: float = 0.83     # block SHORT when put/call ratio <= this (bullish flow)
    FUNDING_GATE_TRADIER_NEAR_MONEY_PREFER: bool = True   # prefer near_money_pc_ratio (±5% strikes) when present — purer signal
    FUNDING_GATE_TRADIER_HEDGE_GATE_ENABLED: bool = False # apply gate to hedge entries too (default OFF)
    FUNDING_GATE_TRADIER_STALE_MAX_HOURS: float = 4.0     # skip gate if cache older than 4h (fail-open)
    OI_CONFIRM_ENABLED_TRADIER: bool = False          # 4-quadrant OI×price gate (Schabacker classic)
    OI_CONFIRM_MIN_OI_CHANGE_PCT_TRADIER: float = 0.5 # |total OI change since last cache snapshot| significance threshold
    OI_CONFIRM_MIN_PRICE_PCT_TRADIER: float = 0.3     # |price change since last cache snapshot| significance threshold
    OI_CONFIRM_TRADIER_HEDGE_GATE_ENABLED: bool = False
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
        "PYPL": "TECH_CONS", "ABNB": "TECH_CONS", "DUOL": "TECH_CONS",
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
    OPTIONS_MAX_PER_SECTOR: float = 0.35      # tightened 2026-04-26 per §L6.2 (was 0.40)
    OPTIONS_MAX_PER_GROUP: float = 0.60       # Max 60% of portfolio in one sector group
    OPTIONS_MAX_PER_SYMBOL: float = 0.20      # tightened 2026-04-26 per §L6.1 (was 0.25)
    # === MULTI-TF WT/DC ENTRY GATE (2026-04-26 — closes "buy any cheap option" leak) ===
    # Every options buy runs wt_dc_score_entry on the underlying's indicators.
    # Score 0-100 (validated Sharpe 27.4, 121 stocks 2.9yr). Threshold 70 ≈
    # 3-of-5 majors aligned (D + 4h + 1h_cross + dc_1h + k_5m).
    OPTIONS_BUY_WT_DC_GATE_ENABLED: bool = True
    OPTIONS_BUY_MIN_WT_DC_SCORE: float = 70.0
    # === AUGMENT-INTO-LOSS BLOCK (2026-04-26 — closes PLTR Jul17 averaging-down pattern) ===
    # Refuse to add more contracts to an existing OCC if its current bid is
    # below entry_avg × this fraction. 0.85 = "down 15%+ already, do not double down."
    OPTIONS_AUGMENT_INTO_LOSS_BLOCK_ENABLED: bool = True
    OPTIONS_AUGMENT_INTO_LOSS_THRESHOLD: float = 0.85
    # === HEDGE LADDER (2026-04-26 — replaces plain SELL_NOW on losing positions) ===
    # Order: HOLD if at confirmed bottom → BUY_PUT if underpriced put exists →
    # EQUITY_HEDGE. See tradier_options_hedge.py. Wired at the existing
    # OPTIONS_EQUITY_HEDGE entry in tradier_options_analyzer:2650.
    # *** DEFAULT OFF *** — flip to True only after verifying with shadow runner.
    # When False, existing equity-hedge path fires unchanged (status quo).
    OPTIONS_HEDGE_LADDER_ENABLED: bool = False
    # === OPENING-BUFFER NO-CLOSE (2026-04-26 — don't sell at open) ===
    # Block CLOSE/sell-to-close orders during first N minutes after market open.
    # Wired in tradier_manage.evaluate_stop AND tradier_options_analyzer.auto_sell.
    OPENING_BUFFER_NO_CLOSE_MINUTES: float = 30.0
    # === PREMARKET NO-FIRE (2026-04-26 — no surprise BUYS while owner sleeps) ===
    # Default-on guard: premarket cron saves the adjusted plan but skips _place_gtc_buys.
    # Morning brief surfaces the held plan; owner re-runs manually without flag to fire.
    OPTIONS_PREMARKET_NO_FIRE: bool = True
    # === LOSS-DEEPENING ALERTS (2026-04-26 — close monitor on the 4 losers) ===
    # Alerts fire when a position drops > DROP_PP from its prior worst-seen pct
    # (across snapshots) OR crosses ABS_LOSS_PP (one-shot per position).
    # Output: data/options_state/alerts_<YYYYMMDD>.jsonl + morning brief surface.
    OPTIONS_ALERT_DROP_PP: float = 5.0
    OPTIONS_ALERT_ABS_LOSS_PP: float = 25.0
    OPTIONS_HEDGE_BOTTOM_MIN_SIGNALS: int = 2
    OPTIONS_HEDGE_K_OVERSOLD_PCT: float = 25.0
    OPTIONS_HEDGE_DC_REL_TOL_PCT: float = 1.0
    OPTIONS_HEDGE_PUT_DELTA_MIN: float = 0.30
    OPTIONS_HEDGE_PUT_DELTA_MAX: float = 0.50
    OPTIONS_HEDGE_PUT_MAX_IV_RANK: float = 35.0
    OPTIONS_HEDGE_PUT_DTE_MIN: int = 45
    OPTIONS_HEDGE_PUT_DTE_MAX: int = 120
    OPTIONS_HEDGE_PUT_MAX_SPREAD_PCT: float = 8.0
    RISK_FREE_RATE: float = 0.045
    OPTIONS_MIN_SECTORS: int = 2              # Min sectors for hedged tier
    OPTIONS_MIN_GROUPS: int = 3               # Min groups for full diversification tier
    OPTIONS_HEDGE_RATIO_MIN: float = 0.25     # Min puts/(puts+calls) to qualify as hedged
    OPTIONS_MAX_CONTRACTS_PER_ORDER: int = 3   # Hard cap: never buy >N contracts in one order (practical ceil given $800/order + $9/share rule)
    # Market direction ratio: bull_exposure / (bull + bear). Too high = over-long market.
    OPTIONS_MARKET_RATIO_MIN: float = 0.25    # Min fraction of exposure that is bull-market-bets
    OPTIONS_MARKET_RATIO_MAX: float = 0.75    # Max fraction of exposure that is bull-market-bets
    # === OPTIONS EXIT THRESHOLDS ===
    # 2026-04-22 user directive: DISABLE MAX_LOSS_GUARD entirely. It sold NEM at -40.6% bottom.
    # WT_DELTA_SLOWDOWN / HTF_WT_CROSS_AGAINST / PEAK_GIVEBACK are the legitimate exits.
    # MAX_LOSS thresholds kept for if ever re-enabled — only checked when _ENABLED=True.
    OPTIONS_MAX_LOSS_GUARD_ENABLED: bool = False # 2026-04-22 disabled per user — bottom-seller
    OPTIONS_MAX_LOSS_PCT_DTE_30: float = -80.0   # (inactive unless re-enabled) DTE > 30
    OPTIONS_MAX_LOSS_PCT_DTE_14: float = -60.0   # (inactive unless re-enabled) 14 < DTE <= 30
    OPTIONS_MAX_LOSS_PCT_DTE_LOW: float = -40.0  # (inactive unless re-enabled) DTE <= 14
    # === OPTIONS EQUITY-HEDGE (2026-04-22 user directive) ===
    # Replaces MAX_LOSS_GUARD. When an option is losing AND can't be sold at better
    # than OPTIONS_EQUITY_HEDGE_TRIGGER_PCT (i.e. best bid would realize worse than
    # -10% loss), open a stock hedge of equal-and-opposite delta so directional
    # exposure is neutralized while premium decays. Unwind hedge when the option
    # is sellable again (bid recovers past the threshold).
    # Losing CALL -> sell_short stock; Losing PUT -> buy stock. Size = |delta|×qty×100.
    OPTIONS_EQUITY_HEDGE_ENABLED: bool = False
    OPTIONS_EQUITY_HEDGE_TRIGGER_PCT: float = -10.0  # Unsellable := bid implies loss ≤ this (%)
    # === HEDGE SIZING CAPS (2026-04-27 — PLTR triple-fire $50k short on $4.3k call) ===
    # Hedge qty = min( delta×contracts×100, MAX_PCT_OF_OPT_COST × cb / px,
    #                  MAX_NOTIONAL_USD / px, MAX_POSITION_SIZE / px, MAX_ORDER_VALUE / px )
    # First pass at PLTR opened 137-share short on a $4,262 call (~$19.6k notional, 4.6× option
    # cost basis). User considers any hedge >>1× option cost basis insane. Default 150% (1.5×).
    OPTIONS_EQUITY_HEDGE_MAX_PCT_OF_OPT_COST: float = 100.0  # was 150 — 2026-04-27 emergency tighten: hedge ≤ 1× option cost basis
    OPTIONS_EQUITY_HEDGE_MAX_NOTIONAL_USD: float = 2500.0    # was 5000 — 2026-04-27 emergency halve
    # === HEDGE DUP-FIRE & DC-BREACH GUARDS (2026-04-27 — same incident) ===
    # Two analyzer processes (--auto-sell + --daemon) raced and fired hedge 3× in 12min,
    # net 346 shares short vs 137 fair. fcntl lock on options_equity_hedges.lock prevents
    # the race. Per-OCC cooldown adds defense in depth.
    OPTIONS_EQUITY_HEDGE_COOLDOWN_MIN: float = 60.0  # don't re-fire same OCC within N minutes
    # Wrong-direction exit: if existing hedge is sell_short and price > dc_high_5m (or
    # buy hedge below dc_low_5m), close immediately — don't wait for option to recover.
    OPTIONS_EQUITY_HEDGE_DC_BREACH_EXIT_ENABLED: bool = False
    # Direction guard at OPEN: never sell_short while market is moving up, never buy
    # while market is moving down — even when "hedging". Hedging is not suicide.
    OPTIONS_EQUITY_HEDGE_DIRECTION_GUARD_ENABLED: bool = False
    # === HEDGE PAIR GUARD (2026-04-22 — NEM naked-short incident) ===
    # When an option is hedged by an opposite-side stock position on the same underlying
    # (call paired with short shares, or put paired with long shares), auto-sell MUST NOT
    # close the option alone — doing so leaves the stock leg naked directional. The NEM
    # incident on 2026-04-22 cost $1,500 (calls sold) + ongoing $500 loss on naked -200 NEM short.
    # Auto-sell is blocked; exit signals still surface so user can close both legs manually.
    OPTIONS_HEDGE_PAIR_GUARD_ENABLED: bool = False
    # WT-velocity (NOT greeks delta) SLOWDOWN exit — rewritten 2026-04-22.
    # OLD (pre-fix) logic: sell when ADVERSE velocity accelerates → sold NEM at -40.6% bottom.
    # NEW logic per user rule: sell when FAVORABLE velocity slows down = momentum peak in.
    # CALL fires if wt_velocity_D was > OPTIONS_WT_ACCEL_MIN_ABS and current value shrunk by ≥ OPTIONS_WT_SLOWDOWN_PCT.
    # PUT fires symmetrically on |velocity| shrinking toward 0.
    OPTIONS_WT_ACCEL_MIN_ABS: float = 10.0       # min |wt_velocity_D_prev| for slowdown to count (ignore noise)
    OPTIONS_WT_ACCEL_GROWTH_PCT: float = 25.0    # LEGACY alias — reused as slowdown % if OPTIONS_WT_SLOWDOWN_PCT unset
    OPTIONS_WT_SLOWDOWN_PCT: float = 25.0        # velocity must shrink by ≥25% bar-over-bar for slowdown to fire
    # Support/resistance break exit — symmetric to DC-High Reversal rule on stocks side
    OPTIONS_LEVEL_BREAK_BUFFER: float = 0.01     # 1% buffer past dc_low_D (call) / dc_high_D (put)
    OPTIONS_LEVEL_BREAK_MIN_DTE: int = 14        # Don't fire on sub-14-DTE (noise dominates)
    # Continuous sector/put-call enforcement (applied in daily + premarket cycles)
    OPTIONS_CONTINUOUS_SECTOR_GATE: bool = True  # Block new buys that widen existing sector/group/symbol violation
    OPTIONS_USER_CANCEL_COOLDOWN_HOURS: float = 4.0  # Don't re-propose a user-canceled OCC for N hours
    # === OPTIONS ORDER BUDGET RULES (2026-04-23) ===
    # Max spend per new order = $800. Exception: if one contract costs more than $800, still
    # buy exactly 1 contract (no multi-contract spending spree). Hard rule: if option
    # price > $9/share (= $900/contract), max 1 contract regardless of budget.
    OPTIONS_MAX_ORDER_BUDGET: float = 400.0         # was 800 — 2026-04-27 emergency halve
    OPTIONS_MAX_SINGLE_CONTRACT_PRICE: float = 9.0  # If price/share > this, max qty=1
    # === OPTIONS BUY SANITY GATES (2026-04-22 — blocks JNJ/ABT-style misbuys) ===
    # Background: 2026-04-22 14:05 UTC cron bypass bought 2 OTM calls on downtrending,
    # non-allowlisted stocks (JNJ 6.7% OTM delta 0.14, ABT 6% OTM). -$500 same-day loss.
    # These gates apply in make_decisions() (run_agent path).
    OPTIONS_BUY_MIN_ABS_DELTA: float = 0.35      # Reject lottery tickets — min |delta| for any new buy
    OPTIONS_BUY_MAX_OTM_PCT: float = 3.0         # Reject strikes >3% OTM (calls) / <3% ITM for puts relative to underlying
    OPTIONS_BUY_REQUIRE_D_ALIGN: bool = True     # CALL needs wt_cross_D != BEAR; PUT needs wt_cross_D != BULL
    OPTIONS_BUY_MIN_DTE: int = 60                # User rule 2026-04-22: never open options <2 months out (JNJ bought at 22 DTE = theta trap)
    OPTIONS_BUY_PREFERRED_DTE: int = 90          # Prefer 3+ months out — score bonus applied when dte >= this
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
    ENTRY_ZONE_LONG: float =               80.0   # 2026-04-27 LOOSENED from 35 — was blocking 65% of long entries. Now permissive: longs allowed when k<80.
    ENTRY_ZONE_SHORT: float =              20.0   # 2026-04-27 FIX TYPO — was 100.0 (always-block bug from 2026-04-16 wiring task; comment said 100-35=65 but value typed wrong). Loosened to 20: shorts allowed when k>20.
    ENTRY_MIN_ALIGNMENT: int =             5      # 2026-06-09: lowered 10→5 (10=impossible, max score=10 but alignment=5/10 was blocking good trades). Was 8→10. ROLLBACK: 8.
    ENTRY_PRIMARY_TF: str =                '4h'   # BACKTEST_CHANGE_T7 was 1h → 4h slower primary TF
    ENTRY_TRIGGER_TF: str =                '15m'  # Trigger TF for crossover (was 5m, shifted to 15m for stocks) ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:2680 referenced in entry eval
    # === SMA200 DISTANCE FILTER (backtest) ===
    SMA200_DIST_ENTRY_ENABLED: bool = False  # BACKTEST_CHANGE_T3 SMA200 distance gate for entries
    SMA200_DIST_LONG_THRESHOLD_4H: float = -10.0  # BACKTEST_CHANGE_T3 only long when price within -10% of SMA200 on 4h
    # === MFI ENTRY FILTER (backtest) ===
    # 2026-05-18 18:30 FIXED (was LIVE BUG): old code blocked LONG when mfi_D > 20 with comment "not oversold"
    # — but MFI_D normal range is 30-70 so this blocked ~95%+ of LONG entries. The "only long when MFI<20"
    # oversold-confirm intent was wrong for a trend-following stocks system. Inverted to OVERBOUGHT FILTER:
    # block LONG only when MFI_D > MFI_LONG_THRESHOLD_D (default 80 = overbought reversal expected).
    # Gate logic in tradier_manage.py:11156-11160 reads `mfi_D > MFI_LONG_THRESHOLD_D` → with new threshold=80
    # this now correctly blocks ~5-15% of LONG entries (overbought zone) rather than ~95%.
    MFI_ENTRY_ENABLED: bool = False  # DESTROYED 2026-09-02 per user NEVER 0 TRADES — BLOCKED_MFI_ENTRY_D_81 completely destroyed
    MFI_LONG_THRESHOLD_D: float = 80.0  # block LONG when mfi_D > 80 (overbought reversal expected); was 20.0 (oversold-required, broken)
    # === WT CROSSUNDER SHORT (backtest) ===
    WT_CROSSUNDER_15M_SHORT: bool = True  # BACKTEST_CHANGE_T5 enable WT crossunder on 15m for short entries
    # === ALIGNMENT GATE (backtest) ===
    ALIGNMENT_GATE_MIN: int = 4  # BACKTEST_CHANGE_T8 minimum indicators aligned
    ALIGNMENT_GATE_TOTAL: int = 36  # BACKTEST_CHANGE_T8 total alignment score required ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:8009,8187 alignment gate log denominator
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
    SATOSHIT_LONG_MFI_MAX_TRADIER: float = 120.0
    SATOSHIT_SHORT_RSI_MIN_TRADIER: float = 55.0
    SATOSHIT_SHORT_STOCH_K_MIN_TRADIER: float = 50.0
    SATOSHIT_SHORT_MFI_MIN_TRADIER: float = 62.5
    SATOSHIT_HTF_MFI_D_MIN_TRADIER: float = 30.0
    SATOSHIT_HTF_RVOL_1H_MIN_TRADIER: float = 0.3
    # Exit thresholds
    SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER: float = 55.0
    SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER: float = 60.0
    SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER: float = 42.0
    SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER: float = 50.0
    # === PARTIAL_PROFIT_LOCK (2026-04-21) — mirror of crypto PPL, stock-tuned defaults ===
    # Step 1 (gain >= PPL_GAIN_PCT_TRADIER): close 50% via execute_trade_action(action='REDUCE').
    # Step 2 (gain >= PPL_ARM_GAIN_PCT_TRADIER): arm trailing stop at first-exit price.
    # Step 3 (price back to first-exit price): close remainder via execute_trade_action(action='CLOSE').
    # Values differ from crypto: stocks have wider spreads + 5m base, so gain bars are larger.
    # USER 2026-07-22: stocks churn far cheaper than crypto — measured on real in/out fills,
    # 0.1% was too pessimistic and ~0.06% is realistic. Until now this was UNDECLARED here and
    # backtest_v8_engine.py:1759 fell back to a hardcoded 0.05, so no config or sweep could
    # reach it. Declared so it is one number, visible in the matrix and overridable per symbol.
    # USER 2026-07-29: stocks use 0.05% total round-trip slippage/spread;
    # Tradier equity commissions are effectively zero.  This is a percent,
    # not a decimal fraction (0.05 == five basis points round trip).
    ROUND_TRIP_COST_PCT: float = 0.05
    # Global master/kill switch. When False, live per-symbol/hourly/Redis overlays
    # are not allowed to re-enable PPL.
    PARTIAL_PROFIT_LOCK_ENABLED: bool = False   # 2026-08-10 USER MANDATE: ever-in-gain never slip to loss -> PPL ON (was OFF 2026-07-19). 50% at +0.5%, arm at +0.75% breakeven
    # 2026-04-25 PPL CURVE (114-sym, 4.3yr, HVC sweep confirmed): 0.5%=2.832 Sharpe/114%gain | 0.9375%(baseline)=2.435/171% | 1.5%=1.784/206% | 2.0%=1.507/226% | 2.5%=1.379/239% | 3.0%=1.253/244% | 4.0%=1.130/259% | 5.0%=1.092/262% | disabled=1.077/247%.
    # 2026-05-26 crypto grid favours 1.5% (best risk-adjusted vs PPL OFF baseline); apply to stocks symmetrically — tradier-specific sweep can refine later.
    # Use PARTIAL_PROFIT_LOCK_GAIN_PCT (NOT _TRADIER key) for vectorized sweeps.
    PARTIAL_PROFIT_LOCK_ACCOUNTS_TRADIER: List[str] = field(default_factory=lambda: ["trb", "trc"])
    PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER: float = 0.5      # 2026-08-10 winner-protection: was 1.5 (too late). 0.5% locks ever-in-gain
    PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER: float = 0.75 # 2026-08-10 proportional to 0.5% (+0.25%)
    # P2-G: PPL sweep test — vary close and arm thresholds with commission-aware testing
    # Sweep range: close=[0.1,0.2,0.3,0.5,0.75], arm=[0.3,0.5,0.75,1.0]
    # NOTE: at 0.1% close trigger, round-trip commission ~0.12% makes trade economically borderline
    PARTIAL_PROFIT_LOCK_SWEEP_ENABLED: bool = False  # use sweep params instead of live params (backtest only)
    PARTIAL_PROFIT_LOCK_SWEEP_GAIN_PCT: float = 0.3   # sweep variant of GAIN_PCT (default = live default)
    PARTIAL_PROFIT_LOCK_SWEEP_ARM_PCT: float = 0.5    # sweep variant of ARM_PCT
    PARTIAL_PROFIT_LOCK_SLIPPAGE_PCT: float = 0.05    # per-leg slippage for sweep (0.05% × 2 sides + 0.01% commission ≈ 0.12% round-trip)
    PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER: float = 0.02
    PARTIAL_PROFIT_LOCK_FRAC_TRADIER: float = 0.625
    PARTIAL_PROFIT_LOCK_USE_MAKER_TRADIER: bool = True
    # === 2026-04-26 USER RULE — MICRO_SCALP_STOCKS_MAKER (mirror of crypto MICRO_SCALP_USDC_MAKER) ===
    # Stocks-side micro-scalper: closes positions at gain >= MICRO_SCALP_STOCKS_GAIN_THRESHOLD_PCT
    # AND first deceleration (gain < prev_gain). Reopens when price re-crosses exit_price.
    # Threshold higher than crypto (0.05% vs 0.02%) because stock spreads + 5m base TF.
    # Limit-only via existing place_order chase loop (already maker-first with market fallback at 10s).
    # Fires from process_position BEFORE evaluate_stop — bypasses STOCK_MIN_HOLD/UNIVERSAL_NOLOSS_GATE
    # because we close ONLY at positive gain. Bypasses are safe by construction.
    # Constrained to RTH (13:30-20:00 UTC) by is_regular_trading_hours() inside place_order.
    MICRO_SCALP_STOCKS_MAKER_ENABLED: bool = False
    MICRO_SCALP_STOCKS_ACCOUNTS: List[str] = field(default_factory=lambda: [])#"trb", "trc", "tra"])
    MICRO_SCALP_STOCKS_GAIN_THRESHOLD_PCT: float = 0.2
    MICRO_SCALP_STOCKS_PEAK_FLOOR_PCT: float = 0.6  # 2026-05-28 USER: micro-scalp may only CLOSE a position whose gain has ALREADY peaked >= this floor. Stops 0.05-0.1% round-trip churn (IBIT g0.074% peak0.098% never near 0.5%).
    # 2026-05-29 USER: stocks reentry anti-churn — require a 4-bar Donchian breakout (dc_high4_5m /
    # dc_low4_5m, computed on already-closed 5m bars) before re-entering, not just a touch of the exit
    # price. Mirror of crypto REENTRY_LIVE_MONITOR_DC_BREAK (vec winner). Stocks base TF = 5m.
    REENTRY_LIVE_MONITOR_DC_BREAK_ENABLED: bool = True  # 2026-08-03 EMERGENCY ANTI-CHURN: mandatory reentry requires a live 5m Donchian break
    REENTRY_LIVE_MONITOR_DC_BREAK_TF: str = "5m"
    REENTRY_LIVE_MONITOR_DC_BREAK_USE_4BAR: bool = False  # emergency contract uses dc_high_5m/dc_low_5m, not the tighter 4-bar level
    # ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━ PARITY MASTERS — TWO INDEPENDENT ━━━━━━━━━━━━━━━━━━━━┓
    # ┃ MASTER 1 — 1m/3m/5m TF availability → LIVE_5m_trading_ENABLED + PARITY_MIN_DECISION_TF ┃
    # ┃ MASTER 2 — non-vectorizable/NPZ-unavailable → PARITY_DISABLE_NON_VECTORIZABLE         ┃
    # ┃ These are INDEPENDENT — do not touch 2 when testing 1. See config.py:4260 block.    ┃
    # ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
    # 2026-09-09 PORT from crypto config.py — reentry easier/better (green candle HTF bypass + bar-turn)
    REENTRY_CONFIRMATION_GATES_ENABLED: bool = True
    LIVE_5m_trading_ENABLED: bool = True  # MASTER 1 — 1m/3m/5m TF availability (stocks 5m, crypto 3m) — OFF for parity test
    REENTRY_BAR_TURN_ENABLED: bool = True  # also open if WT not flipped but 3m/5m bar turning (HH/HL long)
    REENTRY_BAR_TURN_TF: str = "5m"  # stocks 5m (crypto 3m)
    REENTRY_BAR_TURN_REQUIRE_BOTH: bool = False
    REENTRY_BAR_STRUCTURE_ENABLED: bool = True  # replace fixed pct with HL/HH structure
    REENTRY_BAR_STRUCTURE_TF: str = "5m"
    REENTRY_SMA200_BACKUP_ENABLED: bool = True  # ON — alternative tested via TEMPLATE
    PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED: bool = True
    PRICE_CROSSED_HTF_AGAINST_VETO_BAR_TURN_BYPASS: bool = True
    PRICE_CROSSED_HTF_AGAINST_VETO_HA_BYPASS: bool = True
    RECENT_REDUCTION_GUARD_ENABLED: bool = True
    RECENT_REDUCTION_GUARD_WINDOW_S: float = 450.0
    RECENT_REDUCTION_GUARD_USE_4BAR: bool = True
    # 2026-09-09 aggressive reentry near exit (better churn than miss) + rally sizing
    REENTRY_PRICE_IMPROVE_PCT: float = 0.08  # legacy, superseded by bar-structure above
    REENTRY_NEAR_EXIT_CHURN_OK: bool = True  # allow reentry ~exit price when retrace absent
    REENTRY_POSITIVE_EXIT_SIZE_MULT: float = 1.25  # +25% qty after profitable exit
    # LEGENDARY RUN LEDGER + GOLDEN PULLBACK — mirrors crypto config.py 2026-09-09
    EXPLODING_LEDGER_ENABLED: bool = True
    EXPLODING_LEDGER_LOOKBACK_DAYS: int = 15
    EXPLODING_LEDGER_TOP_N: int = 20
    EXPLODING_LEDGER_MIN_MOVE_PCT: float = 8.0
    GOLDEN_PULLBACK_ENABLED: bool = True
    GOLDEN_PULLBACK_SIZE_MULT: float = 2.0  # 2x max previous week
    GOLDEN_PULLBACK_SIZE_CAP_MULT: float = 4.0
    GOLDEN_PULLBACK_STOCH_LOW_THR: float = 35.0  # loosened 25->35
    GOLDEN_PULLBACK_DC_BASIS_TOL_PCT: float = 1.50  # loosened 0.50->1.50
    # NOLOSS exception (sweep-only, default OFF): 5/5 WT TFs against → allow bypass. TFs: 5m/15m/1h/4h/D for stocks.
    # 2026-04-25 rapid-grid HVC sweep (114-sym, 4.3yr): CONFIRMED DAMAGING on all thresholds:
    #   3TF=0.880 Sharpe (-1.56 vs baseline, 3.76% DD) | 4TF=1.092 (-1.34, 2.79% DD) | 5TF=0.731 (-1.70, 8.3% DD).
    # VERDICT: HOLD wins. Stocks recover after WT reversal — closing on technicals at any threshold destroys edge.
    # COMBINED WS_KILL+4TF=1.071 Sharpe (-1.36) — combining kill+bypass does NOT help.
    NOLOSS_BYPASS_WT_5OF5_ENABLED: bool = True  # 2026-09-11 EMERGENCY falling market: False→True — longs at -0.3% held as NOLOSS_HOLD while market falling quickly, account draining, need WT-against bypass.
    NOLOSS_BYPASS_WT_5OF5_MIN_TFS: int = 3  # 2026-09-11 EMERGENCY: 5→3 — 5/5 never fires, 3/5 allows longs to close at loss when 3 TFs bear (MCD/NOC/LMT longs draining).
    # 2026-05-09 USER MANDATE — WT_15M_VEL_SLOW loss-bypass exit (mirror of crypto).
    # Fires CLOSE before NO_LOSS / hedge / MTF when:
    #   gain < band (0.10), wt_velocity_15m sign opposes position, AND
    #   (|wt_velocity_15m| ≤ near_zero (0.1) OR |vel_now| < |vel_prev|).
    WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED: bool = True
    # 2026-05-09 USER MANDATE: stocks need higher TFs (D/W rule the moves; markets closed
    # most of the day). Sweep-test which TFs and decel-ratio actually fire on stocks.
    # 2026-05-09 USER REFINEMENT — R2 peak-then-collapse only (matches crypto):
    #   max_gain ≥ R2_PEAK_MIN_PCT AND FLOOR ≤ current gain ≤ BAND.
    R2_PEAK_MIN_PCT: float = 0.5                   # max_gain must have peaked ≥ this
    WT_15M_VEL_SLOW_GAIN_BAND_PCT: float = 0.10    # current gain ≤ this (collapsed back)
    WT_15M_VEL_SLOW_GAIN_FLOOR_PCT: float = 0.01   # current gain ≥ this (don't close at loss — R1/DC15M backstop handle that)
    WT_15M_VEL_NEAR_ZERO_THRESHOLD: float = 0.1    # legacy fixed gate
    WT_VEL_DECEL_RATIO: float = 0.5                # |vel| < |vel_prev| * RATIO (DYNAMIC)
    WT_VEL_USE_DECEL_RATIO_ONLY: bool = True
    R2_TF_LIST: tuple = ('1h', '4h', 'D')          # stocks: HTFs primary per user 2026-05-09
    # === WT_15M_BOUNCE OPEN — 2026-09-01 parity fix (tradier mirror) ===
    WT_15M_BOUNCE_OPEN_ENABLED: bool = False
    WT_15M_BOUNCE_BB_MIN: float = 0.05
    WT_15M_BOUNCE_BB_MAX: float = 0.95
    WT_15M_BOUNCE_REQUIRE_BOTH_HTF: bool = False
    WT_15M_BOUNCE_FILTER_HL_ENABLED: bool = False
    WT_15M_BOUNCE_FILTER_HH_ENABLED: bool = False
    WT_15M_BOUNCE_FILTER_MODE: str = "AND"
    WT_15M_BOUNCE_VOLUME_FILTER_ENABLED: bool = False
    WT_15M_BOUNCE_VOLUME_MODE: str = "relvol"
    WT_15M_BOUNCE_VOLUME_THRESHOLD: float = 1.0
    WT_15M_BOUNCE_LOW_1H_GT_PREV: bool = False  # alias for FILTER_HL
    WT_15M_BOUNCE_HIGH_1H_GT_PREV: bool = False  # alias for FILTER_HH
    WT_15M_BOUNCE_REL_VOL_GT_1: bool = False  # alias for VOLUME_FILTER
    WT_ACCEL_EXIT_ENABLED: bool = False  # FIX 2026-09-08: parity guard
    WT_DIV_EXIT_ENABLED: bool = False  # FIX 2026-09-08: parity guard
    SIMPLE_PRICE_GT0_ENABLED: bool = False  # SIMPLE price>0 test — ridiculously simple, always trades when enabled (added 2026-09-06 alongside WT15, never fails)
    # 2026-05-09 USER MANDATE — R3 HEDGE_INVARIANT (mirror of crypto). Stocks
    # don't have the same hedge engine but the rule still applies: position with
    # 5m+1h WT against AND gain<0 AND no hedge → dump + alert. Tradier doesn't
    # currently auto-hedge so R3 will fire as a forced-exit + alert in practice.
    R3_HEDGE_INVARIANT_DUMP_ENABLED: bool = False  # default OFF for stocks until hedge engine exists
    R3_GAIN_MAX_PCT: float = 0.0
    # 2026-05-10 — HTF veto (mirror crypto). Stocks HTF = 4h/D/W.
    GOLDEN_RULE_HTF_VETO_ENABLED: bool = True
    GUARANTEED_REENTRY_HTF_VETO_ENABLED: bool = False
    HTF_VETO_REQUIRE_D: bool = True
    # Consensus uses existing GOLDEN_RULE_HTF_MIN_TFS + GOLDEN_RULE_MIN_IND
    # (defined further down) — same as crypto, same as sweeps. No new knobs.
    GOLDEN_RULE_REQUIRE_HEDGE_OPEN: bool = False  # stocks: no auto-hedge engine yet
    GUARANTEED_REENTRY_REQUIRE_HEDGE_OPEN: bool = False
    # R1 — DC_LOW4 EMERGENCY CLOSE (stocks mirror; uses 5m base instead of 3m)
    # 2026-08-03 containment: OFF.  The tight 5m DC_LOW4 route produced
    # close/re-entry churn and must remain research-only until a fresh, strict
    # full-V8 qualification explicitly promotes it.
    R1_DC_LOW4_3M_EMERGENCY_ENABLED: bool = False
    R1_REQUIRE_WT15_ADVERSE: bool = True
    TRADIER_EMERGENCY_ANTI_CHURN_GATES_ENABLED: bool = True
    R1_NEWBORN_WINDOW_MIN: float = 15.0            # kept for legacy; fixed-stop now active
    NEWBORN_DC_STOP_ENABLED: bool = True
    NEWBORN_DC_STOP_MAX_AGE_MIN: float = 20.0
    NEWBORN_DC_STOP_FIELD: str = 'dc_low4_5m'
    EMERGENCY_BRAKE_DC_STOP_ENABLED: bool = True
    EMERGENCY_BRAKE_DC_STOP_FIELD: str = 'dc_low_15m'
    # USER 2026-05-18: FROZEN ACTIVATION-TF STOP — per stocks team finding: frozen dc_low_4h@entry + -8% floor.
    # Worst-case stocks loss capped at -9% (vs -12.6% baseline / -17% intra-trade). 250 stops × 30 syms × 2.1yr = 4/sym/yr.
    FROZEN_ACTIVATION_STOP_ENABLED: bool = False  # 2026-06-02 USER MANDATE: OFF. Frozen-activation stop fires ~0% gain (48x in /history), is LIVE-ONLY (not in backtest) — a near-breakeven commission-burn exit, not a sanctioned technical exit. ROLLBACK: True.
    LONG_STRUCT_EXIT_TF: str = "D"
    SHORT_STRUCT_EXIT_TF: str = "15m"
    EOD_SLIM_RATIO_ENABLED: bool = False  # 2026-06-02 USER MANDATE: OFF. last_hour_balancing_loop EOD_SLIM_RATIO trim fires at any gain (incl ~0%), LIVE-ONLY rebalance not modeled in backtest. ROLLBACK: True.
    FROZEN_ACTIVATION_TF: str = "4h"
    FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER: float = -8.0
    LIVE_VEC_STALE_MARK_PRICE_ENABLED: bool = False
    LIVE_VEC_EMERGENCY_BRAKE_ENABLED: bool = False
    LIVE_VEC_QUARANTINE_STRATEGY_ENABLED: bool = False
    LR_PCTB_D_LONG_ENTRY_ENABLED: bool = False
    LR_PCTB_D_LONG_ENTRY_THRESHOLD: float = 0.20
    # 2026-07-15 grey-band long regression channel + graduated band/slope sizing (bt_band_bounce v2:
    # stocks grad sizing uplift +0.35..+1.04%/trade; long windows D_L200/4h_L400 >> 50)
    LR_CHANNEL_LONG_LENGTHS: dict = field(default_factory=lambda: {"1h": 200, "4h": 400, "D": 200})  # user spec D 6mo, 4h 1mo, 1h 1wk, 15m 1D stdev 2.5
    # STDEV SLOPE SIZING LADDER — the only proven way to beat B&H (user 2026-09-12)
    STDEV_SLOPE_SIZING_ENABLED: bool = True
    STDEV_BAND_MULTIPLIER: float = 2.5
    STDEV_SLOPE_SIZING_D_MAX: float = 10.0  # D 6mo 10x
    STDEV_SLOPE_SIZING_4H_MAX: float = 4.0  # 4h 1mo 4x
    STDEV_SLOPE_SIZING_1H_MAX: float = 2.0  # 1h 1wk 2x
    STDEV_SLOPE_SIZING_15M_MAX: float = 1.5  # 15m 1D 1.5x
    STDEV_SLOPE_LOOKBACK_D: int = 180
    STDEV_SLOPE_LOOKBACK_4H: int = 180
    STDEV_SLOPE_LOOKBACK_1H: int = 168
    STDEV_SLOPE_LOOKBACK_15M: int = 96
    BAND_SLOPE_SIZING_V2_ENABLED: bool = True      # sizing-only modifier on existing entries; validated on 305-sym bt_band_bounce v2
    BAND_SLOPE_SIZING_V2_TF: str = "D"
    BAND_SLOPE_SIZING_V2_DEPTH_GAIN: float = 1.0
    BAND_SLOPE_SIZING_V2_SLOPE_NORM_PCT_DAY: float = 1.0
    BAND_SLOPE_SIZING_V2_MIN: float = 0.5
    BAND_SLOPE_SIZING_V2_MAX: float = 2.5
    # 2026-07-15 LR_BAND swing-harvest strategy (bt_band_bounce v7: stocks D_L200 lo0.3 r2_0.7
    # h0.7 f0.25 ra0.3 L-only pool_sharpe 0.5949 / +15.75%/sym/yr, matches 0.58 baseline).
    # DEFAULT OFF — Tier-2 A/B required. Harvest/BE/readd knobs declared for the engine.
    # ═══ BAND LADDER (USER 2026-07-22) — a CONTINUOUS sizing ladder, not a threshold gate ═══
    # The previous LR_BAND_ENTRY only fired when pct_b <= LR_BAND_ENTRY_LO, i.e. only at the
    # extremes, which the user identified as structurally wrong. The ladder instead sizes EVERY
    # green arrow by where price sits between the regression bands (lrL_pct_b: 0 = lower band,
    # 1 = upper band). Every arrow opens if flat — not only band touches.
    #   pct_b < 0   (below lower band)  -> BELOW_BOTTOM_MULT (0 = ignore completely)
    #   pct_b = 0   (at lower band)     -> BOTTOM_MULT  (big: 3x normal)
    #   pct_b = 1   (at upper band)     -> TOP_MULT     (small: 0.3x normal)
    #   pct_b > 1   (above upper band)  -> ABOVE_TOP_MULT (1x normal)
    # MODE "linear"        interpolates BOTTOM->TOP across the channel.
    # MODE "center_plateau" holds BOTTOM_MULT from the CENTER down to the lower band, then
    #   cuts to BELOW_BOTTOM_MULT underneath it (the user's fallback if linear underperforms).
    # Escalation ladder to try in order: 3x/0.3x -> 5x/0.5x -> 10x/1x -> center_plateau.
    # ═══ BAND ARROW SYSTEM (USER 2026-07-23) — the whole strategy in two rules ═══
    # BUY every GREEN arrow (regression slope UP) on each entry TF, sized by the band ladder
    #   (band depth -> quantity: big at the lower band, small at the upper, 0 below it).
    # SELL when a RED arrow (regression slope DOWN) appears on any exit TF (D/4h, maybe 1h).
    # Accumulates across TFs and re-enters after every exit -> ~70% time in market, and with the
    # ladder multipliers the aim is >=5x b&h. Uses lrL_slope_{tf} (sign = arrow colour) and
    # lrL_pct_b_{tf} (position in the grey zone). NPZ has these for 1h/4h/D; 15m needs the
    # regression band precomputed before it can be added to ENTRY_TFS.
    # ═══ SWING SYSTEM (USER 2026-07-23) — the strategy that BEATS b&h BY CONSTRUCTION ═══
    # THE GUARANTEE: exit on a downtrend, and only ever RE-ENTER AT OR BELOW the exit price with
    # >= the shares sold. Then you hold the same position at a LOWER cost basis than buy-and-hold
    # -> you beat b&h mathematically. Every prior attempt lost because it re-entered at whatever
    # the signal said, which in an uptrend is usually ABOVE the exit -> re-buying higher = worse
    # than holding. The <=exit-price rule is what makes it work.
    #
    # EXIT: lower-low AND lower-high on the exit TF (downtrend structure confirmed).
    # RE-ENTER: on a green arrow OR higher-high+higher-low, sized by:
    #   price <= last exit  -> FULL prior size (or SWING_REENTER_MULT x) : strictly ahead of b&h
    #   price >  last exit  -> only START_POSITION_SIZE (minimal, so a runaway trend isn't missed,
    #                          at the cost of a tiny give-up vs the shares sold)
    SWING_ENABLED: bool = False
    SWING_EXIT_TFS: str = "D"                      # start with D only; test 4h then 1h after
    SWING_REENTER_SIGNAL: str = "green_or_hhll"    # green_arrow | hhll | green_or_hhll
    SWING_REENTER_AT_OR_BELOW_EXIT: bool = True    # THE GUARANTEE — do not disable lightly
    SWING_REENTER_TOLERANCE_PCT: float = 0.0       # allow re-entry up to this % ABOVE exit (0=strict)
    SWING_REENTER_MULT: float = 1.0                # size multiplier on the below-exit re-entry
    SWING_RUNAWAY_REENTER: bool = True             # re-enter at START_POSITION_SIZE if price ran away up
    BAND_ARROW_ENABLED: bool = False
    BAND_ARROW_ENTRY_TFS: str = "D,4h,1h"          # buy a green arrow on any of these
    BAND_ARROW_EXIT_TFS: str = "D,4h"              # sell a red arrow on any of these (test +1h)
    BAND_ARROW_ACCUMULATE: bool = True            # every green arrow adds while in-trend
    BAND_ARROW_MAX_POS_MULT: float = 30.0         # cap total exposure at N x START_POSITION_SIZE
    BAND_ARROW_SLOPE_DEADBAND: float = 0.0        # |slope| must exceed this to count as an arrow
    LR_BAND_LADDER_ENABLED: bool = False
    LR_BAND_LADDER_MODE: str = "center_plateau"   # linear | center_plateau (10x at centre and below)
    LR_BAND_LADDER_BOTTOM_MULT: float = 10.0      # at the lower band (scalar fallback)
    LR_BAND_LADDER_TOP_MULT: float = 3.0          # at the upper band (scalar fallback)
    LR_BAND_LADDER_ABOVE_TOP_MULT: float = -1.0   # <0 = use TOP_MULT ("3x at or above top")
    LR_BAND_LADDER_BELOW_BOTTOM_MULT: float = 0.0 # below the lower band = NO trade
    LR_BAND_LADDER_CENTER: float = 0.5            # plateau edge for center_plateau mode
    # Per-TF BOTTOM/TOP pairs (user ladder rule): D 10x->6x, 4h 6x->4x, 1h 4x->1x.
    # Each timeframe carries its own pair; a scalar cannot express these ratios. BOTTOM is
    # the multiplier at/below the channel centre, TOP is the multiplier at the upper gray band.
    # Continuous interpolation between the two (NOT a threshold gate — that was built wrong once
    # and the user called it "structurally wRONG"). Below the lower band = 0x, no trade.
    LR_BAND_LADDER_TF_BOTTOM: dict = field(default_factory=lambda: {"D": 10.0, "4h": 6.0, "1h": 4.0})
    LR_BAND_LADDER_TF_TOP: dict = field(default_factory=lambda: {"D": 6.0, "4h": 4.0, "1h": 1.0})
    # Shared research/ordinary parity adapter.  Defaults remain inert in live;
    # exact sweeps opt in explicitly.  When enabled, D/4h/1h completed parents
    # resolve to one strongest absolute target under the same $16k contract as
    # vector research (never repeated additive rung orders).
    LR_BAND_LADDER_ORDINARY_PARITY_ENABLED: bool = False
    LR_BAND_LADDER_TRIGGER: str = "union"          # green | structure | union
    LR_BAND_LADDER_BASIS: float = 0.5
    LR_BAND_LADDER_STOCH_EXTREME: float = 30.0
    LR_BAND_LADDER_BASE_UNIT_USD: float = 2000.0
    LR_BAND_LADDER_CAPACITY_USD: float = 16000.0
    # Completed-4h Donchian N=30 control paired with the parity ladder.  Kept
    # separate and default-off so importing this repair cannot alter live exits.
    LR_BAND_E02_EXIT_ENABLED: bool = False
    # Shared Bottom-A protective-trail state machine.  The vector-only
    # ``..._EXTENDED`` name is deliberately not a live/config switch; exact
    # recipes resolve its numeric parameters into this one default-off family.
    BOTTOM_A_PROTECTIVE_TRAIL_ENABLED: bool = False
    BOTTOM_A_PROTECTIVE_TRAIL_ARM_TIMEFRAME: str = "4h"
    BOTTOM_A_PROTECTIVE_TRAIL_TRAIL_TIMEFRAME: str = "5m"
    BOTTOM_A_PROTECTIVE_TRAIL_MODE: str = "STDEV"
    BOTTOM_A_PROTECTIVE_TRAIL_BREAK_BUFFER_ATR: float = 0.5
    BOTTOM_A_PROTECTIVE_TRAIL_DISTANCE_MULT: float = 1.0
    BOTTOM_A_PROTECTIVE_TRAIL_LOOKBACK: int = 6
    # Prepared direct-route envelopes.  These fields are data/config plumbing
    # only: every action switch stays false until a reviewed shared adapter is
    # installed in BOTH Tradier and V8.  Their values are set solely by a
    # selected full-recipe override; no ambient defaults are a route decision.
    COMPLETED_CANDLE_SNAPSHOT_DIRECT_ENABLED: bool = False
    ENTRY_STOCH_HHHL_DIRECT_ENABLED: bool = False
    ENTRY_STOCH_HHHL_DIRECT_TFS: list[str] = field(default_factory=lambda: ["1h"])
    ENTRY_STOCH_HHHL_DIRECT_MIN_CONFIRMING_TFS: int = 1
    ENTRY_STOCH_HHHL_DIRECT_STOCH_THRESHOLD: float = 20.0
    ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED: bool = False
    ENTRY_BOUNCE_DONCHIAN_DIRECT_TIMEFRAME: str = "5m"
    ENTRY_BOUNCE_DONCHIAN_DIRECT_DISTANCE: float = 0.008
    ENTRY_BOUNCE_DONCHIAN_DIRECT_RECOVERY_ONLY: bool = False
    ENTRY_BOUNCE_DONCHIAN_DIRECT_CONFIRMATION: str = "none"
    ENTRY_STOCH_PARENT_DIRECT_ENABLED: bool = False
    ENTRY_STOCH_PARENT_DIRECT_FAMILY: str = "ENTRY_1H_TURN_UP"
    ENTRY_STOCH_PARENT_DIRECT_THRESHOLD: float = 40.0
    ENTRY_STOCH_PARENT_DIRECT_TURN_DEFINITION: str = "rising-vs-prior"
    WT_DC_DIRECT_COMPLETED_ENABLED: bool = False
    WT_DC_DIRECT_THRESHOLD: float = 20.0
    WT_DC_DIRECT_HTF_GATE: str = "none"
    WT_DC_DIRECT_HTF_ALIGN_REQUIRED: int = 0
    WT_DC_DIRECT_COMBINED_STOCH_GATE: float = 100.0
    BB_RECOVERY_DIRECT_ENABLED: bool = False
    BB_RECOVERY_DIRECT_TIMEFRAME: str = "1h"
    BB_RECOVERY_DIRECT_BARS: int = 1
    BB_RECOVERY_DIRECT_MIN_EXCURSION_ATR: float = 0.5
    LONG_WAIT_DIRECT_ENABLED: bool = False
    LONG_WAIT_DIRECT_BOUNCE_TIMEFRAME: str = "15m"
    LONG_WAIT_DIRECT_BOUNCE_DISTANCE: float = 0.015
    LONG_WAIT_DIRECT_DEEP_K4H: float = 50.0
    LONG_WAIT_DIRECT_TURN_K1H: float = 40.0
    LONG_WAIT_DIRECT_CONFIRMATION: str = "stoch5"
    BOTTOM_B_DELAYED_LOWER_TOP_ENABLED: bool = True  # 2026-09-10 FIX vs B&H: wait for bottoms/lower-top confirmation. User: does not wait for bottoms. Hardened default.
    BOTTOM_B_DELAYED_LOWER_TOP_ARM_TF: str = "4h"
    BOTTOM_B_DELAYED_LOWER_TOP_CONFIRM_TF: str = "1h"
    BOTTOM_B_DELAYED_LOWER_TOP_REBOUND_ATR: float = 0.5
    BOTTOM_B_DELAYED_LOWER_TOP_PREBREAK_LOOKBACK: int = 20
    BOTTOM_B_DELAYED_LOWER_TOP_MAX_WAIT_1H: int = 12
    BOTTOM_B_DELAYED_LOWER_TOP_CONFIRMATION_MODE: str = "lower_top"
    BOTTOM_B_DELAYED_LOWER_TOP_CONFIRMATION_BARS: int = 1
    BOTTOM_B_DELAYED_LOWER_TOP_ARM_BREAK_MODE: str = "ATR"
    BOTTOM_B_DELAYED_LOWER_TOP_ARM_BREAK_THRESHOLD: float = 0.25
    MTF_ATR_MULTITF_DIRECT_ENABLED: bool = False
    MTF_ATR_MULTITF_DIRECT_TIMEFRAMES: list[str] = field(default_factory=lambda: ["1h", "4h", "D"])
    MTF_ATR_MULTITF_DIRECT_MULT: float = 1.5
    MTF_ATR_MULTITF_DIRECT_MIN_PROFIT_PCT: float = 0.5
    MTF_ATR_MULTITF_DIRECT_MIN_CONFIRMING_TFS: int = 1
    # Per-key production parity envelope for an exact selected recipe.  When
    # enabled, process_position permits only mandatory reclaim plus the shared
    # ordinary ladder while flat, and evaluate_stop permits only Bottom-A
    # while positioned.  The unconditional 4h Donchian safety close remains
    # outside this envelope.  Default-off means no existing live key changes.
    FULL_RECIPE_ONLY_ENABLED: bool = False
    LR_BAND_ENTRY_ENABLED: bool = False
    LR_BAND_ENTRY_TF: str = "D"
    LR_BAND_ENTRY_LO: float = 0.3
    LR_BAND_ENTRY_R2_MIN: float = 0.7
    LR_BAND_ENTRY_SIDES: str = "L"
    LR_BAND_HARVEST_HI: float = 0.7
    LR_BAND_HARVEST_FRAC: float = 0.25
    LR_BAND_HARVEST_ENABLED: bool = False          # 2026-07-19 USER band mandate: upper-band exit wired in tradier_manage (was dead knob); OFF until Tier-2 pack proof
    # Research/parity route: direct against-position 15m WT cross.  Kept
    # disabled by default; V8/vector comparison runs enable it explicitly.
    # 2026-09-11 FIX: True→False — META sold 8m after buy while RISING (+0.05%) via BEAR cross, bypassed hold and should not fire while rising even without hold.
    MTF_WT_CROSS_EXIT_DIRECT_ENABLED: bool = False
    LR_BAND_REGIME_ENABLED: bool = False           # 2026-07-20 USER: catch EVERY upswing — long anywhere below REGIME_MAX_PB while channel slope>0 (not only band touches)
    LR_BAND_REGIME_MAX_PB: float = 0.6
    LR_BAND_SLOPE_FLIP_EXIT_ENABLED: bool = False  # 2026-07-20 USER: channel slope flip → full PROFIT exit (loss exits stay R1/R2/HEDGE_FAILED)
    LR_BAND_SLOPE_FLIP_MIN_PCT_DAY: float = 0.05   # deadband: slope must be decisively negative (%/day), not just <=0 — bare zero-cross caused 60/80 MU churn exits at +0.0x%
    LR_BAND_SLOPE_FLIP_MIN_HOLD_MIN: float = 240.0 # and the position must have held this long first
    TRADIER_LONG_ONLY_ENTRIES: bool = False        # 2026-07-20 USER band mandate: skip SHORT entries entirely (shorts were squatting symbols and blocking LONG band entries via has_opposing_pos)
    LR_BAND_ENTRY_PRIORITY: bool = False           # 2026-07-20 USER: evaluate band/regime entry FIRST (was last in cascade → 0 fires on 3189 eligible ARM bars)
    LR_BAND_SIZE_DEPTH_GAIN: float = 1.0           # size law: deeper in channel = bigger
    LR_BAND_SIZE_SLOPE_GAIN: float = 1.0           # size law: steeper HTF slope = bigger
    LR_BAND_SLOPE_NORM_PCT_DAY: float = 0.3
    LR_BAND_SIZE_MAX: float = 3.0
    MTF_ARROW_ENTRY_ENABLED: bool = False          # 2026-07-20 USER multi-TF arrow system (lab-proven ARM 5.81x b&h sized); OFF until Tier-2 confirms
    # Short-side mirror of the multi-timeframe arrow path.  The implementation
    # in tradier_manage deliberately has its own opt-in because the long green
    # arrow and short red-arrow state machines are directional.  This field was
    # previously read by that implementation but absent from TradierConfig,
    # leaving the short path invisible to config inventories and impossible to
    # enable as a normal per-symbol/live setting (it only happened to work for
    # an explicit V8 override).  Default-off preserves live behaviour.
    MTF_ARROW_SHORT_ENTRY_ENABLED: bool = False
    MTF_ARROW_THETA: float = 0.3                   # entry gate on the weighted HTF band-depth+slope score
    MTF_ARROW_SIZE_GAIN: float = 1.0               # size = 1 + gain*score (deeper HTF + steeper slope = bigger)
    MTF_ARROW_SIZE_MAX: float = 4.0
    MTF_ARROW_SLOPE_LAMBDA: float = 1.0            # weight of the slope term vs depth term
    MTF_ARROW_CONFIRM_PCT: float = 2.0             # 5m green-arrow confirm: entry only when price reverses >= this % off the running low (lab phase_b)
    MTF_ARROW_TRAIL_EXIT_ENABLED: bool = False     # lab-faithful exit: close when price retraces CONFIRM_PCT off running high; loss-closes need MTF_ARROW_TRAIL in the noloss bypass list (pack-scoped)
    MTF_ARROW_SLOPE_NORM_PCT_DAY: float = 0.3
    MTF_ARROW_WEIGHTS: dict = field(default_factory=lambda: {"1h": 0.35, "4h": 0.35, "D": 0.30})
    LR_BAND_READD_LO: float = 0.3
    LR_BAND_BE_RATCHET: bool = True
    LR_BAND_EXIT_EXEMPT: bool = True
    R1_USE_DC_4BAR: bool = True                    # 2026-07-01 wired (was DEAD) + A/B-testable. STOCKS STAY 4-bar dc_low4_5m pending A/B — 20-bar proven for crypto only, unproven on $70k. True=4-bar, False=20-bar dc_low_5m.
    R1_TF: str = '5m'                              # tradier base TF
    # Backtest DC stop loss sweep flags (tradier uses 5m TF):
    DC_LOW4_STOP_ENABLED: bool = False            # 2026-07-08 GAINMO triage: True→False — armed %-class stop in a no-stop-loss system, undated flip, also silently changed the backtest baseline (backtest_v8_engine reads this)
    DC_LOW_STOP_ENABLED: bool = False             # 2026-07-08 GAINMO triage: True→False — same reason as DC_LOW4_STOP_ENABLED
    # Generalized frozen stop (engine-level; backcompat: DC_LOW_4H_FROZEN_STOP_ENABLED still works)
    DC_LOW_FROZEN_STOP_ENABLED: bool = True       # RECONNECT 2026-09-01 user mandate: dc_low_4h/dc_high_4h ON by default, per_sym can only override (was False)       # master switch; sweep variants set True + TF
    DC_LOW_FROZEN_STOP_TF: str = '4h'             # TF to freeze: '5m','15m','1h','4h','D' (D added 2026-07-18 — engine reads dc_low_{tf} generically, NPZ has D)
    DC_LOW_FROZEN_STOP_USE_4BAR: bool = False      # True=dc_low4_{tf} (4-bar tight), False=dc_low_{tf} (20-bar)
    DC_LOW_FROZEN_STOP_FLOOR_PCT: float = -999.0  # abs loss floor; -999 = off
    BB_FROZEN_STOP_ENABLED: bool = False           # freeze bb_lower/upper/basis at entry as stop
    BB_FROZEN_STOP_TF: str = '1h'                 # TF to freeze: '3m','5m','15m','1h','4h','D','W' (D/W added 2026-07-18 — engine reads bb_{field}_{tf} generically, NPZ has D+W)
    BB_FROZEN_STOP_FIELD: str = 'lower'           # 'lower' (LONG stop), 'upper' (SHORT stop), 'basis' (both)
    DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED: bool = False  # hedge instead of stop if GR score >= min_tfs x min_ind against
    DC4_STOP_GR_SCORE_MIN_TFS: int = 3
    DC4_STOP_GR_SCORE_MIN_IND: int = 5
    DUP_GUARD_GAIN_MULTIPLIER: float = 0.5
    DUP_GUARD_USE_GAIN_GATE: bool = True
    # USER 2026-05-29 (mirror of config.py): reentry/augment must NOT be blocked by MTF
    # armed-state filter or HTF_TREND_VETO. Augment guaranteed at bounce >= 0.5*MIN_GAIN.
    # Reentry guaranteed. Fresh OPENs still gated. ROLLBACK: set False.
    GUARANTEED_REENTRY_AUGMENT_ENABLED: bool = False
    # WRONG_SIDE_ABS_KILL — stocks mirror crypto v2 (K irrelevant, divergence confirms reduced threshold).
    # 2026-04-25 rapid-grid HVC sweep (114-sym): WS_KILL_on=0.407 Sharpe (-2.03 vs baseline, 19.1% DD). CATASTROPHIC.
    # Stocks are mean-reverting — cutting on WT against destroys recovery edge. NEVER enable for tradier.
    WRONG_SIDE_ABS_KILL_ENABLED: bool = False  # 2026-07-08 GAINMO triage: the at-floor evidence (114-sym sweep, Sharpe -2.03, header above: 'NEVER enable for tradier') outweighs the unsampled 'Phase 6 winner' note; loss-close path outside the sanctioned R1/R2/HEDGE_FAILED trio
    WRONG_SIDE_MIN_AGE_MIN: float = 30.0
    WRONG_SIDE_WT_TFS_REQUIRED: int = 4  # 2026-04-26: 4-of-5 (Phase 6 winner)
    WRONG_SIDE_WT_TFS_REDUCED: int = 3
    WRONG_SIDE_DIV_TFS_REQUIRED: int = 1  # 2026-04-26: ≥1 div confirms (lowered from 2)
    WRONG_SIDE_DIV_LOOKBACK_BARS: int = 20
    WRONG_SIDE_K_TFS_REQUIRED: int = 0
    # HEDGE_ENTRY_MODE (shared semantics with crypto).
    HEDGE_ENTRY_MODE: str = "LOSS_AND_WT"
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
        try:
            curr_dir = Path(__file__).resolve().parent
            if curr_dir.exists() and any(name in curr_dir.name.lower() for name in ("binance", "sandbox")): return curr_dir
        except Exception: pass
        system = platform.system()
        home = Path.home()
        candidates = [home / "Documents" / "binance", Path("/home/niels/binance-sandbox"), home / "binance", Path("/binance")]
        for p in candidates:
            if p.exists(): return p
        return home / "binance"

    AVAILABLE_IPS: List[str] = field(default_factory=lambda: ["5.75.211.216", "49.13.32.80",  "157.180.125.52" ])
    
    ENABLE_IP_ROTATION: bool = False  # NOT DEAD_CONFIRMED (priority 15/100) — no plausible wiring site found 20260416

    ACCOUNT_KEYS = ['tra','trb','trc']
    BASE_PATH: Path = _resolve_base_path()
    DATA_DIR: Path = BASE_PATH / "data" / "tradier"
    KLINES_CACHE_DIR: Path = BASE_PATH / "klines_cache" / "tradier"
    SYMBOLS_FILE: Path = BASE_PATH / "symbols_tradier.json"
    TRADIER_SYMBOLS_FILE: Path = BASE_PATH / "symbols_tradier.json"
    # 2026-04-27 user rule: indicators max 1 min stale. Narrow universe + bump parallelism.
    # 270 syms × 7 TFs × cycle_sem=4 → ~12 min. Narrow (~120, watchlist + positions) +
    # cycle_sem=24 + http=48 + idle=1s → expected <60s. Flip NARROW=False to revert.
    TRADIER_INDICATORS_NARROW_UNIVERSE: bool = True
    TRADIER_INDICATORS_CYCLE_CONCURRENCY: int = 24       # was 4
    TRADIER_INDICATORS_HTTP_CONCURRENCY: int = 48        # was 15
    TRADIER_INDICATORS_IDLE_SLEEP_SEC: float = 1.0       # was 15
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
    PARITY_COMPARISON_MODE: bool = False  # 2026-06-02 USER MASTER SWITCH. Default False = NORMAL LIVE (all gain-augmenting entry strategies ON: Rotation/RSI2/GapFill/ORB/EP/Clenow/SMFI/Minervini/Connors/FH_Momentum). Set True ONLY during a live↔backtest parity A/B — it suppresses ALL those augmenting strategies AT ONCE in live (gate: tradier_manage.py _rotation_rsi2_loop ~15360) so live == backtest is apples-to-apples. AFTER confirming parity, set back to False to resume gains. These strategies augment live gains but are NOT fully in the vec/Tier-2 sweep, so they must be flipped off only for the comparison window, never left off.
    ROTATION_ENABLED: bool = True
    ROTATION_TOP_N: int = 3       # URGENT_FIX: fewer long positions in bear market (was 5)
    ROTATION_BOTTOM_N: int = 8   # URGENT_FIX: more short candidates (was 5)
    ROTATION_HOLD_DAYS: int = 7  # BACKTEST_CHANGE_T44 was 5 → 7 longer hold
    ROTATION_LOOKBACK_DAYS: int = 10  # 10-day return lookback (5yr optimal, was 3)
    ROTATION_POSITION_SIZE: float = 1200.0  # BACKTEST_CHANGE_T28 was 800 → 1200 larger rotation size
    ROTATION_SMA200_FILTER: bool = True  # BACKTEST_CHANGE_T47 filter rotation candidates by SMA200
    # === 2026-09-03 HARD SHORT GATES — baked into function, not toggleable (see tradier_manage evaluate_rotation_entry + _check_dc_break + WT_DC) ===
    ROTATION_S_FINAL_SCORE_MAX: float = 0.35  # LT trend: SHORT only if final_score_norm_lt < 0.35 (weak/ bearish). SNDK was strong uptrend.
    ROTATION_S_WT_BEAR_ALIGN_MIN: int = 2  # HTF bear alignment: need >=2 of 1h/4h/D wt1<wt2
    ROTATION_S_K5M_MIN: float = 20.0  # exhaustion: SHORT only if k5m >=20 (not rolling over at bottom)
    ROTATION_S_DC_POS_MIN_D: float = 0.10  # closeness to bottom: SHORT only if dc_pos_D >=0.10 (not at low band)
    ROTATION_S_RSI_MIN_D: float = 25.0  # exhaustion: SHORT only if rsi_D >=25
    ROTATION_S_RET_EXHAUSTED_PCT: float = 0.25  # if |ret_nd| >25% and k5m<30, skip (exhausted loser bounce risk)
    DC_BREAK_LOW_DC_POS_MIN: float = 0.15  # closeness: must be >=0.15 above low (not deep oversold)
    DC_BREAK_LOW_K5M_MIN: float = 15.0  # exhaustion floor for DC break shorts
    DC_BREAK_LOW_RSI_MIN: float = 25.0  # rsi15m floor
    DC_BREAK_LOW_FINAL_SCORE_MAX: float = 0.45  # LT trend gate: only when weak
    DC_BREAK_LOW_HTF_ALIGN_MIN: int = 2  # hard HTF bear align (replaces toggleable DC_BREAK_LOW_REQUIRE_HTF_*)
    WT_DC_K5M_MIN_SHORT_HARD: float = 20.0  # hard threshold when enabled
    WT_DC_K5M_HARD_ENABLED: bool = False  # 2026-09-03: npz has no 5m, default OFF for parity/forward comparison (no 1m/5m trading). When False, K5M hard gate disabled in live + v12. Turn True to reintroduce.
    WT_DC_DC_POS_MIN: float = 0.20  # closeness to bottom
    WT_DC_FINAL_SCORE_MAX: float = 0.40  # LT trend
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

    # === GAP_RISK_EXIT (intraday gap structure risk — ENABLED by default 2026-09-08: QBTS/losers gap continuation trap) ===
    # Short gap-up: open_D > close_D_prev. Long gap-down: open_D < close_D_prev.
    # Retraces to prev_close (fill touch), then continuation risk: close when price
    # re-breaks open in gap direction OR makes higher-high (short) / lower-low (long)
    # after retrace. Intraday structure-risk exit, NOT take-profit. Fully switchable.
    # Live uses open_D / close_D_prev + stateful retrigger; vector is vec_decisions.
    GAP_RISK_EXIT_ENABLED: bool = True  # ENABLED by default — prevents losers like QBTS gap continuation
    GAP_RISK_EXIT_SHORT_ENABLED: bool = True  # ENABLED — short gap-up
    GAP_RISK_EXIT_LONG_ENABLED: bool = True  # ENABLED — long gap-down
    GAP_RISK_EXIT_OPEN_RECLAIM_ENABLED: bool = True  # COND_A alias — open reclaim — ENABLED
    GAP_RISK_EXIT_STRUCTURE_BREAK_ENABLED: bool = True  # COND_B alias — structure break HH/LL — ENABLED
    GAP_RISK_EXIT_COND_A_ENABLED: bool = True  # alias for OPEN_RECLAIM — ENABLED
    GAP_RISK_EXIT_COND_B_ENABLED: bool = True  # alias for STRUCTURE_BREAK — ENABLED
    # === GAP_RISK_REENTRY (gap close reentry — flag remains after reduce/close, reenters when gap fills) ===
    # Gaps close in days as trend continues — after GAP_RISK_EXIT reduce/close, flag remains for reentry when price comes back to prev_close/gap fill.
    GAP_RISK_REENTRY_ENABLED: bool = True  # ENABLED by default — gap exit remains flagged for reentry on gap fill
    GAP_RISK_REENTRY_ON_FILL: bool = True  # reenter when price returns to prev_close (gap fill) — fills in days
    GAP_RISK_REENTRY_MAX_DAYS: int = 5  # keep flagged 5 days (gaps usually close within days)
    GAP_RISK_REENTRY_SIZE_PCT: float = 100.0  # size of reentry vs original (100% = full)
    GAP_RISK_REENTRY_REQUIRE_TREND: bool = False  # if True, require trend still in original gap direction to reenter
    # === GAP_INVENTORY + PRE-CLOSE / MORNING REENTRY (2026-09-09: harvest overnight gaps) ===
    # Tradier has no commissions — exiting on intraday tops (vv) / bottoms is free.
    # Danger is open/close gaps: inventory tracks sum(% open<close) vs sum(% open>close).
    # Sheet logic (SPREADSHEETS/TEMPLATE.xlsx gap sheet): sum pos gaps and sum neg gaps over
    # last month (20d) → bias = sum_pos + sum_neg. Longs close before close at any small
    # top in last 90min when bias negative (avg open down); shorts v.v. Reenter next morning
    # first opportunity if trend still favorable. This sentinel IS vectorized via NPZ
    # open_D/close_D_prev per-symbol; portfolio bias aggregated market-wide live.
    GAP_INVENTORY_ENABLED: bool = True  # track cumulative gap sums to data/gap_inventory_tradier.json
    GAP_INVENTORY_FILE: str = "data/gap_inventory_tradier.json"
    GAP_INVENTORY_LOOKBACK_DAYS: int = 20  # rolling window for bias (20 trading days)
    # Per-symbol gap inventory (ONLY source for 90m sentinel — market-wide bias deprecated 2026-09-11 per user, retuned 2026-09-11 to 0.10 per user: |avg|>0.10 closes)
    GAP_PER_SYMBOL_INVENTORY_FILE: str = "data/gap_inventory_tradier_per_symbol.json"
    GAP_PER_SYMBOL_HISTORY_FILE: str = "data/gap_history_1yr_tradier.json"  # historic >1yr date-by-date per-symbol gaps for backtest (>252 trading days)
    GAP_PER_SYMBOL_AVG_THRESH_PCT: float = 0.10  # |avg_gap| <= this = near 0 → don't close on gap bias (only VV). User 2026-09-11: anything >0.10 avg/day closes at top/bottom
    GAP_PER_SYMBOL_LOOKBACK_DAYS: int = 30  # informational — file stores 30d of daily gaps
    # Pre-close sentinel window: poll every 2m from 90m before close (14:30 ET) until
    # 10m before close (15:50 ET); exit at small top/bottom if bias says close. Force MOC
    # at 15:59 if still not exited (gap safety fallback).
    GAP_MOC_EXIT_ENABLED: bool = True  # exit before close per inventory bias
    GAP_MOC_WINDOW_MINUTES: int = 90  # start trimming window (90m before close = 14:30 ET)
    GAP_MOC_EXIT_MINUTES_BEFORE_CLOSE: int = 10  # hard MOC deadline (15:50 ET)
    GAP_MOC_REQUIRE_TOP: bool = True  # only exit longs at small top (WT down / HA flip), shorts at bottom
    GAP_MOC_FORCE_MOC_AT_CLOSE: bool = True  # force MOC at deadline even if no top (gap safety)
    GAP_MORNING_REENTRY_ENABLED: bool = True  # re-enter first hours if trend still right
    GAP_MORNING_REENTRY_MINUTES_AFTER_OPEN: int = 90  # 09:30-11:00 ET window
    GAP_MOC_HOLD_POSITIVE_BIAS_PCT: float = 0.30  # if sum_pos_gap - sum_neg_gap >0.30% keep open hoping for pos gap (unless dc_4h_high danger)
    # Safety: never hold overnight close to dc_4h_high when wt_15 or 1h pointing down
    GAP_MOC_DC_WT_SAFETY_ENABLED: bool = True
    GAP_MOC_DC_PROXIMITY_PCT: float = 0.50  # within 0.50% of dc_4h_high = "close to high"
    # Reentry sizing mirrors crypto: +25% after profitable gap exit
    GAP_MOC_REENTRY_SIZE_MULT: float = 1.25
    # === INTRADAY L/S RATIO REBALANCE — LIVE-ONLY PORTFOLIO GATE (NON-VECTORIZABLE) ===
    # Enforces portfolio long/short ratio = f(market sentiment) throughout the day by
    # slimming down the overweight-side underperformers at favorable intraday tops/
    # bottoms. Vector engines set PARITY_DISABLE_NON_VECTORIZABLE=True so this never
    # fires in backtest (no portfolio to measure). Live stays ON.
    INTRADAY_RATIO_REBALANCE_ENABLED: bool = True  # LIVE-ONLY (see BACKTEST_REPLICA_SWITCHES §16)
    INTRADAY_RATIO_DEVIATION_THR: float = 0.10  # |actual_long - target_long| >10% triggers trim
    INTRADAY_RATIO_CHECK_INTERVAL_MIN: int = 15  # check every 15m during market hours
    INTRADAY_RATIO_TRIM_FRAC: float = 0.30  # trim 30% of worst performer on overweight side
    INTRADAY_RATIO_REQUIRE_TOP: bool = False  # 2026-09-11 EMERGENCY falling market: True→False — longs falling quickly never at small top, intraday trim never fired, account draining 10/1 long heavy. Allow trim at any price.
    INTRADAY_RATIO_COOLDOWN_MIN: int = 30  # don't re-trim same symbol within 30m
    INTRADAY_RATIO_MAX_TRIMS_PER_DAY: int = 8  # safety cap

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
    # USER 2026-05-06 (ZECUSDC missed-trend mirror for tradier): PARABOLIC PROTECTION —
    # bypass WT_CROSSUNDER_FINAL/WT_CROSSOVER_FINAL/WT_DC_EXIT closes + REENTRY_MONITOR HTF
    # K-extreme blocks when extreme momentum confluence is on (rsi_4h + rsi_1h + bb_pct_b_4h).
    # Mirrors the crypto patch verbatim — see config.py 2026-05-06 ZECUSDC block.
    PARABOLIC_PROTECTION_ENABLED: bool = True
    PARABOLIC_RSI_4H_MIN: float = 70.0
    PARABOLIC_RSI_1H_MIN: float = 65.0
    PARABOLIC_BB_PCT_B_4H_MIN: float = 0.90
    PARABOLIC_RSI_4H_MAX: float = 30.0
    PARABOLIC_RSI_1H_MAX: float = 35.0
    PARABOLIC_BB_PCT_B_4H_MAX: float = 0.10
    EXTREME_OB_OS_OVERRIDE_ENABLED: bool = True
    EXTREME_OB_RSI_4H_MIN: float = 80.0
    EXTREME_OB_RSI_D_MIN: float = 75.0
    EXTREME_OB_BB_PCT_B_4H_MIN: float = 1.0
    EXTREME_OS_RSI_4H_MAX: float = 20.0
    EXTREME_OS_RSI_D_MAX: float = 25.0
    EXTREME_OS_BB_PCT_B_4H_MAX: float = 0.0
    WT_CROSSUNDER_FINAL_ENABLED: bool = True  # T25 2026-04-14: True=0.357 vs False=0.363 (Δ=0.006) — essentially noise. Keeping True for live WT exit coverage.
    ATR_TRAIL_2X_EXIT_ENABLED: bool = False  # BACKTEST_CHANGE_T58: was True. ATR trail = #1 stock PnL destroyer (-2557% cumulative). Disabled.
    STOCH_CROSS_1H_EXIT_ENABLED: bool = False  # BACKTEST_CHANGE_T17 stoch cross on 1h triggers exit
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
    NOLOSS_MIN_PROFIT_PCT_TRADIER: float = 0.01  # 2026-07-11 MU forensics: 0.0 let DELTA_EXIT/technical-flip paths close LOSERS (MU -3.55% closes, June ledger) in violation of the exit mandate (only R1/R2/HEDGE_FAILED may close at a loss). 0.01 = flips exit winners only; losers go to R1/R2 (which did not exist when the 2026-04-25 KILL set 0.0). ROLLBACK: 0.0.
    # === BB RECOVERY-TO-ENTRY EXIT BYPASS (2026-04-15, stocks) ===
    # When True: if entry_price > bb_high_1h (LONG) or < bb_low_1h (SHORT),
    # AND current 3m close has recovered within tolerance of entry_price,
    # AND current 3m bar shows reversal, ALLOW close at loss (bypass NOLOSS gate).
    # Defaults OFF — sweep first.
    BB_RECOVERY_EXIT_ENABLED_TRADIER: bool = False  # 2026-04-20 sweep: unlocks stranded positions stuck above bb_1h
    BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER: float = 0.30  # stock pct tolerance around entry_price
    BB_RECOVERY_EXIT_TOLERANCE_ATR_MULT_TRADIER: float = 0.0  # if >0, uses N * atr_3m instead of pct
    # wt_D bounce augment — add to losing position when daily WT turns, bypasses gain gates
    WT_D_BOUNCE_AUG_ENABLED: bool = False  # 2026-04-23 EMERGENCY: disabled — was augmenting losers at gain<0. Best 2.8155 config has this False.
    WT_D_BOUNCE_AUG_MULTIPLIER: float = 2.0  # 2026-04-20: 2x (add 1x to existing) per user directive. Was 4x.
    WT_D_BOUNCE_AUG_REQUIRE_HIGHER_WT: bool = True  # 2026-04-20: require bounce WT > last aug WT (was False)
    WT_D_BOUNCE_AUG_REQUIRE_HIGHER_PRICE: bool = True  # 2026-04-20: require bounce price > last aug price (higher low for LONG)
    WT_D_BOUNCE_AUG_COOLDOWN_HOURS: float = 1.0  # min hours between wt_D augments per position
    WT_D_BOUNCE_DD_STOP_ENABLED: bool = True  # 2026-04-20: cut extra DD leg if price continues below aug price
    K_ZONE_ENTRY_ENABLED_TRADIER: bool = False  # BACKTEST_CHANGE_T52: K-zone entry — K in zone + turning + candle confirms. No crossover wait.
    K_ZONE_VETO_ENABLED_TRADIER: bool = False  # VARIANCE_FIX 2026-04-14: when True, K_ZONE_LONG/SHORT_THRESHOLD veto entries on wt_dc path (proves switch gates trades). Default False = live unchanged.
    WT_COMPOSITE_VETO_ENABLED_TRADIER: bool = False  # VARIANCE_FIX 2026-04-14: when True, WT_COMPOSITE_SCORING_ENABLED vetoes wt_dc entries lacking composite alignment. Default False = live unchanged.
    MI_EXIT_VETO_ENABLED_TRADIER: bool = False  # SENTINEL_FIX 2026-04-14: when True, MI_EXIT_ENABLED_TRADIER actually gates exits. Default False = live unchanged.
    WT_EXIT_VETO_ENABLED_TRADIER: bool = False  # SENTINEL_FIX 2026-04-14: when True, WT_EXIT_MIN_TFS_TRADIER actually gates exits. Default False = live unchanged.
    DC_ENTRY_VETO_ENABLED_TRADIER: bool = False  # SENTINEL_FIX 2026-04-14: when True, DC_POSITION_ENTRY_THRESHOLD gates entries (require dc_pos in zone). Default False = live unchanged.
    K_ZONE_LONG_THRESHOLD_TRADIER: int = 35   # S1_SWEEP_2026-04-15: 35 top S1 cfg Sharpe=4.23 on 20605 trades (was 80)
    K_ZONE_SHORT_THRESHOLD_TRADIER: int = 65  # S1_SWEEP_2026-04-15: 65 top S1 cfg Sharpe=4.23 on 20605 trades (was 20)
    K_ZONE_ENTRY_BONUS_TRADIER: int = 20  # 2026-04-08 SWEEP: 20 → Sharpe 11.12 vs 25 → 6.92 (+61%). Biggest single config win.
    BOUNCE_REENTRY_ENABLED_TRADIER: bool = False  # BACKTEST_CHANGE_T53: After profitable exit, K must reset to zone before reentry.
    BOUNCE_REENTRY_K_RESET_LONG_TRADIER: int = 35  # 2026-04-26 WIRED — tradier_manage.py:5449 (new K-reset bounce branch in evaluate_reentry, conviction 75) + 8343 (REENTRY_MONITOR k_5m gate, was hardcoded 50). Previously DEAD_CONFIRMED (priority 90).
    BOUNCE_REENTRY_K_RESET_SHORT_TRADIER: int = 65  # 2026-04-26 WIRED — tradier_manage.py:5450 + 8344. SHORT mirror. Previously DEAD_CONFIRMED (priority 90).
    # ═══ SAFETY SWITCHES (2026-04-16 audit) ═══
    TRADIER_REQUIRE_TRADEABLE_KEY: bool = True     # Gate entry at execute_now if not in tradeable_keys
    TRADIER_RATIO_REQUIRE_MIN_GAIN: bool = False   # Block RATIO_BOOST on positions with gain < min
    TRADIER_RATIO_BOOST_MIN_GAIN_PCT: float = 1.0  # Min gain for ratio boost to fire
    TRADIER_REENTRY_OVERDUE_BYPASS_ENABLED: bool = False  # True=legacy (bypass after 48h); False=always enforce stoch
    TRADIER_NOLOSS_SRS_BYPASS: bool = True          # True=SRS reason bypasses NOLOSS; False=no reason bypass
    # === TWO-TIER MANDATORY REENTRY — STOCKS (BC_155) ===
    REENTRY_TIER1_SIZE_MULT_TRADIER: float = 1.5  # 2026-04-26 WIRED — tradier_manage.py:5561 applies this mult to base_qty cap (was hardcoded 1.5). Tier 1: % of closed qty. Default 1.5 preserves prior behavior. Previously DEAD_CONFIRMED (priority 70).
    REENTRY_TIER2_SIZE_MULT_TRADIER: float = 0.8  # Tier 2: 80% of closed qty
    REENTRY_TIER2_PRICE_PCT_TRADIER: float = 0.003  # 0.3% price move triggers Tier 2
    # 2026-05-30 USER: dip/breakout/extended reentry sizing tiers (mirror of crypto config.py:537-540, consumed at ez_manage.py:34204). PENDING wiring into tradier_manage reentry sizing (apply _re_size_mult to reentry qty using current_price vs reentry_level + k_1h).
    REENTRY_SIZE_DIP_MULT: float = 2.0         # 2026-05-30 USER GO-LIVE: dip (k_15<30) → 200% REENTER BIGGER
    REENTRY_SIZE_BREAKOUT_MULT: float = 1.5    # 2026-05-30: ~same price (continuation) → 150%
    REENTRY_SIZE_EXTENDED_MULT: float = 1.0    # 2026-05-30: k_1h>90 extended → 100% (was 0.5 — never shrink runner). NOTE: stocks still PENDING wiring into tradier_manage reentry sizing.
    REENTRY_SIZE_EXTENDED_K1H: float = 90.0    # 2026-05-30: 95→90
    # === HTF-REGIME hold + scale-in-at-bottoms (2026-05-30 USER GO-LIVE machinery — DEFAULT OFF) ===
    # Stocks mirror of config.py block. Ladder defaults to 1h/4h/D (stocks lack 3m/15m WT). Master switch is
    # a KILL SWITCH defaulting OFF; a stock trades under this ONLY if marked tradeable in the per_sym HTF
    # ledger (4yr gate: x_bh>=5 AND net pool_sharpe>=0.25 AND beats baseline). See PERSYM_VALIDATION_MAP.
    HTF_REGIME_ENABLED: bool = False            # MASTER KILL SWITCH — default OFF
    HTF_REGIME_LEDGER_PATH: str = "data/_diagnostic/persym_htf_ledger_full.json"
    HTF_REGIME_TF: str = "D"
    HTF_REGIME_EXIT_TF: str = "1h"             # stocks: 1h tight-cut (markets closed most of day)
    HTF_REGIME_SCALE_IN: bool = True
    HTF_REGIME_ADD_MULT_PER_SMA: float = 0.75
    HTF_REGIME_SIZE_CAP: float = 3.0
    HTF_REGIME_VOL_TARGET: float = 0.0
    REENTRY_TIER2_MIN_MINUTES_TRADIER: float = 10.0  # Min minutes before Tier 2
    REENTRY_TIER2_MAX_MINUTES_TRADIER: float = 120.0  # Force entry after 120min
    # RALLY REENTRY GATE (0-3h after exit): k5m+k15m rising + HTF WT aligned
    # REENTRY_RALLY_K15M_MAX: additional k15m level cap — 100=disabled, 40=moderate, 20=strict oversold. 2026-04-25 rapid-grid: K60+gap2 = +0.007 Sharpe +1 trade (marginal, not promoted). K40/50 neutral. K gate not the binding constraint for tradier trade count.
    # REENTRY_RALLY_HTF_MIN: min HTF TFs (1h/4h/D) aligned — 1=loose, 2=default, 3=strict
    REENTRY_RALLY_K15M_MAX: float = 100.0# sweep: 100 (off) / 40 / 20
    REENTRY_RALLY_HTF_MIN: int = 1          # 2026-09-11 EMERGENCY falling market: 3→1 — 3 HTF required blocked all 4 shorts below exit (MCD/NOC/LMT/AXON) as RALLY_HTF_GATE htf1<3. Was 3 (was 2).
    TRADIER_REENTRY_HARDCOOL_MIN: float = 15.0  # 2026-09-10 REENTRY FIX STOCKS: 30→15min (crypto proven 300s/5min recaptures continuations). Stocks need slightly longer than crypto but 30 blocked valid 15-25m bounces (REENTRY_PENDING logs 30m overdue). 15 keeps churn guard (PRICE_CROSS_BACK 0.3% + WT2of3) but doubles reentry surface. ROLLBACK: 30.0
    REENTRY_STOCH_K_MAX_LONG: float = 80.0  # 2026-09-10 STOCKS ALIGN CRYPTO: was default 40 via fallback (over-strict). 80 restores SAFE single-WT path
    REENTRY_STOCH_K_MIN_SHORT: float = 20.0
    REENTRY_GOLDEN_BLOCK_ENABLED: bool = False  # 2026-09-10 STOCKS: golden DC-high block was killing trending reentries (price>dc_high 1h). OFF for reentry — trend continuation should reenter even if extended; HTF WT still protects
    TRADIER_REENTRY_ANTI_CHURN_ENABLED: bool = False  # 2026-05-10 USER MANDATE: REENTRY guaranteed — ANTI_CHURN_exit_score gate was blocking reentries when wt_dc still indicated exit. Default OFF.
    TRADIER_REENTRY_RZ_BLOCK_ENABLED: bool = False    # 2026-05-10 USER MANDATE: REENTRY guaranteed — RZ_BLOCK_REENTRY_LONG_AT_TOP / SHORT_AT_BOTTOM gate was blocking reentries via DELTA zone. Default OFF.
    # P2-D: REENTRY_BREAKOUT — re-enter after DC break, structural stop if price falls back through breakout level
    REENTRY_BREAKOUT_ENABLED: bool = False  # P2-D: exit if price crosses back through the DC level that triggered the reentry
    # MINIMUM HOLD TIME — prevents churning/death-by-1000-cuts on stocks
    MIN_HOLD_MINUTES_TRADIER: float = 30.0  # No exits before 30 min. Bypassed only if loss > -5%. ; WIRED 2026-04-16 (priority 90/100) — tradier_manage.py:3891 stock min hold fallback
    # MULTI-TF EXIT CONFIRMATION — exits must mirror entry strength
    # Entry needs multi-TF WT alignment → exit needs multi-TF WT disalignment
    # Prevents 5m noise from killing positions that 15m/1h/4h still support
    MIN_EXIT_TF_AGAINST_TRADIER: int = 3  # 2026-04-26: 3 TFs against (was 2) — fewer false exits
    # BOUNCE-TOP EXIT — V4 backtest proven: Sharpe -0.5 → +0.42 on 121 stocks 2yr
    # Exits losing positions at the TOP of a bounce (not the bottom like a stop loss).
    # Mandatory reentry follows: 150% at pullback, 200% at rising WT cross.
    BOUNCE_TOP_EXIT_ENABLED: bool = False  # KILLED 2026-03-30: percentage stop loss in disguise. Exits ONLY on technicals. ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    BOUNCE_TOP_MIN_HOLD_MINUTES: float = 1440.0  # 24h min hold before bounce exit eligible
    BOUNCE_TOP_MIN_LOSS_PCT: float = -3.0  # Only fires when loss is between -3% and -50%
    BOUNCE_TOP_MAX_LOSS_PCT: float = -50.0  # Don't exit positions beyond -50% (too late)
    BOUNCE_TOP_REENTRY_MULT: float = 1.5  # 2026-04-26 WIRED — tradier_manage.py:5525, 5544 (divergence reentry size mult, was hardcoded 1.5). Previously DEAD_CONFIRMED (priority 70).
    BOUNCE_TOP_RISING_CROSS_MULT: float = 2.0  # 2026-04-26 WIRED — tradier_manage.py:5531, 5550 (rising/falling WT cross reentry size mult, was hardcoded 1.5; raised default to 2.0 to match config intent). Previously DEAD_CONFIRMED (priority 70).
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
    STOCH_CROSS_ENTRY_TRADIER: bool = True  # BACKTEST_CHANGE_T59: was True. Stoch crossover = noise on daily bars. RSI(10) is the real entry.
    AUGMENT_PYRAMID_TRADIER: bool = False  # BACKTEST_CHANGE_T60: Pyramiding barely fires on stocks (0-10 trades). Disabled. ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    # === BEAR MARKET MODE ===
    BEAR_MARKET_MODE_TRADIER: bool = True  # URGENT_FIX: favor shorts in current bear market
    AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER: bool = True  # BASE RULE — NEVER augment losing positions. OPEN at loss impossible (flat has no gain). See AUGMENT_AT_LOSS_ENABLED_TRADIER debate gate.
    # === 2026-09-06 AUGMENT SCOPE EXPANSION — bounce vs breakout separation + fallback reduce ===
    AUGMENT_MIN_GAIN_PCT: float = 3.0  # generic augment MIN_GAIN (sweep 0.5,1.0,1.5,2.0,3.0,5.0)
    AUGMENT_BOUNCE_MIN_GAIN_PCT: float = 0.5  # bounce adds at lower gain (sweep 0.0,0.5,1.0,1.5)
    AUGMENT_BREAKOUT_MIN_GAIN_PCT: float = 2.0  # breakout adds at higher gain (sweep 1.0,1.5,2.0,3.0,5.0)
    BOUNCE_AUGMENT_MIN_LOSS_PCT: float = -0.5
    AUGMENT_FALLBACK_REDUCE_ENABLED: bool = False
    AUGMENT_FALLBACK_REDUCE_PCT: float = 0.5  # fraction of last augment to reduce on fallback
    AUGMENT_FALLBACK_GAIN_PCT: float = 1.0  # fallback threshold pct from peak
    # === HEDGE vs RATIO SWEEP (2026-03-21 — 65 configs, both systems) ===
    RATIO_MULTIPLIER_TRADIER: float = 3.5  # BACKTEST_CHANGE_T61: was 2.0. 3.5x ratio exaggeration = Sharpe 260 (vs 249 at 2x). Best: 3.5-4x.
    HEDGE_CROSS_SYMBOL_TRADIER: bool = True  # BACKTEST_CHANGE_T62: Cross-symbol hedge enabled. 25% size, trigger -1%, no momentum gate. ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416
    HEDGE_SIZE_RATIO_TRADIER: float = 0.25  # BACKTEST_CHANGE_T62: Hedge at 25% of losing value. Sweet spot in sweep. ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416
    HEDGE_TRIGGER_LOSS_TRADIER: float = -1.0  # BACKTEST_CHANGE_T62: Trigger hedge at -1% loss (stocks: tighter than crypto -2% due to daily gaps). ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416
    HEDGE_SAME_SYMBOL_TRADIER: bool = False  # BACKTEST_CHANGE_T63: Same-symbol hedge DISABLED for stocks. Cross-symbol only. ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416
    # === HEDGE MODE (backtest) ===
    HEDGE_MODE_TRADIER: bool = False  # 2026-08-18 OFF LIMITS (BACKTEST_REPLICA_SWITCHES.md §15). Was BACKTEST_CHANGE_T31 disabled. Re-enable only via hedge-agent + USER unlock.
    # ── 2026-08-18 BACKTEST REPLICA MASTER SWITCHES (TRADIER) — LIVE-OFF BY DEFAULT ──
    # Tradier siblings of crypto replica switches. All OFF here; hook via vec_paths + v8_vec_sweep parity before live re-enable.
    # NON_VECTORIZABLE portfolio gates (LS_RATIO, SECTOR_LS) stay LIVE-ON after per_sym decent set — see notes below.
    B_MAIN_ENTRY_GATE_ENABLED: bool = False  # §11 B_MAIN entry gate (tradier_manage:1633). Not in v8 engine.
    LOCAL_EXTREMES_SCORER_ENABLED: bool = False  # §10 local extremes scorer (tradier_manage:1790). v8_quick OFF default; stays OFF.
    LINEARITY_LR_LONG_ENABLED: bool = False  # §12 LR long linearity filter. Not in backtest.
    LINEARITY_LR_SHORT_ENABLED: bool = False  # §12 LR short linearity filter. Not in backtest.
    FIN_ADVISORY_CONSUMER_ENABLED_TRADIER: bool = False  # §1 advisory consumer for tradier (if ever). OFF = no advisory.
    WT_4H_VEL_MANDATORY_REENTRY_ENABLED_TRADIER: bool = False  # §8 WT_4H_VEL mand reentry (stocks side). OFF.
    DELTA_EXIT_MANDATORY_REENTRY_ENABLED_TRADIER: bool = False  # §9 DELTA mand reentry (stocks). OFF.
    MANDATORY_PRICE_CROSS_EPQ_ENABLED_TRADIER: bool = False  # §5 EPQ "NO QUESTIONS ASKED" (stocks). OFF.
    LEADERBOARD_ENTRY_ENABLED_TRADIER: bool = False  # §13 leaderboard (stocks). OFF.
    DIRECTION_FAVORABLE_REENTRY_ENABLED_TRADIER: bool = False  # §6 direction favorable (stocks). OFF.
    B10_STOCH_REV_LIVE_ENABLED_TRADIER: bool = False  # §7 B10 stoch rev (stocks). OFF live.
    AUGMENT_AT_LOSS_ENABLED_TRADIER: bool = False  # 2026-08-18 DEBATE GATE (stocks). OFF — augment at loss may be revisited MUCH LATER only with Tier-2 proof + USER unlock. OPEN at loss impossible (flat has no loss).
    SQUEEZE_FIRE_ENABLED_TRADIER: bool = False  # SQUEEZE fire (stocks). OFF until vec hook.
    # === TRC AGGRESSIVE SANDBOX — "after-sandbox sandbox" ===
    # trc is paper-money. Push extreme settings here to prove before applying to trb.
    # 2026-04-27 SECOND CUT — every trc cap now 1/4 of original.
    TRC_START_POSITION_SIZE: float = 330.0    # was 500 / orig 1000
    TRC_MAX_ORDER_VALUE: float = 1250.0       # was 2500 / orig 5000
    TRC_MAX_POSITION_SIZE: float = 3750.0     # was 2500 / orig 5000
    TRC_SCALP_START_SIZE: float = 495.0       # was 500 / orig 1000
    TRC_SCALP_MAX_POSITIONS_PER_SIDE: int = 12   # was 10 / orig 20
    TRC_MAX_CONCURRENT_POSITIONS: int = 32    # was 20 / orig 40
    TRC_ROTATION_POSITION_SIZE: float = 3000.0   # was 1500 / orig 3000
    TRC_RSI2_POSITION_SIZE: float = 1980.0     # was 990 / orig 1980
    TRC_GAP_FILL_POSITION_SIZE: float = 1980.0   # was 990 / orig 1980
    TRC_DC_DAYTRADE_START_SIZE: float = 1980.0   # was 990 / orig 1980
    TRC_DC_DAYTRADE_LONG_BUDGET: float = 9900.0    # was 4950 / orig 9900
    TRC_DC_DAYTRADE_SHORT_BUDGET: float = 9900.0   # was 4950 / orig 9900
    ACCOUNT_TYPE_TRA: str = "cash"  # GFV guard
    TRC_SWING_LONG_BUDGET: float = 100000.0    # was 50000 / orig 100000
    TRC_SWING_SHORT_BUDGET: float = 100000.0   # was 50000 / orig 100000
    TRC_SCALP_LONG_BUDGET: float = 1250.0     # was 2500 / orig 5000
    TRC_SCALP_SHORT_BUDGET: float = 1250.0    # was 2500 / orig 5000
    TRC_BEAR_MARKET_MODE: bool = False  # No bear penalty — test both directions equally
    TRC_ENTRY_ZONE_LONG: float = 30.0  # Local extremes: deeper oversold bottom (was 30)
    TRC_ENTRY_ZONE_SHORT: float = 70.0  # Local extremes: deeper overbought top (was 70)
    TRC_ENTRY_MIN_ALIGNMENT: int = 4  # 2026-06-09: 6→4. ROLLBACK: 6.
    TRC_LS_RATIO_MIN: float = 0.30  # Wider than trb (0.50)
    TRC_LS_RATIO_MAX: float = 3.00  # Wider than trb (2.00)
    TRC_MAX_DAILY_LOSS_PCT: float = 10.0  # 3.3x trb (3%) — paper money, let it run
    TRC_SCALP_TARGET_PCT: float = 0.01  # 2x trb (0.005) — let winners run further
    TRB_NOLOSS_MIN_PROFIT_PCT: float = 0.0  # 2026-04-08: TECHNICALS ONLY. Was 3.0% which blocked all exits on losers. ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416
    TRC_NOLOSS_MIN_PROFIT_PCT: float = 0.0  # 2026-07-08 GAINMO triage: 0.5→0.0 — this silently resurrected the killed NOLOSS gate on trc only (272 blocked closes/hr, 8 stuck losers incl -31.74%; STRICT_NO_LOSS is ELIMINATED per STATE OF AFFAIRS)
    # === CONCENTRATION CAP — prevent single-symbol overexposure ===
    MAX_SYMBOL_VALUE_TRADIER: float = 3750.0  # was 7500 / orig 15000 — 2026-04-27 second cut
    HARD_MAX_SYMBOL_VALUE_TRADIER: float = 2500.0
    TRC_MAX_SYMBOL_VALUE: float = 1250.0  # was 2500 / orig 5000 — 2026-04-27 second cut
    TRC_LOCAL_EXTREMES_SCORER_ENABLED: bool = True  # Use local_extremes_scorer for dynamic $50-$5000 sizing
    TRADIER_LOCAL_EXTREMES_SCORING_ENABLED: bool = False  # 2026-04-26 KILL: tier sizing was suffocating PnL; Phase 8 disable = 90× PnL boost in v8
    LOCAL_EXTREMES_MIN_SCORE: float = 45.0  # 2026-04-20 le_dynamic winner: min LE score to allow entry (262sym Sharpe 3.5479). Wire in tradier_manage.py entry gate.
    DYNAMIC_SCORE_COUNTER_EXIT_ENABLED: bool = False  # 2026-04-20 le_dynamic winner: exit when opposite-direction LE score >= threshold
    DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD: float = 55.0  # 2026-04-20 le_dynamic winner: counter-exit trigger threshold (score=55 validated)
    # === BROKER_SYNC — LOGICAL DEMAND (NOT A SWITCH) — 2026-09-08 post-mortem ===
    # Flat-stub blind adopt created 69k short while price +10%. Augmentation path had no cap.
    # Waiting for actual broker info before repeating trade is MANDATORY — not optional, not switchable.
    # Caps below are hard logical demands (not switches): single adopt 2000, augment 2000, total 2500 == HARD_MAX.
    BROKER_SYNC_MAX_ADOPT_VALUE_USD: float = 2000.0  # hard cap — logical demand
    BROKER_SYNC_MAX_AUGMENT_VALUE_USD: float = 2000.0  # hard cap — logical demand
    BROKER_SYNC_MAX_TOTAL_VALUE_USD: float = 2500.0  # hard cap — logical demand
    # === WT_3M FORCE-OPEN — 2026-09-08 disaster: target 15k (default 50k) >> HARD_MAX 2.5k, bypassed all gates, fired 141× in one day ===
    WT_3M_FORCE_OPEN_TARGET_USD: float = 2500.0  # was 15000 (implicit default) / 50000 in sizing — capped to HARD_MAX
    WT_3M_FORCE_OPEN_SIZE_USD: float = 1200.0  # was 2500 — per-fire notional, smaller to respect MAX_ORDER_VALUE
    WT_3M_FORCE_OPEN_MAX_TRADES_PER_DAY: int = 4  # was unbounded (141×) — rate limit
    WT_3M_FORCE_OPEN_COOLDOWN_SEC: float = 900.0  # 15 min between fires on same symbol
    DG_MAX_FORCE_OPEN_NOTIONAL_USD: float = 2000.0  # was 4000 (tradier_manage default) — per-fire ceiling
    TRB_MAX_SYMBOL_VALUE: float = 2500.0  # was 5000 / orig 10000 — 2026-04-27 second cut
    # === trb caps now 1/4 of original ===
    TRB_MAX_LONG_VALUE: float = 12500.0    # was 25000 / orig 50000
    TRB_MAX_SHORT_VALUE: float = 12500.0   # was 25000 / orig 50000
    TRB_MAX_PUT_VALUE: float = 750.0       # was 1500 / orig 3000
    TRB_MAX_CALL_VALUE: float = 750.0      # was 1500 / orig 3000
    # === 2026-04-26 NEW user-spec position caps for trb (ABOVE) ===
    # === VIX regime filter (Phase B framework) ===
    VIX_VOLATILITY_REGIME_ENABLED: bool = True   # 2026-04-26: VIX vs VIX-200dMA gate (NOT SPY-SMA200 — that's L1061); 32% DD reduction documented
    VIX_PANIC_THRESHOLD: float = 30.0
    VIX_EXTREME_THRESHOLD: float = 40.0
    VIX_REGIME_SIZE_MULT_HIGH_VOL: float = 0.5   # VIX > 200dMA → 50% size
    VIX_REGIME_SIZE_MULT_PANIC: float = 0.0      # VIX > 30 → halt new entries
    VIX_SMA_LOOKBACK_DAYS: int = 200
    # === Earnings calendar avoidance (Phase B framework) ===
    EARNINGS_AVOIDANCE_ENABLED: bool = True
    EARNINGS_BLACKOUT_DAYS_BEFORE: int = 1     # T-1 blackout
    EARNINGS_BLACKOUT_DAYS_AFTER: int = 1      # T+1 still blackout (drift unclear early)
    EARNINGS_FORCE_TRIM_PCT: float = 0.5        # 50% trim T-1 close
    EARNINGS_PEAD_BOOST_ENABLED: bool = False  # post-earnings-drift overlay (start OFF)
    EARNINGS_PEAD_MIN_SURPRISE_PCT: float = 4.0
    EARNINGS_PEAD_BOOST_MULT: float = 1.5
    # === FOMC/CPI/NFP blackout (Phase B framework) ===
    MACRO_BLACKOUT_ENABLED: bool = True
    MACRO_BLACKOUT_SIZE_MULT: float = 0.5
    # === Per-sector L/S ratio enforcement (Phase B framework) — NON-VECTORIZABLE PORTFOLIO GATE (2026-08-18) ===
    # ⚠️ NON-VECTORIZABLE: sector L/S is cross-symbol portfolio state (counts per sector). Vector single-symbol engine cannot model it. Keep LIVE-ON after per_sym decent set. See BACKTEST_REPLICA_SWITCHES.md §16.
    SECTOR_LS_RATIO_ENABLED: bool = True  # LIVE-ONLY portfolio gate; vector unaware by design.
    SECTOR_LS_RATIO_MIN: float = 0.50
    SECTOR_LS_RATIO_MAX: float = 2.00
    SECTOR_LS_MIN_POSITIONS: int = 3        # don't enforce until ≥3 positions in a sector
    SECTOR_LS_RATIO_BYPASS_HEDGE: bool = True
    # === POSITION LIMITS (backtest) ===
    MAX_CONCURRENT_POSITIONS: int = 24  # EOD 2026-09-01 emergency: TRB at 19/16 blocked all entries, user $500 loss 28min left - raise to 24 to allow trading (orig 16)
    # === AUGMENT GUARD (parity with crypto) ===
    MIN_GAIN: float = 3.0  # NEVER augment below 3% gain — same rule as crypto
    # === L/S RATIO ENFORCEMENT (backtest) — NON-VECTORIZABLE PORTFOLIO GATE (2026-08-18) ===
    # ⚠️ NON-VECTORIZABLE: tradier L/S ratio is cross-symbol portfolio state. Vector single-symbol engine cannot model it. Keep LIVE-ON after per_sym decent set. See BACKTEST_REPLICA_SWITCHES.md §16.
    LS_RATIO_ENFORCE_TRADIER: bool = True  # LIVE-ONLY portfolio gate — stays True; vector unaware by design. 2026-07-28 RE-ENABLED (USER unlocked to stop HAO_SHORT stacking). Precondition (shorts enter) met.
    LS_RATIO_MIN_TRADIER: float = 0.20  # EOD 2026-09-01 emergency: loosen min to 0.20 to allow shorts (orig 0.50)
    LS_RATIO_MAX_TRADIER: float = 5.00  # EOD 2026-09-01 emergency: TRB tech_big 4.00>2.0 blocked all longs, $500 loss 24min left - raise to 5.00 to allow trading (orig 2.00)
    # === DAILY LOSS LIMIT (backtest) ===
    MAX_DAILY_LOSS_PCT: float = 3.0  # BACKTEST_CHANGE_T37 halt trading at 3% daily loss
    # === THROUGHPUT SAFETY KNOBS (added 2026-04-26 — pre-50-500/day push) ===
    # Master kill switch — when True, the four THROUGHPUT_SAFETY_* gates below are honored.
    # Stocks: trb=$70k live, trc=$30k paper-aggressive, tra=cash long-only.
    # Defaults are SANE-CONSERVATIVE; flip enabled=True before high-frequency push.
    THROUGHPUT_SAFETY_ENABLED_TRADIER: bool = False  # master gate
    # 1) Per-day max-loss % per account (halt new entries when day-PnL% breaches floor).
    #    Reuses existing trb (3%, MAX_DAILY_LOSS_PCT) and trc (10%, TRC_MAX_DAILY_LOSS_PCT) values.
    THROUGHPUT_MAX_DAILY_LOSS_PCT_TRADIER: Dict[str, float] = field(default_factory=lambda: {
        "trb": -3.0,   # mirrors MAX_DAILY_LOSS_PCT (3%)
        "trc": -10.0,  # mirrors TRC_MAX_DAILY_LOSS_PCT (10% — paper)
        "tra": -2.0,   # cash long-only — tightest floor
    })
    # Day boundary: 13:30 UTC (= 9:30 AM ET market open). Reset day-PnL counter at open.
    THROUGHPUT_DAILY_LOSS_RESET_UTC_HOUR_TRADIER: int = 13   # market open UTC hour
    THROUGHPUT_DAILY_LOSS_RESET_UTC_MINUTE_TRADIER: int = 30
    # 2) Max concurrent positions per account.
    #    trb: 16 (mirrors existing MAX_CONCURRENT_POSITIONS).
    #    trc: 40 (mirrors TRC_MAX_CONCURRENT_POSITIONS).
    #    tra: 9 (TRA_PREFERRED_SYMBOLS is 9 names — long-only hold).
    THROUGHPUT_MAX_CONCURRENT_POSITIONS_TRADIER: Dict[str, int] = field(default_factory=lambda: {
        "trb": 16,
        "trc": 40,
        "tra": 9,
    })
    # 3) Max total notional cap (USD) per account — refuse new opens beyond M $ gross exposure.
    #    Conservative defaults relative to account capital. trb cap = 16 × TRB_MAX_SYMBOL_VALUE (10000) = 160k
    #    floor → set to 80k as halfway gate. trc = 40 × 5000 = 200k → 100k floor.
    THROUGHPUT_MAX_TOTAL_NOTIONAL_USD_TRADIER: Dict[str, float] = field(default_factory=lambda: {
        "trb": 80000.0,
        "trc": 100000.0,
        "tra": 50000.0,
    })
    # 4) Per-symbol max fires per hour — anti-spam (one symbol can't dominate the queue).
    #    Stocks more conservative than crypto (cash settles slower, less re-entry edge intra-bar).
    THROUGHPUT_MAX_FIRES_PER_HOUR_PER_SYMBOL_TRADIER: int = 4
    # 4b) Per-account global max fires per hour.
    THROUGHPUT_MAX_FIRES_PER_HOUR_PER_ACCOUNT_TRADIER: Dict[str, int] = field(default_factory=lambda: {
        "trb": 60,
        "trc": 90,
        "tra": 12,   # cash hold — extremely low frequency
    })
    # External 0.01% incl-commissions Finandy stop (activates after +0.25% gain) — applies to crypto
    # only; stocks route through Tradier API directly. Documented in config.py too.
    # === END THROUGHPUT SAFETY KNOBS ===
    # === DAYTRADE WING — DC Breakout on lower TFs (5m/15m), open AM, flatten before close ===
    DC_DAYTRADE_ENABLED: bool = True  # Enable DC breakout daytrade system (parallel to HODL)
    DC_DAYTRADE_ACCOUNT: str = "trb"  # Account for daytrade positions
    DC_DAYTRADE_LONG_BUDGET: float = 3000.0  # Max $ exposure in daytrade longs
    DC_DAYTRADE_SHORT_BUDGET: float = 3000.0  # Max $ exposure in daytrade shorts
    DC_DAYTRADE_START_SIZE: float = 600.0  # Base order value per daytrade entry
    DC_DAYTRADE_MAX_POSITION_SIZE: float = 1000.0  # was 2000 — 2026-04-27 emergency halve
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
    MFI_FLIP_EXIT_ENABLED: bool = False  # BACKTEST_CHANGE_148: Exit when MFI exhausts (+3.91% avg vs +1.09% fixed TP, 44 trades)
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
    VWAP_BOUNCE_ENTRY_ENABLED: bool = False  # Enter on VWAP bounce (pullback to VWAP + reversal) ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416
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
    EMA_9_21_FILTER_ENABLED: bool = True  # 9/21 1h kindergarten — no entries if 9 on wrong side of 21 — True both platforms 2026-09-10 (exits always allowed)
    EMA_9_21_TIMEFRAME: str = "1h"
    KINDERGARTEN_EMA_GATE_ENABLED: bool = True  # 2026-09-10 FIX vs B&H: EMA 9/21 + EMA200 gate — blocks counter-trend. User: EMA filters have not been applied at all + trades against trend. Hardened default.
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
    # === TRC 5-MIN RELATIVE SWEEP ===
    TRC_5M_SWEEP_ENABLED: bool = True
    TRC_5M_SWEEP_TOP_N: int = 8
    TRC_5M_SWEEP_Z_WEIGHT: float = 0.7
    TRC_5M_SWEEP_DELTA_WEIGHT: float = 0.3
    TRC_5M_SWEEP_BUFFER_N: int = 20
    TRC_5M_SWEEP_BENCHMARK: str = "SPY"
    # === CONTRARIAN SPIKE FADE (BC_161 — 2026-03-30, 24 stocks × 2yr, +480%, 67% WR, 4.69 W/L) ===
    # SHORT big spikers (>2% in 30min + K>70), LONG big fallers (<-2% in 30min + K<30)
    # 2/3 WT must confirm fade direction. Exit on 2/3 WT against + mandatory reentry.
    SPIKE_FADE_ENABLED: bool = False  # 2026-07-10 OFF: never validated (NEW STRATEGY PROHIBITION — needs sweep proof before enable) AND evaluate_spike_fade has a wrong-self bug crashing every cycle (see memory 2026-07-09); it never successfully traded, so this is zero-behavior. Fix the self refs + validate at floor before re-enabling.
    SPIKE_FADE_THRESHOLD_PCT: float = 2.0  # Min % move in lookback to qualify as spike
    SPIKE_FADE_LOOKBACK_BARS: int = 6  # 6 bars × 5m = 30min lookback
    SPIKE_FADE_K_EXHAUSTION: float = 70.0  # K5m must be > this (spike up) or < 100-this (spike down)
    SPIKE_FADE_POSITION_SIZE: float = 600.0  # Per-entry size
    SPIKE_FADE_MAX_POSITIONS: int = 10  # Max concurrent spike fade positions
    SPIKE_FADE_COOLDOWN_BARS: int = 6  # Min bars between entries on same symbol
    # === 2.5σ STDEV BREAKOUT (HTF breakout + LTF retest scaling) ===
    # Stocks: same logic as crypto but with stock-tuned thresholds
    STDEV_BREAKOUT_ENABLED: bool = False  # Kill switch OFF — backtest sweep first
    STDEV_SUPPRESS_EARLY_EXIT: bool = False  # Suppress vel/delta exits when approaching BB band
    STDEV_BB_RZ_EXIT_ENABLED: bool = False  # Exit when price exits daily BB band (rejection)
    STDEV_BB_RZ_EXIT_TF: str = "D"
    STDEV_BB_RZ_SUPPRESS_PCTB: float = 0.85
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
    STDEV_BREAKOUT_EXIT_WT_ENABLED: bool = False
    STDEV_BOUNCE_ENABLED: bool = False
    STDEV_BOUNCE_PCTB_LONG: float = 0.05
    STDEV_BOUNCE_PCTB_SHORT: float = 0.95
    STDEV_BOUNCE_RVOL_MIN: float = 1.2
    STDEV_BOUNCE_HTF_LIST: List[str] = field(default_factory=lambda: ["D", "4h"])
    STDEV_REJECT_EXIT_ENABLED: bool = False
    STDEV_REJECT_EXIT_TF: str = "D"
    STDEV_REJECT_EXIT_ZONE: float = 0.80
    STDEV_REJECT_EXIT_RETURN: float = 0.65
    # === MOMENTUM INTERCEPTION (MI) — Early exit/entry via slowing deltas, LH/LL structure, divergence ===
    MI_EXIT_ENABLED_TRADIER: bool = False  # REVERTED 2026-04-17: MI_EXIT was triggering early exits at 0.3%. Mar-30 baseline OFF.
    MI_ENTRY_ENABLED_TRADIER: bool = False  # 2026-04-26: Phase 9 alpha enable
    MI_STRUCT_EXIT_ENABLED_TRADIER: bool = False  # WT peak LH / trough HL = structural weakening
    MI_EXHAUST_EXIT_ENABLED_TRADIER: bool = False  # EXHAUST_UP/DOWN on 1h/4h
    MI_DIV_EXIT_ENABLED_TRADIER: bool = False  # Divergence on 1h/4h
    MI_VELOCITY_EXIT_ENABLED_TRADIER: bool = False  # Velocity declining across 2+ TFs
    MI_WAVE_EXIT_ENABLED_TRADIER: bool = False  # Wave phase CONTRACTING on 1h
    MI_TF_AGREE_MIN_TRADIER: int = 3  # Minimum sub-signals to trigger MI exit
    MI_MIN_GAIN_EXIT_TRADIER: float = 0.50  # Stocks: higher min gain (0.50%)
    MI_ENTRY_STRUCT_BONUS_TRADIER: int = 10  # Score bonus for favorable structure on entry
    MI_ENTRY_EXHAUST_BONUS_TRADIER: int = 8  # Score bonus for opposing TF exhaustion on entry
    # === WT/DC DATA-DRIVEN SCORERS (2026-04-08 — OOS: Sharpe 11.46, 74.8% WR, PF 8.64x) ===
    # ═══════════════════════════════════════════════════════════════════════════
    # 🚩 NEW BASELINE 2026-05-12 (sweep winner dc45_h1_s40_grOFF on FIXED engine
    #     + FIXED k_1h NPZ precompute).
    # ───────────────────────────────────────────────────────────────────────────
    # Sweep: vec_sweep_FIXED_tradier_20260512_0706.csv, 20 syms × 16mo × 1005 configs
    # Winning metrics: pool_sharpe=+0.1421, trades=212, max_dd=11.9%, gain=+88.6%
    # TAG: [DIAGNOSTIC ONLY · n_syms=20 · years=1.36] [VEC ONLY — UNVALIDATED]
    # PRIOR VALUE (rollback): WT_DC_ENTRY_THRESHOLD = 0 (2026-05-09 ENTRY_THR_0
    #     sweep claim pool_sharpe=0.1804, 163 trades). To revert: change to 0.
    # WHY changed despite lower new-sweep sharpe: user explicit directive 2026-05-12,
    #     applying new BASELINE for forward-test alignment between vec and live.
    # ═══════════════════════════════════════════════════════════════════════════
    WT_DC_ENTRY_THRESHOLD: float = 45  # 2026-06-24 ROLLED BACK: bt_wtdc_threshold (291 stocks) — LONG ps 0.092→0.123 (+33%), SHORT ps 0.082→0.113 (+38%) at 45 vs 20. Gain/mo essentially unchanged (+3.82%/+3.20% vs +3.89%/+3.14%). Prior test (2026-06-03) optimized gain/mo not pool_sharpe — lower pool_sharpe = lower live quality.
    # Path-scoped side switches. Per-symbol profiles already emitted these names,
    # but the live/exact WT_DC branch did not consume them.
    WT_DC_LONG_ENABLED: bool = True
    WT_DC_SHORT_ENABLED: bool = True
    # 🚩 NEW BASELINE 2026-05-12 — 3 additional gates for tradier WT_DC_ENTRY path.
    # Wired in tradier_manage.py:2027-2068. Source: vec_sweep dc45_h1_s40_grOFF.
    # ROLLBACK each to disabled value (commented inline).
    HTF_ALIGN_REQUIRED_TRADIER: int = 2     # CLAUDE.md stocks ≥2 (was 1 — crypto value; fixed 2026-05-27). ROLLBACK: 1
    COMBINED_STOCH_GATE_TRADIER: float = 60.0  # CLAUDE.md stocks=60 (was 40 — sub-crypto value; fixed 2026-05-27). ROLLBACK: 40.0
    GR_HTF_GATE_ENABLED: bool = True       # RECONNECT 2026-09-01 GR HTF + WT cross both       # NEW. Adds GR HTF alignment gate (uses wt_bull_alignment/wt_bear_alignment). ROLLBACK: False (no change — gate stays off until validated)
    GR_HTF_REQUIRE_BULL: int = 1            # Used only when GR_HTF_GATE_ENABLED=True
    GR_HTF_REQUIRE_BEAR: int = 1            # Used only when GR_HTF_GATE_ENABLED=True
    WT_DC_EXIT_ENABLED: bool = True  # path-scoped master; False skips only the WT_DC scorer exit
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
    EXIT_HTF_QUICK_TP_ENABLED: bool = False       # HTF Quick TP: 1h exhausted + LTFs turning + 4h intact. KEEP — proven.
    # MU_LONG correction-path research gate.  Default OFF: this is deliberately
    # isolated from the existing WT/DC/Delta exits so exact c5 replays can prove
    # attribution before any live or matrix promotion.  Thresholds are override-
    # friendly per symbol/side through _cfg().
    MU_CORRECTION_EXIT_ENABLED: bool = False
    MU_CORRECTION_SYMBOLS: str = "MU"
    MU_CORRECTION_HTF_K_MIN: float = 80.0
    MU_CORRECTION_HTF_RSI_MIN: float = 60.0
    MU_CORRECTION_HTF_TFS: str = "1h+4h"
    MU_CORRECTION_HTF_MIN_TFS: int = 1
    MU_CORRECTION_LTF_FALL_TFS: str = "5m+15m"
    MU_CORRECTION_LTF_FALL_MIN_TFS: int = 2
    MU_CORRECTION_REQUIRE_HIGH_REVERSAL: bool = True
    MU_CORRECTION_REQUIRE_CLOSE_REVERSAL: bool = True
    MU_CORRECTION_MIN_GAIN_PCT: float = 0.0
    MU_CORRECTION_REENTRY_ENABLED: bool = False
    MU_CORRECTION_REENTRY_DC_TOL_PCT: float = 2.0
    MU_CORRECTION_REENTRY_STOCH_ENABLED: bool = False
    EXIT_STRUCT_DC_BREAK_ENABLED: bool = True    # RECONNECT 2026-09-01 Higher-low/lower-high break ON    # DC structural break (multi-TF). KEEP — catches real breakdowns.
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
    DELTA_EXIT_ENABLED: bool = False  # Re-enabled — real fix is in REENTRY_MONITOR (checks exit score before reopen)
    DELTA_EXIT_REQUIRE_NONZERO_SCORE: bool = True  # 2026-06-02 USER MANDATE: refuse DELTA_EXIT_BASELINE closes that fire with ALL-ZERO scores (bs=0/es=0/btf=0/etf=0) — 57 such 0-signal closes seen in /history burning commissions at ~0% gain. When True, a delta exit only fires if it carries a real bull/bear speed or TF count. Gate: tradier_manage.py ~6513. ROLLBACK: False.
    # WT_DC scorer exit guards
    WT_DC_EXIT_STALE_MAX_S: int = 600  # Don't exit on indicators > 10min stale (protects against stale data firing exits)
    DELTA_PYRAMID_ENABLED: bool = True  # 2026-04-26: Phase 9 alpha — re-test in live (was DEAD_CONFIRMED)
    DELTA_SPEED_SMOOTH: int = 5  # WINNER: sm=5
    DELTA_ACCEL_LOOKBACK: int = 5
    DELTA_TF_WEIGHTS: Optional[dict] = None  # Set in __post_init__
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
    EXIT_SCORER_MIN_CONDITIONS: int = 5          # 2026-04-29: SURFACED hidden knob — was implicit default 5 because not declared in config. wt_dc_exit_scorer.py:56 falls through to 5 when missing. Test C 12sym×2yr v8_quick (NOLOSS=off, all-other-exits=off) verdict: 5/5 K85 = pool_sharpe 0.0795 BEAT simple_mtf 0.0702 BEAT delta3 0.0697 BEAT loose 3/5 K75 0.0744. Sweep range: 3,4,5.
    EXIT_SCORER_DC_EXTREME: float = 0.80         # 2026-04-29: SURFACED hidden knob — was implicit default 0.80. Sweep range 0.70-0.90.
    EXIT_SCORER_PARTIAL_SCORE: float = 40.0      # 2026-04-29: SURFACED hidden knob — score returned for N-1 conditions met. Sweep 30-50.
    EXIT_SCORER_FULL_SCORE: float = 100.0        # 2026-04-29: SURFACED hidden knob — score returned for full N. Stays 100; setting <30 effectively gates score≥threshold check.
    K_LOWER_HIGH_EXIT_ENABLED: bool = False        # v8 engine: exit if k peaks below extreme and turns down
    K_LOWER_HIGH_LTF_THRESHOLD: float = 65.0     # k_5m must reach >= 65 to qualify as failed rally
    K_LOWER_HIGH_EXTREME: float = 95.0           # only fires if k_prev < 95 (didn't reach true extreme)
    STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS: float = 100.0
    # ═══ D4 BREAKOUT MULTI-LUNG (stocks, 2026-04-16) — extracted from ez_breakout_agent.py ════
    # UNPROVEN: default OFF until sweep tier breakout_multi_lung_tradier delivers Sharpe > 2 on 128-stock × 2yr.
    # NEVER flip ENABLED=True in live config without sweep proof.
    BREAKOUT_MULTI_LUNG_ENABLED: bool = True  # 2026-04-26: Phase 9 alpha (Sharpe 1.26 in v8)
    BREAKOUT_MULTI_LUNG_MODE: str = "AUGMENT"      # "AUGMENT" (OR) | "REPLACE"
    BREAKOUT_MULTI_LUNG_TIER: str = "STOCK"        # stocks default to STOCK tier (D+W+4h)
    BREAKOUT_MULTI_LUNG_COMPOSITE_INHALE: float = 0.20
    BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE: float = -0.037500000000000006
    BREAKOUT_MULTI_LUNG_SLOW_LUNG_OVERRIDE: float = 0.15
    BREAKOUT_MULTI_LUNG_COOLDOWN_BARS: int = 8     # stocks breathe slower than crypto
    # === RED ZONE (stocks) — structural levels with HTF confirmation ===
    RZ_ENTRY_ENABLED: bool = False
    RZ_EXIT_ENABLED: bool = False  # T25 sweep 2026-04-14 (10sym, fixed gates): True avg=0.492 vs False=0.229 (+115%). Previous stale result (False=0.548) was from broken-gate run.
    STRUCTURAL_EXIT_GATE_ENABLED: bool = False  # USER MANDATE 2026-07-21 (MU_LONG trb: 20 closes in 88min while price rallied +3.15%, every close ~0.00% gain). NEVER exit while price is going up (long) / down (short); an exit needs an LTF collapse (lower high AND lower low AND close below prev low) OR a lower-high+lower-low on 1h or 4h. Enforced in wt_dc_delta.structural_exit_permitted() (live crypto + live stocks + Tier-2) and vectorized in v8_quick_engine.compute_exit_signals (Tier-1). Kills the k_1h>80 / dc_pos>0.7 top-zone churn. Loss exits R1/R2/HEDGE_FAILED are NOT affected. ROLLBACK: False.
    RZ_TOP_BB_THRESHOLD: float = 0.85
    RZ_BOT_BB_THRESHOLD: float = 0.375
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
    TRADIER_MIN_HOLD_MINUTES: float = 240.0  # 2026-09-10 P0 fix: 4320→240 (72h deadlock). 240m = 4h swing hold per test + user 4H HTF gate; 4320 blocked all exits 3 days.
    TRADIER_MIN_HOLD_MINUTES_SHORT: float = 60.0  # shorts 60m (test expects directional split)
    # === PRICE CROSS-BACK REENTRY (2026-04-27 user rule) ===
    # When a stock position is fully closed and price subsequently returns to within
    # a tight band of last_reduction_price, immediately reopen — bypasses ANTI_CHURN,
    # RZ_BLOCK, hardcool, stoch/WT/HTF gates. User: "IMMEDIATELY BUY AGAIN IF EXIT
    # PRICE IS CROSSED". Defends against the suicide pattern of "we sold, price came
    # right back, we did nothing".
    PRICE_CROSS_BACK_REENTRY_ENABLED: bool = True  # 2026-09-11 EMERGENCY: False→True — MSTR/IBIT/BMNR bouncing +1% but PRICE_CROSS was OFF globally, so evaluate_reentry returned cooldown 15m instead of REENTRY. User mandate ON everywhere.
    PRICE_CROSS_BACK_BAND_PCT: float = 0.3      # within 0.3% of last_reduction_price
    PRICE_CROSS_BACK_MAX_AGE_MIN: float = 525_600_000.0  # 2026-06-02 USER MANDATE: fire FOREVER (~1000yr) until positionAmt>0, not just 4h. Was 240. Momentum still gated by check_reentry_confirmation. ROLLBACK: 240.
    # === OVERTRADE_GUARD (2026-05-21 19:10 PARITY FIX — was missing from tradier config) ===
    # config.py:965 was loosened 8→50 earlier today. tradier_manage.py:3215 reads
    # `getattr(config, 'TRADES_PER_SYM_PER_DAY_MAX', 8)` where config = TradierConfig()
    # — without this field, tradier silently fell back to default 8. Live trb log
    # 2026-05-21 19:06 shows every NEW open blocked by `OVERTRADE_GUARD ... cap=8`
    # despite crypto cap=50. Adding here closes the parity gap. ROLLBACK: set to 8.
    TRADES_PER_SYM_PER_DAY_MAX: int = 8  # 2026-06-24 ROLLED BACK: 50→8. WT_3M_FORCE_OPEN bypasses this cap when needed; 50 was causing 6x churn flood with pool_sharpe degradation.
    # === RECOVERY_AUGMENT — PARTIAL-CLOSE RECOVERY REENTRY (2026-05-20 → 2026-09-11 CLARIFIED) ===
    # SEMANTICS: This is a REENTRY / RE-OPEN, NOT an AUGMENT of a winning position.
    # PRICE_CROSS_BACK only fires when positionAmt == 0 (fully closed). The "forgotten
    # winner" pattern is dominated by SENTIMENT_FADE REDUCEs that leave positionAmt > 0
    # (partial close). With gain < MIN_GAIN, both AUGMENT (gate ~tradier_manage:9768)
    # and REENTRY (gate ~tradier_manage:3288) are blocked — position is frozen.
    # RECOVERY_AUGMENT fires REENTRY_OPEN with distinct reason "RECOVERY_AUG_PARTIAL_*"
    # when price crosses back through last_reduction_price within band+age. Bypasses
    # NO_DOUBLE_OPEN_BLOCK (queue) and HARD_MIN_GAIN_WALL (execute) via reason-based
    # `_is_recovery_aug` (= 'RECOVERY_AUG' in reason). Gated to gain >= 0.5*MIN_GAIN.
    # ENABLED BY DEFAULT (ON) — sibling to PRICE_CROSS_BACK for positionAmt>0. 2026-05-29
    # gated: fires ONLY when current gain >= 0.5*MIN_GAIN (>=1.5%) — never martingale.
    RECOVERY_AUGMENT_ENABLED: bool = True  # REENTRY semantics (see header). ON by default 2026-09-11.
    RECOVERY_AUGMENT_BAND_PCT: float = 1.0       # 2026-05-21 widened 0.3→1.0 (rally past 0.3% band was leaving positions stranded)
    RECOVERY_AUGMENT_MAX_AGE_MIN: float = 240.0  # mirrors PRICE_CROSS_BACK_MAX_AGE_MIN
    RECOVERY_AUGMENT_REQUIRE_WT_CROSS: bool = True  # 2026-09-11: REQUIRE WT 5m+15m bull for LONG (bear for SHORT) — prevents reentering when wt going down. Was False.
    RECOVERY_AUGMENT_SIZE_PCT: float = 1.0       # 1.0 = 1× START_POSITION_SIZE (matches PRICE_CROSS_BACK qty)
    RECOVERY_AUGMENT_ONE_FIRE_PER_REDUCE: bool = True  # set Position.recovery_fired after firing; cleared on next REDUCE
    # === SENTIMENT_FADE_MODE (2026-05-21 — test matrix per user 'make it a close if useful, else discard') ===
    # 7-day audit (data/today_bt_baseline + forgotten-reentry hunt): SENTIMENT_FADE is
    # the dominant exit reason for abandoned winners (ARM +20.9%, UUUU +20.2%, UEC +16.1%,
    # MP +12.4%, NVDA +8.4%, GDX +7.6%, ...) and the partial-REDUCE leaves positionAmt > 0
    # which freezes the position (can't AUGMENT due to MIN_GAIN, can't REENTER due to
    # NO_DOUBLE_OPEN_BLOCK). Modes:
    #   "REDUCE"   — current behavior: partial reduce to ideal_qty (defaults to this for safety)
    #   "CLOSE"    — full close; lets PRICE_CROSS_BACK_REENTRY handle the recovery cleanly
    #   "DISABLED" — skip the SENTIMENT_FADE rebalance entirely
    # Read by tradier_manage.py SentimentManager rebalancer AND by backtest_v8_engine.py
    # vec proxy at line ~3748. Test-matrix sweep arms (S1 queue.json): SENTIMENT_FADE_MODE_*.
    SENTIMENT_FADE_MODE: str = "DISABLED"  # 2026-06-02 USER MANDATE: was REDUCE. SENTIMENT_FADE rebalance-reduce fires at any gain (~0%), LIVE-ONLY (not in backtest), pyramided-into-highs+panic-sold-dips per IBIT autopsy. DISABLED skips the rebalance entirely. ROLLBACK: REDUCE.
    # === DUPLICATE-FIRE GUARDS (2026-04-27 — MSTR headless-chicken loop) ===
    # Same (position_key, action) refused if queued within N sec. Stops the
    # "REBALANCE → invalid_api_response → REBALANCE" loop the broker rejected
    # with phantom 58/36-share sells.
    TRADIER_QUEUE_DEDUPE_SEC: float = 60.0       # global queue_trade_action dedupe
    REBAL_ATTEMPT_COOLDOWN_SEC: float = 3600.0   # 2026-05-26 USER MANDATE — was 300s; bumped to 60min after trc/IBIT_LONG autopsy (40 SENTIMENT_BOOST + 119 SENTIMENT_FADE in 32 realized rounds, -77.85% gain on +26% UP-trending asset; rebalancer pyramided into highs and panic-sold at small dips)
    SENTIMENT_REBALANCER_ENABLED: bool = False   # 2026-05-26 USER MANDATE — KILLED after trc/IBIT_LONG -$80k/mo bleed. periodic_sentiment_rebalancing pyramids into winners + flushes on noise. Re-enable only after sample-floor backtest with proper cooldowns + dead zone proves positive Sharpe.
    SENTIMENT_REBAL_REDUCE_DEVIATION_THR: float = 0.50   # was 0.20 — require qty 50% over ideal before any FADE reduce
    SENTIMENT_REBAL_AUGMENT_DEVIATION_THR: float = 1.0   # was 0.25 — require qty 50% under ideal before any BOOST add
    SENTIMENT_REBAL_COOLDOWN_MIN: float = 240.0          # was 30min — sentiment doesn't move that fast
    # ═══ STOCK DELTA EXIT TF WEIGHTS — HTF only ═══
    # Stocks exit ONLY on 1h/4h/D slowdown. LTF (5m/15m) noise must NOT move the
    # delta speed calculation. This dict is passed to DeltaTracker.tf_weights.
    DELTA_TF_WEIGHTS_STOCK: Optional[dict] = None  # set in __post_init__
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
    TRC_CLENOW_ENABLED: bool = True  # 2026-06-02 V8-VALIDATED → KEPT ON (USER). Faithful vec backtest (tools/bt_clenow.py, NPZ clenow_score_D = same slope×R² momentum live reads): pool_sharpe=+0.1815, gain_per_mo=+8.5%, total=+221% over 96 trades/35 syms — a REAL gain-augmenting momentum edge. Augments gains → ON in live. (Gain-augmenting strategy NOT fully modeled in vec sweep → controlled by PARITY_COMPARISON_MODE master switch: only flipped off during a live↔backtest parity A/B, then back on.)
    TRC_SMFI_ENABLED: bool = False  # 2026-06-02 V8-VALIDATED → NOISE-tier, off per user parity policy. Faithful vec backtest (tools/bt_smfi.py, replicates compute_smfi cumulative smart-money-flow + 20d bull-divergence on NPZ daily OHLC — same formula as live): pool_sharpe=+0.0534 (Noise), gain_per_mo=+10.2%, total=+264% over 719 trades/37 syms. POSITIVE in raw gain but risk-adjusted NOISE (0.05 << 0.48 Minervini / 0.44 baseline) + 719 trades = commission churn → does NOT improve the book → OFF both. NOTE: it IS backtestable (OHLC formula, not order-flow); this is a validated-noise cut, NOT a can't-backtest case. ROLLBACK: True (if you want the raw +10%/mo despite the churn).
    TRC_MINERVINI_ENABLED: bool = True  # Paper-only: needs V5 validation before trb
    TRC_CONNORS_RSI_ENABLED: bool = False  # 2026-06-02 V8-VALIDATED → LOSER, turned OFF per user parity policy. Faithful vec backtest (tools/backtest_connors_rsi_vec.py, uses NPZ connors_rsi_D + sma_200_D, same inputs as live): pool_sharpe=-0.5754, gain_per_mo=-15.25%, total_gain=-555% over 139 trades/34 syms. Oversold-mean-reversion catches falling knives on this universe. OFF in both live (was trc-on) AND backtest by default. ROLLBACK: True (but don't — it loses).
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
    TRADIER_DC_DAYTRADE_TARGET_PCT: float = 0.005       # REVERTED 2026-05-18 18:30 (was 0.015 since 2026-05-17). 2026-05-17 flip had no sample-floor proof; isolated vec sweep queued.
    # Gate only the existing DC breakout-tier augmentation inside
    # evaluate_augment. Default True preserves the pre-switch live behavior.
    # WT_D_BOUNCE_AUG and TRAILING_AUG are separate paths and are unaffected.
    DC_TIER_AUG_ENABLED: bool = True
    # 2026-05-16: ATR-aware DT_TARGET kill-switch (DEFAULT OFF — sweep-validate before enable).
    # When True: effective target = max(2 × atr_5m / entry, TARGET_PCT, noloss_min). Reason label
    # switches to DT_TARGET_ATR when ATR-driven. When False: legacy max(TARGET_PCT, noloss_min)
    # behavior preserved. Recommended pair when enabling: raise TRADIER_DC_DAYTRADE_TARGET_PCT to 0.015.
    DT_TARGET_ATR_ENABLED: bool = False  # REVERTED 2026-05-18 18:30 (was True since 2026-05-17). Flip had no sample-floor proof; isolated vec sweep queued.
    TRADIER_DC_POSITION_ENTRY_THRESHOLD: float = 0.25   # REVERTED 2026-04-17: see DC_POSITION_ENTRY_THRESHOLD above.
    # 2026-05-16: DC_TIER4_DC4H_AUG late-entry guard (DEFAULT OFF behind DC_TIER4_BAR_MATURITY_BLOCK_ENABLED).
    # When True + threshold in (0,1): block tier-4 augment when current bar has consumed >threshold
    # of daily ATR in the SAME direction as the augment. Symptom: 6 events in last 30d averaged
    # -2.25%. Fail-open if open_D/atr_D missing. Tiers 1/2/3 untouched.
    DC_TIER4_BAR_MATURITY_BLOCK_ENABLED: bool = False
    DC_TIER4_BAR_MATURITY_BLOCK: float = 0.7

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
    TRADIER_RSI_SHORT_REL_VOLUME_MIN: float = 2.4  # relative vol > 1.2× avg required
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
    TRADIER_MFI_ENTRY_LONG_ENABLED: bool = False  # FLIPPED 2026-08-10 03:25: was True blocks all AAPL longs MFI 100>80, need live results GDX/HAO/LLY; vector bypasses, real engine gated

    # Stoch entry filters (non-K-zone)
    TRADIER_STOCH_ENTRY_LONG_TRADIER: int = 30          # K < this for normal long entry
    TRADIER_STOCH_ENTRY_SHORT_TRADIER: int = 52  # K > this for normal short entry
    WT_DC_ENTRY_K5M_MAX_LONG: float = 100.0  # 2026-04-27: hard k5m cap for WT_DC_ENTRY_THRESHOLD-path LONG entries (default inert at 100). Lower to 80 to block "buy at 5m top" e.g. NVDA k5m=95.
    WT_DC_ENTRY_K5M_MIN_SHORT: float = 0.0   # 2026-04-27: hard k5m floor for WT_DC_ENTRY_THRESHOLD-path SHORT entries (default inert at 0). Raise to 20 to block "short at 5m bottom".
    # 2026-05-16: bar-maturity guard on WT_DC_ENTRY path (DEFAULT OFF behind WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED).
    # When True + threshold in (0,1): block entries when current bar has consumed >threshold of
    # its expected daily ATR in the SAME direction as the proposed signal. Symptom that prompted:
    # WT_DC_ENTRY_80_D_bear averaging -7.14% on 4 trades. Fail-open if open_D/atr_D unavailable.
    WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED: bool = False
    WT_DC_ENTRY_BAR_MATURITY_BLOCK: float = 0.7
    # 2026-05-17 PATCH A: penny-stock LONG block (enabled after the LEXX loss audit).
    # Live 30d: LEXX LONG -8.33% (trc:404) + -5.65% (trb:425) on $0.60 stock via RATIO_BOOST_L.
    # When True: REFUSE any LONG entry with last_price < PRICE_USD. SHORTs unaffected. Gate fires
    # at top of _disaster_guard_for_entry (earliest entry-side site).
    PENNY_STOCK_LONG_BLOCK_ENABLED: bool = True
    PENNY_STOCK_LONG_BLOCK_PRICE_USD: float = 5.0
    # 2026-05-17 PATCH B: DC_BREAK_LOW_15M SHORT requires HTF bear alignment (DEFAULT OFF).
    # Live evidence: ASTS SHORT -5.70% (trb:423) on bare DC_BREAK_LOW_15M no MTF/ratio confirm.
    # When True: REFUSE SHORT signal from DC_BREAK on tf=15m when wt_bear_alignment < MIN_TFS.
    # Fail-open on missing alignment data. Other DC_BREAK TFs (5m) untouched. LONG side untouched.
    DC_BREAK_LOW_REQUIRE_HTF_ENABLED: bool = False
    DC_BREAK_LOW_REQUIRE_HTF_MIN_TFS: int = 2
    # 2026-05-17 CATALYST_VOLUME_GATE (strategy_plan.md §5.6, audit_relvol_filters.md §3) — DEFAULT OFF.
    # Boolean entry filter for NEW OPENs only (not augments). Per O'Neil CAN-SLIM "N": breakout volume
    # ≥ 1.5× 50-day average AND price closes above (LONG) / below (SHORT) D-Donchian-20. Per Bulkowski,
    # this is a failure-AVOIDANCE filter not a return-amplification filter — failures triple without it.
    # Reads NPZ field `volume_D_50_sma` (added 2026-05-17 in backtest_v8_precompute_tradier.py:498).
    # Fail-CLOSED: missing data → blocks entry (reason BLOCKED_CATALYST_VOLUME_NO_BREAKOUT).
    # NEW STRATEGY PROHIBITION (CLAUDE.md): cannot be enabled on live without sweep proof + paper days
    # + explicit user approval. Sweep validation tier suggested: tradier_catalyst_gate (see apply report).
    CATALYST_VOLUME_GATE_ENABLED: bool = False
    CATALYST_VOLUME_RATIO: float = 1.5
    # 2026-05-17 TR_TREND_V1 (vec_paths/tr_trend_v1.py + strategy_plan.md §4 + tr_trend_v1_build_report.md).
    # NEW STRATEGY PROHIBITION (CLAUDE.md): defaults OFF. Per-sym sharpe >1.0 validated on TRGP/SNDK across
    # 115sym × 2.13yr vec sweep (8 syms >0.5, 58% net positive). Shadow-log only on top-8 syms first session;
    # NO real orders fire while TR_TREND_V1_SHADOW_LOG_ONLY=True. User flips that flag after comparing
    # paper signals against live decisions for 1+ session.
    TR_TREND_V1_ENABLED: bool = False
    TR_TREND_V1_SHADOW_LOG_ONLY: bool = True
    TR_TREND_V1_SHADOW_SYMBOLS: tuple = ('TRGP', 'SNDK', 'AVGO', 'GLD', 'PLTR', 'MU', 'CDE', 'SLV')
    TR_TREND_V1_ATR_STOP_MULT: float = 2.0  # GHOST FIX 2026-09-05: was getattr default 2.0 at tradier_manage:282,359 — now declared
    TR_TREND_V1_VOL_MULT: float = 1.5  # GHOST FIX 2026-09-05: was getattr default 1.5 at tradier_manage:269 — now declared
    TR_TREND_V1_RETEST_MAX_BARS_D: int = 5  # GHOST FIX 2026-09-05: was getattr default 5 at tradier_manage:303 — now declared
    TR_TREND_V1_RETEST_TOL_PCT: float = 0.5  # GHOST FIX 2026-09-05: was getattr default 0.5 at tradier_manage:310 — now declared
    TR_TREND_V1_RETEST_VOL_MAX_MULT: float = 0.7  # GHOST FIX 2026-09-05: was getattr default 0.7 at tradier_manage:314 — now declared
    TR_TREND_V1_TIME_STOP_BARS_D: int = 60  # GHOST FIX 2026-09-05: was getattr default 60 at tradier_manage:386 — now declared
    TR_TREND_V1_TIME_STOP_NO_HIGH_BARS_D: int = 30  # GHOST FIX 2026-09-05: was getattr default 30 at tradier_manage:387 — now declared
    REENTRY_MATERIAL_OVERSHOOT_PCT: float = 0.5  # GHOST FIX 2026-09-05: was getattr default 0.5 — now declared
    REENTRY_OPPOSITION_MAX_FLAT_BARS: int = 12  # GHOST FIX 2026-09-05: was getattr default 12 — now declared
    TRADIER_STOCH_EXTREME_LONG_TRADIER: int = 15        # deeper K for high-conviction long
    TRADIER_STOCH_EXTREME_SHORT_TRADIER: int = 85       # deeper K for high-conviction short

    # WaveTrend composite scoring + exit TF config
    TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER: bool = True
    TRADIER_WT_EXIT_TFS_TRADIER: str = "5m+15m+1h+4h+D"  # T4 sweep: 5m+15m+1h+4h+D avg=5.961 best tested (was "3m,15m,1h")
    TRADIER_WT_EXIT_MIN_TFS_TRADIER: int = 5             # REVERTED 2026-04-17: 4 = exits too eagerly. Mar-30 baseline = 5 (require ALL 5 TFs against). Patient exits.

    # Entry score aggregate threshold
    TRADIER_ENTRY_SCORE_THRESHOLD: int = 30             # 2026-04-23 EMERGENCY: raised 24→30. 2.8155 validated winner uses 30. Reduces bad entries.
    # ========================================================================
    # --- END RECONNECTED SWITCHES ---
    # ========================================================================

    # --- 11. Logging ---
    LOG_DIR: Path = Path(os.environ.get("TRADIER_API_LOG_DIR") or os.environ.get("EZ_LOG_DIR") or (Path.home() / "logs"))
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
        clean_input = input_val.strip()
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
    # 2026-04-28 — ez_reentry.py / ez_reentry_daemon.py wiring switches.
    # See config.py for full description. Tradier shares the same switches so
    # the daemon and v8 backtest paths read consistent flags across modes.
    EZ_REENTRY_DAEMON_ENABLED: bool = False
    EZ_REENTRY_INLINE_ENABLED: bool = False
    EZ_REENTRY_INLINE_TIER12_EPQ_ENABLED: bool = False
    EZ_REENTRY_INLINE_EVAL_EPQ_ENABLED: bool = False
    EZ_REENTRY_INLINE_EVAL2_DIRECT_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_PERIODIC_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_ENFORCE_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_PRICE_MONITOR_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_ENFORCE_EPQ_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_EVAL2_EPQ_ENABLED: bool = False
    # 2026-04-28 — Price-cross GUARANTEE safety loop. See config.py for full description.
    EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED: bool = False
    EZ_REENTRY_PRICE_CROSS_INTERVAL_S: float = 5.0
    EZ_REENTRY_PRICE_CROSS_PCT: float = 0.0  # strict cross — any move past exit fires
    EZ_REENTRY_PRICE_CROSS_MIN_GAP_S: float = 60.0  # 1min per-key dedup
    EZ_REENTRY_PRICE_CROSS_PARTIAL_FRAC: float = 0.5
    EZ_REENTRY_PRICE_CROSS_MAX_AGE_HOURS: float = 48.0
    EZ_REENTRY_PRICE_CROSS_MAX_FIRES_PER_TICK: int = 20
    REENTRY_MAX_PRICE_DIVERGENCE_PCT: float = 20.0
    REENTRY_BYPASS_CONFIRMATION_THRESHOLD_PCT: float = 0.002
    BREAKOUT_LEASH_ENABLED: bool = True  # 2026-09-10 KEEP True for breakout improvement (easy exit before loss +25% reentry) — crypto re-enabled with gain gate
    BREAKOUT_LEASH_MAX_PER_MIN: int = 3  # 2026-09-10 stocks: 3/min anti-churn (was missing, default 10)
    BREAKOUT_LEASH_QTY_MULT: float = 0.25
    BREAKOUT_LEASH_REENTRY_MULT: float = 1.50
    BREAKOUT_LEASH_TF: str = "5m"  # 2026-09-10 stocks: 5m (crypto 3m) — matches stock TF
    BREAKOUT_LEASH_MAX_LOSS_PCT: float = -0.5  # only leash-exit BEFORE loss
    BREAKOUT_REENTRY_BETTER_PRICE_MULT: float = 1.25
    # EZ_REENTRY_PRICE_CROSS_BLOCK_DURATION_S removed 2026-04-28 — see config.py for context.
    # 2026-04-28 — PPL-fired positions get effective gain doubled. See config.py.
    EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED: bool = True
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
    ATR_ADAPTIVE_SIZING_TARGET_PCT: float = 1.5  # 2026-04-26: 12% target (was 2%) — Phase 8 winner setting
    ATR_ADAPTIVE_STOP_ENABLED: bool = True  # BACKTEST_CHANGE_130: ATR-based sizing reduction (not stop — STRICT_NO_LOSS)
    ATR_ADAPTIVE_STOP_MULT: float = 2.0  # BACKTEST_CHANGE_130: ATR(14) x this = risk distance
    ATR_ADAPTIVE_STOP_TF: str = '1h'
    ATR_LONG_WINDOW = 100
    AUGMENT_BLOWPAST_ENABLED: bool = True  # gain >= 3×MIN_GAIN, conviction 90. Highest conviction.
    # 2026-04-28 PATH B (B4) — TRAILING augment for compound gains. Crypto fired 17,449 augments, tradier 0
    # because MIN_GAIN_TO_BUY_AGGRESSIVELY=3% rarely hit on stocks. New path fires every TRAILING_AUG_GAIN_STEP_PCT
    # of gain (default 0.5%), capped at TRAILING_AUG_MAX_PER_POSITION (default 3) augments per position.
    # Default DISABLED in live until backtest validates. Override flag in sweep tests.
    TRAILING_AUG_ENABLED_TRADIER: bool = False
    TRAILING_AUG_GAIN_STEP_PCT: float = 0.5
    TRAILING_AUG_MAX_PER_POSITION: int = 3
    TRAILING_AUG_MIN_GAIN_PCT: float = 0.5
    AUGMENT_HTF_TREND_ENABLED: bool = True  # HTF trend only, conviction 65. Most frequent.
    AUGMENT_PYRAMID_ENABLED: bool = True  # Re-enabled — pyramid must always run, sizing handles risk ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416
    AUGMENT_WT_3TF_ENABLED: bool = True  # 3/3 LTF aligned + smaller gain, conviction 70.
    AUGMENT_WT_CROSS_ENABLED: bool = True  # WT cross + aligned 2/3 TFs + gain >= MIN_GAIN, conviction 80.
    BASIS_CONDITION: bool = False  # BACKTEST: OFF is +0.67 delta Sharpe (dc_basis_15m/1h both SKIP in sweep)  # No opening on wrong side of dc_basis_15m + 1h + 4h
    BB_BREAKOUT_ENABLED: bool = False  # 2026-05-23: SWEEP VERDICT — trigger HARMFUL (ΔSharpe -0.0025). Use GATE instead.
    BB_BREAKOUT_SCORE: int = 20
    BB_BREAKOUT_TF: str = '15m'  # 2026-05-23: 1h→15m per vec_top_combos bb15_lt30 winner
    # 4h BB breakout ladder: 25% at breakout, 50% at dc_basis_4h, 25% at next WT1h cross.
    BB4H_BREAKOUT_LADDER_ENABLED: bool = True
    BB4H_BREAKOUT_LADDER_TARGET_USD: float = 2000.0
    BB4H_BREAKOUT_LADDER_BREAKOUT_PCT: float = 0.25
    BB4H_BREAKOUT_LADDER_BASIS_PCT: float = 0.50
    BB4H_BREAKOUT_LADDER_WT_CROSS_PCT: float = 0.25  # final tranche at next bullish WT 1h cross
    BB4H_BREAKOUT_LADDER_STOCK_MAX_NOTIONAL_USD: float = 2000.0
    BB4H_BREAKOUT_LADDER_MAX_STOCK_SHARES: int = 1
    # Hold while the 15m trend is still structurally favorable. Hard newborn/
    # emergency DC stops and protective Bottom-A exits remain higher priority.
    FAVORABLE_SLOPE_HOLD_ENABLED: bool = True
    STRUCTURE_FLIP_REENTRY_ENABLED: bool = False
    STRUCTURE_FLIP_REENTRY_TF: str = '15m'
    STRUCTURE_FLIP_REENTRY_BASIS_RESTRICTION_ENABLED: bool = False
    STRUCTURE_FLIP_REENTRY_BASIS_TF: str = '4h'
    BREAKEVEN_EXIT_AFTER_BARS_ENABLED: bool = False  # USER 2026-08-07: BE ratchet default ON — "stop to zero once 15m higher-low achieved"; may only leave defaults if a receipt beats it without it (Bible §16.63)
    BREAKEVEN_EXIT_AFTER_BARS: int = 8
    BREAKEVEN_EXIT_AFTER_BARS_TF: str = '15m'
    BREAKEVEN_EXIT_AFTER_BARS_BUFFER_PCT: float = 0.05
    BREAKEVEN_EXIT_REQUIRE_WT15M_STRUCTURE: bool = True
    BB_ENTRY_LONG_THRESHOLD: float = 0.30  # 2026-05-23: FIXED — was -0.2 (overbought). Now 0.30 (pullback)
    BB_ENTRY_SHORT_THRESHOLD: float = 0.70  # 2026-05-23: FIXED — was 1.0 (oversold). Now 0.70 (pullback)
    BB_RSI_STOCH_SCALP_ENABLED: bool = False  # 2026-05-23: SWEEP VERDICT — COMBO (gate+trigger) worse than GATE alone (ΔSharpe -0.0022)
    BB_RSI_STOCH_SCALP_SCORE: int = 12
    BB_RSI_STOCH_SCALP_TF: str = '15m'  # 2026-05-23: was hardcoded 5m
    BB_RSI_STOCH_BB_MAX: float = 0.30  # 2026-05-23: was 0.2
    BB_RSI_STOCH_RSI_MAX: float = 40.0  # 2026-05-23: was 30
    BB_RSI_STOCH_K_MAX: float = 30.0  # 2026-05-23: was 20
    BB_PULLBACK_GATE_ENABLED: bool = True  # 2026-05-23: SWEEP WINNER — ΔSharpe +0.0056 vs baseline, only positive arm
    BB_PULLBACK_GATE_TF: str = '15m'
    BB_PULLBACK_GATE_LONG_MAX: float = 0.30
    BB_PULLBACK_GATE_SHORT_MIN: float = 0.70
    BB_SQUEEZE_COOLDOWN: float = 300.0  # Seconds between BB squeeze entries per symbol
    BB_SQUEEZE_ENABLED: bool = True  # Master toggle for BB squeeze breakout entries
    BB_SQUEEZE_ENTRY_ENABLED: bool = False  # Enter when Bollinger bands compress (< threshold) ; DEAD_CONFIRMED (priority 90/100) — no plausible wiring site found 20260416
    BB_SQUEEZE_EXIT_ENABLED: bool = False  # Exit squeeze release - split from master to avoid double count 2026-09-04
    # === PER-ROW FILTERS (2026-09-07) -- per-switch filter settings from TEMPLATE.xlsx L:BI ===
    # Maps switch name -> {filter_name: value, ...} for filters that are opportune for that switch.
    # Filled from TEMPLATE.xlsx per-row L:BI delta columns and FILTER_DICTIONARY_V8 gated logic.
    # Live trading (ez_manage/tradier_manage) checks this first via get_per_switch_filter(switch, filter) before global getattr.
    # Example: {"WT_15M_BOUNCE_OPEN_ENABLED": {"WT_15M_BOUNCE_BB_MIN": 0.1, "WT_15M_BOUNCE_LOW_1H_GT_PREV": True}}
    PER_ROW_FILTERS: dict = field(default_factory=dict)

    def get_per_switch_filter(switch: str, filter_name: str, default=None):
        """Return per-switch filter value if exists, else global default."""
        try:
            per = PER_ROW_FILTERS.get(switch, {})
            if filter_name in per:
                return per[filter_name]
        except Exception:
            pass
        return default

    def set_per_switch_filter(switch: str, filter_name: str, value):
        """Set per-switch filter (used by v12_pilot_sheet_runner when promoting pos delta)."""
        if switch not in PER_ROW_FILTERS:
            PER_ROW_FILTERS[switch] = {}
        PER_ROW_FILTERS[switch][filter_name] = value

    TRADEABLE_KEYS_MANDATORY_ENABLED: bool = True  # added

    MOMENTUM_WATCHDOG_ENABLED: bool = True  # added

    SBA_BOUNCE_ENABLED: bool = True  # vectorizable parity 2026-09-08: mirror QuickConfig (SBA bounce confluence BB+K+WT)

    COUNTER_TREND_ADD_BLOCK_ENABLED: bool = True  # vectorizable parity 2026-09-08: block OPEN/AUGMENT/REENTRY against wt1_1h (same default as QuickConfig/config.py)
    COUNTER_TREND_SMA200_BYPASS_ENABLED: bool = True  # vectorizable parity 2026-09-08: trend-aligned bypass for sma_200_15m + 1h structure (same default as QuickConfig/config.py)

    CYCLE_TP_ENABLED: bool = False  # added

    STALE_HOLD: bool = True  # added

    QUICK_REDUCE_TECHNICAL_ONLY: bool = True  # added

    BLACKLIST_SYMBOLS: List[str] = field(default_factory=list)  # added

    DC_HOPELESS_ENABLED: bool = False  # vectorizable parity 2026-09-08: alias for DC_HOPELESS_EXIT_ENABLED parity (live default False)

    DD_BOUNCE_ENABLED: bool = False  # vectorizable parity 2026-09-08: double-down bounce while losing (live default False per config.py)

    FIN_ADVISORY_CONSUMER_ENABLED: bool = False  # vectorizable parity 2026-09-08: plain parity mirror for config.py (live default False)

    MARKET_CRASH_THRESHOLD_PCT: float = 0.0  # vectorizable parity 2026-09-08: index crash blanket disabled when 0 (live default 0)

    MARKET_JUMP_THRESHOLD_PCT: float = 0.0  # vectorizable parity 2026-09-08: companion to MARKET_CRASH

    TOP_OF_RANGE_BLOCK_ENABLED: bool = True  # vectorizable parity 2026-09-08: top-of-range block (config.py True) alphabetical parity

    UNIVERSAL_NOLOSS_GATE: bool = False  # vectorizable parity 2026-09-08: blanket noloss OFF LIMITS per user (config.py False)

    WT_EXHAUST_ENABLED: bool = False  # vectorizable parity 2026-09-08: alias for WT_EXHAUST_EXIT_ENABLED (live default False)

    WT_PERCENTILE_ENABLED: bool = False  # vectorizable parity 2026-09-08: alias for WT_PERCENTILE_EXIT_ENABLED (live default False)

    SCALP_V3_ENABLED: bool = False  # added

    STRICT_VEC_PARITY_MODE: bool = False  # added
