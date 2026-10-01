
## 2026-10-01T00:01Z · window 48.0h · 493 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 18 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×5, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `WDAY_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=+0.355/F=-0.047
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `AGI_SHORT` [s5] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.023/F=+3.786
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `CRM_LONG` [s2] 1 cases — WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED T=-3.169/F=+0.043
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T00:16Z · window 48.0h · 493 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 18 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `AXTI_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.098/F=-2.141
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `AGI_SHORT` [s5] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.023/F=+3.786
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `CRM_LONG` [s2] 1 cases — WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED T=-3.169/F=+0.043
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T00:31Z · window 48.0h · 493 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 17 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `AXTI_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.098/F=-2.141
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `AGI_SHORT` [s5] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.023/F=+3.786
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T00:47Z · window 48.0h · 498 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 20 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×5, `MTF_BB_REJECT_EXIT_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `AXTI_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.098/F=-2.141
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `AGI_SHORT` [s5] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.023/F=+3.786
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `CME_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.527/F=+0.216
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T01:01Z · window 48.0h · 501 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 20 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×5, `MTF_BB_REJECT_EXIT_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `AXTI_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.098/F=-2.141
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `AGI_SHORT` [s5] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.023/F=+3.786
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `CME_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.527/F=+0.216
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T01:16Z · window 48.0h · 501 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 22 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×5, `MTF_BB_REJECT_EXIT_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×1, `PARTIAL_PROFIT_LOCK_ENABLED`×1, `VIGILANCE_GUARD_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `AXTI_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.098/F=-2.141
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `FCN_SHORT` [s2] 1 cases — PARTIAL_PROFIT_LOCK_ENABLED T=+0.107/F=-1.483
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `AGI_SHORT` [s5] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.023/F=+3.786
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `CME_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.527/F=+0.216
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T01:31Z · window 48.0h · 501 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 22 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×5, `MTF_BB_REJECT_EXIT_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×1, `PARTIAL_PROFIT_LOCK_ENABLED`×1, `VIGILANCE_GUARD_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `AXTI_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.098/F=-2.141
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `FCN_SHORT` [s2] 1 cases — PARTIAL_PROFIT_LOCK_ENABLED T=+0.107/F=-1.483
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `AGI_SHORT` [s5] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.023/F=+3.786
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `CME_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.527/F=+0.216
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T01:46Z · window 48.0h · 501 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 23 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×5, `MTF_BB_REJECT_EXIT_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×1, `PARTIAL_PROFIT_LOCK_ENABLED`×1, `VIGILANCE_GUARD_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `AXTI_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.098/F=-2.141
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `FCN_SHORT` [s2] 1 cases — PARTIAL_PROFIT_LOCK_ENABLED T=+0.107/F=-1.483
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `AGI_SHORT` [s5] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.023/F=+3.786
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `CME_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.527/F=+0.216
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T02:01Z · window 48.0h · 501 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 23 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×6, `MTF_BB_REJECT_EXIT_ENABLED`×2, `PARTIAL_PROFIT_LOCK_ENABLED`×1, `VIGILANCE_GUARD_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `FCN_SHORT` [s2] 1 cases — PARTIAL_PROFIT_LOCK_ENABLED T=+0.107/F=-1.483
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `AGI_SHORT` [s5] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.023/F=+3.786
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `CME_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.527/F=+0.216
    - `UEC_LONG` [s1] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.059/F=+0.174
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.
