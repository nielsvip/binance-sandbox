"""Repo-test bypass for the NPZ freshness gate (tools/opt/v12_pilot.npz_freshness).

Unit tests run against fixture/frozen NPZs that are stale by design — the
STALE_NPZ_SKIPPED gate must not fail them. Production default (env unset) is
enforce; this file only affects pytest runs.
"""
import os

os.environ.setdefault("V12_ALLOW_STALE_NPZ", "1")
