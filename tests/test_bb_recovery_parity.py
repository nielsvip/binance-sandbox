"""The crypto BB recovery switch must have a named, fee-positive vector close."""

import numpy as np

from vec_decisions.bb_recovery_exit import (
    bb_recovery_exit_mask,
    positive_after_estimated_fees,
    tradier_recovery_bypass_mask,
)
from vec_decisions.twin_p0_crypto_a import vec_bb_recovery_exit


def _simulate(is_long, target_price, enabled=True):
    import v12_quick_engine as vq

    n = 110
    close = np.full(n, 100.0)
    close[12:] = target_price
    k = np.full(n, 50.0)
    k[12:] = 70.0 if is_long else 30.0
    ts = np.arange(1700000000, 1700000000 + n * 180, 180)
    npz = {
        "close": close,
        "close_3m": close,
        "close_15m": close,
        "close_5m": close,
        "timestamps": ts,
        "timestamp_3m": ts,
        "timestamp_15m": ts,
        "low_3m": close,
        "high_3m": close,
        "open_3m": close,
        "dc_low_4h": np.full(n, 80.0),
        "dc_high_4h": np.full(n, 120.0),
        "dc_low_15m": np.full(n, 80.0),
        "dc_high_15m": np.full(n, 120.0),
        "bb_upper_1h": np.full(n, 110.0),
        "bb_lower_1h": np.full(n, 90.0),
        "stoch_k_1h": k,
    }
    for tf in ("15m", "1h", "4h", "D"):
        npz[f"wt1_{tf}"] = np.zeros(n)
        npz[f"wt2_{tf}"] = np.zeros(n)
    cfg = vq.QuickConfig()
    cfg.MODE = "crypto"
    cfg.BASE_TF = "3m"
    cfg.START_POSITION_SIZE = 500.0
    cfg.COOLDOWN_BARS = 1000
    cfg.COMMISSION_BUFFER_PCT = 0.08
    cfg.CRYPTO_ROUND_TRIP_COMMISSION_PCT = 0.04
    cfg.STRUCTURAL_EXIT_GATE_ENABLED = False
    for key in vars(cfg):
        if key.endswith("_ENABLED") and any(
            token in key for token in ("EXIT", "TP", "STOP", "PROFIT", "SATOSHIT")
        ):
            setattr(cfg, key, False)
    cfg.BB_RECOVERY_EXIT_ENABLED_TRADIER = enabled
    cfg.VEC_EXIT_SIG_IS_TRIGGER = False
    cfg.ABLATION_DISABLE_REENTRY = True
    result = vq.simulate_one(npz, "BB_RECOVERY_TEST", is_long, cfg, force_initial_seed=True)
    return result, cfg, npz


def test_shared_scalar_and_vector_predicate_match_and_reject_invalid_bands():
    price = np.array([101.0, 99.0, 101.0, 99.0])
    upper = np.array([110.0, 110.0, 0.0, 110.0])
    lower = np.array([90.0, 90.0, 90.0, 90.0])
    k = np.array([70.0, 30.0, 90.0, np.nan])
    for is_long in (True, False):
        actual = bb_recovery_exit_mask(price, upper, lower, k, is_long)
        expected = vec_bb_recovery_exit(price, upper, lower, k, is_long)
        expected &= np.isfinite(upper) & np.isfinite(lower) & (upper > 0) & (lower > 0) & np.isfinite(k)
        np.testing.assert_array_equal(actual, expected)
    assert actual.shape == (4,)


def test_bb_recovery_books_only_enabled_fee_positive_long_and_short_closes():
    for is_long, target, expected in ((True, 101.0, "LONG"), (False, 99.0, "SHORT")):
        result, cfg, npz = _simulate(is_long, target, enabled=True)
        assert cfg.BB_RECOVERY_EXIT_ENABLED_TRADIER is True
        row = next(
            item for item in result["ledger"]
            if item.get("type") == "CLOSE" and item.get("reason") == f"BB_RECOVERY_EXIT_TRADIER_{expected}"
        )
        assert row["bar_exit"] == 12
        assert row["pnl_dollars"] > 0.0
        assert bb_recovery_exit_mask(
            npz["close"], npz["bb_upper_1h"], npz["bb_lower_1h"], npz["stoch_k_1h"], is_long
        )[12]


def test_bb_recovery_switch_off_and_below_fee_floor_do_not_book_close():
    off, cfg, _ = _simulate(True, 101.0, enabled=False)
    assert cfg.BB_RECOVERY_EXIT_ENABLED_TRADIER is False
    assert not [
        item for item in off["ledger"]
        if item.get("type") == "CLOSE" and str(item.get("reason", "")).startswith("BB_RECOVERY_EXIT_TRADIER_")
    ]
    below_fee, _, _ = _simulate(True, 100.01, enabled=True)
    assert not [
        item for item in below_fee["ledger"]
        if item.get("type") == "CLOSE" and str(item.get("reason", "")).startswith("BB_RECOVERY_EXIT_TRADIER_")
    ]
    assert not positive_after_estimated_fees(0.08, 0.08)
    assert positive_after_estimated_fees(0.09, 0.08)


def _simulate_tradier_bypass(enabled, target_price):
    import v12_quick_engine as vq

    n = 110
    close = np.full(n, 100.0)
    close[12:] = target_price
    from datetime import datetime, timezone

    start_ts = int(datetime(2026, 10, 26, 13, 30, tzinfo=timezone.utc).timestamp())
    ts = np.arange(start_ts, start_ts + n * 300, 300)
    npz = {
        "close": close,
        "close_5m": close,
        "close_15m": close,
        "timestamps": ts,
        "timestamp_5m": ts,
        "timestamp_15m": ts,
        "low_5m": close,
        "high_5m": close,
        "open_5m": close,
        "dc_low_15m": np.full(n, 80.0),
        "dc_high_15m": np.full(n, 120.0),
        "dc_low_4h": np.full(n, 80.0),
        "dc_high_4h": np.full(n, 120.0),
        "bb_high_1h": np.full(n, 99.0),
        "bb_low_1h": np.full(n, 90.0),
        "atr_3m": np.full(n, 0.1),
        "ha_3m": np.full(n, -1, dtype=np.int8),
        "stoch_k_3m": np.full(n, 25.0),
        "stoch_k_3m_prev": np.full(n, 30.0),
    }
    for tf in ("15m", "1h", "4h", "D"):
        npz[f"wt1_{tf}"] = np.zeros(n)
        npz[f"wt2_{tf}"] = np.zeros(n)
    cfg = vq.QuickConfig()
    cfg.MODE = "tradier"
    cfg.BASE_TF = "5m"
    cfg.START_POSITION_SIZE = 500.0
    cfg.COOLDOWN_BARS = 1000
    cfg.COMMISSION_BUFFER_PCT = 0.08
    cfg.COMMISSION_PCT = 0.0
    cfg.BB_RECOVERY_EXIT_ENABLED_TRADIER = enabled
    cfg.BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER = 0.30
    cfg.BB_RECOVERY_EXIT_TOLERANCE_ATR_MULT_TRADIER = 0.0
    cfg.STOCKS_NOLOSS_HOLD_ENABLED = True
    cfg.NOLOSS_MIN_PROFIT_PCT_TRADIER_LIVE = 0.50
    cfg.VEC_EXIT_SIG_IS_TRIGGER = True
    cfg.ABLATION_DISABLE_REENTRY = True
    for key in vars(cfg):
        if key.endswith("_ENABLED") and any(
            token in key for token in ("EXIT", "TP", "STOP", "PROFIT", "SATOSHIT")
        ):
            setattr(cfg, key, False)
    cfg.BB_RECOVERY_EXIT_ENABLED_TRADIER = enabled
    cfg.STOCKS_NOLOSS_HOLD_ENABLED = True
    cfg.NOLOSS_MIN_PROFIT_PCT_TRADIER_LIVE = 0.50
    original = vq.compute_exit_signals
    exit_mask = np.zeros(n, dtype=bool)
    exit_mask[12] = True
    vq.compute_exit_signals = lambda *args, **kwargs: exit_mask.copy()
    try:
        result = vq.simulate_one(
            npz, "BB_RECOVERY_TR_TEST", True, cfg, force_initial_seed=True
        )
    finally:
        vq.compute_exit_signals = original
    return result, cfg


def test_tradier_recovery_only_bypasses_existing_noloss_when_fee_positive():
    admitted, cfg = _simulate_tradier_bypass(True, 100.20)
    assert cfg.BB_RECOVERY_EXIT_ENABLED_TRADIER is True
    close = next(
        row for row in admitted["ledger"]
        if row.get("type") == "CLOSE" and row.get("bar_exit") == 12
    )
    assert close["reason"] == "BB_RECOVERY_EXIT_TRADIER_LONG"

    disabled, _ = _simulate_tradier_bypass(False, 100.20)
    assert not any(
        row.get("type") == "CLOSE" and row.get("bar_exit") == 12
        for row in disabled["ledger"]
    )
    fee_blocked, _ = _simulate_tradier_bypass(True, 100.03)
    assert not any(
        row.get("type") == "CLOSE" and row.get("bar_exit") == 12
        for row in fee_blocked["ledger"]
    )


def test_tradier_recovery_predicate_uses_entry_bb_tolerance_and_reversal():
    args = (100.0, 100.2, 99.0, 90.0, 0.1, -1, 25.0, 30.0, True, 0.30, 0.0)
    assert bool(tradier_recovery_bypass_mask(*args))
    assert not bool(tradier_recovery_bypass_mask(*args[:1], 100.5, *args[2:]))
    assert not bool(tradier_recovery_bypass_mask(*args[:5], 1, 30.0, 25.0, *args[8:]))


def test_quickconfig_keeps_crypto_on_and_tradier_category_off():
    import v12_quick_engine as vq

    cfg = vq.QuickConfig()
    assert cfg.BB_RECOVERY_EXIT_ENABLED_TRADIER is True
    cfg.apply_tradier_defaults()
    assert cfg.MODE == "tradier"
    assert cfg.BB_RECOVERY_EXIT_ENABLED_TRADIER is False
