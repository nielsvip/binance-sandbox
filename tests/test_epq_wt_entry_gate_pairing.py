import ast
from pathlib import Path
from types import SimpleNamespace
import pytest
import numpy as np
import v12_quick_engine as V

@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("ltf_enabled", [True, False])
def test_actual_native_quality_gate_reads_paired_wt_when_ltf_disabled(is_long, ltf_enabled):
    tree = ast.parse(Path("ez_positions_quick.py").read_text())
    names = {"_gate_ltf_tf", "_gw1_3m", "_gw2_3m"}
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)]
    assert len(nodes) == 3
    nodes.sort(key=lambda node: node.lineno)
    signed = 1 if is_long else -1
    ind = {"wt1_3m": -20*signed, "wt2_3m": 0, "wt1_15m": 20*signed, "wt2_15m": 10*signed}
    ns = {"_gate_symbol": "TEST", "_gate_is_long": is_long, "_gate_ind": ind, "config": SimpleNamespace(USE_1M_3M_SIGNALS_ENABLED=not ltf_enabled), "_psym_cs_get": lambda *args: ltf_enabled, "safe_fetch_float": lambda value, default: float(value) if value is not None else default}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "actual_native_wt_inputs", "exec"), ns)
    tf = "3m" if ltf_enabled else "15m"
    assert ns["_gw1_3m"] == ind[f"wt1_{tf}"]
    assert ns["_gw2_3m"] == ind[f"wt2_{tf}"]
    if not ltf_enabled:
        cfg = SimpleNamespace(BASE_TF="15m")
        arrays = {k: np.asarray([v], dtype=float) for k,v in ind.items()}
        assert ns["_gw1_3m"] == V._base_safe(arrays, "wt1", 1, cfg)[0]
        assert ns["_gw2_3m"] == V._base_safe(arrays, "wt2", 1, cfg)[0]
