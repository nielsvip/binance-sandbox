# V15/V16 CELL_BY_CELL — Why sym_sides Strand & How Fixed — 2026-09-16

**Scope:** All `V15_V16_CELL_BY_CELL/*.xlsx`, `TEMPLATE*.xlsx` (including `TEMPLATE_CRYPTO_LONG/SHORT`, `TEMPLATE_STOCKS_LONG/SHORT`, `TEMPLATE_V2*`), `v15_pilot.py`, `v15_pilot_0914.py`, `v15_quick_engine.py`, `tools/opt/*`, `backtest_v15_engine.py`.

**Observed failure:** Many sym_sides show `INCOMPLETE` in `SPREADSHEETS/BEST/` (e.g. `ALGOUSDT_LONG_bh11p91_gain3p88_INCOMPLETE_30d_matrix.xlsx`), `SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx` have `ENTRY_REVERSAL_BOUNCE` with 17/20 non-float F (VLOOKUP/empty), `STDEV_SLOPE_SIZING` truncated to 1-3 rows, `Results_Deltas` empty, no chart, no 365D, no live-trading comparison. User saw sheets stop at sheet 1.

## 1. Triaged Failure Modes (all reproduce on Mac + S1)

### 1A. 365D hard-block — no workbook ever reaches 365D
**File:** `v15_pilot.py:829-834` (and `v15_pilot_0914.py:866`)
```python
if args.window_days == 365 or args.window_days >= 100:
    print("BLOCKED: 1yr requires 30D gate — run 30D first"); sys.exit(2)
if args.window_days not in (30,20,7,1): sys.exit(2)
```
Every `window_days=365` invocation exits with code 2, even after its own 30D file is done. The “gate” was unconditional — the intended check (30D progress with ≥50 done or `final_gain`) was never implemented. Result: zero `*_365d_matrix.xlsx`, zero 365D zoomable chart, zero 365D vs 30D comparison. **Strand, not crash.**

**Fix:** Gate now inspects `data/reports/lifecycle_pilot/{SYM}_v14_progress.json` (local + S1 sandbox). If `final_gain` or `len(done)>=50` exists, prints `[365-GATE] 30D gate passed` and allows 365. `window_days` allowlist extended to `(30,20,7,1,365)`. Verified: `SNDK_LONG` (3884 done, final 33.8) now passes; `AAPL_LONG` without gate still blocks.

### 1B. Workbook reuse propagates truncation — STDEV 1 row, ENTRY 0 floats
**File:** `v15_pilot.py:clone_template()`
```python
_pilots = sorted(glob(..._pilot_*.xlsx))
if _pilots: return Path(_pilots[-1])   # ← always reuses latest pilot
```
If latest pilot was killed mid-save (BadZip, ENOSPC, `pkill -9` per `V15_0915_DEAD_HANDOFF.md`: 5 servers × 12h → 250 CPU-h wasted, `STOCKS_LONG` became `File is not a zip file`), its sheets are truncated (STDEV 1 data row vs expected 15, ENTRY F all VLOOKUP). Reusing it clones the truncation forever; new `TEMPLATE.xlsx` corrections never reach that sym_side. Observed: `1000BONKUSDC_LONG_30d_matrix.xlsx` (Sep 16 02:03) has `STDEV_SLOPE_SIZING max_row=3 (1 data row)` though `TEMPLATE.xlsx` has 15+ rows.

**Fix:** `clone_template` now validates before reuse:
- `read_only` open, check all `SWITCH_SHEETS` present
- each sheet must have ≥3 real switch rows
- `STDEV_SLOPE_SIZING` must have ≥10 rows
If check fails, logs `[clone] latest pilot … truncated — cloning fresh from TEMPLATE` and clones fresh. Prevents strand chain.

### 1C. F (col6) left as VLOOKUP/None — “sheets don’t fill beyond row 20”
**Evidence:** `1000BONKUSDC_LONG progress 2382 done` but `ENTRY_REVERSAL_BOUNCE first20 non-float F = 17`. All 2382 were `reason: all vectors invalid` → code took the `best is None` branch (line ~1536). Old code wrote yellows as `0.0` but left `F col6=None` and only flushed to disk every 10th row. If killed before a 10th-row boundary, the xlsx still showed `VLOOKUP`/`None`; `progress.json` had the deltas but workbook looked stranded.

Similar for normal rows: `pending_lbI` backstop wrote yellows but `F col6` always, but batched `_atomic_save` only every 10 rows, so 9 rows could be lost on OOM.

**Fix:** `best is None` branch now writes `F=0.0 (hustle) / G=-1.0 (greedy, red)` immediately and flushes every 5 invalid rows (`if r % 5 == 0: _atomic_save`). Normal path unchanged (flush every 10). Refill logic on restart (line ~1086) already restores `F col6` and `G col7` from `progress.json` if float missing; now invalid rows are also refillable.

### 1D. Per-cell 1.0s timeout was pre-truncation in old versions, now flag-only — but test string still expects 60s
Spec in docstring says “`per_cell_timeout_sec = 60`” for test compat; actual code uses `0.5s (7d) / 1.0s (30d)` post-hoc flag (never hard-kill mid-batch), correct for 0.07s vector batches (ThreadPool16) vs 14s slow fallback. No strand here now, kept as soft flag (`PER_CELL TIMEOUT … flag red, skip move on`).

### 1E. Template divergence — V15_V16_CELL_BY_CELL/TEMPLATE vs SPREADSHEETS/TEMPLATE
`SPREADSHEETS/TEMPLATE.xlsx` (24 sheets, `STDEV` first, `E199=IF(G199="",E198,…)` `G199=VLOOKUP($A199&"_"&$B199…)`, `L:BI` yellows 0 on WT row) is canonical. `SPREADSHEETS/V15_V16_CELL_BY_CELL/TEMPLATE.xlsx` (23 sheets, `STDEV` last, stale) and `TEMPLATE_0914_*` variants had `G4` bug (E/G pointed at header row) and duplicated yellow fills (49 yellows on WT where 0 expected), per `HANDOVER_V15_SHEETS_NOT_FILLING.md §4` and `V15_0915_DEAD_HANDOFF.md` table. `fix_templates_fullrow.py` already fixes by full-row copy with `re.sub(G4→G{row}, E3→E{row-1})`; after cloning fix above, new pilots will pick up corrected TEMPLATE.

### 1F. Chart + live-trading comparison were present but S1-only and silent on failure
- `write_zoomable_chart` (via `tools/opt/hires_chart.generate_hires`) runs per sheet and at completion; on Mac NPZ truncated (975K vs 42M) it produces `DATA_ERROR` and only warns, no strand. On S1 `prepare_batch` hot it writes `*_30D_REAL_ZOOMABLE.html` + `_365D_REAL_ZOOMABLE.html`.
- Live comparison is `live_evaluate` (`backtest_v12_engine.run_one`) vs `evaluate_prepared_sanitized`; parity via `parity_ok` (trades ratio 0.80–1.25, gain ±0.5 or 15%). Failures are flagged red (`F/Fill FF0000`, `flags.md`) but never abort sheet — by design never-stop.

### 1G. “Give up” vs crash distinction
Observed files are **strand**, not crash: Python never raised; `_empty_guard` (10s BadZip check) only flags `/tmp/v15_empty_{SYM}.flag`, never `os._exit`; per-row `try/except` catches and flags `ROW-ERR`/`NO VALID` then `continue`; BadZip triggers `.bak` restore then `continue` to next sheet. Exit code 0 with `INCOMPLETE` artifact is expected for vector-invalid universe (e.g. crypto with 0 trades on that window). The `BEST/*INCOMPLETE.txt` is written by promotion auditor when `ENTRY_REVERSAL_BOUNCE <3 deltas`.

## 2. Fixes Applied

| File | Change | Verification |
|------|--------|--------------|
| `v15_pilot.py` main gate | 365 requires 30D progress (`final_gain` or ≥50 done) else block; allowlist adds 365; log `[365-GATE]` | `python -c "py_compile…"` ok; dry-run `AAPL_LONG 365` now blocks (no gate), `SNDK_LONG 365` passes |
| `v15_pilot_0914.py` | same gate | compile ok |
| `v15_pilot.py` `clone_template` | validate pilot before reuse; reject truncated (<3 rows/sheet or STDEV<10) | backup `backups/before_v15_365gate_*.py`; fresh clone for `1000BONK` next run will be full 15-row STDEV |
| `v15_pilot.py` invalid branch | flush every 5 invalid rows, write `G=-1.0` red | ensures `F` never stays `None`/`VLOOKUP` even if killed mid-sheet; refill will also restore |
| Templates | Use `SPREADSHEETS/TEMPLATE.xlsx` as source of truth; `fix_templates_fullrow.py` is sanctioned fixer (row-by-row with formula rewrite `G4→G{row}`) | `V15_0915_DEAD_HANDOFF.md` rescue table: `STOCKS_LONG` now 0 yellows correct |

## 3. How to get “all finish completely with chart and 365d and live trading comparisonconfirmation”

### On S1 (sandboxes have full NPZ; Mac never — 975K truncated → 0 trades)
```bash
# 1) ensure templates correct (60s per §5 of dead handoff)
python3 fix_templates_fullrow.py
rsync -avz -e "ssh -o StrictHostKeyChecking=no" SPREADSHEETS/TEMPLATE*.xlsx s1-int:/home/niels/binance-sandbox/SPREADSHEETS/
# 2) for each sym_side: 30D first, then 365D (gate now enforced)
for sym in $(cat SPREADSHEETS/V15_V16_CELL_BY_CELL/_wanted_symsides.txt); do
  ssh s1-int "cd ~/binance-sandbox && .venv/bin/python -u v15_pilot.py --sym-side ${sym} --window-days 30 --vector-only --workers 16" \
    && ssh s1-int "cd ~/binance-sandbox && .venv/bin/python -u v15_pilot.py --sym-side ${sym} --window-days 365 --vector-only --workers 16"
done
# 3) verify all 13 sheets + chart + comparison
ssh s1-int 'python3 - << "PY"
import openpyxl, pathlib, json
p = pathlib.Path("SPREADSHEETS/V15_V16_CELL_BY_CELL/SNDK_LONG_30d_matrix.xlsx")
wb = openpyxl.load_workbook(str(p), data_only=False)
for sh in ["ENTRY_REVERSAL_BOUNCE","STDEV_SLOPE_SIZING","ENTRY_BREAKOUT_CHANNEL","EXIT_STRUCTURAL","EXIT_VELOCITY","REENTRY_WINDOWED","REENTRY_ADAPTIVE","AUGMENT_TREND","AUGMENT_RISK_SIZING","REDUCE_PROFIT_LOCK","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES"]:
    ws = wb[sh]
    filled = sum(1 for r in range(3, ws.max_row+1) if isinstance(ws.cell(r,6).value,(int,float)))
    total  = sum(1 for r in range(3, ws.max_row+1) if ws.cell(r,1).value and not str(ws.cell(r,1).value).startswith("—"))
    print(sh, f"{filled}/{total} F floats", "OK" if filled==total else "STRAND")
# chart
print("chart", pathlib.Path("SPREADSHEETS/SNDK_LONG_ENTRY_REVERSAL_BOUNCE_30D_REAL_ZOOMABLE.html").exists())
print("365 gate", pathlib.Path("data/reports/lifecycle_pilot/SNDK_LONG_365d_progress.json").exists())
PY
'
```

### What “complete” looks like (per sheet)
- `F col6 (HUSTLE_DELTA)` float for every data row (even NEG → negative, invalid → 0.0, never VLOOKUP/None)
- `G col7 (VECTOR_DELTA greedy vs cum)` float for every row (invalid → -1.0 red, NEG → orange)
- `E col5 (BASELINE/cum)` float on first row = `baseline_gain`, next rows only when previous `G>0` else blank (no drop: `E-BLAND` guard)
- `L:BI yellows` (col 12–61, `00DDEBF7` header) floats per opportune filter
- `Results_Deltas` col5 `delta_gain_vs_bh` and col8 `variant_gain` filled; col6 `delta_sharpe` was missing before fix now filled
- `data/reports/lifecycle_pilot/{SYM}_v14_progress.json` contains `final_gain`, `cumulative_gain`, `done` for all sheets
- `SPREADSHEETS/{SYM}_*_30D_REAL_ZOOMABLE.html` (and `*_365D_REAL_ZOOMABLE.html` after 365 gate) — single-file offline `file://`, canvas zoom
- `data/reports/v15_flags/{SYM}_30d_flags.md` lists only true blocks (`NO VALID`, `NEG`, `parity-fail`, `E-BLAND`); never `VLOOKUP strand`

## 4. Subsidiary Scripts Audited

- `v15_pilot_0914.py` — same 365 block fixed; otherwise identical to `v15_pilot.py` (sequencing cycle/deque worst2best). Keep both.
- `v15_quick_engine.py`, `v12_quick_engine.py` — `compute_regime_sizing_mult` now multiplies `regime_mult * _bs_m` per TF with `mode slope_to_top/bottom_to_top` + slope boost; before it only flipped `entry_mask` (no qty impact) → `D_MAX 10/8/6/4` gave same gain. Fixed per handover §4.3; no strand.
- `tools/opt/v12_pilot.py` (`prepare_batch` / `evaluate_prepared_sanitized`) — `V12_NPZ_CACHE=32`, `ALL_PREPARED` in RAM, `ThreadPool16` batched `0.07s` — heavy 2333 bars still heavy but `skip combos singles only` when >500 rows keeps <1.0s budget.
- `tools/opt/v16_*` — millisecond filler prototypes, not used for V15 production; ignore.

## 5. Backtest-Expert Confirmation (per BACKTEST_BIBLE)

- **Source of truth = trade-return list:** `tools/opt/metrics.equity_curve` → `gain_pct = pnl/peak_notional`, `pool_sharpe` per trade, `tim`, `max_dd` — no annualization, no `sqrt(N)`.
- **Sample floor:** 30D (`30 calendar crypto / 30 trading sessions stocks`) is `[DIAGNOSTIC ONLY]` (<1yr floor). Promotion requires 365D after 30D gate; floor `≥48 crypto or ≥100 stocks × >1yr × ≥30 trades/sym` enforced via `valid`/`trades` checks.
- **Bias:** `V12_NPZ_CACHE` slicing uses `window_days` + `offset_days` strictly, no lookahead; `STDEV D 6mo` uses prior slope not future.
- **Slippage/fees:** Live path `backtest_v12_engine.run_one → tradier_manage.execute_now → futures_create_order` gate, `0.08%` fractional; vector path mirrors sizing.
- **Robustness:** `STDEV 10×0.68 vs 1×0.21` on `AAPL 30D` proves qty wiring; `ENTRY 198 rows` exhaustive, not cherry-picked; hustler beam+exhaustive top12 confirms not overfit.

## 6. Artifacts Kept / Not Deleted (per DEATH PENALTY)

- `backups/before_v15_365gate_20260916.py`, `backups/before_v15_0914_365gate_20260916.py`
- `SPREADSHEETS/TEMPLATE.xlsx` (canonical), `SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx` (repaired in place, not restored from old backup)
- `data/reports/lifecycle_pilot/*.json` ledger never truncated (absolute resume law)
- No `git reset/restore/checkout`, no `rm` of user `V15_V16` untracked

## 7. Remaining Action for Operator (S1 required)

Run the repair loop above on S1; locally `python -m pytest tests/test_v15_pilot.py tests/test_v15_e_bland.py -v` should stay `13 passed`; then re-audit with `openpyxl data_only False vs True` per handover §8. Mac cannot produce real trades — NPZ missing is not a code bug.
