#!/usr/bin/env python3
import sys as _s; _s.exit("REFUSED (USER 2026-10-01): this script can move/rewrite TEMPLATE cells and once moved cells without their row. Templates are changed ONLY by tools/v15_daily_template_update.py / tools/v15_template_restructure_v2.py (whole-row moves, tools/template_row_guard.py verified).")
"""
Fix TEMPLATE_*.xlsx per user rules:
- NO BLANK ROW BELOW ORANGE ROW
- ONE FONT ONE SIZE (Calibri 11)
- CELL COLORS NEVER CHANGE AND MOVE WITH ROW
- NO BOLD UNLESS DEFAULT FOR THAT CAT/SIDE (exactly ONE per switch)
- EVERY SWITCH HAS DEFAULT BOLD (defined in config.py / config_tradier.py)

Run on MacBook, fixes all 4 templates in place.
"""
import pathlib, re, ast, openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from collections import defaultdict

BASE = pathlib.Path("/Users/niels/Documents/binance")
TEMPLATES = {
    "CRYPTO_LONG": BASE / "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx",
    "CRYPTO_SHORT": BASE / "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx",
    "STOCKS_LONG": BASE / "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx",
    "STOCKS_SHORT": BASE / "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx",
}
CONFIG_CRYPTO = BASE / "config.py"
CONFIG_STOCKS = BASE / "config_tradier.py"

# --- Parse config defaults ---
def parse_defaults(path):
    defaults = {}
    text = path.read_text()
    # Find assignments like NAME: type = value or NAME = value
    # We need to handle simple literals
    for m in re.finditer(r'^\s*([A-Z0-9_]+)\s*[:=][^=]*=\s*([^\n#]+)', text, re.MULTILINE):
        name = m.group(1)
        val_str = m.group(2).strip()
        # Remove trailing comment
        val_str = val_str.split('#')[0].strip()
        # Try to parse via ast
        try:
            val = ast.literal_eval(val_str)
            defaults[name] = val
        except:
            # Ignore non-literals
            pass
    return defaults

crypto_defaults = parse_defaults(CONFIG_CRYPTO)
stocks_defaults = parse_defaults(CONFIG_STOCKS)
print(f"Parsed crypto defaults {len(crypto_defaults)}, stocks {len(stocks_defaults)}")
# Show some switch defaults
for k in ["WT_15M_BOUNCE_OPEN_ENABLED","BB_SQUEEZE_WIDTH_PERCENTILE","BB_SQUEEZE_ENABLED","DELTA_GATE_BB_SQUEEZE","WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED"]:
    print(f" crypto {k}={crypto_defaults.get(k, 'MISSING')} stocks {k}={stocks_defaults.get(k, 'MISSING')}")

# Define category to defaults map
CAT_DEFAULTS = {
    "CRYPTO_LONG": crypto_defaults,
    "CRYPTO_SHORT": crypto_defaults,
    "STOCKS_LONG": stocks_defaults,
    "STOCKS_SHORT": stocks_defaults,
}

# Target single font/size
TARGET_FONT_NAME = "Calibri"
TARGET_FONT_SIZE = 11

# Orange fill RGBs to detect orange rows
ORANGE_RGBS = {"FFFFC000","FFED7D31","FFFFA500","FFFF6600","FFC000","FFFFC000"}

def is_orange_cell(cell):
    if not cell.fill or not cell.fill.start_color or not cell.fill.start_color.rgb:
        return False
    rgb = str(cell.fill.start_color.rgb).upper()
    return rgb in ORANGE_RGBS or "C000" in rgb or "ED7D31" in rgb or "A500" in rgb

def fmt_val_for_compare(v):
    # Normalize for comparison with config default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        if v.lower() in ("true","false"):
            return v.lower() == "true"
        try:
            if "." in v:
                return float(v)
            return int(v)
        except:
            return v
    return v

for cat, path in TEMPLATES.items():
    print(f"\n=== Fixing {cat} {path.name} ===")
    wb = openpyxl.load_workbook(path)
    defaults = CAT_DEFAULTS[cat]

    for sheet_name in wb.sheetnames:
        if sheet_name in ("LEGEND_FILTERS","INSTRUCTIONS","FILTERS_EXPLAINED","INSTRUCTIONS_V2","FILTER_DICTIONARY_V2","12SYM_PARITY","FORMULAS","ABLATION_LIVE_EXTRA"):
            continue
        if "BASELINE" in sheet_name:
            continue
        ws = wb[sheet_name]
        # --- 1) Fix font to single --- #
        for row in ws.iter_rows():
            for cell in row:
                # Keep bold status to be fixed later, but set font name/size to single
                is_bold = cell.font.bold if cell.font else False
                cell.font = Font(name=TARGET_FONT_NAME, size=TARGET_FONT_SIZE, bold=is_bold, color=cell.font.color if cell.font and cell.font.color else None)
                # Also ensure alignment and border not messed
        # --- 2) No blank row below orange row: find orange rows, delete blank rows below --- #
        orange_rows = []
        for r in range(1, ws.max_row+1):
            for c in range(1, min(10, ws.max_column+1)):
                if is_orange_cell(ws.cell(row=r, column=c)):
                    orange_rows.append(r)
                    break
        if orange_rows:
            max_orange = max(orange_rows)
            # Find contiguous blank rows starting at max_orange+1
            rows_to_delete = []
            for r in range(max_orange+1, ws.max_row+1):
                vals = [ws.cell(row=r, column=c).value for c in range(1, 4)]
                if all(v is None or str(v).strip()=="" for v in vals):
                    # Check if entire row is blank (check up to 10 cols)
                    all_blank = all(ws.cell(row=r, column=c).value is None for c in range(1, ws.max_column+1))
                    # Also check if row has any fill that would indicate it's not blank but just empty
                    has_fill = any(ws.cell(row=r, column=c).fill.start_color.rgb not in (None,"00000000","FFFFFFFF") for c in range(1,5))
                    if all_blank and not has_fill:
                        rows_to_delete.append(r)
                    else:
                        # First non-blank ends the contiguous blank block below orange; stop
                        break
                else:
                    break
            if rows_to_delete:
                print(f" {sheet}: deleting {len(rows_to_delete)} blank rows below orange {max_orange}: {rows_to_delete[:5]}")
                for r in reversed(rows_to_delete):
                    ws.delete_rows(r, 1)
        # --- 3) Fix bold: exactly ONE per switch, must match config default --- #
        # Group rows by switch name (col A)
        switch_rows = defaultdict(list)
        for r in range(3, ws.max_row+1):
            a = ws.cell(row=r, column=1).value
            b = ws.cell(row=r, column=2).value
            if a and str(a).strip() and b is not None and str(b).strip()!="":
                sw = str(a).strip()
                switch_rows[sw].append(r)
        for sw, rows in switch_rows.items():
            # Find default value for this switch in config
            # Switch name in template may be like WT_15M_BOUNCE_OPEN_ENABLED, BB_SQUEEZE_WIDTH_PERCENTILE etc.
            # Config name is same
            default_val = defaults.get(sw, None)
            # If not found, try to find via prefix
            if default_val is None:
                # Try to handle alias like WT_15M_BOUNCE_LOW_1H_GT_PREV etc. which may be aliases
                # Keep as is: if no default found, bold the first row (existing behavior) but log
                pass
            # Determine which row should be bold (exactly one)
            target_row = None
            if default_val is not None:
                for r in rows:
                    b = ws.cell(row=r, column=2).value
                    # Compare b to default_val
                    # Normalize b
                    if isinstance(default_val, bool):
                        # Template B may be string "True"/"False" or bool
                        if isinstance(b, bool) and b == default_val:
                            target_row = r
                            break
                        if isinstance(b, str) and b.lower() == str(default_val).lower():
                            target_row = r
                            break
                    elif isinstance(default_val, (int,float)):
                        try:
                            bv = float(str(b).strip()) if isinstance(b, str) else float(b)
                            if abs(bv - float(default_val)) < 1e-9:
                                target_row = r
                                break
                        except:
                            pass
                    elif isinstance(default_val, str):
                        if str(b).strip() == str(default_val).strip():
                            target_row = r
                            break
                # If default not found among rows, keep first row as bold and warn
                if target_row is None:
                    print(f" {ws.title} {sw}: default {default_val!r} not found among rows {[ws.cell(row=r, column=2).value for r in rows]}")
                    target_row = rows[0]
            else:
                # No config default, keep existing bold if exactly one, else first
                # Find existing bold
                existing_bold = [r for r in rows if ws.cell(row=r, column=2).font.bold]
                if len(existing_bold)==1:
                    target_row = existing_bold[0]
                else:
                    target_row = rows[0]
            # Now set bold: target_row bold=True, others False, keep font name/size
            for r in rows:
                cell = ws.cell(row=r, column=2)
                is_bold = (r == target_row)
                # Preserve color if any, but set correct bold and single font
                old_color = cell.font.color
                cell.font = Font(name=TARGET_FONT_NAME, size=TARGET_FONT_SIZE, bold=is_bold, color=old_color)
                # Also ensure column A's font is not bold (only B should be bold)
                cell_a = ws.cell(row=r, column=1)
                cell_a.font = Font(name=TARGET_FONT_NAME, size=TARGET_FONT_SIZE, bold=False, color=cell_a.font.color if cell_a.font and cell_a.font.color else None)
            # Log if we fixed
            if len(rows)>1:
                # Check that we now have exactly one bold
                bolds = sum(1 for r in rows if ws.cell(row=r, column=2).font.bold)
                if bolds != 1:
                    print(f"  ERROR {sheet} {sw} bolds {bolds} !=1")

        # --- 4) Cell colors: ensure they move with row (they already do via per-cell fill, but we need to ensure no conditional formatting that doesn't move)
        # Remove any conditional formatting that could cause colors not to move (just clear)
        if ws.conditional_formatting:
            # Keep it but log
            print(f" {sheet}: has {len(ws.conditional_formatting)} conditional formats (will keep but colors are per-cell)")

    # Save
    _aws_badzip(wb, path)  # BADZIP FIX 2026-09-29
    print(f" Saved {path} ({path.stat().st_size/1024:.0f} KB)")

print("\nDone. Verifying...")
for cat, path in TEMPLATES.items():
    wb = openpyxl.load_workbook(path, data_only=False)
    ws = wb["ENTRY_REVERSAL_BOUNCE"] if "ENTRY_REVERSAL_BOUNCE" in wb.sheetnames else wb.active
    # Check font uniformity
    fonts = set((c.font.name, c.font.size) for row in ws.iter_rows() for c in row if c.font)
    print(f"{cat} fonts {fonts}")
    wb.close()
