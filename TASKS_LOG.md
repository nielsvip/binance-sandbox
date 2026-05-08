# Tasks Log — Live Trading Audit (started 2026-05-08)

Master record of issues, decisions, and pending edits surfaced during the fin-account audit. Updated each turn so context survives compaction.

## Edits SHIPPED 2026-05-08 22:50–23:15 UTC

| # | Edit | Files | md5 (Mac=S1 verified) |
|---|---|---|---|
| **A** | _recent_order_reasons ring on TradeManager populated by send_webhook; _append_to_history prefers qty-matched entry over Redis decision_context (curse fix). | ez_manage.py / ez_positions_service.py | ezm=453947ee … epsv=cf62b3dd |
| **B** | First-line tradeable + augmented filter in process_position (line 20482), check_entry_candidates_for_account (line 14376), evaluate_reentry_2 inner loop (line 16429). | ez_manage.py / ez_positions_quick.py | ezm=453947ee … epq=9fb85f76 |
| **C** | ratio_rebalance: open-underweight path GATED OFF behind RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY=True. New close-overweight branch sorts by smallest \|wt1_15m - wt2_15m\|, max 3 closes/cycle. Open path also adds tradeable_keys filter for the day we re-enable it. | ez_manage.py | ezm=453947ee |
| **D-NEW** | New WT_15M_VEL_SLOW_AT_ZERO_GAIN exit branch above ALL_TF_AGAINST_CLOSE in process_position. \|gain\|<0.05% AND wt_velocity_15m sign-against AND \|vel_now\|<\|vel_prev\| → CLOSE. | ez_manage.py / config.py | ezm=453947ee … cfg=e74f1e6d |

**Live workers NOT restarted** — they're still running pre-edit code. New code only takes effect when watchdog respawns or user restarts. Per CLAUDE.md auto-restart on critical files is NOT enabled.

## S1 sweep relaunched 2026-05-08 23:17 UTC

- Old sweeps (PIDs 11064/11066/12455/12487, --start 2026-01-01, 8 crypto + 20 tradier syms) killed.
- `watchdog_sweep_s1.sh` edited (backup `.before_4wk_*` saved on S1) — `--start 2026-04-08` (4 weeks), crypto expanded to 12 syms (added LTCUSDC, UNIUSDC, DOGEUSDC, CRVUSDC).
- New PIDs 548360/548436 (crypto) + 549060/549100 (tradier) running. Sweep banner confirms md5 fingerprints from this session's edits: ez_manage=453947ee23 / ez_positions_quick=9fb85f76bf / config=e74f1e6d56.
- system_combo grid = 52 variants × 12 syms × 4 wk. tradier_param_hunt grid × 20 syms × 4 wk. Each variant ~3-7 min vs ~18 min on the 4-month window.

## NOT YET DONE — pending user direction

1. **Restart live ez_manage / ez_positions_service workers** so they pick up Edits A/B/C/D-NEW. Per CLAUDE.md no auto-restart on critical-parity files. Restarting risks position-state hiccups during the swap. **User: explicitly OK to restart?**
2. **Expand backtest_v8_sweep.grid_system_combo()** to cover the ~25 knobs identified above (DELTA_ENTRY_MIN_TF 1..5, NO_STRUCT_OR_BREAKOUT looser, HEDGE_CLEANUP_R6 wt-flip variants 3m / 3m+1h / 3m+15m, AUGMENTED floor 0.25/0.5/1.0×, REENTRY_K15M_THR 85/90/95, ALL_TF_AGAINST count 3/4/5, PEAK_GIVEBACK 0.3/0.5/0.75/1.0, WT_15M_VEL_SLOW_GAIN_BAND_PCT 0.02/0.05/0.10, RATIO_REBALANCE_MAX_CLOSES 1/3/5, etc.). Critical-parity file — backup + rsync.
3. **Verify the 10 known cooldowns/locks are actually firing** for the NOTUSDT thrash pattern. User assertion: ~10 cooldown systems exist; one or more must be bypassed since thrash happens. Inventory + audit.
4. **GOLDEN_RULE definition matrix** — still pending user pick (1/5..5/5 × wt/mfi/rsi/all). Currently disabled in grid (toggle ON/OFF only).

## Live edits authorized by user (executing now in batch)

| # | Edit | File | Status |
|---|---|---|---|
| **A** | Pass `reason` directly into `handle_augmentation` / `handle_reduction` (drop Redis decision-cache lookup that mis-attributes thrash fills). | `ez_positions_service.py` (handle_aug, handle_red, _append_to_history; callers at 2731-2736, 6753-6767) | **EXECUTING** |
| **B** | Pre-source filter at the CALLER sites of `process_position`, `check_entry_candidate*`, `evaluate_reentry*` — NOT inside the functions. Filter by `tradeable_keys` + open-position validity + (positionAmt>0 AND gain<0.5×MIN_GAIN → skip). FIRST LINE of every entry-side loop. Plus first-line filter inside the functions themselves as defense-in-depth. | `ez_manage.py` (callers + first lines of process_position / check_entry_candidates / evaluate_reentry / evaluate_reentry_2 / queue_processor) | **EXECUTING** |
| **C** | ratio_rebalance: (1) filter candidates by tradeable_keys + open-position state. (2) When imbalance detected, **close the OVERWEIGHT side** picking positions with smallest `|wt1_15m - wt2_15m|` delta (least conviction, easiest to close). NO opening on underweight side until system is verified working. | `ez_manage.py:16410+` (ratio_rebalance_loop) | **EXECUTING** |
| **D-NEW** | CLOSE when `abs(gain) < 0.05% AND wt_velocity_15m sign-against-position AND |vel_now| < |vel_prev|` (bypass STRICT_NO_LOSS since gain ≈ 0). Add wherever exit signals are generated — process_position exit branches in ez_manage AND ez_positions_quick. | `ez_manage.py` + `ez_positions_quick.py` | **EXECUTING** |
| (FLIP_COOLDOWN) | per-pkey AUGMENT/REDUCE cooldown | — | **REJECTED** by user — MTF confirmation should suffice. Flag for re-investigation if thrash continues. |
| GOLDEN_RULE | Definition matrix (1/5..5/5 × wt/mfi/rsi/all) | — | **DEFERRED** — concept stage, not enabled in current sweeps. Gets its own scoping pass after current edits ship. |

## Open user questions / decisions needed

| ? | Topic | Status |
|---|---|---|
| **D-DEFINE** | What IS GOLDEN_RULE conceptually? `1/5..5/5` of which set? wt? mfi? rsi? Multi-indicator? Sweep matrix can't be designed until this is defined. | **BLOCKING** sweep design |
| Threshold: `REENTRY_K15M_PARTIAL_THRESHOLD` 90 vs 95 (config.py:1023) | one-line | Pending user pick |

## Findings to verify still

- [ ] **Per-pkey thrash in NOTUSDT_SHORT 03-28 23:22→23:46** (5 reverses in 25min). User assertion: existing dicts/locks (`augmented_positions`, `reduced_positions`, position-object timestamps, tracker.json hedges) ALREADY have this state with timers. **Action**: enumerate every cooldown/lock mechanism currently in code; verify which one *should* have stopped each of the 5 reverses; identify the broken / bypassed one. If NONE were operative — that's the bug to fix.
- [ ] **Confirm NO_STRUCT_OR_BREAKOUT is firing as documented** (ez_manage.py:481-509). 97 SHORT entries blocked today; would have made +4.58% over the day per the CSV. Is this gate too tight or correctly catching false breakouts?

## S1 sweep status (as of 2026-05-08 22:35)

```
PIDs running:
  11064/11066 backtest_v8_sweep --mode crypto  --account ang --start 2026-01-01 --symbols 8 --tier system_combo --workers 1
  12455/12487 backtest_v8_sweep --mode tradier --account trb --start 2026-01-01 --symbols 20 --tier tradier_param_hunt --workers 1
```

**Problem 1**: `--start 2026-01-01` = ~4 months of bars. User wants **4 weeks** for parameter A/B (cheap signal) → start should be `2026-04-08`. Each variant ~18.5min on 4 months → ~2-4min on 4 weeks. **5x faster turnover.**

**Problem 2**: Current `system_combo` grid covers ~12 knob families:
1. ENTRY_SCORE_THRESHOLD
2. K_ZONE_BONUS
3. WT_EXIT_MIN_TFS (1/2/3/4/5 — this *is* the MTF count user described)
4. STRUCTURAL_EXIT TF
5. RZ_EXIT on/off
6. PPL_GAIN
7. REENTRY_SIZE
8. DELTA_ENGINE on/off + HTF gate {none/any/all}
9. SATOSHIT
10. FUNDING_GATE thresholds
11. ENTRY_SCORE_THRESHOLD (dup)
12. GOLDEN_RULE on/off

**Knobs NOT yet in the grid** (~25–30 of the things discussed):

| Knob | Why test |
|---|---|
| DELTA_ENTRY_MIN_TF (1..5) | User: "DELTA_4h_AGAINST cannot be a single rule, must be N-of-5" |
| DELTA_HTF_GATE crypto: `none` / `4h` / `4h_D` / `D` | Today's CSV: 250 SHORTs blocked, would-be +3.45%/D |
| NO_STRUCT_OR_BREAKOUT looser variants | CSV: 97 blocks, would-be +4.58%/D |
| HEDGE_CLEANUP_R6 wt-flip trigger: 3m / 3m+1h / 3m+15m / 3m+15m+1h | Currently 3m+1h |
| HEDGE_CLEANUP_R6 commission buffer 0.0 / 0.05 / 0.10 / 0.15% | Currently 0.10 |
| HEDGE_BANDAID_OFF wt_3m vs wt_15m | Currently wt_15m |
| AUGMENTED_POSITIONS_GUARD floor: 0.25 / 0.5 / 1.0 × MIN_GAIN | Currently 0.5× |
| REENTRY_K15M_PARTIAL_THRESHOLD: 85 / 90 / 95 | Currently 90 |
| ALL_TF_AGAINST_CLOSE count: 3/5 / 4/5 / 5/5 | Currently 5/5 |
| DC_BB_D_BREAK_REVERSE on/off | Currently ON |
| PARABOLIC_PROTECTION rsi/bb thresholds | Currently 70/65/0.90 |
| WT_4H_VEL_EXIT velocity threshold | Currently 5.0 |
| PEAK_GIVEBACK pct: 0.3 / 0.5 / 0.75 / 1.0 | Currently 0.5 |
| NOLOSS_BYPASS_WT_5OF5 ENABLED + MIN_TFS | Currently OFF |
| HEDGE_TRIGGER_LOSS_PCT_ENTRY: -1 / -1.5 / -2 / -3 | Currently -2 |
| RATIO target / multiplier | Currently 75/25, mult 3.0 |
| LINEARITY_LR LONG/SHORT enabled | Currently OFF |
| STDEV_BREAKOUT / STDEV_BOUNCE | Currently ON for tradier |
| BB_CONFIDENCE multiplier scale | One scale value tested |
| MANIP_FLAG mult + max_usd | Currently 0.3 / $10 |
| MICRO_SCALP rules | Largely untested |
| GOLDEN_RULE TF set + indicator basis | **Blocked on D-DEFINE** |
| GOLDEN_RULE_MULT_BREAKOUT / _RETEST | Currently 0.1 / 5.0 |
| WT velocity exit threshold | Currently fixed |

## NO-LIES enforcement reminder

- Every result row goes through `metrics_guard.write_sharpe_row()` — the 9 canonical fields are mandatory.
- Sample floor: ≥48 crypto / ≥100 stocks × >1yr × ≥30 trades/sym for any **publishable** Sharpe. 4-week × 12-symbol runs are **`[DIAGNOSTIC ONLY]`** — they tell us *direction* of a knob (helps / hurts), not absolute numbers we promote to live.
- v8_quick is the lying-engine; backtest_v8_engine is truth (per memory `feedback_v8quick_is_lie_backtest_v8_is_truth_20260507`). Continue with backtest_v8_sweep.

## Past audit results referenced

- CSV `data/fin_blocked_decisions_20260508.csv` — 390 blocked entries with 15m/1h/4h/D forward returns
  - DELTA_NO_SIGNAL_htf=4h_D: 250 blocks, avg +3.45%/D in trade direction
  - ENTRY_VET_NO_STRUCT_OR_BREAKOUT: 97 blocks, +4.58%/D
  - DELTA_HTF_4h_AGAINST: 20 blocks, +2.49%/D
  - NON_TRADEABLE_POSITION_KEY: 14 blocks, +2.70%/D (waste — should never reach this gate)
  - ALREADY_AUGMENTED (AIA): 8 blocks, **−12.21%/D** (gate correctly saved pain)
- The GOLDEN_RULE typo `dc_h_15m` (`ez_manage.py:7108`) crashed the live breakout-retest loop every iteration since 2026-05-05 21:57 UTC. 590 BREAKOUT/RETEST detections today, **0 successful execute_now calls** from this loop. Intervention queue (separate loop) is the only path delivering GOLDEN_RULE_*_INTERVENTION fills.
- DELTA_HTF_GATE='4h_D' for crypto has **NO sweep evidence on disk** — only `phase4_htfport_tradier_*.csv` exists, and it's tradier HTF_W_M_ALIGN/HTF_DC_BREAKOUT, not DELTA. Untested for crypto.
- `_append_to_history` reads reason from Redis `decision:<pkey>` cache — race condition mis-attributes thrash fills (Edit A fixes this).
- `ratio_rebalance_loop` (line 16410) pulls candidates from `_reg.top_longs[:30]` un-filtered against tradeable_keys → MORPHO_LONG ghost entries (Edit C fixes).
- positionAmt is stored **abs** everywhere (line 1712, 1720, 1733, 7173, 7454, 5608). Side is in `position_side` and pkey suffix only. NEVER signed.
- `is_reentry_eligible` (ez_reentry.py:348-394) DOES filter tradeable + augmented for the reentry loop. The wasted-compute issue is in `execute_trade_action` (Edit B fixes).

## Cooldowns / locks / timers known to exist (verify not bypassed)

Task pending: enumerate and verify each is honored. User assertion: ~10 of these exist; thrash bug means at least one is broken or bypassed.

- `_AUGMENT_LOCK_MIN_SECONDS` (900s on `augmented_positions` dict)
- `_ABSOLUTE_OPEN_LOCK` (5min TTL)
- `processing_keys` set (per-pkey in-flight guard)
- `_pkey_last_action_ts` — does NOT exist (no flip cooldown)
- `_history_dedup` (10s window in `_append_to_history`)
- `_dc_bb_d_break_state` per-symbol break tracker
- `_underwater_safety_cooldown`
- `_all_tf_against_cooldown` (30s default)
- `_AUTH_BAN_UNTIL` (300s WS auth thrash)
- `_golden_last_trigger` (600s per pkey in GOLDEN_RULE loop)
- `_open_lock` / `_pos_lock` per-account
- `pending_reentries` status state machine
- `tracker.active_hedges` — daily-cap + per-pkey cooldown via Redis

## Snapshot — what's running on machines

| Machine | Process | Status |
|---|---|---|
| MacBook | ez_manage --account {ang,inf,fin,flz,men}, tradier_manage --account {trb,trc} | live trading |
| S1 | backtest_v8_sweep crypto (8 syms ang) + tradier (20 syms trb) — system_combo + tradier_param_hunt — start=2026-01-01 (4 months — should be 4 weeks) | running |
| S2 | dead since 2026-05-08 | do not touch |
