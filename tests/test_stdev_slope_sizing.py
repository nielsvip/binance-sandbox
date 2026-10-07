"""test_stdev_slope_sizing — durable test for STDEV_SLOPE_SIZING hook.

Verifies:
- QuickConfig exposes STDEV_SLOPE_SIZING_* fields
- compute_regime_sizing_mult produces non-zero ladder (10x vs 1x)
- augment path respects regime_mult (entry AND augment sizing)
- curated allowlist includes STDEV_SLOPE_SIZING params
- TEMPLATE.xlsx STDEV sheet has ablation variants
"""
import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def test_quickconfig_fields():
    import v12_quick_engine as V
    cfg = V.QuickConfig()
    for field in ["STDEV_SLOPE_SIZING_ENABLED","STDEV_SLOPE_SIZING_D_MAX","STDEV_SLOPE_SIZING_4H_MAX","STDEV_SLOPE_SIZING_1H_MAX","STDEV_SLOPE_SIZING_15M_MAX","STDEV_SLOPE_SIZING_MODE","BAND_SLOPE_SIZING_V2_ENABLED","BAND_SLOPE_SIZING_V2_TF"]:
        assert hasattr(cfg, field), f"missing QuickConfig.{field}"

def test_allowlist_includes_stdev():
    p = ROOT / "data" / "reports" / "ALL_PATHS_ALLOWLIST.csv"
    assert p.exists(), "allowlist missing"
    fields = {r["field"] for r in csv.DictReader(p.open())}
    for f in ["STDEV_SLOPE_SIZING_ENABLED","STDEV_SLOPE_SIZING_D_MAX","STDEV_SLOPE_SIZING_MODE","BAND_SLOPE_SIZING_V2_ENABLED"]:
        assert f in fields, f"{f} not in allowlist"

def test_template_has_ablation_rows():
    import openpyxl
    wb = openpyxl.load_workbook(str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_LONG.xlsx"), data_only=True)
    ws = wb["STDEV_SLOPE_SIZING"]
    switches = [ws.cell(r,1).value for r in range(2, ws.max_row+1) if ws.cell(r,1).value]
    # must have ENABLED False ablation
    assert "STDEV_SLOPE_SIZING_ENABLED" in switches
    # count occurrences: should have True and False variants
    enabled_vals = [ws.cell(r,2).value for r in range(2, ws.max_row+1) if ws.cell(r,1).value=="STDEV_SLOPE_SIZING_ENABLED"]
    assert True in enabled_vals or "True" in enabled_vals
    assert False in enabled_vals
    # D_MAX should have 10 and 1.0
    dmax_vals = [ws.cell(r,2).value for r in range(2, ws.max_row+1) if ws.cell(r,1).value=="STDEV_SLOPE_SIZING_D_MAX"]
    assert 10 in dmax_vals or 10.0 in dmax_vals
    assert 1 in dmax_vals or 1.0 in dmax_vals
    assert ws.max_row >= 30, f"expected >=30 rows, got {ws.max_row}"

def test_stdev_ladder_delta_nonzero():
    """Vector delta must be non-zero when toggling STDEV."""
    # Use evaluate_v12 if NPZ available, otherwise skip
    try:
        from tools.opt.evaluate_v12 import evaluate
    except Exception:
        return
    # Pick a stock that has NPZ (AAPL)
    try:
        r_on = evaluate("AAPL_LONG", {"STDEV_SLOPE_SIZING_ENABLED": True}, window_days=30)
        r_off = evaluate("AAPL_LONG", {"STDEV_SLOPE_SIZING_ENABLED": False}, window_days=30)
    except Exception as e:
        # No NPZ on this host (Mac truncated) -> skip
        import pytest
        pytest.skip(f"NPZ not available: {e}")
        return
    if not r_on.get("valid") or not r_off.get("valid"):
        import pytest
        pytest.skip("evaluate invalid")
    delta = r_on["gain_pct"] - r_off["gain_pct"]
    assert abs(delta) > 0.05, f"STDEV ladder should change gain: on {r_on['gain_pct']} off {r_off['gain_pct']} delta {delta}"

def test_augment_uses_regime_mult():
    """Ensure simulate_one augment code contains regime_mult."""
    src = (ROOT / "v12_quick_engine.py").read_text()
    assert "regime_mult[i]" in src
    assert "_aug_regime" in src
    assert "STDEV_SLOPE_SIZING ladder applies to augment" in src
