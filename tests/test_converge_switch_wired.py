"""USER 2026-10-21 BOARD-STATE CONVERGE: pins the in-manage switch wiring.

Defaults ON (explicit user order: force board-state parity); kill switch
present; loops execute only through execute_now; stale inputs refused.
Source-level pins, no live stack import.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_converge_defaults_on():
    import config as C
    import config_tradier as CT

    assert C.Config.BOARD_STATE_CONVERGE_ENABLED is True
    assert CT.TradierConfig.BOARD_STATE_CONVERGE_ENABLED is True


def test_converge_loops_wired():
    ez = (ROOT / "ez_manage.py").read_text()
    tr = (ROOT / "tradier_manage.py").read_text()
    for src in (ez, tr):
        i_fn = src.index("async def board_state_converge_loop")
        assert "BOARD_STATE_CONVERGE_ENABLED" in src[i_fn:i_fn + 2000]
        assert "execute_now" in src[i_fn:i_fn + 9000]
        assert "StaleInput" in src[i_fn:i_fn + 9000]
        assert "already_forced" in src[i_fn:i_fn + 9000]
        assert "build_record" in src[i_fn:i_fn + 9000]
    assert "board_state_converge_loop(trade_manager)" in ez
    assert "board_state_converge_loop(self)" in tr


def test_converge_no_order_bypass():
    for fn in ("ez_manage.py", "tradier_manage.py"):
        src = (ROOT / fn).read_text()
        i_fn = src.index("async def board_state_converge_loop")
        region = src[i_fn:i_fn + 9000]
        assert "futures_create_order" not in region
        assert "/fapi/v1/order" not in region


def test_ratio_rebalancer_never_opens():
    src = (ROOT / "tradier_manage.py").read_text()
    i_fn = src.index("async def intraday_ratio_rebalance_loop")
    i_end = src.index("async def get_rankings", i_fn)
    region = src[i_fn:i_end]
    assert 'action="AUGMENT"' in region
    assert 'action="REDUCE"' in region
    assert "INTRADAY_RATIO_OPEN_" not in region
    assert "no balancer opens" in region


def test_portfolio_ratio_controls_do_not_emit_open_or_close_actions():
    src = (ROOT / "tradier_manage.py").read_text()
    i_now = src.index("async def execute_now(")
    now_guard = src[i_now:i_now + 1400]
    assert 'startswith("INTRADAY_RATIO_")' in now_guard
    assert '_ratio_action == "AUGMENT"' in now_guard
    assert '_ratio_action == "REDUCE"' in now_guard
    assert "math.floor(_ratio_position_qty) - 1" in now_guard
    i_sector = src.index("# 2026-04-26 Per-sector L/S ratio check")
    sector = src[i_sector:i_sector + 900]
    assert 'action == "AUGMENT"' in sector
    assert "is_entry_action" not in sector
    i_qta = src.index("async def queue_trade_action(")
    qta = src[i_qta:src.index("async def continuous_queue_processor(", i_qta)]
    assert 'action == "AUGMENT"' in qta
    assert "ls_ratio_scale, ls_ratio_reason = 1.0, \"\"" in qta
