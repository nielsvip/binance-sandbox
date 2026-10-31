import ast
from pathlib import Path
from types import SimpleNamespace

from mtf_live_evaluator import gr_filter_pass


def test_tradier_gr_namespace_propagates_ltf_switch_and_pairs_5m_when_off():
    source = Path(__file__).resolve().parents[1] / "tradier_manage.py"
    tree = ast.parse(source.read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_GrNS"
        and any(keyword.arg == "LTF_SUB15M_TRADING_ENABLED" for keyword in node.keywords)
    ]
    assert calls, "Tradier GR_FILTER_ALL_ENTRIES must pass its LTF policy to gr_filter_pass"
    ltf = next(keyword.value for node in calls for keyword in node.keywords if keyword.arg == "LTF_SUB15M_TRADING_ENABLED")
    assert isinstance(ltf, ast.Call)
    assert ast.unparse(ltf) == "bool(_gr_get('LTF_SUB15M_TRADING_ENABLED', False))"

    row = {"wt1_5m": 1.0, "wt2_5m": 0.0, "wt1_15m": -1.0, "wt2_15m": 0.0}
    common = {"MTF_GR_MIN_TFS": 1, "MTF_GR_MIN_IND": 1, "MTF_GR_FILTER_ENABLED": True}
    assert gr_filter_pass(row, "LONG", "tradier", SimpleNamespace(**common, LTF_SUB15M_TRADING_ENABLED=True))
    assert not gr_filter_pass(row, "LONG", "tradier", SimpleNamespace(**common, LTF_SUB15M_TRADING_ENABLED=False))
