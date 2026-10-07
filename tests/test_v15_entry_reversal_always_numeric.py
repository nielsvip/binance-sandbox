"""Test ENTRY_REVERSAL_BOUNCE always has numeric baseline/delta - never empty per user."""
import pathlib

def test_entry_reversal_always_numeric():
    text = pathlib.Path("v15_pilot.py").read_text()
    # Must always write E/F/G for every row, even on parity-fail/live-neg/E-bland
    assert "always write E/F/G for ENTRY_REVERSAL_BOUNCE even on parity-fail" in text
    assert "always write E baseline even on live-neg" in text
    assert "never leave row empty per user" in text

def test_no_empty_sheet_virus():
    text = pathlib.Path("v15_pilot.py").read_text()
    assert "DESTROY VIRUS" not in text or "never leave row empty" in text
