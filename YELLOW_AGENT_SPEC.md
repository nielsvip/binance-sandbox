# YELLOW AGENT — SPEC (what it must be, how it runs, what it fills, in what sequence)

**Status 2026-09-29 PM:** NOT running (was a spawned fork, lost across session compaction). Deferred by
operator for now ("we don't need it for now") but must be rebuilt as a **cron-driven, reboot-proof,
resumable background job** that can run for many days and pick up where it left off. This file is the spec.

## 1. PURPOSE

Compute the **complete yellow + orange delta matrix**: for every **switch × every applicable filter ×
5 timeframes × 4 cat_sides**, the vector delta of applying that filter (at that TF) to that switch vs the
switch's own baseline. The finished map:
- fills the **yellow cells** (`L:BI` — the per-filter "Option Value" columns) of every switch row, and
- decides which filters are **"opportune"** for each switch (promoted from the shared orange filter pool to
  that switch's yellow cells), i.e. it rebuilds `data/opportune_filter_map.json`.

It runs on an **OLD/stable template** (frozen switch×filter layout) so the long job is not invalidated by
template reorders; when it finishes, its map is **applied to the LATEST template** (yellow cells + orange
rows), independent of any row reorder the daily loop did in the meantime.

## 2. THE MATRIX — 5 × 4, ALL SWITCH ALL FILTER

- **5 timeframes** per filter: `OFF, D, 4h, 1h, 15m` (a filter cell's "Option Value").
- **4 cat_sides**: `CRYPTO_LONG, CRYPTO_SHORT, STOCKS_LONG, STOCKS_SHORT` (one template each).
- **All switches**: every row of the 13 SWITCH_SHEETS (`STDEV_SLOPE_SIZING … GLOBAL_RISK_GATES`).
- **All filters**: every filter in `FILTER_DICTIONARY_V2` that is *applicable* to the switch (grounded in
  `data/opportune_filter_map.json` code map; where inapplicable-set is unknown, test all).
- A cell = `(cat_side, sheet, switch=value, filter, TF)` → `delta` (vector gain of switch+filter@TF minus
  switch baseline), plus `valid` (TIM≤80, DD≤30, trades≥floor per §58) and `trades`.

Total ≈ (switches per cat_side) × (filters per switch) × 5 TF × 4 cat_side. Days of compute.

## 3. SEQUENCE (resumable, deterministic)

For each `cat_side` (4):
1. Load the OLD template; compute each switch's **baseline** (defaults / bold col-B) once, cache it.
2. For each `switch` row (worst-first or document order — deterministic), for each applicable `filter`,
   for each `TF in {OFF,D,4h,1h,15m}`:
   a. evaluate `switch=value + filter@TF` via `v12_quick_engine` (vector, batched, workers).
   b. `delta = gain − switch_baseline`; write the cell to the store IMMEDIATELY (checkpoint per cell).
   c. keep the best positive TF per (switch,filter); a filter with a positive best-TF delta is
      **opportune** for that switch → belongs in its yellow cells.
3. After all switches of a cat_side: emit that cat_side's yellow/orange map slice.

## 4. STATE / RESUME / OUTPUT (the hard requirements)

- **Store:** an append-only, row-per-cell store keyed by `(cat_side, sheet, switch, value, filter, TF)` —
  SQLite (`data/yellow_matrix.db`, table `yellow_cells`) or a per-cat_side CSV. Each finished cell is a row.
- **Resume:** on start, load done keys from the store; **skip any cell already present** (idempotent). Never
  restart from zero. A reboot/crash loses at most the in-flight cell.
- **Cron:** `*/10 * * * *` guard — launch the agent if not already running (flock singleton on
  `/tmp/yellow_agent.lock`); `@reboot` too. It must **yield to the 30D sweep** (nice -n 19, gated on spare
  CPU/RAM) — the 30D twice-nightly run is the priority; the yellow matrix may take days.
- **Progress signal:** write `data/yellow_matrix_progress.json` = `{cat_side: {done_cells, total_cells}}`
  each pass so the operator sees % complete.

## 5. APPLY-ON-COMPLETE

When all 5×4 cells for a cat_side are present and `valid`:
- For each switch, set its yellow cells `L:BI` from the store (best-TF per opportune filter), rebuild
  `data/opportune_filter_map.json`, and remove promoted filters from the shared orange rows (a MOVE, not a
  drop — orange→yellow per operator 2026-09-29).
- Merge into the **LATEST** template (match by switch NAME + filter NAME, never by row index — the daily
  loop reorders rows). Back up the template first; validate the zip (≥10 entries) before `os.replace`.

## 6. GUARDRAILS

- Never run on the Mac's 134-NPZ subset for crypto (returns 0 trades) — run where full NPZ live (s1/s5 crypto,
  s2 stocks) on spare cycles.
- NO-LIES: a repeated-identical delta across unrelated filters = the fabricated-distinctness trap
  (`v12_quick_engine` arange/hash) — flag, don't write it as truth (see `v15_delta_health_monitor.py`).
- It edits ONLY yellow cells / orange rows / `opportune_filter_map.json` — never the bold defaults (those
  change only via promotion in the daily loop) and never live config.
