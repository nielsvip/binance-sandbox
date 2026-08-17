import json

from tools import current_matrix_reporting as cmr
from tools.build_lifecycle_receipt_envelope import build, canonical_hash, file_hash
from tools.materialize_lifecycle_workbook_adapter import materialize


def _signed(payload, field):
    payload[field] = canonical_hash(payload)
    return payload


def _campaign(path):
    path.mkdir(parents=True, exist_ok=True)
    manifest = _signed(
        {
            "source_npz": {"path": "/npz/XYZ.npz", "sha256": "a" * 64},
            "source_code": {"path": "/src/runner.py", "sha256": "b" * 64},
            "window": {"start": "2024-01-01", "end_exclusive": "2026-01-01"},
            "commission_bps_one_way": 0.0,
            "slippage_bps_one_way": 5.0,
            "base_unit_usd": 2000.0,
            "capacity_usd": 16000.0,
            "ladder": {},
        },
        "manifest_sha256",
    )
    (path / "campaign_manifest.json").write_text(json.dumps(manifest))
    (path / "path_stream_inventory.json").write_text("{}\n")
    uniqueness = _signed(
        {"schema": "u", "behavior_unique_results": 1}, "receipt_sha256"
    )
    (path / "uniqueness_receipt.json").write_text(json.dumps(uniqueness))
    best = _signed({
        "symbol": "XYZ",
        "side": "LONG",
        "manifest_sha256": manifest["manifest_sha256"],
        "gain_pct_per_month": 3.0,
        "bh_gain_pct_per_month": 1.0,
        "delta_gain_mo_vs_bh": 2.0,
        "no_lookahead_future_htf_count": 0,
        "recipe_id": "recipe-a",
        "recipe": {"exit": 1},
        "paths": {"exit": {"label": "WT"}},
        "capital_return_pct": 6.0,
        "max_drawdown_account_pct": 10.0,
        "time_in_market_pct": 60.0,
        "weighted_time_in_market_pct": 30.0,
        "fills": 3,
        "augments": 1,
        "reduces": 0,
        "full_exits": 2,
        "real_close_actions": 2,
        "reentries": 1,
        "closes_per_month": 1.0,
        "capacity_clamps": 0,
        "capacity_precheck_suppressed": 0,
        "bars_flat_beyond_reclaim": 0,
        "activity_status": "PASS_MONTHLY_FLOOR",
    }, "result_sha256")
    (path / "raw_results.jsonl").write_text(json.dumps(best) + "\n")
    (path / "finalists.jsonl").write_text(json.dumps(best) + "\n")
    result = _signed(
        {
            "status": "COMPLETE",
            "symbol": "XYZ",
            "side": "LONG",
            "manifest_sha256": manifest["manifest_sha256"],
            "raw_results_sha256": file_hash(path / "raw_results.jsonl"),
            "uniqueness_receipt_sha256": file_hash(
                path / "uniqueness_receipt.json"
            ),
            "rows_evaluated": 1,
            "logical_recipes": 1,
            "behavior_unique_results": 1,
            "causally_unique_recipes": 1,
            "quarantined_duplicate_recipes": 0,
            "duplicate_behavior_groups": 0,
            "best": best,
        },
        "receipt_sha256",
    )
    (path / "result.json").write_text(json.dumps(result))
    envelope = build(path)
    (path / "envelope.json").write_text(json.dumps(envelope))
    return envelope


def test_verified_campaign_builds_hash_bound_envelope(tmp_path):
    envelope = _campaign(tmp_path)
    assert all(envelope["verification"].values())
    unsigned = {k: v for k, v in envelope.items() if k != "envelope_sha256"}
    assert envelope["envelope_sha256"] == canonical_hash(unsigned)


def test_adapter_rebinds_best_and_is_discovered_without_exact_credit(tmp_path):
    source = tmp_path / "source"
    _campaign(source)
    output = tmp_path / "data/reports/vec_research/xyz_lifecycle_causal_v2"
    receipt = materialize(source, output)
    # A second transport copy of the same signed recipe must collapse to one
    # workbook row rather than tripping the duplicate guard downstream.
    materialize(
        source,
        tmp_path / "data/reports/vec_research/xyz_lifecycle_causal_v2_copy",
    )
    assert receipt["scalar_matrix_written"] is False
    adapted = json.loads((output / "result.json").read_text())
    best = dict(adapted["best"])
    expected = canonical_hash({k: v for k, v in best.items() if k != "result_sha256"})
    assert best["result_sha256"] == expected
    rows = cmr.lifecycle_workbook_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["key"] == "XYZ_LONG"
    assert rows[0]["evidence_tier"] == "VECTOR_LIFECYCLE_REMOTE_RECEIPT_UNVERIFIED"
    dashboard = cmr.lifecycle_dashboard_rows(tmp_path)
    assert dashboard == rows
    assert dashboard[0]["verification_status"] == "REMOTE_RECEIPT_UNVERIFIED"
    assert dashboard[0]["recipe"] == {"exit": 1}
    assert dashboard[0]["paths"]["exit"]["label"] == "WT"
