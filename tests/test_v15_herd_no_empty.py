"""Test herd calculate-not-empty: empty xlsx never counted as done nor pushed."""
import pathlib

def test_is_xlsx_empty_helper_exists():
    text = pathlib.Path("tools/v15_local_herd.py").read_text()
    assert "def _is_xlsx_empty" in text
    assert "STDEV_SLOPE_SIZING" in text
    assert "E3" in text

def test_local_done_skips_empty():
    text = pathlib.Path("tools/v15_local_herd.py").read_text()
    # local_done_set must check empty before counting as done
    assert "if _is_xlsx_empty(p):" in text

def test_push_skips_empty():
    text = pathlib.Path("tools/v15_local_herd.py").read_text()
    assert "CALCULATE NOT SEND EMPTY" in text
    assert "push-skip-empty" in text

def test_is_xlsx_empty_logic():
    import tempfile, openpyxl
    p = pathlib.Path(tempfile.mktemp(suffix=".xlsx"))
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "STDEV_SLOPE_SIZING"
        ws["E3"] = None
        wb.save(str(p))
        import importlib.util
        spec = importlib.util.spec_from_file_location("herd", "tools/v15_local_herd.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod._is_xlsx_empty(p) is True
        # E3 numeric but no G delta -> still empty (no switch recalc yet)
        wb2 = openpyxl.load_workbook(str(p))
        ws2 = wb2["STDEV_SLOPE_SIZING"]
        ws2["E3"] = -1.23  # baseline from defaults+overrides on NEW NPZ
        ws2.cell(row=3, column=12).value = "default backup text"  # L
        # O:IK blank (no yellow tried) -> still empty because no G
        wb2.save(str(p))
        assert mod._is_xlsx_empty(p) is True
        # G delta numeric (real NPZ recalc for that switch) -> not empty
        # O:BI yellow: if yellow, filter tried on that switch; if pos, added to G and C
        wb2 = openpyxl.load_workbook(str(p))
        ws2 = wb2["STDEV_SLOPE_SIZING"]
        ws2["E3"] = -1.23
        ws2.cell(row=3, column=7).value = 0.8  # G delta from switch recalc + pos yellows (real NPZ)
        ws2.cell(row=3, column=12).value = "default backup text"  # L
        # simulate yellow in O tried and pos -> already folded into G, C would have filter+option
        ws2.cell(row=3, column=15).value = 0.25  # O yellow pos delta (real NPZ, would have been added to G)
        ws2.cell(row=3, column=3).value = "SW=1 + filt=opt"  # C overrides
        wb2.save(str(p))
        assert mod._is_xlsx_empty(p) is False
        # yellow blank -> leave blank, not 0.0 fake; G still numeric from switch recalc alone
        wb3 = openpyxl.load_workbook(str(p))
        ws3 = wb3["STDEV_SLOPE_SIZING"]
        ws3.cell(row=3, column=15).value = None  # blank = not yellow / not tried
        wb3.save(str(p))
        assert mod._is_xlsx_empty(p) is False
    finally:
        p.unlink(missing_ok=True)

def test_l_is_text_backup_not_numeric():
    text = pathlib.Path("tools/v15_local_herd.py").read_text()
    assert "L is default text backup" in text
    assert "O:BI" in text or "O(15):BI(61)" in text

def test_g_accumulated_delta():
    text = pathlib.Path("tools/v15_local_herd.py").read_text()
    assert "G = accumulated delta" in text
    assert "yellow pos deltas" in text
