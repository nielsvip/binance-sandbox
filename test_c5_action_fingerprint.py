from c5_action_fingerprint import exact_action_fingerprint
from tools import param_results_store


def _trade(**updates):
    row = {
        "symbol": "MU",
        "side": "LONG",
        "entry_ts": 1,
        "exit_ts": 2,
        "entry_price": 100.0,
        "exit_price": 110.0,
        "pnl_pct": 9.95,
        "pnl_usd": 99.5,
        "entry_reason": "LADDER",
        "exit_reason": "TOP",
        "requested_open_qty": 10.0,
        "executed_open_qty": 9.0,
        "requested_close_qty": 9.0,
        "executed_close_qty": 9.0,
        "gross_cash_flow": 90.0,
        "net_cash_flow": 89.5,
        "partial_cash_flow": 0.0,
        "partial_close_count": 0,
        "exposure_qty_seconds": 900.0,
        "exposure_notional_seconds": 90000.0,
        "action_events": [
            {
                "timestamp": 1,
                "action": "OPEN",
                "side": "BUY",
                "position_side": "LONG",
                "reason": "LADDER",
                "requested_qty": 10.0,
                "executed_qty": 9.0,
                "price": 100.0,
                "cash_flow": -900.0,
            },
            {
                "timestamp": 2,
                "action": "CLOSE",
                "side": "SELL",
                "position_side": "LONG",
                "reason": "TOP",
                "requested_qty": 9.0,
                "executed_qty": 9.0,
                "price": 110.0,
                "cash_flow": 990.0,
                "is_full_close": True,
            },
        ],
    }
    row.update(updates)
    return row


def test_c5_fingerprint_is_order_stable_and_versioned():
    one = _trade(symbol="MU")
    two = _trade(symbol="NVDA", entry_ts=3, exit_ts=4)
    forward = exact_action_fingerprint([one, two])
    reverse = exact_action_fingerprint([two, one])
    assert forward == reverse
    assert ":exact-actions-v2:" in forward


def test_every_required_execution_dimension_changes_fingerprint():
    baseline = exact_action_fingerprint([_trade()])
    mutations = {
        "requested_open_qty": 11.0,
        "executed_open_qty": 8.0,
        "requested_close_qty": 8.0,
        "executed_close_qty": 8.0,
        "gross_cash_flow": 80.0,
        "net_cash_flow": 79.5,
        "partial_cash_flow": 55.0,
        "partial_close_count": 1,
        "exit_reason": "OTHER_TOP",
        "exposure_qty_seconds": 800.0,
        "exposure_notional_seconds": 80000.0,
    }
    for field, value in mutations.items():
        assert exact_action_fingerprint([_trade(**{field: value})]) != baseline


def test_action_ledger_requested_quantity_changes_fingerprint():
    baseline = _trade()
    changed = _trade(
        action_events=[
            {**baseline["action_events"][0], "requested_qty": 12.0},
            baseline["action_events"][1],
        ]
    )
    assert exact_action_fingerprint([changed]) != exact_action_fingerprint(
        [baseline]
    )


def test_store_requires_explicit_c5_contract_and_preserves_c4_identity():
    trade = _trade()
    assert (
        param_results_store.trades_fingerprint([trade])
        == param_results_store.trades_fingerprint_c4([trade])
    )
    assert ":exact-actions-v2:" in param_results_store.trades_fingerprint(
        [trade], contract_version="tradier-matrix-exec-c5-20260730"
    )
