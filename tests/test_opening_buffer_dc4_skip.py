"""Open-bleed fix (USER 2026-10-09): intraday-TF DC4 stops skip the opening buffer."""

import tradier_manage as TM


def test_dc4_intraday_classifier():
    assert TM._dc4_intraday_skip_in_buffer("15m") is True
    assert TM._dc4_intraday_skip_in_buffer("1h") is True
    assert TM._dc4_intraday_skip_in_buffer("5m") is True
    assert TM._dc4_intraday_skip_in_buffer(" 15M ") is True
    assert TM._dc4_intraday_skip_in_buffer("4h") is False
    assert TM._dc4_intraday_skip_in_buffer("D") is False
    assert TM._dc4_intraday_skip_in_buffer("daily") is False
    assert TM._dc4_intraday_skip_in_buffer("") is True
    assert TM._dc4_intraday_skip_in_buffer(None) is True


def test_backtest_never_skips(monkeypatch):
    monkeypatch.setenv("V12_PARITY_MODE", "1")
    assert TM._dc4_intraday_skip_in_buffer("15m") is False
    assert TM._dc4_intraday_skip_in_buffer("") is False


def test_opening_buffer_disabled_path():
    assert TM.in_opening_buffer(min_minutes=0) == (False, 0.0)
    assert TM.in_opening_buffer(min_minutes=-5) == (False, 0.0)


def test_both_dc4_sites_honor_skip():
    import config_tradier

    src = open(TM.__file__).read()
    assert src.count("_dc4_intraday_skip_in_buffer(_vg_tf)") == 2
    assert src.count("OPENING_BUFFER_DC4_INTRADAY_SKIP_ENABLED") == 2
    assert config_tradier.TradierConfig.OPENING_BUFFER_DC4_INTRADAY_SKIP_ENABLED is True
