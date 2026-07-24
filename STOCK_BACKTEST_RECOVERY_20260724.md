# Stock backtest recovery — 2026-07-24

This is the recovery map for the Tradier/stocks backtest system on
`s1:/home/niels/binance-sandbox`. It reconciles the work recorded in
`BACKTEST_BIBLE.md` with the state actually found on S1. It is also the implementation
specification for the later `ez_` crypto port.

## Executive status

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
