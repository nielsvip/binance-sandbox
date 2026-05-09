# ═══════════════════════════════════════════════════════════════════════
# SWEEP REFERENCE: data/sweep_tiers.json — prioritized list of ALL sweepable
# switches with ranges, tiers (TIER_1→TIER_3), and notes. Agents: read that
# file before starting any parameter sweep. TIER_1 first, skip DEAD/FIXED.
# ═══════════════════════════════════════════════════════════════════════
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
import json

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

    MIN_POSITION_SIZE: float = 1.0
    # 1/50 RULE: No single position > 2% of total capital ($1k crypto = $20/pos max)
    MAX_POSITION_SIZE: float = 20.0  # 2026-03-30: 1/50 of $1k. Was $800 (80% of capital = suicide).
    MAX_POSITION_SIZE_BTC: float = 2000.0  # 2026-03-30: Same rule for BTC. Was $6000.
    MAX_POSITION_SIZE_MEN: float = 20.0  # 2026-03-30: Same. Was $1200.
    MAX_POSITION_SIZE_FIN: float = 20.0  # 2026-03-30: Same. Was $4000.
    HIGH_GAIN_AUGMENTATION_MIN_SIZE = 50  # BACKTEST_CHANGE_25: was 200. Lower threshold lets more winners get augmented

    MAX_ORDER_VALUE: float = 200.0  # 2026-03-30: 1/50 rule. Was $280.
    MAX_ORDER_VALUE_MEN: float = 20.0  # Was $240.
    MAX_ORDER_VALUE_FIN: float = 20.0  # Was $120.
    START_POSITION_SIZE: float = 9.0  # Start size per entry. Capped by MAX_POSITION_SIZE.

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
    COMMISSION_BUFFER_PCT: float = 0.10
    REENTRY_PRICE_IMPROVE_PCT: float = 0.10  # require 0.10% price improvement vs exit before reentry
    AUGMENT_ONLY_WHEN_PROFITABLE: bool = True  # URGENT_FIX: NEVER augment a position with gain < 0
    MAX_AUGMENTS_PER_POSITION: int = 3  # URGENT_FIX: cap total augments, stop piling into losers
    BEAR_MARKET_MODE: bool = True  # URGENT_FIX: When True, favor shorts over longs
    # MIN_PROFIT_FOR_PROFIT_TAKING: float =   0.4

    EXTREME_MODE: bool = False
    LIGHT_MODE: bool = False
    MARKET_MODE: str = "NORMAL_MODE"
    REV_MODE: bool = False

    SERVICE_STOP = True
    SERVICE_REDUCE = True
    MANAGE_REDUCE = True
    HEDGE_MODE: bool = True  # RE-ENABLED 2026-03-30: Can't hold losers without hedging. Cascade was OBLIGATORY_HEDGE loops (killed), NOT HEDGE_MODE. Hedges via ez_positions_quick ONLY.
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
    SCALP_V3_ENABLED: bool = True
    SCALP_V3_ACCOUNTS: list = field(default_factory=lambda: ["inf"])
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
    SCALP_V3_ENTRY_VOL_SPIKE_MULT: float = 1.5
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
    SCALP_V3_REENTRY_COOLDOWN_S: int = 0
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
    HEDGE_ACCOUNTS = ["ang", "inf", "men", "fin", "flz"]  # 2026-05-05: ADDED flz per user — flz was bleeding without hedge support; UNDERWATER_HEDGE_OR_CLOSE was logging but not firing. Cascade guards multi-layered (see history below).
    HEDGE_WEBHOOK_LOCK_TTL_SEC: float = 3600.0  # 2026-04-24: 1-hour Redis-backed lock per (account:symbol:side) for HEDGE-reason webhooks. Matches HEDGE_COMPLETED_LOCKOUT_SECONDS. Non-hedge webhooks keep 30s TTL.
    HEDGE_CLOSE_SCALP_MODE: bool = True  # 2026-04-24: user directive — close hedge on ANY 1m/3m LH/HH/LL/HL against hedge. Don't wait for wt_3m+wt_1h confirmation (too slow for scalp cycles). Original wt_3m+wt_1h gate still fires first if it matches.
    HEDGE_SCALP_MAX_AGE_MIN: float = 15.0  # 2026-04-25 Rule C: losing hedge stuck >15min → close (prevents dual-losing pair like WIFUSDC -0.62%/-0.25%).
    SCALP_V3_PEAK_GIVEBACK_PCT: float = 0.15  # 2026-04-25: if V3 position peaked ≥0.3% and gave back this pp, exit to lock profit. Separate from SCALP_V3_PG_ARM_PCT/_PG_GIVEBACK_PCT which gate only >0.5% peaks.
    STRICT_NO_LOSS_ACCOUNTS = ['ang','flz', 'men', 'fin', 'inf']  # 2026-04-24: added 'inf'. MOVR -13% was hit with DC_BREACH_REDUCE_UNHEDGED instead of DC_BREACH_HEDGE_TRIGGER because inf was missing from this list (the hedge branch at ez_manage.py:14491 requires STRICT_NO_LOSS membership). RE-ENABLED 2026-04-07: Removing this halved account value in 10 minutes. NO closing at a loss. EVER. Hedge + ratio IS the protection.
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
    TF_ALL: list = None    # Auto-populated: [TF_MICRO, TF_SCALP, TF_HTF1, TF_HTF2, TF_HTF3, TF_MACRO]
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
    WT_15M_SAME_HEDGE_ENABLED: bool = True  # RE-ENABLED 2026-04-16: root cause was hedge exemption in DUPLICATE_OPEN_GUARD (line 11000) + size gate (line 11165). Both exemptions REMOVED. Hedges now subject to 900s cooldown like all other opens.
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
    REENTRY_TIER2_PRICE_PCT: float = 0.003  # 0.3% price move past exit triggers Tier 2
    REENTRY_TIER2_MIN_MINUTES: float = 10.0  # Minimum minutes before Tier 2 activates
    REENTRY_TIER2_MAX_MINUTES: float = 120.0  # After this, Tier 2 forces entry at 50% size
    REENTRY_ESCALATION_WARN_MIN: float = 30.0  # WARNING log if reentry pending > 30min
    REENTRY_ESCALATION_CRIT_MIN: float = 60.0  # CRITICAL log if reentry pending > 60min
    REENTRY_RALLY_K15M_MAX: float = 30.0    # 2026-04-20 sweep: vel=9+rally=30 → Sharpe 2.598. 2026-04-25 rapid-grid 50-sym: K40/50/60/60+gap3 all identical to K30 — reentry count not K-gated, binding constraint is entry score + cooldown.
    REENTRY_RALLY_HTF_MIN: int = 1          # sweep: 1 / 2 / 3 — min of (1h/4h/D) WT aligned at reentry
    REENTRY_MIN_GAP_MINUTES: float = 15.0   # 2026-04-17 Chapter-C winning bundle used BARS=5 (~15min on 3m). Was 3.0. Switchable.
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
    HEDGE_NEWBORN_GRACE_MINUTES: float = 10.0  # 2026-04-16: hedges blocked for N min after open, unless DC breach
    HEDGE_NEWBORN_DC_BREACH_ALLOWED: bool = True  # allow hedge during grace if price breaches dc_low_3m (LONG) / dc_high_3m (SHORT)
    OBLIGATORY_HEDGE_WT_TFS: int = 2  # Need 2 TFs with WT against before opening hedge.
    HEDGE_CLOSE_WT_TFS_FAVOR: int = 3  # BC_988: r2 winner but this is now unused — 15m WT close in code.
    HEDGE_SAME_SYMBOL_ENABLED: bool = True  # Re-enabled 2026-04-01: 150% same-symbol always active regardless of HEDGE_MODE. Cross-symbol only when HEDGE_MODE=True.
    HEDGE_DUAL_IF_HEDGE_MODE: bool = False  # Cross-symbol dual hedge disabled.
    HEDGE_ALL_POSITIONS: bool = False  # BC_988: NEW. If True, hedge ALL positions when wt15m against (not just losers). Test pending.
    # === 2026-04-17 HEDGE OVERHAUL — user directive: hedges close on wt_3m flip no matter the P/L ===
    HEDGE_EXIT_BYPASS_NOLOSS: bool = True  # Hedge closes on wt1_3m flip regardless of gain. Bypasses STRICT_NO_LOSS lock.
    HEDGE_EXIT_WT_TF: str = "3m"  # Which TF's WT flip triggers hedge close ("3m" per user rule).
    HEDGE_CLOSE_REMOVE_FROM_TRADEABLE: bool = True  # On hedge close, drop position_key from tradeable_keys.
    HEDGE_SAME_SYMBOL_PCT: float = 1.0  # Same-symbol hedge size as fraction of loser qty (1.0 = 100%).
    HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE: bool = True  # Same-symbol hedge bypasses tradeable_keys gate (special hedge status).
    # === 2026-04-26 HEDGE SYMBOL-SELECTION GUARDS (sweep-testable) — user wants gain-deterioration as primary trigger, DC zones secondary ===
    HEDGE_DC_RESISTANCE_GATE_ENABLED: bool = False  # 2026-04-26: OFF — was blocking hedges precisely when needed (V3 SHORT bleeding into a pump, all LONG hedge candidates rejected because they were pumping too). User rule: hedges activate on deteriorating gains, INDEPENDENT of dc position. Sweep-only knob now.
    HEDGE_DC_LONG_REJECT_DCP: float = 0.85          # LONG-side dc_position_1h/4h threshold (>=) for rejection.
    HEDGE_DC_SHORT_REJECT_DCP: float = 0.15         # SHORT-side dc_position_1h/4h threshold (<=) for rejection.
    HEDGE_WT_VEL_GATE_ENABLED: bool = False         # 2026-04-26: OFF — same reason as DC gate above. Hedge-the-bleeder must not be filtered by candidate-symbol velocity. Sweep-only knob.
    # 2026-04-26 — Force-reentry HTF veto (refuse PRICE_CROSSED_MANDATORY when 1h+15m+4h all confirm trend AGAINST). Triggered after C98USDT triple-open against bullish HTF.
    PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED: bool = True
    # 2026-04-26 USER RULE — HARD hedge sizing caps. Trades move <1% per cycle on a ~$1k crypto
    # account, so hedges must NEVER exceed 1.5× loser notional or absolute $25. Caps applied in
    # both compute_hedge_size and execute_same_symbol_hedge inner. Triggered after ALTUSDT_LONG
    # accumulated to $1013 / 12478% in tracker from pre-fix double-fires.
    HEDGE_MAX_PCT_OF_LOSER: float = 1.0  # 2026-04-26 user rule: hedges NEVER exceed loser size
    HEDGE_MAX_ABSOLUTE_USD: float = 25.0
    # 2026-04-26 — refuse new hedge orders if existing hedge-side position already covers
    # >= this fraction of target. Stops accumulation across many cycles.
    HEDGE_ALREADY_COVERED_THRESHOLD: float = 0.9
    # 2026-04-26 USER RULE — MICRO_SCALP_USDC_MAKER. USDC pairs only, maker-only (zero fees on
    # Binance Futures USDC pairs), no webhook fallback. Closes at gain >= threshold AND first
    # deceleration; reopens when price re-crosses exit_price. Fires from process_position before
    # other close paths. Bypasses STRICT_NO_LOSS / UNG / hedge gates — close only on POSITIVE gain.
    MICRO_SCALP_USDC_MAKER_ENABLED: bool = True
    MICRO_SCALP_USDC_ACCOUNTS: list = field(default_factory=lambda: ["ang", "inf", "flz", "men", "fin"])
    MICRO_SCALP_GAIN_THRESHOLD_PCT: float = 0.02
    # === 2026-04-26 HEDGE OPEN TRIGGER (sweep-testable) — gain-deterioration before WT flip is "wrong moment" prevention ===
    HEDGE_DETERIORATING_GAIN_ENABLED: bool = True   # scan_and_hedge_losers requires losing position's gain to be actively deteriorating.
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
    RED_ZONE_GATE_ENABLED: bool = True             # 2026-04-28 restored — probe one-by-one to find which gate actually regressed
    RED_ZONE_MIN_DISTANCE_PCT: float = 0.4         # block entry when wall is closer than 0.4% from current price
    RED_ZONE_MIN_WALL_NOTIONAL_USD: float = 50_000 # ignore walls smaller than $50k notional (illiquid noise)
    RED_ZONE_HEDGE_GATE_ENABLED: bool = True       # apply red-zone gate to hedge entries too (stops hedging into hard wall)
    RED_ZONE_AUGMENT_GATE_ENABLED: bool = True     # apply to AUGMENT actions (don't add into resistance)
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
    VP_GATE_ENABLED: bool = True                   # 2026-04-28 restored — probe one-by-one to find which gate actually regressed
    VP_GATE_MIN_DISTANCE_PCT: float = 1.0          # block entries within 1% of an HVN shelf
    VP_GATE_MIN_DENSITY_Z: float = 2.0             # require HVN density-z ≥ 2.0 (~5× mean) to block
    VP_GATE_HEDGE_GATE_ENABLED: bool = False       # apply to hedge entries (default off)
    VP_GATE_AUGMENT_GATE_ENABLED: bool = True      # apply to AUGMENT actions
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
    LH_HL_FILTER_AUGMENT_GATE_ENABLED: bool = True     # apply to AUGMENT actions (don't add into reversing trend)
    LH_HL_FILTER_HEDGE_GATE_ENABLED: bool = False      # apply to hedge entries (default OFF — hedges are intentional counter-trend)
    # Sweep dimension (user 2026-04-27): test LH alone vs LH+LL for LONGS, HL alone vs HL+HH for SHORTS.
    #   False (default): block LONG on LH only / block SHORT on HL only — catches trend hesitation.
    #   True: also require LL (LONG) / HH (SHORT) — full descending/ascending channel confirmation.
    LH_HL_FILTER_REQUIRE_BOTH: bool = False
    # === 2026-04-26 USER ABSOLUTE: hedges NEVER close at a loss (overrides feedback_hedge_wt3m_close_absolute.md until tests prove otherwise) ===
    # Applied to: HEDGE_CLOSE_WT3M1H_PRE_GATE (ez_manage), HEDGE_CLOSE_WT3M1H_PP_ABS (ez_manage), HEDGE_CLOSE_WT3M1H_ABS (ez_positions_quick), HEDGE_KILL_REVERSING_WT (ez_positions_quick).
    # If gain<0 the WT-flip signal is recorded but the close is held; we wait for gain>=0 OR the position to organically improve. STRICT_NO_LOSS-aligned.
    HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN: bool = True
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
    EZ_REENTRY_INLINE_ENABLED: bool = False
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
    EZ_REENTRY_PRICE_CROSS_PCT: float = 0.0
    EZ_REENTRY_PRICE_CROSS_MIN_GAP_S: float = 60.0
    EZ_REENTRY_PRICE_CROSS_PARTIAL_FRAC: float = 0.5
    EZ_REENTRY_PRICE_CROSS_MAX_AGE_HOURS: float = 48.0
    EZ_REENTRY_PRICE_CROSS_MAX_FIRES_PER_TICK: int = 20
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
    HEDGE_CLOSE_MODE: str = 'wt_3m_1h'
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
    HEDGE_BANDAID_OFF_ENABLED: bool = True
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
    RIDICULOUS_HOLD_GUARD_ENABLED: bool = True
    RIDICULOUS_LOSS_PCT: float = -15.0    # absolute loss cap — never exceed this
    RIDICULOUS_HOLD_HOURS: float = 48.0   # 2 days max underwater duration
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
    WT15M_AGAINST_FORCE_HEDGE_ENABLED: bool = True
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
    # R1 — DC4_3M EMERGENCY CLOSE within newborn window (USER 2026-05-09)
    # Fires while position is fresh and price breaks 4-bar 3m channel low/high.
    # Bypasses NO_LOSS, hedge, MTF. Desktop alert + JSONL log naming entry signal.
    R1_DC_LOW4_3M_EMERGENCY_ENABLED: bool = True
    R1_NEWBORN_WINDOW_MIN: float = 15.0            # active only first N min after open
    R1_USE_DC_4BAR: bool = True                    # True=dc_low4_3m (4-bar). False=dc_low_3m (1-bar).
    R1_TF: str = '3m'                              # sweep-testable
    # _DUPLICATE_OPEN_GUARD gain-based replacement (USER 2026-05-09):
    # Replaces 900s time-cooldown with a gain gate. Augments require gain > 0.5*MIN_GAIN.
    DUP_GUARD_GAIN_MULTIPLIER: float = 0.5         # threshold = MULT * config.MIN_GAIN (=1.5% by default)
    DUP_GUARD_USE_GAIN_GATE: bool = True           # False = revert to 900s time gate
    # 2026-05-08 USER MANDATE — ratio_rebalance: close OVERWEIGHT side instead of opening
    # underweight. Picks positions with smallest |wt1_15m - wt2_15m| (least conviction).
    # Set False to re-enable the old open-underweight path once system is verified.
    RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY: bool = True
    RATIO_REBALANCE_MAX_CLOSES: int = 3
    # 2026-05-09 USER MANDATE — OVERTRADE_GUARD in execute_now caps OPEN/AUGMENT
    # to this many per (pkey × UTC day). CLOSE/REDUCE not capped. Emergency-exit
    # reasons (RIDICULOUS, BREAK_REVERSE, ALL_TF_AGAINST, INTERVENTION, MANUAL)
    # bypass the cap. Set 0 to disable.
    TRADES_PER_SYM_PER_DAY_MAX: int = 8
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
    ALL_TF_AGAINST_CLOSE_MIN_TFS: int = 5
    # check_entry_vetting NO_STRUCT_OR_BREAKOUT at ez_manage.py:507 had no toggle.
    # When False, the "structure_ok or dc_breakout" requirement is bypassed (entry trigger alone gates).
    ENTRY_VET_NO_STRUCT_OR_BREAKOUT_REQUIRED: bool = True
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
    HEDGE_TRIGGER_USE_WT_3M_ALONE: bool = False  # 2026-05-06: reverted to 15m-OR-(3m+1h). Backtest winner -0.5%/15m. 3m-alone was causing premature hedges.
    # Companion: peak-decay nuke. When hedge gain peaks >1% then drops back to 0.5% → close before
    # going negative. Default True per the historical "exist as SHORT as possible, NEVER close at a loss"
    # paragraph at ez_positions_quick.py:5385 — this is the "before negative" half of that rule.
    HEDGE_DECAY_NUKE_ENABLED: bool = True
    # === 2026-04-29 SURFACED hidden wt_dc_exit_scorer knobs (were implicit defaults via getattr-fallback in wt_dc_exit_scorer.py) ===
    # ez_manage.py and tradier_manage.py both call wt_dc_score_exit. Crypto live had ZERO of these declared,
    # so wt_dc_exit_scorer.py was using its hardcoded fallbacks (5/75/0.80). Now explicit + sweep-testable.
    # Test C v8_quick verdict (12sym×2yr crypto NOLOSS=off): 5/5 K=85 wins pool_sharpe 0.1695 vs simple_mtf 0.105 vs delta3 0.135 vs loose 3/5 K75 0.142.
    WT_DC_EXIT_THRESHOLD: float = 25.0          # ez_manage.py read with default 25 — surfaced for sweep
    EXIT_SCORER_MIN_CONDITIONS: int = 5         # was implicit default 5; Test C confirms strict 5/5 wins on crypto too. Sweep 3,4,5.
    EXIT_SCORER_K_EXTREME: float = 75.0         # was implicit default 75. Sweep 70,75,80,85.
    EXIT_SCORER_DC_EXTREME: float = 0.80        # was implicit default 0.80. Sweep 0.70-0.90.
    EXIT_SCORER_PARTIAL_SCORE: float = 40.0     # was implicit default 40 (N-1 conditions). Sweep 30-50.
    EXIT_SCORER_FULL_SCORE: float = 100.0       # was implicit default 100. Stays.
    # === 2026-04-26 USER ABSOLUTE: cross-symbol hedge picker must verify WT across ALL TFs, not just velocity ===
    # _quick_hedge_rank rejects hedge candidates where < HEDGE_STRICT_WT_MIN_TFS_AGAINST of the 5 TFs (3m/15m/1h/4h/D) align against the proposed hedge direction.
    # Stops "shorting a rocket" — symbol may have negative wt_velocity_1h but still be raging on D/4h.
    HEDGE_STRICT_WT_ALL_TFS_ENABLED: bool = True
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
    RATIO_MULTIPLIER: float = 4.0  # BC_160: 4x = +2052% vs 3x = +1593% on WT exit/reentry backtest (12 sym, 2025). 60/40 → 90/10. DD 0.7%.
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
    UNIVERSAL_NOLOSS_GATE: bool = True
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
        # 2026-05-09 USER MANDATE: only R1, R2, hedge-failed can close at loss.
        'R1_DC_LOW4_3M_EMERGENCY',        # newborn-window dc4_3m breach → close
        'R2_WT_VEL_SLOW',                 # wt vel slowdown near 0 gain → close at small positive
        'WT_15M_VEL_SLOW',                # legacy alias for R2 (existing block at ez_manage:20696)
        'HEDGE_FAILED',                   # hedge couldn't be taken → fallback close at loss
    ])
    # === DC RECOVERY-TO-ENTRY EXIT BYPASS (2026-04-15, crypto) ===
    # When True: if entry_price is on wrong side of dc_high_4h (LONG above) / dc_low_4h (SHORT below),
    # AND current 3m close has recovered to within tolerance of entry_price,
    # AND current 3m bar shows reversal, ALLOW close at loss (bypass UNIVERSAL_NOLOSS_GATE).
    # Replaces UNIVERSAL_NOLOSS for "bad-entry escape near breakeven". Defaults OFF — sweep first.
    DC_RECOVERY_EXIT_ENABLED: bool = False
    DC_RECOVERY_EXIT_TOLERANCE_PCT: float = 0.25  # crypto pct tolerance around entry_price
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
    # === AUGMENT BLOCKS (2026-04-16) — 4 blocks switch-gated for sweep ===
    # 2026-04-25 rapid-grid finding: AUGMENT_WT_4H_BOUNCE (v8_quick_engine) → +0.029 pool_sharpe on crypto 50-sym. No live equivalent yet — wire as AUGMENT_WT_4H_BOUNCE_ENABLED when sweep validates on full 50-sym.
    AUGMENT_BLOWPAST_ENABLED: bool = True  # gain >= 3×MIN_GAIN, conviction 90. Highest conviction.
    AUGMENT_WT_CROSS_ENABLED: bool = True  # WT cross + aligned 2/3 TFs + gain >= MIN_GAIN, conviction 80.
    AUGMENT_WT_3TF_ENABLED: bool = True  # 3/3 LTF aligned + smaller gain, conviction 70.
    AUGMENT_HTF_TREND_ENABLED: bool = True  # HTF trend only, conviction 65. Most frequent.
    # === EVALUATE_REENTRY_2 (2026-04-16) — periodic reentry pass switches ===
    REENTRY_2_ENABLED: bool = True  # Master switch. ~$420 PnL per ablation.
    REENTRY2_DIR_FAV_ENABLED: bool = True  # BC_152 direction-favorable immediate reentry
    REENTRY2_DC_BREAK_ENABLED: bool = True  # DC breakout fast-path reentry
    REENTRY2_QUICK_RECOVERY_ENABLED: bool = True  # quick recovery after exit + momentum
    QUICK_RECOVERY_WINDOW_MIN: float = 120.0  # 2026-04-26 NEW — was hardcoded 60.0 (ez_manage.py:18259, ez_positions_quick.py:14647). Widened to give K3m alignment more time. 9,386 NOT_ALLOWED rejects in 2d at 60.
    # === V8_QUICK v2 WINNER (2026-04-16 micro-experiments) ===
    # Progression:
    #   Baseline (no filter):          Sharpe 0.17 on 11-sym
    #   v1 (strength filter only):     Sharpe 0.94 on 11-sym, 87% WR, 1.42% avg (5.5× baseline)
    #   v2 (+ PT=1.5) 11-sym:          Sharpe 1.39, 89% WR, 1.37% avg (8× baseline)
    #   v2 TOP-5 symbols:              Sharpe 1.74, 96% WR, 1.59% avg, 89 trades
    #   v2 TOP-3 symbols:              Sharpe 1.90, 98% WR, 1.73% avg, 58 trades ✅ EXCEEDS 1.8
    V8Q_STRENGTH_FILTER_ENABLED: bool = True
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
    DELTA_PYRAMID_ENABLED: bool = True  # Disabled until sweep validates
    DELTA_SPEED_SMOOTH: int = 5  # WINNER: sm=5
    DELTA_ACCEL_LOOKBACK: int = 5
    DELTA_TF_WEIGHTS: dict = None  # Set in __post_init__ — 3m dominant
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
    DELTA_HTF_GATE: str = "4h_D"  # WINNER: both 4h AND D must confirm
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
    REENTRY_COOLDOWN_S: float = 0.0          # Was 15s; zero for instant reentry
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
    OBLIGATORY_HEDGE_ENABLED: bool = True                # FOREVER RULE — never False in live
    OBLIGATORY_HEDGE_MIN_LOSS_PCT: float = -0.5           # 2026-05-06: -0.25→-0.5 per HEDGE_BANDAID_BACKTEST winner. trigger when gain below this
    OBLIGATORY_HEDGE_PCT: float = 1.0                    # hedge size (1.0 = 100%)
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
    OBLIGATORY_HEDGE_OR_CLOSE_LOOP_ENABLED: bool = True              # master switch for the periodic loop
    OBLIGATORY_HEDGE_OR_CLOSE_LOOP_INTERVAL_SECONDS: float = 60.0    # how often to scan losing positions
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H: bool = True                  # USER MANDATE: trigger requires BOTH wt_3m AND wt_1h against
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
    REENTRY_DISPATCH_MAX_ATTEMPTS: int = 3                            # number of retry attempts on transient queue failure
    REENTRY_DISPATCH_BACKOFF_S: float = 0.4                           # backoff between attempts (async sleep)
    # ═══ PEAK_GIVEBACK_PROTECTION (2026-04-19) ═══
    # ⚠️ DO NOT DISABLE WITHOUT EXPLICIT USER PERMISSION — REAL MONEY PROTECTION
    # MOVEUSDT bled from +1.26% peak to -13% because HTF_EXIT_VETO blocked breakeven exit.
    # Fires when position was profitable and gains have been given back. Bypasses HTF_EXIT_VETO.
    PEAK_GIVEBACK_PROTECTION_ENABLED: bool = True    # ⚠️ DO NOT DISABLE WITHOUT EXPLICIT USER PERMISSION
    PEAK_GIVEBACK_MIN_PEAK_PCT: float = 0.5          # must have reached >= 0.5% gain to activate
    PEAK_GIVEBACK_DROP_PCT: float = 0.5              # 2026-04-27: 1.0→0.5. Live audit: V3 positions peaked +3-7% (UMA 6.58, INX 4.94, NEIRO 4.72) and gave it ALL back to 0% before PEAK_GIVEBACK fired (fired at peak_drop≥1pp, so 4% peak only triggered exit after dropping to 3%). Tighter pp threshold locks more of the peak.
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
    RZ_EXIT_ENABLED: bool = False  # 2026-04-19: premature exits dropped Sharpe 2.5→1.25 on 48-sym sweep. Was True.
    RZ_TOP_BB_THRESHOLD: float = 0.85  # bb_pct_b above this = TOP zone (sweep: 0.85/0.92/0.97)
    RZ_BOT_BB_THRESHOLD: float = 0.15  # bb_pct_b below this = BOTTOM zone (sweep: 0.15/0.08/0.03)
    RZ_LEGS_MIN: float = 20.0  # Minimum legs remaining for entry (sweep: 10/20/35)
    RZ_REQUIRE_STRUCT: bool = False  # Require HH/HL or LH/LL structure for entry (sweep: True/False)
    RZ_K_EXIT: float = 95.0  # 2026-04-11 SWEEP: 95 > 90 > 80. Now k_15m (not k_1h) + SMART_RZ slowdown gate. 80=destructive, 95=best.
    RZ_MFI_EXIT: float = 85.0  # MFI above this at TOP = exit long (sweep: 75/85)
    RZ_K_ENTRY_MAX: float = 50.0  # Entry LONG only when k_1h < this (sweep: 40/50/60). Proven: 50
    RZ_K_ENTRY_BOTTOM: float = 10.0  # Stoch K below this at BOTTOM = exit short (mirror)
    RZ_MFI_ENTRY_BOTTOM: float = 15.0  # MFI below this at BOTTOM = exit short (mirror)
    BOUNCE_AUGMENT_ENABLED: bool = True  # D-low bounce augment for losing positions
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
    HTF_DIRECTION_GATE_ENABLED: bool = True
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
    RATIO_REBALANCE_COOLDOWN_CRASH: float = 300.0
    RATIO_REBALANCE_COOLDOWN_EXTREME: float = 600.0
    RATIO_REBALANCE_COOLDOWN_PNL_DIVERGENT: float = 120.0
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
    WIN_TRAIL_EROSION_PCT: float = 0.50  # BACKTEST_CHANGE_105: Tournament v2: 50% trail slightly better than 30% (let winners run more). Was 0.30.
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
    PARTIAL_PROFIT_LOCK_ENABLED: bool = True
    PARTIAL_PROFIT_LOCK_ACCOUNTS: List[str] = field(default_factory=lambda: ["ang", "inf", "flz", "men", "fin"])
    PARTIAL_PROFIT_LOCK_GAIN_PCT: float = 0.5          # TP trigger. 2026-04-25 rapid-grid: vectorized optimum 1.125%; 0.8%→2.02, 0.5%→1.52, 0.3%→1.25 pool_sharpe (vs 2.66 at 1.125%). Live stays 0.5% (Finandy latency limits); do not lower further.
    PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT: float = 0.75     # At this gain, upgrade stop from BE+buffer to first_exit_price (0.5% level)
    PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT: float = 0.10    # 2026-04-28 user: bumped from 0.02 → 0.10 to cover commissions (round-trip ~0.04% maker + ~0.06% slippage). Stop now fires only when remainder is net-positive after fees.
    # 2026-04-28 USER RULE: maker CLOSE orders rest at a commission-positive price.
    # When market is below the floor (LONG close) / above the floor (SHORT close), the post-only
    # GTX limit rests at the floor and waits — does not chase into a net-loss fill.
    MAKER_CLOSE_COMMISSION_FLOOR_ENABLED: bool = True
    MAKER_CLOSE_COMMISSION_FLOOR_TTL_SEC: float = 300.0  # how long to wait at floor before timing out
    # 2026-04-28 USER RULE: GUARANTEED_REENTRY needs more WT and/or K confirmation, plus a tight stop.
    GUARANTEED_REENTRY_STRICT_CONFIRMATION: bool = True
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
    TR_ADX4H_GATE_ENABLED: bool = True  # BC_155a: Boycott when ADX_4h trending (bad for mean-reversion system)
    TR_ADX4H_MAX: float = 20.0  # BC_155a: Conservative (16 optimal). ADX_4h above this = heavy penalty
    TR_ADX4H_BOYCOTT_SCORE: int = -40  # BC_155a: Severe. Stacked with BB_width: 81% OOS WR
    # --- BC_155b: BB_WIDTH_4h GATE — #2 filter. BB_w<=7.94: 68.4% IS / 68.7% OOS (+11.0pp) ---
    TR_BBWIDTH4H_GATE_ENABLED: bool = True  # BC_155b: Boycott wide BBands (high vol = bad entries)
    TR_BBWIDTH4H_MAX: float = 10.0  # BC_155b: Conservative (7.94 optimal)
    TR_BBWIDTH4H_BOYCOTT_SCORE: int = -35  # BC_155b: Severe penalty when too volatile
    # --- BC_155c: CHOPPINESS_4h GATE — #3 filter. Chop>=54: 64% IS / 66.7% OOS (+8.9pp) ---
    TR_CHOP4H_GATE_ENABLED: bool = True  # BC_155c: Bonus choppy, penalty trending. Our system IS mean-reversion.
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
    ADX_REGIME_FILTER_ENABLED: bool = True  # BACKTEST_CHANGE_137: ADX<20 = sizing penalty + entry deduction.
    ADX_TRENDING_THRESHOLD: float = 25.0  # BACKTEST_CHANGE_137: ADX above this = trending
    ADX_RANGING_THRESHOLD: float = 20.0  # BACKTEST_CHANGE_137: ADX below this = ranging (only mean-reversion)
    ADX_TF: str = "1h"  # BACKTEST_CHANGE_137: Timeframe for ADX regime check
    # === BC_170-174: COPY TRADER NPZ GATES (50k+ trades, 110+ traders, 426 NPZ indicators) ===
    # ADDITIVE gates — only block bad entries, never create new ones. Default OFF until V8 validated.
    CT_WT_VELOCITY_GATE_ENABLED: bool = True  # BC_170: ENABLED 2026-04-08. 5yr validated: Sharpe 1.94→5.26, 100% monthly positive, keeps 67% of trades. Don't trade against 1h WT velocity.
    GOLDEN_RULE_HTF_MIN_TFS: int = 0  # GOLDEN_RULE gate: require this many TFs to confirm (0=off). TFs=[3m,15m,1h,4h,D]. Sweep 1-5 to find best.
    GOLDEN_RULE_MIN_IND: int = 2  # Per-TF: need this many of [WT,RSI,MFI,DC,BB] to agree. Sweep 1-5.
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
    CT_DC_CROSSOVER_SKIP_ENABLED: bool = True  # BC_172: ENABLED 2026-04-08. 5yr validated: SHORT Sharpe +34%, removes only 1.3% of trades. Skip SHORT when DC basis crosses over on 15m/1h.
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
    SBA_MAX_TOTAL_MULT: float = 2.5  # BACKTEST_CHANGE_145: Position can't exceed 2.5x START_POSITION_SIZE
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
            "TRXUSDT",  # BACKTEST_CHANGE_48: worst real symbol in backtest (-63 Sharpe)
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
    BREAKEVEN_GAIN_EROSION_MIN_GAIN: float = 0.0     # gate fires only when MIN_GAIN <= current_gain < 0.02
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
    # EXIT: PERCENTILE OB/OS — close LONG when D+4h both overbought, SHORT when oversold
    WT_PERCENTILE_EXIT_ENABLED: bool = False        # OFF: in strong rally D WT stays elevated, exits too early
    WT_PERCENTILE_EXIT_OB_D: float = 90.0          # D percentile > this → exit LONG
    WT_PERCENTILE_EXIT_OB_4H: float = 75.0         # 4h percentile > this → confirm exit LONG
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
    EXECUTE_NOW_MAX_MARK_AGE_S: float = 3.0
    # User 2026-05-05 (1000LUNCUSDT screenshot): a LONG/SHORT on a symbol whose
    # OPPOSITE side is deeply losing acts as a de-facto hedge. Ban PPL,
    # WT_CROSS_EXIT, BANDAID_OFF, PEAK_GIVEBACK, and similar small-gain closes
    # while opposite is bleeding and current side has not yet earned enough to
    # offset. Bypass at EMERGENCY/HARD_STOP/MAX_AGE/ORPHAN/LIQ/STRUCTURAL/AGENT/USER.
    OPPOSITE_LOSER_HEDGE_PROTECT_ENABLED: bool = True
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
    SYMBOLS: Path = BASE_PATH / "symbols.json"
    SYMBOLS_ACTIVE_FILE: Path = BASE_PATH / "symbols_active.json"
    SYMBOLS_ANG_LONG: Path = BASE_PATH / "symbols_ang_long.json"
    SYMBOLS_INF_LONG: Path = BASE_PATH / "symbols_inf_long.json"
    SYMBOLS_INF_SHORT: Path = BASE_PATH / "symbols_inf_short.json"
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
    BTC_DEDICATED_ENABLED: bool = True                                   # MASTER kill switch — flip True only after sweep proof + paper days + user approval
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
