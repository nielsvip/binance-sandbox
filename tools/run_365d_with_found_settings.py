#!/usr/bin/env python3
"""
Run 365D test for each FINAL 30D sym_side using its found 30D settings.
- For each FINAL file SPREADSHEETS/V15_V16_CELL_BY_CELL_FINAL/*_30d_matrix.xlsx (85 files), extract sym_side
- Find its hustler_best.json (or FINAL's own overrides) as baseline
- Run v15_pilot.py --window-days 365 --vector-only --workers 16 --baseline-json <hustler_best>
- Output: SPREADSHEETS/V15_V16_CELL_BY_CELL/<sym>_365d_matrix.xlsx with bh and gain in FINAL name after publish
- Charts: Excel zoomable charts already in template, will contain 365D data
"""
import pathlib, subprocess, json, re, sys, os, glob

ROOT = pathlib.Path.home() / "binance-sandbox"
FINAL_DIR = ROOT / "SPREADSHEETS/V15_V16_CELL_BY_CELL_FINAL"
OUT_DIR = ROOT / "SPREADSHEETS/V15_V16_CELL_BY_CELL"
TEMPLATE_MAP = {
    "LONG": "TEMPLATE_STOCKS_LONG.xlsx",
    "SHORT": "TEMPLATE_STOCKS_SHORT.xlsx",
}
# fallback for crypto
TEMPLATE_CRYPTO = {
    "LONG": "TEMPLATE_CRYPTO_LONG.xlsx",
    "SHORT": "TEMPLATE_CRYPTO_SHORT.xlsx",
}

def is_stock(sym): return "USDT" not in sym and "USDC" not in sym
def get_template(sym_side):
    side = sym_side.rsplit("_",1)[-1]
    base = sym_side.rsplit("_",1)[0]
    # check crypto vs stock
    if not is_stock(sym_side):
        tmpl = ROOT / f"SPREADSHEETS/{TEMPLATE_CRYPTO[side]}"
    else:
        tmpl = ROOT / f"SPREADSHEETS/{TEMPLATE_MAP[side]}"
    if tmpl.exists(): return tmpl
    return ROOT / "SPREADSHEETS/TEMPLATE.xlsx"

def find_baseline(sym_side):
    # hustler_best.json is source of found settings
    cand = OUT_DIR / f"{sym_side}_hustler_best.json"
    if cand.exists(): return cand
    cand2 = ROOT / f"SPREADSHEETS/V15_V16_CELL_BY_CELL/{sym_side}_hustler_best.json"
    if cand2.exists(): return cand2
    # fallback: try to extract overrides from FINAL xlsx? For now return None (will use defaults)
    return None

def run_one(sym_side):
    tmpl = get_template(sym_side)
    baseline = find_baseline(sym_side)
    if not baseline or not baseline.exists():
        print(f"[365D-SKIP] {sym_side} no hustler_best.json (no found 30D settings) — skip 365D until 30D best exists", flush=True)
        return
    cmd = [
        str(ROOT / ".venv/bin/python"), "-u", str(ROOT / "v15_pilot.py"),
        "--sym-side", sym_side,
        "--template", str(tmpl),
        "--window-days", "365",
        "--vector-only",
        "--workers", "16",
        "--baseline-json", str(baseline),
    ]
    print(f"[365D] {sym_side} tmpl={tmpl.name} baseline={baseline} cmd={' '.join(cmd)}", flush=True)
    try:
        subprocess.run(cmd, timeout=3600, check=False)
    except subprocess.TimeoutExpired:
        print(f"[365D-timeout] {sym_side}", flush=True)
    except Exception as e:
        print(f"[365D-err] {sym_side} {e}", flush=True)

def stable_hash(s: str) -> int:
    import hashlib
    return int(hashlib.md5(s.encode()).hexdigest(), 16)

def get_host_idx():
    import socket
    h = socket.gethostname().lower()
    if "s5" in h: return 2
    if "s6" in h: return 0
    if "s2" in h or "tradier" in h: return 1
    # S1 niels — for 365D, S1 handles crypto shard 0 separate from S6 stocks shard 0
    # S1 crypto-only while fleet alive, so S1 365D will be crypto only, S6 stocks shard 0
    if "niels" in h:
        return 0  # crypto shard 0, but stocks will be filtered later
    return 0

if __name__ == "__main__":
    finals = sorted(FINAL_DIR.glob("*_30d_matrix.xlsx"))
    print(f"[365D] Found {len(finals)} FINAL 30D syms", flush=True)
    host_idx = get_host_idx()
    # Shard 85 syms across idle S5/S6 + S1: S5 idx2, S6 idx0, S1 idx0 (but S1 already busy, so S5/S6 take most)
    # If on S5/S6, only run its 1/3 shard; if on S1, run remaining after S5/S6 (or all if they are idle)
    # For now, each host runs its shard: prevents duplicate 365D work, respects S6 never AAPL
    filtered = []
    import socket
    _is_s1 = "niels" in socket.gethostname().lower()
    for f in finals:
        m = re.match(r"(.+_(?:LONG|SHORT))_bh", f.name)
        if not m:
            print(f"[skip] no bh in {f.name}", flush=True)
            continue
        sym_side = m.group(1)
        # S1 crypto-only while fleet alive: skip stocks for S1 365D, let S2/S5/S6 handle stocks
        is_stock_sym = "USDT" not in sym_side and "USDC" not in sym_side
        if _is_s1 and is_stock_sym:
            continue
        base = sym_side.rsplit("_",1)[0]
        # For S1, use crypto hash; for S2/S5/S6 use stocks+crypto hash but S1 already filtered stocks
        if stable_hash(base) % 3 != host_idx:
            continue
        filtered.append((f, sym_side))
    print(f"[365D] Host idx {host_idx} shard {len(filtered)}/{len(finals)} (S5=2 S6=0 S2=1 S1=0 crypto-only S1)", flush=True)
    # also include non-FINAL winning? Use FINAL as source of truth (1 per sym_side, bh+gain)
    for f, sym_side in filtered:
        run_one(sym_side)
    print("[365D] All done shard", flush=True)
