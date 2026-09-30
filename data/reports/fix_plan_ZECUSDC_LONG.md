# Fix Report for ZECUSDC_LONG — 2026-09-30T01:55:56.528032Z

## Summary
- Timeouts detected: 73
- First timeout at: BB_SQUEEZE_WIDTH_PERCENTILE
- Pattern: All cells at same cumulative baseline (system stall after baseline)

## Root Cause Hypothesis
The baseline evaluation succeeds (~0.07s), but then the ThreadPool/evaluation cache
becomes unable to process subsequent variants. This suggests:

1. **Per-row disk reload** — a filter is calling file I/O in the variant loop
2. **Missing NPZ key with fallback** — stdev_edge_* or similar not cached; fallback recomputes
3. **Slow vec_decisions function** — a mask calculation takes >10s on this symbol
4. **ThreadPool deadlock** — the Executor's wait() call hangs on unfinished futures

## Investigation Steps (in order)

### Step 1: Verify NPZ preload
Check that NPZ keys are loaded and no disk I/O happens during evaluation.

### Step 2: Profile the first timeout switch
Profile which specific calculation is timing out.


### Step 3: Check if it's a disk reload
```bash
grep -n "\.seek\|np\.load\|npz\[" v12_quick_engine.py | head -20
```

## Fixes to Apply

### BB_SQUEEZE_WIDTH_PERCENTILE
- **Type:** numpy_optimize
- **File:** v12_quick_engine.py
- **Issue:** Bollinger band calculation may be doing per-bar computation
- **Fix:** Ensure BB calculation is fully vectorized; use pre-computed BB arrays from NPZ

### ATR_LONG_WINDOW
- **Type:** npz_key_check
- **File:** v12_quick_engine.py
- **Issue:** ATR calculation may fall back to computing from close when NPZ key missing
- **Fix:** Check if 'atr_*' keys exist in NPZ; if not, pre-compute or use fallback mask

### WT_DC_HTF_GATE
- **Type:** remove_python_loop
- **File:** v12_quick_engine.py
- **Issue:** Wave Trend HTF gate may be doing per-bar Python loop
- **Fix:** Vectorize HTF gate check using numpy.where() instead of bar-by-bar iteration

### DC_BREAK_WAIT_WT15_CLOSE_ENABLED
- **Type:** numpy_optimize
- **File:** v12_quick_engine.py
- **Issue:** DC break detection may have per-bar Python logic
- **Fix:** Vectorize DC break mask computation; ensure WT_15M data is pre-loaded

### WT_4H_VEL_EXIT_REQUIRE_K_EXTREME
- **Type:** unknown
- **File:** unknown
- **Issue:** Unknown timeout switch: WT_4H_VEL_EXIT_REQUIRE_K_EXTREME
- **Fix:** Profile the switch to identify bottleneck

## Validation Checklist

- [ ] Identified bottleneck function
- [ ] Confirmed it's not per-row disk I/O
- [ ] Optimized to numpy-only vectorization
- [ ] Verified delta unchanged from original (<1e-9)
- [ ] Tested on ≥3 symbols (crypto + stocks)
- [ ] Compared vector result with live trading result
- [ ] Deployed to S1/S2/S5
- [ ] Restarted herd and monitored for regressions

## Next Steps

1. Run diagnosis script: `python3 tools/diagnose_baseline_stall.py`
2. Identify slow function from output
3. Optimize that function to <100ms
4. Validate delta unchanged
5. Deploy: `rsync -az <file> s1-int:~/binance-sandbox/`
6. Restart: `ssh s1-int "pkill -f v15_pilot.py"`
7. Monitor flags directory for improvement
