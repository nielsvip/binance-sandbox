# Backtest launch-policy audit — 2026-08-02

Authority: `BACKTEST_BIBLE.md` §16.21–§16.25, especially §16.22B. Both Bible
copies had SHA-256
`a94884cd0dc48d7a88759c6a8f8022f3d6cc9583c87e87ade69766b4bb763ee1`
when this audit began.

## Enforced execution boundary

- `VECTOR_DISCOVERY` uses the causal lifecycle path-productivity beam. It loads
  one versioned causal NPZ per key, reuses arrays, screens complete lifecycle
  recipes, retains losing evidence, and publishes OOS/B&H/TIM/DD/activity/
  clamp/causality receipts.
- `backtest_v8_sweep.py` and `backtest_v8_engine.py` are not discovery tools.
  They may verify only the top one or two already-defined, behavior-unique,
  complete vector recipes. Scalar component deltas may never be summed into a
  recipe result.
- A legacy scalar lane may not search `all_tiers`, dependency packs, numeric
  grids, or the broad universe. A future exact launch needs a hash-bound vector
  winner receipt and complete resolved recipe.

## S1 process and scheduler findings

At approximately 02:03 UTC, four orphaned broad V8 sweep processes were found:

- crypto `system_combo` over BTC/ETH/SOL/XRP;
- Tradier `tradier_grtf7_hunt_resume` over 20 symbols;
- each had a parent sweep process and one child process.

All four were terminated. A second process audit showed no remaining
`backtest_v8_sweep`, `watchdog_sweep`, `watchdog_lab_matrix`, or `engine_ofat`
process.

Two non-canonical `c4_vector_first_cycle.sh` cron entries were disabled. The
active crontab now retains only report-only matrix exports/baseline reporting
and the exact watchdog entry.

The exact watchdog manifest claimed
`EXACT_ENGINE_PARITY_AND_FINALIST_CONFIRMATION`, but its six workers specified
`all_tiers=true` and `DEPENDENCY_PACKS_ONLY`. They were repeatedly relaunched
with stale fingerprints and were not complete-recipe finalist jobs. The lane
was therefore paused with the S1 operator file `data/MATRIX_WORKERS_PAUSED`.
It must remain paused until replaced by hash-bound, complete vector finalists.

## Matrix publication repairs

- Serialized workbook auditing now reconstructs grouped path axes and accepts
  the single permitted two-value plateau only when it exactly matches the
  current hash-recomputed strict receipt (key, parameter, value pair, metric,
  behavior fingerprint, and at least five measured source-axis values).
- Strict vector audit selection no longer trusts file modification time. Every
  candidate is recomputed from its hash-bound source ledgers, and the largest
  valid source union wins deterministically. The current selected union is the
  Cohort-5 LONG union: 34 ledgers, 2,189 raw rows, 278 PASS and 1,911
  quarantined.
- `tools/matrix_guard.py` no longer reopens the retired full-blank broadcast
  ledger. It reads the same largest hash-verified active union as the exporter;
  its provisional count is therefore 278 PASS / 1,911 quarantined rather than
  the polluted 8 / 410,733 observation.
- Preserved exact V8 evidence remains higher precedence and unchanged at 7,595
  rows. Vector cells remain amber diagnostic evidence and do not become exact
  merely by appearing in the workbook.

Verification after these repairs: 189 focused tests passed, one skipped.

## Capacity rule added by operator

Bible §16.22B-F now requires maximum safe parallel useful vector work on S1:
normally 80–95% aggregate CPU and 70–85% RAM, with at least 4 GiB available
and 15% headroom. Utilization may come only from disjoint, validated causal
vector cohorts—not dummy allocation, duplicated datasets, legacy V8 search or
scalar OFAT. The two Bible copies now have SHA-256
`31ba6718cbd9393d693d53f7f35a0ef8772e6efa703b417d22f61f31b0eab97a`.
