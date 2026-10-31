from vec_decisions.ez_missing_b04_retest import tradier_flat_open_proposal


def test_tradier_b04_stock_proposal_on_frozen_aapl_long_bar():
    indicators = {
        "dc_high_4h": 348.14990234375,
        "dc_high_4h_ant": 342.9880065917969,
        "k_15m": 56.44818878173828,
        "d_15m": 36.071922302246094,
        "k_15m_prev": 51.01660919189453,
    }
    assert tradier_flat_open_proposal(indicators, 343.1199951171875, True, True, 600) == (
        "B04_DC_RETEST_DC_HIGH_RETEST_4H_bp=1.5%_level=342.988007_mult=2.0x",
        88.0,
        3,
    )


def test_tradier_b04_stock_proposal_on_frozen_aapl_short_bar():
    indicators = {
        "dc_low_1h": 334.3500061035156,
        "dc_low_1h_ant": 341.0299987792969,
        "k_15m": 24.767309188842773,
        "d_15m": 40.00799560546875,
        "k_15m_prev": 31.404542922973633,
    }
    assert tradier_flat_open_proposal(indicators, 343.1341857910156, False, True, 600) == (
        "B04_DC_RETEST_DC_LOW_RETEST_1H_bp=2.0%_level=341.029999_mult=1.5x",
        88.0,
        2,
    )
    assert tradier_flat_open_proposal(indicators, 343.1341857910156, False, False, 600) is None
