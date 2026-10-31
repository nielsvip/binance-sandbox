import hashlib
import importlib.util
import json
from pathlib import Path


spec = importlib.util.spec_from_file_location("vector_provenance_test", Path(__file__).resolve().parents[1] / "tools/vector_direct_trader.py")
trader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trader)


def test_snapshot_survives_same_bar_settings_replacement(tmp_path):
    state = {"npz_tail": 900, "ts": "first", "settings_sha256": "old", "acts_last_bar": [{"type": "OPEN", "reason": "REENTRY_LOWER_HIGH"}]}
    first = trader.persist_decision_state(tmp_path, state)
    state["settings_sha256"] = "new"
    second = trader.persist_decision_state(tmp_path, state)
    assert first["state_sha256"] != second["state_sha256"]
    saved = json.loads((tmp_path / first["state_file"]).read_text())
    assert saved["settings_sha256"] == "old"
    digest = hashlib.sha256(json.dumps(saved, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    assert digest == first["state_sha256"]
    assert trader.persist_decision_state(tmp_path, saved) == first


def test_order_records_actual_consumed_state_without_rereading_live_file(tmp_path):
    cls = next(value for value in vars(trader).values() if isinstance(value, type) and hasattr(value, "capture_order_states"))
    runner = cls.__new__(cls)
    runner.out = tmp_path
    states = {"X_LONG": {"npz_tail": 900, "engine_ast_sha256": "engine", "open": True}}
    orders = [{"acct": "ang", "ss": "X_LONG", "act": "OPEN"}]
    runner.capture_order_states(orders, states)
    states["X_LONG"]["open"] = False
    saved = json.loads((tmp_path / orders[0]["decision_provenance"]["state_file"]).read_text())
    assert saved["open"] is True
    assert saved["engine_ast_sha256"] == "engine"
