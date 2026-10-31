import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from vec_decisions.asr_channel_confluence_score import apply_channel_confluence_vec
from vec_decisions.asr_input_adapter import paired_event_view, paired_npz_view


def _native_adapter_nodes():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "ez_positions_quick.py").read_text())
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    wanted = []
    for node in ast.walk(rate):
        if isinstance(node, ast.ImportFrom) and node.module == "vec_decisions.asr_input_adapter":
            wanted.append(node)
        elif isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if "position_side" in names or "_asr_ltf_enabled" in names or "_asr_tf_micro" in names or "_asr_tf_scalp" in names or "_asr_tf_focus" in names:
                wanted.append(node)
            elif isinstance(node.targets[0], ast.Tuple) and any(isinstance(target, ast.Name) and target.id == "_asr_ltf_missing" for target in ast.walk(node.targets[0])):
                wanted.append(node)
    return sorted(wanted, key=lambda node: node.lineno)


def _run_native_binding(use_ltf, ind, metrics=None):
    cfg = SimpleNamespace(USE_1M_3M_SIGNALS_ENABLED=use_ltf, TF_MICRO="1m", TF_SCALP="3m", TF_FOCUS="3m")
    env = {"is_long": True, "symbol": "TEST", "ind": ind, "metrics": metrics or {}, "config": cfg,
           "_psym_get": lambda symbol, side, key, default: cfg.USE_1M_3M_SIGNALS_ENABLED}
    exec(compile(ast.Module(body=_native_adapter_nodes(), type_ignores=[]), "native-ASR-input-binding", "exec"), env)
    return env


def test_actual_rate_binding_uses_per_symbol_15m_pair_and_does_not_mutate_input():
    ind = {"k_3m": 3.0, "d_3m": 2.0, "k_3m_prev": 1.0, "k_15m": 77.0, "d_15m": 70.0,
           "k_15m_prev": 65.0, "wt1_3m": -2.0, "wt1_15m": 25.0, "rsi_1h": 44.0}
    metrics = {"k_1m": 1.0, "k_1m_prev": .5, "k_15m": 77.0, "k_15m_prev": 65.0}
    env = _run_native_binding(False, ind, metrics)
    assert env["_asr_ltf_enabled"] is False
    assert env["ind"]["k_3m"] == 77.0 and env["ind"]["d_3m"] == 70.0
    assert env["ind"]["k_3m_prev"] == 65.0 and env["ind"]["wt1_3m"] == 25.0
    assert env["metrics"]["k_1m"] == 77.0 and env["metrics"]["k_1m_prev"] == 65.0
    assert (env["_asr_tf_micro"], env["_asr_tf_scalp"], env["_asr_tf_focus"]) == ("15m", "15m", "15m")
    assert ind["k_3m"] == 3.0 and metrics["k_1m"] == 1.0
    assert env["ind"]["rsi_1h"] == 44.0


def test_actual_rate_binding_keeps_ltf_on_and_exposes_missing_micro_input():
    ind = {"k_3m": 3.0, "k_15m": 77.0}
    env = _run_native_binding(True, ind)
    assert env["_asr_ltf_enabled"] is True
    assert env["ind"]["k_3m"] == 3.0
    assert (env["_asr_tf_micro"], env["_asr_tf_scalp"], env["_asr_tf_focus"]) == ("1m", "3m", "3m")
    assert "k_3m" not in env["_asr_ltf_missing"]
    assert "k_1m" in env["_asr_ltf_missing"] and "wt1_3m" in env["_asr_ltf_missing"]


def test_vector_score_reader_hydrates_actual_15m_peer_when_ltf_off():
    arrays = {"close": np.array([100.0]), "k_3m": np.array([95.0]), "d_3m": np.array([80.0]),
              "k_15m": np.array([80.0]), "d_15m": np.array([70.0]), "k_1h": np.array([50.0]),
              "d_1h": np.array([50.0]), "k_4h": np.array([50.0]), "d_4h": np.array([50.0]),
              "dc_basis_1h": np.array([90.0]), "dc_basis_15m": np.array([95.0]), "dc_basis_4h": np.array([95.0]),
              "dc_high_3m": np.array([101.0]), "dc_low_3m": np.array([90.0]),
              "dc_high_15m": np.array([105.0]), "dc_low_15m": np.array([90.0])}
    actual, _ = apply_channel_confluence_vec(arrays, 1, True, score=np.array([0.0]), current_price=arrays["close"], use_ltf=False)
    paired = paired_npz_view(arrays, False)
    explicit, _ = apply_channel_confluence_vec(paired, 1, True, score=np.array([0.0]), current_price=paired["close"], use_ltf=True)
    assert actual.tolist() == explicit.tolist()
    assert actual[0] != apply_channel_confluence_vec(arrays, 1, True, score=np.array([0.0]), current_price=arrays["close"], use_ltf=True)[0][0]


def test_native_event_cache_selects_15m_pair_when_ltf_off():
    events = {"3m": {"wt_crossover": {"latest": {"price": 3.0}}},
              "15m": {"wt_crossover": {"latest": {"price": 15.0}}},
              "1h": {"wt_crossover": {"latest": {"price": 60.0}}}}
    paired = paired_event_view(events, False)
    assert paired["3m"]["wt_crossover"]["latest"]["price"] == 15.0
    assert paired["1h"]["wt_crossover"]["latest"]["price"] == 60.0
    assert events["3m"]["wt_crossover"]["latest"]["price"] == 3.0
    assert paired_event_view(events, True)["3m"]["wt_crossover"]["latest"]["price"] == 3.0
