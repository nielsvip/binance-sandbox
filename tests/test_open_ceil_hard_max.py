"""OPEN_CEIL hard-$28 clamp pins (USER 2026-10-06: reentries included, no tier boost may exceed $28).

Light by design (Mac trades-only): no engine imports. Pins the config default and
the clamp expression inside the execute_now ceiling block. Behavioral proof comes
from live [OPEN_CEIL_SPS ... hard $28] log lines after the next restart.
"""
import re


def _read(path):
    with open(path) as fh:
        return fh.read()


def test_hard_max_default_is_28():
    src = _read("config.py")
    m = re.search(r"OPEN_CEIL_HARD_MAX_USD:\s*float\s*=\s*([0-9.]+)", src)
    assert m, "OPEN_CEIL_HARD_MAX_USD missing from config.py"
    assert float(m.group(1)) == 28.0, f"hard max must be 28.0, got {m.group(1)}"


def test_ceiling_block_applies_hard_clamp():
    src = _read("ez_manage.py")
    assert '_oc_hard = float(getattr(config, "OPEN_CEIL_HARD_MAX_USD", 28.0))' in src
    assert "if _oc_ceil > _oc_hard:" in src
    assert "_oc_ceil = _oc_hard" in src
    assert "hard ${_oc_hard:.0f}" in src


def test_ceiling_still_covers_opens_not_reduces():
    src = _read("ez_manage.py")
    assert "OPEN_CEIL_SPS_TIER_ENABLED" in src
    assert "not is_reduce" in src
