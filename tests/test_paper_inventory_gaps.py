import pytest

from tools.paper_scenario_db import ScenarioDB


def test_inventory_gaps_are_immutable_and_source_epoch_scoped(tmp_path):
    db = ScenarioDB(tmp_path / "inventory.sqlite")
    gap = dict(gap_id="gap-v1", sym_side="BTCUSDC_LONG", reason="NO_ACTIVE_SETTINGS", observed_at="2026-10-31T00:00:00Z", source_hash="source-v1", payload={"source": "plan"})
    assert db.add_inventory_gap(**gap) == "gap-v1"
    assert db.add_inventory_gap(**gap) == "gap-v1"
    with pytest.raises(ValueError, match="conflict"):
        db.add_inventory_gap(**{**gap, "reason": "INVALID_CAT_SIDE"})
    later = {**gap, "gap_id": "gap-v2", "observed_at": "2026-11-01T00:00:00Z", "source_hash": "source-v2"}
    assert db.add_inventory_gap(**later) == "gap-v2"
    rows = db.query("SELECT gap_id,reason,source_hash FROM inventory_gaps ORDER BY observed_at")
    assert [tuple(row) for row in rows] == [
        ("gap-v1", "NO_ACTIVE_SETTINGS", "source-v1"),
        ("gap-v2", "NO_ACTIVE_SETTINGS", "source-v2"),
    ]
    db.close()
