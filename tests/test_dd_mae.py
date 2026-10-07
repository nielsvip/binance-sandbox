"""DD semantics: realized vs wick-adverse MAE drawdown (USER 2026-10-03).

max_dd_pct  = realized peak-to-trough on peak-concurrent equity (standard).
max_dd_mae  = wick-adverse MTM on the SAME base = max COULD-have-lost.
Invariant: mae >= realized always; mae >= |worst trade|/peak always.
Uncomputable -> None, never 0.0.
"""
import sys
sys.path.insert(0, '.')
from tools.opt.evaluate_v12 import _wick_mae_dd_pct, _honest_max_dd_pct


def _close(be, bx, entry, exitp, qty, pnl, ts0=1000.0):
    return {"type": "CLOSE", "bar_entry": be, "bar_exit": bx, "entry_price": entry,
            "exit_price": exitp, "qty": qty, "pnl_dollars": pnl,
            "deployed": entry * qty, "ts": ts0 + bx}


def _open(bar, price, qty, ts0=1000.0):
    return {"type": "OPEN", "bar": bar, "price": price, "qty": qty, "ts": ts0 + bar}


def test_short_wick_spike_mae_exceeds_realized():
    # SHORT 100 @ 10.0, exits 9.9 (+$10 realized) but wicked to 12.0 mid-hold.
    # Realized DD = 0 (only a winner); MAE DD must see the -$200 excursion.
    led = [_open(0, 10.0, 100), _close(0, 4, 10.0, 9.9, 100, 10.0)]
    n = 6
    hi = [10.0, 12.0, 10.5, 10.0, 9.9, 9.9]
    lo = [9.9] * n
    ts = [1000.0 + i for i in range(n)]
    mae = _wick_mae_dd_pct(led, hi, lo, ts, False, 1000.0)
    assert mae is not None and abs(mae - 20.0) < 1e-9, mae
    assert _honest_max_dd_pct({"ledger": led}) == 0.0


def test_long_mirror():
    led = [_open(0, 10.0, 100), _close(0, 4, 10.0, 10.1, 100, 10.0)]
    n = 6
    lo = [10.0, 8.0, 9.5, 10.0, 10.1, 10.1]
    hi = [10.2] * n
    ts = [1000.0 + i for i in range(n)]
    mae = _wick_mae_dd_pct(led, hi, lo, ts, True, 1000.0)
    assert mae is not None and abs(mae - 20.0) < 1e-9, mae


def test_mae_covers_consecutive_losers():
    # two clean SHORT losers back to back, no wicks: mae == realized == 3%.
    led = [_open(0, 10.0, 100), _close(0, 2, 10.0, 10.1, 100, -10.0),
            _open(3, 10.0, 100), _close(3, 5, 10.0, 10.2, 100, -20.0)]
    n = 7
    hi = [10.0, 10.1, 10.1, 10.0, 10.2, 10.2, 10.2]
    lo = [9.9] * n
    ts = [1000.0 + i for i in range(n)]
    mae = _wick_mae_dd_pct(led, hi, lo, ts, False, 1000.0)
    assert mae is not None and abs(mae - 3.0) < 1e-9, mae


def test_legacy_no_open_events_falls_back():
    led = [_close(0, 2, 10.0, 10.1, 100, -10.0)]
    n = 4
    hi = [10.0, 10.3, 10.1, 10.1]
    lo = [9.9] * n
    ts = [1000.0 + i for i in range(n)]
    mae = _wick_mae_dd_pct(led, hi, lo, ts, False, 1000.0)
    assert mae is not None and abs(mae - 3.0) < 1e-9, mae


def test_uncomputable_is_none():
    led = [_close(0, 2, 10.0, 10.1, 100, -10.0)]
    assert _wick_mae_dd_pct([], [1], [1], [1], False, 100.0) is None
    assert _wick_mae_dd_pct(led, [1], [1], [1], False, 0.0) is None
    assert _wick_mae_dd_pct(led, [], [], [], False, 100.0) is None
    assert _wick_mae_dd_pct([{"type": "OPEN"}], [1], [1], [1], False, 100.0) is None
