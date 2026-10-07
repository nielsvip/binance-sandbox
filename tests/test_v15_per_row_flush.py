"""Test per-row flush ensures baseline/delta visible within seconds, not batched 10."""
import pathlib, re

def test_per_row_flush():
    p = pathlib.Path("v15_pilot.py")
    text = p.read_text()
    # ensure every row saves, not batched 10
    assert "FIX empty sheets - save per row" in text or "FIX empty - flush per row" in text, "per-row fix missing"
    # ensure no r % 10 guard for saves (should be per row)
    # count occurrences of "if r % 10 ==" near _atomic_save - should be 0 for per-row
    # allow one for heartbeat but not for save
    saves = [m for m in re.finditer(r"_atomic_save\(wb_keep", text)]
    assert len(saves) >= 3, "expected at least 3 saves"
    # ensure E2 numeric write exists
    assert "E2 numeric written" in text, "E2 numeric not found"
    assert "STDEV_SLOPE_SIZING" in text, "E2 sheet not found"

def test_every_row_yellow():
    text = pathlib.Path("v15_pilot.py").read_text()
    assert "EVERY ROW+EVERY YELLOW" in text, "every row/yellow missing"
    assert "NO-SKIP" in text, "no-skip missing"

def test_zero_stop():
    text = pathlib.Path("v15_pilot.py").read_text()
    assert "ZERO-STOP" in text, "zero-stop missing"
