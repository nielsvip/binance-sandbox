# BACKTEST_BIBLE ARCHIVE — superseded sections moved verbatim 2026-10-13

Never resurrect these as law; see BACKTEST_BIBLE.md §0.

## 26. SESSION LEARNINGS LEDGER — 2026-09-29 (operator-corrected)

Facts established this session, kept so they are not re-derived:

1. Baseline delta+baseline accumulation math is **correct**; integrity (determinism + idempotency) **passes**. The greedy rule is as in §14.
2. The correct baseline is **daytrade-on with DC-channel exits** (§15); daytrade was **not** to be turned off.
3. Simple and big systems share **one engine** (§16); `ENTRY_DC_TF`/`ENTRY_DC_BUFFER_PCT` are wired in the engine and are a candidate template-coverage add for the big system.
4. `QuickConfig` was **out of parity** with live config (~652 crypto / ~365 tradier strategy-field mismatches); a **blind full-sync zeroes the backtest** (live-only entry engines) — sync must be curated (§17). (QuickConfig parity remediation was assigned to a separate agent on 2026-09-29; do not double-edit.)
5. The GDX_LONG delta profile at 16:05 == the profile at 00:15 (§20) — **no regression** across the handoff window; "cells filled" = cells written, delta content mostly 0 for that symbol under the current baseline.
6. Read-only `getattr` "wiring" scaffolding (`_batchN_template_wiring`) was removed as fake-audit; it must **not** be reintroduced (§19).
7. Timing is within budget (~0.07s cached; ~1.7% of evals >0.1s, max 0.23s) — no per-cell perf regression at present (§22).
8. When many cells read 0, the investigation order is parity → candidate==effective-baseline → symbol-binding → genuine gap — reported per-cause from the delta-log, never as a blanket "unwired" count (§18).

---

## 34. DAEMON LAYER — RETIRED 2026-10-06 (SCHEDULER ERA)

The herd/sentinel/finisher/assure/redflag daemons are **retired**: none run on the fleet (verified 2026-10-07 — absent from S1 crontab and process list), `tools/v15_local_herd.py` is deleted. Do not revive them; do not cite their logs/flags as live mechanisms.

What replaced each (see §74):

- **Launch/queue/reap** → `tools/v15_fleet_scheduler.py` (S1 cron `*/2`): slots per host, memory guard, OOM/hardcap/stall reaps, `done30` tracking. Log `/tmp/v15_fleet_sched.log` (JSONL per tick).
- **Publish** → in-pilot DONE stage (§32).
- **Refill** → `tools/v15_refill_from_json.py` on demand (JSON truth → XLS).
- **Idle/stuck pilots** → scheduler stall reap (`stall_min`) + OOM reap.

**Never kill an in-flight pilot to make room** — it loses that sheet's compute. Add load only where there is headroom; a RAM-starved box must not get another max-worker pilot.

---

## 44. 365D ROBUSTNESS

- After the 30D greedy+hustle, prepare 365D via `prepare_batch(sym, 365)` and evaluate the winning set vs baseline (`evaluate_prepared_sanitized`) + scalar parity.
- Overfit guards: `365D delta < 50% of 30D delta` → warn; `trades < 30` → diagnostic-only; DD/sharpe gates.
- Live crypto opens are additionally gated by `data/confirmed_365d.json` (365D, gain>0, ≥30 trades, ≤30d fresh) via `tools/confirm_365d.py` — never weaken this certifier.
- 365D artifacts: `*_365d_matrix.xlsx` with `bh/gain` in filename + `_BASELINE_METRICS` 365D rows + `365D_REAL_ZOOMABLE` chart. No promotion without positive 365D gain unless 30D positive or beats B&H.
- **A negative or invalid 365D result is NOT a disqualification — it is a diagnosis that the 30D sheet is faulty (usually: no exits kept). It triggers the §58 repair loop; the sym_side is only promoted once BOTH 30D and 365D are valid and positive.**

---

## 55. SESSION TIMELINE — WHAT WAS ACTUALLY ESTABLISHED (2026-09-29)

Kept so the next agent does not re-run the same 30 diagnostics:

- Mac and S1 ran identical engine `bd804da7` (Sep 28 20:14). Determinism + idempotency PASS (§21).
- GDX_LONG (tradier baseline): gain +5.28, 210 trades, ~71% win; AXTI_SHORT +1.62; the DC-channel daytrade path closes frequently on this data (short holds), which is the strategy, not a bug.
- Delta-log audit (§20/§30): GDX_LONG 16:05 real run and 00:15 isolated re-run were statistically identical (~2% naked non-zero, same ~11 mover switches, ~17 distinct gains). **No handoff-window regression.**
- The 20:04 engine autosave removed `_batch3_template_wiring` (pure `getattr` no-op scaffolding) + some `_b4_*` unused locals — correct fake-audit cleanup (§19); it changed no trade behavior (the reads were inert), only the appearance of "used" switches.
- Config parity gap measured (§17): ~652 crypto / ~365 tradier strategy mismatches; blind full-sync → 0 trades (live-only entry engines). Curated sync required; remediation assigned to a separate agent.
- vec_decisions gate families (`filter_tf_gates`, `wave4_families`, `generic_filter_tf`) execute and return real masks — no swallowed-exception cascade (§25).
- Timing within budget (~0.07s cached; ~1.7% >0.1s, max 0.23s) (§22).
- No engine/config file was edited by this session (one safety backup made; all diagnostics read-only; one isolated `/tmp/proof_gdx` proof run that did not touch the herd).

---

## 58. NEGATIVE 365D = FAULTY 30D SHEET → EXIT/TRADE REPAIR LOOP (USER 2026-09-29, AUTHORITATIVE)

**Rule (operator, verbatim intent):** "−37.81%, in a position 96% of the time means it never tested any exits … few trades → add exits … if a sym_side is going up for 30D, exits will not give a pos delta but 365D will punish that — that is why we need it. Find best possible settings with more exits and more trades and rerun that on 365D — repeat until both are positive." A negative/invalid 365D is **never** a reason to discard a sym_side; it explains why its 30D sheet is wrong right now.

**Why it happens.** The greedy 30D sheet only keeps rows with a positive delta vs the current cumulative. On a 30D window that trends in the side's favour, every exit row cuts a winner short → negative 30D delta → never promoted. The winning set then holds one position (SOLUSDC_LONG: 30D +15%, 365D **−37.81% with 7 trades, TIM 96.4%**). 365D exposes it. The adaptive mandate (HANDOVER_SWEEP_20260929) says the same thing from the other side: *too few trades → add entries; too many trades / low win rate → add filters; TIM too high → add exits.*

**Hard gates in BOTH evaluators (fixed 2026-09-29).** `tools/opt/v12_pilot.evaluate_prepared_sanitized` (what every sheet row uses) now applies the same vomit gates as `lifecycle_pilot.evaluate_month` (the 365D verifier): **TIM > 80% or DD > 30% ⇒ `valid=False`** (`"TIM x% >80% (vomit)"`), plus the window floor (≥10 trades 30D, ≥30 trades 365D). Before this, sheets could promote hold-forever sets that only the 365D check rejected. A baseline that fails these gates is not credible → the pilot's credible-baseline stage (`_credible_baseline`, §56-adjacent; `[ADAPT-*]` log lines) adapts it before the sheet is filled.

**The loop — `tools/v15_365_repair.py --progress <SS>_v14_progress.json --template TEMPLATE_{CAT}_{SIDE}.xlsx`:**
1. Start from the sheet's 30D winning set (`cumulative_overrides`); prepare the 30D **and** the 365D NPZ slice once (forked workers share them).
2. Each step: evaluate every live-wired (non-grey, vector-wired, promotable, de-duplicated) row of `EXIT_STRUCTURAL, EXIT_VELOCITY, REDUCE_PROFIT_LOCK, REDUCE_SIGNAL_RATER, REENTRY_WINDOWED, REENTRY_ADAPTIVE` (+ `ENTRY_*` while trades < 30) on BOTH windows; apply the single row with the best joint score = (windows valid AND positive, windows valid, worst-window gain, 365D gain).
3. Stop when **30D and 365D are both valid (TIM ≤ 80, DD ≤ 30, trades ≥ floor) and positive**, or no row improves, or `--max-steps` (8).
4. Output `{SS}_365_repair.json` (start, every applied step with both windows' metrics, final set).
5. **Re-run the 30D sheet from the repaired set:** `V15_START_OVERRIDES=<SS>_365_repair.json V15_FRESH_RUN=1 v15_pilot.py --sym-side <SS> …` (the repaired set becomes the sheet baseline; the sheet can only climb from it).
6. Re-verify the new sheet's final set on 365D. If 365D is negative/invalid again → back to step 1 with the new sheet. **Repeat until both are positive.** Only then is the sym_side promotable (§51); until then it stays switched off in live (acc_gain_pct ≤ 0 / `_NEG_BLOCK`).

**Proof (2026-09-29, engine fd93da9a):** SOLUSDC_LONG start 30D +7.93 (11 tr) / 365D −37.81 (7 tr, TIM 96.4, invalid) → 5 steps, all exit/reentry rows: `MTF_EXIT_USE_COMPOUND=True`, `OI_CONFIRM_MIN_CHANGE_PCT=0`, `REENTRY_ENTRY_FILTER_ENABLED=True`, `REENTRY_FILTER_MIN_PASS=2`, `MIN_HOLD_BARS_BEFORE_EXIT=32` → **30D +9.36 (37 tr, TIM 40.4) / 365D +10.45 (714 tr, TIM 69.4), both valid**. The 30D sheet is being re-run from that set.

**Trade floors (USER 2026-09-29, hard):** every live sym_side must trade **≥ 10 trades per 30D AND ≥ 80 trades per 365D**. Too few trades (incl. 0) is repaired immediately — soften filters (bool FILTER/GATE/BLOCK/REQUIRE/CONFIRM/VETO/GUARD switches → False) and open entry + reentry paths (ENTRY_*/REENTRY_* rows, `*_ENABLED` entry switches → True) — never accepted. **Target ≥ 30 trades per 30D:** below that, `v15_365_repair.py` runs a BOOST phase (same candidates); a softer set is kept only if both windows stay valid + positive + floored and the worst-window gain does not drop. If no softer set keeps the results, go live with ≥ 10 trades per 30D. A 365D verdict is only real if the 365D slice covers ≥ 330 days of NPZ history — shorter = `UNVERIFIABLE` (wait for full-history NPZ), never a pass.

**Book replacement (USER 2026-09-29):** a verified both-positive set REPLACES the current live per-sym book unless the book itself passes both windows on the current engine + NPZ (valid, positive, floors met) AND is ≥ the new set on 365D. Old book gains measured on a different NPZ timespan are not evidence; a book with a big 365D but a negative/invalid 30D does not reproduce and is replaced.

**Start from the best previous settings (USER 2026-09-29):** a FRESH sheet always compares live recipe / previous best / template defaults on the current engine and starts from the best credible one (`v15_pilot` 77542378 `[ADAPT-BASE]`); a sheet that did not start from the best previous settings is re-run.

**Do not:** discard a sym_side for a negative 365D; promote a set that is only 30D-positive; loosen the TIM/DD/floor gates to make a window "valid"; count a window as fixed while it is invalid.

## 59. FLEET RUN REGIME — RETIRED 2026-10-06 (SCHEDULER ERA; was USER 2026-09-29 PM)

The sweep-cron/full-sweep-driver regime below is **retired** (replaced by `tools/v15_fleet_scheduler.py`, §74). Kept as history; only the pipeline-order law survives:

**Pipeline (order is law):** 30D run #1 ALL sym_sides → recalc avg-delta → rebuild templates → 30D run #2 ALL sym_sides → 365D verification + adjustments (§58). No 365D run until run #2 completes.

**Guard-bypass law (survives):** the pilot's `ALREADY FINISHED (early) — MUST NOT RETOUCH` skip only fires when it finds a prior progress JSON in `V15_PROGRESS_DIR`. Every fresh pass MUST point `--progress-dir` at a NEW empty iso dir — that is what re-runs a finished side on the new template. Sheets still land in `SPREADSHEETS/V15_V16_CELL_BY_CELL/`.

(Retired detail: `v15_sweep_cron.sh`/`v15_full_sweep_driver.py` sharding, `v15_delta_health_monitor.py` Mac cron, the unconfirmed yellow-agent job, `reset_fleet_20260929.sh` — see git/backups history. Servers run backtests AND (S1) the live stack — §11.)

---

*End of the 2026-09-29 body — if a procedure above conflicts with older text, this wins. §59's pipeline-order law and §58 (negative 365D = faulty 30D sheet → repair loop, never a disqualification) are operator-authoritative. §14–§57 are the 2026-09-29 operator-corrected additions; §56 is the operator's verbatim fill spec and is the highest authority on how the templates are filled; §57 + DAILY_OPTIMIZATION_PLAN.md define the daily self-optimization loop; current run regime lives in §74.*


## 61. AUTOPILOT (USER 2026-10-02) — ON HOLD (`#BUILDER_HOLD` in S1 crontab, verified 2026-10-07). See AUTOPILOT_RUNBOOK.md.
`tools/v15_autopilot.py` (s1 cron */5, flock, resumable state `data/autopilot/state.json`) drives rounds: SWEEP (scheduler launches 30D pilots into the current progress dir) → COLLECT (per-host partials → merged workbook) → TEMPLATE (`v15_daily_template_update --apply`, NO `--sync-defaults`) → NORMALISE (`TEMPLATE_FINAL_NORM`) → DEFAULTS (sweep-only json) → SYNC (s2/s5, md5) → RESTART (new progress dir + defaults round id). `tools/v15_npz_keeper.py` (s1 cron */10) refreshes the NPZs of the next 10 pending STOCK symbols just before their turn (Tradier tail rebuild on s1, push to s2/s5 with md5, skipped for a host that is running the symbol). s1 is the template source of truth while the user is away (Mac template pushes are disabled, `#AUTOPILOT_OFF#` in the Mac crontab). Failure policy: a failing round-end stage is retried every tick; after 6 failures the round restarts on the PREVIOUS defaults so servers stay busy, and `STATUS.json` carries an `alert`.


## 64. CAT_DEFAULTS-BASELINE LAW (USER 2026-10-03, AUTHORITATIVE — comparability)

**Rule:** every pilot run baselines against the **latest cat_side defaults** (template bold/is_default=YES rows = `_tpl_defaults`, gated against venue truth + `cat_side_promotions.json`). Starting from a previous test — live recipes, `*_best.json` / `hustler_best.json`, previous progress `cumulative_overrides`, previous-xls C-fill, or an ADAPT-BASE that picks `previous_best` — is **completely forbidden until all 354×2 sym_sides have produced final sheets**. Rationale: chaining from a 5-minute-old test bakes that test's greedy path into every number and makes cross-sym sheets incomparable; the first full-coverage round must be defaults-pure.

**Enforcement (mechanism, verified in `v15_pilot.py`):** `V15_TEMPLATE_DEFAULTS=1` drops all ingested overrides → `overrides = dict(_tpl_defaults)` (:4658-4662); `V15_ADAPT_BASELINE=0` disables the ADAPT-BASE max-pick (default 1, and forced on under `V15_FRESH_RUN=1` at :4908); omit `V15_INGEST_BEST` (without it, FRESH_RUN falls back to recipe-only at :4630 — still previous-test-derived, so TEMPLATE_DEFAULTS=1 must accompany it). `V15_FRESH_RUN=1` + isolated `V15_PROGRESS_DIR` + isolated `--out` still required for ISO rounds. `CAT_SIDE_DEFAULTS_PATH` is consumed nowhere in the pilot/tools tree (dead env var — do not rely on it). Consequence, accepted: a defaults baseline that lands sub-floor yields a `[DIAGNOSTIC ONLY]` sheet with the sweep skipped — honest, per the rule — instead of a rescued number. Standing violation to clear: the run25 fleet launches with `V15_INGEST_BEST=1`; its rounds are previous-test-chained until the herd switches to this mode. **CLEARED 2026-10-06/07: current launcher env is `V15_TEMPLATE_DEFAULTS=1` + `V15_FRESH_RUN=1` + `V15_UNWIRED_SKIP=0` with NO `V15_INGEST_BEST` (verified on live fleet cmdlines) — rounds are defaults-pure.** Second enforcement (2026-10-03, `v15_pilot.py:2979` → `:3072` after the parallel manifest update, which preserved the gate; fleet md5 `402312f95ec7b674fac8c1e7f8a89079`): compliance-repair round 2 used to inject `prior_0/1` fleet sets via `_prior_final_sets()` — under `V15_TEMPLATE_DEFAULTS=1` priors are now forced to `[]`, so every compliance round stays defaults-pure. Third enforcement — the bold-type law: a template bold that cannot pass `_coerce_override` under its field type deterministically voids EVERY defaults-pure set (observed: bold `'0.5'` for bool `WT_EXHAUST_EXIT_REQUIRE_GAIN` rejected all PEPE/1INCH compliance rounds 0-1, forcing the §64-violating round-2 priors that produced the VOID first-launch ADAPT numbers +27.65/+12.41). Fix (2026-10-03): 6 phantom promotions voided from `data/cat_side_promotions.json` + SQL KV on Mac/S1 (S2/S5 have no SQL store, JSON fallback covers them) — `WT_EXHAUST_EXIT_REQUIRE_GAIN`/`WT_4H_VEL_EXIT_REQUIRE_PROFIT` (CRYPTO_LONG), `LEGACY_PROC_SINGLE_REENTRY`/`LEGACY_REENTRY_PSR_DC_BOUNCE` (STOCKS_LONG), `LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK` (STOCKS_SHORT), `DC_BREAKOUT_SCORE` (CRYPTO_SHORT) — and template bolds moved to config-parity (`WT_EXHAUST`→`False` ×4, `DC_BREAKOUT_SCORE`→`15`, `LH_HL_FILTER_REQUIRE_BOTH`→native bool). Exact-loader audit (`template_bold_defaults` + coerce per key) now reports 0 poison keys and 0 loader violations on all 4 cat_sides. Note: the Oct-02 avg_delta batch that wrote the phantoms measured deltas for values the strict engine cannot evaluate — any future promoter must coerce-check values before recording them. Fourth enforcement — the ablation exemption (2026-10-03, entry-death outage): `ABLATION_DISABLE_*=True` in crypto template bolds deterministically kills ALL entries (fleet-wide 0-1 trades on every crypto sym_side, both directions; proven: cat_side set with 13 True → 1 trade, same set with all False → 203 trades PEPE / 186 1INCH, valid). Root cause chain: `config.py` ablation defaults flipped True on 2026-09-21 (live forward-proving regime) → template bolds followed to config-parity → every defaults-pure baseline died; prior-based runs survived only via per_sym overrides that lack ablation keys... until the Oct-02 builder run (`tools/build_cat_side_defaults_4.py`, 18:33Z) also wrote True into `cat_side_defaults_4.json`. Fix: bold+`is_default=YES` moved True→False for all 13 ablation switches in `TEMPLATE_CRYPTO_LONG/SHORT` + `TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_*` (102 bold flips + 102 YES moves, zero row add/delete; STOCKS untouched — working), JSON purged to match (md5 `d29bd72d`), deployed S1/S2. LAW: ablation switches are EXEMPT from config-parity — the live OFF-proving state must never leak into backtest defaults; any builder/promoter emitting `ABLATION_DISABLE_*=True` into a crypto default set is a P0 violation. Fifth enforcement — the per_sym-baseline skip: the Oct-02 "cat_side vs per_sym, best as E3" block (`v15_pilot.py:5077`) starts a sym from its own prior best = running against itself. Defaults-pure rounds must export `V15_SKIP_CAT_PERSYM_BASELINE=1` (baseline = template bolds only, no comparison). Verified 2026-10-03: PEPE 226 trades / 1INCH 222 trades, valid, zero priors. Sixth enforcement — the self-improvement loop (2026-10-03): defaults improve ONLY via rebuild→daily-update→builder, unfrozen this date (`TEMPLATES_FROZEN` lifted to backups/, user-ordered). Promoter gates now: coerce-check per field type (phantom class refused), ablation-True P0-refused, `--min-n 3` evidence floor, every refusal ledgered. Builder P0: crypto `ABLATION_DISABLE_*` forced False post-fill (template False bolds are dropped as "untrusted vs config truth" and refilled True = entry death; observed 9 re-emitted, forced back). Fleet runs `TEMPLATE_FINAL_NORM` (the ONLY templates since 2026-10-07 — the legacy `SPREADSHEETS/TEMPLATE_*.xlsx` set is archived; the updater runs on FINAL_NORM only). Zero-delta watchdog (`tools/v15_zero_delta_watchdog.py`): avg==0+median==0 over ≥8 syms on a non-default value = dead suspect → `--apply` appends to the pilot's skip list (KG never added); first run: 1 suspect row (`WT_15M_BOUNCE_REL_VOL_GT_1=True`), 0 disablable. Daily cron `V15_SELFIMPROVE` 05:00 UTC (`tools/v15_daily_selfimprove.sh`, dated-agg explicit — `*latest*` names are clobbered by S1→Mac pulls). Full-compute law: `V15_POSSYM_SAMPLING=0` fleet-wide (both launchers) — validity requires every row calculated. **SUSPENDED by later user order 2026-10-06 ("sampling ON again" — fleet now launches `V15_POSSYM_SAMPLING=1`; see §73). Sampling defers low-evidence rows/cells to a sample-free heal pass rather than deleting them, which is why the suspension is validity-safe.** Seventh enforcement — the complete-rounds law (USER 2026-10-03, round `run27_complete_20261003`): (1) CORPSE-CLEAR — rows stuck `is_running` are dead-run orphans (QBTS_LONG: 835 permanent blanks reading as done); the pilot drops uncalculated ones at startup (post-DEDUP, safe) and re-drives them. (2) UNWIRED RECALC — `V15_UNWIRED_SKIP=0` fleet-wide: audit keys calculate anyway, honest 0.0 rows tagged `UNWIRED_CALCULATED` (never blank, never fake). No switch is ignored until several complete rounds exist. (3) HOLLOW BOARDS REOPEN — `symside_status` no longer counts corpses, undecided blanks, `NOT_WIRED_VEC` or `SKIPPED_SAMPLING` rows as filled (QBTS_LONG 3084/3088 → 2280/3088); re-driving hollow boards is NOT a §65 retest (they never qualified). (4) ACTIVE-ONLY SCOPE — this round runs actively-traded symbols only (crypto `symbols_active.json` + TRB long/short; default ON, `V15_ACTIVE_ONLY=0` restores full queue; explicit `--order` bypasses): S1 150→100, s2 204→111. (5) CLAIM-ADOPT + DONE-CLEAR — a restarted herd adopts same-host dead-PID claims immediately instead of idling 90m on its own corpse (registry lives on S1; workers adopt via `_s1_run`; other hosts' claims untouched); `_only_complete` clears claim done-markers for reopened boards (a `done` file otherwise blocks the redrive forever — 29 stale markers found). (6) NO-SHADOW LAW — `import glob, json` inside `main()` made `json` function-local → UnboundLocalError swallowed by inner excepts (silent unfiltered order) / fatal outside try (herd death, S1 idle); fixed via `_jsu`/`_jsact` aliases; regression `test_herd_noshadow.py` (AST: main() binds no module-global name) fails pre-fix, passes post-fix. (7) TEMPLATE HYGIENE — 20 untestable rows deleted from all 8 templates (phantom switches `UNIVERSAL_NOLOSS_BYPASS_REASONS`/`CIRCUIT_BREAKER_THRESHOLD_PCT` in no config + unwired; `BB_BREAKOUT_ENTRY_TF=20` int-for-str) + `DC_BREAKOUT_SCORE` 0.75→0 (label==tested value); parser coerces bool-words/native-bools → 1.0/0.0 for float defaults (config_tradier 0.0/1.0 bool encoding: `HTF_GATE_D_MANDATORY`, `LH_HL_FILTER_REQUIRE_BOTH`). Fleet topology corrected: `s2-int` tunnels to S1 (same bytes); real workers are S1 `niels` + s2-box `10.0.0.4` (hostname s2, stocks) + s5 `10.0.0.5` (hostname s5) — all three herds restarted uniform this date.

## 65. NO-RETEST LAW (USER 2026-10-03, AUTHORITATIVE — compute-once)

**Rule:** a sym_side with a **qualifying final** is NEVER relaunched until **every** sym_side in the order has one **and** its NPZ was refreshed after the final. Qualifying = published `{SYM}_bh*_gain*_30d_matrix.xlsx` in `V15_V16_CELL_BY_CELL` with `gain>0` (parsed from the filename: `p`=decimal point, `m`=negative), **and** not a loser (`final_gain<0` or 365D `<0` in progress). USER 2026-10-03 pos-gain go-live: BH is recorded (filename + manifest `gain_vs_bh`) but NEVER gating — a moonshot BH month must not quarantine a sound positive set; the 365D verify (valid + gain>0 + ≥80 trades) remains the quality gate. Note: the 30D finish docstring historically claimed a DD≤30 check the code never implemented — no DD gate exists; do not assume one. Rationale: re-running a qualified sym against itself burns fleet hours for a second opinion nobody asked for; coverage first, refinement only on fresh data.

**Why the hole existed:** three retest paths ignored prior finals — (1) template-mtime staleness (`xlsx older than TEMPLATE_*` → re-run, so every template deploy retested the universe), (2) BEST-order freshness (BEST validation ignores global done and re-runs on latest template), (3) the `is_complete` requalify (a final whose progress JSON is missing locally reads as not-done). The pilot's DEDUP lock and the claim registry only stop *concurrent* doubles, never sequential reruns.

**Enforcement — HERD MECHANISM RETIRED 2026-10-06 with the herd; scheduler-era enforcement is PARTIAL (gap flagged 2026-10-07):** the retired mechanism (`tools/v15_local_herd.py` `_protected_qualified(order)`, NPZ-refresh release valve, ssh union of S1 finals) no longer runs. What the scheduler enforces today: `done30` tracking per cat_side from published finals — finished pairs are not relaunched. NOT yet ported: the NPZ-refresh release valve (fresh data does not currently release a qualified sym) and the ssh finals union (each host counts its own finals). The hollow-board exception below and the pilot-side midrun-swap refusal still hold (pilot code, launcher-independent).

**Hollow-board exception (USER 2026-10-03 complete-rounds, enforced in `_qualified_final_set`):** a qualifying final protects ONLY a complete board. A qual whose local progress/final exists on this host but `is_complete()` is False (hollow: corpses/blanks/NOT_WIRED/SKIPPED rows unfilled) is released to `todo` with a `[HOLLOW-REOPEN]` line — redrive is not retest (the board never finished). Remote-only quals (no local progress/final — finished elsewhere) stay protected (compute-once). Zero-trades boards report `is_complete` True (terminal, never redriven); unfillable redrives park via the herd STALLED guard. Fleet proof 2026-10-04: S1 64→1 protected/todo 51, s2 99→1/todo 110, s5 69→2/todo 46; claim slate 317→0 (zero done-markers existed); S1/s5 crypto queues split disjoint 76/74 by base (twins together, was identical 150-line files); herd md5 `405800a7`.

**Midrun-swap refusal (USER 2026-10-04 ALGO stale-751, enforced in the `v15_pilot.py` FINAL gate):** the run-start `npz_id` stamp (RULE#4) is re-compared at FINAL publish; a changed NPZ (regen swap under a measuring pilot) REFUSES the final — board archived (`.npzprev`), `needs_redo` scheduled, herd relaunch refills every row on the new generation — so no final ever mixes generations. Unchanged NPZ stamps `npz_id_final` as proof. Guard-exception fail-opens with an honest `npz_final_check` flag. Regression `test_npz_midrun_*` in `test_v15_row_guards.py` (2 passed; pre-existing `test_scan_board_tally` failure untouched — tally drift in another lane). Deployed 2026-10-04 (pilot md5 `077fc1bb`, all 6 targets verified); running pilots finish undisturbed, new launches carry the gate.

## 73. POSSYM SAMPLING — CURRENT PROBABILITIES + PASS STRUCTURE (USER 2026-10-06, AMENDED 2026-10-07)

Sampling is ON fleet-wide (`V15_POSSYM_SAMPLING=1`; the 2026-10-03 full-compute suspension in §64 is
itself suspended — sampling defers, never deletes). Per-round compute probabilities from
`data/avg_delta_pos_sym.json` evidence (`pos_sym`/`n_sym`, min evidence `V15_POSSYM_MIN_N=3`):
**None→1/20, 0→1/20, 1→1/10, 2→1/6, 3→½, ≥4→every** (deterministic draw per sym|tab|row|round —
same input re-skips identically within a round; 2026-10-07: rows with NO pos_sym at all are now
sampled 1/20 like pos_sym 0 — previously always computed). Pass 1 evaluates winners; skipped
rows/cells stay pending (`SKIPPED_SAMPLING`, blank); RULE#3 refuses publish while hollow, which
schedules a sample-free REDO heal pass that evaluates every hole (holes-only, chain carried).
Bold defaults and just-added (`new`) rows are never sampled. Yellow sampling uses the filter's
orange-row evidence as proxy (cell-level evidence now exists for the zero-skip, §72, but sampling
still uses the proxy) — EXCEPT unevidenced cells: a name-yellow cell with NO entry in
`data/cat_avg/priority_evidence_{cat}.json` (NONE: never numeric in any finalized sheet) is
must-calculate priority inside every computed row and is never sampled out (`none_priority`
bucket; fail-open when the JSONs are absent). Row sampling still gates the row; the priority
applies within computed rows. Full work order: `NONE_CELLS_PRIORITY_20261007.md`.
**Fleet enforcement (USER 2026-10-07):** two layers — scheduler launch env `V15_POSSYM_SAMPLING=1`
(env wins) plus `data/possym_sampling.flag=1` on every host (fallback for any launcher); verified
live on s1/s2/s5 + Mac. Evidence distribution: chain step 4 rebuilds + pushes
`data/avg_delta_pos_sym.json`, step 4b regens + pushes `data/avg_delta_pos_sym_cell.json` +
`data/cell_evidence/*.json` (multi progress-dir union, newest-wins per sym_side); Mac-apply pulls
both with per-file stamp-md5 checks. Guard tests: `tests/test_v15_possym_sampling.py` (exact prob
table, always-compute buckets, determinism, pos=0 rate band, env precedence, zero default-ON).

## 74. FLEET REALITY + WALL-TIME MATH (AUDITED 2026-10-07)

Launcher: `tools/v15_fleet_scheduler.py` (S1 cron `*/2`, `--once` per tick) with
`tools/fleet_hosts_final.json` (4 hosts since 2026-10-07: s1/s2/s5/s6; `max_pairs`, `workers_per_side`,
`mem_reserve_mb`, `oom_mb` per host; tuned there, never in prose). Launches `--seq-mode worst2best --window-days 30 --vector-only`
(`V15_TEMPLATE_DEFAULTS=1`, `V15_FRESH_RUN=1`, `V15_POSSYM_SAMPLING=1`, `V15_UNWIRED_SKIP=0`,
`V15_SKIP_LIVE_AT_DONE=1`, NO `V15_INGEST_BEST`). Reaps: 8h hardcap, 60min stall, OOM floor
(youngest pilot SIGKILLed — progress JSON persists, relaunch refills). No launches over cap or under
reserve. Observed 2026-10-07: S1 also runs the live stack + scheduler (load 20–40 pre-tune);
S2/S5 additionally run the `v15_trade_parity.py` lane (~3.5 cores each); S1 reboots interrupted runs
(daily ~12:55 plus ad-hoc — cause under investigation, needs sudo).
**Chain sequence (VERIFIED 2026-10-07 — currently OFF):** the scheduler codes the full per-symbol
chain (`need30` → `need365` → GS-heal/`needrepair` → terminal; pass 2 steals 365D/REPAIR-a1 onto idle
hosts; slot released only at terminal), BUT it is gated: `V15_SCHED_NO_CHAIN=1` (S1 cron env, USER
2026-10-01 order) + missing `data/autopilot/chain_mode.flag` → slots release after 30D. The 30D→365D→
30D-again loop runs ONLY when the flag file exists (one command: `touch` it on S1). Do not create it
without explicit user order — 365D chains multiply per-symbol compute several-fold.

**Wall-time math (why 0.07s becomes hours — all figures audited, none estimated):** 0.07s is per-EVAL
(one candidate; loaded 0.09–0.17s). Per-ROW ≈ 205 evals / workers (≈7s at 3 workers). Per-SYM ≈
3393 rows × ~1.5s avg (skipped + eval mix) × ~2.7 refill passes + FINAL_RECHECK (~3800 evals) +
DIAGNOSE/REPAIR365 (~20k evals) ≈ 5–8h at unloaded pace, 12h+ contended. Reduction levers in force:
fewer+faster pilots (cores are the bound), zero-formula skip (§72, ~70% of cell evals), possym
sampling (§73). Structural floor: full-fidelity (all yellows + all passes + post-stages) cannot go
sub-~50min even at 16 workers — sub-10min requires cutting evals (fewer yellows / single pass / no
post-stages), a science decision, not an ops tweak.

