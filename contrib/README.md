# Contributor workspace contract

This folder documents the no-SSH worker model for macOS and Ubuntu machines.
Use a mounted/shared folder for inputs and outputs; do not put NPZ files or
credentials in the repository.

Required environment variables:

```bash
export NPZ_ROOT=/mnt/trb-npz/stocks_repaired_20260730_c5
export RESULT_ROOT=/mnt/trb-results
export MATRIX_DROP=/mnt/trb-matrix-drop
export WORKER_ID="$(hostname)-$(uname -m)"
```

`NPZ_ROOT` is read-only. `RESULT_ROOT` is private to the worker. `MATRIX_DROP`
is a shared folder for immutable result bundles; only the S1 aggregator reads
and ingests it. The worker never needs SSH access.

Run `python tools/contributor_preflight.py` before any campaign. The generated
manifest is part of the result bundle and is required for ingestion.

## Supported platforms

- macOS ARM64/x86_64
- Ubuntu ARM64/x86_64
- Python 3.11 or newer

The preflight reports missing compiler, NumPy, or NPZ fields. A worker may run
only the lanes whose prerequisites pass; it must record the quarantine reason
instead of writing zeros.
