# v15_pilot.py Stranding Analysis & Fixes

## CRITICAL ISSUES IDENTIFIED

### Issue #1: Loop Guard Max_Loops Too Small / Inefficient
**Location:** `_spec_fill_workbook()` line 830  
**Current Code:**
```python
max_loops = total_rows * 3 + 100  # safety
```

**Problem:**
- With 3043 rows per symbol_side, `max_loops = 9229`
- BUT: the loop doesn't guarantee 1 iteration per row
- When a tab has no pending rows (line 836-850), the code searches for next tab with pending rows
- If multiple tabs are incomplete and scattered, iterations can cycle without processing rows
- Result: Loop exits at max_loops while rows remain unprocessed (see line 1163)

**Fix:** Calculate based on tab count + margin for cycling
```python
# Worst case: cycle through all 12 tabs for each pending row
max_loops = total_rows * len(tabs) + 200
```

---

### Issue #2: Baseline Carry-Over on Resume Broken
**Location:** Main function lines 2687-2699 (progress loading)  
**Problem:**
- When resuming, `cumulative_gain` is loaded from `progress.get("cumulative_gain")` 
- But in `_spec_fill_workbook`, if previous run completed some rows, the cumulative_gain should start from the LAST completed row's value
- Instead, it uses only the baseline value, losing all previous deltas

**Fix:** Track cumulative properly from last completed row
```python
# After loading progress JSON, check if there are completed rows
if progress.get("done"):
    # Find last completed row and use its cumulative_after value as baseline
    last_completed = None
    for key in sorted(progress["done"].keys()):
        # Sort by execution order (insertion order preserved) 
        last_completed = progress["done"][key]
    if last_completed and "cumulative_after" in last_completed:
        cumulative_gain = max(cumulative_gain, float(last_completed["cumulative_after"]))
```

---

### Issue #3: Progress Key Mismatch After Template Resorting
**Location:** `_spec_fill_workbook()` line 769-774 (`_next_pending`)  
**Problem:**
- When worksheet is resorted (columns reordered), row numbers change
- `progress["done"]` keys still use OLD row numbers: `"{sheet}!{old_row}:{switch}={cand}"`
- `_next_pending()` builds `per_tab_rows` from CURRENT worksheet, which has DIFFERENT row numbers
- Result: Keys never match → `_next_pending()` doesn't find already-completed rows as pending
- All previously completed rows are processed AGAIN (or appear stuck)

**Fix:** Use switch name lookup, not row number
```python
def _next_pending(sname: str):
    for (rr, sw, cand) in per_tab_rows.get(sname, []):
        # First try exact key match (same row number)
        key_exact = f"{sname}!{rr}:{sw}={cand}"
        if key_exact in progress.get("done", {}):
            continue
        
        # Fallback: if row number mismatch after resorting, find by switch+cand name
        key_by_switch = f"{sw}={cand}"
        if any(key_by_switch in k for k in progress.get("done", {}).keys() if k.startswith(sname + "!")):
            continue
            
        return (rr, sw, cand)  # This row hasn't been processed yet
    return None
```

---

### Issue #4: Incomplete Tab Rotation / Cycling
**Location:** `_spec_fill_workbook()` lines 836-850, 1125-1158  
**Problem:**
- When processing NEG delta (line 1125-1158), code looks for NEXT tab (starting from offset=1)
- If current tab still has pending rows but all OTHER tabs are complete, the loop will:
  1. Not find any other tab with pending rows
  2. Advance current_idx anyway (line 1154)
  3. On next iteration, check current_idx (which is now complete), find no pending rows
  4. Try to find next tab again (won't find any since we're looping)
  5. Eventually hit max_loops and exit

**Fix:** Track if we've cycled all tabs without progress
```python
_cycles_without_progress = 0
_max_empty_cycles = len(tabs) + 1

while _any_pending() and loop_guard < max_loops:
    loop_guard += 1
    
    # Check if current tab has pending
    sname = tabs[current_idx % len(tabs)]
    pending = _next_pending(sname)
    
    if pending is None:
        # Current tab complete, move to next with pending
        # ... existing logic ...
        # but track: if we cycled and found nothing, break (all done)
        if not _any_pending():
            break
```

---

### Issue #5: E2 Header / E3 Baseline Corruption
**Location:** Main function line 2510-2511  
**Problem:**
- When writing baseline in SINGLE-LOAD (line 2507-2511):
  ```python
  ws.cell(row=2, column=5).value = "BASELINE"  # E2 = header
  ws.cell(row=3, column=5).value = float(baseline_gain)  # E3 = value
  ```
- BUT: if column 5 isn't actually "BASELINE" column (due to resorting), writes go to wrong place
- Also: overwrites whatever was in E2 (header might already have been there)

**Fix:** Use `_resolve_cols()` to find correct column, check header first
```python
for sname in SWITCH_SHEETS:
    if sname in wb_single.sheetnames:
        ws_fix = wb_single[sname]
        cols = _resolve_cols(ws_fix)  # Get actual column mapping
        e_col = cols["E"]
        # Only write E2 if it's not already a header
        e2_val = ws_fix.cell(row=2, column=e_col).value
        if e2_val is None or (isinstance(e2_val, str) and e2_val.strip().upper() not in ("BASELINE",)):
            ws_fix.cell(row=2, column=e_col).value = "BASELINE"
        ws_fix.cell(row=3, column=e_col).value = float(baseline_gain)
```

---

### Issue #6: BEST-C-FILL Only Overwrites if Current C is Different
**Location:** Main function line 2498  
**Problem:**
- Line 2498 checks: `if cur_c is None or str(cur_c).strip().upper() != val_str.strip().upper()`
- If C already has the override value, it's skipped (not refilled)
- BUT: the font styling (bold for override) might be wrong
- When resuming, overrides might not have bold styling applied

**Fix:** Always apply styling, not just when value differs
```python
cur_c = ws_c.cell(row=r, column=3).value
if cur_c is None or str(cur_c).strip().upper() != val_str.strip().upper():
    ws_c.cell(row=r, column=3).value = val_str
    filled_c += 1
# Always apply styling for overrides (regardless of value match)
if sw in overrides:
    ws_c.cell(row=r, column=3).font = Font(name="Arial", size=10, bold=True, color="000000")
    ws_c.cell(row=r, column=3).alignment = Alignment(horizontal="left", vertical="center")
```

---

## RECOMMENDED FIX ORDER

1. **Fix #1 (max_loops)** - Prevents early exit (CRITICAL)
2. **Fix #3 (key mismatch)** - Prevents infinite loops on resume (CRITICAL)  
3. **Fix #2 (baseline carry)** - Prevents losing progress on resume (HIGH)
4. **Fix #4 (tab cycling)** - Ensures all tabs visited (HIGH)
5. **Fix #5 (E2 header)** - Prevents corruption (MEDIUM)
6. **Fix #6 (C styling)** - Ensures proper formatting (MEDIUM)

---

## TESTING STRATEGY

After fixes, test with:
- Large symbol_side (>2000 rows) to verify max_loops doesn't hit
- Resume from partial progress.json to verify baseline carry-over
- Worksheet with resorting to verify key matching
- Multiple tabs with mixed POS/NEG to verify cycling

Check logs for:
- `[spec-fill] incomplete after loop guard` messages (should not appear)
- `[spec-row]` messages should show all tabs visited
- No rows appearing in both `done` keys and unreprocessed

