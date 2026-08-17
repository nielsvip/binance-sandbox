from pathlib import Path


def test_operator_walks_live_trb_side_lists_not_workbook_columns():
    text = Path("tools/trb_operator_watchdog.sh").read_text()
    assert '"$ROOT/symbols_trb_long.json" "$ROOT/symbols_trb_short.json"' in text
    assert "Workbook columns can be stale" in text
    # The workbook should remain a reporting surface; it is not a queue source.
    assert 'SWITCH_MATRIX_TRB.xlsx" "$mapfile_path"' not in text


def test_persistent_operator_refreshes_read_only_lineage_before_dispatch():
    text = Path("tools/trb_operator_watchdog_loop.sh").read_text()
    assert 'tools/tradier_per_sym_lineage.py' in text
    assert 'tools/trb_operator_watchdog.sh' in text
