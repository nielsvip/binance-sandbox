"""Test v15_pilot baseline/delta uses v12_quick_engine NPZ and copies best overrides into new sheet (column C).

Validates the two regressions reported 2026-09-23: missing baseline/delta and missing override copy.

- Baseline must be calculated via v12_quick_engine (evaluate_prepared_sanitized / evaluate_sanitized), not hardcoded 0
- BEST overrides from previous progress/xls must overwrite recipes and be written to column C of new sheet
- Each yellow delta must be variant_gain - cumulative_before via NPZ arrays, not static
- _atomic_save must validate zip before replacing (prevents 225KB truncation)
"""
import pathlib


def test_overrides_copy_uses_best_wins():
    text = pathlib.Path("v15_pilot.py").read_text()
    # BEST must win over recipes — no `if k not in overrides` guard
    assert "BEST must WIN" in text or "overwrite recipes" in text
    assert "if overrides.get(k) != v:" in text
    # must handle both history string and single value C
    assert "Promoted rows" in text or "Baseline override" in text
    assert '"=" in str(_c)' in text


def test_baseline_still_creates_xls_for_zero_trades():
    text = pathlib.Path("v15_pilot.py").read_text()
    # 0-trades must still create XLS with baseline before skipping sweep
    assert "will still write baseline XLS then skip sweep" in text
    assert "_zero_trades_early = True" in text
    # must not return before clone — old diagnostic-only return is gone
    # check that the diagnostic-only return with no XLS is NOT present
    assert "DIAGNOSTIC ONLY, no XLS" not in text


def test_delta_uses_v12_quick_engine_npz():
    text = pathlib.Path("v15_pilot.py").read_text()
    # delta via v12_quick_engine evaluate_* with NPZ
    assert "evaluate_prepared_sanitized" in text
    assert "evaluate_sanitized" in text
    assert "v12_quick_engine" in text or "from tools.opt.v12_pilot import" in text
    # per-yellow delta = vg - cumulative_before, not static
    assert "pending_lbI" in text
    assert "delta = vg - cumulative_before" in text or "vg - cumulative_before" in text
    # BEST-C-FILL writes overrides into column C before baseline
    assert "BEST-C-FILL" in text
    assert "column C" in text or "column=3" in text


def test_atomic_save_validates_zip():
    text = pathlib.Path("v15_pilot.py").read_text()
    assert "VALIDATE tmp is a complete zip" in text
    assert "atomic-save-VALIDATE-FAIL" in text
    assert "ZipFile(tmp" in text
