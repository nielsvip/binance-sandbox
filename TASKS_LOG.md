# Tasks Log — Live Trading Audit (started 2026-05-08)

Master record of issues, decisions, and pending edits surfaced during the fin-account audit. Updated each turn so context survives compaction.

## Live edits authorized by user

| # | Edit | File | Status |
|---|---|---|---|
| **A** | Pass `reason` directly into `handle_augmentation` / `handle_reduction` (drop Redis decision-cache lookup that mis-attributes thrash fills). | `ez_positions_service.py` (handle_augmentation, handle_reduction, _append_to_history; PAU/WS callers at 2731-2736 + 6753-6767) | **APPROVED — pending execute** |
| **B** | Hoist `BLOCKED_NON_TRADEABLE_POSITION_KEY` (line 11087) and `BLOCKED_ALREADY_AUGMENTED` (line 12193) to the **top** of `execute_trade_action` before BB_CONFIDENCE / RATIO_BOOST / MANIP_FLAG_SIZE_CUT. | `ez_manage.py` | **APPROVED — pending execute** |
| **D-NEW** | Add CLOSE rule: when `abs(gain) < 0.05% AND wt_velocity_15m sign-against-position AND |vel|<|vel_prev|` → CLOSE (bypass STRICT_NO_LOSS since gain ≈ 0). Add wherever exit signals are generated. | `ez_manage.py` (next to RIDICULOUS_HOLD / DC_BB_D_BREAK_REVERSE / ALL_TF_AGAINST_CLOSE branches), `ez_positions_quick.py` (process_position exit gates) | **APPROVED — pending execute** |
| C | ratio_rebalance pre-filter against `tradeable_keys` at `ez_manage.py:16664` | `ez_manage.py` | Approved earlier — pending after A/B |
| (Edit "FLIP_COOLDOWN") | New per-pkey AUGMENT/REDUCE cooldown | — | **REJECTED** by user — MTF confirmation should make this unnecessary; if thrash recurs, find the HTF-blind caller and gate it instead |

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
