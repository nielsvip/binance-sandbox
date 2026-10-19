#!/usr/bin/env python3
"""Tests for tools/ez_indicators_warmup_guard.py.

The guard exists because `ez_indicators.py` builds its task set — `_save_loop` among them — only AFTER a serial
warm-up over every timeframe (ez_indicators.py:2926-2930), while `run_with_watchdog.sh:186` SIGTERMs it after 120 s
of log silence. Warm-up legitimately logs one line per timeframe, minutes apart, so the watchdog killed a healthy
process forever and `data/latest_market_data.json` went 6 h stale with no error line (2026-10-18 20:46Z ->
2026-10-19 03:0xZ).

The guard's ONLY power is to append a heartbeat line to the log the watchdog stats, and the whole safety argument
is that it does so ONLY while the process is provably alive and progressing. These tests pin every refusal, so a
later edit cannot quietly turn the guard into a blanket "never kill ez_indicators" switch — which would be strictly
worse than the bug it fixes.
"""
import importlib.util
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "tools" / "ez_indicators_warmup_guard.py"


def _load():
    spec = importlib.util.spec_from_file_location("ez_indicators_warmup_guard", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def guard():
    return _load()


@pytest.fixture
def live_pid():
    """A real, CPU-burning process to stand in for a healthy ez_indicators."""
    proc = subprocess.Popen(
        ["/usr/bin/python3", "-c", "import time\nt=time.time()\nwhile time.time()-t<60: pass"]
    )
    time.sleep(0.5)
    yield proc.pid
    proc.kill()
    proc.wait()


@pytest.fixture
def parked_pid():
    """A process blocked on a read: alive in ps, consuming no CPU — the shape of a deadlocked interpreter."""
    proc = subprocess.Popen(["/bin/cat"], stdin=subprocess.PIPE)
    time.sleep(0.5)
    yield proc.pid
    proc.kill()
    proc.wait()


def _log_info(guard_mod, **over):
    now = time.time()
    base = {
        "warmup_done": False,
        "log_age_s": float(guard_mod.HB_WHEN_LOG_AGE_S + 240),
        "last_restart_epoch": now - 200,
        "last_tf_epoch": now - 100,
        "tfs_since_restart": ["D"],
        "restarts_last_hour": 3,
    }
    base.update(over)
    return base


def test_ps_time_parser_handles_bsd_format(guard):
    """macOS ps has no `etimes`; both age and cpu come from the [[dd-]hh:]mm:ss columns."""
    pid = subprocess.Popen(["/bin/cat"], stdin=subprocess.PIPE)
    try:
        time.sleep(0.5)
        assert guard._proc_age_s(pid.pid) is not None, "process age must parse on this host"
        assert guard._cpu_seconds(pid.pid) is not None, "cpu time must parse on this host"
        assert guard._proc_age_s(pid.pid) >= 0.0
    finally:
        pid.kill()
        pid.wait()


def test_ps_time_parser_rejects_keyword_dump(guard):
    """Asking BSD ps for an unknown keyword prints its keyword list; that must never parse as a number."""
    assert guard._ps_seconds(99999999, "cputime") is None


def test_protects_a_healthy_progressing_warmup(guard, live_pid):
    ok, reason = guard._heartbeat_decision(live_pid, _log_info(guard), False)
    assert ok is True, reason
    assert "alive and progressing" in reason


def test_refuses_when_process_is_parked(guard, parked_pid):
    """No CPU advance => the watchdog must be allowed to kill it."""
    ok, reason = guard._heartbeat_decision(parked_pid, _log_info(guard), False)
    assert ok is False
    assert "not computing" in reason


def test_refuses_when_no_process(guard):
    ok, reason = guard._heartbeat_decision(None, _log_info(guard), False)
    assert ok is False
    assert "no ez_indicators process" in reason


def test_refuses_once_warmup_is_complete(guard, live_pid):
    """After warm-up the save loop exists and keeps the log busy by itself — no protection wanted."""
    ok, reason = guard._heartbeat_decision(live_pid, _log_info(guard, warmup_done=True), False)
    assert ok is False
    assert "warm-up already complete" in reason


def test_refuses_while_log_is_still_fresh(guard, live_pid):
    """Only top up when the log is halfway to the watchdog's 120 s limit."""
    ok, reason = guard._heartbeat_decision(live_pid, _log_info(guard, log_age_s=5.0), False)
    assert ok is False
    assert "log fresh" in reason


def test_refuses_when_warmup_progress_has_stalled(guard, live_pid):
    """A timeframe line older than TF_MAX_GAP_S means the warm-up itself is wedged, CPU or no CPU."""
    now = time.time()
    info = _log_info(
        guard,
        last_restart_epoch=now - (guard.TF_MAX_GAP_S + 1300),
        last_tf_epoch=now - (guard.TF_MAX_GAP_S + 800),
    )
    ok, reason = guard._heartbeat_decision(live_pid, info, False)
    assert ok is False
    assert "wedged" in reason


def test_refuses_past_the_absolute_grace_bound(guard, live_pid, monkeypatch):
    """Patience is bounded: past WARMUP_GRACE_S the guard stops protecting no matter how healthy it looks."""
    monkeypatch.setattr(guard, "WARMUP_GRACE_S", 0)
    ok, reason = guard._heartbeat_decision(live_pid, _log_info(guard), False)
    assert ok is False
    assert "protection bounded" in reason


def test_dry_run_never_writes(guard, live_pid):
    ok, reason = guard._heartbeat_decision(live_pid, _log_info(guard), True)
    assert ok is False
    assert "dry-run" in reason


def test_heartbeat_line_is_tagged_and_timestamped(guard, tmp_path, monkeypatch):
    """The line must be parseable by anything reading this log, and self-identifying."""
    fake = tmp_path / "ez_indicators.log"
    fake.write_text("")
    monkeypatch.setattr(guard, "LOG", fake)
    line = guard._write_heartbeat(4242, "test reason")
    assert "[WARMUP_HEARTBEAT]" in line
    assert "pid=4242" in line
    assert "ez_indicators_warmup_guard.py" in line
    assert guard.TS_RE.match(fake.read_text().splitlines()[0]), "must carry a parseable log timestamp"


def test_guard_never_kills_or_signals_anything():
    """The guard's contract: it appends a log line and writes reports. Nothing else."""
    src = GUARD.read_text()
    for forbidden in ("kill(", "SIGTERM", "SIGKILL", "pkill", "killall", "terminate()", "os.kill"):
        assert forbidden not in src, f"the guard must never signal a process ({forbidden} found)"


def test_guard_reports_and_alert_paths_are_under_data_reports(guard):
    assert guard.OUT.parent == ROOT / "data" / "reports"
    assert guard.ALERT.parent == ROOT / "data" / "reports"
