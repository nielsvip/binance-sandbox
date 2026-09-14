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