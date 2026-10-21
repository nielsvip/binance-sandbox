"""USER 2026-10-21 EXACT-PARITY: capital-realloc donor closes have no vector
model, so the switch defaults off. Pins default + gate order in
reallocate_capital_for_winner. Source-level pin, no live stack import."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_realloc_default_off():
    import config as C

    assert C.Config.CAPITAL_REALLOC_ENABLED is False


def test_realloc_gate_before_cooldown():
    src = (ROOT / "ez_positions_quick.py").read_text()
    i_fn = src.index("async def reallocate_capital_for_winner")
    i_gate = src.index("CAPITAL_REALLOC_ENABLED", i_fn)
    i_cd = src.index("_realloc_cooldowns.get", i_fn)
    assert i_fn < i_gate < i_cd, "kill switch must precede any donor-close logic"
    assert "return 0.0, None" in src[i_gate:i_cd + 40]
