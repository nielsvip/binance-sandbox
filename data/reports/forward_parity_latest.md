# Forward parity — LIVE vs VECTOR (auto, read-only)

generated 2026-10-06T20:52:59+00:00 · producers: tools/forward_parity/live_vs_vec.py (crypto hourly :07, stocks every 15 min RTH), tools/decisions_history_parity.py (:17), tools/exit_engine_parity_monitor.py (launchd hourly), tools/daily_parity_test.py (launchd daily)

**REGIME PARITY_VEC_EXACT since 2026-10-06T18:48:00Z** — all monitors reset at the switch; old-regime results archived in `data/forward_parity/archive/pre_vec_exact_until_20261006T1848Z`. Universe: tradeable_keys.json + open positions only.

PASS (exact regime) = every vec decision on the judged bars is a live fill at the same bar/side or a logged hard-safety refusal, and every live fill is a vec decision. FAIL classes: VEC_DECISION_NOT_FILLED_NO_LOG, VEC_DECISION_REFUSED_NON_SAFETY, NATIVE_FILL_IN_EXACT_MODE (a non-vec live path traded), VEC_EXACT_FILL_NOT_IN_REPLAY (live vec on live_klines vs replay on the S1 NPZ disagree), STATE_CASCADE, NPZ_STALE.

PASS (legacy definition) = every vector decision on the judged 15m bars has a live fill of the same class within +-2 bars, every live fill has a vector decision, and end state (in/out) agrees. FAIL classes: VEC_ONLY_LIVE_NO_SIGNAL (live logic did not fire), VEC_ONLY_LIVE_BLOCKED (live attempted, gate/execution stopped it), VEC_ONLY_STATE_CASCADE (consequence of an earlier divergence), LIVE_ONLY_VEC_FAMILY (vector twin exists but did not fire), LIVE_ONLY_NONVEC (1m/3m/5m/tick/webhook/portfolio input — no vec counterpart), STATE_MISMATCH, NPZ_STALE (S1 NPZ behind live, judged on the newest window the vector can see).

## LIVE GUARDIAN (every 20 s, both systems) — tools/forward_parity/live_guardian.py

last sweep 2026-10-06T20:52:59Z (0 min ago) · 0.34 s · dry_run=False · RTH=False · regime start 2026-10-06T18:48:00Z

thresholds: STORM_KEY_FILLS=6, STORM_KEY_MIN=10, ATTEMPT_STORM=30, ATTEMPT_MIN=5, CHURN_ROUNDTRIPS=3, DUP_S=20, ACCOUNT_STORM_FILLS=25, ACCOUNT_STORM_MIN=10, ACCOUNT_DUP_PAIRS=3, FILL_WAIT_S=600, SIZE_MULT=3.0, LOSS_DROP_PP=4.0, LOSS_FLOOR_PCT=-3.0, EQUITY_DROP_USD=25.0, RESTARTS=3, STALE_LOG_S=120, NON_VEC_BLOCK_N=2, KEY_BLOCK_TTL_S=7200, POS_MISMATCH_PCT=5.0

alerts this sweep: VEC_DECISION_NOT_FILLED 33, VEC_EXIT_WHILE_LIVE_FLAT 29, FILL_WITHOUT_VEC_DECISION 18, SIZE_ANOMALY 5, VEC_OPEN_WHILE_LIVE_IN 1, VEC_DECISION_REFUSED 1

### Active interventions (12) — exits are never blocked; positions are never closed by the guardian

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
| key:inf:ARUSDT_LONG | 20:13:15Z | SIZE_ANOMALY entry notional $101.26 (median $5.86, MAX_ORDER_VALUE $300) @ 2026-10-06T20:12:57Z B_KZONE |VEC_EXACT | `redis-cli -p 6379 DEL open_blocked:inf:ARUSDT_LONG` |
| key:men:ALGOUSDT_SHORT | 20:16:42Z | SIZE_ANOMALY BREAKOUT_SIZE_LADDER qty 1333.63->4000.9 ($495) past 1.5x MAX_ORDER_VALUE | `redis-cli -p 6379 DEL open_blocked:men:ALGOUSDT_SHORT` |
| key:men:AXSUSDT_SHORT | 20:21:33Z | SIZE_ANOMALY BREAKOUT_SIZE_LADDER qty 125.853->377.56 ($472) past 1.5x MAX_ORDER_VALUE | `redis-cli -p 6379 DEL open_blocked:men:AXSUSDT_SHORT` |

### Latest CRITICAL/WARN alerts

| ts | severity | kind | key | evidence |
|---|---|---|---|---|
| 20:52:59 | CRITICAL | VEC_DECISION_NOT_FILLED | ang:AAPLUSDT_LONG | OPEN B12 decided 2026-10-06T20:37:37Z bar=2026-10-06T20:15:00Z: no fill in 600s and no refusal logged |
| 20:52:59 | CRITICAL | VEC_DECISION_NOT_FILLED | ang:XTZUSDT_LONG | OPEN B_SRS_ENTRY decided 2026-10-06T20:37:37Z bar=2026-10-06T20:15:00Z: no fill in 600s and no refusal logged |
| 20:52:59 | CRITICAL | VEC_DECISION_NOT_FILLED | ang:SNXUSDT_LONG | OPEN B10 decided 2026-10-06T20:37:38Z bar=2026-10-06T20:15:00Z: no fill in 600s and no refusal logged |
| 20:52:59 | CRITICAL | FILL_WITHOUT_VEC_DECISION | ang:NEARUSDC_LONG | AUGMENT 7 @ 2026-10-06T20:18:41Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:52:59 | CRITICAL | FILL_WITHOUT_VEC_DECISION | ang:EDUUSDT_LONG | AUGMENT 1193 @ 2026-10-06T20:41:54Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:52:59 | CRITICAL | FILL_WITHOUT_VEC_DECISION | ang:ZROUSDT_LONG | AUGMENT 79 @ 2026-10-06T20:05:31Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:52:59 | CRITICAL | FILL_WITHOUT_VEC_DECISION | ang:TRBUSDT_LONG | AUGMENT 1.8 @ 2026-10-06T20:17:46Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:52:59 | CRITICAL | FILL_WITHOUT_VEC_DECISION | ang:ENJUSDT_LONG | AUGMENT 240 @ 2026-10-06T20:17:54Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:52:59 | CRITICAL | FILL_WITHOUT_VEC_DECISION | ang:SANDUSDT_LONG | AUGMENT 305 @ 2026-10-06T20:19:12Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:52:59 | CRITICAL | SIZE_ANOMALY | inf:ARUSDT_LONG | entry notional $75.44 (median $5.86, MAX_ORDER_VALUE $300) @ 2026-10-06T20:47:21Z B_VWAPBOUNCE /VEC_EXACT |
| 20:52:59 | CRITICAL | VEC_DECISION_NOT_FILLED | inf:ZROUSDT_LONG | OPEN B15 decided 2026-10-06T19:13:57Z bar=2026-10-06T18:45:00Z: no fill in 600s and no refusal logged |
| 20:52:59 | CRITICAL | VEC_DECISION_NOT_FILLED | inf:RENDERUSDT_LONG | OPEN B14 decided 2026-10-06T19:19:45Z bar=2026-10-06T19:00:00Z: no fill in 600s and no refusal logged |
| 20:52:59 | CRITICAL | VEC_DECISION_NOT_FILLED | inf:IOTXUSDT_LONG | OPEN B_KZONE decided 2026-10-06T19:34:49Z bar=2026-10-06T19:15:00Z: no fill in 600s and no refusal logged |
| 20:52:59 | CRITICAL | VEC_DECISION_NOT_FILLED | inf:AGLDUSDT_SHORT | OPEN B_SRS_ENTRY decided 2026-10-06T20:37:45Z bar=2026-10-06T20:15:00Z: no fill in 600s and no refusal logged |
| 20:52:59 | CRITICAL | VEC_DECISION_NOT_FILLED | inf:XTZUSDT_LONG | OPEN B_SRS_ENTRY decided 2026-10-06T20:37:45Z bar=2026-10-06T20:15:00Z: no fill in 600s and no refusal logged |
| 20:52:59 | CRITICAL | FILL_WITHOUT_VEC_DECISION | inf:NEARUSDC_LONG | AUGMENT 7 @ 2026-10-06T20:17:50Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:52:59 | CRITICAL | FILL_WITHOUT_VEC_DECISION | inf:ARUSDT_LONG | REDUCE 22.1 @ 2026-10-06T20:39:16Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:52:59 | CRITICAL | FILL_WITHOUT_VEC_DECISION | inf:ARUSDT_LONG | AUGMENT 16.4 @ 2026-10-06T20:47:21Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:52:59 | CRITICAL | FILL_WITHOUT_VEC_DECISION | inf:ZROUSDT_LONG | AUGMENT 26.6 @ 2026-10-06T20:15:53Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:52:59 | CRITICAL | FILL_WITHOUT_VEC_DECISION | inf:NMRUSDT_LONG | AUGMENT 4.4 @ 2026-10-06T20:16:30Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:52:59 | CRITICAL | VEC_DECISION_NOT_FILLED | flz:ZECUSDC_LONG | CLOSE EXIT_VELOCITY_WT against-long g-0.12% decided 2026-10-06T18:55:38Z bar=2026-10-06T18:30:00Z: no fill in 600s and no refusal logged |
| 20:52:59 | CRITICAL | VEC_DECISION_NOT_FILLED | flz:BTCDOMUSDT_LONG | OPEN B_VWAPBOUNCE decided 2026-10-06T19:34:36Z bar=2026-10-06T19:15:00Z: no fill in 600s and no refusal logged |
| 20:52:59 | CRITICAL | FILL_WITHOUT_VEC_DECISION | flz:HYPEUSDT_LONG | AUGMENT 0.78 @ 2026-10-06T20:48:22Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:52:59 | CRITICAL | FILL_WITHOUT_VEC_DECISION | flz:ZECUSDC_LONG | AUGMENT 0.058 @ 2026-10-06T20:03:23Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 20:52:59 | CRITICAL | FILL_WITHOUT_VEC_DECISION | flz:ZECUSDC_LONG | REDUCE 0.058 @ 2026-10-06T20:24:10Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |

live vec decisions since switch (per account): fin: {'live_vec_decisions': 4, 'state_divergent': 3, 'filled': 0, 'refused_non_safety': 0, 'safety_refusals': 0, 'not_filled_no_log': 1, 'vec_exact_fills_without_decision': 1}; men: {'live_vec_decisions': 26, 'state_divergent': 11, 'filled': 1, 'refused_non_safety': 0, 'safety_refusals': 0, 'not_filled_no_log': 14, 'vec_exact_fills_without_decision': 3}; ang: {'live_vec_decisions': 23, 'state_divergent': 9, 'filled': 2, 'refused_non_safety': 1, 'safety_refusals': 0, 'not_filled_no_log': 11, 'vec_exact_fills_without_decision': 6}; inf: {'live_vec_decisions': 12, 'state_divergent': 6, 'filled': 1, 'refused_non_safety': 0, 'safety_refusals': 0, 'not_filled_no_log': 5, 'vec_exact_fills_without_decision': 5}; flz: {'live_vec_decisions': 3, 'state_divergent': 1, 'filled': 0, 'refused_non_safety': 0, 'safety_refusals': 0, 'not_filled_no_log': 2, 'vec_exact_fills_without_decision': 3}

processes: fin pids=['8471'] starts30m=2 log_age=2.0s, men pids=['18119'] starts30m=2 log_age=1.0s, ang pids=['8166'] starts30m=2 log_age=2.0s, inf pids=['8345'] starts30m=2 log_age=7.0s, flz pids=['8374'] starts30m=2 log_age=3.0s, trb pids=[] starts30m=0 log_age=359.0s, trc pids=[] starts30m=0 log_age=359.0s

## Test 1 - 15m LIVE vs VECTOR, CRYPTO

run 2026-10-06T20:23:12+00:00 (30 min ago) · engine v12 2e4a9d18 · NPZ sync tail · window 24.0h from 2026-10-06T18:48Z · tol +-1 bars · 196 live keys / 139 sym_sides · 970.9 s

**Regime PARITY_VEC_EXACT since 2026-10-06T18:48:00Z** (previous regime archived: `data/forward_parity/archive/pre_vec_exact_until_20261006T1848Z`). Rule: every live fill = a vec decision at the same 15m bar and side (fill may land up to 1 bar later); every vec decision = a live fill or a logged hard-safety refusal; quantity not compared.

**Verdict: FAIL** — FAIL 26 · PASS with decisions 11 · IDLE (no decision either side, counts as pass) 145 · NO_DATA 14 · judged on a STALE-shifted window (S1 NPZ behind live) 0

- vector fired, live did not (VEC_ONLY): MISSING_NO_LIVE_LOG 22, LIVE_ALREADY_FLAT 20, LIVE_ALREADY_IN_POSITION 3, REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 1
- live fired, vector did not (LIVE_ONLY): VEC_EXACT_FILL_NOT_IN_REPLAY 2
- NPZ missing on S1 (no vector possible): 100PEPEUSDC, EDUUSDT, KMNOUSDT, MELANIAUSDT, NMRUSDT
- vector replay made 0 trades in 30D for 22/139 sym_sides with the live set (engine md5 2e4a9d18): every live fill on those keys is LIVE_ONLY by construction — engine/set problem, not live drift. e.g. 1000LUNCUSDT_SHORT, ADAUSDC_LONG, AGLDUSDT_SHORT, ALGOUSDT_LONG, ARUSDT_SHORT, BNBUSDC_SHORT, BTCUSDC_SHORT, COMPUSDT_LONG
- vector errors: prepare failed: no npz 12
- FAIL classes (sym_sides): STATE_CASCADE 21, VEC_DECISION_NOT_FILLED_NO_LOG 20, VEC_EXACT_FILL_NOT_IN_REPLAY 2, VEC_DECISION_REFUSED_NON_SAFETY 1
- since switch: live fills 14 (tagged |VEC_EXACT 14) · vec decisions 58 · matched 2 · out-of-universe live fills (not judged, no CPU): 4

### Per sym_side with any decision since the switch

| key | status | vec decisions | live fills (VEC_EXACT) | matched | vec not filled (why) | live not vec (what) |
|---|---|---|---|---|---|---|
| ang:AAVEUSDC_LONG | PASS | 1 | 0 (0) | 0 | - | - |
| ang:API3USDT_LONG | FAIL | 1 | 0 (0) | 0 | LIVE_ALREADY_FLAT 1 | - |
| ang:ASTSUSDT_LONG | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| ang:EDUUSDT_LONG | NO_DATA | 0 | 1 (1) | 0 | - | - |
| ang:NEARUSDC_LONG | PASS | 1 | 1 (1) | 1 | - | - |
| ang:NMRUSDT_LONG | NO_DATA | 0 | 2 (2) | 0 | - | - |
| ang:RLCUSDT_LONG | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| ang:SANDUSDT_LONG | PASS | 0 | 1 (1) | 0 | - | - |
| ang:TRBUSDT_LONG | STALE_PASS | 0 | 1 (1) | 0 | - | - |
| ang:ZROUSDT_LONG | STALE_PASS | 0 | 1 (1) | 0 | - | - |
| fin:IOTXUSDT_LONG | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1 | - |
| fin:RLCUSDT_LONG | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| fin:SANDUSDT_LONG | PASS | 0 | 1 (1) | 0 | - | - |
| flz:DASHUSDT_SHORT | FAIL | 4 | 0 (0) | 0 | LIVE_ALREADY_IN_POSITION 2, MISSING_NO_LIVE_LOG 1 | - |
| flz:GRAMUSDT_LONG | FAIL | 4 | 0 (0) | 0 | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1 | - |
| flz:ZECUSDC_LONG | FAIL | 3 | 1 (1) | 0 | MISSING_NO_LIVE_LOG 2, LIVE_ALREADY_IN_POSITION 1 | VEC_EXACT_FILL_NOT_IN_REPLAY 1 |
| inf:1INCHUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| inf:AAVEUSDC_LONG | PASS | 1 | 0 (0) | 0 | - | - |
| inf:APEUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| inf:API3USDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| inf:ARKMUSDT_SHORT | FAIL | 1 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1 | - |
| inf:ARUSDT_LONG | PASS | 0 | 1 (1) | 0 | - | - |
| inf:IOTXUSDT_LONG | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1 | - |
| men:1INCHUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| men:DASHUSDT_SHORT | FAIL | 4 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 2, LIVE_ALREADY_FLAT 1 | - |
| men:EGLDUSDT_SHORT | FAIL | 1 | 0 (0) | 0 | LIVE_ALREADY_FLAT 1 | - |
| men:ENJUSDT_LONG | FAIL | 2 | 0 (0) | 0 | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 1, LIVE_ALREADY_FLAT 1 | - |
| men:ENJUSDT_SHORT | FAIL | 1 | 0 (0) | 0 | LIVE_ALREADY_FLAT 1 | - |
| men:IOTXUSDT_LONG | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1 | - |
| men:KSMUSDT_SHORT | FAIL | 0 | 1 (1) | 0 | - | VEC_EXACT_FILL_NOT_IN_REPLAY 1 |
| men:LINKUSDC_SHORT | FAIL | 3 | 0 (0) | 0 | LIVE_ALREADY_FLAT 1, MISSING_NO_LIVE_LOG 1 | - |
| men:LTCUSDC_LONG | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| men:LTCUSDC_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| men:NEARUSDC_LONG | PASS | 1 | 0 (0) | 0 | - | - |
| men:NEARUSDC_SHORT | FAIL | 1 | 0 (0) | 0 | LIVE_ALREADY_FLAT 1 | - |
| men:NMRUSDT_LONG | NO_DATA | 0 | 1 (1) | 0 | - | - |
| men:RENDERUSDT_LONG | STALE_PASS | 0 | 1 (1) | 0 | - | - |
| men:RLCUSDT_LONG | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| men:RLCUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| men:SNXUSDT_SHORT | PASS | 1 | 1 (1) | 1 | - | - |

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|
| flz:ZECUSDC_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG, VEC_EXACT_FILL_NOT_IN_REPLAY | MISSING_NO_LIVE_LOG 2, LIVE_ALREADY_IN_POSITION 1 | VEC_EXACT_FILL_NOT_IN_REPLAY 1 | None/None | last window | per_sym_store |
| flz:DASHUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_IN_POSITION 2, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | per_sym_active_config:clean_ |
| flz:GRAMUSDT_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | vec_driven:progress:a7c93e12 |
| men:DASHUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 2, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_active_config:clean_ |
| ang:ASTSUSDT_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| ang:RLCUSDT_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | vec_driven:progress:c5f4588a |
| fin:RLCUSDT_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | vec_driven:progress:c5f4588a |
| inf:1INCHUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | vec_driven:candidates:63e881 |
| inf:APEUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_active_config:clean_ |
| inf:API3USDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_active_config:clean_ |
| men:1INCHUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | vec_driven:candidates:63e881 |
| men:ENJUSDT_LONG | STATE_CASCADE, VEC_DECISION_REFUSED_NON_SAFETY | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_active_config:clean_ |
| men:LINKUSDC_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_FLAT 1, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | defaults(cat_side) |
| men:LTCUSDC_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| men:LTCUSDC_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | defaults(cat_side) |
| men:RLCUSDT_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | vec_driven:progress:c5f4588a |
| men:RLCUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_active_config:clean_ |
| ang:API3USDT_LONG | STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| fin:IOTXUSDT_LONG | VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1 | - | None/None | last window | vec_driven:progress:a761dc6d |
| inf:ARKMUSDT_SHORT | VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1 | - | None/None | last window | per_sym_active_config:clean_ |
| inf:IOTXUSDT_LONG | VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1 | - | None/None | last window | vec_driven:progress:a761dc6d |
| men:EGLDUSDT_SHORT | STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_active_config:clean_ |
| men:ENJUSDT_SHORT | STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_active_config:clean_ |
| men:IOTXUSDT_LONG | VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1 | - | None/None | last window | vec_driven:progress:a761dc6d |
| men:KSMUSDT_SHORT | VEC_EXACT_FILL_NOT_IN_REPLAY | - | VEC_EXACT_FILL_NOT_IN_REPLAY 1 | None/None | last window | per_sym_active_config:clean_ |
| men:NEARUSDC_SHORT | STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | None/None | last window | defaults(cat_side) |

PASS with activity: ang:AAVEUSDC_LONG, ang:NEARUSDC_LONG, ang:SANDUSDT_LONG, fin:SANDUSDT_LONG, inf:AAVEUSDC_LONG, inf:ARUSDT_LONG, men:NEARUSDC_LONG, men:SNXUSDT_SHORT  ·  PASS_IDLE (no decision either side): 145

NO_DATA reasons: prepare failed: no npz 14

### Per function family (crypto)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| EXIT_VELOCITY_WT | 1h | Y | EXIT_VELOCITY_WT_ENABLED | 36 | 0 | 24 | 0 | 21 | 0 | FAIL |
| UNCLASSIFIED:B | 15m | Y | ? (switch not identified) | 14 | 4 | 11 | 2 | 8 | 1 | FAIL |
| UNCLASSIFIED:B_KZONE | 15m | Y | ? (switch not identified) | 14 | 2 | 13 | 0 | 13 | 0 | FAIL |
| GOLDEN_RULE | 15m | Y | GOLDEN_RULE_ENABLED | 18 | 0 | 0 | 0 | 0 | 0 | IDLE |
| QUICK_OPEN_STRONG | 3m | Y | QUICK_OPEN_STRONG_VEC_ENABLED | 11 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| HARDCODED_RALLY_REENTRY | 15m | Y | HARDCODED_RALLY_REENTRY_ENABLED | 6 | 2 | 0 | 0 | 0 | 1 | FAIL |
| UNCLASSIFIED:ENTRY_SIGNAL | 15m | Y | ? (switch not identified) | 2 | 2 | 2 | 0 | 0 | 0 | PASS |
| UNCLASSIFIED:B_SRS_ENTRY | 15m | Y | ? (switch not identified) | 4 | 0 | 1 | 0 | 0 | 0 | PASS |
| CRYPTO_SPIKE_FADE | 3m | N | CRYPTO_SPIKE_FADE_ENABLED | 4 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:WT_CROSS_EXIT | 15m | Y | ? (switch not identified) | 0 | 0 | 3 | 0 | 0 | 0 | PASS |
| UNCLASSIFIED:BB_TAKE_H | 15m | N | ? (switch not identified) | 2 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:DC_BREAKOUT_ENTRY | 15m | Y | ? (switch not identified) | 1 | 0 | 1 | 0 | 1 | 0 | FAIL |
| PARTIAL_PROFIT_LOCK | tick | Y | PARTIAL_PROFIT_LOCK_ENABLED | 0 | 0 | 2 | 0 | 2 | 0 | FAIL |
| UNCLASSIFIED:B_DELTAENTRY | 15m | N | ? (switch not identified) | 1 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_OTHER | 3m | N | ABLATION_DISABLE_QUICK_EXIT | 1 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:PROFIT_TP_EXHAUSTION | 15m | N | ? (switch not identified) | 1 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:B_EMASLOPE | 15m | Y | ? (switch not identified) | 0 | 0 | 1 | 0 | 1 | 0 | FAIL |

## Test 2 - LIVE_ONLY functions (1m/3m/5m, tick, webhook, portfolio, no vec twin), CRYPTO

These decisions have no 15m vector counterpart by construction; listed so drift is visible (attempts = execute_now calls in the window).

| function family | input TF | attempts | fills | gate switch |
|---|---|---|---|---|
| QUICK_OPEN_STRONG | 3m | 11 | 0 | QUICK_OPEN_STRONG_VEC_ENABLED |
| CRYPTO_SPIKE_FADE | 3m | 4 | 0 | CRYPTO_SPIKE_FADE_ENABLED |
| UNCLASSIFIED:BB_TAKE_H | 15m | 2 | 0 | ? (switch not identified) |
| UNCLASSIFIED:B_DELTAENTRY | 15m | 1 | 0 | ? (switch not identified) |
| QUICK_REDUCE_OTHER | 3m | 1 | 0 | ABLATION_DISABLE_QUICK_EXIT |
| UNCLASSIFIED:PROFIT_TP_EXHAUSTION | 15m | 1 | 0 | ? (switch not identified) |

## Test 1 - 15m LIVE vs VECTOR, STOCKS

run 2026-10-06T20:28:45+00:00 (24 min ago) · engine v12 bfb137e3 · NPZ sync tail · window 24.0h from 2026-10-06T18:48Z · tol +-1 bars · 42 live keys / 31 sym_sides · 332.8 s

**Regime PARITY_VEC_EXACT since 2026-10-06T18:48:00Z** (previous regime archived: `data/forward_parity/archive/pre_vec_exact_until_20261006T1848Z`). Rule: every live fill = a vec decision at the same 15m bar and side (fill may land up to 1 bar later); every vec decision = a live fill or a logged hard-safety refusal; quantity not compared.

**Verdict: FAIL** — FAIL 3 · PASS with decisions 1 · IDLE (no decision either side, counts as pass) 38 · NO_DATA 0 · judged on a STALE-shifted window (S1 NPZ behind live) 0

- vector fired, live did not (VEC_ONLY): MISSING_NO_LIVE_LOG 3, LIVE_ALREADY_FLAT 1, LIVE_ALREADY_IN_POSITION 1
- live fired, vector did not (LIVE_ONLY): -
- vector replay made 0 trades in 30D for 3/31 sym_sides with the live set (engine md5 bfb137e3): every live fill on those keys is LIVE_ONLY by construction — engine/set problem, not live drift. e.g. A_LONG, EQT_SHORT, SNDK_LONG
- FAIL classes (sym_sides): VEC_DECISION_NOT_FILLED_NO_LOG 3, STATE_CASCADE 2
- since switch: live fills 236 (tagged |VEC_EXACT 0) · vec decisions 5 · matched 0 · out-of-universe live fills (not judged, no CPU): 0

### Per sym_side with any decision since the switch

| key | status | vec decisions | live fills (VEC_EXACT) | matched | vec not filled (why) | live not vec (what) |
|---|---|---|---|---|---|---|
| trb:ADBE_SHORT | FAIL | 2 | 0 (0) | 0 | LIVE_ALREADY_FLAT 1, MISSING_NO_LIVE_LOG 1 | - |
| trc:ADBE_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_IN_POSITION 1 | - |
| trc:AXON_SHORT | FAIL | 1 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1 | - |
| trc:IBIT_LONG | PASS | 0 | 236 (0) | 0 | - | - |

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|
| trb:ADBE_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_FLAT 1, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | trb/active_config |
| trc:ADBE_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_IN_POSITION 1 | - | None/None | last window | trb/active_config |
| trc:AXON_SHORT | VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1 | - | None/None | last window | trb/active_config |

PASS with activity: trc:IBIT_LONG  ·  PASS_IDLE (no decision either side): 38

### Per function family (stocks)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| BROKER_SYNC | tick | N | safety (exempt) | 0 | 236 | 0 | 0 | 0 | 0 | PASS |
| DC_DAYTRADE_TARGET | 15m | Y | DC_DAYTRADE_ENABLED | 0 | 0 | 2 | 0 | 2 | 0 | FAIL |
| UNCLASSIFIED:B_SRS_ENTRY | 15m | Y | ? (switch not identified) | 0 | 0 | 2 | 0 | 2 | 0 | FAIL |
| GAP_MOC | 5m | Y | ? (switch not identified) | 0 | 0 | 1 | 0 | 1 | 0 | FAIL |

## Test 2 - LIVE_ONLY functions (1m/3m/5m, tick, webhook, portfolio, no vec twin), STOCKS

These decisions have no 15m vector counterpart by construction; listed so drift is visible (attempts = execute_now calls in the window).

| function family | input TF | attempts | fills | gate switch |
|---|---|---|---|---|

## Sizing — live-sized fills vs the same trades at vec size (paper)

Same trades (live entry fill matched to the vec decision at that bar), closed PnL of exits since the regime switch. live = live qty x live prices; vec-size = the same exits scaled by vec notional / live notional of the matched entries (the exact vectorized size the paper test executes). sizing alpha = live - vec-size. Factors = sizing multipliers logged by ez_manage within 10 min before the entry fill (HELD_x1:* = resolved but forced x1.0 by PARITY_LIVE_SIZING_MULT_ENABLED=False). Open positions are not counted until they exit.

### CRYPTO — totals per day

no exits since the switch yet

sizing factors seen on matched entries: BAND_SLOPE_SIZING_V2 2

### CRYPTO — per sym_side per day

| key | day | exits (comparable) | live PnL $ | vec-size PnL $ | alpha $ | avg live/vec size | factors |
|---|---|---|---|---|---|---|---|
| ang:NEARUSDC_LONG | open | - | - | - | - | 0.84x | BAND_SLOPE_SIZING_V2 1 |
| men:SNXUSDT_SHORT | open | - | - | - | - | 0.03x | BAND_SLOPE_SIZING_V2 1 |

### STOCKS — totals per day

no exits since the switch yet

## decisions -> history parity (switch intents vs live fills) — rows since 2026-10-06T18:48:00+00:00 only

`DH_20261005_20261006.json` generated 2026-10-06T20:17:00.611335+00:00 (36 min ago) · intents 522 · matched 45 · **missing 477**

matched/intents per account: fin 4/10, men 11/48, ang 17/33, inf 3/8, flz 10/11, trb 0/229, trc 0/183

| function family | action | intent result | n | blocking execution filter | vector same decision +-2 bars (Y/N/? = not judged) |
|---|---|---|---|---|---|
| UNCLASSIFIED:B_KZONE | OPEN | MISSING | 5 | ORDER_SIZE_CAP 2, LIVE_RESTART_ORPHAN 2, UNATTRIBUTED 1 | ? 4, Y 1 |
| UNCLASSIFIED:B | OPEN | MISSING | 5 | MAKER_FAILED_SUPPRESS_WEBHOOK 3, NUKE_STALE_MAKER_ORDER 2 | ? 5 |
| UNCLASSIFIED:? | OPEN | MISSING | 3 | UNATTRIBUTED 3 | ? 3 |
| UNCLASSIFIED:? | CLOSE | MISSING | 3 | UNATTRIBUTED 3 | N 2, Y 1 |
| UNCLASSIFIED:B_SRS_ENTRY | OPEN | MISSING | 2 | LIVE_RESTART_ORPHAN 1, MAKER_ZERO_QTY 1 | ? 2 |
| UNCLASSIFIED:ENTRY_SIGNAL | OPEN | MISSING | 1 | NUKE_STALE_MAKER_ORDER 1 | ? 1 |
| UNCLASSIFIED:PROFIT_TP_EXHAUSTION | CLOSE | MISSING | 1 | LIVE_RESTART_ORPHAN 1 | N 1 |
| HARDCODED_RALLY_REENTRY | OPEN | MISSING | 1 | MAKER_FAILED_SUPPRESS_WEBHOOK 1 | ? 1 |
| GOLDEN_RULE | OPEN | MISSING | 1 | MAKER_FAILED_SUPPRESS_WEBHOOK 1 | ? 1 |
| UNCLASSIFIED:B_DELTAENTRY | OPEN | MISSING | 1 | LIVE_RESTART_ORPHAN 1 | ? 1 |
| HARDCODED_RALLY_REENTRY | OPEN | MATCHED | 4 | filled 4 | ? 3, N 1 |
| UNCLASSIFIED:B | OPEN | MATCHED | 3 | filled 3 | ? 2, N 1 |
| UNCLASSIFIED:B_KZONE | OPEN | MATCHED | 2 | filled 2 | ? 2 |
| GOLDEN_RULE | OPEN | MATCHED | 1 | filled 1 | ? 1 |

Gap classes: NUKE_STALE_MAKER_ORDER = maker exit rested >60 s and was cancelled (microstructure, no vec twin); UNATTRIBUTED = no blocking log line found (logging hook missing in the execution path); MAKER_ZERO_QTY / MARKET_API_ERROR = sizing/broker. Operator decisions: `data/decisions_history_parity/NEEDS-OPERATOR-DECISION_*.md`.

## Exit-engine gate parity (execute_now, all exits)

generated 2026-10-06T20:00:22.441438+00:00 (53 min ago) · lookback 48.0h · exit rows 12 · **LEAKS 0** · gate ON 4 · UNGATED 0 · safety 0 · unmapped 0

## 7-day rollup (tools/daily_parity_test.py)

`data/parity/daily_parity_20261006.md`

- Generated: 2026-10-06T06:15:12+00:00 | engine: tools/forward_parity/live_vs_vec.py (same bars, same per-sym set)
- crypto: 139 live keys · status {'PASS_IDLE': 98, 'NO_DATA': 13, 'STALE_PASS_IDLE': 11, 'FAIL': 17} · VEC_ONLY {'LIVE_ALREADY_IN_POSITION': 3, 'LIVE_NO_SIGNAL': 3} · LIVE_ONLY {'LIVE_ONLY_VEC_FAMILY:GOLDEN_RULE': 15, 'LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT': 7, 'LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m': 6, 'LIVE_ONLY_NONVEC:RANKING_DIRECT_ALL_GREEN@webhook': 5, 'LIVE_ONLY_VEC_FAMILY:DC_DAYTRADE_TARGET': 4, 'LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m': 3, 'LIVE_ONLY_VEC_FAMILY:WT_LOWER_CROSS_EXIT': 1, 'LIVE_ONLY_NONVEC:RATIO_REBALANCE@portfolio': 1, 'LIVE_ONLY_NONVEC:BREAK_EVEN_GUARD@tick': 1, 'LIVE_ONLY_VEC_FAMILY:MTF_BB_REJECT_EXIT': 1, 'LIVE_ONLY_NONVEC:ALL_TF_AGAINST_CLOSE@3m': 1, 'LIVE_ONLY_NONVEC:GUARANTEED_REENTRY@3m': 1, 'LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY': 1, 'LIVE_ONLY_NONVEC:GAIN_EROSION_STOP@3m': 1}
- stocks: 175 live keys · status {'FAIL': 151, 'PASS_IDLE': 19, 'NO_DATA': 4, 'STALE_PASS_IDLE': 1} · VEC_ONLY {'LIVE_NO_SIGNAL': 208, 'LIVE_ALREADY_FLAT': 162, 'LIVE_ALREADY_IN_POSITION': 28, 'LIVE_INTENT_NOT_FILLED:UNCLASSIFIED:?': 2} · LIVE_ONLY {'LIVE_ONLY_NONVEC:REENTRY_OTHER@5m': 226, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:SYNC_DETECTION@15m': 71, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:MFI_MEAN_REVERSION@15m': 57, 'LIVE_ONLY_VEC_FAMILY:REENTRY_OTHER': 47, 'LIVE_ONLY_NONVEC:FALLBACK_CLOSE@15m': 27, 'LIVE_ONLY_NONVEC:GAP_MOC@5m': 24, 'LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY': 23, 'LIVE_ONLY_VEC_FAMILY:DC_DAYTRADE_TARGET': 21, 'LIVE_ONLY_VEC_FAMILY:HARDCODED_RALLY_REENTRY': 18, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TARGET@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DC_BREAK_HIGH@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:ULTIMATE_DC_H@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@15m': 11, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:INTRADAY_RATIO_TRIM@15m': 5, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:STRUCT_BREAK_DC@15m': 5, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:SENT_STRAT_DIV@15m': 3, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:OVERBOUGHT_TAKE_PROFIT@15m': 2, 'LIVE_ONLY_NONVEC:GAP_FILL@5m': 2, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:GR_HTF_DIRECT@3m': 2, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:NOLOSS_BBH_BREAKDOWN@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@3m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@5m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:ROTATION_ENTRY_L@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:CLENOW_ENTRY@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:STRUCT_BREAK_DC@5m': 1}
- Totals: PASS 129 · FAIL 168 · sign flips (>=5 exits each side) 0 · alerts 86
