"""USER 2026-10-25 scalp kill-switch: Tradier (stocks) floor wiring pins.

The venue-neutral helper (live_trade_history.scalp_floor_refusal) is covered
by tests/test_live_trade_history.py. These pins cover the STOCKS side only:
config value, helper behaviour on stock-shaped reasons, and an AST pin that
TradierTradeManager._execute_now_inner actually calls the floor (guards
wiring rot — the failure mode that left 526 sub-120s stock ledger pairs).
"""
import ast
from pathlib import Path

import live_trade_history as lth


def test_tradier_floor_config():
    import config_tradier
    assert float(config_tradier.TradierConfig.SCALP_MIN_HOLD_SEC) == 180.0
    import config
    assert float(config.Config.SCALP_MIN_HOLD_SEC) == 180.0


def test_tradier_young_close_refused():
    assert lth.scalp_floor_refusal(True, True, "TECHNICAL_TARGET", 45.0, 180.0) == "REFUSED_SCALP_FLOOR"
    assert lth.scalp_floor_refusal(True, False, "DAYTRADE_TARGET", 119.0, 180.0) == "REFUSED_SCALP_FLOOR"


def test_tradier_protection_and_vec_pass():
    assert lth.scalp_floor_refusal(True, True, "FIXED_STOP", 5.0, 180.0) == ""
    assert lth.scalp_floor_refusal(True, True, "HARD_STOP", 5.0, 180.0) == ""
    assert lth.scalp_floor_refusal(True, True, "BROKER_SYNC_CLOSE", 5.0, 180.0) == ""
    assert lth.scalp_floor_refusal(True, True, "VEC_FWD_FLAT", 5.0, 180.0, False, True) == ""
    assert lth.scalp_floor_refusal(True, True, "TECHNICAL_TARGET", None, 180.0) == ""


def test_tradier_inner_calls_floor():
    src = Path("tradier_manage.py").read_text()
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_execute_now_inner":
            body = ast.dump(node)
            if "scalp_floor_refusal" in body and "SCALP_MIN_HOLD_SEC" in body and "_tr_parity_vec_order" in body:
                found = True
    assert found, "TradierTradeManager._execute_now_inner must call scalp_floor_refusal with vec pass-through"


def test_ez_inner_calls_floor():
    src = Path("ez_manage.py").read_text()
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_execute_now_inner":
            if "scalp_floor_refusal" in ast.dump(node):
                found = True
    assert found, "crypto inner gate must keep calling scalp_floor_refusal"
