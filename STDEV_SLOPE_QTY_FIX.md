# STDEV Slope Sizing — Qty Multiplier Fix Brief (for agent)

**Symptom (observed 2026-09-14):** `STDEV_SLOPE_SIZING` switch sheet in `SPREADSHEETS/V15_V16_CELL_BY_CELL/*_30d_matrix.xlsx` shows all rows as:

```
STDEV_SLOPE_SIZING_ENABLED TRUE  STDEV_LADDER  -4.964...  0  NO
STDEV_BAND_MULTIPLIER 2.5  STDEV_LADDER  -4.964...  0  NO
...
```

`E = -4.964 (baseline)`, `F = 0`, `→ NO` — no variant produces delta. Expected: `F` should show stdev-scaled qty delta and some rows should promote.

**Root cause (qty caps):** Backtest caps `MAX_POSITION_SIZE` / `MAX_ORDER_VALUE` (and BTC/MEN/FIN twins) are equal to `START_POSITION_SIZE` (≈ $14–$200 in `config.py:77-87`), so the stdev slope ladder (10× D, 4× 4h, 2× 1h, 1.5× 15m at `config.py:1163-1166`) cannot express 10× size. `max(START, capital*0.5)` in `backtest_v15_engine.py:2743-2773` still yields ~$200 when `START=14` and `capital=1k`, i.e. 1×.

**Required invariant (enforce in backtest *before* any `v12_quick_engine` / `backtest_v15_engine` sizing):**

```python
# in every backtest entry point that touches sizing (backtest_v15_engine.py, backtest_v12_engine.py, backtest_v8/baseline, v12_quick_engine.QuickConfig)
START = float(config.START_POSITION_SIZE)
for key in ("MAX_POSITION_SIZE", "MAX_POSITION_SIZE_BTC", "MAX_POSITION_SIZE_MEN", "MAX_POSITION_SIZE_FIN",
            "MAX_ORDER_VALUE", "MAX_ORDER_VALUE_MEN", "MAX_ORDER_VALUE_FIN",
            "SCALP_MAX_POSITION_SIZE", "SWING_MAX_POSITION_SIZE"):
    if hasattr(config, key):
        setattr(config, key, max(float(getattr(config, key) or START), START * 10.0))

# also ensure BAND_ARROW_MAX_POS_MULT * START does not cap below 10×
config.BAND_ARROW_MAX_POS_MULT = max(float(config.BAND_ARROW_MAX_POS_MULT), 10.0)
```

Verify post-fix:

```
python -c "import config; print(config.START_POSITION_SIZE, config.MAX_POSITION_SIZE, config.MAX_POSITION_SIZE/config.START_POSITION_SIZE)"
# must be >= 10.0
```

**Gradient stdev qty calculation — must be applied in *all* backtest paths:**

1. **Live path (reference):** `tradier_manage.py:25928-25960` — `STDEV_SLOPE_SIZING_ENABLED` ladder:
   - per-TF `max_map = {D:10.0, 4h:4.0, 1h:2.0, 15m:1.5}` at `config:1163-1166`
   - `mode = STDEV_SLOPE_SIZING_MODE` (`slope_to_top` vs `bottom_to_top` at `config:1171`)
   - `band = STDEV_BAND_MULTIPLIER=2.5` at `config:1162`
   - `lookback = {D:180, 4h:180, 1h:168, 15m:96}` at `config:1167-1170`
   - qty = `START * (1 + (slope_factor) * (max-1))` clamped by `MAX_*/MAX_ORDER` caps above.

2. **Backtest paths that must mirror live:**
   - `backtest_v15_engine.py` / `backtest_v12_engine.py` — `compute_regime_sizing_mult` / `sanitize_overrides` must read the same `STDEV_SLOPE_*` knobs and produce identical `qty_mult`.
   - `v12_quick_engine.py` / `v12_quick_engine_fast_v2.py` `QuickConfig` must expose `STDEV_SLOPE_SIZING_*` fields and wire `config_tradier.TradierConfig` defaults via `get_defaults_for_symside()` so `sanitize_overrides` can map.
   - Any `v8_quick_engine` shim must not drop the field (see `README_FIX_RED_CELLS.md:28` — add to `QuickConfig` if missing).

3. **Verification (no re-implemented logic):** Run real `v15_pilot.py --sym-side AMAT_SHORT --window-days 30 --vector-only` (or any `SYM_SHORT` with `.npz` on S1) and check:

```
# after run, open SPREADSHEETS/V15_V16_CELL_BY_CELL/AMAT_SHORT_30d_matrix.xlsx
# STDEV_SLOPE_SIZING sheet: column F (VECTOR_DELTA) must be !=0 for at least one row, column G (LIVE_DELTA) same, and at least one row must be POS/→ YES (not all NO).
python3 - <<'PY'
import openpyxl
wb=openpyxl.load_workbook("SPREADSHEETS/V15_V16_CELL_BY_CELL/AMAT_SHORT_30d_matrix.xlsx", data_only=True)
ws=wb["STDEV_SLOPE_SIZING"]
print([ws.cell(2,c).value for c in range(1,10)])
for r in range(3,14):
    print(r, [ws.cell(r,c).value for c in range(1,9)])
PY
# expected: row with STDEV_SLOPE_SIZING_ENABLED TRUE → F !=0, e.g. 0.8 or 1.2, not 0
```

Also check sizing log:

```
python -c "import config; assert config.MAX_POSITION_SIZE >= 10*config.START_POSITION_SIZE, f'{config.MAX_POSITION_SIZE} < 10*{config.START_POSITION_SIZE}'"
```

**Files to touch (minimal):**
- `config.py` (and `config_tradier.py` for tradier parity) — bump defaults or add backtest-only override in `backtest_v*_engine.py` as above.
- `backtest_v15_engine.py:2743-2811` — sizing block (add 10× clamp).
- `v12_quick_engine.py` `QuickConfig` — ensure `STDEV_SLOPE_SIZING_*` fields exist.

**Sample floor reminder (CLAUDE.md):** Do not promote STDEV sizing until ≥48 crypto or ≥100 stocks × >1yr × ≥30 trades/sym, with `metrics_guard` Sharpe via `pool_sharpe`.

**Agent checklist before handing off:**
- [ ] `config.MAX_POSITION_SIZE / START_POSITION_SIZE >= 10` (and same for BTC/MEN/FIN, MAX_ORDER_VALUE twins) in backtest.
- [ ] `BAND_ARROW_MAX_POS_MULT >=10`.
- [ ] `STDEV_SLOPE_SIZING` sheet shows non-zero deltas and at least one POS promotion on a real `AMAT_SHORT`/`BWXT_SHORT` run.
- [ ] No bare `sharpe` label, no annualization, `metrics_guard` used.

If fix proven to work and produce pos delta sync all script changes btw s2 s1 and macbook

---

# ADDENDUM 2026-09-14 — root-cause verdict (S1 probe, MSFT_LONG 30d, quick engine)

Probe (`/tmp/stdev_probe.py` on S1, real `v12_quick_engine`): `lrL_*` present (D pb mean 0.97),
regime_mult mean 11.3. A(default,STDEV on) gain −0.80 / B(STDEV off) −1.86 → **ladder alive: −1.06
without it**. MODE bottom_to_top +0.72. Cap 2500→5000 +0.73. So the MSFT sheet zeros were NOT a
dead ladder — they were four distinct defects:

1. **Quick-engine order cap clipped the ladder (FIXED).** Live (`tradier_manage.calculate_position_size`)
   does `max_value = min(START, MAX_ORDER)` THEN `max_value *= ladder` → full 10x expresses.
   Quick (`v12_quick_engine.py` entries) did `min(START*mult, MAX_ORDER_VALUE=2500)` → 10x D ladder
   clipped at 5x. Fix: floor the entry cap at `START*10` (2 sites, seed + entry). Deliberately did
   NOT change QuickConfig field defaults — the `_DEFAULTS_625` audit blocks key off cfg-vs-default
   differences, so a default change would silently rewire entries everywhere.
2. **Sheet tested defaults against themselves.** Every STDEV row's cand == cumulative value
   (QuickConfig STDEV defaults == TEMPLATE == recipes) → delta 0 by construction. BAND rows moved
   only because QuickConfig BAND defaults (TF 4h, MAX 1.25, MIN 0.25, DEPTH/NORM 0.5) ≠ TEMPLATE/live
   (TF D, MAX 2.5, MIN 0.5, DEPTH/NORM 1.0). To measure the ladder's contribution, ablate:
   set col B `STDEV_SLOPE_SIZING_ENABLED=FALSE` on a scratch copy — the recorded NEG delta magnitude
   IS the contribution (pilot records F/G floats even when it blocks promotion).
3. **Dead knobs (all paths, by design).** `STDEV_BAND_MULTIPLIER` + 4 `STDEV_SLOPE_LOOKBACK_*` are read
   NOWHERE — precompute hardcodes 2.5σ and fixed windows (`backtest_v8_precompute.py` lrL block).
   They are precompute-locked constants, not switches. Left in sheet as documentation; expect 0.
4. **Missing MODE row (FIXED in TEMPLATE).** `STDEV_SLOPE_SIZING_MODE` moves +0.72 on MSFT but had no
   sheet row and no dictionary entry — appended as row 18 (`slope_to_top`, append-only, no row shift).

Also fixed (live parity, same files): crypto venue slope factors were stock-session based
(4h 1.625x instead of 6x → slope_mult understated ~3.7x on all crypto quick sims); now branches on
`cfg.MODE` to match `ez_positions_quick` (24/6/96) vs `tradier_manage` (6.5/1.625/26). Missing
`lrL_*` channel keys now skip the ladder (live fails closed; quick used to fail open at ~10x).

Files touched: `v12_quick_engine.py` (STDEV block + 2 cap sites), `v12_quick_engine_fast_v2.py`
(STDEV block), `SPREADSHEETS/TEMPLATE.xlsx` (MODE row 18). No live-trading file touched.