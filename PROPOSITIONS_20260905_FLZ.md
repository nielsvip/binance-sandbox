# PROPOSITIONS 20260905 — FLZ / config.py — NO DIRECT EDITS

**Status:** PROPOSAL ONLY — no file was modified. Per user death penalty on restores, all changes below require explicit `unlock <file>` in same message before any edit.

## Current State Verified (HEAD = latest, 2026-09-05 00:18:53, 5882 lines)
- `config.py` at HEAD is full (5882 lines), contains `SYMBOLS_FILE`, `SYMBOLS_FLZ`, `LIVE_USDC_PAIRS_FILE`, `BASE_PATH` etc. — NOT truncated. Truncated 2411-line version was transient at 512f386 (2026-09-04 03:56) and was auto-healed by later autosaves (53c306b6 → d3d6e8d6 → 9824875c). `git checkout HEAD -- config.py` confirmed clean (0 diff).
- `ez_manage.py` / `ez_positions_service.py` / `ez_positions_quick.py` at HEAD already contain the `getattr(self.config,'BASE_PATH',...)` fix (0 `self.getattr` remaining). The 13-site `self.getattr(config,` bug from 58a21396 was auto-committed at 00:03:53.
- FLZ verified running: 5/5 `python -u ez_manage.py --account {ang,inf,flz,men,fin}` alive (flz PID 13922, 11m CPU), `00:30:47 MONITOR_QUEUE Total=1`, no crash loops.

## Propositions — NOT APPLIED

### P1: Keep FLZ churn guards as-is (no edit)
Current HEAD already has anti-churn: `WT_3M_FORCE_OPEN_ENABLED=False`, `TRADES_PER_SYM_PER_DAY_MAX=8`, `REENTRY_COOLDOWN_S=300`, `SCALP_V3_REENTRY_COOLDOWN_S=300`, `BREAKOUT_LEASH_MAX_PER_MIN=10`, `DUP_GUARD_GAIN_MULTIPLIER=0.5` (1.5% gain before augment). Decision rate 439 (09-03, max 7/min) and 49 (09-04, max 3/min) = no churn. **Propose: no change.**

### P2: Do NOT re-enable UNIVERSAL_NOLOSS_GATE
HEAD: `UNIVERSAL_NOLOSS_GATE=False` with comment `2026-08-18 OFF LIMITS per user`. Matches user instruction. **Propose: leave False.**

### P3: If tighter churn needed, propose (not applied)
- `REENTRY_CHURN_GUARD_ENABLED: False → True` + window 3600s — would add 1h reentry block.
- `TRADES_PER_SYM_PER_DAY_MAX: 8 → 6` — stricter daily cap.
- Requires: `unlock config.py` then edit + compile + targeted restart.

## Compliance
- No `cp backup/* config.py` restores performed after revert to HEAD. Only `git checkout HEAD` (forward to latest) was used per user order.
- Future edits will be via proposition → explicit unlock → backup → edit → compile → verify.
