"""USER 2026-10-23 ladder fix: the price-level reentry monitor's execute_now
fire call passed (order_queue, trade_manager, ...) positionally into a
(position_key, account_key, ...) signature — every slot misaligned, so no
ladder/file level ever fired (0 [REENTRY_MONITOR] fires on any account log).
Pins the keyword call + crossed-marking + signature binding."""

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _monitor_src():
    src = (ROOT / "ez_manage.py").read_text()
    start = src.index("async def _price_level_reentry_monitor(")
    end = src.index("async def _price_level_reentry_monitor_loop(")
    return src[start:end]


def test_fire_call_is_keyworded():
    mon = _monitor_src()
    assert "position_key=position_key" in mon
    assert "account_key=_acc" in mon
    assert 'action="AUGMENT"' in mon
    assert "original_positionAmt=_rm_amt_now" in mon


def test_old_positional_call_gone():
    mon = _monitor_src()
    assert "trade_manager.order_queue," not in mon
    assert (
        "position_key,\n                    _acc,\n                    _sym," not in mon
    )


def test_ladder_levels_marked_crossed():
    mon = _monitor_src()
    assert '["crossed"] = True' in mon


def test_kwargs_bind_to_execute_now():
    import ez_manage

    sig = inspect.signature(ez_manage.MultiAccountTradeManager.execute_now)
    params = set(sig.parameters)
    for kw in (
        "position_key",
        "account_key",
        "symbol",
        "original_positionAmt",
        "side",
        "position_side",
        "quantity",
        "old_price",
        "unique_id",
        "reason",
        "is_full_close",
        "action",
    ):
        assert kw in params, kw
