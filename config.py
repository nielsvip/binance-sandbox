# ═══════════════════════════════════════════════════════════════════════
# SWEEP REFERENCE: data/sweep_tiers.json — prioritized list of ALL sweepable
# switches with ranges, tiers (TIER_1→TIER_3), and notes. Agents: read that
# file before starting any parameter sweep. TIER_1 first, skip DEAD/FIXED.
# ═══════════════════════════════════════════════════════════════════════
import asyncio
import json
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
    _REGIME_OVERRIDES: ClassVar[Dict[str, Dict[str, object]]] = {}
    _REGIME_REDIS_CACHE: ClassVar[Dict[str, Dict]] = {}
    _REGIME_REDIS_TS: ClassVar[float] = 0.0
    _REGIME_LOG: ClassVar[list] = []

    def __hash__(self):
        return id(self)

    BASE_TF: str = "3m"  # parity 2026-08-17: vector->live (was vector-only)
    BB_PCTB_ENTRY_ENABLED: bool = False  # parity 2026-08-17: vector->live (was vector-only)
    COOLDOWN_BARS: int = 3  # parity 2026-08-17: vector->live (was vector-only)
    ENTRY_SCORE_THRESHOLD: float = 18.0  # parity 2026-08-17: vector->live (was vector-only)
    MIN_HOLD_BARS: int = 10  # parity 2026-08-17: vector->live (was vector-only)
    MODE: str = "crypto"  # parity 2026-08-17: vector->live (was vector-only)
    STOCH_ENTRY_ENABLED: bool = False  # parity 2026-08-17: SWITCH tested per_sym + 7D crypto+stocks (bypass removed)
    WT_ENTRY_ENABLED: bool = False  # parity 2026-08-17: SWITCH tested per_sym + 7D crypto+stocks (bypass removed)
    REENTRY_PULL1_ENABLED: bool = False  # parity 2026-08-17: vector->live (was vector-only)
    REENTRY_PULL2_ENABLED: bool = False  # parity 2026-08-17: vector->live (was vector-only)
    REENTRY_PULL3_ENABLED: bool = False  # parity 2026-08-17: vector->live (was vector-only)
    REENTRY_PULL4_ENABLED: bool = False  # parity 2026-08-17: vector->live (was vector-only)
    SATOSHIT_ENTRY_ENABLED: bool = False  # parity 2026-08-17: vector->live (was vector-only)
    WT_EXIT_MIN_TFS: int = 2  # parity 2026-08-17: vector->live (was vector-only)
    WT_VEL_DECAY_EXIT_ENABLED: bool = False  # parity 2026-08-17: vector->live (was vector-only)
    WT_VEL_DECAY_THRESHOLD: float = 1.0  # parity 2026-08-17: vector->live (was vector-only)
    MIN_POSITION_SIZE: float = 1.0
    # 1/50 RULE: No single position > 2% of total capital ($1k crypto = $20/pos max)
    MAX_POSITION_SIZE: float = 200.0  # 2026-03-30: 1/50 of $1k. Was $800 (80% of capital = suicide).
    MAX_POSITION_SIZE_BTC: float = 2000.0  # 2026-03-30: Same rule for BTC. Was $6000.
    MAX_POSITION_SIZE_MEN: float = 200.0  # 2026-03-30: Same. Was $1200.
    MAX_POSITION_SIZE_FIN: float = 200.0  # 2026-03-30: Same. Was $4000.
    HIGH_GAIN_AUGMENTATION_MIN_SIZE = 50  # BACKTEST_CHANGE_25: was 200. Lower threshold lets more winners get augmented

    MAX_ORDER_VALUE: float = 200.0  # 2026-03-30: 1/50 rule. Was $280.
    MAX_ORDER_VALUE_MEN: float =60.0  # Was $240.
    MAX_ORDER_VALUE_FIN: float = 60.0  # Was $120.
    START_POSITION_SIZE: float = 14.0  # Start size per entry. Capped by MAX_POSITION_SIZE.

    # PnL Deterioration Settings
    PNL_DECAY_START_HOURS: int = 1  # HOURS
    PNL_DECAY_COMPLETE_DAYS: int = 5  # DAYS

    PNL_DECAY_FINAL_PERCENTAGE: float = 0.1  # Keep 10% after full decay
    MAX_DECAY_START_HOURS = 1
    MAX_DECAY_COMPLETE_DAYS = 7
    MAX_GAIN_DECAY_COMPLETE_DAYS = 7
    # PNL_PERFORMANCE_WINDOW_HOURS: int =     48   # Window for recent performance calculation
    ZERO_CONFIRMATION_THRESHOLD_WS: int = 1  # Single WS positionAmt=0 is authoritative — was 2, caused 81 phantom positions
    ZERO_CONFIRMATION_THRESHOLD_API: int = 2  # 2026-04-27 owner: tightened 5→2 ("twice in a row triggers zero"). Stricter phantom detection. Trade-off: higher false-positive rate (~22% restoration historically), but stale ghosts cost augment cycles + HAIKU_STALE block storms.
    MIN_PERC_FROM_SMA_1: float = 1.0 / 100  # SMA_1
    MIN_PERC_FROM_SMA_15: float = 3.0 / 100  # SMA_15
    MIN_GAIN: float = 3.0  # was 5.0 (too late, near TP). 3.0% = 2.8% buffer after 50% aug, survives 1.5% reversal. Tiered: 0.4x=1.2% pullback, 0.5x=1.5% reduced, 1x=3.0% full
    # 2026-04-28 user: "make sure commissions are included in the sell before loss !!!! that is the real bleed"
    # Maker fee is 0.02% per side = 0.04% round-trip. Taker is 0.05% per side = 0.10% round-trip.
    # Set buffer to 0.10% to cover worst case (taker close + slippage). Closing below this = NET LOSS.
    COMMISSION_BUFFER_PCT: float = 0.08
    REENTRY_PRICE_IMPROVE_PCT: float = 0.08  # require 0.10% price improvement vs exit before reentry
    AUGMENT_ONLY_WHEN_PROFITABLE: bool = True  # URGENT_FIX: NEVER augment a position with gain < 0
    MAX_AUGMENTS_PER_POSITION: int = 999999  # USER 2026-05-30: NO cap — augment a million times as long as gain > 0.5*MIN_GAIN (and wt1_1h aligned via add-block)  # 2026-05-21 USER MANDATE: position must compound to 20x start_position_size when price moves favorably. Was 3 ("stop piling into losers") — but combined with disabled-BREAKEVEN_GAIN_EROSION (line 2559) and MTF_ATR_TRAIL=2x protection (line 1363), augments only continue when price is moving in our favor. ROLLBACK: 3.
    BEAR_MARKET_MODE: bool = True  # URGENT_FIX: When True, favor shorts over longs
    # MIN_PROFIT_FOR_PROFIT_TAKING: float =   0.4

    EXTREME_MODE: bool = False
    LIGHT_MODE: bool = False
    MARKET_MODE: str = "NORMAL_MODE"
    REV_MODE: bool = False

    SERVICE_STOP = True
    SERVICE_REDUCE = True
    MANAGE_REDUCE = True
    HEDGE_MODE: bool = False  # 2026-08-14 REVERTED per user: hedgeengine too faulty, first round this weekend WITHOUT hedging, hedge fix via dedicated agent 900*900*160: hedge was dead and missing from >2000 sweep, retesting all HEDGE_* combos, replaced by MTF compound exit (2x ATR15m trail + GR/WT/DC/BB rejection). All hedge code paths gated by config.HEDGE_MODE → False short-circuits them. ROLLBACK: True restores hedge protection.
    SANDBOX_MODE: bool = False
    SANDBOX_ACCOUNTS: List[str] = field(default_factory=lambda: ["sbx"])

    # SERVICE_STOP=                           True
    # SERVICE_REDUCE=                         True
    # MANAGE_REDUCE=                          True
    # HEDGE_MODE:bool =                       False

    SCALP_ACCOUNTS = []  # 2026-04-16: inf = spike-fade momentum, needs fast scalp. ang/men hold long-term — wrong fit.
    # ───────────────────────────────────────────────────────────────────────────
    # SCALP_MODE — HTF Breakout Scalper V2 (2026-04-09)
    # Entry: price > dc_high_15m_prev AND dc_high_1h_prev (LONG), mirror SHORT.
    # Exit: configurable variant (V1_WT_CONFIRM is sweep winner).
    #
    # SWEEP RESULTS (2026-04-16, 48 sym, 30d, post-commission):
    #   V1_WT_CONFIRM  hold=15m  N=4506  net=+634%  mean=+0.141%  WR=66.8%  Sharpe=107
    #   REDZONE_K90    hold=15m  N=4416  net=+553%  mean=+0.125%  WR=60.9%  Sharpe=97
    #   LH_LL_15m      hold=15m  N=4414  net=+493%  mean=+0.112%  WR=64.7%  Sharpe=68
    #   hold=60m universally worse — inf needs rapid exits, not holding.
    #   WT_DC_SCORER underperforms — multi-TF consensus too slow for spike-fade.
    # ───────────────────────────────────────────────────────────────────────────
    # ═══ SCALP_V2 — PRIORITY P0 (test first, week 1) ═══════════════════════════
    SCALP_MODE: bool = False               # P0: ON for inf. V8 showed -0.30 Sharpe BUT that was with ISOLATE=False (main exits interfered). Now ISOLATE=True + inf excluded from hedging.
    SCALP_REDUCE_ENABLED: bool = False     # 2026-05-30 USER: OFF. The AdvancedSignalRater.rate "SCALP_REDUCE" profit-protect reduces (TIGHT_LEASH_PROFIT_SAVE/Quick_Profit/DECENT_GAIN_PROTECT) cut winners early — gated at queue_trade_action. Breakeven-lock (+0.5%->+0.02%) remains the sanctioned profit protection. ROLLBACK: True.
    RULE_B_3M_EXIT_ENABLED: bool = True     # 2026-05-30 USER: rewire the reduce onto the VALIDATED Rule B signal — exit a LONG on a 3m lower-low+lower-high, a SHORT on a 3m higher-high+higher-low (the trend turning against the position). Fires a REDUCE via the existing exit path; still NO-LOSS-gated (locks profit on the turn, losers held by R1/R2). Replaces the winner-cutting SCALP_REDUCE with the right trigger. ROLLBACK: False.
    QUICK_REDUCE_TECHNICAL_ONLY: bool = True  # 2026-06-02 USER MANDATE: DISABLE the QUICK_REDUCE stochastic/euphoria traps (AB_S200/EUPHORIA_TIGHTEN/Overbought_safety/SIMPLE_TP/NO_PROFIT 'Get Out'/Stagnant — hardcoded since 2026-03-03, never approved, never backtested, cut 76% of reduces with no technical breakdown incl 164 above-sma200 winners). When True, a reduce/close from rate(is_exit) only fires if the reason carries a SANCTIONED TECHNICAL token (R1 dc_low4 / R2 WT-vel / MTF-ATR-trail / DC break / MTF / HTF/ALL_TF against / RULE_B 3m struct / FAST_CUT_LOSS / PARTIAL-PROFIT-LOCK / HEDGE_FAILED); pure stochastic reduces are suppressed (logged QUICK_REDUCE_TRAP_SUPPRESSED). Gate: ez_positions_quick.py ~14538. ROLLBACK: False.
    PARITY_COMPARISON_MODE: bool = False  # 2026-06-02 USER MASTER SWITCH (crypto side, for symmetry). Default False = NORMAL LIVE (all gain-augmenting live-only signals ON). Set True ONLY during a live↔backtest parity A/B to suppress live-only augmenting behavior at once so live==backtest. AFTER confirming parity, set back to False. (Stocks equivalent in config_tradier.py gates the tradier strategy loop; crypto augmenting-signal gating to be wired here as needed.)
    SCALP_V2_VARIANT: str = "V1_WT_CONFIRM"  # P0: sweep winner Sharpe=107. DO NOT change until V8 validates
    SCALP_V2_DC_HTF_REQUIRE_ALL: bool = True  # P0: quality gate. True = fewer but better. Keep True.
    SCALP_V2_MAX_HOLD_MINUTES: float = 15.0   # P0: sweep-proven, 60m universally worse for inf
    SCALP_V2_DC_HTF_LIST: list = field(default_factory=lambda: ["15m", "1h"])  # P3: adding "4h" didn't improve
    SCALP_V2_MAX_CONCURRENT: int = 5      # P3: fine for now, only tune if hitting position limits
    SCALP_V2_REENTRY_COOLDOWN_S: int = 300  # P3: 5 min reasonable, shorter = more chop
    SCALP_V2_ISOLATE: bool = True          # 2026-04-16: ON for live — V2 positions ONLY use V2 exits, main pipeline exits skip them. V8 -0.30 Sharpe was from main exits trampling V2 positions.
    # 2026-04-16: Scalp entry mode — sweep knob, NOT yet backtested per-account.
    # "breakout" is current live. "pullback" added as variant, must backtest inf before default change.
    SCALP_V2_ENTRY_MODE: str = "breakout"  # "breakout" (live) | "pullback" (tested only)
    # ═══ SCALP_V2 SECONDARY EXITS — PRIORITY P1/P2 (test after P0 stable) ════
    SCALP_V2_REDZONE_EXIT: bool = True     # P1: #2 in sweep (Sharpe 97). Catches exits V1_WT misses. +6 extra exits in smoke test.
    SCALP_V2_REDZONE_K_THRESHOLD: int = 90 # P1: sweep winner=90. Try 80 only after 90 tested.
    SCALP_V2_LH_LL_EXIT: bool = True       # P2: #4 in sweep (Sharpe 68). +23 extra exits in smoke test. 15m structure break.
    SCALP_V2_LH_LL_TF: str = "15m"        # P2: sweep winner=15m. 1h too slow, 3m too noisy.
    # ═══ SCALP_V3 — ULTRA-SHORT BAR-BASED SCALPER (2026-04-22) ══════════════════
    # Entry: latest 1m bar HH AND HL + K oversold context + HTF runway + volume spike.
    # Exit: latest bar LL/LH + K overextension context (NO crossunders — bar IS signal).
    # Reentry: immediate if k_15m still rising; else wait for clear 15m bounce.
    # UNPROVEN. Defaults OFF. Path: 1m backtest (~25h) → 3m-proxy longer → forward paper on inf → live.
    # 2026-04-22 user-authorized live flip with pos_min_qty cap
    SCALP_V3_ENABLED: bool = False        # USER 2026-05-30: SCALP_V3 PROHIBITED (counter-trend churn). Stays off.
    SCALP_V3_ACCOUNTS: list = field(default_factory=lambda: [])  # USER 2026-05-30: emptied — no account runs SCALP_V3.
    SCALP_V3_MAX_CONCURRENT: int = 8              # max open V3 positions per account
    SCALP_V3_POSITION_CAP_USD: float = 20.0       # 2026-04-23: bumped 10→20 (Binance min $5, want >$10 after any residual cuts)
    # --- ENTRY (LONG; SHORT mirror auto-inverted in scalp_v3.py) ---
    SCALP_V3_ENTRY_K_1M_MAX: int = 20       # 2026-04-23 20k sweep best: 15-40 varies, 20 median of top 20
    SCALP_V3_ENTRY_K_3M_MAX: int = 40
    SCALP_V3_ENTRY_K_15M_MAX: int = 65
    SCALP_V3_ENTRY_K_1H_MAX: int = 80
    SCALP_V3_ENTRY_K_4H_MAX: int = 85
    SCALP_V3_ENTRY_REQUIRE_K_TURNUP: bool = False
    SCALP_V3_ENTRY_BAR_1M_REQUIRE: str = "HH_AND_HL"
    SCALP_V3_ENTRY_BAR_3M_REQUIRE: str = "HH"
    SCALP_V3_ENTRY_VOL_SPIKE_MULT: float = 1.25
    # TF mode — SWEEP KNOB. Which TF bar(s) required:
    #   "1M_ONLY"        — 1m bar + 1m K only (no 3m bar needed)
    #   "3M_ONLY"        — 3m bar + 3m K only (no 1m bar needed) — backtest-friendly
    #   "1M_AND_3M"      — both must fire (strictest; default)
    #   "3M_CONFIRMS_1M" — 1m fires, 3m must also confirm
    SCALP_V3_ENTRY_TF_MODE: str = "3M_ONLY"   # 2026-04-24 winners_refined sweep: 3M dominates top 10 (9/10); prior "1M_AND_3M" was pre-sweep
    SCALP_V3_EXIT_TF_MODE: str = "15M_ONLY"   # 2026-04-24 winners: 9/10 top by Sharpe use 15M_ONLY; "ANY" is noisier
    # --- EXIT (bar + K-as-context; NO crossunders) ---
    SCALP_V3_EXIT_1M_K_MIN: int = 90        # 2026-04-23 20k sweep: top variants 85-98, 90 dominant
    SCALP_V3_EXIT_1M_BAR: str = "LL_AND_LH"
    SCALP_V3_EXIT_3M_K_MIN: int = 95
    SCALP_V3_EXIT_3M_BAR: str = "LL_OR_LH"
    SCALP_V3_EXIT_15M_K_MIN: int = 95
    SCALP_V3_EXIT_15M_BAR: str = "LL_OR_LH"
    SCALP_V3_MAX_HOLD_MIN: float = 5.0       # 2026-04-24 winners_refined: all top 10 by Sharpe used max_hold=5. Was 10.0.
    SCALP_V3_STALL_GAIN_MAX_PCT: float = -0.1  # 2026-04-23 20k sweep: -0.1 in 100% of top 20
    SCALP_V3_STALL_ENABLED: bool = False  # 2026-04-24: DISABLED — 177/180 paper exits hit STALL with 0% WR (-73.81% total). Only 15M_BAR exit wins. Set True only if future sweep finds a STALL variant that actually wins.
    # --- REENTRY ---
    SCALP_V3_REENTRY_REQUIRE_BOUNCE_IF_15M_FALLING: bool = True
    # Reentry bounce definition — SWEEP KNOB:
    #   "3M_BAR_ONLY"       — only 3m HH+HL (LONG) / LL+LH (SHORT) clears the block
    #   "K15M_ONLY"         — only k_15m oversold+turnup clears
    #   "3M_BAR_OR_K15M"    — either clears (default, most permissive)
    #   "3M_BAR_AND_K15M"   — both required (strictest)
    SCALP_V3_REENTRY_BOUNCE_MODE: str = "3M_BAR_OR_K15M"
    SCALP_V3_REENTRY_BOUNCE_BAR_3M: str = "HH_AND_HL"  # LONG convention; SHORT mirrors
    SCALP_V3_REENTRY_BOUNCE_K_15M_MAX: int = 25
    SCALP_V3_REENTRY_COOLDOWN_S: int = 300  # 2026-07-08 GAINMO anti-churn: 0→300 (match V2; churn law: >100 trades/sym/yr collapses pool_sharpe to <0.05)
    # --- RANKING BOOST (ez_rankings.py; feeds final_score_raw_st → symbols_inf_*) ---
    # 2026-04-22 user-authorized: enabled with small weight — paper test observability.
    SCALP_V3_BOOST_ENABLED: bool = True
    SCALP_V3_BOOST_WEIGHT: float = 0.2
    SCALP_V3_BOOST_LOOKBACK_BARS_3M: int = 5
    SCALP_V3_BOOST_VOL_Z_MIN: float = 1.5
    # --- SHORT-specific entry gate: "fall off cliff then bounce to dc_basis then fail" pattern ---
    # Reference: wt_dc_delta.py:876-894 BASELINE_BOUNCE_SHORT (live on ang). Cruder here:
    # require that last N minutes showed ≥X% drop — meaning price has already fallen hard
    # and current elevated-K bar+structure = bounce-fail entry.
    SCALP_V3_SHORT_REQUIRE_RECENT_DUMP: bool = True
    SCALP_V3_SHORT_RECENT_DUMP_PCT: float = 3.0
    SCALP_V3_SHORT_RECENT_DUMP_LOOKBACK_MIN: int = 60
    SCALP_V3_SHORT_ENTRY_K_3M_MAX: int = 15   # 2026-04-25: SHORT fires when k_3m > 85 (sweep winner). Long uses shared ENTRY_K_3M_MAX=40 (k<40).
    SCALP_V3_SHORT_ENTRY_K_15M_MAX: int = 30  # SHORT fires when k_15m > 70
    SCALP_V3_SHORT_ENTRY_K_1H_MAX: int = 40   # SHORT fires when k_1h > 60
    # Dedicated V3 scan loop (2026-04-23): ranks tradeable symbols by sentiment
    # divergence vs global market score. Fastest risers → LONG, fastest fallers → SHORT.
    SCALP_V3_SCAN_INTERVAL_SEC: float = 10.0     # 2026-04-24: 30→10 — protective exits need to fire fast on LH/LL
    SCALP_V3_SCAN_TOP_N: int = 8                 # 2026-04-23 evening: 5→15→8 (15 caused weight overrun)
    SCALP_V3_SCAN_MIN_DIVERGENCE: float = 0.3    # 2026-04-23 evening: 0.3→0.05→0.3 (0.05 flooded API)
    SCALP_V3_DIAG_LOG: bool = True               # set False once firing confirmed to reduce log noise
    SCALP_V3_EXIT_PROFIT_ONLY: bool = False      # 2026-04-26 SBL test flag: when True, V3 technical exits only fire while gain>0 (lock profit, never close at loss). Default False = unchanged. Shadow A/B variants override to True.
    SCALP_V3_SIDE_MODE: str = "BOTH"              # 2026-04-27: REVERTED SHORT_ONLY → BOTH. SHORT_ONLY was set on a 12sym×6mo backtest (best pool_sharpe 0.63 — below 1.0 trash floor and violates CLAUDE.md ≥48-sym/≥1-yr published-Sharpe rule) plus a 330-cycle shadow during a flat/bearish micro-window. Regime flipped bullish (>20% rally); SHORT_ONLY shorted into rallies (1000BONKUSDC 03:04 cluster) and refused obvious LONG breakouts (e.g. WIFUSDC). The original comment itself said "revert to BOTH if regime flips bullish" — done.
    SCALP_V3_SCAN_BYPASS_GATES: bool = False     # 2026-04-26: OFF after V3 trend-follow rewrite. Bypass let the scanner open SHORTs into rallies (XTZ/KSM/AWE on 2026-04-25), which is exactly the mean-rev pattern the rewrite removed. Scanner now respects new trend-follow K/WT/HTF gates.
    # 2026-04-27 K-FRESHNESS WINDOW (anti-extreme-entry). Pre-fix audit: mean k_3m at
    # SHORT_TREND fire was 24.6 (26% at k<10 = falling knife) and at LONG_TREND fire
    # was 86.7 (61% at k>90 = buying the top). Restrict TREND entries to the mid-range
    # zone right after a 50-line cross — fresh momentum with room to run.
    SCALP_V3_K_FRESH_LO: float = 15.0              # 2026-04-27 relaxed 25→15: catch SHORTs earlier in down-momentum
    SCALP_V3_K_FRESH_MID_LO: float = 40.0          # 2026-04-27 relaxed 50→40: LONG fires before full cross of 50 (catch the bounce)
    SCALP_V3_K_FRESH_MID_HI: float = 60.0          # 2026-04-27 relaxed 50→60: SHORT fires before full break of 50 (catch the rejection)
    SCALP_V3_K_FRESH_HI: float = 85.0              # 2026-04-27 relaxed 75→85: LONG fires later in up-momentum
    # 2026-04-27 K-EXTREME + OB-RESISTANCE EXIT (user directive: close as soon as K is
    # high/low and price runs into the wall). Fires inside _scalp_v3_protective_exits
    # which runs every 10s. Pairs with PEAK_GIVEBACK loosening to lock profits faster.
    SCALP_V3_K_OB_EXIT_ENABLED: bool = True
    SCALP_V3_K_OB_EXIT_K3M_HI: float = 80.0        # LONG: close if k_3m >= this AND ask wall close
    SCALP_V3_K_OB_EXIT_K3M_LO: float = 20.0        # SHORT: close if k_3m <= this AND bid wall close
    SCALP_V3_K_OB_EXIT_K15M_HI: float = 80.0       # LONG: also k_15m gate
    SCALP_V3_K_OB_EXIT_K15M_LO: float = 20.0       # SHORT: also k_15m gate
    SCALP_V3_K_OB_EXIT_WALL_PCT: float = 0.5       # treat wall as "close" if within this % of price
    # 2026-04-27 V3 FAST PPL — process_position runs PPL but cadence misses fast V3
    # peaks (5min holds, gain crosses 0.5% and back within process_position interval).
    # _scalp_v3_protective_exits runs every 10s and now fires PPL on V3 directly.
    SCALP_V3_FAST_PPL_ENABLED: bool = True
    SCALP_V3_FAST_PPL_GAIN_PCT: float = 0.5        # mirror PARTIAL_PROFIT_LOCK_GAIN_PCT
    # ez_rankings outlier detector: boosts symbols whose 15-min return deviates from
    # the market median. Positive z-score → top_winners_st → symbols_inf_long_list
    # (auto-added to tradeable_keys). Negative → symbols_inf_short_list. This is
    # what routes user's chart-visible outliers (THETA, ZEC, COMP, DOT, CRV etc) into
    # the live pipeline automatically, without manual tradeable_keys edits.
    SCALP_V3_OUTLIER_ENABLED: bool = True
    SCALP_V3_OUTLIER_WEIGHT: float = 3.0         # legacy; unused after 2026-04-23 rewrite to pure injection
    SCALP_V3_OUTLIER_MIN_Z_LONG: float = 1.5     # min z-score (vs median) for a LONG outlier inject
    SCALP_V3_OUTLIER_MIN_Z_SHORT: float = -1.5   # max z-score for a SHORT outlier inject
    SCALP_V3_OUTLIER_MAX_INJECT: int = 15        # cap how many get appended per side
    # ── TRENDER + BREAKOUT INJECTION (2026-05-09) ───────────────────────────────────
    # Two new injectors that complement the velocity-only funnels (15m gain_score,
    # weighted_gains_st, V3 outlier z-score). Both default to SHADOW_LOG only — they
    # write candidates to data/inject_shadow_log.jsonl WITHOUT modifying inf lists.
    # Flip *_LIVE=True after backtest+live audit confirms picks beat the existing set.
    # NO top-N cap — quiet markets inject 0, moving markets inject 30+.
    #
    # TRENDER: sustained + linear + size-credible grinders. Reward symbols that
    # quietly trend up across 1h/4h/24h on real volume with low drawdown.
    TRENDER_INJECT_SHADOW_LOG: bool = True
    TRENDER_INJECT_LIVE: bool = False
    TRENDER_LIN_MIN: float = 0.65                # avg |Pearson r| floor across {15m,1h,4h,D} — 2026-05-09 audit: 0.55→0.65
    TRENDER_RET24_MIN_PCT: float = 2.5           # absolute 24h return floor (% — kills drift) — 2026-05-09 audit: 1.5→2.5
    TRENDER_QV_FLOOR_USD: float = 25_000_000     # 24h quote-volume floor (kills pump shells & micro-caps)
    TRENDER_QV_ANCHOR_USD: float = 50_000_000    # log10(qv/anchor) → score multiplier
    TRENDER_QV_MAX_BOOST: float = 1.5            # cap so megacaps don't auto-win
    TRENDER_DD_RATIO_MAX: float = 0.40           # peak→now drawdown / total return; kills spike-then-fade
    #
    # BREAKOUT: cross-sectional 4h-return outliers — symbols breaking up/down out of
    # the congested cluster where most coins move in tandem (the ZEC/TON pattern).
    BREAKOUT_INJECT_SHADOW_LOG: bool = True
    BREAKOUT_INJECT_LIVE: bool = False
    BREAKOUT_MAD_MULTIPLIER: float = 2.0         # threshold = median ± N×MAD of cross-sectional 4h returns
    BREAKOUT_MIN_RET_PCT: float = 5.0            # absolute floor regardless of band width — 2026-05-09 audit: 3.0→5.0 (best LONG fwd_4h +0.77%, pos% 56.5%)
    BREAKOUT_MAX_DD_RATIO: float = 0.5           # looser than TRENDER (breakouts spike) but still cap fades
    # ── VEC-ENGINE-VALIDATED ENTRY GATES (2026-05-09 publishable on 56sym×1.17yr) ─────
    # Boolean-AND condition gates from vec_trender_breakout.py, all default OFF for
    # safe rollout. Flip *_GATE_ENABLED=True per account when paper-validated.
    # Two-account split per user: fin=mean-rev (MR5_L+MR3S_S), men=momentum (MOM5+TRENDER_L+MOM4S_S).
    # Validated Sharpe (sign-corrected for SHORT): MR3S 1.05, MR5 0.95, MOM4S 0.78, MOM5+TRENDER 0.62.
    MR5_L_GATE_ENABLED: bool = False
    MR5_L_ACCOUNTS: list = field(default_factory=lambda: ["fin"])
    MR3S_S_GATE_ENABLED: bool = False
    MR3S_S_ACCOUNTS: list = field(default_factory=lambda: ["fin"])
    MOM5_TRENDER_L_GATE_ENABLED: bool = False
    MOM5_TRENDER_L_ACCOUNTS: list = field(default_factory=lambda: ["men"])
    MOM4S_S_GATE_ENABLED: bool = False
    MOM4S_S_ACCOUNTS: list = field(default_factory=lambda: ["men"])
    # When True, log gate evaluation to data/vec_gates_shadow_log.jsonl WITHOUT enforcing
    # entry decisions (pipeline behavior unchanged). Set False once gates are paper-validated
    # and you flip _GATE_ENABLED=True for actual enforcement.
    VEC_GATES_LOG_ONLY: bool = True
    # Orderbook-primary entry signals (2026-04-23 late): ez_orderbook.py writes
    # ob_long_score / ob_short_score (0..100). Scanner opens on OB score instead of
    # waiting for K/candle confirmation.
    SCALP_V3_OB_REQUIRED: bool = True            # if True, orderbook signal is required (no pure-divergence opens)
    SCALP_V3_OB_MIN_SCORE: float = 70.0          # 2026-04-24: 45→70. Winners_refined + paper OB-score use 70 threshold for composite gate.
    # Augment-on-winner (2026-04-24 user directive): pyramid into V3 positions
    # that are in profit > SCALP_V3_AUG_MIN_GAIN with pullback signals.
    SCALP_V3_AUG_ENABLED: bool = True
    SCALP_V3_AUG_MIN_GAIN: float = 2.0           # 2026-04-24: 0.5→2.0 — real cushion before adding
    SCALP_V3_AUG_COOLDOWN_SEC: float = 180.0     # 2026-04-24: 60→180s — let adds breathe
    SCALP_V3_AUG_MAX_FRAC_OF_POS: float = 0.5    # 2026-04-24: cap aug at half of current pos notional
    # Break-even protection for augmented positions (2026-04-24): if a V3 position
    # was augmented (it peaked high enough to merit adding) and then gave back all
    # the gains, close 100% at +0.1% before it slips into a loss.
    SCALP_V3_AUG_BE_STOP_ENABLED: bool = True
    SCALP_V3_AUG_BE_STOP_PCT: float = 0.1        # close when gain drops below this (% after-fee)
    # Protective exit (2026-04-24 MOVR incident): close V3 positions IMMEDIATELY on
    # any reversal signal while in gain — before the gain disappears.
    SCALP_V3_PROTECTIVE_EXIT_ENABLED: bool = True
    SCALP_V3_PROTECTIVE_K_DROP_MIN: float = 5.0  # K-point drop threshold (1m or 3m)
    SCALP_V3_MAX_LOSS_PCT: float = -1.5           # 2026-04-25: hard max loss cap (% gain). Bypasses S/R guard. Uses SCALP_V3_OPEN_PROTECTIVE bypass in UNIVERSAL_NOLOSS_GATE.
    # Support/resistance check before closing (2026-04-24 user): never close a V3
    # position when price is at DC support (LONG) / resistance (SHORT) — bounce likely.
    # Entry-side flag: require S/R proximity on NEW V3 opens (soft filter, off by default).
    SCALP_V3_REQUIRE_SR_ON_ENTRY: bool = False
    # S/R guard tolerance + hold threshold (2026-04-26 fix: 1.5% was blocking ALL exits)
    SCALP_V3_SR_TOL_PCT: float = 0.5            # within X% of a DC/BB/SMA level = "at" it (was hardcoded 1.5 — too wide, blocks all exits)
    SCALP_V3_SR_HOLD_MIN_GAIN_PCT: float = 0.1  # only hold at S/R if gain > this. At a loss, close on technicals regardless of S/R.
    # 2026-04-27 — live entry-engine boost (defaults OFF for safety; user flips when ready).
    # Engines are pure-function additive triggers in entry_engine_{wt,stoch,dc,htf}.py — they
    # boost the existing entry score when they fire above LIVE_ENTRY_ENGINE_MIN_SCORE; they
    # NEVER block existing entries. Worst case is a few extra entries fire.
    LIVE_ENTRY_ENGINE_ENABLED: bool = True          # 2026-04-27: ALL ON per user. Master flag.
    LIVE_ENTRY_ENGINE_WT_ENABLED: bool = True       # convergent in sweep: wt_all3 dominates winners
    LIVE_ENTRY_ENGINE_STOCH_ENABLED: bool = True    # convergent: k4h<20 + kD<40 extreme oversold tier
    LIVE_ENTRY_ENGINE_DC_ENABLED: bool = True       # convergent: dc_x4h breakout in 90% of S1 top-3
    LIVE_ENTRY_ENGINE_HTF_ENABLED: bool = True      # convergent: sma200up_D + ha alignment
    LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED: bool = False  # 2026-05-19 PATH D: STDEV_D200_HIGH+WT_D_BEAR composite (68.5% WR, +41 bps fwd60 per signal-fire audit). Default OFF until Tier-2 multi-symbol backtest passes sample-floor + pool_sharpe>1.0.
    LIVE_ENTRY_ENGINE_MIN_SCORE: float = 0.5        # 2026-04-27: lowered 0.6→0.5 per user (way too few trades). Crypto reentries rarely cleared 0.6.
    LIVE_ENTRY_ENGINE_BOOST_SCORE: float = 8.0      # additive bump to entry score when an engine fires above threshold
    # 2026-04-27 — REENTRY engine hook: engines NEVER block reentries, only ADD size + tag reason.
    # 1.0 = pass-through (engines run, log +ENGINES tag for observability, NO size change). 1.5 = up to +50% size at max conviction.
    LIVE_ENTRY_ENGINE_REENTRY_SIZE_MULT: float = 1.0
    # ═══ WINNER TECHNIQUES from 2026-04-24 winners_refined sweep (Sharpe +1.298, WR 97.1%, DD 0.0%) ═══
    # Apply equally to LONG and SHORT (SIDE_MODE=BOTH preserved — sweep's LONG_ONLY bias was bull-market window).
    SCALP_V3_ATR_TP_MULT: float = 0.8        # exit when gain >= N × 3m-ATR%. 0.8 = winners median.
    SCALP_V3_ATR_SL_MULT: float = 0.0        # % stop = 0 (user rule: NO % stops, technicals only)
    SCALP_V3_VWAP_DEV_MIN_PCT: float = 0.3   # require price stretched ≥X% from 30-bar 3m-VWAP (mean-revert setup)
    SCALP_V3_BB_SQUEEZE_MAX_PCT: float = 2.0 # require 3m BB width ≤X% (compression before breakout)
    SCALP_V3_PIN_BAR_RATIO: float = 2.5      # require entry bar wick ≥N× body (rejection)
    SCALP_V3_PG_ARM_PCT: float = 0.5         # peak-giveback arms when gain reaches this %
    SCALP_V3_PG_GIVEBACK_PCT: float = 0.2    # exit when gain drops by this % from peak (after arm)
    SCALP_V3_MIN_TP_FOR_EARLY_EXIT: float = 0.2  # 2026-04-24: when gain >= this, any bar LH/LL fires exit (lock profit early)
    SCALP_V3_HTF_TREND_VEL_GATE: float = 3.0     # 2026-04-25: 5.0→3.0 (tighter bull-market guard; NOTUSDT incident was vel_4h=+15.3).
    SCALP_V3_USE_HA_3M: bool = True          # 2026-04-24 winners: 6/10 used Heikin-Ashi 3m bars for entry pattern
    SCALP_V3_USE_HA_1M: bool = False         # winners split; keep default off
    # ═══ ORDERBOOK COMPOSITE GATES (2026-04-24 NEW) ═══
    # Paper + live scanner both consult ez_orderbook.py `orderbook:<SYM>` for these.
    SCALP_V3_OB_MIN_LONG_SCORE: float = 70.0     # min ob_long_score (0..100) for LONG. Was 45.
    SCALP_V3_OB_MIN_SHORT_SCORE: float = 70.0    # min ob_short_score for SHORT.
    SCALP_V3_OB_WALL_TOO_CLOSE_PCT: float = 0.5  # skip entry if opposite-side wall within X% (resistance/support too close)
    SCALP_V3_OB_MIN_DIFF: float = 30.0           # 2026-04-25: require |ob_long - ob_short| >= this — no near-tied signals
    SCALP_V3_OB_DIV_CONFLICT_MAX_NET: float = 50.0  # 2026-04-25: when OB says SHORT but div>0.3 (bullish), need |net|>=50 to override conflict
    SCALP_V3_OB_VOID_EXTEND_HOLD: bool = True    # when void above (LONG) or below (SHORT), extend hold — skip non-STALL exits once
    # 2026-04-27 OB-LEADS-3M FLOW AGREEMENT GATE (OFF by default — A/B before flipping live):
    # The OB long/short scores are STATIC liquidity geometry (walls/voids/imb5), not directional flow.
    # Bleed pattern 2026-04-25: SHORT picks fired during rallies because wall-above + bid-void-below
    # repeatedly hit short_score=108-115 even as price rallied through the wall. Fix: require live momentum
    # to AGREE with OB-picked side using wt_velocity_1m / wt_velocity_3m as direction proxies (these are
    # actually live in hot_metrics; the 2026-04-26 scalp_v3_live.py rewrite used high_3m/low_3m which are
    # backtest-only fields and thus dead-coded the entry check). This gate kills the rally-fade SHORT pattern.
    SCALP_V3_OB_FLOW_AGREE_ENABLED: bool = True   # require live momentum to agree with OB side
    SCALP_V3_OB_FLOW_AGREE_MODE: str = "WT_VEL"   # "WT_VEL" (cheap, always live) | "OFI" (needs ez_orderbook running)
    SCALP_V3_OB_FLOW_VEL_3M_MIN: float = 0.0      # |wt_velocity_3m| must exceed this AND match OB side
    SCALP_V3_OB_FLOW_VEL_1M_MIN: float = 0.0      # |wt_velocity_1m| must exceed this AND match OB side
    SCALP_V3_OB_FLOW_K_AGREE: bool = True         # require k_3m vs k_3m_prev direction to match OB side
    SCALP_V3_OB_FLOW_OFI_MIN_ABS: float = 0.0     # min |ob_ofi_1s| when MODE=OFI; 0=any non-zero sign agreement
    # ═══ GLOBAL ORDERBOOK GATES (2026-04-24) — apply to any account listed. Enter at support, exit at resistance. ═══
    # Per-account opt-in list. If empty, no impact. To enable for ang+men: OB_ENTRY_GATE_ACCOUNTS=["ang","men"].
    OB_ENTRY_GATE_ACCOUNTS: list = field(default_factory=lambda: [])
    OB_ENTRY_MIN_LONG_SCORE: float = 0.0         # 0=disabled. Recommend 60 for entry-at-support.
    OB_ENTRY_MIN_SHORT_SCORE: float = 0.0        # 0=disabled. Recommend 60.
    OB_ENTRY_WALL_TOO_CLOSE_PCT: float = 0.0     # 0=disabled. 0.5 skips LONG when ask wall <0.5% above.
    # ═══ OB PRICE-DEFERRAL (2026-04-24) — overrides maker limit price toward OB walls. ═══
    # "Enter at support, exit at resistance" — when signal fires, if a wall exists within
    # MAX_DISTANCE_PCT, set the limit AT the wall and extend timeout so the limit has time to
    # fill. If price already at level (within AT_LEVEL_TOL), use normal maker. If no wall or
    # too far, normal maker (best_bid+tick for BUY, best_ask-tick for SELL).
    OB_PRICE_DEFER_ENABLED: bool = False                   # master switch
    OB_PRICE_DEFER_ACCOUNTS: list = field(default_factory=lambda: [])  # empty = all accounts when enabled
    OB_PRICE_DEFER_MAX_DISTANCE_PCT: float = 0.5           # 2026-04-24: 1.5→0.5. Realistic: 5min avg alt move is 0.2-0.5%; 1.5% rarely fills in TTL window → was degenerating to near-full-TTL no-fills.
    OB_PRICE_DEFER_AT_LEVEL_TOL_PCT: float = 0.05          # 2026-04-24: 0.1→0.05. Tighter "already at level" band — if wall within 0.05% it's effectively the current price.
    OB_PRICE_DEFER_TTL_SEC: float = 300.0                  # 5min hold — paired with 0.5% distance cap this gives ~1x of normal alt volatility to fill.
    # ═══ D4 BREAKOUT MULTI-LUNG — extracted from ez_breakout_agent.py (2026-04-16) ════
    # UNPROVEN: default OFF until sweep tier breakout_multi_lung delivers Sharpe > 2 on 48-crypto × 4yr.
    # Never flip ENABLED=True in live config without sweep proof — this switch is a validity-marker only.
    BREAKOUT_MULTI_LUNG_ENABLED: bool = False
    BREAKOUT_MULTI_LUNG_MODE: str = "AUGMENT"      # "AUGMENT" (OR with existing entries) | "REPLACE" (use only)
    BREAKOUT_MULTI_LUNG_TIER: str = "CRYPTO"       # "CRYPTO" | "MOVER" | "STOCK"
    BREAKOUT_MULTI_LUNG_COMPOSITE_INHALE: float = 0.20    # Entry threshold
    BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE: float = -0.10   # Exit threshold
    BREAKOUT_MULTI_LUNG_SLOW_LUNG_OVERRIDE: float = 0.15  # HTF veto threshold (slow lung still inhaling → don't exit)
    BREAKOUT_MULTI_LUNG_COOLDOWN_BARS: int = 4     # bars between multi-lung entries
    HEDGE_ACCOUNTS = []  # 2026-08-14 REVERTED: hedge OFF for first round weekend, fixes via agent: re-enable all crypto for 900*900*160 — flz was bleeding without hedge support; UNDERWATER_HEDGE_OR_CLOSE was logging but not firing. Cascade guards multi-layered (see history below).
    HEDGE_WEBHOOK_LOCK_TTL_SEC: float = 60.0  # USER 2026-05-10: lowered 3600→60 to match HEDGE_COMPLETED_LOCKOUT_SECONDS — re-hedge cycles must be allowed.
    # REVERTED 2026-05-18 18:30: 3600 had no sample-floor evidence (violates 2026-05-16 mandate). Restoring 2026-05-10 root-cause fix value 60. Isolated vec sweep queued.
    HEDGE_COMPLETED_LOCKOUT_SECONDS: int = 60  # REVERTED 2026-05-18 18:30 (was 3600 since 2026-05-17, was 60 since 2026-05-10)
    HEDGE_CLOSE_SCALP_MODE: bool = True  # 2026-04-24: user directive — close hedge on ANY 1m/3m LH/HH/LL/HL against hedge. Don't wait for wt_3m+wt_1h confirmation (too slow for scalp cycles). Original wt_3m+wt_1h gate still fires first if it matches.
    HEDGE_SCALP_MAX_AGE_MIN: float = 15.0  # 2026-04-25 Rule C: losing hedge stuck >15min → close (prevents dual-losing pair like WIFUSDC -0.62%/-0.25%).
    SCALP_V3_PEAK_GIVEBACK_PCT: float = 0.15  # 2026-04-25: if V3 position peaked ≥0.3% and gave back this pp, exit to lock profit. Separate from SCALP_V3_PG_ARM_PCT/_PG_GIVEBACK_PCT which gate only >0.5% peaks.
    STRICT_NO_LOSS_ACCOUNTS = []#'ang','flz', 'men', 'fin', 'inf']  # 2026-04-24: added 'inf'. MOVR -13% was hit with DC_BREACH_REDUCE_UNHEDGED instead of DC_BREACH_HEDGE_TRIGGER because inf was missing from this list (the hedge branch at ez_manage.py:14491 requires STRICT_NO_LOSS membership). RE-ENABLED 2026-04-07: Removing this halved account value in 10 minutes. NO closing at a loss. EVER. Hedge + ratio IS the protection.
    SCALP_OVERRIDE = False
    # === THROUGHPUT SAFETY KNOBS (added 2026-04-26 — pre-50-500/day push) ===
    # Master kill switch — when True, all four THROUGHPUT_SAFETY_* gates below are honored.
    # Defaults are SANE-CONSERVATIVE for current account sizes ($1k crypto). Flip enabled=True
    # before high-frequency push; tune per-account dicts as accounts scale.
    THROUGHPUT_SAFETY_ENABLED: bool = False  # master gate — set True only after wiring is verified
    # 1) Per-day max-loss kill switch — halts NEW entries when account day-PnL% breaches floor.
    #    Defaults: -3% per crypto account (small sizes, contained drawdown). Flip THROUGHPUT_SAFETY_ENABLED to enforce.
    THROUGHPUT_MAX_DAILY_LOSS_PCT: Dict[str, float] = field(default_factory=lambda: {
        "ang": -3.0, "inf": -3.0, "flz": -3.0, "men": -3.0, "fin": -3.0,
    })
    THROUGHPUT_DAILY_LOSS_RESET_UTC_HOUR: int = 0  # day boundary (00:00 UTC). Stocks override in tradier config.
    # 2) Max concurrent positions cap — refuse new opens beyond N total per account.
    #    Defaults match current observed live counts with ~30% headroom.
    THROUGHPUT_MAX_CONCURRENT_POSITIONS: Dict[str, int] = field(default_factory=lambda: {
        "ang": 25, "inf": 30, "flz": 20, "men": 25, "fin": 20,
    })
    # 3) Max total notional cap (USD) — refuse new opens beyond M $ gross exposure per account.
    #    Defaults: $1500 per crypto account (1.5× nominal $1k account size).
    THROUGHPUT_MAX_TOTAL_NOTIONAL_USD: Dict[str, float] = field(default_factory=lambda: {
        "ang": 1500.0, "inf": 1500.0, "flz": 1500.0, "men": 1500.0, "fin": 1500.0,
    })
    # 4) Per-symbol max fires per hour — anti-spam (one symbol can't dominate the queue).
    #    Default: 6 fires/hour/symbol = 1 every 10 min.
    THROUGHPUT_MAX_FIRES_PER_HOUR_PER_SYMBOL: int = 6
    # 4b) Per-account global max fires per hour — circuit breaker for queue spam at account level.
    THROUGHPUT_MAX_FIRES_PER_HOUR_PER_ACCOUNT: Dict[str, int] = field(default_factory=lambda: {
        "ang": 60, "inf": 90, "flz": 60, "men": 60, "fin": 60,
    })
    # External 0.01% incl-commissions Finandy stop (activates after +0.25% gain) is configured
    # in Finandy's webhook settings, NOT here. Documented for traceability — do not duplicate logic.
    # === END THROUGHPUT SAFETY KNOBS ===
    # === PER-ACCOUNT STRATEGIES — gate ablation tested (47 sym, 4yr, 25 configs) ===
    # ALL_GATES: Sharpe 2.62L/-15.69S, 58/31 trades → BROKEN
    # SCALP_FAST: Sharpe 4.46L/6.02S, 473/481 trades → ang/men
    # MOMENTUM: Sharpe 4.16L/5.33S, 420/433 trades → inf/flz
    # SWING_PATIENT: Sharpe 4.51L/6.79S, 227/258 trades → fin
    ACCOUNT_OVERRIDES: Dict[str, Dict] = field(
        default_factory=lambda: {
            "ang": {"NOLOSS_MIN_PROFIT_PCT": 0.0, "HTF_STRICT": False, "K3M_CAP": 0, "LONG_STOCH_CHASE_BLOCK": False, "ENTRY_ATR_PCT_MIN": 0.0},
            "men": {"NOLOSS_MIN_PROFIT_PCT": 0.0, "HTF_STRICT": False, "K3M_CAP": 0, "LONG_STOCH_CHASE_BLOCK": False, "ENTRY_ATR_PCT_MIN": 0.0},
            "inf": {"NOLOSS_MIN_PROFIT_PCT": 0.0, "HTF_STRICT": False, "K3M_CAP": 80, "LONG_STOCH_CHASE_BLOCK": False, "ENTRY_ATR_PCT_MIN": 0.0},
            "flz": {"NOLOSS_MIN_PROFIT_PCT": 0.0, "HTF_STRICT": False, "K3M_CAP": 80, "LONG_STOCH_CHASE_BLOCK": False, "ENTRY_ATR_PCT_MIN": 0.0},
            "fin": {"NOLOSS_MIN_PROFIT_PCT": 0.0, "HTF_STRICT": False},
        }
    )

    # --- Timeframe Focus Architecture ---
    TF_FOCUS: str = "3m"  # BACKTEST_CHANGE_1: was "15m". 3m dominates top 5000 (79%, avg Sharpe 305 vs 256)
    TF_FOCUS_WEIGHT: float = 8.0  # BACKTEST_CHANGE_2: was 5.0. 3m is 1.9x better than 15m
    TF_FOCUS_ENTRY_HARD_GATE: bool = True  # Focus TF must agree for entry
    TF_FOCUS_EXIT_HARD_GATE: bool = True  # Focus TF crossunder = immediate exit
    # TF HIERARCHY — defines the chain from micro to macro for WT cross analysis
    # Each level confirms the one below. wt1_{TF_MICRO} cross → confirmed by wt1_{TF_FOCUS} → confirmed by wt1_{TF_HTF1} etc.
    TF_MICRO: str = "1m"  # Fastest — entry timing precision (from hot_metrics bridge)
    TF_SCALP: str = "3m"  # Scalp — same as TF_FOCUS for crypto
    TF_HTF1: str = "15m"  # First HTF confirmation
    TF_HTF2: str = "1h"   # Second HTF confirmation
    TF_HTF3: str = "4h"   # Third HTF confirmation (strongest trend signal)
    TF_MACRO: str = "D"   # Macro trend — weekly/daily direction
    TF_ALL: Optional[list] = None  # Auto-populated: [TF_MICRO, TF_SCALP, TF_HTF1, TF_HTF2, TF_HTF3, TF_MACRO]
    TF_ALIGNMENT_MIN_TOTAL: int = 4  # 2026-03-30: Entries need 3/3 LTF + D mandatory + 2/3 HTF = 4+ TFs. Hardcoded in check_entry_alignment.
    TF_ALIGNMENT_MIN_SHORT: int = 2  # Exits need 2 TFs turning against
    TF_ALIGNMENT_MIN_LONG: int = 2  # Exits need 2 TFs turning against
    # === MULTI-TF STATE (MTS) GATE — analyze_multi_tf_state() unified K+WT scoring ===
    # Ablation: MTS_b10_eq5 = Sharpe 4.98L/5.84S (48 sym, 4-6yr). Without = 0.87L/4.13S.
    # MTS gate: OFF globally (gate ablation: WT+MTS noloss0.05 = Sharpe 5.1L/6.4S vs ALL_GATES 2.6L/-15.7S)
    # fin overrides MTS_GATE_ENABLED=True in ACCOUNT_OVERRIDES for swing strategy
    MTS_GATE_ENABLED: bool = True  # Ablation: ON globally gives +8.5% Sharpe (14.2 vs 13.1)
    MTS_BOTTOM_MIN: float = 15.0  # Ablation winner: b=15 (was 10). Filters low-quality entries.
    MTS_ENTRY_QUALITY_MIN: float = 8.0  # Ablation winner: eq=8 (was 5). Requires recent WT cross + structure.
    MTS_BOTTOM_MIN_SHORT: float = 10.0  # Shorts: slightly relaxed (was 5)
    MTS_ENTRY_QUALITY_MIN_SHORT: float = 5.0  # Shorts: slightly relaxed (was 0)
    MTS_BOTTOM_BONUS_THRESHOLD: float = 25.0  # bottom_score above this adds +4 score bonus
    MTS_BOTTOM_STRONG_THRESHOLD: float = 40.0  # bottom_score above this adds +8 score bonus
    MTS_ENTRY_QUALITY_BONUS: float = 25.0  # entry_quality above this adds +2 score bonus
    MTS_ENTRY_QUALITY_STRONG: float = 40.0  # entry_quality above this adds +5 score bonus
    # TF WEIGHTS for analyze_multi_tf_state — 1h_dom wins (Sharpe 10.2 vs default 7.1)
    # Phase 4 sweep: 1h_dom={1h:12, 4h:4, D:2} > balanced > default > htf_heavy
    MTS_WEIGHT_1m: float = 3.0   # Scalp timing — fast but noisy
    MTS_WEIGHT_3m: float = 5.0   # Scalp decision TF
    MTS_WEIGHT_15m: float = 8.0  # First HTF — strong entry confirmation
    MTS_WEIGHT_1h: float = 12.0  # DOMINANT — Sharpe 10.2 when 1h weighted highest
    MTS_WEIGHT_4h: float = 4.0   # Trend direction — moderate weight
    MTS_WEIGHT_D: float = 2.0    # Macro — low weight, slow to react

    ENABLE_LOSS_PROTECTION: bool = True  # Block closing positions with gain <= 0.12%
    NEW_POSITION_MIN_AGE_SECONDS: float = (
        180.0  # Consider position "new" if opened within this time
    )
    NEW_POSITION_MAX_LOSS_THRESHOLD: float = (
        -0.7
    )  # New positions can only reduce if loss > this threshold (loss > -0.2% allows reduction)
    # === STOP-THE-BLEED: Centralized Loss Prevention ===
    STOP_TIMEFRAME: str = (
        "15m"  # Controls dc_low/dc_high used for stops (was hardcoded 3m)
    )
    FAST_CUT_LOSS_THRESHOLD: float = -999.0  # BACKTEST_CHANGE_18: was -1.5. Dead code — ALL accounts STRICT_NO_LOSS
    FAST_CUT_LOSS_MIN_AGE_MINUTES: float = 15.0  # Was 6 min (too short)
    AGGRESSIVE_LOSS_CUT_ENABLED: bool = False  # DEAD CODE — replaced by STRUCTURAL_RANGE_SHIFT_EXIT (2026-04-11). DO NOT re-enable.
    BREAKOUT_GUARD_LOSS_THRESHOLD: float = -999.0  # BACKTEST_CHANGE_20: was -0.5. Dead code under STRICT_NO_LOSS
    BREAKOUT_GUARD_MOMENTUM_CHECK_ENABLED: bool = False  # Disables 1-sec momentum kills
    EXIT_ON_ALL_ENABLED: bool = False  # Was True via EXIT_ON_ALL
    REDUCE_HUGE_LOSS_THRESHOLD: float = -999.0  # BACKTEST_CHANGE_19: was -2.0. Dead code under STRICT_NO_LOSS
    STOP_MAJOR_LOSS_ENABLED: bool = False  # ABLATION_BACKTEST: was implicitly True. #1 PnL destroyer (-125k%). L/S ratio hedge handles risk
    HEDGE_TRIGGER_LOSS_PCT: float = -0.05  # BACKTEST_CHANGE_38: was -0.10. Hedge earlier with 0.3% TP system
    ORPHAN_HEDGE_CHECK_GAIN: bool = True  # Check gain before killing orphans
    # ═══ ROGUE HEDGE FIX 2026-04-16 — CRITICAL ═══════════════════════════════
    WT_15M_SAME_HEDGE_ENABLED: bool = False  # 2026-05-29 USER: ZERO hedging anywhere (was True; dead-gated by HEDGE_MODE=False but disabled explicitly).
    WT_15M_SAME_HEDGE_DAILY_CAP: int = 2  # 2026-04-16: max SAME_HEDGE opens per symbol per day. 45× BAT/DOT/ATOM firestorm = daily cap missing.
    WT_15M_SAME_HEDGE_COOLDOWN_SEC: int = 1800  # 2026-04-16: Redis-backed cooldown (survives restarts — old 300s in-memory wiped on process restart).
    # ═══════════════════════════════════════════════════════════════════════
    # SHARPE-TRIPLE ENHANCEMENTS 2026-04-16 — break the 0.69 plateau
    # Implementations in strategy_enhancements.py. ALL DEFAULT OFF so agents
    # sweep each independently + stacked. Target: Sharpe 0.69 → 2.0.
    # See data/sweep_tiers.json for ranges. Sweep order: TIER_A first.
    # ═══════════════════════════════════════════════════════════════════════
    # --- TIER A #1: ASYMMETRIC STOPS — tight losers, wide winners (biggest lever) ---
    ASYMMETRIC_STOPS_ENABLED: bool = False           # TIER_A: estimated Sharpe +0.5 alone.
    ASYMMETRIC_LOSER_MIN_AGE_SECONDS: float = 540    # 9min = 3 bars. Below this, no stop (avoid noise).
    ASYMMETRIC_WINNER_GAIN_PCT: float = 1.5          # At this gain, position switches to winner rules.
    # --- TIER A #2: PROGRESSIVE PROFIT LOCK — 25% reduce at each tier ---
    PROGRESSIVE_LOCK_ENABLED: bool = False           # TIER_A: Sharpe +0.3. Staged profit without full close.
    PROGRESSIVE_LOCK_TIERS_PCT: list = field(default_factory=lambda: [1.0, 2.0, 3.0, 5.0, 8.0])
    PROGRESSIVE_LOCK_FRACTION: float = 0.25          # Reduce fraction per tier.
    # --- TIER A #3: REGIME GATE — skip chop/compression entries ---
    REGIME_GATE_ENABLED: bool = False                # TIER_A: Sharpe +0.3. Kills low-WR chop tail.
    REGIME_ATR_RATIO_MIN: float = 0.25               # atr_3m/atr_1h min. Below = compressed.
    REGIME_BB_WIDTH_PCT_MIN: float = 2.0             # bb_width_1h as % of price. Below = squeeze.
    REGIME_DC_ATR_RATIO_MIN: float = 1.5             # dc_width_15m / atr_3m. Below = no room.
    # --- TIER B #4: PER-SYMBOL CONFIG ROUTING — load sweep winners per symbol ---
    PER_SYMBOL_CONFIG_ENABLED: bool = False          # TIER_B: Sharpe +0.1-0.2. Overnight sweep infra ready.
    PER_SYMBOL_CONFIG_FILE: str = "data/sweep_results/per_symbol_best_crypto_20260416_0507.json"
    # --- TIER C #6: VOLUME CONFIRMATION on entry ---
    VOLUME_CONFIRMATION_ENABLED: bool = False        # TIER_C: Sharpe +0.1. Kills dead-zone entries.
    VOLUME_CONFIRMATION_MULT: float = 1.2            # volume_3m > N × avg_20_3m required.
    # --- TIER C #7: HOUR-OF-DAY GATE ---
    HOUR_OF_DAY_GATE_ENABLED: bool = False           # TIER_C: Sharpe +0.1. Audit hourly Sharpe first.
    HOUR_OF_DAY_BLOCKED_UTC: list = field(default_factory=list)  # e.g. [22,23,0,1,2,3,4] Asia chop
    # --- TIER C #8: SYMBOL CIRCUIT BREAKER ---
    CIRCUIT_BREAKER_ENABLED: bool = False            # TIER_C: Sharpe +0.1. Prevents regime-mismatch bleed.
    CIRCUIT_BREAKER_SYMBOL_LOSSES: int = 3           # N consec losses/symbol → halt
    CIRCUIT_BREAKER_SYMBOL_HALT_MIN: int = 30        # halt duration (min)
    CIRCUIT_BREAKER_ACCOUNT_LOSSES: int = 5          # N consec losses/account → halt
    CIRCUIT_BREAKER_ACCOUNT_HALT_MIN: int = 60       # halt duration (min)
    # --- TIER D #9: PYRAMID INTO STRENGTH ---
    PYRAMID_ENABLED: bool = False                    # TIER_D: Sharpe +0.2. Amplifies winners.
    PYRAMID_MIN_GAIN_PCT: float = 1.5                # Fires once gain >= this.
    PYRAMID_MIN_WT_VEL_1H: float = 2.0               # 1h velocity must trend.
    PYRAMID_MIN_DC_POS_15M: float = 0.7              # LONG: DC pos > 0.7 = upper third.
    PYRAMID_MAX_DC_POS_15M_SHORT: float = 0.3        # SHORT: DC pos < 0.3 = lower third.
    PYRAMID_SIZE_MULT: float = 0.5                   # Add N × position_amt (0.5 = 50%).
    REENTRY_MANDATORY: bool = True  # Enforce reentry after every exit
    # === TWO-TIER MANDATORY REENTRY (BC_155) ===
    # Tier 1 (PULLBACK): Wait for K zone reset, enter at 120-150% size (better price)
    # Tier 2 (CHASE): Trend continues without pullback, enter at 70-100% size (don't miss move)
    REENTRY_TIER1_SIZE_MULT: float = 1.5  # Tier 1: 150% of closed qty (better price reward)
    REENTRY_TIER2_SIZE_MULT: float = 0.8  # Tier 2: 80% of closed qty (worse price, smaller)
    # USER 2026-05-30 RESTORED — the long-standing reentry SIZING rule (was lost when the price-cross path
    # got disabled to stop a 5s flood; NO backtest justified erasing it). Applied in the live reentry path:
    #   buy-the-DIP (price below exit, LONG) → 150% | BREAKOUT (price above exit) → 100% | k_1h extended → 50%.
    # Sweepable (sweep tier reentry_size_tiers tests 150/100/50 vs 200/150/100 vs 300/200/50 etc).
    REENTRY_SIZE_DIP_MULT: float = 2.0          # 2026-05-30 USER GO-LIVE: lower entry (dip, k_15<30) → 200% — REENTER BIGGER when trend continues
    REENTRY_SIZE_BREAKOUT_MULT: float = 1.5     # 2026-05-30: ~same price (continuation) → 150%
    REENTRY_SIZE_EXTENDED_MULT: float = 1.0     # 2026-05-30: k_1h>90 extended/still-ripping → 100% (was 0.5 — NEVER shrink the runner)
    REENTRY_SIZE_EXTENDED_K1H: float = 90.0     # 2026-05-30: extended threshold lowered 95→90 per user
    # === HTF-REGIME hold + scale-in-at-bottoms (2026-05-30 USER GO-LIVE machinery — DEFAULT OFF) ===
    # Shared core: vec_decisions/htf_regime_scale.py (live==backtest, parity 0/20000). Master switch is a
    # KILL SWITCH defaulting OFF (new-strategy rule). A symbol/side trades under this ONLY if it is marked
    # tradeable in the per_sym HTF ledger (passed the 4yr gate: x_bh>=5 AND net pool_sharpe>=0.25 AND beats
    # its own baseline — see data/_diagnostic/PERSYM_VALIDATION_MAP_20260530.md). Ledger gate is enforced
    # by HTF_REGIME_LEDGER_PATH; absent/empty ledger => nothing trades even if master switch is ON.
    HTF_REGIME_ENABLED: bool = False            # MASTER KILL SWITCH — default OFF
    HTF_REGIME_LEDGER_PATH: str = "data/_diagnostic/persym_htf_ledger_full.json"
    HTF_REGIME_TF: str = "D"                    # which 200-SMA TF gates the up/down/flat regime
    HTF_REGIME_EXIT_TF: str = "15m"            # tight-cut TF in chop / non-uptrend
    HTF_REGIME_SCALE_IN: bool = True           # per-symbol overridable via ledger cfg
    HTF_REGIME_ADD_MULT_PER_SMA: float = 0.75  # size added per lower-SMA reclaimed (catch the bottom)
    HTF_REGIME_SIZE_CAP: float = 3.0           # bounded — never 2^% leverage ruin
    HTF_REGIME_VOL_TARGET: float = 0.0         # 0=off; >0 de-levers when realized vol high
    REENTRY_TIER2_PRICE_PCT: float = 0.003  # 0.3% price move past exit triggers Tier 2
    REENTRY_TIER2_MIN_MINUTES: float = 10.0  # Minimum minutes before Tier 2 activates
    REENTRY_TIER2_MAX_MINUTES: float = 120.0  # After this, Tier 2 forces entry at 50% size
    # === RECOVERY_AUGMENT (2026-05-20 — partial-close trap fix, mirrors config_tradier) ===
    # Fires AUGMENT with distinct reason "RECOVERY_AUG_*" when a partially-reduced
    # position (positionAmt > 0 after SENTIMENT_FADE / WT_BANDAID / DELTA_EXIT REDUCE)
    # sees price cross back through last_reduction_price within the band+age window.
    # Bypasses HARD_MIN_GAIN_WALL via reason-based `_is_recovery_aug` flag in execute_now.
    # Default OFF — flip only after backtest on the 7-day "forgotten" set (713 closes).
    RECOVERY_AUGMENT_ENABLED: bool = False
    RECOVERY_AUGMENT_BAND_PCT: float = 0.3       # within 0.3% of last_reduction_price
    RECOVERY_AUGMENT_MAX_AGE_MIN: float = 240.0  # only fire within 4h of the reduction
    RECOVERY_AUGMENT_REQUIRE_WT_CROSS: bool = False  # if True, require favorable WT cross on 3m before firing
    RECOVERY_AUGMENT_SIZE_PCT: float = 1.0       # 1.0 = 1× START_POSITION_SIZE
    RECOVERY_AUGMENT_ONE_FIRE_PER_REDUCE: bool = True  # set Position.recovery_fired after firing; cleared on next REDUCE
    REENTRY_ESCALATION_WARN_MIN: float = 30.0  # WARNING log if reentry pending > 30min
    REENTRY_ESCALATION_CRIT_MIN: float = 60.0  # CRITICAL log if reentry pending > 60min
    REENTRY_RALLY_K15M_MAX: float = 30.0    # 2026-04-20 sweep: vel=9+rally=30 → Sharpe 2.598. 2026-04-25 rapid-grid 50-sym: K40/50/60/60+gap3 all identical to K30 — reentry count not K-gated, binding constraint is entry score + cooldown.
    REENTRY_RALLY_HTF_MIN: int = 1          # sweep: 1 / 2 / 3 — min of (1h/4h/D) WT aligned at reentry
    REENTRY_MIN_GAP_MINUTES: float = 0.0    # 2026-05-22 USER MANDATE: hard 15-min gap blocked reentry on NEAR +83% rally. If exit <15min old, deeper reentry pipeline (T-Defer-1) requires breakout-through-prior-high; >15min, exit_price is the trigger. Removing the bar-count floor.
    REENTRY_SYMGATE_ENABLED: bool = False   # 2026-04-19 FIX: Chapter-C tested on broken B15/B11 data. Re-sweep pending.
    REENTRY_SYMGATE_SPEED_MIN: float = 0.5  # Crypto: 0.5 bull/bear speed min (stocks=1.0). Below = momentum slowing -> block.
    ENTRY_SYMGATE_ENABLED: bool = False     # 2026-04-19 FIX: Chapter-C tested on broken B15/B11 data. Re-sweep pending.
    NOLOSS_DC4H_GATE_ENABLED: bool = True   # HARD RULE: never close at a loss inside dc_4h channel — hedge instead.
    LOSS_EXIT_TECHNICAL_BYPASS: tuple = ('LIQUIDATION', 'EMERGENCY_DC1H_BREACH', 'PARABOLIC_EXIT', 'GAIN_EROSION')  # close reasons that bypass NOLOSS_DC4H; GAIN_EROSION added 2026-04-20 so DC_LOW4_3M closes instead of hedging
    LOSS_EXIT_REQUIRES_HEDGE: bool = True  # Master: can only exit at loss if hedge >= losing value
    HEDGE_OVERSIZE_RATIO: float = 2.0  # Max 200% of losing position. Tiered: 50% at -0.6%, 100% at -1%, 150% at -1%, 200% at -2%
    HEDGE_MOMENTUM_GATE: bool = False  # BACKTEST_CHANGE_119: No momentum gate — 15m WT is the sole gate.
    HEDGE_MAX_RATIO: float = 2.0  # Hard cap 200% of losing position value.
    HEDGE_TRIGGER_LOSS_PCT_ENTRY: float = -2.0  # Cross-symbol trigger (HEDGE_MODE only, not obligatory).
    OBLIGATORY_HEDGE_PCT: float = 0.0  # DISABLED 2026-03-30: Caused cascade. Was 2.0 (200% of losing). Fires regardless of HEDGE_MODE — THAT WAS THE PROBLEM.
    OBLIGATORY_HEDGE_MIN_LOSS_PCT: float = 0.0  # 2026-04-18: hedge when gain hits 0% — if system failed to sell at a gain this is the fallback
    HEDGE_NEWBORN_GRACE_MINUTES: float = 0.0  # 2026-05-12 USER MANDATE: hedges should have NO grace period — must close instantly when WT flips. Original 10.0 blocked needed unwinds during fast adverse moves.
    HEDGE_NEWBORN_DC_BREACH_ALLOWED: bool = True  # allow hedge during grace if price breaches dc_low_3m (LONG) / dc_high_3m (SHORT)
    OBLIGATORY_HEDGE_WT_TFS: int = 2  # Need 2 TFs with WT against before opening hedge.
    HEDGE_CLOSE_WT_TFS_FAVOR: int = 3  # BC_988: r2 winner but this is now unused — 15m WT close in code.
    HEDGE_SAME_SYMBOL_ENABLED: bool = False  # 2026-05-20 USER MANDATE: same-symbol hedge OFF — MTF compound exit replaces it. ROLLBACK: True restores 150% same-symbol hedge.
    HEDGE_DUAL_IF_HEDGE_MODE: bool = False  # Cross-symbol dual hedge disabled.
    HEDGE_ALL_POSITIONS: bool = False   # 2026-05-29 USER: ZERO hedging anywhere (was True; superseded the 2026-05-10 100%-same-symbol-hedge mandate — no hedging now).
    # === 2026-04-17 HEDGE OVERHAUL — user directive: hedges close on wt_3m flip no matter the P/L ===
    # 2026-05-17 R6 ROLLBACK: was True. True = close hedge on 1-of-3 wt flip regardless of P/L (orphan-kill at loss). False = require 3-of-3 wt flip (Apr 13 working behavior).
    HEDGE_EXIT_BYPASS_NOLOSS: bool = False  # ROLLED BACK 2026-05-17 (was True)
    HEDGE_EXIT_WT_TF: str = "3m"  # Which TF's WT flip triggers hedge close ("3m" per user rule).
    # 2026-05-17 R6 ROLLBACK: was True. Dropping pk from tradeable_keys on hedge close → symbol couldn't be re-opened cleanly → cascading orphan-kill.
    HEDGE_CLOSE_REMOVE_FROM_TRADEABLE: bool = False  # ROLLED BACK 2026-05-17 (was True)
    HEDGE_SAME_SYMBOL_PCT: float = 1.0  # Same-symbol hedge size as fraction of loser qty (1.0 = 100%).
    HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE: bool = True  # Same-symbol hedge bypasses tradeable_keys gate (special hedge status).
    # === 2026-04-26 HEDGE SYMBOL-SELECTION GUARDS (sweep-testable) — user wants gain-deterioration as primary trigger, DC zones secondary ===
    HEDGE_DC_RESISTANCE_GATE_ENABLED: bool = False  # 2026-04-26: OFF — was blocking hedges precisely when needed (V3 SHORT bleeding into a pump, all LONG hedge candidates rejected because they were pumping too). User rule: hedges activate on deteriorating gains, INDEPENDENT of dc position. Sweep-only knob now.
    HEDGE_DC_LONG_REJECT_DCP: float = 0.85          # LONG-side dc_position_1h/4h threshold (>=) for rejection.
    HEDGE_DC_SHORT_REJECT_DCP: float = 0.15         # SHORT-side dc_position_1h/4h threshold (<=) for rejection.
    HEDGE_WT_VEL_GATE_ENABLED: bool = False         # 2026-04-26: OFF — same reason as DC gate above. Hedge-the-bleeder must not be filtered by candidate-symbol velocity. Sweep-only knob.
    # 2026-04-26 — Force-reentry HTF veto (refuse PRICE_CROSSED_MANDATORY when 1h+15m+4h all confirm trend AGAINST). Triggered after C98USDT triple-open against bullish HTF.
    PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED: bool = False
    # 2026-04-26 USER RULE — HARD hedge sizing caps. Trades move <1% per cycle on a ~$1k crypto
    # account, so hedges must NEVER exceed 1.5× loser notional or absolute $25. Caps applied in
    # both compute_hedge_size and execute_same_symbol_hedge inner. Triggered after ALTUSDT_LONG
    # accumulated to $1013 / 12478% in tracker from pre-fix double-fires.
    # 2026-05-16 USER MANDATE (PHBUSDT $6.94-hedge-vs-$49-SHORT incident, accounts wiped):
    # hedges are ALWAYS 100% of origin notional. PCT capped at 1.0× (no over-hedge). The $25
    # absolute cap is RAISED to effectively unlimited so the percent cap is what binds — a $48
    # SHORT now produces a $48 hedge, not $25. To distinguish original vs hedge when ambiguous,
    # consult symbols_{acc}_long.json / symbols_{acc}_short.json — side present in that file =
    # original; opposite side = hedge.
    HEDGE_MAX_PCT_OF_LOSER: float = 1.0  # 100% of loser. NEVER exceed loser size.
    HEDGE_MAX_ABSOLUTE_USD: float = 100000.0  # 2026-05-16: was $25 (over-tight). Now effectively unlimited; pct cap binds.
    # 2026-04-26 — refuse new hedge orders if existing hedge-side position already covers
    # >= this fraction of target. Stops accumulation across many cycles.
    HEDGE_ALREADY_COVERED_THRESHOLD: float = 0.9
    # 2026-04-26 USER RULE — MICRO_SCALP_USDC_MAKER. USDC pairs only, maker-only (zero fees on
    # Binance Futures USDC pairs), no webhook fallback. Closes at gain >= threshold AND first
    # deceleration; reopens when price re-crosses exit_price. Fires from process_position before
    # other close paths. Bypasses STRICT_NO_LOSS / UNG / hedge gates — close only on POSITIVE gain.
    # 2026-05-22 USER MANDATE: DISABLED globally. Gate ONLY fires on gain>=0.02% AND decelerating —
    # never on losers (gain<0). Net effect: cuts every winner at the first tiny pullback past 0.02%,
    # never closes losers. Asymmetric exit. 2026-05-21 incident: ZECUSDC flz closed at +0.666% while
    # price continued to +1% more. Re-enable ONLY if scalar A/B sweep at thresholds 0.1%..2.0%
    # (MICROSCALP_THR_* arms in sweep_coordinator) shows POSITIVE delta pool_sharpe vs OFF baseline.
    # ROLLBACK: set ENABLED=True (and use the threshold that won the sweep).
    MICRO_SCALP_USDC_MAKER_ENABLED: bool = False
    MICRO_SCALP_USDC_ACCOUNTS: list = field(default_factory=lambda: ["ang", "inf", "flz", "men", "fin"])
    MICRO_SCALP_GAIN_THRESHOLD_PCT: float = 0.02
    # === 2026-04-26 HEDGE OPEN TRIGGER (sweep-testable) — gain-deterioration before WT flip is "wrong moment" prevention ===
    # 2026-05-17 R6 ROLLBACK: was False (fires on any wt-against). True = require deteriorating-gain prerequisite → fewer spurious hedges. May 28k/day hedges (WR 6%) vs Mar 1k/day (WR 87%).
    HEDGE_DETERIORATING_GAIN_ENABLED: bool = False  # 2026-05-29 USER: ZERO hedging anywhere (was True).
    HEDGE_DETERIORATING_GAIN_DELTA_PP: float = 0.10 # Min pp drop from prev_gain to qualify as "deteriorating" (e.g., gain went -0.5% → -0.6% = 0.1pp drop).
    # 2026-04-27 USER (C98USDT incident): block hedge entries opening into adverse orderbook pressure.
    # ez_orderbook publishes ob_bid_ask_imb_10 (bid pressure / total). Block LONG hedge if imb < (1-bound), SHORT if imb > bound.
    # Bound 0.65 → blocks egregious mismatches (allows neutral 0.35-0.65 range). Reads `orderbook:{SYM}` from Redis live.
    HEDGE_OPEN_OB_CHECK_ENABLED: bool = True
    HEDGE_OPEN_OB_IMB_BOUND: float = 0.65
    # 2026-04-27 USER ABSOLUTE: gain/mark_price/positionAmt MUST NEVER be stale when deciding to close a hedge.
    # Refuse to close if EITHER position.last_updated OR mark_price_last_updated is older than this many seconds.
    # Fallback chain (per user): ez_positions_realtime → S1/MacBook/gateway rsync → fail closed if all stale.
    LIVE_POSITION_FRESHNESS_MAX_SEC: float = 3.0
    # === 2026-04-27 FUNDING-RATE GATES (Binance Futures, 8h funding) — DECISIVE FACTOR (USER DIRECTIVE) ===
    # Funding rate >0 means longs pay shorts (overheated long market). <0 means shorts pay longs.
    # Knob names mirror v8_quick_engine.py:699-701 so sweeps test the SAME live knobs.
    # Cache: binance_funding_fetcher.py → data/funding_cache/{sym}.json (8h API).
    # Live wire: ez_market_data.funding_rate_loop refreshes hourly into data_manager._cold_data[sym]['funding_rate'].
    # Backtest wire: NPZ field `funding_rate_{ltf}` (forward-filled) — already integrated in backtest_v8_precompute._inject_funding_oi.
    # Live entry gate: ez_positions_quick.execute_trade_wrapper reads `funding_rate*` from indicator dict.
    FUNDING_GATE_ENABLED: bool = True               # 2026-04-27 default ON in live per user directive (was OFF in pre-existing sweep knob)
    FUNDING_GATE_LONG_MAX: float = 0.0005           # reject NEW LONG when funding_rate >= 0.05%
    FUNDING_GATE_SHORT_MIN: float = -0.0005         # reject NEW SHORT when funding_rate <= -0.05%
    FUNDING_HEDGE_GATE_ENABLED: bool = True         # apply funding gate to hedge entries too (helps "wrong moment" hedge open)
    FUNDING_GATE_MTF_REQUIRED: bool = True          # 2026-05-31 ENABLED: only veto when HTF WaveTrend disagrees with the trade. Data (9.3M bars, 24h fwd): naive long-block +0.150% (HURTS — kills momentum longs) vs MTF long-block bull<=0 -0.912% (filters only worst longs); naive short-block +0.427% vs MTF short-block bear<=1 +1.078% (targets squeezes). False = legacy naive snapshot gate.
    FUNDING_GATE_MTF_LONG_MAX_BULL_TFS: int = 0     # BEST: block NEW LONG only if 0 of (15m,1h,4h,D) are WT-bullish (wt1>wt2) — HTF fully bearish. blocked-long 24h fwd -0.912% n=6860.
    FUNDING_GATE_MTF_SHORT_MAX_BEAR_TFS: int = 1    # BEST: block NEW SHORT only if <=1 of (15m,1h,4h,D) WT-bearish (wt1<wt2) — HTF not confirmed-down. blocked-short 24h fwd +1.078% n=9525.
    OI_CONFIRM_ENABLED: bool = True                 # 2026-04-27: live ON per user directive after Batch 1 A/B (+40% max / +107% avg). Backtest reconfirm queued. 4-quadrant OI×price (Schabacker classic).
    OI_CONFIRM_MIN_CHANGE_PCT: float = 0.5          # |oi_change_1h_pct| must exceed this to consider OI move significant
    OI_CONFIRM_MIN_PRICE_PCT: float = 0.3           # |price_change_1h_pct| must exceed this; gate fires only when BOTH oi+price are significant
    OI_HEDGE_GATE_ENABLED: bool = False             # apply OI gate to hedge entries too (default OFF)
    OI_LIVE_REFRESH_HOURS: float = 1.0              # ez_market_data refreshes OI hourly via Binance OI API
    FUNDING_LIVE_REFRESH_HOURS: float = 1.0         # how often ez_market_data refreshes funding rates from Binance API in live
    # === 2026-04-27 FUNDING + OI EXTREME-OUTLIER INJECTION INTO ez_rankings winners/losers ===
    # Stretched funding fades the overcrowded side (longs overcrowded → SHORT, shorts overcrowded → LONG).
    # OI×price conviction quadrants only inject (price↑+OI↑ → LONG, price↓+OI↑ → SHORT).
    FUNDING_OI_INJECT_ENABLED: bool = True
    FUNDING_INJECT_LONG_OVERCROWDED_ABOVE: float = 0.0008   # funding ≥ +0.08% → SHORT injection (longs paying heavily)
    FUNDING_INJECT_SHORT_OVERCROWDED_BELOW: float = -0.0008 # funding ≤ -0.08% → LONG injection (shorts overcrowded)
    FUNDING_OI_INJECT_OI_MIN_PCT: float = 1.0       # |oi_change_1h_pct| threshold for OI-based injection
    FUNDING_OI_INJECT_PRICE_MIN_PCT: float = 0.5    # |price_change_1h_pct| threshold (filters tiny moves)
    FUNDING_OI_INJECT_MAX_EACH: int = 10            # cap per side
    # === 2026-04-27 ORDER-BOOK RED-ZONE GATE (heatmap walls from real bids/asks) ===
    # ez_orderbook.py DeepBook already computes per-sym wall fields and writes to Redis `orderbook:<SYM>` (TTL 10s):
    #   ob_bid_wall_pct  — distance % to nearest support wall (>4× mean bid-bucket notional, ≥0.5% from mid)
    #   ob_ask_wall_pct  — distance % to nearest resistance wall (>4× mean ask-bucket notional, ≥0.5% from mid)
    #   ob_bid_wall_size, ob_ask_wall_size — notional in those walls
    #   ob_long_score / ob_short_score — composite 0..100 (wall + void + top-imbalance)
    # Live propagation: ez_market_data.broadcast_loop MGETs `orderbook:*` per cycle and injects ob_* fields
    # into per-sym Redis hot_metrics:{sym} payload → data_manager.get_hot_state → execute_trade_wrapper._gate_ind.
    # Entry gate (ez_positions_quick.execute_trade_wrapper):
    #   - LONG blocked when ob_ask_wall_pct < RED_ZONE_MIN_DISTANCE_PCT  (resistance wall too close above)
    #   - SHORT blocked when ob_bid_wall_pct < RED_ZONE_MIN_DISTANCE_PCT (support wall too close below)
    # Wall must also exceed RED_ZONE_MIN_WALL_NOTIONAL_USD to count (filters tiny walls on illiquid pairs).
    # Stocks side: Tradier exposes no L2 depth — RED_ZONE_GATE is crypto-only.
    # Stocks proxy = options-chain OI walls (call OI = ceiling, put OI = floor) — deferred to tradier_options_oi_fetcher build.
    RED_ZONE_GATE_ENABLED: bool = False             # 2026-04-28 restored — probe one-by-one to find which gate actually regressed
    RED_ZONE_MIN_DISTANCE_PCT: float = 0.4         # block entry when wall is closer than 0.4% from current price
    RED_ZONE_MIN_WALL_NOTIONAL_USD: float = 50_000 # ignore walls smaller than $50k notional (illiquid noise)
    RED_ZONE_HEDGE_GATE_ENABLED: bool = False       # apply red-zone gate to hedge entries too (stops hedging into hard wall)
    RED_ZONE_AUGMENT_GATE_ENABLED: bool = False     # apply to AUGMENT actions (don't add into resistance)
    RED_ZONE_STALE_MAX_SEC: float = 30.0           # ignore ob_*_wall fields older than 30s (orderbook:_heartbeat dead)
    # === 2026-04-28 DEEP VOLUME-PROFILE HEATMAP GATE (crypto, ±50% range) ===
    # User 2026-04-28: "Did you search find and apply heat maps (basically ez_order_book but over the next 50% up or down...
    # same utility as oi, not applied in our system needs adding and testing".
    # Source: ez_volume_profile.py → Redis vol_profile:<SYM> (TTL ~1h).
    # Bucketize 1500 × 15m bars × volume into 1%-wide bins ±50%, identify HVNs (density z≥1.5).
    # Top-K HVN above price = resistance shelves; below = support floors.
    # Block LONG when underlying within VP_GATE_MIN_DISTANCE_PCT below an HVN above (resistance shelf).
    # Block SHORT when underlying within VP_GATE_MIN_DISTANCE_PCT above an HVN below (support floor).
    # Complements RED_ZONE_GATE (near-term, ±5% L2 walls): VP_GATE = long-term (±50% historical density).
    VP_GATE_ENABLED: bool = False                   # 2026-04-28 restored — probe one-by-one to find which gate actually regressed
    VP_GATE_MIN_DISTANCE_PCT: float = 1.0          # block entries within 1% of an HVN shelf
    VP_GATE_MIN_DENSITY_Z: float = 2.0             # require HVN density-z ≥ 2.0 (~5× mean) to block
    VP_GATE_HEDGE_GATE_ENABLED: bool = False       # apply to hedge entries (default off)
    VP_GATE_AUGMENT_GATE_ENABLED: bool = False      # apply to AUGMENT actions
    VP_GATE_STALE_MAX_SEC: float = 7200.0          # 2h freshness — daemon refreshes hourly
    # === 2026-04-27 LOWER-HIGHS / HIGHER-LOWS FILTER (sweep-testable, default OFF) ===
    # User: "block long trades while 1h/4h charts make lower highs (shorts vv) instead of the sma_200_D filter (or on top of it)".
    # LONG blocked when 1h+4h are making lower highs (downtrend confirming on HTFs).
    # SHORT blocked when 1h+4h are making higher lows (uptrend confirming).
    # Modes:
    #   "DISABLED" — gate off (default for forward-testing safety)
    #   "STRICT_2BAR" — high_1h < high_1h_prev AND high_4h < high_4h_prev (LONG block); mirror for SHORT (low > low_prev)
    #   "DC_REGRESS" — current high < dc_high_*(1-threshold%) — failing to take out recent N-bar swing high
    # TF_REQ controls how many timeframes (1h, 4h) must confirm: 1=either, 2=both.
    # REPLACE_SMA200D=True also disables HTF_GATE_SIGNALS_SMA200D so this REPLACES the SMA200_D filter rather than ADDS.
    LH_HL_FILTER_ENABLED: bool = False
    LH_HL_FILTER_MODE: str = "STRICT_2BAR"
    LH_HL_FILTER_TF_REQ: int = 2                       # 1=either 1h or 4h, 2=both must confirm
    LH_HL_FILTER_DC_THRESHOLD_PCT: float = 0.5         # only used in DC_REGRESS mode
    LH_HL_FILTER_REPLACE_SMA200D: bool = False         # if True, also turns off HTF_GATE_SIGNALS_SMA200D
    LH_HL_FILTER_AUGMENT_GATE_ENABLED: bool = False     # apply to AUGMENT actions (don't add into reversing trend)
    LH_HL_FILTER_HEDGE_GATE_ENABLED: bool = False      # apply to hedge entries (default OFF — hedges are intentional counter-trend)
    # Sweep dimension (user 2026-04-27): test LH alone vs LH+LL for LONGS, HL alone vs HL+HH for SHORTS.
    #   False (default): block LONG on LH only / block SHORT on HL only — catches trend hesitation.
    #   True: also require LL (LONG) / HH (SHORT) — full descending/ascending channel confirmation.
    LH_HL_FILTER_REQUIRE_BOTH: bool = False
    # === 2026-04-26 USER ABSOLUTE: hedges NEVER close at a loss (overrides feedback_hedge_wt3m_close_absolute.md until tests prove otherwise) ===
    # Applied to: HEDGE_CLOSE_WT3M1H_PRE_GATE (ez_manage), HEDGE_CLOSE_WT3M1H_PP_ABS (ez_manage), HEDGE_CLOSE_WT3M1H_ABS (ez_positions_quick), HEDGE_KILL_REVERSING_WT (ez_positions_quick).
    # If gain<0 the WT-flip signal is recorded but the close is held; we wait for gain>=0 OR the position to organically improve. STRICT_NO_LOSS-aligned.
    HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN: bool = False  # USER 2026-05-10: "closed ANY moment wt1_3m disagrees" — no gain condition. Was True; held hedge open while losing → cascading bleed.
    # 2026-04-27 — wires the compact 7-block evaluate_reentry (was dead code, defined at line 16593, never called).
    # 683 reentry mentions / 0 executions today across 5 crypto accounts. When True, process_position calls
    # evaluate_reentry per cycle for fresh-flat or partially-reduced positions. User: "test the difference
    # (huge functions so augments backtest times by up to 50% but if it works it works)".
    EVAL_REENTRY_ENABLED: bool = True
    # 2026-04-28 — ez_reentry.py / ez_reentry_daemon.py wiring switches.
    # 2026-05-08 — Reentry via subprocess daemon (spawned by ez_manage, no start_everything change).
    # Inline loops DISABLED — daemon is sole reentry signal source.
    # Kill child (pkill -f ez_reentry_daemon) to stop reentries while trading continues.
    EZ_REENTRY_DAEMON_ENABLED: bool = True
    EZ_REENTRY_INLINE_ENABLED: bool = True
    EZ_REENTRY_INLINE_TIER12_EPQ_ENABLED: bool = False
    EZ_REENTRY_INLINE_EVAL_EPQ_ENABLED: bool = False
    EZ_REENTRY_INLINE_EVAL2_DIRECT_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_PERIODIC_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_ENFORCE_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_PRICE_MONITOR_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_ENFORCE_EPQ_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_EVAL2_EPQ_ENABLED: bool = False
    # Inline price-cross DISABLED — was the "100% reentry AT ONCE" culprit firing every 5s
    # inside ez_manage immediately after exits. Daemon subprocess owns this path now.
    EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED: bool = False
    EZ_REENTRY_PRICE_CROSS_INTERVAL_S: float = 5.0
    EZ_REENTRY_PRICE_CROSS_PCT: float = 0.10  # 2026-07-08 GAINMO anti-churn: 0.0→0.10 — daemon reentry requires 0.10% price improvement past the cross (bare cross-back was #1 fill reason; commission = 41% of the 16d loss)
    EZ_REENTRY_PRICE_CROSS_MIN_GAP_S: float = 300.0   # 2026-06-25 anti-churn (USER: crypto >50% loss / commission bleed). Was 60.0 — same-key daemon reentry no more than 1/5min. DAEMON_PRICE_CROSS_REENTRY was #1 fill reason (ang 1075/16d) feeding -$120 commission. ROLLBACK: 60.0.
    # 2026-06-03 DISABLED — DO NOT re-enable in the daemon. The reentry DAEMON has NO indicators
    # (Redis indicators:{sym} keys don't exist on its 6379 feed → dc_high4_3m always 0), so this
    # guard fail-closed and blocked EVERY first-hour reentry (20k+ CHURN_GUARD blocks). The
    # daemon runs purely on exit-price cross. Anti-churn (dc_high4_3m/dc_low4_3m 4-bar breakout
    # for the first window after exit) must live in the QUEUE CONSUMER (ez_manage), which has
    # real live indicators — NOT the daemon. Code kept (fail-open) but OFF until moved there.
    REENTRY_CHURN_GUARD_ENABLED: bool = False
    REENTRY_CHURN_GUARD_WINDOW_S: float = 3600.0
    REENTRY_CHURN_GUARD_USE_4BAR: bool = True
    # 2026-06-03 RECENT_REDUCTION_GUARD (USER MANDATE — kill buy-high/sell-low churn at the ONE gate).
    # In execute_now, block any re-add (OPEN/AUGMENT/REENTRY) on a key within WINDOW_S of a REDUCE/CLOSE
    # on that SAME key UNLESS price makes a GENUINE 4-bar 3m Donchian breakout (dc_high4_3m long /
    # dc_low4_3m short) — a real continuation, not the bare exit-price cross-back that fed the loop.
    # Catches EVERY buy-side source (QUICK_OPEN, MOMENTUM_WATCHDOG, WT_3M_ESCALATE, daemon reentry) because
    # all route through execute_now. Reuses the existing _recent_reduces stamp. DEFAULT-OFF — proven in
    # backtest/counterfactual before enabling live. ROLLBACK: RECENT_REDUCTION_GUARD_ENABLED=False.
    RECENT_REDUCTION_GUARD_ENABLED: bool = True   # 2026-06-03 ENABLED (USER: crypto churning) — blocks bare exit-price cross-back re-adds (DAEMON_PRICE_CROSS_REENTRY/QUICK_OPEN/WT_3M_ESCALATE) within WINDOW_S of a reduce unless a genuine Donchian breakout. ROLLBACK: False.
    RECENT_REDUCTION_GUARD_WINDOW_S: float = 3600.0   # 2026-06-25 anti-churn (USER: crypto >50% loss). Was 900.0 — widen guard window to 1hr so the 13-min reenter→stale-exit→reenter loop (DAEMON_REENTRY_STALE_EXIT ang 283/16d) is blocked unless a genuine 4-bar DC breakout. ROLLBACK: 900.0.
    RECENT_REDUCTION_GUARD_USE_4BAR: bool = True
    # 2026-06-03 USER MANDATE: S1 = live trader, Mac = testing only. On a NON-server box, execute_now
    # + send_webhook refuse live orders when the server holds a fresh heartbeat for the account → no
    # double-trading. Fail-OPEN (Mac keeps managing positions until S1 is genuinely live). ROLLBACK: False.
    SERVER_HEARTBEAT_BLOCK_ENABLED: bool = True
    # 2026-06-03 USER: re-add foothold on CLOSE/REDUCE so every exit announces its reason (was OPEN-only). ROLLBACK: False.
    CLOSE_FOOTHOLD_ENABLED: bool = True
    # 2026-06-02 under-reentry fix: when a winner exits near the top and the EXTREME WT-cross
    # confirmation would block re-entry, allow it if the trend is still intact (price on the
    # right side of sma_200_15m). DEFAULT OFF — enable after A/B. Single-sourced in
    # vec_decisions/guaranteed_price_cross_reentry.py (live scalar == vec, parity-tested).
    REENTRY_SMA200_BACKUP_ENABLED: bool = False
    EZ_REENTRY_PRICE_CROSS_PARTIAL_FRAC: float = 0.5
    EZ_REENTRY_PRICE_CROSS_MAX_AGE_HOURS: float = 48.0
    EZ_REENTRY_PRICE_CROSS_MAX_FIRES_PER_TICK: int = 3  # 2026-07-08 GAINMO anti-churn: 20→3 fires/tick
    REENTRY_MAX_PRICE_DIVERGENCE_PCT: float = 20.0
    QUICK_HEDGE_SAME_SYM_LAST_RESORT_ENABLED: bool = False
    REENTRY_BYPASS_CONFIRMATION_THRESHOLD_PCT: float = 0.002
    UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED: bool = True
    BREAKOUT_LEASH_ENABLED: bool = True
    BREAKOUT_LEASH_QTY_MULT: float = 0.25
    BREAKOUT_LEASH_REENTRY_MULT: float = 1.50
    BREAKOUT_LEASH_TF: str = "3m"
    # Queue consumer: reads daemon command files, calls execute_now.
    EZ_REENTRY_QUEUE_CONSUMER_ENABLED: bool = True
    EZ_REENTRY_QUEUE_CONSUMER_INTERVAL_S: float = 5.0
    # EZ_REENTRY_PRICE_CROSS_BLOCK_DURATION_S removed 2026-04-28 — replaced by upstream
    # is_reentry_eligible() pre-flight gate at the source of each REENTRY call so we
    # never call execute_now when we already know it'll be blocked. Per user mandate
    # "find from where the call came and ADD THE FILTER there instead of just dumbly
    # turning it off when it might be a critical close from somewhere else".
    # 2026-04-28 — When PARTIAL_PROFIT_LOCK has fired (50% closed at small profit),
    # treat remaining position's gain as raw_gain / (1 - PPL_FRAC). For FRAC=0.5
    # this DOUBLES the effective gain so augment/reentry eligibility (gain>MIN_GAIN)
    # is reachable on a profit-locked position. User: realized half is bookable
    # profit, remaining half is essentially "free" — should not block augments.
    EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED: bool = True
    # 2026-04-27 — hedge_decisions.should_close_hedge_wt3m1h now configurable via HEDGE_CLOSE_MODE.
    # Default 'wt_3m_1h' = LEGACY behavior (was hardcoded since 2026-04-26). Sweep-testable alternatives:
    # 'wt_3m' / 'wt_3m_15m' / 'wt_3m_15m_1h' (3-TF strict) / 'wt_3m_15m_htf1' (3m+15m+1of{1h,4h,D})
    # 'wt_3m_15m_htf2' / 'wt_3m_15m_htf3' (3m+15m+ALL HTF) / 'wt_dc_score' (use wt_dc_exit_scorer).
    # 2026-05-17 R6 ROLLBACK: was 'wt_3m'. 'wt_3m_and_1h' = require BOTH 3m AND 1h flip before close → fewer premature closes at loss.
    HEDGE_CLOSE_MODE: str = 'wt_3m_and_1h'  # ROLLED BACK 2026-05-17 (was 'wt_3m')
    HEDGE_CLOSE_WT_DC_THRESHOLD: float = 25.0
    # === 2026-04-30 RESTORED HEDGE_BANDAID_OFF (the rule that's been here for 500 yrs) ===
    # User: "CLOSE the hedge when wt_15m goes against it. Then open again when it goes in favor.
    #  PLUS when losing (original) positions closes hedge closes at the same moment. Same as
    #  last 500 years. until you took it out. now you have to put it back acagin for the 1000th time."
    # Implementation lives at ez_positions_quick.py:5384-5392. Was disabled (default False)
    # since some prior agent removed the config line. Re-enabled here. NO P/L gate — when 15m
    # WT flips to favor origin, hedge closes regardless of gain (that's the whole point of the rule).
    # Reopen handled by scan_and_hedge_losers when 15m WT goes against origin again.
    # Origin-close → hedge-close is _close_associated_hedge in ez_manage.py:14453 (always was on).
    HEDGE_BANDAID_OFF_ENABLED: bool = False  # 2026-05-29 USER: ZERO hedging anywhere. Only path emitting HEDGE-tagged events past HEDGE_MODE=False (legacy-hedge unwind); active_hedges=0 all accts → nothing to unwind → guaranteed zero hedge events.
    # USER 2026-05-05 mandate: when position underwater AND wt1_3m flipped against trade:
    #   if NO active hedge → fire hedge NOW
    #   if hedge already active → close primary IMMEDIATELY (don't bleed further)
    # Signal-driven (not %-based) — fires in process_position EARLY before other paths.
    UNDERWATER_HEDGE_OR_CLOSE_ENABLED: bool = True
    UNDERWATER_HEDGE_OR_CLOSE_HTF_CLOSE_REQUIRED: int = 2  # N of 4 HTF (15m/1h/4h/D) must agree before force-closing origin when hedge active
    UNDERWATER_HOC_USDC_MAKER_BYPASS: bool = True  # USDC perp + MICRO_SCALP_USDC_MAKER_ENABLED = 3m ok to close+reenter (zero maker fee)
    # USER 2026-05-05 (LUNC -65% incident on ang): catastrophic-loss safety net.
    # Even with hedging, positions reached -65% because hedges kept getting closed (HEDGE_BANDAID_OFF)
    # while the underlying short bled unbounded. These two caps prevent "ridiculous holds":
    #   1. RIDICULOUS_LOSS_PCT — absolute loss ceiling: ANY position past this is force-closed regardless of hedge state.
    #   2. RIDICULOUS_HOLD_HOURS — max underwater duration: position underwater this long → force-closed.
    # Fires in process_position BEFORE the standard UNDERWATER_HEDGE_OR_CLOSE logic.
    # Note: previous HARD_MAX_LOSS_PCT=-5% destroyed gains. -15% is the empirical "definitely dead" threshold.
    RIDICULOUS_HOLD_GUARD_ENABLED: bool = False  # USER 2026-05-16: PHBUSDT $0.0751→$0.096 (-27%) demonstrated this fires LATE — by the time guard checked, position was -27% not -15%. Locks in worst-case loss. Per user's "ONLY 3 sanctioned loss-exit paths" mandate (R1/R2/HEDGE_FAILED), RIDICULOUS_LOSS is NOT sanctioned. Disabling both RIDICULOUS_HOLD (already neutered at 720h) and RIDICULOUS_LOSS.
    RIDICULOUS_LOSS_PCT: float = -15.0    # absolute loss cap — only checked when GUARD_ENABLED above (now False)
    RIDICULOUS_HOLD_HOURS: float = 720.0  # USER 2026-05-11: was 48h → 720h (30 days). 64.1% of all closes in 24h were RIDICULOUS_HOLD time-caps closing losers at -12/-15/-17%. Not one of the 3 sanctioned loss-exit paths (R1/R2/HEDGE_FAILED). See also RIDICULOUS_HOLD_REQUIRE_GAIN_NONNEG below.
    RIDICULOUS_HOLD_REQUIRE_GAIN_NONNEG: bool = True  # USER 2026-05-11: HOLD-cap path may only flatten stale positions when gain >= 0 (clean-up winners that ran out of momentum). Losers get hedged via OBLIGATORY_HEDGE / R1 / R2 instead.
    # USER 2026-05-06 (1000LUNC -18% incident): when DC/BB Daily band breaks (UP or DOWN),
    # close any wrong-side position and immediately open opposite. Reverse AGAIN if same level
    # crossed back (per-sym state tracked in trade_manager._dc_bb_d_break_state).
    DC_BB_D_BREAK_REVERSE_ENABLED: bool = True
    # USER 2026-05-06 (ZECUSDC +94% week / max gain $0.70 / 6× DC_CROSSBACK closes today):
    # PARABOLIC PROTECTION — let the trend run on extreme momentum instead of whipsawing
    # exits/reentries. Applies to: DC_BB_D_BREAK_REVERSE, WT15M_AGAINST_FORCE_CLOSE,
    # MANDATORY_REENTRY K-extreme block. Confluence required: rsi_4h + rsi_1h + bb_pct_b_4h.
    PARABOLIC_PROTECTION_ENABLED: bool = True
    PARABOLIC_RSI_4H_MIN: float = 70.0
    PARABOLIC_RSI_1H_MIN: float = 65.0
    PARABOLIC_BB_PCT_B_4H_MIN: float = 0.70  # was 0.90 — lowered 2026-05-07: ZEC +57% run, parabolic gate was never firing
    PARABOLIC_RSI_4H_MAX: float = 30.0
    PARABOLIC_RSI_1H_MAX: float = 35.0
    PARABOLIC_BB_PCT_B_4H_MAX: float = 0.10
    # DC_BB crossback hysteresis — price must drop this % BELOW break level before CROSSBACK fires
    # Prevents hair-trigger flips on tiny consolidations during strong trends (ZEC 2026-05-07)
    DC_BB_CROSSBACK_HYSTERESIS_PCT: float = 2.0
    # BB breakout continuation window — after DC/BB daily break, protect the position for N hours.
    # Suppresses DC_CROSSBACK_TO_SHORT (or LONG) so pullbacks to BB basis don't flip direction.
    BB_BREAKOUT_CONT_ENABLED: bool = True
    BB_BREAKOUT_CONT_HOURS: float = 72.0
    # USER 2026-05-06: when extreme overbought, allow SHORT entry/augment to fire even on a
    # rising ticker (override SHORT_SMA_GATE) — so we can short the demise of parabolic moves
    # instead of being locked out by trend filters. Mirror for extreme oversold + LONG.
    EXTREME_OB_OS_OVERRIDE_ENABLED: bool = True
    EXTREME_OB_RSI_4H_MIN: float = 80.0
    EXTREME_OB_RSI_D_MIN: float = 75.0
    EXTREME_OB_BB_PCT_B_4H_MIN: float = 1.0
    EXTREME_OS_RSI_4H_MAX: float = 20.0
    EXTREME_OS_RSI_D_MAX: float = 25.0
    EXTREME_OS_BB_PCT_B_4H_MAX: float = 0.0
    # USER 2026-05-06: wt1_15m flipped against trade → fire hedge IMMEDIATELY (no matter what).
    # If hedge already active and bleed continues → close primary. 30s per-position cooldown.
    WT15M_AGAINST_FORCE_HEDGE_ENABLED: bool = False  # 2026-05-29 USER: ZERO hedging anywhere (was True).
    WT15M_AGAINST_FORCE_HEDGE_COOLDOWN_SEC: float = 30.0
    # USER 2026-05-06: ALL TFs (3m/15m/1h/4h/D) against → close primary, hedge becomes main.
    # Strongest single signal — no other gate considered.
    ALL_TF_AGAINST_CLOSE_ENABLED: bool = True
    ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC: float = 30.0
    # 2026-05-08 USER MANDATE — WT_15M_VEL_SLOW exit branch (loss-bypass companion
    # to dc_low4_3m / dc_high4_3m breach exits). Fires immediate close BEFORE
    # hedging logic — skips NO_LOSS, skips MTF requirements, skips cooldowns.
    #
    # Trigger (LONG; mirrored for SHORT):
    #   gain < WT_15M_VEL_SLOW_GAIN_BAND_PCT (default 0.10 — fires on slight profit
    #     OR ANY loss; "loss or not" per 2026-05-09 user spec)
    #   AND wt_velocity_15m sign opposes position
    #   AND (|wt_velocity_15m| <= WT_15M_VEL_NEAR_ZERO_THRESHOLD     OR
    #        |wt_velocity_15m| < |wt_velocity_15m_prev|)
    #     i.e., momentum is either near-zero (≤0.1) OR decelerating against us.
    #
    # 2026-05-09: widened band 0.05 → 0.10, added velocity-near-zero alternative.
    # Original 0.05 + decel-only path was too narrow; positions could drift to
    # -2% before the abs(gain)<0.05 ever fired again.
    WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED: bool = True
    # 2026-05-09 USER MANDATE: thresholds become configurable + dynamic, not fixed.
    # Band defines "approaching 0 gain" zone where R2 fires. Floor ensures we don't
    # close at a real loss (R1 + hedges handle losses). Decel ratio is the dynamic
    # slowdown gate replacing fixed NEAR_ZERO threshold per user
    # ("some moves are fast others are slow this can never be a fixed number").
    # 2026-05-09 USER REFINEMENT — R2 fires ONLY on peak-then-collapse, not from-open-tiny-profit:
    #   max_gain (peak ever seen this position) >= R2_PEAK_MIN_PCT
    #   AND R2_GAIN_FLOOR_PCT <= current gain <= R2_GAIN_BAND_PCT
    # I.e. position WAS profitable (≥0.5%), gain has FALLEN BACK to ≤0.10, momentum dying.
    # Position that opened straight into a small loss without ever reaching +0.5% is NOT
    # eligible — that's R1 (DC4 newborn window) territory.
    R2_PEAK_MIN_PCT: float = 0.5                   # max_gain must have peaked ≥ this
    WT_15M_VEL_SLOW_GAIN_BAND_PCT: float = 0.10    # current gain ≤ this (collapsed back to ~0)
    WT_15M_VEL_SLOW_GAIN_FLOOR_PCT: float = 0.01   # current gain ≥ this (don't close at a real loss)
    WT_15M_VEL_NEAR_ZERO_THRESHOLD: float = 0.1    # legacy fixed gate (still readable but bypass-able by ratio)
    WT_VEL_DECEL_RATIO: float = 0.5                # |vel| < |vel_prev| * RATIO → "decelerating" — DYNAMIC
    WT_VEL_USE_DECEL_RATIO_ONLY: bool = True       # 2026-05-09: default ON crypto. False = legacy NEAR_ZERO OR decel.
    R2_TF_LIST: tuple = ('15m',)                   # crypto: 15m primary. Sweep tests 1h too.
    # 2026-05-09 USER MANDATE — R3 HEDGE_INVARIANT loss-bypass dump.
    # When wt1_3m AND wt1_1h are against a position AND gain<0 AND no hedge
    # exists (and none is pending) → DUMP at a loss, skip NO_LOSS, skip MTF.
    # The invariant: any losing position with both LTF (3m+1h) WT against MUST
    # have a live hedge. If the hedge engine failed to set one up, the position
    # is naked and bleeds — better to dump and alert root cause.
    # Hedge presence: tracker_manager.active_hedges where hedge_for==position_key
    # AND account match. Pending: Redis 'hedge_pending:<key>' (60s TTL).
    R3_HEDGE_INVARIANT_DUMP_ENABLED: bool = True
    R3_GAIN_MAX_PCT: float = 0.0  # only fires when gain < this (default 0 = any loss)
    # 2026-05-10 USER MANDATE — HTF-trend veto on GOLDEN_RULE + GUARANTEED_REENTRY.
    # STRKUSDT_SHORT was opened in a clear D-up trend (price 0.026→0.06 over 2 wks)
    # because phase-1 BREAKOUT only checked wt1_3m<wt2_3m and price below dc_low_1h.
    # Veto: don't enter AGAINST the HTF trend regardless of LTF setup.
    #   LONG vetoed when D bearish (ha_D=='red' AND wt1_D<wt2_D)
    #     OR (HTF_VETO_REQUIRE_D=False) 4h bearish.
    #   SHORT vetoed when mirror.
    # ═══════════════════════════════════════════════════════════════════════════
    # 2026-05-10 USER MANDATE — GOLDEN_RULE + GUARANTEED_REENTRY stay ENABLED.
    # Both are sacred per user. Reentry and hedge are OBLIGATIONS, not options.
    # The fix for STRKUSDT_SHORT (opened in clear D-up) is NOT to disable the
    # producer — it's to harden the entry gate INSIDE the producer with:
    #   (a) ≥ TF_CONSENSUS_REQUIRED of 5 TFs (3m/15m/1h/4h/D) agreeing
    #   (b) ≥ INDICATOR_CONSENSUS_REQUIRED of 15 indicators agreeing
    #       (3 indicators × 5 TFs: WT direction, stoch K vs D, HA color)
    #   (c) REQUIRE_HEDGE_OPEN=True forces a paired-side hedge to be queued in
    #       the SAME tick as the entry. If the hedge can't be opened, refuse
    #       the entry too — never enter naked.
    GOLDEN_RULE_ENABLED: bool = True                       # SACRED — never disable
    LEGACY_GUARANTEED_REENTRY: bool = True                 # OBLIGATION — never disable
    # 2026-05-16 wire-up: these were previously read via getattr(config, ..., default)
    # in backtest_v8_engine.py:4497-4508 and vec_paths/golden_rule_enforce.py:88-99
    # WITHOUT being defined as BotConfig fields — so JSON overrides silently dropped
    # (the dataclass loader rejects unknown keys). Defaults below match the prior
    # getattr defaults; behavior is identical until a sweep overrides them.
    GOLDEN_RULE_BASE_USD: float = 5.0
    GOLDEN_RULE_DC_15M_ENABLED: bool = True
    GOLDEN_RULE_BB_15M_ENABLED: bool = True
    GOLDEN_RULE_DC_1H_ENABLED: bool = True
    GOLDEN_RULE_BB_1H_ENABLED: bool = True
    GOLDEN_RULE_DC_4H_ENABLED: bool = True
    GOLDEN_RULE_BB_4H_ENABLED: bool = True
    GOLDEN_RULE_DC_D_ENABLED: bool = True
    GOLDEN_RULE_BB_D_ENABLED: bool = True
    GOLDEN_RULE_DC_W_ENABLED: bool = True   # 2026-05-17 USER: +W
    GOLDEN_RULE_BB_W_ENABLED: bool = True   # 2026-05-17 USER: +W
    GOLDEN_RULE_MULT_15M: float = 1.0
    GOLDEN_RULE_MULT_1H: float = 1.5
    GOLDEN_RULE_MULT_4H: float = 2.0
    GOLDEN_RULE_MULT_D: float = 3.0
    GOLDEN_RULE_MULT_W: float = 4.0         # 2026-05-17 USER: +W mult (cascade 1.0/1.5/2.0/3.0/4.0)
    # USER 2026-05-18: activation/entry TF split (mirrors STDEV_BREAKOUT_HTF_LIST + STDEV_BREAKOUT_RETEST_TF_LIST).
    # Previously GR scored all 6 TFs equally — a 3m wick contributed the same vote as a Daily bar.
    # New: breakout must HAPPEN on activation TF (D/4h), entry trigger must CONFIRM on entry TF (1h/15m/3m).
    # Same structure as STDEV_BREAKOUT which has been working in tradier_manage.py.
    GOLDEN_RULE_REQUIRE_ACTIVATION: bool = True              # if True, gate fails when no activation TF shows breakout
    GOLDEN_RULE_ACTIVATION_TF_LIST: List[str] = field(default_factory=lambda: ["D", "4h"])  # WHERE breakout must happen
    GOLDEN_RULE_ENTRY_TF_LIST: List[str] = field(default_factory=lambda: ["1h", "15m", "3m"])  # WHERE entry trigger fires
    # The GOLDEN_RULE entry consensus uses the EXISTING tested config keys
    # GOLDEN_RULE_HTF_MIN_TFS and GOLDEN_RULE_MIN_IND (defined at line ~1964
    # below) — same knobs that backtest_v8_engine.py and backtest_v8_sweep.py
    # already test. Default 0 = disabled, sweep winners drive the live value.
    # No new consensus knobs; no hardcoded thresholds. The gate inside the
    # GOLDEN_RULE producer in ez_manage.py reads those same keys.
    GOLDEN_RULE_REQUIRE_HEDGE_OPEN: bool = True            # mandatory paired hedge (TODO wire)
    GUARANTEED_REENTRY_REQUIRE_HEDGE_OPEN: bool = True
    # HTF-trend vetoes — defense-in-depth alongside the consensus gate.
    GOLDEN_RULE_HTF_VETO_ENABLED: bool = False  # USER 2026-05-11: was True → False. Veto was killing day-1 LONG entries on every breakout (Daily HA still red when 3m flips up). Live evidence: missed 1000BONK / TON / ZEC rallies entirely. Backtest GR_HTF_VETO_off shows ambiguous signal (sub-floor DIAGNOSTIC); user mandate explicit.
    GUARANTEED_REENTRY_HTF_VETO_ENABLED: bool = False  # USER 2026-05-11: same rationale — REENTRY must fire on bounce regardless of HTF, mirroring the entry-side loosening.
    HTF_VETO_REQUIRE_D: bool = True  # (no-op while HTF_VETO_ENABLED=False) True=D mandatory bearish/bullish; False=either D or 4h
    # R1 — DC4_3M EMERGENCY CLOSE within newborn window (USER 2026-05-09)
    # Fires while position is fresh and price breaks 4-bar 3m channel low/high.
    # Bypasses NO_LOSS, hedge, MTF. Desktop alert + JSONL log naming entry signal.
    R1_DC_LOW4_3M_EMERGENCY_ENABLED: bool = True   # 2026-05-21 22:47 USER MANDATE: RE-ENABLED after ORDIUSDC top-of-range incident — emergency close on dc4_3m breach is back ON. Previous 2026-05-20 OFF was based on assumption that MTF compound exit would replace it; MTF did not fire on ORDI 6%+ drawdown so R1 is restored.
    R1_NEWBORN_WINDOW_MIN: float = 15.0            # kept for legacy; fixed-stop now active
    # ═══════════════════════════════════════════════════════════════════════════
    # 2026-05-21 22:47 USER MANDATE — NEWBORN_LOSS_KILL.
    # After ORDIUSDC bought at 1h channel high then wicked 14%, user mandate:
    # "STUPIDITIES like ORDI entry AT THE VERY FUCKING TOP of all timeframes can NEVER
    # happen and EVEN IF THEY DO THEY GET CLOSED IMMEDIATELY when price < entry price."
    # NEWBORN_LOSS_KILL implements the close-side: any position younger than
    # NEWBORN_LOSS_KILL_WINDOW_MIN with gain < NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT
    # is force-closed, bypassing UNIVERSAL_NOLOSS_GATE. Hedges excluded.
    # ROLLBACK: NEWBORN_LOSS_KILL_ENABLED=False.
    # ═══════════════════════════════════════════════════════════════════════════
    NEWBORN_LOSS_KILL_ENABLED: bool = False        # 2026-05-22 00:00 — V1 DISABLED after A/B (ΔSharpe=-0.0139). V2 adds WT vel confirmation; awaiting A/B verdict before enabling.
    NEWBORN_LOSS_KILL_WINDOW_MIN: float = 30.0     # window from open within which loss-kill applies
    NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT: float = 0.0   # 2026-05-22 V3 breakeven exit (V1 -0.5% / V2 -0.5%+vel both -ΔSharpe). User: "CLOSE AT ENTRY PRICE as they most likely fall back".
    NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST: bool = True  # 2026-05-22 V2: only close if wt_velocity_TF also against position (not a wick)
    NEWBORN_LOSS_KILL_VEL_TF: str = ""             # auto: "3m" crypto / "5m" tradier when empty
    NEWBORN_LOSS_KILL_MIN_AGE_MIN: float = 15.0    # 2026-05-22 V4 user: "they need time to breathe so do not let it kick in until 12-25 min after open"
    NEWBORN_LOSS_KILL_SURGICAL_ONLY: bool = True   # 2026-05-22 V4: only close positions that were BREAKOUT entries (bypassed TOR via raw_dc_pos>1.0)
    # ═══════════════════════════════════════════════════════════════════════════
    # 2026-05-22 TOP_OF_RANGE_BLOCK — prevent ORDI-style top-of-range entries.
    # Block OPEN/AUGMENT when price in top THRESHOLD% of DC channel on ALL listed TFs
    # (LONG), or bottom (SHORT). Vec A/B (sub-floor, 7 syms × 1.95yr): ΔSharpe=+0.0009.
    # Marginal positive — user mandate "if positive deploy". ROLLBACK: ENABLED=False.
    # ═══════════════════════════════════════════════════════════════════════════
    TOP_OF_RANGE_BLOCK_ENABLED: bool = True
    TOP_OF_RANGE_BLOCK_THRESHOLD: float = 0.95
    TOP_OF_RANGE_BLOCK_TF_LIST: str = "1h,4h,D"
    TOP_OF_RANGE_BLOCK_REQUIRE_ALL: bool = True
    # USER 2026-05-18: FROZEN ACTIVATION-TF STOP — replaces RIDICULOUS_LOSS late-fire (PHBUSDT -27% lock-in).
    # At first per-bar evaluation, freeze dc_low_4h (LONG) / dc_high_4h (SHORT) on position.
    # Per-bar check: if current_price breaches frozen level AND gain<0 → CLOSE (FROZEN_ACT_STOP_FROZEN_BREACH).
    # Or if gain ≤ FROZEN_ABSOLUTE_FLOOR_PCT_CRYPTO → CLOSE (FROZEN_ACT_STOP_ABSOLUTE_FLOOR). Active path mirroring stocks team's finding.
    FROZEN_ACTIVATION_STOP_ENABLED: bool = True
    FROZEN_ACTIVATION_TF: str = "4h"               # which TF's dc_low/high we freeze at entry (D / 4h)
    BB_FROZEN_STOP_ENABLED: bool = True
    BB_FROZEN_STOP_TF: str = "1h"
    BB_FROZEN_STOP_FIELD: str = "lower"
    LIVE_VEC_STALE_MARK_PRICE_ENABLED: bool = False
    LIVE_VEC_EMERGENCY_BRAKE_ENABLED: bool = False
    LIVE_VEC_QUARANTINE_STRATEGY_ENABLED: bool = False
    LR_PCTB_D_LONG_ENTRY_ENABLED: bool = False
    LR_PCTB_D_LONG_ENTRY_THRESHOLD: float = 0.20
    # 2026-07-15 grey-band long regression channel (bt_band_bounce v2: long windows >> LINREG_LENGTH=50)
    LR_CHANNEL_LONG_LENGTHS: dict = field(default_factory=lambda: {"1h": 200, "4h": 200, "D": 300})
    BAND_SLOPE_SIZING_V2_ENABLED: bool = True      # 2026-07-15 v2-v5 campaign: grad sizing uplift positive on 176-sym 6.5yr (L200+ quality subsets +2.3..+8.4%/trade); entry system stays OFF (Noise) — sizing only, conservative clamps
    BAND_SLOPE_SIZING_V2_TF: str = "4h"
    BAND_SLOPE_SIZING_V2_DEPTH_GAIN: float = 1.0
    BAND_SLOPE_SIZING_V2_SLOPE_NORM_PCT_DAY: float = 1.0
    BAND_SLOPE_SIZING_V2_MIN: float = 0.7
    BAND_SLOPE_SIZING_V2_MAX: float = 1.8
    # 2026-07-15 LR_BAND swing-harvest strategy (bt_band_bounce v7: crypto 4h_L200 r2_0.7 L-only
    # pool_sharpe 0.4427 / sym_sharpe 0.51 / +24.6%/sym/yr). DEFAULT OFF — Tier-2 A/B required
    # (kill switch per NEW STRATEGY PROHIBITION). Harvest/BE/readd knobs declared for the engine.
    LR_BAND_ENTRY_ENABLED: bool = False
    LR_BAND_ENTRY_TF: str = "4h"
    LR_BAND_ENTRY_LO: float = 0.1
    LR_BAND_ENTRY_R2_MIN: float = 0.7
    LR_BAND_ENTRY_SIDES: str = "L"
    LR_BAND_HARVEST_HI: float = 0.7
    LR_BAND_HARVEST_FRAC: float = 0.25
    LR_BAND_HARVEST_ENABLED: bool = False          # 2026-07-19 USER band mandate: upper-band exit wired in ez_manage (was dead knob); OFF until Tier-2 pack proof
    LR_BAND_REGIME_ENABLED: bool = False           # 2026-07-20 USER: catch EVERY upswing — long anywhere below REGIME_MAX_PB while channel slope>0
    LR_BAND_REGIME_MAX_PB: float = 0.6
    LR_BAND_SLOPE_FLIP_EXIT_ENABLED: bool = False  # 2026-07-20 USER: channel slope flip → full PROFIT exit
    LR_BAND_READD_LO: float = 0.3
    LR_BAND_BE_RATCHET: bool = True
    LR_BAND_EXIT_EXEMPT: bool = True
    FROZEN_ABSOLUTE_FLOOR_PCT_CRYPTO: float = -10.0  # crypto more volatile than stocks; loosen vs -8 default. Sweep range: -5/-8/-10/-15.
    R1_USE_DC_4BAR: bool = False                   # 2026-07-01 USER: was DEAD (never read); now WIRED. 4-bar dc_low4_3m (tight) churned R1; 20-bar dc_low_3m (wide) proven better for CRYPTO → False. True=4-bar, False=20-bar dc_low_3m. ROLLBACK True.
    # Backtest DC stop loss sweep flags (crypto uses 3m TF):
    DC_LOW4_STOP_ENABLED: bool = False             # stop at dc_low4_3m/dc_high4_3m recorded at entry
    DC_LOW_STOP_ENABLED: bool = False              # stop at dc_low_3m/dc_high_3m (1-bar, wider)
    # 2026-05-15 USER: when DC4 stop would close, check GR score against position.
    # If GR ≥ DC4_STOP_GR_SCORE_MIN_TFS TFs × DC4_STOP_GR_SCORE_MIN_IND ind (default 3×5=15),
    # hedge instead of close. Sweep variant: DC4_GR_HEDGE_ON.
    DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED: bool = False
    DC4_STOP_GR_SCORE_MIN_TFS: int = 3
    DC4_STOP_GR_SCORE_MIN_IND: int = 5
    R1_TF: str = '3m'                              # sweep-testable
    # _DUPLICATE_OPEN_GUARD gain-based replacement (USER 2026-05-09):
    # Replaces 900s time-cooldown with a gain gate. Augments require gain > 0.5*MIN_GAIN.
    DUP_GUARD_GAIN_MULTIPLIER: float = 0.5         # threshold = MULT * config.MIN_GAIN (=1.5% by default)
    DUP_GUARD_USE_GAIN_GATE: bool = True            # 2026-06-02 USER MANDATE "augment should never fire without a gain check first": ON restores the 2026-05-09 gain-gate (AUGMENT requires gain > MIN_GAIN*DUP_GUARD_GAIN_MULTIPLIER = 1.5%). Was False (900s time-cooldown only, NO gain check) → let GOLDEN_RULE_SHORT + every augment add to LOSERS (the documented SHORT-martingale bleed). REENTRY (is_augment=False, ez_manage:17251) + OPEN (pos_val=0) UNAFFECTED. To allow adds from gain>0 instead of >1.5%, set DUP_GUARD_GAIN_MULTIPLIER=0. ROLLBACK: False (time gate).
    # 2026-05-08 USER MANDATE — ratio_rebalance: close OVERWEIGHT side instead of opening
    # underweight. Picks positions with smallest |wt1_15m - wt2_15m| (least conviction).
    # Set False to re-enable the old open-underweight path once system is verified.
    RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY: bool = True
    RATIO_REBALANCE_MAX_CLOSES: int = 3
    # 2026-05-09 USER MANDATE — OVERTRADE_GUARD in execute_now caps OPEN/AUGMENT
    # to this many per (pkey × UTC day). CLOSE/REDUCE not capped. Emergency-exit
    # reasons (RIDICULOUS, BREAK_REVERSE, ALL_TF_AGAINST, INTERVENTION, MANUAL)
    # bypass the cap. Set 0 to disable.
    TRADES_PER_SYM_PER_DAY_MAX: int = 8  # 2026-07-08 GAINMO anti-churn: 50→8 rollback. The 2026-05-21 starvation (attempt-time counting + 99-100% block rates) no longer applies post-loosening; force-open/OBLIGATORY reasons bypass this guard anyway (ez_manage.py ~23247). CAVEAT: crypto still counts attempts (tradier counts submissions since 2026-07-03) — if BLOCKED_OVERTRADE starves real entries, port the count-on-submission fix, do NOT re-raise the cap.
    FOOTHOLD_PILEON_ENABLED: bool = False  # 2026-05-12 ADDED: emergency kill of hardcoded 5-min lock that fires on 3 attempts. Was blocking breakouts. Default OFF.
    # 2026-05-09 USER MANDATE — sweep gating thresholds.
    # Cheap test (12 syms × 4 mo): variants below DISCARD floor are flagged DISCARD.
    # Only variants with pool_sharpe ≥ DEEP_TEST floor AND positive monthly gain
    # qualify for the expensive 4-year × 48-symbol sweep.
    SWEEP_DISCARD_POOL_SHARPE_FLOOR: float = 0.4
    SWEEP_DEEP_TEST_POOL_SHARPE_MIN: float = 0.5  # 4yr deep test gate
    SWEEP_DEEP_TEST_GAIN_PER_MO_MIN_PCT: float = 1.0  # ≥ 1%/mo to deserve deep test
    # 2026-05-09 SWEEP-EXPOSURE — knobs that were hardcoded in ez_manage.py, now config.
    # AUGMENTED_POSITIONS_GUARD floor at ez_manage.py:12190 was `0.5 * MIN_GAIN`; tune via this mult.
    AUGMENTED_POSITIONS_GUARD_FLOOR_MULT: float = 0.5
    # ALL_TF_AGAINST_CLOSE at ez_manage.py:20633 required ALL 5 TFs against; tune min_tfs (1..5).
    ALL_TF_AGAINST_CLOSE_MIN_TFS: int = 4   # USER 2026-05-30: 4/5 crypto (stocks 5, use more TFs)
    # check_entry_vetting NO_STRUCT_OR_BREAKOUT at ez_manage.py:507 had no toggle.
    # When False, the "structure_ok or dc_breakout" requirement is bypassed (entry trigger alone gates).
    ENTRY_VET_NO_STRUCT_OR_BREAKOUT_REQUIRED: bool = True
    # 2026-05-21 20:10 — Graded relax-mode replacing boolean. Per user "no switch-off, parameter sweeps".
    # 0=strict (both structure_ok AND dc_breakout required), 1=current (either, default), 2=trigger-only, 3=auto-pass.
    # Sweep [0,1,2,3] for positive delta. ROLLBACK: 1.
    ENTRY_VET_RELAX_MODE: int = 1
    # User 2026-05-05 (1000LUNCUSDT): BANDAID_OFF was killing the hedge on a 15m
    # flip even while wt_3m still agreed with the hedge AND origin was still
    # losing — leaving the underlying SHORT naked at -45%. With this guard,
    # BANDAID_OFF requires wt_3m to ALSO flip back to favor origin OR the origin
    # to have recovered above BANDAID_OFF_LOSER_RECOVER_PCT.
    HEDGE_BANDAID_OFF_REQUIRE_WT_3M_FLIP: bool = True
    BANDAID_OFF_LOSER_RECOVER_PCT: float = -0.25
    # User 2026-05-05: same-symbol hedge fires when wt1_3m agrees with the hedge
    # direction (i.e., wt_3m against the loser). Original BC_988 rule was 15m OR
    # (3m+1h) which missed the 1000LUNCUSDT case where 15m was friendly to the
    # loser but 3m had already flipped against it. Trigger lives at
    # ez_positions_quick.py:5044-5048.
    # 2026-05-17 R6 ROLLBACK: was True. False = require 3m AND 1h flip to open hedge → reduces over-eager hedge firing in noise.
    HEDGE_TRIGGER_USE_WT_3M_ALONE: bool = False  # ROLLED BACK 2026-05-17 (was True)
    # 2026-05-10 misinterpretation safety: WT_3M_OPEN_GATE was added to refuse OPEN/AUGMENT/REENTRY when wt1_3m is against.
    # User clarified that's already-implicit behavior elsewhere — keep code as sweep knob, default OFF so it doesn't fire.
    WT_3M_OPEN_GATE_ENABLED: bool = False
    # Companion: peak-decay nuke. When hedge gain peaks >1% then drops back to 0.5% → close before
    # going negative. Default True per the historical "exist as SHORT as possible, NEVER close at a loss"
    # paragraph at ez_positions_quick.py:5385 — this is the "before negative" half of that rule.
    HEDGE_DECAY_NUKE_ENABLED: bool = True
    # === 2026-04-29 SURFACED hidden wt_dc_exit_scorer knobs (were implicit defaults via getattr-fallback in wt_dc_exit_scorer.py) ===
    # ez_manage.py and tradier_manage.py both call wt_dc_score_exit. Crypto live had ZERO of these declared,
    # so wt_dc_exit_scorer.py was using its hardcoded fallbacks (5/75/0.80). Now explicit + sweep-testable.
    # Test C v8_quick verdict (12sym×2yr crypto NOLOSS=off): 5/5 K=85 wins pool_sharpe 0.1695 vs simple_mtf 0.105 vs delta3 0.135 vs loose 3/5 K75 0.142.
    WT_DC_EXIT_THRESHOLD: float = 25.0          # ez_manage.py read with default 25 — surfaced for sweep
    WT_DC_LONG_ENABLED: bool = False          # REVERTED 2026-08-11 per audit M3 — cross-connect unvalidated, re-enable only via 1yr Tier-2 per param
    WT_DC_SHORT_ENABLED: bool = False         # REVERTED 2026-08-11
    WT_DC_ENTRY_ENABLED: bool = False         # REVERTED 2026-08-11
    STOCH_CROSS_ENTRY_ENABLED: bool = False   # REVERTED 2026-08-11
    EXIT_SCORER_MIN_CONDITIONS: int = 5         # was implicit default 5; Test C confirms strict 5/5 wins on crypto too. Sweep 3,4,5.
    EXIT_SCORER_K_EXTREME: float = 75.0         # was implicit default 75. Sweep 70,75,80,85.
    EXIT_SCORER_DC_EXTREME: float = 0.80        # was implicit default 0.80. Sweep 0.70-0.90.
    EXIT_SCORER_PARTIAL_SCORE: float = 40.0     # was implicit default 40 (N-1 conditions). Sweep 30-50.
    EXIT_SCORER_FULL_SCORE: float = 100.0       # was implicit default 100. Stays.
    # === 2026-04-26 USER ABSOLUTE: cross-symbol hedge picker must verify WT across ALL TFs, not just velocity ===
    # _quick_hedge_rank rejects hedge candidates where < HEDGE_STRICT_WT_MIN_TFS_AGAINST of the 5 TFs (3m/15m/1h/4h/D) align against the proposed hedge direction.
    # Stops "shorting a rocket" — symbol may have negative wt_velocity_1h but still be raging on D/4h.
    # 2026-05-17 R6 ROLLBACK: was False. True = require 4-of-5 TF consensus before picking hedge candidate → fewer wrong-direction hedges in trending markets.
    HEDGE_STRICT_WT_ALL_TFS_ENABLED: bool = True  # ROLLED BACK 2026-05-17 (was False)
    HEDGE_STRICT_WT_MIN_TFS_AGAINST: int = 4  # Out of 5: 3m/15m/1h/4h/D. 4 = strong consensus; raise to 5 for unanimous, lower to 3 to relax.
    # === 2026-04-26 SCALP_V3 exit-trigger toggles (sweep-testable) — user hypothesis: V3 closes too early on minor 3m bar wobbles ===
    # Disable any of these to A/B test which V3 exit family is most/least valuable.
    SCALP_V3_EXIT_BAR_REVERSAL_ENABLED: bool = True   # Close LONG on 3m LL/LH (price turning down). Disable -> only WT/K trigger close.
    SCALP_V3_EXIT_WT_FLIP_ENABLED: bool = True        # Close LONG on wt1_3m < wt2_3m. Disable -> wait for bar/K signal.
    SCALP_V3_EXIT_K_CROSS_ENABLED: bool = True        # Close LONG on k_3m crossing down through 50. Disable -> wait for bar/WT signal.
    # 2026-04-26 USER: "exit at TOP not at fixed %". Require N of {bar,wt,k} signals to fire before closing.
    # 1 = OR (current); 2 = require 2/3 confirmation (filters noise); 3 = unanimous (most patient).
    SCALP_V3_EXIT_REQUIRE_N_SIGNALS: int = 3  # 2026-04-26 SHADOW LIVE A/B: sig3=-0.89% vs default=-15.21% over 483 cycles (17× better, WR 30%→47%, trades 113→17). Confirms research consensus: OR-fan exit IS the bleed cause; require all 3 confirmation signals before close. Reverts: 1 = original OR; 2 = compromise.
    # 2026-04-26 USER: V3 reentry pipeline broken — 33/44 V3-traded syms drop out of inf universe after close (0/345 same-key reentries).
    # Sticky window: after a V3 entry OR exit, mark sym/side in Redis with TTL so ez_rankings keeps it in symbols_inf_*_list for the next N min, allowing V3 to re-fire.
    SCALP_V3_REENTRY_STICKY_MIN: int = 30
    SCALP_V3_REENTRY_STICKY_ENABLED: bool = True
    # 2026-04-27 USER: V3 LONG only on inf_long-listed symbols (winners), SHORT only on inf_short-listed (losers). Stops "shorting rallies".
    # EXEMPTION: same-symbol hedge — if opposite-side position already open on this symbol, V3 may fire either direction (so a losing LONG can be hedged by V3-SHORT and vice versa).
    SCALP_V3_ENFORCE_UNIVERSE_DIRECTION: bool = True
    # 2026-04-27 USER-authorized HTF direction gate bypass for V3. Universe direction (LONG only on inf_long, SHORT only on inf_short) IS the trend filter; HTF gate would double-restrict.
    SCALP_V3_BYPASS_HTF_DIRECTION_GATE: bool = True
    # 2026-04-26 USER + research-agent verdict: technical exits should fire ONLY when in profit ("exit at top, never at loss").
    # If True and gain<=0, no BAR/WT/K close fires; only MAX_HOLD or hedge-engine handles the position. Aligns with STRICT_NO_LOSS doctrine.
    SCALP_V3_EXIT_PROFIT_ONLY: bool = False
    # 2026-04-26 USER: anchored VWAP gate. Default OFF — opt-in via shadow A/B first.
    # DC_BREAK = use vwap_dc_long/vwap_dc_short anchored at last DC channel break (no session assumption).
    # US_RTH   = use vwap_us_rth anchored at most-recent 13:30 UTC (US equity open; ~70% of crypto vol).
    # BOTH     = require LONG entry: price > vwap_dc_long AND price > vwap_us_rth (mirror SHORT).
    SCALP_V3_VWAP_FILTER_ENABLED: bool = False
    SCALP_V3_VWAP_TYPE: str = "BOTH"  # "DC_BREAK" | "US_RTH" | "BOTH"
    # === 2026-04-26 V3 entry-quality filters (research-agent recommendations to fix shameful WR 30-47%) ===
    # HTF SMA200 alignment: LONG entry requires price > sma_200_D AND price > sma_200_4h. Mirror SHORT. Research consensus: kills ~40% counter-trend losers.
    SCALP_V3_HTF_SMA200_ENABLED: bool = False
    # ATR percentile gate: skip entries when current ATR_3m below Nth percentile of last 100 bars (filters dead-vol chop).
    SCALP_V3_ATR_PCTL_GATE_ENABLED: bool = False
    SCALP_V3_ATR_PCTL_MIN: float = 40.0  # 40 = block bottom 40% of vol regimes
    # UTC session block: skip entries during these UTC hours (low-liquidity Asian-overnight window). Empty = no block.
    SCALP_V3_SESSION_BLOCK_HOURS: list = field(default_factory=list)  # e.g. [3,4,5] to block 03-06 UTC
    # === 2026-04-26 V3 entry-path expansion (user: "AUGMENT trades 20-1000x — more entry paths not stricter filters") ===
    # Each path is independently switchable; on each cycle V3 fires the FIRST path that matches. Default: TREND only (current behavior).
    # 2026-04-27 BAR_BREAK — pure price-action entry. Fires the moment a 3m bar
    # breaks against direction (wt1_3m + velocity_3m proxy in live since prev-bar
    # OHLC isn't in hot_metrics). User directive: "ACTUAL PRICE ACTION should have
    # entered when falling below prev close and exited at the rebounds." TREND
    # waits for K-cross + HTF stoch which lags. BAR_BREAK fires earlier; the global
    # HTF_DIRECTION_GATE downstream still blocks counter-macro entries.
    SCALP_V3_ENTRY_BAR_BREAK_ENABLED: bool = True
    SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN: float = 1.0    # |wt_velocity_3m| must exceed this to filter noise
    SCALP_V3_ENTRY_TREND_ENABLED: bool = True       # current strict trend-follow (HH+HL + k_3m rising + HTF stoch + WT bull)
    SCALP_V3_ENTRY_PULLBACK_ENABLED: bool = True    # 2026-04-27 user-authorized re-enable. Direction gate + HTF bypass + AUGMENT guards now prevent the prior multi-open / shorting-rallies issues
    SCALP_V3_ENTRY_DC_BREAK_ENABLED: bool = False   # 2026-04-27 OFF — same
    SCALP_V3_ENTRY_WT_CROSS_ENABLED: bool = False   # 2026-04-27 OFF — was firing SHORT on rallying WR-tagged symbols (>90% of V3 entries today)
    SCALP_V3_ENTRY_STOCH_BOUNCE_ENABLED: bool = False  # 2026-04-27 OFF — same
    # All 4 new paths flipped True per user "AUGMENT trades 20-1000x — more entry paths". Sig3 exit + reentry sticky + hedge no-close-at-loss are the safety net. Revert any to False to disable a single path.
    # === 2026-04-28 STDEV path — auto-tuned BB %B entry (BREAKOUT or BOUNCE) + band-rejection exit ===
    # MODE='BREAKOUT' fires LONG when bb_pct_b crosses above BREAK_HI on TF (with WT+k confirm).
    # MODE='BOUNCE' fires LONG when bb_pct_b drops below BOUNCE_LO and wt is turning up (mean-rev fit for V3).
    # TF='3m' or '15m'. EXIT_STDEV_REJECT adds an SDREJ signal when price reaches opposite band (target).
    # Default OFF — A/B variants flip these flags via shadow-runner overrides.
    SCALP_V3_ENTRY_STDEV_ENABLED: bool = False  # 2026-04-29 reverted to False for live default — phase3 shadow showed -0.37% / 189 cycles. Knob exists, sweep-only until shadow validates.
    SCALP_V3_STDEV_TF: str = '3m'
    SCALP_V3_STDEV_MODE: str = 'BOUNCE'  # 'BREAKOUT' or 'BOUNCE'
    SCALP_V3_STDEV_BREAK_HI: float = 1.0   # bb_pct_b > this for LONG breakout (>1.0 = above upper band)
    SCALP_V3_STDEV_BREAK_LO: float = 0.0   # bb_pct_b < this for SHORT breakout
    SCALP_V3_STDEV_BOUNCE_LO: float = 0.10 # bb_pct_b < this for LONG bounce (at lower band)
    SCALP_V3_STDEV_BOUNCE_HI: float = 0.90 # bb_pct_b > this for SHORT bounce (at upper band)
    SCALP_V3_EXIT_STDEV_REJECT_ENABLED: bool = False  # 2026-04-29 reverted to False for live default — pairs with ENTRY_STDEV which is shadow-only until validated
    SCALP_V3_STDEV_REJECT_HI: float = 0.95 # LONG exits when bb_pct_b >= this (target hit at upper band)
    SCALP_V3_STDEV_REJECT_LO: float = 0.05 # SHORT exits when bb_pct_b <= this (target hit at lower band)
    # 2026-04-29 USER RULE: per-side V3 params — SHORT trades don't follow same rules as LONG
    SCALP_V3_LONG_K_RISE_MIN: float = 40.0      # LONG fires when k_3m ≥ this (default to legacy MID_LO=40)
    SCALP_V3_LONG_K_RISE_MAX: float = 85.0      # LONG fires when k_3m ≤ this (default to legacy HI=85)
    SCALP_V3_SHORT_K_FALL_MIN: float = 15.0     # SHORT fires when k_3m ≥ this (default to legacy LO=15)
    SCALP_V3_SHORT_K_FALL_MAX: float = 60.0     # SHORT fires when k_3m ≤ this (default to legacy MID_HI=60)
    SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN_LONG: float = 1.0    # LONG bar-break velocity floor
    SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN_SHORT: float = 1.5   # SHORT bar-break needs stronger downward velocity (don't catch falling knives)
    SCALP_V3_STDEV_BREAK_HI_LONG: float = 1.0   # per-side STDEV breakout (long)
    SCALP_V3_STDEV_BREAK_LO_SHORT: float = 0.0  # per-side STDEV breakout (short)
    SCALP_V3_STDEV_BOUNCE_LO_LONG: float = 0.10 # per-side STDEV bounce (long, lower band)
    SCALP_V3_STDEV_BOUNCE_HI_SHORT: float = 0.85# per-side STDEV bounce (short, tighter — catch only deeper rejections at upper band)
    # 2026-04-29 USER A/B: emergency-exit Donchian basis. 'DC4' = 4-bar low/high (current — frequent fires, churny);
    # 'DC' = 20-bar low/high (looser — bigger losses when fires, but far less churn). Test via shadow variant.
    BREAKEVEN_DC_FIELD_MODE: str = 'DC4'
    # 2026-04-29 user: process_account_update timeout (was hardcoded 60s, caused flz exit-42 every 116s).
    # Slower accounts (flz) need 90-120s; stale-data trading risk vs constant-crash data loss.
    PAU_TIMEOUT_SEC: float = 120.0
    # === 2026-04-18/19 LIVE CHANGES — UNTESTED, PENDING SWEEP COVERAGE (see V8_SWEEP_PRIORITY_MATRIX.md) ===
    # Kill switches — flip any to False to disable the corresponding live behavior.
    HEDGE_EXIT_DELTA_CHECK_ENABLED: bool = False  # Legacy delta-decel hedge close. Default OFF per user rule "wt only at exit".
    WRONG_SIDE_ABS_KILL_ENABLED: bool = True  # Kill non-hedge positions when ALL WT+K TFs against side.
    WRONG_SIDE_MIN_AGE_MIN: float = 30.0  # Grace period — won't fire on newborn positions.
    WRONG_SIDE_WT_TFS_REQUIRED: int = 4  # Of 5 WT TFs (3m/15m/1h/4h/D), how many must be against. 2026-04-26: 5→4. 7-day inf forensic: 5/5 fired ZERO times (data/v3_loss_pattern_analysis_20260426_1835.md). Mega-loser tail (-135% on 5 trades: APE/INX/BB) walked free. 4/5 + K already 0 = WT-only gate, still technical, still bypasses STRICT_NO_LOSS by design. Sweep evidence on this knob unreliable (12-sym chained, fails ≥48 floor).
    WRONG_SIDE_K_TFS_REQUIRED: int = 3  # Of 3 stoch K TFs (3m/15m/1h), how many must be against.
    STALL_SUB_ENABLED: bool = False  # Close flat-delta stalled positions to free capital for high-delta entries.
    STALL_AGE_MIN_MIN: float = 180.0  # Position must be at least N minutes old to count as "stalled".
    STALL_GAIN_ABS_MAX: float = 0.5  # |gain| must be below this % to count as stalled.
    STALL_DELTA_SPEED_MAX: float = 1.0  # max(bull_speed, bear_speed) must be below this for "delta dead".
    STALL_MAX_CLOSES_PER_CYCLE: int = 2  # Max stall-closes per account per 30s cycle.
    RATIO_MULTIPLIER: float = 3.0  # 2026-07-08 GAINMO: 4.0→3.0 per CLAUDE.md STATE OF AFFAIRS mandate. The 4.0 justification (BC_160) was 12-sym sub-floor [DIAGNOSTIC] — re-raise only after a >=48-sym Tier-2 proof.
    # === V4 BACKTEST-PROVEN EXIT TUNING (2026-03-27) ===
    # Crypto sweep: vel-6/frac15 = Sharpe 0.457 vs baseline 0.404 (+13%), DD 9.67% vs 10.93%
    WT_EXIT_VEL_THRESHOLD: float = -6.0  # V4: was -2.0 hardcoded. Calmer exits = let winners run longer.
    WT_REDUCE_FRAC_LOW: float = 0.15  # V4: was 0.30. At gains 0.3-0.5%, only reduce 15% (was 30%).
    WT_REDUCE_FRAC_MED: float = 0.25  # V4: was 0.50. At gains 0.5-1.0%, only reduce 25% (was 50%).
    WT_REDUCE_FRAC_HIGH: float = 0.50  # V4: at gains 1-3%, reduce 50% (was 70%).
    MIN_HOLD_BARS_BEFORE_EXIT: int = 32  # V4: 8 hours min hold. Sharpe 0.503 vs 0.460 baseline (+9.3%), PnL +49%.
    # === BOUNCE-TOP EXIT FOR LOSERS (proven on stocks, adapted for crypto) ===
    # Wait for price to bounce toward entry, exit at TOP of bounce = smallest possible loss
    # Mandatory reentry follows: 150% at pullback, 200% at rising WT cross
    # === MOMENTUM INTERCEPTION (MI) — Early exit/entry via slowing deltas, LH/LL structure, divergence ===
    # Detects momentum degradation BEFORE D/W values flip. All sub-signals vote; threshold decides.
    MI_EXIT_ENABLED: bool = False  # Master switch — OFF until sweep-proven
    MI_ENTRY_ENABLED: bool = False  # Entry scoring bonus for favorable MI signals
    MI_STRUCT_EXIT_ENABLED: bool = True  # WT peak LH (long) / trough HL (short) = structural weakening
    MI_EXHAUST_EXIT_ENABLED: bool = True  # EXHAUST_UP (long) / EXHAUST_DOWN (short) on 1h/4h
    MI_DIV_EXIT_ENABLED: bool = True  # BEAR div (long) / BULL div (short) on 1h/4h
    MI_VELOCITY_EXIT_ENABLED: bool = True  # Velocity declining across 2+ TFs (slowing deltas)
    MI_WAVE_EXIT_ENABLED: bool = True  # Wave phase CONTRACTING on 1h
    MI_TF_AGREE_MIN: int = 3  # Minimum sub-signals required to trigger MI exit
    MI_MIN_GAIN_EXIT: float = 0.10  # Minimum gain % before MI exit allowed (crypto: 0.10%)
    MI_ENTRY_STRUCT_BONUS: int = 10  # Score bonus for favorable structure on entry (HL for long, LH for short)
    MI_ENTRY_EXHAUST_BONUS: int = 8  # Score bonus for opposing TF exhaustion on entry
    # === CRYPTO EXIT PATH SWITCHES (2026-04-08) ===
    # Each exit path can be toggled independently for testing/sweep
    EXIT_DELTA_SPEED_ENABLED: bool = True         # Delta engine speed decay exit (PRIMARY)
    EXIT_KEY_LEVEL_CRASH_ENABLED: bool = True     # Multi-TF DC break — catches real breakdowns
    EXIT_TREND_REVERSAL_ENABLED: bool = True      # HTF trend score flip
    EXIT_GAIN_EROSION_ENABLED: bool = True        # Peak gain eroding toward 0 with WT against
    EXIT_STDEV_BREAKOUT_FAIL_ENABLED: bool = True # BB breakout failure (price back inside bands)
    EXIT_DC_BREACH_REDUCE_ENABLED: bool = True    # Augmented position DC breach
    EXIT_HEDGE_LOSS_KILL_ENABLED: bool = True     # Kill losing hedges when gain < prev_gain
    EXIT_HEDGE_ORPHAN_KILL_ENABLED: bool = True   # Kill hedges with no original position
    EXIT_EMERGENCY_DC1H_ENABLED: bool = False     # Emergency DC 1h breach — disabled, too aggressive
    EXIT_DEAD_CODE_ENABLED: bool = False           # Dead code path — disabled
    # === ADDITIONAL EXIT TOGGLES (2026-04-10 sweep) ===
    EXIT_MARKET_SPIKE_REDUCE_ENABLED: bool = True  # Reduce shorts on market spike / longs on drop — sweep: keeps Sharpe
    EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED: bool = False  # 2026-04-11 SWEEP: dead code, no effect on results. Disabled.
    EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED: bool = True  # 2026-04-11: now gated by WT 3m+15m both against
    EXIT_HARD_MAX_LOSS_CAP_ENABLED: bool = False  # 2026-04-11 SWEEP WINNER: #1 PnL destroyer. Sharpe 2.3 disabled vs 0.2 enabled. UNIVERSAL_NOLOSS_GATE handles loss protection.
    EXIT_PREEMPTIVE_BREAKEVEN_ENABLED: bool = True  # Sweep: helps slightly, keep on
    HARD_MAX_LOSS_PCT: float = -999.0  # 2026-04-11 SWEEP: disabled (-999). Was -5.0 — forced closes at -5% destroyed all gains.
    # === UNIVERSAL NO-LOSS GATE (2026-04-10) ===
    # When True: execute_now blocks ALL reduce/close where real_gain < 0%
    # Exceptions: hedges (is_hedge=True), STRUCTURAL_RANGE_SHIFT_EXIT, LIQUIDATION
    # This replaces Finandy's external NO_LOSS so it can be turned off safely.
    UNIVERSAL_NOLOSS_GATE: bool = True  # 2026-07-06 RE-ARMED: was False → all 3 protections (this, STRICT_NO_LOSS_ACCOUNTS=[], HEDGE_MODE=False) were OFF, so ~40 exit reasons freely closed losers → 10,622 loss-closes drained live accounts. Re-arming restores the no-loss gate at ez_manage.py:25051. ROLLBACK: False (removes ALL loss protection — do not).
    # 2026-04-16: bypass for technical-exit reasons so WT/DC/structure reversals can close losers.
    # Without this, UNIVERSAL_NOLOSS_GATE turns all technical exits into no-ops on losing positions,
    # which is the exact pattern that kept ATOMUSDT SHORT bleeding from 0 to -2.78%.
    UNIVERSAL_NOLOSS_GATE_BYPASS_TECHNICAL: bool = True
    # Substrings matched (uppercased) against the exit reason. If ANY matches, UNIVERSAL_NOLOSS_GATE
    # allows the close at a loss. These are technical (reversal) exits only — no %-based stops.
    # Per CLAUDE.md: "NO % Stops — Technical Exits ONLY. Only WT turn, volume die, DC reversal, stoch cross."
    UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS: list = field(default_factory=lambda: [
        # 2026-04-24 CLEARED again: account draining via GAIN_EROSION bypass. MOVR closed at -13%, RLC at -0.22%,
        # KSM at -0.59% — all via this bypass. User directive: NEVER close at loss, hedge instead.
        # If the hedge mechanism can't keep up, fix the hedge side (faster close rule, better reopen), not the NOLOSS gate.
        # 2026-04-17 CLEARED per user: "WT_CROSS_EXIT, DC_BREAK etc are NOT bypass reasons at all ever."
        # 2026-04-20 re-added GAIN_EROSION → REMOVED again 2026-04-24 (incident: account drain).
        # 2026-04-25 SCALP_V3: V3 scalper cuts losses via technicals + max-loss stop. Short-duration trades; holding losers is NOT the strategy.
        # SCALP_V3_CLOSE = exits from scalp_v3_live.py (stall/bar/K signal exits)
        # SCALP_V3_OPEN_PROTECTIVE = exits from _scalp_v3_protective_exits + max-loss stop
        'SCALP_V3_CLOSE',
        'SCALP_V3_OPEN_PROTECTIVE',
        # 2026-05-05 USER MANDATE (LUNC -65% incident): emergency safety closes BYPASS NOLOSS.
        # These are LAST-RESORT exits when position is hopeless (held >48h, or beyond -15%, or wt-against bleed).
        # NOLOSS_GATE blocking these IS what caused -65% LUNC to never close.
        'RIDICULOUS_HOLD',                # RIDICULOUS_HOLD_age{X}h_cap48h_g{X}% — held too long
        'RIDICULOUS_LOSS',                # RIDICULOUS_LOSS_g{X}%_cap-15.0% — beyond catastrophic
        'UNDERWATER_HEDGE_OR_CLOSE',      # wt1_3m flipped against + already hedged + still bleeding
        'DC_BB_D_BREAK_REVERSE',          # 2026-05-06 user mandate (LUNC -18%): D-band break/cross-back wrong-side close
        'WT15M_AGAINST',                  # 2026-05-06 user mandate: wt1_15m against → hedge or close NO MATTER WHAT
        'ALL_TF_AGAINST',                 # 2026-05-06 user mandate: all TFs against → close primary, hedge becomes main
        'HTF_AGAINST_FORCE_CLOSE',        # 2026-05-30 user ABSOLUTE: wt1_1h against → close NOW, nothing survives a 1h flip
        # 2026-05-09 USER MANDATE: only R1, R2, hedge-failed can close at loss.
        'R1_DC_LOW4_3M_EMERGENCY',        # newborn-window dc4_3m breach → close
        'NEWBORN_LOSS_KILL',              # 2026-05-21 USER: newborn position with gain<threshold → close (ORDI-protection)
        'FROZEN_ACT_STOP_FROZEN_BREACH',  # 2026-05-18 USER: price < frozen_dc_low_4h@entry (LONG) / > frozen_dc_high_4h@entry (SHORT) AND gain<0 → close
        'FROZEN_ACT_STOP_ABSOLUTE_FLOOR', # 2026-05-18 USER: gain ≤ FROZEN_ABSOLUTE_FLOOR_PCT_(CRYPTO|TRADIER) → close. Replaces RIDICULOUS_LOSS late-fire (which caught PHBUSDT at -27% not -15%). Active per-bar at entry-frozen activation TF level.
        'R2_WT_VEL_SLOW',                 # wt vel slowdown near 0 gain → close at small positive
        'WT_15M_VEL_SLOW',                # legacy alias for R2 (existing block at ez_manage:20696)
        'R3_HTF_FLIP',                    # 2026-05-17 USER: Daily-close + parallel 4h structural flip → close. data/research_20260516/PLAN.md §3.6
        'R3_HTF_FLIP_4H',                 # 2026-05-17 USER: 4h-tier of R3 (parallel to Daily so positions don't sit adverse up to 24h)
        'R4_STDEV_MACRO_TOP',             # 2026-05-17 USER: long-window log-price z >+2.5σ on D AND W → close LONG (additive to BB; macro tops/bottoms). Default OFF.
        'R4_STDEV_MACRO_BOT',             # 2026-05-17 USER: long-window log-price z <-2.5σ on D AND W → close SHORT. Default OFF.
        'HEDGE_FAILED',                   # hedge couldn't be taken → fallback close at loss
        # 2026-05-10 USER NON-NEGOTIABLE MANDATE: every tradeable_key with wt1_3m vs wt2_3m
        # condition met must always have a position. Reopen after every close. See
        # WT_3M_FORCE_OPEN_ENABLED below for full description.
        'WT_3M_FORCE_OPEN',
        # 🚩 2026-05-12 USER MANDATE: GR_HTF_DIRECT_EXIT closes at any gain (loss exit sanctioned).
        # Per CLAUDE.md exit-rules: "exit signal when >=18 (against the trade) → close even at a loss."
        # ROLLBACK: remove this line + set GR_HTF_DIRECT_EXIT_ENABLED=False in config.
        'GR_HTF_DIRECT_EXIT',
        # 2026-05-15 USER: daemon reentry premise failed (price crossed back past exit level) → close.
        'DAEMON_REENTRY_STALE_EXIT',
        # 2026-05-16 BACKTEST SWEEP ONLY: DC4 stop research variable in backtest_v8_engine.py.
        # This reason is NEVER emitted by live trading code — safe to bypass NOLOSS gate.
        'DC_STOP_BREACH',
        # 2026-05-19 Path A Phase 1: MTF compound exit (ATR trail + GR HTF slowdown + WT cross + DC/BB reject).
        # ALL bypass NOLOSS — these are sanctioned loss-exit replacements behind master MTF_EXIT_USE_COMPOUND switch.
        # Default OFF; baseline_v5 cert flips master True after sweep proves replacement quality.
        'MTF_ATR_TRAIL',
        'MTF_DC_REJECT',
        'MTF_BB_REJECT',
        'MTF_GR_WT_EXIT',
        # 2026-05-21 USER: ZEC supervisor agent (Sonnet) autonomous close, capped 1/hr.
        # See ZEC_SUPERVISOR_* knobs below and zec_supervisor_agent.py.
        'AGENT_AUTONOMOUS_CLOSE',
        'ZEC_SUPERVISOR_CLOSE',
    ])
    # 2026-05-15 USER: SHORT price-cross daemon reentries require wt1_3m crossunder + k_3m>60.
    # 2026-05-16 RE-FLIPPED to False — earlier edit reverted by an external process.
    # Per agent audit: True gate silently dropped every SHORT reentry below k_3m=60
    # (inverse of "lower K is better" mandate). Set False to unblock SHORT reentry path.
    DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED: bool = False
    # 2026-05-15 USER: when a DAEMON_PRICE_CROSS_REENTRY position's premise fails (price
    # crosses back above/below the encoded exit_price), close immediately bypassing NOLOSS.
    DAEMON_REENTRY_STALE_EXIT_ENABLED: bool = True
    # 2026-05-10 USER NON-NEGOTIABLE: every tradeable_key with wt1_3m > wt2_3m (LONG) /
    # wt1_3m < wt2_3m (SHORT) must always have a position open. If flat, OPEN immediately;
    # reopen after every close. Reentry/hedge gates may NOT block this. The reason
    # 'WT_3M_FORCE_OPEN' bypasses HARD_AUGMENT_LOCK / DUP_GUARD / NOLOSS in execute_now.
    # 2026-05-17 USER MANDATE REVERSED → False. Source: data/research_20260516/PLAN.md.
    # Research (Dobrynskaya 2021 SSRN 3913263, Wen 2022 SSRN 4080253): 3m crypto = reversal-
    # dominated; Rule A scoring of 591 live trades 30d showed 84.5% NO_SETUP. Default = flat;
    # entries only via existing paths (Rule A/B/C scaffolding follows). Sweep variants queued
    # on S1: WT3MFO_OFF_CRYPTO, WT3MFO_ON_CRYPTO_CONTROL.
    # ROLLBACK: set ENABLED + BYPASS_GATES = True (live default pre-2026-05-17).
    # REVERTED 2026-05-18 18:30: restored 2026-05-10 NON-NEGOTIABLE mandate value (True).
    # 2026-05-17 flip to False had no sample-floor evidence; isolated vec sweep queued.
    WT_3M_FORCE_OPEN_ENABLED: bool = True  # USER 2026-06-01: Enabled with >1% SMA200_15m distance, WaveTrend velocity, and wick filters.
    WT_3M_FORCE_OPEN_BYPASS_GATES: bool = True  # USER 2026-06-01: Enable bypass gates to ensure always-open operative status.
    WT_3M_FORCE_OPEN_SIZE_USD: float = 25.0     # USER 2026-05-11: raised 9→25. $9 too small to ride breakouts when wt_3m fires (1000BONK / TON / ZEC missed-rally pattern).
    # GR vote gate for FORCE_OPEN / TRADEABLE_KEYS_MANDATORY signals.
    # Total votes = sum of bullish indicators across 5 TFs (3m/15m/1h/4h/D), max 35.
    # MIN_TFS x MIN_IND_PER_TF: when MIN_TFS > 0, also require that many TFs to each
    # have >= MIN_IND_PER_TF indicators agree (3x5 = 3 TFs with 5+ indicators each).
    # Set MIN_TFS=0 to use total-vote-only gate (no per-TF floor).
    WT_3M_FORCE_OPEN_GR_GATE_ENABLED: bool = False  # 2026-06-03 USER MANDATE — PERMANENTLY OFF. The GR-vote gate (20 votes/4 TFs) blocked the sma±1%+wt1_3m force-opener 100% (0 fires). "NO FILTERS CAN STOP THIS." Adapt values, never re-enable.
    WT_3M_FORCE_OPEN_SMA_PCT: float = 1.0  # 2026-06-03 USER: distance past sma_200_15m that mandates an open when 3m WT cross agrees (LONG px>sma*(1+pct/100) / SHORT px<sma*(1-pct/100)). Adapt value, never disable.
    # 2026-05-17 TIGHTENED per vec_top_combo_validator winners (56 syms × 1.25yr):
    # Top-25 winners (ps>0.5, trades>1k) are 25-of-25 LONG-side, all use 4-condition combos
    # with HTF (4h/D) WT confirm + K-extreme-low entry. Was VOTE_MIN=15/TFS=3/IND=5.
    # Now VOTE_MIN=20/TFS=4/IND=5 → require 4-TF agreement (matches top combo pattern).
    # Rollback: flip back to 15/3/5 + restart ez_manage_<acct> procs.
    WT_3M_FORCE_OPEN_GR_VOTE_MIN: int = 20      # was 15. Aligned with top combo Sharpe 1.18
    WT_3M_FORCE_OPEN_GR_MIN_TFS: int = 4       # was 3. Forces 4-TF confirmation
    WT_3M_FORCE_OPEN_GR_MIN_IND_PER_TF: int = 5 # per-TF indicator floor when MIN_TFS > 0
    # ═══════════════════════════════════════════════════════════════════
    # RULES A/B/C + R3_HTF_FLIP EXIT + HTF VETO (2026-05-17 USER MANDATE)
    # Source: data/research_20260516/PLAN.md §§3.2-3.8 + research_summary.md.
    # Replaces WT_3M_FORCE_OPEN reopen-on-every-cross mandate. Each rule is
    # behind its own kill-switch. ROLLBACK = set the *_ENABLED flag False.
    # Sweep variants queued on S1 sweep_coordinator/queue.json 2026-05-17.
    # ═══════════════════════════════════════════════════════════════════
    # REVERTED 2026-05-18 18:30: all 4 flips below had no sample-floor evidence (DEAD KNOB / BLOCKED_NON_VEC sweeps only). Isolated vec sweeps queued on S1.
    HTF_TREND_VETO_ENABLED: bool = True                  # 2026-05-26 USER MANDATE — re-enabled after BTCDOM autopsy + switch_hunt arm htf_veto_on showed ΔPS +0.020 / -57% trades vs baseline. Mirrors tradier (line 2238 in config_tradier.py)
    # 2026-05-22 USER MANDATE: bottom entries hold until HTF flips. Block reduce/close when Daily WT supports position.
    HTF_TREND_VETO_ON_REDUCE_ENABLED: bool = True
    R3_HTF_FLIP_EXIT_ENABLED: bool = False               # was True 2026-05-17; reverted — no sample-floor proof
    R3_HTF_FLIP_4H_TIER_ENABLED: bool = False            # was True 2026-05-17; reverted — no sample-floor proof
    BREAKOUT_RETEST_ARMED_ENABLED: bool = False          # was True 2026-05-17; reverted — no sample-floor proof. Rule A retest dead until isolated vec sweep validates.
    BREAKOUT_RETEST_ARMED_WINDOW_DAYS: int = 7           # retest must fire within N days of arm
    BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT: float = 0.30  # |close_3m - armed_level| / atr_D < this
    BREAKOUT_RETEST_ARMED_VOLUME_MULT: float = 1.25      # volume_D > MULT * sma(volume_D, 20) required to arm
    BREAKOUT_RETEST_ARMED_K_3M_PREV_MAX: int = 30        # 2026-05-18 sweep knob: LONG fires when stoch_k_3m_prev < this (SHORT mirrors at 100-this). Default mirrors live <30.
    BREAKOUT_RETEST_ARMED_HTF_STACK_MIN: int = 2         # 2026-05-18 sweep knob: min count of {15m, 1h} HTFs aligned with side. 2 = both (live "AND"), 1 = OR.
    RULE_B_W_TREND_4H_PULLBACK_ENABLED: bool = False     # Rule B (weekly trend + 4h pullback). Default OFF until helpers exist.
    RULE_C_FUNDING_EXTREME_ENABLED: bool = False         # Rule C (funding-extreme mean-reversion). Default OFF until funding gate exists.
    FUNDING_EXTREME_LONG_THRESHOLD_PCT: float = -0.03    # crowded shorts → contrarian LONG (literature default, user-confirmed 2026-05-17)
    FUNDING_EXTREME_SHORT_THRESHOLD_PCT: float = 0.05    # crowded longs → contrarian SHORT (BitMEX-historic threshold)
    RULE_NAME_TAGGING_ENABLED: bool = True               # write rule_name=RULE_A|B|C|LEGACY into history JSONL at every OPEN. Pure observability.
    HEDGE_HTF_VETO_ENABLED: bool = True                  # 2026-05-17: block OBLIGATORY_HEDGE if Daily WT hasn't flipped to support hedge direction. For LONG position the hedge is SHORT (requires wt1_D < wt2_D), for SHORT position the hedge is LONG (requires wt1_D > wt2_D). Source: data/research_20260516/PLAN.md §3.7. Live data showed hedge entries dominating opens (58-82% per acct) and QUICK_HEDGE_PROTECT_LONG_LOSS averaging -0.49%. ROLLBACK: set False.
    BREAKOUT_RETEST_ARMED_PERSISTENT_ENABLED: bool = False  # 2026-05-17: future feature — replace stateless dc_basis_D anchor with persistent breakout_retest_armed[symbol][side] state dict (arm on dc_high_D[prev_D] cross + volume confirm, fire on retest within 7d). Wired in ez_manage MultiAccountTradeManager state dicts. Default OFF — needs code in next session, sweep variant queued for forward validation.
    # ═══════════════════════════════════════════════════════════════════
    # 2026-05-20 USER MANDATE — MTF protocol filter (Phase I REJ_1h winner)
    # Phase J sample-floor: 293 stocks × 2.13y → pool_S +0.28, avg DD 6.5%, +113%/sym/yr.
    # FILTER mode: existing entries must additionally pass MTF gates.
    # See data/hourly_reconfig/_baselines/baseline_v6_*_mtf_phase_i_20260520.json.
    # mtf_live_evaluator.py is the runtime; vec_paths/mtf_armed_entries.py is the backtest mirror.
    # ROLLBACK: MTF_ARMED_ENTRY_ENABLED=False.
    # ═══════════════════════════════════════════════════════════════════
    MTF_ARMED_ENTRY_ENABLED: bool = True             # 2026-05-22 02:37 Re-enabled post persistent hydration fix
    MTF_ARMED_ENTRY_SKIP_SHORT: bool = True          # 2026-06-04 USER ("MTF it's live ... badly written, throttles shorts, fix RIGHT NOW"): the execute_now MTF armed-gate was applied to SHORT opens identically to LONG, but A/B (project_persym_mtf_interaction_20260531) shows MTF HELPS longs (+0.02) and HURTS shorts (crypto -0.10). Skip the MTF armed-gate for SHORT entries only (longs keep it — see force-opener mandate below); still ENFORCED during the cold-start window so the flood guard holds. Site: ez_manage.py execute_now ~23147. ROLLBACK: False = gate both sides.
    # 2026-06-03 USER MANDATE: the force-opener (watchdog REQ1 sma±pct+wt-cross + REQ3 multi-TF DC)
    # MUST require MTF armed-state + GR confirmation (via mtf_entry_filter_passes). A/B proved
    # MTF-gating = pool_sharpe 0.53 vs 0.11 ungated. The watchdog applies this at source so live ==
    # the MTF-gated backtest. NEVER bypass quality on the force-open again. ROLLBACK: False (NOT advised).
    FORCE_OPEN_REQUIRE_MTF_GR: bool = True
    REENTRY_CONFIRMATION_GATES_ENABLED: bool = True
    REENTRY_STOCH_K_MAX_LONG: float = 40.0
    REENTRY_STOCH_K_MIN_SHORT: float = 60.0
    REENTRY_WAVETREND_CONFIRM_ENABLED: bool = True
    # 2026-05-21 USER: bypass MTF_FILTER for STRONG_BUY and QUICK_OPEN reasons. Same pattern as
    # DELTA_GATE_STRONG_BUY_QUICK_BYPASS — MTF state wipes every restart and takes hours to re-arm,
    # so high-conviction QUICK_OPEN scoring entries get blocked for hours post-restart. Parameter-level
    # bypass per "no switch-off" mandate (line 1296). ROLLBACK: set to False.
    # 2026-05-21 22:47 — REVERTED to False after ORDIUSDC top-of-range incident. MTF_FILTER back ON.
    # 2026-05-22 21:30 — RE-ENABLED: TOP_OF_RANGE_BLOCK now catches ORDI-type dc_pos≥0.95 entries.
    # Without this bypass, zero entries for 15.5h post-restart (MTF takes hours to re-arm).
    MTF_FILTER_STRONG_BUY_QUICK_BYPASS: bool = True
    # ═══ 2026-06-04 COLD-START FLOOD GUARD + OPEN-RATE CIRCUIT BREAKER (ez_manage execute_now) ═══
    # After a simultaneous cold restart of all 5 crypto procs, the QUICK_OPEN/breakout/reentry
    # MTF-bypasses opened ~96 junk shorts in seconds against an empty (un-armed) MTF state.
    # (A) For COLD_START_OPEN_BYPASS_SUPPRESS_SEC after proc start, those opener bypasses are
    #     suppressed so fresh opens must pass MTF normally (blocked until armed). 0 = disabled.
    # (B) OPEN_RATE breaker caps fresh OPEN/ENTRY/REENTRY to OPEN_RATE_MAX per OPEN_RATE_WINDOW_SEC
    #     per process — a hard backstop against ANY open flood. Exits/reduces never affected.
    COLD_START_OPEN_BYPASS_SUPPRESS_SEC: float = 30.0
    OPEN_RATE_BREAKER_ENABLED: bool = True
    OPEN_RATE_MAX: int = 30
    OPEN_RATE_WINDOW_SEC: float = 30.0
    MTF_ARMED_HTF_LIST: str = '1h,4h,D,W'
    MTF_ARMED_BANDTYPES: str = 'dc,bb,wt'
    # 2026-05-21 19:35 — REVERTED 19:25 False back to True per user mandate "no switch-off, change parameters instead".
    # State-loss workaround moves to parameter sweep on MTF_ARMED_HTF_LIST: '1h,4h,D,W' → '1h,4h' so ARM fires
    # on shorter HTFs and recovers faster post-restart. Sweep validates positive-delta-Sharpe before live flip.
    MTF_REQUIRE_ARMED_ANY: bool = True
    MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED: bool = True
    # 2026-05-22 USER MANDATE: bypass the "wt1 still rising" suspend during expansion regime
    # (HTF dc_pos at extreme = breakout). NEAR +83% rally: WT flattened at extreme while price
    # climbed — suspend blocked all entries. Default True; flip to False to revert.
    MTF_ARMED_WT_DIRECTION_SUSPEND_BREAKOUT_BYPASS: bool = True
    MTF_ENTRY_REQUIRE_GR_FILTER: bool = True
    MTF_GR_FILTER_ENABLED: bool = True
    MTF_GR_MIN_TFS: int = 3                          # Phase I winner
    MTF_GR_MIN_IND: int = 7                          # 2026-06-03 USER "FIX GR": A/B winner (12 syms) breakout-mode min7 GR universal gate, pool 0.4146→0.4355 / per_sym 0.4529→0.4822. Live mtf_entry_filter_passes/gr_filter_pass read this key → live GR == vec GR.
    MTF_GR_INVERT_DC_BB: bool = True                 # 2026-06-03 BREAKOUT mode (GR fires ON breakouts — golden_rule_htf intended use). Room mode was inert + penalized breakouts.
    GR_FILTER_ALL_ENTRIES: bool = True               # 2026-06-03 USER "GR is the prime entrypoint": gate EVERY entry by GR. ACTIVE in vec (v8_vec_sweep). LIVE: force-open already GR-gated via mtf_entry_filter_passes; universal live gate (DELTA/GR-entry) is a pending follow-up.
    # ═══════════════════════════════════════════════════════════════════
    # GR v5 — Breakout-confirm (4h/D/W) → Bounce-entry (3m/15m/1h) state machine
    # User mandate 2026-05-18: replace simple-mult GR composite with strict two-phase
    # state machine. Design doc: data/research_20260518/gr_v5_breakout_bounce_design.md.
    # NPZ degradation: spec was 1m/3m/5m/15m/1h for bounce but NPZ has no 1m + no
    # 5m-for-crypto, so crypto LTFs degrade to {3m, 15m, 1h}; tradier LTFs use
    # {5m, 15m, 1h}. All knobs default OFF until sample-floor sweep proves edge.
    # NOT WIRED in ez_manage / tradier_manage / backtest_v8_engine. vec_paths/
    # gr_v5_state.py skeleton present; full state-machine impl pending followup.
    # ═══════════════════════════════════════════════════════════════════
    GR_V5_ENABLED: bool = False
    GR_V5_HTF_TFS: tuple = ('4h', 'D', 'W')              # breakout-confirm TFs
    GR_V5_HTF_MIN_ALIGN: int = 2                          # 2-of-3 alignment to arm
    GR_V5_BREAKOUT_REQUIRE_VOLUME: bool = True
    GR_V5_BREAKOUT_VOL_MULT: float = 1.25
    GR_V5_LTF_TFS: tuple = ('3m', '15m', '1h')           # crypto bounce TFs (NPZ has no 1m, no 5m-for-crypto)
    GR_V5_LTF_MIN_ALIGN: int = 2                          # 2-of-3 (was "3 of 5" pre NPZ-degradation)
    GR_V5_BOUNCE_STOCH_LONG: float = 25.0                 # k oversold for LONG bounce fire
    GR_V5_BOUNCE_STOCH_SHORT: float = 75.0                # k overbought for SHORT bounce fire
    GR_V5_BOUNCE_WT_CROSS_REQUIRED: bool = True
    GR_V5_ARM_WINDOW_BARS: int = 168                      # 7d at 1h cadence (or 56 at 3h)
    GR_V5_RETEST_BAND_PCT: float = 0.03                   # |price - armed_price|/armed_price < 3%
    GR_V5_INVALIDATE_PCT: float = 0.02                    # close below armed_price*(1 - 0.02) → disarm
    # ═══════════════════════════════════════════════════════════════════
    # STDEV_MACRO — long-window log-price z-score on D/W/M (2026-05-17)
    # User mandate: BB is for short-window breakouts (untouched). STDEV is for
    # REAL macro tops/bottoms on D/W/M. Additive ONLY — never replaces, never
    # silently overrides BB-breakout logic. All gates default OFF. See
    # stdev_macro.py for fail-open semantics and vec_paths/stdev_macro_vec.py
    # for the rolling-z math. Sweep arms queued on S1 sweep_coordinator.
    # State definitions (from stdev_macro.derive_state):
    #   STRONG_TOP: z_D > 2.5 AND z_W > 1.5
    #   TOP:        z_D > 1.5 OR  z_W > 1.5 (and not also BOT)
    #   STRONG_BOT: z_D < -2.5 AND z_W < -1.5
    #   BOT:        z_D < -1.5 OR  z_W < -1.5 (and not also TOP)
    #   MID:        else, OR conflicting (one TF top + other TF bot)
    # ═══════════════════════════════════════════════════════════════════
    STDEV_MACRO_ENTRY_VETO_ENABLED: bool = False         # block OPEN+AUGMENT when STRONG_TOP (LONG) / STRONG_BOT (SHORT). Fail-open on missing data.
    STDEV_MACRO_AUGMENT_VETO_ENABLED: bool = False       # block AUGMENT only (not OPEN-on-empty) at TOP/BOT. BB breakouts from flat never blocked.
    STDEV_MACRO_ENTRY_BOOST_ENABLED: bool = False        # size multiplier when entering AGAINST macro extreme (mean-revert). Never changes side.
    STDEV_MACRO_ENTRY_BOOST_MULT: float = 1.3            # size ×1.3 LONG at STRONG_BOT, ×1.3 SHORT at STRONG_TOP
    STDEV_MACRO_R4_EXIT_ENABLED: bool = False            # fire CLOSE on STRONG_TOP (LONG) / STRONG_BOT (SHORT) + LTF flip (wt_4h). Runs AFTER R1/R2/R3 — never preempts. Bypass reasons R4_STDEV_MACRO_TOP/BOT already added to UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS.
    STDEV_MACRO_R4_REQUIRE_LTF_FLIP: bool = True         # require wt1_4h vs wt2_4h flip alongside macro extreme; False = fire on macro state alone (more trigger-happy)
    STDEV_MACRO_HEDGE_BOOST_ENABLED: bool = False        # extra OBLIGATORY_HEDGE trigger when origin held against macro extreme. Additive to existing 3m/15m/1h triggers — never removes them.
    # ═══════════════════════════════════════════════════════════════════
    # MTF COMPOUND EXIT — Path A Phase 1 wiring 2026-05-19 (USER MANDATE)
    # Source: data/_diagnostic/protection_stack_2026051*.md + vec_paths/mtf_armed_entries.py:166-235.
    # Replacement protection stack for HEDGE_MODE + DC_LOW_4 emergency. 5 triggers, ANY fires close:
    #   1. ATR trail hit (HARD, ratchets from entry)
    #   2. DC reject (price was outside dc_high/low_TF and re-crossed back)
    #   3. BB reject (recent BB tag-fail mask)
    #   4. GR HTF exit gate (opposite-side GR score passes)  ─┬─ BOTH required
    #   5. WT cross 15m/1h against side                       ─┘  for soft exit
    # Master switch MTF_EXIT_USE_COMPOUND defaults False — wiring is no-op until baseline_v5 cert flips it.
    # Crypto base TF = 3m → MTF_ATR_TRAIL_TF=15m default (5x base).
    # New CLOSE reasons (MTF_ATR_TRAIL_*, MTF_DC_REJECT_*, MTF_BB_REJECT_*, MTF_GR_WT_EXIT_*)
    # added to UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS above.
    # ROLLBACK: set MTF_EXIT_USE_COMPOUND=False (already default) — entire branch becomes inert.
    # ═══════════════════════════════════════════════════════════════════
    # 2026-05-20 FLIPPED ON per USER mandate (hedge OFF requires stops; Phase I REJ_1h winner).
    # Phase J sample-floor: 293 stocks × 2.13y → pool_S +0.28, avg DD 6.5%, +113%/sym/yr.
    # ROLLBACK: set MTF_EXIT_USE_COMPOUND=False (one-line kill switch).
    MTF_EXIT_USE_COMPOUND: bool = True                   # master switch — compound exit replaces hedge protection
    MTF_ATR_TRAIL_ENABLED: bool = True                   # 2026-05-20 ON (Phase I)
    MTF_ATR_TRAIL_MULT: float = 2.0                      # 2026-05-20 USER MANDATE: 2x ATR 15m trail from current price. Was 3.0 (loose), now tightened to spec. Phase I tested {1.5,2,3,4} all tied on Sharpe/DD.
    # 2026-05-20 USER MANDATE: MTF compound exit ONLY applies to positions opened
    # AFTER this timestamp.
    # 2026-07-14 FIX: was 0.0 -> code fell back to trade_manager startup time
    # (time.time() at process start), which re-arms on EVERY restart and
    # permanently orphans any position that predates the CURRENT process
    # instance from MTF_ATR_TRAIL/DC_REJECT/BB_REJECT/WT_EXIT for the rest of
    # its life. Verified live: after an 18:15-18:20 UTC restart, EVERY open
    # crypto position (all 5 accounts) and every open trb stock position lost
    # this protection, including ones opened hours earlier the same day.
    # Fixed epoch = 2026-05-20T00:00:00Z (when this feature actually shipped)
    # restores the ORIGINAL intent -- protect everything opened after the
    # feature launched -- without being reset by restarts. Do not revert to
    # 0.0 / time.time().
    MTF_EXIT_MIN_OPEN_TS: float = 1779235200.0
    MTF_ATR_TRAIL_TF: str = '15m'                        # Phase I winner
    MTF_DC_REJECT_EXIT_ENABLED: bool = True              # 2026-05-20 ON (Phase I)
    MTF_DC_REJECT_EXIT_LOOKBACK: int = 5
    MTF_DC_REJECT_EXIT_TF: str = '1h'                    # Phase I winner (REJ_1h)
    MTF_BB_REJECT_EXIT_ENABLED: bool = True              # 2026-05-20 ON (Phase I)
    MTF_BB_REJECT_EXIT_LOOKBACK: int = 5
    MTF_BB_REJECT_EXIT_TF: str = '1h'                    # Phase I winner (REJ_1h)
    MTF_GR_EXIT_GATE_ENABLED: bool = True                # 2026-05-20 ON (Phase I)
    MTF_GR_EXIT_MIN_TFS: int = 3
    MTF_GR_EXIT_MIN_IND: int = 5
    MTF_WT_CROSS_EXIT_ENABLED: bool = True               # 2026-05-20 ON (Phase I)
    MTF_WT_CROSS_EXIT_TF: str = '15m'                    # '15m' | '1h' | 'either'
    # === DC RECOVERY-TO-ENTRY EXIT BYPASS (2026-04-15, crypto) ===
    # When True: if entry_price is on wrong side of dc_high_4h (LONG above) / dc_low_4h (SHORT below),
    # AND current 3m close has recovered to within tolerance of entry_price,
    # AND current 3m bar shows reversal, ALLOW close at loss (bypass UNIVERSAL_NOLOSS_GATE).
    # Replaces UNIVERSAL_NOLOSS for "bad-entry escape near breakeven". Defaults OFF — sweep first.
    DC_RECOVERY_EXIT_ENABLED: bool = True  # STRUCTURAL — was False, now True per tradier parity (user 2026-08-14: DC 5m/15m/1h/4h/D reject)
    DC_RECOVERY_EXIT_TOLERANCE_PCT: float = 0.10  # structural 0.05-0.20 sweep, was 0.25
    DC_RECOVERY_EXIT_TOLERANCE_ATR_MULT: float = 0.0  # if >0, uses 0.0..N * atr_3m instead of pct
    # === LOSS-EXIT SWITCH GATES (2026-04-15) — default OFF, sweep-only ===
    # Each wraps an existing close/reduce-at-loss path so it stays disabled in live unless sweep enables.
    LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED: bool = False  # ez_manage.py:20621 STOP_FUNCTIONS_KILL @ gain<-5%
    LOSS_EXIT_HEDGE_MODE_BLOCK_ESCAPE_ENABLED: bool = False  # ez_manage.py:20647 hedge-failed escape @ gain<-15% & 30min unhedged
    LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED: bool = False  # ez_positions_quick.py:10778 allow exit when max_gain≥0.5% & fresh_gain>-0.5
    # === REENTRY BLOCKS (2026-04-16) — ABLATION RESULTS, 11sym crypto + 12sym tradier ===
    # 7 KEEP (default True), 2 CUT (default False, switch kept for sweep re-test)
    REENTRY_B01_WT_2of3_ENABLED: bool = False  # ABLATION: Sharpe 0.033/0.036 = noise. 256K/177K trades. CUT.
    REENTRY_B02_BC156_BOTTOM_ENABLED: bool = True  # ABLATION: Sharpe 0.31/0.32, 22K/14K trades, 62.5% WR. Best balance.
    REENTRY_B04_DC_RETEST_ENABLED: bool = True  # ABLATION: Sharpe 0.39/0.31, 579/335 trades. High quality.
    REENTRY_B09_SNAPBACK_ENABLED: bool = False  # ABLATION: Sharpe 0.022/0.024 = weak. CUT.
    REENTRY_B10_STOCH_REV_ENABLED: bool = True  # ABLATION: Sharpe 0.07/0.12, 69-75% WR. Keep for WR.
    REENTRY_B11_DC_BREAK_ENABLED: bool = True  # ABLATION: Sharpe 0.34/0.31, 94-97% WR. Top quality.
    REENTRY_B12_WT_MOM_ENABLED: bool = True  # ABLATION: Sharpe 0.15/0.17, 112K/73K trades. Volume king.
    REENTRY_B14_HA_TREND_ENABLED: bool = True  # ABLATION: Sharpe 0.11/0.13. Moderate.
    REENTRY_B15_STRONG_TREND_ENABLED: bool = True  # ABLATION: Sharpe 0.89/0.72, 94-97% WR. Sniper.
    REENTRY_B16_MIDRANGE_ENABLED: bool = True  # DC midrange reclaim + 15m WT cross — fires when trend resumes after reduction
    REENTRY_EXIT_RECLAIM_ENABLED: bool = True  # B00: price recovered above last_reduction_price + 15m WT momentum (live only)
    REENTRY_EXIT_RECLAIM_BUFFER_PCT: float = 0.2  # B00: price must be this % above exit price (SMA200 + k3m momentum also required)
    # === AUGMENT BLOCKS (2026-04-16) — 4 blocks switch-gated for sweep ===
    # 2026-04-25 rapid-grid finding: AUGMENT_WT_4H_BOUNCE (v8_quick_engine) → +0.029 pool_sharpe on crypto 50-sym. No live equivalent yet — wire as AUGMENT_WT_4H_BOUNCE_ENABLED when sweep validates on full 50-sym.
    AUGMENT_BLOWPAST_ENABLED: bool = True  # gain >= 3×MIN_GAIN, conviction 90. Highest conviction.
    AUGMENT_WT_CROSS_ENABLED: bool = True  # WT cross + aligned 2/3 TFs + gain >= MIN_GAIN, conviction 80.
    AUGMENT_WT_3TF_ENABLED: bool = True  # 3/3 LTF aligned + smaller gain, conviction 70.
    AUGMENT_HTF_TREND_ENABLED: bool = True  # HTF trend only, conviction 65. Most frequent.
    # === EVALUATE_REENTRY_2 (2026-04-16) — periodic reentry pass switches ===
    REENTRY_2_ENABLED: bool = True  # Master switch. ~$420 PnL per ablation.
    REENTRY2_DIR_FAV_ENABLED: bool = True  # BC_152 direction-favorable immediate reentry
    REENTRY2_DC_BREAK_ENABLED: bool = True  # DC breakout fast-path reentry — USER 2026-05-29 LOCKED ON: the live gate IGNORES this flag; DC-break reentry can NEVER be switched off (config/per_sym). dc_1h base is the permanent trigger. Only the sub-knobs below are testable.
    REENTRY2_DC_BREAK_REQUIRE_K_FILTER: bool = True   # require stoch k>d (LONG)/<(SHORT) on FILTER_TF. Sweep OFF to drop the K gate.
    REENTRY2_DC_BREAK_REQUIRE_WT_FILTER: bool = False  # require wt1>wt2 (LONG)/<(SHORT) on FILTER_TF. Sweep ON to add a WT-momentum gate.
    REENTRY2_DC_BREAK_ALLOW_15M: bool = True           # also fire on a dc_15m break. dc_1h ALWAYS fires (never off). Sweep OFF for 1h-only.
    REENTRY2_DC_BREAK_FILTER_TF: str = "3m"            # K/WT filters read stoch_k_<TF>/wt1_<TF>. Live stays 3m until SWEEP proves 15m/1h better (USER 2026-05-29: "3m seems too short — TEST IT"). Missing-data fails OPEN (does not block).
    REENTRY2_QUICK_RECOVERY_ENABLED: bool = True  # quick recovery after exit + momentum
    QUICK_RECOVERY_WINDOW_MIN: float = 120.0  # 2026-04-26 NEW — was hardcoded 60.0 (ez_manage.py:18259, ez_positions_quick.py:14647). Widened to give K3m alignment more time. 9,386 NOT_ALLOWED rejects in 2d at 60.
    # === V8_QUICK v2 WINNER (2026-04-16 micro-experiments) ===
    # Progression:
    #   Baseline (no filter):          Sharpe 0.17 on 11-sym
    #   v1 (strength filter only):     Sharpe 0.94 on 11-sym, 87% WR, 1.42% avg (5.5× baseline)
    #   v2 (+ PT=1.5) 11-sym:          Sharpe 1.39, 89% WR, 1.37% avg (8× baseline)
    #   v2 TOP-5 symbols:              Sharpe 1.74, 96% WR, 1.59% avg, 89 trades
    #   v2 TOP-3 symbols:              Sharpe 1.90, 98% WR, 1.73% avg, 58 trades ✅ EXCEEDS 1.8
    V8Q_STRENGTH_FILTER_ENABLED: bool = False
    V8Q_STRENGTH_MIN_SCORE: float = 5.0
    V8Q_HTF_MIN_ALIGNED: int = 1
    V8Q_MIN_HOLD_BARS: int = 250  # 2026-04-19: 12.5h minimum hold. Sharpe 1.508→2.554 on 48-sym crypto. Was 10.
    V8Q_WT_EXIT_MIN_TFS: int = 3  # 2026-04-19: EXIT=3 → Sharpe 1.065→1.508. 2026-04-25 rapid-grid: EXIT=2 → +37 trades (+30%) BUT pool_sharpe 1.25 vs 2.66. Extra trades are 2-TF noise exits that reverse. NEVER drop below 3.
    V8Q_COOLDOWN_BARS: int = 3
    V8Q_D_TREND_REQUIRED: bool = True
    V8Q_K3M_FLOOR: int = 30
    # Symbol tiers — sorted by per-symbol Sharpe descending
    V8Q_SYMBOL_TIER_TOP3: tuple = ("LINKUSDC", "ETHUSDC", "DOTUSDT")  # Sharpe 1.93, 58 trades, 98.3% WR
    V8Q_SYMBOL_TIER_TOP4: tuple = ("LINKUSDC", "ETHUSDC", "DOTUSDT", "BTCUSDC")  # Sharpe 1.88, 78 trades, 96.2% WR (best balance)
    V8Q_SYMBOL_TIER_TOP5: tuple = ("LINKUSDC", "ETHUSDC", "DOTUSDT", "BTCUSDC", "UNIUSDC")  # Sharpe 1.74, 89 trades, 95.5% WR
    V8Q_SYMBOL_TIER_TOP6: tuple = ("LINKUSDC", "ETHUSDC", "DOTUSDT", "BTCUSDC", "UNIUSDC", "SOLUSDC")  # Sharpe 1.78, 126 trades, 95.2% WR
    REENTRY2_STOCH_CROSS_ENABLED: bool = False  # 2026-04-17: user said stoch crossovers not used anymore — replaced by WT-15m-cross reentry below.
    # === 2026-04-17 REENTRY OVERHAUL — user directive: WT crossovers, not stoch ===
    # C: 15m WT cross after exit + HTF still favorable (1h or 4h wt1 > wt2 for long, vv short) → 1.5x reentry
    REENTRY_WT15M_CROSS_ENABLED: bool = True  # Master switch for WT-15m-cross reentry path
    REENTRY_WT15M_SIZE_MULT: float = 1.5  # Size multiplier when HTF favorable on WT-15m cross reentry
    REENTRY_WT15M_K_MAX: float = 50.0  # k_15m must be below this (LONG) / above (100-val, SHORT) for low-K entry bonus
    REENTRY_WT15M_HTF_FAVOR_REQUIRED: bool = True  # Require wt1_1h>wt2_1h OR wt1_4h>wt2_4h (LONG) — vv for SHORT
    # D: k_15m-based partial reentry (high K = smaller size, not skipped)
    REENTRY_K15M_PARTIAL_ENABLED: bool = True
    REENTRY_K15M_PARTIAL_THRESHOLD: float = 90.0  # User 2026-04-17: LONG k_15m<90 → 100%, >=90 → partial. SHORT mirrors at 10.
    REENTRY_K15M_PARTIAL_MULT: float = 0.5  # Multiplier applied to reentry qty when K is in partial zone
    # E: Post-consolidation boost reentry (easier gate than entry — 1 TF momentum confirm)
    REENTRY_POST_CONSOL_ENABLED: bool = True
    REENTRY_POST_CONSOL_MULT: float = 1.5  # Size boost after consolidation breakout
    REENTRY_POST_CONSOL_ATR_THRESHOLD: float = 0.15  # bar_atr_rank below = compressed
    REENTRY_POST_CONSOL_TFS_REQUIRED: int = 2  # How many TFs (of 1h/4h/D) must be compressed
    # === DELTA ENGINE — FINAL WINNERS (2026-04-09, 48 sym × 4yr, Phase 2 sweep) ===
    # Crypto ST WINNER: Sharpe 0.806, ATR Sharpe 0.857, WR 84.5%, 97.9% profitable
    # Entry: mtf=3, ez=2.5, ea=0.0, tz=1.5, htf=4h_D, cd=120, tw=3m-dominant
    # Exit: speed_decay on 15m, sp=20, max_hold=30, avg hold 7 bars, giveback 0.58%
    # Crypto LT (20 sym): Sharpe 0.457, WR 68.1%, +2.59%/trade — stoch_cross on 4h
    DELTA_ENGINE_ENABLED: bool = True  # Master switch
    DELTA_ENTRY_ENABLED: bool = True
    DELTA_EXIT_ENABLED: bool = True
    # TODO URGENT RETEST 2026-04-19: delta was hard-blocking ALL reentries (z=0.0<1.0 for entire market).
    # Delta = sizing ADDITION, NOT a filter. Set False until NPZ speed_z fields are validated + retest sweep done.
    DELTA_REENTRY_FILTER_ENABLED: bool = False
    WT_COMPOSITE_SCORING_ENABLED: bool = True  # reach existing bonus block at ez_positions_quick.py:1715-1751 (stocks already on via WT_COMPOSITE_SCORING_ENABLED_TRADIER)
    # 2026-07-04 audit fix — register knobs that were read only via getattr(config, X, <default>)
    # (absent from config.py → frozen at the hardcoded default → sweeps were no-ops). Defaults
    # below == the exact prior getattr fallbacks, so ZERO behavior change; now tunable/sweepable.
    WT_COMPOSITE_ENTRY_BLOCK: float = -20.0     # ez_positions_quick.py:2399
    WT_COMPOSITE_ENTRY_STRONG: float = 50.0     # :2409
    WT_COMPOSITE_ENTRY_GOOD: float = 30.0       # :2410
    WT_COMPOSITE_ENTRY_OK: float = 10.0         # :2411
    WT_COMPOSITE_HTF_GATE: bool = False         # :2384
    WT15M_AGAINST_PENALTY: float = -5.0         # :2478
    RZ_BREAKOUT_ENTRY_ENABLED: bool = False     # ez_manage.py:33414
    RZ_BREAKOUT_BAND: float = 0.05              # :33418
    REENTRY_GR_HTF_MIN_TFS: int = 0            # ez_manage.py:31682 (0 → GR-HTF reentry gate dead until raised)
    REENTRY_GR_MIN_IND: int = 2                # :31687
    K3M_CAP_BREAKOUT_BYPASS: bool = True        # ez_positions_quick.py:2722
    OVERBOUGHT_SCORE_GUT_BREAKOUT_BYPASS: bool = True  # :4407
    WT_3M_FORCE_OPEN_REQUIRE_HH_CROSS: bool = True  # 2026-07-04 USER: WT_3M reentry only on higher-high (LONG)/lower-low (SHORT) crossover PRICE
    DELTA_PYRAMID_ENABLED: bool = True  # Disabled until sweep validates
    DELTA_SPEED_SMOOTH: int = 5  # WINNER: sm=5
    DELTA_ACCEL_LOOKBACK: int = 5
    DELTA_TF_WEIGHTS: Optional[dict] = None  # Set in __post_init__ — 3m dominant
    DELTA_TF_Z_THRESHOLD: float = 1.5  # WINNER: tz=1.5
    DELTA_ENTRY_Z_THRESHOLD: float = 2.5  # WINNER: ez=2.5 (was 1.5). NOTE: crypto_t13 2026-04-10 reversal: 1.5=2.045 vs 2.5=1.961 Sharpe on 4sym. Keeping 2.5 until larger cross-symbol sweep confirms reversal.
    DELTA_ENTRY_ACCEL_THRESHOLD: float = 0.0  # WINNER: ea=0.0
    DELTA_ENTRY_MIN_TF: int = 3  # WINNER: mtf=3 (was 2, strict = higher Sharpe)
    DELTA_EXIT_DECAY_RATIO: float = 0.90  # 2026-04-10 04:15 APPLIED — winner per user "yes apply" (was 0.80)
    DELTA_EXIT_TF: str = "3m"  # 2026-04-10 04:15 APPLIED — winner per user "yes apply" (was 15m)
    # 2026-04-10 04:15 SWEEP WINNER APPLIED PER USER DIRECTIVE "yes apply":
    #   dom=3m  → WR 89.0%, PF 26.35, Sharpe +3.45, avg +1.155% per trade ⭐ WINNER (NOW LIVE)
    #   dom=1h  → WR 87.0%, PF 20.7
    #   dom=ANY → WR 87.0%, PF 20.5 (previous safe default)
    #   dom=15m → WR 85.2%, PF 18.7
    # Sweep data: 4yr × 48 crypto symbols × 59,849 trades per combo.
    # 4 winning values now LIVE: DELTA_EXIT_DOM_TF_ENABLED=True, DELTA_EXIT_TF="3m",
    #   DELTA_EXIT_DECAY_RATIO=0.90, DELTA_EXIT_MIN_TF_LOST=1.
    # DO NOT REVERT without user approval. Active monitoring at /Users/niels/logs/ez_manage_*.log.
    DELTA_EXIT_DOM_TF_ENABLED: bool = True  # 2026-04-10 04:15 APPLIED — winner per user "yes apply" (was False)
    DELTA_EXIT_ACCEL_THRESHOLD: float = -0.1
    DELTA_EXIT_OPPOSING_RATIO: float = 1.5
    DELTA_EXIT_MIN_TF_LOST: int = 1  # 2026-04-10 04:15 APPLIED — winner per user "yes apply" (was 2)
    DELTA_EXIT_MIN_HOLD: int = 4
    DELTA_PYRAMID_MAX: int = 8
    DELTA_PYRAMID_MIN_BARS: int = 8
    DELTA_PYRAMID_PRICE_TOL: float = 0.02
    DELTA_PYRAMID_QTY_MULT: float = 1.5
    DELTA_PYRAMID_ACCEL_THRESHOLD: float = 0.2
    DELTA_Z_WINDOW: int = 200
    DELTA_MAX_HOLD_BARS: int = 0  # DISABLED — ride winners until technicals turn. No fixed time exits.
    DELTA_COOLDOWN_BARS: int = 120  # WINNER: 120 bars (6h at 3m)
    DELTA_HTF_GATE: str = "hh_hl_4h"  # 2026-05-22: HH/HL structure gate — LONG if (dc_high_4h>prev AND dc_low_4h>prev) OR ha_4h=="green"; SHORT vv. More reactive than WT crossover. Values: 'none'/'4h'/'4h_D'/'hh_hl_4h'
    DELTA_ATR_ENTRY_FILTER: bool = False  # WINNER: OFF for crypto
    # LT overrides (for swing mode on specific accounts/positions)
    DELTA_LT_EXIT_TF: str = "4h"  # Crypto LT: exit on 4h (stoch_cross)
    DELTA_LT_ENTRY_Z_THRESHOLD: float = 1.5  # LT: looser entry for swing
    DELTA_LT_ENTRY_MIN_TF: int = 2  # LT: mtf=2
    # === DELTA DECISION GATES — on/off for every path (read live, no restart needed) ===
    # Entry paths: delta must confirm before ANY open/augment/reentry
    DELTA_GATE_OPEN: bool = True           # Block OPEN without delta signal
    DELTA_GATE_AUGMENT: bool = True        # Block AUGMENT without delta signal
    DELTA_GATE_REENTRY: bool = True        # Block REENTRY without delta signal
    DELTA_GATE_DC_BREAKOUT: bool = True    # Block DC_BREAKOUT entries without delta
    DELTA_GATE_SBA: bool = False           # Block SBA (underwater adds) without delta — OFF: SBA has own logic
    DELTA_GATE_BB_SQUEEZE: bool = True     # Block BB_SQUEEZE entries without delta
    DELTA_GATE_STDEV_BREAKOUT: bool = True # Block STDEV_BREAKOUT without delta
    DELTA_GATE_VOL_SPIKE: bool = True      # Block VOL_SPIKE_REVERSAL without delta
    DELTA_GATE_GUARANTEED_REENTRY: bool = True  # Block GUARANTEED_REENTRY — #1 loss source
    DELTA_GATE_DIRECTION_FAVORABLE: bool = True # Block DIRECTION_FAVORABLE_REENTRY — #2 loss source
    DELTA_GATE_RATIO_REBALANCE: bool = False    # Block RATIO_REBALANCE opens — OFF: ratio is sacred
    # Exit paths: delta speed_decay as exit signal
    DELTA_EXIT_SPEED_DECAY: bool = True    # Speed decay exit (primary)
    DELTA_EXIT_WT_CROSS: bool = True       # WT cross against as exit
    DELTA_EXIT_DC_FLOOR: bool = True       # DC15M floor break as exit
    DELTA_EXIT_OVERRIDE_NOLOSS: bool = True  # Delta exits bypass STRICT_NO_LOSS
    # Hedge paths
    DELTA_GATE_HEDGE_OPEN: bool = False    # Do NOT gate hedges — they must always execute
    # 2026-05-21 USER: bypass DELTA_GATE for STRONG_BUY and QUICK_OPEN reasons. Trade-resumption
    # after 99% block found in filter-block triage. QUICK_OPEN scanner + scoring-engine STRONG_BUY
    # already pass multiple upstream gates; delta_tracker NO_SIGNAL was vetoing them in addition.
    # 2026-05-21 22:47 — REVERTED to False after ORDIUSDC top-of-range entry incident (fin GR
    # 5.0x @ 4.36393 dc_h1h=4.366, then 14% wick). DELTA_GATE is back ON.
    # 2026-05-22 21:30 — RE-ENABLED: TOP_OF_RANGE_BLOCK (dc_pos≥0.95 all 1h/4h/D) now prevents
    # the ORDI scenario. Without bypass, 100% entry block for hours post-restart.
    DELTA_GATE_STRONG_BUY_QUICK_BYPASS: bool = True
    # Reduce/close paths in service
    DELTA_SERVICE_REDUCE_GATE: bool = True  # Service reductions need delta confirmation
    DELTA_SERVICE_TRAILING_STOP: bool = True # Trailing stops use delta context
    DELTA_SERVICE_BLEED_STOP: bool = True   # Bleed stop uses delta
    # STRUCTURAL RANGE SHIFT EXIT: hold losers until profitable, BUT if the 4h DC channel
    # has moved so far that entry_price is outside [dc_low_4h, dc_high_4h], accept the loss
    # at the DC boundary (dc_high_4h for longs, dc_low_4h for shorts) — the market permanently
    # shifted, recovery is unlikely. Gated by config, default OFF for live, ON for sweep testing.
    STRUCTURAL_RANGE_SHIFT_EXIT: bool = True  # 2026-04-10 APPLIED — sweep winner: SRS=True Sharpe +0.830 vs False +0.804, PnL +29.5% vs +28.5%. NOLOSS=0.0 per user "we'll swallow the commissions"
    STRUCTURAL_RANGE_SHIFT_TF: str = "dc_4h"  # Which range defines "structural shift". Options: dc_1h, dc_4h, dc_D, bb_1h, bb_4h, bb_D. Sweep Tier 15 tests all 6.
    # 2026-04-14 CASCADE: exit on multi-TF exhaustion near the boundary (NOT on approach alone)
    # LONG: entry > upper; wait until 1h+15m k overbought turning down + wt bearish, then 3m wt bearish cross fires close
    # SHORT: entry > lower; wait until 1h+15m k oversold turning up + wt bullish, then 3m wt bullish cross fires close
    STRUCTURAL_RANGE_SHIFT_K_HIGH: float = 75.0     # LONG: stoch_k_1h/15m must be >= this AND turning down
    STRUCTURAL_RANGE_SHIFT_K_LOW: float = 25.0      # SHORT: stoch_k_1h/15m must be <= this AND turning up
    STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS: float = 100.0  # Price must be within N bps of the boundary (100bps = 1%)
    # Multi-TF minimum for ANY action (rule 6: no single TF triggers)
    DELTA_MIN_TF_FOR_ACTION: int = 2       # Minimum TFs confirming for any buy/sell decision
    # ═══ GAIN EROSION TRAILING STOP (sweepable) — user directive 2026-04-10 ═══
    # ═══ BREAKEVEN STOP — user directive 2026-04-10 ═══
    # "NO LOSS ACCEPTED after first 12-15min or dc_low4_3m crossunder"
    # After grace: if gain < 0 → exit. DC_LOW4_3M crossunder: structural stop ANY TIME.
    # BREAKEVEN_GRACE_MINUTES moved to line ~606 (canonical 5.0) — duplicate 15.0 removed 2026-04-16
    BREAKEVEN_DC_LOW4_ENABLED: bool = True   # DC_LOW4_3M structural stop
    # Feature toggles for ablation (2026-04-11)
    RZ_ZSCORE_ZONE_ENABLED: bool = True
    RZ_DIV_BLOCK_MIN: int = 2
    RZ_TWO_PHASE_EXIT_ENABLED: bool = True
    RZ_DIV_EXIT_ENABLED: bool = True
    RZ_ZSCORE_EXIT_ENABLED: bool = True
    # ═══ REENTRY — user directive "reenter ASAP" ═══
    REENTRY_COOLDOWN_S: float = 300.0        # 2026-07-08 GAINMO anti-churn: 0→300s ("reenter ASAP" churned; matches EZ_REENTRY_PRICE_CROSS_MIN_GAP_S=300 locked 2026-06-25; churn law in GAINMO_MAXIMIZATION_20260708.md)
    # Aggressive tier window (2026-04-17 reentry sweep: crypto peak at 8-12 bars = 24-36min on 3m).
    # Within this window, a fresh dc_x3m or stoch_x3m with 1h still trending bypasses safety gates.
    REENTRY_AGGRESSIVE_WINDOW_MIN: float = 30.0  # 30 min on crypto (3m base = 10 bars — matches Sharpe peak)
    # Scoring integration
    DELTA_SCORE_WEIGHT: float = 30.0       # Weight of delta signal in AdvancedSignalRater (0-100)
    DELTA_ENTRY_SCORE_BONUS: int = 15      # Score bonus when delta confirms entry
    DELTA_ENTRY_SCORE_PENALTY: int = -25   # Score penalty when delta opposes entry
    DELTA_EXIT_SCORE_BONUS: int = 20       # Score bonus when delta confirms exit
    # STRUCTURAL FIX 2026-04-17 — A+B+C (ez_manage.py / ez_positions_quick.py)
    # Context: ALGOUSDT SHORT killed at -0.74% (BREAKEVEN) right before big drop, no reentry.
    # A: HTF-trending exit veto — block 3m structural stops when HTF still with us
    HTF_EXIT_VETO_ENABLED: bool = True
    HTF_EXIT_VETO_MIN_ALIGNED: int = 2           # of {1h, 4h, D} WT
    HTF_EXIT_VETO_MAX_LOSS_PCT: float = 2.0      # only veto when abs(gain) ≤ 2% (don't hold big bleeders)
    # B: Favorable-move reentry — force reentry when price moved ≥ X% in our favor after exit
    # VALIDATED 2026-04-17: queue test 0.5%=lost 0.016, 1.0%=won +0.410 Sharpe +$102 PnL
    REENTRY_FAVORABLE_MOVE_PCT: float = 1.0      # 1.0% favorable move required (0.5% too noisy)
    REENTRY_FAVORABLE_HTF_MIN: int = 2           # at least 2 HTFs must still be aligned
    REENTRY_FAVORABLE_QTY_MULT: float = 1.0
    # ═══ OBLIGATORY HEDGE — SACRED RULE RE-ENABLED 2026-04-17 ═══
    # User: "Every short (or long v.v.) with wt1_3m>wt2_3m HAS TO HEDGE. Forever rule."
    # History: killed 2026-03-29 after 15,378 rogue opens (3 OBLIGATORY_HEDGE loops without
    # tracker consultation). Cascade guards now in execute_dual_hedge (5244-5261): blocks
    # hedge-of-hedge, already-hedged, in-flight dedup. Rule re-enabled with multi-TF WT gate.
    # NEVER DISABLED via `if False:` — tune only via these switches.
    OBLIGATORY_HEDGE_ENABLED: bool = False               # 2026-05-20 USER MANDATE: hedge OFF entirely, MTF compound exit replaces it. Prior "FOREVER RULE" comment superseded by explicit user authorization 2026-05-20. ROLLBACK: True restores obligatory-hedge scanner.
    OBLIGATORY_HEDGE_MIN_LOSS_PCT: float = -0.5           # 2026-05-06: -0.25→-0.5 per HEDGE_BANDAID_BACKTEST winner. trigger when gain below this
    # Per-TF enables (VALIDATED 2026-04-17 60d test on 8 bleeding inf shorts):
    # best=3m+1h both required (47% precision, +359% cumulative PnL proxy, +0.19% avg).
    # Adding 1m/15m didn't help (correlated); K filter hurt (-19% PnL).
    OBLIGATORY_HEDGE_WT_USE_1M: bool = False             # noise, no signal vs 3m
    OBLIGATORY_HEDGE_WT_USE_3M: bool = True              # primary signal (sacred rule)
    OBLIGATORY_HEDGE_WT_USE_15M: bool = False            # redundant with 3m+1h
    OBLIGATORY_HEDGE_WT_USE_1H: bool = True              # HTF confirmation
    OBLIGATORY_HEDGE_WT_TFS_REQUIRED: int = 0            # 2026-04-27 USER: hedge ENTRY = gain<0 only (no WT confirmation needed). Same-symbol same-qty hedge → effective delta = 0 → deep loss is irrelevant. Was 2 (required wt 2/2 against). Set 0 to fire hedge unconditionally on gain<0 trigger. Per `feedback_hedge_k_wt_3m_1h_gate.md`: "hedge ENTRY = gain<0 only".
    # ═══ OBLIGATORY HEDGE OR CLOSE — USER MANDATE 2026-05-09 ═══
    # ⚠️ DEATH PENALTY — DO NOT DISABLE WITHOUT EXPLICIT USER PERMISSION
    # Re-enables periodic scan_and_hedge_losers loop with proper cascade guards. Trigger:
    # wt1_3m AND wt1_1h against the trade direction → MUST take 100% same-symbol hedge.
    # If hedge cannot be opened for ANY reason → close losing position immediately, OVERRIDING NOLOSS.
    # SKY.USDT incident 2026-05-09: -17% for 11 days, no hedge, no close — exact failure mode this fixes.
    OBLIGATORY_HEDGE_OR_CLOSE_LOOP_ENABLED: bool = False              # master switch for the periodic loop
    OBLIGATORY_HEDGE_OR_CLOSE_LOOP_INTERVAL_SECONDS: float = 60.0    # how often to scan losing positions
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H: bool = False                 # USER 2026-05-11: was True → False. Live data: 5,848 HEDGE_FAILED_FALLBACK_CLOSE in 24h (31.8% of all closes) because 1h hadn't flipped when 3m did. Superseded by HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H below.
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H: bool = True           # USER 2026-05-11 LATEST: hedge OPEN requires wt1_3m against AND (wt1_15m against OR wt1_1h against). Catches sharp 3m+15m moves the 1h-lag couldn't, while keeping 2-TF confirmation. Close still uses wt_3m alone (HEDGE_CLOSE_MODE='wt_3m').
    HEDGE_TRIGGER_GR_SCORE_ENABLED: bool = True                      # USER 2026-05-13: add GR HTF vote score as 3rd confirmation path. Hedge fires when wt1_3m AND (15m OR 1h OR gr_against_score >= GR_HEDGE_SCORE_FLOOR). Reduces churn from WT-only timing noise.
    GR_HEDGE_SCORE_FLOOR: int = 15                                    # min GR total-vote-score to trigger hedge without 15m/1h WT confirmation. Score = SUM of per-TF raw indicator votes against (0-35 range: 5 crypto TFs × 7 ind). 15 ≈ "3 TFs fully against (5×3) or 2 TFs with 7-8 each". User mandate: keep 15-20 or fires every bar.
    HEDGE_FAILED_FALLBACK_CLOSE_ENABLED: bool = True                 # on hedge failure → close (HEDGE_FAILED bypass already in NOLOSS list)
    # ═══ REENTRY NEVER-SKIP — USER MANDATE 2026-05-09 ═══
    # ⚠️ DO NOT DISABLE WITHOUT EXPLICIT USER PERMISSION
    # When True: reentry signals that fail to queue (transient: lock contention, in-flight, redis miss, crash)
    # get RETRIED up to REENTRY_DISPATCH_MAX_ATTEMPTS times with REENTRY_DISPATCH_BACKOFF_S between attempts.
    # On final failure, REENTRY_DISPATCH_FAILED_PERSIST log line is emitted (CRITICAL level, visible) and
    # the failure is recorded on trade_manager._reentry_dispatch_failures for diagnostics.
    # GUARANTEED price-cross reentry path (ez_positions_quick.py:15885+) keeps its existing crash-on-timeout
    # mechanism — this switch wraps the OTHER 8 dispatch sites in process_single_reentry_evaluation_epq.
    REENTRY_NEVER_SKIP_ENABLED: bool = True
    # ═══ GUARANTEED_REENTRY_AUGMENT (USER 2026-05-29; corrected 2026-05-30) ═══
    # User report: price moved 15% with NO reentry. Root cause (live logs): 200×
    # BLOCKED_MTF_NO_ARMED_STATE + 31× BLOCKED_HTF_TREND_VETO blocking entries/reentries.
    # This switch ONLY un-blocks: when True, REENTRY and AUGMENT actions BYPASS the MTF
    # armed-state filter and HTF_TREND_VETO. It does NOT touch any gain gate:
    #   • REENTRY = OPEN a FLAT position (positionAmt==0) → fires on bounce/cross/breakout,
    #     with NO gain requirement (no position exists, so there is no gain to wait for).
    #   • AUGMENT = add to an EXISTING position → still requires gain > MIN_GAIN (3%);
    #     that 3% gate is enforced separately and is NOT affected by this switch.
    # Fresh non-reentry OPENs still respect MTF/HTF. ROLLBACK: set False.
    GUARANTEED_REENTRY_AUGMENT_ENABLED: bool = True
    # USER 2026-05-30: every-minute MOMENTUM force-open SAFETY NET. Scans every tradeable_key; force-OPENs any
    # FLAT key where price > PCT% above sma_200_15m AND wt1_15m < WT_CAP (not overbought) AND wt1_15m rising.
    # Catches the biggest winners the normal entry path misses. ROLLBACK: MOMENTUM_SMA_WATCHDOG_ENABLED=False.
    MOMENTUM_SMA_WATCHDOG_ENABLED: bool = True
    MOMENTUM_SMA_WATCHDOG_INTERVAL_S: float = 60.0
    MOMENTUM_SMA_WATCHDOG_PCT: float = 1.0          # 2026-05-31 USER: 2.0->1.0 (global per_sym sweep: 1% median pool_sharpe 0.155 > 2% 0.150). Per-sym pct_entry from FINAL book overrides this. price must be > this % above sma_200_15m
    OBLIGATORY_SMA200_WT3M_ENABLED: bool = True     # 2026-06-04 USER: unblockable obligatory open in momentum_sma_watchdog_loop — runs BEFORE the cooldown/per-tick gates (was missing 24h tumbles: 4800 SKIP cooldown). SHORT when price >OBLIGATORY_SMA200_PCT% BELOW sma_200_15m AND wt1_3m falling; LONG when >PCT% ABOVE AND wt1_3m rising. reason OBLIGATORY_OPEN bypasses COUNTER_TREND (+ shorts bypass MTF); flood rate-breaker/cold-start still apply. OBLIGATORY_OPEN positions receive a frozen tight dc_low4_3m/dc_high4_3m leash (20-bar fallback). ROLLBACK: False.
    OBLIGATORY_SMA200_PCT: float = 1.0              # distance beyond sma_200_15m (%) that triggers the obligatory open
    OBLIGATORY_OPEN_USD: float = 400.0              # notional $ for each obligatory open (escalates via the watchdog ladder on subsequent WT crosses)
    PERSYM_FINAL_BOOK_ENABLED: bool = True          # 2026-05-31 USER "put all new per_sym settings live + block negative-sharpe keys". data/persym_final_book.json: 96 tradeable (>=30tr & ps>0 & not-short-uptrend) enabled + per-sym pct_entry/size_cap; 54 tested-but-excluded -> side disabled (PER_SYM_SIDE_DISABLED gate blocks entries, never exits). ROLLBACK: False.
    CONVICTION_SIZING_ENABLED: bool = True          # 2026-06-02 USER: scale base entry size by per-sym conviction (size_mult from FINAL book) so proven winners (ZEC/MU/SNDK) open BIG, tag-alongs small. Applied in _psym_sps. ROLLBACK: False.
    CONVICTION_SIZING_MAX: float = 8.0              # safety cap on conviction multiplier (crypto-validated cap; prevents runaway). ZEC size_mult ~3.3 -> base $45 x 3.3 ~= $147.
    # ═══════════════════════════════════════════════════════════════════
    # 🏆 INF DEDICATED-WINNERS MODE — USER 2026-07-08 mandate: "dedicate inf account to only
    # trade the absolute winners (per_sym gainers that manage to get a better 7D score get big
    # trades)". Gate lives in ez_manage.execute_now (the single order chokepoint): any inf
    # OPEN/AUGMENT/ENTRY for a symbol NOT in INF_DEDICATED_WINNERS is BLOCKED — no force-open/
    # OBLIGATORY/emergency-reason bypass. CLOSE/REDUCE/exit paths are NEVER touched.
    # ROLLBACK: INF_DEDICATED_WINNERS_ENABLED=False.
    INF_DEDICATED_WINNERS_ENABLED: bool = True
    # Consistent positives — median sym_sharpe 0.41-0.56, 76-100% of central-DB runs positive,
    # n=52-136 runs each; source GAINMO_MAXIMIZATION_20260708.md. All 12 are legacy-USDT syms
    # (no USDC perp exists for any of them — USDC-over-USDT policy respected).
    INF_DEDICATED_WINNERS: set = field(default_factory=lambda: {})
    INF_7D_BEAT_SIZE_MULT: float = 2.0              # winner-set keys whose per_sym 7D-agent winner beats baseline (delta_wsharpe from data/hourly_reconfig/inf/active_config_7d.json) get this x entry size on inf; downstream MAX_ORDER_VALUE caps still clamp
    INF_7D_BEAT_MIN_DELTA: float = 0.0              # delta_wsharpe must EXCEED this for the 7D boost to fire
    MOMENTUM_SMA_WATCHDOG_WT_CAP: float = 80.0      # wt1_15m must be BELOW this (not yet overbought)

    # Classic chart formations.  The detector is shared with the causal NPZ
    # vector path; individual action switches stay independently controllable
    # so only full-universe holdout-supported families are promoted live.
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
    MOMENTUM_SMA_WATCHDOG_COOLDOWN_S: float = 300.0 # per-key re-fire cooldown
    # ═══════════════════════════════════════════════════════════════════
    # 2026-06-03 USER MANDATE — MULTI-TF DONCHIAN FORCE-OPEN + ESCALATING AUGMENT.
    # "NOTHING can be flat below/above dc_low/high_15m, and a HUGE position below/above
    #  dc_low/high_1h (bigger again for 4h, D). NEVER turn this off — adapt values only."
    # The watchdog force-OPENs any flat tradeable key whose price is beyond the prev/raw
    # Donchian channel of a TF (LONG px>=dc_high_{tf} or dc_high_crossover_{tf};
    # SHORT px<=dc_low_{tf} or dc_low_crossunder_{tf}) — NO WaveTrend filter on the DC path.
    # Size scales by the LARGEST TF broken (15m base → 1h huge → 4h/D bigger).
    # NOTE: live indicator dict carries DC for 3m/15m/1h/4h/D only — there is NO Weekly DC
    # field produced live yet, so 'W' is not wired (would require dc_high_W/dc_low_W in
    # ez_indicators precompute — NOT fabricated). D is the top tier until W is added.
    # ═══════════════════════════════════════════════════════════════════
    WATCHDOG_DC_FORCE_OPEN_ENABLED: bool = True      # master — USER: never disable, adapt sizes only
    WATCHDOG_DC_TFS: list = field(default_factory=lambda: ["15m", "1h", "4h", "D"])  # W absent live (see note)
    WATCHDOG_DC_BASE_USD: float = 25.0               # 15m base notional
    WATCHDOG_DC_MULT_15M: float = 1.0                # 15m → small
    WATCHDOG_DC_MULT_1H: float = 4.0                 # 1h → HUGE
    WATCHDOG_DC_MULT_4H: float = 8.0                 # 4h → bigger
    WATCHDOG_DC_MULT_D: float = 16.0                 # D → biggest
    WATCHDOG_DC_MAX_USD: float = 600.0               # hard safety cap on any single force-open notional
    # Escalating reopen/augment on each fresh 3m WT cross in favor: 20% → 50% → 100% → 150%
    # more of current position notional, capped. USER: "EVERY time wt1_3m turns in favor it
    # reopens at 20/50/100/150% more." Pyramids WITH momentum (COUNTER_TREND_ADD_BLOCK still
    # guards against adding against wt1_1h — anti-martingale preserved).
    WATCHDOG_WT3M_ESCALATE_ENABLED: bool = True
    WATCHDOG_WT3M_ESCALATE_LADDER: list = field(default_factory=lambda: [1.00, 2.00, 3.00, 5.00])  # 2026-06-03 USER MANDATE: re-enter/add at 100-500% of position on each fresh favorable continuation so price can never recover/continue without us holding MORE than before. Was [0.20,0.50,1.00,1.50]. Per-add still bounded by WATCHDOG_WT3M_ESCALATE_MAX_USD (account-size rail). Exact rungs to be refined by the 4yr A/B ("unless backtest chose other multipliers"). ROLLBACK: restore [0.20,0.50,1.00,1.50].
    WATCHDOG_WT3M_ESCALATE_MAX_USD: float = 600.0    # cap per escalation add
    # USER 2026-05-30 ABSOLUTE: NOTHING stays open on a sharp move the other way; martingale destroyed everywhere.
    HTF_AGAINST_FORCE_CLOSE_ENABLED: bool = True     # close ANY position (winner OR loser) the instant wt1_1h is against its side
    HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H: bool = False # also require wt1_4h against (sharper); default just 1h per user mandate
    COUNTER_TREND_ADD_BLOCK_ENABLED: bool = True     # block any OPEN/AUGMENT/REENTRY whose side is against wt1_1h (kills martingale)
    COUNTER_TREND_SMA200_BYPASS_ENABLED: bool = True # 2026-06-04 USER directional rule: a SHORT below sma_200_15m (LONG above) WITH 1h structure (1h lower-low/higher-high OR wt1_1h agreeing) is TREND-ALIGNED → bypass the laggy wt1_1h COUNTER_TREND_ADD_BLOCK so tumble-shorts fire (was 14k BLOCKED_COUNTER_TREND_1H_AGAINST_SHORT/2h). Genuine counter-trend (wrong side of sma_200_15m) stays blocked. ROLLBACK: False.
    # USER 2026-05-30: NEVER MISS A MOVE. A true breakout — price breaking the PREVIOUS-bar 1h Donchian
    # (LONG: price>dc_high_1h_prev; SHORT: price<dc_low_1h_prev) — is a 100% pass: it bypasses the MTF
    # armed-state gate (#1 live open-blocker, BLOCKED_MTF_NO_ARMED_STATE) for ANY tradeable symbol. Prev-bar
    # level because the live Donchian auto-extends on the breakout bar. COUNTER_TREND_ADD_BLOCK still runs
    # first, so a breakout against the 1h trend can never sneak through. ROLLBACK: =False.
    BREAKOUT_DC1H_BYPASS_ENABLED: bool = True
    # USER 2026-05-30: HIGHER open/reentry quantity when the bounce is strongly extended past sma_200_15m.
    # Tiered multiplier on OPEN/AUGMENT/REENTRY qty by |price-sma_200_15m|/sma_200_15m (LONG above / SHORT
    # below). ADD-TO-STRENGTH, never martingale — only sizes up when price is already extended the RIGHT way.
    # ROLLBACK: BREAKOUT_SIZE_LADDER_ENABLED=False.
    BREAKOUT_SIZE_LADDER_ENABLED: bool = True
    BREAKOUT_SIZE_SMA200_T1_PCT: float = 1.0     # >=1.0% past sma_200_15m → ×T1
    BREAKOUT_SIZE_SMA200_T1_MULT: float = 1.5
    BREAKOUT_SIZE_SMA200_T2_PCT: float = 1.5     # >=1.5% → ×T2
    BREAKOUT_SIZE_SMA200_T2_MULT: float = 2.0
    BREAKOUT_SIZE_SMA200_T3_PCT: float = 2.5     # >=2.5% → ×T3
    BREAKOUT_SIZE_SMA200_T3_MULT: float = 3.0
    BREAKOUT_SIZE_MAX_MULT: float = 3.0          # hard cap on the qty multiplier
    REENTRY_DISPATCH_MAX_ATTEMPTS: int = 3                            # number of retry attempts on transient queue failure
    REENTRY_DISPATCH_BACKOFF_S: float = 0.4                           # backoff between attempts (async sleep)
    # ═══ PEAK_GIVEBACK_PROTECTION (2026-04-19) ═══
    # ⚠️ DO NOT DISABLE WITHOUT EXPLICIT USER PERMISSION — REAL MONEY PROTECTION
    # MOVEUSDT bled from +1.26% peak to -13% because HTF_EXIT_VETO blocked breakeven exit.
    # Fires when position was profitable and gains have been given back. Bypasses HTF_EXIT_VETO.
    PEAK_GIVEBACK_PROTECTION_ENABLED: bool = True    # master switch — keep hard_zero breakeven-protect branch alive
    PEAK_GIVEBACK_MIN_PEAK_PCT: float = 0.5          # must have reached >= 0.5% gain to activate
    PEAK_GIVEBACK_DROP_PCT: float = 0.5              # (no-op while PEAK_GIVEBACK_DROP_TRIGGER_ENABLED=False) drop threshold for the giveback branch.
    PEAK_GIVEBACK_DROP_TRIGGER_ENABLED: bool = False # USER 2026-05-11: was implicit-True → False. 0.5% giveback trigger was closing breakouts on first retest. User mandate: "wait until rejection (from bb/dc) before closing". The hard_zero breakeven branch remains active (governed by PEAK_GIVEBACK_HARD_ZERO_ENABLED).
    PEAK_GIVEBACK_HARD_ZERO_ENABLED: bool = False    # 2026-04-27 OFF: was forcing exit at exactly 0% gain after a profitable peak — that's WHY UMA/INX/NEIRO all closed at 0%. Now technicals own the loss-side; let positions ride past BE if WT/K/DC haven't reversed.
    # ═══ HARD_BREAKEVEN_FLOOR (2026-04-19) ═══
    # ⚠️ DO NOT DISABLE WITHOUT EXPLICIT USER PERMISSION — REAL MONEY PROTECTION
    # Overrides HTF_EXIT_VETO for breakeven stop when position was genuinely profitable.
    # Winner-became-loser = IMPOSSIBLE rule. HTF alignment irrelevant once gain was positive.
    HARD_BREAKEVEN_FLOOR_ENABLED: bool = True        # ⚠️ DO NOT DISABLE WITHOUT EXPLICIT USER PERMISSION
    HARD_BREAKEVEN_MIN_PEAK_PCT: float = 0.5         # if max_gain >= 0.5%, HTF veto bypassed for breakeven
    # ═══ MANDATORY_HEDGE_ON_NEGATIVE (2026-04-19) ═══
    # ⚠️ DEATH PENALTY — DO NOT DISABLE WITHOUT EXPLICIT USER PERMISSION
    # Unconditional same-symbol hedge when gain < threshold regardless of WT direction.
    # Catches positions where HTF is still aligned (so OBLIGATORY_HEDGE WT gate never fires)
    # but price has been bleeding for a long time (MOVEUSDT: -13%, no hedge, 1 month open).
    MANDATORY_HEDGE_GAIN_THRESHOLD_PCT: float = -0.5    # 2026-05-06: wt1_15m-gated hedge fires when gain < this (was getattr default -0.05%)
    MANDATORY_HEDGE_ON_NEGATIVE_ENABLED: bool = True   # ⚠️ DEATH PENALTY — DO NOT DISABLE WITHOUT EXPLICIT USER PERMISSION
    MANDATORY_HEDGE_HARD_THRESHOLD_PCT: float = -2.0   # unconditional hedge at -2% (WT-gated fires at -0.5%)
    # ═══ HEDGE MAX AGE (2026-04-17) — user rule: "minutes max hours never days" ═══
    # Every open hedge must close by this age cap regardless of WT state. Safety net for
    # stuck hedges when WT-flip close rule fails to fire (e.g., loser's wt flat for hours).
    HEDGE_MAX_AGE_HOURS: float = 6.0                     # close any hedge older than this
    # ═══ DC_RECOVERY_EXIT per-account disable (2026-04-17) ═══
    # "entry_price outside dc_4h range close" rule = the only legitimate close-at-loss path.
    # Temporarily disabled for inf while bleeding shorts (-4% to -26%) can't absorb the realized
    # losses. Re-enable for inf once account stabilizes or positions recover near entry.
    DC_RECOVERY_EXIT_DISABLED_ACCOUNTS: list = field(default_factory=lambda: ['inf'])
    # Legacy REENTRY paths — ON because they're the only thing that lifted Sharpe >1.
    # Each one is now gated by TOLERANT delta conditions (looser than fresh-entry gate).
    LEGACY_GUARANTEED_REENTRY: bool = True     # 2026-04-15: logic REWRITTEN with 60min/HTF gate (see reentry_enforcement_loop)
    LEGACY_DIRECTION_FAVORABLE: bool = True    # ON — gated by tolerant delta
    LEGACY_DC_BREAKOUT_REENTRY: bool = True    # ON — gated by tolerant delta
    LEGACY_PROC_SINGLE_REENTRY: bool = False   # OFF 2026-04-15 per user — fired mid-move on LTF only
    # Per-variant reentry switches (each fires a distinct path so backtest can A/B)
    LEGACY_REENTRY_GUARANTEED_BOTTOM: bool = False     # ez_manage.py:16128 — wt15m bounce + 1h trend + 2/4 WT, 150%
    LEGACY_REENTRY_GUARANTEED_CROSS: bool = False      # ez_manage.py:16133 — exit crossed + 3/4 WT, 50-100% by DC pos
    LEGACY_REENTRY_GUARANTEED_2WT: bool = False        # ez_manage.py:16138 — exit crossed >0.3% + 2/4 WT, 50%
    LEGACY_REENTRY_PSR_QUICK_RECOVERY: bool = True     # 2026-04-25 ENABLED — was False (dead switch). Implements user's "early-exit reentry on price recovery" hypothesis: ≤60min after exit + price moved past last_reduction_price ± atr_3m + K3m aligned → REENTRY conviction 75. Sites: ez_manage.py:18254, ez_positions_quick.py:14572.
    LEGACY_REENTRY_PSR_K_DC_CROSSOVER: bool = False    # ez_manage.py:18777 LONG / 18798 SHORT — k_3m/15m crossover above dc_low_3m/15m
    LEGACY_REENTRY_PSR_FULL_DC: bool = True            # 2026-04-25 ENABLED + monitored — fires on stoch_3m crossover & price >/< dc_basis_15m, OR dc_basis_crossover_3m. Sites: ez_manage.py:18369, ez_positions_quick.py:14634. Conviction 80.
    LEGACY_REENTRY_PSR_DC_BOUNCE: bool = True          # 2026-04-25 ENABLED + monitored — fires on DC band bounce within 8h of last reduction, requires dc_high_1h>dc_high_1h_ant. Sites: ez_manage.py:18393, ez_positions_quick.py:14652. Conviction 65.
    LEGACY_WR_PULLBACK: bool = True            # ON
    LEGACY_FAST_CUT_LOSS: bool = False         # OFF — % stop in disguise
    LEGACY_AGGRESSIVE_LOSS_CUT: bool = False   # OFF — single TF 1m flip
    # TOLERANT delta gate for REENTRIES (looser than fresh-entry — reentry re-takes a proven position)
    DELTA_REENTRY_MIN_TF: int = 2              # 2 TFs vs 3 for fresh entries
    DELTA_REENTRY_Z_THRESHOLD: float = 1.0     # 1.0 vs 2.5 for fresh entries
    DELTA_REENTRY_HTF_GATE: str = "4h"         # 4h only vs 4h_D for fresh entries
    DELTA_REENTRY_REQUIRE_NOT_EXITING: bool = True  # Delta must not be in exit state
    # === RED ZONE — structural level entry/exit using BB stdev + DC position + WT structure ===
    RZ_ENTRY_ENABLED: bool = True  # Use RED ZONE for entries (BASELINE cross, TOP breakout, BOTTOM rejection)
    # USER 2026-05-16 kill switch (PHBUSDT $0.69/$0.14 account-wipe incident): the BASELINE_BOUNCE_SHORT
    # variant fired against bullish 3m+15m flow with only 1h vel bearish + only one engine voting
    # (+ENGINES(htf=0.70)), opened $49 SHORT, then hedge/exit cascade wiped both accounts. SHORT side
    # of baseline-bounce DISABLED until sweep-validated. LONG side stays on. Gate site: wt_dc_delta.py.
    RZ_BASELINE_BOUNCE_SHORT_ENABLED: bool = False
    RZ_EXIT_ENABLED: bool = False  # 2026-04-19: premature exits dropped Sharpe 2.5→1.25 on 48-sym sweep. Was True.
    STRUCTURAL_EXIT_GATE_ENABLED: bool = True  # USER MANDATE 2026-07-21 (MU_LONG trb: 20 closes in 88min while price rallied +3.15%, every close ~0.00% gain). NEVER exit while price is going up (long) / down (short); an exit needs an LTF collapse (lower high AND lower low AND close below prev low) OR a lower-high+lower-low on 1h or 4h. Enforced in wt_dc_delta.structural_exit_permitted() (live crypto + live stocks + Tier-2) and vectorized in v8_quick_engine.compute_exit_signals (Tier-1). Kills the k_1h>80 / dc_pos>0.7 top-zone churn. Loss exits R1/R2/HEDGE_FAILED are NOT affected. ROLLBACK: False.
    RZ_TOP_BB_THRESHOLD: float = 0.85  # bb_pct_b above this = TOP zone (sweep: 0.85/0.92/0.97)
    RZ_BOT_BB_THRESHOLD: float = 0.15  # bb_pct_b below this = BOTTOM zone (sweep: 0.15/0.08/0.03)
    RZ_LEGS_MIN: float = 20.0  # Minimum legs remaining for entry (sweep: 10/20/35)
    RZ_REQUIRE_STRUCT: bool = False  # Require HH/HL or LH/LL structure for entry (sweep: True/False)
    RZ_K_EXIT: float = 95.0  # 2026-04-11 SWEEP: 95 > 90 > 80. Now k_15m (not k_1h) + SMART_RZ slowdown gate. 80=destructive, 95=best.
    RZ_MFI_EXIT: float = 85.0  # MFI above this at TOP = exit long (sweep: 75/85)
    RZ_K_ENTRY_MAX: float = 50.0  # Entry LONG only when k_1h < this (sweep: 40/50/60). Proven: 50
    RZ_K_ENTRY_BOTTOM: float = 10.0  # Stoch K below this at BOTTOM = exit short (mirror)
    RZ_MFI_ENTRY_BOTTOM: float = 15.0  # MFI below this at BOTTOM = exit short (mirror)
    BOUNCE_AUGMENT_ENABLED: bool = False  # 2026-08-10 USER MANDATE: NEVER augment losing positions — prohibited always, not a switch
    BOUNCE_AUGMENT_PAPER: bool = True  # Paper mode — log only, no real orders
    BOUNCE_AUGMENT_MIN_LOSS_PCT: float = -0.5  # ANY loss triggers evaluation (user: "not -10%, ANY loss")
    BOUNCE_AUGMENT_K_D_THRESHOLD: float = 20.0  # k_D must be below this (oversold on daily)
    BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE: float = 0.02  # Price within 2% of dc_low_D
    BOUNCE_AUGMENT_K_D_CROSSING_UP: bool = True  # k_D must be turning up (k_D > k_D_prev)
    BOUNCE_TOP_EXIT_ENABLED: bool = False  # KILLED 2026-03-30: This is a PERCENTAGE stop loss in disguise. Exits ONLY on technicals (WT turn, volume die, DC reversal). NEVER on % thresholds.
    BOUNCE_TOP_MIN_HOLD_MINUTES: float = 2880.0
    BOUNCE_TOP_MIN_LOSS_PCT: float = -3.0
    BOUNCE_TOP_MAX_LOSS_PCT: float = -50.0
    # === L/S RATIO ENFORCEMENT ===
    LS_RATIO_ENFORCE: bool = True  # Master toggle for L/S ratio enforcement
    LS_RATIO_MIN: float = 0.11  # WT alignment controls direction, ratio follows — allow 90/10 short/long
    LS_RATIO_MAX: float = 9.0  # WT alignment controls direction, ratio follows — allow 90/10 long/short
    LS_RATIO_HARD_MIN: float = 0.05  # Near-zero: ratio must FOLLOW the WT direction, not fight it
    LS_RATIO_HARD_MAX: float = 3.50  # Was 2.00 — raised to let shorts open while ratio recovers. Still blocks extreme >3.5 longs.
    # === RATIO EMERGENCY EXIT — close worst longs when ratio is dangerously high ===
    RATIO_EMERGENCY_EXIT_ENABLED: bool = False  # PERMANENTLY DISABLED: closing losers = Sharpe 19 vs ratio-only 357. Fix ratio by OPENING underweight side, NEVER by closing losers.
    RATIO_EMERGENCY_EXIT_THRESHOLD: float = 999.0  # Set to impossible value so it can NEVER trigger even if enabled by accident
    RATIO_EMERGENCY_EXIT_MAX_LOSS_PCT: float = -999.0  # Set to impossible value
    RATIO_EMERGENCY_EXIT_MAX_PER_CYCLE: int = 0  # Zero = can never close anything
    RATIO_EMERGENCY_EXIT_COOLDOWN: float = 999999.0  # Infinite cooldown
    LS_RATIO_REBALANCE_THRESHOLD: float = (
        0.60  # Was 0.35 — rebalance kicks in earlier to prevent short-heavy drift
    )
    LS_RATIO_LOG_INTERVAL: int = 60  # Seconds between ratio warning logs
    # === HTF DIRECTION GATE — entries must align with D/4h/1h WT + price vs SMA200D ===
    # Added 2026-04-16 after audit: shorts opened against bullish 4h/1h/D caused 1:10 short-heavy PnL trap
    HTF_DIRECTION_GATE_ENABLED: bool = False
    HTF_GATE_MIN_CONFIRMATIONS: int = 2  # 2026-04-16: lowered 3→2 per user directive "HTF confirmations should not be exaggerated". D still mandatory via HTF_GATE_D_MANDATORY.
    HTF_GATE_D_MANDATORY: bool = False  # 2026-04-27 owner: loosened from True. With min_conf=2, requiring D=aligned in addition was too strict — V3 blocks at 03:31 had D=✗ but 4h+1h+SMA all aligned. Now D can dissent if 2+ of (4h,1h,SMA) align.
    HTF_GATE_SIGNALS_SMA200D: bool = True  # include price vs sma_200_D as the 4th signal
    HTF_GATE_APPLY_TO_OPEN: bool = True  # gate applies to OPEN actions
    HTF_GATE_APPLY_TO_AUGMENT: bool = True  # 2026-04-16 flipped True: enforce 4h/D veto on augments too. Existing 3m+15m gate still runs in addition.
    # TF→size multipliers for RZ/breakout entries. Size = START_POSITION_SIZE × mult_for_tf_of_signal.
    # TF parsed from reason string markers (_3M_, _15M_, _1H_, _4H_, _D_). Default 1.0× if no marker.
    # Higher TF signals get bigger size because the setup is more reliable and less likely to reverse.
    BREAKOUT_TF_SIZE_ENABLED: bool = True
    BREAKOUT_TF_SIZE_MULT_3M: float = 0.5
    BREAKOUT_TF_SIZE_MULT_15M: float = 1.0
    BREAKOUT_TF_SIZE_MULT_1H: float = 2.0
    BREAKOUT_TF_SIZE_MULT_4H: float = 3.0
    BREAKOUT_TF_SIZE_MULT_D: float = 4.0
    BREAKOUT_TF_SIZE_CAP_MULT: float = 5.0  # absolute cap on any TF multiplier
    # 2026-04-16 per user directive: NO _LONG tradeable_keys can be without a position while price > dc_high_3m and rising. vv for _SHORT.
    # Extends existing _process_single_override_check to also OPEN from zero (it currently skips zero positions at line 18061).
    # Respects tradeable_keys (hand-picked), HTF_GATE via queue_trade_action gates downstream, and ratio gates.
    TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED: bool = True  # 2026-04-16: re-enabled. The HTF_TREND_VETO block is fixed separately via HTF_TREND_VETO_BYPASS_REASONS.
    TRADEABLE_KEYS_MANDATORY_SIZE_USD: float = 9.0  # uses START_POSITION_SIZE if <=0
    # ═══ 2026-04-16 MARKET-DATA INTEGRATION — route 598 pre-computed composite fields into scoring + winner protection ═══
    # Per user directive: "integrate ALL values, understand ST/LT outperformers, best buys get priority, don't close too soon".
    # Zero new strategy logic — only routes already-computed fields (0dc_moment, 0ranking_points_global, etc.) into
    # existing scoring and exit paths. All switchable so a sweep can disable individually.
    # --- Feature 1: Rank-driven conviction boost (uses 0ranking_points_global) ---
    RANK_CONVICTION_ENABLED: bool = False  # 2026-04-19 FIX: hard block in engine ≠ score bonus in live; tested on broken data. Re-sweep pending.
    RP_STRONG_THRESHOLD: float = 70.0  # |ranking_points_global| >= this, direction matches → bonus
    RP_STRONG_BONUS: float = 15.0
    RP_WEAK_THRESHOLD: float = 30.0    # |ranking_points_global| < this → penalty (low quality symbol)
    RP_WEAK_PENALTY: float = -10.0
    RP_OPPOSITE_PENALTY: float = -20.0  # ranking_points opposite to direction + strong → heavy penalty
    # --- Feature 2: DC-moment strength gate (uses 0dc_moment) ---
    DC_MOMENT_ENABLED: bool = False  # 2026-04-19 FIX: tested on broken B15/B11 data. Re-sweep pending.
    DC_MOMENT_STRONG_THRESHOLD: float = 40.0  # |dc_moment| >= this, sign matches direction → bonus
    DC_MOMENT_STRONG_BONUS: float = 10.0
    DC_MOMENT_OPPOSITE_PENALTY: float = -15.0  # dc_moment opposite to direction + strong → penalty
    # --- Feature 3: Winner protection in exit evaluation (uses 0ranking_points_global) ---
    # High-rank winners skip the poll-based exit cycle until they've locked >= RP_PROTECT_MIN_GAIN.
    # Technical exits (WT_CROSS_EXIT, DC_LOW4_3M, DELTA_EXIT, HEDGE paths, STRUCTURAL_RANGE_SHIFT) still fire.
    WINNER_PROTECT_ENABLED: bool = False  # 2026-04-27 sweep T1: 8/91 winners had this False; flipping per sweep recommendation. Was True.
    RP_PROTECT_THRESHOLD: float = 70.0  # ranking_points_global matches direction AND >= this → protect
    RP_PROTECT_MIN_GAIN: float = 1.0    # 2026-04-17 coord descent: 1.0 beats 1.5/2.0 (+1.618 vs 1.547). Was 2.0.
    # --- Feature 4: ST vs LT outperformer split in ez_rankings ---
    # Populates top_100_long_term (currently unused) and keeps top_longs/top_shorts biased to ST momentum.
    # Ratio_rebalance_loop + RZ augment priority can then prefer LT winners for augments, ST for fresh opens.
    ST_LT_SPLIT_ENABLED: bool = True
    LT_SCORE_WEIGHT_HTF: float = 0.7   # weight for 4h/D/sentiment when scoring LT outperformers
    ST_SCORE_WEIGHT_LTF: float = 0.7   # weight for 3m/15m/velocity when scoring ST outperformers
    LT_TOP_SIZE: int = 100             # cap for top_100_long_term
    # 2026-04-16: HTF_TREND_VETO at ez_manage.py:11013-11023 blocks all non-REDUCE non-HEDGE non-REENTRY non-QUICK
    # entries when HTF is opposite. It has no RZ/breakout exemption. That's why breakouts through red zones,
    # DC levels, compression expansions don't actually open — they bypass MY new gate but hit this old one.
    # These reasons already pass their own internal HTF validation (RZ in wt_dc_delta._run_redzone) or are
    # mandated regardless (TRADEABLE_KEYS_MANDATORY). Substrings matched uppercased against the reason.
    HTF_TREND_VETO_BYPASS_ENABLED: bool = True
    # 2026-05-21 20:05 — wired knob (was hardcoded ±5 in ez_manage.py:18379/18382).
    # Live: 70 BLOCKED_HTF_TREND_VETO_SHORT/5min on men/fin from htfScore=5-8. Wider threshold lets
    # weak-conviction HTF trends through. Sweep [5,6,7,8,10,12,15]. ROLLBACK: 5.
    HTF_TREND_VETO_SCORE_MIN_ABS: float = 5.0
    # 2026-05-21 20:15 — Knob-gated rollback for HTF_TREND_VETO augment-only fix (ez_manage.py:18358).
    # True  → AUGMENT veto only fires on REAL augments (position has size, action not OPEN). Default after fix.
    # False → PRE-FIX behavior: AUGMENT veto also catches plain OPENs.
    # 497 men + 338 fin SHORT OPENs/8h were blocked by the buggy pre-fix block (action=OPEN, is_augment=True
    # because is_augment=not is_reduce). Per user: backtest and revert if Sharpe delta negative.
    HTF_AUG_VETO_FIX_ENABLED: bool = True
    HTF_TREND_VETO_BYPASS_REASONS: list = field(default_factory=lambda: [
        "TRADEABLE_KEYS_MANDATORY",
        "RZ_",
        "RED_ZONE",
        "BASELINE_BOUNCE",
        "BREAKDOWN_TRUCK",
        "BOTTOM_HUGE",
        "TRUCK_LOAD",
        "REJECTION_OLD_REDZONE",
        "COMPRESSION_BREAKOUT",
        "DC_BREAKOUT",
        "BB_SQUEEZE_BREAKOUT",
        "OVERRIDE_DC_PRICE_MOVE",   # existing enforcement at ez_manage.py:18113
        "STDEV_BREAKOUT",
        "OBLIGATORY_OPEN",
    ])
    HTF_GATE_BYPASS_RZ: bool = True  # preserve RZ bounce bypass (bounce logic HTF-validates internally)
    # === WT CROSS EXIT — fires when WT flips against direction on 1h (+ 15m confirm) ===
    WT_CROSS_EXIT_ENABLED: bool = True
    WT_CROSS_EXIT_REQUIRE_15M_CONFIRM: bool = True
    WT_CROSS_EXIT_MIN_AGE_MINUTES: float = 1.0  # 2026-04-16: 2.0→1.0 per user "exit before getting into a loss"
    WT_CROSS_EXIT_APPLIES_TO_LOSERS: bool = True  # fire on losing positions (the whole point)
    WT_CROSS_EXIT_APPLIES_TO_WINNERS: bool = True
    BREAKEVEN_GRACE_MINUTES: float = 5.0  # was 15.0 — cut losers faster on gain erosion
    LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK: bool = True  # stale-price abort skipped for technical (non-%) exits
    SHORT_ABOVE_EMA20_IS_PENALTY: bool = True  # flip BC_104 bonus → penalty (shorts above EMA20 are counter-trend)
    # === RATIO PNL-WEIGHTED TARGET OVERRIDE ===
    # Breadth-only target clamps to [25,75]. When per-side PnL diverges, shift target toward winning side up to [10,90].
    RATIO_PNL_WEIGHT_ENABLED: bool = True
    RATIO_PNL_WEIGHT: float = 0.5  # 0 = pure breadth, 1 = pure PnL signal
    RATIO_PNL_DELTA_THRESHOLD: float = 3.0  # min |long_avg_gain - short_avg_gain| % to engage override
    RATIO_PNL_ACCELERATION: float = 2.5  # target_long shift per 1% pnl delta (pnl_signal = 0.5 + delta*accel/100)
    RATIO_PNL_TARGET_LONG_MIN: float = 10.0  # clamp min target% when PnL divergent
    RATIO_PNL_TARGET_LONG_MAX: float = 90.0  # clamp max target% when PnL divergent
    RATIO_REBALANCE_COOLDOWN_NORMAL: float = 3600.0
    RATIO_REBALANCE_COOLDOWN_CRASH: float = 1800.0
    RATIO_REBALANCE_COOLDOWN_EXTREME: float = 3600.0
    RATIO_REBALANCE_COOLDOWN_PNL_DIVERGENT: float = 1200.0
    RATIO_REBALANCE_SIZE_MULT: float = 4.0  # base mult vs START_POSITION_SIZE
    RATIO_REBALANCE_SIZE_SKEW_BOOST: float = 0.05  # extra mult per 1pp of skew above dead zone (0.05 = 5% per pp)
    RATIO_REBALANCE_SIZE_MAX_MULT: float = 10.0  # absolute cap on size mult
    RATIO_REBALANCE_MAX_OPENS_NORMAL: int = 8
    RATIO_REBALANCE_MAX_OPENS_STUCK: int = 10
    RATIO_REBALANCE_MAX_OPENS_EXTREME: int = 12
    RATIO_REBALANCE_APPLY_HTF_GATE: bool = True  # re-check per-symbol HTF before each open
    # PnL-tightened entry ratio gates
    RATIO_PNL_DYNAMIC_GATES_ENABLED: bool = True
    RATIO_PNL_GATE_SOFT_MIN: float = 0.5  # tighten soft_min (block more shorts) when longs winning
    RATIO_PNL_GATE_SOFT_MAX: float = 2.0  # tighten soft_max (block more longs) when shorts winning
    # === RATIO CLOSE LOSING OVERWEIGHT — opt-in; defaults OFF ===
    # Historically closing losers to fix ratio destroyed Sharpe (19 vs ratio-only 357).
    # Enable only after monitoring one cycle of logs. Closes worst losers on overweight side when:
    # (skew > MIN_SKEW_PP) AND (|PnL delta| > MIN_PNL_DELTA_PCT) AND (position gain < MIN_LOSS_PCT).
    RATIO_CLOSE_LOSING_OVERWEIGHT: bool = False
    RATIO_CLOSE_LOSING_MIN_LOSS_PCT: float = -5.0
    RATIO_CLOSE_LOSING_MIN_SKEW_PP: float = 40.0
    RATIO_CLOSE_LOSING_MIN_PNL_DELTA_PCT: float = 10.0
    RATIO_CLOSE_LOSING_MAX_PER_CYCLE: int = 2
    RATIO_CLOSE_LOSING_COOLDOWN_SECONDS: float = 900.0
    STORM_REDUCE_ENABLED: bool = (
        True  # Allow reducing losing position when HTF confirms storm
    )
    # === MOMENTUM RIDER ===
    MOMENTUM_RIDER_ENABLED: bool = False  # DISABLED 2026-03-29: bypasses ALL execute_now guards + auto-expands tradeable_keys
    MOMENTUM_RIDER_ACCOUNT: str = "men"
    MOMENTUM_RIDER_SCAN_INTERVAL: float = 10.0
    MOMENTUM_RIDER_MAX_SYMBOLS: int = 5
    MOMENTUM_RIDER_BASE_SIZE_USD: float = 50.0
    MOMENTUM_RIDER_MAX_SIZE_USD: float = 400.0
    MOMENTUM_RIDER_HEDGE_RATIO: float = 1.2
    MOMENTUM_RIDER_DC_WIDTH_MIN: float = 8.0
    MOMENTUM_RIDER_REL_VOL_MIN: float = 3.0
    MOMENTUM_RIDER_COOLDOWN: float = 300.0
    # === NEWS SENTIMENT ===
    NEWS_SENTIMENT_ENABLED: bool = True  # Master toggle for news sentiment in rankings
    NEWS_SENTIMENT_WEIGHT: float = 0.10  # Max +/-10% score adjustment from news
    NEWS_POLL_INTERVAL_CRYPTO: int = 300  # 5 min (CryptoPanic)
    NEWS_POLL_INTERVAL_SOCIAL: int = 900  # 15 min (Reddit + Twitter)
    NEWS_SENTIMENT_DECAY_HOURS: int = 4  # Older articles decay to 0
    NEWS_SENTIMENT_MIN_ARTICLES: int = 2  # Min sources to form a score
    # === SYMBOL PERFORMANCE TRACKING ===
    SYMBOL_PERF_ENABLED: bool = True
    SYMBOL_PERF_WINDOW_DAYS: int = 14
    SYMBOL_PERF_MIN_TRADES: int = 5
    SYMBOL_PERF_MAX_MULT: float = 10.0
    SYMBOL_PERF_MIN_MULT: float = 0.1
    SYMBOL_PERF_REFRESH_SECONDS: float = 3600.0
    SYMBOL_PERF_DECAY_HOURS: float = 12.0
    # === OUTLIER DETECTION ===
    OUTLIER_DETECTOR_ENABLED: bool = True
    OUTLIER_SCAN_INTERVAL: float = 60.0
    OUTLIER_STUCK_HOURS: float = 2.0
    OUTLIER_STUCK_ATR_FACTOR: float = 0.5
    OUTLIER_RUNAWAY_ATR_FACTOR: float = 2.0
    OUTLIER_STALE_HOURS: float = 6.0
    # === BC_162: CRYPTO SPIKE FADE (contrarian P&D fading) ===
    # SWEEP WINNER: thresh=10% lb=3 k=80 cd=3 → Sharpe 2.43, 47% WR, $541K PnL (noloss=N)
    CRYPTO_SPIKE_FADE_ENABLED: bool = True
    CRYPTO_SPIKE_FADE_THRESHOLD_PCT: float = 10.0  # SWEEP: 10% > 5% > 3% > 2%. Higher threshold = fewer but much better trades.
    CRYPTO_SPIKE_FADE_LOOKBACK_BARS: int = 3  # SWEEP: 3 bars (9min) beats all longer lookbacks. Catch spike fast.
    CRYPTO_SPIKE_FADE_K_EXHAUSTION: float = 80.0  # SWEEP: 80 slightly better than 75. Not critical.
    CRYPTO_SPIKE_FADE_MAX_POSITIONS: int = 6
    CRYPTO_SPIKE_FADE_COOLDOWN_SEC: float = 540.0  # 3 bars × 3min = 9min. SWEEP: cd=3 bars wins.
    # === CRYPTO FH MOMENTUM (BACKTEST_CHANGE_MT3) ===
    # US market open momentum: at 14:00-14:30 UTC, crypto reacts to Wall Street direction.
    # 10-10:30 AM ET = manipulation/direction window. Same logic as stock FH momentum.
    # Validated on stocks: Sharpe 1.54, +100% PnL, 25/25 configs profitable.
    CRYPTO_FH_MOMENTUM_ENABLED: bool = True
    CRYPTO_FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5
    CRYPTO_FH_MOMENTUM_POSITION_SIZE_MULT: float = 1.0  # Multiplier of START_POSITION_SIZE
    CRYPTO_FH_MOMENTUM_MAX_POSITIONS: int = 4
    CRYPTO_FH_MOMENTUM_DC_CONFIRM: bool = True  # DC retest scoring
    CRYPTO_FH_MOMENTUM_DC_MAX_LONG: float = 0.5
    # === WINNER/LOSER TIERING ===
    TIER_ENABLED: bool = True
    TIER_A_WIN_RATE: float = 0.60
    TIER_A_MIN_GAIN: float = 0.3
    TIER_A_MIN_TRADES: int = 10
    TIER_B_WIN_RATE: float = 0.45
    TIER_B_MIN_TRADES: int = 5
    TIER_A_MULTIPLIER: float = 1.2
    TIER_C_MULTIPLIER: float = 0.7
    # TREND_GATES: bool =                     False
    # HTF1_CONF:bool =                        False
    # HTF4_CONF:bool =                        False
    TREND_GATES: bool = True
    HTF1_CONF: bool = True  # BACKTEST_CHANGE_35: REVERTED (was False). Ablation: -1.680 Sharpe on 233 crypto symbols, only 7% improved. Note: OFF helped on tradier (20 stock symbols) — keep OFF in config_tradier only.
    HTF4_CONF: bool = True
    BASIS_CONDITION: bool = False  # BACKTEST: OFF is +0.67 delta Sharpe (dc_basis_15m/1h both SKIP in sweep)  # No opening on wrong side of dc_basis_15m + 1h + 4h
    # === BB SQUEEZE BREAKOUT ===
    BB_SQUEEZE_ENABLED: bool = True  # Master toggle for BB squeeze breakout entries
    BB_SQUEEZE_WIDTH_PERCENTILE: float = 0.2  # Width must be in bottom 20% to count as squeeze
    BB_SQUEEZE_MIN_ALIGNMENT: int = 10  # Minimum alignment score to allow BB squeeze entry
    BB_SQUEEZE_COOLDOWN: float = 300.0  # Seconds between BB squeeze entries per symbol
    VOL_SPIKE_ENABLED: bool = True  # Volume spike reversal: 93.5% WR, Sharpe 13.9
    VOL_SPIKE_RELVOL_THRESHOLD: float = 3.0  # Relative volume must be > 3x 20-bar avg
    VOL_SPIKE_BODY_RATIO: float = 0.7  # Candle body must be > 70% of total range
    VOL_SPIKE_COOLDOWN: float = 300.0  # Seconds between vol spike entries per symbol
    VOL_SPIKE_MIN_ALIGNMENT: int = 3  # Lower alignment threshold for spike entries
    VOL_SPIKE_LS_MAX_IMBALANCE: float = 1.5  # Max L/S ratio imbalance before blocking
    # === 2.5σ STDEV BREAKOUT (HTF breakout + LTF retest scaling) ===
    # Replaces all_red/all_green. When price breaks 2.5σ on D/4h, enter immediately.
    # Scale in on LTF retests to the band with larger size. Technical exit on band failure.
    # Math: bb_pctb > 1.125 = above 2.5σ (with 2.0σ BB bands). pctb_at_Nσ = (N+2)/4
    STDEV_BREAKOUT_ENABLED: bool = False  # Kill switch OFF — backtest sweep first
    STDEV_SUPPRESS_EARLY_EXIT: bool = False  # Suppress vel/delta exits when approaching BB band
    STDEV_BB_RZ_EXIT_ENABLED: bool = False  # Exit when price exits daily BB band (rejection)
    STDEV_BB_RZ_EXIT_TF: str = "D"
    STDEV_BB_RZ_SUPPRESS_PCTB: float = 0.85
    STDEV_BREAKOUT_PCTB_LONG: float = 1.125  # bb_pctb threshold for LONG breakout (2.5σ)
    STDEV_BREAKOUT_PCTB_SHORT: float = -0.125  # bb_pctb threshold for SHORT breakout (2.5σ)
    STDEV_BREAKOUT_HTF_LIST: List[str] = field(default_factory=lambda: ["D", "4h"])  # HTFs for breakout detection
    STDEV_BREAKOUT_RETEST_TF_LIST: List[str] = field(default_factory=lambda: ["1h", "15m"])  # LTFs for retest
    STDEV_BREAKOUT_RETEST_PCTB_MIN: float = 0.85  # Retest: pctb must pull back to at least this (near 2σ band)
    STDEV_BREAKOUT_RETEST_PCTB_MAX: float = 1.05  # Retest: pctb must not exceed this (still near band, not blown through)
    STDEV_BREAKOUT_MAX_RETESTS: int = 3  # Max retest adds per breakout cycle
    STDEV_BREAKOUT_RETEST_SIZE_MULT: float = 1.5  # Size multiplier for retest entries (more volume on retests)
    STDEV_BREAKOUT_COOLDOWN: float = 600.0  # Min seconds between breakout entries per symbol
    STDEV_BREAKOUT_RETEST_COOLDOWN: float = 300.0  # Min seconds between retest adds
    STDEV_BREAKOUT_SCORE: int = 25  # Score for initial breakout entry
    STDEV_BREAKOUT_RETEST_SCORE: int = 22  # Score for retest entries
    STDEV_BREAKOUT_RVOL_MIN: float = 1.2  # Min relative volume for breakout confirmation
    STDEV_BREAKOUT_MAX_AGE_BARS: int = 50  # Breakout state expires after this many bars (no follow-through)
    STDEV_BREAKOUT_EXIT_PCTB_FAIL: float = 0.75  # Exit if HTF pctb falls below this (breakout failed, back inside 1σ)
    STDEV_BOUNCE_ENABLED: bool = False
    STDEV_BOUNCE_PCTB_LONG: float = 0.05
    STDEV_BOUNCE_PCTB_SHORT: float = 0.95
    STDEV_BOUNCE_RVOL_MIN: float = 1.2
    STDEV_BOUNCE_HTF_LIST: List[str] = field(default_factory=lambda: ["D", "4h"])
    STDEV_REJECT_EXIT_ENABLED: bool = False
    STDEV_REJECT_EXIT_TF: str = "D"
    STDEV_REJECT_EXIT_ZONE: float = 0.80
    STDEV_REJECT_EXIT_RETURN: float = 0.65
    STDEV_BREAKOUT_EXIT_WT_ENABLED: bool = True  # Also exit on WT turn against on 1h
    # === HLR_RALLY: Higher Low Rally / Lower High Breakdown detector ===
    # Detects price making higher lows (LONG) or lower highs (SHORT) while pulling back toward 200 SMA.
    # Each confirmed TF earns escalating score bonus + forces oversized entry qty.
    # Near-SMA = price within HLR_SMA_BAND_PCT of sma_200 on that TF (full points).
    # Off-SMA = structure confirmed but no pullback touch (half points, 70% size mult).
    HLR_RALLY_ENABLED: bool = True
    HLR_SMA_BAND_PCT: float = 0.03  # within 3% of SMA200 to qualify as "near-SMA" (full points)
    HLR_PTS_3M: int = 15    # score bonus for higher-low on 3m TF
    HLR_PTS_15M: int = 25   # score bonus for higher-low on 15m TF
    HLR_PTS_1H: int = 40    # score bonus for higher-low on 1h TF
    HLR_PTS_4H: int = 60    # score bonus for higher-low on 4h TF
    HLR_PTS_D: int = 90     # score bonus for higher-low on Daily TF
    HLR_PTS_W: int = 130    # score bonus for higher-low on Weekly TF
    HLR_SZ_3M: float = 1.2    # entry qty multiplier when 3m TF fires
    HLR_SZ_15M: float = 1.5   # entry qty multiplier when 15m TF fires
    HLR_SZ_1H: float = 2.0    # entry qty multiplier when 1h TF fires
    HLR_SZ_4H: float = 3.5    # entry qty multiplier when 4h TF fires
    HLR_SZ_D: float = 6.0     # entry qty multiplier when Daily TF fires
    HLR_SZ_W: float = 10.0    # entry qty multiplier when Weekly TF fires (max cap 10x)
    HLR_SZ_MAX: float = 10.0  # absolute cap on HLR size multiplier regardless of stacking
    HLR_OFF_SMA_PTS_FRAC: float = 0.5   # fraction of pts when structure fires but price not near SMA
    HLR_OFF_SMA_SZ_FRAC: float = 0.7    # fraction of size mult when structure fires but price not near SMA
    HLR_MIN_TFS_FOR_BYPASS: int = 1     # min TF count needed to bypass SHIT_IDEA gate (1 = any TF ≥ 15m)
    HLR_BYPASS_MIN_TF_WEIGHT: int = 25  # min HLR points from a single TF to bypass SHIT_IDEA gate (25 = 15m tier)
    # HLR_TOP_EXIT: sell at WT delta slowdown across HTFs, then reenter at 1.5-3x previous positionAmt
    HLR_TOP_EXIT_ENABLED: bool = True
    HLR_TOP_MIN_GAIN_PCT: float = 1.5    # HLR_TOP_EXIT only fires when position gain >= this % (prevents exit at 0.3%)
    HLR_TOP_MIN_TFS: int = 2             # min TFs confirming top (must include at least one ≥ 4h)
    HLR_TOP_VEL_1H_THRESH: float = -1.0  # wt_velocity_1h must be < this to count as 1h top signal
    HLR_TOP_VEL_4H_THRESH: float = 0.0   # wt_velocity_4h < this counts (0 = any negative)
    HLR_TOP_VEL_D_THRESH: float = 0.0    # wt_velocity_D < this counts
    HLR_REENTRY_MULT_1H: float = 1.5     # reentry qty = prev_positionAmt * this when 1h fires top
    HLR_REENTRY_MULT_4H: float = 2.0     # reentry qty multiplier when 4h fires top
    HLR_REENTRY_MULT_D: float = 2.5      # reentry qty multiplier when D fires top
    HLR_REENTRY_MULT_W: float = 3.0      # reentry qty multiplier when W fires top (max)
    HLR_REENTRY_MAX_AGE_S: float = 14400.0  # max seconds since HLR_TOP_EXIT to still use the mult (4h)
    # === BACKTEST SWEEP WINNERS (2026-03-16) ===
    K3M_CAP: int = 80  # BACKTEST_CHANGE_105: REVERTED to 80. Tournament (10 rounds, 3042 combos) winner uses 80. BACKTEST_CHANGE_8 (70) reversed.
    K3M_FLOOR: int = 30  # BACKTEST_CHANGE_9: NEW. Block SHORT when k_3m <= 30 (mirror of K3M_CAP)
    CYCLE_TP_PCT: float = 0.60  # Let winners run to 60%. TP only used as absolute cap, NOT as early exit.
    CYCLE_TP_CONDITIONAL_EXIT: float = 0.003  # BACKTEST_CHANGE_101: was 0.5%. OKX top traders exit at 0.3% when stoch turns against. Matches profitable trader behavior.
    ACCOUNT_TP_PCT: Dict[str, float] = field(default_factory=dict)  # DISABLED 2026-03-29: NO fixed TP — ride winners until technicals turn. Exits via CYCLE_TP_STOCH_AGAINST, DC_BASIS_PROFIT_EXIT, IN_GAIN_TREND_EXIT only.
    TREND_ACCOUNTS: List[str] = field(default_factory=lambda: [])  # 2026-04-18: removed flz — TREND_ACCOUNTS blocks ALL gain-harvest exits, flz:BTCUSDC_LONG held through 7.6% gain into -155% loss
    TREND_HTF_MIN_BULL: int = 7
    TREND_HTF_MIN_BEAR: int = 7
    TREND_EXIT_SCORE_FLIP: int = 0
    TREND_MIN_GAIN_EXIT: float = 0.10
    TREND_HEDGE_MAX_SEC: int = 180
    HTF_STRICT: bool = True  # BACKTEST_CHANGE_106: REVERTED to True. Tournament winner uses strict (all HTFs K>D+HA aligned). Sharpe 242 vs 133 for kd_only.
    HA_3M_ENTRY_WEIGHT: float = -0.5  # BACKTEST_CHANGE_31: was 0.0. HA harmful for entries — use as negative (contrarian) signal
    # === METRIC SWEEP WINNERS (2026-03-16 — 360 sym × 4 TF × 45 metrics, min Sharpe 75) ===
    # bb_squeeze: 11 symbols, avg Sharpe 89.7 — tight bands predict explosive moves
    BB_SQUEEZE_ENTRY_ENABLED: bool = True  # Enter when Bollinger bands compress (< threshold)
    BB_SQUEEZE_THRESHOLD_1H: float = 0.03  # bb_squeeze < this on 1h = entry signal
    BB_SQUEEZE_THRESHOLD_15M: float = 0.025  # bb_squeeze < this on 15m = entry signal
    # sma200_dist: 9 symbols, avg Sharpe 93.2 — price distance from SMA200
    SMA200_DIST_ENTRY_ENABLED: bool = True  # SHORT when price crosses back above SMA200 on 1h
    SMA200_DIST_LONG_THRESHOLD: float = -3.0  # BACKTEST_CHANGE_7: was -2.0. Wider captures more mean-reversion setups
    # ema20_slope: 6 symbols, avg Sharpe 86.4 — EMA20 momentum direction
    EMA20_SLOPE_ENTRY_ENABLED: bool = True
    EMA20_SLOPE_SHORT_THRESHOLD_1H: float = 0.05  # SHORT when ema20 slope > this (extended, mean revert)
    # Best TF for entries: 1h (55% of winners), 15m (38%)
    SWEEP_OPTIMAL_ENTRY_TF: str = "1h"  # 1h produced most Sharpe>75 results
    SWEEP_OPTIMAL_HOLD_BARS: int = 8  # Most common winning hold period
    # === BACKTEST MATRIX WINNERS (2026-03-18 — 4,982,146 combos × 360 sym × 5 TF × 22 indicators) ===
    # Entry signals: mean-reversion on 3m is the core edge
    EMA_DIST_ENTRY_ENABLED: bool = True  # BACKTEST_CHANGE_3: #1 signal in top 5000
    EMA_DIST_LONG_THRESHOLD: float = -1.0  # LONG when ema_dist < -1.0 (price far below EMA20)
    EMA_DIST_SHORT_THRESHOLD: float = 1.0  # SHORT when ema_dist > 1.0 (price far above EMA20)
    MOM3_ENTRY_ENABLED: bool = True  # BACKTEST_CHANGE_4: #2 signal, 3-bar momentum mean-reversion
    MOM3_LONG_THRESHOLD: float = -1.0  # LONG when mom3 < -1.0
    MOM3_SHORT_THRESHOLD: float = 1.0  # SHORT when mom3 > 1.0
    MOM5_ENTRY_ENABLED: bool = True  # BACKTEST_CHANGE_5: #3 signal, 5-bar momentum
    MOM5_LONG_THRESHOLD: float = -1.0
    MOM5_SHORT_THRESHOLD: float = 1.0
    BB_ENTRY_LONG_THRESHOLD: float = -0.2  # BACKTEST_CHANGE_6: BB %B extremes
    BB_ENTRY_SHORT_THRESHOLD: float = 1.0
    # Exit: tiered TP + hold bars
    CYCLE_TP_TIERED_ENABLED: bool = True  # BACKTEST_CHANGE_12: AGGRESSIVE tiered wins 74% of symbols
    CYCLE_TP_TIERED_LEVELS: list = field(default_factory=lambda: [0.0015, 0.003, 0.005, 0.007, 0.010, 0.015, 0.020, 0.030])
    CYCLE_TP_TIERED_FRAC: float = 0.25  # Close 25% of remaining at each tier
    STOCH_CROSS_3M_EXIT_ENABLED: bool = False  # 2026-04-10: REMOVED — stoch is way lagging vs DELTA/WT. DELTA→WT priority means this never reaches if upstream works.
    HARD_MAX_LOSS_PCT: float = -5.0  # 2026-04-10: SAFETY NET. NO position ever allowed past -5% loss. DELTA/WT/DC should normally fire way before. Set -9999 to disable (ablation only).
    OPTIMAL_HOLD_BARS_3M: int = 999  # ABLATION_V3_REVERT: was 21 (BC_15). Confirmed on BOTH v2 (4-day) and v3 (3-year, 215 sym): +1.04 Sharpe, 85% improved. Forced exit kills winners.
    OPTIMAL_HOLD_BARS_15M: int = 999  # BACKTEST_CHANGE_16: REVERTED (was 13). Ablation: -6.983 Sharpe, WORST of 52 tested. 0% symbols improved. Hold period too short kills winners.
    # Sizing: ema_dist proportional sizing
    EMA_DIST_SIZING_ENABLED: bool = True  # BACKTEST_CHANGE_24: scale size by ema_dist strength
    EMA_DIST_SIZING_MULT: float = 2.0  # Max 2x size when ema_dist is extreme
    # Per-symbol sizing multipliers for top backtest performers
    SYMBOL_SIZE_MULTIPLIERS: Dict[str, float] = field(default_factory=lambda: {"CELOUSDT": 2.0, "DYDXUSDT": 1.5, "GTCUSDT": 1.5, "1000SATSUSDT": 1.5})  # BACKTEST_CHANGE_27
    # === DC WIDTH INDEX SIZING (2026-03-16) ===
    DC_WIDTH_SIZING_ENABLED: bool = True
    DC_WIDTH_MAX_MULT: float = 5.0  # BACKTEST_CHANGE_23: was 8.0. DC is 7th best indicator, don't over-weight
    DC_WIDTH_CAP_MULT: float = 10.0
    # === DYNAMIC SIZING: DC EDGE (2026-03-22 — 45 configs, sideways vs trend) ===
    DC_EDGE_SIZING_ENABLED: bool = True  # BACKTEST_CHANGE_122: Scale position size by DC channel position. Edge=trending=3x, center=sideways=1x. +68% PnL vs flat.
    DC_EDGE_SIZING_MAX_MULT: float = 3.0  # BACKTEST_CHANGE_122: Max 3x at DC edges (trending). 1x at DC center (sideways).
    DC_EDGE_SIZING_MIN_MULT: float = 1.0  # BACKTEST_CHANGE_122: Min 1x at DC center. Set to 0.5 to reduce in sideways.
    DC_EDGE_SIZING_PERIOD: int = 20  # DC lookback period for edge detection
    # === MASTER TRADER ANALYSIS FILTERS (2026-03-18 — 400 trades × 20 Finandy traders, indicator overlay) ===
    ENTRY_VOL_MIN_RATIO: float = 1.3  # BACKTEST_CHANGE_100: was 1.0. Winners enter at 1.95x avg vol vs losers 1.27x — raise floor
    SHORT_RSI_MIN_1H: float = 40.0  # BACKTEST_CHANGE_101: Block SHORT when rsi_1h < 40
    LONG_STOCH_CHASE_BLOCK: bool = True  # BACKTEST_CHANGE_102: NEW. Block LONG when stoch_k_1h > 70 AND ha_streak > 2 — chasing overbought = loser
    ENTRY_ATR_PCT_MIN: float = 1.5  # BACKTEST_CHANGE_103: NEW. Min ATR% for entry — winners trade 1.97% ATR vs losers 1.22%
    SHORT_ABOVE_SMA20_BONUS: int = 15  # BACKTEST_CHANGE_104: NEW. Bonus for SHORT when price above EMA20 — mean-reversion shorts win (3.15% above vs losers 0.10%)
    # === NO-LOSS NATURAL EXIT + K-ZONE ENTRY + BOUNCE REENTRY (2026-03-21 — 207 sym × 15m, 1765 combos) ===
    NOLOSS_MIN_PROFIT_PCT: float = 0.0  # SWEEP 2026-04-01: ELIMINATED. Was 3.0%. WT technical exits now fire at any gain/loss. HTF WT exhaustion = exit, period.
    # === BACKTEST_CHANGES_100 — OKX Top Trader Findings (2026-03-24) ===
    # Source: Analysis of 20 OKX copy trading leaders with 200+ trades
    # Key finding: profitable traders have LOWER WR (66%) but cut losses 13x faster
    # The #1 trader (1121% ROI, Expert-Ethash-Camel) uses only 14x leverage, 68% WR
    # See: data/okx_traders/TOP_TRADER_ANALYSIS.xlsx for full analysis
    # BACKTEST_CHANGE_102/103: REVERTED. Loss cutting DESTROYED 20%+ weekly in real trading.
    # Our system is fundamentally different from OKX single-position traders:
    # - We run 350 hedged symbols where L/S ratio IS the hedge
    # - Cutting a loser breaks the hedge ratio and forces re-entry at worse prices
    # - The real edge is NEVER selling at a loss — proven by months of live data
    # MAX_LOSS_HOLD_MINUTES and LOSS_CUT disabled permanently.
    LOSS_CUT_ENABLED: bool = False  # NEVER enable — proven to lose 20%+ weekly
    WIN_TRAIL_EROSION_PCT: float = 0.0  # STRUCTURAL ONLY — was 0.50, now disabled (user 2026-08-14: never exit on percentage, only DC reject / WT 15m cross / HH+HL)
    K_ZONE_ENTRY_ENABLED: bool = True  # BACKTEST_CHANGE_109: K-zone entry — enter when K in zone (<35 LONG / >65 SHORT) + K turning + candle confirms. No crossover wait needed.
    K_ZONE_LONG_THRESHOLD: int = 35  # BACKTEST_CHANGE_101: Tournament v2 (76.8K configs): 35/50/70 identical but 35 matches original K-zone logic. Was 90 (basically no filter).
    K_ZONE_SHORT_THRESHOLD: int = 10  # BACKTEST_CHANGE_101: wide zone accepts more profitable entries (was 65)
    K_ZONE_ENTRY_BONUS: int = 25  # Score bonus when K-zone + candle confirms (HA flip, hammer, engulfing)
    BOUNCE_REENTRY_ENABLED: bool = True  # BACKTEST_CHANGE_110: After profitable exit, require K to pull back to zone before reentering. 96%+ reentry WR.
    BOUNCE_REENTRY_K_RESET_LONG: int = 35  # K must drop below this after exit before LONG reentry allowed
    # === MOVER DETECTION — INF ACCOUNT (2026-03-22 — 65k combos × 207 sym, 99%+ WR) ===
    MOVER_DETECTION_ENABLED: bool = True  # BACKTEST_CHANGE_111: Scan all symbols for sudden spikes, fade them (mean reversion)
    MOVER_LOOKBACK: int = 8  # BACKTEST_CHANGE_111: Bars to compute slope/linearity (8 on 15m = 2h window). Best: 8-13.
    MOVER_THRESHOLD: float = 5.0  # BACKTEST_CHANGE_111: Min mover score to qualify. 5.0 = 99.7% WR across 193 symbols. Higher = fewer but cleaner.
    MOVER_LINEARITY_MIN: float = 0.3  # BACKTEST_CHANGE_111: Min R² — 0.3 = clean directional move (not choppy)
    MOVER_VOL_MIN: float = 1.0  # BACKTEST_CHANGE_111: Min relative volume to confirm move is real
    MOVER_SCORE_BONUS: int = 40  # Score bonus for mover-detected entries (high conviction)
    MOVER_MAX_POSITIONS: int = 6  # Max concurrent mover positions in inf account
    MOVER_ACCOUNT: str = "inf"  # Account for mover trades
    BOUNCE_REENTRY_K_RESET_SHORT: int = 65  # K must rise above this after exit before SHORT reentry allowed
    # === SATOSHIT2024 STRATEGY — Reverse-engineered copy trader (15m mean-reversion + HTF filters) ===
    # Backtest: 100% WR on 48 symbols (8k trades), 97.8% WR on 5yr data. Derived from 180 ETH trades.
    SATOSHIT_ENABLED: bool = True
    SATOSHIT_ACCOUNTS: List[str] = field(default_factory=lambda: ["ang", "inf", "flz", "men", "fin"])
    SATOSHIT_EXIT_ENABLED: bool = False  # DISABLED 2026-04-21 — replaced by PARTIAL_PROFIT_LOCK mechanism (50% at 0.5%, SL-arm at 0.7%). Config retained for sweep compatibility.
    SATOSHIT_PROTECT_TRADES: bool = True  # ON — only Satoshit exit can close Satoshit-opened positions
    SATOSHIT_EXIT_USE_MAKER: bool = True  # Use maker order for partial close (bypasses Finandy full close)
    SATOSHIT_EXIT_PARTIAL_PCT: float = 0.70  # Close 70% of position, keep 30% as runner
    # === PARTIAL_PROFIT_LOCK — 50% close at +0.5% via webhook_url_2/maker, arm SL at first-exit price when +0.7% ===
    # 2026-04-21: Replaces SATOSHIT_PARTIAL_EXIT 70% with deterministic gain-triggered 50% lock.
    # Step 1 (gain >= PPL_GAIN_PCT): close PPL_FRAC via place_maker_order; fallback send_webhook url_variant="2".
    # Step 2 (gain >= PPL_ARM_GAIN_PCT): arm trailing stop at first-exit price.
    # Step 3 (SL hit: price back to first-exit price): close remainder via maker → webhook_url_2 fallback.
    # 2026-05-22 DISABLED: closes 50% at +0.5% with zero HTF check — kills bottom entries.
    # Already disabled for tradier. Hold until HTF flips per user mandate.
    # 2026-05-26 RE-ENABLED at 1.5%/1.75% per PPL × MIN_GAIN grid winner (ΔPS +0.0212 vs default 0.5%, monotonic 1.5>1.0>0.5).
    PARTIAL_PROFIT_LOCK_ENABLED: bool = True
    PARTIAL_PROFIT_LOCK_ACCOUNTS: List[str] = field(default_factory=lambda: ["ang", "inf", "flz", "men", "fin"])
    PARTIAL_PROFIT_LOCK_GAIN_PCT: float = 1.5          # 2026-05-26 GRID WINNER (was 0.5). 1.5% > 1.0% > 0.5% monotonic; lets winners run before harvesting.
    PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT: float = 1.75     # 2026-05-26 proportional to GAIN_PCT (+0.25%). Upgrade stop to first_exit_price when remainder hits 1.75%
    PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT: float = 0.10    # 2026-04-28 user: bumped from 0.02 → 0.10 to cover commissions (round-trip ~0.04% maker + ~0.06% slippage). Stop now fires only when remainder is net-positive after fees.
    # 2026-04-28 USER RULE: maker CLOSE orders rest at a commission-positive price.
    # When market is below the floor (LONG close) / above the floor (SHORT close), the post-only
    # GTX limit rests at the floor and waits — does not chase into a net-loss fill.
    MAKER_CLOSE_COMMISSION_FLOOR_ENABLED: bool = False  # 2026-05-12 USER MANDATE: DISABLED. Was clamping SELL limits ABOVE market (entry+buf) — orders never filled when price moved against entry. User: "only valid value is dc_low4_3m and that can never be above price". Until rewritten to use dc_low4_3m (LONG) / dc_high4_3m (SHORT), keep OFF so exits aren't blocked.
    MAKER_CLOSE_COMMISSION_FLOOR_TTL_SEC: float = 300.0  # how long to wait at floor before timing out
    # 2026-04-28 USER RULE: GUARANTEED_REENTRY needs more WT and/or K confirmation, plus a tight stop.
    GUARANTEED_REENTRY_STRICT_CONFIRMATION: bool = True  # 2026-05-22 RESTORED: K-adverse block (k_3m≥80 LONG / k_3m≤20 SHORT) + 3m+15m WT stack + ≥1 HTF. Without this, fires at K=79 with no WT check → 42.8% WR. Original strip 2026-05-10 was treating symptom (blocked reentries) instead of fixing root cause (invalid gate logic).
    GUARANTEED_REENTRY_DELTA_GATE_ENABLED: bool = False  # 2026-05-10 USER MANDATE: DELTA_REENTRY_BLOCKED_tf/_z/_4h_against gates were silently rejecting reentries via check_reentry_delta_tolerant. Default OFF — re-enable as sweep knob only.
    GUARANTEED_REENTRY_K_HIGH_BLOCK: float = 80.0      # block LONG reentry when k_3m >= this (adverse extreme)
    GUARANTEED_REENTRY_K_LOW_BLOCK: float = 20.0       # block SHORT reentry when k_3m <= this (adverse extreme)
    GUARANTEED_REENTRY_K_FAVORABLE_LOW: float = 30.0   # LONG reentry favorable: k_3m <= this (oversold)
    GUARANTEED_REENTRY_K_FAVORABLE_HIGH: float = 70.0  # SHORT reentry favorable: k_3m >= this (overbought)
    # Tight stop on GUARANTEED_REENTRY positions: cut early if reversed (controlled bleed, bypasses commission floor)
    GUARANTEED_REENTRY_TIGHT_STOP_ENABLED: bool = True
    GUARANTEED_REENTRY_TIGHT_STOP_PCT: float = 0.5     # close at gain <= -0.5% if origin was GUARANTEED_REENTRY
    GUARANTEED_REENTRY_TIGHT_STOP_MIN_AGE_S: float = 60.0   # don't fire in first 60s (let entry settle)
    GUARANTEED_REENTRY_TIGHT_STOP_MAX_AGE_S: float = 1800.0  # only relevant for first 30min after reentry
    PARTIAL_PROFIT_LOCK_FRAC: float = 0.5              # Semantic only — URL2 handles actual 50% on Finandy side
    PARTIAL_PROFIT_LOCK_USE_MAKER: bool = True
    # NOLOSS exception (sweep-only, default OFF): if all 5 WT TFs (3m/15m/1h/4h/D) flip against → allow close at loss. 2026-04-25 rapid-grid: neutral for crypto on both 4TF and 5TF — existing exit paths already cover confirmed reversals.
    NOLOSS_BYPASS_WT_5OF5_ENABLED: bool = False
    NOLOSS_BYPASS_WT_5OF5_MIN_TFS: int = 5
    # WRONG_SIDE_ABS_KILL — kill when N-of-5 WT TFs against + (optionally) divergence confirms.
    # K (stoch) requirement DEFERRED (irrelevant per user 2026-04-21). Divergence detection active.
    # If M TFs show divergence (price HH + WT LH for LONG, mirror for SHORT, over DIV_LOOKBACK_BARS),
    # reduce WT requirement from TFS_REQUIRED to TFS_REDUCED. Sweep N=[3,4,5], REDUCED=[2,3,4], age=[15,30,60,120], div_lookback=[10,20,40].
    # 2026-04-25 rapid-grid: already True in crypto baseline; disabling costs −0.007 Sharpe. Confirmed correct.
    WRONG_SIDE_ABS_KILL_ENABLED: bool = True
    WRONG_SIDE_MIN_AGE_MIN: float = 30.0
    WRONG_SIDE_WT_TFS_REQUIRED: int = 4              # 2026-04-26: 5→4. See line ~466 for full reasoning.
    WRONG_SIDE_WT_TFS_REDUCED: int = 3              # when divergence confirms, this threshold applies
    WRONG_SIDE_DIV_TFS_REQUIRED: int = 2            # min TFs with divergence to reduce threshold
    WRONG_SIDE_DIV_LOOKBACK_BARS: int = 20          # K-bars back for HH/LH comparison
    WRONG_SIDE_K_TFS_REQUIRED: int = 0              # DEFERRED — set to 0 (disabled)
    # HEDGE_ENTRY_MODE — 'LOSS_ONLY' (gain<0), 'LOSS_AND_WT' (current: gain<0 AND wt_3m+1h against),
    # 'LOSS_OR_WT' (user proposal: gain<0 OR (wt_3m AND wt_1h against)). Sweep all 3.
    HEDGE_ENTRY_MODE: str = "LOSS_AND_WT"
    SATOSHIT_MIN_VOTES: int = 3  # 3-of-5 entry indicators must agree
    SATOSHIT_SCORE_BONUS: int = 30  # Score bonus when Satoshit fires (below mover=40)
    SATOSHIT_QTY_MULT: float = 3.0  # 3x position size for satoshit entries — 100% WR, avg +5.58% gain
    SATOSHIT_LONG_RSI_MAX: float = 50.0  # 15m RSI must be below this for LONG (his median: 32)
    SATOSHIT_LONG_BB_PCTB_MAX: float = 0.50  # 1h BB%B proxy (his 15m median: 0.08)
    SATOSHIT_LONG_HA_STREAK_MAX: int = 1  # HA must be bearish/neutral (his median: -3)
    SATOSHIT_LONG_STOCH_K_MAX: float = 60.0  # 15m StochK low (his median: 22)
    SATOSHIT_LONG_MFI_MAX: float = 60.0  # 15m MFI low (his median: 28)
    SATOSHIT_SHORT_RSI_MIN: float = 55.0  # 15m RSI must be above this for SHORT (his median: 77)
    SATOSHIT_SHORT_BB_PCTB_MIN: float = 0.55  # 1h BB%B proxy (his 15m median: 1.16)
    SATOSHIT_SHORT_HA_STREAK_MIN: int = 0  # HA must be bullish (his median: 4)
    SATOSHIT_SHORT_STOCH_K_MIN: float = 50.0  # 15m StochK high (his median: 83)
    SATOSHIT_SHORT_MFI_MIN: float = 50.0  # 15m MFI high (his median: 76)
    SATOSHIT_HTF_MFI_D_MIN: float = 30.0  # Daily MFI must be positive
    SATOSHIT_HTF_RVOL_1H_MIN: float = 0.3  # 1h relative volume confirmation
    SATOSHIT_EXIT_LONG_RSI_MIN: float = 55.0  # Exit LONG when 15m RSI recovers
    SATOSHIT_EXIT_LONG_STOCH_K_MIN: float = 60.0  # Exit LONG when StochK recovers
    SATOSHIT_EXIT_SHORT_RSI_MAX: float = 42.0  # Exit SHORT when 15m RSI drops
    SATOSHIT_EXIT_SHORT_STOCH_K_MAX: float = 50.0  # Exit SHORT when StochK drops
    # === TRADER RESEARCH HARD GATES — BC_155 (2026-03-30 — 1615 trades, 70/30 OOS validated) ===
    # Source: trader_research_agent.py edge mining, 1130 IS / 485 OOS split
    # Method: quantile scan + stacked gates. Only gates with IS lift>5pp AND OOS lift>2pp AND n>=50
    # --- BC_155a: ADX_4h GATE — #1 filter. ADX_4h<=16: 75.6% IS / 69.2% OOS (+11.5pp) ---
    TR_ADX4H_GATE_ENABLED: bool = False  # BC_155a: Boycott when ADX_4h trending (bad for mean-reversion system)
    TR_ADX4H_MAX: float = 20.0  # BC_155a: Conservative (16 optimal). ADX_4h above this = heavy penalty
    TR_ADX4H_BOYCOTT_SCORE: int = -40  # BC_155a: Severe. Stacked with BB_width: 81% OOS WR
    # --- BC_155b: BB_WIDTH_4h GATE — #2 filter. BB_w<=7.94: 68.4% IS / 68.7% OOS (+11.0pp) ---
    TR_BBWIDTH4H_GATE_ENABLED: bool = False  # BC_155b: Boycott wide BBands (high vol = bad entries)
    TR_BBWIDTH4H_MAX: float = 10.0  # BC_155b: Conservative (7.94 optimal)
    TR_BBWIDTH4H_BOYCOTT_SCORE: int = -35  # BC_155b: Severe penalty when too volatile
    # --- BC_155c: CHOPPINESS_4h GATE — #3 filter. Chop>=54: 64% IS / 66.7% OOS (+8.9pp) ---
    TR_CHOP4H_GATE_ENABLED: bool = False  # BC_155c: Bonus choppy, penalty trending. Our system IS mean-reversion.
    TR_CHOP4H_MIN: float = 50.0  # BC_155c: Choppy above this = bonus
    TR_CHOP4H_BONUS: int = 15  # BC_155c: Mean-reversion sweet spot
    TR_CHOP4H_TREND_MAX: float = 38.0  # BC_155c: Strong trend below this = penalty
    TR_CHOP4H_PENALTY: int = -20  # BC_155c: Penalty in trending regime
    # --- BC_155d: LONG MFI_4h gate (d=+0.354, p<0.000001). Winners median 51.7, losers 38.9 ---
    TR_MFI4H_LONG_ENABLED: bool = True  # BC_155d: LONG boycott when MFI_4h too low (no buying pressure)
    TR_MFI4H_LONG_MIN: float = 40.0  # BC_155d: Conservative (41 was loser mean)
    TR_MFI4H_LONG_BOYCOTT_SCORE: int = -25  # BC_155d: Moderate penalty
    # --- BC_155e: SHORT DC_width_4h gate (d=-0.470, p=0.00005). Winners narrow, losers wide ---
    TR_DCWIDTH4H_SHORT_ENABLED: bool = True  # BC_155e: SHORT boycott in wide DC channel
    TR_DCWIDTH4H_SHORT_MAX: float = 15.0  # BC_155e: Conservative (winners median ~11)
    TR_DCWIDTH4H_SHORT_BOYCOTT_SCORE: int = -25  # BC_155e: Moderate penalty
    # === SQUEEZE FIRE — Improvement Framework A3 (2026-04-25, default OFF, NEEDS Tier 2 SWEEP) ===
    # NPZ fields kc_upper/mid/lower_{tf}, squeeze_{tf} (1=BB inside KC), squeeze_fire_{tf} (+1 bull / -1 bear release / 0).
    # Distinct from BC_150 (ATR-percentile sizing). Squeeze fire is the binary entry trigger BC_150 was approximating.
    # BB(20, 2.0) inside KC(20, ATR×1.5). Release on the bar BB exits KC; direction = close vs kc_mid.
    # Replaces dead BB_SQUEEZE_THRESHOLD_1H (no plausible wiring site found 20260416).
    SQUEEZE_FIRE_ENABLED: bool = False  # default OFF — sweep on top of c08_crypto_winner baseline (pool_sharpe 2.6365)
    SQUEEZE_FIRE_TFS: List[str] = field(default_factory=lambda: ["1h", "4h"])  # which TFs the fire counts on
    SQUEEZE_FIRE_SCORE_BONUS: int = 20  # entry score boost when squeeze_fire matches direction
    # === MOMENTUM FADE — ALL ACCOUNTS (2026-03-23 — 117k combos × 207 sym, 99.5%+ WR across 186 sym) ===
    MOMENTUM_FADE_ENABLED: bool = False  # ABLATION_V3_REVERT: was True (BC_113b). Deep 15m test (3yr, 215 sym): +2.36 Sharpe by removing, 94% improved. Momentum fade DESTROYS edge.
    MOMENTUM_FADE_BODY_ATR_MIN: float = 2.0  # BACKTEST_CHANGE_113b: Candle range must be >= 2x ATR to qualify as "big move". 2.0x = sweet spot (WR 96-99%).
    MOMENTUM_FADE_VOL_MIN: float = 2.0  # BACKTEST_CHANGE_113b: Volume must be >= 2x 20-bar avg. 2.0x = best general (186 sym). 3x/5x = higher WR but fewer signals.
    MOMENTUM_FADE_K_ZONE: bool = True  # BACKTEST_CHANGE_113b: Only fade when K is overbought (>60 for SHORT) or oversold (<40 for LONG). Improves Sharpe 296→426.
    MOMENTUM_FADE_SCORE_BONUS: int = 35  # BACKTEST_CHANGE_113b: Score bonus for momentum fade entries. Below mover (40) but above k-zone (25).
    # === FUNCTION-LEVEL ABLATION FLAGS (sweep_daemon uses these to test each function's value) ===
    # Default: all False (all functions enabled). Set one to True to disable that function.
    # V8 sweep overrides via V8_OVERRIDE_FILE JSON.
    ABLATION_DISABLE_ENTRY_TECHNICAL: bool = True       # ABLATION: -0.018 Sharpe delta = redundant. REENTRY alone = same performance.
    ABLATION_DISABLE_ENTRY_LEADERBOARD: bool = False    # Keep — needs live testing
    ABLATION_DISABLE_ENTRY_RANKING: bool = True         # ABLATION: 0.000 Sharpe delta = no effect (needs Redis, adds noise)
    ABLATION_DISABLE_ENTRY_REVERSAL: bool = False       # Keep — reversal entry untested
    ABLATION_DISABLE_REENTRY: bool = False              # CRITICAL: -1.9 Sharpe when removed. THE system IS reentry. NEVER disable.
    ABLATION_DISABLE_AUGMENTATION: bool = False         # -0.3 Sharpe when removed. Moderate help. Keep.
    ABLATION_DISABLE_FAST_RISER: bool = False           # Keep
    ABLATION_DISABLE_CHECK_NOLOSS: bool = False         # Keep
    ABLATION_DISABLE_HEDGE: bool = False                # Keep
    ABLATION_DISABLE_RATIO_REBALANCE: bool = False      # Keep
    ABLATION_DISABLE_QUICK_EXIT: bool = False           # Disable check_exit_candidates (WT/DC exits)
    ABLATION_DISABLE_QUICK_ENTRY: bool = False          # Disable check_entry_candidates (quick entries)
    ABLATION_DISABLE_REENTRY_ENFORCE: bool = False      # Disable reentry enforcement loop
    ABLATION_DISABLE_SPIKE_FADE_EXIT: bool = False      # Disable spike fade 1m exit monitor
    ABLATION_DISABLE_SCALP_GUARD: bool = False          # Disable monitor_strict_close_positions
    ABLATION_DISABLE_DC_BREACH_REDUCE: bool = False     # Disable DC breach reduce monitor
    ABLATION_DISABLE_AGGRESSIVE_HEDGE: bool = False     # Disable aggressive_hedge_scanner
    ABLATION_DISABLE_HIGH_GAIN_AUGMENT: bool = False    # Disable direct_high_gain_augmentation
    ABLATION_DISABLE_PERIODIC_REENTRY: bool = False     # Disable periodic_evaluate_reentry (evaluate_reentry_2)
    # === ABLATION BACKTEST RESULTS (2026-03-21 — 3507 configs × 243 sym, P1+P2+P3 OOS-validated) ===
    RSI_ENTRY_GATE_ENABLED: bool = False  # BC_154: DISABLED — 67-config ablation (48sym/4yr): stoch_gate_50 does the filtering. no_filter+stoch50 = Sharpe 0.790 (#1) vs RSI37 = 0.638
    RSI_ENTRY_MAX_LONG: float = 37.0  # BC_154: kept for reference but gate is disabled
    RSI_ENTRY_MIN_SHORT: float = 63.0  # BC_154: kept for reference but gate is disabled
    EXIT_GAIN_THRESHOLD_MIN: float = 1.0  # BACKTEST_CHANGE_112: was 0.3 (GAIN_THRESHOLD_LOW). Higher threshold = fewer whipsaw exits. IS+OOS validated.
    STOP_MAJOR_LOSS_BLOCK_ENABLED: bool = True  # BACKTEST_CHANGE_113: Block the STOP_MAJOR_LOSS reduce path entirely. #1 PnL destroyer (-125k% cumulative). L/S ratio IS the hedge.
    IMMEDIATE_WRONG_WAY_ENABLED: bool = False  # BACKTEST_CHANGE_114: was implicitly True. #2 PnL destroyer. Tight stops kill trades that recover.
    FAST_RISER_DOUBLE_ENABLED: bool = False  # BACKTEST_CHANGE_115: was True. Net negative PnL. Fast riser doubles amplify losers.
    AUGMENT_PYRAMID_ENABLED: bool = True  # Re-enabled — pyramid must always run, sizing handles risk
    # === RESEARCH-BACKED STRATEGIES (2026-03-23 — 9 agents, 100+ sources, 567k backtests, academic papers) ===
    ADX_REGIME_FILTER_ENABLED: bool = False  # BACKTEST_CHANGE_137: ADX<20 = sizing penalty + entry deduction.
    ADX_TRENDING_THRESHOLD: float = 25.0  # BACKTEST_CHANGE_137: ADX above this = trending
    ADX_RANGING_THRESHOLD: float = 20.0  # BACKTEST_CHANGE_137: ADX below this = ranging (only mean-reversion)
    ADX_TF: str = "1h"  # BACKTEST_CHANGE_137: Timeframe for ADX regime check
    # === BC_170-174: COPY TRADER NPZ GATES (50k+ trades, 110+ traders, 426 NPZ indicators) ===
    # ADDITIVE gates — only block bad entries, never create new ones. Default OFF until V8 validated.
    CT_WT_VELOCITY_GATE_ENABLED: bool = True  # BC_170: ENABLED 2026-04-08. 5yr validated: Sharpe 1.94→5.26, 100% monthly positive, keeps 67% of trades. Don't trade against 1h WT velocity.
    GOLDEN_RULE_HTF_MIN_TFS: int = 3  # 2026-05-22 RESTORED 1→3: triage set 1 with no backtest. TFs=[3m,15m,1h,4h,D]. ROLLBACK: 1 (triage).
    GOLDEN_RULE_MIN_IND: int = 5  # 2026-05-22 RESTORED 2→5: triage set 2 with no backtest. Per-TF: need this many of [WT,RSI,MFI,DC,BB] to agree. ROLLBACK: 2 (triage).
    # 2026-05-12 USER MANDATE: alternate TOTAL-VOTE-SCORE gate (multiplicative).
    # When > 0: passes if SUM across all TFs of (indicators_agreeing per TF) >= GR_TOTAL_VOTE_SCORE_MIN.
    # Range: 1 (loosest, 1 vote anywhere) ... 35 (5 TFs × 7 indicators all agreeing — tightest possible for crypto).
    # When 0: falls back to legacy MIN_TFS × MIN_IND binary gate above.
    GR_TOTAL_VOTE_SCORE_MIN: int = 0
    GR_DC_EXTENDED_LONG: float = 0.65  # DC extension threshold for LONG breakout (SHORT = 1 - this). Sweep: 0.35/0.50/0.65/0.80
    GR_BB_EXTENDED_LONG: float = 0.75  # BB pct-b threshold for LONG breakout (SHORT = 1 - this). Sweep: 0.45/0.60/0.75/0.90
    # 🚩 NEW 2026-05-18 — GR v5 BREAKOUT-CONFIRM → BOUNCE-ENTRY STATE MACHINE (SKELETON, default OFF)
    # Design doc: data/research_20260518/gr_v5_breakout_bounce_design.md
    # Vec module:  vec_paths/gr_v5_state.py (skeleton — full state arrays land next session)
    # Replaces per-TF indicator-counter weighted composite. Two-phase:
    #   IDLE → ARMED  (≥GR_V5_HTF_MIN_ALIGN of {4h,D,W} break dc_high4_prev + wt1>wt2 + vol)
    #   ARMED → FIRE  (within GR_V5_ARM_WINDOW_BARS, ≥GR_V5_LTF_MIN_ALIGN of {3m,15m,1h}
    #                  show fresh wt cross + stoch_k oversold + price within retest band)
    #   ARMED → DISARM(price moves > GR_V5_INVALIDATE_PCT against, or window elapsed)
    # NPZ availability degraded user's "1m/3m/5m/15m/1h" → crypto {3m,15m,1h}; tradier {5m,15m,1h}.
    # ALL knobs default OFF / inert. NOT wired in ez_manage.py / tradier_manage.py — backtest sweep ONLY.
    GR_V5_ENABLED: bool = False
    GR_V5_HTF_TFS: tuple = ('4h', 'D', 'W')
    GR_V5_HTF_MIN_ALIGN: int = 2
    GR_V5_BREAKOUT_REQUIRE_VOLUME: bool = True
    GR_V5_BREAKOUT_VOL_MULT: float = 1.25
    GR_V5_LTF_TFS: tuple = ('3m', '15m', '1h')
    GR_V5_LTF_MIN_ALIGN: int = 2
    GR_V5_BOUNCE_STOCH_LONG: float = 25.0
    GR_V5_BOUNCE_STOCH_SHORT: float = 75.0
    GR_V5_BOUNCE_WT_CROSS_REQUIRED: bool = True
    GR_V5_ARM_WINDOW_BARS: int = 168
    GR_V5_RETEST_BAND_PCT: float = 0.03
    GR_V5_INVALIDATE_PCT: float = 0.02
    # 🚩 NEW 2026-05-12 — GR_HTF DIRECT ENTRY/EXIT SIGNAL (user mandate)
    # Score = n_tfs_aligned × GOLDEN_RULE_MIN_IND (computed by golden_rule_htf.score_entry_htf)
    # ENTRY: flat position + score >= SCORE_MIN → OPEN at START_POSITION_SIZE.
    #        If score >= DOUBLE_SCORE: size × 2.
    # EXIT:  open position + opposite-direction score >= EXIT_SCORE → CLOSE (bypasses NOLOSS gate).
    # ROLLBACK entry: set GR_HTF_DIRECT_ENTRY_ENABLED=False (or raise SCORE_MIN to 1000.0).
    # ROLLBACK exit : set GR_HTF_DIRECT_EXIT_ENABLED=False (or raise EXIT_SCORE to 1000.0).
    GR_HTF_DIRECT_ENTRY_ENABLED: bool = True       # 🚩 Master entry switch. ROLLBACK: False
    GR_HTF_DIRECT_ENTRY_SCORE_MIN: float = 23.0    # 🚩 2026-05-17 USER: rescaled 12→23. New max = 6 TFs × 11 ind = 66 (was 5×7=35). Same selectivity ratio 34.3%.
    GR_HTF_DIRECT_ENTRY_DOUBLE_SCORE: float = 34.0 # 🚩 2026-05-17 USER: rescaled 18→34 (same ratio 51.4%).
    GR_HTF_DIRECT_EXIT_ENABLED: bool = True         # 🚩 Master exit switch. ROLLBACK: False
    GR_HTF_DIRECT_EXIT_SCORE: float = 15.0   # USER 2026-05-30: was unreachable 29 (max 25). 15 = 3 entry TFs all against → fires.          # 🚩 2026-05-17 USER: rescaled 15.5→29 (same ratio 44.3%). PRIOR 15.5 (for 35-max).
    CT_WT_VELOCITY_1H_MIN: float = 9.0  # 2026-04-20 sweep: vel=9+rally=30 → Sharpe 2.598 (target met). Was 8.0.
    DD_BOUNCE_ENABLED: bool = False  # 2026-04-20: double-down on wt_D or wt_4h bounce while losing. OFF until sweep validates.
    DD_BOUNCE_WT_D_ENABLED: bool = True  # if DD_BOUNCE_ENABLED: use wt_D trigger
    DD_BOUNCE_WT_4H_ENABLED: bool = True  # if DD_BOUNCE_ENABLED: use wt_4h trigger
    DD_BOUNCE_REQUIRE_HIGHER_WT: bool = True  # bounce WT must be > previous bounce WT (lower for SHORT)
    DD_BOUNCE_REQUIRE_HIGHER_PRICE: bool = True  # bounce price must be > previous aug price (higher low for LONG)
    DD_BOUNCE_COOLDOWN_HOURS: float = 4.0  # min hours between DD augments per symbol
    DD_BOUNCE_DD_STOP_ENABLED: bool = True  # cut extra DD leg if price drops below aug entry price
    CT_15M_MOMENTUM_GATE_ENABLED: bool = False  # BC_171: DEAD. ABLATION 2026-04-16: 0.0000 ΔSharpe on 11sym 4yr crypto + 12sym tradier. OFF forever.
    CT_STOCH_K_15M_LONG_MIN: float = 45.0  # BC_171: (disabled)
    CT_STOCH_K_15M_SHORT_MAX: float = 55.0  # BC_171: (disabled)
    CT_MFI_15M_LONG_MIN: float = 45.0  # BC_171: (disabled)
    CT_MFI_15M_SHORT_MAX: float = 55.0  # BC_171: (disabled)
    CT_DC_CROSSOVER_SKIP_ENABLED: bool = False  # DISABLED LIVE TEST 2026-08-17 USER: TRB winning trades closed via 9386 — must be False to give positive delta per_sym
    CT_CHOP_4H_GATE_ENABLED: bool = False  # BC_173: DEAD. ABLATION 2026-04-16: 0.0000 ΔSharpe (no choppiness_4h in NPZ). OFF forever.
    CT_CHOP_4H_MAX: float = 50.0  # BC_173: max choppiness_4h
    CT_VOLUME_SURGE_GATE_ENABLED: bool = False  # BC_174: DEAD. ABLATION 2026-04-16: 0.0000 ΔSharpe on 11sym+12sym. OFF forever.
    CT_REL_VOL_MIN: float = 1.3  # BC_174: min relative_volume for entry
    EMA_PULLBACK_ENABLED: bool = False  # BACKTEST_CHANGE_128: EMA pullback + StochRSI oversold in trend. Validated by academia + copy traders.
    EMA_PULLBACK_SCORE_BONUS: int = 35  # BACKTEST_CHANGE_128: Highest score — matches "retest-and-launch" core edge
    EMA_PULLBACK_TF: str = "15m"  # BACKTEST_CHANGE_128: TF for pullback detection
    FG_SIZING_ENABLED: bool = False  # BACKTEST_CHANGE_141: F&G sizing multiplier (1,240% vs 680% B&H). Fear=bigger, Greed=smaller.
    FG_FEAR_THRESHOLD: int = 25  # BACKTEST_CHANGE_141: F&G below this = extreme fear → increase size
    FG_GREED_THRESHOLD: int = 75  # BACKTEST_CHANGE_141: F&G above this = extreme greed → decrease size
    RSI_MOMENTUM_MODE: bool = False  # BACKTEST_CHANGE_138: Toggle RSI gate to momentum (>50=buy). Crypto-specific.
    SIMPLE_TP_EXIT_ENABLED: bool = False  # BACKTEST_CHANGE_139: Simple fixed TP% exit. 567k backtests: simple > complex trailing.
    SIMPLE_TP_PCT: float = 0.50  # BACKTEST_CHANGE_139: Fixed TP percentage
    BB_RSI_STOCH_SCALP_ENABLED: bool = False  # BACKTEST_CHANGE_134: BB+RSI+Stoch triple confirmation scalp (73-77% WR)
    BB_RSI_STOCH_SCALP_SCORE: int = 25  # BACKTEST_CHANGE_134: Score bonus for triple confirmation
    MACD_ZERO_CROSS_ENABLED: bool = False  # BACKTEST_CHANGE_131: MACD below-zero crossover + SMA200 trend. Confirmation only.
    MACD_ZERO_CROSS_SCORE: int = 15  # BACKTEST_CHANGE_131: Conservative score (MACD 20% WR on crypto standalone)
    MACD_ZERO_CROSS_TF: str = "1h"  # BACKTEST_CHANGE_131: TF for MACD check
    RSI2_MEAN_REVERSION_ENABLED: bool = False  # BACKTEST_CHANGE_126: RSI(2) ultra-oversold (91% WR daily, tiny gains)
    RSI2_THRESHOLD_LONG: float = 15.0  # BACKTEST_CHANGE_126: RSI(2) below this = LONG signal
    RSI2_THRESHOLD_SHORT: float = 85.0  # BACKTEST_CHANGE_126: RSI(2) above this = SHORT signal
    RSI2_SCORE_BONUS: int = 20  # BACKTEST_CHANGE_126: Score bonus for RSI(2) extreme
    HA_WICK_QUALITY_ENABLED: bool = False  # BACKTEST_CHANGE_144: HA streak quality scoring (62% WR with EMA filter)
    HA_WICK_QUALITY_SCORE: int = 15  # BACKTEST_CHANGE_144: Score bonus for strong HA streak
    HA_WICK_QUALITY_TF: str = "1h"  # BACKTEST_CHANGE_144: TF for HA streak check
    BB_BREAKOUT_ENABLED: bool = False  # BACKTEST_CHANGE_132: BB breakout + SMA200 (trending regime only)
    BB_BREAKOUT_SCORE: int = 20  # BACKTEST_CHANGE_132: Score bonus for breakout
    BB_BREAKOUT_TF: str = "1h"  # BACKTEST_CHANGE_132: TF for BB breakout
    # 4h BB breakout ladder: 25% at breakout, 50% at dc_basis_4h, 25% at next WT1h cross.
    BB4H_BREAKOUT_LADDER_ENABLED: bool = True
    BB4H_BREAKOUT_LADDER_TARGET_USD: float = 2000.0
    BB4H_BREAKOUT_LADDER_BREAKOUT_PCT: float = 0.25
    BB4H_BREAKOUT_LADDER_BASIS_PCT: float = 0.50
    BB4H_BREAKOUT_LADDER_WT_CROSS_PCT: float = 0.25  # final tranche at next bullish WT 1h cross
    BB4H_BREAKOUT_LADDER_STOCK_MAX_NOTIONAL_USD: float = 2000.0
    BB4H_BREAKOUT_LADDER_MAX_STOCK_SHARES: int = 1
    TRIPLE_CONF_ENABLED: bool = False  # BACKTEST_CHANGE_125: MACD+RSI+Stoch triple confirmation entry
    TRIPLE_CONF_RSI_LONG: float = 30.0  # BACKTEST_CHANGE_125: RSI(14) below this for LONG
    TRIPLE_CONF_RSI_SHORT: float = 70.0  # BACKTEST_CHANGE_125: RSI(14) above this for SHORT
    TRIPLE_CONF_STOCH_LONG: float = 20.0  # BACKTEST_CHANGE_125: Stoch K below this for LONG
    TRIPLE_CONF_STOCH_SHORT: float = 80.0  # BACKTEST_CHANGE_125: Stoch K above this for SHORT
    TRIPLE_CONF_SCORE: int = 30  # BACKTEST_CHANGE_125: Score bonus when all 3 align
    TRIPLE_CONF_TF: str = "1h"  # BACKTEST_CHANGE_125: TF for triple confirmation
    # === MARKET REGIME DETECTION (ez_regime.py) — OFF by default until V5 proves it ===
    REGIME_DETECTION_ENABLED: bool = False  # Master switch — OFF until backtest-proven
    REGIME_ENTER_TRENDING_THRESHOLD: float = 30.0  # Score > 30 to enter TRENDING_UP (< -30 for DOWN)
    REGIME_EXIT_TRENDING_THRESHOLD: float = 15.0  # Score < 15 to exit back to RANGING (hysteresis)
    REGIME_MIN_DWELL_BARS: int = 16  # 4h at 15m — minimum bars before regime switch
    REGIME_BTC_MARKET_WEIGHT: float = 0.5  # BTC influence on market-wide regime
    # Regime: RANGING (mean reversion, fast exits, small positions)
    REGIME_RANGING_NOLOSS_MIN: float = 0.05  # Take ANY profit in ranging
    REGIME_RANGING_EXIT_GAIN_MIN: float = 0.15  # Exit at 0.15% gain
    REGIME_RANGING_MIN_HOLD_BARS: int = 8  # 2h at 15m — fast turnover
    REGIME_RANGING_WT_REDUCE_FRAC_LOW: float = 0.40  # 0.3-0.5% gain → reduce 40%
    REGIME_RANGING_WT_REDUCE_FRAC_MED: float = 0.60  # 0.5-1.0% gain → reduce 60%
    REGIME_RANGING_WT_EXIT_VEL: float = -3.0  # Exit on lighter reversal
    REGIME_RANGING_K_ZONE_BONUS: int = 40  # Mean reversion K-zone bonus (was 25)
    REGIME_RANGING_DC_BREAKOUT_SCORE: int = 0  # DC breakout disabled in ranging
    REGIME_RANGING_POSITION_SIZE_MULT: float = 0.5  # Half-size, more slots
    REGIME_RANGING_SLOT_RESERVE_PCT: float = 0.60  # Reserve 60% slots for new entries
    REGIME_RANGING_REENTRY_SIZE_MULT: float = 1.0  # Standard reentry
    REGIME_RANGING_STALE_HOURS: float = 48.0  # Evict breakeven positions after 48h
    REGIME_RANGING_STALE_MIN_PROFIT: float = 0.02  # Must be slightly profitable to evict
    # Regime: TRENDING (ride trends, wide exits, large positions)
    REGIME_TRENDING_NOLOSS_MIN: float = 0.50  # Let winners run in trends
    REGIME_TRENDING_EXIT_GAIN_MIN: float = 2.0  # Only exit at 2%+ gain
    REGIME_TRENDING_MIN_HOLD_BARS: int = 48  # 12h at 15m — hold longer
    REGIME_TRENDING_WT_REDUCE_FRAC_LOW: float = 0.10  # Trim gently
    REGIME_TRENDING_WT_REDUCE_FRAC_MED: float = 0.15  # Still gentle
    REGIME_TRENDING_WT_EXIT_VEL: float = -12.0  # Only exit on strong reversal
    REGIME_TRENDING_K_ZONE_BONUS: int = 15  # K-zone less important
    REGIME_TRENDING_DC_BREAKOUT_SCORE: int = 30  # DC breakout valuable in trends
    REGIME_TRENDING_POSITION_SIZE_MULT: float = 1.5  # Full-size, fewer trades
    REGIME_TRENDING_SLOT_RESERVE_PCT: float = 0.40  # Reserve 40% slots
    REGIME_TRENDING_REENTRY_SIZE_MULT: float = 2.0  # Aggressive reentry in trends
    REGIME_TRENDING_K_RESET_THRESHOLD: float = 40.0  # Shallower pullback K reset
    EMA200_STOCHRSI_ENABLED: bool = False  # BACKTEST_CHANGE_127: EMA200 trend + StochRSI reversal + candle body
    EMA200_STOCHRSI_K_LONG: float = 20.0  # BACKTEST_CHANGE_127: Stoch K below this for LONG
    EMA200_STOCHRSI_K_SHORT: float = 80.0  # BACKTEST_CHANGE_127: Stoch K above this for SHORT
    EMA200_STOCHRSI_BODY_MULT: float = 1.05  # BACKTEST_CHANGE_127: Candle body 5%+ larger than prev
    EMA200_STOCHRSI_SCORE: int = 25  # BACKTEST_CHANGE_127: Score bonus
    EMA200_STOCHRSI_TF: str = "1h"  # BACKTEST_CHANGE_127: TF for check
    RSI_MACD_EMA_ENABLED: bool = False  # BACKTEST_CHANGE_129: RSI+MACD+EMA9 cross combined entry
    RSI_MACD_EMA_RSI_LONG: float = 35.0  # BACKTEST_CHANGE_129: Relaxed RSI — 35 not 30
    RSI_MACD_EMA_RSI_SHORT: float = 65.0  # BACKTEST_CHANGE_129: Relaxed RSI — 65 not 70
    RSI_MACD_EMA_SCORE: int = 25  # BACKTEST_CHANGE_129: Score bonus
    RSI_MACD_EMA_TF: str = "1h"  # BACKTEST_CHANGE_129: TF
    ATR_ADAPTIVE_STOP_ENABLED: bool = False  # BACKTEST_CHANGE_130: ATR-based sizing reduction (not stop — STRICT_NO_LOSS)
    ATR_ADAPTIVE_STOP_MULT: float = 2.0  # BACKTEST_CHANGE_130: ATR(14) x this = risk distance
    ATR_ADAPTIVE_STOP_TF: str = "1h"  # BACKTEST_CHANGE_130: TF for ATR
    DC_BREAKOUT_ENTRY_ENABLED: bool = True  # BACKTEST_CHANGE_133: Donchian breakout entry (trend-following)
    DC_BREAKOUT_SCORE: int = 15  # BACKTEST_CHANGE_133: Conservative (30% WR in ranging)
    DC_BREAKOUT_TF: str = "1h"  # BACKTEST_CHANGE_133: TF for breakout
    DC_BREAKOUT_ALLOW_15M: bool = False  # Allow 15m DC breakout entries. Default False — 15m DC had 37.7% WR. User mandate 2026-05-22: 1h+ only unless backtest proves otherwise.
    DC_BREAKOUT_ALLOW_3M: bool = False  # Allow 3m DC breakout entries. Default False — 3m DC had ~37.7% WR, fires too frequently. Requires DC_BREAKOUT_ALLOW_15M=True.
    ATR_ADAPTIVE_SIZING_ENABLED: bool = False  # BACKTEST_CHANGE_135: Inverse ATR sizing (high vol = smaller)
    ATR_ADAPTIVE_SIZING_TARGET_PCT: float = 2.0  # BACKTEST_CHANGE_135: Target ATR%. Size=1x at this ATR.
    MACD_EXIT_ENABLED: bool = False  # BACKTEST_CHANGE_136: MACD cross-back exit for profitable positions
    MACD_EXIT_MIN_GAIN: float = 0.3  # BACKTEST_CHANGE_136: Min gain% before MACD exit allowed
    MACD_EXIT_TF: str = "15m"  # BACKTEST_CHANGE_136: TF for MACD exit signal
    LS_RATIO_CONTRARIAN_ENABLED: bool = False  # BACKTEST_CHANGE_142: L/S ratio contrarian filter
    LS_RATIO_EXTREME_THRESHOLD: float = 70.0  # BACKTEST_CHANGE_142: L/S ratio above this = suppress that side
    LS_RATIO_PENALTY: int = 15  # BACKTEST_CHANGE_142: Score penalty for crowded side
    OI_DIVERGENCE_ENABLED: bool = False  # BACKTEST_CHANGE_143: OI divergence confirmation
    OI_DIVERGENCE_PENALTY: int = 10  # BACKTEST_CHANGE_143: Score penalty for OI divergence
    REGIME_ADAPTIVE_ENABLED: bool = False  # Regime-adaptive strategy selection (ADX+CHOP)
    # === ADAPTIVE REGIME (adaptive_regime.py) — per-symbol live config optimization ===
    ADAPTIVE_REGIME_ENABLED: bool = True  # Master switch for regime daemon
    ADAPTIVE_REGIME_PAPER: bool = True  # Paper mode: log decisions, don't override real configs
    ADAPTIVE_REGIME_DC_BREAKOUT_THRESHOLD: float = 0.90  # dc_position > this = breakout UP
    ADAPTIVE_REGIME_DC_BREAKDOWN_THRESHOLD: float = 0.10  # dc_position < this = breakout DOWN
    ADAPTIVE_REGIME_HEAT_TRIGGER: float = 30.0  # Re-optimize when heat score > this
    ADAPTIVE_REGIME_NPZ_CACHE_HOURS: float = 4.0  # Re-fetch NPZ from server every N hours
    ADAPTIVE_REGIME_DECAY_HALFLIFE_H: float = 24.0  # Exponential weight half-life (hours)
    ADAPTIVE_REGIME_LOOKBACK_DAYS: int = 7  # Rolling backtest window
    ADAPTIVE_REGIME_MIN_SIGNALS: int = 5  # Min weighted signals to trust optimizer (paper: 5, live: 10+)
    ADAPTIVE_REGIME_SHARPE_FLOOR: float = 0.0  # Paper phase: observe all. Raise to 0.5+ for live.
    CHOP_RANGING_THRESHOLD: float = 61.8  # Choppiness above this = ranging
    CHOP_TRENDING_THRESHOLD: float = 38.2  # Choppiness below this = trending
    # === STRATEGIC BOUNCE AVERAGING (SBA) — BACKTEST_CHANGE_145 ===
    # Validated by Finandy data: 41% of cost-avg trades break even, median loss only $9 vs $3900 non-CA
    SBA_ENABLED: bool = False  # BACKTEST_CHANGE_145: Add to underwater positions at confirmed bounce
    SBA_MIN_LOSS_PCT: float = -2.0  # BACKTEST_CHANGE_145: Only trigger at -2% or worse (backtest: -2% optimal)
    SBA_MAX_LOSS_PCT: float = -15.0  # BACKTEST_CHANGE_145: Stop averaging beyond -15% (backtest: -15% and -10% tied)
    SBA_SIZE_FRACTION: float = 0.40  # BACKTEST_CHANGE_145: 40% of START_POSITION_SIZE per add (backtest: 0.40 > 0.25/0.35)
    SBA_MAX_ADDS: int = 2  # BACKTEST_CHANGE_145: Max recovery adds per position (backtest: 2 > 1)
    SBA_MAX_TOTAL_MULT: float = 20.0  # 2026-05-21 USER MANDATE: position must be able to compound to 20x start_position_size when price keeps moving favorably. Was 2.5. ROLLBACK: 2.5 restores backtest-145 cap.
    SBA_MIN_SCORE: float = 3.5  # BACKTEST_CHANGE_145: Min bounce score to trigger (backtest: 3.5 > 4.0/4.5, 66% SBA WR)
    SBA_COOLDOWN_POSITION_S: int = 3375  # BACKTEST_CHANGE_145: ~56min between adds (backtest: 15 bars × 15m = 3375s optimal)
    SBA_COOLDOWN_GLOBAL_S: int = 300  # BACKTEST_CHANGE_145: 5min between ANY SBA add (crash guard)
    SBA_MAX_CONCURRENT: int = 3  # BACKTEST_CHANGE_145: Max positions receiving SBA at once
    SBA_ADX_MAX: float = 25.0  # BACKTEST_CHANGE_145: ADX < 25 (backtest: 25 > 20, Sharpe +0.232 vs +0.140)
    SBA_ADX_TF: str = "1h"  # BACKTEST_CHANGE_145: TF for ADX regime check
    MARKET_QUALITY_SCORE_ENABLED: bool = False  # BACKTEST_CHANGE_146: Market quality entry filter (backtest: Sharpe +430%, 60% symbols improved)

    MAX_HEDGE_BALANCE_VALUE_USD: float = (
        500.0  # Maximum USD value for hedge balance adjustments
    )
    MAX_HEDGE_BALANCE_MULTIPLIER: float = (
        1.8  # Maximum hedge size multiplier (1.5x = hedge can be 1.5x regular position)
    )
    HEDGE_BALANCE_COOLDOWN_SECONDS: float = (
        180.0  # Cooldown between hedge balance adjustments (5 minutes)
    )

    # Loss Mitigator Settings (ez_loss_mitigator.py — ang account gain guard)
    MITIGATOR_ENABLED: bool = False  # DISABLED: 8 triggers kill winners between 0.03-2.5%. Let winners run.
    MITIGATOR_ACCOUNT: list = field(default_factory=lambda: [])
    MITIGATOR_SCAN_INTERVAL: float = 3.0
    MITIGATOR_TIER1_PEAK: float = 0.15  # Peak gain must reach this before tier 1 arms
    MITIGATOR_TIER1_DROP: float = 0.08  # Reduce 25% when gain drops to this
    MITIGATOR_TIER1_REDUCE_PCT: float = 0.25
    MITIGATOR_TIER2_DROP: float = 0.02  # Reduce 50% of remaining at breakeven
    MITIGATOR_TIER2_REDUCE_PCT: float = 0.50
    MITIGATOR_TIER3_DROP: float = -0.05  # Full close — tiny loss better than big loss
    MITIGATOR_AUGMENT_THRESHOLD: float = 0.30  # Augment winners above this gain
    MITIGATOR_AUGMENT_CONSECUTIVE: int = 3  # Must rise for 3+ scans
    MITIGATOR_COOLDOWN: float = 15.0  # Seconds between actions per position
    MITIGATOR_REENTRY_COOLDOWN: float = 180.0  # 3 min before re-entry
    MITIGATOR_REENTRY_PRICE_PCT: float = 0.15  # Favorable price move for re-entry
    STOP_LOSS_THRESHOLD = 999.0  # BACKTEST_CHANGE_17: was 1.0. Dead code under STRICT_NO_LOSS — disabled
    GAIN_THRESHOLD_LOW = 1.0  # BACKTEST_CHANGE_112: was 0.15 (was 0.50). Higher = fewer whipsaw exits. OOS-validated at 1.0%
    CHECK_INTERVAL = 3.0  # Check every 4 seconds
    ENABLE_FAST_RISER_REDUCE: bool = (
        True  # Enable fast riser logic: DOUBLE when k_3m < 70 and quick jump (momentum), REDUCE when k_3m > 70 and overbought (take profit)
    )
    LEADERBOARD_FILTER: bool = True
    COUNTER_TREND_CRYPTO: list = field(
        default_factory=lambda: [
            "XAUUSDT",
            "PAXGUSDT",
            "XAGUSDT",
            "BTCDOMUSDT",
            "SKYUSDT",
            #"TRXUSDT",  # BACKTEST_CHANGE_48: worst real symbol in backtest (-63 Sharpe)
        ]
    )  # Go up when market goes down — invert ratio_mult
    BLACKLIST_SYMBOLS: list = field(default_factory=lambda: [])  # REVERTED: Change #46+50 removed. All symbols in symbols.json must remain tradeable

    VALIDATE_REFRESH: int = 2  # seconds
    VERBOSE: bool = True
    VERBOSE2: bool = False
    VERBOSE_STOPS: bool = False
    VERBOSE_TIMER: bool = False
    VERBOSE_FETCH_LOGGING: bool = False
    DEBUG: bool = False
    REDUCTION_COOLDOWN_SECONDS = 90.0
    AUGMENTATION_COOLDOWN_SECONDS = 540.0

    PERSIST = 72.0  # hours to stay in tradeable_keys after deletion (ang — 3 days)
    PERSIST_INF: float = 4.0   # hours: inf extends for minutes-hours only (not days)
    PERSIST_FLZ: float = 0.0   # 0 = no separate persistence needed; cleanup_positions loop adds both sides when symbol not in winners/losers
    PERSIST_MEN: float = 24.0  # hours: retention for men keys after falling out of classification
    PERSIST_FIN: float = 24.0  # hours: retention for fin keys after falling out of classification

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

    # ═══════════════════════════════════════════════════════════════════════
    # 2026-04-18 INDICATOR-AUDIT EXPERIMENTAL SWITCHES (ALL DEFAULT OFF)
    # Wired into ez_positions_quick.rate() + evaluate_reentry_epq behind these
    # flags. Parameters are editable; lock the values once sweep-validated.
    # Source: analysis/indicator_audit_2026-04-18.xlsx (Sheet 5 — Suggestions)
    # ═══════════════════════════════════════════════════════════════════════
    # R-G1: cross-symbol sentiment rank boycott (only LONG top-N / SHORT bottom-N)
    SENTIMENT_TOP_N_GATE_ENABLED: bool = False
    SENTIMENT_TOP_N: int = 20                    # universe size threshold; sweep [10,20,30,40]
    # R-G2: multi-TF WT velocity alignment gate — VALIDATED 2026-04-19 (+20% Sharpe at vel_min=2)
    WT_MTF_VEL_GATE_ENABLED: bool = True
    WT_MTF_VEL_MIN: int = 2                      # TF count in [2,3,4]; sweet spot=2 (vel_min=3 is loser)
    # R-G3: chop boycott via wt_cross_count_{dir}_{tf} in last 50 bars
    WT_CHOP_GATE_ENABLED: bool = False
    WT_CHOP_MAX: int = 8                         # per-TF cross count >= this → boycott; sweep [6,8,10,12]
    # R-Z1: wire rankings.json order_multiplier into sizing chain
    RANKING_MULT_ENABLED: bool = False
    RANKING_MULT_MIN: float = 0.3
    RANKING_MULT_MAX: float = 2.5
    # R-Z4: continuous crash_mult gradient scaled by 0sentiment_strength
    CRASH_MULT_GRADIENT_ENABLED: bool = False
    CRASH_MULT_GRADIENT_MAX: float = 2.5         # upper clamp
    # RE-1: reentry cross-freshness gate (wt_cross_bars_ago_{tf} < N)
    REENTRY_CROSS_FRESHNESS_ENABLED: bool = False
    REENTRY_CROSS_MAX_BARS_AGO: int = 5           # sweep [3,5,8,12]
    # WT_4H_VEL_EXIT switches — 2026-04-19: add kill switch + fix broken SHORT condition
    # OLD SHORT: _wt1_4h > _wt2_4h (any bullish cross → exit short — no velocity threshold, too aggressive)
    # NEW SHORT: wt_velocity_4h > WT_4H_VEL_EXIT_SHORT_VEL_MIN (symmetric with LONG side)
    # Real baseline showed this caused 409 closes at 14% WR (-4.69% total) with BTCUSDC
    WT_4H_VEL_EXIT_ENABLED: bool = True
    WT_4H_VEL_EXIT_LONG_VEL_MIN: float = -2.0    # LONG exits when vel_4h < this (downward momentum)
    WT_4H_VEL_EXIT_SHORT_VEL_MIN: float = 2.0    # SHORT exits when vel_4h > this (upward momentum)
    # 2026-04-28 USER RULE: WT_4H_VEL_EXIT must require profit AND extreme stoch K
    # Old gate fired at -9.75% on API3USDT_SHORT, looped MANDATORY_REENTRY → -374% bleed across 89 closes today.
    WT_4H_VEL_EXIT_REQUIRE_PROFIT: bool = True       # only fire when current_gain >= 0
    WT_4H_VEL_EXIT_REQUIRE_K_EXTREME: bool = True    # only fire when K is at extreme against position direction
    WT_4H_VEL_EXIT_K_EXTREME_HIGH: float = 80.0      # LONG exit needs k_3m>=80 OR k_15m>=80 (overbought top)
    WT_4H_VEL_EXIT_K_EXTREME_LOW: float = 20.0       # SHORT exit needs k_3m<=20 OR k_15m<=20 (oversold bottom)
    # 2026-04-28 USER RULE: MANDATORY_REENTRY must require WT agreement AND K not at extreme
    # Old gate fired with WT=1/3 OR WT=0 (price-cross only) → bought tops, sold bottoms
    MANDATORY_REENTRY_MIN_WT_AGREE: int = 2          # need at least 2/3 WT TFs (3m/15m/1h) agreeing
    MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME: bool = True
    MANDATORY_REENTRY_K_HIGH_BLOCK: float = 80.0     # block LONG reentry when k_3m >= this (top)
    MANDATORY_REENTRY_K_LOW_BLOCK: float = 20.0      # block SHORT reentry when k_3m <= this (bottom)
    MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS: bool = False  # banned by user — WT must agree, no WT=0 exception

    # ════════════════════════════════════════════════════════════════════════
    # 2026-05-08 OBLIGATORY_REENTRY (user mandate: ANY exit MUST reenter on:
    #   Tier 1: bounce above ema_50_<TF> + 3-of-5 HTF aligned (3m,15m,1h,4h,D)  → 1.5x size
    #   Tier 2: 3m alignment + ≥1/5 HTF aligned (no SMA check)                  → 1.0x size
    #   Tier 3: pass exit price + break dc_high4_3m (LONG) / dc_low4_3m (SHORT) → 1.0x size
    # K_15m extreme (>95 LONG, <5 SHORT) = SIZE REDUCTION (×0.5) NOT BLOCK.
    # SHORT mirror has independent flags so it can be sweep-tuned separately.
    # ════════════════════════════════════════════════════════════════════════
    OBLIGATORY_REENTRY_ENABLED: bool = True
    OBLIGATORY_REENTRY_LONG_ENABLED: bool = True
    OBLIGATORY_REENTRY_SHORT_ENABLED: bool = True
    OBLIGATORY_REENTRY_SMA_TF: str = "15m"           # 3m / 15m / 1h
    OBLIGATORY_REENTRY_SMA_FIELD: str = "ema_50"     # ema_50 / sma_200 (sma_50 not in indicators)
    OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED: int = 3   # 3-of-5 (3m,15m,1h,4h,D)
    OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED: int = 1   # 1-of-5 minimum
    OBLIGATORY_REENTRY_K15_HIGH_BLOCK: float = 95.0  # LONG: k_15m >= this → size REDUCED
    OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC: float = 0.5
    OBLIGATORY_REENTRY_SMA_BOUNCE_SIZE_MULT: float = 1.5
    OBLIGATORY_REENTRY_DEFAULT_SIZE_MULT: float = 1.0
    OBLIGATORY_REENTRY_SCORE_TIER1: int = 40
    OBLIGATORY_REENTRY_SCORE_TIER2: int = 30
    OBLIGATORY_REENTRY_SCORE_TIER3: int = 30
    # SHORT-side independent (mirror) — sweep-tunable separately
    OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK: float = 5.0
    OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC: float = 0.5
    OBLIGATORY_REENTRY_SHORT_SMA_BOUNCE_SIZE_MULT: float = 1.5
    # 2026-04-28 USER RULE: BREAKEVEN_GAIN_EROSION may not close at a loss — only fire when in profit zone
    BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT: bool = True
    # 2026-05-21 USER MANDATE: ZECUSDC flz lost +17% trend ride to 5+ closes at gain 0.11/0.15/0.20/0.24/0.48% age 15-20m.
    # Combined with MTF_ATR_TRAIL=2x protection (line 1363), this noise-zone scalp is now strictly harmful.
    # Hard kill via switch + window raised to 50.0–50.5% (effectively unreachable). ROLLBACK: ENABLED=True + MIN_GAIN=0.0.
    BREAKEVEN_GAIN_EROSION_ENABLED: bool = False
    BREAKEVEN_GAIN_EROSION_MIN_GAIN: float = 50.0     # gate fires only when MIN_GAIN <= current_gain < MIN_GAIN+0.5
    # 2026-05-21 USER MANDATE: ZECUSDC custom flz config.
    #   ZEC_FLZ_LONG_SIZE_MULT: applied to base_usdc_size in calculate_dynamic_quantity AND to
    #     augment_qty in WT_3M / WINNER_SIZE / forced sites when account=='flz' & sym=='ZECUSDC' & is_long.
    #     ROLLBACK: set to 1.0.
    #   ZEC_SUPERVISOR_*: zec_supervisor_agent.py (Sonnet 4.6) daemon authority.
    #     AUTONOMOUS_CLOSE_PER_HOUR_MAX caps autonomous closes; OVERRIDE_PATCH unlimited.
    #     Bypass reasons AGENT_AUTONOMOUS_CLOSE / ZEC_SUPERVISOR_CLOSE in UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS.
    ZEC_FLZ_LONG_SIZE_MULT: float = 5.0
    ZEC_SUPERVISOR_ENABLED: bool = True
    ZEC_SUPERVISOR_POLL_INTERVAL_SEC: int = 300
    ZEC_SUPERVISOR_AUTONOMOUS_CLOSE_PER_HOUR_MAX: int = 1
    ZEC_SUPERVISOR_MODEL: str = "claude-sonnet-4-6"
    ZEC_SUPERVISOR_HISTORY_LOOKBACK_MIN: int = 30
    # 2026-04-28 USER RULE: HEDGE_MAX_AGE_KILL may not close hedge at a loss
    HEDGE_MAX_AGE_KILL_REQUIRE_PROFIT: bool = True
    # 2026-04-28 USER RULE: HEDGE_CLOSE_SCALP Rule C requires combined (hedge+orig) >= 0 before firing
    HEDGE_SCALP_C_REQUIRE_COMBINED_NONNEG: bool = True
    # 2026-04-28 USER RULE: RED_ZONE_GATE OB-fallback when ez_orderbook key missing for symbol
    # Uses 1m K direction + k_15m extreme + 1h/4h LH/HL pattern as substitute for L2 walls
    RED_ZONE_GATE_FALLBACK_ENABLED: bool = True
    RED_ZONE_FALLBACK_K15_HIGH: float = 80.0   # LONG block: k_15m >= this (overbought, top warning)
    RED_ZONE_FALLBACK_K15_LOW: float = 20.0    # SHORT block: k_15m <= this (oversold, bottom warning)
    # DC_HOPELESS_EXIT — close if entry_price is now outside the dc_4h channel entirely
    # LONG: entry_price > dc_high_4h → bought above channel ceiling, channel moved below us
    # SHORT: entry_price < dc_low_4h → sold below channel floor, channel moved above us
    # Exits at market structure break, not at %. Technical exit, not stop-loss.
    DC_HOPELESS_EXIT_ENABLED: bool = True
    DC_HOPELESS_EXIT_MIN_AGE_S: int = 900         # only fire after 15min (avoid newborn noise)
    # ═══════════════════════════════════════════════════════════════════════
    # 2026-04-19 FULL INDICATOR WIRE-IN — 51 fields confirmed in NPZ + live
    # All exits default ON (proven exit signals); all entry gates default ON
    # ═══════════════════════════════════════════════════════════════════════
    # EXIT: EXHAUST — close when 4h momentum exhausted in direction against position
    # Requires 4h EXHAUST confirmed by 1h or 15m (prevents premature exit on single TF)
    WT_EXHAUST_EXIT_ENABLED: bool = True
    WT_EXHAUST_EXIT_REQUIRE_GAIN: bool = False     # True = only exit on EXHAUST if gain > 0
    WT_EXHAUST_EXIT_MIN_GAIN_PCT: float = 0.5      # Only fire WT_EXHAUST after position peaked ≥ this. Prevents firing at tiny gains (0.1%) in backtest where 4h state repeats every 15m bar — same as R2_PEAK_MIN_PCT so the two gates don't compete.
    # EXIT: PERCENTILE OB/OS — close LONG when D+4h both overbought, SHORT when oversold
    # 2026-05-22 USER MANDATE: re-enabled with TIGHTER thresholds based on vec sweep
    # of 14 arms on ZECUSDC LONG 4yr post-B-fixes. Arm 11 (OB_D=75, OB_4H=55) produced
    # best non-sizing Sharpe (+0.0414 vs +0.0348 default OFF) and best gain/yr
    # (+200.78%/yr vs +165.22%/yr OFF). Prior "OFF — exits too early" reason no longer
    # applies at 75/55: lower thresholds bracket overbought earlier (exit sooner on
    # tops) which IS the "out at the top" mandate.
    # ROLLBACK: ENABLED=False, OB_D=90, OB_4H=75.
    WT_PERCENTILE_EXIT_ENABLED: bool = True
    WT_PERCENTILE_EXIT_OB_D: float = 75.0          # was 90 — sell sooner on D overbought (vec arm 11)
    WT_PERCENTILE_EXIT_OB_4H: float = 55.0         # was 75 — 4h confirmation tighter (vec arm 11)
    WT_PERCENTILE_EXIT_OS_D: float = 10.0          # D percentile < this → exit SHORT
    WT_PERCENTILE_EXIT_OS_4H: float = 25.0         # 4h percentile < this → confirm exit SHORT
    # ENTRY: wt_composite_delta gate — block entries when MTF bias strongly opposes
    WT_COMPOSITE_DELTA_GATE_ENABLED: bool = True
    WT_COMPOSITE_DELTA_LONG_MIN: float = -100.0    # block LONG when delta < this (all TFs bearish)
    WT_COMPOSITE_DELTA_SHORT_MAX: float = 100.0    # block SHORT when delta > this (all TFs bullish)
    # ENTRY: exhaust gate — OFF: exhaust mid-rally still valid entry, blocks too aggressively
    WT_EXHAUST_ENTRY_GATE_ENABLED: bool = False
    # ENTRY: percentile daily OB/OS gate — OFF: never block longs in a rally at high percentile
    WT_PERCENTILE_ENTRY_GATE_ENABLED: bool = False
    WT_PERCENTILE_ENTRY_OB_D: float = 90.0         # block LONG when daily WT > 90th percentile
    WT_PERCENTILE_ENTRY_OS_D: float = 10.0         # block SHORT when daily WT < 10th percentile
    # ENTRY: divergence gate — bear div blocks LONG, bull div blocks SHORT (price vs WT tops/bottoms)
    WT_DIV_ENTRY_GATE_ENABLED: bool = True         # wt_any_bear_div blocks LONG; wt_any_bull_div blocks SHORT
    # SCORING: wt_composite_delta bonus in rate() scoring
    WT_COMPOSITE_DELTA_SCORE_ENABLED: bool = True
    WT_COMPOSITE_DELTA_SCORE_THRESHOLD: float = 50.0  # delta > this adds bonus score
    WT_COMPOSITE_DELTA_SCORE_BONUS: float = 3.0       # bonus score points per threshold crossed
    # ───────────────────────────────────────────────────────────────────────
    # R-S SCORING ENHANCEMENTS (from indicator_audit_2026-04-18.xlsx sheet 4+5)
    # Each default OFF. Flip one at a time and sweep the value parameter.
    # ───────────────────────────────────────────────────────────────────────
    # R-S1 HIGH impact. Replace per-TF wt_bullish bool counting (rate L1791-1795)
    # with single wt_composite_delta read. Removes 5 redundant compares, exposes
    # true magnitude. Sweep THRESHOLD in [30, 50, 80, 120]. Expected: 1.3-1.5x Sharpe.
    R_S1_WT_COMPOSITE_DELTA_USE_ENABLED: bool = False
    R_S1_WT_COMPOSITE_DELTA_THR: float = 50.0
    # R-S2 HIGH. Adaptive OB/OS via wt_percentile instead of fixed wt1 thresholds.
    # 200-bar percentile self-calibrates per symbol. Sweep WT_PCT_OS in [5,10,15,20].
    # Expected: recovers edge on low-vol coins where fixed thresholds miss.
    R_S2_WT_ADAPTIVE_OS_ENABLED: bool = False
    R_S2_WT_PCT_OS_LONG: float = 10.0    # LONG triggers when wt_percentile_15m < this
    R_S2_WT_PCT_OB_SHORT: float = 90.0   # SHORT triggers when wt_percentile_15m > this
    # R-S3 HIGH. Divergence stacking: ≥2 HIDDEN_BULL TFs → +bonus (continuation);
    # BEAR div on 15m+1h → -penalty. Expected: best "reversal vs continuation" signal.
    # Sweep BONUS in [15, 25, 35], PENALTY in [-10, -20, -30].
    R_S3_DIV_STACK_ENABLED: bool = False
    R_S3_HIDDEN_BONUS: float = 25.0      # bonus for HIDDEN_BULL (LONG) / HIDDEN_BEAR (SHORT) on ≥2 TFs
    R_S3_MAIN_PENALTY: float = -20.0     # penalty for main divergence against position
    # R-S3 HTF WEIGHTING (2026-04-19): BEAR/BULL main divergences on 4h & D are currently
    # IGNORED entirely (old code only checked 15m/1h). Under-representing HTF is dangerous:
    # a 4h bear div is a much bigger signal than a 15m one. With this switch ON, R-S3 applies
    # MAIN_PENALTY weighted by TF — each hit contributes (TF_WEIGHT × MAIN_PENALTY) to score.
    # Sweep weights loosely-geometric to bias HTF heavily. Expected: cuts bad entries at major tops.
    R_S3_HTF_WEIGHT_ENABLED: bool = False
    R_S3_TF_WEIGHT_3M: float = 0.25      # 3m divergence — very noisy, low weight
    R_S3_TF_WEIGHT_15M: float = 0.5
    R_S3_TF_WEIGHT_1H: float = 1.0
    R_S3_TF_WEIGHT_4H: float = 2.5       # 4h = real HTF signal
    R_S3_TF_WEIGHT_D: float = 4.0        # D = strongest divergence signal
    # Same TF weights drive the HIDDEN (continuation) bonus side when enabled.
    # R-S4 MED. HA streak bonus: 5 * min(ha_streak_{tf}, 5). Replaces single-bar
    # ha=='green'/'red' check. Sweep WEIGHT in [3, 5, 7, 10]. Expected: +5-10% score
    # granularity for trend-continuation entries.
    R_S4_HA_STREAK_ENABLED: bool = False
    R_S4_HA_STREAK_WEIGHT: float = 5.0
    R_S4_HA_STREAK_TF: str = "1h"        # which TF to read ha_streak from
    # R-S5 MED. Sentiment velocity accelerator: bonus when sign(velocity)=side AND
    # |velocity| > Pxx. Sweep PCT in [60, 75, 90]. Expected: catches acceleration
    # before it's reflected in ranks.
    R_S5_SENT_VEL_ENABLED: bool = False
    R_S5_SENT_VEL_PCT_THR: float = 75.0  # |velocity| threshold (75th percentile)
    R_S5_SENT_VEL_BONUS: float = 5.0
    # R-S6 MED. wt_momentum_state entry filter. Block LONG when ANY LTF in EXHAUST_UP.
    # Modes: 0=OFF, 1=block_any_LTF_EXHAUST, 2=block_any_TF_EXHAUST, 3=warn_only.
    # Expected: avoids chasing exhausted pumps. Similar to existing R-G5 but more TFs.
    R_S6_WT_MSTATE_GATE_MODE: int = 0
    # R-S7 HIGH. HH/LL multi-indicator multi-TF stacking bonus. For each TF in R_S7_HHLL_TFS,
    # count indicators confirming HH (for LONG) or LL (for SHORT): price high/low_{tf} vs _prev,
    # wt_structure_{tf} (HH/LL label), stoch_k_{tf} vs stoch_k_{tf}_prev. If ≥ MIN indicators
    # confirm on that TF, the TF counts. Bonus = confirmed_TFs × BONUS_PER_TF. Expected: stacks
    # the "indicators all agree across TFs" structural signal that decision core currently ignores.
    R_S7_HHLL_STACK_ENABLED: bool = False
    R_S7_HHLL_TFS: str = "15m,1h,4h,D"    # which TFs to check (comma-separated)
    R_S7_HHLL_MIN_INDICATORS: int = 2      # min indicators confirming per TF (of 3: price, WT, stoch)
    R_S7_HHLL_BONUS_PER_TF: float = 3.0    # score points per confirmed TF
    R_S7_HHLL_MIN_TFS_FOR_BONUS: int = 2   # requires ≥ this many TFs confirming before any bonus fires
    # R-G10 HIGH. HTF-ONLY divergence hard gate. When a wt_divergence_{tf} for tf in R_G10_TFS
    # reports main divergence AGAINST the entry direction (BEAR on LONG / BULL on SHORT),
    # BOYCOTT the entry outright. R-G7 already does this for the "any TF" case; R-G10 is stricter,
    # specifically targeting 4h and D where divergence = top/bottom of cycle. Expected: prevents
    # buying-into-tops / selling-into-bottoms at cycle extremes.
    R_G10_HTF_DIV_GATE_ENABLED: bool = False
    R_G10_HTF_DIV_TFS: str = "4h,D"        # which HTFs to check (comma-separated)
    # ───────────────────────────────────────────────────────────────────────
    # R-Z SIZING ENHANCEMENTS
    # All multiplicative on top of existing target_notional. Default 1.0 = no effect.
    # ───────────────────────────────────────────────────────────────────────
    # R-Z1 DONE — already wired at ez_positions_quick.py:1239-1251 (switch: RANKING_MULT_ENABLED
    # at line 1286 above). Sweep RANKING_MULT_MAX in [1.5, 2.0, 2.5, 3.0] with MIN=0.3-0.5.
    # R-Z2 MED. combined_percentile 3-tier size scaler. Top 10% → 1.5x, bottom 30% → 0.5x.
    # Sweep 3 or 5 tier configs. Expected: prioritize capital on highest-quality setups.
    R_Z2_PERCENTILE_SCALER_ENABLED: bool = False
    R_Z2_PCT_TOP_THR: float = 90.0       # combined_percentile > this → TOP tier
    R_Z2_PCT_BOT_THR: float = 30.0       # combined_percentile < this → BOT tier
    R_Z2_TOP_MULT: float = 1.5
    R_Z2_BOT_MULT: float = 0.5
    # R-Z3 HIGH. wt_composite_long/short graduated sizing: >50 → 1x, >100 → 1.5x, >150 → 2x.
    # Sweep TIER_THR in {[30,60,90], [50,100,150]}. Expected: replaces multi-signal
    # voting stack (MTS/SATOSHIT) with single pre-computed number.
    R_Z3_WT_COMPOSITE_SIZE_ENABLED: bool = False
    R_Z3_T1_THR: float = 50.0
    R_Z3_T2_THR: float = 100.0
    R_Z3_T3_THR: float = 150.0
    R_Z3_T1_MULT: float = 1.0
    R_Z3_T2_MULT: float = 1.5
    R_Z3_T3_MULT: float = 2.0
    # R-Z4 DONE — already wired at ez_positions_quick.py:1107-1122 (switch:
    # CRASH_MULT_GRADIENT_ENABLED at line 1290 above). Sweep CRASH_MULT_GRADIENT_MAX in [2.0, 2.5, 3.0].
    # R-Z5 MED. Pullback-in-uptrend sizing: dc_position_15m < LOW_THR AND
    # dc_position_4h > HTF_MIN → MULT. Sweep HTF_MIN in [0.5, 0.6, 0.7].
    # Expected: classic pullback-buy setup, already 100% pre-computed.
    R_Z5_DC_PULLBACK_SIZING_ENABLED: bool = False
    R_Z5_DC_LTF_LOW_THR: float = 0.2     # LTF must be < 0.2 (near channel floor for LONG)
    R_Z5_DC_HTF_MIN: float = 0.6         # HTF must be > 0.6 (in channel top half for LONG)
    R_Z5_DC_PULLBACK_MULT: float = 1.5
    # ───────────────────────────────────────────────────────────────────────
    # RE REENTRY ENHANCEMENTS (additive inside existing blocks)
    # Default OFF preserves original block behavior exactly.
    # ───────────────────────────────────────────────────────────────────────
    # RE-2 MED. B02/B09 use wt_percentile_15m < PCT_OS instead of wt1_15m < -20.
    # Sweep PCT_OS in [3, 5, 10, 15]. Expected: adaptive OB/OS bottom detection.
    RE_2_USE_PERCENTILE_ENABLED: bool = False
    RE_2_PCT_OS: float = 5.0             # B02 LONG fires when wt_percentile_15m < this
    RE_2_PCT_OB: float = 95.0            # B02 SHORT fires when wt_percentile_15m > this
    # RE-3 MED. B12 conviction += BONUS when wt_rising_cross_count ≥ THR.
    # Sweep BONUS in [5, 10, 15]. Expected: continuation confidence ramp.
    RE_3_B12_RISING_BONUS_ENABLED: bool = False
    RE_3_RISING_COUNT_THR: int = 3
    RE_3_CONVICTION_BONUS: float = 10.0
    # RE-4 MED. B14 conviction scaled by min(ha_streak_{tf}, CAP). Replaces flat 70.
    # Sweep WEIGHT in [3, 5, 7]. Expected: 6-bar streak → higher conviction.
    RE_4_B14_HA_STREAK_CONV_ENABLED: bool = False
    RE_4_HA_STREAK_WEIGHT: float = 5.0
    RE_4_HA_STREAK_CAP: int = 5
    # RE-5 MED. B04 DC_RETEST bonus when bar_inside_count_15m ≥ THR (coiled spring).
    # Sweep BONUS in [5, 10, 15]. Expected: tight ranges breaking out = backbone of momentum.
    RE_5_B04_COMPRESSION_BONUS_ENABLED: bool = False
    RE_5_INSIDE_COUNT_THR: int = 3
    RE_5_COMPRESSION_BONUS: float = 10.0
    # RE-6 MED. B11/B15 require ≥ MIN_EXPANDING TFs in wt_wave_phase=EXPANDING.
    # Sweep MIN_EXPANDING in [1, 2, 3]. Expected: filters breakouts from chop phase.
    # Caution: default MIN=1 (least restrictive); NPZ coverage of wt_wave_phase varies.
    RE_6_WAVE_PHASE_GATE_ENABLED: bool = False
    RE_6_MIN_EXPANDING_TFS: int = 2
    # ───────────────────────────────────────────────────────────────────────
    # E EXIT ENHANCEMENTS
    # REPLACE existing logic — null-guard to existing path when switch OFF.
    # ───────────────────────────────────────────────────────────────────────
    # E-1 MED. Replace 5-TF WT_EXIT_MIN_TFS vote with wt_composite_delta threshold.
    # LONG exits when delta < -THR (strong cross-TF bear). Sweep THR in [30, 50, 80].
    # Expected: continuous signal better than 5-of-5 boolean vote (that produces Sharpe ≈ 0).
    E_1_WT_EXIT_USE_DELTA_ENABLED: bool = False
    E_1_EXIT_DELTA_THR: float = 50.0
    # E-3 MED. Use wt_structure_{tf} HH/HL labels in exit. Modes: 0=off, 1=shadow
    # (log only, no action), 2=on (live exits). Expected: single source of truth for
    # structure break; eliminates reimplementation bugs in high/low lookups.
    E_3_USE_WT_STRUCTURE_EXIT_MODE: int = 0
    # ═══════════════════════════════════════════════════════════════════════
    # END INDICATOR-AUDIT SWITCHES
    # ═══════════════════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════════════════
    # REENTRY GUARANTEE SWITCHES (2026-04-19)
    # ═══════════════════════════════════════════════════════════════════════
    # TIER1: always fire partial when price crosses exit level, even when k_3m > 95 (exhausted)
    REENTRY_EXHAUSTED_PARTIAL_ENABLED: bool = True
    # B16: 200 SMA pullback — price returns to 200SMA after exit, HTF still trending → 150-300%
    REENTRY_B16_SMA200_PULLBACK_ENABLED: bool = True
    REENTRY_B16_SMA200_PROX_PCT: float = 0.005        # within 0.5% of sma_200_1h triggers
    REENTRY_B16_SIZE_MULT_STRONG: float = 3.0          # 300% when wt_vel_1h confirms trend still on
    REENTRY_B16_SIZE_MULT_WEAK: float = 1.5            # 150% when vel weak / move fading
    # LIVE MONITOR: poll all reentry JSON sources every N seconds
    REENTRY_LIVE_MONITOR_ENABLED: bool = True
    REENTRY_LIVE_MONITOR_INTERVAL_S: int = 30          # check ladder + reentry files every 30s
    REENTRY_LIVE_MONITOR_PARTIAL_PCT: float = 0.5      # ladder level fires 50% of normal size
    # 2026-05-28 USER anti-churn TEST SWITCH (default OFF): the level-cross reentry
    # (price touches the stored exit/reentry level) churns. When ENABLED, reentry fires
    # only if price ALSO breaks a Donchian level — dc_high_<TF> (long) / dc_low_<TF>
    # (short), or dc_high4_<TF>/dc_low4_<TF> (latest 4-bar) when USE_4BAR=True — i.e. a
    # real breakout, not a touch-back. Fail-open if the indicator is missing.
    REENTRY_LIVE_MONITOR_DC_BREAK_ENABLED: bool = False  # 2026-05-29 NEUTRALIZED to default-OFF: an agent set this True (live default-ON) on a SUB-FLOOR (20<48 sym) DIAGNOSTIC vec A/B — violates sample-floor + new-strategy + kill-switch-default-OFF. Gate code preserved in ez_manage.py; enable only after >=48-sym proof + user OK.
    REENTRY_LIVE_MONITOR_DC_BREAK_TF: str = "3m"
    REENTRY_LIVE_MONITOR_DC_BREAK_USE_4BAR: bool = True  # 2026-05-29 4-bar prior high (dc_high4_3m) won over full-DC on every metric
    # ═══════════════════════════════════════════════════════════════════════
    # END REENTRY GUARANTEE SWITCHES
    # ═══════════════════════════════════════════════════════════════════════

    def __post_init__(self):
        self._INSTANCES.add(self)
        if self.DELTA_TF_WEIGHTS is None:
            self.DELTA_TF_WEIGHTS = {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5}  # WINNER: 3m-dominant (48sym 4yr Sharpe 0.806)
        self._apply_mode(self._resolve_initial_mode())
        if self.SANDBOX_MODE:
            for sa in self.SANDBOX_ACCOUNTS:
                if sa not in self.ACCOUNT_KEYS:
                    self.ACCOUNT_KEYS.append(sa)

    def _resolve_initial_mode(self) -> str:
        if self.EXTREME_MODE and not self.LIGHT_MODE:
            return "EXTREME_MODE"
        if self.LIGHT_MODE and not self.EXTREME_MODE:
            return "LIGHT_MODE"
        if self.MARKET_MODE in {"EXTREME_MODE", "LIGHT_MODE", "NORMAL_MODE"}:
            return self.MARKET_MODE
        return self._CURRENT_MARKET_MODE

    def _apply_mode(self, mode: str):  # emergency mode
        light = {
            # "START_POSITION_SIZE": 11.0,#emergency mode
            # "MAX_POSITION_SIZE": 250.0,
            # "MAX_ORDER_VALUE": 110.0,
            # "MAX_ORDER_VALUE_MEN": 180.0,#emergency mode
            # "MAX_ORDER_VALUE_FIN": 200.0,#emergency mode
            # "HIGH_GAIN_AUGMENTATION_MIN_SIZE": 200,
            # "REDUCTION_COOLDOWN_SECONDS": 480.0,
            # "AUGMENTATION_COOLDOWN_SECONDS": 480.0,  # REDUCED: From 480 to 240 for faster reactions
            # "MIN_GAIN": 1.2,#emergency mode
            # "MAX_POSITION_SIZE_MEN": 800.0,#emergency mode
            # "MAX_POSITION_SIZE_FIN": 900.0#emergency mode
            "START_POSITION_SIZE": 6.0,  # emergency mode
            "MAX_POSITION_SIZE": 155.0,
            "MAX_ORDER_VALUE": 80.0,
            "MAX_ORDER_VALUE_MEN": 440.0,
            "MAX_ORDER_VALUE_FIN": 18.0,
            "HIGH_GAIN_AUGMENTATION_MIN_SIZE": 100,
            "REDUCTION_COOLDOWN_SECONDS": 30.0,
            "AUGMENTATION_COOLDOWN_SECONDS": 660.0,
            "MIN_GAIN": 3.0,  # was 5.0. 3.0% survives 1.5% reversal after 50% aug
            "MAX_POSITION_SIZE_MEN": 220.0,
            "MAX_POSITION_SIZE_FIN": 120.0,
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
            # "MIN_GAIN": 0.8,#emergency mode
            # "MAX_POSITION_SIZE_MEN": 160.0,#emergency mode
            # "MAX_POSITION_SIZE_FIN": 300.0#emergency mode
            "START_POSITION_SIZE": 18.0,
            "MAX_POSITION_SIZE": 20.0,  # 1/50 RULE: $1k / 50 = $20 max per position
            "MAX_ORDER_VALUE": 20.0,  # 1/50 RULE
            "MAX_ORDER_VALUE_MEN": 20.0,  # 1/50 RULE
            "MAX_ORDER_VALUE_FIN": 20.0,  # 1/50 RULE
            "HIGH_GAIN_AUGMENTATION_MIN_SIZE": 50,  # BACKTEST_CHANGE_25: was 100
            "REDUCTION_COOLDOWN_SECONDS": 15.0,  # BACKTEST_CHANGE_42: was 30. Faster gain-taking on 3m
            "AUGMENTATION_COOLDOWN_SECONDS": 90.0,  # BACKTEST_CHANGE_41: was 160. 3m TF needs faster aug
            "MIN_GAIN": 3.0,  # was 5.0. 3.0% survives 1.5% reversal after 50% aug
            "MAX_POSITION_SIZE_MEN": 20.0,  # 1/50 RULE
            "MAX_POSITION_SIZE_FIN": 20.0,  # 1/50 RULE
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
            "MIN_GAIN": 3.0,  # was 5.0. 3.0% survives 1.5% reversal after 50% aug
            "MAX_POSITION_SIZE_MEN": 3200.0,
            "MAX_POSITION_SIZE_FIN": 4000.0,
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
        if mode not in {"NORMAL_MODE", "EXTREME_MODE", "LIGHT_MODE"}:
            mode = "NORMAL_MODE"
        cls._CURRENT_MARKET_MODE = mode
        for instance in list(cls._INSTANCES):
            instance._apply_mode(mode)

    def get_account_setting(self, account_key: str, setting_name: str):
        return self.ACCOUNT_OVERRIDES.get(account_key, {}).get(
            setting_name, getattr(self, setting_name, None)
        )

    def get_symbol_setting(self, account_key: str, position_key: str, setting_name: str):
        """Hot-path config lookup: regime override → account override → global default.
        Checks in-process _REGIME_OVERRIDES first, then Redis cache (refreshed every 5s)."""
        pk = position_key if ":" not in position_key else position_key.split(":", 1)[1]
        full_key = f"{account_key}:{pk}"
        # 1. In-process overrides (same process as adaptive_regime daemon)
        regime = self._REGIME_OVERRIDES.get(full_key)
        if regime and setting_name in regime and not regime.get("_paper", False):
            return regime[setting_name]
        # 2. Redis cross-process cache (when trading runs in separate process)
        regime = self._get_regime_from_redis(full_key)
        if regime and setting_name in regime and not regime.get("_paper", False):
            return regime[setting_name]
        return self.get_account_setting(account_key, setting_name)

    @classmethod
    def _get_regime_from_redis(cls, full_key: str) -> dict | None:
        """Load single regime override from Redis. 5s cache per key."""
        import time as _time
        now = _time.time()
        cached = cls._REGIME_REDIS_CACHE.get(full_key)
        if cached and now - cached.get("_cache_ts", 0) < 5.0:
            return cached
        try:
            import json as _json

            import redis as _redis
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
        """Called by adaptive_regime.py to hot-inject per-symbol config. position_key = 'ang:BTCUSDC_LONG'."""
        cls._REGIME_OVERRIDES[position_key] = overrides

    @classmethod
    def clear_regime_override(cls, position_key: str):
        cls._REGIME_OVERRIDES.pop(position_key, None)

    @classmethod
    def get_all_regime_overrides(cls) -> Dict[str, Dict]:
        return dict(cls._REGIME_OVERRIDES)

    def get_stop_indicator_keys(self, account_key: str) -> tuple:
        tf = self.get_account_setting(account_key, "STOP_TIMEFRAME")
        return (
            f"dc_low_{tf}",
            f"dc_high_{tf}",
            f"dc_low4_{tf}",
            f"dc_high4_{tf}",
            f"dc_basis_{tf}",
        )

    MARKET_MODE_FILE: str = "data/market_mode.json"

    async def save_market_mode(self):
        import json as _json

        path = self.BASE_PATH / self.MARKET_MODE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "w") as f:
            await f.write(
                _json.dumps(
                    {
                        "market_mode": self.MARKET_MODE,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            )

    async def load_market_mode(self):
        import json as _json

        path = self.BASE_PATH / self.MARKET_MODE_FILE
        try:
            async with aiofiles.open(path, "r") as f:
                data = _json.loads(await f.read())
                mode = data.get("market_mode", "NORMAL_MODE")
                if mode in {"NORMAL_MODE", "EXTREME_MODE", "LIGHT_MODE"}:
                    self.set_market_mode(mode)
        except (FileNotFoundError, _json.JSONDecodeError):
            pass

    PAPER_TRADING: bool = (
        False  # Enable paper trading mode - records trades without executing
    )
    PAPER_TRADING_QUICK: bool = (
        False  # Enable paper trading mode - records trades without executing
    )

    SLEEP_TIME_PER_TASKS: int = 3  # FETCH and leaderboards
    SLEEP_TIME_PROC_ACCT: float = 5  # Check symbols - 100 times per minute minimum
    DIRECT_HIGH_GAIN_COOLDOWN_SECONDS = 15
    # =============================================================================
    # TIMING CONFIGURATION - ALL TIMING SETTINGS IN ONE PLACE FOR EASY TWEAKING
    # =============================================================================
    MAX_CONCURRENT_ORDERS: float = 186
    # MAX_CONCURRENT_TASKS: float =           75 #SEMAPHORE (under 40 limit for API throttling)
    FORCE_REFRESH_SECONDS: float = 10  # BACKTEST_CHANGE_43: was 16. Fresher data for 3m decisions
    MAX_MARKET_DATA_FILE_AGE_SECONDS: float = 1200
    MARKET_DATA_REFRESH_INTERVAL_SECONDS: float = 45.0
    # --- CACHE TIMING (seconds) ---
    # POSITION_CACHE_TTL: int =               5  # Position data cache duration
    ORDER_CACHE_TTL: int = 60  # Open orders cache duration (was 10 — caused IP bans with 5 accounts)
    POSITIONS_SNAPSHOT_MAX_AGE: float = (
        6.0  # Max age (seconds) accepted for RPC snapshots
    )
    LOCAL_DATA_MAX_AGE: float = (
        200.0  # Max age (seconds) accepted for file-based fallbacks
    )
    INDICATOR_MAX_AGE_SECONDS = 200.0
    STALE_WARNING_INTERVAL_SECONDS: float = 30.0
    POSITION_SAVE_INTERVAL: float = 6.0
    INDICATORS_SAVE_INTERVAL_SECONDS: float = 10.0
    POSITION_REDIS_REFRESH_INTERVAL: float = 6.0
    LADDER_AUTO_SAVE_SECONDS: float = 60.0
    MONITOR_REDUCTION_STALE_THRESHOLD: float = 180.0
    POSITION_STALE_THRESHOLD_SECONDS: float = 60.0
    # MARK_PRICE_GUARD_INTERVAL: float =      1.0  # Seconds between websocket staleness checks
    MARK_PRICE_MAX_STALENESS: float = 2  # Maximum acceptable age of cached mark price
    # User 2026-05-05: hard freshness gate at execute_now + execute_trade_wrapper.
    # If position.mark_price_last_updated > this, REFUSE non-CLOSE orders (after
    # one Redis refresh attempt). Saved 1000LUNCUSDT-style 45% loss where every
    # gain-gated guard read an hour-stale mark and fired wrong decisions.
    EXECUTE_NOW_MAX_MARK_AGE_S: float = 60.0  # 2026-05-12: was 120→60. Fallback now queries price_cache (WS-updated, no TTL) so block only fires if ALL in-mem caches are >60s old — impossible under normal WS operation.
    # User 2026-05-05 (1000LUNCUSDT screenshot): a LONG/SHORT on a symbol whose
    # OPPOSITE side is deeply losing acts as a de-facto hedge. Ban PPL,
    # WT_CROSS_EXIT, BANDAID_OFF, PEAK_GIVEBACK, and similar small-gain closes
    # while opposite is bleeding and current side has not yet earned enough to
    # offset. Bypass at EMERGENCY/HARD_STOP/MAX_AGE/ORPHAN/LIQ/STRUCTURAL/AGENT/USER.
    OPPOSITE_LOSER_HEDGE_PROTECT_ENABLED: bool = False
    OPPOSITE_LOSER_DEEP_LOSS_PCT: float = -5.0
    OPPOSITE_LOSER_HEDGE_PROTECT_MAX_GAIN: float = 5.0
    OPPOSITE_LOSER_HEDGE_PROTECT_REQUIRE_WT_3M: bool = False
    # PRICE_FALLBACK_INTERVAL: float =        1.0  # Interval for REST/Redis mark-price fallback loop
    EZ_INDICATORS_SHUTDOWN_CMD: Optional[str] = (
        None  # shell command to stop ez_indicators gracefully
    )
    EZ_INDICATORS_START_CMD: Optional[str] = (
        None  # shell command to (re)start ez_indicators
    )
    EZ_INDICATORS_RESTART_COOLDOWN: float = (
        60.0  # Minimum seconds between ez_indicators restart attempts
    )
    EZ_INDICATORS_CMD_TIMEOUT: float = (
        10.0  # Timeout for ez_indicators start/stop commands
    )
    POSITIONS_SERVICE_START_CMD: Optional[str] = (
        None  # shell command to start ez_positions_service
    )
    POSITIONS_SERVICE_HEALTH_TIMEOUT: float = 4.0  # Seconds to wait for RPC ping
    POSITIONS_SERVICE_HEALTH_RETRIES: int = (
        3  # Number of retries before giving up on RPC ping
    )
    # STOP_ORDERS_CACHE_TTL: int =            15  # Stop orders cache duration
    # INDICATOR_CACHE_TTL: int =              10   # Indicator cache duration (seconds, keep data sub-second)

    # --- API RATE LIMITING ---
    # POSITION_RATE_LIMIT_SECONDS: int =      2  # Min seconds between position API calls (4 per minute)
    CIRCUIT_BREAKER_COOLDOWN: int = 60  # BACKTEST_CHANGE_40: was 120. 3m TF needs faster recovery

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
    REENTER_SAVE_DEBOUNCE_SECONDS: int = 30  # BACKTEST_CHANGE_45: was 50. Faster reentry on 3m TF

    # --- OTHER TIMING ---
    # FORCE_REFRESH_SECONDS: int =            600
    # FORCE_SYMBOL_REFRESH_SECONDS: int =     600
    LADDER_TTL_MINUTES: int = 24 * 60  # Ladder order TTL
    # SAVE_INTERVAL: int =                    60
    # RETRY_DELAY: int =                      5
    REDIS_EXPIRY_SECONDS: int = 180
    MIN_USD_DELTA_CONFIRM: float = 1.0

    FAPI_BASE_URL: str = "https://fapi.binance.com/fapi/v1"
    FSTREAM_WS_URL_BASE: str = "wss://fstream.binance.com/stream"
    WS_URL: str = "wss://fstream.binance.com/ws"
    USE_WS_3M: bool = (
        True  # If True, consume 3m klines directly from Binance WS (no local resampling)
    )
    REDIS_1M_TAIL: int = (
        1500  # Number of most recent 1m bars to keep/publish in Redis payload
    )
    # EXTERNAL_3M_PRODUCER: bool = True  # If True, ez_prices skips internal 3m generation (handled by WS or external)
    # MARK_PRICES_ONLY: bool = True    # If True, ez_mark_prices only publishes mark prices (no 1m Redis publish), but still writes 1m JSON
    # FETCH_1M_FROM_API: bool = False
    # FETCH_3M_FROM_API: bool = True
    # ENABLE_WS_BACKUP_CONSOLIDATION: bool = True  # Enable backup consolidation in ez_prices_ws
    INDICATORS_DATA_CACHE_SIZE = 2048
    # ORDER_WORKERS                       =   20
    LOG_MAX_BYTES = 1024 * 1024 * 20
    LOG_BACKUP_COUNT = 30
    # ENABLE_CONVICTION: bool =               True
    USE_INDICATOR_SNAPSHOT: bool = True
    POSITION_REFRESH_MIN_INTERVAL: int = 5  # \seconds
    # SINGLE_FETCH_COOLDOWN: int =            15

    # PERIODIC_STOP_ORDERS_ENABLED: bool =    False
    # Enable periodic stop order management
    # PERIODIC_STOP_ORDERS_INTERVAL: int =    20   # Run every 20 seconds (check for missing stops)    SYMBOL_TRACKER_ENABLED: bool = False
    # RATE_LIMIT_DUPLICATE_FILTER_ENABLED: bool = True
    # MAKER_ORDER_CONFIG: Dict[str, Any] = field(default_factory=lambda: {
    # "TIMEOUT_SECONDS": 30, })

    def _resolve_base_path() -> Path:
        env_base = os.environ.get("BASE_PATH")
        if env_base:
            base = Path(env_base.replace("~", os.environ.get("HOME") or "/tmp")) if "~" in env_base else Path(env_base)
            candidates = [base]
            if base.name.lower() != "binance": candidates.append(base / "binance")
            for candidate in candidates:
                try:
                    if candidate.exists(): return candidate
                except Exception: pass
            return candidates[0]
        try:
            curr_dir = Path(__file__).resolve().parent
            if curr_dir.exists() and any(name in curr_dir.name.lower() for name in ("binance", "sandbox")): return curr_dir
        except Exception: pass
        system_name = platform.system()
        if system_name == "Darwin": return Path("/Users/niels/Documents/binance")
        if system_name == "Linux":
            sandbox = Path("/home/niels/binance-sandbox")
            if sandbox.exists(): return sandbox
            return Path("/home/niels/binance")
        return Path.home() / "Documents" / "binance"

    # LADDER_LEVELS: int = 6        # Number of post-exit ladder orders
    # LADDER_SPLIT: List[float] = field(default_factory=lambda: [0.33, 0.33, 0.34])
    # MIN_LADDER_POSITION_SIZE: float = 0.5 * START_POSITION_SIZE
    LOG_DIR: Path = Path(os.environ.get("EZ_LOG_DIR") or (Path.home() / "logs"))
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
    SYMBOLS: Path = BASE_PATH / "symbols.json"
    SYMBOLS_ACTIVE_FILE: Path = BASE_PATH / "symbols_active.json"
    SYMBOLS_ANG_LONG: Path = BASE_PATH / "symbols_ang_long.json"
    # 2026-07-19 USER: inf redirected onto men's symbol universe (symbols_men_long/short.json,
    # per_sym settings + 7D reapplied on top) for a live A/B comparison, instead of its own
    # momentum-ranked list. ez_rankings.py still writes the old symbols_inf_long.json/short.json
    # (now unused/orphaned) — kept for a fast revert. See BACKTEST_BIBLE.md §inf/men parity.
    SYMBOLS_INF_LONG: Path = BASE_PATH / "symbols_men_long.json"
    SYMBOLS_INF_SHORT: Path = BASE_PATH / "symbols_men_short.json"
    # ================================================================
    # INF RANKING PRIORITY BYPASS — 2026-04-16
    # When a symbol is in symbols_inf_long/short (built by ez_rankings
    # from recent 15m/3m extreme movers), relax specific entry gates
    # that empirically block 94% of qualifying big movers (replay test
    # inf_replay_gates.py on 72 missed >=3% moves 2026-02-24..03-26).
    # MASTER FLAG DEFAULTS OFF — sub-flags do nothing until master=True.
    # Wiring into live entry code is pending V8 backtest validation.
    # ================================================================
    INF_RANKING_PRIORITY_BYPASS: bool = False            # master switch
    INF_RANKING_BYPASS_STOCH: bool = True                # relax K3M_CAP/K15M — unlocks 73.6% alone
    INF_RANKING_BYPASS_HTF: bool = True                  # 2/3 HTF -> 1/3 HTF — +18pp on top of stoch
    INF_RANKING_BYPASS_SCORE: bool = False               # score gate bypass (unclear impact, keep off)
    INF_RANKING_BYPASS_DELTA: bool = False               # DELTA_GATE_OPEN bypass (not measured yet)
    INF_RANKING_BYPASS_WT: bool = False                  # WT composite bypass (trend misread risk)
    INF_RANKING_BYPASS_FRESHNESS_MIN: int = 30           # only bypass within N min of list entry
    INF_RANKING_BYPASS_MAX_POS: int = 8                  # soft cap on concurrent bypass-entries
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
    PRICE_CACHE_PULL_S1: Path = BASE_PATH / "data" / "mark_prices_pull" / "from_s1.json"
    PRICE_CACHE_PULL_MAX_AGE_SEC: float = 3.0
    MIN_QTY_FILE: Path = BASE_PATH / "min_qty.json"
    MULT_FILE: Path = BASE_PATH / "multipliers.json"
    SYMBOL_CONFIGS_FILE: Path = BASE_PATH / "symbol_configs.json"
    RANKING_RESULTS_FILE: Path = BASE_PATH / "ranking_results.json"
    TRADEABLE_KEYS: Path = BASE_PATH / "tradeable_keys.json"

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
    indicators_filepath: Path = DATA_DIR / "latest_market_data.json"
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
    REENTER_ORPHAN_THRESHOLD: timedelta = field(
        default_factory=lambda: timedelta(days=70)
    )

    # --- ANALYZER SETTINGS ---
    # ANALYZER_DAYS_BACK: int = 30  # How many days back the analyzer should look
    # ANALYZER_MIN_TRADES_FOR_ANALYSIS: int = 5  # Minimum trades needed for meaningful analysis
    # ANALYZER_SHOW_TOP_N_TRADES: int = 5  # Number of top/bottom trades to show

    ACCOUNT_SIDE_MAPPING = {
        "ang": ["LONG", "SHORT"],
        "inf": ["LONG", "SHORT"],
        "men": ["LONG", "SHORT"],
        "flz": ["LONG", "SHORT"],
        "fin": ["LONG", "SHORT"],
    }

    # --- REDIS SETTINGS ---
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_CHANNEL_SIGNALS: str = "signals_channel"
    REDIS_KEY_MARKET_DATA: str = (
        "latest_market_data"  # Redis key for market data (not a channel)
    )
    WORKER_INSTANCE_ID: int = int(
        os.getenv("WORKER_INSTANCE_ID", "0")
    )  # 0-based instance ID for symbol splitting (0, 1, 2, ...)
    WORKER_TOTAL_INSTANCES: int = int(
        os.getenv("WORKER_TOTAL_INSTANCES", "1")
    )  # Total number of worker instances (1=single, 2=dual, etc.)
    ENABLE_MULTI_INSTANCE_ON_MACBOOK: bool = (
        False  # If False, forces single instance on macbook even if WORKER_TOTAL_INSTANCES > 1
    )

    # --- CONSTANTS ---
    KLINE_COLUMNS: List[str] = field(
        default_factory=lambda: ["timestamp", "open", "high", "low", "close", "volume"]
    )
    # ALL_TIMEFRAMES: List[str] = field(default_factory=lambda: ["3m", "15m", "1h", "4h", "D"])
    ACCOUNT_KEYS: List[str] = field(
        default_factory=lambda: ["ang", "inf", "men", "flz", "fin"]
    )

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
    EZ_KLINES_API_MAX_PER_SECOND: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 22, "server": 20, "macbook": 22}
    )
    EZ_KLINES_API_MAX_PER_MINUTE: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 200, "server": 200, "macbook": 300}
    )
    EZ_KLINES_MAX_CONCURRENT: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 10, "server": 10, "macbook": 15}
    )
    EZ_KLINES_SEMAPHORE: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 10, "server": 10, "macbook": 15}
    )

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
    EZ_MANAGE_WS_SEMAPHORE: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 140, "macbook": 140}
    )
    EZ_MANAGE_RATE_LIMIT_SEMAPHORE: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 35, "macbook": 50}
    )
    EZ_MANAGE_MAKER_SEMAPHORE: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 45, "macbook": 65}
    )
    EZ_MANAGE_THROTTLER_RATE: Dict[str, int] = field(
        default_factory=lambda: {
            "gateway": 200,
            "macbook": 250,  # INCREASED: From 50/125 to 200/250 for faster position processing
        }
    )
    EZ_MANAGE_CONCURRENCY_LIMIT: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 190, "macbook": 180}
    )
    # --- EZ_PRICEWS CONTROLS (gateway, server, macbook) ---
    EZ_PRICEWS_API_SEMAPHORE: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 2, "server": 2, "macbook": 2}
    )
    EZ_PRICEWS_CONNECTOR_LIMIT: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 5, "server": 5, "macbook": 5}
    )
    EZ_PRICEWS_LIMIT_PER_HOST: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 3, "server": 3, "macbook": 3}
    )
    EZ_PRICEWS_API_DELAY: Dict[str, float] = field(
        default_factory=lambda: {"gateway": 0.5, "server": 0.5, "macbook": 0.5}
    )

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
    EZ_RANKINGS_THROTTLER_RATE: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 500, "macbook": 250}
    )

    # --- EZ_MARK_PRICES CONTROLS (gateway, server, macbook) ---
    EZ_MARK_PRICES_API_SEMAPHORE: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 35, "server": 35, "macbook": 35}
    )
    EZ_MARK_PRICES_STARTUP_SEMAPHORE: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 30, "server": 30, "macbook": 30}
    )

    # --- EZ_PRICES CONTROLS (already configured above) ---
    # FILE_IO_CONCURRENCY, API_CONCURRENCY, fapi_semaphore in ResamplingAndGapFillEngine.__init__
    EZ_PRICES_FILE_IO_SEMAPHORE: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 100, "server": 100, "macbook": 50}
    )
    EZ_PRICES_API_SEMAPHORE: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 3, "server": 3, "macbook": 3}
    )
    EZ_PRICES_FAPI_SEMAPHORE: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 2, "server": 2, "macbook": 2}
    )
    EZ_PRICES_API_DELAY: Dict[str, float] = field(
        default_factory=lambda: {"gateway": 0.5, "server": 0.5, "macbook": 0.5}
    )
    EZ_PRICES_API_SLEEP_AFTER: Dict[str, float] = field(
        default_factory=lambda: {"gateway": 0.3, "server": 0.3, "macbook": 0.3}
    )
    EZ_PRICES_LIMIT_PER_HOST: Dict[str, int] = field(
        default_factory=lambda: {"gateway": 10, "server": 10, "macbook": 5}
    )
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
    RANKING_LOOP_SLEEP_SECONDS: int = 120  # BACKTEST_CHANGE_44: ranking loop sleep (2 minutes, was 3)
    # SIGNALS_LOOP_SLEEP_SECONDS: int = 300  # Signals loop sleep (5 minutes)
    # PLOT_LOOP_SLEEP_SECONDS: int = 3600  # Plot loop sleep (1 hour)
    # REDIS_HEALTH_CHECK_INTERVAL_SECONDS: int = 120  # Redis health check interval
    MEMORY_MONITOR_SLEEP_SECONDS: int = 60  # Memory monitor loop sleep
    PLOT_LOOP_INTERVAL_SECONDS: int = 1800  # Plot loop interval
    SIGNALS_LOOP_INTERVAL_SECONDS: int = 300  # Signals loop interval
    ERROR_RECOVERY_SLEEP_SECONDS: int = 60  # Sleep after errors
    # INITIAL_WAIT_SECONDS: int = 30  # Initial wait for setup

    # ════════════════════════════════════════════════════════════════════
    # BTC-DEDICATED LOOP (flz:BTCUSDC + inf BTC trades). Default ALL OFF.
    # Spec: BTC_DEDICATED_LOOP_DESIGN_20260427.md. 20× leverage. Path B (technical exit
    # + guaranteed reentry) is default; sweep validates Path A (hedge) too.
    # CRITICAL: paper-live-backtest parity — same Python function objects.
    # ════════════════════════════════════════════════════════════════════
    BTC_DEDICATED_ENABLED: bool = False                                  # 2026-07-01 USER: DISABLED — btc_loop.py v0/WIP has UNBUILT safety knobs (BTC_TECH_EXIT_AT_ANY_PNL wired but BTC_PYRAMID_DISABLED/REGIME_PAUSE/REVERSE_REQUIRE_HTF gate nonexistent logic) → unsafe at 20x. Re-enable only after those are built+proven. ROLLBACK True.
    BTC_DEDICATED_ACCOUNTS: List[str] = field(default_factory=lambda: ["flz", "inf"])  # accounts that route BTC trades through this loop
    BTC_DEDICATED_SYMBOLS: List[str] = field(default_factory=lambda: ["BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC", "XRPUSDC", "DOGEUSDC", "ZECUSDC", "BTCDOMUSDT"])  # all flz BTC_DEDICATED symbols
    BTC_PER_SYM_CONFIG_ENABLED: bool = True                               # load per-symbol overrides from data/hourly_reconfig/flz/active_config.json
    BTC_HARD_BLOCK_OTHER_ACCOUNTS: bool = False                            # block ang/men/fin from BTCUSDC + BTCUSDC at is_tradeable
    PER_SYM_CONFIG_ENABLED: bool = True                                    # load per-symbol entry-score overrides from data/hourly_reconfig/per_sym_active_config.json (global, all accounts)

    # --- Red zone composition (existing wt_dc + new fib + new round numbers) ---
    BTC_RZ_USE_WT_DC: bool = True
    BTC_RZ_USE_FIB: bool = True
    BTC_RZ_USE_ROUND: bool = True
    BTC_RZ_PROXIMITY_PCT: float = 0.5                                     # within X% of any level = active red zone

    # --- Fib levels (4h/D/W/M/Y) ---
    BTC_FIB_LOOKBACK_4H: int = 200
    BTC_FIB_LOOKBACK_D: int = 180
    BTC_FIB_LOOKBACK_W: int = 104
    BTC_FIB_LOOKBACK_M: int = 24
    BTC_FIB_LOOKBACK_Y: int = 5
    BTC_FIB_RECOMPUTE_ON_NEW_HL: bool = True                              # recompute fib for a TF whenever it makes a new local H or L

    # --- Round-number bands ---
    BTC_ROUND_INC_PRIMARY_USD: float = 5000.0
    BTC_ROUND_INC_SECONDARY_USD: float = 1000.0
    BTC_ROUND_BANDS_EACH_SIDE: int = 8

    # --- Multi-TF accelerating WT delta gate (PRIMARY entry trigger) ---
    BTC_ACCEL_RAMP_ENABLED: bool = True                                   # Δwt > Δwt_prev > 0 on every TF
    BTC_ACCEL_RAMP_MIN_TFS: int = 5                                       # default strict (all 5: 3m,15m,1h,4h,D)
    BTC_ACCEL_RAMP_REQUIRE_POSITIVE: bool = True                          # require accel > 0 (true ramp), not just rising-from-negative
    BTC_ACCEL_RAMP_PRICE_BOUNCE_TF: str = "3m"
    BTC_ACCEL_RAMP_PRICE_BOUNCE_BARS: int = 3

    # --- Divergence (continuous monitoring on multiple indicators) ---
    BTC_DIVERGENCE_ENABLED: bool = True
    BTC_DIVERGENCE_BULL_MIN_INDS: int = 2                                 # 2-of-5 inds (WT/RSI/MFI/OBV/CVD) showing bull div
    BTC_DIVERGENCE_BEAR_MIN_INDS: int = 2
    BTC_DIVERGENCE_LOOKBACK_BARS: int = 5
    BTC_DIVERGENCE_BLOCK_AGAINST: bool = True                             # bear div blocks LONG entries
    BTC_DIVERGENCE_EXIT_AGAINST: bool = True                              # bear div triggers LONG exit

    # --- Entry trigger composition ---
    BTC_ENTRY_PRIMARY_REQUIRE_RZ: bool = True                             # red zone must be active
    BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP: bool = True                     # accel ramp must align
    BTC_ENTRY_PRIMARY_BLOCK_OPPOSING_DIV: bool = True
    BTC_ENTRY_DIV_ONLY_ENABLED: bool = False                              # secondary path: divergence-strong + RZ
    BTC_ENTRY_DIV_ONLY_MIN_INDS: int = 3

    # --- Risk path selection ---
    BTC_RISK_PATH: str = "technical"                                      # "technical" (default per user) | "hedge" — sweep both

    # --- Risk path A: HEDGE (sweep all settings) ---
    BTC_HEDGE_TRIGGER_LOSS_PCT: float = -0.4                              # tight at 20× (target: hedge before -0.55%)
    BTC_HEDGE_SAME_SYMBOL_PCT: float = 1.0
    BTC_HEDGE_MIN_HOLD_BARS: int = 10
    BTC_HEDGE_WT_KILL_CONFIRM_TF: str = "1h"
    BTC_HEDGE_DC_RESISTANCE_GATE_ENABLED: bool = True
    BTC_HEDGE_WT_VEL_GATE_ENABLED: bool = True
    BTC_HEDGE_REQUIRE_4OF5_WT_TFS: bool = True
    BTC_HEDGE_NEVER_CLOSE_AT_LOSS: bool = True

    # --- Risk path B: TECHNICAL EXIT + GUARANTEED REENTRY (default per user 2026-04-27) ---
    BTC_TECH_EXIT_WT_MIN_TFS: int = 3
    BTC_TECH_EXIT_DC_BREACH_TF: str = "15m"
    BTC_TECH_EXIT_AT_ANY_PNL: bool = True                                 # bypass NOLOSS — sell at technicals at any P/L
    BTC_GUARANTEED_REENTRY_ENABLED: bool = True
    BTC_GUARANTEED_REENTRY_MAX_AGE_BARS: int = 480                        # 480 × 3m = 24h max persistence
    BTC_GUARANTEED_REENTRY_MIN_GAP_BARS: int = 5
    BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE: bool = True
    BTC_GUARANTEED_REENTRY_SIZE_MULT: float = 1.0

    # --- 20× leverage hard caps (CRITICAL — 2.5% loss = 50% account wipe) ---
    BTC_LEVERAGE: float = 20.0
    BTC_PER_TRADE_NOTIONAL_USD_MAX: float = 290.0                          # OWN-CAPITAL cap per trade. With 20× → $1,800 effective notional. Per user 2026-04-27.
    BTC_TOTAL_NOTIONAL_USD_MAX: float = 980.0                             # max concurrent OWN-capital across all BTC positions ($3,600 effective at 20×)
    BTC_HARD_LOSS_USD_PER_TRADE: float = 90.0                             # max $ loss per trade — implies ~0.55% adverse on $1,800 notional. Hard panic exit.
    BTC_DAILY_LOSS_PCT_FLOOR: float = -1.5                                # halt new entries if day PnL < -0.5%
    BTC_WEEKLY_LOSS_PCT_FLOOR: float = -2.5                               # halt all BTC trading 24h if week PnL < -1.5%
    BTC_PYRAMID_DISABLED: bool = True                                     # NO augmenting at 20×
    BTC_INTRABAR_REVERSAL_EXIT: bool = True                               # exit on accel sign-flip without TF confirm
    BTC_REGIME_PAUSE_ENABLED: bool = True                                 # pause new entries during BTC funding spike or extreme OI

    # --- Paper/live parity (HARD RULE per user 2026-04-27) ---
    BTC_PAPER_PARITY_VERIFY_AT_STARTUP: bool = True                       # check id() equality of decision functions paper-vs-live
    BTC_PAPER_RECONCILE_ALARM_DRIFT_PCT: float = 0.1                      # alarm if paper-live decision drift > 0.1% per day

    # --- BTC trade-pacing knobs (separate from generic crypto COOLDOWN/MIN_HOLD) ---
    BTC_COOLDOWN_BARS: int = 5                                            # min bars between exit and next entry consideration
    BTC_MIN_HOLD_BARS: int = 5                                            # min bars after entry before non-panic exit can fire

    # --- BREAKOUT entry mode (added 2026-04-27 — bounce-only missed big moves) ---
    BTC_BREAKOUT_ENTRY_ENABLED: bool = True                               # enable breakout entries alongside bounce
    BTC_BREAKOUT_DC_TF: str = "3m"                                        # DC channel TF for breakout detection
    BTC_BREAKOUT_ACCEL_MIN_TFS: int = 2                                   # looser than bounce default (3) — breakouts are momentum
    BTC_BREAKOUT_BLOCK_OPPOSING_DIV: bool = True                          # bear div still blocks LONG breakouts
    BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE: float = 5.0                     # tighter $ stop ($5 vs $10 bounce)
    BTC_BREAKOUT_MIN_HOLD_BARS: int = 3                                   # short min-hold — breakouts go fast or fail fast
    BTC_BREAKOUT_COOLDOWN_BARS: int = 3                                   # short cooldown after breakout exit
    BTC_BREAKOUT_REENTRY_ON_EXIT: bool = True                             # reenter same side after breakout exit
    BTC_BREAKOUT_REENTRY_REQUIRE_TREND: bool = True                       # reentry needs accel still aligned
    BTC_FOLLOW_THROUGH_REENTRY_ENABLED: bool = True                       # reentry past exit price even without RZ
    BTC_FOLLOW_THROUGH_MIN_MOVE_PCT: float = 0.3                          # min %% past exit price to trigger
    # ── Same-symbol HEDGE engine in v8_quick_engine BTC sim (2026-04-28) ──
    # Per user: when technicals (wt 3m + wt 15m + ≥1 of wt 1h/4h/D) turn against a primary
    # already in loss, open opposite-side hedge same notional. Hedge closes when wt 3m+1h
    # flip in hedge's favor + hedge gain ≥ 0 (per memory feedback_hedge_wt3m_close_absolute).
    BTC_HEDGE_SAMESYM_ENABLED: bool = False                               # default OFF — sweep validates before flipping live
    BTC_HEDGE_SAMESYM_TRIGGER_LOSS_PCT: float = -0.3                      # primary pnl ≤ this triggers hedge eligibility
    BTC_HEDGE_SAMESYM_REQUIRE_WT_3M: bool = True                          # wt1_3m against primary side required
    BTC_HEDGE_SAMESYM_REQUIRE_WT_15M: bool = True                         # wt1_15m against required
    BTC_HEDGE_SAMESYM_REQUIRE_HTF_TFS_MIN: int = 1                        # of {1h, 4h, D} how many wt against required
    BTC_HEDGE_SAMESYM_NOTIONAL_PCT: float = 1.0                           # 1.0 = same notional as primary (full delta-neutral)
    BTC_HEDGE_SAMESYM_CLOSE_REQUIRE_NONNEG_GAIN: bool = True              # only close hedge when hedge_pnl ≥ 0
    BTC_HEDGE_SAMESYM_CLOSE_REQUIRE_WT_3M_AND_1H: bool = True             # both wt 3m AND 1h must flip in hedge's favor
    BTC_HEDGE_SAMESYM_HEDGE_HARD_LOSS_PCT: float = -2.0                   # hedge hard stop (rare — hedge usually closes via WT flip)
    # HTF alignment for BREAKOUT — prevents buying breakouts INTO a downtrend
    BTC_BREAKOUT_REQUIRE_HTF_ALIGNED: bool = True                         # 2026-04-27: required after chart showed BK_L firing during clear bear leg
    BTC_BREAKOUT_HTF_MIN_ALIGNED: int = 2                                 # min HTFs (of 3 = 1h/4h/D) wt1>wt2 same direction
    # RZ semantics redesign (2026-04-27 user option 3): RZ no longer gates,
    # instead SOFTENS the accel-ramp requirement when active. With default fib+round+wt_dc
    # density, RZ was always-active → no-op as a gate. As softener it becomes meaningful:
    # entries near RZ levels can fire with fewer accel TFs aligned.
    BTC_RZ_AS_BOOST_ENABLED: bool = True                                  # True = softener mode (default), False = legacy gate
    BTC_RZ_SOFTEN_ACCEL_BY: int = 1                                       # min_tfs reduction when RZ active (0 = no effect, 1 = 1 fewer TF needed, ...)
    # Divergence redesign (2026-04-27 user: D = clockwork, 1h/4h testable, 3m/15m noise)
    BTC_DIVERGENCE_MIN_TF: str = "4h"                                     # only count divergence from this TF up
    BTC_DIVERGENCE_LB_3M: int = 5
    BTC_DIVERGENCE_LB_15M: int = 10
    BTC_DIVERGENCE_LB_1H: int = 20
    BTC_DIVERGENCE_LB_4H: int = 20
    BTC_DIVERGENCE_LB_D: int = 10
    BTC_DIVERGENCE_REQUIRE_D_CONFIRM_BARS: int = 2                        # D-div exits/blocks require N consecutive bars
    # Same-bar REVERSE-ON-EXIT — when exiting on bear/bull signal and opposite breakout fires, flip immediately
    BTC_REVERSE_ON_EXIT_ENABLED: bool = True                              # 2026-04-27: was missing reverse opportunities per chart audit
    BTC_REVERSE_REQUIRE_HTF_ALIGNED: bool = True

    # ════════════════════════════════════════════════════════════════════════════
    # 2026-05-26 — Vec-engine live-parity knobs.
    # Default OFF/0 preserves current vec behaviour bit-exactly. Enable for an A/B
    # sweep to measure CLOSE-vs-REDUCE structural-parity ΔSharpe.
    # ════════════════════════════════════════════════════════════════════════════
    # vec_paths/exit_to_reduce_adapter.py — convert vec CLOSE → REDUCE-labeled event.
    # 2026-05-26 22:00 BATCH 2 — user-clarified architecture (see
    # data/_diagnostic/REDUCE_VS_CLOSE_ARCHITECTURE.md):
    # every live REDUCE on the main Finandy webhook is a FULL CLOSE (state.qty→0).
    # Only PPL step 1 (via <acct>_WEBHOOK_URL2) is genuinely partial (50%).
    # Default frac=1.0 = REDUCE label + full close (same P&L as legacy CLOSE).
    VEC_LIVE_REDUCE_PARITY_ENABLED: bool = False
    VEC_LIVE_REDUCE_DEFAULT_FRAC: float = 1.0     # non-PPL exits → full close (REDUCE-labeled)
    VEC_LIVE_REDUCE_PPL_STEP1_FRAC: float = 0.5   # PPL step 1 → genuine 50% partial
    VEC_LIVE_REDUCE_PPL_REASONS: tuple = ("PARTIAL_PROFIT_LOCK_STEP1", "PPL_STEP1")
    VEC_REDUCE_CASCADE_COOLDOWN_S: float = 0.0    # 0 = OFF; live's Redis _recent_reduces floor (~15-60s)
    VEC_LIVE_REDUCE_PARITY_FRAC: float = 0.0      # legacy override (still respected if >0)
    VEC_LIVE_REDUCE_PARITY_KEEP_DUST: bool = False
    # vec_paths/ratio_reduce_sym_proxy.py — per-sym ratio-trim approximation
    VEC_RATIO_REDUCE_PROXY_ENABLED: bool = False
    RATIO_TRIM_MIN_INTERVAL_S: float = 14400.0   # 4h between proxy trims
    RATIO_TRIM_MIN_AGE_S: float = 7200.0          # 2h min age before first trim
    RATIO_TRIM_GAIN_FLOOR: float = 0.5            # trim when gain <= 0.5%
    # vec_paths/first_open_throttle.py — block first OPEN until bar_idx >= N
    VEC_FIRST_OPEN_THROTTLE_BARS: int = 0         # 0 = OFF
    # 2026-05-26 BATCH 3 — WT_CROSSUNDER_FINAL per-sym-side cooldown (vec only).
    # Live `_recent_reduces` Redis floor prevents this exit from firing >1×/3600s
    # per sym-side; live audit shows 0 fires. Vec emits 41,597 fires across 11
    # syms in 1yr without this gate. Default 0.0 = inert (baseline preservation).
    WT_CROSSUNDER_FINAL_COOLDOWN_S: float = 0.0
    # 2026-05-26 BATCH 3 — PPL fire cooldown (vec only) per sym-side.
    # Mirrors live's _recent_ppl_fires Redis key (~24h). Survives close/reopen
    # cycles because the cooldown lives on SymState, not on _pos (which resets).
    PPL_FIRE_COOLDOWN_S: float = 0.0
    # MIN_GAIN_TO_BUY_AGGRESSIVELY: vec/Tier2 UAG fallback. Crypto Config uses
    # MIN_GAIN=3.0 (line 59); UAG references MIN_GAIN_TO_BUY_AGGRESSIVELY but
    # falls back to 3.0 if absent. Mirror it explicitly for clarity.
    MIN_GAIN_TO_BUY_AGGRESSIVELY: float = 3.0
    # 2026-05-26 BATCH 4 — GR_OPEN per-sym-side cooldown (vec + Tier 2).
    # Live `_recent_opens` Redis floor: GR mult=1.0 OPEN-on-empty fires ≤22/yr/sym.
    # Vec emits 7,975 GR OPENs across 11 syms in 1yr without this gate — UAG only
    # covers AUGMENT, OPEN-on-empty bypasses it. Default 0.0 = inert (baseline preserved).
    # Mirror live: 3600.0 = 1h cooldown after every GR OPEN, persistent across close/reopen.
    GR_OPEN_COOLDOWN_S: float = 0.0
    # 2026-05-26 BATCH 4 — time-axis sibling to UAG. Live `_recent_augments` Redis
    # floor blocks repeat AUGMENT within 60-300s regardless of gain. UAG gates by
    # gain progression; AUG_COOLDOWN_S gates by time. Both required to mirror live.
    # Default 0.0 = inert (baseline preserved). Mirror live: 300.0 = 5-min floor.
    AUG_COOLDOWN_S: float = 0.0

    # 2026-05-27 BATCH 5 — top 20 LIVE_ONLY signals port (vec_paths/live_only_signals_batch5.py).
    # All knobs default OFF / 0 to preserve Arm A bit-exact baseline. These are
    # ALL vec-side knobs — live code does not read them (live's behavior is
    # already what the vec is now modeling).
    # Entry signals:
    HEDGE_PROTECT_LOSS_VEC_ENABLED: bool = False
    HEDGE_PROTECT_TRIGGER_GAIN_PCT: float = -0.5
    HEDGE_PROTECT_QTY_PCT: float = 1.0
    SYNTHETIC_LOSER_THRESHOLD_PCT: float = -2.0
    SYNTHETIC_LOSER_MIN_AGE_MIN: float = 30.0
    QUICK_OPEN_STRONG_VEC_ENABLED: bool = False
    QUICK_OPEN_STRONG_VEL_MIN: float = 1.0
    QUICK_OPEN_STRONG_K_LONG_MAX: float = 25.0
    QUICK_OPEN_STRONG_K_SHORT_MIN: float = 75.0
    QUICK_OPEN_STRONG_BB_LONG_MAX: float = 0.30
    QUICK_OPEN_STRONG_BB_SHORT_MIN: float = 0.70
    QUICK_OPEN_STRONG_DC_LONG_MAX: float = 0.40
    QUICK_OPEN_STRONG_DC_SHORT_MIN: float = 0.60
    QUICK_HEDGE_SAME_SYM_LAST_RESORT_VEC_ENABLED: bool = False
    QUICK_HEDGE_SAME_SYM_LAST_RESORT_GAIN_PCT: float = -3.0
    QUICK_HEDGE_SAME_SYM_LAST_RESORT_AGE_MIN: float = 240.0
    QUICK_HEDGE_SAME_SYM_LAST_RESORT_QTY_PCT: float = 1.0
    DAEMON_PRICE_CROSS_REENTRY_VEC_ENABLED: bool = False
    DAEMON_PRICE_CROSS_REENTRY_MAX_AGE_HOURS: float = 48.0
    DAEMON_PRICE_CROSS_PCT: float = 0.0
    GUARANTEED_PRICE_CROSS_REENTRY_DISK_VEC_ENABLED: bool = False
    DIRECTION_FAVORABLE_REENTRY_VEC_ENABLED: bool = False
    DIRECTION_FAVORABLE_MAX_MINUTES: float = 30.0
    # Exit signals:
    RIDICULOUS_HOLD_VEC_ENABLED: bool = False
    QUICK_REDUCE_STRONG_REDUCE_VEC_ENABLED: bool = False
    HLR_MIN_GAIN_PCT: float = 1.0
    HLR_MIN_TFS: int = 2
    HLR_REENTRY_MULT: float = 1.5
    HLR_REDUCE_FRAC: float = 0.5
    QUICK_BREAKEVEN_GAIN_EROSION_VEC_ENABLED: bool = False
    QUICK_CYCLE_TP_STOCH_AGAINST_VEC_ENABLED: bool = False
    QUICK_CYCLE_TP_MIN_GAIN_PCT: float = 1.0
    QUICK_CYCLE_TP_REDUCE_FRAC: float = 0.5
    QUICK_BANDAID_OFF_VEC_ENABLED: bool = False
    DELTA_EXIT_SPEED_DECAY_VEC_ENABLED: bool = False
    DELTA_EXIT_SPEED_DECAY_MIN_GAIN: float = 0.5
    DELTA_EXIT_SPEED_DECAY_MIN_TFS: int = 2
    QUICK_SENTIMENT_CUT_GAIN_VEC_ENABLED: bool = False
    QUICK_SENTIMENT_CUT_MIN_GAIN: float = 0.5
    QUICK_SENTIMENT_CUT_REDUCE_FRAC: float = 0.5
    HEDGE_BANDAID_OFF_FIRST_PRE_VEC_ENABLED: bool = False
    VEC_FIX_R1_REASON_STRING_FOR_DIFF: bool = False
    IN_GAIN_TREND_EXIT_LIVE_PARITY_ENABLED: bool = False
    IN_GAIN_TREND_REDUCE_FRAC: float = 0.5

    # 2026-05-27 BATCH 7 — STRUCTURAL PARITY GATES (mirror of v8_vec_sweep.py SweepConfig)
    # Vec-only knobs; live code does NOT read these. Default OFF preserves Arm A bit-exact.
    # See v8_vec_sweep.py header docs at SweepConfig.VEC_MTF_ARMED_STATE_ENABLED for details.
    VEC_MTF_ARMED_STATE_ENABLED: bool = False
    VEC_MTF_ARMED_GATE_REENTRY: bool = False
    VEC_MTF_ARMED_GATE_HEDGE_OPEN: bool = False
    VEC_MTF_ARMED_BYPASS_STRONG: bool = True
    VEC_MTF_ARMED_RESULTING_REASON: str = "MTF_NO_ARMED_STATE"
    VEC_MULTI_SYM_OUTER_LOOP_ENABLED: bool = False

    # ═══════════════════════════════════════════════════════════════════════════
    # 2026-05-28 STRICT VEC PARITY (user mandate) + EXECUTE_NOW SINGLE-GATE
    # ───────────────────────────────────────────────────────────────────────────
    # STRICT_VEC_PARITY_MODE: when True, execute_now() blocks every entry/exit
    #   whose reason is NOT achievable in the vectorized backtest engine (see
    #   vec_paths/vec_parity_gate.py allowlist). Live then trades ONLY the switches
    #   the vectorized per_sym backtest also trades -> closest reproducible parity.
    #   Default False = current full-live behavior (the "on / trade all signals" arm
    #   of the A/B). Flip True for the "off / parity-only" arm.
    # STRICT_VEC_PARITY_SHADOW: when True (and MODE False), execute_now LOGS every
    #   order it WOULD block but blocks nothing. Use this FIRST to validate the
    #   allowlist against real live reasons before enforcing. Zero behavior change.
    # EXECUTE_NOW_SINGLE_GATE_ENFORCE: when True (default), the direct-Finandy
    #   bypass in ez_positions_quick.py TrackerManager.send_webhook is re-routed
    #   through trade_manager.execute_now() (single gate). CLAUDE.md: execute_now
    #   is the ONLY order gate. Set False only to restore the prohibited legacy
    #   direct-post path (do not).
    # EXECUTE_NOW_WIRE_TRIPWIRE_SHADOW: when True (default), the broker-wire sites
    #   (futures_create_order in place_maker_order, Finandy session.post in
    #   send_webhook) emit a diagnostic log if they fire >N seconds after the last
    #   execute_now() entry — a future-bypass tripwire. Log-only, never blocks.
    # ═══════════════════════════════════════════════════════════════════════════
    STRICT_VEC_PARITY_MODE: bool = False
    STRICT_VEC_PARITY_SHADOW: bool = False
    STRICT_VEC_PARITY_GATE_ENTRIES: bool = True
    STRICT_VEC_PARITY_GATE_EXITS: bool = True
    EXECUTE_NOW_SINGLE_GATE_ENFORCE: bool = True
    EXECUTE_NOW_WIRE_TRIPWIRE_SHADOW: bool = True
    EXECUTE_NOW_WIRE_TRIPWIRE_MAX_LAG_S: float = 5.0

    # ═══════════════════════════════════════════════════════════════════════════
    # 2026-08-17 PORTED FROM TradierConfig — 885 switches for cross-asset sweep
    # Enables 900+ entry vs 900+ exit vs 120+ filter factorial in single Config
    # ═══════════════════════════════════════════════════════════════════════════
    ACCOUNT_SIDE_MAPPING: Dict[str, List[str]] = field(default_factory=lambda: {"tra": ["LONG"]})  # DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    AI_PREMARKET_DECISIONS_DIR: str = "data/ai_premarket"  # relative to BASE_PATH  # PORTED from TradierConfig 2026-08-17
    AI_PREMARKET_ENABLED_TRB: bool = False       # control — never inject into TRB  # PORTED from TradierConfig 2026-08-17
    AI_PREMARKET_ENABLED_TRC: bool = True        # paper — TRC = TRB + AI picks  # PORTED from TradierConfig 2026-08-17
    AI_PREMARKET_EXPIRES_ET: str = "20:00"       # advisory expires at market close same day  # PORTED from TradierConfig 2026-08-17
    AI_PREMARKET_MAX_NEW_PER_SIDE: int = 8       # cap new AI symbols per side per day  # PORTED from TradierConfig 2026-08-17
    AI_PREMARKET_MIN_CONVICTION: float = 0.55    # minimum LLM conviction to inject symbol  # PORTED from TradierConfig 2026-08-17
    AI_PREMARKET_SIZE_MULT_MAX: float = 1.5      # advisory size_override cap  # PORTED from TradierConfig 2026-08-17
    AI_PREMARKET_TRADINGVIEW_ENABLED: bool = True  # use TradingView MCP when available, fallback to local indicators  # PORTED from TradierConfig 2026-08-17
    ALIGNMENT_GATE_MIN: int = 4  # BACKTEST_CHANGE_T8 minimum indicators aligned  # PORTED from TradierConfig 2026-08-17
    ALIGNMENT_GATE_TOTAL: int = 36  # BACKTEST_CHANGE_T8 total alignment score required ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:8009,8187 alignment gate log denominator  # PORTED from TradierConfig 2026-08-17
    ATR_PARITY_EQUITY_BASE_USD: float = 35000.0             # nominal sleeve capital (50% of $70k trb+trc)  # PORTED from TradierConfig 2026-08-17
    ATR_PARITY_QTY_CAP_MULT: float = 5.0                    # cap qty at 5× DEFAULT (prevents runaway low-vol sizes)  # PORTED from TradierConfig 2026-08-17
    ATR_PARITY_TARGET_RISK_PCT: float = 0.20                # % of equity risked per trade (0.20 = aggressive)  # PORTED from TradierConfig 2026-08-17
    ATR_PARITY_USE_DAILY: bool = True                       # True=atr_D (audited-winner standard), False=atr_5m  # PORTED from TradierConfig 2026-08-17
    ATR_TRAIL_2X_EXIT_ENABLED: bool = False  # BACKTEST_CHANGE_T58: was True. ATR trail = #1 stock PnL destroyer (-2557% cumulative). Disabled.  # PORTED from TradierConfig 2026-08-17
    ATR_TRAIL_ENABLED_TRADIER: bool = False  # BACKTEST_CHANGE_T58: was True. ATR trailing stop = #1 stock PnL destroyer (-2557%). Disabled. ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    AUGMENTATION_COOLDOWN_SECONDS: float = 300.0  # PORTED from TradierConfig 2026-08-17
    AUGMENT_PYRAMID_TRADIER: bool = False  # BACKTEST_CHANGE_T60: Pyramiding barely fires on stocks (0-10 trades). Disabled. ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    BACKTEST_VALIDATED_GATES_TRADIER: bool = True  # Block entries on signals confirmed -EV on both train+test  # PORTED from TradierConfig 2026-08-17
    BAND_ARROW_ACCUMULATE: bool = True            # every green arrow adds while in-trend  # PORTED from TradierConfig 2026-08-17
    BAND_ARROW_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    BAND_ARROW_ENTRY_TFS: str = "D,4h,1h"          # buy a green arrow on any of these  # PORTED from TradierConfig 2026-08-17
    BAND_ARROW_EXIT_TFS: str = "D,4h"              # sell a red arrow on any of these (test +1h)  # PORTED from TradierConfig 2026-08-17
    BAND_ARROW_MAX_POS_MULT: float = 30.0         # cap total exposure at N x START_POSITION_SIZE  # PORTED from TradierConfig 2026-08-17
    BAND_ARROW_SLOPE_DEADBAND: float = 0.0        # |slope| must exceed this to count as an arrow  # PORTED from TradierConfig 2026-08-17
    BB_PULLBACK_GATE_ENABLED: bool = True  # 2026-05-23: SWEEP WINNER — ΔSharpe +0.0056 vs baseline, only positive arm  # PORTED from TradierConfig 2026-08-17
    BB_PULLBACK_GATE_LONG_MAX: float = 0.30  # PORTED from TradierConfig 2026-08-17
    BB_PULLBACK_GATE_SHORT_MIN: float = 0.70  # PORTED from TradierConfig 2026-08-17
    BB_PULLBACK_GATE_TF: str = '15m'  # PORTED from TradierConfig 2026-08-17
    BB_RECOVERY_DIRECT_BARS: int = 1  # PORTED from TradierConfig 2026-08-17
    BB_RECOVERY_DIRECT_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    BB_RECOVERY_DIRECT_MIN_EXCURSION_ATR: float = 0.5  # PORTED from TradierConfig 2026-08-17
    BB_RECOVERY_DIRECT_TIMEFRAME: str = "1h"  # PORTED from TradierConfig 2026-08-17
    BB_RECOVERY_EXIT_ENABLED_TRADIER: bool = True  # 2026-04-20 sweep: unlocks stranded positions stuck above bb_1h  # PORTED from TradierConfig 2026-08-17
    BB_RECOVERY_EXIT_TOLERANCE_ATR_MULT_TRADIER: float = 0.0  # if >0, uses N * atr_3m instead of pct  # PORTED from TradierConfig 2026-08-17
    BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER: float = 0.30  # stock pct tolerance around entry_price  # PORTED from TradierConfig 2026-08-17
    BB_RSI_STOCH_BB_MAX: float = 0.30  # 2026-05-23: was 0.2  # PORTED from TradierConfig 2026-08-17
    BB_RSI_STOCH_K_MAX: float = 30.0  # 2026-05-23: was 20  # PORTED from TradierConfig 2026-08-17
    BB_RSI_STOCH_RSI_MAX: float = 40.0  # 2026-05-23: was 30  # PORTED from TradierConfig 2026-08-17
    BB_RSI_STOCH_SCALP_TF: str = '15m'  # 2026-05-23: was hardcoded 5m  # PORTED from TradierConfig 2026-08-17
    BOTTOM_A_PROTECTIVE_TRAIL_ARM_TIMEFRAME: str = "4h"  # PORTED from TradierConfig 2026-08-17
    BOTTOM_A_PROTECTIVE_TRAIL_BREAK_BUFFER_ATR: float = 0.5  # PORTED from TradierConfig 2026-08-17
    BOTTOM_A_PROTECTIVE_TRAIL_DISTANCE_MULT: float = 1.0  # PORTED from TradierConfig 2026-08-17
    BOTTOM_A_PROTECTIVE_TRAIL_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    BOTTOM_A_PROTECTIVE_TRAIL_LOOKBACK: int = 6  # PORTED from TradierConfig 2026-08-17
    BOTTOM_A_PROTECTIVE_TRAIL_MODE: str = "STDEV"  # PORTED from TradierConfig 2026-08-17
    BOTTOM_A_PROTECTIVE_TRAIL_TRAIL_TIMEFRAME: str = "5m"  # PORTED from TradierConfig 2026-08-17
    BOTTOM_B_DELAYED_LOWER_TOP_ARM_BREAK_MODE: str = "ATR"  # PORTED from TradierConfig 2026-08-17
    BOTTOM_B_DELAYED_LOWER_TOP_ARM_BREAK_THRESHOLD: float = 0.25  # PORTED from TradierConfig 2026-08-17
    BOTTOM_B_DELAYED_LOWER_TOP_ARM_TF: str = "4h"  # PORTED from TradierConfig 2026-08-17
    BOTTOM_B_DELAYED_LOWER_TOP_CONFIRMATION_BARS: int = 1  # PORTED from TradierConfig 2026-08-17
    BOTTOM_B_DELAYED_LOWER_TOP_CONFIRMATION_MODE: str = "lower_top"  # PORTED from TradierConfig 2026-08-17
    BOTTOM_B_DELAYED_LOWER_TOP_CONFIRM_TF: str = "1h"  # PORTED from TradierConfig 2026-08-17
    BOTTOM_B_DELAYED_LOWER_TOP_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    BOTTOM_B_DELAYED_LOWER_TOP_MAX_WAIT_1H: int = 12  # PORTED from TradierConfig 2026-08-17
    BOTTOM_B_DELAYED_LOWER_TOP_PREBREAK_LOOKBACK: int = 20  # PORTED from TradierConfig 2026-08-17
    BOTTOM_B_DELAYED_LOWER_TOP_REBOUND_ATR: float = 0.5  # PORTED from TradierConfig 2026-08-17
    BOUNCE_TOP_REENTRY_MULT: float = 1.5  # 2026-04-26 WIRED — tradier_manage.py:5525, 5544 (divergence reentry size mult, was hardcoded 1.5). Previously DEAD_CONFIRMED (priority 70).  # PORTED from TradierConfig 2026-08-17
    BOUNCE_TOP_RISING_CROSS_MULT: float = 2.0  # 2026-04-26 WIRED — tradier_manage.py:5531, 5550 (rising/falling WT cross reentry size mult, was hardcoded 1.5; raised default to 2.0 to match config intent). Previously DEAD_CONFIRMED (priority 70).  # PORTED from TradierConfig 2026-08-17
    BREAKEVEN_EXIT_AFTER_BARS: int = 8  # PORTED from TradierConfig 2026-08-17
    BREAKEVEN_EXIT_AFTER_BARS_BUFFER_PCT: float = 0.05  # PORTED from TradierConfig 2026-08-17
    BREAKEVEN_EXIT_AFTER_BARS_ENABLED: bool = True  # USER 2026-08-07: BE ratchet default ON — "stop to zero once 15m higher-low achieved"; may only leave defaults if a receipt beats it without it (Bible §16.63)  # PORTED from TradierConfig 2026-08-17
    BREAKEVEN_EXIT_AFTER_BARS_TF: str = '15m'  # PORTED from TradierConfig 2026-08-17
    BREAKEVEN_EXIT_REQUIRE_WT15M_STRUCTURE: bool = True  # PORTED from TradierConfig 2026-08-17
    BREAKOUT_SIZE_EMA200_T1_MULT: float = 1.5  # PORTED from TradierConfig 2026-08-17
    BREAKOUT_SIZE_EMA200_T1_PCT: float = 1.0  # PORTED from TradierConfig 2026-08-17
    BREAKOUT_SIZE_EMA200_T2_MULT: float = 2.0  # PORTED from TradierConfig 2026-08-17
    BREAKOUT_SIZE_EMA200_T2_PCT: float = 1.5  # PORTED from TradierConfig 2026-08-17
    BREAKOUT_SIZE_EMA200_T3_MULT: float = 3.0  # PORTED from TradierConfig 2026-08-17
    BREAKOUT_SIZE_EMA200_T3_PCT: float = 2.5  # PORTED from TradierConfig 2026-08-17
    BREAKOUT_TF_SIZE_MULT_5M: float = 0.5  # PORTED from TradierConfig 2026-08-17
    BROKER_PREFLIGHT_CACHE_S: float = 3.0                     # cache the broker snapshot this long to avoid rate-limit (≤5s per user mandate)  # PORTED from TradierConfig 2026-08-17
    BROKER_PREFLIGHT_ENABLED: bool = True                     # tradier_manage hits Tradier's /positions itself before every entry order  # PORTED from TradierConfig 2026-08-17
    BROKER_PREFLIGHT_MAX_SAME_SIDE_QTY: float = 50.0          # refuse further entries on same side if broker already holds ≥ this many shares  # PORTED from TradierConfig 2026-08-17
    CATALYST_VOLUME_GATE_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    CATALYST_VOLUME_RATIO: float = 1.5  # PORTED from TradierConfig 2026-08-17
    CLENOW_ENABLED: bool = False  # DISABLED 2026-03-30: fake backtest Sharpe. Needs V5 validation.  # PORTED from TradierConfig 2026-08-17
    CLENOW_GATE_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    CLENOW_GATE_MIN_SCORE: float = 30.0       # slope_ann × R² (renamed from CLENOW_MIN_SCORE — collided with existing Clenow strategy param at line 1044)  # PORTED from TradierConfig 2026-08-17
    CLENOW_LOOKBACK: int = 90  # Regression window (Clenow default) ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    CLENOW_MIN_SCORE: float = 5.0  # Min score (slope * R²) to qualify  # PORTED from TradierConfig 2026-08-17
    CLENOW_POSITION_SIZE: float = 800.0  # Per-entry size  # PORTED from TradierConfig 2026-08-17
    CLENOW_REBALANCE_DAYS: int = 21  # Monthly rebalance  # PORTED from TradierConfig 2026-08-17
    CLENOW_REGIME_FILTER: bool = True  # Only hold when SPY > SMA200  # PORTED from TradierConfig 2026-08-17
    CLENOW_TOP_N: int = 20  # Buy top N% of ranked symbols  # PORTED from TradierConfig 2026-08-17
    CLOSE_ZONE_SIZE_MULT: float = 1.5  # BACKTEST_CHANGE_T23 size multiplier in close zone  # PORTED from TradierConfig 2026-08-17
    COMBINED_STOCH_GATE_TRADIER: float = 60.0  # CLAUDE.md stocks=60 (was 40 — sub-crypto value; fixed 2026-05-27). ROLLBACK: 40.0  # PORTED from TradierConfig 2026-08-17
    COMPLETED_CANDLE_SNAPSHOT_DIRECT_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    CONFLUENCE_MIN_BLOCKS: int = 2  # PORTED from TradierConfig 2026-08-17
    CONFLUENCE_MODE_ENABLED: bool = False  # vector 1177: N blocks must agree  # PORTED from TradierConfig 2026-08-17
    CONGRESS_CONVICTION_MIN_SOURCES: int = 2  # PORTED from TradierConfig 2026-08-17
    CONGRESS_CONVICTION_SIZING_BOOST: float = 1.3  # PORTED from TradierConfig 2026-08-17
    CONNORS_RSI2_EXIT_SMA_BARS_DAILY: int = 5  # PORTED from TradierConfig 2026-08-17
    CONNORS_RSI2_PRIORITY_OVERRIDE_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    CONNORS_RSI2_REQUIRE_ABOVE_200SMA: bool = True  # PORTED from TradierConfig 2026-08-17
    CONNORS_RSI2_THRESHOLD: float = 10.0                    # connors_rsi composite (NPZ field connors_rsi_D); <10 = oversold  # PORTED from TradierConfig 2026-08-17
    CONNORS_RSI2_TIME_STOP_BARS_DAILY: int = 10             # max hold = 10 trading days  # PORTED from TradierConfig 2026-08-17
    CONNORS_RSI_ENABLED: bool = False  # DISABLED 2026-03-30: augmented MRVL at -6.74% on real money. Needs V5 validation.  # PORTED from TradierConfig 2026-08-17
    CONNORS_RSI_ENTRY_THRESHOLD: float = 10.0  # Buy when CRSI < 10  # PORTED from TradierConfig 2026-08-17
    CONNORS_RSI_EXIT_THRESHOLD: float = 70.0  # Sell when CRSI > 70  # PORTED from TradierConfig 2026-08-17
    CONNORS_RSI_MAX_HOLD_DAYS: int = 20  # DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    CONNORS_RSI_POSITION_SIZE: float = 600.0  # PORTED from TradierConfig 2026-08-17
    CONVICTION_SHORT_THRESHOLD: int = 20  # BACKTEST_CHANGE_T9 min conviction score for short entries  # PORTED from TradierConfig 2026-08-17
    COOLDOWN_BARS_TRADIER: int = 8  # 2026-04-08 SWEEP: 8 bars (40min) → Sharpe 8.22 (+1.30 vs 0 cooldown). Was 16 (80min). ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    CRYPTO_ROUND_TRIP_COMMISSION_PCT: float = 0.0  # tradier commission-free; crypto 0.08% only  # PORTED from TradierConfig 2026-08-17
    DC_BREAK_GR_MULT_BREAKOUT: float = 0.1          # Phase 1: tiny entry on DC break  # PORTED from TradierConfig 2026-08-17
    DC_BREAK_GR_MULT_ENABLED: bool = False          # P2-C: route DC_BREAK entries through GR Phase 1/2 sizing  # PORTED from TradierConfig 2026-08-17
    DC_BREAK_GR_MULT_RETEST: float = 3.0            # Phase 2: large entry on dc_basis retest + WT confirm  # PORTED from TradierConfig 2026-08-17
    DC_BREAK_GR_RETEST_TOLERANCE_PCT: float = 0.3   # dc_basis within this % = retest zone  # PORTED from TradierConfig 2026-08-17
    DC_BREAK_LOW_REQUIRE_HTF_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    DC_BREAK_LOW_REQUIRE_HTF_MIN_TFS: int = 2  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_ACCOUNT: str = "trb"  # Account for daytrade positions  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_BUFFER: float = 0.001  # Min % outside channel to confirm break (0.1%)  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_ENABLED: bool = True  # Enable DC breakout daytrade system (parallel to HODL)  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_K_EXHAUSTED_LONG: float = 85.0  # Don't go long if 15m K > this (chasing)  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_K_EXHAUSTED_SHORT: float = 15.0  # Don't go short if 15m K < this (chasing)  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_LONG_BUDGET: float = 3000.0  # Max $ exposure in daytrade longs  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_MAX_HOLD_MINUTES: float = 240.0  # 4h max hold (flatten before close regardless)  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_MAX_PER_SIDE: int = 5  # Max concurrent daytrade positions per side  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_MAX_POSITION_SIZE: float = 1000.0  # was 2000 — 2026-04-27 emergency halve  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_PRE_CLOSE_MINUTES: int = 120  # Start flattening 2h before market close (14:00 ET)  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_REQUIRE_1H_EXPANSION: bool = True  # Only trade DC breaks when 1h channel is expanding in same direction  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_SHORT_BUDGET: float = 3000.0  # Max $ exposure in daytrade shorts  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_START_SIZE: float = 600.0  # Base order value per daytrade entry  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_STOCH_FILTER: bool = True  # Require stoch not exhausted in entry direction  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_STOP_PCT: float = 0.015  # 1.5% hard stop for daytrades  # PORTED from TradierConfig 2026-08-17
    DC_DAYTRADE_TARGET_PCT: float = 0.01  # 1% profit target  # PORTED from TradierConfig 2026-08-17
    DC_ENTRY_VETO_ENABLED_TRADIER: bool = False  # SENTINEL_FIX 2026-04-14: when True, DC_POSITION_ENTRY_THRESHOLD gates entries (require dc_pos in zone). Default False = live unchanged.  # PORTED from TradierConfig 2026-08-17
    DC_LOW_FROZEN_STOP_ENABLED: bool = False       # master switch; sweep variants set True + TF  # PORTED from TradierConfig 2026-08-17
    DC_LOW_FROZEN_STOP_FLOOR_PCT: float = -999.0  # abs loss floor; -999 = off  # PORTED from TradierConfig 2026-08-17
    DC_LOW_FROZEN_STOP_TF: str = '4h'             # TF to freeze: '5m','15m','1h','4h','D' (D added 2026-07-18 — engine reads dc_low_{tf} generically, NPZ has D)  # PORTED from TradierConfig 2026-08-17
    DC_LOW_FROZEN_STOP_USE_4BAR: bool = False      # True=dc_low4_{tf} (4-bar tight), False=dc_low_{tf} (20-bar)  # PORTED from TradierConfig 2026-08-17
    DC_POSITION_ENTRY_THRESHOLD: float = 0.25  # REVERTED 2026-04-17: 0.15 was too tight. Mar-30 baseline 0.25 = Sharpe 18.57 on 61 stocks.  # PORTED from TradierConfig 2026-08-17
    DC_TIER4_BAR_MATURITY_BLOCK: float = 0.7  # PORTED from TradierConfig 2026-08-17
    DC_TIER4_BAR_MATURITY_BLOCK_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    DC_TIER_AUG_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    DD_KELLY_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    DD_KELLY_TIER1_PCT: float = 10.0       # at -10% DD, size × 0.5  # PORTED from TradierConfig 2026-08-17
    DD_KELLY_TIER2_PCT: float = 15.0       # at -15% DD, size × 0.25  # PORTED from TradierConfig 2026-08-17
    DD_KELLY_TIER3_PCT: float = 20.0       # at -20% DD, size × 0.125  # PORTED from TradierConfig 2026-08-17
    DELTA_EXIT_REENTRY_COOLDOWN_MIN: float = 45.0   # block DELTA_EXIT for 45min after reentry fill (USER 2026-06-22); Rollback: 0  # PORTED from TradierConfig 2026-08-17
    DELTA_EXIT_REQUIRE_NONZERO_SCORE: bool = True  # 2026-06-02 USER MANDATE: refuse DELTA_EXIT_BASELINE closes that fire with ALL-ZERO scores (bs=0/es=0/btf=0/etf=0) — 57 such 0-signal closes seen in /history burning commissions at ~0% gain. When True, a delta exit only fires if it carries a real bull/bear speed or TF count. Gate: tradier_manage.py ~6513. ROLLBACK: False.  # PORTED from TradierConfig 2026-08-17
    DELTA_EXIT_TYPE: str = "speed_decay"  # WINNER ST: speed_decay sp=70 Sharpe 0.710 WR 77.7% (corrected from wt_cross) ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    DELTA_LT_COOLDOWN_BARS: int = 120  # ~10h ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    DELTA_LT_ENTRY_ACCEL_THRESHOLD: float = 0.0  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    DELTA_LT_EXIT_SPEED_PCT: int = 50  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    DELTA_LT_EXIT_TYPE: str = "combined_wt_speed"  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    DELTA_LT_HTF_GATE: str = "4h_D"  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    DELTA_OPTIONS_COOLDOWN: int = 120  # 10h between trades ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    DELTA_OPTIONS_ENTRY_Z: float = 3.0  # Stricter: ez=3.0 for options (wider spreads) ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    DELTA_OPTIONS_EXIT_TYPE: str = "giveback"  # V2 sweep: giveback wins for options ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    DELTA_OPTIONS_GIVEBACK_PCT: float = 30.0  # Close when 30% of max gain given back ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    DELTA_OPTIONS_HTF_GATE: str = "4h_D"  # Both 4h AND D must confirm ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    DELTA_OPTIONS_MAX_HOLD: int = 240  # 20h max hold ; DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    DELTA_TF_WEIGHTS_STOCK: Optional[dict] = None  # set in __post_init__  # PORTED from TradierConfig 2026-08-17
    DG_BROKER_MEMORY_SYNC_BLOCK: bool = True                  # Control 6: if broker amt>0 but local memory has no position → REFUSE further opens for that key  # PORTED from TradierConfig 2026-08-17
    DG_DAILY_GAIN_BLOCK_SHORT_PCT: float = 2.5                # Control 1: refuse SHORT entry if symbol is up ≥ this % today  # PORTED from TradierConfig 2026-08-17
    DG_DAILY_LOSS_BLOCK_LONG_PCT: float = 2.5                 # Control 2: refuse LONG entry if symbol is down ≥ this % today  # PORTED from TradierConfig 2026-08-17
    DG_HIGH_VOLATILITY_ATR_PCT: float = 4.0                   # Control 9: if (atr_1h / price) * 100 ≥ this, refuse force-opens (volatile day = false 3m crosses)  # PORTED from TradierConfig 2026-08-17
    DG_HTF_ALIGN_REQUIRE_1H: bool = False                     # Control 3c: 1h close vs prev 1h close (default OFF — too noisy)  # PORTED from TradierConfig 2026-08-17
    DG_HTF_ALIGN_REQUIRE_4H: bool = True                      # Control 3b: 4h close vs prev 4h close must agree with side  # PORTED from TradierConfig 2026-08-17
    DG_HTF_ALIGN_REQUIRE_D: bool = True                       # Control 3a: D close vs prev_close must agree with side  # PORTED from TradierConfig 2026-08-17
    DG_MAX_FORCE_OPEN_NOTIONAL_USD: float = 4000.0  # 2026-06-03 USER: was 500 — clamped the with-trend build; raised so above-sma200 winners can size up. Direction guards (DG_DAILY_GAIN/LOSS) still block shorting winners/longing losers.             # Control 5: WT_3M_FORCE_OPEN must NEVER size above this $ per fire  # PORTED from TradierConfig 2026-08-17
    DG_MOMENTUM_BLOCK_RSI15M_FOR_LONG: float = 35.0           # Control 4b: refuse LONG if rsi_15m ≤ this (catch falling knife)  # PORTED from TradierConfig 2026-08-17
    DG_MOMENTUM_BLOCK_RSI15M_FOR_SHORT: float = 65.0          # Control 4a: refuse SHORT if rsi_15m ≥ this  # PORTED from TradierConfig 2026-08-17
    DG_MOMENTUM_BLOCK_RSI1H_FOR_LONG: float = 35.0            # Control 4d: refuse LONG if rsi_1h ≤ this  # PORTED from TradierConfig 2026-08-17
    DG_MOMENTUM_BLOCK_RSI1H_FOR_SHORT: float = 65.0           # Control 4c: refuse SHORT if rsi_1h ≥ this  # PORTED from TradierConfig 2026-08-17
    DG_OPPOSITE_SIDE_PROFIT_BLOCK_PCT: float = 1.0            # Control 7: if opposite side has gain ≥ this %, block this side opening  # PORTED from TradierConfig 2026-08-17
    DG_REPEAT_OPEN_PER_DAY_MAX: int = 60                       # Control 8: cap opens per pos_key per session-day to this many fires of WT_3M_FORCE_OPEN  # PORTED from TradierConfig 2026-08-17
    DG_SMA200_SHORT_BYPASS: bool = True                       # Controls 3/4h/10/11 bypass when price < sma_200_15m — structural bear overrides candle-color gates  # PORTED from TradierConfig 2026-08-17
    DG_WT_3M_REQUIRE_HTF_CONFIRM: bool = True                 # Control 10: WT_3M_FORCE_OPEN requires at least D OR 4h agreeing with intended side  # PORTED from TradierConfig 2026-08-17
    DISASTER_GUARD_ENABLED: bool = True                       # master switch — never let this off without explicit user override  # PORTED from TradierConfig 2026-08-17
    DT_TARGET_ATR_ENABLED: bool = False  # REVERTED 2026-05-18 18:30 (was True since 2026-05-17). Flip had no sample-floor proof; isolated vec sweep queued.  # PORTED from TradierConfig 2026-08-17
    DYNAMIC_SCORE_COUNTER_EXIT_ENABLED: bool = True  # 2026-04-20 le_dynamic winner: exit when opposite-direction LE score >= threshold  # PORTED from TradierConfig 2026-08-17
    DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD: float = 55.0  # 2026-04-20 le_dynamic winner: counter-exit trigger threshold (score=55 validated)  # PORTED from TradierConfig 2026-08-17
    D_TREND_REQUIRED: bool = True  # vector: ha_D alignment required  # PORTED from TradierConfig 2026-08-17
    EARNINGS_AVOIDANCE_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    EARNINGS_BLACKOUT_DAYS_AFTER: int = 1      # T+1 still blackout (drift unclear early)  # PORTED from TradierConfig 2026-08-17
    EARNINGS_BLACKOUT_DAYS_BEFORE: int = 1     # T-1 blackout  # PORTED from TradierConfig 2026-08-17
    EARNINGS_FORCE_TRIM_PCT: float = 0.5        # 50% trim T-1 close  # PORTED from TradierConfig 2026-08-17
    EARNINGS_PEAD_BOOST_ENABLED: bool = False  # post-earnings-drift overlay (start OFF)  # PORTED from TradierConfig 2026-08-17
    EARNINGS_PEAD_BOOST_MULT: float = 1.5  # PORTED from TradierConfig 2026-08-17
    EARNINGS_PEAD_MIN_SURPRISE_PCT: float = 4.0  # PORTED from TradierConfig 2026-08-17
    EMA_9_21_FILTER_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    EMA_9_21_SCORE_BONUS: int = 5  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    EMA_9_21_TIMEFRAME: str = "5m"  # PORTED from TradierConfig 2026-08-17
    EMERGENCY_BRAKE_DC_STOP_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    EMERGENCY_BRAKE_DC_STOP_FIELD: str = 'dc_low_15m'  # PORTED from TradierConfig 2026-08-17
    ENABLE_IP_ROTATION: bool = False  # NOT DEAD_CONFIRMED (priority 15/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_BOUNCE_DISTANCE: float = 0.015  # PORTED from TradierConfig 2026-08-17
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_BOUNCE_TIMEFRAME: str = "5m"  # PORTED from TradierConfig 2026-08-17
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_CONFIRMATION_MIN: int = 2  # PORTED from TradierConfig 2026-08-17
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_DEEP_K4H: float = 50.0  # PORTED from TradierConfig 2026-08-17
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SIDE: str = "SHORT"  # PORTED from TradierConfig 2026-08-17
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SYMBOLS: tuple[str, ...] = ("WDAY",)  # PORTED from TradierConfig 2026-08-17
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_TURN_K1H: float = 40.0  # PORTED from TradierConfig 2026-08-17
    ENTRY_BOUNCE_DONCHIAN_DIRECT_CONFIRMATION: str = "none"  # PORTED from TradierConfig 2026-08-17
    ENTRY_BOUNCE_DONCHIAN_DIRECT_DISTANCE: float = 0.008  # PORTED from TradierConfig 2026-08-17
    ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    ENTRY_BOUNCE_DONCHIAN_DIRECT_RECOVERY_ONLY: bool = False  # PORTED from TradierConfig 2026-08-17
    ENTRY_BOUNCE_DONCHIAN_DIRECT_TIMEFRAME: str = "5m"  # PORTED from TradierConfig 2026-08-17
    ENTRY_MIN_ALIGNMENT: int =             5      # 2026-06-09: lowered 10→5 (10=impossible, max score=10 but alignment=5/10 was blocking good trades). Was 8→10. ROLLBACK: 8.  # PORTED from TradierConfig 2026-08-17
    ENTRY_PRIMARY_TF: str =                '4h'   # BACKTEST_CHANGE_T7 was 1h → 4h slower primary TF  # PORTED from TradierConfig 2026-08-17
    ENTRY_STOCH_HHHL_DIRECT_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    ENTRY_STOCH_HHHL_DIRECT_MIN_CONFIRMING_TFS: int = 1  # PORTED from TradierConfig 2026-08-17
    ENTRY_STOCH_HHHL_DIRECT_STOCH_THRESHOLD: float = 20.0  # PORTED from TradierConfig 2026-08-17
    ENTRY_STOCH_HHHL_DIRECT_TFS: list[str] = field(default_factory=lambda: ["1h"])  # PORTED from TradierConfig 2026-08-17
    ENTRY_STOCH_PARENT_DIRECT_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    ENTRY_STOCH_PARENT_DIRECT_FAMILY: str = "ENTRY_1H_TURN_UP"  # PORTED from TradierConfig 2026-08-17
    ENTRY_STOCH_PARENT_DIRECT_THRESHOLD: float = 40.0  # PORTED from TradierConfig 2026-08-17
    ENTRY_STOCH_PARENT_DIRECT_TURN_DEFINITION: str = "rising-vs-prior"  # PORTED from TradierConfig 2026-08-17
    ENTRY_TRIGGER_TF: str =                '15m'  # Trigger TF for crossover (was 5m, shifted to 15m for stocks) ; WIRED 2026-04-16 (priority 92/100) — tradier_manage.py:2680 referenced in entry eval  # PORTED from TradierConfig 2026-08-17
    ENTRY_ZONE_LONG: float =               80.0   # 2026-04-27 LOOSENED from 35 — was blocking 65% of long entries. Now permissive: longs allowed when k<80.  # PORTED from TradierConfig 2026-08-17
    ENTRY_ZONE_SHORT: float =              20.0   # 2026-04-27 FIX TYPO — was 100.0 (always-block bug from 2026-04-16 wiring task; comment said 100-35=65 but value typed wrong). Loosened to 20: shorts allowed when k>20.  # PORTED from TradierConfig 2026-08-17
    EOD_RATIO_ENFORCE_TRADIER: bool = False  # BACKTEST_CHANGE_147: Scale down entries 30min before close, block at 5min  # PORTED from TradierConfig 2026-08-17
    EOD_SLIM_RATIO_ENABLED: bool = False  # 2026-06-02 USER MANDATE: OFF. last_hour_balancing_loop EOD_SLIM_RATIO trim fires at any gain (incl ~0%), LIVE-ONLY rebalance not modeled in backtest. ROLLBACK: True.  # PORTED from TradierConfig 2026-08-17
    EPISODIC_PIVOT_ENABLED: bool = False  # OFF for trb. TRC overrides to True.  # PORTED from TradierConfig 2026-08-17
    EP_MAX_CONSOLIDATION_DAYS: int = 8  # DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    EP_MAX_RETRACE_PCT: float = 25.0  # DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    EP_MIN_GAP_PCT: float = 5.0  # DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    EP_MIN_VOL_MULT: float = 3.0  # DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    EP_POSITION_SIZE: float = 800.0  # PORTED from TradierConfig 2026-08-17
    EXIT_ALGO_SCORE_ENABLED: bool = False        # Old calculate_signal_score exit. Bypassed scorer, closed PLTR.  # PORTED from TradierConfig 2026-08-17
    EXIT_BOUNCE_TOP_ENABLED: bool = False        # Bounce-top loss exit. Percentage-based in disguise.  # PORTED from TradierConfig 2026-08-17
    EXIT_CONV_FAIL_ENABLED: bool = False         # Convergence failure early exit. Was closing at tiny gains.  # PORTED from TradierConfig 2026-08-17
    EXIT_HARD_DROP_5M_ENABLED: bool = False      # Price < prev 5m low. Too aggressive — kills options on minor dips.  # PORTED from TradierConfig 2026-08-17
    EXIT_HTF_QUICK_TP_ENABLED: bool = True       # HTF Quick TP: 1h exhausted + LTFs turning + 4h intact. KEEP — proven.  # PORTED from TradierConfig 2026-08-17
    EXIT_IBS_EXHAUSTION_ENABLED: bool = False    # Internal Bar Strength extreme. Minor signal, not worth standalone exit.  # PORTED from TradierConfig 2026-08-17
    EXIT_K5M_BOUNCE_ENABLED: bool = False       # K5M stoch bounce turn + low break. Was closing on 5m noise.  # PORTED from TradierConfig 2026-08-17
    EXIT_MAX_HOLD_ENABLED: bool = False          # Max hold timeout. OFF — technicals decide, not clocks.  # PORTED from TradierConfig 2026-08-17
    EXIT_MAX_HOLD_MINUTES: float = 99999         # If enabled: max minutes before force-close.  # PORTED from TradierConfig 2026-08-17
    EXIT_MI_ENABLED: bool = False                # Momentum Interception sub-signals. Tested: marginal value.  # PORTED from TradierConfig 2026-08-17
    EXIT_ON_ALL: bool = True  # PORTED from TradierConfig 2026-08-17
    EXIT_SENTIMENT_ENABLED: bool = False         # Sentiment collapse exit. Unreliable signal source. ; WIRED 2026-04-16 (priority 60/100) — tradier_manage.py:4149 exit guard  # PORTED from TradierConfig 2026-08-17
    EXIT_STRUCT_BREAK_5M_ENABLED: bool = False   # 5m LH/HL structure exit. Too noisy for swing/options.  # PORTED from TradierConfig 2026-08-17
    EXIT_STRUCT_DC_BREAK_ENABLED: bool = True    # DC structural break (multi-TF). KEEP — catches real breakdowns.  # PORTED from TradierConfig 2026-08-17
    FAVORABLE_SLOPE_HOLD_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    FH_MOMENTUM_DC_CONFIRM: bool = True  # DC retest logic handles smart filtering now  # PORTED from TradierConfig 2026-08-17
    FH_MOMENTUM_DC_MAX_LONG: float = 0.5  # Sweep: 0.25-1.0 all Sharpe>1.36. 0.5 = balanced.  # PORTED from TradierConfig 2026-08-17
    FH_MOMENTUM_ENABLED: bool = True  # VALIDATED: Sharpe 1.54, +100% PnL, 25/25 configs profitable. V8 T10 sweep 2026-04-07.  # PORTED from TradierConfig 2026-08-17
    FH_MOMENTUM_EVAL_MINUTES: int = 30  # 30min after open. Research: first 30min predicts day 82%.  # PORTED from TradierConfig 2026-08-17
    FH_MOMENTUM_MAX_POSITIONS: int = 5  # PORTED from TradierConfig 2026-08-17
    FH_MOMENTUM_MFI_CONFIRM: bool = False  # Sweep: MFI barely matters (1.314 vs 1.313). OFF = more entries.  # PORTED from TradierConfig 2026-08-17
    FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5  # Sweep: 0.3-1.0% all Sharpe>1.47. 0.5% = sweet spot (82% day-follows rate).  # PORTED from TradierConfig 2026-08-17
    FH_MOMENTUM_POSITION_SIZE: float = 600.0  # PORTED from TradierConfig 2026-08-17
    FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER: float = -8.0  # PORTED from TradierConfig 2026-08-17
    FULL_RECIPE_ONLY_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    FUNDING_GATE_PC_RATIO_LONG_MAX: float = 1.2       # block LONG when put/call ratio >= this (bearish flow)  # PORTED from TradierConfig 2026-08-17
    FUNDING_GATE_PC_RATIO_SHORT_MIN: float = 0.83     # block SHORT when put/call ratio <= this (bullish flow)  # PORTED from TradierConfig 2026-08-17
    FUNDING_GATE_TRADIER_HEDGE_GATE_ENABLED: bool = False # apply gate to hedge entries too (default OFF)  # PORTED from TradierConfig 2026-08-17
    FUNDING_GATE_TRADIER_NEAR_MONEY_PREFER: bool = True   # prefer near_money_pc_ratio (±5% strikes) when present — purer signal  # PORTED from TradierConfig 2026-08-17
    FUNDING_GATE_TRADIER_STALE_MAX_HOURS: float = 4.0     # skip gate if cache older than 4h (fail-open)  # PORTED from TradierConfig 2026-08-17
    GAP_FILL_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    GAP_FILL_MAX_GAP_PCT: float = 5.0  # PORTED from TradierConfig 2026-08-17
    GAP_FILL_MIN_GAP_PCT: float = 0.5  # BACKTEST_CHANGE_MT1: was 1.0. T6 sweep: Sharpe +0.120 (GAP=0.5) vs -0.138 (GAP=1.0). 180 configs, 2yr, 10 symbols. Smaller gaps fill more reliably.  # PORTED from TradierConfig 2026-08-17
    GAP_FILL_POSITION_SIZE: float = 600.0  # BACKTEST_CHANGE_T30 was 400 → 600 align sizing  # PORTED from TradierConfig 2026-08-17
    GAP_FILL_STOP_MULT: float = 0.3  # PORTED from TradierConfig 2026-08-17
    GAP_FILL_TP_FILL_PCT: float = 0.7  # BACKTEST_CHANGE_T18 was 0.5 → 0.7 capture more of gap  # PORTED from TradierConfig 2026-08-17
    GHOST_ABSENT_ALERT_THRESHOLD: int = 3  # 2026-05-28: fire desktop alert after N consecutive API misses (state NEVER zeroed)  # PORTED from TradierConfig 2026-08-17
    GHOST_CLOSE_REQUIRE_CONFIRMATION: bool = True   # DEPRECATED — ghost-close zeroing abolished 2026-05-28 (ASTS disaster)  # PORTED from TradierConfig 2026-08-17
    GOLDEN_RULE_EXIT_MIN_IND: int = 2  # Per-TF min indicators for exit gate.  # PORTED from TradierConfig 2026-08-17
    GOLDEN_RULE_EXIT_MIN_TFS: int = 0  # GOLDEN_RULE exit gate: only exit when N TFs show bearish (0=off, no restriction on exits).  # PORTED from TradierConfig 2026-08-17
    GR_HTF_GATE_ENABLED: bool = False       # NEW. Adds GR HTF alignment gate (uses wt_bull_alignment/wt_bear_alignment). ROLLBACK: False (no change — gate stays off until validated)  # PORTED from TradierConfig 2026-08-17
    GR_HTF_REQUIRE_BEAR: int = 1            # Used only when GR_HTF_GATE_ENABLED=True  # PORTED from TradierConfig 2026-08-17
    GR_HTF_REQUIRE_BULL: int = 1            # Used only when GR_HTF_GATE_ENABLED=True  # PORTED from TradierConfig 2026-08-17
    HARD_MAX_SYMBOL_VALUE_TRADIER: float = 2500.0  # PORTED from TradierConfig 2026-08-17
    HEDGE_CROSS_SYMBOL_TRADIER: bool = True  # BACKTEST_CHANGE_T62: Cross-symbol hedge enabled. 25% size, trigger -1%, no momentum gate. ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    HEDGE_SAME_SYMBOL_TRADIER: bool = False  # BACKTEST_CHANGE_T63: Same-symbol hedge DISABLED for stocks. Cross-symbol only. ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    HEDGE_SIZE_RATIO_TRADIER: float = 0.25  # BACKTEST_CHANGE_T62: Hedge at 25% of losing value. Sweet spot in sweep. ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    HEDGE_TRIGGER_LOSS_TRADIER: float = -1.0  # BACKTEST_CHANGE_T62: Trigger hedge at -1% loss (stocks: tighter than crypto -2% due to daily gaps). ; DEAD_CONFIRMED (priority 82/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    HIGH_GAIN_AUGMENTATION_MIN_SIZE: float = 200.0  # PORTED from TradierConfig 2026-08-17
    HODL_LONG_ONLY: bool = True  # BACKTEST_CHANGE_T54: HODL strategy is LONG only. SHORT on stocks = negative returns (upward bias kills hold-forever shorts). ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    HOLD_BARS_CLOSE: int = 50  # BACKTEST_CHANGE_T25 max hold bars during close zone ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    HOLD_BARS_MID: int = 500  # BACKTEST_CHANGE_T25 max hold bars during mid zone ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    HOLD_BARS_OPEN: int = 200  # BACKTEST_CHANGE_T25 max hold bars during open zone ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    HTF_ALIGNMENT_ENABLED: bool = True  # vector 1140: htf_cnt >= HTF_MIN_ALIGNED  # PORTED from TradierConfig 2026-08-17
    HTF_ALIGN_REQUIRED_TRADIER: int = 2     # CLAUDE.md stocks ≥2 (was 1 — crypto value; fixed 2026-05-27). ROLLBACK: 1  # PORTED from TradierConfig 2026-08-17
    HTF_DC_BREAKOUT_TRADIER_ENABLED: bool = False      # F2: additive entry — close > dc_high_4h * (1+thr) AND W WT on side  # PORTED from TradierConfig 2026-08-17
    HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT: bool = True  # require W WaveTrend on side (HTF anchor)  # PORTED from TradierConfig 2026-08-17
    HTF_DC_BREAKOUT_TRADIER_TF: str = "4h"             # 4h | D | W (DC band timeframe)  # PORTED from TradierConfig 2026-08-17
    HTF_DC_BREAKOUT_TRADIER_THRESHOLD_PCT: float = 0.0 # 0 = exact break; 0.1 = +0.1% confirm  # PORTED from TradierConfig 2026-08-17
    HTF_MIN_ALIGNED: int = 1  # PORTED from TradierConfig 2026-08-17
    HTF_W_M_ALIGN_GATE_TRADIER_ENABLED: bool = False   # F1: entry GATE — N of 2 (W, M) WT must agree with side  # PORTED from TradierConfig 2026-08-17
    HTF_W_M_ALIGN_TRADIER_REQUIRED: int = 2            # 1=either; 2=both  # PORTED from TradierConfig 2026-08-17
    HTF_W_REVERSAL_EXIT_TRADIER_ENABLED: bool = False  # F3: exit when wt1_W against side AND wt1_D against side  # PORTED from TradierConfig 2026-08-17
    HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D: bool = True # also require D against (2-TF anchor; if False, W alone suffices)  # PORTED from TradierConfig 2026-08-17
    INDICATORS_FILE: Path = DATA_DIR / "tradier_indicators_latest.json"  # WIRED 2026-04-16 (priority 15/100) — tradier_rankings.py:144  # PORTED from TradierConfig 2026-08-17
    INDICATOR_UPDATE_INTERVAL: float = 30.0  # BACKTEST_CHANGE_T49 was 60 → 30 faster indicator refresh ; DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    K_LOWER_HIGH_EXIT_ENABLED: bool = True        # v8 engine: exit if k peaks below extreme and turns down  # PORTED from TradierConfig 2026-08-17
    K_LOWER_HIGH_EXTREME: float = 95.0           # only fires if k_prev < 95 (didn't reach true extreme)  # PORTED from TradierConfig 2026-08-17
    K_LOWER_HIGH_LTF_THRESHOLD: float = 65.0     # k_5m must reach >= 65 to qualify as failed rally  # PORTED from TradierConfig 2026-08-17
    K_ZONE_VETO_ENABLED_TRADIER: bool = False  # VARIANCE_FIX 2026-04-14: when True, K_ZONE_LONG/SHORT_THRESHOLD veto entries on wt_dc path (proves switch gates trades). Default False = live unchanged.  # PORTED from TradierConfig 2026-08-17
    LEADERBOARD_LONG: Path = BASE_PATH / "symbols_long_tr.json"  # DEAD_CONFIRMED (priority 15/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    LEADERBOARD_SHORT: Path = BASE_PATH / "symbols_short_tr.json"  # DEAD_CONFIRMED (priority 15/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    LIVE_INDICATOR_MAX_BARS_PER_TF: int = 600                 # Clip klines bundle to latest N bars per TF in live indicator cycles (0=unlimited; reduces 15+min cycles to <2min)  # PORTED from TradierConfig 2026-08-17
    LOCAL_EXTREMES_MIN_SCORE: float = 45.0  # 2026-04-20 le_dynamic winner: min LE score to allow entry (262sym Sharpe 3.5479). Wire in tradier_manage.py entry gate.  # PORTED from TradierConfig 2026-08-17
    LOG_BACKUP_COUNT: int = 30  # PORTED from TradierConfig 2026-08-17
    LOG_FILE_TRADIER_MANAGE: Path = LOG_DIR / "tradier_manage.log"  # DEAD_CONFIRMED (priority 10/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    LOG_FILE_TRADIER_POSITIONS: Path = LOG_DIR / "tradier_positions.log"  # PORTED from TradierConfig 2026-08-17
    LOG_FILE_TRADIER_PRICES: Path = LOG_DIR / "tradier_prices.log"  # PORTED from TradierConfig 2026-08-17
    LOG_MAX_BYTES: int = 1024 * 1024 * 20  # PORTED from TradierConfig 2026-08-17
    LONG_STRUCT_EXIT_TF: str = "D"  # PORTED from TradierConfig 2026-08-17
    LONG_WAIT_DIRECT_BOUNCE_DISTANCE: float = 0.015  # PORTED from TradierConfig 2026-08-17
    LONG_WAIT_DIRECT_BOUNCE_TIMEFRAME: str = "15m"  # PORTED from TradierConfig 2026-08-17
    LONG_WAIT_DIRECT_CONFIRMATION: str = "stoch5"  # PORTED from TradierConfig 2026-08-17
    LONG_WAIT_DIRECT_DEEP_K4H: float = 50.0  # PORTED from TradierConfig 2026-08-17
    LONG_WAIT_DIRECT_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    LONG_WAIT_DIRECT_TURN_K1H: float = 40.0  # PORTED from TradierConfig 2026-08-17
    LR_BAND_E02_EXIT_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    LR_BAND_ENTRY_PRIORITY: bool = False           # 2026-07-20 USER: evaluate band/regime entry FIRST (was last in cascade → 0 fires on 3189 eligible ARM bars)  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_ABOVE_TOP_MULT: float = -1.0   # <0 = use TOP_MULT ("3x at or above top")  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_BASE_UNIT_USD: float = 2000.0  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_BASIS: float = 0.5  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_BELOW_BOTTOM_MULT: float = 0.0 # below the lower band = NO trade  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_BOTTOM_MULT: float = 10.0      # at the lower band (scalar fallback)  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_CAPACITY_USD: float = 16000.0  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_CENTER: float = 0.5            # plateau edge for center_plateau mode  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_MODE: str = "center_plateau"   # linear | center_plateau (10x at centre and below)  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_ORDINARY_PARITY_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_STOCH_EXTREME: float = 30.0  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_TF_BOTTOM: dict = field(default_factory=lambda: {"D": 10.0, "4h": 6.0, "1h": 4.0})  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_TF_TOP: dict = field(default_factory=lambda: {"D": 6.0, "4h": 4.0, "1h": 1.0})  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_TOP_MULT: float = 3.0          # at the upper band (scalar fallback)  # PORTED from TradierConfig 2026-08-17
    LR_BAND_LADDER_TRIGGER: str = "union"          # green | structure | union  # PORTED from TradierConfig 2026-08-17
    LR_BAND_SIZE_DEPTH_GAIN: float = 1.0           # size law: deeper in channel = bigger  # PORTED from TradierConfig 2026-08-17
    LR_BAND_SIZE_MAX: float = 3.0  # PORTED from TradierConfig 2026-08-17
    LR_BAND_SIZE_SLOPE_GAIN: float = 1.0           # size law: steeper HTF slope = bigger  # PORTED from TradierConfig 2026-08-17
    LR_BAND_SLOPE_FLIP_MIN_HOLD_MIN: float = 240.0 # and the position must have held this long first  # PORTED from TradierConfig 2026-08-17
    LR_BAND_SLOPE_FLIP_MIN_PCT_DAY: float = 0.05   # deadband: slope must be decisively negative (%/day), not just <=0 — bare zero-cross caused 60/80 MU churn exits at +0.0x%  # PORTED from TradierConfig 2026-08-17
    LR_BAND_SLOPE_NORM_PCT_DAY: float = 0.3  # PORTED from TradierConfig 2026-08-17
    LR_PCTB_D_SHORT_THRESHOLD: float = 0.1  # BACKTEST_CHANGE_T10 daily LR %B threshold for shorts  # PORTED from TradierConfig 2026-08-17
    LUNCH_DEADZONE_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    LUNCH_DEADZONE_MODE: str = "BLOCK_MOMENTUM"  # PORTED from TradierConfig 2026-08-17
    LUNCH_DEADZONE_SIZE_MULT: float = 0.5  # DEAD_CONFIRMED (priority 55/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    MACRO_BLACKOUT_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    MACRO_BLACKOUT_SIZE_MULT: float = 0.5  # PORTED from TradierConfig 2026-08-17
    MANAGE_REDUCE: bool = True  # PORTED from TradierConfig 2026-08-17
    MANDATORY_REENTRY_DC4_WINDOW_MIN: float = 30.0  # require dc_high4_5m break within 30min for MANDATORY_REENTRY exits (USER 2026-06-22); Rollback: 0  # PORTED from TradierConfig 2026-08-17
    MANDATORY_REENTRY_WT_FILTER_ENABLED: bool = True  # 2026-08-03 EMERGENCY: price-cross reentry requires 15m WT confirmation  # PORTED from TradierConfig 2026-08-17
    MANDATORY_REENTRY_WT_FILTER_MIN_TFS: int = 1  # PORTED from TradierConfig 2026-08-17
    MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY: float = 0.0  # PORTED from TradierConfig 2026-08-17
    MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP: bool = False  # PORTED from TradierConfig 2026-08-17
    MANDATORY_REENTRY_WT_FILTER_TF_MODE: str = "15m_only"  # PORTED from TradierConfig 2026-08-17
    MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO: float = 0.90  # PORTED from TradierConfig 2026-08-17
    MARKET_CLOSE_HOUR: int = 16  # PORTED from TradierConfig 2026-08-17
    MARKET_CLOSE_MINUTE: int = 0  # PORTED from TradierConfig 2026-08-17
    MARKET_OPEN_HOUR: int = 9  # DEAD_CONFIRMED (priority 40/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    MARKET_OPEN_MINUTE: int = 30  # DEAD_CONFIRMED (priority 40/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    MAX_ALLOWED_DRAWDOWN_PCT: float = 50.0  # PORTED from TradierConfig 2026-08-17
    MAX_CONCURRENT_POSITIONS: int = 16  # BACKTEST_CHANGE_T35 total max positions across all strategies  # PORTED from TradierConfig 2026-08-17
    MAX_DAILY_LOSS_PCT: float = 3.0  # BACKTEST_CHANGE_T37 halt trading at 3% daily loss  # PORTED from TradierConfig 2026-08-17
    MAX_SYMBOL_VALUE_TRADIER: float = 3750.0  # was 7500 / orig 15000 — 2026-04-27 second cut  # PORTED from TradierConfig 2026-08-17
    MFI_ENTRY_ENABLED: bool = True   # FIXED 2026-05-18: semantics inverted from "oversold-required" to "overbought-block"  # PORTED from TradierConfig 2026-08-17
    MFI_ENTRY_LONG_MAX: float = 60.0  # vector MFI gate long  # PORTED from TradierConfig 2026-08-17
    MFI_ENTRY_SHORT_MIN: float = 40.0  # vector MFI gate short  # PORTED from TradierConfig 2026-08-17
    MFI_FLIP_EXIT_ENABLED: bool = True  # BACKTEST_CHANGE_148: Exit when MFI exhausts (+3.91% avg vs +1.09% fixed TP, 44 trades)  # PORTED from TradierConfig 2026-08-17
    MFI_FLIP_EXIT_LONG_THRESHOLD: float = 70.0  # Exit LONG when MFI_1h > 70 (overbought = sell)  # PORTED from TradierConfig 2026-08-17
    MFI_FLIP_EXIT_SHORT_THRESHOLD: float = 30.0  # Exit SHORT when MFI_1h < 30 (oversold = cover)  # PORTED from TradierConfig 2026-08-17
    MFI_LONG_THRESHOLD_D: float = 80.0  # block LONG when mfi_D > 80 (overbought reversal expected); was 20.0 (oversold-required, broken)  # PORTED from TradierConfig 2026-08-17
    MICRO_SCALP_STOCKS_ACCOUNTS: List[str] = field(default_factory=lambda: [])#"trb", "trc", "tra"])  # PORTED from TradierConfig 2026-08-17
    MICRO_SCALP_STOCKS_GAIN_THRESHOLD_PCT: float = 0.2  # PORTED from TradierConfig 2026-08-17
    MICRO_SCALP_STOCKS_MAKER_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    MICRO_SCALP_STOCKS_PEAK_FLOOR_PCT: float = 0.6  # 2026-05-28 USER: micro-scalp may only CLOSE a position whose gain has ALREADY peaked >= this floor. Stops 0.05-0.1% round-trip churn (IBIT g0.074% peak0.098% never near 0.5%).  # PORTED from TradierConfig 2026-08-17
    MID_ZONE_SHORT_EXTRA_IND: str = "wt_crossunder_15m"  # BACKTEST_CHANGE_T24 extra indicator for mid-zone shorts ; DEAD_CONFIRMED (priority 35/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    MINERVINI_ENABLED: bool = False  # DISABLED 2026-03-30: fake backtest Sharpe. Needs V5 validation.  # PORTED from TradierConfig 2026-08-17
    MINERVINI_GATE_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    MINERVINI_LONG_BUDGET: float = 4000.0  # WIRED 2026-04-16 (priority 75/100) — tradier_manage.py:5286 TRC override destination  # PORTED from TradierConfig 2026-08-17
    MINERVINI_MAX_HOLD_DAYS: int = 40  # Swing trade hold  # PORTED from TradierConfig 2026-08-17
    MINERVINI_MIN_SCORE: int = 5          # int 0-6 (5 = all 5 SEPA conditions met)  # PORTED from TradierConfig 2026-08-17
    MINERVINI_MIN_SEPA_SCORE: int = 5  # Need 5 of 6 conditions  # PORTED from TradierConfig 2026-08-17
    MINERVINI_POSITION_SIZE: float = 800.0  # PORTED from TradierConfig 2026-08-17
    MINERVINI_TARGET_PCT: float = 25.0  # Take profit at 25%  # PORTED from TradierConfig 2026-08-17
    MIN_EXIT_TF_AGAINST_TRADIER: int = 3  # 2026-04-26: 3 TFs against (was 2) — fewer false exits  # PORTED from TradierConfig 2026-08-17
    MIN_HOLD_BARS_TRADIER: int = 40  # 2026-04-20 sweep: 40 (200min) consistently wins over 32 (160min)  # PORTED from TradierConfig 2026-08-17
    MIN_HOLD_MINUTES_TRADIER: float = 30.0  # No exits before 30 min. Bypassed only if loss > -5%. ; WIRED 2026-04-16 (priority 90/100) — tradier_manage.py:3891 stock min hold fallback  # PORTED from TradierConfig 2026-08-17
    MI_EXIT_VETO_ENABLED_TRADIER: bool = False  # SENTINEL_FIX 2026-04-14: when True, MI_EXIT_ENABLED_TRADIER actually gates exits. Default False = live unchanged.  # PORTED from TradierConfig 2026-08-17
    MTF_ARROW_CONFIRM_PCT: float = 2.0             # 5m green-arrow confirm: entry only when price reverses >= this % off the running low (lab phase_b)  # PORTED from TradierConfig 2026-08-17
    MTF_ARROW_ENTRY_ENABLED: bool = False          # 2026-07-20 USER multi-TF arrow system (lab-proven ARM 5.81x b&h sized); OFF until Tier-2 confirms  # PORTED from TradierConfig 2026-08-17
    MTF_ARROW_SHORT_ENTRY_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    MTF_ARROW_SIZE_GAIN: float = 1.0               # size = 1 + gain*score (deeper HTF + steeper slope = bigger)  # PORTED from TradierConfig 2026-08-17
    MTF_ARROW_SIZE_MAX: float = 4.0  # PORTED from TradierConfig 2026-08-17
    MTF_ARROW_SLOPE_LAMBDA: float = 1.0            # weight of the slope term vs depth term  # PORTED from TradierConfig 2026-08-17
    MTF_ARROW_SLOPE_NORM_PCT_DAY: float = 0.3  # PORTED from TradierConfig 2026-08-17
    MTF_ARROW_THETA: float = 0.3                   # entry gate on the weighted HTF band-depth+slope score  # PORTED from TradierConfig 2026-08-17
    MTF_ARROW_TRAIL_EXIT_ENABLED: bool = False     # lab-faithful exit: close when price retraces CONFIRM_PCT off running high; loss-closes need MTF_ARROW_TRAIL in the noloss bypass list (pack-scoped)  # PORTED from TradierConfig 2026-08-17
    MTF_ARROW_WEIGHTS: dict = field(default_factory=lambda: {"1h": 0.35, "4h": 0.35, "D": 0.30})  # PORTED from TradierConfig 2026-08-17
    MTF_ATR_MULTITF_DIRECT_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    MTF_ATR_MULTITF_DIRECT_MIN_CONFIRMING_TFS: int = 1  # PORTED from TradierConfig 2026-08-17
    MTF_ATR_MULTITF_DIRECT_MIN_PROFIT_PCT: float = 0.5  # PORTED from TradierConfig 2026-08-17
    MTF_ATR_MULTITF_DIRECT_MULT: float = 1.5  # PORTED from TradierConfig 2026-08-17
    MTF_ATR_MULTITF_DIRECT_TIMEFRAMES: list[str] = field(default_factory=lambda: ["1h", "4h", "D"])  # PORTED from TradierConfig 2026-08-17
    MTF_WT_CROSS_EXIT_DIRECT_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    MU_CORRECTION_EXIT_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    MU_CORRECTION_HTF_K_MIN: float = 80.0  # PORTED from TradierConfig 2026-08-17
    MU_CORRECTION_HTF_MIN_TFS: int = 1  # PORTED from TradierConfig 2026-08-17
    MU_CORRECTION_HTF_RSI_MIN: float = 60.0  # PORTED from TradierConfig 2026-08-17
    MU_CORRECTION_HTF_TFS: str = "1h+4h"  # PORTED from TradierConfig 2026-08-17
    MU_CORRECTION_LTF_FALL_MIN_TFS: int = 2  # PORTED from TradierConfig 2026-08-17
    MU_CORRECTION_LTF_FALL_TFS: str = "5m+15m"  # PORTED from TradierConfig 2026-08-17
    MU_CORRECTION_MIN_GAIN_PCT: float = 0.0  # PORTED from TradierConfig 2026-08-17
    MU_CORRECTION_REENTRY_DC_TOL_PCT: float = 2.0  # PORTED from TradierConfig 2026-08-17
    MU_CORRECTION_REENTRY_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    MU_CORRECTION_REENTRY_STOCH_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    MU_CORRECTION_REQUIRE_CLOSE_REVERSAL: bool = True  # PORTED from TradierConfig 2026-08-17
    MU_CORRECTION_REQUIRE_HIGH_REVERSAL: bool = True  # PORTED from TradierConfig 2026-08-17
    MU_CORRECTION_SYMBOLS: str = "MU"  # PORTED from TradierConfig 2026-08-17
    NEWBORN_DC_STOP_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    NEWBORN_DC_STOP_FIELD: str = 'dc_low4_5m'  # PORTED from TradierConfig 2026-08-17
    NEWBORN_DC_STOP_MAX_AGE_MIN: float = 20.0  # PORTED from TradierConfig 2026-08-17
    NOLOSS_BB1H_GATE_ENABLED: bool = False  # 2026-07-08 GAINMO triage: True→False — wired loss-close on 1h-BB break bypassing STOCK_MIN_HOLD + UNIVERSAL_NOLOSS_GATE, outside the sanctioned loss-exit trio; value contradicted its own KILL comment  # PORTED from TradierConfig 2026-08-17
    NOLOSS_ENABLED: bool = False  # vector 1116: hold losers unless DC recovery  # PORTED from TradierConfig 2026-08-17
    OBLIGATORY_SECTOR_HEDGE_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    OBLIGATORY_SECTOR_HEDGE_LOOP_INTERVAL_SECONDS: float = 90.0          # how often to scan stock losers  # PORTED from TradierConfig 2026-08-17
    OBLIGATORY_SECTOR_HEDGE_TRIGGER_REQUIRE_WT_5M_AND_1H: bool = True   # USER mandate: 5m AND 1h against (stocks 5m base TF)  # PORTED from TradierConfig 2026-08-17
    OI_CONFIRM_MIN_OI_CHANGE_PCT_TRADIER: float = 0.5 # |total OI change since last cache snapshot| significance threshold  # PORTED from TradierConfig 2026-08-17
    OI_CONFIRM_TRADIER_HEDGE_GATE_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    OPENING_BUFFER_NO_CLOSE_MINUTES: float = 30.0  # PORTED from TradierConfig 2026-08-17
    OPTIONS_ALERT_ABS_LOSS_PP: float = 25.0  # PORTED from TradierConfig 2026-08-17
    OPTIONS_ALERT_DROP_PP: float = 5.0  # PORTED from TradierConfig 2026-08-17
    OPTIONS_AUGMENT_INTO_LOSS_BLOCK_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    OPTIONS_AUGMENT_INTO_LOSS_THRESHOLD: float = 0.85  # PORTED from TradierConfig 2026-08-17
    OPTIONS_BASE_CAP: float = 5000.0          # Max with zero diversification  # PORTED from TradierConfig 2026-08-17
    OPTIONS_BUY_MAX_OTM_PCT: float = 3.0         # Reject strikes >3% OTM (calls) / <3% ITM for puts relative to underlying  # PORTED from TradierConfig 2026-08-17
    OPTIONS_BUY_MIN_ABS_DELTA: float = 0.35      # Reject lottery tickets — min |delta| for any new buy  # PORTED from TradierConfig 2026-08-17
    OPTIONS_BUY_MIN_DTE: int = 60                # User rule 2026-04-22: never open options <2 months out (JNJ bought at 22 DTE = theta trap)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_BUY_MIN_WT_DC_SCORE: float = 70.0  # PORTED from TradierConfig 2026-08-17
    OPTIONS_BUY_PREFERRED_DTE: int = 90          # Prefer 3+ months out — score bonus applied when dte >= this  # PORTED from TradierConfig 2026-08-17
    OPTIONS_BUY_REQUIRE_D_ALIGN: bool = True     # CALL needs wt_cross_D != BEAR; PUT needs wt_cross_D != BULL  # PORTED from TradierConfig 2026-08-17
    OPTIONS_BUY_WT_DC_GATE_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CONTINUOUS_SECTOR_GATE: bool = True  # Block new buys that widen existing sector/group/symbol violation  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_DTE_MAX: int = 90                # Max DTE (3 months — keeps liquidity + balances theta capture)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_DTE_MIN: int = 60                # Min days-to-expiry — at least 2 months ahead (time-premium strategy)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_EDGE_MARGIN: float = 1.15        # Sell-structure must beat buy-structure edge by 15% to be picked  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_ENABLED: bool = False            # Master switch — keep False until backtest + forward-test proven  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MAX_CAPITAL_PCT: float = 0.30    # Max fraction of available cash tied up in CSPs at once  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MAX_DELTA: float = 0.30          # Max |delta| on the put sold (30Δ ≈ 70% win rate empirically)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MAX_HOLD_DAYS: int = 21          # Force close after 21 days open regardless (~50% through a 60-DTE window)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MAX_POS_PCT_OF_ACCOUNT: float = 0.03  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MIN_DELTA: float = 0.15          # Min |delta| — don't sell puts too far OTM (premium too thin)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MIN_EXTRINSIC_PCT: float = 0.015 # Min extrinsic value as % of strike (1.5%) — premium must be worth it  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MIN_IV_RANK: float = 40.0        # Only sell premium when IV rank >= 40 (rich premium)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MONITOR_CALL_BREACH_PCT: float = 0.05     # SHORT CALL: close if underlying rises 5%+ ABOVE strike (disabled v1 but gate wired)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MONITOR_CALL_GAP_FROM_ENTRY_PCT: float = 0.15  # SHORT CALL: close on 15%+ upside gap from entry  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MONITOR_CORRELATED_BREACH_N: int = 3      # N positions breaching simultaneously triggers emergency log/alert  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MONITOR_GAP_FROM_ENTRY_PCT: float = 0.15  # SHORT PUT: close if underlying drops 15%+ from entry spot (catches gap-down / earnings crash)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MONITOR_LOG_EVERY_TICK: bool = True       # Log every poll for audit trail (required for non-skippable)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MONITOR_LOSS_TRIGGER_PCT: float = -0.20   # Arm close gate only on 20%+ premium drawdown (was -5%)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MONITOR_MAX_LOSS_PCT: float = -1.50       # Hard premium cut: pnl <= -150% (buy-back costs 2.5x premium) — catastrophic only  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MONITOR_POLL_SEC: int = 60                # Poll interval in seconds  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MONITOR_REQUIRE_WT_D_TURN: bool = True    # Require wt_D turn against position to confirm soft close  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_MONITOR_STRIKE_BREACH_PCT: float = 0.05   # SHORT PUT: close if underlying drops 5%+ BELOW strike (put is 5% ITM)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_NAKED_CALL_ENABLED: bool = False # HARD-disabled. Unlimited upside risk. Never flip without Level-4 margin + explicit approval.  # PORTED from TradierConfig 2026-08-17
    OPTIONS_CSP_PROFIT_TARGET_PCT: float = 0.50  # Close at 50% of premium collected (≈ 2-week avg hold on 60-DTE position)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_EQUITY_HEDGE_COOLDOWN_MIN: float = 60.0  # don't re-fire same OCC within N minutes  # PORTED from TradierConfig 2026-08-17
    OPTIONS_EQUITY_HEDGE_DC_BREACH_EXIT_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    OPTIONS_EQUITY_HEDGE_DIRECTION_GUARD_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    OPTIONS_EQUITY_HEDGE_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    OPTIONS_EQUITY_HEDGE_MAX_NOTIONAL_USD: float = 2500.0    # was 5000 — 2026-04-27 emergency halve  # PORTED from TradierConfig 2026-08-17
    OPTIONS_EQUITY_HEDGE_MAX_PCT_OF_OPT_COST: float = 100.0  # was 150 — 2026-04-27 emergency tighten: hedge ≤ 1× option cost basis  # PORTED from TradierConfig 2026-08-17
    OPTIONS_EQUITY_HEDGE_TRIGGER_PCT: float = -10.0  # Unsellable := bid implies loss ≤ this (%)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_FULL_DIV_CAP: float = 15000.0     # Max with hedging + 3+ sector groups  # PORTED from TradierConfig 2026-08-17
    OPTIONS_HEDGED_CAP: float = 10000.0       # Max with call+put hedging within sectors  # PORTED from TradierConfig 2026-08-17
    OPTIONS_HEDGE_BOTTOM_MIN_SIGNALS: int = 2  # PORTED from TradierConfig 2026-08-17
    OPTIONS_HEDGE_DC_REL_TOL_PCT: float = 1.0  # PORTED from TradierConfig 2026-08-17
    OPTIONS_HEDGE_K_OVERSOLD_PCT: float = 25.0  # PORTED from TradierConfig 2026-08-17
    OPTIONS_HEDGE_LADDER_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    OPTIONS_HEDGE_PAIR_GUARD_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    OPTIONS_HEDGE_PUT_DELTA_MAX: float = 0.50  # PORTED from TradierConfig 2026-08-17
    OPTIONS_HEDGE_PUT_DELTA_MIN: float = 0.30  # PORTED from TradierConfig 2026-08-17
    OPTIONS_HEDGE_PUT_DTE_MAX: int = 120  # PORTED from TradierConfig 2026-08-17
    OPTIONS_HEDGE_PUT_DTE_MIN: int = 45  # PORTED from TradierConfig 2026-08-17
    OPTIONS_HEDGE_PUT_MAX_IV_RANK: float = 35.0  # PORTED from TradierConfig 2026-08-17
    OPTIONS_HEDGE_PUT_MAX_SPREAD_PCT: float = 8.0  # PORTED from TradierConfig 2026-08-17
    OPTIONS_HEDGE_RATIO_MIN: float = 0.25     # Min puts/(puts+calls) to qualify as hedged  # PORTED from TradierConfig 2026-08-17
    OPTIONS_LEVEL_BREAK_BUFFER: float = 0.01     # 1% buffer past dc_low_D (call) / dc_high_D (put)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_LEVEL_BREAK_MIN_DTE: int = 14        # Don't fire on sub-14-DTE (noise dominates)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_LIVE_TRADING_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    OPTIONS_MARKET_RATIO_MAX: float = 0.75    # Max fraction of exposure that is bull-market-bets  # PORTED from TradierConfig 2026-08-17
    OPTIONS_MARKET_RATIO_MIN: float = 0.25    # Min fraction of exposure that is bull-market-bets  # PORTED from TradierConfig 2026-08-17
    OPTIONS_MAX_CONTRACTS_PER_ORDER: int = 3   # Hard cap: never buy >N contracts in one order (practical ceil given $800/order + $9/share rule)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_MAX_LOSS_GUARD_ENABLED: bool = False # 2026-04-22 disabled per user — bottom-seller  # PORTED from TradierConfig 2026-08-17
    OPTIONS_MAX_LOSS_PCT_DTE_14: float = -60.0   # (inactive unless re-enabled) 14 < DTE <= 30  # PORTED from TradierConfig 2026-08-17
    OPTIONS_MAX_LOSS_PCT_DTE_30: float = -80.0   # (inactive unless re-enabled) DTE > 30  # PORTED from TradierConfig 2026-08-17
    OPTIONS_MAX_LOSS_PCT_DTE_LOW: float = -40.0  # (inactive unless re-enabled) DTE <= 14  # PORTED from TradierConfig 2026-08-17
    OPTIONS_MAX_ORDER_BUDGET: float = 400.0         # was 800 — 2026-04-27 emergency halve  # PORTED from TradierConfig 2026-08-17
    OPTIONS_MAX_PER_GROUP: float = 0.60       # Max 60% of portfolio in one sector group  # PORTED from TradierConfig 2026-08-17
    OPTIONS_MAX_PER_SECTOR: float = 0.35      # tightened 2026-04-26 per §L6.2 (was 0.40)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_MAX_PER_SYMBOL: float = 0.20      # tightened 2026-04-26 per §L6.1 (was 0.25)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_MAX_SINGLE_CONTRACT_PRICE: float = 9.0  # If price/share > this, max qty=1  # PORTED from TradierConfig 2026-08-17
    OPTIONS_MIN_GROUPS: int = 3               # Min groups for full diversification tier  # PORTED from TradierConfig 2026-08-17
    OPTIONS_MIN_SECTORS: int = 2              # Min sectors for hedged tier  # PORTED from TradierConfig 2026-08-17
    OPTIONS_PREMARKET_NO_FIRE: bool = True  # PORTED from TradierConfig 2026-08-17
    OPTIONS_SPREAD_DTE_MAX: int = 75               # Max DTE (~10 weeks)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_SPREAD_DTE_MIN: int = 55               # Min DTE (~2 months out)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_SPREAD_ENABLED: bool = True           # Master gate — flip when ready  # PORTED from TradierConfig 2026-08-17
    OPTIONS_SPREAD_IV_RANK_MIN: float = 75.0       # Chain-relative IV rank gate (biggest backtest edge)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_SPREAD_MAX_CONCURRENT: int = 15        # Max simultaneous spread positions  # PORTED from TradierConfig 2026-08-17
    OPTIONS_SPREAD_MAX_HOLD_DAYS: int = 21         # Force close after 21 days  # PORTED from TradierConfig 2026-08-17
    OPTIONS_SPREAD_PROFIT_TARGET_PCT: float = 0.50 # Close at 50% of credit captured  # PORTED from TradierConfig 2026-08-17
    OPTIONS_SPREAD_SHORT_DELTA: float = 0.25       # Short-put target delta  # PORTED from TradierConfig 2026-08-17
    OPTIONS_SPREAD_UNIVERSE: tuple = ("SPY", "QQQ", "AAPL", "AMD", "AMZN", "META", "NVDA", "JPM", "CAT", "XLK", "XLF", "GLD")  # PORTED from TradierConfig 2026-08-17
    OPTIONS_SPREAD_WIDTH: float = 10.0             # $ between short and long strike  # PORTED from TradierConfig 2026-08-17
    OPTIONS_STOCK_CSP_ENABLED: bool = False        # Disabled by default; enable explicitly  # PORTED from TradierConfig 2026-08-17
    OPTIONS_STOCK_CSP_IV_RANK_MIN: float = 85.0    # Stricter than spreads  # PORTED from TradierConfig 2026-08-17
    OPTIONS_STOCK_CSP_MAX_CONCURRENT: int = 2      # Hard cap on concurrent positions  # PORTED from TradierConfig 2026-08-17
    OPTIONS_STOCK_CSP_MIN_CASH: float = 30000.0    # Only proceed if cash available >= this  # PORTED from TradierConfig 2026-08-17
    OPTIONS_USER_CANCEL_COOLDOWN_HOURS: float = 4.0  # Don't re-propose a user-canceled OCC for N hours  # PORTED from TradierConfig 2026-08-17
    OPTIONS_WT_ACCEL_GROWTH_PCT: float = 25.0    # LEGACY alias — reused as slowdown % if OPTIONS_WT_SLOWDOWN_PCT unset  # PORTED from TradierConfig 2026-08-17
    OPTIONS_WT_ACCEL_MIN_ABS: float = 10.0       # min |wt_velocity_D_prev| for slowdown to count (ignore noise)  # PORTED from TradierConfig 2026-08-17
    OPTIONS_WT_SLOWDOWN_PCT: float = 25.0        # velocity must shrink by ≥25% bar-over-bar for slowdown to fire  # PORTED from TradierConfig 2026-08-17
    ORB_ENABLED: bool = False  # OFF for trb (real $). TRC overrides to True.  # PORTED from TradierConfig 2026-08-17
    ORB_LONG_BUDGET: float = 2000.0  # WIRED 2026-04-16 (priority 85/100) — tradier_manage.py:5286 TRC override destination  # PORTED from TradierConfig 2026-08-17
    ORB_MAX_HOLD_MINUTES: float = 150.0  # PORTED from TradierConfig 2026-08-17
    ORB_MAX_PER_DAY: int = 3  # PORTED from TradierConfig 2026-08-17
    ORB_POSITION_SIZE: float = 600.0  # PORTED from TradierConfig 2026-08-17
    ORB_RVOL_MIN: float = 1.5  # PORTED from TradierConfig 2026-08-17
    ORB_SHORT_BUDGET: float = 2000.0  # WIRED 2026-04-16 (priority 85/100) — tradier_manage.py:5286 TRC override destination  # PORTED from TradierConfig 2026-08-17
    ORB_STOP_MIDPOINT: bool = True  # PORTED from TradierConfig 2026-08-17
    ORB_TARGET_MULT: float = 1.5  # PORTED from TradierConfig 2026-08-17
    ORB_WINDOW_MINUTES: int = 15  # DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    OVERNIGHT_GAP_HEDGE_CLOSE_MINUTES: float = 5.0          # close N min after 09:30 ET open (→ 09:35 ET)  # PORTED from TradierConfig 2026-08-17
    OVERNIGHT_GAP_HEDGE_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    OVERNIGHT_GAP_HEDGE_OPEN_MINUTES: float = 15.0          # fire N min before 16:00 ET close (→ 15:45 ET)  # PORTED from TradierConfig 2026-08-17
    OVERNIGHT_GAP_HEDGE_SENTIMENT_THRESHOLD: float = 20.0   # abs(market_sentiment_score) must exceed this  # PORTED from TradierConfig 2026-08-17
    OVERNIGHT_GAP_HEDGE_SIZE_FRAC: float = 0.50             # 50% of original position notional  # PORTED from TradierConfig 2026-08-17
    PARTIAL_PROFIT_LOCK_SLIPPAGE_PCT: float = 0.05    # per-leg slippage for sweep (0.05% × 2 sides + 0.01% commission ≈ 0.12% round-trip)  # PORTED from TradierConfig 2026-08-17
    PARTIAL_PROFIT_LOCK_SWEEP_ARM_PCT: float = 0.5    # sweep variant of ARM_PCT  # PORTED from TradierConfig 2026-08-17
    PARTIAL_PROFIT_LOCK_SWEEP_ENABLED: bool = False  # use sweep params instead of live params (backtest only)  # PORTED from TradierConfig 2026-08-17
    PARTIAL_PROFIT_LOCK_SWEEP_GAIN_PCT: float = 0.3   # sweep variant of GAIN_PCT (default = live default)  # PORTED from TradierConfig 2026-08-17
    PEAK_GIVEBACK_NEGATIVE_GAIN_FLOOR_PCT: float = 0.0  # 2026-08-10 was -0.5 (let slip to loss). Now 0.0 — close at breakeven, reenter on signal  # PORTED from TradierConfig 2026-08-17
    PEAK_GIVEBACK_REQUIRE_NEGATIVE_GAIN: bool = True  # PORTED from TradierConfig 2026-08-17
    PENNY_STOCK_LONG_BLOCK_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    PENNY_STOCK_LONG_BLOCK_PRICE_USD: float = 5.0  # PORTED from TradierConfig 2026-08-17
    POSITION_CACHE_TTL: int = 5  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    POSITION_REFRESH_INTERVAL: float = 6.0  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    PRICE_CROSS_BACK_BAND_PCT: float = 0.3      # within 0.3% of last_reduction_price  # PORTED from TradierConfig 2026-08-17
    PRICE_CROSS_BACK_MAX_AGE_MIN: float = 525_600_000.0  # 2026-06-02 USER MANDATE: fire FOREVER (~1000yr) until positionAmt>0, not just 4h. Was 240. Momentum still gated by check_reentry_confirmation. ROLLBACK: 240.  # PORTED from TradierConfig 2026-08-17
    PRICE_CROSS_BACK_REENTRY_ENABLED: bool = True  # USER: HAS TO BE ON everywhere (proven MU 0.84 / NVDA 0.71 vec). exit_price-cross reentry.  # PORTED from TradierConfig 2026-08-17
    PRICE_REFRESH_INTERVAL: float = 3.0  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    PRICE_UPDATE_INTERVAL: float = 1.0  # PORTED from TradierConfig 2026-08-17
    PROFIT_TARGET_ENABLED: bool = True  # vector 1186: pnl >= PROFIT_TARGET_PCT exit  # PORTED from TradierConfig 2026-08-17
    PROFIT_TARGET_PCT: float = 1.6  # vector 1187: v3 peak 1.6%  # PORTED from TradierConfig 2026-08-17
    PROXIMITY_TOP_GATE_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    PROXIMITY_TOP_MAX_DROP_PCT: float = 5.0        # don't long when within X% of 52w high  # PORTED from TradierConfig 2026-08-17
    R1_REQUIRE_WT15_ADVERSE: bool = True  # PORTED from TradierConfig 2026-08-17
    R3_HTF_FLIP_NEWBORN_WINDOW_MIN: float = 15.0  # suppress R3 for first 15min after open (USER 2026-06-22); Rollback: 0  # PORTED from TradierConfig 2026-08-17
    RANKING_UPDATE_INTERVAL: float = 180.0  # BACKTEST_CHANGE_T50 was 300 → 180 faster ranking refresh  # PORTED from TradierConfig 2026-08-17
    REBAL_ATTEMPT_COOLDOWN_SEC: float = 3600.0   # 2026-05-26 USER MANDATE — was 300s; bumped to 60min after trc/IBIT_LONG autopsy (40 SENTIMENT_BOOST + 119 SENTIMENT_FADE in 32 realized rounds, -77.85% gain on +26% UP-trending asset; rebalancer pyramided into highs and panic-sold at small dips)  # PORTED from TradierConfig 2026-08-17
    REDIS_CHANNEL_MARKET_DATA: str = "tradier_indicators_channel"  # DEAD_CONFIRMED (priority 15/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    REDIS_CHANNEL_POSITIONS: str = "tradier_positions_channel"  # PORTED from TradierConfig 2026-08-17
    REDIS_CHANNEL_PRICES: str = "tradier_prices_channel"  # DEAD_CONFIRMED (priority 15/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    REDUCTION_COOLDOWN_SECONDS: float = 30.0  # BACKTEST_CHANGE_T38 was 60 → 30s faster rotation  # PORTED from TradierConfig 2026-08-17
    RED_ZONE_TRADIER_MIN_OI_AT_WALL: int = 1000     # require wall strike to have ≥1000 OI (filters spurious thin strikes)  # PORTED from TradierConfig 2026-08-17
    RED_ZONE_TRADIER_STALE_MAX_HOURS: float = 4.0   # skip wall check if cache older than 4h  # PORTED from TradierConfig 2026-08-17
    REENTRY_60MIN_MIN_PCT: float = 0.05                # 2026-04-26: tightened to 0.05% (was 0.3%) — almost pure price-cross with tiny epsilon to avoid bid-ask thrash  # PORTED from TradierConfig 2026-08-17
    REENTRY_60MIN_UNCONDITIONAL_ENABLED: bool = True  # 2026-04-26 user directive — "ANY strategy that reenters when exit price is passed OUTPERFORMS B&H by plain logic"  # PORTED from TradierConfig 2026-08-17
    REENTRY_60MIN_WINDOW_MIN: float = 1440.0            # 2026-04-26: 24h window (was 60min) — wide enough to catch back-cross even after weekend  # PORTED from TradierConfig 2026-08-17
    REENTRY_BREAKOUT_ENABLED: bool = False  # P2-D: exit if price crosses back through the DC level that triggered the reentry  # PORTED from TradierConfig 2026-08-17
    RISK_FREE_RATE: float = 0.045  # PORTED from TradierConfig 2026-08-17
    ROTATION_BOTTOM_N: int = 8   # URGENT_FIX: more short candidates (was 5)  # PORTED from TradierConfig 2026-08-17
    ROTATION_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    ROTATION_HOLD_DAYS: int = 7  # BACKTEST_CHANGE_T44 was 5 → 7 longer hold  # PORTED from TradierConfig 2026-08-17
    ROTATION_LOOKBACK_DAYS: int = 10  # 10-day return lookback (5yr optimal, was 3)  # PORTED from TradierConfig 2026-08-17
    ROTATION_POSITION_SIZE: float = 1200.0  # BACKTEST_CHANGE_T28 was 800 → 1200 larger rotation size  # PORTED from TradierConfig 2026-08-17
    ROTATION_SMA200_FILTER: bool = True  # BACKTEST_CHANGE_T47 filter rotation candidates by SMA200  # PORTED from TradierConfig 2026-08-17
    ROTATION_TOP_N: int = 3       # URGENT_FIX: fewer long positions in bear market (was 5)  # PORTED from TradierConfig 2026-08-17
    ROUND_TRIP_COST_PCT: float = 0.05  # PORTED from TradierConfig 2026-08-17
    RSI2_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    RSI2_ENTRY_THRESHOLD: float = 3.0  # BACKTEST_CHANGE_T48 was 5.0 → 3.0 stricter entry  # PORTED from TradierConfig 2026-08-17
    RSI2_EXIT_THRESHOLD_LONG: float = 70.0  # BACKTEST_CHANGE_T13 was 65 → 70 hold longer  # PORTED from TradierConfig 2026-08-17
    RSI2_EXIT_THRESHOLD_SHORT: float = 30.0  # BACKTEST_CHANGE_T14 was 35 → 30 hold longer  # PORTED from TradierConfig 2026-08-17
    RSI2_POSITION_SIZE: float = 600.0  # BACKTEST_CHANGE_T29 was 800 → 600 align sizing  # PORTED from TradierConfig 2026-08-17
    RSI_ENTRY_LONG_TRADIER: float = 40.0  # A/B 2026-04-17 full 109-sym × 3yr: rsi15<40 Sharpe=0.477 beats <42 and <35. Was 42. Evidence: MOM_rsi15_lt40_rsi1h_lt22 peak.  # PORTED from TradierConfig 2026-08-17
    RSI_ENTRY_PERIOD_TRADIER: int = 10  # BACKTEST_CHANGE_T55: was 2. RSI(10) = OOS champion. Deeper mean-reversion captures bigger moves. Sharpe 6.43, WR 73.9%, PF 8.18  # PORTED from TradierConfig 2026-08-17
    RSI_ENTRY_SHORT_TRADIER: float = 58.0  # BACKTEST_CHANGE_T64: was 70. RSI>58 for shorts.  # PORTED from TradierConfig 2026-08-17
    RSI_EXIT_LONG_TRADIER: float = 85.0  # BACKTEST_CHANGE_T56: was 70. Exit at RSI>85 = let winners run longer. +311% PnL over 4.8yr  # PORTED from TradierConfig 2026-08-17
    RSI_EXIT_SHORT_TRADIER: float = 15.0  # BACKTEST_CHANGE_T56: exit when RSI < 15  # PORTED from TradierConfig 2026-08-17
    RULE_B_5M_EXIT_ENABLED: bool = True   # 2026-05-31 USER: stock RULE B exit — LONG on 5m lower-low+lower-high, SHORT on higher-high+higher-low (trend turning against). PROFIT-GATED (gain>=NOLOSS_MIN) → never closes at a loss, holds losers per protection model. Validated 74/74 stock keys positive (ema anchor). Mirrors crypto RULE_B_3M_EXIT. ROLLBACK: False.  # PORTED from TradierConfig 2026-08-17
    RVOL_MOMENTUM_MIN: float = 1.5  # PORTED from TradierConfig 2026-08-17
    RVOL_SCALP_MIN: float = 1.0  # DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    RVOL_SCORE_BOOST_PCT: float = 0.20  # DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    RVOL_SCORE_BOOST_THRESHOLD: float = 2.0  # DEAD_CONFIRMED (priority 60/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    RZ_BASELINE_TOL: float = 0.05  # Stocks: 5% proximity to mean (vs crypto 3%)  # PORTED from TradierConfig 2026-08-17
    RZ_LTF_MICRO: str = "5m"  # Stocks: 5m base; crypto uses 3m  # PORTED from TradierConfig 2026-08-17
    SATOSHIT_ENTRY_FILTER: bool = False  # T25 2026-04-14 (fixed gates): True=0.388 vs False=0.328 (+18%). Previous stale result (False=0.529) was broken-gate run. Marginal — leaving False until larger sweep.  # PORTED from TradierConfig 2026-08-17
    SBA_COOLDOWN_S_TRADIER: int = 7200  # 2hr between adds (stocks move slower, 2x crypto's 1hr) ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    SCALP_LONG_BUDGET: float = 250.0         # was 500 / orig 1000  # PORTED from TradierConfig 2026-08-17
    SCALP_MAX_HOLD_MINUTES: float = 180.0     # URGENT_FIX: shorter holds, take profits/losses faster (was 300)  # PORTED from TradierConfig 2026-08-17
    SCALP_MAX_POSITIONS_PER_SIDE: int = 6    # BACKTEST_CHANGE_T34 was 8 → 6 concentrate capital  # PORTED from TradierConfig 2026-08-17
    SCALP_MAX_POSITION_SIZE: float = 500.0   # was 1000 / orig 2000  # PORTED from TradierConfig 2026-08-17
    SCALP_MIN_MOVE_PCT: float = 0.003      # Min 0.3% 5m deviation from ema_20_5m — 2026-07-08 GAINMO triage: 0.3→0.003 (consumer treats as FRACTION; 0.3 = 30% = scalps never qualify, unit bug)  # PORTED from TradierConfig 2026-08-17
    SCALP_MIN_REL_VOL: float = 1.1           # Min relative volume to qualify  # PORTED from TradierConfig 2026-08-17
    SCALP_SHORT_BUDGET: float = 250.0        # was 500 / orig 1000  # PORTED from TradierConfig 2026-08-17
    SCALP_START_SIZE: float = 150.0          # was 300 / orig 600  # PORTED from TradierConfig 2026-08-17
    SCALP_STOP_PCT: float = 9.99             # BACKTEST_CHANGE_T12 was 1.5% → 999% effectively disabled NO_LOSS mode  # PORTED from TradierConfig 2026-08-17
    SCALP_TARGET_PCT: float = 0.005           # URGENT_FIX: tighter TP in choppy market, take profits faster (was 0.01 = 1.0% → 0.005 = 0.5%)  # PORTED from TradierConfig 2026-08-17
    SCALP_TOP_MOVERS_N: int = 14             # Candidate pool size  # PORTED from TradierConfig 2026-08-17
    SECTOR_GROUPS: Dict[str, str] = field(default_factory=lambda: {  # PORTED from TradierConfig 2026-08-17
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
    SECTOR_LS_MIN_POSITIONS: int = 3        # don't enforce until ≥3 positions in a sector  # PORTED from TradierConfig 2026-08-17
    SECTOR_LS_RATIO_BYPASS_HEDGE: bool = True  # PORTED from TradierConfig 2026-08-17
    SECTOR_LS_RATIO_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    SECTOR_LS_RATIO_MAX: float = 2.00  # PORTED from TradierConfig 2026-08-17
    SECTOR_LS_RATIO_MIN: float = 0.50  # PORTED from TradierConfig 2026-08-17
    SECTOR_MAP: Dict[str, str] = field(default_factory=lambda: {  # PORTED from TradierConfig 2026-08-17
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
    SENTIMENT_FADE_MODE: str = "DISABLED"  # 2026-06-02 USER MANDATE: was REDUCE. SENTIMENT_FADE rebalance-reduce fires at any gain (~0%), LIVE-ONLY (not in backtest), pyramided-into-highs+panic-sold-dips per IBIT autopsy. DISABLED skips the rebalance entirely. ROLLBACK: REDUCE.  # PORTED from TradierConfig 2026-08-17
    SENTIMENT_REBALANCER_ENABLED: bool = False   # 2026-05-26 USER MANDATE — KILLED after trc/IBIT_LONG -$80k/mo bleed. periodic_sentiment_rebalancing pyramids into winners + flushes on noise. Re-enable only after sample-floor backtest with proper cooldowns + dead zone proves positive Sharpe.  # PORTED from TradierConfig 2026-08-17
    SENTIMENT_REBAL_AUGMENT_DEVIATION_THR: float = 1.0   # was 0.25 — require qty 50% under ideal before any BOOST add  # PORTED from TradierConfig 2026-08-17
    SENTIMENT_REBAL_COOLDOWN_MIN: float = 240.0          # was 30min — sentiment doesn't move that fast  # PORTED from TradierConfig 2026-08-17
    SENTIMENT_REBAL_REDUCE_DEVIATION_THR: float = 0.50   # was 0.20 — require qty 50% over ideal before any FADE reduce  # PORTED from TradierConfig 2026-08-17
    SERVICE_STOP: bool = True  # PORTED from TradierConfig 2026-08-17
    SHORT_STRUCT_EXIT_TF: str = "15m"  # PORTED from TradierConfig 2026-08-17
    SIZING_MODE_TRADIER: str = "DEFAULT"                    # DEFAULT (current MFI-momentum sizing) | ATR_PARITY  # PORTED from TradierConfig 2026-08-17
    SMA200_DIST_LONG_THRESHOLD_4H: float = -10.0  # BACKTEST_CHANGE_T3 only long when price within -10% of SMA200 on 4h  # PORTED from TradierConfig 2026-08-17
    SMA_FILTER_PERIOD_TRADIER: int = 100  # BACKTEST_CHANGE_T57: was 200. SMA100 filter = best OOS. Only LONG above SMA, SHORT below  # PORTED from TradierConfig 2026-08-17
    SMFI_ENABLED: bool = False  # DISABLED 2026-03-30: fake backtest Sharpe. Needs V5 validation.  # PORTED from TradierConfig 2026-08-17
    SMFI_LONG_BUDGET: float = 3000.0  # WIRED 2026-04-16 (priority 75/100) — tradier_manage.py:5286 TRC override destination  # PORTED from TradierConfig 2026-08-17
    SMFI_MAX_HOLD_DAYS: int = 10  # Exit when price > 20SMA or 10d hold ; DEAD_CONFIRMED (priority 75/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    SMFI_MAX_PER_SIDE: int = 5  # Max concurrent SMFI positions per side  # PORTED from TradierConfig 2026-08-17
    SMFI_POSITION_SIZE: float = 600.0  # PORTED from TradierConfig 2026-08-17
    SMFI_SHORT_BUDGET: float = 3000.0  # WIRED 2026-04-16 (priority 75/100) — tradier_manage.py:5286 TRC override destination  # PORTED from TradierConfig 2026-08-17
    SPIKE_FADE_COOLDOWN_BARS: int = 6  # Min bars between entries on same symbol  # PORTED from TradierConfig 2026-08-17
    SPIKE_FADE_ENABLED: bool = False  # 2026-07-10 OFF: never validated (NEW STRATEGY PROHIBITION — needs sweep proof before enable) AND evaluate_spike_fade has a wrong-self bug crashing every cycle (see memory 2026-07-09); it never successfully traded, so this is zero-behavior. Fix the self refs + validate at floor before re-enabling.  # PORTED from TradierConfig 2026-08-17
    SPIKE_FADE_K_EXHAUSTION: float = 70.0  # K5m must be > this (spike up) or < 100-this (spike down)  # PORTED from TradierConfig 2026-08-17
    SPIKE_FADE_LOOKBACK_BARS: int = 6  # 6 bars × 5m = 30min lookback  # PORTED from TradierConfig 2026-08-17
    SPIKE_FADE_MAX_POSITIONS: int = 10  # Max concurrent spike fade positions  # PORTED from TradierConfig 2026-08-17
    SPIKE_FADE_POSITION_SIZE: float = 600.0  # Per-entry size  # PORTED from TradierConfig 2026-08-17
    SPIKE_FADE_THRESHOLD_PCT: float = 2.0  # Min % move in lookback to qualify as spike  # PORTED from TradierConfig 2026-08-17
    SPY_REGIME_BLOCK_LONGS_BELOW: bool = True               # block new LONG opens when SPY < 200SMA  # PORTED from TradierConfig 2026-08-17
    SPY_REGIME_BLOCK_SHORTS_ABOVE: bool = False             # opt-in: block new SHORT opens when SPY > 200SMA  # PORTED from TradierConfig 2026-08-17
    SPY_REGIME_GATE_ENABLED_TRADIER: bool = False           # master flag for the gate  # PORTED from TradierConfig 2026-08-17
    SPY_REGIME_SMA_BARS_DAILY: int = 200                    # daily SMA lookback  # PORTED from TradierConfig 2026-08-17
    SPY_REGIME_SYMBOL: str = "SPY"                          # reference symbol; switch to "QQQ" or other if desired  # PORTED from TradierConfig 2026-08-17
    SQUEEZE_ENABLED: bool = False  # OFF for trb. TRC overrides to True. ; WIRED 2026-04-16 (priority 80/100) — tradier_manage.py:5286 TRC override destination  # PORTED from TradierConfig 2026-08-17
    SQUEEZE_FIRE_BONUS_SCORE: float = 11.25  # PORTED from TradierConfig 2026-08-17
    SQUEEZE_FIRE_ENTRY_ENABLED: bool = True  # 2026-04-27 sweep T1 (S2/91 winners): 9× True. Was False.  # PORTED from TradierConfig 2026-08-17
    SQUEEZE_FIRE_TF: str = "5m"  # PORTED from TradierConfig 2026-08-17
    SQUEEZE_SCORE_BONUS: int = 15  # DEAD_CONFIRMED (priority 80/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    SRS_K_EXIT_1H: float = 85.0                  # v8 engine: SRS exit k_1h threshold (matches K_HIGH above)  # PORTED from TradierConfig 2026-08-17
    STOCH_1H_EXIT_K_MIN: float = 85.0            # v8 engine: stoch cross exit requires k_1h >= 85  # PORTED from TradierConfig 2026-08-17
    STOCH_CROSS_1H_EXIT_ENABLED: bool = True  # BACKTEST_CHANGE_T17 stoch cross on 1h triggers exit  # PORTED from TradierConfig 2026-08-17
    STOCH_CROSS_ENTRY_TRADIER: bool = True  # BACKTEST_CHANGE_T59: was True. Stoch crossover = noise on daily bars. RSI(10) is the real entry.  # PORTED from TradierConfig 2026-08-17
    STOP_LOSS_ENABLED: bool = False  # vector sweep-only cap  # PORTED from TradierConfig 2026-08-17
    STOP_LOSS_PCT: float = 2.0  # PORTED from TradierConfig 2026-08-17
    STRENGTH_FILTER_ENABLED: bool = True  # vector 1181: score >= STRENGTH_MIN_SCORE  # PORTED from TradierConfig 2026-08-17
    STRENGTH_MIN_SCORE: float = 5.0  # PORTED from TradierConfig 2026-08-17
    STRUCTURE_FLIP_REENTRY_BASIS_RESTRICTION_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    STRUCTURE_FLIP_REENTRY_BASIS_TF: str = '4h'  # PORTED from TradierConfig 2026-08-17
    STRUCTURE_FLIP_REENTRY_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    STRUCTURE_FLIP_REENTRY_TF: str = '15m'  # PORTED from TradierConfig 2026-08-17
    SWING_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    SWING_EXIT_TFS: str = "D"                      # start with D only; test 4h then 1h after  # PORTED from TradierConfig 2026-08-17
    SWING_LONG_BUDGET: float = 2500.0      # was 50000 / orig 100000  # PORTED from TradierConfig 2026-08-17
    SWING_MAX_POSITION_SIZE: float = 1100.0   # was 1000 (DEAD)  # PORTED from TradierConfig 2026-08-17
    SWING_REENTER_AT_OR_BELOW_EXIT: bool = True    # THE GUARANTEE — do not disable lightly  # PORTED from TradierConfig 2026-08-17
    SWING_REENTER_MULT: float = 1.0                # size multiplier on the below-exit re-entry  # PORTED from TradierConfig 2026-08-17
    SWING_REENTER_SIGNAL: str = "green_or_hhll"    # green_arrow | hhll | green_or_hhll  # PORTED from TradierConfig 2026-08-17
    SWING_REENTER_TOLERANCE_PCT: float = 0.0       # allow re-entry up to this % ABOVE exit (0=strict)  # PORTED from TradierConfig 2026-08-17
    SWING_RUNAWAY_REENTER: bool = True             # re-enter at START_POSITION_SIZE if price ran away up  # PORTED from TradierConfig 2026-08-17
    SWING_SHORT_BUDGET: float = 2500.0     # was 50000 / orig 100000  # PORTED from TradierConfig 2026-08-17
    SWING_START_SIZE: float = 200.0          # was 400 (DEAD)  # PORTED from TradierConfig 2026-08-17
    THROUGHPUT_DAILY_LOSS_RESET_UTC_MINUTE_TRADIER: int = 30  # PORTED from TradierConfig 2026-08-17
    TIMEFRAMES: List[str] = field(default_factory=lambda: ["1m", "5m", "15m", "1h", "4h", "D"])  # PORTED from TradierConfig 2026-08-17
    TIME_ZONE_ENABLED: bool = True  # BACKTEST_CHANGE_T19 enable time-of-day zone sizing  # PORTED from TradierConfig 2026-08-17
    TRADIER_ACCOUNT_ID: str = os.getenv("TRADIER_ACCOUNT_ID_TRC", "")  # DEAD_CONFIRMED (priority 10/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    TRADIER_API_BASE_URL: str = "https://api.tradier.com/v1"  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    TRADIER_API_KEY: str = os.getenv("TRADIER_API_KEY_TRC", "")  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    TRADIER_DC_DAYTRADE_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES: int = 240  # PORTED from TradierConfig 2026-08-17
    TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION: bool = True  # PORTED from TradierConfig 2026-08-17
    TRADIER_DC_DAYTRADE_STOP_PCT: float = 0.005         # 0.5% hard stop  # PORTED from TradierConfig 2026-08-17
    TRADIER_DC_DAYTRADE_TARGET_PCT: float = 0.005       # REVERTED 2026-05-18 18:30 (was 0.015 since 2026-05-17). 2026-05-17 flip had no sample-floor proof; isolated vec sweep queued.  # PORTED from TradierConfig 2026-08-17
    TRADIER_DC_POSITION_ENTRY_THRESHOLD: float = 0.25   # REVERTED 2026-04-17: see DC_POSITION_ENTRY_THRESHOLD above.  # PORTED from TradierConfig 2026-08-17
    TRADIER_EMERGENCY_ANTI_CHURN_GATES_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    TRADIER_ENTRY_SCORE_THRESHOLD: int = 30             # 2026-04-23 EMERGENCY: raised 24→30. 2.8155 validated winner uses 30. Reduces bad entries.  # PORTED from TradierConfig 2026-08-17
    TRADIER_FH_MOMENTUM_DC_CONFIRM: bool = True         # require DC breakout confirm  # PORTED from TradierConfig 2026-08-17
    TRADIER_FH_MOMENTUM_DC_MAX_LONG: float = 0.33       # only longs in bottom third of DC range  # PORTED from TradierConfig 2026-08-17
    TRADIER_FH_MOMENTUM_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    TRADIER_FH_MOMENTUM_MFI_CONFIRM: bool = True        # require MFI > threshold confirm  # PORTED from TradierConfig 2026-08-17
    TRADIER_FH_MOMENTUM_MFI_MIN: float = 55.0           # min MFI for FH long entry  # PORTED from TradierConfig 2026-08-17
    TRADIER_FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5       # min gap move % to qualify  # PORTED from TradierConfig 2026-08-17
    TRADIER_FH_MOMENTUM_WINDOW_MINUTES: int = 60        # FH window in minutes after 13:30 UTC  # PORTED from TradierConfig 2026-08-17
    TRADIER_INDICATORS_CYCLE_CONCURRENCY: int = 24       # was 4  # PORTED from TradierConfig 2026-08-17
    TRADIER_INDICATORS_HTTP_CONCURRENCY: int = 48        # was 15  # PORTED from TradierConfig 2026-08-17
    TRADIER_INDICATORS_IDLE_SLEEP_SEC: float = 1.0       # was 15  # PORTED from TradierConfig 2026-08-17
    TRADIER_INDICATORS_NARROW_UNIVERSE: bool = True  # PORTED from TradierConfig 2026-08-17
    TRADIER_K_ZONE_ENTRY_BONUS_TRADIER: int = 25        # score add when K in zone  # PORTED from TradierConfig 2026-08-17
    TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER: int = 35     # S1_SWEEP_2026-04-15: 35 top S1 cfg Sharpe=4.23 on 20605 trades (was 80)  # PORTED from TradierConfig 2026-08-17
    TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER: int = 65    # S1_SWEEP_2026-04-15: 65 top S1 cfg Sharpe=4.23 on 20605 trades (was 20)  # PORTED from TradierConfig 2026-08-17
    TRADIER_LOCAL_EXTREMES_SCORING_ENABLED: bool = False  # 2026-04-26 KILL: tier sizing was suffocating PnL; Phase 8 disable = 90× PnL boost in v8  # PORTED from TradierConfig 2026-08-17
    TRADIER_LONG_ONLY_ENTRIES: bool = False        # 2026-07-20 USER band mandate: skip SHORT entries entirely (shorts were squatting symbols and blocking LONG band entries via has_opposing_pos)  # PORTED from TradierConfig 2026-08-17
    TRADIER_MFI_ENTRY_LONG_ENABLED: bool = False  # FLIPPED 2026-08-10 03:25: was True blocks all AAPL longs MFI 100>80, need live results GDX/HAO/LLY; vector bypasses, real engine gated  # PORTED from TradierConfig 2026-08-17
    TRADIER_MFI_ENTRY_LONG_TRADIER: float = 60.0        # MFI > this for long entry  # PORTED from TradierConfig 2026-08-17
    TRADIER_MIN_HOLD_MINUTES: float = 4320.0  # 2026-07-08 GAINMO triage: 42→4320 restore. 2026-04-27 user rule: 72h minimum hold. Stocks are NOT scalps — peak-giveback / micro-scalp / market-bias closes must wait 72h. Was 100 (le_dynamic winner) → bleeding from premature exits on MU/SNDK/MSFT/INTC/GOOGL.  # PORTED from TradierConfig 2026-08-17
    TRADIER_MI_ENTRY_ENABLED_TRADIER: bool = False      # wait for MI reset before entry  # PORTED from TradierConfig 2026-08-17
    TRADIER_MI_EXIT_ENABLED_TRADIER: bool = False       # REVERTED 2026-04-17: see MI_EXIT_ENABLED_TRADIER above.  # PORTED from TradierConfig 2026-08-17
    TRADIER_MI_SUBSIGNAL_MIN_COUNT: int = 3             # N of 5 sub-signals must fire  # PORTED from TradierConfig 2026-08-17
    TRADIER_NOLOSS_SRS_BYPASS: bool = True          # True=SRS reason bypasses NOLOSS; False=no reason bypass  # PORTED from TradierConfig 2026-08-17
    TRADIER_OI_INJECT_ENABLED: bool = True         # 2026-04-28 restored — probe one-by-one  # PORTED from TradierConfig 2026-08-17
    TRADIER_OI_INJECT_MAX_EACH: int = 10            # cap per side  # PORTED from TradierConfig 2026-08-17
    TRADIER_OI_INJECT_MIN_TOTAL_OI: int = 1000     # require ≥1000 contracts open across all monitored exps (filters illiquid names)  # PORTED from TradierConfig 2026-08-17
    TRADIER_OI_INJECT_NEAR_MONEY_PREFER: bool = True   # use near_money_pc_ratio (±5% strikes) when present — purer near-term sentiment  # PORTED from TradierConfig 2026-08-17
    TRADIER_OI_INJECT_PC_BEARISH: float = 1.4      # P/C above this → put OI dominates → SHORT bias inject  # PORTED from TradierConfig 2026-08-17
    TRADIER_OI_INJECT_PC_BULLISH: float = 0.6      # P/C below this → call OI dominates → LONG bias inject  # PORTED from TradierConfig 2026-08-17
    TRADIER_OI_INJECT_STALE_MAX_HOURS: float = 4.0 # skip cache files older than 4h (fetcher missed last cycle)  # PORTED from TradierConfig 2026-08-17
    TRADIER_POST_CLOSE_COOLDOWN_MIN: float = 15.0  # PORTED from TradierConfig 2026-08-17
    TRADIER_QUEUE_DEDUPE_SEC: float = 60.0       # global queue_trade_action dedupe  # PORTED from TradierConfig 2026-08-17
    TRADIER_RATIO_BOOST_MIN_GAIN_PCT: float = 1.0  # Min gain for ratio boost to fire  # PORTED from TradierConfig 2026-08-17
    TRADIER_RATIO_REQUIRE_MIN_GAIN: bool = False   # Block RATIO_BOOST on positions with gain < min  # PORTED from TradierConfig 2026-08-17
    TRADIER_REENTRY_ANTI_CHURN_ENABLED: bool = False  # 2026-05-10 USER MANDATE: REENTRY guaranteed — ANTI_CHURN_exit_score gate was blocking reentries when wt_dc still indicated exit. Default OFF.  # PORTED from TradierConfig 2026-08-17
    TRADIER_REENTRY_HARDCOOL_MIN: float = 30.0  # 2026-04-26 NEW: was hardcoded at tradier_manage.py:5392. Default 30 preserves prior behavior. Sweep candidate values: 5/10/15/30. Lower → more reentry surface (helps reentry_rate=3.7% problem) but risk of churn the 30-min was originally protecting against.  # PORTED from TradierConfig 2026-08-17
    TRADIER_REENTRY_OVERDUE_BYPASS_ENABLED: bool = True  # True=legacy (bypass after 48h); False=always enforce stoch  # PORTED from TradierConfig 2026-08-17
    TRADIER_REENTRY_RZ_BLOCK_ENABLED: bool = False    # 2026-05-10 USER MANDATE: REENTRY guaranteed — RZ_BLOCK_REENTRY_LONG_AT_TOP / SHORT_AT_BOTTOM gate was blocking reentries via DELTA zone. Default OFF.  # PORTED from TradierConfig 2026-08-17
    TRADIER_REOPEN_WAIT_S: float = 0.0       # Was 300s (5 min); zero for instant reentry  # PORTED from TradierConfig 2026-08-17
    TRADIER_REQUIRE_TRADEABLE_KEY: bool = True     # Gate entry at execute_now if not in tradeable_keys  # PORTED from TradierConfig 2026-08-17
    TRADIER_RESET_MAX_GAIN_ON_CLOSE: bool = True  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI2_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI2_EXIT_THRESHOLD_LONG: float = 90.0      # exit long when RSI2 > this  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI2_EXIT_THRESHOLD_SHORT: float = 10.0     # exit short when RSI2 < this  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_ENTRY_LONG_TRADIER: float = -1.0        # SENTINEL: <0 => DISABLED (long uses MFI)  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_ENTRY_SHORT_TRADIER: float = 70.0       # RSI > this to consider short (LEGACY: single-TF default)  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_LONG_15M: float = 40.0                  # A/B 2026-04-17 winner (was 42 single-threshold)  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_LONG_1H: float = 22.0                   # A/B 2026-04-17 REAL LEVER (was not per-TF)  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_LONG_4H: float = 35.0  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_LONG_5M: float = 35.0  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_LONG_D: float = 40.0  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_SHORT_15M: float = 65.0                 # validated top short cluster rsi15_gt_65  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_SHORT_1H: float = 65.0                  # validated top short cluster rsi1h_gt_65  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_SHORT_4H: float = 60.0  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_SHORT_5M: float = 65.0  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_SHORT_D: float = 55.0  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_SHORT_REL_VOLUME_MIN: float = 2.4  # relative vol > 1.2× avg required  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_SHORT_RVOL_15M: float = 1.0  # PORTED from TradierConfig 2026-08-17
    TRADIER_RSI_SHORT_RVOL_1H: float = 1.0  # PORTED from TradierConfig 2026-08-17
    TRADIER_SANDBOX_URL: str = "https://sandbox.tradier.com/v1"  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    TRADIER_STOCH_ENTRY_LONG_TRADIER: int = 30          # K < this for normal long entry  # PORTED from TradierConfig 2026-08-17
    TRADIER_STOCH_ENTRY_SHORT_TRADIER: int = 52  # K > this for normal short entry  # PORTED from TradierConfig 2026-08-17
    TRADIER_STOCH_EXTREME_LONG_TRADIER: int = 15        # deeper K for high-conviction long  # PORTED from TradierConfig 2026-08-17
    TRADIER_STOCH_EXTREME_SHORT_TRADIER: int = 85       # deeper K for high-conviction short  # PORTED from TradierConfig 2026-08-17
    TRADIER_STREAMING_URL: str = "https://stream.tradier.com/v1"  # DEAD_CONFIRMED (priority 20/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    TRADIER_SYMBOLS_FILE: Path = BASE_PATH / "symbols_tradier.json"  # PORTED from TradierConfig 2026-08-17
    TRADIER_WS_URL: str = "wss://ws.tradier.com/v1"  # PORTED from TradierConfig 2026-08-17
    TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER: bool = True  # PORTED from TradierConfig 2026-08-17
    TRADIER_WT_EXIT_MIN_TFS_TRADIER: int = 5             # REVERTED 2026-04-17: 4 = exits too eagerly. Mar-30 baseline = 5 (require ALL 5 TFs against). Patient exits.  # PORTED from TradierConfig 2026-08-17
    TRADIER_WT_EXIT_TFS_TRADIER: str = "5m+15m+1h+4h+D"  # T4 sweep: 5m+15m+1h+4h+D avg=5.961 best tested (was "3m,15m,1h")  # PORTED from TradierConfig 2026-08-17
    TRAILING_AUG_ENABLED_TRADIER: bool = False  # PORTED from TradierConfig 2026-08-17
    TRAILING_AUG_GAIN_STEP_PCT: float = 0.5  # PORTED from TradierConfig 2026-08-17
    TRAILING_AUG_MAX_PER_POSITION: int = 3  # PORTED from TradierConfig 2026-08-17
    TRAILING_AUG_MIN_GAIN_PCT: float = 0.5  # PORTED from TradierConfig 2026-08-17
    TRA_ALLOW_BUYS: bool = False                   # USER 2026-08-14: tra NEVER buys, only sells before loss in selloff                       # tra is cash account → no shorts EVER  # PORTED from TradierConfig 2026-08-17
    TRA_BUY_COOLDOWN_AFTER_SELL_HOURS: float = 96.0     # 4 DAYS no buying after a sell on cash (prevent GFV 6th flag = ban)  # PORTED from TradierConfig 2026-08-17
    TRA_DISABLE_AUGMENT: bool = True                 # no churn from augments either  # PORTED from TradierConfig 2026-08-17
    TRA_DISABLE_DELTA_ENTRY: bool = True             # delta engine is too fast for long-term hold  # PORTED from TradierConfig 2026-08-17
    TRA_LONG_ONLY: bool = True                       # tra is cash account → no shorts EVER  # PORTED from TradierConfig 2026-08-17
    TRA_MAX_BUYS_PER_DAY: int = 1                   # GFV guard: at most 1 buy per calendar day for tra (cash acct, 5 flags)  # PORTED from TradierConfig 2026-08-17
    TRA_MIN_HOLD_MINUTES: float = 5760.0             # 4 DAYS hold floor - cash GFV 5 flags, prevent 6th ban  # PORTED from TradierConfig 2026-08-17
    TRA_NO_LOSS_EXIT: bool = False                    # tra never closes a position at a loss  # PORTED from TradierConfig 2026-08-17
    TRA_PREFERRED_SYMBOLS: List[str] = field(default_factory=lambda: ["AAPL", "MSFT", "GOOGL", "MSTR", "PLTR", "NEM", "MU", "SNDK", "NVDA"])  # DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    TRA_SATOSHIT_ONLY: bool = True  # PORTED from TradierConfig 2026-08-17
    TRA_STRICT_EXIT_ONLY: bool = True                # only the 5-of-5 STRICT_EXIT gate counts  # PORTED from TradierConfig 2026-08-17
    TRA_WT_DC_ENTRY_THRESHOLD: float = 85.0          # REVERTED 2026-08-11 per SWITCH_LAB_VECTOR_LIVE_AUDIT.md M3 — live bypass removed, vector+live parity restored; re-promote only via 1yr Tier-2  # PORTED from TradierConfig 2026-08-17
    TRB_MAX_CALL_VALUE: float = 750.0      # was 1500 / orig 3000  # PORTED from TradierConfig 2026-08-17
    TRB_MAX_LONG_VALUE: float = 12500.0    # was 25000 / orig 50000  # PORTED from TradierConfig 2026-08-17
    TRB_MAX_PUT_VALUE: float = 750.0       # was 1500 / orig 3000  # PORTED from TradierConfig 2026-08-17
    TRB_MAX_SHORT_VALUE: float = 12500.0   # was 25000 / orig 50000  # PORTED from TradierConfig 2026-08-17
    TRB_MAX_SYMBOL_VALUE: float = 2500.0  # was 5000 / orig 10000 — 2026-04-27 second cut  # PORTED from TradierConfig 2026-08-17
    TRB_NOLOSS_MIN_PROFIT_PCT: float = 0.0  # 2026-04-08: TECHNICALS ONLY. Was 3.0% which blocked all exits on losers. ; DEAD_CONFIRMED (priority 70/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    TRC_5M_SWEEP_BENCHMARK: str = "SPY"  # PORTED from TradierConfig 2026-08-17
    TRC_5M_SWEEP_BUFFER_N: int = 20  # PORTED from TradierConfig 2026-08-17
    TRC_5M_SWEEP_DELTA_WEIGHT: float = 0.3  # PORTED from TradierConfig 2026-08-17
    TRC_5M_SWEEP_ENABLED: bool = True  # PORTED from TradierConfig 2026-08-17
    TRC_5M_SWEEP_TOP_N: int = 8  # PORTED from TradierConfig 2026-08-17
    TRC_5M_SWEEP_Z_WEIGHT: float = 0.7  # PORTED from TradierConfig 2026-08-17
    TRC_BEAR_MARKET_MODE: bool = False  # No bear penalty — test both directions equally  # PORTED from TradierConfig 2026-08-17
    TRC_CLENOW_ENABLED: bool = True  # 2026-06-02 V8-VALIDATED → KEPT ON (USER). Faithful vec backtest (tools/bt_clenow.py, NPZ clenow_score_D = same slope×R² momentum live reads): pool_sharpe=+0.1815, gain_per_mo=+8.5%, total=+221% over 96 trades/35 syms — a REAL gain-augmenting momentum edge. Augments gains → ON in live. (Gain-augmenting strategy NOT fully modeled in vec sweep → controlled by PARITY_COMPARISON_MODE master switch: only flipped off during a live↔backtest parity A/B, then back on.)  # PORTED from TradierConfig 2026-08-17
    TRC_CLENOW_POSITION_SIZE: float = 2640.0  # PORTED from TradierConfig 2026-08-17
    TRC_CONNORS_RSI_ENABLED: bool = False  # 2026-06-02 V8-VALIDATED → LOSER, turned OFF per user parity policy. Faithful vec backtest (tools/backtest_connors_rsi_vec.py, uses NPZ connors_rsi_D + sma_200_D, same inputs as live): pool_sharpe=-0.5754, gain_per_mo=-15.25%, total_gain=-555% over 139 trades/34 syms. Oversold-mean-reversion catches falling knives on this universe. OFF in both live (was trc-on) AND backtest by default. ROLLBACK: True (but don't — it loses).  # PORTED from TradierConfig 2026-08-17
    TRC_CONNORS_RSI_POSITION_SIZE: float = 1980.0  # PORTED from TradierConfig 2026-08-17
    TRC_DC_DAYTRADE_LONG_BUDGET: float = 9900.0    # was 4950 / orig 9900  # PORTED from TradierConfig 2026-08-17
    TRC_DC_DAYTRADE_SHORT_BUDGET: float = 9900.0   # was 4950 / orig 9900  # PORTED from TradierConfig 2026-08-17
    TRC_DC_DAYTRADE_START_SIZE: float = 1980.0   # was 990 / orig 1980  # PORTED from TradierConfig 2026-08-17
    TRC_ENTRY_MIN_ALIGNMENT: int = 4  # 2026-06-09: 6→4. ROLLBACK: 6.  # PORTED from TradierConfig 2026-08-17
    TRC_ENTRY_ZONE_LONG: float = 30.0  # Local extremes: deeper oversold bottom (was 30)  # PORTED from TradierConfig 2026-08-17
    TRC_ENTRY_ZONE_SHORT: float = 70.0  # Local extremes: deeper overbought top (was 70)  # PORTED from TradierConfig 2026-08-17
    TRC_EPISODIC_PIVOT_ENABLED: bool = False  # Disabled — trc now runs local extremes only  # PORTED from TradierConfig 2026-08-17
    TRC_EP_POSITION_SIZE: float = 2640.0  # PORTED from TradierConfig 2026-08-17
    TRC_GAP_FILL_POSITION_SIZE: float = 1980.0   # was 990 / orig 1980  # PORTED from TradierConfig 2026-08-17
    TRC_LOCAL_EXTREMES_SCORER_ENABLED: bool = True  # Use local_extremes_scorer for dynamic $50-$5000 sizing  # PORTED from TradierConfig 2026-08-17
    TRC_LS_RATIO_MAX: float = 3.00  # Wider than trb (2.00)  # PORTED from TradierConfig 2026-08-17
    TRC_LS_RATIO_MIN: float = 0.30  # Wider than trb (0.50)  # PORTED from TradierConfig 2026-08-17
    TRC_MAX_CONCURRENT_POSITIONS: int = 32    # was 20 / orig 40  # PORTED from TradierConfig 2026-08-17
    TRC_MAX_DAILY_LOSS_PCT: float = 10.0  # 3.3x trb (3%) — paper money, let it run  # PORTED from TradierConfig 2026-08-17
    TRC_MAX_ORDER_VALUE: float = 1250.0       # was 2500 / orig 5000  # PORTED from TradierConfig 2026-08-17
    TRC_MAX_POSITION_SIZE: float = 3750.0     # was 2500 / orig 5000  # PORTED from TradierConfig 2026-08-17
    TRC_MAX_SYMBOL_VALUE: float = 1250.0  # was 2500 / orig 5000 — 2026-04-27 second cut  # PORTED from TradierConfig 2026-08-17
    TRC_MINERVINI_ENABLED: bool = True  # Paper-only: needs V5 validation before trb  # PORTED from TradierConfig 2026-08-17
    TRC_MINERVINI_LONG_BUDGET: float = 13200.0  # PORTED from TradierConfig 2026-08-17
    TRC_MINERVINI_POSITION_SIZE: float = 2640.0  # PORTED from TradierConfig 2026-08-17
    TRC_MOMENTUM_FADE_ENABLED: bool = False  # Disabled — trc now runs local extremes only  # PORTED from TradierConfig 2026-08-17
    TRC_NOLOSS_MIN_PROFIT_PCT: float = 0.0  # 2026-07-08 GAINMO triage: 0.5→0.0 — this silently resurrected the killed NOLOSS gate on trc only (272 blocked closes/hr, 8 stuck losers incl -31.74%; STRICT_NO_LOSS is ELIMINATED per STATE OF AFFAIRS)  # PORTED from TradierConfig 2026-08-17
    TRC_ORB_ENABLED: bool = False  # Disabled — trc now runs local extremes only  # PORTED from TradierConfig 2026-08-17
    TRC_ORB_LONG_BUDGET: float = 6600.0  # PORTED from TradierConfig 2026-08-17
    TRC_ORB_POSITION_SIZE: float = 1980.0  # PORTED from TradierConfig 2026-08-17
    TRC_ORB_SHORT_BUDGET: float = 6600.0  # PORTED from TradierConfig 2026-08-17
    TRC_ROTATION_POSITION_SIZE: float = 3000.0   # was 1500 / orig 3000  # PORTED from TradierConfig 2026-08-17
    TRC_RSI2_POSITION_SIZE: float = 1980.0     # was 990 / orig 1980  # PORTED from TradierConfig 2026-08-17
    TRC_SCALP_LONG_BUDGET: float = 1250.0     # was 2500 / orig 5000  # PORTED from TradierConfig 2026-08-17
    TRC_SCALP_MAX_POSITIONS_PER_SIDE: int = 12   # was 10 / orig 20  # PORTED from TradierConfig 2026-08-17
    TRC_SCALP_SHORT_BUDGET: float = 1250.0    # was 2500 / orig 5000  # PORTED from TradierConfig 2026-08-17
    TRC_SCALP_START_SIZE: float = 495.0       # was 500 / orig 1000  # PORTED from TradierConfig 2026-08-17
    TRC_SCALP_TARGET_PCT: float = 0.01  # 2x trb (0.005) — let winners run further  # PORTED from TradierConfig 2026-08-17
    TRC_SMFI_ENABLED: bool = False  # 2026-06-02 V8-VALIDATED → NOISE-tier, off per user parity policy. Faithful vec backtest (tools/bt_smfi.py, replicates compute_smfi cumulative smart-money-flow + 20d bull-divergence on NPZ daily OHLC — same formula as live): pool_sharpe=+0.0534 (Noise), gain_per_mo=+10.2%, total=+264% over 719 trades/37 syms. POSITIVE in raw gain but risk-adjusted NOISE (0.05 << 0.48 Minervini / 0.44 baseline) + 719 trades = commission churn → does NOT improve the book → OFF both. NOTE: it IS backtestable (OHLC formula, not order-flow); this is a validated-noise cut, NOT a can't-backtest case. ROLLBACK: True (if you want the raw +10%/mo despite the churn).  # PORTED from TradierConfig 2026-08-17
    TRC_SMFI_LONG_BUDGET: float = 9900.0  # PORTED from TradierConfig 2026-08-17
    TRC_SMFI_POSITION_SIZE: float = 1980.0  # PORTED from TradierConfig 2026-08-17
    TRC_SMFI_SHORT_BUDGET: float = 9900.0  # PORTED from TradierConfig 2026-08-17
    TRC_SQUEEZE_ENABLED: bool = False  # Disabled — trc now runs local extremes only  # PORTED from TradierConfig 2026-08-17
    TRC_START_POSITION_SIZE: float = 330.0    # was 500 / orig 1000  # PORTED from TradierConfig 2026-08-17
    TRC_SWING_LONG_BUDGET: float = 100000.0    # was 50000 / orig 100000  # PORTED from TradierConfig 2026-08-17
    TRC_SWING_SHORT_BUDGET: float = 100000.0   # was 50000 / orig 100000  # PORTED from TradierConfig 2026-08-17
    TR_TREND_V1_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    TR_TREND_V1_SHADOW_LOG_ONLY: bool = True  # PORTED from TradierConfig 2026-08-17
    TR_TREND_V1_SHADOW_SYMBOLS: tuple = ('TRGP', 'SNDK', 'AVGO', 'GLD', 'PLTR', 'MU', 'CDE', 'SLV')  # PORTED from TradierConfig 2026-08-17
    TSMOM_BOOK_SCALAR_ENABLED: bool = True  # 2026-04-27 sweep T1: 8× True in winners. Was False.  # PORTED from TradierConfig 2026-08-17
    TSMOM_HIGH_CAP: float = 1.5  # PORTED from TradierConfig 2026-08-17
    TSMOM_LOOKBACK_BARS: int = 252  # PORTED from TradierConfig 2026-08-17
    TSMOM_LOW_CAP: float = 0.25  # PORTED from TradierConfig 2026-08-17
    TSMOM_MIN_AGREEMENT: float = 0.5  # PORTED from TradierConfig 2026-08-17
    VEL_EXIT_ENABLED: bool = True  # vector 1131: gates wt_vel_4h <-2 / >2 exit  # PORTED from TradierConfig 2026-08-17
    VIX_EXTREME_THRESHOLD: float = 40.0  # PORTED from TradierConfig 2026-08-17
    VIX_PANIC_THRESHOLD: float = 30.0  # PORTED from TradierConfig 2026-08-17
    VIX_REGIME_FILTER_ENABLED: bool = True  # Block entries when SPY < SMA200  # PORTED from TradierConfig 2026-08-17
    VIX_REGIME_SIZE_MULT_HIGH_VOL: float = 0.5   # VIX > 200dMA → 50% size  # PORTED from TradierConfig 2026-08-17
    VIX_REGIME_SIZE_MULT_PANIC: float = 0.0      # VIX > 30 → halt new entries  # PORTED from TradierConfig 2026-08-17
    VIX_SMA_LOOKBACK_DAYS: int = 200  # PORTED from TradierConfig 2026-08-17
    VIX_VOLATILITY_REGIME_ENABLED: bool = True   # 2026-04-26: VIX vs VIX-200dMA gate (NOT SPY-SMA200 — that's L1061); 32% DD reduction documented  # PORTED from TradierConfig 2026-08-17
    VOL_TARGET_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    VOL_TARGET_FIELD: str = "yz_vol_60_d"   # NPZ field (Yang-Zhang 60d Daily)  # PORTED from TradierConfig 2026-08-17
    VOL_TARGET_HIGH_CAP: float = 2.0  # PORTED from TradierConfig 2026-08-17
    VOL_TARGET_LOW_CAP: float = 0.25  # PORTED from TradierConfig 2026-08-17
    VOL_TARGET_PCT: float = 20.0       # target annualized vol % (S&P 15-25% range)  # PORTED from TradierConfig 2026-08-17
    VWAP_BOUNCE_DIST_PCT: float = 0.3  # Price must be within 0.3% of VWAP for bounce entry ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    VWAP_BOUNCE_ENTRY_ENABLED: bool = True  # Enter on VWAP bounce (pullback to VWAP + reversal) ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    VWAP_FILTER_ENABLED: bool = False  # 2026-04-20 sweep: VWAP filter suppresses valid trades — top 20 mega configs all False  # PORTED from TradierConfig 2026-08-17
    VWAP_SCORE_BONUS: int = 10  # Score bonus when price is on correct side of VWAP ; DEAD_CONFIRMED (priority 85/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    WT_3M_FORCE_OPEN_BUILD_TO_TARGET: bool = True       # keep adding (with-trend, on 3m WT bounce) until target  # PORTED from TradierConfig 2026-08-17
    WT_3M_FORCE_OPEN_DIST_PCT: float = 0.0              # required % beyond sma_200_15m (0 = just "above"); set >0 for a buffer  # PORTED from TradierConfig 2026-08-17
    WT_3M_FORCE_OPEN_TARGET_USD: float = 2000.0         # 2026-06-24 ROLLED BACK: $50k was entire trb portfolio in 1 sym; $2k = ~3% of $70k per confirmed winner  # PORTED from TradierConfig 2026-08-17
    WT_3M_FORCE_OPEN_TF_LADDER: bool = True             # scale size by # of HTFs (15m/1h/4h/D) confirming  # PORTED from TradierConfig 2026-08-17
    WT_3M_FORCE_OPEN_TF_LADDER_MULT: float = 1.0        # extra ×mult per confirming HTF (3m base ×1, +1.0 each)  # PORTED from TradierConfig 2026-08-17
    WT_3M_FORCE_OPEN_USE_SMA200: bool = True            # anchor sma_200_15m (user spec) vs ema_200_15m  # PORTED from TradierConfig 2026-08-17
    WT_COMPOSITE_VETO_ENABLED_TRADIER: bool = False  # VARIANCE_FIX 2026-04-14: when True, WT_COMPOSITE_SCORING_ENABLED vetoes wt_dc entries lacking composite alignment. Default False = live unchanged.  # PORTED from TradierConfig 2026-08-17
    WT_CROSSUNDER_15M_SHORT: bool = True  # BACKTEST_CHANGE_T5 enable WT crossunder on 15m for short entries  # PORTED from TradierConfig 2026-08-17
    WT_CROSSUNDER_FINAL_ENABLED: bool = True  # T25 2026-04-14: True=0.357 vs False=0.363 (Δ=0.006) — essentially noise. Keeping True for live WT exit coverage.  # PORTED from TradierConfig 2026-08-17
    WT_DC_DIRECT_COMBINED_STOCH_GATE: float = 100.0  # PORTED from TradierConfig 2026-08-17
    WT_DC_DIRECT_COMPLETED_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    WT_DC_DIRECT_HTF_ALIGN_REQUIRED: int = 0  # PORTED from TradierConfig 2026-08-17
    WT_DC_DIRECT_HTF_GATE: str = "none"  # PORTED from TradierConfig 2026-08-17
    WT_DC_DIRECT_THRESHOLD: float = 20.0  # PORTED from TradierConfig 2026-08-17
    WT_DC_ENTRY_BAR_MATURITY_BLOCK: float = 0.7  # PORTED from TradierConfig 2026-08-17
    WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED: bool = False  # PORTED from TradierConfig 2026-08-17
    WT_DC_ENTRY_K5M_MAX_LONG: float = 100.0  # 2026-04-27: hard k5m cap for WT_DC_ENTRY_THRESHOLD-path LONG entries (default inert at 100). Lower to 80 to block "buy at 5m top" e.g. NVDA k5m=95.  # PORTED from TradierConfig 2026-08-17
    WT_DC_ENTRY_K5M_MIN_SHORT: float = 0.0   # 2026-04-27: hard k5m floor for WT_DC_ENTRY_THRESHOLD-path SHORT entries (default inert at 0). Raise to 20 to block "short at 5m bottom".  # PORTED from TradierConfig 2026-08-17
    WT_DC_ENTRY_THRESHOLD: float = 45  # 2026-06-24 ROLLED BACK: bt_wtdc_threshold (291 stocks) — LONG ps 0.092→0.123 (+33%), SHORT ps 0.082→0.113 (+38%) at 45 vs 20. Gain/mo essentially unchanged (+3.82%/+3.20% vs +3.89%/+3.14%). Prior test (2026-06-03) optimized gain/mo not pool_sharpe — lower pool_sharpe = lower live quality.  # PORTED from TradierConfig 2026-08-17
    WT_DC_EXIT_ENABLED: bool = True  # path-scoped master; False skips only the WT_DC scorer exit  # PORTED from TradierConfig 2026-08-17
    WT_DC_EXIT_STALE_MAX_S: int = 600  # Don't exit on indicators > 10min stale (protects against stale data firing exits)  # PORTED from TradierConfig 2026-08-17
    WT_DC_HTF_GATE: str = "4h_D"                     # 2026-07-14 RESTORED 1h->4h_D: the 2026-05-21 loosening to "1h" (for trade-frequency reasons) silently re-opened the EXACT "SHORT-into-uptrend" gap this gate was built to close on 2026-04-27 (see WT_DC_ENTRY comment ~3131) -- a 1h-only check can't see a multi-week Daily uptrend. Live proof: PLTR_SHORT (trb, opened 2026-07-09, reason WT_DC_ENTRY_60_4h_bear|1h_cross_BEAR) and IBIT_SHORT (trb, opened 2026-07-13, same pattern) both entered on 1h/4h bearish WT crosses DURING pullbacks inside established multi-week uptrends (PLTR +25% off its 06-25 low, IBIT +9% off its 06-30 low, both still rising at entry) -- textbook countertrend entries, not "top of a bounce in a downtrend". Both ran hard against (PLTR -6.8%, IBIT required manual close). Trade-off: "4h" alone caused 28/day blocks on trb per the 05-21 note; "4h_D" is the strongest documented setting and is the one the original 04-27 fix intended. ROLLBACK: "1h" (accepts the uptrend-short risk for more trade frequency) or "4h" (partial). Values: 'none' / '1h' / '4h' / '4h_D'  # PORTED from TradierConfig 2026-08-17
    WT_D_BOUNCE_AUG_COOLDOWN_HOURS: float = 1.0  # min hours between wt_D augments per position  # PORTED from TradierConfig 2026-08-17
    WT_D_BOUNCE_AUG_ENABLED: bool = False  # 2026-04-23 EMERGENCY: disabled — was augmenting losers at gain<0. Best 2.8155 config has this False.  # PORTED from TradierConfig 2026-08-17
    WT_D_BOUNCE_AUG_MULTIPLIER: float = 2.0  # 2026-04-20: 2x (add 1x to existing) per user directive. Was 4x.  # PORTED from TradierConfig 2026-08-17
    WT_D_BOUNCE_AUG_REQUIRE_HIGHER_PRICE: bool = True  # 2026-04-20: require bounce price > last aug price (higher low for LONG)  # PORTED from TradierConfig 2026-08-17
    WT_D_BOUNCE_AUG_REQUIRE_HIGHER_WT: bool = True  # 2026-04-20: require bounce WT > last aug WT (was False)  # PORTED from TradierConfig 2026-08-17
    WT_D_BOUNCE_DD_STOP_ENABLED: bool = True  # 2026-04-20: cut extra DD leg if price continues below aug price  # PORTED from TradierConfig 2026-08-17
    WT_EXIT_MIN_TFS_TRADIER: int = 5  # REVERTED 2026-04-17: 4 = exits too eagerly. Mar-30 baseline = 5. Patient exits.  # PORTED from TradierConfig 2026-08-17
    WT_EXIT_TFS_TRADIER: str = "5m+15m+1h+4h+D"  # PORTED from TradierConfig 2026-08-17
    WT_EXIT_VELOCITY_TRADIER: bool = False  # SWEEP: velocity makes zero difference. Cross is simpler. ; DEAD_CONFIRMED (priority 55/100) — no plausible wiring site found 20260416  # PORTED from TradierConfig 2026-08-17
    WT_EXIT_VETO_ENABLED_TRADIER: bool = False  # SENTINEL_FIX 2026-04-14: when True, WT_EXIT_MIN_TFS_TRADIER actually gates exits. Default False = live unchanged.  # PORTED from TradierConfig 2026-08-17
    WT_FORCE_OPEN_FRESH_CROSS_ONLY: bool = False        # [2026-06-27] True=fire only on a FRESH WT cross event (wt_cross_bull/bear), not the standing wt1>wt2 state. Standing-state on higher TF churned MORE (688→940 tr/sym/yr); cross-event fires once per cross. A/B-tested.  # PORTED from TradierConfig 2026-08-17
    WT_FORCE_OPEN_FRESH_MAX_BARS: int = 0               # [2026-06-27] freshness window for FRESH_CROSS_ONLY. 0=this bar only (strictest); N=within N bars of cross (uses wt_cross_bars_ago + wt_cross_rising direction).  # PORTED from TradierConfig 2026-08-17
    WT_FORCE_OPEN_TRIGGER_TF: str = "5m"                # [2026-06-26] force-open WT-cross trigger TF. 5m=current (fires every 5m bar=churn); 15m/1h fire less=less churn. A/B-tested 5m vs 15m vs 1h vs OFF; keep best.  # PORTED from TradierConfig 2026-08-17
    WT_W_EXIT_ENABLED: bool = False      # Exit when weekly WaveTrend crosses against position.  # PORTED from TradierConfig 2026-08-17
    ZONE_CLOSE_THRESHOLD: int = 20  # BACKTEST_CHANGE_T22 minutes before close = "close zone"  # PORTED from TradierConfig 2026-08-17
    ZONE_MID_THRESHOLD: int = 30  # BACKTEST_CHANGE_T21 minutes into session = "mid zone" start  # PORTED from TradierConfig 2026-08-17
    ZONE_OPEN_THRESHOLD: int = 25  # BACKTEST_CHANGE_T20 minutes after open = "open zone"  # PORTED from TradierConfig 2026-08-17

    # REQUIRED_INDICATORS: List[str] = field(default_factory=lambda: list(REQUIRED_INDICATORS))
    # FINAL_SCORING_INDICATORS: List[str] = field(default_factory=lambda: list(_DEFAULT_FINAL_SCORING_INDICATORS))
    # CORE_TECHNICAL_INDICATORS: List[str] = field(default_factory=lambda: list(_DEFAULT_CORE_TECHNICAL_INDICATORS))



DEFAULT_SETTINGS = {
    "bb_len": 20,
    "bb_std": 2.0,
    "wt_chan": 10,
    "wt_avg": 21,
    "dc_period": 20,
    "timeframe": "15m"
}

def get_config(symbol):
    """
    Returns the best config for a symbol. 
    Checks for a per-symbol JSON first, otherwise falls back to baseline.
    """
    # Path where your 'PROMOTE' script saves the best results
    override_path = f"./symbol_configs/{symbol}_best.json"
    
    if os.path.exists(override_path):
        with open(override_path, 'r') as f:
            overrides = json.load(f)
            # Merge baseline with overrides
            config = {**DEFAULT_SETTINGS, **overrides}
            return config
            
    return DEFAULT_SETTINGS


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

# ====================================================================
# 2026-04-26 RESEARCH SCAN — SWEEP-ONLY OVERLAYS + STRATEGY GATES
# All default OFF. Sweep validates before live. See RESEARCH_SCAN_20260426.md
# ====================================================================

# --- Vol-targeting global size scalar (Harvey 2018) ---
VOL_TARGET_ENABLED              = False
VOL_TARGET_PCT                  = 60.0       # target annualized vol % (crypto realizes 50-80%)
VOL_TARGET_LOW_CAP              = 0.25
VOL_TARGET_HIGH_CAP             = 2.0
VOL_TARGET_FIELD                = "yz_vol_60_d"   # NPZ field (Yang-Zhang 60d Daily)

# --- Drawdown-aware fractional Kelly (sizing reduction at account DD tiers) ---
# IMPORTANT: scales SIZING only, never closes positions (per feedback_no_pct_stops).
DD_KELLY_ENABLED                = False
DD_KELLY_TIER1_PCT              = 10.0       # at -10% DD, size × 0.5
DD_KELLY_TIER2_PCT              = 15.0       # at -15% DD, size × 0.25
DD_KELLY_TIER3_PCT              = 20.0       # at -20% DD, size × 0.125

# --- Minervini SEPA gate (long-side trend filter) ---
MINERVINI_GATE_ENABLED          = False
MINERVINI_MIN_SCORE             = 5          # int 0-6 (5 = all 5 SEPA conditions met)

# --- Clenow score gate (long-side trend strength filter) ---
CLENOW_GATE_ENABLED             = False
CLENOW_GATE_MIN_SCORE           = 30.0       # slope_ann × R² (renamed from CLENOW_MIN_SCORE — collided with existing tradier Clenow strategy param)

# --- Weekly WT exit gate (2026-05-15) ---
WT_W_EXIT_ENABLED               = False      # Exit when weekly WaveTrend crosses against position (wt1_W crosses wt2_W).

# --- 52w-high proximity gate (avoid topping out) ---
PROXIMITY_TOP_GATE_ENABLED      = False
PROXIMITY_TOP_MAX_DROP_PCT      = 5.0        # don't long when within X% of 52w high

# --- Squeeze-fire entry score boost (TTM Squeeze release) ---
SQUEEZE_FIRE_ENTRY_ENABLED      = True   # 2026-04-27 sweep T1: 9/91 winners True. Was False.
SQUEEZE_FIRE_TF                 = "5m"
SQUEEZE_FIRE_BONUS_SCORE        = 15.0

# --- TSMOM book-level scalar (12-1 month sign-agreement) ---
TSMOM_BOOK_SCALAR_ENABLED       = True   # 2026-04-27 sweep T1: 8/91 winners True. Was False.
TSMOM_LOOKBACK_BARS             = 252
TSMOM_MIN_AGREEMENT             = 0.5
TSMOM_LOW_CAP                   = 0.25
TSMOM_HIGH_CAP                  = 1.5

REQUIRED_INDICATORS: List[str] = [
    # === RANKING & SCORING (MANDATORY) ===
    "0ranking_points",
    "0ranking_points_global",
    "0market_sentiment_score",
    "0market_sentiment_score_ema",
    "0market_sentiment_local",
    "0sentiment_classification",
    "0sentiment_strength",
    "0is_top_sentiment",
    "0is_bottom_sentiment",
    "0sentiment_rank",
    "0final_score_norm",
    # === CONVICTION SCORES (CALCULATED IN PIPELINE) ===
    "zconviction_augment_long",
    "zconviction_augment_short",
    "zconviction_reasons_augment_long",
    "zconviction_reasons_augment_short",
    # === BASIC PRICE DATA (MANDATORY) ===
    "current_price",
    "prev_price",
    "timestamp",
    "timestamp_3m",
    "timestamp_15m",
    "timestamp_1h",
    "timestamp_4h",
    "timestamp_D",
    # === 1M TIMEFRAME ===
    "stoch_k_1m",
    "stoch_d_1m",
    "k_1m_prev",
    "d_1m_prev",
    "sma_200_1m",
    "sma_200_1m_prev",
    "sma_crossover_1m",
    "sma_crossunder_1m",
    # === 3M TIMEFRAME ===
    "lr_trend_3m",
    "dc_high_3m",
    "dc_low_3m",
    "dc_basis_3m",
    "dc_high_3m_prev",
    "dc_low_3m_prev",
    "dc_high_3m_ant",
    "dc_low_3m_ant",
    "dc_basis_3m_ant",
    "dc_high4_3m",
    "dc_low4_3m",
    "wt1_3m",
    "wt2_3m",
    "wt_signal_3m",
    "wt_score_3m",
    "stoch_k_3m",
    "stoch_d_3m",
    "k_3m_prev",
    "d_3m_prev",
    "stoch_crossover_3m",
    "stoch_crossunder_3m",
    "dc_basis_crossover_3m",
    "dc_basis_crossunder_3m",
    "dc_high_crossover_3m",
    "dc_low_crossunder_3m",
    "dc_high_crossunder_3m",
    "dc_low_crossover_3m",
    "ha_3m",
    "ha_3m_prev",
    "atr_3m",
    "atr_3m_prev",
    "relative_volume_3m",
    "high_3m",
    "low_3m",
    "high_3m_prev",
    "low_3m_prev",
    "mfi_3m",
    "rsi_3m",
    "ema_20_3m",
    "ema_20_std_3m",
    "t_up_3m",
    "tco_3m",
    "tcu_3m",
    # === 15M TIMEFRAME ===
    "lr_trend_15m",
    "dc_high_15m",
    "dc_low_15m",
    "dc_basis_15m",
    "dc_high_15m_prev",
    "dc_low_15m_prev",
    "dc_high_15m_ant",
    "dc_low_15m_ant",
    "dc_basis_15m_ant",
    "dc_high4_15m",
    "dc_low4_15m",
    "stoch_k_15m",
    "stoch_d_15m",
    "stoch_k_15m_prev",
    "d_15m_prev",
    "stoch_crossover_15m",
    "stoch_crossunder_15m",
    "sma_crossover_15m",
    "sma_crossunder_15m",
    "dc_basis_crossover_15m",
    "dc_basis_crossunder_15m",
    "dc_high_crossover_15m",
    "dc_low_crossunder_15m",
    "dc_high_crossunder_15m",
    "dc_low_crossover_15m",
    "wt1_15m",
    "wt2_15m",
    "wt_signal_15m",
    "wt_score_15m",
    "ha_15m",
    "atr_15m",
    "atr_15m_prev",
    "relative_volume_15m",
    "high_15m",
    "low_15m",
    "open_15m",
    "close_15m",
    "high_15m_prev",
    "low_15m_prev",
    "sma_200_15m",
    "sma_200_15m_prev",
    "mfi_15m",
    "rsi_15m",
    "ema_20_15m",
    "t_up_15m",
    # === 1H TIMEFRAME ===
    "lr_trend_1h",
    "dc_high_1h",
    "dc_low_1h",
    "dc_basis_1h",
    "dc_high_1h_ant",
    "dc_low_1h_ant",
    "dc_basis_1h_ant",
    "dc_high4_1h",
    "dc_low4_1h",
    "stoch_k_1h",
    "stoch_d_1h",
    "k_1h_prev",
    "d_1h_prev",
    "stoch_crossover_1h",
    "stoch_crossunder_1h",
    "sma_crossover_1h",
    "sma_crossunder_1h",
    "dc_basis_crossover_1h",
    "dc_basis_crossunder_1h",
    "dc_high_crossover_1h",
    "dc_low_crossunder_1h",
    "dc_high_crossunder_1h",
    "dc_low_crossover_1h",
    "wt1_1h",
    "wt2_1h",
    "wt_signal_1h",
    "wt_score_1h",
    "ha_1h",
    "atr_1h",
    "atr_1h_prev",
    "sma_200_1h",
    "sma_200_1h_prev",
    "high_1h",
    "low_1h",
    "high_1h_prev",
    "low_1h_prev",
    "mfi_1h",
    "rsi_1h",
    "ema_20_1h",
    # === 4H TIMEFRAME ===
    "lr_trend_4h",
    "slope_close_4h",
    "linearity_4h",
    "dc_high_4h",
    "dc_low_4h",
    "dc_basis_4h",
    "dc_high_4h_ant",
    "dc_low_4h_ant",
    "dc_basis_4h_ant",
    "dc_high4_4h",
    "dc_low4_4h",
    "stoch_k_4h",
    "stoch_d_4h",
    "k_4h_prev",
    "stoch_d_4h_prev",
    "stoch_crossover_4h",
    "stoch_crossunder_4h",
    "sma_crossover_4h",
    "sma_crossunder_4h",
    "dc_basis_crossover_4h",
    "dc_basis_crossunder_4h",
    "dc_high_crossover_4h",
    "dc_low_crossunder_4h",
    "dc_high_crossunder_4h",
    "dc_low_crossover_4h",
    "wt1_4h",
    "wt2_4h",
    "wt_signal_4h",
    "wt_score_4h",
    "ha_4h",
    "atr_4h",
    "atr_4h_prev",
    "sma_200_4h",
    "sma_200_4h_prev",
    "ema_20_std_4h",
    "high_4h",
    "low_4h",
    "high_4h_prev",
    "low_4h_prev",
    "mfi_4h",
    "rsi_4h",
    "ema_20_4h",
    # === DAILY (D) TIMEFRAME ===
    "dc_high_D",
    "dc_low_D",
    "dc_basis_D",
    "dc_high_D_prev",
    "dc_low_D_prev",
    "dc_basis_D_prev",
    "dc_high_D_ant",
    "dc_low_D_ant",
    "dc_basis_D_ant",
    "dc_basis_crossover_D",
    "dc_basis_crossunder_D",
    "dc_high_crossover_D",
    "dc_low_crossunder_D",
    "dc_high_crossunder_D",
    "dc_low_crossover_D",
    "stoch_k_D",
    "stoch_d_D",
    "k_D_prev",
    "stoch_d_D_prev",
    "stoch_crossover_D",
    "stoch_crossunder_D",
    "wt1_D",
    "wt2_D",
    "wt_signal_D",
    "wt_score_D",
    "ha_D",
    "atr_D",
    "atr_D_prev",
    "sma_200_D",
    "sma_200_D_prev",
    "mfi_D",
    "rsi_D",
    "high_D",
    "low_D",
    # === BAR PATTERN + VOLUME + STRUCTURE (HTF) ===
    "bar_pattern_1h",
    "bar_direction_1h",
    "bar_strength_1h",
    "bar_vol_confirm_1h",
    "bar_vol_ratio_1h",
    "bar_body_ratio_1h",
    "bar_upper_wick_1h",
    "bar_lower_wick_1h",
    "bar_streak_1h",
    "bar_swing_bull_1h",
    "bar_swing_bear_1h",
    "bar_compression_1h",
    "bar_compression_ratio_1h",
    "bar_inside_count_1h",
    "bar_vol_spike_1h",
    "bar_vol_expanding_1h",
    "bar_vol_regime_1h",
    "bar_atr_rank_1h",
    "bar_pattern_4h",
    "bar_direction_4h",
    "bar_strength_4h",
    "bar_vol_confirm_4h",
    "bar_vol_ratio_4h",
    "bar_body_ratio_4h",
    "bar_upper_wick_4h",
    "bar_lower_wick_4h",
    "bar_streak_4h",
    "bar_swing_bull_4h",
    "bar_swing_bear_4h",
    "bar_compression_4h",
    "bar_compression_ratio_4h",
    "bar_inside_count_4h",
    "bar_vol_spike_4h",
    "bar_vol_expanding_4h",
    "bar_vol_regime_4h",
    "bar_atr_rank_4h",
    "bar_pattern_D",
    "bar_direction_D",
    "bar_strength_D",
    "bar_vol_confirm_D",
    "bar_vol_ratio_D",
    "bar_body_ratio_D",
    "bar_upper_wick_D",
    "bar_lower_wick_D",
    "bar_streak_D",
    "bar_swing_bull_D",
    "bar_swing_bear_D",
    "bar_compression_D",
    "bar_compression_ratio_D",
    "bar_inside_count_D",
    "bar_vol_spike_D",
    "bar_vol_expanding_D",
    "bar_vol_regime_D",
    "bar_atr_rank_D",
]
_DEFAULT_FINAL_SCORING_INDICATORS: List[str] = [
    indicator for indicator in REQUIRED_INDICATORS if indicator.startswith("0")
]

_DEFAULT_CORE_TECHNICAL_INDICATORS: List[str] = [
    indicator for indicator in REQUIRED_INDICATORS if not indicator.startswith("0")
]
