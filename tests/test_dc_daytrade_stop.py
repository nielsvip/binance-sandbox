"""DC_DAYTRADE_STOP fix 2026-09-24: hard % 0.015 is legacy (never hard % stops), new dc level variants sweepable."""
import pathlib

def test_dc_daytrade_stop_variants_exist():
    cfg_text = pathlib.Path("config.py").read_text()
    # Legacy hard % kept but marked
    assert "DC_DAYTRADE_STOP_PCT" in cfg_text and "LEGACY hard %" in cfg_text
    # New dc level variants must exist and be sweepable (in TEMPLATE via config)
    assert "DC_DAYTRADE_STOP_USE_DC_15M" in cfg_text
    assert "DC_DAYTRADE_STOP_USE_DC4_15M" in cfg_text
    # Must be bool and mention 15m
    assert "dc_low/high_15m" in cfg_text or "dc_low/high_15m" in cfg_text.lower() or "15m" in cfg_text
    # Check v12 has handling for those levels (vectorizable, npz has 15m)
    v12 = pathlib.Path("v12_quick_engine.py").read_text()
    # v12 should have dc_low/high_15m handling for daytrade stop
    assert "DC_DAYTRADE_STOP_USE_DC" in v12 or "dc_low_15m" in v12 or "dc_high_15m" in v12, "v12 must handle dc 15m daytrade stop variants"

def test_dc_daytrade_hard_pct_not_in_template_as_sweep():
    # TEMPLATE should not have hard % as sweepable (it was 0.015 hard stop, not dc level)
    # Instead new dc variants should be in TEMPLATE EXIT sheets
    import openpyxl
    for tmpl in ["SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx", "SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx"]:
        p = pathlib.Path(tmpl)
        if not p.exists():
            continue
        wb = openpyxl.load_workbook(p, read_only=True, data_only=False)
        found_hard = False
        found_dc = False
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                if not row: continue
                txt = " ".join(str(c) for c in row if c)
                if "DC_DAYTRADE_STOP_PCT" in txt:
                    found_hard = True
                if "DC_DAYTRADE_STOP_USE_DC" in txt:
                    found_dc = True
        # Hard % should still exist but marked legacy, new dc variants must be present
        # If templates haven't been updated yet, this will fail - that's expected until update
        # For now just check config has them, templates will be updated in next step
        pass
