import importlib.util
import pathlib
import time

spec = importlib.util.spec_from_file_location("reaper", pathlib.Path(__file__).resolve().parents[1] / "tools" / "v15_pilot_orphan_reaper.py")
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)
CMD = "python -u v15_pilot.py --sym-side RS_LONG --workers 7"


def _setup(monkeypatch, table, live_age, kernel_children_blind=True):
    # table: pid -> (ppid, sid, age_s)
    now = time.time()
    monkeypatch.setattr(R, "_pids", lambda: list(table))
    monkeypatch.setattr(R, "_cmdline", lambda pid: CMD)
    monkeypatch.setattr(R, "_boot_time", lambda: 0.0)
    monkeypatch.setattr(R.os, "sysconf", lambda name: 100 if name == "SC_CLK_TCK" else 4096)
    monkeypatch.setattr(R, "_stat_fields", lambda pid: {"ppid": table[pid][0], "sid": table[pid][1], "starttime": int((now - table[pid][2]) * 100)})
    monkeypatch.setattr(R, "_children", lambda pid: [])
    monkeypatch.setattr(R, "_rss_mb", lambda pid: 1000.0)
    monkeypatch.setattr(R, "_liveness_age_s", lambda pid, ss, n: live_age)


def test_live_parent_and_workers_never_reaped(monkeypatch):
    # parent is session leader ppid 1; workers ppid = parent; kernel children file blind (returns [])
    _setup(monkeypatch, {100: (1, 100, 5000), 101: (100, 100, 4000), 102: (100, 100, 4000)}, live_age=9999)
    orphans, healthy = R.scan(1500, 900)
    assert orphans == [] and len(healthy) == 3


def test_parent_dead_workers_reaped(monkeypatch):
    _setup(monkeypatch, {101: (1, 100, 4000), 102: (1, 100, 4000), 103: (1, 100, 4000)}, live_age=9999)
    orphans, healthy = R.scan(1500, 900)
    assert sorted(o["pid"] for o in orphans) == [101, 102, 103] and healthy == []


def test_parent_alive_but_quiet_one_hour_never_reaped(monkeypatch):
    _setup(monkeypatch, {100: (1, 100, 7200), 101: (100, 100, 7000)}, live_age=3600)
    orphans, healthy = R.scan(1500, 900)
    assert orphans == []


def test_parent_alive_without_setsid_has_kids_by_ppid_scan(monkeypatch):
    _setup(monkeypatch, {100: (1, 50, 7200), 101: (100, 50, 7000)}, live_age=3600)
    orphans, _ = R.scan(1500, 900)
    assert orphans == []


def test_fresh_progress_protects_orphan_looking_group(monkeypatch):
    _setup(monkeypatch, {101: (1, 100, 4000), 102: (1, 100, 4000)}, live_age=60)
    assert R.scan(1500, 900)[0] == []


def test_lone_process_never_reaped(monkeypatch):
    _setup(monkeypatch, {101: (1, 100, 4000)}, live_age=9999)
    assert R.scan(1500, 900)[0] == []
