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

---

## 8. SESSION PROGRESS 2026-09-28 (user: "unlock where needed make sure switches and options are added correctly and tested for all sym_sides")

### Inventory corrections (Section 2 is stale)
- **#1 `VIGILANCE_MAX_LOSS_HARD_STOP_USER` — NO LONGER EXISTS in current code.** The fixed-% stop was
  replaced by the user's 3rd mandate ("we do not use fix %") with the structural DC4 stop (#6). Log
  hits are from not-yet-restarted procs only. Nothing to vectorize; after live restart this reason
  never fires again. Do NOT implement a fixed-% vec twin (would fabricate an exit live doesn't do).
- **#6 `VIGILANCE_DC4`** — vec twin was wired 2026-09-28 (after this handoff was written) at
  v12_quick_engine simulate_one (~22492): losing pos + dc_low4/high4_{TF} breach → close + block,
  with consec-loss streak + recovery-unblock logic. Code-inspected FAITHFUL (no arange/hash).
  Templates: GLOBAL_RISK_GATES rows exist (TF OFF/15m/1h/4h etc.).

### Done this session
- **#2 `MTF_ATR_TRAIL` faithful vec twin (P0)**: fabricated scaffolding (atr>1.0 exit_mask OR at
  ~793-806) DELETED; stateful ratcheting trail added in simulate_one via NEW
  `vec_decisions/mtf_atr_trail_exit.py` (pure core; update-then-fire order matches live).
  QuickConfig live-parity defaults: ENABLED True (config.py), MULT 2.0, TF 15m, TF_TRADIER '1h',
  NEW `MTF_ATR_TRAIL_ENABLED_TRADIER=False` (stocks live inert). Synthetic smoke: long fires at
  peak−2·ATR, short at bottom+2·ATR; ON/OFF + COMPOUND gates verified; fires on real NPZ fleet-wide.
- **config_tradier.py**: NEW MTF_EXIT_USE_COMPOUND/MTF_ATR_TRAIL_ENABLED(+_TRADIER)/TF_TRADIER/MULT
  knobs == live _cfg fallbacks (zero live change, now promotable).
- **Templates**: EXIT_VELOCITY rows added to all 4 TEMPLATE_* (crypto 12, stocks 14 rows; first
  row = live default). Synced S1+s2, md5-verified.
- **🔥 STALE-TRAIL LIVE BUG FOUND + FIXED (root cause of the 4279 MTF_ATR_TRAIL blocks/day)**:
  `mtf_compound_exit_state[position_key]` is popped ONLY when the MTF block itself closes
  (ez_manage:47791). Any other exit leaves the trail behind; the NEXT position on the same key
  inherits it → instant MTF_ATR_TRAIL close of fresh opens. LIVE-PROVEN: XLMUSDT_LONG lvl0.222954
  fired 09-25 12:17 AND 09-28 16:48 on a NEW position (grep ez_manage logs, 812 hits). Fixed in
  ez_manage.py + tradier_manage.py: state resets when position opened_at changes (per-lifecycle,
  matching the vec twin and the code's own "per-position" spec). Backups
  `backups/before_mtf_trail_state_lifecycle_fix_202609281735_*`. Compiled. **NOT yet synced to S1
  (production-deploy gate) — user must approve/rsync ez_manage.py + tradier_manage.py to S1 and
  restart live procs (restart already pending per vigilance memory).**
- **Harness**: `tools/vigilance_dc4_parity_ab.py` (VIGILANCE ON/OFF × vec/live-scalar per symside,
  vigilance-blocks file isolated) + `tools/exit_vec_ab_all_symsides.py` (3-case A/B, all 110
  live-book sym_sides, per-family fire counts) — results
  `data/reports/exit_vec_ab_all_symsides.json` on S1.

### Parity state / what blocks certification
- Vec side: VIGILANCE_DC4 + MTF_ATR_TRAIL both fire broadly across the book (fire counts per
  sym_side in the JSON). Scalar side: the live-engine backtest is POISONED by the stale-trail bug
  (positions die instantly to inherited trails → VIGILANCE_DC4 fired 0× live-side while vec fired
  61× on ADAUSDC_LONG). **Scalar parity re-run requires the fixed ez_manage.py on S1 first.**
- **Allowlist tokens (`vec_parity_gate.py` VEC_EXIT_TOKENS) NOT added** — per pipeline step 4,
  only after S1 scalar parity passes. GATE_EXITS stays False.

### Next steps (in order)
1. USER: approve rsync of fixed ez_manage.py + tradier_manage.py Mac→S1 sandbox; restart live procs.
2. Re-run `tools/vigilance_dc4_parity_ab.py` on S1 → scalar deltas should now match vec deltas for
   VIGILANCE_DC4 and MTF_ATR_TRAIL (fires>0 both engines, same bars ideally).
3. Sweep the new EXIT_VELOCITY rows via v15 (settings: TF, MULT candidates in sheets).
4. Then add VIGILANCE_DC4 + MTF_ATR_TRAIL tokens to VEC_EXIT_TOKENS; shadow-validate; only after
   ALL remaining exits (#3 DC_BREACH_REDUCE, #4 ALL_ALL_GREEN, #5 BREAK_EVEN_GUARD, #7 ALL_ALL_RED)
   have faithful twins consider GATE_EXITS=True.

## 9. SESSION PROGRESS 2026-09-28 PM-2 (user: "unlock and fix also make sure MTF_ATR_TRAIL does not
## only test fixed % but also dc_low4/high4_15m and mtf wt crosses as the exit signal")

- **MTF compound-exit branches 2-4 vectorized (crypto)** via NEW `vec_decisions/mtf_compound_exits.py`:
  MTF_DC_REJECT (outside-band arm → re-cross fire; NEW `MTF_DC_REJECT_USE_DC4` knob switches the band
  to dc_high4/dc_low4_{TF}, reason suffix `_dc4`, wired live in ez_manage branch 2 + config.py + v12),
  MTF_BB_REJECT (tag→fail, live's 60s×lookback approximation kept), MTF_GR_WT_EXIT (WT cross against
  + ≥MIN_TFS of 15m/1h/4h/D ladder). Live order preserved: trail → dc → bb → wt, before exit_sig
  (NOLOSS-bypass placement). Tradier mode deliberately UNWIRED (stocks live OFF; structural veto +
  time-based dc step unmodeled — wiring without them would overfire). QuickConfig += MTF_WT_CROSS_
  EXIT_TF='15m', MTF_DC_REJECT_USE_DC4=False. Latent UnboundLocalError in GR_HTF_DIRECT fixed
  (MTF_GR_EXIT_GATE_ENABLED=False sweep rows would have crashed compute_exit_signals).
- **Templates**: TEMPLATE_CRYPTO_{LONG,SHORT} EXIT_VELOCITY +21 rows each (DC reject on/off/TF/dc4,
  BB on/off/lookback, GR gate + WT cross on/off/TF, MIN_TFS 2/3/4).
- **Multi-session coordination now ACTIVE** (binance-28 protocol): engine cuts must be announced to
  binance-28 + binance-99 (sweep owner) with freeze window; push = ONE atomic batch (whole
  vec_decisions/ + engine), md5 + import-test both boxes. Cut history today: …17:53:56Z=4a1bc3fd
  (s1=s2), 18:01:30Z=5c3296bb (s1 only, **PROVISIONAL/SUSPECT** — binance-5d's subagent was mid-edit
  on quick_reduce/augment parity at push time; s2 HELD on 4a1bc3fd). binance-5d's coherent cut
  (~within the hour) equalizes both boxes and supersedes; re-run all vec diagnostics after it.
- **Managers**: binance-6e verified Mac ez_manage.py (72b1937c) + tradier_manage.py (6be14d2f)
  contain BOTH the stale-trail lifecycle fix AND their NEGBOOK build; user's manual rsync of current
  Mac files to s1/s2 loses nothing; live restart still pending.
- All-symsides A/B v2 (7 cases: base/vig_off/trail_off/dc_off/bb_off/wt_off/dc4_on) launched on s1
  @18:05Z against 5c3296bb — output data/reports/exit_vec_ab_all_symsides.json, PROVISIONAL.
- **7-case A/B across all 110 live-book sym_sides (s1, engine 5c3296bb, PROVISIONAL)**,
  `data/reports/exit_vec_ab_all_symsides.json` (Mac+s1): VIGILANCE_DC4 fires on 89/110 (2479
  closes, delta median −0.19), MTF_ATR_TRAIL 90/110 (2672, median 0.00/mean +0.51),
  MTF_GR_WT_EXIT 89/110 (2850, median +0.04/mean +0.95), MTF_DC_REJECT 0/110, MTF_BB_REJECT
  0/110. DC4-band mode moves results on 28/110 (better on 10). Fire-analysis vs live logs:
  · MTF_BB_REJECT = **structurally dead by design** — tag window is LOOKBACK×60 SECONDS (5 min)
    but high_1h/bb_upper_1h update hourly, so fail-after-tag can never happen inside the window.
    0 live fires ever in current logs → vec 0 is FAITHFUL parity of a dead exit. Candidate for
    a real fix (TF-scaled lookback) — needs user decision, would change live behavior.
  · MTF_DC_REJECT: live fires rarely (16 in ~4d logs + history ledgers); vec 0/110 because the
    baseline DAYTRADE dc-target exits close AT the band before price can get outside to arm the
    reject. Scalar harness post-cut will measure the true gap.
  · MTF_GR_WT_EXIT: vec fires ≫ live (~4 in 4d). Main suspected suppressor live:
    MTF_EXIT_MIN_OPEN_TS startup gate (positions opened before manager startup never get
    compound exits — frequent restarts ⇒ most live positions ride legacy exits) + pre-fix
    stale-trail closing positions first. Scalar sim sets startup=sim start, so scalar-vs-vec
    stays the right parity metric; live-vs-backtest frequency gap is orchestration, documented.
- **MTF_BB_REJECT dead-window FIXED (USER: "needs to be fixed first")**: window LOOKBACK×60s flat →
  LOOKBACK×timeframe_seconds(TF) (stocks-identical), in ez_manage live branch + vec twin + v12 call.
  Synthetic proof green. ⚠️ ACTIVATES this exit on live crypto (ON default @1h/lb5) at the
  post-rsync restart; expected fire-rate estimate comes from the post-cut A/B re-run — check it
  before/right after restart. Stocks path unchanged (already TF-scaled).
- Co-editor integrity audit (user request): all markers intact across v12/ez/tradier/config
  (binance-5d qr_cond+3-tuple, binance-6e NEGBOOK, WT_DC scorer, vigilance, 365D gate, my blocks);
  all five files compile. Nothing overwritten.
- **Manager-rsync verification plan CHANGED (binance-6e, 18:4xZ)**: pinned md5s go stale in minutes
  with 4+ concurrent Mac editors — do NOT trust any md5 written in this doc for ez_manage.py /
  tradier_manage.py. At the moment the user runs the manual rsync, verify worker_md5 ==
  Mac_md5_at_that_instant PLUS marker greps ON THE WORKER: ez_manage.py → NEGBOOK_WT_TURN,
  opened_ts, mtf_exit_timing (BB fix), MTF_DC_REJECT_USE_DC4, `_dc4`; tradier_manage.py →
  NEGBOOK_WT_TURN, opened_ts, WT_DC_DETAILED_SCORER_ENABLED. MTF_BB_REJECT is NOLOSS-bypassed BY
  DESIGN (config.py:1538 sanctioned loss-exit replacements) — its loss-closes after activation are
  intended; staging option = MTF_BB_REJECT_EXIT_ENABLED=False before restart.
- **CRYPTO CUT LINE FINAL (binance-99, binding): 18:29:48Z 2026-09-28** — all crypto pilots+driver
  restarted on the coherent s1 trio: engine 261a3dfa + vec_decisions/mtf_compound_exits 38f7d131 +
  tools/opt/evaluate_v12 dbf512b4 (trade-count split: trades == CLOSE rows; AUGMENT/REDUCE
  ledger-visible, excluded from trade metrics; gain_pct formula unchanged — independently
  reproduced by binance-99, -9.1759 both ways on ADAUSDC_LONG). STOCKS line stays 17:53:56Z /
  4a1bc3fd on s2. binance-5d's full re-cut still owed (must snapshot at push time, both boxes).
- **A/B v3 on the FINAL trio (18:29:48Z cut, 110 sym_sides, 0 errors)**, Mac+s1
  `data/reports/exit_vec_ab_all_symsides.json`: MTF_BB_REJECT now ALIVE — fires on 88/110
  (887 closes/30d, median 10 per sym_side, max 26), delta ON-OFF median 0.00 / mean +0.09,
  positive on 50/110 (best LINKUSDC_LONG +3.6, worst THETAUSDT_SHORT −7.7) → live activation
  expectation ≈1 close/3d/sym_side, aggregate ~neutral; per-sym tuning via the new EXIT_VELOCITY
  rows. VIGILANCE_DC4 88/110 (2558, mean −0.50); MTF_ATR_TRAIL 90/110 (2315, mean −0.02);
  MTF_GR_WT_EXIT 89/110 (2624, mean −0.05); MTF_DC_REJECT still 0/110 in vec (dc-target
  interplay — scalar harness will measure); USE_DC4 mode moves results on 25/110.
  These are 30d single-sym diagnostics (below sample floor — settings come from the sweeps).
