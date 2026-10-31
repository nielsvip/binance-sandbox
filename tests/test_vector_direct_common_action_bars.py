import pytest
from tools.vector_direct_trader import plan_account

@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_common_eligible_accounts_plan_all_actions_on_same_bars_despite_sizes(side):
    ss = "TESTUSDC_" + side
    accounts = {"ang": {"qty": 0.0, "done": set(), "rows": []}, "inf": {"qty": 0.0, "done": set(), "rows": []}}
    steps = [(1800, True, []), (2700, True, [{"type": "AUGMENT", "qty": 5, "qty_before": 10}]), (3600, True, [{"type": "REDUCE", "qty_frac": 0.4}]), (4500, False, [{"type": "CLOSE"}])]
    for bar, opened, acts in steps:
        state = {"ok": True, "npz_tail": bar, "open": opened, "engine_qty": 10, "engine_open_qty": 10, "acts_bar_ts": bar, "acts_last_bar": acts}
        for account, book in accounts.items():
            before = book["qty"]
            orders, _ = plan_account(account, "crypto", [ss], {ss: state}, {ss: {"qty": before, "price": 1}}, bar, book["done"], lambda a, key: key == ss, prev_qty={ss: before})
            assert len(orders) == 1
            order = orders[0]
            action = order["act"]
            qty = (10 if account == "ang" else 100) if action == "OPEN" else order["qty"]
            book["qty"] = before + qty if action in ("OPEN", "AUGMENT") else before - qty
            book["done"].add(order["key"])
            book["rows"].append((bar, action, qty))
    assert [r[:2] for r in accounts["ang"]["rows"]] == [(1800, "OPEN"), (2700, "AUGMENT"), (3600, "REDUCE"), (4500, "CLOSE")]
    assert [r[:2] for r in accounts["inf"]["rows"]] == [r[:2] for r in accounts["ang"]["rows"]]
    assert all(right[2] == pytest.approx(left[2]*10) for left,right in zip(accounts["ang"]["rows"], accounts["inf"]["rows"]))
    assert accounts["ang"]["qty"] == accounts["inf"]["qty"] == 0
