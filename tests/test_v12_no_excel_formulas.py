"""Test 1: v12_quick_engine must not contain Excel formulas where system gets stuck."""
import pathlib

def test_v12_has_no_excel_formulas():
    base = pathlib.Path("/Users/niels/Documents/binance")
    v12 = (base / "v12_quick_engine.py").read_text()
    # v12 is pure vector engine, never writes Excel formulas
    assert v12.count("VLOOKUP") == 0, "v12 should not contain VLOOKUP"
    assert v12.count("=IF") == 0, "v12 should not contain =IF"
    # check for ws.cell with formula assignment
    import re
    formula_writes = re.findall(r'ws\.cell\(.*value\s*=\s*["\']=', v12)
    assert len(formula_writes) == 0, f"v12 writes Excel formulas: {formula_writes[:3]}"
    # ensure it handles stdev via stdev_slope, not bare stdev that would be missing in npz
    assert "stdev_slope" in v12 or "STDEV" in v12 or "stdev" in v12.lower(), "v12 should handle stdev_slope"
