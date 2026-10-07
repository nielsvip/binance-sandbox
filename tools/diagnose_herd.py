#!/usr/bin/env python3
import sys, pathlib, time, json
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

ROOT = pathlib.Path.home() / "binance-sandbox"

print("[DIAG] Starting herd diagnostics", flush=True)
t0 = time.time()

# Step 1: Read queue
print(f"[DIAG] Reading queue...", flush=True)
queue_path = ROOT / "SPREADSHEETS" / "V15_SERVER_QUEUE_S1.txt"
order = [l.strip() for l in queue_path.read_text().splitlines() if l.strip()]
print(f"[DIAG] Queue read: {len(order)} symbols in {time.time()-t0:.2f}s", flush=True)

# Step 2: Scan local done (progress JSON)
print(f"[DIAG] Scanning progress JSON files...", flush=True)
t1 = time.time()
local_done = set()
for p in list(pathlib.Path(ROOT / "data/reports/lifecycle_pilot").glob("*_pilot_progress.json")) + list(pathlib.Path(ROOT / "data/reports/lifecycle_pilot").glob("*_v14_progress.json")):
    try:
        j = json.loads(p.read_text())
        if j.get("done"):
            sym = p.name.split("_pilot")[0].split("_v14")[0]
            local_done.add(sym)
    except:
        pass
print(f"[DIAG] Scanned progress: {len(local_done)} done in {time.time()-t1:.2f}s", flush=True)

# Step 3: Check NPZ files
print(f"[DIAG] Checking NPZ files...", flush=True)
t2 = time.time()
npz_ok = 0
npz_missing = 0
for s in order[:10]:  # Just check first 10
    npz = pathlib.Path(f"{ROOT}/backtest_v8/indicators/{s.rsplit('_',1)[0]}.npz")
    if npz.exists() and npz.stat().st_size >= 100_000:
        npz_ok += 1
    else:
        npz_missing += 1
print(f"[DIAG] NPZ check (first 10): {npz_ok} ok, {npz_missing} missing in {time.time()-t2:.2f}s", flush=True)

print(f"[DIAG] Total: {time.time()-t0:.2f}s", flush=True)
