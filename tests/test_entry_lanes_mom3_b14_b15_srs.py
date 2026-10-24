"""Missing-producer lanes (USER 2026-10-24 unlock: wire all missing producers).

B_MOM3, B14, B15 are entry triggers (selective predicates -> opener lanes);
B_SRS_ENTRY is an entry permission (~90% of bars pass -> veto, never opener:
wiring it as an opener would flood entries the vector never takes).
Each live lane shares one pure core with the vector; scalar and mask must
agree exactly, and boundary/threshold behavior must match the vec formula.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_mom3_scalar_mask_agree():
    from vec_decisions import check_entry_mom3 as M

    rng = np.random.default_rng(7)
    close = rng.uniform(5, 500, 40)
    c3 = rng.uniform(5, 500, 40)
    for is_long in (True, False):
        s = np.array([M.mom3_fires_scalar(c, x, -1.0, 1.0, is_long) for c, x in zip(close, c3)])
        m = np.asarray(M.mom3_mask_vec(close, c3, -1.0, 1.0, is_long), dtype=bool)
        assert (s == m).all()
    assert M.mom3_fires_scalar(100.0, 102.0, -1.0, 1.0, True) is True
    assert M.mom3_fires_scalar(100.0, 100.5, -1.0, 1.0, True) is False
    assert M.mom3_fires_scalar(102.0, 100.0, -1.0, 1.0, False) is True
    assert M.mom3_fires_scalar(100.0, 0.0, -1.0, 1.0, True) is False
    assert M.mom3_fires_scalar(None, 100.0, -1.0, 1.0, True) is False


def test_b14_scalar_mask_agree():
    from vec_decisions import check_entry_b14_ha_stack as B

    rng = np.random.default_rng(11)
    ha = rng.choice([-1.0, 0.0, 1.0], 60)
    kk = rng.uniform(0, 100, 60)
    for is_long in (True, False):
        s = np.array([B.b14_fires_scalar(h, h, 1.0 if is_long else -1.0, k, is_long) for h, k in zip(ha, kk)])
        m = np.asarray(B.b14_mask_vec(ha, ha, np.full(60, 1.0 if is_long else -1.0), kk, is_long), dtype=bool)
        assert (s == m).all()
    assert B.b14_fires_scalar(1, 1, 1, 59.9, True) is True
    assert B.b14_fires_scalar(1, 1, 1, 60.0, True) is False
    assert B.b14_fires_scalar(1, 1, -1, 10.0, True) is False
    assert B.b14_fires_scalar(-1, -1, -1, 40.1, False) is True
    assert B.b14_fires_scalar(-1, -1, -1, 40.0, False) is False


def test_b15_scalar_mask_agree():
    from vec_decisions import check_entry_b15_trend as B

    rng = np.random.default_rng(13)
    close = rng.uniform(5, 500, 40)
    dc = rng.uniform(5, 500, 40)
    vel = rng.uniform(-5, 5, 40)
    kk = rng.uniform(0, 100, 40)
    for is_long in (True, False):
        s = np.array([B.b15_fires_scalar(c, d, v, k, is_long) for c, d, v, k in zip(close, dc, vel, kk)])
        m = np.asarray(B.b15_mask_vec(close, dc, vel, kk, is_long), dtype=bool)
        assert (s == m).all()
    assert B.b15_fires_scalar(101.0, 100.0, 2.1, 84.9, True) is True
    assert B.b15_fires_scalar(101.0, 100.0, 2.0, 84.9, True) is False
    assert B.b15_fires_scalar(101.0, 100.0, 2.1, 85.0, True) is False
    assert B.b15_fires_scalar(99.0, 100.0, -2.1, 15.1, False) is True
    assert B.b15_fires_scalar(99.0, 0.0, -9.0, 50.0, False) is False


def test_srs_permission_shape():
    from vec_decisions import check_entry_srs_permission as S

    assert S.pctb_key_for_tf("bb_1h") == "bb_pct_b_1h"
    assert S.pctb_key_for_tf("dc_4h") == "bb_pct_b_4h"
    assert S.pctb_key_for_tf("nonsense") == "bb_pct_b_1h"
    assert S.srs_blocked_scalar(0.97, True) is True
    assert S.srs_blocked_scalar(0.969, True) is False
    assert S.srs_blocked_scalar(0.03, False) is True
    assert S.srs_blocked_scalar(0.031, False) is False
    assert S.srs_blocked_scalar(None, True) is False
    v = np.array([0.5, 0.97, 0.99, 0.02])
    assert list(S.srs_blocked_mask_vec(v, True)) == [False, True, True, False]
    assert list(S.srs_blocked_mask_vec(v, False)) == [False, False, False, True]


def test_lane_wiring_present_in_tradier():
    src = Path("tradier_manage.py").read_text()
    assert 'reason = f"TRADIER_MOM3_' in src
    assert 'reason = f"TRADIER_B14_HA_STACK_' in src
    assert 'reason = f"TRADIER_B15_TREND_' in src
    assert 'SRS_EXTREME_GATE(pctb=' in src
    assert src.count("check_entry_mom3") >= 1
    assert src.count("check_entry_b14_ha_stack") >= 1
    assert src.count("check_entry_b15_trend") >= 1
    assert src.count("check_entry_srs_permission") >= 1
