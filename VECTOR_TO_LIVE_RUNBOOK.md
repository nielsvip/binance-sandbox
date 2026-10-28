# VECTOR → LIVE RUNBOOK

How to prove the vector trading system profitable, how to verify it matches the full live scripts (`ez_manage` / `tradier_manage`), and how to hand execution back to the Mac with **zero discrepancies** and **always the latest data, never a stale NPZ**.

Written 2026-10-28. Owner: the operator. Every number in a verification report must come from broker truth or from the real engine, never from a re-implementation (CLAUDE.md NO-LIES).

---

## 0. Current state (2026-10-28)

| Piece | Where | What it does |
|---|---|---|
| **Execution, crypto** | S1 `tools/vector_direct_trader.py` (systemd `vector-direct-crypto.service`) | Trades each account toward the rolling-forward per-sym state in `~/binance-sandbox/data/forward_state/<SYM_SIDE>.json`: OPEN / CLOSE / AUGMENT / REDUCE at the bar and the engine's qty ratio, `START_POSITION_SIZE` $15 |
| **Execution, stocks** | S5, same script (`vector-direct-stocks.service`) | Same rules, RTH only. One whole share when $15 is less than a share (≤ $500/share) |
| **Protective stops** | `tools/vector_direct_stops.py` | STOP_MARKET at `dc_low_15m` −0.4% (shorts `dc_high_15m` +0.4%) on every OPEN/AUGMENT; cancelled on CLOSE |
| **State producer** | S1 `tools/fwdstate_npz_watch.py` → `tools/v15_forward_state.py` | Replays the published `per_sym_active` set (S1 `~/binance/data/hourly_reconfig/per_sym_store.db`) through `v12_quick_engine` to the latest closed bar |
| **Bars** | Mac `closed_15m_feed` → S1 `data/closed_15m_bars/`; fallback S1 `tools/s1_closed_bar_fallback.py` | Closed 15m bars reach S1 at p50 2–5 s |
| **Mac** | `data/MAC_PAPER_TRADING` → `mac_paper_gate.py` | Full live scripts run, every order is refused at the broker layer and logged to `data/mac_paper/orders_<venue>_<day>.jsonl`. VEC_ logs keep flowing |
| **Backstop / takeover** | `vector_direct_trader.py` | While the Mac trades: paper-plans every bar (`paper_<venue>_<day>.jsonl`), alerts and executes any board OPEN/CLOSE the Mac missed (`BACKSTOP_FAILURE_MAC_MISSED_ORDER`), takes an account over on any Mac data/trading fault |
| **History** | `tools/vector_direct_history_import.py` (Mac, launchd, 30 s) | Every real S1/S5 fill → Mac `data/history/<acct>/<SYM_SIDE>.jsonl` once, with qty, fill price, $ value, reason, order_id |
| **The switch** | `tools/live_mode.sh {paper\|real\|status}` | `real` releases S1/S5 first, then removes the Mac flag (never two executors, never none) |

---

## 1. Gate A: the vector system is profitable

**Source of truth: broker fills only** (Mac `data/history/**` rows with `prov.src` `S1_VECTOR_DIRECT` / `S5_VECTOR_DIRECT` and an `order_id`; cross-check against the broker trade list). Fees included (`fee_model.py`, one fee everywhere). Funding included for crypto.

Metrics go through `metrics_guard.write_sharpe_row()`. No annualisation, no bare "Sharpe", per-trade return list attached.

| Metric | How | PROPOSED threshold (operator decides) |
|---|---|---|
| Net P&L after fees and funding, per venue | Sum of realised + marked open P&L from fills | > 0 over the window |
| `pool_sharpe`, `sym_sharpe` | From the per-trade return list | `pool_sharpe` > 0.2 (CLAUDE.md promotion gate) |
| `avg_gain_trade`, `max_dd_pct`, trades, n_syms | Standard CSV columns (`data/sweep_results` schema) | `max_dd_pct` ≤ 30 |
| Window | Calendar days of live vector execution | ≥ 14 days crypto, ≥ 10 RTH sessions stocks |
| Sample | Closed round trips | ≥ 200 crypto, ≥ 100 stocks |
| Live vs board | Live P&L per sym_side vs the board's forward expectation for the same bars | Live within the fee + slippage band of the board |

Below the sample threshold the result is `[DIAGNOSTIC ONLY]` and Gate A is not passed.

**Known drag to measure explicitly** (memory `live_loses_while_boards_win_20261027`): fees were 44% of the bleed, and the greedy in-sample lift inflates board gains. Report gross edge, fees, funding and slippage separately.

Report file: `data/reports/vector_to_live/gate_a_<date>.md` (+ the CSV row via `metrics_guard`).

---

## 2. Gate B: the vector executor itself trades the state exactly

Before comparing with the Mac, prove the vector executor does what the state says. Per bar, per account, per sym_side:

| Check | Pass condition |
|---|---|
| Coverage | Every tradeable key and every held position has an ok state for the last closed bar (target 100%; S1 measured 135/135 since 2026-10-27 22:45Z) |
| Settings provenance | `settings_sha256` in the state == sha256 of `per_sym_active.full_config_json` at that time; `engine_ast_sha256` == the fleet engine |
| Data freshness | `npz_tail` == the last closed bar (the state includes the just-closed bar) |
| Action match | Executed act == state act (OPEN/CLOSE/AUGMENT/REDUCE), same bar |
| Size match | Executed qty == `START_POSITION_SIZE` × engine ratio, within exchange step/min-notional rounding |
| Latency | Bar close → order sent: p95 ≤ 120 s now; target "within seconds" |
| Stops | Every OPEN/AUGMENT has exactly one resting stop at the formula level |
| Exceptions | Every miss is classified: blocked (margin / leverage / min-notional), stale state, no information, ERROR. Zero unexplained |

Tooling: the S1/S5 per-bar guarantee ledger `data/bar_guarantee/<day>.jsonl` and the exactness audit (built 2026-10-28). Pass when 7 consecutive days show 100% coverage and zero unexplained mismatches.

---

## 3. Gate C: the full live scripts match the vector (zero discrepancies)

The Mac runs the full scripts in paper mode on the same accounts. Compare **what the Mac would have sent** (`data/mac_paper/orders_*.jsonl`, plus `ez_manage` / `tradier_manage` decision logs) with **what the vector executor did** (S1/S5 ledgers + fills), bar by bar.

1. **Decision diff, every bar.** For each account and sym_side: same act, same bar (Mac decisions within the same 15m bar as the state), same direction, same size rule. Classify each difference with the VEC_ discrepancy kinds.
2. **VEC_ census** (`tools/vec_log_census.py` → `data/reports/vec_log_fixes/STATUS.md`) must show **zero open kinds** for 7 consecutive days. Every live-only behaviour is either:
   - a switch with a TEMPLATE row (bold = current live behaviour, ≥ 2 candidates, never grey for gate switches) **and** a vector twin, wired on all 4 surfaces: `config` / `config_tradier`, `QuickConfig`, the live call path (including `vec_decisions/*`), and the vector predicate; or
   - removed from live.

   Never judge wiring by `getattr` counts in `ez_manage.py`. Prove it by calling the shared function.
3. **One function, two callers.** Every rule that exists in both live and vector calls the same `vec_decisions/*` function (pattern: `min_hold_gate.refuse` / `refuse_bars`, `live_native_exit_twins.dc15m_evaluate`, `fixed_stop`). Fixture tests per rule: live predicate == vec predicate on the same bars.
4. **Scalar parity.** `backtest_v12_engine` (calls the real `process_position`) == `v12_quick_engine` on the same frozen 30D NPZ for a sample of ≥ 20 crypto and ≥ 10 stock sym_sides (PARITY_TESTING.md).
5. **Gates that only live knows** (circuit breaker, stale-set halt, vigilance, min hold, overtrade cap) are vector switches the board applies (operator ruling 2026-10-27). Live may not add a refusal the board cannot see. RATIO_CLOSE trims are the one sanctioned exception: they never change vector decisions.
6. **Positions truth** = Redis, with `{acct}/long_positions.json` / `short_positions.json` as backup. Nothing else.

Pass when: zero unexplained decision diffs for 7 consecutive days on all accounts, VEC_ census empty, all parity suites green (pre-existing failures listed and owned).

---

## 4. Gate D: latest data, never a stale NPZ

The requirement: both the vector and live decide on **the bar that just closed**, from the same values.

| Item | Requirement | Check |
|---|---|---|
| Bar source | One closed-15m bar per symbol per bar: the Mac mark-price bar at :00/:15/:30/:45, replaced by the native OHLCV bar when it lands; S1 builds its own when one is missing | Guarantee ledger: received for 100% of symbols by +35 s |
| Vector input | The state reads zip + `.tail` (`tools/npz_merged_view.py`); `npz_tail` == the just-closed bar | Coverage table above |
| Live input | The live indicators for a closed bar must equal the NPZ row for that bar, field for field, for every field a decision reads (including the `*_prev` channels: `dc_low4_*_prev`, `dc_high4_*_prev`, `dc_low_15m_prev`, `dc_high_15m_prev`) | Per-field diff live snapshot vs NPZ row on ≥ 5 crypto + ≥ 3 stock symbols each bar; zero diff. **Open 2026-10-28:** `ez_indicators.py` / `tradier_indicators.py` do not emit the `*_prev` 4-bar channels (unlock given, not yet implemented) |
| Value freshness | Every freshness check uses the per-symbol value timestamp, never the file mtime | `live_failover_common` + `mac_failover_agent` (fixed 2026-10-27) |
| Forming bar | Decisions happen on closed bars only; the forming bar is never traded as if closed (memory `live_trades_the_forming_15m_bar_20261027`) | Decision timestamps vs bar close |

---

## 5. Switching execution back to the Mac

**Preconditions, all true and written into one report** (`data/reports/vector_to_live/switch_<date>.md`):
- [ ] Gates A, B, C and D passed (links to their reports)
- [ ] The operator has signed off in writing (a chat message quoting this runbook)
- [ ] Mac load is healthy enough that engine loops don't freeze (no LOOP_LAG > 30 s in the last 24 h), and disk is > 40 GB free
- [ ] `tools/live_mode.sh status` shows S1/S5 armed and owning their accounts; the Mac is PAPER; Mac engines all show `data/mac_paper/installed/<pid>.json` with `binance: true` and STARTUP COMPLETE
- [ ] Not within 2 minutes of a 15m bar close; for stocks, outside RTH or at least not during the open

**Steps:**
1. `tools/live_mode.sh real`. It releases S1/S5 (`release_<venue>.json`), waits until they own nothing, then removes `data/MAC_PAPER_TRADING`.
2. Within 60 s: the Mac engines' next orders reach the broker (no `MAC_PAPER_TRADING` rejections in the logs); S1/S5 `status_<venue>.json` shows `active_accounts: []` and paper-plans again.
3. For the first 4 bars: watch the S1/S5 paper ledgers and `ALERTS.jsonl` for `BACKSTOP_FAILURE_MAC_MISSED_ORDER`. Any backstop fire is a discrepancy: investigate it the same day.
4. History: Mac fills appear once in `data/history` with `order_id` (positions writers record it since 2026-10-28).

**Rollback, immediate:** `tools/live_mode.sh paper`. S1/S5 are pinned and confirm ownership before the Mac flag is written back.

**Automatic protection after the switch** (stays on): S1/S5 take an account over the moment the Mac misses a heartbeat (> 90 s), reports a health fault, or misses a board OPEN/CLOSE past the grace. They hand it back after 10 healthy minutes (hold cap 1 h).

---

## 6. Open items blocking the gates (as of 2026-10-28)

| Item | Gate | Owner / status |
|---|---|---|
| Live indicators lack `*_prev` 4-bar channels, so live vigilance can't fire | C, D | Unlock given; not implemented (agent stopped to keep focus on servers) |
| S5 stocks never traded real orders yet; first session 2026-10-28 13:30Z | B | S1/S5 guarantee + exactness agent |
| States land 60–87 s after close; target is seconds | B | Same agent |
| Template rows pending install: `DC15M_FORCE_EXIT_ENABLED` (all 4), `VIGILANCE_DC4_STOP_ARMED` (stocks), `IN_GAIN_TREND_EXIT_ENABLED`, phase-2 switches; duplicate-value rows (e.g. `VIGILANCE_CONSEC_LOSSES` CRYPTO_SHORT 115/116) | C | binance-3f |
| `htf_against_force_close` still reads the retired `MIN_HOLD_BARS_BEFORE_EXIT` | C | binance-17 |
| Live stale-set halt counts real closes; the board counts simulated closes | C | Open design question |
| 24 old Mac history rows match no broker order (pre-paper netting) | A | Documented in `data/vector_direct_history/shadows_latest.json` |
| fin / ang margin-insufficient opens (accounts fully loaded) | A, B | Operator: capital per account |
| Mac overload (load 200–280) froze engines | Switch | Move non-trading load off the Mac before switching |

Update this table as items close. Never delete a row: mark it **CLOSED <date> <evidence>**.
