"""ez_positions_realtime.acquire_pid_file (USER 2026-10-22 positions-reliability).

2026-10-22 measured: the old O_TRUNC-before-LOCK_EX left all four realtime pid
files 0 bytes — every duplicate start (keepalive pgrep-miss under load) zeroed
the running worker's pid record, then exited. The lock must be taken BEFORE any
truncate; a lock loser must leave the live record byte-identical.
"""
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ez_positions_realtime as rt  # noqa: E402


def test_acquire_writes_own_pid(tmp_path):
    pid_file = tmp_path / "ez_positions_realtime_fin.pid"
    fd = rt.acquire_pid_file(pid_file)
    try:
        assert pid_file.read_text() == str(os.getpid())
    finally:
        os.close(fd)


def test_lock_loser_leaves_live_record_intact(tmp_path):
    """A live worker holds the file (sentinel content); a duplicate start must raise
    _PidLocked and NOT truncate — the regression that zeroed the fleet's pid files."""
    pid_file = tmp_path / "ez_positions_realtime_fin.pid"
    pid_file.write_text("26031")
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl, time, sys; fd = open(sys.argv[1], 'r+');"
         "fcntl.flock(fd, fcntl.LOCK_EX); time.sleep(15)",
         str(pid_file)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 10
        locked = False
        while time.time() < deadline:
            try:
                probe = os.open(str(pid_file), os.O_RDWR)
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(probe, fcntl.LOCK_UN)
                finally:
                    os.close(probe)
                time.sleep(0.2)
            except BlockingIOError:
                locked = True
                break
        assert locked, "holder child never took the lock"
        with pytest.raises(rt._PidLocked):
            rt.acquire_pid_file(pid_file)
        assert pid_file.read_text() == "26031"
    finally:
        holder.terminate()
        holder.wait(timeout=10)
    fd = rt.acquire_pid_file(pid_file)
    try:
        assert pid_file.read_text() == str(os.getpid())
    finally:
        os.close(fd)
