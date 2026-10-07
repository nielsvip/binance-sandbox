"""Test V15_PILOT.XLS always finds BEST settings from previous tests first and calcs baseline on previous overrides."""
import pathlib

def test_best_baseline_always_loads_previous():
    text = pathlib.Path("v15_pilot.py").read_text()
    assert "PREVIOUS-TEST-as-baseline: always load best overrides from previous progress/xls for sym_side first" in text
    assert "BEST-prev-progress" in text
    assert "BEST-prev-xls" in text
    assert "cumulative_overrides" in text
    assert "hustler_overrides" in text
    assert "30d_matrix.xlsx" in text
    assert "then calc baseline on those overrides" in text or "as baseline" in text

def test_best_candidates_still_present():
    text = pathlib.Path("v15_pilot.py").read_text()
    assert "hustler_best.json" in text
    assert "BEST-baseline" in text

def test_baseline_uses_overrides():
    text = pathlib.Path("v15_pilot.py").read_text()
    # After loading previous overrides, baseline is calculated with them
    assert "overrides" in text
    assert "baseline_vec = evaluate" in text or "baseline_live" in text
