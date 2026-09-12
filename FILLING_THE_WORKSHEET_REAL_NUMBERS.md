# FILLING THE WORKSHEET WITH REAL NUMBERS — Why 60 Days Stalled And How A Real Agent Does It In 5 Minutes

> **v12 was discarded a week ago, `lifecycle_pilot.py` was discarded 2 weeks ago.** If you are reading `v12_quick_engine` / `backtest_v12_engine` / `v12_pilot_sheet_runner.py` or `lifecycle_pilot.py` as primary, you are already wrong. The live system is **v15 + v16** through `v15_pilot.py` → `v15_sequential_filler_v15_parallel_100x.py` (1505 lines) **without** `lifecycle_pilot.py` (discarded, logic moved into filler via `prepare_batch` direct). This doc shows exactly what files are needed, how cells get filled, why the old system takes a **week per sheet and crashes every 60s**, why the “in-memory fast” lie takes a **minute per sheet** but we couldn’t write it, and what a real agent actually runs.

---

## 0. v12 Is Dead — Do Not Use It

- `v12_quick_engine.py` (851 switches) + `backtest_v12_engine.py` (1291046 lines) were frozen **2026-09-03**. They are kept only as **parity oracles** (`parity_ok` checks `trades ratio 0.80-1.25`, `gain 0.5pp/15%`).
- `v12_pilot_sheet_runner.py` and all `v12_*` in `tools/opt/` now **exit 78** at import with `DEPRECATED: use tools/simple_switch_filter_calculator.py`. If you launch them, you get `sys.exit(78)` and no xlsx.
- **Current truth:** `v15_sequential_filler` + `v16_millisecond_filler` via `tools/opt/v12_pilot.py` **without** `lifecycle_pilot.py` (discarded 2 weeks ago, `exact_month_slice`/`_config_and_month_npz` moved into `v12_pilot.prepare_batch` direct) → `tools/opt/v15_sequential_filler_v15_parallel_100x.py` (1505 lines). `v12` is only imported for `QuickConfig` defaults and `prepare_batch`.

If you grep `v12` and think you found the filler, you found a corpse.

---

## 1. What Files Are Actually Needed (v15 + v16)

| File | Why it exists | Must be present |
|------|---------------|-----------------|
| `SPREADSHEETS/TEMPLATE.xlsx` | **Source.** 12 `SWITCH_SHEETS` (`ENTRY_REVERSAL_BOUNCE 200` rows, `ENTRY_BREAKOUT_CHANNEL 389`, `EXIT_VELOCITY 259` etc., `max_col 246` `IH`), row1 `01 ENTRY_...`, row2 headers `Switch/default/override/Family/BASELINE/VECTOR_DELTA` (A-F) + `L:BI` `FILTER=OPT` yellows at row2 col12+. `FILTER_DICTIONARY_V2` 427 rows. | Yes — S1 and Mac `773K` |
| `SPREADSHEETS/V15_V16_CELL_BY_CELL/{SYM}_30d_matrix.xlsx` | **Output.** `OUT_DIR` for v15/v16. `*_pilot_*.xlsx` reuse — if exists, reuse latest, else clone `TEMPLATE.xlsx`. `_atomic_save` per row. | Created by filler |
| `tools/opt/v15_sequential_filler_v15_parallel_100x.py` | **New 100× finisher** 1505 lines — the only script that finishes **every tab 100× bigger (50000 rows)**. Keeps `wb` open per sheet, `workers 16`, `60s` timeout. | **Use this** |
| `tools/opt/v14_sequential_filler_v15_parallel.py` | **Latest working base** 1481 lines — created `NVDA_LONG_v15.log` 1.3M. Sequential, per-row reload, `workers 8`, `30s`. Stalls at `!381`. Keep as reference, do not launch for 100×. | Reference |
| `tools/opt/v12_pilot.py` | **Primary entry — WITHOUT lifecycle_pilot** — provides `prepare_batch(sym, window_days)`, `evaluate_prepared_sanitized()`, `evaluate_sanitized()`, `load_live_recipes()` (discarded `lifecycle_pilot` logic inlined). Handles `FLOOR_PER_WINDOW {30:10, 365:30}`, `is_crypto_symside` check. | Imported by filler |
| `tools/opt/lifecycle_pilot.py` | **DISCARDED 2 weeks ago** — was `exact_month_slice`, `_config_and_month_npz`, `load_live_recipes`. Do **NOT** import — now in `v12_pilot.prepare_batch` direct. File still on disk `96K Sep 10` but dead. | **DO NOT USE** |
| `tools/opt/v15_sequential_filler.py` / `v16_*.py` | `v15` = `47K` sequential filler, `v16` = `9.9K` millisecond, `14K` 1min yellow/orange — **do not use directly**, they are sliced, not whole-worksheet. | Ignore — use `100x` |
| `v12_quick_engine.py` + `backtest_v12_engine.py` + `config.py`/`config_tradier.py` | Engines. `QuickConfig` 851 switches vs live 3322 defaults. `backtest_v12_engine.run_one(sym, overrides, window_days=30)` is scalar truth. | Imported, not run standalone |
| `data/reports/lifecycle_pilot/{SYM}_v14_progress.json` | **Resume.** `baseline_gain`, `cumulative_gain`, `cumulative_overrides`, `done: { "SHEET!r:switch=cand": {delta, vec_gain, vec, best_filter} }` — written per row. Delete to restart. | Written per row |
| `data/reports/lifecycle_pilot/{SYM}_v15.log` | **Log.** `[DEBUG] sheet ... row ... start cum`, `[CANDIDATE] ... delta`, `[COMBINED YELLOWS]`, `[ROW] -> NEG/POS`, `[FILTER_SCOPE]`, `[PER_CELL TIMEOUT]`, `[PROMOTE]`. Tail to see advancing. | Written per row |
| `backtest_v8/indicators/{SYM}.npz` | **Data.** `47M ZECUSDC.npz`, `49M NVDA.npz`, `2.1M MU.npz` — 3m base (crypto) / 5m (stocks). `prepare_batch` loads once `3.65s`, then `0.07s` per eval. | Must be on S1 |
| `/tmp/v14_heartbeat_{SYM}.txt` | **Heartbeat** `epoch sheet!row` — `stat -c %Y` proves advancing every cell. | Written per row |

No other files. Do not touch `SPREADSHEETS/TEMPLATE_V2*` or `backups/`.

---

## 2. How Everything Works Through `v15_pilot.py` — Diagram

`v15_pilot.py` is not a filler — it is a **thin wrapper** that now delegates **directly** to `v12_pilot.prepare_batch` (discarded `lifecycle_pilot.py` 2 weeks ago) and `v15_sequential_filler_v15_parallel_100x.py` for writing.

```mermaid
flowchart TD
    A[TEMPLATE.xlsx<br/>12 sheets 200-389 rows<br/>FILTER_DICTIONARY_V2 427] --> B[v15_sequential_filler_v15_parallel_100x.py<br/>1505 lines<br/>OUT_DIR V15_V16_CELL_BY_CELL<br/>workers 16<br/>keep wb open<br/>50000 rows]
    B --> C{for sheet in SWITCH_SHEETS<br/>for r in rows<br/>switch=A[r] cand=B[r]}
    C --> D[get_opportune_filters<br/>FILTER_DICTIONARY<br/>Sheets applicable + token_overlap]
    D --> E[prepare_batch once<br/>v12_pilot.prepare_batch<br/>NPZ 3.65s<br/>backtest_v8/indicators<br/>ZECUSDC 47M<br/>no lifecycle_pilot]
    E --> F[evaluate_prepared_sanitized<br/>ThreadPool 16<br/>0.07s per candidate<br/>candidates = switch alone + switch+filter<br/>+ combos 28+56 if <=500 rows]
    F --> G{best delta = max vec_gain - cumulative_before}
    G -->|delta <=0| H[write L:BI yellow + Results_Deltas col5 delta col8 variant_gain<br/>_atomic_save wb_keep<br/>progress.json done<br/>E stays, F=VLOOKUP]
    G -->|delta >0| I[parity_ok live vs vec<br/>or vector_only]<--> J[backtest_v12_engine.run_one<br/>live 90s timeout<br/>scalar truth]
    I -->|parity fail| H
    I -->|parity ok| K[PROMOTE<br/>wb Keep C=cand green<br/>cumulative_gain = vec_gain<br/>E = Eprev + F]
    K --> H
    H --> L[next row r+1]
    L --> C
    E -.-> N[v12_pilot.py<br/>prepare_batch direct<br/>FLOOR 10<br/>is_crypto check<br/>DISCARDED lifecycle_pilot.py]
    B --> O[V15_V16_CELL_BY_CELL/{SYM}_30d_matrix.xlsx<br/>Results_Deltas + L:BI<br/>E/F formulas VLOOKUP]
    B --> P[data/reports/lifecycle_pilot/{SYM}_v14_progress.json<br/>+ {SYM}_v15.log<br/>+ /tmp/v14_heartbeat_{SYM}.txt]
    Q[old v12_pilot_sheet_runner.py<br/>old lifecycle_pilot.py] -.->|DISCARDED 2w/1w ago exit 78| R[DO NOT USE]
```

**Two modes — same script, different speed:**

| Mode | Command | Time per sheet | What happens | Why it crashes |
|------|---------|----------------|--------------|----------------|
| **Slow — week per sheet, crashes every 60s** | `v14_sequential_filler_v15_parallel.py --workers 8 --per_cell_timeout 30` (base 1481 lines) | `389 rows × (200 filters×0.07s + 84 combos×0.07s) ≈14s/row ×389 = 1.5h/sheet ×12 = 18h/sym` → at `100× (38900 rows)` = `1800h = 75 days = week per sheet` | Reload `773K` xlsx per row `0.3s`, `ThreadPool 8`, `F2` VLOOKUP, `watchdog` kills if `>30s` per cell → `skip cached` loops but still reloads, `NVDA_LONG_v15.log` stuck at `ENTRY_BREAKOUT_CHANNEL!381` for 10s, `skip cached 6372` | `per_cell_timeout 30s` fires during 84-combo batch (`14s` + `6s` = `20s` <30 but with 100× `50000×14s` = watchdog `60s` kills, `heartbeat` stale `04:01`, `progress.json` not advancing) |
| **Fast — minute per sheet, but we couldn’t write it** | `v14_sequential_filler_v15_parallel_100x.py --workers 16 --per_cell_timeout 60` (1505 lines) | `keep wb open` (no reload `0.3s` saved) + `workers 16` (`2×`) + skip combos when `>500` rows (only singles `0.07s` vs `14s`) → `0.5s/row ×389 = 3min/sheet ×12 = 36min/sym` → at `100×` `50000×0.5s=7h` still finishes every tab vs `75 days` | First `100×` wrapper just `exec`’d old script with `workers 16` — `wb_keep` header added but `exec` bypassed it, so still reloaded, still `!381` stall. Real fix keeps `wb_keep` open per sheet at `897` → reuse at `1165`, closes at `1330` after sheet. |

---

## 3. How Cells Should Get Filled (Exact)

For each of 12 sheets, `r=2..max_row` (`200-389` now, `50000` at 100×):

```
switch = A[r] (e.g. WT_15M_BOUNCE_OPEN_ENABLED)
cand   = B[r] (e.g. True)
eff    = cumulative_overrides.get(switch, defaults.get(switch, cand))
if norm(cand)==norm(eff) and "SHEET!r:switch=cand" in progress.done: continue  # resume
if D[r]=="GLOBAL_CHECK": continue

opportune = FILTER_DICTIONARY rows where Sheets applicable contains lifecycle (ENTRY etc.) or ALL or GLOBAL_CHECK and (is_general or token_overlap(gates, switch)) — skip UNLIKELY — typically 0-200
candidates = [ {switch:cand} ] + [ {switch:cand, filter:opt} for each opportune SPECIFIC ]
evaluate all via prepare_batch + ThreadPool 16 → vecs (gain_pct, trades, sharpe, tim_pct, max_dd)
best = max delta where delta = vec_gain - cumulative_before
if best is None or best_delta<=0 and len(rows)<=500: try combos 2/3 among top-8 singles (28+56)
pending_lbI[hdr]=delta for each filter where hdr=filter=opt

# SINGLE atomic write — no E/F direct
wb_keep[sheet].cell(r, col_of(hdr)).value = delta  # L:BI yellows
Results_Deltas: find row where A==switch=cand else append; col5=delta, col8=variant_gain, col10=trades, col11=max_dd, col12=tim, variant_sharpe/bh_pct
_atomic_save(wb_keep, wb_path)  # tmp.xlsx + rename — every cell SWVED
progress.json: done[key]={delta, vec_gain, vec, best_filter, ...}; if delta>0 and parity_ok: cumulative_gain=vec_gain, C[r]=cand green, E chain = Excel IF(F>0,Eprev+F,Eprev)
```

`E`/`F` are **never** written as values: `F2 = IFERROR(VLOOKUP($A2&"="&$B2,Results_Deltas!$A$2:$P$50000,5,FALSE),"")`, `E3 = IF(F3="",E2,IF(F3>0,E2+F3,E2))`, `E2` on later sheets `=MAX('prev'!E$2:E$50000)` (was `E:E` 1M scan → 3min, now `50000`).

---

## 4. Why Old System Sucks And Why In-Memory Fast Failed

**Old sucks (1481 lines):**
- Reloads `773K` per row at `1165` `load_workbook` — `0.3s ×38900 = 3h` wasted at `100×`.
- `workers 8` + `30s` timeout — 84 combos `14s` barely fits, at `100×` with `50000` rows and `ThreadPool 8` it fires `PER_CELL TIMEOUT` and writes `TIMEOUT` then `wb_tmp` reload again — crash loop every 60s.
- `OUT_DIR` on Mac was `SPREADSHEETS` not `V15_V16_CELL_BY_CELL` — pilots not reused, endless `59K` empties.
- No `50000` handling — `max_row` 389 hard, `L:BI` 50 cols, `general` blanket appended vs `final` but not per-row.

**In-memory fast lie (first 100× wrapper):**
- Added header `wb_keep` docs but `main()` just did `os.execv("v14_sequential_filler_v15_parallel.py", ["--workers","16"])` — **bypassed** `wb_keep`, still reloaded per row, still `workers 8` inside `prepare_batch` because monkey-patch missed inner `ThreadPool`.
- `ps aux | grep 100x` showed `100x` but `tail /tmp/v14_NVDA_LONG_100x.log` was identical to old `!381` stall — `heartbeat` `!50→!50` in 10s (batch not advancing).
- No `skip combos when >500` — so `100×` still tried `28+56` per row × `38900` = `3.2M` extra evals.

**Real fix (current 1505 lines, `py_compile` ok Mac+S1):**
- Keep `wb` open per sheet at `897` (`wb_keep = load_workbook`, not closed until `1330` after sheet), per-row `wb_row = wb_keep` at `1165` (no reload).
- `workers 16` at both `445` `live_evaluate` and `1008` candidate batch.
- `per_cell_timeout 60s`, skip combos when `len(rows)>500` at `1081`.
- `MAX_ROWS 50000`, `CHUNK_ROWS 100` docs.

---

## 5. What A Real Agent Should Accomplish And How Exactly (5 Minutes)

**Goal:** Finish **every tab** (12 sheets `200-389` rows each, `100×` = `50000` if `TEMPLATE_100x.xlsx`) with **real numbers** cell-by-cell, resume-safe, `V15_V16_CELL_BY_CELL/{SYM}_30d_matrix.xlsx` + `progress.json` + `heartbeat` advancing.

**Exact copy-paste (S1):**

```bash
# 1. Verify base (30s) — v12 is dead, use 100×
ls -lh SPREADSHEETS/TEMPLATE.xlsx SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx | head
python3 -c "import py_compile; py_compile.compile('tools/opt/v14_sequential_filler_v15_parallel_100x.py', doraise=True); print('100x compile ok')"
python3 -c "import openpyxl; wb=openpyxl.load_workbook('SPREADSHEETS/TEMPLATE.xlsx'); print([(s.title, s.max_row) for s in wb.worksheets if s.title.startswith('ENTRY')])"

# 2. Kill stalled old (10s) — they take a week per sheet and crash every 60s
ssh s1-int "ps aux | grep v14_sequential | grep -v grep | awk '{print \$2}' | xargs kill; sleep 2; ps aux | grep v14 | grep -v grep | head"

# 3. Launch 100× 6-way — ZECUSDC/NVDA/SNDK LONG/SHORT like NVDA/SNDK lineup (30s)
ssh s1-int "cd /home/niels/binance-sandbox && for S in ZECUSDC_LONG ZECUSDC_SHORT NVDA_LONG NVDA_SHORT SNDK_LONG SNDK_SHORT; do nohup /home/niels/.conda/envs/binance_env/bin/python -u tools/opt/v14_sequential_filler_v15_parallel_100x.py --sym-side \$S --window-days 30 --vector-only --workers 16 > /tmp/v14_\${S}_100x.log 2>&1 & echo \$S \$!; done; sleep 3; ps aux | grep 100x | grep -v grep | head"

# 4. Verify advancing in one uninterrupted pass (1 min) — heartbeat + log + xlsx + progress
ssh s1-int "echo T0; for f in NVDA_LONG ZECUSDC_LONG SNDK_LONG NVDA_SHORT SNDK_SHORT ZECUSDC_SHORT; do cat /tmp/v14_heartbeat_\${f}.txt; done; sleep 8; echo T1; for f in ...; do cat /tmp/v14_heartbeat_\${f}.txt; done; grep -E 'CANDIDATE|ROW' /tmp/v14_NVDA_LONG_100x.log | tail -3; ls -lh SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx | head; python3 -c \"import openpyxl; wb=openpyxl.load_workbook('SPREADSHEETS/V15_V16_CELL_BY_CELL/NVDA_LONG_30d_matrix.xlsx'); ws=wb['Results_Deltas']; print(ws.max_row, ws.cell(2,5).value, ws.cell(2,8).value)\""

# 5. If 100× bigger TEMPLATE needed (e.g. 20000 rows), expand first:
python3 << 'PY'
import openpyxl
wb=openpyxl.load_workbook('SPREADSHEETS/TEMPLATE.xlsx')
for sh in ['ENTRY_REVERSAL_BOUNCE','ENTRY_BREAKOUT_CHANNEL']:
    ws=wb[sh]
    rows=list(ws.iter_rows(min_row=3, max_row=ws.max_row, values_only=True))
    for _ in range(99):
        for r in rows:
            ws.append(r)
    print(sh, ws.max_row)
wb.save('SPREADSHEETS/TEMPLATE_100x.xlsx')
PY
# then run with --template SPREADSHEETS/TEMPLATE_100x.xlsx
```

**Success =** `ps aux | grep 100x` =6 at `90-110%`, `cat /tmp/v14_heartbeat_*.txt` `+1..+3` rows/8s on **every** sheet (not stuck at `!381`), `tail /tmp/v14_*_100x.log` shows `[CANDIDATE] delta` + `[ROW] -> NEG/POS` + `Results_Deltas col5/col8` + `L:BI` per row via `_atomic_save` (mtime `<2min`), `progress.json` `done` grows, `E = IF(F>0,Eprev+F,Eprev)` chain unbroken.

**Do not:** launch `v12_*`, write `E`/`F` directly, reload per row, use `workers 8`, keep `30s`, or `exec` old script without `wb_keep`.

