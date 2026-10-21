"""USER 2026-10-21: OVERNIGHT_GAP_HEDGE on by default (gap protection). Hedge mates
are tagged positions opened pre-close / closed next morning — never board
positions. Pins default + loop gate order. Source-level pin, no live stack import."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_gap_hedge_default_on():
    import config_tradier as C

    assert C.TradierConfig.OVERNIGHT_GAP_HEDGE_ENABLED is True


def test_gap_hedge_gate_before_loop():
    src = (ROOT / "tradier_manage.py").read_text()
    i_fn = src.index("async def overnight_gap_hedge_loop")
    i_gate = src.index("OVERNIGHT_GAP_HEDGE_ENABLED", i_fn)
    i_while = src.index("while getattr(trade_manager", i_fn)
    assert i_fn < i_gate < i_while, "kill switch must precede the hedge loop"
    assert "return" in src[i_gate:i_while]
