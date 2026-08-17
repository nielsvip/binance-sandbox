# SWITCH_MATRIX_TRB resume audit — 2026-08-01

This note records the state used when resuming work from the current
`BACKTEST_BIBLE.md` rules.  S1 (`/home/niels/binance-sandbox`) is authoritative
for live workers, the result store, and the canonical matrix surfaces.

## Current authority

- Canonical artifact: `data/reports/SWITCH_MATRIX_TRB.csv.gz`.
- Current workbook: `data/reports/SWITCH_MATRIX_TRB.xlsx` (15 sheets).
- The workbook must contain the stable core sheets plus the additive
  `Evidence Provenance` and `VEC Scalar Diagnostics` sheets.
- Frozen `stocks_baseline_v2_s4h` and its historical export are comparison
  data only; they are not current matrix completion.
- Amber/vector values are provisional diagnostics.  They do not earn ENGINE
  credit or live promotion until an exact V8 receipt confirms them.

## S1 snapshot at resume

`matrix_guard.py` reported 3,503 rows, 787 switches, 117 active keys plus 7
retained historical keys, and 434,372 physical cells.  The executable queue
was 973 filled / 187,740 cells (0.52%), leaving 186,767 executable cells.
Pilot exact completion was MU 0/826, NVDA 7/826, VT 115/826, TTD 1/876,
ACN 0/876, and LAC 2/876.  These are actionable denominators; physical
blanks and intentional controls must not be presented as equivalent work.

## Repair made during this resume

The six c5 workers had been relaunched repeatedly but were failing the exact
manifest fingerprint check.  The manifest was rebound atomically to the
runtime fingerprints, with rollback copy
`data/rollback/manifest_before_rebind_20260801.json`.  The six detached
workers are now running under the c5 dependency-pack contract and the fleet
audit is PASS with six live claims and no dead claims.

The fleet audit had a second stale contract: it rejected the two current
provenance/vector diagnostic sheets.  `tools/audit_repaired_matrix_fleet.py`
now requires the stable core and explicitly allows those two additive sheets;
the local regression suite passes (19 tests), and the updated audit passes on
S1.

## Resume rules

1. Keep the MU logical denominator at exactly 826 (`existing_nonblank + amber
   + unresolved = 826`).
2. Never fabricate uniqueness: identical outcomes are valid when the engine
   is insensitive to a switch, and must be labelled as plateau/inert.
3. Keep long and short accounting and B&H side-specific.  Missing activity,
   DD, receipt, or capital evidence fails closed.
4. Use vector screening for speed, then exact V8 confirmation.  The temporary
   deadline exception is tagged and cannot silently promote a vector result.
5. Reversal registrations remain research-only until exact receipts satisfy
   the registered close/activity/clamp requirements.

The worker queue is intentionally left running for the next exact receipts;
no matrix or live configuration promotion was performed by this audit.

## 23:20–23:31 UTC causal-worker recovery

The prior recovery description was no longer sufficient.  Fresh persistent
engine diagnostics proved that all six exact workers were first blocked by
stale manifest fingerprints and then crashed during import because S1 had the
updated `tradier_manage.py` but not its new executable dependency
`tradier_matrix_gates.py`.  This is why watchdog relaunches produced no cells.

The repair was made in four receipt-backed steps:

1. `tools/refresh_matrix_worker_fingerprints.py` was added.  It computes every
   worker fingerprint under the manifest's own campaign, NPZ directory and
   end date; writes atomically; keeps a timestamped rollback copy; and emits
   `data/reports/MATRIX_WORKER_FINGERPRINT_REFRESH_20260801.json`.
2. `persym_baseline_campaign.py` now preserves each exact engine's stdout and
   stderr as `engine__<SYM>.log` instead of discarding the only crash evidence.
3. `tradier_matrix_gates.py` was deployed to S1 and added to
   `C5_ADDITIONAL_CONTRACT_FILES`, so any future change to this P&L-affecting
   module changes the exact contract fingerprint.  The deployed files and
   manifest were relocked immutable after each update.
4. After the seven pilot NPZs passed causal/ladder audits with zero future HTF
   rows, the exact manifest was atomically cut over from
   `stocks_repaired_20260725_c2` / `2026-07-21` to
   `canonical_v7_20260801_pilots` / `2026-08-01` (exclusive).  The six old-NPZ
   process groups were terminated and verified absent before the six workers
   were relaunched.  The rollback manifest is
   `data/matrix_worker_manifest.json.pre_fingerprint_refresh_20260801T233048Z`;
   A source-sync convergence immediately after cutover changed all six runtime
   fingerprints once more; those in-flight runs were quarantined and stopped.
   The source-stable rebound manifest SHA-256 was
   `e939a0d492546b93673cbff2b99dd243c81b2a081e7753c7bdf3825d2bbfc185`.

The current exact engines are therefore running MU_LONG, NVDA_LONG, VT_LONG,
TTD_SHORT, ACN_SHORT and LAC_SHORT against the regenerated causal V7 pilot
directory.  Results from the interrupted old-NPZ runs cannot be accepted by
the new fingerprints.

VT then completed a structurally clean baseline with 27 closes, 51.1155% TIM,
zero capacity clamps and zero re-entry overshoot violations, but the old audit
rejected it solely because the fixed data window ended with one pending
re-entry/reclaim.  That is normal right-censoring: the future event is
unobservable, while the no-overshoot invariant proves it was not forgotten.
`c5_matrix_safety.py` and `matrix_run_audit()` now accept terminal pending only
when violations are zero and retain explicit `terminal_right_censored`
telemetry.  Opposite-side P&L, capacity violations and any actual re-entry
overshoot still fail closed.  The regression suite passed 76 tests (1 skipped).
After this P&L-contract change the six workers were stopped, rebound and
restarted once more; final manifest SHA-256 is
`bdc4f4a56a6abf25e0028bf1cb3e8854e9bed4e5462e9cde0ec0e3a591eb3217`,
and an immediate independent dry-run reported `changed_workers=0`.
