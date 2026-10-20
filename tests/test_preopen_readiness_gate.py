#!/usr/bin/env python3
"""Tests for the 2026-10-20 pre-open contract: tools/preopen_readiness_gate.py,
tools/golive_delta_selection.py and tools/live_failover_stability_pin.py.

Each test pins one of the failures measured that morning, so the next regression fails here first:
  * six `timeout 5400` kills left no go-live report and stale size tiers -> apply_report_written
  * the daily chain died at step 2 every day since 10-18 -> defaults_chain_completed
  * 159/474 published boards diverged materially from the live store -> live_set_matches_selection
  * S1 held crypto with entries refused as STALE_INDICATORS for 259 min -> indicators_fresh / s1_capable
  * tradier_manage tra restarted 11x in 300 s while fenced -> churning()
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import golive_delta_selection as delta  # noqa: E402
import live_failover_stability_pin as pin  # noqa: E402
import preopen_readiness_gate as gate  # noqa: E402

OPEN_EPOCH = 1792503000.0  # 2026-10-20T13:30:00Z, the session this all broke in
NOW = OPEN_EPOCH - 600


def test_artefact_and_ablation_divergences_are_benign():
    assert gate.classify_divergence("AAPL_LONG", "MAX_AUGMENTS_PER_POSITION", 0, 999999, True, {"MAX_AUGMENTS_PER_POSITION"}, set(), {}) == "benign_artefact"
    assert gate.classify_divergence("AAPL_LONG", "ABLATION_DISABLE_HEDGE", False, None, False, set(), set(), {}) == "benign_artefact"
    assert gate.classify_divergence("AAPL_LONG", "REENTRY_TIER2_MAX_MINUTES_TRADIER", 5, None, False, set(), {"REENTRY_TIER2_MAX_MINUTES_TRADIER"}, {}) == "benign_migrated"


def test_crypto_sizing_cap_is_benign_only_when_it_clamps_down():
    caps = {"START_POSITION_SIZE": 6.0}
    assert gate.classify_divergence("DUSKUSDT_LONG", "START_POSITION_SIZE", 28.0, 25.0, True, set(), set(), caps) == "benign_sizing_cap"
    assert gate.classify_divergence("DUSKUSDT_LONG", "START_POSITION_SIZE", 10.0, 40.0, True, set(), set(), caps) == "material"
    assert gate.classify_divergence("AAPL_LONG", "START_POSITION_SIZE", 28.0, 25.0, True, set(), set(), caps) == "material"


def test_real_strategy_switch_is_always_material():
    for switch, want, got in (("ADX_REGIME_FILTER_ENABLED", True, False), ("BB_BOUNCE_ENTRY_TF", "1h", "OFF"), ("COOLDOWN_BARS", 3, 0)):
        assert gate.classify_divergence("RS_LONG", switch, want, got, True, {"MODE"}, set(), {"START_POSITION_SIZE": 6.0}) == "material"


def test_apply_report_fails_when_the_applier_was_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "CHAIN_DIR", tmp_path / "chain")
    monkeypatch.setattr(gate, "TIERS", tmp_path / "tiers.json")
    (tmp_path / "chain").mkdir()
    check = gate.check_apply_report(OPEN_EPOCH, NOW)
    assert not check["ok"] and "PARTIAL" in check["detail"]


def test_apply_report_fails_on_a_report_without_fresh_tiers(tmp_path, monkeypatch):
    chain = tmp_path / "chain"
    chain.mkdir()
    report = chain / "persym_golive_20261020_preopen_1140.json"
    report.write_text("{}")
    os.utime(report, (OPEN_EPOCH - 3000, OPEN_EPOCH - 3000))
    tiers = tmp_path / "tiers.json"
    tiers.write_text("{}")
    os.utime(tiers, (OPEN_EPOCH - 200000, OPEN_EPOCH - 200000))
    monkeypatch.setattr(gate, "CHAIN_DIR", chain)
    monkeypatch.setattr(gate, "TIERS", tiers)
    assert not gate.check_apply_report(OPEN_EPOCH, NOW)["ok"]
    os.utime(tiers, (OPEN_EPOCH - 3000, OPEN_EPOCH - 3000))
    assert gate.check_apply_report(OPEN_EPOCH, NOW)["ok"]


def test_defaults_chain_fails_when_no_done_stamp_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "CHAIN_DIR", tmp_path / "data" / "daily_chain")
    (tmp_path / "data" / "daily_chain").mkdir(parents=True)
    check = gate.check_defaults_chain(OPEN_EPOCH, NOW, host=None)
    assert not check["ok"] and "step 2 never finished" in check["detail"]


def test_defaults_chain_passes_on_a_done_stamp_despite_an_old_cat_side_layer(tmp_path, monkeypatch):
    """--sync-defaults is a deliberate manual step, so an aged cat_side layer must NOT fail the gate:
    a permanent FAIL here would pin live trading to S1 for good through the stability pin."""
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    chain = tmp_path / "data" / "daily_chain"
    chain.mkdir(parents=True)
    monkeypatch.setattr(gate, "CHAIN_DIR", chain)
    (chain / "20261020.json").write_text('{"date":"20261020","status":"OK"}')
    catside = tmp_path / "data" / "cat_side_defaults_4.json"
    catside.write_text("{}")
    os.utime(catside, (OPEN_EPOCH - 6 * 86400, OPEN_EPOCH - 6 * 86400))
    check = gate.check_defaults_chain(OPEN_EPOCH, NOW, host=None)
    assert check["ok"] and check["measured"]["cat_side_age_days"] >= 6.0


def test_indicators_check_uses_the_live_90_minute_entry_gate(tmp_path, monkeypatch):
    fresh = "2026-10-20T13:15:00.000000Z"
    stale = "2026-10-20T09:30:00.000000Z"
    snapshot = {"_meta": {"x": 1}}
    snapshot.update({f"OK{i}USDC": {"timestamp_15m": fresh} for i in range(18)})
    snapshot["BADUSDC"] = {"timestamp_15m": stale}
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(snapshot))
    monkeypatch.setattr(gate, "SNAPSHOT", path)
    check = gate.check_indicators(OPEN_EPOCH + 60)
    assert check["ok"] and check["measured"]["stale_over_90min"] == 1
    snapshot.update({f"BAD{i}USDC": {"timestamp_15m": stale} for i in range(8)})
    path.write_text(json.dumps(snapshot))
    assert not gate.check_indicators(OPEN_EPOCH + 60)["ok"]


def test_a_fresh_tick_rescues_a_stale_bar_exactly_as_live_does(tmp_path, monkeypatch):
    path = tmp_path / "snap.json"
    now = OPEN_EPOCH + 60
    path.write_text(json.dumps({"XUSDC": {"timestamp_15m": "2026-10-20T09:30:00.000000Z", "_tick_ts": now - 10}}))
    monkeypatch.setattr(gate, "SNAPSHOT", path)
    assert gate.check_indicators(now)["measured"]["stale_over_90min"] == 0


def _selection(tmp_path, rows):
    path = tmp_path / "published_selection.json"
    path.write_text(json.dumps({"_meta": {"n": len(rows), "built_at": "2026-10-20T12:52:16Z"}, "sym_sides": rows}))
    return path


def test_delta_holds_only_unapplied_keys_newest_first(tmp_path, monkeypatch):
    rows = {
        "LIVE_LONG": {"published_at": "2026-10-20T01:00:00Z", "overrides": {"A": 1}},
        "OLD_SHORT": {"published_at": "2026-10-20T02:00:00Z", "overrides": {"A": 1}},
        "NEW_LONG": {"published_at": "2026-10-20T12:45:00Z", "overrides": {"A": 1}},
        "GONE_LONG": {"published_at": "2026-10-20T03:00:00Z", "overrides": {"A": 1}},
    }
    path = _selection(tmp_path, rows)
    live = {"LIVE_LONG": {"A": 1}, "OLD_SHORT": {"A": 2}, "NEW_LONG": {"A": 2}}
    monkeypatch.setattr(gate, "_benign_classifier", lambda: (set(), None, {}))
    monkeypatch.setitem(sys.modules, "per_sym_live_set", type("M", (), {"full_set": staticmethod(lambda k: live.get(k))}))
    out = tmp_path / "delta.json"
    meta = delta.build(path, out, include_missing=True)
    payload = json.loads(out.read_text())
    assert meta["n"] == 3 and meta["exact"] == 1
    assert list(payload["sym_sides"]) == ["NEW_LONG", "GONE_LONG", "OLD_SHORT"]
    assert payload["sym_sides"]["NEW_LONG"] == rows["NEW_LONG"]


def test_delta_is_empty_when_live_already_matches(tmp_path, monkeypatch):
    path = _selection(tmp_path, {"A_LONG": {"published_at": "2026-10-20T01:00:00Z", "overrides": {"S": True}}})
    monkeypatch.setattr(gate, "_benign_classifier", lambda: (set(), None, {}))
    monkeypatch.setitem(sys.modules, "per_sym_live_set", type("M", (), {"full_set": staticmethod(lambda k: {"S": True})}))
    meta = delta.build(path, tmp_path / "delta.json", include_missing=True)
    assert meta["n"] == 0


def test_churn_markers_inside_the_quiet_window_block_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(pin, "LOGDIR", tmp_path)
    monkeypatch.setattr(pin, "CHURN_QUIET_S", 600.0)
    old = tmp_path / ".restart_alert_ez_manage_ang"
    old.write_text("x")
    os.utime(old, (time.time() - 5000, time.time() - 5000))
    assert pin.churning()[0] is False
    fresh = tmp_path / ".restart_alert_tradier_manage_tra"
    fresh.write_text("x")
    assert pin.churning()[0] is True
    unrelated = tmp_path / ".restart_alert_chart_server"
    unrelated.write_text("x")
    os.utime(fresh, (time.time() - 5000, time.time() - 5000))
    assert pin.churning()[0] is False


def _write_readiness(tmp_path, monkeypatch, verdict, ind_ok=True):
    path = tmp_path / "preopen_readiness.json"
    path.write_text(json.dumps({
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verdict": verdict,
        "failed_critical": [] if verdict == "PASS" else ["apply_report_written"],
        "checks": [{"name": "indicators_fresh", "ok": ind_ok, "detail": "x", "measured": {}}],
    }))
    monkeypatch.setattr(pin, "READINESS", path)
    return path


def test_the_pin_is_read_from_s1_not_from_the_mac_mirror(tmp_path, monkeypatch):
    """The Mac mirror has no `pin` key at all, so reading it there made the un-pin branch unreachable."""
    lease = tmp_path / "lease_local.json"
    lease.write_text(json.dumps({"holder": "s1", "gen": 70, "venues": "crypto,stocks"}))
    monkeypatch.setattr(pin, "LEASE", lease)
    monkeypatch.setattr(pin, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(pin, "LOGDIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    _write_readiness(tmp_path, monkeypatch, "PASS")
    monkeypatch.setattr(pin, "LOAD_MAX", 10000.0)
    monkeypatch.setattr(pin, "current_pin", lambda: ("s1", "s1:lease.json"))
    monkeypatch.setattr(pin, "STABLE_S", 0.0)
    payload = pin.tick(apply_change=False)
    assert payload["lease"]["pin"] == "s1" and payload["action"] == "unpin_auto"


def test_pin_is_not_moved_to_a_blind_s1(tmp_path, monkeypatch):
    _write_readiness(tmp_path, monkeypatch, "FAIL")
    lease = tmp_path / "lease_local.json"
    lease.write_text(json.dumps({"holder": "mac", "gen": 65}))
    monkeypatch.setattr(pin, "LEASE", lease)
    monkeypatch.setattr(pin, "current_pin", lambda: (None, "test"))
    monkeypatch.setattr(pin, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(pin, "LOGDIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(pin, "LOAD_MAX", 10000.0)
    monkeypatch.setattr(pin, "s1_capable", lambda: (False, "s1-pub indicators 101/199 stale (50.8%)"))
    called = []
    monkeypatch.setattr(pin, "set_pin", lambda value, apply_change: called.append(value))
    payload = pin.tick(apply_change=True)
    assert payload["action"] == "NEITHER_HOST_CAPABLE" and called == []


def test_unready_mac_pins_a_capable_s1(tmp_path, monkeypatch):
    _write_readiness(tmp_path, monkeypatch, "FAIL")
    lease = tmp_path / "lease_local.json"
    lease.write_text(json.dumps({"holder": "mac", "gen": 65}))
    monkeypatch.setattr(pin, "LEASE", lease)
    monkeypatch.setattr(pin, "current_pin", lambda: (None, "test"))
    monkeypatch.setattr(pin, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(pin, "LOGDIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(pin, "LOAD_MAX", 10000.0)
    monkeypatch.setattr(pin, "s1_capable", lambda: (True, "ok"))
    payload = pin.tick(apply_change=False)
    assert payload["action"] == "pin_s1" and payload["action_result"]["would"] == "force-s1"


def test_a_ready_mac_is_unpinned_only_after_the_stable_dwell(tmp_path, monkeypatch):
    _write_readiness(tmp_path, monkeypatch, "PASS")
    lease = tmp_path / "lease_local.json"
    lease.write_text(json.dumps({"holder": "s1", "gen": 66}))
    state = tmp_path / "state.json"
    monkeypatch.setattr(pin, "LEASE", lease)
    monkeypatch.setattr(pin, "current_pin", lambda: ("s1", "test"))
    monkeypatch.setattr(pin, "STATE", state)
    monkeypatch.setattr(pin, "LOGDIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(pin, "LOAD_MAX", 10000.0)
    monkeypatch.setattr(pin, "STABLE_S", 900.0)
    first = pin.tick(apply_change=False)
    assert first["mac_ready"] is True and first["action"] is None
    state.write_text(json.dumps({"ready_since": time.time() - 1200, "last_action_at": 0}))
    second = pin.tick(apply_change=False)
    assert second["action"] == "unpin_auto" and second["action_result"]["would"] == "auto"


def test_readiness_report_older_than_the_window_is_not_trusted(tmp_path, monkeypatch):
    path = _write_readiness(tmp_path, monkeypatch, "PASS")
    path.write_text(json.dumps({
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7200)),
        "verdict": "PASS",
        "failed_critical": [],
        "checks": [],
    }))
    monkeypatch.setattr(pin, "LOGDIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(pin, "LOAD_MAX", 10000.0)
    ready, reasons, _ = pin.mac_readiness()
    assert ready is False and any("old" in r for r in reasons)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
