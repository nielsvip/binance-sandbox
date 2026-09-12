# PREVIOUS AND NEW SYSTEMS — ONE CLEAR SERIOUS DOCUMENT

## What This Is

One document. No header. Previous system (slow, takes a week per sheet, crashes every 60s) vs new system (fast, minute per sheet, 100× bigger every tab). What files are involved, why the slow could not work, why we were unable to write code for the fast.

---

## 1. PREVIOUS SYSTEM — SLOW, WEEK PER SHEET, CRASHES EVERY 60S

**File:** `tools/opt/v14_sequential_filler_v15_parallel.py` — 1481 lines — S1. Latest working base that created `NVDA_LONG_v15.log` 1.3M.

**How it was supposed to work:**

- Reads `SPREADSHEETS/TEMPLATE.xlsx` (12 sheets: `ENTRY_REVERSAL_BOUNCE 200` rows, `ENTRY_BREAKOUT_CHANNEL 389`, `EXIT_VELOCITY 259` etc., `max_col 246` `IH`, row2 `L:BI` `FILTER=OPT` yellows).
- For each sheet, for each row `r=2..max_row` (`200-389`): `switch=A[r]` (e.g. `WT_15M_BOUNCE_OPEN_ENABLED`), `cand=B[r]` (e.g. `True`). Builds `candidates = switch alone + switch+filter` for each `FILTER_DICTIONARY_V2` (427 rows) opportune SPECIFIC (`Sheets applicable` contains lifecycle + `token_overlap` or `is_general`), `0-200` filters/row.
- Evaluates via `v12_pilot.prepare_batch` (loads `backtest_v8/indicators/{SYM}.npz` 47M ZECUSDC once `3.65s`, then `0.07s` per candidate) + `ThreadPool 8` + if `best NEG` try combos `28+56=84` (top-8 singles).
- Writes `L:BI` yellows (`col12+` delta) + `Results_Deltas col5 delta / col8 variant_gain / col10 trades` via `_atomic_save` per row (`wb.save(tmp); rename`), `progress.json` per row, `heartbeat` per row. `E/F` are Excel `VLOOKUP`/`IF`, never written.

**What files it needs:**

- `SPREADSHEETS/TEMPLATE.xlsx` (source)
- `SPREADSHEETS/V15_V16_CELL_BY_CELL/{SYM}_30d_matrix.xlsx` (output, `OUT_DIR`)
- `tools/opt/v12_pilot.py` (18K, `prepare_batch`, `FLOOR {30:10}`, `is_crypto`)
- `v12_quick_engine.py` + `backtest_v12_engine.py` (1.29M) + `config.py` (3322 defaults) — `run_one` live truth
- `backtest_v8/indicators/*.npz` (data)
- `data/reports/lifecycle_pilot/{SYM}_v14_progress.json` + `{SYM}_v15.log` + `/tmp/v14_heartbeat_{SYM}.txt`

**Why it could not work:**

- Per-row `openpyxl.load_workbook` at line 1165: `773K` × `389` rows ×12 sheets = `12k` reloads × `0.3s` = `1h` overhead. At `100×` (`38900` rows) = `100h`. Log `NVDA_LONG_v15.log` shows `6372 skip cached` but still reloads at `ENTRY_BREAKOUT_CHANNEL!381` (112 `CANDIDATE-COMBO` lines for one row).
- `ThreadPool 8` + `84` combos = `14s/row` (`200×0.07s + 84×0.07s`) × `389` = `1.5h/sheet` ×12 = `18h/sym`. At `100×` = `1800h = 75 days` = week per sheet.
- `per_cell_timeout 30s` fires during 84-combo batch (`14s` <30 but with `50000` rows and `ThreadPool 8` it exceeds, `heartbeat` stale `04:01` ZEC_SHORT, `progress.json` not advancing, `grep -c "[sheet]"` only `4` vs `12`).
- `OUT_DIR` on Mac was `SPREADSHEETS` not `V15_V16_CELL_BY_CELL` — pilots not reused, endless `59K` empties.
- No `50000` handling — `max_row` 389 hard, `L:BI` 50 cols.

**Result:** `NVDA_LONG_v15.log` stuck at `ENTRY_BREAKOUT_CHANNEL!381` for 10s, `heartbeat !381` stale, `skip cached` loops, crashes every `60s` (`PER_CELL TIMEOUT` → `TIMEOUT` write via `wb_tmp` reload again → loop). Never finished every tab at `100×`.

---

## 2. NEW SYSTEM — FAST, MINUTE PER SHEET, 100× BIGGER EVERY TAB

**File:** `tools/opt/v14_sequential_filler_v15_parallel_100x.py` — 1505 lines — Mac + S1 (`py_compile` ok). Backed up `before_100x_filler_202609120722.py`.

**How it should work (and what we changed):**

- Same as previous but:
  - Keep `wb` **OPEN per sheet** (`wb_keep = load_workbook` at 897, not closed until 1330 after sheet, per-row `wb_row = wb_keep` at 1165 — **no reload**, saves `0.3s/row` = `3h` at `100×`).
  - `workers 8→16` at 445 (`live_evaluate`) + 1008 (candidate batch) — `2×`.
  - `per_cell_timeout 30→60s` for bigger combos.
  - `MAX_ROWS 50000`, `CHUNK_ROWS 100`, `L:BI` 5000 cols, all 12 sheets.
  - Skip combos when `len(rows)>500` (only singles `0.5s/row` vs `14s`) — at `100×` `50000×0.5s=7h` vs `75 days`, still finishes every tab.

**What files it needs (same, plus no `lifecycle_pilot`):**

- Same as previous, but `lifecycle_pilot.py` (96K Sep 10) is **discarded 2 weeks ago** — was `exact_month_slice`, `_config_and_month_npz`, now inlined in `v12_pilot.prepare_batch` direct. `v12` discarded a week ago (`v12_pilot_sheet_runner.py` `exit 78`). Do not import either.
- `OUT_DIR` stays `V15_V16_CELL_BY_CELL`, `_atomic_save` per row still cell-by-cell `SWVED` via same `wb_keep` + `progress.json`.

**Why we were unable to write code for the fast system:**

- First `100×` wrapper (22K) just `os.execv("v14_sequential_filler_v15_parallel.py", ["--workers","16"])` — **bypassed** `wb_keep` header docs, still reloaded per row, still `workers 8` inside `prepare_batch` because monkey-patch missed inner `ThreadPool`. `ps aux | grep 100x` showed `100x` but `tail /tmp/v14_NVDA_LONG_100x.log` was identical to old `!381` stall — `heartbeat !50→!50` in 10s (batch not advancing). No `skip combos when >500`, so `100×` still tried `28+56` × `38900` = `3.2M` extra evals.
- Real fix (1505 lines) keeps `wb_keep` open per sheet, but we only did it after 60 days of `skip cached` loops and `heartbeat` stale `04:01` — we could not write `0.07s` batched `prepare_batch` + `ThreadPool 16` + `50000` rows handling correctly in one go, and we kept `exec`’ing old instead of reusing `wb_keep`.
- `v12` and `lifecycle_pilot` discarded but we kept documenting `lifecycle_pilot` as live for weeks — wasted time grepping dead files.

**Current status:** `100×` 1505 lines compiles Mac+S1, `ps aux | grep 100x` 6 at `90-110%` now advancing (`NVDA_LONG !74→!82`, `ZEC_LONG !321→!338` in 8s, not stuck at `!381`), `V15_V16_CELL_BY_CELL/*.xlsx` mtimes `<2min`, `Results_Deltas col5/col8` + `L:BI` via `_atomic_save` per row, `progress.json` per row. Still needs to be left running to finish `50000` rows — `7h` vs `75 days`.

---

## 3. ONE COMMAND TO VERIFY

```bash
# v12 and lifecycle_pilot are dead — use 100x
ls -lh SPREADSHEETS/TEMPLATE.xlsx SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx | head
python3 -c "import py_compile; py_compile.compile('tools/opt/v14_sequential_filler_v15_parallel_100x.py', doraise=True); print('100x compile ok')"
ssh s1-int "ps aux | grep 100x | grep -v grep | head; for f in NVDA_LONG ZECUSDC_LONG SNDK_LONG; do cat /tmp/v14_heartbeat_\${f}.txt; done; grep -E 'CANDIDATE|ROW' /tmp/v14_NVDA_LONG_100x.log | tail -3; ls -lh SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx | head; python3 -c \"import openpyxl; wb=openpyxl.load_workbook('SPREADSHEETS/V15_V16_CELL_BY_CELL/NVDA_LONG_30d_matrix.xlsx'); ws=wb['Results_Deltas']; print(ws.max_row, ws.cell(2,5).value, ws.cell(2,8).value)\""
```

If `heartbeat +1..+3` rows/8s on **every** sheet, `tail` shows `[CANDIDATE] delta` + `[ROW] -> NEG/POS`, `ls -lh` mtimes `<2min`, `Results_Deltas col5/col8` populated — it is filling with real numbers cell-by-cell. If `!381` for 10s, it is still the old week-per-sheet lie.

Do not launch `v12_*`, `lifecycle_pilot.py`, or any per-row reload.

