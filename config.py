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

    MAX_ORDER_VALUE: float = 20.0  # 2026-03-30: 1/50 rule. Was $280.
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
    ZERO_CONFIRMATION_THRESHOLD_API: int = 2  # FIX 2026-03-29: was 1, killed real hedges on fin. Need 2 misses to confirm phantom.
    MIN_PERC_FROM_SMA_1: float = 1.0 / 100  # SMA_1
    MIN_PERC_FROM_SMA_15: float = 3.0 / 100  # SMA_15
    MIN_GAIN: float = 3.0  # was 5.0 (too late, near TP). 3.0% = 2.8% buffer after 50% aug, survives 1.5% reversal. Tiered: 0.4x=1.2% pullback, 0.5x=1.5% reduced, 1x=3.0% full
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

    SCALP_ACCOUNTS = ["ang", "men"]  # Live restriction: only these accounts can ever scalp. New SCALP_MODE (V2) reads this list.
    # ───────────────────────────────────────────────────────────────────────────
    # SCALP_MODE — HTF Breakout Scalper (replaces legacy scalping behavior 2026-04-09)
    # New mechanism: only fires when price is above dc_high_15m AND dc_high_1h
    # (LONG) or below dc_low_15m AND dc_low_1h (SHORT). 8 exit variants tested
    # in backtest, winner promoted to live. ALL DEFAULTS OFF — flipping SCALP_MODE
    # is the only knob; everything else is variant-tunable.
    #
    # NOTE: This is the SAME flag name as the legacy SCALP_MODE for seamless
    # transition. Legacy SCALP_MODE-gated code paths (SCALP_MOMENTUM_BOYCOTT,
    # quick_scalp_monitor_loop, AdvancedSignalRater scalping_mode) have been
    # neutralized — flipping SCALP_MODE=True now activates V2 only.
    # ───────────────────────────────────────────────────────────────────────────
    SCALP_MODE: bool = False
    SCALP_V2_VARIANT: str = "V1_WT_CONFIRM"  # one of htf_breakout_scalper.VARIANTS
    SCALP_V2_DC_HTF_LIST: list = field(default_factory=lambda: ["15m", "1h"])  # TFs to check for breakout
    SCALP_V2_DC_HTF_REQUIRE_ALL: bool = True  # all TFs in list must agree, vs any
    SCALP_V2_MAX_CONCURRENT: int = 5
    SCALP_V2_MAX_HOLD_MINUTES: float = 30.0
    SCALP_V2_REENTRY_COOLDOWN_S: int = 300
    # SCALP_V2_ISOLATE: BACKTEST-ONLY. When True, the V2 entry/exit hooks are
    # the ONLY entry/exit logic that runs for SCALP_ACCOUNTS — other strategies
    # (technical, leaderboard, ranking, reentry, augment, delta) are bypassed
    # so the breakout strategy is tested in isolation. Live trading should
    # ALWAYS leave this False (the existing strategies coexist with V2 in live).
    SCALP_V2_ISOLATE: bool = False
    HEDGE_ACCOUNTS = ["ang", "inf", "fin", "men", "flz"]  # Hedge accounts — ALL accounts get hedge protection
    STRICT_NO_LOSS_ACCOUNTS = ['ang', 'inf', 'flz', 'men', 'fin']  # RE-ENABLED 2026-04-07: Removing this halved account value in 10 minutes. NO closing at a loss. EVER. Hedge + ratio IS the protection.
    SCALP_OVERRIDE = False
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
    REENTRY_RALLY_K15M_MAX: float = 100.0  # sweep: 100 (off) / 40 / 20 — k15m level cap on WT_2of3 reentry gate
    REENTRY_RALLY_HTF_MIN: int = 1          # sweep: 1 / 2 / 3 — min of (1h/4h/D) WT aligned at reentry
    LOSS_EXIT_REQUIRES_HEDGE: bool = True  # Master: can only exit at loss if hedge >= losing value
    HEDGE_OVERSIZE_RATIO: float = 2.0  # Max 200% of losing position. Tiered: 50% at -0.6%, 100% at -1%, 150% at -1%, 200% at -2%
    HEDGE_MOMENTUM_GATE: bool = False  # BACKTEST_CHANGE_119: No momentum gate — 15m WT is the sole gate.
    HEDGE_MAX_RATIO: float = 2.0  # Hard cap 200% of losing position value.
    HEDGE_TRIGGER_LOSS_PCT_ENTRY: float = -2.0  # Cross-symbol trigger (HEDGE_MODE only, not obligatory).
    OBLIGATORY_HEDGE_PCT: float = 0.0  # DISABLED 2026-03-30: Caused cascade. Was 2.0 (200% of losing). Fires regardless of HEDGE_MODE — THAT WAS THE PROBLEM.
    OBLIGATORY_HEDGE_MIN_LOSS_PCT: float = 0.0  # 2026-04-15 user rule: hedge the INSTANT gain goes <0% — don't wait for larger loss
    OBLIGATORY_HEDGE_WT_TFS: int = 2  # Need 2 TFs with WT against before opening hedge.
    HEDGE_CLOSE_WT_TFS_FAVOR: int = 3  # BC_988: r2 winner but this is now unused — 15m WT close in code.
    HEDGE_SAME_SYMBOL_ENABLED: bool = True  # Re-enabled 2026-04-01: 150% same-symbol always active regardless of HEDGE_MODE. Cross-symbol only when HEDGE_MODE=True.
    HEDGE_DUAL_IF_HEDGE_MODE: bool = False  # Cross-symbol dual hedge disabled.
    HEDGE_ALL_POSITIONS: bool = False  # BC_988: NEW. If True, hedge ALL positions when wt15m against (not just losers). Test pending.
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
    # === DELTA ENGINE — FINAL WINNERS (2026-04-09, 48 sym × 4yr, Phase 2 sweep) ===
    # Crypto ST WINNER: Sharpe 0.806, ATR Sharpe 0.857, WR 84.5%, 97.9% profitable
    # Entry: mtf=3, ez=2.5, ea=0.0, tz=1.5, htf=4h_D, cd=120, tw=3m-dominant
    # Exit: speed_decay on 15m, sp=20, max_hold=30, avg hold 7 bars, giveback 0.58%
    # Crypto LT (20 sym): Sharpe 0.457, WR 68.1%, +2.59%/trade — stoch_cross on 4h
    DELTA_ENGINE_ENABLED: bool = True  # Master switch
    DELTA_ENTRY_ENABLED: bool = True
    DELTA_EXIT_ENABLED: bool = True
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
    BREAKEVEN_GRACE_MINUTES: float = 15.0    # Grace period before no-loss kicks in
    BREAKEVEN_DC_LOW4_ENABLED: bool = True   # DC_LOW4_3M structural stop
    # Feature toggles for ablation (2026-04-11)
    RZ_ZSCORE_ZONE_ENABLED: bool = True
    RZ_DIV_BLOCK_MIN: int = 2
    RZ_TWO_PHASE_EXIT_ENABLED: bool = True
    RZ_DIV_EXIT_ENABLED: bool = True
    RZ_ZSCORE_EXIT_ENABLED: bool = True
    # ═══ REENTRY — user directive "reenter ASAP" ═══
    REENTRY_COOLDOWN_S: float = 0.0          # Was 15s; zero for instant reentry
    # Scoring integration
    DELTA_SCORE_WEIGHT: float = 30.0       # Weight of delta signal in AdvancedSignalRater (0-100)
    DELTA_ENTRY_SCORE_BONUS: int = 15      # Score bonus when delta confirms entry
    DELTA_ENTRY_SCORE_PENALTY: int = -25   # Score penalty when delta opposes entry
    DELTA_EXIT_SCORE_BONUS: int = 20       # Score bonus when delta confirms exit
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
    LEGACY_REENTRY_PSR_QUICK_RECOVERY: bool = False    # ez_manage.py:18757 — price ± atr_3m within 60min, k cross
    LEGACY_REENTRY_PSR_K_DC_CROSSOVER: bool = False    # ez_manage.py:18777 LONG / 18798 SHORT — k_3m/15m crossover above dc_low_3m/15m
    LEGACY_REENTRY_PSR_FULL_DC: bool = False           # ez_manage.py:18812 — full reentry stoch_above_dc OR dc_basis_crossover_3m
    LEGACY_REENTRY_PSR_DC_BOUNCE: bool = False         # ez_manage.py:18836 — DC bounce within 8h, near dc_high/low
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
    RZ_EXIT_ENABLED: bool = True  # Use RED ZONE for exits (TOP exhausted/rejection, BOTTOM failed breakdown)
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
    STDEV_BREAKOUT_EXIT_WT_ENABLED: bool = True  # Also exit on WT turn against on 1h
    # === BACKTEST SWEEP WINNERS (2026-03-16) ===
    K3M_CAP: int = 80  # BACKTEST_CHANGE_105: REVERTED to 80. Tournament (10 rounds, 3042 combos) winner uses 80. BACKTEST_CHANGE_8 (70) reversed.
    K3M_FLOOR: int = 30  # BACKTEST_CHANGE_9: NEW. Block SHORT when k_3m <= 30 (mirror of K3M_CAP)
    CYCLE_TP_PCT: float = 0.60  # Let winners run to 60%. TP only used as absolute cap, NOT as early exit.
    CYCLE_TP_CONDITIONAL_EXIT: float = 0.003  # BACKTEST_CHANGE_101: was 0.5%. OKX top traders exit at 0.3% when stoch turns against. Matches profitable trader behavior.
    ACCOUNT_TP_PCT: Dict[str, float] = field(default_factory=dict)  # DISABLED 2026-03-29: NO fixed TP — ride winners until technicals turn. Exits via CYCLE_TP_STOCH_AGAINST, DC_BASIS_PROFIT_EXIT, IN_GAIN_TREND_EXIT only.
    TREND_ACCOUNTS: List[str] = field(default_factory=lambda: ["flz"])
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
    SATOSHIT_EXIT_ENABLED: bool = True  # Fixed 2026-04-07 — now exits at 1m/3m TOP (stoch cross down from OB), never at higher low
    SATOSHIT_PROTECT_TRADES: bool = True  # ON — only Satoshit exit can close Satoshit-opened positions
    SATOSHIT_EXIT_USE_MAKER: bool = True  # Use maker order for partial close (bypasses Finandy full close)
    SATOSHIT_EXIT_PARTIAL_PCT: float = 0.70  # Close 70% of position, keep 30% as runner
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
    CT_WT_VELOCITY_1H_MIN: float = 0.0  # BC_170: min wt_velocity_1h (0=must be directional)
    CT_15M_MOMENTUM_GATE_ENABLED: bool = False  # BC_171: DISABLED — standalone backtest proved USELESS (same avg return, worse monthly consistency 54.5%). Do NOT enable.
    CT_STOCH_K_15M_LONG_MIN: float = 45.0  # BC_171: (disabled)
    CT_STOCH_K_15M_SHORT_MAX: float = 55.0  # BC_171: (disabled)
    CT_MFI_15M_LONG_MIN: float = 45.0  # BC_171: (disabled)
    CT_MFI_15M_SHORT_MAX: float = 55.0  # BC_171: (disabled)
    CT_DC_CROSSOVER_SKIP_ENABLED: bool = True  # BC_172: ENABLED 2026-04-08. 5yr validated: SHORT Sharpe +34%, removes only 1.3% of trades. Skip SHORT when DC basis crosses over on 15m/1h.
    CT_CHOP_4H_GATE_ENABLED: bool = False  # BC_173: Skip if choppiness_4h > threshold. d=0.60 cross-trader. Winners=40.5, losers=51.3.
    CT_CHOP_4H_MAX: float = 50.0  # BC_173: max choppiness_4h
    CT_VOLUME_SURGE_GATE_ENABLED: bool = False  # BC_174: Require relative_volume_1h or _4h above threshold. d=2.80 (Graal strongest). Winners=2.6x, losers=1.0x.
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

    PERSIST =7200.0  # minutes to stay in tradeable_keys after deletion

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
