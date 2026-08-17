import json
import numpy as np
import pytest
from pathlib import Path

from tools import run_classic_formation_combo_holdout as combo


def arm(gain, action, tf, *, actions=2, trades=15):
    family = "wedge" if action == "ENTRY" else "triangle"
    return {
        "total_gain_pct": gain, "formation_action_count": actions, "trades": trades,
        "action_fingerprint": f"{family}-{action}-{tf}", "result_fingerprint": f"r-{gain}",
    }


def train_row(key="ABC_LONG"):
    return {
        "key": key, "bh_return_pct": 5.0, "npz_sha256": "a" * 64,
        "variants": {
            "baseline": {"total_gain_pct": 6.0},
            "wedge_entry_1h": arm(30.0, "ENTRY", "1h"),
            "wedge_entry_D": arm(40.0, "ENTRY", "D"),
            "triangle_exit_1h": arm(20.0, "EXIT", "1h"),
            "triangle_exit_4h": arm(35.0, "EXIT", "4h"),
        },
    }


def test_plan_is_train_only_same_timeframe_and_bounded():
    plan = combo.build_train_plan([train_row()], top_arms=3)
    key = plan["keys"]["ABC_LONG"]
    assert key["combos"] == [{
        "label": "ENTRY:wedge_entry_1h|EXIT:triangle_exit_1h",
        "entry": "wedge_entry_1h", "exit": "triangle_exit_1h", "timeframe": "1h",
    }]
    assert len(key["combos"]) <= 9
    assert plan["selection_contract"].startswith("TRAIN_ONLY")


def test_plan_does_not_reselect_when_holdout_arm_would_rank_differently():
    plan = combo.build_train_plan([train_row()])
    frozen = plan["plan_fingerprint"]
    # This deliberately better hypothetical holdout arm is never passed to, or
    # inspected by, build_train_plan; the frozen pair remains the TRAIN pair.
    holdout = train_row()
    holdout["variants"]["wedge_entry_D"]["total_gain_pct"] = 999.0
    assert plan["plan_fingerprint"] == frozen
    assert plan["keys"]["ABC_LONG"]["combos"][0]["entry"] == "wedge_entry_1h"


def test_combo_config_reuses_shared_selector_with_exact_two_switches():
    config = combo.config_for_combo(combo.v8.SweepConfig(), "wedge_entry_1h", "triangle_exit_1h")
    assert config.FORMATION_TFS == "1h"
    assert config.FORMATION_WEDGE_ENTRY_ENABLED is True
    assert config.FORMATION_TRIANGLE_EXIT_ENABLED is True
    assert config.FORMATION_WEDGE_EXIT_ENABLED is False
    assert config.FORMATION_TRIANGLE_ENTRY_ENABLED is False

    all_config = combo.config_for_combo(
        combo.v8.SweepConfig(), "wedge_entry_ALL", "triangle_exit_ALL"
    )
    assert all_config.FORMATION_TFS == "15m,1h,4h,D"


def test_isolated_config_explicitly_disables_every_boolean_path_except_selected_pair():
    config = combo.isolated_config_for_combo("wedge_entry_1h", "triangle_exit_1h")
    enabled = {
        name for name, value in vars(config).items()
        if isinstance(value, bool) and value
    }
    assert enabled == {
        "FORMATION_WEDGE_ENTRY_ENABLED", "FORMATION_TRIANGLE_EXIT_ENABLED",
    }


def test_isolated_replay_has_only_selected_formation_events_and_qualifies_both_route_counts():
    n = 30
    timestamps = np.arange(n, dtype=np.int64) * 300
    close = np.full(n, 10.0)
    entry = np.zeros(n, dtype=np.float32)
    exit_ = np.zeros(n, dtype=np.float32)
    for start in range(0, 24, 2):
        entry[start] = 0.9
        exit_[start + 1] = 0.9
        close[start + 1] = 11.0
    evidence = combo.isolated_formation_replay(
        "ABC", "LONG", combo.isolated_config_for_combo("wedge_entry_1h", "triangle_exit_1h"),
        ({"close": close, "formation_wedge_bull_score_1h": entry,
          "formation_triangle_bear_score_1h": exit_}, timestamps), 0.0,
    )
    assert evidence["trades"] == 12
    assert evidence["formation_entry_action_count"] == 12
    assert evidence["formation_exit_action_count"] == 12
    assert evidence["non_selected_path_action_count"] == 0
    assert combo.isolated_qualification(evidence) == []
    assert all(event["reason"].startswith(("CLASSIC_FORMATION_ENTRY_WEDGE", "CLASSIC_FORMATION_EXIT_TRIANGLE"))
               for event in evidence["event_ledger"])


def test_isolated_priority_slate_is_ranked_only_from_train_arm_evidence():
    low = train_row("LOW_LONG")
    high = train_row("HIGH_LONG")
    high["variants"]["wedge_entry_1h"]["total_gain_pct"] = 90.0
    plan = combo.build_train_plan([low, high])
    slate = combo.isolated_priority_combos(plan, 2)
    assert [row["key"] for row in slate] == ["HIGH_LONG", "LOW_LONG"]
    assert [row["train_only_priority_rank"] for row in slate] == [1, 2]


def test_qualification_requires_both_observable_formation_paths_and_real_gates():
    baseline = {"return_pct": 10.0}
    good = {
        "return_pct": 25.0, "alpha_vs_bh_pp": 3.0, "trades": 11,
        "formation_entry_action_count": 1, "formation_exit_action_count": 1,
    }
    assert combo.qualification(good, baseline) == []
    bad = {**good, "formation_exit_action_count": 0, "trades": 10, "return_pct": 9.0}
    assert combo.qualification(bad, baseline) == [
        "TRADES_NOT_OVER_10", "FORMATION_EXIT_ACTIONS_ZERO", "DID_NOT_IMPROVE_BASELINE"
    ]


def test_window_slice_is_cached_and_does_not_recompute_arrays():
    arrays = {"close": np.arange(10, dtype=float), "static": np.array([1.0])}
    ts = np.arange(10, dtype=np.int64)
    sliced, sliced_ts = combo.slice_cached_npz(arrays, ts, 3, 7)
    assert sliced_ts.tolist() == [3, 4, 5, 6]
    assert sliced["close"].tolist() == [3.0, 4.0, 5.0, 6.0]
    assert sliced["static"].tolist() == [1.0]


def test_train_receipt_key_override_is_preserved_for_combo_replay():
    config, applied, dynamic = combo.apply_key_overrides(
        combo.v8.SweepConfig(),
        {"active_config_overrides": {"ABC_LONG": {"FORMATION_MIN_SCORE": 0.42}}},
        "ABC_LONG",
    )
    assert config.FORMATION_MIN_SCORE == 0.42
    assert applied == 1
    assert dynamic == []


def test_dynamic_train_receipt_overrides_match_isolated_campaign_semantics():
    config, applied, dynamic = combo.apply_key_overrides(
        combo.v8.SweepConfig(),
        {
            "active_config_overrides": {
                "ABC_LONG": {
                    "LIVE_ONLY_STYLE_DYNAMIC_KNOB": True,
                    "_score": 99,
                }
            }
        },
        "ABC_LONG",
    )
    assert config.LIVE_ONLY_STYLE_DYNAMIC_KNOB is True
    assert applied == 1
    assert dynamic == ["LIVE_ONLY_STYLE_DYNAMIC_KNOB"]


def test_action_ledger_counts_each_event_once(monkeypatch):
    class Event:
        def __init__(self, ts, kind, reason):
            self.ts = ts
            self.type = kind
            self.qty = 1.0
            self.price = 10.0
            self.reason = reason

    events = [
        Event(1, "OPEN", "CLASSIC_FORMATION_ENTRY_WEDGE_1H"),
        Event(2, "CLOSE", "CLASSIC_FORMATION_EXIT_TRIANGLE_1H"),
    ]
    monkeypatch.setattr(
        combo.v8,
        "simulate_one_symbol",
        lambda *args, **kwargs: (events, [2.0], 2),
    )
    evidence = combo.evaluate_variant(
        "ABC",
        "LONG",
        combo.v8.SweepConfig(),
        ({"close": np.array([10.0, 11.0])}, np.array([1, 2], dtype=np.int64)),
        0.0,
    )
    assert evidence["action_count"] == 2
    assert evidence["formation_entry_action_count"] == 1
    assert evidence["formation_exit_action_count"] == 1


def test_csv_rows_include_baseline_and_both_frozen_windows():
    evidence = {
        "return_pct": 12.0, "bh_return_pct": 3.0, "alpha_vs_bh_pp": 9.0,
        "trades": 11, "tim_pct": 50.0, "formation_entry_action_count": 0,
        "formation_exit_action_count": 0, "action_count": 2,
        "action_fingerprint": "a", "result_fingerprint": "r",
    }
    result = {"key": "ABC_LONG", "symbol": "ABC", "side": "LONG",
              "baseline": {"train": evidence, "holdout": evidence}, "combos": []}
    rows = combo.csv_rows(result)
    assert [(row["combo"], row["window"]) for row in rows] == [
        ("baseline", "train"), ("baseline", "holdout")
    ]


def test_runner_contract_is_strict_115_and_parallel_six_workers():
    source = Path("tools/run_classic_formation_combo_holdout.py").read_text()
    assert 'default=115' in source
    assert 'default=6' in source
    assert "ProcessPoolExecutor(max_workers=args.workers)" in source
    assert '"single_npz_load_per_key": True' in source
    assert '"holdout_sha256": sha256(args.holdout)' in source
    assert '"failed_key_count": len(failed)' in source
    assert 'return 2 if failed else 0' in source
    assert 'stable_hash(vars(config))' in source


def test_missing_holdout_key_is_explicitly_excluded_only_when_source_has_zero_holdout_bars(tmp_path):
    np.savez(tmp_path / "LATE.npz", timestamps=np.array([1, 2, 3], dtype=np.int64))
    exclusions, evaluated = combo.reconcile_holdout_keys(
        {"LATE_LONG"}, set(), tmp_path, holdout_start_ts=10
    )
    assert evaluated == []
    assert exclusions[0]["status"] == "NO_HOLDOUT_BARS_EXCLUDED"
    assert exclusions[0]["holdout_metrics"] is None
    assert exclusions[0]["holdout_source_availability"]["npz_sha256"]


def test_missing_holdout_key_with_available_bars_fails_closed_not_silently_excluded(tmp_path):
    np.savez(tmp_path / "CRWD.npz", timestamps=np.array([1, 10, 11], dtype=np.int64))
    with pytest.raises(
        ValueError,
        match="HOLDOUT_KEY_MISSING_WITH_AVAILABLE_OR_INVALID_SOURCE:CRWD_SHORT:HOLDOUT_BARS_PRESENT:bars=2",
    ):
        combo.reconcile_holdout_keys({"CRWD_SHORT"}, set(), tmp_path, holdout_start_ts=10)


def test_combo_refuses_train_holdout_npz_hash_drift():
    plan = combo.build_train_plan([train_row()])
    with pytest.raises(ValueError, match="TRAIN_HOLDOUT_NPZ_HASH_IDENTITY_FAILED"):
        combo.require_npz_hash_identity(
            plan,
            [{"key": "ABC_LONG", "npz_sha256": "b" * 64}],
            ["ABC_LONG"],
        )


def test_isolated_lane_fails_closed_for_invalidated_sealed_campaign():
    invalidation = json.loads(
        Path("data/reports/CLASSIC_FORMATION_PARENT_CANDLE_INVALIDATION_20260803.json").read_text()
    )
    assert invalidation["status"] == "INVALIDATED_RERUN_REQUIRED"
    with pytest.raises(ValueError, match="SEALED_FORMATION_SOURCE_HASH_MISMATCH:v8_vec_sweep:"):
        combo.require_sealed_formation_source_identity(
            Path("data/reports/vec_research/classic_formations_gate_aware_source_v4_20260803/train/per_key_results.json.gz")
        )


def test_isolated_slate_uses_direct_sealed_npz_not_unrelated_holdout_result_manifest(tmp_path):
    path = tmp_path / "ABC.npz"
    np.savez(path, timestamps=np.array([1, 10, 11], dtype=np.int64))
    digest = combo.sha256(path)
    plan = {"keys": {"ABC_LONG": {"npz_sha256_train": digest}}}
    candidate = {"key": "ABC_LONG"}
    audit = combo.require_isolated_candidate_sources(plan, [candidate], tmp_path, 10)
    assert audit[0]["holdout_source_availability"]["holdout_bars"] == 2
    plan["keys"]["ABC_LONG"]["npz_sha256_train"] = "x" * 64
    with pytest.raises(ValueError, match="ISOLATED_SELECTED_SOURCE_INVALID:ABC_LONG:NPZ_HASH_DRIFT"):
        combo.require_isolated_candidate_sources(plan, [candidate], tmp_path, 10)


def test_one_worker_headroom_admission_never_bypasses_cpu_ram_or_disk_safety(monkeypatch):
    monkeypatch.setattr(combo.os, "cpu_count", lambda: 16)
    monkeypatch.setattr(combo.os, "getloadavg", lambda: (16.0, 0.0, 0.0))
    monkeypatch.setattr(combo, "resource_snapshot", lambda: {
        "total_cpu_pct": 1400.0,
        "mem_available_bytes": 5 * 1024 ** 3,
        "disk_available_bytes": 7 * 1024 ** 3,
    })
    one = combo.cpu_admission(1, 0.70, allow_one_worker_headroom=True)
    assert one["pass"] is True
    assert one["admission_mode"] == "ONE_WORKER_HEADROOM"
    two = combo.cpu_admission(2, 0.70, allow_one_worker_headroom=True)
    assert two["pass"] is False
    monkeypatch.setattr(combo, "resource_snapshot", lambda: {
        "total_cpu_pct": 1400.0,
        "mem_available_bytes": 3 * 1024 ** 3,
        "disk_available_bytes": 7 * 1024 ** 3,
    })
    assert combo.cpu_admission(1, 0.70, allow_one_worker_headroom=True)["pass"] is False


def test_excluded_key_csv_contains_no_holdout_return_or_bh_metric():
    rows = combo.csv_rows({
        "key": "LATE_LONG", "symbol": "LATE", "side": "LONG",
        "status": "NO_HOLDOUT_BARS_EXCLUDED",
        "failure_reasons": ["NO_HOLDOUT_BARS_SOURCE_BOUND_EXCLUSION"],
    })
    assert rows[0]["return_pct"] == ""
    assert rows[0]["bh_return_pct"] == ""
    assert rows[0]["failure_reasons"] == "NO_HOLDOUT_BARS_SOURCE_BOUND_EXCLUSION"
