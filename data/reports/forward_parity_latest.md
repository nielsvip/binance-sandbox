# Forward parity — LIVE vs VECTOR (auto, read-only)

generated 2026-10-06T22:26:06+00:00 · producers: tools/forward_parity/live_vs_vec.py (crypto hourly :07, stocks every 15 min RTH), tools/decisions_history_parity.py (:17), tools/exit_engine_parity_monitor.py (launchd hourly), tools/daily_parity_test.py (launchd daily)

**REGIME PARITY_VEC_EXACT since 2026-10-06T18:48:00Z** — all monitors reset at the switch; old-regime results archived in `data/forward_parity/archive/pre_vec_exact_until_20261006T1848Z`. Universe: tradeable_keys.json + open positions only.

PASS (exact regime) = every vec decision on the judged bars is a live fill at the same bar/side or a logged hard-safety refusal, and every live fill is a vec decision. FAIL classes: VEC_DECISION_NOT_FILLED_NO_LOG, VEC_DECISION_REFUSED_NON_SAFETY, NATIVE_FILL_IN_EXACT_MODE (a non-vec live path traded), VEC_EXACT_FILL_NOT_IN_REPLAY (live vec on live_klines vs replay on the S1 NPZ disagree), STATE_CASCADE, NPZ_STALE.

PASS (legacy definition) = every vector decision on the judged 15m bars has a live fill of the same class within +-2 bars, every live fill has a vector decision, and end state (in/out) agrees. FAIL classes: VEC_ONLY_LIVE_NO_SIGNAL (live logic did not fire), VEC_ONLY_LIVE_BLOCKED (live attempted, gate/execution stopped it), VEC_ONLY_STATE_CASCADE (consequence of an earlier divergence), LIVE_ONLY_VEC_FAMILY (vector twin exists but did not fire), LIVE_ONLY_NONVEC (1m/3m/5m/tick/webhook/portfolio input — no vec counterpart), STATE_MISMATCH, NPZ_STALE (S1 NPZ behind live, judged on the newest window the vector can see).

## LIVE GUARDIAN (every 20 s, both systems) — tools/forward_parity/live_guardian.py

last sweep 2026-10-06T22:26:06Z (0 min ago) · 0.53 s · dry_run=False · RTH=False · regime start 2026-10-06T18:48:00Z

thresholds: STORM_KEY_FILLS=6, STORM_KEY_MIN=10, ATTEMPT_STORM=30, ATTEMPT_MIN=5, CHURN_ROUNDTRIPS=3, DUP_S=20, ACCOUNT_STORM_FILLS=25, ACCOUNT_STORM_MIN=10, ACCOUNT_DUP_PAIRS=3, FILL_WAIT_S=600, SIZE_MULT=3.0, LOSS_DROP_PP=4.0, LOSS_FLOOR_PCT=-3.0, EQUITY_DROP_USD=25.0, RESTARTS=3, STALE_LOG_S=120, NON_VEC_BLOCK_N=2, KEY_BLOCK_TTL_S=7200, POS_MISMATCH_PCT=5.0

alerts this sweep: FILL_WITHOUT_VEC_DECISION 58, VEC_EXIT_WHILE_LIVE_FLAT 53, VEC_DECISION_NOT_FILLED 51, POSITION_STATE_MISMATCH 5, VEC_OPEN_WHILE_LIVE_IN 2, SIZE_ANOMALY 1

### Active interventions (5) — exits are never blocked; positions are never closed by the guardian

| target | since | why | undo |
|---|---|---|---|
| acct:trc | 19:21:57Z | ACCOUNT_EQUITY_DROP unrealised PnL of the same open positions fell $26.54 since 2026-10-06T19:19:08Z (now $-291.98) | `rm /Users/niels/Documents/binance/data/HALT_TRADING_trc` |
| acct:trb | 19:24:23Z | ACCOUNT_EQUITY_DROP unrealised PnL of the same open positions fell $30.53 since 2026-10-06T19:19:51Z (now $+405.99) | `rm /Users/niels/Documents/binance/data/HALT_TRADING_trb` |
| key:men:RLCUSDT_SHORT | 21:03:45Z | SIZE_ANOMALY entry notional $21.22 (median $6.98, MAX_ORDER_VALUE $300) @ 2026-10-06T21:03:27Z B14 |VEC_EXACT | `redis-cli -p 6379 DEL open_blocked:men:RLCUSDT_SHORT` |
| key:men:XLMUSDT_SHORT | 21:04:46Z | SIZE_ANOMALY entry notional $73.83 (median $6.95, MAX_ORDER_VALUE $300) @ 2026-10-06T21:04:44Z B_KZONE |VEC_EXACT | `redis-cli -p 6379 DEL open_blocked:men:XLMUSDT_SHORT` |
| key:ang:EDUUSDT_LONG | 22:24:02Z | RAPID_LOSS_KEY unrealised -3.10% (60-min high +1.49%), amt 1193 (opens/augments blocked; exits untouched) | `redis-cli -p 6379 DEL open_blocked:ang:EDUUSDT_LONG` |

### Latest CRITICAL/WARN alerts

| ts | severity | kind | key | evidence |
|---|---|---|---|---|
| 22:26:06 | CRITICAL | VEC_DECISION_NOT_FILLED | inf:IOTAUSDT_LONG | CLOSE EXIT_VELOCITY_WT against-long g-0.29% decided 2026-10-06T21:11:16Z bar=2026-10-06T20:45:00Z: no fill in 600s and no refusal logged |
| 22:26:06 | CRITICAL | VEC_DECISION_NOT_FILLED | inf:ARPAUSDT_SHORT | OPEN B_KZONE decided 2026-10-06T21:11:16Z bar=2026-10-06T20:45:00Z: no fill in 600s and no refusal logged |
| 22:26:06 | CRITICAL | VEC_DECISION_NOT_FILLED | inf:APEUSDT_SHORT | OPEN B15 decided 2026-10-06T21:11:17Z bar=2026-10-06T20:45:00Z: no fill in 600s and no refusal logged |
| 22:26:06 | CRITICAL | VEC_DECISION_NOT_FILLED | inf:IOTAUSDT_LONG | CLOSE EXIT_VELOCITY_WT against-long g-0.03% decided 2026-10-06T22:05:27Z bar=2026-10-06T21:45:00Z: no fill in 600s and no refusal logged |
| 22:26:06 | CRITICAL | VEC_DECISION_NOT_FILLED | inf:AAVEUSDC_LONG | OPEN B_KZONE decided 2026-10-06T22:05:27Z bar=2026-10-06T21:45:00Z: no fill in 600s and no refusal logged |
| 22:26:06 | CRITICAL | FILL_WITHOUT_VEC_DECISION | inf:NEARUSDC_LONG | AUGMENT 7 @ 2026-10-06T20:17:50Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 22:26:06 | CRITICAL | FILL_WITHOUT_VEC_DECISION | inf:ARUSDT_LONG | REDUCE 22.1 @ 2026-10-06T20:39:16Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 22:26:06 | CRITICAL | FILL_WITHOUT_VEC_DECISION | inf:ARUSDT_LONG | AUGMENT 16.4 @ 2026-10-06T20:47:21Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 22:26:06 | CRITICAL | FILL_WITHOUT_VEC_DECISION | inf:ZROUSDT_LONG | AUGMENT 26.6 @ 2026-10-06T20:15:53Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 22:26:06 | CRITICAL | FILL_WITHOUT_VEC_DECISION | inf:ZROUSDT_LONG | REDUCE 4.8 @ 2026-10-06T22:01:25Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 22:26:06 | CRITICAL | FILL_WITHOUT_VEC_DECISION | inf:ZROUSDT_LONG | REDUCE 21.8 @ 2026-10-06T22:02:33Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 22:26:06 | CRITICAL | FILL_WITHOUT_VEC_DECISION | inf:NMRUSDT_LONG | AUGMENT 4.4 @ 2026-10-06T20:16:30Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 22:26:06 | CRITICAL | FILL_WITHOUT_VEC_DECISION | inf:ARBUSDC_SHORT | AUGMENT 79.7 @ 2026-10-06T22:16:59Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 22:26:06 | WARN | POSITION_STATE_MISMATCH | flz:HYPEUSDT_LONG | positions file amt 0 vs fill-ledger amt 1.08 |
| 22:26:06 | WARN | POSITION_STATE_MISMATCH | flz:ZECUSDC_LONG | positions file amt 0 vs fill-ledger amt 0.016 |
| 22:26:06 | CRITICAL | VEC_DECISION_NOT_FILLED | flz:ZECUSDC_LONG | CLOSE EXIT_VELOCITY_WT against-long g-0.12% decided 2026-10-06T18:55:38Z bar=2026-10-06T18:30:00Z: no fill in 600s and no refusal logged |
| 22:26:06 | CRITICAL | VEC_DECISION_NOT_FILLED | flz:BTCDOMUSDT_LONG | OPEN B_VWAPBOUNCE decided 2026-10-06T19:34:36Z bar=2026-10-06T19:15:00Z: no fill in 600s and no refusal logged |
| 22:26:06 | CRITICAL | VEC_DECISION_NOT_FILLED | flz:GRAMUSDT_SHORT | CLOSE EXIT_VELOCITY_WT against-short g0.13% decided 2026-10-06T21:05:16Z bar=2026-10-06T20:45:00Z: no fill in 600s and no refusal logged |
| 22:26:06 | CRITICAL | VEC_DECISION_NOT_FILLED | flz:HYPEUSDT_LONG | CLOSE EXIT_VELOCITY_WT against-long g-0.04% decided 2026-10-06T21:11:13Z bar=2026-10-06T20:45:00Z: no fill in 600s and no refusal logged |
| 22:26:06 | CRITICAL | VEC_DECISION_NOT_FILLED | flz:GRAMUSDT_SHORT | CLOSE EXIT_VELOCITY_WT against-short g0.13% decided 2026-10-06T21:11:14Z bar=2026-10-06T20:45:00Z: no fill in 600s and no refusal logged |
| 22:26:06 | WARN | VEC_OPEN_WHILE_LIVE_IN | flz:GRAMUSDT_SHORT | OPEN B_KZONE bar=2026-10-06T21:45:00Z: live already holds 55.4 (state divergence) |
| 22:26:06 | CRITICAL | FILL_WITHOUT_VEC_DECISION | flz:HYPEUSDT_LONG | AUGMENT 0.78 @ 2026-10-06T20:48:22Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 22:26:06 | CRITICAL | FILL_WITHOUT_VEC_DECISION | flz:HYPEUSDT_LONG | REDUCE 0.78 @ 2026-10-06T21:06:57Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 22:26:06 | CRITICAL | FILL_WITHOUT_VEC_DECISION | flz:ZECUSDC_LONG | AUGMENT 0.058 @ 2026-10-06T20:03:23Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |
| 22:26:06 | CRITICAL | FILL_WITHOUT_VEC_DECISION | flz:ZECUSDC_LONG | REDUCE 0.058 @ 2026-10-06T20:24:10Z tagged VEC_EXACT but no logged vec decision in the preceding 600s |

live vec decisions since switch (per account): fin: {'live_vec_decisions': 7, 'state_divergent': 6, 'filled': 0, 'refused_non_safety': 0, 'safety_refusals': 0, 'not_filled_no_log': 1, 'vec_exact_fills_without_decision': 2}; men: {'live_vec_decisions': 34, 'state_divergent': 15, 'filled': 3, 'refused_non_safety': 0, 'safety_refusals': 0, 'not_filled_no_log': 16, 'vec_exact_fills_without_decision': 31}; ang: {'live_vec_decisions': 38, 'state_divergent': 19, 'filled': 3, 'refused_non_safety': 0, 'safety_refusals': 0, 'not_filled_no_log': 16, 'vec_exact_fills_without_decision': 13}; inf: {'live_vec_decisions': 27, 'state_divergent': 13, 'filled': 1, 'refused_non_safety': 0, 'safety_refusals': 0, 'not_filled_no_log': 13, 'vec_exact_fills_without_decision': 8}; flz: {'live_vec_decisions': 7, 'state_divergent': 2, 'filled': 0, 'refused_non_safety': 0, 'safety_refusals': 0, 'not_filled_no_log': 5, 'vec_exact_fills_without_decision': 4}

processes: fin pids=['56164'] starts30m=2 log_age=1.0s, men pids=['55979'] starts30m=2 log_age=3.0s, ang pids=['55527'] starts30m=2 log_age=1.0s, inf pids=['55584'] starts30m=2 log_age=10.0s, flz pids=['55576'] starts30m=2 log_age=8.0s, trb pids=[] starts30m=0 log_age=3693.0s, trc pids=[] starts30m=0 log_age=3693.0s

## Test 1 - 15m LIVE vs VECTOR, CRYPTO

run 2026-10-06T22:14:28+00:00 (12 min ago) · engine v12 f7873360 · NPZ sync tail · window 24.0h from 2026-10-06T18:48Z · tol +-1 bars · 222 live keys / 138 sym_sides · 447.6 s

**Regime PARITY_VEC_EXACT since 2026-10-06T18:48:00Z** (previous regime archived: `data/forward_parity/archive/pre_vec_exact_until_20261006T1848Z`). Rule: every live fill = a vec decision at the same 15m bar and side (fill may land up to 1 bar later); every vec decision = a live fill or a logged hard-safety refusal; quantity not compared.

**Verdict: FAIL** — FAIL 62 · PASS with decisions 11 · IDLE (no decision either side, counts as pass) 133 · NO_DATA 16 · judged on a STALE-shifted window (S1 NPZ behind live) 0

- vector fired, live did not (VEC_ONLY): LIVE_ALREADY_FLAT 57, MISSING_NO_LIVE_LOG 50, REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 19, LIVE_ALREADY_IN_POSITION 7, REFUSED_NON_SAFETY:UNTAGGED 1
- live fired, vector did not (LIVE_ONLY): VEC_EXACT_FILL_NOT_IN_REPLAY 15, NATIVE_FILL_IN_EXACT_MODE:EXIT_VELOCITY_WT@1h 3, NATIVE_FILL_IN_EXACT_MODE:WEBHOOK_HANDLE_SIGNAL@webhook 2
- NPZ missing on S1 (no vector possible): 100PEPEUSDC, 1INCHUSDT, AAPL, AAVEUSDC, ACEUSDT, ADAUSDC, AGLDUSDT, AIAUSDT, ALGOUSDT, APEUSDT, API3USDT, ARBUSDC, ARKMUSDT, ARPAUSDT, ARUSDT, ASTSUSDT, ATOMUSDT, AVAXUSDC, AXSUSDT, BBUSDT, BNBUSDC, BTCDOMUSDT, BTCUSDC, CHRUSDT, COMPUSDT, COTIUSDT, DASHUSDT, DOGEUSDC, DOTUSDT, DUSKUSDT, EDUUSDT, EGLDUSDT, ENJUSDT, ENSUSDT, ETCUSDT, ETHFIUSDC, ETHUSDC, FILUSDC, GALAUSDT, GOOGL, GRAMUSDT, GRTUSDT, HYPEUSDT, IOTAUSDT, IOTXUSDT, KMNOUSDT, KSMUSDT, LINKUSDC, LTCUSDC, MANAUSDT, MELANIAUSDT, MINAUSDT, MORPHOUSDT, NEARUSDC, NMRUSDT, ONEUSDT, QTUMUSDT, RENDERUSDT, RLCUSDT, RVNUSDT, SANDUSDT, SKYUSDT, SNXUSDT, SOLUSDC, THETAUSDT, TRBUSDT, UNIUSDC, VETUSDT, WLDUSDC, XLMUSDT, XMRUSDT, XRPUSDC, XTZUSDT, YFIUSDT, ZECUSDC, ZENUSDT, ZROUSDT
- vector replay made 0 trades in 30D for 9/138 sym_sides with the live set (engine md5 f7873360): every live fill on those keys is LIVE_ONLY by construction — engine/set problem, not live drift. e.g. ADAUSDC_LONG, ALGOUSDT_LONG, COMPUSDT_LONG, ENSUSDT_SHORT, GRTUSDT_LONG, GRTUSDT_SHORT, RVNUSDT_SHORT, SOLUSDC_LONG
- vector errors: prepare failed: no npz 13
- FAIL classes (sym_sides): STATE_CASCADE 47, VEC_DECISION_NOT_FILLED_NO_LOG 43, VEC_DECISION_REFUSED_NON_SAFETY 13, VEC_EXACT_FILL_NOT_IN_REPLAY 12, NATIVE_FILL_IN_EXACT_MODE 4
- since switch: live fills 71 (tagged |VEC_EXACT 60) · vec decisions 141 · matched 7 · out-of-universe live fills (not judged, no CPU): 1

### Per sym_side with any decision since the switch

| key | status | vec decisions | live fills (VEC_EXACT) | matched | vec not filled (why) | live not vec (what) |
|---|---|---|---|---|---|---|
| ang:AAVEUSDC_LONG | FAIL | 2 | 0 (0) | 0 | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 2 | - |
| ang:ADAUSDC_SHORT | PASS | 0 | 1 (1) | 0 | - | - |
| ang:ASTSUSDT_LONG | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| ang:COTIUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| ang:EDUUSDT_LONG | NO_DATA | 0 | 3 (2) | 0 | - | - |
| ang:ENJUSDT_LONG | FAIL | 3 | 2 (1) | 1 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | NATIVE_FILL_IN_EXACT_MODE:EXIT_VELOCITY_WT@1h 1 |
| ang:GALAUSDT_SHORT | FAIL | 2 | 4 (3) | 0 | MISSING_NO_LIVE_LOG 2 | - |
| ang:GRAMUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| ang:KSMUSDT_LONG | PASS | 0 | 1 (1) | 0 | - | - |
| ang:NEARUSDC_LONG | FAIL | 0 | 1 (1) | 0 | - | VEC_EXACT_FILL_NOT_IN_REPLAY 1 |
| ang:NMRUSDT_LONG | NO_DATA | 0 | 2 (2) | 0 | - | - |
| ang:SANDUSDT_LONG | FAIL | 3 | 1 (1) | 0 | MISSING_NO_LIVE_LOG 2, LIVE_ALREADY_IN_POSITION 1 | VEC_EXACT_FILL_NOT_IN_REPLAY 1 |
| ang:SKYUSDT_LONG | STALE_PASS | 0 | 1 (1) | 0 | - | - |
| ang:SNXUSDT_LONG | FAIL | 2 | 1 (1) | 0 | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 2 | - |
| ang:TRBUSDT_LONG | STALE_PASS | 0 | 2 (2) | 0 | - | - |
| ang:XTZUSDT_LONG | FAIL | 3 | 0 (0) | 0 | LIVE_ALREADY_FLAT 3 | - |
| ang:ZECUSDC_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| ang:ZROUSDT_LONG | STALE_PASS | 0 | 1 (1) | 0 | - | - |
| fin:IOTXUSDT_LONG | FAIL | 3 | 0 (0) | 0 | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1 | - |
| fin:SANDUSDT_LONG | FAIL | 3 | 2 (1) | 1 | LIVE_ALREADY_IN_POSITION 1, MISSING_NO_LIVE_LOG 1 | VEC_EXACT_FILL_NOT_IN_REPLAY 1 |
| flz:DASHUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | LIVE_ALREADY_IN_POSITION 1, MISSING_NO_LIVE_LOG 1 | - |
| flz:GRAMUSDT_LONG | FAIL | 1 | 0 (0) | 0 | LIVE_ALREADY_FLAT 1 | - |
| flz:GRAMUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | LIVE_ALREADY_IN_POSITION 1, MISSING_NO_LIVE_LOG 1 | - |
| flz:HYPEUSDT_LONG | FAIL | 0 | 2 (2) | 0 | - | VEC_EXACT_FILL_NOT_IN_REPLAY 2 |
| flz:XRPUSDC_LONG | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| flz:ZECUSDC_LONG | FAIL | 3 | 2 (2) | 0 | MISSING_NO_LIVE_LOG 2, LIVE_ALREADY_IN_POSITION 1 | VEC_EXACT_FILL_NOT_IN_REPLAY 2 |
| flz:ZECUSDC_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| inf:1INCHUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| inf:AAVEUSDC_LONG | FAIL | 2 | 0 (0) | 0 | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 2 | - |
| inf:ACEUSDT_SHORT | FAIL | 1 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1 | - |
| inf:AGLDUSDT_SHORT | FAIL | 4 | 0 (0) | 0 | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1, REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 1 | - |
| inf:APEUSDT_SHORT | FAIL | 4 | 0 (0) | 0 | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 2, MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| inf:API3USDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| inf:ARKMUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| inf:ARUSDT_LONG | FAIL | 2 | 3 (3) | 2 | - | VEC_EXACT_FILL_NOT_IN_REPLAY 1 |
| inf:IOTXUSDT_LONG | FAIL | 3 | 0 (0) | 0 | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1 | - |
| inf:NEARUSDC_LONG | FAIL | 0 | 2 (1) | 0 | - | VEC_EXACT_FILL_NOT_IN_REPLAY 1, NATIVE_FILL_IN_EXACT_MODE:EXIT_VELOCITY_WT@1h 1 |
| inf:NMRUSDT_LONG | NO_DATA | 0 | 1 (1) | 0 | - | - |
| inf:XTZUSDT_LONG | FAIL | 3 | 0 (0) | 0 | LIVE_ALREADY_FLAT 3 | - |
| inf:ZROUSDT_LONG | STALE_PASS | 0 | 3 (3) | 0 | - | - |
| men:1INCHUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| men:ADAUSDC_SHORT | FAIL | 0 | 1 (1) | 0 | - | VEC_EXACT_FILL_NOT_IN_REPLAY 1 |
| men:ARUSDT_LONG | FAIL | 2 | 0 (0) | 0 | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 2 | - |
| men:AXSUSDT_SHORT | FAIL | 4 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 2, LIVE_ALREADY_FLAT 2 | - |
| men:CHRUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| men:COMPUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | LIVE_ALREADY_FLAT 1, MISSING_NO_LIVE_LOG 1 | - |
| men:COTIUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 1 | - |
| men:DASHUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| men:EDUUSDT_LONG | NO_DATA | 0 | 3 (2) | 0 | - | - |
| men:EGLDUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | LIVE_ALREADY_FLAT 1, MISSING_NO_LIVE_LOG 1 | - |
| men:ENJUSDT_LONG | FAIL | 3 | 1 (1) | 0 | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 2, LIVE_ALREADY_FLAT 1 | - |
| men:ENJUSDT_SHORT | FAIL | 2 | 1 (1) | 0 | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 1, LIVE_ALREADY_FLAT 1 | - |
| men:ETCUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| men:GALAUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 2 | - |
| men:IOTXUSDT_LONG | FAIL | 3 | 0 (0) | 0 | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1 | - |
| men:KMNOUSDT_SHORT | NO_DATA | 0 | 1 (1) | 0 | - | - |
| men:KSMUSDT_LONG | PASS | 0 | 2 (2) | 0 | - | - |
| men:KSMUSDT_SHORT | FAIL | 2 | 4 (2) | 0 | LIVE_ALREADY_FLAT 1, REFUSED_NON_SAFETY:UNTAGGED 1 | NATIVE_FILL_IN_EXACT_MODE:WEBHOOK_HANDLE_SIGNAL@webhook 2, VEC_EXACT_FILL_NOT_IN_REPLAY 1 |
| men:LINKUSDC_SHORT | FAIL | 3 | 0 (0) | 0 | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1 | - |
| men:LTCUSDC_LONG | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| men:LTCUSDC_SHORT | FAIL | 4 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 2, LIVE_ALREADY_FLAT 2 | - |
| men:MANAUSDT_SHORT | FAIL | 4 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 2, LIVE_ALREADY_FLAT 2 | - |
| men:MELANIAUSDT_SHORT | NO_DATA | 0 | 2 (2) | 0 | - | - |
| men:NEARUSDC_SHORT | FAIL | 1 | 0 (0) | 0 | LIVE_ALREADY_FLAT 1 | - |
| men:NMRUSDT_LONG | NO_DATA | 0 | 2 (1) | 0 | - | - |
| men:QTUMUSDT_SHORT | FAIL | 2 | 0 (0) | 0 | LIVE_ALREADY_FLAT 1, MISSING_NO_LIVE_LOG 1 | - |
| men:RENDERUSDT_LONG | STALE_PASS | 0 | 1 (1) | 0 | - | - |
| men:RLCUSDT_SHORT | FAIL | 3 | 2 (2) | 0 | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1 | VEC_EXACT_FILL_NOT_IN_REPLAY 1 |
| men:SANDUSDT_LONG | FAIL | 3 | 3 (3) | 1 | LIVE_ALREADY_FLAT 1, MISSING_NO_LIVE_LOG 1 | - |
| men:SKYUSDT_LONG | STALE_PASS | 0 | 1 (1) | 0 | - | - |
| men:SNXUSDT_LONG | FAIL | 2 | 2 (2) | 0 | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 2 | - |
| men:SNXUSDT_SHORT | FAIL | 4 | 2 (1) | 0 | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_IN_POSITION 1 | VEC_EXACT_FILL_NOT_IN_REPLAY 1, NATIVE_FILL_IN_EXACT_MODE:EXIT_VELOCITY_WT@1h 1 |
| men:VETUSDT_SHORT | PASS | 0 | 2 (1) | 0 | - | - |
| men:XLMUSDT_SHORT | FAIL | 4 | 3 (3) | 1 | LIVE_ALREADY_FLAT 1, MISSING_NO_LIVE_LOG 1, REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 1 | VEC_EXACT_FILL_NOT_IN_REPLAY 2 |
| men:XMRUSDT_LONG | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| men:XRPUSDC_LONG | FAIL | 2 | 0 (0) | 0 | LIVE_ALREADY_IN_POSITION 1, MISSING_NO_LIVE_LOG 1 | - |
| men:XTZUSDT_LONG | FAIL | 3 | 0 (0) | 0 | LIVE_ALREADY_FLAT 3 | - |
| men:XTZUSDT_SHORT | FAIL | 2 | 1 (1) | 1 | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 1 | - |
| men:ZENUSDT_LONG | FAIL | 2 | 0 (0) | 0 | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - |
| men:ZROUSDT_LONG | STALE_PASS | 0 | 2 (2) | 0 | - | - |

### FAIL per sym_side

| key | classes | vec_only (why) | live_only (function@tf) | vec/live in pos | judged window | set |
|---|---|---|---|---|---|---|
| men:SNXUSDT_SHORT | NATIVE_FILL_IN_EXACT_MODE, STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG, VEC_EXACT_FILL_NOT_IN_REPLAY | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_IN_POSITION 1 | VEC_EXACT_FILL_NOT_IN_REPLAY 1, NATIVE_FILL_IN_EXACT_MODE:EXIT_VELOCITY_WT@1h 1 | None/None | last window | per_sym_store |
| flz:ZECUSDC_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG, VEC_EXACT_FILL_NOT_IN_REPLAY | MISSING_NO_LIVE_LOG 2, LIVE_ALREADY_IN_POSITION 1 | VEC_EXACT_FILL_NOT_IN_REPLAY 2 | None/None | last window | per_sym_store |
| men:KSMUSDT_SHORT | NATIVE_FILL_IN_EXACT_MODE, STATE_CASCADE, VEC_DECISION_REFUSED_NON_SAFETY, VEC_EXACT_FILL_NOT_IN_REPLAY | LIVE_ALREADY_FLAT 1, REFUSED_NON_SAFETY:UNTAGGED 1 | NATIVE_FILL_IN_EXACT_MODE:WEBHOOK_HANDLE_SIGNAL@webhook 2, VEC_EXACT_FILL_NOT_IN_REPLAY 1 | None/None | last window | per_sym_store |
| men:XLMUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG, VEC_DECISION_REFUSED_NON_SAFETY, VEC_EXACT_FILL_NOT_IN_REPLAY | LIVE_ALREADY_FLAT 1, MISSING_NO_LIVE_LOG 1, REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 1 | VEC_EXACT_FILL_NOT_IN_REPLAY 2 | None/None | last window | per_sym_store |
| ang:SANDUSDT_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG, VEC_EXACT_FILL_NOT_IN_REPLAY | MISSING_NO_LIVE_LOG 2, LIVE_ALREADY_IN_POSITION 1 | VEC_EXACT_FILL_NOT_IN_REPLAY 1 | None/None | last window | vec_driven:progress:3d463eab |
| inf:AGLDUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG, VEC_DECISION_REFUSED_NON_SAFETY | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1, REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 1 | - | None/None | last window | per_sym_store |
| inf:APEUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG, VEC_DECISION_REFUSED_NON_SAFETY | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 2, MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| men:AXSUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 2, LIVE_ALREADY_FLAT 2 | - | None/None | last window | vec_driven:candidates:5ca4cb |
| men:LTCUSDC_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 2, LIVE_ALREADY_FLAT 2 | - | None/None | last window | per_sym_store |
| men:MANAUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 2, LIVE_ALREADY_FLAT 2 | - | None/None | last window | per_sym_store |
| men:RLCUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG, VEC_EXACT_FILL_NOT_IN_REPLAY | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1 | VEC_EXACT_FILL_NOT_IN_REPLAY 1 | None/None | last window | per_sym_store |
| ang:ENJUSDT_LONG | NATIVE_FILL_IN_EXACT_MODE, STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | NATIVE_FILL_IN_EXACT_MODE:EXIT_VELOCITY_WT@1h 1 | None/None | last window | per_sym_store |
| ang:XTZUSDT_LONG | STATE_CASCADE | LIVE_ALREADY_FLAT 3 | - | None/None | last window | vec_driven:progress:0638329a |
| fin:IOTXUSDT_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | vec_driven:progress:a761dc6d |
| fin:SANDUSDT_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG, VEC_EXACT_FILL_NOT_IN_REPLAY | LIVE_ALREADY_IN_POSITION 1, MISSING_NO_LIVE_LOG 1 | VEC_EXACT_FILL_NOT_IN_REPLAY 1 | None/None | last window | vec_driven:progress:3d463eab |
| inf:IOTXUSDT_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | vec_driven:progress:a761dc6d |
| inf:XTZUSDT_LONG | STATE_CASCADE | LIVE_ALREADY_FLAT 3 | - | None/None | last window | vec_driven:progress:0638329a |
| men:ENJUSDT_LONG | STATE_CASCADE, VEC_DECISION_REFUSED_NON_SAFETY | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 2, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| men:IOTXUSDT_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | vec_driven:progress:a761dc6d |
| men:LINKUSDC_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_FLAT 2, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | per_sym_store |
| men:XTZUSDT_LONG | STATE_CASCADE | LIVE_ALREADY_FLAT 3 | - | None/None | last window | vec_driven:progress:0638329a |
| ang:AAVEUSDC_LONG | VEC_DECISION_REFUSED_NON_SAFETY | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 2 | - | None/None | last window | vec_driven:candidates:a72467 |
| ang:ASTSUSDT_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| ang:COTIUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| ang:GALAUSDT_SHORT | VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 2 | - | None/None | last window | per_sym_store |
| ang:GRAMUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| ang:SNXUSDT_LONG | VEC_DECISION_REFUSED_NON_SAFETY | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 2 | - | None/None | last window | vec_driven:progress:793e773a |
| ang:ZECUSDC_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| flz:DASHUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_IN_POSITION 1, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | per_sym_store |
| flz:GRAMUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_IN_POSITION 1, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | per_sym_store |
| flz:HYPEUSDT_LONG | VEC_EXACT_FILL_NOT_IN_REPLAY | - | VEC_EXACT_FILL_NOT_IN_REPLAY 2 | None/None | last window | per_sym_store |
| flz:XRPUSDC_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| flz:ZECUSDC_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| inf:1INCHUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | vec_driven:candidates:63e881 |
| inf:AAVEUSDC_LONG | VEC_DECISION_REFUSED_NON_SAFETY | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 2 | - | None/None | last window | vec_driven:candidates:a72467 |
| inf:API3USDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| inf:ARKMUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| inf:NEARUSDC_LONG | NATIVE_FILL_IN_EXACT_MODE, VEC_EXACT_FILL_NOT_IN_REPLAY | - | VEC_EXACT_FILL_NOT_IN_REPLAY 1, NATIVE_FILL_IN_EXACT_MODE:EXIT_VELOCITY_WT@1h 1 | None/None | last window | per_sym_store |
| men:1INCHUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | vec_driven:candidates:63e881 |
| men:ARUSDT_LONG | VEC_DECISION_REFUSED_NON_SAFETY | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 2 | - | None/None | last window | vec_driven:candidates:6b3c88 |
| men:CHRUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| men:COMPUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_FLAT 1, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | per_sym_store |
| men:COTIUSDT_SHORT | VEC_DECISION_NOT_FILLED_NO_LOG, VEC_DECISION_REFUSED_NON_SAFETY | MISSING_NO_LIVE_LOG 1, REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 1 | - | None/None | last window | per_sym_store |
| men:DASHUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| men:EGLDUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_FLAT 1, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | per_sym_store |
| men:ENJUSDT_SHORT | STATE_CASCADE, VEC_DECISION_REFUSED_NON_SAFETY | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| men:ETCUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| men:GALAUSDT_SHORT | VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 2 | - | None/None | last window | per_sym_store |
| men:LTCUSDC_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| men:QTUMUSDT_SHORT | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_FLAT 1, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | per_sym_store |
| men:SANDUSDT_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_FLAT 1, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | vec_driven:progress:3d463eab |
| men:SNXUSDT_LONG | VEC_DECISION_REFUSED_NON_SAFETY | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 2 | - | None/None | last window | vec_driven:progress:793e773a |
| men:XMRUSDT_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | vec_driven:progress:cb297e6c |
| men:XRPUSDC_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | LIVE_ALREADY_IN_POSITION 1, MISSING_NO_LIVE_LOG 1 | - | None/None | last window | per_sym_store |
| men:ZENUSDT_LONG | STATE_CASCADE, VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1, LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| ang:NEARUSDC_LONG | VEC_EXACT_FILL_NOT_IN_REPLAY | - | VEC_EXACT_FILL_NOT_IN_REPLAY 1 | None/None | last window | per_sym_store |
| flz:GRAMUSDT_LONG | STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | None/None | last window | vec_driven:progress:a7c93e12 |
| inf:ACEUSDT_SHORT | VEC_DECISION_NOT_FILLED_NO_LOG | MISSING_NO_LIVE_LOG 1 | - | None/None | last window | vec_driven:progress:3cfa64df |
| inf:ARUSDT_LONG | VEC_EXACT_FILL_NOT_IN_REPLAY | - | VEC_EXACT_FILL_NOT_IN_REPLAY 1 | None/None | last window | vec_driven:candidates:6b3c88 |
| men:ADAUSDC_SHORT | VEC_EXACT_FILL_NOT_IN_REPLAY | - | VEC_EXACT_FILL_NOT_IN_REPLAY 1 | None/None | last window | per_sym_store |
| men:NEARUSDC_SHORT | STATE_CASCADE | LIVE_ALREADY_FLAT 1 | - | None/None | last window | per_sym_store |
| men:XTZUSDT_SHORT | VEC_DECISION_REFUSED_NON_SAFETY | REFUSED_NON_SAFETY:FLAT_NOT_AUGMENT 1 | - | None/None | last window | per_sym_store |

PASS with activity: ang:ADAUSDC_SHORT, ang:KSMUSDT_LONG, men:KSMUSDT_LONG, men:VETUSDT_SHORT  ·  PASS_IDLE (no decision either side): 133

NO_DATA reasons: prepare failed: no npz 16

### Per function family (crypto)

| family | input TF | vec twin | gate switch | live attempts | live fills | vec events | matched | vec_only | live_only | status |
|---|---|---|---|---|---|---|---|---|---|---|
| EXIT_VELOCITY_WT | 1h | Y | EXIT_VELOCITY_WT_ENABLED | 184 | 14 | 48 | 0 | 48 | 5 | FAIL |
| UNCLASSIFIED:B_KZONE | 15m | Y | ? (switch not identified) | 47 | 9 | 26 | 2 | 24 | 3 | FAIL |
| UNCLASSIFIED:B | 15m | Y | ? (switch not identified) | 42 | 12 | 15 | 1 | 14 | 6 | FAIL |
| DC_DAYTRADE_TARGET | 15m | Y | DC_DAYTRADE_ENABLED | 34 | 0 | 13 | 1 | 12 | 0 | FAIL |
| GOLDEN_RULE | 15m | Y | GOLDEN_RULE_ENABLED | 25 | 1 | 3 | 0 | 3 | 0 | FAIL |
| UNCLASSIFIED:ENTRY_SIGNAL | 15m | Y | ? (switch not identified) | 15 | 9 | 2 | 0 | 2 | 2 | FAIL |
| UNCLASSIFIED:B_SRS_ENTRY | 15m | Y | ? (switch not identified) | 18 | 2 | 4 | 0 | 4 | 0 | FAIL |
| UNCLASSIFIED:WT_CROSS_EXIT | 15m | Y | ? (switch not identified) | 3 | 1 | 12 | 1 | 11 | 0 | FAIL |
| QUICK_OPEN_STRONG | 3m | Y | QUICK_OPEN_STRONG_VEC_ENABLED | 11 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:B_DELTAENTRY | 15m | Y | ? (switch not identified) | 5 | 2 | 2 | 1 | 1 | 0 | FAIL |
| HARDCODED_RALLY_REENTRY | 15m | Y | HARDCODED_RALLY_REENTRY_ENABLED | 6 | 2 | 0 | 0 | 0 | 1 | FAIL |
| UNCLASSIFIED:WT_CROSSUNDER_FINAL | 15m | N | ? (switch not identified) | 6 | 1 | 0 | 0 | 0 | 0 | PASS |
| PARTIAL_PROFIT_LOCK | tick | Y | PARTIAL_PROFIT_LOCK_ENABLED | 0 | 0 | 6 | 0 | 6 | 0 | FAIL |
| UNCLASSIFIED:DC_BREAKOUT_ENTRY | 15m | Y | ? (switch not identified) | 3 | 0 | 2 | 0 | 2 | 0 | FAIL |
| CRYPTO_SPIKE_FADE | 3m | N | CRYPTO_SPIKE_FADE_ENABLED | 4 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:B_EMASLOPE | 15m | Y | ? (switch not identified) | 1 | 1 | 2 | 0 | 2 | 0 | FAIL |
| UNCLASSIFIED:STATEFUL_PORTED_EXIT | 15m | Y | ? (switch not identified) | 0 | 0 | 4 | 1 | 3 | 0 | FAIL |
| UNCLASSIFIED:B_VWAPBOUNCE | 15m | N | ? (switch not identified) | 2 | 1 | 0 | 0 | 0 | 1 | LIVE_ONLY(nonvec) |
| WEBHOOK_HANDLE_SIGNAL | webhook | N | ? (switch not identified) | 1 | 2 | 0 | 0 | 0 | 2 | LIVE_ONLY(nonvec) |
| UNCLASSIFIED:BB_TAKE_H | 15m | N | ? (switch not identified) | 2 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:SUBSTITUTION_FOR_MEN | 15m | N | ? (switch not identified) | 2 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:MTF_DC_REJECT | 15m | N | ? (switch not identified) | 2 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:WR_PULLBACK | 15m | Y | ? (switch not identified) | 0 | 0 | 2 | 0 | 2 | 0 | FAIL |
| UNCLASSIFIED:MARGIN_FREE_FOR | 15m | N | ? (switch not identified) | 1 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| CYCLE_TP | tick | Y | CYCLE_TP_TIERED_ENABLED | 1 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:CB_HTF_EXHAUST | 15m | N | ? (switch not identified) | 1 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| QUICK_REDUCE_OTHER | 3m | N | ABLATION_DISABLE_QUICK_EXIT | 1 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |
| UNCLASSIFIED:PROFIT_TP_EXHAUSTION | 15m | N | ? (switch not identified) | 1 | 0 | 0 | 0 | 0 | 0 | LIVE_ONLY_ATTEMPTS |

## Test 2 - LIVE_ONLY functions (1m/3m/5m, tick, webhook, portfolio, no vec twin), CRYPTO

These decisions have no 15m vector counterpart by construction; listed so drift is visible (attempts = execute_now calls in the window).

| function family | input TF | attempts | fills | gate switch |
|---|---|---|---|---|
| QUICK_OPEN_STRONG | 3m | 11 | 0 | QUICK_OPEN_STRONG_VEC_ENABLED |
| UNCLASSIFIED:WT_CROSSUNDER_FINAL | 15m | 6 | 1 | ? (switch not identified) |
| CRYPTO_SPIKE_FADE | 3m | 4 | 0 | CRYPTO_SPIKE_FADE_ENABLED |
| UNCLASSIFIED:B_VWAPBOUNCE | 15m | 2 | 1 | ? (switch not identified) |
| UNCLASSIFIED:BB_TAKE_H | 15m | 2 | 0 | ? (switch not identified) |
| UNCLASSIFIED:SUBSTITUTION_FOR_MEN | 15m | 2 | 0 | ? (switch not identified) |
| UNCLASSIFIED:MTF_DC_REJECT | 15m | 2 | 0 | ? (switch not identified) |
| WEBHOOK_HANDLE_SIGNAL | webhook | 1 | 2 | ? (switch not identified) |
| UNCLASSIFIED:MARGIN_FREE_FOR | 15m | 1 | 0 | ? (switch not identified) |
| CYCLE_TP | tick | 1 | 0 | CYCLE_TP_TIERED_ENABLED |
| UNCLASSIFIED:CB_HTF_EXHAUST | 15m | 1 | 0 | ? (switch not identified) |
| QUICK_REDUCE_OTHER | 3m | 1 | 0 | ABLATION_DISABLE_QUICK_EXIT |
| UNCLASSIFIED:PROFIT_TP_EXHAUSTION | 15m | 1 | 0 | ? (switch not identified) |

## Test 1 - 15m LIVE vs VECTOR, STOCKS

run 2026-10-06T20:28:45+00:00 (117 min ago) · engine v12 bfb137e3 · NPZ sync tail · window 24.0h from 2026-10-06T18:48Z · tol +-1 bars · 42 live keys / 31 sym_sides · 332.8 s

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

| day | exits | comparable | live PnL $ | vec-size PnL $ | sizing alpha $ | uncomparable live PnL $ |
|---|---|---|---|---|---|---|
| 2026-10-06 | 20 | 3 | +0.39 | +0.26 | **+0.13** | -4.09 |

sizing factors seen on matched entries: BAND_SLOPE_SIZING_V2 4

### CRYPTO — per sym_side per day

| key | day | exits (comparable) | live PnL $ | vec-size PnL $ | alpha $ | avg live/vec size | factors |
|---|---|---|---|---|---|---|---|
| men:XTZUSDT_SHORT | open | - | - | - | - | 0.12x | BAND_SLOPE_SIZING_V2 1 |
| ang:ENJUSDT_LONG | 2026-10-06 | 1 (1) | +0.00 | +0.06 | -0.05 | 0.07x | BAND_SLOPE_SIZING_V2 1 |
| ang:GALAUSDT_SHORT | 2026-10-06 | 1 (0) | +0.00 | +0.00 | +0.00 | None | - |
| ang:TRBUSDT_LONG | 2026-10-06 | 1 (0) | +0.00 | +0.00 | +0.00 | None | - |
| fin:SANDUSDT_LONG | 2026-10-06 | 1 (0) | +0.00 | +0.00 | +0.00 | None | - |
| flz:HYPEUSDT_LONG | 2026-10-06 | 1 (0) | +0.00 | +0.00 | +0.00 | None | - |
| flz:ZECUSDC_LONG | 2026-10-06 | 1 (0) | +0.00 | +0.00 | +0.00 | None | - |
| inf:ARUSDT_LONG | 2026-10-06 | 1 (1) | +0.46 | +0.35 | +0.12 | 1.34x | BAND_SLOPE_SIZING_V2 1 |
| inf:NEARUSDC_LONG | 2026-10-06 | 1 (0) | +0.00 | +0.00 | +0.00 | None | - |
| inf:ZROUSDT_LONG | 2026-10-06 | 2 (0) | +0.00 | +0.00 | +0.00 | None | - |
| men:KSMUSDT_LONG | 2026-10-06 | 1 (0) | +0.00 | +0.00 | +0.00 | None | - |
| men:KSMUSDT_SHORT | 2026-10-06 | 2 (0) | +0.00 | +0.00 | +0.00 | None | - |
| men:RLCUSDT_SHORT | 2026-10-06 | 1 (0) | +0.00 | +0.00 | +0.00 | None | - |
| men:SANDUSDT_LONG | 2026-10-06 | 1 (0) | +0.00 | +0.00 | +0.00 | None | - |
| men:SNXUSDT_LONG | 2026-10-06 | 1 (0) | +0.00 | +0.00 | +0.00 | None | - |
| men:SNXUSDT_SHORT | 2026-10-06 | 1 (0) | +0.00 | +0.00 | +0.00 | None | - |
| men:VETUSDT_SHORT | 2026-10-06 | 1 (0) | +0.00 | +0.00 | +0.00 | None | - |
| men:XLMUSDT_SHORT | 2026-10-06 | 1 (1) | -0.08 | -0.14 | +0.07 | 0.54x | BAND_SLOPE_SIZING_V2 1 |
| men:ZROUSDT_LONG | 2026-10-06 | 1 (0) | +0.00 | +0.00 | +0.00 | None | - |

### STOCKS — totals per day

no exits since the switch yet

## decisions -> history parity (switch intents vs live fills) — rows since 2026-10-06T18:48:00+00:00 only

`DH_20261005_20261006.json` generated 2026-10-06T22:17:01.015001+00:00 (9 min ago) · intents 753 · matched 102 · **missing 651**

matched/intents per account: fin 7/15, men 41/194, ang 32/77, inf 9/37, flz 13/18, trb 0/229, trc 0/183

| function family | action | intent result | n | blocking execution filter | vector same decision +-2 bars (Y/N/? = not judged) |
|---|---|---|---|---|---|
| EXIT_VELOCITY_WT | CLOSE | MISSING | 73 | MAKER_BLOCK_LOCK_BUSY 41, NUKE_STALE_MAKER_ORDER 18, LIVE_RESTART_ORPHAN 7, UNATTRIBUTED 6 | ? 52, N 11, Y 10 |
| UNCLASSIFIED:B_KZONE | OPEN | MISSING | 37 | MAKER_FAILED_SUPPRESS_WEBHOOK 17, NUKE_STALE_MAKER_ORDER 11, MAKER_ZERO_QTY 4, UNATTRIBUTED 3 | ? 23, Y 8, N 6 |
| UNCLASSIFIED:B | OPEN | MISSING | 26 | MAKER_FAILED_SUPPRESS_WEBHOOK 12, NUKE_STALE_MAKER_ORDER 10, UNATTRIBUTED 3, LIVE_RESTART_ORPHAN 1 | ? 16, Y 8, N 2 |
| DC_DAYTRADE_TARGET | CLOSE | MISSING | 18 | MAKER_BLOCK_LOCK_BUSY 12, NUKE_STALE_MAKER_ORDER 3, NUKE_STUCK_PLACING_WIPE 2, ORDER_SIZE_CAP 1 | ? 9, Y 6, N 3 |
| UNCLASSIFIED:B_SRS_ENTRY | OPEN | MISSING | 13 | MAKER_FAILED_SUPPRESS_WEBHOOK 7, NUKE_STALE_MAKER_ORDER 3, MAKER_ZERO_QTY 3 | N 6, Y 5, ? 2 |
| UNCLASSIFIED:ENTRY_SIGNAL | OPEN | MISSING | 7 | MAKER_FAILED_SUPPRESS_WEBHOOK 3, MAKER_ZERO_QTY 2, NUKE_STALE_MAKER_ORDER 1, NUKE_STUCK_PLACING_WIPE 1 | ? 6, Y 1 |
| GOLDEN_RULE | OPEN | MISSING | 4 | NUKE_STALE_MAKER_ORDER 3, MAKER_FAILED_SUPPRESS_WEBHOOK 1 | ? 4 |
| UNCLASSIFIED:B_DELTAENTRY | OPEN | MISSING | 3 | LIVE_RESTART_ORPHAN 1, MAKER_FAILED_SUPPRESS_WEBHOOK 1, NUKE_STALE_MAKER_ORDER 1 | Y 2, ? 1 |
| UNCLASSIFIED:B_VWAPBOUNCE | OPEN | MISSING | 3 | MAKER_FAILED_SUPPRESS_WEBHOOK 3 | ? 2, N 1 |
| UNCLASSIFIED:? | OPEN | MISSING | 3 | UNATTRIBUTED 3 | ? 3 |
| UNCLASSIFIED:? | CLOSE | MISSING | 3 | UNATTRIBUTED 3 | N 2, Y 1 |
| UNCLASSIFIED:WINNER | HAIKU_AUGMENT | MISSING | 2 | LIVE_RESTART_ORPHAN 2 | ? 2 |
| UNCLASSIFIED:PROFIT_TP_EXHAUSTION | CLOSE | MISSING | 1 | LIVE_RESTART_ORPHAN 1 | Y 1 |
| HARDCODED_RALLY_REENTRY | OPEN | MISSING | 1 | MAKER_FAILED_SUPPRESS_WEBHOOK 1 | ? 1 |
| UNCLASSIFIED:DC_BREAKOUT_ENTRY | OPEN | MISSING | 1 | MAKER_FAILED_SUPPRESS_WEBHOOK 1 | Y 1 |
| UNCLASSIFIED:MTF_DC_REJECT | CLOSE | MISSING | 1 | LIVE_RESTART_ORPHAN 1 | Y 1 |
| UNCLASSIFIED:WT_CROSSUNDER_FINAL | CLOSE | MISSING | 1 | NUKE_STALE_MAKER_ORDER 1 | Y 1 |
| UNCLASSIFIED:B | OPEN | MATCHED | 15 | filled 15 | ? 9, N 5, Y 1 |
| EXIT_VELOCITY_WT | CLOSE | MATCHED | 14 | filled 14 | ? 8, N 4, Y 2 |
| UNCLASSIFIED:B_KZONE | OPEN | MATCHED | 10 | filled 10 | ? 5, Y 4, N 1 |
| UNCLASSIFIED:ENTRY_SIGNAL | OPEN | MATCHED | 8 | filled 8 | ? 6, Y 2 |
| HARDCODED_RALLY_REENTRY | OPEN | MATCHED | 4 | filled 4 | ? 3, N 1 |
| UNCLASSIFIED:DC_BREAKOUT_ENTRY | OPEN | MATCHED | 2 | filled 2 | ? 2 |
| GOLDEN_RULE | OPEN | MATCHED | 2 | filled 2 | ? 2 |
| UNCLASSIFIED:B_DELTAENTRY | OPEN | MATCHED | 2 | filled 2 | ? 1, Y 1 |
| UNCLASSIFIED:B_SRS_ENTRY | OPEN | MATCHED | 2 | filled 2 | ? 2 |
| UNCLASSIFIED:SUBSTITUTION_FOR_MEN | QUICK_CLOSE | MATCHED | 2 | filled 2 | ? 2 |
| UNCLASSIFIED:WT_CROSSUNDER_FINAL | CLOSE | MATCHED | 1 | filled 1 | Y 1 |
| WEBHOOK_HANDLE_SIGNAL | REDUCE | MATCHED | 1 | filled 1 | Y 1 |
| UNCLASSIFIED:B_EMASLOPE | OPEN | MATCHED | 1 | filled 1 | ? 1 |
| UNCLASSIFIED:WT_CROSS_EXIT | CLOSE | MATCHED | 1 | filled 1 | ? 1 |
| UNCLASSIFIED:MARGIN_FREE_FOR | QUICK_CLOSE | MATCHED | 1 | filled 1 | ? 1 |
| UNCLASSIFIED:B_VWAPBOUNCE | OPEN | MATCHED | 1 | filled 1 | N 1 |

Gap classes: NUKE_STALE_MAKER_ORDER = maker exit rested >60 s and was cancelled (microstructure, no vec twin); UNATTRIBUTED = no blocking log line found (logging hook missing in the execution path); MAKER_ZERO_QTY / MARKET_API_ERROR = sizing/broker. Operator decisions: `data/decisions_history_parity/NEEDS-OPERATOR-DECISION_*.md`.

## Exit-engine gate parity (execute_now, all exits)

generated 2026-10-06T22:00:34.214354+00:00 (26 min ago) · lookback 48.0h · exit rows 353 · **LEAKS 0** · gate ON 157 · UNGATED 0 · safety 0 · unmapped 11

## 7-day rollup (tools/daily_parity_test.py)

`data/parity/daily_parity_20261006.md`

- Generated: 2026-10-06T06:15:12+00:00 | engine: tools/forward_parity/live_vs_vec.py (same bars, same per-sym set)
- crypto: 139 live keys · status {'PASS_IDLE': 98, 'NO_DATA': 13, 'STALE_PASS_IDLE': 11, 'FAIL': 17} · VEC_ONLY {'LIVE_ALREADY_IN_POSITION': 3, 'LIVE_NO_SIGNAL': 3} · LIVE_ONLY {'LIVE_ONLY_VEC_FAMILY:GOLDEN_RULE': 15, 'LIVE_ONLY_VEC_FAMILY:EXIT_VELOCITY_WT': 7, 'LIVE_ONLY_NONVEC:QUICK_OPEN_STRONG@3m': 6, 'LIVE_ONLY_NONVEC:RANKING_DIRECT_ALL_GREEN@webhook': 5, 'LIVE_ONLY_VEC_FAMILY:DC_DAYTRADE_TARGET': 4, 'LIVE_ONLY_NONVEC:QUICK_REDUCE_STRONG@3m': 3, 'LIVE_ONLY_VEC_FAMILY:WT_LOWER_CROSS_EXIT': 1, 'LIVE_ONLY_NONVEC:RATIO_REBALANCE@portfolio': 1, 'LIVE_ONLY_NONVEC:BREAK_EVEN_GUARD@tick': 1, 'LIVE_ONLY_VEC_FAMILY:MTF_BB_REJECT_EXIT': 1, 'LIVE_ONLY_NONVEC:ALL_TF_AGAINST_CLOSE@3m': 1, 'LIVE_ONLY_NONVEC:GUARANTEED_REENTRY@3m': 1, 'LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY': 1, 'LIVE_ONLY_NONVEC:GAIN_EROSION_STOP@3m': 1}
- stocks: 175 live keys · status {'FAIL': 151, 'PASS_IDLE': 19, 'NO_DATA': 4, 'STALE_PASS_IDLE': 1} · VEC_ONLY {'LIVE_NO_SIGNAL': 208, 'LIVE_ALREADY_FLAT': 162, 'LIVE_ALREADY_IN_POSITION': 28, 'LIVE_INTENT_NOT_FILLED:UNCLASSIFIED:?': 2} · LIVE_ONLY {'LIVE_ONLY_NONVEC:REENTRY_OTHER@5m': 226, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:SYNC_DETECTION@15m': 71, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:MFI_MEAN_REVERSION@15m': 57, 'LIVE_ONLY_VEC_FAMILY:REENTRY_OTHER': 47, 'LIVE_ONLY_NONVEC:FALLBACK_CLOSE@15m': 27, 'LIVE_ONLY_NONVEC:GAP_MOC@5m': 24, 'LIVE_ONLY_VEC_FAMILY:GUARANTEED_REENTRY': 23, 'LIVE_ONLY_VEC_FAMILY:DC_DAYTRADE_TARGET': 21, 'LIVE_ONLY_VEC_FAMILY:HARDCODED_RALLY_REENTRY': 18, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TARGET@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DC_BREAK_HIGH@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:ULTIMATE_DC_H@15m': 12, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@15m': 11, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:INTRADAY_RATIO_TRIM@15m': 5, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:STRUCT_BREAK_DC@15m': 5, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:SENT_STRAT_DIV@15m': 3, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:OVERBOUGHT_TAKE_PROFIT@15m': 2, 'LIVE_ONLY_NONVEC:GAP_FILL@5m': 2, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:GR_HTF_DIRECT@3m': 2, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:NOLOSS_BBH_BREAKDOWN@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@3m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:DT_TIMEOUT@5m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:ROTATION_ENTRY_L@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:CLENOW_ENTRY@15m': 1, 'LIVE_ONLY_NONVEC:UNCLASSIFIED:STRUCT_BREAK_DC@5m': 1}
- Totals: PASS 129 · FAIL 168 · sign flips (>=5 exits each side) 0 · alerts 86
