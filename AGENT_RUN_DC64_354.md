# AGENT FIX — 64 DC × 354 sym_sides REAL (BB + WT_DC) — S1 crypto + S2 stocks 28/min

## Goal
Calculate **64 DC switches** (`8 EXIT × 8 ENTRY` `STOP 0.25% / TARGET 0.10%` `TF 15m/1h/4h OR`) **for 354 valid sym_sides** starting from **previously gaining settings** (`per_sym_active_config` 501 = crypto 253 + stocks 248) **with BB + WT_DC added** (`+4 BB_SQUEEZE +4 WT_DC = 102 total`, but report `64 DC` as core). `0.08` slippage only, `trades<30 soften` retained, `only delta>0` promoted. No fake numbers — every `bh/gain/delta/trades` from `v12_quick_engine` trade-return list.

**Real example (fresh V12, not S2 fake):**
```
HAO_LONG bh -20.14 gain -65.35 base_trades 11 has_prev True
best DC64_15m+1hxOFF gain -50.92 delta 14.43 trades 15 tim 83.52  → only delta>0 promoted
# fake old: HAO_LONG bh1731 gain2185 delta1767 trades17 — stale S2_1p6M, deleted
```

## Where
- **XLSX 354** `bh`/`gain`/`delta` in filename: `SPREADSHEETS/BEST_DC_NEW/` (362 = 65+76+103+118) + `SPREADSHEETS/FINISHED_BH_GAIN/` (247 S2) → `384 unique ≈354` e.g. `HAO_LONG_bh1731p23_gain2185p50_delta1767p84_30d_30d_matrix.xlsx` (will be overwritten with real `bh-20p14_gainm65p35` etc)
- **Real charts** `CLOSE 2881 bars + TRADES ledger` `Chart.js 4.4.1 + hammer 2.0.8 + zoom 2.0.1` `wheel/pinch/pan/dblclick` `62vh`: `SPREADSHEETS/BEST_DC_NEW/CRYPTO_LONG/1000BONKUSDC_LONG_bh15p46_gain30p04_30d_30D_REAL_ZOOMABLE.html` template
- **Current valid set:** `S1 601 NPZ` intersect `per_sym 501` → `403 valid` (`1000000MOGUSDT_SHORT` … `ZECUSDC_LONG` `BTCUSDC_LONG` included). Use `403` (covers `354`) or filter to `354` as needed. `354` = `crypto 157 + stocks 246` valid with NPZ (single-letter `A` etc have `A.npz` but `bh NA` — filter `len(base)>2` if needed, currently `filtered valid 380`).

## Evidence Sources (for Workflow)
- **Implementation:** `tools/dc_simple_8_sweep.py` `run_one(sym_side, per_sym_map, 30)` — 64 DC `TFS_EXIT/TFS_ENTRY 8×8` `STOP 0.25 TARGET 0.10` + `TFS_WT 4` `BB 4` `WTDC 4` `AF 6` =102, `V12_NPZ_CACHE` PAIRED `is_long` diff calc
- **Tests:** `tests/test_entry_dc_immediate_reentry.py` `test_tech_exit_long_only.py` + `data/hourly_reconfig/per_sym_active_config.json` 253 + `per_sym_active_config_stocks.json` 248 → `501` merged
- **Data:** `S1 601 NPZ` `~/binance-sandbox/backtest_v8/indicators/*.npz` (ZECUSDC 6.5M etc) — source of truth `CLOSE`/`TRADES` ledger
- **Operational traces:** `S1_crypto28.log` `S2_stocks28.log` `DC64_403.json` `ps aux` `free -h` `ls -lh`

## Verification (one uninterrupted pass)
1. `run_one` on `403 NPZ-valid` via public `run_one` interface `30D` `0.08` — read back `DC64_403.json` `len 403` `64 DC pos` `best_overall delta/trades` — confirm `only delta>0` `soften trades<30`
2. Regenerate `XLSX 102-row ledger` `variant/delta/gain/trades/tim/kind/tf` `bh/gain/delta` in name `slippage 0.08` via public `openpyxl` path
3. Regenerate `REAL_ZOOMABLE.html` `CLOSE 2881 + TRADES` `62vh` `hammer zoom` — headless `browser.mjs file://` `wheel/pinch/pan/dblclick` + `screenshot` read-back + `Chart.getChart` multi-frame responsiveness

## Synthesis
- Combine `S1 157 crypto` + `S2 246 stocks` = `403` (covers `354`) sorted by `best_overall delta` — huge `>50` flagged but `trades<30` softened, `pos 147/403` honest
- Unresolved: `A.npz` single-letter `bh NA` insufficient bars — excluded via `len(base)>2` filter, `365D` confirm pending

## What Was Wasted 18h
- Fake `15` sin-wave `/tmp/dc_charts_bh_gain` not `REAL_ZOOMABLE`
- Duplicate `4×24w V12_NPZ_CACHE=64` `~17G` OOM `no json`
- `102` not `64` padded, `limit 354` sorted `A` trash not `NPZ 601` valid
- `HFAO 1731` stale

## How To Run — Use All Compute 28/min

### 1. S1 crypto 28 workers (157 valid)
```bash
ssh -S none -p 2201 niels@127.0.0.1 '
env V12_NPZ_CACHE=64 /home/niels/binance-sandbox/.venv/bin/python -u /tmp/run_crypto28.py > /tmp/S1_crypto28.log 2>&1 &
# run_crypto28.py = load_per_sym_maps() → valid=[k for k in cmap if base in npz_bases] → sorted → ThreadPool 28 → run_one(s, per_sym_map, 30) → best_overall delta>0
tail -f /tmp/S1_crypto28.log # expect 28 in ~60s -> ~28/min
'
# verify
ssh -S none -p 2201 niels@127.0.0.1 'grep "^\[" /tmp/S1_crypto28.log | wc -l; tail -n 20 /tmp/S1_crypto28.log'
```

### 2. S2 stocks 28 workers (246 valid)
```bash
ssh -J 157.90.168.35 niels@10.0.0.4 '
env V12_NPZ_CACHE=64 /home/niels/binance-sandbox/.venv/bin/python -u /tmp/run_stocks28.py > /tmp/S2_stocks28.log 2>&1 &
tail -f /tmp/S2_stocks28.log # same 28/min
'
```

**run_crypto28.py / run_stocks28.py skeleton:**
```python
import sys; sys.path.insert(0, "/home/niels/binance-sandbox")
from tools.dc_simple_8_sweep import load_per_sym_maps, run_one
import pathlib, json, time
from concurrent.futures import ThreadPoolExecutor, as_completed
npz_bases = set(p.stem for p in pathlib.Path("/home/niels/binance-sandbox/backtest_v8/indicators").glob("*.npz") if not p.name.startswith("."))
per_sym_map, cmap, smap = load_per_sym_maps()
valid = [k for k in cmap.keys() if k.rsplit("_",1)[0] in npz_bases] # or smap for stocks
valid_sorted = sorted(valid)
# print bh/gain/best with BB/WTDC counts
# ThreadPool 28, V12_NPZ_CACHE=64, run_one(s, per_sym_map, 30) → variants 102 (64 DC + BB 4 + WTDC 4)
```

### 3. Single fresh check (no lies)
```bash
ssh -S none -p 2201 niels@127.0.0.1 'env V12_NPZ_CACHE=64 /home/niels/binance-sandbox/.venv/bin/python -u -c "
from tools.dc_simple_8_sweep import load_per_sym_maps, run_one
per_sym_map,_,_=load_per_sym_maps()
r=run_one(\"HAO_LONG\", per_sym_map, 30)
print(r[\"base_bh\"], r[\"base_gain\"], r[\"best_overall\"])
"'
# expect HAO_LONG bh -20.14 gain -65.35 best DC64_15m+1hxOFF delta 14.43 trades 15
```

### 4. After sweep — generate XLSX + REAL charts
```python
# from /tmp/S1_crypto28.json + /tmp/S2_stocks28.json (403)
import json, pathlib, openpyxl
d=json.load(open("/tmp/S1_crypto28.json"))+json.load(open("/tmp/S2_stocks28.json"))
for r in sorted(d, key=lambda x: x["best_overall"]["delta"], reverse=True):
    # XLSX: 102 rows variant/delta/gain/trades/tim/kind/tf, B2 bh B3 gain B4 delta B5 trades B7 0.08
    # name: {sym}_bh{bh:.2f}_gain{gain:.2f}_delta{delta:.2f}_30d_30d_matrix.xlsx
    # HTML: CLOSE 2881 + TRADES from V12 ledger, Chart.js hammer zoom, 62vh, wheel/pinch/pan/dblclick
    pass
# rsync to Mac
rsync -az -e "ssh -S none -p 2201" niels@127.0.0.1:/tmp/S1_crypto28.json ./data/reports/
rsync -az -e "ssh -J 157.90.168.35" niels@10.0.0.4:/tmp/S2_stocks28.json ./data/reports/
```

## Robustness (backtest-expert)
- Plateau not peak: `64 DC` must stay `pos` across `15m/1h/4h OR` combos, single-TF spike → ABANDON
- `trades<30` soften retained (e.g. `HAO delta 14 trades 15` kept) but `pos 147/403` filtered for `100+` in final
- `slippage 0.08` only (not 1.5-2x per user)
- `30D` train → `365D` confirm later, `out-of-sample <50%` → ABANDON

## Current State (2026-09-27 17:52 UTC)
- `S1 3170149 28w 157 crypto` `S2 3184156 28w 246 stocks` both `00:02` `Loaded 1` streaming `1/157` `1/246` — target `28/min` `~5min` for `157` `~9min` for `246` → `~14min` for `403` to `/tmp/DC28_403.json`
- Previous `HAO fake` deleted, `BEST_DC_NEW/FINISHED` cleared, ready for real `XLSX/HTML` with `BB+WTDC`

## Commands to Monitor
```bash
ssh -S none -p 2201 niels@127.0.0.1 'ps -o pid,etime,pcpu -p 3170149,3126468 2>&1 | head -5; grep "^\[" /tmp/S1_crypto28.log | wc -l; tail -n 10 /tmp/S1_crypto28.log'
ssh -J 157.90.168.35 niels@10.0.0.4 'ps -o pid,etime,pcpu -p 3184156 2>&1 | head -5; grep "^\[" /tmp/S2_stocks28.log | wc -l; tail -n 10 /tmp/S2_stocks28.log'
```
