# C4 repaired-matrix throughput audit — 2026-07-30

Scope: the six manifest-pinned, side-isolated exact workers for `MU_LONG`,
`NVDA_LONG`, `VT_LONG`, `TTD_SHORT`, `ACN_SHORT`, and `LAC_SHORT`.

This audit does **not** authorize live promotion. It does not change exact
recipes, exact-engine accounting, the c4 fingerprint, the frozen
interdependency JSON, or the meaning of an `ENGINE` cell.

## Finding

The zero-cell report pasted on 2026-07-29 was stale. At 00:21 UTC on
2026-07-30 the result store contained 89 current-fingerprint ENGINE rows for
the six pilot keys. The store also contained 3,055 rows carrying the current
campaign name, but most were from superseded fingerprints. Campaign name
alone is therefore not a valid coverage query.

The current scheduler is not trying to run all 3,267 matrix rows for each
pilot. Its manifest selects dependency packs:

| side pack | executable rows | parameters | baseline aliases | exact candidates |
|---|---:|---:|---:|---:|
| LONG exit pack | 59 | 18 | 8 | 51 |
| SHORT exit pack | 77 | 25 | 13 | 64 |

Baseline aliases are recognized before the engine call and do not become
ENGINE results. Helper `STOP_PACK`/`TF_EXCLUDE` rows and unrelated
entry/sizing paths are absent from these queues.

At 00:24 UTC the exact candidates already accepted inside the selected packs
were:

| key | accepted pack cells | cached structural failures | still open |
|---|---:|---:|---:|
| MU_LONG | 5 | 2 | 44 |
| NVDA_LONG | 4 | 1 | 46 |
| VT_LONG | 18 | 0 | 33 |
| TTD_SHORT | 10 | 0 | 54 |
| ACN_SHORT | 11 | 0 | 53 |
| LAC_SHORT | 7 | 0 | 57 |

The counts exclude the 21 known baseline aliases. They also do not claim that
every open tail value must execute: after two exact values the wiring gate can
classify an inert, degenerate, gated, or bad-range parameter and safely stop
the remaining values without fabricating ENGINE evidence.

## Why progress looked slow

Six exact engines were active concurrently. Observed full-window wall time was
approximately:

- VT: 4.5 minutes per cell
- ACN: 6.3 minutes per cell
- TTD/LAC: about 8 minutes per cell
- MU/NVDA: about 10 minutes per cell

This gives roughly 40–50 exact results per hour across the fleet. CPU and
memory were not exhausted: with a separate TTD OOS process there were seven
busy engine processes on a 16-core, 30-GiB host, and about 26 GiB remained
available.

The material scheduling waste was breadth starvation. `all_cells()` correctly
puts the two maximally separated smoke values first for each parameter, but
the dependency scheduler grouped *all* values of a parameter together. A
five-value setting could therefore consume three range-refinement runs before
the next path received even its first wiring smoke. VT demonstrated this
directly by completing all five
`STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS` values while later exit settings
remained untouched.

Repeated cached structural failures were visible in worker logs. They are not
the principal engine-time cost because `run_symbol()` reuses the cached
JSONL/audit rather than launching a new backtest. They remain blank ENGINE
cells, correctly, because a structurally failed exact run is not accepted
evidence.

## Safe acceleration implemented

`prioritize_dependency_pack_cells()` now keeps strict stages:

1. activation ancestors;
2. requested family masters;
3. dependent settings.

Within each stage it schedules the first two values of every parameter before
any third/middle/default tail value. Remaining tails are interleaved
deterministically across parameters.

Consequences:

- family activation still precedes dependent testing;
- the exact engine, audit, ingestion, and dedupe paths are unchanged;
- no vector or preflight result can fill an ENGINE cell;
- aliases are still skipped before execution;
- the two-value exact gate can reject a bad/dead knob before its tail;
- promising wired paths retain every configured tail value;
- a restart deterministically reconstructs the same order and resumes through
  current-contract DB dedupe.

Tests prove dependency-pack completeness, parent/master/child order,
smoke-before-tail breadth, deterministic output, input non-mutation,
baseline-alias handling, and existing exact wiring behavior.

Validation command:

```bash
python3 -m pytest -q \
  test_param_matrix_priority_scheduler.py \
  test_param_matrix_speed_gates.py \
  test_srs_matrix_wiring.py \
  test_exact_wiring_gate.py
```

Expected result for this change: `34 passed, 1 skipped`.

## Deployment and rollback

Deploy only:

- `tools/param_matrix_daemon.py`
- `test_param_matrix_priority_scheduler.py`
- this audit

Do not modify
`data/reports/SWITCH_MATRIX_INTERDEPENDENCY_20260729.json`.

The daemon is a process-guarded orchestration file but is not an exact
fingerprint input. A controlled worker restart is therefore required, while
all six c4 exact fingerprints must remain identical to the pinned worker
manifest.

Safe deployment sequence:

1. Let current exact children finish, or stop the six worker process groups in
   a controlled manner before replacing the daemon. Do not overwrite it in
   the middle of an exact run.
2. Save the old daemon in a timestamped rollback directory.
3. Unlock only the daemon, copy the new file, and relock it.
4. Run the focused tests above.
5. Verify all six exact fingerprints still match
   `data/matrix_worker_manifest.json`.
6. Relaunch the six manifested workers and run
   `tools/audit_repaired_matrix_fleet.py`.
7. Require fleet PASS, zero dead claims, and no unmanifested exact worker.

Rollback is orchestration-only: stop the workers, restore the saved daemon,
relock it, and relaunch the same manifest. Existing ENGINE evidence remains
valid because this scheduling change does not alter exact recipes or
fingerprints. No DB deletion, campaign rename, frozen-file edit, or live config
change is part of either deployment or rollback.

## Optional fleet scaling

The host had capacity for a small number of additional exact workers, but
scaling must be manifest-driven. Never launch an ad-hoc unmanifested worker.
If extra tags are added, each must pin the same key fingerprint, dependency
roots, `DEPENDENCY_PACKS_ONLY` selection, and `no_live_promotion=true`.
The claim and smoke locks then prevent duplicate exact work. Re-run the fleet
audit after every topology change.

