# TEMPLATE LAW — 7D 4000-Cell Workbook Must Fill in 10 Minutes

**Requisite #1: Keep NPZ in RAM forever** — `V12_NPZ_CACHE=32` + `ALL_PREPARED` + `ALL_NPZ_ARRAYS` holds 0.85-1.5G per sym hot. Never reload per row (0.07s vs >1s, 14x). `preload_prepared()` once, `evaluate_prepared_sanitized()` for every cell. Per-row disk reload is **FORBIDDEN**.

---

## DO — Correct Filling (from `v15_pilot.py` + rules)

1. **Read by `row2` headers**, not coordinates. `cols = _resolve_cols(ws)` via `BASELINE`, `HUSTLE_DELTA`, `VECTOR_DELTA`, `LIVE_DELTA`, `override`, etc. Columns may be added.
2. **First thing for `sym_side`:** find best `override` + `default` from `SPREADSHEETS/V15_V16_CELL_BY_CELL/*`, `data/reports/lifecycle_pilot/*_v14_progress.json`, `hustler_best.json`. Put every non-default **BOLD** in `C` (`override`), never change `B` (`default`) bold. Only `V15_AVG_DELTAS` maintenance may reorder `worst_first`.
3. **ORANGE `FILTER` (GENERAL) never above white `SWITCH` rows** — `GENERAL` rows are below switches, handled after all switch rows.
4. **Clone:** `TEMPLATE_{CRYPTO|STOCKS}_{LONG|SHORT}.xlsx` → `V15_V16_CELL_BY_CELL/{SYM}_{SIDE}_30d_matrix.xlsx` via `_atomic_save` (ZipFile ≥10 entries validate, avoid 225KB truncation).
5. **Baseline `E3`:** `v12_quick_engine` for `ALL` defaults + `cumulative_overrides` (BEST). `E` is header `BASELINE` at `row2`, `E3` is first numeric baseline. `BASELINE_METRICS!B2` = `cumulative_gain`.
6. **Delta vs LATEST, not vs initial baseline:** `delta = vec_gain - cumulative_before` where `cumulative_before` is current winning `cumulative_gain` (sum of all previous POS deltas). Never `vec_gain - baseline_gain`.
7. **Yellows `L:BI`:** Only **yellow** cells for that row's `switch=cand` (SPECIFIC). `FILTER_DICTIONARY_V2` maps `switch → applicable TFs` (`OFF/D/4h/1h/15m`). Never evaluate random filters. `delta` always written in yellow cell, ` >1e-9` is POS (green `006100`), else red `FFC7CE`.
8. **VECTOR `G`:** `sum(pos yellows)` per row. If `G>0` → **POS**: add `G` to `cumulative`, stay **same tab next row** (`E_next = new cumulative`), write `C = switch + pos yellows`, `K = pos yellows`. If `G≤0/None` → **NEG**: **do NOT move down same tab**, go to **first pending row in next TAB**, write `E = cumulative` there. Never `G` without `E`.
9. **`F` HUSTLE:** **ALWAYS blank** — `HUSTLE` not running. Only `hustle` seq_mode may write `F`; otherwise `F=None` with `VISUAL_F_FILL` black. Never `F = G` or `F = hustle_delta`.
10. **`E` BASELINE:** **Blank until POS** per law — only `first_data_row` per sheet and `POS`-promoted rows get `E = cumulative_before`. `NEG` non-first rows stay `E=None`. Never pre-fill `E` for all rows.
11. **`H` LIVE_DELTA / `I` LIVE_SHARPE:** **Blank until workbook complete** — contain formulas that screw up fill. Only after all 12 tabs done, verify winning `cumulative_overrides` via `backtest_v12_engine` (slow parity) and fill `H/I`.
12. **`K` PER_ROW_FILTERS:** `", ".join(pos_yellows)` per row, never overwrite.
13. **`STDEV_SLOPE_SIZING` skip** — 12 tabs to fill, not 13, until rewritten.
14. **Order:** Fill tabs **sequentially** (STDEV → ENTRY_REVERSAL_BOUNCE → … → GLOBAL), rows `3..max_row` in order, yellows per row before next row. `hustle` mode random only if enabled. If tab complete, skip in remaining rounds.
15. **Stall:** If cell >10s, mark **RED** fill `FF0000`, write reason, continue to next yellow or next TAB (never next ROW in same tab for NEG).
16. **Keep NPZ in RAM until workbook finished** — `ALL_PREPARED` + `ALL_NPZ_ARRAYS` never erase, `V12_NPZ_CACHE=32`, `ThreadPool 16` batch `0.07s` each.

---

## NOT DO — Forbidden

- **NEVER** reload NPZ per row/cell from disk. **NEVER** `evaluate_sanitized` per row without `preload_prepared`.
- **NEVER** write `F` HUSTLE when not in `hustle` mode. `F` is not `G`.
- **NEVER** pre-fill `E` for all rows as `cumulative`. `E` blank until POS (except first row per sheet).
- **NEVER** put `C` `FILTER` (orange GENERAL) above white `SWITCH` rows.
- **NEVER** change `B` (`default`) bold — only `C` (`override`) bold; `B` only via `V15_AVG_DELTAS` worst_first.
- **NEVER** use coordinates (`E=5`, `F=6`) — use `_resolve_cols` via `row2` headers.
- **NEVER** calculate `ORANGE` per-row — orange is per-sheet `GENERAL` after all switch rows.
- **NEVER** evaluate non-yellow filters for a row (wastes CPU, lies provenance).
- **NEVER** write `H/I` before workbook complete (formulas break fill).
- **NEVER** `NEG` → next ROW in same tab. `NEG` → next TAB's first pending.
- **NEVER** `delta` vs initial `baseline`. `delta` vs `cumulative_before` (latest winning set).
- **NEVER** `STDEV_SLOPE_SIZING` until rewritten.
- **NEVER** `abs()` baseline: `LONG` may be negative loss, `SHORT` may be positive — keep raw `gain_pct`/`bh_pct` from engine.
- **NEVER** `max(...,0)` cumulative when baseline negative — keep raw negative, don't force `0`.
- **NEVER** revert script to old backup — diff and patch forward only.

---

## 7D 4000-Cell in 10 Minutes — How

**Requisite:** NPZ hot (`V12_NPZ_CACHE=32`, `ALL_PREPARED`, 0.85-1.5G per sym). Per-row disk reload = `>1s` → `HOURS`; hot = `0.07s` → `~40s` per sym single-thread, `32` parallel workers → `3.5h` for `354` syms, `~2min` for `7d` demo.

**Target:** `4000` cells / `600s` = `0.15s` per cell. `0.07s` naked + `0.08s` yellows (4 yellows) fits.

**Path (current `v15_pilot`: `0.5s`/row, `17s` for `3930` rows via `ThreadPool 16` naked → `~6min` for `4000` cells, within `10min`):**
- `preload_prepared(sym, window_days=7)` once, keep `ALL_PREPARED` hot
- `evaluate_prepared_sanitized(prep, overrides, 7)` for baseline
- For each of `12` tabs, each `r` `3..max_row` sequentially, `ThreadPool 16` for yellows batch per row (`4` yellows `OFF/D/4h/1h` → `0.07s` parallel)
- Write `C` only if `G>0`, `E` only if `first` or `POS`, `F=None`, `G` sum pos, `H/I=None`, yellows `L:BI` per filter, `_atomic_save` every `50` rows (not per row: `27s` saved)

**Verified 7D complete:** `1000000MOGUSDT_LONG` `7d` `3930` rows `3361` bars `baseline -7.4641` `bh -9.83` `44` trades, `F` blank, `E` first-row only until POS, `G=delta vs latest` (not `-28.76` mirrored), `NEG→next TAB`, `STDEV` skipped, `2.3M` xlsx in `<2min` via parallel filler `/tmp/run_complete_now_fixed.py` (16 workers, 4 yellows/row).

**Repro:**
```bash
V15_ALLOW_MAC=1 python3 -u v15_pilot.py --sym-side 1000000MOGUSDT_LONG --window-days 7 --vector-only --workers 16 --allow-mac
# or fast demo (complete, not herd):
python3 -u /tmp/run_complete_now_fixed.py  # 3930 rows, 12 tabs, ~90s, F blank, E per law, G vs latest
open SPREADSHEETS/V15_V16_CELL_BY_CELL/1000000MOGUSDT_LONG_7d_COMPLETE_MATRIX.xlsx
```

**Not done:** Full `30d` (`2881` bars) `≈6min` per sym single-thread → `354` syms `≈35h` single, `≈3.5h` with `32` workers; `365d` (`~30k` bars) gated to winners of `30d` (`delta>1`, `pool_sharpe>0.2`, `TIM 20-80`, `DD≤30`) to keep `10min` per `7d` demo, not per `365d` full sweep. See `SPEEDUP_100_SUGGESTIONS.md` for `100` further cuts.
