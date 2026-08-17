# C5 STDEV raw-indicator migration

Status: prepared, tested, and deliberately inactive. Current c4 engine source
and stored c4 receipts are unchanged.

## Root cause

`StockStrategy.evaluate_stop()` first calls `parse_market_data()`. That parser
does not retain `bb_pct_b_{TF}`, `bb_pct_b_{TF}_prev`, or
`wt_velocity_1h`. The STDEV_REJECT and STDEV_BB_RZ branches later read those
three fields from the parsed dictionary `i`, so they always receive neutral
defaults. The frozen pilot NPZs contain real completed-bar events (3–13
STDEV_REJECT events per pilot key under the configured D thresholds), making
the current zero-close evidence a reader defect rather than a domain plateau.

The candidate implementation is
`tradier_exit_indicator_contract_c5.py`. The exact source edit and its inverse
are stored in:

- `data/reports/patches/c5_stdev_raw_indicator_reader.patch`
- `data/reports/patches/c5_stdev_raw_indicator_reader.rollback.patch`

## Controlled cutover

1. Stop all c4 exact workers and wait for their engine subprocesses to finish.
2. Record the final c4 fingerprint and DB row counts; do not relabel c4 rows.
3. Apply the candidate patch to `tradier_manage.py`.
4. Bump `MATRIX_CONTRACT_VERSION` to
   `tradier-matrix-exec-c5-stdev-raw-20260730`, bump the recipe version, add
   `tradier_exit_indicator_contract_c5.py` to `MATRIX_CONTRACT_FILES`, and
   update contract allowlists in reporting/audit tests in one deployment.
5. Run the c5 regression tests and the full repaired-contract suite before
   launching workers.
6. Start one exact worker only. Re-run STDEV_REJECT true on MU_LONG and
   ACN_SHORT. A valid receipt must show the c5 fingerprint and at least one
   causal STDEV close/reason where a frozen raw event exists.
7. Compare false/true fingerprints and event counters. Do not call a
   no-event key “wired different”; preserve legitimate plateaus.
8. Start the remaining workers only after the one-worker receipts pass.

## Rollback

Stop c5 workers, apply the rollback patch, restore the c4 contract/recipe
version and source allowlists, run the c4 contract tests, then restart. Keep
all c5 rows with their c5 fingerprints as historical evidence; never rewrite
them as c4.
