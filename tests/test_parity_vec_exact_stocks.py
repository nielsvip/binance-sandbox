"""PARITY LOOP STOCKS 2026-10-06 — twin tests for tradier_vec_exact (PARITY_VEC_EXACT_MODE)."""
import dataclasses
import json
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tradier_vec_exact as T  # noqa: E402


def _rows(n, start=1_787_544_000.0, step=900.0):
    out = []
    for k in range(n):
        ts = start + k * step
        out.append({"ts": ts, "timestamp": "x", "timestamp_15m": ts, "close": 100.0 + k, "wt1_15m": float(k % 7), "ha_15m": ["red", "neutral", "green"][k % 3],
                    "current_price": 1.0, "0market_sentiment_local": 5.0, "age_15m": 0.0, "_tick_ts": ts, "note_4h": "LH" if k % 2 else ""})
    return out


def test_normalize_row_inverts_bridge_format():
    r = T.normalize_row({"ts": 1.0, "timestamp_1h": "2026-08-24T13:00:00.000Z", "ha_4h": "green", "flag": True, "current_price": 3.0, "_x": 1, "0s": 2, "s": "HL"})
    assert r["timestamp_1h"] == 1787576400.0
    assert r["ha_4h"] == 1.0 and r["flag"] == 1.0 and r["s"] == "HL"
    assert "current_price" not in r and "_x" not in r and "0s" not in r and "ts" not in r


def test_labels_back_to_npz_codes():
    r = T.normalize_row({"wt_cross_15m": "BULL", "wt_divergence_4h": "", "wt_momentum_state_1h": "EXHAUST_UP", "wt_peak_structure_D": "LH", "wt_composite_bias": "SHORT", "other": "LH"})
    assert r == {"wt_cross_15m": 1.0, "wt_divergence_4h": 0.0, "wt_momentum_state_1h": 2.0, "wt_peak_structure_D": -1.0, "wt_composite_bias": -1.0, "other": "LH"}


def test_buffer_dedupes_bars_and_builds_arrays():
    b = T.SymbolBuffer()
    rows = _rows(5)
    for r in rows:
        assert b.ingest(r)
        assert not b.ingest(r)
    npz = b.npz()
    assert list(npz["timestamps"]) == [r["ts"] for r in rows]
    assert list(npz["close"]) == [100.0, 101.0, 102.0, 103.0, 104.0]
    assert npz["note_4h"].dtype.kind == "U"


def test_compact_matches_lifecycle_pilot_for_study_sizes():
    from tools.opt.lifecycle_pilot import compact_to_completed_timeframe
    n = 400
    base = 1_787_544_000.0 + np.arange(n) * 300.0
    parent = base - (base % 900.0)
    parent[::37] = 0.0
    npz = {"timestamps": base, "timestamp_15m": parent, "close": np.arange(n, dtype=float), "s": np.asarray(["a"] * n), "scalar": 3}
    ref, _ = compact_to_completed_timeframe(dict(npz))
    got = T.compact_completed(dict(npz))
    assert set(ref) == set(got)
    for k in ref:
        assert np.array_equal(np.asarray(ref[k]), np.asarray(got[k])), k


def test_live_action_mapping():
    assert T.live_action_for({"type": "OPEN", "qty": 5}, 0) == ("OPEN", 5.0)
    assert T.live_action_for({"type": "OPEN", "qty": 5}, 3) is None
    assert T.live_action_for({"type": "CLOSE", "qty": 5}, 3) == ("CLOSE", 3.0)
    assert T.live_action_for({"type": "CLOSE", "qty": 5}, 0) is None
    assert T.live_action_for({"type": "REDUCE", "qty": 5}, 3) == ("REDUCE", 3.0)
    assert T.live_action_for({"type": "AUGMENT", "qty": 2}, 3) == ("AUGMENT", 2.0)
    assert T.vec_reason({"type": "OPEN", "reason": "B12"}) == "VEC_EXACT_OPEN_B12"


def test_build_cfg_equals_sheet_cfg():
    """config half must equal tools.opt.evaluate_v12.build_cfg_npz + lifecycle_pilot guard (needs a stock NPZ: server only)."""
    from tools.opt import evaluate_v12 as E
    from min_decision_tf_guard import clamp_config
    ov_p = ROOT / "data" / "parity" / "overrides" / "AAPL_LONG.json"
    if not ov_p.exists():
        pytest.skip("frozen set missing")
    ov = json.loads(ov_p.read_text())["cumulative_overrides"]
    try:
        cfg_ref, npz, *_ = E.build_cfg_npz("AAPL_LONG", dict(ov), window_days=60)
    except Exception as e:
        pytest.skip(f"no NPZ here: {e}")
    if npz is None:
        pytest.skip("no NPZ here")
    clamp_config(cfg_ref, "15m")
    cfg_ref.BASE_TF = "15m"
    cfg = T.build_cfg("AAPL", "LONG", dict(ov))
    diffs = [f.name for f in dataclasses.fields(cfg_ref) if repr(getattr(cfg_ref, f.name)) != repr(getattr(cfg, f.name))]
    assert diffs == [], diffs[:20]


def test_decide_dispatches_once_per_bar(monkeypatch):
    import v12_quick_engine as V
    o = T.VecExactOracle()
    calls = []

    def fake_sim(npz, sym, is_long, cfg):
        calls.append(len(npz["timestamps"]))
        t = float(npz["timestamps"][-1])
        return {"ledger": [{"type": "OPEN", "ts": t, "qty": 2, "price": 1.0, "reason": "B12"}, {"type": "CLOSE", "ts": t, "reason": "FINAL_MTM", "qty": 2}]}
    monkeypatch.setattr(V, "simulate_one", fake_sim)
    monkeypatch.setattr(T, "build_cfg", lambda s, sd, ov: __import__("types").SimpleNamespace())
    for r in _rows(3):
        o.ingest("AAPL", r)
    ev = o.decide("AAPL", "LONG", {})
    assert [e["type"] for e in ev] == ["OPEN"]
    assert o.decide("AAPL", "LONG", {}) == []
    assert calls == [3]


def test_route_contract_vec_exact_reason():
    from tradier_route_contract import is_vec_exact_reason
    assert is_vec_exact_reason("VEC_EXACT_OPEN_B12")
    assert is_vec_exact_reason(" vec_exact_close_x")
    assert not is_vec_exact_reason("WT_DC_ENTRY_58")
    assert not is_vec_exact_reason(None)


def test_tradier_manage_hooks_default_off_and_no_gates():
    """source pins: master default False; exact orders bypass queue/execute_trade_action/execute_now decision gates; NOLOSS exception."""
    import config_tradier
    assert config_tradier.TradierConfig.PARITY_VEC_EXACT_MODE is False
    assert config_tradier.TradierConfig.PARITY_VEC_EXACT_SET_DIR == ""
    src = (ROOT / "tradier_manage.py").read_text()
    assert "if bool(_cfg('PARITY_VEC_EXACT_MODE', False, account_key, symbol, position_side)):\n            return await _vec_exact_process(" in src
    assert "return await _queue_vec_exact_order(order_queue, trade_manager, position_key, action, reason, conviction, override_qty, record_decision)" in src
    assert "_vx_ex = is_vec_exact_reason(reason) and bool(_cfg('PARITY_VEC_EXACT_MODE', False, account_key, symbol, position_side))" in src
    assert "elif _vx_ex:\n                    pass  # PARITY LOOP STOCKS 2026-10-06: NOLOSS exception" in src
    assert src.count("and not _vx_ex") + src.count("not _vx_ex and") >= 16


def test_queue_vec_exact_order_builds_vec_qty_order(monkeypatch):
    import asyncio
    import tradier_manage as TM

    class Pos:
        def __init__(self, amt):
            self.positionAmt = amt

    class PM:
        def __init__(self, book):
            self.book = book
            self.positions = book

        def get_position(self, k):
            return self.book.get(k)

    class Q:
        def __init__(self):
            self.orders = []

        async def add_order(self, o):
            self.orders.append(o)
            return True, "SUCCESS"

    class TMgr:
        def __init__(self, book):
            self.position_manager = PM(book)

        def get_indicators(self, s):
            return {}

        async def get_current_price(self, s):
            return 100.0, 0

    async def no_rec(*a, **k):
        return None
    monkeypatch.setattr(TM, "_record_submit_decision", no_rec)
    q = Q()
    r = asyncio.run(TM._queue_vec_exact_order(q, TMgr({}), "trb:AAPL_LONG", "OPEN", "VEC_EXACT_OPEN_B12", 100.0, 7.0))
    _tm0 = TMgr({})
    _exp = TM._qta_entry_sizing(_tm0, "trb:AAPL_LONG", "trb", "AAPL", "LONG", "OPEN", "VEC_EXACT_OPEN_B12", 7.0, 100.0, *TM._qta_ls_ratio_scale(_tm0, "trb:AAPL_LONG", "LONG", False), False)  # USER 2026-10-06: live sizes the vec decision
    assert r == "SUCCESS" and q.orders[0]["quantity"] == _exp and q.orders[0]["side"] == "BUY" and q.orders[0]["reason"] == "VEC_EXACT_OPEN_B12"
    q = Q()
    r = asyncio.run(TM._queue_vec_exact_order(q, TMgr({"trb:AAPL_LONG": Pos(5)}), "trb:AAPL_LONG", "CLOSE", "VEC_EXACT_CLOSE_DAYTRADE_TARGET", 100.0, 999.0))
    assert r == "SUCCESS" and q.orders[0]["quantity"] == 5.0 and q.orders[0]["side"] == "SELL"
    assert asyncio.run(TM._queue_vec_exact_order(Q(), TMgr({"trb:AAPL_LONG": Pos(5)}), "trb:AAPL_LONG", "OPEN", "VEC_EXACT_OPEN_B12", 100.0, 3.0)) == "VEC_EXACT_ALREADY_OPEN"
    assert asyncio.run(TM._queue_vec_exact_order(Q(), TMgr({}), "trb:AAPL_LONG", "CLOSE", "VEC_EXACT_CLOSE_X", 100.0, 3.0)) == "VEC_EXACT_NO_POSITION"
    assert asyncio.run(TM._queue_vec_exact_order(Q(), TMgr({"trb:AAPL_SHORT": Pos(2)}), "trb:AAPL_LONG", "OPEN", "VEC_EXACT_OPEN_B12", 100.0, 3.0)) == "VEC_EXACT_OPPOSING_POSITION"


def test_vec_exact_gets_same_live_sizing_as_native():
    """USER 2026-10-06 (binding): parity = decisions; live sizes. A VEC_EXACT OPEN gets exactly the live sizing a native OPEN with the same
    decision gets (queue: _qta_ls_ratio_scale + _qta_entry_sizing; execute_now: BREAKOUT_TF_SIZE / L/S RATIO_BOOST/CUT / MTF_SIZE_MULT)."""
    import asyncio
    import pathlib
    import types
    import config_tradier
    import tradier_manage as TM
    cfg = getattr(config_tradier, "TradierConfig", config_tradier)
    assert getattr(cfg, "TRADIER_LS_RATIO_SIZING_ENABLED") is True
    import v12_quick_engine as V
    assert V.QuickConfig().TRADIER_LS_RATIO_SIZING_ENABLED is True
    src = (pathlib.Path(TM.__file__)).read_text()
    assert "and not _is_ladder_parity and bool(_cfg('TRADIER_LS_RATIO_SIZING_ENABLED', True, account_key, symbol, position_side)):" in src
    i = src.index("_tf_mult, _tf_label = _breakout_tf_size_mult_tradier(reason)")
    assert "_vx_ex" not in src[i - 300:i]
    assert "VEC_EXACT skips the MTF decision gate but keeps the live MTF size multiplier" in src
    captured = {}
    class _Q:
        async def add_order(self, order):
            captured.update(order)
            return True, "ok"
    class _PM:
        positions = {}
        def get_position(self, k):
            return None
    ind = {"ema_200_15m": 100.0}
    tm = types.SimpleNamespace(position_manager=_PM(), get_indicators=lambda s: ind)
    async def _px(symbol):
        return (103.0, None)
    tm.get_current_price = _px
    pk = "trb:AAPL_LONG"
    res = asyncio.run(TM._queue_vec_exact_order(_Q(), tm, pk, "OPEN", "VEC_EXACT_OPEN_TEST", 50.0, 21.0, record_decision=False))
    assert res == "SUCCESS", res
    sc, sr = TM._qta_ls_ratio_scale(tm, pk, "LONG", False)
    native = TM._qta_entry_sizing(tm, pk, "trb", "AAPL", "LONG", "OPEN", "VEC_EXACT_OPEN_TEST", 21.0, 103.0, sc, sr, False)
    assert captured["quantity"] == native
    if bool(getattr(cfg, "BREAKOUT_SIZE_LADDER_ENABLED", True)):
        assert captured["quantity"] != 21.0  # 3% past ema_200_15m -> BREAKOUT_SIZE_LADDER amplifies like a native order
