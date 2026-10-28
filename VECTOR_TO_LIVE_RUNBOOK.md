# FULL-SCRIPT PARITY RUNBOOK: back to real trading with the real scripts

Written 2026-10-28. Owner: the operator. Every number in a report here comes from broker truth or the real engine, never from a re-implementation (CLAUDE.md NO-LIES).

## 0. What is going on, and what is NOT the goal

**Right now** live money is traded by `tools/vector_direct_trader.py` on S1 (crypto) and S5 (stocks). It trades the forward vector state: open/flat plus the engine's OPEN/CLOSE/AUGMENT/REDUCE, at $15, with a `dc_15m` ±0.4% stop. That is roughly **5% of what the real trading scripts (`ez_manage.py` / `tradier_manage.py`) do**: trading for five-year-olds. It **loses money minute by minute.** It is a **band-aid**, there only so the accounts are traded at all while the real scripts are fixed.

**The vector is not the god that solves anything.** The vector engine (`v12_quick_engine`) only knows the behaviours that have been turned into template switches and vectorized functions. Everything the real scripts do that the vector doesn't model is exactly what the band-aid is missing, and exactly why the boards and live disagree.

**The goal:** the **real scripts** trade again with **complete parity** to the vector. Every behaviour of `ez_manage` / `tradier_manage` is a template switch with a vectorized twin, so the boards test the full strategy and live executes what the board decided, on the latest data. Then the backstops, takeovers and the band-aid executor can be **removed**, and the system can make money again.

So this runbook is NOT "prove the vector profitable, then switch". It is:
1. Keep the band-aid alive and as harmless as possible (§1).
2. Integrate ALL real-script behaviour into switches + vectors (§2): the main work.
3. Prove live == vector on the latest data (§3, §4).
4. Hand execution back to the real scripts, then remove the backstops (§5, §6).

---

## 1. The band-aid: keep it trading, keep the damage small

| Piece | Where |
|---|---|
| Crypto executor | S1 `tools/vector_direct_trader.py` (systemd `vector-direct-crypto.service`), states `~/binance-sandbox/data/forward_state/<SYM_SIDE>.json` |
| Stocks executor | S5, same script (`vector-direct-stocks.service`), RTH only |
| Stops | `tools/vector_direct_stops.py`: STOP_MARKET `dc_low_15m` −0.4% / `dc_high_15m` +0.4% on every OPEN/AUGMENT |
| Mac | Full scripts run in **paper**: `data/MAC_PAPER_TRADING` → `mac_paper_gate.py` refuses every order and logs it (`data/mac_paper/orders_<venue>_<day>.jsonl`). VEC_ logs keep flowing; they are the work list for §2 |
| History | Every real fill → Mac `data/history/<acct>/<SYM_SIDE>.jsonl` once, with qty, fill price, $, reason, order_id (`tools/vector_direct_history_import.py`) |
| Switch | `tools/live_mode.sh {paper\|real\|status}` |

Band-aid rules:
- **Size stays small** (`START_POSITION_SIZE` $15) until §5. Never scale up the band-aid.
- **Measure its losses honestly**, daily, from fills: net P&L after fees and funding, per venue, per account. They are the cost of the band-aid, the reason to finish §2 fast, and the baseline the real scripts must beat. They are NOT a promotion metric.
- **It must not miss a beat**: every tradeable/held sym_side gets a closed bar, an NPZ append, a state and an execution every 15m (S1/S5 bar guarantee, `data/bar_guarantee/`).
- **Kill switch**: `touch ~/binance/data/vector_direct/KILL` on S1/S5.

---

## 2. The main work: every real-script behaviour becomes a switch + a vector

**Source of the work list:** the real scripts' own logs, in paper mode, of what they WOULD do. These are VEC_ discrepancy records (`data/vector_disparity_queue.jsonl`, `VEC_DRIVEN` / `VEC_EXIT_OWED` / `VEC_LAST_REFUSE` / `POLICY_NON_VECTOR_*` lines), refused paper orders, and native decisions with no vector counterpart. Census: `tools/vec_log_census.py` → `data/reports/vec_log_fixes/STATUS.md`.

**Plus a full inventory, not just what happened to fire this week:** every decision branch in `ez_manage.process_position` / `tradier_manage` (entries, exits, augments, reduces, re-entries, hedges, stops, gates, sizing). Each one gets a row in the inventory with its state:

| State | Meaning |
|---|---|
| `SWITCH+VEC` | Template row(s) exist (bold = current live behaviour, ≥ 2 candidates, never grey for a gate switch), config field, QuickConfig field, live reader **and** vector predicate call the same `vec_decisions/*` function, fixture parity test green |
| `NEEDS_TWIN` | Live has it; vector doesn't |
| `NEEDS_SWITCH` | Hard-coded in live; no switch, no template row |
| `DEAD` | Never fires / unreachable: remove from live (operator approval) |
| `RULED` | Operator ruling recorded (e.g. RATIO_CLOSE trims always pass) |

**Rules for doing it right** (each learned the hard way):
1. **One function, two callers.** Live and vector call the same `vec_decisions/*` function (pattern: `min_hold_gate.refuse` / `refuse_bars`, `live_native_exit_twins.dc15m_evaluate`, `fixed_stop`). Never two implementations.
2. **Four surfaces**: `config` / `config_tradier`, `QuickConfig`, the live call path, and the vector predicate. Never judge wiring by `getattr` counts in `ez_manage.py`; prove it by a call.
3. **Template rows** through the template owner (binance-3f) and BACKTEST_BIBLE §56. Bold = today's live behaviour, so adding a row changes nothing until a board promotes a better option. No duplicate-value rows.
4. **Never force a new default onto existing per-sym sets.** A sym_side gets a new value only from its next 30D board (operator ruling on MIN_HOLD_BARS).
5. **Engine-change discipline**: any `QuickConfig` edit changes the AST hash the forward-state publisher verifies. Resync all hosts in the same breath, and check that ≥ 20 published sets still validate. The validator drops retired keys and never rejects a whole set. Order: validator tolerant → readers → field → template rows.
6. **Live-only gates are not allowed.** Circuit breaker, stale-set halt, vigilance, min hold and the overtrade cap are switches the board applies; live may not refuse something the board cannot see (operator ruling 2026-10-27). Sanctioned exception: RATIO_CLOSE trims (they never change vector decisions).
7. **Default-off proof** for every change: old engine == new engine on ≥ 12 crypto + ≥ 6 stock boards with the switch at its default. **Switch-on proof**: it actually books in the vector and fires identically in live.
8. **Positions truth** = Redis, with `{acct}/long_positions.json` / `short_positions.json` as backup. Nothing else (`positions_truth` was a lie).

**Done when:** the inventory has no `NEEDS_TWIN` / `NEEDS_SWITCH` rows, and the VEC_ census shows zero open kinds for 7 consecutive days with the Mac running the full scripts in paper.

---

## 3. Parity: the real scripts decide what the board decides

Measured every bar while the Mac runs in paper:

| Check | Pass |
|---|---|
| Decision diff | For every account and sym_side, the Mac's (paper) decision and the board's state agree on act, bar, direction and size rule. Zero unexplained diffs for 7 days |
| Scalar parity | `backtest_v12_engine` (calls the real `process_position`) == `v12_quick_engine` on the same frozen 30D NPZ, ≥ 20 crypto + ≥ 10 stock sym_sides (PARITY_TESTING.md) |
| Board authority | No live trade that isn't in the board's rolled-forward chart (`live_twins/board_authority.py`; memory `board_authority_not_reason_labels_20261027`) |
| Timing | The Mac decides within seconds of bar close + kline arrival, not minutes. The Mac overload (load 200–280, loops frozen up to 9 min) must be gone first |

---

## 4. Latest data, never a stale NPZ

Both sides decide on **the bar that just closed**, from identical values.

| Item | Requirement |
|---|---|
| Bar | One closed-15m bar per symbol at :00/:15/:30/:45: the mark-price bar published immediately, replaced by the native OHLCV bar when it lands; S1 builds its own if one is missing. Received ≤ 35 s for 100% of symbols |
| Vector input | The state reads zip + `.tail` (`tools/npz_merged_view.py`); `npz_tail` == the just-closed bar |
| Live input | Every field a live decision reads equals the NPZ row for that closed bar, including the previous-bar channels (`dc_low4_*_prev`, `dc_high4_*_prev`, `dc_low_15m_prev`, `dc_high_15m_prev`). Per-field diff on ≥ 5 crypto + ≥ 3 stock symbols per bar = 0. **Open:** `ez_indicators.py` / `tradier_indicators.py` don't emit the `*_prev` 4-bar channels yet (unlock given) |
| Freshness | Judged by each value's own timestamp, never file mtime |
| Forming bar | Never traded as if closed |

---

## 5. Handing execution back to the real scripts

**Preconditions** (one report, `data/reports/full_parity/switch_<date>.md`):
- [ ] §2 done: the inventory has no open rows, and the VEC_ census has been zero for 7 days
- [ ] §3 parity passed for 7 days
- [ ] §4 latest-data checks at zero diff
- [ ] The boards (now testing the FULL strategy) show positive forward results under the CLAUDE.md promotion law (`pool_sharpe` > 0.2, DD ≤ 30, sample floor); live-vs-board gap within the fee + slippage band
- [ ] Mac healthy: no LOOP_LAG > 30 s in 24 h, > 40 GB free
- [ ] The operator signs off in writing

**Steps:** `tools/live_mode.sh real` (releases S1/S5 first, then removes the Mac flag), outside the 2 minutes after a bar close. Watch the first 4 bars. Rollback: `tools/live_mode.sh paper`.

---

## 6. Removing the backstops

After the switch, S1/S5 stay as backstop: they paper-plan every bar, execute any board OPEN/CLOSE the Mac missed (`BACKSTOP_FAILURE_MAC_MISSED_ORDER`), and take an account over on any Mac fault. **Every backstop fire is a defect in the real scripts**, to be fixed in §2 style, never accepted.

Backstops come off when: zero backstop fires and zero takeovers for 14 consecutive days, with the operator's approval. From then on S1/S5 only hold failover for "Mac offline / cannot produce data".

---

## 7. Open items (2026-10-28). Never delete a row; mark it **CLOSED <date> <evidence>**

| Item | Section | Owner / status |
|---|---|---|
| Full branch inventory of `ez_manage` / `tradier_manage` not built yet | §2 | Next agent task |
| Live indicators lack `*_prev` 4-bar channels, so live vigilance can't fire | §4 | Unlock given; not implemented |
| Template rows pending: `DC15M_FORCE_EXIT_ENABLED` (all 4), `VIGILANCE_DC4_STOP_ARMED` (stocks), `IN_GAIN_TREND_EXIT_ENABLED`, phase-2 switches; duplicate rows (e.g. `VIGILANCE_CONSEC_LOSSES` CRYPTO_SHORT 115/116) | §2 | binance-3f |
| `htf_against_force_close` still reads the retired `MIN_HOLD_BARS_BEFORE_EXIT` | §2 | binance-17 |
| Live stale-set halt counts real closes; the board counts simulated closes | §3 | Open design question |
| Mac overload (load 200–280) freezes the real scripts | §3, §5 | Move non-trading load off the Mac |
| S5 stocks: first real session 2026-10-28 13:30Z | §1 | S1/S5 guarantee agent |
| States land 60–87 s after close; target seconds | §1, §4 | S1/S5 guarantee agent |
| fin / ang margin-insufficient opens (accounts fully loaded) | §1 | Operator |
| 24 old Mac history rows match no broker order | §1 | `data/vector_direct_history/shadows_latest.json` |
