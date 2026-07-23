# CODEX.md — TRB Matrix Operating Brief

This is the Codex working brief for the backtest and `SWITCH_MATRIX_TRB` task. It is derived
from `CLAUDE.md`, `BACKTEST_BIBLE.md`, `AGENT_BRIEF_MU_MATRIX.md`, `REWORK_RESULTS_STORE_20260718.md`,
`INDEX.md`, and the repository's matrix/exporter documentation. The canonical system rules remain
in `CLAUDE.md`; this file records the execution contract for this task.

## Safety and truth rules

- Never invent, round up, extrapolate, or promote a result. A zero-trade run is a bug report.
- Any published backtest result must be faithful Tier-2 output from `backtest_v8_engine.py` and
  must include the canonical metric set through `metrics_guard` when Sharpe is reported.
- Never compare Tier-1 `VEC` output with Tier-2 `ENGINE` output. Export them to separate artifacts.
- A single-symbol/single-side result is diagnostic only; it cannot justify live promotion.
- Buy-and-hold is the floor for every key. Record a cell as a finding only when its same-key,
  same-campaign result beats that key's B&H floor.
- Preserve the complete override recipe, result fingerprint/stamp, trade count, and baseline used
  for every recorded cell. Identical trade fingerprints across values indicate an inert switch.
- Do not edit live trading code, locked files, or server files as part of matrix filling.

## Machine and data roles

- MacBook (`/Users/niels/Documents/binance`) is the live-trading workspace and artifact mirror.
  Do not run sweeps here.
- S1 (`s1-int`, `/home/niels/binance-sandbox`) is the authority for crypto and Tradier sweeps.
- S2 is dead and must not be used.
- The authoritative TRB store is S1's `data/param_results_stocks.db`; the Mac copy may be a
  zero-byte stub. The generated Mac workbook/CSV is only a pulled report artifact.
- TRB keys come from `symbols_trb_long.json` and `symbols_trb_short.json`, represented as
  `<SYMBOL>_LONG` and `<SYMBOL>_SHORT`.
- The campaign currently targeted by the repository workflow is `stocks_baseline_v2_s4h`.
  Session-anchored 4h data is required for Tradier parity; do not mix it with retired wall-clock
  4h results.

## Matrix contract

`tools/export_switch_matrix_xls.py` produces `data/reports/SWITCH_MATRIX_TRB.xlsx` and its
compressed full-width CSV from the authoritative `param_results_stocks.db`:

- one row per `(switch, value)` from `data/param_sweep_manifest_tradier.json`;
- one column per TRB symbol/side key;
- `ENGINE` is the promotable faithful Tier-2 sheet;
- `VEC` belongs only in `SWITCH_MATRIX_TRB_VEC_DIAGNOSTIC.*`;
- `PENDING` means no same-tier result exists, not zero or no effect;
- `INERT_AT_VALUE`, `RECONNECT`, and `DEGENERATE` are wiring/data-quality states, not winners;
- the exporter must be rerun after the store grows because the workbook is generated, not the
  source of truth.

## Work order

Fill the current focus key completely before advancing. For the MU pilot, use `MU_LONG` and the
documented order: HTF exits (D/4h/1h), HTF entries, LTF exits (15m/5m), then LTF entries.
Use the exposure ladder and confirmed `wt_cross_5m`/`wt_cross_15m` fallback documented in
`BACKTEST_BIBLE.md` when the ladder is unavailable. Do not spend compute on a different key until
the focus state says the current key is at least 99.5% complete.

Before claiming progress, verify:

1. S1 worker/store activity and campaign name.
2. Same-tier baseline and B&H floor for the focus key.
3. Count of ENGINE cells with real numeric results, not merely rows emitted by the manifest.
4. Inert/reconnect/degenerate classifications and identical-fingerprint evidence.
5. Export timestamp, row count, key count, and pending count in the handoff.

## Handoff format

State exactly what was run, where the authoritative data lives, how many ENGINE cells are filled,
which cells beat B&H, which switches are inert or disconnected, and what remains pending. Never
call a partial matrix complete.

## 2026-07-23 starting snapshot

- S1 campaign: `stocks_baseline_v2_s4h`; S1 is actively running the baseline/OFAT campaign.
- Authoritative store: S1 `data/param_results_stocks.db`, 75,448 `param_cells` rows and 1,128
  baseline rows at the refresh; the Mac store is a 0-byte stub.
- Tier split in the store: 69,340 `VEC` rows and 6,108 `ENGINE` rows (including legacy-null-tier
  rows interpreted as ENGINE by the exporter). Only ENGINE is used for the TRB report.
- Refreshed local artifact: `data/reports/SWITCH_MATRIX_TRB.xlsx` plus its compressed CSV,
  generated from the S1 store at approximately 2026-07-23 07:26 UTC.
- Current ENGINE matrix: 6,291 switch-value rows; 1,812 rows have at least one measured key;
  4,479 remain `PENDING`; statuses include 1,377 `DEGENERATE`, 6 `INERT_AT_VALUE`, and 429 `OK`.
- MU_LONG coverage: 1,591 of 6,291 switch-value rows have a measured ENGINE value. This is
  progress, not completion; the focus key must continue until the documented completion gate.
- Workbook verification: 8 sheets imported successfully; no formula-error matches were found.
