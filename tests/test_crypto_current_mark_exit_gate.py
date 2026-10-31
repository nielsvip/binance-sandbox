import pytest

from vec_decisions.net_exit_gate import vector_exit_event_allowed

@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("kind", ["CLOSE", "REDUCE"])
@pytest.mark.parametrize("lifecycle_pnl", [-100., 100.])
def test_current_mark_fee_floor_does_not_use_prior_realized_profit(is_long, kind, lifecycle_pnl):
    position = {"avg_price": 100., "entry_price": 90. if is_long else 110.}
    for gain, allowed in [(-1., False), (0., False), (0.05, False), (0.1, True)]:
        price = 100. * (1. + gain / 100.) if is_long else 100. * (1. - gain / 100.)
        event = {"type": kind, "exit_reason": "ORDINARY_EXIT", "price": price, "exit_price": price, "qty": 1., "pnl_dollars": lifecycle_pnl}
        assert vector_exit_event_allowed(event, position, None, is_long=is_long, current_mark_fee_pct=0.08) is allowed

def test_mandatory_frozen_stop_remains_loss_close_and_missing_basis_fails():
    event = {"type": "CLOSE", "exit_reason": "ORDINARY_EXIT", "exit_price": 95., "pnl_dollars": 200.}
    assert not vector_exit_event_allowed(event, None, None, current_mark_fee_pct=0.08)
    event.update(exit_reason="FIXED_STOP", mandatory_stop_kind="DC15_FROZEN_DEFAULT")
    assert vector_exit_event_allowed(event, {"avg_price": 100.}, None, current_mark_fee_pct=0.08)


def test_actual_engine_rejects_red_mark_after_profitable_partial_close(monkeypatch):
    import numpy as np
    import v12_quick_engine as engine
    from vec_decisions import live_exit_chain
    from tests.test_quick_srs_paired_15m import _cfg, _npz
    data = _npz()
    prices = np.full(len(data["timestamps"]), 101.)
    prices[:3] = [100., 100.9, 99.9]
    for key in ("close", "close_15m", "open_15m", "high_15m", "low_15m"):
        data[key] = prices.copy()
    cfg = _cfg()
    for key in ("STRUCTURAL_RANGE_SHIFT_EXIT", "HTF_TREND_VETO_ON_REDUCE_ENABLED", "OPTIMAL_HOLD_BARS_EXIT_ENABLED", "PARTIAL_PROFIT_LOCK_ENABLED", "CYCLE_TP_TIERED_ENABLED", "QUICK_CYCLE_TP_ENABLED", "WT_WINNER_PULLBACK_AUGMENT_ENABLED"):
        setattr(cfg, key, False)
    cfg.COMMISSION_BUFFER_PCT = 0.08
    def produce(prepared, config, index, price, state):
        if index == 1:
            return "TEST_PROFITABLE_PARTIAL", False
        if index in (2, 3):
            return "TEST_ORDINARY_CLOSE", True
        return None
    monkeypatch.setattr(live_exit_chain, "step", produce)
    result = engine.simulate_one(data, "CRYPTO_MARK_FEE_TEST", True, cfg, force_initial_seed=True)
    reductions = [row for row in result["ledger"] if row.get("type") == "REDUCE" and row.get("reason") == "TEST_PROFITABLE_PARTIAL"]
    closes = [row for row in result["ledger"] if row.get("type") == "CLOSE" and not row.get("unrealised")]
    assert len(reductions) == 1 and reductions[0]["bar"] == 1
    assert len(closes) == 1
    assert closes[0].get("bar_exit", closes[0].get("bar")) == 3
    assert closes[0].get("exit_reason", closes[0].get("reason")) == "TEST_ORDINARY_CLOSE"
