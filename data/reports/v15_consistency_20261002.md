
## 2026-10-02T00:02Z · window 48.0h · 537 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×7, `EMA_BLANKET_FILTER_ENABLED`×2, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T00:16Z · window 48.0h · 545 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×7, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T00:31Z · window 48.0h · 545 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×7, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T00:46Z · window 48.0h · 545 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×7, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T01:01Z · window 48.0h · 545 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×7, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T01:16Z · window 48.0h · 545 sym_sides (local+remote)
- ⚠️ **11 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×7, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T01:32Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T01:54Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T02:03Z · window 48.0h · 543 sym_sides (local+remote)
- ⚠️ **14 sym_sides / 16 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×9, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×4, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `ZENUSDT_LONG` [s2] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.679/F=-11.882; HTF_GATE_D_MANDATORY T=-11.310/F=-3.201
    - `RLCUSDT_LONG` [s2] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+2.952/F=-13.943; HTF_GATE_D_MANDATORY T=-2.781/F=-4.361
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `ZECUSDC_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.045/F=-0.676
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T02:22Z · window 48.0h · 499 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6, `EMA_BLANKET_FILTER_ENABLED`×2, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `DASHUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-16.781/F=-14.833
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T02:36Z · window 48.0h · 443 sym_sides (local+remote)
- ⚠️ **6 sym_sides / 6 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `EMA_BLANKET_FILTER_ENABLED`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.673/F=-1.108
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ZECUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-11.138/F=-1.664
    - `UEC_LONG` [s1] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.059/F=+0.174
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T02:45Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T03:00Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T03:15Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T03:30Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T03:45Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T04:00Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T04:15Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T04:30Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T04:45Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T05:00Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T05:15Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T05:30Z · window 48.0h · 549 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T05:45Z · window 48.0h · 546 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T06:00Z · window 48.0h · 537 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T06:16Z · window 48.0h · 527 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T06:30Z · window 48.0h · 519 sym_sides (local+remote)
- ⚠️ **12 sym_sides / 12 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T06:45Z · window 48.0h · 513 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T07:00Z · window 48.0h · 507 sym_sides (local+remote)
- ⚠️ **13 sym_sides / 13 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8, `EMA_BLANKET_FILTER_ENABLED`×2, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-12.120/F=-5.461
    - `GRTUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.700/F=-4.171
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T07:15Z · window 48.0h · 501 sym_sides (local+remote)
- ⚠️ **14 sym_sides / 15 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×9, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `HYPEUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.521/F=-6.363
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-4.996/F=-5.131
    - `NEARUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-10.850/F=-4.620
    - `NEARUSDC_SHORT` [s2] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.176/F=-1.962
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `EGLDUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-4.898/F=+0.381
    - `MANAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.678/F=-1.961
    - `ATOMUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.321/F=-4.247
    - `KSMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-21.388/F=-12.236
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T07:30Z · window 48.0h · 499 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `HYPEUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.521/F=-6.363
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T07:45Z · window 48.0h · 494 sym_sides (local+remote)
- ⚠️ **10 sym_sides / 11 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6, `EMA_BLANKET_FILTER_ENABLED`×2, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `HYPEUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.521/F=-6.363
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T08:00Z · window 48.0h · 483 sym_sides (local+remote)
- ⚠️ **9 sym_sides / 10 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1, `EMA_BLANKET_FILTER_ENABLED`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `HYPEUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-9.521/F=-6.363
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T08:15Z · window 48.0h · 476 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 9 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1, `EMA_BLANKET_FILTER_ENABLED`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T08:30Z · window 48.0h · 463 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 9 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1, `EMA_BLANKET_FILTER_ENABLED`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T08:45Z · window 48.0h · 457 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 9 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1, `EMA_BLANKET_FILTER_ENABLED`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T09:01Z · window 48.0h · 448 sym_sides (local+remote)
- ⚠️ **8 sym_sides / 9 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1, `EMA_BLANKET_FILTER_ENABLED`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T09:15Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T09:30Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T09:45Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T10:00Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T10:15Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T10:30Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T10:45Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T11:00Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T11:15Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T11:30Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 8 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1, `MTF_ATR_TRAIL_ENABLED_TRADIER`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
    - `MSFT_SHORT` [s5] 1 cases — MTF_ATR_TRAIL_ENABLED_TRADIER T=+0.010/F=-0.015
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T11:45Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **6 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `DOGEUSDC_LONG` [s5] 2 cases — HTF_GATE_D_MANDATORY T=-3.599/F=-1.406; WT_15M_LH_WAIT_EXIT_ENABLED T=-0.138/F=+0.035
    - `BNBUSDC_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.240/F=+0.887
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T12:00Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **6 sym_sides / 6 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `HYPEUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-14.093/F=-11.719
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-13.049/F=-5.810
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-19.396/F=-6.293
    - `MANAUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-3.644/F=-1.928
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T12:15Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **7 sym_sides / 7 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6, `MTF_BB_REJECT_EXIT_ENABLED`×1
    - `HYPEUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-14.093/F=-11.719
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-13.049/F=-5.810
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-22.278/F=-15.768
    - `AVAXUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.098/F=-0.488
    - `XLMUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-7.698/F=-1.141
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-29.762/F=-15.745
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T12:30Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **6 sym_sides / 6 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6
    - `HYPEUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-14.093/F=-11.719
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-13.049/F=-5.810
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-22.278/F=-15.768
    - `XLMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-7.869/F=+0.129
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-29.762/F=-15.745
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T12:45Z · window 48.0h · 441 sym_sides (local+remote)
- ⚠️ **6 sym_sides / 6 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6
    - `HYPEUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-14.093/F=-11.719
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-13.049/F=-5.810
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-22.278/F=-15.768
    - `XLMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-7.869/F=+0.129
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-29.762/F=-15.745
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T13:00Z · window 48.0h · 437 sym_sides (local+remote)
- ⚠️ **6 sym_sides / 6 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6
    - `HYPEUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-14.093/F=-11.719
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-13.049/F=-5.810
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-22.278/F=-15.768
    - `XLMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-7.869/F=+0.129
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-29.762/F=-15.745
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T13:15Z · window 48.0h · 427 sym_sides (local+remote)
- ⚠️ **6 sym_sides / 6 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6
    - `HYPEUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-14.093/F=-11.719
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-13.049/F=-5.810
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-22.278/F=-15.768
    - `XLMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-7.869/F=+0.129
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-29.762/F=-15.745
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T13:30Z · window 48.0h · 416 sym_sides (local+remote)
- ⚠️ **6 sym_sides / 6 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6
    - `HYPEUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-14.093/F=-11.719
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-13.049/F=-5.810
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-22.278/F=-15.768
    - `XLMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-7.869/F=+0.129
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-29.762/F=-15.745
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.

## 2026-10-02T13:45Z · window 48.0h · 411 sym_sides (local+remote)
- ⚠️ **6 sym_sides / 6 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×6
    - `HYPEUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-14.093/F=-11.719
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-13.049/F=-5.810
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-22.278/F=-15.768
    - `XLMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-7.869/F=+0.129
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-29.762/F=-15.745
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.
