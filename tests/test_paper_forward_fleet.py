import json
import sqlite3
from pathlib import Path

from tools.paper_fleet_budget import stable_host
from tools.paper_forward_fleet import DEFAULT_MAX_WORKERS, DEFAULT_POLL_SECONDS, _map_worker_status, coverage_summary, discover_specs, main, reconcile, resource_worker_limit, snapshot_state_database, source_window_available, SpecCache


def _symbol_for_host(host):
    for number in range(100):
        symbol = f"COIN{number}USDT"
        if stable_host("crypto", symbol, "LONG") == host:
            return symbol
    raise AssertionError("test could not find stable assignment")


def _spec(root, sym_side, scenario_id, venue="crypto"):
    config = root / "config"
    config.mkdir(exist_ok=True)
    (config / "full.json").write_text('{"A":1}', encoding="utf-8")
    (config / "candidate.json").write_text('{"A":2}', encoding="utf-8")
    engine_root = root / "runtime"
    engine_root.mkdir(exist_ok=True)
    (engine_root / "backtest_v12_engine.py").write_text("# isolated fixture\n", encoding="utf-8")
    (engine_root / "tools").mkdir(exist_ok=True)
    (engine_root / "tools" / "paper_forward_worker.py").write_text("# isolated worker fixture\n", encoding="utf-8")
    symbol = sym_side.rsplit("_", 1)[0]
    host = stable_host(venue, symbol, sym_side.rsplit("_", 1)[1])
    return {
        "run_id": f"run-{scenario_id}", "epoch_id": f"epoch-{scenario_id}", "scenario_id": scenario_id,
        "sym_side": sym_side, "venue": venue, "plan_code_hash": "codehash", "host": host,
        "full_settings": "config/full.json", "candidate_overrides": "config/candidate.json", "engine_root": str(engine_root),
        "execution_mode": "continuous",
    }


def _write_specs(folder, rows, listed=None):
    folder.mkdir(parents=True, exist_ok=True)
    for filename, row in rows.items():
        (folder / filename).write_text(json.dumps(row), encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps({"schema_version": 1, "spec_files": listed if listed is not None else list(rows)}), encoding="utf-8")


def test_discover_specs_registers_listed_unlisted_and_missing(tmp_path):
    specs = tmp_path / "specs"
    good = _spec(tmp_path, "BTCUSDT_LONG", "baseline")
    _write_specs(specs, {"baseline.json": good, "extra.json": good}, ["baseline.json", "missing.json"])

    rows = discover_specs(specs)
    by_name = {Path(row["spec_path"]).name: row["status"] for row in rows}

    assert by_name == {"baseline.json": "REGISTERED", "extra.json": "UNMANIFESTED_SPEC", "missing.json": "MISSING_SPEC"}


def test_manifest_jobs_keep_missing_expected_identity_visible(tmp_path):
    specs = tmp_path / "specs"
    job = _spec(tmp_path, "BTCUSDT_LONG", "not-arrived-yet")
    specs.mkdir()
    (specs / "manifest.json").write_text(json.dumps({"schema_version": 1, "jobs": [{"spec_file": "pending.json", **{key: job[key] for key in ("run_id", "epoch_id", "scenario_id", "sym_side", "venue", "host")}}]}))

    row = discover_specs(specs)[0]

    assert row["status"] == "MISSING_SPEC"
    assert row["spec"]["scenario_id"] == "not-arrived-yet"
    assert row["spec"]["sym_side"] == "BTCUSDT_LONG"


def test_large_job_manifest_uses_manifest_limit_not_spec_limit(tmp_path):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "manifest.json").write_text(json.dumps({"schema_version": 1, "jobs": [], "padding": "x" * (2 * 1024**2 + 10)}), encoding="utf-8")

    assert discover_specs(specs) == []


def test_resource_limit_fails_closed_and_caps_by_headroom():
    missing = {"mem_available_bytes": None, "disk_free_bytes": 20 * 1024**3, "load1": 0.0, "ncpu": 16, "cpu_psi_avg10": 0.0}
    assert resource_worker_limit(missing, "crypto", "s1", max_workers=3)[0] == 0
    observed = {"mem_available_bytes": 10 * 1024**3, "disk_free_bytes": 10 * 1024**3, "load1": 12.0, "ncpu": 16, "cpu_psi_avg10": 0.0}
    slots, decision = resource_worker_limit(observed, "crypto", "s1", max_workers=5)
    assert decision["admit"] is True
    assert slots == 2  # 2 GiB memory headroom, 2 GiB disk headroom, and 0.8 CPU cores


def test_worker_terminal_and_wait_states_are_mapped_explicitly():
    assert _map_worker_status("WAITING_FRESH_INPUT") == "WAITING_FRESH_INPUT"
    assert _map_worker_status("RESOURCE_LIMIT_STOPPING") == "RUNNING"
    assert _map_worker_status("STOPPED_RESOURCE_LIMIT") == "STOPPED_RESOURCE_LIMIT"
    assert _map_worker_status("COMPLETED") == "COMPLETED"
    assert _map_worker_status("DATA_GAP_MISSING_TF_1m") == "DATA_GAP"
    assert _map_worker_status("SETTINGS_MISMATCH_TEMPLATE_HASH") == "SETTINGS_MISMATCH"


def test_worker_data_gap_is_terminal_and_releases_coordinator_pid(tmp_path, monkeypatch):
    specs = tmp_path / "specs"
    symbol = _symbol_for_host("s1")
    job = _spec(tmp_path, f"{symbol}_LONG", "gap")
    job["is_baseline"] = True
    _write_specs(specs, {"job.json": job})
    observed = {"mem_available_bytes": 16 * 1024**3, "disk_free_bytes": 16 * 1024**3, "load1": 1.0, "ncpu": 16, "cpu_psi_avg10": 0.0}

    class Process:
        pid = 123456

    db = tmp_path / "fleet.sqlite3"
    reconcile(specs, db, tmp_path, "s1", once=True, observed=observed, launcher=lambda *_args, **_kwargs: Process())
    with sqlite3.connect(db) as connection:
        run_dir = connection.execute("SELECT run_dir FROM jobs").fetchone()[0]
    Path(run_dir, "worker.status.json").write_text(json.dumps({"status": "DATA_GAP", "reason": "DATA_GAP_MISSING_TF_1m"}), encoding="utf-8")
    monkeypatch.setattr("tools.paper_forward_fleet._pid_alive", lambda *_args: True)

    reconcile(specs, db, tmp_path, "s1", once=True, observed=observed, launcher=lambda *_args, **_kwargs: Process())

    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT status,pid FROM jobs").fetchone() == ("DATA_GAP", None)


def test_spec_cache_avoids_unchanged_reads_and_invalidates_on_manifest_metadata(tmp_path, monkeypatch):
    import os
    import tools.paper_forward_fleet as fleet

    specs = tmp_path / "specs"
    _write_specs(specs, {"job.json": _spec(tmp_path, "BTCUSDT_LONG", "cached")})
    original = fleet.discover_specs
    calls = []

    def counted(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(fleet, "discover_specs", counted)
    cache = SpecCache(specs, refresh_seconds=60)
    assert cache.get(now=100)
    assert cache.get(now=101)
    manifest = specs / "manifest.json"
    stat = manifest.stat()
    os.utime(manifest, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    assert cache.get(now=102)
    assert len(calls) == 2


def test_pending_parameters_are_registered_but_never_assigned_a_worker(tmp_path):
    specs = tmp_path / "specs"
    symbol = _symbol_for_host("s1")
    spec = {"run_id": "run-unresolved", "epoch_id": "epoch-unresolved", "scenario_id": "unresolved", "sym_side": f"{symbol}_LONG",
            "venue": "crypto", "host": "s1", "launchable": False, "pending_reason": "PENDING_PARAMETER_VALUES"}
    _write_specs(specs, {"job.json": spec})

    row = discover_specs(specs)[0]

    assert row["status"] == "PENDING_PARAMETERS"
    assert row["error"] == "PENDING_PARAMETER_VALUES"
    (tmp_path / "data").mkdir()
    db = tmp_path / "fleet.sqlite3"
    observed = {"mem_available_bytes": 2 * 1024**3, "disk_free_bytes": 16 * 1024**3, "load1": 1.0, "ncpu": 16, "cpu_psi_avg10": 0.0}
    launched = []
    reconcile(specs, db, tmp_path, "s1", observed=observed, launcher=lambda command, **kwargs: launched.append(command))
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT status FROM jobs").fetchone()[0] == "PENDING_PARAMETERS"
    assert launched == []


def test_dry_run_registers_every_manifest_item_without_writes_or_launch(tmp_path):
    specs = tmp_path / "specs"
    symbol = _symbol_for_host("s1")
    rows = {f"scenario-{number}.json": _spec(tmp_path, f"{symbol}_LONG", f"scenario-{number}") for number in range(3)}
    rows["scenario-0.json"]["is_baseline"] = True
    _write_specs(specs, rows)
    db = tmp_path / "state.sqlite3"
    root = tmp_path
    (root / "data").mkdir()
    observed = {"mem_available_bytes": 16 * 1024**3, "disk_free_bytes": 16 * 1024**3, "load1": 1.0, "ncpu": 16, "cpu_psi_avg10": 0.0}

    result = reconcile(specs, db, root, "s1", dry_run=True, max_workers=1, observed=observed)

    assert len(result["jobs"]) == 3
    statuses = {row["status"] for row in result["jobs"]}
    assert statuses == {"WAITING_FOR_CAPACITY", "WAITING_FOR_BASELINE"}
    assert not db.exists()


def test_once_persists_all_jobs_and_launches_only_bounded_worker(tmp_path):
    specs = tmp_path / "specs"
    symbol = _symbol_for_host("s1")
    rows = {f"scenario-{number}.json": _spec(tmp_path, f"{symbol}_LONG", f"scenario-{number}") for number in range(3)}
    rows["scenario-0.json"]["is_baseline"] = True
    _write_specs(specs, rows)
    root = tmp_path
    (root / "data").mkdir()
    observed = {"mem_available_bytes": 16 * 1024**3, "disk_free_bytes": 16 * 1024**3, "load1": 1.0, "ncpu": 16, "cpu_psi_avg10": 0.0}
    launched = []

    class Process:
        pid = 123456

    def launcher(command, **kwargs):
        launched.append(command)
        return Process()

    db = tmp_path / "fleet.sqlite3"
    result = reconcile(specs, db, root, "s1", once=True, max_workers=1, observed=observed, launcher=launcher)

    with sqlite3.connect(db) as connection:
        rows_in_db = connection.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()
        all_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        waiting = connection.execute("SELECT COUNT(*) FROM jobs WHERE status='WAITING_FOR_BASELINE'").fetchone()[0]
        heartbeat = connection.execute("SELECT status FROM coordinator_heartbeat WHERE host='s1'").fetchone()[0]
        event_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert len(launched) == 1
    assert "--follow" in launched[0]
    assert "--manifest" in launched[0]
    assert "--engine-root" in launched[0]
    assert launched[0][0] == __import__("sys").executable
    assert launched[0][1] == str((root / "runtime" / "tools" / "paper_forward_worker.py").resolve())
    assert "--workspace-root" in launched[0]
    workspace_index = launched[0].index("--workspace-root") + 1
    assert launched[0][workspace_index] == str(root.resolve())
    engine_index = launched[0].index("--engine-root") + 1
    assert launched[0][engine_index] == str((root / "runtime").resolve())
    assert all_count == 3
    assert waiting == 2
    assert heartbeat == "IDLE"
    assert event_count >= 4
    assert result["status_counts"]["STARTING"] == 1
    assert dict(rows_in_db)["WAITING_FOR_BASELINE"] == 2


def test_baseline_completes_before_matching_candidate_and_queue_alternates_classes(tmp_path, monkeypatch):
    specs = tmp_path / "specs"
    symbol = _symbol_for_host("s1")
    candidate = _spec(tmp_path, f"{symbol}_LONG", "a-candidate")
    baseline = _spec(tmp_path, f"{symbol}_LONG", "z-baseline")
    baseline["epoch_id"] = candidate["epoch_id"]
    baseline["is_baseline"] = True
    _write_specs(specs, {"a-candidate.json": candidate, "z-baseline.json": baseline})
    root = tmp_path
    (root / "data").mkdir()
    observed = {"mem_available_bytes": 16 * 1024**3, "disk_free_bytes": 16 * 1024**3, "load1": 1.0, "ncpu": 16, "cpu_psi_avg10": 0.0}
    launched = []

    class Process:
        pid = 123456

    reconcile(specs, tmp_path / "fleet.sqlite3", root, "s1", max_workers=1, observed=observed,
              launcher=lambda command, **kwargs: (launched.append(command) or Process()))

    with sqlite3.connect(tmp_path / "fleet.sqlite3") as connection:
        run_dir = connection.execute("SELECT run_dir FROM jobs WHERE is_baseline=1").fetchone()[0]
    Path(run_dir, "worker.status.json").write_text(json.dumps({"status": "COMPLETED", "heartbeat_at_utc": "2026-10-31T00:00:00Z"}), encoding="utf-8")
    monkeypatch.setattr("tools.paper_forward_fleet._pid_alive", lambda *_args: True)
    reconcile(specs, tmp_path / "fleet.sqlite3", root, "s1", max_workers=1, observed=observed,
              launcher=lambda command, **kwargs: (launched.append(command) or Process()))

    with sqlite3.connect(tmp_path / "fleet.sqlite3") as connection:
        selected = connection.execute("SELECT scenario_id,is_baseline FROM jobs WHERE status='STARTING'").fetchone()
    assert selected == ("a-candidate", 0)
    assert len(launched) == 2


def test_source_window_waits_until_exact_start_and_end_are_archived(tmp_path):
    spec = _spec(tmp_path, "BTCUSDT_LONG", "window")
    spec["execution_mode"] = "finite_epoch"
    spec["source_journal"] = "data/paper_forward_lab/native_journal_crypto.sqlite3"
    spec["frame_timeframe"] = "1m"
    spec["experiment_window"] = {"window_id": "epoch-1", "source_timeframe": "1m", "start_source_ts": 100.0, "end_source_ts": 160.0}
    database = tmp_path / spec["source_journal"]
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE journal_meta(key TEXT PRIMARY KEY,value TEXT)")
        connection.executemany("INSERT INTO journal_meta VALUES(?,?)", [("host", spec["host"]), ("venue", "crypto"), ("retention_floor_sequence", "0")])
        connection.execute("CREATE TABLE frames(sequence INTEGER,host TEXT,venue TEXT,symbol TEXT,timeframe TEXT,source_ts REAL)")
        connection.executemany("INSERT INTO frames VALUES(?,?,?,?,?,?)", [(1, spec["host"], "crypto", "BTCUSDT", "1m", 100.0), (2, spec["host"], "crypto", "BTCUSDT", "1m", 140.0)])

    ready, reason = source_window_available(spec, tmp_path, tmp_path)
    assert not ready
    assert reason == "WAITING_FOR_SOURCE_WINDOW_END"

    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO frames VALUES(?,?,?,?,?,?)", (3, spec["host"], "crypto", "BTCUSDT", "1m", 160.0))
    ready, reason = source_window_available(spec, tmp_path, tmp_path)
    assert ready
    assert reason == "SOURCE_WINDOW_READY"


def test_source_window_surfaces_native_provenance_gap_without_database(tmp_path):
    spec = _spec(tmp_path, "BTCUSDT_LONG", "provenance-gap")
    spec["source_journal"] = "data/paper_forward_lab/native_journal_crypto.sqlite3"
    spec["experiment_window"] = {"window_id": "epoch-1", "source_timeframe": "1m", "start_source_ts": 100.0, "end_source_ts": 160.0}
    journal = tmp_path / spec["source_journal"]
    journal.parent.mkdir(parents=True)
    Path(f"{journal}.status.json").write_text(json.dumps({"host": spec["host"], "venue": "crypto", "status": "UNVERIFIED_NATIVE_PROVENANCE", "provenance_gaps": ["1m"]}), encoding="utf-8")

    ready, reason = source_window_available(spec, tmp_path, tmp_path)

    assert not ready
    assert reason == "UNVERIFIED_NATIVE_PROVENANCE"


def test_finite_window_pair_waits_for_archive_end_then_runs_baseline_before_candidate(tmp_path):
    specs = tmp_path / "specs"
    symbol = _symbol_for_host("s1")
    baseline = _spec(tmp_path, f"{symbol}_LONG", "baseline-window")
    candidate = _spec(tmp_path, f"{symbol}_LONG", "candidate-window")
    candidate["epoch_id"] = baseline["epoch_id"]
    window = {"window_id": "window-001", "source_timeframe": "1m", "start_source_ts": 100.0, "end_source_ts": 160.0}
    journal = tmp_path / "data" / "paper_forward_lab" / "native_journal_crypto.sqlite3"
    for spec, is_baseline in ((baseline, True), (candidate, False)):
        spec.update(is_baseline=is_baseline, execution_mode="finite_epoch", source_journal=str(journal), frame_timeframe="1m", experiment_window=window)
    _write_specs(specs, {"baseline.json": baseline, "candidate.json": candidate})
    journal.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(journal) as connection:
        connection.execute("CREATE TABLE journal_meta(key TEXT PRIMARY KEY,value TEXT)")
        connection.executemany("INSERT INTO journal_meta VALUES(?,?)", [("host", "s1"), ("venue", "crypto"), ("retention_floor_sequence", "0")])
        connection.execute("CREATE TABLE frames(sequence INTEGER,host TEXT,venue TEXT,symbol TEXT,timeframe TEXT,source_ts REAL)")
        connection.execute("INSERT INTO frames VALUES(?,?,?,?,?,?)", (1, "s1", "crypto", symbol, "1m", 100.0))
    observed = {"mem_available_bytes": 16 * 1024**3, "disk_free_bytes": 16 * 1024**3, "load1": 1.0, "ncpu": 16, "cpu_psi_avg10": 0.0}
    launched = []

    class Process:
        pid = 123456

    db = tmp_path / "fleet.sqlite3"
    reconcile(specs, db, tmp_path, "s1", max_workers=1, observed=observed,
              launcher=lambda command, **kwargs: (launched.append(command) or Process()))
    with sqlite3.connect(db) as connection:
        assert dict(connection.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status")) == {"WAITING_FOR_SOURCE_WINDOW": 2}
    assert launched == []

    with sqlite3.connect(journal) as connection:
        connection.execute("INSERT INTO frames VALUES(?,?,?,?,?,?)", (2, "s1", "crypto", symbol, "1m", 160.0))
    reconcile(specs, db, tmp_path, "s1", max_workers=1, observed=observed,
              launcher=lambda command, **kwargs: (launched.append(command) or Process()))
    assert len(launched) == 1
    assert "--source-journal" in launched[0]
    assert "--snapshot" not in launched[0]
    with sqlite3.connect(db) as connection:
        run_dir = connection.execute("SELECT run_dir FROM jobs WHERE is_baseline=1").fetchone()[0]
    Path(run_dir, "worker.status.json").write_text(json.dumps({"status": "COMPLETED", "heartbeat_at_utc": "2026-10-31T00:00:00Z"}), encoding="utf-8")
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr("tools.paper_forward_fleet._pid_alive", lambda *_args: True)
    reconcile(specs, db, tmp_path, "s1", max_workers=1, observed=observed,
              launcher=lambda command, **kwargs: (launched.append(command) or Process()))
    monkeypatch.undo()
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT status FROM jobs WHERE is_baseline=0").fetchone()[0] == "STARTING"
    assert len(launched) == 2


def test_worker_death_is_recorded_and_not_automatically_restarted(tmp_path, monkeypatch):
    specs = tmp_path / "specs"
    symbol = _symbol_for_host("s1")
    baseline = _spec(tmp_path, f"{symbol}_LONG", "only")
    baseline["is_baseline"] = True
    _write_specs(specs, {"job.json": baseline})
    root = tmp_path
    (root / "data").mkdir()
    observed = {"mem_available_bytes": 16 * 1024**3, "disk_free_bytes": 16 * 1024**3, "load1": 1.0, "ncpu": 16, "cpu_psi_avg10": 0.0}
    launches = []

    class Process:
        pid = 123456

    db = tmp_path / "fleet.sqlite3"
    reconcile(specs, db, root, "s1", once=True, observed=observed, launcher=lambda command, **kwargs: (launches.append(command) or Process()))
    monkeypatch.setattr("tools.paper_forward_fleet._pid_alive", lambda *_args: False)
    reconcile(specs, db, root, "s1", once=True, observed=observed, launcher=lambda command, **kwargs: (launches.append(command) or Process()))

    with sqlite3.connect(db) as connection:
        status = connection.execute("SELECT status FROM jobs").fetchone()[0]
    assert status == "INTERRUPTED"
    assert len(launches) == 1


def test_waiting_for_fresh_input_keeps_worker_alive_without_restart(tmp_path, monkeypatch):
    specs = tmp_path / "specs"
    symbol = _symbol_for_host("s1")
    baseline = _spec(tmp_path, f"{symbol}_LONG", "waiter")
    baseline["is_baseline"] = True
    _write_specs(specs, {"job.json": baseline})
    root = tmp_path
    (root / "data").mkdir()
    observed = {"mem_available_bytes": 16 * 1024**3, "disk_free_bytes": 16 * 1024**3, "load1": 1.0, "ncpu": 16, "cpu_psi_avg10": 0.0}
    launches = []

    class Process:
        pid = 123456

    db = tmp_path / "fleet.sqlite3"
    reconcile(specs, db, root, "s1", once=True, observed=observed, launcher=lambda command, **kwargs: (launches.append(command) or Process()))
    with sqlite3.connect(db) as connection:
        run_dir = connection.execute("SELECT run_dir FROM jobs").fetchone()[0]
    Path(run_dir, "worker.status.json").write_text(json.dumps({"status": "WAITING_FRESH_INPUT", "observed_at_utc": "2026-10-31T00:00:00Z"}), encoding="utf-8")
    monkeypatch.setattr("tools.paper_forward_fleet._pid_alive", lambda *_args: True)
    result = reconcile(specs, db, root, "s1", once=True, observed=observed, launcher=lambda command, **kwargs: (launches.append(command) or Process()))

    with sqlite3.connect(db) as connection:
        status = connection.execute("SELECT status FROM jobs").fetchone()[0]
    assert status == "WAITING_FRESH_INPUT"
    assert result["status_counts"]["WAITING_FRESH_INPUT"] == 1
    assert len(launches) == 1


def test_changed_spec_requires_new_epoch_and_is_not_launched(tmp_path):
    specs = tmp_path / "specs"
    symbol = _symbol_for_host("s1")
    job = _spec(tmp_path, f"{symbol}_LONG", "stable-id")
    job["is_baseline"] = True
    _write_specs(specs, {"job.json": job})
    root = tmp_path
    (root / "data").mkdir()
    low = {"mem_available_bytes": 2 * 1024**3, "disk_free_bytes": 16 * 1024**3, "load1": 1.0, "ncpu": 16, "cpu_psi_avg10": 0.0}
    db = tmp_path / "fleet.sqlite3"
    reconcile(specs, db, root, "s1", once=True, observed=low)
    changed = dict(job, plan_code_hash="changed-without-new-epoch")
    _write_specs(specs, {"job.json": changed})
    launches = []
    reconcile(specs, db, root, "s1", once=True, observed=low, launcher=lambda command, **kwargs: launches.append(command))

    with sqlite3.connect(db) as connection:
        status = connection.execute("SELECT status FROM jobs").fetchone()[0]
    assert status == "SPEC_CHANGED"
    assert launches == []


def test_pending_spec_can_be_resolved_without_changing_planned_identity(tmp_path):
    specs = tmp_path / "specs"
    symbol = _symbol_for_host("s1")
    pending = dict(_spec(tmp_path, f"{symbol}_LONG", "resolve-me"), launchable=False, pending_reason="PENDING_PARAMETER_VALUES", is_baseline=True)
    _write_specs(specs, {"job.json": pending})
    root = tmp_path
    (root / "data").mkdir()
    low = {"mem_available_bytes": 2 * 1024**3, "disk_free_bytes": 16 * 1024**3, "load1": 1.0, "ncpu": 16, "cpu_psi_avg10": 0.0}
    db = tmp_path / "fleet.sqlite3"
    reconcile(specs, db, root, "s1", once=True, observed=low)
    ready = dict(pending, launchable=True, pending_reason=None)
    _write_specs(specs, {"job.json": ready})
    reconcile(specs, db, root, "s1", once=True, observed=low)

    with sqlite3.connect(db) as connection:
        status = connection.execute("SELECT status FROM jobs").fetchone()[0]
    assert status == "RESOURCE_BLOCKED"


def test_cli_once_and_dry_run_are_bounded(tmp_path, monkeypatch, capsys):
    specs = tmp_path / "specs"
    symbol = _symbol_for_host("s1")
    baseline = _spec(tmp_path, f"{symbol}_LONG", "cli")
    baseline["is_baseline"] = True
    _write_specs(specs, {"job.json": baseline})
    root = tmp_path
    (root / "data").mkdir()
    db = tmp_path / "fleet.sqlite3"
    args = ["--host", "s1", "--specs-dir", str(specs), "--root", str(root), "--state-db", str(db), "--once"]

    assert main(args + ["--dry-run"]) == 0
    dry_result = json.loads(capsys.readouterr().out)
    assert dry_result["mode"] == "DRY_RUN"
    assert not db.exists()

    monkeypatch.setattr("tools.paper_forward_fleet.collect_observed", lambda _path: {
        "mem_available_bytes": 2 * 1024**3, "disk_free_bytes": 16 * 1024**3, "load1": 1.0, "ncpu": 16, "cpu_psi_avg10": 0.0,
    })
    assert main(args) == 0
    once_result = json.loads(capsys.readouterr().out)
    assert once_result["mode"] == "ONCE"
    assert once_result["status_counts"]["RESOURCE_BLOCKED"] == 1


def test_database_snapshot_is_atomic_and_reports_per_host_coverage(tmp_path, capsys):
    specs = tmp_path / "specs"
    symbols = {host: _symbol_for_host(host) for host in ("s1", "s2")}
    rows = {f"{host}-{number}.json": _spec(tmp_path, f"{symbols[host]}_LONG", f"coverage-{host}-{number}") for host in ("s1", "s2") for number in range(2)}
    rows["s1-0.json"]["is_baseline"] = True
    rows["s2-0.json"]["is_baseline"] = True
    _write_specs(specs, rows)
    root = tmp_path
    (root / "data").mkdir()
    observed = {"mem_available_bytes": 2 * 1024**3, "disk_free_bytes": 16 * 1024**3, "load1": 1.0, "ncpu": 16, "cpu_psi_avg10": 0.0}
    db = tmp_path / "fleet.sqlite3"
    reconcile(specs, db, root, "s1", once=True, observed=observed)
    destination = tmp_path / "upload" / "fleet_snapshot.sqlite3"

    result = snapshot_state_database(db, destination)
    coverage = coverage_summary(destination)

    assert destination.is_file()
    assert len(result["snapshot_sha256"]) == 64
    assert result["coverage"]["by_host"]["s1"]["jobs"] == 2
    assert result["coverage"]["by_host"]["s2"]["jobs"] == 2
    assert coverage["total_jobs"] == 4
    assert coverage["by_host"]["s1"]["sym_sides"] == 1
    assert coverage["by_host"]["s2"]["sym_sides"] == 1
    assert coverage["by_host"]["s1"]["statuses"] == {"RESOURCE_BLOCKED": 1, "WAITING_FOR_BASELINE": 1}
    assert coverage["by_host"]["s2"]["statuses"] == {"ASSIGNED_OTHER_HOST": 2}
    cli_destination = tmp_path / "upload" / "fleet_cli_snapshot.sqlite3"
    assert main(["--state-db", str(db), "--snapshot-to", str(cli_destination)]) == 0
    cli_result = json.loads(capsys.readouterr().out)
    assert cli_destination.is_file()
    assert cli_result["coverage"]["total_jobs"] == 4


def test_coordinator_defaults_allow_configured_ceiling_with_safe_polling():
    assert DEFAULT_MAX_WORKERS == 64
    assert DEFAULT_POLL_SECONDS == 60
