import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location("actual_profit_guard", Path(__file__).resolve().parents[1] / "tools/vector_direct_trader.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("symbol_side,entry,price", [("BLURUSDT_SHORT", .007955, .0081368703), ("BLURUSDT_SHORT", .007955, .008098), ("WLDUSDC_SHORT", .4127487603, .4137), ("WLDUSDC_SHORT", .4127487603, .416), ("PENGUUSDC_LONG", .007971, .007969)])
def test_actual_loss_is_rejected_despite_modeled_profit(symbol_side, entry, price):
    allowed, evidence = module.actual_reduction_is_profitable(symbol_side, {"qty": 10, "entry": entry}, price)
    assert allowed is False
    assert evidence["gain_per_unit_before_exit_fee"] < 0


def test_break_even_and_stock_cost_basis_are_respected():
    assert module.actual_reduction_is_profitable("X_LONG", {"qty": 2, "entry": 100, "break_even": 100.1}, 100.05)[0] is False
    assert module.actual_reduction_is_profitable("X_SHORT", {"qty": 2, "cost": 200}, 99)[0] is True
    assert module.actual_reduction_is_profitable("X_LONG", {"qty": 2}, 101)[0] is False


def test_send_rechecks_broker_position_and_does_not_finalize_refusal(tmp_path):
    runner = module.Trader.__new__(module.Trader)
    runner.venue = "crypto"
    runner.out = tmp_path
    runner.a = SimpleNamespace(dry_run=True)
    runner.done, runner.quiet = set(), set()
    runner.filled_open_anchors = set()
    rows, calls = [], []
    runner._ledger = rows.append
    runner.alert = lambda *args, **kwargs: None
    runner.broker = SimpleNamespace(positions=lambda account: calls.append("positions") or {"BLURUSDT_SHORT": {"qty": 265, "entry": .007955}}, price=lambda symbol: calls.append("price") or .0081368703, size=lambda order, held, price: (100, ""), send=lambda *args: pytest.fail("losing reduction reached broker"))
    order = {"acct": "ang", "ss": "BLURUSDT_SHORT", "act": "REDUCE", "qty": 100, "key": "real-case", "why": "CYCLE_TP_TIERED_T1_gain1.47"}
    runner.send_orders("ang", [order], {"BLURUSDT_SHORT": {"qty": 265, "entry": .008258, "price": .008}}, False)
    assert calls == ["positions", "price"]
    assert rows[-1]["status"] == "REFUSED_NONPROFIT_REDUCE"
    assert rows[-1]["final"] is False
    assert order["key"] not in runner.done


@pytest.mark.parametrize("side,mark,level", [("LONG", 94, 95), ("SHORT", 106, 105)])
def test_only_bound_crossed_fixed_stop_authorizes_loss_close(side, mark, level):
    position = {"qty": 2, "entry": 100, "price": mark, "position_source": "mac_snapshot_broker_verified", "mac": {"entry_price": 100, "opened_at": "2026-10-31T00:00:00Z", "fixed_stop_anchor": 1793404800, "fixed_stop_price": level, "fixed_stop_armed": True}}
    assert module.verified_fixed_stop_trigger("X_" + side, position)["kind"] == "LIFECYCLE_BOUND_FIXED_STOP_CROSSED"
    position["mac"]["fixed_stop_anchor"] -= 900
    assert module.verified_fixed_stop_trigger("X_" + side, position) is None
    position["mac"]["fixed_stop_anchor"] += 900
    position["mac"]["entry_price"] = 99
    assert module.verified_fixed_stop_trigger("X_" + side, position) is None


def test_stop_reason_or_uncrossed_level_cannot_authorize_loss():
    position = {"qty": 2, "entry": 100, "price": 97, "position_source": "mac_snapshot_broker_verified", "mac": {"entry_price": 100, "opened_at": "2026-10-31T00:00:00Z", "fixed_stop_anchor": 1793404800, "fixed_stop_price": 95, "fixed_stop_armed": True}, "reason": "FIXED_STOP"}
    assert module.verified_fixed_stop_trigger("X_LONG", position) is None
    position["price"] = 94
    position["position_source"] = "broker_verified_mac_quantity_mismatch"
    assert module.verified_fixed_stop_trigger("X_LONG", position) is None


def test_only_current_bar_against_wt_authorizes_deterioration_loss_close():
    bar = 1793132100
    now = bar + 910
    ss = "XUSDT_LONG"
    order = {"ss": ss, "act": "CLOSE", "deterioration_wt15_bar": bar}
    against = {"bar_ts": bar, "wt1_15m": -2.0, "wt2_15m": 1.0, "src": "tail"}
    auth = module.verified_deterioration_wt15_trigger(order, "crypto", {ss}, against, now)
    assert auth["kind"] == "CURRENT_BAR_WT15_AGAINST_HALTED_SIDE"
    assert module.verified_deterioration_wt15_trigger(order, "crypto", set(), against, now) is None
    assert module.verified_deterioration_wt15_trigger(order, "stocks", {ss}, against, now) is None
    assert module.verified_deterioration_wt15_trigger(order, "crypto", {ss}, {**against, "bar_ts": bar - 900}, now) is None
    assert module.verified_deterioration_wt15_trigger(order, "crypto", {ss}, {**against, "bar_ts": bar + 900}, now) is None
    assert module.verified_deterioration_wt15_trigger(order, "crypto", {ss}, {**against, "wt1_15m": 2.0}, now) is None
    assert module.verified_deterioration_wt15_trigger({**order, "deterioration_wt15_bar": bar - 900}, "crypto", {ss}, against, now) is None
