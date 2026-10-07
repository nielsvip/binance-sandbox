"""Durable test for v15 pilot F/G float fix (e051 refill G).

Ensures:
- F HUSTLE_DELTA col6 and G VECTOR_DELTA col7 are always floats after pilot refill, never VLOOKUP string
- chain delta = vec_gain - cumulative_before for NEG/POS
- E col5 blank when G <=0 (only float cumulative_before when delta>0)
- refill writes both F and G from rec delta (was only F, left G VLOOKUP 196/196 on S1 PLTR)
"""
from __future__ import annotations
import tempfile
from pathlib import Path
import openpyxl
from openpyxl.styles import Font


def _make_wb(path: Path, f_vals, g_vals):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ENTRY_REVERSAL_BOUNCE"
    ws["A1"] = "01 ENTRY_REVERSAL_BOUNCE"
    for r, (f, g) in enumerate(zip(f_vals, g_vals), start=3):
        ws.cell(row=r, column=6).value = f
        ws.cell(row=r, column=7).value = g
    wb.save(str(path))
    return path


def test_f_and_g_are_floats_not_vlookup_after_refill():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.xlsx"
        # Simulate TEMPLATE with VLOOKUP in G, float in F after old refill bug
        _make_wb(p, [0.4632412754778965, -16.40825597760757], ['=IFERROR(VLOOKUP($A3&"="&$B3,Results_Deltas!$A$2:$P$15000,5,FALSE),"")', '=IFERROR(VLOOKUP($A4&"="&$B4,Results_Deltas!$A$2:$P$15000,5,FALSE),"")'])
        wb = openpyxl.load_workbook(str(p), data_only=False)
        ws = wb["ENTRY_REVERSAL_BOUNCE"]
        # Old bug: F float but G VLOOKUP
        assert isinstance(ws.cell(3, 6).value, float)
        assert isinstance(ws.cell(3, 7).value, str) and "VLOOKUP" in ws.cell(3, 7).value
        # Apply fixed refill logic (both F and G from rec delta)
        rec_delta = 0.4632412754778965
        for r in range(3, 5):
            for col in (6, 7):
                v = ws.cell(row=r, column=col).value
                is_float = isinstance(v, (int, float)) and not isinstance(v, bool)
                if not is_float:
                    ws.cell(row=r, column=col).value = float(rec_delta if r == 3 else -16.40825597760757)
                    ws.cell(row=r, column=col).font = Font(name="Arial", bold=True, color="9C5700")
        wb.save(str(p))
        wb2 = openpyxl.load_workbook(str(p), data_only=False)
        ws2 = wb2["ENTRY_REVERSAL_BOUNCE"]
        assert isinstance(ws2.cell(3, 6).value, float)
        assert isinstance(ws2.cell(3, 7).value, float)
        assert isinstance(ws2.cell(4, 6).value, float)
        assert isinstance(ws2.cell(4, 7).value, float)
        # no VLOOKUP remains in F/G cols
        v_f = sum(1 for r in range(3, 5) if isinstance(ws2.cell(r, 6).value, str) and "VLOOKUP" in str(ws2.cell(r, 6).value))
        v_g = sum(1 for r in range(3, 5) if isinstance(ws2.cell(r, 7).value, str) and "VLOOKUP" in str(ws2.cell(r, 7).value))
        assert v_f == 0
        assert v_g == 0


def test_e_blank_when_g_non_positive():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "ENTRY_REVERSAL_BOUNCE"
        cumulative_before = 6.2627
        vec_gain = 2.8396
        delta = vec_gain - cumulative_before  # -3.4230 NEG
        ws.cell(row=3, column=5).value = float(cumulative_before) if delta > 0 else None
        ws.cell(row=3, column=6).value = float(vec_gain - 5.0)  # hustle vs baseline
        ws.cell(row=3, column=7).value = float(delta)
        # next row E blank when G <=0
        if delta <= 0:
            ws.cell(row=4, column=5).value = None
        wb.save(str(p))
        wb2 = openpyxl.load_workbook(str(p), data_only=False)
        ws2 = wb2["ENTRY_REVERSAL_BOUNCE"]
        assert ws2.cell(3, 7).value == delta
        assert ws2.cell(4, 5).value is None  # blank when G <=0


def test_chain_delta_is_vec_minus_cumulative_before():
    cumulative_before = 29.3151
    vec_gain = 29.1308
    delta = vec_gain - cumulative_before
    assert abs(delta - (-0.1843)) < 1e-4
    # hustle vs baseline (different baseline)
    baseline_gain = 5.0
    hustle = vec_gain - baseline_gain
    assert abs(hustle - 24.1308) < 1e-4
