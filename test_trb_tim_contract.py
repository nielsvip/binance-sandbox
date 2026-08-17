import pytest

from tools import trb_tim_contract as contract


def test_top_ten_and_remainder_bands_follow_current_ranked_files():
    long_symbols = contract.ranked_symbols("LONG")
    short_symbols = contract.ranked_symbols("SHORT")

    assert contract.tim_band(long_symbols[0], "LONG") == (50.0, 80.0)
    assert contract.tim_band(long_symbols[9], "LONG") == (50.0, 80.0)
    assert contract.tim_band(long_symbols[10], "LONG") == (20.0, 60.0)
    assert contract.tim_band(short_symbols[0], "SHORT") == (50.0, 80.0)
    assert contract.tim_band(short_symbols[9], "SHORT") == (50.0, 80.0)
    assert contract.tim_band(short_symbols[10], "SHORT") == (20.0, 60.0)


def test_pilot_contract_follows_runtime_rank_not_a_hardcoded_snapshot():
    for symbol, side in (
        ("MU", "LONG"),
        ("NVDA", "LONG"),
        ("VT", "LONG"),
        ("TTD", "SHORT"),
        ("ACN", "SHORT"),
        ("LAC", "SHORT"),
    ):
        row = contract.tim_contract(symbol, side)
        expected = (50.0, 80.0) if row["rank"] <= 10 else (20.0, 60.0)
        assert contract.tim_band(symbol, side) == expected


def test_missing_tradeable_key_fails_closed():
    with pytest.raises(ValueError, match="TRB_TIM_KEY_NOT_TRADEABLE"):
        contract.tim_band("NOT_A_REAL_TRB_KEY", "LONG")
