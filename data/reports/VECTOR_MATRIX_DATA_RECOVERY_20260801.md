# Vector matrix data recovery — 2026-08-01

The vectorized/sweep data was not deleted.  It was separated from the current
exact ENGINE surface when the matrix contract was tightened.

## Recovered S1 artifacts

| artifact | contents | treatment |
|---|---:|---|
| `SWITCH_MATRIX_TRB_VEC_DIAGNOSTIC.csv.gz` | 3,528 rows / 69,257 populated cells | preserved VEC diagnostic matrix |
| `full_trb_blank_matrix_vec_approx_20260801/vector_overlay_index.jsonl` | 421,441 overlay rows | full amber/provisional overlay ledger |
| `full_trb_blank_matrix_vec_approx_20260801/all_cells.csv` | 980,488 lines | raw overlay export |
| `param_results_stocks.db` VEC tier | 83,604 rows across 226 keys | vector DB evidence, not ENGINE credit |

The recovered diagnostic CSV checksum on S1 is:

`2fb0133690c170fc2d8df8c2febb975856d6bc1d709cbcf0a5676f84dfaa58fc`

## Why the canonical CSV looked empty

`SWITCH_MATRIX_TRB.csv.gz` is now the exact Tier-2 ENGINE artifact.  Under the
current Bible it must not be populated by unverified vector approximations.
Therefore its blank cells do not mean the vector work vanished.  The VEC
diagnostic matrix, workbook diagnostic sheet, full overlay ledger, and VEC DB
remain the fast-screen data sources.  Exact V8 receipts supersede them cell by
cell.

No vector values were deleted or promoted during this recovery.  This keeps the
~69k previously visible vector cells available while preserving the exact
completion denominator and preventing provisional values from being reported as
validated ENGINE results.
