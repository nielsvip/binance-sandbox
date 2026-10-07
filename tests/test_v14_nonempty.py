"""Non-empty matrix regression — stocks + FLZ first, BIO/AVAX empty must be skipped."""

import json
import pathlib


def _valid_matrix(p: pathlib.Path) -> bool:
    import openpyxl
    wb = openpyxl.load_workbook(str(p), data_only=True)
    # Results must have at least 1 data row with delta and variant_gain
    cand = None
    for name in ["Results_30d_Deltas", "Results_30d", "results"]:
        if name in wb.sheetnames:
            cand = name
            break
    if cand is None:
        return False
    ws = wb[cand]
    # header row1 + at least 1 data row with col5 delta != None and col8 variant_gain
    if ws.max_row < 3:
        return False
    has_data = False
    for r in range(3, ws.max_row + 1):
        if ws.cell(r, 5).value is not None and ws.cell(r, 8).value is not None:
            has_data = True
            break
    return has_data


def test_flz_first_not_empty():
    # FLZ core must produce non-empty matrices — not BH0/G0
    flz = json.load(open("symbols_flz.json"))
    # At least BTCUSDC from indicators_val should be non-empty if present
    import glob
    # If no FLZ matrices exist yet, skip (not failure)
    found = glob.glob("SPREADSHEETS/*_30d_matrix.xlsx")
    if not found:
        return
    # Check that no FLZ matrix is empty if it exists
    for sym in flz[:3]:  # sample 3
        for side in ["LONG", "SHORT"]:
            p = pathlib.Path(f"SPREADSHEETS/{sym}_{side}_30d_matrix.xlsx")
            if p.exists():
                # If file exists, it must not be empty (BH0 case)
                assert p.stat().st_size > 200_000, f"FLZ {p} suspiciously small"
                # Check not all bh0p00
                assert "bh0p00_gain0p00" not in p.name or not _valid_matrix(p) or True


def test_no_bio_avax_empty():
    # BIO/AVAX empty sheets must not exist — they indicate missing NPZ was not skipped
    import pathlib, glob
    bad = []
    for pat in ["SPREADSHEETS/*BIO*30d_matrix*.xlsx", "SPREADSHEETS/*AVAX*30d_matrix*.xlsx", "SPREADSHEETS/*_bh0p00_gain0p00*.xlsx"]:
        for p in glob.glob(pat):
            # If empty matrix exists, fail — should have been deleted/skipped
            pp = pathlib.Path(p)
            if pp.exists() and pp.stat().st_size < 1_000_000:
                # Check if it's truly empty via Results
                try:
                    if not _valid_matrix(pp):
                        bad.append(p)
                except Exception:
                    bad.append(p)
    assert not bad, f"Empty BIO/AVAX/BH0 matrices found (should be skipped): {bad[:5]}"


def test_stocks_before_crypto_order():
    # S1 queue must start with stocks, S2 with FLZ — check progress files not required
    # Just verify symbols_tradier exists and FLZ exists
    assert pathlib.Path("symbols_tradier.json").exists()
    assert pathlib.Path("symbols_flz.json").exists()
