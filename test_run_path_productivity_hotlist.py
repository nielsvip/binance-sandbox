from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import run_path_productivity_hotlist as hotlist


def test_file_path_cli_resolves_tools_package_after_manifest(tmp_path) -> None:
    npz = tmp_path / "npz"
    out = tmp_path / "out"
    npz.mkdir()
    long_list = tmp_path / "long.json"
    short_list = tmp_path / "short.json"
    long_list.write_text("[]")
    short_list.write_text("[]")
    completed = subprocess.run(
        [
            sys.executable,
            str(Path("tools/run_path_productivity_hotlist.py").resolve()),
            "--npz-dir", str(npz),
            "--out-dir", str(out),
            "--long-list", str(long_list),
            "--short-list", str(short_list),
        ],
        cwd=Path.cwd(), capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert (out / "campaign_receipt.json").is_file()


def _path_row(
    group: str,
    family: str,
    param: str,
    *,
    live: bool = True,
    vector_reader: bool = False,
) -> dict:
    return {
        "group": group,
        "family": family,
        "param": param,
        "description": param,
        "consumed_by": {"live": live, "vec": vector_reader},
        "static_read_sites": {
            "live_decision": ["live.py"] if live else [],
            "exact_wrapper": ["exact.py"],
            "vector": ["vec.py"] if vector_reader else [],
        },
        "activation_dependencies": [],
        "deployment_scope": "GLOBAL_ONLY",
        "differential_readiness": "READY_EXACT_ONLY",
    }


def test_connected_lifecycle_inventory_is_complete_and_fail_closed(tmp_path) -> None:
    source = tmp_path / "inventory.json"
    source.write_text(
        json.dumps(
            {
                "paths": [
                    _path_row(
                        "ENTRY",
                        "WT_DC_ENTRY",
                        "WT_DC_ENTRY_THRESHOLD",
                        vector_reader=True,
                    ),
                    _path_row("EXIT", "UNKNOWN_EXIT", "UNKNOWN_EXIT_ENABLED"),
                    _path_row(
                        "ENTRY", "RECLAIM_FAMILY", "REENTRY_LIVE_MONITOR_ENABLED"
                    ),
                    _path_row("ENTRY", "AUG_FAMILY", "PYRAMID_AUGMENT_ENABLED"),
                    _path_row("EXIT", "TRIM_FAMILY", "PARTIAL_PROFIT_ENABLED"),
                    _path_row("OTHER", "NOT_LIFECYCLE", "UNRELATED_LIMIT"),
                    _path_row(
                        "ENTRY", "DISCONNECTED", "DISCONNECTED_ENABLED", live=False
                    ),
                ]
            }
        )
    )

    inventory = hotlist.lifecycle_coverage_inventory(source)

    assert inventory["connected_family_count"] == 5
    assert inventory["family_count_by_role"] == {
        "ENTRY": 1,
        "AUGMENT": 1,
        "REDUCE": 1,
        "EXIT": 1,
        "REENTER": 1,
    }
    by_family = {row["family"]: row for row in inventory["families"]}
    assert by_family["WT_DC_ENTRY"]["coverage_status"] == (
        "VECTOR_ADAPTER_PRESENT_REVIEW_REQUIRED"
    )
    assert by_family["UNKNOWN_EXIT"]["coverage_status"] == (
        "UNTESTED_NEEDS_EXACT"
    )
    assert by_family["UNKNOWN_EXIT"]["retirement"]["status"] == (
        "NOT_RETIRED_COHORT_PROOF_REQUIRED"
    )
    assert "NOT_LIFECYCLE" not in by_family
    assert "DISCONNECTED" not in by_family


def test_bounded_manifest_arguments_require_canonical_six_workers() -> None:
    args = argparse.Namespace(
        workers=6,
        budget_minutes=25,
        random_curves=64,
        entry_shortlist=32,
        entry_beam_width=3,
        exit_beam_width=8,
    )
    hotlist.validate_bounded_args(args)

    args.workers = 5
    with pytest.raises(ValueError, match="exactly six"):
        hotlist.validate_bounded_args(args)
    args.workers = 6
    args.random_curves = 65
    with pytest.raises(ValueError, match="random_curves"):
        hotlist.validate_bounded_args(args)


def test_exact_disjoint_cohort_bypasses_primary_pilots_and_is_hash_bound(
    tmp_path: Path,
) -> None:
    long_list = tmp_path / "long.json"
    short_list = tmp_path / "short.json"
    long_list.write_text(json.dumps(["AAPL", "ARM", "BK"]))
    short_list.write_text(json.dumps(["AU", "WPM", "ALB"]))
    exact = (
        "AAPL_LONG", "ARM_LONG", "BK_LONG",
        "AU_SHORT", "WPM_SHORT", "ALB_SHORT",
    )
    keys, ranked, manifest = hotlist.cohort(long_list, short_list, exact)
    assert keys == list(exact)
    assert ranked == set(exact)
    assert manifest["selection_mode"] == "EXACT_DISJOINT_COHORT"
    assert len(manifest["exact_keys_sha256"]) == 64
    assert not set(keys) & set(hotlist.PILOTS)


def test_exact_single_key_cohort_is_valid_for_bounded_continuation(tmp_path: Path) -> None:
    long_list = tmp_path / "long.json"
    short_list = tmp_path / "short.json"
    long_list.write_text(json.dumps(["AAPL"]))
    short_list.write_text(json.dumps(["AU"]))
    keys, ranked, manifest = hotlist.cohort(long_list, short_list, ("BK_LONG",))
    assert keys == ["BK_LONG"]
    assert ranked == {"BK_LONG"}
    assert manifest["selection_mode"] == "EXACT_DISJOINT_COHORT"


def test_complete_recipe_has_every_role_and_strict_later_gate() -> None:
    candidate = {
        "entry_family": "ENTRY_WT_DC",
        "entry_artifact": "/missing/vector-artifact",
        "exit_family": "EXIT_MTF_ATR_TRAIL",
        "exit_params": {"mult": 2.0},
    }
    same_bar = hotlist.complete_lifecycle_recipe(
        candidate,
        {"exit_indices": [8], "reentry_indices": [8], "reentry_violations": 0},
    )
    later = hotlist.complete_lifecycle_recipe(
        candidate,
        {"exit_indices": [8], "reentry_indices": [9], "reentry_violations": 0},
    )
    no_observation = hotlist.complete_lifecycle_recipe(
        candidate,
        {"exit_indices": [8], "reentry_indices": [], "reentry_violations": 0},
    )

    assert set(hotlist.LIFECYCLE_ROLES).issubset(same_bar)
    assert same_bar["REENTER"]["strictly_later_reentry_proven"] is False
    assert no_observation["REENTER"]["strictly_later_reentry_proven"] is False
    assert later["REENTER"]["strictly_later_reentry_proven"] is True
    assert later["REENTER"]["params"]["same_exit_bar_reentry_allowed"] is False
    assert later["exact_hold_control"] == {
        "entry_floor": "all_entries_off()",
        "exit_floor": "all_exits_off()",
        "seed_count": 1,
        "seed_notional_usd": 2000.0,
        "tested_family_companions_must_reenable_explicitly": True,
        "non_seed_entry_family_allowed": False,
        "clamp_count_allowed": 0,
    }


def test_terminal_right_censoring_never_substitutes_for_strict_later_proof() -> None:
    row = hotlist._strictly_later_reentry(
        {
            "exit_indices": [10],
            "reentry_indices": [],
            "pending_reentry_count": 1,
            "terminal_right_censored": True,
            "reentry_violations": 0,
        }
    )
    assert row["terminal_right_censored"] is True
    assert row["strictly_later_reentry_proven"] is False
    assert row["status"] == "UNTESTED_NEEDS_EXACT"


def test_strict_later_recipe_consumes_causally_linked_reentry_pairs() -> None:
    row = hotlist._strictly_later_reentry(
        {
            "action_evidence_status": "CAUSAL_EVENT_LEDGER",
            "action_fingerprint": "a" * 64,
            "ledger_sha256": "a" * 64,
            "exit_indices": [11, 40],
            "reentry_indices": [12, 41],
            "reentry_pairs": [
                {"exit_index": 11, "reentry_index": 12},
                {"exit_index": 40, "reentry_index": 41},
            ],
            "reentry_violations": 0,
        }
    )
    assert row["strictly_later_reentry_proven"] is True
    assert row["status"] == "VECTOR_ADAPTER_MEASURED"
    assert row["action_evidence_status"] == "CAUSAL_EVENT_LEDGER"
    assert row["action_fingerprint"] == "a" * 64
    assert row["reentry_pairs"][1] == {
        "exit_index": 40,
        "reentry_index": 41,
    }


def test_causal_fold_evidence_can_cross_vector_recipe_acceptance_gate() -> None:
    fingerprint = "b" * 64
    fold = {
        "validation": ["2025-01-01", "2025-07-01"],
        "strategy_return_pct": 12.0,
        "bh_return_pct": 4.0,
        "weighted_tim_pct": 50.0,
        "exit_fills": 30,
        "entry_fills": 31,
        "future_htf_source_count": 0,
        "entry_capacity_breach": False,
        "insolvent": False,
        "bars_flat_beyond_reclaim": 0,
        "action_evidence_status": "CAUSAL_EVENT_LEDGER",
        "action_fingerprint": fingerprint,
        "ledger_sha256": fingerprint,
        "exit_indices": [11],
        "reentry_indices": [12],
        "reentry_pairs": [{"exit_index": 11, "reentry_index": 12}],
        "reentry_violations": 0,
        "strictly_later_reentry_proven": True,
    }
    beam = {
        "results": [
            {
                "frozen_exit_beam": [
                    {
                        "entry_family": "ENTRY_WT_DC",
                        "entry_artifact": "/missing/vector-artifact",
                        "exit_family": "EXIT_MTF_ATR_TRAIL",
                        "exit_params": {"mult": 2.0},
                        "all_fold_clamp_count": 0,
                        "all_fold_max_drawdown_account_pct": 2.0,
                        "all_folds_strict": True,
                        "discovery_strict_fold_count": 2,
                        "discovery_fold_evidence": [],
                        "untouched_final_validation": {
                            "strict": True,
                            "fold_evidence": fold,
                        },
                    }
                ]
            }
        ]
    }

    rows = hotlist.final_candidate_rows(beam, "MU_LONG", (20.0, 60.0))

    assert len(rows) == 1
    assert rows[0]["strictly_later_reentry_gate"] is True
    assert rows[0]["hotlist_eligible"] is True
    assert rows[0]["exact_queue_status"] == (
        "NOT_DISPATCHED_ADAPTER_UNVERIFIED"
    )
    assert rows[0]["action_fingerprint"] == fingerprint
    assert rows[0]["ledger_sha256"] == fingerprint

    beam["results"][0]["frozen_exit_beam"][0]["all_folds_strict"] = False
    rejected = hotlist.final_candidate_rows(beam, "MU_LONG", (20.0, 60.0))
    assert rejected[0]["lifecycle_all_folds_strict_gate"] is False
    assert rejected[0]["hotlist_eligible"] is False
    assert rejected[0]["exact_queue_status"] == "VECTOR_DIAGNOSTIC"


def _final_key_row(key: str, npz: Path) -> dict:
    return {
        "key": key,
        "status": "HOTLIST_WINNER",
        "target_tim_pct": list(hotlist.target_for(key, {key})),
        "elapsed_seconds": 1.0,
        "npz": str(npz.resolve()),
        "npz_sha256": hotlist.sha256(npz),
        "entry_exit_candidates": 1,
        "eligible_candidate_count": 1,
        "champion": {
            "hotlist_eligible": True,
            "alpha_vs_bh_pp": 1.0,
            "entry_family": "ENTRY_WT_DC",
            "exit_family": "EXIT_MTF_ATR_TRAIL",
        },
        "top_candidates": [],
    }


def test_resume_runs_six_slots_only_on_remaining_keys_and_merges_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = [f"SYM{i}_LONG" for i in range(8)]
    ranked = set(keys)
    npz_dir = tmp_path / "npz"
    npz_dir.mkdir()
    for key in keys:
        (npz_dir / f"{key.rsplit('_', 1)[0]}.npz").write_bytes(key.encode())
    long_list = tmp_path / "long.json"
    short_list = tmp_path / "short.json"
    long_list.write_text("[]")
    short_list.write_text("[]")
    progress = tmp_path / "progress.json"
    monkeypatch.setattr(
        hotlist,
        "cohort",
        lambda *_: (
            keys,
            ranked,
            {
                "long_path": str(long_list.resolve()),
                "short_path": str(short_list.resolve()),
                "long_sha256": hotlist.sha256(long_list),
                "short_sha256": hotlist.sha256(short_list),
                "top10_long": [],
                "top10_short": [],
            },
        ),
    )
    monkeypatch.setattr(
        hotlist,
        "lifecycle_coverage_inventory",
        lambda: {"connected_family_count": 0, "families": []},
    )
    from tools import matrix_live_progress

    telemetry: list[tuple[int, str]] = []
    monkeypatch.setattr(
        matrix_live_progress,
        "update_worker",
        lambda _path, slot, **fields: telemetry.append((slot, fields["worker_id"])),
    )
    calls: list[str] = []

    def fake_run_key(key, _ranked, args, heartbeat=None):
        calls.append(key)
        row = _final_key_row(key, npz_dir / f"{key.rsplit('_', 1)[0]}.npz")
        key_dir = args.out_dir / "keys" / key
        key_dir.mkdir(parents=True, exist_ok=True)
        hotlist.atomic_json(key_dir / "key_result.json", row)
        return row

    monkeypatch.setattr(hotlist, "run_key", fake_run_key)

    prior = tmp_path / "prior"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_path_productivity_hotlist.py",
            "--npz-dir", str(npz_dir),
            "--out-dir", str(prior),
            "--long-list", str(long_list),
            "--short-list", str(short_list),
            "--progress-snapshot", str(progress),
        ],
    )
    assert hotlist.main() == 0
    assert sorted(calls) == sorted(keys)

    resumed = keys[:3]
    for key in keys[3:]:
        (prior / "keys" / key / "key_result.json").unlink()
    calls.clear()
    telemetry.clear()
    resumed_out = tmp_path / "resumed"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_path_productivity_hotlist.py",
            "--npz-dir", str(npz_dir),
            "--out-dir", str(resumed_out),
            "--resume-from", str(prior),
            "--long-list", str(long_list),
            "--short-list", str(short_list),
            "--progress-snapshot", str(progress),
        ],
    )

    assert hotlist.main() == 0
    assert sorted(calls) == sorted(keys[3:])
    assert {slot for slot, _worker in telemetry} == set(range(1, 7))
    receipt = json.loads((resumed_out / "campaign_receipt.json").read_text())
    assert receipt["resumed_keys"] == resumed
    assert receipt["resumed_key_count"] == 3
    assert receipt["newly_run_keys"] == keys[3:]
    assert receipt["newly_run_key_count"] == 5
    assert len(receipt["hotlist"]) == len(keys)
    slots = json.loads((resumed_out / "campaign_manifest.json").read_text())[
        "worker_slots"
    ]
    assert len(slots) == 6
    assert sorted(key for slot in slots for key in slot["assigned_keys"]) == sorted(
        keys[3:]
    )


def test_resume_fails_closed_on_manifest_or_final_key_mismatch(tmp_path: Path) -> None:
    npz = tmp_path / "npz" / "MU.npz"
    npz.parent.mkdir()
    npz.write_bytes(b"npz")
    manifest = {
        "schema": "TRB_PATH_PRODUCTIVITY_HOTLIST_V2",
        "tier": "VECTOR_LIFECYCLE",
        "run_class": "VECTOR_DISCOVERY",
        "evidence_class": "VECTOR_LIFECYCLE_NOT_ENGINE",
        "cohort_keys": ["MU_LONG"],
        "ready_keys": ["MU_LONG"],
        "missing_npz_keys": [],
        "source_lists": {
            "long_sha256": "a" * 64,
            "short_sha256": "b" * 64,
            "top10_long": ["MU"],
            "top10_short": [],
        },
        "entry_families": list(hotlist.DEFAULT_ENTRY_FAMILIES),
        "exit_families": list(hotlist.SUPPORTED_EXIT_FAMILIES),
        "complete_recipe_roles": list(hotlist.LIFECYCLE_ROLES),
        "per_key_budget_minutes": 25.0,
        "validation_publication_budget_minutes": 5.0,
        "hard_limits": {"workers": 6, "candidate_grid": "BOUNDED_BEAM_NOT_CARTESIAN"},
        "validation_window": {"start": "2024-01-01", "end": "2026-01-01"},
        "costs": {"commission_bps": 0.0, "slippage_bps": 5.0},
        "strictly_later_reentry_required": True,
        "terminal_right_censoring_contract": True,
        "matrix_written": False,
        "database_written": False,
        "live_written": False,
    }
    prior = tmp_path / "prior"
    hotlist.atomic_json(prior / "campaign_manifest.json", manifest)
    key_dir = prior / "keys" / "MU_LONG"
    key_dir.mkdir(parents=True)
    hotlist.atomic_json(key_dir / "key_result.json", _final_key_row("MU_LONG", npz))

    mismatched = json.loads(json.dumps(manifest))
    mismatched["hard_limits"]["workers"] = 5
    with pytest.raises(ValueError, match="CONTRACT_MISMATCH"):
        hotlist.load_resumed_key_results(prior, mismatched, npz.parent, {"MU_LONG"})

    malformed = _final_key_row("MU_LONG", npz)
    malformed["target_tim_pct"] = [0.0, 100.0]
    hotlist.atomic_json(key_dir / "key_result.json", malformed)
    with pytest.raises(ValueError, match="EVIDENCE_INVALID"):
        hotlist.load_resumed_key_results(prior, manifest, npz.parent, {"MU_LONG"})

    malformed["target_tim_pct"] = [50.0, 80.0]
    malformed["status"] = "PARTIAL_BUT_TRUST_ME"
    hotlist.atomic_json(key_dir / "key_result.json", malformed)
    with pytest.raises(ValueError, match="STATUS_INVALID"):
        hotlist.load_resumed_key_results(prior, manifest, npz.parent, {"MU_LONG"})
