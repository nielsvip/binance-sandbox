import json
import os
from pathlib import Path
import time

from tools.history_7d_sync import apply, export, lease_authorized, merge, resolve_host


def test_auto_host_falls_back_when_public_route_is_down(monkeypatch):
    calls = []
    def probe(command, timeout=90):
        calls.append(command[-2])
        if command[-2] == "s1-pub":
            raise RuntimeError("public route down")
        return "niels\n"
    monkeypatch.setattr("tools.history_7d_sync._run", probe)
    assert resolve_host("auto") == "s1-int"
    assert calls == ["s1-pub", "s1-int"]


def _event(ts, kind, price):
    return json.dumps({"ts": ts, "type": kind, "qty": 1, "price": price}) + "\n"


def test_two_hosts_merge_recent_trades_without_losing_older_history(tmp_path):
    now = time.time()
    mac, s1 = tmp_path / "mac", tmp_path / "s1"
    rel = Path("data/history/men/ARBUSDC_LONG.jsonl")
    for root in (mac, s1):
        (root / rel).parent.mkdir(parents=True)
    old = _event(now - 9 * 86400, "OPEN", 1.0)
    mac_new = _event(now - 60, "CLOSE", 1.1)
    s1_new = _event(now - 30, "OPEN", 1.2)
    (mac / rel).write_text(old + mac_new)
    (s1 / rel).write_text(old + s1_new + "malformed\n")
    os.utime(mac / rel, (now, now))
    os.utime(s1 / rel, (now, now))
    union = merge([export(mac, now), export(s1, now)])
    assert sum(map(len, union["rows"].values())) == 2
    first = apply(mac, union, "test")
    second = apply(s1, union, "test")
    assert first["added_rows"] == second["added_rows"] == 1
    assert (mac / rel).read_text().startswith(old)
    assert (s1 / rel).read_text().startswith(old)
    assert mac_new in (s1 / rel).read_text() and s1_new in (mac / rel).read_text()
    assert (
        mac / "backups/before_history_7d_sync_test/men/ARBUSDC_LONG.jsonl"
    ).read_text() == old + mac_new
    repeated_mac = apply(mac, union, "again")
    repeated_s1 = apply(s1, union, "again")
    assert repeated_mac["added_rows"] == repeated_s1["added_rows"] == 0
    assert repeated_mac["cached_files"] == repeated_s1["cached_files"] == 1


def test_rotated_recent_rows_reach_other_host_and_older_rows_do_not(tmp_path):
    now = time.time()
    source, dest = tmp_path / "source", tmp_path / "dest"
    archive = source / "data/history/fin/QNTUSDT_SHORT.jsonl.202610261200.bak"
    archive.parent.mkdir(parents=True)
    archive.write_text(
        _event(now - 8 * 86400, "OPEN", 3.0) + _event(now - 10, "CLOSE", 3.2)
    )
    os.utime(archive, (now, now))
    pack = export(source, now)
    assert sum(map(len, pack["rows"].values())) == 1
    assert apply(dest, pack, "test")["added_rows"] == 1
    target = dest / "data/history/fin/QNTUSDT_SHORT.jsonl"
    assert '"price": 3.2' in target.read_text()
    assert '"price": 3.0' not in target.read_text()


def test_s1_rows_import_only_during_account_scoped_s1_lease():
    pack = {
        "schema": 1,
        "now": 1_800_000_000.0,
        "days": 7,
        "rows": {
            "men/ARBUSDC_LONG.jsonl": [
                _event(100, "OPEN", 1.0).strip(),
                _event(150, "OPEN", 1.1).strip(),
                _event(200, "CLOSE", 1.2).strip(),
            ],
            "ang/ARBUSDC_LONG.jsonl": [_event(150, "OPEN", 2.0).strip()],
        },
    }
    alerts = "\n".join(
        json.dumps(row)
        for row in (
            {"ts": 90, "kind": "LEASE_MAC"},
            {
                "ts": 120,
                "kind": "LEASE_S1",
                "venues": "crypto",
                "accounts": {"crypto": ["men"]},
            },
            {"ts": 180, "kind": "LEASE_MAC"},
        )
    )
    filtered, audit = lease_authorized(pack, alerts)
    assert audit["s1_lease_rows"] == 1
    assert audit["s1_unproven_rows"] == 3
    assert len(filtered["rows"]["men/ARBUSDC_LONG.jsonl"]) == 1
    assert "ang/ARBUSDC_LONG.jsonl" not in filtered["rows"]
    filtered_again, audit_again = lease_authorized(
        pack,
        alerts,
        {"rows": {"men/ARBUSDC_LONG.jsonl": [_event(150, "OPEN", 1.1).strip()]}},
    )
    assert audit_again["already_on_mac"] == 1
    assert audit_again["s1_lease_rows"] == 0
    assert "men/ARBUSDC_LONG.jsonl" not in filtered_again["rows"]


def test_near_identical_trade_needs_manual_reconciliation(tmp_path):
    root = tmp_path / "mac"
    target = root / "data/history/ang/BBUSDT_SHORT.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text(_event(150, "OPEN", 2.0))
    pack = {
        "schema": 1,
        "now": 200,
        "days": 7,
        "rows": {"ang/BBUSDT_SHORT.jsonl": [_event(152, "OPEN", 2.0).strip()]},
    }
    result = apply(root, pack, "test")
    assert result["ambiguous_rows"] == 1
    assert result["added_rows"] == 0
    assert target.read_text() == _event(150, "OPEN", 2.0)
