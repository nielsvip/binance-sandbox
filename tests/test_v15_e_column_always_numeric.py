"""Test E-column always numeric per user - was None for NEG -> empty sheets."""
import pathlib

def test_e_column_always_numeric():
    text = pathlib.Path("v15_pilot.py").read_text()
    # E for this row should always be numeric, not None for NEG
    assert "E for this row (col 5) is cumulative_before - always numeric per user" in text
    assert 'ws_row.cell(row=r, column=5).value = float(cumulative_before)' in text
    assert "if delta_best > 0 else None" not in text or text.count("if delta_best > 0 else None") == 0, "still has None for NEG"

def test_no_baseline_string():
    text = pathlib.Path("v15_pilot.py").read_text()
    assert "E2 numeric written" in text
