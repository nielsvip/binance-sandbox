# Stock backtest recovery — 2026-07-24

This is the recovery map for the Tradier/stocks backtest system on
`s1:/home/niels/binance-sandbox`. It reconciles the work recorded in
`BACKTEST_BIBLE.md` with the state actually found on S1. It is also the implementation
specification for the later `ez_` crypto port.

## Executive status

### 2026-07-25 cron and Gmail restoration

The S1 jobs paused specifically for the MU-only compute focus were restored:
crypto autoresearch, the weak-key reoptimizer, and the INF/FLZ/MEN/FIN
per-symbol agents. Matrix/report exports remain active while
`data/GRID_SUSPENDED` is present.

The old stock baseline/OFAT, pilot, arrow and sequential test writers remain
quarantined because the B&H forensic found structural input and accounting
defects; reactivating them now would add invalid cells. They are to be restored
after the no-lookahead/data-preflight/side-isolation regression gate passes.

`tools/results_digest_email.py` is scheduled on S1 at 00:00 and 12:00 UTC and
now embeds `SWITCH_MATRIX_TRB_DIGEST.md`. Rendering is verified. SMTP delivery
is still externally blocked: S1 has no Gmail credential and the Mac Keychain
credential is rejected by Gmail with `535 BadCredentials`. No secret was
copied or guessed; a new Gmail app password or OAuth sender is required.

### 2026-07-25 matrix-building continuation

The actionable TRB matrix now has a `description` column populated for every
tradeable path row (3,522 actionable rows; 129 tradeable key columns in the
current ENGINE export). Descriptions explain switch semantics; they are not
profitability claims.

The first interaction replay after fixing entry ablation attempted
`MTF_ARMED_ENTRY_ENABLED=true` with WT-DC threshold restored to 45. The runner
created the override, but the engine returned no JSONL result (`rc=1`). This
cell is therefore **PENDING/RED wiring**, not a zero-trade performance result,
and must not be promoted. The runner now records such failures instead of
silently treating them as zero trades.

Acceptance remains strict: fresh Tier-2 engine result, real closes, changed
trade fingerprint, and performance above the same-key B&H floor. Vector runs
are screening shortcuts only until replayed through the engine.

### Ladder-first correction (2026-07-25)

`tradier_manage.band_ladder_mult()` now uses the requested map as a wiring
baseline: D 10x→6x, 4h 6x→4x, and 1h 4x→1x; below the lower gray band is 0x.
The stale 4h 8x→4x and 1h 4x→2x values/comments were corrected in
`config_tradier.py`. These multipliers are **not accepted performance values**;
they are hypotheses assigned to a dedicated parameter-search lane for empirical
testing across symbols and timeframes.

The ladder sizes an entry path; it does not discover green arrows itself. The
current MTF-arrow path uses a 5m swing-reversal confirmation, while the
separate all-timeframe `BAND_ARROW` path is disabled by default. Ladder-first
validation must therefore prove that the intended D/4h/1h green-arrow or
HH/HL-low-StochRSI triggers reach the sizing function before exit results are
trusted.

The next campaign is exit-first: hold ladder entry fixed, replay individual
exit paths and small combinations, enforce re-entry at or below the recorded
exit/top price, and only then add entry filters.

Do not use the current `SWITCH_MATRIX_TRB` values to select a live configuration yet.

The engine is not currently running a campaign. `data/GRID_SUSPENDED` stops the matrix
watchdog before it launches workers. This is appropriate while the measurement contract is
being repaired.

The principal zero-trade failure is not a market result:

1. Tradier Tier-2 has no final mark-to-market record for an open position.
2. The exposure ladder equates `zero closes` with `opened and held`, although `zero closes`
   also means `never opened`.
3. The Tradier Tier-2 loop does not drive the flat-key WT force-open path used as the ladder
   entry. MU had 406 qualifying bars in a short diagnostic window and produced no raw order
   event.
4. Time-in-market is derived from completed trades only, so open holds report 0%.
5. The ladder invokes Tier-2 twice per cell: once for trades and again to scrape a result
   line. The scraper expects `pnl/trades`, while one engine branch emits
   `gain_pct/closes`.
6. Most generic parameter tests allow the live per-symbol overlay to shadow the test
   override. A cell can therefore measure the live overlay instead of the requested value.

The old ladder campaign is retained as invalid historical evidence. Repaired results use a
new `__ladder_v2` campaign and must not be differenced against the old rows.

## Recovery checkpoint — 2026-07-24 16:50 UTC

The zero-trade result contract is repaired and smoke-tested without restarting live trading:

- MU_LONG Tier-2 opened one full-capital position, made zero real closes, emitted one final
  mark-to-market trade and measured 99.97% time-in-market.
- Its first tradable RTH bar was 120.93, while the raw NPZ B&H benchmark bought premarket at
  119.00. The engine produced 716.12% net from the legal RTH entry; raw NPZ B&H was 729.42%.
  Stage 0 now reports both and validates against first-tradable-bar B&H rather than failing a
  valid hold because of an impossible premarket fill.
- The dedicated result contract contains real closes, MTM count, side-specific opens and
  side-specific time-in-market. The ladder reads the same invocation's result file.
- `SWITCH_MATRIX_TRB.csv.gz` and `.xlsx` were regenerated at 16:48–16:49 UTC with a
  `description` column. The workbook now has Entry, Exit, Sizing, Other, Ladder, BandLadder,
  Coverage, Baselines and Inventory sheets. Non-sweepable/dead/live-only settings are kept in
  Inventory instead of the actionable grid.
- The first fast screen is stored separately as
  `data/reports/VEC_EXPOSURE_LADDER_TRB.jsonl`, tier `VEC_CANDIDATE`. It is not promotion
  evidence and is not yet inserted into the Tier-2 matrix.

The new in-process vector screen loads each NPZ once. Measured full-history runtimes were
36.4 seconds for 23 MU variants, 11.8 seconds for VT and 5.6 seconds for HAO. The MU no-exit
floor runs in 1.6 seconds and reconciles to B&H: 729.370% net versus 729.420% gross, one MTM
trade, zero real closes and 100% exposure.

Initial shortlist signals, pending Tier-2 confirmation:

- MU_LONG `DC_LOW4_STOP`: 85.91% exposure, 1,243.90% vector candidate gain versus 729.42%
  B&H; `WT_CROSSUNDER_FINAL`: 49.76% exposure, 815.79%.
- HAO_SHORT `DC_LOW4_STOP`: 82.16% exposure, 276.86% versus 99.87% side-aware B&H.
- VT_LONG: the allowlisted paths stayed near 100% exposure/B&H; no 70–80% candidate was found.

These figures are screens, not accepted recipes. The vector engine cannot apply several live
per-symbol MU/HAO controls; every omitted control is recorded in `accepted_refused`. Winners
must be replayed with Tier-2 and rejected if trade fingerprints/exposure do not agree.

## What has been built so far

### 1. Truthful Tier-2 and provenance

`backtest_v8_engine.py` is the only decision-grade runner. It calls the real
`tradier_manage`/`ez_manage` strategy paths. Every usable result must retain:

- full override JSON;
- effective configuration difference;
- the engine, manager, WT/DC and config file stamp;
- symbol, side, account, start/end window and tier;
- closed trades plus final mark-to-market;
- time-in-market.

Historical results without these fields remain provenance records, not reusable recipes.

### 2. Stocks input parity

Stocks 4h data was changed from UTC wall-clock bins to live-like market-session bins.
Point-in-time symbol-side universes and universe snapshots were added so the engine does not
test a random union of NPZ symbols.

### 3. Results store and generated reports

The canonical store is `data/param_results_stocks.db`. The relevant components are:

- `tools/param_results_store.py` — baselines and parameter cells;
- `tools/persym_baseline_campaign.py` — Tier-2 campaign orchestration;
- `tools/param_matrix.py` — long-form matrix derivation;
- `tools/export_switch_matrix_xls.py` — generated human report;
- `tools/knob_registry.py` — path roles and wiring scope;
- `tools/red_cell_agent.py` — disconnected/global-only analysis;
- `tools/exposure_ladder.py` — subtractive search from buy-and-hold;
- `tools/param_shortlist.py` and `tools/combo_search.py` — shortlist and interaction search.

The spreadsheet is an export, never the source of truth.

### 4. Strategy findings already established

- Excess churn can turn strongly profitable low-frequency shorts into catastrophic losses.
- Near-entry profit exits removed too much exposure and were switched off for stocks after a
  floor-sized test.
- Several live/backtest parity breaks were found: dead force-open, unread R1 selector,
  stock simulation reading crypto config, stock 4h bin mismatch, and per-symbol/global
  configuration mismatches.
- Strong B&H winners remain barely exposed under the current production-like baseline:
  MU_LONG 19 trades/0.13% time-in-market, HAO_SHORT about 4 trades/0.15%, and VT_LONG zero.
- Vector Tier-1 currently covers only 164 of 921 sweepable parameters. It is a shortlist
  engine, not a matrix completion engine.

## What is invalid or contradictory

### False stage-0 passes

The July 22 MU_LONG and HAO_SHORT stage-0 claims are invalid. Their rule was
`closes == 0`. MU's cached cell and result are empty/zero. A valid stage-0 floor requires all
of:

- at least one verified open event;
- zero real closes before final mark-to-market;
- at least 99% time-in-market;
- final marked-to-market return reconciled to side-aware B&H.

### Old OFAT and ladder cells

Cells produced from a near-zero-exposure baseline cannot show whether most exits bind.
Cells whose per-symbol overlay shadowed the requested override are not attributable. Keep
them in gray/red historical state; do not promote, delete, or repeatedly rerun them.

### Matrix scope

The current 6,291-row export includes 2,637 rows from 780 knobs explicitly marked
`sweepable:false`, including `DEAD` and `LIVE_ONLY` entries. These belong in an Inventory
sheet with an exclusion reason, not in the actionable matrix.

The registry's first-three-token family heuristic is also unsafe: 860 families have no main
switch and 28 have multiple mains. For example, WT force-open is incorrectly grouped under
`BUILD_TO_TARGET` instead of its actual master. Descriptions and sub-settings must attach to
explicit master mappings or the exact knob.

### Promotion thresholds

The documentation alternates between `beats B&H`, `2x B&H`, and `10x B&H`. Use this hierarchy:

1. valid measurement: opens, exposure and marked-to-market reconcile;
2. viable: positive and at least B&H;
3. candidate: at least 2x B&H with adequate trades/sample;
4. mission target: approach 10x B&H without unacceptable drawdown/churn.

Do not discard below-B&H results; retain them gray so they are not retested.

## Repaired runner contract

The Tradier Tier-2 runner must emit, in the same invocation that writes trades:

- side-specific open-event counts;
- real-close count, excluding final mark-to-market;
- final mark-to-market count and trade;
- side-specific time-in-market;
- canonical gain, trade count and pool Sharpe;
- a dedicated result file.

The ladder must run Tier-2 once per cell, read that result file, and use a new campaign/cache
namespace. Stage 0 and stage 1 seed a test-only full-capital position on the first regular
session bar. This seed is for the B&H/exit experiment only; stage 2 entry-path tests must
start flat.

Tests are side-isolated. Per-symbol overlays are disabled during a knob experiment, but the
latest accepted key recipe is first copied into the explicit override. The tested value is
then applied on top. This preserves the accepted baseline while making every cell
reproducible.

## Matrix description contract

Each actionable path needs structured metadata and one rendered human description:

`ACTION — behavior; expected trade/exposure effect; off value; dependency; override scope; code reference.`

Required structured fields:

- group: Entry / Exit / Sizing / Other;
- exact knob and explicit master;
- role: master, filter, threshold, timeframe, condition or sizing;
- behavior;
- expected and observed trade-count effect;
- exact off value;
- dependencies;
- per-symbol/global-only/disconnected scope;
- live and engine references;
- confidence.

Do not infer numeric polarity from a name. Measure the observed effect from otherwise-identical
cells.

Verified examples:

- `WT_3M_FORCE_OPEN_ENABLED` (stocks trigger is 5m): open/augment when price is on the
  correct SMA/EMA-200 side and WT is favorable. Usually more entry/add events and exposure.
  Off is `False`.
- `WT_3M_FORCE_OPEN_BUILD_TO_TARGET`: allows repeated adds toward target notional. It changes
  adds and exposure, not merely the initial entry.
- `WT_3M_FORCE_OPEN_BYPASS_GATES`: bypasses execution/risk gates and generally increases
  fills, but is currently global-only.
- `LONG_STRUCT_EXIT_TF` / `SHORT_STRUCT_EXIT_TF`: structural-break exit selected by
  timeframe; `"None"` disables it.
- `MTF_DC_REJECT_EXIT_ENABLED`: exits when a Donchian breakout rejects back inside. It
  depends on `MTF_EXIT_USE_COMPOUND=True`.
- `MTF_DC_REJECT_EXIT_LOOKBACK`: disconnected; it has no read site.
- `PARTIAL_PROFIT_LOCK_ENABLED`: staged reduce plus a remainder stop. It increases order
  events; its effect on completed round trips and exposure is empirical, not monotonic.

## Replacement for field-by-field filling

Use a recalculating beam/ladder search rather than a static OFAT grid:

1. Freeze code, NPZ, universe, accepted per-key recipe and stamps.
2. Validate stage 0 from a seeded B&H position.
3. Re-enable exit path families one at a time, including their dependent parameters.
4. Keep a small Pareto beam per key, ranked by:
   - B&H-relative gain;
   - target exposure band;
   - drawdown;
   - churn/trades;
   - robustness across nearby parameter values.
5. Recalculate the whole surviving recipe after each path is added. Never add OFAT deltas.
6. Run leave-one-out on every member of a surviving recipe.
7. Test entry families from flat using the best exit recipes.
8. Use vector Tier-1 only for paths it faithfully implements; confirm survivors in Tier-2.
9. Store every attempted recipe and result. Gray below-B&H recipes, red measurement/wiring
   failures, green viable candidates.

Exposure bands:

- top/bottom ten tradeable keys: target 70–80%;
- remaining keys: target 30–50%;
- B&H/return is a constraint, not a reason to force exposure into the band;
- crypto bands must be re-derived because it trades 24/7.

Pilot order is MU_LONG, VT_LONG and HAO_SHORT. Do not fleet out until each has a verified
stage 0 and at least one viable recipe.

## Vectorization plan

Fast work has three levels:

1. vector diagnostics for signal masks, path firing counts, exposure and rough P&L;
2. vector Tier-1 for the 164 currently implemented knobs;
3. faithful Tier-2 confirmation for shortlisted recipes.

Highest-leverage engineering:

- port the missing sweepable entry/exit predicates into batched NumPy masks;
- enable and validate Tier-2 vectorized re-entry;
- evaluate many parameter thresholds against one precomputed signal tensor;
- cache indicator and path-family state by code/NPZ stamp;
- keep hedge/options in a separate fidelity lane until their execution and valuation are
  modeled; never silently label discarded fields as tested.

Implemented first: `tools/vec_exposure_ladder.py` calls
`v8_vec_sweep.simulate_one_symbol(..., _npz_cache=..., seed_position_at_start=True)` for a
positive allowlist of parity-capable families. It suppresses while-held augmentation for the
full-capital seed, isolates vec-native experimental exits, records reason fingerprints, and
never writes a Tier-2 cell. An all-exits-off parity floor is mandatory before its candidates
are usable.

## Red-cell repair lane

Current report counts include about 100 global-only, 51 no-read-site, 61 gated-off and 30
crypto-only knobs. Repair in this order:

1. global-only knobs that should be per-symbol (`getattr(config, X)` to `_cfg(...)`);
2. missing master/sub-setting dependency mapping;
3. no-read-site knobs that represent intended behavior;
4. mode-only ports explicitly required in both systems;
5. hedge and options paths with a dedicated valuation/execution test.

Every repair needs a two-value fingerprint test proving that the trade list changes before a
wide sweep is allowed.

## Crypto (`ez_`) replication

Start only after the stock pilots pass.

Port the method, not stock parameter values:

1. build the crypto knob registry and explicit master mappings;
2. repair crypto `_psym_get`/global-only half-wiring;
3. build a complete crypto exit inventory, including boolean, string and numeric off values;
4. validate one long and one short seeded stage-0 floor;
5. run the recalculating ladder/beam search;
6. create `param_results_crypto.db` and crypto matrix exports;
7. re-derive exposure bands, costs and timeframes.

Important differences:

- crypto base WT timeframe is 3m; stocks is 5m;
- crypto uses `config.Config` and `_psym_get/_psym_sps`;
- crypto round-trip cost and 24/7 exposure differ;
- use USDC in preference to USDT where available;
- stock session-4h logic must not be copied to crypto.

## Rollback and repository plan

Before changing a locked/live file:

1. inspect its complete diff;
2. preserve a dated backup;
3. record the keep/revert split in `LOCKED_FILES.md`;
4. compile and run a short smoke test;
5. reproduce a known cell before restarting a campaign;
6. never restart live trading for a backtest-only change.

The S1 Git repository currently has only an initial local commit and no configured remote.
Before renting compute, create/configure a private GitHub remote and commit only:

- the canonical runner/orchestrators;
- config and strategy code needed for parity;
- schema/migrations and report generators;
- manifests and small reproducibility fixtures;
- deployment/bootstrap scripts and documentation.

Do not commit market data, large databases, credentials, logs or generated result workbooks.
Provisioned machines must reproduce one known S1 cell bit-for-bit before receiving a shard.

## 2026-07-24 zero-trade repair verification

The final zero-trade defect was in `tradier_manage.queue_trade_action()`. The OPEN-only
L/S-ratio bypass looked up `order["position_side"]` before the `order` dictionary was
created. The resulting `UnboundLocalError` was caught by the function's broad exception
handler and returned as bare `False`, so every qualifying real OPEN disappeared as an
ordinary gate refusal.

Repairs:

- resolve the bypass with the already-parsed `position_side`;
- return `QUEUE_ADD_REFUSED:<reason>` if `OrderQueue.add_order()` refuses an order instead
  of reporting unconditional `SUCCESS`;
- keep the force-open caller's truthful queued/refused diagnostic;
- log exceptions returned by the engine's concurrent `process_position` calls;
- honor per-symbol side enablement and the explicit test-only side selector.

Verification used the frozen `MU_LONG_RECOVERY_OVERRIDE_20260724.json`, long-side
isolation, real `process_position`, and July 2026 RTH data:

- before the final repair: 0 opens, 0 closes, 0% time in market; every qualifying force
  entry ended as `WT_3M_FORCE_OPEN_QUEUE_REFUSED ... result=False`;
- after the repair: 7 long opens, 7 real closes, 3 wins / 4 losses, +3.54% engine P&L,
  6.1445% long time in market, and no process-position exception.

This proves the real entry path is connected again. It does **not** prove that this recipe
meets the 70–80% exposure target or beats B&H. The high-exposure vector candidates remain
Tier-1 hypotheses and must be replayed in Tier-2 after their corresponding live predicate
is shown to fire. No live process was restarted and no setting was promoted.

The stage-0 seed itself then exposed a second false-floor case on HAO_SHORT. The test hook
called the real entry executor, marked itself seeded without checking the return value, and
allowed ordinary strategy entry to happen later when the seed had been refused. That cached
artifact showed one MTM trade but only 86.7275% exposure and carried
`GR_HTF_DIRECT_ENTRY`, not the required seed reason.

The seed is now a deliberately synthetic test fixture: only when
`V8_LADDER_FORCE_INITIAL_SIDE` is set, it uses the engine's internal fill/state path while
bypassing strategy entry gates, and marks itself complete only after a returned `SUCCESS`.
The fresh HAO_SHORT replay then produced:

- `V8_LADDER_INITIAL_BH_SEED` at the first tradable bar;
- one short open, zero real closes and one final MTM;
- exactly 100.0000% time in market;
- +99.87% engine P&L versus +99.8745% gross tradable short B&H before the 0.06% round-trip
  cost.

VT_LONG also passes the corrected Tier-2 floor: one synthetic long open, zero real closes,
one final MTM, 99.9855% time in market, and +34.33% engine P&L (+34.2651% trade net after
the 0.06% cost). MU_LONG, VT_LONG and HAO_SHORT therefore all have valid stage-0 floors;
this is only the foundation for recalculating exit/entry combinations.

Do not reuse pre-repair `L2__LADDER0__*` caches. Recovery runs carry a new tag until cache
stamps become a mandatory cache-key component.

The first interaction-aware HAO_SHORT candidate is already a useful rejection. Vector
Tier-1 estimated `DC_LOW4_STOP_ENABLED=True` at +276.86% and 82.1617% exposure versus
+99.8745% side B&H. Fresh Tier-2 replay produced only +0.61%, 16 real closes and 7.6855%
exposure. Keep this tested result gray and do not retest/promote it. The discrepancy is
vector re-entry/fill-path fidelity, not evidence that the live DC_LOW4 path is disconnected:
Tier-2 fired it and changed the trade list materially.

The regenerated ENGINE matrix now contains 3,522 actionable switch-value rows across 129
tradeable keys, 1,721 rows with data, 1,801 pending, and a nonblank description on every
row. Non-actionable settings live in the separate Inventory sheet instead of appearing as
blank work.

The first measured MU_LONG entry candidates are now classified against the clean floor:

- `MTF_ARMED_ENTRY_ENABLED=True`: Tier-2 produced -0.93%, 32 real closes and 0.3927% TIM.
  It is wired but gray/rejected; the older positive matrix delta came from a different
  combination and cannot be treated as a standalone gain.
- `DC_LOW4_STOP_ENABLED=True`: Tier-2 produced the unchanged 99.9722% floor with zero real
  closes. It is inert at this floor and gray; the vector +1.70x B&H screen does not have
  Tier-2 parity.
- `GOLDEN_RULE_REQUIRE_ACTIVATION=True/False`: both values produced the identical -0.93%,
  32-close, 0.3927%-TIM result. This is a red wiring cell: the setting is listed as an entry
  condition but is not connected to the faithful Tier-2 entry path. It is assigned to the
  reconnect lane and must not be used in a recipe until a two-value fingerprint changes.
- `BB_PULLBACK_GATE_ENABLED=False`: reproduced the same -0.93%, 32-close, 0.3927%-TIM
  fingerprint. This is also red/reconnect in the isolated Tier-2 entry lane; the historical
  positive delta was not attributable to this knob alone.

This is the intended recalculating workflow: positive historical deltas are hypotheses, not
promotions, and every candidate is rerun from a fixed accepted recipe plus the current path.

The isolated-entry runner itself required one more correction: `all_entries_off()` was disabling
boolean/string entry switches but leaving numeric `WT_DC_ENTRY_THRESHOLD` and its
`TRA_WT_DC_ENTRY_THRESHOLD` alias at live values. As a result, MTF, BB and Golden Rule tests
all inherited WT-DC entries and produced the same 32-trade fingerprint. Entry floors now set
both thresholds to an unreachable value before replaying a named path; this is required for
any future entry-cell result to be considered valid.
