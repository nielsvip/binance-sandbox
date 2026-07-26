# 📖 BACKTEST BIBLE — THE single source of truth for all backtesting

**EVERY agent MUST read this before running, reading, storing, or applying any backtest.**
It supersedes scattered backtest rules in CLAUDE.md (which now points here). If this doc and
older notes conflict, THIS wins. Last rewritten 2026-07-01.

The mission: **pool_sharpe > 0.5 AND gain/mo > 20% on the REAL live config, ≥10× buy&hold per
symbol.** We are NOT there — current honest baselines are NEGATIVE (crypto pool_sharpe ≈ −0.046,
tradier ≈ −0.055, both sub-floor). The backtest only recently became truthful (see §1). The job:
make structural changes, find the culprits dragging sharpe negative, and prove improvements on a
faithful engine before they touch live money.

---

## §0 — MACHINE ROLES (non-negotiable)
| Machine | Role | Runs | Never |
|---|---|---|---|
| **Mac** (`/Users/niels/Documents/binance`) | LIVE trading + source-of-truth code | live `ez_manage`/`tradier_manage` + feeds | multi-year sweeps |
| **S1** (`s1-int`, `/home/niels/binance-sandbox`) | ALL backtests + 24/7 sweeps | `backtest_v8_engine.py`, OFAT, per_sym | live trading |
| S2 | DEAD (2026-05-08) | — | everything |

Edit code ONLY on Mac → `rsync -az --no-perms --update <file> s1-int:/home/niels/binance-sandbox/<file>`.
**NEVER `push.py` for backtest sync** — it has a STOP-MAC-FIRST path that kills Mac live + starts S1 live. Direct rsync only.

---

## §1 — THE PARITY MANDATE (why every past number was a lie, and the #1 rule)
**A backtest number is worthless unless the engine runs the SAME strategies that trade live.**
Proven 2026-06/07: the dominant live opener `WT_3M_FORCE_OPEN` fired **0** in every backtest for
months (NameError + sweep-gate + dead predicate) → the old backtest showed *positive* sharpe while
the live account bled. Same disease: `R1_USE_DC_4BAR` was defined-but-never-read (R1 always used the
churny 4-bar). **"Dead"/"disconnected" ≠ useless — it is often a wiring BUG killing a live strategy.**

Rules:
1. `backtest_v8_engine.py` calls the REAL `ez_manage.process_position` / `tradier_manage.process_position`
   / `check_entry_candidates` / `evaluate_reentry`. Wiring a knob in the live file fixes the backtest for free.
2. Before trusting a sweep of knob X: confirm X actually EXECUTES in the engine (grep the read site; run one
   backtest and grep the trade reasons for X's effect). If X can't change trades, the sweep is measuring nothing.
3. A sequential per-bar engine CANNOT reproduce live's concurrent-async-loop *labels* (daemon vs quick_open) —
   only AGGREGATE behavior (total trades, churn, PnL). Validate aggregate, not per-producer labels.
4. Known engine gaps still open (fix before trusting affected results): crypto GUARANTEED reentry (~5%),
   per-sym profile-engine reads. Log at `data/_knob_audit/`.

---

## §2 — METRICS: NO LIES, EVER (hard errors)
Route EVERY sharpe/gain number through `metrics_guard.py`. Import it or you may not write a sharpe anywhere.
- **pool_sharpe = mean(all trade returns)/std(all trade returns)**, pooled across all trades of all syms.
  Per-trade returns only. Open losers MtM'd + appended. NO annualization, NO sqrt(252)/sqrt(N). NO bare "Sharpe".
- Canonical reporting line (all 9 fields or invalid):
  `pool_sharpe=X | sym_sharpe=X | avg_gain_trade=X%/trade | gain_per_yr=X%/yr | gain_sym_yr=X%/sym/yr | trades=N | dd=X% | n_syms=N | years=Y`
- **SAMPLE FLOOR for any promotion/live decision: ≥48 crypto OR ≥100 stock syms, >1yr, ≥30 trades/sym.**
  Below floor → tag `[DIAGNOSTIC ONLY · n_syms=X]`, CANNOT decide/promote/recommend. The OFAT (16 syms) is diagnostic.
- pool_sharpe < 1.0 = not a promotion candidate (target 0.5 interim, 1.0+ real). Tier names via `metrics_guard.tier_name()` (never "trash").
- IMPOSTER BLOCK: no single-symbol "BEST" promotion; multi-sym pool only; per-sym diagnostics live in `data/_diagnostic/` and are NOT live-loader-discoverable.

---

## §3 — WHERE TO READ PAST RESULTS (check BEFORE testing anything)
1. **Central DB** `s1:/home/niels/binance-sandbox/data/test_results_central.db` (sqlite; use python, no CLI):
   - `runs` (387k): one row/backtest with all 9 canonical metrics + `label`, `mode`, `account`, `start_date`, `n_syms`, `ts_run`.
   - `symbol_results` (146k): per-symbol pool_sharpe / acc_gain / gain_vs_bh (+ `raw_json`).
   - `ingested_files` (12.5k): which CSVs are already loaded.
2. **Switch priority DB** `s1:.../data/_knob_audit/switch_priority.csv` (+ `.md` top-100): ranked switches, tested?/delta_sharpe/times_tested/priority_score. **THIS decides what to test next.**
3. **OFAT live report** `s1:.../data/ofat_report.md` (refreshed ~5min): current baselines + top delta-sharpe levers.
4. **Raw sweep CSVs** `s1:.../data/sweep_results/*.csv` (canonical columns enforced).
5. **Dead-knob audit** `Mac:/data/_knob_audit/` — `true_disconnected.json` (319 real), `master_ledger.md` (why each disconnected).

---

## §4 — AVOID OVER-TESTING (mandatory pre-flight)
Before launching ANY param test:
1. Query the central DB for that param/value: has it been run at this mode+sym-count? Check `switch_priority.csv` `times_tested`.
2. If `times_tested ≥ 3` and delta_sharpe is flat/known → DO NOT re-test. Pick from the NEVER-TESTED frontier list.
3. If tested only sub-floor and it showed a positive delta → the correct next step is CONFIRM AT FLOOR (Tier-2, ≥100 stock / ≥48 crypto), not re-run the sub-floor screen.
4. Log the intent in `switch_priority.csv` before spawning workers (prevents two agents testing the same thing).

---

## §5 — HOW TO STORE NEW DATA INTO SQL (every new result)
1. Every sweep/backtest writes a canonical-column CSV to `s1:.../data/sweep_results/` via `metrics_guard.write_sharpe_row()` (the ONLY sanctioned writer — validates + refuses violations).
2. The ingest job loads new CSVs into `test_results_central.db` (`runs` + `symbol_results`), deduped by `source_file`+`source_mtime` (tracked in `ingested_files`). Run/verify ingest after a sweep batch completes.
3. Per-symbol results MUST be written (not just pool aggregates) — culprit-hunting needs per-symbol pool_sharpe + gain_vs_bh.
4. Tag every row: `mode` (crypto/tradier), `account`, `n_syms`, `years`, `start_date`, `label`=param+value, `reliable`/`DIAGNOSTIC`.
5. Never overwrite a run; append. Never store a sharpe not produced by real code (§1).

---

## §6 — HOW TO READ, USE, APPLY RESULTS → WHEN TO GO LIVE
Pipeline (each stage gates the next; skipping = how money was lost):
1. **Tier-1 (vec, `v8_quick_engine`)** — feel only, ~85% parity. Shortlist. NEVER "this is better".
2. **Tier-2 (`backtest_v8_engine`, real code)** — THE test. Run shortlist on 12→48 crypto / 100+ stocks.
3. **Floor check** — result must be at floor (§2) with pool_sharpe above current baseline by a real margin.
4. **Per-symbol check** — the change must not tank individual symbols (read `symbol_results`); target ≥10× b&h/sym.
5. **Parity check** — the changed knob must actually execute live (§1) and live==backtest on the same bars.
6. **GO LIVE** only when: Tier-2 at floor + pool_sharpe improvement + no per-sym disaster + parity confirmed + USER approval for anything structural. Then edit live file on Mac → rsync to sandbox → restart the affected live system.
**Never** promote from sub-floor, single-sym, Tier-1, or a knob unproven to execute live.

---

## §7 — WHEN TO START PER_SYM TESTS
Phase 1 (cross-symbol) BEFORE Phase 2 (per-symbol). **Do NOT start per_sym until the cross-sym baseline
is positive** (pool_sharpe > 0 at floor). Per_sym optimizing on a negative cross-sym base just overfits noise
per symbol. Once cross-sym is positive: per_sym agents (`per_sym_engine_crypto/stocks.py`) tune each symbol
vs the applied cross-sym baseline; keep a per-symbol improvement column (Δ vs the final applied baseline).
Per_sym results are `[DIAGNOSTIC · n_syms=1]`, live-loader-hidden, promoted only via the floor-gated promote path.

---

## §8 — THE SWITCH PRIORITY DATABASE (the testing roadmap)
`s1:.../data/_knob_audit/switch_priority.csv` — one row per switch, ranked. Columns: `switch, mode, tested?,
best_delta_sharpe, best_value, baseline_pool_sharpe, n_syms_tested, trades, times_tested, last_tested_ts,
priority_score, notes`. Use it to: pick the next test (highest priority_score, untested-or-underexplored),
avoid re-tests (§4), and see per-past-test delta_sharpe. Regenerate after each sweep batch. Never test a switch
in `true_disconnected.json` until it's WIRED (fix the wiring first — those are the force-open-class finds).

---

## §9 — FINDING THE CULPRITS (why we're not at 10× b&h)
Current baselines are negative → something is actively destroying edge. Hunt order:
1. **Per-symbol worst list** (`symbol_results`, most-negative sym pool_sharpe / gain_vs_bh) — which symbols drag the pool down?
2. **Exit churn** — R1/R2 firing too often (the 4-bar dc_low4 chase was one; now crypto uses frozen 20-bar dc_low).
   Count exit reasons per symbol; a symbol with hundreds of R1_DC_LOW4 round-trips/mo is churning.
3. **Reentry/commission bleed** — daemon+quick_open reopen rate; fees are ~0.08%/side, real drag on $7 positions.
4. **Parity gaps** — any live strategy not in the engine (§1) = the backtest can't even see the culprit.
5. **Config drift** — knobs loosened for "gain/mo" that tanked pool_sharpe (WT_DC, GR_HTF, MIN_IND history).
Report culprits per-symbol with the canonical line; fix structurally, re-test at floor.

---

## §10 — 24/7 S1 SWEEP COMPLIANCE (keep it running + honest)
- Sweeps run continuously via the watchdog; results → `sweep_results/` → ingest → central DB → OFAT report.
- After ANY code/config change: rsync to sandbox so the 24/7 sweeps use fresh code (they subprocess the engine, so next runs pick it up). Verify `md5` Mac==S1.
- Every sweep must: use `metrics_guard`, write canonical columns + per-symbol rows, tag mode/floor, never re-test a ground-to-death param (§4), and respect the parity mandate (§1).
- S1 utilization floor ≥60% CPU+MEM; crypto and tradier alternate; never let a system go >2h without fresh results.
- Log paths under `~/logs/` (not /tmp). Launch detached (`nohup … < /dev/null & disown`).

---

## §9.1 — #1 CULPRIT FOUND (2026-07-01): **CHURN** (not shorts, not direction)
Window = 2024-01→2026-07 = BEAR for alts (ADA −84%, AVAX −87%, ATOM −67%, LINK −64%, SOL −25%; BTC/TRX/XLM up).
Shorts SHOULD print. Central DB decisive proof (same symbol, same period):
- **AVAXUSDC SHORT: 602 trades → +155% ; 183,843 trades → −18,490%.** SOLUSDC: 203 tr → +113% ; 185,175 tr → −18,582%.
- Low-churn shorts (<2000 tr) on the bear alts = **+100% to +856%, pool_sharpe +0.24 to +0.58.** Hyper-churn (185k tr) = −18,000%.
- LONGS on the same alts ALSO churn 185k tr → −18,000% → **churn kills BOTH sides; it is not directional.**
The −18,000% "losses" are FEE + CHOP bleed (185k tr × ~0.16% round-trip ≈ 30,000% in fees). Distribution:
short-trade p50=152, p95=8,422, max=187,243 — only a 77-run tail hyper-churns, but those are the disasters,
and the current live-ish baselines sit churny enough to be negative (OFAT ~900-1200 tr/sym, pool_sharpe <0).
**THE FIX = kill churn (ride trends, don't re-enter/chop):** the churn engines are the reentry daemon, WT_3M_FORCE_OPEN
refire, tight/chasing exits (R1 4-bar was one — now crypto R1 = 20-bar frozen). Same medicine for shorts as longs:
freeze the stop at entry (SHORT mirror = dc_high frozen), ride to the bottom, don't re-short every bounce. Every
churn/reentry/force-open/exit-tightness switch ranks HIGH in the priority DB. Target: get every sym's trade count
into the low-churn regime (~hundreds/yr, not tens-of-thousands) → shorts flip +, pool → +0.2-0.5.

## §9.2 — TEST-EVERY-PATH + REVISE-NEG-DELTA POLICY (USER 2026-07-01)
- The 24/7 OFAT must cover EVERY testable switch (2,784 universe; ~2,694 never-tested = the frontier).
  Priority DB (`switch_priority.csv`) ranks them; work top-down, never re-test a ground-to-death param (§4).
- **A switch whose tested values ALL give ≤0 delta_sharpe is NOT "done" — it must be REVISED and retested
  with DIFFERENT settings** (wider value range, opposite direction, or a structurally different formulation),
  not abandoned. Log the revised range. A path is only "closed" when a meaningfully different set of values
  was tried and none helped at floor.
- Short-side switches are tested/revised FIRST (they're where the loss is, §9.1).

## §10.1 — CENTRALIZED + SEQUENTIAL TEST EXECUTION (USER 2026-07-01, mandatory)
Running many tests in parallel OOM-kills S1 (35 procs → 5.4GB avail → every run dies before writing).
ALL tests MUST serialize and centralize:
1. **Global test lock** `tools/run_seq_test.sh <tag> <cmd...>` — `flock` on `/home/niels/logs/TEST_SEQ.lock`;
   only ONE wrapped test runs at a time; auto-ingests to the central DB on completion. Wrap EVERY ad-hoc test in it.
2. **Launchers capped**: `ofat_watchdog_s1.sh` → `--max-par 1 --min-avail 8000`. `sweep_guardian_s1.sh` → `--workers 1`.
   REMAINING RETROFIT: both watchdogs should acquire the global lock before launching so OFAT+sweep don't run
   concurrently (currently ~memory-safe bounded concurrency, not strict 1-at-a-time). Until then, keep total
   backtest procs ≤ ~a few and avail ≥ 8GB.
3. **Ingest cron** `*/10` → `tools/build_test_results_db.py` loads new `sweep_results/*.csv` into
   `test_results_central.db` (deduped by `ingested_files`). Every result centralizes automatically.
4. **Tier-2 caveat**: multi-symbol crypto Tier-2 ad-hoc runs are SLOW and often time out before writing the
   trades file — prefer the OFAT/sweep infra (writes canonical CSV → DB) or single-symbol short windows.
5. **Vec caveat**: Tier-1 vec does NOT model the churn engines (force-open/R1/daemon-reentry) — churn A/Bs
   MUST use Tier-2. Vec is entry-shortlist only.

## §10.2 — LOCKED-FILE DEPLOY GATE (USER 2026-07-07, hard rule — closes the "circles" loophole)
Every rule above is worthless if a value can still reach live via a careless direct edit + restart. 2026-07-07:
config.py was found carrying a `[DIAGNOSTIC · n_syms=11]`-labeled 50x position-size multiplier, sitting
uncommitted, one restart away from live — the author's own comment admitted it was sub-floor and unvalidated,
yet nothing blocked it. This is the exact failure mode burning "thousands and thousands of dollars month
after month without improvement" (user's words). **Before restarting ANY live process to pick up a config/live-file
edit:**
1. `git diff <file>` FIRST, always — even for a "one-line" requested change. If the diff is bigger than the
   requested change, STOP and triage every extra hunk individually (see [[feedback_config_forensic_triage_pattern_20260707]]):
   keep only what is dated + reasoned + either (a) confirmed at Tier-2 floor per §2/§6, or (b) explicitly
   safety-motivated (disables/tightens risk). Revert everything else to last-known-good — never blind-push,
   never blind-revert-to-HEAD (HEAD may itself be missing other already-approved safety work).
2. Any value/comment self-labeled `[DIAGNOSTIC]`, `n_syms<floor`, "revalidate", or similar hedge language is an
   automatic REVERT candidate — the author already told you it isn't proven. Never deploy on the strength of a
   good headline number alone; check the sample size behind it.
3. Log the keep/revert split explicitly in `LOCKED_FILES.md`'s lock entry so the next session doesn't repeat
   the archaeology, and preserve the full pre-edit state in `backups/` (nothing gets silently discarded).
4. This gate applies even under HANDS_FREE — it is not a "may I proceed" question, it is a mechanical diff
   check that must happen before every locked-file restart, full stop.

## §11 — CHANGE LOG (structural changes must be recorded here + in memory)
- 2026-07-19: trb/trc symbol-universe mirroring (tradier_rankings.py) + inf/men symbol-universe
  redirect (config.py, per_sym_7d_agent.py, new symbols_men_long/short.json, S1 crontab fix) —
  two live A/B parity arms, §12.14. Also diagnosed VPN/egress blackout as men's (and ang/flz/fin's)
  "not trading" root cause — infra issue, not code, see memory `project_vpn_down_still_ongoing_20260719.md`.
- 2026-07-18: RESULTS-STORE REWORK (stocks first): universe registry + 30-min snapshots (§12.7); per-key param-range store + hope/inert analytics + XLSX (§12.8); stocks session-4h precompute fix staged for post-campaign regen (§12.9); stop-TF manifest expansion (DC D, BB D/W); per_sym_trb_profiles.py → old/; persym campaign crons on S1; locked-file patches staged in patches/.
- 2026-07-01: force-open wired (0→247 fills); R1_USE_DC_4BAR wired, crypto→20-bar dc_low (frozen at entry, no chase), stocks stay 4-bar (A/B pending); BTC_DEDICATED disabled (unbuilt 20× safety knobs); classifier fixed (true disconnected=319 not 842); switch priority DB built (`data/_knob_audit/switch_priority.csv`, 2694 never-tested frontier); **#1 culprit = SHORT side (§9.1).**

**When in doubt: truthful −0.05 beats a lying +0.5. Prove it real, or don't ship it.**

---

## §12 — CAPTURE ERA (2026-07-08 → ): b&h floor, per-trade audits, the trade gate (USER MANDATES)

**§12.1 The b&h floor.** For every (sym,side) key, buy&hold is the FLOOR: `gain_vs_bh < 1.0` = a DEFECT to
analyze and readjust — never merely gate off. Perfect-capture CEILING (sum of ≥15% swing amplitudes; MU ≈
+30,900%, SNDK ≈ +100,600%) is the reference; coverage% is the metric. Side-aware b&h: SHORT keys use the
short-and-hold return.

**§12.2 Per-trade capture audits** (`tools/capture_analysis.py`, artifacts `data/_diagnostic/capture/<KEY>[.tier].md`
+ `.summary.json` + `.trades.jsonl`): every trade decomposed — entry lag vs swing low (which gate/silence caused
it), exit giveback vs swing high (verbatim exit reason + post-exit runup), verdict OK/BOUGHT_LATE/SOLD_EARLY/
BOTH/COUNTER_SWING. The retune loop ranks knobs by summed giveback pp and adjusts ONLY implicated knobs.
TF ZOOM-OUT ladder when a tier's best config stays <2× b&h: T1 exit patience → T2 structure zoom → T3 full
swing (D/W, tens of trades/yr accepted). T3 failure on a mover = named system defect, not a benched ticker.

**§12.3 THE TRADE GATE** (in `tools/apply_gainmo_gate_and_mult.py` → `data/persym_final_book.json`):
_LONG tradeable only if post-parity `gain_vs_bh ≥ 2.0`; _SHORT: 2× short-b&h when short-b&h>0, else absolute
(gain/mo>0 ∧ pool_sharpe>0 ∧ trades≥30). MANDATORY keys (user pins) always tradeable, never bench-writable.
Verdict source = post-parity verdict store ONLY (never DB medians); keys without verdicts keep current state.
Scoreboard: `data/_diagnostic/trade_gate_scoreboard.json` (+ digest email).

**§12.4 Provenance stamps.** Every engine run stamps FOUR file md5s (`eng+tm+wdd+cfgt` = backtest_v8_engine,
tradier_manage, wt_dc_delta, config_tradier); any change mid-run auto-invalidates the arm (no-delete quarantine).
Results without stamps = pre-parity era = DO NOT COUNT. The wall-clock-gate parity class (§12.6 case law) is why.

**§12.5 FULL-SETTINGS REQUIREMENT (USER 2026-07-18).** A result without its complete recipe is a LIE by
omission. EVERY test row — baseline sweeps, per_sym tests, 7D tests — MUST persist alongside its metrics:
(a) the override file contents verbatim, (b) the resolved EFFECTIVE config diff vs code defaults, (c) the 4-file
stamp, (d) start date/window/symbols. Applies to verdict.json, capture summaries, sweep CSVs (overrides_json
column), central-DB rows, and the XLS exports (settings columns per test). Writers that can't provide this must
not write. Historical rows without settings stay usable for "when", never for "what was tested" (§3 caveat).

**§12.7 UNIVERSE MANDATE (USER 2026-07-18).** Backtests MUST test the live tradeable
(symbol, side) universe — not the union-of-both-sides or the raw NPZ directory ("random"
universe). Source of truth: `tools/universe_registry.py` over
`data/universe_history/universe_snapshots.jsonl` (Mac cron snapshots the live
`symbols_{acct}_{long,short}.json` every 30 min; seeded with the 2026-05-08 s2 anchor +
2026-06-26 trc backups + trade-ledger monthly reconstruction — a LOWER bound before Jul-2026).
Point-in-time: `get_tradeable_keys(acct, at_ts)` / engine env `V8_UNIVERSE_AT` (patch staged,
`patches/PENDING_UNLOCK_universe_side_gating_20260718.md`). Off-universe sides may still be
RECORDED (campaign `_offuni`) for the curated-vs-random comparison, but never promoted.
NPZ coverage must include ALL of `symbols_tradier.json` (and crypto `symbols.json` later) so
historical rankings/tradeable keys can be derived at any backtest moment; gaps logged to
`data/universe_history/npz_missing_tradier.json`.

**§12.8 PER-KEY PARAM-RANGE STORE (USER 2026-07-18).** Every per-(symbol, side) test cell
persists to `data/param_results_stocks.db` (`tools/param_results_store.py`): baseline row
per key (gain/mo, side-aware b&h/mo, delta) + one row per (param, value) tested, ALWAYS with
full overrides_json + 4-file stamp. Derived per key×param: tested range, gain/mo-vs-baseline
range, edge_flag (HOPE_ABOVE/HOPE_BELOW/EDGES_DIVERGE = extremes not equal → test OUTSIDE the
range; CONVERGED = closed), suggested_next values, and INERT detection (no key moved ≥0.05
gain/mo pp → wiring suspect per §1 — FIX THE WIRING before pruning the param). Param pruning
for test-efficiency is allowed ONLY from the relevance ranking (max |Δgain/mo| across keys)
AND only after inert params are proven wired. Data is never deleted (dedupe via
`already_tested()` + central DB — nothing is ever re-run). XLSX export:
`data/reports/PARAM_BASELINE_STOCKS.xlsx` (Baselines/ParamRanges/Suggestions/Relevance).
Runner: `tools/persym_baseline_campaign.py` (S1 24/7 via cron + run_seq_test.sh; digest →
`data/reports/persym_campaign_digest.md` → morning email). Retired to `old/`:
`per_sym_trb_profiles.py` (imported deleted v8_quick_engine; replaced by the campaign).

**§12.9 STOCKS 4h = SESSION BARS (2026-07-18).** Live stock 4h was ALREADY session-anchored
(open/noon/close = 09:30/12:45/16:00 ET via `tradier_indicators.get_4h_bin`); the NPZ was
wall-clock UTC 4h — a parity break in EVERY `*_4h` stock field. `backtest_v8_precompute.py`
now session-bins 4h for MODE=tradier (crypto unchanged, 24/7 wall-clock is correct there).
NPZ regen required to take effect — regen AFTER the running campaign baseline completes, then
A/B (expect improvement; the old-4h arm results stay tagged by their stamp). Extreme-stop
options (frozen DC 4h/D, BB 4h/D/W + near-entry stops OFF) are STOP_PACK cells in the
campaign; engine reads `dc_low_{tf}`/`bb_{field}_{tf}` generically so D/W need no engine edit.

**§12.6 Culprit case law (what "terrible performance" has actually been, in order found):** dead driver loops
(force-open, _rotation_rsi2_loop); wall-clock gates inert in sim (MTF_ATR_TRAIL, BB_FROZEN_STOP); R1 stocks
mutated into a permanent ratcheting 5m stop; TOP_EXIT_LONG bare-velocity OR-gate (longs structurally couldn't
ride trends); dataclass-vs-module config reads (reentry daemon knobs silently defaulted); DELTA_ENTRY (the
proven winner's entry engine) disabled live by a pre-parity pool-average; MIN_IND=5 starving specific keys;
LS_RATIO×dead-shorts long starvation; NOLOSS floor 0.0 letting DELTA_EXIT close losers; phantom broker
successes (Finandy empty-fill). Pattern: code that LOOKS wired but never executes where it counts. Checklist:
verify the DRIVER, grep time.time() near gates, count attribution from trade JSONLs (never logs), confirm
module-vs-instance config reads, read broker response BODIES.

### §12.10 — PARAM MATRIX (USER MANDATE 2026-07-19): the full per-key × per-param ledger

**Every non-boolean param (1199 currently) gets a column group per (symbol, side) per stage** —
`<P>__tested` (value range, e.g. "30-50-70-90"), `<P>__delta` (Δ gain/mo range), `<P>__best`,
`<P>__suggest` (HOPE values outside the range) — across stages `baseline_ofat` / `per_sym` / `7d_trc`.
Source of truth: `param_results_stocks.db` tables `param_registry`, `stage_results` (long, full data),
`pooled_param_history`, `param_matrix`. Export: `data/reports/PARAM_MATRIX_STOCKS.csv.gz` (full width,
column groups SORTED BY BASELINE IMPACT = the pruning order) + `Matrix_Top` sheet (top 150) in
`PARAM_BASELINE_STOCKS.xlsx`. Rebuilt hourly (S1 cron :05, `tools/param_matrix.py all`), pulled to Mac :25.
Provenance rules: per-sym attributable deltas only from single-param tests; multi-param historical rows
carry `combo:`; pooled-only history carries `pooled:`; central-DB rows with NULL mode are EXCLUDED
(415K rows — no mode signal, crypto/tradier contamination risk; noted in build log, never silent).
Pruning params per symbol is allowed ONLY from this matrix's impact ordering after inert-wiring proof
(§12.8) — tests must shrink from 1199 params/sym to the relevant set, but every result stays recorded here.

### §12.11 — NEAR-ENTRY-STOP REMOVAL: evidence, overnight plan, live-flip protocol (2026-07-19)

**Finding (capture analysis, receipts in REWORK_RESULTS_STORE_20260718.md):** stocks baselines
underperform b&h ~90% because near-entry 5m exits (`MTF_ATR_TRAIL_5m_x2.0`, PPL, breakeven,
preemptive) scalp hours-long trades inside multi-month trends. Time-in-market: ROKU_L 3.3%,
ARM_L 0.1%, LLY_L 0.5%. A/B at n=78 keys: baseline +61.2% vs STOP_PACK NEAR_ENTRY_OFF_ONLY
**+185.6% (3.0x)**; shorts transform (AU_S +2.7→+15.2, AGI_S +2.4→+14.3). Independent
corroboration: t2_arm_queue `ts_atr_off` arm tests the same trail-off on 105 stocks.

**Overnight compute (Sun→Mon):** ts_atr_off batches PAUSED (driver dead until 13:15 watchdog;
resumable via .done markers). Two engine workers: main OFAT (forward-alpha) +
`tools/psc_priority_runner.py` (reverse-alpha, same run_symbol cache+stamps). Priority:
NEAR_ENTRY_OFF completion → DC_FROZEN_D → BB_FROZEN_W → `TRADIER_WT_EXIT_TFS_TRADIER="1h+4h+D"`
(exit-TF escalation) → remaining packs. New columns `time_in_mkt_pct` + `capture_vs_bh` in
key_baseline/param_cells (the ball-dropped metrics — REQUIRED in all future campaign reports).

**LIVE-FLIP PROTOCOL (Mon pre-open, HARD GATES):**
1. GATE 1 — evidence: NEAR_ENTRY_OFF complete on ALL 113 syms AND total-pnl ratio vs baseline
   ≥1.5x AND no catastrophic per-key regression (no key loses >5% that baseline kept).
2. GATE 2 — lock: `config_tradier.py` is LOCKED; flip requires user to say
   "unlock config_tradier.py". NO flip without it. NO route-arounds via override files.
3. The flip: `PARTIAL_PROFIT_LOCK_ENABLED`, `BREAKEVEN_DC_LOW4_ENABLED`,
   `EXIT_PREEMPTIVE_BREAKEVEN_ENABLED`, `MTF_ATR_TRAIL_ENABLED` → False (config_tradier only —
   crypto config.py UNTOUCHED). Backup first, py_compile, restart trb/trc before 13:30 UTC open,
   verify exits in decisions log no longer fire MTF_ATR_TRAIL/PPL reasons.
4. R1/R2/HEDGE_FAILED loss-exit paths UNTOUCHED (user exit-rules mandate) — this removes
   PROFIT-side near-entry churn only.
5. Rollback: restore backup, restart. Watch first 2h of open; regression = instant rollback.

**§12.12 — CRYPTO REPLICATION TODO (start ONLY after tradier changes are live + verified):**
1. Universe registry seeder for crypto: per-account (sym, side) sets from persym_final_book +
   account winner sets (inf 12-sym etc.); snapshot cadence as stocks.
2. `param_results_crypto.db` via mode-tagged store (one-line DB switch); campaign MODE=crypto,
   ACCOUNT=ang, START=2022-01-01 (bear+bull), LTF 3m, USDC-over-USDT klines, commission 0.08%/side.
3. Baseline pass: every (sym, side) × faithful Tier-2 + side-aware b&h floor + time_in_mkt_pct
   + capture_vs_bh from day one.
4. STOP_PACK equivalents: crypto R1 already uses 20-bar frozen dc_low — packs test dc_high
   (short mirror) + BB, TFs 4h/D/W; near-entry-off arm = PPL/breakeven/preemptive/trail off
   (config.py knobs) — SAME hypothesis, crypto validation required (params are OPPOSITE-prone;
   NEVER copy stocks values).
5. OFAT over crypto manifest (param_sweep_manifest_crypto) with artifact-stamp sidecars.
6. param_matrix MODE=crypto → PARAM_MATRIX_CRYPTO.csv.gz + Matrix_Top sheet + hourly cron.
7. compare-universe (curated vs random) for crypto accounts.
8. Chart integration: psc:: runs for crypto symbols on :5077 (crypto tab).
9. Session-4h N/A for crypto (24/7 — precompute already gates MODE=="tradier").
10. Live-flip protocol identical: evidence gates + LOCKED config.py unlock + restart + watch.

### §12.15 — THE HOLD-FLOOR PARITY BREAK + LEAN-BASELINE MANDATE (USER 2026-07-20)

**USER: "the 72hr hold is bullshit and has never even been applied — we never hold anything for
72 hours, so everything it has can be changed if that yields better results."** VERIFIED against the
live ledger (`data/history/tr[bc]/*.jsonl`, 499 round-trips): median hold **1.0h**, 42.7% under 30
minutes, **77.6% closed inside 72h**. `TRADIER_MIN_HOLD_MINUTES=4320` (+ `TRA_MIN_HOLD_MINUTES=1440`,
`BOUNCE_TOP_MIN_HOLD_MINUTES=1440`, `MIN_HOLD_MINUTES_TRADIER=30`) therefore governs the BACKTEST but
not LIVE — the engine has been simulating a strictly more constrained strategy than the one trading.
Same class as the crypto-BB-stop poison (§12.14.1). The 2026-04-27 "72h minimum hold" rule is
SUPERSEDED; all hold floors are free parameters, decided by measured gain/mo.

**Why it mattered:** with `TRADES_PER_SYM_PER_DAY_MAX=8`, the production config STRUCTURALLY forbids
the proven 5m-arrow system (holds minutes, ~9 trades/day → NVDA 9.57x b&h, MU 2.49x in
`tools/mtf_ladder_lab.py` on the plot-basis slopes). No exit-knob tuning could ever have reproduced
it; that is the mechanical origin of the "~2% of b&h" wall on every engine cell.

**LEAN-BASELINE MANDATE (supersedes OFAT-from-production-config):** one-at-a-time switch testing MUST
perturb a WORKING baseline, never a broken one. `persym_baseline_campaign.LEAN_BASELINE` opens the
throughput caps (all hold floors 0, cap 999, reopen 0) and the entry gates (`WT_DC_HTF_GATE=none`,
`HTF_ALIGN_REQUIRED_TRADIER=0`, `COMBINED_STOCH_GATE=100`, entry-score thresholds 0,
`MTF_ARMED_ENTRY_ENABLED=False`) with the trend-scalping exits off, long-only. Isolation arms
`LEAN_PLUS_{HOLD30,CAP8,HTFGATE,ALIGN2}` price each culprit individually. The 1759-param OFAT
(`PSC_SYMS=` scoping added) then adds every real config switch back ON TOP of LEAN → per-key
delta gain/mo → `PARAM_MATRIX_STOCKS` spreadsheet → relevance ranking → per_sym targets → new
universe-wide baseline. Never re-baseline on the production config while it sits at ~2% of b&h.

### §12.11-APPLIED (2026-07-19 21:15 UTC) + §12.13 TF-EXCLUSION ARM

**FLIP APPLIED.** Gate 1 passed at n=132 keys: baseline +128.4% vs NEAR_ENTRY_OFF +337.9%
(**2.63x**), worst regression ARM_SHORT +1.9→−3.9 (under the 5% line). User unlock granted
("unlock whatever needed"). config_tradier: 4 knobs → False (backup
`before_near_entry_off_flip_202607192112.py`), synced to S1, LOCKED_FILES row added.
Weekend tradier procs down by design (market-hours gate); `tradier_watchdog_cron` re-enabled
in Mac crontab (was disabled with a stale NO-LIVE-MAC comment — the missing startup path
behind the Jul-17/18 outage). Stack auto-starts Mon ~13:30 UTC with new config. Open-day
watch: MTF_ATR_TRAIL/PPL exit reasons must be ABSENT from decisions logs.

**Session-4h NPZ cutover (running tonight via `tools/psc_v2_chain.sh` on S1):** wait pack1
113/113 → stop v1 workers → `backtest_v8_precompute.py --all --mode tradier` (session-anchored
4h, verbatim port of live `get_4h_bin`: 09:30/12:45/16:00 ET bins) → HARD VERIFY: 4h timestamps
must be exactly {13:30, 16:45, 20:00} UTC (DST), abort cutover on >5% violations → cron +
runner switch to campaign `stocks_baseline_v2_s4h`. v1 pack A/B stays valid (both arms on old
NPZ); ALL new results (v2 baseline, packs re-run, OFAT, TF-exclusions) on session-4h NPZ =
live parity. The 7 missing NPZs (ALKT/ALMU/COE/HP/LITE/PRI/QRVO) are created by this regen.

### §12.14 — BAND-CAPTURE ERA (USER 2026-07-19 late): the ARM_LONG hiatus map + grey-band fixpack

**Mandate:** take the biggest b&h winners (ARM_LONG first; queue SNDK/RKLB/ASTS/MU/PLTR/LRCX/MRVL,
b&h +91%…+2573%), find every hiatus between per_sym results (capture ≈0–4% of b&h) and the b&h floor,
reach gain_vs_bh ≥ 2 by settings first, then per-trade band analysis: **in at the LOWER stdev/regression
band, out at the UPPER band whenever the channel slope is up for that TF, size ∝ slope.**

**The hiatus map (3 parallel audits, receipts in agent transcripts + capture/ARM_LONG.md):**
1. **Entry starvation/lag** — Tier-2 stocks entries come from `wt_dc_score_entry`≥45 gated by
   `WT_DC_HTF_GATE="4h_D"` + `HTF_ALIGN_REQUIRED_TRADIER=2`, and the scorer's dominant weight (2.25)
   rewards HIGH dc_position — a breakout-chaser that cannot buy a swing low. ARM entries were 8–21%
   late; curated-arm baseline had 0 trades. (`evaluate_open`/ENTRY_SCORE_THRESHOLD/SATOSHIT/LS_RATIO
   are INERT on this path — do not tune them expecting entry changes.)
2. **2nd-layer exit scalping** (post NEAR_ENTRY_OFF) — by summed giveback on ARM: GR_HTF_DIRECT_EXIT
   259pp (`GR_HTF_DIRECT_EXIT_SCORE=12` vs max achievable 15, fires pre-min-hold), SRS bb_1h 172pp
   (in the HTF-veto bypass tuple → fires through an up daily), PEAK_GIVEBACK 133pp (now loss-only),
   DYNAMIC_SCORE_COUNTER 60pp, WT_CROSSUNDER_FINAL (5m+15m + 1 HTF). NEAR_ENTRY_OFF alone left the
   mega-winners at capture 0–7% — these are the next wall.
3. **Backtest exit-parity gap** — engine V8 close paths skip live `HTF_TREND_VETO_ON_REDUCE`; live is
   partially protected where backtest is not (SRS/GAIN_EROSION bypass the veto in BOTH).
4. **Grey-band wiring rot** — `LR_BAND_ENTRY` (= the user's lower-band+slope entry) existed but: OFF,
   required `lrL_r2_{tf}` which live indicators computed then DROPPED (never emitted) → could never
   pass live; upper-band harvest knobs (`LR_BAND_HARVEST_*`) consumed NOWHERE; crypto
   `BAND_SLOPE_SIZING_V2` wired only in the quick-open path (all other opens unsized) and silent (no
   log); `lr_pctb_D` fix inverted the key mismatch (now live-active, backtest-inert — NPZ has only the
   Bollinger-alias spelling). NPZ `lrL_pct_b/slope/r2_{1h,4h,D}` are the ONLY real regression fields.

**FIXPACK APPLIED 2026-07-19 22:30 (LOCKED_FILES row "band-system-fixpack"):** lrL_r2 emission in both
indicator scripts; BAND_SLOPE_SIZING_V2 at ez_manage's central sizing chokepoint (QUICK-deduped) + log
lines both stacks; NEW `LR_BAND_HARVEST` upper-band profit-only exit wired in tradier_manage +
ez_manage behind `LR_BAND_HARVEST_ENABLED=False` (crypto always full-close — REDUCE==close). All
synced to S1; ez_indicators respawned; stocks stack picks up Mon 13:30 UTC.

**RIDE packs** (in `persym_baseline_campaign.STOP_PACKS`, runner `tools/psc_winner_band_runner.py`
grinding the 8 winners reverse-priority on S1): `RIDE_EXITS_OFF` (near-entry off + the 5 culprit exits
off) → `RIDE_DC_D` (+frozen-D stop) → `RIDE_BAND_HARVEST` (+upper-band full-harvest exit) →
`RIDE_BAND_FULL` (+`LR_BAND_ENTRY_ENABLED=True, R2_MIN=0.5, WT_DC_HTF_GATE=none,
HTF_ALIGN_REQUIRED_TRADIER=0` — bottom-band entries with HTF gates relaxed). Reference ceilings
(DIAGNOSTIC, `tools/band_capture_ref.py` + `tools/bt_band_bounce.py`): full-exit band trading alone
tops out ~0.46× b&h on ARM (in-market 30%) — the winning shape is core-hold + harvest/re-add; slope
multiplier adds ~+35% on ARM (42.11 vs 31.26 acc). Pooled 199-sym swing best so far 0.46× b&h /
pool_sharpe 0.59 → band timing must COMBINE with trend-riding, not replace it.

**§12.14.1 CROSS-CONFIG ENGINE POISON (2026-07-20, THE big one).** `run_simulation_tradier` read 15
knobs from the CRYPTO config module: crypto `BB_FROZEN_STOP_ENABLED=True/1h` put a 1h bb_lower
loss-stop under every stocks backtest that live stocks does NOT run (config_tradier=False) — the top
ARM killer in v1 trades and the reason DC/BB stop arms looked inert (shadowed). The sim also ran the
KILLED sentiment rebalancer (crypto attr undefined→True). FIXED: all reads → `tm_mod.config`
(LOCKED_FILES 2026-07-20). **Every pre-fix stocks Tier-2 number is engine-poisoned relative to live**
— internal A/Bs stay directionally usable, absolute levels do not. v2 (`stocks_baseline_v2_s4h`,
session-4h NPZ, fixed engine) is the re-baseline; §12.6 checklist gains: "grep getattr(config, inside
run_simulation_tradier — the tradier sim must read tm_mod.config." Also: `resolved_config.json` is
override-blind (snapshot diffs against the override-patched defaults) — treat it as reporting only
the NON-overridden diff until fixed. Band-entry calibration law: `lrL_r2_D>=0.5` co-occurs with
lower-band+rising-slope on ZERO ARM bars — r2 gates must be per-TF calibrated (D: use 0.0–0.2), never
copied from the 0.7 default.

**Results digest (stocks) revised:** campaign `cmd_report` now writes the B&H WINNERS capture
scoreboard (`data/reports/stocks_bh_capture.json` + table in `persym_campaign_digest.md`; capture<1 =
DEFECT), pulled to Mac at :25, rendered as the lead section of the results-digest email.

**§12.13 TF-EXCLUSION ARM (USER 2026-07-19):** per-key cells `TF_EXCLUDE_{5m,15m,1h,4h,D,W}` —
each removes ONE timeframe from every TF-list knob the stocks engine consults (R2_TF_LIST,
WT_EXIT_TFS_TRADIER×2, GOLDEN_RULE_ACTIVATION/ENTRY_TF_LIST, GR_V5_HTF/LTF_TFS,
MTF_ARMED_HTF_LIST, STDEV_BREAKOUT/RETEST/BOUNCE lists, SQUEEZE_FIRE_TFS — 12 knobs, only
those containing the TF). Config-level (live-appliable), NOT data blanking; 5m bars stay the
base. Wired into cmd_ofat cells (after STOP_PACKs) and the v2 priority runner. Purpose: prove
per symbol whether dropping a TF improves gain/mo before the per-sym baseline is finalized;
results land in param_cells/matrix like any other param.

### §12.14 — TRB/TRC AND INF/MEN LIVE PARITY ARMS (USER 2026-07-19)

**Why:** user ordered two live accounts each redirected onto an already-tuned sibling
account's symbol universe (per_sym baseline + rolling reconfig already applied), so the two
accounts in each pair become a genuine live A/B — same symbols, same settings-derivation
methodology, independently executed — instead of comparing apples to oranges.

**§trb/trc parity (stocks).** Before this change trb (57L/72S) and trc (24L/89S) traded fully
disjoint symbol sets, even though both already ran through the identical daily pipeline
(`tradier_hourly_reconfig.py --accounts trb` at 11:00 UTC / `--accounts trc` at 11:20 UTC,
S1 cron, Mon-Fri) — per_sym baseline (`per_sym_active_config.json`) + 14-day rolling window
reconfig (`data/hourly_reconfig/{trb,trc}/active_config.json`, read live by
`tradier_manage._cfg()`). The only real difference was the symbol source. Fixed in
`tradier_rankings.py` (~line 2414, refreshes every ~10 min): `symbols_trc_long`/
`symbols_trc_short` now = `list(symbols_trb_long)`/`list(symbols_trb_short)` (trb's fully
processed lists, after `TRADIER_MANDATORY_LONG_TRB` + news-injection merges) instead of an
independently-computed pool. **No other file needed editing** — `tradier_manage.py`'s
`is_symbol_tradeable()` reads `symbols_trc_{long,short}.json` fresh (30s cache), and the
existing daily cron for trc will naturally recompute per_sym+7D for the new (mirrored)
universe on its own schedule. The stale in-code comment at `tradier_manage.py:663-682`
calling trc a "STATIC control arm, no 7D overlay" is **superseded** — trc has always
received symmetric per-account 7D treatment via the generic `account_key` branch in `_cfg()`;
only the symbol universe differed, and that gap is now closed.

**Continuity action — flatten trc at Monday market open:** per user instruction, all trc
positions must be closed at the 13:30 UTC open so trc starts clean on the mirrored universe
+ its own fresh 7D reconfig rather than carrying legacy positions opened under the old,
disjoint symbol list. See operational note in memory
`project_trc_flatten_market_open_20260719.md` for the exact plan/status if this session
ends before it executes.

**§inf/men parity (crypto).** Before this change `inf` traded a continuously-regenerated
momentum-ranked list (`symbols_inf_long.json`/`symbols_inf_short.json`, rebuilt every cycle
by `ez_rankings.py` from 15m/3m extreme movers, ~600-symbol scan universe) rather than a
static per_sym-tuned universe. Redirected onto `men`'s curated 79-symbol list (both sides,
since men trades LONG+SHORT on every symbol per its account-side config) via:
1. **New files** `symbols_men_long.json` / `symbols_men_short.json` (mirror of
   `symbols_men.json`, not locked, created directly — 76/79 have NPZ on S1; BANANAUSDT/
   FETUSDT/SIRENUSDT lack NPZ and are silently skipped by `load_account_syms`'s existing
   NPZ-existence filter, same as it does for any account).
2. **`config.py`**: `SYMBOLS_INF_LONG`/`SYMBOLS_INF_SHORT` repointed to the new files — this
   is the entry gate for LIVE trading (`get_tradeable_position_keys_for()` in `ez_manage.py`
   builds inf's entire watchlist from `trade_manager.symbols_inf_long/short`, which load
   straight from these config paths). `ez_rankings.py` still writes the OLD
   `symbols_inf_long/short.json` every cycle — now orphaned/unread, kept for a one-line
   revert.
3. **`per_sym_7d_agent.py`**: `load_account_syms()`'s `'inf'` entry repointed to the new
   files, so the existing daily 7D cron (`12 * * * *` on S1) tunes men's symbols and writes
   to `data/hourly_reconfig/inf/active_config_7d.json` — the exact path `ez_manage.py`
   already reads live (hardcoded `_ezm_inf_7d_path`, no code change needed there).
4. **S1 crontab** (infra, not a source file): the `--account inf` cron line carried a
   hardcoded `--syms SKLUSDT,...` 12-symbol override (the old "INF dedicated winners" set
   from the 2026-07-08 GAINMO change — see the locked-file row) that bypassed
   `load_account_syms()` entirely. Removed so the cron actually uses the new source.
   Backup: `backups/s1_crontab_before_inf_persym7d_fix_202607192107.txt`.

**Verified:** `ez_manage.py --account inf` restarted (SIGTERM → clean shutdown sequence →
`run_with_watchdog.sh` auto-relaunch, new PID) and confirmed `config.Config.SYMBOLS_INF_LONG`
resolves to `symbols_men_long.json` (79 syms) post-restart. `tradier_manage`/
`tradier_rankings` were not running at edit time (Sunday, market-hours gate in
`run_with_watchdog.sh`) — they pick up the trc fix automatically at the existing Mon 13:20
UTC pre-market cron launch (already re-enabled 2026-07-19 per §12.11-APPLIED), no manual
restart required.

**Working in tandem going forward:** both arms are now fully automated and require no
recurring manual intervention — trb/trc via the existing 10-min rankings refresh + daily
11:00/11:20 UTC reconfig cron; inf/men via the existing hourly `:12` per_sym_7d_agent cron
feeding `ez_manage.py`'s always-on 7D-boost read. Compare live results going forward via
`data/history/{trb,trc}/` and `data/history/{inf,men}/` (the real ledgers) — NOT `/decisions/`
counts. If either sibling in a pair should ever need to diverge again (e.g. a proven strategy
change validated for one but not the other), revert the specific file listed in the
LOCKED_FILES.md row for this change, not the whole pair.

---

### §12.15 — REENTRY CONFIRMATION BYPASS AND SHORT B&H INVERSION FIX (2026-07-20)

**Short B&H Benchmark Inversion Bug Fix.** Fixed `tools/capture_analysis.py` (line 133, changed `bh_key > 0` to `bh_key != 0` in ratio divisor check) and `tools/reopt_loop.py` (line 219, removed `abs(b)` from divisor) to allow negative `bh_key` values so short outperformance is properly signed (a positive gain in a declining market or short strategy outperforming rising B&H shows correctly).

**Guaranteed Re-entry Trend-Resumption Confirmation Bypass.** Implemented confirmation-gate bypass when a position runs in-favor past the exit price by `REENTRY_BYPASS_CONFIRMATION_THRESHOLD_PCT` (default `0.002` = 0.2%). Bypasses WT/Stoch indicators to force reentry immediately when a strong trend resumes.
- **`vec_decisions/guaranteed_price_cross_reentry.py`**: Added bypass checks to both scalar and vectorized paths.
- **`ez_reentry.py`**: Passed candidate's `exit_price` from processing loop to `check_reentry_confirmation()`.
- **`backtest_v8_engine.py`**: Passed order exit price in `_bt_reduce_price` tracking on reduction/close event hooks to `_v8_reentry_cooldown_check`, resolving reentry starvation in simulated backtests.
- **Configs**: Declared `REENTRY_BYPASS_CONFIRMATION_THRESHOLD_PCT: float = 0.002` in both `config.py` and `config_tradier.py`.

**Empirical Sweep Verification (2026-07-20).** Executed A/B sweep on S1 (SOLUSDC, 2026-06-01 to 2026-07-20, `ang` account, price cross guarantee enabled, recent reduction guard disabled to isolate reentry path):
- **Baseline (Bypass Disabled/999.0%)**: pool_sharpe=**`+1.1336`**, acc_gain=**`+4.512%`**, trades=**`12`**.
- **Enabled (Bypass at 0.2%)**: pool_sharpe=**`+1.5452`** (+36.3% delta), acc_gain=**`+6.885%`** (+52.6% delta), trades=**`18`** (+50.0% delta).
- **Verdict**: Proven positive gain delta. Re-entering strong continuations immediately when price crosses in-favor solves reentry starvation and significantly boosts profit/Sharpe.

---

## §13 — THE PILOT → CATEGORY → COMBINATION → FLEET-OUT PLAN (USER MANDATE 2026-07-22)

**The plan in one line:** fill EVERY field for ONE key (MU_LONG), group the switches into
categories, search combinations *within and across* categories, keep only what survives, and
only then spend money running the surviving shortlist across the other 128 keys.

This ordering is not a preference — §13.4 shows the full grid on every key is **89,913
core-hours** and is unaffordable on any hardware we would rent. The pilot is what makes the
rest of the programme possible.

### §13.1 — Phase 1: fill one key completely (RUNNING)

- Focus is held in `data/matrix_focus.json`; `tools/matrix_focus.py {status,advance,symbol,side}`.
  `advance` moves on ONLY at ≥99.5% of the work-list, so the fleet cannot drift mid-key.
- Order: **MU_LONG → HAO_SHORT → NVDA_LONG → VT_LONG**. The watchdog reads BOTH symbol and
  side from focus state — HAO is a SHORT key and a hardcoded `--side LONG` silently sweeps the
  wrong side.
- Work-list = `param_matrix_daemon.all_cells(manifest, all_tiers=True, side=<FOCUS SIDE>)`,
  currently **3,485 units/key** after pruning.
- **Only ENGINE-tier cells count as filled.** A Tier-1 vec screen does not fill a cell (§13.5).

### §13.2 — Phase 2: categories

The exporter already groups every switch into **Entry / Exit / Sizing / Other**
(`export_switch_matrix_xls.py:group_of`, regex on the switch name) and writes one sheet per
group. Those are the starting categories; refine them only with evidence, and add the
orthogonal axis that matters as much as the category:

- **TIMEFRAME** — most switches carry a TF suffix (`_5m/_15m/_1h/_4h/_D/_W`). A switch that
  helps on D and hurts on 5m is not one result, it is two. Group by (category × timeframe).

Ranking inside a category uses `tools/param_shortlist.py`, which classifies every switch as
**PROMOTE / WATCH / DEGENERATE / NO_EFFECT / INCOMPLETE** from the pilot keys and prints the
exact full-universe cost of the PROMOTE set. PROMOTE requires sign-agreement across pilot keys
AND a median lift floor — a switch that helps MU and hurts HAO is per-key (WATCH), not global.

### §13.3 — Phase 3: combinations (the hard part)

One-at-a-time (OFAT) results do NOT compose: switches interact, and the sum of individually
positive knobs is routinely worse than any of them alone. `tools/combo_search.py` does the
greedy best-first stack with leave-one-out and TF-ablation probes.

**Rules for the combination phase:**
1. Combine only from the **PROMOTE** set. Stacking WATCH/DEGENERATE switches manufactures
   overfit noise.
2. Search **within a category first** (best Entry stack, best Exit stack, best Sizing stack),
   then across categories. The within-category winners are far fewer, so the cross-category
   search stays tractable.
3. Every stack is scored against the SAME baseline as the OFAT cells, and every probe writes a
   cell with its full `overrides_json` — a stack without its recipe is a lie by omission (§12.5).
4. **Leave-one-out is mandatory** before promoting a stack: a member that does not degrade the
   stack when removed is not earning its place and must be dropped.
5. A stack must clear the **b&h floor** (§12) for its key. Beating the OFAT baseline is not
   the bar; beating buy-and-hold is.

### §13.4 — Phase 4: fleet-out, and the rent-a-server decision

Measured 2026-07-22: **one Tier-2 unit = ~12 min of one core** (9m11s isolated; ~50 min once
the baseline actually trades — a richer baseline is ~5× more expensive per cell).

| scope | core-hours | 12 cores (S1) | ~48 cores (1 rented box) | ~240 cores (5 boxes) |
|---|---|---|---|---|
| **full grid × 129 keys** (3,485/key) | **89,913** | 312 days | **78 days** | 15.6 days |
| shortlist 300 cells/key | 7,740 | 26.9 days | **6.7 days** | 1.3 days |
| shortlist 150 cells/key | 3,870 | 13.4 days | 3.4 days | 0.7 days |
| shortlist 60 cells/key | 1,548 | 5.4 days | 1.3 days | 0.3 days |

**Decision rule — do NOT rent hardware to run the full grid.** 78 days on a rented box is not
a plan. Renting is correct ONLY once the pilot has produced a shortlist: at 150–300 promoted
cells/key the whole universe is 1–7 days on a single machine, which is trivially affordable.
The size of the PROMOTE set is therefore the number that decides the budget — get it from
`param_shortlist.py` before provisioning anything. (Hourly pricing changes; verify current
rates rather than trusting a figure written here.)

**Before renting, confirm the target machine reproduces a known cell bit-for-bit** — same NPZ,
same 4-file stamp, same trade list. A fleet-out that silently diverges from S1 produces 129
keys of numbers nobody can compare to anything (§1).

### §13.5 — Hard limits found while building this (do not re-litigate)

- **Tier-1 cannot fill a switch grid.** `v8_vec_sweep.SweepConfig` implements only **164 of the
  921 sweepable params (18%)** — a ceiling of **581 cells/key**. Run over the full manifest it
  answers `unknown SweepConfig knob` and REFUSES (writing no cells, correctly). Making Tier-1
  screening real means porting the missing **757** knobs into the vec engine — that is
  engineering work and is the single highest-leverage project available, because it converts
  every future key from a 40-hour grind into hours.
- **Never difference across tiers.** A Tier-1 result minus a Tier-2 baseline measures the gap
  between two engines, not the knob (it printed a phantom +1.3917 %/mo on 495 MU_LONG cells).
  `param_cells.tier` exists for this; the exporter takes `--tier {ENGINE,VEC}` and never mixes.
- **Symbol batching is rejected** (measured): 4 symbols in one engine invocation = ~29 min vs
  ~36.7 min for 4 singles (~20%), at 1.4GB RSS vs 632MB, AND one invocation shares capital /
  position slots / cross-symbol ranking, so per-symbol trades would not match the single-symbol
  baselines every existing cell was built against.
- **A baseline that does not trade cannot test anything.** The `overrides={}` campaign baseline
  gives MU_LONG 19 trades / 0.13% time-in-market, NVDA_LONG 1 trade, VT_LONG **0**. That is why
  ~80% of cells came back `inert` — with no trades, no knob can bind, and a 0-trade key's grid
  is inert by construction. Check `key_baseline.trades` before believing any grid.
- **Prune only on wiring facts**, never on a small delta: `tools/prune_useless_knobs.py`
  (RECONNECT = every value bit-identical to baseline; DEGENERATE = values differ from baseline
  but not from each other). It MUST NOT prune a sub-knob of a feature whose master `_ENABLED`
  is False — the first run flagged all four `WT_3M_FORCE_OPEN_*` knobs as unwired when they
  were merely gated off, i.e. it would have deleted exactly the knobs that matter once the
  feature is switched on.
- **The spreadsheet is a generated artifact.** `param_cells` fills continuously but
  `SWITCH_MATRIX_TRB.xlsx` only changes when the exporter runs; S1 had NO cron doing that, so
  the sheet sat still while the DB grew underneath it. It now runs every watchdog cycle.
- **Operational**: `run_symbol` blocks while free RAM < `--min-avail` (default 8000MB, a figure
  from old ~10.8GB multi-symbol runs; a single-symbol run is 527–632MB) — at 14 workers the
  fleet parked itself in its own guard, alive and doing nothing. Orphaned engines survive every
  worker restart and re-parent to systemd (~600MB each; 29 found in one sweep). **Do not restart
  the fleet to "check on it"** — each restart orphans in-flight engines and resets a 9–15 min
  unit clock; verify with `SELECT MAX(ts)` instead.

### §13.6 — Re-enable checklist

The lab / vec / combo / hourly-export lanes are OFF while the fleet is single-key. Turn them
back on once the matrix is complete for ALL symbols AND per_sym settings exist for every key —
`tools/watchdog_reenable_check.py` checks both mechanically (exit 0 = re-enable now). The
restore list is a banner at the top of `watchdog_lab_matrix.sh`; nothing was deleted.

### §13.7 — THE EXPOSURE LADDER: test DOWN from b&h, never UP from zero (USER MANDATE 2026-07-22)

**This supersedes the testing *sequence* in §13.1–13.3 (the pilot→category→combination structure
stands; the order in which a key's switches are tested does not).**

The OFAT grid measured every knob against a baseline that is out of the market **99.87%** of the
time (MU_LONG: 19 trades / 2.3yr / 0.13% time-in-market vs b&h +633%). In that regime almost
nothing binds — which is precisely why ~80% of cells came back `inert`. In the user's words, it
"works backwards and starts with 0.1% time in the market, which is absolutely useless."

**The wt_5m cross alone sits at ~50% time-in-market** — the right ballpark. It is lagging and
churning; the remaining ~1,600 switches exist to find the *right* 50–80%, not to claw up from
zero. So the ladder runs subtractively:

| stage | config | what it establishes |
|---|---|---|
| **0** | **ALL exit knobs OFF** (91 `*_ENABLED` flags False) + wt_5m cross entry ON | You never leave a position ⇒ ~100% time-in-market ⇒ **the return IS b&h**. This is THE FLOOR every later config must beat. |
| **1** | exits back on **ONE AT A TIME**, sweeping each one's params | An exit earns its place only if it holds time-in-market in the target band (default **70–80%**) **AND beats b&h**. An exit that cuts exposure without beating b&h is destroying money you'd have made doing nothing. |
| **2** | then all **entry** paths OFF (79 flags), on one at a time, sweeping each | Same test, applied to entries. |

`tools/exposure_ladder.py {stage0,stage1,stage2,report} --symbol MU --side LONG`. Rows land under
campaign `<CAMPAIGN>__ladder`, tier='ENGINE', full `overrides_json` — never mixed with the OFAT grid.

**Stage 0 is also a free diagnostic.** If disabling every exit flag does NOT reproduce ~100%
time-in-market and ~b&h, then some exit path is **not behind a flag** (or entries are being
blocked upstream). Find that before tuning anything — the tool prints an explicit warning below
90% TIM. Nothing in the grid is meaningful until stage 0 lands on the floor.

**Scale**: stage 1 is **135 units** for a key (91 exit knobs × their swept values), stage 2 is
similar — versus 3,485 blind cells. The ladder is both the more meaningful search AND ~25× smaller,
which is what makes the fleet-out economics in §13.4 work. **Run this sequence for every ticker.**

### §13.8 — THE EXIT INVENTORY MUST BE COMPLETE BEFORE ANY NUMBER MEANS ANYTHING (USER 2026-07-22)

**"If MU_LONG all_exits_off does not produce b&h you need to first move the exit that caused it
to exit to the exits! You cannot start calculating anything until you have all exits on the
exits sheet."**

Stage 0 is therefore not just a floor — it is the **completeness test for the exit inventory**,
and it is run as a convergence loop:

```
run stage0  ->  did it reach ~100% TIM / ~b&h ?
   no  -> `exposure_ladder.py verify` lists the exit_reason FAMILIES that still fired
       -> find each one's controlling switch in the code
       -> add it to the inventory (it now appears on the Exit sheet and can be switched off)
       -> re-run
   yes -> the inventory is COMPLETE. Only now may stage 1 begin.
```

**Three switch shapes exist and NONE is inferable from the name.** Each was found the hard way
by a stage-0 run that failed to reach b&h:

| shape | disable with | found via |
|---|---|---|
| boolean `X_ENABLED` | `False` | — |
| boolean **without** `_ENABLED` | `False` | `STRUCTURAL_RANGE_SHIFT_EXIT` produced **100% of 1,310 closes**; only 92 of 144 boolean exit knobs end in `_ENABLED` |
| **string TF/TYPE** | `"None"` | `LONG_STRUCT_EXIT_TF='D'` → `HYBRID_STRUCT_EXIT_D`, **69 of 149** closes; `MTF_DC_REJECT_EXIT_TF='1h'` is also the exit churning MU flat **live** |

`REQUIRE*` knobs are **conditions** on an exit, not switches — forcing them loosens rather than
disables. Skip them.

Progression of the off-set as the inventory converged: **92 → 141 → 163** switches, and MU_LONG
stage 0 moved **26.9% TIM/+222% → 40.0% TIM/+318.93%** against b&h +632.98%. Never enumerate
switches by naming convention — read the real config types (`_cfg_bools()` / `_cfg_strs()`).

**The Ladder sheet** in `SWITCH_MATRIX_TRB.xlsx` shows this live: one row per config in time
order, with time-in-market, gain, b&h, vs-b&h, gain/mo, pool_sharpe and trades, and every row
that improved on the running best highlighted — so the climb from "no exits = b&h" through each
exit added and adjusted, then each entry, is readable top-to-bottom as it happens.

---

## §14 — REPLICATING THE trb SYSTEM IN THE CRYPTO (ez_) UNIVERSE (USER MANDATE 2026-07-22)

The stocks side now has a working method: a proven exit inventory, a floor at 100% in-market, a
ladder that earns every exit back, a knob registry, and a red-cell agent that says *why* a knob
is dead. Crypto has been left bungling. **Prove it on stocks first, then replicate — do not run
both in parallel and debug two systems at once.**

Order matters: every step below depends on the one above it, and skipping to the sweep is what
produced 16 hours of numbers measured against a baseline that never traded.

### §14.1 — Preconditions (do not start until ALL are true)
1. MU_LONG has a complete stage0→stage3 ladder and a promoted baseline (§13.7).
2. `data/reports/RED_CELLS.md` shows **GLOBAL_ONLY = 0** for the knobs in that baseline — a
   result from a knob whose per-symbol override is ignored is not reproducible per symbol.
3. HAO_SHORT stage0 = `closes=0` (done 2026-07-22 — the inventory holds on the short side).
4. `tools/watchdog_reenable_check.py` passes, or the suspended lanes are consciously left off.

### §14.2 — The steps, in order

| # | step | tool | done when |
|---|---|---|---|
| 1 | **Build the crypto knob registry** — same vocabulary, both modes | `knob_registry.py build` (already emits `crypto`) | `data/knob_registry.json` has crypto families/roles/off_values |
| 2 | **Audit crypto half-wiring** — master per-sym, sub-settings global | `knob_registry.py audit` | the 5 crypto half-wired families are fixed (`getattr` → `_psym_get`) |
| 3 | **Port the CRYPTO_ONLY / STOCKS_ONLY knobs** | `red_cell_agent.py once` | each knob reads in the system that is supposed to have it |
| 4 | **Build the crypto exit inventory** | `exposure_ladder.py` with crypto `EXIT_PAT` + `_cfg_bools/_cfg_strs` pointed at `config.Config` | all four switch shapes enumerated (bool, non-`_ENABLED` bool, string TF, numeric threshold) |
| 5 | **stage0 per key: `closes=0`** — the completeness test | `exposure_ladder.py stage0 --mode crypto` | 100% in-market, return == b&h, for ONE long key and ONE short key |
| 6 | **stage1/2/3 ladder** on the pilot key | `exposure_ladder.py stage1..3` | a promoted baseline that beats b&h |
| 7 | **Shortlist** the switches worth sweeping universe-wide | `param_shortlist.py` | PROMOTE set sized (this is the number that decides the rented-box budget, §13.4) |

### §14.3 — What is genuinely different in crypto (do not copy blindly)
- **Base TF is 3m, not 5m.** The WT trigger knob is `WT_3M_FORCE_OPEN_*` in both, but crypto
  actually reads `wt1_3m` while stocks read `wt1_5m` (`WT_FORCE_OPEN_TRIGGER_TF`).
- **Per-sym resolution differs**: stocks use `tradier_manage._cfg()`, crypto uses
  `ez_manage._psym_get()` / `_psym_sps()`. The red-cell agent already understands both.
- **Config class differs**: `config.Config` (crypto) vs `config_tradier.TradierConfig` (stocks).
  Read the CLASS attr, never the module — a bare module `getattr` is what silently pinned the
  stocks round-trip cost to a hardcoded 0.05 (§13.5).
- **Round-trip cost is NOT the same**: crypto 0.08%, stocks 0.06% (measured 2026-07-22; stocks
  churn far cheaper than crypto, so a cost assumption copied across modes is a lie).
- **Crypto trades 24/7**; there is no market-hours gate, so time-in-market bands differ and the
  70–80% target from §13.7 must be re-derived, not assumed.
- **USDC-over-USDT policy** applies to symbol naming everywhere (CLAUDE.md).

### §14.4 — Then, and only then, the rented box
Once BOTH systems have a proven per-key baseline and a shortlist, rent the box for the mega
sweep (§13.4). The decisive number is the size of the PROMOTE set, not the number of keys:
full grid × 129 keys = **89,913 core-hours (78 days on one box — never do this)**; a 150–300-cell
shortlist = **1–7 days**. Verify the rented machine reproduces a known cell bit-for-bit (same
NPZ, same 4-file stamp, same trade list) before trusting anything it returns.

---

## §15 — 2026-07-24 recovery correction

The claims above that MU_LONG/HAO_SHORT stage 0 was complete using `closes=0` are superseded.
Zero closes can mean either “opened and held” or “never opened.” The repaired contract requires
an observed open, zero real closes, a final MTM trade, at least 99% exposure, and return
reconciliation.

The implementation status, invalid-result inventory, runner choice, vector/Tier-2 boundary,
matrix-description schema, interaction-aware beam/ladder method, rollback steps and later
crypto port are maintained in
[`STOCK_BACKTEST_RECOVERY_20260724.md`](STOCK_BACKTEST_RECOVERY_20260724.md).

Current runner rule:

- `backtest_v8_engine.py` is Tier-2 decision evidence;
- `v8_vec_sweep.py` and `tools/vec_exposure_ladder.py` are Tier-1 shortlist tools;
- `per_sym_engine_stocks.py`, `per_sym_vec_engine_stocks.py` and historical profiles are not
  substitutes for Tier-2;
- vector candidates never become accepted configurations without a matching Tier-2 replay.

### §15.1 — Stocks 5m history and provenance contract

The stock provider exposes only limited native 5m history. Older 5m execution bars therefore
have to be interpolated from their **containing** 15m bars; there is no honest workaround for the
missing archive. This is accepted historical input and not an automatic test failure. It is a
disclosed within-parent approximation, distinct from accidentally exposing later 1h/4h/D bars.

The non-negotiable rules are:

1. Every native 5m bar collected from now on is retained permanently. Cleanup, refresh and
   backfill jobs may merge or repair bars but may never truncate the accumulated native history.
2. Native 5m observations replace interpolated bars on exact timestamp overlap.
3. Every NPZ carries `synthetic_5m` provenance (`0=native`, `1=15m interpolation`), and reports
   disclose the native/interpolated percentages for the tested window.
4. Each synthetic trio may use only its containing 15m OHLCV bar, never a later parent.
   `synthetic_5m_parent_close_ts` records the unavoidable 0/5/10-minute within-parent lag.
5. Strategy comparisons use the same frozen data snapshot and provenance mix. A result is not
   invalid merely because its older execution history is interpolated.
6. For a fixed symbol/history endpoint, the native bar count cannot fall after refresh. A
   decrease is a retention regression and blocks regeneration/promotion.

`backtest_v8_precompute.py` implements the hybrid merge and
`tools/backtest_data_contract.py` audits/discloses `synthetic_5m_pct`.

The first corrected MU_LONG Tier-2 floor opened, held to final MTM, and measured 99.97%
time-in-market. Its legal first RTH entry differs from the raw NPZ's premarket first bar, so
reports now show both first-tradable B&H and raw NPZ B&H rather than comparing the engine to an
unfillable benchmark.

The last real-entry zero-trade defect was repaired on 2026-07-24: the Tradier queue's OPEN
branch accessed `order["position_side"]` before constructing `order`; the broad queue exception
handler turned that `UnboundLocalError` into a silent `False`. Use the already-parsed
`position_side` and propagate `OrderQueue.add_order()` refusal details. A frozen MU_LONG July
replay then changed from 0 opens / 0% exposure to 7 long opens, 7 real closes, +3.54%, and
6.1445% exposure. That is a connectivity proof, not an accepted recipe: it remains well below
the target exposure band and has not been promoted or loaded into a live process.

Stage-0 seeding must also be verified by `entry_reason=V8_LADDER_INITIAL_BH_SEED`, not merely
by one open/MTM. HAO_SHORT exposed a refused seed followed by a later ordinary strategy open:
the stale artifact looked like a hold but measured only 86.7275% exposure. The repaired
test-only seed bypasses strategy entry gates, requires a successful internal fill before
marking itself seeded, and fresh HAO_SHORT proof is now one open, zero real closes, one MTM,
100.0000% exposure, +99.87% net versus +99.8745% gross tradable short B&H.
VT_LONG independently passes the same contract with one open, zero real closes, one MTM,
99.9855% exposure and +34.33% engine P&L.

The first post-floor HAO_SHORT replay also demonstrates why Tier-1 never promotes directly:
vector `DC_LOW4_STOP_ENABLED=True` estimated +276.86% at 82.1617% exposure, while faithful
Tier-2 returned +0.61%, 16 real closes and 7.6855% exposure. The cell is tested and gray
(discarded), not red: the live path fired, but the vector re-entry/fill approximation lacked
parity.

MU_LONG confirms the same rule for entries: `MTF_ARMED_ENTRY_ENABLED=True` looked positive in
the older matrix combination, but isolated Tier-2 replay returned -0.93%, 32 closes and 0.3927%
exposure. `DC_LOW4_STOP_ENABLED=True` was inert on the clean floor. Both remain gray until a
new interaction recipe gives a reproducible B&H-relative improvement.

`GOLDEN_RULE_REQUIRE_ACTIVATION=True` and `False` produced the exact same Tier-2 fingerprint
(-0.93%, 32 closes, 0.3927% TIM), so this is red/reconnect rather than gray performance. A
numeric or boolean switch cannot be considered tested until its two-value fingerprint moves.
`BB_PULLBACK_GATE_ENABLED=False` produced that same fingerprint and is likewise red/reconnect
in the isolated entry lane.

The entry ablation contract also had to disable both numeric WT-DC threshold aliases. Without
that, every named entry replay still admitted `WT_DC_ENTRY_*` signals and identical fingerprints
were expected. `tools/exposure_ladder.py` now sets both thresholds out of reach before testing
an entry path.
