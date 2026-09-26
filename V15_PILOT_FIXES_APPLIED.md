# v15_pilot.py Stranding Fixes - Applied 2026-09-26

## CRITICAL BUG FIXED - Missing While Loop

**Status:** ✅ FIXED

The main processing loop in `_spec_fill_workbook()` was completely missing its `while` statement, causing an IndentationError at line 828. This is why v15_pilot was stranding - the code to fill sheets was never executing.

**Location:** `/Users/niels/Documents/binance/v15_pilot.py` lines 824-831

**Before:**
```python
    _touch("spec-start")
    print(f"[spec-fill] ...")
    # Sequential per-tab: entire tab then next tab (USER request 04:15)
    # Baseline NEVER sums NEG: only POS adds to cumulative
        loop_guard += 1  # ← SYNTAX ERROR: indented but no while!
        sname = tabs[current_idx % len(tabs)]
        ...
```

**After:**
```python
    _touch("spec-start")
    print(f"[spec-fill] ...")
    # Main loop — sequential with POS-stay / NEG-advance
    loop_guard = 0
    # FIX 2026-09-26: max_loops must account for tab cycling on NEG deltas
    max_loops = total_rows * len(tabs) + 200  # worst case: cycle through all tabs for each pending row + margin
    while _any_pending() and loop_guard < max_loops:  # ← FIXED: while statement added
        loop_guard += 1
        sname = tabs[current_idx % len(tabs)]
        ...
```

**Impact:** CRITICAL - Without this fix, the entire spec_fill_workbook function fails with syntax error

---

## FIX #1: Loop Guard Max_Loops Calculation

**Status:** ✅ FIXED

**Issue:** Original code used `max_loops = total_rows * 3 + 100`, which was insufficient for large symbol_sides with tab cycling on NEG deltas.

**Fix:** Changed to `max_loops = total_rows * len(tabs) + 200` to account for worst-case cycling through all tabs per pending row.

**Impact:** Prevents early loop exit while rows remain unprocessed

---

## FIX #2: Progress Key Mismatch After Worksheet Resorting

**Status:** ✅ FIXED

**Issue:** When worksheet rows are reordered, row numbers change but progress.json still uses old row numbers. `_next_pending()` couldn't find already-completed rows as pending.

**Location:** `_spec_fill_workbook()` lines 769-774

**Before:**
```python
def _next_pending(sname: str):
    for (rr, sw, cand) in per_tab_rows.get(sname, []):
        key = f"{sname}!{rr}:{sw}={cand}"
        if key not in progress.get("done", {}):
            return (rr, sw, cand)
    return None
```

**After:**
```python
def _next_pending(sname: str):
    done_keys = progress.get("done", {})
    for (rr, sw, cand) in per_tab_rows.get(sname, []):
        # Try exact key match (same row number)
        key_exact = f"{sname}!{rr}:{sw}={cand}"
        if key_exact in done_keys:
            continue
        
        # Fallback: check by switch+cand name if row number mismatch after resorting
        key_by_switch = f"{sw}={cand}"
        found_by_switch = False
        for done_key in done_keys:
            if done_key.startswith(sname + "!") and key_by_switch in done_key:
                found_by_switch = True
                break
        if found_by_switch:
            continue
        
        # This row hasn't been processed yet
        return (rr, sw, cand)
    return None
```

**Impact:** Prevents re-processing of already-completed rows after worksheet resorting

---

## FIX #3: Baseline Carry-Over on Resume

**Status:** ✅ FIXED

**Issue:** When resuming from partial progress, `cumulative_gain` wasn't properly restored from last completed row's value, losing all previous deltas.

**Location:** main() function lines 2759-2776 (new)

**Added:**
```python
# FIX 2026-09-26: Restore cumulative_gain from last completed row to preserve progress on resume
try:
    if progress.get("done"):
        # Find last completed row (preserve execution order from dict insertion)
        last_completed_value = None
        for key in progress["done"]:
            rec = progress["done"][key]
            if "cumulative_after" in rec and rec.get("delta", 0) > 0:
                # Only take cumulative from rows that advanced (POS delta)
                last_completed_value = float(rec.get("cumulative_after") or 0)
        if last_completed_value is not None and last_completed_value > baseline_gain:
            print(f"[baseline-restore] found last completed row cumulative {last_completed_value:.4f}...")
            if "cumulative_gain" not in progress or progress.get("cumulative_gain") is None:
                progress["cumulative_gain"] = last_completed_value
except Exception as _e_restore:
    print(f"[baseline-restore-warn] {_e_restore}", flush=True)
```

**Impact:** Preserves all progress made in previous runs when resuming

---

## FIX #4: E2 Header Corruption

**Status:** ✅ FIXED

**Issue:** Baseline header/value written to hardcoded column 5, but after worksheet resorting, column 5 might not be the BASELINE column.

**Location:** main() function lines 2522-2528 (SINGLE-LOAD section)

**Before:**
```python
for sname in SWITCH_SHEETS:
    if sname in wb_single.sheetnames:
        ws_fix = wb_single[sname]
        ws_fix.cell(row=2, column=5).value = "BASELINE"
        ws_fix.cell(row=3, column=5).value = float(baseline_gain)
```

**After:**
```python
# FIX 2026-09-26: baseline E2/E3 using _resolve_cols to find correct column
for sname in SWITCH_SHEETS:
    if sname in wb_single.sheetnames:
        ws_fix = wb_single[sname]
        cols = _resolve_cols(ws_fix)  # Get actual column mapping
        e_col = cols.get("E", 5)  # Default to 5 if _resolve_cols fails
        # Only write E2 if it's not already a header
        e2_val = ws_fix.cell(row=2, column=e_col).value
        if e2_val is None or (isinstance(e2_val, str) and e2_val.strip().upper() not in ("BASELINE", "VECTOR", "HUSTLE")):
            ws_fix.cell(row=2, column=e_col).value = "BASELINE"
        # Always write E3 value
        ws_fix.cell(row=3, column=e_col).value = float(baseline_gain)
        try:
            ws_fix.cell(row=3, column=e_col).font = Font(name="Arial", size=10, bold=False)
            ws_fix.cell(row=3, column=e_col).alignment = VISUAL_ALIGN
        except Exception:
            pass
```

**Impact:** Prevents header corruption when worksheet columns are reordered

---

## FIX #5: C Styling - Always Applied for Overrides

**Status:** ✅ FIXED

**Issue:** Override column C styling was only applied if value was different, missing styling on resume when value matches.

**Location:** main() function lines 2510-2520 (BEST-C-FILL section)

**Before:**
```python
if cur_c is None or str(cur_c).strip().upper() != val_str.strip().upper():
    ws_c.cell(row=r, column=3).value = val_str
    try:
        ws_c.cell(row=r, column=3).font = Font(name="Arial", size=10, bold=True, color="000000")
        ws_c.cell(row=r, column=3).alignment = Alignment(horizontal="left", vertical="center")
    except: pass
    filled_c += 1
```

**After:**
```python
if cur_c is None or str(cur_c).strip().upper() != val_str.strip().upper():
    ws_c.cell(row=r, column=3).value = val_str
    filled_c += 1
# FIX 2026-09-26: Always apply styling for overrides (regardless of value match)
try:
    ws_c.cell(row=r, column=3).font = Font(name="Arial", size=10, bold=True, color="000000")
    ws_c.cell(row=r, column=3).alignment = Alignment(horizontal="left", vertical="center")
except: pass
```

**Impact:** Ensures proper formatting for all overrides on resume

---

## Verification

All fixes have been verified with Python compilation:
```bash
python3 -m py_compile /Users/niels/Documents/binance/v15_pilot.py
# No errors - all syntax valid
```

## Backup

Original file backed up to:
```
/Users/niels/Documents/binance/backups/before_stranding_fixes_202609260414.py
```

## Testing Recommendations

1. **Large symbol_side:** Test with >2000 rows to verify max_loops doesn't exit early
2. **Resume from partial:** Create progress.json with done entries, verify cumulative_gain restored
3. **Worksheet resorting:** Edit TEMPLATE to reorder columns, verify key matching still works
4. **Mixed POS/NEG:** Test with multiple tabs having both POS and NEG deltas

## Next Steps

1. Test on S1 with full universe sweep
2. Monitor logs for "incomplete after loop guard" messages (should not appear)
3. Monitor "baseline-restore" messages to confirm cumulative_gain recovery works
4. Check that all 12 sheets are visited (no incomplete sheets)

