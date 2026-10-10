"""Pusher 50-min budget: screen_all honors deadlines, greedy_push reports budget_hit."""

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import v15_gain_pusher as P


def _fake_eval(prep, trial, window_days=30, include_ledger=False):
    return {"gain_pct": 10.0, "trades": 50, "valid": True, "ledger": []}


def test_screen_all_deadline_stops(monkeypatch):
    monkeypatch.setattr(P, "timed_eval", _fake_eval)
    cands = [(f"C{i}", {f"SW{i}": True}) for i in range(30)]
    out = P.screen_all("X_LONG", None, {}, cands, {}, 5.0, {}, deadline=time.time() - 1)
    assert out == []
    out2 = P.screen_all("X_LONG", None, {}, cands, {}, 5.0, {}, deadline=time.time() + 600)
    assert len(out2) == 30
    out3 = P.screen_all("X_LONG", None, {}, cands, {}, 5.0, {})
    assert len(out3) == 30


def test_screen_all_skips_unchanged(monkeypatch):
    monkeypatch.setattr(P, "timed_eval", _fake_eval)
    cands = [("SAME", {"A": 1})]
    out = P.screen_all("X_LONG", None, {"A": 1}, cands, {"A": 1}, 5.0, {})
    assert out == []
