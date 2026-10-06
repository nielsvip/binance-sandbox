# Forward parity — LIVE vs VECTOR (auto, read-only)

generated 2026-10-06T04:30:20+00:00 · producers: tools/forward_parity/live_vs_vec.py (crypto hourly :07, stocks every 15 min RTH), tools/decisions_history_parity.py (:17), tools/exit_engine_parity_monitor.py (launchd hourly), tools/daily_parity_test.py (launchd daily)

PASS = every vector decision on the judged 15m bars has a live fill of the same class within +-2 bars, every live fill has a vector decision, and end state (in/out) agrees. FAIL classes: VEC_ONLY_LIVE_NO_SIGNAL (live logic did not fire), VEC_ONLY_LIVE_BLOCKED (live attempted, gate/execution stopped it), VEC_ONLY_STATE_CASCADE (consequence of an earlier divergence), LIVE_ONLY_VEC_FAMILY (vector twin exists but did not fire), LIVE_ONLY_NONVEC (1m/3m/5m/tick/webhook/portfolio input — no vec counterpart), STATE_MISMATCH, NPZ_STALE (S1 NPZ behind live, judged on the newest window the vector can see).

## Test 1 - 15m LIVE vs VECTOR, CRYPTO

run 2026-10-06T04:26:34+00:00 (4 min ago) · window 24.0h from 2026-10-05T04:22Z · tol +-2 bars · 212 live keys / 120 sym_sides · 245.8 s

**Verdict: FAIL** — PASS 166 · FAIL 31 · NO_DATA 15 · judged on a STALE-shifted window (S1 NPZ behind live) 183

- vector fired, live did not (VEC_ONLY): LIVE_NO_SIGNAL 63, LIVE_ALREADY_FLAT 57, LIVE_ALREADY_IN_POSITION 6
- live fired, vector did not (LIVE_ONLY): LIVE_ONLY_VEC_FAMILY:GOLDEN_RULE 1, LIVE_ONLY_NONVEC:ALL_TF_AGAINST_CLOSE@3m 1
- NPZ missing on S1 (no vector possible): 100PEPEUSDC, EDUUSDT, KMNOUSDT, MELANIAUSDT
- vector errors: prepare failed (no npz) 11
- FAIL classes (sym_sides): NPZ_STALE 27, STATE_MISMATCH 24, VEC_ONLY_LIVE_NO_SIGNAL 12, VEC_ONLY_STATE_CASCADE 12, LIVE_ONLY_NONVEC 1, LIVE_ONLY_VEC_FAMILY 1

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|
| men:AXSUSDT_SHORT (VD) | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 10, LIVE_ALREADY_FLAT 10 | - | False/False | 2026-10-01T01:00Z..2026-10-02T01:00Z | vec_driven:candidates:5ca4cb |
| men:LINKUSDC_SHORT | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 10, LIVE_ALREADY_FLAT 10 | - | False/False | 2026-10-01T03:30Z..2026-10-02T03:30Z | defaults(cat_side) |
| ang:1INCHUSDT_SHORT | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 6, LIVE_ALREADY_FLAT 6 | - | True/False | last window | vec_driven:candidates:63e881 |
| fin:1INCHUSDT_SHORT (VD) | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 6, LIVE_ALREADY_FLAT 6 | - | True/False | last window | vec_driven:candidates:63e881 |
| inf:1INCHUSDT_SHORT (VD) | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 6, LIVE_ALREADY_FLAT 6 | - | True/False | last window | vec_driven:candidates:63e881 |
| men:1INCHUSDT_SHORT (VD) | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 6, LIVE_ALREADY_FLAT 6 | - | True/False | last window | vec_driven:candidates:63e881 |
| men:LTCUSDC_SHORT | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 4, LIVE_ALREADY_FLAT 4 | - | False/False | 2026-09-29T19:30Z..2026-09-30T19:30Z | defaults(cat_side) |
| ang:YFIUSDT_SHORT | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 3, LIVE_ALREADY_FLAT 3 | - | False/False | 2026-10-03T07:15Z..2026-10-04T07:15Z | defaults(cat_side) |
| fin:ALGOUSDT_SHORT (VD) | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 3, LIVE_NO_SIGNAL 3 | - | False/True | 2026-10-03T06:45Z..2026-10-04T06:45Z | vec_driven:candidates:ed033e |
| inf:ALGOUSDT_SHORT (VD) | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 3, LIVE_ALREADY_FLAT 3 | - | False/False | 2026-10-03T06:45Z..2026-10-04T06:45Z | vec_driven:candidates:ed033e |
| men:ALGOUSDT_SHORT (VD) | NPZ_STALE, STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_IN_POSITION 3, LIVE_NO_SIGNAL 3 | - | False/True | 2026-10-03T06:45Z..2026-10-04T06:45Z | vec_driven:candidates:ed033e |
| men:YFIUSDT_SHORT | NPZ_STALE, VEC_ONLY_LIVE_NO_SIGNAL, VEC_ONLY_STATE_CASCADE | LIVE_NO_SIGNAL 3, LIVE_ALREADY_FLAT 3 | - | False/False | 2026-10-03T07:15Z..2026-10-04T07:15Z | defaults(cat_side) |
| flz:DASHUSDT_LONG | LIVE_ONLY_NONVEC, LIVE_ONLY_VEC_FAMILY, NPZ_STALE | - | LIVE_ONLY_VEC_FAMILY:GOLDEN_RULE 1, LIVE_ONLY_NONVEC:ALL_TF_AGAINST_CLOSE@3m 1 | False/False | 2026-10-03T06:45Z..2026-10-04T06:45Z | defaults(cat_side) |
| ang:GALAUSDT_SHORT | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-01T03:00Z..2026-10-02T03:00Z | per_sym_active_config:clean_ |
| ang:XLMUSDT_LONG | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-03T07:15Z..2026-10-04T07:15Z | vec_driven:progress:a73eac61 |
| flz:BNBUSDC_LONG (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-01T01:00Z..2026-10-02T01:00Z | vec_driven:candidates:ea9eb6 |
| flz:BTCUSDC_LONG (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-01T01:30Z..2026-10-02T01:30Z | vec_driven:candidates:f6f02f |
| flz:DASHUSDT_SHORT | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-03T06:45Z..2026-10-04T06:45Z | per_sym_active_config:clean_ |
| flz:ETHUSDC_LONG | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-01T02:00Z..2026-10-02T02:00Z | per_sym_active_config:clean_ |
| flz:GRAMUSDT_SHORT | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-09-30T01:30Z..2026-10-01T01:30Z | defaults(cat_side) |
| flz:HYPEUSDT_LONG | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-01T02:45Z..2026-10-02T02:45Z | per_sym_active_config:clean_ |
| flz:HYPEUSDT_SHORT | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-01T02:45Z..2026-10-02T02:45Z | per_sym_active_config:clean_ |
| flz:SOLUSDC_LONG | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-01T03:30Z..2026-10-02T03:30Z | per_sym_active_config:clean_ |
| flz:XRPUSDC_SHORT | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-01T02:30Z..2026-10-02T02:30Z | per_sym_active_config:clean_ |
| flz:ZECUSDC_LONG | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-01T04:30Z..2026-10-02T04:30Z | per_sym_active_config:clean_ |
| inf:IOTAUSDT_LONG (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-03T07:00Z..2026-10-04T07:00Z | vec_driven:progress:07b10a2f |
| inf:SANDUSDT_SHORT | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-03T06:45Z..2026-10-04T06:45Z | per_sym_active_config:clean_ |
| men:COTIUSDT_LONG (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-03T06:45Z..2026-10-04T06:45Z | vec_driven_MD5_MISMATCH:07b1 |
| men:KSMUSDT_LONG (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-03T06:45Z..2026-10-04T06:45Z | vec_driven:progress:e3f3ba65 |
| men:LINKUSDC_LONG (VD) | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-01T03:30Z..2026-10-02T03:30Z | vec_driven:progress:558e933b |
| men:XRPUSDC_LONG | NPZ_STALE, STATE_MISMATCH | - | - | False/True | 2026-10-01T02:30Z..2026-10-02T02:30Z | per_sym_active_config:clean_ |

PASS with activity: -  ·  PASS_IDLE (no decision either side): 166

NO_DATA reasons: prepare failed (no npz) 15

### Per function family (crypto)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| RANKING_DIRECT_ALL_GREEN | webhook | N | **UNGATED** | 479 | 0 | 0 | 0 | 0 | 0 | IDLE |
| GOLDEN_RULE | 15m | Y | GOLDEN_RULE_ENABLED | 215 | 2 | 0 | 0 | 0 | 1 | FAIL |
| EXIT_VELOCITY_WT | 1h | Y | EXIT_VELOCITY_WT_ENABLED | 100 | 5 | 2 | 0 | 40 | 0 | FAIL |
| QUICK_OPEN_STRONG | 3m | Y | QUICK_OPEN_STRONG_VEC_ENABLED | 95 | 7 | 0 | 0 | 0 | 0 | PASS |
| CRYPTO_SPIKE_FADE | 3m | N | CRYPTO_SPIKE_FADE_ENABLED | 28 | 0 | 0 | 0 | 0 | 0 | IDLE |
| RANKING_DIRECT_ALL_RED | webhook | N | **UNGATED** | 27 | 0 | 0 | 0 | 0 | 0 | IDLE |
| BB_RECOVERY_EXIT | 15m | N | BB_RECOVERY_DIRECT_ENABLED | 23 | 1 | 0 | 0 | 0 | 0 | PASS |
| QUICK_REDUCE_STRONG | 3m | N | ABLATION_DISABLE_QUICK_EXIT | 21 | 1 | 0 | 0 | 0 | 0 | PASS |
| WT_LOWER_CROSS_EXIT | 1h | Y | WT_LOWER_CROSS_EXIT_TF | 17 | 0 | 0 | 0 | 0 | 0 | IDLE |
| QUICK_REDUCE_NO_PROFIT | 1m | N | ABLATION_DISABLE_QUICK_EXIT | 12 | 0 | 0 | 0 | 0 | 0 | IDLE |
| WEBHOOK_HANDLE_SIGNAL | webhook | N | **UNGATED** | 10 | 0 | 0 | 0 | 0 | 0 | IDLE |
| QUICK_REDUCE_SCALP | 1m | N | ABLATION_DISABLE_SCALP_GUARD | 5 | 0 | 0 | 0 | 0 | 0 | IDLE |
| GUARANTEED_REENTRY | 3m | Y | **UNGATED** | 2 | 2 | 0 | 0 | 0 | 0 | PASS |
| DC_DAYTRADE_TARGET | 15m | Y | DC_DAYTRADE_ENABLED | 3 | 0 | 1 | 0 | 7 | 0 | FAIL |
| UNCLASSIFIED:B_KZONE | 15m | N | **UNGATED** | 0 | 0 | 4 | 0 | 37 | 0 | LIVE_ONLY(no 15m vec twin) |
| UNCLASSIFIED:QUICK_OPEN_GOOD | 15m | N | **UNGATED** | 3 | 0 | 0 | 0 | 0 | 0 | IDLE |
| QUICK_REDUCE_OTHER | 3m | N | ABLATION_DISABLE_QUICK_EXIT | 3 | 0 | 0 | 0 | 0 | 0 | IDLE |
| RATIO_REBALANCE | portfolio | N | ABLATION_DISABLE_RATIO_REBALANCE | 2 | 0 | 0 | 0 | 0 | 0 | IDLE |
| UNCLASSIFIED:QUICK_DC_HIGH | 3m | N | **UNGATED** | 1 | 1 | 0 | 0 | 0 | 0 | PASS |
| UNCLASSIFIED:B | 15m | N | **UNGATED** | 0 | 0 | 2 | 0 | 15 | 0 | LIVE_ONLY(no 15m vec twin) |
| UNCLASSIFIED:HARDCODED_RALLY_CLOSE< | 15m | N | **UNGATED** | 1 | 0 | 0 | 0 | 0 | 0 | IDLE |
| DELTA_EXIT | 3m | N | DELTA_EXIT_ENABLED | 1 | 0 | 0 | 0 | 0 | 0 | IDLE |
| UNCLASSIFIED:MTF_GR_WT | 15m | N | **UNGATED** | 0 | 0 | 1 | 0 | 4 | 0 | LIVE_ONLY(no 15m vec twin) |
| MTF_ATR_TRAIL | 15m | Y | MTF_ATR_TRAIL_ENABLED | 0 | 0 | 1 | 0 | 7 | 0 | FAIL |
| ALL_TF_AGAINST_CLOSE | 15m | Y | ALL_TF_AGAINST_CLOSE_ENABLED | 0 | 0 | 1 | 0 | 4 | 1 | FAIL |
| UNCLASSIFIED:B_SRS_ENTRY | 15m | N | **UNGATED** | 0 | 0 | 1 | 0 | 1 | 0 | LIVE_ONLY(no 15m vec twin) |
| FINAL_MTM | 15m | Y | **UNGATED** | 0 | 0 | 1 | 0 | 0 | 0 | PASS |

## Test 2 - LIVE_ONLY functions (1m/3m/5m, tick, webhook, portfolio, no vec twin), CRYPTO

These decisions have no 15m vector counterpart by construction; listed so drift is visible (attempts = execute_now calls in the window).

| function family | input TF | attempts | fills | gate switch |
|---|---|---|---|---|
| RANKING_DIRECT_ALL_GREEN | webhook | 479 | 0 | **UNGATED (no switch)** |
| QUICK_OPEN_STRONG | 3m | 95 | 7 | QUICK_OPEN_STRONG_VEC_ENABLED |
| CRYPTO_SPIKE_FADE | 3m | 28 | 0 | CRYPTO_SPIKE_FADE_ENABLED |
| RANKING_DIRECT_ALL_RED | webhook | 27 | 0 | **UNGATED (no switch)** |
| BB_RECOVERY_EXIT | 15m | 23 | 1 | BB_RECOVERY_DIRECT_ENABLED |
| QUICK_REDUCE_STRONG | 3m | 21 | 1 | ABLATION_DISABLE_QUICK_EXIT |
| QUICK_REDUCE_NO_PROFIT | 1m | 12 | 0 | ABLATION_DISABLE_QUICK_EXIT |
| WEBHOOK_HANDLE_SIGNAL | webhook | 10 | 0 | **UNGATED (no switch)** |
| QUICK_REDUCE_SCALP | 1m | 5 | 0 | ABLATION_DISABLE_SCALP_GUARD |
| UNCLASSIFIED:QUICK_OPEN_GOOD | 15m | 3 | 0 | **UNGATED (no switch)** |
| QUICK_REDUCE_OTHER | 3m | 3 | 0 | ABLATION_DISABLE_QUICK_EXIT |
| GUARANTEED_REENTRY | 3m | 2 | 2 | **UNGATED (no switch)** |
| RATIO_REBALANCE | portfolio | 2 | 0 | ABLATION_DISABLE_RATIO_REBALANCE |
| UNCLASSIFIED:QUICK_DC_HIGH | 3m | 1 | 1 | **UNGATED (no switch)** |
| UNCLASSIFIED:HARDCODED_RALLY_CLOSE< | 15m | 1 | 0 | **UNGATED (no switch)** |
| DELTA_EXIT | 3m | 1 | 0 | DELTA_EXIT_ENABLED |

## Test 3 - VEC_DRIVEN bridge (S1 vec intents -> live fills)

intents in window: 1 · result: NOT_IN_MAC_REGISTRY 1 · consumer files: **none (live consumer has not written data/vec_live/live_exec_*.jsonl)**

execute_now gate refusals tagged VEC_DRIVEN in ez_manage logs: {'fin': {'UNKNOWN': 20}, 'men': {'UNKNOWN': 32}, 'ang': {'UNKNOWN': 26}, 'inf': {'UNKNOWN': 12}}

| bar | ss | acct | type | vec reason | result |
|---|---|---|---|---|---|
| 2026-10-06T03:30Z | 1000BONKUSDC_SHORT | - | CLOSE | E_3_STRUCTURE_EXIT_2TF_15m_4h | NOT_IN_MAC_REGISTRY |

## Test 1 - 15m LIVE vs VECTOR, STOCKS

No run yet (`data/forward_parity/stocks_latest.json` missing).

## decisions -> history parity (switch intents vs live fills)

`DH_20261005_20261006.json` generated 2026-10-06T04:17:01.211804+00:00 (13 min ago) · intents 40 · matched 24 · **missing 16**

matched/intents per account: fin 2/4, men 4/13, ang 9/13, inf 0/0 (no decision file), flz 9/10, trb 0/0 (no decision file), trc 0/0 (no decision file)

| function family | action | missing | blocking execution filter (live log attribution) |
|---|---|---|---|
| EXIT_VELOCITY_WT | CLOSE | 7 | NUKE_STALE_MAKER_ORDER 7 |
| WT_LOWER_CROSS_EXIT | CLOSE | 5 | NUKE_STALE_MAKER_ORDER 4, UNATTRIBUTED 1 |
| GOLDEN_RULE | OPEN | 2 | MAKER_FAILED_SUPPRESS_WEBHOOK 2 |
| QUICK_OPEN_STRONG | QUICK_OPEN | 2 | MAKER_ZERO_QTY 2 |

Gap classes: NUKE_STALE_MAKER_ORDER = maker exit rested >60 s and was cancelled (microstructure, no vec twin); UNATTRIBUTED = no blocking log line found (logging hook missing in the execution path); MAKER_ZERO_QTY / MARKET_API_ERROR = sizing/broker. Operator decisions: `data/decisions_history_parity/NEEDS-OPERATOR-DECISION_*.md`.

## Exit-engine gate parity (execute_now, all exits)

generated 2026-10-06T04:30:17.996032+00:00 (0 min ago) · lookback 48.0h · exit rows 2429 · **LEAKS 111** · gate ON 1131 · UNGATED 1151 · safety 4 · unmapped 0

LEAKS (gate OFF for that sym_side yet fired): QUICK_OPEN_STRONG 109, WT_DC_ENTRY (WT_DC_ENTRY_ENABLED=False) 2

UNGATED families (fire with no switch — live-side switch hook missing, cannot be turned off): RANKING_DIRECT_ALL_GREEN 1118, RANKING_DIRECT_ALL_RED 33

## 7-day rollup (tools/daily_parity_test.py)

`data/parity/daily_parity_20260830.md`

- Generated: 2026-08-30T20:47:26.351754+00:00  | window: trailing 7 days  | accounts: ang,inf,flz,men,fin
- Reference = per_sym_active_config.json `wsharpe` (the config-settings backtest sharpe). Live = realized from /history/.
- Symbols with live trades in window: **7**  | DESYNC flagged: **1**
- live pool_sharpe=+0.7010 [DIAGNOSTIC ONLY · n_syms=7 · years=0.02] | tier=Best-of-current
- live avg_gain_trade=+1.2891%/trade | window_total_gain=+25.78% (over 7d) | trades=20 | n_syms=7
- (annualized extrapolation, window<<1yr so read with care: gain_per_yr=+1345.2%/yr | gain_sym_yr=+192.1770%/sym/yr | years=0.019)
