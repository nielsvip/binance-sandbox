"""DD semantics: realized vs wick-adverse MAE drawdown (USER 2026-10-03).

max_dd_pct  = realized peak-to-trough on peak-concurrent equity (standard).
max_dd_mae  = wick-adverse MTM on the SAME base = max COULD-have-lost.
Invariant: mae >= realized always; mae >= |worst trade|/peak always.
Uncomputable -> None, never 0.0.
"""
import sys
sys.path.insert(0, '.')
import pytest
from tools.opt.evaluate_v12 import _wick_mae_dd_pct, _honest_max_dd_pct, _peak_concurrent


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


def _ev(ty, bar, price, qty):
    return {"type": ty, "bar": bar, "price": price, "qty": qty, "ts": 1000.0 + bar}


def _red_trade(be, bx, entry, exitp, pnl):
    return {"type": "REDUCE", "bar_entry": be, "bar_exit": bx, "entry_price": entry,
            "exit_price": exitp, "qty": 0.0, "pnl_dollars": pnl,
            "deployed": entry * 100.0, "ts": 1000.0 + bx}


def test_fee_drift_cannot_invert_augment_reduce():
    # E1 M1: row pnl is net of fees; the gross re-accrual rode $fees high and
    # inverted mae>=realized (BAT $6.56 on $211.88 cap). Two LONG losers with
    # AUGMENT+REDUCE legs, flat prices (no wick margin to hide behind).
    led = []
    for k in (0, 4):
        led += [_ev("OPEN", k, 10.0, 100.0), _ev("AUGMENT", k + 1, 10.0, 100.0),
                _ev("REDUCE", k + 2, 9.9, 100.0),
                _close(k, k + 3, 10.0, 9.9, 100.0, -22.0)]
    # gross per trade: reduce leg -10 + final leg -10 = -20; row charges -$2 fees.
    n = 9
    lo = [10.0, 10.0, 9.9, 9.9, 10.0, 10.0, 9.9, 9.9, 9.9]
    hi = [10.0] * n
    ts = [1000.0 + i for i in range(n)]
    cap = _peak_concurrent(led)
    assert cap == 2000.0
    mae = _wick_mae_dd_pct(led, hi, lo, ts, True, cap)
    real = _honest_max_dd_pct({"ledger": led})
    assert mae is not None and mae == pytest.approx(real, abs=1e-9), (mae, real)
    assert real == pytest.approx(2.2, abs=1e-9), real


def test_reduce_typed_full_close_accrues():
    # E1 M2: engine full closes typed REDUCE (pnl+bars, qty=0) were swallowed
    # as no-op scaling events — realized accrued, MAE never did (fork shape).
    led = [_ev("OPEN", 0, 10.0, 100.0), _red_trade(0, 2, 10.0, 9.5, -50.0),
           _ev("OPEN", 3, 9.5, 100.0), _red_trade(3, 5, 9.5, 9.2, -30.0)]
    n = 7
    lo = [10.0, 10.0, 9.5, 9.5, 9.5, 9.2, 9.2]
    hi = [10.1] * n
    ts = [1000.0 + i for i in range(n)]
    cap = _peak_concurrent(led)
    assert cap == 1000.0
    mae = _wick_mae_dd_pct(led, hi, lo, ts, True, cap)
    real = _honest_max_dd_pct({"ledger": led})
    assert mae is not None and mae == pytest.approx(real, abs=1e-9), (mae, real)
    assert real == pytest.approx(8.0, abs=1e-9), real


def test_stray_augment_while_flat_invents_nothing():
    # E1: AUGMENT with no open position is a missing-OPEN artefact — the old
    # code materialised a full phantom position (25% DD on flat data here).
    # (Cap fidelity for stray events is _peak_concurrent's lane, untouched.)
    led = [_ev("OPEN", 0, 10.0, 1.0), _close(0, 1, 10.0, 10.0, 1.0, 0.0),
           _ev("AUGMENT", 2, 12.0, 1000.0),
           _close(2, 3, 10.0, 9.9, 1.0, -0.1)]
    n = 5
    lo = [10.0, 10.0, 9.0, 9.9, 9.9]
    hi = [12.0] * n
    ts = [1000.0 + i for i in range(n)]
    cap = _peak_concurrent(led)
    mae = _wick_mae_dd_pct(led, hi, lo, ts, True, cap)
    real = _honest_max_dd_pct({"ledger": led})
    assert mae is not None and mae == pytest.approx(real, abs=1e-9), (mae, real)


def test_same_bar_multi_close_all_accrue():
    # E1: two closes sharing one exit bar — the old code reset qty after the
    # first and silently dropped the second leg from the MAE curve.
    led = [_close(0, 2, 10.0, 9.7, 100.0, -30.0),
           _close(1, 2, 10.0, 9.8, 100.0, -20.0)]
    n = 4
    lo = [10.0, 10.0, 9.7, 9.7]
    hi = [10.0] * n
    ts = [1000.0 + i for i in range(n)]
    cap = _peak_concurrent(led)
    mae = _wick_mae_dd_pct(led, hi, lo, ts, True, cap)
    real = _honest_max_dd_pct({"ledger": led})
    assert mae is not None and mae == pytest.approx(real, abs=1e-9), (mae, real)
    assert real == pytest.approx(2.5, abs=1e-9), real


def test_mae_ge_realized_random_augment_reduce_ledgers():
    # E1 property: random augment/reduce/fee ledgers — mae >= realized always.
    import random
    rng = random.Random(7)
    for _case in range(25):
        led = []
        lo = [10.0] * 40
        hi = [10.0] * 40
        b = 0
        for _t in range(rng.randint(1, 4)):
            if b + 4 >= 40:
                break
            be = b
            px = 10.0
            q = float(rng.choice([10, 50, 100]))
            led.append(_ev("OPEN", be, px, q))
            avg = px
            qty = q
            gross = 0.0
            for j in range(1, 4):
                lo[be + j] = round(px + rng.uniform(-0.4, 0.4), 2)
                hi[be + j] = round(px + rng.uniform(-0.4, 0.4), 2)
                r = rng.random()
                if r < 0.35:
                    aq = float(rng.choice([10, 50]))
                    ap = round(px + rng.uniform(-0.2, 0.2), 2)
                    led.append(_ev("AUGMENT", be + j, ap, aq))
                    avg = (avg * qty + ap * aq) / (qty + aq)
                    qty += aq
                elif r < 0.6 and qty > 1:
                    rq = round(qty * rng.choice([0.25, 0.5]), 2)
                    rp = round(px + rng.uniform(-0.3, 0.3), 2)
                    led.append(_ev("REDUCE", be + j, rp, rq))
                    gross += (rp - avg) * rq
                    qty -= rq
            bx = be + 3
            xp = round(px + rng.uniform(-0.5, 0.5), 2)
            gross += (xp - avg) * qty
            fee = round(abs(gross) * 0.02 + 0.5, 2)
            led.append(_close(be, bx, px, xp, qty, round(gross - fee, 2)))
            lo[bx] = min(lo[bx], xp)
            hi[bx] = max(hi[bx], xp)
            b = bx + 1
        ts = [1000.0 + i for i in range(40)]
        cap = _peak_concurrent(led)
        if cap <= 0:
            continue
        mae = _wick_mae_dd_pct(led, hi, lo, ts, True, cap)
        real = _honest_max_dd_pct({"ledger": led})
        assert mae is not None and mae + 1e-9 >= real, (mae, real)
