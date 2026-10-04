"""BB band-touch + stoch-cross-3m live-twin parity (2026-10-04 wiring mandate).

Proves vec_decisions.bb_stoch_exits reproduces v12_quick_engine
compute_exit_signals semantics exactly (EXIT_STRUCTURAL).
"""
import vec_decisions.bb_stoch_exits as X


def _get(d):
    return lambda k, default: d.get(k, default)


def test_off_inert():
    assert X.bb_band_exits(_get({}), True, {}) == (False, "")
    assert X.stoch_cross_3m_exit(_get({}), True, {}) == (False, "")


def test_tf_parse():
    assert X.resolve_bb_band_tf("15m") == "15m"
    assert X.resolve_bb_band_tf("3m") == "15m"
    assert X.resolve_bb_band_tf("5m") == "15m"
    assert X.resolve_bb_band_tf("OFF") is None
    assert X.resolve_bb_band_tf("15m,1h") is None
    assert X.resolve_bb_band_tf("") is None


def test_bb_loss():
    ind = {"bb_pct_b_15m": 0.03, "bb_lower_15m": 100.0, "bb_upper_15m": 110.0}
    assert X.bb_band_exits(_get({"BB_EXIT_AT_LOSS_TF": "15m"}), True, ind) == (True, "BB_LOSS_15m")
    assert X.bb_band_exits(_get({"BB_EXIT_AT_LOSS_TF": "15m"}), False, ind) == (False, "")
    ind2 = {"bb_pct_b_1h": 0.97, "bb_lower_1h": 100.0, "bb_upper_1h": 110.0}
    assert X.bb_band_exits(_get({"BB_EXIT_AT_LOSS_TF": "1h"}), False, ind2) == (True, "BB_LOSS_1h")
    assert X.bb_band_exits(_get({"BB_EXIT_AT_LOSS_TF": "1h"}), True, ind2) == (False, "")


def test_bb_take():
    ind = {"bb_pct_b_15m": 0.97, "bb_lower_15m": 100.0, "bb_upper_15m": 110.0}
    assert X.bb_band_exits(_get({"BB_PROFIT_TAKE_TF": "15m"}), True, ind) == (True, "BB_TAKE_15m")
    assert X.bb_band_exits(_get({"BB_PROFIT_TAKE_TF": "15m"}), False, ind) == (False, "")
    ind2 = {"bb_pct_b_4h": 0.02, "bb_lower_4h": 100.0, "bb_upper_4h": 110.0}
    assert X.bb_band_exits(_get({"BB_PROFIT_TAKE_TF": "4h"}), False, ind2) == (True, "BB_TAKE_4h")


def test_bb_requires_level():
    ind = {"bb_pct_b_15m": 0.01, "bb_lower_15m": 0.0, "bb_upper_15m": 0.0}
    assert X.bb_band_exits(_get({"BB_EXIT_AT_LOSS_TF": "15m"}), True, ind) == (False, "")


def test_stoch_cross_long():
    ind = {"k_3m": 65.0, "d_3m": 70.0, "k_3m_prev": 72.0}
    assert X.stoch_cross_3m_exit(_get({"STOCH_CROSS_3M_EXIT_ENABLED": True}), True, ind) == (True, "STOCH_CROSS_3M_EXIT")
    ind2 = {"k_3m": 65.0, "d_3m": 70.0, "k_3m_prev": 68.0}
    assert X.stoch_cross_3m_exit(_get({"STOCH_CROSS_3M_EXIT_ENABLED": True}), True, ind2) == (False, "")
    ind3 = {"k_3m": 55.0, "d_3m": 70.0, "k_3m_prev": 72.0}
    assert X.stoch_cross_3m_exit(_get({"STOCH_CROSS_3M_EXIT_ENABLED": True}), True, ind3) == (False, "")


def test_stoch_cross_short():
    ind = {"k_3m": 35.0, "d_3m": 30.0, "k_3m_prev": 28.0}
    assert X.stoch_cross_3m_exit(_get({"STOCH_CROSS_3M_EXIT_ENABLED": True}), False, ind) == (True, "STOCH_CROSS_3M_EXIT")
    ind2 = {"k_3m": 35.0, "d_3m": 30.0, "k_3m_prev": 32.0}
    assert X.stoch_cross_3m_exit(_get({"STOCH_CROSS_3M_EXIT_ENABLED": True}), False, ind2) == (False, "")
    ind3 = {"k_3m": 45.0, "d_3m": 30.0, "k_3m_prev": 28.0}
    assert X.stoch_cross_3m_exit(_get({"STOCH_CROSS_3M_EXIT_ENABLED": True}), False, ind3) == (False, "")


def test_stoch_cross_no_prev_no_fire():
    ind = {"k_3m": 65.0, "d_3m": 70.0}
    assert X.stoch_cross_3m_exit(_get({"STOCH_CROSS_3M_EXIT_ENABLED": True}), True, ind) == (False, "")
