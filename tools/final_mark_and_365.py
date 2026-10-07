#!/usr/bin/env python3
import pathlib, re, shutil, openpyxl

BEST = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/BEST")
ROOT = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS")

def base_key(name):
    return re.sub(r"_bh.*$","", name.split("_30d_matrix")[0]) if "_30d_matrix" in name else None

def is_incomplete(path):
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
        if "ENTRY_REVERSAL_BOUNCE" not in wb.sheetnames:
            wb.close()
            return True
        ws = wb["ENTRY_REVERSAL_BOUNCE"]
        # Count filled F (col 6, index 5) in first 10 data rows (rows 3-12)
        filled = 0
        for row in ws.iter_rows(min_row=3, max_row=12, values_only=True):
            v = row[5] if len(row)>5 else None
            if v is not None:
                try:
                    # Must be number
                    float(v)
                    filled += 1
                except:
                    pass
        wb.close()
        # Need at least 3 filled deltas to be considered having finished that sheet
        if filled < 3:
            return True
        # Also check size
        if path.stat().st_size < 1_000_000:
            return True
        return False
    except:
        return True

# Build 365D map
chart_365 = {}
for h in ROOT.glob("*365D_REAL_ZOOMABLE.html"):
    if "VERIFY" in h.name:
        continue
    m = re.match(r"(.+?_(?:LONG|SHORT))", h.name)
    if m and h.name == f"{m.group(1)}_365D_REAL_ZOOMABLE.html":
        chart_365[m.group(1)] = h

marked = 0
added = 0
for cat in ["STOCKS_LONG","STOCKS_SHORT","CRYPTO_LONG","CRYPTO_SHORT"]:
    for xlsx in list((BEST/cat).glob("*.xlsx")):
        if "_INCOMPLETE" in xlsx.name:
            continue
        base = base_key(xlsx.name)
        if not base:
            continue
        # Check incomplete
        if is_incomplete(xlsx):
            new_name = xlsx.name.replace("_30d_matrix", "_INCOMPLETE_30d_matrix")
            if "_INCOMPLETE" not in xlsx.name:
                new_path = xlsx.parent / new_name
                # Rename xlsx
                xlsx.rename(new_path)
                # Rename its 30D chart as well to keep pairing
                chart = BEST/cat/f"{base}_30D_REAL_ZOOMABLE.html"
                if chart.exists():
                    new_chart = BEST/cat/f"{base}_INCOMPLETE_30D_REAL_ZOOMABLE.html"
                    try:
                        chart.rename(new_chart)
                    except:
                        pass
                # Create marker txt
                marker = BEST/cat/f"{base}_INCOMPLETE.txt"
                marker.write_text(f"{base} INCOMPLETE: ENTRY_REVERSAL_BOUNCE has <3 deltas filled, pilot not finished all 13 sheets. File: {new_name}\n")
                marked += 1
                base = base  # keep for 365
        # Add 365D if exists
        if base in chart_365:
            src = chart_365[base]
            # For incomplete, 365D name should also be marked? Keep same base name
            dest = BEST/cat/f"{base}_365D_REAL_ZOOMABLE.html"
            # If file is marked incomplete, 365D should not exist yet (since 30D not finished), but if it does, keep it
            if not dest.exists():
                shutil.copy2(src, dest)
                added += 1

print(f"Marked incomplete: {marked}, Added 365D: {added}")
for cat in ["STOCKS_LONG","STOCKS_SHORT","CRYPTO_LONG","CRYPTO_SHORT"]:
    x = len(list((BEST/cat).glob("*.xlsx")))
    inc = len(list((BEST/cat).glob("*INCOMPLETE*.xlsx")))
    h30 = len([p for p in (BEST/cat).glob("*.html") if "30D" in p.name and "365D" not in p.name])
    h365 = len([p for p in (BEST/cat).glob("*365D*.html")])
    print(f"{cat}: {x} xlsx ({inc} incomplete) + {h30} 30D + {h365} 365D")
