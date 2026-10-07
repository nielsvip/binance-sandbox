"""Parity lane B 2026-10-06 phase 4 (director): live twins of lane-D vec-only switches + sizing hold + EXIT_VELOCITY_WT master.
- CRYPTO_REENTRY_PATHWAYS_ENABLED: live_twins.vec_reentry.fires == the vec pathway predicates v12 uses
  (twin_vec_special.htf_wt_churn_fires, target_dc_reentry_gate.fires, hardcoded rally, reentry_pathways F / blanket).
- KEY_LEVEL_CRASH_EXIT_ENABLED: live_twins.key_level.decide == vec_decisions.live_exit_chain prepare/step/reduce_fraction.
- HAIKU_WINNER_AUGMENT_ENABLED / CRYPTO_REENTRY_PATHWAYS / KEY_LEVEL_CRASH_EXIT: STRICT_VEC_PARITY admits them only while the switch is on.
- defaults inert; PARITY_LIVE_SIZING_MULT_ENABLED=False holds sizing; EXIT_VELOCITY_WT_ENABLED=True keeps today's exit.
"""
import asyncio
import itertools
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as C
import ez_manage
from live_twins import key_level as KL
from live_twins import vec_reentry as VR
from vec_decisions import live_exit_chain as LEC
from vec_decisions import reentry_pathways as RPW
from vec_decisions import target_dc_reentry_gate as TDG
from vec_decisions import twin_vec_special as TVS


def _get(d):
    return lambda k, default=None: d.get(k, default)


BASE = {"CRYPTO_REENTRY_PATHWAYS_ENABLED": True, "HTF_WT_CHURN_REENTRY_ENABLED": False, "TARGET_DC_IMMEDIATE_REENTRY_ENABLED": False,
        "HARDCODED_RALLY_REENTRY_ENABLED": False, "REENTRY_MANDATORY": False, "REENTRY_BLANKET_FIRE_ENABLED": False}


def test_config_defaults():
    c = C.Config()
    assert c.CRYPTO_REENTRY_PATHWAYS_ENABLED is False and c.HAIKU_WINNER_AUGMENT_ENABLED is False
    assert c.KEY_LEVEL_CRASH_EXIT_ENABLED is False
    assert c.MULTI_TF_EXIT_ENABLED is False and c.PARITY_LIVE_SIZING_MULT_ENABLED is False
    assert c.EXIT_VELOCITY_WT_ENABLED is True and c.CANDLE_PATTERN_STOPS_FILTER_TF == "OFF"


def test_vec_reentry_inert_default():
    ind = {"wt1_1h": 10, "wt2_1h": 0}
    assert VR.fires(_get({}), ind, True, 2.0, 1.0, "X", 5.0) == (False, "")


@pytest.mark.parametrize("is_long", [True, False])
def test_htf_wt_churn_matches_vec(is_long):
    cfg = dict(BASE, HTF_WT_CHURN_REENTRY_ENABLED=True, HTF_WT_CHURN_REENTRY_MAX_AGE_MIN=120.0)
    for a, b, c, age in itertools.product((-5.0, 5.0), (-5.0, 5.0), (-5.0, 5.0), (30.0, 200.0)):
        ind = {"wt1_1h": a, "wt2_1h": 0.0, "wt1_15m": b, "wt2_15m": 0.0, "wt1_4h": c, "wt2_4h": 0.0}
        vec = TVS.htf_wt_churn_fires(a, 0.0, b, 0.0, c, 0.0, is_long, age, 120.0, 0.0, 28.0)
        assert VR.fires(_get(cfg), ind, is_long, 1.0, 1.0, "X", age)[0] == vec


@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("reason", ["DAYTRADE_TARGET dc_15m_high -0.10% TARGET_BUF", "SELL_TOP_X", "HLR_TOP_EXIT_Y", "WT_CROSS_EXIT"])
def test_target_selltop_recross_matches_vec(is_long, reason):
    cfg = dict(BASE, TARGET_DC_IMMEDIATE_REENTRY_ENABLED=True)
    for px in (0.9, 1.0, 1.1):
        trades = [{"exit_price": 1.0, "exit_reason": reason}]
        vec_tgt = TDG.fires(SimpleNamespace(TARGET_DC_IMMEDIATE_REENTRY_ENABLED=True), is_long, px, trades)
        vec_st = ("SELL_TOP" in reason or "HLR_TOP_EXIT" in reason) and ((px > 1.0) if is_long else (px < 1.0))
        assert VR.fires(_get(cfg), {}, is_long, px, 1.0, reason, 5.0)[0] == (vec_tgt or vec_st)


@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("req_wt", [False, True])
def test_hardcoded_rally_matches_vec(is_long, req_wt):
    cfg = dict(BASE, HARDCODED_RALLY_REENTRY_ENABLED=True, HARDCODED_RALLY_REENTRY_REQUIRE_WT=req_wt)
    for px, w1, w1p in itertools.product((0.9, 1.1), (1.0, 3.0), (2.0,)):
        vec = ((px > 1.0 and (not req_wt or w1 > w1p)) if is_long else (px < 1.0 and (not req_wt or w1 < w1p)))
        live = VR.fires(_get(cfg), {"wt1_15m": w1, "wt1_15m_prev": w1p}, is_long, px, 1.0, "X", 5.0, rally_ok=lambda *a: True)[0]
        assert live == vec


@pytest.mark.parametrize("is_long", [True, False])
def test_mandatory_pathway_f_and_blanket_match_vec(is_long):
    cfg = dict(BASE, REENTRY_MANDATORY=True, REENTRY_FAVORABLE_HTF_MIN=2, REENTRY_FAVORABLE_MOVE_PCT=1.0)
    for a, b, d, px in itertools.product((-1.0, 1.0), (-1.0, 1.0), (-1.0, 1.0), (0.98, 1.0, 1.02)):
        ind = {"wt1_1h": a, "wt2_1h": 0.0, "wt1_4h": b, "wt2_4h": 0.0, "wt1_D": d, "wt2_D": 0.0}
        npz = {k: np.array([v]) for k, v in ind.items()}
        htf = bool(RPW.favorable_move_mask(npz, 1, is_long, SimpleNamespace(REENTRY_FAVORABLE_HTF_MIN=2), lambda z, k, n, dflt: z.get(k, np.full(n, dflt)))[0])
        vec = htf and RPW.favorable_price_ok(is_long, px, 1.0, 1.0)
        assert VR.fires(_get(cfg), ind, is_long, px, 1.0, "X", 5.0)[0] == vec
    assert VR.fires(_get(dict(cfg, REENTRY_BLANKET_FIRE_ENABLED=None)), {}, is_long, 1.0, 1.0, "X", 5.0) == (True, "MANDATORY_BLANKET")
    assert VR.fires(_get(dict(cfg, REENTRY_BLANKET_FIRE_ENABLED=True, REENTRY_TIER_MODEL_ENABLED=True)), {}, is_long, 1.0, 1.0, "X", 5.0)[0] is False


def _safe(npz, key, n, default=0.0):
    v = npz.get(key)
    if v is None or len(v) != n:
        return np.full(n, default, dtype=float)
    return np.asarray(v, dtype=float)


@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("gain", [-0.5, 0.0, 0.6, 1.5, 4.0])
def test_key_level_matches_live_exit_chain(is_long, gain):
    lv = {"15m": 10.0, "1h": 10.2, "4h": 10.4, "D": 10.6}
    pxs = np.array([10.7, 10.5, 10.3, 10.1, 9.9]) if is_long else np.array([9.9, 10.1, 10.3, 10.5, 10.7])
    n = len(pxs)
    npz = {}
    for t, v in lv.items():
        npz[f"dc_low_{t}_prev" if is_long else f"dc_high_{t}_prev"] = np.full(n, v)
    cfgd = {"KEY_LEVEL_CRASH_ENABLED": True, "KEY_LEVEL_CRASH_EXIT_ENABLED": True, "PARABOLIC_EXIT_ENABLED": True, "BREAKEVEN_DC_LOW4_ENABLED": False,
            "WT_CROSS_EXIT_ENABLED": False, "PEAK_GIVEBACK_PROTECTION_ENABLED": False, "AUGMENTED_DC_BREAK_ENABLED": False, "HTF_EXIT_VETO_ENABLED": False}
    cfg = SimpleNamespace(**cfgd)
    P = LEC.prepare(npz, n, is_long, cfg, pxs, _safe)
    for i in range(n):
        hit = LEC.step(P, cfg, i, float(pxs[i]), {"gain": gain, "peak": max(gain, 0.0), "age_min": 600.0, "entry_price": 10.0})
        ind = {k: float(v[i]) for k, v in npz.items()}
        live = KL.decide(_get(cfgd), ind, is_long, float(pxs[i]), gain)
        if hit is None:
            assert live[0] is False
        else:
            assert live[0] is True and live[1] == hit[0] and hit[1] is False
            assert live[2] == LEC.reduce_fraction(cfg, gain)


def test_key_level_inert_default():
    ind = {"dc_low_15m_prev": 10, "dc_low_1h_prev": 10, "dc_low_4h_prev": 10, "dc_low_D_prev": 10}
    assert KL.decide(_get({}), ind, True, 5.0, 1.0)[0] is False


def test_strict_parity_exemptions(monkeypatch):
    sw = {}
    monkeypatch.setattr(ez_manage, "_psym_cs_get", lambda s, sd, k, d: sw.get(k, d))
    ok = ez_manage._lane_b_vec_only_parity_ok
    for r in ("CRYPTO_REENTRY_PATHWAY_HARDCODED_RALLY_LONG", "HAIKU_WINNER_AUG_3.2pct", "KEY_LEVEL_CRASH_S3_DC_LOW_BROKEN_g0.6"):
        assert ok("AAAUSDT", "LONG", r) is False
    sw.update(CRYPTO_REENTRY_PATHWAYS_ENABLED=True, HAIKU_WINNER_AUGMENT_ENABLED=True, HAIKU_WINNER_ENABLED=True, KEY_LEVEL_CRASH_EXIT_ENABLED=True)
    for r in ("CRYPTO_REENTRY_PATHWAY_HARDCODED_RALLY_LONG", "HAIKU_WINNER_AUG_3.2pct", "HAIKU_REENTER_3.1pct", "KEY_LEVEL_CRASH_S3_DC_LOW_BROKEN_g0.6"):
        assert ok("AAAUSDT", "LONG", r) is True
    sw["HAIKU_WINNER_ENABLED"] = False
    assert ok("AAAUSDT", "LONG", "HAIKU_WINNER_AUG_3.2pct") is False
    assert ok("AAAUSDT", "LONG", "SOMETHING_ELSE") is False


def test_flat_check_queues_vec_reentry(monkeypatch):
    sw = dict(BASE, HARDCODED_RALLY_REENTRY_ENABLED=True)
    monkeypatch.setattr(ez_manage, "_psym_cs_get", lambda s, sd, k, d: sw.get(k, d))
    monkeypatch.setattr(ez_manage, "_vx_native_off", lambda: False)  # native path (exact mode off); under PARITY_VEC_EXACT_MODE this producer is off at the source
    queued = []

    async def fake_ii(tm, sym):
        return {"wt1_15m": 1.0, "wt1_15m_prev": 0.0}

    async def fake_price(sym, pos=None, **kw):
        return 1.10

    async def fake_q(oq, tm, pk, action, reason, conf):
        queued.append((action, reason))
        return "OK"

    monkeypatch.setattr(ez_manage, "ii", fake_ii)
    monkeypatch.setattr(ez_manage, "price", fake_price)
    monkeypatch.setattr(ez_manage, "queue_trade_action", fake_q)
    monkeypatch.setattr(ez_manage, "_recent_opens", {})
    tm = SimpleNamespace(reentry_data={"acc:AAAUSDT_LONG": {"reentry_level": 1.0, "timestamp": "2026-10-06T00:00:00.000000Z", "reason": "CLOSED_DAYTRADE_TARGET"}})
    asyncio.run(ez_manage._parity_flat_open_check(tm, "acc:AAAUSDT_LONG", None, object()))
    assert len(queued) == 1 and queued[0][0] == "REENTRY" and queued[0][1].startswith("CRYPTO_REENTRY_PATHWAY_HARDCODED_RALLY_LONG")
    sw["CRYPTO_REENTRY_PATHWAYS_ENABLED"] = False
    queued.clear()
    asyncio.run(ez_manage._parity_flat_open_check(tm, "acc:AAAUSDT_LONG", None, object()))
    assert queued == []


def test_static_wiring_present():
    src = (Path(__file__).resolve().parents[1] / "ez_manage.py").read_text()
    assert 'bool(getattr(_ezm_base_config, "PARITY_LIVE_SIZING_MULT_ENABLED", False))' in src
    assert '_psym_cs_get(symbol, position_side, "EXIT_VELOCITY_WT_ENABLED", True)' in src
    assert 'not _vp_ok(reason or "") and not _lane_b_vec_only_parity_ok(symbol, position_side, reason or "")' in src
    assert "_lbkl.decide(" in src and "_lbvr.fires(" in src
