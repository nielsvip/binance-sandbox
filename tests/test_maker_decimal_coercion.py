"""Maker REDUCE-timeout Decimal×float crash regression (2026-10-05).

Live crash, 4x in /Users/niels/logs/ez_manage_flz.log on Oct 4 01:33-01:50:
  [MAKER_CRITICAL_FAIL] flz:DASHUSDT_LONG: unsupported operand type(s)
  for *: 'decimal.Decimal' and 'float'

Root cause: place_maker_order REDUCE-timeout fallback evaluated
  `remaining > (step * 0.5)` with step: Decimal (built at `tick/step = ...`
  Decimal(...) lines) and 0.5: float. Decimal.__mul__(float) raises
  TypeError, so EVERY REDUCE reaching the 90s-timeout fallback crashed
  instead of sending the MARKET fallback / logging MAKER_EXIT_DONE.

Fix: `step * Decimal("0.5")`, matching the Decimal conventions used by all
surrounding maker qty math ((Decimal(...) // step) * step).
"""

import inspect
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _maker_source():
    import ez_manage as ez
    return inspect.getsource(ez.MultiAccountTradeManager.place_maker_order)


def test_old_expression_reproduces_live_crash():
    """Pin the exact crash mechanism/signature seen in the live log."""
    step = Decimal(str(0.001))  # DASHUSDT step_size per live [MAKER_QTY] lines
    try:
        step * 0.5
    except TypeError as e:
        assert str(e) == "unsupported operand type(s) for *: 'decimal.Decimal' and 'float'"
    else:
        raise AssertionError("expected TypeError for Decimal * float")


def test_no_decimal_times_float_literal_in_maker():
    """The crashing `step * 0.5` float-literal multiply must be gone.

    Fails on pre-fix source, passes after the Decimal("0.5") coercion.
    """
    src = _maker_source()
    bad = re.findall(r"step\s*\*\s*0\.5(?!\d)", src)
    assert not bad, f"Decimal*float crash pattern still present: {bad}"
    assert 'step * Decimal("0.5")' in src, "expected Decimal-coerced threshold"


def test_reduce_threshold_replay_dashusdt_values():
    """Replay the crashed DASHUSDT REDUCE-timeout comparison numerically.

    Live values: step=0.001, qty_abs=0.424000 side=SELL (REDUCE) per
    `04 01:19:06 [MAKER_QTY] flz:DASHUSDT_LONG` (crash window).
    """
    step = Decimal(str(0.001))
    # Full remainder after timeout with zero fills -> MARKET fallback branch
    remaining = 0.424000 - 0.0
    assert remaining > (step * Decimal("0.5"))
    # Dust remainder below half-step -> MAKER_EXIT_DONE branch, no crash
    assert not (0.0004 > (step * Decimal("0.5")))
