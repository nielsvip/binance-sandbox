# Entry Fleet Metric Normalization — 2026-07-26

## Outcome

The entry-overlay fleet ledger was mixing three different aggregation rules
under the single stage label `VEC_UNTOUCHED_OOS`:

- strategy, B&H, and same-entry control returns were **sums of three outer
  validation-fold capital-return percentages**;
- time in market was a **row-weighted mean across those folds**; and
- trades were the **sum of exit fills across those folds**.

Those aggregate rows are useful robustness evidence, but they are not one
untouched holdout return. They can no longer be read as one: each historical
row is preserved, an explicitly scoped aggregate row has been appended, and
the final chronological validation fold has been appended as the true
`VEC_UNTOUCHED_OOS` row.

No vector campaign was rerun.

## Append-only migration

Normalization version: `entry_fleet_metric_scope_v1`

| job | path | historical rows retained | aggregate rows added | final OOS rows added |
|---:|---|---:|---:|---:|
| 34 | `ENTRY_WT_DC` | 10 | 10 | 10 |
| 36 | `ENTRY_STOCH_HHHL` | 20 | 20 | 20 |
| 38 | `ENTRY_GOLDEN_RULE` | 10 | 10 | 10 |
| 43 | `ENTRY_BB_RECOVERY` | 20 | 20 | 20 |
| 45 | `ENTRY_DELTA_MTF` | 20 | 20 | 20 |
| 49 | `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | 20 | 20 | 20 |
| 50 | `ENTRY_DC_BREAK_ENTRY_ENABLED` | 20 | 20 | 20 |
| **Total** |  | **120** | **120** | **120** |

Job 50, `ENTRY_DC_BREAK_ENTRY_ENABLED`, was deliberately excluded from this
agent's write because its own path owner was already applying the same generic
append-only aggregate/final-fold split. Its 20 wiring-audit rows were also
preserved. Coordinating the writes avoided two agents appending competing
corrections.

The migration was rerun after application as an idempotency check: it found
all 200 corrected rows and inserted zero additional rows. Every appended
aggregate row exactly matches its historical source row across strategy, B&H,
control, TIM, and trade metrics (0 mismatches out of 100).
Job 50's owner separately verified its 40 corrected rows with the same
normalization payload contract.

## Explicit metric contracts

`VEC_NESTED_FOLD_AGGREGATE`:

- `metric_scope=NESTED_OUTER_VALIDATION_FOLD_AGGREGATE`
- `return_unit=SUM_OF_FOLD_CAPITAL_RETURN_PCT`
- `return_aggregation=SUM_ACROSS_OUTER_VALIDATION_FOLDS`
- `tim_unit=PCT`
- `tim_aggregation=ROW_WEIGHTED_MEAN_ACROSS_OUTER_VALIDATION_FOLDS`
- `trades_unit=EXIT_FILLS`
- `trades_aggregation=SUM_ACROSS_OUTER_VALIDATION_FOLDS`
- `untouched_oos=false`

Corrected `VEC_UNTOUCHED_OOS`:

- `metric_scope=FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD`
- `return_unit=CAPITAL_RETURN_PCT`
- `return_aggregation=NONE_SINGLE_FOLD`
- `tim_unit=PCT`
- `tim_aggregation=NONE_SINGLE_FOLD`
- `trades_unit=EXIT_FILLS`
- `trades_aggregation=NONE_SINGLE_FOLD`
- `untouched_oos=true`

The recurring workbook now exposes these fields as dedicated columns on
`Path Fleet Results`. The digest shows metric scope and units beside every
fleet row and marks older evidence without those fields `LEGACY_UNSCOPED`.

## Corrected final-fold research snapshot

These are research screens, not promotion claims. “Beat control” means the
candidate exceeded the same frozen ladder/entry control in the final
chronological outer validation fold.

| path | keys | beat B&H | beat control | beat both | mean TIM | total exits |
|---|---:|---:|---:|---:|---:|---:|
| `ENTRY_WT_DC` | 10 | 10 | 5 | 5 | 77.82% | 47 |
| `ENTRY_STOCH_HHHL` | 20 | 16 | 11 | 10 | 54.90% | 141 |
| `ENTRY_GOLDEN_RULE` | 10 | 10 | 2 | 2 | 77.10% | 52 |
| `ENTRY_BB_RECOVERY` | 20 | 17 | 11 | 10 | 56.16% | 163 |
| `ENTRY_DELTA_MTF` | 20 | 16 | 10 | 9 | 55.46% | 128 |
| `ENTRY_AUGMENT_TREND_RESUME_ENABLED` | 20 | 16 | 10 | 10 | 85.00% | 143 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | 20 | 16 | 11 | 10 | 58.98% | 151 |

The corrected rows expose the real problem more clearly: many entry overlays
beat the $2,000 side-specific B&H comparator, but substantially fewer improve
on the already-strong same-entry control. That distinction was obscured when
three fold returns were presented as one untouched result.

## Prevention and verification

- `tools/ingest_entry_overlay_fleet.py` now writes the aggregate and final
  fold as separate rows for new artifacts.
- `tools/normalize_entry_fleet_metrics.py` performs the historical,
  append-only, idempotent migration.
- Regression tests prove the final chronological fold is selected, aggregate
  and final units are separated, historical rows are not rewritten, repeated
  migration is a no-op, and workbook/digest unit fields remain visible.
- Local verification: 12 focused tests passed.

## Rollback

The pre-migration SQLite backup is:

`data/reports/path_fleet/queue.pre_entry_metric_normalization_20260726.db`

Job 50's separately coordinated pre-write backup is:

`data/reports/path_fleet/queue.db.bak_job50_metric_scope_20260726T2035Z`

The migration report on s1 is:

`data/reports/path_fleet/ENTRY_FLEET_METRIC_NORMALIZATION_20260726.json`

Rollback should only be done while fleet writers are stopped, by restoring
that consistent SQLite backup. Normal operation should retain the historical
rows and use the corrected rows instead.
