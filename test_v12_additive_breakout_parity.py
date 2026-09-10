import numpy as np

import v12_quick_engine as v12


def _npz():
    n = 6
    return {
        "close_5m": np.array([100.0, 106.0, 94.0, 110.0, 90.0, 100.0]),
        "bb_pct_b_1h": np.array([0.5, 1.1, -0.1, 1.2, -0.2, 0.5]),
        "sma_200_1h": np.full(n, 100.0),
        "adx_1h": np.array([30.0, 30.0, 30.0, 20.0, 30.0, 30.0]),
        # Deliberately unusable current channels: the causal breakout must use
        # the prior completed channel arrays below.
        "dc_high_1h": np.full(n, 200.0),
        "dc_low_1h": np.full(n, 1.0),
        "dc_high_1h_prev": np.full(n, 105.0),
        "dc_low_1h_prev": np.full(n, 95.0),
    }


def _cfg(**overrides):
    cfg = v12.QuickConfig()
    cfg.BASE_TF = "5m"
    for name, value in overrides.items():
        setattr(cfg, name, value)
    return cfg


def test_bb_breakout_matches_live_selected_tf_predicate():
    npz = _npz()
    long = v12.compute_reentry_blocks(
        npz, 6, True, _cfg(BB_BREAKOUT_ENABLED=True)
    )["B_BB_BREAKOUT"]
    short = v12.compute_reentry_blocks(
        npz, 6, False, _cfg(BB_BREAKOUT_ENABLED=True)
    )["B_BB_BREAKOUT"]
    assert long.tolist() == [False, True, False, False, False, False]
    assert short.tolist() == [False, False, True, False, True, False]


def test_dc_breakout_uses_prior_completed_channel_and_fails_closed():
    npz = _npz()
    long = v12.compute_reentry_blocks(
        npz, 6, True, _cfg(DC_BREAKOUT_ENTRY_ENABLED=True)
    )["B_DC_BREAKOUT"]
    short = v12.compute_reentry_blocks(
        npz, 6, False, _cfg(DC_BREAKOUT_ENTRY_ENABLED=True)
    )["B_DC_BREAKOUT"]
    assert long.tolist() == [False, True, False, False, False, False]
    assert short.tolist() == [False, False, True, False, True, False]

    missing = dict(npz)
    missing.pop("dc_high_1h_prev")
    blocked = v12.compute_reentry_blocks(
        missing, 6, True, _cfg(DC_BREAKOUT_ENTRY_ENABLED=True)
    )["B_DC_BREAKOUT"]
    assert not blocked.any()


def test_breakout_scores_are_exact_additive_contributions():
    blocks = {
        "B_BB_BREAKOUT": np.array([True, False, True]),
        "B_DC_BREAKOUT": np.array([False, True, True]),
    }
    cfg = _cfg(
        BB_BREAKOUT_SCORE=20,
        DC_BREAKOUT_SCORE=15,
        ENTRY_SCORE_THRESHOLD=18,
    )
    admitted, score = v12._combine_entry_blocks(blocks, cfg, 3)
    assert score.tolist() == [20.0, 15.0, 35.0]
    assert admitted.tolist() == [True, False, True]
