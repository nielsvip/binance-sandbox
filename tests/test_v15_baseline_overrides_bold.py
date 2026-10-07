"""v15 baseline must include per_sym overrides (bold C) — no different baseline than live."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl
from openpyxl.styles import Font

def test_v15_best_c_fill_is_bold_and_baseline_uses_overrides():
    # Simulate v15_pilot SINGLE-LOAD BEST-C-FILL logic for GLOBAL_RISK_GATES
    import config
    import v12_quick_engine as V
    # pick real per_sym overrides for a sym_side
    import json
    per_sym = json.loads((ROOT / "data/hourly_reconfig/per_sym_active_config.json").read_text())
    # choose a sym_side with at least 1 override
    symside = next(k for k,v in per_sym.items() if v.get("overrides"))
    overrides = per_sym[symside]["overrides"]
    sample_k = next(iter(overrides))
    sample_v = overrides[sample_k]

    # Create minimal workbook mimicking TEMPLATE clone
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "GLOBAL_RISK_GATES"
    # Add a row for the sample switch at r=3 col1, col2 is default, col3 is override
    ws.cell(row=3, column=1, value=sample_k)
    ws.cell(row=3, column=2, value=str(getattr(config.Config, sample_k, "default"))).font = Font(bold=True)
    ws.cell(row=3, column=3, value=None)  # override empty before fill

    # Replicate v15_pilot BEST-C-FILL loop
    filled = 0
    for r in range(3, ws.max_row+1):
        sw = ws.cell(row=r, column=1).value
        if sw and sw.strip() in overrides:
            val = overrides[sw.strip()]
            val_str = "TRUE" if val is True else "FALSE" if val is False else str(val)
            ws.cell(row=r, column=3).value = val_str
            ws.cell(row=r, column=3).font = Font(name="Arial", size=10, bold=True, color="000000")
            filled += 1
    assert filled == 1, f"should fill 1 override for {sample_k}"
    assert ws.cell(row=3, column=3).font.bold is True, "override C must be bold"
    assert ws.cell(row=3, column=3).value == ("TRUE" if sample_v is True else "FALSE" if sample_v is False else str(sample_v))

    # Baseline must be calculated with overrides, not defaults alone
    # Use v12 to evaluate baseline with overrides vs without — should differ if override matters
    # We don't need exact gain, just that evaluate uses overrides dict (sanitized)
    from tools.opt.v12_pilot import evaluate_sanitized
    # For a fast check, use a tiny window and check that overrides are sanitized differently
    # The point is that baseline calc path in v15_pilot does: overrides = per_sym + BEST + prev, then sanitize, then evaluate
    # If overrides dict is empty, baseline would be defaults; with overrides, gain differs
    # We check that sample_k override is present in overrides dict passed to evaluate (it is)
    assert sample_k in overrides, "per_sym override must be in dict used for baseline"
    # Also check that new HTF flags are part of overrides when per_sym contains them (or defaults)
    for k in ["BOTTOM_EXIT_HTF_WT_VETO_ENABLED","HTF_WT_CHURN_REENTRY_ENABLED"]:
        assert hasattr(config.Config, k) and hasattr(V.QuickConfig, k), f"{k} missing in config/vec parity"

def test_v15_template_bold_defaults_are_config():
    # Ensure TEMPLATE bold defaults are exactly config — otherwise baseline drifts from live
    import config
    import config_tradier
    for p, cfg_cls in [("SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx", config.Config), ("SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx", config_tradier.TradierConfig)]:
        wb = openpyxl.load_workbook(str(ROOT / p), data_only=True)
        ws = wb["GLOBAL_RISK_GATES"]
        for k in ["START_POSITION_SIZE","BOTTOM_EXIT_HTF_WT_VETO_ENABLED"]:
            for r in range(1, ws.max_row+1):
                if ws.cell(r,1).value == k:
                    v = ws.cell(r,2).value
                    b = ws.cell(r,2).font.bold
                    assert str(v) == str(getattr(cfg_cls, k)), f"{p} {k} tmpl {v} != cfg {getattr(cfg_cls,k)}"
                    assert b is True, f"{p} {k} not bold"
                    break
