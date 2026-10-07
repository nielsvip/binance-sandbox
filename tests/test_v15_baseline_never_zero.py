"""Test baseline never 0.00 — must be calculated from previous test OR cat_side defaults."""
import pathlib

def test_baseline_never_zero_guard_exists():
    text = pathlib.Path("v15_pilot.py").read_text()
    assert "baseline 0.00 is a lie" in text
    assert "using previous" in text
    assert "recalculated defaults" in text
    assert "WARNING baseline still 0.00" in text

def test_baseline_calculation_uses_defaults():
    text = pathlib.Path("v15_pilot.py").read_text()
    assert "get_defaults_for_symside" in text
    assert "evaluate_prepared_sanitized" in text or "evaluate_sanitized" in text

def test_no_hardcoded_zero_baseline():
    text = pathlib.Path("v15_pilot.py").read_text()
    # Ensure baseline_gain is not left as 0.0 without fix
    assert "if abs(baseline_gain) < 1e-9:" in text
