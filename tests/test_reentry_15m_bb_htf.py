"""Testable 15m BB/DC reentries with HTF confirmation — both live and vector."""

import types
import numpy as np

from vec_decisions.reentry_15m_bb_htf import (
    _dc_basis_cross_htf_fires,
    _lrl_pullback_htf_fires,
    _bb1h_low_bounce_htf_fires,
    check_dc_basis_cross_htf,
    check_dc_basis_cross_htf_vec,
)


def _cfg(**kw):
    c = types.SimpleNamespace()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _ind(**kw):
    return dict(kw)


def test_dc_basis_cross_long_fires():
    assert _dc_basis_cross_htf_fires(close_15m=101, close_15m_prev=99, dc_basis_15m=100, dc_basis_prev=100,
                                     htf_count=2, bb_pct_b_1h=0.5, price=99.5, exit_price=100, is_long=True, min_tfs=2, better_pct=0.002) is True


def test_dc_basis_cross_blocks_when_htf_low():
    assert _dc_basis_cross_htf_fires(close_15m=101, close_15m_prev=99, dc_basis_15m=100, dc_basis_prev=100,
                                     htf_count=1, bb_pct_b_1h=0.5, price=99.5, exit_price=100, is_long=True, min_tfs=2, better_pct=0.002) is False


def test_dc_basis_blocks_when_not_better_price():
    assert _dc_basis_cross_htf_fires(close_15m=101, close_15m_prev=99, dc_basis_15m=100, dc_basis_prev=100,
                                     htf_count=3, bb_pct_b_1h=0.5, price=100.5, exit_price=100, is_long=True, min_tfs=2, better_pct=0.002) is False


def test_lrl_pullback_long_fires():
    assert _lrl_pullback_htf_fires(lrl_pct_prev=0.15, lrl_pct_now=0.35, wt1_15m=60, wt2_15m=40,
                                   htf_count=2, price=99.5, exit_price=100, is_long=True, min_tfs=2, better_pct=0.002) is True


def test_lrl_pullback_blocks_when_wt_bearish():
    assert _lrl_pullback_htf_fires(lrl_pct_prev=0.15, lrl_pct_now=0.35, wt1_15m=40, wt2_15m=60,
                                   htf_count=2, price=99.5, exit_price=100, is_long=True, min_tfs=2, better_pct=0.002) is False


def test_bb1h_low_bounce_long_fires():
    assert _bb1h_low_bounce_htf_fires(bb_pct_1h=0.30, bb_pct_prev=0.15, close_15m=101, bb_lower_1h=100,
                                     wt1_15m=60, wt2_15m=40, htf_count=2, price=99.5, exit_price=100, is_long=True, min_tfs=2, better_pct=0.002) is True


def test_bb1h_low_bounce_blocks_when_no_wt():
    assert _bb1h_low_bounce_htf_fires(bb_pct_1h=0.30, bb_pct_prev=0.15, close_15m=101, bb_lower_1h=100,
                                     wt1_15m=40, wt2_15m=60, htf_count=2, price=99.5, exit_price=100, is_long=True, min_tfs=2, better_pct=0.002) is False


def test_scalar_disabled_gate():
    cfg = _cfg(REENTRY_15M_DC_BASIS_CROSS_HTF_ENABLED=False)
    ind = _ind(close_15m=101, close_15m_prev=99, dc_basis_15m=100, dc_basis_15m_prev=100,
               wt1_15m=60, wt2_15m=40, wt1_1h=60, wt2_1h=40, wt1_4h=60, wt2_4h=40, wt1_D=60, wt2_D=40, bb_pct_b_1h=0.5)
    fires, _ = check_dc_basis_cross_htf(ind, True, 99.5, 100, cfg)
    assert fires is False


def test_scalar_enabled_fires():
    cfg = _cfg(REENTRY_15M_DC_BASIS_CROSS_HTF_ENABLED=True, REENTRY_15M_DC_BASIS_CROSS_HTF_MIN_TFS=2)
    ind = _ind(close_15m=101, close_15m_prev=99, dc_basis_15m=100, dc_basis_15m_prev=100,
               wt1_15m=60, wt2_15m=40, wt1_1h=60, wt2_1h=40, wt1_4h=60, wt2_4h=40, wt1_D=60, wt2_D=40, bb_pct_b_1h=0.5)
    fires, reason = check_dc_basis_cross_htf(ind, True, 99.5, 100, cfg)
    assert fires is True
    assert "BB_BASIS_CROSS" in reason or "DC_BASIS_CROSS" in reason


def test_vec_dc_basis_matches_scalar():
    price = np.array([99.5, 100.5, 99.5])
    exit_px = np.array([100.0, 100.0, 100.0])
    close_15m = np.array([101.0, 101.0, 99.0])
    close_prev = np.array([99.0, 99.0, 99.0])
    dc_basis = np.array([100.0, 100.0, 100.0])
    dc_prev = np.array([100.0, 100.0, 100.0])
    htf = np.array([2, 2, 2])
    bb = np.array([0.5, 0.5, 0.5])
    mask = check_dc_basis_cross_htf_vec(close_15m, close_prev, dc_basis, dc_prev, htf, bb, price, exit_px, True, 2, 0.002)
    # 0 fires (better+cross+htf), 1 fails better_price, 2 fails cross (still below)
    assert list(mask) == [True, False, False]
