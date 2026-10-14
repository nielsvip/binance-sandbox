"""2026-10-14 twin<->live drift fix (live_twins/vec_exact.py + tools/vx_tracked_exec.py).

1. Frozen append-only twin store: past bars never revised by a rebuild; restart-proof; shared; re-anchor bounded.
2. Set pinned per held position: exits keep the held set across a go-live change; entries/flat use the latest set; AUGMENT gate.
3. Set switch drops cached decisions; [VEC_EXACT_REWRITE] fires when a decided bar's paper state changes.
4. Timeout MARKET fallback carries the execute_now token (was ORDER_DEDUPE_BLOCK:NOT_VIA_EXECUTE_NOW).
"""
import asyncio
import json
import logging
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_twins import vec_exact as vx  # noqa: E402


def _raw(t0, n, base=1.0, extra=None):
    ts = np.arange(n, dtype=np.float64) * 900.0 + t0
    d = {"timestamps": ts, "close": np.full(n, base) + np.arange(n) * 0.01, "wt1_4h": np.full(n, base * 10.0), "htf_align": np.array(["causal_v3"])}
    d.update(extra or {})
    return d


@pytest.fixture
def frozen_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(vx, "_FROZEN_DIR", str(tmp_path / "frozen"))
    monkeypatch.setattr(vx, "_FROZEN", {})
    monkeypatch.setattr(vx, "_knob", lambda name, default: default)
    return tmp_path / "frozen"


def test_freeze_keeps_past_bars_and_appends_new(frozen_dir):
    t0 = 1_790_000_000.0
    a = _raw(t0, 200)
    fa = vx._freeze("TESTUSDT", a)
    assert np.array_equal(fa["close"], a["close"])
    b = _raw(t0 + 900.0, 200, base=2.0)  # rebuild: start moved 1 bar, every past value revised, 1 new bar
    fb = vx._freeze("TESTUSDT", b)
    assert len(fb["timestamps"]) == 201
    assert fb["timestamps"][0] == t0  # anchor fixed
    assert np.array_equal(fb["close"][:200], a["close"])  # past bars frozen at first sight
    assert fb["close"][200] == b["close"][-1]  # only the new bar comes from the rebuild
    assert np.array_equal(fb["htf_align"], a["htf_align"])


def test_freeze_survives_restart_and_is_shared(frozen_dir):
    t0 = 1_790_000_000.0
    vx._freeze("TESTUSDT", _raw(t0, 150))
    vx._FROZEN.clear()  # new process (restart / another account process)
    out = vx._freeze("TESTUSDT", _raw(t0 + 9000.0, 150, base=5.0))
    assert out["timestamps"][0] == t0 and len(out["timestamps"]) == 160
    assert out["close"][0] == 1.0


def test_freeze_same_bar_from_two_processes_identical(frozen_dir):
    t0 = 1_790_000_000.0
    vx._freeze("TESTUSDT", _raw(t0, 120))
    p1 = vx._freeze("TESTUSDT", _raw(t0, 121, base=3.0))
    vx._FROZEN.clear()
    p2 = vx._freeze("TESTUSDT", _raw(t0, 121, base=7.0))  # second process, different build of the same new bar
    assert np.array_equal(p1["close"], p2["close"])


def test_freeze_resets_on_keyset_change_and_no_overlap(frozen_dir, caplog):
    t0 = 1_790_000_000.0
    vx._freeze("TESTUSDT", _raw(t0, 120))
    with caplog.at_level(logging.WARNING):
        out = vx._freeze("TESTUSDT", _raw(t0, 121, base=3.0, extra={"new_col": np.zeros(121)}))
    assert "KEYSET_CHANGED" in caplog.text and out["close"][0] == 3.0
    out2 = vx._freeze("TESTUSDT", _raw(t0 + 10 * 86400.0, 50, base=4.0, extra={"new_col": np.zeros(50)}))
    assert out2["timestamps"][0] == t0 + 10 * 86400.0


def test_freeze_reanchor_bounded(frozen_dir, caplog):
    t0 = 1_790_000_000.0
    vx._freeze("TESTUSDT", _raw(t0, 96 * 28))
    with caplog.at_level(logging.WARNING):
        out = vx._freeze("TESTUSDT", _raw(t0 + 96 * 2 * 900.0, 96 * 28))  # span becomes 30d > 29d
    sec = out["timestamps"]
    assert (sec[-1] - sec[0]) <= 21 * 86400.0 + 1
    assert "RE-ANCHOR" in caplog.text


def test_freeze_disabled_is_passthrough(frozen_dir, monkeypatch):
    monkeypatch.setattr(vx, "_knob", lambda name, default: False if name == "VEC_EXACT_FROZEN_STORE_ENABLED" else default)
    b = _raw(1_790_000_000.0, 100)
    assert vx._freeze("TESTUSDT", b) is b


@pytest.fixture
def pins(tmp_path, monkeypatch):
    monkeypatch.setattr(vx, "_base_path", lambda: tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(vx, "_PIN", {})
    monkeypatch.setattr(vx, "_OV_PIN", {})
    monkeypatch.setattr(vx, "_SET_MD5", {})
    monkeypatch.setattr(vx, "_knob", lambda name, default: 0.0 if name == "VEC_EXACT_OWNED_DROP_GRACE_S" else default)
    cur = {"v": {"A": 1}}
    monkeypatch.setattr(vx, "overrides_for", lambda s, sd: dict(cur["v"]))
    return cur


def test_pin_keeps_held_set_across_golive(pins, tmp_path):
    pk = "ang:TESTUSDT_LONG"
    vx.pin_activate(pk)
    pins["v"] = {"A": 2}  # go-live
    assert vx.effective_overrides("TESTUSDT", "LONG") == {"A": 1}
    ok, why = vx.pin_is_latest("TESTUSDT", "LONG")
    assert not ok and why.startswith("PINNED_")
    vx._PIN.clear(); vx._OV_PIN.clear()  # restart
    vx.pin_activate(pk)
    assert vx.effective_overrides("TESTUSDT", "LONG") == {"A": 1}
    assert json.loads((tmp_path / "data" / "vec_exact_pinned_ang.json").read_text())[pk]["ov"] == {"A": 1}
    vx.pin_release(pk)
    assert vx.effective_overrides("TESTUSDT", "LONG") == {"A": 2}
    assert vx.pin_is_latest("TESTUSDT", "LONG")[0]


def test_pin_release_grace_keeps_fresh_pin(pins, monkeypatch):
    monkeypatch.setattr(vx, "_knob", lambda name, default: 900.0 if name == "VEC_EXACT_OWNED_DROP_GRACE_S" else default)
    pk = "ang:TESTUSDT_LONG"
    vx.pin_activate(pk)
    vx.pin_release(pk)
    assert pk in vx._PIN["ang"]  # pending maker fill window: persisted pin kept


def test_pin_disabled_uses_latest(pins, monkeypatch):
    monkeypatch.setattr(vx, "_knob", lambda name, default: False if name == "VEC_EXACT_PIN_SET_PER_POSITION" else default)
    vx.pin_activate("ang:TESTUSDT_LONG")
    pins["v"] = {"A": 9}
    assert vx.effective_overrides("TESTUSDT", "LONG") == {"A": 9}


def _fake_prep(n):
    ts = np.arange(n, dtype=np.float64) * 900.0 + 1_790_000_000.0
    return {"prep": {"npz_prepared": {"timestamps": ts, "close": np.ones(n)}}, "mtime": 1, "anchor": ts[0], "src": "npz"}, ts


def test_actions_at_uses_pinned_set_and_drops_cache_on_switch(pins, monkeypatch):
    from tools.opt import evaluate_v12 as E
    st, ts = _fake_prep(150)
    monkeypatch.setattr(vx, "_prepared", lambda ss: st)
    monkeypatch.setattr(vx, "_DECIDED", {})
    monkeypatch.setattr(vx, "_VHOLD", {})
    seen = []

    def fake_eval(q, ov, include_ledger=True):
        seen.append(dict(ov))
        return {"execution_ledger": [{"ts": float(ts[110]), "type": "OPEN", "qty": 1.0, "reason": "B12"}]}

    monkeypatch.setattr(E, "evaluate_prepared", fake_eval)
    monkeypatch.setattr(vx, "bar_hard_stop_action", lambda *a, **k: None)
    pk = "ang:TESTUSDT_LONG"
    vx.pin_activate(pk)
    r = vx._actions_at("TESTUSDT", "LONG", float(ts[120]) + 1)
    assert seen[-1] == {"A": 1} and r["vec_holds"] is True
    pins["v"] = {"A": 2}
    vx._actions_at("TESTUSDT", "LONG", float(ts[120]) + 1)
    assert len(seen) == 1  # held position: still the pinned set -> cached decision reused
    vx.pin_release(pk)
    vx._actions_at("TESTUSDT", "LONG", float(ts[120]) + 1)
    assert len(seen) == 2 and seen[-1] == {"A": 2}  # flat: latest set, cache for the old set dropped


def test_rewrite_detector_logs(pins, monkeypatch, caplog):
    from tools.opt import evaluate_v12 as E
    st, ts = _fake_prep(150)
    monkeypatch.setattr(vx, "_prepared", lambda ss: st)
    monkeypatch.setattr(vx, "_DECIDED", {})
    monkeypatch.setattr(vx, "_VHOLD", {})
    monkeypatch.setattr(vx, "bar_hard_stop_action", lambda *a, **k: None)
    led = {"v": [{"ts": float(ts[110]), "type": "OPEN", "qty": 1.0, "reason": "B12"}]}
    monkeypatch.setattr(E, "evaluate_prepared", lambda q, ov, include_ledger=True: {"execution_ledger": led["v"]})
    vx._actions_at("TESTUSDT", "LONG", float(ts[120]) + 1)  # bar 120: holds
    led["v"] = []  # store revision: the open never happened
    with caplog.at_level(logging.WARNING):
        r = vx._actions_at("TESTUSDT", "LONG", float(ts[121]) + 1)
    assert r["vec_holds"] is False
    assert "[VEC_EXACT_REWRITE] TESTUSDT_LONG" in caplog.text and "HOLD -> FLAT" in caplog.text


def test_timeout_market_carries_execute_now_token(monkeypatch):
    import importlib.util
    import order_dedupe_guard as odg
    spec = importlib.util.spec_from_file_location("vx_tracked_exec_t", ROOT / "tools" / "vx_tracked_exec.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    seen = {}

    class Client:
        def futures_get_open_orders(self, symbol):
            return []

        def futures_create_order(self, **kw):
            tok = odg._ORDER_AUTH.get()
            seen.update(tok=tok, kw=kw)
            return {"orderId": 1, "status": "FILLED"}

    class Pos:
        positionAmt = 12.34

    class TM:
        positions = {"ang:TESTUSDT_LONG": Pos()}

        def get_client(self, acct):
            return Client()

        def get_symbol_config(self, s):
            return {"step_size": 0.1}

    res = asyncio.run(m.fire_exit_market(TM(), "ang:TESTUSDT_LONG", "TESTUSDT", "SELL", "LONG"))
    assert res.startswith("TIMEOUT_EXIT_MARKET")
    assert seen["tok"] is not None and seen["tok"]["symbol"] == "TESTUSDT" and seen["tok"]["action"] == "CLOSE" and seen["tok"]["account"] == "ang"
    assert seen["kw"]["type"] == "MARKET" and abs(float(seen["kw"]["quantity"]) - 12.3) < 1e-9
    assert odg._ORDER_AUTH.get() is None  # token never leaks past the call
