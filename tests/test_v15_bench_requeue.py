import importlib.util
import pytest
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("V15_BENCH_DIR", "/nonexistent_bench_for_tests")
spec = importlib.util.spec_from_file_location("v15_npz_bench", ROOT / "tools" / "v15_npz_bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

# A "pilot" that takes the DEDUP flock like v15_pilot.py, then forks a worker that inherits the lock fd and ignores
# SIGTERM, and whose leader exits at once on SIGTERM -> exactly the lingering-lock case of 2026-10-20.
PILOT = r'''
import fcntl, os, signal, sys, time
lk = open(sys.argv[1], "w")
fcntl.flock(lk.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
if os.fork() == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(120)
    os._exit(0)
signal.signal(signal.SIGTERM, lambda *a: os._exit(0))
open(sys.argv[2], "w").write("up")
time.sleep(120)
'''


def _start(tmp_path, side):
    locks = tmp_path / "data" / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    lock = locks / f"v15_pilot_{side}_abcdef0123456789.lock"
    ready = tmp_path / "ready"
    pr = subprocess.Popen([sys.executable, "-c", PILOT, str(lock), str(ready)], start_new_session=True)
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(0.05)
    assert ready.exists()
    return pr, lock


def test_lingering_worker_keeps_lock_until_reaped_then_requeue_can_lock(tmp_path):
    pr, lock = _start(tmp_path, "CHRUSDT_LONG")
    time.sleep(0.2)
    pr.terminate()
    pr.wait()
    assert not bench.lock_is_free(lock), "precondition: orphaned worker still holds the lock (old terminate() behaviour)"
    assert bench._pgrp_alive(pr.pid)
    os.killpg(pr.pid, signal.SIGKILL)
    deadline = time.time() + 5
    while not bench.lock_is_free(lock) and time.time() < deadline:
        time.sleep(0.1)


def test_reap_pilot_kills_the_tree_after_grace_and_clear_side_lock_frees_it(tmp_path):
    pr, lock = _start(tmp_path, "AMD_LONG")
    tree_s, hard = bench.reap_pilot(pr, grace_s=0.5, kill_wait_s=5)
    assert hard, "worker ignores SIGTERM -> SIGKILL after grace"
    assert not bench._pgrp_alive(pr.pid)
    cleared, waited = bench.clear_side_lock(tmp_path, "AMD_LONG", timeout_s=5)
    assert cleared and not lock.exists()


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs Linux /proc")
def test_clear_side_lock_kills_escaped_holder_outside_the_group(tmp_path):
    locks = tmp_path / "data" / "locks"
    locks.mkdir(parents=True)
    lock = locks / "v15_pilot_QBTS_LONG_0011223344556677.lock"
    ready = tmp_path / "ready"
    holder = subprocess.Popen([sys.executable, "-c", PILOT, str(lock), str(ready)], start_new_session=True)
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(0.05)
    try:
        assert not bench.lock_is_free(lock)
        cleared, waited = bench.clear_side_lock(tmp_path, "QBTS_LONG", timeout_s=10)
        assert cleared and not lock.exists()
    finally:
        try:
            os.killpg(holder.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        holder.wait()


def test_clear_side_lock_noop_when_no_locks(tmp_path):
    assert bench.clear_side_lock(tmp_path, "NOPE_LONG", timeout_s=1)[0]
