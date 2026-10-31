"""WR/LR uses common canonical tradeable sym-side eligibility, not legacy ranking books."""

import numpy as np

from vec_decisions.check_entry_candidates_crypto__wr_lr_pullback import (
    _wr_lr_pullback_fires,
    canonical_tradeable_sym_side,
)


def test_retained_tradeable_key_fires_shared_wr_even_when_ang_and_inf_books_omit_it():
    symbol = "RETAINEDUSDC"
    tradeable_keys = {"ang:RETAINEDUSDC_LONG"}
    legacy_books = {"ang_long": set(), "ang_short": set(), "inf_long": set(), "inf_short": set()}
    assert not any(symbol in legacy_books[name] for name in ("ang_long", "inf_long"))
    assert not any(symbol in legacy_books[name] for name in ("ang_short", "inf_short"))
    ang_authorized = canonical_tradeable_sym_side(symbol, True, "ang", tradeable_keys)
    inf_authorized = canonical_tradeable_sym_side(symbol, True, "inf", tradeable_keys)
    assert ang_authorized
    assert not inf_authorized
    fire_ang = _wr_lr_pullback_fires(
        "green", 30.0, 20.0, 20.0, 20.0, 19.0, "", ang_authorized, True
    )
    fire_inf = _wr_lr_pullback_fires(
        "green", 30.0, 20.0, 20.0, 20.0, 19.0, "", inf_authorized, True
    )
    assert fire_ang is True
    assert fire_inf is False


def test_canonical_tradeable_sym_side_is_side_specific_and_fails_closed_when_absent():
    keys = {"ang:RETAINEDUSDC_LONG"}
    assert canonical_tradeable_sym_side("RETAINEDUSDC", True, "ang", keys)
    assert not canonical_tradeable_sym_side("RETAINEDUSDC", False, "ang", keys)
    assert not canonical_tradeable_sym_side("OTHERUSDC", True, "ang", keys)
    assert not canonical_tradeable_sym_side("RETAINEDUSDC", True, "ang", set())
    assert not canonical_tradeable_sym_side("RETAINEDUSDC", True, "ANG", keys)
    assert canonical_tradeable_sym_side("retainedusdc", True, "ang", keys)


def _wr_npz(n=110):
    close = np.full(n, 100.0)
    timestamps = np.arange(1_800_000_000, 1_800_000_000 + n * 900, 900)
    k15 = np.full(n, 20.0)
    k15_prev = np.full(n, 20.0)
    k15_prev[12] = 19.0
    npz = {
        "close_15m": close,
        "open_15m": close,
        "high_15m": close * 1.01,
        "low_15m": close * 0.99,
        "timestamps": timestamps,
        "timestamp_15m": timestamps,
        "stoch_k_15m": k15,
        "stoch_k_15m_prev": k15_prev,
        "stoch_k_1h": np.full(n, 30.0),
        "stoch_k_4h": np.full(n, 50.0),
        "wt_cross_bull_15m": np.zeros(n),
        "wt_cross_bear_15m": np.zeros(n),
        "ha_4h": np.ones(n, dtype=np.int8),
        "dc_low_15m": np.full(n, 80.0),
        "dc_high_15m": np.full(n, 120.0),
        "dc_low_4h": np.full(n, 70.0),
        "dc_high_4h": np.full(n, 130.0),
    }
    for tf in ("15m", "1h", "4h", "D"):
        npz[f"wt1_{tf}"] = np.zeros(n)
        npz[f"wt2_{tf}"] = np.zeros(n)
    return npz


def _wr_cfg(enabled):
    import v12_quick_engine as vq

    cfg = vq.QuickConfig()
    cfg.MODE = "crypto"
    cfg.BASE_TF = "15m"
    cfg.apply_cat_side_defaults("CRYPTO_LONG")
    for name in vars(cfg):
        if name.endswith("_ENABLED"):
            setattr(cfg, name, False)
    cfg.WR_PULLBACK_ENABLED = enabled
    cfg.USE_1M_3M_SIGNALS_ENABLED = False
    cfg.STRENGTH_FILTER_ENABLED = True
    cfg.STRENGTH_MIN_SCORE = 5.0
    cfg.START_POSITION_SIZE = 100.0
    cfg.MAX_ORDER_VALUE = 500.0
    cfg.COOLDOWN_BARS = 0
    cfg.CRYPTO_ROUND_TRIP_COMMISSION_PCT = 0.04
    cfg.COMMISSION_BUFFER_PCT = 0.08
    cfg._DECISION_START_TS = 0.0
    cfg.ENTRY_ISOLATE_FAMILY = "WR_PULLBACK"
    return cfg


def test_wr_switch_changes_actual_simulate_one_open_bar_not_just_score():
    import v12_quick_engine as vq

    npz = _wr_npz()
    on_cfg = _wr_cfg(True)
    off_cfg = _wr_cfg(False)
    on_mask = vq.compute_entry_signals(npz, len(npz["close_15m"]), True, on_cfg)
    off_mask = vq.compute_entry_signals(npz, len(npz["close_15m"]), True, off_cfg)
    assert bool(on_mask[12]) is True
    assert bool(off_mask[12]) is False
    on = vq.simulate_one(npz, "RETAINEDUSDC", True, on_cfg)
    off = vq.simulate_one(npz, "RETAINEDUSDC", True, off_cfg)
    on_rows = [row for row in on["ledger"] if row.get("type") == "OPEN" and row.get("bar") == 12]
    off_rows = [row for row in off["ledger"] if row.get("type") == "OPEN" and row.get("bar") == 12]
    assert on_rows
    assert not off_rows


def test_wr_15m_wt_cross_decoder_opens_without_stoch_turn():
    import v12_quick_engine as vq

    npz = _wr_npz()
    npz["stoch_k_15m_prev"][12] = 20.0
    npz["wt_cross_bull_15m"][12] = 1.0
    cfg = _wr_cfg(True)
    assert bool(vq.compute_entry_signals(npz, len(npz["close_15m"]), True, cfg)[12])
    result = vq.simulate_one(npz, "RETAINEDUSDC", True, cfg)
    assert any(row.get("type") == "OPEN" and row.get("bar") == 12 for row in result["ledger"])
