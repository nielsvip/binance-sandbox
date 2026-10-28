import copy
import dataclasses
import types

import pytest

from tools import v15_sim_memo as SM


@dataclasses.dataclass
class Cfg:
    A: int = 1
    B: bool = False
    C: float = 0.5
    UNREAD: str = "x"


def _engine():
    V = types.SimpleNamespace()
    V._MISSING = object()
    V.calls = 0

    def _npz_memo_fp(npz, n, is_long):
        return (n, bool(is_long), len(npz["close"]), float(sum(npz["close"])))

    def _memo_same(a, b):
        return a is b or (type(a) is type(b) and a == b)

    def simulate_one(npz, sym, is_long, cfg, force_initial_seed=False):
        V.calls += 1
        g = cfg.A * 10.0
        if cfg.B:  # C is only read when B is on
            g += cfg.C
        lane = copy.copy(cfg)
        lane.A = 99  # a copy's write stays local and is not an input
        g += 0 if lane.A == 99 else 1
        cfg.SIDE_INFO = {"g": g}  # side-channel write on the caller's cfg
        npz["derived"] = [g]
        return {"gain": g, "ledger": [{"g": g}]}

    V._npz_memo_fp, V._memo_same, V.simulate_one = _npz_memo_fp, _memo_same, simulate_one
    return V


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("V12_SIM_MEMO", "2")
    SM._STATE.update(installed=False, orig=None, hits=0, runs=0)
    SM._INDEX.clear()
    SM._LINEAR.clear()
    SM._CLASSES.clear()
    yield


def test_unread_key_hits_and_replays_side_channel():
    V = _engine()
    assert SM.install(V)
    npz = {"close": [1.0, 2.0]}
    r1 = V.simulate_one(npz, "X", True, Cfg(A=2))
    c2 = Cfg(A=2, UNREAD="changed", C=7.0)  # UNREAD never read; C not read while B is False
    npz2 = {"close": [1.0, 2.0]}
    r2 = V.simulate_one(npz2, "X", True, c2)
    assert V.calls == 1 and SM.stats() == {"hits": 1, "runs": 1}
    assert r2 == r1 and r2 is not r1
    assert c2.SIDE_INFO == {"g": 20.0} and npz2["derived"] == [20.0]


def test_read_key_change_misses_and_matches_fresh():
    V = _engine()
    SM.install(V)
    npz = {"close": [1.0, 2.0]}
    V.simulate_one(npz, "X", True, Cfg(A=2, B=True, C=0.5))
    r = V.simulate_one({"close": [1.0, 2.0]}, "X", True, Cfg(A=2, B=True, C=0.25))
    assert V.calls == 2 and r["gain"] == 20.25
    r3 = V.simulate_one({"close": [1.0, 2.0]}, "X", True, Cfg(A=2, B=True, C=0.25))
    assert V.calls == 2 and r3 == r


def test_type_change_is_a_miss_and_other_npz_or_side_is_a_miss():
    V = _engine()
    SM.install(V)
    V.simulate_one({"close": [1.0, 2.0]}, "X", True, Cfg(A=2))
    V.simulate_one({"close": [1.0, 2.0]}, "X", True, Cfg(A=2.0))  # 2 == 2.0 but a different type: never a hit
    V.simulate_one({"close": [1.0, 3.0]}, "X", True, Cfg(A=2))
    V.simulate_one({"close": [1.0, 2.0]}, "X", False, Cfg(A=2))
    assert V.calls == 4 and SM.stats()["hits"] == 0


def test_disabled_without_env(monkeypatch):
    monkeypatch.setenv("V12_SIM_MEMO", "0")
    V = _engine()
    assert SM.install(V) is False
    V.simulate_one({"close": [1.0]}, "X", True, Cfg())
    V.simulate_one({"close": [1.0]}, "X", True, Cfg())
    assert V.calls == 2
