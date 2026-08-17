from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import sync_manifest


ROOT = Path(__file__).resolve().parent


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _result_manifest(path: Path, entries: list[dict], generated: float) -> None:
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    path.write_text(
        json.dumps(
            {
                "schema": sync_manifest.SCHEMA_RESULTS,
                "generated_at_epoch": generated,
                "authority": "S1_RESULT_EVIDENCE_ONLY",
                "host": "s1",
                "root": "/s1",
                "entry_count": len(entries),
                "entries": entries,
                "content_fingerprint": hashlib.sha256(canonical).hexdigest(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _manifest_entry(path: str, payload: bytes, mtime_ns: int) -> dict:
    return {
        "path": path,
        "sha256": _digest(payload),
        "size": len(payload),
        "mtime_ns": mtime_ns,
    }


def test_source_manifest_is_directional_and_contains_sync_runtime(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    (root / "tools").mkdir(parents=True)
    (root / "chart_static").mkdir()
    (root / "data/reports").mkdir(parents=True)
    (root / "chart_server.py").write_text("app = 1\n")
    (root / "tools/sync_manifest.py").write_text("# sync\n")
    (root / "chart_static/matrix_live_progress.html").write_text("progress\n")
    (root / "BACKTEST_BIBLE.md").write_text("bible\n")
    (root / "data/reports/BACKTEST_BIBLE.md").write_text("bible\n")
    (root / "data/reports/result.json").write_text("result\n")
    output = tmp_path / "source.json"

    payload = sync_manifest.build_source(root, output)
    paths = {row["path"] for row in payload["entries"]}

    assert "chart_server.py" in paths
    assert "tools/sync_manifest.py" in paths
    assert "chart_static/matrix_live_progress.html" in paths
    assert "BACKTEST_BIBLE.md" in paths
    assert "data/reports/BACKTEST_BIBLE.md" in paths
    assert "data/reports/result.json" not in paths
    sync_manifest.verify(root, output, sync_manifest.SCHEMA_SOURCE)


def test_post_source_hook_keeps_current_coupled_r6_lane_alive() -> None:
    source = (ROOT / "tools/post_source_sync_s1.sh").read_text(encoding="utf-8")
    assert '"$SBX/tools/s1_coupled_path_qualification_watchdog.sh"' in source
    assert "s1_coupled_path_qualification_watchdog.cron.log" in source
    exact_watchdog = (
        ROOT / "tools/s1_hotlist_v8_full_recipe_watchdog.sh"
    ).read_text(encoding="utf-8")
    assert "coupled_qualification_r6_tim50_source_complete_20260803T1555Z" in exact_watchdog
    assert "coupled_qualification_r5_dc4h_parity_20260803T1420Z" not in exact_watchdog


def test_result_manifest_contains_conclusions_but_excludes_raw_data_and_db(
    tmp_path: Path,
) -> None:
    root = tmp_path / "s1"
    (root / "data/reports").mkdir(parents=True)
    (root / "data/reopt_loop").mkdir(parents=True)
    (root / "chart_server.py").write_text("never pull source\n")
    (root / "data/reports/result.json").write_text('{"ok": true}\n')
    (root / "data/reports/conclusion.md").write_text("winner\n")
    (root / "data/reports/S1_VEC_RESOURCE_HEARTBEAT.json").write_text(
        '{"runtime": true}\n'
    )
    (root / "data/reports/SWITCH_MATRIX_TRB.xlsx").write_bytes(b"xlsx")
    (root / "data/reports/raw.jsonl").write_text("{}\n")
    (root / "data/reports/run.npz").write_bytes(b"npz")
    beam = root / "data/reports/campaign/beam/generic"
    beam.mkdir(parents=True)
    (beam / "result.json").write_text('{"raw": true}\n')
    (root / "data/reports/BACKTEST_BIBLE.md").write_text("never pull bible\n")
    database = root / "data/param_results_stocks.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    database.write_bytes(b"database stays on s1")
    output = root / "data/reports/S1_RESULT_SYNC_MANIFEST.json"

    payload = sync_manifest.build_results(root, output, None)
    paths = {row["path"] for row in payload["entries"]}

    assert "data/reports/result.json" not in paths
    assert "data/reports/conclusion.md" in paths
    assert "data/reports/S1_VEC_RESOURCE_HEARTBEAT.json" not in paths
    assert "data/reports/SWITCH_MATRIX_TRB.xlsx" in paths
    assert "data/reports/raw.jsonl" not in paths
    assert "data/reports/run.npz" not in paths
    assert "data/reports/campaign/beam/generic/result.json" not in paths
    assert "data/reports/sync_snapshots/param_results_stocks.db" not in paths
    assert "data/reports/BACKTEST_BIBLE.md" not in paths
    assert "chart_server.py" not in paths
    assert not (root / "data/reports/sync_snapshots").exists()
    sync_manifest.verify(root, output, sync_manifest.SCHEMA_RESULTS)


def test_result_manifest_excludes_obsolete_broadcast_payloads(tmp_path: Path) -> None:
    root = tmp_path / "s1"
    old = root / "data/reports/full_trb_blank_matrix_vec_approx_20260801"
    old.mkdir(parents=True)
    (old / "all_cells.jsonl").write_text("broadcast\n")
    (old / "vector_overlay_index.jsonl").write_text("broadcast\n")
    (old / "campaign_receipt.json").write_text("{}\n")
    (old / "raw_matrix_overlay_0001.xlsx").write_bytes(b"raw xlsx")

    paths = {path.as_posix() for path in sync_manifest.result_paths(root)}

    assert not any(path.endswith("all_cells.jsonl") for path in paths)
    assert not any(path.endswith("vector_overlay_index.jsonl") for path in paths)
    assert not any(path.endswith("raw_matrix_overlay_0001.xlsx") for path in paths)
    assert any(path.endswith("campaign_receipt.json") for path in paths)


def test_result_snapshot_is_immutable_compact_and_cleanup_is_fail_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "s1"
    reports = root / "data/reports"
    reports.mkdir(parents=True)
    conclusion = reports / "CONCLUSION.md"
    conclusion.write_text("initial winner\n", encoding="utf-8")
    workbook = reports / "SWITCH_MATRIX_TRB.xlsx"
    workbook.write_bytes(b"compact workbook")
    # These must never enter either the snapshot or its final manifest.
    (reports / "raw.jsonl").write_text('{"raw": true}\n', encoding="utf-8")
    (reports / "market.npz").write_bytes(b"raw npz")
    database = root / "data/param_results_stocks.db"
    database.parent.mkdir(exist_ok=True)
    database.write_bytes(b"database")
    (root / "data/param_results_stocks.db-wal").write_bytes(b"wal")

    snapshot = sync_manifest.snapshot_results(root)
    snapshot_root = Path(snapshot["snapshot_root"])
    manifest = Path(snapshot["manifest"])
    try:
        assert snapshot_root.parent == Path("/tmp")
        assert snapshot_root.name.startswith(sync_manifest.RESULT_SNAPSHOT_PREFIX)
        entries = json.loads(manifest.read_text(encoding="utf-8"))["entries"]
        paths = {row["path"] for row in entries}
        assert paths == {
            "data/reports/CONCLUSION.md",
            "data/reports/SWITCH_MATRIX_TRB.xlsx",
        }
        assert not (snapshot_root / "data/reports/raw.jsonl").exists()
        assert not (snapshot_root / "data/reports/market.npz").exists()
        assert not (snapshot_root / "data/param_results_stocks.db").exists()
        assert not (snapshot_root / "data/param_results_stocks.db-wal").exists()

        # A live rewrite after snapshot creation cannot affect the manifest,
        # transfer source, or verification view used by the synchronizer.
        conclusion.write_text("later live rewrite\n", encoding="utf-8")
        assert (
            snapshot_root / "data/reports/CONCLUSION.md"
        ).read_text(encoding="utf-8") == "initial winner\n"
        sync_manifest.verify(snapshot_root, manifest, sync_manifest.SCHEMA_RESULTS)
    finally:
        sync_manifest.cleanup_result_snapshot(snapshot_root)
    assert not snapshot_root.exists()

    with pytest.raises(ValueError, match="CLEANUP_REFUSED"):
        sync_manifest.cleanup_result_snapshot(tmp_path / "not-a-s1-snapshot")


def test_result_paths_skip_hash_identical_protected_evidence(tmp_path: Path) -> None:
    root = tmp_path / "mac"
    evidence = root / "data/reports/PRESERVED_V8_CELLS.jsonl"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("immutable-v8\n")
    changed = root / "data/reports/new_result.json"
    changed.write_text("old\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "path": "data/reports/PRESERVED_V8_CELLS.jsonl",
                        "size": evidence.stat().st_size,
                        "sha256": sync_manifest.sha256(evidence),
                    },
                    {
                        "path": "data/reports/new_result.json",
                        "size": 4,
                        "sha256": "0" * 64,
                    },
                ]
            }
        )
    )
    paths = tmp_path / "paths.txt"

    sync_manifest.write_paths(manifest, paths, root)

    assert paths.read_text().splitlines() == ["data/reports/new_result.json"]


def test_incremental_result_plan_fast_forwards_only_current_safe_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "mac"
    reports = root / "data/reports"
    reports.mkdir(parents=True)
    old = b"old current\n"
    new = b"new current result with a different size\n"
    protected = b"preserved exact v8\n"
    divergent = b"mac-only evidence\n"
    matching = b"already synchronized\n"
    (reports / "current.json").write_bytes(old)
    (reports / "PRESERVED_V8_CELLS.jsonl").write_bytes(protected)
    (reports / "divergent.json").write_bytes(divergent)
    (reports / "matching.json").write_bytes(matching)

    prior_entries = [
        _manifest_entry("data/reports/current.json", old, 50_000_000_000),
        _manifest_entry(
            "data/reports/PRESERVED_V8_CELLS.jsonl",
            protected,
            50_000_000_000,
        ),
        _manifest_entry("data/reports/divergent.json", b"old remote\n", 50_000_000_000),
        _manifest_entry(
            "data/reports/historical-only.json", b"historical\n", 50_000_000_000
        ),
    ]
    remote_entries = [
        _manifest_entry("data/reports/current.json", new, 150_000_000_000),
        _manifest_entry(
            "data/reports/PRESERVED_V8_CELLS.jsonl",
            b"remote replacement forbidden\n",
            150_000_000_000,
        ),
        _manifest_entry(
            "data/reports/divergent.json", b"new remote\n", 150_000_000_000
        ),
        _manifest_entry("data/reports/matching.json", matching, 150_000_000_000),
        _manifest_entry(
            "data/reports/historical-only.json", b"historical\n", 50_000_000_000
        ),
        _manifest_entry(
            "data/reports/new-current.json", b"new result\n", 150_000_000_000
        ),
    ]
    prior = tmp_path / "prior.json"
    remote = tmp_path / "remote.json"
    plan_path = tmp_path / "plan.json"
    transfer = tmp_path / "transfer.json"
    conflicts = tmp_path / "conflicts.json"
    _result_manifest(prior, prior_entries, generated=100.0)
    _result_manifest(remote, remote_entries, generated=200.0)
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    plan = sync_manifest.build_result_pull_plan(
        local_root=root,
        remote_manifest=remote,
        prior_manifest=prior,
        output=plan_path,
        transfer_manifest=transfer,
        conflict_manifest=conflicts,
    )

    assert plan["transfer_paths"] == [
        "data/reports/current.json",
        "data/reports/new-current.json",
    ]
    assert plan["matching_paths"] == ["data/reports/matching.json"]
    assert {row["path"] for row in plan["conflicts"]} == {
        "data/reports/PRESERVED_V8_CELLS.jsonl",
        "data/reports/divergent.json",
    }
    assert (
        next(
            row
            for row in plan["conflicts"]
            if row["path"] == "data/reports/PRESERVED_V8_CELLS.jsonl"
        )["reason"]
        == "PROTECTED_LOCAL_DIVERGENCE"
    )
    assert [row["path"] for row in plan["historical_missing"]] == [
        "data/reports/historical-only.json"
    ]
    assert json.loads(transfer.read_text())["entry_count"] == 2
    assert json.loads(conflicts.read_text())["entry_count"] == 2
    after = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_result_plan_treats_filesystem_immutable_fast_forward_as_conflict(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "mac"
    target = root / "data/reports/current.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old\n")
    prior = tmp_path / "prior.json"
    remote = tmp_path / "remote.json"
    _result_manifest(
        prior,
        [_manifest_entry("data/reports/current.json", b"old\n", 10)],
        generated=1.0,
    )
    _result_manifest(
        remote,
        [_manifest_entry("data/reports/current.json", b"new\n", 2_000_000_000)],
        generated=2.0,
    )
    monkeypatch.setattr(sync_manifest, "_local_immutable", lambda path: True)

    plan = sync_manifest.build_result_pull_plan(
        local_root=root,
        remote_manifest=remote,
        prior_manifest=prior,
        output=tmp_path / "plan.json",
        transfer_manifest=tmp_path / "transfer.json",
        conflict_manifest=tmp_path / "conflicts.json",
    )

    assert plan["transfer_count"] == 0
    assert plan["conflict_count"] == 1
    assert plan["conflicts"][0]["reason"] == "PROTECTED_LOCAL_DIVERGENCE"
    assert target.read_bytes() == b"old\n"


def test_first_result_plan_uses_explicit_current_watermark(tmp_path: Path) -> None:
    root = tmp_path / "mac"
    root.mkdir()
    remote = tmp_path / "remote.json"
    _result_manifest(
        remote,
        [
            _manifest_entry("data/reports/old.json", b"old\n", 9_000_000_000),
            _manifest_entry("data/reports/new.json", b"new\n", 11_000_000_000),
        ],
        generated=12.0,
    )

    plan = sync_manifest.build_result_pull_plan(
        local_root=root,
        remote_manifest=remote,
        current_since_epoch=10.0,
        output=tmp_path / "plan.json",
        transfer_manifest=tmp_path / "transfer.json",
        conflict_manifest=tmp_path / "conflicts.json",
    )

    assert plan["transfer_paths"] == ["data/reports/new.json"]
    assert [row["path"] for row in plan["historical_missing"]] == [
        "data/reports/old.json"
    ]


def test_first_result_plan_skips_old_existing_divergence_without_hashing(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "mac"
    target = root / "data/reports/old.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"independent mac evidence\n")
    remote = tmp_path / "remote.json"
    _result_manifest(
        remote,
        [_manifest_entry("data/reports/old.json", b"old s1 evidence\n", 9_000_000_000)],
        generated=12.0,
    )
    original_sha256 = sync_manifest.sha256
    monkeypatch.setattr(
        sync_manifest,
        "sha256",
        lambda path: (
            original_sha256(path)
            if Path(path) == remote
            else (_ for _ in ()).throw(AssertionError(f"unexpected hash: {path}"))
        ),
    )

    plan = sync_manifest.build_result_pull_plan(
        local_root=root,
        remote_manifest=remote,
        current_since_epoch=10.0,
        output=tmp_path / "plan.json",
        transfer_manifest=tmp_path / "transfer.json",
        conflict_manifest=tmp_path / "conflicts.json",
    )

    assert plan["transfer_count"] == 0
    assert plan["conflict_count"] == 0
    assert plan["historical_existing_count"] == 1
    assert target.read_bytes() == b"independent mac evidence\n"


def test_result_plan_rejects_unsafe_remote_paths(tmp_path: Path) -> None:
    root = tmp_path / "mac"
    root.mkdir()
    remote = tmp_path / "remote.json"
    _result_manifest(remote, [_manifest_entry("../escape", b"bad", 1)], generated=1)

    with pytest.raises(ValueError, match="UNSAFE_PATH"):
        sync_manifest.build_result_pull_plan(
            local_root=root,
            remote_manifest=remote,
            current_since_epoch=0,
            output=tmp_path / "plan.json",
            transfer_manifest=tmp_path / "transfer.json",
            conflict_manifest=tmp_path / "conflicts.json",
        )


def test_result_plan_rejects_manifest_fingerprint_drift(tmp_path: Path) -> None:
    root = tmp_path / "mac"
    root.mkdir()
    remote = tmp_path / "remote.json"
    _result_manifest(
        remote,
        [_manifest_entry("data/reports/current.json", b"good", 1)],
        generated=1,
    )
    payload = json.loads(remote.read_text())
    payload["entries"][0]["sha256"] = "0" * 64
    remote.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="CONTENT_FINGERPRINT_MISMATCH"):
        sync_manifest.build_result_pull_plan(
            local_root=root,
            remote_manifest=remote,
            current_since_epoch=0,
            output=tmp_path / "plan.json",
            transfer_manifest=tmp_path / "transfer.json",
            conflict_manifest=tmp_path / "conflicts.json",
        )


def test_shell_contract_uses_authenticated_dual_s1_route_and_never_push_py() -> None:
    text = (ROOT / "tools/always_connected_sync.sh").read_text()
    assert "S1=niels@localhost" in text
    assert "-p 2201 niels@localhost true" in text
    assert "S1=niels@127.0.0.1" not in text
    assert "TRANSPORT_ROUTE=LOCALHOST_TUNNEL" in text
    assert "select_transport" in text
    assert "configure_localhost_transport" in text
    assert "FIXED_PUBLIC_S1" not in text
    assert "157.180.125.52" in text
    assert "configure_public_transport" in text
    assert "PUBLIC_S1_PROBE_FAILED" not in text
    assert "ALLOW_LOCALHOST_VERIFIED_SYNC" in text
    assert "ssh -4" in text or "SSH=(/usr/bin/ssh -4" in text
    assert "-p 2201" in text
    assert "ControlMaster=no" in text
    assert "ControlPath=none" in text
    assert "repair_localhost_tunnel" in text
    assert "LOCALHOST_TUNNEL_RECYCLE" in text
    assert "LOCALHOST_TUNNEL_RECYCLE_TIMEOUT" in text
    assert "repair_timeout_seconds=30" in text
    assert 'kill "$repair_pid"' in text
    assert "LOCALHOST_PROBE_TIMEOUT" in text
    assert "probe_timeout_seconds=20" in text
    assert 'kill "$probe_pid"' in text
    assert "sample_s1_free_kb" in text
    assert "S1_SPACE_SAMPLE_INVALID" in text
    assert "S1_FRONTIER_RUNTIME_REPAIR_REQUEST" in text
    assert "deploy_priority_backtest_repairs" in text
    assert "PRIORITY_BACKTEST_REPAIR_PASS" in text
    assert "tools/append_vector_capacity_frontier.py" in text
    assert "VALIDATED_VECTOR_QUEUE_ROWS" in text
    assert "from tools.vector_capacity_admission import validate_manifest" in text
    assert "tools/run_hotlist_v8_full_recipe.py" in text
    assert "backtest_v8_engine.py" in text
    assert "/usr/bin/ssh -O exit s1-sftp" in text
    assert "/usr/bin/ssh -O forward -L 2201:127.0.0.1:22 s1-int" in text
    assert "LOCALHOST_TUNNEL_REPAIRED via=s1-int-control-master" in text
    select_body = text[text.index("select_transport() {") : text.index("mkdir -p \"$STATE\"")]
    assert select_body.index("if probe_localhost") < select_body.index(
        "configure_public_transport"
    )
    assert "SYNC_PENDING both localhost:2201 and 157.180.125.52:22 unavailable" in select_body
    assert "--no-perms --update --relative" in text
    assert 'touch "$SOURCE_SNAPSHOT/$relative"' in text
    assert "push.py" not in text
    assert "--delay-updates" in text
    assert 'sync_manifest.py" plan-results' in text
    assert '--current-since-epoch "$RESULT_CURRENT_SINCE"' in text
    assert 'sync_manifest.py" verify' in text
    assert 'cmp -s "$BASE/BACKTEST_BIBLE.md"' in text
    assert ".s1_transport.lock" in text
    lock_busy = text.index('echo "$(date -u +%FT%TZ) SKIP lock busy"')
    assert "exit 3" in text[lock_busy : lock_busy + 400]
    assert "set_phase RESULT_UNLOCK_LOCAL" in text
    assert 'done <"$result_paths"' in text
    assert ".S1_RESULT_SYNC_MANIFEST.$$.tmp" in text
    assert ".S1_RESULT_PATHS.$$.txt" in text
    assert 'local temp="$SYNC_STATUS.$$.tmp"' in text
    assert "RESULT_CONFLICT_QUARANTINE" in text
    assert "SOURCE_PASS_RESULTS_PENDING" in text
    assert "snapshot-results --root '$S1_ROOT' --snapshot-parent /tmp" in text
    assert "RESULT_SNAPSHOT_REMOTE" in text
    assert '"$S1:$RESULT_SNAPSHOT_REMOTE/" "$BASE/"' in text
    assert '"$S1:$RESULT_SNAPSHOT_REMOTE/" "$quarantine_root/"' in text
    assert "cleanup-result-snapshot --snapshot '$snapshot'" in text
    assert 'chflags nouchg "$protected"' not in text


def test_mac_heartbeat_accepts_the_literal_bible_localhost_target() -> None:
    text = (ROOT / "mac_live_heartbeat.py").read_text()
    assert '"niels@localhost",' in text
    assert '"niels@127.0.0.1",' not in text


def test_heartbeat_accepts_operator_authorized_authenticated_dual_route() -> None:
    text = (ROOT / "mac_live_heartbeat.py").read_text()
    assert "REFUSED_UNSAFE_S1_TRANSPORT" in text
    assert 'forbidden = (' in text
    assert "not any(" in text
    assert '"niels@157.180.125.52"' in text
    assert '"configure_public_transport"' in text
    assert '"ALLOW_LOCALHOST_VERIFIED_SYNC": "1"' in text
    assert '"ALLOW_VERIFIED_S1_SYNC": "1"' not in text
    assert "VERIFIED_DUAL_ROUTE_S1_TRANSPORT" in text


def test_matrix_pause_does_not_pause_transport_sync() -> None:
    chart_sync = (ROOT / "sync_chart_data.sh").read_text()
    exact_watchdog = (ROOT / "tools/param_matrix_watchdog.sh").read_text()
    assert "always_connected_sync.sh" in chart_sync
    assert "pull_current_matrix_surface_s1.sh" in chart_sync
    assert "MATRIX_WORKERS_PAUSED" not in chart_sync
    assert "MATRIX_WORKERS_PAUSED" in exact_watchdog
    assert 'payload.get("status") == "PASS"' in chart_sync
    assert "completed >= started" in chart_sync
    assert 'rm -f "$BASE/.s1_s2_autosync.pause"' not in chart_sync


def test_optional_matrix_snapshot_cannot_hold_sync_transport_lock_forever() -> None:
    text = (ROOT / "tools/always_connected_sync.sh").read_text()
    assert text.count(
        '/opt/homebrew/bin/timeout 20 "$PY" "$BASE/tools/refresh_matrix_progress_snapshot.py"'
    ) == 2


def test_current_matrix_surface_lane_never_replaces_protected_mac_evidence() -> None:
    source = (ROOT / "tools/pull_current_matrix_surface_s1.sh").read_text()
    assert "SEMANTIC_COMPARE_THEN_QUARANTINE" in source
    assert "SEMANTIC_MATCH_METADATA_ONLY" in source
    assert "current_matrix_surface_conflicts" in source
    assert 'canonical_modified\":false' in source
    assert 'chflags nouchg "$destination"' not in source
    assert 'mv "$STAGE/$relative" "$destination"' not in source


def test_progress_wording_separates_sync_from_compute() -> None:
    page = (ROOT / "chart_static/matrix_live_progress.html").read_text()
    monitor = (ROOT / "tools/refresh_matrix_progress_snapshot.py").read_text()
    assert "does not mean matrix workers are running" in page
    assert "pause_all_workers" in monitor
    assert "PAUSED_STATUS" in monitor


def test_s1_chart_post_sync_is_retryable_and_semantically_verified() -> None:
    script = (ROOT / "tools/post_source_sync_s1.sh").read_text()

    # A source hash is a verified deployment marker, not an attempted-start
    # marker. This ordering prevents one failed launch from poisoning retries.
    assert script.index("phase=FINAL_ROUTE_VERIFICATION") < script.index(
        'mv "$VERIFIED_SHA.tmp" "$VERIFIED_SHA"'
    )
    assert script.index("phase=FINAL_EXECUTION_MODE_VERIFICATION") < script.index(
        'mv "$VERIFIED_SHA.tmp" "$VERIFIED_SHA"'
    )
    assert "PERSISTED_AWAITING" not in script
    assert "param_matrix_daemon" not in script
    assert "MATRIX_WORKERS_PAUSED" in script
    assert 'mv -- "$PAUSE_FILE"' in script
    assert 'rm -f "$SBX/data/MATRIX_WORKERS_PAUSED"' not in script
    assert "MATRIX_WORKERS_PAUSED.cleared_vector_mode_" in script
    assert "retire_obsolete_pause" in script
    assert script.count("retire_obsolete_pause") >= 5
    assert "phase=EXECUTION_MODE_BEFORE_RESTART" in script
    assert "phase=FINAL_EXECUTION_MODE_VERIFICATION" in script

    # HTTP 200 alone is insufficient: the API must parse as six slots and the
    # page must contain the expected route marker.
    assert 'len(payload.get("workers") or []) != 6' in script
    assert '"SWITCH_MATRIX_TRB live progress" not in' in script
    assert "write_status FAILED" in script
    assert "write_status VERIFIED ROUTES_OK" in script
    assert '"status":"PASS"' not in script
    assert "combo worker still owns six-worker lane" in script
    assert "combo_holdout/.combo_campaign.lock" in script


def test_s1_chart_post_sync_preflights_before_killing_old_service() -> None:
    script = (ROOT / "tools/post_source_sync_s1.sh").read_text()
    preflight = script.index('client.get("/api/matrix_live_progress")')
    kill = script.index('kill -TERM "$pid"')
    assert preflight < kill
