# Claude handoff: daily V15 30D charts with original metric deltas

## Objective

Implement and start a daily chart refresh for every published V15 `sym_side` represented by the latest 30D board in `SPREADSHEETS/V15_V16_CELL_BY_CELL/`.

- Run at **20:00 UTC every day** (the user previously phrased this as market close / 4pm ET; schedule explicitly at 20:00 UTC as most recently directed).
- Start with a run immediately, not just a future schedule.
- Recalculate the existing published V15 switch set against the rolling 30D window using real worker NPZ data (NPZs reside on S1/S5; do not run a Mac simulation against incomplete Mac NPZ inventory).
- Render the chart in the established hires zoomable chart style.
- Publish/overwrite one current chart per `sym_side` in `/Users/niels/Documents/binance/SPREADSHEETS/charts/`.
- Remove the default heading phrase `why not beating B&H — hires zoomable` from these generated charts.
- Add a compact comparison of current recalculated metrics versus the original board HTML in `SPREADSHEETS/V15_V16_CELL_BY_CELL/`, including how long ago the source calculation was performed. Use actual calculation provenance, not the HTML file mtime or republish time.
- Preserve truthful source attribution and report missing/invalid provenance or NPZ data as an explicit failure; do not fabricate or silently substitute metrics.

## User clarification / authorization

The user has explicitly said to do this and then said `unlock`; edits needed for this work are authorized. They clarified that the board HTML is the visual reference and calculation source, and that charts should be regenerated from the `/SPREADSHEETS/V15..` published boards and overwritten daily in `SPREADSHEETS/charts`. They were frustrated by repeated diagnostic-only replies; implement and execute rather than asking them to restate the request.

## Verified local facts (2026-10-30)

- Repository: `/Users/niels/Documents/binance`.
- Example visual reference: `SPREADSHEETS/V15_V16_CELL_BY_CELL/SAP_LONG_bh8_gain17_t44_30d_matrix.html`.
- SAP board manifest: `SPREADSHEETS/V15_V16_CELL_BY_CELL/SAP_LONG_bh8_gain17_t44_30d_matrix_manifest.json`.
  - `published_utc`: `2026-10-30T00:54:54Z`.
  - metrics: gain `16.695918741773188`, trades `44`, TIM `44.51`, max DD `0.6241761824477133`, BH `7.8415644293792885`, gain-vs-BH `8.228724334198429`.
  - manifest has `overrides_md5`, `npz_name`, `npz_md5`, `pilot_md5`, template and other provenance. Inspect its full contents and related progress JSON before choosing source-of-truth fields.
- There are roughly 1,519 `*_30d_matrix.xlsx` boards in the directory; identify latest qualifying board per `sym_side`, use matching manifest/provenance and switch set.
- `SPREADSHEETS/charts/` currently contains only `.DS_Store` (no chart inventory to enumerate there yet).
- Chart renderer: `tools/opt/hires_chart.py`, `generate_hires(..., out_dir=...)`; it currently defaults heading to `symside + ' — why not beating B&H — hires zoomable'` near line 669. Supply a suitable explicit heading or alter the chart producer safely.
- Existing Mac helper `tools/generate_zoomable_charts_mac.py` is a historical candidate, but it generates/copies chart files and skips already-existing good charts. It does not implement the requested daily overwrite or the original-board metric delta/age.
- `tools/v15_bulk_charts.py` is an S1 published-final chart generator with strict progress/manifest C-set checks, but currently outputs beside the XLSX / under `data/reports/charts_1Y`, not the requested Mac `SPREADSHEETS/charts` daily output.
- `tools/daily_forward_charts.py` is a different forward-state chart workflow; do not use its forward-state inventory as a substitute for published V15 boards.
- `BACKTEST_BIBLE.md` was read this session. Required workflow also says read `LOCKED_FILES.md` before edits. User supplied unlock in this conversation.
- Backups were created before any edits (no edits were ultimately made):
  - `backups/before_daily_chart_delta_202610300318.py` (copy of `tools/opt/hires_chart.py`)
  - `backups/before_daily_chart_overwrite_202610300318.py` (copy of `tools/generate_zoomable_charts_mac.py`)
- Current session clock at last check: `2026-10-30T03:18:27Z`; use current time when generating provenance age and schedule.

## Implementation guidance

1. Inspect `tools/opt/hires_chart.py`, V15 board manifests, latest board HTML metadata, and worker sync / execution paths. Establish the authoritative V15 switch set per board. Do not infer switches from filename alone or use stale unrelated `*_v14_progress.json` without matching it to the board manifest (`overrides_md5`, gain, board name).
2. Build a dedicated daily orchestrator, likely under `tools/`, that:
   - discovers latest valid published 30D V15 board per `sym_side`;
   - validates manifest and C-set provenance;
   - dispatches calculation to S1/S5 worker(s) holding NPZs, preserving the real V15/vector calculation path and 30D rolling window;
   - returns metrics/ledger/chart data to Mac or renders worker-side and syncs resulting HTML;
   - compares current values against the original board HTML’s calculation-time values (at minimum gain, BH, trades, TIM, max DD, and any other metrics present in both); shows signed differences and original calculation timestamp + elapsed age;
   - writes/overwrites deterministic `<SYM_SIDE>_30d_daily.html` (or a clearly defined stable naming pattern) under `SPREADSHEETS/charts/` and writes a manifest with per-symbol success/failure, input board/manifest, C-set hash, NPZ identity, window cut, calculation timestamp, metrics and deltas;
   - never advertises a chart as current when the worker eval or sync failed.
3. Use the existing `tools.opt.hires_chart.generate_hires` renderer, passing an explicit clean heading, or extend its interface to accept an injected evaluation result if needed. Avoid reimplementing chart rendering or trading metrics.
4. Schedule daily at 20:00 UTC via an appropriate persistent scheduler on the Mac (likely launchd); inspect existing LaunchAgents conventions first. Ensure the job catches up once if the machine was asleep at scheduled time only if appropriate and without generating misleading dates.
5. Run the first refresh immediately. It may take time for ~1.5K sym_sides: parallelize carefully across available workers and report actual coverage, failures, and completion. Do not call an incomplete run complete.
6. Validate generated HTML and its metrics/provenance against the worker result and the original source board, and confirm schedule is loaded/enabled.

## Critical honesty constraints

- Metrics must come from actual evaluation outputs on the correct worker NPZs.
- Do not stamp an old board or chart with today’s date; distinguish original calculation time from new chart generation time.
- If an original HTML does not expose enough provenance, use its paired manifest/progress/delta log only after establishing the exact matching source; otherwise show provenance unavailable and flag it.
- Do not tell the user the job is installed/running until an actual execution and scheduler verification prove it.
