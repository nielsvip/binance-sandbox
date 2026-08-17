import ast
from pathlib import Path

import pytest

from backtest_v8_harness import accumulate_partial_close
from c5_action_fingerprint import exact_action_fingerprint


ROOT = Path(__file__).resolve().parent


def _reconstruct(events):
    tree = ast.parse((ROOT / "backtest_v8_engine.py").read_text())
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_reconstruct_chart_trades"
    )
    namespace = {
        "datetime": __import__("datetime").datetime,
        "_round_trip_cost_for_sym": lambda _symbol: 0.05,
        "accumulate_partial_close": accumulate_partial_close,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<test>", "exec"), namespace)
    return namespace["_reconstruct_chart_trades"](events)["MU"][0]


def _event(ts, action, qty, price, *, full=False, requested=None):
    side = "BUY" if action in {"OPEN", "AUGMENT"} else "SELL"
    return {
        "timestamp": ts,
        "type": "eta",
        "position_key": "trb:MU_LONG",
        "position_side": "LONG",
        "side": side,
        "action": action,
        "quantity": qty,
        "executed_qty": qty,
        "requested_qty": qty if requested is None else requested,
        "price": price,
        "reason": action,
        "is_full_close": full,
    }


def test_reconstruction_carries_quantity_partial_cash_and_exposure():
    trade = _reconstruct(
        [
            _event(0, "OPEN", 10, 100, requested=12),
            _event(10, "REDUCE", 4, 110),
            _event(20, "CLOSE", 6, 120, full=True),
        ]
    )
    assert trade["requested_open_qty"] == 12
    assert trade["executed_open_qty"] == 10
    assert trade["requested_close_qty"] == 10
    assert trade["executed_close_qty"] == 10
    assert trade["partial_close_count"] == 1
    assert trade["partial_cash_flow"] == 440
    assert trade["gross_cash_flow"] == 160
    assert trade["exposure_qty_seconds"] == 160
    assert trade["exposure_notional_seconds"] == 16600
    assert [event["action"] for event in trade["action_events"]] == [
        "OPEN",
        "REDUCE",
        "CLOSE",
    ]


def test_ladder_multiplier_quantity_has_differential_proof():
    small = _reconstruct(
        [
            _event(0, "OPEN", 2, 100),
            _event(10, "CLOSE", 2, 110, full=True),
        ]
    )
    large = _reconstruct(
        [
            _event(0, "OPEN", 10, 100),
            _event(10, "CLOSE", 10, 110, full=True),
        ]
    )
    assert small["entry_ts"] == large["entry_ts"]
    assert small["exit_ts"] == large["exit_ts"]
    assert small["pnl_pct"] == pytest.approx(large["pnl_pct"])
    assert exact_action_fingerprint([small]) != exact_action_fingerprint(
        [large]
    )


def test_c5_shadow_import_is_contract_gated():
    source = (ROOT / "backtest_v8_engine.py").read_text()
    assert 'V8_C5_SHADOW_PATH' in source
    assert '"tradier-matrix-exec-c5"' in source
    assert "and _C5_SHADOW_PATH" in source
