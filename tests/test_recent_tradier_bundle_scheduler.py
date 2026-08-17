import json
import time

import pytest

from tools import recent_tradier_bundle_scheduler as scheduler


def _insert_handle(
    con,
    *,
    symbol,
    bundle_id,
    promising,
    fail_streak=0,
    status="PENDING",
    lease_at=None,
):
    con.execute(
        """INSERT INTO handles
           (campaign_id,symbol,side,cohort_bucket,bundle_id,prior_promising,
            historical_positive,historical_negative,historical_inert,
            fail_streak,status,time_budget_s,lease_owner,lease_at,updated_at)
           VALUES('test',?,'LONG','TEST',?,?,0,0,0,?,?,30,
                  CASE WHEN ? IS NULL THEN NULL ELSE 'dead-worker' END,?,?)""",
        (
            symbol,
            bundle_id,
            int(promising),
            fail_streak,
            status,
            lease_at,
            lease_at,
            scheduler.now_iso(),
        ),
    )


def test_bundle_catalog_only_uses_vectorized_knobs():
    scheduler.validate_catalog()


def test_artifact_parser_ignores_progress_trade_count(tmp_path):
    summary = tmp_path / "summary.jsonl"
    trades = tmp_path / "trades.jsonl"
    output = "\n".join(
        [
            "V8_VEC_PROGRESS: sym=ACN side=SHORT trades=34 elapsed_s=1.65",
            f"  summary={summary}",
            f"  trades ={trades}",
            "V8_RESULT: trades=34",
        ]
    )
    assert scheduler._artifact_paths(output) == (summary, trades)


def test_claim_sequence_is_deterministic_nine_to_one(tmp_path):
    con = scheduler.connect(tmp_path)
    scheduler.meta_set(con, "campaign_id", "test")
    scheduler.meta_set(con, "claim_sequence", 0)
    for index in range(30):
        _insert_handle(
            con,
            symbol=f"P{index}",
            bundle_id=f"PRIORITY_{index}",
            promising=True,
        )
        _insert_handle(
            con,
            symbol=f"E{index}",
            bundle_id=f"EXPLORE_{index}",
            promising=False,
        )
    con.commit()
    lanes = []
    for _ in range(20):
        row, lane = scheduler.claim(con, "test-worker")
        lanes.append(lane)
        con.execute(
            "UPDATE handles SET status='EXACT_PENDING' WHERE id=?", (row["id"],)
        )
        con.commit()
    assert lanes == ["PRIORITY"] * 9 + ["EXPLORE"] + ["PRIORITY"] * 9 + [
        "EXPLORE"
    ]


def test_three_failures_demote_a_promising_handle_to_exploration(tmp_path):
    con = scheduler.connect(tmp_path)
    scheduler.meta_set(con, "campaign_id", "test")
    _insert_handle(
        con,
        symbol="MU",
        bundle_id="BUNDLE",
        promising=True,
        fail_streak=3,
    )
    con.commit()
    row = con.execute("SELECT * FROM handles").fetchone()
    assert scheduler.effective_lane(row) == "EXPLORE"


def test_stale_lease_is_resumable_but_data_quarantine_is_terminal(tmp_path):
    con = scheduler.connect(tmp_path)
    scheduler.meta_set(con, "campaign_id", "test")
    scheduler.meta_set(con, "claim_sequence", 0)
    _insert_handle(
        con,
        symbol="STALE",
        bundle_id="B1",
        promising=True,
        status="RUNNING",
        lease_at=time.time() - 100,
    )
    _insert_handle(
        con,
        symbol="BAD",
        bundle_id="B2",
        promising=True,
        status="DATA_QUARANTINED",
    )
    con.commit()
    row, lane = scheduler.claim(con, "replacement", lease_s=10)
    assert row["symbol"] == "STALE"
    assert lane == "PRIORITY"


def test_claim_never_leaks_a_handle_from_an_old_campaign(tmp_path):
    con = scheduler.connect(tmp_path)
    scheduler.meta_set(con, "campaign_id", "test")
    scheduler.meta_set(con, "claim_sequence", 0)
    _insert_handle(
        con,
        symbol="CURRENT",
        bundle_id="CURRENT",
        promising=True,
    )
    _insert_handle(
        con,
        symbol="OLD",
        bundle_id="OLD",
        promising=True,
    )
    con.execute(
        "UPDATE handles SET campaign_id='old-campaign' WHERE symbol='OLD'"
    )
    con.commit()
    row, _lane = scheduler.claim(con, "worker")
    assert row["symbol"] == "CURRENT"


def test_init_labels_pilots_and_never_invents_ach_or_hao(tmp_path, monkeypatch):
    cohort = tmp_path / "cohort.json"
    cohort.write_text(
        json.dumps(
            {
                "window_start": "2026-06-30T00:00:00+00:00",
                "window_end_exclusive": "2026-07-30T00:00:00+00:00",
                "cohort": [
                    {"symbol": "MU", "side": "LONG"},
                    {"symbol": "MU", "side": "SHORT"},
                ],
            }
        )
    )
    monkeypatch.setattr(scheduler, "validate_catalog", lambda: None)
    root = tmp_path / "campaign"
    status = scheduler.init_campaign(
        root=root,
        cohort_path=cohort,
        fleet_db=tmp_path / "missing.db",
        pilot_exceptions=("TTD_SHORT", "ACN_SHORT"),
        priority_budget_s=30,
        explore_budget_s=15,
    )
    assert status["distinct_keys"] == 4
    assert status["cohort_buckets"]["USER_PILOT_EXCEPTION_NOT_RECENT"] > 0
    con = scheduler.connect(root)
    assert not con.execute(
        "SELECT 1 FROM handles WHERE symbol IN ('ACH','HAO')"
    ).fetchone()
    con.close()
    for forbidden in ("ACH_SHORT", "HAO_SHORT"):
        with pytest.raises(ValueError):
            scheduler.init_campaign(
                root=tmp_path / forbidden,
                cohort_path=cohort,
                fleet_db=tmp_path / "missing.db",
                pilot_exceptions=(forbidden,),
                priority_budget_s=30,
                explore_budget_s=15,
            )


def test_strict_fold_requires_alpha_control_trades_and_65_to_80_tim():
    control = {"strategy_return_pct": 8.0}
    good = {
        "strategy_return_pct": 12.0,
        "side_benchmark_pct": 10.0,
        "trades": 4,
        "tim_pct": 65.0,
        "max_dd_pct": 20.0,
    }
    assert scheduler.fold_strict(
        good, control, tim_min=65.0, tim_max=80.0
    ) == (True, [])
    bad = dict(good, strategy_return_pct=9.0, tim_pct=81.0)
    strict, failures = scheduler.fold_strict(
        bad, control, tim_min=65.0, tim_max=80.0
    )
    assert not strict
    assert "did_not_beat_side_bh_or_cash" in failures
    assert "tim_not_65_80" in failures


def test_committed_fill_ledger_never_revalues_short_denominator():
    timestamps = scheduler.np.asarray([1, 2, 3, 4], dtype=scheduler.np.int64)
    closes = scheduler.np.asarray([100.0, 100.0, 90.0, 80.0])
    events = [
        {
            "ts": "1970-01-01T00:00:01+00:00",
            "type": "OPEN",
            "qty": 1.0,
            "price": 100.0,
        },
        {
            "ts": "1970-01-01T00:00:04+00:00",
            "type": "CLOSE",
            "qty": 1.0,
            "price": 80.0,
        },
    ]
    row = scheduler._committed_fill_ledger(
        events,
        timestamps,
        closes,
        side="SHORT",
        round_trip_cost_pct=0.0,
    )
    assert row["realized_net_pnl_usd"] == 20.0
    assert row["average_committed_fill_notional_usd"] == 75.0
    assert row["strategy_return_pct"] == pytest.approx(26.6666666667)
    assert row["ledger_errors"] == []


def test_strict_fold_rejects_hedges_and_invalid_fill_ledger():
    control = {"strategy_return_pct": 1.0}
    metrics = {
        "strategy_return_pct": 12.0,
        "side_benchmark_pct": 10.0,
        "trades": 4,
        "tim_pct": 70.0,
        "max_dd_pct": 20.0,
        "hedge_event_count": 1,
        "ledger_errors": ["close_while_flat"],
    }
    strict, failures = scheduler.fold_strict(
        metrics, control, tim_min=65.0, tim_max=80.0
    )
    assert not strict
    assert "hedge_events_out_of_scope" in failures
    assert "committed_fill_ledger_invalid" in failures


def test_parser_failure_recovery_archives_and_rewinds_whole_campaign(tmp_path):
    con = scheduler.connect(tmp_path)
    scheduler.meta_set(con, "campaign_id", "test")
    scheduler.meta_set(con, "claim_sequence", 0)
    for index in range(9):
        _insert_handle(
            con,
            symbol=f"P{index}",
            bundle_id=f"PRIORITY_{index}",
            promising=True,
        )
    _insert_handle(
        con,
        symbol="E0",
        bundle_id="EXPLORE_0",
        promising=False,
    )
    con.commit()

    failure = (
        "RUNNER:FileNotFoundError:[Errno 2] "
        "No such file or directory: '34'"
    )
    for _ in range(10):
        handle, lane = scheduler.claim(con, "canary")
        receipt = {
            "status": "VECTOR_REJECTED",
            "failures": [failure],
            "folds": [],
            "exact_invoked": False,
            "started_at": scheduler.now_iso(),
            "lane": lane,
            "elapsed_s": 0.1,
        }
        scheduler.finish_attempt(con, tmp_path, handle, receipt)

    preview = scheduler.recover_artifact_parser_failures(
        con, tmp_path, expected_count=10
    )
    assert preview["recoverable_count"] == 10
    assert preview["applied"] is False
    assert con.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 10

    applied = scheduler.recover_artifact_parser_failures(
        con, tmp_path, expected_count=10, apply=True
    )
    assert applied["applied"] is True
    assert con.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    assert scheduler.meta_get(con, "claim_sequence") == "0"
    handles = con.execute(
        "SELECT status,attempts,fail_streak FROM handles"
    ).fetchall()
    assert all(tuple(row) == ("PENDING", 0, 0) for row in handles)
    archive = scheduler.Path(applied["archive"])
    assert len(list(archive.glob("h*_a*.json"))) == 10
    manifest = json.loads((archive / "recovery_manifest.json").read_text())
    assert manifest["applied"] is True
    con.close()


def test_legacy_contract_invalidation_is_exact_and_archival(
    tmp_path, monkeypatch
):
    con = scheduler.connect(tmp_path)
    scheduler.meta_set(con, "campaign_id", "test")
    scheduler.meta_set(con, "claim_sequence", 0)
    replacement = {
        "sha256": "f" * 64,
        "files": {
            "v8_vec_sweep.py": "1" * 64,
            "wt_dc_entry_scorer.py": "2" * 64,
            "wt_dc_entry_scorer_vec.py": "3" * 64,
            "vec_paths/live_entry_engine.py": "4" * 64,
            "tools/recent_tradier_bundle_scheduler.py": "5" * 64,
        },
    }
    monkeypatch.setattr(
        scheduler, "current_code_contract", lambda: replacement
    )
    for index in range(2):
        _insert_handle(
            con,
            symbol=f"P{index}",
            bundle_id=f"PRIORITY_{index}",
            promising=True,
        )
    con.commit()

    attempts = []
    for _ in range(2):
        handle, lane = scheduler.claim(con, "legacy")
        receipt = {
            "status": "VECTOR_REJECTED",
            "failures": ["legacy"],
            "folds": [],
            "exact_invoked": False,
            "started_at": scheduler.now_iso(),
            "lane": lane,
            "elapsed_s": 0.1,
        }
        path = scheduler.finish_attempt(con, tmp_path, handle, receipt)
        row = con.execute(
            "SELECT * FROM attempts WHERE handle_id=? ORDER BY id DESC LIMIT 1",
            (handle["id"],),
        ).fetchone()
        attempts.append(
            {
                "attempt_id": row["id"],
                "handle_id": row["handle_id"],
                "attempt_number": row["attempt_number"],
                "lane": row["lane"],
                "status": row["status"],
                "receipt_sha256": scheduler.sha256(path),
            }
        )

    control_receipt = tmp_path / "control.json"
    scheduler.atomic_json(control_receipt, {"legacy": True})
    con.execute(
        """INSERT INTO controls
           (campaign_id,symbol,side,fold,npz_sha256,metrics_json,receipt_path)
           VALUES('test','P0','LONG','D1',?,'{}',?)""",
        ("a" * 64, str(control_receipt)),
    )
    con.commit()
    evidence = {
        "schema_version": 1,
        "campaign_id": "test",
        "binding_basis": (
            "legacy receipts lacked code_contract; exact attempt/receipt SHA "
            "allowlist completed before the observed replacement deployment"
        ),
        "old_contract": {
            "files": {
                "v8_vec_sweep.py": "a" * 64,
                "wt_dc_entry_scorer.py": "b" * 64,
                "tools/recent_tradier_bundle_scheduler.py": "c" * 64,
            }
        },
        "replacement_contract": replacement,
        "finished_before": "2999-01-01T00:00:00+00:00",
        "attempts": attempts,
    }
    evidence_path = tmp_path / "evidence.json"
    scheduler.atomic_json(evidence_path, evidence)

    preview = scheduler.invalidate_legacy_code_contract(
        con, tmp_path, evidence_path=evidence_path
    )
    assert preview["attempt_count"] == 2
    assert preview["control_count"] == 1
    assert con.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 2

    applied = scheduler.invalidate_legacy_code_contract(
        con, tmp_path, evidence_path=evidence_path, apply=True
    )
    assert applied["applied"] is True
    assert con.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM controls").fetchone()[0] == 0
    assert scheduler.meta_get(con, "claim_sequence") == "0"
    assert json.loads(scheduler.meta_get(con, "code_contract_json")) == replacement
    handles = con.execute(
        "SELECT status,attempts,fail_streak FROM handles"
    ).fetchall()
    assert all(tuple(row) == ("PENDING", 0, 0) for row in handles)
    archive = scheduler.Path(applied["archive"])
    assert len(list(archive.glob("attempt_*.json"))) == 2
    assert len(list(archive.glob("control_*.json"))) == 1
    manifest = json.loads((archive / "invalidation_manifest.json").read_text())
    assert manifest["applied"] is True
    con.close()
