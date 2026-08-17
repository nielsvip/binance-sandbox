from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path


def _load_gate_functions():
    source = (Path(__file__).parent / "tradier_manage.py").read_text()
    tree = ast.parse(source)
    wanted = {
        "_collect_sharpe_sources",
        "_validated_historical_matrix_receipt",
        "_validated_matrix_gainmo_promotion",
        "_inject_neg_sharpe_no_trade",
    }
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name)
            and target.id.startswith("_MATRIX_GAINMO_")
            for target in node.targets
        ):
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted:
            nodes.append(node)
    namespace = {
        "hashlib": hashlib,
        "json": json,
        "Path": Path,
    }
    exec(compile(ast.Module(nodes, type_ignores=[]), "tradier_manage.py", "exec"), namespace)
    return (
        namespace["_inject_neg_sharpe_no_trade"],
        namespace["_validated_matrix_gainmo_promotion"],
    )


(
    _inject_neg_sharpe_no_trade,
    _validated_matrix_gainmo_promotion,
) = _load_gate_functions()


def _matrix_entry(**result_updates):
    result = {
        "key": "ACN_SHORT",
        "campaign": "stocks_repaired_20260730_c5",
        "result_rowid": 123,
        "gain_per_mo": 2.01,
        "normalized_gain_per_mo": 2.01,
        "delta_gain_mo_vs_bh": 0.01,
        "normalized_delta_gain_mo_vs_bh": 0.01,
        "real_closes": 30,
        "validation_status": "PASS",
        "contract_fingerprint": "tradier-matrix-exec-c5-20260730:abc",
        "trades_path": "/tmp/validated.json",
        "promotion_rule": (
            "normalized_gain_per_mo>2 and normalized_delta_gain_mo_vs_bh>0; "
            "receipt proves average-deployment normalization to $2000; "
            "trades>10 and real_closes>10; TIM is descriptive"
        ),
        "capital_accounting_version": "avg-trade-deployed-2000-v1",
        "benchmark_deployed_usd": 2000.0,
        "average_deployed_usd": 1750.0,
        "capital_normalization_factor": 1.142857,
        "raw_pnl_usd": 350.0,
        "normalized_pnl_usd": 400.0,
        "max_dd_pct": -4.0,
        "pool_sharpe_live_gate_waived": True,
    }
    result.update(result_updates)
    return {
        "winning_tag": "MATRIX_C5_1YR_TEST_true",
        "meta_diagnostic_tag": (
            "TRB_MATRIX_USER_EXCEPTION_GAIN_MO_GT_2_BEATS_BH_NO_TIM_GATE"
        ),
        "wsharpe": -0.5,
        "trades": 30,
        "overrides": {
            "LONG_ENABLED": False,
            "SHORT_ENABLED": True,
        },
        "matrix_result": result,
    }


def test_validated_matrix_gainmo_waiver_survives_negative_sharpe():
    entry = _matrix_entry()

    assert _validated_matrix_gainmo_promotion("ACN_SHORT", entry)
    overrides = _inject_neg_sharpe_no_trade("ACN_SHORT", entry)

    assert overrides["SHORT_ENABLED"] is True
    assert "HTF_TREND_VETO_ENABLED" not in overrides


def test_validated_one_year_c5_uses_same_exact_contract():
    entry = _matrix_entry(
        campaign="stocks_repaired_20260730_c5_1yr",
    )

    assert _validated_matrix_gainmo_promotion("ACN_SHORT", entry)


def test_waiver_fails_closed_for_every_required_receipt_field():
    required_changes = {
        "gain_per_mo": 2.0,
        "normalized_gain_per_mo": 2.0,
        "delta_gain_mo_vs_bh": 0.0,
        "normalized_delta_gain_mo_vs_bh": 0.0,
        "real_closes": 0,
        "validation_status": "FAIL",
        "capital_accounting_version": "legacy",
        "pool_sharpe_live_gate_waived": False,
        "contract_fingerprint": "tradier-matrix-exec-c4:old",
        "result_rowid": 0,
        "trades_path": "",
        "benchmark_deployed_usd": 10000.0,
        "average_deployed_usd": 0.0,
        "capital_normalization_factor": 0.0,
        "normalized_pnl_usd": 0.0,
        "raw_pnl_usd": None,
        "max_dd_pct": None,
        "promotion_rule": "almost",
    }
    for field, bad_value in required_changes.items():
        entry = _matrix_entry(**{field: bad_value})
        assert not _validated_matrix_gainmo_promotion("ACN_SHORT", entry)
        overrides = _inject_neg_sharpe_no_trade("ACN_SHORT", entry)
        assert overrides["SHORT_ENABLED"] is False
        assert overrides["HTF_TREND_VETO_ENABLED"] is True


def test_waiver_requires_more_than_ten_trades_and_closes():
    for entry_trades, closes in ((10, 30), (30, 10)):
        entry = _matrix_entry(real_closes=closes)
        entry["trades"] = entry_trades
        assert not _validated_matrix_gainmo_promotion("ACN_SHORT", entry)


def test_wrong_key_or_tag_never_waives_three_source_rule():
    entry = _matrix_entry()
    assert not _validated_matrix_gainmo_promotion("TTD_SHORT", entry)
    wrong_tag = copy.deepcopy(entry)
    wrong_tag["meta_diagnostic_tag"] = "almost"
    assert not _validated_matrix_gainmo_promotion("ACN_SHORT", wrong_tag)


def test_allowlisted_revalued_c2_uses_its_own_exact_identity():
    entry = _matrix_entry()
    entry["winning_tag"] = "MATRIX_C2_ALLOWLISTED_2YR_TEST_true"
    result = entry["matrix_result"]
    result["campaign"] = "stocks_repaired_20260725_c2"
    result["contract_fingerprint"] = "tradier-matrix-exec-c4-20260729:abc"
    result["legacy_contract_allowlisted"] = True

    assert _validated_matrix_gainmo_promotion("ACN_SHORT", entry)

    wrong_prefix = copy.deepcopy(entry)
    wrong_prefix["matrix_result"]["contract_fingerprint"] = (
        "tradier-matrix-c2-20260725:wrong"
    )
    assert not _validated_matrix_gainmo_promotion(
        "ACN_SHORT",
        wrong_prefix,
    )


def _historical_entry(tmp_path, winner_prefix, contract_prefix, campaign):
    entry = _matrix_entry()
    entry["winning_tag"] = winner_prefix + "TEST_true"
    result = entry["matrix_result"]
    result["campaign"] = campaign
    result["contract_fingerprint"] = contract_prefix + "abc"
    result["historical_receipt_validated"] = True
    ledger = tmp_path / "cell__ACN.jsonl"
    ledger.write_text(
        "".join(
            json.dumps(
                {
                    "symbol": "ACN",
                    "side": "SHORT",
                    "pnl_pct": 1.0,
                }
            )
            + "\n"
            for _ in range(result["real_closes"])
        )
    )
    audit = tmp_path / "audit__ACN.json"
    audit.write_text(
        json.dumps(
            {
                "status": "PASS",
                "contract_fingerprint": result["contract_fingerprint"],
                "trade_fingerprint": "ledger-fingerprint",
            }
        )
    )
    override = tmp_path / "override__ACN.json"
    override.write_text(json.dumps({"TEST_ENABLED": True}))
    result.update(
        {
            "source_file": "param_matrix_daemon/legacy/TEST__true",
            "trades_path": str(ledger),
            "audit_path": str(audit),
            "override_path": str(override),
            "trades_fingerprint": "ledger-fingerprint",
            "overrides_sha256": hashlib.sha256(
                override.read_bytes()
            ).hexdigest(),
        }
    )
    identity_fields = (
        "audit_path",
        "campaign",
        "contract_fingerprint",
        "key",
        "override_path",
        "overrides_sha256",
        "source_file",
        "trades_fingerprint",
        "trades_path",
    )
    identity = {name: result[name] for name in identity_fields}
    result["receipt_identity_sha256"] = hashlib.sha256(
        json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return entry


def test_all_protected_historical_classes_require_reopened_receipt(tmp_path):
    classes = (
        (
            "MATRIX_HIST_C1_VALIDATED_",
            "tradier-matrix-c1-20260725:",
            "stocks_repaired_20260725_c1",
        ),
        (
            "MATRIX_HIST_C2_VALIDATED_",
            "tradier-matrix-c2-20260725:",
            "stocks_repaired_20260725_c2",
        ),
        (
            "MATRIX_HIST_C3_VALIDATED_",
            "tradier-matrix-exec-c3-20260729:",
            "stocks_repaired_20260725_c2",
        ),
        (
            "MATRIX_HIST_C4_VALIDATED_",
            "tradier-matrix-exec-c4-20260729:",
            "stocks_repaired_20260725_c2",
        ),
    )
    for winner, contract, campaign in classes:
        cell_dir = tmp_path / winner
        cell_dir.mkdir()
        entry = _historical_entry(
            cell_dir,
            winner,
            contract,
            campaign,
        )
        assert _validated_matrix_gainmo_promotion("ACN_SHORT", entry)

        tampered = copy.deepcopy(entry)
        tampered["matrix_result"]["receipt_identity_sha256"] = "0" * 64
        assert not _validated_matrix_gainmo_promotion(
            "ACN_SHORT",
            tampered,
        )


def test_historical_receipt_fails_if_artifact_changes_after_promotion(tmp_path):
    entry = _historical_entry(
        tmp_path,
        "MATRIX_HIST_C4_VALIDATED_",
        "tradier-matrix-exec-c4-20260729:",
        "stocks_repaired_20260725_c2",
    )
    Path(entry["matrix_result"]["override_path"]).write_text(
        json.dumps({"TEST_ENABLED": False})
    )

    assert not _validated_matrix_gainmo_promotion("ACN_SHORT", entry)


def test_historical_class_rejects_wrong_campaign_prefix_or_tag(tmp_path):
    entry = _historical_entry(
        tmp_path,
        "MATRIX_HIST_C4_VALIDATED_",
        "tradier-matrix-exec-c4-20260729:",
        "stocks_repaired_20260725_c2",
    )
    mutations = (
        ("campaign", "stocks_repaired_20260725_c1", None),
        (
            "contract_fingerprint",
            "tradier-matrix-exec-c3-20260729:abc",
            None,
        ),
        (None, None, "MATRIX_HIST_C2_VALIDATED_TEST_true"),
    )
    for field, value, winner_tag in mutations:
        bad = copy.deepcopy(entry)
        if field:
            bad["matrix_result"][field] = value
        if winner_tag:
            bad["winning_tag"] = winner_tag
        assert not _validated_matrix_gainmo_promotion("ACN_SHORT", bad)


def test_positive_legacy_source_still_unbans_without_matrix_receipt():
    entry = {
        "wsharpe": -0.5,
        "overrides": {"SHORT_ENABLED": False},
    }
    baseline = {"_promoted_wsharpe": 0.1}

    overrides = _inject_neg_sharpe_no_trade(
        "ACN_SHORT",
        entry,
        baseline,
    )

    assert overrides["SHORT_ENABLED"] is True
