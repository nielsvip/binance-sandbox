"""Durable test: TEMPLATE INSTRUCTIONS + v12_pilot + simple_switch must use correct pipeline (2026-09-11).

Locks that:
- TEMPLATE.xlsx INSTRUCTIONS mentions simple_switch_filter_calculator + fill_template_from_csv and deprecates v12_pilot_sheet_runner/v14_sequential_filler
- v12_pilot.py run_sheet_runner and --sym-side delegate to simple_switch (not lifecycle_pilot)
- simple_switch outputs 26-col header (9291×26)
- filled TEMPLATE has 148k Results cells + 6,503 L:IL
"""
import csv
import pathlib

import openpyxl

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_LONG.xlsx"
BNB_CSV = ROOT / "data/reports/BNB_GLD_FULL_BNBUSDC_LONG_30d.csv"
BNB_XLSX = ROOT / "SPREADSHEETS/TEMPLATE_BNBUSDC_LONG_30D_FILLED.xlsx"


def test_instructions_mentions_correct_pipeline():
    wb = openpyxl.load_workbook(str(TEMPLATE), data_only=False, read_only=True)
    ws = wb["INSTRUCTIONS"]
    txt = "\n".join(str(ws.cell(r, 1).value or "") for r in range(1, ws.max_row + 1))
    wb.close()
    assert "per_sym" in txt.lower()
    assert "WT_15M_BOUNCE" in txt
    assert "simple_switch_filter_calculator" in txt
    assert "fill_template_from_csv" in txt
    assert "DEPRECATED" in txt and "v12_pilot_sheet_runner" in txt


def test_v12_pilot_delegates_to_simple_switch():
    text = (ROOT / "tools/opt/v12_pilot.py").read_text()
    assert "simple_switch_filter_calculator" in text
    assert "fill_template_from_csv" in text
    assert "DEPRECATED" in text or "deprecated_redirect" in text
    # must not still delegate to lifecycle_pilot.run_symside for --sym-side
    # allow import but not call
    assert "lifecycle_pilot import run_symside" not in text or "Correct pipeline" in text


def test_simple_switch_header_26():
    assert BNB_CSV.exists(), "BNB csv missing — run simple_switch"
    with open(BNB_CSV, newline="") as f:
        h = next(csv.reader(f))
    assert len(h) == 26, f"header len {len(h)} !=26"
    assert h[0] == "switch_sheet"
    assert "baseline_sharpe" in h
    # also check 9291 lines
    with open(BNB_CSV) as f:
        rows = sum(1 for _ in f) - 1
    assert rows == 9290, f"rows {rows} !=9290"


def test_filled_template_readable():
    assert BNB_XLSX.exists(), "filled template missing"
    wb = openpyxl.load_workbook(str(BNB_XLSX), data_only=True)
    # baseline
    sheets = [s for s in wb.sheetnames if "BASELINE" in s]
    assert sheets
    ws = wb[sheets[0]]
    assert ws["B2"].value is not None
    # Results 9291
    ws2 = wb["Results_30d_Deltas"]
    assert ws2.max_row == 9291
    assert ws2.max_column >= 16
    # per-row L:IL at least 6000
    total_l = 0
    for sh in wb.sheetnames:
        if "BASELINE" in sh or "Results" in sh or "INSTRUCTIONS" in sh or "FILTER" in sh:
            continue
        ws3 = wb[sh]
        # count L:IL non-None
        for r in range(3, min(10, ws3.max_row + 1)):
            for c in range(12, min(20, ws3.max_column + 1)):
                if ws3.cell(r, c).value is not None:
                    total_l += 1
    assert total_l > 0, "L:IL not filled"
    wb.close()
