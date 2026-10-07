#!/usr/bin/env python3
"""
Publish final winners: 1 XLSX per sym_side (bh+gain) to V15_V16_CELL_BY_CELL_FINAL.
S1 has 115 winners but Mac has 1737 (161MB vs 2.1GB) due to timestamped interim
and multiple historical winners per sym. Mac was drowning at 1GB/min.
This script keeps only the latest winning file per sym_side (by mtime, then gain)
in FINAL, so Mac pull with --delete gets exactly 1 per sym_side, no interim.
"""
import pathlib, re, shutil, hashlib
ROOT=pathlib.Path.home()/ "binance-sandbox" if pathlib.Path.home().joinpath("binance-sandbox").exists() else pathlib.Path("/Users/niels/Documents/binance")
SRC=ROOT/"SPREADSHEETS/V15_V16_CELL_BY_CELL"
DST=ROOT/"SPREADSHEETS/V15_V16_CELL_BY_CELL_FINAL"
DST.mkdir(parents=True, exist_ok=True)

def sym_of(name: str) -> str | None:
    # Extract SYM_SIDE from winning filename: e.g. 1000BONKUSDC_LONG_bh15p46_gain15p11_30d_matrix.xlsx -> 1000BONKUSDC_LONG
    # Winning pattern: <SYMBOL>_<SIDE>_bh..._gain..._30d_matrix.xlsx
    m=re.match(r"^(.+?_(?:LONG|SHORT))_bh.*_gain.*_30d_matrix\.xlsx$", name)
    return m.group(1) if m else None

def is_winning(name: str) -> bool:
    low=name.lower()
    if "pilot" in low: return False
    if "_30d_matrix_20" in name or "_matrix_20" in name: return False
    if "bh" not in low or "gain" not in low: return False
    if not low.endswith(".xlsx"): return False
    if "30d" not in low: return False
    return sym_of(name) is not None

# Gather all winning files
all_win=[p for p in SRC.glob("*.xlsx") if is_winning(p.name)]
print(f"found {len(all_win)} winning files in {SRC}")

# Group by sym_side, keep latest by mtime (and gain as tiebreaker)
from collections import defaultdict
by_sym=defaultdict(list)
for p in all_win:
    sym=sym_of(p.name)
    if sym: by_sym[sym].append(p)

kept=set()
for sym, files in by_sym.items():
    # Sort by mtime desc, then by gain desc (parse gain)
    def gain_of(p):
        m=re.search(r"gainm?(\d+)p(\d+)?", p.name)
        if not m: return 0
        sign=-1 if "gainm" in p.name else 1
        return sign*float(f"{m.group(1)}.{m.group(2) or 0}")
    files_sorted=sorted(files, key=lambda p: (p.stat().st_mtime, gain_of(p)), reverse=True)
    best=files_sorted[0]
    kept.add(best.name)
    # Copy/hardlink to FINAL (use copy to avoid updating mtime of source)
    dst=DST/best.name
    # Only copy if not exists or source is newer
    if not dst.exists() or best.stat().st_mtime > dst.stat().st_mtime:
        # Use hardlink if same filesystem, else copy
        try:
            if dst.exists(): dst.unlink()
            dst.hardlink_to(best)
        except:
            shutil.copy2(best, dst)
    # USER 2026-10-03 (xlsx source-of-truth): FINAL carries the complete truth bundle — xlsx + coherent chart + manifest sidecar
    for _sib_name in (best.name.replace(".xlsx", ".html"), best.name.replace(".xlsx", "_manifest.json")):
        _src_sib = best.parent / _sib_name
        if _src_sib.exists():
            kept.add(_sib_name)
            _dst_sib = DST / _sib_name
            if not _dst_sib.exists() or _src_sib.stat().st_mtime > _dst_sib.stat().st_mtime:
                try:
                    if _dst_sib.exists(): _dst_sib.unlink()
                    _dst_sib.hardlink_to(_src_sib)
                except Exception:
                    shutil.copy2(_src_sib, _dst_sib)

# Remove stale files from FINAL that are no longer the winner for that sym
for p in list(DST.glob("*.xlsx")) + list(DST.glob("*.html")) + list(DST.glob("*_manifest.json")):
    if p.name not in kept:
        p.unlink()
        print(f"removed stale {p.name} from FINAL")

print(f"FINAL {len(list(DST.glob('*.xlsx')))} winning files (1 per sym_side) in {DST} vs {len(all_win)} total winning, 1737 on Mac before")
# Also ensure Mac's old interim files are not in FINAL
print(f"SRC total {len(list(SRC.glob('*.xlsx')))} (includes 11981 timestamped + 3813 pilot)")
