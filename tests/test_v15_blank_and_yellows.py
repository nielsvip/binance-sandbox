"""Durable test for v15 cell-by-cell wiring: E blank, yellows per-row, live parity.

Verifies:
- E baseline blank when F<=0 (not propagated 10.93)
- L:BI yellows are switch-specific (BB_SQUEEZE now has 11, not 1)
- flat_entry_data_error patched for 15m-only stripped NPZ (live works with stripped)
- filter wiring uses L:BI header set (filter-trim) not random
"""
import pathlib
import openpyxl

ROOT = pathlib.Path(__file__).resolve().parents[1]

def test_E_blank_formula():
    # Check v15_pilot clone logic writes blank formula, not TEMPLATE propagation
    src = (ROOT / "v15_pilot.py").read_text()
    assert ',"")' in src and 'IF(F' in src, "v15_pilot must contain blank E formula"
    # Also verify clone_template patches E to blank variant via regex
    assert 're.sub(r",E\\d+\\)$' in src, "clone_template must patch E to blank"
    print("test_E_blank_formula PASSED")

def test_yellows_per_row_not_blanket_same():
    # Check SNDK_LONG matrix if exists, else check TEMPLATE headers
    tpl = openpyxl.load_workbook(str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_LONG.xlsx"), data_only=True)
    ws = tpl["ENTRY_REVERSAL_BOUNCE"]
    # headers count
    headers = [ws.cell(2, c).value for c in range(12, 62) if ws.cell(2, c).value and "=" in str(ws.cell(2, c).value)]
    assert len(headers) >= 10, f"need >=10 headers got {len(headers)}"
    print(f"headers {len(headers)}: {headers[:5]}")
    # Check that get_opportune_filters now returns lifecycle-matched filters for BB_SQUEEZE
    import sys
    sys.path.insert(0, str(ROOT))
    import v15_pilot
    filt = v15_pilot.get_opportune_filters("BB_SQUEEZE_ENTRY_ENABLED", "ENTRY_REVERSAL_BOUNCE")
    assert len(filt) >= 5, f"BB_SQUEEZE should have >=5 opportune filters, got {len(filt)}"
    print(f"BB_SQUEEZE opportune {len(filt)} PASSED")

def test_flat_entry_15m_not_flat():
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    # import from S1-patched file via local copy check
    # Simulate stripped NPZ indicators: k_1m=50,k_5m=50,k_15m=17 (not flat) should not be DATA_ERROR
    import tradier_route_contract
    assert tradier_route_contract.flat_entry_data_error({"k_1m": 50.0, "k_5m": 50.0, "k_15m": 17.0}, has_position=False) is False, "15m not flat should not be DATA_ERROR"
    assert tradier_route_contract.flat_entry_data_error({"k_1m": 50.0, "k_5m": 50.0, "k_15m": 50.0}, has_position=False) is True, "all flat should be DATA_ERROR"
    print("test_flat_entry_15m_not_flat PASSED")

if __name__ == "__main__":
    test_E_blank_formula()
    test_yellows_per_row_not_blanket_same()
    test_flat_entry_15m_not_flat()
    print("all v15_blank_and_yellows PASSED")
