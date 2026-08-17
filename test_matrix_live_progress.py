from __future__ import annotations

import json
import sqlite3
import time

import chart_server
from tools import matrix_live_progress as progress
from tools import param_matrix_daemon as daemon
from tools import refresh_matrix_progress_snapshot as refresh_snapshot


def test_atomic_worker_updates_preserve_six_slots(tmp_path) -> None:
    path = tmp_path / "data" / "reports" / "matrix_live_progress.json"
    progress.update_worker(
        path,
        1,
        worker_id="vec-1",
        parameter="WT_DC_EXIT_THRESHOLD",
        value="45",
        symbol="MU",
        side="LONG",
        combination="ENTRY+EXIT+REENTER",
        stage="VECTOR",
        status="TESTING",
        cells_remaining=825,
        total_cells=826,
        provisional_filled=1,
        exact_verified=0,
        accepted_cells_per_minute=2,
        rejected_cells=1,
        now_epoch=100.0,
    )
    progress.update_worker(
        path,
        6,
        worker_id="vec-6",
        parameter="DC_RECLAIM_BUFFER",
        value="0.5",
        symbol="TTD",
        side="SHORT",
        combination="EXIT+REENTER",
        stage="AUDIT",
        status="MOVED",
        cells_remaining=824,
        provisional_filled=2,
        accepted_cells_per_minute=3,
        quarantined_cells=1,
        now_epoch=101.0,
    )

    parsed = json.loads(path.read_text())
    assert parsed["schema"] == progress.SCHEMA
    assert len(parsed["workers"]) == 6
    assert parsed["workers"][0]["parameter"] == "WT_DC_EXIT_THRESHOLD"
    assert parsed["workers"][5]["status"] == "MOVED"
    assert parsed["cells_remaining"] == 824
    assert parsed["total_cells"] == 826
    assert parsed["exact_verified"] == 0
    assert parsed["accepted_cells_per_minute"] == 5
    assert parsed["rejected_cells"] == 1
    assert parsed["quarantined_cells"] == 1
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_dashboard_snapshot_marks_worker_and_file_stale(tmp_path) -> None:
    path = tmp_path / "progress.json"
    progress.update_worker(
        path,
        2,
        status="TESTING",
        cells_remaining=40,
        now_epoch=100.0,
    )

    payload = progress.dashboard_snapshot(
        path, stale_after_seconds=10, now_epoch=115.0
    )
    assert payload["source_available"] is True
    assert payload["stale"] is True
    assert payload["workers"][1]["age_seconds"] == 15.0
    assert payload["workers"][1]["stale"] is True
    assert payload["workers"][0]["stale"] is True

    missing = progress.dashboard_snapshot(
        tmp_path / "missing.json", stale_after_seconds=10, now_epoch=115.0
    )
    assert missing["source_available"] is False
    assert missing["stale"] is True
    assert len(missing["workers"]) == 6


def test_terminal_complete_is_distinct_from_stale_heartbeat(tmp_path) -> None:
    path = tmp_path / "progress.json"
    payload = progress.empty_snapshot()
    payload["producer_state"] = "VECTOR_DISCOVERY_COMPLETE"
    payload["producer_request_id"] = "vd-20260802t071859z-a11ce507"
    payload["vector_updated_at_epoch"] = 100.0
    payload["vector_updated_at"] = "1970-01-01T00:01:40Z"
    payload["vector_keys_remaining"] = 0
    for worker in payload["workers"]:
        worker.update({
            "run_class": "VECTOR_DISCOVERY",
            "status": "COMPLETE",
            "stage": "SLOT_COMPLETE",
            "last_update_epoch": 100.0,
        })
    progress.atomic_write_snapshot(path, payload)

    dashboard = progress.dashboard_snapshot(
        path, stale_after_seconds=10, now_epoch=200.0
    )
    assert dashboard["stale"] is True
    assert dashboard["heartbeat_stale"] is True
    assert dashboard["producer_terminal"] is True
    assert dashboard["producer_health_status"] == "TERMINAL_COMPLETE"


def test_scalar_strict_terminal_statuses_produce_scalar_complete_state(
    tmp_path,
) -> None:
    path = tmp_path / "progress.json"
    request_id = "vas-20260802t090000z-deadbeef"
    for slot in range(1, 7):
        progress.update_worker(
            path,
            slot,
            worker_id=f"vec-approx-slot-{slot}",
            run_class="VECTOR_DISCOVERY",
            status="COMPLETE_STRICT",
            stage="AUDIT",
            worker_keys_remaining=0,
            accepted_cells_per_minute=0,
            producer_request_id=request_id,
            now_epoch=100.0 + slot,
        )
    stored = progress.read_snapshot(path)
    assert stored["producer_state"] == "VECTOR_APPROX_SCALAR_COMPLETE"
    dashboard = progress.dashboard_snapshot(
        path, stale_after_seconds=10, now_epoch=200.0
    )
    assert dashboard["stale"] is True
    assert dashboard["producer_terminal"] is True
    assert dashboard["producer_health_status"] == "TERMINAL_COMPLETE"


def test_5077_overlays_request_bound_terminal_counters(
    monkeypatch, tmp_path
) -> None:
    request_id = "vd-20260802t071859z-a11ce507"
    monkeypatch.setattr(chart_server, "BASE_PATH", tmp_path)
    monkeypatch.delenv("MATRIX_PROGRESS_SNAPSHOT", raising=False)
    selector = tmp_path / "data/sync/VECTOR_DISCOVERY_PULL_REQUEST.json"
    selector.parent.mkdir(parents=True)
    selector.write_text(json.dumps({
        "schema": "vector-discovery-pull-request-v1",
        "action": "WATCH_FIXED_VECTOR_DISCOVERY_FROM_S1",
        "request_id": request_id,
        "requested_at_epoch": time.time() - 1,
    }))
    snapshot = (
        tmp_path / "data/sync/vector_discovery_progress"
        / f"{request_id}.json"
    )
    payload = progress.empty_snapshot()
    payload.update({
        "producer_state": "VECTOR_DISCOVERY_COMPLETE",
        "producer_request_id": request_id,
        "vector_updated_at_epoch": 100.0,
        "vector_updated_at": "1970-01-01T00:01:40Z",
        "vector_keys_remaining": 0,
    })
    progress.atomic_write_snapshot(snapshot, payload)
    summary = (
        tmp_path / "data/sync/vector_discovery_terminal_summary"
        / f"{request_id}.json"
    )
    summary.parent.mkdir(parents=True)
    summary.write_text(json.dumps({
        "schema": "vector-discovery-terminal-summary-v1",
        "request_id": request_id,
        "s1_status": "COMPLETE_BLOCKED",
        "authority": "REQUEST_BOUND_PULLED_CAMPAIGN_RECEIPT",
        "source_receipt_sha256": "a" * 64,
        "cohort_key_count": 25,
        "ready_key_count": 25,
        "candidate_recipe_count": 450,
        "accepted_vector_recipe_count": 7,
        "authoritative_engine_pass_cell_count": 0,
        "authoritative_matrix_write_count": 0,
    }))

    response = chart_server.app.test_client().get(
        "/api/matrix_live_progress"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["stale"] is True
    assert body["producer_health_status"] == "TERMINAL_COMPLETE"
    assert body["terminal_campaign_counters"] == {
        "authority": "REQUEST_BOUND_PULLED_CAMPAIGN_RECEIPT",
        "request_id": request_id,
        "source_receipt_sha256": "a" * 64,
        "cohort_key_count": 25,
        "ready_key_count": 25,
        "candidate_recipe_count": 450,
        "accepted_vector_recipe_count": 7,
        "authoritative_engine_pass_cell_count": 0,
        "authoritative_matrix_write_count": 0,
    }


def test_5077_newer_scalar_selector_beats_old_lifecycle_and_overlays_terminal(
    monkeypatch, tmp_path
) -> None:
    vd_id = "vd-20260802t071859z-a11ce507"
    vas_id = "vas-20260802t090000z-deadbeef"
    monkeypatch.setattr(chart_server, "BASE_PATH", tmp_path)
    monkeypatch.delenv("MATRIX_PROGRESS_SNAPSHOT", raising=False)
    sync = tmp_path / "data/sync"
    sync.mkdir(parents=True)
    (sync / "VECTOR_DISCOVERY_PULL_REQUEST.json").write_text(json.dumps({
        "schema": "vector-discovery-pull-request-v1",
        "action": "WATCH_FIXED_VECTOR_DISCOVERY_FROM_S1",
        "request_id": vd_id,
        "requested_at_epoch": 100.0,
    }))
    (sync / "VECTOR_APPROX_SCALAR_PULL_REQUEST.json").write_text(json.dumps({
        "schema": "vector-approx-scalar-pull-request-v1",
        "action": "WATCH_FIXED_SIX_SLOT_VECTOR_APPROX_SCALAR_FROM_S1",
        "request_id": vas_id,
        "requested_at_epoch": 200.0,
    }))
    vd_progress = sync / "vector_discovery_progress" / f"{vd_id}.json"
    vd_payload = progress.empty_snapshot()
    vd_payload["producer_request_id"] = vd_id
    progress.atomic_write_snapshot(vd_progress, vd_payload)
    vas_progress = (
        sync / "vector_approx_scalar_progress" / f"{vas_id}.json"
    )
    vas_payload = progress.empty_snapshot()
    vas_payload.update({
        "producer_request_id": vas_id,
        # Older scalar producers used the shared lifecycle state name. The
        # request-bound terminal receipt supplies the specific terminal truth.
        "producer_state": "VECTOR_DISCOVERY_RUNNING",
        "vector_updated_at_epoch": 100.0,
        "vector_updated_at": "1970-01-01T00:01:40Z",
    })
    progress.atomic_write_snapshot(vas_progress, vas_payload)
    summary = (
        sync / "vector_approx_scalar_terminal_summary" / f"{vas_id}.json"
    )
    summary.parent.mkdir(parents=True)
    summary.write_text(json.dumps({
        "schema": "vector-approx-scalar-terminal-summary-v1",
        "request_id": vas_id,
        "s1_status": "COMPLETE_READY",
        "fleet_status": "COMPLETE_STRICT",
        "authority": "REQUEST_BOUND_PULLED_SCALAR_FLEET_RECEIPT",
        "source_receipt_sha256": "b" * 64,
        "key_count": 6,
        "terminal_key_count": 6,
        "keys_remaining": 0,
        "strict_passed_cell_count": 1352,
        "raw_result_row_count": 1352,
        "ranked_exact_candidate_count": 24,
        "moved_cell_count": 77,
        "inert_cell_count": 1275,
        "zero_trade_cell_count": 0,
        "authoritative_engine_pass_cell_count": 0,
        "authoritative_matrix_write_count": 0,
    }))

    assert chart_server._matrix_progress_snapshot_path() == vas_progress
    body = chart_server.app.test_client().get(
        "/api/matrix_live_progress"
    ).get_json()
    assert body["producer_request_id"] == vas_id
    assert body["producer_state"] == "VECTOR_APPROX_SCALAR_COMPLETE"
    assert body["producer_health_status"] == "TERMINAL_COMPLETE"
    assert body["stale"] is True
    assert body["heartbeat_stale"] is True
    assert body["terminal_scalar_counters"][
        "strict_passed_cell_count"
    ] == 1352
    assert body["terminal_scalar_counters"][
        "ranked_exact_candidate_count"
    ] == 24
    assert body["terminal_scalar_counters"][
        "authoritative_engine_pass_cell_count"
    ] == 0


def test_5077_newer_lifecycle_selector_beats_old_scalar(
    monkeypatch, tmp_path
) -> None:
    vd_id = "vd-20260802t100000z-a11ce507"
    vas_id = "vas-20260802t090000z-deadbeef"
    monkeypatch.setattr(chart_server, "BASE_PATH", tmp_path)
    monkeypatch.delenv("MATRIX_PROGRESS_SNAPSHOT", raising=False)
    sync = tmp_path / "data/sync"
    sync.mkdir(parents=True)
    selectors = (
        (
            "VECTOR_DISCOVERY_PULL_REQUEST.json",
            "vector-discovery-pull-request-v1",
            "WATCH_FIXED_VECTOR_DISCOVERY_FROM_S1",
            vd_id,
            300.0,
        ),
        (
            "VECTOR_APPROX_SCALAR_PULL_REQUEST.json",
            "vector-approx-scalar-pull-request-v1",
            "WATCH_FIXED_SIX_SLOT_VECTOR_APPROX_SCALAR_FROM_S1",
            vas_id,
            200.0,
        ),
    )
    for name, schema, action, request_id, epoch in selectors:
        (sync / name).write_text(json.dumps({
            "schema": schema,
            "action": action,
            "request_id": request_id,
            "requested_at_epoch": epoch,
        }))
    vd_progress = sync / "vector_discovery_progress" / f"{vd_id}.json"
    vd_payload = progress.empty_snapshot()
    vd_payload["producer_request_id"] = vd_id
    progress.atomic_write_snapshot(vd_progress, vd_payload)
    vas_progress = (
        sync / "vector_approx_scalar_progress" / f"{vas_id}.json"
    )
    vas_payload = progress.empty_snapshot()
    vas_payload["producer_request_id"] = vas_id
    progress.atomic_write_snapshot(vas_progress, vas_payload)

    assert chart_server._matrix_progress_snapshot_path() == vd_progress


def test_5077_scalar_preflight_failure_is_terminal_without_fleet_receipt(
    monkeypatch, tmp_path
) -> None:
    request_id = "vas-20260802t090630z-f1ee7006"
    monkeypatch.setattr(chart_server, "BASE_PATH", tmp_path)
    monkeypatch.delenv("MATRIX_PROGRESS_SNAPSHOT", raising=False)
    sync = tmp_path / "data/sync"
    sync.mkdir(parents=True)
    (sync / "VECTOR_APPROX_SCALAR_PULL_REQUEST.json").write_text(json.dumps({
        "schema": "vector-approx-scalar-pull-request-v1",
        "action": "WATCH_FIXED_SIX_SLOT_VECTOR_APPROX_SCALAR_FROM_S1",
        "request_id": request_id,
        "requested_at_epoch": 200.0,
    }))
    bound = sync / "vector_approx_scalar_progress" / f"{request_id}.json"
    payload = progress.empty_snapshot()
    payload.update({
        "producer_request_id": request_id,
        "producer_state": "VECTOR_APPROX_SCALAR_RUNNING",
        "candidate_recipes_per_minute": 99.0,
    })
    progress.atomic_write_snapshot(bound, payload)
    pulled = (
        tmp_path
        / "data/reports/vector_approx_scalar_s1_pulls"
        / request_id
        / "S1_VECTOR_APPROX_SCALAR_STATUS.json"
    )
    pulled.parent.mkdir(parents=True)
    pulled.write_text(json.dumps({
        "request_id": request_id,
        "status": "COMPLETE_BLOCKED",
        "reason": "BACKTEST_DATA_CONTRACT_FAILED:PWR:core",
        "exact_v8_invoked": False,
        "live_config_write_attempted": False,
        "database_write_attempted": False,
        "workbook_write_attempted": False,
        "matrix_write_attempted": False,
        "canonical_write_attempted": False,
    }))

    body = chart_server.app.test_client().get(
        "/api/matrix_live_progress"
    ).get_json()
    assert body["producer_state"] == "VECTOR_APPROX_SCALAR_TERMINAL_BLOCKED"
    assert body["producer_terminal"] is True
    assert body["producer_terminal_reason"] == (
        "BACKTEST_DATA_CONTRACT_FAILED:PWR:core"
    )
    assert body["candidate_recipes_per_minute"] == 0.0
    assert all(row["status"] == "TERMINAL" for row in body["workers"])


def test_5077_uses_selected_scalar_global_snapshot_until_bound_pull_arrives(
    monkeypatch, tmp_path
) -> None:
    vas_id = "vas-20260802t090000z-deadbeef"
    monkeypatch.setattr(chart_server, "BASE_PATH", tmp_path)
    monkeypatch.delenv("MATRIX_PROGRESS_SNAPSHOT", raising=False)
    selector = tmp_path / "data/sync/VECTOR_APPROX_SCALAR_PULL_REQUEST.json"
    selector.parent.mkdir(parents=True)
    selector.write_text(json.dumps({
        "schema": "vector-approx-scalar-pull-request-v1",
        "action": "WATCH_FIXED_SIX_SLOT_VECTOR_APPROX_SCALAR_FROM_S1",
        "request_id": vas_id,
        "requested_at_epoch": 200.0,
    }))
    global_snapshot = progress.default_snapshot_path(tmp_path)
    payload = progress.empty_snapshot()
    payload["producer_request_id"] = vas_id
    progress.atomic_write_snapshot(global_snapshot, payload)

    assert chart_server._matrix_progress_snapshot_path() == global_snapshot


def test_port_5077_progress_api_and_page_use_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(chart_server, "BASE_PATH", tmp_path)
    monkeypatch.delenv("MATRIX_PROGRESS_SNAPSHOT", raising=False)
    monkeypatch.setenv("MATRIX_PROGRESS_STALE_SECONDS", "90")
    path = progress.default_snapshot_path(tmp_path)
    progress.update_worker(
        path,
        3,
        worker_id="worker-c",
        parameter="ENTRY_ZONE_LONG",
        value="0.25",
        symbol="NVDA",
        side="LONG",
        combination="ENTRY+AUGMENT",
        stage="VECTOR",
        status="TESTING",
        cells_remaining=321,
        total_cells=826,
        provisional_filled=505,
        exact_verified=17,
        now_epoch=time.time(),
    )
    progress.update_blocker_panel(
        path,
        canonical_uniqueness={
            "authority": "CANONICAL_PUBLISHED_RECEIPT",
            "status": "BLOCKED",
            "quarantined_numeric_overlays": 429,
            "metric_collision_groups": 18,
            "fingerprint_collision_groups": 19,
        },
        prospective_repaired_code_audit={
            "authority": "PROSPECTIVE_READ_ONLY_NON_CANONICAL",
            "status": "UNPUBLISHED",
        },
        sync={
            "authority": "SYNC_ONLY_NOT_PRODUCTION",
            "status": "PASS_TIMESTAMP_SIZE_MATCH",
        },
    )
    summary = tmp_path / "chart_static/matrix_canonical_summary.json"
    summary.write_text(json.dumps({
        "schema": "switch-matrix-canonical-summary-v1",
        "status": "PASS",
        "total_cells": 900,
        "exact_verified": 17,
        "provisional_filled": 579,
        "cells_remaining": 321,
        "exact_cells_remaining": 883,
        "quarantined_cells": 0,
        "updated_at_epoch": time.time(),
    }))

    client = chart_server.app.test_client()
    response = client.get("/api/matrix_live_progress")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["workers"][2]["parameter"] == "ENTRY_ZONE_LONG"
    assert payload["workers"][2]["combination"] == "ENTRY+AUGMENT"
    assert payload["cells_remaining"] == 321
    assert payload["exact_cells_remaining"] == 883
    assert payload["total_cells"] == 900
    assert payload["stale"] is False
    assert payload["blocker_panel"]["canonical_uniqueness"][
        "quarantined_numeric_overlays"
    ] == 429
    assert payload["blocker_panel"]["prospective_repaired_code_audit"][
        "status"
    ] == "UNPUBLISHED"
    assert payload["blocker_panel"]["sync"]["status"] == (
        "PASS_TIMESTAMP_SIZE_MATCH"
    )
    assert response.headers["Cache-Control"].startswith("no-store")

    page = client.get("/matrix_live_progress")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Six worker slots" in body
    assert "parameter" in body
    assert "combination" in body
    assert "cells remaining" in body
    assert "accepted cells / minute" in body
    assert "terminal cumulative vector candidates" in body
    assert "terminal cumulative vector accepted" in body
    assert "terminal cumulative engine-pass cells" in body
    assert "COMPLETE · HEARTBEAT STOPPED" in body
    assert "worker heartbeat stopped as expected" in body
    assert "sync/dashboard freshness never counts as production" in body
    assert "setInterval(refresh,3000)" in body
    assert "/api/matrix_live_progress" in body


def test_pause_all_clears_every_stale_claim_and_zeroes_throughput(tmp_path) -> None:
    path = tmp_path / "progress.json"
    progress.update_worker(
        path,
        1,
        parameter="DUPLICATE_CULPRIT_FIXED",
        value="true",
        symbol="MU",
        side="LONG",
        combination="ENTRY+EXIT",
        status="TESTING",
        accepted_cells_per_minute=4,
        rejected_cells=2,
        quarantined_cells=1,
        now_epoch=100.0,
    )
    progress.pause_all_workers(path, now_epoch=110.0)

    payload = progress.dashboard_snapshot(path, now_epoch=111.0)
    assert payload["producer_state"] == "PAUSED"
    assert payload["accepted_cells_per_minute"] == 0
    assert payload["production_updated_at_epoch"] == 110.0
    assert len(payload["workers"]) == 6
    for worker in payload["workers"]:
        assert worker["status"] == "PAUSED_UNIQUENESS_REPAIR"
        assert worker["stage"] == "PAUSED"
        assert worker["accepted_cells_per_minute"] == 0
        assert worker["rejected_cells"] == 0
        assert worker["quarantined_cells"] == 0
        assert {
            worker["parameter"],
            worker["value"],
            worker["symbol"],
            worker["side"],
            worker["combination"],
        } == {"—"}


def test_repeated_pause_refresh_does_not_fake_production_heartbeat(tmp_path) -> None:
    path = tmp_path / "progress.json"
    first = progress.pause_all_workers(path, now_epoch=100.0)
    second = progress.pause_all_workers(path, now_epoch=900.0)

    assert first["production_updated_at_epoch"] == 100.0
    assert second["production_updated_at_epoch"] == 100.0
    assert all(row["last_update_epoch"] == 100.0 for row in second["workers"])
    assert second["surface_updated_at_epoch"] is None


def test_idle_transition_clears_pause_without_claiming_results(tmp_path) -> None:
    path = tmp_path / "progress.json"
    progress.pause_all_workers(path, now_epoch=100.0)
    idle = progress.idle_all_workers(path, now_epoch=200.0)

    assert idle["producer_state"] == "IDLE"
    assert idle["accepted_cells_per_minute"] == 0
    assert idle["production_updated_at_epoch"] == 200.0
    assert all(row["status"] == "IDLE_VECTOR_PREFLIGHT" for row in idle["workers"])
    assert all(row["stage"] == "VECTOR_PREFLIGHT" for row in idle["workers"])


def test_surface_refresh_does_not_refresh_production_health(tmp_path) -> None:
    path = tmp_path / "progress.json"
    progress.update_worker(path, 1, status="TESTING", now_epoch=100.0)
    progress.update_summary(
        path,
        total_cells=826,
        provisional_filled=500,
        exact_verified=20,
        cells_remaining=326,
        now_epoch=200.0,
    )
    payload = progress.dashboard_snapshot(
        path, stale_after_seconds=90, now_epoch=200.0
    )
    assert payload["surface_updated_at_epoch"] == 200.0
    assert payload["production_updated_at_epoch"] == 100.0
    assert payload["stale"] is True


def test_daemon_heartbeat_counts_only_authoritative_exact_pass_and_tracks_failures(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "progress.json"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "campaign": "stocks_repaired_20260730_c5",
                "workers": [
                    {
                        "tag": "w1",
                        "symbol": "MU",
                        "side": "LONG",
                        "expected_contract_fingerprint": "c5:exact",
                    }
                ],
            }
        )
    )
    db = tmp_path / "param_results_stocks.db"
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE param_cells ("
        "symbol TEXT, side TEXT, campaign TEXT, param TEXT, value_json TEXT, "
        "tier TEXT, validation_status TEXT, contract_fingerprint TEXT, ts TEXT, "
        "source_file TEXT)"
    )
    connection.executemany(
        "INSERT INTO param_cells VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "MU", "LONG", "stocks_repaired_20260730_c5", "A", "1",
                "ENGINE", "PASS", "c5:exact", "99", "a/result.json",
            ),
            # A persisted failed row must never increase accepted/min.
            (
                "MU", "LONG", "stocks_repaired_20260730_c5", "FAIL", "1",
                "ENGINE", "FAIL", "c5:exact", "99", "fail/result.json",
            ),
        ],
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(daemon.time, "time", lambda: 100.0)
    heartbeat = daemon.ProductionProgressHeartbeat(
        2,
        "pmx:test:1",
        "MU",
        "LONG",
        path=path,
        db_path=db,
        manifest_path=manifest,
        autostart=False,
    )
    heartbeat.set_remaining(10)
    heartbeat.set_current(
        parameter="WT_DC_EXIT_THRESHOLD",
        value="45",
        combination="WT_DC_EXIT_THRESHOLD+WT_DC_EXIT_ENABLED",
        stage="ENGINE_V8",
        status="TESTING",
    )
    heartbeat.persisted()
    heartbeat.rejected(status="REJECTED_NO_DB_CHANGE")
    heartbeat.quarantined(status="QUARANTINED_CONTRACT_CHANGED")

    current = progress.read_snapshot(path)["workers"][1]
    assert current["accepted_cells_per_minute"] == 0.1
    assert current["accepted_throughput_status"] == "AUTHORITATIVE_EXACT_PASS"
    assert current["accepted_throughput_source"] == (
        "param_results_stocks.db:param_cells"
    )
    assert current["accepted_window_seconds"] == 600
    assert current["rejected_cells"] == 1
    assert current["quarantined_cells"] == 1
    assert current["cells_remaining"] == 10

    heartbeat.emit(now_epoch=701.0, force_throughput_refresh=True)
    aged = progress.read_snapshot(path)["workers"][1]
    assert aged["accepted_cells_per_minute"] == 0


def test_manifest_order_resolves_stable_six_slots(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"workers": [{"tag": f"w{i}"} for i in range(1, 7)]})
    )
    assert daemon.progress_slot_for_tag(manifest, "w1") == 1
    assert daemon.progress_slot_for_tag(manifest, "w6") == 6
    assert daemon.progress_slot_for_tag(manifest, "missing") is None


def test_both_production_launchers_publish_pause_and_stable_slots() -> None:
    root = chart_server.BASE_PATH
    primary = (root / "tools" / "param_matrix_watchdog.sh").read_text()
    legacy_repaired = (root / "watchdog_lab_matrix.sh").read_text()
    for source in (primary, legacy_repaired):
        assert "MATRIX_WORKERS_PAUSED" in source
        assert "--pause-all" in source
        assert "PAUSED_UNIQUENESS_REPAIR" in source
        assert "--progress-slot" in source


def test_refresh_short_circuits_to_pause_before_canonical_surface_reads(
    monkeypatch, tmp_path
) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "MATRIX_WORKERS_PAUSED").write_text("paused\n")
    path = progress.default_snapshot_path(tmp_path)
    progress.update_worker(
        path,
        1,
        parameter="DUPLICATE_CULPRIT_FIXED",
        status="TESTING",
        accepted_cells_per_minute=9.5,
    )
    monkeypatch.setattr(refresh_snapshot, "ROOT", tmp_path)
    monkeypatch.setattr(
        refresh_snapshot,
        "canonical_counts",
        lambda: (_ for _ in ()).throw(AssertionError("surface read while paused")),
    )

    assert refresh_snapshot.main() == 0
    paused = progress.read_snapshot(path)
    assert paused["producer_state"] == "PAUSED"
    assert paused["accepted_cells_per_minute"] == 0.0
    assert all(
        row["status"] == "PAUSED_UNIQUENESS_REPAIR"
        for row in paused["workers"]
    )
    assert paused["blocker_panel"]["canonical_uniqueness"]["status"] == (
        "UNAVAILABLE"
    )
    assert paused["blocker_panel"]["prospective_repaired_code_audit"][
        "status"
    ] == "UNPUBLISHED"


def test_5077_blocker_panel_keeps_canonical_prospective_and_sync_separate(
    monkeypatch, tmp_path
) -> None:
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    (reports / "VECTOR_OVERLAY_UNIQUENESS_AUDIT.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-08-01T23:35:58Z",
                "campaign_must_stop": True,
                "quarantined_numeric_overlays": 429,
                "metric_collision_groups": 18,
                "fingerprint_collision_groups": 19,
            }
        )
    )
    (reports / "S1_REPORT_SYNC_RECEIPT_20260801.json").write_text(
        json.dumps(
            {
                "status": "PASS_TIMESTAMP_SIZE_MATCH",
                "observed_at": "2026-08-01T19:32:44Z",
            }
        )
    )
    monkeypatch.setattr(refresh_snapshot, "ROOT", tmp_path)

    canonical, prospective, sync = refresh_snapshot.blocker_panel_state()

    assert canonical == {
        "authority": "CANONICAL_PUBLISHED_RECEIPT",
        "status": "BLOCKED",
        "source": "data/reports/VECTOR_OVERLAY_UNIQUENESS_AUDIT.json",
        "generated_at": "2026-08-01T23:35:58Z",
        "quarantined_numeric_overlays": 429,
        "metric_collision_groups": 18,
        "fingerprint_collision_groups": 19,
    }
    assert prospective["status"] == "UNPUBLISHED"
    assert prospective["source"] is None
    assert "quarantined_numeric_overlays" not in prospective
    assert sync["status"] == "PASS_TIMESTAMP_SIZE_MATCH"
    assert sync["authority"] == "SYNC_ONLY_NOT_PRODUCTION"
    assert sync["transport_status"] == "UNAVAILABLE"

    path = progress.default_snapshot_path(tmp_path)
    progress.update_worker(path, 1, status="TESTING", now_epoch=100.0)
    refresh_snapshot.publish_blocker_panel(path)
    payload = progress.read_snapshot(path)
    assert payload["blocker_panel"]["states_merged"] is False
    assert payload["blocker_panel"]["canonical_uniqueness"] == canonical
    assert payload["blocker_panel"]["prospective_repaired_code_audit"] == (
        prospective
    )
    assert payload["blocker_panel"]["sync"] == sync
    # Receipt/sync publication is surface metadata, never a worker heartbeat.
    assert payload["production_updated_at_epoch"] == 100.0


def test_5077_page_labels_blocker_states_as_non_substitutable() -> None:
    body = chart_server.app.test_client().get("/matrix_live_progress").get_data(
        as_text=True
    )
    assert "uniqueness blocker · states never merged" in body
    assert "prospective repaired-code audit" in body
    assert "non-canonical; never substituted" in body
    assert "independent of production and uniqueness" in body


def test_vector_rates_and_remaining_keys_are_not_engine_cells(tmp_path) -> None:
    path = tmp_path / "progress.json"
    payload = progress.update_worker(
        path,
        2,
        worker_id="vector-discovery-2",
        run_class="VECTOR_DISCOVERY",
        parameter="ENTRY_WT_DC",
        symbol="NVDA",
        side="LONG",
        combination="ENTRY_WT_DC×EXIT_MTF_ATR_TRAIL×REENTER",
        stage="COMPLETE_RECIPE_VALIDATION",
        status="RUNNING",
        accepted_cells_per_minute=99,
        candidate_recipes_per_minute=4.5,
        accepted_recipes_per_minute=0.5,
        candidate_recipe_count=9,
        accepted_recipe_count=1,
        worker_keys_remaining=3,
        vector_keys_remaining=12,
        now_epoch=100.0,
    )

    worker = payload["workers"][1]
    assert payload["producer_state"] == "VECTOR_DISCOVERY_RUNNING"
    assert payload["accepted_cells_per_minute"] == 0
    assert worker["accepted_cells_per_minute"] == 0
    assert payload["candidate_recipes_per_minute"] == 4.5
    assert payload["accepted_recipes_per_minute"] == 0.5
    assert payload["cells_remaining"] is None
    assert payload["vector_keys_remaining"] == 12
    assert worker["cells_remaining"] is None
    assert worker["keys_remaining"] == 3
    assert payload["production_updated_at_epoch"] is None
    assert payload["vector_updated_at_epoch"] == 100.0

    dashboard = progress.dashboard_snapshot(
        path, stale_after_seconds=20, now_epoch=110.0
    )
    assert dashboard["stale"] is False


def test_exact_pause_preserves_active_vector_discovery_heartbeat(tmp_path) -> None:
    path = tmp_path / "progress.json"
    progress.update_worker(
        path,
        1,
        run_class="VECTOR_DISCOVERY",
        worker_id="vector-discovery-1",
        parameter="ENTRY_DELTA_MTF",
        symbol="MU",
        side="LONG",
        combination="BOUNDED_LIFECYCLE_BEAM",
        stage="ENTRY_SCREEN",
        status="RUNNING",
        candidate_recipes_per_minute=3,
        accepted_recipes_per_minute=0.25,
        worker_keys_remaining=2,
        vector_keys_remaining=7,
        now_epoch=100.0,
    )
    progress.update_worker(
        path,
        2,
        run_class="EXACT_ENGINE",
        status="TESTING",
        accepted_cells_per_minute=2,
        now_epoch=101.0,
    )

    paused = progress.pause_all_workers(path, now_epoch=110.0)
    vector = paused["workers"][0]
    exact = paused["workers"][1]
    assert paused["producer_state"] == "VECTOR_DISCOVERY_RUNNING_EXACT_PAUSED"
    assert vector["worker_id"] == "vector-discovery-1"
    assert vector["parameter"] == "ENTRY_DELTA_MTF"
    assert vector["status"] == "RUNNING"
    assert vector["keys_remaining"] == 2
    assert exact["status"] == "PAUSED_UNIQUENESS_REPAIR"
    assert exact["run_class"] == "EXACT_ENGINE"
    assert paused["accepted_cells_per_minute"] == 0
    assert paused["candidate_recipes_per_minute"] == 3
    assert paused["accepted_recipes_per_minute"] == 0.25
    assert paused["vector_keys_remaining"] == 7

    repeated = progress.pause_all_workers(path, now_epoch=900.0)
    assert repeated["production_updated_at_epoch"] == 110.0
    assert repeated["workers"][0]["last_update_epoch"] == 100.0


def test_5077_zeros_prior_campaign_rates_during_scalar_setup() -> None:
    payload = {
        "producer_request_id": "vas-20260802t090630z-f1ee7006",
        "candidate_recipes_per_minute": 28.1,
        "accepted_recipes_per_minute": 1.0,
        "workers": [
            {
                "stage": "CORE_LADDER_FLOOR_VALIDATION",
                "candidate_recipes_per_minute": 4.5,
                "accepted_recipes_per_minute": 0.5,
                "candidate_recipe_count": 24,
                "accepted_recipe_count": 1,
            },
            {
                "stage": "SCALAR_VECTOR_SCREEN",
                "candidate_recipes_per_minute": 3.0,
                "accepted_recipes_per_minute": 0.25,
                "candidate_recipe_count": 7,
                "accepted_recipe_count": 2,
            },
        ],
    }

    normalized = chart_server._normalize_scalar_request_rates(payload)

    assert normalized["workers"][0]["candidate_recipe_count"] == 0
    assert normalized["workers"][0]["accepted_recipe_count"] == 0
    assert normalized["candidate_recipes_per_minute"] == 3.0
    assert normalized["accepted_recipes_per_minute"] == 0.25
