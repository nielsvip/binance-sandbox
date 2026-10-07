import openpyxl
from pathlib import Path

TEMPLATE_DIR = Path("/Users/niels/Documents/binance/SPREADSHEETS")
TARGETS = ["CRYPTO_SPIKE_FADE_THRESHOLD_PCT","DC_MOMENT_STRONG_THRESHOLD","FG_FEAR_THRESHOLD","FG_GREED_THRESHOLD","HA_WICK_QUALITY_SCORE","MANDATORY_REENTRY_WT_FILTER_MIN_TFS","MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP","MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO","MOVER_THRESHOLD"]
CRYPTO_EXPECTED = {
    "CRYPTO_SPIKE_FADE_THRESHOLD_PCT": ("10", ["10","12.5","7.5","5"]),
    "DC_MOMENT_STRONG_THRESHOLD": ("40", ["40","50","30","20"]),
    "FG_FEAR_THRESHOLD": ("25", ["25","31.25","18.75","12.5"]),
    "FG_GREED_THRESHOLD": ("75", ["75","93.75","56.25","37.5"]),
    "HA_WICK_QUALITY_SCORE": ("15", ["15","10"]),
    "MANDATORY_REENTRY_WT_FILTER_MIN_TFS": ("1", ["1","2","3"]),
    "MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP": ("False", ["False","True"]),
    "MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO": ("0.90", ["0.90","0.80"]),
    "MOVER_THRESHOLD": ("5", ["5","6.25","3.75","2.5"]),
}
SWITCH_SHEETS = ["ENTRY_REVERSAL_BOUNCE","STDEV_SLOPE_SIZING","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES","EXIT_STRUCTURAL","EXIT_VELOCITY","REENTRY_WINDOWED","REENTRY_ADAPTIVE","AUGMENT_TREND","AUGMENT_RISK_SIZING","REDUCE_PROFIT_LOCK","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES"]
# Only 5 sheets had those placeholders, but check all
PLACEMENT_SHEETS = ["ENTRY_REVERSAL_BOUNCE","ENTRY_BREAKOUT_CHANNEL","REENTRY_WINDOWED","REENTRY_ADAPTIVE","AUGMENT_RISK_SIZING"]

def _load(path):
    return openpyxl.load_workbook(str(path), data_only=False)

def test_no_blanket():
    for name in ["TEMPLATE_CRYPTO_LONG.xlsx","TEMPLATE_CRYPTO_SHORT.xlsx","TEMPLATE_STOCKS_LONG.xlsx","TEMPLATE_STOCKS_SHORT.xlsx"]:
        wb = _load(TEMPLATE_DIR / name)
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    assert not (isinstance(c.value, str) and "Blanket" in c.value), f"{name} {ws.title} {c.coordinate} still has Blanket: {c.value!r}"

def test_no_formulas():
    for name in ["TEMPLATE_CRYPTO_LONG.xlsx","TEMPLATE_CRYPTO_SHORT.xlsx","TEMPLATE_STOCKS_LONG.xlsx","TEMPLATE_STOCKS_SHORT.xlsx"]:
        wb = _load(TEMPLATE_DIR / name)
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    assert c.data_type != 'f', f"{name} {ws.title} {c.coordinate} still has formula data_type f"
                    assert not (isinstance(c.value, str) and c.value.startswith("=")), f"{name} {ws.title} {c.coordinate} still has = {c.value!r}"

def test_alignment_left():
    for name in ["TEMPLATE_CRYPTO_LONG.xlsx","TEMPLATE_CRYPTO_SHORT.xlsx","TEMPLATE_STOCKS_LONG.xlsx","TEMPLATE_STOCKS_SHORT.xlsx"]:
        wb = _load(TEMPLATE_DIR / name)
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if c.value is not None:
                        assert c.alignment.horizontal == 'left', f"{name} {ws.title} {c.coordinate} not left: {c.alignment.horizontal}"

def test_crypto_targets_expanded():
    for name in ["TEMPLATE_CRYPTO_LONG.xlsx","TEMPLATE_CRYPTO_SHORT.xlsx"]:
        wb = _load(TEMPLATE_DIR / name)
        for sheet in PLACEMENT_SHEETS:
            ws = wb[sheet]
            for target in TARGETS:
                rows = []
                for r in range(3, ws.max_row+1):
                    if ws.cell(row=r, column=1).value == target:
                        # only count base rows: they have L=YES/NO and are not yellow filter rows (yellow fill FFFE699)
                        is_def = ws.cell(row=r, column=12).value
                        if is_def not in ("YES","NO"):
                            continue
                        fill = ws.cell(row=r, column=1).fill.fgColor.rgb if ws.cell(row=r, column=1).fill and ws.cell(row=r, column=1).fill.fgColor and ws.cell(row=r, column=1).fill.fgColor.rgb != "00000000" else "white"
                        # yellow filter rows have fill FFFE699 / FFFFEB9C etc, base rows are white (00000000)
                        if fill not in ("00000000", "white", "none", None):
                            # check if yellow
                            if "FFE" in str(fill) or "FFFE" in str(fill):
                                continue
                        rows.append((r, str(ws.cell(row=r, column=2).value), ws.cell(row=r, column=1).font.bold, is_def))
                if not rows:
                    continue
                expected_default, expected_options = CRYPTO_EXPECTED[target]
                # must have same count as expected_options
                assert len(rows) == len(expected_options), f"{name} {sheet} {target} expected {len(expected_options)} rows got {len(rows)}: {rows}"
                # check values match options set
                vals = [v for _, v, _, _ in rows]
                assert set(vals) == set(expected_options), f"{name} {sheet} {target} vals {vals} vs expected {expected_options}"
                # only default bold and YES
                for r, v, bold, is_def in rows:
                    if v == expected_default:
                        assert bold is True, f"{name} {sheet} {target} default {v} should be bold"
                        assert is_def == "YES", f"{name} {sheet} {target} default should have YES in col L"
                    else:
                        assert bold is not True, f"{name} {sheet} {target} alt {v} should not be bold"
                        assert is_def == "NO", f"{name} {sheet} {target} alt {v} should have NO in col L"

def test_stock_targets_removed():
    for name in ["TEMPLATE_STOCKS_LONG.xlsx","TEMPLATE_STOCKS_SHORT.xlsx"]:
        wb = _load(TEMPLATE_DIR / name)
        for ws in wb.worksheets:
            if ws.title in ("LEGEND_FILTERS","INSTRUCTIONS","FILTERS_EXPLAINED","FILTER_DICTIONARY_V2","Results_Deltas","Results_30d_Deltas","INSTRUCTIONS_V2"):
                continue
            for r in range(3, ws.max_row+1):
                a = ws.cell(row=r, column=1).value
                assert a not in TARGETS, f"{name} {ws.title} R{r} still has {a} should be removed for stocks"

def test_no_zero_defaults_for_targets_crypto():
    for name in ["TEMPLATE_CRYPTO_LONG.xlsx","TEMPLATE_CRYPTO_SHORT.xlsx"]:
        wb = _load(TEMPLATE_DIR / name)
        for ws in wb.worksheets:
            if ws.title in ("LEGEND_FILTERS","INSTRUCTIONS","FILTERS_EXPLAINED","FILTER_DICTIONARY_V2","Results_Deltas","Results_30d_Deltas","INSTRUCTIONS_V2"):
                continue
            for r in range(3, ws.max_row+1):
                a = ws.cell(row=r, column=1).value
                b = ws.cell(row=r, column=2).value
                if a in TARGETS:
                    is_zero = (b == 0 or b == "0" or (isinstance(b, str) and b.strip() == "0"))
                    assert not is_zero, f"{name} {ws.title} R{r} {a} still has 0"

def test_results_deltas_no_hustle():
    for name in ["TEMPLATE_CRYPTO_LONG.xlsx","TEMPLATE_CRYPTO_SHORT.xlsx","TEMPLATE_STOCKS_LONG.xlsx","TEMPLATE_STOCKS_SHORT.xlsx"]:
        wb = _load(TEMPLATE_DIR / name)
        for sheet in ["Results_Deltas","Results_30d_Deltas"]:
            ws = wb[sheet]
            for r in range(1, ws.max_row+1):
                for c in range(1, ws.max_column+1):
                    v = ws.cell(row=r, column=c).value
                    assert not (isinstance(v, str) and v.strip() == "HUSTLE_DELTA"), f"{name} {sheet} {ws.cell(row=r, column=c).coordinate} still has HUSTLE_DELTA"

def test_results_deltas_headers_blue():
    for name in ["TEMPLATE_CRYPTO_LONG.xlsx","TEMPLATE_CRYPTO_SHORT.xlsx","TEMPLATE_STOCKS_LONG.xlsx","TEMPLATE_STOCKS_SHORT.xlsx"]:
        wb = _load(TEMPLATE_DIR / name)
        for sheet in ["Results_Deltas","Results_30d_Deltas"]:
            ws = wb[sheet]
            for c in range(1, ws.max_column+1):
                hdr = ws.cell(row=1, column=c)
                if hdr.value is None:
                    continue
                rgb = hdr.fill.fgColor.rgb if hdr.fill and hdr.fill.fgColor and hdr.fill.fgColor.rgb != "00000000" else None
                assert rgb == "FFD9E1F2", f"{name} {sheet} {hdr.coordinate} header fill not blue: {rgb} val={hdr.value!r}"
                assert hdr.font.bold is True, f"{name} {sheet} {hdr.coordinate} header not bold"
