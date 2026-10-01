
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

## 2026-10-01T02:17Z · window 48.0h · 501 sym_sides (local+remote)
- ⚠️ **14 sym_sides / 24 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×7, `MTF_BB_REJECT_EXIT_ENABLED`×2, `PARTIAL_PROFIT_LOCK_ENABLED`×1, `VIGILANCE_GUARD_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
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
    - `CRWD_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.221/F=+0.081
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T02:31Z · window 48.0h · 501 sym_sides (local+remote)
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
    - `UEC_LONG` [s1] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.059/F=+0.174
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `CRWD_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.221/F=+0.081
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T02:47Z · window 48.0h · 501 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 22 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×5, `MTF_BB_REJECT_EXIT_ENABLED`×2, `PARTIAL_PROFIT_LOCK_ENABLED`×1, `VIGILANCE_GUARD_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `FCN_SHORT` [s2] 1 cases — PARTIAL_PROFIT_LOCK_ENABLED T=+0.107/F=-1.483
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `UEC_LONG` [s1] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.059/F=+0.174
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `CRWD_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.221/F=+0.081
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T03:01Z · window 48.0h · 501 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 21 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×5, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `UEC_LONG` [s1] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.059/F=+0.174
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `CRWD_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.221/F=+0.081
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T03:17Z · window 48.0h · 501 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 21 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×5, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `UEC_LONG` [s1] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.059/F=+0.174
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `CRWD_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.221/F=+0.081
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T03:31Z · window 48.0h · 501 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 21 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×5, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `UEC_LONG` [s1] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.059/F=+0.174
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `CRWD_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.221/F=+0.081
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T03:46Z · window 48.0h · 501 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 21 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×5, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `UEC_LONG` [s1] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.059/F=+0.174
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `CRWD_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.221/F=+0.081
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T04:01Z · window 48.0h · 501 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 21 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×5, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `UEC_LONG` [s1] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.059/F=+0.174
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `CRWD_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.221/F=+0.081
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T04:17Z · window 48.0h · 501 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 21 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×5, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `ALL_TF_AGAINST_CLOSE_ENABLED`×1, `AUGMENT_WT_4H_BOUNCE_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `COUNTER_TREND_ADD_BLOCK_ENABLED`×1, `DAEMON_REENTRY_STALE_EXIT_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `GUARANTEED_REENTRY_TIGHT_STOP_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `MARKET_QUALITY_SCORE_ENABLED`×1, `WT_AGAINST_FILTER_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `ASTS_SHORT` [mac] 11 cases — WT_AGAINST_FILTER_ENABLED T=-14.055/F=-14.055; ALL_TF_AGAINST_CLOSE_ENABLED T=-14.055/F=-14.055; COUNTER_TREND_ADD_BLOCK_ENABLED T=-14.055/F=-14.055
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `UEC_LONG` [s1] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.059/F=+0.174
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `CRWD_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.221/F=+0.081
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T04:31Z · window 48.0h · 498 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×5, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `UEC_LONG` [s1] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.059/F=+0.174
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `CRWD_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.221/F=+0.081
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T04:46Z · window 48.0h · 498 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 9 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×4, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `CRWD_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.221/F=+0.081
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T05:01Z · window 48.0h · 497 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 9 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×4, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `CRWD_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.221/F=+0.081
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T05:16Z · window 48.0h · 497 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 9 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×4, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `CRWD_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.221/F=+0.081
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T05:31Z · window 48.0h · 496 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 9 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×4, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `CRWD_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-1.221/F=+0.081
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T05:49Z · window 48.0h · 496 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×3, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T06:01Z · window 48.0h · 495 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×3, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T06:16Z · window 48.0h · 492 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×3, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T06:31Z · window 48.0h · 491 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×3, `MTF_BB_REJECT_EXIT_ENABLED`×2, `VIGILANCE_GUARD_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T06:46Z · window 48.0h · 491 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×3, `MTF_BB_REJECT_EXIT_ENABLED`×2, `NEWBORN_LOSS_KILL_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `QTUMUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.131/F=-4.145
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T07:01Z · window 48.0h · 491 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×3, `NEWBORN_LOSS_KILL_ENABLED`×2, `HTF_GATE_D_MANDATORY`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T07:16Z · window 48.0h · 491 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×3, `NEWBORN_LOSS_KILL_ENABLED`×2, `HTF_GATE_D_MANDATORY`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T07:31Z · window 48.0h · 493 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×4, `NEWBORN_LOSS_KILL_ENABLED`×2, `HTF_GATE_D_MANDATORY`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `UEC_LONG` [s1] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.059/F=+0.174
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `ARM_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-3.361/F=+1.080
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T07:46Z · window 48.0h · 495 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×4, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T08:01Z · window 48.0h · 498 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×4, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T08:16Z · window 48.0h · 503 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×4, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T08:32Z · window 48.0h · 517 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T08:46Z · window 48.0h · 517 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T09:01Z · window 48.0h · 521 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T09:16Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `WT_4H_VEL_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `BTCUSDC_SHORT` [s1] 1 cases — WT_4H_VEL_EXIT_ENABLED T=-0.114/F=+0.039
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T09:31Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T09:46Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T10:01Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T10:16Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T10:32Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T10:46Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `E_1_WT_EXIT_USE_DELTA_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `XMRUSDT_SHORT` [s1] 1 cases — E_1_WT_EXIT_USE_DELTA_ENABLED T=-6.142/F=+0.334
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T11:01Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×7, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.687/F=-0.687
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T11:16Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×7, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `1000FLOKIUSDT_LONG` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.839/F=-4.045
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.687/F=-0.687
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T11:32Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×7, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.687/F=-0.687
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T11:46Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `MASKUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.281/F=-0.281
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.687/F=-0.687
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T12:01Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `MASKUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.281/F=-0.281
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.687/F=-0.687
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T12:16Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 14 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.687/F=-0.687
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T12:31Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **14 sym_sides / 15 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.687/F=-0.687
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T12:46Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 14 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×7, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T13:01Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `LH_HL_FILTER_REQUIRE_BOTH`×1, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T13:16Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `LH_HL_FILTER_REQUIRE_BOTH`×1, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T13:31Z · window 48.0h · 524 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `LH_HL_FILTER_REQUIRE_BOTH`×1, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T13:47Z · window 48.0h · 516 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 46 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×4, `HTF_DIRECTION_GATE_ENABLED`×3, `BAND_ARROW_ENABLED`×2, `DELTA_REENTRY_FILTER_ENABLED`×2, `GR_FILTER_VEC_ENABLED`×2, `HA_WICK_QUALITY_ENABLED`×2, `HTF_TREND_VETO_BYPASS_ENABLED`×2, `LH_HL_FILTER_ENABLED`×2, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×2, `HTF_GATE_D_MANDATORY`×2, `HTF4_CONF`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `VIGILANCE_GUARD_ENABLED`×1, `BB_SQUEEZE_ENTRY_ENABLED`×1, `BB_SQUEEZE_EXIT_ENABLED`×1, `DELTA_GATE_BB_SQUEEZE`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1, `NEWBORN_LOSS_KILL_ENABLED`×1, `UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED`×1
    - `GOOGLUSDT_SHORT` [mac] 20 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `BNBUSDC_SHORT` [mac] 17 cases — OI_CONFIRM_ENABLED T=-5.970/F=-5.970; BAND_ARROW_ENABLED T=-5.970/F=-5.970; MARKET_QUALITY_SCORE_ENABLED T=-5.970/F=-5.970
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `ZENUSDT_LONG` [s5] 1 cases — VIGILANCE_GUARD_ENABLED T=-6.265/F=+2.119
    - `ZECUSDC_LONG` [s5] 1 cases — HTF_DIRECTION_GATE_ENABLED T=-28.898/F=+0.457
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `1000000MOGUSDT_SHORT` [s2] 1 cases — UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED T=+0.004/F=-0.099
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T14:02Z · window 48.0h · 526 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×3, `HTF_GATE_D_MANDATORY`×3, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `LH_HL_FILTER_REQUIRE_BOTH`×1, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T14:16Z · window 48.0h · 526 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×3, `HTF_GATE_D_MANDATORY`×3, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `LH_HL_FILTER_REQUIRE_BOTH`×1, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T14:32Z · window 48.0h · 526 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×3, `HTF_GATE_D_MANDATORY`×3, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `LH_HL_FILTER_REQUIRE_BOTH`×1, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T14:46Z · window 48.0h · 526 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×4, `HTF_GATE_D_MANDATORY`×3, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `LH_HL_FILTER_REQUIRE_BOTH`×1, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `1000PEPEUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.403/F=-0.396
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T15:01Z · window 48.0h · 526 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×4, `HTF_GATE_D_MANDATORY`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `LH_HL_FILTER_REQUIRE_BOTH`×1, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `1000PEPEUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.403/F=-0.396
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T15:16Z · window 48.0h · 526 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×6, `HTF_GATE_D_MANDATORY`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `LH_HL_FILTER_REQUIRE_BOTH`×1, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `UNIUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+12.187/F=-7.316
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `1000PEPEUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.403/F=-0.396
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `XTZUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.311/F=-20.860
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T15:31Z · window 48.0h · 526 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×6, `HTF_GATE_D_MANDATORY`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `LH_HL_FILTER_REQUIRE_BOTH`×1, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `UNIUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+12.187/F=-7.316
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `1000PEPEUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.403/F=-0.396
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `XTZUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.311/F=-20.860
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T15:46Z · window 48.0h · 526 sym_sides (local+remote)
- ⚠️ **14 sym_sides / 15 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×8, `HTF_GATE_D_MANDATORY`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `LH_HL_FILTER_REQUIRE_BOTH`×1, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `UNIUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+12.187/F=-7.316
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `1000PEPEUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.403/F=-0.396
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `SNXUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.944/F=-23.332
    - `XTZUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.311/F=-20.860
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `KSMUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.316/F=-19.262
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T16:02Z · window 48.0h · 526 sym_sides (local+remote)
- ⚠️ **15 sym_sides / 16 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×9, `HTF_GATE_D_MANDATORY`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `LH_HL_FILTER_REQUIRE_BOTH`×1, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `AVAXUSDC_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.480/F=-1.038
    - `UNIUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+12.187/F=-7.316
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `1000PEPEUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.403/F=-0.396
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `SNXUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.944/F=-23.332
    - `XTZUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.311/F=-20.860
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `KSMUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.316/F=-19.262
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T16:16Z · window 48.0h · 526 sym_sides (local+remote)
- ⚠️ **17 sym_sides / 18 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×10, `NEWBORN_LOSS_KILL_ENABLED`×2, `HTF_GATE_D_MANDATORY`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `LH_HL_FILTER_REQUIRE_BOTH`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `AVAXUSDC_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.480/F=-1.038
    - `UNIUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+12.187/F=-7.316
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `1000PEPEUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.403/F=-0.396
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `COTIUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.560/F=-20.551
    - `EGLDUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.478/F=-13.938
    - `SNXUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.944/F=-23.332
    - `XTZUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.311/F=-20.860
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `KSMUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.316/F=-19.262
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T16:31Z · window 48.0h · 526 sym_sides (local+remote)
- ⚠️ **18 sym_sides / 19 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×11, `NEWBORN_LOSS_KILL_ENABLED`×2, `HTF_GATE_D_MANDATORY`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `LH_HL_FILTER_REQUIRE_BOTH`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `1000PEPEUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.403/F=-0.396
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `COTIUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.560/F=-20.551
    - `SNXUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.944/F=-23.332
    - `XTZUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.311/F=-20.860
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `UNIUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+12.187/F=-7.316
    - `LINKUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.434/F=-10.548
    - `AVAXUSDC_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.480/F=-1.038
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `EGLDUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.478/F=-13.938
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `KSMUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.316/F=-19.262
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T16:46Z · window 48.0h · 525 sym_sides (local+remote)
- ⚠️ **18 sym_sides / 19 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×11, `NEWBORN_LOSS_KILL_ENABLED`×2, `HTF_GATE_D_MANDATORY`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `LH_HL_FILTER_REQUIRE_BOTH`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `1000PEPEUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.403/F=-0.396
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `COTIUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.560/F=-20.551
    - `SNXUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.944/F=-23.332
    - `XTZUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.311/F=-20.860
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `UNIUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+12.187/F=-7.316
    - `LINKUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.434/F=-10.548
    - `AVAXUSDC_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.480/F=-1.038
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `EGLDUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.478/F=-13.938
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `KSMUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.316/F=-19.262
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T17:01Z · window 48.0h · 533 sym_sides (local+remote)
- ⚠️ **18 sym_sides / 19 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×11, `NEWBORN_LOSS_KILL_ENABLED`×2, `HTF_GATE_D_MANDATORY`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `LH_HL_FILTER_REQUIRE_BOTH`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `1000PEPEUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.403/F=-0.396
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `COTIUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.560/F=-20.551
    - `SNXUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.944/F=-23.332
    - `XTZUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.311/F=-20.860
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `UNIUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+12.187/F=-7.316
    - `LINKUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.434/F=-10.548
    - `AVAXUSDC_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.480/F=-1.038
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `EGLDUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.478/F=-13.938
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `KSMUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.316/F=-19.262
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T17:16Z · window 48.0h · 533 sym_sides (local+remote)
- ⚠️ **18 sym_sides / 19 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×11, `NEWBORN_LOSS_KILL_ENABLED`×2, `HTF_GATE_D_MANDATORY`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `LH_HL_FILTER_REQUIRE_BOTH`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `1000PEPEUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.403/F=-0.396
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `COTIUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.560/F=-20.551
    - `SNXUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.944/F=-23.332
    - `XTZUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.311/F=-20.860
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `UNIUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+12.187/F=-7.316
    - `LINKUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.434/F=-10.548
    - `AVAXUSDC_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.480/F=-1.038
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `EGLDUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.478/F=-13.938
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `KSMUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.316/F=-19.262
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T17:31Z · window 48.0h · 537 sym_sides (local+remote)
- ⚠️ **18 sym_sides / 19 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×11, `NEWBORN_LOSS_KILL_ENABLED`×2, `HTF_GATE_D_MANDATORY`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `LH_HL_FILTER_REQUIRE_BOTH`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `1000PEPEUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.403/F=-0.396
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `COTIUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.560/F=-20.551
    - `SNXUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.944/F=-23.332
    - `XTZUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.311/F=-20.860
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `UNIUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+12.187/F=-7.316
    - `LINKUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.434/F=-10.548
    - `AVAXUSDC_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.480/F=-1.038
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `EGLDUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.478/F=-13.938
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `KSMUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.316/F=-19.262
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T17:46Z · window 48.0h · 547 sym_sides (local+remote)
- ⚠️ **18 sym_sides / 19 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×11, `NEWBORN_LOSS_KILL_ENABLED`×2, `HTF_GATE_D_MANDATORY`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `LH_HL_FILTER_REQUIRE_BOTH`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `1000PEPEUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.403/F=-0.396
    - `COTIUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.560/F=-20.551
    - `SNXUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.944/F=-23.332
    - `XTZUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.311/F=-20.860
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `UNIUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+12.187/F=-7.316
    - `LINKUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.434/F=-10.548
    - `AVAXUSDC_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.480/F=-1.038
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `EGLDUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.478/F=-13.938
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `KSMUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.316/F=-19.262
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T18:01Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **18 sym_sides / 19 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×11, `NEWBORN_LOSS_KILL_ENABLED`×2, `HTF_GATE_D_MANDATORY`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `LH_HL_FILTER_REQUIRE_BOTH`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `1000PEPEUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.403/F=-0.396
    - `COTIUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.560/F=-20.551
    - `SNXUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.944/F=-23.332
    - `XTZUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.311/F=-20.860
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `UNIUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+12.187/F=-7.316
    - `LINKUSDC_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.434/F=-10.548
    - `AVAXUSDC_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.480/F=-1.038
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `EGLDUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+10.478/F=-13.938
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `KSMUSDT_LONG` [s5] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.316/F=-19.262
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T18:16Z · window 48.0h · 523 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `NEWBORN_LOSS_KILL_ENABLED`×2, `EMA_BLANKET_FILTER_ENABLED`×2
    - `SOLUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.341/F=+2.204
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `QTUMUSDT_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.375/F=-0.199
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.042/F=+0.335
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.687/F=-0.687
    - `MASKUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.281/F=-0.281
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T18:31Z · window 48.0h · 523 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `ZENUSDT_LONG` [s2] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.679/F=-11.882; HTF_GATE_D_MANDATORY T=-11.310/F=-3.201
    - `RLCUSDT_LONG` [s2] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.952/F=-13.943; HTF_GATE_D_MANDATORY T=-2.781/F=-4.361
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-4.847/F=+0.737
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.687/F=-0.687
    - `MASKUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.281/F=-0.281
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T18:46Z · window 48.0h · 523 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×7, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `ZENUSDT_LONG` [s2] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.679/F=-11.882; HTF_GATE_D_MANDATORY T=-11.310/F=-3.201
    - `RLCUSDT_LONG` [s2] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.952/F=-13.943; HTF_GATE_D_MANDATORY T=-2.781/F=-4.361
    - `HYPEUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.886/F=-7.051
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.687/F=-0.687
    - `MASKUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.281/F=-0.281
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T19:01Z · window 48.0h · 528 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `EMA_BLANKET_FILTER_ENABLED`×2, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `ZENUSDT_LONG` [s2] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.679/F=-11.882; HTF_GATE_D_MANDATORY T=-11.310/F=-3.201
    - `RLCUSDT_LONG` [s2] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.952/F=-13.943; HTF_GATE_D_MANDATORY T=-2.781/F=-4.361
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.687/F=-0.687
    - `MASKUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.281/F=-0.281
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T19:16Z · window 48.0h · 531 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `HTF_GATE_D_MANDATORY`×4, `EMA_BLANKET_FILTER_ENABLED`×2, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `ZENUSDT_LONG` [s2] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.679/F=-11.882; HTF_GATE_D_MANDATORY T=-11.310/F=-3.201
    - `WLDUSDC_LONG` [s2] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.472/F=-14.776; HTF_GATE_D_MANDATORY T=-5.512/F=-2.065
    - `BNBUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-1.237/F=+2.119; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.692/F=-3.962
    - `DOGEUSDC_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.295/F=-1.994
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.687/F=-0.687
    - `ZECUSDC_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.045/F=-0.676
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-01T19:31Z · window 48.0h · 531 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `HTF_GATE_D_MANDATORY`×4, `EMA_BLANKET_FILTER_ENABLED`×2, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `ZENUSDT_LONG` [s2] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.679/F=-11.882; HTF_GATE_D_MANDATORY T=-11.310/F=-3.201
    - `WLDUSDC_LONG` [s2] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.472/F=-14.776; HTF_GATE_D_MANDATORY T=-5.512/F=-2.065
    - `BNBUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-1.237/F=+2.119; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.692/F=-3.962
    - `DOGEUSDC_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.295/F=-1.994
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-0.687/F=-0.687
    - `ZECUSDC_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.045/F=-0.676
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.
