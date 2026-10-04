
## 2026-10-04T00:02Z · window 48.0h · 348 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 72 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `AUGMENT_BULL_KILL_ENABLED`×2, `BAND_ARROW_ENABLED`×2, `DELTA_REENTRY_FILTER_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_BOUNCE_REL_VOL_GT_1`×2, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_AT_LOSS_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `BEAR_MARKET_MODE`×1, `BREAKEVEN_GAIN_EROSION_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `COUNTER_TREND_SMA200_BYPASS_ENABLED`×1, `DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `SHORT_DC_LOW_BREAK_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `WT_15M_BOUNCE_LOW_1H_GT_PREV`×1
    - `ADAUSDC_SHORT` [mac] 43 cases — UNIVERSAL_NOLOSS_GATE T=-7.279/F=-4.468; COUNTER_TREND_ADD_BLOCK_ENABLED T=-4.468/F=-7.279; RECENT_REDUCTION_GUARD_ENABLED T=-4.468/F=-7.450
    - `HYPEUSDT_LONG` [mac] 12 cases — HTF_DIRECTION_GATE_ENABLED T=-1.163/F=-0.633; MTS_GATE_ENABLED T=-1.105/F=-0.633; HA_WICK_QUALITY_ENABLED T=-1.450/F=-0.633
    - `AIAUSDT_LONG` [mac] 7 cases — SHORT_DC_LOW_BREAK_ENABLED T=-10.782/F=-10.782; WT_15M_BOUNCE_REL_VOL_GT_1 T=-10.782/F=-10.782; WT_15M_BOUNCE_LOW_1H_GT_PREV T=-10.782/F=-10.782
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T00:20Z · window 48.0h · 348 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 73 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×4, `ALL_TF_AGAINST_CLOSE_ENABLED`×2, `BAND_ARROW_ENABLED`×2, `HTF_BULL_ENTRY_FILTER_ENABLED`×1, `VEC_REENTRY_DC4_EXITPRICE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `AUGMENT_AT_LOSS_ENABLED`×1, `AUGMENT_BULL_KILL_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `BEAR_MARKET_MODE`×1, `BREAKEVEN_GAIN_EROSION_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `COUNTER_TREND_SMA200_BYPASS_ENABLED`×1, `DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `WT_ACCEL_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1
    - `ADAUSDC_SHORT` [mac] 43 cases — UNIVERSAL_NOLOSS_GATE T=-7.279/F=-4.468; COUNTER_TREND_ADD_BLOCK_ENABLED T=-4.468/F=-7.279; RECENT_REDUCTION_GUARD_ENABLED T=-4.468/F=-7.450
    - `HYPEUSDT_LONG` [mac] 12 cases — HTF_DIRECTION_GATE_ENABLED T=-1.163/F=-0.633; MTS_GATE_ENABLED T=-1.105/F=-0.633; HA_WICK_QUALITY_ENABLED T=-1.450/F=-0.633
    - `COMPUSDT_LONG` [mac] 4 cases — WT_AGAINST_FILTER_ENABLED T=-15.630/F=-15.630; ALL_TF_AGAINST_CLOSE_ENABLED T=-15.630/F=-15.630; VEC_REENTRY_DC4_EXITPRICE_ENABLED T=-15.630/F=-15.630
    - `COMPUSDT_SHORT` [mac] 4 cases — BB_SQUEEZE_ENTRY_ENABLED T=+15.361/F=+15.361; WT_DIV_EXIT_ENABLED T=+2.458/F=+2.458; WT_15M_BOUNCE_OPEN_ENABLED T=+4.359/F=+2.458
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T00:33Z · window 48.0h · 351 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 82 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×4, `ALL_TF_AGAINST_CLOSE_ENABLED`×3, `BAND_ARROW_ENABLED`×2, `BB_SQUEEZE_EXIT_ENABLED`×2, `COUNTER_TREND_ADD_BLOCK_ENABLED`×2, `HTF_BULL_ENTRY_FILTER_ENABLED`×1, `VEC_REENTRY_DC4_EXITPRICE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `AUGMENT_AT_LOSS_ENABLED`×1, `AUGMENT_BULL_KILL_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BEAR_MARKET_MODE`×1, `BREAKEVEN_GAIN_EROSION_ENABLED`×1, `COUNTER_TREND_SMA200_BYPASS_ENABLED`×1, `DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `WT_ACCEL_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1
    - `ADAUSDC_SHORT` [mac] 43 cases — UNIVERSAL_NOLOSS_GATE T=-7.279/F=-4.468; COUNTER_TREND_ADD_BLOCK_ENABLED T=-4.468/F=-7.279; RECENT_REDUCTION_GUARD_ENABLED T=-4.468/F=-7.450
    - `ETHUSDC_SHORT` [mac] 21 cases — OI_CONFIRM_ENABLED T=-20.658/F=-21.196; UNIVERSAL_NOLOSS_GATE T=-20.596/F=-20.596; MTS_GATE_ENABLED T=-20.596/F=-21.196
    - `COMPUSDT_LONG` [mac] 4 cases — WT_AGAINST_FILTER_ENABLED T=-15.630/F=-15.630; ALL_TF_AGAINST_CLOSE_ENABLED T=-15.630/F=-15.630; VEC_REENTRY_DC4_EXITPRICE_ENABLED T=-15.630/F=-15.630
    - `COMPUSDT_SHORT` [mac] 4 cases — BB_SQUEEZE_ENTRY_ENABLED T=+15.361/F=+15.361; WT_DIV_EXIT_ENABLED T=+2.458/F=+2.458; WT_15M_BOUNCE_OPEN_ENABLED T=+4.359/F=+2.458
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T00:47Z · window 48.0h · 351 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 39 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×4, `ALL_TF_AGAINST_CLOSE_ENABLED`×2, `HTF_BULL_ENTRY_FILTER_ENABLED`×1, `VEC_REENTRY_DC4_EXITPRICE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `WT_ACCEL_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `BAND_ARROW_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1
    - `ETHUSDC_SHORT` [mac] 21 cases — OI_CONFIRM_ENABLED T=-20.658/F=-21.196; UNIVERSAL_NOLOSS_GATE T=-20.596/F=-20.596; MTS_GATE_ENABLED T=-20.596/F=-21.196
    - `COMPUSDT_LONG` [mac] 4 cases — WT_AGAINST_FILTER_ENABLED T=-15.630/F=-15.630; ALL_TF_AGAINST_CLOSE_ENABLED T=-15.630/F=-15.630; VEC_REENTRY_DC4_EXITPRICE_ENABLED T=-15.630/F=-15.630
    - `COMPUSDT_SHORT` [mac] 4 cases — BB_SQUEEZE_ENTRY_ENABLED T=+15.361/F=+15.361; WT_DIV_EXIT_ENABLED T=+2.458/F=+2.458; WT_15M_BOUNCE_OPEN_ENABLED T=+4.359/F=+2.458
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T01:03Z · window 48.0h · 356 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 44 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `ALL_TF_AGAINST_CLOSE_ENABLED`×3, `WT_ACCEL_EXIT_ENABLED`×3, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `COUNTER_TREND_ADD_BLOCK_ENABLED`×2, `GR_FILTER_VEC_ENABLED`×2, `HTF_DIRECTION_GATE_ENABLED`×2, `WT_AGAINST_FILTER_ENABLED`×2, `WT_DIV_EXIT_ENABLED`×2, `HTF_BULL_ENTRY_FILTER_ENABLED`×2, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HAIKU_ENTRY_GATE_ENABLED`×1, `VEC_REENTRY_DC4_EXITPRICE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BAND_ARROW_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1
    - `ETHUSDC_SHORT` [mac] 21 cases — OI_CONFIRM_ENABLED T=-20.658/F=-21.196; UNIVERSAL_NOLOSS_GATE T=-20.596/F=-20.596; MTS_GATE_ENABLED T=-20.596/F=-21.196
    - `NKE_SHORT` [s1] 7 cases — WT_DIV_EXIT_ENABLED T=-13.093/F=-13.093; ALL_TF_AGAINST_CLOSE_ENABLED T=-13.093/F=-13.093; COUNTER_TREND_ADD_BLOCK_ENABLED T=-13.093/F=-13.093
    - `COMPUSDT_LONG` [mac] 4 cases — WT_AGAINST_FILTER_ENABLED T=-15.630/F=-15.630; ALL_TF_AGAINST_CLOSE_ENABLED T=-15.630/F=-15.630; VEC_REENTRY_DC4_EXITPRICE_ENABLED T=-15.630/F=-15.630
    - `COMPUSDT_SHORT` [mac] 4 cases — BB_SQUEEZE_ENTRY_ENABLED T=+15.361/F=+15.361; WT_DIV_EXIT_ENABLED T=+2.458/F=+2.458; WT_15M_BOUNCE_OPEN_ENABLED T=+4.359/F=+2.458
    - `NEM_LONG` [s1] 3 cases — WT_ACCEL_EXIT_ENABLED T=+14.011/F=+14.011; HTF_BULL_ENTRY_FILTER_ENABLED T=+14.011/F=+14.011; HAIKU_ENTRY_GATE_ENABLED T=+12.021/F=+12.021
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T01:19Z · window 48.0h · 355 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 44 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `ALL_TF_AGAINST_CLOSE_ENABLED`×3, `WT_ACCEL_EXIT_ENABLED`×3, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `COUNTER_TREND_ADD_BLOCK_ENABLED`×2, `GR_FILTER_VEC_ENABLED`×2, `HTF_DIRECTION_GATE_ENABLED`×2, `WT_AGAINST_FILTER_ENABLED`×2, `WT_DIV_EXIT_ENABLED`×2, `HTF_BULL_ENTRY_FILTER_ENABLED`×2, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HAIKU_ENTRY_GATE_ENABLED`×1, `VEC_REENTRY_DC4_EXITPRICE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BAND_ARROW_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1
    - `ETHUSDC_SHORT` [mac] 21 cases — OI_CONFIRM_ENABLED T=-20.658/F=-21.196; UNIVERSAL_NOLOSS_GATE T=-20.596/F=-20.596; MTS_GATE_ENABLED T=-20.596/F=-21.196
    - `NKE_SHORT` [mac] 7 cases — WT_DIV_EXIT_ENABLED T=-13.093/F=-13.093; ALL_TF_AGAINST_CLOSE_ENABLED T=-13.093/F=-13.093; COUNTER_TREND_ADD_BLOCK_ENABLED T=-13.093/F=-13.093
    - `COMPUSDT_LONG` [mac] 4 cases — WT_AGAINST_FILTER_ENABLED T=-15.630/F=-15.630; ALL_TF_AGAINST_CLOSE_ENABLED T=-15.630/F=-15.630; VEC_REENTRY_DC4_EXITPRICE_ENABLED T=-15.630/F=-15.630
    - `COMPUSDT_SHORT` [mac] 4 cases — BB_SQUEEZE_ENTRY_ENABLED T=+15.361/F=+15.361; WT_DIV_EXIT_ENABLED T=+2.458/F=+2.458; WT_15M_BOUNCE_OPEN_ENABLED T=+4.359/F=+2.458
    - `NEM_LONG` [mac] 3 cases — WT_ACCEL_EXIT_ENABLED T=+14.011/F=+14.011; HTF_BULL_ENTRY_FILTER_ENABLED T=+14.011/F=+14.011; HAIKU_ENTRY_GATE_ENABLED T=+12.021/F=+12.021
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T01:33Z · window 48.0h · 357 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 44 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `ALL_TF_AGAINST_CLOSE_ENABLED`×3, `WT_ACCEL_EXIT_ENABLED`×3, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `COUNTER_TREND_ADD_BLOCK_ENABLED`×2, `GR_FILTER_VEC_ENABLED`×2, `HTF_DIRECTION_GATE_ENABLED`×2, `WT_AGAINST_FILTER_ENABLED`×2, `WT_DIV_EXIT_ENABLED`×2, `HTF_BULL_ENTRY_FILTER_ENABLED`×2, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HAIKU_ENTRY_GATE_ENABLED`×1, `VEC_REENTRY_DC4_EXITPRICE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BAND_ARROW_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1
    - `ETHUSDC_SHORT` [mac] 21 cases — OI_CONFIRM_ENABLED T=-20.658/F=-21.196; UNIVERSAL_NOLOSS_GATE T=-20.596/F=-20.596; MTS_GATE_ENABLED T=-20.596/F=-21.196
    - `NKE_SHORT` [mac] 7 cases — WT_DIV_EXIT_ENABLED T=-13.093/F=-13.093; ALL_TF_AGAINST_CLOSE_ENABLED T=-13.093/F=-13.093; COUNTER_TREND_ADD_BLOCK_ENABLED T=-13.093/F=-13.093
    - `COMPUSDT_LONG` [mac] 4 cases — WT_AGAINST_FILTER_ENABLED T=-15.630/F=-15.630; ALL_TF_AGAINST_CLOSE_ENABLED T=-15.630/F=-15.630; VEC_REENTRY_DC4_EXITPRICE_ENABLED T=-15.630/F=-15.630
    - `COMPUSDT_SHORT` [mac] 4 cases — BB_SQUEEZE_ENTRY_ENABLED T=+15.361/F=+15.361; WT_DIV_EXIT_ENABLED T=+2.458/F=+2.458; WT_15M_BOUNCE_OPEN_ENABLED T=+4.359/F=+2.458
    - `NEM_LONG` [mac] 3 cases — WT_ACCEL_EXIT_ENABLED T=+14.011/F=+14.011; HTF_BULL_ENTRY_FILTER_ENABLED T=+14.011/F=+14.011; HAIKU_ENTRY_GATE_ENABLED T=+12.021/F=+12.021
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T01:50Z · window 48.0h · 358 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 59 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `ALL_TF_AGAINST_CLOSE_ENABLED`×3, `GR_FILTER_VEC_ENABLED`×3, `HTF_DIRECTION_GATE_ENABLED`×3, `WT_ACCEL_EXIT_ENABLED`×3, `COUNTER_TREND_ADD_BLOCK_ENABLED`×2, `WT_AGAINST_FILTER_ENABLED`×2, `WT_DIV_EXIT_ENABLED`×2, `HTF_BULL_ENTRY_FILTER_ENABLED`×2, `BB_SQUEEZE_ENTRY_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `BAND_ARROW_ENABLED`×2, `BB_SQUEEZE_EXIT_ENABLED`×2, `DELTA_REENTRY_FILTER_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `HA_WICK_QUALITY_ENABLED`×2, `HTF_TREND_VETO_BYPASS_ENABLED`×2, `LH_HL_FILTER_ENABLED`×2, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×2, `HAIKU_ENTRY_GATE_ENABLED`×1, `VEC_REENTRY_DC4_EXITPRICE_ENABLED`×1
    - `ETHUSDC_SHORT` [mac] 21 cases — OI_CONFIRM_ENABLED T=-20.658/F=-21.196; UNIVERSAL_NOLOSS_GATE T=-20.596/F=-20.596; MTS_GATE_ENABLED T=-20.596/F=-21.196
    - `BNBUSDC_SHORT` [s1] 17 cases — OI_CONFIRM_ENABLED T=-5.970/F=-5.970; BAND_ARROW_ENABLED T=-5.970/F=-5.970; MARKET_QUALITY_SCORE_ENABLED T=-5.970/F=-5.970
    - `NKE_SHORT` [mac] 7 cases — WT_DIV_EXIT_ENABLED T=-13.093/F=-13.093; ALL_TF_AGAINST_CLOSE_ENABLED T=-13.093/F=-13.093; COUNTER_TREND_ADD_BLOCK_ENABLED T=-13.093/F=-13.093
    - `COMPUSDT_LONG` [mac] 4 cases — WT_AGAINST_FILTER_ENABLED T=-15.630/F=-15.630; ALL_TF_AGAINST_CLOSE_ENABLED T=-15.630/F=-15.630; VEC_REENTRY_DC4_EXITPRICE_ENABLED T=-15.630/F=-15.630
    - `COMPUSDT_SHORT` [mac] 4 cases — BB_SQUEEZE_ENTRY_ENABLED T=+15.361/F=+15.361; WT_DIV_EXIT_ENABLED T=+2.458/F=+2.458; WT_15M_BOUNCE_OPEN_ENABLED T=+4.359/F=+2.458
    - `NEM_LONG` [mac] 3 cases — WT_ACCEL_EXIT_ENABLED T=+14.011/F=+14.011; HTF_BULL_ENTRY_FILTER_ENABLED T=+14.011/F=+14.011; HAIKU_ENTRY_GATE_ENABLED T=+12.021/F=+12.021
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T02:04Z · window 48.0h · 360 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 59 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `ALL_TF_AGAINST_CLOSE_ENABLED`×3, `GR_FILTER_VEC_ENABLED`×3, `HTF_DIRECTION_GATE_ENABLED`×3, `WT_ACCEL_EXIT_ENABLED`×3, `COUNTER_TREND_ADD_BLOCK_ENABLED`×2, `WT_AGAINST_FILTER_ENABLED`×2, `WT_DIV_EXIT_ENABLED`×2, `HTF_BULL_ENTRY_FILTER_ENABLED`×2, `BB_SQUEEZE_ENTRY_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `BAND_ARROW_ENABLED`×2, `BB_SQUEEZE_EXIT_ENABLED`×2, `DELTA_REENTRY_FILTER_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `HA_WICK_QUALITY_ENABLED`×2, `HTF_TREND_VETO_BYPASS_ENABLED`×2, `LH_HL_FILTER_ENABLED`×2, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×2, `HAIKU_ENTRY_GATE_ENABLED`×1, `VEC_REENTRY_DC4_EXITPRICE_ENABLED`×1
    - `ETHUSDC_SHORT` [mac] 21 cases — OI_CONFIRM_ENABLED T=-20.658/F=-21.196; UNIVERSAL_NOLOSS_GATE T=-20.596/F=-20.596; MTS_GATE_ENABLED T=-20.596/F=-21.196
    - `BNBUSDC_SHORT` [mac] 17 cases — OI_CONFIRM_ENABLED T=-5.970/F=-5.970; BAND_ARROW_ENABLED T=-5.970/F=-5.970; MARKET_QUALITY_SCORE_ENABLED T=-5.970/F=-5.970
    - `NKE_SHORT` [mac] 7 cases — WT_DIV_EXIT_ENABLED T=-13.093/F=-13.093; ALL_TF_AGAINST_CLOSE_ENABLED T=-13.093/F=-13.093; COUNTER_TREND_ADD_BLOCK_ENABLED T=-13.093/F=-13.093
    - `COMPUSDT_LONG` [mac] 4 cases — WT_AGAINST_FILTER_ENABLED T=-15.630/F=-15.630; ALL_TF_AGAINST_CLOSE_ENABLED T=-15.630/F=-15.630; VEC_REENTRY_DC4_EXITPRICE_ENABLED T=-15.630/F=-15.630
    - `COMPUSDT_SHORT` [mac] 4 cases — BB_SQUEEZE_ENTRY_ENABLED T=+15.361/F=+15.361; WT_DIV_EXIT_ENABLED T=+2.458/F=+2.458; WT_15M_BOUNCE_OPEN_ENABLED T=+4.359/F=+2.458
    - `NEM_LONG` [mac] 3 cases — WT_ACCEL_EXIT_ENABLED T=+14.011/F=+14.011; HTF_BULL_ENTRY_FILTER_ENABLED T=+14.011/F=+14.011; HAIKU_ENTRY_GATE_ENABLED T=+12.021/F=+12.021
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T02:20Z · window 48.0h · 301 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.303/F=-3.288
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-13.618/F=-6.318
    - `CHRUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.091/F=-1.091
    - `DASHUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-13.925/F=-11.016
    - `SANDUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-7.397/F=-8.473
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-29.078/F=-14.845
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T02:35Z · window 48.0h · 354 sym_sides (local+remote)
- ⚠️ **6 sym_sides / 36 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_ACCEL_EXIT_ENABLED`×3, `ALL_TF_AGAINST_CLOSE_ENABLED`×2, `GR_FILTER_VEC_ENABLED`×2, `HTF_DIRECTION_GATE_ENABLED`×2, `WT_AGAINST_FILTER_ENABLED`×2, `WT_DIV_EXIT_ENABLED`×2, `HTF_BULL_ENTRY_FILTER_ENABLED`×2, `BB_SQUEEZE_ENTRY_ENABLED`×2, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `HAIKU_ENTRY_GATE_ENABLED`×1, `VEC_REENTRY_DC4_EXITPRICE_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_15M_BOUNCE_OPEN_ENABLED`×1, `BAND_ARROW_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `DELTA_GATE_BB_SQUEEZE`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1
    - `BNBUSDC_SHORT` [mac] 17 cases — OI_CONFIRM_ENABLED T=-5.970/F=-5.970; BAND_ARROW_ENABLED T=-5.970/F=-5.970; MARKET_QUALITY_SCORE_ENABLED T=-5.970/F=-5.970
    - `NKE_SHORT` [mac] 7 cases — WT_DIV_EXIT_ENABLED T=-13.093/F=-13.093; ALL_TF_AGAINST_CLOSE_ENABLED T=-13.093/F=-13.093; COUNTER_TREND_ADD_BLOCK_ENABLED T=-13.093/F=-13.093
    - `COMPUSDT_LONG` [mac] 4 cases — WT_AGAINST_FILTER_ENABLED T=-15.630/F=-15.630; ALL_TF_AGAINST_CLOSE_ENABLED T=-15.630/F=-15.630; VEC_REENTRY_DC4_EXITPRICE_ENABLED T=-15.630/F=-15.630
    - `COMPUSDT_SHORT` [mac] 4 cases — BB_SQUEEZE_ENTRY_ENABLED T=+15.361/F=+15.361; WT_DIV_EXIT_ENABLED T=+2.458/F=+2.458; WT_15M_BOUNCE_OPEN_ENABLED T=+4.359/F=+2.458
    - `NEM_LONG` [mac] 3 cases — WT_ACCEL_EXIT_ENABLED T=+14.011/F=+14.011; HTF_BULL_ENTRY_FILTER_ENABLED T=+14.011/F=+14.011; HAIKU_ENTRY_GATE_ENABLED T=+12.021/F=+12.021
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T02:51Z · window 48.0h · 310 sym_sides (local+remote)
- ⚠️ **5 sym_sides / 5 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×4, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `SANDUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-7.397/F=-8.473
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-29.078/F=-14.845
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T03:07Z · window 48.0h · 310 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×4
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `SANDUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-7.397/F=-8.473
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-29.078/F=-14.845
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T03:25Z · window 48.0h · 311 sym_sides (local+remote)
- ⚠️ **5 sym_sides / 5 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×4, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `SANDUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-7.397/F=-8.473
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-29.078/F=-14.845
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T03:39Z · window 48.0h · 316 sym_sides (local+remote)
- ⚠️ **5 sym_sides / 5 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×4, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `SANDUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-7.397/F=-8.473
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-29.078/F=-14.845
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T03:58Z · window 48.0h · 324 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×3, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `SANDUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-7.397/F=-8.473
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T04:23Z · window 48.0h · 329 sym_sides (local+remote)
- ⚠️ **3 sym_sides / 3 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T04:34Z · window 48.0h · 329 sym_sides (local+remote)
- ⚠️ **3 sym_sides / 3 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T04:55Z · window 48.0h · 331 sym_sides (local+remote)
- ⚠️ **3 sym_sides / 3 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T05:06Z · window 48.0h · 332 sym_sides (local+remote)
- ⚠️ **3 sym_sides / 3 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T05:25Z · window 48.0h · 333 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T05:33Z · window 48.0h · 333 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T05:53Z · window 48.0h · 334 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T06:13Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T06:19Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T06:27Z · window 48.0h · 337 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T06:39Z · window 48.0h · 337 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T07:01Z · window 48.0h · 337 sym_sides (local+remote)
- ⚠️ **3 sym_sides / 3 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `HTF_GATE_D_MANDATORY`×1
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T07:14Z · window 48.0h · 335 sym_sides (local+remote)
- ⚠️ **3 sym_sides / 3 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `HTF_GATE_D_MANDATORY`×1
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T07:31Z · window 48.0h · 289 sym_sides (local+remote)
- ⚠️ **3 sym_sides / 3 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `HTF_GATE_D_MANDATORY`×1
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T07:52Z · window 48.0h · 288 sym_sides (local+remote)
- ⚠️ **3 sym_sides / 3 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `HTF_GATE_D_MANDATORY`×1
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-04T08:18Z · window 48.0h · 169 sym_sides (local+remote)
- ⚠️ **1 sym_sides / 1 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.
