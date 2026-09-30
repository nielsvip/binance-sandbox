
## 2026-09-30T19:54Z · window 48.0h · 312 sym_sides (local)
- ⚠️ **24 sym_sides / 281 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `GR_FILTER_VEC_ENABLED`×10, `BAND_ARROW_ENABLED`×9, `DELTA_REENTRY_FILTER_ENABLED`×9, `HTF_DIRECTION_GATE_ENABLED`×9, `EMA_BLANKET_FILTER_ENABLED`×8, `HA_WICK_QUALITY_ENABLED`×7, `HTF_TREND_VETO_BYPASS_ENABLED`×7, `LH_HL_FILTER_ENABLED`×7, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×7, `HTF_BULL_ENTRY_FILTER_ENABLED`×7, `BB_SQUEEZE_EXIT_ENABLED`×6, `COUNTER_TREND_ADD_BLOCK_ENABLED`×6, `ALL_TF_AGAINST_CLOSE_ENABLED`×6, `BB_SQUEEZE_ENTRY_ENABLED`×6, `WT_AGAINST_FILTER_ENABLED`×4, `WT_ACCEL_EXIT_ENABLED`×4, `HAIKU_ENTRY_GATE_ENABLED`×3, `MARKET_QUALITY_SCORE_ENABLED`×3, `DELTA_GATE_BB_SQUEEZE`×3, `ATR_TRAIL_SWEEP_ENABLED`×2
    - `1INCHUSDT_SHORT` [mac] 43 cases — OPEN_RATE_BREAKER_ENABLED T=-4.258/F=-4.258; HTF_DIRECTION_GATE_ENABLED T=-4.258/F=-4.258; MTS_GATE_ENABLED T=-4.258/F=-4.258
    - `ADAUSDC_SHORT` [mac] 43 cases — UNIVERSAL_NOLOSS_GATE T=-7.279/F=-4.468; COUNTER_TREND_ADD_BLOCK_ENABLED T=-4.468/F=-7.279; RECENT_REDUCTION_GUARD_ENABLED T=-4.468/F=-7.450
    - `XTZUSDT_LONG` [mac] 21 cases — HTF_DIRECTION_GATE_ENABLED T=-1.097/F=-1.097; MTS_GATE_ENABLED T=-1.097/F=-1.097; HA_WICK_QUALITY_ENABLED T=-1.097/F=-1.097
    - `ETHUSDC_SHORT` [mac] 21 cases — OI_CONFIRM_ENABLED T=-20.658/F=-21.196; UNIVERSAL_NOLOSS_GATE T=-20.596/F=-20.596; MTS_GATE_ENABLED T=-20.596/F=-21.196
    - `GOOGLUSDT_SHORT` [mac] 20 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_LONG` [mac] 18 cases — HAIKU_ENTRY_GATE_ENABLED T=+7.403/F=+7.403; WT_ACCEL_EXIT_ENABLED T=+1.989/F=+1.989; HTF_BULL_ENTRY_FILTER_ENABLED T=+1.989/F=+1.989
    - `BHP_LONG` [mac] 18 cases — BB_SQUEEZE_ENTRY_ENABLED T=-1.009/F=-1.009; ALL_TF_AGAINST_CLOSE_ENABLED T=-1.009/F=-1.009; BTC_GUARANTEED_REENTRY_ENABLED T=-1.009/F=-1.009
    - `INTC_LONG` [mac] 17 cases — BAND_ARROW_ENABLED T=-15.139/F=-15.139; EMA_BLANKET_FILTER_ENABLED T=-15.139/F=-15.139; HA_WICK_QUALITY_ENABLED T=-15.139/F=-15.139
    - `BNBUSDC_SHORT` [mac] 17 cases — OI_CONFIRM_ENABLED T=-5.970/F=-5.970; BAND_ARROW_ENABLED T=-5.970/F=-5.970; MARKET_QUALITY_SCORE_ENABLED T=-5.970/F=-5.970
    - `HYPEUSDT_LONG` [mac] 12 cases — HTF_DIRECTION_GATE_ENABLED T=-1.163/F=-0.633; MTS_GATE_ENABLED T=-1.105/F=-0.633; HA_WICK_QUALITY_ENABLED T=-1.450/F=-0.633
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `NKE_SHORT` [mac] 7 cases — WT_DIV_EXIT_ENABLED T=-13.093/F=-13.093; ALL_TF_AGAINST_CLOSE_ENABLED T=-13.093/F=-13.093; COUNTER_TREND_ADD_BLOCK_ENABLED T=-13.093/F=-13.093
    - `AIAUSDT_LONG` [mac] 7 cases — SHORT_DC_LOW_BREAK_ENABLED T=-10.782/F=-10.782; WT_15M_BOUNCE_REL_VOL_GT_1 T=-10.782/F=-10.782; WT_15M_BOUNCE_LOW_1H_GT_PREV T=-10.782/F=-10.782
    - `COMPUSDT_LONG` [mac] 4 cases — WT_AGAINST_FILTER_ENABLED T=-15.630/F=-15.630; ALL_TF_AGAINST_CLOSE_ENABLED T=-15.630/F=-15.630; VEC_REENTRY_DC4_EXITPRICE_ENABLED T=-15.630/F=-15.630
    - `COMPUSDT_SHORT` [mac] 4 cases — BB_SQUEEZE_ENTRY_ENABLED T=+15.361/F=+15.361; WT_DIV_EXIT_ENABLED T=+2.458/F=+2.458; WT_15M_BOUNCE_OPEN_ENABLED T=+4.359/F=+2.458
    - `NEM_LONG` [mac] 3 cases — WT_ACCEL_EXIT_ENABLED T=+14.011/F=+14.011; HTF_BULL_ENTRY_FILTER_ENABLED T=+14.011/F=+14.011; HAIKU_ENTRY_GATE_ENABLED T=+12.021/F=+12.021
    - `1INCHUSDT_LONG` [mac] 3 cases — FOLLOW_THROUGH_REENTRY_ENABLED T=+0.911/F=+0.911; MU_CORRECTION_REENTRY_ENABLED T=+0.911/F=+0.911; DELTA_EXIT_MANDATORY_REENTRY_ENABLED T=+0.911/F=+0.911
    - `AVAXUSDC_LONG` [mac] 2 cases — WT_AGAINST_FILTER_ENABLED T=-3.415/F=-3.415; GR_FILTER_VEC_ENABLED T=-1.671/F=-1.671
    - `1000FLOKIUSDT_SHORT` [mac] 2 cases — STDEV_BREAKOUT_ENABLED T=+0.242/F=+0.242; HTF_BULL_ENTRY_FILTER_ENABLED T=+0.242/F=+0.242
    - `RGLD_LONG` [mac] 2 cases — BTC_BREAKOUT_ENTRY_ENABLED T=+0.121/F=+0.121; HTF_BULL_ENTRY_FILTER_ENABLED T=+0.121/F=+0.121
    - `ETCUSDT_LONG` [mac] 2 cases — ATR_TRAIL_SWEEP_ENABLED T=+3.925/F=+3.925; UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED T=+3.925/F=+15.073
    - `SANDUSDT_SHORT` [mac] 2 cases — STDEV_SLOPE_SIZING_ENABLED T=-0.487/F=-0.541; BAND_SLOPE_SIZING_V2_ENABLED T=-0.487/F=-0.487
    - `PTBUSDT_SHORT` [mac] 1 cases — WT_ACCEL_EXIT_ENABLED T=+0.031/F=+0.031
    - `XLMUSDT_SHORT` [mac] 1 cases — OPEN_RATE_BREAKER_ENABLED T=+0.196/F=+0.196
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-09-30T20:02Z · window 48.0h · 493 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 14 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×3, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `WDAY_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=+0.355/F=-0.047
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-09-30T20:16Z · window 48.0h · 493 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 14 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×3, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `WDAY_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=+0.355/F=-0.047
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-09-30T20:33Z · window 48.0h · 493 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 14 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×3, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `WDAY_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=+0.355/F=-0.047
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-09-30T20:46Z · window 48.0h · 493 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 14 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×3, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `WDAY_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=+0.355/F=-0.047
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.
