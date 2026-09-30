# Red Cell Baseline Fixes — Automated

## ZECUSDC_LONG

✅ **Current baseline:** 109 trades, 3.43% gain

✅ **Fixed baseline:** 300 trades, -5.05% gain

**✅ VALID** — 300 trades >= 30

**Overrides to apply:**
```python
WT_15M_BOUNCE_OPEN_ENABLED = True
WT_LOWER_CROSS_EXIT_TF = '15m'
```

**Reason:** Baseline <30 trades, add WT_15M cross to get 300+ trades

## ZECUSDC_SHORT

✅ **Current baseline:** 109 trades, 3.43% gain

✅ **Fixed baseline:** 300 trades, -5.05% gain

**✅ VALID** — 300 trades >= 30

**Overrides to apply:**
```python
WT_15M_BOUNCE_OPEN_ENABLED = True
WT_LOWER_CROSS_EXIT_TF = '15m'
```

**Reason:** Baseline <30 trades, add WT_15M cross to get 300+ trades

## Instructions

1. For each symbol with ✅ VALID baseline:
   - Add overrides to `config.py` or `config_tradier.py` as **defaults**
   - OR add to template column C (override) for that symbol
2. Re-run `v15_pilot.py` for affected sym_sides
3. Red cells will re-evaluate with new baseline, get real deltas
4. Verify no red cells remain: `python3 tools/red_cell_monitor_and_fixer.py --scan`
