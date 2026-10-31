import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from vec_decisions.quick_open_strong import augment_fire_from_state

def _native():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "ez_positions_quick.py").read_text())
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "calculate_trading_signals")
    for argument in function.args.args:
        argument.annotation = None
    function.returns = None
    def number(value, default):
        try:
            return default if value is None else float(value)
        except (ValueError, TypeError):
            return default
    namespace = {"safe_fetch_float": number, "datetime": datetime, "timezone": timezone, "config": SimpleNamespace(), "_psym_get": lambda *args: args[-1]}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), "actual_native_quick_signals", "exec"), namespace)
    return namespace[function.name]

@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("history", [[], [40.], [40., 40.], [24., 24., 24.]])
@pytest.mark.parametrize("indicators", [{}, {"volatility_1h_%": 76.}, {"0sentiment_strength": 76.}, {"volatility_1h_%": 75.}, {"volatility_1h_%": None, "0sentiment_strength": 90.}, {"volatility_1h_%": 100., "avg_volatility_1h_%": 0.}])
def test_actual_native_signal_calculator_matches_driver_on_identical_history(is_long, history, indicators):
    class Data:
        async def get_hot_state(self, symbol):
            return {}, indicators, 0., 0., 0., 0., True
    candidate = {"gain_list": history.copy(), "positionAmt": 1., "average_entry_price": 100., "current_gain_%": 40., "max_gain": 40.}
    side = "LONG" if is_long else "SHORT"
    result = asyncio.run(_native()(None, f"test:TEST_{side}", candidate, 140., is_long, Data()))
    expected = result["trading_signals"]["action"] in ("STRONG_BUY", "STRONG_SELL")
    cfg = SimpleNamespace(MODE="crypto", QUICK_OPEN_STRONG_VEC_ENABLED=True)
    position = {"qty": 1., "peak_pnl_pct": 40., "_qos_gain_hist": [*history, 40.]}
    actual, _ = augment_fire_from_state(cfg, position, 40., is_long, indicators)
    assert actual == expected
