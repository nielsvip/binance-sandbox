# 🚨 HANDOFF — EXIT VECTORIZATION → TRUE PARITY (priority project)

**Created:** 2026-09-28 · **Status:** OPEN, needs a dedicated session with **S1 access** and
**undivided attention**. This is a menace to the whole system: it trades **position protection**
against **backtest parity**, and getting it wrong either **strands losers** (positions bleed
un-stopped) or **breaks parity** (live does exits the backtest never modeled → fake results).
Do NOT rush. Do NOT fake vec logic. One exit end-to-end, verified on S1, before the next.

---

## 0. TL;DR / current live state (safe, but band-aided)

- **Emergency band-aid deployed & verified:** `config.py:4422 STRICT_VEC_PARITY_GATE_EXITS = False`.
  Exits are **never** parity-blocked now → stops fire, losers close. Verified 2026-09-28: 0 exit-blocks
  fleet-wide, stranded ADAUSDC closed via its DC4 stop.
- **The cost of the band-aid = the reason this project exists:** with `GATE_EXITS=False`, live fires
  exit paths the **vectorized backtest does not model** → live≠vec → **parity is broken** on exits.
- **Goal of this project:** faithfully vectorize every blocked exit path so parity holds, then
  re-enable `GATE_EXITS=True` safely (nothing gets stranded because vec now covers every live stop).
- **Alternative framing the USER rejected:** just leaving `GATE_EXITS=False` forever = unprotected
  parity gap. USER wants each exit path as a **switch**, backtested (some with multiple settings),
  vectorized, then promoted to config + live per results.

---

## 1. Root cause (two layers)

**Layer 1 — the gate stranded losers.** `STRICT_VEC_PARITY_MODE=True` (config.py:4419, set
2026-09-26 for a 20% churn fix) blocks any entry/exit whose reason is not in the allowlist
`vec_paths/vec_parity_gate.py` (`is_vec_achievable`). The allowlist (VEC_EXIT_TOKENS) lists only
R1_DC_LOW, R2_WT_VEL_SLOW, WT_*_VEL_EXIT, DC_HOPELESS, PPL_*, DELTA_EXIT, CYCLE_TP, HEDGE_CLOSE_WT,
IN_GAIN_TREND_EXIT, RIDICULOUS_*, SENTIMENT_CUT_GAIN, HLR_TOP_EXIT. It **omits every real stop-loss**,
so live loss-exits were BLOCKED and positions bled (ADAUSDC -3.97%→-4.78%). The gate check is in
`ez_manage.py:~29912-29932` (execute_now); it returns `BLOCKED_VEC_PARITY_*`.

**Layer 2 — v12_quick's exit "coverage" is FABRICATED.** Even where grep shows a family in
`v12_quick_engine.py`, the logic is scaffolding, not real: MTF_ATR_TRAIL "vec" (~793-825) = `atr>1.0`
OR'd into exit_mask + `arange(n)%12==0` flip + "fire on all bars if a threshold ≠ default". **1073
such fabrication markers** in the file. So you CANNOT just add tokens to the allowlist — you must
REPLACE the scaffolding with faithful vec logic and prove it on S1. (See memory
`v12_quick_engine_synthetic_distinctness`.)

---

## 2. COMPLETE inventory of blocked exit paths (what needs a switch + vec twin)

Counts = times STRICT_VEC_PARITY blocked it (all accounts, all history, pre-band-aid).
v12 status from grep + code inspection 2026-09-28.

| # | exit path (live reason prefix) | blocks | v12_quick status | live emit site | priority |
|---|---|---|---|---|---|
| 1 | `VIGILANCE_MAX_LOSS_HARD_STOP_USER` | 6376 | **MISSING (0)** | grep ez_positions_quick.py + ez_manage.py | 🔴 P0 (main stranding culprit; simplest to vectorize: exit when cum-gain < −MAX_LOSS%) |
| 2 | `MTF_ATR_TRAIL_{TF}_x{mult}` | 4279 | **FAKE scaffolding** | `ez_manage.py:47691,47697` | 🔴 P0 |
| 3 | `DC_BREACH_REDUCE_UNHEDGED_LOW/HIGH` | 615 | **FAKE (~544,6793)** | grep `DC_BREACH_REDUCE` emit | 🟠 P1 |
| 4 | `ALL_ALL_GREEN_DIRECT_CLOSE` | 387 | **MISSING (0)** | grep ez_manage.py/ez_positions_quick.py | 🟠 P1 (winner close) |
| 5 | `BREAK_EVEN_GUARD_EXIT` | 293 | **MISSING (0)** | `ez_manage.py:51657` | 🟠 P1 |
| 6 | `VIGILANCE_DC4_{TF}_STOP` | 232 | **FAKE (3 refs)** | `ez_manage.py:46457-46458` | 🔴 P0 (DC4 breach stop) |
| 7 | `ALL_ALL_RED_DIRECT_CLOSE` | 1 | **MISSING (0)** | pair of #4 | 🟢 P2 (loser close) |
| — | `RATIO_CLOSE_*` | 4 | n/a (hedge/ratio) | ratio rebalance | 🟢 P2 (may be VEC_UNSUPPORTED) |

**Already handled this session (NOT part of this project):** `MOMENTUM_WATCHDOG_*` (illegal opens —
CUT via `_NON_VEC_KNOBS_EZ`), `STOP_FUNCTIONS_KILL` (DELETED — v15_pilot never-positive). Do not
resurrect these.

**Already vec-achievable (in allowlist, leave alone):** R1_DC_LOW, R2_WT_VEL_SLOW, WT_*_VEL_EXIT,
DC_HOPELESS, PPL_TP/SL, DELTA_EXIT_speed, CYCLE_TP, IN_GAIN_TREND_EXIT, RIDICULOUS_*, HLR_TOP_EXIT,
SENTIMENT_CUT_GAIN, HEDGE_CLOSE_WT.

---

## 3. The pipeline — do this PER EXIT, one end-to-end before the next

1. **TEMPLATE switch.** Add the exit as a switch row in `SPREADSHEETS/TEMPLATE_CRYPTO_{LONG,SHORT}.xlsx`,
   `TEMPLATE_STOCKS_{LONG,SHORT}.xlsx`, `TEMPLATE.xlsx` — in the `EXIT_STRUCTURAL` sheet (hard stops /
   DC / structural) or `EXIT_VELOCITY` sheet (ATR/velocity trails). Column A = switch name (the
   `*_ENABLED`/`*_TF` knob), B = baseline default, C = per-sym override. For settings that need
   testing (TF: OFF/D/4h/1h/15m/3m; thresholds), add candidate values as the L:BI yellow filters for
   that row (see CLAUDE.md "TEMPLATE SHEETS" §). "some need to test settings" = e.g. MAX_LOSS %
   {2,3,4,5}, ATR mult {1.5,2.0,2.5}, DC4 TF {15m,1h}.
2. **Faithful vec twin.** In `v12_quick_engine.py`, REPLACE the scaffolding for that family with real
   vectorized logic mirroring the live emit (Section 2 sites). NO `arange`/hash/`atr>1.0`/threshold-≠-
   default fabrications. Examples of faithful logic:
   - MAX_LOSS_HARD_STOP: `exit_mask |= (cum_gain_pct < -MAX_LOSS_PCT)`.
   - VIGILANCE_DC4: long `exit_mask |= (close < dc_low4_{TF}) & losing`; short mirror on dc_high4.
   - MTF_ATR_TRAIL: ratcheting trail = running max(entry, close-mult*atr); exit when close < trail.
   - DC_BREACH_REDUCE: augmented-position close < dc_low_{TF} (long).
   - BREAK_EVEN_GUARD: after gain≥X, exit if price falls back through entry (breakeven).
   - ALL_ALL_GREEN/RED: exit when all TFs (3m/15m/1h/4h/D) align against/for — count TFs, threshold.
3. **Backtest + verify (S1 ONLY).** Sync Mac→S1 (`rsync` per CLAUDE.md INFRASTRUCTURE §; md5-verify),
   run `v15_pilot.py` on S1 to sweep the switch's settings on `backtest_v8/indicators/*.npz`, and
   assert **`v12_quick_engine` == `backtest_v12_engine`** (live-faithful scalar) on the SAME frozen
   30d NPZ (parity harness — see PARITY_TESTING.md). Mac returns 0 trades (no crypto NPZ) — cannot
   verify here. A non-zero delta means nothing until you confirm it's real signal, not scaffolding.
4. **Promote per results.** Winning setting → `config.py` default (respect STATE OF AFFAIRS locks).
   Add the exit's token to `vec_paths/vec_parity_gate.py` `VEC_EXIT_TOKENS` **only after** S1 parity
   passes. Live already emits these — no live emit change needed.
5. **Re-enable the gate LAST.** Once ALL 7 have faithful vec twins + allowlist tokens + S1 parity,
   flip `STRICT_VEC_PARITY_GATE_EXITS = True`. Verify no exit gets blocked that should fire (shadow
   first: `STRICT_VEC_PARITY_SHADOW=True, MODE=False` logs would-blocks without blocking).

---

## 4. ⚠️ DANGER / guardrails (read before touching anything)

- **NEVER re-enable `GATE_EXITS=True` until every live stop-loss is genuinely vec-covered.** Doing so
  re-strands losers (the exact bug just fixed). The sacred rule (CLAUDE.md): WT/DC technical exits
  MUST be able to close losers; never leave a losing position unprotected.
- **NEVER add an exit token to the allowlist while its v12 logic is scaffolding.** That fabricates
  parity (the NO-LIES cardinal sin — "lying numbers wiped out half the net worth").
- **v12_quick / backtest_v12 verification is S1-only.** No Mac shortcut. Do not claim parity from Mac.
- **v12_quick_engine.py + tools/hooks/persistent_v12_hooks.py were `chflags uchg` locked** (LOCKED_FILES
  2026-09-04, "vec-identical hook"); currently the uchg flag is OFF (editable) but the file is still
  edit-LOCKED — get explicit user unlock, and the `ensure_vec_identical()` guard rejects inline
  predicates without a `vec_decisions/` module, so add faithful logic via that pattern.
- **STRICT_VEC_PARITY_SHADOW mode** (config.py:4420) is the safe way to validate the allowlist before
  flipping MODE: it LOGS every would-block without blocking.
- Backup every file before edit (`cp <f> backups/before_<desc>_<YYYYMMDDHHMM>.py`); compile-check;
  never revert live scripts to older versions.

---

## 5. Acceptance criteria ("done")

1. All 7 exit paths (Section 2) exist as switch rows in the TEMPLATE_* EXIT sheets, with test settings
   where noted.
2. Each has a FAITHFUL vec twin in v12_quick_engine.py (no scaffolding markers).
3. Each is S1-verified: v12_quick delta reproduces backtest_v12_engine on the same frozen NPZ (parity
   harness green), sweep results recorded in `SPREADSHEETS/V15_V16_CELL_BY_CELL/` + lifecycle_pilot JSON.
4. Winning settings promoted to config.py defaults; tokens added to vec_parity_gate.py allowlist.
5. `STRICT_VEC_PARITY_GATE_EXITS = True` re-enabled; shadow-validated; no legit loss-exit blocked.
6. Live forward-test confirms losers still close AND live exits now match vec (parity holds).

---

## 6. Context: what else this session changed (all deployed, backed up)

- `config.py:4422` GATE_EXITS=False (this fix). Backup `backups/before_vec_parity_exit_strand_fix_*.py`.
- `ez_manage.py` `_NON_VEC_KNOBS_EZ` += MOMENTUM_SMA_WATCHDOG_ENABLED, WATCHDOG_DC_FORCE_OPEN_ENABLED
  (cut 837 illegal MOMENTUM_WATCHDOG opens). Backup `before_watchdog_openleak_mask_*.py`.
- `ez_manage.py:~51792` STOP_FUNCTIONS_KILL deleted (gated to knob; v15_pilot never-positive).
  Backup `before_delete_stop_functions_kill_*.py`.
- `ez_manage.py` execute_now top: EXIT-ENGINE PARITY instrumentation (module="exit_engine" rows in
  `data/live_vs_vec_compare.jsonl`; `EXIT_ENGINE_PARITY_LOG_ENABLED`). Monitor: launchd
  `com.binance.exit-parity-monitor` → `data/reports/exit_engine_parity_monitor_latest.md`.
- `ez_positions_service.py`: IP-ban guard in fetch_positions (~6516) + WS-dead REST throttle (~6508,
  `WS_DEAD_REST_POLL_INTERVAL_S=15`). Fixed the -1003 ban cascade (8h+ ban-free).
- `ez_positions_service.py:start_account_monitors` flz-abort fix (partial WS fix — WS still doesn't
  start; see separate "A" task: the in-process WS launcher isn't invoked / bootstrap race).
- Related docs: `data/history/exit_vectorization_parity_plan_20260928.md`,
  `data/history/ez_crypto_parity_audit_20260928.md`. Memories: `ez_crypto_parity_harness_gap`,
  `v12_quick_engine_synthetic_distinctness`.

## 6b. SEPARATE OPEN TASK — "A": in-process user-data WS never starts (deep, focused)

**Symptom:** WS is dead on all 5 crypto accounts (`ws_alive=False` forever) → REST force-fetch
fallback (throttled to 15s now, ban-safe). Whole-log: **0× "Starting WebSocket manager for real-time"
ever**, 280× "Account monitors disabled".
**Architecture:** two WS paths — (a) in-process `WebSocketManager.start()` launched by
`ez_positions_service.py:start_account_monitors` (call sites 10785 in `initialize`, 13918 in
`bootstrap` redis-branch), and (b) dedicated `ez_positions_realtime.py` workers spawned by
`ez_positions_watchdog.py` **only when REST data goes stale >30s** (on-demand backup). With the
15s throttle, staleness rarely triggers → realtime workers rarely spawn → REST-primary.
**Fixed so far (2026-09-28):** `start_account_monitors` used to `return` (abort ALL monitors) on
`account_key == 'flz'` or transiently-empty accounts — removed (backup
`before_ws_start_flz_abort_fix_*.py`, deployed to fin only). Necessary but INSUFFICIENT.
**Remaining root cause (unresolved):** even post-fix, `start_account_monitors` logs nothing on a fresh
fin — not the abort, not WS-create, not an exception. So it's **not invoked / not completing**: the
invocation guard `if enable_auto_fetch and _should_enable_account_monitors()` (=`bool(self.accounts)`)
likely fails due to account-load timing (race), OR it runs but skips the WS-create loop (5587
`if account_key not in self.websocket_managers`) silently. Redis is UP (ruled out). Needs async
bootstrap tracing: confirm `self.accounts` is populated at the 13918/10785 guard; if it's a race, call
`start_account_monitors` after `_loading_complete_event` (idempotently) instead of at bootstrap.
**Not urgent:** system is stable on throttled REST (8h+ ban-free, positions <15s fresh). Fix the WS
for real-time updates only; do it in a focused session, canary-per-account, with rollback ready.
NOTE: the in-process WS may also just not deliver (comment ez_positions_service.py:2480 says the
user-data WS dying "may be a Binance-side or account-level issue") — verify it actually receives
frames before declaring it fixed.

## 7. Quick-start commands for the next session

```bash
# see what's still being blocked / firing now
grep -h "STRICT_VEC_PARITY.*BLOCKED" /Users/niels/logs/ez_manage_*.log | grep -oE "reason=[A-Z_0-9]+" | sort | uniq -c | sort -rn
# locate a live emit to mirror
grep -nE "VIGILANCE_MAX_LOSS_HARD_STOP|ALL_ALL_GREEN_DIRECT_CLOSE" ez_manage.py ez_positions_quick.py
# confirm v12 scaffolding for a family
grep -nE "MTF_ATR_TRAIL|arange\(n\) %|_atr > 1\.0" v12_quick_engine.py
# S1 (verification): rsync per CLAUDE.md INFRASTRUCTURE §, then run v15_pilot + parity harness there
```
