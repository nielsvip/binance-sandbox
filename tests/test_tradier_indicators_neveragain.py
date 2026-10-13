"""NEVER-AGAIN 2026-10-13: slow-TF (15m/1h) scheduling starved for 20d while
1m/5m stayed fresh, plus an immutable flag froze latest.json, plus orphan
duplicate writers raced snapshots. Pins: window/overdue policy, EPERM
self-heal, singleton claim/takeover. All sync/pure — no event loop."""
import os
import time

import tradier_indicators as ti


def test_window_due_verbatim_gates():
    assert ti._tf_window_due("15m", 13, 0) is True
    assert ti._tf_window_due("15m", 13, 15) is True
    assert ti._tf_window_due("15m", 13, 30) is True
    assert ti._tf_window_due("15m", 13, 45) is True
    assert ti._tf_window_due("15m", 13, 16) is False
    assert ti._tf_window_due("1h", 10, 0) is True
    assert ti._tf_window_due("1h", 10, 30) is False
    assert ti._tf_window_due("1h", 8, 0) is False
    assert ti._tf_window_due("4h", 9, 30) is True
    assert ti._tf_window_due("4h", 13, 0) is True
    assert ti._tf_window_due("4h", 10, 0) is False
    assert ti._tf_window_due("D", 16, 0) is True
    assert ti._tf_window_due("D", 15, 59) is False
    assert ti._tf_window_due("1m", 3, 22) is True
    assert ti._tf_window_due("5m", 3, 22) is True


def test_should_run_window_and_overdue():
    now = 1_791_930_000.0
    # window match runs even with fresh ledger
    assert ti._tf_should_run("15m", 13, 30, now - 60, now) is True
    # off-window + fresh ledger -> no run
    assert ti._tf_should_run("15m", 13, 16, now - 60, now) is False
    # off-window + overdue ledger -> catch-up run
    assert ti._tf_should_run("15m", 13, 16, now - 2701, now) is True
    # off-window + missing ledger -> catch-up run (stale boot heals)
    assert ti._tf_should_run("15m", 13, 16, None, now) is True
    assert ti._tf_should_run("15m", 13, 16, 0, now) is True
    # 1h overdue threshold is hours, not minutes
    assert ti._tf_should_run("1h", 13, 16, now - 2701, now) is False
    assert ti._tf_should_run("1h", 13, 16, now - 3 * 3600 - 1, now) is True
    # fast TFs always run
    assert ti._tf_should_run("1m", 13, 16, now - 1, now) is True


def test_replace_self_heal_recovers_uchg(tmp_path, monkeypatch):
    src = tmp_path / "a.tmp"
    dst = tmp_path / "latest.json"
    src.write_bytes(b"{}")
    calls = {"chflags": 0}
    real_replace = os.replace

    def flaky_replace(a, b):
        if calls["chflags"] == 0 and str(b) == str(dst):
            raise OSError(1, "Operation not permitted")
        return real_replace(a, b)

    import subprocess

    def fake_run(*a, **k):
        calls["chflags"] += 1
        return None

    monkeypatch.setattr(os, "replace", flaky_replace)
    monkeypatch.setattr(subprocess, "run", fake_run)
    ti._replace_self_heal(src, dst, "test")
    assert dst.read_bytes() == b"{}"
    assert calls["chflags"] == 1


def test_replace_self_heal_reraises_non_eperm(tmp_path, monkeypatch):
    import errno

    def boom(a, b):
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(os, "replace", boom)
    try:
        ti._replace_self_heal(tmp_path / "x", tmp_path / "y", "test")
    except OSError as e:
        assert e.errno == errno.EACCES
    else:
        raise AssertionError("must reraise non-EPERM")


def test_singleton_claim_and_takeover(tmp_path, monkeypatch):
    lp = tmp_path / "t.lock"
    monkeypatch.setattr(ti, "_instance_lock_path", lambda: lp)
    assert ti._try_claim_instance() is True
    # a DIFFERENT live holder blocks contenders
    lp.write_text("424242 1\n")
    monkeypatch.setattr(ti, "_pid_is_live_indicators", lambda pid: pid == 424242)
    assert ti._try_claim_instance() is False
    # dead holder lock is taken over
    monkeypatch.setattr(ti, "_pid_is_live_indicators", lambda pid: False)
    assert ti._try_claim_instance() is True
    assert lp.read_text().split()[0] == str(os.getpid())
    ti._release_instance()
    assert not lp.exists()


def test_stale_first_orders_oldest_first():
    order = ti._stale_first(["B", "A", "C"], {"A": 100.0, "B": 50.0}.get)
    assert order[0] == "C"  # unknown (0.0) first
    assert order[1] == "B"
    assert order[2] == "A"
    assert ti._stale_first(["X"], lambda s: 1 / 0) == ["X"]


def _bare_orchestrator(**attrs):
    orch = object.__new__(ti.TradierIndicatorOrchestrator)
    orch._tf_gen = {}
    orch._tf_compute_lane = ti.TradierIndicatorOrchestrator._tf_compute_lane.__get__(orch)
    orch._compute_guarded = ti.TradierIndicatorOrchestrator._compute_guarded.__get__(orch)
    for k, v in attrs.items():
        setattr(orch, k, v)
    return orch


def test_compute_lane_split_fast_vs_slow():
    import asyncio
    orch = _bare_orchestrator(executor_fast="FAST", executor_slow="SLOW",
                              _compute_sem_fast=asyncio.Semaphore(2),
                              _compute_sem_slow=asyncio.Semaphore(4))
    assert orch._tf_compute_lane("1m")[0] == "FAST"
    assert orch._tf_compute_lane("5m")[0] == "FAST"
    for tf in ("15m", "1h", "4h", "D"):
        assert orch._tf_compute_lane(tf)[0] == "SLOW"
    assert orch._tf_compute_lane("1m")[0] is not orch._tf_compute_lane("15m")[0]


def test_compute_guarded_skips_stale_generation():
    calls = []

    class FakeCalc:
        def compute(self, *a):
            calls.append(a)
            return {"k": 1}

    orch = _bare_orchestrator(calculator=FakeCalc())
    orch._tf_gen["15m"] = 7
    assert orch._compute_guarded(6, "15m", None, "CME", None, None, False) is None
    assert calls == []
    assert orch._compute_guarded(7, "15m", None, "CME", None, None, False) == {"k": 1}
    assert len(calls) == 1


def test_guarded_run_bumps_generation():
    import asyncio
    orch = _bare_orchestrator()
    orch._tf_inflight = {}
    seen = []

    async def fake_process(tf, force=False):
        seen.append((tf, force))

    orch._process_timeframe = fake_process
    asyncio.run(ti.TradierIndicatorOrchestrator._run_tf_guarded(orch, "15m", True))
    assert orch._tf_gen["15m"] == 1
    assert seen == [("15m", True)]
    assert orch._tf_inflight["15m"] is False
    asyncio.run(ti.TradierIndicatorOrchestrator._run_tf_guarded(orch, "15m", False))
    assert orch._tf_gen["15m"] == 2


def test_guarded_run_clears_inflight_on_crash():
    import asyncio
    orch = _bare_orchestrator()
    orch._tf_inflight = {"1m": True}

    async def boom(tf, force=False):
        raise RuntimeError("wedged pass")

    orch._process_timeframe = boom
    asyncio.run(ti.TradierIndicatorOrchestrator._run_tf_guarded(orch, "1m", False))
    assert orch._tf_inflight["1m"] is False
    assert orch._tf_gen["1m"] == 1
