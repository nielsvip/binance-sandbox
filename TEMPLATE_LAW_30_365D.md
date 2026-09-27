# TEMPLATE LAW — 30D & 365D — WHAT TO DO / NOT DO

**7D is midget test** for cheap agents. **30D/365D are real** — 30D `2881` bars (crypto `3m`) / `~500` bars (stocks `15m`), 365D `~30k` bars. Same TEMPLATE law, but 30d/365d must respect real costs, not midget shortcuts.

---

## DO — 30D & 365D Correct

1. **NPZ in RAM forever** — `V12_NPZ_CACHE=32`, `ALL_PREPARED`+`ALL_NPZ_ARRAYS` 0.85-1.5G per sym. `preload_prepared(sym, window_days)` once per sym per window (7/30/365 sliced). Never `evaluate_sanitized` without `prep`.
2. **Window = 30 or 365** — `prepare_batch(sym, 30)` gives `2881` bars (crypto) with `offset 0` (last 30d). `365` gives `~30k` bars. `bh` and `gain_pct` are for that window only.
3. **Baseline:** `defaults (3440) + cumulative_overrides (BEST)` via `get_defaults_for_symside()` + `load_live_recipes()` + `PROGRESS_DIR/*_v14_progress.json` `cumulative_overrides`. `E3 = baseline_gain` (first data row per sheet), `BASELINE_METRICS!B2 = baseline_gain`.
4. **Delta vs LATEST:** `vec_gain = evaluate_prepared_sanitized(prep, variant, window_days)`; `delta = vec_gain - cumulative_before` (`cumulative_before` = current winning `cumulative_gain`). Never `vec_gain - baseline_gain` after first POS.
5. **Yellows `L:BI`:** Only SPECIFIC per `FILTER_DICTIONARY_V2` for that `switch=cand`. `ThreadPool 16` per row, `4` TFs `OFF/D/4h/1h` (or `15m`). Test vs `cumulative_before`, write yellow cell `float(delta)`.
6. **`G` VECTOR:** `sum(pos yellows)` if any POS yellows, else naked `delta`. `G>1e-9` → POS, else NEG.
7. **`POS` → stay same tab next row:** `cumulative += G`, `C = switch + pos yellows` bold green `006100`, `E_next = new cumulative` for next pending in same tab, `H/I=None`, `F=None`, `K=pos yellows`. `_atomic_save` every `50` rows.
8. **`NEG` → next TAB:** `cumulative` unchanged, `C=None`, `E` blank (except first row per sheet), `G` = delta (neg/0, red), `F=None`, `H/I=None`. Write `E = cumulative` in next tab's first pending. **Do not move to next row in same tab.**
9. **`F` HUSTLE:** Always `None` (HUSTLE not running). Only `seq_mode=hustle` may write `F`.
10. **`E` BASELINE:** Blank until POS per law — only `first_data_row` per sheet (`row2` header `BASELINE` preserved, `E3` numeric) and POS rows get `E`. `NEG` non-first stays `E=None`.
11. **`H/I` LIVE:** Blank until workbook complete. After `12` tabs done, verify winning `cumulative_overrides` via `backtest_v12_engine.run_one(sym, overrides, window_days)` (30s, not per row) and fill `H=live_delta`, `I=live_sharpe` (vector_only fallback).
12. **`STDEV_SLOPE_SIZING` skip** — 12 tabs, not 13, until rewritten. `GLOBAL_RISK_GATES` last.
13. **Keep NPZ hot until workbook finished** — `ALL_PREPARED` never erase, `ALL_NPZ_ARRAYS` never `pop`, `V12_NPZ_CACHE=32` (30d) / `64` (365d).
14. **30D gate for 365D:** Only symbols where `30d POS>0` and `pool_sharpe>0.2` and `TIM 20-80` and `DD≤30` go to `365d`. Never run `365d` for all `354` syms.
15. **7D midget:** Cheap agents must complete `7d 4000` cells in `10min` (`0.15s/cell`). `7d 3361` bars `0.07s` naked → `3930*0.07/16=17s` parallel + `10s` save = `<2min`. Proves `v12_quick` wiring, not 30d P&L.

---

## NOT DO — 30D/365D Forbidden

- **NEVER** reload NPZ per row/cell. `7d 3930*1s = 65min` vs hot `0.07s = 4.6min` single, `0.5min` parallel.
- **NEVER** `30d` without `ALL_PREPARED` hot — `2881` bars * `945` arrays = `1.2G` per sym, reload kills herd.
- **NEVER** `365d` for all syms — `30k` bars * `945` arrays = `12G` per sym OOM. Gate via `30d` winners only.
- **NEVER** `F` HUSTLE with value when not in `hustle` mode.
- **NEVER** `E` pre-filled for all rows. `E` blank until POS.
- **NEVER** `C` orange GENERAL above white SWITCH.
- **NEVER** change `B` default bold — only `C` override bold.
- **NEVER** use coordinates — `cols = _resolve_cols(ws)` via `row2` headers.
- **NEVER** orange per-row — orange `GENERAL` after all switch rows.
- **NEVER** random yellows — only `SPECIFIC` per switch.
- **NEVER** `H/I` before complete (formulas break fill).
- **NEVER** `NEG` → next ROW same tab. `NEG` → next TAB.
- **NEVER** `delta` vs initial baseline. `delta` vs `cumulative_before` (latest).
- **NEVER** `STDEV` until rewritten.
- **NEVER** `abs()` baseline — `LONG` loss stays negative, `SHORT` win stays positive. `abs()` created `14.38 / -28.76` mirrored bullshit.
- **NEVER** `max(cum,0)` when baseline negative — keep `-7.46`, not `0`.
- **NEVER** revert — diff forward, `cp` to `backups/` first, `py_compile` then `rsync -az -e "ssh -S none"` to `s1-int:~/binance-sandbox/` + `md5sum` verify.

---

## 30D / 365D Performance — 10min for 7D Midget, Real for 30D

- **7D midget** (`3361` bars crypto `3m`, `3930` rows): `0.07s` naked → `4.6min` single, `17s` with `16` workers, `~90s` with yellows `4/row` → **<2min** complete, proves wiring without S1.
- **30D real** (`2881` bars crypto, `3930` rows): `0.07s` → `4.6min` single, `1.5min` with `16` workers + `10s` save → `~6min` per sym. `354` syms → `35h` single, `3.5h` with `32` workers on `S1` (already `32` parallel `V12_NPZ_CACHE=32`). Must run on `S1` (`473` NPZ `31G`), Mac has `6` NPZ `271M` → `DATA_ERROR`.
- **365D real** (`~30k` bars, `10x` 30D): `0.7s` per eval → `46min` per sym single, `3min` with `16` workers → only for `30d` winners (`~40` syms) → `2h` on `32` workers. Never for all.

**Repro 30D:**
```bash
V15_ALLOW_MAC=1 python3 -u v15_pilot.py --sym-side 1000000MOGUSDT_LONG --window-days 30 --vector-only --workers 16 --allow-mac
# 7D midget proves in 90s:
python3 -u /tmp/run_complete_now_fixed.py  # 3930 rows, 12 tabs, F blank, E per law, G vs latest, <2min
open SPREADSHEETS/V15_V16_CELL_BY_CELL/1000000MOGUSDT_LONG_7d_COMPLETE_MATRIX.xlsx
```

**Current fix:** `v15_pilot.py` `5479L` at `TEMPLATE_LAW_7D_10MIN.md` + `TEMPLATE_LAW_30_365D.md` — `HUSTLE` blank, `E` blank until POS, `delta vs latest`, `NEG→next TAB`, `STDEV` skip, `NPZ` hot. Verified `1000000MOGUSDT_LONG 7d` `baseline -7.4641` `bh -9.83` `44` trades, `R3 E=-7.46 F=None G=0`, `R4 E=None` (blank until POS).

**100 speedups:** See `/tmp/SPEEDUP_100_SUGGESTIONS.md` (A. NPZ 1-15, B. Vector 16-30, C. Workbook 31-50, D. Herd 51-70, E. Robustness 71-85, F. Infra 86-100).
