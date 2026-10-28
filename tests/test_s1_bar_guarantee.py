import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bg", ROOT / "tools" / "s1_bar_guarantee.py")
G = importlib.util.module_from_spec(spec)
sys.modules["bg"] = G
spec.loader.exec_module(G)


def _g(tmp_path):
    g = G.Guarantee.__new__(G.Guarantee)
    g.venue, g.out, g.vd, g.state_dir = "crypto", tmp_path / "bg", tmp_path / "vd", tmp_path / "fs"
    for d in (g.out, g.vd, g.state_dir):
        d.mkdir(parents=True)
    g.trader_log, g.bells, g.state_cache = tmp_path / "trader.log", [], {}
    bar = (int(time.time()) // 900 - 1) * 900
    side = lambda held: {"accts": ["ang"], "held": held, "state_ok_at": None, "exec_at": None, "untr_at": None, "untr_ack_at": None, "reason": "x",
                         "orders": [], "consumed": {}, "repairs": []}
    g.bar = {"bar": bar, "close": bar + 900, "sides": {"AUSDT_LONG": side(["ang"]), "BUSDT_SHORT": side([])},
             "syms": {"AUSDT": {"recv_at": 1, "append_at": None, "repairs": []}, "BUSDT": {"recv_at": 1, "append_at": 1, "repairs": []}}}
    return g, bar


def test_declare_marks_untradeable_rings_and_resolves_on_trader_ack(tmp_path):
    g, bar = _g(tmp_path)
    g.declare({"AUSDT_LONG": "npz failed", "BUSDT_SHORT": "state not ok"}, "npz")
    doc = json.loads((g.vd / "untradeable_crypto.json").read_text())
    assert set(doc["sides"]) == {"AUSDT_LONG", "BUSDT_SHORT"} and doc["sides"]["AUSDT_LONG"]["bar"] == bar
    assert (g.vd / "PLAN_NOW_crypto").exists() and "CRITICAL" in g.trader_log.read_text()
    assert json.loads((g.out / "BELLS_crypto.json").read_text())["items"][0]["kind"] == "UNTRADEABLE_DECLARED"
    assert not g.complete()
    for ss in g.bar["sides"]:
        g.bar["sides"][ss]["untr_ack_at"] = time.time()
    assert g.complete()


def test_restore_tradeable_when_fresh_ok_state_returns(tmp_path):
    g, bar = _g(tmp_path)
    g.declare({"AUSDT_LONG": "npz failed"}, "npz")
    g.bar = None
    (g.state_dir / "AUSDT_LONG.json").write_text(json.dumps({"ok": True, "npz_tail": bar}))
    g.restore_tradeable()
    assert json.loads((g.vd / "untradeable_crypto.json").read_text())["sides"] == {}
