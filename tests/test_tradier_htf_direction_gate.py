import numpy as np
from types import SimpleNamespace

from vec_decisions.wave4_families import (
    htf_direction_gate,
    htf_direction_live_pass,
)


def _cfg(**overrides):
    values = {
        "HTF_DIRECTION_GATE_ENABLED": True,
        "HTF_GATE_APPLY_TO_OPEN": True,
        "HTF_GATE_SIGNALS_SMA200D": True,
        "HTF_GATE_MIN_CONFIRMATIONS": 2,
        "HTF_GATE_D_MANDATORY": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _npz_rows(rows):
    return {key: np.array([row.get(key, 0.0) for row in rows], dtype=float) for key in rows[0]}


def test_live_scalar_and_vector_htf_gate_match_both_sides():
    rows = [
        {"wt1_D": 2, "wt2_D": 1, "wt1_4h": 1, "wt2_4h": 2, "wt1_1h": 3, "wt2_1h": 1, "sma_200_D": 9, "close": 10},
        {"wt1_D": 1, "wt2_D": 2, "wt1_4h": 3, "wt2_4h": 1, "wt1_1h": 1, "wt2_1h": 2, "sma_200_D": 9, "close": 8},
    ]
    npz = _npz_rows(rows)
    for is_long in (True, False):
        cfg = _cfg()
        safe = lambda source, key, n, default=0.0: source.get(key, np.full(n, default))
        vector = htf_direction_gate(npz, len(rows), is_long, cfg, npz["close"], safe)
        assert vector is not None
        for index, row in enumerate(rows):
            get = lambda key, default=None: getattr(cfg, key, default)
            scalar = htf_direction_live_pass(row, is_long, get, row["close"])
            assert bool(vector[index]) is scalar


def test_live_htf_gate_respects_master_and_open_application_switches():
    row = {"wt1_D": -2, "wt2_D": 2, "wt1_4h": -2, "wt2_4h": 2, "wt1_1h": -2, "wt2_1h": 2, "sma_200_D": 10}
    assert htf_direction_live_pass(row, True, lambda key, default: False if key == "HTF_DIRECTION_GATE_ENABLED" else default, 9.0)
    assert htf_direction_live_pass(row, True, lambda key, default: False if key == "HTF_GATE_APPLY_TO_OPEN" else default, 9.0)


def test_live_htf_gate_matches_d_mandatory_and_missing_zero_semantics():
    cfg = _cfg(HTF_GATE_MIN_CONFIRMATIONS=1, HTF_GATE_D_MANDATORY=True)
    get = lambda key, default=None: getattr(cfg, key, default)
    row = {"wt1_D": 0, "wt2_D": 0, "wt1_4h": 2, "wt2_4h": 1, "wt1_1h": 2, "wt2_1h": 1, "sma_200_D": 0}
    assert not htf_direction_live_pass(row, True, get, 11.0)
