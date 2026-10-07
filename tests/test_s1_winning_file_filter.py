"""
Durable test for S1 winning-file-only filter (bh+gain) vs timestamped interim exclusion.

S1 must NOT send timestamped interim files to MacBook, only winning files with bh and gain.
Winning:  *bh*_gain*_30d_matrix.xlsx  (e.g. 1000BONKUSDC_LONG_bh15p46_gain15p11_30d_matrix.xlsx)
Interim (excluded): *_202609*.xlsx, *_pilot_*.xlsx, *_30d_matrix.xlsx without bh/gain
"""
import fnmatch

def is_winning_file(name: str) -> bool:
    # Must contain both bh and gain and be a 30d matrix, and must NOT be timestamped/pilot
    low = name.lower()
    # Exclude pilot and timestamped interim
    if "pilot" in low:
        return False
    # timestamped interim has _20YYMMDD or _2026 in name before .xlsx (8-digit date)
    # winning files have _bh..._gain..._30d_matrix.xlsx with no 14-digit timestamp
    # Interim also has _30d_matrix_2026*.xlsx
    if "_20" in name and name.count("_20") >= 1:
        # check if after _20 there are 6+ digits before .xlsx (timestamp)
        # winning files have _bh... not _20
        # Simple: if name contains _20260 and ends with .xlsx and has timestamp after _30d_matrix_
        if "_30d_matrix_20" in name:
            return False
        if "_matrix_20" in name:
            return False
    # Must have both bh and gain
    if "bh" not in low or "gain" not in low:
        return False
    if not low.endswith(".xlsx"):
        return False
    if "30d" not in low:
        return False
    return True

def test_winning_files_pass():
    winners = [
        "1000BONKUSDC_LONG_bh15p46_gain15p11_30d_matrix.xlsx",
        "1000FLOKIUSDT_LONG_bh16p45_gain14p51_30d_matrix.xlsx",
        "BTCUSDT_LONG_bh2p10_gain5p00_30d_matrix.xlsx",
        "AAPL_LONG_bh6p45_gain0p63_30d_matrix.xlsx",
    ]
    for f in winners:
        assert is_winning_file(f), f"should be winning: {f}"

def test_interim_timestamped_excluded():
    interim = [
        "1000BONKUSDC_LONG_30d_matrix_20260919210918.xlsx",
        "1000FLOKIUSDT_LONG_30d_matrix_20260919210921.xlsx",
        "ABT_LONG_30d_matrix_20260919210918.xlsx",
        "AAPL_LONG_30d_matrix_20260919210918.xlsx",
    ]
    for f in interim:
        assert not is_winning_file(f), f"should be excluded (timestamped): {f}"

def test_pilot_excluded():
    pilot = [
        "1000LUNCUSDT_SHORT_bh3p15_gain0p73_30d_matrix_pilot_20260918184149.xlsx",
        "ABT_SHORT_30d_matrix_pilot_20260919.xlsx",
        "AAPL_LONG_pilot_20260919.xlsx",
    ]
    for f in pilot:
        assert not is_winning_file(f), f"should be excluded (pilot): {f}"

def test_plain_matrix_without_bh_gain_excluded():
    plain = [
        "1000BONKUSDC_LONG_30d_matrix.xlsx",
        "BTCUSDT_SHORT_30d_matrix.xlsx",
        "TEMPLATE.xlsx",
        "TEMPLATE_STOCKS_LONG.xlsx",
    ]
    for f in plain:
        assert not is_winning_file(f), f"should be excluded (no bh/gain): {f}"

def test_rsync_filter_would_only_send_winning():
    # Simulate rsync include/exclude: only winning files pass
    all_files = [
        "1000BONKUSDC_LONG_bh15p46_gain15p11_30d_matrix.xlsx",
        "1000BONKUSDC_LONG_30d_matrix_20260919210918.xlsx",
        "1000BONKUSDC_LONG_30d_matrix.xlsx",
        "AAPL_LONG_bh6p45_gain0p63_30d_matrix.xlsx",
        "AAPL_LONG_30d_matrix_20260919210918.xlsx",
    ]
    winning = [f for f in all_files if is_winning_file(f)]
    assert winning == [
        "1000BONKUSDC_LONG_bh15p46_gain15p11_30d_matrix.xlsx",
        "AAPL_LONG_bh6p45_gain0p63_30d_matrix.xlsx",
    ]
    assert len(winning) == 2

if __name__ == "__main__":
    test_winning_files_pass()
    test_interim_timestamped_excluded()
    test_pilot_excluded()
    test_plain_matrix_without_bh_gain_excluded()
    test_rsync_filter_would_only_send_winning()
    print("test_s1_winning_file_filter PASSED")
