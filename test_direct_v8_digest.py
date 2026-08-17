from __future__ import annotations

import json
from datetime import datetime, timezone

from tools import direct_v8_digest as digest


def _row(run_id, *, finished, gain_mo, delta_mo, qualified, override):
    return {
        "run_id": run_id,
        "source": "autonomous_direct_v8_combination",
        "state": "AUDITED",
        "symbol": "MU",
        "requested_symbol": "MU",
        "side": "LONG",
        "months": 12,
        "capital_deployment_policy": "full_unlevered_fresh_entry_v1",
        "accounting_verified": True,
        "capital_normalized": True,
        "finished_at": finished,
        "gain_per_mo_pct": gain_mo,
        "gain_pct": gain_mo * 12,
        "bh_gain_per_mo_pct": 5.0,
        "bh_gain_pct": 60.0,
        "delta_vs_bh_per_mo_pct": delta_mo,
        "delta_vs_bh_pct": delta_mo * 12,
        "max_dd_pct": 20.0,
        "tim_pct": 50.0,
        "win_rate_pct": 60.0,
        "closes_per_month": 12.0,
        "screen_qualified": qualified,
        "screen_qualification_reasons": [] if qualified else ["TIM_OUTSIDE_20_75"],
        "overrides": override,
    }


def test_digest_ignores_legacy_rows_and_reports_since_cursor(tmp_path, monkeypatch):
    status_path = tmp_path / "STATUS.json"
    status_path.write_text(json.dumps({
        "updated_at": "2026-08-06T00:00:00Z",
        "manual_runs": [
            {"source": "autonomous_direct_v8_combination", "state": "AUDITED", "months": 12,
             "symbol": "OLD", "side": "LONG", "capital_deployment_policy": None},
            _row("new", finished="2026-08-06T00:00:00Z", gain_mo=8.0, delta_mo=3.0,
                 qualified=True, override={"BB_FROZEN_STOP_ENABLED": True}),
        ],
        "direct_v8_combiner": {"stage": "BOUNDARY_REPAIR_12M_SEARCH", "baseline_receipts": 12,
                               "baseline_required": 12, "combination_audited": 1,
                               "boundary_clearing_receipts": 1},
        "direct_v8_path_census": {"stage": "PATH_CENSUS_12M_RUNNING", "completed_value_probes": 7,
                                  "declared_value_probes": 2373, "behavior_proven_value_probes": 2,
                                  "read_but_not_triggered_value_probes": 3, "wiring_unproven_value_probes": 1},
        "alerts": [],
    }))
    monkeypatch.setenv("DIRECT_V8_STATUS_PATH", str(status_path))
    monkeypatch.setattr(digest, "STATE_PATH", tmp_path / "digest-state.json")
    cursor = datetime(2026, 8, 5, tzinfo=timezone.utc).timestamp()

    snap = digest.snapshot(now=datetime(2026, 8, 6, 1, tzinfo=timezone.utc).timestamp(),
                           state={"direct_v8": {"last_sent_ts": cursor}})

    assert len(snap["rows"]) == 1
    assert len(snap["since"]) == 1
    rendered = digest.render_section(snap)
    assert "BB_FROZEN_STOP_ENABLED=true" in rendered
    assert "SWITCH_MATRIX" in rendered
    assert "PATH_CENSUS_12M_RUNNING" in rendered
    assert "7/2373 matched values" in rendered
    assert "8.00%" in rendered


def test_best_result_prefers_screen_qualified_before_raw_return():
    rows = [
        _row("bad-high-return", finished="2026-08-06T00:00:00Z", gain_mo=20.0, delta_mo=15.0,
             qualified=False, override={"DC_LOW_STOP_ENABLED": True}),
        _row("good-screen", finished="2026-08-06T00:01:00Z", gain_mo=5.0, delta_mo=1.0,
             qualified=True, override={"BB_FROZEN_STOP_ENABLED": True}),
    ]
    best = digest.best_by_symbol_side(rows)[("MU", "LONG")]
    assert best["run_id"] == "good-screen"


def test_historical_npz_window_does_not_expire_from_digest(tmp_path, monkeypatch):
    status_path = tmp_path / "STATUS.json"
    row = _row("historical", finished="2025-08-06T00:00:00Z", gain_mo=4.0,
               delta_mo=1.0, qualified=True, override={})
    row["window_end_timestamp"] = datetime(2025, 8, 6, tzinfo=timezone.utc).timestamp()
    status_path.write_text(json.dumps({"manual_runs": [row], "alerts": []}))
    monkeypatch.setenv("DIRECT_V8_STATUS_PATH", str(status_path))
    monkeypatch.setattr(digest, "STATE_PATH", tmp_path / "digest-state.json")

    snap = digest.snapshot(now=datetime(2026, 8, 6, tzinfo=timezone.utc).timestamp())

    assert [row["run_id"] for row in snap["rows"]] == ["historical"]
    assert snap["stale_receipt_count"] == 0
    assert "Current-data warning" not in digest.render_section(snap)


def test_negative_audited_receipt_stays_out_of_result_digest(tmp_path, monkeypatch):
    status_path = tmp_path / "STATUS.json"
    negative = _row("negative", finished="2026-08-06T00:00:00Z", gain_mo=-2.0,
                    delta_mo=1.0, qualified=False, override={})
    status_path.write_text(json.dumps({"manual_runs": [negative], "alerts": []}))
    monkeypatch.setenv("DIRECT_V8_STATUS_PATH", str(status_path))
    monkeypatch.setattr(digest, "STATE_PATH", tmp_path / "digest-state.json")

    snap = digest.snapshot(now=datetime(2026, 8, 6, tzinfo=timezone.utc).timestamp())

    assert snap["rows"] == []
    assert snap["since"] == []
