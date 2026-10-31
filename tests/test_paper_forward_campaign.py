import json
import sqlite3
from datetime import datetime, timezone

import pytest

from tools import paper_forward_campaign as campaign


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return path


def _fixture(tmp_path, waiting_candidate=False):
    now = datetime.now(timezone.utc).timestamp()
    now_text = datetime.fromtimestamp(now, timezone.utc).isoformat().replace("+00:00", "Z")
    release = tmp_path / "release-old"
    host_dir = release / "s5"
    groups = [
        {"sym_side": "AAPL_LONG", "venue": "stocks", "host": "s5", "frame_timeframe": "5m", "scenario_ids": ["base", "candidate"], "epoch_id": "epoch-old-5m", "settings_sha256": "settings", "side": "LONG"},
        {"sym_side": "AAPL_LONG", "venue": "stocks", "host": "s5", "frame_timeframe": "1m", "scenario_ids": ["base-1m", "candidate-1m"], "epoch_id": "epoch-old-1m", "settings_sha256": "settings", "side": "LONG"},
    ]
    plan = {"schema_version": 1, "experiment_window": {"window_id": "old-window", "source_timeframe": "1m", "anchor": "first_available_after", "not_before_source_ts": now - 50000, "duration_seconds": 21600}, "groups": groups, "settings_snapshots": {"settings": {"SAMPLE": True}}, "catalog": {"scenarios": []}, "uncovered": []}
    _write(release / "plan.json", plan)
    jobs, status_rows = [], []
    for group in groups:
        tf = group["frame_timeframe"]
        window_id = "old-window-" + tf
        for scenario_id in group["scenario_ids"]:
            is_baseline = scenario_id.startswith("base")
            launchable = not (tf == "1m" and scenario_id == "candidate-1m")
            run_id = "run-" + scenario_id
            spec_file = run_id + ".json"
            spec = {"run_id": run_id, "epoch_id": group["epoch_id"], "scenario_id": scenario_id, "sym_side": group["sym_side"], "venue": group["venue"], "host": group["host"], "launchable": launchable, "is_baseline": is_baseline, "experiment_window": {"window_id": window_id, "source_timeframe": tf}}
            _write(host_dir / "specs" / spec_file, spec)
            jobs.append({"spec_file": spec_file, "run_id": run_id, "epoch_id": group["epoch_id"], "scenario_id": scenario_id, "sym_side": group["sym_side"], "venue": group["venue"], "host": group["host"]})
            status = "PENDING_PARAMETERS" if not launchable else ("WAITING_FOR_SOURCE_WINDOW" if tf == "1m" else "COMPLETED")
            run_dir = tmp_path / "data/paper_forward_lab/runs" / scenario_id
            if status == "COMPLETED" or (waiting_candidate and scenario_id == "candidate"):
                _write(run_dir / "worker.result_manifest.json", {"run_id": run_id, "epoch_id": group["epoch_id"], "scenario_id": scenario_id, "sym_side": group["sym_side"], "experiment_window_resolution": {"window_id": window_id, "start_source_ts": now - 30000, "end_source_ts": now - 8400}})
            if waiting_candidate and scenario_id == "candidate":
                status = "RUNNING"
            status_rows.append((run_id, group["epoch_id"], scenario_id, group["sym_side"], window_id, status, str(run_dir), int(is_baseline), "{}", group["host"]))
    _write(host_dir / "specs" / "manifest.json", {"schema_version": 1, "jobs": jobs})
    coordinator = tmp_path / "fleet_s5.snapshot.sqlite3"
    with sqlite3.connect(coordinator) as db:
        db.execute("CREATE TABLE coordinator_heartbeat(host TEXT PRIMARY KEY,at_utc TEXT,status TEXT,detail TEXT)")
        db.execute("INSERT INTO coordinator_heartbeat VALUES(?,?,?,?)", ("s5", now_text, "RECONCILING", "{}"))
        db.execute("CREATE TABLE jobs(run_id TEXT,epoch_id TEXT,scenario_id TEXT,sym_side TEXT,window_id TEXT,status TEXT,run_dir TEXT,is_baseline INTEGER,detail TEXT,assigned_host TEXT)")
        db.executemany("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)", status_rows)
    journal_status = tmp_path / "journal.status.json"
    _write(journal_status, {"host": "s5", "venue": "stocks", "poll_observed_at_utc": now_text, "blocked_timeframes": ["1m"], "eligible_timeframes": ["5m"], "status": "NO_FRESH_FRAMES"})
    return release, coordinator, journal_status, tmp_path, now


def test_next_epoch_advances_eligible_clock_and_carries_blocked_clock(tmp_path):
    release, coordinator, journal_status, root, now = _fixture(tmp_path)
    before = (release / "plan.json").read_bytes()
    result = campaign.inspect_previous_epoch(release, {"s5": coordinator}, {"s5": journal_status}, root, now=now)
    assert result["status"] == "READY_WITH_CARRYOVER"
    assert [group["frame_timeframe"] for group in result["plan"]["groups"]] == ["5m"]
    assert result["plan"]["experiment_window"]["not_before_source_ts"] == now - 8400 + 1
    assert result["carryovers"][0]["frame_timeframe"] == "1m"
    assert result["pending_parameters"][0]["status"] == "PENDING_PARAMETERS"
    assert (release / "plan.json").read_bytes() == before


def test_nonterminal_eligible_job_blocks_rollover(tmp_path):
    release, coordinator, journal_status, root, now = _fixture(tmp_path, waiting_candidate=True)
    result = campaign.inspect_previous_epoch(release, {"s5": coordinator}, {"s5": journal_status}, root, now=now)
    assert result["status"] == "WAITING_FOR_PRIOR_ELIGIBLE_JOBS"
    assert result["plan"] is None
    assert any(row.get("scenario_id") == "candidate" and row["reason"] == "JOB_NOT_TERMINAL" for row in result["incomplete"])


def test_export_refuses_when_disk_reserve_would_be_breached(tmp_path, monkeypatch):
    release, coordinator, journal_status, root, now = _fixture(tmp_path)
    result = campaign.inspect_previous_epoch(release, {"s5": coordinator}, {"s5": journal_status}, root, now=now)
    destination = tmp_path / "next"
    monkeypatch.setattr(campaign.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 10})())
    monkeypatch.setattr(campaign.launch_plan, "export", lambda *args: pytest.fail("export should not run after reserve failure"))
    with pytest.raises(OSError, match="insufficient disk reserve"):
        campaign.export_next_epoch(result, destination, "/remote", "/runtime", minimum_free_bytes=100)
    assert not destination.exists()
