"""USER 2026-10-21: portfolio balancer stays ON but existence-neutral (same bars as
vector, never same quantities). Pins default + gate order + donor REDUCE-half
(never CLOSE/flatten) in reallocate_capital_for_winner. Source-level pin, no live stack import."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_realloc_default_on():
    import config as C

    assert C.Config.CAPITAL_REALLOC_ENABLED is True


def test_realloc_gate_before_cooldown():
    src = (ROOT / "ez_positions_quick.py").read_text()
    i_fn = src.index("async def reallocate_capital_for_winner")
    i_gate = src.index("CAPITAL_REALLOC_ENABLED", i_fn)
    i_cd = src.index("_realloc_cooldowns.get", i_fn)
    assert i_fn < i_gate < i_cd, "kill switch must precede any donor logic"
    assert "return 0.0, None" in src[i_gate:i_cd + 40]


def test_realloc_donor_reduce_never_close():
    src = (ROOT / "ez_positions_quick.py").read_text()
    i_fn = src.index("async def reallocate_capital_for_winner")
    region = src[i_fn:src.index("async def execute_trade_wrapper")]
    assert '"REDUCE"' in region, "donor leg must be a REDUCE"
    assert '"CLOSE"' not in region, "balancer must never flatten the donor"
    assert "donor_amt / 2" in region, "donor trim keeps a live stub (half)"
