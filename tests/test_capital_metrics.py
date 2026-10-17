"""2026-10-17 USER: headline = $ gain / max $ invested; capital_metrics shows what sizing did."""
from tools.opt.evaluate_v12 import capital_metrics, _peak_concurrent


def _led():
    # trade A: 100 @ 10 ($1000), augment 50 @ 12 (+$600 → $1600), close +$160 ; trade B: 50 @ 10 ($500), close -$25
    return [
        {"type": "OPEN", "ts": 1, "bar": 0, "price": 10.0, "qty": 100.0},
        {"type": "AUGMENT", "ts": 2, "bar": 2, "price": 12.0, "qty": 50.0},
        {"type": "CLOSE", "ts": 3, "bar_exit": 4, "pnl_dollars": 160.0, "deployed": 1600.0, "bar_entry": 0},
        {"type": "OPEN", "ts": 4, "bar": 6, "price": 10.0, "qty": 50.0},
        {"type": "CLOSE", "ts": 5, "bar_exit": 8, "pnl_dollars": -25.0, "deployed": 500.0, "bar_entry": 6},
    ]


def test_capital_metrics_hand_computed():
    led = _led()
    assert _peak_concurrent(led) == 1600.0
    m = capital_metrics(led)
    assert m["net_gain_dollars"] == 135.0
    assert m["max_invested_dollars"] == 1600.0
    assert abs(m["flat_gain_pct"] - (160 / 1600 * 100 - 25 / 500 * 100)) < 1e-9  # 10% - 5% = 5%
    assert abs(m["sizing_lift_pct"] - (135 / 1600 * 100 - 5.0)) < 1e-9  # 8.4375 - 5 = +3.4375 (bigger size went to the winner)
    # avg invested over open bars: bars 0-2 @1000, 2-4 @1600, 6-8 @500 → (2000+3200+1000)/6
    assert abs(m["avg_invested_dollars"] - 6200 / 6) < 1e-6
    assert m["lifecycles"] == 2


def test_partial_reduce_counts_toward_its_trade():
    led = [
        {"type": "OPEN", "ts": 1, "bar": 0, "price": 10.0, "qty": 100.0},
        {"type": "REDUCE", "ts": 2, "bar": 1, "price": 10.2, "qty": 50.0, "pnl_dollars": 10.0},
        {"type": "CLOSE", "ts": 3, "bar_exit": 2, "pnl_dollars": 5.0, "deployed": 1000.0, "bar_entry": 0},
    ]
    m = capital_metrics(led)
    assert m["lifecycles"] == 1 and abs(m["flat_gain_pct"] - 1.5) < 1e-9  # $15 on $1000 peak
