# DISASTER FIXES — All Identified Problems & Required Fixes

**Date**: 2026-03-04
**Situation**: ang account lost 20% in 12 hours. 107 open positions, 87 losing. Market only down 2%.

---

## ROOT CAUSES IDENTIFIED

### 1. `direct_high_gain_augmentation()` — ez_manage.py line 16355
**Problem**: Forces augment evaluation on ANY positive gain. 2,830+ occurrences in logs.
- Line 16492: Allows augmentation on ANY gain >= 0 when position < START_POSITION_SIZE
- Keeps pumping money into positions with 0.001% gain
**Fix**: Require gain >= 1.0% minimum before any augmentation. Already added AUGMENT_GATE in execute_now but the function itself still fires constantly.

### 2. `evaluate_reentry()` — ez_manage.py line 13196
**Problem**: Primary entry mechanism for ALL catastrophic positions. Sub-strategies too aggressive:
- `s0_recovery_reentry`: Re-enters positions that just lost money
- `s0_quick_turnaround`: Opens on minimal bounce signals
- `s1_aggr_reentry`: Aggressive re-entry on tiny stoch alignment
- `s2_price_based`: Re-enters based on price alone
- `BIG_GAIN_FALLBACK_REENTRY` (line 13297): Very loose conditions
NOT THE ISSUE. YOU NEED TO LOOK AT HEDGEENGINE!!!!
**Fix**: Add minimum stoch alignment requirements, require trend confirmation, block re-entry when stoch is declining.

### 3. `QUICK_HEDGE_ACTUAL_OPEN` bug — ez_positions_quick.py line 2619
**Problem**: "cannot access local variable 'qty'" causing infinite retry loops and 36+ timeout occurrences. Creates shadow positions on Binance that aren't tracked locally.
**Fix**: Fix the variable scoping bug.

### 4. `_manage_individual_position()` pyramiding — ez_positions_quick.py line 5723
**Problem**: Augments at 0.5% gain with PULLBACK reason when k_15m < 30 (stoch low = price falling). Pyramids INTO falling positions.
**Fix**: Require stoch to be RISING (k > prev_k), not just low. Raise minimum gain to 1.5%.

### 5. No force-close of orphaned/non-tradeable positions
**Problem**: 107 open positions but many aren't even tradeable keys anymore. System blocks NEW trades on orphans but NEVER closes existing losing orphan positions. They just sit there bleeding.
**Fix**: Add periodic scan that force-closes any position NOT in tradeable_keys that is in loss.
ANY POSITION SHOULD BE CLOSED BEFORE LOSING WHILE BEING HEDGED AND THE HEDGE SHOUDL BE CLOSED AND THE LOSING POSITION REOPENED AS SOON AS THE HEDGE GAIN STARTS GOING DOWN. HOW FUCKING HARD IS THAT

### 6. Hedge stays open when tide turns (SKYAI)
**Problem**: Long was losing, short hedge was winning. Tide turned, hedge started losing too. Hedge STAYED OPEN. Now BOTH sides losing (-0.57% long, -0.18% short = $5.88 total loss).
**Fix**: Hedge must close IMMEDIATELY when its gain drops below previous gain reading (gain < prev_gain). Already added fixes to 9 hedge code paths but they're not aggressive enough — threshold should be ANY decline in gain, not just < -0.005%.
ANY POSITION SHOULD BE CLOSED BEFORE LOSING WHILE BEING HEDGED AND THE HEDGE SHOUDL BE CLOSED AND THE LOSING POSITION REOPENED AS SOON AS THE HEDGE 
### 7. `_process_single_monitor_direct_high_gain()` — ez_manage.py line 17014
**Problem**: Lines 17014-17021 BLOCK reductions when gain < 0.5%, effectively locking tiny-gain positions. Line 17030 `if position.gain < 0.5: return` prevents any action on sub-0.5% positions.
**Fix**: Remove the reduction block. Allow reductions at any gain level. Positions with gain < 0.5% should be REDUCED, not locked.
We have other funcitons that should be taking care of the 0.5 gain look at the md's that have categorized the functions .if they are gone make them again they are important and were meant to AVOID SITUATIONS LIKE THIS
### 8. Orphan position injection (DIRECT_INJECT)
**Problem**: All 6 hedge pairs traced to DIRECT_INJECT at 03-03 23:58:44 — untracked positions on Binance that got auto-injected into the system without proper tracking. These become orphans that no cleanup catches.
**Fix**: DIRECT_INJECT positions that are in loss should be immediately flagged for graceful exit, not adopted as real positions.
BUY LONGS WHEN SHIT GOES UP LOSE THEM WHEN RALLY STALLS VV
---

## FIXES ALREADY APPLIED (in execute_now gate)

| Fix | Location | What |
|-----|----------|------|
| DRAIN_PROTECTION bypass | ez_manage.py:12061 | GAIN_GUARD/FORCE bypass drain block |
| ABORT STOP bypass | ez_manage.py:12063 | GAIN_GUARD/FORCE bypass abort block |
| retention_qty bypass | ez_manage.py:12073 | GAIN_GUARD/FORCE bypass retention |
| AUGMENT_GATE | ez_manage.py:11983+ | Block ALL augments if gain < 1.0% |
| SIZE_GATE | ez_manage.py:11983+ | Block augment if position > 3x START_SIZE |
| SHORT_SMA_GATE | ez_manage.py:11983+ | Shorts above sma_200_15m limited |
| HEDGE_AUGMENT_BLOCK | ez_manage.py:11983+ | Block augment on losing hedges |

## FIXES ALREADY APPLIED (in ez_positions_quick.py hedge system)

| Fix | Location | What |
|-----|----------|------|
| Losing hedge kill in balancer | line 2234+ | Kill hedge if gain < -0.01% |
| Hedge augment requires gain >= 0.05% | balancer | No augment on losing hedge |
| FORTIFY disabled | fortify section | Was causing infinite hedge growth |
| _manage_hedge block if losing | _manage_hedge | Return immediately if hedge losing |
| execute_dual_hedge same-symbol block | line 2579+ | Block if existing hedge losing |
| execute_dual_hedge elected block | line 2537+ | Skip losing hedge candidates |
| execute_same_symbol_hedge block | same_symbol | Block if existing hedge losing |
| aggressive_hedge_scanner kill | scanner | Detect and kill losing hedges |
| Kill threshold -0.02% → -0.005% | multiple | More aggressive kill |

---
## FIXES NOW APPLIED (2026-03-04)

### Priority 1: EMERGENCY
1. **DONE** Force-close all non-tradeable losing positions — added MITIGATOR_ORPHAN_KILL in ez_loss_mitigator.py
2. **DONE** Kill ALL losing hedges immediately — tightened to 0% (any loss = instant kill)
3. **DONE** Hedge tide-turn detection — HEDGE_TIDE_TURN_KILL: close hedge when gain declining AND < 0.05%
4. **DONE** When hedge is winning, close the LOSING position (not the hedge) — HEDGE_PROFIT_COORD logic + KILL loser when hedge profitable

### Priority 2: Source functions
5. **DONE** direct_high_gain_augmentation() — HARD GATE: gain < 1.0% = BLOCKED
6. **DONE** gain >= 0 path blocked — changed to gain >= 1.0
7. **DONE** _manage_individual_position() — raised to 1.5%, removed PULLBACK, requires stoch RISING
8. **DONE** _process_single_monitor_direct_high_gain() — removed chop zone block, raised gain gate to 1.0%
9. **DONE** execute_trade_wrapper MIN_AUGMENT_GAIN raised 0.5% → 1.0%

### Skipped
- evaluate_reentry() — user confirmed NOT THE ISSUE
- QUICK_HEDGE_ACTUAL_OPEN — code correct, transient runtime error

### Priority 3: ASSASSIN REMOVAL (2026-03-04 06:00)
10. **DONE** HEDGE_PROFIT_DECLINING — neutered: now requires 3+ consecutive declines AND > 0.03% drop (was: any decline = kill)
11. **DONE** HEDGE_TIDE_TURN_KILL — raised threshold: now only fires when hedge goes INTO LOSS (was: < 0.05%)
12. **DONE** HEDGE_WINNING_KILL_LOSER — raised: hedge must be > 0.3% winning AND original > -0.5% losing (was: 0.05% / -0.02%)
13. **DONE** HEDGE_BAL_FAILURE_KILL — DISABLED: API failure is not the position's fault
14. **DONE** scan_and_hedge_losers — BLOCKED for < 1% loss AND < $50 notional (was: -0.3% any size)
15. **DONE** aggressive_hedge_scanner — BLOCKED for < 1% loss AND < $50 notional (was: -0.25% any size)
16. **DONE** BREAKOUT_GUARD hedge trigger — BLOCKED for < 1% loss AND < $50 notional
17. **DONE** ratio_rebalance_loop — skips positions with gain > 0.3% (was: kills any gain level)
18. **DONE** RatingRegistry top list threshold 12 → 20 (fewer low-quality entries)
19. **DONE** RatingRegistry auto-discovery threshold 24 → 30 (no untested symbols)
20. **DONE** _inject_opportunities() — DISABLED (was force-feeding entries)
21. **DONE** get_hottest_hedge min score 10 → 20 (better hedge candidates)
22. **DONE** Orphan kill threshold -0.05% → 0.0% (any loss on orphan = instant kill)

### Still TODO
- Account-level circuit breaker when total loss > 5%
