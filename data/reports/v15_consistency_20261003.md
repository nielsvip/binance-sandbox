
## 2026-10-03T00:00Z · window 48.0h · 326 sym_sides (local+remote)
- ⚠️ **5 sym_sides / 5 switch×baseline cases** where a bool switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).
- Worst switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×5
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `AXSUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-2.303/F=-3.288
    - `SANDUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-7.397/F=-8.473
    - `MANAUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-6.233/F=-3.543
    - `CHRUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.091/F=-1.091
- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a `0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline (dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.
