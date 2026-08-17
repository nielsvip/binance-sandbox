from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_priority_publisher_is_bounded_verified_and_public() -> None:
    text = (ROOT / "tools/publish_frontier_priority_s1.sh").read_text()
    assert "niels@157.180.125.52" in text
    assert "/Users/niels/.ssh/id_ed25519" in text
    assert "S1_FRONTIER_RUNTIME_REPAIR_REQUEST" in text
    assert ".frontier_priority_publish.lock" in text
    assert "VALIDATED_VECTOR_QUEUE_ROWS" in text
    assert "sha256sum" in text and "cmp -s" in text
    assert "tools/s1_vector_capacity_watchdog.sh" in text
    assert "tools/s1_hotlist_v8_full_recipe_watchdog.sh" in text
    assert "tools/vec_entry_overlay_walkforward.py" in text
    assert "tradier_manage.py" in text
    assert "v8_completed_snapshot_adapter.py" in text


def test_exact_watchdog_requires_fresh_strict_vector_admission() -> None:
    text = (ROOT / "tools/s1_hotlist_v8_full_recipe_watchdog.sh").read_text()
    assert 'row.get("gate_aware_vector_pass") is True' in text
    assert "There are no legacy revalidation bypasses" in text
    assert "known_revalidation_keys" not in text
    assert "prior_exact_pass_keys" not in text
    assert 'row.get("classic_formation_exact_route_preflight_pass") is not True' in text
    assert "IDLE_GATE_AWARE_VECTOR_PENDING" in text
    for forbidden in ("*.npz", "*.db", "*.db-wal", "SWITCH_MATRIX_TRB.xlsx"):
        assert forbidden not in text


def test_heartbeat_dispatches_priority_publisher() -> None:
    text = (ROOT / "mac_live_heartbeat.py").read_text()
    assert "_launch_requested_frontier_priority_publish()" in text
    assert "tools/publish_frontier_priority_s1.sh" in text
