"""OPEN_CEIL clamp pins (USER 2026-10-12: MAX_ORDER_SIZE = 4x per-sym SPS, absolute risk ceiling $8000).

Supersedes the static 2026-10-11 $180 veto (kept dormant in config.py for history):
per-sym SPS varies ($45 default, overrides x conviction), so the max order must be
computed per sym_side, with $8000 as the general ceiling no tier can exceed.

Light by design (Mac trades-only): no engine imports. Pins the config default and
the clamp expressions inside the execute_now ceiling block. Behavioral proof comes
from live [OPEN_CEIL_SPS ...] log lines after the next restart.
"""
import re


def _read(path):
    with open(path) as fh:
        return fh.read()


def test_absolute_max_default_is_8000():
    src = _read("config.py")
    m = re.search(r"OPEN_CEIL_ABSOLUTE_MAX_USD:\s*float\s*=\s*\(\s*([0-9.]+)", src)
    assert m, "OPEN_CEIL_ABSOLUTE_MAX_USD missing from config.py"
    assert float(m.group(1)) == 8000.0, f"absolute max must be 8000.0, got {m.group(1)}"


def test_ceiling_block_applies_4x_and_absolute_clamp():
    src = _read("ez_manage.py")
    assert '_oc_abs = float(getattr(config, "OPEN_CEIL_ABSOLUTE_MAX_USD", 8000.0))' in src
    assert "_oc_hard = max(0.0, min(4.0 * _oc_base, _oc_abs))" in src
    assert "if _oc_ceil > _oc_hard:" in src
    assert "_oc_ceil = _oc_hard" in src
    assert "hard ${_oc_hard:.0f}" in src


def test_static_hard_max_no_longer_read_live():
    src = _read("ez_manage.py")
    assert 'getattr(config, "OPEN_CEIL_HARD_MAX_USD"' not in src
    assert "_psym_cs_get(symbol, position_side, \"OPEN_CEIL_HARD_MAX_USD\"" not in src


def test_ceiling_still_covers_opens_not_reduces():
    src = _read("ez_manage.py")
    assert "OPEN_CEIL_SPS_TIER_ENABLED" in src
    assert "not is_reduce" in src
