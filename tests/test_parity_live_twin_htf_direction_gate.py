"""Parity lane B 2026-10-06: live strict open veto (HTF_DIRECTION_GATE_ENABLED + nested OI_CONFIRM_*;
live_twins.parity_open_gates.strict_open_veto, called from ez_manage.execute_now for every non-augment open)
== vec wave4_families.htf_direction_gate / oi_confirm_entry_gate (v12 _strict_open_block), inert at default.
"""
import itertools
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as config_mod
from live_twins import parity_open_gates as G
from vec_decisions import wave4_families as W


def _safe(npz, key, n, default=0.0):
    v = npz.get(key)
    if v is None or len(v) != n:
        return np.full(n, default, dtype=float)
    return np.asarray(v, dtype=float)


def _grid():
    vals = (-20.0, 0.0, 20.0)
    rows = []
    for d1, d2, h1, h2, o1, o2 in itertools.product(vals, (-10.0, 10.0), vals, (-10.0, 10.0), vals, (-10.0, 10.0)):
        rows.append((d1, d2, h1, h2, o1, o2))
    n = len(rows)
    a = np.array(rows)
    close = np.linspace(90.0, 110.0, n)
    npz = {"wt1_D": a[:, 0], "wt2_D": a[:, 1], "wt1_4h": a[:, 2], "wt2_4h": a[:, 3], "wt1_1h": a[:, 4], "wt2_1h": a[:, 5], "sma_200_D": np.full(n, 100.0)}
    return npz, close, n


def _vec_block(npz, n, is_long, cfg, close):
    blk = np.zeros(n, dtype=bool)
    for fn in (W.htf_direction_gate, W.oi_confirm_entry_gate):
        m = fn(npz, n, is_long, cfg, close, _safe)
        if m is not None:
            blk |= ~np.asarray(m, dtype=bool)
    return blk


@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("min_conf,dmand,sma", [(2, False, False), (3, True, True), (1, True, False), (4, False, True)])
def test_htf_matches_vec(is_long, min_conf, dmand, sma):
    npz, close, n = _grid()
    cfg = SimpleNamespace(HTF_DIRECTION_GATE_ENABLED=True, HTF_GATE_APPLY_TO_OPEN=True, HTF_GATE_MIN_CONFIRMATIONS=min_conf, HTF_GATE_D_MANDATORY=dmand, HTF_GATE_SIGNALS_SMA200D=sma, OI_CONFIRM_ENABLED=False)
    vec = _vec_block(npz, n, is_long, cfg, close)
    get = lambda k, d=None: getattr(cfg, k, d)
    live = [G.strict_open_veto({k: float(v[i]) for k, v in npz.items()}, is_long, float(close[i]), get)[0] for i in range(n)]
    assert live == [bool(x) for x in vec]
    assert any(live) and not all(live)


@pytest.mark.parametrize("is_long", [True, False])
def test_oi_nested_matches_vec(is_long):
    pxc = np.array([1.0, 1.0, -1.0, -1.0, 0.1, 1.0, 1.0])
    oi = np.array([1.0, -1.0, 1.0, -1.0, 2.0, 0.2, np.nan])
    p1h = np.full(len(pxc), 100.0)
    close = p1h * (1 + pxc / 100.0)
    n = len(close)
    npz = {"oi_change_1h_pct": oi, "close_1h_prev": p1h}
    cfg = SimpleNamespace(HTF_DIRECTION_GATE_ENABLED=True, HTF_GATE_APPLY_TO_OPEN=False, OI_CONFIRM_ENABLED=True, OI_CONFIRM_MIN_CHANGE_PCT=0.5, OI_CONFIRM_MIN_PRICE_PCT=0.3)
    vec = _vec_block(npz, n, is_long, cfg, close)
    get = lambda k, d=None: getattr(cfg, k, d)
    live = []
    for i in range(n):
        ind = {"close_1h_prev": float(p1h[i])}
        if np.isfinite(oi[i]):
            ind["oi_change_1h_pct"] = float(oi[i])
        live.append(G.strict_open_veto(ind, is_long, float(close[i]), get)[0])
    assert live == [bool(x) for x in vec]
    assert any(live) and not all(live)
    # OI nested under the HTF master (vec + live EPQ): master off -> never blocks
    cfg.HTF_DIRECTION_GATE_ENABLED = False
    assert not any(G.strict_open_veto({"oi_change_1h_pct": float(oi[i]) if np.isfinite(oi[i]) else None, "close_1h_prev": 100.0}, is_long, float(close[i]), get)[0] for i in range(n))
    assert W.oi_confirm_entry_gate(npz, n, is_long, cfg, close, _safe) is None


def test_config_default_inert():
    c = config_mod.Config()
    get = lambda k, d=None: getattr(c, k, d)
    assert c.HTF_DIRECTION_GATE_ENABLED is False
    ind = {"wt1_D": -50.0, "wt2_D": 50.0, "wt1_4h": -50.0, "wt2_4h": 50.0, "wt1_1h": -50.0, "wt2_1h": 50.0, "oi_change_1h_pct": 5.0, "close_1h_prev": 90.0}
    assert G.strict_open_veto(ind, True, 100.0, get) == (False, "")


@pytest.mark.parametrize("reason,bypass", [("RZ_BOUNCE_X", True), ("RED_ZONE_ENTRY", True), ("SCALP_V3_OPEN_1", True), ("RATIO_RECOVERY_OPEN", True), ("WT_DC_ENTRY", False), ("", False)])
def test_bypass_families(reason, bypass):
    assert G.htf_gate_bypassed(reason, lambda k, d=None: d) is bypass
    assert G.htf_gate_bypassed("RZ_BOUNCE", lambda k, d=None: False if k == "HTF_GATE_BYPASS_RZ" else d) is False
