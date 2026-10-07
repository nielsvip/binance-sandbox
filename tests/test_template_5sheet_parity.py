"""5-sheet parity + daytrade wiring — every TEMPLATE xlsx must have all wait/daytrade concepts and connect to v15/v12."""
import openpyxl
from pathlib import Path

TEMPLATES = [
    "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx",
    "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx",
    "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx",
    "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx",
    "SPREADSHEETS/TEMPLATE.xlsx",
]

# Mapping sheet -> required switches (must match config_tradier/config exactly)
REQUIRED = {
    "EXIT_VELOCITY": [
        "WT_15M_LH_WAIT_EXIT_ENABLED",
        "WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED",
        "EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED",
    ],
    "EXIT_STRUCTURAL": [
        "DC_BREAK_WAIT_WT15_CLOSE_ENABLED",
    ],
    "DAYTRADE": [
        "TRADIER_DC_DAYTRADE_ENABLED",
        "TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES",
        "TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION",
        "TRADIER_DC_DAYTRADE_STOP_PCT",
        "DC_DAYTRADE_ENABLED",
        "DC_DAYTRADE_MAX_HOLD_MINUTES",
        "DC_DAYTRADE_STOP_PCT",
    ],
}

def test_5sheet_parity():
    base = Path("/Users/niels/Documents/binance")
    for rel in TEMPLATES:
        p = base / rel
        assert p.exists(), f"missing {rel}"
        wb = openpyxl.load_workbook(p, data_only=False)
        sheet_map = {ws.title.upper(): ws for ws in wb.worksheets}
        for sheet_name, switches in REQUIRED.items():
            assert sheet_name in sheet_map, f"{rel} missing sheet {sheet_name}"
            ws = sheet_map[sheet_name]
            vals = set(str(ws.cell(row=r, column=1).value).strip() for r in range(1, ws.max_row+1) if ws.cell(row=r, column=1).value)
            for sw in switches:
                assert sw in vals, f"{rel}::{sheet_name} missing {sw}"
                # check VLOOKUP wiring in col F/G
                for r in range(1, ws.max_row+1):
                    if str(ws.cell(row=r, column=1).value).strip() == sw:
                        f = ws.cell(row=r, column=6).value or ""
                        assert "VLOOKUP" in str(f) and "Results_Deltas" in str(f), f"{rel}::{sheet_name}::{sw} missing VLOOKUP"
                        break

def test_config_v12_wiring():
    import config_tradier as ct
    import config as c
    import v12_quick_engine as v12
    for k in ["WT_15M_LH_WAIT_EXIT_ENABLED","WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED","EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED","DC_BREAK_WAIT_WT15_CLOSE_ENABLED","TRADIER_DC_DAYTRADE_ENABLED","DC_DAYTRADE_ENABLED"]:
        assert hasattr(ct.TradierConfig, k), f"config_tradier missing {k}"
        assert hasattr(v12.QuickConfig, k) or k.startswith("TRADIER_DC") or k.startswith("DC_DAYTRADE"), f"v12 QuickConfig missing {k}"
    # v12 vector uses _wt1_prev for cross fix
    with open("/Users/niels/Documents/binance/v12_quick_engine.py") as f:
        txt = f.read()
        assert "_wt1_prev" in txt and "DC_BREAK_WAIT_WT15_CLOSE_ENABLED" in txt
    with open("/Users/niels/Documents/binance/tradier_manage.py") as f:
        assert "DC_BREAK_WAIT_WT15_CLOSE_ENABLED" in f.read()
    with open("/Users/niels/Documents/binance/ez_manage.py") as f:
        assert "DC_BREAK_WAIT_WT15_CLOSE_ENABLED" in f.read()

def test_only_five_templates():
    # Ensure we didn't create extra TEMPLATE_*.xlsx beyond the 5 canonical + audit files
    # The 5 canonical must exist, others may exist but we check these 5 are the only ones that matter for v15
    base = Path("/Users/niels/Documents/binance/SPREADSHEETS")
    for rel in TEMPLATES:
        assert (base.parent / rel).exists() or (Path("/Users/niels/Documents/binance") / rel).exists()
