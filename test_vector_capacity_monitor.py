import argparse
from pathlib import Path

from tools import vector_capacity_monitor as monitor


def test_parse_campaign():
    label, path = monitor.parse_campaign("long=/tmp/long.pid")
    assert label == "long"
    assert path == Path("/tmp/long.pid")


def test_owned_root_records_process_group_ownership(monkeypatch, tmp_path):
    pidfile = tmp_path / "worker.pid"
    pidfile.write_text("123")
    monkeypatch.setattr(monitor.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(monitor.os, "getpgid", lambda pid: 456)
    roots = monitor.read_owned_roots([("cohort", pidfile)])
    assert roots[0]["process_group"] == 456
    assert roots[0]["owns_process_group"] is False


def test_sample_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "cpu_sample", lambda seconds: (88.0, 12.0))
    monkeypatch.setattr(
        monitor,
        "memory_sample",
        lambda: {
            "total_bytes": 32 * monitor.GIB,
            "used_bytes": 24 * monitor.GIB,
            "available_bytes": 8 * monitor.GIB,
            "used_pct": 75.0,
            "available_gib": 8.0,
            "swap_total_bytes": 0,
            "swap_free_bytes": 0,
        },
    )
    monkeypatch.setattr(monitor, "process_table", lambda: [])
    args = argparse.Namespace(
        cpu_sample_seconds=0.1,
        min_available_gib=4.0,
        max_memory_used_pct=85.0,
        min_headroom_pct=15.0,
    )
    row = monitor.sample(args, [])
    assert row["cpu_busy_pct"] == 88.0
    assert row["safety"]["unsafe"] is False
    assert row["discovery_contract"].startswith("VECTOR_DISCOVERY_ONLY")


def test_memory_breach_marks_unsafe(monkeypatch):
    monkeypatch.setattr(monitor, "cpu_sample", lambda seconds: (90.0, 10.0))
    monkeypatch.setattr(
        monitor,
        "memory_sample",
        lambda: {
            "total_bytes": 32 * monitor.GIB,
            "used_bytes": 29 * monitor.GIB,
            "available_bytes": 3 * monitor.GIB,
            "used_pct": 90.625,
            "available_gib": 3.0,
            "swap_total_bytes": 0,
            "swap_free_bytes": 0,
        },
    )
    monkeypatch.setattr(monitor, "process_table", lambda: [])
    args = argparse.Namespace(
        cpu_sample_seconds=0.1,
        min_available_gib=4.0,
        max_memory_used_pct=85.0,
        min_headroom_pct=15.0,
    )
    assert monitor.sample(args, [])["safety"]["unsafe"] is True
