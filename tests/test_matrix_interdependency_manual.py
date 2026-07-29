import json

from tools import build_matrix_interdependency_manual as manual


def test_every_tradeable_matrix_path_has_executable_metadata():
    expected = set(manual.tradeable_manifest_rows())
    payload = manual.build_payload()
    rows = {row["param"]: row for row in payload["paths"]}
    assert set(rows) == expected
    assert payload["path_count"] == len(expected)
    for name, row in rows.items():
        assert len(row["description"]) >= 40, name
        assert row["main_switch"], name
        assert row["precedence_layer"] in manual.LAYERS, name
        assert row["precedence_order"] == manual.LAYERS[
            row["precedence_layer"]
        ]
        assert row["retest_class"] in manual.RETEST_RULES, name
        assert row["invalidates_layers"], name
        assert set(row["requires_controls"]) <= set(manual.CONTRACT_NODES), name
        assert set(manual.CONTRACT_NODES) <= set(row["dependencies"]), name


def test_master_edges_and_retest_rules_are_machine_readable(tmp_path):
    payload = manual.build_payload()
    json_path = tmp_path / "manual.json"
    csv_path = tmp_path / "manual.csv"
    manual.write_outputs(payload, json_path, csv_path)
    restored = json.loads(json_path.read_text())
    assert restored["write_contract"] == {
        "live_config": False,
        "matrix_cells": False,
        "fingerprints": False,
        "research_metadata_only": True,
    }
    masters = {
        (edge["from"], edge["to"])
        for edge in restored["adjacency"]
        if edge["kind"] == "FAMILY_MASTER_ENABLES"
    }
    for row in restored["paths"]:
        if row["main_switch"] != row["param"]:
            assert (row["main_switch"], row["param"]) in masters
    assert len(csv_path.read_text().splitlines()) == restored["path_count"] + 1
