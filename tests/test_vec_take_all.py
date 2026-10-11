"""Twin take_all coalescing (USER 2026-10-11 own-it: offloop convoy -> exit-42). The manager EXIT path took 3 separate global-lock acquisitions per symbol-decision (take + take_catchup + expire_old); boundary bursts convoyed past the 120s PAU timeout and 3 consecutive hangs os._exit(42) (fleet-wide chronic: thousands of >30s takes). take_all runs the same three calls in the same order in one worker under one acquisition — same order, same thread, same lock, zero new concurrency. Semaphore parallelism was REJECTED: take()'s check-then-add _EMITTED claim is not atomic across threads (double-handout -> double orders).
"""
import copy
from live_twins import vec_exact as V


CUR = 1791697500.0
SYM, SIDE = "TSTUSDC", "LONG"
SS = "TSTUSDC_LONG"


def _seed(monkeypatch):
    decided = {
        (SS, CUR): [{"type": "CLOSE", "reason": "R_NOW", "n": 1}],
        (SS, CUR - 900.0): [{"type": "REDUCE", "reason": "R_CATCH", "n": 2}],
        (SS, CUR - 5 * 900.0): [{"type": "CLOSE", "reason": "R_OLD", "n": 3}],
    }
    monkeypatch.setattr(V, "_DECIDED", copy.deepcopy(decided))
    monkeypatch.setattr(V, "_EMITTED", set())
    monkeypatch.setattr(V, "_EXPIRED", set())
    monkeypatch.setattr(V, "actions_at", lambda s, t, now: {"status": "OK", "bar_ts": CUR, "actions": decided[(SS, CUR)]})
    return decided


def _snap():
    return (set(V._EMITTED), set(V._EXPIRED))


def test_take_all_matches_sequential_results_and_state(monkeypatch):
    _seed(monkeypatch)
    want = ("CLOSE", "REDUCE")
    seq_now = V.take(SYM, SIDE, CUR, want)
    seq_catch = V.take_catchup(SYM, SIDE, CUR)
    V.expire_old(SYM, SIDE, CUR)
    seq_state = _snap()
    _seed(monkeypatch)
    got_now, got_catch = V.take_all(SYM, SIDE, CUR, want)
    assert got_now == seq_now and got_catch == seq_catch
    assert _snap() == seq_state
    assert [a["reason"] for a in got_now] == ["R_NOW"]
    assert [a["reason"] for a in got_catch] == ["R_CATCH"]


def test_take_all_emits_each_action_once(monkeypatch):
    _seed(monkeypatch)
    want = ("CLOSE", "REDUCE")
    first = V.take_all(SYM, SIDE, CUR, want)
    assert len(first[0]) == 1 and len(first[1]) == 1
    second = V.take_all(SYM, SIDE, CUR, want)
    assert second == ([], [])


def test_take_all_non_ok_short_circuits(monkeypatch):
    _seed(monkeypatch)
    monkeypatch.setattr(V, "actions_at", lambda s, t, now: {"status": "WARMUP", "bar_ts": 0.0, "actions": []})
    assert V.take_all(SYM, SIDE, CUR, ("CLOSE",)) == ([], [])
