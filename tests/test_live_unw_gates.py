"""UNW-L parity tests: live scalar predicates (vec_decisions/live_unw_gates) vs the numpy expressions in v12_quick_engine."""
import os
import re
import sys
from types import SimpleNamespace

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from vec_decisions import live_unw_gates as G  # noqa: E402

SRC = open(os.path.join(ROOT, "v12_quick_engine.py")).read()


def _get(cfg):
    return lambda k, d=None: cfg.get(k, d)


def test_vector_expressions_unchanged():
    # drift guard: if the vector expression changes this test must be re-reviewed with the live twin
    assert "_bull_mask = (_close_d > _sma20_d) & (_wt_4h_bull > _wt_thr)" in SRC
    assert "_bear_mask = (_close_d_b < _sma20_b) & (_wt_4h_bear < _wt_thr_b)" in SRC
    assert "_bull_a = (_close_d_a > _sma20_a) & (_wt4_a > _thr_a)" in SRC
    assert "augment_sig = augment_sig & (~_bull_a)" in SRC
    assert "_c != '1h_4h_D'" in SRC and "_p = _c.split('_')" in SRC


def test_bull_bear_masks_match_vector():
    rng = np.random.default_rng(7)
    n = 5000
    close_d = rng.uniform(50, 150, n)
    sma = close_d * rng.uniform(0.8, 1.2, n)
    wt = rng.uniform(-100, 100, n)
    thr_b, thr_r = -53.0, 53.0
    vec_bull = (close_d > sma) & (wt > thr_b)
    vec_bear = (close_d < sma) & (wt < thr_r)
    for i in range(n):
        ind = {"close_D": close_d[i], "sma_20_D": sma[i], "wt1_4h": wt[i]}
        assert G.d_bull(ind, thr_b, fallback_wt_4h=False) == bool(vec_bull[i])
        assert G.d_bear(ind, thr_r, fallback_wt_4h=False) == bool(vec_bear[i])


def test_augment_bull_kill_default_off_and_on():
    ind = {"close_D": 110.0, "sma_20_D": 100.0, "wt1_4h": -10.0}
    assert G.augment_bull_kill_blocked(_get({}), ind) is False
    assert G.augment_bull_kill_blocked(_get({"AUGMENT_BULL_KILL_ENABLED": True}), ind) is True
    assert G.augment_bull_kill_blocked(_get({"AUGMENT_BULL_KILL_ENABLED": True}), {"close_D": 90.0, "sma_20_D": 100.0, "wt1_4h": -10.0}) is False
    # missing sma -> equals close -> not bull (vector: _safe default = close)
    assert G.augment_bull_kill_blocked(_get({"AUGMENT_BULL_KILL_ENABLED": True}), {"close_D": 110.0, "wt1_4h": 10.0}) is False


def test_exit_hold():
    bull = {"close_D": 110.0, "sma_20_D": 100.0, "wt1_4h": 0.0}
    bear = {"close_D": 90.0, "sma_20_D": 100.0, "wt1_4h": 0.0}
    assert G.exit_hold_blocked(_get({}), bull, True, "WT_CROSS_EXIT") is False  # default 0 = off
    assert G.exit_hold_blocked(_get({"BULL_HOLD_EXIT_DELAY_BARS": 24}), bull, True, "WT_CROSS_EXIT_15M") is True
    assert G.exit_hold_blocked(_get({"BULL_HOLD_EXIT_DELAY_BARS": 24}), bear, True, "WT_CROSS_EXIT_15M") is False
    assert G.exit_hold_blocked(_get({"BEAR_HOLD_EXIT_DELAY_BARS": 24}), bear, False, "DELTA_EXIT_TOP") is True
    assert G.exit_hold_blocked(_get({"BEAR_HOLD_EXIT_DELAY_BARS": 24}), bear, True, "DELTA_EXIT_TOP") is False  # long unaffected
    # protective exits are never held
    assert G.exit_hold_blocked(_get({"BULL_HOLD_EXIT_DELAY_BARS": 24}), bull, True, "NOLOSS_HEDGE_FAILED") is False
    assert G.exit_hold_blocked(_get({"BULL_HOLD_EXIT_DELAY_BARS": 24}), bull, True, "R1_LOSS_EXIT") is False


def test_wtdc_combo_matches_vector_resolver():
    import v12_quick_engine as V
    for combo in ["1h_4h_D", "15m_1h_4h", "15m_1h_4h_D", "4h_D_none", "bad", "", None]:
        cfg = SimpleNamespace(WT_DC_TF_COMBO=combo, WT_DC_TF_ENTRY="1h", WT_DC_TF_HTF="4h", WT_DC_TF_HTF2="D")
        V._wtdc_combo_resolve(cfg)
        live = G.wtdc_combo_tfs(_get({"WT_DC_TF_COMBO": combo} if combo is not None else {}))
        exp = (cfg.WT_DC_TF_ENTRY, cfg.WT_DC_TF_HTF, cfg.WT_DC_TF_HTF2)
        got = live if live is not None else ("1h", "4h", "D")
        assert got == exp, (combo, got, exp)


def test_augment_typed_tier_default_neutral_and_typed():
    generic = 3.0
    assert G.augment_tier_min_gain(_get({}), "AUG_B_WT_CROSS_15m_g1.0%", generic) == generic
    assert G.augment_tier_min_gain(_get({"AUGMENT_BOUNCE_MIN_GAIN_PCT": 0.25}), "AUG_B_WT_CROSS_15m", generic) == generic  # master off
    on = {"AUGMENT_TYPED_MIN_GAIN_ENABLED": True, "AUGMENT_BOUNCE_MIN_GAIN_PCT": 1.0, "AUGMENT_BREAKOUT_MIN_GAIN_PCT": 4.0}
    assert G.augment_tier_min_gain(_get(on), "AUG_B_WT_CROSS_15m_g1.0%", generic) == 1.0
    assert G.augment_tier_min_gain(_get(on), "AUG_A_BLOWPAST_g2.0%", generic) == 4.0
    assert G.augment_tier_min_gain(_get(on), "DC_TIER2_1h_AUG", generic) == 4.0
    assert G.augment_tier_min_gain(_get(on), "HAIKU_AUGMENT", generic) == generic  # untyped -> generic tier


def test_wt_composite_block_matches_vector():
    from vec_decisions import wt_composite_gate as W
    rng = np.random.default_rng(3)
    n = 3000
    npz = {"wt_bull_alignment": rng.integers(0, 6, n).astype(float), "wt_bear_alignment": rng.integers(0, 6, n).astype(float),
           "wt_composite_long": rng.uniform(-100, 100, n), "wt_composite_short": rng.uniform(-100, 100, n)}
    safe = lambda d, k, nn, default=0.0: d.get(k, np.full(nn, default))
    cfg = SimpleNamespace(WT_COMPOSITE_HTF_GATE=True, WT_COMPOSITE_SCORING_ENABLED=True, WT_COMPOSITE_ENTRY_BLOCK=-20.0)
    for is_long in (True, False):
        vec = W.block_mask(npz, n, is_long, cfg, safe)
        kk = {"WT_COMPOSITE_HTF_GATE": True, "WT_COMPOSITE_SCORING_ENABLED": True, "WT_COMPOSITE_ENTRY_BLOCK": -20.0}
        for i in range(n):
            ind = {k: v[i] for k, v in npz.items()}
            assert G.wt_composite_block(_get(kk), ind, is_long) == bool(vec[i])
    assert G.wt_composite_block(_get({}), {"wt_bull_alignment": 0, "wt_composite_long": -99}, True) is False  # master off
