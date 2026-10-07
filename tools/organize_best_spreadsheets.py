#!/usr/bin/env python3
"""
Organize SPREADSHEETS to keep ONLY final (best gain) xlsx + their zoomable charts.
Creates 4 subfolders: BEST/STOCKS_LONG, BEST/STOCKS_SHORT, BEST/CRYPTO_LONG, BEST/CRYPTO_SHORT
Charts are copied to SAME folder as their xlsx for easy review.

- Best = max gain in filename (_gainX pY -> X.Y), fallback to latest mtime if no gain.
- Keeps 1 xlsx per base_key (SYM_SIDE stripped of _bh... and _30d_matrix)
- Copies matching zoomable html: *ZOOMABLE*.html and *_chart.html for that SYM_SIDE
- Reports suffocation savings; does NOT delete originals in V15_V16_CELL_BY_CELL (source of truth)
  but can prune SPREADSHEETS root html/xlsx that are not BEST (with --cleanup)
"""
import pathlib, re, shutil
from collections import defaultdict

SPREADSHEETS = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS")
CELL = SPREADSHEETS / "V15_V16_CELL_BY_CELL"
BEST = SPREADSHEETS / "BEST"

def base_key(name: str):
    if "_30d_matrix" not in name:
        return None
    prefix = name.split("_30d_matrix")[0]
    prefix = re.sub(r"_bh.*$", "", prefix)
    return prefix

def parse_gain(name: str):
    m = re.search(r"_gain([m]?)([\d]+)p([\d]+)", name)
    if not m:
        return None
    sign = -1 if m.group(1) == "m" else 1
    return sign * (int(m.group(2)) + int(m.group(3)) / 100)

def is_crypto(sym_side: str):
    return "USDT" in sym_side or "USDC" in sym_side

def category(sym_side: str):
    crypto = is_crypto(sym_side)
    side = "LONG" if sym_side.endswith("_LONG") else "SHORT" if sym_side.endswith("_SHORT") else "UNKNOWN"
    prefix = "CRYPTO" if crypto else "STOCKS"
    return f"{prefix}_{side}"

def html_sym_side(name: str):
    m = re.match(r"(.+?_(?:LONG|SHORT))", name)
    return m.group(1) if m else None

def main(cleanup=False):
    if not CELL.exists():
        print(f"CELL not found {CELL}")
        return
    BEST.mkdir(exist_ok=True)
    for cat in ["STOCKS_LONG", "STOCKS_SHORT", "CRYPTO_LONG", "CRYPTO_SHORT"]:
        (BEST / cat).mkdir(exist_ok=True)

    # Group xlsx by base
    files = [p for p in CELL.glob("*.xlsx") if p.is_file() and "_30d_matrix" in p.name]
    groups = defaultdict(list)
    for f in files:
        b = base_key(f.name)
        if b:
            groups[b].append(f)
    # Select best per group
    best_map = {}  # base -> Path
    for base, lst in groups.items():
        def key(p):
            g = parse_gain(p.name)
            # best gain first, then latest mtime, then largest size
            return (g if g is not None else -9999, p.stat().st_mtime, p.stat().st_size)
        best = max(lst, key=key)
        best_map[base] = best

    print(f"Groups {len(groups)} -> best {len(best_map)}")
    # Copy best xlsx to BEST/category
    copied_xlsx = 0
    for base, src in best_map.items():
        cat = category(base)
        dest = BEST / cat / src.name
        # copy only if newer or not exists
        if not dest.exists() or src.stat().st_mtime > dest.stat().st_mtime:
            shutil.copy2(src, dest)
            copied_xlsx += 1
    # Copy zoomable charts
    htmls = list(SPREADSHEETS.glob("*.html"))
    # Filter to zoomable/chart only
    zoomables = [h for h in htmls if ("ZOOMABLE" in h.name or h.name.endswith("_chart.html"))]
    copied_html = 0
    html_by_base = defaultdict(list)
    for h in zoomables:
        base = html_sym_side(h.name)
        if base and base in best_map:
            html_by_base[base].append(h)
    for base, hlist in html_by_base.items():
        cat = category(base)
        dest_dir = BEST / cat
        for src in hlist:
            dest = dest_dir / src.name
            if not dest.exists() or src.stat().st_mtime > dest.stat().st_mtime:
                try:
                    shutil.copy2(src, dest)
                    copied_html += 1
                except: pass

    # Report
    total_best_xlsx = sum(1 for _ in BEST.rglob("*.xlsx"))
    total_best_html = sum(1 for _ in BEST.rglob("*.html"))
    size_best = sum(p.stat().st_size for p in BEST.rglob("*") if p.is_file())
    size_cell = sum(p.stat().st_size for p in CELL.rglob("*") if p.is_file())
    size_root_html = sum(p.stat().st_size for p in SPREADSHEETS.glob("*.html") if p.is_file())
    size_spread = sum(p.stat().st_size for p in SPREADSHEETS.iterdir() if p.is_file())
    print(f"Copied {copied_xlsx} xlsx, {copied_html} zoomables to BEST")
    print(f"BEST: {total_best_xlsx} xlsx + {total_best_html} html = {size_best/1e6:.1f} MB in 4 folders")
    for cat in ["STOCKS_LONG", "STOCKS_SHORT", "CRYPTO_LONG", "CRYPTO_SHORT"]:
        d = BEST / cat
        nx = len(list(d.glob("*.xlsx")))
        nh = len(list(d.glob("*.html")))
        sz = sum(p.stat().st_size for p in d.iterdir() if p.is_file())/1e6
        print(f"  {cat}: {nx} xlsx + {nh} charts {sz:.1f} MB")
    print(f"CELL source: {len(files)} xlsx {size_cell/1e6:.1f} MB")
    print(f"Root before cleanup: {len(htmls)} html {size_root_html/1e6:.1f} MB zoomables + {len(list(SPREADSHEETS.glob('*.xlsx')))} root xlsx")

    if cleanup:
        # Remove non-BEST html from root (keep templates, keep BEST), remove root xlsx that are not templates
        keep_templates = {"TEMPLATE.xlsx", "TEMPLATE_STOCKS_LONG.xlsx", "TEMPLATE_STOCKS_SHORT.xlsx", "TEMPLATE_CRYPTO_LONG.xlsx", "TEMPLATE_CRYPTO_SHORT.xlsx", "TEMPLATE_V15_STOCKS_LONG.xlsx", "TEMPLATE_V15_STOCKS_SHORT.xlsx", "TEMPLATE_V15_CRYPTO_LONG.xlsx", "TEMPLATE_V15_CRYPTO_SHORT.xlsx"}
        deleted = 0
        for p in SPREADSHEETS.glob("*.html"):
            base = html_sym_side(p.name)
            # delete if it's a zoomable chart not in BEST (i.e., sym_side not in best_map) OR if it's duplicate progress file
            # We keep html that is in BEST (already copied) out of root to avoid duplication - delete from root after copy
            if ("ZOOMABLE" in p.name or p.name.endswith("_chart.html")) and base in best_map:
                # this html has been copied to BEST, remove from root to avoid suffocation
                try:
                    p.unlink()
                    deleted += 1
                except: pass
        # delete root xlsx that are pilot matrices (not templates) - they belong in CELL
        for p in SPREADSHEETS.glob("*.xlsx"):
            if p.name in keep_templates or p.name.startswith("TEMPLATE"):
                continue
            # if it's a matrix file (contains _30d_matrix or _matrix), remove from root (CELL is source)
            if "_matrix" in p.name:
                try:
                    p.unlink()
                    deleted += 1
                except: pass
        # delete .tmp .bak
        for p in SPREADSHEETS.glob("*.tmp"):
            try: p.unlink(); deleted+=1
            except: pass
        for p in SPREADSHEETS.glob("*.bak"):
            try: p.unlink(); deleted+=1
            except: pass
        # delete from CELL .tmp .bak
        for p in CELL.glob("*.tmp"):
            try: p.unlink(); deleted+=1
            except: pass
        for p in CELL.glob("*.bak"):
            try: p.unlink(); deleted+=1
            except: pass
        print(f"Cleanup deleted {deleted} duplicate root/charts/tmp files (templates kept, BEST retained)")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true", help="move charts to BEST and delete duplicates from root to save space")
    args = ap.parse_args()
    main(cleanup=args.cleanup)
