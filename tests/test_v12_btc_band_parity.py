"""BTC/BAND_ARROW parity wiring — red cells 35-41 were all NEG with identical 1.7126 due to stubbed vector path."""
import pathlib

import openpyxl


def test_btc_band_vector_distinct_and_flagged():
    # Vector path for BTC_ACCEL_RAMP / BTC_HARD_BLOCK must be wired (not stub _ = cfg) so AAPL 7d rows 36-38 give distinct gains
    # This test checks that the vector file has distinct F for those switches after fix, and that red flag + MD are created
    # It runs on the already-generated S1 7d matrix if exists, otherwise just checks v12 wiring
    import v12_quick_engine as vqe

    # Check that v12 has real entry_mask logic for those switches (not just _ =)
    src = pathlib.Path("v12_quick_engine.py").read_text()
    assert "BTC_ACCEL_RAMP_REQUIRE_POSITIVE" in src
    assert "BTC_HARD_BLOCK_OTHER_ACCOUNTS" in src
    # After fix, both have `entry_mask = entry_mask & _cond` within 5 lines of the thr fetch
    for sw in ["BTC_ACCEL_RAMP_REQUIRE_POSITIVE", "BTC_HARD_BLOCK_OTHER_ACCOUNTS"]:
        idx = src.find(sw)
        snippet = src[idx : idx + 600]
        assert "entry_mask = entry_mask & _cond" in snippet, f"{sw} not wired with entry_mask"

    # If a 7d matrix exists, check that those rows are distinct (not all 1.7126) and red flags exist for NEG
    p = pathlib.Path("SPREADSHEETS/V15_V16_CELL_BY_CELL/AAPL_LONG_7d_matrix.xlsx")
    if p.exists():
        wb = openpyxl.load_workbook(p, data_only=False)
        ws = wb["ENTRY_REVERSAL_BOUNCE"]
        # rows 36-38 are BTC switches, should be distinct after fix (not all same)
        vals = [ws.cell(r, 6).value for r in [36, 37, 38] if isinstance(ws.cell(r, 6).value, (int, float))]
        # At least 2 distinct before fix they were all 1.7126
        if len(vals) == 3:
            assert len(set(round(v, 4) for v in vals)) >= 2, f"BTC rows still identical {vals}"
        wb.close()

    # Flags MD should exist for any NEG blocking row (dedicated agent)
    flags = pathlib.Path("data/reports/v15_flags/AAPL_LONG_7d_flags.md")
    readme = pathlib.Path("data/reports/v15_flags/README_FIX_RED_CELLS.md")
    assert readme.exists(), "README_FIX_RED_CELLS.md missing"
    # If flags already generated, check header
    if flags.exists():
        assert "Sheet | Row" in flags.read_text()
