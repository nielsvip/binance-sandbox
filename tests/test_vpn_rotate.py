"""2026-10-24 always-on VPN: rotation must never wedge with all slots
simultaneously skipped, must restore a tunnel even when naked is healthy,
and must never tear down the working link before the replacement verifies
(make-before-break). Pins the pure selection logic behind those laws."""

import importlib.util
import time
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "vpn_rotate",
    Path(__file__).resolve().parent.parent / "tools" / "surfshark_vpn_rotate.py",
)
vpn = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vpn)


def test_slot_rank_prefers_uk_sg_never_nl_us():
    assert vpn.slot_rank("uk-lon-st001.prod.surfshark.com") == 1
    assert vpn.slot_rank("sg-sng-st007.prod.surfshark.com") == 2
    assert vpn.slot_rank("nl-ams-st001.prod.surfshark.com") is None
    assert vpn.slot_rank("us-nyc-st001.prod.surfshark.com") is None
    assert vpn.slot_rank("de-fra-st001.prod.surfshark.com") == 3


def _fake_env(monkeypatch, fails):
    monkeypatch.setattr(
        vpn,
        "slots",
        lambda: [
            {"name": "Surfshark. Dausos", "status": "Disconnected"},
            {"name": "Surfshark. WireGuard", "status": "Disconnected"},
        ],
    )
    monkeypatch.setattr(
        vpn, "slot_server", lambda n: ("uk-lon-st002.prod.surfshark.com", None)
    )
    return {"slot_fail": fails, "slot_exit": {}}


def test_eligible_skips_recent_fail_but_not_old(monkeypatch):
    monkeypatch.setattr(vpn, "OVPN_ONLY", False)
    now = time.time()
    state = _fake_env(monkeypatch, {"Surfshark. Dausos": now - 60})
    got = [s["name"] for s in vpn.eligible_slots(set(), state, None)]
    assert got == ["Surfshark. WireGuard"]
    state["slot_fail"]["Surfshark. Dausos"] = now - vpn.FAIL_SKIP_S - 1
    got = [s["name"] for s in vpn.eligible_slots(set(), state, None)]
    assert sorted(got) == ["Surfshark. Dausos", "Surfshark. WireGuard"]


def test_eligible_honors_skip_name(monkeypatch):
    monkeypatch.setattr(vpn, "OVPN_ONLY", False)
    state = _fake_env(monkeypatch, {})
    got = [s["name"] for s in vpn.eligible_slots(set(), state, "Surfshark. Dausos")]
    assert got == ["Surfshark. WireGuard"]


def test_fail_skip_window_is_minutes_not_hours():
    assert vpn.FAIL_SKIP_S <= 600


def test_scan_trips_on_fresh_ban_line(tmp_path, monkeypatch):
    import json
    import os

    f = tmp_path / "ez_manage_fin.log"
    f.write_text(
        "24 06:00:00 - ERROR - [fin] Failed: APIError(code=-1003): Way too many requests\n"
    )
    ban = tmp_path / "ban.json"
    monkeypatch.setattr(vpn, "BAN_LOG_FILES", [str(f)])
    monkeypatch.setattr(vpn, "BAN_FILES", [str(ban)])
    assert vpn.scan_logs_for_ban() is True
    d = json.loads(ban.read_text())
    assert d["ban_until_ms"] > __import__("time").time() * 1000


def test_scan_ignores_stale_file_and_clean_tail(tmp_path, monkeypatch):
    import os
    import time

    f = tmp_path / "ez_manage_fin.log"
    f.write_text("24 06:00:00 - ERROR - APIError(code=-1003): banned\n")
    old = time.time() - 600
    os.utime(f, (old, old))
    g = tmp_path / "ez_market_data.log"
    g.write_text("24 06:00:00 - INFO - all quiet\n")
    ban = tmp_path / "ban.json"
    monkeypatch.setattr(vpn, "BAN_LOG_FILES", [str(f), str(g)])
    monkeypatch.setattr(vpn, "BAN_FILES", [str(ban)])
    assert vpn.scan_logs_for_ban() is False
    assert not ban.exists()


def test_ovpn_hidden_until_supervisor_installed(tmp_path, monkeypatch):
    import os

    d = tmp_path / "servers"
    d.mkdir()
    (d / "uk-lon.prod.surfshark.comsurfshark_openvpn_udp.ovpn").write_text("remote x\n")
    monkeypatch.setattr(vpn, "OVPN_DIR", str(tmp_path))
    real_exists = os.path.exists
    monkeypatch.setattr(
        vpn.os.path,
        "exists",
        lambda p: (
            False
            if p == "/Library/LaunchDaemons/com.binance.ovpn-supervisor.plist"
            else real_exists(p)
        ),
    )
    assert vpn.ovpn_servers() == {}


def test_ovpn_build_active_rewrites_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn, "OVPN_DIR", str(tmp_path))
    monkeypatch.setattr(vpn, "OVPN_AUTH", "/auth.txt")
    (tmp_path / "s.ovpn").write_text("dev tun\nauth-user-pass\nremote h 1194\n")
    assert vpn.ovpn_build_active(str(tmp_path / "s.ovpn")) is True
    txt = (tmp_path / "active.conf").read_text()
    assert "auth-user-pass /auth.txt" in txt
    assert txt.count("auth-user-pass") == 1


def test_slot_dispatch_prefers_ovpn_branch():
    assert vpn._is_ovpn("OVPN:uk-lon") is True
    assert vpn._is_ovpn("Surfshark. Dausos") is False
    assert vpn.slot_rank("uk-lon.prod.surfshark.com") == 1
    assert vpn.slot_rank("sg-sng.prod.surfshark.com") == 2


def test_tour_walks_ring_skipping_current():
    order = ["Surfshark. Dausos", "Surfshark. WireGuard", "(no-vpn)"]
    assert vpn._tour_next("Surfshark. Dausos", order) == "Surfshark. WireGuard"
    assert vpn._tour_next("(no-vpn)", order) == "Surfshark. Dausos"
    assert vpn._tour_next("Surfshark. Dausos", ["Surfshark. Dausos"]) is None
    assert vpn._tour_next("x", []) is None


def _staged_servers():
    import pytest

    d = Path(vpn.OVPN_DIR) / "servers"
    files = sorted(d.glob("*.ovpn")) if d.is_dir() else []
    if not files:
        pytest.skip("no staged OVPN server files on this host")
    return files


def test_ovpn_remotes_sane():
    import re

    wl = vpn.whitelist()
    for f in _staged_servers():
        txt = f.read_text()
        ips = re.findall(r"^remote\s+(\d+\.\d+\.\d+\.\d+)\s", txt, re.M)
        hosts = re.findall(r"^remote\s+([a-zA-Z0-9.-]+)\s", txt, re.M)
        hosts = [h for h in hosts if not re.match(r"^\d+\.\d+\.\d+\.\d+$", h)]
        # STATIC-ONLY doctrine (operator): pool hostnames churn, so every remote
        # must be a literal operator static inside the Binance whitelist.
        assert ips, f"{f.name}: no static-IP remote"
        assert (
            not hosts
        ), f"{f.name}: pool-hostname remote {hosts} (churns; use statics)"
        assert (
            set(ips) <= wl
        ), f"{f.name}: non-whitelisted remotes {set(ips) - wl} would deny orders"


def test_ovpn_only_flag_gates_app_slots(monkeypatch):
    monkeypatch.setattr(
        vpn,
        "slots",
        lambda: [
            {"name": "Surfshark. Dausos", "status": "Disconnected"},
            {"name": "Surfshark. WireGuard", "status": "Disconnected"},
            {"name": "OVPN:uk-lon", "status": "Disconnected"},
        ],
    )
    monkeypatch.setattr(
        vpn, "slot_server", lambda n: ("uk-lon.prod.surfshark.com", None)
    )
    state = {"slot_fail": {}, "slot_exit": {}}
    monkeypatch.setattr(vpn, "OVPN_ONLY", True)
    got = [s["name"] for s in vpn.eligible_slots(set(), state, None)]
    assert got == ["OVPN:uk-lon"]
    monkeypatch.setattr(vpn, "OVPN_ONLY", False)
    got = sorted(s["name"] for s in vpn.eligible_slots(set(), state, None))
    assert got == ["OVPN:uk-lon", "Surfshark. Dausos", "Surfshark. WireGuard"]


def test_wait_state_same_ip_ok_for_statics(monkeypatch):
    monkeypatch.setattr(vpn, "slots", lambda: [{"name": "X", "status": "Connected"}])
    monkeypatch.setattr(vpn, "egress_ip", lambda: "1.2.3.4")
    assert (
        vpn.wait_state(["X"], "Connected", "1.2.3.4", timeout=5, same_ip_ok=True)
        is True
    )
    assert vpn.wait_state(["X"], "Connected", "1.2.3.4", timeout=0) is False
    assert vpn.wait_state(["X"], "Connected", "9.9.9.9", timeout=5) is True


def test_should_stay_put_doctrine():
    wl = {"200.118.16.10", "185.134.22.81"}
    home = ["200.118.16.10"]
    assert vpn.should_stay_put("200.118.16.10", {"ping": 200}, wl, home) is True
    assert vpn.should_stay_put("185.134.22.81", {"ping": 200}, wl, home) is False
    assert vpn.should_stay_put("200.118.16.10", {"ping": 418}, wl, home) is False
    assert vpn.should_stay_put("9.9.9.9", {"ping": 200}, wl, home) is False
    assert vpn.should_stay_put(None, {"ping": 200}, wl, home) is False


def test_tour_disabled_by_doctrine():
    assert vpn.TOUR_ENABLED is False


def test_home_ips_whitelisted():
    cfg = vpn.net_cfg()
    assert set(cfg["home_ips"]) <= vpn.whitelist()
    assert cfg["home_ips"] == ["200.118.16.10"]


def test_ban_fallback_london_first():
    assert vpn.BAN_FALLBACK_ORDER[0] == "Surfshark. WireGuard®"
    cands = [{"name": n} for n in ["OVPN:sg-sng", "OVPN:uk-lon", "Surfshark. Dausos"]]
    assert [c["name"] for c in vpn.order_candidates(cands)] == [
        "OVPN:uk-lon",
        "Surfshark. Dausos",
        "OVPN:sg-sng",
    ]
    cands = [{"name": n} for n in ["OVPN:de-fra-static", "Surfshark. WireGuard®"]]
    assert [c["name"] for c in vpn.order_candidates(cands)][
        0
    ] == "Surfshark. WireGuard®"


def test_slot_rank_covers_static_geos():
    assert vpn.slot_rank("br-sao-static.prod.surfshark.com") == 3
    assert vpn.slot_rank("au-syd-static.prod.surfshark.com") == 3
    assert vpn.slot_rank("fr-par-static.prod.surfshark.com") == 3
    assert vpn.slot_rank("de-fra-static.prod.surfshark.com") == 3


def test_ovpn_hardening_knobs():
    for f in _staged_servers():
        txt = f.read_text()
        for knob in [
            "ping-restart 60",
            "hand-window 20",
            "tls-timeout 5",
            "persist-tun",
            "persist-key",
            "explicit-exit-notify 1",
            "mssfix 1400",
            "remote-random",
            "replay-window 64 60",
            "resolv-retry 10",
        ]:
            assert knob in txt, f"{f.name}: missing {knob}"
        assert "ping-restart 0" not in txt, f"{f.name}: dead-peer detection disabled"


def test_build_active_preserves_hardening(tmp_path, monkeypatch):
    monkeypatch.setattr(vpn, "OVPN_DIR", str(tmp_path))
    monkeypatch.setattr(vpn, "OVPN_AUTH", "/auth.txt")
    src = (
        "dev tun\nauth-user-pass\nremote 1.2.3.4 1194\nremote-random\n"
        "ping-restart 60\nhand-window 20\npersist-tun\npersist-key\n"
    )
    (tmp_path / "s.ovpn").write_text(src)
    assert vpn.ovpn_build_active(str(tmp_path / "s.ovpn")) is True
    txt = (tmp_path / "active.conf").read_text()
    assert (
        "remote 1.2.3.4 1194" in txt
        and "persist-tun" in txt
        and "hand-window 20" in txt
    )


def test_restore_try_bounded():
    assert vpn.RESTORE_TRY_S <= 180
