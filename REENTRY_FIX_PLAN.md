# REENTRY SYSTEM FIX — 2026-04-21

## Goal
After a reduction/partial-exit, guarantee 100% reentry for any symbol in tradeable_keys:
- **Bounce below exit price**: FULL REENTRY at 100–150% of max qty held before reduction
- **Break above exit price**: 50–100% depending on k_15m/k_1h (within first hour: 50% straight away when k_3m bounces above exit price)
- **8 sources checked every minute** for the exit price (no single-source failure can break reentry)

## Files Involved
- `ez_positions_quick.py` — guaranteed_reentry_loop, reentry logic (LOCKED 2026-04-21)
- `ez_manage.py` — process_position, reentry decision flow (LOCKED 2026-04-20+)
- `v8_quick_engine.py` — vectorized reentry for backtest

## Status
- [ ] Read reentry code in ez_positions_quick.py
- [ ] Read reentry code in ez_manage.py
- [ ] Read ang logs to find failure cases
- [ ] Identify root causes
- [ ] Fix ez_positions_quick.py (need user to unlock)
- [ ] Fix ez_manage.py (need user to unlock)
- [ ] Fix v8_quick_engine.py for backtest validation
- [ ] Run 4-year backtest and confirm 100% reentry execution
- [ ] Sync to S1+S2

## Root Causes Found
(fill in after reading)

## Fixes Applied
(fill in after applying)

## Backtest Results
(fill in after running)
