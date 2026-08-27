from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tools.opt import lifecycle_pilot as P


def _npz(days, bars_per_day=2):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stamps = []
    values = []
    for day in days:
        for bar in range(bars_per_day):
            stamps.append((start + timedelta(days=day, minutes=5 * bar)).timestamp() * 1000)
            values.append(day * 10 + bar)
    return {"timestamps": np.array(stamps), "close": np.array(values, dtype=float), "constant": "x"}


def test_crypto_window_is_exactly_30_frozen_calendar_days():
    sliced, meta = P.exact_month_slice(_npz(range(41)), crypto=True)
    seconds = sliced["timestamps"] / 1000
    assert meta["policy"] == "30_calendar_days"
    assert seconds[-1] - seconds[0] <= 30 * 86400
    assert len(sliced["close"]) == 61


def test_stock_window_is_last_20_distinct_sessions():
    sessions = [day for day in range(40) if day % 7 not in (2, 3)]
    sliced, meta = P.exact_month_slice(_npz(sessions), crypto=False)
    unique = np.unique((sliced["timestamps"] / 1000).astype("datetime64[s]").astype("datetime64[D]"))
    assert meta["policy"] == "20_trading_sessions"
    assert len(unique) == 20
    assert len(sliced["close"]) == 40


def test_completed_parent_clock_uses_first_causally_available_row():
    base = np.arange(306) * 300 + 1_700_000_000
    parent = np.repeat(np.arange(102) * 900 + 1_700_000_000, 3)
    npz = {"timestamps": base, "timestamp_15m": parent, "close": np.arange(306.0)}
    compact, meta = P.compact_to_completed_timeframe(npz)
    assert list(compact["close"][:4]) == [0.0, 3.0, 6.0, 9.0]
    assert meta["execution_bars"] == 102


def test_ratchet_rejects_inert_and_negative_delta():
    base = {"valid": True, "score": 1.0, "delta_vs_bh": 1.0, "max_dd_pct": 10,
            "tim_pct": 50, "behavior_fingerprint": "same"}
    inert = {**base, "score": 9.0}
    negative = {**base, "score": 9.0, "delta_vs_bh": -0.1, "behavior_fingerprint": "new"}
    good = {**base, "score": 2.0, "delta_vs_bh": 2.0, "behavior_fingerprint": "new",
            "pool_sharpe": 0.3, "trades": 32}
    assert not P.improves(inert, base)
    assert not P.improves(negative, base)
    assert P.improves(good, base)


def test_research_ratchet_can_build_tim_before_final_gate():
    incumbent = {"valid": True, "score": 1.0, "delta_vs_bh": 1.0,
                 "max_dd_pct": 10, "tim_pct": 1.0,
                 "pool_sharpe": 0.0, "trades": 2,
                 "behavior_fingerprint": "old"}
    useful_step = {**incumbent, "score": 2.0, "delta_vs_bh": 2.0,
                   "tim_pct": 5.0, "trades": 8,
                   "behavior_fingerprint": "new"}
    assert P.ratchet_improves(useful_step, incumbent)
    assert not P.improves(useful_step, incumbent)


def test_stage_fails_closed_without_verified_immutable_receipt(tmp_path):
    summary = {"symside": "BTCUSDC_LONG", "baseline": {"valid": True},
               "final": {"valid": True}, "final_overrides": {}}
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summary))
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps({"verified": False}))
    with pytest.raises(ValueError, match="not verified"):
        P.stage_promotion(summary_path, receipt_path)


def test_stage_rejects_legacy_v8_receipt_before_touching_live_files(tmp_path):
    summary = {"symside": "BTCUSDC_LONG", "baseline": {"valid": True},
               "final": {"valid": True}, "final_overrides": {}}
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summary))
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps({
        "verified": True,
        "engine": "backtest_v8_engine.py",
        "summary_sha256": P.hashlib.sha256(summary_path.read_bytes()).hexdigest(),
    }))
    with pytest.raises(ValueError, match="backtest_v12_engine"):
        P.stage_promotion(summary_path, receipt_path)


def test_child_patch_enables_complete_parent_chain():
    trial = P.SwitchTrial("DELTA_ENTRY_ACCEL_THRESHOLD", 2.0, "ENTRY", "ENTRY:DELTA",
                          ("DELTA_ENTRY_ENABLED", "ENTRY_TECHNICAL_ENABLED"))
    assert trial.patch() == {"DELTA_ENTRY_ENABLED": True,
                             "ENTRY_TECHNICAL_ENABLED": True,
                             "DELTA_ENTRY_ACCEL_THRESHOLD": 2.0}


def test_entry_roots_toggle_live_state_and_order_ablations_first(monkeypatch):
    monkeypatch.setattr(P, "_config_fields_cached", lambda: {
        "NORMAL_ENTRY_ENABLED": False,
        "ABLATION_DISABLE_ENTRY_X": True,
    })
    trials = [
        P.SwitchTrial("NORMAL_ENTRY_ENABLED", True, "ENTRY", "ENTRY:NORMAL"),
        P.SwitchTrial("ABLATION_DISABLE_ENTRY_X", False, "ENTRY", "ENTRY:ABLATION"),
    ]
    roots = P._entry_activations(trials, "BTCUSDC_LONG", {
        "NORMAL_ENTRY_ENABLED": False,
        "ABLATION_DISABLE_ENTRY_X": True,
    })
    assert [(root.name, root.value) for root in roots] == [
        ("ABLATION_DISABLE_ENTRY_X", False),
        ("NORMAL_ENTRY_ENABLED", True),
    ]


def test_setting_binding_is_exact_global_or_unresolved(monkeypatch):
    entries = [
        P.SwitchTrial("ALPHA_ENTRY_ENABLED", True, "ENTRY", "ENTRY:ALPHA"),
        P.SwitchTrial("BETA_ENTRY_ENABLED", True, "ENTRY", "ENTRY:BETA"),
    ]
    settings = [
        P.SwitchTrial("ALPHA_ENTRY_THRESHOLD", 2, "ENTRY", "ENTRY:ALPHA",
                      ("ALPHA_ENTRY_ENABLED",)),
        P.SwitchTrial("SHARED_ENTRY_FILTER", True, "FILTER", "FILTER:SHARED"),
        P.SwitchTrial("MYSTERY_LIMIT", 3, "FILTER", "FILTER:MYSTERY"),
    ]
    monkeypatch.setattr(P, "_registry_sources", lambda: ({}, {
        "SHARED_ENTRY_FILTER": {"lifecycle_consumers": "ENTRY|REENTRY"},
    }, {}))
    monkeypatch.setattr(P, "_all_path_metadata", lambda: {})
    monkeypatch.setattr(P, "_catalog_metadata", lambda: {})
    by_entry, owners, scopes, global_settings, unresolved = P._entry_setting_bindings(entries, settings)
    assert scopes["ALPHA_ENTRY_THRESHOLD"] == "SOME"
    assert owners["ALPHA_ENTRY_THRESHOLD"] == {"ALPHA_ENTRY_ENABLED"}
    assert [trial.name for trial in by_entry["ALPHA_ENTRY_ENABLED"]] == ["ALPHA_ENTRY_THRESHOLD"]
    assert scopes["SHARED_ENTRY_FILTER"] == "ANY"
    assert {trial.name for trial in global_settings} == {"SHARED_ENTRY_FILTER"}
    assert scopes["MYSTERY_LIMIT"] == "UNRESOLVED"
    assert {trial.name for trial in unresolved} == {"MYSTERY_LIMIT"}


def test_subsettings_are_nested_below_individual_switch(monkeypatch):
    monkeypatch.setattr(P, "_config_fields_cached", lambda: {"CHILD_ENABLED": False, "CHILD_LIMIT": 1})
    monkeypatch.setattr(P, "_catalog_metadata", lambda: {
        "CHILD_LIMIT": {"main_switch": "CHILD_ENABLED", "activation_dependencies": ["ROOT", "CHILD_ENABLED"]}
    })
    children = [
        P.SwitchTrial("CHILD_ENABLED", True, "ENTRY", "ENTRY:X", ("ROOT",)),
        P.SwitchTrial("CHILD_LIMIT", 2, "ENTRY", "ENTRY:X", ("CHILD_ENABLED", "ROOT")),
    ]
    tree = P._nested_setting_nodes("ROOT", children,
                                   {"CHILD_ENABLED": "SOME", "CHILD_LIMIT": "SOME"},
                                   {"CHILD_ENABLED": {"ROOT"}, "CHILD_LIMIT": {"ROOT"}})
    assert [node["name"] for node in tree] == ["CHILD_ENABLED"]
    assert [node["name"] for node in tree[0]["children"]] == ["CHILD_LIMIT"]
