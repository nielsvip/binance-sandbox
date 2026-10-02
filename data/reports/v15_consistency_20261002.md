
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
