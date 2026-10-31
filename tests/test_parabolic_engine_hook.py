"""Actual simulate_one PARABOLIC_EXIT ledger reachability in the paired 15m lane."""

import numpy as np
import pytest

from vec_decisions.parabolic_delta import TF_FIELDS, WT_DELTA_FIELDS


def _npz(is_long, n=110):
    close = np.full(n, 100.0)
    close[12:] = 101.0 if is_long else 99.0
    timestamps = np.arange(1_700_000_000, 1_700_000_000 + n * 900, 900)
    arrays = {
        "close": close, "timestamps": timestamps, "timestamp_15m": timestamps,
        "k_15m": np.full(n, 50.0), "k_15m_prev": np.full(n, 50.0),
        "dc_high4_15m": np.full(n, 100.0), "dc_low4_15m": np.full(n, 100.0),
        "low_15m": np.full(n, 10.0), "low_15m_prev": np.full(n, 10.0),
        "high_15m": np.full(n, 10.0), "high_15m_prev": np.full(n, 10.0),
        "dc_low_4h": np.full(n, 50.0), "dc_high_4h": np.full(n, 150.0),
        "dc_low_15m": np.full(n, 50.0), "dc_high_15m": np.full(n, 150.0),
        "open_3m": close, "high_3m": close, "low_3m": close,
        "bb_upper_1h": np.full(n, 150.0), "bb_lower_1h": np.full(n, 50.0),
        "stoch_k_1h": np.full(n, 50.0),
    }
    arrays["k_15m"][12] = 95.0 if is_long else 5.0
    if is_long:
        arrays["low_15m"][12] = 9.0
        arrays["low_15m_prev"][12] = 10.0
    else:
        arrays["high_15m"][12] = 11.0
        arrays["high_15m_prev"][12] = 10.0
    for tf_index, tf in enumerate(("15m", "1h", "4h", "D")):
        for field_index, field in enumerate(TF_FIELDS):
            current = np.full(n, 20.0 + tf_index + field_index)
            if field in WT_DELTA_FIELDS:
                direction = 1.0 if is_long else -1.0
                current = np.arange(n, dtype=float) * direction + 20.0 + tf_index + field_index
                previous = current - direction
                previous[12] = current[12] - direction * 100.0
                arrays[f"{field}_prev_{tf}"] = previous
            elif field.startswith("dc_") and field in ("dc_basis", "dc_high", "dc_low"):
                arrays[f"{field}_{tf}_prev"] = current.copy()
            arrays[f"{field}_{tf}"] = current
    for tf in ("15m", "1h", "4h", "D"):
        arrays[f"wt1_{tf}"] = np.zeros(n)
        arrays[f"wt2_{tf}"] = np.zeros(n)
    return arrays


def _run(is_long, enabled):
    import v12_quick_engine as vq

    cfg = vq.QuickConfig()
    cfg.MODE = "crypto"
    cfg.BASE_TF = "15m"
    cfg.START_POSITION_SIZE = 500.0
    cfg.COOLDOWN_BARS = 1000
    cfg.COMMISSION_BUFFER_PCT = 0.08
    cfg.CRYPTO_ROUND_TRIP_COMMISSION_PCT = 0.04
    cfg.STRUCTURAL_EXIT_GATE_ENABLED = False
    for key in vars(cfg):
        if key.endswith("_ENABLED") and any(token in key for token in ("EXIT", "TP", "STOP", "PROFIT", "SATOSHIT")):
            setattr(cfg, key, False)
    cfg.LIVE_EXIT_CHAIN_ENABLED = True
    cfg.PARABOLIC_EXIT_ENABLED = enabled
    cfg.LIVE_5m_trading_ENABLED = False
    cfg.AUGMENTED_DC_BREAK_ENABLED = False
    cfg.VEC_EXIT_SIG_IS_TRIGGER = False
    cfg.ABLATION_DISABLE_REENTRY = True
    cfg._DECISION_START_TS = 0.0
    return vq.simulate_one(_npz(is_long), "PARABOLIC_TEST", is_long, cfg, force_initial_seed=True)


@pytest.mark.parametrize("is_long", [True, False])
def test_actual_simulate_one_books_parabolic_when_enabled_and_not_when_disabled(is_long):
    enabled = _run(is_long, True)
    disabled = _run(is_long, False)
    expected = f"PARABOLIC_EXIT_{'LONG' if is_long else 'SHORT'}"
    enabled_rows = [row for row in enabled["ledger"] if expected in str(row.get("reason", ""))]
    disabled_rows = [row for row in disabled["ledger"] if "PARABOLIC_EXIT" in str(row.get("reason", ""))]
    assert enabled_rows
    assert not disabled_rows
