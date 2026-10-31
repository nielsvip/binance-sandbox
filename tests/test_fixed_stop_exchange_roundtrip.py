import pytest
from vec_decisions.fixed_stop import build_stop_params, exchange_matches_software, breached


@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("soft,tick", [(0.004765157187357545, 0.0000001), (101.23456, 0.01), (71000.123, 0.1)])
def test_rounded_exchange_stop_preserves_exact_software_touch(is_long, soft, tick):
    exchange = float(build_stop_params(is_long, soft, tick)["stopPrice"])
    assert exchange_matches_software(is_long, soft, exchange, tick)
    assert breached(is_long, soft, soft)
    assert not exchange_matches_software(is_long, soft * 1.02, exchange, tick)


@pytest.mark.parametrize("tick", [0, None, float("nan")])
def test_unknown_tick_does_not_claim_exchange_equivalence(tick):
    assert not exchange_matches_software(True, 100.0, 99.85, tick)


def test_actual_native_restore_branch_keeps_roundtrip_level_and_repairs_real_mismatch():
    import ast
    import asyncio
    import logging
    from pathlib import Path
    from types import SimpleNamespace
    from vec_decisions import fixed_stop
    tree = ast.parse(Path("ez_manage.py").read_text())
    branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If) and ast.unparse(node.test).startswith("isinstance(_fx_saved, dict)") and "entry_sig" in ast.unparse(node.test))
    fn = ast.AsyncFunctionDef(name="run", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]), body=[ast.Global(names=["_fx_lvl", "_fx_anchor"]), branch], decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    soft = 0.004765157187357545
    exchange = float(fixed_stop.build_stop_params(True, soft, 1e-7)["stopPrice"])
    position = SimpleNamespace(fixed_stop_price=soft, fixed_stop_anchor=100.0)
    async def tick(account, symbol):
        return 1e-7
    scope = {"_fx_saved":{"entry_sig":"same", "stopPrice":exchange}, "_fx_oa":"same", "_fx_is_long":True, "_fx_lvl":soft, "_fx_oa_ep":100.0, "_fx_anchor":100.0, "position":position, "trade_manager":SimpleNamespace(_fixed_stop_tick=tick), "account_key":"ang", "symbol":"1000PEPEUSDC", "position_key":"ang:1000PEPEUSDC_LONG", "_fixed_stop_core":fixed_stop, "safe_fetch_float":lambda x,d:float(x or d), "logger":logging.getLogger("test")}
    exec(compile(module, "native_stop_branch", "exec"), scope)
    asyncio.run(scope["run"]())
    assert position.fixed_stop_price == soft
    scope["_fx_lvl"] = soft * 1.02
    position.fixed_stop_price = soft * 1.02
    asyncio.run(scope["run"]())
    assert position.fixed_stop_price == exchange * (0.9975 / 0.996)
