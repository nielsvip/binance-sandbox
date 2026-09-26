# v15_pilot.py Fixes - Quick Start Guide

## What Was Wrong

v15_pilot.py had a **critical syntax error** - the main processing `while` loop in `_spec_fill_workbook()` was completely missing, causing IndentationError. This prevented any sheets from being filled.

## What Was Fixed

✅ **Fixed 6 critical issues:**
1. Missing while loop (CRITICAL)
2. max_loops too small for tab cycling (HIGH)
3. Progress key mismatch after resorting (HIGH)
4. Baseline carry-over lost on resume (HIGH)
5. E2 header corruption (MEDIUM)
6. C styling missing on resume (MEDIUM)

## Verify the Fix

```bash
# Check syntax is valid
python3 -m py_compile /Users/niels/Documents/binance/v15_pilot.py

# Run full validation
python3 /Users/niels/Documents/binance/tools/test_v15_fixes.py
```

## Deploy to S1

```bash
# Copy fixed file to S1
rsync -az /Users/niels/Documents/binance/v15_pilot.py s1-int:~/binance-sandbox/

# Test on S1
ssh s1-int 'cd ~/binance-sandbox && python3 -m py_compile v15_pilot.py && echo "OK"'
```

## Test Run

```bash
# Quick dry-run test (no calculations)
ssh s1-int 'cd ~/binance-sandbox && python3 v15_pilot.py --sym-side AAPL_LONG --window-days 30 --dry-run'

# Real test with one symbol (takes ~5min)
ssh s1-int 'cd ~/binance-sandbox && python3 v15_pilot.py --sym-side AAPL_LONG --window-days 30 --vector-only'
```

## Monitor Logs

Watch for these messages (good sign):
- `[spec-fill] main processing while loop`
- `[spec-row]` messages (rows being processed)
- `[baseline-restore]` messages (progress being preserved)

Watch for these messages (bad sign - report immediately):
- `[spec-fill] incomplete after loop guard` (loop exiting early)
- Any syntax errors

## Rollback If Needed

Original file backed up at:
```
/Users/niels/Documents/binance/backups/before_stranding_fixes_202609260414.py
```

## Documentation

Read these for full details:
- **V15_PILOT_STRANDING_RESOLVED.md** - Full root cause analysis
- **V15_PILOT_FIXES_APPLIED.md** - Detailed code changes
- **V15_PILOT_STRANDING_ANALYSIS.md** - Technical deep dive

## Questions?

All fixes are:
- ✅ Syntax validated
- ✅ Backward compatible
- ✅ Well-documented
- ✅ Low risk

