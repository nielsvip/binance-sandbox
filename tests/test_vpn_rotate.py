"""2026-10-24 always-on VPN: rotation must never wedge with all slots
simultaneously skipped, must restore a tunnel even when naked is healthy,
and must never tear down the working link before the replacement verifies
(make-before-break). Pins the pure selection logic behind those laws."""
import importlib.util
import time
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "vpn_rotate", Path(__file__).resolve().parent.parent / "tools" / "surfshark_vpn_rotate.py")
vpn = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vpn)


def test_slot_rank_prefers_uk_sg_never_nl_us():
    assert vpn.slot_rank("uk-lon-st001.prod.surfshark.com") == 1
    assert vpn.slot_rank("sg-sng-st007.prod.surfshark.com") == 2
    assert vpn.slot_rank("nl-ams-st001.prod.surfshark.com") is None
    assert vpn.slot_rank("us-nyc-st001.prod.surfshark.com") is None
    assert vpn.slot_rank("de-fra-st001.prod.surfshark.com") == 3


def _fake_env(monkeypatch, fails):
    monkeypatch.setattr(vpn, "slots", lambda: [
        {"name": "Surfshark. Dausos", "status": "Disconnected"},
        {"name": "Surfshark. WireGuard", "status": "Disconnected"},
    ])
    monkeypatch.setattr(vpn, "slot_server", lambda n: ("uk-lon-st002.prod.surfshark.com", None))
    return {"slot_fail": fails, "slot_exit": {}}


def test_eligible_skips_recent_fail_but_not_old(monkeypatch):
    now = time.time()
    state = _fake_env(monkeypatch, {"Surfshark. Dausos": now - 60})
    got = [s["name"] for s in vpn.eligible_slots(set(), state, None)]
    assert got == ["Surfshark. WireGuard"]
    state["slot_fail"]["Surfshark. Dausos"] = now - vpn.FAIL_SKIP_S - 1
    got = [s["name"] for s in vpn.eligible_slots(set(), state, None)]
    assert sorted(got) == ["Surfshark. Dausos", "Surfshark. WireGuard"]


def test_eligible_honors_skip_name(monkeypatch):
    state = _fake_env(monkeypatch, {})
    got = [s["name"] for s in vpn.eligible_slots(set(), state, "Surfshark. Dausos")]
    assert got == ["Surfshark. WireGuard"]


def test_fail_skip_window_is_minutes_not_hours():
    assert vpn.FAIL_SKIP_S <= 600


def test_scan_trips_on_fresh_ban_line(tmp_path, monkeypatch):
    import json
    import os
    f = tmp_path / "ez_manage_fin.log"
    f.write_text("24 06:00:00 - ERROR - [fin] Failed: APIError(code=-1003): Way too many requests\n")
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


def test_tour_walks_ring_skipping_current():
    order = ["Surfshark. Dausos", "Surfshark. WireGuard", "(no-vpn)"]
    assert vpn._tour_next("Surfshark. Dausos", order) == "Surfshark. WireGuard"
    assert vpn._tour_next("(no-vpn)", order) == "Surfshark. Dausos"
    assert vpn._tour_next("Surfshark. Dausos", ["Surfshark. Dausos"]) is None
    assert vpn._tour_next("x", []) is None
