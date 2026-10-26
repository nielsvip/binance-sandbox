from types import SimpleNamespace

import numpy as np

from v12_quick_engine import _ema_blanket_final_entry_mask
from vec_decisions.check_entry_b14_ha_stack import b14_fires_scalar


def _safe(npz, key, n, default=0.0):
    return np.asarray(npz.get(key, np.full(n, default)))


def test_tia_b14_event_is_blocked_by_live_off_tf_ema_blanket_and_other_or_is_inert():
    # TIAUSDC_LONG NPZ at 2026-10-26 04:00 UTC (bar 1792987200).
    event = {
        "ha_15m": np.asarray([1.0]),
        "ha_1h": np.asarray([1.0]),
        "stoch_k_15m": np.asarray([49.024497985839844]),
        "ema_9_above_21_15m": np.asarray([0.0]),
        "ema_9_above_21_1h": np.asarray([0.0]),
        "ema_9_above_21_4h": np.asarray([0.0]),
        "ema_9_above_21_D": np.asarray([1.0]),
    }
    assert b14_fires_scalar(1.0, 1.0, 1.0, 49.024497985839844, True)
    cfg = SimpleNamespace(
        MODE="crypto",
        EMA_BLANKET_FILTER_ENABLED=True,
        EMA_BLANKET_FILTER_FILTER_TF="OFF",
        EMA_BLANKET_FILTER_MIN_TFS=3,
    )
    gate = _ema_blanket_final_entry_mask(event, 1, True, cfg, np.ones(1), _safe)
    assert gate.tolist() == [False]
    candidate_from_any_or_source = np.asarray([True])
    assert (candidate_from_any_or_source & gate).tolist() == [False]
    cfg.EMA_BLANKET_FILTER_ENABLED = False
    assert _ema_blanket_final_entry_mask(event, 1, True, cfg, np.ones(1), _safe) is None
    assert candidate_from_any_or_source.tolist() == [True]
