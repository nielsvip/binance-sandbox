"""Peak-concurrent capital base: event-level signed replay (USER 2026-10-03).

Trade `deployed` sums fills and never subtracts REDUCEs — an augment-scalp
trade churning $511 gross on $254 max open reported $511 'concurrent',
shrinking every gain/DD % by up to 1.57x (GALAUSDT_SHORT proof).
_peak_concurrent must replay OPEN/AUGMENT/REDUCE/CLOSE and report max open.
"""
import sys
sys.path.insert(0, '.')
from tools.opt.evaluate_v12 import _peak_concurrent


def test_augment_reduce_churn_reports_max_open():
    # OPEN 100 @ 10 ($1000), AUGMENT 50 @ 10 ($500), REDUCE 50 @ 10, CLOSE.
    # Fill-sum deployed would say $1500; true max open is $1500 here too —
    # then REDUCE drops to $1000. Peak must be $1500, not fill-sum $1500...
    # use asymmetric prices to separate: AUGMENT @ 12, REDUCE @ 12.
    led = [
        {"type": "OPEN", "ts": 1.0, "price": 10.0, "qty": 100.0},
        {"type": "AUGMENT", "ts": 2.0, "price": 12.0, "qty": 50.0},
        {"type": "REDUCE", "ts": 3.0, "price": 12.0, "qty": 50.0},
        {"type": "CLOSE", "ts": 4.0, "pnl_dollars": 0.0, "deployed": 1500.0,
         "bar_entry": 0, "bar_exit": 4},
    ]
    assert _peak_concurrent(led) == 1800.0  # 150 x $12, not fill-sum


def test_reduce_before_second_augment_caps_peak():
    # OPEN 100 @ 10, REDUCE 100 @ 10 (flat), AUGMENT-like reopen is a new
    # trade in practice; here: OPEN 100 @10, REDUCE 60 @10, AUGMENT 60 @10.
    # Max open $1000 despite $1600 gross fills.
    led = [
        {"type": "OPEN", "ts": 1.0, "price": 10.0, "qty": 100.0},
        {"type": "REDUCE", "ts": 2.0, "price": 10.0, "qty": 60.0},
        {"type": "AUGMENT", "ts": 3.0, "price": 10.0, "qty": 60.0},
        {"type": "CLOSE", "ts": 4.0, "pnl_dollars": 0.0, "deployed": 1600.0,
         "bar_entry": 0, "bar_exit": 4},
    ]
    assert _peak_concurrent(led) == 1000.0


def test_close_resets_between_trades():
    led = [
        {"type": "OPEN", "ts": 1.0, "price": 10.0, "qty": 100.0},
        {"type": "CLOSE", "ts": 2.0, "pnl_dollars": 5.0, "deployed": 1000.0,
         "bar_entry": 0, "bar_exit": 2},
        {"type": "OPEN", "ts": 3.0, "price": 20.0, "qty": 100.0},
        {"type": "CLOSE", "ts": 4.0, "pnl_dollars": 5.0, "deployed": 2000.0,
         "bar_entry": 3, "bar_exit": 4},
    ]
    assert _peak_concurrent(led) == 2000.0


def test_legacy_no_events_falls_back_to_window_sweep():
    led = [
        {"pnl_dollars": 5.0, "deployed": 1000.0, "bar_entry": 0, "bar_exit": 2},
        {"pnl_dollars": 5.0, "deployed": 2000.0, "bar_entry": 5, "bar_exit": 7},
    ]
    assert _peak_concurrent(led) == 2000.0


def test_empty_and_garbage():
    assert _peak_concurrent([]) == 0.0
    assert _peak_concurrent(None) == 0.0
    assert _peak_concurrent([{"type": "OPEN", "price": 0, "qty": 0}]) == 0.0
