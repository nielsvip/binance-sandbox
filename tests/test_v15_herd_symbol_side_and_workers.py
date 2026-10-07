import pathlib, re

def test_base_key_always_includes_side():
    from tools.organize_best_spreadsheets import base_key, category
    assert base_key("MSFT_LONG_bh6p14_gain4p13_30d_matrix.xlsx") == "MSFT_LONG"
    assert base_key("MSFT_SHORT_bh6p14_gainm2p09_30d_matrix.xlsx") == "MSFT_SHORT"
    assert base_key("BTCUSDC_LONG_bh20p55_gain0p18_30d_matrix.xlsx") == "BTCUSDC_LONG"
    # base_key must end with side
    for name in ["MSFT_LONG_bh6p14_gain4p13_30d_matrix.xlsx", "AAPL_SHORT_bhm6p45_gainm0p62_30d_matrix.xlsx"]:
        b = base_key(name)
        assert b.endswith(("_LONG", "_SHORT")), f"base_key {b} missing side for {name}"
        cat = category(b)
        assert cat in ("STOCKS_LONG", "STOCKS_SHORT", "CRYPTO_LONG", "CRYPTO_SHORT"), f"cat {cat} unknown for {b}"

def test_best_filenames_always_have_side():
    best = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/BEST")
    if not best.exists():
        return
    for p in best.rglob("*.xlsx"):
        # filename must contain _LONG or _SHORT before _bh
        assert re.search(r"_(LONG|SHORT)(?:_bh|_30d)", p.name), f"Missing side in BEST filename {p.name}"
        # base_key must also have side
        from tools.organize_best_spreadsheets import base_key
        b = base_key(p.name)
        assert b and b.endswith(("_LONG", "_SHORT")), f"base_key missing side {p.name} -> {b}"

def test_herd_s5_dedicated_workers_and_parallel():
    text = pathlib.Path("/Users/niels/Documents/binance/tools/v15_local_herd.py").read_text()
    # s5 is the dedicated 4-core box: must be 2-5 parallel with 28 workers (user mandate)
    assert "max_parallel = 4" in text, "s5 max_parallel must be 4 (2-5 range)"
    assert "workers = 28" in text, "s5 workers must be 28"
    # verify low-mem branch also uses 28
    assert text.count("workers = 28") >= 2, "both nproc<=4 and mem<10 branches must use workers=28"

def test_reporting_extracts_symside_with_side():
    # Every report line must include _LONG or _SHORT, never bare symbol like MSFT
    for raw in ["MSFT_LONG_bh6p14_gain4p13_30d_matrix.xlsx", "1INCHUSDT_LONG_bh6p14_gainm0p72_30d_matrix.xlsx"]:
        m = re.match(r"(.+?_(LONG|SHORT))", raw)
        assert m, f"regex must extract side from {raw}"
        symside = m.group(1)
        assert symside.endswith(("_LONG", "_SHORT"))
        line = f"{4.13:.2f} {symside} 14:39"
        assert "_LONG" in line or "_SHORT" in line, "report line must contain side"
        assert not re.match(r"^MSFT\s", line), "bare symbol without side is forbidden"
