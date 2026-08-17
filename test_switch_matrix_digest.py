import json
import os
import sqlite3

from tools import switch_matrix_digest as digest
from tools import matrix_guard


def test_strategy_rows_include_wt_force_open_entry_family():
    assert "param LIKE 'WT_3M_FORCE_OPEN%'" in digest.strategy_where()


def test_current_pilot_keys_match_canonical_six_and_exclude_dropped_hao():
    assert digest.DEFAULT_KEYS == (
        "MU_LONG",
        "NVDA_LONG",
        "VT_LONG",
        "TTD_SHORT",
        "ACN_SHORT",
        "LAC_SHORT",
    )


def test_provisional_completion_includes_all_actionable_categories():
    assert digest.PROVISIONAL_ACTIONABLE_CATEGORIES == matrix_guard.ACTIONABLE_CATEGORIES
    assert digest.PROVISIONAL_ACTIONABLE_CATEGORIES == {
        "ACTIONABLE_EXACT_ONLY",
        "ACTIONABLE_VECTOR_THEN_EXACT",
        "ACTIONABLE_BINDING_PROBE",
    }


def _factorial_payload(value):
    return {
        "rows": [
            {
                "classification": "SCREENING_LEAD_NOT_PROMOTABLE",
                "alpha_vs_same_entry_floor_pp": value,
                "metrics": {"capital_return_pct_sum": value},
            }
        ]
    }


def test_exit_factorial_never_falls_back_to_superseded_dc_receipts(
    tmp_path, monkeypatch
):
    reports = tmp_path / "reports"
    root = reports / "vec_research" / "exit_factorial_20260729"
    root.mkdir(parents=True)
    (root / "TTD_SHORT_WT.json").write_text(
        json.dumps(_factorial_payload(98.425))
    )
    (root / "ACN_SHORT_WT.json").write_text(
        json.dumps(_factorial_payload(107.207))
    )
    (root / "MU_LONG.json").write_text(json.dumps(_factorial_payload(0)))
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    rows = digest.load_exit_factorial()

    assert set(rows) == {"MU_LONG"}
    assert "TTD_SHORT" not in rows
    assert "ACN_SHORT" not in rows


def test_exit_factorial_prefers_dc_parity_correction(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    root = reports / "vec_research" / "exit_factorial_20260729"
    root.mkdir(parents=True)
    correction = {
        "contract": "MTF_DC_EXACT_VECTOR_PARITY_V1",
        "supersedes": ["TTD_SHORT_WT.json", "ACN_SHORT_WT.json"],
        "semantics": {"decision_clock": "every execution row"},
        "corrected_best_by_key": {
            "TTD_SHORT": {
                "classification": "SCREENING_LEAD_NOT_PROMOTABLE",
                "alpha_vs_same_entry_floor_pp": 3.152,
                "metrics": {"capital_return_pct_sum": 1136.148},
            },
            "ACN_SHORT": {
                "classification": "SCREENING_LEAD_NOT_PROMOTABLE",
                "alpha_vs_same_entry_floor_pp": 105.440,
                "metrics": {"capital_return_pct_sum": 577.617},
            },
        },
        "superseded_pack_results": {
            "TTD_SHORT": {
                "alpha_vs_same_entry_floor_pp": -29.412,
                "verdict": "GRAY",
            }
        },
    }
    (root / "DC_PARITY_CORRECTION.json").write_text(json.dumps(correction))
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    rows = digest.load_exit_factorial()
    old = digest.load_superseded_exit_factorial_packs()

    assert rows["TTD_SHORT"]["_parity_corrected"] is True
    assert rows["TTD_SHORT"]["_best"]["alpha_vs_same_entry_floor_pp"] == 3.152
    assert rows["ACN_SHORT"]["_best"]["alpha_vs_same_entry_floor_pp"] == 105.440
    assert old["TTD_SHORT"]["alpha_vs_same_entry_floor_pp"] == -29.412


def test_exact_factorial_loader_separates_ttd_and_superseded_acn(
    tmp_path, monkeypatch
):
    reports = tmp_path / "reports"
    root = reports / "exact_exit_factorial_20260729"
    root.mkdir(parents=True)
    for name in (
        "TTD_SHORT.json",
        "ACN_SHORT.json",
        "ACN_SHORT__DC_ONLY.json",
        "ACN_SHORT__WT_ONLY.json",
    ):
        (root / name).write_text(json.dumps({"metrics": {"acc_gain_pct": 1}}))
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    rows = digest.load_exact_exit_factorial()
    by_variant = {row["_variant"]: row for row in rows}

    assert by_variant["dc_only_exact"]["_key"] == "TTD_SHORT"
    assert by_variant["dc_only_exact"]["_superseded"] is False
    assert by_variant["superseded_dc_plus_wt"]["_superseded"] is True
    assert by_variant["dc_only"]["_superseded"] is False
    assert by_variant["wt_only"]["_artifact"].endswith(
        "ACN_SHORT__WT_ONLY.json"
    )


def test_load_vec_research_prefers_newest_artifact_per_window(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    root = reports / "vec_research"
    old = root / "top_exit_20260725T100000Z_MU_LONG"
    new = root / "top_exit_20260725T110000Z_MU_LONG"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    payload = {
        "start": "2024-01-01",
        "end_exclusive": None,
        "contract_valid": True,
        "policy_top20": [{"strategy": "old"}],
    }
    (old / "digest_MU_LONG.json").write_text(json.dumps(payload))
    payload["policy_top20"] = [{"strategy": "new"}]
    (new / "digest_MU_LONG.json").write_text(json.dumps(payload))
    # File mtimes, not directory names, define freshness.
    os.utime(old / "digest_MU_LONG.json", ns=(1_000_000_000, 1_000_000_000))
    os.utime(new / "digest_MU_LONG.json", ns=(2_000_000_000, 2_000_000_000))
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    rows = digest.load_vec_research(("MU_LONG",))

    assert len(rows["MU_LONG"]) == 1
    assert digest.vec_candidate(rows["MU_LONG"][0])["strategy"] == "new"


def test_load_vec_research_surfaces_quarantine_without_digest(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    directory = (
        reports
        / "vec_research"
        / "top_exit_20260725T100000Z_HAO_SHORT_QUARANTINE"
    )
    directory.mkdir(parents=True)
    (directory / "quarantine.json").write_text(
        json.dumps({"reason": "invalid HTF alias"})
    )
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    rows = digest.load_vec_research(("HAO_SHORT",))

    assert len(rows["HAO_SHORT"]) == 1
    assert rows["HAO_SHORT"][0]["contract_valid"] is False
    assert rows["HAO_SHORT"][0]["invalid_data_diagnostic"] is True
    assert digest.vec_candidate(rows["HAO_SHORT"][0]) is None


def test_load_exact_replays_keys_latest_summary_by_source(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    root = reports / "vec_research"
    source = root / "top_exit_source_MU_LONG"
    source.mkdir(parents=True)
    first = root / "v8_exact_replay_1_MU_LONG"
    second = root / "v8_exact_replay_2_MU_LONG"
    first.mkdir()
    second.mkdir()
    (first / "run_summary.json").write_text(
        json.dumps({"source_artifact": str(source), "status": "FAIL"})
    )
    (second / "run_summary.json").write_text(
        json.dumps({"source_artifact": str(source), "status": "PASS"})
    )
    os.utime(
        first / "run_summary.json",
        ns=(1_000_000_000, 1_000_000_000),
    )
    os.utime(
        second / "run_summary.json",
        ns=(2_000_000_000, 2_000_000_000),
    )
    monkeypatch.setattr(digest, "REPORTS", reports)

    rows = digest.load_exact_replays()

    assert rows[str(source.resolve())]["status"] == "PASS"


def test_load_latest_robust_walk_forward_prefers_newest(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    root = reports / "vec_research"
    old = root / "walkforward_top_exit_1_MU_LONG"
    new = root / "walkforward_top_exit_2_MU_LONG"
    old.mkdir(parents=True)
    new.mkdir()
    name = "walkforward_digest_MU_LONG.json"
    (old / name).write_text(json.dumps({"run_id": "old"}))
    (new / name).write_text(json.dumps({"run_id": "new"}))
    os.utime(old / name, ns=(1_000_000_000, 1_000_000_000))
    os.utime(new / name, ns=(2_000_000_000, 2_000_000_000))
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    rows = digest.load_latest_robust_walk_forward(("MU_LONG",))

    assert rows["MU_LONG"]["run_id"] == "new"


def test_load_latest_partial_regime_walk_forward(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    root = reports / "vec_research"
    directory = root / "partial_regime_walkforward_1_VT_LONG"
    directory.mkdir(parents=True)
    (directory / "digest_VT_LONG.json").write_text(
        json.dumps({"run_id": "vt-partial"})
    )
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    rows = digest.load_latest_partial_regime_walk_forward(("VT_LONG",))

    assert rows["VT_LONG"]["run_id"] == "vt-partial"


def test_load_mu_daily_deep_pareto_holdout(tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    receipt = (
        reports
        / "vec_research"
        / "MU_DAILY_DEEP_PARETO_HOLDOUT_RECEIPT_20260727.json"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "classification": "GRAY_PARETO_HOLDOUT_REJECTED",
                "final": {"return_pct": 1170.7, "weighted_tim_pct": 68.62},
            }
        )
    )
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    row = digest.load_mu_daily_deep_pareto_holdout()

    assert row is not None
    assert row["classification"] == "GRAY_PARETO_HOLDOUT_REJECTED"
    assert row["final"]["weighted_tim_pct"] == 68.62
    assert row["_artifact"].endswith(
        "MU_DAILY_DEEP_PARETO_HOLDOUT_RECEIPT_20260727.json"
    )


def test_load_hao_short_native_phase3(tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    receipt = (
        reports
        / "vec_research"
        / "HAO_SHORT_NATIVE_PHASE3_RECEIPT_20260727.json"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "contract": "HAO_SHORT_NATIVE_PHASE3_V4",
                "candidate_count": 48,
                "final_fold_status": "SEALED_NOT_EVALUATED",
            }
        )
    )
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    row = digest.load_hao_short_native_phase3()

    assert row is not None
    assert row["contract"] == "HAO_SHORT_NATIVE_PHASE3_V4"
    assert row["candidate_count"] == 48
    assert row["_artifact"].endswith(
        "HAO_SHORT_NATIVE_PHASE3_RECEIPT_20260727.json"
    )


def test_load_hao_short_exposure_phase4_is_gray_receipt(tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    receipt = (
        reports
        / "vec_research"
        / "HAO_SHORT_EXPOSURE_PHASE4_RECEIPT_20260727.json"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "contract": "HAO_SHORT_EXPOSURE_PHASE4_V3_RECLAIM_CONFIRM",
                "discovery_strict_count": 3,
                "status": "GRAY_DISCOVERY_PASS_FINAL_TIM_FAIL",
                "promotion_allowed": False,
            }
        )
    )
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    row = digest.load_hao_short_exposure_phase4()

    assert row is not None
    assert row["discovery_strict_count"] == 3
    assert row["promotion_allowed"] is False
    assert row["_artifact"].endswith(
        "HAO_SHORT_EXPOSURE_PHASE4_RECEIPT_20260727.json"
    )


def test_load_other_pilot_survivor_audit_stays_gray(tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    receipt = (
        reports
        / "vec_research"
        / "OTHER_PILOT_SURVIVOR_RECEIPT_20260729.json"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "contract": "OTHER_PILOT_DEPLOYED_ALPHA_V1",
                "summary": {
                    "discovery_survivors": 0,
                    "nearest_key": "TTD_SHORT",
                    "promotions": 0,
                },
                "rows": [
                    {
                        "key": "TTD_SHORT",
                        "verdict": "NO_DISCOVERY_SURVIVOR_GRAY",
                        "promotion_eligible": False,
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    row = digest.load_other_pilot_survivor_audit()

    assert row is not None
    assert row["summary"]["nearest_key"] == "TTD_SHORT"
    assert row["summary"]["promotions"] == 0
    assert row["rows"][0]["promotion_eligible"] is False
    assert row["_artifact"].endswith(
        "OTHER_PILOT_SURVIVOR_RECEIPT_20260729.json"
    )


def test_ladder_retune_exact_must_reference_same_source_artifact(
    tmp_path, monkeypatch
):
    reports = tmp_path / "reports"
    root = reports / "vec_research"
    mu = root / "ladder_exposure_retune_1_MU_LONG"
    dino = root / "ladder_exposure_retune_2_DINO_LONG"
    mu.mkdir(parents=True)
    dino.mkdir()
    (mu / "result.json").write_text(
        json.dumps({"manifest": {"symbol": "MU", "side": "LONG"}})
    )
    (dino / "result.json").write_text(
        json.dumps({"manifest": {"symbol": "DINO", "side": "LONG"}})
    )
    stale_mu_exact = root / "v8_exact_ladder_replay_3_MU_LONG"
    dino_exact = root / "v8_exact_ladder_replay_4_DINO_LONG"
    stale_mu_exact.mkdir()
    dino_exact.mkdir()
    (stale_mu_exact / "run_summary.json").write_text(
        json.dumps(
            {
                "source_artifact": str(root / "band_ladder_walkforward_old_MU_LONG"),
                "status": "PASS",
            }
        )
    )
    (dino_exact / "run_summary.json").write_text(
        json.dumps({"source_artifact": str(dino), "status": "PASS"})
    )
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    rows = {row["_key"]: row for row in digest.load_latest_ladder_retunes()}

    assert rows["MU_LONG"]["_exact"] is None
    assert rows["DINO_LONG"]["_exact"]["status"] == "PASS"


def test_dc_low4_below_bh_is_diagnostic_gray_verdict():
    row = {
        "validation_status": "PASS",
        "reentry_violations": 0,
        "inert": 0,
        "trades": 31,
        "gain_per_mo": 0.1,
        "delta_gain_mo_vs_bh": -26.0,
        "time_in_mkt_pct": 0.39,
        "param": "DC_LOW4_STOP_ENABLED",
    }

    assert digest.verdict(row) == "GRAY: ENTRY-QUALITY FAILURE / LOSING CHURN"


def test_path_fleet_loader_exposes_metric_scope_and_units(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    root = reports / "path_fleet"
    root.mkdir(parents=True)
    (root / "universe.json").write_text("{}")
    con = sqlite3.connect(root / "queue.db")
    con.executescript(
        """
        CREATE TABLE jobs (
          id INTEGER PRIMARY KEY,path_id TEXT,status TEXT,heartbeat_at REAL
        );
        CREATE TABLE results (
          id INTEGER PRIMARY KEY,job_id INTEGER,symbol TEXT,side TEXT,
          stage TEXT,status TEXT,strategy_return_pct REAL,bh_return_pct REAL,
          same_entry_control_return_pct REAL,alpha_vs_bh_pp REAL,
          alpha_vs_control_pp REAL,tim_pct REAL,trades INTEGER,created_at REAL,
          payload_json TEXT
        );
        INSERT INTO jobs VALUES(1,'ENTRY_WT_DC','SCREENED',1);
        """
    )
    payload = {
        "metric_scope": "NESTED_OUTER_VALIDATION_FOLD_AGGREGATE",
        "return_unit": "SUM_OF_FOLD_CAPITAL_RETURN_PCT",
        "return_aggregation": "SUM_ACROSS_OUTER_VALIDATION_FOLDS",
        "tim_unit": "PCT",
        "tim_aggregation": "ROW_WEIGHTED_MEAN_ACROSS_OUTER_VALIDATION_FOLDS",
    }
    con.execute(
        """INSERT INTO results VALUES
           (1,1,'MU','LONG','VEC_NESTED_FOLD_AGGREGATE','DISCARD_GRAY',
            100,30,88,70,12,60,6,1,?)""",
        (json.dumps(payload),),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(digest, "REPORTS", reports)

    progress = digest.load_path_fleet_progress()

    assert progress["rows"][0]["metric_scope"] == payload["metric_scope"]
    assert progress["rows"][0]["return_unit"] == payload["return_unit"]
    assert (
        progress["rows"][0]["tim_aggregation"]
        == payload["tim_aggregation"]
    )


def test_exact_short_scope_renders_fixed_unit_and_both_tim_measures(
    tmp_path, monkeypatch
):
    reports = tmp_path / "reports"
    root = reports / "path_fleet"
    root.mkdir(parents=True)
    (root / "universe.json").write_text("{}")
    con = sqlite3.connect(root / "queue.db")
    con.executescript(
        """
        CREATE TABLE jobs (
          id INTEGER PRIMARY KEY,path_id TEXT,status TEXT,heartbeat_at REAL
        );
        CREATE TABLE results (
          id INTEGER PRIMARY KEY,job_id INTEGER,symbol TEXT,side TEXT,
          stage TEXT,status TEXT,strategy_return_pct REAL,bh_return_pct REAL,
          same_entry_control_return_pct REAL,alpha_vs_bh_pp REAL,
          alpha_vs_control_pp REAL,tim_pct REAL,trades INTEGER,created_at REAL,
          payload_json TEXT
        );
        INSERT INTO jobs VALUES(1,'ENTRY_DISASTER_GUARD_ENABLED','SCREENED',1);
        """
    )
    payload = {
        "metric_scope": "FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD",
        "fold": "FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD",
        "return_unit": "FIXED_2000_USD_CAPITAL_RETURN_PCT",
        "return_aggregation": "NONE_SINGLE_FOLD",
        "capital_base_usd": 2000,
        "tim_unit": "PCT",
        "tim_metric": "BINARY_AND_EXPOSURE_WEIGHTED_TIME_IN_MARKET",
        "tim_aggregation": "NONE_SINGLE_FOLD",
        "tim_binary_pct": 4.2142468733,
        "tim_weighted_pct": 0.7631200384,
    }
    con.execute(
        """INSERT INTO results VALUES
           (1,1,'LRCX','SHORT','V8_EXACT_REPLAY','EXACT_PARITY_ONLY',
            9.6534,-69.9969,9.6534,79.6503,0,4.2142,8,1,?)""",
        (json.dumps(payload),),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(digest, "REPORTS", reports)

    row = digest.load_path_fleet_progress()["rows"][0]
    rendered = digest.format_fleet_metric_scope(row)

    assert row["metric_scope"] == "FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD"
    assert row["return_unit"] == "FIXED_2000_USD_CAPITAL_RETURN_PCT"
    assert row["tim_binary_pct"] == payload["tim_binary_pct"]
    assert row["tim_weighted_pct"] == payload["tim_weighted_pct"]
    assert "binary=4.214%" in rendered
    assert "weighted=0.763%" in rendered
