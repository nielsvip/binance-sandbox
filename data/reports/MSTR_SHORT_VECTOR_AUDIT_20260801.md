# MSTR_SHORT vector calculation and duplicate audit — 2026-08-01

Status: **BLOCKED — DATA_UNAVAILABLE, not a matrix result.**

The local source cache has usable 5m, 15m, and daily MSTR data through
2026-07-31, but its 1h and 4h sources end on 2026-04-07.  Interpolating or
forward-filling those April higher-timeframe values into the May–July 15m
window is invalid.  The generated `backtest_v8/indicators/MSTR.npz` was moved
to `MSTR.npz.STALE_HTF_BLOCKED_20260801`; no new MSTR vector value may reach a
matrix until the 1h and 4h cache is refreshed and this audit passes.

The raw diagnostic run is retained as evidence only.  Its 747 latest logical
cells produced 18 duplicate-metric groups and 19 duplicate-action-fingerprint
groups: 7 cells passed and 740 are quarantined.  This is a generic-proxy
broadcast failure, not a valid calculation or a reason to fill red cells.

The one preserved real V8 row remains immutable and visible separately:

| key | setting | V8 gain/mo | B&H/mo | delta | trades | status |
|---|---|---:|---:|---:|---:|---|
| MSTR_SHORT | `WT_3M_FORCE_OPEN_TF_LADDER=false` | 2.4103 | 8.0425 | -5.6322 | 158 | preserved V8; not promotable |

The duplicate contract permits only one bounded within-parameter plateau such
as `0=20`, provided at least three other values in the same grid each have a
different metric and action fingerprint.  It never permits a cross-parameter
repeat, all-zero group, whole-grid broadcast, or repeated action schedule.

Machine receipts: `MSTR_SHORT_DATA_CONTRACT_20260801.json` and
`MSTR_SHORT_VECTOR_CELL_AUDIT.json`.
