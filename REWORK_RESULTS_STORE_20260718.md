# RESULTS-STORE + BASELINE REWORK — 2026-07-18 (stocks first; crypto application plan at bottom)

USER MANDATE: rework the XLS/database storage of binance-sandbox test results per symbol and
group (baseline); point-in-time symbol/side universes; per-symbol param ranges with hope
suggestions; per_sym retest; 7D on trc; extreme stops; session 4h; Bible compliance; 24/7 +
email digests; param relevance pruning only after wiring proof; script hygiene to /old/.

## What was found (audit, 4 parallel explorations)

1. **Universe**: live is strictly per-(symbol, side) via `symbols_trb_{long,short}.json`
   (gate `tradier_manage.is_symbol_tradeable` ~10414). Backtests were testing a "random"
   universe: `per_sym_20d_agent_stocks` = union of both files, BOTH sides simulated;
   `backtest_v8_engine` = NPZ-directory glob + `_always_tradeable` override that ignores
   side (+ one allowlist block setting dead attr names `symbols_trb_long` instead of
   `symbols_long_trb`); `v8_vec_sweep` = coordinator sets `V8_VEC_SWEEP_BYPASS_ACCT_FILTER=1`.
   **No git history of the universe files exists** — only anchors: s2 backup 2026-05-08,
   trc backups 2026-06-26, current (Jul 16). (.history/ has 2 more — untouched, access
   prohibited without permission.)
2. **Storage**: nothing recorded the per-symbol RANGE of params tested — every store keeps
   only the single winning config. `data/param_workbook.csv` is global-per-param only.
   XLSX exports were stale (Mar–Apr).
3. **4h**: live stock 4h was ALREADY session-anchored (09:30/12:45/16:00 ET,
   `tradier_indicators.get_4h_bin`); the NPZ was wall-clock UTC (12:00–16:00/16:00–20:00) —
   a parity break in every `*_4h` stock field.
4. **Stops**: frozen DC/BB stop machinery exists in the engine and reads
   `dc_low_{tf}`/`bb_{field}_{tf}` generically → D (DC/BB) and W (BB) testable via
   overrides, no engine edit. Near-entry stops to disable in variants:
   PARTIAL_PROFIT_LOCK, BREAKEVEN_DC_LOW4, EXIT_PREEMPTIVE_BREAKEVEN, MTF_ATR_TRAIL.
5. **Broken**: `per_sym_trb_profiles.py` imported deleted `v8_quick_engine` → could not run.

## What was built (all synced Mac↔S1, md5-verified)

| Piece | File | Status |
|---|---|---|
| Point-in-time universe registry | `tools/universe_registry.py` + `data/universe_history/universe_snapshots.jsonl` | seeded (38 snapshots: s2 2026-05-08 anchor, trc 06-26, ledger monthly reconstruction, current); Mac cron `*/30` snapshots on change |
| Per-key param-range store | `tools/param_results_store.py` → `data/param_results_stocks.db` | baselines + param_cells (full overrides_json + 4-file stamp per row, Bible §12.5); range/HOPE/EDGES_DIVERGE/inert analytics; `already_tested()` dedupe vs own DB + central DB; XLSX export |
| 24/7 campaign runner | `tools/persym_baseline_campaign.py` | S1 crons installed: `*/30` baseline→OFAT (6h slices, resumable, run_seq_test.sh serialized), hourly `report` → digest + XLSX |
| Stocks session-4h precompute | `backtest_v8_precompute.py` `_tradier_session_4h` | code live on S1; verified 3 bars/day @13:30/16:45/20:00 UTC; **NPZ regen deferred** until baseline completes (never mix precompute versions) |
| Stop-TF sweep coverage | `data/param_sweep_manifest_tradier.json` | DC_LOW_FROZEN_STOP_TF +['D'], BB_FROZEN_STOP_TF ['1h','4h','D','W'], both ENABLED sweepable; campaign STOP_PACK cells: NEAR_ENTRY_OFF_ONLY / DC_FROZEN_4H / DC_FROZEN_D / BB_FROZEN_4H / BB_FROZEN_D / BB_FROZEN_W |
| Email digest | `morning_email.py` `build_persym_campaign_section` | campaign digest + universe-vs-random table in the private morning email (runs on S1) |
| Bible | §12.7 (universe), §12.8 (param-range store + pruning rules), §12.9 (session 4h + stop packs), §11 changelog | synced to S1 |
| Script hygiene | `per_sym_trb_profiles.py` → `old/` (Mac + S1) | replacement = campaign; no importers existed |
| Locked-file patches (STAGED, not applied) | `patches/PENDING_UNLOCK_universe_side_gating_20260718.md` | side-aware `_always_tradeable`, dead-attr-name fix, `V8_UNIVERSE_AT` env, per_sym_20d side retention, config comment notes |

## Campaign phases (running on S1, serialized behind the in-flight ts_atr_off A/B)

- **Phase 1 baseline**: every symbol in the trb point-in-time universe (129 keys currently),
  one faithful Tier-2 run each on current settings → per-(sym,side) baseline rows with
  gain/mo, side-aware b&h/mo, delta; off-universe sides recorded under `_offuni` from the
  SAME runs (zero extra compute) for the final curated-vs-random comparison.
- **Phase 2 OFAT**: STOP_PACK cells first, then manifest params (priority-ordered:
  stops/exits/entries), one value at a time vs baseline, per-key rows. Dedupe vs
  store + central DB — nothing re-runs, ever.
- **Phase 3 report** (hourly): relevance ranking (max |Δgain/mo| across keys), HOPE
  suggestions (extremes not equal → values outside the range queued), INERT list
  (param moved NO key → wiring suspect; fix wiring BEFORE pruning — user mandate),
  XLSX `data/reports/PARAM_BASELINE_STOCKS.xlsx`, digest → morning email.
- **Phase 4 per-sym retest**: rerun each key starting from its best cell config +
  suggested outside-range values (the campaign's dedupe makes this incremental).
- **Phase 5 7D→trc**: after finals, run the 20d/7D recency arm over the trb lists for trc
  (`per_sym_20d_agent_stocks.py --account trc --syms <trb universe>` — running it is
  allowed; editing it needs unlock, patch #4 staged). Winners stage via the existing
  human promote gate (`promote_pending_per_sym.py`) — never auto-live.
- **Phase 6 universe A/B**: `compare-universe` → `data/reports/universe_vs_random_baseline.json`
  (also emailed).

## Next actions (in order, as compute frees)

1. ts_atr_off A/B finishes → smoke + Phase 1 baseline runs automatically (cron).
2. Baseline complete → fire the session-4h NPZ regen on S1
   (`backtest_v8_precompute.py --all --mode tradier --workers 2`) — includes the 7 missing
   NPZs (ALKT, ALMU, COE, HP, LITE, PRI, QRVO; klines present, 213/220 covered today).
   Then rerun Phase 1 as arm `session4h` and A/B old-vs-new 4h (expect improvement).
3. Phase 2 OFAT grinds 24/7; prune only per §12.8 rules.
4. User: say "unlock backtest_v8_engine.py per_sym_20d_agent_stocks.py config_tradier.py"
   to apply the staged side-gating patches (campaign enforces the universe at orchestrator
   level meanwhile, so this closes the hole for OTHER engine callers).

## CRYPTO APPLICATION PLAN (apply after stocks is fully running)

Everything is mode-tagged; the changes needed:
1. **Universe registry**: crypto tradeable keys live in per-account structures
   (`symbols.json` + `data/persym_final_book.json` + account winner-sets like inf's 12);
   add a crypto seeder mapping account → (sym, side) sets and snapshot the same way. The
   ledger reconstruction works unchanged (`data/history/{ang,inf,fin,flz,men}/`).
2. **Store**: `param_results_store.py` is mode-parameterized already — use
   `data/param_results_crypto.db` (one-line DB_PATH switch or env), campaign
   `MODE="crypto"`, `ACCOUNT="ang"`, `START="2022-01-01"` (bear+bull convention).
3. **Campaign**: b&h loader must read `klines_cache_backtest/{SYM}USDC|USDT_15m.json`
   (USDC-over-USDT policy for the 10 majors); LTF is 3m not 5m; commission 0.08%/side.
   STOP_PACK equivalents: crypto R1 already uses 20-bar frozen dc_low — the crypto packs
   test dc_high (short mirror) TFs 4h/D and BB 4h/D/W the same way (engine path shared).
4. **Session 4h does NOT apply** — crypto is 24/7; wall-clock 4h is correct (explicitly
   gated `MODE == "tradier"` in the precompute fix).
5. **Off-universe comparison**: same `_offuni` trick; crypto live sides come from the
   final book's per-key tradeable flags rather than long/short files.
6. **7D arm**: `per_sym_7d_agent.py` already exists for crypto accounts — point it at the
   campaign finals the same way Phase 5 does for trc.

## 2026-07-19 SHEPHERD LOG (15:00–16:00 UTC)

- **Baseline + correction COMPLETE**: 226/226 key_baseline rows with bh_pct (MU verified: 2.31y, LONG Δ−21.96 / SHORT Δ+22.02 vs b&h).
- **Universe-vs-random RECORDED** (`data/reports/universe_vs_random_baseline.json`): universe 129 keys +9.33%/mo book, mean Δ vs b&h **+0.155**; off-universe 97 keys −0.72%/mo, Δ **−0.118**. Point-in-time universe selection validated.
- **S1 reboots daily at 13:05 UTC** — killed the 12:19 psc_ofat chain silently (8 cells stored). The */30 flock cron self-healed at 13:30 (by design). OFAT resumes via `already_tested()` skip — no rework.
- **OLD OFAT GRINDER RETIRED**: `alternating_grinder.sh` / `engine_ofat_screen.py` moved to `old/` (Mac + S1), cron line commented (`# RETIRED 2026-07-19`). It held the single test slot 3h/day while completing ~8 cells/run with an EMPTY baseline (all deltas = raw sharpe) and writing outside the param_cells store. Superseded by campaign OFAT (2722 cells × 113 syms, stamps + real deltas). NOTE: crypto grind (0/4335, alive=0) died with it — crypto gets the campaign port per plan above.
- **Digest false-INERT fixed** (`param_results_store.py::param_ranges`): spread now includes the implicit baseline point (delta=0). Before: single-value params showed spread=0 → STOP_PACK falsely INERT. After: STOP_PACK max_spread=0.432, keys_moved=4/8, INERT list empty. First real signal: near-entry stops OFF helps miners (AGI +0.43, AG +0.39, AEM +0.29 gain/mo).
- **Test-slot queue** (single TEST_SEQ lock): ts_atr_off b1 RUNNING (5 syms, ≤3h) → psc_baseline (fast, cached) → psc_ofat (6h slices, --param-limit 24 from next cron). ts_atr_off has 18/21 batches left → **session-4h NPZ regen stays HELD for days** unless ts_atr_off is deprioritized (user call).
- **19:20 UTC — HONEST-STAMP FIX** (`persym_baseline_campaign.py`): the trades cache (`cell__<sym>.jsonl`) was keyed only by file existence, so a stamp change re-stamped OLD cached trades with the NEW code hash (that's how "226 baselines recomputed" in 26 min after today's engine churn — they were cache re-reads). Now `run_symbol` writes `stamp__<sym>.txt` at engine-run time and rows carry `artifact_stamp()` (the code that actually produced the trades); pre-fix cache is labeled `cache-prestamp|<current>`. Deltas were internally consistent (cells + baselines share artifacts) but the stamp column lied. Related: Mac→S1 `rsync_to_sandbox.sh` (*/5) propagates every Mac engine edit mid-campaign — 3 engine stamps seen today; ts_atr_off A/B runs WITHOUT /tmp/BACKTEST_HOLD (its per-batch version.json sidecars are the only defense).
- **19:55 UTC — CAPTURE ANALYSIS (user: "90% underperformance vs b&h — where is the ball dropped?")**: per-trade forensics on ROKU/ARM/LLY LONG baselines: time-in-market 0.1–3.3%, avg hold 1–2.2h, dominant exits `MTF_ATR_TRAIL_5m_x2.0` + 5m DELTA_EXIT/WT_CROSSUNDER — the engine scalps hours-long trades inside multi-month rallies (same family as the 07-11 R1 5m-stop churn defect). Direct A/B on the 78 keys with STOP_PACK__NEAR_ENTRY_OFF_ONLY done: **baseline +61.2% total vs pack +185.6% (3.0x)**; biggest winners are SHORTS (AU_S +2.7→+15.2, AGI_S +2.4→+14.3, ASTS_S +0.9→+11.3, AG_S −5.9→+4.9). Time-in-market stays 1–5% on most longs even in the pack → next lever is exit-TF escalation + reentry, and the DC_FROZEN_D/BB_FROZEN_W packs (catastrophic-only stops). Charts: every cell renders at :5077 trb_review as `psc::<CELL>` per symbol (verified ROKU baseline 96 trades, AGI pack 63).

## 2026-07-20 CUTOVER LOG (01:00–01:40 UTC)

- **Regen COMPLETED** (262/262 NPZs incl. the 7 missing syms) despite exit-kill (rc=137 after DONE — RAM contention with the cron OFAT the chain failed to hold off).
- **Session-4h VERIFIED by bin boundaries**: ARM/AAPL open_4h changes at 13:30/16:45/20:00 UTC (EDT) + 14:30/17:45/21:00 (EST winter). LLY's extra 12:30 = premarket rows joining the prior-day 16:00 bin — consistent with live `get_4h_bin`. Chain's original verifier was wrong (wrong key `timestamps_4h` vs `timestamp_4h`, no DST) — fail-safe held, verifier rewritten boundary-based/DST-aware.
- **Chain design flaw found**: it killed running v1 workers but not the */30 cron → cron relaunched v1 OFAT at 23:41 mid-regen → 16 DC_FROZEN_4H cells + 6 pack dirs (incl. parallel-session RIDE_* packs) computed on MIXED NPZ. **Quarantined**: rows deleted, dirs removed, `QUARANTINE_NOTE_20260720.json` written. Lesson: a cutover chain must disable the re-launcher BEFORE changing shared inputs.
- **v2 LIVE**: cron + runner on `PSC_CAMPAIGN=stocks_baseline_v2_s4h`; baseline recomputing on session-4h NPZ under the flipped (near-entry-off) config = live parity. Packs, RIDE packs, exit-TF cell, TF_EXCLUDE_{5m,15m,1h,4h,D,W} all recompute against v2.
- Monitor re-armed: v2 baseline completion + open-time (13:30–15:00 UTC) tradier-stack-up check.

## 2026-07-20 02:00 UTC — DIGEST ALL-ZEROS INCIDENT (user report) — ROOT-CAUSED + FIXED

**Symptom:** capture scoreboard showed trades=0 / gain 0.000 for every key since the ~16:xx pass.
**Root cause (two layers):** (1) `is_in_intervals` originally called `pd.Timestamp` with pandas never
imported; the blanket `except: pass` swallowed the NameError → every trade silently rejected. A later
strptime rewrite fixed parsing but exposed layer (2): the point-in-time membership filter treats the
registry's LOWER-BOUND reconstruction as hard truth — earliest evidence is seed snapshots
(ledger-recon 2026-03, s2 dump 05-08, trc 06-26) which are CENSORED observations of a long-standing
universe, not join events → 2024→2026 trades (the bulk) discarded → zeros upserted OVER the good rows.
**Fix:** `collapse_intervals(..., censor)` — membership collapses to one interval; any first-evidence
before `REGISTRY_LIVE_TRACKING_START=2026-07-18` (when change-tracked snapshots began) is censored →
member since window START; keys first appearing after the epoch keep their true join date.
Never-member keys stay excluded (curated-vs-random comparison intact). Universe rows now use the
membership window for years AND b&h (no more filtered-gain ÷ full-window months deflation).
**Result:** 133/139 v1 keys restored with real numbers + time_in_mkt_pct/capture_vs_bh populated
(MU_LONG 19 trades capture 0.0008 tim 0.12% — the ball-dropped receipts). v2 unaffected going forward
(same code path). **Lessons:** never `except: pass` a filter that can zero a metric — log it;
snapshot-derived membership data is censored, not complete; a filter change that rewrites baselines
must diff row counts/trades before upserting over good data.

## 2026-07-20 02:3x UTC — SESSION-4H FIRST A/B (CONFOUNDED — read caveats before citing)

v1 NEAR_ENTRY_OFF pack (wall-clock 4h NPZ) vs v2 baseline (session-4h NPZ), 226 keys, both
stops-off: **wall-clock +665.6% total vs session-4h +397.6%** (38 improved / 101 worsened / 87 flat).
Session-4h did NOT improve raw totals — CONTRARY to the expectation in the original prompt.
**CONFOUNDS (why this is not a clean verdict):** (1) regen refreshed kline tails — v2 sees newer
data; (2) engine/tradier_manage/config edits (parallel session) landed between the arms;
(3) v1 arm = overrides on old defaults, v2 = new defaults natively.
**DECISION: session-4h STAYS.** Live has always been session-anchored — wall-clock 4h backtests
were parity lies regardless of their prettier totals (Bible parity mandate outranks score).
The wall-clock arm is retired; all packs/TF_EXCLUDE/OFAT now measure against the v2 session-4h
baseline. If a clean wall-clock-vs-session A/B is ever wanted, precompute needs an explicit
V8_4H_WALLCLOCK escape flag + frozen klines — do NOT compare across regens again.

## 2026-07-20 ~05:00 UTC — A/A TEST + VERSION-SOUP FINDING

NEAR_ENTRY_OFF pack under v2 = built-in A/A (knobs already off in v2 defaults): **224/226 keys
byte-identical** — harness consistent. The 2 mismatches (XOP +7.17→+8.63, WPM +2.87→+3.73) are
NOT noise: artifact stamps show FOUR engine hashes inside v2 (faa00a80, dfdd5869, 91b26b1b,
c6b8095f) — live Mac edits stream into the running campaign via the */5 autosync and DO change
trade outcomes. Honest-stamp sidecars caught it (their design purpose).
**RECOMMENDATION (campaign v3 / before per-sym finals):** FREEZE the engine for a campaign —
snapshot backtest_v8_engine.py + tradier_manage.py + wt_dc_delta.py + config_tradier.py into a
campaign-pinned dir at campaign start; run_symbol executes the frozen copy; refresh = explicit
new campaign version. Until then: analysis must pair cells with SAME-stamp baselines (stamps are
in every row); cross-stamp deltas are suspect. ZIM sidecars empty — investigate stamp() write path.
