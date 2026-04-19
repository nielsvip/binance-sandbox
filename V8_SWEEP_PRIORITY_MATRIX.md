# V8 Sweep Priority Matrix — UNTESTED LIVE CHANGES

**Status:** 2026-04-19 — several live changes applied without sweep coverage. Dangerous. This doc tracks which knobs need testing, what engine can simulate them, and the priority.

**Ban reminder:** Per `feedback_no_scalar_backtests.md`, `v8_quick_sweep` / `backtest_v8_sweep` / `tradier_vectorized_mass` / `nightly_lab` / `sentinel` are banned. Only `vec_mass_scan` / `vec_backlog` / `vec_phase2` / `vec_validate` allowed. But those are entry-forward-return only — they CANNOT simulate hedge, stall-sub, wrong-side-kill, ratio rebalance, or account-level substitution.

**Engine coverage reality:**
| Engine | Per-symbol entry | Exit | Hedge | Ratio rebalance | Stall-sub | Cross-position |
|--------|------------------|------|-------|-----------------|-----------|----------------|
| vec_mass_scan / vec_backlog | ✅ fwd-return | ❌ | ❌ | ❌ | ❌ | ❌ |
| v8_quick_engine (banned per scalar rule) | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| backtest_v8_engine (banned) | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| V5 tournament | ✅ | ✅ | partial | ❌ | ❌ | ❌ |

---

## RECENT LIVE CHANGES — PRIORITY ORDER (P0 = highest)

### P0 — Cross-position logic with zero sweep coverage

Requires new engine extension to simulate. Cannot be validated with existing infra.

| # | Change | File | Config switch | Current live default | Engine needed |
|---|--------|------|---------------|----------------------|---------------|
| 1 | `WRONG_SIDE_ABS_KILL` — force-close non-hedges on 5/5 WT + 3/3 K against | ez_manage.py process_position | `WRONG_SIDE_ABS_KILL_ENABLED` (True), `WRONG_SIDE_MIN_AGE_MIN` (30), `WRONG_SIDE_WT_TFS_REQUIRED` (5), `WRONG_SIDE_K_TFS_REQUIRED` (3) | active | Per-position exit sim — covered by v8_quick_engine if ban lifted |
| 2 | `STALL_SUB` — close stalled flat-delta positions every 30s | ez_manage.py ratio_rebalance_loop | `STALL_SUB_ENABLED` (True), `STALL_AGE_MIN_MIN` (180), `STALL_GAIN_ABS_MAX` (0.5), `STALL_DELTA_SPEED_MAX` (1.0), `STALL_MAX_CLOSES_PER_CYCLE` (2) | active | Cross-position account-level sim — NO engine supports this today |
| 3 | L/S ratio reflects sentiment (compute_applied_ratio) | ez_manage.py | `RATIO_MULTIPLIER` (4.0), formula change (sentiment-anchored) | active | Account-level ratio sim — NO engine supports this today |

### P1 — Per-hedge logic with zero sweep coverage

Hedge simulation doesn't exist in any vectorized engine.

| # | Change | File | Config switch | Current live default | Engine needed |
|---|--------|------|---------------|----------------------|---------------|
| 4 | Hedge ENTRY gate = gain<0 only (no indicator check) | ez_positions_quick.py `_hedge_entry_is_valid` | — (hardcoded to `return True`) | active | Hedge lifecycle sim — not built |
| 5 | Hedge EXIT = wt_3m AND wt_1h (no k) | ez_positions_quick.py `_hedge_should_exit`, `HEDGE_CLOSE_WT3M1H_ABS`; ez_manage.py R6 | `HEDGE_EXIT_BYPASS_NOLOSS` (True), `HEDGE_EXIT_DELTA_CHECK_ENABLED` (False) | active | Hedge lifecycle sim — not built |
| 6 | `hedge_gain >= 0.1` refuse-to-close gates removed | ez_positions_quick.py monitor_hedge_health_loop (5 sites) | — (hardcoded) | active | Hedge lifecycle sim — not built |

### P2 — Nice-to-have knobs for sensitivity analysis (once engine exists)

- `WRONG_SIDE_MIN_AGE_MIN`: [15, 30, 60, 120] — tightness of newborn grace
- `WRONG_SIDE_WT_TFS_REQUIRED`: [3, 4, 5] — strictness of WT unanimity
- `WRONG_SIDE_K_TFS_REQUIRED`: [2, 3] — strictness of K unanimity
- `STALL_AGE_MIN_MIN`: [60, 120, 180, 240, 360] — what counts as "old enough to kill"
- `STALL_GAIN_ABS_MAX`: [0.2, 0.3, 0.5, 1.0] — stall band width
- `STALL_DELTA_SPEED_MAX`: [0.5, 1.0, 2.0, 3.0] — stall delta threshold
- `STALL_MAX_CLOSES_PER_CYCLE`: [1, 2, 3, 5] — close rate
- `RATIO_MULTIPLIER`: [2.0, 3.0, 4.0, 5.0, 6.0] — sentiment amplification
- `HEDGE_EXIT_DELTA_CHECK_ENABLED`: [False, True] — safety net on/off

---

## WHAT TO BUILD BEFORE THESE CAN BE SWEPT

**Minimum viable extension to v8_quick_engine:**
1. Per-position opened_at tracking (stall age computation)
2. Account-level L/S breadth tally (sentiment anchor + rebalance skew)
3. A 5-WT-TF + 3-K-TF "all against" check per bar for wrong-side kill
4. Synthetic hedge lifecycle (open on origin<0, close on wt_3m+1h against)
5. Stall-sub decision pass every N bars

This unlocks P0 #1-3 and P1 #4-6. Estimated ~6-10 hours of engine work.

**Alternative path:** Paper-validate for 5-7 days on one small account (e.g. `fin`, `flz`) with telemetry written to `data/decisions/`, then compare to prior window.

---

## TRACKING

- [ ] Build engine extension (unblocks P0/P1)
- [ ] Run P0 sweep (WRONG_SIDE + STALL grid, ~200 configs)
- [ ] Run P1 sweep (hedge lifecycle, ~100 configs)
- [ ] Run P2 sensitivity grid
- [ ] Re-validate each live change against sweep winners → adjust or disable

Until tracking items complete, **all P0/P1 changes are live on unverified math.** Kill switches in `config.py` permit fast rollback: flip `WRONG_SIDE_ABS_KILL_ENABLED=False` and `STALL_SUB_ENABLED=False` to revert to prior behavior.
