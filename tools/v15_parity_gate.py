#!/usr/bin/env python3
"""v15_parity_gate — enforce vector<->live-faithful parity on the 365D-certified set.

After tools/confirm_365d.py --all has certified the 365D-positive winners into
data/confirmed_365d.json, this re-runs backtest_v12_engine parity (tools/v15_parity_check)
on each certified sym_side and REVOKES (via the sanctioned tools/confirm_365d.py --revoke)
any that fail parity — so only 365D-pos AND parity-clean settings remain live-eligible.
A sym_side whose NPZ is absent on this box (wrong venue) is skipped, not revoked (the
owning box gates it). Prints a PASS/FAIL/SKIP line per sym_side and a summary.
"""
import argparse, json, os, pathlib, subprocess, sys

ROOT = pathlib.Path.home() / "binance-sandbox"
if not ROOT.exists():
    ROOT = pathlib.Path("/Users/niels/Documents/binance")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("BASE_PATH", str(ROOT))
CONFIRM = ROOT / "data" / "confirmed_365d.json"


def npz_here(symside):
    sym = symside.split("_")[0]
    p = ROOT / "backtest_v8" / "indicators" / f"{sym}.npz"
    if p.exists() and p.stat().st_size > 100000:
        return True
    p2 = ROOT / "backtest_v8" / "indicators" / f"{sym.replace('USDC','').replace('USDT','')}.npz"
    return p2.exists() and p2.stat().st_size > 100000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true", help="report only, do not revoke")
    args = ap.parse_args()
    if not CONFIRM.exists():
        print("no confirmed_365d.json — nothing to gate"); return
    data = json.loads(CONFIRM.read_text())
    from v15_parity_check import check as parity_check
    n_pass = n_fail = n_skip = 0
    for key in sorted(data):
        if not npz_here(key):
            print(f"PARITYGATE {key} SKIP (no local NPZ — other venue)", flush=True); n_skip += 1; continue
        try:
            ok = parity_check(key, args.window_days)
        except Exception as e:
            ok = False
            print(f"PARITYGATE {key} FAIL (check error {str(e)[:100]})", flush=True)
        if ok:
            n_pass += 1
        else:
            n_fail += 1
            if not args.dry_run:
                subprocess.run([sys.executable, str(ROOT / "tools" / "confirm_365d.py"), "--revoke", key], timeout=60)
                print(f"PARITYGATE {key} REVOKED (parity fail)", flush=True)
    print(f"PARITYGATE SUMMARY pass={n_pass} fail={n_fail} skip={n_skip} (of {len(data)} certified)", flush=True)


if __name__ == "__main__":
    main()
