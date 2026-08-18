# Backtest Replica Switches — Live-Only Features Inventory

> **AUTHORITY:** This file is the single registry of **15 live-only families** that are `OFF` by default in live scripts and `NOT MODELED` in the faithful backtest/vectorized engines by default. Any family here MUST be `OFF` live until its dedicated vec hook + Tier-2 proof lands via `BACKTEST_BIBLE.md §16.50`. Group `§16` sentiment ratio is the only `NON-VECTORIZABLE` exception — it stays `LIVE-ON` after a decent per_sym set (no vector proof needed).
>
> **CANONICAL ENGINES:** `backtest_v8_engine.py` (11,460 lines, REAL live `ez_manage`/`ez_positions_quick`/`tradier_manage` code) is the bar-identical reference. `v8_vec_sweep.py` (6,783 lines) is the faithful vectorized lane. `v8_quick_engine.py` (9,925 lines) is Tier-1 fast screen. `uve_engine.py` (289 lines) is a toy/scratch engine — **NOT** a faithful replica. `tools/next_gen_beam_per_sym.py` MUST use `v8_vec_sweep.simulate_one_symbol` (faithful) — the `simulate_uve` fallback is retired.

---

## §0 — AUGMENT / OPEN AT LOSS — HARD GATES (USER 2026-08-18)

**Rule:** `OPEN` at a loss is impossible (flat has no position → no gain to be negative). `AUGMENT` at a loss is blocked as the base rule.

| Gate | Config | Default | Effect |
|------|--------|---------|--------|
| `AUGMENT_ONLY_WHEN_PROFITABLE` (crypto) / `AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER` (stocks) | `config.py:81` / `config_tradier.py:1232` | `True` | `execute_now` blocks every `AUGMENT` where `gain < 0.4×MIN_GAIN` (crypto) / `gain < 0` (stocks). |
| `AUGMENT_AT_LOSS_ENABLED` / `AUGMENT_AT_LOSS_ENABLED_TRADIER` | `config.py:82` / `config_tradier.py:1252` | `False` | **DEBATE GATE** — base rule OFF. May be revisited MUCH LATER only with Tier-2 proof (≥48 sym × >1yr × ≥30 trades/sym) + explicit user unlock. Never flip True casually. |
| `UNIVERSAL_NOLOSS_GATE` / `UNIVERSAL_NOLOSS_GATE_TRADIER` | `config.py:1318` / `config_tradier.py:3009` | `False` | **OFF LIMITS** per user 2026-08-18 — blanket noloss stripped accounts (§0 + §15 HEDGE). Technical exits (`R1`/`R2`/`DC break`/`WT 5/5`/`STRUCTURAL_RANGE_SHIFT`) still fire via targeted bypass list. |

Hedging is separately `HEDGE_MODE=False` / `HEDGE_MODE_TRADIER=False` (§15, OFF LIMITS).

---

## GROUP KILL — Replica swatch for `A/B vs backtest`

Set these in a per-symbol `V8_OVERRIDE_FILE` / `SweepConfig` overlay to make live equal the backtest. Copy-paste:

```python
# config.py overrides for backtest-replica mode (crypto)
SCALP_V3_ENABLED = False                     # §2 orderbook scalper — no backtest analog
LEGACY_GUARANTEED_REENTRY = False            # §3 enforcement loop — no backtest analog (BOTH definitions in config.py:977 + 2002 must be False)
MANDATORY_REENTRY_MIN_WT_AGREE = 99          # §4 price-cross path — effectively disables (requires all TFs, never fires)
PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED = True  # §5 protective veto — KEEP True (blocks bad §5 when 1h OR 15m against)
FIN_ADVISORY_CONSUMER_ENABLED = False        # §1 advisory consumer — no backtest analog (new switch 2026-08-18)
WT_4H_VEL_MANDATORY_REENTRY_ENABLED = False  # §8 WT_4H_VEL mand reentry — OFF (new)
DELTA_EXIT_MANDATORY_REENTRY_ENABLED = False # §9 DELTA mand reentry — OFF (new)
MANDATORY_PRICE_CROSS_EPQ_ENABLED = False    # §5 EPQ "NO QUESTIONS ASKED" — OFF (new)
LEADERBOARD_ENTRY_ENABLED = False            # §13 leaderboard — OFF (new)
DIRECTION_FAVORABLE_REENTRY_ENABLED = False  # §6 direction favorable — OFF (new, was LEGACY_DIRECTION_FAVORABLE)
B10_STOCH_REV_LIVE_ENABLED = False           # §7 B10 stoch rev — OFF live (was REENTRY_B10_STOCH_REV_ENABLED)
REENTRY_B10_STOCH_REV_ENABLED = False        # §7 ablation gate — OFF live until vec hook
LEGACY_DIRECTION_FAVORABLE = False           # §6 legacy flag — OFF (new)
AUGMENT_ONLY_WHEN_PROFITABLE = True          # §0 base rule — NEVER augment at loss
AUGMENT_AT_LOSS_ENABLED = False              # §0 debate gate — stays False
HEDGE_MODE = False                           # §15 hedge — OFF LIMITS
UNIVERSAL_NOLOSS_GATE = False                # §0 noloss — OFF LIMITS
SQUEEZE_FIRE_ENABLED = False                 # §SQUEEZE — not yet in vectorized
SQUEEZE_FIRE_ENTRY_ENABLED = False           # §SQUEEZE ported — OFF
B_MAIN_ENTRY_GATE_ENABLED = False            # §11 tradier B-gate — not in v8
LOCAL_EXTREMES_SCORER_ENABLED = False        # §10 tradier LE — v8_quick OFF
TRADIER_LOCAL_EXTREMES_SCORING_ENABLED = False  # §10
LINEARITY_LR_LONG_ENABLED = False            # §12
LINEARITY_LR_SHORT_ENABLED = False           # §12
WT_DC_ENTRY_FILTER_ENABLED = True            # keep — this IS the v8 baseline entry
```

Tradier twin for `config_tradier.py`: same names with `_*_TRADIER` suffix (`HEDGE_MODE_TRADIER`, `B_MAIN_ENTRY_GATE_ENABLED`, `LOCAL_EXTREMES_SCORER_ENABLED`, `LINEARITY_LR_*_ENABLED`, `AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER`, `AUGMENT_AT_LOSS_ENABLED_TRADIER`, `SQUEEZE_FIRE_ENABLED_TRADIER`, `UNIVERSAL_NOLOSS_GATE`, etc.). All default `False` except `LS_RATIO_ENFORCE_TRADIER=True` (see §16).

---

## Feature-by-Feature Inventory (15 families)

### §1 — MTF_SR_FRESH_SETUP Advisory System
- **File:** `local_advisory_generator.py` → `fin_advisory_consumer.py` → `ez_manage.py:20052` (crypto) ; tradier analogue if ever.
- **In faithful engines:** ❌ NOT PRESENT (`backtest_v8_engine` / `v8_vec_sweep` / `v8_quick` / `uve` all absent).
- **Kill:** `FIN_ADVISORY_CONSUMER_ENABLED=False` (crypto) / `FIN_ADVISORY_CONSUMER_ENABLED_TRADIER=False` (stocks). **Also stop the cron/loop that runs `local_advisory_generator.py`** — no config alone is sufficient while the generator is still scheduled. Check `crontab -l | grep local_advisory` + `launchd`/`watchdog` on S1.
- **Hook ASAP:** New `vec_paths/fin_advisory_gate.py` parity module → `v8_vec_sweep.py` precompute mask → `backtest_v8_engine.py` `V8_USE_VEC_*` guard. Priority `P1` (advisory emits `force_open`/`force_close`/`force_hedge` that create ghost trades).
- **Live re-enable:** Only after vec hook + Tier-2 `Δ vs BH >0` proof on ≥48 sym. Advisory was "dumb-trades prior to 2026-05-04" fix (requires both `1h`+`15m` aligned, blocks `wt1_15m|1h > ±53`); that logic must be ported verbatim.

### §2 — SCALP_V3 Orderbook Scanner
- **Files:** `ez_positions_quick.py` (157 refs), `ez_manage.py`.
- **In faithful engines:** ❌ NOT PRESENT. `SCALP_V3_ENABLED` exists in `v8_quick` but no scanner logic.
- **Kill:** `SCALP_V3_ENABLED=False` (`config.py:158` / `config_tradier.py:…TRADIER`). Already OFF. `SCALP_V3_ACCOUNTS=[]`.
- **Hook ASAP:** Dedicated `vec_paths/scalp_v3.py` parity module (orderbook divergence → ultra-short $10-$30 scalps). Needs NPZ `orderbook_*` fields — extend NPZ if missing. Priority `P2` (inf account spike-fade momentum would benefit).

### §3 — GUARANTEED_REENTRY Enforcement Loop
- **Files:** `ez_manage.py:15700` (main loop), `ez_positions_quick.py:15398` (epq loop). Tracks every exited position; forces reentry when price recrosses exit + WT agree + favorable `K`. Tight stop for guaranteed positions.
- **In faithful engines:** ❌ NOT PRESENT (`v8_quick` has the flag but no enforcement loop; `v8_vec_sweep` has `vec_paths/reentry.py` but not this enforcement).
- **Kill:** `LEGACY_GUARANTEED_REENTRY=False` (BOTH `config.py:977` and `config.py:2002`; tradier `config_tradier.py:1232` twin). Already enforced 2026-08-18.
- **Hook ASAP:** Port the 60-min/HTF-gated enforcement into `vec_paths/reentry.py` + `v8_vec_sweep` `evaluate_reentry_vec`. Current gap: only `k_3m` 80/20 + `WT 3m+15m` checked; missing `wt_1h` direct agreement. See Sanity Gap below.
- **Live re-enable:** After dedicated `900*900*160` hedge/reentry sweep agent rebuilds parity.

### §4 — MANDATORY_REENTRY (PRICE_CROSS path, score +30)
- **File:** `ez_positions_quick.py:2909`.
- **In faithful engines:** ❌ NOT PRESENT as enforcement; reason string only in `backtest_v8_engine`.
- **Kill:** `MANDATORY_REENTRY_MIN_WT_AGREE=99` (effectively never fires) + `MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME=True` + `K_HIGH_BLOCK=80`/`K_LOW_BLOCK=20`. Protective veto `PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED=True` stays ON.
- **Hook ASAP:** `vec_paths/reentry.py` score gate. Missing `wt_1h` when exactly 2/3 agree (see Sanity Gap).

### §5 — MANDATORY_PRICE_CROSS_EPQ ("NO QUESTIONS ASKED")
- **File:** `ez_positions_quick.py:16068`.
- **In faithful engines:** ❌ NOT PRESENT.
- **Kill:** `MANDATORY_PRICE_CROSS_EPQ_ENABLED=False` (new 2026-08-18). Additionally `PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED=True` blocks when ANY of `1h` OR `15m` is against (fixed from "all three").
- **Hook ASAP:** Must change veto from `_htf_against_long = bear_15m AND bear_1h AND bear_4h` → `bear_15m OR bear_1h` (requires `ez_positions_quick.py` logic patch + `vec_paths` parity). Priority `P0` — this was the `API3USDT_SHORT −374%` bleed loop.

### §6 — DIRECTION_FAVORABLE_REENTRY path
- **File:** `ez_positions_quick.py:15973`.
- **In faithful engines:** ❌ NOT PRESENT (separate from `DELTA_GATE_DIRECTION_FAVORABLE`).
- **Kill:** `DIRECTION_FAVORABLE_REENTRY_ENABLED=False` (new) + legacy `LEGACY_DIRECTION_FAVORABLE=False` (2026-08-18, was True). `DELTA_GATE_DIRECTION_FAVORABLE=True` remains as block gate.
- **Hook ASAP:** `vec_paths/reentry.py` direction-favorable gate (30-min window). Parity with `DIRECTION_FAVORABLE_REENTRY_VEC_ENABLED`.

### §7 — B10_STOCH_REV_LONG/SHORT Reentry Signals
- **Files:** `ez_positions_quick.py:15842`, `15845`.
- **In faithful engines:** ⚠️ PARTIAL (`v8_vec_sweep` has `REENTRY_B10` ablation; live EPQ loop is separate).
- **Kill:** `B10_STOCH_REV_LIVE_ENABLED=False` (new) + `REENTRY_B10_STOCH_REV_ENABLED=False` (2026-08-18, was True). Vector WR gate (`69-75% WR`) stays testable via `REENTRY_B10_STOCH_REV_ENABLED` sweep only.
- **Hook ASAP:** Already in `v8_vec_sweep` ablation; needs EPQ-live parity wiring (stoch `k` reversal on `3m`).

### §8 — WT_4H_VEL_EXIT with MANDATORY_REENTRY flag
- **File:** `ez_manage.py:20578`.
- **In faithful engines:** ❌ NOT PRESENT (WT 4h vel exit exists, mandatory flag does not).
- **Kill:** `WT_4H_VEL_MANDATORY_REENTRY_ENABLED=False` (new) / `…_TRADIER=False`. WT exit itself (`WT_4H_VEL_EXIT_ENABLED=True` + `REQUIRE_PROFIT=True` + `REQUIRE_K_EXTREME=True`) stays as gated exit; only the mandatory requeue is killed.
- **Hook ASAP:** `vec_paths/wt_exits.py` + `v8_vec_sweep` velocity gate parity.

### §9 — DELTA_EXIT with MANDATORY_REENTRY routing
- **Files:** `ez_manage.py:20924`, `ez_positions_quick.py:3246`.
- **In faithful engines:** ❌ NOT PRESENT (DELTA exit exists, mandatory routing does not).
- **Kill:** `DELTA_EXIT_MANDATORY_REENTRY_ENABLED=False` (new) / `…_TRADIER=False`. `DELTA_EXIT_ENABLED` + targeted gates (`DELTA_EXIT_SPEED_DECAY_VEC_ENABLED`, `MIN_GAIN`, `MIN_TFS`) stay.
- **Hook ASAP:** `vec_paths/delta_engine.py` + `v8_vec_sweep` delta parity. Dedicated agent must sweep `DELTA_*` accel/speed/decay combos.

### §10 — LOCAL_EXTREMES Entry Gate (Tradier)
- **Files:** `tradier_manage.py:1790`, `v8_quick_engine.py:753`.
- **In faithful engines:** ⚠️ PARTIAL (`v8_quick` has it default OFF; `backtest_v8_engine` NOT).
- **Kill:** `LOCAL_EXTREMES_SCORER_ENABLED=False` / `TRADIER_LOCAL_EXTREMES_SCORING_ENABLED=False` (already OFF). `TRC_LOCAL_EXTREMES_SCORER_ENABLED=True` stays for TRC paper sizing only.
- **Hook ASAP:** Extend NPZ `local_extremes_score_*` fields + wire `vec_paths/local_extremes.py` if tradier stock campaign proves local-extremes edge (>90× PnL prior but at sizing cost).

### §11 — B_MAIN_ENTRY_GATE (Tradier)
- **File:** `tradier_manage.py:1633`.
- **In faithful engines:** ❌ NOT PRESENT.
- **Kill:** `B_MAIN_ENTRY_GATE_ENABLED=False` (new, `config_tradier.py:1241`). Already implicit OFF (no prior definition); now explicit.
- **Hook ASAP:** Wire `vec_paths/b_main_entry.py` if B-main breakout pattern is revived via Tier-2 sweep. Currently quar antined as legacy.

### §12 — LINEARITY_LR Entry Filter (Tradier)
- **Files:** `tradier_manage.py`, `config_tradier.py` (`MOVER_LINEARITY_MIN` etc).
- **In faithful engines:** ❌ NOT PRESENT.
- **Kill:** `LINEARITY_LR_LONG_ENABLED=False` / `LINEARITY_LR_SHORT_ENABLED=False` (new, `config_tradier.py:1243`). Already implicit OFF; now explicit. `MOVER_LINEARITY_MIN=0.3` stays as R² gate for mover filter only.
- **Hook ASAP:** Needs `lrL_r2_*` from `tradier_indicators.py` / NPZ; wire into `vec_paths/linearity_lr.py` if sweep proves `R²≥0.3` clean-move edge.

### §13 — Leaderboard Entry Path
- **File:** `ez_manage.py:17030`.
- **In faithful engines:** ❌ NOT PRESENT. Rarely fires in practice.
- **Kill:** `LEADERBOARD_ENTRY_ENABLED=False` (new) / `LEADERBOARD_ENTRY_ENABLED_TRADIER=False`. Existing `ABLATION_DISABLE_ENTRY_LEADERBOARD=False` kept for sweep gating.
- **Hook ASAP:** Low priority — only wire if leaderboard injection proves >`Δ 0` in `v8_vec_sweep` A/B.

### §14 — SRS NOLOSS Bypass (Structural Range Shift)
- **Files:** `tradier_manage.py`, `ez_manage.py` (`structural_range_shift.py`).
- **In faithful engines:** ⚠️ PARTIAL (SRS logic present but backtest cannot exit at loss; live allows controlled loss via `SRS_K_EXIT_1H`).
- **Kill:** No kill — difference is `UNIVERSAL_NOLOSS_GATE` blanket vs targeted bypass list. SRS bypass lives in `UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS` (`'STRUCTURAL_RANGE_SHIFT'`); with `UNIVERSAL_NOLOSS_GATE=False` this is moot, but the SRS loss-escape path (`SRS_K_EXIT_1H=85`) is still reachable via structural gate.
- **Hook ASAP:** Ensure `vec_paths/structural_range_shift.py` + `v8_vec_sweep` SRS parity emits same `'STRUCTURAL_RANGE_SHIFT'` reason and is included in `UCS_NOLOSS_BYPASS` union.

### §15 — Hedge Engine (same-symbol & cross-symbol hedging)
- **Files:** `ez_positions_quick.py` extensive, `ez_manage.py:31553`, `vec_paths/hedge_engine.py` / `hedge_scan_gates.py`.
- **In faithful engines:** ❌ NOT PRESENT (`HEDGE_MODE=False` short-circuits; `vec_paths/hedge_engine.py` exists but not invoked in `v8_vec_sweep` by default).
- **Kill:** `HEDGE_MODE=False` / `HEDGE_MODE_TRADIER=False` — **OFF LIMITS** per user 2026-08-18 (stripped accounts, replaced by MTF compound exit: `2×ATR15m trail + GR/WT/DC/BB rejection`). Every `HEDGE_*` sub-knob (`HEDGE_MAX_PCT_OF_LOSER=1.0`, `HEDGE_MAX_ABSOLUTE_USD=100000`, `HEDGE_DC_RESISTANCE_GATE_ENABLED=False`, `HEDGE_WT_VEL_GATE_ENABLED=False`, etc.) is dead-gated.
- **Hook ASAP:** Dedicated hedge-agent must retest `900×900×160` combos (gain-deterioration primary, DC zones secondary) and rebuild `HEDGE_TRIGGER_LOSS_PCT_ENTRY`, `HEDGE_SAME_SYMBOL_PCT`, sizing via `vec_paths/hedge_scan_gates.py` parity. Re-enable only via USER explicit unlock + Tier-2 `pool_sharpe>0.5` on ≥48 sym.

### §16 — Market Sentiment L/S Ratio — NON-VECTORIZABLE LIVE-ONLY GATE

- **Files:** `ez_manage.py:compute_applied_ratio` / `ratio_rebalance_loop` (3455 lines), `tradier_manage.py:LS_RATIO_ENFORCE_TRADIER`, `config.py:2051` (`LS_RATIO_ENFORCE`, `RATIO_MULTIPLIER=3.0`, `LS_RATIO_HARD_MIN/MAX`, `RATIO_EMERGENCY_EXIT_*`), `config_tradier.py:1321` (`LS_RATIO_ENFORCE_TRADIER=True`, `SECTOR_LS_RATIO_*`).
- **Vector model:** ❌ **NON-VECTORIZABLE** by construction. Ratio is cross-symbol portfolio state: `(long count + long PnL) vs (short count + short PnL)` across ~30 live positions, driven by `market_sentiment_score` (amplified `50 + (score-50)×RATIO_MULTIPLIER`, clamped `10-90`). Single-symbol vectorized runs (`v8_vec_sweep`, `v8_quick`, `uve`) have no portfolio to measure — they cannot know if the book is `90/10` or `10/90`.
- **Live:** ✅ **RE-ENABLE LIVE as soon as decent per_sym set exists.** Keep `LS_RATIO_ENFORCE=True` / `LS_RATIO_ENFORCE_TRADIER=True` / `SECTOR_LS_RATIO_ENABLED=True` live. They currently stay `True` by default (portfolio-healthy gate). The emergency closer `RATIO_EMERGENCY_EXIT_ENABLED=False` (kills losers = `Sharpe 19 vs 357` for ratio-only) stays `False` permanently — ratio is fixed by **opening** the underweight side, NEVER by closing losers.
- **Backtest:** Vector ignores ratio (correct). `backtest_v8_engine.py` full-portfolio run DOES enforce it (it has the portfolio). So Tier-1 vec screens will optimistically over-allocate the favored side; Tier-2 exact `backtest_v8` will then clamp it. This tier gap is expected and documented in `BACKTEST_BIBLE.md §16.11`.
- **Hook:** No vector hook needed. After `data/hourly_reconfig/*/active_config.json` per_sym winners prove decent (`gain/mo>2%`, `pool_sharpe>0.2`, `TIM<85%`, `DD≤30%`, `closes≥10/mo`), keep ratio live. If Tier-2 shows a symbol systematically blocked by ratio, surface via `data/reports/TRB_LIVE_SYMBOL_SIDE_FLOOR.json` / `:5057` dash.

---

## Missing Config Switch TODO — RESOLVED 2026-08-18

All four previously missing kills are now defined (see `§0` + Group Kill):

| Feature | Switch | File |
|---------|--------|------|
| Advisory consumer | `FIN_ADVISORY_CONSUMER_ENABLED` / `…_TRADIER` | `config.py:96` / `config_tradier.py:1242` |
| WT_4H_VEL mand reentry | `WT_4H_VEL_MANDATORY_REENTRY_ENABLED` / `…_TRADIER` | `config.py:96` / `config_tradier.py:1242` |
| DELTA mand reentry routing | `DELTA_EXIT_MANDATORY_REENTRY_ENABLED` / `…_TRADIER` | `config.py:96` / `config_tradier.py:1242` |
| EPQ "NO QUESTIONS" | `MANDATORY_PRICE_CROSS_EPQ_ENABLED` / `…_TRADIER` | `config.py:96` / `config_tradier.py:1242` |
| Leaderboard | `LEADERBOARD_ENTRY_ENABLED` / `…_TRADIER` | `config.py:96` / `config_tradier.py:1242` |
| Direction favorable | `DIRECTION_FAVORABLE_REENTRY_ENABLED` / `…_TRADIER` + `LEGACY_DIRECTION_FAVORABLE=False` | `config.py:96,2018` |
| B10 stoch rev | `B10_STOCH_REV_LIVE_ENABLED` / `…_TRADIER` + `REENTRY_B10_STOCH_REV_ENABLED=False` | `config.py:96,1627` |
| Tradier B/LR | `B_MAIN_ENTRY_GATE_ENABLED`, `LOCAL_EXTREMES_SCORER_ENABLED`, `LINEARITY_LR_*_ENABLED`, `TRADIER_LOCAL_EXTREMES_SCORING_ENABLED` | `config_tradier.py:1241` |

---

## Sanity Gap — "Don't Be Stupid" Filter (all reentry/hedge必须)

> USER 2026-08-18: *"ANY reentry or hedge — even GUARANTEED or MANDATORY — MUST pass the 'don't be stupid' filter: wt_1h AND wt_15m must agree, and K must not be at extreme."*

| Path | File | Current | Missing | Fix (unlock + parity) |
|------|------|---------|---------|----------------------|
| `GUARANTEED_REENTRY` | `ez_manage.py:15870` | `k_3m` 80/20 + `WT 3m+15m` | `wt_1h` direct agreement | Add `_wt_1h_ok = (wt1_1h > wt2_1h if is_long else wt1_1h < wt2_1h)`; require `wt_1h AND (wt_3m OR wt_15m)` |
| `MANDATORY_REENTRY PRICE_CROSS` | `ez_positions_quick.py:2909` | `k_3m` block + `2/3 WT` | `wt_1h` when exactly 2/3 agree | Require `wt_1h` mandatory in the 2/3 quorum |
| `MANDATORY_PRICE_CROSS_EPQ` | `ez_positions_quick.py:16046` | Block only if ALL 3 (`15m`+`1h`+`4h`) against | `1h OR 15m` disagrees | Flip veto: `_htf_against = bear_15m OR bear_1h` (done via `PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED=True`) |

**Status 2026-08-18:** `PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED=True` (protective) is now set. The deeper `wt_1h` mandatory patches for the first two rows require `ez_manage.py` + `ez_positions_quick.py` edits and their `vec_paths` twins — queued for the dedicated reentry-agent (see `S1_JOB_RESTORE_20260729.md`). Live stays safe via the OFF defaults above.

---

## Live ↔ Vector ↔ Backtest Parity Map

| Live path | `config.*` OFF default | `v8_vec_sweep` hook | `backtest_v8_engine` hook | Priority |
|-----------|------------------------|---------------------|---------------------------|----------|
| §1 Advisory | `FIN_ADVISORY_*=False` | `vec_paths/fin_advisory_gate.py` (new) | `V8_USE_VEC_*` shadow | P1 |
| §2 SCALP_V3 | `SCALP_V3_ENABLED=False` | `vec_paths/scalp_v3.py` + NPZ `orderbook_*` | direct (no live code import) | P2 |
| §3 Guaranteed | `LEGACY_GUARANTEED_REENTRY=False` | `vec_paths/reentry.py` 60-min/HTF + `wt_1h` | `V8_USE_VEC_ALL` reentry shadow | P0 |
| §4 Price-cross +30 | `MANDATORY_REENTRY_MIN_WT_AGREE=99` | same `vec_paths/reentry.py` gate | shadow | P0 |
| §5 EPQ NO_QUESTIONS | `MANDATORY_PRICE_CROSS_EPQ_ENABLED=False` | `vec_paths/reentry.py` veto flip | shadow | P0 |
| §6 Direction fav | `DIRECTION_FAVORABLE_*=False` | `vec_paths/reentry.py` 30-min window | shadow | P1 |
| §7 B10 | `B10_STOCH_REV_LIVE_ENABLED=False` | already in ablation; wire EPQ loop | shadow | P1 |
| §8 WT_4H_VEL mand | `WT_4H_VEL_MANDATORY_*=False` | `vec_paths/wt_exits.py` | `V8_USE_VEC_ALL` | P1 |
| §9 DELTA mand | `DELTA_EXIT_MANDATORY_*=False` | `vec_paths/delta_engine.py` | shadow | P0 |
| §10 LE | `*LOCAL_EXTREMES* =False` | `vec_paths/local_extremes.py` + NPZ `le_score` | `V8_USE_VEC_LS` | P2 |
| §11 B_MAIN | `B_MAIN_ENTRY_GATE_ENABLED=False` | `vec_paths/b_main_entry.py` | direct tradier | P2 |
| §12 LR | `LINEARITY_LR_*=False` | `vec_paths/linearity_lr.py` + NPZ `lrL_r2` | direct tradier | P2 |
| §13 Leaderboard | `LEADERBOARD_ENTRY_ENABLED=False` | `vec_paths/leaderboard.py` | direct | P2 |
| §14 SRS | (via `UNIVERSAL_NOLOSS_GATE=False`) | `vec_paths/structural_range_shift.py` | `UCS_BYPASS` union | P1 |
| §15 Hedge | `HEDGE_MODE=False` / `HEDGE_MODE_TRADIER=False` | `vec_paths/hedge_scan_gates.py` | `HEDGE_MODE` gate | P0 — OFF LIMITS until 900×900 agent |
| §16 Ratio | `LS_RATIO_ENFORCE=True` (LIVE-ONLY) | **NON-VECTORIZABLE** — no hook | `backtest_v8_engine` portfolio run only | LIVE-ON after per_sym decent |

---

## Verification

```bash
# Live OFF defaults
python3 -c "import config; c=config.Config(); assert not c.FIN_ADVISORY_CONSUMER_ENABLED; assert not c.LEGACY_GUARANTEED_REENTRY; assert not c.MANDATORY_PRICE_CROSS_EPQ_ENABLED; assert not c.HEDGE_MODE; assert not c.UNIVERSAL_NOLOSS_GATE; assert c.AUGMENT_ONLY_WHEN_PROFITABLE; assert not c.AUGMENT_AT_LOSS_ENABLED; print('crypto replica OFF ok')"
python3 -c "import config_tradier; c=config_tradier.TradierConfig(); assert not c.HEDGE_MODE_TRADIER; assert not c.B_MAIN_ENTRY_GATE_ENABLED; assert c.AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER; assert not c.UNIVERSAL_NOLOSS_GATE; assert c.LS_RATIO_ENFORCE_TRADIER; print('tradier replica OFF ok, ratio ON ok')"

# Faithful engine identity
sha256sum backtest_v8_engine.py v8_vec_sweep.py v8_quick_engine.py
# Vector beam uses faithful lane, not UVE toy
grep -c "simulate_one_symbol" tools/next_gen_beam_per_sym.py  # should be >0 and NOT "if False"
```

See also `BACKTEST_BIBLE.md §16.50` (full-V8 admission), `CLAUDE.md § STATE OF AFFAIRS`, `S1_JOB_RESTORE_20260729.md` (hedge-agent 900*900*160 queue).
