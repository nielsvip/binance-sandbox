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
