import pytest
from vec_decisions.fixed_stop import build_stop_params, exchange_matches_software, breached


@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("soft,tick", [(0.004765157187357545, 0.0000001), (101.23456, 0.01), (71000.123, 0.1)])
def test_rounded_exchange_stop_preserves_exact_software_touch(is_long, soft, tick):
    exchange = float(build_stop_params(is_long, soft, tick)["stopPrice"])
    assert exchange_matches_software(is_long, soft, exchange, tick)
    assert breached(is_long, soft, soft)
    assert not exchange_matches_software(is_long, soft * 1.02, exchange, tick)


@pytest.mark.parametrize("tick", [0, None, float("nan")])
def test_unknown_tick_does_not_claim_exchange_equivalence(tick):
    assert not exchange_matches_software(True, 100.0, 99.85, tick)
