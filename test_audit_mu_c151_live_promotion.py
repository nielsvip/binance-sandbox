import json
from datetime import datetime, timezone
from pathlib import Path

from tools.audit_mu_c151_live_promotion import (
    CANDIDATE_LABEL,
    CONTRACT_NAME,
    audit,
    prospective_contract,
)


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def _fixture(tmp_path: Path):
    root = tmp_path
    _write(
        root / "data/hourly_reconfig/trb/active_config.json",
        {"MU_LONG": {"overrides": {"LONG_ENABLED": True}}},
    )
    _write(
        root / "data/hourly_reconfig/per_sym_active_config.json",
        {"MU_LONG": {"overrides": {"LONG_ENABLED": True}}},
    )
    (root / "tools").mkdir(exist_ok=True)
    (root / "tools/vec_band_ladder_walkforward.py").write_text(
        "BASE_UNIT = 2_000.0\nCAPACITY = 16_000.0\n"
        "for slot, tf in enumerate(TF_ORDER):\n    pass\n"
        'if curve.trigger == "green":\n    pass\n'
        "next_strictly_later_index(data.ts, i, right)\n"
    )
    (root / "tools/v8_research_ladder_adapter.py").write_text("adapter = True\n")
    (root / "tools/run_v8_research_ladder_replay.py").write_text(
        'flag = "--research-ladder-spec"\npromotion_allowed = False\n'
    )
    (root / "backtest_v8_engine.py").write_text("engine = 'current'\n")
    (root / "tradier_manage.py").write_text(
        "_ma_confirmed = current_price >= _ma_st['lo']\n"
        "_ld_tf = str(_cfg('LR_BAND_ENTRY_TF', 'D'))\n"
    )
    frozen = {
        "candidate": {"number": 151, "label": CANDIDATE_LABEL},
        "inputs": {"npz_sha256": "npz", "candidate_discovery_result_sha256": "grid"},
        "discovery": {
            "future_htf_count": 0,
            "folds": [
                {"weighted_tim_pct": 77.39, "bh_multiple": 12.8, "return_pct": 567, "minimum_equity_usd": 8136, "clamps": 0},
                {"weighted_tim_pct": 77.42, "bh_multiple": 5.3, "return_pct": 707, "minimum_equity_usd": 8613, "clamps": 0},
            ],
        },
        "final": {
            "weighted_tim_pct": 68.62,
            "bh_multiple": 5.70,
            "bh_return_pct": 205.25,
            "return_pct": 1170,
            "minimum_equity_usd": 7912,
            "clamps": 0,
            "future_htf_count": 0,
        },
        "exact_v3": {
            "status": "PASS",
            "signal_parity": True,
            "future_htf_count": 0,
            "refusals": 0,
            "actions_executed": 33,
            "actions_scheduled": 33,
            "weighted_tim_delta_pp": 0.0,
            "accounting_delta_bp": 0.0,
            "engine_sha256": "old-engine",
        },
    }
    tim65 = {
        "inputs": dict(frozen["inputs"]),
        "deployed_capital": {
            "base_unit_usd": 2000.0,
            "hard_capacity_usd": 16000.0,
            "peak_post_fill_notional_usd": 16000.0,
        },
        "final": {
            "fill_ratio": 1.0,
            "checks_all": {
                "no_capacity_breach": True,
                "no_flat_beyond_reclaim": True,
                "no_unfilled_reclaim": True,
            },
        },
    }
    stability = {
        "symbol": "MU",
        "side": "LONG",
        "untouched_final_fold": {"end_exclusive": "2026-07-25"},
    }
    return (
        root,
        _write(root / "frozen.json", frozen),
        _write(root / "tim65.json", tim65),
        _write(root / "stability.json", stability),
    )


def test_contract_is_new_prospective_65_80_contract():
    contract = prospective_contract()
    assert contract["contract"] == CONTRACT_NAME
    assert contract["weighted_time_in_market_pct_each_fold"] == [65.0, 80.0]
    assert contract["not_a_holdout_preregistration"] is True


def test_realistic_fixture_fails_closed_on_live_parity_engine_drift_and_staleness(tmp_path):
    root, frozen, tim65, stability = _fixture(tmp_path)
    result = audit(
        root=root,
        frozen_receipt_path=frozen,
        tim65_receipt_path=tim65,
        stability_receipt_path=stability,
        as_of=datetime(2026, 7, 29, 15, 30, tzinfo=timezone.utc),
    )
    assert result["promotion_allowed"] is False
    assert result["activation_performed"] is False
    assert set(result["failures"]) == {
        "ordinary_live_path_semantic_parity",
        "exact_engine_hash_matches_current_and_revalidated",
        "fresh_deployment_data",
    }
    passed = {row["name"]: row["pass"] for row in result["checks"]}
    assert passed["weighted_tim_each_fold_65_80"]
    assert passed["positive_side_bh_and_at_least_2x_each_fold"]
    assert passed["benchmark_2000_capacity_16000"]
    assert passed["completed_parent_causality"]
    assert passed["reentry_invariant"]
    assert passed["exact_v3_schedule_accounting_tim_capacity"]


def test_fails_identity_when_tim65_receipt_is_not_same_frozen_candidate(tmp_path):
    root, frozen, tim65, stability = _fixture(tmp_path)
    payload = json.loads(tim65.read_text())
    payload["inputs"]["npz_sha256"] = "different"
    tim65.write_text(json.dumps(payload))
    result = audit(
        root=root,
        frozen_receipt_path=frozen,
        tim65_receipt_path=tim65,
        stability_receipt_path=stability,
        as_of=datetime(2026, 7, 25, 12, tzinfo=timezone.utc),
    )
    assert "candidate_identity_and_hash_chain" in result["failures"]
