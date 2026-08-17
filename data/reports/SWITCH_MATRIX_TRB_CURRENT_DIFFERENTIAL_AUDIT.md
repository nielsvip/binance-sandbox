# SWITCH_MATRIX_TRB current differential audit

Generated: `2026-07-30T02:56:52.854504+00:00`

## Verdict

**No — every matrix row is not currently proven to produce unique executable data.**

Require a distinct current c5 action fingerprint on at least one applicable key after activation dependencies are reached.

A c5 uniqueness claim is **not allowed yet: the canonical store has zero accepted c5 exact rows.**

## Scope lock

- Matrix: `/home/niels/binance-sandbox/data/reports/SWITCH_MATRIX_TRB.csv.gz`
- Matrix SHA-256: `31ac83a1f4135891b3ee240e49539615067e77ed106167d898186c92c22f255f`
- Matrix mtime: `2026-07-30T02:50:05.673062+00:00`
- Campaign: `stocks_repaired_20260725_c2` only
- Tier: `ENGINE` only
- Historical campaigns read: none
- Static manifest/registry/interdependency hashes: verified
- TIM: first ten symbols in each current configured side list must be 50–80%; every other key must be 20–60%.
- Top-10 LONG: `A, PLTR, LEXX, OKE, CIBR, EXEL, PR, PSX, TSM, CHRD`; SHORT: `MSTR, FCX, PLTR, AXON, HAO, MSFT, FCN, OKE, PR, VALE`.

## Current evidence

| measure | value |
|---|---:|
| matrix path/value rows | 3280 |
| actionable rows | 2530 |
| tradeable key columns | 159 |
| current configured tradeable keys | 129 |
| matrix numeric cells | 162 |
| numeric cells on current configured keys | 162 |
| numeric cells on non-active matrix columns | 0 |
| matrix rows with numeric evidence | 72 |
| accepted current exact rows | 167 |
| stale/foreign-contract rows excluded | 2962 |
| accepted c5 exact rows | 0 |
| params with a current exact value pair | 20 |
| pairwise key probes | 41 |
| latest accepted exact row | `2026-07-30T02:56:21Z` |

- Current keys missing from the matrix: `none`.
- Non-active key columns retained in the matrix: `30` (listed in the JSON; they are never treated as current promotion targets).

### Canonical matrix row statuses

| status | rows |
|---|---:|
| `DEGENERATE` | 4 |
| `EXACT_QUEUE_EMPTY` | 408 |
| `INERT_AT_VALUE` | 12 |
| `NOT_ENGINE_TESTED` | 1073 |
| `OK` | 32 |
| `RECONNECT` | 24 |
| `STALE_ENGINE_CONTRACT` | 1727 |

### TIM gate on accepted current exact rows

| verdict | rows |
|---|---:|
| `FAIL` | 163 |
| `PASS` | 4 |

| key | pass | fail | missing |
|---|---:|---:|---:|
| `ACN_SHORT` | 0 | 36 | 0 |
| `LAC_SHORT` | 0 | 20 | 0 |
| `MU_LONG` | 2 | 18 | 0 |
| `NVDA_LONG` | 1 | 19 | 0 |
| `TTD_SHORT` | 0 | 26 | 0 |
| `VT_LONG` | 1 | 44 | 0 |

## Differential classifications

| class | params |
|---|---:|
| `CONFIRMED_DISCOVERABILITY_ONLY` | 30 |
| `CONFIRMED_NO_LIVE_DECISION_READ` | 253 |
| `CROSS_KEY_IDENTICAL_ACTION_AND_RESULT_SUSPECT` | 8 |
| `INAPPLICABLE_CONTROL` | 45 |
| `INSUFFICIENT_CURRENT_EXACT_PAIR` | 548 |
| `PAIRWISE_DISTINCT_EXACT_PROOF` | 7 |
| `RESEARCH_HELPER_NOT_SINGLE_KNOB` | 2 |
| `SINGLE_KEY_SIGNAL_PLATEAU_REQUIRES_CROSS_KEY_PROBE` | 3 |

## Dynamic suspects requiring action or another applicable-key probe

| param | class | collision keys | distinct keys |
|---|---|---|---|
| `DELTA_EXIT_ACCEL_THRESHOLD` | `CROSS_KEY_IDENTICAL_ACTION_AND_RESULT_SUSPECT` | TTD_SHORT, ACN_SHORT | — |
| `DELTA_EXIT_DECAY_RATIO` | `CROSS_KEY_IDENTICAL_ACTION_AND_RESULT_SUSPECT` | TTD_SHORT, ACN_SHORT, LAC_SHORT | — |
| `DELTA_EXIT_MIN_HOLD` | `SINGLE_KEY_SIGNAL_PLATEAU_REQUIRES_CROSS_KEY_PROBE` | ACN_SHORT | — |
| `DELTA_EXIT_MIN_TF_LOST` | `SINGLE_KEY_SIGNAL_PLATEAU_REQUIRES_CROSS_KEY_PROBE` | ACN_SHORT | — |
| `DELTA_EXIT_OPPOSING_RATIO` | `CROSS_KEY_IDENTICAL_ACTION_AND_RESULT_SUSPECT` | TTD_SHORT, ACN_SHORT, LAC_SHORT | — |
| `GR_HTF_DIRECT_EXIT_SCORE` | `CROSS_KEY_IDENTICAL_ACTION_AND_RESULT_SUSPECT` | MU_LONG, NVDA_LONG, VT_LONG | — |
| `LR_BAND_HARVEST_FRAC` | `SINGLE_KEY_SIGNAL_PLATEAU_REQUIRES_CROSS_KEY_PROBE` | VT_LONG | — |
| `MTF_BB_REJECT_EXIT_ENABLED` | `CROSS_KEY_IDENTICAL_ACTION_AND_RESULT_SUSPECT` | TTD_SHORT, ACN_SHORT | — |
| `STDEV_REJECT_EXIT_RETURN` | `CROSS_KEY_IDENTICAL_ACTION_AND_RESULT_SUSPECT` | VT_LONG, TTD_SHORT, ACN_SHORT, LAC_SHORT | — |
| `STDEV_REJECT_EXIT_ZONE` | `CROSS_KEY_IDENTICAL_ACTION_AND_RESULT_SUSPECT` | VT_LONG, TTD_SHORT, ACN_SHORT, LAC_SHORT | — |
| `STRUCTURAL_RANGE_SHIFT_K_LOW` | `CROSS_KEY_IDENTICAL_ACTION_AND_RESULT_SUSPECT` | NVDA_LONG, VT_LONG | — |

## Current exact distinct proofs

| param | distinct keys |
|---|---|
| `DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD` | VT_LONG |
| `LR_BAND_HARVEST_HI` | VT_LONG |
| `MTF_DC_REJECT_EXIT_ENABLED` | ACN_SHORT |
| `STRUCTURAL_RANGE_SHIFT_K_HIGH` | NVDA_LONG, VT_LONG |
| `STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS` | VT_LONG |
| `STRUCTURAL_RANGE_SHIFT_TF` | MU_LONG |
| `WT_3M_FORCE_OPEN_ENABLED` | MU_LONG |

## Static reconnect backlog

- `253` params have no live decision read in the verified source inventory.
- `30` params are referenced only by non-functional discoverability metadata.
- These are a reconnect/prune backlog, not dynamic proof that a particular threshold is economically useful. The machine-readable JSON contains every name and activation dependency.

## Interpretation

- An off/default control may legitimately equal the control.
- A side/account-inapplicable row must be skipped, not called dead.
- One identical key can be a signal-domain plateau. The same collision on multiple applicable keys is a wiring suspect.
- c4 trade fingerprints are too weak for final uniqueness. Only c5 action fingerprints include sizing, partial cash flow, exposure and action events.
- No row may be promoted from rounded matrix deltas alone; the current exact receipt and fingerprint are authoritative.

This audit is read-only: it does not modify the matrix, result DB, live configuration, or any historical result.
