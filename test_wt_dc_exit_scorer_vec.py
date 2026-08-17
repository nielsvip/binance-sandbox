from types import SimpleNamespace

import numpy as np

from vec_paths.rz_cascade import score_wt_dc_exit_vec
from wt_dc_exit_scorer import score_exit


def test_wt_dc_exit_vector_scores_match_scalar_n_of_five():
    cfg = SimpleNamespace(
        EXIT_SCORER_MIN_CONDITIONS=5,
        EXIT_SCORER_K_EXTREME=75.0,
        EXIT_SCORER_DC_EXTREME=0.80,
        EXIT_SCORER_PARTIAL_SCORE=40.0,
        EXIT_SCORER_FULL_SCORE=100.0,
    )
    npz = {
        "wt1_1h": np.array([-1, -1, 1, 1], dtype=float),
        "wt2_1h": np.zeros(4),
        "wt1_4h": np.array([-1, -1, 1, 1], dtype=float),
        "wt2_4h": np.zeros(4),
        "wt1_D": np.array([-1, 1, 1, -1], dtype=float),
        "wt2_D": np.zeros(4),
        "stoch_k_1h": np.array([80, 80, 20, 20], dtype=float),
        "stoch_k_4h": np.full(4, 50.0),
        "dc_position_1h": np.array([0.9, 0.9, 0.1, 0.1]),
        "dc_position_4h": np.full(4, 0.5),
    }
    for is_long in (True, False):
        vector = score_wt_dc_exit_vec(npz, 4, is_long, cfg)
        scalar = []
        for index in range(4):
            indicators = {name: float(values[index]) for name, values in npz.items()}
            scalar.append(score_exit(indicators, is_long, cfg=cfg)[0])
        assert vector.tolist() == scalar
