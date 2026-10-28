import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_queue_order_biggest_held_first_then_rest(monkeypatch):
    fake = types.ModuleType("tools.v15_forward_state")
    fake._one, fake.OUT, fake.store_db = (lambda s: {}), Path("/tmp"), (lambda: "")
    monkeypatch.setitem(sys.modules, "tools.v15_forward_state", fake)
    spec = importlib.util.spec_from_file_location("fw", ROOT / "tools" / "fwdstate_npz_watch.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    pending = {"A_LONG": 1, "B_LONG": 0, "C_SHORT": 0, "D_LONG": 1, "E_LONG": -1}
    assert m.queue_order(pending, {"B_LONG": 10.0, "C_SHORT": 80.0}) == ["E_LONG", "C_SHORT", "B_LONG", "A_LONG", "D_LONG"]
