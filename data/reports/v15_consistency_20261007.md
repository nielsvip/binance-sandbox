
## 2026-10-07T00:01Z · window 48.0h · 208 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `OI_CONFIRM_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `RZ_BREAKOUT_ENTRY_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `RLCUSDT_LONG` [mac] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=-1.948/F=+0.680
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-07T00:15Z · window 48.0h · 208 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `OI_CONFIRM_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `RZ_BREAKOUT_ENTRY_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `RLCUSDT_LONG` [mac] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=-1.948/F=+0.680
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-07T00:30Z · window 48.0h · 208 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `OI_CONFIRM_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `RZ_BREAKOUT_ENTRY_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `RLCUSDT_LONG` [mac] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=-1.948/F=+0.680
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-07T00:46Z · window 48.0h · 208 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `OI_CONFIRM_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `RZ_BREAKOUT_ENTRY_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `RLCUSDT_LONG` [mac] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=-1.948/F=+0.680
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-07T01:01Z · window 48.0h · 207 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `OI_CONFIRM_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `RZ_BREAKOUT_ENTRY_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `RLCUSDT_LONG` [mac] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=-1.948/F=+0.680
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-07T01:16Z · window 48.0h · 205 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `OI_CONFIRM_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `RZ_BREAKOUT_ENTRY_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `RLCUSDT_LONG` [mac] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=-1.948/F=+0.680
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-07T01:30Z · window 48.0h · 205 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `OI_CONFIRM_ENABLED`×2, `HTF_GATE_SIGNALS_SMA200D`×2, `RZ_BREAKOUT_ENTRY_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `RLCUSDT_LONG` [mac] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=-1.948/F=+0.680
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-07T01:45Z · window 48.0h · 205 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `OI_CONFIRM_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `RZ_BREAKOUT_ENTRY_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `WLDUSDC_LONG` [mac] 1 cases — OI_CONFIRM_ENABLED T=+3.958/F=+0.617
    - `RLCUSDT_LONG` [mac] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=-1.948/F=+0.680
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-07T02:01Z · window 48.0h · 206 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `OI_CONFIRM_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `RZ_BREAKOUT_ENTRY_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `WLDUSDC_LONG` [mac] 1 cases — OI_CONFIRM_ENABLED T=+3.958/F=+0.617
    - `RLCUSDT_LONG` [mac] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=-1.948/F=+0.680
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-07T02:15Z · window 48.0h · 204 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `OI_CONFIRM_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `RZ_BREAKOUT_ENTRY_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `WLDUSDC_LONG` [mac] 1 cases — OI_CONFIRM_ENABLED T=+3.958/F=+0.617
    - `RLCUSDT_LONG` [mac] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=-1.948/F=+0.680
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-07T02:30Z · window 48.0h · 204 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `OI_CONFIRM_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `RZ_BREAKOUT_ENTRY_ENABLED`×1, `WT_15M_BOUNCE_HIGH_1H_GT_PREV`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `WLDUSDC_LONG` [mac] 1 cases — OI_CONFIRM_ENABLED T=+3.958/F=+0.617
    - `RLCUSDT_LONG` [mac] 1 cases — WT_15M_BOUNCE_HIGH_1H_GT_PREV T=-1.948/F=+0.680
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-07T02:46Z · window 48.0h · 199 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `OI_CONFIRM_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `RZ_BREAKOUT_ENTRY_ENABLED`×1, `FUNDING_CROWD_ENTRY_ENABLED`×1, `EMA_9_21_FILTER_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `WLDUSDC_LONG` [mac] 1 cases — OI_CONFIRM_ENABLED T=+3.958/F=+0.617
    - `RLCUSDT_LONG` [s2] 1 cases — FUNDING_CROWD_ENTRY_ENABLED T=-8.272/F=+12.442
    - `BTCUSDC_LONG` [mac] 1 cases — EMA_9_21_FILTER_ENABLED T=+0.004/F=-0.066
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-07T03:01Z · window 48.0h · 199 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `OI_CONFIRM_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `RZ_BREAKOUT_ENTRY_ENABLED`×1, `FUNDING_CROWD_ENTRY_ENABLED`×1, `EMA_9_21_FILTER_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `WLDUSDC_LONG` [mac] 1 cases — OI_CONFIRM_ENABLED T=+3.958/F=+0.617
    - `RLCUSDT_LONG` [s2] 1 cases — FUNDING_CROWD_ENTRY_ENABLED T=-8.272/F=+12.442
    - `BTCUSDC_LONG` [mac] 1 cases — EMA_9_21_FILTER_ENABLED T=+0.004/F=-0.066
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-07T03:15Z · window 48.0h · 194 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `OI_CONFIRM_ENABLED`×3, `HTF_GATE_SIGNALS_SMA200D`×2, `RZ_BREAKOUT_ENTRY_ENABLED`×1, `FUNDING_CROWD_ENTRY_ENABLED`×1, `EMA_9_21_FILTER_ENABLED`×1, `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `WLDUSDC_LONG` [mac] 1 cases — OI_CONFIRM_ENABLED T=+3.958/F=+0.617
    - `RLCUSDT_LONG` [s2] 1 cases — FUNDING_CROWD_ENTRY_ENABLED T=-8.272/F=+12.442
    - `BTCUSDC_LONG` [mac] 1 cases — EMA_9_21_FILTER_ENABLED T=+0.004/F=-0.066
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.
