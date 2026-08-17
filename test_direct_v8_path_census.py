"""Contracts for exhaustive matched-control direct-V8 path research."""
from __future__ import annotations

import json
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import gui_lab_manual_v8_runner as runner  # noqa: E402


def test_census_covers_every_declared_noncontrol_value_once():
    catalog = runner.catalog_by_param()
    tasks = runner.path_census_tasks()
    expected = set()
    for param, row in catalog.items():
        values = row.get("test_values") or []
        control = False if row.get("kind") == "bool" else row.get("default")
        if not runner.value_matches(control, values):
            control = values[0]
        for value in values:
            if not runner.value_matches(value, [control]):
                expected.add((param, json.dumps(value, sort_keys=True)))

    actual = {(task["param"], json.dumps(task["value"], sort_keys=True)) for task in tasks}

    assert actual == expected
    assert {task["param"] for task in tasks} == set(catalog)
    assert {task["lane"] for task in tasks} == set(range(len(runner.AUTONOMOUS_KEYS)))


def test_child_probe_enables_declared_parent_on_both_legs():
    task = next(task for task in runner.path_census_tasks() if task["param"] == "ATR_ADAPTIVE_STOP_MULT")

    assert task["control_overrides"]["ATR_ADAPTIVE_STOP_ENABLED"] is True
    assert task["candidate_overrides"]["ATR_ADAPTIVE_STOP_ENABLED"] is True
    assert task["control_overrides"]["ATR_ADAPTIVE_STOP_MULT"] == 2.0
    assert task["candidate_overrides"]["ATR_ADAPTIVE_STOP_MULT"] != 2.0


def test_same_ledger_is_not_behavior_proven_even_when_the_switch_was_read():
    task = {"id": "pair"}
    base = {
        "source": "autonomous_direct_v8_path_probe", "state": "AUDITED",
        "path_probe": {"id": "pair", "behavior_fingerprint": "same", "read_count": 3},
    }
    evidence = runner.path_census_case_state(task, {
        ("pair", "control"): {**base, "path_probe": {**base["path_probe"], "leg": "control"}},
        ("pair", "candidate"): {**base, "path_probe": {**base["path_probe"], "leg": "candidate"}},
    })

    assert evidence["state"] == "READ_BUT_NO_TRIGGER"


def test_retired_probe_receipt_cannot_block_its_fresh_restart():
    rows = [{
        "source": "autonomous_direct_v8_path_probe", "state": "RETIRED_STRICT_CONTROL_RESTART",
        "path_probe": {"id": "pair", "leg": "control"},
    }]

    assert runner._path_probe_rows(rows) == {}


def test_unselected_legacy_exit_branches_are_guarded_by_their_own_masters():
    source = (Path(__file__).resolve().parent / "tradier_manage.py").read_text()

    assert "EXIT_K5M_BOUNCE_ENABLED', False, _exit_acct, symbol, _exit_side" in source
    assert "EXIT_STRUCT_BREAK_5M_ENABLED', False, _exit_acct, symbol, _exit_side" in source
    assert "EXIT_IBS_EXHAUSTION_ENABLED', False, _exit_acct, symbol, _exit_side" in source
    assert 'EXIT_EMERGENCY_DC1H_ENABLED", False' in source
