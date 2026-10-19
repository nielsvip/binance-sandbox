"""owned_fresh: a stale owned claim must not block the converge-open retry forever.

USER 2026-10-19 unlock-and-fix: owned_add runs BEFORE every twin OPEN is sent and owned_drop has no live caller,
so a twin OPEN that timed out in dispatch stayed owned (persisted per account) and its VEC_CONVERGE_OPEN retry was
skipped every sweep — tens of thousands of BLOCKED_DISPATCH_TIMEOUT with zero entries. Fresh-owned (< grace) still
blocks the duplicate (2026-10-13 men EGLD double-open lesson); stale-owned retries.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_twins import vec_exact as VX  # noqa: E402


def _seed(pk, at):
    acct = pk.split(":", 1)[0]
    VX._OWNED[acct] = {pk: {"at": at, "reason": "test"}}
    return acct


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def test_missing_key_is_not_fresh():
    acct = _seed("tacct:AAAUSDT_LONG", _now_iso())
    try:
        assert VX.owned_fresh("tacct:BBBUSDT_LONG", max_age_s=60) is False
    finally:
        VX._OWNED.pop(acct, None)


def test_fresh_claim_blocks():
    acct = _seed("tacct:AAAUSDT_LONG", _now_iso())
    try:
        assert VX.owned_has("tacct:AAAUSDT_LONG") is True
        assert VX.owned_fresh("tacct:AAAUSDT_LONG", max_age_s=900) is True
    finally:
        VX._OWNED.pop(acct, None)


def test_stale_claim_retries():
    acct = _seed("tacct:AAAUSDT_LONG", "2026-01-01T00:00:00Z")
    try:
        assert VX.owned_has("tacct:AAAUSDT_LONG") is True
        assert VX.owned_fresh("tacct:AAAUSDT_LONG", max_age_s=900) is False
    finally:
        VX._OWNED.pop(acct, None)


def test_unparsable_stamp_fails_open():
    acct = _seed("tacct:AAAUSDT_LONG", "not-a-stamp")
    try:
        assert VX.owned_fresh("tacct:AAAUSDT_LONG", max_age_s=900) is True
    finally:
        VX._OWNED.pop(acct, None)
