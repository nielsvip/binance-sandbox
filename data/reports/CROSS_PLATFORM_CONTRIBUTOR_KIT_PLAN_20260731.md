# Cross-platform backtest contributor kit plan

Status: design only. This plan does not authorize matrix, database, live-config,
promotion, or remote-host writes.

## 1. Objective and trust boundary

The kit lets an external machine run one frozen research assignment using one
explicitly supplied NPZ without cloning the live repository or receiving SSH
access. A contributor may read the frozen kit and assigned NPZ and may write
only to a local/output folder. Returned artifacts are quarantined research
receipts until an owner-side validator accepts them; they never flow directly
to `param_results_stocks.db`, `SWITCH_MATRIX_TRB.csv.gz`, `active_config.json`,
ENGINE ranking, or promotion.

The first supported assignment is the MU combination explorer. The packaging
contract should be generic enough to add other read-only vector research tools
later, but v1 must not bundle the exact engine or live trading code.

## 2. Frozen kit layout

Each published kit is an immutable, versioned directory or archive:

```text
contributor-kit-<kit_id>/
  KIT_MANIFEST.json
  CHECKSUMS.sha256
  README_RUN.md
  ASSIGNMENT.schema.json
  RESULT_RECEIPT.schema.json
  requirements.lock
  bootstrap.py
  verify_bundle.py
  run_assignment.py
  validate_outbox.py
  src/
    mu_combo_explorer.py
    vec_top_exit_campaign.py
    vec_top_exit_scan.c
    backtest_data_contract.py
    research_availability_clock.py
  assignments/
    <job_id>.json
  inputs/
    npz/
      MU.npz
  wheelhouse/                 # optional offline platform variant
    <platform_tag>/
  outbox/                     # initially empty; only writable kit subtree
```

The MU v1 source closure is exactly the five files under `src/` above. The kit
builder must import-test the closure in a clean temporary directory; no source
file may resolve imports from the contributor's checkout, home directory, or
the live Binance repository.

`KIT_MANIFEST.json` records the SHA-256 and size of every distributed file,
the kit/job schema versions, allowed entry points, supported Python/NumPy
versions, and the single permitted output subtree. It also records these hard
false authority fields:

```json
{
  "research_only": true,
  "exact_completion_credit": false,
  "engine_ranking_allowed": false,
  "db_engine_write_allowed": false,
  "promotion_allowed": false,
  "live_config_write_allowed": false,
  "matrix_write_allowed": false
}
```

Build the archive only after the source is frozen. For the current MU source,
the pending assignment expects `mu_combo_explorer.py` SHA-256
`066492c51e3982ee89d06e43b68eb6663e15c234f5b5a954a43fc04f34ffffbe`;
the kit builder must recompute this value and fail on any mismatch rather than
silently updating the assignment.

Do not include `.git`, credentials, SSH material, environment files, broker
data, live positions, active configurations, databases, matrix files, cached
emails, or unrelated symbol NPZ files.

## 3. Safe read-only NPZ contract

Only the NPZ files named by the assignment are supplied. For MU v1 that is
`inputs/npz/MU.npz`.

Safety is enforced in layers because `chmod` is not reliable on every shared
filesystem:

1. The owner records the NPZ SHA-256, byte size, symbol, profile, and allowed
   date window in both the kit and assignment manifests.
2. `verify_bundle.py` rejects missing files, symlinks, hard-link aliases,
   unexpected NPZs, checksum changes, and any real path outside `inputs/npz`.
3. The launcher opens NPZ data only with `numpy.load(..., allow_pickle=False)`
   and calls the bundled read-only `audit_npz(..., profile="ladder")` before
   computation.
4. The runner resolves input and output paths and refuses to start if either
   contains the other. Outputs may only be created under `outbox/<run_id>/`.
5. The NPZ is hashed immediately before and after the run. A changed hash
   produces `INPUT_MUTATED` and invalidates every metric.
6. The publisher marks `inputs/` non-writable where supported (`chmod -R
   a-w inputs` on Unix). This is defense in depth, not the sole control.
7. The application never rewrites availability clocks in the NPZ. The
   bundled research clock creates only in-memory views.

The assignment must disclose NPZ provenance and data-licensing permission.
Do not redistribute market data until the owner confirms that the source
license permits it.

## 4. Assignment contract

An assignment JSON freezes all degrees of freedom. Required fields are:

- `schema`, `kit_id`, `job_id`, `attempt_id`, `created_utc`, and optional
  expiry;
- source file hashes and input NPZ hash/size;
- symbol, side, start/end, campaign tag, slippage, candidate cap, execution
  clock, and expected candidate/re-entry/maximum-row inventory;
- exact command arguments as a JSON array, never a shell string;
- expected output filenames and result schema;
- CPU/memory guidance and maximum wall-clock guidance;
- all authority flags above, plus `required_next_stage`;
- an owner signature or detached manifest hash when signing infrastructure is
  not yet available.

The launcher rejects unknown arguments and environment-provided strategy
overrides. It sets a deterministic environment (`TZ=UTC`, `LC_ALL=C`,
`PYTHONHASHSEED=0`, and one thread for BLAS/OpenMP) and records the effective
environment in the receipt. Contributors may not edit an assignment. A retry
uses a new `attempt_id` and preserves the failed attempt.

## 5. Result/output contract

Each attempt writes to `outbox/<run_id>.partial/` and atomically renames that
directory to `outbox/<run_id>.ready/` only after validation. A complete ready
directory contains:

```text
result.json
RESULTS.md
receipt.json
stdout.log
stderr.log
CHECKSUMS.sha256
```

`result.json` remains the native research payload. For MU v1 it must retain
`schema=MU_COMBO_EXPLORER_V1`, `tier=VEC_RESEARCH`, the frozen inventory,
per-row diagnostics, B&H telemetry, capacity disclosure, and hard-false
promotion authority. NaN/Infinity JSON is prohibited.

`receipt.json` is the portable envelope and must include:

- kit/job/run/attempt IDs and start/end timestamps;
- `status` from `COMPLETE`, `FAILED`, `INTERRUPTED`, or `INPUT_MUTATED`;
- OS, release, machine architecture, CPU count, Python, NumPy, compiler
  identity, and compiled scanner hash;
- exact argv and deterministic environment;
- all source/input hashes before and after;
- exit code, elapsed seconds, peak RSS when available, row counts, and result
  hash;
- warnings, exception class/message, and stdout/stderr hashes;
- all false authority fields and `required_next_stage=EXACT_V8_REPLAY_WITH_TRADE_LEDGER`.

Failure is also a result: the wrapper must write a failure receipt and logs,
then leave no `.ready` directory. It must never replace an older attempt.

Owner-side intake validates checksums, schemas, authority flags, expected row
inventory, finite numbers, input/source identity, and duplicate run IDs. It
extracts archives in a size-limited quarantine that rejects absolute paths,
`..`, symlinks, devices, and archive expansion abuse. Accepted artifacts are
copied to a research-only receipt store. Promotion or matrix ingestion remains
a separate exact-engine workflow.

Cross-architecture comparisons use declared numeric tolerances for floating
metrics but require exact candidate labels, event counts, and action/order
fingerprints. A small golden fixture must run on every supported platform
before a full result is trusted.

## 6. Bootstrap matrix

The supported baseline is CPython 3.11.x and NumPy 1.26.4. The compiled C
scanner is built natively on each machine; compiled binaries are never shared
between architectures.

| Platform | Architectures | Prerequisites | Bootstrap |
|---|---|---|---|
| macOS 13+ | Apple Silicon `arm64`, Intel `x86_64` | Xcode Command Line Tools (`cc`/Apple clang), native CPython 3.11 | create `.venv`; install locked requirements; compile scanner; run golden self-test |
| Ubuntu 22.04/24.04 | `aarch64`, `x86_64` | `build-essential`, `python3.11`, `python3.11-venv` (or the kit's pinned Python bootstrap) | create `.venv`; install locked requirements; compile scanner with GCC/clang; run golden self-test |

`bootstrap.py` must identify OS/architecture and refuse Rosetta or emulated
execution unless the assignment explicitly permits it. It checks that `cc`
can compile and load the scanner, installs only `requirements.lock`, runs
`python -m py_compile` on the source closure, runs the data-contract tests on
a tiny synthetic fixture, and runs the scanner's compiled/reference parity
self-test.

For internet-connected machines, the README may use standard Python downloads
and `pip --require-hashes`. For offline contributors, publish four separate
wheelhouse variants (macOS arm64/x86_64 and manylinux aarch64/x86_64). The
offline bootstrap uses `--no-index --find-links` and verifies every wheel hash.
Do not ask contributors to install from an unpinned global environment.

Docker/Podman may be offered as an optional Linux route, including
`linux/amd64` and `linux/arm64` images, but native execution remains necessary
for macOS coverage. Containers run with `--network none`, the kit/inputs
mounted read-only, and only outbox mounted read-write.

## 7. No-SSH folder-sharing model

Use a storage provider or SMB share with three directional areas:

```text
published/kits/<kit_id>/                   owner writes; contributors read
assignments/<contributor_id>/inbox/        owner writes; one contributor reads
assignments/<contributor_id>/outbox/       contributor writes; owner reads
owner-quarantine/<received_run_id>/        owner only
```

Google Drive, Dropbox, Box, Syncthing, or an SMB/NAS folder can implement this
model. Permissions, not naming conventions, enforce direction. Contributors
download/copy a kit to a local work directory; they do not compute inside a
cloud-synced input directory. Completed `.ready` directories are archived and
uploaded to the assigned outbox using a temporary `.uploading` name, then
renamed to `.ready` after upload. The owner copies ready uploads into
quarantine before validation.

There is no SSH, VPN, shared shell account, remote command execution, or
bidirectional sync with S1. Contributors never see S1 paths. Assignment
commands use kit-relative paths so the same job runs on every platform.

## 8. Contributor and agent run instructions

The README and any delegated coding agent must follow this exact sequence:

1. Copy the versioned kit into a new local directory. Do not edit it in place
   on the shared drive.
2. Run `python3 verify_bundle.py --assignment assignments/<job_id>.json`.
   Stop on any checksum, path, schema, or expiry error.
3. Run `python3 bootstrap.py`. Save its machine-readable preflight receipt.
4. Run `./.venv/bin/python run_assignment.py --assignment
   assignments/<job_id>.json`. Do not translate the manifest into an ad-hoc
   command or add strategy flags.
5. Monitor only `outbox/<run_id>.partial/heartbeat.json` and logs. The wrapper
   updates a heartbeat at least every 60 seconds without rewriting research
   inputs.
6. On failure, return the partial attempt with its failure receipt; do not
   patch code or inputs and rerun under the same attempt ID.
7. Run `./.venv/bin/python validate_outbox.py
   outbox/<run_id>.ready`. Upload only a validator-passing archive.
8. Report the run ID, status, archive hash, platform/architecture, and elapsed
   time. Never describe vector research as exact or promotable.

Agents are specifically prohibited from reading parent directories, searching
for credentials, installing SSH tools, modifying source/NPZ/assignment files,
writing outside outbox, updating the matrix/database/live configuration, or
inventing missing metrics. A useful question may pause an assignment; while
waiting, an agent may run only the frozen preflight and read-only validation.

## 9. Build and acceptance checklist

Before publishing v1, the owner must:

1. Freeze the source and assignment, then generate checksums once.
2. Build in a clean directory and prove that the five-file source closure has
   no repository-relative dependency leak.
3. Add path-containment, symlink, hash-before/hash-after, authority-flag,
   archive-safety, and duplicate-attempt tests.
4. Add a tiny distributable synthetic NPZ and golden result used only for
   cross-platform preflight.
5. Test macOS arm64 and x86_64 plus Ubuntu aarch64 and x86_64, recording
   compiler/runtime identities and golden tolerances.
6. Run one full MU job on an owner-controlled clean machine and compare its
   event/order fingerprint to S1 before inviting contributors.
7. Test interrupted, corrupt-input, stale-assignment, compiler-failure, and
   partial-upload recovery paths.
8. Confirm the returned archive cannot trigger any automatic matrix/live
   ingestion.
9. Obtain explicit permission to distribute the NPZ data.

The kit is ready only when all four platform preflights pass and the owner-side
quarantine validator rejects every deliberately malformed fixture. Until then,
the existing pending S1 manifest remains the authoritative MU job definition.
