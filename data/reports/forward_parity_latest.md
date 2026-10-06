# Forward parity — LIVE vs VECTOR (auto, read-only)

generated 2026-10-06T20:11:32+00:00 · producers: tools/forward_parity/live_vs_vec.py (crypto hourly :07, stocks every 15 min RTH), tools/decisions_history_parity.py (:17), tools/exit_engine_parity_monitor.py (launchd hourly), tools/daily_parity_test.py (launchd daily)

**REGIME PARITY_VEC_EXACT since 2026-10-06T18:48:00Z** — all monitors reset at the switch; old-regime results archived in `data/forward_parity/archive/pre_vec_exact_until_20261006T1848Z`. Universe: tradeable_keys.json + open positions only.

PASS (exact regime) = every vec decision on the judged bars is a live fill at the same bar/side or a logged hard-safety refusal, and every live fill is a vec decision. FAIL classes: VEC_DECISION_NOT_FILLED_NO_LOG, VEC_DECISION_REFUSED_NON_SAFETY, NATIVE_FILL_IN_EXACT_MODE (a non-vec live path traded), VEC_EXACT_FILL_NOT_IN_REPLAY (live vec on live_klines vs replay on the S1 NPZ disagree), STATE_CASCADE, NPZ_STALE.

PASS (legacy definition) = every vector decision on the judged 15m bars has a live fill of the same class within +-2 bars, every live fill has a vector decision, and end state (in/out) agrees. FAIL classes: VEC_ONLY_LIVE_NO_SIGNAL (live logic did not fire), VEC_ONLY_LIVE_BLOCKED (live attempted, gate/execution stopped it), VEC_ONLY_STATE_CASCADE (consequence of an earlier divergence), LIVE_ONLY_VEC_FAMILY (vector twin exists but did not fire), LIVE_ONLY_NONVEC (1m/3m/5m/tick/webhook/portfolio input — no vec counterpart), STATE_MISMATCH, NPZ_STALE (S1 NPZ behind live, judged on the newest window the vector can see).

## LIVE GUARDIAN (every 20 s, both systems) — tools/forward_parity/live_guardian.py

last sweep 2026-10-06T20:11:31Z (0 min ago) · 0.62 s · dry_run=False · RTH=False · regime start 2026-10-06T18:48:00Z

thresholds: STORM_KEY_FILLS=6, STORM_KEY_MIN=10, ATTEMPT_STORM=30, ATTEMPT_MIN=5, CHURN_ROUNDTRIPS=3, DUP_S=20, ACCOUNT_STORM_FILLS=25, ACCOUNT_STORM_MIN=10, ACCOUNT_DUP_PAIRS=3, FILL_WAIT_S=600, SIZE_MULT=3.0, LOSS_DROP_PP=4.0, LOSS_FLOOR_PCT=-3.0, EQUITY_DROP_USD=25.0, RESTARTS=3, STALE_LOG_S=120, NON_VEC_BLOCK_N=2, KEY_BLOCK_TTL_S=7200, POS_MISMATCH_PCT=5.0

alerts this sweep: VEC_EXIT_WHILE_LIVE_FLAT 14, VEC_DECISION_NOT_FILLED 13, SIZE_ANOMALY 6, RESTART_LOOP 5, FILL_WITHOUT_VEC_DECISION 4, VEC_DECISION_REFUSED 1

### Active interventions (9) — exits are never blocked; positions are never closed by the guardian

| target | since | why | undo |
|---|---|---|---|
| key:men:ZROUSDT_LONG | 19:18:27Z | SIZE_ANOMALY BREAKOUT_SIZE_LADDER qty 130.845->392.534 ($868) past 1.5x MAX_ORDER_VALUE | `redis-cli -p 6379 DEL open_blocked:men:ZROUSDT_LONG` |
| acct:trc | 19:21:57Z | ACCOUNT_EQUITY_DROP unrealised PnL of the same open positions fell $26.54 since 2026-10-06T19:19:08Z (now $-291.98) | `rm /Users/niels/Documents/binance/data/HALT_TRADING_trc` |
| key:men:RENDERUSDT_LONG | 19:21:57Z | SIZE_ANOMALY BREAKOUT_SIZE_LADDER qty 76.4951->229.485 ($495) past 1.5x MAX_ORDER_VALUE | `redis-cli -p 6379 DEL open_blocked:men:RENDERUSDT_LONG` |
| acct:trb | 19:24:23Z | ACCOUNT_EQUITY_DROP unrealised PnL of the same open positions fell $30.53 since 2026-10-06T19:19:51Z (now $+405.99) | `rm /Users/niels/Documents/binance/data/HALT_TRADING_trb` |
| key:flz:ZECUSDC_LONG | 20:03:38Z | SIZE_ANOMALY entry notional $78.15 (median $24.82, MAX_ORDER_VALUE $300) @ 2026-10-06T20:03:23Z HARDCODED_RALLY_REENTRY  | `redis-cli -p 6379 DEL open_blocked:flz:ZECUSDC_LONG` |
| key:men:EDUUSDT_LONG | 20:04:19Z | SIZE_ANOMALY BREAKOUT_SIZE_LADDER qty 4299.15->12897.4 ($840) past 1.5x MAX_ORDER_VALUE | `redis-cli -p 6379 DEL open_blocked:men:EDUUSDT_LONG` |
| key:men:NMRUSDT_LONG | 20:04:19Z | SIZE_ANOMALY BREAKOUT_SIZE_LADDER qty 13.0015->39.0045 ($630) past 1.5x MAX_ORDER_VALUE | `redis-cli -p 6379 DEL open_blocked:men:NMRUSDT_LONG` |
| key:men:KSMUSDT_SHORT | 20:05:00Z | SIZE_ANOMALY entry notional $179.47 (median $6.82, MAX_ORDER_VALUE $300) @ 2026-10-06T20:04:46Z B15 |VEC_EXACT | `redis-cli -p 6379 DEL open_blocked:men:KSMUSDT_SHORT` |
| key:ang:NMRUSDT_LONG | 20:06:22Z | SIZE_ANOMALY entry notional $349.68 (median $0.00, MAX_ORDER_VALUE $300) @ 2026-10-06T20:06:05Z HARDCODED_RALLY_REENTRY  | `redis-cli -p 6379 DEL open_blocked:ang:NMRUSDT_LONG` |

### Latest CRITICAL/WARN alerts

| ts | severity | kind | key | evidence |
|---|---|---|---|---|
| 20:11:31 | CRITICAL | VEC_DECISION_NOT_FILLED | men:GRTUSDT_LONG | OPEN ENTRY_SIGNAL decided 2026-10-06T18:59:47Z bar=2026-10-06T18:30:00Z: no fill in 600s and no refusal logged |
| 20:11:31 | CRITICAL | VEC_DECISION_NOT_FILLED | men:ZROUSDT_LONG | OPEN HARDCODED_RALLY_REENTRY decided 2026-10-06T19:06:18Z bar=2026-10-06T18:45:00Z: no fill in 600s and no refusal logged |
| 20:11:31 | CRITICAL | VEC_DECISION_NOT_FILLED | men:EDUUSDT_LONG | OPEN B15 decided 2026-10-06T19:11:42Z bar=2026-10-06T18:45:00Z: no fill in 600s and no refusal logged |
| 20:11:31 | CRITICAL | VEC_DECISION_NOT_FILLED | men:RENDERUSDT_LONG | OPEN B14 decided 2026-10-06T19:18:52Z bar=2026-10-06T19:00:00Z: no fill in 600s and no refusal logged |
| 20:11:31 | CRITICAL | FILL_WITHOUT_VEC_DECISION | men:NMRUSDT_LONG | AUGMENT 11.1 @ 2026-10-06T20:04:49Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:11:31 | CRITICAL | FILL_WITHOUT_VEC_DECISION | men:KSMUSDT_SHORT | AUGMENT 36.4 @ 2026-10-06T20:04:46Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:11:31 | CRITICAL | SIZE_ANOMALY | ang:NMRUSDT_LONG | entry notional $349.68 (median $0.00, MAX_ORDER_VALUE $300) @ 2026-10-06T20:06:05Z HARDCODED_RALLY_REENTRY /VEC_EXACT |
| 20:11:31 | CRITICAL | SIZE_ANOMALY | ang:NMRUSDT_LONG | BREAKOUT_SIZE_LADDER resized qty 8.43592->25.3078 = $405.00 > MAX_ORDER_VALUE $300 (executed qty would exceed the validated qty 8.43592) |
| 20:11:32 | CRITICAL | VEC_DECISION_NOT_FILLED | ang:EDUUSDT_LONG | OPEN B15 decided 2026-10-06T19:08:38Z bar=2026-10-06T18:45:00Z: no fill in 600s and no refusal logged |
| 20:11:32 | CRITICAL | VEC_DECISION_NOT_FILLED | ang:ZROUSDT_LONG | OPEN B15 decided 2026-10-06T19:14:16Z bar=2026-10-06T18:45:00Z: no fill in 600s and no refusal logged |
| 20:11:32 | CRITICAL | VEC_DECISION_NOT_FILLED | ang:RENDERUSDT_LONG | OPEN B14 decided 2026-10-06T19:19:11Z bar=2026-10-06T19:00:00Z: no fill in 600s and no refusal logged |
| 20:11:32 | CRITICAL | VEC_DECISION_REFUSED | ang:AAOIUSDT_LONG | CLOSE EXIT_VELOCITY_WT against-long g-0.42% bar=2026-10-06T19:30:00Z refused by non-safety gate: 06 20:00:56 - ERROR - [ang] 🛡️ [execute_trade_action] |
| 20:11:32 | CRITICAL | FILL_WITHOUT_VEC_DECISION | ang:ZROUSDT_LONG | AUGMENT 79 @ 2026-10-06T20:05:31Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:11:32 | CRITICAL | VEC_DECISION_NOT_FILLED | inf:ZROUSDT_LONG | OPEN B15 decided 2026-10-06T19:13:57Z bar=2026-10-06T18:45:00Z: no fill in 600s and no refusal logged |
| 20:11:32 | CRITICAL | VEC_DECISION_NOT_FILLED | inf:RENDERUSDT_LONG | OPEN B14 decided 2026-10-06T19:19:45Z bar=2026-10-06T19:00:00Z: no fill in 600s and no refusal logged |
| 20:11:32 | CRITICAL | VEC_DECISION_NOT_FILLED | inf:IOTXUSDT_LONG | OPEN B_KZONE decided 2026-10-06T19:34:49Z bar=2026-10-06T19:15:00Z: no fill in 600s and no refusal logged |
| 20:11:32 | CRITICAL | SIZE_ANOMALY | flz:ZECUSDC_LONG | entry notional $78.15 (median $24.82, MAX_ORDER_VALUE $300) @ 2026-10-06T20:03:23Z HARDCODED_RALLY_REENTRY /VEC_EXACT |
| 20:11:32 | CRITICAL | VEC_DECISION_NOT_FILLED | flz:ZECUSDC_LONG | CLOSE EXIT_VELOCITY_WT against-long g-0.12% decided 2026-10-06T18:55:38Z bar=2026-10-06T18:30:00Z: no fill in 600s and no refusal logged |
| 20:11:32 | CRITICAL | VEC_DECISION_NOT_FILLED | flz:BTCDOMUSDT_LONG | OPEN B_VWAPBOUNCE decided 2026-10-06T19:34:36Z bar=2026-10-06T19:15:00Z: no fill in 600s and no refusal logged |
| 20:11:32 | CRITICAL | FILL_WITHOUT_VEC_DECISION | flz:ZECUSDC_LONG | AUGMENT 0.058 @ 2026-10-06T20:03:23Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:11:32 | CRITICAL | RESTART_LOOP | fin | 4 new process ids in 30 min (['63196', '80533', '7388', '22500']) |
| 20:11:32 | CRITICAL | RESTART_LOOP | men | 3 new process ids in 30 min (['72151', '80421', '6108']) |
| 20:11:32 | CRITICAL | RESTART_LOOP | ang | 4 new process ids in 30 min (['66247', '80422', '7614', '25998']) |
| 20:11:32 | CRITICAL | RESTART_LOOP | inf | 4 new process ids in 30 min (['69301', '80401', '7595', '23636']) |
| 20:11:32 | CRITICAL | RESTART_LOOP | flz | 4 new process ids in 30 min (['59886', '80799', '9438', '20132']) |

live vec decisions since switch (per account): fin: {'live_vec_decisions': 4, 'state_divergent': 3, 'filled': 0, 'refused_non_safety': 0, 'safety_refusals': 0, 'not_filled_no_log': 1, 'vec_exact_fills_without_decision': 0}; men: {'live_vec_decisions': 7, 'state_divergent': 3, 'filled': 0, 'refused_non_safety': 0, 'safety_refusals': 0, 'not_filled_no_log': 4, 'vec_exact_fills_without_decision': 2}; ang: {'live_vec_decisions': 13, 'state_divergent': 6, 'filled': 2, 'refused_non_safety': 1, 'safety_refusals': 0, 'not_filled_no_log': 3, 'vec_exact_fills_without_decision': 1}; inf: {'live_vec_decisions': 8, 'state_divergent': 2, 'filled': 0, 'refused_non_safety': 0, 'safety_refusals': 0, 'not_filled_no_log': 3, 'vec_exact_fills_without_decision': 0}; flz: {'live_vec_decisions': 2, 'state_divergent': 0, 'filled': 0, 'refused_non_safety': 0, 'safety_refusals': 0, 'not_filled_no_log': 2, 'vec_exact_fills_without_decision': 1}

processes: fin pids=['22500'] starts30m=4 log_age=28.0s, men pids=['6108'] starts30m=3 log_age=12.0s, ang pids=['25998'] starts30m=4 log_age=2.0s, inf pids=['23636'] starts30m=4 log_age=11.0s, flz pids=['20132'] starts30m=4 log_age=9.0s, trb pids=[] starts30m=1 log_age=238.0s, trc pids=[] starts30m=1 log_age=238.0s

## Test 1 - 15m LIVE vs VECTOR, CRYPTO

run 2026-10-06T19:26:39+00:00 (45 min ago) · engine v12 70968f5e · NPZ sync tail · window 24.0h from 2026-10-06T18:48Z · tol +-1 bars · 190 live keys / 131 sym_sides · 1178.1 s

**Regime PARITY_VEC_EXACT since 2026-10-06T18:48:00Z** (previous regime archived: `data/forward_parity/archive/pre_vec_exact_until_20261006T1848Z`). Rule: every live fill = a vec decision at the same 15m bar and side (fill may land up to 1 bar later); every vec decision = a live fill or a logged hard-safety refusal; quantity not compared.

**Verdict: FAIL** — FAIL 1 · PASS with decisions 0 · IDLE (no decision either side, counts as pass) 172 · NO_DATA 17 · judged on a STALE-shifted window (S1 NPZ behind live) 0

- vector fired, live did not (VEC_ONLY): LIVE_ALREADY_FLAT 1
- live fired, vector did not (LIVE_ONLY): -
- NPZ missing on S1 (no vector possible): 100PEPEUSDC, EDUUSDT, KMNOUSDT, MELANIAUSDT, NMRUSDT
- vector replay made 0 trades in 30D for 60/131 sym_sides with the live set (engine md5 70968f5e): every live fill on those keys is LIVE_ONLY by construction — engine/set problem, not live drift. e.g. 1INCHUSDT_SHORT, ADAUSDC_LONG, ADAUSDC_SHORT, AGLDUSDT_SHORT, ALGOUSDT_LONG, ALGOUSDT_SHORT, APEUSDT_SHORT, API3USDT_SHORT
- vector errors: prepare failed: no npz 13
- FAIL classes (sym_sides): STATE_CASCADE 1
- since switch: live fills 0 (tagged |VEC_EXACT 0) · vec decisions 2 · matched 0 · out-of-universe live fills (not judged, no CPU): 0

### Per sym_side with any decision since the switch

| key | status | vec decisions | live fills (VEC_EXACT) | matched | vec not filled (why) | live not vec (what) |
|---|---|---|---|---|---|---|
| flz:GRAMUSDT_LONG | FAIL | 2 | 0 (0) | 0 | LIVE_ALREADY_FLAT 1 | - |

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|
| flz:GRAMUSDT_LONG | STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | None/None | last window | vec_driven:progress:a7c93e12 |

PASS with activity: -  ·  PASS_IDLE (no decision either side): 172

NO_DATA reasons: prepare failed: no npz 17

### Per function family (crypto)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| GOLDEN_RULE | 15m | Y | GOLDEN_RULE_ENABLED | 17 | 0 | 0 | 0 | 0 | 0 | IDLE |
| QUICK_OPEN_STRONG | 3m | Y | QUICK_OPEN_STRONG_VEC_ENABLED | 14 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| EXIT_VELOCITY_WT | 1h | Y | EXIT_VELOCITY_WT_ENABLED | 5 | 0 | 1 | 0 | 1 | 0 | FAIL |
| UNCLASSIFIED:B | 15m | N | ? (switch not identified) | 5 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| CRYPTO_SPIKE_FADE | 3m | N | CRYPTO_SPIKE_FADE_ENABLED | 4 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_OPEN_GOOD | 3m | N | ? (switch not identified) | 2 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_OTHER | 3m | N | ABLATION_DISABLE_QUICK_EXIT | 1 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| HARDCODED_RALLY_REENTRY | 15m | Y | HARDCODED_RALLY_REENTRY_ENABLED | 1 | 0 | 0 | 0 | 0 | 0 | IDLE |
| UNCLASSIFIED:B_KZONE | 15m | Y | ? (switch not identified) | 0 | 0 | 1 | 0 | 0 | 0 | PASS |

## Test 2 - LIVE_ONLY functions (1m/3m/5m, tick, webhook, portfolio, no vec twin), CRYPTO

These decisions have no 15m vector counterpart by construction; listed so drift is visible (attempts = execute_now calls in the window).

| function family | input TF | attempts | fills | gate switch |
|---|---|---|---|---|
| QUICK_OPEN_STRONG | 3m | 14 | 0 | QUICK_OPEN_STRONG_VEC_ENABLED |
| UNCLASSIFIED:B | 15m | 5 | 0 | ? (switch not identified) |
| CRYPTO_SPIKE_FADE | 3m | 4 | 0 | CRYPTO_SPIKE_FADE_ENABLED |
| QUICK_OPEN_GOOD | 3m | 2 | 0 | ? (switch not identified) |
| QUICK_REDUCE_OTHER | 3m | 1 | 0 | ABLATION_DISABLE_QUICK_EXIT |

## Test 1 - 15m LIVE vs VECTOR, STOCKS

run 2026-10-06T19:54:33+00:00 (17 min ago) · engine v12 e2f51016 · NPZ sync tail · window 24.0h from 2026-10-06T18:48Z · tol +-1 bars · 42 live keys / 31 sym_sides · 150.1 s

**Regime PARITY_VEC_EXACT since 2026-10-06T18:48:00Z** (previous regime archived: `data/forward_parity/archive/pre_vec_exact_until_20261006T1848Z`). Rule: every live fill = a vec decision at the same 15m bar and side (fill may land up to 1 bar later); every vec decision = a live fill or a logged hard-safety refusal; quantity not compared.

**Verdict: PASS** — FAIL 0 · PASS with decisions 1 · IDLE (no decision either side, counts as pass) 41 · NO_DATA 0 · judged on a STALE-shifted window (S1 NPZ behind live) 0

- vector fired, live did not (VEC_ONLY): -
- live fired, vector did not (LIVE_ONLY): -
- vector replay made 0 trades in 30D for 10/31 sym_sides with the live set (engine md5 e2f51016): every live fill on those keys is LIVE_ONLY by construction — engine/set problem, not live drift. e.g. AXTI_LONG, A_LONG, CLS_LONG, CRWD_LONG, EQT_SHORT, EXEL_LONG, GOOGL_LONG, MU_LONG
- since switch: live fills 236 (tagged |VEC_EXACT 0) · vec decisions 0 · matched 0 · out-of-universe live fills (not judged, no CPU): 0

### Per sym_side with any decision since the switch

| key | status | vec decisions | live fills (VEC_EXACT) | matched | vec not filled (why) | live not vec (what) |
|---|---|---|---|---|---|---|
| trc:IBIT_LONG | PASS | 0 | 236 (0) | 0 | - | - |

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|

PASS with activity: trc:IBIT_LONG  ·  PASS_IDLE (no decision either side): 41

### Per function family (stocks)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| BROKER_SYNC | tick | N | safety (exempt) | 0 | 236 | 0 | 0 | 0 | 0 | PASS |

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

`DH_20261005_20261006.json` generated 2026-10-06T19:17:02.209748+00:00 (55 min ago) · intents 495 · matched 35 · **missing 460**

matched/intents per account: fin 4/9, men 8/37, ang 14/27, inf 0/0 (no decision file), flz 9/10, trb 0/229, trc 0/183

| function family | action | intent result | n | blocking execution filter | vector same decision +-2 bars (Y/N/? = not judged) |
|---|---|---|---|---|---|
| UNCLASSIFIED:? | OPEN | MISSING | 3 | UNATTRIBUTED 3 | ? 3 |
| UNCLASSIFIED:? | CLOSE | MISSING | 3 | UNATTRIBUTED 3 | N 2, ? 1 |

Gap classes: NUKE_STALE_MAKER_ORDER = maker exit rested >60 s and was cancelled (microstructure, no vec twin); UNATTRIBUTED = no blocking log line found (logging hook missing in the execution path); MAKER_ZERO_QTY / MARKET_API_ERROR = sizing/broker. Operator decisions: `data/decisions_history_parity/NEEDS-OPERATOR-DECISION_*.md`.

## Exit-engine gate parity (execute_now, all exits)

generated 2026-10-06T20:00:22.441438+00:00 (11 min ago) · lookback 48.0h · exit rows 12 · **LEAKS 0** · gate ON 4 · UNGATED 0 · safety 0 · unmapped 0

## 7-day rollup (tools/daily_parity_test.py)

`data/parity/daily_parity_20261006.md`

- Generated: 2026-10-06T06:15:12+00:00 | engine: tools/forward_parity/live_vs_vec.py (same bars, same per-sym set)
- crypto: 139 live keys · status {'PASS_IDLE': 98, 'NO_DATA': 13, 'STALE_PASS_IDLE': 11, 'FAIL': 17} · VEC_ONLY {'LIVE_ALREADY_IN_POSITION': 3, 'LIVE_NO_SIGNAL': 3} · LIVE_ONLY {'LIVE_ONLY_VEC_FAMILY:GOLDEN_RULE': 15, 'LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT': 7, 'LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m': 6, 'LIVE_ONLY_NONVEC:RANKING_DIRECT_ALL_GREEN@webhook': 5, 'LIVE_ONLY_VEC_FAMILY:DC_DAYTRADE_TARGET': 4, 'LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m': 3, 'LIVE_ONLY_VEC_FAMILY:WT_LOWER_CROSS_EXIT': 1, 'LIVE_ONLY_NONVEC:RATIO_REBALANCE@portfolio': 1, 'LIVE_ONLY_NONVEC:BREAK_EVEN_GUARD@tick': 1, 'LIVE_ONLY_VEC_FAMILY:MTF_BB_REJECT_EXIT': 1, 'LIVE_ONLY_NONVEC:ALL_TF_AGAINST_CLOSE@3m': 1, 'LIVE_ONLY_NONVEC:GUARANTEED_REENTRY@3m': 1, 'LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY': 1, 'LIVE_ONLY_NONVEC:GAIN_EROSION_STOP@3m': 1}
- stocks: 175 live keys · status {'FAIL': 151, 'PASS_IDLE': 19, 'NO_DATA': 4, 'STALE_PASS_IDLE': 1} · VEC_ONLY {'LIVE_NO_SIGNAL': 208, 'LIVE_ALREADY_FLAT': 162, 'LIVE_ALREADY_IN_POSITION': 28, 'LIVE_INTENT_NOT_FILLED:UNCLASSIFIED:?': 2} · LIVE_ONLY {'LIVE_ONLY_NONVEC:REENTRY_OTHER@5m': 226, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:SYNC_DETECTION@15m': 71, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:MFI_MEAN_REVERSION@15m': 57, 'LIVE_ONLY_VEC_FAMILY:REENTRY_OTHER': 47, 'LIVE_ONLY_NONVEC:FALLBACK_CLOSE@15m': 27, 'LIVE_ONLY_NONVEC:GAP_MOC@5m': 24, 'LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY': 23, 'LIVE_ONLY_VEC_FAMILY:DC_DAYTRADE_TARGET': 21, 'LIVE_ONLY_VEC_FAMILY:HARDCODED_RALLY_REENTRY': 18, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TARGET@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DC_BREAK_HIGH@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:ULTIMATE_DC_H@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@15m': 11, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:INTRADAY_RATIO_TRIM@15m': 5, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:STRUCT_BREAK_DC@15m': 5, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:SENT_STRAT_DIV@15m': 3, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:OVERBOUGHT_TAKE_PROFIT@15m': 2, 'LIVE_ONLY_NONVEC:GAP_FILL@5m': 2, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:GR_HTF_DIRECT@3m': 2, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:NOLOSS_BBH_BREAKDOWN@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@3m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@5m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:ROTATION_ENTRY_L@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:CLENOW_ENTRY@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:STRUCT_BREAK_DC@5m': 1}
- Totals: PASS 129 · FAIL 168 · sign flips (>=5 exits each side) 0 · alerts 86
