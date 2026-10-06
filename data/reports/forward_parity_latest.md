# Forward parity — LIVE vs VECTOR (auto, read-only)

generated 2026-10-06T08:17:12+00:00 · producers: tools/forward_parity/live_vs_vec.py (crypto hourly :07, stocks every 15 min RTH), tools/decisions_history_parity.py (:17), tools/exit_engine_parity_monitor.py (launchd hourly), tools/daily_parity_test.py (launchd daily)

PASS = every vector decision on the judged 15m bars has a live fill of the same class within +-2 bars, every live fill has a vector decision, and end state (in/out) agrees. FAIL classes: VEC_ONLY_LIVE_NO_SIGNAL (live logic did not fire), VEC_ONLY_LIVE_BLOCKED (live attempted, gate/execution stopped it), VEC_ONLY_STATE_CASCADE (consequence of an earlier divergence), LIVE_ONLY_VEC_FAMILY (vector twin exists but did not fire), LIVE_ONLY_NONVEC (1m/3m/5m/tick/webhook/portfolio input — no vec counterpart), STATE_MISMATCH, NPZ_STALE (S1 NPZ behind live, judged on the newest window the vector can see).

## Test 1 - 15m LIVE vs VECTOR, CRYPTO

run 2026-10-06T08:12:34+00:00 (5 min ago) · engine v12 697b2734 · NPZ sync tail · window 24.0h from 2026-10-05T08:07Z · tol +-2 bars · 225 live keys / 120 sym_sides · 333.2 s

**Verdict: FAIL** — FAIL 10 · PASS with decisions 0 · IDLE (no decision either side, counts as pass) 200 · NO_DATA 15 · judged on a STALE-shifted window (S1 NPZ behind live) 25

- vector fired, live did not (VEC_ONLY): LIVE_NO_SIGNAL 10, LIVE_ALREADY_IN_POSITION 5, LIVE_ALREADY_FLAT 5
- live fired, vector did not (LIVE_ONLY): LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 6, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 6, LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m 3, LIVE_ONLY_NONVEC:GUARANTEED_REENTRY@3m 1, LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY 1, LIVE_ONLY_NONVEC:GAIN_EROSION_STOP@3m 1
- NPZ missing on S1 (no vector possible): 100PEPEUSDC, EDUUSDT, KMNOUSDT, MELANIAUSDT
- vector replay made 0 trades in 30D for 102/120 sym_sides with the live set (engine md5 697b2734): every live fill on those keys is LIVE_ONLY by construction — engine/set problem, not live drift. e.g. 1000BONKUSDC_LONG, 1INCHUSDT_LONG, 1INCHUSDT_SHORT, AAVEUSDC_LONG, ADAUSDC_LONG, ADAUSDC_SHORT, AGLDUSDT_SHORT, ALGOUSDT_LONG
- vector errors: prepare failed: no npz 11
- FAIL classes (sym_sides): LIVE_ONLY_NONVEC 7, LIVE_ONLY_VEC_FAMILY 6, VEC_ONLY_LIVE_NO_SIGNAL 2, VEC_ONLY_STATE_CASCADE 2, STATE_MISMATCH 1

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|
| flz:XRPUSDC_SHORT | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 5, LIVE_NO_SIGNAL 5 | - | False/False | last window | per_sym_active_config:clean_ |
| men:XRPUSDC_SHORT | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 5, LIVE_ALREADY_FLAT 5 | - | False/False | last window | per_sym_active_config:clean_ |
| ang:ZECUSDC_LONG | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 2, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 2 | False/False | last window | per_sym_active_config:clean_ |
| flz:SOLUSDC_SHORT | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY | - | LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY 1, LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m 1, LIVE_ONLY_NONVEC:GAIN_EROSION_STOP@3m 1 | False/False | last window | per_sym_active_config:clean_ |
| ang:GALAUSDT_SHORT | LIVE_ONLY_NONVEC | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 1, LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m 1 | False/False | last window | per_sym_active_config:clean_ |
| fin:THETAUSDT_LONG | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 1, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 1 | False/False | last window | vec_driven:progress:f4144e33 |
| flz:ETHUSDC_SHORT | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY | - | LIVE_ONLY_NONVEC:GUARANTEED_REENTRY@3m 1, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 1 | False/False | last window | per_sym_active_config:clean_ |
| flz:ZECUSDC_LONG | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 1, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 1 | False/False | last window | per_sym_active_config:clean_ |
| men:GALAUSDT_SHORT | LIVE_ONLY_NONVEC, STATE_MISMATCH | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 1, LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m 1 | False/True | last window | per_sym_active_config:clean_ |
| men:VETUSDT_LONG | LIVE_ONLY_VEC_FAMILY | - | LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 1 | False/False | last window | defaults(cat_side) |

PASS with activity: -  ·  PASS_IDLE (no decision either side): 200

NO_DATA reasons: prepare failed: no npz 15

### Per function family (crypto)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| QUICK_OPEN_STRONG | 3m | Y | QUICK_OPEN_STRONG_VEC_ENABLED | 965 | 6 | 0 | 0 | 0 | 6 | LIVE_ONLY(nonvec) |
| RANKING_DIRECT_ALL_GREEN | webhook | N | **UNGATED (verified: no switch)** | 478 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| GOLDEN_RULE | 15m | Y | GOLDEN_RULE_ENABLED | 308 | 0 | 0 | 0 | 0 | 0 | IDLE |
| EXIT_VELOCITY_WT | 1h | Y | EXIT_VELOCITY_WT_ENABLED | 170 | 6 | 10 | 0 | 10 | 6 | FAIL |
| BB_RECOVERY_EXIT | 15m | N | BB_RECOVERY_EXIT_ENABLED_TRADIER | 118 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_STRONG | 3m | N | ABLATION_DISABLE_QUICK_EXIT | 33 | 3 | 0 | 0 | 0 | 3 | LIVE_ONLY(nonvec) |
| RANKING_DIRECT_ALL_RED | webhook | N | **UNGATED (verified: no switch)** | 27 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| CRYPTO_SPIKE_FADE | 3m | N | CRYPTO_SPIKE_FADE_ENABLED | 26 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_OTHER | 3m | N | ABLATION_DISABLE_QUICK_EXIT | 21 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_NO_PROFIT | 1m | N | ABLATION_DISABLE_QUICK_EXIT | 19 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| WT_LOWER_CROSS_EXIT | 1h | Y | WT_LOWER_CROSS_EXIT_TF | 17 | 0 | 0 | 0 | 0 | 0 | IDLE |
| QUICK_OPEN_GOOD | 3m | N | ? (switch not identified) | 16 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| GUARANTEED_REENTRY | 15m | Y | ? (switch not identified) | 10 | 2 | 0 | 0 | 0 | 2 | FAIL |
| WEBHOOK_HANDLE_SIGNAL | webhook | N | ? (switch not identified) | 12 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| DC_DAYTRADE_TARGET | 15m | Y | DC_DAYTRADE_ENABLED | 10 | 0 | 0 | 0 | 0 | 0 | IDLE |
| HARDCODED_RALLY_REENTRY | 15m | Y | HARDCODED_RALLY_REENTRY_ENABLED | 4 | 0 | 6 | 0 | 6 | 0 | FAIL |
| RATIO_REBALANCE | portfolio | N | ABLATION_DISABLE_RATIO_REBALANCE | 9 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_SCALP | 1m | N | ABLATION_DISABLE_SCALP_GUARD | 5 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:B_KZONE | 15m | Y | ? (switch not identified) | 0 | 0 | 4 | 0 | 4 | 0 | FAIL |
| DELTA_EXIT | 3m | N | DELTA_EXIT_ENABLED | 2 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| DC_BREACH_REDUCE | 15m | Y | ABLATION_DISABLE_DC_BREACH_REDUCE | 2 | 0 | 0 | 0 | 0 | 0 | IDLE |
| GAIN_EROSION_STOP | 3m | N | ? (switch not identified) | 1 | 1 | 0 | 0 | 0 | 1 | LIVE_ONLY(nonvec) |

## Test 2 - LIVE_ONLY functions (1m/3m/5m, tick, webhook, portfolio, no vec twin), CRYPTO

These decisions have no 15m vector counterpart by construction; listed so drift is visible (attempts = execute_now calls in the window).

| function family | input TF | attempts | fills | gate switch |
|---|---|---|---|---|
| QUICK_OPEN_STRONG | 3m | 965 | 6 | QUICK_OPEN_STRONG_VEC_ENABLED |
| RANKING_DIRECT_ALL_GREEN | webhook | 478 | 0 | **UNGATED (verified: no switch)** |
| BB_RECOVERY_EXIT | 15m | 118 | 0 | BB_RECOVERY_EXIT_ENABLED_TRADIER |
| QUICK_REDUCE_STRONG | 3m | 33 | 3 | ABLATION_DISABLE_QUICK_EXIT |
| RANKING_DIRECT_ALL_RED | webhook | 27 | 0 | **UNGATED (verified: no switch)** |
| CRYPTO_SPIKE_FADE | 3m | 26 | 0 | CRYPTO_SPIKE_FADE_ENABLED |
| QUICK_REDUCE_OTHER | 3m | 21 | 0 | ABLATION_DISABLE_QUICK_EXIT |
| QUICK_REDUCE_NO_PROFIT | 1m | 19 | 0 | ABLATION_DISABLE_QUICK_EXIT |
| QUICK_OPEN_GOOD | 3m | 16 | 0 | ? (switch not identified) |
| WEBHOOK_HANDLE_SIGNAL | webhook | 12 | 0 | ? (switch not identified) |
| RATIO_REBALANCE | portfolio | 9 | 0 | ABLATION_DISABLE_RATIO_REBALANCE |
| QUICK_REDUCE_SCALP | 1m | 5 | 0 | ABLATION_DISABLE_SCALP_GUARD |
| DELTA_EXIT | 3m | 2 | 0 | DELTA_EXIT_ENABLED |
| GAIN_EROSION_STOP | 3m | 1 | 1 | ? (switch not identified) |

## Test 3 - VEC_DRIVEN bridge (S1 vec intents -> live fills)

intents in window: 2 · result: NOT_IN_MAC_REGISTRY 2 · consumer files: **none (live consumer has not written data/vec_live/live_exec_*.jsonl)**

execute_now gate refusals tagged VEC_DRIVEN in ez_manage logs: {'fin': {'UNKNOWN': 124, 'GOLDEN_RULE': 1}, 'men': {'UNKNOWN': 110, 'GOLDEN_RULE': 1}, 'inf': {'UNKNOWN': 228, 'GOLDEN_RULE': 2}, 'flz': {'UNKNOWN': 78, 'GOLDEN_RULE': 4}}

| bar | ss | acct | type | vec reason | result |
|---|---|---|---|---|---|
| 2026-10-06T03:30Z | 1000BONKUSDC_SHORT | - | CLOSE | E_3_STRUCTURE_EXIT_2TF_15m_4h | NOT_IN_MAC_REGISTRY |
| 2026-10-06T05:45Z | ADAUSDC_SHORT | - | OPEN | B14 | NOT_IN_MAC_REGISTRY |

## Test 1 - 15m LIVE vs VECTOR, STOCKS

run 2026-10-06T06:09:23+00:00 (2.1 h ago) · engine v12 697b2734 · NPZ sync ? · window 24.0h from 2026-10-05T06:09Z · tol +-2 bars · 144 live keys / 125 sym_sides · 19.1 s

**Verdict: FAIL** — FAIL 78 · PASS with decisions 0 · IDLE (no decision either side, counts as pass) 55 · NO_DATA 11 · judged on a STALE-shifted window (S1 NPZ behind live) 4

- vector fired, live did not (VEC_ONLY): LIVE_NO_SIGNAL 44, LIVE_ALREADY_FLAT 36, LIVE_ALREADY_IN_POSITION 6
- live fired, vector did not (LIVE_ONLY): -
- vector replay made 0 trades in 30D for 22/125 sym_sides with the live set (engine md5 697b2734): every live fill on those keys is LIVE_ONLY by construction — engine/set problem, not live drift. e.g. AMZN_SHORT, A_LONG, BA_SHORT, BNO_LONG, BWXT_SHORT, CIBR_LONG, COHR_LONG, COPX_LONG
- vector errors: prepare failed (no npz) 11
- FAIL classes (sym_sides): STATE_MISMATCH 69, VEC_ONLY_LIVE_NO_SIGNAL 21, VEC_ONLY_STATE_CASCADE 18, NPZ_STALE 3

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|
| trb:SPY_LONG | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 10, LIVE_ALREADY_FLAT 10 | - | False/False | last window | trb/active_config |
| trb:QRVO_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 4, LIVE_ALREADY_FLAT 4 | - | True/False | 2026-09-10T16:00Z..2026-09-11T16:00Z | per_sym_active_config_stocks |
| trb:ADBE_SHORT | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 4, LIVE_ALREADY_FLAT 3 | - | True/True | last window | trb/active_config |
| trb:GOOGL_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 4, LIVE_ALREADY_FLAT 3 | - | True/False | last window | trb/active_config |
| trc:ADBE_SHORT | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 4, LIVE_NO_SIGNAL 3 | - | True/True | last window | trb/active_config |
| trb:QCOM_LONG | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 3, LIVE_ALREADY_FLAT 3 | - | True/False | 2026-09-10T16:00Z..2026-09-11T16:00Z | defaults(cat_side) |
| trb:SHOP_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 2, LIVE_ALREADY_FLAT 2 | - | True/False | last window | defaults(cat_side) |
| trb:UEC_SHORT | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 2, LIVE_NO_SIGNAL 1 | - | False/False | last window | trb/active_config |
| trc:UEC_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 2, LIVE_NO_SIGNAL 1 | - | False/True | last window | trb/active_config |
| trb:GME_LONG | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 1, LIVE_ALREADY_FLAT 1 | - | False/False | last window | defaults(cat_side) |
| trb:IBIT_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 1, LIVE_NO_SIGNAL 1 | - | False/True | last window | trb/active_config |
| trb:MSTR_LONG | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 1, LIVE_ALREADY_FLAT 1 | - | False/False | last window | trb/active_config |
| trc:IBIT_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 1, LIVE_NO_SIGNAL 1 | - | False/True | last window | trb/active_config |
| trb:AAPL_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | True/False | last window | trb/active_config |
| trb:AMAT_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | True/False | last window | defaults(cat_side) |
| trb:AMD_LONG | VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | last window | trb/active_config |
| trb:CLX_SHORT | VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | last window | defaults(cat_side) |
| trb:EQT_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | False/True | last window | trb/active_config |
| trb:FIX_LONG | STATE_MISMATCH, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | True/False | last window | defaults(cat_side) |
| trb:LEXX_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | True/False | last window | defaults(cat_side) |
| trb:LMT_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | True/False | last window | trb/active_config |
| trb:SMCI_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | True/False | last window | defaults(cat_side) |
| trb:STZ_SHORT | VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | last window | defaults(cat_side) |
| trb:UNH_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | True/False | last window | defaults(cat_side) |
| trc:EQT_SHORT | STATE_MISMATCH, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/True | last window | trb/active_config |
| trc:RRC_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | False/True | last window | trb/active_config |
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
| trb:COIN_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:CVS_SHORT | STATE_MISMATCH | - | - | True/False | last window | defaults(cat_side) |
| trb:EXEL_LONG | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
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
| trb:VLO_LONG | STATE_MISMATCH | - | - | True/False | last window | trb/active_config |
| trc:AXON_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:A_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:BWXT_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:META_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:MU_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:NKE_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:PSX_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |

PASS with activity: -  ·  PASS_IDLE (no decision either side): 55

NO_DATA reasons: prepare failed (no npz) 11

### Per function family (stocks)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| DC_DAYTRADE_TARGET | 15m | Y | DC_DAYTRADE_ENABLED | 0 | 0 | 21 | 0 | 21 | 0 | FAIL |
| HARDCODED_RALLY_REENTRY | 15m | Y | HARDCODED_RALLY_REENTRY_ENABLED | 0 | 0 | 18 | 0 | 18 | 0 | FAIL |
| UNCLASSIFIED:DD_BOUNCE_STOP | 15m | Y | ? (switch not identified) | 0 | 0 | 10 | 0 | 10 | 0 | FAIL |
| UNCLASSIFIED:B_EMASLOPE | 15m | Y | ? (switch not identified) | 0 | 0 | 8 | 0 | 8 | 0 | FAIL |
| UNCLASSIFIED:PULLBACK_AUG | 15m | Y | ? (switch not identified) | 0 | 0 | 8 | 0 | 8 | 0 | FAIL |
| DC_DAYTRADE_STOP | 15m | Y | DC_DAYTRADE_ENABLED | 0 | 0 | 8 | 0 | 8 | 0 | FAIL |
| UNCLASSIFIED:B | 15m | Y | ? (switch not identified) | 0 | 0 | 5 | 0 | 5 | 0 | FAIL |
| GAP_MOC | 5m | Y | ? (switch not identified) | 0 | 0 | 3 | 0 | 3 | 0 | FAIL |
| UNCLASSIFIED:UAG_LADDER | 15m | Y | ? (switch not identified) | 0 | 0 | 2 | 0 | 2 | 0 | FAIL |
| REENTRY_OTHER | 15m | Y | ABLATION_DISABLE_REENTRY | 0 | 0 | 2 | 0 | 2 | 0 | FAIL |
| UNCLASSIFIED:B_WT_M | 15m | Y | ? (switch not identified) | 0 | 0 | 1 | 0 | 1 | 0 | FAIL |

## Test 2 - LIVE_ONLY functions (1m/3m/5m, tick, webhook, portfolio, no vec twin), STOCKS

These decisions have no 15m vector counterpart by construction; listed so drift is visible (attempts = execute_now calls in the window).

| function family | input TF | attempts | fills | gate switch |
|---|---|---|---|---|

## decisions -> history parity (switch intents vs live fills)

`DH_20261005_20261006.json` generated 2026-10-06T08:17:02.695681+00:00 (0 min ago) · intents 79 · matched 35 · **missing 44**

matched/intents per account: fin 4/9, men 8/33, ang 14/27, inf 0/0 (no decision file), flz 9/10, trb 0/0 (no decision file), trc 0/0 (no decision file)

| function family | action | intent result | n | blocking execution filter | vector same decision +-2 bars (Y/N/? = not judged) |
|---|---|---|---|---|---|
| BB_RECOVERY_EXIT | REDUCE | MISSING | 18 | NO_QUANTITY_LEFT_TO_REDUCE 5, BLOCKED_MAKER_SUPPRESS_WEBHOOK 5, HARD_REDUCE_LOCK 4, NUKE_STALE_MAKER_ORDER 3 | ? 18 |
| EXIT_VELOCITY_WT | CLOSE | MISSING | 10 | NUKE_STALE_MAKER_ORDER 10 | N 9, ? 1 |
| WT_LOWER_CROSS_EXIT | CLOSE | MISSING | 5 | NUKE_STALE_MAKER_ORDER 4, UNATTRIBUTED 1 | ? 4, N 1 |
| QUICK_REDUCE_STRONG | STRONG_REDUCE | MISSING | 4 | NUKE_STALE_MAKER_ORDER 4 | N 4 |
| GOLDEN_RULE | OPEN | MISSING | 2 | MAKER_FAILED_SUPPRESS_WEBHOOK 2 | ? 2 |
| RATIO_REBALANCE | REDUCE | MISSING | 2 | NUKE_STALE_MAKER_ORDER 1, BLOCKED_MAKER_SUPPRESS_WEBHOOK 1 | ? 2 |
| QUICK_OPEN_STRONG | QUICK_OPEN | MISSING | 2 | MAKER_ZERO_QTY 2 | N 2 |
| DC_BREACH_REDUCE | CLOSE | MISSING | 1 | UNATTRIBUTED 1 | ? 1 |
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

generated 2026-10-06T07:59:43.910861+00:00 (17 min ago) · lookback 48.0h · exit rows 3452 · **LEAKS 934** · gate ON 1404 · UNGATED 1049 · safety 4 · unmapped 0

LEAKS (gate OFF for that sym_side yet fired): QUICK_OPEN_STRONG 932, WT_DC_ENTRY (WT_DC_ENTRY_ENABLED=False) 2

UNGATED families (fire with no switch — live-side switch hook missing, cannot be turned off): RANKING_DIRECT_ALL_GREEN 1021, RANKING_DIRECT_ALL_RED 28

## 7-day rollup (tools/daily_parity_test.py)

`data/parity/daily_parity_20261006.md`

- Generated: 2026-10-06T06:15:12+00:00 | engine: tools/forward_parity/live_vs_vec.py (same bars, same per-sym set)
- crypto: 139 live keys · status {'PASS_IDLE': 98, 'NO_DATA': 13, 'STALE_PASS_IDLE': 11, 'FAIL': 17} · VEC_ONLY {'LIVE_ALREADY_IN_POSITION': 3, 'LIVE_NO_SIGNAL': 3} · LIVE_ONLY {'LIVE_ONLY_VEC_FAMILY:GOLDEN_RULE': 15, 'LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT': 7, 'LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m': 6, 'LIVE_ONLY_NONVEC:RANKING_DIRECT_ALL_GREEN@webhook': 5, 'LIVE_ONLY_VEC_FAMILY:DC_DAYTRADE_TARGET': 4, 'LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m': 3, 'LIVE_ONLY_VEC_FAMILY:WT_LOWER_CROSS_EXIT': 1, 'LIVE_ONLY_NONVEC:RATIO_REBALANCE@portfolio': 1, 'LIVE_ONLY_NONVEC:BREAK_EVEN_GUARD@tick': 1, 'LIVE_ONLY_VEC_FAMILY:MTF_BB_REJECT_EXIT': 1, 'LIVE_ONLY_NONVEC:ALL_TF_AGAINST_CLOSE@3m': 1, 'LIVE_ONLY_NONVEC:GUARANTEED_REENTRY@3m': 1, 'LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY': 1, 'LIVE_ONLY_NONVEC:GAIN_EROSION_STOP@3m': 1}
- stocks: 175 live keys · status {'FAIL': 151, 'PASS_IDLE': 19, 'NO_DATA': 4, 'STALE_PASS_IDLE': 1} · VEC_ONLY {'LIVE_NO_SIGNAL': 208, 'LIVE_ALREADY_FLAT': 162, 'LIVE_ALREADY_IN_POSITION': 28, 'LIVE_INTENT_NOT_FILLED:UNCLASSIFIED:?': 2} · LIVE_ONLY {'LIVE_ONLY_NONVEC:REENTRY_OTHER@5m': 226, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:SYNC_DETECTION@15m': 71, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:MFI_MEAN_REVERSION@15m': 57, 'LIVE_ONLY_VEC_FAMILY:REENTRY_OTHER': 47, 'LIVE_ONLY_NONVEC:FALLBACK_CLOSE@15m': 27, 'LIVE_ONLY_NONVEC:GAP_MOC@5m': 24, 'LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY': 23, 'LIVE_ONLY_VEC_FAMILY:DC_DAYTRADE_TARGET': 21, 'LIVE_ONLY_VEC_FAMILY:HARDCODED_RALLY_REENTRY': 18, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TARGET@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DC_BREAK_HIGH@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:ULTIMATE_DC_H@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@15m': 11, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:INTRADAY_RATIO_TRIM@15m': 5, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:STRUCT_BREAK_DC@15m': 5, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:SENT_STRAT_DIV@15m': 3, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:OVERBOUGHT_TAKE_PROFIT@15m': 2, 'LIVE_ONLY_NONVEC:GAP_FILL@5m': 2, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:GR_HTF_DIRECT@3m': 2, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:NOLOSS_BBH_BREAKDOWN@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@3m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@5m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:ROTATION_ENTRY_L@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:CLENOW_ENTRY@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:STRUCT_BREAK_DC@5m': 1}
- Totals: PASS 129 · FAIL 168 · sign flips (>=5 exits each side) 0 · alerts 86
