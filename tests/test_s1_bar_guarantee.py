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


def test_declare_marks_untradeable_rings_and_resolves_on_trader_ack(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "MASS_FAIL_PCT", 100.0)
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


def test_restore_tradeable_when_fresh_ok_state_returns(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "MASS_FAIL_PCT", 100.0)
    g, bar = _g(tmp_path)
    g.declare({"AUSDT_LONG": "npz failed"}, "npz")
    g.bar = None
    (g.state_dir / "AUSDT_LONG.json").write_text(json.dumps({"ok": True, "npz_tail": bar}))
    g.restore_tradeable()
    assert json.loads((g.vd / "untradeable_crypto.json").read_text())["sides"] == {}


def _bar(g, bar, n, held=()):
    side = lambda ss: {"accts": ["ang"], "held": ["ang"] if ss in held else [], "state_ok_at": None, "exec_at": None, "untr_at": None, "untr_ack_at": None,
                       "untr_mode": None, "state_last_at": None, "reason": "x", "orders": [], "consumed": {}, "repairs": []}
    names = [f"S{i}USDT_LONG" for i in range(n)]
    g.bar = {"bar": bar, "close": bar + 900, "sides": {ss: side(ss) for ss in names},
             "syms": {ss.rsplit("_", 1)[0]: {"recv_at": 1, "append_at": 1, "repairs": []} for ss in names}}
    return names


def _modes(g):
    return {ss: u["mode"] for ss, u in json.loads((g.vd / "untradeable_crypto.json").read_text())["sides"].items()}


def _kinds(g):
    return [i["kind"] for i in json.loads((g.out / "BELLS_crypto.json").read_text())["items"]]


def test_below_mass_threshold_closes_immediately(tmp_path):
    g, bar = _g(tmp_path)
    names = _bar(g, bar, 8)
    g.declare({names[0]: "npz failed", names[1]: "state not ok"}, "state")  # 2/8 = 25% -> not above
    assert set(_modes(g).values()) == {"close"} and "UNTRADEABLE_DECLARED" in _kinds(g) and "UNTRADEABLE_MASS_HOLD" not in _kinds(g)


def test_above_threshold_holds_one_bar_then_closes_still_failing(tmp_path):
    g, bar = _g(tmp_path)
    names = _bar(g, bar, 8, held=("S0USDT_LONG",))
    g.declare({ss: "state not ok" for ss in names[:5]}, "state")  # 5/8 = 62.5% -> hold
    assert set(_modes(g).values()) == {"hold"} and "UNTRADEABLE_MASS_HOLD" in _kinds(g)
    names2 = _bar(g, bar + 900, 8, held=("S0USDT_LONG",))
    g.declare({ss: "still not ok" for ss in names2[:5]}, "state")  # next bar: closed whatever the share
    assert set(_modes(g).values()) == {"close"} and "UNTRADEABLE_MASS_CLOSE" in _kinds(g)


def test_partial_recovery_releases_recovered_and_closes_the_rest(tmp_path):
    g, bar = _g(tmp_path)
    names = _bar(g, bar, 4)
    g.declare({ss: "state not ok" for ss in names}, "state")  # 4/4 -> hold
    assert set(_modes(g).values()) == {"hold"}
    for ss in names[:2]:
        (g.state_dir / f"{ss}.json").write_text(json.dumps({"ok": True, "npz_tail": bar + 900}))
    _bar(g, bar + 900, 4)
    for ss in names[:2]:
        g.bar["sides"][ss]["state_ok_at"] = time.time()
    g.restore_tradeable()
    assert set(_modes(g)) == set(names[2:])
    g.declare({ss: "still not ok" for ss in names[2:]}, "state")  # 2/4 = 50% but held over -> close
    assert _modes(g) == {names[2]: "close", names[3]: "close"}
