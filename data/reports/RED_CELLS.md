# RED CELLS — knobs that never moved the simulation

A red cell is a WIRING fact, not a result: the trade list came back identical to baseline, so the code never read the knob. Sweeping its values can never produce a number — fix the wiring, then it is re-measured automatically.

- **GLOBAL_ONLY**: 100
- **CRYPTO_ONLY**: 30
- **NO_READ_SITE**: 51
- **GATED_OFF**: 61
- **READ_OK**: 45

**GLOBAL_ONLY is the one-line mechanical fix**: `getattr(config, X)` -> `_cfg(X, default, account_key, symbol, position_side)`.

| knob | verdict | system | keys | cells | site | why |
|---|---|---|---|---|---|---|
| BOUNCE_REENTRY_K_RESET_SHORT_TRADIER | GLOBAL_ONLY | STOCKS | 8 | 38 | tradier_manage.py:8785 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| CONVICTION_SHORT_THRESHOLD | GLOBAL_ONLY | STOCKS | 8 | 38 | tradier_manage.py:13499 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_K_EXHAUSTED_SHORT | GLOBAL_ONLY | STOCKS | 8 | 36 | tradier_manage.py:17640 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_SHORT_BUDGET | GLOBAL_ONLY | STOCKS | 8 | 34 | tradier_manage.py:9085 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DG_DAILY_GAIN_BLOCK_SHORT_PCT | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:11416 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DG_MOMENTUM_BLOCK_RSI15M_FOR_SHORT | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:11462 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DG_MOMENTUM_BLOCK_RSI1H_FOR_SHORT | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:11465 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DG_SMA200_SHORT_BYPASS | GLOBAL_ONLY | STOCKS | 6 | 12 | tradier_manage.py:11411 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| FUNDING_GATE_PC_RATIO_SHORT_MIN | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:12184 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| LR_PCTB_D_SHORT_THRESHOLD | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:13516 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| REENTRY_STOCH_K_MIN_SHORT | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:8682 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| RSI2_EXIT_THRESHOLD_SHORT | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:581 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| BOUNCE_REENTRY_K_RESET_LONG_TRADIER | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:8784 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_K_EXHAUSTED_LONG | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:17639 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_LONG_BUDGET | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:9085 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DG_DAILY_LOSS_BLOCK_LONG_PCT | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:11417 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DG_MOMENTUM_BLOCK_RSI15M_FOR_LONG | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:11468 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DG_MOMENTUM_BLOCK_RSI1H_FOR_LONG | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:11471 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| FH_MOMENTUM_DC_MAX_LONG | GLOBAL_ONLY | BOTH | 6 | 30 | tradier_manage.py:563 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| FUNDING_GATE_PC_RATIO_LONG_MAX | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:12183 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| REENTRY_STOCH_K_MAX_LONG | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:8681 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| RSI2_EXIT_THRESHOLD_LONG | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:580 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| RSI_ENTRY_LONG_TRADIER | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:582 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| SCALP_LONG_BUDGET | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:9085 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| SMA200_DIST_LONG_THRESHOLD | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:13181 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| SMA200_DIST_LONG_THRESHOLD_4H | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:13181 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| SWING_LONG_BUDGET | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:9085 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| TF_ALIGNMENT_MIN_LONG | GLOBAL_ONLY | STOCKS | 6 | 30 | tradier_manage.py:11115 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| RSI_ENTRY_SHORT_TRADIER | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:583 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| SCALP_SHORT_BUDGET | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:9085 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| SWING_SHORT_BUDGET | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:9085 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| TF_ALIGNMENT_MIN_SHORT | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:11116 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| TRADIER_RSI_SHORT_15M | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:5984 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| TRADIER_RSI_SHORT_1H | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:5985 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| TRADIER_RSI_SHORT_RVOL_15M | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:5986 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| TRADIER_RSI_SHORT_RVOL_1H | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:5961 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| WT_CROSSUNDER_15M_SHORT | GLOBAL_ONLY | STOCKS | 4 | 8 | tradier_manage.py:13459 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| WT_DC_ENTRY_K5M_MIN_SHORT | GLOBAL_ONLY | STOCKS | 4 | 12 | tradier_manage.py:3461 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| TRA_LONG_ONLY | GLOBAL_ONLY | STOCKS | 4 | 8 | tradier_manage.py:4588 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| WT_DC_ENTRY_K5M_MAX_LONG | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:3461 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_STOP_PCT | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:573 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| GAP_FILL_STOP_MULT | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:16201 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| SCALP_STOP_PCT | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:17353 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| WT_D_BOUNCE_DD_STOP_ENABLED | GLOBAL_ONLY | STOCKS | 4 | 8 | tradier_manage.py:3085 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:2276 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| ABLATION_DISABLE_SPIKE_FADE_EXIT | GLOBAL_ONLY | STOCKS | 4 | 8 | tradier_manage.py:515 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| BB_RECOVERY_EXIT_ENABLED_TRADIER | GLOBAL_ONLY | STOCKS | 4 | 8 | tradier_manage.py:11866 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| BB_RECOVERY_EXIT_TOLERANCE_ATR_MULT_TRADIER | GLOBAL_ONLY | STOCKS | 4 | 12 | tradier_manage.py:11884 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:11883 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DELTA_EXIT_REENTRY_COOLDOWN_MIN | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:7329 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DELTA_EXIT_REQUIRE_NONZERO_SCORE | GLOBAL_ONLY | STOCKS | 4 | 8 | tradier_manage.py:7322 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| EXIT_DC_BREACH_REDUCE_ENABLED | GLOBAL_ONLY | STOCKS | 4 | 8 | tradier_manage.py:520 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| EXIT_DELTA_SPEED_ENABLED | GLOBAL_ONLY | STOCKS | 4 | 8 | tradier_manage.py:521 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| EXIT_GAIN_EROSION_ENABLED | GLOBAL_ONLY | STOCKS | 4 | 8 | tradier_manage.py:522 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| EXIT_HTF_QUICK_TP_ENABLED | GLOBAL_ONLY | STOCKS | 4 | 8 | tradier_manage.py:524 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED | GLOBAL_ONLY | BOTH | 4 | 8 | tradier_manage.py:528 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| EXIT_STDEV_BREAKOUT_FAIL_ENABLED | GLOBAL_ONLY | STOCKS | 4 | 8 | tradier_manage.py:529 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| EXIT_TREND_REVERSAL_ENABLED | GLOBAL_ONLY | STOCKS | 4 | 8 | tradier_manage.py:540 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| GOLDEN_RULE_EXIT_MIN_IND | GLOBAL_ONLY | STOCKS | 4 | 20 | tradier_manage.py:6864 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| R2_PEAK_MIN_PCT | GLOBAL_ONLY | BOTH | 2 | 10 | tradier_manage.py:2550 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| MI_DIV_EXIT_ENABLED_TRADIER | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:7648 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| MI_EXHAUST_EXIT_ENABLED_TRADIER | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:7644 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| MI_MIN_GAIN_EXIT_TRADIER | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:7520 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| MI_STRUCT_EXIT_ENABLED_TRADIER | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:7637 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| MI_VELOCITY_EXIT_ENABLED_TRADIER | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:7652 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| MI_WAVE_EXIT_ENABLED_TRADIER | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:7666 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| RULE_B_5M_EXIT_ENABLED | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:7802 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| RZ_DIV_EXIT_ENABLED | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:9240 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| RZ_EXIT_ENABLED | GLOBAL_ONLY | BOTH | 2 | 4 | tradier_manage.py:7394 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| RZ_K_EXIT | GLOBAL_ONLY | BOTH | 2 | 10 | tradier_manage.py:9231 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| RZ_MFI_EXIT | GLOBAL_ONLY | BOTH | 2 | 10 | tradier_manage.py:9233 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| RZ_TWO_PHASE_EXIT_ENABLED | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:9239 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| RZ_ZSCORE_EXIT_ENABLED | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:9241 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| TRADIER_WT_EXIT_MIN_TFS_TRADIER | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:593 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| TRADIER_WT_EXIT_TFS_TRADIER | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:592 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| TRA_NO_LOSS_EXIT | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:6937 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| TRA_STRICT_EXIT_ONLY | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:6942 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| WT_DC_EXIT_STALE_MAX_S | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:7473 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| WT_EXIT_VETO_ENABLED_TRADIER | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:7499 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| REENTRY_TIER1_SIZE_MULT_TRADIER | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:8900 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| REENTRY_TIER2_MAX_MINUTES_TRADIER | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:8735 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| REENTRY_TIER2_MIN_MINUTES_TRADIER | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:8734 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| REENTRY_TIER2_PRICE_PCT_TRADIER | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:8733 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| REENTRY_TIER2_SIZE_MULT_TRADIER | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:8736 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| ABLATION_DISABLE_DC_BREACH_REDUCE | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:503 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_BREAK_GR_RETEST_TOLERANCE_PCT | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:17813 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_BUFFER | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:17633 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_ENABLED | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:570 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_MAX_HOLD_MINUTES | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:571 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_MAX_PER_SIDE | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:17696 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_MAX_POSITION_SIZE | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:17724 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| TRA_WT_DC_ENTRY_THRESHOLD | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:3457 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_PRE_CLOSE_MINUTES | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:17627 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_START_SIZE | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:9085 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_REQUIRE_1H_EXPANSION | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:572 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_TARGET_PCT | GLOBAL_ONLY | STOCKS | 2 | 10 | tradier_manage.py:574 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| DC_DAYTRADE_STOCH_FILTER | GLOBAL_ONLY | STOCKS | 2 | 4 | tradier_manage.py:17637 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| FH_MOMENTUM_DC_CONFIRM | GLOBAL_ONLY | BOTH | 2 | 4 | tradier_manage.py:562 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| HEDGE_FAILED_FALLBACK_CLOSE_ENABLED | GLOBAL_ONLY | BOTH | 1 | 2 | tradier_manage.py:1667 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| RECOVERY_AUGMENT_ONE_FIRE_PER_REDUCE | GLOBAL_ONLY | STOCKS | 1 | 2 | tradier_manage.py:8658 | read via getattr(config,...) — per-symbol overrides are IGNORED |
| CT_MFI_15M_SHORT_MAX | CRYPTO_ONLY | CRYPTO | 8 | 32 | ez_manage.py:265 | no stocks read site; wired in crypto at ez_manage.py:265 — port it |
| CT_STOCH_K_15M_SHORT_MAX | CRYPTO_ONLY | CRYPTO | 8 | 38 | ez_manage.py:260 | no stocks read site; wired in crypto at ez_manage.py:260 — port it |
| EMA_DIST_SHORT_THRESHOLD | CRYPTO_ONLY | CRYPTO | 6 | 30 | ez_manage.py:33956 | no stocks read site; wired in crypto at ez_manage.py:33956 — port it |
| MOM3_SHORT_THRESHOLD | CRYPTO_ONLY | CRYPTO | 6 | 30 | ez_manage.py:33973 | no stocks read site; wired in crypto at ez_manage.py:33973 — port it |
| MOM5_SHORT_THRESHOLD | CRYPTO_ONLY | CRYPTO | 6 | 30 | ez_manage.py:33990 | no stocks read site; wired in crypto at ez_manage.py:33990 — port it |
| RSI_ENTRY_MIN_SHORT | CRYPTO_ONLY | CRYPTO | 6 | 22 | ez_positions_quick.py:15996 | no stocks read site; wired in crypto at ez_positions_quick.py:15996 — port it |
| CT_MFI_15M_LONG_MIN | CRYPTO_ONLY | CRYPTO | 6 | 30 | ez_manage.py:254 | no stocks read site; wired in crypto at ez_manage.py:254 — port it |
| CT_STOCH_K_15M_LONG_MIN | CRYPTO_ONLY | CRYPTO | 6 | 30 | ez_manage.py:249 | no stocks read site; wired in crypto at ez_manage.py:249 — port it |
| EMA_DIST_LONG_THRESHOLD | CRYPTO_ONLY | CRYPTO | 6 | 30 | ez_manage.py:33948 | no stocks read site; wired in crypto at ez_manage.py:33948 — port it |
| MOM3_LONG_THRESHOLD | CRYPTO_ONLY | CRYPTO | 6 | 30 | ez_manage.py:33969 | no stocks read site; wired in crypto at ez_manage.py:33969 — port it |
| MOM5_LONG_THRESHOLD | CRYPTO_ONLY | CRYPTO | 6 | 30 | ez_manage.py:33986 | no stocks read site; wired in crypto at ez_manage.py:33986 — port it |
| RSI_ENTRY_MAX_LONG | CRYPTO_ONLY | CRYPTO | 6 | 30 | ez_positions_quick.py:15993 | no stocks read site; wired in crypto at ez_positions_quick.py:15993 — port it |
| ATR_ADAPTIVE_STOP_ENABLED | CRYPTO_ONLY | CRYPTO | 4 | 8 | ez_manage.py:34418 | no stocks read site; wired in crypto at ez_manage.py:34418 — port it |
| ATR_ADAPTIVE_STOP_MULT | CRYPTO_ONLY | CRYPTO | 4 | 20 | ez_manage.py:34424 | no stocks read site; wired in crypto at ez_manage.py:34424 — port it |
| ABLATION_DISABLE_QUICK_EXIT | CRYPTO_ONLY | CRYPTO | 4 | 8 | ez_positions_quick.py:13538 | no stocks read site; wired in crypto at ez_positions_quick.py:13538 — port it |
| DC_RECOVERY_EXIT_ENABLED | CRYPTO_ONLY | CRYPTO | 4 | 8 | ez_manage.py:25252 | no stocks read site; wired in crypto at ez_manage.py:25252 — port it |
| DC_RECOVERY_EXIT_TOLERANCE_ATR_MULT | CRYPTO_ONLY | CRYPTO | 4 | 12 | ez_manage.py:25284 | no stocks read site; wired in crypto at ez_manage.py:25284 — port it |
| DC_RECOVERY_EXIT_TOLERANCE_PCT | CRYPTO_ONLY | CRYPTO | 4 | 20 | ez_manage.py:25277 | no stocks read site; wired in crypto at ez_manage.py:25277 — port it |
| DELTA_REENTRY_REQUIRE_NOT_EXITING | CRYPTO_ONLY | CRYPTO | 4 | 6 | ez_manage.py:91 | no stocks read site; wired in crypto at ez_manage.py:91 — port it |
| BB_FROZEN_STOP_ENABLED | CRYPTO_ONLY | CRYPTO | 2 | 4 | ez_manage.py:39588 | no stocks read site; wired in crypto at ez_manage.py:39588 — port it |
| REENTRY_B04_DC_RETEST_ENABLED | CRYPTO_ONLY | CRYPTO | 2 | 4 | ez_positions_quick.py:16573 | no stocks read site; wired in crypto at ez_positions_quick.py:16573 — port it |
| REENTRY_B11_DC_BREAK_ENABLED | CRYPTO_ONLY | CRYPTO | 2 | 4 | ez_positions_quick.py:16587 | no stocks read site; wired in crypto at ez_positions_quick.py:16587 — port it |
| SATOSHIT_EXIT_PARTIAL_PCT | CRYPTO_ONLY | CRYPTO | 2 | 10 | ez_manage.py:43455 | no stocks read site; wired in crypto at ez_manage.py:43455 — port it |
| BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE | CRYPTO_ONLY | CRYPTO | 2 | 10 | ez_positions_quick.py:13976 | no stocks read site; wired in crypto at ez_positions_quick.py:13976 — port it |
| CT_DC_CROSSOVER_SKIP_ENABLED | CRYPTO_ONLY | CRYPTO | 2 | 4 | ez_manage.py:271 | no stocks read site; wired in crypto at ez_manage.py:271 — port it |
| DC_EDGE_SIZING_MAX_MULT | CRYPTO_ONLY | CRYPTO | 2 | 10 | ez_positions_quick.py:1095 | no stocks read site; wired in crypto at ez_positions_quick.py:1095 — port it |
| DC_EDGE_SIZING_ENABLED | CRYPTO_ONLY | CRYPTO | 2 | 4 | ez_positions_quick.py:1087 | no stocks read site; wired in crypto at ez_positions_quick.py:1087 — port it |
| DC_EDGE_SIZING_MIN_MULT | CRYPTO_ONLY | CRYPTO | 2 | 10 | ez_positions_quick.py:1094 | no stocks read site; wired in crypto at ez_positions_quick.py:1094 — port it |
| DC_WIDTH_CAP_MULT | CRYPTO_ONLY | CRYPTO | 2 | 10 | ez_positions_quick.py:1131 | no stocks read site; wired in crypto at ez_positions_quick.py:1131 — port it |
| REENTRY2_DC_BREAK_ENABLED | CRYPTO_ONLY | CRYPTO | 2 | 4 | ez_manage.py:35920 | no stocks read site; wired in crypto at ez_manage.py:35920 — port it |
| EMA20_SLOPE_SHORT_THRESHOLD_1H | NO_READ_SITE | NEITHER | 6 | 30 | - | never read in live code — needs wiring from scratch |
| MFI_FLIP_EXIT_SHORT_THRESHOLD | NO_READ_SITE | NEITHER | 6 | 30 | - | never read in live code — needs wiring from scratch |
| PYRAMID_MAX_DC_POS_15M_SHORT | NO_READ_SITE | NEITHER | 6 | 30 | - | never read in live code — needs wiring from scratch |
| MFI_FLIP_EXIT_LONG_THRESHOLD | NO_READ_SITE | NEITHER | 6 | 30 | - | never read in live code — needs wiring from scratch |
| RSI_EXIT_LONG_TRADIER | NO_READ_SITE | NEITHER | 6 | 28 | - | never read in live code — needs wiring from scratch |
| SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER | NO_READ_SITE | NEITHER | 6 | 30 | - | never read in live code — needs wiring from scratch |
| SATOSHIT_LONG_BB_PCTB_MAX | NO_READ_SITE | NEITHER | 6 | 30 | - | never read in live code — needs wiring from scratch |
| SATOSHIT_LONG_HA_STREAK_MAX | NO_READ_SITE | NEITHER | 6 | 30 | - | never read in live code — needs wiring from scratch |
| SATOSHIT_LONG_MFI_MAX_TRADIER | NO_READ_SITE | NEITHER | 6 | 30 | - | never read in live code — needs wiring from scratch |
| SATOSHIT_LONG_RSI_MAX_TRADIER | NO_READ_SITE | NEITHER | 6 | 30 | - | never read in live code — needs wiring from scratch |
| SATOSHIT_LONG_STOCH_K_MAX_TRADIER | NO_READ_SITE | NEITHER | 6 | 30 | - | never read in live code — needs wiring from scratch |
| RSI_EXIT_SHORT_TRADIER | NO_READ_SITE | NEITHER | 4 | 20 | - | never read in live code — needs wiring from scratch |
| SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER | NO_READ_SITE | NEITHER | 4 | 20 | - | never read in live code — needs wiring from scratch |
| SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER | NO_READ_SITE | NEITHER | 4 | 20 | - | never read in live code — needs wiring from scratch |
| SATOSHIT_SHORT_BB_PCTB_MIN | NO_READ_SITE | NEITHER | 4 | 20 | - | never read in live code — needs wiring from scratch |
| SATOSHIT_SHORT_HA_STREAK_MIN | NO_READ_SITE | NEITHER | 4 | 12 | - | never read in live code — needs wiring from scratch |
| SATOSHIT_SHORT_MFI_MIN_TRADIER | NO_READ_SITE | NEITHER | 4 | 20 | - | never read in live code — needs wiring from scratch |
| SATOSHIT_SHORT_RSI_MIN_TRADIER | NO_READ_SITE | NEITHER | 4 | 20 | - | never read in live code — needs wiring from scratch |
| SATOSHIT_SHORT_STOCH_K_MIN_TRADIER | NO_READ_SITE | NEITHER | 4 | 20 | - | never read in live code — needs wiring from scratch |
| DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED | NO_READ_SITE | NEITHER | 4 | 8 | - | never read in live code — needs wiring from scratch |
| DC4_STOP_GR_SCORE_MIN_IND | NO_READ_SITE | NEITHER | 4 | 20 | - | never read in live code — needs wiring from scratch |
| DC4_STOP_GR_SCORE_MIN_TFS | NO_READ_SITE | NEITHER | 4 | 20 | - | never read in live code — needs wiring from scratch |
| ATR_TRAIL_2X_EXIT_ENABLED | NO_READ_SITE | NEITHER | 4 | 8 | - | never read in live code — needs wiring from scratch |
| CYCLE_TP_CONDITIONAL_EXIT | NO_READ_SITE | NEITHER | 4 | 20 | - | never read in live code — needs wiring from scratch |
| DELTA_EXIT_DC_FLOOR | NO_READ_SITE | NEITHER | 4 | 8 | - | never read in live code — needs wiring from scratch |
| DELTA_EXIT_OVERRIDE_NOLOSS | NO_READ_SITE | NEITHER | 4 | 8 | - | never read in live code — needs wiring from scratch |
| DELTA_EXIT_WT_CROSS | NO_READ_SITE | NEITHER | 4 | 8 | - | never read in live code — needs wiring from scratch |
| EXIT_SCORER_FULL_SCORE | NO_READ_SITE | NEITHER | 4 | 20 | - | never read in live code — needs wiring from scratch |
| EXIT_SCORER_PARTIAL_SCORE | NO_READ_SITE | NEITHER | 4 | 18 | - | never read in live code — needs wiring from scratch |
| EXIT_SENTIMENT_ENABLED | NO_READ_SITE | NEITHER | 4 | 8 | - | never read in live code — needs wiring from scratch |
| MFI_FLIP_EXIT_ENABLED | NO_READ_SITE | NEITHER | 4 | 8 | - | never read in live code — needs wiring from scratch |
| MIN_HOLD_BARS_BEFORE_EXIT | NO_READ_SITE | NEITHER | 4 | 16 | - | never read in live code — needs wiring from scratch |
| BB_PULLBACK_GATE_SHORT_MIN | NO_READ_SITE | NEITHER | 2 | 10 | - | never read in live code — needs wiring from scratch |
| BB_PULLBACK_GATE_LONG_MAX | NO_READ_SITE | NEITHER | 2 | 10 | - | never read in live code — needs wiring from scratch |
| DC_LOW_FROZEN_STOP_ENABLED | NO_READ_SITE | NEITHER | 2 | 4 | - | never read in live code — needs wiring from scratch |
| MTF_GR_INVERT_DC_BB | NO_READ_SITE | NEITHER | 2 | 4 | - | never read in live code — needs wiring from scratch |
| REGIME_RANGING_WT_EXIT_VEL | NO_READ_SITE | NEITHER | 2 | 10 | - | never read in live code — needs wiring from scratch |
| REGIME_TRENDING_WT_EXIT_VEL | NO_READ_SITE | NEITHER | 2 | 10 | - | never read in live code — needs wiring from scratch |
| STOCH_CROSS_1H_EXIT_ENABLED | NO_READ_SITE | NEITHER | 2 | 4 | - | never read in live code — needs wiring from scratch |
| STOCH_CROSS_3M_EXIT_ENABLED | NO_READ_SITE | NEITHER | 2 | 4 | - | never read in live code — needs wiring from scratch |
| DD_KELLY_TIER1_PCT | NO_READ_SITE | NEITHER | 2 | 10 | - | never read in live code — needs wiring from scratch |
| DD_KELLY_TIER2_PCT | NO_READ_SITE | NEITHER | 2 | 10 | - | never read in live code — needs wiring from scratch |
| DC_EDGE_SIZING_PERIOD | NO_READ_SITE | NEITHER | 2 | 10 | - | never read in live code — needs wiring from scratch |
| PYRAMID_MIN_DC_POS_15M | NO_READ_SITE | NEITHER | 2 | 10 | - | never read in live code — needs wiring from scratch |
| DELTA_GATE_DC_BREAKOUT | NO_READ_SITE | NEITHER | 2 | 4 | - | never read in live code — needs wiring from scratch |
| REGIME_DC_ATR_RATIO_MIN | NO_READ_SITE | NEITHER | 2 | 10 | - | never read in live code — needs wiring from scratch |
| REGIME_RANGING_DC_BREAKOUT_SCORE | NO_READ_SITE | NEITHER | 2 | 6 | - | never read in live code — needs wiring from scratch |
| REGIME_TRENDING_DC_BREAKOUT_SCORE | NO_READ_SITE | NEITHER | 2 | 10 | - | never read in live code — needs wiring from scratch |
| PARTIAL_PROFIT_LOCK_SWEEP_ARM_PCT | NO_READ_SITE | NEITHER | 1 | 5 | - | never read in live code — needs wiring from scratch |
| PARTIAL_PROFIT_LOCK_SWEEP_ENABLED | NO_READ_SITE | NEITHER | 1 | 2 | - | never read in live code — needs wiring from scratch |
| PARTIAL_PROFIT_LOCK_SWEEP_GAIN_PCT | NO_READ_SITE | NEITHER | 1 | 5 | - | never read in live code — needs wiring from scratch |
| WT_3M_FORCE_OPEN_BUILD_TO_TARGET | GATED_OFF | STOCKS | 10 | 20 | tradier_manage.py:2804 | its feature's master _ENABLED is False — cannot bind until enabled |
| WT_3M_FORCE_OPEN_SIZE_USD | GATED_OFF | BOTH | 10 | 48 | tradier_manage.py:2843 | its feature's master _ENABLED is False — cannot bind until enabled |
| WT_3M_FORCE_OPEN_TF_LADDER_MULT | GATED_OFF | STOCKS | 10 | 50 | tradier_manage.py:2836 | its feature's master _ENABLED is False — cannot bind until enabled |
| WT_3M_FORCE_OPEN_USE_SMA200 | GATED_OFF | STOCKS | 8 | 14 | tradier_manage.py:2811 | its feature's master _ENABLED is False — cannot bind until enabled |
| ORB_SHORT_BUDGET | GATED_OFF | STOCKS | 6 | 30 | tradier_manage.py:9096 | its feature's master _ENABLED is False — cannot bind until enabled |
| LR_PCTB_D_LONG_ENTRY_ENABLED | GATED_OFF | BOTH | 6 | 12 | tradier_manage.py:3577 | its feature's master _ENABLED is False — cannot bind until enabled |
| LR_PCTB_D_LONG_ENTRY_THRESHOLD | GATED_OFF | BOTH | 6 | 30 | tradier_manage.py:3580 | its feature's master _ENABLED is False — cannot bind until enabled |
| MINERVINI_LONG_BUDGET | GATED_OFF | STOCKS | 6 | 30 | tradier_manage.py:9096 | its feature's master _ENABLED is False — cannot bind until enabled |
| ORB_LONG_BUDGET | GATED_OFF | STOCKS | 6 | 30 | tradier_manage.py:9096 | its feature's master _ENABLED is False — cannot bind until enabled |
| SMFI_LONG_BUDGET | GATED_OFF | STOCKS | 6 | 30 | tradier_manage.py:9096 | its feature's master _ENABLED is False — cannot bind until enabled |
| STDEV_BOUNCE_PCTB_LONG | GATED_OFF | BOTH | 6 | 28 | tradier_manage.py:531 | its feature's master _ENABLED is False — cannot bind until enabled |
| STDEV_BREAKOUT_PCTB_LONG | GATED_OFF | BOTH | 6 | 30 | tradier_manage.py:2144 | its feature's master _ENABLED is False — cannot bind until enabled |
| SMFI_SHORT_BUDGET | GATED_OFF | STOCKS | 4 | 20 | tradier_manage.py:9096 | its feature's master _ENABLED is False — cannot bind until enabled |
| STDEV_BOUNCE_PCTB_SHORT | GATED_OFF | BOTH | 4 | 20 | tradier_manage.py:532 | its feature's master _ENABLED is False — cannot bind until enabled |
| STDEV_BREAKOUT_PCTB_SHORT | GATED_OFF | BOTH | 4 | 20 | tradier_manage.py:2145 | its feature's master _ENABLED is False — cannot bind until enabled |
| TRC_ORB_SHORT_BUDGET | GATED_OFF | STOCKS | 4 | 20 | tradier_manage.py:9096 | its feature's master _ENABLED is False — cannot bind until enabled |
| TRC_SMFI_SHORT_BUDGET | GATED_OFF | STOCKS | 4 | 20 | tradier_manage.py:9096 | its feature's master _ENABLED is False — cannot bind until enabled |
| TRC_ORB_LONG_BUDGET | GATED_OFF | STOCKS | 4 | 20 | tradier_manage.py:9096 | its feature's master _ENABLED is False — cannot bind until enabled |
| TRC_SMFI_LONG_BUDGET | GATED_OFF | STOCKS | 4 | 20 | tradier_manage.py:9096 | its feature's master _ENABLED is False — cannot bind until enabled |
| ORB_STOP_MIDPOINT | GATED_OFF | STOCKS | 4 | 8 | tradier_manage.py:15444 | its feature's master _ENABLED is False — cannot bind until enabled |
| CONNORS_RSI_EXIT_THRESHOLD | GATED_OFF | STOCKS | 4 | 20 | tradier_manage.py:15799 | its feature's master _ENABLED is False — cannot bind until enabled |
| EXIT_ALGO_SCORE_ENABLED | GATED_OFF | STOCKS | 4 | 8 | tradier_manage.py:516 | its feature's master _ENABLED is False — cannot bind until enabled |
| EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED | GATED_OFF | BOTH | 4 | 8 | tradier_manage.py:517 | its feature's master _ENABLED is False — cannot bind until enabled |
| EXIT_BOUNCE_TOP_ENABLED | GATED_OFF | STOCKS | 4 | 8 | tradier_manage.py:518 | its feature's master _ENABLED is False — cannot bind until enabled |
| EXIT_CONV_FAIL_ENABLED | GATED_OFF | STOCKS | 4 | 8 | tradier_manage.py:519 | its feature's master _ENABLED is False — cannot bind until enabled |
| EXIT_HARD_DROP_5M_ENABLED | GATED_OFF | STOCKS | 4 | 8 | tradier_manage.py:523 | its feature's master _ENABLED is False — cannot bind until enabled |
| EXIT_IBS_EXHAUSTION_ENABLED | GATED_OFF | STOCKS | 4 | 8 | tradier_manage.py:525 | its feature's master _ENABLED is False — cannot bind until enabled |
| EXIT_K5M_BOUNCE_ENABLED | GATED_OFF | STOCKS | 4 | 8 | tradier_manage.py:526 | its feature's master _ENABLED is False — cannot bind until enabled |
| EXIT_MI_ENABLED | GATED_OFF | STOCKS | 4 | 8 | tradier_manage.py:527 | its feature's master _ENABLED is False — cannot bind until enabled |
| EXIT_STRUCT_BREAK_5M_ENABLED | GATED_OFF | STOCKS | 4 | 8 | tradier_manage.py:538 | its feature's master _ENABLED is False — cannot bind until enabled |
| HTF_W_REVERSAL_EXIT_TRADIER_ENABLED | GATED_OFF | STOCKS | 4 | 8 | tradier_manage.py:6870 | its feature's master _ENABLED is False — cannot bind until enabled |
| LR_BAND_SLOPE_FLIP_EXIT_ENABLED | GATED_OFF | BOTH | 4 | 8 | tradier_manage.py:6973 | its feature's master _ENABLED is False — cannot bind until enabled |
| DC_BREAK_LOW_REQUIRE_HTF_ENABLED | GATED_OFF | STOCKS | 2 | 4 | tradier_manage.py:17682 | its feature's master _ENABLED is False — cannot bind until enabled |
| BB_BREAKOUT_ENABLED | GATED_OFF | BOTH | 2 | 4 | tradier_manage.py:3436 | its feature's master _ENABLED is False — cannot bind until enabled |
| STDEV_BB_RZ_EXIT_ENABLED | GATED_OFF | STOCKS | 2 | 4 | tradier_manage.py:7543 | its feature's master _ENABLED is False — cannot bind until enabled |
| STDEV_REJECT_EXIT_ENABLED | GATED_OFF | BOTH | 2 | 4 | tradier_manage.py:534 | its feature's master _ENABLED is False — cannot bind until enabled |
| STDEV_REJECT_EXIT_RETURN | GATED_OFF | BOTH | 2 | 10 | tradier_manage.py:537 | its feature's master _ENABLED is False — cannot bind until enabled |
| STDEV_REJECT_EXIT_ZONE | GATED_OFF | BOTH | 2 | 10 | tradier_manage.py:536 | its feature's master _ENABLED is False — cannot bind until enabled |
| LR_BAND_ENTRY_R2_MIN | GATED_OFF | BOTH | 2 | 10 | tradier_manage.py:3389 | its feature's master _ENABLED is False — cannot bind until enabled |
| BREAKEVEN_DC_LOW4_ENABLED | GATED_OFF | BOTH | 2 | 4 | tradier_manage.py:7033 | its feature's master _ENABLED is False — cannot bind until enabled |
| DC_BREAK_GR_MULT_BREAKOUT | GATED_OFF | STOCKS | 2 | 10 | tradier_manage.py:17659 | its feature's master _ENABLED is False — cannot bind until enabled |
| DC_BREAK_GR_MULT_ENABLED | GATED_OFF | STOCKS | 2 | 4 | tradier_manage.py:17658 | its feature's master _ENABLED is False — cannot bind until enabled |
| DC_BREAK_GR_MULT_RETEST | GATED_OFF | STOCKS | 2 | 10 | tradier_manage.py:17821 | its feature's master _ENABLED is False — cannot bind until enabled |
| DC_TIER4_BAR_MATURITY_BLOCK | GATED_OFF | STOCKS | 2 | 10 | tradier_manage.py:8488 | its feature's master _ENABLED is False — cannot bind until enabled |
| HTF_DC_BREAKOUT_TRADIER_THRESHOLD_PCT | GATED_OFF | STOCKS | 2 | 6 | tradier_manage.py:13055 | its feature's master _ENABLED is False — cannot bind until enabled |
| LH_HL_FILTER_DC_THRESHOLD_PCT | GATED_OFF | BOTH | 2 | 10 | tradier_manage.py:13194 | its feature's master _ENABLED is False — cannot bind until enabled |
| WT_DC_ENTRY_BAR_MATURITY_BLOCK | GATED_OFF | STOCKS | 2 | 10 | tradier_manage.py:3534 | its feature's master _ENABLED is False — cannot bind until enabled |
| WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED | GATED_OFF | STOCKS | 2 | 4 | tradier_manage.py:3534 | its feature's master _ENABLED is False — cannot bind until enabled |
| BB_BREAKOUT_SCORE | GATED_OFF | BOTH | 2 | 4 | tradier_manage.py:3440 | its feature's master _ENABLED is False — cannot bind until enabled |
| DC_TIER4_BAR_MATURITY_BLOCK_ENABLED | GATED_OFF | STOCKS | 2 | 4 | tradier_manage.py:8488 | its feature's master _ENABLED is False — cannot bind until enabled |
| HTF_DC_BREAKOUT_TRADIER_ENABLED | GATED_OFF | STOCKS | 2 | 4 | tradier_manage.py:13052 | its feature's master _ENABLED is False — cannot bind until enabled |
| HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT | GATED_OFF | STOCKS | 2 | 4 | tradier_manage.py:13056 | its feature's master _ENABLED is False — cannot bind until enabled |
| DELTA_ENTRY_ACCEL_THRESHOLD | GATED_OFF | BOTH | 1 | 4 | tradier_manage.py:9217 | its feature's master _ENABLED is False — cannot bind until enabled |
| LR_BAND_HARVEST_ENABLED | GATED_OFF | BOTH | 1 | 2 | tradier_manage.py:6956 | its feature's master _ENABLED is False — cannot bind until enabled |
| LR_BAND_HARVEST_FRAC | GATED_OFF | STOCKS | 1 | 5 | tradier_manage.py:6984 | its feature's master _ENABLED is False — cannot bind until enabled |
| DELTA_ENTRY_ENABLED | GATED_OFF | BOTH | 1 | 2 | tradier_manage.py:3241 | its feature's master _ENABLED is False — cannot bind until enabled |
| DELTA_ENTRY_MIN_TF | GATED_OFF | BOTH | 1 | 5 | tradier_manage.py:9218 | its feature's master _ENABLED is False — cannot bind until enabled |
| MTF_ATR_TRAIL_ENABLED | GATED_OFF | BOTH | 1 | 2 | tradier_manage.py:2702 | its feature's master _ENABLED is False — cannot bind until enabled |
| MTF_ATR_TRAIL_MULT | GATED_OFF | BOTH | 1 | 5 | tradier_manage.py:2699 | its feature's master _ENABLED is False — cannot bind until enabled |
| DELTA_ENTRY_Z_THRESHOLD | GATED_OFF | BOTH | 1 | 4 | tradier_manage.py:9217 | its feature's master _ENABLED is False — cannot bind until enabled |
| PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER | GATED_OFF | STOCKS | 1 | 5 | tradier_manage.py:7184 | its feature's master _ENABLED is False — cannot bind until enabled |
| TRADIER_FH_MOMENTUM_DC_MAX_LONG | READ_OK | STOCKS | 6 | 30 | tradier_manage.py:563 | read correctly — deadness is real, knob genuinely does nothing here |
| TRADIER_RSI2_EXIT_THRESHOLD_SHORT | READ_OK | STOCKS | 4 | 20 | tradier_manage.py:581 | read correctly — deadness is real, knob genuinely does nothing here |
| TRADIER_RSI_ENTRY_SHORT_TRADIER | READ_OK | STOCKS | 4 | 20 | tradier_manage.py:583 | read correctly — deadness is real, knob genuinely does nothing here |
| TRADIER_RSI_SHORT_REL_VOLUME_MIN | READ_OK | STOCKS | 4 | 20 | tradier_manage.py:584 | read correctly — deadness is real, knob genuinely does nothing here |
| TRADIER_STOCH_ENTRY_SHORT_TRADIER | READ_OK | STOCKS | 4 | 20 | tradier_manage.py:588 | read correctly — deadness is real, knob genuinely does nothing here |
| TRADIER_STOCH_EXTREME_SHORT_TRADIER | READ_OK | STOCKS | 4 | 20 | tradier_manage.py:590 | read correctly — deadness is real, knob genuinely does nothing here |
| TRC_DC_DAYTRADE_SHORT_BUDGET | READ_OK | STOCKS | 4 | 20 | tradier_manage.py:9085 | read correctly — deadness is real, knob genuinely does nothing here |
| TRC_ENTRY_ZONE_SHORT | READ_OK | STOCKS | 4 | 20 | tradier_manage.py:9085 | read correctly — deadness is real, knob genuinely does nothing here |
