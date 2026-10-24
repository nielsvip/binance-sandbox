"""Bridge + safe-reclaim pins for setup_ssh_tunnels (USER 2026-10-24 resilient tunnels).

No network is touched: subprocess/time are monkeypatched throughout.
"""
import setup_ssh_tunnels as mgr


def test_bridge_prefers_direct_then_gateway():
    assert mgr.S1_BRIDGE_CANDIDATES[0][0] == "direct"
    assert mgr.S1_BRIDGE_CANDIDATES[1][0] == "gateway"
    assert mgr.HOSTS["s1-plan-a"]["forwards"][0]["local_port"] == 2201


def test_is_forward_owner_spares_data_sessions():
    mgr_child = ("ssh -N -L localhost:2201:localhost:22 -o ControlPath=/Users/niels/"
                 ".ssh/tunnel_controls/s1-plan-a.sock niels@157.180.125.52")
    assert mgr.is_forward_owner(mgr_child, 2201) is True
    hand = "ssh -fNT s1-sftp"
    assert mgr.is_forward_owner(hand + " -L 2201:10.0.0.3:22", 2201) is True
    data = ("ssh -4 -i /Users/niels/.ssh/id_ed25519 -p 2201 -o ConnectTimeout=20 "
            "niels@127.0.0.1 rsync --server --sender -g -l -o -p -D -r . /x")
    assert mgr.is_forward_owner(data, 2201) is False
    assert mgr.is_forward_owner("rsync -az -e ssh ./ h:~/binance-sandbox/", 2201) is False
    assert mgr.is_forward_owner("", 2201) is False
    assert mgr.is_forward_owner("ssh -N -L localhost:2202:localhost:22 h", 2201) is False


def test_bridge_falls_to_gateway_when_direct_unhealthy(monkeypatch):
    started = []

    class Proc:
        def __init__(self, pid):
            self.pid = pid

        def poll(self):
            return None

    monkeypatch.setattr(mgr, "_stop_host", lambda host_key: None)
    monkeypatch.setattr(mgr, "_kill_stale_port_holders", lambda host_key: None)
    monkeypatch.setattr(mgr.time, "sleep", lambda s: None)
    monkeypatch.setattr(mgr, "_start_forward",
                        lambda host_key, host: started.append(host) or Proc(len(started)))
    monkeypatch.setattr(mgr, "s1_end_to_end",
                        lambda port, timeout=8: started[-1] != "niels@157.180.125.52")
    monkeypatch.setattr(mgr, "_write_bridge_status", lambda route: None)
    mgr.bridge_route.pop("s1-plan-a", None)
    mgr.processes.pop("s1-plan-a", None)
    mgr.start_host("s1-plan-a")
    assert started[0] == "niels@157.180.125.52"
    assert started[-1] == "s1-via-gateway"
    assert mgr.bridge_route["s1-plan-a"] == "gateway"
    mgr.bridge_route.pop("s1-plan-a", None)
    mgr.processes.pop("s1-plan-a", None)


def test_transport_lock_defers_bridge_recycle(tmp_path, monkeypatch):
    assert mgr.transport_locked() in (True, False)
    monkeypatch.setattr(mgr, "TRANSPORT_LOCK", str(tmp_path / "nope"))
    assert mgr.transport_locked() is False
    (tmp_path / "nope").mkdir()
    assert mgr.transport_locked() is True
