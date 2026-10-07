"""Focused reusable test for 5 bounce-after-correction reentry predicates.

Covers each switch at its threshold + disabled + better-price failure,
and vector parity for the two vec-masked predicates.

Mirrors test style of vec_decisions/test_guaranteed_price_cross_reentry.py
but minimal — one file owns all 5 predicates.
"""

import numpy as np
import types

from vec_decisions.reentry_bounce_after_correction import (
    _bounce_bar_gr_fires,
    _pullback_gr_fires,
    _dc_mid_fires,
    _k_reset_fires,
    _sma200_gr_fires,
    check_bounce_bar_gr,
    check_bounce_bar_gr_vec,
    check_pullback_gr_vec,
)


def _cfg(**kw):
    c = types.SimpleNamespace()
    for k, v in kw.items():
        setattr(c, k, v)
    # defaults for bounce
    for k, d in [
        ("REENTRY_BOUNCE_BAR_GR_ENABLED", False),
        ("REENTRY_BOUNCE_BAR_GR_MIN_TFS", 2),
        ("REENTRY_BOUNCE_BAR_GR_BETTER_PCT", 0.002),
        ("REENTRY_PULLBACK_GR_SCORE_ENABLED", False),
        ("REENTRY_PULLBACK_GR_SCORE_MIN", 12),
        ("REENTRY_DC_MID_PULLBACK_ENABLED", False),
        ("REENTRY_K_RESET_GR_ENABLED", False),
        ("REENTRY_SMA200_GR_CONTINUATION_ENABLED", False),
    ]:
        if not hasattr(c, k):
            setattr(c, k, d)
    return c


def _ind(**kw):
    return dict(kw)


# ── Pure predicate unit tests ──

def test_bounce_bar_gr_fires_at_better_price_with_gr():
    assert _bounce_bar_gr_fires(price=99.5, exit_price=100.0, is_long=True,
                                gr_count=2, k15m=40, bar_turn=True, better_pct=0.002, min_tfs=2) is True


def test_bounce_bar_gr_blocks_when_not_better_price():
    # price above exit = worse for LONG
    assert _bounce_bar_gr_fires(price=100.5, exit_price=100.0, is_long=True,
                                gr_count=3, k15m=40, bar_turn=True, better_pct=0.002, min_tfs=2) is False


def test_bounce_bar_gr_blocks_when_no_bar_turn():
    assert _bounce_bar_gr_fires(price=99.5, exit_price=100.0, is_long=True,
                                gr_count=3, k15m=40, bar_turn=False, better_pct=0.002, min_tfs=2) is False


def test_bounce_bar_gr_blocks_when_gr_too_low():
    assert _bounce_bar_gr_fires(price=99.5, exit_price=100.0, is_long=True,
                                gr_count=1, k15m=40, bar_turn=True, better_pct=0.002, min_tfs=2) is False


def test_bounce_bar_gr_disabled_gate():
    ind = _ind(high_3m=102, high_3m_prev=101, low_3m=98, low_3m_prev=97,
               wt1_3m=60, wt2_3m=40, wt1_15m=60, wt2_15m=40, stoch_k_15m=40)
    cfg = _cfg(REENTRY_BOUNCE_BAR_GR_ENABLED=False)
    fires, _ = check_bounce_bar_gr(ind, True, 99.5, 100.0, cfg)
    assert fires is False


def test_bounce_bar_gr_enabled_fires_via_scalar():
    ind = _ind(high_3m=102, high_3m_prev=101, low_3m=98, low_3m_prev=97,
               wt1_3m=60, wt2_3m=40, wt1_15m=60, wt2_15m=40, wt1_1h=60, wt2_1h=40, wt1_4h=60, wt2_4h=40, wt1_D=60, wt2_D=40,
               stoch_k_15m=40)
    cfg = _cfg(REENTRY_BOUNCE_BAR_GR_ENABLED=True, REENTRY_BOUNCE_BAR_GR_MIN_TFS=2)
    fires, reason = check_bounce_bar_gr(ind, True, 99.5, 100.0, cfg)
    assert fires is True
    assert "BOUNCE_BAR_GR" in reason


def test_pullback_gr_pullback_condition():
    # 3m bullish, 15m bearish, gr_score 15 >=12, k 30 in range
    assert _pullback_gr_fires(wt1_3m=60, wt2_3m=40, wt1_15m=40, wt2_15m=60,
                              gr_score=15, k15m=30, price=99.5, exit_price=100.0,
                              is_long=True, score_min=12, better_pct=0.002) is True


def test_pullback_gr_blocks_when_15m_not_bearish():
    assert _pullback_gr_fires(wt1_3m=60, wt2_3m=40, wt1_15m=60, wt2_15m=40,
                              gr_score=15, k15m=30, price=99.5, exit_price=100.0,
                              is_long=True, score_min=12, better_pct=0.002) is False


def test_dc_mid_fires_with_contracting_width():
    assert _dc_mid_fires(price=100.0, dc_basis=100.0, dc_width=10.0, dc_pos=0.4,
                         gr_count=2, width_max=12.0, min_tfs=2,
                         exit_price=100.5, is_long=True, better_pct=0.002) is True


def test_dc_mid_blocks_when_width_too_wide():
    assert _dc_mid_fires(price=100.0, dc_basis=100.0, dc_width=15.0, dc_pos=0.4,
                         gr_count=3, width_max=12.0, min_tfs=2,
                         exit_price=100.5, is_long=True, better_pct=0.002) is False


def test_k_reset_fires_on_cross():
    assert _k_reset_fires(k=35, k_prev=25, gr_count=2, wt1_3m=60, wt2_3m=40,
                          is_long=True, min_tfs=2, price=99.5, exit_price=100.0, better_pct=0.002) is True


def test_k_reset_blocks_when_no_cross():
    assert _k_reset_fires(k=40, k_prev=35, gr_count=2, wt1_3m=60, wt2_3m=40,
                          is_long=True, min_tfs=2, price=99.5, exit_price=100.0, better_pct=0.002) is False


def test_sma200_gr_fires_above_sma_with_hh():
    assert _sma200_gr_fires(price=101.0, sma200=100.0, gr_count=2, is_long=True,
                            min_tfs=2, exit_price=105.0, better_pct=0.002, hh=True) is True


def test_sma200_gr_blocks_when_below_sma_long():
    assert _sma200_gr_fires(price=99.0, sma200=100.0, gr_count=3, is_long=True,
                            min_tfs=2, exit_price=105.0, better_pct=0.002, hh=True) is False


# ── Vector parity (bounce + pullback vec masks) ──

def test_vec_bounce_bar_matches_scalar():
    price = np.array([99.5, 100.5, 99.5, 99.5])
    exit_px = np.array([100.0, 100.0, 100.0, 100.0])
    gr = np.array([2, 2, 1, 2])
    k = np.array([40.0, 40.0, 40.0, 75.0])
    bar = np.array([True, True, True, True])
    mask = check_bounce_bar_gr_vec(price, exit_px, True, gr, k, bar, 0.002, 2)
    # only first should fire: better+gr+k+bar ; 2nd worse price, 3rd gr low, 4th k too high
    assert list(mask) == [True, False, False, False]


def test_vec_pullback_matches_scalar():
    price = np.array([99.5, 99.5, 99.5])
    exit_px = np.array([100.0, 100.0, 100.0])
    gr_score = np.array([15, 15, 5])
    k = np.array([30.0, 30.0, 30.0])
    wt1_3m = np.array([60.0, 60.0, 60.0]); wt2_3m = np.array([40.0, 40.0, 40.0])
    wt1_15m = np.array([40.0, 60.0, 40.0]); wt2_15m = np.array([60.0, 40.0, 60.0])
    mask = check_pullback_gr_vec(price, exit_px, True, wt1_3m, wt2_3m, wt1_15m, wt2_15m, gr_score, k, 0.002, 12)
    assert list(mask) == [True, False, False]
