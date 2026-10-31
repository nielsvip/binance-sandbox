from datetime import datetime, timezone
import ast
from pathlib import Path
from types import SimpleNamespace


def _mark_refresh_helper():
    source = Path("backtest_v12_engine.py").read_text()
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_refresh_sim_position_marks"
    )
    namespace = {"datetime": datetime}
    helper_module = ast.Module(body=[node], type_ignores=[])
    exec(compile(helper_module, "backtest_v12_engine.py", "exec"), namespace)
    return namespace[node.name]


def test_sim_position_mark_uses_each_new_bar_price():
    position = SimpleNamespace(
        mark_price=10.0,
        mark_price_last_updated=None,
        positionAmt=2.0,
        entry_price=10.0,
        gain=0.0,
        prev_gain=0.0,
        max_gain=0.0,
        opened_at=None,
        last_updated=None,
    )
    manager = SimpleNamespace(positions={"acct:ABC_LONG": position})
    prices = {"ABC": 11.0}
    dt1 = datetime(2026, 10, 30, 11, 30, tzinfo=timezone.utc)

    refresh = _mark_refresh_helper()
    refresh(manager, prices, dt1)

    assert position.mark_price == 11.0
    assert position.gain == 10.0
    assert position.mark_price_last_updated == dt1

    prices["ABC"] = 9.0
    dt2 = datetime(2026, 10, 30, 11, 45, tzinfo=timezone.utc)
    refresh(manager, prices, dt2)

    assert position.mark_price == 9.0
    assert position.gain == -10.0
    assert position.mark_price_last_updated == dt2


def test_refresh_occurs_after_current_bar_cache_advance():
    source = Path("backtest_v12_engine.py").read_text()
    start = source.index("async def run_simulation(")
    end = source.index("\n# ═══════════════════════════════════════════════════════════════", start)
    source = source[start:end]
    assert source.index("price_cache[sym] = p") < source.index(
        "_refresh_sim_position_marks(trade_manager, price_cache, _sim_dt)"
    ) < source.index("await ez_manage.process_position(")
