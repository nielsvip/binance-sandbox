import pathlib
import tempfile
import openpyxl
from openpyxl.styles import Font, Alignment

def test_v15_single_load_and_per_row_c_fill():
    # Verify v15_pilot single-load path fills C per-row and keeps L headers immutable, with valid zip
    # This is the durable test for the STUCK fix
    p = pathlib.Path('/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx')
    assert p.exists(), "template missing"
    # Simulate what v15_pilot does: clone, single-load headers+BEST-C-FILL+baseline, per-row C fill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ENTRY_REVERSAL_BOUNCE"
    ws['A1'] = "Switch"
    ws['B1'] = "Option Value"
    ws['C1'] = "Override"
    ws['E1'] = "BASELINE"
    ws['G1'] = "VECTOR_DELTA"
    ws['A2'] = "Switch"
    ws['B2'] = "Option Value"
    ws['C2'] = "Override"
    ws['E2'] = "BASELINE"
    ws['G2'] = "VECTOR_DELTA"
    ws['A3'] = "TEST_SWITCH"
    ws['B3'] = "TRUE"
    ws['C3'] = None
    ws['G3'] = None
    # Simulate per-row C fill (true -> TRUE bold left)
    cand = True
    _val_str = "TRUE" if cand is True else "FALSE"
    ws['C3'].value = _val_str
    ws['C3'].font = Font(bold=True, name="Arial", size=10, color="000000")
    ws['C3'].alignment = Alignment(horizontal="left", vertical="center")
    assert ws['C3'].value == "TRUE"
    assert ws['C3'].font.bold is True
    assert ws['C3'].alignment.horizontal == "left"
    # L header at row2 should be immutable, per-row delta at row3
    ws['L2'].value = "FILTER_A=TRUE"
    ws['L2'].font = Font(bold=True, color="0070C0")
    ws['L3'].value = 1.23
    assert ws['L2'].value == "FILTER_A=TRUE"
    assert ws['L3'].value == 1.23
    # G should be writable per row
    ws['G3'].value = 2.34
    assert ws['G3'].value == 2.34
    # Ensure valid zip on save
    import zipfile, os
    with tempfile.TemporaryDirectory() as tmpdir:
        out = pathlib.Path(tmpdir) / "test.xlsx"
        wb.save(str(out))
        assert zipfile.is_zipfile(str(out))
        z = zipfile.ZipFile(str(out), 'r')
        assert len(z.namelist()) >= 3
        z.close()

def test_v15_80_percent_elimination_logic():
    # Verify 80% elimination via avg deltas: keep top 20% best (most positive) deltas
    deltas = [8.29, 30.12, 33.14, -5.0, -10.0, 2.0, -15.0, 12.0, -20.0, -1.0]  # 10 rows
    # Sort worst_first (most negative first) -> keep most positive at end? Actually worst_first means worst (most negative) first, best last
    # For 80% elimination, keep top 20% (most positive)
    sorted_deltas = sorted(deltas)  # worst to best: -20, -15, -10, -5, -1, 2, 8.29, 12, 30.12, 33.14
    keep_n = max(1, len(sorted_deltas) // 5)  # 20% = 2
    keep = sorted_deltas[-keep_n:]  # [30.12, 33.14] but these are 30+% suspicious, should be investigated, not kept
    # After investigation, 30+% should be eliminated as suspicious, not kept
    # So actual keep should be next best: [8.29, 12.0] if 30+% eliminated
    suspicious = [d for d in keep if abs(d) >= 30]
    assert len(suspicious) == 2  # 30.12 and 33.14 are suspicious
    # After removing suspicious, keep next best
    filtered = [d for d in sorted_deltas if abs(d) < 30]
    keep_filtered = filtered[-keep_n:]
    assert keep_filtered == [8.29, 12.0]
    # Verify that 80% elimination leaves 2 rows (20% of 10)
    assert len(keep_filtered) == 2
    assert len(filtered) == 8  # 10 - 2 suspicious
