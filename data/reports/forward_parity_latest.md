# Forward parity — LIVE vs VECTOR (auto, read-only)

generated 2026-10-06T16:34:16+00:00 · producers: tools/forward_parity/live_vs_vec.py (crypto hourly :07, stocks every 15 min RTH), tools/decisions_history_parity.py (:17), tools/exit_engine_parity_monitor.py (launchd hourly), tools/daily_parity_test.py (launchd daily)

PASS = every vector decision on the judged 15m bars has a live fill of the same class within +-2 bars, every live fill has a vector decision, and end state (in/out) agrees. FAIL classes: VEC_ONLY_LIVE_NO_SIGNAL (live logic did not fire), VEC_ONLY_LIVE_BLOCKED (live attempted, gate/execution stopped it), VEC_ONLY_STATE_CASCADE (consequence of an earlier divergence), LIVE_ONLY_VEC_FAMILY (vector twin exists but did not fire), LIVE_ONLY_NONVEC (1m/3m/5m/tick/webhook/portfolio input — no vec counterpart), STATE_MISMATCH, NPZ_STALE (S1 NPZ behind live, judged on the newest window the vector can see).

## Test 1 - 15m LIVE vs VECTOR, CRYPTO

run 2026-10-06T16:21:27+00:00 (13 min ago) · engine v12 d84863e5 · NPZ sync tail · window 24.0h from 2026-10-05T16:07Z · tol +-2 bars · 242 live keys / 131 sym_sides · 865.1 s

**Verdict: FAIL** — FAIL 29 · PASS with decisions 0 · IDLE (no decision either side, counts as pass) 193 · NO_DATA 20 · judged on a STALE-shifted window (S1 NPZ behind live) 222

- vector fired, live did not (VEC_ONLY): LIVE_NO_SIGNAL 14, LIVE_ALREADY_IN_POSITION 7, LIVE_ALREADY_FLAT 7
- live fired, vector did not (LIVE_ONLY): LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 6, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 6, LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m 3, LIVE_ONLY_NONVEC:GUARANTEED_REENTRY@3m 1, LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY 1, LIVE_ONLY_NONVEC:GAIN_EROSION_STOP@3m 1
- NPZ missing on S1 (no vector possible): 100PEPEUSDC, EDUUSDT, KMNOUSDT, MELANIAUSDT, NMRUSDT
- vector replay made 0 trades in 30D for 110/131 sym_sides with the live set (engine md5 d84863e5): every live fill on those keys is LIVE_ONLY by construction — engine/set problem, not live drift. e.g. 1000BONKUSDC_LONG, 1INCHUSDT_LONG, 1INCHUSDT_SHORT, AAVEUSDC_LONG, ADAUSDC_LONG, ADAUSDC_SHORT, AGLDUSDT_SHORT, ALGOUSDT_LONG
- vector errors: prepare failed: no npz 13
- FAIL classes (sym_sides): NPZ_STALE 29, STATE_MISMATCH 23, LIVE_ONLY_NONVEC 7, LIVE_ONLY_VEC_FAMILY 6, VEC_ONLY_LIVE_NO_SIGNAL 2, VEC_ONLY_STATE_CASCADE 2

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|
| flz:XRPUSDC_SHORT | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 7, LIVE_NO_SIGNAL 7 | - | False/True | 2026-10-05T13:00Z..2026-10-06T13:00Z | per_sym_active_config:clean_ |
| men:XRPUSDC_SHORT | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 7, LIVE_ALREADY_FLAT 7 | - | False/False | 2026-10-05T13:00Z..2026-10-06T13:00Z | per_sym_active_config:clean_ |
| ang:ZECUSDC_LONG | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY, NPZ_STALE | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 2, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 2 | False/False | 2026-10-05T13:00Z..2026-10-06T13:00Z | per_sym_active_config:clean_ |
| flz:SOLUSDC_SHORT | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY, NPZ_STALE | - | LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY 1, LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m 1, LIVE_ONLY_NONVEC:GAIN_EROSION_STOP@3m 1 | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| ang:GALAUSDT_SHORT | LIVE_ONLY_NONVEC, NPZ_STALE, STATE_MISMATCH | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 1, LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m 1 | False/True | 2026-10-05T13:00Z..2026-10-06T13:00Z | per_sym_active_config:clean_ |
| fin:THETAUSDT_LONG | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY, NPZ_STALE | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 1, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 1 | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:f4144e33 |
| flz:ETHUSDC_SHORT | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY, NPZ_STALE | - | LIVE_ONLY_NONVEC:GUARANTEED_REENTRY@3m 1, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 1 | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| flz:ZECUSDC_LONG | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY, NPZ_STALE, STATE_MISMATCH | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 1, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 1 | False/True | 2026-10-05T13:00Z..2026-10-06T13:00Z | per_sym_active_config:clean_ |
| men:GALAUSDT_SHORT | LIVE_ONLY_NONVEC, NPZ_STALE, STATE_MISMATCH | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 1, LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m 1 | False/True | 2026-10-05T13:00Z..2026-10-06T13:00Z | per_sym_active_config:clean_ |
| men:VETUSDT_LONG | LIVE_ONLY_VEC_FAMILY, NPZ_STALE | - | LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 1 | False/False | 2026-10-05T12:45Z..2026-10-06T12:45Z | defaults(cat_side) |
| ang:ATOMUSDT_SHORT | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| fin:ALGOUSDT_SHORT (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:candidates:ed033e |
| flz:BNBUSDC_LONG (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:candidates:ea9eb6 |
| flz:BTCUSDC_LONG (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:candidates:f6f02f |
| flz:DASHUSDT_SHORT | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T13:00Z..2026-10-06T13:00Z | per_sym_active_config:clean_ |
| flz:ETHUSDC_LONG | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| flz:GRAMUSDT_SHORT | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-09-30T01:30Z..2026-10-01T01:30Z | defaults(cat_side) |
| flz:HYPEUSDT_LONG | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T13:00Z..2026-10-06T13:00Z | per_sym_active_config:clean_ |
| flz:HYPEUSDT_SHORT | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T13:00Z..2026-10-06T13:00Z | per_sym_active_config:clean_ |
| flz:SOLUSDC_LONG | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| inf:ACEUSDT_SHORT (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:3cfa64df |
| inf:IOTAUSDT_LONG (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T13:00Z..2026-10-06T13:00Z | vec_driven:progress:07b10a2f |
| inf:SANDUSDT_SHORT | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | per_sym_active_config:clean_ |
| men:ADAUSDC_LONG | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:candidates:c37700 |
| men:ALGOUSDT_SHORT (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:candidates:ed033e |
| men:COTIUSDT_LONG (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven_MD5_MISMATCH:07b1 |
| men:KSMUSDT_LONG (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:e3f3ba65 |
| men:LINKUSDC_LONG (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T12:45Z..2026-10-06T12:45Z | vec_driven:progress:558e933b |
| men:XRPUSDC_LONG | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-05T13:00Z..2026-10-06T13:00Z | per_sym_active_config:clean_ |

PASS with activity: -  ·  PASS_IDLE (no decision either side): 193

NO_DATA reasons: prepare failed: no npz 20

### Per function family (crypto)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| QUICK_OPEN_STRONG | 3m | Y | QUICK_OPEN_STRONG_VEC_ENABLED | 2890 | 6 | 0 | 0 | 0 | 6 | LIVE_ONLY(nonvec) |
| GOLDEN_RULE | 15m | Y | GOLDEN_RULE_ENABLED | 728 | 0 | 0 | 0 | 0 | 0 | IDLE |
| RANKING_DIRECT_ALL_GREEN | webhook | N | **UNGATED (verified: no switch)** | 510 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| EXIT_VELOCITY_WT | 1h | Y | EXIT_VELOCITY_WT_ENABLED | 181 | 6 | 14 | 0 | 14 | 6 | FAIL |
| BB_RECOVERY_EXIT | 15m | N | BB_RECOVERY_EXIT_ENABLED_TRADIER | 118 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_STRONG | 3m | N | ABLATION_DISABLE_QUICK_EXIT | 33 | 3 | 0 | 0 | 0 | 3 | LIVE_ONLY(nonvec) |
| CRYPTO_SPIKE_FADE | 3m | N | CRYPTO_SPIKE_FADE_ENABLED | 31 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_OTHER | 3m | N | ABLATION_DISABLE_QUICK_EXIT | 30 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_OPEN_GOOD | 3m | N | ? (switch not identified) | 28 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| RANKING_DIRECT_ALL_RED | webhook | N | **UNGATED (verified: no switch)** | 27 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_NO_PROFIT | 1m | N | ABLATION_DISABLE_QUICK_EXIT | 19 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| WT_LOWER_CROSS_EXIT | 1h | Y | WT_LOWER_CROSS_EXIT_TF | 17 | 0 | 0 | 0 | 0 | 0 | IDLE |
| GUARANTEED_REENTRY | 3m | Y | ? (switch not identified) | 13 | 2 | 0 | 0 | 0 | 2 | LIVE_ONLY(nonvec) |
| WEBHOOK_HANDLE_SIGNAL | webhook | N | ? (switch not identified) | 14 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| HARDCODED_RALLY_REENTRY | 15m | Y | HARDCODED_RALLY_REENTRY_ENABLED | 5 | 0 | 8 | 0 | 8 | 0 | FAIL |
| DC_DAYTRADE_TARGET | 15m | Y | DC_DAYTRADE_ENABLED | 10 | 0 | 0 | 0 | 0 | 0 | IDLE |
| RATIO_REBALANCE | portfolio | N | ABLATION_DISABLE_RATIO_REBALANCE | 9 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:B_KZONE | 15m | Y | ? (switch not identified) | 0 | 0 | 6 | 0 | 6 | 0 | FAIL |
| QUICK_REDUCE_SCALP | 1m | N | ABLATION_DISABLE_SCALP_GUARD | 5 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| DC_BREACH_REDUCE | 15m | Y | ABLATION_DISABLE_DC_BREACH_REDUCE | 4 | 0 | 0 | 0 | 0 | 0 | IDLE |
| UNCLASSIFIED:MTF_GR_WT | 15m | N | ? (switch not identified) | 4 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| DELTA_EXIT | 3m | N | DELTA_EXIT_ENABLED | 3 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| GAIN_EROSION_STOP | 3m | N | ? (switch not identified) | 1 | 1 | 0 | 0 | 0 | 1 | LIVE_ONLY(nonvec) |

## Test 2 - LIVE_ONLY functions (1m/3m/5m, tick, webhook, portfolio, no vec twin), CRYPTO

These decisions have no 15m vector counterpart by construction; listed so drift is visible (attempts = execute_now calls in the window).

| function family | input TF | attempts | fills | gate switch |
|---|---|---|---|---|
| QUICK_OPEN_STRONG | 3m | 2890 | 6 | QUICK_OPEN_STRONG_VEC_ENABLED |
| RANKING_DIRECT_ALL_GREEN | webhook | 510 | 0 | **UNGATED (verified: no switch)** |
| BB_RECOVERY_EXIT | 15m | 118 | 0 | BB_RECOVERY_EXIT_ENABLED_TRADIER |
| QUICK_REDUCE_STRONG | 3m | 33 | 3 | ABLATION_DISABLE_QUICK_EXIT |
| CRYPTO_SPIKE_FADE | 3m | 31 | 0 | CRYPTO_SPIKE_FADE_ENABLED |
| QUICK_REDUCE_OTHER | 3m | 30 | 0 | ABLATION_DISABLE_QUICK_EXIT |
| QUICK_OPEN_GOOD | 3m | 28 | 0 | ? (switch not identified) |
| RANKING_DIRECT_ALL_RED | webhook | 27 | 0 | **UNGATED (verified: no switch)** |
| QUICK_REDUCE_NO_PROFIT | 1m | 19 | 0 | ABLATION_DISABLE_QUICK_EXIT |
| WEBHOOK_HANDLE_SIGNAL | webhook | 14 | 0 | ? (switch not identified) |
| GUARANTEED_REENTRY | 3m | 13 | 2 | ? (switch not identified) |
| RATIO_REBALANCE | portfolio | 9 | 0 | ABLATION_DISABLE_RATIO_REBALANCE |
| QUICK_REDUCE_SCALP | 1m | 5 | 0 | ABLATION_DISABLE_SCALP_GUARD |
| UNCLASSIFIED:MTF_GR_WT | 15m | 4 | 0 | ? (switch not identified) |
| DELTA_EXIT | 3m | 3 | 0 | DELTA_EXIT_ENABLED |
| GAIN_EROSION_STOP | 3m | 1 | 1 | ? (switch not identified) |

## Test 3 - VEC_DRIVEN bridge (S1 vec intents -> live fills)

intents in window: 2 · result: NOT_IN_MAC_REGISTRY 2 · consumer files: **none (live consumer has not written data/vec_live/live_exec_*.jsonl)**

execute_now gate refusals tagged VEC_DRIVEN in ez_manage logs: {'flz': {'GOLDEN_RULE': 1}}

| bar | ss | acct | type | vec reason | result |
|---|---|---|---|---|---|
| 2026-10-06T03:30Z | 1000BONKUSDC_SHORT | - | CLOSE | E_3_STRUCTURE_EXIT_2TF_15m_4h | NOT_IN_MAC_REGISTRY |
| 2026-10-06T05:45Z | ADAUSDC_SHORT | - | OPEN | B14 | NOT_IN_MAC_REGISTRY |

## Test 1 - 15m LIVE vs VECTOR, STOCKS

run 2026-10-06T16:34:13+00:00 (0 min ago) · engine v12 45b890a3 · NPZ sync tail · window 24.0h from 2026-10-05T16:22Z · tol +-2 bars · 148 live keys / 129 sym_sides · 729.8 s

**Verdict: FAIL** — FAIL 82 · PASS with decisions 3 · IDLE (no decision either side, counts as pass) 59 · NO_DATA 4 · judged on a STALE-shifted window (S1 NPZ behind live) 5

- vector fired, live did not (VEC_ONLY): LIVE_NO_SIGNAL 48, LIVE_ALREADY_FLAT 40, LIVE_ALREADY_IN_POSITION 5
- live fired, vector did not (LIVE_ONLY): -
- vector replay made 0 trades in 30D for 24/129 sym_sides with the live set (engine md5 45b890a3): every live fill on those keys is LIVE_ONLY by construction — engine/set problem, not live drift. e.g. AMZN_SHORT, APA_LONG, A_LONG, BA_SHORT, BNO_LONG, BWXT_SHORT, CIBR_LONG, COHR_LONG
- vector errors: prepare failed: ValueError: NPZ has only 19 distinct stock s 2, prepare failed: ValueError: NPZ has only 20 distinct stock s 1, prepare failed: ValueError: NPZ has only 18 distinct stock s 1
- FAIL classes (sym_sides): STATE_MISMATCH 73, VEC_ONLY_LIVE_NO_SIGNAL 17, VEC_ONLY_STATE_CASCADE 15, NPZ_STALE 4

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|
| trb:SPY_LONG | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 8, LIVE_ALREADY_FLAT 8 | - | False/False | last window | trb/active_config |
| trb:VT_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 8, LIVE_NO_SIGNAL 8 | - | True/False | last window | trb/active_config |
| trb:GOOGL_LONG | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 6, LIVE_ALREADY_FLAT 5 | - | True/True | last window | trb/active_config |
| trb:ADBE_SHORT | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 5, LIVE_ALREADY_FLAT 4 | - | True/True | last window | trb/active_config |
| trc:ADBE_SHORT | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 5, LIVE_NO_SIGNAL 4 | - | True/True | last window | trb/active_config |
| trb:QRVO_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 4, LIVE_ALREADY_FLAT 4 | - | True/False | 2026-09-10T16:00Z..2026-09-11T16:00Z | per_sym_active_config_stocks |
| trb:QCOM_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 3, LIVE_ALREADY_FLAT 3 | - | True/False | 2026-09-10T16:00Z..2026-09-11T16:00Z | defaults(cat_side) |
| trb:SHOP_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 1, LIVE_ALREADY_FLAT 1 | - | True/False | last window | defaults(cat_side) |
| trb:UEC_SHORT | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 1, LIVE_ALREADY_FLAT 1 | - | False/False | last window | trb/active_config |
| trc:UEC_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 1, LIVE_ALREADY_FLAT 1 | - | False/True | last window | trb/active_config |
| trb:AAPL_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | True/False | last window | trb/active_config |
| trb:AMAT_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | True/False | last window | defaults(cat_side) |
| trb:AMD_LONG | VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | last window | trb/active_config |
| trb:FIX_LONG | STATE_MISMATCH, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | True/False | last window | defaults(cat_side) |
| trb:GME_LONG | VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | last window | defaults(cat_side) |
| trb:IBIT_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | False/True | last window | trb/active_config |
| trb:LEXX_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | True/False | last window | defaults(cat_side) |
| trb:LMT_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | True/False | last window | trb/active_config |
| trb:MSTR_LONG | VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | last window | trb/active_config |
| trb:SMCI_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | True/False | last window | defaults(cat_side) |
| trb:VICR_LONG | NPZ_STALE, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | 2026-09-10T19:30Z..2026-09-11T19:30Z | defaults(cat_side) |
| trc:IBIT_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | False/True | last window | trb/active_config |
| trb:AAPL_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:ABT_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:ALKT_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:AMZN_LONG | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:AR_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:ASML_LONG | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:BABA_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:BIDU_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:BKR_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:BMNR_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:BWXT_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:CF_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:COHR_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:COIN_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:COPX_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:CVS_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:EQT_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:FANG_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:GE_SHORT | NPZ_STALE, STATE_MISMATCH | - | - | True/False | 2026-09-10T16:00Z..2026-09-11T16:00Z | defaults(cat_side) |
| trb:GM_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:HD_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:LITE_LONG | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:LSCC_LONG | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:MNTS_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:MOS_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:MRVL_LONG | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:MU_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:NFLX_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:NKE_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:NOC_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:NUE_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:NXE_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:OLED_LONG | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:PEP_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:PYPL_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:QQQ_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:RBLX_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:RTX_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:SBUX_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:SLB_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:SNDK_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:SNOW_LONG | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:TDG_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:TMO_LONG | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:TSLA_SHORT | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:TSM_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:UNH_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:UPS_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:VALE_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:VLO_LONG | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trb:ZCSH_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:ZETA_LONG | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trc:AXON_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:A_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:BWXT_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:EQT_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:MU_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:NKE_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |

... 2 more FAIL rows in `data/forward_parity/stocks_latest.json`

PASS with activity: trb:EXEL_LONG, trb:PSX_LONG, trc:META_LONG  ·  PASS_IDLE (no decision either side): 59

NO_DATA reasons: prepare failed: ValueError: NPZ has only 19 distin 2, prepare failed: ValueError: NPZ has only 20 distin 1, prepare failed: ValueError: NPZ has only 18 distin 1

### Per function family (stocks)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| BROKER_SYNC | tick | N | safety (exempt) | 0 | 336 | 0 | 0 | 0 | 0 | PASS |
| DC_DAYTRADE_TARGET | 15m | Y | DC_DAYTRADE_ENABLED | 0 | 2 | 33 | 0 | 33 | 0 | FAIL |
| HARDCODED_RALLY_REENTRY | 15m | Y | HARDCODED_RALLY_REENTRY_ENABLED | 0 | 0 | 15 | 0 | 15 | 0 | FAIL |
| UNCLASSIFIED:B_EMASLOPE | 15m | Y | ? (switch not identified) | 0 | 0 | 12 | 0 | 12 | 0 | FAIL |
| UNCLASSIFIED:B | 15m | Y | ? (switch not identified) | 0 | 0 | 10 | 0 | 10 | 0 | FAIL |
| UNCLASSIFIED:DD_BOUNCE_STOP | 15m | Y | ? (switch not identified) | 0 | 0 | 9 | 0 | 9 | 0 | FAIL |
| UNCLASSIFIED:PULLBACK_AUG | 15m | Y | ? (switch not identified) | 0 | 0 | 8 | 0 | 8 | 0 | FAIL |
| CYCLE_TP | tick | Y | CYCLE_TP_TIERED_ENABLED | 0 | 4 | 0 | 0 | 0 | 0 | PASS |
| DC_BREAK | 15m | N | ? (switch not identified) | 0 | 3 | 0 | 0 | 0 | 0 | PASS |
| DC_DAYTRADE_STOP | 15m | Y | DC_DAYTRADE_ENABLED | 0 | 0 | 2 | 0 | 2 | 0 | FAIL |
| REENTRY_OTHER | 15m | Y | ABLATION_DISABLE_REENTRY | 0 | 2 | 0 | 0 | 0 | 0 | PASS |
| GAP_MOC | 5m | Y | ? (switch not identified) | 0 | 0 | 2 | 0 | 2 | 0 | FAIL |
| UNCLASSIFIED:B_WT_M | 15m | Y | ? (switch not identified) | 0 | 0 | 1 | 0 | 1 | 0 | FAIL |
| UNCLASSIFIED:UAG_LADDER | 15m | Y | ? (switch not identified) | 0 | 0 | 1 | 0 | 1 | 0 | FAIL |
| SENTIMENT_STRATEGY_DIVERGENCE | tick | N | ? (switch not identified) | 0 | 1 | 0 | 0 | 0 | 0 | PASS |

## Test 2 - LIVE_ONLY functions (1m/3m/5m, tick, webhook, portfolio, no vec twin), STOCKS

These decisions have no 15m vector counterpart by construction; listed so drift is visible (attempts = execute_now calls in the window).

| function family | input TF | attempts | fills | gate switch |
|---|---|---|---|---|

## decisions -> history parity (switch intents vs live fills)

`DH_20261005_20261006.json` generated 2026-10-06T16:17:02.563374+00:00 (17 min ago) · intents 357 · matched 35 · **missing 322**

matched/intents per account: fin 4/9, men 8/37, ang 14/27, inf 0/0 (no decision file), flz 9/10, trb 0/156, trc 0/118

| function family | action | intent result | n | blocking execution filter | vector same decision +-2 bars (Y/N/? = not judged) |
|---|---|---|---|---|---|
| UNCLASSIFIED:? | OPEN | MISSING | 80 | UNATTRIBUTED 80 | ? 79, N 1 |
| UNCLASSIFIED:? | REDUCE | MISSING | 73 | UNATTRIBUTED 73 | ? 73 |
| UNCLASSIFIED:? | CLOSE | MISSING | 42 | UNATTRIBUTED 42 | ? 42 |
| UNCLASSIFIED:? | REENTRY | MISSING | 30 | UNATTRIBUTED 30 | ? 30 |
| UNCLASSIFIED:? | SHORT SELL | MISSING | 19 | UNATTRIBUTED 19 | ? 19 |
| BB_RECOVERY_EXIT | REDUCE | MISSING | 18 | NO_QUANTITY_LEFT_TO_REDUCE 5, BLOCKED_MAKER_SUPPRESS_WEBHOOK 5, HARD_REDUCE_LOCK 4, NUKE_STALE_MAKER_ORDER 3 | ? 18 |
| UNCLASSIFIED:? | WAIT | MISSING | 16 | UNATTRIBUTED 16 | ? 16 |
| UNCLASSIFIED:? | LONG BUY | MISSING | 11 | UNATTRIBUTED 11 | ? 11 |
| EXIT_VELOCITY_WT | CLOSE | MISSING | 10 | NUKE_STALE_MAKER_ORDER 10 | N 9, ? 1 |
| WT_LOWER_CROSS_EXIT | CLOSE | MISSING | 5 | UNATTRIBUTED 3, NUKE_STALE_MAKER_ORDER 2 | ? 4, N 1 |
| QUICK_REDUCE_STRONG | STRONG_REDUCE | MISSING | 4 | NUKE_STALE_MAKER_ORDER 4 | N 4 |
| QUICK_REDUCE_OTHER | REDUCE | MISSING | 3 | UNATTRIBUTED 3 | N 3 |
| GOLDEN_RULE | OPEN | MISSING | 2 | MAKER_FAILED_SUPPRESS_WEBHOOK 2 | ? 2 |
| RATIO_REBALANCE | REDUCE | MISSING | 2 | NUKE_STALE_MAKER_ORDER 1, BLOCKED_MAKER_SUPPRESS_WEBHOOK 1 | ? 2 |
| DC_BREACH_REDUCE | CLOSE | MISSING | 2 | UNATTRIBUTED 2 | N 2 |
| QUICK_OPEN_STRONG | QUICK_OPEN | MISSING | 2 | MAKER_ZERO_QTY 2 | N 2 |
| UNCLASSIFIED:? | 🔫 HEAVY_ART | MISSING | 2 | UNATTRIBUTED 2 | ? 2 |
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

generated 2026-10-06T16:00:10.279643+00:00 (34 min ago) · lookback 48.0h · exit rows 5956 · **LEAKS 3085** · gate ON 1753 · UNGATED 1035 · safety 4 · unmapped 2

LEAKS (gate OFF for that sym_side yet fired): QUICK_OPEN_STRONG 3083, WT_DC_ENTRY (WT_DC_ENTRY_ENABLED=False) 2

UNGATED families (fire with no switch — live-side switch hook missing, cannot be turned off): RANKING_DIRECT_ALL_GREEN 1007, RANKING_DIRECT_ALL_RED 28

## 7-day rollup (tools/daily_parity_test.py)

`data/parity/daily_parity_20261006.md`

- Generated: 2026-10-06T06:15:12+00:00 | engine: tools/forward_parity/live_vs_vec.py (same bars, same per-sym set)
- crypto: 139 live keys · status {'PASS_IDLE': 98, 'NO_DATA': 13, 'STALE_PASS_IDLE': 11, 'FAIL': 17} · VEC_ONLY {'LIVE_ALREADY_IN_POSITION': 3, 'LIVE_NO_SIGNAL': 3} · LIVE_ONLY {'LIVE_ONLY_VEC_FAMILY:GOLDEN_RULE': 15, 'LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT': 7, 'LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m': 6, 'LIVE_ONLY_NONVEC:RANKING_DIRECT_ALL_GREEN@webhook': 5, 'LIVE_ONLY_VEC_FAMILY:DC_DAYTRADE_TARGET': 4, 'LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m': 3, 'LIVE_ONLY_VEC_FAMILY:WT_LOWER_CROSS_EXIT': 1, 'LIVE_ONLY_NONVEC:RATIO_REBALANCE@portfolio': 1, 'LIVE_ONLY_NONVEC:BREAK_EVEN_GUARD@tick': 1, 'LIVE_ONLY_VEC_FAMILY:MTF_BB_REJECT_EXIT': 1, 'LIVE_ONLY_NONVEC:ALL_TF_AGAINST_CLOSE@3m': 1, 'LIVE_ONLY_NONVEC:GUARANTEED_REENTRY@3m': 1, 'LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY': 1, 'LIVE_ONLY_NONVEC:GAIN_EROSION_STOP@3m': 1}
- stocks: 175 live keys · status {'FAIL': 151, 'PASS_IDLE': 19, 'NO_DATA': 4, 'STALE_PASS_IDLE': 1} · VEC_ONLY {'LIVE_NO_SIGNAL': 208, 'LIVE_ALREADY_FLAT': 162, 'LIVE_ALREADY_IN_POSITION': 28, 'LIVE_INTENT_NOT_FILLED:UNCLASSIFIED:?': 2} · LIVE_ONLY {'LIVE_ONLY_NONVEC:REENTRY_OTHER@5m': 226, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:SYNC_DETECTION@15m': 71, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:MFI_MEAN_REVERSION@15m': 57, 'LIVE_ONLY_VEC_FAMILY:REENTRY_OTHER': 47, 'LIVE_ONLY_NONVEC:FALLBACK_CLOSE@15m': 27, 'LIVE_ONLY_NONVEC:GAP_MOC@5m': 24, 'LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY': 23, 'LIVE_ONLY_VEC_FAMILY:DC_DAYTRADE_TARGET': 21, 'LIVE_ONLY_VEC_FAMILY:HARDCODED_RALLY_REENTRY': 18, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TARGET@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DC_BREAK_HIGH@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:ULTIMATE_DC_H@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@15m': 11, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:INTRADAY_RATIO_TRIM@15m': 5, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:STRUCT_BREAK_DC@15m': 5, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:SENT_STRAT_DIV@15m': 3, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:OVERBOUGHT_TAKE_PROFIT@15m': 2, 'LIVE_ONLY_NONVEC:GAP_FILL@5m': 2, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:GR_HTF_DIRECT@3m': 2, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:NOLOSS_BBH_BREAKDOWN@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@3m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@5m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:ROTATION_ENTRY_L@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:CLENOW_ENTRY@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:STRUCT_BREAK_DC@5m': 1}
- Totals: PASS 129 · FAIL 168 · sign flips (>=5 exits each side) 0 · alerts 86
