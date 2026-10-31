import json
import math

import pytest

import tools.paper_counterfactual_sql as sql_ingest
from tools.paper_counterfactual_sql import ingest_counterfactual
from tools.paper_scenario_db import ScenarioDB


def _write_events(root, events):
    directory = root / "data" / "paper_nonvec"
    directory.mkdir(parents=True)
    with (directory / "events_20261031.jsonl").open("w", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event) + "\n")


def _write_scores(root, records, generated_at="2026-10-31T12:00:00Z"):
    directory = root / "data" / "paper_forward_comprehensive"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "counterfactual_action_scores.json").write_text(
        json.dumps({"generated_at": generated_at, "records": records}), encoding="utf-8"
    )


def test_ingests_deduplicates_raw_events_and_keeps_route_views_separate(tmp_path):
    events = [
        {"ts": "2026-10-31T10:00:00Z", "venue": "crypto", "account": "a", "position_key": "a:BTCUSDC_LONG", "action": "CLOSE", "fn": "QUICK_WT_CROSS_EXIT_1H_BEAR", "_t": 1, "_venue": "crypto", "_fam": "stale"},
        {"ts": "2026-10-31T10:01:00Z", "venue": "crypto", "account": "a", "position_key": "a:ETHUSDC_LONG", "action": "CLOSE", "fn": "QUICK_FAST_EXIT_5m"},
        {"ts": "2026-10-31T10:02:00Z", "venue": "tradier", "account": "b", "position_key": "b:AAPL_LONG", "action": "CLOSE", "fn": "OPAQUE_PATH"},
    ]
    _write_events(tmp_path, [events[0], {**events[0], "_t": 99}, events[1], events[2]])
    db = ScenarioDB(tmp_path / "scenario.sqlite")
    counts = ingest_counterfactual(db, tmp_path)
    assert counts["actions_inserted"] == 3
    assert counts["actions_deduplicated"] == 1
    rows = db.query("SELECT path_name,route,canonical_switch,source_json FROM counterfactual_actions ORDER BY source_ts")
    assert rows[0]["route"] == "vector_backtest"
    assert rows[0]["canonical_switch"] == "WT_CROSS_EXIT_ENABLED"
    assert "_t" not in rows[0]["source_json"]
    candidates = db.query("SELECT route,comparison_status FROM forward_counterfactual_candidates ORDER BY source_ts")
    assert [(row["route"], row["comparison_status"]) for row in candidates] == [("native_counterfactual", "PENDING_COMPARISON")]
    assert db.query("SELECT route FROM counterfactual_actions WHERE path_name='OPAQUE_PATH'")[0]["route"] == "needs_wiring_audit"
    assert len(db.query("SELECT * FROM vector_control_actions")) == 1
    stocks = db.query("SELECT venue,source_json FROM counterfactual_actions WHERE path_name='OPAQUE_PATH'")[0]
    assert stocks["venue"] == "stocks"
    assert json.loads(stocks["source_json"])["venue"] == "tradier"
    assert db.query("SELECT * FROM counterfactual_positive_diagnostics") == []
    db.close()


def test_imports_negative_positive_pending_versions_idempotently(tmp_path):
    events = [
        {"ts": f"2026-10-31T10:0{i}:00Z", "venue": "crypto", "account": "a", "position_key": f"a:SYM{i}USDC_LONG", "action": "CLOSE", "fn": f"PATH_{i}"}
        for i in range(3)
    ]
    _write_events(tmp_path, events)
    records = [
        {"event": events[0], "path": "PATH_0", "status": "SCORED", "delta_fraction": -0.02, "method": "paired"},
        {"event": events[1], "path": "PATH_1", "status": "SCORED", "delta_fraction": 0.03, "method": "paired"},
        {"event": events[2], "path": "PATH_2", "status": "PENDING_BASELINE", "delta_fraction": None, "method": "paired"},
    ]
    _write_scores(tmp_path, records)
    db = ScenarioDB(tmp_path / "scenario.sqlite")
    first = ingest_counterfactual(db, tmp_path)
    assert first["outcomes_inserted"] == 3
    rows = db.query("SELECT status,delta_fraction FROM counterfactual_outcomes ORDER BY action_id")
    assert sorted((r["status"], r["delta_fraction"]) for r in rows) == [
        ("PENDING_BASELINE", None), ("SCORED", -0.02), ("SCORED", 0.03)
    ]
    # PATH_1 is unclassified, so its positive cannot enter the candidate view.
    assert db.query("SELECT * FROM counterfactual_positive_diagnostics") == []
    _write_scores(tmp_path, records, generated_at="2026-11-01T00:00:00Z")
    second = ingest_counterfactual(db, tmp_path)
    assert second["outcomes_deduplicated"] == 3
    assert db.query("SELECT COUNT(*) AS n FROM counterfactual_outcomes")[0]["n"] == 3
    assert {r["computed_at"] for r in db.query("SELECT computed_at FROM counterfactual_outcomes")} == {"2026-10-31T12:00:00Z"}
    db.close()


def test_positive_view_uses_latest_outcome_and_excludes_vector_and_audit(tmp_path):
    native = {"ts": "2026-10-31T10:00:00Z", "venue": "crypto", "account": "a", "position_key": "a:ETHUSDC_LONG", "action": "CLOSE", "fn": "QUICK_FAST_EXIT_5m"}
    vector = {"ts": "2026-10-31T10:01:00Z", "venue": "crypto", "account": "a", "position_key": "a:BTCUSDC_LONG", "action": "CLOSE", "fn": "QUICK_WT_CROSS_EXIT_1H_BEAR"}
    audit = {"ts": "2026-10-31T10:02:00Z", "venue": "crypto", "account": "a", "position_key": "a:XRPUSDC_LONG", "action": "CLOSE", "fn": "OPAQUE"}
    scored_native = {"ts": "2026-10-31T10:03:00Z", "venue": "crypto", "account": "a", "position_key": "a:ADAUSDC_LONG", "action": "CLOSE", "fn": "QUICK_FAST_EXIT_5m"}
    _write_events(tmp_path, [native, vector, audit, scored_native])
    records = [
        {"event": native, "status": "SCORED", "delta_fraction": 0.1, "method": "paired", "computed_at": "2026-10-31T11:00:00Z"},
        {"event": native, "status": "PENDING_BASELINE", "delta_fraction": None, "method": "paired", "computed_at": "2026-10-31T12:00:00Z"},
        {"event": vector, "status": "SCORED", "delta_fraction": 0.2, "method": "paired"},
        {"event": audit, "status": "SCORED", "delta_fraction": 0.3, "method": "paired"},
        {"event": scored_native, "status": "SCORED", "delta_fraction": 0.4, "method": "paired"},
    ]
    _write_scores(tmp_path, records)
    db = ScenarioDB(tmp_path / "scenario.sqlite")
    ingest_counterfactual(db, tmp_path)
    positives = db.query("SELECT path_name,delta_fraction FROM counterfactual_positive_diagnostics")
    assert [(row["path_name"], row["delta_fraction"]) for row in positives] == [("QUICK_FAST_EXIT_5m", 0.4)]
    native_row = db.query("SELECT * FROM forward_counterfactual_candidates WHERE path_name='QUICK_FAST_EXIT_5m'")[0]
    assert native_row["comparison_status"] == "PENDING_BASELINE"
    assert native_row["delta_fraction"] is None
    db.close()


def test_reclassification_appends_routing_revision_without_mutating_action(tmp_path, monkeypatch):
    event = {"ts": "2026-10-31T10:00:00Z", "venue": "crypto", "account": "a", "position_key": "a:ETHUSDC_LONG", "action": "CLOSE", "fn": "OPAQUE"}
    _write_events(tmp_path, [event])
    db = ScenarioDB(tmp_path / "scenario.sqlite")
    monkeypatch.setattr(sql_ingest, "_classifier_hash", lambda: "a-old")
    monkeypatch.setattr(sql_ingest, "_now", lambda: "2026-10-31T12:00:00Z")
    ingest_counterfactual(db, tmp_path)
    initial = db.query("SELECT action_id,route,canonical_switch FROM counterfactual_actions")[0]

    def revised_classifier(path_name, metadata=None, venue=None):
        return {"route": "vector_backtest", "canonical_switch": "REVISED_SWITCH", "timeframe": "15m", "evidence": ["revised classifier"]}

    monkeypatch.setattr(sql_ingest, "classify_path", revised_classifier)
    monkeypatch.setattr(sql_ingest, "_classifier_hash", lambda: "z-new")
    monkeypatch.setattr(sql_ingest, "_now", lambda: "2026-10-31T13:00:00Z")
    counts = ingest_counterfactual(db, tmp_path)
    assert counts["routing_revisions_inserted"] == 1
    current = db.query("SELECT action_id,route,canonical_switch FROM counterfactual_actions")[0]
    assert tuple(current) == tuple(initial)
    assert db.query("SELECT COUNT(*) AS n FROM counterfactual_routing")[0]["n"] == 2
    assert db.query("SELECT route FROM vector_control_actions")[0]["route"] == "vector_backtest"
    assert db.query("SELECT * FROM forward_counterfactual_candidates") == []
    db.close()


@pytest.mark.parametrize("bad", [True, math.inf, -math.inf, float("nan"), "0.1"])
def test_rejects_boolean_nonfinite_or_nonnumeric_delta(tmp_path, bad):
    event = {"ts": "2026-10-31T10:00:00Z", "venue": "crypto", "account": "a", "position_key": "a:BTCUSDC_LONG", "action": "CLOSE", "fn": "PATH"}
    _write_events(tmp_path, [event])
    _write_scores(tmp_path, [{"event": event, "status": "SCORED", "delta_fraction": bad, "method": "paired"}])
    db = ScenarioDB(tmp_path / "scenario.sqlite")
    with pytest.raises(ValueError, match="delta_fraction"):
        ingest_counterfactual(db, tmp_path)
    db.close()


@pytest.mark.parametrize(
    "status,delta",
    [("SCORED", None), ("PENDING_BASELINE", 0.0)],
)
def test_rejects_status_delta_mismatch(tmp_path, status, delta):
    event = {"ts": "2026-10-31T10:00:00Z", "venue": "crypto", "account": "a", "position_key": "a:BTCUSDC_LONG", "action": "CLOSE", "fn": "PATH"}
    _write_events(tmp_path, [event])
    _write_scores(tmp_path, [{"event": event, "status": status, "delta_fraction": delta, "method": "paired"}])
    db = ScenarioDB(tmp_path / "scenario.sqlite")
    with pytest.raises(ValueError, match="outcome requires"):
        ingest_counterfactual(db, tmp_path)
    db.close()
