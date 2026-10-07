"""Parity lane B 2026-10-06:
- flat openers WT_15M_BOUNCE_OPEN_ENABLED / BB_BOUNCE_ENTRY_TF: ez_manage._parity_flat_open_check calls the shared
  vec_decisions/twin_entry_ports_b predicates (vec-formula agreement proven in test_entry_ports_b_twin.py) and queues an
  OPEN through queue_trade_action -> execute_now; inert when both switches are off; BB_BOUNCE generic entry AND-mask
  (live_twins.parity_open_gates.bb_bounce_blocks_live) == vec generic_filter_tf 'bb_bounce'.
- MULTI_TF_EXIT: live runs evaluate_multi_tf_exit only when MULTI_TF_EXIT_ENABLED; WT_DIV / WT_15M_LH_WAIT component
  scores agree with vec_decisions/mtf_exit_scorer on equivalent inputs.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ez_manage
from live_twins import parity_open_gates as G
from vec_decisions import generic_filter_tf as GFT
from vec_decisions import mtf_exit_scorer as MTS


def _safe(npz, key, n, default=0.0):
    v = npz.get(key)
    if v is None or len(v) != n:
        return np.full(n, default, dtype=float)
    return np.asarray(v, dtype=float)


def _run_flat(monkeypatch, switches, ind):
    queued = []
    monkeypatch.setattr(ez_manage, "_psym_cs_get", lambda s, sd, k, d: switches.get(k, d))

    async def fake_ii(tm, sym):
        return ind

    async def fake_price(sym, pos=None, **kw):
        return 1.0

    async def fake_q(oq, tm, pk, action, reason, conf):
        queued.append((pk, action, reason))
        return "OK"

    monkeypatch.setattr(ez_manage, "ii", fake_ii)
    monkeypatch.setattr(ez_manage, "price", fake_price)
    monkeypatch.setattr(ez_manage, "queue_trade_action", fake_q)
    monkeypatch.setattr(ez_manage, "_recent_opens", {})
    asyncio.run(ez_manage._parity_flat_open_check(SimpleNamespace(), "acc:AAAUSDT_LONG", None, object()))
    return queued


def test_flat_open_inert_by_default(monkeypatch):
    ind = {"bb_pct_b_15m_prev": 0.1, "bb_pct_b_15m": 0.3, "bb_lower_15m": 0.9}
    assert _run_flat(monkeypatch, {}, ind) == []
    assert ez_manage._parity_flat_open_enabled("acc:AAAUSDT_LONG") is False


def test_bb_bounce_opener_fires(monkeypatch):
    ind = {"bb_pct_b_15m_prev": 0.1, "bb_pct_b_15m": 0.3, "bb_lower_15m": 0.9}
    q = _run_flat(monkeypatch, {"BB_BOUNCE_ENTRY_TF": "15m"}, ind)
    assert len(q) == 1 and q[0][1] == "OPEN" and q[0][2].startswith("BB_BOUNCE_ENTRY_15m_L")
    assert _run_flat(monkeypatch, {"BB_BOUNCE_ENTRY_TF": "15m"}, {"bb_pct_b_15m_prev": 0.3, "bb_pct_b_15m": 0.3, "bb_lower_15m": 0.9}) == []


def test_wt15_bounce_opener_fires(monkeypatch):
    ind = {"wt1_15m": 5.0, "wt2_15m": 1.0, "wt1_15m_prev": 0.0, "wt2_15m_prev": 1.0, "bb_pct_b_15m": 0.5, "wt1_1h": 3.0, "wt2_1h": 1.0, "wt1_4h": -1.0, "wt2_4h": 1.0}
    q = _run_flat(monkeypatch, {"WT_15M_BOUNCE_OPEN_ENABLED": True}, ind)
    assert len(q) == 1 and q[0][2].startswith("WT_15M_BOUNCE_L")
    ind_nocross = dict(ind, wt1_15m_prev=2.0)
    assert _run_flat(monkeypatch, {"WT_15M_BOUNCE_OPEN_ENABLED": True}, ind_nocross) == []


@pytest.mark.parametrize("is_long", [True, False])
def test_bb_bounce_gate_matches_vec(is_long):
    close = np.array([10.2, 10.2, 9.8, 9.8, 10.2, 9.8])
    npz = {"bb_lower_1h": np.array([10.0, 10.0, 10.0, 0.0, 10.0, 10.0]), "bb_upper_1h": np.array([10.0, 10.0, 10.0, 0.0, 10.0, 10.0]),
           "low_1h": np.array([9.9, 10.1, 9.7, 9.0, 0.0, 9.7]), "high_1h": np.array([10.3, 10.1, 10.1, 11.0, 10.3, 9.9])}
    vec = GFT._cond("bb_bounce", npz, len(close), "1h", is_long, close, _safe)
    get = lambda k, d=None: "1h" if k == "BB_BOUNCE_ENTRY_TF" else d
    live = [not G.entry_filter_tf_veto({k: float(v[i]) for k, v in npz.items()}, is_long, float(close[i]), get)[0] for i in range(len(close))]
    assert live == [bool(x) for x in vec]
    assert any(live) and not all(live)


def _live_score(ind, is_long, cfg, monkeypatch):
    for k, v in cfg.items():
        monkeypatch.setattr(ez_manage.config, k, v, raising=False)
    return ez_manage.evaluate_multi_tf_exit(ind, is_long, 0.0, 0.0, 1.0)[2]


@pytest.mark.parametrize("is_long", [True, False])
def test_wt_div_component_matches_vec(monkeypatch, is_long):
    neutral = {"dc_position_15m": 0.5, "dc_position_1h": 0.5, "dc_position_4h": 0.5}
    live_ind = dict(neutral, wt1_1h=10.0 if is_long else -10.0, wt_peak_value_1h=50.0, wt_trough_value_1h=-50.0, close_1h=1.0, close_1h_prev=0.99 if is_long else 1.01)
    npz = {k: np.array([v]) for k, v in neutral.items()}
    npz.update({"wt1_1h": np.array([live_ind["wt1_1h"]]), "wt_peak_1h": np.array([50.0]), "wt_trough_1h": np.array([-50.0]), "close_1h": np.array([1.0]), "close_1h_prev": np.array([live_ind["close_1h_prev"]])})
    off = dict(WT_DIV_EXIT_ENABLED=False, WT_ACCEL_EXIT_ENABLED=False, WT_15M_LH_WAIT_EXIT_ENABLED=False, TRADIER_WT_EXIT_MIN_TFS_TRADIER=0)
    on = dict(off, WT_DIV_EXIT_ENABLED=True)
    d_live = _live_score(live_ind, is_long, on, monkeypatch) - _live_score(live_ind, is_long, off, monkeypatch)
    b_on, l_on, _, _ = MTS.score_array_parts(npz, 1, is_long, SimpleNamespace(**on), _safe, np.array([1.0]))
    b_off, l_off, _, _ = MTS.score_array_parts(npz, 1, is_long, SimpleNamespace(**off), _safe, np.array([1.0]))
    d_vec = float((b_on + l_on)[0] - (b_off + l_off)[0])
    assert d_live == pytest.approx(d_vec) and d_vec == 6.0


def test_lh_wait_component_matches_vec(monkeypatch):
    neutral = {"dc_position_15m": 0.7, "dc_position_1h": 0.5, "dc_position_4h": 0.5}
    live_ind = dict(neutral, wt_peak_structure_15m="LH")
    npz = {k: np.array([v]) for k, v in neutral.items()}
    npz["wt_peak_structure_15m"] = np.array([-1.0])
    off = dict(WT_DIV_EXIT_ENABLED=False, WT_ACCEL_EXIT_ENABLED=False, WT_15M_LH_WAIT_EXIT_ENABLED=False, TRADIER_WT_EXIT_MIN_TFS_TRADIER=0)
    on = dict(off, WT_15M_LH_WAIT_EXIT_ENABLED=True)
    d_live = _live_score(live_ind, True, on, monkeypatch) - _live_score(live_ind, True, off, monkeypatch)
    b_on, l_on, _, _ = MTS.score_array_parts(npz, 1, True, SimpleNamespace(**on), _safe, np.array([1.0]))
    b_off, l_off, _, _ = MTS.score_array_parts(npz, 1, True, SimpleNamespace(**off), _safe, np.array([1.0]))
    assert d_live == pytest.approx(float((b_on + l_on)[0] - (b_off + l_off)[0])) == 18.0


def test_multi_tf_exit_master_default_off():
    import config as C
    assert C.Config().MULTI_TF_EXIT_ENABLED is False
    src = (Path(__file__).resolve().parents[1] / "ez_manage.py").read_text()
    assert "_mtfx_fire, _mtfx_reason, _mtfx_score = evaluate_multi_tf_exit(" in src
