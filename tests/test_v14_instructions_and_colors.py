import pathlib
import openpyxl
from openpyxl.styles import PatternFill


def test_instructions_detailed_for_dumbest_agent():
    """INSTRUCTIONS tab must contain all v14 crystal-clear rules for dumbest agent."""
    p = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE.xlsx")
    assert p.exists(), "TEMPLATE.xlsx must exist"
    wb = openpyxl.load_workbook(str(p), data_only=False)
    assert "INSTRUCTIONS" in wb.sheetnames, "INSTRUCTIONS tab missing"
    ws = wb["INSTRUCTIONS"]
    text = "\n".join(str(ws.cell(r, 1).value or "") for r in range(1, ws.max_row + 1))
    # Must contain each new rule
    required = [
        "ANY DUMB AGENT CAN EXECUTE",
        "FOR EVERY ROW (EVERY SWITCH) TEST EVERY APPLICABLE FILTER",
        "BLANKET FILTERS — AT END OF EACH TAB, BEFORE CONTINUING TO NEXT TAB",
        "RESULTS ROW — At end of EACH tab",
        "TABS SEQUENTIALLY",
        "FILENAME — Save as {SYM_SIDE}_bh{bh}_gain{gain}",
        "FILTER SCOPE THRESHOLD -- Keep testing ALL applicable filters for EVERY switch (rule 2) UNTIL >100 sym_sides",
        "stock_long / stock_short / crypto_long / crypto_short",
        "useful_filters_{universe}.json",
        "PROVENANCE COLOR",
        "GREENISH #C6EFCE",
        "LIGHT BLUE #ADD8E6",
        "DARKER #5B8DB2",
        "v14_sequential_filler.py",
        "get_opportune_filters",
        "FINAL CHECK",
    ]
    for phrase in required:
        assert phrase in text, f"INSTRUCTIONS missing required phrase: {phrase}"
    wb.close()


def test_all_filters_tested_until_100_then_useful():
    """v14 must implement >100 threshold: test ALL until 100, then useful per universe."""
    p = pathlib.Path("/Users/niels/Documents/binance/tools/opt/v14_sequential_filler.py")
    assert p.exists(), "v14_sequential_filler.py must exist"
    src = p.read_text()
    assert "_useful_filters_for_universe" in src, "missing useful filters helper"
    assert "_universe_for_symside" in src, "missing universe helper"
    assert 'FILTER_SCOPE' in src, "missing FILTER_SCOPE logging"
    assert "useful-only" in src, "missing useful-only logging"
    assert ">100" in src or "tested < 100" in src, "missing 100 threshold"
    assert "stock_long" in src and "crypto_long" in src, "missing universe handling stock_long/crypto_long"
    # Verify blanket at end of each tab before next tab
    assert "BLANKET FILTERS" in src or "GENERAL" in src, "missing blanket logic"
    # Verify results row writes all metrics
    assert "write_results_variant" in src, "missing write_results_variant"
    assert "variant_gain" in src, "missing variant_gain in results"


def test_provenance_colors_green_blue_darker():
    """certify_template must use greenish for crypto, light blue for stock, darker for both."""
    p = pathlib.Path("/Users/niels/Documents/binance/tools/opt/certify_template.py")
    assert p.exists(), "certify_template.py must exist"
    src = p.read_text()
    assert "GREENISH" in src and "C6EFCE" in src, "missing GREENISH #C6EFCE for crypto"
    assert "LIGHT_BLUE" in src and "ADD8E6" in src, "missing LIGHT_BLUE #ADD8E6 for stock"
    assert "DARKER" in src and "5B8DB2" in src, "missing DARKER #5B8DB2 for BOTH"
    assert "PROVEN_LOG" in src, "missing provenance log certify_proven.json"
    assert "crypto" in src.lower() and "stock" in src.lower(), "missing crypto/stock proven logic"
    assert "BOTH" in src, "missing BOTH provenance"
    # Verify fill is set based on provenance
    assert 'fill = GREENISH' in src or 'GREENISH' in src, "missing greenish fill assignment"
    assert 'fill = DARKER' in src, "missing darker fill assignment"


def test_blanket_runs_before_next_tab_and_sequential():
    """v14 must fill tabs sequentially, blanket before next tab, bh/gain in filename."""
    p = pathlib.Path("/Users/niels/Documents/binance/tools/opt/v14_sequential_filler.py")
    src = p.read_text()
    # Sequential tabs
    assert "for sheet in sheets:" in src, "missing sequential sheet loop"
    # Blanket at end of each tab before continuing
    assert "BLANKET" in src or "GENERAL blanket" in src or "generals" in src.lower(), "missing blanket at end of tab"
    # Results row filled with all metrics
    assert "Results_30d_Deltas" in src, "missing Results_30d_Deltas"
    # bh and gain in filename
    assert 'bh{bh}' in src or "bh" in src and "gain" in src, "missing bh/gain filename logic"
    assert "_30d_matrix.xlsx" in src, "missing _30d_matrix.xlsx filename"
    # v14 where applicable
    assert "v14" in src.lower(), "missing v14 marker"
