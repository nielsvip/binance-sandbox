# Forward parity — LIVE vs VECTOR (auto, read-only)

generated 2026-10-06T18:24:41+00:00 · producers: tools/forward_parity/live_vs_vec.py (crypto hourly :07, stocks every 15 min RTH), tools/decisions_history_parity.py (:17), tools/exit_engine_parity_monitor.py (launchd hourly), tools/daily_parity_test.py (launchd daily)

PASS = every vector decision on the judged 15m bars has a live fill of the same class within +-2 bars, every live fill has a vector decision, and end state (in/out) agrees. FAIL classes: VEC_ONLY_LIVE_NO_SIGNAL (live logic did not fire), VEC_ONLY_LIVE_BLOCKED (live attempted, gate/execution stopped it), VEC_ONLY_STATE_CASCADE (consequence of an earlier divergence), LIVE_ONLY_VEC_FAMILY (vector twin exists but did not fire), LIVE_ONLY_NONVEC (1m/3m/5m/tick/webhook/portfolio input — no vec counterpart), STATE_MISMATCH, NPZ_STALE (S1 NPZ behind live, judged on the newest window the vector can see).

## Test 1 - 15m LIVE vs VECTOR, CRYPTO

run 2026-10-06T18:20:10+00:00 (5 min ago) · engine v12 918b5bdc · NPZ sync tail · window 24.0h from 2026-10-05T18:07Z · tol +-2 bars · 242 live keys / 131 sym_sides · 787.3 s

**Verdict: FAIL** — FAIL 112 · PASS with decisions 0 · IDLE (no decision either side, counts as pass) 110 · NO_DATA 20 · judged on a STALE-shifted window (S1 NPZ behind live) 222

- vector fired, live did not (VEC_ONLY): LIVE_ALREADY_FLAT 1180, LIVE_NO_SIGNAL 941, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 104, LIVE_ALREADY_IN_POSITION 87, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 59, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_GOOD 6, LIVE_ATTEMPT_NOT_FILLED:CRYPTO_SPIKE_FADE 1, LIVE_ATTEMPT_NOT_FILLED:EXIT_VELOCITY_WT 1
- live fired, vector did not (LIVE_ONLY): LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 6, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 6, LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m 3, LIVE_ONLY_NONVEC:GUARANTEED_REENTRY@3m 1, LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY 1, LIVE_ONLY_NONVEC:GAIN_EROSION_STOP@3m 1
- NPZ missing on S1 (no vector possible): 100PEPEUSDC, EDUUSDT, KMNOUSDT, MELANIAUSDT, NMRUSDT
- vector replay made 0 trades in 30D for 60/131 sym_sides with the live set (engine md5 918b5bdc): every live fill on those keys is LIVE_ONLY by construction — engine/set problem, not live drift. e.g. 1INCHUSDT_SHORT, ADAUSDC_SHORT, AGLDUSDT_SHORT, ALGOUSDT_LONG, ALGOUSDT_SHORT, APEUSDT_SHORT, API3USDT_SHORT, ARBUSDC_SHORT
- vector errors: prepare failed: no npz 13
- FAIL classes (sym_sides): NPZ_STALE 112, VEC_ONLY_STATE_CASCADE 92, VEC_ONLY_LIVE_NO_SIGNAL 91, VEC_ONLY_LIVE_BLOCKED 46, STATE_MISMATCH 42, LIVE_ONLY_NONVEC 7, LIVE_ONLY_VEC_FAMILY 6

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|
| ang:QNTUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 36, LIVE_NO_SIGNAL 32 | - | False/False | 2026-09-30T01:45Z..2026-10-01T01:45Z | vec_driven:progress:2e74fbeb |
| ang:GRAMUSDT_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 32, LIVE_ALREADY_FLAT 31 | - | True/False | 2026-09-30T01:30Z..2026-10-01T01:30Z | vec_driven:progress:a7c93e12 |
| flz:GRAMUSDT_LONG (VD) | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 32, LIVE_ALREADY_FLAT 31 | - | True/False | 2026-09-30T01:30Z..2026-10-01T01:30Z | vec_driven:progress:a7c93e12 |
| ang:RLCUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 28, LIVE_NO_SIGNAL 13, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 5 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:c5f4588a |
| fin:RLCUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 28, LIVE_NO_SIGNAL 13, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 5 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:c5f4588a |
| fin:XTZUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 40, LIVE_NO_SIGNAL 13 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:0638329a |
| inf:RLCUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 28, LIVE_NO_SIGNAL 25 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:c5f4588a |
| inf:XTZUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 40, LIVE_NO_SIGNAL 8, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 3 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:0638329a |
| men:RLCUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 28, LIVE_NO_SIGNAL 16, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 5 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:c5f4588a |
| men:XTZUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 40, LIVE_NO_SIGNAL 8, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 3 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:0638329a |
| ang:ZROUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 27, LIVE_NO_SIGNAL 21 | - | False/False | 2026-09-29T20:15Z..2026-09-30T20:15Z | defaults(cat_side) |
| inf:ZROUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 27, LIVE_NO_SIGNAL 21 | - | False/False | 2026-09-29T20:15Z..2026-09-30T20:15Z | defaults(cat_side) |
| men:ZROUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 27, LIVE_NO_SIGNAL 21 | - | False/False | 2026-09-29T20:15Z..2026-09-30T20:15Z | defaults(cat_side) |
| ang:AAVEUSDC_LONG (VD) | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 24, LIVE_NO_SIGNAL 12, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 6 | - | True/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:candidates:a72467 |
| inf:AAVEUSDC_LONG (VD) | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 24, LIVE_NO_SIGNAL 11, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 5 | - | True/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:candidates:a72467 |
| fin:COTIUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 25, LIVE_NO_SIGNAL 20 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven_MD5_MISMATCH:07b1 |
| inf:COTIUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 25, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 12, LIVE_NO_SIGNAL 5 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven_MD5_MISMATCH:07b1 |
| men:COTIUSDT_LONG (VD) | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 25, LIVE_ALREADY_IN_POSITION 20 | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven_MD5_MISMATCH:07b1 |
| ang:SNXUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 20, LIVE_NO_SIGNAL 15, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 5 | - | False/False | 2026-10-05T13:00Z..2026-10-06T13:00Z | vec_driven:progress:793e773a |
| fin:SNXUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 20, LIVE_NO_SIGNAL 19, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 1 | - | False/False | 2026-10-05T13:00Z..2026-10-06T13:00Z | vec_driven:progress:793e773a |
| men:SNXUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 20, LIVE_NO_SIGNAL 13, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 7 | - | False/False | 2026-10-05T13:00Z..2026-10-06T13:00Z | vec_driven:progress:793e773a |
| ang:EGLDUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 22, LIVE_NO_SIGNAL 13, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 2 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:27171403 |
| ang:MOVRUSDT_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 18, LIVE_NO_SIGNAL 14, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 5 | - | True/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | defaults(cat_side) |
| fin:EGLDUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 22, LIVE_NO_SIGNAL 15 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:27171403 |
| inf:EGLDUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 22, LIVE_NO_SIGNAL 10, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 4 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:27171403 |
| men:EGLDUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 22, LIVE_NO_SIGNAL 14, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 1 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:27171403 |
| ang:AIAUSDT_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 19, LIVE_NO_SIGNAL 13, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 2 | - | True/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:candidates:da58f5 |
| inf:AIAUSDT_LONG (VD) | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 19, LIVE_NO_SIGNAL 13, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 2 | - | True/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:candidates:da58f5 |
| flz:HYPEUSDT_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 16, LIVE_NO_SIGNAL 16 | - | False/True | 2026-10-05T13:00Z..2026-10-06T13:00Z | per_sym_active_config:clean_ |
| ang:ZENUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 15, LIVE_ALREADY_FLAT 15 | - | False/False | 2026-10-03T07:00Z..2026-10-04T07:00Z | per_sym_active_config:clean_ |
| men:ZENUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 15, LIVE_ALREADY_FLAT 15 | - | False/False | 2026-10-03T07:00Z..2026-10-04T07:00Z | per_sym_active_config:clean_ |
| fin:XLMUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 14, LIVE_ALREADY_FLAT 14 | - | False/False | 2026-10-05T13:00Z..2026-10-06T13:00Z | vec_driven:progress:a73eac61 |
| men:BBUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 14, LIVE_ALREADY_FLAT 14 | - | False/False | 2026-09-29T21:15Z..2026-09-30T21:15Z | defaults(cat_side) |
| men:XLMUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 14, LIVE_ALREADY_FLAT 14 | - | False/False | 2026-10-05T13:00Z..2026-10-06T13:00Z | vec_driven:progress:a73eac61 |
| men:ONEUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 13, LIVE_ALREADY_FLAT 13 | - | False/False | 2026-09-28T14:15Z..2026-09-29T14:15Z | defaults(cat_side) |
| men:DUSKUSDT_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 13, LIVE_ALREADY_FLAT 12 | - | True/False | 2026-09-28T14:15Z..2026-09-29T14:15Z | defaults(cat_side) |
| ang:RENDERUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 12, LIVE_ALREADY_FLAT 12 | - | False/False | 2026-09-29T21:00Z..2026-09-30T21:00Z | defaults(cat_side) |
| inf:RENDERUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 12, LIVE_ALREADY_FLAT 12 | - | False/False | 2026-09-29T21:00Z..2026-09-30T21:00Z | defaults(cat_side) |
| men:RENDERUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 12, LIVE_ALREADY_FLAT 12 | - | False/False | 2026-09-29T21:00Z..2026-09-30T21:00Z | defaults(cat_side) |
| ang:ZECUSDC_LONG | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY, NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 9, LIVE_NO_SIGNAL 6, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 2 | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 2, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 2 | True/False | 2026-10-05T13:00Z..2026-10-06T13:00Z | per_sym_active_config:clean_ |
| men:GALAUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 11, LIVE_NO_SIGNAL 8, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 3 | - | False/False | 2026-10-05T13:00Z..2026-10-06T13:00Z | defaults(cat_side) |
| flz:ZECUSDC_LONG | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY, NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 10, LIVE_NO_SIGNAL 9 | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 1, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 1 | True/True | 2026-10-05T13:00Z..2026-10-06T13:00Z | per_sym_active_config:clean_ |
| men:COMPUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 11, LIVE_NO_SIGNAL 9, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 1 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| men:ENJUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 13, LIVE_NO_SIGNAL 8 | - | False/False | 2026-10-03T06:45Z..2026-10-04T06:45Z | per_sym_active_config:clean_ |
| ang:AVAXUSDC_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 10, LIVE_ALREADY_FLAT 10 | - | False/False | 2026-10-01T00:30Z..2026-10-02T00:30Z | per_sym_active_config:clean_ |
| fin:AVAXUSDC_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 10, LIVE_ALREADY_FLAT 10 | - | False/False | 2026-10-01T00:30Z..2026-10-02T00:30Z | per_sym_active_config:clean_ |
| flz:SOLUSDC_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 10, LIVE_NO_SIGNAL 10 | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| men:AVAXUSDC_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 10, LIVE_ALREADY_FLAT 10 | - | False/False | 2026-10-01T00:30Z..2026-10-02T00:30Z | per_sym_active_config:clean_ |
| men:SOLUSDC_LONG | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 10, LIVE_NO_SIGNAL 9, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 1 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| ang:NEARUSDC_LONG | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 9, LIVE_NO_SIGNAL 7, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 2 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| ang:YFIUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 9, LIVE_NO_SIGNAL 6, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 2 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| fin:IOTXUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 9, LIVE_ALREADY_FLAT 9 | - | False/False | 2026-10-05T13:00Z..2026-10-06T13:00Z | vec_driven:progress:a761dc6d |
| fin:KSMUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 9, LIVE_NO_SIGNAL 5, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 2 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:e3f3ba65 |
| flz:DOGEUSDC_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 9, LIVE_ALREADY_FLAT 9 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| flz:ETHUSDC_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 9, LIVE_NO_SIGNAL 9 | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| inf:DOGEUSDC_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 9, LIVE_ALREADY_FLAT 9 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| inf:IOTXUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 9, LIVE_NO_SIGNAL 8, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 1 | - | False/False | 2026-10-05T13:00Z..2026-10-06T13:00Z | vec_driven:progress:a761dc6d |
| inf:KSMUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 9, LIVE_NO_SIGNAL 5, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 2 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:e3f3ba65 |
| inf:NEARUSDC_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 9, LIVE_ALREADY_FLAT 9 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| men:AXSUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 9, LIVE_NO_SIGNAL 8, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 1 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| men:ETHUSDC_LONG | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 9, LIVE_NO_SIGNAL 8, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 1 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| men:IOTXUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 9, LIVE_ALREADY_FLAT 9 | - | False/False | 2026-10-05T13:00Z..2026-10-06T13:00Z | vec_driven:progress:a761dc6d |
| men:KSMUSDT_LONG (VD) | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 9, LIVE_NO_SIGNAL 9 | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:e3f3ba65 |
| men:NEARUSDC_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 9, LIVE_ALREADY_FLAT 9 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| men:YFIUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 9, LIVE_NO_SIGNAL 7, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 2 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| men:ENSUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 8, LIVE_NO_SIGNAL 7, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 1 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | defaults(cat_side) |
| men:GRTUSDT_LONG | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 8, LIVE_NO_SIGNAL 4, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 2 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| men:LTCUSDC_LONG | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 8, LIVE_ALREADY_FLAT 8 | - | False/False | 2026-09-29T19:30Z..2026-09-30T19:30Z | per_sym_active_config:clean_ |
| ang:WLDUSDC_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 7, LIVE_NO_SIGNAL 6, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 2 | - | True/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:10196d09 |
| fin:1INCHUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 8, LIVE_NO_SIGNAL 6, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 1 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:candidates:c425b2 |
| flz:WLDUSDC_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 7, LIVE_NO_SIGNAL 5, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 3 | - | True/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:10196d09 |
| inf:WLDUSDC_LONG (VD) | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 7, LIVE_NO_SIGNAL 5, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 3 | - | True/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:10196d09 |
| men:1INCHUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 8, LIVE_NO_SIGNAL 6, LIVE_ATTEMPT_NOT_FILLED:GOLDEN_RULE 1 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:candidates:c425b2 |
| ang:SANDUSDT_LONG (VD) | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 6, LIVE_NO_SIGNAL 5, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 2 | - | True/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:3d463eab |
| fin:SANDUSDT_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 6, LIVE_NO_SIGNAL 5, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 2 | - | True/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:3d463eab |
| men:SANDUSDT_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 6, LIVE_ALREADY_FLAT 6, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 1 | - | True/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:3d463eab |
| ang:XMRUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_BLOCKED, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 6, LIVE_NO_SIGNAL 3, LIVE_ATTEMPT_NOT_FILLED:QUICK_OPEN_STRONG 1 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:cb297e6c |
| fin:BNBUSDC_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 5, LIVE_ALREADY_FLAT 5 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:candidates:ea9eb6 |
| flz:BNBUSDC_LONG (VD) | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 5, LIVE_NO_SIGNAL 5 | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:candidates:ea9eb6 |
| flz:XMRUSDT_LONG (VD) | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 6, LIVE_NO_SIGNAL 4 | - | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:cb297e6c |

... 32 more FAIL rows in `data/forward_parity/crypto_latest.json`

PASS with activity: -  ·  PASS_IDLE (no decision either side): 110

NO_DATA reasons: prepare failed: no npz 20

### Per function family (crypto)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| QUICK_OPEN_STRONG | 3m | Y | QUICK_OPEN_STRONG_VEC_ENABLED | 3062 | 6 | 0 | 0 | 0 | 6 | LIVE_ONLY(nonvec) |
| EXIT_VELOCITY_WT | 1h | Y | EXIT_VELOCITY_WT_ENABLED | 181 | 6 | 955 | 0 | 955 | 6 | FAIL |
| GOLDEN_RULE | 15m | Y | GOLDEN_RULE_ENABLED | 792 | 0 | 0 | 0 | 0 | 0 | IDLE |
| RANKING_DIRECT_ALL_GREEN | webhook | N | **UNGATED (verified: no switch)** | 510 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| HARDCODED_RALLY_REENTRY | 15m | Y | HARDCODED_RALLY_REENTRY_ENABLED | 5 | 0 | 323 | 0 | 323 | 0 | FAIL |
| UNCLASSIFIED:B | 15m | Y | ? (switch not identified) | 0 | 0 | 261 | 0 | 261 | 0 | FAIL |
| UNCLASSIFIED:B_KZONE | 15m | Y | ? (switch not identified) | 0 | 0 | 174 | 0 | 174 | 0 | FAIL |
| UNCLASSIFIED:WT_CROSS_EXIT | 15m | Y | ? (switch not identified) | 0 | 0 | 169 | 0 | 169 | 0 | FAIL |
| UNCLASSIFIED:B_SRS_ENTRY | 15m | Y | ? (switch not identified) | 0 | 0 | 143 | 0 | 143 | 0 | FAIL |
| BB_RECOVERY_EXIT | 15m | N | BB_RECOVERY_EXIT_ENABLED_TRADIER | 118 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:B_EMASLOPE | 15m | Y | ? (switch not identified) | 0 | 0 | 70 | 0 | 70 | 0 | FAIL |
| UNCLASSIFIED:ENTRY_SIGNAL | 15m | Y | ? (switch not identified) | 0 | 0 | 69 | 0 | 69 | 0 | FAIL |
| DC_DAYTRADE_TARGET | 15m | Y | DC_DAYTRADE_ENABLED | 10 | 0 | 33 | 0 | 33 | 0 | FAIL |
| CRYPTO_SPIKE_FADE | 3m | N | CRYPTO_SPIKE_FADE_ENABLED | 36 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_STRONG | 3m | N | ABLATION_DISABLE_QUICK_EXIT | 33 | 3 | 0 | 0 | 0 | 3 | LIVE_ONLY(nonvec) |
| WT_LOWER_CROSS_EXIT | 1h | Y | WT_LOWER_CROSS_EXIT_TF | 17 | 0 | 15 | 0 | 15 | 0 | FAIL |
| QUICK_REDUCE_OTHER | 3m | N | ABLATION_DISABLE_QUICK_EXIT | 30 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_OPEN_GOOD | 3m | N | ? (switch not identified) | 29 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| RANKING_DIRECT_ALL_RED | webhook | N | **UNGATED (verified: no switch)** | 27 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:MI_EXIT | 15m | Y | ? (switch not identified) | 0 | 0 | 23 | 0 | 23 | 0 | FAIL |
| PARTIAL_PROFIT_LOCK | tick | Y | PARTIAL_PROFIT_LOCK_ENABLED | 0 | 0 | 21 | 0 | 21 | 0 | FAIL |
| QUICK_REDUCE_NO_PROFIT | 1m | N | ABLATION_DISABLE_QUICK_EXIT | 19 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:STATEFUL_PORTED_EXIT | 15m | Y | ? (switch not identified) | 0 | 0 | 18 | 0 | 18 | 0 | FAIL |
| UNCLASSIFIED:B_VWAPBOUNCE | 15m | Y | ? (switch not identified) | 0 | 0 | 16 | 0 | 16 | 0 | FAIL |
| GUARANTEED_REENTRY | 3m | Y | ? (switch not identified) | 13 | 2 | 0 | 0 | 0 | 2 | LIVE_ONLY(nonvec) |
| WEBHOOK_HANDLE_SIGNAL | webhook | N | ? (switch not identified) | 14 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| ALL_TF_AGAINST_CLOSE | 15m | Y | ALL_TF_AGAINST_CLOSE_ENABLED | 0 | 0 | 14 | 0 | 14 | 0 | FAIL |
| UNCLASSIFIED:UAG_LADDER | 15m | Y | ? (switch not identified) | 0 | 0 | 12 | 0 | 12 | 0 | FAIL |
| UNCLASSIFIED:B_SMADIST | 15m | Y | ? (switch not identified) | 0 | 0 | 12 | 0 | 12 | 0 | FAIL |
| UNCLASSIFIED:B_MOM | 15m | Y | ? (switch not identified) | 0 | 0 | 11 | 0 | 11 | 0 | FAIL |
| RATIO_REBALANCE | portfolio | N | ABLATION_DISABLE_RATIO_REBALANCE | 9 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:MTF_GR_WT | 15m | Y | ? (switch not identified) | 5 | 0 | 3 | 0 | 3 | 0 | FAIL |
| UNCLASSIFIED:PULLBACK_AUG | 15m | Y | ? (switch not identified) | 0 | 0 | 8 | 0 | 8 | 0 | FAIL |
| UNCLASSIFIED:DD_BOUNCE_STOP | 15m | Y | ? (switch not identified) | 0 | 0 | 8 | 0 | 8 | 0 | FAIL |
| UNCLASSIFIED:B_EMADIST | 15m | Y | ? (switch not identified) | 0 | 0 | 7 | 0 | 7 | 0 | FAIL |
| QUICK_REDUCE_SCALP | 1m | N | ABLATION_DISABLE_SCALP_GUARD | 5 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| DC_BREACH_REDUCE | 15m | Y | ABLATION_DISABLE_DC_BREACH_REDUCE | 4 | 0 | 0 | 0 | 0 | 0 | IDLE |
| GAIN_EROSION_STOP | 3m | Y | ? (switch not identified) | 1 | 1 | 2 | 0 | 2 | 1 | FAIL |
| UNCLASSIFIED:LH_LL_TOP | 15m | Y | ? (switch not identified) | 0 | 0 | 4 | 0 | 4 | 0 | FAIL |
| DELTA_EXIT | 3m | N | DELTA_EXIT_ENABLED | 3 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:MTF_DC_REJECT | 15m | Y | ? (switch not identified) | 0 | 0 | 3 | 0 | 3 | 0 | FAIL |
| UNCLASSIFIED:NEWBORN_LOSS_KILL | 15m | Y | ? (switch not identified) | 0 | 0 | 3 | 0 | 3 | 0 | FAIL |
| REENTRY_OTHER | 15m | Y | ABLATION_DISABLE_REENTRY | 0 | 0 | 2 | 0 | 2 | 0 | FAIL |
| UNCLASSIFIED:BB_BOUNCE_ENTRY | 15m | N | ? (switch not identified) | 1 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |

## Test 2 - LIVE_ONLY functions (1m/3m/5m, tick, webhook, portfolio, no vec twin), CRYPTO

These decisions have no 15m vector counterpart by construction; listed so drift is visible (attempts = execute_now calls in the window).

| function family | input TF | attempts | fills | gate switch |
|---|---|---|---|---|
| QUICK_OPEN_STRONG | 3m | 3062 | 6 | QUICK_OPEN_STRONG_VEC_ENABLED |
| RANKING_DIRECT_ALL_GREEN | webhook | 510 | 0 | **UNGATED (verified: no switch)** |
| BB_RECOVERY_EXIT | 15m | 118 | 0 | BB_RECOVERY_EXIT_ENABLED_TRADIER |
| CRYPTO_SPIKE_FADE | 3m | 36 | 0 | CRYPTO_SPIKE_FADE_ENABLED |
| QUICK_REDUCE_STRONG | 3m | 33 | 3 | ABLATION_DISABLE_QUICK_EXIT |
| QUICK_REDUCE_OTHER | 3m | 30 | 0 | ABLATION_DISABLE_QUICK_EXIT |
| QUICK_OPEN_GOOD | 3m | 29 | 0 | ? (switch not identified) |
| RANKING_DIRECT_ALL_RED | webhook | 27 | 0 | **UNGATED (verified: no switch)** |
| QUICK_REDUCE_NO_PROFIT | 1m | 19 | 0 | ABLATION_DISABLE_QUICK_EXIT |
| WEBHOOK_HANDLE_SIGNAL | webhook | 14 | 0 | ? (switch not identified) |
| GUARANTEED_REENTRY | 3m | 13 | 2 | ? (switch not identified) |
| RATIO_REBALANCE | portfolio | 9 | 0 | ABLATION_DISABLE_RATIO_REBALANCE |
| QUICK_REDUCE_SCALP | 1m | 5 | 0 | ABLATION_DISABLE_SCALP_GUARD |
| DELTA_EXIT | 3m | 3 | 0 | DELTA_EXIT_ENABLED |
| GAIN_EROSION_STOP | 3m | 1 | 1 | ? (switch not identified) |
| UNCLASSIFIED:BB_BOUNCE_ENTRY | 15m | 1 | 0 | ? (switch not identified) |

## Test 3 - VEC_DRIVEN bridge (S1 vec intents -> live fills)

intents in window: 2 · result: NOT_IN_MAC_REGISTRY 2 · consumer files: **none (live consumer has not written data/vec_live/live_exec_*.jsonl)**

| bar | ss | acct | type | vec reason | result |
|---|---|---|---|---|---|
| 2026-10-06T03:30Z | 1000BONKUSDC_SHORT | - | CLOSE | E_3_STRUCTURE_EXIT_2TF_15m_4h | NOT_IN_MAC_REGISTRY |
| 2026-10-06T05:45Z | ADAUSDC_SHORT | - | OPEN | B14 | NOT_IN_MAC_REGISTRY |

## Test 1 - 15m LIVE vs VECTOR, STOCKS

run 2026-10-06T18:24:40+00:00 (0 min ago) · engine v12 918b5bdc · NPZ sync tail · window 24.0h from 2026-10-05T18:20Z · tol +-2 bars · 148 live keys / 129 sym_sides · 266.9 s

**Verdict: FAIL** — FAIL 70 · PASS with decisions 3 · IDLE (no decision either side, counts as pass) 71 · NO_DATA 4 · judged on a STALE-shifted window (S1 NPZ behind live) 5

- vector fired, live did not (VEC_ONLY): LIVE_NO_SIGNAL 5, LIVE_ALREADY_FLAT 5, LIVE_ALREADY_IN_POSITION 1
- live fired, vector did not (LIVE_ONLY): -
- vector replay made 0 trades in 30D for 60/129 sym_sides with the live set (engine md5 918b5bdc): every live fill on those keys is LIVE_ONLY by construction — engine/set problem, not live drift. e.g. AAPL_LONG, ACN_LONG, AMAT_LONG, AMZN_LONG, AMZN_SHORT, APA_LONG, ASML_LONG, AXTI_LONG
- vector errors: prepare failed: ValueError: NPZ has only 19 distinct stock s 2, prepare failed: ValueError: NPZ has only 20 distinct stock s 1, prepare failed: ValueError: NPZ has only 18 distinct stock s 1
- FAIL classes (sym_sides): STATE_MISMATCH 65, VEC_ONLY_STATE_CASCADE 6, VEC_ONLY_LIVE_NO_SIGNAL 5, NPZ_STALE 2

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|
| trb:UEC_SHORT | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 1, LIVE_ALREADY_FLAT 1 | - | False/False | last window | trb/active_config |
| trc:UEC_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 1, LIVE_ALREADY_FLAT 1 | - | False/True | last window | trb/active_config |
| trb:ADBE_SHORT | VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | True/True | last window | trb/active_config |
| trb:AMD_LONG | VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | last window | trb/active_config |
| trb:IBIT_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | False/True | last window | trb/active_config |
| trb:LEXX_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | True/False | last window | defaults(cat_side) |
| trb:QLYS_LONG | NPZ_STALE, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | 2026-09-10T16:00Z..2026-09-11T16:00Z | defaults(cat_side) |
| trc:ADBE_SHORT | VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 1 | - | True/True | last window | trb/active_config |
| trc:IBIT_LONG | STATE_MISMATCH, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/True | last window | trb/active_config |
| trb:AAPL_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:ABT_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:ALKT_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:AR_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:AXTI_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:BABA_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:BIDU_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:BKR_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:BMNR_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:BWXT_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:CF_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:CLS_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:COIN_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:COPX_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:CRWD_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:CVS_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:EQT_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:EXEL_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:FANG_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:GE_SHORT | NPZ_STALE, STATE_MISMATCH | - | - | True/False | 2026-09-10T16:00Z..2026-09-11T16:00Z | defaults(cat_side) |
| trb:GM_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:GOOGL_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:HD_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:HOOD_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:LMT_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:MNTS_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:MOS_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:MSFT_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:MU_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:NFLX_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:NKE_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:NOC_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:NUE_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:NVDA_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:NXE_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:PEP_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:PLTR_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:PYPL_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:QQQ_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:RBLX_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:RTX_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:SBUX_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:SLB_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:SNDK_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:TDG_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:TSLA_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:TSM_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:UNH_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:UPS_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:VALE_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:ZCSH_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trc:AXON_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:A_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:BWXT_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:EQT_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:MU_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:NKE_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:NVDA_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:PSX_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:RRC_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:VLO_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |

PASS with activity: trb:COHR_LONG, trb:PSX_LONG, trc:META_LONG  ·  PASS_IDLE (no decision either side): 71

NO_DATA reasons: prepare failed: ValueError: NPZ has only 19 distin 2, prepare failed: ValueError: NPZ has only 20 distin 1, prepare failed: ValueError: NPZ has only 18 distin 1

### Per function family (stocks)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| BROKER_SYNC | tick | N | safety (exempt) | 0 | 178 | 0 | 0 | 0 | 0 | PASS |
| DC_DAYTRADE_TARGET | 15m | Y | DC_DAYTRADE_ENABLED | 0 | 1 | 3 | 0 | 3 | 0 | FAIL |
| DC_BREAK | 15m | N | ? (switch not identified) | 0 | 3 | 0 | 0 | 0 | 0 | PASS |
| HARDCODED_RALLY_REENTRY | 15m | Y | HARDCODED_RALLY_REENTRY_ENABLED | 0 | 1 | 2 | 0 | 2 | 0 | FAIL |
| UNCLASSIFIED:B_EMASLOPE | 15m | Y | ? (switch not identified) | 0 | 0 | 2 | 0 | 2 | 0 | FAIL |
| REENTRY_OTHER | 15m | Y | ABLATION_DISABLE_REENTRY | 0 | 2 | 0 | 0 | 0 | 0 | PASS |
| GAP_MOC | 5m | Y | ? (switch not identified) | 0 | 0 | 2 | 0 | 2 | 0 | FAIL |
| UNCLASSIFIED:PRE_CLOSE | 15m | N | ? (switch not identified) | 0 | 1 | 0 | 0 | 0 | 0 | PASS |
| UNCLASSIFIED:B | 15m | Y | ? (switch not identified) | 0 | 0 | 1 | 0 | 1 | 0 | FAIL |
| DC_DAYTRADE_STOP | 15m | Y | DC_DAYTRADE_ENABLED | 0 | 0 | 1 | 0 | 1 | 0 | FAIL |

## Test 2 - LIVE_ONLY functions (1m/3m/5m, tick, webhook, portfolio, no vec twin), STOCKS

These decisions have no 15m vector counterpart by construction; listed so drift is visible (attempts = execute_now calls in the window).

| function family | input TF | attempts | fills | gate switch |
|---|---|---|---|---|

## decisions -> history parity (switch intents vs live fills)

`DH_20261005_20261006.json` generated 2026-10-06T18:17:02.821182+00:00 (8 min ago) · intents 448 · matched 35 · **missing 413**

matched/intents per account: fin 4/9, men 8/37, ang 14/27, inf 0/0 (no decision file), flz 9/10, trb 0/201, trc 0/164

| function family | action | intent result | n | blocking execution filter | vector same decision +-2 bars (Y/N/? = not judged) |
|---|---|---|---|---|---|
| UNCLASSIFIED:? | OPEN | MISSING | 92 | UNATTRIBUTED 92 | ? 91, N 1 |
| UNCLASSIFIED:? | REDUCE | MISSING | 89 | UNATTRIBUTED 89 | ? 89 |
| UNCLASSIFIED:? | CLOSE | MISSING | 57 | UNATTRIBUTED 57 | ? 57 |
| UNCLASSIFIED:? | REENTRY | MISSING | 48 | UNATTRIBUTED 48 | ? 48 |
| UNCLASSIFIED:? | WAIT | MISSING | 30 | UNATTRIBUTED 30 | ? 30 |
| UNCLASSIFIED:? | SHORT SELL | MISSING | 23 | UNATTRIBUTED 23 | ? 23 |
| UNCLASSIFIED:? | LONG BUY | MISSING | 21 | UNATTRIBUTED 21 | ? 21 |
| BB_RECOVERY_EXIT | REDUCE | MISSING | 18 | NO_QUANTITY_LEFT_TO_REDUCE 5, BLOCKED_MAKER_SUPPRESS_WEBHOOK 5, HARD_REDUCE_LOCK 4, NUKE_STALE_MAKER_ORDER 3 | ? 18 |
| EXIT_VELOCITY_WT | CLOSE | MISSING | 10 | NUKE_STALE_MAKER_ORDER 10 | N 9, ? 1 |
| WT_LOWER_CROSS_EXIT | CLOSE | MISSING | 5 | UNATTRIBUTED 3, NUKE_STALE_MAKER_ORDER 2 | ? 4, N 1 |
| QUICK_REDUCE_STRONG | STRONG_REDUCE | MISSING | 4 | NUKE_STALE_MAKER_ORDER 4 | N 4 |
| QUICK_REDUCE_OTHER | REDUCE | MISSING | 3 | UNATTRIBUTED 3 | N 3 |
| GOLDEN_RULE | OPEN | MISSING | 2 | MAKER_FAILED_SUPPRESS_WEBHOOK 2 | ? 2 |
| RATIO_REBALANCE | REDUCE | MISSING | 2 | NUKE_STALE_MAKER_ORDER 1, BLOCKED_MAKER_SUPPRESS_WEBHOOK 1 | ? 2 |
| DC_BREACH_REDUCE | CLOSE | MISSING | 2 | UNATTRIBUTED 2 | N 2 |
| QUICK_OPEN_STRONG | QUICK_OPEN | MISSING | 2 | MAKER_ZERO_QTY 2 | N 2 |
| UNCLASSIFIED:? | 🔫 HEAVY_ART | MISSING | 2 | UNATTRIBUTED 2 | ? 2 |
| UNCLASSIFIED:? | 🔫 HEAVY_ART_S | MISSING | 2 | UNATTRIBUTED 2 | ? 2 |
| UNCLASSIFIED:? | 💥CLOSE | MISSING | 1 | UNATTRIBUTED 1 | ? 1 |
| EXIT_VELOCITY_WT | CLOSE | MATCHED | 8 | filled 8 | N 6, ? 2 |
| QUICK_OPEN_STRONG | QUICK_OPEN | MATCHED | 7 | filled 7 | N 6, ? 1 |
| GOLDEN_RULE | OPEN | MATCHED | 6 | filled 6 | ? 6 |
| BB_RECOVERY_EXIT | REDUCE | MATCHED | 4 | filled 4 | ? 4 |
| QUICK_REDUCE_STRONG | STRONG_REDUCE | MATCHED | 3 | filled 3 | N 3 |
| WT_LOWER_CROSS_EXIT | CLOSE | MATCHED | 2 | filled 2 | ? 2 |
| GUARANTEED_REENTRY | OPEN | MATCHED | 2 | filled 2 | N 2 |
| RANKING_DIRECT_ALL_GREEN | CLOSE | MATCHED | 1 | filled 1 | ? 1 |
| DC_DAYTRADE_TARGET | CLOSE | MATCHED | 1 | filled 1 | ? 1 |
| GAIN_EROSION_STOP | CLOSE | MATCHED | 1 | filled 1 | N 1 |

Gap classes: NUKE_STALE_MAKER_ORDER = maker exit rested >60 s and was cancelled (microstructure, no vec twin); UNATTRIBUTED = no blocking log line found (logging hook missing in the execution path); MAKER_ZERO_QTY / MARKET_API_ERROR = sizing/broker. Operator decisions: `data/decisions_history_parity/NEEDS-OPERATOR-DECISION_*.md`.

## Exit-engine gate parity (execute_now, all exits)

generated 2026-10-06T18:00:17.269906+00:00 (24 min ago) · lookback 48.0h · exit rows 6207 · **LEAKS 3295** · gate ON 1789 · UNGATED 1035 · safety 4 · unmapped 6

LEAKS (gate OFF for that sym_side yet fired): QUICK_OPEN_STRONG 3293, WT_DC_ENTRY (WT_DC_ENTRY_ENABLED=False) 2

UNGATED families (fire with no switch — live-side switch hook missing, cannot be turned off): RANKING_DIRECT_ALL_GREEN 1007, RANKING_DIRECT_ALL_RED 28

## 7-day rollup (tools/daily_parity_test.py)

`data/parity/daily_parity_20261006.md`

- Generated: 2026-10-06T06:15:12+00:00 | engine: tools/forward_parity/live_vs_vec.py (same bars, same per-sym set)
- crypto: 139 live keys · status {'PASS_IDLE': 98, 'NO_DATA': 13, 'STALE_PASS_IDLE': 11, 'FAIL': 17} · VEC_ONLY {'LIVE_ALREADY_IN_POSITION': 3, 'LIVE_NO_SIGNAL': 3} · LIVE_ONLY {'LIVE_ONLY_VEC_FAMILY:GOLDEN_RULE': 15, 'LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT': 7, 'LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m': 6, 'LIVE_ONLY_NONVEC:RANKING_DIRECT_ALL_GREEN@webhook': 5, 'LIVE_ONLY_VEC_FAMILY:DC_DAYTRADE_TARGET': 4, 'LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m': 3, 'LIVE_ONLY_VEC_FAMILY:WT_LOWER_CROSS_EXIT': 1, 'LIVE_ONLY_NONVEC:RATIO_REBALANCE@portfolio': 1, 'LIVE_ONLY_NONVEC:BREAK_EVEN_GUARD@tick': 1, 'LIVE_ONLY_VEC_FAMILY:MTF_BB_REJECT_EXIT': 1, 'LIVE_ONLY_NONVEC:ALL_TF_AGAINST_CLOSE@3m': 1, 'LIVE_ONLY_NONVEC:GUARANTEED_REENTRY@3m': 1, 'LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY': 1, 'LIVE_ONLY_NONVEC:GAIN_EROSION_STOP@3m': 1}
- stocks: 175 live keys · status {'FAIL': 151, 'PASS_IDLE': 19, 'NO_DATA': 4, 'STALE_PASS_IDLE': 1} · VEC_ONLY {'LIVE_NO_SIGNAL': 208, 'LIVE_ALREADY_FLAT': 162, 'LIVE_ALREADY_IN_POSITION': 28, 'LIVE_INTENT_NOT_FILLED:UNCLASSIFIED:?': 2} · LIVE_ONLY {'LIVE_ONLY_NONVEC:REENTRY_OTHER@5m': 226, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:SYNC_DETECTION@15m': 71, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:MFI_MEAN_REVERSION@15m': 57, 'LIVE_ONLY_VEC_FAMILY:REENTRY_OTHER': 47, 'LIVE_ONLY_NONVEC:FALLBACK_CLOSE@15m': 27, 'LIVE_ONLY_NONVEC:GAP_MOC@5m': 24, 'LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY': 23, 'LIVE_ONLY_VEC_FAMILY:DC_DAYTRADE_TARGET': 21, 'LIVE_ONLY_VEC_FAMILY:HARDCODED_RALLY_REENTRY': 18, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TARGET@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DC_BREAK_HIGH@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:ULTIMATE_DC_H@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@15m': 11, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:INTRADAY_RATIO_TRIM@15m': 5, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:STRUCT_BREAK_DC@15m': 5, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:SENT_STRAT_DIV@15m': 3, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:OVERBOUGHT_TAKE_PROFIT@15m': 2, 'LIVE_ONLY_NONVEC:GAP_FILL@5m': 2, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:GR_HTF_DIRECT@3m': 2, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:NOLOSS_BBH_BREAKDOWN@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@3m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@5m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:ROTATION_ENTRY_L@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:CLENOW_ENTRY@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:STRUCT_BREAK_DC@5m': 1}
- Totals: PASS 129 · FAIL 168 · sign flips (>=5 exits each side) 0 · alerts 86
