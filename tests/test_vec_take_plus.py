"""take_plus: one serialized evaluation serves the OPEN acts and the bar snapshot.

USER 2026-10-19 unlock-and-fix: the ENTRY sweep spent two serialized offloop acquisitions per flat key (take, then
converge actions_at) queued behind 100+ keys on one global lock — 44.7s per eval measured, 58/58 dispatches timing out
at the 120s budget with zero successes. take_plus bundles both into one acquisition; exactly-once handout is the real
take(), unchanged.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_twins import vec_exact as VX  # noqa: E402


def test_single_evaluation_contract(monkeypatch):
    calls = {"take": 0, "actions_at": 0}

    def fake_take(symbol, side, now_ts, want):
        calls["take"] += 1
        return [{"n": 7, "type": "OPEN", "reason": "r", "bar_ts": 123.0}]

    def fake_actions_at(symbol, side, now_ts):
        calls["actions_at"] += 1
        return {"status": "OK", "bar_ts": 123.0, "vec_holds": True, "actions": []}

    monkeypatch.setattr(VX, "take", fake_take)
    monkeypatch.setattr(VX, "actions_at", fake_actions_at)
    acts, d = VX.take_plus("ZZZ", "LONG", 124.0, ("OPEN",))
    assert [a["n"] for a in acts] == [7]
    assert d["bar_ts"] == 123.0 and d["vec_holds"] is True
    assert calls == {"take": 1, "actions_at": 1}, calls


def test_unknown_sym_side_is_empty_not_an_error():
    acts, d = VX.take_plus("NOSUCHSYM", "LONG", 0.0, ("OPEN",))
    assert acts == []
    assert isinstance(d, dict) and d.get("status") != "OK"
