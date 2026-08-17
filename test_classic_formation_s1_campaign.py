import json
import pytest
from pathlib import Path

from tools import run_classic_formation_s1_campaign as campaign
from tools.sync_manifest import source_paths


ROOT = Path(__file__).resolve().parent


def test_s1_absolute_tools_launch_can_import_root_precompute_module(tmp_path):
    import subprocess
    import sys

    script = ROOT / "tools/run_classic_formation_s1_campaign.py"
    code = (
        "import runpy; "
        f"ns=runpy.run_path({str(script)!r}, run_name='campaign_import_probe'); "
        "ns['_derive_missing_formation_timeframes']"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_request_and_full_universe_contract_fails_closed_on_live_universe_drift():
    request = json.loads((ROOT / campaign.REQUEST_REL).read_text())
    assert request["schema"] == campaign.SCHEMA
    assert request["action"] == campaign.ACTION
    assert request["workers"] == 6
    assert request["key_count"] == 115
    assert request["underlying_symbols"] == 108
    assert request["train_start"] == campaign.TRAIN_START
    assert request["train_end_exclusive"] == campaign.TRAIN_END
    assert request["holdout_start"] == campaign.HOLDOUT_START
    longs = json.loads((ROOT / "symbols_trb_long.json").read_text())
    shorts = json.loads((ROOT / "symbols_trb_short.json").read_text())
    keys = [*(f"{symbol}_LONG" for symbol in longs), *(f"{symbol}_SHORT" for symbol in shorts)]
    expected = f"UNIVERSE_CONTRACT_FAILED:keys={len(set(keys))}:symbols={len(set(longs) | set(shorts))}"
    with pytest.raises(RuntimeError, match=expected):
        campaign.universe(ROOT)


def test_request_is_part_of_verified_mac_source_transaction():
    assert campaign.REQUEST_REL in source_paths(ROOT)
    source = (ROOT / "tools/sync_manifest.py").read_text()
    assert '"data/sync/CLASSIC_FORMATION_S1_STATUS.json"' in source
    prefixes = __import__("tools.sync_manifest", fromlist=["BOOTSTRAP_FAST_FORWARD_PREFIXES"]).BOOTSTRAP_FAST_FORWARD_PREFIXES
    assert "data/sync/CLASSIC_FORMATION_S1_STATUS.json" in prefixes


def test_campaign_windows_and_safety_contract():
    assert campaign.TRAIN_START == "2025-01-01"
    assert campaign.TRAIN_END == campaign.HOLDOUT_START == "2025-12-01"
    assert campaign.HISTORY_WARMUP_START == "2024-08-01"
    assert campaign.WORKERS == 6
    assert campaign.PRECOMPUTE_WORKERS == 1
    assert "DISPATCHED" in campaign.ACTIVE_STATUSES
    assert "BUILDING_INCOMPLETE_NPZ" in campaign.ACTIVE_STATUSES
    assert "BACKFILLING_NATIVE_5M" in campaign.ACTIVE_STATUSES
    assert "PRECOMPUTING_REPAIRED_NPZ" in campaign.ACTIVE_STATUSES
    assert "ALREADY_RUNNING" not in campaign.ACTIVE_STATUSES
    assert "COMPLETE_HOLDOUT_RANKED" in campaign.ACTIVE_STATUSES
    source = (ROOT / "tools/run_classic_formation_s1_campaign.py").read_text()
    assert '"matrix_written": False' in source
    assert '"database_written": False' in source
    assert '"live_config_written": False' in source
    assert "completed_key_count\") != 115" in source
    assert "EXISTING_NPZ_PLUS_INCOMPLETE_RAW_KLINE_REBUILD" in source
    assert '"canonical_npz_modified": False' in source
    assert "destination.symlink_to(source)" in source
    assert "destination.is_symlink()" in source
    assert "destination.unlink()" in source
    assert "symbol in CORPORATE_ACTION_SPLITS and destination.exists()" in source
    assert '"--symbols", ",".join(to_build)' in source
    assert '"download_stock_klines_5m.py"' in source
    assert '"--repair-range"' in source
    assert '"--from-date", HISTORY_WARMUP_START' in source
    assert "timeout=48 * 3600" in source
    assert '"insufficient_train_coverage"' in source
    assert 'not canonical_scores[symbol]["covers_train_window"]' in source
    assert "or symbol in CORPORATE_ACTION_SPLITS" in source
    assert '"--split-adjustments-json"' in source
    assert '"--profile", "formations"' in source
    assert '"tools/rank_classic_formation_holdout.py"' in source
    assert '"--expected-count", "115"' in source
    assert '"COMPLETE_HOLDOUT_RANKED"' in source
    assert "BACKTEST_DATA_CONTRACT_STATUS.json" in source
    assert '"FAILED_DATA_CONTRACT"' in source
    assert 'audits[symbol].get("errors", [])' in source
    assert 'publish(root, request, "ALREADY_RUNNING"' not in source
    assert 'campaign / f"{label}_failure.json"' in source
    assert 'campaign / "last_failure.json"' in source
    assert 'campaign / "native_5m_backfill_receipt.json"' in source
    assert '"log_tail": text_tail' in source
    assert 'owner_proc = Path(f"/proc/{owner_pid}")' in source
    assert "if owner_alive:" in source
    assert "write_dead_owner_receipt(root, request, status, owner_pid)" in source
    assert 'reason=f"STALE_ACTIVE_PID_NOT_ALIVE:{owner_pid}"' in source
    assert 'status.get("worker_pid")' in source
    assert 'pid=process.pid' in source
    assert '"FAILED_DATA_CONTRACT",' in source
    assert "TERMINAL_SAME_REQUEST_STATUSES" in source


def test_campaign_rebuild_field_contract_includes_all_formation_inputs(tmp_path):
    import numpy as np

    path = tmp_path / "MU.npz"
    np.savez_compressed(path, close_15m=np.ones(3))
    missing = campaign.missing_formation_fields(path)
    assert "open_15m" in missing
    assert "close_15m" not in missing
    assert "timestamp_D" in missing


def test_crwd_four_for_one_split_is_forced_into_versioned_rebuild():
    action = campaign.CORPORATE_ACTION_SPLITS["CRWD"][0]
    assert action["effective_epoch"] == 1782925500
    assert action["split_ratio"] == 4.0
    assert "FIRST_CHANGED_BAR" in action["source"]


def test_dispatch_supersedes_verified_older_request_before_new_spawn():
    source = Path("tools/run_classic_formation_s1_campaign.py").read_text()
    assert "_supersede_stale_request_worker" in source
    assert "STALE_WORKER_IDENTITY_MISMATCH" in source
    assert "CORRECTED_SOURCE_OR_DATA_CONTRACT_REQUIRES_FRESH_115_CELL_RUN" in source
    assert "os.killpg(pid, signal.SIGTERM)" in source


def test_failure_history_survives_automatic_relaunch_status(tmp_path):
    request = {"request_id": "classic-formations-test-history"}
    campaign.publish(tmp_path, request, "FAILED", reason="RuntimeError:broken-source")
    campaign.publish(tmp_path, request, "BUILDING_INCOMPLETE_NPZ")

    payload = json.loads((tmp_path / campaign.STATUS_REL).read_text())
    assert payload["status"] == "BUILDING_INCOMPLETE_NPZ"
    assert payload["failure_history"][-1]["reason"] == "RuntimeError:broken-source"


def test_legacy_failed_status_is_captured_when_relaunched(tmp_path):
    request = {"request_id": "classic-formations-test-legacy-failure"}
    status_path = tmp_path / campaign.STATUS_REL
    campaign.atomic_json(
        status_path,
        {
            "request_id": request["request_id"],
            "status": "FAILED",
            "observed_at": "2026-08-03T00:00:00Z",
            "pid": 123,
            "reason": "RuntimeError:legacy-hidden-failure",
        },
    )
    campaign.publish(tmp_path, request, "DISPATCHED", worker_pid=456)

    payload = json.loads(status_path.read_text())
    assert payload["failure_history"][-1]["reason"] == "RuntimeError:legacy-hidden-failure"


def test_dead_owner_replacement_writes_bounded_forensic_receipt(tmp_path):
    request = {"request_id": "classic-formations-test-dead-owner"}
    previous = {
        "request_id": request["request_id"],
        "status": "BUILDING_INCOMPLETE_NPZ",
        "pid": 987654,
        "repair_symbols": ["ABC"],
    }

    path = campaign.write_dead_owner_receipt(tmp_path, request, previous, 987654)
    payload = json.loads(path.read_text())

    assert payload["schema"] == "classic-formation-dead-owner-receipt-v1"
    assert payload["status"] == "FAILED_OWNER_REPLACED"
    assert payload["reason"] == "STALE_ACTIVE_PID_NOT_ALIVE:987654"
    assert payload["previous_status"] == previous
    assert payload["matrix_written"] is False
    assert payload["database_written"] is False
    assert payload["live_config_written"] is False
    assert len(payload["log_tail"].encode()) <= 20000 + 1024


def test_parent_dispatch_publish_cannot_regress_child_owned_phase(tmp_path):
    request = {"request_id": "classic-formations-test-dispatch-race"}
    campaign.publish(
        tmp_path,
        request,
        "BUILDING_INCOMPLETE_NPZ",
        pid=456,
        repair_symbols=["ABC"],
    )

    campaign.publish(
        tmp_path,
        request,
        "DISPATCHED",
        pid=456,
        worker_pid=456,
    )
    payload = json.loads((tmp_path / campaign.STATUS_REL).read_text())

    assert payload["status"] == "BUILDING_INCOMPLETE_NPZ"
    assert payload["pid"] == 456
    assert payload["repair_symbols"] == ["ABC"]
    transitions = json.loads(
        (tmp_path / campaign.CAMPAIGN_REL / "campaign_transition_receipt.json").read_text()
    )["transitions"]
    assert transitions[-1]["state"] == "DISPATCHED"
    assert transitions[-1]["ignored_reason"] == "CHILD_PHASE_ALREADY_MORE_SPECIFIC"


def test_transition_receipt_is_request_bound_and_bounded(tmp_path):
    request = {"request_id": "classic-formations-test-transitions"}
    for index in range(25):
        campaign.publish(tmp_path, request, "RUNNING_TRAIN", completed_key_count=index)

    payload = json.loads(
        (tmp_path / campaign.CAMPAIGN_REL / "campaign_transition_receipt.json").read_text()
    )
    assert payload["request_id"] == request["request_id"]
    assert len(payload["transitions"]) == 20
    assert payload["transitions"][0]["completed_key_count"] == 5
    assert payload["transitions"][-1]["completed_key_count"] == 24
    assert payload["matrix_written"] is False


def test_complete_retained_npz_is_linked_without_memory_materialization(tmp_path):
    import numpy as np

    seed = tmp_path / "seed.npz"
    destination = tmp_path / "campaign/ABC.npz"
    timestamps = np.asarray([1735689600, 1764633600], dtype=np.int64)
    arrays = {"timestamps": timestamps}
    arrays.update(
        {
            name: np.ones(len(timestamps), dtype=np.float32)
            for name in campaign.FORMATION_REQUIRED_FIELDS
        }
    )
    np.savez_compressed(seed, **arrays)

    receipt = campaign.prepare_campaign_seed("ABC", seed, destination)

    assert receipt["status"] == "LINKED_COMPLETE_RETAINED_SEED"
    assert destination.is_symlink()
    assert destination.resolve() == seed.resolve()


def test_incomplete_seed_is_deferred_to_raw_rebuild_without_loading(tmp_path):
    import numpy as np

    seed = tmp_path / "seed.npz"
    destination = tmp_path / "campaign/ABC.npz"
    destination.parent.mkdir(parents=True)
    np.savez_compressed(seed, timestamps=np.asarray([1735689600, 1764633600]))
    destination.write_bytes(b"stale")

    receipt = campaign.prepare_campaign_seed("ABC", seed, destination)

    assert receipt["status"] == "DEFERRED_TO_RAW_REBUILD"
    assert receipt["reason"] == "SEED_FIELDS_OR_COVERAGE_INCOMPLETE"
    assert not destination.exists()


def test_hao_verified_transient_jump_acceptance_is_exact_bar_bound(monkeypatch, tmp_path):
    import subprocess

    payload = {
        "valid": False,
        "errors": ["unadjusted/corrupt price discontinuity: max one-bar jump=145.8%"],
        "warnings": [],
        "stats": {
            "max_bar_jump_classification": "TRANSIENT_ROUNDTRIP",
            "max_bar_jump_from_timestamp": 1780941000,
            "max_bar_jump_to_timestamp": 1780941300,
            "max_bar_jump_from_close": 0.8299999833,
            "max_bar_jump_to_close": 2.0399999619,
            "max_bar_jump_pct": 145.783,
        },
    }

    def completed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 2, json.dumps(payload) + "\n", "")

    monkeypatch.setattr(campaign.subprocess, "run", completed)
    _, accepted = campaign.audit_npz(tmp_path, Path("npz"), "HAO")
    _, rejected_other_symbol = campaign.audit_npz(tmp_path, Path("npz"), "MU")

    assert accepted["valid"] is True
    assert accepted["errors"] == []
    assert accepted["verified_transient_market_jump"]["from_timestamp"] == 1780941000
    assert rejected_other_symbol["valid"] is False


def test_partial_universe_worker_status_is_rejected():
    request_id = "classic-formations-20260802-full-universe-v1"
    assert campaign._is_partial_universe_status(
        {
            "request_id": request_id,
            "status": "RUNNING_TRAIN",
            "eligible_key_count": 114,
            "quarantined_symbols": ["CRWD"],
        },
        request_id,
    )
    assert not campaign._is_partial_universe_status(
        {
            "request_id": request_id,
            "status": "RUNNING_TRAIN",
            "eligible_key_count": 115,
            "quarantined_symbols": [],
        },
        request_id,
    )


def test_partial_universe_outputs_are_archived_before_clean_restart(tmp_path):
    request = {"request_id": "classic-formations-test"}
    train = tmp_path / campaign.CAMPAIGN_REL / "train"
    train.mkdir(parents=True)
    (train / "campaign_manifest.json").write_text("{}\n")
    lock = tmp_path / campaign.LOCK_REL
    lock.mkdir(parents=True)

    campaign._reject_partial_universe_worker(
        tmp_path,
        request,
        {
            "request_id": request["request_id"],
            "pid": 0,
            "eligible_key_count": 114,
            "quarantined_symbols": ["CRWD"],
        },
    )

    receipt = json.loads(
        (tmp_path / campaign.CAMPAIGN_REL / "partial_universe_rejection.json").read_text()
    )
    assert receipt["status"] == "REJECTED"
    assert receipt["reason"] == "FULL_115_DIRECTIONAL_CELL_CONTRACT_REQUIRED"
    assert receipt["moved"] == ["train"]
    assert not train.exists()
    assert not lock.exists()


def test_late_listing_uses_first_real_session_not_fabricated_history(tmp_path):
    import numpy as np
    import pandas as pd

    path = tmp_path / "SNDK.npz"
    start = int(pd.Timestamp("2025-02-24 14:30", tz="UTC").timestamp())
    end = int(pd.Timestamp("2026-01-02 21:00", tz="UTC").timestamp())
    np.savez_compressed(path, timestamps=np.array([start, end], dtype=np.int64))

    score = campaign._npz_seed_score(path, "SNDK")

    assert score["covers_train_window"] is True
    assert score["coverage_required_start"] == "2025-02-24"


def test_campaign_npz_materializer_derives_missing_htf_and_availability(tmp_path):
    import numpy as np

    n = 6000
    timestamps = np.arange(n, dtype=np.int64) * 300 + 1_700_000_000
    parent = timestamps - timestamps % 900
    x = np.linspace(10.0, 20.0, n, dtype=np.float32)
    arrays = {"timestamps": timestamps, "close": x.copy(), "timestamp_15m": parent}
    for stem, values in {
        "open": x - 0.05,
        "high": x + 0.10,
        "low": x - 0.10,
        "close": x,
        "volume": np.full(n, 100.0, dtype=np.float32),
    }.items():
        arrays[f"{stem}_15m"] = values
    seed = tmp_path / "seed.npz"
    destination = tmp_path / "out.npz"
    np.savez_compressed(seed, **arrays)
    receipt = campaign.materialize_campaign_npz("AMAT", seed, destination)
    assert receipt["status"] == "PASS"
    assert campaign.missing_formation_fields(destination) == []
    with np.load(destination, allow_pickle=False) as payload:
        assert np.all(payload["timestamp_D"] <= payload["timestamps"])
        assert np.count_nonzero(payload["close_D"]) > 0


def test_campaign_npz_materializer_copies_trusted_object_metadata(tmp_path):
    import numpy as np

    n = 6000
    timestamps = np.arange(n, dtype=np.int64) * 300 + 1_700_000_000
    parent = timestamps - timestamps % 900
    x = np.linspace(10.0, 20.0, n, dtype=np.float32)
    arrays = {
        "timestamps": timestamps,
        "timestamp_15m": parent,
        "source_metadata": np.array({"provider": "trusted-local-seed"}, dtype=object),
    }
    for stem, values in {
        "open": x - 0.05,
        "high": x + 0.10,
        "low": x - 0.10,
        "close": x,
        "volume": np.full(n, 100.0, dtype=np.float32),
    }.items():
        arrays[f"{stem}_15m"] = values
    seed = tmp_path / "seed_with_metadata.npz"
    destination = tmp_path / "out.npz"
    np.savez_compressed(seed, **arrays)

    receipt = campaign.materialize_campaign_npz("AMAT", seed, destination)

    assert receipt["status"] == "PASS"
    assert campaign.missing_formation_fields(destination) == []
    with np.load(destination, allow_pickle=True) as payload:
        assert payload["source_metadata"].item()["provider"] == "trusted-local-seed"
        assert np.count_nonzero(payload["close_D"]) > 0


def test_seed_selection_prefers_retained_npz_covering_train_window(tmp_path):
    import numpy as np
    import pandas as pd

    symbol = "PWR"
    canonical = tmp_path / campaign.NPZ_REL / f"{symbol}.npz"
    retained = (
        tmp_path
        / "data/reports/vector_discovery_campaigns/retained/causal_tradier_npz_v1"
        / f"{symbol}.npz"
    )
    destination = (
        tmp_path / campaign.CAMPAIGN_REL / "npz_universe_v1" / f"{symbol}.npz"
    )
    canonical.parent.mkdir(parents=True)
    retained.parent.mkdir(parents=True)
    recent_ts = np.array(
        [int(pd.Timestamp("2026-07-01", tz="UTC").timestamp())], dtype=np.int64
    )
    history_ts = np.array(
        [
            int(pd.Timestamp("2024-08-02", tz="UTC").timestamp()),
            int(pd.Timestamp("2026-02-01", tz="UTC").timestamp()),
        ],
        dtype=np.int64,
    )
    complete_recent = {"timestamps": recent_ts}
    complete_history = {"timestamps": history_ts}
    for name in campaign.FORMATION_REQUIRED_FIELDS:
        complete_recent[name] = np.ones(len(recent_ts), dtype=np.float32)
        complete_history[name] = np.ones(len(history_ts), dtype=np.float32)
    np.savez_compressed(canonical, **complete_recent)
    np.savez_compressed(retained, **complete_history)

    selected, diagnostics = campaign.select_best_npz_seed(
        tmp_path, symbol, destination
    )

    assert selected == retained
    assert diagnostics[0]["covers_train_window"] is True
    assert diagnostics[0]["required_fields"] == len(campaign.FORMATION_REQUIRED_FIELDS)


def test_npz_split_materializer_adjusts_only_price_and_raw_volume_fields():
    import numpy as np

    effective = campaign.CORPORATE_ACTION_SPLITS["CRWD"][0]["effective_epoch"]
    timestamps = np.arange(effective - 20 * 300, effective + 20 * 300, 300, dtype=np.int64)
    before = timestamps < effective
    close = np.where(before, 10.0, 40.0).astype(np.float32)
    arrays = {
        "timestamps": timestamps,
        "close": close.copy(),
        "close_15m": close.copy(),
        "volume_15m": np.full(len(close), 100.0, dtype=np.float32),
        "high_vol_regime": np.ones(len(close), dtype=np.int8),
        "volume_spike_15m": np.ones(len(close), dtype=np.float32),
    }
    receipt = campaign._apply_npz_split_adjustments("CRWD", arrays)
    assert receipt[0]["resolved_price_factor"] == 4.0
    assert np.allclose(arrays["close"][:20], 40.0)
    assert np.allclose(arrays["volume_15m"][:20], 25.0)
    assert np.all(arrays["high_vol_regime"] == 1)
    assert np.all(arrays["volume_spike_15m"] == 1.0)


def test_formation_data_contract_validates_only_causal_formation_inputs():
    source = (ROOT / "tools/backtest_data_contract.py").read_text()
    assert 'profile == "formations"' in source
    assert 'for stem in ("open", "high", "low", "close")' in source
    assert '[f"volume_{tf}" for tf in SIGNAL_TFS]' in source
    assert 'choices=("floor", "core", "ladder", "formations")' in source
