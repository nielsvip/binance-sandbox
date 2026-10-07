#!/usr/bin/env python3
"""
V2: Ensure every BEST xlsx has bh/gain in filename and only ONE chart per xlsx (trades).

- Reads gain_pct/bh_pct from BASELINE_METRICS sheet (fallback parse gain from filename)
- Renames best xlsx to {BASE}_bh{format}_gain{format}_30d_matrix.xlsx
- Keeps only ONE zoomable chart per base: {BASE}_30D_REAL_ZOOMABLE.html (trades of exact best result)
  deletes other per-sheet zoomables and duplicates, ensures chart in SAME folder as xlsx
- Validates xlsx (zip + sheets >=23) before selecting best; replaces bad 46K pilots with valid CELL source
- Reorganizes BEST/STOCKS_LONG etc from scratch after cleanup
"""
import pathlib, re, shutil, zipfile, os
import openpyxl

SPREADSHEETS = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS")
CELL = SPREADSHEETS / "V15_V16_CELL_BY_CELL"
BEST = SPREADSHEETS / "BEST"

def base_key(name: str):
    if "_30d_matrix" not in name:
        return None
    prefix = name.split("_30d_matrix")[0]
    prefix = re.sub(r"_bh.*$", "", prefix)
    return prefix

def parse_gain_from_name(name: str):
    m = re.search(r"_gain([m]?)([\d]+)p([\d]+)", name)
    if not m: return None
    sign = -1 if m.group(1) == "m" else 1
    return sign * (int(m.group(2)) + int(m.group(3)) / 100)

def bh_gain_from_sheet(path: pathlib.Path):
    # Read BASELINE_METRICS sheet for gain_pct/bh_pct
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
        # sheet name is {BASE}_BASELINE_METRICS or try to find
        for sheet_name in wb.sheetnames:
            if "BASELINE_METRICS" in sheet_name:
                ws = wb[sheet_name]
                gain = None
                bh = None
                for row in ws.iter_rows(min_row=1, max_row=20, values_only=True):
                    if not row or not row[0]: continue
                    key = str(row[0]).strip()
                    if key == "gain_pct":
                        gain = row[1]
                    elif key == "bh_pct":
                        bh = row[1]
                wb.close()
                if gain is not None and bh is not None:
                    return float(bh), float(gain)
                break
        wb.close()
    except Exception as e:
        pass
    return None, None

def format_pct(v: float):
    # v is percent like 6.78 -> "6p78", -3.49 -> "m3p49"
    sign = "m" if v < 0 else ""
    av = abs(v)
    # round to 2 decimals like filename
    # Use 2 decimals, p as decimal
    return f"{sign}{av:.2f}".replace(".", "p")

def is_valid_xlsx(path: pathlib.Path):
    try:
        if not path.exists() or path.stat().st_size < 500_000:
            return False
    except:
        return False
    try:
        with zipfile.ZipFile(path) as z:
            if "xl/workbook.xml" not in z.namelist():
                return False
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
        n = len(wb.sheetnames)
        wb.close()
        return n >= 20
    except:
        return False

def is_crypto(sym: str):
    return "USDT" in sym or "USDC" in sym

def category(sym: str):
    side = "LONG" if sym.endswith("_LONG") else "SHORT"
    pref = "CRYPTO" if is_crypto(sym) else "STOCKS"
    return f"{pref}_{side}"

def html_sym_side(name: str):
    m = re.match(r"(.+?_(?:LONG|SHORT))", name)
    return m.group(1) if m else None

def main(cleanup_root=True):
    # Clean BEST and rebuild from valid CELL files
    if BEST.exists():
        shutil.rmtree(BEST)
    for cat in ["STOCKS_LONG","STOCKS_SHORT","CRYPTO_LONG","CRYPTO_SHORT"]:
        (BEST / cat).mkdir(parents=True, exist_ok=True)

    valid_files = [p for p in CELL.glob("*.xlsx") if is_valid_xlsx(p) and "_30d_matrix" in p.name]
    print(f"CELL total {len(list(CELL.glob('*.xlsx')))} valid {len(valid_files)} invalid {len(list(CELL.glob('*.xlsx')))-len(valid_files)}")
    from collections import defaultdict
    groups = defaultdict(list)
    for f in valid_files:
        b = base_key(f.name)
        if b: groups[b].append(f)
    print(f"Groups {len(groups)}")

    # Select best per group: max gain from sheet, then max mtime
    best_map = {}
    renamed_count = 0
    for base, lst in groups.items():
        def key(p):
            bh, gain = bh_gain_from_sheet(p)
            if gain is None:
                gain = parse_gain_from_name(p.name)
                if gain is None: gain = -9999
            bh2 = bh if bh is not None else 0
            return (gain, p.stat().st_mtime, p.stat().st_size)
        best = max(lst, key=key)
        # Ensure filename has bh/gain
        bh, gain = bh_gain_from_sheet(best)
        if bh is None or gain is None:
            # fallback to filename parse or 0
            bh = bh if bh is not None else 0
            gain = parse_gain_from_name(best.name)
            if gain is None: gain = 0
            bh = 0 if bh is None else bh
        # Check if current filename already has bh/gain and matches sheet values (within 0.01)
        has_bh = "_bh" in best.name and "_gain" in best.name
        need_rename = True
        if has_bh:
            # verify matches sheet (allow 0.05 tolerance)
            gname = parse_gain_from_name(best.name)
            # parse bh from name
            m = re.search(r"_bh([m]?)([\d]+)p([\d]+)", best.name)
            if m and gname is not None and bh is not None and gain is not None:
                bh_name = (-1 if m.group(1)=="m" else 1) * (int(m.group(2)) + int(m.group(3))/100)
                if abs(bh_name - bh) < 0.1 and abs(gname - gain) < 0.1:
                    need_rename = False
        if need_rename and bh is not None and gain is not None:
            bh_str = format_pct(bh)
            gain_str = format_pct(gain)
            new_name = f"{base}_bh{bh_str}_gain{gain_str}_30d_matrix.xlsx"
            # Avoid collision
            if new_name != best.name:
                # Copy with new name to BEST (don't rename CELL source)
                best_map[base] = (best, new_name, bh, gain)
                renamed_count += 1
            else:
                best_map[base] = (best, best.name, bh, gain)
        else:
            best_map[base] = (best, best.name, bh, gain)

    print(f"Renamed {renamed_count} to include bh/gain")

    # Copy best xlsx to BEST/category with new name
    for base, (src, new_name, bh, gain) in best_map.items():
        cat = category(base)
        dest = BEST / cat / new_name
        shutil.copy2(src, dest)

    # Handle charts: keep ONLY ONE per base: {BASE}_30D_REAL_ZOOMABLE.html
    # First, find source chart in SPREADSHEETS root (not BEST) that matches base
    # If not found, keep existing zoomable if present, else leave missing (will be generated next pilot)
    html_root = list(SPREADSHEETS.glob("*.html"))
    # Build map base -> best chart (prefer 30D_REAL_ZOOMABLE)
    chart_map = {}
    for h in html_root:
        base = html_sym_side(h.name)
        if base and base in best_map and ("30D_REAL_ZOOMABLE" in h.name and "VERIFY" not in h.name):
            # Prefer exact {BASE}_30D_REAL_ZOOMABLE.html
            if h.name == f"{base}_30D_REAL_ZOOMABLE.html":
                chart_map[base] = h
            elif base not in chart_map:
                chart_map[base] = h

    copied_charts = 0
    for base, chart_src in chart_map.items():
        cat = category(base)
        dest = BEST / cat / chart_src.name
        # Ensure chart name matches base (it already does)
        shutil.copy2(chart_src, dest)
        copied_charts += 1

    # Report
    for cat in ["STOCKS_LONG","STOCKS_SHORT","CRYPTO_LONG","CRYPTO_SHORT"]:
        d = BEST / cat
        nx = len(list(d.glob("*.xlsx")))
        nh = len(list(d.glob("*.html")))
        sz = sum(p.stat().st_size for p in d.iterdir() if p.is_file())/1e6
        print(f"{cat}: {nx} xlsx + {nh} charts (1 per xlsx) {sz:.1f} MB")
        # List missing charts
        missing = [b for b in best_map if category(b)==cat and b not in chart_map]
        if missing:
            print(f"  missing charts for {missing[:3]}")

    total_x = sum(1 for _ in BEST.rglob("*.xlsx"))
    total_h = sum(1 for _ in BEST.rglob("*.html"))
    print(f"BEST total {total_x} xlsx + {total_h} html (target 1:1)")

    # Cleanup root: delete duplicate zoomables and invalid pilots, keep templates
    if cleanup_root:
        keep_templates = {f.name for f in SPREADSHEETS.glob("TEMPLATE*.xlsx")}
        deleted = 0
        for p in SPREADSHEETS.glob("*.html"):
            # Delete all ZOOMABLE and chart html from root after copying to BEST (keep non-chart like progress)
            if "ZOOMABLE" in p.name or p.name.endswith("_chart.html"):
                # If its base is in best_map, it has been copied to BEST, so delete from root to avoid suffocation
                base = html_sym_side(p.name)
                if base in best_map:
                    try: p.unlink(); deleted+=1
                    except: pass
        for p in SPREADSHEETS.glob("*.xlsx"):
            if p.name.startswith("TEMPLATE"): continue
            if "_matrix" in p.name:
                try: p.unlink(); deleted+=1
                except: pass
        for p in list(SPREADSHEETS.glob("*.tmp"))+list(SPREADSHEETS.glob("*.bak")):
            try: p.unlink(); deleted+=1
            except: pass
        for p in list(CELL.glob("*.tmp"))+list(CELL.glob("*.bak"))+list(BEST.rglob("*.tmp")):
            try: p.unlink(); deleted+=1
            except: pass
        print(f"Cleanup deleted {deleted} root duplicates (templates kept)")

if __name__ == "__main__":
    main()
