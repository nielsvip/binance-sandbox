
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
