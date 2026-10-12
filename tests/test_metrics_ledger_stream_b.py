"""Stream B metrics/ledger fixes: header==closes, closes-only Sharpe, MAE>=realized.

Covers the five Stream B items without touching an NPZ or the live engine:
  (1) header/embedded trade-count fork — PAR/001 realised-closes unit
  (2) Sharpe dilution — pool_sharpe on closes-only via metrics_guard
  (3) H-column verify guards — V8_SKIP refusal + spike parity fail
  (4) provenance record/verify round-trip
  (5) this file (mae>=realized invariant on synthetic ledgers)
"""
import os
import sys

sys.path.insert(0, ".")

from metrics_guard import pool_sharpe, pool_sharpe_from_ledger, realised_closes
from tools.opt.evaluate_v12 import _honest_max_dd_pct, _pool_sharpe_from_ledger, _wick_mae_dd_pct
from tools.opt.hires_chart import _split_ledger
from tools.opt.npz_provenance import record_window_provenance, verify_window_provenance


def _close(pnl_pct, pnl_dollars, seed=False, be=0, bx=1):
    t = {"type": "CLOSE", "bar_entry": be, "bar_exit": bx, "entry_price": 10.0,
         "exit_price": 10.0, "qty": 1.0, "pnl_pct": pnl_pct,
         "pnl_dollars": pnl_dollars, "deployed": 10.0, "ts": 1000.0 + bx}
    if seed:
        t["seeded_prewindow"] = True
    return t


def _event(ty="OPEN"):
    return {"type": ty, "ts": 1001.0, "price": 10.0, "qty": 1.0,
            "deployed": 10.0, "reason": "TEST"}


def test_realised_closes_excludes_events_and_seeds():
    ledger = [_event("OPEN"), _close(1.0, 1.0), _event("AUGMENT"),
              _close(-0.5, -0.5), _event("REDUCE"), _close(9.9, 9.9, seed=True)]
    real = realised_closes(ledger)
    assert len(real) == 2, real
    assert [t["pnl_pct"] for t in real] == [1.0, -0.5]


def test_sharpe_closes_only_no_dilution():
    closes = [_close(1.0, 1.0), _close(-0.5, -0.5), _close(0.5, 0.5),
              _close(0.25, 0.25)]
    full = [_event("OPEN")] + closes[:2] + [_event("AUGMENT")] + closes[2:] + [_event("REDUCE")]
    want = pool_sharpe([1.0, -0.5, 0.5, 0.25])
    assert _pool_sharpe_from_ledger(full) == want
    assert pool_sharpe_from_ledger(full) == want
    diluted = pool_sharpe([1.0, -0.5, 0.5, 0.25, 0.0, 0.0, 0.0])
    assert want != diluted  # proves events would have diluted if included


def test_sharpe_call_through_matches_guard():
    ledger = [_close(2.0, 2.0, be=i, bx=i + 1) for i in range(3)]
    ledger += [_close(-1.0, -1.0, be=9, bx=10)]
    assert _pool_sharpe_from_ledger(ledger) == pool_sharpe_from_ledger(ledger)
    assert _pool_sharpe_from_ledger([]) == 0.0
    assert _pool_sharpe_from_ledger([_event("OPEN")]) == 0.0


def test_split_ledger_header_equals_realised():
    ledger = [_event("OPEN"), _close(1.0, 1.0), _event("AUGMENT"),
              _close(-2.0, -2.0), _close(5.0, 5.0, seed=True)]
    trades, events = _split_ledger(ledger, [1000.0, 1001.0, 1002.0])
    assert len(trades) == 2  # header prints len(trades); seed + events excluded
    assert len(events) == 2
    assert all(t.get("pnl_dollars") is not None for t in trades)


def test_mae_ge_realized_with_augment():
    led = [{"type": "OPEN", "bar": 0, "price": 10.0, "qty": 100.0, "ts": 1000.0},
           {"type": "CLOSE", "bar_entry": 0, "bar_exit": 3, "entry_price": 10.0,
            "exit_price": 10.2, "qty": 100, "pnl_dollars": 20.0,
            "deployed": 1000.0, "ts": 1003.0}]
    n = 5
    lo = [10.0, 9.0, 9.8, 10.2, 10.2]
    hi = [10.1] * n
    ts = [1000.0 + i for i in range(n)]
    mae = _wick_mae_dd_pct(led, hi, lo, ts, True, 1000.0)
    real = _honest_max_dd_pct({"ledger": led, "mean_deployed": 1000.0})
    assert mae is not None and mae >= real, (mae, real)
    assert mae >= 9.9  # $100 wick excursion on $1000 base


def test_mae_ge_realized_multi_trade():
    led = [{"type": "CLOSE", "bar_entry": 0, "bar_exit": 1, "entry_price": 10.0,
            "exit_price": 11.0, "qty": 100, "pnl_dollars": 100.0,
            "deployed": 1000.0, "ts": 1001.0},
           {"type": "CLOSE", "bar_entry": 2, "bar_exit": 3, "entry_price": 10.0,
            "exit_price": 9.5, "qty": 100, "pnl_dollars": -50.0,
            "deployed": 1000.0, "ts": 1003.0}]
    n = 5
    lo = [10.0, 11.0, 9.0, 9.5, 9.5]
    hi = [11.5] * n
    ts = [1000.0 + i for i in range(n)]
    mae = _wick_mae_dd_pct(led, hi, lo, ts, True, 1000.0)
    real = _honest_max_dd_pct({"ledger": led, "mean_deployed": 1000.0})
    assert mae is not None and mae >= real, (mae, real)


def _synth_npz(path, drift_close=None):
    import numpy as np
    n = 10
    ts = np.array([1700000000 + i * 900 for i in range(n)], dtype=np.int64)
    base = np.linspace(100.0, 110.0, n)
    kw = {"timestamps": ts}
    for k in ("open_15m", "high_15m", "low_15m", "close_15m", "volume_15m"):
        kw[k] = (np.array(drift_close, dtype=np.float64) if k == "close_15m" and drift_close is not None else base.copy())
    np.savez(str(path), **kw)


def test_provenance_roundtrip_and_drift(tmp_path):
    import numpy as np
    f = tmp_path / "w.npz"
    _synth_npz(f)
    z = np.load(str(f), allow_pickle=True)
    win = {k: np.asarray(z[k])[2:8] for k in ("timestamps", "open_15m", "high_15m", "low_15m", "close_15m", "volume_15m")}
    rec = record_window_provenance(str(f), win["timestamps"],
                                   {k: win[k] for k in win if k != "timestamps"})
    assert set(rec) == {"npz_mtime_ns", "npz_size", "win_ts_first",
                        "win_ts_last", "win_slice_sha"}
    ok, reason = verify_window_provenance(str(f), rec)
    assert ok, reason
    ok3, _ = verify_window_provenance(str(tmp_path / "gone.npz"), rec)
    assert not ok3
    rec2 = record_window_provenance(str(f), win["timestamps"],
                                    {k: win[k] for k in win if k != "timestamps"})
    assert rec2["win_slice_sha"] == rec["win_slice_sha"]  # deterministic
    _synth_npz(f, drift_close=[100.0, 101.0, 102.0, 103.0, 999.0, 105.0, 106.0, 107.0, 108.0, 110.0])
    ok4, reason4 = verify_window_provenance(str(f), rec)
    assert not ok4 and "drift" in reason4  # value drift fails closed


def test_v8skip_guard_refuses_live():
    old = os.environ.get("V8_SKIP_PROCESS_POSITION")
    os.environ["V8_SKIP_PROCESS_POSITION"] = "1"
    try:
        from v15_pilot import live_evaluate
        res = live_evaluate("BTCUSDC_LONG", {}, 30)
        assert res.get("valid") is False
        assert "V8_SKIP" in str(res.get("invalid_reason"))
        assert res.get("gain_pct") is None  # nothing publishable to H
    finally:
        if old is None:
            os.environ.pop("V8_SKIP_PROCESS_POSITION", None)
        else:
            os.environ["V8_SKIP_PROCESS_POSITION"] = old


def test_parity_spike_fail():
    from v15_pilot import parity_ok
    live = {"valid": True, "gain_pct": 5.0, "trades": 40, "spike_frac": 0.05}
    vec = {"valid": True, "gain_pct": 5.0, "trades": 40, "spike_frac": 0.0}
    ok, reason = parity_ok(live, vec)
    assert not ok and "spike" in reason
    ok2, _ = parity_ok({"valid": True, "gain_pct": 5.0, "trades": 40}, vec)
    assert ok2  # legs without spike fields unaffected
