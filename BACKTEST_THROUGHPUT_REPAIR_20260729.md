# Backtest throughput and exact-matrix repair — 2026-07-29

## Scope and invariant

This repair makes research and matrix filling faster without changing any live
Tradier setting. It preserves the completed-parent clock, LONG/SHORT isolation,
fill-cost accounting, reentry state, and the recorded provenance of native
versus interpolated 5-minute data. Exact engine runs remain the authority for a
candidate that survives screening; the fast path is a parity-checked causal
screen, not a substitute accounting model.

## Preserved pre-repair evidence

Before deploying the repair, the current S1 workbook was preserved and audited:

- canonical CSV snapshot:
  `/private/tmp/SWITCH_MATRIX_TRB_20260729.csv.gz`
- CSV SHA-256:
  `848cb491392326d54fdccb67c9441489fdd2143a7aa44e3daef50550d59a7b8b`
- copied: `2026-07-29T15:38:46Z`
- S1 workbook SHA-256 immediately before deployment:
  `53d054d0b6da3ae8e1b0c420f7c78045b09fc63f21032b894f945c952b3c7713`
- pre-repair S1 code:
  `88ac8f2cde580c880a8c447e503a0ac2eaa78710`

The old rows were not deleted. They are retained as historical evidence and
become stale automatically when the repaired matrix contract fingerprint
changes.

The snapshot showed why “populated” was not equivalent to “tested”:

| key | populated | reconnect | inert | OK |
|---|---:|---:|---:|---:|
| MU_LONG | 406 | 346 | 9 | 51 |
| TTD_SHORT | 125 | 72 | 13 | 40 |
| ACN_SHORT | 152 | 99 | 13 | 40 |

For MU, 355/406 rows had the same exact `-3.3471` delta and 77/88 tested
multi-value groups produced identical results at every value. Thirty-seven MU
rows tested another account's namespace (`TRA_` or `TRC_`) inside TRB. This is
wasted exact work, not evidence that 355 independent strategy changes lose by
the same amount.

## Repairs

### 1. Compiled causal scanner

`tools/vec_band_ladder_scan.c` implements the stateful ladder accounting loop.
`tools/vec_band_ladder_walkforward.py` precomputes the causal arrays and
schedule once, then calls the compiled loop. Its Python implementation remains
as the executable reference and rollback path.

Randomized parity covers LONG and SHORT, target and additive sizing, duplicate
parent timestamps, capacity, costs, exits, and reentry. Metric equality is
checked for every result field.

Real S1 NPZ benchmark, three repeats over three validation windows:

| key | rows | Python wall | compiled wall | speedup | parity |
|---|---:|---:|---:|---:|---|
| MU_LONG | 45,917 | 0.4334 s | 0.00219 s | 197.85× | PASS |
| VT_LONG | 35,632 | 0.3414 s | 0.00127 s | 267.99× | PASS |
| HAO_SHORT | 27,092 | 0.2603 s | 0.00111 s | 235.04× | PASS |
| TTD_LONG | 45,904 | 0.4393 s | 0.00151 s | 291.87× | PASS |

Aggregate scanner speedup was 242.62×. A separate end-to-end TTD ladder command
improved from 30.32 seconds to 3.02 seconds (10.04×), including work outside
the scanner. Machine receipt:
`data/reports/BACKTEST_SCANNER_BENCHMARK_20260729.json`.

### 2. Explicit exact-cell precedence

`tradier_manage._cfg` now has a guarded test-only precedence layer. It is active
only when both `V8_SWEEP_MODE=1` and
`V8_BACKTEST_OVERRIDE_PRECEDENCE=1` are set. The exact cell then overrides the
accepted per-symbol overlay for that one field; all other overlay values remain
the accepted baseline. An empty override preserves the baseline unchanged.

`tools/persym_baseline_campaign.py` enables that guard only for its backtest
engine subprocess. Ordinary live execution does not meet the two-part guard and
is unchanged.

### 3. Inert/reconnect first, exact grid second

`tools/exact_wiring_gate.py` classifies only current-contract, same-symbol,
same-side, exact ENGINE evidence. It checks stock-code read sites, master
switches, semantic domains, and observed NPZ indicator ranges. A knob first
receives two maximally separated, non-default smoke values. Remaining exact
values are skipped when it is:

- `RED_RECONNECT`: no stock read site or no effect at either smoke value;
- `RED_GATED_OFF`: its master is disabled in the accepted baseline;
- `RED_BAD_RANGE`: proposed values are outside the indicator/config domain;
- `RED_DEGENERATE`: smoke values differ from baseline but not each other.

Skip receipts are deduplicated by contract and verdict in the claims database,
then written to `data/reports/EXACT_WIRING_GATE_EVENTS.jsonl`. Exact claims use
twice the configured engine timeout as their lease, preventing a legitimate
long run from being stolen and duplicated.

Boolean database values are canonical JSON (`true`/`false`), not the strings
`True`/`False`. A regression exercises both passes and proves the two typed
values can reach `WIRED_DIFFERENT`.

### 4. Semantic and account preflight

`sweep_value_semantics.py` rejects invalid ranges before execution:

- RSI, Stoch, MFI and similar oscillators must stay in their actual domain;
- integer counts cannot become negative or fractional;
- booleans must be JSON booleans;
- ambiguous percentage units are quarantined rather than silently clamped;
- a quarantined grid is recorded by the manifest builder and expander.

TRB no longer exact-tests `TRA_` or `TRC_` fields. Equivalent filters apply for
the other account namespaces.

The legacy `tools/prune_useless_knobs.py` refuses to write against a
`stocks_repaired_*` campaign. Historical mixed-contract “useless” lists are not
valid proof for this repaired contract.

### 5. Fleet audit follows the manifest

`tools/audit_repaired_matrix_fleet.py` reads
`data/matrix_worker_manifest.json`; it no longer hardcodes an obsolete worker
list. Each manifested worker must exist exactly once with its declared symbol
and side and be independently detached. Other safe-contract workers are
reported separately but remain valid owners of their exact-engine descendants.
They are not mislabeled as orphans and are not killed.

## Operating sequence

1. Preserve the current workbook/database hashes.
2. Deploy all contract files together. Do not kill active exact engines.
3. Let workers detect the source-signature change and re-exec between units.
4. Rebuild the same-symbol/same-side baseline under the new fingerprint.
5. Run two-value wiring smoke tests.
6. Screen coherent entry/exit/ladder bundles with the causal compiled path.
7. Send only strict survivors to the exact engine.
8. Promote nothing live until exact schedule/accounting parity, holdouts,
   solvency, capacity, reentry, data provenance, and the 65–80% MU TIM contract
   all pass.

## Rollback

- Scanner: route `_simulate` to `_simulate_python`; the parity reference was
  deliberately retained.
- Exact precedence: remove either guard environment variable. Live behavior is
  already outside both guards.
- Matrix: restore the prior code commit and use the preserved snapshot/hash.
  Do not relabel post-repair rows as pre-repair evidence.
- Gate: the JSONL receipts are evidence only; removing the preflight does not
  modify strategy configuration.

## Crypto replication

Apply this sequence to `ez_` only after the stock campaign proves the full
contract. Reuse the causal compiled scanner and semantic validator, but rebuild
the indicator-domain audit, account namespace rules, accepted per-symbol
baseline, and exact fingerprint for the crypto code and its own data
provenance. Never copy stock result rows or knob-wiring verdicts into crypto.
