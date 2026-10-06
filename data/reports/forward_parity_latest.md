# Forward parity — LIVE vs VECTOR (auto, read-only)

generated 2026-10-06T19:00:22+00:00 · producers: tools/forward_parity/live_vs_vec.py (crypto hourly :07, stocks every 15 min RTH), tools/decisions_history_parity.py (:17), tools/exit_engine_parity_monitor.py (launchd hourly), tools/daily_parity_test.py (launchd daily)

**REGIME PARITY_VEC_EXACT since 2026-10-06T18:48:00Z** — all monitors reset at the switch; old-regime results archived in `data/forward_parity/archive/pre_vec_exact_until_20261006T1848Z`. Universe: tradeable_keys.json + open positions only.

PASS (exact regime) = every vec decision on the judged bars is a live fill at the same bar/side or a logged hard-safety refusal, and every live fill is a vec decision. FAIL classes: VEC_DECISION_NOT_FILLED_NO_LOG, VEC_DECISION_REFUSED_NON_SAFETY, NATIVE_FILL_IN_EXACT_MODE (a non-vec live path traded), VEC_EXACT_FILL_NOT_IN_REPLAY (live vec on live_klines vs replay on the S1 NPZ disagree), STATE_CASCADE, NPZ_STALE.

PASS (legacy definition) = every vector decision on the judged 15m bars has a live fill of the same class within +-2 bars, every live fill has a vector decision, and end state (in/out) agrees. FAIL classes: VEC_ONLY_LIVE_NO_SIGNAL (live logic did not fire), VEC_ONLY_LIVE_BLOCKED (live attempted, gate/execution stopped it), VEC_ONLY_STATE_CASCADE (consequence of an earlier divergence), LIVE_ONLY_VEC_FAMILY (vector twin exists but did not fire), LIVE_ONLY_NONVEC (1m/3m/5m/tick/webhook/portfolio input — no vec counterpart), STATE_MISMATCH, NPZ_STALE (S1 NPZ behind live, judged on the newest window the vector can see).

## Test 1 - 15m LIVE vs VECTOR, CRYPTO

No run yet (`data/forward_parity/crypto_latest.json` missing).

## Test 1 - 15m LIVE vs VECTOR, STOCKS

run 2026-10-06T18:53:37+00:00 (7 min ago) · engine v12 918b5bdc · NPZ sync tail · window 24.0h from 2026-10-05T18:52Z · tol +-2 bars · 42 live keys / 31 sym_sides · 94.7 s

**Verdict: FAIL** — FAIL 26 · PASS with decisions 0 · IDLE (no decision either side, counts as pass) 16 · NO_DATA 0 · judged on a STALE-shifted window (S1 NPZ behind live) 0

- vector fired, live did not (VEC_ONLY): LIVE_ALREADY_FLAT 2, LIVE_NO_SIGNAL 1
- live fired, vector did not (LIVE_ONLY): -
- vector replay made 0 trades in 30D for 16/31 sym_sides with the live set (engine md5 918b5bdc): every live fill on those keys is LIVE_ONLY by construction — engine/set problem, not live drift. e.g. AXTI_LONG, A_LONG, BWXT_SHORT, CLS_LONG, CRWD_LONG, EXEL_LONG, GOOGL_LONG, MSFT_LONG
- FAIL classes (sym_sides): STATE_MISMATCH 26, VEC_ONLY_STATE_CASCADE 2, VEC_ONLY_LIVE_NO_SIGNAL 1

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|
| trb:IBIT_LONG | STATE_MISMATCH, VEC_ONLY_LIVE_NO_SIGNAL | LIVE_NO_SIGNAL 1 | - | False/True | last window | trb/active_config |
| trc:IBIT_LONG | STATE_MISMATCH, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/True | last window | trb/active_config |
| trc:UEC_SHORT | STATE_MISMATCH, VEC_ONLY_STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | False/True | last window | trb/active_config |
| trb:AXTI_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:BWXT_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:CLS_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:CRWD_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:EQT_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:EXEL_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:GOOGL_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:MSFT_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:NKE_SHORT | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:NVDA_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:QQQ_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:SNDK_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
| trb:TSM_LONG | STATE_MISMATCH | - | - | False/True | last window | trb/active_config |
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

PASS with activity: -  ·  PASS_IDLE (no decision either side): 16

### Per function family (stocks)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| BROKER_SYNC | tick | N | safety (exempt) | 0 | 450 | 0 | 0 | 0 | 0 | PASS |
| DC_BREAK | 15m | N | ? (switch not identified) | 0 | 2 | 0 | 0 | 0 | 0 | PASS |
| DC_DAYTRADE_TARGET | 15m | Y | DC_DAYTRADE_ENABLED | 0 | 0 | 2 | 0 | 2 | 0 | FAIL |
| REENTRY_OTHER | 15m | Y | ABLATION_DISABLE_REENTRY | 0 | 1 | 0 | 0 | 0 | 0 | PASS |
| GAP_MOC | 5m | Y | ? (switch not identified) | 0 | 0 | 1 | 0 | 1 | 0 | FAIL |

## Test 2 - LIVE_ONLY functions (1m/3m/5m, tick, webhook, portfolio, no vec twin), STOCKS

These decisions have no 15m vector counterpart by construction; listed so drift is visible (attempts = execute_now calls in the window).

| function family | input TF | attempts | fills | gate switch |
|---|---|---|---|---|

## Sizing — live-sized fills vs the same trades at vec size (paper)

Same trades (live entry fill matched to the vec decision at that bar), closed PnL of exits since the regime switch. live = live qty x live prices; vec-size = the same exits scaled by vec notional / live notional of the matched entries (the exact vectorized size the paper test executes). sizing alpha = live - vec-size. Factors = sizing multipliers logged by ez_manage within 10 min before the entry fill (HELD_x1:* = resolved but forced x1.0 by PARITY_LIVE_SIZING_MULT_ENABLED=False). Open positions are not counted until they exit.

### CRYPTO — totals per day

no exits since the switch yet

### STOCKS — totals per day

no exits since the switch yet

(no matched entries yet)

## decisions -> history parity (switch intents vs live fills) — rows since 2026-10-06T18:48:00+00:00 only

`DH_20261005_20261006.json` generated 2026-10-06T18:17:02.821182+00:00 (43 min ago) · intents 448 · matched 35 · **missing 413**

matched/intents per account: fin 4/9, men 8/37, ang 14/27, inf 0/0 (no decision file), flz 9/10, trb 0/201, trc 0/164

Gap classes: NUKE_STALE_MAKER_ORDER = maker exit rested >60 s and was cancelled (microstructure, no vec twin); UNATTRIBUTED = no blocking log line found (logging hook missing in the execution path); MAKER_ZERO_QTY / MARKET_API_ERROR = sizing/broker. Operator decisions: `data/decisions_history_parity/NEEDS-OPERATOR-DECISION_*.md`.

## Exit-engine gate parity (execute_now, all exits)

generated 2026-10-06T19:00:22.014298+00:00 (0 min ago) · lookback 48.0h · exit rows 0 · **LEAKS 0** · gate ON 0 · UNGATED 0 · safety 0 · unmapped 0

## 7-day rollup (tools/daily_parity_test.py)

`data/parity/daily_parity_20261006.md`

- Generated: 2026-10-06T06:15:12+00:00 | engine: tools/forward_parity/live_vs_vec.py (same bars, same per-sym set)
- crypto: 139 live keys · status {'PASS_IDLE': 98, 'NO_DATA': 13, 'STALE_PASS_IDLE': 11, 'FAIL': 17} · VEC_ONLY {'LIVE_ALREADY_IN_POSITION': 3, 'LIVE_NO_SIGNAL': 3} · LIVE_ONLY {'LIVE_ONLY_VEC_FAMILY:GOLDEN_RULE': 15, 'LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT': 7, 'LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m': 6, 'LIVE_ONLY_NONVEC:RANKING_DIRECT_ALL_GREEN@webhook': 5, 'LIVE_ONLY_VEC_FAMILY:DC_DAYTRADE_TARGET': 4, 'LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m': 3, 'LIVE_ONLY_VEC_FAMILY:WT_LOWER_CROSS_EXIT': 1, 'LIVE_ONLY_NONVEC:RATIO_REBALANCE@portfolio': 1, 'LIVE_ONLY_NONVEC:BREAK_EVEN_GUARD@tick': 1, 'LIVE_ONLY_VEC_FAMILY:MTF_BB_REJECT_EXIT': 1, 'LIVE_ONLY_NONVEC:ALL_TF_AGAINST_CLOSE@3m': 1, 'LIVE_ONLY_NONVEC:GUARANTEED_REENTRY@3m': 1, 'LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY': 1, 'LIVE_ONLY_NONVEC:GAIN_EROSION_STOP@3m': 1}
- stocks: 175 live keys · status {'FAIL': 151, 'PASS_IDLE': 19, 'NO_DATA': 4, 'STALE_PASS_IDLE': 1} · VEC_ONLY {'LIVE_NO_SIGNAL': 208, 'LIVE_ALREADY_FLAT': 162, 'LIVE_ALREADY_IN_POSITION': 28, 'LIVE_INTENT_NOT_FILLED:UNCLASSIFIED:?': 2} · LIVE_ONLY {'LIVE_ONLY_NONVEC:REENTRY_OTHER@5m': 226, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:SYNC_DETECTION@15m': 71, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:MFI_MEAN_REVERSION@15m': 57, 'LIVE_ONLY_VEC_FAMILY:REENTRY_OTHER': 47, 'LIVE_ONLY_NONVEC:FALLBACK_CLOSE@15m': 27, 'LIVE_ONLY_NONVEC:GAP_MOC@5m': 24, 'LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY': 23, 'LIVE_ONLY_VEC_FAMILY:DC_DAYTRADE_TARGET': 21, 'LIVE_ONLY_VEC_FAMILY:HARDCODED_RALLY_REENTRY': 18, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TARGET@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DC_BREAK_HIGH@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:ULTIMATE_DC_H@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@15m': 11, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:INTRADAY_RATIO_TRIM@15m': 5, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:STRUCT_BREAK_DC@15m': 5, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:SENT_STRAT_DIV@15m': 3, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:OVERBOUGHT_TAKE_PROFIT@15m': 2, 'LIVE_ONLY_NONVEC:GAP_FILL@5m': 2, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:GR_HTF_DIRECT@3m': 2, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:NOLOSS_BBH_BREAKDOWN@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@3m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@5m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:ROTATION_ENTRY_L@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:CLENOW_ENTRY@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:STRUCT_BREAK_DC@5m': 1}
- Totals: PASS 129 · FAIL 168 · sign flips (>=5 exits each side) 0 · alerts 86
