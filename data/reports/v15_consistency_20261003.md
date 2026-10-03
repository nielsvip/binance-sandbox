
## 2026-10-03T00:00Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **5 sym_sides / 5 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.303/F=-3.288
    - `SANDUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-7.397/F=-8.473
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
    - `CHRUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.091/F=-1.091
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T00:15Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **5 sym_sides / 5 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.303/F=-3.288
    - `SANDUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-7.397/F=-8.473
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
    - `CHRUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.091/F=-1.091
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T00:30Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×4
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.303/F=-3.288
    - `SANDUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-7.397/F=-8.473
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T00:45Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×3, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `XMRUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.539/F=-1.738
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.303/F=-3.288
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T01:00Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **3 sym_sides / 3 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HTF_GATE_D_MANDATORY`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `MANAUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.218/F=-5.978
    - `AXSUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.125/F=-0.607
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T01:15Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **3 sym_sides / 3 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HTF_GATE_D_MANDATORY`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `MANAUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.218/F=-5.978
    - `AXSUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.125/F=-0.607
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T01:30Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **3 sym_sides / 3 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HTF_GATE_D_MANDATORY`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `MANAUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.218/F=-5.978
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T01:45Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **3 sym_sides / 3 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HTF_GATE_D_MANDATORY`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `MANAUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.218/F=-5.978
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T02:01Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **3 sym_sides / 3 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HTF_GATE_D_MANDATORY`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `MANAUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.218/F=-5.978
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T02:18Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HTF_GATE_D_MANDATORY`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `MANAUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.218/F=-5.978
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T02:31Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HTF_GATE_D_MANDATORY`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `MANAUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.218/F=-5.978
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T02:45Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HTF_GATE_D_MANDATORY`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `MANAUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.218/F=-5.978
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T03:00Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HTF_GATE_D_MANDATORY`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `MANAUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.218/F=-5.978
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T03:15Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HTF_GATE_D_MANDATORY`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `MANAUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.218/F=-5.978
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T03:30Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HTF_GATE_D_MANDATORY`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `MANAUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.218/F=-5.978
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T03:45Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **4 sym_sides / 4 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×2, `HTF_GATE_D_MANDATORY`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `MANAUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.218/F=-5.978
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T04:01Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **3 sym_sides / 22 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `GOOGLUSDT_SHORT` [s1] 20 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T04:16Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **5 sym_sides / 25 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×3, `EMA_BLANKET_FILTER_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 20 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T04:31Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **5 sym_sides / 24 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×3, `EMA_BLANKET_FILTER_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T04:45Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 26 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×4, `EMA_BLANKET_FILTER_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `WT_15M_BOUNCE_OPEN_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T05:01Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 26 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×4, `EMA_BLANKET_FILTER_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `WT_15M_BOUNCE_OPEN_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T05:16Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 26 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×4, `EMA_BLANKET_FILTER_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `WT_15M_BOUNCE_OPEN_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T05:31Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 28 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T05:46Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 28 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T06:01Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 28 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T06:16Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 28 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T06:34Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 28 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T06:46Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 28 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T07:01Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 28 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T07:16Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 32 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×6, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `EMA_BLANKET_FILTER_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T07:31Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 32 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×6, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `EMA_BLANKET_FILTER_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T07:46Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 32 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×6, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `EMA_BLANKET_FILTER_ENABLED`×2, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `CF_SHORT` [s5] 2 cases — EMA_BLANKET_FILTER_ENABLED T=+2.562/F=-1.940; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.077/F=-5.423
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T08:01Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T08:16Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T08:31Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T08:46Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T09:01Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T09:16Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T09:31Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T09:46Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T10:01Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T10:16Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T10:31Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T10:46Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T11:01Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T11:16Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T11:31Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T11:46Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T12:01Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T12:16Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T12:31Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T12:46Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T13:01Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T13:16Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T13:31Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T13:46Z · window 48.0h · 346 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T14:01Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T14:16Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T14:31Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T14:46Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T15:01Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T15:16Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T15:31Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T15:46Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T16:01Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T16:16Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T16:31Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T16:46Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T17:01Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T17:16Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T17:31Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T17:46Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T18:01Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T18:16Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T18:31Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-03T18:46Z · window 48.0h · 350 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 30 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1, `MTS_GATE_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `WT_15M_BOUNCE_REL_VOL_GT_1`×1, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386; OI_CONFIRM_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521; WT_15M_BOUNCE_VOLUME_FILTER_ENABLED T=+0.265/F=+0.265
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.
