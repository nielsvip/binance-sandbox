
## 2026-10-05T00:06Z · window 48.0h · 318 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XMRUSDT_LONG` [s1] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-4.546/F=+0.596
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T00:21Z · window 48.0h · 318 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XMRUSDT_LONG` [s1] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-4.546/F=+0.596
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T00:36Z · window 48.0h · 318 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XMRUSDT_LONG` [s1] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-4.546/F=+0.596
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T00:51Z · window 48.0h · 318 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XMRUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-4.546/F=+0.596
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T01:05Z · window 48.0h · 318 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XMRUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-4.546/F=+0.596
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T01:20Z · window 48.0h · 319 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XMRUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-4.546/F=+0.596
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T01:36Z · window 48.0h · 319 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XMRUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-4.546/F=+0.596
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T01:51Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XMRUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-4.546/F=+0.596
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T02:05Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T02:20Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T02:35Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T02:51Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T03:06Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T03:21Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T03:36Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T03:52Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T04:06Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **6 sym_sides / 6 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×3, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T04:29Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **6 sym_sides / 6 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×3, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T04:40Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **6 sym_sides / 6 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×3, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T04:53Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×3, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `OI_CONFIRM_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T05:09Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×3, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `HTF_GATE_SIGNALS_SMA200D`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T05:25Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_DIV_EXIT_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `HTF_GATE_SIGNALS_SMA200D`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T05:38Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_DIV_EXIT_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `HTF_GATE_SIGNALS_SMA200D`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T05:52Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_DIV_EXIT_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `HTF_GATE_SIGNALS_SMA200D`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T06:09Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `OI_CONFIRM_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T06:26Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T06:38Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T06:51Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T07:06Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 9 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T07:22Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 9 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T07:36Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 9 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T07:51Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T08:05Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T08:18Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `OI_CONFIRM_ENABLED`×2, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T08:35Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T08:48Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `OI_CONFIRM_ENABLED`×2, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T09:05Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `OI_CONFIRM_ENABLED`×2, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T09:20Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `OI_CONFIRM_ENABLED`×2, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T09:35Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `OI_CONFIRM_ENABLED`×2, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T09:48Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `OI_CONFIRM_ENABLED`×2, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T10:04Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `OI_CONFIRM_ENABLED`×2, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T10:19Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `OI_CONFIRM_ENABLED`×2, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T10:35Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T10:51Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T11:06Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T11:20Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `OI_CONFIRM_ENABLED`×2, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T11:36Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T11:49Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `OI_CONFIRM_ENABLED`×2, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T12:05Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `OI_CONFIRM_ENABLED`×2, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T12:20Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T12:35Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T12:48Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `OI_CONFIRM_ENABLED`×2, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T13:06Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T13:20Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `OI_CONFIRM_ENABLED`×2, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T13:36Z · window 48.0h · 321 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T13:51Z · window 48.0h · 322 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T14:05Z · window 48.0h · 322 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T14:20Z · window 48.0h · 322 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T14:37Z · window 48.0h · 322 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T14:56Z · window 48.0h · 262 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×4, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `PBF_SHORT` [s1] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.121/F=+0.957
    - `WLDUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-0.845/F=+0.440
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `AAVEUSDC_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-6.588/F=+0.076
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `NFLX_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-2.469/F=+0.037
    - `ENSUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T15:09Z · window 48.0h · 221 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 9 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `OI_CONFIRM_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T15:23Z · window 48.0h · 322 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T15:37Z · window 48.0h · 322 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T15:51Z · window 48.0h · 322 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `SNDK_LONG` [s1] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T16:06Z · window 48.0h · 322 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `GOOGL_LONG` [s1] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `SNDK_LONG` [s1] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T16:21Z · window 48.0h · 324 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `GOOGL_LONG` [s1] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `SNDK_LONG` [s1] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T16:41Z · window 48.0h · 324 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `GOOGL_LONG` [s1] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `SNDK_LONG` [s1] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T16:51Z · window 48.0h · 324 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `GOOGL_LONG` [s1] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `SNDK_LONG` [s1] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T17:05Z · window 48.0h · 324 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `GOOGL_LONG` [s1] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `SNDK_LONG` [s1] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T17:21Z · window 48.0h · 324 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `GOOGL_LONG` [s1] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `SNDK_LONG` [s1] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-05T18:05Z · window 48.0h · 324 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `GOOGL_LONG` [s1] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.
