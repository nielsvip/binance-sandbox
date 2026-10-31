import copy
import dataclasses
import types

import numpy as np
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


def test_npz_side_effects_are_deep_copied_on_store_and_each_replay():
    V = _engine()
    assert SM.install(V)
    first_npz = {"close": [1.0, 2.0]}
    first = V.simulate_one(first_npz, "X", True, Cfg(A=2))
    first_npz["derived"][0] = -1.0
    first["ledger"][0]["g"] = -2.0
    replay_npz = {"close": [1.0, 2.0]}
    replay = V.simulate_one(replay_npz, "X", True, Cfg(A=2))
    assert replay_npz["derived"] == [20.0]
    assert replay["ledger"] == [{"g": 20.0}]
    replay_npz["derived"][0] = -3.0
    replay["ledger"][0]["g"] = -4.0
    next_npz = {"close": [1.0, 2.0]}
    next_replay = V.simulate_one(next_npz, "X", True, Cfg(A=2))
    assert next_npz["derived"] == [20.0]
    assert next_replay["ledger"] == [{"g": 20.0}]
    assert V.calls == 1 and SM.stats() == {"hits": 2, "runs": 1}


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


def test_same_close_with_different_indicator_arrays_is_a_miss():
    V = _engine()
    calls = {"n": 0}

    def simulate_one(npz, sym, is_long, cfg, force_initial_seed=False):
        calls["n"] += 1
        value = float(np.asarray(npz["indicator"], dtype=float).sum())
        return {"gain": value, "ledger": [{"indicator_sum": value}]}

    V.simulate_one = simulate_one
    assert SM.install(V)
    first = V.simulate_one({"close": [1.0, 2.0], "indicator": np.array([3.0, 4.0])}, "X", True, Cfg())
    second = V.simulate_one({"close": [1.0, 2.0], "indicator": np.array([3.0, 9.0])}, "X", True, Cfg())
    assert calls["n"] == 2
    assert first["gain"] == 7.0 and second["gain"] == 12.0
    assert SM.stats() == {"hits": 0, "runs": 2}


def test_in_place_indicator_mutation_invalidates_memo():
    V = _engine()
    calls = {"n": 0}

    def simulate_one(npz, sym, is_long, cfg, force_initial_seed=False):
        calls["n"] += 1
        value = float(np.asarray(npz["indicator"], dtype=float).sum())
        return {"gain": value, "ledger": [{"indicator_sum": value}]}

    V.simulate_one = simulate_one
    assert SM.install(V)
    npz = {"close": [1.0, 2.0], "indicator": np.array([3.0, 4.0])}
    first = V.simulate_one(npz, "X", True, Cfg())
    npz["indicator"][0] = 10.0
    second = V.simulate_one(npz, "X", True, Cfg())
    assert calls["n"] == 2
    assert first["gain"] == 7.0 and second["gain"] == 14.0
    assert SM.stats() == {"hits": 0, "runs": 2}


def test_unsupported_nested_object_array_fails_open_without_cache_hit():
    V = _engine()
    calls = {"n": 0}

    def simulate_one(npz, sym, is_long, cfg, force_initial_seed=False):
        calls["n"] += 1
        value = repr(npz["opaque"][0])
        return {"gain": value, "ledger": [{"opaque": value}]}

    V.simulate_one = simulate_one
    assert SM.install(V)
    first = V.simulate_one({"close": [1.0, 2.0], "opaque": np.array([object()], dtype=object)}, "X", True, Cfg())
    second = V.simulate_one({"close": [1.0, 2.0], "opaque": np.array([object()], dtype=object)}, "X", True, Cfg())
    assert calls["n"] == 2 and first != second
    assert SM.stats() == {"hits": 0, "runs": 0}


def test_fx_probe_sink_dict_bypasses_memo_and_keeps_mutations():
    V = _engine()
    calls = {"n": 0}

    def simulate_one(npz, sym, is_long, cfg, force_initial_seed=False):
        calls["n"] += 1
        sink = object.__getattribute__(cfg, "_FX_PROBE_SINK")
        sink["probe"] = np.array([calls["n"]])
        return None

    V.simulate_one = simulate_one
    assert SM.install(V)
    cfg = Cfg()
    cfg._FX_PROBE_SINK = {}
    assert V.simulate_one({"close": [1.0, 2.0]}, "X", True, cfg) is None
    assert cfg._FX_PROBE_SINK["probe"].tolist() == [1]
    assert V.simulate_one({"close": [1.0, 2.0]}, "X", True, cfg) is None
    assert cfg._FX_PROBE_SINK["probe"].tolist() == [2]
    assert calls["n"] == 2 and SM.stats() == {"hits": 0, "runs": 0}


def test_template_layer_is_applied_before_cfg_proxy():
    V = _engine()

    def ensure_template_layer(cfg, sym, is_long):
        if type(cfg) is Cfg:
            layered = copy.copy(cfg)
            if layered.A == 1:
                layered.A = 42
            return layered
        return cfg

    def simulate_one(npz, sym, is_long, cfg, force_initial_seed=False):
        cfg = V.ensure_template_layer(cfg, sym, is_long)
        cfg.SIDE_INFO = {"effective_a": cfg.A}
        return {"gain": cfg.A, "ledger": [{"a": cfg.A}]}

    V.ensure_template_layer = ensure_template_layer
    V.simulate_one = simulate_one
    assert SM.install(V)
    original = Cfg()
    result = V.simulate_one({"close": [1.0, 2.0]}, "X", True, original)
    assert result["gain"] == 42
    assert original.A == 1 and not hasattr(original, "SIDE_INFO")
    replay = V.simulate_one({"close": [1.0, 2.0]}, "X", True, original)
    assert replay == result and replay is not result
    assert original.A == 1 and not hasattr(original, "SIDE_INFO")
    assert SM.stats() == {"hits": 1, "runs": 1}


def test_disabled_without_env(monkeypatch):
    monkeypatch.setenv("V12_SIM_MEMO", "0")
    V = _engine()
    assert SM.install(V) is False
    V.simulate_one({"close": [1.0]}, "X", True, Cfg())
    V.simulate_one({"close": [1.0]}, "X", True, Cfg())
    assert V.calls == 2


@pytest.mark.parametrize("memo", ["0", "2"])
def test_pilot_starts_with_and_without_memo(memo):
    # 2026-10-28 regression: an early os.environ read in _main_single hit "local variable 'os' referenced before assignment"
    # (os is imported later inside that function) and killed EVERY board in 5 s, memo on or off.
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, V12_SIM_MEMO=memo)
    r = subprocess.run([sys.executable, "v15_pilot.py", "--help"], cwd=str(root), env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-800:]
