import json
from pathlib import Path

from tools import append_vector_capacity_frontier as frontier


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def achievement(*rows):
    return {"schema": "matrix-symbol-side-achievement-v1", "rows": list(rows)}


def row(key, *, live=False, qualified=False, entry_family=None):
    return {
        "key": key,
        "live_enabled": live,
        "beats_bh_and_over_10_real_trades": qualified,
        "entry_family": entry_family,
    }


def setup_root(tmp_path, rows):
    queue = tmp_path / "data/reports/queue.json"
    write_json(queue, {"schema": "S1_VECTOR_CAPACITY_QUEUE_V1", "contract": frontier.CONTRACT, "queue": []})
    write_json(tmp_path / "data/reports/MATRIX_SYMBOL_SIDE_ACHIEVEMENT_CURRENT.json", achievement(*rows))
    return queue


def frontier_items(queue_path):
    return [
        item for item in json.loads(queue_path.read_text())["queue"]
        if item.get("frontier_contract") == frontier.CONTRACT
    ]


def test_live_unqualified_is_first_and_existing_npz_is_reused(tmp_path):
    queue = setup_root(tmp_path, [
        row("REST_LONG"),
        row("LIVE_SHORT", live=True),
        row("LIVE_LONG", live=True),
        row("DONE_LONG", live=True, qualified=True),
    ])
    existing = tmp_path / "data/matrix_npz/reused/LIVE.npz"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"npz-placeholder")

    result = frontier.append(tmp_path, queue)
    assert result["status"] == "APPENDED"
    items = frontier_items(queue)
    discovery = [item for item in items if item["kind"] == "refinement"]
    precompute = [item for item in items if item["kind"] == "precompute"]

    # The first causal window is scheduled before the second, and live keys
    # precede the non-live unqualified key in that first window.
    first_window = [item for item in discovery if item["causal_window"]["id"] == "20240326_20260801_v3_gate_aware"]
    first_keys = [key for item in first_window for key in item["frontier_identities"]]
    assert first_keys[:2] == ["LIVE_LONG@20240326_20260801_v3_gate_aware", "LIVE_SHORT@20240326_20260801_v3_gate_aware"]
    assert all(item["workers"] == 6 and item["budget_minutes"] == 25 for item in discovery)
    assert all(item["runner"] == "tools/run_path_productivity_hotlist.py" for item in discovery)
    assert all(item["workers"] == 6 for item in precompute)
    assert not any("LIVE" in item["symbols"] for item in precompute)
    assert any(item["npz_dir"] == "data/matrix_npz/reused" for item in discovery)
    assert {item["causal_window"]["start"] for item in discovery} == {"2024-03-26", "2024-07-11"}


def test_append_is_idempotent_and_key_window_identity_prevents_repeat(tmp_path):
    queue = setup_root(tmp_path, [row("AAA_LONG", live=True), row("BBB_SHORT")])
    first = frontier.append(tmp_path, queue)
    before = queue.read_bytes()
    second = frontier.append(tmp_path, queue)
    assert first["status"] == "APPENDED"
    assert second["status"] == "ALREADY_QUEUED"
    assert queue.read_bytes() == before
    identities = [
        value for item in frontier_items(queue)
        for value in item.get("frontier_identities", [])
    ]
    assert sorted(identities) == sorted({
        "AAA_LONG@20240326_20260801_v3_gate_aware", "AAA_LONG@20240711_20260801_v3_gate_aware",
        "BBB_SHORT@20240326_20260801_v3_gate_aware", "BBB_SHORT@20240711_20260801_v3_gate_aware",
    })


def test_legacy_invalid_short_windows_remain_readable_but_are_not_reused(tmp_path):
    queue = setup_root(tmp_path, [row("AAA_LONG", live=True)])
    payload = json.loads(queue.read_text())
    payload["queue"].append(
        {
            "frontier_contract": frontier.CONTRACT,
            "frontier_identities": ["AAA_LONG@20250701_20260801"],
        }
    )
    write_json(queue, payload)
    result = frontier.append(tmp_path, queue)
    assert result["status"] == "APPENDED"
    identities = [
        value for item in frontier_items(queue)
        for value in item.get("frontier_identities", [])
    ]
    assert "AAA_LONG@20240326_20260801_v3_gate_aware" in identities
    assert "AAA_LONG@20240711_20260801_v3_gate_aware" in identities
    stored = json.loads(queue.read_text())["queue"]
    assert stored[0]["frontier_identities"][0].endswith("_v3_gate_aware")


def test_existing_v3_rows_follow_canonical_and_precede_v2_and_legacy(tmp_path):
    queue = setup_root(tmp_path, [row("AAA_LONG", live=True)])
    payload = json.loads(queue.read_text())
    canonical = {"label": "canonical", "kind": "hotlist"}
    legacy = {
        "label": "legacy", "frontier_contract": frontier.CONTRACT,
        "frontier_identities": ["AAA_LONG@20250701_20260801"],
    }
    v2a = {
        "label": "v2a", "frontier_contract": frontier.CONTRACT,
        "frontier_identities": ["AAA_LONG@20240326_20260801_v2"],
    }
    v2b = {
        "label": "v2b", "frontier_contract": frontier.CONTRACT,
        "frontier_identities": ["AAA_LONG@20240711_20260801_v2"],
    }
    v3a = {
        "label": "v3a", "frontier_contract": frontier.CONTRACT,
        "frontier_identities": ["AAA_LONG@20240326_20260801_v3_gate_aware"],
    }
    v3b = {
        "label": "v3b", "frontier_contract": frontier.CONTRACT,
        "frontier_identities": ["AAA_LONG@20240711_20260801_v3_gate_aware"],
    }
    payload["queue"] = [canonical, legacy, v2a, v3a, v2b, v3b]
    write_json(queue, payload)
    result = frontier.append(tmp_path, queue)
    assert result["status"] == "REPRIORITIZED_GATE_AWARE_V3"
    stored = json.loads(queue.read_text())["queue"]
    assert [item["label"] for item in stored] == [
        "canonical", "v3a", "v3b", "legacy", "v2a", "v2b",
    ]


def test_qualified_direct_recipe_is_retested_but_ordinary_recipe_is_not(tmp_path):
    queue = setup_root(tmp_path, [
        row("DIRECT_LONG", qualified=True, entry_family="ENTRY_STOCH_HHHL"),
        row("ORDINARY_LONG", qualified=True, entry_family="ENTRY_LADDER_GREEN"),
    ])
    result = frontier.append(tmp_path, queue)
    assert result["status"] == "APPENDED"
    identities = [
        value for item in frontier_items(queue)
        for value in item.get("frontier_identities", [])
    ]
    assert any(value.startswith("DIRECT_LONG@") for value in identities)
    assert not any(value.startswith("ORDINARY_LONG@") for value in identities)


def test_target_reached_refuses_to_append_even_with_unqualified_rows(tmp_path):
    rows = [row(f"Q{i:02d}_LONG", qualified=True) for i in range(60)]
    rows.append(row("NEEDS_WORK_SHORT", live=True))
    queue = setup_root(tmp_path, rows)
    before = queue.read_bytes()
    result = frontier.append(tmp_path, queue)
    assert result == {
        "status": "TARGET_REACHED_NO_APPEND",
        "qualified_count": 60,
        "target": 60,
        "added_labels": [],
    }
    assert queue.read_bytes() == before


def test_rejects_duplicate_frontier_identity_in_existing_queue(tmp_path):
    queue = setup_root(tmp_path, [row("AAA_LONG")])
    payload = json.loads(queue.read_text())
    payload["queue"].append({"frontier_identities": ["AAA_LONG@bad-window"]})
    write_json(queue, payload)
    try:
        frontier.append(tmp_path, queue)
    except ValueError as exc:
        assert "invalid frontier window" in str(exc)
    else:
        raise AssertionError("invalid stored identity was accepted")


def test_post_source_sync_registers_frontier_appender():
    source = Path("tools/post_source_sync_s1.sh").read_text()
    assert "append_vector_capacity_frontier.py" in source
