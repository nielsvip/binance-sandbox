# S1 V8 replay status — 2026-08-04

Matrix-only execution record. No live configuration, live orders, canonical
exact DB, or promotion writes were performed.

## Vector beam completion

The S1 lifecycle beam completed for the first four queue keys:

| key | rows | best gain %/mo | sided B&H %/mo | delta pp/mo | real closes |
|---|---:|---:|---:|---:|---:|
| USAR_LONG | 8,874 | 214.7467 | 0.3873 | 214.3594 | 706 |
| PSX_LONG | 8,874 | 1.7084 | -0.8376 | 2.5460 | 37 |
| ACN_SHORT | 8,874 | 9.9849 | 0.9457 | 9.0392 | 275 |
| TTD_SHORT | 8,658 | 21.9541 | 2.6944 | 19.2597 | 105 |

These are VECTOR_LIFECYCLE results, not exact V8 results.

## Exact V8 admission results

- `USAR_LONG`: `STRICT_VECTOR_EXACT_ADMISSION_DENIED` — no current
  hash-bound positive-return multi-fold winner.
- `ACN_SHORT`: `R4_STRICT_V8_ADMISSION_DENIED` —
  `TRAIN_OR_FINAL_BH_OBJECTIVE_GATE_FAILED`.
- `PSX_LONG`: current hotlist surface is non-unique/duplicate and cannot be
  selected safely by the full-recipe runner.
- Existing S1 full-recipe status contains no accepted pass for these new
  lifecycle queue rows.

## Admission-denied repair and recovery result

The recovery planner had been emitting a legacy plan without the executable
chain hashes required by the resource-guarded executor. That produced a
false `PLAN_VECTOR_ENGINE_HASH_MISMATCH` before qualification started. The
planner now emits the top-level `v8_vec_sweep_sha256` plus all six chain
hashes, and the regenerated 31-row plan was accepted by S1.

The bounded recovery runs completed with current frozen artifacts:

| key | packs | train strict survivors | untouched-final strict survivors | result |
|---|---:|---:|---:|---|
| USAR_LONG | 3 | 1 | 0 | `REJECTED_STRICT_FOLD` |
| ACN_SHORT | 3 | 0 | 0 | `REJECTED_STRICT_FOLD` |

The false planner blocker is fixed, but neither candidate has a fresh
all-fold strict receipt. Exact V8 correctly remains admission-denied; there
is no safe live promotion to make yet.

The exact V8 runner was not bypassed. No candidate is live-eligible until a
supported current-contract recipe passes V8 with its NPZ/code hashes, real
closes, capacity/reclaim checks, and receipt identity.
