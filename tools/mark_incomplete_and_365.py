#!/usr/bin/env python3
import pathlib, re, shutil, openpyxl, time

BEST = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/BEST")
ROOT = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS")

def base_key(name):
    return re.sub(r"_bh.*$","", name.split("_30d_matrix")[0]) if "_30d_matrix" in name else None

def count_overrides(path):
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
        total = 0
        for sheet in wb.sheetnames:
            if sheet in ("LEGEND_FILTERS","INSTRUCTIONS","FILTERS_EXPLAINED","INSTRUCTIONS_V2","FILTER_DICTIONARY_V2","FORMULAS","Results_Deltas","Results_30d_Deltas","12SYM_PARITY"):
                continue
            if "BASELINE_METRICS" in sheet:
                continue
            ws = wb[sheet]
            # Column C is override (index 3)
            for row in ws.iter_rows(min_row=3, max_row=ws.max_row, min_col=3, max_col=3, values_only=True):
                val = row[0]
                if val and isinstance(val, str) and len(val.strip())>5:
                    total += 1
        wb.close()
        return total
    except Exception as e:
        return 0

# Build 365D chart map
chart_365_map = {}
for h in ROOT.glob("*365D_REAL_ZOOMABLE.html"):
    if "VERIFY" in h.name: continue
    m = re.match(r"(.+?_(?:LONG|SHORT))", h.name)
    if m and h.name == f"{m.group(1)}_365D_REAL_ZOOMABLE.html":
        chart_365_map[m.group(1)] = h

marked_incomplete = 0
added_365 = 0
for cat in ["STOCKS_LONG","STOCKS_SHORT","CRYPTO_LONG","CRYPTO_SHORT"]:
    for xlsx in list((BEST/cat).glob("*.xlsx")):
        base = base_key(xlsx.name)
        if not base: continue
        # Check completeness
        overrides = count_overrides(xlsx)
        # Heuristic: complete should have at least 10 overrides across 13 sheets (pilot fills progressively)
        # Also check file age <1h and size
        is_incomplete = overrides < 5  # very few overrides = not finished all sheets
        # Also check sheets count
        try:
            wb = openpyxl.load_workbook(str(xlsx), data_only=True, read_only=True)
            sheets = len(wb.sheetnames)
            wb.close()
            if sheets < 23:
                is_incomplete = True
        except:
            is_incomplete = True

        if is_incomplete:
            # Mark clearly: rename to include _INCOMPLETE
            if "_INCOMPLETE" not in xlsx.name:
                new_name = xlsx.name.replace("_30d_matrix", "_INCOMPLETE_30d_matrix")
                new_path = xlsx.parent / new_name
                xlsx.rename(new_path)
                # Also rename its 30D chart to match
                old_chart = BEST/cat/f"{base}_30D_REAL_ZOOMABLE.html"
                if old_chart.exists():
                    new_chart = BEST/cat/f"{base}_INCOMPLETE_30D_REAL_ZOOMABLE.html"
                    try: old_chart.rename(new_chart)
                    except: pass
                marked_incomplete += 1
                # Update base for 365D handling
                base = base  # keep original base for 365 lookup
            else:
                marked_incomplete += 0  # already marked

        # Add 365D chart if exists for this base
        if base in chart_365_map:
            src = chart_365_map[base]
            dest = BEST/cat/f"{base}_365D_REAL_ZOOMABLE.html"
            if not dest.exists():
                shutil.copy2(src, dest)
                added_365 += 1
                # Also ensure we don't have duplicate VERIFY
        # For incomplete files, also ensure placeholder indicates incomplete
        if is_incomplete:
            # Create marker txt
            marker = BEST/cat/f"{base}_INCOMPLETE.txt"
            if not marker.exists():
                marker.write_text(f"{base} INCOMPLETE: only {overrides} overrides filled, {xlsx.name} not finished all 13 sheets. Pilot still running worst2best.\n")

print(f"Marked incomplete: {marked_incomplete}, Added 365D charts: {added_365}")
for cat in ["STOCKS_LONG","STOCKS_SHORT","CRYPTO_LONG","CRYPTO_SHORT"]:
    x = len(list((BEST/cat).glob("*.xlsx")))
    h30 = len([p for p in (BEST/cat).glob("*.html") if "30D" in p.name])
    h365 = len([p for p in (BEST/cat).glob("*365D*.html")])
    inc = len([p for p in (BEST/cat).glob("*INCOMPLETE*")])
    print(f"{cat}: {x} xlsx ({inc} incomplete) + {h30} 30D + {h365} 365D")
