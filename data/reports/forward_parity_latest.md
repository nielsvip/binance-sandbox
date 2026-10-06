# Forward parity — LIVE vs VECTOR (auto, read-only)

generated 2026-10-06T05:59:42+00:00 · producers: tools/forward_parity/live_vs_vec.py (crypto hourly :07, stocks every 15 min RTH), tools/decisions_history_parity.py (:17), tools/exit_engine_parity_monitor.py (launchd hourly), tools/daily_parity_test.py (launchd daily)

PASS = every vector decision on the judged 15m bars has a live fill of the same class within +-2 bars, every live fill has a vector decision, and end state (in/out) agrees. FAIL classes: VEC_ONLY_LIVE_NO_SIGNAL (live logic did not fire), VEC_ONLY_LIVE_BLOCKED (live attempted, gate/execution stopped it), VEC_ONLY_STATE_CASCADE (consequence of an earlier divergence), LIVE_ONLY_VEC_FAMILY (vector twin exists but did not fire), LIVE_ONLY_NONVEC (1m/3m/5m/tick/webhook/portfolio input — no vec counterpart), STATE_MISMATCH, NPZ_STALE (S1 NPZ behind live, judged on the newest window the vector can see).

## Test 1 - 15m LIVE vs VECTOR, CRYPTO

run 2026-10-06T05:59:29+00:00 (0 min ago) · engine v12 5506425e · NPZ sync tail · window 24.0h from 2026-10-05T05:50Z · tol +-2 bars · 208 live keys / 119 sym_sides · 569.4 s

**Verdict: FAIL** — FAIL 10 · PASS with decisions 0 · IDLE (no decision either side, counts as pass) 183 · NO_DATA 15 · judged on a STALE-shifted window (S1 NPZ behind live) 24

- vector fired, live did not (VEC_ONLY): LIVE_NO_SIGNAL 6, LIVE_ALREADY_IN_POSITION 3, LIVE_ALREADY_FLAT 3
- live fired, vector did not (LIVE_ONLY): LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 6, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 6, LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m 3, LIVE_ONLY_NONVEC:GUARANTEED_REENTRY@3m 1, LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY 1, LIVE_ONLY_NONVEC:GAIN_EROSION_STOP@3m 1
- NPZ missing on S1 (no vector possible): 100PEPEUSDC, EDUUSDT, KMNOUSDT, MELANIAUSDT
- vector replay made 0 trades in 30D for 101/119 sym_sides with the live set (engine md5 5506425e): every live fill on those keys is LIVE_ONLY by construction — engine/set problem, not live drift. e.g. 1000BONKUSDC_LONG, 1INCHUSDT_LONG, 1INCHUSDT_SHORT, AAVEUSDC_LONG, ADAUSDC_LONG, ADAUSDC_SHORT, AGLDUSDT_SHORT, ALGOUSDT_LONG
- vector errors: prepare failed (no npz) 11
- FAIL classes (sym_sides): LIVE_ONLY_NONVEC 7, LIVE_ONLY_VEC_FAMILY 6, STATE_MISMATCH 3, VEC_ONLY_LIVE_NO_SIGNAL 2, VEC_ONLY_STATE_CASCADE 2

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|
| flz:XRPUSDC_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 3, LIVE_NO_SIGNAL 3 | - | True/False | last window | per_sym_active_config:clean_ |
| men:XRPUSDC_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 3, LIVE_ALREADY_FLAT 3 | - | True/False | last window | per_sym_active_config:clean_ |
| ang:ZECUSDC_LONG | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 2, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 2 | False/False | last window | per_sym_active_config:clean_ |
| flz:SOLUSDC_SHORT | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY | - | LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY 1, LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m 1, LIVE_ONLY_NONVEC:GAIN_EROSION_STOP@3m 1 | False/False | last window | per_sym_active_config:clean_ |
| ang:GALAUSDT_SHORT | LIVE_ONLY_NONVEC | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 1, LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m 1 | False/False | last window | per_sym_active_config:clean_ |
| fin:THETAUSDT_LONG | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 1, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 1 | False/False | last window | vec_driven:progress:f4144e33 |
| flz:ETHUSDC_SHORT | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY | - | LIVE_ONLY_NONVEC:GUARANTEED_REENTRY@3m 1, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 1 | False/False | last window | per_sym_active_config:clean_ |
| flz:ZECUSDC_LONG | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 1, LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 1 | False/False | last window | per_sym_active_config:clean_ |
| men:GALAUSDT_SHORT | LIVE_ONLY_NONVEC, STATE_MISMATCH | - | LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m 1, LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m 1 | False/True | last window | per_sym_active_config:clean_ |
| men:VETUSDT_LONG | LIVE_ONLY_VEC_FAMILY | - | LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT 1 | False/False | last window | defaults(cat_side) |

PASS with activity: -  ·  PASS_IDLE (no decision either side): 183

NO_DATA reasons: prepare failed (no npz) 15

### Per function family (crypto)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| QUICK_OPEN_STRONG | 3m | Y | QUICK_OPEN_STRONG_VEC_ENABLED | 640 | 6 | 0 | 0 | 0 | 6 | LIVE_ONLY(nonvec) |
| RANKING_DIRECT_ALL_GREEN | webhook | N | **UNGATED (verified: no switch)** | 477 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| GOLDEN_RULE | 15m | Y | GOLDEN_RULE_ENABLED | 233 | 0 | 0 | 0 | 0 | 0 | IDLE |
| EXIT_VELOCITY_WT | 1h | Y | EXIT_VELOCITY_WT_ENABLED | 151 | 6 | 6 | 0 | 6 | 6 | FAIL |
| BB_RECOVERY_EXIT | 15m | N | BB_RECOVERY_EXIT_ENABLED_TRADIER | 91 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_STRONG | 3m | N | ABLATION_DISABLE_QUICK_EXIT | 33 | 3 | 0 | 0 | 0 | 3 | LIVE_ONLY(nonvec) |
| CRYPTO_SPIKE_FADE | 3m | N | CRYPTO_SPIKE_FADE_ENABLED | 29 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| RANKING_DIRECT_ALL_RED | webhook | N | **UNGATED (verified: no switch)** | 27 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_NO_PROFIT | 1m | N | ABLATION_DISABLE_QUICK_EXIT | 19 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_OTHER | 3m | N | ABLATION_DISABLE_QUICK_EXIT | 18 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| WT_LOWER_CROSS_EXIT | 1h | Y | WT_LOWER_CROSS_EXIT_TF | 17 | 0 | 0 | 0 | 0 | 0 | IDLE |
| WEBHOOK_HANDLE_SIGNAL | webhook | N | ? (switch not identified) | 11 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_OPEN_GOOD | 3m | N | ? (switch not identified) | 10 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| DC_DAYTRADE_TARGET | 15m | Y | DC_DAYTRADE_ENABLED | 8 | 0 | 0 | 0 | 0 | 0 | IDLE |
| HARDCODED_RALLY_REENTRY | 15m | Y | HARDCODED_RALLY_REENTRY_ENABLED | 4 | 0 | 4 | 0 | 4 | 0 | FAIL |
| RATIO_REBALANCE | portfolio | N | ABLATION_DISABLE_RATIO_REBALANCE | 7 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_SCALP | 1m | N | ABLATION_DISABLE_SCALP_GUARD | 5 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| GUARANTEED_REENTRY | 3m | Y | ? (switch not identified) | 3 | 2 | 0 | 0 | 0 | 2 | LIVE_ONLY(nonvec) |
| UNCLASSIFIED:B_KZONE | 15m | Y | ? (switch not identified) | 0 | 0 | 4 | 0 | 2 | 0 | FAIL |
| DELTA_EXIT | 3m | N | DELTA_EXIT_ENABLED | 2 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| GAIN_EROSION_STOP | 3m | N | ? (switch not identified) | 1 | 1 | 0 | 0 | 0 | 1 | LIVE_ONLY(nonvec) |
| FINAL_MTM | 15m | Y | safety (exempt) | 0 | 0 | 2 | 0 | 0 | 0 | PASS |
| DC_BREACH_REDUCE | 15m | Y | ABLATION_DISABLE_DC_BREACH_REDUCE | 1 | 0 | 0 | 0 | 0 | 0 | IDLE |

## Test 2 - LIVE_ONLY functions (1m/3m/5m, tick, webhook, portfolio, no vec twin), CRYPTO

These decisions have no 15m vector counterpart by construction; listed so drift is visible (attempts = execute_now calls in the window).

| function family | input TF | attempts | fills | gate switch |
|---|---|---|---|---|
| QUICK_OPEN_STRONG | 3m | 640 | 6 | QUICK_OPEN_STRONG_VEC_ENABLED |
| RANKING_DIRECT_ALL_GREEN | webhook | 477 | 0 | **UNGATED (verified: no switch)** |
| BB_RECOVERY_EXIT | 15m | 91 | 0 | BB_RECOVERY_EXIT_ENABLED_TRADIER |
| QUICK_REDUCE_STRONG | 3m | 33 | 3 | ABLATION_DISABLE_QUICK_EXIT |
| CRYPTO_SPIKE_FADE | 3m | 29 | 0 | CRYPTO_SPIKE_FADE_ENABLED |
| RANKING_DIRECT_ALL_RED | webhook | 27 | 0 | **UNGATED (verified: no switch)** |
| QUICK_REDUCE_NO_PROFIT | 1m | 19 | 0 | ABLATION_DISABLE_QUICK_EXIT |
| QUICK_REDUCE_OTHER | 3m | 18 | 0 | ABLATION_DISABLE_QUICK_EXIT |
| WEBHOOK_HANDLE_SIGNAL | webhook | 11 | 0 | ? (switch not identified) |
| QUICK_OPEN_GOOD | 3m | 10 | 0 | ? (switch not identified) |
| RATIO_REBALANCE | portfolio | 7 | 0 | ABLATION_DISABLE_RATIO_REBALANCE |
| QUICK_REDUCE_SCALP | 1m | 5 | 0 | ABLATION_DISABLE_SCALP_GUARD |
| GUARANTEED_REENTRY | 3m | 3 | 2 | ? (switch not identified) |
| DELTA_EXIT | 3m | 2 | 0 | DELTA_EXIT_ENABLED |
| GAIN_EROSION_STOP | 3m | 1 | 1 | ? (switch not identified) |

## Test 3 - VEC_DRIVEN bridge (S1 vec intents -> live fills)

intents in window: 1 · result: NOT_IN_MAC_REGISTRY 1 · consumer files: **none (live consumer has not written data/vec_live/live_exec_*.jsonl)**

execute_now gate refusals tagged VEC_DRIVEN in ez_manage logs: {'fin': {'UNKNOWN': 104, 'GOLDEN_RULE': 1}, 'men': {'UNKNOWN': 82, 'GOLDEN_RULE': 1}, 'ang': {'UNKNOWN': 114, 'GOLDEN_RULE': 1}, 'inf': {'UNKNOWN': 192, 'GOLDEN_RULE': 2}, 'flz': {'UNKNOWN': 74, 'GOLDEN_RULE': 3}}

| bar | ss | acct | type | vec reason | result |
|---|---|---|---|---|---|
| 2026-10-06T03:30Z | 1000BONKUSDC_SHORT | - | CLOSE | E_3_STRUCTURE_EXIT_2TF_15m_4h | NOT_IN_MAC_REGISTRY |

## Test 1 - 15m LIVE vs VECTOR, STOCKS

run 2026-10-06T05:46:24+00:00 (13 min ago) · engine v12 ? · NPZ sync ? · window 24.0h from 2026-10-05T05:41Z · tol +-2 bars · 144 live keys / 125 sym_sides · 315.1 s

**Verdict: FAIL** — FAIL 19 · PASS with decisions 1 · IDLE (no decision either side, counts as pass) 5 · NO_DATA 119 · judged on a STALE-shifted window (S1 NPZ behind live) 1

- vector fired, live did not (VEC_ONLY): LIVE_NO_SIGNAL 15, LIVE_ALREADY_FLAT 11, LIVE_ALREADY_IN_POSITION 4
- live fired, vector did not (LIVE_ONLY): -
- stock fills present only in tradier_manage logs, missing from data/history (live logging gap): {'trb': 6}
- vector errors: prepare failed (no npz) 106
- FAIL classes (sym_sides): STATE_MISMATCH 12, VEC_ONLY_LIVE_NO_SIGNAL 9, VEC_ONLY_STATE_CASCADE 9, NPZ_STALE 1

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|
| trb:ADBE_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 4, LIVE_ALREADY_FLAT 4 | - | False/True | last window | trb/active_config |
| trc:ADBE_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 4, LIVE_NO_SIGNAL 4 | - | False/True | last window | trb/active_config |
| trb:AAPL_LONG | VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 1, LIVE_ALREADY_FLAT 1 | - | False/False | last window | trb/active_config |
| trb:AAPL_SHORT | VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | last window | trb/active_config |
| trb:ABT_SHORT | VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | last window | trb/active_config |
| trb:ALB_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | False/True | last window | trb/active_config |
| trb:ALKT_SHORT | VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | last window | defaults(cat_side) |
| trb:AMD_LONG | VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | last window | trb/active_config |
| trb:APO_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | False/True | last window | trb/active_config |
| trb:AR_SHORT | VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | last window | defaults(cat_side) |
| trb:AXTI_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | False/True | last window | trb/active_config |
| trb:FIX_LONG | NPZ_STALE, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/False | 2026-09-30T16:45Z..2026-10-01T16:45Z | defaults(cat_side) |
| trc:ALB_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | False/True | last window | trb/active_config |
| trc:APO_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | False/True | last window | trb/active_config |
| trc:AVGO_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | False/True | last window | per_sym_active_config_stocks |
| trb:BWXT_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:AXON_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:A_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trc:BWXT_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |

PASS with activity: trb:ACN_LONG  ·  PASS_IDLE (no decision either side): 5

NO_DATA reasons: prepare failed (no npz) 119

### Per function family (stocks)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| FINAL_MTM | 15m | Y | safety (exempt) | 0 | 0 | 14 | 0 | 14 | 0 | FAIL |
| DC_DAYTRADE_TARGET | 15m | Y | DC_DAYTRADE_ENABLED | 0 | 0 | 7 | 0 | 7 | 0 | FAIL |
| GAP_FILL | 5m | N | ? (switch not identified) | 0 | 6 | 0 | 0 | 0 | 0 | PASS |
| UNCLASSIFIED:B_EMASLOPE | 15m | Y | ? (switch not identified) | 0 | 0 | 4 | 0 | 4 | 0 | FAIL |
| UNCLASSIFIED:B | 15m | Y | ? (switch not identified) | 0 | 0 | 2 | 0 | 2 | 0 | FAIL |
| HARDCODED_RALLY_REENTRY | 15m | Y | HARDCODED_RALLY_REENTRY_ENABLED | 0 | 0 | 2 | 0 | 2 | 0 | FAIL |
| UNCLASSIFIED:B_WT_M | 15m | Y | ? (switch not identified) | 0 | 0 | 1 | 0 | 1 | 0 | FAIL |

## Test 2 - LIVE_ONLY functions (1m/3m/5m, tick, webhook, portfolio, no vec twin), STOCKS

These decisions have no 15m vector counterpart by construction; listed so drift is visible (attempts = execute_now calls in the window).

| function family | input TF | attempts | fills | gate switch |
|---|---|---|---|---|

## decisions -> history parity (switch intents vs live fills)

`DH_20261005_20261006.json` generated 2026-10-06T05:17:00.704286+00:00 (43 min ago) · intents 65 · matched 33 · **missing 32**

matched/intents per account: fin 4/9, men 6/19, ang 14/27, inf 0/0 (no decision file), flz 9/10, trb 0/0 (no decision file), trc 0/0 (no decision file)

| function family | action | intent result | n | blocking execution filter | vector same decision +-2 bars (Y/N/? = not judged) |
|---|---|---|---|---|---|
| EXIT_VELOCITY_WT | CLOSE | MISSING | 10 | NUKE_STALE_MAKER_ORDER 10 | N 9, ? 1 |
| BB_RECOVERY_EXIT | REDUCE | MISSING | 7 | BLOCKED_MAKER_SUPPRESS_WEBHOOK 5, HARD_REDUCE_LOCK 1, NUKE_STALE_MAKER_ORDER 1 | ? 7 |
| WT_LOWER_CROSS_EXIT | CLOSE | MISSING | 5 | NUKE_STALE_MAKER_ORDER 4, UNATTRIBUTED 1 | ? 4, N 1 |
| QUICK_REDUCE_STRONG | STRONG_REDUCE | MISSING | 4 | NUKE_STALE_MAKER_ORDER 4 | N 4 |
| GOLDEN_RULE | OPEN | MISSING | 2 | MAKER_FAILED_SUPPRESS_WEBHOOK 2 | ? 2 |
| RATIO_REBALANCE | REDUCE | MISSING | 2 | NUKE_STALE_MAKER_ORDER 1, BLOCKED_MAKER_SUPPRESS_WEBHOOK 1 | ? 2 |
| QUICK_OPEN_STRONG | QUICK_OPEN | MISSING | 2 | MAKER_ZERO_QTY 2 | N 2 |
| EXIT_VELOCITY_WT | CLOSE | MATCHED | 8 | filled 8 | N 6, ? 2 |
| QUICK_OPEN_STRONG | QUICK_OPEN | MATCHED | 7 | filled 7 | N 6, ? 1 |
| GOLDEN_RULE | OPEN | MATCHED | 6 | filled 6 | ? 6 |
| QUICK_REDUCE_STRONG | STRONG_REDUCE | MATCHED | 3 | filled 3 | N 3 |
| BB_RECOVERY_EXIT | REDUCE | MATCHED | 3 | filled 3 | ? 3 |
| WT_LOWER_CROSS_EXIT | CLOSE | MATCHED | 2 | filled 2 | ? 2 |
| GUARANTEED_REENTRY | OPEN | MATCHED | 2 | filled 2 | N 2 |
| DC_DAYTRADE_TARGET | CLOSE | MATCHED | 1 | filled 1 | ? 1 |
| GAIN_EROSION_STOP | CLOSE | MATCHED | 1 | filled 1 | N 1 |

Gap classes: NUKE_STALE_MAKER_ORDER = maker exit rested >60 s and was cancelled (microstructure, no vec twin); UNATTRIBUTED = no blocking log line found (logging hook missing in the execution path); MAKER_ZERO_QTY / MARKET_API_ERROR = sizing/broker. Operator decisions: `data/decisions_history_parity/NEEDS-OPERATOR-DECISION_*.md`.

## Exit-engine gate parity (execute_now, all exits)

generated 2026-10-06T05:59:40.734436+00:00 (0 min ago) · lookback 48.0h · exit rows 3086 · **LEAKS 662** · gate ON 1298 · UNGATED 1076 · safety 4 · unmapped 0

LEAKS (gate OFF for that sym_side yet fired): QUICK_OPEN_STRONG 660, WT_DC_ENTRY (WT_DC_ENTRY_ENABLED=False) 2

UNGATED families (fire with no switch — live-side switch hook missing, cannot be turned off): RANKING_DIRECT_ALL_GREEN 1048, RANKING_DIRECT_ALL_RED 28

## 7-day rollup (tools/daily_parity_test.py)

`data/parity/daily_parity_20260830.md`

- Generated: 2026-08-30T20:47:26.351754+00:00  | window: trailing 7 days  | accounts: ang,inf,flz,men,fin
- Reference = per_sym_active_config.json `wsharpe` (the config-settings backtest sharpe). Live = realized from /history/.
- Symbols with live trades in window: **7**  | DESYNC flagged: **1**
- live pool_sharpe=+0.7010 [DIAGNOSTIC ONLY · n_syms=7 · years=0.02] | tier=Best-of-current
- live avg_gain_trade=+1.2891%/trade | window_total_gain=+25.78% (over 7d) | trades=20 | n_syms=7
- (annualized extrapolation, window<<1yr so read with care: gain_per_yr=+1345.2%/yr | gain_sym_yr=+192.1770%/sym/yr | years=0.019)
