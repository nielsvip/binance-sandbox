# CONFIG_DOCUMENTATION.md — Reference for config.py, config_tradier.py, execute_trade_wrapper

**CRITICAL RULE**: Crypto and stock parameters are OPPOSITE — NEVER copy between them.

---

## config.py — Crypto Trading Config (`Config` dataclass)

### Position Sizing

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `MIN_POSITION_SIZE` | 1.0 | Floor — no position can be smaller |
| `MAX_POSITION_SIZE` | 20.0 | Cap per position (1/50 rule: 2% of $1k capital) |
| `MAX_POSITION_SIZE_BTC` | 2000.0 | Higher cap for BTC |
| `START_POSITION_SIZE` | 9.0 | Base entry size; scaled by scoring and sizing multipliers |
| `MAX_ORDER_VALUE` | 20.0 | Hard cap per individual order |
| `HIGH_GAIN_AUGMENTATION_MIN_SIZE` | 50 | Min account size to allow augmentation |
| `MIN_GAIN` | 3.0% | Min gain before augmenting a position (never augment losers) |
| `AUGMENT_ONLY_WHEN_PROFITABLE` | True | Block all augments when gain < 0 |
| `MAX_AUGMENTS_PER_POSITION` | 3 | Cap on total augment count per position |

---

### Trade Mode Flags

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `HEDGE_MODE` | True | Enable hedging via ez_positions_quick (NEVER the loops) |
| `BEAR_MARKET_MODE` | True | Favor shorts over longs when True |
| `SANDBOX_MODE` | False | Paper-trade mode for `sbx` account |
| `SCALP_MODE` | True | HTF Breakout Scalper V2 (inf account only) |
| `SCALP_V2_VARIANT` | "V1_WT_CONFIRM" | Sweep winner: Sharpe 107. DO NOT change. |
| `SCALP_V2_MAX_HOLD_MINUTES` | 15.0 | Proven: 60m universally worse for scalp |

---

### Timeframe Hierarchy

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `TF_FOCUS` | "3m" | Base TF for entries and exits (3m = 5min bar for crypto) |
| `TF_HTF1` | "15m" | First higher TF for alignment check |
| `TF_HTF2` | "1h" | Second higher TF |
| `TF_HTF3` | "4h" | Third higher TF |
| `TF_MACRO` | "D" | Daily — macro direction filter |
| `LTF` | "3m" | LTF alias used by quick engine (set by mode) |

---

### WaveTrend (WT) Exit Rules

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `WT_EXIT_MIN_TFS` | 2 | Min TFs that must flip against to trigger WT exit |
| `WT_CROSS_EXIT_ENABLED` | True | Fire when WT flips on 1h + 15m confirm |
| `WT_CROSS_EXIT_REQUIRE_15M_CONFIRM` | True | Require 15m confirmation for 1h cross exit |
| `WT_CROSS_EXIT_MIN_AGE_MINUTES` | 1.0 | Position must be open this long before exit allowed |
| `WRONG_SIDE_ABS_KILL_ENABLED` | True | **Dominant lever**: Close when 5/5 WT TFs against, age ≥30min |
| `WRONG_SIDE_WT_TFS_REQUIRED` | 5 | TFs against to trigger absolute kill |
| `WRONG_SIDE_MIN_AGE_MIN` | 30.0 | Min position age in minutes before kill fires |
| `WRONG_SIDE_K_TFS_REQUIRED` | 0 | Stoch K TFs required (0 = disabled) |

---

### NOLOSS / No-Loss Protection

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS` | ['GAIN_EROSION'] | Reasons that bypass the no-loss gate |
| `DC_RECOVERY_EXIT_ENABLED` | False | Allow close at loss when entry_price outside dc_4h range |
| `HARD_BREAKEVEN_FLOOR_ENABLED` | True | Once gain ≥ 0.5%, never close below breakeven again |
| `BREAKEVEN_GRACE_MINUTES` | 5.0 | Grace period before breakeven stop arms |
| `NOLOSS_BYPASS_WT_5OF5_ENABLED` | False | Allow close at loss if ALL 5 WT TFs flip against |
| `PEAK_GIVEBACK_PROTECTION_ENABLED` | True | Exit if gain drops ≥1.0% from peak after ≥0.5% gain |
| `PEAK_GIVEBACK_MIN_PEAK_PCT` | 0.5 | Min peak gain to arm giveback protection |
| `PEAK_GIVEBACK_DROP_PCT` | 1.0 | Exit if current gain dropped this much from peak |
| `HTF_EXIT_VETO_ENABLED` | True | Block 3m structural stops when HTF still aligned |
| `HTF_EXIT_VETO_MIN_ALIGNED` | 2 | Min of {1h, 4h, D} WT TFs that must confirm veto |
| `HTF_EXIT_VETO_MAX_LOSS_PCT` | 2.0 | Veto only applies when |gain| ≤ 2% (don't hold big bleeders) |

---

### Partial Profit Lock (PPL) — THE ONLY TP MECHANISM

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `PARTIAL_PROFIT_LOCK_ENABLED` | True | 50% close at +0.5% via maker/webhook_url_2 |
| `PARTIAL_PROFIT_LOCK_GAIN_PCT` | 0.5 | Step 1 trigger: close 50% at this gain % |
| `PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT` | 0.75 | Step 2: arm trailing stop at first-exit price |
| `PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT` | 0.02 | Stop = entry × (1 ± buffer) after Step 1 fires |
| `PARTIAL_PROFIT_LOCK_FRAC` | 0.5 | Semantic only — Finandy URL2 always closes 50% |
| `PARTIAL_PROFIT_LOCK_USE_MAKER` | True | Use maker order for lower fees |
| `PARTIAL_PROFIT_LOCK_ACCOUNTS` | all crypto accounts | Accounts where PPL is active |

**PPL is a REAL live mechanism**: Step 1 fires `place_maker_order` then falls back to `send_webhook(url_variant="2")` which routes to Finandy's 50% close config. NEVER add PROFIT_TARGET_PCT — it was fake and inflated Sharpe 8×.

---

### Hedge Configuration

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `OBLIGATORY_HEDGE_ENABLED` | True | FOREVER RULE — hedge every short (long) when WT agrees |
| `OBLIGATORY_HEDGE_MIN_LOSS_PCT` | -0.25 | Trigger hedge when gain drops below this |
| `OBLIGATORY_HEDGE_PCT` | 1.0 | Hedge size = 100% of position |
| `OBLIGATORY_HEDGE_WT_USE_3M` | True | 3m WT required for hedge trigger |
| `OBLIGATORY_HEDGE_WT_USE_1H` | True | 1h WT required for hedge trigger |
| `OBLIGATORY_HEDGE_WT_TFS_REQUIRED` | 2 | Both 3m AND 1h must confirm |
| `MANDATORY_HEDGE_ON_NEGATIVE_ENABLED` | True | Unconditional hedge at -2% regardless of WT |
| `MANDATORY_HEDGE_HARD_THRESHOLD_PCT` | -2.0 | Unconditional hedge threshold |
| `HEDGE_MAX_AGE_HOURS` | 6.0 | Force close any hedge older than this |
| `HEDGE_ENTRY_MODE` | "LOSS_AND_WT" | Entry condition: gain<0 AND wt_3m+1h against |

---

### Delta Engine (WT Velocity)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `DELTA_ENGINE_ENABLED` | True | Master switch |
| `DELTA_ENTRY_ENABLED` | True | Use delta as entry gate |
| `DELTA_EXIT_ENABLED` | True | Use delta decay as exit signal |
| `DELTA_ENTRY_Z_THRESHOLD` | 2.5 | Z-score threshold for entry confirmation |
| `DELTA_ENTRY_MIN_TF` | 3 | Min TFs confirming for fresh entry (strict gate) |
| `DELTA_REENTRY_MIN_TF` | 2 | Min TFs for reentry (looser — re-takes a proven position) |
| `DELTA_HTF_GATE` | "4h_D" | Both 4h AND D must confirm for delta entry |
| `DELTA_COOLDOWN_BARS` | 120 | Bars between delta entries (6h at 3m = sweep winner) |
| `DELTA_EXIT_TF` | "3m" | Exit on 3m dominance (sweep winner: 3m > 15m > 1h) |
| `DELTA_EXIT_DECAY_RATIO` | 0.90 | Speed decay ratio (sweep winner was 0.90, was 0.80) |
| `DELTA_EXIT_MIN_HOLD` | 4 | Bars minimum hold before delta can trigger exit |
| `DELTA_MAX_HOLD_BARS` | 0 | 0 = disabled. Never use fixed time exits. |
| `DELTA_GATE_GUARANTEED_REENTRY` | True | Block guaranteed reentry without delta (#1 loss source) |
| `DELTA_GATE_RATIO_REBALANCE` | False | Don't gate ratio rebalances — they are sacred |

---

### Entry Gate: Copy Trader Validated Gates (BC_170+172)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `CT_WT_VELOCITY_GATE_ENABLED` | True | Block entries against 1h WT velocity (5yr validated) |
| `CT_WT_VELOCITY_1H_MIN` | 9.0 | Min wt_velocity_1h for entry (sweep: 9+rally=30 → Sharpe 2.598) |
| `CT_DC_CROSSOVER_SKIP_ENABLED` | True | Skip SHORT when DC basis crosses over on 15m/1h |
| `CT_15M_MOMENTUM_GATE_ENABLED` | False | DEAD — 0.0000 ΔSharpe. Off forever. |
| `CT_CHOP_4H_GATE_ENABLED` | False | DEAD — no choppiness_4h in NPZ. Off forever. |
| `CT_VOLUME_SURGE_GATE_ENABLED` | False | DEAD — 0.0000 ΔSharpe. Off forever. |

---

### Entry Gates: HTF Direction

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `HTF_DIRECTION_GATE_ENABLED` | True | Block entries against D/4h/1h WT direction |
| `HTF_GATE_MIN_CONFIRMATIONS` | 2 | Min of {1h, 4h, D} that must agree with trade direction |
| `HTF_GATE_D_MANDATORY` | True | wt_D MUST align with trade direction |
| `HTF_GATE_SIGNALS_SMA200D` | True | Include price vs SMA200 as 4th signal |
| `D_TREND_REQUIRED` | True | Require daily trend alignment (crypto-specific) |

---

### RED ZONE (RZ) — Structural Level Entry/Exit

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `RZ_ENTRY_ENABLED` | True | Use BB stdev + DC position for entries |
| `RZ_EXIT_ENABLED` | False | Premature exits dropped Sharpe 2.5→1.25. Off. |
| `RZ_TOP_BB_THRESHOLD` | 0.85 | bb_pct_b above this = TOP zone |
| `RZ_BOT_BB_THRESHOLD` | 0.15 | bb_pct_b below this = BOTTOM zone |
| `RZ_CASCADE_ENABLED` | True | Multi-level cascade entry at RZ boundaries |
| `RZ_BREAKOUT_ENTRY_ENABLED` | True | Breakout entries near RZ boundaries |

---

### Reentry System

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `REENTRY_B02_BC156_BOTTOM_ENABLED` | True | BC156 bottom bounce reentry (best balance Sharpe 0.31/0.32) |
| `REENTRY_B04_DC_RETEST_ENABLED` | True | DC breakout retest reentry (high quality Sharpe 0.39) |
| `REENTRY_B11_DC_BREAK_ENABLED` | True | DC break reentry (94-97% WR, top quality) |
| `REENTRY_B12_WT_MOM_ENABLED` | True | WT momentum reentry (volume king 112k trades) |
| `REENTRY_B15_STRONG_TREND_ENABLED` | True | Strong trend reentry (94-97% WR, sniper) |
| `REENTRY_B01_WT_2of3_ENABLED` | False | ABLATION: Sharpe 0.033 = noise. CUT. |
| `REENTRY_B09_SNAPBACK_ENABLED` | False | ABLATION: Sharpe 0.022 = weak. CUT. |
| `REENTRY_WT15M_CROSS_ENABLED` | True | WT 15m cross + HTF favorable = 1.5x reentry |
| `REENTRY_K15M_PARTIAL_ENABLED` | True | k_15m-based partial reentry (high K = smaller size) |
| `REENTRY_K15M_PARTIAL_THRESHOLD` | 90.0 | k_15m ≥ 90 (LONG) → partial reentry at smaller size |
| `REENTRY_AGGRESSIVE_WINDOW_MIN` | 30.0 | 30min window after exit for aggressive reentry (peak at 8-12 bars) |
| `REENTRY_COOLDOWN_S` | 0.0 | Zero = instant reentry (was 15s) |
| `REENTRY_2_ENABLED` | True | Periodic reentry pass (~$420 PnL per ablation) |

---

### L/S Ratio Enforcement

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `LS_RATIO_ENFORCE` | True | Master toggle for ratio enforcement |
| `LS_RATIO_MIN` | 0.11 | Allow 90/10 short/long (WT controls direction) |
| `LS_RATIO_MAX` | 9.0 | Allow 90/10 long/short |
| `LS_RATIO_HARD_MIN` | 0.05 | Near-zero floor — must follow WT, not fight it |
| `LS_RATIO_HARD_MAX` | 3.50 | Block extreme longs if ratio > 3.5 |
| `RATIO_EMERGENCY_EXIT_ENABLED` | False | PERMANENTLY DISABLED: closing losers destroyed Sharpe (19 vs 357 ratio-only) |
| `RATIO_MULTIPLIER` | 3.0 | Base sizing for rebalance entries (NEVER below 3.0) |
| `RATIO_SENTINEL_FILTER_ENABLED` | True | Breadth-driven ratio direction filter |
| `RATIO_PNL_WEIGHT_ENABLED` | True | Shift ratio target toward winning side based on PnL |
| `RATIO_CLOSE_LOSING_OVERWEIGHT` | False | OFF — historically destroys Sharpe. Never enable without monitoring. |

---

### Structural Range Shift Exit (SRS)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `STRUCTURAL_RANGE_SHIFT_EXIT` | True | Exit when entry_price is outside dc_4h range (market permanently shifted) |
| `STRUCTURAL_RANGE_SHIFT_TF` | "dc_4h" | Reference TF — crypto uses dc_4h, stocks use bb_1h |
| `STRUCTURAL_RANGE_SHIFT_K_HIGH` | 75.0 | LONG: k_1h/15m must be ≥ this AND turning down |
| `STRUCTURAL_RANGE_SHIFT_K_LOW` | 25.0 | SHORT: k_1h/15m must be ≤ this AND turning up |

---

### MIN_HOLD_BARS (critical for Sharpe)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `V8Q_MIN_HOLD_BARS` | 250 | 12.5h hold minimum (3m × 250). Sharpe 1.508→2.554 on 48 sym. |
| `OPTIMAL_HOLD_BARS_3M` | 999 | No forced exit: confirmed +1.04 Sharpe, 85% improved |
| `OPTIMAL_HOLD_BARS_15M` | 999 | No forced exit: removing forced exit was #1 best change |

---

### Strategies (Master Switches)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `SATOSHIT_ENABLED` | True | Mean-reversion copy trader strategy (100% WR in backtest) |
| `SATOSHIT_EXIT_ENABLED` | False | DISABLED — replaced by PPL mechanism |
| `DELTA_ENGINE_ENABLED` | True | WT velocity engine (Phase 2 winner: Sharpe 0.806, WR 84.5%) |
| `BB_SQUEEZE_ENABLED` | True | Bollinger band compression breakout entries |
| `VOL_SPIKE_ENABLED` | True | Volume spike reversal (93.5% WR, Sharpe 13.9) |
| `STDEV_BREAKOUT_ENABLED` | False | 2.5σ breakout — backtest first |
| `CRYPTO_SPIKE_FADE_ENABLED` | True | P&D fade (Sharpe 2.43, thresh=10%, lb=3) |
| `HLR_RALLY_ENABLED` | True | Higher Low Rally / Lower High Breakdown detector |
| `MOMENTUM_FADE_ENABLED` | False | ABLATION_V3_REVERT: +2.36 Sharpe by removing. DESTROYS edge. |
| `MOMENTUM_RIDER_ENABLED` | False | PERMANENTLY DISABLED: bypasses ALL execute_now guards |
| `LOSS_CUT_ENABLED` | False | NEVER ENABLE: proven to lose 20%+ weekly |

---

### Ablation Flags (Default = function enabled)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `ABLATION_DISABLE_REENTRY` | False | CRITICAL: -1.9 Sharpe when removed. The system IS reentry. |
| `ABLATION_DISABLE_AUGMENTATION` | False | -0.3 Sharpe. Keep. |
| `ABLATION_DISABLE_HEDGE` | False | Keep. |
| `ABLATION_DISABLE_RATIO_REBALANCE` | False | Keep. |
| `ABLATION_DISABLE_QUICK_EXIT` | False | Disable WT/DC exits entirely |
| `ABLATION_DISABLE_QUICK_ENTRY` | False | Disable check_entry_candidates |
| `ABLATION_DISABLE_ENTRY_TECHNICAL` | True | ABLATION: -0.018 Sharpe delta = redundant |
| `ABLATION_DISABLE_ENTRY_RANKING` | True | ABLATION: 0.000 delta (needs Redis, adds noise) |

---

## config_tradier.py — Stock Trading Config (`TradierConfig` dataclass)

### KEY DIFFERENCE FROM CRYPTO

> Stocks are OPPOSITE to crypto in almost every parameter. Never copy config values between modes.
> SRS TF = `bb_1h` (NEVER `dc_4h` — caused April-13 disaster). ATR_TRAIL = DISABLED (#1 PnL destroyer).

### Position Sizing

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `MIN_POSITION_SIZE` | 100.0 | Floor for stock positions ($100 minimum) |
| `MAX_POSITION_SIZE` | 5000.0 | Cap per position |
| `START_POSITION_SIZE` | 600.0 | Base entry size (was 400, raised for larger base) |
| `MAX_ORDER_VALUE` | 2000.0 | Hard cap per order |
| `MIN_GAIN_TO_BUY_AGGRESSIVELY` | 3.0% | Min gain before augmenting (NEVER below 2.5%) |

### Key Tradier-Specific Switches

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `WT_COMPOSITE_SCORING_ENABLED_TRADIER` | True | Cross-TF WT scoring for stocks |
| `ATR_TRAIL_ENABLED_TRADIER` | False | DISABLED: #1 PnL destroyer on stocks (-2557%) |
| `STRUCTURAL_RANGE_SHIFT_TF_TRADIER` | "bb_1h" | Stocks use BB1h, NOT dc_4h |
| `TRA_LONG_ONLY` | True | `tra` is cash account — no shorts ever |
| `TRA_NO_LOSS_EXIT` | True | `tra` never closes at a loss |
| `NOLOSS_ENABLED_TRADIER` | True | Universal no-loss gate for stock positions |
| `UNIVERSAL_NOLOSS_GATE_ENABLED` | True | Function-top NOLOSS gate in evaluate_stop() |
| `DELTA_EXIT_OVERRIDE_NOLOSS_TRADIER` | False | Delta exits do NOT bypass NOLOSS for stocks |

### Entry Gates (Stock-Optimized)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `ENTRY_SCORE_THRESHOLD_TRADIER` | 24 | Stocks require score 24 (crypto: 18) — higher bar |
| `TF_ALIGNMENT_MIN_LONG_TRADIER` | 2 | Stocks need ≥2 HTF aligned (crypto: 1) |
| `STOCH_CROSS_ENTRY_TRADIER` | True | Stochastic cross required for stock entries |
| `CT_WT_VELOCITY_GATE_ENABLED_TRADIER` | True | Same velocity gate as crypto (BC_170) |
| `BACKTEST_VALIDATED_GATES_TRADIER` | True | Only use sweep-validated entry conditions |

### Tradier MTS Weights (Daily-Dominant)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `MTS_WEIGHT_5m` | 2.0 | 5m TF weight in WT composite scoring |
| `MTS_WEIGHT_15m` | 4.0 | 15m TF weight |
| `MTS_WEIGHT_1h` | 3.0 | 1h weight (stocks: 3 vs crypto: 12 — D matters more) |
| `MTS_WEIGHT_4h` | 5.0 | 4h weight |
| `MTS_WEIGHT_D` | 12.0 | **DOMINANT** for stocks (vs 1h for crypto) |
| `MTS_GATE_ENABLED_TRADIER` | False | OFF — stocks benefit from WT entry alone |

### PPL for Tradier

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `PARTIAL_PROFIT_LOCK_ENABLED_TRADIER` | True | 50% close at +0.5% on stocks |
| `PARTIAL_PROFIT_LOCK_ACCOUNTS_TRADIER` | ["trb","trc"] | Accounts for stock PPL |
| `PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER` | 0.5 | Same as crypto: 0.5% trigger |
| `PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER` | 0.75 | Step 2 arm at 0.75% |
| `PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER` | 0.02 | Breakeven buffer |

### WRONG_SIDE_ABS_KILL for Stocks

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `WRONG_SIDE_ABS_KILL_ENABLED_TRADIER` | True | Same dominant lever as crypto |
| `WRONG_SIDE_WT_TFS_REQUIRED_TRADIER` | 5 | 5/5 TFs against (5m/15m/1h/4h/D) |
| `WRONG_SIDE_MIN_AGE_MIN_TRADIER` | 30.0 | 30min position age before kill fires |
| `WRONG_SIDE_K_TFS_REQUIRED_TRADIER` | 0 | K gate disabled (same as crypto) |

### LOCAL_EXTREMES_SCORER (entry filter)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `TRADIER_LOCAL_EXTREMES_SCORING_ENABLED` | True | Gate entries through 25-indicator LE scorer, $50-$5000 tier sizing |
| `LOCAL_EXTREMES_MIN_SCORE` | 45.0 | Min LE score to allow entry (262sym validated Sharpe 3.5479) |
| `TRC_LOCAL_EXTREMES_SCORER_ENABLED` | True | LE scorer on trc (paper) account |
| `DYNAMIC_SCORE_COUNTER_EXIT_ENABLED` | True | Exit when opposite-direction LE score ≥ threshold |
| `DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD` | 55.0 | Counter-exit trigger (score=55 le_dynamic winner) |

---

### Entry Gates (Stock-Specific Levels)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `ENTRY_ZONE_LONG` | 35.0 | WT entry zone — only long when wt ≤ 35 (V8 ablation winner, was 25) |
| `ENTRY_ZONE_SHORT` | 100.0 | WT short zone (100−35=65 mirror) |
| `ENTRY_MIN_ALIGNMENT` | 10 | Min alignment score (V8 ablation: Sharpe 1.0, WR 53.9%) |
| `ENTRY_PRIMARY_TF` | "4h" | Primary TF for entry direction (was 1h, slower = better for stocks) |
| `ENTRY_TRIGGER_TF` | "15m" | Trigger TF for crossover confirmation |
| `ALIGNMENT_GATE_MIN` | 4 | Min indicators aligned |
| `ALIGNMENT_GATE_TOTAL` | 12 | Total alignment score required |
| `TRADIER_ENTRY_SCORE_THRESHOLD` | 24 | Min aggregate signal score for entry (stocks need 24 vs crypto 18) |
| `DC_POSITION_ENTRY_THRESHOLD` | 0.25 | DC channel position for entry (0.25 = lower quarter / upper quarter) |
| `SMA200_DIST_ENTRY_ENABLED` | True | SMA200 distance gate: only long within −10% of SMA200 |
| `SMA200_DIST_LONG_THRESHOLD_4H` | -10.0 | Max distance below SMA200 for longs |
| `MFI_ENTRY_ENABLED` | True | Daily MFI gate: only long when MFI_D < 20 (oversold) |
| `MFI_LONG_THRESHOLD_D` | 20.0 | Daily MFI threshold for longs |
| `TRADIER_MFI_ENTRY_LONG_TRADIER` | 60.0 | MFI > 60 for long entry (LONGS ONLY — RSI for shorts) |
| `TRADIER_MFI_ENTRY_LONG_ENABLED` | True | MFI gate is active for longs |
| `TRADIER_RSI_ENTRY_LONG_TRADIER` | -1.0 | SENTINEL: <0 = DISABLED (longs use MFI, not RSI) |
| `TRADIER_RSI_ENTRY_SHORT_TRADIER` | 70.0 | RSI > 70 for short entry (SHORTS ONLY) |
| `TRADIER_RSI_SHORT_REL_VOLUME_MIN` | 1.2 | Relative volume > 1.2× required for short RSI entry |
| `TRADIER_RSI_LONG_15M` | 40.0 | 15m RSI < 40 for long (A/B winner 2026-04-17) |
| `TRADIER_RSI_LONG_1H` | 22.0 | 1h RSI < 22 for long — REAL LEVER (was not per-TF) |
| `HTF1_CONF` | True | BC_154: HTF2 stocks Sharpe 0.600 vs default -0.177. ON. |
| `HTF4_CONF` | False | htf1 alone sufficient; htf4 too restrictive |
| `BACKTEST_VALIDATED_GATES_TRADIER` | True | Block entries confirmed −EV on both train+test |

---

### Stoch / K-Zone Entry

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `K_ZONE_LONG_THRESHOLD_TRADIER` | 35 | K < 35 = oversold zone for longs (S1 sweep: Sharpe 4.23) |
| `K_ZONE_SHORT_THRESHOLD_TRADIER` | 65 | K > 65 = overbought zone for shorts |
| `K_ZONE_ENTRY_BONUS_TRADIER` | 20 | Score bonus in K-zone (2026-04-08: Sharpe 11.12 at 20 vs 25→6.92) |
| `TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER` | 35 | Reconnected mirror of K_ZONE_LONG (same value) |
| `TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER` | 65 | Reconnected mirror of K_ZONE_SHORT |
| `TRADIER_STOCH_ENTRY_LONG_TRADIER` | 30 | K < 30 for normal long entry |
| `TRADIER_STOCH_ENTRY_SHORT_TRADIER` | 70 | K > 70 for normal short entry |
| `TRADIER_STOCH_EXTREME_LONG_TRADIER` | 15 | K < 15 for high-conviction long |
| `TRADIER_STOCH_EXTREME_SHORT_TRADIER` | 85 | K > 85 for high-conviction short |

---

### RSI Per-TF Thresholds (2026-04-17)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `TRADIER_RSI_LONG_5M` | 35.0 | 5m RSI < this for long |
| `TRADIER_RSI_LONG_15M` | 40.0 | 15m RSI < this for long (A/B winner) |
| `TRADIER_RSI_LONG_1H` | 22.0 | 1h RSI < this for long (KEY lever) |
| `TRADIER_RSI_LONG_4H` | 35.0 | 4h RSI < this for long |
| `TRADIER_RSI_LONG_D` | 40.0 | Daily RSI < this for long |
| `TRADIER_RSI_SHORT_5M` | 65.0 | 5m RSI > this for short |
| `TRADIER_RSI_SHORT_15M` | 65.0 | 15m RSI > this for short |
| `TRADIER_RSI_SHORT_1H` | 65.0 | 1h RSI > this for short |
| `TRADIER_RSI_SHORT_4H` | 60.0 | 4h RSI > this for short |
| `TRADIER_RSI_SHORT_D` | 55.0 | Daily RSI > this for short |

---

### CT Gates (BC_170/172 for Stocks)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `CT_WT_VELOCITY_GATE_ENABLED` | True | Block against 1h WT velocity (same as crypto — validated) |
| `CT_WT_VELOCITY_1H_MIN` | 2.0 | Min wt_velocity_1h (stocks: 2.0 vs crypto: 9.0 — stocks move slower) |
| `CT_DC_CROSSOVER_SKIP_ENABLED` | True | Skip SHORT when DC basis crosses over |
| `CT_REL_VOL_MIN` | 1.3 | Min relative volume for entry |
| `CT_15M_MOMENTUM_GATE_ENABLED` | False | DEAD — 0.0000 ΔSharpe. Off forever. |
| `CT_CHOP_4H_GATE_ENABLED` | False | DEAD — no choppiness_4h. Off forever. |
| `CT_VOLUME_SURGE_GATE_ENABLED` | False | DEAD — 0.0000 ΔSharpe. Off forever. |

---

### Exit Gates (Stocks)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `WT_EXIT_MIN_TFS_TRADIER` | 5 | 5/5 TFs against = exit. Patient exits (Mar-30 baseline). |
| `MIN_HOLD_BARS_TRADIER` | 40 | 40 bars × 5m = 200min min hold (2026-04-20 sweep winner) |
| `TRADIER_MIN_HOLD_MINUTES` | 100.0 | Min hold 100min (le_dynamic winner, was 240=4h) |
| `MIN_HOLD_MINUTES_TRADIER` | 30.0 | No exits before 30min (bypassed only if loss > −5%) |
| `MIN_EXIT_TF_AGAINST_TRADIER` | 2 | Need 2+ TFs (of 5m/15m/1h/4h) with WT against before exit |
| `STOCH_CROSS_1H_EXIT_ENABLED` | True | Stoch cross on 1h triggers exit |
| `WT_CROSSUNDER_FINAL_ENABLED` | True | WT crossunder on 15m for short exits |
| `ATR_TRAIL_ENABLED_TRADIER` | False | **DISABLED: #1 stock PnL destroyer (−2557%). NEVER enable.** |
| `STRUCTURAL_RANGE_SHIFT_EXIT` | True | Exit at bb_1h boundary when range shifts (NOT dc_4h — April-13 disaster) |
| `STRUCTURAL_RANGE_SHIFT_TF` | "bb_1h" | STOCKS: bb_1h MANDATORY. CRYPTO: dc_4h. bb_4h = disaster. |
| `STRUCTURAL_RANGE_SHIFT_K_HIGH` | 85.0 | Only exit at extreme overbought (tightened from 75) |
| `STRUCTURAL_RANGE_SHIFT_K_LOW` | 15.0 | Only exit at extreme oversold (tightened from 25) |
| `MFI_FLIP_EXIT_ENABLED` | True | Exit when MFI exhausts (BACKTEST_CHANGE_148: +3.91% avg vs +1.09% fixed TP) |
| `MFI_FLIP_EXIT_LONG_THRESHOLD` | 70.0 | Exit LONG when MFI_1h > 70 (overbought) |
| `MFI_FLIP_EXIT_SHORT_THRESHOLD` | 30.0 | Exit SHORT when MFI_1h < 30 (oversold = cover) |
| `TRADIER_WT_EXIT_MIN_TFS_TRADIER` | 5 | Reconnected: same as WT_EXIT_MIN_TFS (all 5 TFs must agree) |
| `WT_DC_EXIT_THRESHOLD` | 30 | WT/DC scorer exit threshold |
| `COOLDOWN_BARS_TRADIER` | 8 | 40min cooldown between entries (sweep: Sharpe 8.22 at 8 vs 0) |

---

### Exit Path ON/OFF Switches

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `EXIT_HTF_QUICK_TP_ENABLED` | True | Keep: 1h exhausted + LTFs turning + 4h intact |
| `EXIT_STRUCT_DC_BREAK_ENABLED` | True | Keep: DC structural break (multi-TF) |
| `EXIT_K5M_BOUNCE_ENABLED` | False | OFF: 5m noise kills positions |
| `EXIT_HARD_DROP_5M_ENABLED` | False | OFF: too aggressive on minor dips |
| `EXIT_ALGO_SCORE_ENABLED` | False | OFF: old scorer, bypassed current system |
| `EXIT_STRUCT_BREAK_5M_ENABLED` | False | OFF: too noisy for swing/options |
| `EXIT_IBS_EXHAUSTION_ENABLED` | False | OFF: minor signal, not standalone |
| `EXIT_SENTIMENT_ENABLED` | False | OFF: unreliable source |
| `EXIT_MI_ENABLED` | False | OFF: marginal value |
| `EXIT_CONV_FAIL_ENABLED` | False | OFF: closed at tiny gains |
| `EXIT_BOUNCE_TOP_ENABLED` | False | OFF: percentage stop in disguise |
| `EXIT_MAX_HOLD_ENABLED` | False | OFF: technicals decide, not clocks |

---

### NOLOSS for Stocks

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `NOLOSS_MIN_PROFIT_PCT_TRADIER` | 3.0 | Min profit % before close allowed (REVERTED from 0%: 0% caused exits at 0.3%) |
| `UNIVERSAL_NOLOSS_GATE` | True | Function-top gate in evaluate_stop() — fires before ALL other exit logic |
| `TRADIER_NOLOSS_SRS_BYPASS` | True | SRS reason bypasses NOLOSS gate |
| `NOLOSS_BYPASS_WT_5OF5_ENABLED` | False | Exception: 5/5 TFs against → allow exit at loss |
| `NOLOSS_BYPASS_WT_5OF5_MIN_TFS` | 5 | Min TFs required for bypass |
| `BB_RECOVERY_EXIT_ENABLED_TRADIER` | True | Allow loss exit when price recovers into entry zone (unlocks stranded positions) |
| `BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER` | 0.30 | Price must return within 0.30% of entry_price |
| `WT_D_BOUNCE_AUG_ENABLED` | True | Add to losing position when daily WT bounces (2026-04-20 live) |
| `WT_D_BOUNCE_AUG_MULTIPLIER` | 2.0 | 2x add (add 1x to existing — was 4x) |
| `WT_D_BOUNCE_AUG_REQUIRE_HIGHER_WT` | True | Bounce WT must be > last aug WT |
| `WT_D_BOUNCE_AUG_REQUIRE_HIGHER_PRICE` | True | Bounce price must be > last aug price (higher low for LONG) |

---

### PPL for Stocks (2026-04-21)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `PARTIAL_PROFIT_LOCK_ENABLED` | False | **OFF**: 2026-04-21 sweep: PPL underperforms on tradier (gain −10%, Sharpe flat). 108-config grid. DO NOT re-enable without fresh sweep. |
| `PARTIAL_PROFIT_LOCK_ACCOUNTS_TRADIER` | ["trb","trc"] | Accounts for stock PPL |
| `PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER` | 0.5 | Trigger: close 50% at +0.5% |
| `PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER` | 0.75 | Upgrade stop at +0.75% |
| `PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER` | 0.02 | Breakeven buffer |
| `PARTIAL_PROFIT_LOCK_USE_MAKER_TRADIER` | True | Maker order for lower fees |

---

### WRONG_SIDE_ABS_KILL for Stocks

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `WRONG_SIDE_ABS_KILL_ENABLED` | True | Close when 5/5 WT TFs against, age ≥30min (dominant lever) |
| `WRONG_SIDE_WT_TFS_REQUIRED` | 5 | All 5 TFs (5m/15m/1h/4h/D) must be against |
| `WRONG_SIDE_WT_TFS_REDUCED` | 3 | Divergence path: 3/5 TFs against + divergence |
| `WRONG_SIDE_MIN_AGE_MIN` | 30.0 | Min position age in minutes |
| `WRONG_SIDE_K_TFS_REQUIRED` | 0 | K gate disabled for stocks |

---

### Trading Strategies

#### Rotation Strategy (5yr: +71.2%, Sharpe 0.60)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `ROTATION_ENABLED` | True | Momentum rotation across symbols |
| `ROTATION_TOP_N` | 3 | Buy top 3 momentum symbols |
| `ROTATION_BOTTOM_N` | 8 | Short bottom 8 (more short candidates in bear) |
| `ROTATION_HOLD_DAYS` | 7 | Hold 7 days (was 5, raised to 7 for longer holds) |
| `ROTATION_LOOKBACK_DAYS` | 10 | 10-day return lookback (5yr optimal) |
| `ROTATION_POSITION_SIZE` | 1200.0 | Base size per rotation trade (was 800) |

#### RSI(2) Mean Reversion (Sharpe 2.05, 60.8% WR)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `RSI2_ENABLED` | True | 2-period RSI mean reversion strategy |
| `RSI2_ENTRY_THRESHOLD` | 3.0 | Buy when RSI2 < 3 (was 5 — stricter) |
| `RSI2_EXIT_THRESHOLD_LONG` | 70.0 | Exit long when RSI2 > 70 (hold longer, was 65) |
| `RSI2_EXIT_THRESHOLD_SHORT` | 30.0 | Exit short when RSI2 < 30 (hold longer, was 35) |
| `TRADIER_RSI2_ENABLED` | True | Reconnected mirror (same behavior) |
| `TRADIER_RSI2_EXIT_THRESHOLD_LONG` | 90.0 | Reconnected: exit long at RSI2 > 90 |

#### Gap Fill Strategy (OOS Sharpe 9.05, 66.1% WR)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `GAP_FILL_ENABLED` | True | Fill overnight gaps |
| `GAP_FILL_MIN_GAP_PCT` | 0.5 | Min gap size (T6 sweep: 0.5% > 1.0% fills more reliably) |
| `GAP_FILL_MAX_GAP_PCT` | 5.0 | Max gap (gaps > 5% are news events, skip) |
| `GAP_FILL_TP_FILL_PCT` | 0.7 | Close at 70% of gap filled (was 0.5, capture more) |
| `GAP_FILL_STOP_MULT` | 0.3 | Stop at 0.3× gap size |

#### First-Hour Momentum (FH) — Sharpe 1.36-1.54, 25/25 profitable

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `FH_MOMENTUM_ENABLED` | True | First-30min range breakout detection |
| `FH_MOMENTUM_MIN_MOVE_PCT` | 0.5 | Min gap move to qualify (82% day-follows at 0.5%) |
| `FH_MOMENTUM_EVAL_MINUTES` | 30 | Eval window after market open |
| `FH_MOMENTUM_DC_CONFIRM` | True | DC retest logic for smart filtering |
| `FH_MOMENTUM_MFI_CONFIRM` | False | MFI barely matters (1.314 vs 1.313 Sharpe) |
| `TRADIER_FH_MOMENTUM_ENABLED` | True | Reconnected master switch (same as FH_MOMENTUM_ENABLED) |
| `TRADIER_FH_MOMENTUM_MIN_MOVE_PCT` | 0.5 | Reconnected: same as FH_MOMENTUM_MIN_MOVE_PCT |
| `TRADIER_FH_MOMENTUM_WINDOW_MINUTES` | 60 | Extended FH window to 60min for reconnected switch |
| `TRADIER_FH_MOMENTUM_MFI_MIN` | 55.0 | MFI > 55 for FH long (when MFI_CONFIRM=True) |

#### DC Daytrade (open AM, flatten before close)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `DC_DAYTRADE_ENABLED` | True | DC breakout daytrade system |
| `DC_DAYTRADE_LONG_BUDGET` | 3000.0 | Max $ in daytrade longs |
| `DC_DAYTRADE_SHORT_BUDGET` | 3000.0 | Max $ in daytrade shorts |
| `DC_DAYTRADE_START_SIZE` | 600.0 | Base order size |
| `DC_DAYTRADE_REQUIRE_1H_EXPANSION` | True | Only trade DC breaks when 1h channel expanding |
| `DC_DAYTRADE_STOP_PCT` | 0.015 | 1.5% hard stop |
| `DC_DAYTRADE_TARGET_PCT` | 0.01 | 1% profit target |
| `DC_DAYTRADE_MAX_HOLD_MINUTES` | 240.0 | 4h max hold (flatten before close) |
| `DC_DAYTRADE_PRE_CLOSE_MINUTES` | 120 | Flatten 2h before close (14:00 ET = 18:00 UTC) |
| `TRADIER_DC_DAYTRADE_ENABLED` | True | Reconnected mirror |
| `TRADIER_DC_DAYTRADE_TARGET_PCT` | 0.005 | Reconnected: 0.5% target (sweep winner) |

#### Spike Fade (BC_161 — +480%, 67% WR, W/L 4.69)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `SPIKE_FADE_ENABLED` | True | SHORT big spikers / LONG big fallers |
| `SPIKE_FADE_THRESHOLD_PCT` | 2.0 | Min move in lookback to qualify as spike |
| `SPIKE_FADE_LOOKBACK_BARS` | 6 | 6 bars × 5m = 30min lookback |
| `SPIKE_FADE_K_EXHAUSTION` | 70.0 | K5m > 70 (spike up) or < 30 (spike down) |
| `SPIKE_FADE_MAX_POSITIONS` | 10 | Max concurrent spike fade positions |

---

### Delta Engine (Stocks — Different from Crypto)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `DELTA_ENGINE_ENABLED` | True | 121sym/2yr: Sharpe 0.038→0.485 (T25: keeping True) |
| `DELTA_ENTRY_ENABLED` | False | T25 sweep: False avg=0.527 vs True=0.507. Best OFF. |
| `DELTA_EXIT_ENABLED` | True | Re-enabled with REENTRY_MONITOR fix |
| `DELTA_ENTRY_Z_THRESHOLD` | 2.5 | Z-score for entry (stocks ST winner: ez=2.5) |
| `DELTA_ENTRY_MIN_TF` | 3 | Min TFs for fresh entry (ST: 3, LT: 2) |
| `DELTA_HTF_GATE` | "4h" | Stocks: 4h must confirm (LT: 4h+D) |
| `DELTA_EXIT_TF` | "15m" | Exit TF (ST winner: 15m) |
| `DELTA_EXIT_DECAY_RATIO` | 0.30 | Speed decay from peak (0.30 = stocks ST #1) |
| `DELTA_TF_WEIGHTS_STOCK` | {set in __post_init__} | HTF-only weights: 5m/15m = 0, 1h/4h/D get weight |
| `DELTA_COOLDOWN_BARS` | 60 | 60 bars = 5h (ST winner) |
| `DELTA_MAX_HOLD_BARS` | 0 | DISABLED — ride winners, no fixed time exits |

---

### SATOSHIT2024 — Stocks

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `SATOSHIT_ENTRY_FILTER` | False | T25: True=0.388 vs False=0.328 (+18%). Marginal — leaving False. |
| `SATOSHIT_MIN_VOTES_TRADIER` | 3 | 3 of 5 sub-signals must agree |
| `SATOSHIT_LONG_RSI_MAX_TRADIER` | 50.0 | Long entry: RSI < 50 |
| `SATOSHIT_LONG_STOCH_K_MAX_TRADIER` | 60.0 | Long entry: K < 60 |
| `SATOSHIT_LONG_MFI_MAX_TRADIER` | 60.0 | Long entry: MFI < 60 |
| `SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER` | 55.0 | Exit long: RSI > 55 |
| `SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER` | 60.0 | Exit long: K > 60 |

---

### Reentry (Stocks)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `REENTRY_RALLY_K15M_MAX` | 100.0 | 100 = OFF (sweep: 100/40/20 grid) |
| `REENTRY_RALLY_HTF_MIN` | 3 | 2026-04-18: wt_all3 avg 0.1036 vs wt_2of3 −0.0468. Was 2. |
| `TRADIER_REOPEN_WAIT_S` | 0.0 | Zero = instant reentry (was 300s) |
| `REENTRY_TIER1_SIZE_MULT_TRADIER` | 1.5 | Tier 1: 150% of closed qty |
| `REENTRY_TIER2_SIZE_MULT_TRADIER` | 0.8 | Tier 2: 80% of closed qty (later pullback) |
| `REENTRY_TIER2_PRICE_PCT_TRADIER` | 0.003 | 0.3% price move triggers Tier 2 |
| `REENTRY_TIER2_MAX_MINUTES_TRADIER` | 120.0 | Force Tier 2 entry after 120min |

---

### L/S Ratio, Position Limits, Account Settings

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `LS_RATIO_ENFORCE_TRADIER` | True | Enforce L/S ratio |
| `LS_RATIO_MIN_TRADIER` | 0.50 | Min L/S (stocks more balanced than crypto) |
| `LS_RATIO_MAX_TRADIER` | 2.00 | Max L/S |
| `MAX_CONCURRENT_POSITIONS` | 16 | Total max positions across all strategies |
| `MAX_SYMBOL_VALUE_TRADIER` | 15000.0 | Max $ per symbol (USO hit $352k — disaster) |
| `RATIO_MULTIPLIER_TRADIER` | 3.5 | Ratio rebalance sizing (sweep: 3.5x = Sharpe 260) |
| `BEAR_MARKET_MODE_TRADIER` | True | Favor shorts in current bear market |
| `AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER` | True | Never augment losing positions |
| `MAX_DAILY_LOSS_PCT` | 3.0 | Halt trading at 3% daily loss |
| `MIN_GAIN` | 3.0 | Same 3% augment rule as crypto |

---

### TRC Sandbox Account Overrides (Paper Money)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `TRC_START_POSITION_SIZE` | 1000.0 | 1.67× trb base ($600) |
| `TRC_MAX_POSITION_SIZE` | 5000.0 | Hard cap at $5000 per trc position |
| `TRC_MAX_CONCURRENT_POSITIONS` | 40 | 20 long + 20 short |
| `TRC_SWING_LONG_BUDGET` | 100000.0 | Unlimited paper budget for local extremes |
| `TRC_LOCAL_EXTREMES_SCORER_ENABLED` | True | LE scorer on trc |
| `TRC_BEAR_MARKET_MODE` | False | Test both directions equally on paper |
| `TRC_ENTRY_ZONE_LONG` | 25.0 | Deeper oversold threshold vs trb (35) |
| `TRC_MAX_SYMBOL_VALUE` | 5000.0 | Per-symbol cap on paper |
| `TRC_NOLOSS_MIN_PROFIT_PCT` | 0.0 | Technical exits only on paper |

---

### Options Strategies (trb_long/trb_short)

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `OPTIONS_ENABLED` | True | Master switch for options strategies |
| `OPTIONS_POSITION_SIZE` | 1000.0 | Base option purchase size |
| `OPTIONS_MAX_CONCURRENT` | 20 | Max concurrent option positions |
| `OPTIONS_MAX_TOTAL_VALUE` | 15000.0 | Max total option portfolio $ |
| `OPTIONS_LONG_BUDGET` | 7500.0 | Max in longs (65%) |
| `OPTIONS_SHORT_BUDGET` | 7500.0 | Max in shorts (65%) |
| `OPTIONS_SPREAD_ENABLED` | True | Bull Put Credit Spread (7yr: Sharpe 0.62, 80% WR) |
| `OPTIONS_SPREAD_SHORT_DELTA` | 0.25 | Short-put target delta |
| `OPTIONS_SPREAD_IV_RANK_MIN` | 75.0 | Chain IV rank gate (biggest backtest edge) |
| `OPTIONS_SPREAD_DTE_MIN` | 55 | Min 2-month DTE |
| `OPTIONS_SPREAD_DTE_MAX` | 75 | Max ~10 week DTE |
| `OPTIONS_SPREAD_PROFIT_TARGET_PCT` | 0.50 | Close at 50% of credit captured |
| `OPTIONS_CSP_MONITOR_ENABLED` | True | Non-skippable background CSP risk monitor |
| `OPTIONS_CSP_MONITOR_STRIKE_BREACH_PCT` | 0.05 | Close if underlying drops 5%+ below strike |
| `OPTIONS_CSP_MONITOR_GAP_FROM_ENTRY_PCT` | 0.15 | Close on 15%+ gap crash from entry |

---

### Congress Conviction Sizing

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `CONGRESS_CONVICTION_SIZING_BOOST` | 1.3 | 1.3× sizing boost for backtest-proven politicians (55%+ WR at 30d) |
| `CONGRESS_CONVICTION_MIN_SOURCES` | 2 | Min disclosure sources to qualify |

---

### Disabled Strategies (NEVER enable without V5 backtest proof)

| Strategy | Switch | Status | Reason |
|----------|--------|--------|--------|
| Clenow Exp Regression | `CLENOW_ENABLED` | False | Fake backtest. Real $ loss risk. |
| Smart Money Flow Index | `SMFI_ENABLED` | False | Fake backtest. |
| Minervini SEPA | `MINERVINI_ENABLED` | False | Fake backtest. |
| Connors RSI | `CONNORS_RSI_ENABLED` | False | Augmented MRVL at −6.74% on real $. |
| Momentum Fade | `MOMENTUM_FADE_ENABLED_TRADIER` | False | Zero impact (0.00 Sharpe delta). |
| ATR Trail | `ATR_TRAIL_ENABLED_TRADIER` | False | #1 stock PnL destroyer (−2557% cumulative). NEVER re-enable. |
| Bounce Top Exit | `BOUNCE_TOP_EXIT_ENABLED` | False | Percentage stop in disguise. KILLED 2026-03-30. |
| SBA (stock averaging) | `SBA_ENABLED_TRADIER` | False | DEAD (no wiring found 20260416). |

---

## execute_trade_wrapper in ez_manage.py

### Location
`ez_manage.py` — search for `def execute_trade_wrapper` or `async def execute_trade_wrapper`.

### Purpose
**The only approved gate for all order placement**. Every open, close, augment, hedge, and reduce MUST go through `execute_now()` which calls this wrapper. No `futures_create_order` or REST calls outside this path.

### Key Guards (5 layers)

1. **WRAPPER_DUP_ENTRY_BLOCK_900s**: Blocks duplicate entries for the same position key within 900 seconds. Prevents the 2026-04-20 BIGTIME 11× open disaster.

2. **HEDGE_ONE_ENTRY_ONLY**: For hedge entries, only one open allowed per position key per cycle. Blocks hedge-of-hedge cascades.

3. **Mode check**: Validates that the config file matches the trading mode (`crypto` vs `tradier`). If mismatched, logs `MODE_CONFIG_MISMATCH_SKIP` and returns without placing order.

4. **LS ratio gate**: Checks L/S ratio balance before opening new positions. Blocks opens that would push ratio beyond `LS_RATIO_HARD_MAX` or below `LS_RATIO_HARD_MIN`.

5. **Position size cap**: Enforces `MAX_POSITION_SIZE` and `MAX_ORDER_VALUE` hard caps before any order reaches the exchange.

### Critical Rules
- **NEVER** add `not is_hedge` bypasses — guards apply to ALL callers including hedges
- **NEVER** bypass this for momentum or any strategy — MOMENTUM_RIDER was PERMANENTLY DISABLED for this reason
- All reason strings are for LOGGING ONLY — they do not change gate behavior
- `persist_hedge_record` MUST be called after any execute_now for a hedge

---

## Pool Sharpe Definition (CLAUDE.md — NON-NEGOTIABLE)

**`pool_sharpe = mean(all_trade_returns) / std(all_trade_returns)`**

- `all_trade_returns` = per-trade P&L % across ALL symbols pooled into one array
- Each trade contributes exactly ONE return to the array (no weighting by symbol)
- NEVER annualize by `sqrt(N)` or `sqrt(trades_per_year)` — that is banned
- Open losing positions at end-of-test MUST be marked-to-market (included as negative)
- Minimum valid sample: ≥48 symbols (crypto) / ≥100 symbols (stocks), >1 year, pool-averaged

**B&H Baselines (measured 2026-04-21):**
- Crypto (50 sym, 2022-2026): accumulated = -3,623% (bear market start)
- Tradier (114 sym, 2024-2026): accumulated = +7,720.55% (bull market)
- 10x targets: Crypto = +3,623%, Tradier = +77,205%
