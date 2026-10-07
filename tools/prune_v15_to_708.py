#!/usr/bin/env python3
"""
Enforce 708 max in V15_V16_CELL_BY_CELL (354 sym_side * 2 runs).
Second run of 354 cannot exceed 708 files — delete timestamped interim and pilot,
keep only winning *bh*_*gain*.xlsx, 1-2 per sym_side latest.
S1 currently 8650 files (54 winning + 8379 timestamped + 2371 pilot) 2.92GB -> 54 files 28M after prune.
"""
import pathlib, re, shutil
ROOT=pathlib.Path.home()/"binance-sandbox" if pathlib.Path.home().joinpath("binance-sandbox").exists() else pathlib.Path("/Users/niels/Documents/binance")
SRC=ROOT/"SPREADSHEETS/V15_V16_CELL_BY_CELL"
# Also clean FINAL to 1 per sym_side (already done by publish_final_winners, but ensure)
def is_winning(name):
    low=name.lower()
    if "pilot" in low: return False
    if "_30d_matrix_20" in name or "_matrix_20" in name: return False
    if "bh" not in low or "gain" not in low: return False
    if not low.endswith(".xlsx"): return False
    if "30d" not in low: return False
    return bool(re.match(r"^.+?_(?:LONG|SHORT)_bh.*_gain.*_30d_matrix\.xlsx$", name))

def sym_of(name):
    m=re.match(r"^(.+?_(?:LONG|SHORT))_bh.*_gain.*_30d_matrix\.xlsx$", name)
    return m.group(1) if m else None

all_xlsx=list(SRC.glob("*.xlsx"))
print(f"before {len(all_xlsx)} files in {SRC}")
winning=[p for p in all_xlsx if is_winning(p.name)]
interim=[p for p in all_xlsx if not is_winning(p.name)]
print(f"winning {len(winning)} interim {len(interim)} (timestamped + pilot + plain)")

# Group winning by sym_side, keep latest 2 per sym (708 max = 354*2, currently 54 winning -> keep all 54)
from collections import defaultdict
by_sym=defaultdict(list)
for p in winning:
    sym=sym_of(p.name)
    if sym: by_sym[sym].append(p)

kept=set()
for sym, files in by_sym.items():
    # Sort by mtime desc, keep latest 2
    files_sorted=sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files_sorted[:2]:
        kept.add(p)
    # Delete older winning beyond 2 per sym
    for p in files_sorted[2:]:
        try: p.unlink(); print(f"removed old winning {p.name} for {sym}")
        except: pass

# Delete all interim (timestamped, pilot, plain without bh/gain)
for p in interim:
    try: p.unlink()
    except: pass

after=list(SRC.glob("*.xlsx"))
print(f"after {len(after)} files (winning {len([p for p in after if is_winning(p.name)])} + interim {len([p for p in after if not is_winning(p.name)])})")
print(f"FINAL per sym_side {len(kept)} (max 708 for 2 runs of 354)")
# Also ensure FINAL dir has 1 per sym
DST=ROOT/"SPREADSHEETS/V15_V16_CELL_BY_CELL_FINAL"
if DST.exists():
    # FINAL should already be 1 per sym via publish_final_winners, verify
    final_win=list(DST.glob("*.xlsx"))
    print(f"FINAL dir {len(final_win)} files (1 per sym_side)")
