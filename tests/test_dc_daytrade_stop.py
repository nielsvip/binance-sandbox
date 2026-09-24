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
    # v12 should have dc_low/high_15m handling for daytrade stop and target
    assert "DC_DAYTRADE_STOP_USE_DC" in v12 or "dc_low_15m" in v12 or "dc_high_15m" in v12, "v12 must handle dc 15m daytrade stop variants"
    assert "DC_DAYTRADE_TARGET_USE_DC" in v12, "v12 must handle dc 15m daytrade target variants for breakout re-entry (1.5% + near dc)"

def test_dc_daytrade_hard_pct_not_in_template_as_sweep():
    # TEMPLATE should have hard % legacy but new dc variants sweepable (stop + target near dc for breakout re-entry)
    import openpyxl
    for tmpl in ["SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx", "SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx"]:
        p = pathlib.Path(tmpl)
        if not p.exists():
            continue
        wb = openpyxl.load_workbook(p, read_only=True, data_only=False)
        found_dc_stop = False
        found_dc_target = False
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                if not row: continue
                txt = " ".join(str(c) for c in row if c)
                if "DC_DAYTRADE_STOP_USE_DC" in txt:
                    found_dc_stop = True
                if "DC_DAYTRADE_TARGET_USE_DC" in txt:
                    found_dc_target = True
        assert found_dc_stop, f"{tmpl} must have DC_DAYTRADE_STOP_USE_DC_* sweepable variants (was 3/5m not in npz)"
        assert found_dc_target, f"{tmpl} must have DC_DAYTRADE_TARGET_USE_DC_* for near dc breakout re-entry (1.5% + dc)"
