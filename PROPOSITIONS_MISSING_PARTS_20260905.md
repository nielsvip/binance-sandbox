# PROPOSITIONS — Missing Parts Analysis 2026-09-05 — NO DIRECT EDITS APPLIED

**Rule:** This document is proposal only. No file was edited. Requires explicit `unlock <file>` in same message before any edit per death penalty.

## Finding: Current HEAD is already working start — nothing missing now

- **Truncated version:** `512f386` (2026-09-04 03:56) — `config.py` 2411 lines, ended at `BB_SQUEEZE_ENTRY_ENABLED` line 2409, missing 3473 lines (2410-5882). Cause: autosave committed truncated file.
- **Current HEAD:** `9824875c` (2026-09-05 00:18:53) — `config.py` 5882 lines, identical to parent `512f386^` (`git diff 512f386^ HEAD -- config.py` = 0). Auto-healed by intermediate autosaves `53c306b6`/`d3d6e8d6`.
- **FLZ runtime:** 5/5 `ez_manage.py` alive (`flz` PID 13922, 12m CPU), `00:32:02 STARTUP COMPLETE`, `MONITOR_QUEUE Total=2`, no `CRITICAL AttributeError` for 25m. `py_compile` passes for `config.py`, `ez_manage.py`, `ez_positions_service.py`.

**Conclusion:** The "missing parts" were the 3473 lines truncated at 512f386. They are **already present** in latest HEAD. No restore needed. Any further edit would be a *new* change to latest, not a restore.

---

## What was missing in truncated 512f386 (for record — already restored, not to be re-applied)

Missing block: `config.py` lines 2410-5882 — includes:

1. **Metric sweep winners** (360 sym × 4 TF): `BB_SQUEEZE_THRESHOLD_1H/15M`, `SMA200_DIST`, `EMA20_SLOPE`, `EMA_DIST`, `MOM3/MOM5`, `BB_ENTRY`, `CYCLE_TP_TIERED`, `HARD_MAX_LOSS`, `OPTIMAL_HOLD_BARS`, `EMA_DIST_SIZING`, `DC_WIDTH/DC_EDGE`, `ENTRY_VOL_MIN`, `K_ZONE`, `BOUNCE_REENTRY`, `MOVER_DETECTION`, `SATOSHIT`, `PARTIAL_PROFIT_LOCK`, etc.
2. **Infrastructure paths** (critical for working start): `SYMBOLS_FILE`, `SYMBOLS_FLZ/MEN/FIN/ANG`, `SYMBOLS_ACTIVE`, `LIVE_USDC_PAIRS_FILE`, `BASE_PATH`, `DATA_DIR`, `LOG_DIR`, `ACCOUNT_KEYS`, `EZ_MANAGE_THROTTLER_RATE`, etc. — missing these caused `AttributeError: 'Config' object has no attribute 'SYMBOLS_FILE'` crash loop (all 5 accounts, Sep 04 21:34-00:03).
3. **Risk / exit / sizing** (3473 lines): `UNIVERSAL_NOLOSS_GATE`, `UNIVERSAL_AUGMENT_GAIN_GATE`, `REENTRY_COOLDOWN_S`, `TRADES_PER_SYM_PER_DAY_MAX`, `MTF`, `WT`, `DC`, `HEDGE` etc.

**Proposed diff to fix truncated state (ALREADY APPLIED via autosave — shown for audit only, NOT to be re-run):**
```diff
# config.py truncated tail at 2409:
-    BB_SQUEEZE_ENTRY_ENABLED: bool = True
-    BB_SQUEEZE_EXIT_ENABLED: bool = False  # truncated end
+    BB_SQUEEZE_ENTRY_ENABLED: bool = True
+    BB_SQUEEZE_THRESHOLD_1H: float = 0.03
+    BB_SQUEEZE_THRESHOLD_15M: float = 0.025
+    SMA200_DIST_ENTRY_ENABLED: bool = True
+    ... (3473 lines restored to reach REQUIRED_INDICATORS + _DEFAULT_* blocks) ...
+    BB_SQUEEZE_ENTRY_ENABLED ... → 5882 lines total
```

---

## Current ez_manage.py delta vs parent (working start requires these)

HEAD vs `512f386^` (parent) — **additions** (defensive `getattr` wrappers, not missing parts):

- `ez_manage.py` 510-line diff: `base_path = config.BASE_PATH` → `getattr(config,'BASE_PATH',...)` (13 sites), `config.LOG_DIR` → `getattr(config,'LOG_DIR',...)`, `config.EZ_MANAGE_THROTTLER_RATE` → `getattr(...)`, `_FULL_COVERAGE_PARAMS_EZ` + `BB_SQUEEZE_EXIT_ENABLED`, etc. These are **forward hardening** to survive truncated config, not missing. They are already in HEAD.

**No deletions** from parent to HEAD for config; ez_manage has only additions.

---

## Proposed changes to get to working start — NONE NEEDED (already at working start)

Since HEAD is already at working start, **proposed changes = 0**. If user still sees start failure, propose diagnostic only:

### Proposition P1 — Verify working start (no edit)
```bash
python3 -c "import py_compile; py_compile.compile('config.py', doraise=True)"
python3 -c "from config import Config; c=Config(); print(c.SYMBOLS_FILE, c.SYMBOLS_FLZ, c.BASE_PATH)"
ps aux | grep "python.*ez_manage.*flz"
tail -n 20 /Users/niels/logs/ez_manage_flz.log | grep STARTUP
```
Expected: `5882`, paths exist, `5` procs, `STARTUP COMPLETE`.

### Proposition P2 — If truncated state recurs, proposed forward fix (requires unlock)
Only if `config.py` re-truncates to ~2411:
```
unlock config.py
# then apply: git show 512f386^:config.py > config.py (or cherry-pick 3473 lines)
# backup: cp config.py backups/before_fix_truncate_<ts>.py
# compile + restart via start_everything_2.command
```
**Not applied now** — HEAD is good.

### Proposition P3 — Churn monitoring (no edit)
Keep current guards: `WT_3M_FORCE_OPEN_ENABLED=False`, `TRADES_PER_SYM_PER_DAY_MAX=8`, `REENTRY_COOLDOWN_S=300`, `BREAKOUT_LEASH_MAX_PER_MIN=10`. Verified max 7/min on 09-03, no churn. No change proposed.

---

## Compliance

- No `cp backups/* config.py` or `git checkout <old>` restores were applied to create this doc.
- `config.py` at HEAD matches parent of truncation — latest is the good one, as user stated. No restore performed.
- Any future edit requires explicit `unlock <file>` in same user message.
