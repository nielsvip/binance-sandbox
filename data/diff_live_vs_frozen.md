# Live vs Frozen (READONLY_LATEST) Drift Report

## File-level drift

| File | Drift | Live md5 | Frozen md5 | Live size | Frozen size |
|---|---|---|---|---|---|
| `config.py` | 🔴 DRIFT | `7c5bdd7c57` | `6007d598a2` | 180053 | 96533 |
| `config_tradier.py` | 🔴 DRIFT | `da113dbede` | `cde52bed8b` | 153965 | 46695 |
| `ez_manage.py` | 🔴 DRIFT | `feb702eaa7` | `333eb615f5` | 1630368 | 1606549 |
| `ez_positions_quick.py` | 🔴 DRIFT | `7fc571f5c7` | `9ed37d4c03` | 1099145 | 837186 |
| `ez_positions_service.py` | 🔴 DRIFT | `021fa8dd24` | `e4a966e206` | 798510 | 793892 |
| `ez_indicators.py` | 🔴 DRIFT | `5775d44302` | `bc8881a3e4` | 222924 | 219403 |
| `tradier_manage.py` | 🔴 DRIFT | `e23677995b` | `6783e2ccca` | 750595 | 630288 |
| `tradier_indicators.py` | 🔴 DRIFT | `e2c19680ef` | `6af40ec88b` | 138250 | 136949 |
| `utils.py` | 🔴 DRIFT | `029eb8d484` | `2ddefc0c5d` | 212429 | 210130 |
| `backtest_v8_engine.py` | 🔴 DRIFT | `9fc9ee67a1` | `11b0d7b29e` | 157787 | 47362 |
| `backtest_v8_precompute.py` | 🔴 DRIFT | `50c71a6479` | `e43a96087b` | 30693 | 23684 |

## `config.py` — constant drift

Live defines **1165** constants; frozen defines **606**.  CHANGED: **11**, ADDED: **559**, REMOVED: **0**.

### CHANGED (value differs between live and frozen)

| Name | Live | Frozen |
|---|---|---|
| `Config.HEDGE_ACCOUNTS` | `['ang', 'fin', 'men', 'flz', 'inf']` | `['ang', 'inf', 'fin', 'men', 'flz']` |
| `Config.MAX_POSITION_SIZE_BTC` | `2000.0` | `20.0` |
| `Config.MIN_POSITION_SIZE` | `1.0` | `0.9` |
| `Config.OBLIGATORY_HEDGE_MIN_LOSS_PCT` | `-0.25` | `-0.5` |
| `Config.OBLIGATORY_HEDGE_PCT` | `1.0` | `0.0` |
| `Config.SCALP_ACCOUNTS` | `[]` | `['ang', 'men']` |
| `Config.SCALP_MODE` | `True` | `False` |
| `Config.START_POSITION_SIZE` | `9.0` | `18.0` |
| `Config.STOCH_CROSS_3M_EXIT_ENABLED` | `False` | `True` |
| `Config.STRICT_NO_LOSS_ACCOUNTS` | `['ang', 'inf', 'flz', 'men', 'fin']` | `[]` |
| `Config.TREND_ACCOUNTS` | `'<expr:field(default_factory=lambda: [])>'` | `"<expr:field(default_factory=lambda: ['flz'])>"` |

### ADDED to live (559 new names, not in frozen)

- `Config.ABLATION_DISABLE_AGGRESSIVE_HEDGE` = `False`
- `Config.ABLATION_DISABLE_AUGMENTATION` = `False`
- `Config.ABLATION_DISABLE_CHECK_NOLOSS` = `False`
- `Config.ABLATION_DISABLE_DC_BREACH_REDUCE` = `False`
- `Config.ABLATION_DISABLE_ENTRY_LEADERBOARD` = `False`
- `Config.ABLATION_DISABLE_ENTRY_RANKING` = `True`
- `Config.ABLATION_DISABLE_ENTRY_REVERSAL` = `False`
- `Config.ABLATION_DISABLE_ENTRY_TECHNICAL` = `True`
- `Config.ABLATION_DISABLE_FAST_RISER` = `False`
- `Config.ABLATION_DISABLE_HEDGE` = `False`
- `Config.ABLATION_DISABLE_HIGH_GAIN_AUGMENT` = `False`
- `Config.ABLATION_DISABLE_PERIODIC_REENTRY` = `False`
- `Config.ABLATION_DISABLE_QUICK_ENTRY` = `False`
- `Config.ABLATION_DISABLE_QUICK_EXIT` = `False`
- `Config.ABLATION_DISABLE_RATIO_REBALANCE` = `False`
- `Config.ABLATION_DISABLE_REENTRY` = `False`
- `Config.ABLATION_DISABLE_REENTRY_ENFORCE` = `False`
- `Config.ABLATION_DISABLE_SCALP_GUARD` = `False`
- `Config.ABLATION_DISABLE_SPIKE_FADE_EXIT` = `False`
- `Config.ADAPTIVE_REGIME_DC_BREAKDOWN_THRESHOLD` = `0.1`
- `Config.ADAPTIVE_REGIME_DC_BREAKOUT_THRESHOLD` = `0.9`
- `Config.ADAPTIVE_REGIME_DECAY_HALFLIFE_H` = `24.0`
- `Config.ADAPTIVE_REGIME_ENABLED` = `True`
- `Config.ADAPTIVE_REGIME_HEAT_TRIGGER` = `30.0`
- `Config.ADAPTIVE_REGIME_LOOKBACK_DAYS` = `7`
- `Config.ADAPTIVE_REGIME_MIN_SIGNALS` = `5`
- `Config.ADAPTIVE_REGIME_NPZ_CACHE_HOURS` = `4.0`
- `Config.ADAPTIVE_REGIME_PAPER` = `True`
- `Config.ADAPTIVE_REGIME_SHARPE_FLOOR` = `0.0`
- `Config.ASYMMETRIC_LOSER_MIN_AGE_SECONDS` = `540`
- `Config.ASYMMETRIC_STOPS_ENABLED` = `False`
- `Config.ASYMMETRIC_WINNER_GAIN_PCT` = `1.5`
- `Config.AUGMENT_BLOWPAST_ENABLED` = `True`
- `Config.AUGMENT_HTF_TREND_ENABLED` = `True`
- `Config.AUGMENT_WT_3TF_ENABLED` = `True`
- `Config.AUGMENT_WT_CROSS_ENABLED` = `True`
- `Config.BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE` = `0.02`
- `Config.BOUNCE_AUGMENT_ENABLED` = `True`
- `Config.BOUNCE_AUGMENT_K_D_CROSSING_UP` = `True`
- `Config.BOUNCE_AUGMENT_K_D_THRESHOLD` = `20.0`
- `Config.BOUNCE_AUGMENT_MIN_LOSS_PCT` = `-0.5`
- `Config.BOUNCE_AUGMENT_PAPER` = `True`
- `Config.BREAKEVEN_DC_LOW4_ENABLED` = `True`
- `Config.BREAKEVEN_GRACE_MINUTES` = `5.0`
- `Config.BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE` = `-0.1`
- `Config.BREAKOUT_MULTI_LUNG_COMPOSITE_INHALE` = `0.2`
- `Config.BREAKOUT_MULTI_LUNG_COOLDOWN_BARS` = `4`
- `Config.BREAKOUT_MULTI_LUNG_ENABLED` = `False`
- `Config.BREAKOUT_MULTI_LUNG_MODE` = `'AUGMENT'`
- `Config.BREAKOUT_MULTI_LUNG_SLOW_LUNG_OVERRIDE` = `0.15`
- `Config.BREAKOUT_MULTI_LUNG_TIER` = `'CRYPTO'`
- `Config.BREAKOUT_TF_SIZE_CAP_MULT` = `5.0`
- `Config.BREAKOUT_TF_SIZE_ENABLED` = `True`
- `Config.BREAKOUT_TF_SIZE_MULT_15M` = `1.0`
- `Config.BREAKOUT_TF_SIZE_MULT_1H` = `2.0`
- `Config.BREAKOUT_TF_SIZE_MULT_3M` = `0.5`
- `Config.BREAKOUT_TF_SIZE_MULT_4H` = `3.0`
- `Config.BREAKOUT_TF_SIZE_MULT_D` = `4.0`
- `Config.CIRCUIT_BREAKER_ACCOUNT_HALT_MIN` = `60`
- `Config.CIRCUIT_BREAKER_ACCOUNT_LOSSES` = `5`
- `Config.CIRCUIT_BREAKER_ENABLED` = `False`
- `Config.CIRCUIT_BREAKER_SYMBOL_HALT_MIN` = `30`
- `Config.CIRCUIT_BREAKER_SYMBOL_LOSSES` = `3`
- `Config.CRASH_MULT_GRADIENT_ENABLED` = `False`
- `Config.CRASH_MULT_GRADIENT_MAX` = `2.5`
- `Config.CRYPTO_FH_MOMENTUM_DC_CONFIRM` = `True`
- `Config.CRYPTO_FH_MOMENTUM_DC_MAX_LONG` = `0.5`
- `Config.CRYPTO_FH_MOMENTUM_ENABLED` = `True`
- `Config.CRYPTO_FH_MOMENTUM_MAX_POSITIONS` = `4`
- `Config.CRYPTO_FH_MOMENTUM_MIN_MOVE_PCT` = `0.5`
- `Config.CRYPTO_FH_MOMENTUM_POSITION_SIZE_MULT` = `1.0`
- `Config.CT_15M_MOMENTUM_GATE_ENABLED` = `False`
- `Config.CT_CHOP_4H_GATE_ENABLED` = `False`
- `Config.CT_CHOP_4H_MAX` = `50.0`
- `Config.CT_DC_CROSSOVER_SKIP_ENABLED` = `True`
- `Config.CT_MFI_15M_LONG_MIN` = `45.0`
- `Config.CT_MFI_15M_SHORT_MAX` = `55.0`
- `Config.CT_REL_VOL_MIN` = `1.3`
- `Config.CT_STOCH_K_15M_LONG_MIN` = `45.0`
- `Config.CT_STOCH_K_15M_SHORT_MAX` = `55.0`
- `Config.CT_VOLUME_SURGE_GATE_ENABLED` = `False`
- `Config.CT_WT_VELOCITY_1H_MIN` = `6.0`
- `Config.CT_WT_VELOCITY_GATE_ENABLED` = `True`
- `Config.DC_HOPELESS_EXIT_ENABLED` = `True`
- `Config.DC_HOPELESS_EXIT_MIN_AGE_S` = `900`
- `Config.DC_MOMENT_ENABLED` = `True`
- `Config.DC_MOMENT_OPPOSITE_PENALTY` = `-15.0`
- `Config.DC_MOMENT_STRONG_BONUS` = `10.0`
- `Config.DC_MOMENT_STRONG_THRESHOLD` = `40.0`
- `Config.DC_RECOVERY_EXIT_DISABLED_ACCOUNTS` = `"<expr:field(default_factory=lambda: ['inf'])>"`
- `Config.DC_RECOVERY_EXIT_ENABLED` = `False`
- `Config.DC_RECOVERY_EXIT_TOLERANCE_ATR_MULT` = `0.0`
- `Config.DC_RECOVERY_EXIT_TOLERANCE_PCT` = `0.25`
- `Config.DELTA_ACCEL_LOOKBACK` = `5`
- `Config.DELTA_ATR_ENTRY_FILTER` = `False`
- `Config.DELTA_COOLDOWN_BARS` = `120`
- `Config.DELTA_ENGINE_ENABLED` = `True`
- `Config.DELTA_ENTRY_ACCEL_THRESHOLD` = `0.0`
- `Config.DELTA_ENTRY_ENABLED` = `True`
- `Config.DELTA_ENTRY_MIN_TF` = `3`
- `Config.DELTA_ENTRY_SCORE_BONUS` = `15`
- `Config.DELTA_ENTRY_SCORE_PENALTY` = `-25`
- `Config.DELTA_ENTRY_Z_THRESHOLD` = `2.5`
- `Config.DELTA_EXIT_ACCEL_THRESHOLD` = `-0.1`
- `Config.DELTA_EXIT_DC_FLOOR` = `True`
- `Config.DELTA_EXIT_DECAY_RATIO` = `0.9`
- `Config.DELTA_EXIT_DOM_TF_ENABLED` = `True`
- `Config.DELTA_EXIT_ENABLED` = `True`
- `Config.DELTA_EXIT_MIN_HOLD` = `4`
- `Config.DELTA_EXIT_MIN_TF_LOST` = `1`
- `Config.DELTA_EXIT_OPPOSING_RATIO` = `1.5`
- `Config.DELTA_EXIT_OVERRIDE_NOLOSS` = `True`
- `Config.DELTA_EXIT_SCORE_BONUS` = `20`
- `Config.DELTA_EXIT_SPEED_DECAY` = `True`
- `Config.DELTA_EXIT_TF` = `'3m'`
- `Config.DELTA_EXIT_WT_CROSS` = `True`
- `Config.DELTA_GATE_AUGMENT` = `True`
- `Config.DELTA_GATE_BB_SQUEEZE` = `True`
- `Config.DELTA_GATE_DC_BREAKOUT` = `True`
- `Config.DELTA_GATE_DIRECTION_FAVORABLE` = `True`
- `Config.DELTA_GATE_GUARANTEED_REENTRY` = `True`
- `Config.DELTA_GATE_HEDGE_OPEN` = `False`
- `Config.DELTA_GATE_OPEN` = `True`
- `Config.DELTA_GATE_RATIO_REBALANCE` = `False`
- `Config.DELTA_GATE_REENTRY` = `True`
- `Config.DELTA_GATE_SBA` = `False`
- `Config.DELTA_GATE_STDEV_BREAKOUT` = `True`
- `Config.DELTA_GATE_VOL_SPIKE` = `True`
- `Config.DELTA_HTF_GATE` = `'4h_D'`
- `Config.DELTA_LT_ENTRY_MIN_TF` = `2`
- `Config.DELTA_LT_ENTRY_Z_THRESHOLD` = `1.5`
- `Config.DELTA_LT_EXIT_TF` = `'4h'`
- `Config.DELTA_MAX_HOLD_BARS` = `0`
- `Config.DELTA_MIN_TF_FOR_ACTION` = `2`
- `Config.DELTA_PYRAMID_ACCEL_THRESHOLD` = `0.2`
- `Config.DELTA_PYRAMID_ENABLED` = `True`
- `Config.DELTA_PYRAMID_MAX` = `8`
- `Config.DELTA_PYRAMID_MIN_BARS` = `8`
- `Config.DELTA_PYRAMID_PRICE_TOL` = `0.02`
- `Config.DELTA_PYRAMID_QTY_MULT` = `1.5`
- `Config.DELTA_REENTRY_FILTER_ENABLED` = `False`
- `Config.DELTA_REENTRY_HTF_GATE` = `'4h'`
- `Config.DELTA_REENTRY_MIN_TF` = `2`
- `Config.DELTA_REENTRY_REQUIRE_NOT_EXITING` = `True`
- `Config.DELTA_REENTRY_Z_THRESHOLD` = `1.0`
- `Config.DELTA_SCORE_WEIGHT` = `30.0`
- `Config.DELTA_SERVICE_BLEED_STOP` = `True`
- `Config.DELTA_SERVICE_REDUCE_GATE` = `True`
- `Config.DELTA_SERVICE_TRAILING_STOP` = `True`
- `Config.DELTA_SPEED_SMOOTH` = `5`
- `Config.DELTA_TF_WEIGHTS` = `None`
- `Config.DELTA_TF_Z_THRESHOLD` = `1.5`
- `Config.DELTA_Z_WINDOW` = `200`
- `Config.ENTRY_SYMGATE_ENABLED` = `True`
- `Config.EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED` = `False`
- `Config.EXIT_DC_BREACH_REDUCE_ENABLED` = `True`
- `Config.EXIT_DEAD_CODE_ENABLED` = `False`
- `Config.EXIT_DELTA_SPEED_ENABLED` = `True`
- `Config.EXIT_EMERGENCY_DC1H_ENABLED` = `False`
- `Config.EXIT_GAIN_EROSION_ENABLED` = `True`
- `Config.EXIT_HARD_MAX_LOSS_CAP_ENABLED` = `False`
- `Config.EXIT_HEDGE_LOSS_KILL_ENABLED` = `True`
- `Config.EXIT_HEDGE_ORPHAN_KILL_ENABLED` = `True`
- `Config.EXIT_KEY_LEVEL_CRASH_ENABLED` = `True`
- `Config.EXIT_MARKET_SPIKE_REDUCE_ENABLED` = `True`
- `Config.EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED` = `True`
- `Config.EXIT_PREEMPTIVE_BREAKEVEN_ENABLED` = `True`
- `Config.EXIT_STDEV_BREAKOUT_FAIL_ENABLED` = `True`
- `Config.EXIT_TREND_REVERSAL_ENABLED` = `True`
- `Config.E_1_EXIT_DELTA_THR` = `50.0`
- `Config.E_1_WT_EXIT_USE_DELTA_ENABLED` = `False`
- `Config.E_3_USE_WT_STRUCTURE_EXIT_MODE` = `0`
- `Config.HARD_BREAKEVEN_FLOOR_ENABLED` = `True`
- `Config.HARD_BREAKEVEN_MIN_PEAK_PCT` = `0.5`
- `Config.HARD_MAX_LOSS_PCT` = `-5.0`
- `Config.HEDGE_CLOSE_REMOVE_FROM_TRADEABLE` = `True`
- `Config.HEDGE_EXIT_BYPASS_NOLOSS` = `True`
- `Config.HEDGE_EXIT_DELTA_CHECK_ENABLED` = `False`
- `Config.HEDGE_EXIT_WT_TF` = `'3m'`
- `Config.HEDGE_MAX_AGE_HOURS` = `6.0`
- `Config.HEDGE_NEWBORN_DC_BREACH_ALLOWED` = `True`
- `Config.HEDGE_NEWBORN_GRACE_MINUTES` = `10.0`
- `Config.HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE` = `True`
- `Config.HEDGE_SAME_SYMBOL_PCT` = `1.0`
- `Config.HLR_BYPASS_MIN_TF_WEIGHT` = `25`
- `Config.HLR_MIN_TFS_FOR_BYPASS` = `1`
- `Config.HLR_OFF_SMA_PTS_FRAC` = `0.5`
- `Config.HLR_OFF_SMA_SZ_FRAC` = `0.7`
- `Config.HLR_PTS_15M` = `25`
- `Config.HLR_PTS_1H` = `40`
- `Config.HLR_PTS_3M` = `15`
- `Config.HLR_PTS_4H` = `60`
- `Config.HLR_PTS_D` = `90`
- `Config.HLR_PTS_W` = `130`
- `Config.HLR_RALLY_ENABLED` = `True`
- `Config.HLR_REENTRY_MAX_AGE_S` = `14400.0`
- `Config.HLR_REENTRY_MULT_1H` = `1.5`
- `Config.HLR_REENTRY_MULT_4H` = `2.0`
- `Config.HLR_REENTRY_MULT_D` = `2.5`
- `Config.HLR_REENTRY_MULT_W` = `3.0`
- … and 359 more

## `config_tradier.py` — constant drift

Live defines **1228** constants; frozen defines **390**.  CHANGED: **13**, ADDED: **839**, REMOVED: **1**.

### CHANGED (value differs between live and frozen)

| Name | Live | Frozen |
|---|---|---|
| `TradierConfig.COOLDOWN_BARS_TRADIER` | `8` | `16` |
| `TradierConfig.ENTRY_MIN_ALIGNMENT` | `10` | `8` |
| `TradierConfig.ENTRY_ZONE_LONG` | `35.0` | `25.0` |
| `TradierConfig.ENTRY_ZONE_SHORT` | `100.0` | `75.0` |
| `TradierConfig.GAP_FILL_MIN_GAP_PCT` | `0.5` | `1.0` |
| `TradierConfig.K_ZONE_ENTRY_BONUS_TRADIER` | `20` | `25` |
| `TradierConfig.MARKET_QUALITY_SCORE_ENABLED_TRADIER` | `True` | `False` |
| `TradierConfig.NOLOSS_MIN_PROFIT_PCT_TRADIER` | `3.0` | `0.3` |
| `TradierConfig.RSI_ENTRY_LONG_TRADIER` | `40.0` | `42.0` |
| `TradierConfig.TRB_NOLOSS_MIN_PROFIT_PCT` | `0.0` | `0.3` |
| `TradierConfig.TRC_NOLOSS_MIN_PROFIT_PCT` | `0.0` | `0.5` |
| `TradierConfig.WT_EXIT_MIN_TFS_TRADIER` | `5` | `1` |
| `TradierConfig.WT_EXIT_TFS_TRADIER` | `'5m+15m+1h+4h+D'` | `'1h+4h+D'` |

### REMOVED from live (present in frozen, absent in live)

- `TradierConfig.SATOSHIT_ENABLED_TRADIER` = `True`

### ADDED to live (839 new names, not in frozen)

- `TradierConfig.ABLATION_DISABLE_AGGRESSIVE_HEDGE` = `False`
- `TradierConfig.ABLATION_DISABLE_AUGMENTATION` = `False`
- `TradierConfig.ABLATION_DISABLE_CHECK_NOLOSS` = `False`
- `TradierConfig.ABLATION_DISABLE_DC_BREACH_REDUCE` = `False`
- `TradierConfig.ABLATION_DISABLE_ENTRY_LEADERBOARD` = `False`
- `TradierConfig.ABLATION_DISABLE_ENTRY_RANKING` = `True`
- `TradierConfig.ABLATION_DISABLE_ENTRY_REVERSAL` = `False`
- `TradierConfig.ABLATION_DISABLE_ENTRY_TECHNICAL` = `True`
- `TradierConfig.ABLATION_DISABLE_FAST_RISER` = `False`
- `TradierConfig.ABLATION_DISABLE_HEDGE` = `False`
- `TradierConfig.ABLATION_DISABLE_HIGH_GAIN_AUGMENT` = `False`
- `TradierConfig.ABLATION_DISABLE_PERIODIC_REENTRY` = `False`
- `TradierConfig.ABLATION_DISABLE_QUICK_ENTRY` = `False`
- `TradierConfig.ABLATION_DISABLE_QUICK_EXIT` = `False`
- `TradierConfig.ABLATION_DISABLE_RATIO_REBALANCE` = `False`
- `TradierConfig.ABLATION_DISABLE_REENTRY` = `False`
- `TradierConfig.ABLATION_DISABLE_REENTRY_ENFORCE` = `False`
- `TradierConfig.ABLATION_DISABLE_SCALP_GUARD` = `False`
- `TradierConfig.ABLATION_DISABLE_SPIKE_FADE_EXIT` = `False`
- `TradierConfig.ADAPTIVE_REGIME_DC_BREAKDOWN_THRESHOLD` = `0.1`
- `TradierConfig.ADAPTIVE_REGIME_DC_BREAKOUT_THRESHOLD` = `0.9`
- `TradierConfig.ADAPTIVE_REGIME_DECAY_HALFLIFE_H` = `24.0`
- `TradierConfig.ADAPTIVE_REGIME_ENABLED` = `True`
- `TradierConfig.ADAPTIVE_REGIME_HEAT_TRIGGER` = `30.0`
- `TradierConfig.ADAPTIVE_REGIME_LOOKBACK_DAYS` = `7`
- `TradierConfig.ADAPTIVE_REGIME_MIN_SIGNALS` = `5`
- `TradierConfig.ADAPTIVE_REGIME_NPZ_CACHE_HOURS` = `4.0`
- `TradierConfig.ADAPTIVE_REGIME_PAPER` = `True`
- `TradierConfig.ADAPTIVE_REGIME_SHARPE_FLOOR` = `0.0`
- `TradierConfig.ADX_RANGING_THRESHOLD` = `20.0`
- `TradierConfig.ADX_REGIME_FILTER_ENABLED` = `True`
- `TradierConfig.ADX_TF` = `'1h'`
- `TradierConfig.ADX_TRENDING_THRESHOLD` = `25.0`
- `TradierConfig.AGGRESSIVE_LOSS_CUT_ENABLED` = `False`
- `TradierConfig.ASYMMETRIC_LOSER_MIN_AGE_SECONDS` = `540`
- `TradierConfig.ASYMMETRIC_STOPS_ENABLED` = `False`
- `TradierConfig.ASYMMETRIC_WINNER_GAIN_PCT` = `1.5`
- `TradierConfig.ATR_ADAPTIVE_SIZING_ENABLED` = `False`
- `TradierConfig.ATR_ADAPTIVE_SIZING_TARGET_PCT` = `2.0`
- `TradierConfig.ATR_ADAPTIVE_STOP_ENABLED` = `False`
- `TradierConfig.ATR_ADAPTIVE_STOP_MULT` = `2.0`
- `TradierConfig.ATR_ADAPTIVE_STOP_TF` = `'1h'`
- `TradierConfig.ATR_LONG_WINDOW` = `100`
- `TradierConfig.AUGMENT_BLOWPAST_ENABLED` = `True`
- `TradierConfig.AUGMENT_HTF_TREND_ENABLED` = `True`
- `TradierConfig.AUGMENT_PYRAMID_ENABLED` = `True`
- `TradierConfig.AUGMENT_WT_3TF_ENABLED` = `True`
- `TradierConfig.AUGMENT_WT_CROSS_ENABLED` = `True`
- `TradierConfig.BASIS_CONDITION` = `False`
- `TradierConfig.BB_BREAKOUT_ENABLED` = `False`
- `TradierConfig.BB_BREAKOUT_SCORE` = `20`
- `TradierConfig.BB_BREAKOUT_TF` = `'1h'`
- `TradierConfig.BB_ENTRY_LONG_THRESHOLD` = `-0.2`
- `TradierConfig.BB_ENTRY_SHORT_THRESHOLD` = `1.0`
- `TradierConfig.BB_RECOVERY_EXIT_ENABLED_TRADIER` = `False`
- `TradierConfig.BB_RECOVERY_EXIT_TOLERANCE_ATR_MULT_TRADIER` = `0.0`
- `TradierConfig.BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER` = `0.3`
- `TradierConfig.BB_RSI_STOCH_SCALP_ENABLED` = `False`
- `TradierConfig.BB_RSI_STOCH_SCALP_SCORE` = `25`
- `TradierConfig.BB_SQUEEZE_COOLDOWN` = `300.0`
- `TradierConfig.BB_SQUEEZE_ENABLED` = `True`
- `TradierConfig.BB_SQUEEZE_ENTRY_ENABLED` = `True`
- `TradierConfig.BB_SQUEEZE_MIN_ALIGNMENT` = `10`
- `TradierConfig.BB_SQUEEZE_THRESHOLD_15M` = `0.025`
- `TradierConfig.BB_SQUEEZE_THRESHOLD_1H` = `0.03`
- `TradierConfig.BB_SQUEEZE_WIDTH_PERCENTILE` = `0.2`
- `TradierConfig.BINANCE_API_BASE` = `'https://fapi.binance.com'`
- `TradierConfig.BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE` = `0.02`
- `TradierConfig.BOUNCE_AUGMENT_ENABLED` = `True`
- `TradierConfig.BOUNCE_AUGMENT_K_D_CROSSING_UP` = `True`
- `TradierConfig.BOUNCE_AUGMENT_K_D_THRESHOLD` = `20.0`
- `TradierConfig.BOUNCE_AUGMENT_MIN_LOSS_PCT` = `-0.5`
- `TradierConfig.BOUNCE_AUGMENT_PAPER` = `True`
- `TradierConfig.BREAKEVEN_DC_LOW4_ENABLED` = `True`
- `TradierConfig.BREAKEVEN_GRACE_MINUTES` = `15.0`
- `TradierConfig.BREAKOUT_GUARD_LOSS_THRESHOLD` = `-999.0`
- `TradierConfig.BREAKOUT_GUARD_MOMENTUM_CHECK_ENABLED` = `False`
- `TradierConfig.BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE` = `-0.1`
- `TradierConfig.BREAKOUT_MULTI_LUNG_COMPOSITE_INHALE` = `0.2`
- `TradierConfig.BREAKOUT_MULTI_LUNG_COOLDOWN_BARS` = `8`
- `TradierConfig.BREAKOUT_MULTI_LUNG_ENABLED` = `False`
- `TradierConfig.BREAKOUT_MULTI_LUNG_MODE` = `'AUGMENT'`
- `TradierConfig.BREAKOUT_MULTI_LUNG_SLOW_LUNG_OVERRIDE` = `0.15`
- `TradierConfig.BREAKOUT_MULTI_LUNG_TIER` = `'STOCK'`
- `TradierConfig.CHECK_INTERVAL` = `3.0`
- `TradierConfig.CHOP_RANGING_THRESHOLD` = `61.8`
- `TradierConfig.CHOP_TRENDING_THRESHOLD` = `38.2`
- `TradierConfig.CIRCUIT_BREAKER_ACCOUNT_HALT_MIN` = `60`
- `TradierConfig.CIRCUIT_BREAKER_ACCOUNT_LOSSES` = `5`
- `TradierConfig.CIRCUIT_BREAKER_COOLDOWN` = `60`
- `TradierConfig.CIRCUIT_BREAKER_ENABLED` = `False`
- `TradierConfig.CIRCUIT_BREAKER_SYMBOL_HALT_MIN` = `30`
- `TradierConfig.CIRCUIT_BREAKER_SYMBOL_LOSSES` = `3`
- `TradierConfig.CONGRESS_CONVICTION_MIN_SOURCES` = `2`
- `TradierConfig.CONGRESS_CONVICTION_SIZING_BOOST` = `1.3`
- `TradierConfig.CRYPTO_FH_MOMENTUM_DC_CONFIRM` = `True`
- `TradierConfig.CRYPTO_FH_MOMENTUM_DC_MAX_LONG` = `0.5`
- `TradierConfig.CRYPTO_FH_MOMENTUM_ENABLED` = `True`
- `TradierConfig.CRYPTO_FH_MOMENTUM_MAX_POSITIONS` = `4`
- `TradierConfig.CRYPTO_FH_MOMENTUM_MIN_MOVE_PCT` = `0.5`
- `TradierConfig.CRYPTO_FH_MOMENTUM_POSITION_SIZE_MULT` = `1.0`
- `TradierConfig.CRYPTO_SPIKE_FADE_COOLDOWN_SEC` = `540.0`
- `TradierConfig.CRYPTO_SPIKE_FADE_ENABLED` = `True`
- `TradierConfig.CRYPTO_SPIKE_FADE_K_EXHAUSTION` = `80.0`
- `TradierConfig.CRYPTO_SPIKE_FADE_LOOKBACK_BARS` = `3`
- `TradierConfig.CRYPTO_SPIKE_FADE_MAX_POSITIONS` = `6`
- `TradierConfig.CRYPTO_SPIKE_FADE_THRESHOLD_PCT` = `10.0`
- `TradierConfig.CT_15M_MOMENTUM_GATE_ENABLED` = `False`
- `TradierConfig.CT_CHOP_4H_GATE_ENABLED` = `False`
- `TradierConfig.CT_CHOP_4H_MAX` = `50.0`
- `TradierConfig.CT_DC_CROSSOVER_SKIP_ENABLED` = `True`
- `TradierConfig.CT_MFI_15M_LONG_MIN` = `45.0`
- `TradierConfig.CT_MFI_15M_SHORT_MAX` = `55.0`
- `TradierConfig.CT_REL_VOL_MIN` = `1.3`
- `TradierConfig.CT_STOCH_K_15M_LONG_MIN` = `45.0`
- `TradierConfig.CT_STOCH_K_15M_SHORT_MAX` = `55.0`
- `TradierConfig.CT_VOLUME_SURGE_GATE_ENABLED` = `False`
- `TradierConfig.CT_WT_VELOCITY_1H_MIN` = `0.0`
- `TradierConfig.CT_WT_VELOCITY_GATE_ENABLED` = `True`
- `TradierConfig.CYCLE_TP_CONDITIONAL_EXIT` = `0.003`
- `TradierConfig.CYCLE_TP_PCT` = `0.6`
- `TradierConfig.CYCLE_TP_TIERED_ENABLED` = `True`
- `TradierConfig.CYCLE_TP_TIERED_FRAC` = `0.25`
- `TradierConfig.DATA_READY_TIMEOUT_SECONDS` = `20`
- `TradierConfig.DAYS_PLOT` = `20`
- `TradierConfig.DC_BREAKOUT_ENTRY_ENABLED` = `True`
- `TradierConfig.DC_BREAKOUT_SCORE` = `15`
- `TradierConfig.DC_BREAKOUT_TF` = `'1h'`
- `TradierConfig.DC_EDGE_SIZING_ENABLED` = `True`
- `TradierConfig.DC_EDGE_SIZING_MAX_MULT` = `3.0`
- `TradierConfig.DC_EDGE_SIZING_MIN_MULT` = `1.0`
- `TradierConfig.DC_EDGE_SIZING_PERIOD` = `20`
- `TradierConfig.DC_ENTRY_VETO_ENABLED_TRADIER` = `False`
- `TradierConfig.DC_RECOVERY_EXIT_ENABLED` = `False`
- `TradierConfig.DC_RECOVERY_EXIT_TOLERANCE_ATR_MULT` = `0.0`
- `TradierConfig.DC_RECOVERY_EXIT_TOLERANCE_PCT` = `0.25`
- `TradierConfig.DC_WIDTH_CAP_MULT` = `10.0`
- `TradierConfig.DC_WIDTH_MAX_MULT` = `5.0`
- `TradierConfig.DC_WIDTH_SIZING_ENABLED` = `True`
- `TradierConfig.DELTA_ACCEL_LOOKBACK` = `5`
- `TradierConfig.DELTA_ATR_ENTRY_FILTER` = `True`
- `TradierConfig.DELTA_COOLDOWN_BARS` = `60`
- `TradierConfig.DELTA_ENGINE_ENABLED` = `True`
- `TradierConfig.DELTA_ENTRY_ACCEL_THRESHOLD` = `0.3`
- `TradierConfig.DELTA_ENTRY_ENABLED` = `False`
- `TradierConfig.DELTA_ENTRY_MIN_TF` = `3`
- `TradierConfig.DELTA_ENTRY_SCORE_BONUS` = `15`
- `TradierConfig.DELTA_ENTRY_SCORE_PENALTY` = `-25`
- `TradierConfig.DELTA_ENTRY_Z_THRESHOLD` = `2.5`
- `TradierConfig.DELTA_EXIT_ACCEL_THRESHOLD` = `-0.1`
- `TradierConfig.DELTA_EXIT_DC_FLOOR` = `True`
- `TradierConfig.DELTA_EXIT_DECAY_RATIO` = `0.3`
- `TradierConfig.DELTA_EXIT_DOM_TF_ENABLED` = `True`
- `TradierConfig.DELTA_EXIT_ENABLED` = `True`
- `TradierConfig.DELTA_EXIT_MIN_HOLD` = `4`
- `TradierConfig.DELTA_EXIT_MIN_TF_LOST` = `2`
- `TradierConfig.DELTA_EXIT_OPPOSING_RATIO` = `1.5`
- `TradierConfig.DELTA_EXIT_OVERRIDE_NOLOSS` = `True`
- `TradierConfig.DELTA_EXIT_SCORE_BONUS` = `20`
- `TradierConfig.DELTA_EXIT_SPEED_DECAY` = `True`
- `TradierConfig.DELTA_EXIT_TF` = `'15m'`
- `TradierConfig.DELTA_EXIT_TYPE` = `'speed_decay'`
- `TradierConfig.DELTA_EXIT_WT_CROSS` = `True`
- `TradierConfig.DELTA_GATE_AUGMENT` = `True`
- `TradierConfig.DELTA_GATE_BB_SQUEEZE` = `True`
- `TradierConfig.DELTA_GATE_DC_BREAKOUT` = `True`
- `TradierConfig.DELTA_GATE_GUARANTEED_REENTRY` = `True`
- `TradierConfig.DELTA_GATE_HEDGE_OPEN` = `False`
- `TradierConfig.DELTA_GATE_OPEN` = `True`
- `TradierConfig.DELTA_GATE_RATIO_REBALANCE` = `False`
- `TradierConfig.DELTA_GATE_REENTRY` = `True`
- `TradierConfig.DELTA_GATE_SBA` = `False`
- `TradierConfig.DELTA_GATE_STDEV_BREAKOUT` = `True`
- `TradierConfig.DELTA_GATE_VOL_SPIKE` = `True`
- `TradierConfig.DELTA_HTF_GATE` = `'4h'`
- `TradierConfig.DELTA_LT_COOLDOWN_BARS` = `120`
- `TradierConfig.DELTA_LT_ENTRY_ACCEL_THRESHOLD` = `0.0`
- `TradierConfig.DELTA_LT_ENTRY_MIN_TF` = `2`
- `TradierConfig.DELTA_LT_ENTRY_Z_THRESHOLD` = `2.0`
- `TradierConfig.DELTA_LT_EXIT_SPEED_PCT` = `50`
- `TradierConfig.DELTA_LT_EXIT_TF` = `'4h'`
- `TradierConfig.DELTA_LT_EXIT_TYPE` = `'combined_wt_speed'`
- `TradierConfig.DELTA_LT_HTF_GATE` = `'4h_D'`
- `TradierConfig.DELTA_MAX_HOLD_BARS` = `0`
- `TradierConfig.DELTA_MIN_TF_FOR_ACTION` = `2`
- `TradierConfig.DELTA_OPTIONS_COOLDOWN` = `120`
- `TradierConfig.DELTA_OPTIONS_ENTRY_Z` = `3.0`
- `TradierConfig.DELTA_OPTIONS_EXIT_TYPE` = `'giveback'`
- `TradierConfig.DELTA_OPTIONS_GIVEBACK_PCT` = `30.0`
- `TradierConfig.DELTA_OPTIONS_HTF_GATE` = `'4h_D'`
- `TradierConfig.DELTA_OPTIONS_MAX_HOLD` = `240`
- `TradierConfig.DELTA_PYRAMID_ACCEL_THRESHOLD` = `0.2`
- `TradierConfig.DELTA_PYRAMID_ENABLED` = `False`
- `TradierConfig.DELTA_PYRAMID_MAX` = `8`
- `TradierConfig.DELTA_PYRAMID_MIN_BARS` = `8`
- `TradierConfig.DELTA_PYRAMID_PRICE_TOL` = `0.02`
- `TradierConfig.DELTA_PYRAMID_QTY_MULT` = `1.5`
- `TradierConfig.DELTA_REENTRY_HTF_GATE` = `'4h'`
- `TradierConfig.DELTA_REENTRY_MIN_TF` = `2`
- `TradierConfig.DELTA_REENTRY_REQUIRE_NOT_EXITING` = `True`
- … and 639 more
