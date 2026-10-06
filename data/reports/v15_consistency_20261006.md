
## 2026-10-06T00:01Z · window 48.0h · 337 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T00:17Z · window 48.0h · 339 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T00:32Z · window 48.0h · 337 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T00:47Z · window 48.0h · 336 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T01:01Z · window 48.0h · 335 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [s1] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T01:16Z · window 48.0h · 333 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [s1] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T01:31Z · window 48.0h · 331 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T01:46Z · window 48.0h · 328 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T02:01Z · window 48.0h · 327 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T02:16Z · window 48.0h · 324 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T02:31Z · window 48.0h · 320 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T02:46Z · window 48.0h · 319 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T03:01Z · window 48.0h · 318 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T03:16Z · window 48.0h · 318 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T03:31Z · window 48.0h · 315 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T03:46Z · window 48.0h · 306 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T04:01Z · window 48.0h · 281 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T04:16Z · window 48.0h · 270 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T04:31Z · window 48.0h · 269 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T04:46Z · window 48.0h · 268 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T05:01Z · window 48.0h · 265 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T05:21Z · window 48.0h · 228 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_DIV_EXIT_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `FILUSDC_LONG` [mac] 2 cases — WT_DIV_EXIT_ENABLED T=+2.203/F=+1.075; HTF_DIRECTION_GATE_ENABLED T=+0.012/F=-3.596
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T05:31Z · window 48.0h · 259 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T05:47Z · window 48.0h · 253 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T06:01Z · window 48.0h · 248 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T06:17Z · window 48.0h · 246 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T06:31Z · window 48.0h · 244 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-6.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T06:46Z · window 48.0h · 236 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `AMD_LONG` [s2] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=+3.372/F=+3.372
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-6.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T07:01Z · window 48.0h · 235 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `AMD_LONG` [s2] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=+3.372/F=+3.372
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-6.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T07:17Z · window 48.0h · 231 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `AMD_LONG` [s2] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=+3.372/F=+3.372
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-6.467
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T07:31Z · window 48.0h · 230 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `AMD_LONG` [s2] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=+3.372/F=+3.372
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T07:46Z · window 48.0h · 223 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `AMD_LONG` [s2] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=+3.372/F=+3.372
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-6.141
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T08:01Z · window 48.0h · 219 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `AMD_LONG` [s2] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=+3.372/F=+3.372
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-6.141
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T08:16Z · window 48.0h · 209 sym_sides (local+remote)
- ⚠️ **14 sym_sides / 14 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `AMD_LONG` [s2] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=+3.372/F=+3.372
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-6.141
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T08:31Z · window 48.0h · 208 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `WT_15M_BOUNCE_OPEN_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `AMD_LONG` [s2] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=+3.372/F=+3.372
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T08:46Z · window 48.0h · 207 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `MTF_DC_REJECT_USE_DC4`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — MTF_DC_REJECT_USE_DC4 T=-0.122/F=+0.316
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T09:01Z · window 48.0h · 208 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `MTF_DC_REJECT_USE_DC4`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — MTF_DC_REJECT_USE_DC4 T=-0.122/F=+0.316
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T09:16Z · window 48.0h · 203 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `MTF_DC_REJECT_USE_DC4`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — MTF_DC_REJECT_USE_DC4 T=-0.122/F=+0.316
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T09:31Z · window 48.0h · 201 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `MTF_DC_REJECT_USE_DC4`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — MTF_DC_REJECT_USE_DC4 T=-0.122/F=+0.316
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T09:45Z · window 48.0h · 198 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `MTF_DC_REJECT_USE_DC4`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — MTF_DC_REJECT_USE_DC4 T=-0.122/F=+0.316
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T10:01Z · window 48.0h · 202 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `MTF_DC_REJECT_USE_DC4`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — MTF_DC_REJECT_USE_DC4 T=-0.122/F=+0.316
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T10:15Z · window 48.0h · 198 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `MTF_DC_REJECT_USE_DC4`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — MTF_DC_REJECT_USE_DC4 T=-0.122/F=+0.316
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T10:31Z · window 48.0h · 197 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `MTF_DC_REJECT_USE_DC4`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — MTF_DC_REJECT_USE_DC4 T=-0.122/F=+0.316
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T10:45Z · window 48.0h · 196 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `MTF_DC_REJECT_USE_DC4`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — MTF_DC_REJECT_USE_DC4 T=-0.122/F=+0.316
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T11:01Z · window 48.0h · 195 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `MTF_DC_REJECT_USE_DC4`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — MTF_DC_REJECT_USE_DC4 T=-0.122/F=+0.316
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T11:15Z · window 48.0h · 195 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `MTF_DC_REJECT_USE_DC4`×1
    - `SNDK_LONG` [mac] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.596/F=+0.171
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — MTF_DC_REJECT_USE_DC4 T=-0.122/F=+0.316
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-06T11:31Z · window 48.0h · 195 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `MTF_DC_REJECT_USE_DC4`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `GOOGL_LONG` [mac] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.576/F=-1.831
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — MTF_DC_REJECT_USE_DC4 T=-0.122/F=+0.316
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.
