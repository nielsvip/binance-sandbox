"""USER 2026-10-12 dispatch gate: no v15_pilot starts or is dispatched unless its NPZ
is complete to the latest bar (crypto: last closed 15m; stocks: last RTH close).

Covers tools/npz_cutoffs.tail_currency (single source used by the worker-side
ready probe, the S1 hold-triage, and these pins) + the scheduler wiring (worker
probe compiles and enforces the tail check; ok-cache short enough that currency
cannot decay 12 bars; tail-holds carry S1 triage).
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import v15_fleet_scheduler as Sched
from npz_cutoffs import last_closed_bar, last_rth_close_bar, tail_currency

TUE_RTH = dt.datetime(2026, 10, 13, 14, 0, tzinfo=dt.timezone.utc)  # Tue 10:00 ET, live RTH
SUN = dt.datetime(2026, 10, 11, 12, 0, tzinfo=dt.timezone.utc)  # Sunday noon UTC


def test_crypto_tail_currency():
    cutoff = last_closed_bar(int(TUE_RTH.timestamp()))
    assert tail_currency(cutoff, "crypto", TUE_RTH)[0] is True
    assert tail_currency(cutoff - 2 * 900, "crypto", TUE_RTH)[0] is True  # grace: 2 bars
    ok, why = tail_currency(cutoff - 3 * 900, "crypto", TUE_RTH)
    assert ok is False and "bars behind" in why


def test_stocks_tail_currency_rth_day():
    cutoff = last_rth_close_bar(TUE_RTH)
    assert tail_currency(cutoff, "stocks", TUE_RTH)[0] is True
    assert tail_currency(cutoff - 900, "stocks", TUE_RTH)[0] is True  # 1-bar grace
    ok, why = tail_currency(cutoff - 2 * 900, "stocks", TUE_RTH)
    assert ok is False and "behind RTH close" in why


def test_stocks_weekend_expects_friday_close():
    cutoff = last_rth_close_bar(SUN)
    fri_close = dt.datetime(2026, 10, 9, 16, 0, tzinfo=Sched.NY).timestamp()
    assert cutoff == int(fri_close)
    assert tail_currency(cutoff, "stocks", SUN)[0] is True
    thu_close = cutoff - 86400
    assert tail_currency(thu_close, "stocks", SUN)[0] is False


def test_tail_currency_bad_inputs():
    assert tail_currency("garbage", "crypto")[0] is False
    assert tail_currency(None, "stocks")[0] is False
    assert tail_currency(last_closed_bar(), "CRYPTO")[0] is True  # venue case-insensitive


def test_worker_probe_compiles_and_enforces_tail():
    compile(Sched.HOST_PY, "<HOST_PY>", "exec")
    assert "tail_currency" in Sched.HOST_PY
    assert "tail-stale: %s" in Sched.HOST_PY
    assert "cutoffs-import-fail" in Sched.HOST_PY  # fallback is loud, never silent


def test_readiness_ok_cache_short():
    src = inspect.getsource(Sched.readiness)
    assert "10800" not in src  # 3h ok-cache is gone: currency decays every 15m bar
    assert "> 1800" in src


def test_hold_triage_missing_and_never_raises(monkeypatch):
    from pathlib import Path as _P
    assert Sched._npz_hold_triage("DEFINITELY_NOT_A_SYMBOL_XYZ") == "s1:missing"
    monkeypatch.setattr(Sched, "ROOT", _P("/nonexistent-root-xyz"))
    assert Sched._npz_hold_triage("AAPL") == "s1:missing"
