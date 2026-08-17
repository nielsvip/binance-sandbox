from pathlib import Path

import ssh_tunnel_keeper as keeper


ROOT = Path(__file__).resolve().parent


def test_s1_forward_health_uses_exact_bible_route(monkeypatch):
    calls = []

    class Result:
        returncode = 0

    def fake_run(command, **kwargs):
        calls.append(command)
        return Result()

    monkeypatch.setattr(keeper.subprocess, "run", fake_run)
    assert keeper.is_alive("s1-sftp") is True
    assert "-p" in calls[0] and calls[0][calls[0].index("-p") + 1] == "2201"
    assert "niels@127.0.0.1" in calls[0]
    assert str(Path.home() / ".ssh/id_ed25519") in calls[0]


def test_s1_forward_repair_reuses_live_s1_int_master(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(
        keeper.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or Result(),
    )
    assert keeper.connect("s1-sftp") == (True, "s1-int-control-master")
    assert calls[0] == [
        "ssh", "-O", "cancel", "-L", "2201:127.0.0.1:22", "s1-int"
    ]
    assert calls[1] == [
        "ssh", "-O", "forward", "-L", "2201:127.0.0.1:22", "s1-int"
    ]


def test_non_forward_master_does_not_open_probe(monkeypatch):
    calls = []

    class Result:
        returncode = 0

    monkeypatch.setattr(
        keeper.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or Result(),
    )
    assert keeper.is_alive("gateway-internal") is True
    assert calls == [["ssh", "-O", "check", "gateway-internal"]]


def test_repaired_services_restart_tunnel_keeper_after_sync_pass():
    source = (ROOT / "mac_live_heartbeat.py").read_text()
    assert '"com.niels.ssh_tunnel_keeper"' in source
    assert "_restart_requested_tunnel_keeper()" in source
    assert "RESTART_TUNNEL_KEEPER_REQUEST" in source


def test_keeper_yields_to_verified_transport_lock():
    source = (ROOT / "ssh_tunnel_keeper.py").read_text()
    assert ".s1_transport.lock" in source
    assert 'host == "s1-sftp" and TRANSPORT_LOCK.is_dir()' in source
