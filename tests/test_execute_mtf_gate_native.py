import ast
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import mtf_live_evaluator as native
from vec_decisions.execute_mtf_gate import execution_passes, prepare

@pytest.fixture(scope="module")
def native_gate_code():
    tree = ast.parse(Path("ez_manage.py").read_text())
    matches = [node for node in ast.walk(tree) if isinstance(node, ast.Try) and any(isinstance(child, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_reason_up_mtf" for target in child.targets) for child in node.body)]
    assert len(matches) == 1
    function = ast.parse("async def native_gate():\n    pass\n").body[0]
    function.body = [matches[0], ast.Return(value=ast.Constant(value="ALLOW"))]
    return compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), "native_execute_now_mtf", "exec")

@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("reason,action", [("ordinary", "OPEN"), ("ordinary", "QUICK_OPEN"), ("LEGACY_PSR_REENTRY", "OPEN"), ("ordinary", "AUGMENT"), ("DC_BREAKOUT", "OPEN"), ("NOT A TRADEABLE KEY QUICK_OPEN", "OPEN"), ("MOMENTUM_WATCHDOG", "OPEN")])
def test_native_execution_gate_sequence(native_gate_code, monkeypatch, side, reason, action):
    monkeypatch.setattr(native, "save_mtf_states", lambda states: None)
    monkeypatch.setitem(sys.modules, "live_filter_per_sym_retest", SimpleNamespace(maybe_queue_filter_retest=lambda *args: False))
    n = 8
    data = {"close_1h": np.full(n, 100.), "wt1_1h": np.array([-2., -1., 2., 1., -1., -2., 3., 2.]), "wt2_1h": np.zeros(n), "dc_high_1h_prev": np.full(n, 110.), "dc_low_1h_prev": np.full(n, 90.)}
    cfg = SimpleNamespace(MTF_ARMED_ENTRY_ENABLED=True, MTF_ARMED_HTF_LIST="1h", MTF_ARMED_BANDTYPES="wt", MTF_ENTRY_REQUIRE_GR_FILTER=False, MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED=False)
    prepared = prepare(data, n, side == "LONG", cfg, np.full(n, 100.))
    manager = SimpleNamespace(mtf_states={})
    clock = SimpleNamespace(value=0.)
    row = {}
    async def indicators(*args):
        return row
    logger = SimpleNamespace(warning=lambda *args: None, critical=lambda *args: None)
    namespace = {"reason": reason, "action": action, "_kill_act": action, "position_key": "ang:TEST_" + side, "symbol": "TEST", "position_side": side, "account_key": "ang", "old_price": 100., "quantity": 1., "config": cfg, "time": SimpleNamespace(time=lambda: clock.value), "_PROCESS_START_TS": 0., "self": manager, "ii": indicators, "safe_fetch_float": lambda value, default: float(value) if value is not None else default, "_psym_get": lambda symbol, side, key, default: getattr(cfg, key, default), "logger": logger}
    exec(native_gate_code, namespace)
    for index in range(n):
        clock.value = index * 30.
        row = {key: values[index] for key, values in data.items()}
        expected = asyncio.run(namespace["native_gate"]())
        actual = execution_passes(prepared, index, action=action, reason=reason, price=100., now=clock.value, process_started_at=0., account_key="ang")
        assert actual == ((True, "") if expected == "ALLOW" else (False, expected.removeprefix("BLOCKED_")))
        if manager.mtf_states:
            assert prepared["state"] == manager.mtf_states["TEST_" + side]
