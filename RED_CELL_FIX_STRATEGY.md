# Red Cell Fix Strategy — 2026-09-30

## Problem Statement

**Pattern observed:** All red cells in ZECUSDC_LONG_30d_flags.md and similar files show:
- Same cumulative baseline: 5.544918357875113
- Identical "stuck >10s" timeout message
- All cells at rows 10+ across all sheets
- Implies: baseline calculated successfully, but subsequent evaluations all timeout

**Root cause hypothesis:** After baseline evaluation completes, the ThreadPool or evaluation cache becomes unable to process subsequent variants. This is NOT individual cell slowness but a **system-level bottleneck**.

## Investigation Steps (Run in Order)

### Step 1: Diagnose baseline stall
```bash
python3 tools/diagnose_baseline_stall.py
```

This will:
- Verify baseline evaluates <1s
- Test if variants also hang at 10s
- Check if ThreadPool causes the issue
- Verify memory isn't exhausted
- Compare sequential vs parallel performance

### Step 2: Identify which calculation is blocking
Common culprits (BACKTEST_BIBLE §7.1):
1. **Per-row disk reload** — forbidden; use `ALL_PREPARED` cache
2. **Missing NPZ keys with fallback** — `compute_regime_sizing_mult()` when `stdev_edge_*` missing
3. **Python loops over bar data** — must be numpy vectorized
4. **Slow vec_decisions function** — timeout in mask computation

### Step 3: Profile the slow path
Once we know which switch/filter times out:
```bash
python3 -c "
from tools.red_cell_monitor_and_fixer import profile_slow_calculation
result = profile_slow_calculation('ZECUSDC_LONG', 'BB_SQUEEZE_WIDTH_PERCENTILE', '0.1')
print(result)
"
```

### Step 4: Optimize identified function
When slow function is found:
1. Check if it's doing disk I/O in a loop → use preloaded NPZ
2. Check if it's calling a Python loop → vectorize with numpy
3. Check if it's using a missing NPZ key → add fallback or skip computation
4. Check if it's calling a slow vec_decisions function → optimize that

### Step 5: Validate fix
After optimization:
1. Verify function runs <100ms
2. Verify it matches live trading logic (ez_manage or tradier_manage)
3. Run 10-symbol sweep to confirm no regressions
4. Ensure delta output matches original (within 1e-9)

## Known Fast Paths (Baselines)

These functions have proven fast (<50ms on Mac, tested 2026-09-30):
- `simulate_one()` in v12_quick_engine.py — 71ms per eval with NPZ in RAM ✓
- `evaluate_prepared_sanitized()` — 170ms total (mostly I/O on first run)
- Individual vec_decisions masks — all <10ms when vectorized ✓

## Known Slow Paths (To Avoid)

NEVER do these in evaluation loop:
1. `npz.seek()` or `np.load()` inside row loop (per-row disk reload) → 1s+
2. `for bar in range(n)` Python loops over bar data → scales O(n)
3. `if key not in npz` with expensive fallback → stalls on recompute
4. Thread creation inside loop → ThreadPool overhead
5. No timeout on evaluation → hangs entire workbook

## Immediate Actions

### Action 1: Verify NPZ preload is working
```bash
# On S1, check if prepare_batch() succeeds
ssh s1-int "cd ~/binance-sandbox && python3 -c \"
from tools.opt.v12_pilot import prepare_batch
p = prepare_batch('ZECUSDC', 30)
print(f'Prepared: {type(p).__name__}, keys={len(p.keys()) if hasattr(p, \"keys\") else \"N/A\"}')
\""
```

### Action 2: Check if ThreadPool stalls on specific switch
```bash
# Run the diagnose script on S1
ssh s1-int "cd ~/binance-sandbox && python3 tools/diagnose_baseline_stall.py 2>&1 | head -100"
```

### Action 3: Fix most common timeout (if it's BB_SQUEEZE_*)
The flags show `BB_SQUEEZE_WIDTH_PERCENTILE` as first timeout. Check:
```bash
grep -n "BB_SQUEEZE_WIDTH_PERCENTILE\|BB_SQUEEZE" v12_quick_engine.py | head -10
```

If found, check if it's:
- Using an NPZ key that doesn't exist
- Doing a slow filter scan
- Looping over time windows

### Action 4: Deploy fix to servers
Once the bottleneck is identified:
1. Optimize locally on Mac
2. Test with `python3 tools/diagnose_baseline_stall.py`
3. Verify delta unchanged: `python3 -c "from tools.opt.v12_pilot import evaluate_sanitized; b=evaluate_sanitized('ZECUSDC_LONG', {}, 30); v=evaluate_sanitized('ZECUSDC_LONG', {'BB_SQUEEZE_ENTRY_ENABLED': True}, 30); print(f'Delta: {v[\"gain_pct\"] - b[\"gain_pct\"]}')"`
4. Sync to S1: `rsync -az v12_quick_engine.py s1-int:~/binance-sandbox/`
5. Restart S1 herd: `ssh s1-int "pkill -f v15_pilot.py"`

## Validation Checklist

- [ ] Baseline evaluates <1s (0.07s cached)
- [ ] Simple variant evaluates <1s
- [ ] ThreadPool with 16 workers processes 5 configs in <5s
- [ ] Memory doesn't grow >2GB during eval
- [ ] No per-row disk I/O detected
- [ ] Delta identical to original (<1e-9)
- [ ] Works on crypto AND stocks
- [ ] Works on all 4 template variants (LONG/SHORT × crypto/stocks)

## Success Metrics

- **Before:** All rows timeout after baseline, 0% cells filled
- **After:** All rows evaluate <0.1s, >95% cells filled (some may still be legitimately 0-delta)
- **Time to fix:** <2 hours once bottleneck is identified
- **Cost savings:** $2.40/hr × (hours saved) on S1/S2/S5 compute

## References

- BACKTEST_BIBLE §7 (v12_quick_engine stall-prone areas)
- BACKTEST_BIBLE §22 (timing targets and guards)
- BACKTEST_BIBLE §5.7 (spec for RED cell handling)
- Monitor script: `tools/red_cell_monitor_and_fixer.py`
- Diagnosis script: `tools/diagnose_baseline_stall.py`
