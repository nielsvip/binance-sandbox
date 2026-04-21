# REENTRY SYSTEM FIX — 2026-04-21

## Goal
After a reduction/partial-exit, guarantee 100% reentry for any symbol in tradeable_keys:
- **Bounce below exit price**: FULL REENTRY at 100–150% of max qty held before reduction
- **Break above exit price**: 50–100% immediately (within first hour: 50% no questions asked)
- **8 sources checked every minute** for the exit price

## Files Involved
- `ez_manage.py` — LOCKED (needs unlock)
- `ez_positions_quick.py` — LOCKED (needs unlock)
- `v8_quick_engine.py` — likely editable

---

## ROOT CAUSE ANALYSIS

### BUG 1 — CRITICAL: k_15m UnboundLocalError crashes ALL reentry orders
**File:** `ez_manage.py` line 10951
**Error seen in logs:** `cannot access local variable 'k_15m' where it is not associated with a value`

**What happens:**
1. Reentry enforcement loop fires action="REENTRY" with override_qty set (non-zero)
2. In execute_trade_action: action REENTRY → converted to AUGMENT at line 10601
3. Line 10951: `if not override_qty or 'REENTRY' in action:` → evaluates to **False**
   - `not override_qty` = False (override_qty is set)
   - `'REENTRY' in action` = False (action is now 'AUGMENT')
4. **ENTIRE BLOCK SKIPPED** — including k_15m, k_3m, d_15m and 150+ indicator variable assignments at line 10985
5. Later code uses k_15m → UnboundLocalError

**Evidence:**
```
[execute_trade_action] ENTRY: ang:GRASSUSDT_LONG AUGMENT BUY qty=26.250000 $9.07
[ang:GRASSUSDT_LONG] Handle order exception (Attempt 1): cannot access local variable 'k_15m' where it is not associated with a value
```
4 consecutive identical failures. Then position bled away via STALL_SUB + RATIO_REBALANCE. Expired 546h later.

**Fix (1 line change):**
```python
# Line 10951: Change from:
if not override_qty or 'REENTRY' in action:
# To:
if not override_qty or 'REENTRY' in action or _original_action_was_reentry:
```

---

### BUG 2 — CRITICAL: Strong trend gate blocks reentry EVEN when price already crossed exit level
**Files:** `ez_manage.py` lines 17910-17918 AND `ez_positions_quick.py` lines 14120-14131

**What happens:**
When k_15m > 80 (strong uptrend) and k_3m hasn't bounced yet, BOTH `process_single_reentry_evaluation` AND `process_single_reentry_evaluation_epq` return without queuing — even when current_price is already ABOVE the exit level.

**Evidence:**
```
[process_single_reentry_evaluation] ang:GRASSUSDT_LONG: k_15m 86.25907058665508 strong trend but no bounce yet
```
GRASS exit level = 0.293847, current price = 0.345409 (17.5% above exit). Still blocked.

**User spec:** "if it breaks the exit price: enters with 50-100% depending on k_15m/k_1h — straight away no questions asked in the first hour with 50%"

**Fix in BOTH functions:**
Add `price_above_reduction` check BEFORE the strong_trend gate:
```python
# Compute BEFORE line 17910 (ez_manage) / 14120 (ez_positions_quick):
price_above_reduction = (is_long and current_price >= reentry_level) or (not is_long and current_price <= reentry_level)

# Then at the top of the strong_trend block — if price already crossed, bypass all gates:
if price_above_reduction:
    # PRICE ALREADY CROSSED EXIT — force reentry immediately (within 1h: 50%, after 1h: per k_15m/k_1h)
    _force_pct = 0.5 if min_since_exit < 60 else (0.5 if (k_15m > 70 or k_1h > 70) else 1.0)
    # ... queue reentry with _force_pct sizing and return
else:
    # existing strong_trend check below
```

---

### BUG 3 — SECONDARY: EPQ enforcement loop never gets GRASS into pending_reentries
The `reentry_enforcement_loop_epq` only reads `trade_manager.service.reentry_data` or `trade_manager.reentry_data`. GRASS data was sourced from a FILE (`long_reentry.json`) by the REENTRY_MONITOR loop. The EPQ loop never saw it → never triggered the T1_PRICE_CROSS_MANDATORY path.

**Fix:** The REENTRY_MONITOR should also populate `trade_manager.reentry_data` when it finds a file-based reentry entry, so the EPQ enforcement loop can also act on it.

---

### BUG 4 — SECONDARY: Stale REENTRY_STALE items (37-56h old) never firing
Multiple symbols stuck with reentry pending for 37-56h. They're in `trade_manager.pending_reentries` with status='pending' but guards block them (SYMGATE, RALLY_K15M, etc.). None of these guards should apply when price_crossed=True, but the price may not have crossed for those symbols.

This is not a bug per se — if price never crossed and no oversold bounce occurred, reentry was not triggered. These will resolve when price eventually bounces or crosses. However the 72h expiry needs investigation.

---

## Fix Execution Plan

### Step 1 — Backup + fix ez_manage.py (NEEDS UNLOCK)
1. Backup: `cp ez_manage.py backups/before_reentry_k15m_fix_YYYYMMDDHHMM.py`
2. Fix line 10951: add `or _original_action_was_reentry`
3. Fix lines 17908-17918: add `price_above_reduction` early exit before strong_trend gate
4. Compile test
5. Sync to S1 + S2

### Step 2 — Backup + fix ez_positions_quick.py (NEEDS UNLOCK)
1. Backup
2. Fix lines 14119-14131: add `price_above_reduction` early exit before strong_trend gate
3. Compile test
4. Sync to S1 + S2

### Step 3 — Fix v8_quick_engine.py backtest
1. Add T1_PRICE_CROSS_MANDATORY vectorized block: within window_bars after exit, if price returns to exit level, force 50% reentry
2. Run 4-year backtest
3. Confirm reentry fires 100% of the time

---

## Status
- [x] Root causes identified (3 bugs)
- [ ] WAITING FOR UNLOCK: ez_manage.py, ez_positions_quick.py
- [ ] Fix Bug 1: ez_manage.py line 10951
- [ ] Fix Bug 2: ez_manage.py lines 17908-17918
- [ ] Fix Bug 2: ez_positions_quick.py lines 14119-14131
- [ ] Fix Bug 3: REENTRY_MONITOR → populate trade_manager.reentry_data
- [ ] Fix Bug 4 (v8): T1_PRICE_CROSS vectorized block
- [ ] 4-year backtest run + confirm
- [ ] Sync S1 + S2
